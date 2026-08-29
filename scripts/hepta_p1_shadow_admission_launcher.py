#!/usr/bin/env python3

"""Root-only, fail-closed P1 SHADOW admission launcher.

This is the sole orchestration boundary for a load-probe followed by one
formally admitted SHADOW campaign.  It never calls a PAPER, LIVE, order,
position, or direct-broker surface.  All authority lifecycle operations go
through the alpha WATCH custodian.

The frozen package must pre-create ``/var/lib/hepta/p1-admission`` and its
``private`` (0700), ``public`` (0755), and ``readers`` (0755) root-owned
children.  The launcher refuses to create or repair those trust anchors.
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass, field
import fcntl
import hashlib
import importlib.util
import json
import math
import os
from pathlib import Path
import re
import signal
import stat
import subprocess
import sys
import time
from typing import Any, Callable, Protocol

from hepta_agent_trust_domain import (
    TrustDomainRuntimeError,
    read_alpha_gateway_process_identity,
    read_alpha_gateway_process_profile,
    read_alpha_gateway_profile,
    read_alpha_gateway_socket,
)


ROOT_UID = 0
ROOT_GID = 0
READER_UID = 1000
READER_GID = 1000
WATCH_UID = 2104
WATCH_GID = 2104
DOMAIN_ID = "alpha"
DOMAIN_CONFIG = Path("/etc/heptatrader/trust-domains/alpha.json")
GATEWAY_PROFILE = Path("/etc/heptatrader/trust-domains/alpha.env")
GATEWAY_UNIT = "hepta-tool-gateway@alpha.service"
BROKER_EGRESS_UNIT = "hepta-broker-egress-policy.service"
ACTIVATION_RECONCILE_TIMER = (
    "hepta-p1-watch-activation-reconcile.timer")
BROKER_EGRESS_POLICY = Path("/usr/libexec/hepta-broker-egress-policy")
BROKER_INTERPRETER = Path("/usr/bin/python3.12")
BROKER_PAPER_IDENTITIES = Path(
    "/etc/heptatrader/hepta-agent-trust-domain-paper-identities-v1.json")
BROKER_CREDENTIAL_SOURCE = Path(
    "/run/credentials/hepta-broker-egress-policy.service/"
    "hepta-broker-egress-policy.py")
PREDECESSOR_ACTIVATION_SUCCESS_RECEIPT = Path(
    "/var/lib/hepta/shadow-observation/"
    "p1-watch-activation-round95-receipt-v3.json")
PREDECESSOR_ACTIVATION_SUCCESS_RECEIPT_FILE_SHA256 = (
    "sha256:c4b92e92bcdd55792e32fbe7f28a5399617352f7469e6661a09148efe6bdd5f3")
PREDECESSOR_ACTIVATION_SUCCESS_RECEIPT_BODY_SHA256 = (
    "sha256:2d433239397a9820af0080628f424f5b6985d01ed9b5748a2064f903e1a2ed80")
PREDECESSOR_ACTIVATION_SUCCESS_JOURNAL_SHA256 = (
    "sha256:4a080a21a471b3664351053d5948376e9f7b0f172f3ef69eb10413a4387b766e")
PREDECESSOR_ACTIVATION_FAILED_RECEIPT = Path(
    "/var/lib/hepta/shadow-observation/"
    "p1-watch-activation-round95-failed-receipt-v2.json")
PREDECESSOR_ACTIVATION_FAILED_RECEIPT_FILE_SHA256 = (
    "sha256:860cf9ab2005ebcc2f6d5a83e931ebe18e6a5764f502a503aa305fb009bff55d")
PREDECESSOR_ACTIVATION_FAILED_RECEIPT_BODY_SHA256 = (
    "sha256:a3097ec265d66cb6ad99db8555b777c3fd0009cbe7f85e453a1d7a8f126174ed")
PREDECESSOR_ACTIVATION_JOURNAL = Path(
    "/var/lib/heptatrader/p1-watch-activation/round95/journal")
PREDECESSOR_ACTIVATION_JOURNAL_SHA256 = (
    "sha256:7d18a341a2e6ae322acd1b477f6287686af090e4a35716dc496bb8ab0f1a698e")
ANCESTOR_ACTIVATION_FAILED_RECEIPT = Path(
    "/var/lib/hepta/shadow-observation/"
    "p1-watch-activation-failed-receipt-v1.json")
ANCESTOR_ACTIVATION_FAILED_RECEIPT_FILE_SHA256 = (
    "sha256:957559d6a0ae12433c3ec59aee5bc4707c4c8dda2af74a0babed8da65d7dba15")
ANCESTOR_ACTIVATION_FAILED_RECEIPT_BODY_SHA256 = (
    "sha256:22abc6d6316e9a0576e782957c886033acc50c1e97ba97d5a7a417b8274d03f7")
ANCESTOR_ACTIVATION_JOURNAL = Path(
    "/var/lib/heptatrader/p1-watch-activation/round86/journal")
ANCESTOR_ACTIVATION_JOURNAL_SHA256 = (
    "sha256:9b20db0e816e10dab879411ee9b255adae7d6760e159c6fbfb38b61447c8ffa6")
ACTIVATION_FAILED_RECEIPT = Path(
    "/var/lib/hepta/shadow-observation/"
    "p1-watch-activation-round114-failed-receipt-v3.json")
ACTIVATION_FAILED_RECEIPT_REPLACEMENT = ACTIVATION_FAILED_RECEIPT.with_name(
    ".p1-watch-activation-round114-failed-receipt-v3.replacement")
ACTIVATION_PENDING_RECEIPT_ARCHIVE = ACTIVATION_FAILED_RECEIPT.with_name(
    "p1-watch-activation-round114-pending-receipt-v3.json")
ACTIVATION_FAILURE_ARTIFACTS = (
    ACTIVATION_FAILED_RECEIPT,
    ACTIVATION_FAILED_RECEIPT_REPLACEMENT,
    ACTIVATION_PENDING_RECEIPT_ARCHIVE,
)
EXPECTED_GATEWAY_PROFILE_SHA256 = (
    "sha256:ffcde4c46237ecacb3c32603f3aca0ba1a51c5b353b4fd2e5ab2f42ca1470e3f")
EXPECTED_GATEWAY_PROFILE_VALUES = {
    "HEPTA_EXECUTION_REMOTE_MODE": "SIMULATOR",
    "HEPTA_TOOL_ACCOUNT": "SIM",
    "HEPTA_EXECUTION_DOMAIN_ID": "SIM:alpha",
    "HEPTA_TOOL_ALLOW_TRADE": "0",
    "HEPTA_TOOL_SESSION_TEMPLATES": "watch",
    "HEPTA_TOOL_CONTRACT_BINDINGS": "EUR.USD|EUR|CASH|IDEALPRO|USD",
}
PROFILE_DEPLOYMENT_RECEIPT = Path(
    "/var/lib/heptatrader/p1-watch-profile-receipts/round114-generation22.json")
PROFILE_DEPLOYMENT_RECEIPT_STAGING = PROFILE_DEPLOYMENT_RECEIPT.with_name(
    ".round114-generation22.json.hepta-p1-round114.tmp")
PROFILE_TRANSITION_RECEIPT = Path(
    "/var/lib/heptatrader/p1-watch-profile-receipts/"
    "round114-dormant-paper-to-watch.json")
PROFILE_TRANSITION_RECEIPT_SCHEMA = (
    "hepta.p1-watch-profile-dormant-paper-transition-receipt.v2")
PROFILE_TRANSITION_RECEIPT_VERSION = 2
PROFILE_TRANSITION_RECEIPT_FIELDS = frozenset({
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
PROFILE_TRANSITION_PREIMAGE = Path(
    "/var/lib/heptatrader/p1-watch-profile-backups/"
    "round114-dormant-paper-to-watch/preimage-evidence.json")
PROFILE_TRANSITION_PREIMAGE_SCHEMA = (
    "hepta.p1-watch-profile-transition-preimage-evidence.v1")
PROFILE_TRANSITION_PREIMAGE_VERSION = 1
PROFILE_TRANSITION_PREIMAGE_FIELDS = frozenset({
    "schema", "version", "status", "round", "domain", "transition_token",
    "created_at_ms", "target_before", "backup", "predecessor_profile_receipt",
    "preflight", "paper_authorized", "live_authorized",
    "mutation_attempted", "direct_broker_access", "shadow_install_evidence",
    "body_sha256",
})
LEGACY_PROFILE_DEPLOYMENT_RECEIPT = Path(
    "/var/lib/heptatrader/p1-watch-profile-receipts/round86.json")
LEGACY_PROFILE_BACKUP = Path(
    "/var/lib/heptatrader/p1-watch-profile-backups/round86/alpha.env")
LEGACY_PROFILE_RETAINED_TARGET = GATEWAY_PROFILE.with_name(
    ".alpha.env.hepta-p1-round86.tmp")
LEGACY_PROFILE_SHA256 = (
    "sha256:2397f4c86156adaa9dca0e929e727b827080312fd57ede3ffd1597d1bdc37ea1")
LEGACY_PROFILE_BYTES = 677
LEGACY_PROFILE_RECEIPT_FILE_SHA256 = (
    "sha256:3904f17a444fb7a6a482b187c081c9a8eba854d39dd476ff948477eb7b9376aa")
LEGACY_PROFILE_RECEIPT_BODY_SHA256 = (
    "sha256:17fcaee75ce5a3bc67f944b3d0fc5bc63512a39f4d85dc6e2b04f71af81da4ff")
LEGACY_PROFILE_RECEIPT_BYTES = 33103
PREDECESSOR_PROFILE_DEPLOYMENT_RECEIPT = Path(
    "/var/lib/heptatrader/p1-watch-profile-receipts/round95-generation20.json")
PREDECESSOR_PROFILE_RECEIPT_FILE_SHA256 = (
    "sha256:c1557c1fe0bbab68bfc0c85148f2dcb3b32a2c8b75da7b229296d1b99daebd67")
PREDECESSOR_PROFILE_RECEIPT_BODY_SHA256 = (
    "sha256:e09712acbfed117a47ad5e86c63bbfe638ec38d89d7579e85b47409b57728fb2")
PREDECESSOR_PROFILE_RECEIPT_BYTES = 58196
SHADOW_INSTALL_LOCK_PATH = Path("/var/lib/hepta/.shadow-runtime-install.lock")
SHADOW_CURRENT_INSTALL_POINTER_PATH = Path(
    "/var/lib/hepta/shadow-runtime-install-state/current-install-v1.json")
SHADOW_INSTALL_RECEIPT_PATH = Path(
    "/var/lib/hepta/shadow-runtime-install-receipts/"
    "hepta-p1-round114-generation22-passive.json")
SHADOW_INSTALL_MANIFEST_PATH = Path(
    "/var/lib/hepta/shadow-runtime-install-artifacts/"
    "hepta-p1-round114-generation22-shadow-runtime.manifest.json")
SHADOW_INSTALL_BACKUP_ROOT = Path(
    "/var/lib/hepta/shadow-runtime-backups/hepta-p1-round114-generation22-passive")
EXPECTED_SHADOW_INSTALL_GENERATION = 22
SHADOW_INSTALL_FILE_COUNT = 128
EXPECTED_PREDECESSOR_SHADOW_INSTALL_GENERATION = 21
EXPECTED_PREDECESSOR_CURRENT_INSTALL_POINTER_FILE_SHA256 = (
    "sha256:2beeb507fcafbbfc2c93d2e4756fddf0b27e9872733ff97d28af47006461d406")
SHADOW_DEFAULT_DENY_IDENTITY_SHA256 = (
    "sha256:4a94d555cad61a9de67b809cfae301eadd6ebf2511714c93343f10decb34e435")
SHADOW_INSTALLER = Path("/usr/libexec/hepta-shadow-host-installer")
SHADOW_INSTALLER_MEMBER = "usr/libexec/hepta-shadow-host-installer"
ADMISSION_LAUNCHER_MEMBER = (
    "usr/libexec/hepta-p1-shadow-admission-launcher")
PROFILE_DEPLOYER = Path("/usr/libexec/hepta-p1-watch-profile-deployer")
PROFILE_DEPLOYER_MEMBER = "usr/libexec/hepta-p1-watch-profile-deployer"
# This is a fixed activation-generation trust anchor, not a caller-selectable
# receipt.  The activation transaction owns publication at this exact path.
ACTIVATION_RECEIPT = Path(
    "/var/lib/hepta/shadow-observation/"
    "p1-watch-activation-round114-receipt-v4.json")
LEGACY_ACTIVATION_RECEIPT = Path(
    "/var/lib/hepta/shadow-observation/"
    "p1-watch-activation-receipt-v1.json")
PREDECESSOR_ACTIVATION_RECEIPT = Path(
    "/var/lib/hepta/shadow-observation/"
    "p1-watch-activation-receipt-v2.json")
GATEWAY_SOCKET = Path("/run/hepta-agent-alpha/tools.sock")
SUPERVISOR_SOCKET = Path(
    "/run/hepta-tool-gateway-alpha/session-supervisor.sock")
AUDIT_JOURNAL = Path("/var/lib/hepta-tool-gateway-alpha/session-audit.jsonl")
WATCH_SNAPSHOT = Path("/var/lib/hepta-shadow-watch-alpha/private/snapshot.json")
WATCH_EXPORT = Path("/run/hepta-shadow-watch-export-alpha")
WATCH_SESSIONS = Path("/run/hepta-agent-alpha/sessions")
WATCH_PRIVATE = Path("/var/lib/hepta-shadow-watch-alpha/private")
SESSION_BOOTSTRAP_LOCK = ".session-bootstrap.lock"
EVIDENCE_ROOT = Path("/var/lib/hepta/market-evidence")
EXPORT_ROOT = Path("/var/lib/hepta/shadow-observation")
SOURCE_BUNDLE = EXPORT_ROOT / "official-source-bundle.json"
STRATEGY = Path(
    "/usr/share/heptatrader/strategies/"
    "eurusd-confirmed-momentum-shadow-v2.json")
STATE_BASE = Path("/var/lib/hepta/p1-admission")
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
SHARED_EXECUTION_SOCKET_PATHS = (
    Path("/run/hepta-execution/execution.sock"),
    Path("/run/hepta-execution/events.sock"),
    Path("/run/hepta-execution-alpha/execution.sock"),
    Path("/run/hepta-execution-alpha/events.sock"),
)
# The execution/event sockets above are shared endpoints legitimately owned by
# SIMULATOR while every PAPER unit is inactive.  Their pathnames therefore do
# not establish PAPER residue.  The campaign operator socket is PAPER-only and
# must never survive a closed P1 boundary.
PAPER_OPERATOR_SOCKET_PATHS = (
    Path("/run/hepta-agent-alpha/campaign.sock"),
)

SYSTEMCTL = "/usr/bin/systemctl"
SYSTEMD_RUN = "/usr/bin/systemd-run"
CUSTODIAN = "/usr/libexec/hepta-shadow-watch-custodian"
BUILDER = "/usr/libexec/build-hepta-p1-observation-policy"
VALIDATOR = "/usr/libexec/hepta-p1-load-probe-validator"
HOST = "/usr/libexec/hepta-p1-shadow-host-controller"
READER = "/usr/libexec/hepta-p1-shadow-observer-controller"
OBSERVER = "/usr/libexec/hepta_bounded_shadow_observer.py"
COLLECTOR = "/usr/libexec/hepta-shadow-watch-collector"
EXPORTER = "/usr/libexec/hepta-shadow-watch-exporter"
HEPTACTL = "/usr/bin/heptactl"
GATEWAY = "/usr/libexec/hepta-tool-gatewayd"
CAPTURE = "/usr/libexec/hepta-official-source-capture"
LAUNCHER_EXECUTABLE = "/usr/libexec/hepta-p1-shadow-admission-launcher"
VERIFIER = "/usr/libexec/hepta-bounded-shadow-closure-verifier"
MARKET_CONTEXT_BUILDER = "/usr/libexec/hepta_market_context_builder.py"
MARKET_EVIDENCE_NORMALIZER = (
    "/usr/libexec/hepta_market_evidence_normalizer.py")
MARKET_OFFICIAL_SOURCE_EXTRACTOR = (
    "/usr/libexec/hepta_market_official_source_extractor.py")
MOMENTUM_STRATEGY = (
    "/usr/libexec/hepta_eurusd_confirmed_momentum_strategy.py")
MARKET_HISTORY = "/usr/libexec/hepta_shadow_market_history.py"
STRATEGY_RUNNER = "/usr/libexec/hepta_strategy_shadow_runner.py"
STRATEGY_CONTRACTS = "/usr/libexec/hepta_strategy_contracts.py"
DECISION_RECEIPT_VALIDATOR = (
    "/usr/libexec/validate_hepta_strategy_decision_receipt.py")
TRUST_DOMAIN_RUNTIME = "/usr/libexec/hepta_agent_trust_domain.py"

HELPERS = {
    "builder_sha256": Path(BUILDER),
    "validator_sha256": Path(VALIDATOR),
    "host_controller_sha256": Path(HOST),
    "reader_controller_sha256": Path(READER),
    "observer_sha256": Path(OBSERVER),
    "collector_sha256": Path(COLLECTOR),
    "exporter_sha256": Path(EXPORTER),
    "heptactl_sha256": Path(HEPTACTL),
    "gateway_sha256": Path(GATEWAY),
    "custodian_sha256": Path(CUSTODIAN),
    "capture_sha256": Path(CAPTURE),
    "launcher_sha256": Path(LAUNCHER_EXECUTABLE),
    "closure_verifier_sha256": Path(VERIFIER),
    "market_context_builder_sha256": Path(MARKET_CONTEXT_BUILDER),
    "market_evidence_normalizer_sha256": Path(MARKET_EVIDENCE_NORMALIZER),
    "market_official_source_extractor_sha256":
        Path(MARKET_OFFICIAL_SOURCE_EXTRACTOR),
    "momentum_strategy_sha256": Path(MOMENTUM_STRATEGY),
    "market_history_sha256": Path(MARKET_HISTORY),
    "strategy_runner_sha256": Path(STRATEGY_RUNNER),
    "strategy_contracts_sha256": Path(STRATEGY_CONTRACTS),
    "decision_receipt_validator_sha256": Path(DECISION_RECEIPT_VALIDATOR),
    "domain_config_sha256": DOMAIN_CONFIG,
    "gateway_profile_sha256": GATEWAY_PROFILE,
    "trust_domain_runtime_sha256": Path(TRUST_DOMAIN_RUNTIME),
}
HELPER_MODES = {
    field: (
        frozenset({0o600, 0o640, 0o644})
        if field == "domain_config_sha256" else
        frozenset({0o644})
        if field in {"strategy_contracts_sha256", "gateway_profile_sha256"} else
        frozenset({0o755})
    )
    for field in HELPERS
}
ALLOWED_EXECUTABLES = frozenset({
    SYSTEMCTL, SYSTEMD_RUN, CUSTODIAN, BUILDER, VALIDATOR, HOST,
    VERIFIER, str(BROKER_INTERPRETER),
})
BROKER_DENY_ALL_CHECK_COMMAND = (
    str(BROKER_INTERPRETER), "-I", "-S", str(BROKER_CREDENTIAL_SOURCE),
    "--check-deny-all",
)
SANITIZED_ENVIRONMENT = {
    "PATH": "/usr/sbin:/usr/bin:/sbin:/bin",
    "LANG": "C",
    "LC_ALL": "C",
    "PYTHONNOUSERSITE": "1",
}

MAXIMUM_OUTPUT_BYTES = 256 * 1024
MAXIMUM_JSON_BYTES = 4 * 1024 * 1024
WATCH_TTL_SECONDS = 3600
LOAD_PROBE_RUNS = 91
FORMAL_ITERATIONS = 241
POLICY_SLOT_INTERVAL_MS = 2 * 60 * 1000
POLICY_MINIMUM_WARMUP_MS = 210 * 60 * 1000
PROBE_DISPATCH_LEAD_MS = 20 * 60 * 1000
FORMAL_PREPARATION_LEAD_MS = 30 * 1000
FORMAL_START_CLOCK_TOLERANCE_MS = 60 * 1000
FORMAL_START_MAXIMUM_CLOCK_DRIFT_MS = 1_000
POLICY_MAXIMUM_LATENESS_MS = 60_000
VERIFIER_TIMEOUT_SECONDS = 90
CAPTURE_LEAD_SECONDS = 180
CAPTURE_TIMEOUT_SECONDS = 150
ADMISSION_MAXIMUM_AGE_MS = 60_000
TERMINAL_HEARTBEAT_MAXIMUM_AGE_MS = 15_000
MAXIMUM_POLICY_TIMESTAMP_MS = (1 << 63) - 1

DIGEST = re.compile(r"sha256:[0-9a-f]{64}")
BOOT_ID = re.compile(
    r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-"
    r"[0-9a-f]{4}-[0-9a-f]{12}")
PROBE_CAMPAIGN = re.compile(
    r"hepta-p1-shadow-load-probe-round([1-9][0-9]*)-([0-9]{8})")
FORMAL_CAMPAIGN = re.compile(
    r"hepta-p1-shadow-soak-round([1-9][0-9]*)-([0-9]{8})")
POLICY_IDENTIFIER = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,127}")
PERMISSION_FIELDS = frozenset({
    "paper_authorized", "live_authorized", "mutation_authorized",
    "mutation_attempted", "direct_broker_access",
})
ACTIVATION_RECEIPT_FIELDS = frozenset({
    "schema", "version", "status", "round", "domain", "started_at_ms",
    "completed_at_ms", "boot_id", "profile_deployment_receipt_path",
    "profile_deployment_receipt_file_sha256",
    "profile_deployment_receipt_body_sha256", "profile_sha256",
    "profile_bytes", "journal_sha256", "broker_before", "broker_after",
    "gateway_after", "reconcile_timer", "paper_units", "kill_switch_engaged",
    "watch_boundary", "stale_bundles", "systemctl_mutations",
    "fresh_activation_transaction", "gateway_activated",
    "gateway_profile_loaded", "gateway_contract_binding_loaded",
    "broker_loaded_source_attested",
    "broker_deny_all_continuity_attested", "watch_authority_provisioned",
    "campaign_launched", "paper_authorized", "live_authorized",
    "mutation_attempted", "direct_broker_access",
    "admission_prerequisite_satisfied", "paper_prerequisite_satisfied",
    "shadow_install_evidence", "predecessor_activation_success",
    "predecessor_activation_failure",
    "body_sha256",
})
PREDECESSOR_ACTIVATION_SUCCESS_RECEIPT_FIELDS = (
    ACTIVATION_RECEIPT_FIELDS - frozenset({"predecessor_activation_success"}))
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
PREDECESSOR_FAILED_RECEIPT_FIELDS = frozenset({
    "schema", "version", "revision", "status", "round", "domain",
    "reason", "completed_at_ms", "quarantine", "previous_failed_receipt",
    "predecessor_activation_failure",
    "paper_authorized", "live_authorized", "mutation_attempted",
    "direct_broker_access", "body_sha256",
})
PREDECESSOR_PREVIOUS_FAILED_RECEIPT_FIELDS = frozenset({
    "path", "file_sha256", "body_sha256", "device", "inode", "mode",
    "nlink", "uid", "gid", "bytes", "mtime_ns", "ctime_ns",
})
PREDECESSOR_ACTIVATION_PHASES = (
    "PREPARED", "TIMER_ENABLE_INTENT", "TIMER_ARMED",
    "STALE_QUARANTINE_INTENT", "STALE_CLEAN", "DAEMON_RELOAD_INTENT",
    "MANAGER_RELOADED", "BROKER_STOP_INTENT",
    "BROKER_STOPPED_DENY_ALL", "BROKER_START_INTENT",
    "BROKER_ACTIVE_DENY_ALL_ATTESTED", "GATEWAY_UNMASK_INTENT",
    "GATEWAY_UNMASKED_RELOADED", "GATEWAY_START_INTENT",
    "GATEWAY_ACTIVE_ATTESTED", "COMMIT_INTENT",
)
PREDECESSOR_QUARANTINE_PHASES = (
    "QUARANTINE_INTENT", "GATEWAY_MASKED_STOPPED", "BROKER_DENY_ALL",
    "AUTHORITY_EMPTY", "FAILED_CLOSED",
)
PROFILE_DEPLOYMENT_RECEIPT_FIELDS = frozenset({
    "schema", "version", "status", "round", "domain", "started_at_ms",
    "finished_at_ms", "target_path", "receipt_staging_path",
    "target_before", "target_after", "target_final", "legacy_receipt",
    "legacy_backup", "legacy_retained_target", "preflight_before",
    "preflight_after", "preflight_final", "profile_content_changed",
    "target_written", "target_replaced", "services_started",
    "services_stopped", "services_restarted", "campaign_launched", "paper_authorized",
    "live_authorized", "mutation_attempted", "direct_broker_access",
    "body_sha256", "activation_receipt_eligible",
    "preflight_reusable_for_activation", "broker_loaded_source_attested",
    "broker_deny_all_continuity_attested",
    "fresh_activation_transaction_required",
    "shadow_install_evidence", "predecessor_profile_receipt",
    "dormant_paper_to_watch_transition_receipt",
})
PROFILE_FILE_EVIDENCE_FIELDS = frozenset({
    "path", "sha256", "bytes", "device", "inode", "mode", "nlink",
    "uid", "gid", "mtime_ns", "ctime_ns",
})
PROFILE_LEGACY_RECEIPT_EVIDENCE_FIELDS = frozenset({
    *PROFILE_FILE_EVIDENCE_FIELDS, "body_sha256",
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
ACTIVATION_BROKER_FIELDS = frozenset({
    "unit", "active_state", "sub_state", "main_pid", "invocation_id",
    "exec_main_start_timestamp_monotonic_us", "process_starttime_ticks",
    "interpreter_path", "interpreter_sha256", "credential_source_path",
    "credential_source_sha256", "installed_source_path",
    "installed_source_sha256", "cmdline_sha256", "status_text",
    "tasks_current", "deny_all_policy_sha256", "authorized_connectors",
    "authorized_uids", "protected_ports", "unit_contract_sha256",
})
ACTIVATION_GATEWAY_FIELDS = frozenset({
    "unit", "active_state", "sub_state", "gateway_main_pid",
    "gateway_invocation_id",
    "gateway_exec_main_start_timestamp_monotonic_us",
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
ACTIVATION_WATCH_BOUNDARY_FIELDS = frozenset({
    "export_absent", "sessions_authority_count", "private_authority_count",
    "custodian_transaction_absent",
    "session_bootstrap_idle_lock_observed",
})
ACTIVATION_RECONCILE_TIMER_FIELDS = frozenset({
    "unit", "load_state", "active_state", "sub_state", "job",
    "unit_file_state", "unit_contract_sha256",
})
ACTIVATION_RECONCILE_TIMER_MANAGER_FIELDS = (
    "LoadState", "ActiveState", "SubState", "Job", "UnitFileState",
)
ACTIVATION_BROKER_UNIT_CONTRACT_FIELDS = (
    "LoadState", "ActiveState", "SubState", "MainPID", "InvocationID",
    "ExecMainStartTimestampMonotonic", "Type", "NotifyAccess",
    "StatusText", "TasksCurrent", "NRestarts", "ExecStart",
    "LoadCredential",
)
ACTIVATION_GATEWAY_UNIT_CONTRACT_FIELDS = (
    "LoadState", "ActiveState", "SubState", "MainPID", "InvocationID",
    "ExecMainStartTimestampMonotonic", "ExecStart", "EnvironmentFiles",
    "BindsTo", "After",
)
STALE_TERMINAL_RECEIPT_SHA256 = {
    110: "sha256:3fe92cd29c23b78166fc557be2f88c29df1a41aec716958a3061331b3a1e6a35",
    112: "sha256:a0c61e38581f8918d540d7940bea2ebfe49e9a8263a40b2a3a95130f59e5c24d",
}
ENVIRONMENT_FIELDS = frozenset({
    "boot_id", "audit_journal_device", "audit_journal_inode",
    "collector_sha256", "exporter_sha256", "heptactl_sha256",
    "gateway_sha256", "custodian_sha256", "observer_sha256",
    "host_controller_sha256", "domain_config_sha256",
    "gateway_profile_sha256", "gateway_process_profile_sha256",
    "gateway_invocation_id", "gateway_main_pid",
    "gateway_exec_main_start_timestamp_monotonic_us",
    "gateway_socket_device", "gateway_socket_inode",
})
ADMISSION_FIELDS = frozenset({
    "schema", "version", "status", "campaign_id",
    "prospective_campaign_id", "prospective_policy_path",
    "authority_marker_path", "validated_at_ms",
    "host_receipt_body_sha256",
    "observer_controller_status_body_sha256",
    "observer_state_body_sha256", "history_head_body_sha256",
    "probe_execution_service_epoch",
    "probe_execution_service_fencing_generation",
    "probe_first_collection_started_at_ms", "probe_first_exported_at_ms",
    "probe_first_record_sha256", "probe_first_snapshot_body_sha256",
    "probe_last_collection_started_at_ms", "probe_last_exported_at_ms",
    "probe_last_record_sha256", "probe_last_snapshot_body_sha256",
    "probe_history_record_bytes", "probe_audit_cursor_sequence",
    "probe_audit_expected_previous_sha256", "sample_count",
    "collection_cadence_ms", "maximum_collection_jitter_ms",
    "missed_sample_count", "missed_decision_count", "environment",
    "paper_authorized", "live_authorized", "mutation_authorized",
    "direct_broker_access", "body_sha256",
})
FORMAL_POLICY_FIELDS = frozenset({
    "schema", "version", "campaign_id", "campaign_sha256",
    "strategy_id", "strategy_version", "strategy_sha256",
    "valid_after_ms", "expires_at_ms", "slot_interval_ms",
    "maximum_iterations", "maximum_lateness_ms", "shadow_only",
    "paper_authorized", "live_authorized", "mutation_attempted",
    "direct_broker_access", "body_sha256",
})
CONTROLLER_STATUS_FIELDS = frozenset({
    "schema", "version", "campaign_id", "controller_pid",
    "controller_uid", "controller_gid", "state", "started_at_ms",
    "updated_at_ms", "observer_invocations",
    "last_export_receipt_body_sha256", "last_snapshot_body_sha256",
    "last_lease_generation", "locked_execution_service_epoch",
    "locked_execution_service_fencing_generation", "observer_status",
    "observer_outcome", "completed_iterations", "reason",
    "paper_authorized", "live_authorized", "mutation_attempted",
    "direct_broker_access", "body_sha256",
})
OBSERVER_STATE_FIELDS = frozenset({
    "schema", "version", "campaign_id", "campaign_sha256",
    "policy_sha256", "policy_body_sha256", "strategy_id",
    "strategy_version", "strategy_sha256", "status",
    "collection_cadence_ms", "maximum_collection_jitter_ms",
    "valid_after_ms", "expires_at_ms", "slot_interval_ms",
    "maximum_iterations", "maximum_lateness_ms", "segment_index",
    "segment_status", "segment_record_count",
    "segment_history_head_sha256", "last_collection_started_at_ms",
    "last_generated_at_ms", "last_snapshot_body_sha256",
    "last_watch_generation", "last_lease_receipt_body_sha256",
    "last_lease_receipt_file_sha256", "completed_iterations",
    "last_receipt_sha256", "missed_sample_count",
    "missed_decision_count", "sample_count", "accounted_payload_bytes",
    "accounted_payload_files", "accounted_payload_accumulator",
    "last_storage_audit_sample_count", "last_storage_audit_accumulator",
    "final_audit_receipt_sha256", "final_audit_segment_count",
    "audit_events", "paper_authorized", "live_authorized",
    "mutation_attempted", "direct_broker_access", "body_sha256",
})
FORMAL_RESULT_FIELDS = frozenset({
    "schema", "status", "campaign_id", "lease_generation",
    "collector_runs", "completed_iterations", "reader_completion",
    "close_result",
    "paper_authorized", "live_authorized", "mutation_authorized",
    "direct_broker_access",
})
READER_COMPLETION_FIELDS = frozenset({
    "reader_unit", "reader_pid", "acknowledged_at_ms",
    "controller_status_file_sha256", "controller_status_body_sha256",
    "observer_state_file_sha256", "observer_state_body_sha256",
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
    "receipt_body_sha256", "receipt_file_sha256",
    "extractor_code_sha256", "semantic_output_sha256",
    "completeness_sha256", "raw_payloads_verified",
})
LAUNCHER_IDENTITY_FIELDS = frozenset({
    "unit", "invocation_id", "main_pid", "type", "restart",
    "remain_after_exit", "user", "group", "exec_start",
    "environment", "launcher_sha256",
    "conflicts",
})
PAPER_CONFLICTS_PROPERTY = "--property=Conflicts=" + " ".join(PAPER_UNITS)
TRANSIENT_ENVIRONMENT_ARGUMENTS = tuple(
    f"--setenv={field}={value}"
    for field, value in SANITIZED_ENVIRONMENT.items()
)


class LauncherError(RuntimeError):
    """Stable launcher failure."""


class LauncherSignal(LauncherError):
    """Terminal signal raised while launcher-owned state may exist."""

    def __init__(self, signum: int) -> None:
        super().__init__(f"P1_LAUNCHER_SIGNAL_{signum}")
        self.signum = signum


def _require(condition: bool, reason: str) -> None:
    if not condition:
        raise LauncherError(reason)


def canonical_bytes(value: Any) -> bytes:
    try:
        return (
            json.dumps(
                value, ensure_ascii=True, allow_nan=False, sort_keys=True,
                separators=(",", ":")) + "\n"
        ).encode("ascii")
    except (TypeError, ValueError, UnicodeError) as error:
        raise LauncherError("P1_LAUNCHER_CANONICALIZATION_FAILED") from error


def digest_bytes(contents: bytes) -> str:
    return "sha256:" + hashlib.sha256(contents).hexdigest()


def seal(body: dict[str, Any]) -> dict[str, Any]:
    return {**body, "body_sha256": digest_bytes(canonical_bytes(body))}


def _pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        _require(key not in result, "P1_LAUNCHER_DUPLICATE_JSON_KEY")
        result[key] = value
    return result


def _reject_permissions(value: Any) -> None:
    if isinstance(value, dict):
        for key, child in value.items():
            if key in PERMISSION_FIELDS:
                _require(child is False, "P1_LAUNCHER_PERMISSION_NOT_FALSE")
            _reject_permissions(child)
    elif isinstance(value, list):
        for child in value:
            _reject_permissions(child)


def _validate_shadow_install_evidence(value: Any, reason: str) -> None:
    _require(
        isinstance(value, dict) and
        set(value) == SHADOW_INSTALL_EVIDENCE_FIELDS and
        value.get("schema") ==
            "hepta.shadow-runtime-install-consumption-evidence.v3" and
        type(value.get("version")) is int and value["version"] == 3 and
        value.get("receipt_path") == str(SHADOW_INSTALL_RECEIPT_PATH) and
        value.get("manifest_path") == str(SHADOW_INSTALL_MANIFEST_PATH) and
        value.get("domain") == DOMAIN_ID and
        value.get("backup_root") == str(SHADOW_INSTALL_BACKUP_ROOT) and
        value.get("current_install_pointer_path") ==
            str(SHADOW_CURRENT_INSTALL_POINTER_PATH) and
        type(value.get("install_generation")) is int and
        value["install_generation"] == EXPECTED_SHADOW_INSTALL_GENERATION and
        type(value.get("predecessor_install_generation")) is int and
        value["predecessor_install_generation"] ==
            EXPECTED_PREDECESSOR_SHADOW_INSTALL_GENERATION and
        value.get("predecessor_current_install_pointer_file_sha256") ==
            EXPECTED_PREDECESSOR_CURRENT_INSTALL_POINTER_FILE_SHA256 and
        type(value.get("installed_file_count")) is int and
        value["installed_file_count"] == SHADOW_INSTALL_FILE_COUNT and
        value.get("default_deny_identity_sha256") ==
            SHADOW_DEFAULT_DENY_IDENTITY_SHA256 and
        value.get("lock_mode") == "exclusive" and
        value.get("verified_under_lock") is True and
        value.get("paper_authorized") is False and
        value.get("live_authorized") is False and
        value.get("mutation_attempted") is False and
        value.get("direct_broker_access") is False,
        reason)
    for field in (
            "receipt_file_sha256", "receipt_body_sha256",
            "manifest_file_sha256", "archive_sha256",
            "source_baseline_sha256", "installer_sha256",
            "installed_paths_sha256", "closure_sha256",
            "current_install_pointer_file_sha256",
            "predecessor_current_install_pointer_file_sha256"):
        _require(
            type(value.get(field)) is str and
            DIGEST.fullmatch(value[field]) is not None,
            reason)
    lock = value.get("transaction_lock")
    _require(
        isinstance(lock, dict) and set(lock) == {
            "path", "device", "inode", "nlink", "uid", "gid", "mode",
            "size", "mtime_ns", "ctime_ns", "created_during_transaction",
            "persistent", "held_during_transaction"} and
        lock.get("path") == str(SHADOW_INSTALL_LOCK_PATH) and
        type(lock.get("device")) is int and lock["device"] >= 0 and
        type(lock.get("inode")) is int and lock["inode"] > 0 and
        type(lock.get("nlink")) is int and lock["nlink"] == 1 and
        type(lock.get("uid")) is int and lock["uid"] == ROOT_UID and
        type(lock.get("gid")) is int and lock["gid"] == ROOT_GID and
        lock.get("mode") == "0600" and
        type(lock.get("size")) is int and lock["size"] == 0 and
        type(lock.get("mtime_ns")) is int and lock["mtime_ns"] >= 0 and
        type(lock.get("ctime_ns")) is int and lock["ctime_ns"] >= 0 and
        type(lock.get("created_during_transaction")) is bool and
        lock.get("persistent") is True and
        lock.get("held_during_transaction") is True,
        reason)


def _valid_predecessor_deny_all(value: Any) -> bool:
    return (
        isinstance(value, dict) and set(value) == {
            "policy_sha256", "authorized_connectors", "authorized_uids",
            "protected_ports"} and
        isinstance(value.get("policy_sha256"), str) and
        DIGEST.fullmatch(value["policy_sha256"]) is not None and
        value.get("authorized_connectors") == 0 and
        value.get("authorized_uids") == [] and
        value.get("protected_ports") == 4)


def _validate_predecessor_previous_failed_receipt(
    value: Any,
    reason: str,
) -> None:
    _require(
        isinstance(value, dict) and
        set(value) == PREDECESSOR_PREVIOUS_FAILED_RECEIPT_FIELDS and
        value.get("path") == str(PREDECESSOR_ACTIVATION_FAILED_RECEIPT) and
        isinstance(value.get("file_sha256"), str) and
        DIGEST.fullmatch(value["file_sha256"]) is not None and
        isinstance(value.get("body_sha256"), str) and
        DIGEST.fullmatch(value["body_sha256"]) is not None and
        all(type(value.get(field)) is int for field in (
            "device", "inode", "mode", "nlink", "uid", "gid", "bytes",
            "mtime_ns", "ctime_ns")) and
        value["device"] >= 0 and value["inode"] > 0 and
        stat.S_ISREG(value["mode"]) and
        stat.S_IMODE(value["mode"]) == 0o600 and
        value["nlink"] == 1 and value["uid"] == ROOT_UID and
        value["gid"] == ROOT_GID and 0 < value["bytes"] <= MAXIMUM_JSON_BYTES and
        value["mtime_ns"] >= 0 and value["ctime_ns"] >= 0,
        reason)


def _validate_predecessor_activation_success_evidence(
    value: Any,
    reason: str,
) -> None:
    _require(
        isinstance(value, dict) and
        set(value) == PREDECESSOR_ACTIVATION_SUCCESS_FIELDS and
        value.get("receipt_path") ==
            str(PREDECESSOR_ACTIVATION_SUCCESS_RECEIPT) and
        value.get("receipt_file_sha256") ==
            PREDECESSOR_ACTIVATION_SUCCESS_RECEIPT_FILE_SHA256 and
        value.get("receipt_body_sha256") ==
            PREDECESSOR_ACTIVATION_SUCCESS_RECEIPT_BODY_SHA256 and
        value.get("receipt_schema") ==
            "hepta.p1-watch-activation-receipt.v3" and
        value.get("receipt_version") == 3 and
        value.get("receipt_status") == "WATCH_GATEWAY_ACTIVATED" and
        value.get("receipt_round") == 95 and
        value.get("receipt_domain") == DOMAIN_ID and
        all(type(value.get(field)) is int for field in (
            "receipt_device", "receipt_inode", "receipt_mode",
            "receipt_nlink", "receipt_uid", "receipt_gid", "receipt_bytes",
            "receipt_mtime_ns", "receipt_ctime_ns")) and
        value["receipt_device"] >= 0 and value["receipt_inode"] > 0 and
        stat.S_ISREG(value["receipt_mode"]) and
        stat.S_IMODE(value["receipt_mode"]) == 0o600 and
        value["receipt_nlink"] == 1 and value["receipt_uid"] == ROOT_UID and
        value["receipt_gid"] == ROOT_GID and
        0 < value["receipt_bytes"] <= MAXIMUM_JSON_BYTES and
        value["receipt_mtime_ns"] >= 0 and value["receipt_ctime_ns"] >= 0,
        reason)


def _validate_predecessor_activation_success_document(
    document: dict[str, Any],
    reason: str,
) -> None:
    _require(
        set(document) == PREDECESSOR_ACTIVATION_SUCCESS_RECEIPT_FIELDS and
        document.get("schema") ==
            "hepta.p1-watch-activation-receipt.v3" and
        document.get("version") == 3 and
        document.get("status") == "WATCH_GATEWAY_ACTIVATED" and
        document.get("round") == 95 and document.get("domain") == DOMAIN_ID and
        document.get("body_sha256") ==
            PREDECESSOR_ACTIVATION_SUCCESS_RECEIPT_BODY_SHA256 and
        document.get("journal_sha256") ==
            PREDECESSOR_ACTIVATION_SUCCESS_JOURNAL_SHA256 and
        all(document.get(field) is False for field in (
            "paper_authorized", "live_authorized", "mutation_attempted",
            "direct_broker_access", "watch_authority_provisioned",
            "campaign_launched", "paper_prerequisite_satisfied")) and
        document.get("admission_prerequisite_satisfied") is True and
        document.get("fresh_activation_transaction") is True and
        document.get("gateway_activated") is True and
        document.get("gateway_profile_loaded") is True and
        document.get("gateway_contract_binding_loaded") is True,
        reason)
    _validate_ancestor_activation_failure_evidence(
        document.get("predecessor_activation_failure"), reason)


def _validate_predecessor_failed_receipt_document(
    document: dict[str, Any],
    reason: str,
) -> None:
    quarantine = document.get("quarantine")
    _require(
        set(document) == PREDECESSOR_FAILED_RECEIPT_FIELDS and
        document.get("schema") ==
            "hepta.p1-watch-activation-failed-receipt.v2" and
        document.get("version") == 2 and
        document.get("revision") == 1 and
        document.get("status") == "FAILED_CLOSED" and
        document.get("round") == 95 and document.get("domain") == DOMAIN_ID and
        type(document.get("completed_at_ms")) is int and
        document["completed_at_ms"] >= 0 and
        isinstance(document.get("reason"), str) and
        re.fullmatch(r"[A-Z][A-Z0-9_]{0,255}", document["reason"]) is not None and
        all(document.get(field) is False for field in (
            "paper_authorized", "live_authorized", "mutation_attempted",
            "direct_broker_access")) and
        isinstance(quarantine, dict) and
        set(quarantine) == {"errors", "deny_all", "complete"} and
        quarantine.get("errors") == [] and quarantine.get("complete") is True and
        _valid_predecessor_deny_all(quarantine.get("deny_all")) and
        document.get("body_sha256") ==
            PREDECESSOR_ACTIVATION_FAILED_RECEIPT_BODY_SHA256,
        reason)
    _require(document.get("previous_failed_receipt") is None, reason)
    _validate_ancestor_activation_failure_evidence(
        document.get("predecessor_activation_failure"), reason)


def _validate_ancestor_activation_failure_evidence(
    value: Any,
    reason: str,
) -> None:
    _require(
        isinstance(value, dict) and
        set(value) == PREDECESSOR_ACTIVATION_FAILURE_FIELDS and
        value.get("receipt_path") ==
            str(ANCESTOR_ACTIVATION_FAILED_RECEIPT) and
        value.get("receipt_file_sha256") ==
            ANCESTOR_ACTIVATION_FAILED_RECEIPT_FILE_SHA256 and
        value.get("receipt_body_sha256") ==
            ANCESTOR_ACTIVATION_FAILED_RECEIPT_BODY_SHA256 and
        value.get("receipt_schema") ==
            "hepta.p1-watch-activation-failed-receipt.v1" and
        value.get("receipt_version") == 1 and
        value.get("receipt_revision") == 1 and
        value.get("receipt_status") == "FAILED_CLOSED" and
        value.get("receipt_round") == 86 and
        value.get("receipt_domain") == DOMAIN_ID and
        isinstance(value.get("receipt_reason"), str) and
        re.fullmatch(r"[A-Z][A-Z0-9_]{0,255}",
                     value["receipt_reason"]) is not None and
        all(type(value.get(field)) is int for field in (
            "receipt_device", "receipt_inode", "receipt_mode",
            "receipt_nlink", "receipt_uid", "receipt_gid", "receipt_bytes",
            "receipt_mtime_ns", "receipt_ctime_ns", "journal_record_count")) and
        value["receipt_device"] >= 0 and value["receipt_inode"] > 0 and
        stat.S_ISREG(value["receipt_mode"]) and
        stat.S_IMODE(value["receipt_mode"]) == 0o600 and
        value["receipt_nlink"] == 1 and value["receipt_uid"] == ROOT_UID and
        value["receipt_gid"] == ROOT_GID and
        0 < value["receipt_bytes"] <= MAXIMUM_JSON_BYTES and
        value["receipt_mtime_ns"] >= 0 and value["receipt_ctime_ns"] >= 0 and
        value.get("journal_path") == str(ANCESTOR_ACTIVATION_JOURNAL) and
        value.get("journal_sha256") == ANCESTOR_ACTIVATION_JOURNAL_SHA256 and
        value.get("journal_record_count") == 5 and
        value.get("journal_terminal_phase") == "FAILED_CLOSED",
        reason)


def _validate_predecessor_activation_failure_evidence(
    value: Any,
    reason: str,
) -> None:
    _require(
        isinstance(value, dict) and
        set(value) == PREDECESSOR_ACTIVATION_FAILURE_FIELDS and
        value.get("receipt_path") ==
            str(PREDECESSOR_ACTIVATION_FAILED_RECEIPT) and
        value.get("receipt_file_sha256") ==
            PREDECESSOR_ACTIVATION_FAILED_RECEIPT_FILE_SHA256 and
        value.get("receipt_body_sha256") ==
            PREDECESSOR_ACTIVATION_FAILED_RECEIPT_BODY_SHA256 and
        value.get("receipt_schema") ==
            "hepta.p1-watch-activation-failed-receipt.v2" and
        value.get("receipt_version") == 2 and
        value.get("receipt_revision") == 1 and
        value.get("receipt_status") == "FAILED_CLOSED" and
        value.get("receipt_round") == 95 and
        value.get("receipt_domain") == DOMAIN_ID and
        isinstance(value.get("receipt_reason"), str) and
        re.fullmatch(r"[A-Z][A-Z0-9_]{0,255}",
                     value["receipt_reason"]) is not None and
        all(type(value.get(field)) is int for field in (
            "receipt_device", "receipt_inode", "receipt_mode",
            "receipt_nlink", "receipt_uid", "receipt_gid", "receipt_bytes",
            "receipt_mtime_ns", "receipt_ctime_ns", "journal_record_count")) and
        value["receipt_device"] >= 0 and value["receipt_inode"] > 0 and
        stat.S_ISREG(value["receipt_mode"]) and
        stat.S_IMODE(value["receipt_mode"]) == 0o600 and
        value["receipt_nlink"] == 1 and value["receipt_uid"] == ROOT_UID and
        value["receipt_gid"] == ROOT_GID and
        0 < value["receipt_bytes"] <= MAXIMUM_JSON_BYTES and
        value["receipt_mtime_ns"] >= 0 and value["receipt_ctime_ns"] >= 0 and
        value.get("journal_path") == str(PREDECESSOR_ACTIVATION_JOURNAL) and
        value.get("journal_sha256") == PREDECESSOR_ACTIVATION_JOURNAL_SHA256 and
        value["journal_record_count"] == 21 and
        value.get("journal_terminal_phase") == "FAILED_CLOSED",
        reason)


def _validate_profile_file_evidence(
    value: Any,
    *,
    path: Path,
    sha256: str,
    size: int,
    mode: int,
    legacy_receipt: bool = False,
) -> dict[str, Any]:
    reason = "P1_LAUNCHER_ACTIVATION_RECEIPT_INVALID"
    fields = (
        PROFILE_LEGACY_RECEIPT_EVIDENCE_FIELDS
        if legacy_receipt else PROFILE_FILE_EVIDENCE_FIELDS)
    _require(
        isinstance(value, dict) and set(value) == fields and
        value.get("path") == str(path) and value.get("sha256") == sha256 and
        value.get("bytes") == size and value.get("mode") == stat.S_IFREG | mode and
        value.get("nlink") == 1 and value.get("uid") == ROOT_UID and
        value.get("gid") == ROOT_GID and
        (not legacy_receipt or value.get("body_sha256") ==
         LEGACY_PROFILE_RECEIPT_BODY_SHA256),
        reason)
    for field in (
            "device", "inode", "mode", "nlink", "uid", "gid", "bytes",
            "mtime_ns", "ctime_ns"):
        _require(type(value.get(field)) is int and value[field] >= 0, reason)
    _require(value["device"] > 0 and value["inode"] > 0, reason)
    return value


def _validate_predecessor_profile_receipt_evidence(value: Any) -> None:
    reason = "P1_LAUNCHER_ACTIVATION_RECEIPT_INVALID"
    _require(
        isinstance(value, dict) and
        set(value) == PROFILE_LEGACY_RECEIPT_EVIDENCE_FIELDS and
        value.get("path") == str(PREDECESSOR_PROFILE_DEPLOYMENT_RECEIPT) and
        value.get("sha256") == PREDECESSOR_PROFILE_RECEIPT_FILE_SHA256 and
        value.get("body_sha256") == PREDECESSOR_PROFILE_RECEIPT_BODY_SHA256 and
        value.get("bytes") == PREDECESSOR_PROFILE_RECEIPT_BYTES and
        value.get("mode") == stat.S_IFREG | 0o600 and
        value.get("nlink") == 1 and value.get("uid") == ROOT_UID and
        value.get("gid") == ROOT_GID and
        all(type(value.get(field)) is int and value[field] >= 0 for field in (
            "device", "inode", "mode", "nlink", "uid", "gid", "bytes",
            "mtime_ns", "ctime_ns")) and
        value["device"] > 0 and value["inode"] > 0,
        reason)


def _validate_profile_transition_receipt_evidence(value: Any) -> None:
    """Validate the dynamic Round114 transition receipt's exact envelope."""

    reason = "P1_LAUNCHER_ACTIVATION_RECEIPT_INVALID"
    _require(
        isinstance(value, dict) and
        set(value) == PROFILE_LEGACY_RECEIPT_EVIDENCE_FIELDS and
        value.get("path") == str(PROFILE_TRANSITION_RECEIPT) and
        isinstance(value.get("sha256"), str) and
        DIGEST.fullmatch(value["sha256"]) is not None and
        isinstance(value.get("body_sha256"), str) and
        DIGEST.fullmatch(value["body_sha256"]) is not None and
        all(type(value.get(field)) is int for field in (
            "device", "inode", "mode", "nlink", "uid", "gid", "bytes",
            "mtime_ns", "ctime_ns")) and
        value["device"] > 0 and value["inode"] > 0 and
        value["mode"] == stat.S_IFREG | 0o600 and value["nlink"] == 1 and
        value["uid"] == ROOT_UID and value["gid"] == ROOT_GID and
        0 < value["bytes"] <= MAXIMUM_JSON_BYTES and
        value["mtime_ns"] >= 0 and value["ctime_ns"] >= 0,
        reason)


