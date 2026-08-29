#!/usr/bin/env python3
"""Build a read-only PAPER-testing admission *candidate* receipt.

This verifier has no broker, credential, systemd, socket, order, or service
control surface.  It consumes immutable evidence files and publishes one
content-addressed candidate receipt with an atomic no-replace rename.  A GO
receipt is deliberately non-authorizing: a separate, later authority boundary
must consume and pin it before any PAPER action can become possible.
"""

from __future__ import annotations

import argparse
import ctypes
from dataclasses import dataclass
from datetime import datetime
import errno
import hashlib
import importlib.machinery
import importlib.util
import json
import os
from pathlib import Path, PurePosixPath
import re
import secrets
import select
import signal
import stat
import subprocess
import sys
import time
from types import ModuleType
from typing import Any, Callable, Mapping


RECEIPT_SCHEMA = "hepta.paper-testing-admission-candidate-receipt.v1"
RECEIPT_VERSION = 1
INSTALLED_EXECUTABLE = Path(
    "/usr/libexec/hepta-p1-paper-admission-verifier")
ZERO_SNAPSHOT_PRODUCER_EXECUTABLE = Path(
    "/usr/libexec/hepta-p1-paper-zero-exposure-snapshot-producer")
ZERO_EXPOSURE_ATTESTOR_EXECUTABLE = Path(
    "/usr/libexec/hepta-p1-paper-zero-exposure-attestor")
BROKER_POLICY_HELPER_EXECUTABLE = Path(
    "/usr/libexec/hepta-broker-egress-policy")
ZERO_SNAPSHOT_PRODUCER_SOURCE = (
    "scripts/hepta_p1_paper_zero_exposure_snapshot_producer.py")
ADMISSION_VERIFIER_SOURCE = "scripts/hepta_p1_paper_admission_verifier.py"
MINIMUM_TRADING_DAYS = 10
MAXIMUM_TRADING_DAYS = 20
MINIMUM_ELIGIBLE_DECISIONS = 200
MINIMUM_COMPLETE_PPM = 990_001
MINIMUM_BOOTTIME_DURATION_NS = 72 * 60 * 60 * 1_000_000_000
RELEASE_CAUSAL_PYTHON = Path("/usr/bin/python3.12")
RELEASE_CAUSAL_OPENSSL = Path("/usr/bin/openssl")
# Kept as an audited legacy constant for installed-layout compatibility tests;
# production uses the already-running process's fchdir(2)+chroot(2) syscalls
# and never executes this path.
RELEASE_CAUSAL_CHROOT = Path("/usr/sbin/chroot")
RELEASE_CAUSAL_VERIFIER = Path(
    "/usr/libexec/hepta-release-validation-closure-verifier")
RELEASE_CAUSAL_STAGE = Path(
    "/run/hepta/.hepta-release-causal-stage")
RELEASE_CAUSAL_EXPECTED_STDOUT = (
    "heptatrader-release-validation-verification: decision=GO round=114 "
    "candidate_only=true authority=false\n")
RELEASE_CAUSAL_TIMEOUT_SECONDS = 180
MAXIMUM_RELEASE_CAUSAL_DEPENDENCY_BYTES = 512 * 1024 * 1024
RELEASE_CAUSAL_MINIMUM_ROOTFS_BYTES = 64 * 1024 * 1024
RELEASE_CAUSAL_TMPFS_BYTES = 64 * 1024 * 1024
RELEASE_CAUSAL_ENVIRONMENT = {
    "PATH": "/usr/bin", "LANG": "C", "LC_ALL": "C", "TZ": "UTC0",
    "HOME": "/tmp", "TMPDIR": "/tmp", "PYTHONDONTWRITEBYTECODE": "1",
    "PYTHONNOUSERSITE": "1",
    "OPENSSL_CONF": "/etc/heptatrader/release-causal-openssl.cnf",
    "OPENSSL_MODULES": "/nonexistent-release-causal-provider-directory",
    "HEPTA_RELEASE_CAUSAL_ROOTFS": "1",
}
RELEASE_CAUSAL_OPENSSL_CONFIGURATION = b"""\
openssl_conf = openssl_init
[openssl_init]
providers = provider_sect
[provider_sect]
default = default_sect
[default_sect]
activate = 1
"""
RELEASE_CAUSAL_EXECUTABLE_SOURCE_PATHS = frozenset({
    "scripts/aggregate_hepta_execution_native_systemd_gate.py",
    "scripts/build_hepta_execution_native_vm_bundle.py",
    "scripts/build_heptatrader_evidence_ingestion_request.py",
    "scripts/converge_ctp_vendor_headers.py",
    "scripts/run_hepta_execution_native_systemd_gate.py",
    "scripts/verify_hepta_execution_native_vm_bundle.py",
    "scripts/verify_heptatrader_evidence_ingestion_receipt.py",
    "scripts/verify_heptatrader_evidence_set.py",
})
RELEASE_CAUSAL_SOURCE_INSTALL_PATHS = {
    path: (
        (
            "usr/libexec/hepta-release-validation-closure-verifier",
            "0644", "0755",
        )
        if path == "scripts/verify_heptatrader_release_validation_closure.py"
        else (
            "usr/libexec/" + (
                path.removeprefix("scripts/")
                if path.startswith("scripts/") else path),
            "0755" if path in RELEASE_CAUSAL_EXECUTABLE_SOURCE_PATHS
            else "0644", "0644",
        ))
    for path in (
        "scripts/aggregate_hepta_execution_native_systemd_gate.py",
        "scripts/build_hepta_execution_native_vm_bundle.py",
        "scripts/build_heptatrader_delivery_closure.py",
        "scripts/build_heptatrader_engineering_closure.py",
        "scripts/build_heptatrader_evidence_index.py",
        "scripts/build_heptatrader_evidence_ingestion_request.py",
        "scripts/build_heptatrader_release_validation_closure.py",
        "scripts/build_heptatrader_verification_evidence.py",
        "scripts/check_hepta_agent_os_provisioned_host.py",
        "scripts/converge_ctp_vendor_headers.py",
        "scripts/hepta_service_identities.py",
        "scripts/heptatrader_secure_artifacts.py",
        "scripts/run_execution_gateway_soak.py",
        "scripts/run_hepta_execution_native_systemd_gate.py",
        "scripts/run_hepta_execution_rootful_systemd_gate.py",
        "scripts/verify_hepta_execution_native_vm_bundle.py",
        "scripts/verify_heptatrader_agent_os_source_bundle.py",
        "scripts/verify_heptatrader_clean_source_bundle.py",
        "scripts/verify_heptatrader_delivery_closure.py",
        "scripts/verify_heptatrader_engineering_closure.py",
        "scripts/verify_heptatrader_evidence_index.py",
        "scripts/verify_heptatrader_evidence_ingestion_receipt.py",
        "scripts/verify_heptatrader_evidence_set.py",
        "scripts/verify_heptatrader_prebuilt_assets.py",
        "scripts/verify_heptatrader_release_validation_closure.py",
        "scripts/verify_heptatrader_runtime_package.py",
        "scripts/verify_heptatrader_vendor_assets.py",
        "hepta_ops/__init__.py",
        "hepta_ops/agent_os_source.py",
        "hepta_ops/registry.py",
    )
}
RELEASE_CAUSAL_PYTHON_PACKAGE_MANIFESTS = (
    Path("/var/lib/dpkg/info/python3.12-minimal.list"),
    Path("/var/lib/dpkg/info/python3.12.list"),
    Path("/var/lib/dpkg/info/libpython3.12-minimal:amd64.list"),
    Path("/var/lib/dpkg/info/libpython3.12-stdlib:amd64.list"),
)
RELEASE_CAUSAL_ABI_LOGICAL_PATHS = (
    Path("/lib64/ld-linux-x86-64.so.2"),
    Path("/lib/x86_64-linux-gnu/libbz2.so.1.0"),
    Path("/lib/x86_64-linux-gnu/libc.so.6"),
    Path("/lib/x86_64-linux-gnu/libcrypt.so.1"),
    Path("/lib/x86_64-linux-gnu/libcrypto.so.3"),
    Path("/lib/x86_64-linux-gnu/libdb-5.3.so"),
    Path("/lib/x86_64-linux-gnu/libexpat.so.1"),
    Path("/lib/x86_64-linux-gnu/libffi.so.8"),
    Path("/lib/x86_64-linux-gnu/liblzma.so.5"),
    Path("/lib/x86_64-linux-gnu/libm.so.6"),
    Path("/lib/x86_64-linux-gnu/libncursesw.so.6"),
    Path("/lib/x86_64-linux-gnu/libpanelw.so.6"),
    Path("/lib/x86_64-linux-gnu/libreadline.so.8"),
    Path("/lib/x86_64-linux-gnu/libsqlite3.so.0"),
    Path("/lib/x86_64-linux-gnu/libssl.so.3"),
    Path("/lib/x86_64-linux-gnu/libtinfo.so.6"),
    Path("/lib/x86_64-linux-gnu/libz.so.1"),
)
CLONE_NEWNS = 0x00020000
MS_RDONLY = 1
MS_NOSUID = 2
MS_NODEV = 4
MS_NOEXEC = 8
MS_REMOUNT = 32
MS_REC = 16384
MS_PRIVATE = 1 << 18
PRODUCTION_MODE = "PRODUCTION_ROOT_ADMISSION_CANDIDATE_VERIFIER"
ZERO_SNAPSHOT_PRODUCTION_MODE = (
    "PRODUCTION_ROOT_OFFLINE_SIGNED_ACCOUNT_ADAPTER")
WATCH_HANDOFF_PRODUCER_PATH = Path(
    "/usr/libexec/hepta-p1-watch-to-paper-handoff")
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
WATCH_HANDOFF_SCHEMA = "hepta.p1-watch-to-paper-handoff-receipt.v2"
WATCH_HANDOFF_VERSION = 2
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
ROUND = 114
INSTALL_GENERATION = 22
PREDECESSOR_INSTALL_GENERATION = 21
INSTALLED_FILE_COUNT = 128
PREDECESSOR_INSTALL_POINTER_SHA256 = (
    "sha256:2beeb507fcafbbfc2c93d2e4756fddf0b27e9872733ff97d28af47006461d406")
INSTALL_RECEIPT_PATH = (
    "/var/lib/hepta/shadow-runtime-install-receipts/"
    "hepta-p1-round114-generation22-passive.json")
INSTALL_MANIFEST_PATH = (
    "/var/lib/hepta/shadow-runtime-install-artifacts/"
    "hepta-p1-round114-generation22-shadow-runtime.manifest.json")
INSTALL_BACKUP_ROOT = (
    "/var/lib/hepta/shadow-runtime-backups/"
    "hepta-p1-round114-generation22-passive")
PREDECESSOR_PROFILE_RECEIPT_PATH = (
    "/var/lib/heptatrader/p1-watch-profile-receipts/round95-generation20.json")
PREDECESSOR_PROFILE_RECEIPT_FILE_SHA256 = (
    "sha256:c1557c1fe0bbab68bfc0c85148f2dcb3b32a2c8b75da7b229296d1b99daebd67")
PREDECESSOR_PROFILE_RECEIPT_BODY_SHA256 = (
    "sha256:e09712acbfed117a47ad5e86c63bbfe638ec38d89d7579e85b47409b57728fb2")
PREDECESSOR_PROFILE_RECEIPT_BYTES = 58196
MAXIMUM_INPUT_BYTES = 16 * 1024 * 1024
MAXIMUM_OUTPUT_BYTES = 2 * 1024 * 1024
MAXIMUM_STATIC_AGE_MS = 45 * 24 * 60 * 60 * 1000
MAXIMUM_EXPOSURE_AGE_MS = 5 * 60 * 1000
MAXIMUM_GATE_PROVENANCE_LIFETIME_MS = 24 * 60 * 60 * 1000
OUTPUT_LIFETIME_MS = 5 * 60 * 1000
MAXIMUM_CLOCK_SKEW_MS = 30 * 1000

DIGEST = re.compile(r"sha256:[0-9a-f]{64}")
GIT_HEAD = re.compile(r"[0-9a-f]{40}")
BARE_SHA256 = re.compile(r"[0-9a-f]{64}")
RUN_ID = re.compile(r"[0-9a-f]{32}")
BOOT_ID = re.compile(
    r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-"
    r"[0-9a-f]{4}-[0-9a-f]{12}")
PINNED_IMAGE = re.compile(
    r"[a-z0-9][a-z0-9._/:-]*@sha256:[0-9a-f]{64}")
DOMAIN = re.compile(r"[a-z][a-z0-9-]{0,31}")
CAMPAIGN = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,127}")
VERSION_TOKEN = re.compile(r"[0-9A-Za-z][0-9A-Za-z.+-]{0,126}-round114")
NONCE = re.compile(r"[0-9a-f]{64}")
RESERVATION_ID = re.compile(r"zero-exposure-[0-9a-f]{48}")

ROOT_UID = 0
ROOT_GID = 0
NOFOLLOW = getattr(os, "O_NOFOLLOW", 0)
CLOEXEC = getattr(os, "O_CLOEXEC", 0)
NONBLOCK = getattr(os, "O_NONBLOCK", 0)
DIRECTORY_FLAGS = os.O_RDONLY | os.O_DIRECTORY | NOFOLLOW | CLOEXEC
READ_FLAGS = os.O_RDONLY | NOFOLLOW | CLOEXEC | NONBLOCK
CREATE_FLAGS = os.O_WRONLY | os.O_CREAT | os.O_EXCL | NOFOLLOW | CLOEXEC
RENAME_NOREPLACE = 1
_LIBC = ctypes.CDLL(None, use_errno=True)
CLI_RUN_TOKEN = object()

INPUT_NAMES = (
    "source_baseline",
    "install_manifest",
    "install_receipt",
    "install_pointer",
    "profile_receipt",
    "activation_receipt",
    "p1_audit_receipt",
    "release_validation_receipt",
    "agent_os_rootful_gate_receipt",
    "dual_domain_gate_receipt",
    "rootful_gate_receipt",
    "p1_liveness_gate_receipt",
    "network_gate_receipt",
    "hard_network_gate_receipt",
    "native_gate_receipt",
    "watch_handoff_receipt",
    "zero_exposure_receipt",
)

RELEASE_VALIDATION_FIELDS = frozenset({
    "schema", "version", "project_id", "round", "release_version",
    "evaluated_at", "expires_at", "decision", "passed",
    "candidate_scope", "local_evidence", "retention_evidence",
    "safety_boundaries",
})
RELEASE_SAFETY_BOUNDARIES = {
    "broker_connection_performed": False,
    "direct_broker_access_authorized": False,
    "live_authorized": False,
    "mutation_authorized": False,
    "mutation_performed": False,
    "order_placement_authorized": False,
    "order_placement_performed": False,
    "paper_authorized": False,
    "release_authorized": False,
    "source_files_deleted": False,
    "source_removal_authorized": False,
}
RELEASE_LOCAL_FIELDS = frozenset({
    "profile", "round", "release_version", "artifact_directory",
    "input_manifest_sha256", "source_baseline", "source_lineage",
    "verification", "delivery", "native", "critical_files",
    "safety_boundaries",
})
RELEASE_SOURCE_LINEAGE_FIELDS = frozenset({
    "git_head", "strict_source_bundle_sha256",
    "strict_source_manifest_sha256",
    "strict_source_security_manifest_sha256", "strict_source_files_sha256",
    "agent_source_bundle_sha256", "runtime_package_sha256",
    "runtime_package_manifest_sha256",
})
RELEASE_VERIFICATION_FIELDS = frozenset({
    "matrix_generated_at", "runner_generated_at", "fresh_until",
    "maximum_age_seconds", "lanes",
})
RELEASE_LANE_FIELDS = frozenset({
    "name", "build_type", "build_testing", "ibapi_enabled",
    "expected_tests", "observed_tests", "selection", "passed",
})
RELEASE_CRITICAL_FILE_FIELDS = frozenset({
    "role", "path", "sha256", "size", "mode",
})
RELEASE_BINDING_FIELDS = frozenset({"path", "sha256", "size", "mode"})
RELEASE_RETENTION_FIELDS = frozenset({
    "inputs", "evidence_root", "verification",
})
RELEASE_RETENTION_INPUTS = frozenset({
    "evidence_set_manifest", "index", "receipt", "request",
    "retention_policy", "trust_policy",
})
RELEASE_RETENTION_VERIFICATION_FIELDS = frozenset({
    "schema", "trust_scope", "signature_status", "retention_status",
    "current_policy_satisfied_object_count", "statement_sha256",
    "request_sha256", "index_sha256", "evidence_set_manifest_sha256",
    "trust_policy_sha256", "evidence_set_id", "profile", "role_count",
    "production_contract_verified",
})

AGENT_OS_ROOTFUL_FIELDS = frozenset({
    "schema", "passed", "decision", "certification_ready",
    "certification_blockers", "certification_level", "production_eligible",
    "environment_review_closure", "duration_ms", "build", "builder",
    "base_image", "docker_host", "apparmor", "docker_apparmor_namespace",
    "image", "base_holder", "container", "inner", "inputs",
    "input_stability", "owned_docker_objects_cleanup_complete",
    "owned_docker_objects_cleanup", "boundary", "apparmor_post_cleanup",
    "apparmor_revalidated", "apparmor_records_equal",
    "docker_apparmor_namespace_post_cleanup",
    "docker_apparmor_namespace_revalidated",
    "docker_apparmor_namespace_records_equal", "completed_checks",
})
AGENT_OS_BOUNDARY = {
    "host_hepta_units_started": False,
    "host_bind_mounts": 0,
    "real_broker_connections": 0,
    "paper_orders": 0,
    "paper_authorized": False,
    "live_authorized": False,
    "ib_adapter_staged": False,
    "container_network": "none",
}
AGENT_OS_INNER_IDENTITIES = {
    "agent_uid": 2004,
    "gateway_uid": 2001,
    "simulator_execution_uid": 2002,
    "ib_execution_uid_reserved_not_started": 2003,
    "trust_domains": {
        "codex-a": {
            "gateway_uid": 2101, "agent_uid": 2104,
            "execution_uid": 2111, "reader_uid": 2121,
        },
        "openclaw-b": {
            "gateway_uid": 2102, "agent_uid": 2105,
            "execution_uid": 2112, "reader_uid": 2122,
        },
    },
}
AGENT_OS_INNER_CHECKS = frozenset({
    "systemd_pid1", "network_none_loopback_only",
    "no_host_mount_or_docker_socket", "fixed_identity_isolation",
    "two_domain_execution_identity_isolation",
    "two_domain_execution_socket_cross_access_denied",
    "two_domain_execution_authorities_started_and_stopped",
    "two_domain_runtime_configs_root_owned_regular",
    "two_domain_agent_host_dropins_isolated",
    "two_agent_gateway_execution_watch_chains",
    "two_domain_uid_config_cross_rejected",
    "two_domain_token_cross_rejected",
    "two_domain_account_binding_cross_rejected",
    "two_domain_execution_binding_cross_rejected",
    "two_domain_gateway_socket_cross_rejected",
    "two_domain_watch_restart_fails_closed",
    "two_domain_collector_typed_terminal",
    "two_domain_watch_sessions_revoked",
    "two_domain_custodian_reader_identity_isolation",
    "two_domain_watch_environments_root_owned_private",
    "two_domain_custodian_services_monitored",
    "two_domain_custodian_reconcile_timers_enabled",
    "two_domain_custodian_rotation_bound",
    "two_domain_custodian_sigkill_crash_closed",
    "two_domain_custodian_closure_receipts_exact",
    "two_domain_custodian_authority_residue_absent",
    "uid1000_observer_reads_uid2101_proc_stat",
    "broker_network_policy_active", "broker_watchdog_timeout_observed",
    "broker_watchdog_timeout_stop_contract",
    "broker_watchdog_gateway_binds_to_stop",
    "broker_watchdog_deny_all_persisted",
    "broker_watchdog_watch_terminalized", "broker_watchdog_clean_restart",
    "agent_ib_ports_denied", "gateway_ib_ports_denied",
    "ib_execution_ib_ports_denied", "agent_model_egress_preserved",
    "ib_paper_surface_absent", "installation_preflight",
    "simulator_dual_socket_activation", "gateway_dual_socket_activation",
    "root_watch_bootstrap", "uid_2004_mcp_initialize",
    "uid_2004_exact_watch_tool_list", "uid_2004_read_only_probes",
    "gateway_service_socket_reactivation",
    "simulator_service_socket_reactivation",
    "socket_stop_removes_paths", "socket_restart_recreates_paths",
    "watch_restart_fails_closed", "runtime_preflight_after_restart",
    "watch_session_revoked", "all_runtime_paths_removed",
})
AGENT_OS_INNER_BOUNDARY = {
    "container_network": "none", "real_broker_connections": 0,
    "paper_orders": 0, "paper_authorized": False,
    "live_authorized": False, "ib_adapter_staged": False,
    "host_hepta_units_started": False, "host_bind_mounts": 0,
    "raw_session_token_recorded": False,
}
AGENT_OS_SOURCE_EXECUTABLES = frozenset({
    "scripts/hepta_official_source_capture.py",
    "scripts/hepta_p1_watch_profile_deployer.py",
    "scripts/hepta_shadow_host_installer.py",
    "scripts/run_hepta_agent_os_rootful_systemd_e2e_gate.py",
    "tests/agent_os_rootful_systemd/hepta-agent-os-systemd-entrypoint",
    "tests/agent_os_rootful_systemd/hepta_agent_os_rootful_inner_gate.py",
})
AGENT_OS_SOURCE_MODES = {
    path: "0755" if path in AGENT_OS_SOURCE_EXECUTABLES else "0644"
    for path in (
        ".agents/plugins/marketplace.json",
        "adapters/mcp/hepta_mcp_server.py",
        "docs/AGENT-NATIVE-TRADING-OS-ARCHITECTURE.md",
        "docs/BROKER-NETWORK-ISOLATION.md", "docs/RUNBOOK-STARTUP.md",
        "plugins/heptatrader-agent-os/.codex-plugin/plugin.json",
        "plugins/heptatrader-agent-os/.mcp.json",
        "plugins/heptatrader-agent-os/README.md",
        "scripts/build_hepta_p1_observation_policy.py",
        "scripts/check_hepta_agent_os_provisioned_host.py",
        "scripts/check_hepta_agent_trust_domains.py",
        "scripts/hepta_agent_mcp_launcher.py",
        "scripts/hepta_agent_session_bootstrap.py",
        "scripts/hepta_agent_trust_domain.py",
        "scripts/hepta_bounded_shadow_closure_verifier.py",
        "scripts/hepta_bounded_shadow_observer.py",
        "scripts/hepta_broker_egress_policy.py",
        "scripts/hepta_eurusd_confirmed_momentum_strategy.py",
        "scripts/hepta_market_context_builder.py",
        "scripts/hepta_market_evidence_normalizer.py",
        "scripts/hepta_market_official_source_extractor.py",
        "scripts/hepta_official_source_capture.py",
        "scripts/hepta_p1_load_probe_validator.py",
        "scripts/hepta_p1_shadow_admission_launcher.py",
        "scripts/hepta_p1_shadow_host_controller.py",
        "scripts/hepta_p1_shadow_observer_controller.py",
        "scripts/hepta_p1_watch_activation_transaction.py",
        "scripts/hepta_p1_watch_profile_deployer.py",
        "scripts/hepta_paper_receipt_contracts.py",
        "scripts/hepta_rootful_review_closure_consumer.py",
        "scripts/hepta_shadow_host_installer.py",
        "scripts/hepta_shadow_market_history.py",
        "scripts/hepta_shadow_watch_collector.py",
        "scripts/hepta_shadow_watch_custodian.py",
        "scripts/hepta_shadow_watch_exporter.py",
        "scripts/hepta_strategy_contracts.py",
        "scripts/hepta_strategy_shadow_runner.py",
        "scripts/run_hepta_agent_os_rootful_systemd_e2e_gate.py",
        "scripts/validate_hepta_strategy_decision_receipt.py",
        "strategies/eurusd-confirmed-momentum-shadow-v2.json",
        "systemd/hepta-agent-broker-egress-policy.conf.example",
        "systemd/hepta-agent-host-identity.conf.example",
        "systemd/hepta-agent-trust-domain-paper-identities-v1.json.example",
        "systemd/hepta-agent-trust-domain-policy-v1.json",
        "systemd/hepta-broker-egress-policy.service",
        "systemd/hepta-broker-network-policy-v1.json",
        "systemd/hepta-execution-events-simulator.socket",
        "systemd/hepta-execution-events-simulator@.socket",
        "systemd/hepta-execution-simulator.env.example",
        "systemd/hepta-execution-simulator.service",
        "systemd/hepta-execution-simulator.socket",
        "systemd/hepta-execution-simulator@.service",
        "systemd/hepta-execution-simulator@.socket",
        "systemd/hepta-p1-watch-activation-reconcile.service",
        "systemd/hepta-p1-watch-activation-reconcile.timer",
        "systemd/hepta-p1-watch-activation.service",
        "systemd/hepta-service-identities-v1.json",
        "systemd/hepta-shadow-watch-collector@.service",
        "systemd/hepta-shadow-watch-collector@.timer",
        "systemd/hepta-shadow-watch-custodian-reconcile@.service",
        "systemd/hepta-shadow-watch-custodian-reconcile@.timer",
        "systemd/hepta-shadow-watch-custodian@.service",
        "systemd/hepta-shadow-watch-domain.env.example",
        "systemd/hepta-shadow-watch-export@.service",
        "systemd/hepta-tool-gateway-domain.env.example",
        "systemd/hepta-tool-gateway.env.example",
        "systemd/hepta-tool-gateway.service",
        "systemd/hepta-tool-gateway.service.d/"
        "10-hepta-broker-egress-policy.conf",
        "systemd/hepta-tool-gateway.socket",
        "systemd/hepta-tool-gateway@.service",
        "systemd/hepta-tool-gateway@.service.d/"
        "10-hepta-broker-egress-policy.conf",
        "systemd/hepta-tool-gateway@.socket",
        "systemd/hepta-tool-session-supervisor.socket",
        "systemd/hepta-tool-session-supervisor@.socket",
        "tests/agent_os_rootful_systemd/Dockerfile",
        "tests/agent_os_rootful_systemd/hepta-agent-os-rootful-e2e.target",
        "tests/agent_os_rootful_systemd/hepta-agent-os-systemd-entrypoint",
        "tests/agent_os_rootful_systemd/hepta_agent_os_rootful_inner_gate.py",
        "tests/agent_os_rootful_systemd/hepta_broker_network_rootful_probe.py",
        "tests/fixtures/hepta-agent-trust-domains-v1.json",
        "tmpfiles.d/heptatrader-agent-os.conf",
    )
}
AGENT_OS_BUILD_BINARIES = frozenset({
    "hepta-executiond", "hepta-tool-gatewayd", "hepta-sessionctl",
    "heptactl",
})
AGENT_OS_RUNTIME_BINARY_PATHS = {
    "hepta-executiond": "usr/libexec/hepta-executiond",
    "hepta-tool-gatewayd": "usr/libexec/hepta-tool-gatewayd",
    "hepta-sessionctl": "usr/bin/hepta-sessionctl",
    "heptactl": "usr/bin/heptactl",
}
AGENT_OS_NATIVE_BINARY_DIGEST_FIELDS = {
    "hepta-executiond": "simulator_sha256",
    "hepta-tool-gatewayd": "agent_os_gateway_sha256",
    "hepta-sessionctl": "agent_os_sessionctl_sha256",
}

P1_LIVENESS_GATE_FIELDS = frozenset({
    "schema", "run_id", "decision", "passed", "rehearsal_passed",
    "certification_ready", "certification_blockers", "scope",
    "started_at_ms", "completed_at_ms", "expires_at_ms", "body_sha256",
    "producer", "production_mode", "paper_test_admission_candidate",
    "paper_admission_authorized", "paper_authorized", "live_authorized",
    "mutation_authorized", "direct_broker_access",
    "order_submission_authorized", "duration_ms", "lineage", "inputs",
    "generated_input_sha256", "platform", "container",
    "disposable_cleanup", "certification", "environment_review_closure",
    "inner", "boundary",
})
P1_LIVENESS_EXPECTED_CHECKS = frozenset({
    "real_systemd_pid1_private_cgroup",
    "production_unit_inputs_present_and_hardened",
    "effective_watchdog_timeout_observed",
    "watchdog_restart_changed_process_identity",
    "watchdog_recovered_and_remained_healthy",
    "worker_failure_durable_before_exit",
    "worker_restart_refused_terminal_replay",
    "coordinator_observed_worker_terminal",
    "failed_closed_chain_forbids_catch_up",
    "effective_unit_hardening_exact",
    "target_stop_cleaned_all_owned_units",
    "owned_process_residue_absent",
    "container_tcp_udp_surface_empty",
    "all_authority_and_order_flags_false",
})
P1_LIVENESS_BOUNDARY = {
    "broker_connectors": 0, "broker_connections": 0,
    "broker_protocol_messages": 0, "paper_orders": 0,
    "paper_test_admission_candidate": False, "paper_authorized": False,
    "live_authorized": False, "mutation_authorized": False,
    "direct_broker_access": False, "order_submission_authorized": False,
    "host_bind_mounts": 0, "host_systemd_units_touched": 0,
    "host_network_rules_touched": 0, "real_credentials": 0,
}

PRODUCTION_PRODUCER_PATHS = {
    "p1_auditor": (
        "scripts/hepta_p1_safety_soak_auditor.py",
        "usr/libexec/hepta-p1-safety-soak-auditor"),
    "watch_handoff": (
        "scripts/hepta_p1_watch_to_paper_handoff.py",
        "usr/libexec/hepta-p1-watch-to-paper-handoff"),
    "zero_attestor": (
        "scripts/hepta_p1_paper_zero_exposure_attestor.py",
        "usr/libexec/hepta-p1-paper-zero-exposure-attestor"),
    "zero_snapshot": (
        "scripts/hepta_p1_paper_zero_exposure_snapshot_producer.py",
        "usr/libexec/hepta-p1-paper-zero-exposure-snapshot-producer"),
    "rootful_review_verifier": (
        "scripts/hepta_rootful_systemd_environment_provenance.py",
        "usr/libexec/hepta-rootful-systemd-environment-provenance"),
}
RELEASE_VALIDATION_SOURCE_PATHS = (
    "scripts/build_heptatrader_release_validation_closure.py",
    "scripts/verify_heptatrader_release_validation_closure.py",
)
P1_LIVENESS_SOURCE_MODES = {
    "scripts/run_hepta_p1_dual_domain_rootful_gate.py": "0644",
    "scripts/run_hepta_p1_campaign_rootful_liveness_gate.py": "0644",
    "scripts/hepta_rootful_review_closure_consumer.py": "0644",
    "systemd/hepta-p1-safety-soak-campaign@.service": "0644",
    "systemd/hepta-p1-safety-soak-observer-worker@.service": "0644",
    "systemd/hepta-p1-safety-soak-recorder-worker@.service": "0644",
    "systemd/hepta-p1-safety-soak@.target": "0644",
    "tests/p1_campaign_rootful_liveness_systemd/Dockerfile": "0644",
    "tests/p1_campaign_rootful_liveness_systemd/"
    "hepta-p1-liveness-systemd-entrypoint": "0755",
    "tests/p1_campaign_rootful_liveness_systemd/"
    "hepta_p1_liveness_inner_gate.py": "0755",
    "tests/p1_campaign_rootful_liveness_systemd/"
    "hepta_p1_liveness_daemon.py": "0755",
    "tests/p1_campaign_rootful_liveness_systemd/"
    "hepta-p1-liveness-watchdog.service": "0644",
    "tests/p1_campaign_rootful_liveness_systemd/"
    "hepta-p1-liveness-worker.service": "0644",
    "tests/p1_campaign_rootful_liveness_systemd/"
    "hepta-p1-liveness-coordinator.service": "0644",
    "tests/p1_campaign_rootful_liveness_systemd/"
    "hepta-p1-campaign-rootful-liveness.target": "0644",
}

SOURCE_BASELINE_FIELDS = frozenset({
    "schema", "version", "generated_at", "git_head", "source_manifest",
    "source_baseline_frozen", "clean_checkout_certified",
    "release_authorized", "paper_authorized", "live_authorized",
    "worktree_status_entry_count", "blocked_reason",
    "excluded_unsafe_tree",
})
SOURCE_MANIFEST_FIELDS = frozenset({"file_count", "sha256", "files"})
SOURCE_FILE_FIELDS = frozenset({"path", "mode", "size", "sha256"})
INSTALL_MANIFEST_FIELDS = frozenset({
    "schema", "version", "archive_sha256", "source_baseline_sha256",
    "installer_sha256", "files", "paper_authorized", "live_authorized",
    "mutation_attempted", "direct_broker_access",
})
INSTALL_RECEIPT_FIELDS = frozenset({
    "schema", "version", "finished_at_ms", "domain", "archive_sha256",
    "source_baseline_sha256", "installer_sha256", "installed_file_count",
    "installed_paths_sha256", "backup_root", "replaced_file_count",
    "new_file_count", "default_deny_identity_manifest", "reader_gid",
    "install_generation", "predecessor_install_generation",
    "predecessor_current_install_pointer_file_sha256", "transaction_lock",
    "preflight_before", "preflight_after", "preflight_continuity_claimed",
    "paper_authorized", "live_authorized", "mutation_attempted",
    "direct_broker_access", "services_started", "services_enabled",
    "status", "body_sha256",
})
INSTALL_POINTER_FIELDS = frozenset({
    "schema", "version", "generation", "domain", "backup_root",
    "manifest_path", "manifest_file_sha256", "receipt_path",
    "receipt_file_sha256", "archive_sha256", "source_baseline_sha256",
    "installer_sha256", "installed_file_count", "installed_paths_sha256",
    "transaction_lock_path", "paper_authorized", "live_authorized",
    "mutation_attempted", "direct_broker_access", "body_sha256",
})
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
PROFILE_RECEIPT_FIELDS = frozenset({
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
    "dormant_paper_to_watch_transition_receipt",
    "body_sha256",
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
DORMANT_PAPER_TO_WATCH_TRANSITION_RECEIPT_PATH = (
    "/var/lib/heptatrader/p1-watch-profile-receipts/"
    "round114-dormant-paper-to-watch.json")
REFERENCE_FIELDS = frozenset({"path", "file_sha256", "body_sha256"})
P1_AUDIT_FIELDS = frozenset({
    "schema", "version", "phase", "verdict", "campaign_id", "domain_id",
    "independent_auditor_id", "audited_at_ms", "campaign_spec_file_sha256",
    "campaign_spec_body_sha256", "freeze_bundle", "campaign_runtime",
    "producer", "production_mode", "source_manifest_sha256",
    "policy_sha256", "strategy_sha256", "evaluated_interval", "counts",
    "completeness", "checked_artifacts", "failed_invariants",
    "exposure_summary", "cleanup_status", "p1_safety_soak_gate_satisfied",
    "paper_test_admission_candidate", "safest_allowed_next_action",
    "paper_authorized", "live_authorized", "mutation_authorized",
    "direct_broker_access", "body_sha256",
})
P1_CAMPAIGN_RUNTIME_SCHEMA = "hepta.p1-safety-soak-campaign-runtime.v1"
P1_CAMPAIGN_RUNTIME_REFERENCE_FIELDS = REFERENCE_FIELDS | frozenset({
    "schema",
})
P1_INTERVAL_FIELDS = frozenset({
    "clock_id", "boot_id", "start_boottime_ns", "end_boottime_ns",
    "duration_ns", "maximum_checkpoint_gap_ns", "consecutive",
    "continuity_origin_ms", "continuity_end_ms", "continuity_final_slot",
})
P1_COUNTS_FIELDS = frozenset({
    "launcher_receipts", "verified_closures", "continuity_checkpoints",
    "declared_trading_days", "observed_trading_days", "scheduled_decisions",
    "decision_receipts", "eligible_decisions", "complete_eligible_decisions",
    "incomplete_eligible_decisions", "catch_up_decisions", "planned_faults",
    "fault_results", "authority_snapshots", "cleanup_snapshots",
})
P1_COMPLETENESS_FIELDS = frozenset({
    "numerator", "denominator", "ppm", "strictly_greater_than_99_percent",
})
P1_CHECKED_ARTIFACT_FIELDS = frozenset({
    "role", "path", "file_sha256", "body_sha256",
})
P1_EXPOSURE_FIELDS = frozenset({
    "evidence_present", "maximum_connector_count",
    "maximum_authorized_uid_count", "maximum_paper_unit_active_count",
    "campaign_socket_ever_present", "kill_switch_continuously_engaged",
    "local_boundary_uncertain", "scope",
    "authoritative_account_state_observed",
})
P1_CLEANUP_FIELDS = frozenset({
    "required_subject_count", "verified_subject_count", "complete",
})
DUAL_DOMAIN_GATE_FIELDS = frozenset({
    "schema", "run_id", "decision", "passed", "rehearsal_passed",
    "certification_ready", "certification_blockers", "scope",
    "started_at_ms", "completed_at_ms", "expires_at_ms", "body_sha256",
    "paper_test_admission_candidate", "paper_admission_authorized",
    "paper_authorized", "live_authorized", "mutation_authorized",
    "direct_broker_access", "order_submission_authorized", "duration_ms",
    "lineage", "inputs", "generated_input_sha256", "platform", "container",
    "disposable_cleanup", "certification", "environment_review_closure",
    "inner", "boundary",
})
ROOTFUL_GATE_FIELDS = frozenset({
    "schema", "run_id", "decision", "passed", "rehearsal_passed",
    "certification_ready", "certification_blockers", "scope",
    "started_at_ms", "completed_at_ms", "expires_at_ms", "duration_ms",
    "paper_test_admission_candidate", "paper_authorized", "live_authorized",
    "mutation_authorized", "direct_broker_access",
    "order_submission_authorized", "lineage", "inputs",
    "generated_input_sha256", "platform", "container",
    "disposable_cleanup", "certification", "environment_review_closure",
    "inner", "boundary",
    "body_sha256",
})
NETWORK_GATE_FIELDS = frozenset({
    "schema", "passed", "run_id", "base_image", "image_id", "container_id",
    "staged_inputs", "inner", "actual_rootful_container_run",
    "host_policy_applied", "host_services_started",
    "real_broker_connections", "paper_orders", "live_authorized",
})
HARD_NETWORK_GATE_FIELDS = frozenset({
    "schema", "run_id", "decision", "passed", "certification_ready",
    "rehearsal_passed", "execution_mode", "body_sha256", "scope",
    "started_at_ms", "completed_at_ms", "expires_at_ms", "duration_ms",
    "lineage", "provenance", "environment", "topology", "phases",
    "checks", "exposure", "cleanup", "boundary", "failure",
    "environment_review_closure",
    "paper_test_admission_authorized", "paper_authorized", "live_authorized",
    "mutation_authorized", "direct_broker_access",
    "order_submission_authorized",
})
NATIVE_GATE_FIELDS = frozenset({
    "schema", "passed", "certification_level", "variants", "common_closure",
    "aggregation_inputs", "boundary",
})
WATCH_HANDOFF_FIELDS = frozenset({
    "schema", "version", "status", "issued_at_ms", "expires_at_ms",
    "round", "domain", "campaign_id", "source_baseline_sha256",
    "producer", "production_mode",
    "activation_receipt", "p1_audit_receipt", "freeze_bundle",
    "watch_units_inactive",
    "watch_authority_count", "watch_socket_count", "watch_timer_count",
    "paper_units_inactive", "broker_deny_all", "kill_switch_engaged",
    "global_kill_switch_engaged", "identity_count",
    "identity_manifest_sha256", "paper_profile_restored",
    "paper_profile_restoration", "profile_candidate_absent",
    "paper_runtime_profile_hardened", "paper_runtime_profile_hardening",
    "paper_runtime_profile_candidate_absent",
    "crash_recovery_verified", "cleanup_residue_count",
    "paper_authorized", "live_authorized", "mutation_authorized",
    "direct_broker_access", "order_submission_authorized", "body_sha256",
})
HANDOFF_PROFILE_FILE_FIELDS = frozenset({
    "path", "file_sha256", "bytes", "mode", "uid", "gid", "nlink",
    "device", "inode", "mtime_ns", "ctime_ns",
})
HANDOFF_PROFILE_SEALED_FILE_FIELDS = frozenset({
    *HANDOFF_PROFILE_FILE_FIELDS, "body_sha256",
})
HANDOFF_PROFILE_RESTORATION_FIELDS = frozenset({
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
HANDOFF_RUNTIME_PROFILE_HARDENING_FIELDS = (
    PAPER_RUNTIME_PROFILE_HARDENING_FIELDS)
HANDOFF_PROFILE_TRANSITION_FIELDS = frozenset({
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
HANDOFF_PROFILE_PREIMAGE_FIELDS = frozenset({
    "schema", "version", "status", "round", "domain", "transition_token",
    "created_at_ms", "target_before", "backup",
    "predecessor_profile_receipt", "preflight", "paper_authorized",
    "live_authorized", "mutation_attempted", "direct_broker_access",
    "shadow_install_evidence", "body_sha256",
})
ZERO_EXPOSURE_FIELDS = frozenset({
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
    "host_authority_lease_reacquired", "paper_authorized",
    "live_authorized", "mutation_authorized", "direct_broker_access",
    "order_submission_authorized", "body_sha256",
})
EXECUTABLE_REFERENCE_FIELDS = frozenset({"path", "file_sha256"})
SIGNED_REFERENCE_FIELDS = frozenset({
    "path", "file_sha256", "signed_payload_sha256"})
RESERVATION_REFERENCE_FIELDS = frozenset({
    "path", "file_sha256", "body_sha256", "device", "inode", "uid",
    "gid", "mode", "size", "mtime_ns", "ctime_ns",
})
SIGNATURE_ATTESTATION_FIELDS = frozenset({
    "algorithm", "public_key", "verifier", "signature_sha256",
    "signed_payload_sha256", "return_code", "stdout", "stderr",
    "stdout_sha256", "stderr_sha256",
})
HOST_AUTHORITY_LEASE_FIELDS = frozenset({
    "directory_path", "lease_path", "owner_path", "directory_device",
    "directory_inode", "directory_uid", "directory_gid", "directory_mode",
    "lease_device", "lease_inode", "lease_uid", "lease_gid", "lease_mode",
    "lease_size", "held_exclusive", "boot_id",
})
HOST_AUTHORITY_DIRECTORY = Path("/run/hepta/ib-paper-host-authority")
HOST_AUTHORITY_LEASE_PATH = HOST_AUTHORITY_DIRECTORY / "lease.lock"
HOST_AUTHORITY_OWNER_PATH = HOST_AUTHORITY_DIRECTORY / "owner.v1"
RESERVATION_LIFECYCLE = (
    "CHALLENGE_ISSUED_TO_PAPER_TESTING_ADMISSION_FINALIZATION")
RESERVATION_NEXT_CONSUMER = "PAPER_TESTING_ADMISSION_VERIFIER"
RESERVATION_FINALIZATION_SCHEMA = (
    "hepta.p1-paper-zero-exposure-reservation-finalization.v1")
RESERVATION_CURRENT_POINTER_SCHEMA = (
    "hepta.p1-paper-zero-exposure-finalization-current.v1")
RESERVATION_FINALIZATION_ORDER = (
    "CANDIDATE_COMMIT_THEN_TOMBSTONE_COMMIT_THEN_CURRENT_POINTER_COMMIT_"
    "THEN_OWNER_REMOVE_THEN_REOPEN")
REVIEW_RECORD_FIELDS = frozenset({
    "schema", "status", "verified_at_ms", "expires_at_ms",
    "source_commit", "base_image_reference", "buildkit_image_reference",
    "output_directory", "verifier", "closure", "request",
    "authorization", "outputs", "invocation", "environment_fingerprint",
    "reopened_after_invocation", "reopened_at_gate_end",
    "paper_authorized", "live_authorized", "mutation_authorized",
    "direct_broker_access", "order_submission_authorized",
})
REVIEW_FILE_RECORD_FIELDS = frozenset({
    "path", "file_sha256", "mode", "uid", "gid", "identity_sha256",
})
REVIEW_VERIFIER_FIELDS = REVIEW_FILE_RECORD_FIELDS | frozenset({
    "source_path", "source_file_sha256", "source_commit",
})
REVIEW_INVOCATION_FIELDS = frozenset({
    "argv_sha256", "stdout_sha256", "returncode", "duration_ms",
    "exact_success_output", "no_shell",
})
ENVIRONMENT_FINGERPRINT_SCHEMA = (
    "hepta.rootful-systemd-environment-fingerprint.v1")
ENVIRONMENT_REVIEW_AUTHORITY = (
    "EXTERNAL_INDEPENDENT_ROOTFUL_ENVIRONMENT_REVIEW")
ENVIRONMENT_REVIEWER_ID = re.compile(
    r"[A-Za-z0-9][A-Za-z0-9._:@+-]{0,127}")
ENVIRONMENT_IMAGE_ID = re.compile(r"sha256:[0-9a-f]{64}")
ENVIRONMENT_SEMVER = re.compile(
    r"[0-9]+\.[0-9]+\.[0-9]+(?:[-+][0-9A-Za-z._-]+)?")
ENVIRONMENT_BUILDKIT_VERSION = re.compile(
    r"v[0-9]+\.[0-9]+\.[0-9]+(?:[-+][0-9A-Za-z._-]+)?")
ENVIRONMENT_DOCKER_API_VERSION = re.compile(r"[1-9][0-9]*\.[0-9]+")
ENVIRONMENT_BUILD_ID = re.compile(
    r"[0-9A-Za-z][0-9A-Za-z._+-]{0,127}")
ENVIRONMENT_DAEMON_ID = re.compile(
    r"[A-Za-z0-9][A-Za-z0-9:._-]{0,127}")
ENVIRONMENT_RAW_ABI = re.compile(r"v[1-9][0-9]{0,2}")
ENVIRONMENT_FINGERPRINT_FIELDS = frozenset({
    "schema", "source_commit", "verifier_file_sha256",
    "verifier_source_file_sha256", "review_authority", "reviewer_id",
    "observations", "trust_bindings", "body_sha256",
})
ENVIRONMENT_OBSERVATION_FIELDS = frozenset({
    "base_image", "isolated_builder", "apparmor", "docker_namespace",
})
ENVIRONMENT_BASE_FIELDS = frozenset({
    "image_id", "repo_digest", "repo_digests", "labels_sha256", "os",
    "architecture", "declared_volumes", "onbuild_instructions",
})
ENVIRONMENT_BUILDER_FIELDS = frozenset({
    "image_id", "repo_digest", "repo_digests", "config_sha256", "os",
    "architecture", "entrypoint", "buildkit_binary_path",
    "buildkit_binary_sha256", "buildkit_version", "buildx_path",
    "buildx_path_sha256", "buildx_binary_sha256", "buildx_version",
    "docker_server_version", "docker_server_api_version",
    "docker_server_git_commit",
})
ENVIRONMENT_APPARMOR_FIELDS = frozenset({
    "profile", "mode", "attach", "learning_count",
    "policy_source_sha256", "profile_sha256", "raw_sha256", "raw_abi",
    "raw_data_id", "namespace_name", "namespace_level",
    "namespace_stacked", "profile_inventory_sha256",
})
ENVIRONMENT_DOCKER_FIELDS = frozenset({
    "docker_daemon_id", "docker_daemon_pid",
    "docker_daemon_start_time_ticks", "docker_daemon_exe_sha256",
    "host_boot_id", "host_namespace_name", "host_namespace_level",
    "host_namespace_stacked", "daemon_namespace_name",
    "daemon_namespace_level", "daemon_namespace_stacked",
    "daemon_apparmor_current", "self_user_namespace_inode",
    "daemon_user_namespace_inode",
})
ENVIRONMENT_TRUST_FIELDS = frozenset({
    "producer", "docker_cli", "signature_verifier", "verification_key",
    "apparmor_policy_source",
})
ENVIRONMENT_REFERENCE_FIELDS = frozenset({"path", "sha256"})
ENVIRONMENT_TRUST_PATHS = {
    "producer": "/usr/libexec/hepta-rootful-systemd-environment-provenance",
    "docker_cli": "/usr/bin/docker",
    "signature_verifier": "/usr/bin/openssl",
    "verification_key":
        "/etc/heptatrader/rootful-systemd-review-ed25519.pub",
    "apparmor_policy_source":
        "/usr/share/heptatrader/systemd/hepta-systemd-gate.apparmor",
}
ENVIRONMENT_BASE_LABELS = {
    "io.hepta.rootful-systemd-base.offline-ready": "true",
    "io.hepta.rootful-systemd-base.version": "1",
}
REVIEW_OUTPUT_SCHEMAS = {
    "base": "hepta.agent-os-rootful-systemd-base-reviewed-provenance.v1",
    "builder": (
        "hepta.agent-os-rootful-systemd-isolated-builder-"
        "reviewed-provenance.v1"),
    "apparmor": (
        "hepta.agent-os-rootful-systemd-apparmor-reviewed-provenance.v1"),
    "docker_namespace": (
        "hepta.agent-os-rootful-systemd-docker-apparmor-namespace-"
        "reviewed-provenance.v1"),
}
REVIEW_OUTPUT_FILENAMES = {
    "base": "reviewed-base-image-provenance.v1.json",
    "builder": "reviewed-isolated-builder-provenance.v1.json",
    "apparmor": "reviewed-apparmor-provenance.v1.json",
    "docker_namespace":
        "reviewed-docker-apparmor-namespace-provenance.v1.json",
}
WATCH_HANDOFF_PRODUCER_FIELDS = frozenset({"path", "file_sha256"})
OUTPUT_BINDING_FIELDS = frozenset({
    "path", "file_sha256", "body_sha256", "schema", "version", "status",
})
OUTPUT_FIELDS = frozenset({
    "schema", "version", "status", "evaluated_at_ms", "expires_at_ms",
    "round", "domain", "campaign_id", "source_baseline_sha256",
    "strategy_sha256",
    "input_bindings", "findings", "paper_test_admission_candidate",
    "paper_authorized", "live_authorized", "mutation_authorized",
    "direct_broker_access", "order_submission_authorized",
    "authorization_effect", "body_sha256",
})
INSTALL_PAPER_UNITS = (
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
INSTALL_BLOCKING_UNITS = (
    "hepta-tool-gateway@alpha.service",
    "hepta-tool-gateway@alpha.socket",
    "hepta-tool-session-supervisor@alpha.socket",
    "hepta-p1-watch-activation.service",
    "hepta-p1-watch-activation-reconcile.service",
    "hepta-p1-watch-activation-reconcile.timer",
    "hepta-shadow-watch-custodian@alpha.service",
    "hepta-shadow-watch-custodian-reconcile@alpha.service",
    "hepta-shadow-watch-custodian-reconcile@alpha.timer",
    "hepta-shadow-watch-collector@alpha.service",
    "hepta-shadow-watch-collector@alpha.timer",
    "hepta-shadow-watch-export@alpha.service",
)
TRANSACTION_LOCK_FIELDS = frozenset({
    "path", "device", "inode", "nlink", "uid", "gid", "mode", "size",
    "mtime_ns", "ctime_ns", "created_during_transaction", "persistent",
    "held_during_transaction",
})
INSTALL_PREFLIGHT_FIELDS = frozenset({
    "domain", "paper_units", "installation_blocking_units",
    "campaign_policy_count", "kill_switch_engaged", "broker_egress_deny_all",
})
PROFILE_FILE_EVIDENCE_FIELDS = frozenset({
    "path", "sha256", "bytes", "device", "inode", "mode", "nlink",
    "uid", "gid", "mtime_ns", "ctime_ns",
})
PROFILE_LEGACY_RECEIPT_EVIDENCE_FIELDS = frozenset({
    *PROFILE_FILE_EVIDENCE_FIELDS, "body_sha256",
})
PROFILE_PREFLIGHT_FIELDS = frozenset({
    "gateway_units", "gateway_masks", "gateway_unit_closure",
    "systemd_manager", "manager_unit_contracts", "broker_egress_unit",
    "broker_egress_check", "paper_units", "campaign_policy_count",
    "kill_switch_engaged", "watch_boundary",
    "broker_egress_deny_all_observed",
})
BROKER_AFTER_FIELDS = frozenset({
    "unit", "active_state", "sub_state", "main_pid", "invocation_id",
    "exec_main_start_timestamp_monotonic_us", "process_starttime_ticks",
    "interpreter_path", "interpreter_sha256", "credential_source_path",
    "credential_source_sha256", "installed_source_path",
    "installed_source_sha256", "cmdline_sha256", "status_text",
    "tasks_current", "deny_all_policy_sha256", "authorized_connectors",
    "authorized_uids", "protected_ports", "unit_contract_sha256",
})
GATEWAY_AFTER_FIELDS = frozenset({
    "unit", "active_state", "sub_state", "gateway_main_pid",
    "gateway_invocation_id", "gateway_exec_main_start_timestamp_monotonic_us",
    "process_starttime_ticks", "gateway_executable_path",
    "gateway_executable_sha256", "domain_config_sha256",
    "gateway_profile_path", "gateway_profile_sha256",
    "gateway_process_profile_sha256", "execution_remote_mode",
    "tool_account", "execution_domain_id", "tool_allow_trade",
    "session_templates", "contract_bindings", "gateway_socket_path",
    "gateway_socket_device", "gateway_socket_inode", "supervisor_socket_path",
    "supervisor_socket_device", "supervisor_socket_inode",
    "unit_contract_sha256",
})
WATCH_BOUNDARY_FIELDS = frozenset({
    "export_absent", "sessions_authority_count", "private_authority_count",
    "custodian_transaction_absent", "session_bootstrap_idle_lock_observed",
})
RECONCILE_TIMER_FIELDS = frozenset({
    "unit", "load_state", "active_state", "sub_state", "job",
    "unit_file_state", "unit_contract_sha256",
})
DUAL_DOMAIN_EXPECTED_CHECKS = frozenset({
    "real_systemd_pid1_and_private_cgroup",
    "all_four_fixture_units_loaded",
    "watch_and_inert_paper_concurrent_same_boot",
    "uid_gid_sets_pairwise_distinct", "watch_socket_cross_domain_denied",
    "paper_socket_cross_domain_denied",
    "watch_paper_socket_cross_plane_denied",
    "watch_credentials_cross_domain_denied",
    "paper_credentials_cross_domain_denied",
    "control_directories_cross_plane_denied",
    "session_tokens_cross_domain_denied",
    "paper_kill_switch_engaged_initially",
    "paper_kill_switch_engaged_through_faults",
    "paper_kill_switch_engaged_finally", "watchdog_timeout_restarted_watch",
    "service_crash_restarted_inert_paper", "socket_reactivation_remained_inert",
    "stale_generation_rejected", "generation_tombstones_bound_cleanup",
    "stopped_socket_paths_removed", "all_fixture_units_inactive_after_cleanup",
    "authority_residue_absent_after_cleanup", "loopback_only_container_network",
    "zero_broker_ports_and_protocol", "zero_orders_and_all_authority_flags_false",
})
ROOTFUL_EXPECTED_CHECKS = frozenset({
    "real_templated_units_loaded",
    "preflight_manual_start_refused_before_authority",
    "socket_manual_start_refused_before_authority",
    "broker_guard_started_under_systemd",
    "idle_concurrent_cold_start_has_one_authority",
    "domain_b_full_composition_active", "domain_a_full_composition_active",
    "second_domain_flock_rejected_without_listener",
    "daemon_sigkill_restarts_under_same_authority",
    "startup_failure_hits_composition_start_limit_and_reclaims_all",
    "systemd_exec_stop_post_is_input_independent_deny_all",
    "stopped_socket_cannot_reactivate_daemon",
})
NETWORK_EXPECTED_CHECKS = frozenset({
    "fixed_only_default", "all_agent_gateway_simulator_uids_denied",
    "domain_ib_uids_denied_before_opt_in",
    "second_domain_manifest_rejected_without_policy_change",
    "one_domain_ib_uid_allowed_after_exact_opt_in",
    "second_domain_ib_uid_denied_during_opt_in",
    "domain_ib_uids_denied_after_revocation",
    "fixed_ib_uid_disabled_in_templated_mode",
    "agent_non_broker_egress_preserved", "nft_syntax_checked_and_applied",
    "exact_live_nft_json_verified",
    "broker_guard_detects_table_flush_and_tightens",
    "broker_guard_detects_manifest_replacement_and_tightens",
    "authority_guard_holds_lifetime_host_lease",
    "second_domain_rejected_while_first_guard_active",
    "foreign_domain_exec_stop_post_is_noop",
    "second_domain_guard_allowed_after_first_stops",
    "clean_broker_guard_stop_revokes_all",
    "broker_exec_stop_post_revokes_all_after_sigkill",
    "authority_exec_stop_post_revokes_after_sigkill",
    "authority_sigkill_tombstone_blocks_competing_start",
    "authority_clean_stop_revokes_domain_preserves_broker_guard",
    "ipv4_and_ipv6_loopback_enforced",
})
NETWORK_EXPECTED_IDENTITIES = {
    "fixed_ib_uid": 2003,
    "authorized_domain_ib_uid": 2121,
    "rejected_second_domain_ib_uid": 2122,
    "agent_uids": [2004, 2104, 2105],
    "gateway_uids": [2001, 2101, 2102],
    "simulator_uids": [2002, 2111, 2112],
}
NETWORK_GATE_SOURCE_MODES = {
    "scripts/hepta_broker_egress_policy.py": "0644",
    "scripts/hepta_ib_paper_domain_authority.py": "0644",
    "systemd/hepta-broker-network-policy-v1.json": "0644",
    "systemd/hepta-service-identities-v1.json": "0644",
    "tests/broker_network_rootful/hepta_broker_network_opt_in_gate.py":
        "0644",
    "tests/broker_network_rootful/Dockerfile": "0644",
}
HARD_NETWORK_EXPECTED_CHECKS = frozenset({
    "root_disposable_provenance_bound",
    "clean_frozen_source_bound",
    "native_base_and_tooling_provenance_bound",
    "clean_initial_residue",
    "unique_netns_uid_cgroup_topology",
    "kill_switch_engaged_initially",
    "all_roles_denied_initially_all_protected_ports",
    "exact_execution_uid_cgroup_only_positive",
    "agent_gateway_simulator_all_denied",
    "wrong_execution_uid_denied",
    "wrong_execution_cgroup_denied",
    "no_real_ib_binary_credential_protocol_order",
    "forwarder_proxy_process_socket_inventory_zero_or_allowlisted",
    "host_firewall_flush_preserved_isolation",
    "host_firewall_reload_preserved_isolation",
    "execution_restart_preserved_isolation",
    "execution_sigkill_failed_closed_and_recovered",
    "sentinel_restart_preserved_isolation",
    "route_revoke_regrant_failed_closed",
    "interface_revoke_regrant_failed_closed",
    "execution_outbound_revocation_failed_closed",
    "broker_inbound_revocation_failed_closed",
    "bilateral_revocation_regrant_verified",
    "kill_switch_engaged_throughout",
    "final_deny_all",
    "final_namespaces_veth_cgroups_units_residue_zero",
    "host_firewall_restored",
    "final_forwarder_inventory_unchanged",
})

CERTIFICATION_BLOCKERS = (
    "reviewed-base-image-provenance-required",
    "reviewed-apparmor-profile-provenance-required",
    "reviewed-isolated-builder-buildx-provenance-required",
    "reviewed-docker-daemon-apparmor-namespace-boot-provenance-required",
)
CERTIFICATION_FIELDS = frozenset({
    "requested", "eligible", "provenance", "provenance_reopened_equal",
    "reviewed_base", "reviewed_buildkit", "buildx_toolchain",
    "isolated_builder", "isolated_builder_cleanup", "docker_socket_before",
    "docker_socket_after", "docker_socket_records_equal", "apparmor_before",
    "apparmor_after", "apparmor_records_equal", "docker_namespace_before",
    "docker_namespace_after", "docker_namespace_records_equal",
})
REVIEWED_PROVENANCE_SCHEMAS = {
    "base": "hepta.agent-os-rootful-systemd-base-reviewed-provenance.v1",
    "builder": (
        "hepta.agent-os-rootful-systemd-isolated-builder-"
        "reviewed-provenance.v1"),
    "apparmor": (
        "hepta.agent-os-rootful-systemd-apparmor-reviewed-provenance.v1"),
    "docker_namespace": (
        "hepta.agent-os-rootful-systemd-docker-apparmor-namespace-"
        "reviewed-provenance.v1"),
}
REVIEWED_PROVENANCE_FIELDS = {
    "base": frozenset({
        "schema", "decision", "issued_at_ms", "expires_at_ms", "image_id",
        "repo_digest", "labels_sha256",
    }),
    "builder": frozenset({
        "schema", "decision", "issued_at_ms", "expires_at_ms", "image_id",
        "repo_digest", "config_sha256", "buildkit_version", "buildx_version",
        "buildx_binary_sha256", "docker_server_version",
        "docker_server_api_version", "docker_server_git_commit",
    }),
    "apparmor": frozenset({
        "schema", "decision", "issued_at_ms", "expires_at_ms", "profile",
        "policy_source_sha256", "profile_sha256", "raw_sha256", "raw_abi",
    }),
    "docker_namespace": frozenset({
        "schema", "decision", "issued_at_ms", "expires_at_ms",
        "docker_daemon_id", "docker_daemon_pid",
        "docker_daemon_start_time_ticks", "host_boot_id",
        "host_namespace_name", "host_namespace_level",
        "host_namespace_stacked", "daemon_namespace_name",
        "daemon_namespace_level", "daemon_namespace_stacked",
    }),
}
PAPER_PROVENANCE_METADATA_FIELDS = frozenset({
    "path", "document_sha256", "root_owned", "canonical_json", "mode",
    "device", "inode", "nlink", "uid", "gid", "identity_sha256",
})
DUAL_PROVENANCE_METADATA_FIELDS = frozenset({
    "document_sha256", "root_owned", "canonical_json", "mode",
    "identity_sha256",
})

DUAL_RUNTIME_CAPABILITIES = (
    "CHOWN", "DAC_OVERRIDE", "FOWNER", "KILL", "MKNOD", "SETGID",
    "SETPCAP", "SETUID", "SYS_ADMIN", "SYS_CHROOT",
)
DUAL_RUNTIME_TMPFS = {
    "/etc/heptatrader": "rw,nosuid,nodev,noexec,mode=0755,size=8m",
    "/run": "rw,nosuid,nodev,mode=0755,size=64m",
    "/run/lock": "rw,nosuid,nodev,noexec,mode=0755,size=8m",
    "/tmp": "rw,nosuid,nodev,noexec,mode=1777,size=64m",
    "/var/lib": "rw,nosuid,nodev,noexec,mode=0755,size=64m",
    "/var/log": "rw,nosuid,nodev,noexec,mode=0755,size=32m",
    "/var/tmp": "rw,nosuid,nodev,noexec,mode=1777,size=32m",
}
PAPER_RUNTIME_CAPABILITIES = (
    "CHOWN", "DAC_OVERRIDE", "FOWNER", "KILL", "MKNOD", "NET_ADMIN",
    "SETGID", "SETPCAP", "SETUID", "SYS_ADMIN", "SYS_CHROOT",
)
PAPER_RUNTIME_TMPFS = {
    "/etc/heptatrader": "rw,nosuid,nodev,noexec,mode=0755,size=8m",
    "/usr/share/heptatrader": "rw,nosuid,nodev,noexec,mode=0755,size=8m",
    "/run": "rw,nosuid,nodev,mode=0755,size=64m",
    "/run/lock": "rw,nosuid,nodev,noexec,mode=0755,size=8m",
    "/tmp": "rw,nosuid,nodev,noexec,mode=1777,size=64m",
    "/var/lib/hepta-ib-execution-codex-a":
        "rw,nosuid,nodev,noexec,mode=0700,size=16m",
    "/var/lib/hepta-ib-execution-openclaw-b":
        "rw,nosuid,nodev,noexec,mode=0700,size=16m",
    "/var/log": "rw,nosuid,nodev,noexec,mode=0755,size=32m",
    "/var/tmp": "rw,nosuid,nodev,noexec,mode=1777,size=32m",
}
DUAL_EXPECTED_IDENTITIES = [
    {
        "plane": "WATCH", "domain_id": "codex-a",
        "name": "hepta-p1-watch-codex-a", "uid": 2211, "gid": 2211,
        "socket": "/run/hepta-p1-dual/watch-codex-a.sock",
        "credential":
            "/etc/heptatrader/credentials/watch/codex-a/lease.fixture",
        "runtime_directory": "/run/hepta-p1-watch-codex-a",
        "state_directory": "/var/lib/hepta-p1-watch-codex-a",
    },
    {
        "plane": "WATCH", "domain_id": "openclaw-b",
        "name": "hepta-p1-watch-openclaw-b", "uid": 2212, "gid": 2212,
        "socket": "/run/hepta-p1-dual/watch-openclaw-b.sock",
        "credential":
            "/etc/heptatrader/credentials/watch/openclaw-b/lease.fixture",
        "runtime_directory": "/run/hepta-p1-watch-openclaw-b",
        "state_directory": "/var/lib/hepta-p1-watch-openclaw-b",
    },
    {
        "plane": "PAPER_INERT", "domain_id": "codex-a",
        "name": "hepta-p1-paper-codex-a", "uid": 2231, "gid": 2231,
        "socket": "/run/hepta-p1-dual/paper-codex-a.sock",
        "credential": (
            "/etc/heptatrader/credentials/paper/codex-a/"
            "authorization.fixture"),
        "runtime_directory": "/run/hepta-p1-paper-codex-a",
        "state_directory": "/var/lib/hepta-p1-paper-codex-a",
        "control_directory": "/run/hepta-p1-dual/control/paper-codex-a",
        "kill_switch":
            "/run/hepta-p1-dual/control/paper-codex-a/kill-switch",
    },
    {
        "plane": "PAPER_INERT", "domain_id": "openclaw-b",
        "name": "hepta-p1-paper-openclaw-b", "uid": 2232, "gid": 2232,
        "socket": "/run/hepta-p1-dual/paper-openclaw-b.sock",
        "credential": (
            "/etc/heptatrader/credentials/paper/openclaw-b/"
            "authorization.fixture"),
        "runtime_directory": "/run/hepta-p1-paper-openclaw-b",
        "state_directory": "/var/lib/hepta-p1-paper-openclaw-b",
        "control_directory":
            "/run/hepta-p1-dual/control/paper-openclaw-b",
        "kill_switch":
            "/run/hepta-p1-dual/control/paper-openclaw-b/kill-switch",
    },
]
DUAL_EXPECTED_FAULTS = {
    "watchdog_timeout": ("WATCH", "codex-a"),
    "service_crash_restart": ("PAPER_INERT", "openclaw-b"),
    "socket_reactivation": ("PAPER_INERT", "codex-a"),
}
DUAL_EXPECTED_BOUNDARY = {
    "same_systemd_environment_count": 1, "watch_domains": 2,
    "inert_paper_domains": 2, "distinct_uids": 4, "distinct_gids": 4,
    "kill_switch_state": "engaged", "broker_connectors": 0,
    "broker_connections": 0, "broker_protocol_messages": 0,
    "paper_orders": 0, "paper_authorized": False,
    "live_authorized": False, "mutation_authorized": False,
    "direct_broker_access": False, "host_bind_mounts": 0,
    "host_systemd_units_touched": 0, "host_network_rules_touched": 0,
    "real_credentials": 0, "inert_credentials": 4,
}
PAPER_EXPECTED_INNER_BOUNDARY = {
    "paper_unit_instances_observed": 8,
    "broker_policy_unit_observed": 1,
    "domain_compositions_observed": 2,
    "max_concurrent_inert_execution_stub_processes": 1,
    "ib_api_binaries": 0, "real_broker_connections": 0,
    "broker_protocol_messages": 0, "real_credentials": 0,
    "inert_credential_fixtures": 4, "paper_orders": 0,
    "live_authorized": False, "host_systemd_units_touched": 0,
    "host_nft_tables_touched": 0,
}
PAPER_EXPECTED_OUTER_BOUNDARY = {
    "host_root_sentinel_required": False,
    "host_systemd_units_touched": 0, "host_nft_tables_touched": 0,
    "real_broker_connections": 0, "broker_protocol_messages": 0,
    "real_credentials": 0, "paper_orders": 0,
    "paper_units_instantiated": 8, "inert_stub_only": True,
    "fixture_local_authority_only": True,
    "paper_test_admission_candidate": False,
    "paper_authorized": False, "live_authorized": False,
    "mutation_authorized": False, "direct_broker_access": False,
    "order_submission_authorized": False,
}
DUAL_GATE_SOURCE_MODES = {
    "scripts/run_hepta_p1_dual_domain_rootful_gate.py": "0644",
    "tests/p1_dual_domain_rootful_systemd/Dockerfile": "0644",
    "tests/p1_dual_domain_rootful_systemd/"
    "hepta-p1-dual-domain-systemd-entrypoint": "0755",
    "tests/p1_dual_domain_rootful_systemd/"
    "hepta-p1-dual-domain-rootful.target": "0644",
    "tests/p1_dual_domain_rootful_systemd/"
    "hepta_p1_dual_domain_daemon.py": "0755",
    "tests/p1_dual_domain_rootful_systemd/"
    "hepta_p1_dual_domain_inner_gate.py": "0755",
    "tests/p1_dual_domain_rootful_systemd/"
    "hepta-p1-dual-watch@.service": "0644",
    "tests/p1_dual_domain_rootful_systemd/"
    "hepta-p1-dual-watch@.socket": "0644",
    "tests/p1_dual_domain_rootful_systemd/"
    "hepta-p1-dual-paper@.service": "0644",
    "tests/p1_dual_domain_rootful_systemd/"
    "hepta-p1-dual-paper@.socket": "0644",
}
PAPER_GATE_SOURCE_MODES = {
    "scripts/run_hepta_paper_domain_rootful_systemd_gate.py": "0644",
    "scripts/hepta_broker_egress_policy.py": "0755",
    "scripts/hepta_ib_paper_domain_authority.py": "0755",
    "systemd/hepta-broker-egress-policy.service": "0644",
    "systemd/hepta-ib-paper-domain-preflight@.service": "0644",
    "systemd/hepta-execution-ib-paper@.service": "0644",
    "systemd/hepta-execution-ib-paper@.socket": "0644",
    "systemd/hepta-execution-events-ib-paper@.socket": "0644",
    "systemd/hepta-execution-ib-paper@.service.d/"
    "10-hepta-broker-egress-policy.conf": "0644",
    "systemd/hepta-broker-network-policy-v1.json": "0644",
    "systemd/hepta-service-identities-v1.json": "0644",
    "tests/paper_domain_rootful_systemd/"
    "hepta_paper_inert_execution_stub.py": "0755",
    "tests/paper_domain_rootful_systemd/Dockerfile": "0644",
    "tests/paper_domain_rootful_systemd/"
    "hepta-paper-domain-systemd-entrypoint": "0755",
    "tests/paper_domain_rootful_systemd/"
    "hepta-paper-domain-rootful-systemd.target": "0644",
    "tests/paper_domain_rootful_systemd/"
    "hepta_paper_domain_rootful_inner_gate.py": "0755",
}


class AdmissionError(RuntimeError):
    """One stable fail-closed verifier error."""

    def __init__(self, reason: str, *, dangerous: bool = True):
        super().__init__(reason)
        self.reason = reason
        self.dangerous = dangerous


@dataclass(frozen=True)
class InputSnapshot:
    name: str
    path: Path
    payload: bytes
    metadata: os.stat_result
    document: dict[str, Any]
    file_sha256: str
    body_sha256: str


@dataclass(frozen=True)
class Facts:
    source: str | None = None
    domain: str | None = None
    campaign: str | None = None
    issued_at_ms: int | None = None
    expires_at_ms: int | None = None
    status: str = "VALID"
    readiness: tuple[str, ...] = ()
    dangers: tuple[str, ...] = ()
    strategy_sha256: str | None = None


@dataclass(frozen=True)
class Evaluation:
    receipt: dict[str, Any]
    snapshots: Mapping[str, InputSnapshot]


@dataclass(frozen=True)
class LoadedProducerModule:
    path: Path
    payload: bytes
    metadata: os.stat_result
    module: ModuleType

    def reopen(self) -> None:
        payload, metadata = secure_read(
            self.path, expected_uid=ROOT_UID,
            modes=frozenset({0o500, 0o555, 0o700, 0o755}))
        _require(
            payload == self.payload and
            _identity(metadata) == _identity(self.metadata),
            "ADMISSION_ZERO_PRODUCER_REBOUND")


@dataclass(frozen=True)
class BoundRuntimeFile:
    path: Path
    payload: bytes
    metadata: os.stat_result
    expected_uid: int
    modes: frozenset[int]
    maximum: int
    minimum: int
    reason: str

    def reopen(self) -> None:
        payload, metadata = secure_read(
            self.path, expected_uid=self.expected_uid, modes=self.modes,
            maximum=self.maximum, minimum=self.minimum)
        _require(
            payload == self.payload and
            _identity(metadata) == _identity(self.metadata), self.reason)


@dataclass(frozen=True)
class StagedRuntimeFile:
    relative_path: PurePosixPath
    payload: bytes
    metadata: os.stat_result
    mode: int


@dataclass(frozen=True)
class RootfsRuntimeFile:
    """One immutable payload at its child-visible absolute path."""

    logical_path: Path
    payload: bytes
    mode: int
    binding: BoundRuntimeFile | None

    def reopen(self) -> None:
        if self.binding is not None:
            self.binding.reopen()


@dataclass(frozen=True)
class ReleaseCausalRuntime:
    interpreter: BoundRuntimeFile
    verifier: BoundRuntimeFile
    runtime_modules: tuple[BoundRuntimeFile, ...]
    package_manifests: tuple[BoundRuntimeFile, ...]
    rootfs_files: tuple[RootfsRuntimeFile, ...]

    def reopen(self) -> None:
        self.interpreter.reopen()
        self.verifier.reopen()
        seen: set[int] = {id(self.interpreter), id(self.verifier)}
        for binding in (*self.runtime_modules, *self.package_manifests):
            if id(binding) not in seen:
                binding.reopen()
                seen.add(id(binding))
        for entry in self.rootfs_files:
            if entry.binding is not None and id(entry.binding) not in seen:
                entry.binding.reopen()
                seen.add(id(entry.binding))


@dataclass(frozen=True)
class ReleaseCausalStage:
    path: Path
    verifier_relative_path: PurePosixPath
    owner_uid: int
    parent_before_identity: tuple[int, ...]
    parent_active_identity: tuple[int, ...]
    root_metadata: os.stat_result
    directory_metadata: Mapping[PurePosixPath, os.stat_result]
    files: tuple[StagedRuntimeFile, ...]
    private_mount_namespace: bool = False
    pinned_root_descriptor: int | None = None

    @property
    def verifier_path(self) -> Path:
        return self.path.joinpath(*self.verifier_relative_path.parts)

    @property
    def child_verifier_path(self) -> Path:
        return Path("/").joinpath(*self.verifier_relative_path.parts)

    def reopen(self) -> None:
        _reopen_release_causal_stage(self)

    def cleanup(self) -> None:
        _cleanup_release_causal_stage(self)


@dataclass(frozen=True)
class ReleaseCausalVerification:
    closure: InputSnapshot
    runtime: ReleaseCausalRuntime
    evidence_dependencies: tuple[BoundRuntimeFile, ...]

    def reopen(self) -> None:
        payload, metadata = secure_read(
            self.closure.path, expected_uid=ROOT_UID,
            maximum=MAXIMUM_INPUT_BYTES, modes=frozenset({0o600}))
        _require(
            payload == self.closure.payload and
            _identity(metadata) == _identity(self.closure.metadata),
            "ADMISSION_RELEASE_CAUSAL_CLOSURE_REBOUND")
        self.runtime.reopen()
        for binding in self.evidence_dependencies:
            binding.reopen()


def canonical_bytes(value: Any) -> bytes:
    return (json.dumps(
        value, ensure_ascii=True, allow_nan=False, sort_keys=True,
        separators=(",", ":")) + "\n").encode("utf-8")


def pretty_baseline_bytes(value: Any) -> bytes:
    return (json.dumps(
        value, ensure_ascii=True, allow_nan=False, sort_keys=True,
        indent=2) + "\n").encode("utf-8")


def digest_bytes(payload: bytes) -> str:
    return "sha256:" + hashlib.sha256(payload).hexdigest()


def _wall_clock_ms() -> int:
    """Return the non-injectable clock used at production commit seams."""

    return time.time_ns() // 1_000_000


def seal(body: Mapping[str, Any]) -> dict[str, Any]:
    document = dict(body)
    document["body_sha256"] = digest_bytes(canonical_bytes(document))
    return document


def _require(condition: bool, reason: str, *, dangerous: bool = True) -> None:
    if not condition:
        raise AdmissionError(reason, dangerous=dangerous)


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


def _open_anchored_directory(path: Path, reason: str) -> int:
    path = _canonical_path(path, reason)
    try:
        descriptor = os.open("/", DIRECTORY_FLAGS)
    except OSError as error:
        raise AdmissionError(reason) from error
    try:
        for component in path.parts[1:]:
            before = os.stat(component, dir_fd=descriptor,
                             follow_symlinks=False)
            child = os.open(component, DIRECTORY_FLAGS, dir_fd=descriptor)
            opened = os.fstat(child)
            _require(
                stat.S_ISDIR(opened.st_mode) and
                _directory_identity(before) ==
                    _directory_identity(opened), reason)
            os.close(descriptor)
            descriptor = child
        return descriptor
    except (OSError, AdmissionError) as error:
        os.close(descriptor)
        if isinstance(error, AdmissionError):
            raise
        if isinstance(error, FileNotFoundError):
            raise AdmissionError(reason, dangerous=False) from error
        raise AdmissionError(reason) from error


def _trusted_directory_identity(
    descriptor: int, *, expected_uid: int, reason: str,
) -> tuple[int, ...]:
    """Return a stable identity only for an owner-controlled directory."""

    try:
        metadata = os.fstat(descriptor)
    except OSError as error:
        raise AdmissionError(reason) from error
    _require(
        stat.S_ISDIR(metadata.st_mode) and
        metadata.st_uid == expected_uid and
        stat.S_IMODE(metadata.st_mode) & 0o022 == 0,
        reason,
    )
    return _directory_identity(metadata)


def secure_read(
    path: Path,
    *,
    expected_uid: int,
    maximum: int = MAXIMUM_INPUT_BYTES,
    minimum: int = 1,
    modes: frozenset[int] = frozenset({0o400, 0o440, 0o600, 0o640, 0o644}),
) -> tuple[bytes, os.stat_result]:
    """Read a single-link regular file through an anchored no-follow path."""

    _require(0 <= minimum <= maximum, "ADMISSION_INPUT_LIMIT_INVALID")
    path = _canonical_path(path, "ADMISSION_INPUT_PATH_INVALID")
    try:
        parent = _open_anchored_directory(
            path.parent, "ADMISSION_INPUT_PARENT_INVALID")
    except AdmissionError as error:
        if not error.dangerous:
            raise AdmissionError(
                "ADMISSION_INPUT_MISSING", dangerous=False) from error
        raise
    try:
        parent_identity = _trusted_directory_identity(
            parent, expected_uid=expected_uid,
            reason="ADMISSION_INPUT_PARENT_UNTRUSTED")
        try:
            before = os.stat(path.name, dir_fd=parent, follow_symlinks=False)
            descriptor = os.open(path.name, READ_FLAGS, dir_fd=parent)
        except FileNotFoundError as error:
            raise AdmissionError(
                "ADMISSION_INPUT_MISSING", dangerous=False) from error
        except OSError as error:
            raise AdmissionError("ADMISSION_INPUT_OPEN_INVALID") from error
        try:
            opened = os.fstat(descriptor)
            _require(
                stat.S_ISREG(opened.st_mode) and opened.st_nlink == 1 and
                opened.st_uid == expected_uid and
                stat.S_IMODE(opened.st_mode) in modes and
                minimum <= opened.st_size <= maximum and
                _identity(before) == _identity(opened),
                "ADMISSION_INPUT_METADATA_INVALID")
            chunks: list[bytes] = []
            remaining = maximum + 1
            while remaining:
                chunk = os.read(descriptor, min(65536, remaining))
                if not chunk:
                    break
                chunks.append(chunk)
                remaining -= len(chunk)
            payload = b"".join(chunks)
            after = os.fstat(descriptor)
            final = os.stat(path.name, dir_fd=parent, follow_symlinks=False)
            _require(
                minimum <= len(payload) <= maximum and
                _identity(opened) == _identity(after) == _identity(final) and
                parent_identity == _trusted_directory_identity(
                    parent, expected_uid=expected_uid,
                    reason="ADMISSION_INPUT_PARENT_REBOUND"),
                "ADMISSION_INPUT_REBOUND")
            return payload, opened
        except OSError as error:
            raise AdmissionError("ADMISSION_INPUT_READ_INVALID") from error
        finally:
            os.close(descriptor)
    finally:
        os.close(parent)


def strict_object(payload: bytes, reason: str) -> dict[str, Any]:
    def unique(values: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in values:
            if key in result:
                raise AdmissionError(reason)
            result[key] = value
        return result

    try:
        value = json.loads(
            payload.decode("utf-8", errors="strict"),
            object_pairs_hook=unique,
            parse_constant=lambda _value: (_ for _ in ()).throw(
                AdmissionError(reason)),
        )
    except AdmissionError:
        raise
    except (UnicodeError, json.JSONDecodeError) as error:
        raise AdmissionError(reason) from error
    _require(isinstance(value, dict), reason)
    return value


def _sealed(document: dict[str, Any], fields: frozenset[str], reason: str) -> str:
    _require(set(document) == fields, reason)
    claimed = document.get("body_sha256")
    _require(type(claimed) is str and DIGEST.fullmatch(claimed) is not None,
             reason)
    body = dict(document)
    del body["body_sha256"]
    _require(claimed == digest_bytes(canonical_bytes(body)), reason)
    return claimed


def _digest(value: Any, reason: str) -> str:
    _require(type(value) is str and DIGEST.fullmatch(value) is not None, reason)
    return value


def _safe_token(value: Any, pattern: re.Pattern[str], reason: str) -> str:
    _require(type(value) is str and pattern.fullmatch(value) is not None, reason)
    return value


def _integer(value: Any, reason: str, minimum: int = 0) -> int:
    _require(type(value) is int and value >= minimum, reason)
    return value


def _boundary_findings(
    document: Mapping[str, Any], name: str,
    fields: tuple[str, ...] = (
        "paper_authorized", "live_authorized", "mutation_authorized",
        "direct_broker_access", "order_submission_authorized"),
) -> tuple[str, ...]:
    findings: list[str] = []
    for field in fields:
        if field not in document:
            continue
        value = document[field]
        _require(type(value) is bool, f"{name}_BOUNDARY_INVALID")
        if value:
            findings.append(f"{name}_{field.upper()}_DANGEROUS")
    return tuple(findings)


def _times(document: Mapping[str, Any], prefix: str) -> tuple[int, int]:
    issued = _integer(document.get("issued_at_ms"), f"{prefix}_TIME_INVALID")
    expires = _integer(document.get("expires_at_ms"), f"{prefix}_TIME_INVALID")
    _require(issued < expires, f"{prefix}_TIME_INVALID")
    return issued, expires


def _reference(value: Any, reason: str) -> dict[str, str]:
    _require(isinstance(value, dict) and set(value) == REFERENCE_FIELDS, reason)
    path = _canonical_path(Path(value.get("path", "")), reason)
    return {
        "path": str(path),
        "file_sha256": _digest(value.get("file_sha256"), reason),
        "body_sha256": _digest(value.get("body_sha256"), reason),
    }


def _executable_reference(value: Any, reason: str) -> dict[str, str]:
    _require(
        isinstance(value, dict) and
        set(value) == EXECUTABLE_REFERENCE_FIELDS, reason)
    path = _canonical_path(Path(value.get("path", "")), reason)
    return {
        "path": str(path),
        "file_sha256": _digest(value.get("file_sha256"), reason),
    }


def _signed_reference(value: Any, reason: str) -> dict[str, str]:
    _require(
        isinstance(value, dict) and set(value) == SIGNED_REFERENCE_FIELDS,
        reason)
    path = _canonical_path(Path(value.get("path", "")), reason)
    return {
        "path": str(path),
        "file_sha256": _digest(value.get("file_sha256"), reason),
        "signed_payload_sha256": _digest(
            value.get("signed_payload_sha256"), reason),
    }


def _reservation_reference(value: Any, reason: str) -> dict[str, Any]:
    _require(
        isinstance(value, dict) and
        set(value) == RESERVATION_REFERENCE_FIELDS, reason)
    result = dict(value)
    _require(
        _canonical_path(Path(result.get("path", "")), reason) ==
            HOST_AUTHORITY_OWNER_PATH,
        reason)
    _digest(result.get("file_sha256"), reason)
    _digest(result.get("body_sha256"), reason)
    for field in (
        "device", "inode", "uid", "gid", "mode", "size", "mtime_ns",
        "ctime_ns",
    ):
        _integer(result.get(field), reason)
    _require(
        result["device"] > 0 and result["inode"] > 0 and
        result["uid"] == ROOT_UID and result["gid"] == ROOT_GID and
        result["mode"] == 0o600 and result["size"] > 0,
        reason)
    return result


def _historical_host_authority_lease(
    value: Any, reason: str,
) -> dict[str, Any]:
    _require(
        isinstance(value, dict) and
        set(value) == HOST_AUTHORITY_LEASE_FIELDS, reason)
    result = dict(value)
    for field, expected in {
        "directory_path": HOST_AUTHORITY_DIRECTORY,
        "lease_path": HOST_AUTHORITY_LEASE_PATH,
        "owner_path": HOST_AUTHORITY_OWNER_PATH,
    }.items():
        _require(
            _canonical_path(Path(result.get(field, "")), reason) == expected,
            reason)
    for field in (
        "directory_device", "directory_inode", "directory_uid",
        "directory_gid", "directory_mode", "lease_device", "lease_inode",
        "lease_uid", "lease_gid", "lease_mode", "lease_size",
    ):
        _integer(result.get(field), reason)
    _safe_token(result.get("boot_id"), BOOT_ID, reason)
    _require(
        result["directory_device"] > 0 and
        result["directory_inode"] > 0 and result["lease_device"] > 0 and
        result["lease_inode"] > 0 and result["directory_uid"] == ROOT_UID and
        result["directory_gid"] == ROOT_GID and
        result["directory_mode"] == 0o700 and
        result["lease_uid"] == ROOT_UID and result["lease_gid"] == ROOT_GID and
        result["lease_mode"] == 0o600 and result["lease_size"] == 0 and
        result.get("held_exclusive") is True,
        reason)
    return result


def _signature_attestation(value: Any, reason: str) -> None:
    _require(
        isinstance(value, dict) and
        set(value) == SIGNATURE_ATTESTATION_FIELDS, reason)
    _require(
        value.get("algorithm") == "ED25519" and
        value.get("return_code") == 0 and
        value.get("stdout") == "Signature Verified Successfully\n" and
        value.get("stderr") == "" and
        value.get("stdout_sha256") ==
            digest_bytes(b"Signature Verified Successfully\n") and
        value.get("stderr_sha256") == digest_bytes(b""),
        reason)
    _executable_reference(value.get("public_key"), reason)
    _executable_reference(value.get("verifier"), reason)
    _digest(value.get("signature_sha256"), reason)
    _digest(value.get("signed_payload_sha256"), reason)


def _canonical_object_digest(value: Any) -> str:
    payload = json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=True,
        allow_nan=False).encode("ascii")
    return digest_bytes(payload)


def _environment_trust_bindings(
    value: Any, reason: str,
) -> dict[str, dict[str, str]]:
    _require(
        type(value) is dict and set(value) == ENVIRONMENT_TRUST_FIELDS,
        reason)
    result: dict[str, dict[str, str]] = {}
    for key in ENVIRONMENT_TRUST_FIELDS:
        reference = value.get(key)
        _require(
            type(reference) is dict and
            set(reference) == ENVIRONMENT_REFERENCE_FIELDS and
            type(reference.get("path")) is str and
            reference["path"].startswith("/") and
            os.path.normpath(reference["path"]) == reference["path"] and
            reference["path"] == ENVIRONMENT_TRUST_PATHS[key],
            reason)
        _digest(reference.get("sha256"), reason)
        result[key] = reference
    return result


def _environment_observations(value: Any, reason: str) -> dict[str, Any]:
    _require(
        type(value) is dict and set(value) == ENVIRONMENT_OBSERVATION_FIELDS,
        reason)
    base = value.get("base_image")
    _require(
        type(base) is dict and set(base) == ENVIRONMENT_BASE_FIELDS, reason)
    base_repo_digests = base.get("repo_digests")
    _require(
        type(base.get("image_id")) is str and
        ENVIRONMENT_IMAGE_ID.fullmatch(base["image_id"]) is not None and
        type(base.get("repo_digest")) is str and
        PINNED_IMAGE.fullmatch(base["repo_digest"]) is not None and
        type(base_repo_digests) is list and
        base_repo_digests == sorted(base_repo_digests) and
        len(base_repo_digests) == len(set(base_repo_digests)) and
        base["repo_digest"] in base_repo_digests and
        all(type(item) is str and PINNED_IMAGE.fullmatch(item) is not None
            for item in base_repo_digests) and
        base.get("labels_sha256") ==
            _canonical_object_digest(ENVIRONMENT_BASE_LABELS) and
        base.get("os") == "linux" and base.get("architecture") == "amd64" and
        base.get("declared_volumes") == 0 and
        base.get("onbuild_instructions") == 0,
        reason)

    builder = value.get("isolated_builder")
    _require(
        type(builder) is dict and
        set(builder) == ENVIRONMENT_BUILDER_FIELDS, reason)
    builder_repo_digests = builder.get("repo_digests")
    buildx_path = builder.get("buildx_path")
    _require(
        type(builder.get("image_id")) is str and
        ENVIRONMENT_IMAGE_ID.fullmatch(builder["image_id"]) is not None and
        type(builder.get("repo_digest")) is str and
        PINNED_IMAGE.fullmatch(builder["repo_digest"]) is not None and
        type(builder_repo_digests) is list and
        builder_repo_digests == sorted(builder_repo_digests) and
        len(builder_repo_digests) == len(set(builder_repo_digests)) and
        builder["repo_digest"] in builder_repo_digests and
        all(type(item) is str and PINNED_IMAGE.fullmatch(item) is not None
            for item in builder_repo_digests) and
        all(type(builder.get(field)) is str and
            DIGEST.fullmatch(builder[field]) is not None
            for field in (
                "config_sha256", "buildkit_binary_sha256",
                "buildx_path_sha256", "buildx_binary_sha256")) and
        builder.get("os") == "linux" and
        builder.get("architecture") == "amd64" and
        builder.get("entrypoint") in (
            ["buildkitd"], ["/usr/bin/buildkitd"],
            ["/usr/local/bin/buildkitd"]) and
        builder.get("buildkit_binary_path") in (
            "/usr/bin/buildkitd", "/usr/local/bin/buildkitd") and
        type(builder.get("buildkit_version")) is str and
        ENVIRONMENT_BUILDKIT_VERSION.fullmatch(
            builder["buildkit_version"]) is not None and
        type(buildx_path) is str and buildx_path.startswith("/") and
        digest_bytes(buildx_path.encode("utf-8")) ==
            builder.get("buildx_path_sha256") and
        type(builder.get("buildx_version")) is str and
        ENVIRONMENT_SEMVER.fullmatch(builder["buildx_version"]) is not None and
        type(builder.get("docker_server_version")) is str and
        ENVIRONMENT_SEMVER.fullmatch(
            builder["docker_server_version"]) is not None and
        type(builder.get("docker_server_api_version")) is str and
        ENVIRONMENT_DOCKER_API_VERSION.fullmatch(
            builder["docker_server_api_version"]) is not None and
        type(builder.get("docker_server_git_commit")) is str and
        ENVIRONMENT_BUILD_ID.fullmatch(
            builder["docker_server_git_commit"]) is not None,
        reason)

    apparmor = value.get("apparmor")
    _require(
        type(apparmor) is dict and
        set(apparmor) == ENVIRONMENT_APPARMOR_FIELDS and
        apparmor.get("profile") == "hepta-systemd-gate" and
        apparmor.get("mode") == "enforce" and
        apparmor.get("attach") == "hepta-systemd-gate" and
        apparmor.get("learning_count") == 0 and
        all(type(apparmor.get(field)) is str and
            DIGEST.fullmatch(apparmor[field]) is not None
            for field in (
                "policy_source_sha256", "profile_sha256", "raw_sha256",
                "profile_inventory_sha256")) and
        type(apparmor.get("raw_abi")) is str and
        ENVIRONMENT_RAW_ABI.fullmatch(apparmor["raw_abi"]) is not None and
        type(apparmor.get("raw_data_id")) is str and
        re.fullmatch(r"[1-9][0-9]{0,19}", apparmor["raw_data_id"]) is not None
        and apparmor.get("namespace_name") == "root" and
        apparmor.get("namespace_level") == 0 and
        apparmor.get("namespace_stacked") is False,
        reason)

    docker = value.get("docker_namespace")
    _require(
        type(docker) is dict and set(docker) == ENVIRONMENT_DOCKER_FIELDS,
        reason)
    daemon_pid = docker.get("docker_daemon_pid")
    daemon_ticks = docker.get("docker_daemon_start_time_ticks")
    user_namespace_inode = docker.get("self_user_namespace_inode")
    _require(
        type(docker.get("docker_daemon_id")) is str and
        ENVIRONMENT_DAEMON_ID.fullmatch(
            docker["docker_daemon_id"]) is not None and
        type(daemon_pid) is int and 1 < daemon_pid <= 4_194_304 and
        type(daemon_ticks) is int and daemon_ticks > 0 and
        type(docker.get("docker_daemon_exe_sha256")) is str and
        DIGEST.fullmatch(docker["docker_daemon_exe_sha256"]) is not None and
        type(docker.get("host_boot_id")) is str and
        BOOT_ID.fullmatch(docker["host_boot_id"]) is not None and
        docker.get("host_namespace_name") == "root" and
        docker.get("host_namespace_level") == 0 and
        docker.get("host_namespace_stacked") is False and
        docker.get("daemon_namespace_name") == "root" and
        docker.get("daemon_namespace_level") == 0 and
        docker.get("daemon_namespace_stacked") is False and
        docker.get("daemon_apparmor_current") == "unconfined" and
        type(user_namespace_inode) is int and user_namespace_inode > 0 and
        docker.get("daemon_user_namespace_inode") == user_namespace_inode,
        reason)
    return value


def _environment_fingerprint(
    value: Any, review: Mapping[str, Any], reason: str,
) -> dict[str, Any]:
    _require(
        type(value) is dict and
        set(value) == ENVIRONMENT_FINGERPRINT_FIELDS, reason)
    body = dict(value)
    claimed = body.pop("body_sha256", None)
    observations = _environment_observations(
        value.get("observations"), reason)
    trust = _environment_trust_bindings(value.get("trust_bindings"), reason)
    _require(
        value.get("schema") == ENVIRONMENT_FINGERPRINT_SCHEMA and
        value.get("source_commit") == review.get("source_commit") and
        value.get("verifier_file_sha256") ==
            review["verifier"].get("file_sha256") and
        value.get("verifier_source_file_sha256") ==
            review["verifier"].get("source_file_sha256") and
        value.get("verifier_file_sha256") ==
            value.get("verifier_source_file_sha256") ==
            trust["producer"]["sha256"] and
        value.get("review_authority") == ENVIRONMENT_REVIEW_AUTHORITY and
        value.get("review_authority") ==
            review["closure"].get("review_authority") ==
            review["authorization"].get("review_authority") and
        type(value.get("reviewer_id")) is str and
        ENVIRONMENT_REVIEWER_ID.fullmatch(value["reviewer_id"]) is not None and
        value.get("reviewer_id") ==
            review["closure"].get("reviewer_id") ==
            review["authorization"].get("reviewer_id") and
        observations["base_image"]["repo_digest"] ==
            review.get("base_image_reference") and
        observations["isolated_builder"]["repo_digest"] ==
            review.get("buildkit_image_reference") and
        type(claimed) is str and DIGEST.fullmatch(claimed) is not None and
        claimed == digest_bytes(canonical_bytes(body)),
        reason)
    return value


def _environment_review_record(
    value: Any, reason: str, *, at_ms: int,
) -> dict[str, Any]:
    _require(isinstance(value, dict) and set(value) == REVIEW_RECORD_FIELDS,
             reason)
    _require(
        value.get("schema") ==
            "hepta.rootful-systemd-review-closure-verification.v1" and
        value.get("status") ==
            "VERIFIED_EXTERNALLY_SIGNED_REVIEW_CLOSURE" and
        all(value.get(field) is False for field in (
            "paper_authorized", "live_authorized", "mutation_authorized",
            "direct_broker_access", "order_submission_authorized")) and
        type(value.get("verified_at_ms")) is int and
        type(value.get("expires_at_ms")) is int and
        value["verified_at_ms"] <= at_ms + MAXIMUM_CLOCK_SKEW_MS and
        value["verified_at_ms"] < value["expires_at_ms"] and
        value["expires_at_ms"] > at_ms and
        value.get("reopened_after_invocation") is True and
        value.get("reopened_at_gate_end") is True and
        type(value.get("source_commit")) is str and
        GIT_HEAD.fullmatch(value["source_commit"]) is not None and
        type(value.get("base_image_reference")) is str and
        PINNED_IMAGE.fullmatch(value["base_image_reference"]) is not None and
        type(value.get("buildkit_image_reference")) is str and
        PINNED_IMAGE.fullmatch(value["buildkit_image_reference"]) is not None,
        reason)
    output_directory = _canonical_path(
        Path(value.get("output_directory", "")), reason)
    verifier = value.get("verifier")
    _require(
        isinstance(verifier, dict) and
        set(verifier) == REVIEW_VERIFIER_FIELDS and
        _canonical_path(Path(verifier.get("path", "")), reason) ==
            Path("/usr/libexec/hepta-rootful-systemd-environment-provenance")
        and verifier.get("source_commit") == value["source_commit"] and
        verifier.get("file_sha256") == verifier.get("source_file_sha256") and
        verifier.get("mode") == "0755" and verifier.get("uid") == ROOT_UID and
        verifier.get("gid") == ROOT_GID,
        reason)
    for field in ("file_sha256", "source_file_sha256", "identity_sha256"):
        _digest(verifier.get(field), reason)
    _canonical_path(Path(verifier.get("source_path", "")), reason)
    extensions = {
        "closure": frozenset({
            "closure_sha256", "review_authority", "reviewer_id"}),
        "request": frozenset({"request_sha256", "nonce"}),
        "authorization": frozenset({
            "signed_payload_sha256", "signature_sha256",
            "review_authority", "reviewer_id"}),
    }
    for label, extra in extensions.items():
        record = value.get(label)
        _require(
            isinstance(record, dict) and
            set(record) == REVIEW_FILE_RECORD_FIELDS | extra and
            record.get("uid") == ROOT_UID and record.get("gid") == ROOT_GID,
            reason)
        _canonical_path(Path(record.get("path", "")), reason)
        _digest(record.get("file_sha256"), reason)
        _digest(record.get("identity_sha256"), reason)
    _digest(value["closure"].get("closure_sha256"), reason)
    _digest(value["request"].get("request_sha256"), reason)
    _safe_token(value["request"].get("nonce"), NONCE, reason)
    _digest(value["authorization"].get("signed_payload_sha256"), reason)
    _digest(value["authorization"].get("signature_sha256"), reason)
    _require(
        value["closure"].get("mode") == "0400" and
        value["request"].get("mode") in {"0400", "0600"} and
        value["authorization"].get("mode") in {"0400", "0600"},
        reason)
    outputs = value.get("outputs")
    _require(
        isinstance(outputs, dict) and
        set(outputs) == set(REVIEW_OUTPUT_FILENAMES), reason)
    for key, record in outputs.items():
        _require(
            isinstance(record, dict) and
            set(record) == REVIEW_FILE_RECORD_FIELDS | {"schema"} and
            record.get("schema") == REVIEW_OUTPUT_SCHEMAS[key] and
            record.get("mode") == "0400" and record.get("uid") == ROOT_UID and
            record.get("gid") == ROOT_GID and
            _canonical_path(Path(record.get("path", "")), reason) ==
                output_directory / REVIEW_OUTPUT_FILENAMES[key],
            reason)
        _digest(record.get("file_sha256"), reason)
        _digest(record.get("identity_sha256"), reason)
    invocation = value.get("invocation")
    _require(
        isinstance(invocation, dict) and
        set(invocation) == REVIEW_INVOCATION_FIELDS and
        invocation.get("returncode") == 0 and
        invocation.get("exact_success_output") is True and
        invocation.get("no_shell") is True and
        type(invocation.get("duration_ms")) is int and
        invocation["duration_ms"] >= 0,
        reason)
    _digest(invocation.get("argv_sha256"), reason)
    _digest(invocation.get("stdout_sha256"), reason)
    _environment_fingerprint(
        value.get("environment_fingerprint"), value, reason)
    return value


def _environment_review_identity(value: Mapping[str, Any]) -> dict[str, Any]:
    """Return the sealed exact environment, excluding ceremony data."""

    fingerprint = value.get("environment_fingerprint")
    _require(type(fingerprint) is dict,
             "ROOTFUL_ENVIRONMENT_REVIEW_IDENTITY_INVALID")
    return fingerprint


def _rfc3339_ms(value: Any, reason: str) -> int:
    _require(type(value) is str and value.endswith("Z"), reason)
    try:
        parsed = datetime.fromisoformat(value[:-1] + "+00:00")
    except ValueError as error:
        raise AdmissionError(reason) from error
    _require(
        parsed.utcoffset() is not None and
        parsed.isoformat().replace("+00:00", "Z") == value,
        reason)
    return int(parsed.timestamp() * 1000)


def _release_binding(value: Any, reason: str, *, absolute: bool) -> dict[str, Any]:
    _require(
        isinstance(value, dict) and set(value) == RELEASE_BINDING_FIELDS,
        reason)
    path = value.get("path")
    _require(type(path) is str and bool(path), reason)
    if absolute:
        _canonical_path(Path(path), reason)
    else:
        candidate = Path(path)
        _require(
            not candidate.is_absolute() and path == candidate.as_posix() and
            ".." not in candidate.parts and "." not in candidate.parts,
            reason)
    _bare_digest(value.get("sha256"), reason)
    _require(
        type(value.get("size")) is int and value["size"] >= 0 and
        type(value.get("mode")) is str and
        re.fullmatch(r"0[0-7]{3}", value["mode"]) is not None,
        reason)
    return value


def validate_release_validation(document: dict[str, Any]) -> Facts:
    reason = "RELEASE_VALIDATION_RECEIPT_INVALID"
    _require(set(document) == RELEASE_VALIDATION_FIELDS, reason)
    _require(
        document.get("schema") ==
            "heptatrader.release-validation-closure.v1" and
        document.get("version") == 1 and
        document.get("project_id") == "heptatrader-agent-os" and
        document.get("round") == ROUND and
        type(document.get("release_version")) is str and
        VERSION_TOKEN.fullmatch(document["release_version"]) is not None and
        document.get("decision") == "GO" and
        document.get("passed") is True and
        document.get("candidate_scope") ==
            "paper-testing-admission-candidate-only" and
        document.get("safety_boundaries") == RELEASE_SAFETY_BOUNDARIES,
        reason)
    evaluated = _rfc3339_ms(document.get("evaluated_at"), reason)
    expires = _rfc3339_ms(document.get("expires_at"), reason)
    _require(evaluated < expires, reason)

    local = document.get("local_evidence")
    _require(
        isinstance(local, dict) and set(local) == RELEASE_LOCAL_FIELDS and
        local.get("profile") == "release-validation-p0-v1" and
        local.get("round") == ROUND and
        local.get("release_version") == document["release_version"] and
        type(local.get("artifact_directory")) is str and
        re.fullmatch(
            r"heptatrader-round114-engineering-artifacts-v[1-9][0-9]*",
            local["artifact_directory"]) is not None and
        local.get("safety_boundaries") == RELEASE_SAFETY_BOUNDARIES,
        reason)
    _bare_digest(local.get("input_manifest_sha256"), reason)
    _release_binding(local.get("source_baseline"), reason, absolute=False)
    lineage = local.get("source_lineage")
    _require(
        isinstance(lineage, dict) and
        set(lineage) == RELEASE_SOURCE_LINEAGE_FIELDS and
        GIT_HEAD.fullmatch(str(lineage.get("git_head", ""))) is not None,
        reason)
    for field in RELEASE_SOURCE_LINEAGE_FIELDS - {"git_head"}:
        _bare_digest(lineage.get(field), reason)

    verification = local.get("verification")
    _require(
        isinstance(verification, dict) and
        set(verification) == RELEASE_VERIFICATION_FIELDS and
        verification.get("maximum_age_seconds") == 24 * 60 * 60 and
        verification.get("fresh_until") == document["expires_at"],
        reason)
    _rfc3339_ms(verification.get("matrix_generated_at"), reason)
    _rfc3339_ms(verification.get("runner_generated_at"), reason)
    lanes = verification.get("lanes")
    _require(isinstance(lanes, list) and len(lanes) == 4, reason)
    lane_names: set[str] = set()
    combinations: set[tuple[bool, bool]] = set()
    for lane in lanes:
        _require(
            isinstance(lane, dict) and set(lane) == RELEASE_LANE_FIELDS and
            type(lane.get("name")) is str and bool(lane["name"]) and
            lane.get("build_type") == "Release" and
            lane.get("build_testing") is True and
            type(lane.get("ibapi_enabled")) is bool and
            type(lane.get("expected_tests")) is int and
            lane["expected_tests"] > 0 and
            lane.get("observed_tests") == lane["expected_tests"] and
            lane.get("selection") == [] and lane.get("passed") is True,
            reason)
        lane_names.add(lane["name"])
        no_git = "no-git" in lane["name"] or "no_git" in lane["name"]
        combinations.add((no_git, lane["ibapi_enabled"]))
    _require(len(lane_names) == 4 and combinations == {
        (False, False), (False, True), (True, False), (True, True)}, reason)

    delivery = local.get("delivery")
    _require(
        isinstance(delivery, dict) and set(delivery) == {
            "closure_sha256", "artifact_roles",
            "four_soaks_eight_rounds_verified"} and
        type(delivery.get("artifact_roles")) is list and
        len(delivery["artifact_roles"]) > 0 and
        len(delivery["artifact_roles"]) ==
            len(set(delivery["artifact_roles"])) and
        all(type(item) is str and bool(item)
            for item in delivery["artifact_roles"]) and
        delivery.get("four_soaks_eight_rounds_verified") is True,
        reason)
    _bare_digest(delivery.get("closure_sha256"), reason)
    native = local.get("native")
    _require(
        isinstance(native, dict) and set(native) == {
            "schema", "certification_level", "distinct_native_vms",
            "distinct_provisioner_attested_instances",
            "external_instance_receipts_verified",
            "runtime_contract_verified"} and
        native.get("schema") ==
            "hepta.execution-native-systemd-aggregate.v6" and
        native.get("certification_level") ==
            "native-disposable-vm-agent-os-watch-runtime-rootful-systemd" and
        native.get("distinct_native_vms") == 3 and
        native.get("distinct_provisioner_attested_instances") == 3 and
        native.get("external_instance_receipts_verified") is True and
        native.get("runtime_contract_verified") is True,
        reason)
    critical = local.get("critical_files")
    _require(isinstance(critical, list) and len(critical) >= 24, reason)
    roles: set[str] = set()
    paths: set[str] = set()
    for record in critical:
        _require(
            isinstance(record, dict) and
            set(record) == RELEASE_CRITICAL_FILE_FIELDS and
            type(record.get("role")) is str and bool(record["role"]), reason)
        _release_binding(
            {field: record[field] for field in RELEASE_BINDING_FIELDS},
            reason, absolute=False)
        roles.add(record["role"])
        paths.add(record["path"])
    required_roles = {
        "release-input-manifest", "round-closure",
        "source-baseline-manifest", "strict-source-bundle",
        "strict-source-bundle-manifest", "agent-os-source-bundle",
        "agent-os-source-manifest", "agent-os-source-policy",
        "runtime-package", "runtime-package-manifest", "test-matrix-report",
        "runner-identity-report", "native-runtime-aggregate",
        "native-variant-report-real", "native-variant-report-sandbox",
        "native-variant-report-stub",
        "native-instance-receipt-real", "native-instance-receipt-sandbox",
        "native-instance-receipt-stub",
    }
    _require(
        required_roles.issubset(roles) and len(roles) == len(critical) and
        len(paths) == len(critical) and
        any(role.startswith("supporting-evidence-") for role in roles),
        reason)

    retention = document.get("retention_evidence")
    _require(
        isinstance(retention, dict) and
        set(retention) == RELEASE_RETENTION_FIELDS and
        type(retention.get("evidence_root")) is str,
        reason)
    _canonical_path(Path(retention["evidence_root"]), reason)
    retention_inputs = retention.get("inputs")
    _require(
        isinstance(retention_inputs, dict) and
        set(retention_inputs) == RELEASE_RETENTION_INPUTS,
        reason)
    for binding in retention_inputs.values():
        _release_binding(binding, reason, absolute=True)
    retention_verification = retention.get("verification")
    _require(
        isinstance(retention_verification, dict) and
        set(retention_verification) ==
            RELEASE_RETENTION_VERIFICATION_FIELDS and
        retention_verification.get("schema") ==
            "heptatrader.evidence-ingestion-receipt-verification.v2" and
        retention_verification.get("trust_scope") == "system-production" and
        retention_verification.get("signature_status") == "verified" and
        retention_verification.get("retention_status") ==
            "current-policy-satisfied" and
        type(retention_verification.get(
            "current_policy_satisfied_object_count")) is int and
        retention_verification[
            "current_policy_satisfied_object_count"] > 0 and
        retention_verification.get("profile") ==
            "release-validation-p0-v1" and
        retention_verification.get("role_count") == len(critical) and
        retention_verification.get("production_contract_verified") is True,
        reason)
    for field in (
        "statement_sha256", "request_sha256", "index_sha256",
        "evidence_set_manifest_sha256", "trust_policy_sha256",
    ):
        _bare_digest(retention_verification.get(field), reason)
    _require(
        type(retention_verification.get("evidence_set_id")) is str and
        bool(retention_verification["evidence_set_id"]), reason)
    return Facts(
        source="sha256:" +
            lineage["strict_source_security_manifest_sha256"],
        issued_at_ms=evaluated, expires_at_ms=expires, status="PASS")


def _validate_transaction_lock(value: Any, reason: str) -> dict[str, Any]:
    _require(isinstance(value, dict) and set(value) == TRANSACTION_LOCK_FIELDS,
             reason)
    _canonical_path(Path(value.get("path", "")), reason)
    for field in (
        "device", "inode", "nlink", "uid", "gid", "size", "mtime_ns",
        "ctime_ns",
    ):
        _integer(value.get(field), reason)
    _require(
        value["device"] > 0 and value["inode"] > 0 and
        value["nlink"] == 1 and value["uid"] == ROOT_UID and
        value["gid"] == ROOT_GID and value.get("mode") == "0600" and
        value["size"] == 0 and
        type(value.get("created_during_transaction")) is bool and
        value.get("persistent") is True and
        value.get("held_during_transaction") is True,
        reason)
    return value


def _validate_install_preflight(
    value: Any, domain: str, reason: str,
) -> dict[str, Any]:
    _require(isinstance(value, dict) and set(value) == INSTALL_PREFLIGHT_FIELDS,
             reason)
    paper = value.get("paper_units")
    blocking = value.get("installation_blocking_units")
    allowed = {"inactive", "failed", "unknown"}
    _require(
        value.get("domain") == domain and
        isinstance(paper, dict) and set(paper) == set(INSTALL_PAPER_UNITS) and
        all(type(state) is str and state in allowed for state in paper.values())
        and isinstance(blocking, dict) and
        set(blocking) == set(INSTALL_BLOCKING_UNITS) and
        all(type(state) is str and state in allowed
            for state in blocking.values()) and
        value.get("campaign_policy_count") == 0 and
        value.get("kill_switch_engaged") is True and
        value.get("broker_egress_deny_all") is True,
        reason)
    return value


def _validate_profile_file_evidence(
    value: Any, reason: str, *, legacy_receipt: bool = False,
) -> dict[str, Any]:
    fields = (
        PROFILE_LEGACY_RECEIPT_EVIDENCE_FIELDS if legacy_receipt
        else PROFILE_FILE_EVIDENCE_FIELDS)
    _require(isinstance(value, dict) and set(value) == fields, reason)
    _canonical_path(Path(value.get("path", "")), reason)
    _digest(value.get("sha256"), reason)
    if legacy_receipt:
        _digest(value.get("body_sha256"), reason)
    for field in (
        "bytes", "device", "inode", "mode", "nlink", "uid", "gid",
        "mtime_ns", "ctime_ns",
    ):
        _integer(value.get(field), reason)
    _require(
        value["bytes"] > 0 and value["device"] > 0 and value["inode"] > 0 and
        stat.S_ISREG(value["mode"]) and value["nlink"] == 1 and
        value["uid"] == ROOT_UID and value["gid"] == ROOT_GID,
        reason)
    return value


def _validate_predecessor_profile_receipt(value: Any, reason: str) -> None:
    evidence = _validate_profile_file_evidence(
        value, reason, legacy_receipt=True)
    _require(
        evidence.get("path") == PREDECESSOR_PROFILE_RECEIPT_PATH and
        evidence.get("sha256") == PREDECESSOR_PROFILE_RECEIPT_FILE_SHA256 and
        evidence.get("body_sha256") ==
            PREDECESSOR_PROFILE_RECEIPT_BODY_SHA256 and
        evidence.get("bytes") == PREDECESSOR_PROFILE_RECEIPT_BYTES and
        stat.S_IMODE(evidence["mode"]) == 0o600,
        reason)


def _validate_dormant_paper_to_watch_transition_receipt(
    value: Any, reason: str,
) -> None:
    evidence = _validate_profile_file_evidence(
        value, reason, legacy_receipt=True)
    _require(
        evidence.get("path") ==
            DORMANT_PAPER_TO_WATCH_TRANSITION_RECEIPT_PATH and
        stat.S_IMODE(evidence["mode"]) == 0o600, reason)


def _validate_profile_preflight(value: Any, reason: str) -> dict[str, Any]:
    _require(isinstance(value, dict) and set(value) == PROFILE_PREFLIGHT_FIELDS,
             reason)
    for field in (
        "gateway_units", "gateway_masks", "gateway_unit_closure",
        "systemd_manager", "manager_unit_contracts", "broker_egress_unit",
        "broker_egress_check", "paper_units", "watch_boundary",
    ):
        _require(isinstance(value.get(field), dict) and bool(value[field]), reason)
    _require(
        value.get("campaign_policy_count") == 0 and
        value.get("kill_switch_engaged") is True and
        value.get("broker_egress_deny_all_observed") is True and
        all(
            isinstance(state, dict) and
            state.get("ActiveState") == "inactive" and
            state.get("SubState") == "dead" and state.get("Job") == ""
            for state in value["paper_units"].values()),
        reason)
    return value


def _validate_activation_predecessor_lineage(
    success_value: Any, failure_value: Any, reason: str,
) -> None:
    """Bind v4 to the exact Round95 receipts and their Round86 ancestor."""
    success = success_value if isinstance(success_value, dict) else {}
    failure = failure_value if isinstance(failure_value, dict) else {}
    _require(
        set(success) == PREDECESSOR_ACTIVATION_SUCCESS_FIELDS and
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
        set(failure) == PREDECESSOR_ACTIVATION_FAILURE_FIELDS and
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


def _validate_activation_runtime(document: Mapping[str, Any], reason: str) -> None:
    _validate_activation_predecessor_lineage(
        document.get("predecessor_activation_success"),
        document.get("predecessor_activation_failure"), reason)
    broker_before = document.get("broker_before")
    broker_after = document.get("broker_after")
    gateway = document.get("gateway_after")
    timer = document.get("reconcile_timer")
    boundary = document.get("watch_boundary")
    paper = document.get("paper_units")
    _require(
        isinstance(broker_before, dict) and
        set(broker_before) == {
            "policy_sha256", "authorized_connectors", "authorized_uids",
            "protected_ports"} and
        broker_before.get("authorized_connectors") == 0 and
        broker_before.get("authorized_uids") == [] and
        type(broker_before.get("protected_ports")) is int and
        broker_before["protected_ports"] > 0,
        reason)
    _digest(broker_before.get("policy_sha256"), reason)
    _require(isinstance(broker_after, dict) and
             set(broker_after) == BROKER_AFTER_FIELDS, reason)
    for field in (
        "interpreter_sha256", "credential_source_sha256",
        "installed_source_sha256", "cmdline_sha256", "deny_all_policy_sha256",
        "unit_contract_sha256",
    ):
        _digest(broker_after.get(field), reason)
    _require(
        broker_after.get("active_state") == "active" and
        broker_after.get("sub_state") == "running" and
        broker_after.get("authorized_connectors") == 0 and
        broker_after.get("authorized_uids") == [] and
        type(broker_after.get("protected_ports")) is int and
        broker_after["protected_ports"] > 0 and
        broker_after.get("deny_all_policy_sha256") ==
            broker_before["policy_sha256"], reason)
    _require(isinstance(gateway, dict) and
             set(gateway) == GATEWAY_AFTER_FIELDS, reason)
    for field in (
        "gateway_executable_sha256", "domain_config_sha256",
        "gateway_profile_sha256", "gateway_process_profile_sha256",
        "unit_contract_sha256",
    ):
        _digest(gateway.get(field), reason)
    _require(
        gateway.get("active_state") == "active" and
        gateway.get("sub_state") == "running" and
        gateway.get("execution_remote_mode") == "SIMULATOR" and
        gateway.get("tool_account") == "SIM" and
        gateway.get("tool_allow_trade") == "0" and
        gateway.get("session_templates") == "watch" and
        type(gateway.get("gateway_socket_inode")) is int and
        gateway["gateway_socket_inode"] > 0 and
        type(gateway.get("supervisor_socket_inode")) is int and
        gateway["supervisor_socket_inode"] > 0,
        reason)
    _require(isinstance(timer, dict) and set(timer) == RECONCILE_TIMER_FIELDS,
             reason)
    _digest(timer.get("unit_contract_sha256"), reason)
    _require(
        timer.get("load_state") == "loaded" and
        timer.get("active_state") == "active" and
        timer.get("sub_state") == "waiting" and timer.get("job") == "" and
        timer.get("unit_file_state") == "enabled", reason)
    _require(
        isinstance(boundary, dict) and set(boundary) == WATCH_BOUNDARY_FIELDS and
        boundary == {
            "export_absent": True, "sessions_authority_count": 0,
            "private_authority_count": 0,
            "custodian_transaction_absent": True,
            "session_bootstrap_idle_lock_observed": True,
        }, reason)
    _require(
        isinstance(paper, dict) and bool(paper) and all(
            isinstance(state, dict) and set(state) == {
                "ActiveState", "SubState", "Job"} and
            state == {"ActiveState": "inactive", "SubState": "dead", "Job": ""}
            for state in paper.values()), reason)
    _require(
        isinstance(document.get("stale_bundles"), list) and
        isinstance(document.get("systemctl_mutations"), list) and
        bool(document["systemctl_mutations"]) and all(
            isinstance(argv, list) and bool(argv) and
            all(type(item) is str and item for item in argv)
            for argv in document["systemctl_mutations"]), reason)


def _validate_source_files(value: Any, reason: str) -> tuple[list[str], str]:
    _require(isinstance(value, dict) and set(value) == SOURCE_MANIFEST_FIELDS,
             reason)
    count = _integer(value.get("file_count"), reason, minimum=1)
    records = value.get("files")
    _require(isinstance(records, list) and len(records) == count, reason)
    paths: list[str] = []
    for record in records:
        _require(isinstance(record, dict) and set(record) == SOURCE_FILE_FIELDS,
                 reason)
        path = record.get("path")
        _require(
            type(path) is str and path and not path.startswith("/") and
            "\\" not in path and all(part not in {"", ".", ".."}
                                      for part in Path(path).parts), reason)
        _require(record.get("mode") in {"0644", "0755"}, reason)
        _integer(record.get("size"), reason)
        _digest(record.get("sha256"), reason)
        paths.append(path)
    _require(paths == sorted(paths) and len(paths) == len(set(paths)), reason)
    expected = digest_bytes(json.dumps(
        records, ensure_ascii=True, allow_nan=False, sort_keys=True,
        separators=(",", ":")).encode("utf-8"))
    _require(value.get("sha256") == expected, reason)
    return paths, expected


def validate_source_baseline(document: dict[str, Any]) -> Facts:
    reason = "SOURCE_BASELINE_INVALID"
    _require(set(document) == SOURCE_BASELINE_FIELDS, reason)
    _require(document.get("schema") == "hepta.versioned-source-baseline.v1",
             reason)
    _safe_token(document.get("version"), VERSION_TOKEN, reason)
    _safe_token(document.get("git_head"), GIT_HEAD, reason)
    _, source = _validate_source_files(document.get("source_manifest"), reason)
    generated = document.get("generated_at")
    _require(type(generated) is str and generated.isascii(), reason)
    try:
        parsed = datetime.fromisoformat(generated.replace("Z", "+00:00"))
    except ValueError as error:
        raise AdmissionError(reason) from error
    _require(parsed.tzinfo is not None and parsed.utcoffset() is not None,
             reason)
    issued = int(parsed.timestamp() * 1000)
    readiness: list[str] = []
    if not (
        document.get("source_baseline_frozen") is True and
        document.get("clean_checkout_certified") is True and
        document.get("worktree_status_entry_count") == 0 and
        document.get("blocked_reason") is None
    ):
        readiness.append("SOURCE_BASELINE_NOT_CLEAN_FROZEN")
    _require(document.get("release_authorized") is False, reason)
    _require(document.get("excluded_unsafe_tree") ==
             "compat/unsafe-direct-broker", reason)
    dangers = _boundary_findings(
        document, "SOURCE_BASELINE", ("paper_authorized", "live_authorized"))
    return Facts(source=source, issued_at_ms=issued, status="FROZEN",
                 readiness=tuple(readiness), dangers=dangers)


def validate_install_manifest(document: dict[str, Any]) -> Facts:
    reason = "INSTALL_MANIFEST_INVALID"
    _require(set(document) == INSTALL_MANIFEST_FIELDS, reason)
    _require(
        document.get("schema") == "hepta.shadow-runtime-install-manifest.v2"
        and document.get("version") == 2, reason)
    for field in ("archive_sha256", "source_baseline_sha256",
                  "installer_sha256"):
        _digest(document.get(field), reason)
    records = document.get("files")
    _require(isinstance(records, list) and bool(records), reason)
    paths: list[str] = []
    for record in records:
        _require(isinstance(record, dict) and set(record) == SOURCE_FILE_FIELDS,
                 reason)
        path = record.get("path")
        _require(type(path) is str and path and not path.startswith("/") and
                 "\\" not in path and all(part not in {"", ".", ".."}
                                           for part in Path(path).parts), reason)
        _require(record.get("mode") in {"0600", "0644", "0755"}, reason)
        _integer(record.get("size"), reason)
        _digest(record.get("sha256"), reason)
        paths.append(path)
    _require(paths == sorted(paths) and len(paths) == len(set(paths)), reason)
    dangers = _boundary_findings(
        document, "INSTALL_MANIFEST",
        ("paper_authorized", "live_authorized", "mutation_attempted",
         "direct_broker_access"))
    return Facts(source=document["source_baseline_sha256"],
                 status="PASSIVE", dangers=dangers)


def validate_install_receipt(document: dict[str, Any]) -> Facts:
    reason = "INSTALL_RECEIPT_INVALID"
    _sealed(document, INSTALL_RECEIPT_FIELDS, reason)
    _require(
        document.get("schema") == "hepta.shadow-runtime-install-receipt.v4"
        and document.get("version") == 4, reason)
    source = _digest(document.get("source_baseline_sha256"), reason)
    domain = _safe_token(document.get("domain"), DOMAIN, reason)
    issued = _integer(document.get("finished_at_ms"), reason)
    _require(
        document.get("install_generation") == INSTALL_GENERATION and
        document.get("predecessor_install_generation") ==
            PREDECESSOR_INSTALL_GENERATION and
        document.get("predecessor_current_install_pointer_file_sha256") ==
            PREDECESSOR_INSTALL_POINTER_SHA256 and
        document.get("installed_file_count") == INSTALLED_FILE_COUNT,
        reason)
    for field in ("archive_sha256", "installer_sha256",
                  "installed_paths_sha256"):
        _digest(document.get(field), reason)
    installed = _integer(document.get("installed_file_count"), reason, 1)
    replaced = _integer(document.get("replaced_file_count"), reason)
    new = _integer(document.get("new_file_count"), reason)
    _require(replaced + new == installed, reason)
    deny = document.get("default_deny_identity_manifest")
    _require(isinstance(deny, dict) and set(deny) == {
        "destination", "archive_path", "uid", "gid", "mode", "size",
        "sha256", "installed", "preexisting_backed_up", "new_file"}, reason)
    _canonical_path(Path(deny.get("destination", "")), reason)
    _require(type(deny.get("archive_path")) is str and
             bool(deny["archive_path"]) and not deny["archive_path"].startswith("/")
             and deny.get("uid") == ROOT_UID and deny.get("gid") == ROOT_GID and
             deny.get("mode") == "0600" and
             type(deny.get("size")) is int and deny["size"] > 0 and
             deny.get("installed") is True and
             type(deny.get("preexisting_backed_up")) is bool and
             type(deny.get("new_file")) is bool and
             deny["preexisting_backed_up"] != deny["new_file"], reason)
    _digest(deny.get("sha256"), reason)
    _require(document.get("reader_gid") == 1000, reason)
    _validate_transaction_lock(document.get("transaction_lock"), reason)
    before = _validate_install_preflight(
        document.get("preflight_before"), domain, reason)
    after = _validate_install_preflight(
        document.get("preflight_after"), domain, reason)
    _require(before == after, reason)
    readiness = () if document.get("status") == "PASSIVE_INSTALL_COMPLETE" \
        else ("INSTALL_RECEIPT_NOT_PASS",)
    dangers = _boundary_findings(
        document, "INSTALL_RECEIPT",
        ("paper_authorized", "live_authorized", "mutation_attempted",
         "direct_broker_access", "services_started", "services_enabled",
         "preflight_continuity_claimed"))
    return Facts(source=source, domain=domain, issued_at_ms=issued,
                 status=str(document.get("status")), readiness=readiness,
                 dangers=dangers)


def validate_install_pointer(document: dict[str, Any]) -> Facts:
    reason = "INSTALL_POINTER_INVALID"
    _sealed(document, INSTALL_POINTER_FIELDS, reason)
    _require(
        document.get("schema") == "hepta.shadow-runtime-current-install.v1"
        and document.get("version") == 1 and
        document.get("generation") == INSTALL_GENERATION and
        document.get("installed_file_count") == INSTALLED_FILE_COUNT and
        document.get("manifest_path") == INSTALL_MANIFEST_PATH and
        document.get("receipt_path") == INSTALL_RECEIPT_PATH and
        document.get("backup_root") == INSTALL_BACKUP_ROOT, reason)
    source = _digest(document.get("source_baseline_sha256"), reason)
    domain = _safe_token(document.get("domain"), DOMAIN, reason)
    for field in ("manifest_file_sha256", "receipt_file_sha256",
                  "archive_sha256", "installer_sha256",
                  "installed_paths_sha256"):
        _digest(document.get(field), reason)
    for field in ("manifest_path", "receipt_path", "backup_root",
                  "transaction_lock_path"):
        _canonical_path(Path(document.get(field, "")), reason)
    _integer(document.get("installed_file_count"), reason, 1)
    dangers = _boundary_findings(
        document, "INSTALL_POINTER",
        ("paper_authorized", "live_authorized", "mutation_attempted",
         "direct_broker_access"))
    return Facts(source=source, domain=domain, status="CURRENT", dangers=dangers)


def _validate_shadow_evidence(value: Any, reason: str) -> dict[str, Any]:
    _require(isinstance(value, dict) and
             set(value) == SHADOW_INSTALL_EVIDENCE_FIELDS, reason)
    _require(
        value.get("schema") ==
            "hepta.shadow-runtime-install-consumption-evidence.v3" and
        value.get("version") == 3 and
        value.get("install_generation") == INSTALL_GENERATION and
        value.get("predecessor_install_generation") ==
            PREDECESSOR_INSTALL_GENERATION and
        value.get("installed_file_count") == INSTALLED_FILE_COUNT and
        value.get("receipt_path") == INSTALL_RECEIPT_PATH and
        value.get("manifest_path") == INSTALL_MANIFEST_PATH and
        value.get("backup_root") == INSTALL_BACKUP_ROOT and
        value.get("predecessor_current_install_pointer_file_sha256") ==
            PREDECESSOR_INSTALL_POINTER_SHA256 and
        value.get("verified_under_lock") is True and
        value.get("lock_mode") == "exclusive", reason)
    for field in (
        "receipt_file_sha256", "receipt_body_sha256", "manifest_file_sha256",
        "archive_sha256", "source_baseline_sha256", "installer_sha256",
        "installed_paths_sha256", "closure_sha256",
        "default_deny_identity_sha256", "current_install_pointer_file_sha256",
        "predecessor_current_install_pointer_file_sha256",
    ):
        _digest(value.get(field), reason)
    for field in ("receipt_path", "manifest_path",
                  "current_install_pointer_path", "backup_root"):
        _canonical_path(Path(value.get(field, "")), reason)
    _safe_token(value.get("domain"), DOMAIN, reason)
    _integer(value.get("installed_file_count"), reason, 1)
    _validate_transaction_lock(value.get("transaction_lock"), reason)
    dangers = _boundary_findings(
        value, "SHADOW_INSTALL_EVIDENCE",
        ("paper_authorized", "live_authorized", "mutation_attempted",
         "direct_broker_access"))
    _require(not dangers, dangers[0] if dangers else reason)
    return value


def validate_profile_receipt(document: dict[str, Any]) -> Facts:
    reason = "PROFILE_RECEIPT_INVALID"
    _sealed(document, PROFILE_RECEIPT_FIELDS, reason)
    _require(
        document.get("schema") ==
            "hepta.p1-watch-profile-deployment-receipt.v8" and
        document.get("version") == 8 and document.get("round") == ROUND,
        reason)
    evidence = _validate_shadow_evidence(
        document.get("shadow_install_evidence"), reason)
    started = _integer(document.get("started_at_ms"), reason)
    finished = _integer(document.get("finished_at_ms"), reason)
    _require(started <= finished, reason)
    fixed_false = (
        "profile_content_changed", "target_written", "target_replaced",
        "services_started", "services_stopped", "services_restarted",
        "campaign_launched", "activation_receipt_eligible",
        "preflight_reusable_for_activation", "broker_loaded_source_attested",
        "broker_deny_all_continuity_attested",
    )
    _require(all(document.get(field) is False for field in fixed_false), reason)
    _require(document.get("fresh_activation_transaction_required") is True,
             reason)
    target_before = _validate_profile_file_evidence(
        document.get("target_before"), reason)
    target_after = _validate_profile_file_evidence(
        document.get("target_after"), reason)
    target_final = _validate_profile_file_evidence(
        document.get("target_final"), reason)
    _require(target_before == target_after == target_final, reason)
    _validate_profile_file_evidence(
        document.get("legacy_receipt"), reason, legacy_receipt=True)
    _validate_profile_file_evidence(document.get("legacy_backup"), reason)
    _validate_profile_file_evidence(
        document.get("legacy_retained_target"), reason)
    _validate_predecessor_profile_receipt(
        document.get("predecessor_profile_receipt"), reason)
    _validate_dormant_paper_to_watch_transition_receipt(
        document.get("dormant_paper_to_watch_transition_receipt"), reason)
    before = _validate_profile_preflight(document.get("preflight_before"), reason)
    after = _validate_profile_preflight(document.get("preflight_after"), reason)
    final = _validate_profile_preflight(document.get("preflight_final"), reason)
    _require(before == after == final, reason)
    readiness = () if document.get("status") == \
        "OFFLINE_PASSIVE_WATCH_PROFILE_REATTESTED" else \
        ("PROFILE_RECEIPT_NOT_PASS",)
    dangers = _boundary_findings(
        document, "PROFILE_RECEIPT",
        ("paper_authorized", "live_authorized", "mutation_attempted",
         "direct_broker_access"))
    return Facts(source=evidence["source_baseline_sha256"],
                 domain=document.get("domain"), issued_at_ms=finished,
                 status=str(document.get("status")), readiness=readiness,
                 dangers=dangers)


def validate_activation_receipt(document: dict[str, Any]) -> Facts:
    reason = "ACTIVATION_RECEIPT_INVALID"
    _sealed(document, ACTIVATION_RECEIPT_FIELDS, reason)
    _require(
        document.get("schema") == "hepta.p1-watch-activation-receipt.v4" and
        document.get("version") == 4 and document.get("round") == ROUND,
        reason)
    evidence = _validate_shadow_evidence(
        document.get("shadow_install_evidence"), reason)
    started = _integer(document.get("started_at_ms"), reason)
    completed = _integer(document.get("completed_at_ms"), reason)
    _require(started <= completed, reason)
    _require(
        document.get("fresh_activation_transaction") is True and
        document.get("gateway_activated") is True and
        document.get("gateway_profile_loaded") is True and
        document.get("gateway_contract_binding_loaded") is True and
        document.get("broker_loaded_source_attested") is True and
        document.get("broker_deny_all_continuity_attested") is True and
        document.get("watch_authority_provisioned") is False and
        document.get("campaign_launched") is False and
        document.get("admission_prerequisite_satisfied") is True and
        document.get("paper_prerequisite_satisfied") is False and
        document.get("kill_switch_engaged") is True,
        reason)
    _validate_activation_runtime(document, reason)
    readiness = () if document.get("status") == "WATCH_GATEWAY_ACTIVATED" \
        else ("ACTIVATION_RECEIPT_NOT_PASS",)
    dangers = _boundary_findings(
        document, "ACTIVATION_RECEIPT",
        ("paper_authorized", "live_authorized", "mutation_attempted",
         "direct_broker_access"))
    return Facts(source=evidence["source_baseline_sha256"],
                 domain=document.get("domain"), issued_at_ms=completed,
                 status=str(document.get("status")), readiness=readiness,
                 dangers=dangers)


def validate_p1_audit(document: dict[str, Any]) -> Facts:
    reason = "P1_AUDIT_RECEIPT_INVALID"
    _sealed(document, P1_AUDIT_FIELDS, reason)
    _require(
        document.get("schema") ==
            "hepta.p1-safety-soak-audit-receipt.v1" and
        document.get("version") == 1 and document.get("phase") == "P1_SHADOW"
        and document.get("verdict") in {"GO", "NO_GO", "HALT"},
        reason)
    source = _digest(document.get("source_manifest_sha256"), reason)
    domain = _safe_token(document.get("domain_id"), DOMAIN, reason)
    campaign = _safe_token(document.get("campaign_id"), CAMPAIGN, reason)
    issued = _integer(document.get("audited_at_ms"), reason)
    _safe_token(document.get("independent_auditor_id"), CAMPAIGN, reason)
    _reference(document.get("freeze_bundle"), reason)
    campaign_runtime = document.get("campaign_runtime")
    _require(
        isinstance(campaign_runtime, dict) and
        set(campaign_runtime) == P1_CAMPAIGN_RUNTIME_REFERENCE_FIELDS and
        campaign_runtime.get("schema") == P1_CAMPAIGN_RUNTIME_SCHEMA,
        reason)
    _reference({
        field: campaign_runtime[field] for field in REFERENCE_FIELDS
    }, reason)
    producer = document.get("producer")
    _require(
        isinstance(producer, dict) and
        set(producer) == WATCH_HANDOFF_PRODUCER_FIELDS and
        _canonical_path(Path(producer.get("path", "")), reason) ==
            Path("/usr/libexec/hepta-p1-safety-soak-auditor") and
        _digest(producer.get("file_sha256"), reason) !=
            "sha256:" + "0" * 64 and
        document.get("production_mode") == "PRODUCTION_ROOT_AUDIT",
        reason)
    for field in (
        "campaign_spec_file_sha256", "campaign_spec_body_sha256",
        "policy_sha256", "strategy_sha256",
    ):
        _digest(document.get(field), reason)
    interval = document.get("evaluated_interval")
    _require(isinstance(interval, dict) and
             set(interval) == P1_INTERVAL_FIELDS, reason)
    for field in (
        "start_boottime_ns", "end_boottime_ns", "duration_ns",
        "maximum_checkpoint_gap_ns", "continuity_origin_ms",
        "continuity_end_ms", "continuity_final_slot",
    ):
        _integer(interval.get(field), reason)
    _require(
        interval.get("clock_id") == "CLOCK_BOOTTIME" and
        type(interval.get("boot_id")) is str and bool(interval["boot_id"]) and
        interval["start_boottime_ns"] < interval["end_boottime_ns"] and
        interval["duration_ns"] ==
            interval["end_boottime_ns"] - interval["start_boottime_ns"] and
        interval["duration_ns"] >= MINIMUM_BOOTTIME_DURATION_NS and
        0 <= interval["maximum_checkpoint_gap_ns"] <=
            15 * 60 * 1_000_000_000 and
        interval["continuity_origin_ms"] < interval["continuity_end_ms"] and
        interval["continuity_final_slot"] >= 1 and
        interval.get("consecutive") is True,
        reason)
    counts = document.get("counts")
    _require(isinstance(counts, dict) and set(counts) == P1_COUNTS_FIELDS,
             reason)
    for field in P1_COUNTS_FIELDS:
        _integer(counts.get(field), reason)
    completeness = document.get("completeness")
    _require(isinstance(completeness, dict) and
             set(completeness) == P1_COMPLETENESS_FIELDS, reason)
    for field in ("numerator", "denominator", "ppm"):
        _integer(completeness.get(field), reason)
    _require(completeness["ppm"] <= 1_000_000, reason)
    artifacts = document.get("checked_artifacts")
    _require(isinstance(artifacts, list) and bool(artifacts), reason)
    artifact_order: list[tuple[str, str]] = []
    for artifact in artifacts:
        _require(isinstance(artifact, dict) and
                 set(artifact) == P1_CHECKED_ARTIFACT_FIELDS, reason)
        role = _safe_token(artifact.get("role"), CAMPAIGN, reason)
        path = _canonical_path(Path(artifact.get("path", "")), reason)
        _digest(artifact.get("file_sha256"), reason)
        _digest(artifact.get("body_sha256"), reason)
        artifact_order.append((role, str(path)))
    _require(artifact_order == sorted(set(artifact_order)) and
             any(role == "launcher_receipt" for role, _ in artifact_order),
             reason)
    exposure = document.get("exposure_summary")
    _require(isinstance(exposure, dict) and set(exposure) == P1_EXPOSURE_FIELDS,
             reason)
    for field in (
        "maximum_connector_count", "maximum_authorized_uid_count",
        "maximum_paper_unit_active_count",
    ):
        _integer(exposure.get(field), reason)
    _require(
        all(type(exposure.get(field)) is bool for field in (
            "evidence_present", "campaign_socket_ever_present",
            "kill_switch_continuously_engaged", "local_boundary_uncertain",
            "authoritative_account_state_observed")) and
        exposure.get("scope") == "LOCAL_HOST_BOUNDARY_ONLY",
        reason)
    cleanup = document.get("cleanup_status")
    _require(isinstance(cleanup, dict) and set(cleanup) == P1_CLEANUP_FIELDS,
             reason)
    _integer(cleanup.get("required_subject_count"), reason)
    _integer(cleanup.get("verified_subject_count"), reason)
    _require(isinstance(document.get("failed_invariants"), list) and
             all(type(item) is str and item
                 for item in document["failed_invariants"]), reason)
    ready = (
        document.get("verdict") == "GO" and
        MINIMUM_TRADING_DAYS <= counts["declared_trading_days"] <=
            MAXIMUM_TRADING_DAYS and
        counts["observed_trading_days"] ==
            counts["declared_trading_days"] and
        counts["launcher_receipts"] > 0 and
        counts["verified_closures"] == counts["launcher_receipts"] and
        counts["continuity_checkpoints"] ==
            interval["continuity_final_slot"] + 1 and
        counts["decision_receipts"] == counts["scheduled_decisions"] and
        counts["eligible_decisions"] >= MINIMUM_ELIGIBLE_DECISIONS and
        counts["eligible_decisions"] <= counts["scheduled_decisions"] and
        counts["complete_eligible_decisions"] +
            counts["incomplete_eligible_decisions"] ==
            counts["eligible_decisions"] and
        counts["catch_up_decisions"] == 0 and
        counts["planned_faults"] > 0 and
        counts["fault_results"] == counts["planned_faults"] and
        counts["authority_snapshots"] > 0 and
        counts["cleanup_snapshots"] > 0 and
        completeness["numerator"] == counts["complete_eligible_decisions"] and
        completeness["denominator"] == counts["eligible_decisions"] and
        completeness["ppm"] ==
            counts["complete_eligible_decisions"] * 1_000_000 //
            counts["eligible_decisions"] and
        completeness["ppm"] >= MINIMUM_COMPLETE_PPM and
        completeness["strictly_greater_than_99_percent"] is True and
        document["failed_invariants"] == [] and
        exposure.get("evidence_present") is True and
        all(exposure[field] == 0 for field in (
            "maximum_connector_count", "maximum_authorized_uid_count",
            "maximum_paper_unit_active_count")) and
        exposure.get("campaign_socket_ever_present") is False and
        exposure.get("kill_switch_continuously_engaged") is True and
        exposure.get("local_boundary_uncertain") is False and
        exposure.get("authoritative_account_state_observed") is False and
        cleanup["required_subject_count"] > 0 and
        cleanup["verified_subject_count"] == cleanup["required_subject_count"]
        and cleanup.get("complete") is True and
        document.get("p1_safety_soak_gate_satisfied") is True and
        document.get("paper_test_admission_candidate") is False and
        document.get("safest_allowed_next_action") ==
            "CONTINUE_REMAINING_PAPER_ADMISSION_GATES"
    )
    readiness = () if ready else ("P1_AUDIT_NOT_GO",)
    dangers = list(_boundary_findings(document, "P1_AUDIT_RECEIPT"))
    if any(exposure[field] > 0 for field in (
            "maximum_connector_count", "maximum_authorized_uid_count",
            "maximum_paper_unit_active_count")) or \
            exposure.get("campaign_socket_ever_present") is True or \
            exposure.get("kill_switch_continuously_engaged") is False or \
            exposure.get("local_boundary_uncertain") is True or \
            exposure.get("authoritative_account_state_observed") is True:
        dangers.append("P1_AUDIT_EXPOSURE_SIGNAL")
    if document.get("paper_test_admission_candidate") is True:
        dangers.append("P1_AUDIT_CANDIDATE_AUTHORITY_LEAK")
    if document.get("verdict") == "HALT":
        dangers.append("P1_AUDIT_UPSTREAM_HALT")
    return Facts(
        source, domain, campaign, issued, None,
        str(document.get("verdict")), readiness, tuple(dangers),
        document["strategy_sha256"])


def _bare_digest(value: Any, reason: str) -> str:
    _require(type(value) is str and re.fullmatch(r"[0-9a-f]{64}", value)
             is not None, reason)
    return value


def _gate_input_records(value: Any, reason: str) -> dict[str, dict[str, Any]]:
    _require(isinstance(value, dict) and bool(value), reason)
    for path, record in value.items():
        _require(
            type(path) is str and path and not path.startswith("/") and
            all(part not in {"", ".", ".."} for part in Path(path).parts) and
            isinstance(record, dict) and set(record) == {"sha256", "size", "mode"},
            reason)
        _bare_digest(record.get("sha256"), reason)
        _integer(record.get("size"), reason, 1)
        _require(record.get("mode") in {"0600", "0644", "0755"}, reason)
    return value


def _checks(
    value: Any, reason: str, expected: frozenset[str] | None = None,
) -> dict[str, bool]:
    _require(isinstance(value, dict) and bool(value) and
             all(type(key) is str and key and result is True
                 for key, result in value.items()), reason)
    if expected is not None:
        _require(set(value) == set(expected), reason)
    return value


def _gate_times(
    document: Mapping[str, Any], reason: str, *,
    maximum_window_ms: int = MAXIMUM_GATE_PROVENANCE_LIFETIME_MS,
) -> tuple[int, int, int]:
    started = _integer(document.get("started_at_ms"), reason)
    completed = _integer(document.get("completed_at_ms"), reason)
    expires = _integer(document.get("expires_at_ms"), reason)
    duration = _integer(document.get("duration_ms"), reason)
    _require(started <= completed < expires and
             duration == completed - started and
             expires - started <= maximum_window_ms, reason)
    return started, completed, expires


def _validate_disposable_cleanup(value: Any, reason: str) -> None:
    _require(value == {
        "container_absent": True,
        "image_tag_absent": True,
        "image_id_absent": True,
    }, reason)


def _validate_rootful_container(
    value: Any, reason: str, *, paper: bool,
) -> None:
    _require(isinstance(value, dict) and set(value) == {
        "image_id", "network_mode", "read_only_rootfs",
        "private_cgroup_namespace", "privileged", "bind_mounts",
        "published_ports", "devices", "device_requests", "links",
        "tmpfs_allowlist", "capabilities", "apparmor_profile",
    }, reason)
    _digest(value.get("image_id"), reason)
    _require(
        value.get("network_mode") == "none" and
        value.get("read_only_rootfs") is True and
        value.get("private_cgroup_namespace") is True and
        value.get("privileged") is False and
        all(value.get(field) == 0 for field in (
            "bind_mounts", "published_ports", "devices",
            "device_requests", "links")) and
        value.get("tmpfs_allowlist") == (
            PAPER_RUNTIME_TMPFS if paper else DUAL_RUNTIME_TMPFS) and
        value.get("capabilities") == list(
            PAPER_RUNTIME_CAPABILITIES if paper else
            DUAL_RUNTIME_CAPABILITIES) and
        value.get("apparmor_profile") == "hepta-systemd-gate", reason)


def _validate_rootful_platform(value: Any, reason: str, *, paper: bool) -> None:
    common_fields = {
        "host_kernel", "host_architecture", "docker_client",
        "docker_server_version", "docker_server_api_version",
        "docker_server_os", "docker_server_architecture",
        "docker_cgroup_driver", "docker_cgroup_version",
        "docker_default_runtime", "docker_security_options",
        "base_image_reference", "base_image_id", "base_image_os",
        "base_image_architecture", "systemd", "container_boot_id",
        "container_pid1_cgroup",
    }
    paper_fields = {
        "nft", "container_kernel", "container_architecture",
        "container_cgroup", "immutable_file_count",
        "immutable_file_inventory_sha256", "package_count",
        "package_inventory_sha256",
    }
    expected = common_fields | (paper_fields if paper else set())
    _require(isinstance(value, dict) and set(value) == expected, reason)
    _require(
        type(value.get("base_image_reference")) is str and
        PINNED_IMAGE.fullmatch(value["base_image_reference"]) is not None and
        type(value.get("base_image_id")) is str and
        DIGEST.fullmatch(value["base_image_id"]) is not None and
        type(value.get("container_boot_id")) is str and
        BOOT_ID.fullmatch(value["container_boot_id"]) is not None and
        value.get("container_pid1_cgroup") == "0::/" and
        value.get("docker_cgroup_version") == "2" and
        isinstance(value.get("docker_security_options"), list) and
        all(type(item) is str for item in value["docker_security_options"]) and
        any("apparmor" in item.lower()
            for item in value["docker_security_options"]),
        reason)
    if paper:
        _require(
            value.get("base_image_os") == "linux" and
            value.get("container_cgroup") == "v2-private" and
            all(type(value.get(field)) is str and bool(value[field])
                for field in expected - {"docker_security_options"}) and
            all(type(value.get(field)) is str and value[field].isdecimal() and
                int(value[field]) > 0
                for field in ("immutable_file_count", "package_count")) and
            all(BARE_SHA256.fullmatch(str(value.get(field))) is not None
                for field in ("immutable_file_inventory_sha256",
                              "package_inventory_sha256")), reason)


def _validate_gate_lineage(
    document: Mapping[str, Any], reason: str, *, paper: bool, go: bool,
) -> dict[str, dict[str, Any]]:
    lineage = document.get("lineage")
    expected = {
        "source_commit", "expected_source_commit", "source_tree_clean",
        "all_inputs_versioned", "inputs_stable", "final_lineage",
        "input_manifest_sha256", "runner_sha256",
    }
    if paper:
        expected |= {
            "expected_input_manifest_sha256", "expected_runner_sha256"}
    _require(isinstance(lineage, dict) and set(lineage) == expected, reason)
    _safe_token(lineage.get("source_commit"), GIT_HEAD, reason)
    _require(
        lineage.get("expected_source_commit") == lineage["source_commit"] and
        all(type(lineage.get(field)) is bool for field in (
            "source_tree_clean", "all_inputs_versioned", "inputs_stable",
            "final_lineage")) and
        lineage.get("inputs_stable") is True and
        lineage.get("final_lineage") == lineage.get("source_tree_clean"),
        reason)
    if go:
        _require(all(lineage.get(field) is True for field in (
            "source_tree_clean", "all_inputs_versioned", "final_lineage")),
            reason)
    inputs = _gate_input_records(document.get("inputs"), reason)
    expected_modes = PAPER_GATE_SOURCE_MODES if paper else DUAL_GATE_SOURCE_MODES
    _require(set(inputs) == set(expected_modes), reason)
    runner_path = (
        "scripts/run_hepta_paper_domain_rootful_systemd_gate.py" if paper else
        "scripts/run_hepta_p1_dual_domain_rootful_gate.py")
    _require(runner_path in inputs, reason)
    manifest_bare = hashlib.sha256(canonical_bytes(inputs)).hexdigest()
    runner_bare = inputs[runner_path]["sha256"]
    if paper:
        manifest = "sha256:" + manifest_bare
        runner = "sha256:" + runner_bare
        _require(
            lineage.get("input_manifest_sha256") == manifest and
            lineage.get("runner_sha256") == runner and
            (go and
             lineage.get("expected_input_manifest_sha256") == manifest and
             lineage.get("expected_runner_sha256") == runner or
             not go and
             lineage.get("expected_input_manifest_sha256") is None and
             lineage.get("expected_runner_sha256") is None), reason)
    else:
        _require(
            lineage.get("input_manifest_sha256") == manifest_bare and
            lineage.get("runner_sha256") == runner_bare, reason)
    return inputs


def _validate_reviewed_provenance(
    value: Any, reason: str, *, paper: bool,
    started_at_ms: int, completed_at_ms: int,
) -> tuple[dict[str, dict[str, Any]], list[int]]:
    _require(isinstance(value, dict) and
             set(value) == set(REVIEWED_PROVENANCE_SCHEMAS), reason)
    bodies: dict[str, dict[str, Any]] = {}
    expiries: list[int] = []
    document_pins: set[str] = set()
    identity_pins: set[str] = set()
    paths: set[str] = set()
    inodes: set[tuple[int, int]] = set()
    metadata_fields = (
        PAPER_PROVENANCE_METADATA_FIELDS if paper else
        DUAL_PROVENANCE_METADATA_FIELDS)
    for kind, schema in REVIEWED_PROVENANCE_SCHEMAS.items():
        record = value.get(kind)
        body_fields = REVIEWED_PROVENANCE_FIELDS[kind]
        _require(isinstance(record, dict) and
                 set(record) == body_fields | metadata_fields, reason)
        body = {field: record[field] for field in body_fields}
        _require(
            record.get("schema") == schema and
            record.get("decision") == "GO" and
            record.get("root_owned") is True and
            record.get("canonical_json") is True and
            record.get("mode") in ({"0400", "0600"} if paper else {"0400"}) and
            record.get("document_sha256") ==
                digest_bytes(canonical_bytes(body)), reason)
        _digest(record.get("identity_sha256"), reason)
        issued = _integer(record.get("issued_at_ms"), reason)
        expires = _integer(record.get("expires_at_ms"), reason)
        _require(
            issued <= started_at_ms < expires and completed_at_ms < expires and
            issued < expires and
            expires - issued <= MAXIMUM_GATE_PROVENANCE_LIFETIME_MS, reason)
        if kind in {"base", "builder"}:
            _digest(record.get("image_id"), reason)
            _require(type(record.get("repo_digest")) is str and
                     PINNED_IMAGE.fullmatch(record["repo_digest"]) is not None,
                     reason)
        if kind == "base":
            _digest(record.get("labels_sha256"), reason)
            expected_labels_sha = digest_bytes(json.dumps({
                "io.hepta.rootful-systemd-base.offline-ready": "true",
                "io.hepta.rootful-systemd-base.version": "1",
            }, ensure_ascii=True, allow_nan=False, sort_keys=True,
                separators=(",", ":")).encode("ascii"))
            _require(record.get("labels_sha256") == expected_labels_sha,
                     reason)
        elif kind == "builder":
            for field in ("config_sha256", "buildx_binary_sha256"):
                _digest(record.get(field), reason)
            _require(all(type(record.get(field)) is str and bool(record[field])
                         for field in (
                             "buildkit_version", "buildx_version",
                             "docker_server_version",
                             "docker_server_api_version",
                             "docker_server_git_commit")) and
                     re.fullmatch(
                         r"v[0-9]+\.[0-9]+\.[0-9]+(?:[-+][0-9A-Za-z._-]+)?",
                         record["buildkit_version"]) is not None and
                     re.fullmatch(
                         r"[0-9]+\.[0-9]+\.[0-9]+(?:[-+][0-9A-Za-z._-]+)?",
                         record["buildx_version"]) is not None and
                     re.fullmatch(
                         r"[0-9]+\.[0-9]+\.[0-9]+(?:[-+][0-9A-Za-z._-]+)?",
                         record["docker_server_version"]) is not None and
                     re.fullmatch(r"[1-9][0-9]*\.[0-9]+",
                                  record["docker_server_api_version"])
                         is not None, reason)
        elif kind == "apparmor":
            _require(record.get("profile") == "hepta-systemd-gate", reason)
            for field in (
                    "policy_source_sha256", "profile_sha256", "raw_sha256"):
                _digest(record.get(field), reason)
            _require(type(record.get("raw_abi")) is str and
                     re.fullmatch(r"v[1-9][0-9]{0,2}", record["raw_abi"])
                     is not None, reason)
        else:
            _require(
                type(record.get("docker_daemon_id")) is str and
                bool(record["docker_daemon_id"]) and
                _integer(record.get("docker_daemon_pid"), reason, 2) >= 2 and
                _integer(record.get("docker_daemon_start_time_ticks"),
                         reason, 1) >= 1 and
                type(record.get("host_boot_id")) is str and
                BOOT_ID.fullmatch(record["host_boot_id"]) is not None and
                record.get("host_namespace_name") == "root" and
                record.get("host_namespace_level") == 0 and
                record.get("host_namespace_stacked") is False and
                record.get("daemon_namespace_name") == "root" and
                record.get("daemon_namespace_level") == 0 and
                record.get("daemon_namespace_stacked") is False, reason)
        if paper:
            path = record.get("path")
            _require(type(path) is str and path.startswith("/") and
                     not path.startswith("//") and
                     os.path.normpath(path) == path, reason)
            _require(
                all(type(record.get(field)) is int for field in (
                    "device", "inode", "nlink", "uid", "gid")) and
                record["device"] >= 0 and record["inode"] > 0 and
                record["nlink"] == 1 and record["uid"] == 0 and
                record["gid"] == 0, reason)
            paths.add(path)
            inodes.add((record["device"], record["inode"]))
        document_pins.add(record["document_sha256"])
        identity_pins.add(record["identity_sha256"])
        expiries.append(expires)
        bodies[kind] = body
    _require(len(document_pins) == 4 and len(identity_pins) == 4, reason)
    if paper:
        _require(len(paths) == 4 and len(inodes) == 4, reason)
    return bodies, expiries


def _validate_certification_evidence(
    value: Any, reason: str, *, paper: bool, go: bool,
    started_at_ms: int, completed_at_ms: int, expires_at_ms: int,
    platform: Mapping[str, Any], run_id: str,
) -> None:
    _require(isinstance(value, dict) and set(value) == CERTIFICATION_FIELDS,
             reason)
    evidence_fields = (
        "provenance", "reviewed_base", "reviewed_buildkit",
        "buildx_toolchain", "isolated_builder", "isolated_builder_cleanup",
        "docker_socket_before", "docker_socket_after", "apparmor_before",
        "apparmor_after", "docker_namespace_before", "docker_namespace_after")
    equality_fields = (
        "provenance_reopened_equal", "docker_socket_records_equal",
        "apparmor_records_equal", "docker_namespace_records_equal")
    if not go:
        _require(
            value.get("requested") is False and
            value.get("eligible") is False and
            all(value.get(field) is None for field in evidence_fields) and
            all(value.get(field) is False for field in equality_fields), reason)
        return
    _require(
        value.get("requested") is True and value.get("eligible") is True and
        all(isinstance(value.get(field), dict) for field in evidence_fields) and
        all(value.get(field) is True for field in equality_fields) and
        value["docker_socket_before"] == value["docker_socket_after"] and
        value["apparmor_before"] == value["apparmor_after"] and
        value["docker_namespace_before"] == value["docker_namespace_after"],
        reason)
    provenance = value["provenance"]
    bodies, provenance_expiries = _validate_reviewed_provenance(
        provenance, reason, paper=paper, started_at_ms=started_at_ms,
        completed_at_ms=completed_at_ms)
    expected_expiry = min(provenance_expiries)
    if paper:
        expected_expiry = min(expected_expiry, completed_at_ms + 60 * 60 * 1000)
    # A signed environment-review closure may expire before the four
    # provenance records.  The outer validator binds the exact minimum after
    # it has validated that independent review record.
    _require(expires_at_ms <= expected_expiry, reason)

    reviewed_base = value["reviewed_base"]
    _require(isinstance(reviewed_base, dict) and set(reviewed_base) == {
        "reference", "id", "repo_digests", "os", "architecture",
        "declared_volumes", "onbuild_instructions", "labels_sha256",
        "production_approved", "reviewed_provenance",
    }, reason)
    _require(
        reviewed_base.get("reference") == platform.get("base_image_reference") ==
            bodies["base"]["repo_digest"] and
        reviewed_base.get("id") == platform.get("base_image_id") ==
            bodies["base"]["image_id"] and
        reviewed_base.get("labels_sha256") == bodies["base"]["labels_sha256"] and
        reviewed_base.get("os") == "linux" and
        reviewed_base.get("os") == platform.get("base_image_os") and
        reviewed_base.get("architecture") == "amd64" and
        reviewed_base.get("architecture") ==
            platform.get("base_image_architecture") and
        reviewed_base.get("declared_volumes") == 0 and
        reviewed_base.get("onbuild_instructions") == 0 and
        reviewed_base.get("production_approved") is True and
        reviewed_base.get("reviewed_provenance") == provenance["base"] and
        isinstance(reviewed_base.get("repo_digests"), list) and
        reviewed_base["repo_digests"] ==
            sorted(set(reviewed_base["repo_digests"])) and
        reviewed_base["reference"] in reviewed_base["repo_digests"] and
        all(type(item) is str and PINNED_IMAGE.fullmatch(item) is not None
            for item in reviewed_base["repo_digests"]), reason)

    reviewed_builder = value["reviewed_buildkit"]
    _require(isinstance(reviewed_builder, dict) and set(reviewed_builder) == {
        "reference", "id", "bare_id", "repo_digests", "os",
        "architecture", "config_sha256", "config_labels", "entrypoint",
        "production_approved", "reviewed_provenance",
    }, reason)
    _require(
        reviewed_builder.get("reference") == bodies["builder"]["repo_digest"] and
        reviewed_builder.get("id") == bodies["builder"]["image_id"] and
        reviewed_builder.get("bare_id") ==
            str(reviewed_builder["id"]).removeprefix("sha256:") and
        reviewed_builder.get("config_sha256") ==
            bodies["builder"]["config_sha256"] and
        reviewed_builder.get("os") == "linux" and
        reviewed_builder.get("architecture") == "amd64" and
        reviewed_builder.get("production_approved") is True and
        reviewed_builder.get("reviewed_provenance") == provenance["builder"] and
        isinstance(reviewed_builder.get("repo_digests"), list) and
        reviewed_builder["repo_digests"] ==
            sorted(set(reviewed_builder["repo_digests"])) and
        reviewed_builder["reference"] in reviewed_builder["repo_digests"] and
        all(type(item) is str and PINNED_IMAGE.fullmatch(item) is not None
            for item in reviewed_builder["repo_digests"]) and
        isinstance(reviewed_builder.get("config_labels"), dict) and
        all(type(key) is str and type(item) is str
            for key, item in reviewed_builder["config_labels"].items()) and
        not {"io.hepta.purpose", "io.hepta.role", "io.hepta.run-id",
             "io.hepta.buildkit-image-id", "io.hepta.buildx-builder"}.intersection(
                 reviewed_builder["config_labels"]) and
        reviewed_builder.get("entrypoint") in (
            ["buildkitd"], ["/usr/bin/buildkitd"],
            ["/usr/local/bin/buildkitd"]), reason)

    toolchain = value["buildx_toolchain"]
    _require(isinstance(toolchain, dict) and set(toolchain) == {
        "buildx_path_sha256", "buildx_version", "buildx_binary_sha256",
        "docker_server_version", "docker_server_api_version",
        "docker_server_git_commit", "reviewed",
    } and toolchain.get("reviewed") is True and
             toolchain.get("buildx_binary_sha256") ==
                bodies["builder"]["buildx_binary_sha256"] and
             toolchain.get("buildx_version") ==
                bodies["builder"]["buildx_version"] and
             toolchain.get("docker_server_version") ==
                bodies["builder"]["docker_server_version"] ==
                platform.get("docker_server_version") and
             toolchain.get("docker_server_api_version") ==
                bodies["builder"]["docker_server_api_version"] ==
                platform.get("docker_server_api_version") and
             toolchain.get("docker_server_git_commit") ==
                bodies["builder"]["docker_server_git_commit"], reason)
    _digest(toolchain.get("buildx_path_sha256"), reason)

    builder = value["isolated_builder"]
    _require(isinstance(builder, dict) and set(builder) == {
        "names", "container_id", "volume", "container_before_start",
        "container_running", "runtime",
    }, reason)
    names = builder.get("names")
    builder_name = (
        "hepta-paper-domain-isolated-" if paper else
        "hepta-p1-dual-isolated-") + run_id
    expected_names = {
        "builder": builder_name, "node": builder_name + "0",
        "container": "buildx_buildkit_" + builder_name + "0",
        "volume": "buildx_buildkit_" + builder_name + "0_state",
    }
    _require(names == expected_names and type(builder.get("container_id")) is str
             and BARE_SHA256.fullmatch(builder["container_id"]) is not None,
             reason)
    purpose = (
        "paper-domain-rootful-systemd-gate" if paper else
        "p1-dual-domain-rootful-gate")
    common_labels = {
        "io.hepta.purpose": purpose,
        "io.hepta.run-id": run_id,
        "io.hepta.buildkit-image-id": reviewed_builder["id"],
        "io.hepta.buildx-builder": builder_name,
    }
    expected_state_labels = {
        **common_labels, "io.hepta.role": "isolated-buildkit-state"}
    expected_daemon_labels = {
        **common_labels, "io.hepta.role": "isolated-buildkit-daemon"}
    running = builder.get("container_running")
    stopped = builder.get("container_before_start")
    expected_container_fields = {
        "container_id", "name", "network_mode", "privileged", "bind_mounts",
        "devices", "published_ports", "running", "labels",
    }
    for record, expected_running in ((stopped, False), (running, True)):
        _require(
            isinstance(record, dict) and set(record) == expected_container_fields and
            record.get("network_mode") == "none" and
            record.get("privileged") is True and
            record.get("bind_mounts") == 0 and record.get("devices") == 0 and
            record.get("published_ports") == 0 and
            record.get("running") is expected_running and
            record.get("name") == expected_names["container"] and
            record.get("labels") == expected_daemon_labels, reason)
    _require(stopped.get("container_id") == running.get("container_id") ==
             builder.get("container_id"), reason)
    volume = builder.get("volume")
    _require(isinstance(volume, dict) and set(volume) == {
        "name", "driver", "scope", "labels", "mountpoint_sha256"} and
        volume.get("name") == expected_names["volume"] and
        volume.get("driver") == "local" and volume.get("scope") == "local" and
        volume.get("labels") == expected_state_labels, reason)
    _digest(volume.get("mountpoint_sha256"), reason)
    runtime = builder.get("runtime")
    _require(isinstance(runtime, dict) and set(runtime) == {
        "builder", "node", "driver", "status", "buildkit_version"} and
        runtime.get("builder") == expected_names["builder"] and
        runtime.get("node") == expected_names["node"] and
        runtime.get("driver") == "docker-container" and
        runtime.get("status") == "running" and
        runtime.get("buildkit_version") == bodies["builder"]["buildkit_version"],
        reason)

    cleanup = value["isolated_builder_cleanup"]
    _require(isinstance(cleanup, dict) and cleanup == {
        "buildx_rm": "completed", "container_absent": True,
        "state_volume_absent": True,
        "cache_cleanup": "state-volume-removed",
        "private_builder_metadata_absent": True,
        "buildkit_image_retained": True,
    }, reason)
    socket = value["docker_socket_before"]
    _require(isinstance(socket, dict) and set(socket) == {
        "device", "inode", "mode", "uid", "gid", "owner_root",
        "world_writable"} and type(socket.get("device")) is int and
        socket["device"] >= 0 and type(socket.get("inode")) is int and
        socket["inode"] > 0 and type(socket.get("mode")) is str and
        re.fullmatch(r"0[0-7]{3}", socket["mode"]) is not None and
        not int(socket["mode"], 8) & 0o002 and socket.get("uid") == 0 and
        type(socket.get("gid")) is int and socket["gid"] >= 0 and
        socket.get("owner_root") is True and
        socket.get("world_writable") is False, reason)

    apparmor = value["apparmor_before"]
    _require(isinstance(apparmor, dict) and set(apparmor) == {
        "profile", "mode", "attach", "learning_count", "profile_sha256",
        "raw_sha256", "raw_abi", "raw_data_id", "profile_inventory_count",
        "profile_inventory_sha256", "namespace", "reviewed_provenance"} and
        apparmor.get("profile") == "hepta-systemd-gate" and
        apparmor.get("mode") == "enforce" and
        apparmor.get("attach") == "hepta-systemd-gate" and
        apparmor.get("learning_count") == 0 and
        type(apparmor.get("raw_data_id")) is str and
        re.fullmatch(r"[1-9][0-9]{0,19}", apparmor["raw_data_id"])
            is not None and
        type(apparmor.get("profile_inventory_count")) is int and
        apparmor["profile_inventory_count"] > 0 and
        apparmor.get("namespace") == {
            "name": "root", "level": 0, "stacked": False} and
        apparmor.get("reviewed_provenance") == provenance["apparmor"] and
        apparmor.get("profile_sha256") == bodies["apparmor"]["profile_sha256"] and
        apparmor.get("raw_sha256") == bodies["apparmor"]["raw_sha256"] and
        apparmor.get("raw_abi") == bodies["apparmor"]["raw_abi"], reason)
    _digest(apparmor.get("profile_inventory_sha256"), reason)

    namespace = value["docker_namespace_before"]
    _require(isinstance(namespace, dict) and set(namespace) == {
        "docker_daemon_id", "docker_daemon_pid",
        "docker_daemon_start_time_ticks", "docker_daemon_comm",
        "docker_daemon_process_inode", "host_boot_id", "host_namespace",
        "daemon_namespace", "same_apparmor_namespace_attested",
        "reviewed_provenance"} and
        namespace.get("docker_daemon_id") ==
            bodies["docker_namespace"]["docker_daemon_id"] and
        namespace.get("docker_daemon_pid") ==
            bodies["docker_namespace"]["docker_daemon_pid"] and
        namespace.get("docker_daemon_start_time_ticks") ==
            bodies["docker_namespace"]["docker_daemon_start_time_ticks"] and
        namespace.get("host_boot_id") ==
            bodies["docker_namespace"]["host_boot_id"] and
        namespace.get("docker_daemon_comm") == "dockerd" and
        type(namespace.get("docker_daemon_process_inode")) is int and
        namespace["docker_daemon_process_inode"] > 0 and
        namespace.get("host_namespace") == {
            "name": "root", "level": 0, "stacked": False} and
        namespace.get("daemon_namespace") == {
            "name": "root", "level": 0, "stacked": False} and
        namespace.get("same_apparmor_namespace_attested") is True and
        namespace.get("reviewed_provenance") == provenance["docker_namespace"],
        reason)


def _validate_dual_inner(value: Any, reason: str, run_id: str) -> None:
    _require(isinstance(value, dict) and set(value) == {
        "schema", "passed", "run_id", "checks", "boot", "identities",
        "faults", "inventory", "boundary"} and
        value.get("schema") == "hepta.p1-dual-domain-rootful-inner.v1" and
        value.get("passed") is True and value.get("run_id") == run_id, reason)
    _checks(value.get("checks"), reason, DUAL_DOMAIN_EXPECTED_CHECKS)
    boot = value.get("boot")
    _require(isinstance(boot, dict) and set(boot) == {
        "boot_id", "pid1_cgroup", "systemd"} and
        type(boot.get("boot_id")) is str and
        BOOT_ID.fullmatch(boot["boot_id"]) is not None and
        boot.get("pid1_cgroup") == "0::/" and
        type(boot.get("systemd")) is str and bool(boot["systemd"]), reason)
    _require(value.get("identities") == DUAL_EXPECTED_IDENTITIES, reason)
    faults = value.get("faults")
    _require(isinstance(faults, dict) and
             set(faults) == set(DUAL_EXPECTED_FAULTS), reason)
    for name, record in faults.items():
        _require(isinstance(record, dict) and set(record) == {
            "plane", "domain_id", "before_pid", "after_pid",
            "before_generation", "after_generation", "tombstone_generation",
            "restart_observed", "stale_generation_rejected"}, reason)
        plane, domain = DUAL_EXPECTED_FAULTS[name]
        _require(
            record.get("plane") == plane and record.get("domain_id") == domain and
            all(type(record.get(field)) is int and record[field] > 0
                for field in ("before_pid", "after_pid", "before_generation",
                              "after_generation", "tombstone_generation")) and
            record["before_pid"] != record["after_pid"] and
            record["after_generation"] == record["before_generation"] + 1 and
            record["tombstone_generation"] == record["before_generation"] and
            record.get("restart_observed") is True and
            record.get("stale_generation_rejected") is True, reason)
    inventory = value.get("inventory")
    _require(isinstance(inventory, dict) and set(inventory) == {
        "immutable_file_count", "immutable_file_inventory_sha256",
        "inert_daemon_sha256", "forbidden_ib_api_payloads",
        "protected_broker_sockets", "network_interfaces"} and
        type(inventory.get("immutable_file_count")) is int and
        inventory["immutable_file_count"] > 0 and
        BARE_SHA256.fullmatch(str(
            inventory.get("immutable_file_inventory_sha256"))) is not None and
        BARE_SHA256.fullmatch(str(
            inventory.get("inert_daemon_sha256"))) is not None and
        inventory.get("forbidden_ib_api_payloads") == 0 and
        inventory.get("protected_broker_sockets") == 0 and
        inventory.get("network_interfaces") == ["lo"], reason)
    _require(value.get("boundary") == DUAL_EXPECTED_BOUNDARY, reason)


def _validate_paper_inner(value: Any, reason: str, run_id: str) -> None:
    _require(isinstance(value, dict) and set(value) == {
        "schema", "passed", "run_id", "checks", "versions", "boot",
        "boundary"} and
        value.get("schema") == "hepta.paper-domain-rootful-systemd-inner.v2" and
        value.get("passed") is True and value.get("run_id") == run_id, reason)
    _checks(value.get("checks"), reason, ROOTFUL_EXPECTED_CHECKS)
    versions = value.get("versions")
    _require(isinstance(versions, dict) and set(versions) == {
        "systemd", "nft", "kernel", "architecture", "cgroup",
        "immutable_file_count", "immutable_file_inventory_sha256",
        "package_count", "package_inventory_sha256"} and
        all(type(item) is str and bool(item) for item in versions.values()) and
        all(versions[field].isdecimal() and int(versions[field]) > 0
            for field in ("immutable_file_count", "package_count")) and
        all(BARE_SHA256.fullmatch(versions[field]) is not None for field in (
            "immutable_file_inventory_sha256", "package_inventory_sha256")),
        reason)
    boot = value.get("boot")
    _require(isinstance(boot, dict) and set(boot) == {
        "boot_id", "pid1_cgroup"} and type(boot.get("boot_id")) is str and
        BOOT_ID.fullmatch(boot["boot_id"]) is not None and
        boot.get("pid1_cgroup") == "0::/", reason)
    _require(value.get("boundary") == PAPER_EXPECTED_INNER_BOUNDARY, reason)


def _validate_agent_os_inner(value: Any, reason: str) -> None:
    fields = {
        "schema", "profile", "passed", "identities", "checks",
        "lifecycle", "boundary",
    }
    _require(
        isinstance(value, dict) and set(value) == fields and
        value.get("schema") ==
            "hepta.agent-os-rootful-systemd-e2e-inner.v2" and
        value.get("profile") ==
            "two-domain-agent-gateway-execution-watch" and
        value.get("passed") is True and
        value.get("identities") == AGENT_OS_INNER_IDENTITIES and
        value.get("boundary") == AGENT_OS_INNER_BOUNDARY,
        reason)
    _checks(value.get("checks"), reason, AGENT_OS_INNER_CHECKS)
    lifecycle = value.get("lifecycle")
    _require(
        isinstance(lifecycle, dict) and set(lifecycle) == {
            "watch_generation", "initial", "service_reactivation",
            "socket_reactivation", "trust_domains"} and
        type(lifecycle.get("watch_generation")) is int and
        lifecycle["watch_generation"] > 0,
        reason)
    phase_fields = {
        "gateway_pid", "simulator_pid", "tool_socket_inode",
        "supervisor_socket_inode", "execution_socket_inode",
        "events_socket_inode",
    }
    for phase in ("initial", "service_reactivation", "socket_reactivation"):
        record = lifecycle.get(phase)
        _require(
            isinstance(record, dict) and set(record) == phase_fields and
            all(type(item) is int and item > 0 for item in record.values()),
            reason)
    initial = lifecycle["initial"]
    services = lifecycle["service_reactivation"]
    sockets = lifecycle["socket_reactivation"]
    _require(
        initial["gateway_pid"] != services["gateway_pid"] and
        initial["simulator_pid"] != services["simulator_pid"] and
        services["gateway_pid"] != sockets["gateway_pid"] and
        services["simulator_pid"] != sockets["simulator_pid"] and
        all(initial[field] == services[field] != sockets[field]
            for field in phase_fields - {"gateway_pid", "simulator_pid"}),
        reason)
    domains = lifecycle.get("trust_domains")
    domain_fields = {
        "watch_generation", "gateway_pid", "simulator_pid",
        "custodian_pid", "reader_owner_pid", "custodian_crash_generation",
        "custodian_restart_count", "closure_receipt_count",
        "tool_socket_inode", "supervisor_socket_inode",
        "execution_socket_inode", "events_socket_inode",
    }
    _require(
        isinstance(domains, dict) and
        set(domains) == {"codex-a", "openclaw-b"}, reason)
    for record in domains.values():
        _require(
            isinstance(record, dict) and set(record) == domain_fields and
            all(type(item) is int and item > 0 for item in record.values()),
            reason)
    distinct_fields = {
        "gateway_pid", "simulator_pid", "custodian_pid", "reader_owner_pid",
        "tool_socket_inode", "supervisor_socket_inode",
        "execution_socket_inode", "events_socket_inode",
    }
    _require(all(
        domains["codex-a"][field] != domains["openclaw-b"][field]
        for field in distinct_fields), reason)


def _validate_agent_os_inputs(
    value: Any, build: Any, reason: str,
) -> list[dict[str, Any]]:
    _require(
        isinstance(build, dict) and set(build) == {
            "path", "cmake_cache_sha256", "compile_commands_sha256",
            "build_type", "ibapi_enabled", "legacy_bridge_enabled"} and
        build.get("build_type") == "Release" and
        build.get("ibapi_enabled") is False and
        build.get("legacy_bridge_enabled") is False,
        reason)
    for field in ("cmake_cache_sha256", "compile_commands_sha256"):
        _bare_digest(build.get(field), reason)
    build_path = build.get("path")
    _require(type(build_path) is str and bool(build_path), reason)
    build_relative = PurePosixPath(build_path)
    _require(
        not build_relative.is_absolute() and
        build_relative.as_posix() == build_path and
        all(part not in {"", ".", ".."} for part in build_relative.parts),
        reason)
    _require(isinstance(value, list) and bool(value), reason)
    paths: list[str] = []
    records: dict[str, dict[str, Any]] = {}
    for record in value:
        _require(
            isinstance(record, dict) and set(record) == {
                "path", "sha256", "size", "mode", "device", "inode"} and
            type(record.get("path")) is str and bool(record["path"]) and
            BARE_SHA256.fullmatch(str(record.get("sha256", ""))) is not None and
            type(record.get("size")) is int and record["size"] > 0 and
            re.fullmatch(r"0[0-7]{3}", str(record.get("mode", ""))) is not None
            and type(record.get("device")) is int and record["device"] >= 0 and
            type(record.get("inode")) is int and record["inode"] > 0,
            reason)
        path = record["path"]
        relative = PurePosixPath(path)
        _require(
            not relative.is_absolute() and relative.as_posix() == path and
            all(part not in {"", ".", ".."} for part in relative.parts),
            reason)
        paths.append(path)
        records[path] = record
    _require(
        len(records) == len(value) and paths == sorted(paths), reason)
    static = set(records) & set(AGENT_OS_SOURCE_MODES)
    dynamic = set(records) - static
    _require(
        static == set(AGENT_OS_SOURCE_MODES) and
        all(records[path]["mode"] == AGENT_OS_SOURCE_MODES[path]
            for path in static) and
        len(dynamic) == len(AGENT_OS_BUILD_BINARIES) and
        {PurePosixPath(path).name for path in dynamic} ==
            set(AGENT_OS_BUILD_BINARIES) and
        all(build_relative in PurePosixPath(path).parents and
            records[path]["mode"] == "0755" for path in dynamic),
        reason)
    return value


def _validate_agent_os_reviewed_record(
    value: Any, kind: str, review: Mapping[str, Any], reason: str,
) -> dict[str, Any]:
    fields = REVIEWED_PROVENANCE_FIELDS[kind] | {"document_sha256"}
    _require(
        isinstance(value, dict) and set(value) == fields and
        value.get("schema") == REVIEWED_PROVENANCE_SCHEMAS[kind] and
        value.get("decision") == "GO" and
        value.get("document_sha256") ==
            review["outputs"][kind]["file_sha256"],
        reason)
    _digest(value.get("document_sha256"), reason)
    _integer(value.get("issued_at_ms"), reason)
    _integer(value.get("expires_at_ms"), reason)
    return value


def _validate_agent_os_builder_runtime(
    builder: Mapping[str, Any], *, run_id: str, buildkit_image_id: str,
    buildkit_version: str, reason: str,
) -> None:
    builder_name = f"hepta-isolated-{run_id}"
    node_name = builder_name + "0"
    container_name = "buildx_buildkit_" + node_name
    volume_name = container_name + "_state"
    names = {
        "builder": builder_name, "node": node_name,
        "container": container_name, "volume": volume_name,
    }
    common_labels = {
        "io.hepta.purpose": "agent-os-rootful-systemd-e2e-gate",
        "io.hepta.run-id": run_id,
        "io.hepta.buildkit-image-id": buildkit_image_id,
        "io.hepta.buildx-builder": builder_name,
    }
    state_labels = {
        **common_labels, "io.hepta.role": "isolated-buildkit-state"}
    daemon_labels = {
        **common_labels, "io.hepta.role": "isolated-buildkit-daemon"}
    objects = builder.get("objects")
    _require(
        isinstance(objects, dict) and set(objects) == {
            "names", "container_id", "volume", "container_before_start",
            "container_running", "runtime"} and
        objects.get("names") == names and
        re.fullmatch(r"[0-9a-f]{64}", str(
            objects.get("container_id", ""))) is not None,
        reason)
    volume = objects["volume"]
    _require(
        isinstance(volume, dict) and set(volume) == {
            "name", "driver", "scope", "labels", "mountpoint_sha256"} and
        volume.get("name") == volume_name and
        volume.get("driver") == volume.get("scope") == "local" and
        volume.get("labels") == state_labels,
        reason)
    _digest(volume.get("mountpoint_sha256"), reason)
    container_fields = {
        "container_id", "name", "image_id", "builder", "node",
        "state_volume", "network_mode", "privileged", "bind_mounts",
        "published_ports", "running", "labels",
    }
    expected_container = {
        "container_id": objects["container_id"], "name": container_name,
        "image_id": buildkit_image_id, "builder": builder_name,
        "node": node_name, "state_volume": volume_name,
        "network_mode": "none", "privileged": True, "bind_mounts": 0,
        "published_ports": 0, "labels": daemon_labels,
    }
    for field, running in (
            ("container_before_start", False),
            ("container_running", True)):
        record = objects[field]
        _require(
            isinstance(record, dict) and set(record) == container_fields and
            {key: record.get(key) for key in expected_container} ==
                expected_container and record.get("running") is running,
            reason)
    runtime = objects["runtime"]
    _require(
        isinstance(runtime, dict) and set(runtime) == {
            "builder", "node", "driver", "status", "buildkit_version"} and
        runtime == {
            "builder": builder_name, "node": node_name,
            "driver": "docker-container", "status": "running",
            "buildkit_version": buildkit_version,
        }, reason)
    cache = builder.get("builder_cache_before_cleanup")
    _require(
        isinstance(cache, dict) and set(cache) == {
            "record_count", "inventory_sha256"} and
        type(cache.get("record_count")) is int and
        cache["record_count"] >= 0, reason)
    _digest(cache.get("inventory_sha256"), reason)
    stopped = builder.get("builder_stopped")
    _require(
        isinstance(stopped, dict) and set(stopped) == container_fields and
        {key: stopped.get(key) for key in expected_container} ==
            expected_container and stopped.get("running") is False,
        reason)
    cleanup = builder.get("cleanup")
    _require(
        isinstance(cleanup, dict) and set(cleanup) == {
            "buildx_rm", "container_absent", "state_volume_absent",
            "private_builder_metadata_absent", "exact_container_fallback",
            "exact_volume_fallback", "cache_cleanup"} and
        cleanup.get("buildx_rm") == "completed" and
        cleanup.get("container_absent") is True and
        cleanup.get("state_volume_absent") is True and
        cleanup.get("private_builder_metadata_absent") is True and
        type(cleanup.get("exact_container_fallback")) is bool and
        type(cleanup.get("exact_volume_fallback")) is bool and
        cleanup.get("cache_cleanup") == "state-volume-removed",
        reason)


def _validate_agent_os_outer_evidence(
    document: Mapping[str, Any], review: Mapping[str, Any], reason: str,
) -> None:
    observations = review["environment_fingerprint"]["observations"]
    base_observation = observations["base_image"]
    base = document.get("base_image")
    _require(
        isinstance(base, dict) and set(base) == {
            "reference", "id", "repo_digests", "os", "architecture",
            "declared_volumes", "base_class", "production_approved",
            "production_status", "reviewed_provenance"} and
        base.get("reference") == base_observation["repo_digest"] and
        base.get("id") == base_observation["image_id"] and
        base.get("repo_digests") == base_observation["repo_digests"] and
        base.get("os") == base_observation["os"] == "linux" and
        base.get("architecture") == base_observation["architecture"] and
        base.get("declared_volumes") == 0 and
        base.get("base_class") == "reviewed-offline-ready" and
        base.get("production_approved") is True and
        base.get("production_status") == "external-reviewed-go",
        reason)
    base_review = _validate_agent_os_reviewed_record(
        base.get("reviewed_provenance"), "base", review, reason)
    _require(
        base_review["image_id"] == base["id"] and
        base_review["repo_digest"] == base["reference"] and
        base_review["labels_sha256"] == base_observation["labels_sha256"],
        reason)

    builder_observation = observations["isolated_builder"]
    builder = document.get("builder")
    _require(
        isinstance(builder, dict) and set(builder) == {
            "mode", "isolated", "cache_reuse", "builder_cache_cleanup",
            "preloaded_image_only", "reviewed_provenance",
            "production_eligible", "toolchain", "buildkit_image", "objects",
            "builder_cache_before_cleanup", "builder_stopped", "cleanup",
            "cleanup_complete"} and
        builder.get("mode") == "reviewed-isolated-buildx" and
        builder.get("isolated") is True and
        builder.get("cache_reuse") == "disabled" and
        builder.get("builder_cache_cleanup") == "state-volume-removed" and
        builder.get("preloaded_image_only") is True and
        builder.get("production_eligible") is True and
        builder.get("cleanup_complete") is True and
        all(isinstance(builder.get(field), dict) and builder[field]
            for field in (
                "toolchain", "buildkit_image", "objects",
                "builder_cache_before_cleanup", "builder_stopped", "cleanup")),
        reason)
    builder_review = _validate_agent_os_reviewed_record(
        builder.get("reviewed_provenance"), "builder", review, reason)
    buildkit = builder["buildkit_image"]
    _require(
        set(buildkit) == {
            "reference", "id", "bare_id", "repo_digests", "config_sha256",
            "config_labels", "entrypoint", "production_status",
            "production_approved"} and
        buildkit.get("reference") == builder_observation["repo_digest"] and
        buildkit.get("id") == builder_observation["image_id"] and
        buildkit.get("bare_id") == buildkit["id"].removeprefix("sha256:") and
        buildkit.get("repo_digests") == builder_observation["repo_digests"] and
        buildkit.get("config_sha256") == builder_observation["config_sha256"] and
        buildkit.get("entrypoint") == builder_observation["entrypoint"] and
        buildkit.get("production_status") == "external-reviewed-go" and
        buildkit.get("production_approved") is True and
        isinstance(buildkit.get("config_labels"), dict) and
        builder_review["image_id"] == buildkit["id"] and
        builder_review["repo_digest"] == buildkit["reference"] and
        builder_review["config_sha256"] == buildkit["config_sha256"],
        reason)
    _require(
        all(type(key) is str and type(value) is str
            for key, value in buildkit["config_labels"].items()) and
        not {
            "io.hepta.purpose", "io.hepta.role", "io.hepta.run-id",
            "io.hepta.buildkit-image-id", "io.hepta.buildx-builder",
        }.intersection(buildkit["config_labels"]), reason)
    toolchain = builder["toolchain"]
    _require(
        set(toolchain) == {
            "buildx_path", "buildx_version", "buildx_binary_sha256",
            "docker_server_version", "docker_server_api_version",
            "docker_server_git_commit", "reviewed"} and
        toolchain.get("reviewed") is True and
        toolchain.get("buildx_version") ==
            builder_observation["buildx_version"] and
        toolchain.get("buildx_binary_sha256") ==
            builder_observation["buildx_binary_sha256"] and
        toolchain.get("docker_server_version") ==
            builder_observation["docker_server_version"] and
        toolchain.get("docker_server_api_version") ==
            builder_observation["docker_server_api_version"] and
        toolchain.get("docker_server_git_commit") ==
            builder_observation["docker_server_git_commit"],
        reason)
    image_record = document.get("image")
    _require(
        isinstance(image_record, dict) and
        RUN_ID.fullmatch(str(image_record.get("run_id", ""))) is not None,
        reason)
    _validate_agent_os_builder_runtime(
        builder, run_id=image_record["run_id"],
        buildkit_image_id=buildkit["id"],
        buildkit_version=builder_review["buildkit_version"], reason=reason)
    _require(
        type(toolchain.get("buildx_path")) is str and
        PurePosixPath(toolchain["buildx_path"]).is_absolute() and
        PurePosixPath(toolchain["buildx_path"]).as_posix() ==
            toolchain["buildx_path"] and
        all(part not in {"", ".", ".."}
            for part in PurePosixPath(toolchain["buildx_path"]).parts[1:]),
        reason)

    docker_host = document.get("docker_host")
    _require(
        isinstance(docker_host, dict) and set(docker_host) == {
            "socket_owner_root", "socket_world_writable", "client"} and
        docker_host.get("socket_owner_root") is True and
        docker_host.get("socket_world_writable") is False and
        type(docker_host.get("client")) is str and
        docker_host["client"].startswith("Docker version "), reason)

    apparmor_observation = observations["apparmor"]
    apparmor = document.get("apparmor")
    kernel_anchor = (
        apparmor.get("kernel_anchor") if isinstance(apparmor, dict) else None)
    kernel_namespace = (
        kernel_anchor.get("namespace")
        if isinstance(kernel_anchor, dict) else None)
    _require(
        isinstance(apparmor, dict) and set(apparmor) == {
            "profile", "mode", "attach", "learning_count", "profile_sha256",
            "raw_sha256", "raw_abi", "raw_data_id", "raw_data_size",
            "policy_entry", "profile_inventory_count",
            "profile_inventory_sha256", "policy_content_attested",
            "reviewed_provenance", "kernel_anchor", "kernel_aafs_attested"} and
        apparmor.get("profile") == apparmor_observation["profile"] and
        apparmor.get("mode") == apparmor_observation["mode"] == "enforce" and
        apparmor.get("attach") == apparmor_observation["attach"] and
        apparmor.get("learning_count") == 0 and
        apparmor.get("profile_sha256") ==
            apparmor_observation["profile_sha256"] and
        apparmor.get("raw_sha256") == apparmor_observation["raw_sha256"] and
        apparmor.get("raw_abi") == apparmor_observation["raw_abi"] and
        apparmor.get("profile_inventory_sha256") ==
            apparmor_observation["profile_inventory_sha256"] and
        apparmor.get("policy_content_attested") is True and
        apparmor.get("kernel_aafs_attested") is True and
        isinstance(kernel_anchor, dict) and
        isinstance(kernel_namespace, dict) and
        set(kernel_namespace) == {
            "name", "level", "stacked", "field_metadata_sha256"} and
        kernel_namespace == {
            "name": apparmor_observation["namespace_name"],
            "level": apparmor_observation["namespace_level"],
            "stacked": apparmor_observation["namespace_stacked"],
            "field_metadata_sha256":
                kernel_namespace.get("field_metadata_sha256"),
        },
        reason)
    _digest(kernel_namespace.get("field_metadata_sha256"), reason)
    apparmor_review = _validate_agent_os_reviewed_record(
        apparmor.get("reviewed_provenance"), "apparmor", review, reason)
    _require(
        apparmor_review["profile"] == apparmor["profile"] and
        apparmor_review["profile_sha256"] == apparmor["profile_sha256"] and
        apparmor_review["raw_sha256"] == apparmor["raw_sha256"] and
        apparmor_review["raw_abi"] == apparmor["raw_abi"] and
        apparmor_review["policy_source_sha256"] ==
            apparmor_observation["policy_source_sha256"], reason)

    namespace_observation = observations["docker_namespace"]
    namespace = document.get("docker_apparmor_namespace")
    _require(
        isinstance(namespace, dict) and set(namespace) == {
            "docker_daemon_id", "docker_daemon_pid",
            "docker_daemon_start_time_ticks", "docker_daemon_comm",
            "docker_daemon_process_inode",
            "docker_daemon_process_metadata_sha256", "host_boot_id",
            "host_namespace", "daemon_namespace",
            "same_apparmor_namespace_attested", "reviewed_provenance"} and
        namespace.get("docker_daemon_id") ==
            namespace_observation["docker_daemon_id"] and
        namespace.get("docker_daemon_pid") ==
            namespace_observation["docker_daemon_pid"] and
        namespace.get("docker_daemon_start_time_ticks") ==
            namespace_observation["docker_daemon_start_time_ticks"] and
        namespace.get("docker_daemon_comm") == "dockerd" and
        namespace.get("host_boot_id") == namespace_observation["host_boot_id"] and
        namespace.get("host_namespace") == {
            "name": namespace_observation["host_namespace_name"],
            "level": namespace_observation["host_namespace_level"],
            "stacked": namespace_observation["host_namespace_stacked"],
        } and namespace.get("daemon_namespace") == {
            "name": namespace_observation["daemon_namespace_name"],
            "level": namespace_observation["daemon_namespace_level"],
            "stacked": namespace_observation["daemon_namespace_stacked"],
        } and namespace.get("same_apparmor_namespace_attested") is True,
        reason)
    _digest(namespace.get("docker_daemon_process_metadata_sha256"), reason)
    _integer(namespace.get("docker_daemon_process_inode"), reason, minimum=1)
    namespace_review = _validate_agent_os_reviewed_record(
        namespace.get("reviewed_provenance"), "docker_namespace", review,
        reason)
    _require(
        all(namespace_review[field] == namespace_observation[field]
            for field in (
                "docker_daemon_id", "docker_daemon_pid",
                "docker_daemon_start_time_ticks", "host_boot_id",
                "host_namespace_name", "host_namespace_level",
                "host_namespace_stacked", "daemon_namespace_name",
                "daemon_namespace_level", "daemon_namespace_stacked")),
        reason)

    image = document.get("image")
    _require(
        isinstance(image, dict) and set(image) == {
            "id", "purpose", "role", "run_id", "build_network",
            "cache_reuse", "builder_cache_cleanup", "source_image_id",
            "base_rootfs_sha256", "base_rootfs_size",
            "base_construction_version", "labels", "repo_tags",
            "repo_digests"} and
        DIGEST.fullmatch(str(image.get("id", ""))) is not None and
        image.get("purpose") == "agent-os-rootful-systemd-e2e-gate" and
        image.get("role") == "offline-rootful-systemd-runtime" and
        RUN_ID.fullmatch(str(image.get("run_id", ""))) is not None and
        image.get("build_network") == "none" and
        image.get("cache_reuse") == "disabled" and
        image.get("builder_cache_cleanup") == "state-volume-removed" and
        image.get("source_image_id") == base["id"] and
        image.get("base_construction_version") ==
            "docker-export-scratch-add-v1" and
        type(image.get("base_rootfs_size")) is int and
        image["base_rootfs_size"] > 0 and isinstance(image.get("labels"), dict)
        and isinstance(image.get("repo_tags"), list) and
        isinstance(image.get("repo_digests"), list), reason)
    _digest(image.get("base_rootfs_sha256"), reason)
    holder = document.get("base_holder")
    _require(
        isinstance(holder, dict) and set(holder) == {
            "container_id", "name", "image_id", "purpose", "role", "run_id",
            "network_mode", "read_only_rootfs", "mounts", "volumes"} and
        re.fullmatch(r"[0-9a-f]{64}", str(holder.get("container_id", ""))) and
        holder.get("image_id") == base["id"] and
        holder.get("purpose") == image["purpose"] and
        holder.get("role") == "base-rootfs-snapshot-holder" and
        holder.get("run_id") == image["run_id"] and
        holder.get("network_mode") == "none" and
        holder.get("read_only_rootfs") is True and
        holder.get("mounts") == holder.get("volumes") == 0, reason)
    container = document.get("container")
    _require(
        isinstance(container, dict) and set(container) == {
            "container_id", "image_id", "network_mode", "read_only_rootfs",
            "bind_mounts", "published_ports", "privileged",
            "apparmor_profile", "private_cgroup_namespace"} and
        re.fullmatch(r"[0-9a-f]{64}", str(container.get("container_id", ""))) and
        container.get("image_id") == image["id"] and
        container.get("network_mode") == "none" and
        container.get("read_only_rootfs") is True and
        container.get("bind_mounts") == container.get("published_ports") == 0
        and container.get("privileged") is False and
        container.get("apparmor_profile") == apparmor["profile"] and
        container.get("private_cgroup_namespace") is True, reason)
    cleanup = document.get("owned_docker_objects_cleanup")
    _require(
        cleanup == {
            "runtime_container": {"absent": True},
            "built_image": {
                "tag_absent": True, "exact_image_id_absent": True},
            "base_holder": {"absent": True},
        }, reason)


def validate_agent_os_rootful_gate(document: dict[str, Any]) -> Facts:
    reason = "AGENT_OS_ROOTFUL_GATE_INVALID"
    _require(
        set(document) == AGENT_OS_ROOTFUL_FIELDS and
        document.get("schema") ==
            "hepta.agent-os-rootful-systemd-e2e-gate.v1" and
        document.get("decision") == "GO" and
        document.get("passed") is True and
        document.get("certification_ready") is True and
        document.get("certification_blockers") == [] and
        document.get("certification_level") ==
            "externally-reviewed-rootful-systemd-certification" and
        document.get("production_eligible") is True and
        type(document.get("duration_ms")) is int and
        document["duration_ms"] >= 0 and
        document.get("input_stability") is True and
        document.get("owned_docker_objects_cleanup_complete") is True and
        document.get("boundary") == AGENT_OS_BOUNDARY and
        document.get("apparmor_revalidated") is True and
        document.get("apparmor_records_equal") is True and
        document.get("docker_apparmor_namespace_revalidated") is True and
        document.get("docker_apparmor_namespace_records_equal") is True and
        document.get("apparmor_post_cleanup") == document.get("apparmor") and
        document.get("docker_apparmor_namespace_post_cleanup") ==
            document.get("docker_apparmor_namespace"),
        reason)
    checks = document.get("completed_checks")
    required_checks = {
        "isolated_builder_contract", "local_inputs",
        "apparmor_policy_attested", "docker_apparmor_namespace_attested",
        "buildx_toolchain_attested", "pinned_local_buildkit",
        "pinned_local_base", "local_base_rootfs_snapshot",
        "isolated_builder_started", "isolated_builder_stopped",
        "offline_image_build", "container_isolation", "systemd_pid1",
        "four_uid_watch_runtime", "isolated_builder_cache_removed",
        "apparmor_revalidated", "docker_apparmor_namespace_revalidated",
        "environment_review_closure_reopened",
    }
    _require(
        isinstance(checks, list) and len(checks) == len(set(checks)) and
        set(checks) == required_checks, reason)
    review = _environment_review_record(
        document.get("environment_review_closure"), reason,
        at_ms=_integer(
            document["environment_review_closure"].get("verified_at_ms"),
            reason))
    _validate_agent_os_inputs(
        document.get("inputs"), document.get("build"), reason)
    _validate_agent_os_inner(document.get("inner"), reason)
    _validate_agent_os_outer_evidence(document, review, reason)
    base = document["base_image"]
    buildkit = document["builder"]["buildkit_image"]
    _require(
        review["base_image_reference"] == base["reference"] and
        review["buildkit_image_reference"] == buildkit["reference"], reason)
    return Facts(
        issued_at_ms=review["verified_at_ms"],
        expires_at_ms=review["expires_at_ms"], status="PASS")


def _validate_liveness_inner(value: Any, reason: str, run_id: str) -> None:
    fields = {
        "schema", "passed", "run_id", "checks", "inner_executable", "boot",
        "production_unit_inputs", "watchdog", "durable_failure",
        "effective_units_before_fault", "effective_units_after_fault",
        "cleanup", "boundary",
    }
    _require(
        isinstance(value, dict) and set(value) == fields and
        value.get("schema") ==
            "hepta.p1-safety-soak-campaign-rootful-liveness-inner.v1" and
        value.get("passed") is True and value.get("run_id") == run_id and
        value.get("boundary") == P1_LIVENESS_BOUNDARY,
        reason)
    _checks(value.get("checks"), reason, P1_LIVENESS_EXPECTED_CHECKS)
    executable = value.get("inner_executable")
    _require(
        isinstance(executable, dict) and set(executable) == {
            "path", "file_sha256", "mode", "uid", "gid"} and
        executable.get("path") == "/usr/libexec/hepta-p1-liveness-inner-gate" and
        executable.get("mode") == "0755" and executable.get("uid") == 0 and
        executable.get("gid") == 0,
        reason)
    _digest(executable.get("file_sha256"), reason)
    boot = value.get("boot")
    _require(
        isinstance(boot, dict) and set(boot) == {
            "boot_id", "pid1", "pid1_comm", "pid1_cgroup", "systemd"} and
        BOOT_ID.fullmatch(str(boot.get("boot_id", ""))) is not None and
        boot.get("pid1") == 1 and boot.get("pid1_comm") == "systemd" and
        boot.get("pid1_cgroup") == "0::/", reason)
    watchdog = value.get("watchdog")
    _require(
        isinstance(watchdog, dict) and set(watchdog) == {
            "first", "recovered", "first_pid", "recovered_pid",
            "first_invocation_id", "recovered_invocation_id", "n_restarts",
            "effective_watchdog_usec"} and
        type(watchdog.get("n_restarts")) is int and
        watchdog["n_restarts"] >= 1 and
        watchdog.get("first_pid") != watchdog.get("recovered_pid") and
        watchdog.get("first_invocation_id") !=
            watchdog.get("recovered_invocation_id") and
        watchdog.get("effective_watchdog_usec") == "2s", reason)
    failure = value.get("durable_failure")
    _require(
        isinstance(failure, dict) and
        failure.get("worker_status") == "FAILED_CLOSED" and
        failure.get("coordinator_status") == "FAILED_CLOSED" and
        failure.get("catch_up") is False and
        failure.get("post_restart_journal_entry_count") == 1 and
        failure.get("terminal_observation_acknowledged") is True and
        type(failure.get("worker_n_restarts")) is int and
        failure["worker_n_restarts"] >= 1,
        reason)
    cleanup = value.get("cleanup")
    _require(
        isinstance(cleanup, dict) and set(cleanup) == {
            "target", "units", "all_inactive", "process_residue_absent"} and
        cleanup.get("target") ==
            "hepta-p1-campaign-rootful-liveness.target" and
        cleanup.get("all_inactive") is True and
        cleanup.get("process_residue_absent") is True,
        reason)


def validate_p1_liveness_gate(document: dict[str, Any]) -> Facts:
    reason = "P1_LIVENESS_GATE_INVALID"
    _sealed(document, P1_LIVENESS_GATE_FIELDS, reason)
    run_id = _safe_token(document.get("run_id"), RUN_ID, reason)
    started, completed, expires = _gate_times(document, reason)
    _require(
        document.get("schema") ==
            "hepta.p1-safety-soak-campaign-rootful-liveness-gate.v1" and
        document.get("decision") == "GO" and
        document.get("passed") is True and
        document.get("rehearsal_passed") is True and
        document.get("certification_ready") is True and
        document.get("certification_blockers") == [] and
        document.get("scope") ==
            "p1-campaign-coordinator-rootful-liveness-prerequisite-only" and
        document.get("production_mode") ==
            "PRODUCTION_REVIEWED_ROOTFUL_CERTIFICATION" and
        document.get("duration_ms") == completed - started and
        document.get("generated_input_sha256") == {} and
        document.get("boundary") == P1_LIVENESS_BOUNDARY and
        all(document.get(field) is False for field in (
            "paper_test_admission_candidate", "paper_admission_authorized",
            "paper_authorized", "live_authorized", "mutation_authorized",
            "direct_broker_access", "order_submission_authorized")),
        reason)
    producer = document.get("producer")
    lineage = document.get("lineage")
    inputs = document.get("inputs")
    _require(
        isinstance(producer, dict) and set(producer) == {
            "path", "file_sha256"} and
        type(producer.get("path")) is str and
        isinstance(lineage, dict) and set(lineage) == {
            "source_commit", "expected_source_commit", "source_tree_clean",
            "all_inputs_versioned", "inputs_stable", "final_lineage",
            "input_manifest_sha256", "runner_sha256"} and
        GIT_HEAD.fullmatch(str(lineage.get("source_commit", ""))) is not None and
        lineage.get("expected_source_commit") == lineage["source_commit"] and
        all(lineage.get(field) is True for field in (
            "source_tree_clean", "all_inputs_versioned", "inputs_stable",
            "final_lineage")) and
        isinstance(inputs, dict) and bool(inputs), reason)
    _digest(producer.get("file_sha256"), reason)
    _canonical_path(Path(producer["path"]), reason)
    _digest(lineage.get("input_manifest_sha256"), reason)
    _digest(lineage.get("runner_sha256"), reason)
    for record in inputs.values():
        _require(
            isinstance(record, dict) and set(record) == {
                "sha256", "size", "mode"} and
            BARE_SHA256.fullmatch(str(record.get("sha256", ""))) is not None and
            type(record.get("size")) is int and record["size"] > 0 and
            type(record.get("mode")) is str and
            re.fullmatch(r"0[0-7]{3}", record["mode"]) is not None,
            reason)
    _require(
        set(inputs) == set(P1_LIVENESS_SOURCE_MODES) and
        all(inputs[path]["mode"] == mode
            for path, mode in P1_LIVENESS_SOURCE_MODES.items()),
        reason)
    runner_path = "scripts/run_hepta_p1_campaign_rootful_liveness_gate.py"
    _require(
        runner_path in inputs and
        lineage["runner_sha256"] == "sha256:" + inputs[runner_path]["sha256"] and
        producer["file_sha256"] == lineage["runner_sha256"] and
        lineage["input_manifest_sha256"] == digest_bytes(canonical_bytes(inputs)),
        reason)
    _validate_rootful_platform(document.get("platform"), reason, paper=False)
    _validate_rootful_container(document.get("container"), reason, paper=False)
    _validate_disposable_cleanup(document.get("disposable_cleanup"), reason)
    _validate_liveness_inner(document.get("inner"), reason, run_id)
    certification = document.get("certification")
    _require(
        isinstance(certification, dict) and
        set(certification) == CERTIFICATION_FIELDS and
        certification.get("requested") is True and
        certification.get("eligible") is True and
        certification.get("provenance_reopened_equal") is True and
        certification.get("docker_socket_records_equal") is True and
        certification.get("apparmor_records_equal") is True and
        certification.get("docker_namespace_records_equal") is True and
        certification.get("docker_socket_before") ==
            certification.get("docker_socket_after") and
        certification.get("apparmor_before") ==
            certification.get("apparmor_after") and
        certification.get("docker_namespace_before") ==
            certification.get("docker_namespace_after"), reason)
    bodies, provenance_expiries = _validate_reviewed_provenance(
        certification.get("provenance"), reason, paper=False,
        started_at_ms=started, completed_at_ms=completed)
    review = _environment_review_record(
        document.get("environment_review_closure"), reason,
        at_ms=completed)
    _require(
        expires == min(min(provenance_expiries), review["expires_at_ms"]) and
        review["source_commit"] == lineage["source_commit"] and
        review["base_image_reference"] ==
            document["platform"]["base_image_reference"] and
        review["buildkit_image_reference"] == bodies["builder"]["repo_digest"]
        and all(
            review["outputs"][kind]["file_sha256"] ==
                certification["provenance"][kind]["document_sha256"]
            for kind in REVIEW_OUTPUT_SCHEMAS),
        reason)
    return Facts(issued_at_ms=completed, expires_at_ms=expires, status="PASS")


def validate_dual_domain_gate(document: dict[str, Any]) -> Facts:
    reason = "DUAL_DOMAIN_GATE_INVALID"
    _sealed(document, DUAL_DOMAIN_GATE_FIELDS, reason)
    _require(document.get("schema") ==
             "hepta.p1-dual-domain-rootful-gate.v1", reason)
    run_id = _safe_token(document.get("run_id"), RUN_ID, reason)
    started, completed, expires = _gate_times(document, reason)
    _require(document.get("duration_ms") == completed - started, reason)
    decision = document.get("decision")
    go = decision == "GO"
    _require(decision in {"GO", "REHEARSAL_ONLY"} and
             document.get("passed") is go and
             document.get("rehearsal_passed") is True and
             document.get("certification_ready") is go and
             document.get("certification_blockers") ==
                ([] if go else list(CERTIFICATION_BLOCKERS)) and
             document.get("scope") ==
                "broker-free-p1-dual-domain-rootful-prerequisite-only" and
             all(document.get(field) is False for field in (
                 "paper_test_admission_candidate",
                 "paper_admission_authorized", "paper_authorized",
                 "live_authorized", "mutation_authorized",
                 "direct_broker_access", "order_submission_authorized")),
             reason)
    _validate_gate_lineage(document, reason, paper=False, go=go)
    generated = document.get("generated_input_sha256")
    _require(isinstance(generated, dict) and set(generated) == {
        "identities.json", "boundary.json", "watch-codex-a.credential",
        "watch-openclaw-b.credential", "paper-codex-a.credential",
        "paper-openclaw-b.credential"}, reason)
    for value in generated.values():
        _bare_digest(value, reason)
    platform = document.get("platform")
    _validate_rootful_platform(platform, reason, paper=False)
    _validate_rootful_container(document.get("container"), reason, paper=False)
    _validate_disposable_cleanup(document.get("disposable_cleanup"), reason)
    _validate_dual_inner(document.get("inner"), reason, run_id)
    _require(document.get("boundary") == DUAL_EXPECTED_BOUNDARY, reason)
    _validate_certification_evidence(
        document.get("certification"), reason, paper=False, go=go,
        started_at_ms=started, completed_at_ms=completed,
        expires_at_ms=expires, platform=platform, run_id=run_id)
    if go:
        review = _environment_review_record(
            document.get("environment_review_closure"), reason,
            at_ms=completed)
        provenance_expiry = min(
            int(record["expires_at_ms"])
            for record in document["certification"]["provenance"].values())
        _require(
            expires == min(provenance_expiry, review["expires_at_ms"]) and
            review["source_commit"] ==
                document["lineage"]["source_commit"] and
            review["base_image_reference"] ==
                document["platform"]["base_image_reference"] and
            all(
                review["outputs"][kind]["file_sha256"] ==
                    document["certification"]["provenance"][kind][
                        "document_sha256"]
                for kind in REVIEW_OUTPUT_SCHEMAS),
            reason)
    else:
        _require(document.get("environment_review_closure") is None, reason)
    readiness = () if go else ("DUAL_DOMAIN_GATE_NOT_PASS",)
    return Facts(issued_at_ms=completed, expires_at_ms=expires,
                 status="PASS" if go else "FAIL", readiness=readiness)


def validate_rootful_gate(document: dict[str, Any]) -> Facts:
    reason = "ROOTFUL_GATE_INVALID"
    if document.get("schema") == "hepta.paper-domain-rootful-systemd-gate.v1":
        dangers = _boundary_findings(document, "ROOTFUL_GATE", (
            "paper_test_admission_candidate", "paper_admission_authorized",
            "paper_authorized", "live_authorized", "mutation_authorized",
            "direct_broker_access", "order_submission_authorized"))
        return Facts(status="FAIL", readiness=("ROOTFUL_GATE_NOT_PASS",),
                     dangers=dangers)
    _sealed(document, ROOTFUL_GATE_FIELDS, reason)
    _require(document.get("schema") ==
             "hepta.paper-domain-rootful-systemd-gate.v2", reason)
    run_id = _safe_token(document.get("run_id"), RUN_ID, reason)
    started, completed, expires = _gate_times(document, reason)
    _require(expires - completed <= 60 * 60 * 1000, reason)
    decision = document.get("decision")
    go = decision == "GO"
    blockers = document.get("certification_blockers")
    _require(
        decision in {"GO", "NO_GO", "REHEARSAL_ONLY"} and
        type(document.get("rehearsal_passed")) is bool and
        document.get("passed") is go and
        document.get("certification_ready") is go and
        (decision == "NO_GO" or document.get("rehearsal_passed") is True) and
        isinstance(blockers, list) and
        all(type(item) is str and bool(item) for item in blockers) and
        (blockers == [] if go else
         blockers == list(CERTIFICATION_BLOCKERS)
         if decision == "REHEARSAL_ONLY" else bool(blockers)) and
        document.get("scope") ==
            "broker-free-paper-domain-rootful-prerequisite-only" and
        all(document.get(field) is False for field in (
            "paper_test_admission_candidate", "paper_authorized",
            "live_authorized", "mutation_authorized", "direct_broker_access",
            "order_submission_authorized")), reason)
    _validate_gate_lineage(document, reason, paper=True, go=go)
    generated = document.get("generated_input_sha256")
    _require(isinstance(generated, dict) and bool(generated), reason)
    for value in generated.values():
        _bare_digest(value, reason)
    platform = document.get("platform")
    _validate_rootful_platform(platform, reason, paper=True)
    _validate_rootful_container(document.get("container"), reason, paper=True)
    _validate_disposable_cleanup(document.get("disposable_cleanup"), reason)
    _validate_paper_inner(document.get("inner"), reason, run_id)
    _require(document.get("boundary") == PAPER_EXPECTED_OUTER_BOUNDARY, reason)
    _validate_certification_evidence(
        document.get("certification"), reason, paper=True, go=go,
        started_at_ms=started, completed_at_ms=completed,
        expires_at_ms=expires, platform=platform, run_id=run_id)
    if go:
        review = _environment_review_record(
            document.get("environment_review_closure"), reason,
            at_ms=completed)
        provenance_expiry = min(
            int(record["expires_at_ms"])
            for record in document["certification"]["provenance"].values())
        _require(
            expires == min(
                provenance_expiry, completed + 60 * 60 * 1000,
                review["expires_at_ms"]) and
            review["source_commit"] ==
                document["lineage"]["source_commit"] and
            review["base_image_reference"] ==
                document["platform"]["base_image_reference"] and
            all(
                review["outputs"][kind]["file_sha256"] ==
                    document["certification"]["provenance"][kind][
                        "document_sha256"]
                for kind in REVIEW_OUTPUT_SCHEMAS),
            reason)
    else:
        _require(document.get("environment_review_closure") is None, reason)
    readiness = () if go else ("ROOTFUL_GATE_NOT_PASS",)
    return Facts(issued_at_ms=completed, expires_at_ms=expires,
                 status="PASS" if go else "FAIL", readiness=readiness)


def validate_network_gate(document: dict[str, Any]) -> Facts:
    reason = "NETWORK_GATE_INVALID"
    if document.get("schema") == "hepta.broker-network-rootful-gate.v3" and \
            document.get("passed") is False:
        dangers = list(_boundary_findings(
            document, "NETWORK_GATE", ("live_authorized",)))
        if document.get("real_broker_connections") not in (0, None) or \
                document.get("paper_orders") not in (0, None):
            dangers.append("NETWORK_GATE_EXPOSURE_SIGNAL")
        return Facts(status="FAIL", readiness=("NETWORK_GATE_NOT_PASS",),
                     dangers=tuple(dangers))
    _require(set(document) == NETWORK_GATE_FIELDS and
             document.get("schema") == "hepta.broker-network-rootful-gate.v3",
             reason)
    _safe_token(document.get("run_id"), CAMPAIGN, reason)
    _require(type(document.get("base_image")) is str and
             re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._/:@-]*@sha256:[0-9a-f]{64}",
                          document["base_image"]) is not None, reason)
    _require(type(document.get("image_id")) is str and
             re.fullmatch(r"sha256:[0-9a-f]{64}", document["image_id"])
             is not None and type(document.get("container_id")) is str and
             re.fullmatch(r"[0-9a-f]{64}", document["container_id"]) is not None,
             reason)
    staged = _gate_input_records(document.get("staged_inputs"), reason)
    _require(
        set(staged) == set(NETWORK_GATE_SOURCE_MODES) and
        all(staged[path]["mode"] == mode
            for path, mode in NETWORK_GATE_SOURCE_MODES.items()),
        reason)
    inner = document.get("inner")
    _require(isinstance(inner, dict) and set(inner) == {
        "schema", "passed", "checks", "identities", "boundary"} and
        inner.get("schema") == "hepta.broker-network-opt-in-rootful.v3" and
        inner.get("passed") is True and
        inner.get("identities") == NETWORK_EXPECTED_IDENTITIES, reason)
    _checks(inner.get("checks"), reason, NETWORK_EXPECTED_CHECKS)
    expected_boundary = {
        "network_only": True, "inert_loopback_sentinels": True,
        "loopback_families": ["ipv4", "ipv6"], "real_broker_connections": 0,
        "broker_protocol_messages": 0, "ib_binaries": 0, "paper_units": 0,
        "credentials": 0, "default_engaged_kill_switch_fixtures": 2,
        "paper_orders": 0, "live_authorized": False,
    }
    _require(inner.get("boundary") == expected_boundary, reason)
    ready = () if (
        document.get("passed") is True and
        document.get("actual_rootful_container_run") is True and
        document.get("host_policy_applied") is False and
        document.get("host_services_started") is False and
        document.get("real_broker_connections") == 0 and
        document.get("paper_orders") == 0 and
        document.get("live_authorized") is False
    ) else ("NETWORK_GATE_NOT_PASS",)
    dangers: tuple[str, ...] = ()
    if document.get("real_broker_connections") not in (0, None) or \
            document.get("paper_orders") not in (0, None) or \
            document.get("live_authorized") is True:
        dangers = ("NETWORK_GATE_EXPOSURE_SIGNAL",)
    return Facts(status="PASS" if not ready else "FAIL", readiness=ready,
                 dangers=dangers)


def _hard_network_topology(run_id: str) -> dict[str, Any]:
    roles = ("broker", "execution", "gateway", "agent", "simulator")
    clients = ("execution", "gateway", "agent", "simulator")
    short = run_id[:10]
    namespaces = {role: f"hpn-{run_id}-{role}" for role in roles}
    slices = {role: f"heptahn{run_id}{role}.slice" for role in roles}
    units = {
        "sentinel": f"hepta-hn-{run_id}-sentinel.service",
        "execution_probe": f"hepta-hn-{run_id}-exec-probe.service",
        "execution_wrong_uid": f"hepta-hn-{run_id}-wrong-uid.service",
        "execution_wrong_cgroup": f"hepta-hn-{run_id}-wrong-cgroup.service",
        "gateway_probe": f"hepta-hn-{run_id}-gateway-probe.service",
        "agent_probe": f"hepta-hn-{run_id}-agent-probe.service",
        "simulator_probe": f"hepta-hn-{run_id}-sim-probe.service",
        "execution_anchor": f"hepta-hn-{run_id}-exec-anchor.service",
    }
    links: dict[str, dict[str, str]] = {}
    for index, role in enumerate(clients, start=1):
        links[role] = {
            "broker_interface": f"hb{short}{index}",
            "client_interface": f"hc{short}{index}",
            "broker_ipv4": f"198.18.{index}.1",
            "client_ipv4": f"198.18.{index}.2",
            "broker_ipv6": f"fd42:{short[:4]}:{index}::1",
            "client_ipv6": f"fd42:{short[:4]}:{index}::2",
        }
    return {
        "namespaces": namespaces, "slices": slices, "units": units,
        "uids": {
            "broker": 29001, "execution": 29002, "gateway": 29003,
            "agent": 29004, "simulator": 29005,
        },
        "links": links,
    }


def validate_hard_network_gate(document: dict[str, Any]) -> Facts:
    reason = "HARD_NETWORK_GATE_INVALID"
    _sealed(document, HARD_NETWORK_GATE_FIELDS, reason)
    _require(document.get("schema") ==
             "hepta.broker-network-hard-isolation-gate.v1", reason)
    run_id = _safe_token(document.get("run_id"), RUN_ID, reason)
    started, completed, expires = _gate_times(document, reason)
    _require(document.get("duration_ms") == completed - started, reason)
    decision = document.get("decision")
    passed = document.get("passed") is True
    rehearsal_passed = document.get("rehearsal_passed") is True
    _require(
        decision in {"GO", "NO_GO", "REHEARSAL_ONLY"} and
        type(document.get("passed")) is bool and
        type(document.get("rehearsal_passed")) is bool and
        document.get("certification_ready") is document.get("passed") and
        document.get("execution_mode") in {
            "NATIVE_PRODUCTION", "INJECTED_REHEARSAL"} and
        document.get("scope") ==
            "DEDICATED_BROKER_NETNS_HARD_CERTIFICATION_GATE" and
        all(document.get(field) is False for field in (
            "paper_test_admission_authorized", "paper_authorized",
            "live_authorized", "mutation_authorized", "direct_broker_access",
            "order_submission_authorized")), reason)

    lineage = document.get("lineage")
    _require(isinstance(lineage, dict) and set(lineage) == {
        "host_id", "boot_id", "source_commit", "source_manifest_sha256",
        "runner_sha256"} and type(lineage.get("host_id")) is str and
        bool(lineage["host_id"]) and type(lineage.get("boot_id")) is str and
        BOOT_ID.fullmatch(lineage["boot_id"]) is not None and
        type(lineage.get("source_commit")) is str and
        GIT_HEAD.fullmatch(lineage["source_commit"]) is not None and
        all(type(lineage.get(field)) is str and
            BARE_SHA256.fullmatch(lineage[field]) is not None
            for field in ("source_manifest_sha256", "runner_sha256")), reason)

    provenance = document.get("provenance")
    _require(isinstance(provenance, dict) and
             set(provenance) == {"host", "source", "base", "tooling"},
             reason)
    file_hashes: set[str] = set()
    body_hashes: set[str] = set()
    provenance_expiries: list[int] = []
    parent_fields = {
        "st_dev", "st_ino", "st_uid", "st_gid", "st_mode", "st_nlink"}
    for record in provenance.values():
        _require(isinstance(record, dict) and set(record) == {
            "path", "file_sha256", "body_sha256", "size", "issued_at_ms",
            "expires_at_ms", "device", "inode", "mode", "nlink", "uid",
            "gid", "mtime_ns", "ctime_ns", "parent_identity"}, reason)
        path = record.get("path")
        _require(type(path) is str and path.startswith("/") and
                 not path.startswith("//") and os.path.normpath(path) == path,
                 reason)
        file_sha = record.get("file_sha256")
        _require(type(file_sha) is str and
                 BARE_SHA256.fullmatch(file_sha) is not None, reason)
        body_sha = _digest(record.get("body_sha256"), reason)
        issued = _integer(record.get("issued_at_ms"), reason)
        record_expires = _integer(record.get("expires_at_ms"), reason)
        parent = record.get("parent_identity")
        _require(
            issued <= started and record_expires >= expires and
            issued < record_expires and
            record_expires - issued <= MAXIMUM_GATE_PROVENANCE_LIFETIME_MS and
            type(record.get("size")) is int and record["size"] >= 1 and
            type(record.get("device")) is int and record["device"] >= 0 and
            type(record.get("inode")) is int and record["inode"] >= 1 and
            record.get("mode") == "0600" and record.get("nlink") == 1 and
            type(record.get("uid")) is int and record["uid"] >= 0 and
            type(record.get("gid")) is int and record["gid"] >= 0 and
            type(record.get("mtime_ns")) is int and
            type(record.get("ctime_ns")) is int and
            isinstance(parent, dict) and set(parent) == parent_fields and
            all(type(item) is int for item in parent.values()), reason)
        if passed:
            _require(record["uid"] == 0 and record["gid"] == 0 and
                     parent["st_uid"] == 0 and parent["st_gid"] == 0, reason)
        file_hashes.add(file_sha)
        body_hashes.add(body_sha)
        provenance_expiries.append(record_expires)
    _require(len(file_hashes) == 4 and len(body_hashes) == 4, reason)
    if not passed:
        _require(min(provenance_expiries) == expires, reason)

    environment = document.get("environment")
    _require(isinstance(environment, dict) and set(environment) == {
        "boot_id", "cgroup_filesystem", "source_commit", "virtualization",
        "source_manifest_sha256", "initial_listener_inventory_sha256",
        "initial_netns_inventory_sha256",
        "initial_firewall_semantic_sha256"}, reason)
    if rehearsal_passed:
        _require(
            environment.get("boot_id") == lineage["boot_id"] and
            environment.get("source_commit") == lineage["source_commit"] and
            environment.get("source_manifest_sha256") ==
                lineage["source_manifest_sha256"] and
            environment.get("cgroup_filesystem") == "cgroup2" and
            environment.get("virtualization") in {"kvm", "qemu", "vmware"}
            and all(type(environment.get(field)) is str and
                    BARE_SHA256.fullmatch(environment[field]) is not None
                    for field in (
                        "initial_listener_inventory_sha256",
                        "initial_netns_inventory_sha256",
                        "initial_firewall_semantic_sha256")), reason)
    _require(document.get("topology") == _hard_network_topology(run_id), reason)

    phases = document.get("phases")
    _require(isinstance(phases, list) and all(
        isinstance(item, dict) and set(item) == {
            "sequence", "name", "detail", "kill_switch_state"}
        for item in phases) and
        [item["sequence"] for item in phases] ==
            list(range(1, len(phases) + 1)) and
        all(item["kill_switch_state"] in {
            "not-created", "engaged", "engaged-finally-then-removed"}
            for item in phases), reason)
    if rehearsal_passed:
        _require(
            [item["name"] for item in phases] == [
                "preflight", "setup", "fault-and-revocation-drills",
                "final-deny-all", "cleanup"] and
            [item["kill_switch_state"] for item in phases] == [
                "not-created", "engaged", "engaged", "engaged",
                "engaged-finally-then-removed"], reason)

    checks = document.get("checks")
    _require(isinstance(checks, dict) and
             set(checks) == HARD_NETWORK_EXPECTED_CHECKS and
             all(type(value) is bool for value in checks.values()), reason)
    exposure = document.get("exposure")
    _require(isinstance(exposure, dict) and set(exposure) == {
        "host_listener_allowlist_count", "reachable_forwarders",
        "ib_binaries", "broker_credentials", "broker_protocol_messages",
        "orders", "command_transcript_sha256", "command_count",
        "kill_switch"} and
        all(exposure.get(field) == 0 for field in (
            "reachable_forwarders", "ib_binaries", "broker_credentials",
            "broker_protocol_messages", "orders")) and
        type(exposure.get("host_listener_allowlist_count")) is int and
        exposure["host_listener_allowlist_count"] >= 0 and
        type(exposure.get("command_count")) is int and
        exposure["command_count"] >= 0 and
        type(exposure.get("command_transcript_sha256")) is str and
        BARE_SHA256.fullmatch(exposure["command_transcript_sha256"])
            is not None, reason)
    if rehearsal_passed:
        kill_switch = exposure.get("kill_switch")
        _require(isinstance(kill_switch, dict) and set(kill_switch) == {
            "state", "sha256", "device", "inode", "mode",
            "parent_identity"} and kill_switch.get("state") == "engaged" and
            kill_switch.get("mode") == "0400" and
            type(kill_switch.get("sha256")) is str and
            BARE_SHA256.fullmatch(kill_switch["sha256"]) is not None and
            type(kill_switch.get("device")) is int and
            type(kill_switch.get("inode")) is int and
            isinstance(kill_switch.get("parent_identity"), dict) and
            set(kill_switch["parent_identity"]) == parent_fields and
            all(type(item) is int
                for item in kill_switch["parent_identity"].values()), reason)

    cleanup = document.get("cleanup")
    _require(isinstance(cleanup, dict) and set(cleanup) == {
        "attempted", "complete", "firewall_reload_attempted",
        "firewall_restored", "residue"} and
        all(type(cleanup.get(field)) is bool for field in (
            "attempted", "complete", "firewall_reload_attempted",
            "firewall_restored")) and
        isinstance(cleanup.get("residue"), list) and
        all(type(item) is str for item in cleanup["residue"]), reason)
    if rehearsal_passed:
        _require(cleanup.get("attempted") is True and
                 cleanup.get("complete") is True and
                 cleanup.get("firewall_restored") is True and
                 cleanup.get("residue") == [], reason)
    failure = document.get("failure")
    _require(failure is None or
             (type(failure) is str and bool(failure) and len(failure) <= 2048),
             reason)
    semantic_pass = all(checks.values()) and failure is None
    _require(
        rehearsal_passed is semantic_pass and
        (not passed or semantic_pass) and
        (not passed or document.get("execution_mode") == "NATIVE_PRODUCTION")
        and not (document.get("execution_mode") == "INJECTED_REHEARSAL" and
                 passed) and
        decision == (
            "GO" if passed else "REHEARSAL_ONLY" if semantic_pass else
            "NO_GO"), reason)
    _require(document.get("boundary") == {
        "native_disposable_host": True,
        "dedicated_network_namespaces": 5,
        "dedicated_cgroup_v2_slices": 5,
        "protected_ports": [4001, 4002, 7496, 7497],
        "inert_ipv4_sentinel_listeners": 16,
        "inert_ipv6_sentinel_listeners": 16,
        "controlled_positive_target": "inert-sentinel-only",
        "kill_switch_state": "engaged",
        "host_firewall_flush_reload_drill": True,
        "forwarder_inventory": "exact-zero-or-reviewed-allowlist",
        "real_ib_binaries": 0, "real_broker_credentials": 0,
        "broker_protocol_messages": 0, "orders": 0,
        "paper_authorized": False, "live_authorized": False,
        "mutation_authorized": False, "direct_broker_access": False,
    }, reason)
    if passed:
        review = _environment_review_record(
            document.get("environment_review_closure"), reason,
            at_ms=completed)
        _require(
            review["source_commit"] == lineage["source_commit"] and
            expires == min(
                min(provenance_expiries), review["expires_at_ms"]), reason)
    else:
        _require(document.get("environment_review_closure") is None, reason)
    readiness = () if passed else ("HARD_NETWORK_GATE_NOT_PASS",)
    return Facts(issued_at_ms=completed, expires_at_ms=expires,
                 status="PASS" if passed else "FAIL", readiness=readiness)


def validate_native_gate(document: dict[str, Any]) -> Facts:
    reason = "NATIVE_GATE_INVALID"
    if document.get("schema") == "hepta.execution-native-systemd-aggregate.v6" \
            and document.get("passed") is False:
        dangers: list[str] = []
        boundary = document.get("boundary")
        if isinstance(boundary, dict):
            if boundary.get("paper_authorized") is True or \
                    boundary.get("live_enabled") is True:
                dangers.append("NATIVE_GATE_AUTHORITY_SIGNAL")
            if boundary.get("real_broker_connections") not in (0, None) or \
                    boundary.get("paper_orders") not in (0, None):
                dangers.append("NATIVE_GATE_EXPOSURE_SIGNAL")
        return Facts(status="FAIL", readiness=("NATIVE_GATE_NOT_PASS",),
                     dangers=tuple(dangers))
    _require(set(document) == NATIVE_GATE_FIELDS and
             document.get("schema") ==
                "hepta.execution-native-systemd-aggregate.v6" and
             document.get("passed") is True and
             document.get("certification_level") ==
                "native-disposable-vm-agent-os-watch-runtime-rootful-systemd",
             reason)
    variants = document.get("variants")
    variant_fields = {
        "vm_type", "kernel_release", "vm_image_manifest_sha256",
        "provisioning_manifest_sha256", "machine_id_sha256", "boot_id_sha256",
        "run_id_sha256", "instance_uuid", "instance_challenge_sha256",
        "instance_provisioner_id", "instance_hypervisor_id",
        "instance_receipt_file_sha256", "instance_receipt_body_sha256",
        "instance_receipt_issued_at_ms", "instance_receipt_expires_at_ms",
        "agent_os_installation_manifest_sha256",
        "agent_os_runtime_input_manifest_sha256",
        "agent_os_runtime_input_records_sha256", "agent_os_runtime_result_sha256",
        "agent_os_runtime_lifecycle_sha256", "agent_os_runtime_watch_generation",
        "agent_os_runtime_preflight_executed", "agent_os_watch_session_revoked",
        "agent_os_runtime_cleanup_complete", "executed_kind",
        "executed_ib_path_sha256",
    }
    _require(isinstance(variants, dict) and
             set(variants) == {"real", "sandbox", "stub"}, reason)
    distinct: dict[str, set[str]] = {
        field: set() for field in (
            "vm_image_manifest_sha256", "machine_id_sha256", "boot_id_sha256",
            "run_id_sha256", "instance_uuid", "instance_challenge_sha256")}
    for variant, record in variants.items():
        _require(isinstance(record, dict) and set(record) == variant_fields,
                 reason)
        for field in variant_fields & {
            "vm_image_manifest_sha256", "provisioning_manifest_sha256",
            "machine_id_sha256", "boot_id_sha256", "run_id_sha256",
            "instance_challenge_sha256", "instance_receipt_file_sha256",
            "instance_receipt_body_sha256",
            "agent_os_installation_manifest_sha256",
            "agent_os_runtime_input_manifest_sha256",
            "agent_os_runtime_input_records_sha256",
            "agent_os_runtime_result_sha256", "agent_os_runtime_lifecycle_sha256",
            "executed_ib_path_sha256"}:
            _bare_digest(record.get(field), reason)
        _require(
            type(record.get("vm_type")) is str and bool(record["vm_type"]) and
            type(record.get("kernel_release")) is str and
            bool(record["kernel_release"]) and
            re.fullmatch(
                r"[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-"
                r"[89ab][0-9a-f]{3}-[0-9a-f]{12}",
                str(record.get("instance_uuid", ""))) is not None and
            all(re.fullmatch(
                r"[A-Za-z0-9][A-Za-z0-9._:/@+\-]{0,127}",
                str(record.get(field, ""))) is not None for field in (
                    "instance_provisioner_id", "instance_hypervisor_id")) and
            type(record.get("instance_receipt_issued_at_ms")) is int and
            type(record.get("instance_receipt_expires_at_ms")) is int and
            record["instance_receipt_issued_at_ms"] > 0 and
            record["instance_receipt_expires_at_ms"] >
                record["instance_receipt_issued_at_ms"] and
            type(record.get("agent_os_runtime_watch_generation")) is int and
            record["agent_os_runtime_watch_generation"] > 0 and
            all(record.get(field) is True for field in (
                "agent_os_runtime_preflight_executed",
                "agent_os_watch_session_revoked",
                "agent_os_runtime_cleanup_complete")), reason)
        for field in distinct:
            distinct[field].add(record[field])
    _require(all(len(values) == 3 for values in distinct.values()), reason)
    _require(
        max(record["instance_receipt_issued_at_ms"]
            for record in variants.values()) <
        min(record["instance_receipt_expires_at_ms"]
            for record in variants.values()), reason)
    common = document.get("common_closure")
    common_fields = {
        "platform_policy_sha256", "clean_source_bundle_sha256",
        "clean_source_manifest_sha256", "clean_source_files_sha256",
        "simulator_sha256", "client_probe_sha256", "formal_ibapi_sha256",
        "agent_os_installation_manifest_sha256",
        "agent_os_installation_file_count", "agent_os_gateway_sha256",
        "agent_os_sessionctl_sha256", "agent_os_mcp_server_sha256",
        "agent_os_runtime_input_manifest_sha256",
        "agent_os_runtime_input_content_sha256",
        "agent_os_runtime_inner_gate_sha256", "agent_os_runtime_input_file_count",
        "agent_os_fixed_identities", "agent_os_watch_tools",
        "agent_os_read_probes", "all_agent_os_runtime_preflights_executed",
        "all_agent_os_watch_sessions_revoked",
        "all_agent_os_runtime_cleanup_complete", "distinct_native_vms",
        "distinct_provisioner_attested_instances",
        "external_instance_receipts_verified",
        "instance_receipt_validity_windows_overlap",
        "all_networks_loopback_only", "all_inputs_stable",
    }
    _require(isinstance(common, dict) and set(common) == common_fields, reason)
    for field in common_fields & {
        "platform_policy_sha256", "clean_source_bundle_sha256",
        "clean_source_manifest_sha256", "clean_source_files_sha256",
        "simulator_sha256", "client_probe_sha256", "formal_ibapi_sha256",
        "agent_os_installation_manifest_sha256", "agent_os_gateway_sha256",
        "agent_os_sessionctl_sha256", "agent_os_mcp_server_sha256",
        "agent_os_runtime_input_manifest_sha256",
        "agent_os_runtime_input_content_sha256",
        "agent_os_runtime_inner_gate_sha256"}:
        _bare_digest(common.get(field), reason)
    _require(
        common.get("distinct_native_vms") == 3 and
        common.get("distinct_provisioner_attested_instances") == 3 and
        all(common.get(field) is True for field in (
            "external_instance_receipts_verified",
            "instance_receipt_validity_windows_overlap",
            "all_agent_os_runtime_preflights_executed",
            "all_agent_os_watch_sessions_revoked",
            "all_agent_os_runtime_cleanup_complete", "all_networks_loopback_only",
            "all_inputs_stable")), reason)
    inputs = document.get("aggregation_inputs")
    _require(isinstance(inputs, list) and len(inputs) == 3, reason)
    for expected, record in zip(("real", "sandbox", "stub"), inputs,
                                strict=True):
        _require(isinstance(record, dict) and set(record) == {
            "variant", "path", "sha256", "size", "mode"} and
            record.get("variant") == expected and record.get("mode") == "0600",
            reason)
        _canonical_path(Path(record.get("path", "")), reason)
        _bare_digest(record.get("sha256"), reason)
        _integer(record.get("size"), reason, 1)
    boundary = document.get("boundary")
    expected_boundary_fields = {
        "real_ibapi_elf_executed", "real_broker_connections", "paper_orders",
        "live_enabled", "paper_authorized",
        "native_agent_os_installation_gate_satisfied",
        "native_agent_os_runtime_gate_satisfied",
        "agent_os_runtime_preflight_executed",
        "agent_os_runtime_preflight_required",
        "agent_os_runtime_evidence_fabricated", "agent_os_runtime_source",
        "ib_adapter_visible_during_agent_os_runtime", "paper_certification",
    }
    _require(isinstance(boundary, dict) and
             set(boundary) == expected_boundary_fields and
             boundary.get("real_broker_connections") == 0 and
             boundary.get("paper_orders") == 0 and
             all(boundary.get(field) is False for field in (
                "real_ibapi_elf_executed", "live_enabled", "paper_authorized",
                "agent_os_runtime_evidence_fabricated",
                "ib_adapter_visible_during_agent_os_runtime")) and
             all(boundary.get(field) is True for field in (
                "native_agent_os_installation_gate_satisfied",
                "native_agent_os_runtime_gate_satisfied",
                "agent_os_runtime_preflight_executed",
                "agent_os_runtime_preflight_required")), reason)
    _require(
        boundary.get("agent_os_runtime_source") ==
            "three-distinct-externally-attested-native-vms" and
        boundary.get("paper_certification") ==
            "requires_separate_explicit_authorization", reason)
    # ``clean_source_files_sha256`` is the full strict-bundle file closure,
    # not the frozen security-manifest identity used by the common source
    # lineage.  Its equality is checked explicitly against the release
    # closure in ``_cross_validate``; placing it in ``Facts.source`` would
    # conflate the two digest domains and reject every real bundle.
    return Facts(status="PASS")


def _reverify_native_gate_evidence(document: dict[str, Any]) -> None:
    """Securely rebuild the aggregate from raw reports and signed receipts."""

    reason = "NATIVE_GATE_CAUSAL_EVIDENCE_INVALID"
    try:
        import aggregate_hepta_execution_native_systemd_gate as native_aggregate

        reconstructed = native_aggregate.verify_runtime_aggregate(document)
    except Exception as error:
        raise AdmissionError(reason) from error
    _require(reconstructed == document, reason)


def _handoff_profile_record(
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


def _secure_handoff_read(
    path: Path, reason: str, **arguments: Any,
) -> tuple[bytes, os.stat_result]:
    try:
        return secure_read(path, **arguments)
    except AdmissionError as error:
        raise AdmissionError(reason) from error


def _read_handoff_profile_file(
    path: Path, reason: str, *, expected_uid: int, expected_gid: int,
    mode: int, sha256: str, size: int,
) -> dict[str, Any]:
    payload, metadata = _secure_handoff_read(
        path, reason,
        expected_uid=expected_uid, modes=frozenset({mode}),
        maximum=max(size, 1), minimum=size)
    record = _handoff_profile_record(path, payload, metadata)
    _require(
        record["file_sha256"] == sha256 and record["bytes"] == size and
        record["mode"] == stat.S_IFREG | mode and
        record["uid"] == expected_uid and record["gid"] == expected_gid and
        record["nlink"] == 1, reason)
    return record


def _validate_handoff_profile_record(
    value: Any, actual: Mapping[str, Any], reason: str, *, sealed: bool = False,
) -> None:
    fields = (HANDOFF_PROFILE_SEALED_FILE_FIELDS if sealed else
              HANDOFF_PROFILE_FILE_FIELDS)
    _require(isinstance(value, dict) and set(value) == fields, reason)
    for field in HANDOFF_PROFILE_FILE_FIELDS:
        _require(value.get(field) == actual.get(field), reason)
    if sealed:
        _require(_digest(value.get("body_sha256"), reason) ==
                 actual.get("body_sha256"), reason)


def _read_handoff_sealed_profile_document(
    path: Path, evidence: Any, reason: str, *, expected_uid: int,
    expected_gid: int, fields: frozenset[str], schema: str, version: int,
    status: str,
) -> dict[str, Any]:
    payload, metadata = _secure_handoff_read(
        path, reason,
        expected_uid=expected_uid, modes=frozenset({0o600}))
    document = strict_object(payload, reason)
    _require(payload == canonical_bytes(document), reason)
    _sealed(document, fields, reason)
    _require(
        document.get("schema") == schema and document.get("version") == version
        and document.get("status") == status and
        document.get("round") == ROUND and document.get("domain") == "alpha",
        reason)
    actual = {**_handoff_profile_record(path, payload, metadata),
              "body_sha256": document["body_sha256"]}
    _validate_handoff_profile_record(evidence, actual, reason, sealed=True)
    _require(actual["gid"] == expected_gid, reason)
    return document


def _validate_handoff_legacy_profile_record(
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


def _require_handoff_profile_candidate_absent(
    path: Path, reason: str, *, expected_uid: int,
) -> None:
    parent = _open_anchored_directory(path.parent, reason)
    try:
        parent_identity = _trusted_directory_identity(
            parent, expected_uid=expected_uid, reason=reason)
        try:
            os.stat(path.name, dir_fd=parent, follow_symlinks=False)
        except FileNotFoundError:
            pass
        else:
            raise AdmissionError(reason)
        _require(parent_identity == _trusted_directory_identity(
            parent, expected_uid=expected_uid, reason=reason), reason)
    except OSError as error:
        raise AdmissionError(reason) from error
    finally:
        os.close(parent)


def _validate_watch_handoff_runtime_profile_binding(
    document: Mapping[str, Any], *, expected_uid: int,
) -> None:
    reason = "WATCH_HANDOFF_RUNTIME_PROFILE_HARDENING_INVALID"
    expected_gid = ROOT_GID if expected_uid == ROOT_UID else os.getegid()
    value = document.get("paper_runtime_profile_hardening")
    _require(
        document.get("paper_runtime_profile_hardened") is True and
        document.get("paper_runtime_profile_candidate_absent") is True and
        isinstance(value, dict) and
        set(value) == HANDOFF_RUNTIME_PROFILE_HARDENING_FIELDS and
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
    for field in (
        "harden_intent_record_sha256", "harden_exchange_record_sha256",
    ):
        _require(
            _digest(value.get(field), reason) != "sha256:" + "0" * 64,
            reason)
    target = _read_handoff_profile_file(
        PAPER_RUNTIME_PROFILE_PATH, reason, expected_uid=expected_uid,
        expected_gid=expected_gid, mode=0o644,
        sha256=PAPER_RUNTIME_PROFILE_HARDENED_SHA256,
        size=PAPER_RUNTIME_PROFILE_HARDENED_BYTES)
    backup = _read_handoff_profile_file(
        PAPER_RUNTIME_PROFILE_BACKUP_PATH, reason,
        expected_uid=expected_uid, expected_gid=expected_gid, mode=0o600,
        sha256=PAPER_RUNTIME_PROFILE_LEGACY_SHA256,
        size=PAPER_RUNTIME_PROFILE_LEGACY_BYTES)
    retained = _read_handoff_profile_file(
        PAPER_RUNTIME_PROFILE_RETAINED_PATH, reason,
        expected_uid=expected_uid, expected_gid=expected_gid, mode=0o600,
        sha256=PAPER_RUNTIME_PROFILE_LEGACY_SHA256,
        size=PAPER_RUNTIME_PROFILE_LEGACY_BYTES)
    _validate_handoff_profile_record(value.get("target"), target, reason)
    _validate_handoff_profile_record(
        value.get("legacy_backup"), backup, reason)
    _validate_handoff_profile_record(
        value.get("retained_legacy"), retained, reason)
    _require_handoff_profile_candidate_absent(
        PAPER_RUNTIME_PROFILE_CANDIDATE_PATH, reason,
        expected_uid=expected_uid)


def _validate_watch_handoff_profile_binding(
    document: Mapping[str, Any], *, expected_uid: int,
) -> None:
    reason = "WATCH_HANDOFF_PROFILE_RESTORATION_INVALID"
    expected_gid = ROOT_GID if expected_uid == ROOT_UID else os.getegid()
    value = document.get("paper_profile_restoration")
    _require(isinstance(value, dict) and
             set(value) == HANDOFF_PROFILE_RESTORATION_FIELDS, reason)
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
    _digest(value.get("restore_intent_record_sha256"), reason)
    _digest(value.get("restore_exchange_record_sha256"), reason)
    target = _read_handoff_profile_file(
        PAPER_PROFILE_PATH, reason, expected_uid=expected_uid,
        expected_gid=expected_gid, mode=0o644,
        sha256=PAPER_PROFILE_DORMANT_SHA256,
        size=PAPER_PROFILE_DORMANT_BYTES)
    backup = _read_handoff_profile_file(
        PAPER_PROFILE_DORMANT_BACKUP_PATH, reason, expected_uid=expected_uid,
        expected_gid=expected_gid, mode=0o600,
        sha256=PAPER_PROFILE_DORMANT_SHA256,
        size=PAPER_PROFILE_DORMANT_BYTES)
    retained = _read_handoff_profile_file(
        PAPER_PROFILE_FORWARD_RETAINED_PATH, reason,
        expected_uid=expected_uid, expected_gid=expected_gid, mode=0o600,
        sha256=PAPER_PROFILE_DORMANT_SHA256,
        size=PAPER_PROFILE_DORMANT_BYTES)
    retired = _read_handoff_profile_file(
        PAPER_PROFILE_RETIRED_WATCH_PATH, reason, expected_uid=expected_uid,
        expected_gid=expected_gid, mode=0o600,
        sha256=PAPER_PROFILE_WATCH_SHA256, size=PAPER_PROFILE_WATCH_BYTES)
    _validate_handoff_profile_record(value.get("target"), target, reason)
    _validate_handoff_profile_record(value.get("dormant_backup"), backup,
                                     reason)
    _validate_handoff_profile_record(
        value.get("forward_retained_dormant"), retained, reason)
    _validate_handoff_profile_record(value.get("retired_watch"), retired,
                                     reason)
    transition = _read_handoff_sealed_profile_document(
        PAPER_PROFILE_FORWARD_TRANSITION_RECEIPT_PATH,
        value.get("forward_transition_receipt"), reason,
        expected_uid=expected_uid, expected_gid=expected_gid,
        fields=HANDOFF_PROFILE_TRANSITION_FIELDS,
        schema=PROFILE_TRANSITION_SCHEMA, version=2,
        status=PROFILE_TRANSITION_STATUS)
    deployment = _read_handoff_sealed_profile_document(
        PAPER_PROFILE_DEPLOYMENT_RECEIPT_PATH,
        value.get("profile_deployment_receipt"), reason,
        expected_uid=expected_uid, expected_gid=expected_gid,
        fields=PROFILE_RECEIPT_FIELDS, schema=PROFILE_DEPLOYMENT_SCHEMA,
        version=8, status=PROFILE_DEPLOYMENT_STATUS)
    preimage = _read_handoff_sealed_profile_document(
        PAPER_PROFILE_FORWARD_PREIMAGE_PATH,
        value.get("forward_preimage_evidence"), reason,
        expected_uid=expected_uid, expected_gid=expected_gid,
        fields=HANDOFF_PROFILE_PREIMAGE_FIELDS, schema=PROFILE_PREIMAGE_SCHEMA,
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
    for referenced in (transition, preimage):
        _validate_handoff_legacy_profile_record(
            referenced.get("backup"), reason,
            path=PAPER_PROFILE_DORMANT_BACKUP_PATH,
            sha256=PAPER_PROFILE_DORMANT_SHA256,
            size=PAPER_PROFILE_DORMANT_BYTES, mode=0o600,
            expected_uid=expected_uid, expected_gid=expected_gid)
    _require_handoff_profile_candidate_absent(
        PAPER_PROFILE_CANDIDATE_PATH, reason, expected_uid=expected_uid)
    _validate_watch_handoff_runtime_profile_binding(
        document, expected_uid=expected_uid)
    identity_payload, identity_metadata = _secure_handoff_read(
        IDENTITY_MANIFEST_PATH, reason,
        expected_uid=expected_uid,
        modes=frozenset({0o600}), maximum=64 * 1024)
    _require(
        digest_bytes(identity_payload) == DISABLED_IDENTITY_MANIFEST_SHA256 and
        identity_metadata.st_gid == expected_gid and
        document.get("identity_manifest_sha256") ==
            DISABLED_IDENTITY_MANIFEST_SHA256 and
        document.get("identity_count") == 0 and
        document.get("paper_profile_restored") is True and
        document.get("profile_candidate_absent") is True and
        document.get("paper_runtime_profile_hardened") is True and
        document.get("paper_runtime_profile_candidate_absent") is True and
        document.get("global_kill_switch_engaged") is True, reason)
    for path, gid in ((KILL_SWITCH_PATH, PAPER_CONTROL_GID),
                      (GLOBAL_KILL_SWITCH_PATH, GLOBAL_PAPER_CONTROL_GID)):
        payload, metadata = _secure_handoff_read(
            path, reason,
            expected_uid=expected_uid, modes=frozenset({0o440}),
            maximum=7, minimum=7)
        _require(payload == b"engaged" and metadata.st_gid == gid, reason)


def validate_watch_handoff(document: dict[str, Any]) -> Facts:
    reason = "WATCH_HANDOFF_RECEIPT_INVALID"
    _sealed(document, WATCH_HANDOFF_FIELDS, reason)
    _require(
        document.get("schema") == WATCH_HANDOFF_SCHEMA and
        document.get("version") == WATCH_HANDOFF_VERSION and
        document.get("round") == ROUND and
        document.get("status") in {
            "WATCH_RETIRED_HANDOFF_COMPLETE", "FAILED_CLOSED"},
        reason)
    source = _digest(document.get("source_baseline_sha256"), reason)
    domain = _safe_token(document.get("domain"), DOMAIN, reason)
    campaign = _safe_token(document.get("campaign_id"), CAMPAIGN, reason)
    issued, expires = _times(document, "WATCH_HANDOFF_RECEIPT")
    producer = document.get("producer")
    _require(
        isinstance(producer, dict) and
        set(producer) == WATCH_HANDOFF_PRODUCER_FIELDS and
        producer.get("path") == str(WATCH_HANDOFF_PRODUCER_PATH) and
        _digest(producer.get("file_sha256"), reason) !=
            "sha256:" + "0" * 64 and
        document.get("production_mode") == "PRODUCTION_ROOT_SYSTEMD",
        reason)
    _reference(document.get("activation_receipt"), reason)
    _reference(document.get("p1_audit_receipt"), reason)
    _reference(document.get("freeze_bundle"), reason)
    for field in ("watch_authority_count", "watch_socket_count",
                  "watch_timer_count", "cleanup_residue_count",
                  "identity_count"):
        _integer(document.get(field), reason)
    _digest(document.get("identity_manifest_sha256"), reason)
    _require(
        type(document.get("paper_profile_restored")) is bool and
        type(document.get("profile_candidate_absent")) is bool and
        type(document.get("paper_runtime_profile_hardened")) is bool and
        type(document.get("paper_runtime_profile_candidate_absent")) is bool
        and type(document.get("global_kill_switch_engaged")) is bool,
        reason)
    ready = (
        document.get("status") == "WATCH_RETIRED_HANDOFF_COMPLETE" and
        document.get("watch_units_inactive") is True and
        document.get("watch_authority_count") == 0 and
        document.get("watch_socket_count") == 0 and
        document.get("watch_timer_count") == 0 and
        document.get("paper_units_inactive") is True and
        document.get("broker_deny_all") is True and
        document.get("kill_switch_engaged") is True and
        document.get("global_kill_switch_engaged") is True and
        document.get("identity_count") == 0 and
        document.get("identity_manifest_sha256") ==
            DISABLED_IDENTITY_MANIFEST_SHA256 and
        document.get("paper_profile_restored") is True and
        document.get("profile_candidate_absent") is True and
        document.get("paper_runtime_profile_hardened") is True and
        document.get("paper_runtime_profile_candidate_absent") is True and
        document.get("crash_recovery_verified") is True and
        document.get("cleanup_residue_count") == 0
    )
    readiness = () if ready else ("WATCH_HANDOFF_NOT_COMPLETE",)
    dangers = _boundary_findings(document, "WATCH_HANDOFF_RECEIPT")
    return Facts(source, domain, campaign, issued, expires,
                 str(document.get("status")), readiness, dangers)


def validate_zero_exposure(document: dict[str, Any]) -> Facts:
    reason = "ZERO_EXPOSURE_RECEIPT_INVALID"
    _sealed(document, ZERO_EXPOSURE_FIELDS, reason)
    _require(
        document.get("schema") ==
            "hepta.p1-paper-deny-all-zero-exposure-receipt.v1" and
        document.get("version") == 1 and document.get("round") == ROUND and
        document.get("status") in {"PASS", "NO_GO", "HALT"} and
        document.get("production_mode") ==
            "PRODUCTION_ROOT_OFFLINE_SIGNED_ACCOUNT_ATTESTOR" and
        document.get("snapshot_production_mode") ==
            ZERO_SNAPSHOT_PRODUCTION_MODE,
        reason)
    source = _digest(document.get("source_baseline_sha256"), reason)
    domain = _safe_token(document.get("domain"), DOMAIN, reason)
    campaign = _safe_token(document.get("campaign_id"), CAMPAIGN, reason)
    _safe_token(document.get("intent_id"), CAMPAIGN, reason)
    observed = _integer(document.get("observed_at_ms"), reason)
    expires = _integer(document.get("expires_at_ms"), reason)
    _require(observed < expires, reason)
    producer = _executable_reference(document.get("producer"), reason)
    snapshot_producer = _executable_reference(
        document.get("snapshot_producer"), reason)
    broker_helper = _executable_reference(
        document.get("broker_policy_helper"), reason)
    _require(
        producer["path"] == str(ZERO_EXPOSURE_ATTESTOR_EXECUTABLE) and
        snapshot_producer["path"] ==
            str(ZERO_SNAPSHOT_PRODUCER_EXECUTABLE) and
        broker_helper["path"] == str(BROKER_POLICY_HELPER_EXECUTABLE),
        reason)
    for field in (
        "operator_intent_reference", "watch_handoff_receipt",
        "challenge_reference", "broker_boundary_reference",
        "authoritative_state_reference",
    ):
        _reference(document.get(field), reason)
    reservation = _reservation_reference(
        document.get("host_authority_reservation"), reason)
    reservation_id = _safe_token(
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
            HOST_AUTHORITY_DIRECTORY /
            f"finalized.{reservation_id}.v1.json") and
        document.get("reservation_finalization_current_pointer_path") == str(
            HOST_AUTHORITY_DIRECTORY / "finalization-current.v1.json") and
        document.get("reservation_finalization_schema") ==
            RESERVATION_FINALIZATION_SCHEMA and
        document.get("reservation_finalization_order") ==
            RESERVATION_FINALIZATION_ORDER,
        reason)
    reservation_boot_id = _safe_token(
        document.get("reservation_boot_id"), BOOT_ID, reason)
    for field in ("reservation_lease_device", "reservation_lease_inode"):
        _require(_integer(document.get(field), reason) > 0, reason)
    _signed_reference(document.get("signed_evidence_reference"), reason)
    _signature_attestation(document.get("signature_verification"), reason)
    _safe_token(document.get("request_nonce"), NONCE, reason)
    for field in (
        "account_id_sha256", "provider_request_id_sha256",
        "provider_response_sha256", "snapshot_sha256", "policy_sha256",
    ):
        _digest(document.get(field), reason)
    for field in (
        "provider_id", "query_epoch", "query_invocation_id",
        "broker_observer_id", "account_observer_id",
    ):
        _safe_token(document.get(field), CAMPAIGN, reason)
    _integer(document.get("query_fencing_generation"), reason, minimum=1)
    _require(
        document.get("observation_method") ==
            "FIXED_LOCAL_READ_ONLY_SYSTEMD_PROC_BROKER_POLICY" and
        document.get("broker_observer_id") ==
            "hepta-p1-zero-exposure-local-boundary-v2" and
        document.get("account_observer_id") ==
            "hepta-p1-zero-exposure-signed-adapter-v2" and
        document.get("observation_authority") ==
            "INDEPENDENT_REMOTE_READ_ONLY_ACCOUNT" and
        document.get("query_effect") == "READ_ONLY" and
        document.get("protected_broker_ports") == [4001, 4002, 7496, 7497],
        reason)
    for field in ("authorized_connectors", "broker_socket_count",
                  "broker_process_count", "credential_exposure_count",
                  "order_count", "position_count",
                  "gross_absolute_position"):
        _integer(document.get(field), reason)
    uids = document.get("authorized_uids")
    _require(
        isinstance(uids, list) and len(uids) <= 1024 and
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
    ):
        _require(type(document.get(field)) is bool, reason)
    lease = _historical_host_authority_lease(
        document.get("host_authority_lease"), reason)
    _require(
        reservation_boot_id == lease["boot_id"] and
        document["reservation_lease_device"] == lease["lease_device"] and
        document["reservation_lease_inode"] == lease["lease_inode"] and
        reservation["device"] > 0 and reservation["inode"] > 0,
        reason)
    ready = (
        document.get("status") == "PASS" and
        document.get("read_only_authority") is True and
        document.get("authoritative") is True and
        document.get("account_complete") is True and
        document.get("observation_complete") is True and
        document.get("broker_deny_all") is True and
        document.get("end_flat") is True and
        document.get("authorized_connectors") == 0 and
        uids == [] and
        document.get("broker_socket_count") == 0 and
        document.get("broker_process_count") == 0 and
        document.get("credential_exposure_count") == 0 and
        document.get("order_count") == 0 and
        document.get("position_count") == 0 and
        document.get("gross_absolute_position") == 0 and
        document.get("paper_units_inactive") is True and
        document.get("kill_switch_engaged") is True and
        document.get("process_inventory_complete") is True and
        document.get("socket_inventory_complete") is True and
        document.get("credential_inventory_complete") is True and
        document.get("host_authority_lease_reacquired") is True and
        document.get("reservation_continuity_verified") is True and
        document.get("reservation_finalization_tombstone_absent") is True
    )
    readiness = () if ready else ("ZERO_EXPOSURE_NOT_PASS",)
    dangers = list(_boundary_findings(document, "ZERO_EXPOSURE_RECEIPT"))
    if (
        document.get("authorized_connectors", 0) > 0 or
        document.get("authorized_uids") not in ([], None) or
        document.get("broker_socket_count", 0) > 0 or
        document.get("broker_process_count", 0) > 0 or
        document.get("credential_exposure_count", 0) > 0 or
        document.get("order_count", 0) > 0 or
        document.get("position_count", 0) > 0 or
        document.get("gross_absolute_position", 0) > 0 or
        document.get("end_flat") is False or
        document.get("broker_deny_all") is False or
        document.get("paper_units_inactive") is False or
        document.get("kill_switch_engaged") is False or
        document.get("reservation_continuity_verified") is False or
        document.get("reservation_finalization_tombstone_absent") is False
    ):
        dangers.append("ZERO_EXPOSURE_DANGEROUS_SIGNAL")
    if document.get("status") == "HALT":
        dangers.append("ZERO_EXPOSURE_UPSTREAM_HALT")
    return Facts(source, domain, campaign, observed, expires,
                 str(document.get("status")), readiness, tuple(dangers))


VALIDATORS: Mapping[str, Callable[[dict[str, Any]], Facts]] = {
    "source_baseline": validate_source_baseline,
    "install_manifest": validate_install_manifest,
    "install_receipt": validate_install_receipt,
    "install_pointer": validate_install_pointer,
    "profile_receipt": validate_profile_receipt,
    "activation_receipt": validate_activation_receipt,
    "p1_audit_receipt": validate_p1_audit,
    "release_validation_receipt": validate_release_validation,
    "agent_os_rootful_gate_receipt": validate_agent_os_rootful_gate,
    "dual_domain_gate_receipt": validate_dual_domain_gate,
    "rootful_gate_receipt": validate_rootful_gate,
    "p1_liveness_gate_receipt": validate_p1_liveness_gate,
    "network_gate_receipt": validate_network_gate,
    "hard_network_gate_receipt": validate_hard_network_gate,
    "native_gate_receipt": validate_native_gate,
    "watch_handoff_receipt": validate_watch_handoff,
    "zero_exposure_receipt": validate_zero_exposure,
}


def _load_input(name: str, path: Path, expected_uid: int) -> tuple[InputSnapshot, Facts]:
    payload, metadata = secure_read(path, expected_uid=expected_uid)
    document = strict_object(payload, f"{name.upper()}_JSON_INVALID")
    expected_payload = (
        pretty_baseline_bytes(document)
        if name in {
            "source_baseline", "network_gate_receipt", "native_gate_receipt"}
            | {"agent_os_rootful_gate_receipt"}
        else canonical_bytes(document))
    _require(payload == expected_payload, f"{name.upper()}_NOT_CANONICAL")
    facts = VALIDATORS[name](document)
    if name == "watch_handoff_receipt":
        _validate_watch_handoff_profile_binding(
            document, expected_uid=expected_uid)
    body_sha = (
        document["body_sha256"] if "body_sha256" in document
        else digest_bytes(canonical_bytes(document)))
    return InputSnapshot(
        name, path, payload, metadata, document, digest_bytes(payload), body_sha,
    ), facts


def _binding(
    name: str, path: Path, snapshot: InputSnapshot | None,
    raw_sha: str | None = None,
) -> dict[str, Any]:
    if snapshot is None:
        return {
            "path": str(path), "file_sha256": raw_sha,
            "body_sha256": None, "schema": None, "version": None,
            "status": None,
        }
    document = snapshot.document
    status = document.get("status")
    if status is None:
        status = document.get("verdict")
    if status is None and "passed" in document:
        status = "PASS" if document.get("passed") is True else "FAIL"
    if status is None:
        status = document.get("decision", "VALID")
    return {
        "path": str(path), "file_sha256": snapshot.file_sha256,
        "body_sha256": snapshot.body_sha256,
        "schema": document.get("schema"), "version": document.get("version"),
        "status": status,
    }


def _matches(reference: Any, snapshot: InputSnapshot) -> bool:
    return reference == {
        "path": str(snapshot.path),
        "file_sha256": snapshot.file_sha256,
        "body_sha256": snapshot.body_sha256,
    }


def _cross_validate(
    snapshots: Mapping[str, InputSnapshot], facts: Mapping[str, Facts],
    expected_domain: str, expected_campaign: str,
) -> tuple[list[str], list[str], str | None]:
    dangers: list[str] = []
    readiness: list[str] = []
    baseline = snapshots["source_baseline"].document
    manifest = snapshots["install_manifest"].document
    receipt = snapshots["install_receipt"].document
    pointer = snapshots["install_pointer"].document
    profile = snapshots["profile_receipt"].document
    activation = snapshots["activation_receipt"].document
    audit = snapshots["p1_audit_receipt"].document
    release = snapshots["release_validation_receipt"].document
    agent_os = snapshots["agent_os_rootful_gate_receipt"].document
    dual = snapshots["dual_domain_gate_receipt"].document
    rootful = snapshots["rootful_gate_receipt"].document
    liveness = snapshots["p1_liveness_gate_receipt"].document
    network = snapshots["network_gate_receipt"].document
    hard_network = snapshots["hard_network_gate_receipt"].document
    handoff = snapshots["watch_handoff_receipt"].document
    exposure = snapshots["zero_exposure_receipt"].document
    _validate_watch_handoff_profile_binding(
        handoff, expected_uid=snapshots["watch_handoff_receipt"].metadata.st_uid)

    if snapshots["native_gate_receipt"].document.get("passed") is True:
        try:
            _reverify_native_gate_evidence(
                snapshots["native_gate_receipt"].document)
        except AdmissionError as error:
            dangers.append(error.reason)

    source = baseline["source_manifest"]["sha256"]
    sources = {value.source for value in facts.values() if value.source is not None}
    if sources != {source}:
        dangers.append("SOURCE_LINEAGE_MISMATCH")
    domains = {value.domain for value in facts.values() if value.domain is not None}
    if domains != {expected_domain}:
        dangers.append("DOMAIN_LINEAGE_MISMATCH")
    campaigns = {
        value.campaign for value in facts.values() if value.campaign is not None}
    if campaigns != {expected_campaign}:
        dangers.append("CAMPAIGN_LINEAGE_MISMATCH")

    baseline_records = {
        record["path"]: record for record in baseline["source_manifest"]["files"]}
    installed_records = {
        record["path"]: record for record in manifest["files"]}
    for label, records in (
        ("DUAL_DOMAIN_GATE", dual["inputs"]),
        ("ROOTFUL_GATE", rootful["inputs"]),
        ("P1_LIVENESS_GATE", liveness["inputs"]),
        ("NETWORK_GATE", network["staged_inputs"]),
    ):
        if any(
            path not in baseline_records or
            record.get("sha256") !=
                baseline_records[path]["sha256"].removeprefix("sha256:") or
            record.get("size") != baseline_records[path]["size"] or
            record.get("mode") != baseline_records[path]["mode"]
            for path, record in records.items()
        ):
            dangers.append(f"{label}_SOURCE_BINDING_MISMATCH")
    agent_input_records = {
        record["path"]: record for record in agent_os["inputs"]
        if record["path"] in AGENT_OS_SOURCE_MODES
    }
    if (
        set(agent_input_records) != set(AGENT_OS_SOURCE_MODES) or
        any(
            path not in baseline_records or
            record["sha256"] !=
                baseline_records[path]["sha256"].removeprefix("sha256:") or
            record["size"] != baseline_records[path]["size"] or
            record["mode"] != baseline_records[path]["mode"]
            for path, record in agent_input_records.items())
    ):
        dangers.append("AGENT_OS_ROOTFUL_GATE_SOURCE_BINDING_MISMATCH")
    agent_binary_records = {
        PurePosixPath(record["path"]).name: record
        for record in agent_os["inputs"]
        if record["path"] not in AGENT_OS_SOURCE_MODES
    }
    native_common = snapshots["native_gate_receipt"].document[
        "common_closure"]
    if (
        set(agent_binary_records) != set(AGENT_OS_RUNTIME_BINARY_PATHS) or
        any(
            not isinstance(installed_records.get(runtime_path), dict) or
            agent_binary_records[binary]["sha256"] !=
                installed_records[runtime_path]["sha256"].removeprefix(
                    "sha256:") or
            agent_binary_records[binary]["size"] !=
                installed_records[runtime_path]["size"] or
            (agent_binary_records[binary]["mode"] !=
                installed_records[runtime_path]["mode"] or
             installed_records[runtime_path]["mode"] != "0755")
            for binary, runtime_path in
            AGENT_OS_RUNTIME_BINARY_PATHS.items()
        ) or
        any(
            agent_binary_records[binary]["sha256"] != native_common[field]
            for binary, field in
            AGENT_OS_NATIVE_BINARY_DIGEST_FIELDS.items()
        )
    ):
        dangers.append("AGENT_OS_ROOTFUL_GATE_BINARY_BINDING_MISMATCH")
    hard_runner_path = "scripts/run_hepta_broker_network_hard_isolation_gate.py"
    hard_lineage = hard_network["lineage"]
    if not (
        dual["lineage"]["source_commit"] == baseline["git_head"] and
        rootful["lineage"]["source_commit"] == baseline["git_head"] and
        hard_lineage["source_commit"] == baseline["git_head"] and
        liveness["lineage"]["source_commit"] == baseline["git_head"] and
        agent_os["environment_review_closure"]["source_commit"] ==
            baseline["git_head"] and
        release["local_evidence"]["source_lineage"]["git_head"] ==
            baseline["git_head"]
    ):
        dangers.append("ROOTFUL_GATE_GIT_LINEAGE_MISMATCH")

    review_documents = {
        "agent_os": agent_os["environment_review_closure"],
        "dual": dual["environment_review_closure"],
        "paper": rootful["environment_review_closure"],
        "liveness": liveness["environment_review_closure"],
        "hard_network": hard_network["environment_review_closure"],
    }
    review_identities = {
        label: _environment_review_identity(value)
        for label, value in review_documents.items()
        if isinstance(value, dict)
    }
    if review_identities and len({
            canonical_bytes(value) for value in review_identities.values()}) != 1:
        dangers.append("ROOTFUL_ENVIRONMENT_REVIEW_IDENTITY_MISMATCH")

    producer_digests = {
        "p1_auditor": audit["producer"]["file_sha256"],
        "watch_handoff": handoff["producer"]["file_sha256"],
        "zero_attestor": exposure["producer"]["file_sha256"],
        "zero_snapshot": exposure["snapshot_producer"]["file_sha256"],
        "rootful_review_verifier":
            agent_os["environment_review_closure"]["verifier"][
                "file_sha256"],
    }
    for label, (source_path, installed_path) in PRODUCTION_PRODUCER_PATHS.items():
        source_record = baseline_records.get(source_path)
        installed_record = installed_records.get(installed_path)
        if not (
            isinstance(source_record, dict) and
            isinstance(installed_record, dict) and
            source_record["sha256"] == installed_record["sha256"] ==
                producer_digests[label] and
            source_record["size"] == installed_record["size"] and
            source_record["mode"] == installed_record["mode"] == "0755"
        ):
            dangers.append(
                "PRODUCTION_PRODUCER_SOURCE_INSTALL_BINDING_MISMATCH_" +
                label.upper())
    if any(path not in baseline_records
           for path in RELEASE_VALIDATION_SOURCE_PATHS):
        dangers.append("RELEASE_VALIDATION_PRODUCER_SOURCE_BINDING_MISMATCH")

    release_local = release["local_evidence"]
    release_lineage = release_local["source_lineage"]
    release_critical = {
        record["role"]: record for record in release_local["critical_files"]}
    release_baseline = release_local["source_baseline"]
    release_native = release_critical.get("native-runtime-aggregate")
    if not (
        release_lineage["strict_source_security_manifest_sha256"] ==
            source.removeprefix("sha256:") and
        release_baseline["sha256"] ==
            snapshots["source_baseline"].file_sha256.removeprefix("sha256:") and
        release_critical.get("source-baseline-manifest") == {
            "role": "source-baseline-manifest", **release_baseline} and
        isinstance(release_native, dict) and
        release_native.get("sha256") ==
            snapshots["native_gate_receipt"].file_sha256.removeprefix("sha256:")
        and release_lineage["strict_source_files_sha256"] ==
            snapshots["native_gate_receipt"].document["common_closure"][
                "clean_source_files_sha256"]
    ):
        dangers.append("RELEASE_VALIDATION_SOURCE_BINDING_MISMATCH")
    hard_runner_record = baseline_records.get(hard_runner_path)
    if not (
        hard_lineage["source_manifest_sha256"] ==
            baseline["source_manifest"]["sha256"].removeprefix("sha256:") and
        hard_runner_record is not None and
        hard_lineage["runner_sha256"] ==
            hard_runner_record["sha256"].removeprefix("sha256:")
    ):
        dangers.append("HARD_NETWORK_GATE_SOURCE_BINDING_MISMATCH")
    handoff_runner_path = "scripts/hepta_p1_watch_to_paper_handoff.py"
    handoff_runner_record = baseline_records.get(handoff_runner_path)
    if not (
        handoff_runner_record is not None and
        handoff["producer"]["file_sha256"] ==
            handoff_runner_record["sha256"]
    ):
        dangers.append("WATCH_HANDOFF_SOURCE_BINDING_MISMATCH")

    paths = [record["path"] for record in manifest["files"]]
    if not (
        receipt["archive_sha256"] == manifest["archive_sha256"] ==
            pointer["archive_sha256"] and
        receipt["source_baseline_sha256"] ==
            manifest["source_baseline_sha256"] ==
            pointer["source_baseline_sha256"] == source and
        receipt["installer_sha256"] == manifest["installer_sha256"] ==
            pointer["installer_sha256"] and
        receipt["installed_file_count"] == len(paths) ==
            pointer["installed_file_count"] and
        receipt["installed_paths_sha256"] == pointer["installed_paths_sha256"]
            == digest_bytes(canonical_bytes(paths)) and
        pointer["manifest_path"] == str(snapshots["install_manifest"].path) and
        pointer["manifest_file_sha256"] ==
            snapshots["install_manifest"].file_sha256 and
        pointer["receipt_path"] == str(snapshots["install_receipt"].path) and
        pointer["receipt_file_sha256"] ==
            snapshots["install_receipt"].file_sha256
    ):
        dangers.append("INSTALL_LINEAGE_MISMATCH")

    evidence = profile["shadow_install_evidence"]
    expected_install_evidence = {
        "receipt_path": str(snapshots["install_receipt"].path),
        "receipt_file_sha256": snapshots["install_receipt"].file_sha256,
        "receipt_body_sha256": snapshots["install_receipt"].body_sha256,
        "manifest_path": str(snapshots["install_manifest"].path),
        "manifest_file_sha256": snapshots["install_manifest"].file_sha256,
        "current_install_pointer_path": str(snapshots["install_pointer"].path),
        "current_install_pointer_file_sha256":
            snapshots["install_pointer"].file_sha256,
    }
    if any(evidence.get(key) != value
           for key, value in expected_install_evidence.items()):
        dangers.append("PROFILE_INSTALL_BINDING_MISMATCH")
    if activation["shadow_install_evidence"] != evidence:
        dangers.append("ACTIVATION_INSTALL_BINDING_MISMATCH")
    if not (
        activation["profile_deployment_receipt_path"] ==
            str(snapshots["profile_receipt"].path) and
        activation["profile_deployment_receipt_file_sha256"] ==
            snapshots["profile_receipt"].file_sha256 and
        activation["profile_deployment_receipt_body_sha256"] ==
            snapshots["profile_receipt"].body_sha256
    ):
        dangers.append("ACTIVATION_PROFILE_BINDING_MISMATCH")
    if not (
        _matches(handoff["activation_receipt"],
                 snapshots["activation_receipt"]) and
        _matches(handoff["p1_audit_receipt"], snapshots["p1_audit_receipt"]) and
        handoff["freeze_bundle"] == audit["freeze_bundle"]
    ):
        dangers.append("WATCH_HANDOFF_BINDING_MISMATCH")
    if not _matches(exposure["watch_handoff_receipt"],
                    snapshots["watch_handoff_receipt"]):
        dangers.append("ZERO_EXPOSURE_HANDOFF_BINDING_MISMATCH")
    return readiness, dangers, source


def validate_output_receipt(document: Any) -> dict[str, Any]:
    reason = "ADMISSION_OUTPUT_RECEIPT_INVALID"
    _require(isinstance(document, dict), reason)
    _sealed(document, OUTPUT_FIELDS, reason)
    _require(
        document.get("schema") == RECEIPT_SCHEMA and
        document.get("version") == RECEIPT_VERSION and
        document.get("status") in {"GO", "NO_GO", "HALT"} and
        document.get("round") == ROUND and
        document.get("authorization_effect") ==
            "NONE_READ_ONLY_CANDIDATE_ONLY" and
        all(document.get(field) is False for field in (
            "paper_authorized", "live_authorized", "mutation_authorized",
            "direct_broker_access", "order_submission_authorized")) and
        document.get("paper_test_admission_candidate") is
            (document.get("status") == "GO"), reason)
    _safe_token(document.get("domain"), DOMAIN, reason)
    _safe_token(document.get("campaign_id"), CAMPAIGN, reason)
    _digest(document.get("source_baseline_sha256"), reason)
    strategy = _digest(document.get("strategy_sha256"), reason)
    evaluated = _integer(document.get("evaluated_at_ms"), reason)
    expires = _integer(document.get("expires_at_ms"), reason)
    _require(evaluated < expires, reason)
    bindings = document.get("input_bindings")
    _require(isinstance(bindings, dict) and set(bindings) == set(INPUT_NAMES),
             reason)
    for binding in bindings.values():
        _require(isinstance(binding, dict) and
                 set(binding) == OUTPUT_BINDING_FIELDS, reason)
        _canonical_path(Path(binding.get("path", "")), reason)
        for field in ("file_sha256", "body_sha256"):
            value = binding.get(field)
            _require(value is None or
                     (type(value) is str and DIGEST.fullmatch(value) is not None),
                     reason)
    findings = document.get("findings")
    _require(isinstance(findings, list) and findings == sorted(set(findings)) and
             all(type(item) is str and item for item in findings), reason)
    if document["status"] == "GO":
        _require(strategy != "sha256:" + "0" * 64 and not findings and all(
            binding["file_sha256"] is not None and
            binding["body_sha256"] is not None
            for binding in bindings.values()), reason)
    return document


def evaluate_candidate(
    paths: Mapping[str, Path], *, expected_domain: str,
    expected_campaign: str, expected_uid: int = ROOT_UID,
    now_ms: int | None = None,
) -> Evaluation:
    """Evaluate immutable inputs without publishing or granting authority."""

    _safe_token(expected_domain, DOMAIN, "EXPECTED_DOMAIN_INVALID")
    _safe_token(expected_campaign, CAMPAIGN, "EXPECTED_CAMPAIGN_INVALID")
    _require(set(paths) == set(INPUT_NAMES), "ADMISSION_INPUT_SET_INVALID")
    normalized = {
        name: _canonical_path(Path(paths[name]), "ADMISSION_INPUT_PATH_INVALID")
        for name in INPUT_NAMES
    }
    _require(len(set(normalized.values())) == len(normalized),
             "ADMISSION_INPUT_PATH_ALIAS")
    now = time.time_ns() // 1_000_000 if now_ms is None else now_ms
    _require(type(now) is int and now >= 0, "ADMISSION_TIME_INVALID")

    snapshots: dict[str, InputSnapshot] = {}
    facts: dict[str, Facts] = {}
    readiness: list[str] = []
    dangers: list[str] = []
    bindings: dict[str, dict[str, Any]] = {}
    for name in INPUT_NAMES:
        path = normalized[name]
        try:
            snapshot, input_facts = _load_input(name, path, expected_uid)
            snapshots[name] = snapshot
            facts[name] = input_facts
            bindings[name] = _binding(name, path, snapshot)
            readiness.extend(input_facts.readiness)
            dangers.extend(input_facts.dangers)
        except AdmissionError as error:
            raw_sha: str | None = None
            if error.reason not in {"ADMISSION_INPUT_MISSING"}:
                try:
                    raw, _ = secure_read(path, expected_uid=expected_uid)
                    raw_sha = digest_bytes(raw)
                except AdmissionError:
                    pass
            bindings[name] = _binding(name, path, None, raw_sha)
            target = dangers if error.dangerous else readiness
            target.append(f"{name.upper()}_{error.reason}")

    source: str | None = None
    strategy: str | None = None
    if len(snapshots) == len(INPUT_NAMES):
        more_readiness, more_dangers, source = _cross_validate(
            snapshots, facts, expected_domain, expected_campaign)
        readiness.extend(more_readiness)
        dangers.extend(more_dangers)
        for name, value in facts.items():
            if value.issued_at_ms is not None:
                if value.issued_at_ms > now + MAXIMUM_CLOCK_SKEW_MS:
                    dangers.append(f"{name.upper()}_FUTURE_DATED")
                age_limit = (
                    MAXIMUM_EXPOSURE_AGE_MS if name == "zero_exposure_receipt"
                    else MAXIMUM_STATIC_AGE_MS)
                if now >= value.issued_at_ms + age_limit:
                    readiness.append(f"{name.upper()}_STALE")
            if value.expires_at_ms is not None and now >= value.expires_at_ms:
                readiness.append(f"{name.upper()}_EXPIRED")
    else:
        baseline_facts = facts.get("source_baseline")
        source = baseline_facts.source if baseline_facts else None

    audit_facts = facts.get("p1_audit_receipt")
    strategy = audit_facts.strategy_sha256 if audit_facts else None

    if source is None:
        source = "sha256:" + "0" * 64
    if strategy is None:
        strategy = "sha256:" + "0" * 64
    status = "HALT" if dangers else "NO_GO" if readiness else "GO"
    findings = sorted(set(dangers + readiness))
    upstream_expiries = [
        value.expires_at_ms for value in facts.values()
        if value.expires_at_ms is not None and value.expires_at_ms > now]
    age_deadlines = [
        value.issued_at_ms + (
            MAXIMUM_EXPOSURE_AGE_MS
            if name == "zero_exposure_receipt" else MAXIMUM_STATIC_AGE_MS)
        for name, value in facts.items()
        if value.issued_at_ms is not None and
        value.issued_at_ms + (
            MAXIMUM_EXPOSURE_AGE_MS
            if name == "zero_exposure_receipt" else MAXIMUM_STATIC_AGE_MS
        ) > now
    ]
    expires = min([
        now + OUTPUT_LIFETIME_MS, *upstream_expiries, *age_deadlines])
    if expires <= now:
        expires = now + 1
    body = {
        "schema": RECEIPT_SCHEMA,
        "version": RECEIPT_VERSION,
        "status": status,
        "evaluated_at_ms": now,
        "expires_at_ms": expires,
        "round": ROUND,
        "domain": expected_domain,
        "campaign_id": expected_campaign,
        "source_baseline_sha256": source,
        "strategy_sha256": strategy,
        "input_bindings": bindings,
        "findings": findings,
        "paper_test_admission_candidate": status == "GO",
        "paper_authorized": False,
        "live_authorized": False,
        "mutation_authorized": False,
        "direct_broker_access": False,
        "order_submission_authorized": False,
        "authorization_effect": "NONE_READ_ONLY_CANDIDATE_ONLY",
    }
    receipt = seal(body)
    validate_output_receipt(receipt)
    return Evaluation(receipt, snapshots)


def assert_inputs_unchanged(
    snapshots: Mapping[str, InputSnapshot], *, expected_uid: int,
) -> None:
    for name in INPUT_NAMES:
        snapshot = snapshots.get(name)
        if snapshot is None:
            continue
        payload, metadata = secure_read(snapshot.path, expected_uid=expected_uid)
        _require(
            payload == snapshot.payload and
            _identity(metadata) == _identity(snapshot.metadata),
            f"{name.upper()}_SECURE_REOPEN_MISMATCH")


def _rename_noreplace(parent: int, source: str, destination: str) -> None:
    function = getattr(_LIBC, "renameat2", None)
    if function is None:
        raise AdmissionError("ADMISSION_RENAMEAT2_UNAVAILABLE")
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
            raise AdmissionError("ADMISSION_OUTPUT_ALREADY_EXISTS")
        raise AdmissionError("ADMISSION_OUTPUT_RENAME_FAILED")


def publish_candidate(
    evaluation: Evaluation, output: Path, *, expected_uid: int = ROOT_UID,
) -> str:
    """Securely reopen every input and atomically publish one new receipt."""

    output = _canonical_path(output, "ADMISSION_OUTPUT_PATH_INVALID")
    _require(output not in {item.path for item in evaluation.snapshots.values()},
             "ADMISSION_OUTPUT_ALIASES_INPUT")
    payload = canonical_bytes(evaluation.receipt)
    _require(len(payload) <= MAXIMUM_OUTPUT_BYTES,
             "ADMISSION_OUTPUT_TOO_LARGE")
    validate_output_receipt(evaluation.receipt)
    assert_inputs_unchanged(evaluation.snapshots, expected_uid=expected_uid)
    parent = _open_anchored_directory(
        output.parent, "ADMISSION_OUTPUT_PARENT_INVALID")
    parent_identity = _trusted_directory_identity(
        parent, expected_uid=expected_uid,
        reason="ADMISSION_OUTPUT_PARENT_UNTRUSTED")
    temporary = f".{output.name}.hepta-admission-{secrets.token_hex(16)}.tmp"
    descriptor: int | None = None
    renamed = False
    try:
        try:
            os.stat(output.name, dir_fd=parent, follow_symlinks=False)
        except FileNotFoundError:
            pass
        else:
            raise AdmissionError("ADMISSION_OUTPUT_ALREADY_EXISTS")
        descriptor = os.open(temporary, CREATE_FLAGS, 0o600, dir_fd=parent)
        os.fchmod(descriptor, 0o600)
        os.fchown(descriptor, expected_uid, ROOT_GID if expected_uid == 0 else os.getegid())
        written = 0
        while written < len(payload):
            count = os.write(descriptor, payload[written:])
            _require(count > 0, "ADMISSION_OUTPUT_WRITE_FAILED")
            written += count
        os.fsync(descriptor)
        prepared = os.fstat(descriptor)
        _require(
            stat.S_ISREG(prepared.st_mode) and prepared.st_nlink == 1 and
            prepared.st_uid == expected_uid and
            stat.S_IMODE(prepared.st_mode) == 0o600 and
            prepared.st_size == len(payload),
            "ADMISSION_OUTPUT_METADATA_INVALID")
        os.fsync(parent)
        _require(
            parent_identity == _trusted_directory_identity(
                parent, expected_uid=expected_uid,
                reason="ADMISSION_OUTPUT_PARENT_REBOUND"),
            "ADMISSION_OUTPUT_PARENT_REBOUND")
        assert_inputs_unchanged(evaluation.snapshots, expected_uid=expected_uid)
        _rename_noreplace(parent, temporary, output.name)
        renamed = True
        os.fsync(parent)
        _require(
            parent_identity == _trusted_directory_identity(
                parent, expected_uid=expected_uid,
                reason="ADMISSION_OUTPUT_PARENT_REBOUND"),
            "ADMISSION_OUTPUT_PARENT_REBOUND")
    except OSError as error:
        raise AdmissionError("ADMISSION_OUTPUT_PUBLISH_FAILED") from error
    finally:
        if descriptor is not None:
            os.close(descriptor)
        if not renamed:
            try:
                os.unlink(temporary, dir_fd=parent)
                os.fsync(parent)
            except FileNotFoundError:
                pass
            except OSError:
                pass
        os.close(parent)
    committed, _ = secure_read(
        output, expected_uid=expected_uid, maximum=MAXIMUM_OUTPUT_BYTES,
        modes=frozenset({0o600}))
    _require(committed == payload, "ADMISSION_OUTPUT_POST_VERIFY_FAILED")
    validate_output_receipt(strict_object(
        committed, "ADMISSION_OUTPUT_POST_VERIFY_FAILED"))
    return digest_bytes(committed)


def _load_zero_snapshot_producer() -> LoadedProducerModule:
    """Load the fixed producer bytes without a second path-based code read."""

    path = ZERO_SNAPSHOT_PRODUCER_EXECUTABLE
    payload, metadata = secure_read(
        path, expected_uid=ROOT_UID,
        modes=frozenset({0o500, 0o555, 0o700, 0o755}))
    name = "_hepta_admission_zero_producer_" + secrets.token_hex(16)
    module = ModuleType(name)
    module.__file__ = str(path)
    module.__package__ = ""
    module.__loader__ = importlib.machinery.SourceFileLoader(name, str(path))
    module.__spec__ = importlib.util.spec_from_loader(
        name, module.__loader__, origin=str(path))
    sys.modules[name] = module
    try:
        code = compile(payload, str(path), "exec", dont_inherit=True)
        exec(code, module.__dict__)
    except BaseException as error:
        sys.modules.pop(name, None)
        raise AdmissionError("ADMISSION_ZERO_PRODUCER_LOAD_FAILED") from error
    _require(
        getattr(module, "INSTALLED_EXECUTABLE", None) == path and
        getattr(module, "PRODUCTION_MODE", None) ==
            ZERO_SNAPSHOT_PRODUCTION_MODE and
        callable(getattr(module, "open_admission_reservation_session", None))
        and callable(getattr(module, "reservation_reference", None)) and
        getattr(module, "CLI_RUN_TOKEN", None) is not None,
        "ADMISSION_ZERO_PRODUCER_CONTRACT_INVALID")
    binding = LoadedProducerModule(path, payload, metadata, module)
    binding.reopen()
    return binding


def _bind_running_admission_executable() -> tuple[bytes, os.stat_result]:
    reason = "ADMISSION_FIXED_INSTALL_REQUIRED"
    lexical = Path(__file__).absolute()
    try:
        metadata = os.lstat(lexical)
        resolved = lexical.resolve(strict=True)
    except OSError as error:
        raise AdmissionError(reason) from error
    _require(
        lexical == INSTALLED_EXECUTABLE and resolved == INSTALLED_EXECUTABLE and
        not stat.S_ISLNK(metadata.st_mode) and
        os.path.samefile(lexical, INSTALLED_EXECUTABLE),
        reason)
    return secure_read(
        INSTALLED_EXECUTABLE, expected_uid=ROOT_UID,
        modes=frozenset({0o500, 0o555, 0o700, 0o755}))


def _reopen_executable(
    path: Path, payload: bytes, metadata: os.stat_result,
) -> None:
    current, current_metadata = secure_read(
        path, expected_uid=ROOT_UID,
        modes=frozenset({0o500, 0o555, 0o700, 0o755}))
    _require(
        current == payload and
        _identity(current_metadata) == _identity(metadata),
        "ADMISSION_EXECUTABLE_REBOUND")


def _bind_runtime_file(
    path: Path, *, modes: frozenset[int], maximum: int, reason: str,
    minimum: int = 1,
) -> BoundRuntimeFile:
    payload, metadata = secure_read(
        path, expected_uid=ROOT_UID, modes=modes, maximum=maximum,
        minimum=minimum)
    result = BoundRuntimeFile(
        path, payload, metadata, ROOT_UID, modes, maximum, minimum, reason)
    result.reopen()
    return result


def _rootfs_relative_path(path: Path, reason: str) -> PurePosixPath:
    path = _canonical_path(path, reason)
    relative = PurePosixPath(*path.parts[1:])
    _require(
        relative.parts and ".." not in relative.parts and
        all(part not in {"", ".", ".."} for part in relative.parts) and
        not any(part == "__pycache__" for part in relative.parts) and
        relative.suffix not in {".pyc", ".pyo"},
        reason)
    return relative


def _bind_logical_runtime_file(
    logical_path: Path, *, reason: str, minimum: int = 1,
) -> RootfsRuntimeFile:
    """Bind resolved bytes once; the child receives a regular no-link copy."""

    logical_path = _canonical_path(logical_path, reason)
    try:
        lexical_before = os.lstat(logical_path)
        source_path = logical_path.resolve(strict=True)
        source_metadata = os.stat(source_path, follow_symlinks=False)
        lexical_after = os.lstat(logical_path)
    except OSError as error:
        raise AdmissionError(reason) from error
    _require(
        (stat.S_ISREG(lexical_before.st_mode) or
         stat.S_ISLNK(lexical_before.st_mode)) and
        _identity(lexical_before) == _identity(lexical_after) and
        stat.S_ISREG(source_metadata.st_mode), reason)
    mode = stat.S_IMODE(source_metadata.st_mode)
    _require(
        mode in {0o400, 0o440, 0o444, 0o500, 0o550, 0o555,
                 0o600, 0o640, 0o644, 0o700, 0o750, 0o755},
        reason)
    binding = _bind_runtime_file(
        source_path, modes=frozenset({mode}),
        maximum=MAXIMUM_RELEASE_CAUSAL_DEPENDENCY_BYTES, reason=reason,
        minimum=minimum)
    return RootfsRuntimeFile(
        logical_path, binding.payload, mode, binding)


def _bind_release_causal_python_tree(
) -> tuple[tuple[BoundRuntimeFile, ...], tuple[RootfsRuntimeFile, ...]]:
    """Use root-owned dpkg manifests as the exact Python runtime allowlist."""

    reason = "ADMISSION_RELEASE_CAUSAL_PYTHON_RUNTIME_INVALID"
    manifest_bindings: list[BoundRuntimeFile] = []
    logical_paths: set[Path] = set()
    for manifest_path in RELEASE_CAUSAL_PYTHON_PACKAGE_MANIFESTS:
        binding = _bind_runtime_file(
            manifest_path, modes=frozenset({0o644}), maximum=2 * 1024 * 1024,
            reason="ADMISSION_RELEASE_CAUSAL_PACKAGE_MANIFEST_REBOUND")
        manifest_bindings.append(binding)
        try:
            text = binding.payload.decode("utf-8", errors="strict")
        except UnicodeDecodeError as error:
            raise AdmissionError(reason) from error
        _require(text.endswith("\n") and "\r" not in text and "\0" not in text,
                 reason)
        lines = text.splitlines()
        _require(len(lines) == len(set(lines)), reason)
        for item in lines:
            if not item.startswith("/usr/lib/python3.12/"):
                continue
            lexical = PurePosixPath(item)
            _require(
                lexical.is_absolute() and lexical.as_posix() == item and
                ".." not in lexical.parts and
                all(part not in {"", ".", ".."}
                    for part in lexical.parts[1:]),
                reason)
            try:
                path = _canonical_path(Path(item), reason)
            except (AdmissionError, ValueError) as error:
                if isinstance(error, AdmissionError):
                    raise
                raise AdmissionError(reason) from error
            _require(Path("/usr/lib/python3.12") in path.parents, reason)
            try:
                metadata = os.lstat(path)
            except OSError as error:
                raise AdmissionError(reason) from error
            if stat.S_ISDIR(metadata.st_mode):
                continue
            _rootfs_relative_path(path, reason)
            _require(
                stat.S_ISREG(metadata.st_mode) or
                stat.S_ISLNK(metadata.st_mode), reason)
            logical_paths.add(path)
    _require(
        Path("/usr/lib/python3.12/json/__init__.py") in logical_paths and
        Path("/usr/lib/python3.12/subprocess.py") in logical_paths and
        Path("/usr/lib/python3.12/tempfile.py") in logical_paths and
        len(logical_paths) >= 500,
        reason)
    entries = tuple(
        _bind_logical_runtime_file(
            path, reason="ADMISSION_RELEASE_CAUSAL_PYTHON_FILE_REBOUND",
            minimum=0)
        for path in sorted(logical_paths, key=str))
    _require(
        len({entry.logical_path for entry in entries}) == len(entries), reason)
    return tuple(manifest_bindings), entries


def _bind_release_causal_runtime() -> ReleaseCausalRuntime:
    executable_modes = frozenset({0o500, 0o550, 0o555, 0o700, 0o750, 0o755})
    interpreter = _bind_runtime_file(
        RELEASE_CAUSAL_PYTHON, modes=executable_modes,
        maximum=MAXIMUM_RELEASE_CAUSAL_DEPENDENCY_BYTES,
        reason="ADMISSION_RELEASE_CAUSAL_INTERPRETER_REBOUND")
    verifier = _bind_runtime_file(
        RELEASE_CAUSAL_VERIFIER, modes=executable_modes,
        maximum=MAXIMUM_RELEASE_CAUSAL_DEPENDENCY_BYTES,
        reason="ADMISSION_RELEASE_CAUSAL_VERIFIER_REBOUND")
    runtime_modules: list[BoundRuntimeFile] = []
    project_entries: list[RootfsRuntimeFile] = [
        RootfsRuntimeFile(
            RELEASE_CAUSAL_PYTHON, interpreter.payload,
            stat.S_IMODE(interpreter.metadata.st_mode), interpreter),
        RootfsRuntimeFile(
            RELEASE_CAUSAL_VERIFIER, verifier.payload,
            stat.S_IMODE(verifier.metadata.st_mode), verifier),
    ]
    for source_path, (installed_path, _source_mode, installed_mode) in sorted(
            RELEASE_CAUSAL_SOURCE_INSTALL_PATHS.items()):
        if source_path == \
                "scripts/verify_heptatrader_release_validation_closure.py":
            continue
        logical = Path("/") / installed_path
        binding = _bind_runtime_file(
            logical, modes=frozenset({int(installed_mode, 8)}),
            maximum=MAXIMUM_RELEASE_CAUSAL_DEPENDENCY_BYTES,
            reason="ADMISSION_RELEASE_CAUSAL_RUNTIME_MODULE_REBOUND")
        runtime_modules.append(binding)
        project_entries.append(RootfsRuntimeFile(
            logical, binding.payload, int(installed_mode, 8), binding))
    openssl = _bind_runtime_file(
        RELEASE_CAUSAL_OPENSSL, modes=executable_modes,
        maximum=MAXIMUM_RELEASE_CAUSAL_DEPENDENCY_BYTES,
        reason="ADMISSION_RELEASE_CAUSAL_OPENSSL_REBOUND")
    runtime_modules.append(openssl)
    project_entries.append(RootfsRuntimeFile(
        RELEASE_CAUSAL_OPENSSL, openssl.payload,
        stat.S_IMODE(openssl.metadata.st_mode), openssl))
    manifests, python_entries = _bind_release_causal_python_tree()
    abi_entries = tuple(
        _bind_logical_runtime_file(
            path, reason="ADMISSION_RELEASE_CAUSAL_ABI_FILE_REBOUND")
        for path in RELEASE_CAUSAL_ABI_LOGICAL_PATHS)
    generated = RootfsRuntimeFile(
        Path("/etc/heptatrader/release-causal-openssl.cnf"),
        RELEASE_CAUSAL_OPENSSL_CONFIGURATION, 0o400, None)
    rootfs_files = tuple(
        sorted(
            (*project_entries, *python_entries, *abi_entries, generated),
            key=lambda entry: str(entry.logical_path)))
    _require(
        len({entry.logical_path for entry in rootfs_files}) ==
            len(rootfs_files) and
        all(entry.mode & 0o022 == 0 for entry in rootfs_files),
        "ADMISSION_RELEASE_CAUSAL_ROOTFS_CONTRACT_INVALID")
    result = ReleaseCausalRuntime(
        interpreter, verifier, tuple(runtime_modules), manifests, rootfs_files)
    result.reopen()
    return result


def _write_release_causal_stage_file(
    directory: int, name: str, payload: bytes, *, mode: int, owner_uid: int,
) -> os.stat_result:
    reason = "ADMISSION_RELEASE_CAUSAL_STAGE_CREATE_FAILED"
    descriptor: int | None = None
    try:
        descriptor = os.open(name, CREATE_FLAGS, mode, dir_fd=directory)
        os.fchmod(descriptor, mode)
        written = 0
        while written < len(payload):
            count = os.write(descriptor, payload[written:])
            _require(count > 0, reason)
            written += count
        os.fsync(descriptor)
        metadata = os.fstat(descriptor)
        _require(
            stat.S_ISREG(metadata.st_mode) and metadata.st_nlink == 1 and
            metadata.st_uid == owner_uid and
            stat.S_IMODE(metadata.st_mode) == mode and
            metadata.st_size == len(payload), reason)
        return metadata
    except AdmissionError:
        raise
    except OSError as error:
        raise AdmissionError(reason) from error
    finally:
        if descriptor is not None:
            os.close(descriptor)


def _open_release_causal_relative_directory(
    root: int, relative: PurePosixPath, reason: str,
) -> int:
    descriptor = os.dup(root)
    try:
        if relative == PurePosixPath("."):
            return descriptor
        for component in relative.parts:
            child = os.open(component, DIRECTORY_FLAGS, dir_fd=descriptor)
            os.close(descriptor)
            descriptor = child
        return descriptor
    except OSError as error:
        os.close(descriptor)
        raise AdmissionError(reason) from error


def _release_causal_libc() -> ctypes.CDLL:
    """Return the already-mapped process libc used for namespace syscalls."""

    library = ctypes.CDLL(None, use_errno=True)
    library.unshare.argtypes = [ctypes.c_int]
    library.unshare.restype = ctypes.c_int
    library.mount.argtypes = [
        ctypes.c_char_p, ctypes.c_char_p, ctypes.c_char_p,
        ctypes.c_ulong, ctypes.c_void_p,
    ]
    library.mount.restype = ctypes.c_int
    library.umount2.argtypes = [ctypes.c_char_p, ctypes.c_int]
    library.umount2.restype = ctypes.c_int
    return library


def _release_causal_syscall(
    function: Callable[..., int], reason: str, *arguments: Any,
) -> None:
    ctypes.set_errno(0)
    if function(*arguments) != 0:
        error = ctypes.get_errno()
        raise AdmissionError(reason) from OSError(error, os.strerror(error))


def _release_causal_mountinfo(reason: str) -> tuple[dict[str, Any], ...]:
    """Capture the kernel mount table without accepting escaped mount paths."""

    try:
        payload = Path("/proc/self/mountinfo").read_bytes()
    except OSError as error:
        raise AdmissionError(reason) from error
    _require(
        0 < len(payload) <= 4 * 1024 * 1024 and payload.endswith(b"\n") and
        b"\0" not in payload,
        reason)
    result: list[dict[str, Any]] = []
    try:
        lines = payload.decode("ascii", errors="strict").splitlines()
    except UnicodeDecodeError as error:
        raise AdmissionError(reason) from error
    for line in lines:
        fields = line.split(" ")
        _require("-" in fields and len(fields) >= 10, reason)
        separator = fields.index("-")
        _require(separator >= 6 and len(fields) >= separator + 4, reason)
        result.append({
            "mount_id": fields[0],
            "parent_id": fields[1],
            "device": fields[2],
            "root": fields[3],
            "mount_point": fields[4],
            "mount_options": frozenset(fields[5].split(",")),
            "optional": tuple(fields[6:separator]),
            "filesystem": fields[separator + 1],
            "source": fields[separator + 2],
            "super_options": frozenset(fields[separator + 3].split(",")),
        })
    _require(len(result) == len(lines), reason)
    return tuple(result)


def _release_causal_mount_record(path: Path, reason: str) -> dict[str, Any]:
    matches = [
        record for record in _release_causal_mountinfo(reason)
        if record["mount_point"] == str(path)
    ]
    _require(len(matches) == 1, reason)
    return matches[0]


def _verify_release_causal_private_propagation(reason: str) -> None:
    records = _release_causal_mountinfo(reason)
    root = [record for record in records if record["mount_point"] == "/"]
    _require(
        len(root) == 1 and
        not any(
            field.startswith(("shared:", "master:", "propagate_from:")) or
            field == "unbindable"
            for record in records for field in record["optional"]),
        reason)


def _verify_release_causal_mapped_libc(
    rootfs_files: tuple[RootfsRuntimeFile, ...],
) -> None:
    """Bind the libc already executing the namespace/chroot syscalls."""

    reason = "ADMISSION_RELEASE_CAUSAL_MAPPED_LIBC_INVALID"
    candidates = [
        entry for entry in rootfs_files
        if entry.logical_path == Path("/lib/x86_64-linux-gnu/libc.so.6")
    ]
    _require(len(candidates) == 1 and candidates[0].binding is not None, reason)
    binding = candidates[0].binding
    assert binding is not None
    binding.reopen()
    try:
        lines = Path("/proc/self/maps").read_text(
            encoding="ascii", errors="strict").splitlines()
    except (OSError, UnicodeDecodeError) as error:
        raise AdmissionError(reason) from error
    identities: set[tuple[int, int, int]] = set()
    for line in lines:
        fields = line.split(maxsplit=5)
        if len(fields) != 6 or fields[5] != str(binding.path):
            continue
        try:
            major_text, minor_text = fields[3].split(":", 1)
            identities.add((
                int(major_text, 16), int(minor_text, 16), int(fields[4])))
        except (ValueError, TypeError) as error:
            raise AdmissionError(reason) from error
    metadata = binding.metadata
    _require(
        identities == {(
            os.major(metadata.st_dev), os.minor(metadata.st_dev),
            metadata.st_ino)},
        reason)
    binding.reopen()


def _enter_release_causal_private_mount_namespace() -> None:
    """Detach mount propagation before creating the executable child root."""

    reason = "ADMISSION_RELEASE_CAUSAL_MOUNT_NAMESPACE_INVALID"
    _require(os.geteuid() == 0 and os.getegid() == 0, reason)
    library = _release_causal_libc()
    _release_causal_syscall(library.unshare, reason, CLONE_NEWNS)
    _release_causal_syscall(
        library.mount, reason, None, b"/", None,
        MS_REC | MS_PRIVATE, None)
    _verify_release_causal_private_propagation(reason)


def _mount_release_causal_tmpfs(
    path: Path, *, source: bytes, flags: int, size: int, reason: str,
) -> None:
    _require(size > 0 and b"," not in source and b"\0" not in source, reason)
    data = f"mode=0700,size={size},uid=0,gid=0".encode("ascii")
    library = _release_causal_libc()
    _release_causal_syscall(
        library.mount, reason, source, os.fsencode(path), b"tmpfs", flags,
        ctypes.c_char_p(data))


def _remount_release_causal_root_read_only(path: Path, reason: str) -> None:
    library = _release_causal_libc()
    _release_causal_syscall(
        library.mount, reason, None, os.fsencode(path), None,
        MS_REMOUNT | MS_RDONLY | MS_NOSUID | MS_NODEV, None)


def _unmount_release_causal_path(path: Path, reason: str) -> None:
    library = _release_causal_libc()
    _release_causal_syscall(
        library.umount2, reason, os.fsencode(path), 0)


def _verify_release_causal_mounts(
    stage_path: Path, *, read_only: bool, reason: str,
) -> None:
    """Require one exact executable root and one writable noexec /tmp."""

    temporary_path = stage_path / "tmp"
    records = _release_causal_mountinfo(reason)
    descendants = {
        record["mount_point"]: record for record in records
        if record["mount_point"] == str(stage_path) or
        record["mount_point"].startswith(str(stage_path) + "/")
    }
    _require(set(descendants) == {str(stage_path), str(temporary_path)}, reason)
    root = descendants[str(stage_path)]
    temporary = descendants[str(temporary_path)]
    root_options = root["mount_options"] | root["super_options"]
    temporary_options = (
        temporary["mount_options"] | temporary["super_options"])
    _require(
        root["filesystem"] == "tmpfs" and
        root["source"] == "hepta-release-causal-rootfs" and
        {"nosuid", "nodev"}.issubset(root_options) and
        "noexec" not in root_options and
        (("ro" in root_options) is read_only) and
        (("rw" in root_options) is (not read_only)) and
        temporary["filesystem"] == "tmpfs" and
        temporary["source"] == "hepta-release-causal-tmp" and
        {"rw", "nosuid", "nodev", "noexec"}.issubset(
            temporary_options) and "ro" not in temporary_options,
        reason)
    root_flags = os.statvfs(stage_path).f_flag
    temporary_flags = os.statvfs(temporary_path).f_flag
    _require(
        bool(root_flags & os.ST_RDONLY) is read_only and
        root_flags & (os.ST_NOSUID | os.ST_NODEV) ==
            os.ST_NOSUID | os.ST_NODEV and
        root_flags & os.ST_NOEXEC == 0 and
        temporary_flags & os.ST_RDONLY == 0 and
        temporary_flags & (os.ST_NOSUID | os.ST_NODEV | os.ST_NOEXEC) ==
            os.ST_NOSUID | os.ST_NODEV | os.ST_NOEXEC and
        os.stat(stage_path).st_dev != os.stat(stage_path.parent).st_dev and
        os.stat(temporary_path).st_dev != os.stat(stage_path).st_dev,
        reason)


def _release_causal_directory_set(
    files: tuple[RootfsRuntimeFile, ...],
) -> tuple[PurePosixPath, ...]:
    directories: set[PurePosixPath] = {PurePosixPath("tmp")}
    for entry in files:
        relative = _rootfs_relative_path(
            entry.logical_path,
            "ADMISSION_RELEASE_CAUSAL_ROOTFS_CONTRACT_INVALID")
        parent = relative.parent
        while parent != PurePosixPath("."):
            directories.add(parent)
            parent = parent.parent
    return tuple(sorted(directories, key=lambda item: (len(item.parts), str(item))))


def _create_release_causal_stage(
    *, rootfs_files: tuple[RootfsRuntimeFile, ...],
    verifier_path: Path = RELEASE_CAUSAL_VERIFIER,
    owner_uid: int | None = None,
    private_mount_namespace: bool = False,
) -> ReleaseCausalStage:
    """Build an exact child root from bound bytes; residue is never reused."""

    reason = "ADMISSION_RELEASE_CAUSAL_STAGE_CREATE_FAILED"
    if owner_uid is None:
        owner_uid = ROOT_UID
    _require(
        not private_mount_namespace or
        (owner_uid == ROOT_UID and os.geteuid() == 0 and os.getegid() == 0),
        reason)
    stage_path = _canonical_path(RELEASE_CAUSAL_STAGE, reason)
    _require(stage_path.parent != Path("/") and stage_path.name.startswith("."),
             reason)
    verifier_relative = _rootfs_relative_path(verifier_path, reason)
    relative_files = tuple(
        (_rootfs_relative_path(entry.logical_path, reason), entry)
        for entry in rootfs_files)
    _require(
        len({relative for relative, _entry in relative_files}) ==
            len(relative_files) and
        verifier_relative in {relative for relative, _entry in relative_files},
        reason)
    if private_mount_namespace:
        _require(
            all(relative.parts[0] != "tmp"
                for relative, _entry in relative_files), reason)
    directories = _release_causal_directory_set(rootfs_files)
    parent = _open_anchored_directory(stage_path.parent, reason)
    stage: int | None = None
    root_mounted = False
    temporary_mounted = False
    stage_created = False
    pinned_root_descriptor: int | None = None
    try:
        parent_before = _trusted_directory_identity(
            parent, expected_uid=owner_uid, reason=reason)
        try:
            os.stat(stage_path.name, dir_fd=parent, follow_symlinks=False)
        except FileNotFoundError:
            pass
        else:
            raise AdmissionError("ADMISSION_RELEASE_CAUSAL_STAGE_RESIDUE")
        os.mkdir(stage_path.name, 0o700, dir_fd=parent)
        stage_created = True
        os.fsync(parent)
        if private_mount_namespace:
            rootfs_bytes = sum(len(entry.payload) for entry in rootfs_files)
            rootfs_capacity = max(
                RELEASE_CAUSAL_MINIMUM_ROOTFS_BYTES,
                rootfs_bytes + max(
                    RELEASE_CAUSAL_MINIMUM_ROOTFS_BYTES,
                    rootfs_bytes // 4))
            _mount_release_causal_tmpfs(
                stage_path, source=b"hepta-release-causal-rootfs",
                flags=MS_NOSUID | MS_NODEV, size=rootfs_capacity,
                reason=reason)
            root_mounted = True
        stage = os.open(stage_path.name, DIRECTORY_FLAGS, dir_fd=parent)
        os.fchmod(stage, 0o700)
        _require(
            os.fstat(stage).st_uid == owner_uid and
            stat.S_IMODE(os.fstat(stage).st_mode) == 0o700, reason)
        for relative in directories:
            parent_relative = relative.parent
            directory = _open_release_causal_relative_directory(
                stage, parent_relative, reason)
            try:
                os.mkdir(relative.name, 0o700, dir_fd=directory)
                child = os.open(
                    relative.name, DIRECTORY_FLAGS, dir_fd=directory)
                try:
                    os.fchmod(child, 0o700)
                    _require(
                        os.fstat(child).st_uid == owner_uid and
                        stat.S_IMODE(os.fstat(child).st_mode) == 0o700,
                        reason)
                    os.fsync(child)
                finally:
                    os.close(child)
                os.fsync(directory)
            finally:
                os.close(directory)
        if private_mount_namespace:
            _mount_release_causal_tmpfs(
                stage_path / "tmp", source=b"hepta-release-causal-tmp",
                flags=MS_NOSUID | MS_NODEV | MS_NOEXEC,
                size=RELEASE_CAUSAL_TMPFS_BYTES, reason=reason)
            temporary_mounted = True
            _verify_release_causal_mounts(
                stage_path, read_only=False, reason=reason)
        staged_files: list[StagedRuntimeFile] = []
        for relative, entry in sorted(
                relative_files, key=lambda item: str(item[0])):
            directory = _open_release_causal_relative_directory(
                stage, relative.parent, reason)
            try:
                metadata = _write_release_causal_stage_file(
                    directory, relative.name, entry.payload, mode=entry.mode,
                    owner_uid=owner_uid)
                os.fsync(directory)
            finally:
                os.close(directory)
            staged_files.append(StagedRuntimeFile(
                relative, entry.payload, metadata, entry.mode))
        directory_metadata: dict[PurePosixPath, os.stat_result] = {}
        for relative in directories:
            directory = _open_release_causal_relative_directory(
                stage, relative, reason)
            try:
                os.fsync(directory)
                directory_metadata[relative] = os.fstat(directory)
            finally:
                os.close(directory)
        os.fsync(stage)
        root_metadata = os.fstat(stage)
        if private_mount_namespace:
            _remount_release_causal_root_read_only(stage_path, reason)
            _verify_release_causal_mounts(
                stage_path, read_only=True, reason=reason)
            pinned_root_descriptor = os.dup(stage)
            os.set_inheritable(pinned_root_descriptor, False)
            _require(
                _directory_identity(os.fstat(pinned_root_descriptor)) ==
                    _directory_identity(root_metadata), reason)
        os.fsync(parent)
        parent_active = _trusted_directory_identity(
            parent, expected_uid=owner_uid, reason=reason)
        result = ReleaseCausalStage(
            path=stage_path, verifier_relative_path=verifier_relative,
            owner_uid=owner_uid,
            parent_before_identity=parent_before,
            parent_active_identity=parent_active,
            root_metadata=root_metadata,
            directory_metadata=directory_metadata,
            files=tuple(staged_files),
            private_mount_namespace=private_mount_namespace,
            pinned_root_descriptor=pinned_root_descriptor)
    except (AdmissionError, OSError) as error:
        if pinned_root_descriptor is not None:
            os.close(pinned_root_descriptor)
            pinned_root_descriptor = None
        if stage is not None:
            os.close(stage)
            stage = None
        if temporary_mounted:
            try:
                _unmount_release_causal_path(stage_path / "tmp", reason)
            except AdmissionError:
                pass
            temporary_mounted = False
        if root_mounted:
            try:
                _unmount_release_causal_path(stage_path, reason)
            except AdmissionError:
                pass
            root_mounted = False
        if stage_created:
            try:
                os.rmdir(stage_path.name, dir_fd=parent)
                os.fsync(parent)
            except OSError:
                pass
        if isinstance(error, AdmissionError):
            raise
        raise AdmissionError(reason) from error
    finally:
        if stage is not None:
            os.close(stage)
        os.close(parent)
    result.reopen()
    return result


def _read_release_causal_stage_file(
    directory: int, expected: StagedRuntimeFile, *, owner_uid: int,
) -> None:
    reason = "ADMISSION_RELEASE_CAUSAL_STAGE_REBOUND"
    descriptor: int | None = None
    try:
        before = os.stat(
            expected.relative_path.name, dir_fd=directory,
            follow_symlinks=False)
        descriptor = os.open(
            expected.relative_path.name, READ_FLAGS, dir_fd=directory)
        opened = os.fstat(descriptor)
        _require(
            stat.S_ISREG(opened.st_mode) and opened.st_nlink == 1 and
            opened.st_uid == owner_uid and
            stat.S_IMODE(opened.st_mode) == expected.mode and
            _identity(before) == _identity(opened) ==
                _identity(expected.metadata), reason)
        chunks: list[bytes] = []
        remaining = len(expected.payload) + 1
        while remaining:
            chunk = os.read(descriptor, min(65536, remaining))
            if not chunk:
                break
            chunks.append(chunk)
            remaining -= len(chunk)
        _require(
            b"".join(chunks) == expected.payload and
            _identity(os.fstat(descriptor)) == _identity(expected.metadata),
            reason)
    except AdmissionError:
        raise
    except OSError as error:
        raise AdmissionError(reason) from error
    finally:
        if descriptor is not None:
            os.close(descriptor)


def _release_causal_expected_children(
    stage_binding: ReleaseCausalStage,
) -> Mapping[PurePosixPath, set[str]]:
    result: dict[PurePosixPath, set[str]] = {PurePosixPath("."): set()}
    for relative in stage_binding.directory_metadata:
        result.setdefault(relative, set())
        result.setdefault(relative.parent, set()).add(relative.name)
    for entry in stage_binding.files:
        result.setdefault(entry.relative_path.parent, set()).add(
            entry.relative_path.name)
    return result


def _reopen_release_causal_stage(stage_binding: ReleaseCausalStage) -> None:
    reason = "ADMISSION_RELEASE_CAUSAL_STAGE_REBOUND"
    if stage_binding.private_mount_namespace:
        _require(stage_binding.pinned_root_descriptor is not None, reason)
        try:
            pinned_metadata = os.fstat(stage_binding.pinned_root_descriptor)
        except OSError as error:
            raise AdmissionError(reason) from error
        _require(
            stat.S_ISDIR(pinned_metadata.st_mode) and
            _directory_identity(pinned_metadata) ==
                _directory_identity(stage_binding.root_metadata), reason)
        _verify_release_causal_private_propagation(reason)
        _verify_release_causal_mounts(
            stage_binding.path, read_only=True, reason=reason)
    parent = _open_anchored_directory(stage_binding.path.parent, reason)
    stage: int | None = None
    try:
        _require(
            _trusted_directory_identity(
                parent, expected_uid=stage_binding.owner_uid, reason=reason) ==
                    stage_binding.parent_active_identity, reason)
        before = os.stat(
            stage_binding.path.name, dir_fd=parent, follow_symlinks=False)
        stage = os.open(
            stage_binding.path.name, DIRECTORY_FLAGS, dir_fd=parent)
        opened = os.fstat(stage)
        _require(
            opened.st_uid == stage_binding.owner_uid and
            stat.S_IMODE(opened.st_mode) == 0o700 and
            _directory_identity(before) == _directory_identity(opened) ==
                _directory_identity(stage_binding.root_metadata), reason)
        for relative, names in _release_causal_expected_children(
                stage_binding).items():
            directory = _open_release_causal_relative_directory(
                stage, relative, reason)
            try:
                metadata = os.fstat(directory)
                expected_metadata = stage_binding.root_metadata \
                    if relative == PurePosixPath(".") else \
                    stage_binding.directory_metadata[relative]
                _require(
                    metadata.st_uid == stage_binding.owner_uid and
                    stat.S_IMODE(metadata.st_mode) == 0o700 and
                    _directory_identity(metadata) ==
                        _directory_identity(expected_metadata) and
                    set(os.listdir(directory)) == names,
                    reason)
            finally:
                os.close(directory)
        for entry in stage_binding.files:
            directory = _open_release_causal_relative_directory(
                stage, entry.relative_path.parent, reason)
            try:
                _read_release_causal_stage_file(
                    directory, entry, owner_uid=stage_binding.owner_uid)
            finally:
                os.close(directory)
    except AdmissionError:
        raise
    except OSError as error:
        raise AdmissionError(reason) from error
    finally:
        if stage is not None:
            os.close(stage)
        os.close(parent)


def _cleanup_release_causal_stage(
    stage_binding: ReleaseCausalStage,
) -> None:
    """Remove only a fully revalidated exact stage, never unknown residue."""

    reason = "ADMISSION_RELEASE_CAUSAL_STAGE_CLEANUP_FAILED"
    stage_binding.reopen()
    parent = _open_anchored_directory(stage_binding.path.parent, reason)
    stage: int | None = None
    try:
        _require(
            _trusted_directory_identity(
                parent, expected_uid=stage_binding.owner_uid, reason=reason) ==
                    stage_binding.parent_active_identity, reason)
        if stage_binding.private_mount_namespace:
            _verify_release_causal_private_propagation(reason)
            _verify_release_causal_mounts(
                stage_binding.path, read_only=True, reason=reason)
            _require(stage_binding.pinned_root_descriptor is not None, reason)
            try:
                os.close(stage_binding.pinned_root_descriptor)
            except OSError as error:
                raise AdmissionError(reason) from error
            _unmount_release_causal_path(stage_binding.path / "tmp", reason)
            _unmount_release_causal_path(stage_binding.path, reason)
            os.rmdir(stage_binding.path.name, dir_fd=parent)
            os.fsync(parent)
            try:
                os.stat(stage_binding.path.name, dir_fd=parent,
                        follow_symlinks=False)
            except FileNotFoundError:
                pass
            else:
                raise AdmissionError(reason)
            _require(
                _trusted_directory_identity(
                    parent, expected_uid=stage_binding.owner_uid,
                    reason=reason) == stage_binding.parent_before_identity,
                reason)
            return
        stage = os.open(
            stage_binding.path.name, DIRECTORY_FLAGS, dir_fd=parent)
        _require(
            _directory_identity(os.fstat(stage)) ==
                _directory_identity(stage_binding.root_metadata), reason)
        for entry in sorted(
                stage_binding.files,
                key=lambda item: (len(item.relative_path.parts), str(item.relative_path)),
                reverse=True):
            directory = _open_release_causal_relative_directory(
                stage, entry.relative_path.parent, reason)
            try:
                os.unlink(entry.relative_path.name, dir_fd=directory)
                os.fsync(directory)
            finally:
                os.close(directory)
        for relative in sorted(
                stage_binding.directory_metadata,
                key=lambda item: (len(item.parts), str(item)), reverse=True):
            directory = _open_release_causal_relative_directory(
                stage, relative.parent, reason)
            try:
                os.rmdir(relative.name, dir_fd=directory)
                os.fsync(directory)
            finally:
                os.close(directory)
        os.fsync(stage)
        os.close(stage)
        stage = None
        os.rmdir(stage_binding.path.name, dir_fd=parent)
        os.fsync(parent)
        try:
            os.stat(stage_binding.path.name, dir_fd=parent,
                    follow_symlinks=False)
        except FileNotFoundError:
            pass
        else:
            raise AdmissionError(reason)
        _require(
            _trusted_directory_identity(
                parent, expected_uid=stage_binding.owner_uid, reason=reason) ==
                    stage_binding.parent_before_identity, reason)
    except AdmissionError:
        raise
    except OSError as error:
        raise AdmissionError(reason) from error
    finally:
        if stage is not None:
            os.close(stage)
        os.close(parent)


def _bind_release_causal_dependencies(
    closure: InputSnapshot,
) -> tuple[BoundRuntimeFile, ...]:
    reason = "ADMISSION_RELEASE_CAUSAL_DEPENDENCY_INVALID"
    document = closure.document
    local = document["local_evidence"]
    evidence_root = _canonical_path(
        Path(document["retention_evidence"]["evidence_root"]), reason)
    records: dict[Path, dict[str, Any]] = {}
    for record in local["critical_files"]:
        path = _canonical_path(evidence_root / record["path"], reason)
        previous = records.setdefault(path, record)
        _require(previous == record, reason)
    for record in document["retention_evidence"]["inputs"].values():
        path = _canonical_path(Path(record["path"]), reason)
        previous = records.setdefault(path, record)
        _require(previous == record, reason)
    _require(closure.path not in records, reason)
    result: list[BoundRuntimeFile] = []
    bindings_by_path: dict[Path, BoundRuntimeFile] = {}
    for path, record in sorted(records.items(), key=lambda item: str(item[0])):
        mode = int(record["mode"], 8)
        binding = _bind_runtime_file(
            path, modes=frozenset({mode}),
            maximum=MAXIMUM_RELEASE_CAUSAL_DEPENDENCY_BYTES,
            reason="ADMISSION_RELEASE_CAUSAL_DEPENDENCY_REBOUND")
        _require(
            len(binding.payload) == record["size"] and
            digest_bytes(binding.payload).removeprefix("sha256:") ==
                record["sha256"],
            reason)
        result.append(binding)
        bindings_by_path[path] = binding
    trust_record = document["retention_evidence"]["inputs"].get(
        "trust_policy")
    _require(isinstance(trust_record, dict), reason)
    trust_path = _canonical_path(Path(trust_record["path"]), reason)
    trust_binding = bindings_by_path.get(trust_path)
    _require(trust_binding is not None, reason)
    trust_policy = strict_object(trust_binding.payload, reason)
    keys = trust_policy.get("keys") if isinstance(trust_policy, dict) else None
    _require(isinstance(keys, list) and keys, reason)
    key_paths: set[Path] = set()
    for key in keys:
        relative = key.get("public_key_path") if isinstance(key, dict) else None
        _require(
            isinstance(relative, str) and relative not in {"", ".", ".."} and
            not Path(relative).is_absolute() and "\0" not in relative and
            os.path.normpath(relative) == relative and
            ".." not in Path(relative).parts,
            reason)
        key_path = _canonical_path(trust_path.parent / relative, reason)
        _require(key_path not in records and key_path not in key_paths, reason)
        key_paths.add(key_path)
        result.append(_bind_runtime_file(
            key_path,
            modes=frozenset({0o400, 0o440, 0o444, 0o600, 0o640, 0o644}),
            maximum=64 * 1024,
            reason="ADMISSION_RELEASE_CAUSAL_TRUST_KEY_REBOUND"))
    return tuple(result)


def _release_causal_evidence_rootfs_files(
    closure: InputSnapshot,
    dependencies: tuple[BoundRuntimeFile, ...],
) -> tuple[RootfsRuntimeFile, ...]:
    reason = "ADMISSION_RELEASE_CAUSAL_EVIDENCE_ROOTFS_INVALID"
    result = [RootfsRuntimeFile(
        closure.path, closure.payload,
        stat.S_IMODE(closure.metadata.st_mode), None)]
    result.extend(RootfsRuntimeFile(
        binding.path, binding.payload,
        stat.S_IMODE(binding.metadata.st_mode), binding)
        for binding in dependencies)
    _require(
        len({entry.logical_path for entry in result}) == len(result) and
        all(entry.logical_path.is_absolute() and entry.mode & 0o022 == 0
            for entry in result), reason)
    return tuple(result)


def _run_release_causal_pinned_child(
    stage: ReleaseCausalStage, *, arguments: tuple[str, ...],
    environment: Mapping[str, str], timeout: int,
) -> subprocess.CompletedProcess[bytes]:
    """Fork, fchroot to the pinned root, and exec only staged Python bytes."""

    reason = "ADMISSION_RELEASE_CAUSAL_VERIFICATION_FAILED"
    stage.reopen()
    root_descriptor = stage.pinned_root_descriptor
    _require(
        stage.private_mount_namespace and root_descriptor is not None and
        arguments and arguments[0] == str(RELEASE_CAUSAL_PYTHON) and
        timeout > 0,
        reason)
    input_descriptor: int | None = None
    stdout_read: int | None = None
    stdout_write: int | None = None
    stderr_read: int | None = None
    stderr_write: int | None = None
    try:
        input_descriptor = os.open("/dev/null", os.O_RDONLY | CLOEXEC)
        stdout_read, stdout_write = os.pipe2(CLOEXEC)
        stderr_read, stderr_write = os.pipe2(CLOEXEC)
        pid = os.fork()
    except OSError as error:
        for descriptor in (
                input_descriptor, stdout_read, stdout_write,
                stderr_read, stderr_write):
            if descriptor is not None:
                os.close(descriptor)
        raise AdmissionError(reason) from error
    if pid == 0:
        try:
            os.dup2(input_descriptor, 0)
            os.dup2(stdout_write, 1)
            os.dup2(stderr_write, 2)
            os.fchdir(root_descriptor)
            os.chroot(".")
            os.chdir("/")
            root_metadata = os.stat("/", follow_symlinks=False)
            if (_directory_identity(root_metadata) !=
                    _directory_identity(stage.root_metadata)):
                os._exit(126)
            os.closerange(3, 1_048_576)
            os.execve(arguments[0], list(arguments), dict(environment))
        except BaseException:
            try:
                os.write(2, b"release-causal pinned child setup failed\n")
            except OSError:
                pass
            os._exit(127)
    assert stdout_read is not None and stdout_write is not None
    assert stderr_read is not None and stderr_write is not None
    assert input_descriptor is not None
    os.close(input_descriptor)
    os.close(stdout_write)
    os.close(stderr_write)
    os.set_blocking(stdout_read, False)
    os.set_blocking(stderr_read, False)
    buffers = {stdout_read: bytearray(), stderr_read: bytearray()}
    streams = set(buffers)
    status: int | None = None
    deadline = time.monotonic() + timeout
    try:
        while streams or status is None:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise subprocess.TimeoutExpired(arguments, timeout)
            readable, _writable, _exceptional = select.select(
                list(streams), [], [], min(remaining, 0.1))
            for descriptor in readable:
                try:
                    chunk = os.read(descriptor, 65536)
                except BlockingIOError:
                    continue
                if not chunk:
                    os.close(descriptor)
                    streams.remove(descriptor)
                    continue
                buffers[descriptor].extend(chunk)
                if len(buffers[descriptor]) > MAXIMUM_OUTPUT_BYTES:
                    raise AdmissionError(reason)
            if status is None:
                completed_pid, candidate_status = os.waitpid(pid, os.WNOHANG)
                if completed_pid == pid:
                    status = candidate_status
        stdout = bytes(buffers[stdout_read])
        stderr = bytes(buffers[stderr_read])
    except BaseException:
        try:
            os.kill(pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        try:
            os.waitpid(pid, 0)
        except ChildProcessError:
            pass
        for descriptor in tuple(streams):
            try:
                os.close(descriptor)
            except OSError:
                pass
        raise
    _require(status is not None, reason)
    return subprocess.CompletedProcess(
        list(arguments), os.waitstatus_to_exitcode(status), stdout, stderr)


def _run_release_causal_verifier(
    evaluation: Evaluation, *, runtime: ReleaseCausalRuntime,
) -> ReleaseCausalVerification:
    reason = "ADMISSION_RELEASE_CAUSAL_VERIFICATION_FAILED"
    closure = evaluation.snapshots.get("release_validation_receipt")
    _require(closure is not None, reason)
    dependencies = _bind_release_causal_dependencies(closure)
    verification = ReleaseCausalVerification(
        closure, runtime, dependencies)
    verification.reopen()
    evidence_files = _release_causal_evidence_rootfs_files(
        closure, dependencies)
    rootfs_files = tuple(sorted(
        (*runtime.rootfs_files, *evidence_files),
        key=lambda entry: str(entry.logical_path)))
    _require(
        len({entry.logical_path for entry in rootfs_files}) ==
            len(rootfs_files),
        "ADMISSION_RELEASE_CAUSAL_ROOTFS_PATH_COLLISION")
    _verify_release_causal_mapped_libc(rootfs_files)
    _enter_release_causal_private_mount_namespace()
    stage = _create_release_causal_stage(
        rootfs_files=rootfs_files, verifier_path=runtime.verifier.path,
        private_mount_namespace=True)
    try:
        stage.reopen()
        _require(
            os.geteuid() == 0 and os.getegid() == 0,
            "ADMISSION_RELEASE_CAUSAL_ROOT_ISOLATION_REQUIRED")
        argv = (
            str(RELEASE_CAUSAL_PYTHON), "-I", "-S", "-B",
            str(stage.child_verifier_path),
            "--closure", str(closure.path),
        )
        try:
            completed = _run_release_causal_pinned_child(
                stage, arguments=argv,
                environment=RELEASE_CAUSAL_ENVIRONMENT,
                timeout=RELEASE_CAUSAL_TIMEOUT_SECONDS)
        except (OSError, subprocess.TimeoutExpired) as error:
            raise AdmissionError(reason) from error
        _require(
            completed.returncode == 0 and
            completed.stdout ==
                RELEASE_CAUSAL_EXPECTED_STDOUT.encode("ascii") and
            completed.stderr == b"", reason)
        stage.reopen()
        _verify_release_causal_mapped_libc(rootfs_files)
        verification.reopen()
        return verification
    finally:
        stage.cleanup()


def _validate_agent_os_binary_causal_binding(
    evaluation: Evaluation, verification: ReleaseCausalVerification,
) -> None:
    """Bind Agent-OS executables to the independently verified package.

    The release causal child has already verified the complete runtime package
    and its external manifest from pinned bytes.  This consumer binds those
    exact manifest bytes to the four executables reported by the rootful gate,
    the installed-layout manifest, and the independently reverified native
    aggregate.  It deliberately adds no new admission input.
    """

    reason = "ADMISSION_AGENT_OS_BINARY_CAUSAL_BINDING_INVALID"
    closure = evaluation.snapshots.get("release_validation_receipt")
    agent_snapshot = evaluation.snapshots.get(
        "agent_os_rootful_gate_receipt")
    install_snapshot = evaluation.snapshots.get("install_manifest")
    native_snapshot = evaluation.snapshots.get("native_gate_receipt")
    _require(
        closure is not None and verification.closure is closure and
        agent_snapshot is not None and install_snapshot is not None and
        native_snapshot is not None,
        reason)
    verification.reopen()
    release = closure.document
    records = [
        record for record in release["local_evidence"]["critical_files"]
        if record.get("role") == "runtime-package-manifest"
    ]
    _require(len(records) == 1, reason)
    critical = records[0]
    evidence_root = _canonical_path(
        Path(release["retention_evidence"]["evidence_root"]), reason)
    manifest_path = _canonical_path(evidence_root / critical["path"], reason)
    dependencies = [
        binding for binding in verification.evidence_dependencies
        if binding.path == manifest_path
    ]
    _require(len(dependencies) == 1, reason)
    manifest_binding = dependencies[0]
    _require(
        len(manifest_binding.payload) == critical["size"] and
        digest_bytes(manifest_binding.payload).removeprefix("sha256:") ==
            critical["sha256"] and
        stat.S_IMODE(manifest_binding.metadata.st_mode) ==
            int(critical["mode"], 8),
        reason)
    runtime_manifest = strict_object(manifest_binding.payload, reason)
    _require(
        manifest_binding.payload == canonical_bytes(runtime_manifest) and
        set(runtime_manifest) == {
            "schema", "package_class", "release_version", "root",
            "source_ref", "vendor_ref", "target", "boundary",
            "file_count", "files_sha256", "files",
        } and
        runtime_manifest.get("schema") == "hepta.runtime-package.v1" and
        isinstance(runtime_manifest.get("files"), list) and
        runtime_manifest.get("file_count") ==
            len(runtime_manifest["files"]),
        reason)
    runtime_records: dict[str, dict[str, Any]] = {}
    for record in runtime_manifest["files"]:
        _require(
            isinstance(record, dict) and set(record) == {
                "path", "mode", "size", "sha256", "payload"} and
            type(record.get("path")) is str and
            type(record.get("mode")) is str and
            type(record.get("size")) is int and record["size"] > 0 and
            type(record.get("sha256")) is str and
            DIGEST.fullmatch(record["sha256"]) is not None and
            record["path"] not in runtime_records,
            reason)
        runtime_records[record["path"]] = record

    agent_records = {
        PurePosixPath(record["path"]).name: record
        for record in agent_snapshot.document["inputs"]
        if record["path"] not in AGENT_OS_SOURCE_MODES
    }
    installed_records = {
        record["path"]: record
        for record in install_snapshot.document["files"]
    }
    native_common = native_snapshot.document["common_closure"]
    _require(
        set(agent_records) == set(AGENT_OS_RUNTIME_BINARY_PATHS), reason)
    for binary, runtime_path in AGENT_OS_RUNTIME_BINARY_PATHS.items():
        runtime_record = runtime_records.get(runtime_path)
        installed_record = installed_records.get(runtime_path)
        agent_record = agent_records[binary]
        _require(
            isinstance(runtime_record, dict) and
            isinstance(installed_record, dict) and
            runtime_record["sha256"].removeprefix("sha256:") ==
                agent_record["sha256"] ==
                installed_record["sha256"].removeprefix("sha256:") and
            runtime_record["size"] == agent_record["size"] ==
                installed_record["size"] and
            runtime_record["mode"] == agent_record["mode"] ==
                installed_record["mode"] == "0755",
            reason)
        native_field = AGENT_OS_NATIVE_BINARY_DIGEST_FIELDS.get(binary)
        if native_field is not None:
            _require(
                agent_record["sha256"] == native_common[native_field],
                reason)


def _validate_installed_source_binding(
    evaluation: Evaluation, *, expected_source: str,
    admission_payload: bytes, zero_producer: LoadedProducerModule,
    release_verifier: BoundRuntimeFile,
    release_runtime_modules: tuple[BoundRuntimeFile, ...],
) -> None:
    reason = "ADMISSION_INSTALLED_SOURCE_BINDING_INVALID"
    baseline_snapshot = evaluation.snapshots.get("source_baseline")
    manifest_snapshot = evaluation.snapshots.get("install_manifest")
    _require(
        baseline_snapshot is not None and manifest_snapshot is not None and
        evaluation.receipt.get("source_baseline_sha256") == expected_source,
        reason)
    baseline = baseline_snapshot.document
    manifest = manifest_snapshot.document
    _require(
        baseline.get("source_manifest", {}).get("sha256") == expected_source and
        manifest.get("source_baseline_sha256") == expected_source,
        reason)
    source_records = {
        item["path"]: item for item in baseline["source_manifest"]["files"]}
    installed_records = {item["path"]: item for item in manifest["files"]}
    bindings = [
        (
            ADMISSION_VERIFIER_SOURCE,
            "usr/libexec/hepta-p1-paper-admission-verifier",
            admission_payload,
            "0755", "0755",
        ),
        (
            ZERO_SNAPSHOT_PRODUCER_SOURCE,
            "usr/libexec/hepta-p1-paper-zero-exposure-snapshot-producer",
            zero_producer.payload,
            "0755", "0755",
        ),
    ]
    runtime_payloads = {
        binding.path: binding.payload for binding in release_runtime_modules}
    for source_path, (
            installed_path, source_mode, installed_mode,
    ) in sorted(
            RELEASE_CAUSAL_SOURCE_INSTALL_PATHS.items()):
        installed = Path("/") / installed_path
        payload = release_verifier.payload \
            if installed == RELEASE_CAUSAL_VERIFIER else \
            runtime_payloads.get(installed)
        _require(payload is not None, reason)
        bindings.append((
            source_path, installed_path, payload, source_mode, installed_mode))
    for (
        source_path, installed_path, payload, source_mode, installed_mode,
    ) in bindings:
        source_record = source_records.get(source_path)
        installed_record = installed_records.get(installed_path)
        digest = digest_bytes(payload)
        _require(
            isinstance(source_record, dict) and
            source_record.get("sha256") == digest and
            source_record.get("size") == len(payload) and
            source_record.get("mode") == source_mode and
            isinstance(installed_record, dict) and
            installed_record.get("sha256") == digest and
            installed_record.get("size") == len(payload) and
            installed_record.get("mode") == installed_mode,
            reason)


def _validate_active_reservation_binding(
    evaluation: Evaluation, zero_producer: LoadedProducerModule,
    session: Any,
) -> None:
    reason = "ADMISSION_ZERO_RESERVATION_BINDING_INVALID"
    snapshot = evaluation.snapshots.get("zero_exposure_receipt")
    _require(snapshot is not None, reason)
    document = snapshot.document
    if getattr(session, "finalized", False) is True:
        try:
            session.reopen()
            terminal = session.tombstone.document
            lease_reference = session.lease.reference
        except Exception as error:
            raise AdmissionError(reason) from error
        _require(
            terminal.get("status") ==
                "ADMISSION_" + evaluation.receipt["status"] and
            document.get("host_authority_reservation") ==
                terminal.get("reservation_reference") and
            document.get("reservation_id") ==
                terminal.get("reservation_id") and
            document.get("reservation_generation") ==
                terminal.get("reservation_generation") and
            document.get("reservation_predecessor_finalization_body_sha256") ==
                terminal.get("predecessor_finalization_body_sha256") and
            document.get("reservation_prior_finalization_pointer_reference") ==
                terminal.get("prior_finalization_pointer_reference") and
            document.get("reservation_finalization_tombstone_path") ==
                str(session.tombstone.path) and
            document.get("reservation_finalization_current_pointer_path") ==
                str(session.pointer.path) and
            document.get("host_authority_lease") == lease_reference ==
                terminal.get("host_authority_lease"),
            reason)
        return
    try:
        reservation = session.reservation
        lease = session.lease
        reservation_document = reservation.document
        reservation_reference = zero_producer.module.reservation_reference(
            reservation)
        lease_reference = lease.reference
    except Exception as error:
        raise AdmissionError(reason) from error
    _require(
        document.get("host_authority_reservation") == reservation_reference and
        document.get("reservation_id") ==
            reservation_document.get("reservation_id") and
        document.get("reservation_generation") ==
            reservation_document.get("reservation_generation") and
        document.get("reservation_predecessor_finalization_body_sha256") ==
            reservation_document.get(
                "predecessor_finalization_body_sha256") and
        document.get("reservation_prior_finalization_pointer_reference") ==
            reservation_document.get("prior_finalization_pointer_reference") and
        document.get("reservation_lifecycle") ==
            reservation_document.get("reservation_lifecycle") and
        document.get("reservation_next_consumer") ==
            reservation_document.get("next_consumer") and
        document.get("reservation_finalization_tombstone_path") ==
            reservation_document.get("finalization_tombstone_path") and
        document.get("reservation_finalization_current_pointer_path") ==
            reservation_document.get("finalization_current_pointer_path") and
        document.get("reservation_boot_id") ==
            reservation_document.get("boot_id") and
        document.get("host_authority_lease") == lease_reference ==
            reservation_document.get("host_authority_lease") and
        document.get("reservation_lease_device") ==
            lease_reference.get("lease_device") and
        document.get("reservation_lease_inode") ==
            lease_reference.get("lease_inode") and
        document.get("reservation_continuity_verified") is True and
        document.get("reservation_finalization_tombstone_absent") is True,
        reason)


def _publish_or_resume_candidate(
    evaluation: Evaluation, output: Path, *, now_ms: int | None,
) -> Evaluation:
    """Publish once, or securely resume the same still-current candidate."""

    _require(type(now_ms) is int or now_ms is None, "ADMISSION_TIME_INVALID")
    now = _wall_clock_ms()
    _require(
        evaluation.receipt.get("evaluated_at_ms") <= now <
            evaluation.receipt.get("expires_at_ms"),
        "ADMISSION_CANDIDATE_NOT_CURRENT")
    try:
        payload, _metadata = secure_read(
            output, expected_uid=ROOT_UID, maximum=MAXIMUM_OUTPUT_BYTES,
            modes=frozenset({0o600}))
    except AdmissionError as error:
        if error.reason != "ADMISSION_INPUT_MISSING":
            raise
        publish_candidate(evaluation, output, expected_uid=ROOT_UID)
        return evaluation
    document = strict_object(payload, "ADMISSION_RESUME_CANDIDATE_INVALID")
    _require(
        payload == canonical_bytes(document),
        "ADMISSION_RESUME_CANDIDATE_INVALID")
    validate_output_receipt(document)
    expected = evaluation.receipt
    _require(
        document.get("status") == expected.get("status") and
        document.get("round") == expected.get("round") and
        document.get("domain") == expected.get("domain") and
        document.get("campaign_id") == expected.get("campaign_id") and
        document.get("source_baseline_sha256") ==
            expected.get("source_baseline_sha256") and
        document.get("strategy_sha256") == expected.get("strategy_sha256") and
        document.get("input_bindings") == expected.get("input_bindings") and
        document.get("findings") == expected.get("findings") and
        document.get("paper_test_admission_candidate") is
            (document.get("status") == "GO") and
        document.get("evaluated_at_ms") <= now <
            document.get("expires_at_ms"),
        "ADMISSION_RESUME_CANDIDATE_MISMATCH")
    return Evaluation(document, evaluation.snapshots)


def verify_and_publish_production(
    paths: Mapping[str, Path], output: Path, *, expected_domain: str,
    expected_campaign: str, expected_source: str,
    now_ms: int | None = None, _run_token: object | None = None,
) -> Evaluation:
    """Run the root-only, continuously locked production transaction."""

    _require(_run_token is CLI_RUN_TOKEN, "ADMISSION_CLI_RUN_REQUIRED")
    _require(os.geteuid() == ROOT_UID and os.getegid() == ROOT_GID,
             "ADMISSION_REAL_ROOT_REQUIRED")
    _digest(expected_source, "ADMISSION_EXPECTED_SOURCE_INVALID")
    admission_payload, admission_metadata = (
        _bind_running_admission_executable())
    zero_producer = _load_zero_snapshot_producer()
    try:
        session = zero_producer.module.open_admission_reservation_session(
            expected_source=expected_source,
            expected_campaign=expected_campaign,
            candidate_path=output,
            zero_exposure_receipt_path=paths["zero_exposure_receipt"],
            production_mode=ZERO_SNAPSHOT_PRODUCTION_MODE,
            expected_uid=ROOT_UID, expected_gid=ROOT_GID, now_ms=now_ms,
            _run_token=zero_producer.module.CLI_RUN_TOKEN)
    except Exception as error:
        raise AdmissionError("ADMISSION_RESERVATION_OPEN_FAILED") from error
    try:
        with session:
            # The authority lock is held before the first admission input read.
            zero_producer.reopen()
            _reopen_executable(
                INSTALLED_EXECUTABLE, admission_payload, admission_metadata)
            release_runtime = _bind_release_causal_runtime()
            release_verifier = release_runtime.verifier
            release_modules = release_runtime.runtime_modules
            evaluation = evaluate_candidate(
                paths, expected_domain=expected_domain,
                expected_campaign=expected_campaign, expected_uid=ROOT_UID,
                now_ms=now_ms)
            _validate_installed_source_binding(
                evaluation, expected_source=expected_source,
                admission_payload=admission_payload,
                zero_producer=zero_producer,
                release_verifier=release_verifier,
                release_runtime_modules=release_modules)
            _validate_active_reservation_binding(
                evaluation, zero_producer, session)
            release_causal = _run_release_causal_verifier(
                evaluation, runtime=release_runtime)
            _validate_agent_os_binary_causal_binding(
                evaluation, release_causal)
            session.reopen()
            release_causal.reopen()
            zero_producer.reopen()
            _reopen_executable(
                INSTALLED_EXECUTABLE, admission_payload, admission_metadata)
            evaluation = _publish_or_resume_candidate(
                evaluation, output, now_ms=None)
            assert_inputs_unchanged(
                evaluation.snapshots, expected_uid=ROOT_UID)
            session.reopen()
            release_causal.reopen()
            zero_producer.reopen()
            _reopen_executable(
                INSTALLED_EXECUTABLE, admission_payload, admission_metadata)
            assert_inputs_unchanged(
                evaluation.snapshots, expected_uid=ROOT_UID)
            current = _wall_clock_ms()
            _require(
                evaluation.receipt.get("evaluated_at_ms") <= current <
                    evaluation.receipt.get("expires_at_ms"),
                "ADMISSION_CANDIDATE_NOT_CURRENT")
            zero_snapshot = evaluation.snapshots["zero_exposure_receipt"]
            session.finalize(
                candidate_path=output,
                zero_exposure_receipt_path=paths["zero_exposure_receipt"],
                expected_candidate_reference={
                    "path": str(_canonical_path(
                        output, "ADMISSION_OUTPUT_PATH_INVALID")),
                    "file_sha256": digest_bytes(
                        canonical_bytes(evaluation.receipt)),
                    "body_sha256": evaluation.receipt["body_sha256"],
                },
                expected_zero_exposure_receipt_reference={
                    "path": str(zero_snapshot.path),
                    "file_sha256": zero_snapshot.file_sha256,
                    "body_sha256": zero_snapshot.body_sha256,
                },
                status=evaluation.receipt["status"], now_ms=None)
            release_causal.reopen()
            zero_producer.reopen()
            _reopen_executable(
                INSTALLED_EXECUTABLE, admission_payload, admission_metadata)
            return evaluation
    except AdmissionError:
        raise
    except Exception as error:
        raise AdmissionError("ADMISSION_FINALIZATION_FAILED") from error


def verify_and_publish(
    paths: Mapping[str, Path], output: Path, *, expected_domain: str,
    expected_campaign: str, expected_uid: int = ROOT_UID,
    now_ms: int | None = None,
) -> Evaluation:
    evaluation = evaluate_candidate(
        paths, expected_domain=expected_domain,
        expected_campaign=expected_campaign, expected_uid=expected_uid,
        now_ms=now_ms)
    publish_candidate(evaluation, output, expected_uid=expected_uid)
    return evaluation


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run", action="store_true", required=True)
    parser.add_argument("--source-baseline", type=Path, required=True)
    parser.add_argument("--install-manifest", type=Path, required=True)
    parser.add_argument("--install-receipt", type=Path, required=True)
    parser.add_argument("--install-pointer", type=Path, required=True)
    parser.add_argument("--profile-receipt", type=Path, required=True)
    parser.add_argument("--activation-receipt", type=Path, required=True)
    parser.add_argument("--p1-audit-receipt", type=Path, required=True)
    parser.add_argument("--release-validation-receipt", type=Path, required=True)
    parser.add_argument(
        "--agent-os-rootful-gate-receipt", type=Path, required=True)
    parser.add_argument("--dual-domain-gate-receipt", type=Path, required=True)
    parser.add_argument("--rootful-gate-receipt", type=Path, required=True)
    parser.add_argument("--p1-liveness-gate-receipt", type=Path, required=True)
    parser.add_argument("--network-gate-receipt", type=Path, required=True)
    parser.add_argument(
        "--hard-network-gate-receipt", type=Path, required=True)
    parser.add_argument("--native-gate-receipt", type=Path, required=True)
    parser.add_argument("--watch-handoff-receipt", type=Path, required=True)
    parser.add_argument("--zero-exposure-receipt", type=Path, required=True)
    parser.add_argument("--expected-domain", required=True)
    parser.add_argument("--expected-campaign", required=True)
    parser.add_argument(
        "--expected-source-baseline-sha256", required=True)
    parser.add_argument("--output", type=Path, required=True)
    return parser


def main(argv: list[str] | None = None) -> int:
    parsed = _parser().parse_args(argv)
    paths = {name: getattr(parsed, name) for name in INPUT_NAMES}
    try:
        evaluation = verify_and_publish_production(
            paths, parsed.output, expected_domain=parsed.expected_domain,
            expected_campaign=parsed.expected_campaign,
            expected_source=parsed.expected_source_baseline_sha256,
            _run_token=CLI_RUN_TOKEN)
    except AdmissionError as error:
        print(f"FAIL: {error.reason}", file=sys.stderr)
        return 4
    print(f"STATUS={evaluation.receipt['status']}")
    print(f"PAPER_TEST_ADMISSION_CANDIDATE={str(evaluation.receipt['paper_test_admission_candidate']).lower()}")
    print("PAPER_AUTHORIZED=false")
    print("ORDER_SUBMISSION_AUTHORIZED=false")
    return {"GO": 0, "NO_GO": 2, "HALT": 3}[evaluation.receipt["status"]]


if __name__ == "__main__":
    raise SystemExit(main())