def _bootstrap_validate_shadow_installer(
    installer_payload: bytes,
    manifest_payload: bytes,
    expected_manifest_sha256: str,
) -> None:
    """Bind the consumer source before executing any of its own claims."""

    reason = "P1_LAUNCHER_SHADOW_INSTALL_INVALID"
    _require(
        type(expected_manifest_sha256) is str and
        DIGEST.fullmatch(expected_manifest_sha256) is not None and
        digest_bytes(manifest_payload) == expected_manifest_sha256,
        reason)
    try:
        document = json.loads(
            manifest_payload.decode("ascii"), object_pairs_hook=_pairs,
            parse_constant=lambda _value: (_ for _ in ()).throw(
                LauncherError(reason)))
    except (UnicodeError, json.JSONDecodeError, LauncherError) as error:
        raise LauncherError(reason) from error
    _require(
        isinstance(document, dict) and
        canonical_bytes(document) == manifest_payload and
        set(document) == {
            "schema", "version", "archive_sha256",
            "source_baseline_sha256", "installer_sha256", "files",
            "paper_authorized", "live_authorized", "mutation_attempted",
            "direct_broker_access"} and
        document.get("schema") ==
            "hepta.shadow-runtime-install-manifest.v2" and
        type(document.get("version")) is int and document["version"] == 2 and
        type(document.get("files")) is list and
        all(document.get(field) is False for field in (
            "paper_authorized", "live_authorized", "mutation_attempted",
            "direct_broker_access")),
        reason)
    matches = [
        record for record in document["files"]
        if isinstance(record, dict) and
        record.get("path") == SHADOW_INSTALLER_MEMBER]
    installer_sha256 = digest_bytes(installer_payload)
    _require(
        len(matches) == 1 and set(matches[0]) == {
            "path", "mode", "size", "sha256"} and
        matches[0].get("mode") == "0755" and
        matches[0].get("size") == len(installer_payload) and
        matches[0].get("sha256") == installer_sha256 and
        document.get("installer_sha256") == installer_sha256,
        reason)


def _secure_read(
    path: Path,
    reason: str,
    maximum_bytes: int = MAXIMUM_JSON_BYTES,
    *,
    expected_uid: int | None = None,
    expected_gid: int | None = None,
    modes: frozenset[int] | None = None,
) -> bytes:
    flags = os.O_RDONLY | os.O_CLOEXEC
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    try:
        descriptor = os.open(path, flags)
    except OSError as error:
        raise LauncherError(reason) from error
    try:
        before = os.fstat(descriptor)
        _require(
            stat.S_ISREG(before.st_mode) and before.st_nlink == 1 and
            1 <= before.st_size <= maximum_bytes and
            (expected_uid is None or before.st_uid == expected_uid) and
            (expected_gid is None or before.st_gid == expected_gid) and
            (modes is None or stat.S_IMODE(before.st_mode) in modes),
            reason,
        )
        remaining = before.st_size
        chunks: list[bytes] = []
        while remaining:
            chunk = os.read(descriptor, min(remaining, 1024 * 1024))
            _require(bool(chunk), reason)
            chunks.append(chunk)
            remaining -= len(chunk)
        _require(os.read(descriptor, 1) == b"", reason)
        after = os.fstat(descriptor)
        _require(
            (before.st_dev, before.st_ino, before.st_size,
             before.st_mtime_ns, before.st_ctime_ns) ==
            (after.st_dev, after.st_ino, after.st_size,
             after.st_mtime_ns, after.st_ctime_ns),
            reason,
        )
        return b"".join(chunks)
    except OSError as error:
        raise LauncherError(reason) from error
    finally:
        os.close(descriptor)


def _document(
    path: Path,
    label: str,
    *,
    root_owned: bool = False,
    expected_uid: int | None = None,
    expected_gid: int | None = None,
    modes: frozenset[int] | None = None,
) -> tuple[dict[str, Any], bytes]:
    if root_owned:
        _require(
            expected_uid is None and expected_gid is None and modes is None,
            f"{label}_OWNER_ARGUMENTS_INVALID",
        )
        expected_uid = ROOT_UID
        expected_gid = ROOT_GID
        modes = frozenset({0o600, 0o640, 0o644})
    contents = _secure_read(
        path, f"{label}_FILE_INVALID",
        expected_uid=expected_uid,
        expected_gid=expected_gid,
        modes=modes,
    )
    return _decode_document(contents, label), contents


def _decode_document(contents: bytes, label: str) -> dict[str, Any]:
    try:
        value = json.loads(contents, object_pairs_hook=_pairs)
    except (UnicodeError, json.JSONDecodeError, ValueError) as error:
        raise LauncherError(f"{label}_JSON_INVALID") from error
    _require(
        isinstance(value, dict) and canonical_bytes(value) == contents,
        f"{label}_CANONICAL_INVALID",
    )
    body = dict(value)
    claimed = body.pop("body_sha256", None)
    _require(
        isinstance(claimed, str) and DIGEST.fullmatch(claimed) is not None and
        claimed == digest_bytes(canonical_bytes(body)),
        f"{label}_DIGEST_INVALID",
    )
    _reject_permissions(value)
    return value


@dataclass(frozen=True)
class LaunchConfiguration:
    probe_campaign_id: str
    formal_campaign_id: str
    formal_start_ms: int

    def validate(self, now_ms: int) -> tuple[int, int]:
        probe = PROBE_CAMPAIGN.fullmatch(self.probe_campaign_id)
        formal = FORMAL_CAMPAIGN.fullmatch(self.formal_campaign_id)
        warmup_start_ms = self.formal_start_ms
        dispatch_start_ms = (
            warmup_start_ms - PROBE_DISPATCH_LEAD_MS
            if type(warmup_start_ms) is int else None)
        decision_window_start_ms = (
            warmup_start_ms + POLICY_MINIMUM_WARMUP_MS
            if type(warmup_start_ms) is int else None)
        _require(
            probe is not None and formal is not None and
            self.probe_campaign_id != self.formal_campaign_id and
            probe.group(2) == formal.group(2) and
            int(formal.group(1)) == int(probe.group(1)) + 1 and
            type(now_ms) is int and now_ms >= 0 and
            type(warmup_start_ms) is int and warmup_start_ms > 0 and
            warmup_start_ms % POLICY_SLOT_INTERVAL_MS == 0 and
            type(dispatch_start_ms) is int and dispatch_start_ms > 0 and
            abs(dispatch_start_ms - now_ms) <=
            FORMAL_START_CLOCK_TOLERANCE_MS and
            type(decision_window_start_ms) is int and
            decision_window_start_ms <= MAXIMUM_POLICY_TIMESTAMP_MS,
            "P1_LAUNCHER_CONFIGURATION_INVALID",
        )
        return int(probe.group(1)), int(formal.group(1))


@dataclass(frozen=True)
class RunPaths:
    private_directory: Path
    public_directory: Path
    probe_reader_directory: Path
    formal_reader_directory: Path
    state: Path
    receipt: Path
    probe_policy: Path
    probe_marker: Path
    probe_host_receipt: Path
    admission_receipt: Path
    formal_policy: Path
    formal_marker: Path
    formal_verified_closure: Path

    @classmethod
    def derive(
        cls,
        configuration: LaunchConfiguration,
        formal_round: int,
    ) -> "RunPaths":
        private = STATE_BASE / "private" / f"round{formal_round}"
        public = STATE_BASE / "public" / f"round{formal_round}"
        readers = STATE_BASE / "readers"
        return cls(
            private_directory=private,
            public_directory=public,
            probe_reader_directory=readers / configuration.probe_campaign_id,
            formal_reader_directory=readers / configuration.formal_campaign_id,
            state=private / "launcher-state.json",
            receipt=private / "launcher-receipt.json",
            probe_policy=public / "load-probe-policy.json",
            probe_marker=public / "load-probe-authority-marker.json",
            probe_host_receipt=private / "load-probe-host-receipt.json",
            admission_receipt=private / "load-probe-admission-receipt.json",
            formal_policy=public / "formal-policy.json",
            formal_marker=public / "formal-authority-marker.json",
            formal_verified_closure=private / "formal-verified-closure.json",
        )

    def reader_status(self, formal: bool) -> Path:
        directory = (
            self.formal_reader_directory if formal
            else self.probe_reader_directory)
        return directory / "controller-status.json"

    def reader_artifacts(self, formal: bool) -> Path:
        directory = (
            self.formal_reader_directory if formal
            else self.probe_reader_directory)
        return directory / "observer"


@dataclass(frozen=True)
class PolicyArtifacts:
    policy: dict[str, Any]
    policy_file_sha256: str
    marker: dict[str, Any]
    marker_file_sha256: str
    valid_after_ms: int
    maximum_iterations: int


@dataclass(frozen=True)
class FinalReaderArtifacts:
    controller_status: dict[str, Any]
    controller_status_file_sha256: str
    observer_state: dict[str, Any]
    observer_state_file_sha256: str
    final_audit_body_sha256: str
    final_audit_file_sha256: str


@dataclass(frozen=True)
class VerifiedClosureArtifacts:
    closure: dict[str, Any]
    closure_file_sha256: str
    strategy_file_sha256: str


def _validated_policy_schedule(
    configuration: LaunchConfiguration,
    campaign_id: str,
    policy: dict[str, Any],
) -> tuple[int, int]:
    """Validate the complete immutable SHADOW schedule contract."""
    _reject_permissions(policy)
    warmup_start_ms = configuration.formal_start_ms
    decision_window_start_ms = policy.get("valid_after_ms")
    expires_at_ms = policy.get("expires_at_ms")
    slot_interval_ms = policy.get("slot_interval_ms")
    maximum_iterations = policy.get("maximum_iterations")
    expected_decision_window_start_ms = (
        warmup_start_ms + POLICY_MINIMUM_WARMUP_MS)
    body = dict(policy)
    claimed_body_sha256 = body.pop("body_sha256", None)
    campaign_binding = {
        "schema": "hepta.strategy-shadow-observation-campaign.v1",
        "campaign_id": campaign_id,
        "valid_after_ms": expected_decision_window_start_ms,
        "expires_at_ms": (
            expected_decision_window_start_ms +
            FORMAL_ITERATIONS * POLICY_SLOT_INTERVAL_MS),
        "slot_interval_ms": POLICY_SLOT_INTERVAL_MS,
        "maximum_iterations": FORMAL_ITERATIONS,
        "maximum_lateness_ms": POLICY_MAXIMUM_LATENESS_MS,
        "shadow_only": True,
        "paper_authorized": False,
        "live_authorized": False,
        "mutation_attempted": False,
        "direct_broker_access": False,
    }
    _require(
        set(policy) == FORMAL_POLICY_FIELDS and
        policy.get("schema") ==
        "hepta.strategy-shadow-observation-policy.v1" and
        policy.get("version") == 1 and
        policy.get("campaign_id") == campaign_id and
        policy.get("campaign_sha256") ==
        digest_bytes(canonical_bytes(campaign_binding)) and
        isinstance(policy.get("strategy_id"), str) and
        POLICY_IDENTIFIER.fullmatch(policy["strategy_id"]) is not None and
        isinstance(policy.get("strategy_version"), str) and
        POLICY_IDENTIFIER.fullmatch(policy["strategy_version"]) is not None and
        isinstance(policy.get("strategy_sha256"), str) and
        DIGEST.fullmatch(policy["strategy_sha256"]) is not None and
        type(decision_window_start_ms) is int and
        decision_window_start_ms == expected_decision_window_start_ms and
        decision_window_start_ms - warmup_start_ms ==
        POLICY_MINIMUM_WARMUP_MS and
        0 < decision_window_start_ms < MAXIMUM_POLICY_TIMESTAMP_MS and
        type(slot_interval_ms) is int and
        slot_interval_ms == POLICY_SLOT_INTERVAL_MS and
        type(maximum_iterations) is int and
        maximum_iterations == FORMAL_ITERATIONS and
        type(expires_at_ms) is int and
        expires_at_ms == decision_window_start_ms +
        maximum_iterations * slot_interval_ms and
        decision_window_start_ms < expires_at_ms <=
        MAXIMUM_POLICY_TIMESTAMP_MS and
        policy.get("maximum_lateness_ms") == POLICY_MAXIMUM_LATENESS_MS and
        policy.get("shadow_only") is True and
        policy.get("paper_authorized") is False and
        policy.get("live_authorized") is False and
        policy.get("mutation_attempted") is False and
        policy.get("direct_broker_access") is False and
        isinstance(claimed_body_sha256, str) and
        DIGEST.fullmatch(claimed_body_sha256) is not None and
        claimed_body_sha256 == digest_bytes(canonical_bytes(body)),
        "P1_LAUNCHER_POLICY_CONTRACT_INVALID",
    )
    return decision_window_start_ms, maximum_iterations


def _validated_artifact_schedule(
    configuration: LaunchConfiguration,
    campaign_id: str,
    artifacts: PolicyArtifacts,
) -> tuple[int, int]:
    valid_after_ms, maximum_iterations = _validated_policy_schedule(
        configuration, campaign_id, artifacts.policy)
    _require(
        artifacts.valid_after_ms == valid_after_ms and
        type(artifacts.valid_after_ms) is int and
        artifacts.maximum_iterations == maximum_iterations and
        type(artifacts.maximum_iterations) is int and
        isinstance(artifacts.policy_file_sha256, str) and
        DIGEST.fullmatch(artifacts.policy_file_sha256) is not None and
        isinstance(artifacts.marker_file_sha256, str) and
        DIGEST.fullmatch(artifacts.marker_file_sha256) is not None,
        "P1_LAUNCHER_POLICY_ARTIFACT_DRIFT",
    )
    return valid_after_ms, maximum_iterations


@dataclass(frozen=True)
class Registration:
    campaign_id: str
    generation: int
    document: dict[str, Any]


@dataclass
class LaunchEvidence:
    helper_sha256: dict[str, str] = field(default_factory=dict)
    launcher_identity: dict[str, Any] = field(default_factory=dict)
    activation_binding: dict[str, Any] = field(default_factory=dict)
    gateway_identity: dict[str, Any] = field(default_factory=dict)
    probe_policy_sha256: str | None = None
    probe_marker_sha256: str | None = None
    probe_reader_pid: int | None = None
    probe_generation: int | None = None
    probe_host_receipt_sha256: str | None = None
    probe_closure: dict[str, Any] | None = None
    admission_receipt_sha256: str | None = None
    formal_policy_sha256: str | None = None
    formal_marker_sha256: str | None = None
    formal_valid_after_ms: int | None = None
    formal_expected_iterations: int | None = None
    formal_completed_iterations: int | None = None
    formal_final_generation: int | None = None
    formal_controller_status_sha256: str | None = None
    formal_observer_state_sha256: str | None = None
    formal_verified_closure_file_sha256: str | None = None
    formal_verified_closure_body_sha256: str | None = None
    formal_host_result_sha256: str | None = None
    formal_reader_completion: dict[str, Any] | None = None
    formal_post_verifier_reader_evidence: dict[str, Any] | None = None
    execution_service_epoch: str | None = None
    execution_service_fencing_generation: int | None = None
    formal_reader_pid: int | None = None
    formal_generation: int | None = None
    formal_closure: dict[str, Any] | None = None


class Executor(Protocol):
    def prepare(self, paths: RunPaths) -> None: ...
    def helper_hashes(self) -> dict[str, str]: ...
    def launcher_identity(
        self, unit: str, pid: int,
        configuration: LaunchConfiguration,
    ) -> dict[str, Any]: ...
    def activation_binding(self) -> dict[str, Any]: ...
    def gateway_identity(self) -> dict[str, Any]: ...
    def assert_clean(self) -> dict[str, Any]: ...
    def assert_paper_inactive(self) -> dict[str, Any]: ...
    def build_policy(
        self, mode: str, configuration: LaunchConfiguration,
        paths: RunPaths) -> PolicyArtifacts: ...
    def start_reader(
        self, campaign_id: str, unit: str, launcher_unit: str,
        policy: Path, marker: Path, paths: RunPaths, *, formal: bool) -> int: ...
    def provision(self, campaign_id: str, owner_pid: int) -> Registration: ...
    def start_backstop(self) -> None: ...
    def run_probe_host(
        self, configuration: LaunchConfiguration, paths: RunPaths,
        reader_unit: str, generation: int, capture_sha256: str,
    ) -> tuple[dict[str, Any], str]: ...
    def validate_probe(
        self, configuration: LaunchConfiguration, paths: RunPaths,
    ) -> tuple[dict[str, Any], str]: ...
    def run_formal_host(
        self, configuration: LaunchConfiguration, paths: RunPaths,
        reader_unit: str, generation: int, capture_sha256: str,
        policy_artifacts: PolicyArtifacts,
    ) -> dict[str, Any]: ...
    def read_formal_evidence(self, paths: RunPaths) -> FinalReaderArtifacts: ...
    def verify_formal_closure(
        self, paths: RunPaths,
    ) -> VerifiedClosureArtifacts: ...
    def assert_reader_active(self, unit: str, pid: int) -> dict[str, Any]: ...
    def stop_unit(self, unit: str) -> None: ...
    def close_and_verify(self, reason: str) -> dict[str, Any]: ...


class StateStore(Protocol):
    def write_state(self, paths: RunPaths, document: dict[str, Any]) -> None: ...
    def write_receipt(self, paths: RunPaths, document: dict[str, Any]) -> None: ...


def reader_unit(round_number: int) -> str:
    return f"hepta-p1-shadow-reader-round{round_number}.service"


def host_unit(round_number: int) -> str:
    return f"hepta-p1-shadow-host-round{round_number}.service"


def admission_unit(round_number: int) -> str:
    return f"hepta-p1-shadow-admission-round{round_number}.service"


def launcher_command(configuration: LaunchConfiguration) -> list[str]:
    return [
        LAUNCHER_EXECUTABLE,
        "--probe-campaign-id", configuration.probe_campaign_id,
        "--formal-campaign-id", configuration.formal_campaign_id,
        "--formal-start-ms", str(configuration.formal_start_ms),
    ]


class Launcher:
    def __init__(
        self,
        configuration: LaunchConfiguration,
        executor: Executor,
        store: StateStore,
        *,
        now_ms: int | None = None,
        _wall_now_ms: Callable[[], int] | None = None,
        _monotonic_clock: Callable[[], float] | None = None,
        _sleep: Callable[[float], None] | None = None,
    ) -> None:
        self.configuration = configuration
        self.executor = executor
        self.store = store
        self._wall_now_ms = (
            (lambda: time.time_ns() // 1_000_000)
            if _wall_now_ms is None else _wall_now_ms)
        self._monotonic_clock = (
            time.monotonic
            if _monotonic_clock is None else _monotonic_clock)
        self._sleep = time.sleep if _sleep is None else _sleep
        self.now_ms = self._wall_now_ms() if now_ms is None else now_ms
        self._launch_clock_wall_ms = self.now_ms
        self._launch_clock_monotonic = self._monotonic_clock()
        _require(
            not isinstance(self._launch_clock_monotonic, bool) and
            isinstance(self._launch_clock_monotonic, (int, float)) and
            math.isfinite(self._launch_clock_monotonic),
            "P1_LAUNCHER_START_CLOCK_INVALID",
        )
        self._launch_clock_monotonic = float(
            self._launch_clock_monotonic)
        probe_round, formal_round = configuration.validate(self.now_ms)
        self.probe_round = probe_round
        self.formal_round = formal_round
        self.paths = RunPaths.derive(configuration, formal_round)
        self.probe_reader_unit = reader_unit(probe_round)
        self.probe_host_unit = host_unit(probe_round)
        self.formal_reader_unit = reader_unit(formal_round)
        self.formal_host_unit = host_unit(formal_round)
        self.launcher_unit = admission_unit(formal_round)
        self.evidence = LaunchEvidence()
        self.probe_reader_started = False
        self.probe_provisioned = False
        self.formal_reader_started = False
        self.formal_provisioned = False

    def _verified_clock_sample(self) -> tuple[int, float]:
        wall_now_ms = self._wall_now_ms()
        monotonic_now = self._monotonic_clock()
        _require(
            type(wall_now_ms) is int and wall_now_ms >= 0 and
            not isinstance(monotonic_now, bool) and
            isinstance(monotonic_now, (int, float)) and
            math.isfinite(monotonic_now) and
            monotonic_now >= self._launch_clock_monotonic,
            "P1_LAUNCHER_START_CLOCK_INVALID",
        )
        expected_wall_now_ms = (
            self._launch_clock_wall_ms +
            round((float(monotonic_now) -
                   self._launch_clock_monotonic) * 1000)
        )
        _require(
            abs(wall_now_ms - expected_wall_now_ms) <=
            FORMAL_START_MAXIMUM_CLOCK_DRIFT_MS,
            "P1_LAUNCHER_START_CLOCK_DRIFT",
        )
        return wall_now_ms, float(monotonic_now)

    def _assert_start_clock(self) -> None:
        wall_now_ms, _ = self._verified_clock_sample()
        self.configuration.validate(wall_now_ms)

    def _wait_until_wall_ms(self, target_ms: int, reason: str) -> int:
        wall_now_ms, monotonic_now = self._verified_clock_sample()
        target_monotonic = (
            self._launch_clock_monotonic +
            (target_ms - self._launch_clock_wall_ms) / 1000.0)
        if wall_now_ms < target_ms and monotonic_now < target_monotonic:
            self._sleep(target_monotonic - monotonic_now)
            wall_now_ms, _ = self._verified_clock_sample()
        _require(wall_now_ms >= target_ms, reason)
        return wall_now_ms

    def _wait_for_formal_preparation_window(self) -> None:
        warmup_start_ms = self.configuration.formal_start_ms
        now_ms = self._wait_until_wall_ms(
            warmup_start_ms - FORMAL_PREPARATION_LEAD_MS,
            "P1_LAUNCHER_FORMAL_PREPARATION_CLOCK_INVALID",
        )
        _require(
            now_ms <= warmup_start_ms,
            "P1_LAUNCHER_FORMAL_PREPARATION_LATE",
        )

    def _wait_for_formal_warmup_start(self) -> None:
        warmup_start_ms = self.configuration.formal_start_ms
        now_ms = self._wait_until_wall_ms(
            warmup_start_ms,
            "P1_LAUNCHER_FORMAL_WARMUP_CLOCK_INVALID",
        )
        _require(
            now_ms <= warmup_start_ms + FORMAL_START_CLOCK_TOLERANCE_MS,
            "P1_LAUNCHER_FORMAL_WARMUP_START_LATE",
        )

    def _check_launcher_identity(self) -> None:
        identity = self.evidence.launcher_identity
        _require(
            set(identity) == LAUNCHER_IDENTITY_FIELDS and
            identity.get("unit") == self.launcher_unit and
            re.fullmatch(r"[0-9a-f]{32}",
                         identity.get("invocation_id", "")) is not None and
            identity.get("main_pid") == os.getpid() and
            identity.get("type") == "exec" and
            identity.get("restart") == "no" and
            identity.get("remain_after_exit") == "no" and
            identity.get("user") == "root" and
            identity.get("group") == "root" and
            identity.get("exec_start") == launcher_command(self.configuration) and
            identity.get("environment") == SANITIZED_ENVIRONMENT and
            identity.get("conflicts") == list(PAPER_UNITS) and
            identity.get("launcher_sha256") ==
            self.evidence.helper_sha256.get("launcher_sha256"),
            "P1_LAUNCHER_UNIT_IDENTITY_INVALID",
        )

    def _check_helpers_unchanged(self, reason: str) -> None:
        _require(
            self.executor.helper_hashes() == self.evidence.helper_sha256,
            reason,
        )

    def _check_activation_unchanged(self, reason: str) -> None:
        _require(
            bool(self.evidence.activation_binding) and
            self.executor.activation_binding() ==
            self.evidence.activation_binding,
            reason,
        )

    def _state(self) -> dict[str, Any]:
        return seal({
            "schema": "hepta.p1-shadow-admission-launcher-state.v1",
            "version": 1,
            "status": "STARTING",
            "domain_id": DOMAIN_ID,
            "probe_campaign_id": self.configuration.probe_campaign_id,
            "formal_campaign_id": self.configuration.formal_campaign_id,
            "formal_start_ms": self.configuration.formal_start_ms,
            "launcher_pid": os.getpid(),
            "launcher_unit": self.launcher_unit,
            "launcher_identity": self.evidence.launcher_identity,
            "created_at_ms": self.now_ms,
            "probe_reader_unit": self.probe_reader_unit,
            "probe_host_unit": self.probe_host_unit,
            "formal_reader_unit": self.formal_reader_unit,
            "formal_host_unit": self.formal_host_unit,
            "helper_sha256": self.evidence.helper_sha256,
            "activation_receipt_path": str(ACTIVATION_RECEIPT),
            "activation_receipt_file_sha256":
                self.evidence.activation_binding.get(
                    "activation_receipt_file_sha256"),
            "activation_receipt_body_sha256":
                self.evidence.activation_binding.get(
                    "activation_receipt_body_sha256"),
            "activation_profile_receipt_path":
                self.evidence.activation_binding.get(
                    "profile_receipt_path"),
            "activation_profile_receipt_file_sha256":
                self.evidence.activation_binding.get(
                    "profile_receipt_file_sha256"),
            "activation_profile_receipt_body_sha256":
                self.evidence.activation_binding.get(
                    "profile_receipt_body_sha256"),
            "activation_broker_epoch":
                self.evidence.activation_binding.get("broker"),
            "activation_gateway_epoch":
                self.evidence.activation_binding.get("gateway"),
            "activation_reconcile_timer":
                self.evidence.activation_binding.get("reconcile_timer"),
            "activation_predecessor_success":
                self.evidence.activation_binding.get(
                    "predecessor_activation_success"),
            "activation_predecessor_failure":
                self.evidence.activation_binding.get(
                    "predecessor_activation_failure"),
            "gateway_identity": self.evidence.gateway_identity,
            "probe_policy_path": str(self.paths.probe_policy),
            "probe_policy_file_sha256": self.evidence.probe_policy_sha256,
            "probe_marker_path": str(self.paths.probe_marker),
            "probe_marker_file_sha256": self.evidence.probe_marker_sha256,
            "admission_receipt_path": str(self.paths.admission_receipt),
            "formal_policy_path": str(self.paths.formal_policy),
            "formal_marker_path": str(self.paths.formal_marker),
            "load_probe_admission_receipt_activation_binding_attested":
                False,
            "paper_authorized": False,
            "live_authorized": False,
            "mutation_authorized": False,
            "direct_broker_access": False,
        })

    def _receipt(
        self,
        *,
        status: str,
        reason: str | None,
        cleanup_errors: list[str],
    ) -> dict[str, Any]:
        return seal({
            "schema": "hepta.p1-shadow-admission-launcher-receipt.v1",
            "version": 1,
            "status": status,
            "reason": reason,
            "domain_id": DOMAIN_ID,
            "probe_campaign_id": self.configuration.probe_campaign_id,
            "formal_campaign_id": self.configuration.formal_campaign_id,
            "formal_start_ms": self.configuration.formal_start_ms,
            "completed_at_ms": time.time_ns() // 1_000_000,
            "probe_reader_unit": self.probe_reader_unit,
            "probe_host_unit": self.probe_host_unit,
            "formal_reader_unit": self.formal_reader_unit,
            "formal_host_unit": self.formal_host_unit,
            "launcher_unit": self.launcher_unit,
            "launcher_identity": self.evidence.launcher_identity,
            "helper_sha256": self.evidence.helper_sha256,
            "activation_receipt_path": str(ACTIVATION_RECEIPT),
            "activation_receipt_file_sha256":
                self.evidence.activation_binding.get(
                    "activation_receipt_file_sha256"),
            "activation_receipt_body_sha256":
                self.evidence.activation_binding.get(
                    "activation_receipt_body_sha256"),
            "activation_profile_receipt_path":
                self.evidence.activation_binding.get(
                    "profile_receipt_path"),
            "activation_profile_receipt_file_sha256":
                self.evidence.activation_binding.get(
                    "profile_receipt_file_sha256"),
            "activation_profile_receipt_body_sha256":
                self.evidence.activation_binding.get(
                    "profile_receipt_body_sha256"),
            "activation_broker_epoch":
                self.evidence.activation_binding.get("broker"),
            "activation_gateway_epoch":
                self.evidence.activation_binding.get("gateway"),
            "activation_reconcile_timer":
                self.evidence.activation_binding.get("reconcile_timer"),
            "activation_predecessor_success":
                self.evidence.activation_binding.get(
                    "predecessor_activation_success"),
            "activation_predecessor_failure":
                self.evidence.activation_binding.get(
                    "predecessor_activation_failure"),
            "gateway_identity": self.evidence.gateway_identity,
            "probe_policy_file_sha256": self.evidence.probe_policy_sha256,
            "probe_marker_file_sha256": self.evidence.probe_marker_sha256,
            "probe_reader_pid": self.evidence.probe_reader_pid,
            "probe_generation": self.evidence.probe_generation,
            "probe_host_receipt_file_sha256":
                self.evidence.probe_host_receipt_sha256,
            "probe_closure": self.evidence.probe_closure,
            "admission_receipt_file_sha256":
                self.evidence.admission_receipt_sha256,
            "formal_policy_file_sha256": self.evidence.formal_policy_sha256,
            "formal_marker_file_sha256": self.evidence.formal_marker_sha256,
            "formal_valid_after_ms": self.evidence.formal_valid_after_ms,
            "formal_expected_iterations":
                self.evidence.formal_expected_iterations,
            "formal_completed_iterations":
                self.evidence.formal_completed_iterations,
            "formal_final_generation": self.evidence.formal_final_generation,
            "formal_controller_status_file_sha256":
                self.evidence.formal_controller_status_sha256,
            "formal_observer_state_file_sha256":
                self.evidence.formal_observer_state_sha256,
            "formal_verified_closure_file_sha256":
                self.evidence.formal_verified_closure_file_sha256,
            "formal_verified_closure_body_sha256":
                self.evidence.formal_verified_closure_body_sha256,
            "formal_host_result_sha256":
                self.evidence.formal_host_result_sha256,
            "formal_reader_completion":
                self.evidence.formal_reader_completion,
            "formal_post_verifier_reader_evidence":
                self.evidence.formal_post_verifier_reader_evidence,
            "execution_service_epoch":
                self.evidence.execution_service_epoch,
            "execution_service_fencing_generation":
                self.evidence.execution_service_fencing_generation,
            "formal_reader_pid": self.evidence.formal_reader_pid,
            "formal_generation": self.evidence.formal_generation,
            "formal_closure": self.evidence.formal_closure,
            "cleanup_errors": cleanup_errors,
            "authority_residue": (
                False if not cleanup_errors else "UNKNOWN"),
            "export_residue": (
                False if not cleanup_errors else "UNKNOWN"),
            "load_probe_admission_receipt_activation_binding_attested":
                False,
            "paper_authorized": False,
            "live_authorized": False,
            "mutation_authorized": False,
            "direct_broker_access": False,
        })

    def _check_registration(
        self, registration: Registration, campaign_id: str) -> None:
        document = registration.document
        _reject_permissions(document)
        _require(
            registration.campaign_id == campaign_id and
            registration.generation >= 1 and
            document.get("schema") ==
            "hepta.shadow-watch-custodian-registration.v1" and
            document.get("status") == "REGISTERED" and
            document.get("campaign_id") == campaign_id and
            document.get("lease_generation") == registration.generation and
            document.get("paper_authorized") is False and
            document.get("live_authorized") is False and
            document.get("mutation_authorized") is False and
            document.get("direct_broker_access") is False,
            "P1_LAUNCHER_REGISTRATION_INVALID",
        )

    def _check_admission(
        self,
        admission: dict[str, Any],
        artifacts: PolicyArtifacts,
    ) -> tuple[int, int]:
        _reject_permissions(admission)
        schedule = _validated_artifact_schedule(
            self.configuration,
            self.configuration.formal_campaign_id,
            artifacts,
        )
        epoch = admission.get("probe_execution_service_epoch")
        fencing = admission.get(
            "probe_execution_service_fencing_generation")
        environment = admission.get("environment")
        first_started = admission.get("probe_first_collection_started_at_ms")
        first_exported = admission.get("probe_first_exported_at_ms")
        last_started = admission.get("probe_last_collection_started_at_ms")
        last_exported = admission.get("probe_last_exported_at_ms")
        required_digests = (
            "host_receipt_body_sha256",
            "observer_controller_status_body_sha256",
            "observer_state_body_sha256", "history_head_body_sha256",
            "probe_first_record_sha256", "probe_first_snapshot_body_sha256",
            "probe_last_record_sha256", "probe_last_snapshot_body_sha256",
            "probe_audit_expected_previous_sha256",
        )
        attested_environment = (
            environment if isinstance(environment, dict) else {})
        expected_environment = {
            "boot_id": attested_environment.get("boot_id"),
            "audit_journal_device":
                attested_environment.get("audit_journal_device"),
            "audit_journal_inode":
                attested_environment.get("audit_journal_inode"),
            **self.evidence.gateway_identity,
            **{
                field: self.evidence.helper_sha256[field]
                for field in (
                    "collector_sha256", "exporter_sha256",
                    "heptactl_sha256", "gateway_sha256",
                    "custodian_sha256", "observer_sha256",
                    "host_controller_sha256", "domain_config_sha256",
                    "gateway_profile_sha256",
                )
            },
        }
        _require(
            set(admission) == ADMISSION_FIELDS and
            admission.get("schema") ==
            "hepta.p1-shadow-load-probe-admission-receipt.v1" and
            admission.get("version") == 1 and
            admission.get("status") == "GO" and
            admission.get("campaign_id") ==
            self.configuration.probe_campaign_id and
            admission.get("sample_count") == LOAD_PROBE_RUNS and
            admission.get("collection_cadence_ms") == 10_000 and
            admission.get("maximum_collection_jitter_ms") == 1_000 and
            admission.get("missed_sample_count") == 0 and
            admission.get("missed_decision_count") == 0 and
            admission.get("prospective_campaign_id") ==
            self.configuration.formal_campaign_id and
            admission.get("prospective_policy_path") ==
            str(self.paths.formal_policy) and
            admission.get("authority_marker_path") ==
            str(self.paths.formal_marker) and
            isinstance(epoch, str) and 1 <= len(epoch) <= 256 and
            type(fencing) is int and 1 <= fencing < (1 << 64) and
            all(
                isinstance(admission.get(field), str) and
                DIGEST.fullmatch(admission[field]) is not None
                for field in required_digests
            ) and
            admission.get("probe_audit_cursor_sequence") == LOAD_PROBE_RUNS and
            admission.get("probe_audit_expected_previous_sha256") ==
            admission.get("probe_last_record_sha256") and
            type(admission.get("probe_history_record_bytes")) is int and
            admission["probe_history_record_bytes"] > 0 and
            type(first_started) is int and type(first_exported) is int and
            type(last_started) is int and type(last_exported) is int and
            first_started <= first_exported < last_started <= last_exported and
            isinstance(environment, dict) and
            set(expected_environment) == ENVIRONMENT_FIELDS and
            set(environment) == ENVIRONMENT_FIELDS and
            isinstance(environment.get("boot_id"), str) and
            BOOT_ID.fullmatch(environment["boot_id"]) is not None and
            type(environment.get("audit_journal_device")) is int and
            0 <= environment["audit_journal_device"] < (1 << 64) and
            type(environment.get("audit_journal_inode")) is int and
            1 <= environment["audit_journal_inode"] < (1 << 64) and
            all(environment.get(field) == value
                for field, value in expected_environment.items()) and
            type(admission.get("validated_at_ms")) is int and
            0 <= (time.time_ns() // 1_000_000) -
            admission["validated_at_ms"] <= ADMISSION_MAXIMUM_AGE_MS and
            artifacts.marker.get("execution_service_epoch") == epoch and
            artifacts.marker.get(
                "execution_service_fencing_generation") == fencing and
            artifacts.policy.get("campaign_id") ==
            self.configuration.formal_campaign_id and
            artifacts.marker.get("schema") ==
            "hepta.p1-shadow-admission-authority-marker.v1" and
            artifacts.marker.get("status") == "ACTIVE" and
            artifacts.marker.get("campaign_id") ==
            self.configuration.formal_campaign_id and
            artifacts.marker.get("policy_path") == str(self.paths.formal_policy) and
            artifacts.marker.get("policy_file_sha256") ==
            artifacts.policy_file_sha256 and
            artifacts.marker.get("policy_body_sha256") ==
            artifacts.policy.get("body_sha256") and
            artifacts.marker.get("admission_receipt_path") ==
            str(self.paths.admission_receipt) and
            artifacts.marker.get("admission_receipt_file_sha256") ==
            self.evidence.admission_receipt_sha256 and
            artifacts.marker.get("environment") == environment,
            "P1_LAUNCHER_ADMISSION_INVALID",
        )
        self.evidence.execution_service_epoch = epoch
        self.evidence.execution_service_fencing_generation = fencing
        return schedule

    def _check_final_reader_evidence(
        self,
        artifacts: PolicyArtifacts,
        formal_result: dict[str, Any],
        formal_closure: dict[str, Any],
        final_artifacts: FinalReaderArtifacts,
        initial_generation: int,
    ) -> None:
        status = final_artifacts.controller_status
        state = final_artifacts.observer_state
        _reject_permissions(status)
        _reject_permissions(state)
        valid_after_ms, maximum_iterations = _validated_artifact_schedule(
            self.configuration,
            self.configuration.formal_campaign_id,
            artifacts,
        )
        final_generation = formal_result.get("lease_generation")
        completed_iterations = formal_result.get("completed_iterations")
        audit_events = state.get("audit_events")
        digest_fields = (
            final_artifacts.controller_status_file_sha256,
            final_artifacts.observer_state_file_sha256,
            final_artifacts.final_audit_body_sha256,
            final_artifacts.final_audit_file_sha256,
            status.get("last_export_receipt_body_sha256"),
            status.get("last_snapshot_body_sha256"),
            state.get("last_snapshot_body_sha256"),
            state.get("last_lease_receipt_body_sha256"),
            state.get("last_lease_receipt_file_sha256"),
            state.get("last_receipt_sha256"),
            state.get("final_audit_receipt_sha256"),
        )
        _require(
            set(status) == CONTROLLER_STATUS_FIELDS and
            status.get("schema") ==
            "hepta.p1-shadow-observer-controller-status.v1" and
            status.get("version") == 1 and
            status.get("campaign_id") ==
            self.configuration.formal_campaign_id and
            type(status.get("controller_pid")) is int and
            status["controller_pid"] == self.evidence.formal_reader_pid and
            status.get("controller_uid") == READER_UID and
            status.get("controller_gid") == READER_GID and
            status.get("state") == "TERMINAL" and
            type(status.get("started_at_ms")) is int and
            type(status.get("updated_at_ms")) is int and
            0 < status["started_at_ms"] <= status["updated_at_ms"] and
            type(status.get("observer_invocations")) is int and
            status["observer_invocations"] >= maximum_iterations and
            status.get("observer_status") == "COMPLETE" and
            status.get("observer_outcome") == "COMPLETE" and
            status.get("completed_iterations") == maximum_iterations and
            status.get("reason") is None and
            status.get("last_lease_generation") == final_generation and
            status.get("locked_execution_service_epoch") ==
            self.evidence.execution_service_epoch and
            status.get("locked_execution_service_fencing_generation") ==
            self.evidence.execution_service_fencing_generation and
            set(state) == OBSERVER_STATE_FIELDS and
            state.get("schema") ==
            "hepta.bounded-shadow-observer-state.v1" and
            state.get("version") == 1 and
            state.get("campaign_id") ==
            self.configuration.formal_campaign_id and
            state.get("campaign_sha256") ==
            artifacts.policy.get("campaign_sha256") and
            state.get("policy_sha256") == artifacts.policy_file_sha256 and
            state.get("policy_body_sha256") ==
            artifacts.policy.get("body_sha256") and
            state.get("strategy_id") == artifacts.policy.get("strategy_id") and
            state.get("strategy_version") ==
            artifacts.policy.get("strategy_version") and
            state.get("strategy_sha256") ==
            artifacts.policy.get("strategy_sha256") and
            state.get("status") == "COMPLETE" and
            state.get("collection_cadence_ms") == 10_000 and
            state.get("maximum_collection_jitter_ms") == 1_000 and
            state.get("valid_after_ms") == valid_after_ms and
            state.get("expires_at_ms") ==
            artifacts.policy.get("expires_at_ms") and
            state.get("slot_interval_ms") == POLICY_SLOT_INTERVAL_MS and
            state.get("maximum_iterations") == maximum_iterations and
            state.get("maximum_lateness_ms") ==
            POLICY_MAXIMUM_LATENESS_MS and
            type(state.get("segment_index")) is int and
            state["segment_index"] >= 1 and
            state.get("segment_status") == "OPEN" and
            state.get("completed_iterations") == maximum_iterations and
            state.get("last_watch_generation") == final_generation and
            state.get("missed_sample_count") == 0 and
            state.get("missed_decision_count") == 0 and
            type(state.get("sample_count")) is int and
            state["sample_count"] >= maximum_iterations and
            type(state.get("final_audit_segment_count")) is int and
            state["final_audit_segment_count"] == state["segment_index"] and
            isinstance(audit_events, list) and len(audit_events) >= 1 and
            all(
                isinstance(event, dict) and
                event.get("sequence") == index
                for index, event in enumerate(audit_events, start=1)
            ) and
            type(final_generation) is int and
            final_generation > initial_generation and
            formal_closure.get("lease_generation") == final_generation and
            completed_iterations == maximum_iterations and
            status.get("last_snapshot_body_sha256") ==
            state.get("last_snapshot_body_sha256") and
            all(
                isinstance(value, str) and DIGEST.fullmatch(value) is not None
                for value in digest_fields
            ),
            "P1_LAUNCHER_FINAL_READER_EVIDENCE_INVALID",
        )
        self.evidence.formal_completed_iterations = completed_iterations
        self.evidence.formal_final_generation = final_generation
        self.evidence.formal_controller_status_sha256 = (
            final_artifacts.controller_status_file_sha256)
        self.evidence.formal_observer_state_sha256 = (
            final_artifacts.observer_state_file_sha256)

    def _check_reader_completion(
        self,
        formal_result: dict[str, Any],
        final_artifacts: FinalReaderArtifacts,
    ) -> None:
        completion = formal_result.get("reader_completion")
        status = final_artifacts.controller_status
        state = final_artifacts.observer_state
        now_ms = time.time_ns() // 1_000_000
        acknowledged_at_ms = (
            completion.get("acknowledged_at_ms")
            if isinstance(completion, dict) else None)
        updated_at_ms = status.get("updated_at_ms")
        digest_fields = (
            completion.get("controller_status_file_sha256"),
            completion.get("controller_status_body_sha256"),
            completion.get("observer_state_file_sha256"),
            completion.get("observer_state_body_sha256"),
        ) if isinstance(completion, dict) else ()
        controller_is_acknowledged_bytes = (
            isinstance(completion, dict) and
            completion.get("controller_status_file_sha256") ==
            final_artifacts.controller_status_file_sha256 and
            completion.get("controller_status_body_sha256") ==
            status.get("body_sha256")
        )
        _require(
            isinstance(completion, dict) and
            set(completion) == READER_COMPLETION_FIELDS and
            completion.get("reader_unit") == self.formal_reader_unit and
            completion.get("reader_pid") == self.evidence.formal_reader_pid and
            type(acknowledged_at_ms) is int and
            0 <= now_ms - acknowledged_at_ms <=
            TERMINAL_HEARTBEAT_MAXIMUM_AGE_MS and
            type(updated_at_ms) is int and
            0 <= now_ms - updated_at_ms <=
            TERMINAL_HEARTBEAT_MAXIMUM_AGE_MS and
            (
                (
                    controller_is_acknowledged_bytes and
                    updated_at_ms <= acknowledged_at_ms
                ) or (
                    not controller_is_acknowledged_bytes and
                    updated_at_ms >= acknowledged_at_ms
                )
            ) and
            completion.get("observer_state_file_sha256") ==
            final_artifacts.observer_state_file_sha256 and
            completion.get("observer_state_body_sha256") ==
            state.get("body_sha256") and
            all(
                isinstance(value, str) and DIGEST.fullmatch(value) is not None
                for value in digest_fields
            ),
            "P1_LAUNCHER_READER_COMPLETION_INVALID",
        )
        self.evidence.formal_reader_completion = dict(completion)

    def _check_post_verifier_reader_evidence(
        self,
        formal_result: dict[str, Any],
        formal_artifacts: PolicyArtifacts,
        formal_closure: dict[str, Any],
        initial_artifacts: FinalReaderArtifacts,
        final_artifacts: FinalReaderArtifacts,
        initial_generation: int,
        liveness: dict[str, Any],
    ) -> None:
        completion = formal_result.get("reader_completion")
        status = final_artifacts.controller_status
        initial_status = initial_artifacts.controller_status
        state = final_artifacts.observer_state
        now_ms = time.time_ns() // 1_000_000
        self._check_final_reader_evidence(
            formal_artifacts,
            formal_result,
            formal_closure,
            final_artifacts,
            initial_generation,
        )
        initial_status_semantics = dict(initial_status)
        initial_status_semantics.pop("updated_at_ms", None)
        initial_status_semantics.pop("body_sha256", None)
        final_status_semantics = dict(status)
        final_status_semantics.pop("updated_at_ms", None)
        final_status_semantics.pop("body_sha256", None)
        controller_is_acknowledged_bytes = (
            isinstance(completion, dict) and
            completion.get("controller_status_file_sha256") ==
            final_artifacts.controller_status_file_sha256 and
            completion.get("controller_status_body_sha256") ==
            status.get("body_sha256")
        )
        _require(
            isinstance(completion, dict) and
            set(liveness) == {
                "unit", "active_state", "sub_state", "main_pid"} and
            liveness.get("unit") == self.formal_reader_unit and
            liveness.get("active_state") == "active" and
            liveness.get("sub_state") == "running" and
            liveness.get("main_pid") == self.evidence.formal_reader_pid and
            initial_status_semantics == final_status_semantics and
            type(status.get("updated_at_ms")) is int and
            status["updated_at_ms"] >= initial_status.get("updated_at_ms", 0) and
            0 <= now_ms - status["updated_at_ms"] <=
            TERMINAL_HEARTBEAT_MAXIMUM_AGE_MS and
            (
                controller_is_acknowledged_bytes or
                status["updated_at_ms"] >= completion.get(
                    "acknowledged_at_ms", MAXIMUM_POLICY_TIMESTAMP_MS)
            ) and
            final_artifacts.observer_state_file_sha256 ==
            initial_artifacts.observer_state_file_sha256 ==
            completion.get("observer_state_file_sha256") and
            state.get("body_sha256") ==
            initial_artifacts.observer_state.get("body_sha256") ==
            completion.get("observer_state_body_sha256") and
            final_artifacts.final_audit_body_sha256 ==
            initial_artifacts.final_audit_body_sha256 and
            final_artifacts.final_audit_file_sha256 ==
            initial_artifacts.final_audit_file_sha256 and
            state.get("completed_iterations") == FORMAL_ITERATIONS and
            state.get("missed_sample_count") == 0 and
            state.get("missed_decision_count") == 0,
            "P1_LAUNCHER_POST_VERIFIER_READER_EVIDENCE_INVALID",
        )
        self.evidence.formal_post_verifier_reader_evidence = {
            "reader_unit": self.formal_reader_unit,
            "reader_pid": self.evidence.formal_reader_pid,
            "verified_at_ms": now_ms,
            "controller_status_updated_at_ms": status["updated_at_ms"],
            "controller_status_file_sha256":
                final_artifacts.controller_status_file_sha256,
            "controller_status_body_sha256": status["body_sha256"],
            "observer_state_file_sha256":
                final_artifacts.observer_state_file_sha256,
            "observer_state_body_sha256": state["body_sha256"],
            "final_audit_file_sha256":
                final_artifacts.final_audit_file_sha256,
            "final_audit_body_sha256":
                final_artifacts.final_audit_body_sha256,
        }

    def _check_verified_closure(
        self,
        artifacts: PolicyArtifacts,
        final_artifacts: FinalReaderArtifacts,
        verified_artifacts: VerifiedClosureArtifacts,
    ) -> None:
        closure = verified_artifacts.closure
        state = final_artifacts.observer_state
        _reject_permissions(closure)
        body = dict(closure)
        claimed_body_sha256 = body.pop("body_sha256", None)
        valid_after_ms, maximum_iterations = _validated_artifact_schedule(
            self.configuration,
            self.configuration.formal_campaign_id,
            artifacts,
        )
        segments = closure.get("segments")
        iterations = closure.get("iterations")
        segment_count = closure.get("segment_count")
        verified_at_ms = closure.get("verified_at_ms")
        closure_digest_fields = (
            "campaign_sha256", "policy_body_sha256", "policy_file_sha256",
            "strategy_sha256", "strategy_file_sha256",
            "observer_state_body_sha256", "observer_state_file_sha256",
            "strategy_state_file_sha256", "final_audit_body_sha256",
            "final_audit_file_sha256",
        )
        _require(
            set(closure) == VERIFIED_CLOSURE_FIELDS and
            closure.get("schema") ==
            "hepta.bounded-shadow-campaign-closure.v1" and
            closure.get("version") == 1 and
            closure.get("campaign_id") ==
            self.configuration.formal_campaign_id and
            closure.get("campaign_sha256") ==
            artifacts.policy.get("campaign_sha256") and
            closure.get("policy_body_sha256") ==
            artifacts.policy.get("body_sha256") and
            closure.get("policy_file_sha256") ==
            artifacts.policy_file_sha256 and
            closure.get("strategy_id") ==
            artifacts.policy.get("strategy_id") and
            closure.get("strategy_version") ==
            artifacts.policy.get("strategy_version") and
            closure.get("strategy_sha256") ==
            artifacts.policy.get("strategy_sha256") and
            closure.get("strategy_file_sha256") ==
            verified_artifacts.strategy_file_sha256 and
            closure.get("observer_state_body_sha256") ==
            state.get("body_sha256") and
            closure.get("observer_state_file_sha256") ==
            final_artifacts.observer_state_file_sha256 and
            closure.get("final_audit_body_sha256") ==
            final_artifacts.final_audit_body_sha256 and
            closure.get("final_audit_file_sha256") ==
            final_artifacts.final_audit_file_sha256 and
            closure.get("final_audit_file_sha256") ==
            state.get("final_audit_receipt_sha256") and
            closure.get("completed_iterations") == maximum_iterations and
            closure.get("maximum_iterations") == maximum_iterations and
            closure.get("iteration_count") == maximum_iterations and
            type(segment_count) is int and segment_count >= 1 and
            isinstance(segments, list) and len(segments) == segment_count and
            isinstance(iterations, list) and
            len(iterations) == maximum_iterations and
            type(verified_at_ms) is int and
            verified_at_ms >= state.get("last_generated_at_ms", -1) and
            closure.get("complete_revalidation") is False and
            closure.get("closure_status") ==
            "VERIFIED_WITH_RETAINED_EVIDENCE_RESIDUALS" and
            isinstance(closure.get("residual_evidence"), list) and
            all(
                isinstance(value, str) and value
                for value in closure["residual_evidence"]
            ) and
            closure["residual_evidence"] ==
            sorted(set(closure["residual_evidence"])) and
            all(
                isinstance(closure.get(field), str) and
                DIGEST.fullmatch(closure[field]) is not None
                for field in closure_digest_fields
            ) and
            isinstance(claimed_body_sha256, str) and
            DIGEST.fullmatch(claimed_body_sha256) is not None and
            claimed_body_sha256 == digest_bytes(canonical_bytes(body)) and
            isinstance(verified_artifacts.closure_file_sha256, str) and
            DIGEST.fullmatch(
                verified_artifacts.closure_file_sha256) is not None and
            verified_artifacts.closure_file_sha256 ==
            digest_bytes(canonical_bytes(closure)),
            "P1_LAUNCHER_VERIFIED_CLOSURE_INVALID",
        )

        for index, segment in enumerate(segments, start=1):
            _require(
                isinstance(segment, dict) and
                set(segment) == VERIFIED_SEGMENT_FIELDS and
                type(segment.get("segment_index")) is int and
                segment.get("segment_index") == index and
                type(segment.get("record_count")) is int and
                segment["record_count"] >= 1 and
                all(
                    type(segment.get(field)) is int and segment[field] >= 1
                    for field in (
                        "history_record_bytes", "history_index_bytes",
                        "history_storage_bytes",
                    )
                ) and
                segment["history_storage_bytes"] ==
                segment["history_record_bytes"] +
                segment["history_index_bytes"] and
                all(
                    isinstance(segment.get(field), str) and
                    DIGEST.fullmatch(segment[field]) is not None
                    for field in (
                        "history_head_sha256", "source_sha256",
                        "audit_sha256",
                    )
                ),
                "P1_LAUNCHER_VERIFIED_CLOSURE_INVALID",
            )

        previous_segment = 0
        digest_iteration_fields = (
            VERIFIED_ITERATION_FIELDS - {
                "iteration", "segment_index", "scheduled_at_ms",
                "evaluated_at_ms", "source_first_sequence",
                "source_last_sequence", "source_record_count",
                "source_total_record_count", "source_window_truncated",
                "source_predecessor_record_sha256",
                "materialization_window_ms",
                "materialization_maximum_records", "source_attestation",
                "final_outcome", "residual_evidence",
            }
        )
        for index, iteration in enumerate(iterations, start=1):
            scheduled_at_ms = (
                valid_after_ms +
                (index - 1) * artifacts.policy["slot_interval_ms"])
            evaluated_at_ms = (
                iteration.get("evaluated_at_ms")
                if isinstance(iteration, dict) else None)
            current_segment = (
                iteration.get("segment_index")
                if isinstance(iteration, dict) else None)
            attestation = (
                iteration.get("source_attestation")
                if isinstance(iteration, dict) else None)
            predecessor = (
                iteration.get("source_predecessor_record_sha256")
                if isinstance(iteration, dict) else None)
            residual_evidence = (
                iteration.get("residual_evidence")
                if isinstance(iteration, dict) else None)
            _require(
                isinstance(iteration, dict) and
                set(iteration) == VERIFIED_ITERATION_FIELDS and
                type(iteration.get("iteration")) is int and
                iteration.get("iteration") == index and
                type(current_segment) is int and
                1 <= current_segment <= segment_count and
                current_segment >= previous_segment and
                iteration.get("scheduled_at_ms") == scheduled_at_ms and
                type(evaluated_at_ms) is int and
                scheduled_at_ms <= evaluated_at_ms <=
                scheduled_at_ms + artifacts.policy["maximum_lateness_ms"] and
                type(iteration.get("source_first_sequence")) is int and
                iteration["source_first_sequence"] >= 1 and
                type(iteration.get("source_last_sequence")) is int and
                iteration["source_last_sequence"] >=
                iteration["source_first_sequence"] and
                type(iteration.get("source_record_count")) is int and
                iteration["source_record_count"] >= 1 and
                type(iteration.get("source_total_record_count")) is int and
                iteration["source_total_record_count"] ==
                iteration["source_last_sequence"] and
                iteration["source_record_count"] ==
                iteration["source_last_sequence"] -
                iteration["source_first_sequence"] + 1 and
                type(iteration.get("source_window_truncated")) is bool and
                iteration["source_window_truncated"] is
                (iteration["source_first_sequence"] > 1) and
                (
                    predecessor is None and
                    iteration["source_first_sequence"] == 1 or
                    isinstance(predecessor, str) and
                    DIGEST.fullmatch(predecessor) is not None and
                    iteration["source_first_sequence"] > 1
                ) and
                type(iteration.get("materialization_window_ms")) is int and
                iteration["materialization_window_ms"] >= 1 and
                type(iteration.get("materialization_maximum_records")) is int and
                iteration["materialization_maximum_records"] >= 1 and
                isinstance(iteration.get("final_outcome"), str) and
                bool(iteration["final_outcome"]) and
                isinstance(residual_evidence, list) and
                all(isinstance(value, str) and value
                    for value in residual_evidence) and
                residual_evidence == sorted(set(residual_evidence)) and
                isinstance(attestation, dict) and
                set(attestation) == VERIFIED_SOURCE_ATTESTATION_FIELDS and
                attestation.get("raw_payloads_verified") is True and
                all(
                    isinstance(attestation.get(field), str) and
                    DIGEST.fullmatch(attestation[field]) is not None
                    for field in VERIFIED_SOURCE_ATTESTATION_FIELDS -
                    {"raw_payloads_verified"}
                ) and
                all(
                    isinstance(iteration.get(field), str) and
                    DIGEST.fullmatch(iteration[field]) is not None
                    for field in digest_iteration_fields
                ),
                "P1_LAUNCHER_VERIFIED_CLOSURE_INVALID",
            )
            previous_segment = current_segment

        _require(
            iterations[0]["scheduled_at_ms"] == valid_after_ms and
            iterations[-1]["scheduled_at_ms"] -
            iterations[0]["scheduled_at_ms"] ==
            (FORMAL_ITERATIONS - 1) * POLICY_SLOT_INTERVAL_MS and
            state.get("final_audit_segment_count") == segment_count and
            sum(segment["record_count"] for segment in segments) ==
            state.get("sample_count"),
            "P1_LAUNCHER_VERIFIED_CLOSURE_INVALID",
        )
        self.evidence.formal_verified_closure_file_sha256 = (
            verified_artifacts.closure_file_sha256)
        self.evidence.formal_verified_closure_body_sha256 = (
            claimed_body_sha256)

    def _cleanup(self) -> list[str]:
        errors: list[str] = []
        for unit in (self.formal_host_unit, self.formal_reader_unit,
                     self.probe_host_unit, self.probe_reader_unit):
            try:
                self.executor.stop_unit(unit)
            except Exception as error:  # cleanup must continue
                errors.append(f"STOP_{unit}:{_reason(error)}")
        if self.formal_provisioned or self.probe_provisioned:
            try:
                closure = self.executor.close_and_verify("service-stop")
                if self.formal_provisioned:
                    self.evidence.formal_closure = closure
                elif self.probe_provisioned:
                    self.evidence.probe_closure = closure
            except Exception as error:  # cleanup must continue to receipt
                errors.append("CLOSE:" + _reason(error))
        else:
            try:
                self.executor.assert_clean()
            except Exception as error:
                errors.append("VERIFY:" + _reason(error))
        return errors

    def run(self) -> dict[str, Any]:
        terminal: BaseException | None = None
        final_status = "FAILED_CLOSED"
        cleanup_errors: list[str] = []
        receipt_written = False
        final_receipt: dict[str, Any] | None = None
        try:
            # ``formal_start_ms`` is the warmup start.  The policy's
            # ``valid_after_ms`` is the distinct decision-window start.
            # Reject stale or far-future launch attempts before any probe,
            # reader, WATCH, or filesystem orchestration side effect.
            self._assert_start_clock()
            self.executor.prepare(self.paths)
            self.evidence.helper_sha256 = self.executor.helper_hashes()
            self.evidence.launcher_identity = self.executor.launcher_identity(
                self.launcher_unit, os.getpid(), self.configuration)
            self._check_launcher_identity()
            self.evidence.activation_binding = (
                self.executor.activation_binding())
            self.evidence.gateway_identity = self.executor.gateway_identity()
            self.executor.assert_clean()
            self.executor.assert_paper_inactive()
            # Arm the persistent reconcile timer before any owner or WATCH
            # authority exists.  The supervise service is started again
            # immediately after each exact provision.
            self.executor.start_backstop()

            probe_artifacts = self.executor.build_policy(
                "load-probe", self.configuration, self.paths)
            self.evidence.probe_policy_sha256 = (
                probe_artifacts.policy_file_sha256)
            self.evidence.probe_marker_sha256 = (
                probe_artifacts.marker_file_sha256)
            self.store.write_state(self.paths, self._state())

            probe_pid = self.executor.start_reader(
                self.configuration.probe_campaign_id,
                self.probe_reader_unit,
                self.launcher_unit,
                self.paths.probe_policy,
                self.paths.probe_marker,
                self.paths,
                formal=False,
            )
            self.probe_reader_started = True
            self.evidence.probe_reader_pid = probe_pid
            probe_registration = self.executor.provision(
                self.configuration.probe_campaign_id, probe_pid)
            self._check_registration(
                probe_registration, self.configuration.probe_campaign_id)
            self.probe_provisioned = True
            self.evidence.probe_generation = probe_registration.generation
            self.executor.start_backstop()

            host_receipt, host_receipt_sha256 = self.executor.run_probe_host(
                self.configuration, self.paths, self.probe_reader_unit,
                probe_registration.generation,
                self.evidence.helper_sha256["capture_sha256"],
            )
            _reject_permissions(host_receipt)
            closure = host_receipt.get("close_result")
            _require(
                host_receipt.get("status") == "LOAD_PROBE_COMPLETE" and
                host_receipt.get("collector_runs") == LOAD_PROBE_RUNS and
                isinstance(closure, dict) and
                closure.get("authoritative_revoke_outcome") == "ACCEPTED" and
                closure.get("local_authority_removed") is True and
                closure.get("export_evidence_removed") is True,
                "P1_LAUNCHER_PROBE_HOST_RECEIPT_INVALID",
            )
            self.evidence.probe_host_receipt_sha256 = host_receipt_sha256
            self.evidence.probe_closure = closure

            self.executor.stop_unit(self.probe_reader_unit)
            self.probe_reader_started = False
            self.executor.close_and_verify("service-stop")
            self.probe_provisioned = False

            # Probe history belongs to the disposable probe campaign.  Wait
            # until the bounded preparation window before minting the fresh
            # formal admission/policy pair; it must not age while the formal
            # 210-minute evidence window is still in the future.
            self._wait_for_formal_preparation_window()

            self._check_helpers_unchanged(
                "P1_LAUNCHER_HELPER_DRIFT_BEFORE_VALIDATION")
            self.executor.assert_paper_inactive()
            admission, admission_sha256 = self.executor.validate_probe(
                self.configuration, self.paths)
            self.evidence.admission_receipt_sha256 = admission_sha256
            _require(
                self.executor.gateway_identity() ==
                self.evidence.gateway_identity,
                "P1_LAUNCHER_GATEWAY_DRIFT_AFTER_PROBE",
            )
            self._check_activation_unchanged(
                "P1_LAUNCHER_ACTIVATION_DRIFT_AFTER_PROBE")

            formal_artifacts = self.executor.build_policy(
                "formal", self.configuration, self.paths)
            self.evidence.formal_policy_sha256 = (
                formal_artifacts.policy_file_sha256)
            self.evidence.formal_marker_sha256 = (
                formal_artifacts.marker_file_sha256)
            valid_after_ms, expected_iterations = self._check_admission(
                admission, formal_artifacts)
            warmup_start_ms = self.configuration.formal_start_ms
            decision_window_start_ms = valid_after_ms
            _require(
                decision_window_start_ms - warmup_start_ms ==
                POLICY_MINIMUM_WARMUP_MS,
                "P1_LAUNCHER_FORMAL_START_WINDOW_INVALID",
            )
            self.evidence.formal_valid_after_ms = valid_after_ms
            self.evidence.formal_expected_iterations = expected_iterations
            self._check_helpers_unchanged(
                "P1_LAUNCHER_HELPER_DRIFT_AFTER_FORMAL_POLICY")
            self.executor.assert_paper_inactive()
            _require(
                self.executor.gateway_identity() ==
                self.evidence.gateway_identity,
                "P1_LAUNCHER_GATEWAY_DRIFT_BEFORE_FORMAL",
            )
            self._check_activation_unchanged(
                "P1_LAUNCHER_ACTIVATION_DRIFT_BEFORE_FORMAL")

            # No formal reader, WATCH generation, or history segment exists
            # before this immutable warmup anchor.
            self._wait_for_formal_warmup_start()

            formal_pid = self.executor.start_reader(
                self.configuration.formal_campaign_id,
                self.formal_reader_unit,
                self.launcher_unit,
                self.paths.formal_policy,
                self.paths.formal_marker,
                self.paths,
                formal=True,
            )
            self.formal_reader_started = True
            self.evidence.formal_reader_pid = formal_pid
            self._check_helpers_unchanged(
                "P1_LAUNCHER_HELPER_DRIFT_BEFORE_FORMAL_PROVISION")
            self.executor.assert_paper_inactive()
            formal_registration = self.executor.provision(
                self.configuration.formal_campaign_id, formal_pid)
            self._check_registration(
                formal_registration, self.configuration.formal_campaign_id)
            self.formal_provisioned = True
            self.evidence.formal_generation = formal_registration.generation
            self.executor.start_backstop()

            formal_result = self.executor.run_formal_host(
                self.configuration, self.paths, self.formal_reader_unit,
                formal_registration.generation,
                self.evidence.helper_sha256["capture_sha256"],
                formal_artifacts,
            )
            _reject_permissions(formal_result)
            formal_completed_iterations = formal_result.get(
                "completed_iterations")
            _require(
                set(formal_result) == FORMAL_RESULT_FIELDS and
                formal_result.get("schema") ==
                "hepta.p1-shadow-host-controller-result.v1" and
                formal_result.get("campaign_id") ==
                self.configuration.formal_campaign_id and
                formal_result.get("status") == "ITERATIONS_COMPLETE" and
                type(formal_result.get("lease_generation")) is int and
                formal_result["lease_generation"] >=
                formal_registration.generation and
                type(formal_result.get("collector_runs")) is int and
                formal_result["collector_runs"] >= expected_iterations and
                type(formal_completed_iterations) is int and
                formal_completed_iterations == expected_iterations and
                isinstance(formal_result.get("close_result"), dict),
                "P1_LAUNCHER_FORMAL_RESULT_INVALID",
            )
            formal_closure = formal_result["close_result"]
            _require(
                formal_closure.get("schema") ==
                "hepta.shadow-watch-custodian-closure.v1" and
                formal_closure.get("campaign_id") ==
                self.configuration.formal_campaign_id and
                formal_closure.get("lease_generation") ==
                formal_result.get("lease_generation") and
                formal_closure.get("authoritative_revoke_outcome") in {
                    "ACCEPTED", "ALREADY_ABSENT", "EXPIRED"} and
                formal_closure.get("local_authority_removed") is True and
                formal_closure.get("export_evidence_removed") is True,
                "P1_LAUNCHER_FORMAL_CLOSURE_INVALID",
            )
            self.evidence.formal_closure = formal_closure
            self.evidence.formal_host_result_sha256 = digest_bytes(
                canonical_bytes(formal_result))
            self._check_helpers_unchanged(
                "P1_LAUNCHER_HELPER_DRIFT_AFTER_FORMAL_HOST")
            self.executor.assert_paper_inactive()
            initial_reader_artifacts = self.executor.read_formal_evidence(
                self.paths)
            self._check_final_reader_evidence(
                formal_artifacts,
                formal_result,
                formal_closure,
                initial_reader_artifacts,
                formal_registration.generation,
            )
            self._check_reader_completion(
                formal_result, initial_reader_artifacts)
            verified_closure_artifacts = (
                self.executor.verify_formal_closure(self.paths))
            self._check_verified_closure(
                formal_artifacts,
                initial_reader_artifacts,
                verified_closure_artifacts,
            )
            self._check_helpers_unchanged(
                "P1_LAUNCHER_HELPER_DRIFT_AFTER_VERIFIER")
            self.executor.assert_paper_inactive()
            reader_liveness = self.executor.assert_reader_active(
                self.formal_reader_unit, formal_pid)
            final_reader_artifacts = self.executor.read_formal_evidence(
                self.paths)
            self._check_post_verifier_reader_evidence(
                formal_result,
                formal_artifacts,
                formal_closure,
                initial_reader_artifacts,
                final_reader_artifacts,
                formal_registration.generation,
                reader_liveness,
            )
            self._check_activation_unchanged(
                "P1_LAUNCHER_ACTIVATION_DRIFT_BEFORE_COMPLETION")
            self.executor.stop_unit(self.formal_reader_unit)
            self.formal_reader_started = False
            self.executor.close_and_verify("service-stop")
            self.formal_provisioned = False
            final_status = "FORMAL_COMPLETE"
        except BaseException as error:
            terminal = error
        finally:
            cleanup_errors = self._cleanup()
            if cleanup_errors:
                final_status = "FAILED_UNCLEAN"
            if terminal is None and not cleanup_errors:
                try:
                    # Reconsume the fixed installation generation after all
                    # campaign cleanup and immediately before sealing the
                    # terminal receipt.  Supported installs are also barred
                    # while WATCH/Gateway units are active by installer
                    # preflight, so this closes the final publication seam.
                    self._check_activation_unchanged(
                        "P1_LAUNCHER_ACTIVATION_DRIFT_AT_FINAL_COMMIT")
                except BaseException as error:
                    terminal = error
                    final_status = "FAILED_CLOSED"
            reason = None if terminal is None else _reason(terminal)
            final_receipt = self._receipt(
                status=final_status, reason=reason,
                cleanup_errors=cleanup_errors)
            try:
                self.store.write_receipt(self.paths, final_receipt)
                receipt_written = True
            except Exception as error:
                if terminal is None:
                    terminal = error
                else:
                    cleanup_errors.append("RECEIPT:" + _reason(error))
        _require(receipt_written, "P1_LAUNCHER_RECEIPT_NOT_WRITTEN")
        if terminal is not None:
            raise LauncherError(_reason(terminal)) from terminal
        _require(not cleanup_errors, "P1_LAUNCHER_CLEANUP_FAILED")
        _require(final_receipt is not None, "P1_LAUNCHER_RECEIPT_MISSING")
        return final_receipt


def _reason(error: BaseException) -> str:
    value = str(error)
    return value if re.fullmatch(r"[A-Z0-9_.:-]{3,256}", value) else (
        "P1_LAUNCHER_FAILED")


class ProductionStateStore:
    @staticmethod
    def _write(path: Path, document: dict[str, Any]) -> None:
        _reject_permissions(document)
        _require(path.is_absolute(), "P1_LAUNCHER_STATE_PATH_INVALID")
        flags = os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC
        if hasattr(os, "O_NOFOLLOW"):
            flags |= os.O_NOFOLLOW
        try:
            directory_fd = os.open(path.parent, flags)
            parent = os.fstat(directory_fd)
        except OSError as error:
            raise LauncherError("P1_LAUNCHER_STATE_DIRECTORY_INVALID") from error
        try:
            _require(
                stat.S_ISDIR(parent.st_mode) and parent.st_uid == 0 and
                parent.st_gid == 0 and stat.S_IMODE(parent.st_mode) == 0o700,
                "P1_LAUNCHER_STATE_DIRECTORY_INVALID",
            )
            create = os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_CLOEXEC
            if hasattr(os, "O_NOFOLLOW"):
                create |= os.O_NOFOLLOW
            descriptor = os.open(path.name, create, 0o600, dir_fd=directory_fd)
            try:
                os.fchmod(descriptor, 0o600)
                contents = canonical_bytes(document)
                offset = 0
                while offset < len(contents):
                    offset += os.write(descriptor, contents[offset:])
                os.fsync(descriptor)
                metadata = os.fstat(descriptor)
                _require(
                    metadata.st_uid == 0 and metadata.st_gid == 0 and
                    stat.S_IMODE(metadata.st_mode) == 0o600 and
                    metadata.st_nlink == 1 and
                    metadata.st_size == len(contents),
                    "P1_LAUNCHER_STATE_FILE_INVALID",
                )
            finally:
                os.close(descriptor)
            os.fsync(directory_fd)
        except FileExistsError as error:
            raise LauncherError("P1_LAUNCHER_STATE_EXISTS") from error
        except OSError as error:
            raise LauncherError("P1_LAUNCHER_STATE_WRITE_FAILED") from error
        finally:
            os.close(directory_fd)

    def write_state(self, paths: RunPaths, document: dict[str, Any]) -> None:
        self._write(paths.state, document)

    def write_receipt(self, paths: RunPaths, document: dict[str, Any]) -> None:
        self._write(paths.receipt, document)


class ProductionExecutor:
    def _run(
        self,
        arguments: list[str],
        timeout: float,
        *,
        allow_failure: bool = False,
    ) -> subprocess.CompletedProcess[str]:
        _require(
            bool(arguments) and arguments[0] in ALLOWED_EXECUTABLES and
            (arguments[0] != str(BROKER_INTERPRETER) or
             tuple(arguments) == BROKER_DENY_ALL_CHECK_COMMAND) and
            all(isinstance(argument, str) and "\x00" not in argument
                for argument in arguments) and
            0 < timeout <= 400_000,
            "P1_LAUNCHER_COMMAND_INVALID",
        )
        try:
            result = subprocess.run(
                arguments,
                check=False,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                encoding="utf-8",
                errors="strict",
                cwd="/",
                env=SANITIZED_ENVIRONMENT,
                close_fds=True,
                timeout=timeout,
            )
        except (OSError, subprocess.SubprocessError, UnicodeError) as error:
            raise LauncherError("P1_LAUNCHER_COMMAND_FAILED") from error
        _require(
            len(result.stdout.encode("utf-8")) <= MAXIMUM_OUTPUT_BYTES and
            len(result.stderr.encode("utf-8")) <= MAXIMUM_OUTPUT_BYTES,
            "P1_LAUNCHER_COMMAND_OUTPUT_TOO_LARGE",
        )
        if not allow_failure:
            _require(
                result.returncode == 0 and result.stderr == "",
                "P1_LAUNCHER_COMMAND_REJECTED",
            )
        return result

    @staticmethod
    def _ensure_directory(
        path: Path,
        *,
        uid: int,
        gid: int,
        mode: int,
    ) -> None:
        _require(path.is_absolute(), "P1_LAUNCHER_DIRECTORY_INVALID")
        try:
            path.mkdir(parents=True, mode=mode, exist_ok=False)
            os.chown(path, uid, gid)
            os.chmod(path, mode)
        except FileExistsError as error:
            raise LauncherError("P1_LAUNCHER_DIRECTORY_EXISTS") from error
        except OSError as error:
            raise LauncherError("P1_LAUNCHER_DIRECTORY_CREATE_FAILED") from error
        metadata = path.lstat()
        _require(
            stat.S_ISDIR(metadata.st_mode) and not path.is_symlink() and
            metadata.st_uid == uid and metadata.st_gid == gid and
            stat.S_IMODE(metadata.st_mode) == mode,
            "P1_LAUNCHER_DIRECTORY_INVALID",
        )

    def prepare(self, paths: RunPaths) -> None:
        # Installer creates STATE_BASE and its three root-owned parents.  A
        # launcher never repairs or reuses a previous round directory.
        for parent, mode in (
            (STATE_BASE, 0o755),
            (STATE_BASE / "private", 0o700),
            (STATE_BASE / "public", 0o755),
            (STATE_BASE / "readers", 0o755),
        ):
            metadata = parent.lstat()
            _require(
                stat.S_ISDIR(metadata.st_mode) and not parent.is_symlink() and
                metadata.st_uid == 0 and metadata.st_gid == 0 and
                stat.S_IMODE(metadata.st_mode) == mode,
                "P1_LAUNCHER_BASE_DIRECTORY_INVALID",
            )
        self._ensure_directory(
            paths.private_directory, uid=0, gid=0, mode=0o700)
        self._ensure_directory(
            paths.public_directory, uid=0, gid=0, mode=0o755)
        for directory in (
            paths.probe_reader_directory,
            paths.formal_reader_directory,
        ):
            self._ensure_directory(
                directory, uid=READER_UID, gid=READER_GID, mode=0o700)
            self._ensure_directory(
                directory / "observer", uid=READER_UID, gid=READER_GID,
                mode=0o700)

    def helper_hashes(self) -> dict[str, str]:
        result = {
            field: digest_bytes(_secure_read(
                path,
                "P1_LAUNCHER_HELPER_INVALID",
                64 * 1024 * 1024,
                expected_uid=ROOT_UID,
                expected_gid=ROOT_GID,
                modes=HELPER_MODES[field],
            ))
            for field, path in HELPERS.items()
        }
        _require(set(result) == set(HELPERS), "P1_LAUNCHER_HELPER_INVALID")
        return result

    @staticmethod
    def _read_proc_pseudo_file(
        process_fd: int,
        name: str,
        maximum_bytes: int,
    ) -> bytes:
        reason = "P1_LAUNCHER_BROKER_PROCESS_INVALID"
        _require(
            type(process_fd) is int and process_fd >= 0 and
            name in {"stat", "cmdline"} and
            type(maximum_bytes) is int and 1 <= maximum_bytes <= 1024 * 1024,
            reason,
        )
        flags = os.O_RDONLY | os.O_CLOEXEC
        if hasattr(os, "O_NOFOLLOW"):
            flags |= os.O_NOFOLLOW
        descriptor: int | None = None
        try:
            descriptor = os.open(name, flags, dir_fd=process_fd)
            before = os.fstat(descriptor)
            _require(stat.S_ISREG(before.st_mode), reason)
            chunks: list[bytes] = []
            total = 0
            while True:
                chunk = os.read(descriptor, min(65_536, maximum_bytes + 1 - total))
                if not chunk:
                    break
                chunks.append(chunk)
                total += len(chunk)
                _require(total <= maximum_bytes, reason)
            after = os.fstat(descriptor)
            _require(
                ProductionExecutor._stable_metadata(before) ==
                ProductionExecutor._stable_metadata(after),
                reason,
            )
            return b"".join(chunks)
        except LauncherError:
            raise
        except OSError as error:
            raise LauncherError(reason) from error
        finally:
            if descriptor is not None:
                os.close(descriptor)

    @staticmethod
    def _current_boot_id() -> str:
        reason = "P1_LAUNCHER_ACTIVATION_BOOT_ID_INVALID"
        path = "/proc/sys/kernel/random/boot_id"

        def read_once() -> bytes:
            flags = os.O_RDONLY | os.O_CLOEXEC
            if hasattr(os, "O_NOFOLLOW"):
                flags |= os.O_NOFOLLOW
            descriptor: int | None = None
            try:
                descriptor = os.open(path, flags)
                before = os.fstat(descriptor)
                payload = os.read(descriptor, 128)
                _require(os.read(descriptor, 1) == b"", reason)
                after = os.fstat(descriptor)
                _require(
                    stat.S_ISREG(before.st_mode) and
                    ProductionExecutor._stable_metadata(before) ==
                    ProductionExecutor._stable_metadata(after),
                    reason,
                )
                return payload
            except LauncherError:
                raise
            except OSError as error:
                raise LauncherError(reason) from error
            finally:
                if descriptor is not None:
                    os.close(descriptor)

        before = read_once()
        after = read_once()
        _require(before == after, reason)
        try:
            value = before.decode("ascii")
        except UnicodeError as error:
            raise LauncherError(reason) from error
        _require(
            value.endswith("\n") and value.count("\n") == 1 and
            BOOT_ID.fullmatch(value[:-1]) is not None,
            reason,
        )
        return value[:-1]

    @staticmethod
    def _broker_process_evidence(pid: int) -> dict[str, Any]:
        reason = "P1_LAUNCHER_BROKER_PROCESS_INVALID"
        _require(type(pid) is int and pid > 1, reason)
        flags = os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC
        if hasattr(os, "O_NOFOLLOW"):
            flags |= os.O_NOFOLLOW
        process_fd: int | None = None
        try:
            process_fd = os.open(f"/proc/{pid}", flags)
            directory_before = os.fstat(process_fd)
            stat_before = ProductionExecutor._read_proc_pseudo_file(
                process_fd, "stat", 16 * 1024)
            cmdline = ProductionExecutor._read_proc_pseudo_file(
                process_fd, "cmdline", 64 * 1024)
            stat_after = ProductionExecutor._read_proc_pseudo_file(
                process_fd, "stat", 16 * 1024)
            directory_after = os.fstat(process_fd)
            _require(
                stat_before == stat_after and
                ProductionExecutor._stable_metadata(directory_before) ==
                ProductionExecutor._stable_metadata(directory_after),
                reason,
            )
            closing = stat_before.rfind(b") ")
            _require(
                stat_before.startswith(f"{pid} (".encode("ascii")) and
                closing > len(str(pid)) + 2,
                reason,
            )
            stat_fields = stat_before[closing + 2:].split()
            _require(len(stat_fields) >= 20, reason)
            try:
                starttime_ticks = int(stat_fields[19])
            except (TypeError, ValueError) as error:
                raise LauncherError(reason) from error
            _require(starttime_ticks > 0, reason)
            expected_cmdline = b"\0".join(
                value.encode("ascii") for value in (
                    str(BROKER_INTERPRETER), "-I", "-S",
                    str(BROKER_CREDENTIAL_SOURCE), "--supervise-deny-all",
                    "--paper-identities", str(BROKER_PAPER_IDENTITIES),
                )) + b"\0"
            _require(cmdline == expected_cmdline, reason)
            executable = os.stat("exe", dir_fd=process_fd, follow_symlinks=True)
            expected_executable = os.stat(BROKER_INTERPRETER)
            _require(
                stat.S_ISREG(executable.st_mode) and
                (executable.st_dev, executable.st_ino) ==
                (expected_executable.st_dev, expected_executable.st_ino),
                reason,
            )
            interpreter, _interpreter_identity = (
                ProductionExecutor._read_anchored_root_file(
                BROKER_INTERPRETER,
                reason,
                maximum_bytes=64 * 1024 * 1024,
                mode=0o755,
            ))
            credential, _credential_identity = (
                ProductionExecutor._read_anchored_root_file(
                BROKER_CREDENTIAL_SOURCE,
                reason,
                maximum_bytes=64 * 1024 * 1024,
                mode=0o400,
            ))
            final_stat = ProductionExecutor._read_proc_pseudo_file(
                process_fd, "stat", 16 * 1024)
            _require(final_stat == stat_before, reason)
            return {
                "process_starttime_ticks": starttime_ticks,
                "cmdline_sha256": digest_bytes(cmdline),
                "interpreter_sha256": digest_bytes(interpreter),
                "credential_source_sha256": digest_bytes(credential),
            }
        except LauncherError:
            raise
        except OSError as error:
            raise LauncherError(reason) from error
        finally:
            if process_fd is not None:
                os.close(process_fd)

    def _broker_activation_evidence(self) -> dict[str, Any]:
        reason = "P1_LAUNCHER_BROKER_EPOCH_INVALID"
        fields = {
            "ActiveState", "SubState", "InvocationID", "MainPID",
            "ExecMainStartTimestampMonotonic", "TasksCurrent", "StatusText",
        }

        def status() -> dict[str, str]:
            result = self._run([
                SYSTEMCTL, "show", "--no-pager",
                "--property=ActiveState", "--property=SubState",
                "--property=InvocationID", "--property=MainPID",
                "--property=ExecMainStartTimestampMonotonic",
                "--property=TasksCurrent", "--property=StatusText",
                BROKER_EGRESS_UNIT,
            ], 5)
            parsed: dict[str, str] = {}
            for line in result.stdout.splitlines():
                key, separator, value = line.partition("=")
                _require(
                    separator == "=" and key in fields and key not in parsed,
                    reason,
                )
                parsed[key] = value
            _require(
                set(parsed) == fields and
                parsed["ActiveState"] == "active" and
                parsed["SubState"] == "running" and
                re.fullmatch(r"[0-9a-f]{32}", parsed["InvocationID"])
                is not None and
                parsed["MainPID"].isdigit() and int(parsed["MainPID"]) > 1 and
                parsed["ExecMainStartTimestampMonotonic"].isdigit() and
                int(parsed["ExecMainStartTimestampMonotonic"]) > 0 and
                parsed["TasksCurrent"] == "1" and
                parsed["StatusText"] ==
                "HeptaTrader broker boundary exact deny-all",
                reason,
            )
            return parsed

        before = status()
        process_before = self._broker_process_evidence(int(before["MainPID"]))
        credential_before, credential_identity_before = (
            self._read_anchored_root_file(
                BROKER_CREDENTIAL_SOURCE,
                "P1_LAUNCHER_BROKER_CREDENTIAL_INVALID",
                mode=0o400,
                maximum_bytes=64 * 1024 * 1024,
            ))
        installed_before, installed_identity_before = (
            self._read_anchored_root_file(
                BROKER_EGRESS_POLICY,
                "P1_LAUNCHER_BROKER_INSTALLED_SOURCE_INVALID",
                mode=0o755,
                maximum_bytes=64 * 1024 * 1024,
            ))
        _require(
            credential_before == installed_before and
            process_before.get("credential_source_sha256") ==
            digest_bytes(credential_before),
            "P1_LAUNCHER_BROKER_LOADED_SOURCE_INVALID",
        )
        check = self._run(list(BROKER_DENY_ALL_CHECK_COMMAND), 30)
        match = re.fullmatch(
            r"hepta_broker_egress_policy: PASS "
            r"policy_sha256=([0-9a-f]{64}) "
            r"authorized_connectors=0 authorized_uids= protected_ports=4\n?",
            check.stdout,
        )
        _require(match is not None, "P1_LAUNCHER_BROKER_DENY_ALL_INVALID")
        after = status()
        process_after = self._broker_process_evidence(int(after["MainPID"]))
        credential_after, credential_identity_after = (
            self._read_anchored_root_file(
                BROKER_CREDENTIAL_SOURCE,
                "P1_LAUNCHER_BROKER_CREDENTIAL_INVALID",
                mode=0o400,
                maximum_bytes=64 * 1024 * 1024,
            ))
        installed_after, installed_identity_after = (
            self._read_anchored_root_file(
                BROKER_EGRESS_POLICY,
                "P1_LAUNCHER_BROKER_INSTALLED_SOURCE_INVALID",
                mode=0o755,
                maximum_bytes=64 * 1024 * 1024,
            ))
        _require(
            before == after and process_before == process_after and
            credential_before == credential_after ==
            installed_before == installed_after and
            credential_identity_before == credential_identity_after and
            installed_identity_before == installed_identity_after,
            "P1_LAUNCHER_BROKER_EPOCH_CHANGED",
        )
        return {
            "unit": BROKER_EGRESS_UNIT,
            "active_state": before["ActiveState"],
            "sub_state": before["SubState"],
            "main_pid": int(before["MainPID"]),
            "invocation_id": before["InvocationID"],
            "exec_main_start_timestamp_monotonic_us": int(
                before["ExecMainStartTimestampMonotonic"]),
            **process_before,
            "interpreter_path": str(BROKER_INTERPRETER),
            "credential_source_path": str(BROKER_CREDENTIAL_SOURCE),
            "installed_source_path": str(BROKER_EGRESS_POLICY),
            "installed_source_sha256": digest_bytes(installed_before),
            "status_text": before["StatusText"],
            "tasks_current": 1,
            "deny_all_policy_sha256": "sha256:" + match.group(1),
            "authorized_connectors": 0,
            "authorized_uids": [],
            "protected_ports": 4,
        }

    def _activation_unit_contract_sha256(self, unit: str) -> str:
        reason = "P1_LAUNCHER_ACTIVATION_UNIT_CONTRACT_INVALID"
        if unit == BROKER_EGRESS_UNIT:
            fields = ACTIVATION_BROKER_UNIT_CONTRACT_FIELDS
            attempts = 8
        elif unit == GATEWAY_UNIT:
            fields = ACTIVATION_GATEWAY_UNIT_CONTRACT_FIELDS
            attempts = 1
        else:
            raise LauncherError(reason)
        parsed: dict[str, str] = {}
        for attempt in range(attempts):
            result = self._run([
                SYSTEMCTL, "show", "--no-pager",
                *(f"--property={field}" for field in fields), unit,
            ], 5)
            current: dict[str, str] = {}
            for line in result.stdout.splitlines():
                key, separator, value = line.partition("=")
                _require(
                    separator == "=" and key in fields and
                    key not in current,
                    reason,
                )
                current[key] = value
            _require(set(current) == set(fields), reason)
            parsed = current
            if unit != BROKER_EGRESS_UNIT or parsed["TasksCurrent"] == "1":
                break
            if attempt + 1 < attempts:
                time.sleep(0.01)
        if unit == BROKER_EGRESS_UNIT:
            _require(
                parsed["LoadState"] == "loaded" and
                parsed["ActiveState"] == "active" and
                parsed["SubState"] == "running" and
                parsed["Type"] == "notify" and
                parsed["NotifyAccess"] == "main" and
                parsed["StatusText"] ==
                "HeptaTrader broker boundary exact deny-all" and
                parsed["TasksCurrent"] == "1" and
                parsed["NRestarts"] == "0" and
                "--supervise-deny-all" in parsed["ExecStart"] and
                "hepta-broker-egress-policy.py" in
                parsed["LoadCredential"],
                reason,
            )
        else:
            _require(
                parsed["LoadState"] == "loaded" and
                parsed["ActiveState"] == "active" and
                parsed["SubState"] == "running" and
                BROKER_EGRESS_UNIT in parsed["BindsTo"] and
                str(GATEWAY_PROFILE) in parsed["EnvironmentFiles"],
                reason,
            )
        return digest_bytes(canonical_bytes(parsed))

    def gateway_identity(self) -> dict[str, Any]:
        def status() -> dict[str, str]:
            result = self._run([
                SYSTEMCTL, "show", "--no-pager",
                "--property=ActiveState", "--property=SubState",
                "--property=InvocationID", "--property=MainPID",
                "--property=ExecMainStartTimestampMonotonic", GATEWAY_UNIT,
            ], 5)
            parsed: dict[str, str] = {}
            for line in result.stdout.splitlines():
                key, separator, value = line.partition("=")
                _require(separator == "=" and key not in parsed,
                         "P1_LAUNCHER_GATEWAY_INVALID")
                parsed[key] = value
            _require(
                parsed.get("ActiveState") == "active" and
                parsed.get("SubState") == "running" and
                re.fullmatch(
                    r"[0-9a-f]{32}", parsed.get("InvocationID", "")) and
                parsed.get("MainPID", "").isdigit() and
                int(parsed["MainPID"]) > 1 and
                parsed.get("ExecMainStartTimestampMonotonic", "").isdigit() and
                int(parsed["ExecMainStartTimestampMonotonic"]) > 0,
                "P1_LAUNCHER_GATEWAY_INVALID",
            )
            return parsed

        try:
            profile_before = read_alpha_gateway_profile(GATEWAY_PROFILE)
            parsed = status()
            process_profile = read_alpha_gateway_process_profile(
                int(parsed["MainPID"]))
            socket_before = read_alpha_gateway_socket(GATEWAY_SOCKET)
            profile_after = read_alpha_gateway_profile(GATEWAY_PROFILE)
            parsed_after = status()
            process_after = read_alpha_gateway_process_identity(
                int(parsed["MainPID"]))
            socket_after = read_alpha_gateway_socket(GATEWAY_SOCKET)
        except TrustDomainRuntimeError as error:
            raise LauncherError("P1_LAUNCHER_GATEWAY_INVALID") from error
        _require(
            profile_before == profile_after and parsed == parsed_after and
            process_profile.pid_directory_metadata ==
            process_after.pid_directory_metadata and
            process_profile.starttime_ticks == process_after.starttime_ticks and
            socket_before == socket_after,
            "P1_LAUNCHER_GATEWAY_CHANGED",
        )
        return {
            "gateway_invocation_id": parsed["InvocationID"],
            "gateway_main_pid": int(parsed["MainPID"]),
            "gateway_exec_main_start_timestamp_monotonic_us":
                int(parsed["ExecMainStartTimestampMonotonic"]),
            "gateway_socket_device": socket_before.metadata[0],
            "gateway_socket_inode": socket_before.metadata[1],
            "domain_config_sha256": digest_bytes(_secure_read(
                DOMAIN_CONFIG, "P1_LAUNCHER_DOMAIN_CONFIG_INVALID", 1024 * 1024)),
            "gateway_profile_sha256": digest_bytes(profile_before.raw),
            "gateway_process_profile_sha256": digest_bytes(
                process_profile.canonical_projection),
        }

    @staticmethod
    def _unix_socket_identity(
        path: Path,
        reason: str,
        *,
        expected_uid: int,
        expected_gid: int,
    ) -> tuple[int, int]:
        _require(path.is_absolute() and path.name not in {"", ".", ".."},
                 reason)
        parent_fd = ProductionExecutor._open_anchored_directory(
            path.parent, reason)
        _require(parent_fd is not None, reason)
        rebound_fd: int | None = None
        try:
            parent_before = os.fstat(parent_fd)
            before = os.stat(
                path.name, dir_fd=parent_fd, follow_symlinks=False)
            _require(
                stat.S_ISSOCK(before.st_mode) and before.st_nlink == 1 and
                before.st_uid == expected_uid and
                before.st_gid == expected_gid,
                reason,
            )
            rebound_fd = ProductionExecutor._open_anchored_directory(
                path.parent, reason)
            _require(rebound_fd is not None, reason)
            parent_after = os.fstat(rebound_fd)
            after = os.stat(
                path.name, dir_fd=rebound_fd, follow_symlinks=False)
            _require(
                ProductionExecutor._stable_metadata(parent_before) ==
                ProductionExecutor._stable_metadata(parent_after) and
                ProductionExecutor._stable_metadata(before) ==
                ProductionExecutor._stable_metadata(after),
                reason,
            )
            return before.st_dev, before.st_ino
        except LauncherError:
            raise
        except OSError as error:
            raise LauncherError(reason) from error
        finally:
            if rebound_fd is not None:
                os.close(rebound_fd)
            os.close(parent_fd)

    def _gateway_activation_evidence(self) -> dict[str, Any]:
        reason = "P1_LAUNCHER_ACTIVATION_GATEWAY_INVALID"
        before = self.gateway_identity()
        try:
            profile = read_alpha_gateway_profile(GATEWAY_PROFILE)
            process = read_alpha_gateway_process_profile(
                before["gateway_main_pid"])
        except TrustDomainRuntimeError as error:
            raise LauncherError(reason) from error
        flags = os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC
        if hasattr(os, "O_NOFOLLOW"):
            flags |= os.O_NOFOLLOW
        process_fd: int | None = None
        try:
            process_fd = os.open(
                f"/proc/{before['gateway_main_pid']}", flags)
            process_executable = os.stat(
                "exe", dir_fd=process_fd, follow_symlinks=True)
            expected_executable = os.stat(GATEWAY)
            _require(
                stat.S_ISREG(process_executable.st_mode) and
                (process_executable.st_dev, process_executable.st_ino) ==
                (expected_executable.st_dev, expected_executable.st_ino),
                reason,
            )
        except LauncherError:
            raise
        except OSError as error:
            raise LauncherError(reason) from error
        finally:
            if process_fd is not None:
                os.close(process_fd)
        gateway_executable = _secure_read(
            Path(GATEWAY),
            reason,
            64 * 1024 * 1024,
            expected_uid=ROOT_UID,
            expected_gid=ROOT_GID,
            modes=frozenset({0o755}),
        )
        supervisor_before = self._unix_socket_identity(
            SUPERVISOR_SOCKET, reason, expected_uid=2101, expected_gid=2101)
        after = self.gateway_identity()
        supervisor_after = self._unix_socket_identity(
            SUPERVISOR_SOCKET, reason, expected_uid=2101, expected_gid=2101)
        _require(
            before == after and
            supervisor_before == supervisor_after and
            digest_bytes(profile.raw) == EXPECTED_GATEWAY_PROFILE_SHA256 and
            all(
                profile.values.get(field) == expected and
                process.values.get(field) == expected
                for field, expected in EXPECTED_GATEWAY_PROFILE_VALUES.items()
            ) and
            digest_bytes(process.canonical_projection) ==
            before["gateway_process_profile_sha256"] and
            process.starttime_ticks > 0,
            reason,
        )
        return {
            "unit": GATEWAY_UNIT,
            "active_state": "active",
            "sub_state": "running",
            "gateway_main_pid": before["gateway_main_pid"],
            "gateway_invocation_id": before["gateway_invocation_id"],
            "gateway_exec_main_start_timestamp_monotonic_us":
                before["gateway_exec_main_start_timestamp_monotonic_us"],
            "process_starttime_ticks": process.starttime_ticks,
            "gateway_executable_path": GATEWAY,
            "gateway_executable_sha256": digest_bytes(gateway_executable),
            "domain_config_sha256": before["domain_config_sha256"],
            "gateway_profile_path": str(GATEWAY_PROFILE),
            "gateway_profile_sha256": before["gateway_profile_sha256"],
            "gateway_process_profile_sha256":
                before["gateway_process_profile_sha256"],
            "execution_remote_mode":
                profile.values["HEPTA_EXECUTION_REMOTE_MODE"],
            "tool_account": profile.values["HEPTA_TOOL_ACCOUNT"],
            "execution_domain_id":
                profile.values["HEPTA_EXECUTION_DOMAIN_ID"],
            "tool_allow_trade": profile.values["HEPTA_TOOL_ALLOW_TRADE"],
            "session_templates":
                profile.values["HEPTA_TOOL_SESSION_TEMPLATES"],
            "contract_bindings":
                profile.values["HEPTA_TOOL_CONTRACT_BINDINGS"],
            "gateway_socket_path": str(GATEWAY_SOCKET),
            "gateway_socket_device": before["gateway_socket_device"],
            "gateway_socket_inode": before["gateway_socket_inode"],
            "supervisor_socket_path": str(SUPERVISOR_SOCKET),
            "supervisor_socket_device": supervisor_before[0],
            "supervisor_socket_inode": supervisor_before[1],
        }

    @staticmethod
    def _validate_activation_receipt(
        receipt: dict[str, Any],
        *,
        receipt_contents: bytes,
        profile_receipt: dict[str, Any],
        profile_receipt_contents: bytes,
        boot_id: str,
        predecessor_activation_success: dict[str, Any],
        predecessor_activation_failure: dict[str, Any],
    ) -> None:
        reason = "P1_LAUNCHER_ACTIVATION_RECEIPT_INVALID"
        _reject_permissions(receipt)
        _validate_predecessor_activation_success_evidence(
            predecessor_activation_success, reason)
        _validate_predecessor_activation_failure_evidence(
            predecessor_activation_failure, reason)
        _require(
            receipt.get("predecessor_activation_success") ==
                predecessor_activation_success and
            receipt.get("predecessor_activation_failure") ==
                predecessor_activation_failure,
            reason)
        activation_shadow_install_evidence = receipt.get(
            "shadow_install_evidence")
        profile_shadow_install_evidence = profile_receipt.get(
            "shadow_install_evidence")
        _validate_shadow_install_evidence(
            activation_shadow_install_evidence, reason)
        _validate_shadow_install_evidence(
            profile_shadow_install_evidence, reason)
        broker = receipt.get("broker_after")
        gateway = receipt.get("gateway_after")
        reconcile_timer = receipt.get("reconcile_timer")
        started_at_ms = receipt.get("started_at_ms")
        completed_at_ms = receipt.get("completed_at_ms")
        profile_started_at_ms = profile_receipt.get("started_at_ms")
        profile_finished_at_ms = profile_receipt.get("finished_at_ms")
        target_before = _validate_profile_file_evidence(
            profile_receipt.get("target_before"), path=GATEWAY_PROFILE,
            sha256=EXPECTED_GATEWAY_PROFILE_SHA256, size=736, mode=0o644)
        target_after = _validate_profile_file_evidence(
            profile_receipt.get("target_after"), path=GATEWAY_PROFILE,
            sha256=EXPECTED_GATEWAY_PROFILE_SHA256, size=736, mode=0o644)
        target_final = _validate_profile_file_evidence(
            profile_receipt.get("target_final"), path=GATEWAY_PROFILE,
            sha256=EXPECTED_GATEWAY_PROFILE_SHA256, size=736, mode=0o644)
        _require(target_before == target_after == target_final, reason)
        _validate_profile_file_evidence(
            profile_receipt.get("legacy_receipt"),
            path=LEGACY_PROFILE_DEPLOYMENT_RECEIPT,
            sha256=LEGACY_PROFILE_RECEIPT_FILE_SHA256,
            size=LEGACY_PROFILE_RECEIPT_BYTES, mode=0o600,
            legacy_receipt=True)
        _validate_profile_file_evidence(
            profile_receipt.get("legacy_backup"), path=LEGACY_PROFILE_BACKUP,
            sha256=LEGACY_PROFILE_SHA256, size=LEGACY_PROFILE_BYTES,
            mode=0o600)
        _validate_profile_file_evidence(
            profile_receipt.get("legacy_retained_target"),
            path=LEGACY_PROFILE_RETAINED_TARGET,
            sha256=LEGACY_PROFILE_SHA256, size=LEGACY_PROFILE_BYTES,
            mode=0o644)
        _validate_predecessor_profile_receipt_evidence(
            profile_receipt.get("predecessor_profile_receipt"))
        _validate_profile_transition_receipt_evidence(
            profile_receipt.get(
                "dormant_paper_to_watch_transition_receipt"))
        _require(
            isinstance(profile_receipt.get("preflight_before"), dict) and
            profile_receipt.get("preflight_before") ==
                profile_receipt.get("preflight_after") ==
                profile_receipt.get("preflight_final"),
            reason)
        expected_paper_units = {
            unit: {
                "ActiveState": "inactive", "SubState": "dead", "Job": ""}
            for unit in PAPER_UNITS
        }
        expected_mutations = [
            [SYSTEMCTL, "enable", "--now", ACTIVATION_RECONCILE_TIMER],
            [SYSTEMCTL, "daemon-reload"],
            [SYSTEMCTL, "start", BROKER_EGRESS_UNIT],
            [SYSTEMCTL, "unmask", GATEWAY_UNIT,
             "hepta-tool-gateway@alpha.socket",
             "hepta-tool-session-supervisor@alpha.socket"],
            [SYSTEMCTL, "unmask", "--runtime", GATEWAY_UNIT,
             "hepta-tool-gateway@alpha.socket",
             "hepta-tool-session-supervisor@alpha.socket"],
            [SYSTEMCTL, "daemon-reload"],
            [SYSTEMCTL, "start", GATEWAY_UNIT],
        ]
        expected_mutations_with_stop = [
            expected_mutations[0],
            expected_mutations[1],
            [SYSTEMCTL, "stop", BROKER_EGRESS_UNIT],
            *expected_mutations[2:],
        ]
        digest_fields = (
            receipt.get("profile_deployment_receipt_file_sha256"),
            receipt.get("profile_deployment_receipt_body_sha256"),
            receipt.get("profile_sha256"), receipt.get("journal_sha256"),
            *(broker.get(field) if isinstance(broker, dict) else None
              for field in (
                  "interpreter_sha256", "credential_source_sha256",
                  "installed_source_sha256", "cmdline_sha256",
                  "deny_all_policy_sha256", "unit_contract_sha256",
              )),
            *(gateway.get(field) if isinstance(gateway, dict) else None
              for field in (
                  "gateway_executable_sha256", "domain_config_sha256",
                  "gateway_profile_sha256",
                  "gateway_process_profile_sha256",
                  "unit_contract_sha256",
              )),
            reconcile_timer.get("unit_contract_sha256")
            if isinstance(reconcile_timer, dict) else None,
        )
        _require(
            set(receipt) == ACTIVATION_RECEIPT_FIELDS and
            receipt.get("schema") ==
            "hepta.p1-watch-activation-receipt.v4" and
            receipt.get("version") == 4 and
            receipt.get("status") == "WATCH_GATEWAY_ACTIVATED" and
            receipt.get("round") == 114 and
            receipt.get("domain") == DOMAIN_ID and
            set(profile_receipt) == PROFILE_DEPLOYMENT_RECEIPT_FIELDS and
            profile_receipt.get("schema") ==
            "hepta.p1-watch-profile-deployment-receipt.v8" and
            profile_receipt.get("version") == 8 and
            profile_receipt.get("status") ==
            "OFFLINE_PASSIVE_WATCH_PROFILE_REATTESTED" and
            profile_receipt.get("round") == 114 and
            profile_receipt.get("domain") == DOMAIN_ID and
            type(profile_started_at_ms) is int and
            profile_started_at_ms > 0 and
            type(profile_finished_at_ms) is int and
            profile_finished_at_ms >= profile_started_at_ms and
            profile_receipt.get("target_path") == str(GATEWAY_PROFILE) and
            profile_receipt.get("receipt_staging_path") ==
                str(PROFILE_DEPLOYMENT_RECEIPT_STAGING) and
            profile_receipt.get("profile_content_changed") is False and
            profile_receipt.get("target_written") is False and
            profile_receipt.get("target_replaced") is False and
            profile_receipt.get("services_started") is False and
            profile_receipt.get("services_stopped") is False and
            profile_receipt.get("services_restarted") is False and
            profile_receipt.get("campaign_launched") is False and
            profile_receipt.get("paper_authorized") is False and
            profile_receipt.get("live_authorized") is False and
            profile_receipt.get("mutation_attempted") is False and
            profile_receipt.get("direct_broker_access") is False and
            profile_receipt.get("activation_receipt_eligible") is False and
            profile_receipt.get("preflight_reusable_for_activation") is
            False and
            profile_receipt.get("broker_loaded_source_attested") is False and
            profile_receipt.get("broker_deny_all_continuity_attested") is
            False and
            profile_receipt.get("fresh_activation_transaction_required") is
            True and
            activation_shadow_install_evidence ==
            profile_shadow_install_evidence and
            type(started_at_ms) is int and started_at_ms > 0 and
            started_at_ms >= profile_finished_at_ms and
            type(completed_at_ms) is int and
            completed_at_ms >= started_at_ms and
            receipt.get("boot_id") == boot_id and
            receipt.get("profile_deployment_receipt_path") ==
            str(PROFILE_DEPLOYMENT_RECEIPT) and
            receipt.get("profile_deployment_receipt_file_sha256") ==
            digest_bytes(profile_receipt_contents) and
            receipt.get("profile_deployment_receipt_body_sha256") ==
            profile_receipt.get("body_sha256") and
            receipt.get("profile_sha256") ==
            EXPECTED_GATEWAY_PROFILE_SHA256 and
            receipt.get("profile_bytes") == 736 and
            all(
                isinstance(value, str) and DIGEST.fullmatch(value) is not None
                for value in digest_fields
            ) and
            isinstance(receipt.get("broker_before"), dict) and
            set(receipt["broker_before"]) == {
                "policy_sha256", "authorized_connectors",
                "authorized_uids", "protected_ports"} and
            receipt["broker_before"].get("authorized_connectors") == 0 and
            receipt["broker_before"].get("authorized_uids") == [] and
            receipt["broker_before"].get("protected_ports") == 4 and
            isinstance(broker, dict) and
            set(broker) == ACTIVATION_BROKER_FIELDS and
            broker.get("unit") == BROKER_EGRESS_UNIT and
            broker.get("active_state") == "active" and
            broker.get("sub_state") == "running" and
            broker.get("tasks_current") == 1 and
            broker.get("interpreter_path") == str(BROKER_INTERPRETER) and
            broker.get("credential_source_path") ==
            str(BROKER_CREDENTIAL_SOURCE) and
            broker.get("installed_source_path") ==
            str(BROKER_EGRESS_POLICY) and
            broker.get("credential_source_sha256") ==
            broker.get("installed_source_sha256") and
            broker.get("authorized_connectors") == 0 and
            broker.get("authorized_uids") == [] and
            broker.get("protected_ports") == 4 and
            isinstance(gateway, dict) and
            set(gateway) == ACTIVATION_GATEWAY_FIELDS and
            gateway.get("unit") == GATEWAY_UNIT and
            gateway.get("active_state") == "active" and
            gateway.get("sub_state") == "running" and
            gateway.get("gateway_executable_path") == GATEWAY and
            gateway.get("gateway_profile_path") == str(GATEWAY_PROFILE) and
            gateway.get("gateway_profile_sha256") ==
            EXPECTED_GATEWAY_PROFILE_SHA256 and
            gateway.get("execution_remote_mode") == "SIMULATOR" and
            gateway.get("tool_account") == "SIM" and
            gateway.get("execution_domain_id") == "SIM:alpha" and
            gateway.get("tool_allow_trade") == "0" and
            gateway.get("session_templates") == "watch" and
            gateway.get("contract_bindings") ==
            "EUR.USD|EUR|CASH|IDEALPRO|USD" and
            gateway.get("gateway_socket_path") == str(GATEWAY_SOCKET) and
            gateway.get("supervisor_socket_path") ==
            str(SUPERVISOR_SOCKET) and
            isinstance(reconcile_timer, dict) and
            set(reconcile_timer) == ACTIVATION_RECONCILE_TIMER_FIELDS and
            reconcile_timer.get("unit") == ACTIVATION_RECONCILE_TIMER and
            reconcile_timer.get("load_state") == "loaded" and
            reconcile_timer.get("active_state") == "active" and
            reconcile_timer.get("sub_state") == "waiting" and
            reconcile_timer.get("job") == "" and
            reconcile_timer.get("unit_file_state") == "enabled" and
            receipt.get("kill_switch_engaged") is True and
            receipt.get("paper_units") == expected_paper_units and
            receipt.get("watch_boundary") == {
                "export_absent": True, "sessions_authority_count": 0,
                "private_authority_count": 0,
                "custodian_transaction_absent": True,
                "session_bootstrap_idle_lock_observed": True} and
            set(receipt["watch_boundary"]) ==
                ACTIVATION_WATCH_BOUNDARY_FIELDS and
            isinstance(receipt.get("stale_bundles"), list) and
            [item.get("round") for item in receipt["stale_bundles"]
             if isinstance(item, dict)] == [110, 112] and
            all(
                isinstance(item, dict) and
                set(item) == {
                    "round", "status", "bundle_sha256",
                    "terminal_receipt_sha256", "quarantine_root"} and
                item.get("status") == "QUARANTINED" and
                isinstance(item.get("bundle_sha256"), str) and
                DIGEST.fullmatch(item["bundle_sha256"]) is not None and
                isinstance(item.get("terminal_receipt_sha256"), str) and
                DIGEST.fullmatch(item["terminal_receipt_sha256"])
                is not None and
                item.get("terminal_receipt_sha256") ==
                STALE_TERMINAL_RECEIPT_SHA256.get(item.get("round")) and
                item.get("quarantine_root") ==
                ("/var/lib/hepta/p1-admission/quarantine/"
                 f"activation-round114/round{item.get('round')}")
                for item in receipt["stale_bundles"]
            ) and
            canonical_bytes(receipt.get("systemctl_mutations")) in {
                # Convert only for exact comparison; no other optional
                # mutation is accepted.
                canonical_bytes(expected_mutations),
                canonical_bytes(expected_mutations_with_stop),
            } and
            receipt.get("fresh_activation_transaction") is True and
            receipt.get("gateway_activated") is True and
            receipt.get("gateway_profile_loaded") is True and
            receipt.get("gateway_contract_binding_loaded") is True and
            receipt.get("broker_loaded_source_attested") is True and
            receipt.get("broker_deny_all_continuity_attested") is True and
            receipt.get("watch_authority_provisioned") is False and
            receipt.get("campaign_launched") is False and
            receipt.get("paper_authorized") is False and
            receipt.get("live_authorized") is False and
            receipt.get("mutation_attempted") is False and
            receipt.get("direct_broker_access") is False and
            receipt.get("admission_prerequisite_satisfied") is True and
            receipt.get("paper_prerequisite_satisfied") is False and
            canonical_bytes(receipt) == receipt_contents,
            reason,
        )

    def _reconcile_timer_evidence(self) -> dict[str, Any]:
        reason = "P1_LAUNCHER_ACTIVATION_RECONCILE_TIMER_INVALID"

        def sample() -> dict[str, str]:
            result = self._run([
                SYSTEMCTL, "show", "--no-pager",
                *(f"--property={field}"
                  for field in ACTIVATION_RECONCILE_TIMER_MANAGER_FIELDS),
                ACTIVATION_RECONCILE_TIMER,
            ], 5)
            parsed: dict[str, str] = {}
            for line in result.stdout.splitlines():
                key, separator, value = line.partition("=")
                _require(
                    separator == "=" and
                    key in ACTIVATION_RECONCILE_TIMER_MANAGER_FIELDS and
                    key not in parsed,
                    reason,
                )
                parsed[key] = value
            _require(
                set(parsed) ==
                set(ACTIVATION_RECONCILE_TIMER_MANAGER_FIELDS) and
                parsed == {
                    "LoadState": "loaded",
                    "ActiveState": "active",
                    "SubState": "waiting",
                    "Job": "",
                    "UnitFileState": "enabled",
                },
                reason,
            )
            return parsed

        before = sample()
        after = sample()
        _require(before == after, reason)
        return {
            "unit": ACTIVATION_RECONCILE_TIMER,
            "load_state": before["LoadState"],
            "active_state": before["ActiveState"],
            "sub_state": before["SubState"],
            "job": before["Job"],
            "unit_file_state": before["UnitFileState"],
            "unit_contract_sha256": digest_bytes(canonical_bytes(before)),
        }

    def _acquire_shadow_install_binding(
        self,
        expected_evidence: dict[str, Any],
    ) -> dict[str, Any]:
        reason = "P1_LAUNCHER_SHADOW_INSTALL_INVALID"
        _validate_shadow_install_evidence(expected_evidence, reason)
        installer_payload, _ = self._read_anchored_root_file(
            SHADOW_INSTALLER, "P1_LAUNCHER_SHADOW_INSTALLER",
            mode=0o755, maximum_bytes=16 * 1024 * 1024)
        manifest_payload, _ = self._read_anchored_root_file(
            SHADOW_INSTALL_MANIFEST_PATH,
            "P1_LAUNCHER_SHADOW_INSTALL_MANIFEST",
            mode=0o600, maximum_bytes=2 * 1024 * 1024)
        _bootstrap_validate_shadow_installer(
            installer_payload, manifest_payload,
            expected_evidence["manifest_file_sha256"])
        launcher_payload, _ = self._read_anchored_root_file(
            Path(LAUNCHER_EXECUTABLE), "P1_LAUNCHER_INSTALLED_SOURCE",
            mode=0o755, maximum_bytes=16 * 1024 * 1024)
        profile_deployer_payload, _ = self._read_anchored_root_file(
            PROFILE_DEPLOYER, "P1_LAUNCHER_PROFILE_DEPLOYER_SOURCE",
            mode=0o755, maximum_bytes=16 * 1024 * 1024)
        name = "_hepta_shadow_install_consumer_for_admission"
        consumer = importlib.util.module_from_spec(
            importlib.util.spec_from_loader(name, loader=None))
        consumer.__file__ = str(SHADOW_INSTALLER)
        sys.modules[name] = consumer
        verified = None
        try:
            exec(compile(
                installer_payload, str(SHADOW_INSTALLER), "exec"),
                consumer.__dict__)
            _require(
                consumer.RECEIPT_SCHEMA ==
                    "hepta.shadow-runtime-install-receipt.v4" and
                consumer.MANIFEST_SCHEMA ==
                    "hepta.shadow-runtime-install-manifest.v2" and
                consumer.CURRENT_INSTALL_POINTER_SCHEMA ==
                    "hepta.shadow-runtime-current-install.v1" and
                consumer.EXPECTED_SHADOW_FILE_COUNT ==
                    SHADOW_INSTALL_FILE_COUNT,
                reason)
            verified = consumer.acquire_verified_installation(
                receipt_path=SHADOW_INSTALL_RECEIPT_PATH,
                manifest_path=SHADOW_INSTALL_MANIFEST_PATH,
                expected_domain=DOMAIN_ID,
                expected_backup_root=SHADOW_INSTALL_BACKUP_ROOT,
                expected_manifest_sha256=
                    expected_evidence["manifest_file_sha256"],
                expected_receipt_sha256=
                    expected_evidence["receipt_file_sha256"],
                lock_path=SHADOW_INSTALL_LOCK_PATH,
                expected_file_count=SHADOW_INSTALL_FILE_COUNT)
            consumer.require_verified_runtime_member(
                verified, SHADOW_INSTALLER_MEMBER, installer_payload)
            consumer.require_verified_runtime_member(
                verified, ADMISSION_LAUNCHER_MEMBER, launcher_payload)
            consumer.require_verified_runtime_member(
                verified, PROFILE_DEPLOYER_MEMBER,
                profile_deployer_payload)
            current = consumer.validate_verified_installation(verified)
            _require(current == expected_evidence, reason)
            return {
                "consumer": consumer,
                "verified": verified,
                "installer_payload": installer_payload,
                "launcher_payload": launcher_payload,
                "profile_deployer_payload": profile_deployer_payload,
                "expected_evidence": dict(expected_evidence),
            }
        except LauncherError:
            if verified is not None:
                try:
                    consumer.release_verified_installation(verified)
                except Exception:
                    pass
            raise
        except Exception as error:
            if verified is not None:
                try:
                    consumer.release_verified_installation(verified)
                except Exception:
                    pass
            raise LauncherError(reason) from error
        finally:
            sys.modules.pop(name, None)

    @staticmethod
    def _validate_shadow_install_binding(
        binding: dict[str, Any],
    ) -> dict[str, Any]:
        reason = "P1_LAUNCHER_SHADOW_INSTALL_REBOUND"
        try:
            consumer = binding["consumer"]
            verified = binding["verified"]
            current = consumer.validate_verified_installation(verified)
            consumer.require_verified_runtime_member(
                verified, SHADOW_INSTALLER_MEMBER,
                binding["installer_payload"])
            consumer.require_verified_runtime_member(
                verified, ADMISSION_LAUNCHER_MEMBER,
                binding["launcher_payload"])
            consumer.require_verified_runtime_member(
                verified, PROFILE_DEPLOYER_MEMBER,
                binding["profile_deployer_payload"])
            _require(current == binding["expected_evidence"], reason)
            return current
        except LauncherError:
            raise
        except Exception as error:
            raise LauncherError(reason) from error

    @staticmethod
    def _release_shadow_install_binding(binding: dict[str, Any]) -> None:
        try:
            binding["consumer"].release_verified_installation(
                binding["verified"])
        except Exception as error:
            raise LauncherError(
                "P1_LAUNCHER_SHADOW_INSTALL_RELEASE_FAILED") from error

    def _load_verified_profile_deployer(
        self,
        shadow_install_binding: dict[str, Any],
    ) -> Any:
        reason = "P1_LAUNCHER_PROFILE_DEPLOYER_SOURCE_INVALID"
        self._validate_shadow_install_binding(shadow_install_binding)
        payload = shadow_install_binding["profile_deployer_payload"]
        name = "_hepta_verified_profile_deployer_for_admission"
        module = importlib.util.module_from_spec(
            importlib.util.spec_from_loader(name, loader=None))
        module.__file__ = str(PROFILE_DEPLOYER)
        sys.modules[name] = module
        try:
            exec(compile(payload, str(PROFILE_DEPLOYER), "exec"),
                 module.__dict__)
            _require(
                module.ROUND114_RECEIPT_SCHEMA ==
                    "hepta.p1-watch-profile-deployment-receipt.v8" and
                module.ROUND114_RECEIPT_VERSION == 8 and
                module.ROUND114_RECEIPT_PATH ==
                    PROFILE_DEPLOYMENT_RECEIPT and
                module.ROUND114_TRANSITION_RECEIPT_SCHEMA ==
                    PROFILE_TRANSITION_RECEIPT_SCHEMA and
                module.ROUND114_TRANSITION_RECEIPT_VERSION ==
                    PROFILE_TRANSITION_RECEIPT_VERSION and
                module.ROUND114_TRANSITION_RECEIPT_PATH ==
                    PROFILE_TRANSITION_RECEIPT and
                module.ROUND114_TRANSITION_RECEIPT_FIELDS ==
                    PROFILE_TRANSITION_RECEIPT_FIELDS and
                "preimage_evidence" in
                    module.ROUND114_TRANSITION_RECEIPT_FIELDS and
                module.ROUND114_TRANSITION_PREIMAGE_SCHEMA ==
                    PROFILE_TRANSITION_PREIMAGE_SCHEMA and
                module.ROUND114_TRANSITION_PREIMAGE_VERSION ==
                    PROFILE_TRANSITION_PREIMAGE_VERSION and
                module.ROUND114_TRANSITION_PREIMAGE_PATH ==
                    PROFILE_TRANSITION_PREIMAGE and
                module.ROUND114_TRANSITION_PREIMAGE_FIELDS ==
                    PROFILE_TRANSITION_PREIMAGE_FIELDS and
                module.ROUND114_RECEIPT_FIELDS ==
                    PROFILE_DEPLOYMENT_RECEIPT_FIELDS and
                "dormant_paper_to_watch_transition_receipt" in
                    module.ROUND114_RECEIPT_FIELDS and
                module.ROUND95_RECEIPT_FILE_SHA256 ==
                    PREDECESSOR_PROFILE_RECEIPT_FILE_SHA256 and
                module.LEGACY_RECEIPT_FILE_SHA256 ==
                    LEGACY_PROFILE_RECEIPT_FILE_SHA256 and
                callable(getattr(
                    module, "validate_round114_receipt", None)) and
                callable(getattr(
                    module, "validate_round114_receipt_state_binding", None)),
                reason)
            self._validate_shadow_install_binding(shadow_install_binding)
            return module
        except LauncherError:
            raise
        except Exception as error:
            raise LauncherError(reason) from error
        finally:
            sys.modules.pop(name, None)

    def _acquire_profile_artifact_binding(
        self,
        shadow_install_binding: dict[str, Any],
        expected_profile_receipt: dict[str, Any],
        expected_profile_receipt_contents: bytes,
    ) -> dict[str, Any]:
        reason = "P1_LAUNCHER_PROFILE_ARTIFACT_INVALID"
        try:
            evidence = self._validate_shadow_install_binding(
                shadow_install_binding)
            module = self._load_verified_profile_deployer(
                shadow_install_binding)
            snapshot = module.read_anchored_file(
                PROFILE_DEPLOYMENT_RECEIPT, reason)
            _require(
                snapshot.payload == expected_profile_receipt_contents,
                reason)
            document, _receipt_sha256 = module.validate_round114_receipt(
                snapshot, evidence)
            _require(document == expected_profile_receipt, reason)
            artifacts = module.read_rebind_artifacts(
                PREDECESSOR_PROFILE_RECEIPT_FILE_SHA256)
            module.validate_round114_receipt_state_binding(
                document, artifacts)
            artifacts = module.require_rebind_artifacts_unchanged(
                artifacts, PREDECESSOR_PROFILE_RECEIPT_FILE_SHA256)
            module.validate_round114_receipt_state_binding(
                document, artifacts)
            binding = {
                "module": module,
                "document": document,
                "receipt_snapshot": snapshot,
                "artifacts": artifacts,
                "expected_evidence": dict(evidence),
            }
            self._validate_shadow_install_binding(shadow_install_binding)
            self._validate_profile_artifact_binding(
                shadow_install_binding, binding)
            return binding
        except LauncherError:
            raise
        except Exception as error:
            raise LauncherError(reason) from error

    def _validate_profile_artifact_binding(
        self,
        shadow_install_binding: dict[str, Any],
        binding: dict[str, Any],
    ) -> None:
        reason = "P1_LAUNCHER_PROFILE_ARTIFACT_REBOUND"
        try:
            evidence = self._validate_shadow_install_binding(
                shadow_install_binding)
            _require(evidence == binding["expected_evidence"], reason)
            module = binding["module"]
            snapshot = module.read_anchored_file(
                PROFILE_DEPLOYMENT_RECEIPT, reason)
            expected_snapshot = binding["receipt_snapshot"]
            _require(
                snapshot.payload == expected_snapshot.payload and
                module.stable_identity(snapshot.metadata) ==
                    module.stable_identity(expected_snapshot.metadata),
                reason)
            document, _receipt_sha256 = module.validate_round114_receipt(
                snapshot, evidence)
            _require(document == binding["document"], reason)
            artifacts = module.require_rebind_artifacts_unchanged(
                binding["artifacts"], PREDECESSOR_PROFILE_RECEIPT_FILE_SHA256)
            module.validate_round114_receipt_state_binding(
                document, artifacts)
            self._validate_shadow_install_binding(shadow_install_binding)
        except LauncherError:
            raise
        except Exception as error:
            raise LauncherError(reason) from error

    def _activation_binding_under_install_lock(
        self,
        shadow_install_binding: dict[str, Any],
        initial_receipt: dict[str, Any],
        initial_receipt_contents: bytes,
        initial_profile_receipt: dict[str, Any],
        initial_profile_receipt_contents: bytes,
        predecessor_activation_success: dict[str, Any],
        predecessor_activation_failure: dict[str, Any],
    ) -> dict[str, Any]:
        self._predecessor_activation_success_binding(
            predecessor_activation_success)
        self._predecessor_activation_failure_binding(
            predecessor_activation_failure)
        self._assert_activation_failure_artifacts_absent()
        receipt, receipt_contents = self._read_anchored_root_document(
            ACTIVATION_RECEIPT,
            "P1_LAUNCHER_ACTIVATION_RECEIPT",
            mode=0o600,
        )
        profile_receipt, profile_receipt_contents = (
            self._read_anchored_root_document(
                PROFILE_DEPLOYMENT_RECEIPT,
                "P1_LAUNCHER_ACTIVATION_PROFILE_RECEIPT",
                mode=0o600,
            ))
        _require(
            receipt == initial_receipt and
            receipt_contents == initial_receipt_contents and
            profile_receipt == initial_profile_receipt and
            profile_receipt_contents == initial_profile_receipt_contents,
            "P1_LAUNCHER_ACTIVATION_LIVE_BINDING_INVALID")
        boot_id = self._current_boot_id()
        self._validate_activation_receipt(
            receipt,
            receipt_contents=receipt_contents,
            profile_receipt=profile_receipt,
            profile_receipt_contents=profile_receipt_contents,
            boot_id=boot_id,
            predecessor_activation_success=predecessor_activation_success,
            predecessor_activation_failure=predecessor_activation_failure,
        )
        profile_artifact_binding = self._acquire_profile_artifact_binding(
            shadow_install_binding, profile_receipt,
            profile_receipt_contents)
        self._validate_profile_artifact_binding(
            shadow_install_binding, profile_artifact_binding)
        self._validate_shadow_install_binding(shadow_install_binding)
        reconcile_timer = self._reconcile_timer_evidence()
        broker = self._broker_activation_evidence()
        broker["unit_contract_sha256"] = (
            self._activation_unit_contract_sha256(BROKER_EGRESS_UNIT))
        gateway = self._gateway_activation_evidence()
        gateway["unit_contract_sha256"] = (
            self._activation_unit_contract_sha256(GATEWAY_UNIT))
        final_receipt, final_receipt_contents = (
            self._read_anchored_root_document(
                ACTIVATION_RECEIPT,
                "P1_LAUNCHER_ACTIVATION_RECEIPT",
                mode=0o600,
            ))
        final_profile_receipt, final_profile_receipt_contents = (
            self._read_anchored_root_document(
                PROFILE_DEPLOYMENT_RECEIPT,
                "P1_LAUNCHER_ACTIVATION_PROFILE_RECEIPT",
                mode=0o600,
            ))
        final_reconcile_timer = self._reconcile_timer_evidence()
        self._validate_shadow_install_binding(shadow_install_binding)
        self._predecessor_activation_success_binding(
            predecessor_activation_success)
        self._predecessor_activation_failure_binding(
            predecessor_activation_failure)
        self._assert_activation_failure_artifacts_absent()
        self._validate_profile_artifact_binding(
            shadow_install_binding, profile_artifact_binding)
        _require(
            receipt == final_receipt and
            receipt_contents == final_receipt_contents and
            profile_receipt == final_profile_receipt and
            profile_receipt_contents == final_profile_receipt_contents and
            boot_id == self._current_boot_id() and
            reconcile_timer == final_reconcile_timer ==
                receipt["reconcile_timer"] and
            broker == receipt["broker_after"] and
            gateway == receipt["gateway_after"],
            "P1_LAUNCHER_ACTIVATION_LIVE_BINDING_INVALID",
        )
        self._validate_profile_artifact_binding(
            shadow_install_binding, profile_artifact_binding)
        return {
            "activation_receipt_file_sha256":
                digest_bytes(receipt_contents),
            "activation_receipt_body_sha256": receipt["body_sha256"],
            "profile_receipt_path": str(PROFILE_DEPLOYMENT_RECEIPT),
            "profile_receipt_file_sha256":
                digest_bytes(profile_receipt_contents),
            "profile_receipt_body_sha256":
                profile_receipt["body_sha256"],
            "boot_id": boot_id,
            "started_at_ms": receipt["started_at_ms"],
            "completed_at_ms": receipt["completed_at_ms"],
            "reconcile_timer": reconcile_timer,
            "broker": broker,
            "gateway": gateway,
            "predecessor_activation_success":
                predecessor_activation_success,
            "predecessor_activation_failure":
                predecessor_activation_failure,
        }

    def activation_binding(self) -> dict[str, Any]:
        self._assert_activation_failure_artifacts_absent()
        predecessor_activation_success = (
            self._predecessor_activation_success_binding())
        predecessor_activation_failure = (
            self._predecessor_activation_failure_binding())
        receipt, receipt_contents = self._read_anchored_root_document(
            ACTIVATION_RECEIPT,
            "P1_LAUNCHER_ACTIVATION_RECEIPT",
            mode=0o600)
        profile_receipt, profile_receipt_contents = (
            self._read_anchored_root_document(
                PROFILE_DEPLOYMENT_RECEIPT,
                "P1_LAUNCHER_ACTIVATION_PROFILE_RECEIPT",
                mode=0o600))
        self._validate_activation_receipt(
            receipt, receipt_contents=receipt_contents,
            profile_receipt=profile_receipt,
            profile_receipt_contents=profile_receipt_contents,
            boot_id=self._current_boot_id(),
            predecessor_activation_success=predecessor_activation_success,
            predecessor_activation_failure=predecessor_activation_failure)
        binding = self._acquire_shadow_install_binding(
            profile_receipt["shadow_install_evidence"])
        try:
            self._validate_shadow_install_binding(binding)
            return self._activation_binding_under_install_lock(
                binding, receipt, receipt_contents,
                profile_receipt, profile_receipt_contents,
                predecessor_activation_success,
                predecessor_activation_failure)
        finally:
            try:
                self._predecessor_activation_success_binding(
                    predecessor_activation_success)
                self._predecessor_activation_failure_binding(
                    predecessor_activation_failure)
                self._validate_shadow_install_binding(binding)
            finally:
                self._release_shadow_install_binding(binding)

    def launcher_identity(
        self,
        unit: str,
        pid: int,
        configuration: LaunchConfiguration,
    ) -> dict[str, Any]:
        _require(
            re.fullmatch(
                r"hepta-p1-shadow-admission-round[1-9][0-9]*\.service",
                unit) is not None and type(pid) is int and pid > 1 and
            os.geteuid() == ROOT_UID and os.getegid() == ROOT_GID,
            "P1_LAUNCHER_UNIT_IDENTITY_INVALID",
        )
        result = self._run([
            SYSTEMCTL, "show", "--no-pager",
            "--property=ActiveState", "--property=SubState",
            "--property=InvocationID", "--property=MainPID",
            "--property=Type", "--property=Restart",
            "--property=RemainAfterExit", "--property=User",
            "--property=Group", "--property=ExecStart",
            "--property=Environment", "--property=Conflicts", unit,
        ], 5)
        parsed: dict[str, str] = {}
        for line in result.stdout.splitlines():
            key, separator, value = line.partition("=")
            _require(separator == "=" and key not in parsed,
                     "P1_LAUNCHER_UNIT_IDENTITY_INVALID")
            parsed[key] = value
        command = launcher_command(configuration)
        serialized_command = " ".join(command)
        exec_start_prefix = (
            f"{{ path={LAUNCHER_EXECUTABLE} ; "
            f"argv[]={serialized_command} ; ignore_errors=no ; ")
        expected_environment = " ".join(
            f"{field}={value}"
            for field, value in SANITIZED_ENVIRONMENT.items())
        exec_start = parsed.get("ExecStart", "")
        _require(
            set(parsed) == {
                "ActiveState", "SubState", "InvocationID", "MainPID",
                "Type", "Restart", "RemainAfterExit", "User", "Group",
                "ExecStart", "Environment", "Conflicts",
            } and
            parsed.get("ActiveState") == "active" and
            parsed.get("SubState") == "running" and
            re.fullmatch(r"[0-9a-f]{32}", parsed.get("InvocationID", "")) and
            parsed.get("MainPID", "").isdigit() and
            int(parsed["MainPID"]) == pid,
            "P1_LAUNCHER_UNIT_IDENTITY_INVALID",
        )
        _require(
            parsed.get("Type") == "exec" and
            parsed.get("Restart") == "no" and
            parsed.get("RemainAfterExit") == "no" and
            parsed.get("User") == "root" and
            parsed.get("Group") == "root" and
            exec_start.startswith(exec_start_prefix) and
            exec_start.endswith(" }") and
            exec_start.count("{ path=") == 1 and
            exec_start.count("argv[]=") == 1 and
            parsed.get("Environment") == expected_environment and
            len(parsed.get("Conflicts", "").split()) == len(PAPER_UNITS) and
            set(parsed.get("Conflicts", "").split()) == set(PAPER_UNITS),
            "P1_LAUNCHER_UNIT_IDENTITY_INVALID",
        )
        launcher_sha256 = digest_bytes(_secure_read(
            Path(LAUNCHER_EXECUTABLE),
            "P1_LAUNCHER_SELF_INVALID",
            64 * 1024 * 1024,
            expected_uid=ROOT_UID,
            expected_gid=ROOT_GID,
            modes=HELPER_MODES["launcher_sha256"],
        ))
        return {
            "unit": unit,
            "invocation_id": parsed["InvocationID"],
            "main_pid": pid,
            "type": parsed["Type"],
            "restart": parsed["Restart"],
            "remain_after_exit": parsed["RemainAfterExit"],
            "user": parsed["User"],
            "group": parsed["Group"],
            "exec_start": command,
            "environment": dict(SANITIZED_ENVIRONMENT),
            "launcher_sha256": launcher_sha256,
            "conflicts": list(PAPER_UNITS),
        }

    def _json_stdout(self, result: subprocess.CompletedProcess[str]) -> dict[str, Any]:
        try:
            document = json.loads(result.stdout, object_pairs_hook=_pairs)
        except (UnicodeError, json.JSONDecodeError, ValueError) as error:
            raise LauncherError("P1_LAUNCHER_RESULT_INVALID") from error
        _require(isinstance(document, dict), "P1_LAUNCHER_RESULT_INVALID")
        _reject_permissions(document)
        return document

    def assert_clean(self) -> dict[str, Any]:
        result = self._run([
            CUSTODIAN, "--domain-config", str(DOMAIN_CONFIG), "reconcile",
        ], 30)
        document = self._json_stdout(result)
        _require(
            document.get("status") == "NO_ACTIVE_TRANSACTION",
            "P1_LAUNCHER_ACTIVE_TRANSACTION_PRESENT",
        )
        self._assert_no_residue()
        return document

    def assert_paper_inactive(self) -> dict[str, Any]:
        states: dict[str, Any] = {}
        expected_fields = {"ActiveState", "SubState"}
        for unit in PAPER_UNITS:
            result = self._run([
                SYSTEMCTL, "show", "--no-pager",
                "--property=ActiveState", "--property=SubState", unit,
            ], 5)
            parsed: dict[str, str] = {}
            for line in result.stdout.splitlines():
                key, separator, value = line.partition("=")
                _require(
                    separator == "=" and key in expected_fields and
                    key not in parsed and value != "",
                    "P1_LAUNCHER_PAPER_UNIT_STATE_INVALID",
                )
                parsed[key] = value
            _require(
                set(parsed) == expected_fields,
                "P1_LAUNCHER_PAPER_UNIT_STATE_INVALID",
            )
            _require(
                parsed == {"ActiveState": "inactive", "SubState": "dead"},
                "P1_LAUNCHER_PAPER_ACTIVE",
            )
            states[unit] = parsed
        _require(
            not any(
                os.path.lexists(path)
                for path in PAPER_OPERATOR_SOCKET_PATHS
            ),
            "P1_LAUNCHER_PAPER_SOCKET_PRESENT",
        )
        return states

    @staticmethod
    def _assert_no_residue() -> None:
        ProductionExecutor._assert_anchored_path_absent(
            WATCH_EXPORT, "P1_LAUNCHER_EXPORT_RESIDUE")
        ProductionExecutor._assert_directory_without_authority(
            WATCH_SESSIONS,
            expected_uid=ROOT_UID,
            expected_gid=ROOT_GID,
            expected_mode=0o711,
            allow_bootstrap_lock=True,
        )
        ProductionExecutor.\
            _assert_service_owned_watch_private_without_authority()

    @staticmethod
    def _stable_metadata(metadata: os.stat_result) -> tuple[int, ...]:
        return (
            metadata.st_dev,
            metadata.st_ino,
            metadata.st_uid,
            metadata.st_gid,
            stat.S_IMODE(metadata.st_mode),
            metadata.st_nlink,
            metadata.st_size,
            metadata.st_mtime_ns,
            metadata.st_ctime_ns,
        )

    @staticmethod
    def _open_anchored_directory(
        directory: Path,
        reason: str,
    ) -> int | None:
        _require(directory.is_absolute() and directory != Path("/"), reason)
        flags = os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC
        if hasattr(os, "O_NOFOLLOW"):
            flags |= os.O_NOFOLLOW
        current_fd: int | None = None
        try:
            current_fd = os.open("/", flags)
            for component in directory.parts[1:]:
                next_fd = os.open(component, flags, dir_fd=current_fd)
                os.close(current_fd)
                current_fd = next_fd
            return current_fd
        except FileNotFoundError:
            if current_fd is not None:
                os.close(current_fd)
            return None
        except OSError as error:
            if current_fd is not None:
                os.close(current_fd)
            raise LauncherError(reason) from error

    @staticmethod
    def _read_anchored_root_file(
        path: Path,
        label: str,
        *,
        mode: int = 0o600,
        maximum_bytes: int = MAXIMUM_JSON_BYTES,
    ) -> tuple[bytes, tuple[int, ...]]:
        """Read one fixed root-owned file without trusting a pathname.

        Both the parent directory and final entry are rebound after the read.
        Every pathname component is opened with ``O_NOFOLLOW`` by
        ``_open_anchored_directory`` and the final regular file is tied across
        the pre-open, open, post-read, and canonical-reopen observations.
        """
        reason = f"{label}_FILE_INVALID"
        _require(
            path.is_absolute() and path.name not in {"", ".", ".."} and
            type(mode) is int and 0 <= mode <= 0o777 and
            type(maximum_bytes) is int and maximum_bytes >= 1,
            reason,
        )
        parent_fd = ProductionExecutor._open_anchored_directory(
            path.parent, reason)
        _require(parent_fd is not None, reason)
        file_fd: int | None = None
        rebound_fd: int | None = None
        try:
            parent_before = os.fstat(parent_fd)
            _require(
                stat.S_ISDIR(parent_before.st_mode) and
                parent_before.st_uid == ROOT_UID and
                stat.S_IMODE(parent_before.st_mode) & 0o022 == 0,
                reason,
            )
            before = os.stat(
                path.name, dir_fd=parent_fd, follow_symlinks=False)
            flags = os.O_RDONLY | os.O_CLOEXEC
            if hasattr(os, "O_NOFOLLOW"):
                flags |= os.O_NOFOLLOW
            file_fd = os.open(path.name, flags, dir_fd=parent_fd)
            opened = os.fstat(file_fd)
            _require(
                stat.S_ISREG(before.st_mode) and
                stat.S_ISREG(opened.st_mode) and
                ProductionExecutor._stable_metadata(before) ==
                ProductionExecutor._stable_metadata(opened) and
                opened.st_uid == ROOT_UID and opened.st_gid == ROOT_GID and
                stat.S_IMODE(opened.st_mode) == mode and
                opened.st_nlink == 1 and
                1 <= opened.st_size <= maximum_bytes,
                reason,
            )
            remaining = opened.st_size
            chunks: list[bytes] = []
            while remaining:
                chunk = os.read(file_fd, min(remaining, 1024 * 1024))
                _require(bool(chunk), reason)
                chunks.append(chunk)
                remaining -= len(chunk)
            _require(os.read(file_fd, 1) == b"", reason)
            after = os.fstat(file_fd)
            final_parent = os.fstat(parent_fd)
            rebound_fd = ProductionExecutor._open_anchored_directory(
                path.parent, reason)
            _require(rebound_fd is not None, reason)
            rebound_parent = os.fstat(rebound_fd)
            final = os.stat(
                path.name, dir_fd=rebound_fd, follow_symlinks=False)
            identity = ProductionExecutor._stable_metadata(opened)
            _require(
                identity == ProductionExecutor._stable_metadata(after) ==
                ProductionExecutor._stable_metadata(final) and
                ProductionExecutor._stable_metadata(parent_before) ==
                ProductionExecutor._stable_metadata(final_parent) ==
                ProductionExecutor._stable_metadata(rebound_parent),
                reason,
            )
            contents = b"".join(chunks)
            return contents, identity
        except LauncherError:
            raise
        except OSError as error:
            raise LauncherError(reason) from error
        finally:
            if rebound_fd is not None:
                os.close(rebound_fd)
            if file_fd is not None:
                os.close(file_fd)
            os.close(parent_fd)

    @staticmethod
    def _read_anchored_root_document(
        path: Path,
        label: str,
        *,
        mode: int = 0o600,
        maximum_bytes: int = MAXIMUM_JSON_BYTES,
    ) -> tuple[dict[str, Any], bytes]:
        contents, _identity = ProductionExecutor._read_anchored_root_file(
            path, label, mode=mode, maximum_bytes=maximum_bytes)
        return _decode_document(contents, label), contents

    @staticmethod
    def _predecessor_activation_success_binding(
        expected: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        reason = "P1_LAUNCHER_ACTIVATION_PREDECESSOR_SUCCESS_INVALID"
        receipt_contents, receipt_identity = (
            ProductionExecutor._read_anchored_root_file(
                PREDECESSOR_ACTIVATION_SUCCESS_RECEIPT,
                "P1_LAUNCHER_ACTIVATION_PREDECESSOR_SUCCESS_RECEIPT",
                mode=0o600))
        _require(
            digest_bytes(receipt_contents) ==
                PREDECESSOR_ACTIVATION_SUCCESS_RECEIPT_FILE_SHA256,
            reason)
        receipt = _decode_document(receipt_contents, reason)
        _validate_predecessor_activation_success_document(receipt, reason)
        evidence = {
            "receipt_path": str(PREDECESSOR_ACTIVATION_SUCCESS_RECEIPT),
            "receipt_file_sha256": digest_bytes(receipt_contents),
            "receipt_body_sha256": receipt["body_sha256"],
            "receipt_schema": receipt["schema"],
            "receipt_version": receipt["version"],
            "receipt_status": receipt["status"],
            "receipt_round": receipt["round"],
            "receipt_domain": receipt["domain"],
            "receipt_device": receipt_identity[0],
            "receipt_inode": receipt_identity[1],
            "receipt_mode": stat.S_IFREG | receipt_identity[4],
            "receipt_nlink": receipt_identity[5],
            "receipt_uid": receipt_identity[2],
            "receipt_gid": receipt_identity[3],
            "receipt_bytes": receipt_identity[6],
            "receipt_mtime_ns": receipt_identity[7],
            "receipt_ctime_ns": receipt_identity[8],
        }
        _validate_predecessor_activation_success_evidence(evidence, reason)
        if expected is not None:
            _validate_predecessor_activation_success_evidence(expected, reason)
            _require(evidence == expected, reason)
        return evidence

    @staticmethod
    def _predecessor_activation_failure_binding(
        expected: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        reason = "P1_LAUNCHER_ACTIVATION_PREDECESSOR_INVALID"
        receipt_contents, receipt_identity = (
            ProductionExecutor._read_anchored_root_file(
                PREDECESSOR_ACTIVATION_FAILED_RECEIPT,
                "P1_LAUNCHER_ACTIVATION_PREDECESSOR_RECEIPT",
                mode=0o600))
        _require(
            digest_bytes(receipt_contents) ==
                PREDECESSOR_ACTIVATION_FAILED_RECEIPT_FILE_SHA256,
            reason)
        receipt = _decode_document(receipt_contents, reason)
        _validate_predecessor_failed_receipt_document(receipt, reason)

        journal_fd = ProductionExecutor._open_anchored_directory(
            PREDECESSOR_ACTIVATION_JOURNAL, reason)
        _require(journal_fd is not None, reason)
        rebound_fd: int | None = None
        try:
            directory_before = os.fstat(journal_fd)
            _require(
                stat.S_ISDIR(directory_before.st_mode) and
                directory_before.st_uid == ROOT_UID and
                directory_before.st_gid == ROOT_GID and
                stat.S_IMODE(directory_before.st_mode) == 0o700,
                reason)
            names = sorted(os.listdir(journal_fd))
            _require(bool(names), reason)
            previous: str | None = None
            phases: list[str] = []
            records: list[dict[str, Any]] = []
            file_sha256s: list[str] = []
            for index, name in enumerate(names):
                match = re.fullmatch(r"([0-9]{4})-([A-Z_]+)\.json", name)
                _require(
                    match is not None and int(match.group(1)) == index,
                    reason)
                before = os.stat(
                    name, dir_fd=journal_fd, follow_symlinks=False)
                flags = os.O_RDONLY | os.O_CLOEXEC
                if hasattr(os, "O_NOFOLLOW"):
                    flags |= os.O_NOFOLLOW
                record_fd = os.open(name, flags, dir_fd=journal_fd)
                try:
                    opened = os.fstat(record_fd)
                    _require(
                        ProductionExecutor._stable_metadata(before) ==
                            ProductionExecutor._stable_metadata(opened) and
                        stat.S_ISREG(opened.st_mode) and
                        opened.st_uid == ROOT_UID and opened.st_gid == ROOT_GID and
                        stat.S_IMODE(opened.st_mode) == 0o600 and
                        opened.st_nlink == 1 and
                        1 <= opened.st_size <= MAXIMUM_JSON_BYTES,
                        reason)
                    remaining = opened.st_size
                    chunks: list[bytes] = []
                    while remaining:
                        chunk = os.read(record_fd, min(remaining, 1024 * 1024))
                        _require(bool(chunk), reason)
                        chunks.append(chunk)
                        remaining -= len(chunk)
                    _require(os.read(record_fd, 1) == b"", reason)
                    after = os.fstat(record_fd)
                    _require(
                        ProductionExecutor._stable_metadata(opened) ==
                            ProductionExecutor._stable_metadata(after),
                        reason)
                finally:
                    os.close(record_fd)
                record_contents = b"".join(chunks)
                record = _decode_document(record_contents, reason)
                phase = match.group(2)
                _require(
                    set(record) == {
                        "schema", "version", "sequence", "phase",
                        "recorded_at_ms", "previous_record_sha256",
                        "evidence", "body_sha256"} and
                    record.get("schema") ==
                        "hepta.p1-watch-activation-journal.v1" and
                    record.get("version") == 1 and
                    record.get("sequence") == index and
                    record.get("phase") == phase and
                    type(record.get("recorded_at_ms")) is int and
                    record["recorded_at_ms"] >= 0 and
                    record.get("previous_record_sha256") == previous and
                    isinstance(record.get("evidence"), dict),
                    reason)
                file_sha256 = digest_bytes(record_contents)
                previous = file_sha256
                file_sha256s.append(file_sha256)
                phases.append(phase)
                records.append(record)
            quarantine_indices = [
                index for index, phase in enumerate(phases)
                if phase in PREDECESSOR_QUARANTINE_PHASES]
            _require(bool(quarantine_indices), reason)
            first = quarantine_indices[0]
            _require(
                phases[:first] == list(PREDECESSOR_ACTIVATION_PHASES[:first]) and
                phases[first:] == list(PREDECESSOR_QUARANTINE_PHASES),
                reason)
            suffix = records[first:]
            _require(
                suffix[0]["evidence"] == {"reason": receipt["reason"]} and
                set(suffix[1]["evidence"]) == {"evidence"} and
                isinstance(suffix[1]["evidence"]["evidence"], dict) and
                set(suffix[2]["evidence"]) == {"evidence"} and
                _valid_predecessor_deny_all(
                    suffix[2]["evidence"]["evidence"]) and
                suffix[3]["evidence"] == {
                    "export_absent": True, "sessions_authority_count": 0,
                    "private_authority_count": 0,
                    "custodian_transaction_absent": True,
                    "session_bootstrap_idle_lock_observed": True} and
                suffix[4]["evidence"] == {"complete": True},
                reason)
            journal_sha256 = digest_bytes(canonical_bytes(file_sha256s))
            _require(
                journal_sha256 == PREDECESSOR_ACTIVATION_JOURNAL_SHA256 and
                names == sorted(os.listdir(journal_fd)) and
                ProductionExecutor._stable_metadata(directory_before) ==
                    ProductionExecutor._stable_metadata(os.fstat(journal_fd)),
                reason)
            rebound_fd = ProductionExecutor._open_anchored_directory(
                PREDECESSOR_ACTIVATION_JOURNAL, reason)
            _require(
                rebound_fd is not None and
                ProductionExecutor._stable_metadata(directory_before) ==
                    ProductionExecutor._stable_metadata(os.fstat(rebound_fd)),
                reason)
        except LauncherError:
            raise
        except OSError as error:
            raise LauncherError(reason) from error
        finally:
            if rebound_fd is not None:
                os.close(rebound_fd)
            os.close(journal_fd)

        evidence = {
            "receipt_path": str(PREDECESSOR_ACTIVATION_FAILED_RECEIPT),
            "receipt_file_sha256": digest_bytes(receipt_contents),
            "receipt_body_sha256": receipt["body_sha256"],
            "receipt_schema": receipt["schema"],
            "receipt_version": receipt["version"],
            "receipt_revision": receipt["revision"],
            "receipt_status": receipt["status"],
            "receipt_round": receipt["round"],
            "receipt_domain": receipt["domain"],
            "receipt_reason": receipt["reason"],
            "receipt_device": receipt_identity[0],
            "receipt_inode": receipt_identity[1],
            "receipt_mode": stat.S_IFREG | receipt_identity[4],
            "receipt_nlink": receipt_identity[5],
            "receipt_uid": receipt_identity[2],
            "receipt_gid": receipt_identity[3],
            "receipt_bytes": receipt_identity[6],
            "receipt_mtime_ns": receipt_identity[7],
            "receipt_ctime_ns": receipt_identity[8],
            "journal_path": str(PREDECESSOR_ACTIVATION_JOURNAL),
            "journal_sha256": journal_sha256,
            "journal_record_count": len(records),
            "journal_terminal_phase": phases[-1],
        }
        _validate_predecessor_activation_failure_evidence(evidence, reason)
        if expected is not None:
            _require(evidence == expected, reason)
        return evidence

    @staticmethod
    def _assert_anchored_path_absent(path: Path, reason: str) -> None:
        _require(path.is_absolute() and path.name not in {"", ".", ".."},
                 reason)
        parent_fd = ProductionExecutor._open_anchored_directory(
            path.parent, reason)
        if parent_fd is None:
            return
        try:
            try:
                os.stat(path.name, dir_fd=parent_fd, follow_symlinks=False)
            except FileNotFoundError:
                return
            raise LauncherError(reason)
        except LauncherError:
            raise
        except OSError as error:
            raise LauncherError(reason) from error
        finally:
            os.close(parent_fd)

    @staticmethod
    def _assert_activation_failure_artifacts_absent() -> None:
        for path in (*ACTIVATION_FAILURE_ARTIFACTS,
                     LEGACY_ACTIVATION_RECEIPT,
                     PREDECESSOR_ACTIVATION_RECEIPT):
            ProductionExecutor._assert_anchored_path_absent(
                path, "P1_LAUNCHER_ACTIVATION_FAILED_RECEIPT_PRESENT")

    @staticmethod
    def _assert_directory_without_authority(
        directory: Path,
        *,
        expected_uid: int,
        expected_gid: int,
        expected_mode: int,
        allow_bootstrap_lock: bool,
    ) -> None:
        reason = "P1_LAUNCHER_AUTHORITY_RESIDUE"
        directory_fd = ProductionExecutor._open_anchored_directory(
            directory, reason)
        if directory_fd is None:
            return
        lock_fd: int | None = None
        rebound_fd: int | None = None
        try:
            opened = os.fstat(directory_fd)
            _require(
                stat.S_ISDIR(opened.st_mode) and
                opened.st_uid == expected_uid and
                opened.st_gid == expected_gid and
                stat.S_IMODE(opened.st_mode) == expected_mode,
                reason,
            )
            opened_identity = ProductionExecutor._stable_metadata(opened)
            names = sorted(os.listdir(directory_fd))
            if not allow_bootstrap_lock or not names:
                _require(
                    not names and not os.listdir(directory_fd),
                    reason,
                )
            else:
                _require(names == [SESSION_BOOTSTRAP_LOCK], reason)
                before = os.stat(
                    SESSION_BOOTSTRAP_LOCK,
                    dir_fd=directory_fd,
                    follow_symlinks=False,
                )
                lock_flags = os.O_RDWR | os.O_CLOEXEC
                if hasattr(os, "O_NOFOLLOW"):
                    lock_flags |= os.O_NOFOLLOW
                lock_fd = os.open(
                    SESSION_BOOTSTRAP_LOCK,
                    lock_flags,
                    dir_fd=directory_fd,
                )
                locked = os.fstat(lock_fd)
                _require(
                    stat.S_ISREG(before.st_mode) and
                    stat.S_ISREG(locked.st_mode) and
                    (before.st_dev, before.st_ino) ==
                    (locked.st_dev, locked.st_ino) and
                    ProductionExecutor._stable_metadata(before) ==
                    ProductionExecutor._stable_metadata(locked) and
                    locked.st_uid == ROOT_UID and
                    locked.st_gid == ROOT_GID and
                    stat.S_IMODE(locked.st_mode) == 0o600 and
                    locked.st_nlink == 1 and locked.st_size == 0,
                    reason,
                )
                try:
                    fcntl.flock(lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                except BlockingIOError as error:
                    raise LauncherError(reason) from error
                final_names = sorted(os.listdir(directory_fd))
                final_opened = os.fstat(lock_fd)
                final_entry = os.stat(
                    SESSION_BOOTSTRAP_LOCK,
                    dir_fd=directory_fd,
                    follow_symlinks=False,
                )
                _require(
                    final_names == [SESSION_BOOTSTRAP_LOCK] and
                    ProductionExecutor._stable_metadata(locked) ==
                    ProductionExecutor._stable_metadata(final_opened) ==
                    ProductionExecutor._stable_metadata(final_entry),
                    reason,
                )
            final_directory = os.fstat(directory_fd)
            rebound_fd = ProductionExecutor._open_anchored_directory(
                directory, reason)
            _require(
                rebound_fd is not None and
                opened_identity ==
                ProductionExecutor._stable_metadata(final_directory) ==
                ProductionExecutor._stable_metadata(os.fstat(rebound_fd)),
                reason,
            )
        except LauncherError:
            raise
        except OSError as error:
            raise LauncherError(reason) from error
        finally:
            if rebound_fd is not None:
                os.close(rebound_fd)
            if lock_fd is not None:
                os.close(lock_fd)
            os.close(directory_fd)

    @staticmethod
    def _assert_service_owned_watch_private_without_authority() -> None:
        """Bind the fixed service-owned WATCH parent and empty private leaf.

        ``hepta-shadow-watch-alpha`` is a systemd ``StateDirectory`` owned by
        the WATCH service identity.  Keep that narrowly reviewed exception out
        of the general anchored-directory helper: ``/var/lib`` remains a
        root-only anchor, while both the service parent and its sole ``private``
        child must be exact 0700 WATCH-owned directories.  Every directory is
        held open while the inventories are checked and then rebound through a
        fresh no-follow traversal of the canonical path.
        """

        reason = "P1_LAUNCHER_AUTHORITY_RESIDUE"
        private_path = WATCH_PRIVATE
        _require(
            private_path.is_absolute() and
            private_path.name not in {"", ".", ".."} and
            private_path.parent.name not in {"", ".", ".."} and
            private_path.parent.parent != Path("/"),
            reason,
        )
        anchor_path = private_path.parent.parent
        parent_name = private_path.parent.name
        leaf_name = private_path.name
        flags = os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC
        if hasattr(os, "O_NOFOLLOW"):
            flags |= os.O_NOFOLLOW

        anchor_fd: int | None = None
        parent_fd: int | None = None
        leaf_fd: int | None = None
        rebound_anchor_fd: int | None = None
        rebound_parent_fd: int | None = None
        rebound_leaf_fd: int | None = None

        def require_root_anchor(metadata: os.stat_result) -> None:
            _require(
                stat.S_ISDIR(metadata.st_mode) and
                metadata.st_uid == ROOT_UID and
                metadata.st_gid == ROOT_GID and
                stat.S_IMODE(metadata.st_mode) & 0o022 == 0,
                reason,
            )

        def require_service_directory(metadata: os.stat_result) -> None:
            _require(
                stat.S_ISDIR(metadata.st_mode) and
                metadata.st_uid == WATCH_UID and
                metadata.st_gid == WATCH_GID and
                stat.S_IMODE(metadata.st_mode) == 0o700,
                reason,
            )

        try:
            anchor_fd = ProductionExecutor._open_anchored_directory(
                anchor_path, reason)
            _require(anchor_fd is not None, reason)
            anchor_opened = os.fstat(anchor_fd)
            require_root_anchor(anchor_opened)
            anchor_identity = ProductionExecutor._stable_metadata(
                anchor_opened)

            parent_before = os.stat(
                parent_name, dir_fd=anchor_fd, follow_symlinks=False)
            parent_fd = os.open(parent_name, flags, dir_fd=anchor_fd)
            parent_opened = os.fstat(parent_fd)
            require_service_directory(parent_before)
            require_service_directory(parent_opened)
            parent_identity = ProductionExecutor._stable_metadata(
                parent_opened)
            _require(
                ProductionExecutor._stable_metadata(parent_before) ==
                parent_identity,
                reason,
            )
            _require(
                sorted(os.listdir(parent_fd)) == [leaf_name],
                reason,
            )

            leaf_before = os.stat(
                leaf_name, dir_fd=parent_fd, follow_symlinks=False)
            leaf_fd = os.open(leaf_name, flags, dir_fd=parent_fd)
            leaf_opened = os.fstat(leaf_fd)
            require_service_directory(leaf_before)
            require_service_directory(leaf_opened)
            leaf_identity = ProductionExecutor._stable_metadata(leaf_opened)
            _require(
                ProductionExecutor._stable_metadata(leaf_before) ==
                leaf_identity and
                not os.listdir(leaf_fd) and not os.listdir(leaf_fd),
                reason,
            )

            anchor_final = os.fstat(anchor_fd)
            parent_final = os.fstat(parent_fd)
            leaf_final = os.fstat(leaf_fd)
            parent_entry_final = os.stat(
                parent_name, dir_fd=anchor_fd, follow_symlinks=False)
            leaf_entry_final = os.stat(
                leaf_name, dir_fd=parent_fd, follow_symlinks=False)
            require_root_anchor(anchor_final)
            for metadata in (
                    parent_final, parent_entry_final,
                    leaf_final, leaf_entry_final):
                require_service_directory(metadata)
            _require(
                anchor_identity ==
                ProductionExecutor._stable_metadata(anchor_final) and
                parent_identity ==
                ProductionExecutor._stable_metadata(parent_final) ==
                ProductionExecutor._stable_metadata(parent_entry_final) and
                leaf_identity ==
                ProductionExecutor._stable_metadata(leaf_final) ==
                ProductionExecutor._stable_metadata(leaf_entry_final) and
                sorted(os.listdir(parent_fd)) == [leaf_name] and
                not os.listdir(leaf_fd),
                reason,
            )

            rebound_anchor_fd = ProductionExecutor._open_anchored_directory(
                anchor_path, reason)
            _require(rebound_anchor_fd is not None, reason)
            rebound_anchor = os.fstat(rebound_anchor_fd)
            require_root_anchor(rebound_anchor)
            rebound_parent_fd = os.open(
                parent_name, flags, dir_fd=rebound_anchor_fd)
            rebound_parent = os.fstat(rebound_parent_fd)
            rebound_parent_entry = os.stat(
                parent_name,
                dir_fd=rebound_anchor_fd,
                follow_symlinks=False,
            )
            require_service_directory(rebound_parent)
            require_service_directory(rebound_parent_entry)
            rebound_leaf_fd = os.open(
                leaf_name, flags, dir_fd=rebound_parent_fd)
            rebound_leaf = os.fstat(rebound_leaf_fd)
            rebound_leaf_entry = os.stat(
                leaf_name,
                dir_fd=rebound_parent_fd,
                follow_symlinks=False,
            )
            require_service_directory(rebound_leaf)
            require_service_directory(rebound_leaf_entry)
            held_parent_names = sorted(os.listdir(parent_fd))
            held_leaf_names = os.listdir(leaf_fd)
            rebound_parent_names = sorted(os.listdir(rebound_parent_fd))
            rebound_leaf_names = os.listdir(rebound_leaf_fd)

            anchor_after_inventory = os.fstat(anchor_fd)
            rebound_anchor_after_inventory = os.fstat(rebound_anchor_fd)
            parent_after_inventory = os.fstat(parent_fd)
            rebound_parent_after_inventory = os.fstat(rebound_parent_fd)
            leaf_after_inventory = os.fstat(leaf_fd)
            rebound_leaf_after_inventory = os.fstat(rebound_leaf_fd)
            parent_entry_after_inventory = os.stat(
                parent_name, dir_fd=anchor_fd, follow_symlinks=False)
            rebound_parent_entry_after_inventory = os.stat(
                parent_name,
                dir_fd=rebound_anchor_fd,
                follow_symlinks=False,
            )
            leaf_entry_after_inventory = os.stat(
                leaf_name, dir_fd=parent_fd, follow_symlinks=False)
            rebound_leaf_entry_after_inventory = os.stat(
                leaf_name,
                dir_fd=rebound_parent_fd,
                follow_symlinks=False,
            )
            require_root_anchor(anchor_after_inventory)
            require_root_anchor(rebound_anchor_after_inventory)
            for metadata in (
                    parent_after_inventory,
                    rebound_parent_after_inventory,
                    parent_entry_after_inventory,
                    rebound_parent_entry_after_inventory,
                    leaf_after_inventory,
                    rebound_leaf_after_inventory,
                    leaf_entry_after_inventory,
                    rebound_leaf_entry_after_inventory):
                require_service_directory(metadata)
            _require(
                anchor_identity ==
                ProductionExecutor._stable_metadata(rebound_anchor) ==
                ProductionExecutor._stable_metadata(anchor_after_inventory) ==
                ProductionExecutor._stable_metadata(
                    rebound_anchor_after_inventory) and
                parent_identity ==
                ProductionExecutor._stable_metadata(rebound_parent) ==
                ProductionExecutor._stable_metadata(rebound_parent_entry) ==
                ProductionExecutor._stable_metadata(parent_after_inventory) ==
                ProductionExecutor._stable_metadata(
                    rebound_parent_after_inventory) ==
                ProductionExecutor._stable_metadata(
                    parent_entry_after_inventory) ==
                ProductionExecutor._stable_metadata(
                    rebound_parent_entry_after_inventory) and
                leaf_identity ==
                ProductionExecutor._stable_metadata(rebound_leaf) ==
                ProductionExecutor._stable_metadata(rebound_leaf_entry) ==
                ProductionExecutor._stable_metadata(leaf_after_inventory) ==
                ProductionExecutor._stable_metadata(
                    rebound_leaf_after_inventory) ==
                ProductionExecutor._stable_metadata(
                    leaf_entry_after_inventory) ==
                ProductionExecutor._stable_metadata(
                    rebound_leaf_entry_after_inventory) and
                held_parent_names == [leaf_name] and
                not held_leaf_names and
                rebound_parent_names == [leaf_name] and
                not rebound_leaf_names,
                reason,
            )
        except LauncherError:
            raise
        except OSError as error:
            raise LauncherError(reason) from error
        finally:
            for descriptor in (
                    rebound_leaf_fd, rebound_parent_fd, rebound_anchor_fd,
                    leaf_fd, parent_fd, anchor_fd):
                if descriptor is not None:
                    os.close(descriptor)

    @staticmethod
    def _builder_common(campaign_id: str, output: Path, marker: Path) -> list[str]:
        return [
            BUILDER, "--campaign-id", campaign_id,
            "--start-ms", "0", "--output", str(output),
            "--authority-marker", str(marker),
            "--audit-journal", str(AUDIT_JOURNAL),
            "--collector", COLLECTOR, "--exporter", EXPORTER,
            "--heptactl", HEPTACTL, "--gateway", GATEWAY,
            "--custodian", CUSTODIAN, "--observer", OBSERVER,
            "--host-controller", HOST,
            "--domain-config", str(DOMAIN_CONFIG),
            "--gateway-profile", str(GATEWAY_PROFILE),
        ]

    def build_policy(
        self,
        mode: str,
        configuration: LaunchConfiguration,
        paths: RunPaths,
    ) -> PolicyArtifacts:
        _require(mode in {"load-probe", "formal"},
                 "P1_LAUNCHER_POLICY_MODE_INVALID")
        if mode == "load-probe":
            campaign = configuration.probe_campaign_id
            output, marker = paths.probe_policy, paths.probe_marker
            # The probe must remain sample-only for all 91 ten-second runs.
            # Reuse the bounded formal warmup start; using wall-clock now
            # here could cross the distinct decision-window start.
            start_ms = configuration.formal_start_ms
        else:
            campaign = configuration.formal_campaign_id
            output, marker = paths.formal_policy, paths.formal_marker
            start_ms = configuration.formal_start_ms
        arguments = self._builder_common(campaign, output, marker)
        arguments[arguments.index("0")] = str(start_ms)
        arguments[1:1] = ["--mode", mode]
        if mode == "formal":
            arguments.extend([
                "--admission-receipt", str(paths.admission_receipt)])
        self._run(arguments, 30)
        policy, policy_contents = _document(output, "P1_LAUNCHER_POLICY",
                                            root_owned=True)
        marker_document, marker_contents = _document(
            marker, "P1_LAUNCHER_MARKER", root_owned=True)
        _require(
            policy.get("campaign_id") == campaign and
            marker_document.get("campaign_id") == campaign,
            "P1_LAUNCHER_POLICY_BINDING_INVALID",
        )
        valid_after_ms, maximum_iterations = _validated_policy_schedule(
            configuration, campaign, policy)
        return PolicyArtifacts(
            policy=policy,
            policy_file_sha256=digest_bytes(policy_contents),
            marker=marker_document,
            marker_file_sha256=digest_bytes(marker_contents),
            valid_after_ms=valid_after_ms,
            maximum_iterations=maximum_iterations,
        )

    @staticmethod
    def _reader_command(
        campaign_id: str,
        unit: str,
        launcher_unit: str,
        policy: Path,
        marker: Path,
        paths: RunPaths,
        *,
        formal: bool,
    ) -> list[str]:
        status = paths.reader_status(formal)
        artifacts = paths.reader_artifacts(formal)
        return [
            SYSTEMD_RUN, "--quiet", "--collect", f"--unit={unit}",
            f"--uid={READER_UID}", f"--gid={READER_GID}",
            "--service-type=exec",
            *TRANSIENT_ENVIRONMENT_ARGUMENTS,
            PAPER_CONFLICTS_PROPERTY,
            f"--property=BindsTo={launcher_unit} {GATEWAY_UNIT}",
            f"--property=After={launcher_unit} {GATEWAY_UNIT}",
            "--property=NoNewPrivileges=yes",
            "--property=PrivateNetwork=yes",
            "--property=RestrictAddressFamilies=AF_UNIX",
            "--property=PrivateTmp=yes",
            "--property=ProtectHome=yes",
            "--property=KillMode=mixed",
            "--", READER,
            "--campaign-id", campaign_id,
            "--policy", str(policy), "--strategy", str(STRATEGY),
            "--export-directory", str(WATCH_EXPORT),
            "--source-bundle", str(SOURCE_BUNDLE),
            "--artifact-root", str(artifacts),
            "--status", str(status), "--observer", OBSERVER,
            "--authority-marker", str(marker),
        ]

    def start_reader(
        self,
        campaign_id: str,
        unit: str,
        launcher_unit: str,
        policy: Path,
        marker: Path,
        paths: RunPaths,
        *,
        formal: bool,
    ) -> int:
        self._run(self._reader_command(
            campaign_id, unit, launcher_unit, policy, marker, paths,
            formal=formal), 30)
        shown = self._run([
            SYSTEMCTL, "show", "--no-pager", "--property=ActiveState",
            "--property=SubState", "--property=MainPID", unit,
        ], 5)
        parsed = dict(line.split("=", 1) for line in shown.stdout.splitlines())
        _require(
            parsed.get("ActiveState") == "active" and
            parsed.get("SubState") == "running" and
            parsed.get("MainPID", "").isdigit() and
            int(parsed["MainPID"]) > 1,
            "P1_LAUNCHER_READER_NOT_ACTIVE",
        )
        return int(parsed["MainPID"])

    def provision(self, campaign_id: str, owner_pid: int) -> Registration:
        result = self._run([
            CUSTODIAN, "--domain-config", str(DOMAIN_CONFIG), "provision",
            "--campaign-id", campaign_id,
            "--owner-pid", str(owner_pid),
            "--owner-uid", str(READER_UID),
            "--ttl-sec", str(WATCH_TTL_SECONDS),
        ], 30)
        document = self._json_stdout(result)
        generation = document.get("lease_generation")
        _require(type(generation) is int, "P1_LAUNCHER_REGISTRATION_INVALID")
        return Registration(campaign_id, generation, document)

    def start_backstop(self) -> None:
        self._run([
            SYSTEMCTL, "start",
            "hepta-shadow-watch-custodian-reconcile@alpha.timer",
            "hepta-shadow-watch-custodian@alpha.service",
        ], 30)

    @staticmethod
    def _host_prefix(
        host_unit_name: str,
        reader_unit_name: str,
        launcher_unit_name: str,
    ) -> list[str]:
        return [
            SYSTEMD_RUN, "--quiet", "--collect", "--wait", "--pipe",
            f"--unit={host_unit_name}", "--service-type=exec",
            *TRANSIENT_ENVIRONMENT_ARGUMENTS,
            PAPER_CONFLICTS_PROPERTY,
            f"--property=BindsTo={launcher_unit_name} {reader_unit_name} "
            f"{GATEWAY_UNIT}",
            f"--property=After={launcher_unit_name} {reader_unit_name} "
            f"{GATEWAY_UNIT}",
            f"--property=PropagatesStopTo={reader_unit_name}",
            "--property=NoNewPrivileges=yes", "--property=PrivateTmp=yes",
            "--property=ProtectHome=yes", "--property=KillMode=mixed",
            "--", HOST,
        ]

    @staticmethod
    def _host_common(
        configuration: LaunchConfiguration,
        reader_unit_name: str,
        generation: int,
        capture_sha256: str,
        paths: RunPaths,
        *,
        formal: bool,
        valid_after_ms: int,
        maximum_iterations: int,
    ) -> list[str]:
        campaign = (
            configuration.formal_campaign_id if formal
            else configuration.probe_campaign_id)
        return [
            "--campaign-id", campaign,
            "--domain-config", str(DOMAIN_CONFIG),
            "--start-generation", str(generation),
            "--valid-after-ms", str(valid_after_ms),
            "--maximum-iterations", str(maximum_iterations),
            "--capture-lead-sec", str(CAPTURE_LEAD_SECONDS),
            "--capture-timeout-sec", str(CAPTURE_TIMEOUT_SECONDS),
            "--evidence-root", str(EVIDENCE_ROOT),
            "--export-root", str(EXPORT_ROOT),
            "--reader-uid", str(READER_UID),
            "--reader-gid", str(READER_GID),
            "--capture-helper-sha256", capture_sha256,
            "--reader-unit", reader_unit_name,
            "--reader-status", str(paths.reader_status(formal)),
        ]

    def run_probe_host(
        self,
        configuration: LaunchConfiguration,
        paths: RunPaths,
        reader_unit: str,
        generation: int,
        capture_sha256: str,
    ) -> tuple[dict[str, Any], str]:
        arguments = self._host_prefix(
            host_unit(self._round(configuration.probe_campaign_id)),
            reader_unit,
            admission_unit(self._round(configuration.formal_campaign_id)))
        arguments += self._host_common(
            configuration, reader_unit, generation, capture_sha256, paths,
            formal=False,
            valid_after_ms=configuration.formal_start_ms,
            maximum_iterations=1)
        arguments += [
            "--load-probe-runs", str(LOAD_PROBE_RUNS),
            "--load-probe-receipt-output", str(paths.probe_host_receipt),
        ]
        self._run(arguments, 1_000)
        document, contents = _document(
            paths.probe_host_receipt, "P1_LAUNCHER_PROBE_HOST_RECEIPT",
            root_owned=True)
        return document, digest_bytes(contents)

    @staticmethod
    def _round(campaign_id: str) -> int:
        match = re.search(r"(?:^|-)round([1-9][0-9]*)(?:-|$)", campaign_id)
        _require(match is not None, "P1_LAUNCHER_CAMPAIGN_ROUND_INVALID")
        return int(match.group(1))

    def validate_probe(
        self,
        configuration: LaunchConfiguration,
        paths: RunPaths,
    ) -> tuple[dict[str, Any], str]:
        arguments = [
            VALIDATOR,
            "--campaign-id", configuration.probe_campaign_id,
            "--prospective-campaign-id", configuration.formal_campaign_id,
            "--prospective-policy", str(paths.formal_policy),
            "--authority-marker", str(paths.formal_marker),
            "--host-receipt", str(paths.probe_host_receipt),
            "--observer-controller-status", str(paths.reader_status(False)),
            "--observer-state", str(paths.reader_artifacts(False) /
                                      "observer-state.json"),
            "--artifact-root", str(paths.reader_artifacts(False)),
            "--audit-journal", str(AUDIT_JOURNAL),
            "--collector", COLLECTOR, "--exporter", EXPORTER,
            "--heptactl", HEPTACTL, "--gateway", GATEWAY,
            "--custodian", CUSTODIAN, "--observer", OBSERVER,
            "--host-controller", HOST,
            "--domain-config", str(DOMAIN_CONFIG),
            "--gateway-profile", str(GATEWAY_PROFILE),
            "--output", str(paths.admission_receipt),
        ]
        self._run(arguments, 60)
        document, contents = _document(
            paths.admission_receipt, "P1_LAUNCHER_ADMISSION",
            root_owned=True)
        return document, digest_bytes(contents)

    def run_formal_host(
        self,
        configuration: LaunchConfiguration,
        paths: RunPaths,
        reader_unit: str,
        generation: int,
        capture_sha256: str,
        policy_artifacts: PolicyArtifacts,
    ) -> dict[str, Any]:
        policy, policy_contents = _document(
            paths.formal_policy, "P1_LAUNCHER_FORMAL_POLICY_RECHECK",
            root_owned=True)
        _require(
            policy == policy_artifacts.policy and
            digest_bytes(policy_contents) ==
            policy_artifacts.policy_file_sha256,
            "P1_LAUNCHER_FORMAL_POLICY_DRIFT",
        )
        valid_after_ms, maximum_iterations = _validated_artifact_schedule(
            configuration,
            configuration.formal_campaign_id,
            policy_artifacts,
        )
        arguments = self._host_prefix(
            host_unit(self._round(configuration.formal_campaign_id)),
            reader_unit,
            admission_unit(self._round(configuration.formal_campaign_id)))
        arguments += self._host_common(
            configuration, reader_unit, generation, capture_sha256, paths,
            formal=True,
            valid_after_ms=valid_after_ms,
            maximum_iterations=maximum_iterations)
        arguments += [
            "--policy", str(paths.formal_policy),
            "--admission-receipt", str(paths.admission_receipt),
            "--authority-marker", str(paths.formal_marker),
            "--watch-snapshot", str(WATCH_SNAPSHOT),
        ]
        result = self._run(arguments, 400_000)
        return self._json_stdout(result)

    def read_formal_evidence(self, paths: RunPaths) -> FinalReaderArtifacts:
        status, status_contents = _document(
            paths.reader_status(True),
            "P1_LAUNCHER_FINAL_CONTROLLER_STATUS",
            expected_uid=READER_UID,
            expected_gid=READER_GID,
            modes=frozenset({0o600}),
        )
        observer_state, observer_state_contents = _document(
            paths.reader_artifacts(True) / "observer-state.json",
            "P1_LAUNCHER_FINAL_OBSERVER_STATE",
            expected_uid=READER_UID,
            expected_gid=READER_GID,
            modes=frozenset({0o600}),
        )
        final_audit, final_audit_contents = _document(
            paths.reader_artifacts(True) / "final-audit-receipt.json",
            "P1_LAUNCHER_FINAL_AUDIT",
            expected_uid=READER_UID,
            expected_gid=READER_GID,
            modes=frozenset({0o600}),
        )
        return FinalReaderArtifacts(
            controller_status=status,
            controller_status_file_sha256=digest_bytes(status_contents),
            observer_state=observer_state,
            observer_state_file_sha256=digest_bytes(observer_state_contents),
            final_audit_body_sha256=final_audit["body_sha256"],
            final_audit_file_sha256=digest_bytes(final_audit_contents),
        )

    def verify_formal_closure(
        self,
        paths: RunPaths,
    ) -> VerifiedClosureArtifacts:
        output = paths.formal_verified_closure
        artifact_root = paths.reader_artifacts(True)
        _require(
            output.is_absolute() and
            output.parent == paths.private_directory and
            artifact_root.is_absolute(),
            "P1_LAUNCHER_VERIFIED_CLOSURE_PATH_INVALID",
        )
        try:
            private_metadata = paths.private_directory.lstat()
        except OSError as error:
            raise LauncherError(
                "P1_LAUNCHER_VERIFIED_CLOSURE_DIRECTORY_INVALID") from error
        _require(
            stat.S_ISDIR(private_metadata.st_mode) and
            not stat.S_ISLNK(private_metadata.st_mode) and
            private_metadata.st_uid == ROOT_UID and
            private_metadata.st_gid == ROOT_GID and
            stat.S_IMODE(private_metadata.st_mode) == 0o700,
            "P1_LAUNCHER_VERIFIED_CLOSURE_DIRECTORY_INVALID",
        )
        try:
            output.relative_to(artifact_root)
        except ValueError:
            pass
        else:
            raise LauncherError(
                "P1_LAUNCHER_VERIFIED_CLOSURE_PATH_INVALID")

        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_CLOEXEC
        if hasattr(os, "O_NOFOLLOW"):
            flags |= os.O_NOFOLLOW
        try:
            descriptor = os.open(output, flags, 0o600)
            os.fchmod(descriptor, 0o600)
            os.fsync(descriptor)
            metadata = os.fstat(descriptor)
            _require(
                stat.S_ISREG(metadata.st_mode) and metadata.st_nlink == 1 and
                metadata.st_uid == ROOT_UID and metadata.st_gid == ROOT_GID and
                stat.S_IMODE(metadata.st_mode) == 0o600 and
                metadata.st_size == 0,
                "P1_LAUNCHER_VERIFIED_CLOSURE_RESERVATION_INVALID",
            )
            directory_descriptor = os.open(
                paths.private_directory,
                os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC,
            )
            try:
                os.fsync(directory_descriptor)
            finally:
                os.close(directory_descriptor)
        except FileExistsError as error:
            raise LauncherError(
                "P1_LAUNCHER_VERIFIED_CLOSURE_EXISTS") from error
        except OSError as error:
            raise LauncherError(
                "P1_LAUNCHER_VERIFIED_CLOSURE_RESERVATION_FAILED") from error
        finally:
            if "descriptor" in locals():
                os.close(descriptor)

        self._run([
            VERIFIER,
            "--artifact-root", str(artifact_root),
            "--policy", str(paths.formal_policy),
            "--strategy", str(STRATEGY),
            "--output", str(output),
        ], VERIFIER_TIMEOUT_SECONDS)
        closure, contents = _document(
            output,
            "P1_LAUNCHER_VERIFIED_CLOSURE",
            root_owned=True,
        )
        return VerifiedClosureArtifacts(
            closure=closure,
            closure_file_sha256=digest_bytes(contents),
            strategy_file_sha256=digest_bytes(_secure_read(
                STRATEGY,
                "P1_LAUNCHER_STRATEGY_FILE_INVALID",
                4 * 1024 * 1024,
                expected_uid=ROOT_UID,
                expected_gid=ROOT_GID,
                modes=frozenset({0o444, 0o600, 0o640, 0o644}),
            )),
        )

    def assert_reader_active(self, unit: str, pid: int) -> dict[str, Any]:
        _require(
            re.fullmatch(
                r"hepta-p1-shadow-reader-round[1-9][0-9]*\.service",
                unit) is not None and type(pid) is int and pid > 1,
            "P1_LAUNCHER_READER_IDENTITY_INVALID",
        )
        shown = self._run([
            SYSTEMCTL, "show", "--no-pager",
            "--property=ActiveState", "--property=SubState",
            "--property=MainPID", unit,
        ], 5)
        parsed: dict[str, str] = {}
        for line in shown.stdout.splitlines():
            key, separator, value = line.partition("=")
            _require(
                separator == "=" and key not in parsed,
                "P1_LAUNCHER_READER_IDENTITY_INVALID",
            )
            parsed[key] = value
        _require(
            parsed == {
                "ActiveState": "active",
                "SubState": "running",
                "MainPID": str(pid),
            },
            "P1_LAUNCHER_READER_IDENTITY_INVALID",
        )
        return {
            "unit": unit,
            "active_state": "active",
            "sub_state": "running",
            "main_pid": pid,
        }

    def stop_unit(self, unit: str) -> None:
        _require(
            re.fullmatch(r"hepta-p1-shadow-(?:host|reader)-round[1-9][0-9]*\.service",
                         unit) is not None,
            "P1_LAUNCHER_UNIT_INVALID",
        )
        result = self._run(
            [SYSTEMCTL, "stop", unit], 30, allow_failure=True)
        _require(result.returncode in {0, 5}, "P1_LAUNCHER_STOP_FAILED")

    def close_and_verify(self, reason: str) -> dict[str, Any]:
        _require(reason in {"service-stop", "operator-request"},
                 "P1_LAUNCHER_CLOSE_REASON_INVALID")
        result = self._run([
            CUSTODIAN, "--domain-config", str(DOMAIN_CONFIG), "close",
            "--reason", reason,
        ], 30)
        closure = self._json_stdout(result)
        if closure.get("schema") == "hepta.shadow-watch-custodian-closure.v1":
            _require(
                closure.get("authoritative_revoke_outcome") in {
                    "ACCEPTED", "ALREADY_ABSENT", "EXPIRED"} and
                closure.get("local_authority_removed") is True and
                closure.get("export_evidence_removed") is True,
                "P1_LAUNCHER_CLOSURE_INVALID",
            )
        else:
            _require(
                closure.get("status") == "NO_ACTIVE_TRANSACTION",
                "P1_LAUNCHER_CLOSURE_INVALID",
            )
        reconciled = self._run([
            CUSTODIAN, "--domain-config", str(DOMAIN_CONFIG), "reconcile",
        ], 30)
        document = self._json_stdout(reconciled)
        _require(document.get("status") == "NO_ACTIVE_TRANSACTION",
                 "P1_LAUNCHER_RECONCILE_INVALID")
        self._assert_no_residue()
        return closure


def _install_signal_handlers() -> dict[int, Any]:
    previous: dict[int, Any] = {}
    observed = False

    def handler(signum: int, _frame: Any) -> None:
        nonlocal observed
        if observed:
            return
        observed = True
        raise LauncherSignal(signum)

    for signum in (signal.SIGINT, signal.SIGTERM, signal.SIGHUP):
        previous[signum] = signal.getsignal(signum)
        signal.signal(signum, handler)
    return previous


def _restore_signal_handlers(previous: dict[int, Any]) -> None:
    for signum, handler in previous.items():
        signal.signal(signum, handler)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(allow_abbrev=False)
    parser.add_argument("--probe-campaign-id", required=True)
    parser.add_argument("--formal-campaign-id", required=True)
    parser.add_argument("--formal-start-ms", required=True, type=int)
    return parser


def main() -> int:
    arguments = _parser().parse_args()
    try:
        _require(os.geteuid() == 0 and os.getegid() == 0,
                 "P1_LAUNCHER_ROOT_REQUIRED")
        configuration = LaunchConfiguration(
            probe_campaign_id=arguments.probe_campaign_id,
            formal_campaign_id=arguments.formal_campaign_id,
            formal_start_ms=arguments.formal_start_ms,
        )
        previous = _install_signal_handlers()
        try:
            result = Launcher(
                configuration, ProductionExecutor(), ProductionStateStore()
            ).run()
        finally:
            for signum in previous:
                signal.signal(signum, signal.SIG_IGN)
            _restore_signal_handlers(previous)
    except (LauncherError, OSError, ValueError) as error:
        print(f"hepta_p1_shadow_admission_launcher: FAIL {_reason(error)}",
              file=sys.stderr)
        return 78
    sys.stdout.buffer.write(canonical_bytes({
        "schema": "hepta.p1-shadow-admission-launcher-result.v1",
        "status": result["status"],
        "receipt_body_sha256": result["body_sha256"],
        "paper_authorized": False,
        "live_authorized": False,
        "mutation_authorized": False,
        "direct_broker_access": False,
    }))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
