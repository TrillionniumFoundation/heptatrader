#!/usr/bin/env python3

"""Fake-only tests for the root P1 SHADOW admission launcher."""

from __future__ import annotations

from contextlib import ExitStack
from dataclasses import replace
import copy
import importlib.util
import json
import os
from pathlib import Path
import stat
import subprocess
import sys
import tempfile
import time
from types import SimpleNamespace
import unittest
from unittest import mock


SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
ROOT = SCRIPTS.parent
sys.path.insert(0, str(SCRIPTS))
SCRIPT = SCRIPTS / "hepta_p1_shadow_admission_launcher.py"
SPEC = importlib.util.spec_from_file_location("p1_admission_launcher", SCRIPT)
assert SPEC is not None and SPEC.loader is not None
LAUNCHER = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = LAUNCHER
SPEC.loader.exec_module(LAUNCHER)
ACTIVATION_SCRIPT = SCRIPTS / "hepta_p1_watch_activation_transaction.py"
ACTIVATION_SPEC = importlib.util.spec_from_file_location(
    "p1_watch_activation_contract", ACTIVATION_SCRIPT)
assert ACTIVATION_SPEC is not None and ACTIVATION_SPEC.loader is not None
ACTIVATION_CONTRACT = importlib.util.module_from_spec(ACTIVATION_SPEC)
sys.modules[ACTIVATION_SPEC.name] = ACTIVATION_CONTRACT
ACTIVATION_SPEC.loader.exec_module(ACTIVATION_CONTRACT)
import hepta_shadow_market_history as MARKET_HISTORY  # noqa: E402
import hepta_p1_shadow_host_controller as HOST_CONTROLLER  # noqa: E402
import build_hepta_p1_observation_policy as POLICY_BUILDER  # noqa: E402


class LaunchClock:
    def __init__(self, wall_ms: int) -> None:
        self.wall_ms = wall_ms
        self.monotonic_seconds = 100.0

    def wall(self) -> int:
        return self.wall_ms

    def monotonic(self) -> float:
        return self.monotonic_seconds

    def sleep(self, seconds: float) -> None:
        if seconds < 0:
            raise AssertionError("negative fake sleep")
        elapsed_ms = round(seconds * 1000)
        self.wall_ms += elapsed_ms
        self.monotonic_seconds += seconds


def ancestor_evidence(
    *, uid: int | None = None, gid: int | None = None,
) -> dict:
    if uid is None:
        uid = LAUNCHER.ROOT_UID
    if gid is None:
        gid = LAUNCHER.ROOT_GID
    return {
        "receipt_path": str(
            LAUNCHER.ANCESTOR_ACTIVATION_FAILED_RECEIPT),
        "receipt_file_sha256":
            LAUNCHER.ANCESTOR_ACTIVATION_FAILED_RECEIPT_FILE_SHA256,
        "receipt_body_sha256":
            LAUNCHER.ANCESTOR_ACTIVATION_FAILED_RECEIPT_BODY_SHA256,
        "receipt_schema":
            "hepta.p1-watch-activation-failed-receipt.v1",
        "receipt_version": 1, "receipt_revision": 1,
        "receipt_status": "FAILED_CLOSED", "receipt_round": 86,
        "receipt_domain": "alpha",
        "receipt_reason": "ACTIVATION_SYSTEMCTL_FAILED",
        "receipt_device": 1, "receipt_inode": 2,
        "receipt_mode": stat.S_IFREG | 0o600, "receipt_nlink": 1,
        "receipt_uid": uid,
        "receipt_gid": gid,
        "receipt_bytes": 1024,
        "receipt_mtime_ns": 1, "receipt_ctime_ns": 1,
        "journal_path": str(LAUNCHER.ANCESTOR_ACTIVATION_JOURNAL),
        "journal_sha256": LAUNCHER.ANCESTOR_ACTIVATION_JOURNAL_SHA256,
        "journal_record_count": 5,
        "journal_terminal_phase": "FAILED_CLOSED",
    }


def predecessor_success_evidence() -> dict:
    return {
        "receipt_path": str(
            LAUNCHER.PREDECESSOR_ACTIVATION_SUCCESS_RECEIPT),
        "receipt_file_sha256":
            LAUNCHER.PREDECESSOR_ACTIVATION_SUCCESS_RECEIPT_FILE_SHA256,
        "receipt_body_sha256":
            LAUNCHER.PREDECESSOR_ACTIVATION_SUCCESS_RECEIPT_BODY_SHA256,
        "receipt_schema": "hepta.p1-watch-activation-receipt.v3",
        "receipt_version": 3,
        "receipt_status": "WATCH_GATEWAY_ACTIVATED",
        "receipt_round": 95,
        "receipt_domain": "alpha",
        "receipt_device": 1, "receipt_inode": 2,
        "receipt_mode": stat.S_IFREG | 0o600, "receipt_nlink": 1,
        "receipt_uid": LAUNCHER.ROOT_UID,
        "receipt_gid": LAUNCHER.ROOT_GID,
        "receipt_bytes": 1024,
        "receipt_mtime_ns": 1, "receipt_ctime_ns": 1,
    }


def predecessor_evidence() -> dict:
    return {
        "receipt_path": str(
            LAUNCHER.PREDECESSOR_ACTIVATION_FAILED_RECEIPT),
        "receipt_file_sha256":
            LAUNCHER.PREDECESSOR_ACTIVATION_FAILED_RECEIPT_FILE_SHA256,
        "receipt_body_sha256":
            LAUNCHER.PREDECESSOR_ACTIVATION_FAILED_RECEIPT_BODY_SHA256,
        "receipt_schema":
            "hepta.p1-watch-activation-failed-receipt.v2",
        "receipt_version": 2, "receipt_revision": 1,
        "receipt_status": "FAILED_CLOSED", "receipt_round": 95,
        "receipt_domain": "alpha",
        "receipt_reason": "ACTIVATION_SYSTEMCTL_FAILED",
        "receipt_device": 1, "receipt_inode": 2,
        "receipt_mode": stat.S_IFREG | 0o600, "receipt_nlink": 1,
        "receipt_uid": LAUNCHER.ROOT_UID,
        "receipt_gid": LAUNCHER.ROOT_GID, "receipt_bytes": 1024,
        "receipt_mtime_ns": 1, "receipt_ctime_ns": 1,
        "journal_path": str(LAUNCHER.PREDECESSOR_ACTIVATION_JOURNAL),
        "journal_sha256": LAUNCHER.PREDECESSOR_ACTIVATION_JOURNAL_SHA256,
        "journal_record_count": 21,
        "journal_terminal_phase": "FAILED_CLOSED",
    }


def closure(campaign_id: str, generation: int) -> dict:
    return LAUNCHER.seal({
        "schema": "hepta.shadow-watch-custodian-closure.v1",
        "version": 1,
        "domain_id": "alpha",
        "campaign_id": campaign_id,
        "lease_generation": generation,
        "authoritative_revoke_outcome": "ACCEPTED",
        "local_authority_removed": True,
        "export_evidence_removed": True,
        "paper_authorized": False,
        "live_authorized": False,
        "mutation_authorized": False,
        "direct_broker_access": False,
    })


def policy_document(configuration, campaign_id: str) -> dict:
    warmup_start_ms = configuration.formal_start_ms
    decision_window_start_ms = (
        warmup_start_ms +
        LAUNCHER.POLICY_MINIMUM_WARMUP_MS)
    expires_at_ms = (
        decision_window_start_ms +
        LAUNCHER.FORMAL_ITERATIONS * LAUNCHER.POLICY_SLOT_INTERVAL_MS)
    campaign_binding = {
        "schema": "hepta.strategy-shadow-observation-campaign.v1",
        "campaign_id": campaign_id,
        "valid_after_ms": decision_window_start_ms,
        "expires_at_ms": expires_at_ms,
        "slot_interval_ms": LAUNCHER.POLICY_SLOT_INTERVAL_MS,
        "maximum_iterations": LAUNCHER.FORMAL_ITERATIONS,
        "maximum_lateness_ms": LAUNCHER.POLICY_MAXIMUM_LATENESS_MS,
        "shadow_only": True,
        "paper_authorized": False,
        "live_authorized": False,
        "mutation_attempted": False,
        "direct_broker_access": False,
    }
    return LAUNCHER.seal({
        "schema": "hepta.strategy-shadow-observation-policy.v1",
        "version": 1,
        "campaign_id": campaign_id,
        "campaign_sha256": LAUNCHER.digest_bytes(
            LAUNCHER.canonical_bytes(campaign_binding)),
        "strategy_id": "eurusd-confirmed-momentum",
        "strategy_version": "v2",
        "strategy_sha256": "sha256:" + "9" * 64,
        "valid_after_ms": decision_window_start_ms,
        "expires_at_ms": expires_at_ms,
        "slot_interval_ms": LAUNCHER.POLICY_SLOT_INTERVAL_MS,
        "maximum_iterations": LAUNCHER.FORMAL_ITERATIONS,
        "maximum_lateness_ms": LAUNCHER.POLICY_MAXIMUM_LATENESS_MS,
        "shadow_only": True,
        "paper_authorized": False,
        "live_authorized": False,
        "mutation_attempted": False,
        "direct_broker_access": False,
    })


def activation_receipt_document(
    profile_receipt: dict,
    profile_receipt_contents: bytes,
    broker: dict,
    gateway: dict,
    boot_id: str,
) -> dict:
    return LAUNCHER.seal({
        "schema": "hepta.p1-watch-activation-receipt.v4",
        "version": 4,
        "status": "WATCH_GATEWAY_ACTIVATED",
        "round": 114,
        "domain": "alpha",
        "started_at_ms": 1000,
        "completed_at_ms": 2000,
        "boot_id": boot_id,
        "profile_deployment_receipt_path":
            str(LAUNCHER.PROFILE_DEPLOYMENT_RECEIPT),
        "profile_deployment_receipt_file_sha256":
            LAUNCHER.digest_bytes(profile_receipt_contents),
        "profile_deployment_receipt_body_sha256":
            profile_receipt["body_sha256"],
        "profile_sha256": LAUNCHER.EXPECTED_GATEWAY_PROFILE_SHA256,
        "profile_bytes": 736,
        "journal_sha256": "sha256:" + "d" * 64,
        "broker_before": {
            "policy_sha256": "sha256:" + "e" * 64,
            "authorized_connectors": 0,
            "authorized_uids": [],
            "protected_ports": 4,
        },
        "broker_after": copy.deepcopy(broker),
        "gateway_after": copy.deepcopy(gateway),
        "reconcile_timer": {
            "unit": LAUNCHER.ACTIVATION_RECONCILE_TIMER,
            "load_state": "loaded",
            "active_state": "active",
            "sub_state": "waiting",
            "job": "",
            "unit_file_state": "enabled",
            "unit_contract_sha256": LAUNCHER.digest_bytes(
                LAUNCHER.canonical_bytes({
                    "LoadState": "loaded",
                    "ActiveState": "active",
                    "SubState": "waiting",
                    "Job": "",
                    "UnitFileState": "enabled",
                })),
        },
        "paper_units": {
            unit: {"ActiveState": "inactive", "SubState": "dead", "Job": ""}
            for unit in LAUNCHER.PAPER_UNITS
        },
        "kill_switch_engaged": True,
        "watch_boundary": {
            "export_absent": True,
            "sessions_authority_count": 0,
            "private_authority_count": 0,
            "custodian_transaction_absent": True,
            "session_bootstrap_idle_lock_observed": True,
        },
        "stale_bundles": [
            {
                "round": round_number,
                "status": "QUARANTINED",
                "bundle_sha256": "sha256:" + "a" * 64,
                "terminal_receipt_sha256":
                    LAUNCHER.STALE_TERMINAL_RECEIPT_SHA256[round_number],
                "quarantine_root": (
                    "/var/lib/hepta/p1-admission/quarantine/"
                    f"activation-round114/round{round_number}"),
            }
            for round_number in (110, 112)
        ],
        "systemctl_mutations": [
            [LAUNCHER.SYSTEMCTL, "enable", "--now",
             LAUNCHER.ACTIVATION_RECONCILE_TIMER],
            [LAUNCHER.SYSTEMCTL, "daemon-reload"],
            [LAUNCHER.SYSTEMCTL, "start", LAUNCHER.BROKER_EGRESS_UNIT],
            [LAUNCHER.SYSTEMCTL, "unmask", LAUNCHER.GATEWAY_UNIT,
             "hepta-tool-gateway@alpha.socket",
             "hepta-tool-session-supervisor@alpha.socket"],
            [LAUNCHER.SYSTEMCTL, "unmask", "--runtime",
             LAUNCHER.GATEWAY_UNIT,
             "hepta-tool-gateway@alpha.socket",
             "hepta-tool-session-supervisor@alpha.socket"],
            [LAUNCHER.SYSTEMCTL, "daemon-reload"],
            [LAUNCHER.SYSTEMCTL, "start", LAUNCHER.GATEWAY_UNIT],
        ],
        "fresh_activation_transaction": True,
        "gateway_activated": True,
        "gateway_profile_loaded": True,
        "gateway_contract_binding_loaded": True,
        "broker_loaded_source_attested": True,
        "broker_deny_all_continuity_attested": True,
        "watch_authority_provisioned": False,
        "campaign_launched": False,
        "paper_authorized": False,
        "live_authorized": False,
        "mutation_attempted": False,
        "direct_broker_access": False,
        "admission_prerequisite_satisfied": True,
        "paper_prerequisite_satisfied": False,
        "shadow_install_evidence": copy.deepcopy(
            profile_receipt["shadow_install_evidence"]),
        "predecessor_activation_success": predecessor_success_evidence(),
        "predecessor_activation_failure": predecessor_evidence(),
    })


def shadow_install_evidence() -> dict:
    digest_a = "sha256:" + "1" * 64
    digest_b = "sha256:" + "2" * 64
    return {
        "schema": "hepta.shadow-runtime-install-consumption-evidence.v3",
        "version": 3,
        "receipt_path": str(LAUNCHER.SHADOW_INSTALL_RECEIPT_PATH),
        "receipt_file_sha256": digest_a,
        "receipt_body_sha256": digest_b,
        "manifest_path": str(LAUNCHER.SHADOW_INSTALL_MANIFEST_PATH),
        "manifest_file_sha256": digest_a,
        "current_install_pointer_path":
            str(LAUNCHER.SHADOW_CURRENT_INSTALL_POINTER_PATH),
        "current_install_pointer_file_sha256": digest_b,
        "install_generation": LAUNCHER.EXPECTED_SHADOW_INSTALL_GENERATION,
        "predecessor_install_generation":
            LAUNCHER.EXPECTED_PREDECESSOR_SHADOW_INSTALL_GENERATION,
        "predecessor_current_install_pointer_file_sha256":
            LAUNCHER.EXPECTED_PREDECESSOR_CURRENT_INSTALL_POINTER_FILE_SHA256,
        "archive_sha256": digest_b,
        "source_baseline_sha256": digest_a,
        "installer_sha256": digest_b,
        "installed_file_count": LAUNCHER.SHADOW_INSTALL_FILE_COUNT,
        "installed_paths_sha256": digest_a,
        "closure_sha256": digest_b,
        "transaction_lock": {
            "path": str(LAUNCHER.SHADOW_INSTALL_LOCK_PATH),
            "device": 1,
            "inode": 2,
            "nlink": 1,
            "uid": 0,
            "gid": 0,
            "mode": "0600",
            "size": 0,
            "mtime_ns": 3,
            "ctime_ns": 4,
            "created_during_transaction": False,
            "persistent": True,
            "held_during_transaction": True,
        },
        "default_deny_identity_sha256":
            LAUNCHER.SHADOW_DEFAULT_DENY_IDENTITY_SHA256,
        "lock_mode": "exclusive",
        "verified_under_lock": True,
        "domain": "alpha",
        "backup_root": str(LAUNCHER.SHADOW_INSTALL_BACKUP_ROOT),
        "paper_authorized": False,
        "live_authorized": False,
        "mutation_attempted": False,
        "direct_broker_access": False,
    }


def live_binding_documents() -> tuple[
    dict, dict, dict, dict, dict,
]:
    evidence = shadow_install_evidence()
    broker = {"unit_contract_sha256": "sha256:" + "1" * 64}
    gateway = {"unit_contract_sha256": "sha256:" + "2" * 64}
    timer = {"unit_contract_sha256": "sha256:" + "3" * 64}
    receipt = {
        "body_sha256": "sha256:" + "4" * 64,
        "started_at_ms": 1000,
        "completed_at_ms": 2000,
        "broker_after": broker,
        "gateway_after": gateway,
        "reconcile_timer": timer,
        "shadow_install_evidence": copy.deepcopy(evidence),
    }
    profile = {
        "body_sha256": "sha256:" + "5" * 64,
        "shadow_install_evidence": copy.deepcopy(evidence),
    }
    return receipt, profile, broker, gateway, timer


def predecessor_success_fixture(root: Path) -> dict:
    root.mkdir(mode=0o700, parents=True, exist_ok=True)
    receipt_path = root / "p1-watch-activation-round95-receipt-v3.json"
    receipt = {
        field: None
        for field in LAUNCHER.PREDECESSOR_ACTIVATION_SUCCESS_RECEIPT_FIELDS
        if field != "body_sha256"
    }
    receipt.update({
        "schema": "hepta.p1-watch-activation-receipt.v3",
        "version": 3,
        "status": "WATCH_GATEWAY_ACTIVATED",
        "round": 95,
        "domain": "alpha",
        "journal_sha256": "sha256:" + "d" * 64,
        "fresh_activation_transaction": True,
        "gateway_activated": True,
        "gateway_profile_loaded": True,
        "gateway_contract_binding_loaded": True,
        "watch_authority_provisioned": False,
        "campaign_launched": False,
        "paper_authorized": False,
        "live_authorized": False,
        "mutation_attempted": False,
        "direct_broker_access": False,
        "admission_prerequisite_satisfied": True,
        "paper_prerequisite_satisfied": False,
        "predecessor_activation_failure": ancestor_evidence(
            uid=os.geteuid(), gid=os.getegid()),
    })
    receipt = LAUNCHER.seal(receipt)
    receipt_payload = LAUNCHER.canonical_bytes(receipt)
    receipt_path.write_bytes(receipt_payload)
    receipt_path.chmod(0o600)
    return {
        "receipt_path": receipt_path,
        "receipt_payload": receipt_payload,
        "receipt": receipt,
        "receipt_file_sha256": LAUNCHER.digest_bytes(receipt_payload),
        "receipt_body_sha256": receipt["body_sha256"],
        "journal_sha256": receipt["journal_sha256"],
    }


def predecessor_fixture(root: Path) -> dict:
    root.mkdir(mode=0o700, parents=True, exist_ok=True)
    receipt_path = root / "p1-watch-activation-round95-failed-receipt-v2.json"
    journal_path = root / "journal"
    journal_path.mkdir(mode=0o700)
    reason = "ACTIVATION_SYSTEMCTL_FAILED"
    receipt = LAUNCHER.seal({
        "schema": "hepta.p1-watch-activation-failed-receipt.v2",
        "version": 2, "revision": 1, "status": "FAILED_CLOSED",
        "round": 95, "domain": "alpha", "reason": reason,
        "completed_at_ms": 100,
        "quarantine": {
            "errors": [],
            "deny_all": {
                "policy_sha256": "sha256:" + "a" * 64,
                "authorized_connectors": 0,
                "authorized_uids": [], "protected_ports": 4,
            },
            "complete": True,
        },
        "previous_failed_receipt": None,
        "predecessor_activation_failure": ancestor_evidence(
            uid=os.geteuid(), gid=os.getegid()),
        "paper_authorized": False, "live_authorized": False,
        "mutation_attempted": False, "direct_broker_access": False,
    })
    receipt_payload = LAUNCHER.canonical_bytes(receipt)
    receipt_path.write_bytes(receipt_payload)
    receipt_path.chmod(0o600)
    phases = tuple(
        (phase, {})
        for phase in LAUNCHER.PREDECESSOR_ACTIVATION_PHASES
    ) + (
        ("QUARANTINE_INTENT", {"reason": reason}),
        ("GATEWAY_MASKED_STOPPED", {"evidence": {}}),
        ("BROKER_DENY_ALL", {"evidence": receipt["quarantine"]["deny_all"]}),
        ("AUTHORITY_EMPTY", {
            "export_absent": True, "sessions_authority_count": 0,
            "private_authority_count": 0,
            "custodian_transaction_absent": True,
            "session_bootstrap_idle_lock_observed": True,
        }),
        ("FAILED_CLOSED", {"complete": True}),
    )
    previous = None
    file_sha256s = []
    for sequence, (phase, evidence) in enumerate(phases):
        record = LAUNCHER.seal({
            "schema": "hepta.p1-watch-activation-journal.v1",
            "version": 1, "sequence": sequence, "phase": phase,
            "recorded_at_ms": sequence + 1,
            "previous_record_sha256": previous,
            "evidence": evidence,
        })
        payload = LAUNCHER.canonical_bytes(record)
        path = journal_path / f"{sequence:04d}-{phase}.json"
        path.write_bytes(payload)
        path.chmod(0o600)
        previous = LAUNCHER.digest_bytes(payload)
        file_sha256s.append(previous)
    return {
        "receipt_path": receipt_path,
        "receipt_payload": receipt_payload,
        "receipt": receipt,
        "receipt_file_sha256": LAUNCHER.digest_bytes(receipt_payload),
        "receipt_body_sha256": receipt["body_sha256"],
        "journal_path": journal_path,
        "journal_sha256": LAUNCHER.digest_bytes(
            LAUNCHER.canonical_bytes(file_sha256s)),
    }


class FakeStore:
    def __init__(self) -> None:
        self.state = None
        self.receipt = None

    def write_state(self, _paths, document) -> None:
        if self.state is not None:
            raise LAUNCHER.LauncherError("FAKE_STATE_EXISTS")
        self.state = document

    def write_receipt(self, _paths, document) -> None:
        if self.receipt is not None:
            raise LAUNCHER.LauncherError("FAKE_RECEIPT_EXISTS")
        self.receipt = document


class FakeExecutor:
    def __init__(
        self,
        configuration,
        *,
        fail_at: str | None = None,
        signal_at: str | None = None,
    ) -> None:
        self.configuration = configuration
        self.fail_at = fail_at
        self.signal_at = signal_at
        self.actions: list[str] = []
        self.counts: dict[str, int] = {}
        self.probe_generation = 1
        self.formal_generation = 1
        self._gateway = {
            "gateway_invocation_id": "a" * 32,
            "gateway_main_pid": 1234,
            "gateway_exec_main_start_timestamp_monotonic_us": 5_000_000,
            "gateway_socket_device": 11,
            "gateway_socket_inode": 12,
            "domain_config_sha256": "sha256:" + "d" * 64,
            "gateway_profile_sha256": "sha256:" + "e" * 64,
            "gateway_process_profile_sha256": "sha256:" + "f" * 64,
        }
        self._activation = {
            "activation_receipt_file_sha256": "sha256:" + "1" * 64,
            "activation_receipt_body_sha256": "sha256:" + "2" * 64,
            "profile_receipt_path": str(
                LAUNCHER.PROFILE_DEPLOYMENT_RECEIPT),
            "profile_receipt_file_sha256": "sha256:" + "3" * 64,
            "profile_receipt_body_sha256": "sha256:" + "4" * 64,
            "reconcile_timer": {
                "unit": LAUNCHER.ACTIVATION_RECONCILE_TIMER,
                "load_state": "loaded",
                "active_state": "active",
                "sub_state": "waiting",
                "job": "",
                "unit_file_state": "enabled",
                "unit_contract_sha256": "sha256:" + "9" * 64,
            },
            "broker": {
                "unit": LAUNCHER.BROKER_EGRESS_UNIT,
                "active_state": "active",
                "sub_state": "running",
                "main_pid": 2234,
                "invocation_id": "c" * 32,
                "exec_main_start_timestamp_monotonic_us": 4_000_000,
                "process_starttime_ticks": 123456,
                "interpreter_path": str(LAUNCHER.BROKER_INTERPRETER),
                "interpreter_sha256": "sha256:" + "6" * 64,
                "credential_source_path": str(
                    LAUNCHER.BROKER_CREDENTIAL_SOURCE),
                "credential_source_sha256": "sha256:" + "7" * 64,
                "installed_source_path": str(
                    LAUNCHER.BROKER_EGRESS_POLICY),
                "installed_source_sha256": "sha256:" + "7" * 64,
                "cmdline_sha256": "sha256:" + "5" * 64,
                "status_text":
                    "HeptaTrader broker boundary exact deny-all",
                "tasks_current": 1,
                "deny_all_policy_sha256": "sha256:" + "8" * 64,
                "authorized_connectors": 0,
                "authorized_uids": [],
                "protected_ports": 4,
                "unit_contract_sha256": "sha256:" + "a" * 64,
            },
            "gateway": {
                "unit": LAUNCHER.GATEWAY_UNIT,
                "active_state": "active",
                "sub_state": "running",
                "gateway_main_pid": 1234,
                "gateway_invocation_id": "a" * 32,
                "gateway_exec_main_start_timestamp_monotonic_us": 5_000_000,
                "process_starttime_ticks": 654321,
                "gateway_executable_path": LAUNCHER.GATEWAY,
                "gateway_executable_sha256": "sha256:" + "b" * 64,
                "domain_config_sha256": "sha256:" + "d" * 64,
                "gateway_profile_path": str(LAUNCHER.GATEWAY_PROFILE),
                "gateway_profile_sha256":
                    LAUNCHER.EXPECTED_GATEWAY_PROFILE_SHA256,
                "gateway_process_profile_sha256": "sha256:" + "f" * 64,
                "execution_remote_mode": "SIMULATOR",
                "tool_account": "SIM",
                "execution_domain_id": "SIM:alpha",
                "tool_allow_trade": "0",
                "session_templates": "watch",
                "contract_bindings": "EUR.USD|EUR|CASH|IDEALPRO|USD",
                "gateway_socket_path": str(LAUNCHER.GATEWAY_SOCKET),
                "gateway_socket_device": 11,
                "gateway_socket_inode": 12,
                "supervisor_socket_path": (
                    "/run/hepta-tool-gateway-alpha/"
                    "session-supervisor.sock"),
                "supervisor_socket_device": 13,
                "supervisor_socket_inode": 14,
                "unit_contract_sha256": "sha256:" + "c" * 64,
            },
        }
        self.paths = None
        self.helpers = None
        self.admission = None
        self.admission_sha256 = None
        self.formal_artifacts = None
        self.formal_completed_iterations = LAUNCHER.FORMAL_ITERATIONS
        self.formal_status = "ITERATIONS_COMPLETE"
        self.formal_policy_mutation = None
        self.admission_environment_mutation = None
        self.artifact_schedule_drift = False
        self.reader_completion_mutation = None
        self.paper_active = False
        self.paper_active_at = None
        self.helper_drift_at = None
        self.final_evidence_mutation = None
        self.post_verifier_evidence_mutation = None
        self.verified_closure_mutation = None
        self.verified_closure_post_mutation = None
        self.reader_active = True
        self.reader_active_pid = 3002
        self._acknowledged_at_ms = None
        self._ack_artifacts = None
        self._formal_evidence_reads = 0
        self.activation_binding_mutation_at = None
        self.activation_timer_drift_at = None

    def _step(self, name: str) -> str:
        count = self.counts.get(name, 0) + 1
        self.counts[name] = count
        token = name if count == 1 else f"{name}:{count}"
        self.actions.append(token)
        if self.signal_at == token:
            raise LAUNCHER.LauncherSignal(15)
        if self.fail_at == token:
            raise LAUNCHER.LauncherError("INJECTED_" + token.upper().replace(
                ":", "_").replace("-", "_"))
        return token

    def prepare(self, paths) -> None:
        self._step("prepare")
        self.paths = paths

    def helper_hashes(self):
        token = self._step("helper_hashes")
        self.helpers = {
            name: "sha256:" + f"{index:064x}"
            for index, name in enumerate(LAUNCHER.HELPERS, start=1)
        }
        if token == self.helper_drift_at:
            self.helpers["observer_sha256"] = "sha256:" + "f" * 64
        return dict(self.helpers)

    def gateway_identity(self):
        self._step("gateway_identity")
        return dict(self._gateway)

    def activation_binding(self):
        token = self._step("activation_binding")
        result = copy.deepcopy(self._activation)
        if (
                self.activation_binding_mutation_at is not None and
                token == self.activation_binding_mutation_at):
            result["broker"]["tasks_current"] = 2
        if (
                self.activation_timer_drift_at is not None and
                token == self.activation_timer_drift_at):
            result["reconcile_timer"]["unit_file_state"] = "disabled"
        return result

    def launcher_identity(self, unit, pid, configuration):
        self._step("launcher_identity")
        result = {
            "unit": unit,
            "invocation_id": "b" * 32,
            "main_pid": pid,
            "type": "exec",
            "restart": "no",
            "remain_after_exit": "no",
            "user": "root",
            "group": "root",
            "exec_start": LAUNCHER.launcher_command(configuration),
            "environment": dict(LAUNCHER.SANITIZED_ENVIRONMENT),
            "launcher_sha256": self.helpers["launcher_sha256"],
            "conflicts": list(LAUNCHER.PAPER_UNITS),
        }
        return result

    def assert_clean(self):
        self._step("assert_clean")
        return {
            "schema": "hepta.shadow-watch-custodian-status.v1",
            "status": "NO_ACTIVE_TRANSACTION",
            "paper_authorized": False,
            "live_authorized": False,
            "mutation_authorized": False,
            "direct_broker_access": False,
        }

    def assert_paper_inactive(self):
        token = self._step("assert_paper_inactive")
        if self.paper_active or token == self.paper_active_at:
            raise LAUNCHER.LauncherError("FAKE_PAPER_ACTIVE")
        result = {
            unit: {"ActiveState": "inactive", "SubState": "dead"}
            for unit in LAUNCHER.PAPER_UNITS
        }
        return result

    def build_policy(self, mode, configuration, paths):
        self._step(f"build_policy:{mode}")
        campaign = (
            configuration.probe_campaign_id if mode == "load-probe"
            else configuration.formal_campaign_id)
        policy = policy_document(configuration, campaign)
        if mode == "formal" and self.formal_policy_mutation is not None:
            body = dict(policy)
            body.pop("body_sha256")
            self.formal_policy_mutation(body)
            policy = LAUNCHER.seal(body)
        policy_file_sha256 = LAUNCHER.digest_bytes(
            LAUNCHER.canonical_bytes(policy))
        marker_body = {
            "schema": (
                "hepta.p1-shadow-load-probe-authority-marker.v1"
                if mode == "load-probe" else
                "hepta.p1-shadow-admission-authority-marker.v1"),
            "version": 1,
            "status": "ACTIVE",
            "campaign_id": campaign,
            "paper_authorized": False,
            "live_authorized": False,
            "mutation_authorized": False,
            "direct_broker_access": False,
        }
        if mode == "formal":
            marker_body.update({
                "execution_service_epoch": "epoch-1",
                "execution_service_fencing_generation": 7,
                "policy_path": str(paths.formal_policy),
                "policy_file_sha256": policy_file_sha256,
                "policy_body_sha256": policy["body_sha256"],
                "admission_receipt_path": str(paths.admission_receipt),
                "admission_receipt_file_sha256": self.admission_sha256,
                "environment": self.admission["environment"],
            })
        marker = LAUNCHER.seal(marker_body)
        artifacts = LAUNCHER.PolicyArtifacts(
            policy=policy,
            policy_file_sha256=policy_file_sha256,
            marker=marker,
            marker_file_sha256=LAUNCHER.digest_bytes(
                LAUNCHER.canonical_bytes(marker)),
            valid_after_ms=(
                policy.get("valid_after_ms")
                if not self.artifact_schedule_drift else
                policy.get("valid_after_ms", 0) + 1),
            maximum_iterations=policy.get("maximum_iterations"),
        )
        if mode == "formal":
            self.formal_artifacts = artifacts
        return artifacts

    def start_reader(
        self, campaign_id, _unit, _launcher_unit, _policy, _marker, _paths,
        *, formal,
    ):
        self._step("start_reader:formal" if formal else "start_reader:probe")
        expected = (
            self.configuration.formal_campaign_id if formal else
            self.configuration.probe_campaign_id)
        if campaign_id != expected:
            raise LAUNCHER.LauncherError("FAKE_READER_CAMPAIGN_MISMATCH")
        return 3002 if formal else 3001

    def provision(self, campaign_id, owner_pid):
        mode = (
            "formal" if campaign_id == self.configuration.formal_campaign_id
            else "probe")
        self._step(f"provision:{mode}")
        expected_pid = 3002 if mode == "formal" else 3001
        if owner_pid != expected_pid:
            raise LAUNCHER.LauncherError("FAKE_OWNER_PID_MISMATCH")
        generation = (
            self.formal_generation if mode == "formal"
            else self.probe_generation)
        document = {
            "schema": "hepta.shadow-watch-custodian-registration.v1",
            "status": "REGISTERED",
            "campaign_id": campaign_id,
            "lease_generation": generation,
            "paper_authorized": False,
            "live_authorized": False,
            "mutation_authorized": False,
            "direct_broker_access": False,
        }
        return LAUNCHER.Registration(campaign_id, generation, document)

    def start_backstop(self):
        self._step("start_backstop")

    def run_probe_host(
        self, configuration, _paths, _reader_unit, generation, _capture,
    ):
        self._step("run_probe_host")
        close = closure(configuration.probe_campaign_id, generation)
        receipt = LAUNCHER.seal({
            "schema": "hepta.p1-shadow-load-probe-host-receipt.v1",
            "version": 1,
            "status": "LOAD_PROBE_COMPLETE",
            "campaign_id": configuration.probe_campaign_id,
            "collector_runs": 91,
            "close_result": close,
            "paper_authorized": False,
            "live_authorized": False,
            "mutation_authorized": False,
            "direct_broker_access": False,
        })
        return receipt, LAUNCHER.digest_bytes(LAUNCHER.canonical_bytes(receipt))

    def validate_probe(self, configuration, paths):
        self._step("validate_probe")
        now_ms = time.time_ns() // 1_000_000
        environment = {
            **self._gateway,
            **{
                field: self.helpers[field]
                for field in (
                    "collector_sha256", "exporter_sha256",
                    "heptactl_sha256", "gateway_sha256",
                    "custodian_sha256", "observer_sha256",
                    "host_controller_sha256", "domain_config_sha256",
                    "gateway_profile_sha256",
                )
            },
            "boot_id": "00000000-0000-0000-0000-000000000001",
            "audit_journal_device": 20,
            "audit_journal_inode": 21,
        }
        if self.admission_environment_mutation is not None:
            self.admission_environment_mutation(environment)
        digests = {
            field: "sha256:" + f"{index:064x}"
            for index, field in enumerate((
                "host_receipt_body_sha256",
                "observer_controller_status_body_sha256",
                "observer_state_body_sha256", "history_head_body_sha256",
                "probe_first_record_sha256",
                "probe_first_snapshot_body_sha256",
                "probe_last_record_sha256",
                "probe_last_snapshot_body_sha256",
            ), start=40)
        }
        last_record = digests["probe_last_record_sha256"]
        admission = LAUNCHER.seal({
            "schema": "hepta.p1-shadow-load-probe-admission-receipt.v1",
            "version": 1,
            "status": "GO",
            "campaign_id": configuration.probe_campaign_id,
            "prospective_campaign_id": configuration.formal_campaign_id,
            "prospective_policy_path": str(paths.formal_policy),
            "authority_marker_path": str(paths.formal_marker),
            "validated_at_ms": now_ms,
            **digests,
            "sample_count": 91,
            "collection_cadence_ms": 10_000,
            "maximum_collection_jitter_ms": 1_000,
            "missed_sample_count": 0,
            "missed_decision_count": 0,
            "probe_execution_service_epoch": "epoch-1",
            "probe_execution_service_fencing_generation": 7,
            "probe_first_collection_started_at_ms": now_ms - 900_000,
            "probe_first_exported_at_ms": now_ms - 899_900,
            "probe_last_collection_started_at_ms": now_ms - 100,
            "probe_last_exported_at_ms": now_ms - 1,
            "probe_history_record_bytes": 10_000,
            "probe_audit_cursor_sequence": 91,
            "probe_audit_expected_previous_sha256": last_record,
            "environment": environment,
            "paper_authorized": False,
            "live_authorized": False,
            "mutation_authorized": False,
            "direct_broker_access": False,
        })
        self.admission = admission
        self.admission_sha256 = LAUNCHER.digest_bytes(
            LAUNCHER.canonical_bytes(admission))
        return admission, self.admission_sha256

    def run_formal_host(
        self, configuration, paths, reader_unit, generation, _capture,
        policy_artifacts,
    ):
        self._step("run_formal_host")
        if policy_artifacts is not self.formal_artifacts:
            raise LAUNCHER.LauncherError("FAKE_POLICY_ARTIFACT_MISMATCH")
        final_generation = generation + 1
        self._acknowledged_at_ms = time.time_ns() // 1_000_000
        self._ack_artifacts = self._make_final_evidence(paths, post=False)
        result = {
            "schema": "hepta.p1-shadow-host-controller-result.v1",
            "status": self.formal_status,
            "campaign_id": configuration.formal_campaign_id,
            "lease_generation": final_generation,
            "collector_runs": 30_000,
            "completed_iterations": self.formal_completed_iterations,
            "reader_completion": {
                "reader_unit": reader_unit,
                "reader_pid": 3002,
                "acknowledged_at_ms": self._acknowledged_at_ms,
                "controller_status_file_sha256":
                    self._ack_artifacts.controller_status_file_sha256,
                "controller_status_body_sha256":
                    self._ack_artifacts.controller_status["body_sha256"],
                "observer_state_file_sha256":
                    self._ack_artifacts.observer_state_file_sha256,
                "observer_state_body_sha256":
                    self._ack_artifacts.observer_state["body_sha256"],
            },
            "close_result": closure(
                configuration.formal_campaign_id, final_generation),
            "paper_authorized": False,
            "live_authorized": False,
            "mutation_authorized": False,
            "direct_broker_access": False,
        }
        if self.reader_completion_mutation is not None:
            self.reader_completion_mutation(result["reader_completion"])
        return result

    def _make_final_evidence(self, paths, *, post: bool):
        policy = self.formal_artifacts.policy
        final_generation = self.formal_generation + 1
        acknowledged_at_ms = (
            self._acknowledged_at_ms
            if self._acknowledged_at_ms is not None else
            time.time_ns() // 1_000_000)
        status_body = {
            "schema": "hepta.p1-shadow-observer-controller-status.v1",
            "version": 1,
            "campaign_id": self.configuration.formal_campaign_id,
            "controller_pid": 3002,
            "controller_uid": LAUNCHER.READER_UID,
            "controller_gid": LAUNCHER.READER_GID,
            "state": "TERMINAL",
            "started_at_ms": acknowledged_at_ms - 100_000,
            "updated_at_ms": (
                time.time_ns() // 1_000_000 if post else
                acknowledged_at_ms - 1),
            "observer_invocations": 30_000,
            "last_export_receipt_body_sha256": "sha256:" + "1" * 64,
            "last_snapshot_body_sha256": "sha256:" + "2" * 64,
            "last_lease_generation": final_generation,
            "locked_execution_service_epoch": "epoch-1",
            "locked_execution_service_fencing_generation": 7,
            "observer_status": "COMPLETE",
            "observer_outcome": "COMPLETE",
            "completed_iterations": self.formal_completed_iterations,
            "reason": None,
            "paper_authorized": False,
            "live_authorized": False,
            "mutation_attempted": False,
            "direct_broker_access": False,
        }
        audit_events = [{
            "sequence": 1,
            "event": "WATCH_LEASE_ROTATED",
            "at_ms": policy["valid_after_ms"],
            "reason": None,
            "detail": {
                "previous_generation": self.formal_generation,
                "generation": final_generation,
            },
            "paper_authorized": False,
            "live_authorized": False,
            "mutation_attempted": False,
            "direct_broker_access": False,
        }]
        state_body = {
            "schema": "hepta.bounded-shadow-observer-state.v1",
            "version": 1,
            "campaign_id": self.configuration.formal_campaign_id,
            "campaign_sha256": policy["campaign_sha256"],
            "policy_sha256": self.formal_artifacts.policy_file_sha256,
            "policy_body_sha256": policy["body_sha256"],
            "strategy_id": policy["strategy_id"],
            "strategy_version": policy["strategy_version"],
            "strategy_sha256": policy["strategy_sha256"],
            "status": "COMPLETE",
            "collection_cadence_ms": 10_000,
            "maximum_collection_jitter_ms": 1_000,
            "valid_after_ms": policy["valid_after_ms"],
            "expires_at_ms": policy["expires_at_ms"],
            "slot_interval_ms": policy["slot_interval_ms"],
            "maximum_iterations": policy["maximum_iterations"],
            "maximum_lateness_ms": policy["maximum_lateness_ms"],
            "segment_index": 1,
            "segment_status": "OPEN",
            "segment_record_count": 30_000,
            "segment_history_head_sha256": "sha256:" + "3" * 64,
            "last_collection_started_at_ms": policy["expires_at_ms"] - 1,
            "last_generated_at_ms": policy["expires_at_ms"] - 1,
            "last_snapshot_body_sha256": "sha256:" + "2" * 64,
            "last_watch_generation": final_generation,
            "last_lease_receipt_body_sha256": "sha256:" + "4" * 64,
            "last_lease_receipt_file_sha256": "sha256:" + "5" * 64,
            "completed_iterations": self.formal_completed_iterations,
            "last_receipt_sha256": "sha256:" + "6" * 64,
            "missed_sample_count": 0,
            "missed_decision_count": 0,
            "sample_count": 30_000,
            "accounted_payload_bytes": 100_000,
            "accounted_payload_files": 1_000,
            "accounted_payload_accumulator": "sha256:" + "7" * 64,
            "last_storage_audit_sample_count": 30_000,
            "last_storage_audit_accumulator": "sha256:" + "8" * 64,
            "final_audit_receipt_sha256": "sha256:" + "a" * 64,
            "final_audit_segment_count": 1,
            "audit_events": audit_events,
            "paper_authorized": False,
            "live_authorized": False,
            "mutation_attempted": False,
            "direct_broker_access": False,
        }
        if self.final_evidence_mutation is not None:
            self.final_evidence_mutation(status_body, state_body)
        if post and self.post_verifier_evidence_mutation is not None:
            self.post_verifier_evidence_mutation(status_body, state_body)
        status = LAUNCHER.seal(status_body)
        state = LAUNCHER.seal(state_body)
        return LAUNCHER.FinalReaderArtifacts(
            controller_status=status,
            controller_status_file_sha256=LAUNCHER.digest_bytes(
                LAUNCHER.canonical_bytes(status)),
            observer_state=state,
            observer_state_file_sha256=LAUNCHER.digest_bytes(
                LAUNCHER.canonical_bytes(state)),
            final_audit_body_sha256="sha256:" + "3" * 64,
            final_audit_file_sha256=state["final_audit_receipt_sha256"],
        )

    def read_formal_evidence(self, paths):
        self._step("read_formal_evidence")
        self._formal_evidence_reads += 1
        if self._formal_evidence_reads == 1:
            return self._ack_artifacts
        return self._make_final_evidence(paths, post=True)

    def verify_formal_closure(self, paths):
        self._step("verify_formal_closure")
        policy = self.formal_artifacts.policy
        final_artifacts = self._ack_artifacts
        state = final_artifacts.observer_state
        segments = [{
            "segment_index": 1,
            "record_count": state["sample_count"],
            "history_head_sha256": "sha256:" + "b" * 64,
            "source_sha256": "sha256:" + "c" * 64,
            "history_record_bytes": 60_000,
            "history_index_bytes": 40_000,
            "history_storage_bytes": 100_000,
            "audit_sha256": "sha256:" + "d" * 64,
        }]
        digest_fields = (
            LAUNCHER.VERIFIED_ITERATION_FIELDS - {
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
        iterations = []
        for iteration in range(1, LAUNCHER.FORMAL_ITERATIONS + 1):
            scheduled_at_ms = (
                policy["valid_after_ms"] +
                (iteration - 1) * policy["slot_interval_ms"])
            document = {
                "iteration": iteration,
                "segment_index": 1,
                "scheduled_at_ms": scheduled_at_ms,
                "evaluated_at_ms": scheduled_at_ms + 100,
                "source_first_sequence": 1,
                "source_last_sequence": iteration,
                "source_record_count": iteration,
                "source_total_record_count": iteration,
                "source_window_truncated": False,
                "source_predecessor_record_sha256": None,
                "materialization_window_ms": 3_600_000,
                "materialization_maximum_records": 10_000,
                "source_attestation": {
                    field: (
                        True if field == "raw_payloads_verified" else
                        "sha256:" + "e" * 64)
                    for field in LAUNCHER.VERIFIED_SOURCE_ATTESTATION_FIELDS
                },
                "final_outcome": "NO_ACTION",
                "residual_evidence": [
                    "EPHEMERAL_BAR_HISTORY_NOT_RETAINED"],
            }
            document.update({
                field: "sha256:" + "f" * 64
                for field in digest_fields
            })
            iterations.append(document)
        closure_body = {
            "schema": "hepta.bounded-shadow-campaign-closure.v1",
            "version": 1,
            "campaign_id": self.configuration.formal_campaign_id,
            "campaign_sha256": policy["campaign_sha256"],
            "policy_body_sha256": policy["body_sha256"],
            "policy_file_sha256": self.formal_artifacts.policy_file_sha256,
            "strategy_id": policy["strategy_id"],
            "strategy_version": policy["strategy_version"],
            "strategy_sha256": policy["strategy_sha256"],
            "strategy_file_sha256": "sha256:" + "1" * 64,
            "observer_state_body_sha256": state["body_sha256"],
            "observer_state_file_sha256":
                final_artifacts.observer_state_file_sha256,
            "strategy_state_file_sha256": "sha256:" + "2" * 64,
            "final_audit_body_sha256": "sha256:" + "3" * 64,
            "final_audit_file_sha256":
                state["final_audit_receipt_sha256"],
            "verified_at_ms": state["last_generated_at_ms"] + 1,
            "completed_iterations": LAUNCHER.FORMAL_ITERATIONS,
            "maximum_iterations": LAUNCHER.FORMAL_ITERATIONS,
            "segment_count": 1,
            "segments": segments,
            "iteration_count": LAUNCHER.FORMAL_ITERATIONS,
            "iterations": iterations,
            "residual_evidence": [
                "EPHEMERAL_BAR_HISTORY_NOT_RETAINED"],
            "complete_revalidation": False,
            "closure_status":
                "VERIFIED_WITH_RETAINED_EVIDENCE_RESIDUALS",
            "paper_authorized": False,
            "live_authorized": False,
            "mutation_attempted": False,
            "direct_broker_access": False,
        }
        if self.verified_closure_mutation is not None:
            self.verified_closure_mutation(closure_body)
        verified = LAUNCHER.seal(closure_body)
        if self.verified_closure_post_mutation is not None:
            self.verified_closure_post_mutation(verified)
        return LAUNCHER.VerifiedClosureArtifacts(
            closure=verified,
            closure_file_sha256=LAUNCHER.digest_bytes(
                LAUNCHER.canonical_bytes(verified)),
            strategy_file_sha256=closure_body["strategy_file_sha256"],
        )

    def assert_reader_active(self, unit, pid):
        self._step("assert_reader_active")
        if not self.reader_active:
            raise LAUNCHER.LauncherError(
                "P1_LAUNCHER_READER_IDENTITY_INVALID")
        return {
            "unit": unit,
            "active_state": "active",
            "sub_state": "running",
            "main_pid": self.reader_active_pid,
        }

    def stop_unit(self, unit):
        self._step(f"stop_unit:{unit}")

    def close_and_verify(self, _reason):
        self._step("close_and_verify")
        return {
            "schema": "hepta.shadow-watch-custodian-status.v1",
            "status": "NO_ACTIVE_TRANSACTION",
            "paper_authorized": False,
            "live_authorized": False,
            "mutation_authorized": False,
            "direct_broker_access": False,
        }


class LauncherTests(unittest.TestCase):
    def setUp(self) -> None:
        observed_now_ms = time.time_ns() // 1_000_000
        slot_interval_ms = LAUNCHER.POLICY_SLOT_INTERVAL_MS
        warmup_start_ms = (
            (observed_now_ms + LAUNCHER.PROBE_DISPATCH_LEAD_MS +
             slot_interval_ms - 1) // slot_interval_ms
        ) * slot_interval_ms
        self.now_ms = warmup_start_ms - LAUNCHER.PROBE_DISPATCH_LEAD_MS
        self.clock = LaunchClock(self.now_ms)
        self.configuration = LAUNCHER.LaunchConfiguration(
            probe_campaign_id=(
                "hepta-p1-shadow-load-probe-round101-20260731"),
            formal_campaign_id="hepta-p1-shadow-soak-round102-20260731",
            formal_start_ms=warmup_start_ms,
        )

    def launch(self, *, fail_at=None, signal_at=None):
        self.clock = LaunchClock(self.now_ms)
        executor = FakeExecutor(
            self.configuration, fail_at=fail_at, signal_at=signal_at)
        store = FakeStore()
        launcher = LAUNCHER.Launcher(
            self.configuration,
            executor,
            store,
            now_ms=self.now_ms,
            _wall_now_ms=self.clock.wall,
            _monotonic_clock=self.clock.monotonic,
            _sleep=self.clock.sleep,
        )
        return launcher, executor, store

    @staticmethod
    def _predecessor_context(fixture: dict) -> ExitStack:
        stack = ExitStack()
        for name, value in {
            "ROOT_UID": LAUNCHER.os.geteuid(),
            "ROOT_GID": LAUNCHER.os.getegid(),
            "PREDECESSOR_ACTIVATION_FAILED_RECEIPT":
                fixture["receipt_path"],
            "PREDECESSOR_ACTIVATION_FAILED_RECEIPT_FILE_SHA256":
                fixture["receipt_file_sha256"],
            "PREDECESSOR_ACTIVATION_FAILED_RECEIPT_BODY_SHA256":
                fixture["receipt_body_sha256"],
            "PREDECESSOR_ACTIVATION_JOURNAL": fixture["journal_path"],
            "PREDECESSOR_ACTIVATION_JOURNAL_SHA256":
                fixture["journal_sha256"],
        }.items():
            stack.enter_context(mock.patch.object(LAUNCHER, name, value))
        return stack

    @staticmethod
    def _predecessor_success_context(fixture: dict) -> ExitStack:
        stack = ExitStack()
        for name, value in {
            "ROOT_UID": LAUNCHER.os.geteuid(),
            "ROOT_GID": LAUNCHER.os.getegid(),
            "PREDECESSOR_ACTIVATION_SUCCESS_RECEIPT":
                fixture["receipt_path"],
            "PREDECESSOR_ACTIVATION_SUCCESS_RECEIPT_FILE_SHA256":
                fixture["receipt_file_sha256"],
            "PREDECESSOR_ACTIVATION_SUCCESS_RECEIPT_BODY_SHA256":
                fixture["receipt_body_sha256"],
            "PREDECESSOR_ACTIVATION_SUCCESS_JOURNAL_SHA256":
                fixture["journal_sha256"],
        }.items():
            stack.enter_context(mock.patch.object(LAUNCHER, name, value))
        return stack

    def test_predecessor_failure_and_journal_are_exact_and_non_mutating(
            self) -> None:
        with tempfile.TemporaryDirectory(
                dir=str(SCRIPT.parent)) as temporary:
            root = Path(temporary)
            success_fixture = predecessor_success_fixture(root / "success")
            with self._predecessor_success_context(success_fixture):
                success_evidence = LAUNCHER.ProductionExecutor\
                    ._predecessor_activation_success_binding()
                self.assertEqual(
                    LAUNCHER.ProductionExecutor
                    ._predecessor_activation_success_binding(success_evidence),
                    success_evidence)
            fixture = predecessor_fixture(root / "failure")
            before_payload = fixture["receipt_path"].read_bytes()
            before_metadata = fixture["receipt_path"].stat()
            with self._predecessor_context(fixture):
                evidence = LAUNCHER.ProductionExecutor\
                    ._predecessor_activation_failure_binding()
                self.assertEqual(
                    LAUNCHER.ProductionExecutor
                    ._predecessor_activation_failure_binding(evidence),
                    evidence)
                failure_artifacts = tuple(
                    Path(temporary) / name for name in
                    ("new-failed", "new-replacement", "new-pending"))
                with mock.patch.object(
                        LAUNCHER, "ACTIVATION_FAILURE_ARTIFACTS",
                        failure_artifacts), mock.patch.object(
                            LAUNCHER, "LEGACY_ACTIVATION_RECEIPT",
                            Path(temporary) / "legacy-v1"), mock.patch.object(
                            LAUNCHER, "PREDECESSOR_ACTIVATION_RECEIPT",
                            Path(temporary) / "legacy-v2"):
                    LAUNCHER.ProductionExecutor\
                        ._assert_activation_failure_artifacts_absent()
            after_metadata = fixture["receipt_path"].stat()
            self.assertEqual(fixture["receipt_path"].read_bytes(), before_payload)
            self.assertEqual(
                (after_metadata.st_dev, after_metadata.st_ino,
                 after_metadata.st_mode, after_metadata.st_nlink,
                 after_metadata.st_uid, after_metadata.st_gid,
                 after_metadata.st_size, after_metadata.st_mtime_ns,
                 after_metadata.st_ctime_ns),
                (before_metadata.st_dev, before_metadata.st_ino,
                 before_metadata.st_mode, before_metadata.st_nlink,
                 before_metadata.st_uid, before_metadata.st_gid,
                 before_metadata.st_size, before_metadata.st_mtime_ns,
                 before_metadata.st_ctime_ns))

    def test_predecessor_missing_and_metadata_drift_fail_closed(self) -> None:
        with tempfile.TemporaryDirectory(
                dir=str(SCRIPT.parent)) as temporary:
            fixture = predecessor_fixture(Path(temporary))
            with self._predecessor_context(fixture):
                evidence = LAUNCHER.ProductionExecutor\
                    ._predecessor_activation_failure_binding()
                metadata = fixture["receipt_path"].stat()
                LAUNCHER.os.utime(
                    fixture["receipt_path"],
                    ns=(metadata.st_atime_ns, metadata.st_mtime_ns + 1_000_000))
                with self.assertRaisesRegex(
                        LAUNCHER.LauncherError,
                        "P1_LAUNCHER_ACTIVATION_PREDECESSOR_INVALID"):
                    LAUNCHER.ProductionExecutor\
                        ._predecessor_activation_failure_binding(evidence)
                fixture["receipt_path"].unlink()
                with self.assertRaises(LAUNCHER.LauncherError):
                    LAUNCHER.ProductionExecutor\
                        ._predecessor_activation_failure_binding()

    def test_predecessor_forged_receipt_and_journal_fail_closed(self) -> None:
        with tempfile.TemporaryDirectory(
                dir=str(SCRIPT.parent)) as temporary:
            fixture = predecessor_fixture(Path(temporary))
            forged = copy.deepcopy(fixture["receipt"])
            forged.pop("body_sha256")
            forged["status"] = "PENDING_EXPIRY"
            forged = LAUNCHER.seal(forged)
            forged_payload = LAUNCHER.canonical_bytes(forged)
            fixture["receipt_path"].write_bytes(forged_payload)
            fixture["receipt_path"].chmod(0o600)
            forged_fixture = {
                **fixture,
                "receipt_file_sha256": LAUNCHER.digest_bytes(forged_payload),
                "receipt_body_sha256": forged["body_sha256"],
            }
            with self._predecessor_context(forged_fixture), \
                    self.assertRaisesRegex(
                        LAUNCHER.LauncherError,
                        "P1_LAUNCHER_ACTIVATION_PREDECESSOR_INVALID"):
                LAUNCHER.ProductionExecutor\
                    ._predecessor_activation_failure_binding()

            fixture = predecessor_fixture(Path(temporary) / "journal-forgery")
            terminal_path = sorted(fixture["journal_path"].iterdir())[-1]
            terminal = LAUNCHER._decode_document(
                terminal_path.read_bytes(), "TEST_TERMINAL")
            terminal.pop("body_sha256")
            terminal["evidence"] = {"complete": False}
            terminal_payload = LAUNCHER.canonical_bytes(LAUNCHER.seal(terminal))
            terminal_path.write_bytes(terminal_payload)
            terminal_path.chmod(0o600)
            file_sha256s = [
                LAUNCHER.digest_bytes(path.read_bytes())
                for path in sorted(fixture["journal_path"].iterdir())]
            forged_journal_fixture = {
                **fixture,
                "journal_sha256": LAUNCHER.digest_bytes(
                    LAUNCHER.canonical_bytes(file_sha256s)),
            }
            with self._predecessor_context(forged_journal_fixture), \
                    self.assertRaisesRegex(
                        LAUNCHER.LauncherError,
                        "P1_LAUNCHER_ACTIVATION_PREDECESSOR_INVALID"):
                LAUNCHER.ProductionExecutor\
                    ._predecessor_activation_failure_binding()

    def test_successful_order_is_probe_then_formal_and_closed(self) -> None:
        launcher, executor, store = self.launch()
        result = launcher.run()
        self.assertEqual(result["status"], "FORMAL_COMPLETE")
        self.assertEqual(store.receipt, result)
        self.assertEqual(store.state["status"], "STARTING")
        self.assertEqual(
            result["launcher_identity"]["unit"],
            "hepta-p1-shadow-admission-round102.service")
        important = [
            action for action in executor.actions
            if action.startswith((
                "build_policy", "start_reader", "provision",
                "run_probe_host", "validate_probe", "run_formal_host",
                "verify_formal_closure"))
        ]
        self.assertEqual(important, [
            "build_policy:load-probe", "start_reader:probe",
            "provision:probe", "run_probe_host", "validate_probe",
            "build_policy:formal", "start_reader:formal",
            "provision:formal", "run_formal_host",
            "verify_formal_closure",
        ])
        self.assertEqual(result["execution_service_epoch"], "epoch-1")
        self.assertEqual(
            result["execution_service_fencing_generation"], 7)
        self.assertRegex(
            result["formal_verified_closure_file_sha256"],
            r"^sha256:[0-9a-f]{64}$")
        self.assertRegex(
            result["formal_verified_closure_body_sha256"],
            r"^sha256:[0-9a-f]{64}$")
        self.assertRegex(
            result["formal_host_result_sha256"],
            r"^sha256:[0-9a-f]{64}$")
        self.assertEqual(
            set(result["formal_reader_completion"]),
            LAUNCHER.READER_COMPLETION_FIELDS)
        self.assertEqual(
            result["formal_reader_completion"]["reader_pid"], 3002)
        self.assertEqual(
            result["formal_post_verifier_reader_evidence"]["reader_pid"],
            3002)
        self.assertEqual(
            result["formal_reader_completion"]
            ["observer_state_file_sha256"],
            result["formal_post_verifier_reader_evidence"]
            ["observer_state_file_sha256"])
        self.assertEqual(
            result["activation_receipt_file_sha256"],
            executor._activation["activation_receipt_file_sha256"])
        self.assertEqual(
            result["activation_receipt_body_sha256"],
            executor._activation["activation_receipt_body_sha256"])
        self.assertEqual(
            result["activation_broker_epoch"],
            executor._activation["broker"])
        self.assertEqual(
            result["activation_reconcile_timer"],
            executor._activation["reconcile_timer"])
        self.assertFalse(
            result[
                "load_probe_admission_receipt_activation_binding_attested"])
        self.assertEqual(
            [action for action in executor.actions
             if action.startswith("activation_binding")],
            ["activation_binding", "activation_binding:2",
             "activation_binding:3", "activation_binding:4",
             "activation_binding:5"])
        verifier_index = executor.actions.index("verify_formal_closure")
        self.assertGreater(
            executor.actions.index("helper_hashes:6"), verifier_index)
        self.assertGreater(
            executor.actions.index("assert_paper_inactive:6"),
            verifier_index)
        self.assertGreater(
            executor.actions.index("assert_reader_active"), verifier_index)
        self.assertGreater(
            executor.actions.index("read_formal_evidence:2"),
            executor.actions.index("assert_reader_active"))
        self.assertFalse(result["authority_residue"])
        self.assertFalse(result["export_residue"])
        LAUNCHER._reject_permissions(result)

    def test_admission_rejects_extra_environment_field(self) -> None:
        launcher, executor, _store = self.launch()
        executor.admission_environment_mutation = (
            lambda environment: environment.update({"unexpected": "value"}))
        with self.assertRaisesRegex(
                LAUNCHER.LauncherError,
                "P1_LAUNCHER_ADMISSION_INVALID"):
            launcher.run()

    def test_admission_rejects_missing_environment_field(self) -> None:
        launcher, executor, _store = self.launch()
        executor.admission_environment_mutation = (
            lambda environment: environment.pop("boot_id"))
        with self.assertRaisesRegex(
                LAUNCHER.LauncherError,
                "P1_LAUNCHER_ADMISSION_INVALID"):
            launcher.run()

    def test_activation_epoch_drift_is_rejected_at_every_rebind(self) -> None:
        cases = {
            "activation_binding:2":
                "P1_LAUNCHER_ACTIVATION_DRIFT_AFTER_PROBE",
            "activation_binding:3":
                "P1_LAUNCHER_ACTIVATION_DRIFT_BEFORE_FORMAL",
            "activation_binding:4":
                "P1_LAUNCHER_ACTIVATION_DRIFT_BEFORE_COMPLETION",
            "activation_binding:5":
                "P1_LAUNCHER_ACTIVATION_DRIFT_AT_FINAL_COMMIT",
        }
        for token, reason in cases.items():
            with self.subTest(token=token):
                launcher, executor, store = self.launch()
                executor.activation_binding_mutation_at = token
                with self.assertRaisesRegex(
                        LAUNCHER.LauncherError, reason):
                    launcher.run()
                self.assertEqual(store.receipt["status"], "FAILED_CLOSED")
                self.assertFalse(store.receipt["paper_authorized"])
                self.assertFalse(store.receipt["live_authorized"])

    def test_activation_reconcile_timer_drift_is_rejected(self) -> None:
        launcher, executor, store = self.launch()
        executor.activation_timer_drift_at = "activation_binding:2"
        with self.assertRaisesRegex(
                LAUNCHER.LauncherError,
                "P1_LAUNCHER_ACTIVATION_DRIFT_AFTER_PROBE"):
            launcher.run()
        self.assertEqual(store.receipt["status"], "FAILED_CLOSED")
        self.assertFalse(store.receipt["paper_authorized"])
        self.assertFalse(store.receipt["live_authorized"])

    def test_activation_receipt_contract_is_exact_and_fail_closed(self) -> None:
        self.assertEqual(
            (
                LAUNCHER.EXPECTED_SHADOW_INSTALL_GENERATION,
                LAUNCHER.EXPECTED_PREDECESSOR_SHADOW_INSTALL_GENERATION,
                LAUNCHER.SHADOW_INSTALL_FILE_COUNT,
                LAUNCHER
                .EXPECTED_PREDECESSOR_CURRENT_INSTALL_POINTER_FILE_SHA256,
            ),
            (
                22, 21, 128,
                "sha256:2beeb507fcafbbfc2c93d2e4756fddf0"
                "b27e9872733ff97d28af47006461d406",
            ))
        self.assertEqual(
            LAUNCHER.ACTIVATION_WATCH_BOUNDARY_FIELDS,
            ACTIVATION_CONTRACT.WATCH_BOUNDARY_FIELDS)
        self.assertEqual(
            LAUNCHER.ACTIVATION_FAILURE_ARTIFACTS,
            (
                ACTIVATION_CONTRACT.FAILED_RECEIPT_PATH,
                ACTIVATION_CONTRACT.FAILED_RECEIPT_REPLACEMENT_PATH,
                ACTIVATION_CONTRACT.FAILED_RECEIPT_PENDING_ARCHIVE_PATH,
            ))
        executor = FakeExecutor(self.configuration)
        profile_body = {
            field: None
            for field in LAUNCHER.PROFILE_DEPLOYMENT_RECEIPT_FIELDS
            if field != "body_sha256"
        }
        def file_evidence(
            path: Path, sha256: str, size: int, mode: int, inode: int,
        ) -> dict:
            return {
                "path": str(path), "sha256": sha256, "bytes": size,
                "device": 1, "inode": inode,
                "mode": stat.S_IFREG | mode, "nlink": 1,
                "uid": 0, "gid": 0, "mtime_ns": 1, "ctime_ns": 1,
            }
        target = file_evidence(
            LAUNCHER.GATEWAY_PROFILE,
            LAUNCHER.EXPECTED_GATEWAY_PROFILE_SHA256, 736, 0o644, 2)
        profile_body.update({
            "schema": "hepta.p1-watch-profile-deployment-receipt.v8",
            "version": 8,
            "status": "OFFLINE_PASSIVE_WATCH_PROFILE_REATTESTED",
            "round": 114,
            "domain": "alpha",
            "started_at_ms": 100,
            "finished_at_ms": 900,
            "target_path": str(LAUNCHER.GATEWAY_PROFILE),
            "receipt_staging_path":
                str(LAUNCHER.PROFILE_DEPLOYMENT_RECEIPT_STAGING),
            "target_before": target,
            "target_after": copy.deepcopy(target),
            "target_final": copy.deepcopy(target),
            "legacy_receipt": {
                **file_evidence(
                    LAUNCHER.LEGACY_PROFILE_DEPLOYMENT_RECEIPT,
                    LAUNCHER.LEGACY_PROFILE_RECEIPT_FILE_SHA256,
                    LAUNCHER.LEGACY_PROFILE_RECEIPT_BYTES, 0o600, 3),
                "body_sha256": LAUNCHER.LEGACY_PROFILE_RECEIPT_BODY_SHA256,
            },
            "predecessor_profile_receipt": {
                **file_evidence(
                    LAUNCHER.PREDECESSOR_PROFILE_DEPLOYMENT_RECEIPT,
                    LAUNCHER.PREDECESSOR_PROFILE_RECEIPT_FILE_SHA256,
                    LAUNCHER.PREDECESSOR_PROFILE_RECEIPT_BYTES, 0o600, 6),
                "body_sha256":
                    LAUNCHER.PREDECESSOR_PROFILE_RECEIPT_BODY_SHA256,
            },
            "dormant_paper_to_watch_transition_receipt": {
                **file_evidence(
                    LAUNCHER.PROFILE_TRANSITION_RECEIPT,
                    "sha256:" + "7" * 64, 4096, 0o600, 7),
                "body_sha256": "sha256:" + "8" * 64,
            },
            "legacy_backup": file_evidence(
                LAUNCHER.LEGACY_PROFILE_BACKUP,
                LAUNCHER.LEGACY_PROFILE_SHA256,
                LAUNCHER.LEGACY_PROFILE_BYTES, 0o600, 4),
            "legacy_retained_target": file_evidence(
                LAUNCHER.LEGACY_PROFILE_RETAINED_TARGET,
                LAUNCHER.LEGACY_PROFILE_SHA256,
                LAUNCHER.LEGACY_PROFILE_BYTES, 0o644, 5),
            "preflight_before": {}, "preflight_after": {},
            "preflight_final": {},
            "profile_content_changed": False,
            "target_written": False,
            "target_replaced": False,
            "services_started": False,
            "services_stopped": False,
            "services_restarted": False,
            "campaign_launched": False,
            "paper_authorized": False,
            "live_authorized": False,
            "mutation_attempted": False,
            "direct_broker_access": False,
            "activation_receipt_eligible": False,
            "preflight_reusable_for_activation": False,
            "broker_loaded_source_attested": False,
            "broker_deny_all_continuity_attested": False,
            "fresh_activation_transaction_required": True,
            "shadow_install_evidence": shadow_install_evidence(),
        })
        profile_receipt = LAUNCHER.seal(profile_body)
        profile_contents = LAUNCHER.canonical_bytes(profile_receipt)
        boot_id = "00000000-0000-0000-0000-000000000001"
        valid = activation_receipt_document(
            profile_receipt, profile_contents,
            executor._activation["broker"], executor._activation["gateway"],
            boot_id)
        LAUNCHER.ProductionExecutor._validate_activation_receipt(
            valid,
            receipt_contents=LAUNCHER.canonical_bytes(valid),
            profile_receipt=profile_receipt,
            profile_receipt_contents=profile_contents,
            boot_id=boot_id,
            predecessor_activation_success=predecessor_success_evidence(),
            predecessor_activation_failure=predecessor_evidence(),
        )

        for field, replacement in (
                ("path", "/tmp/forged-transition.json"),
                ("sha256", "sha256:" + "z" * 64),
                ("body_sha256", "sha256:" + "z" * 64),
                ("mode", stat.S_IFREG | 0o644),
                ("nlink", 2), ("uid", 1), ("gid", 1), ("bytes", 0)):
            forged_profile = copy.deepcopy(profile_receipt)
            forged_profile[
                "dormant_paper_to_watch_transition_receipt"][field] = (
                    replacement)
            forged_profile = LAUNCHER.seal({
                key: value for key, value in forged_profile.items()
                if key != "body_sha256"})
            with self.subTest(field=field), self.assertRaisesRegex(
                    LAUNCHER.LauncherError,
                    "P1_LAUNCHER_ACTIVATION_RECEIPT_INVALID"):
                LAUNCHER.ProductionExecutor._validate_activation_receipt(
                    valid,
                    receipt_contents=LAUNCHER.canonical_bytes(valid),
                    profile_receipt=forged_profile,
                    profile_receipt_contents=
                        LAUNCHER.canonical_bytes(forged_profile),
                    boot_id=boot_id,
                    predecessor_activation_success=
                        predecessor_success_evidence(),
                    predecessor_activation_failure=predecessor_evidence(),
                )
        missing = copy.deepcopy(profile_receipt)
        del missing["dormant_paper_to_watch_transition_receipt"]
        missing = LAUNCHER.seal({
            key: value for key, value in missing.items()
            if key != "body_sha256"})
        with self.assertRaisesRegex(
                LAUNCHER.LauncherError,
                "P1_LAUNCHER_ACTIVATION_RECEIPT_INVALID"):
            LAUNCHER.ProductionExecutor._validate_activation_receipt(
                valid,
                receipt_contents=LAUNCHER.canonical_bytes(valid),
                profile_receipt=missing,
                profile_receipt_contents=LAUNCHER.canonical_bytes(missing),
                boot_id=boot_id,
                predecessor_activation_success=predecessor_success_evidence(),
                predecessor_activation_failure=predecessor_evidence(),
            )

        mutations = {
            "schema": lambda body: body.__setitem__("schema", "forged"),
            "status": lambda body: body.__setitem__("status", "ACTIVE"),
            "fresh": lambda body: body.__setitem__(
                "fresh_activation_transaction", False),
            "paper": lambda body: body.__setitem__("paper_authorized", True),
            "profile-binding": lambda body: body.__setitem__(
                "profile_deployment_receipt_file_sha256",
                "sha256:" + "0" * 64),
            "install-binding": lambda body: body[
                "shadow_install_evidence"].__setitem__(
                    "closure_sha256", "sha256:" + "0" * 64),
            "broker-after": lambda body: body["broker_after"].pop(
                "process_starttime_ticks"),
            "gateway-after": lambda body: body["gateway_after"].pop(
                "contract_bindings"),
            "runtime-unmask-missing": lambda body: body[
                "systemctl_mutations"].pop(4),
            "runtime-unmask-reordered": lambda body: body[
                "systemctl_mutations"].__setitem__(
                    slice(3, 5),
                    list(reversed(body["systemctl_mutations"][3:5]))),
            "extra": lambda body: body.__setitem__("unexpected", False),
        }
        for name, mutation in mutations.items():
            with self.subTest(name=name):
                body = copy.deepcopy(valid)
                body.pop("body_sha256")
                mutation(body)
                forged = LAUNCHER.seal(body)
                with self.assertRaises(LAUNCHER.LauncherError):
                    LAUNCHER.ProductionExecutor._validate_activation_receipt(
                        forged,
                        receipt_contents=LAUNCHER.canonical_bytes(forged),
                        profile_receipt=profile_receipt,
                        profile_receipt_contents=profile_contents,
                        boot_id=boot_id,
                        predecessor_activation_success=
                            predecessor_success_evidence(),
                        predecessor_activation_failure=predecessor_evidence(),
                    )

    def test_profile_deployer_missing_round114_apis_is_rejected(self) -> None:
        executor = LAUNCHER.ProductionExecutor()
        transition_fields = repr(
            set(LAUNCHER.PROFILE_TRANSITION_RECEIPT_FIELDS))
        preimage_fields = repr(set(LAUNCHER.PROFILE_TRANSITION_PREIMAGE_FIELDS))
        source = "\n".join((
            "from pathlib import Path",
            "ROUND114_RECEIPT_SCHEMA = " +
                repr("hepta.p1-watch-profile-deployment-receipt.v8"),
            "ROUND114_RECEIPT_VERSION = 8",
            "ROUND114_RECEIPT_PATH = Path(" +
                repr(str(LAUNCHER.PROFILE_DEPLOYMENT_RECEIPT)) + ")",
            "ROUND114_TRANSITION_RECEIPT_SCHEMA = " +
                repr(LAUNCHER.PROFILE_TRANSITION_RECEIPT_SCHEMA),
            "ROUND114_TRANSITION_RECEIPT_VERSION = 2",
            "ROUND114_TRANSITION_RECEIPT_PATH = Path(" +
                repr(str(LAUNCHER.PROFILE_TRANSITION_RECEIPT)) + ")",
            "ROUND114_TRANSITION_RECEIPT_FIELDS = frozenset(" +
                transition_fields + ")",
            "ROUND114_TRANSITION_PREIMAGE_SCHEMA = " +
                repr(LAUNCHER.PROFILE_TRANSITION_PREIMAGE_SCHEMA),
            "ROUND114_TRANSITION_PREIMAGE_VERSION = 1",
            "ROUND114_TRANSITION_PREIMAGE_PATH = Path(" +
                repr(str(LAUNCHER.PROFILE_TRANSITION_PREIMAGE)) + ")",
            "ROUND114_TRANSITION_PREIMAGE_FIELDS = frozenset(" +
                preimage_fields + ")",
            "ROUND114_RECEIPT_FIELDS = frozenset(" +
                repr(set(LAUNCHER.PROFILE_DEPLOYMENT_RECEIPT_FIELDS)) + ")",
            "ROUND95_RECEIPT_FILE_SHA256 = " +
                repr(LAUNCHER.PREDECESSOR_PROFILE_RECEIPT_FILE_SHA256),
            "LEGACY_RECEIPT_FILE_SHA256 = " +
                repr(LAUNCHER.LEGACY_PROFILE_RECEIPT_FILE_SHA256),
        )).encode("ascii")
        binding = {"profile_deployer_payload": source}
        with mock.patch.object(
                executor, "_validate_shadow_install_binding",
                return_value={}):
            with self.assertRaisesRegex(
                    LAUNCHER.LauncherError,
                    "P1_LAUNCHER_PROFILE_DEPLOYER_SOURCE_INVALID"):
                executor._load_verified_profile_deployer(binding)
        missing_preimage_field = source.decode("ascii").replace(
            transition_fields,
            repr(set(LAUNCHER.PROFILE_TRANSITION_RECEIPT_FIELDS) -
                 {"preimage_evidence"})) + "\n" + "\n".join((
                     "def validate_round114_receipt(*_args): return ({}, '')",
                     "def validate_round114_receipt_state_binding(*_args): pass",
                 ))
        binding = {
            "profile_deployer_payload": missing_preimage_field.encode("ascii")}
        with mock.patch.object(
                executor, "_validate_shadow_install_binding",
                return_value={}):
            with self.assertRaisesRegex(
                    LAUNCHER.LauncherError,
                    "P1_LAUNCHER_PROFILE_DEPLOYER_SOURCE_INVALID"):
                executor._load_verified_profile_deployer(binding)

    def test_live_reconcile_timer_attestation_is_exact_and_rebound(self) -> None:
        good = (
            "LoadState=loaded\n"
            "ActiveState=active\n"
            "SubState=waiting\n"
            "Job=\n"
            "UnitFileState=enabled\n")
        disabled = good.replace(
            "UnitFileState=enabled", "UnitFileState=disabled")
        executor = LAUNCHER.ProductionExecutor()

        def completed(payload: str) -> subprocess.CompletedProcess[str]:
            return subprocess.CompletedProcess(
                args=[], returncode=0, stdout=payload, stderr="")
        with mock.patch.object(
                executor, "_run",
                side_effect=[completed(good), completed(good)]):
            evidence = executor._reconcile_timer_evidence()
        self.assertEqual(evidence["unit"], LAUNCHER.ACTIVATION_RECONCILE_TIMER)
        self.assertEqual(evidence["unit_file_state"], "enabled")
        self.assertRegex(
            evidence["unit_contract_sha256"], r"^sha256:[0-9a-f]{64}$")
        with mock.patch.object(
                executor, "_run",
                side_effect=[completed(good), completed(disabled)]):
            with self.assertRaisesRegex(
                    LAUNCHER.LauncherError,
                    "P1_LAUNCHER_ACTIVATION_RECONCILE_TIMER_INVALID"):
                executor._reconcile_timer_evidence()

    def test_admission_reopens_actual_profile_artifacts_and_rejects_drift(
            self) -> None:
        executor = LAUNCHER.ProductionExecutor()
        with tempfile.TemporaryDirectory(dir=ROOT) as temporary:
            root = Path(temporary)
            receipt_path = root / "round114.json"
            backup_path = root / "alpha.env.backup"
            receipt_payload = b"round114-receipt\n"
            backup_payload = b"legacy-profile\n"
            receipt_path.write_bytes(receipt_payload)
            receipt_path.chmod(0o600)
            backup_path.write_bytes(backup_payload)
            backup_path.chmod(0o600)
            evidence = shadow_install_evidence()
            document = {
                "schema": "hepta.p1-watch-profile-deployment-receipt.v8",
                "version": 8,
                "round": 114,
                "shadow_install_evidence": copy.deepcopy(evidence),
            }

            def snapshot(path: Path) -> SimpleNamespace:
                return SimpleNamespace(
                    payload=path.read_bytes(), metadata=path.stat())

            def stable_identity(metadata: os.stat_result) -> tuple[int, ...]:
                return (
                    metadata.st_dev, metadata.st_ino, metadata.st_mode,
                    metadata.st_nlink, metadata.st_uid, metadata.st_gid,
                    metadata.st_size, metadata.st_mtime_ns,
                    metadata.st_ctime_ns,
                )

            artifacts = SimpleNamespace(backup=snapshot(backup_path))

            def read_anchored_file(
                path: Path, _reason: str,
            ) -> SimpleNamespace:
                self.assertEqual(path, receipt_path)
                return snapshot(path)

            def validate_round114_receipt(
                candidate: SimpleNamespace,
                expected_evidence: dict,
            ) -> tuple[dict, str]:
                self.assertEqual(candidate.payload, receipt_payload)
                self.assertEqual(expected_evidence, evidence)
                return document, LAUNCHER.digest_bytes(candidate.payload)

            def read_rebind_artifacts(expected_sha256: str) -> SimpleNamespace:
                self.assertEqual(
                    expected_sha256,
                    LAUNCHER.PREDECESSOR_PROFILE_RECEIPT_FILE_SHA256)
                return artifacts

            def require_rebind_artifacts_unchanged(
                candidate: SimpleNamespace,
                expected_sha256: str,
            ) -> SimpleNamespace:
                self.assertIs(candidate, artifacts)
                self.assertEqual(
                    expected_sha256,
                    LAUNCHER.PREDECESSOR_PROFILE_RECEIPT_FILE_SHA256)
                current = snapshot(backup_path)
                if (
                        current.payload != candidate.backup.payload or
                        stable_identity(current.metadata) !=
                        stable_identity(candidate.backup.metadata)):
                    raise RuntimeError("profile predecessor rebound")
                return candidate

            profile_module = SimpleNamespace(
                read_anchored_file=read_anchored_file,
                validate_round114_receipt=validate_round114_receipt,
                read_rebind_artifacts=read_rebind_artifacts,
                validate_round114_receipt_state_binding=lambda *_args: None,
                require_rebind_artifacts_unchanged=
                    require_rebind_artifacts_unchanged,
                stable_identity=stable_identity,
            )
            with ExitStack() as stack:
                stack.enter_context(mock.patch.object(
                    LAUNCHER, "PROFILE_DEPLOYMENT_RECEIPT", receipt_path))
                shadow_binding = object()
                stack.enter_context(mock.patch.object(
                    executor, "_validate_shadow_install_binding",
                    return_value=evidence))
                stack.enter_context(mock.patch.object(
                    executor, "_load_verified_profile_deployer",
                    return_value=profile_module))
                binding = executor._acquire_profile_artifact_binding(
                    shadow_binding, document, receipt_payload)
                executor._validate_profile_artifact_binding(
                    shadow_binding, binding)
                replacement = root / "replacement"
                replacement.write_bytes(backup_payload)
                replacement.chmod(0o600)
                replacement.replace(backup_path)
                with self.assertRaisesRegex(
                        LAUNCHER.LauncherError,
                        "P1_LAUNCHER_PROFILE_ARTIFACT_REBOUND"):
                    executor._validate_profile_artifact_binding(
                        shadow_binding, binding)

    def test_activation_binding_rejects_failed_receipt_in_final_window(
            self) -> None:
        executor = LAUNCHER.ProductionExecutor()
        broker = {"unit_contract_sha256": "sha256:" + "1" * 64}
        gateway = {"unit_contract_sha256": "sha256:" + "2" * 64}
        timer = {"unit_contract_sha256": "sha256:" + "3" * 64}
        receipt = {
            "body_sha256": "sha256:" + "4" * 64,
            "started_at_ms": 1000,
            "completed_at_ms": 2000,
            "broker_after": broker,
            "gateway_after": gateway,
            "reconcile_timer": timer,
        }
        profile = {
            "body_sha256": "sha256:" + "5" * 64,
            "shadow_install_evidence": shadow_install_evidence(),
        }
        reads = [
            (receipt, b"activation\n"), (profile, b"profile\n"),
            (receipt, b"activation\n"), (profile, b"profile\n"),
            (receipt, b"activation\n"), (profile, b"profile\n"),
        ]
        binding = object()
        profile_binding = object()
        acquire_binding = mock.Mock(return_value=binding)
        validate_binding = mock.Mock(
            return_value=profile["shadow_install_evidence"])
        release_binding = mock.Mock()
        with mock.patch.object(
                executor, "_assert_activation_failure_artifacts_absent",
                side_effect=[
                    None,
                    None,
                    LAUNCHER.LauncherError(
                        "P1_LAUNCHER_ACTIVATION_FAILED_RECEIPT_PRESENT"),
                ]), mock.patch.object(
                    executor, "_predecessor_activation_success_binding",
                    return_value=predecessor_success_evidence()), \
                mock.patch.object(
                    executor, "_predecessor_activation_failure_binding",
                    return_value=predecessor_evidence()), mock.patch.object(
                    executor, "_read_anchored_root_document",
                    side_effect=reads), mock.patch.object(
                    executor, "_current_boot_id",
                    return_value="00000000-0000-0000-0000-000000000001"), \
                mock.patch.object(
                    executor, "_validate_activation_receipt"), \
                mock.patch.object(
                    executor, "_reconcile_timer_evidence",
                    return_value=timer), \
                mock.patch.object(
                    executor, "_broker_activation_evidence",
                    return_value={}), \
                mock.patch.object(
                    executor, "_gateway_activation_evidence",
                    return_value={}), \
                mock.patch.object(
                    executor, "_activation_unit_contract_sha256",
                    side_effect=[broker["unit_contract_sha256"],
                                 gateway["unit_contract_sha256"]]), \
                mock.patch.object(
                    executor, "_acquire_shadow_install_binding",
                    acquire_binding), mock.patch.object(
                    executor, "_validate_shadow_install_binding",
                    validate_binding), mock.patch.object(
                    executor, "_release_shadow_install_binding",
                    release_binding), \
                mock.patch.object(
                    executor, "_acquire_profile_artifact_binding",
                    return_value=profile_binding), \
                mock.patch.object(
                    executor, "_validate_profile_artifact_binding"), \
                self.assertRaisesRegex(
                    LAUNCHER.LauncherError,
                    "P1_LAUNCHER_ACTIVATION_FAILED_RECEIPT_PRESENT"):
            executor.activation_binding()
        acquire_binding.assert_called_once_with(
            profile["shadow_install_evidence"])
        self.assertGreaterEqual(validate_binding.call_count, 3)
        release_binding.assert_called_once_with(binding)

    def test_activation_binding_install_guard_acquire_failure_is_closed(
            self) -> None:
        executor = LAUNCHER.ProductionExecutor()
        receipt, profile, _broker, _gateway, _timer = (
            live_binding_documents())
        reader = mock.Mock(side_effect=[
            (receipt, b"activation\n"), (profile, b"profile\n")])
        release_binding = mock.Mock()
        with ExitStack() as stack:
            stack.enter_context(mock.patch.object(
                executor, "_assert_activation_failure_artifacts_absent"))
            stack.enter_context(mock.patch.object(
                executor, "_predecessor_activation_success_binding",
                return_value=predecessor_success_evidence()))
            stack.enter_context(mock.patch.object(
                executor, "_predecessor_activation_failure_binding",
                return_value=predecessor_evidence()))
            stack.enter_context(mock.patch.object(
                executor, "_read_anchored_root_document", reader))
            stack.enter_context(mock.patch.object(
                executor, "_current_boot_id",
                return_value="00000000-0000-0000-0000-000000000001"))
            stack.enter_context(mock.patch.object(
                executor, "_validate_activation_receipt"))
            stack.enter_context(mock.patch.object(
                executor, "_acquire_shadow_install_binding",
                side_effect=LAUNCHER.LauncherError(
                    "P1_LAUNCHER_SHADOW_INSTALL_INVALID")))
            stack.enter_context(mock.patch.object(
                executor, "_release_shadow_install_binding",
                release_binding))
            with self.assertRaisesRegex(
                    LAUNCHER.LauncherError,
                    "P1_LAUNCHER_SHADOW_INSTALL_INVALID"):
                executor.activation_binding()
        self.assertEqual(reader.call_count, 2)
        release_binding.assert_not_called()

    def test_activation_binding_install_guard_precedes_live_windows(
            self) -> None:
        executor = LAUNCHER.ProductionExecutor()
        receipt, profile, broker, gateway, timer = live_binding_documents()
        read_values = [
            (receipt, b"activation\n"), (profile, b"profile\n"),
            (receipt, b"activation\n"), (profile, b"profile\n"),
            (receipt, b"activation\n"), (profile, b"profile\n"),
        ]
        binding = object()
        profile_binding = object()
        held = False
        reads = 0
        failure_windows = 0
        boot_samples = 0
        timer_samples = 0
        events: list[str] = []

        def assert_failure_absent() -> None:
            nonlocal failure_windows
            failure_windows += 1
            if failure_windows > 1:
                self.assertTrue(held)
            events.append(f"failure-{failure_windows}")

        def read_document(*_args, **_kwargs):
            nonlocal reads
            reads += 1
            if reads > 2:
                self.assertTrue(held)
            events.append(f"read-{reads}")
            return read_values[reads - 1]

        def current_boot_id() -> str:
            nonlocal boot_samples
            boot_samples += 1
            if boot_samples > 1:
                self.assertTrue(held)
            return "00000000-0000-0000-0000-000000000001"

        def acquire(expected: dict) -> object:
            nonlocal held
            self.assertEqual(expected, profile["shadow_install_evidence"])
            self.assertFalse(held)
            held = True
            events.append("acquire")
            return binding

        def validate(candidate: object) -> dict:
            self.assertIs(candidate, binding)
            self.assertTrue(held)
            events.append("validate")
            return profile["shadow_install_evidence"]

        def timer_evidence() -> dict:
            nonlocal timer_samples
            timer_samples += 1
            self.assertTrue(held)
            events.append(f"timer-{timer_samples}")
            return copy.deepcopy(timer)

        def contract(unit: str) -> str:
            self.assertTrue(held)
            return (
                broker["unit_contract_sha256"]
                if unit == LAUNCHER.BROKER_EGRESS_UNIT else
                gateway["unit_contract_sha256"])

        def release(candidate: object) -> None:
            nonlocal held
            self.assertIs(candidate, binding)
            self.assertTrue(held)
            events.append("release")
            held = False

        def acquire_profile(
            shadow_binding: object,
            _profile: dict,
            _contents: bytes,
        ) -> object:
            self.assertIs(shadow_binding, binding)
            self.assertTrue(held)
            events.append("profile-acquire")
            return profile_binding

        def validate_profile(
            shadow_binding: object,
            candidate: object,
        ) -> None:
            self.assertIs(shadow_binding, binding)
            self.assertIs(candidate, profile_binding)
            self.assertTrue(held)
            events.append("profile-validate")

        with ExitStack() as stack:
            for name, replacement in (
                ("_assert_activation_failure_artifacts_absent",
                 assert_failure_absent),
                ("_predecessor_activation_success_binding",
                 lambda _expected=None: predecessor_success_evidence()),
                ("_predecessor_activation_failure_binding",
                 lambda _expected=None: predecessor_evidence()),
                ("_read_anchored_root_document", read_document),
                ("_current_boot_id", current_boot_id),
                ("_validate_activation_receipt", lambda *_a, **_k: None),
                ("_acquire_shadow_install_binding", acquire),
                ("_validate_shadow_install_binding", validate),
                ("_release_shadow_install_binding", release),
                ("_acquire_profile_artifact_binding", acquire_profile),
                ("_validate_profile_artifact_binding", validate_profile),
                ("_reconcile_timer_evidence", timer_evidence),
                ("_broker_activation_evidence", lambda: {}),
                ("_gateway_activation_evidence", lambda: {}),
                ("_activation_unit_contract_sha256", contract),
            ):
                stack.enter_context(mock.patch.object(
                    executor, name, replacement))
            result = executor.activation_binding()
        self.assertEqual(
            result["activation_receipt_body_sha256"],
            receipt["body_sha256"])
        self.assertEqual(reads, 6)
        self.assertEqual(failure_windows, 3)
        self.assertEqual(timer_samples, 2)
        self.assertLess(events.index("acquire"), events.index("failure-2"))
        self.assertLess(events.index("failure-2"), events.index("timer-1"))
        self.assertLess(events.index("timer-2"), events.index("failure-3"))
        self.assertLess(events.index("failure-3"), events.index("release"))
        self.assertFalse(held)

    def test_activation_binding_install_guard_final_rebind_fails_closed(
            self) -> None:
        executor = LAUNCHER.ProductionExecutor()
        receipt, profile, broker, gateway, timer = live_binding_documents()
        read_values = [
            (receipt, b"activation\n"), (profile, b"profile\n"),
            (receipt, b"activation\n"), (profile, b"profile\n"),
            (receipt, b"activation\n"), (profile, b"profile\n"),
        ]
        binding = object()
        profile_binding = object()
        held = False
        validations = 0
        failure_windows = 0
        timer_samples = 0
        release_called = False

        def acquire(_expected: dict) -> object:
            nonlocal held
            held = True
            return binding

        def validate(candidate: object) -> dict:
            nonlocal validations
            self.assertIs(candidate, binding)
            self.assertTrue(held)
            validations += 1
            if validations == 3:
                raise LAUNCHER.LauncherError(
                    "P1_LAUNCHER_SHADOW_INSTALL_REBOUND")
            return profile["shadow_install_evidence"]

        def release(candidate: object) -> None:
            nonlocal held, release_called
            self.assertIs(candidate, binding)
            self.assertTrue(held)
            held = False
            release_called = True

        def failure_absent() -> None:
            nonlocal failure_windows
            failure_windows += 1

        def timer_evidence() -> dict:
            nonlocal timer_samples
            timer_samples += 1
            self.assertTrue(held)
            return copy.deepcopy(timer)

        with ExitStack() as stack:
            stack.enter_context(mock.patch.object(
                executor, "_assert_activation_failure_artifacts_absent",
                failure_absent))
            stack.enter_context(mock.patch.object(
                executor, "_predecessor_activation_success_binding",
                return_value=predecessor_success_evidence()))
            stack.enter_context(mock.patch.object(
                executor, "_predecessor_activation_failure_binding",
                return_value=predecessor_evidence()))
            reader = stack.enter_context(mock.patch.object(
                executor, "_read_anchored_root_document",
                side_effect=read_values))
            stack.enter_context(mock.patch.object(
                executor, "_current_boot_id",
                return_value="00000000-0000-0000-0000-000000000001"))
            stack.enter_context(mock.patch.object(
                executor, "_validate_activation_receipt"))
            stack.enter_context(mock.patch.object(
                executor, "_acquire_shadow_install_binding", acquire))
            stack.enter_context(mock.patch.object(
                executor, "_validate_shadow_install_binding", validate))
            stack.enter_context(mock.patch.object(
                executor, "_release_shadow_install_binding", release))
            stack.enter_context(mock.patch.object(
                executor, "_acquire_profile_artifact_binding",
                return_value=profile_binding))
            stack.enter_context(mock.patch.object(
                executor, "_validate_profile_artifact_binding"))
            stack.enter_context(mock.patch.object(
                executor, "_reconcile_timer_evidence", timer_evidence))
            stack.enter_context(mock.patch.object(
                executor, "_broker_activation_evidence", return_value={}))
            stack.enter_context(mock.patch.object(
                executor, "_gateway_activation_evidence", return_value={}))
            stack.enter_context(mock.patch.object(
                executor, "_activation_unit_contract_sha256",
                side_effect=[broker["unit_contract_sha256"],
                             gateway["unit_contract_sha256"]]))
            with self.assertRaisesRegex(
                    LAUNCHER.LauncherError,
                    "P1_LAUNCHER_SHADOW_INSTALL_REBOUND"):
                executor.activation_binding()
        self.assertEqual(reader.call_count, 6)
        self.assertEqual(timer_samples, 2)
        self.assertEqual(failure_windows, 2)
        self.assertGreaterEqual(validations, 4)
        self.assertTrue(release_called)
        self.assertFalse(held)

    def test_activation_failure_artifacts_all_poison_both_windows(
            self) -> None:
        with tempfile.TemporaryDirectory(
                dir=str(SCRIPT.parent)) as temporary:
            root = Path(temporary)
            artifacts = tuple(root / name for name in (
                "failed.json", ".failed.replacement", "pending.json"))
            legacy_v1 = root / "legacy-v1.json"
            legacy_v2 = root / "legacy-v2.json"
            with mock.patch.object(
                    LAUNCHER, "ACTIVATION_FAILURE_ARTIFACTS", artifacts), \
                    mock.patch.object(
                        LAUNCHER, "LEGACY_ACTIVATION_RECEIPT", legacy_v1), \
                    mock.patch.object(
                        LAUNCHER, "PREDECESSOR_ACTIVATION_RECEIPT", legacy_v2):
                LAUNCHER.ProductionExecutor\
                    ._assert_activation_failure_artifacts_absent()
                for artifact in artifacts:
                    for entity in ("file", "directory", "symlink"):
                        with self.subTest(
                                artifact=artifact.name, entity=entity):
                            if entity == "file":
                                artifact.write_bytes(b"poison\n")
                            elif entity == "directory":
                                artifact.mkdir()
                            else:
                                artifact.symlink_to("missing-target")
                            with self.assertRaisesRegex(
                                    LAUNCHER.LauncherError,
                                    "P1_LAUNCHER_ACTIVATION_"
                                    "FAILED_RECEIPT_PRESENT"):
                                LAUNCHER.ProductionExecutor\
                                    ._assert_activation_failure_artifacts_absent()
                            if entity == "directory":
                                artifact.rmdir()
                            else:
                                artifact.unlink()

            actual = root / "actual"
            actual.mkdir()
            linked = root / "linked"
            linked.symlink_to(actual, target_is_directory=True)
            linked_artifacts = tuple(linked / name for name in (
                "failed.json", ".failed.replacement", "pending.json"))
            with mock.patch.object(
                    LAUNCHER, "ACTIVATION_FAILURE_ARTIFACTS",
                    linked_artifacts), mock.patch.object(
                        LAUNCHER, "LEGACY_ACTIVATION_RECEIPT", legacy_v1), \
                    mock.patch.object(
                        LAUNCHER, "PREDECESSOR_ACTIVATION_RECEIPT", legacy_v2), \
                    self.assertRaisesRegex(
                        LAUNCHER.LauncherError,
                        "P1_LAUNCHER_ACTIVATION_FAILED_RECEIPT_PRESENT"):
                LAUNCHER.ProductionExecutor\
                    ._assert_activation_failure_artifacts_absent()

    def test_legacy_v1_activation_receipt_poison_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory(
                dir=str(SCRIPT.parent)) as temporary:
            legacy = Path(temporary) / "p1-watch-activation-receipt-v1.json"
            legacy_v2 = Path(temporary) / "p1-watch-activation-receipt-v2.json"
            with mock.patch.object(
                    LAUNCHER, "LEGACY_ACTIVATION_RECEIPT", legacy), \
                    mock.patch.object(
                        LAUNCHER, "PREDECESSOR_ACTIVATION_RECEIPT", legacy_v2), \
                    mock.patch.object(
                        LAUNCHER, "ACTIVATION_FAILURE_ARTIFACTS", ()):
                LAUNCHER.ProductionExecutor\
                    ._assert_activation_failure_artifacts_absent()
                for entity in ("file", "directory", "symlink"):
                    with self.subTest(entity=entity):
                        if entity == "file":
                            legacy.write_bytes(b"legacy\n")
                        elif entity == "directory":
                            legacy.mkdir()
                        else:
                            legacy.symlink_to("missing-target")
                        with self.assertRaisesRegex(
                                LAUNCHER.LauncherError,
                                "P1_LAUNCHER_ACTIVATION_"
                                "FAILED_RECEIPT_PRESENT"):
                            LAUNCHER.ProductionExecutor\
                                ._assert_activation_failure_artifacts_absent()
                        if entity == "directory":
                            legacy.rmdir()
                        else:
                            legacy.unlink()

    def test_legacy_v2_activation_receipt_poison_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory(
                dir=str(SCRIPT.parent)) as temporary:
            root = Path(temporary)
            legacy_v1 = root / "p1-watch-activation-receipt-v1.json"
            legacy_v2 = root / "p1-watch-activation-receipt-v2.json"
            with mock.patch.object(
                    LAUNCHER, "LEGACY_ACTIVATION_RECEIPT", legacy_v1), \
                    mock.patch.object(
                        LAUNCHER, "PREDECESSOR_ACTIVATION_RECEIPT", legacy_v2), \
                    mock.patch.object(
                        LAUNCHER, "ACTIVATION_FAILURE_ARTIFACTS", ()):
                LAUNCHER.ProductionExecutor\
                    ._assert_activation_failure_artifacts_absent()
                legacy_v2.write_bytes(b"legacy\n")
                with self.assertRaisesRegex(
                        LAUNCHER.LauncherError,
                        "P1_LAUNCHER_ACTIVATION_FAILED_RECEIPT_PRESENT"):
                    LAUNCHER.ProductionExecutor\
                        ._assert_activation_failure_artifacts_absent()
    def test_anchored_activation_receipt_rejects_entity_and_rebind_seams(
            self) -> None:
        document = LAUNCHER.seal({
            "schema": "fixture.v1",
            "paper_authorized": False,
            "live_authorized": False,
            "mutation_attempted": False,
            "direct_broker_access": False,
        })
        payload = LAUNCHER.canonical_bytes(document)
        with tempfile.TemporaryDirectory(
                dir=str(SCRIPT.parent)) as temporary:
            root = Path(temporary)
            parent = root / "receipts"
            parent.mkdir(mode=0o700)
            target = parent / "activation.json"
            target.write_bytes(payload)
            target.chmod(0o600)
            identity = {
                "ROOT_UID": LAUNCHER.os.getuid(),
                "ROOT_GID": LAUNCHER.os.getgid(),
            }
            with mock.patch.multiple(LAUNCHER, **identity):
                value, contents = (
                    LAUNCHER.ProductionExecutor
                    ._read_anchored_root_document(
                        target, "ACTIVATION_FIXTURE", mode=0o600))
            self.assertEqual(value, document)
            self.assertEqual(contents, payload)

            failed = parent / "failed.json"
            with mock.patch.multiple(LAUNCHER, **identity):
                LAUNCHER.ProductionExecutor._assert_anchored_path_absent(
                    failed, "FAILED_RECEIPT_PRESENT")
            failed.write_bytes(payload)
            failed.chmod(0o600)
            with mock.patch.multiple(LAUNCHER, **identity), \
                    self.assertRaisesRegex(
                        LAUNCHER.LauncherError,
                        "FAILED_RECEIPT_PRESENT"):
                LAUNCHER.ProductionExecutor._assert_anchored_path_absent(
                    failed, "FAILED_RECEIPT_PRESENT")
            failed.unlink()

            for case in ("mode", "hardlink", "symlink"):
                with self.subTest(case=case):
                    target.unlink()
                    target.write_bytes(payload)
                    target.chmod(0o600)
                    sibling = parent / "sibling"
                    if sibling.exists() or sibling.is_symlink():
                        sibling.unlink()
                    if case == "mode":
                        target.chmod(0o644)
                    elif case == "hardlink":
                        sibling.hardlink_to(target)
                    else:
                        target.unlink()
                        sibling.write_bytes(payload)
                        target.symlink_to(sibling.name)
                    with mock.patch.multiple(LAUNCHER, **identity), \
                            self.assertRaises(LAUNCHER.LauncherError):
                        (LAUNCHER.ProductionExecutor
                         ._read_anchored_root_document(
                             target, "ACTIVATION_FIXTURE", mode=0o600))
                    if sibling.exists() or sibling.is_symlink():
                        sibling.unlink()

            target.unlink(missing_ok=True)
            target.write_bytes(payload)
            target.chmod(0o600)
            replacement = parent / "replacement"
            replacement.write_bytes(payload)
            replacement.chmod(0o600)
            real_read = LAUNCHER.os.read
            swapped = False

            def swap_after_read(descriptor, maximum):
                nonlocal swapped
                chunk = real_read(descriptor, maximum)
                if chunk and not swapped:
                    swapped = True
                    replacement.replace(target)
                return chunk

            with mock.patch.multiple(LAUNCHER, **identity), \
                    mock.patch.object(
                        LAUNCHER.os, "read", side_effect=swap_after_read), \
                    self.assertRaises(LAUNCHER.LauncherError):
                (LAUNCHER.ProductionExecutor
                 ._read_anchored_root_document(
                     target, "ACTIVATION_FIXTURE", mode=0o600))

            actual = root / "actual"
            actual.mkdir(mode=0o700)
            linked = root / "linked"
            linked.symlink_to(actual, target_is_directory=True)
            linked_target = linked / "activation.json"
            (actual / "activation.json").write_bytes(payload)
            (actual / "activation.json").chmod(0o600)
            with mock.patch.multiple(LAUNCHER, **identity), \
                    self.assertRaises(LAUNCHER.LauncherError):
                (LAUNCHER.ProductionExecutor
                 ._read_anchored_root_document(
                     linked_target, "ACTIVATION_FIXTURE", mode=0o600))

    def test_every_main_failure_point_writes_failed_closed_receipt(self) -> None:
        points = [
            "prepare", "helper_hashes", "activation_binding",
            "gateway_identity", "assert_clean", "launcher_identity",
            "assert_paper_inactive",
            "start_backstop", "build_policy:load-probe", "start_reader:probe",
            "provision:probe", "start_backstop:2", "run_probe_host",
            "close_and_verify", "helper_hashes:2",
            "assert_paper_inactive:2", "validate_probe",
            "gateway_identity:2", "activation_binding:2",
            "build_policy:formal",
            "helper_hashes:3", "assert_paper_inactive:3",
            "gateway_identity:3", "activation_binding:3",
            "start_reader:formal", "provision:formal",
            "helper_hashes:4", "assert_paper_inactive:4",
            "start_backstop:3", "run_formal_host", "helper_hashes:5",
            "assert_paper_inactive:5", "read_formal_evidence",
            "verify_formal_closure", "helper_hashes:6",
            "assert_paper_inactive:6", "assert_reader_active",
            "read_formal_evidence:2", "activation_binding:4",
            "activation_binding:5",
        ]
        for point in points:
            with self.subTest(point=point):
                launcher, executor, store = self.launch(fail_at=point)
                with self.assertRaises(LAUNCHER.LauncherError):
                    launcher.run()
                self.assertIsNotNone(store.receipt)
                self.assertEqual(store.receipt["status"], "FAILED_CLOSED")
                self.assertFalse(store.receipt["authority_residue"])
                self.assertFalse(store.receipt["export_residue"])
                LAUNCHER._reject_permissions(store.receipt)
                self.assertTrue(any(
                    action.startswith("stop_unit:")
                    for action in executor.actions))
                self.assertTrue(
                    "close_and_verify" in executor.actions or
                    "assert_clean:2" in executor.actions or
                    "assert_clean" in executor.actions)

    def test_signal_during_formal_host_closes_and_records_failure(self) -> None:
        launcher, executor, store = self.launch(
            signal_at="run_formal_host")
        with self.assertRaises(LAUNCHER.LauncherError):
            launcher.run()
        self.assertEqual(store.receipt["status"], "FAILED_CLOSED")
        self.assertEqual(store.receipt["reason"], "P1_LAUNCHER_SIGNAL_15")
        self.assertIn("close_and_verify:2", executor.actions)

    def test_all_terminal_signal_handlers_latch_during_cleanup(self) -> None:
        for signum in (
                LAUNCHER.signal.SIGINT,
                LAUNCHER.signal.SIGTERM,
                LAUNCHER.signal.SIGHUP):
            with self.subTest(signum=signum), \
                    mock.patch.object(
                        LAUNCHER.signal, "getsignal",
                        return_value=LAUNCHER.signal.SIG_DFL), \
                    mock.patch.object(LAUNCHER.signal, "signal") as install:
                previous = LAUNCHER._install_signal_handlers()
                handler = next(
                    call.args[1] for call in install.call_args_list
                    if call.args[0] == signum)
                with self.assertRaises(LAUNCHER.LauncherSignal) as raised:
                    handler(signum, None)
                self.assertEqual(raised.exception.signum, signum)
                self.assertIsNone(handler(signum, None))
                self.assertEqual(
                    set(previous), {
                        LAUNCHER.signal.SIGINT,
                        LAUNCHER.signal.SIGTERM,
                        LAUNCHER.signal.SIGHUP,
                    })

    def test_cleanup_failure_is_not_reported_clean(self) -> None:
        launcher, _executor, store = self.launch(
            fail_at=(
                "stop_unit:hepta-p1-shadow-host-round102.service"))
        with self.assertRaises(LAUNCHER.LauncherError):
            launcher.run()
        self.assertEqual(store.receipt["status"], "FAILED_UNCLEAN")
        self.assertTrue(store.receipt["cleanup_errors"])
        self.assertEqual(store.receipt["authority_residue"], "UNKNOWN")
        self.assertEqual(store.receipt["export_residue"], "UNKNOWN")

    def test_campaign_round_and_start_window_are_fixed(self) -> None:
        invalid = [
            LAUNCHER.LaunchConfiguration(
                "wrong", self.configuration.formal_campaign_id,
                self.configuration.formal_start_ms),
            LAUNCHER.LaunchConfiguration(
                self.configuration.probe_campaign_id,
                "hepta-p1-shadow-soak-round102-20260801",
                self.configuration.formal_start_ms),
            LAUNCHER.LaunchConfiguration(
                self.configuration.probe_campaign_id,
                "hepta-p1-shadow-soak-round100-20260731",
                self.configuration.formal_start_ms),
            LAUNCHER.LaunchConfiguration(
                self.configuration.probe_campaign_id,
                "hepta-p1-shadow-soak-round103-20260731",
                self.configuration.formal_start_ms),
            LAUNCHER.LaunchConfiguration(
                self.configuration.probe_campaign_id,
                self.configuration.formal_campaign_id,
                self.configuration.formal_start_ms +
                200 * 60 * 1000),
            LAUNCHER.LaunchConfiguration(
                self.configuration.probe_campaign_id,
                self.configuration.formal_campaign_id,
                self.configuration.formal_start_ms + 1),
            LAUNCHER.LaunchConfiguration(
                self.configuration.probe_campaign_id,
                self.configuration.formal_campaign_id,
                True),
        ]
        for configuration in invalid:
            with self.assertRaises(LAUNCHER.LauncherError):
                configuration.validate(self.now_ms)
        with self.assertRaises(LAUNCHER.LauncherError):
            replace(self.configuration, formal_start_ms=True).validate(0)
        self.assertEqual(
            self.configuration.validate(self.now_ms),
            (101, 102),
        )
        dispatch_start_ms = (
            self.configuration.formal_start_ms -
            LAUNCHER.PROBE_DISPATCH_LEAD_MS)
        for observed_now_ms in (
                dispatch_start_ms -
                LAUNCHER.FORMAL_START_CLOCK_TOLERANCE_MS,
                dispatch_start_ms +
                LAUNCHER.FORMAL_START_CLOCK_TOLERANCE_MS):
            self.assertEqual(
                self.configuration.validate(observed_now_ms),
                (101, 102),
            )
        with self.assertRaises(LAUNCHER.LauncherError):
            self.configuration.validate(
                dispatch_start_ms +
                LAUNCHER.FORMAL_START_CLOCK_TOLERANCE_MS + 1)

    def test_first_formal_decision_is_reachable_from_fresh_history(self) -> None:
        strategy = json.loads((
            ROOT / "strategies" /
            "eurusd-confirmed-momentum-shadow-v2.json").read_bytes())
        requirements = strategy["evidence_requirements"]
        self.assertEqual(
            LAUNCHER.POLICY_MINIMUM_WARMUP_MS,
            MARKET_HISTORY.DEFAULT_MATERIALIZATION_WINDOW_MS,
        )
        self.assertEqual(
            LAUNCHER.POLICY_MINIMUM_WARMUP_MS,
            HOST_CONTROLLER.FORMAL_HISTORY_WARMUP_MS,
        )
        self.assertEqual(
            LAUNCHER.POLICY_MINIMUM_WARMUP_MS,
            POLICY_BUILDER.MINIMUM_WARMUP_MS,
        )
        self.assertEqual(
            LAUNCHER.POLICY_SLOT_INTERVAL_MS,
            POLICY_BUILDER.SLOT_INTERVAL_MS,
        )
        self.assertEqual(LAUNCHER.POLICY_MINIMUM_WARMUP_MS, 210 * 60_000)
        self.assertEqual(LAUNCHER.PROBE_DISPATCH_LEAD_MS, 20 * 60_000)

        # Exercise every five-minute phase reachable from a two-minute policy
        # grid.  Begin formal collection at the latest allowed +60-second
        # boundary and use only this fresh campaign's ten-second samples.
        phase_base_ms = 1_800_000_000_000
        latest_fortieth_close_ms = 0
        for phase_offset_ms in (0, 120_000, 240_000, 360_000, 480_000):
            formal_start_ms = phase_base_ms + phase_offset_ms
            first_collection_ms = (
                formal_start_ms +
                LAUNCHER.FORMAL_START_CLOCK_TOLERANCE_MS)
            decision_window_start_ms = (
                formal_start_ms + LAUNCHER.POLICY_MINIMUM_WARMUP_MS)
            HOST_CONTROLLER._validate_formal_first_collection(
                SimpleNamespace(valid_after_ms=decision_window_start_ms),
                {"collection_started_at_ms": first_collection_ms},
            )
            records = [{
                "sequence": index + 1,
                "collection_started_at_ms": observed_at_ms,
                "quote": {"bid": 1.1, "ask": 1.2},
                "record_sha256":
                    "sha256:" + f"{index + 1:064x}",
            } for index, observed_at_ms in enumerate(range(
                first_collection_ms,
                decision_window_start_ms + 1,
                10_000,
            ))]
            self.assertGreaterEqual(
                len(records), requirements["minimum_raw_quote_observations"])
            self.assertGreaterEqual(
                records[-1]["collection_started_at_ms"] -
                records[0]["collection_started_at_ms"],
                requirements["minimum_history_span_seconds"] * 1000,
            )

            first_minute = (first_collection_ms // 60_000) * 60_000
            closed_minute_end = (
                decision_window_start_ms // 60_000) * 60_000
            one_minute = {}
            cursor = first_minute
            while cursor + 60_000 <= closed_minute_end:
                one_minute[cursor] = MARKET_HISTORY._quote_bar(
                    records,
                    started_at_ms=cursor,
                    interval_ms=60_000,
                    cadence_ms=10_000,
                    maximum_jitter_ms=1_000,
                )
                cursor += 60_000
            first_five = (first_collection_ms // 300_000) * 300_000
            closed_five_end = (
                decision_window_start_ms // 300_000) * 300_000
            five_minute = []
            cursor = first_five
            while cursor + 300_000 <= closed_five_end:
                five_minute.append(MARKET_HISTORY._five_minute_bar(
                    one_minute, started_at_ms=cursor))
                cursor += 300_000
            complete_suffix = (
                MARKET_HISTORY._complete_five_minute_suffix(five_minute))
            self.assertGreaterEqual(
                len(complete_suffix),
                requirements["minimum_bar_observations"],
            )
            fortieth = complete_suffix[
                requirements["minimum_bar_observations"] - 1]
            latest_fortieth_close_ms = max(
                latest_fortieth_close_ms,
                fortieth["finished_at_ms"] + 1 - formal_start_ms,
            )

        self.assertLessEqual(latest_fortieth_close_ms, 205 * 60_000)

    def test_probe_dispatch_waits_without_formal_authority_until_anchor(
            self) -> None:
        launcher, executor, _ = self.launch()
        launcher._wait_for_formal_preparation_window()
        self.assertEqual(
            self.clock.wall_ms,
            self.configuration.formal_start_ms -
            LAUNCHER.FORMAL_PREPARATION_LEAD_MS,
        )
        self.assertFalse(any(
            action in {"start_reader:formal", "provision:formal"}
            for action in executor.actions))
        launcher._wait_for_formal_warmup_start()
        self.assertEqual(
            self.clock.wall_ms, self.configuration.formal_start_ms)

        late_launcher, _executor, _ = self.launch()
        self.clock.sleep(LAUNCHER.PROBE_DISPATCH_LEAD_MS / 1000 + 1)
        with self.assertRaisesRegex(
                LAUNCHER.LauncherError,
                "P1_LAUNCHER_FORMAL_PREPARATION_LATE"):
            late_launcher._wait_for_formal_preparation_window()

    def test_real_probe_and_fresh_admission_sequence_reaches_anchor(self) \
            -> None:
        launcher, executor, store = self.launch()
        original_probe = executor.run_probe_host
        original_validate = executor.validate_probe
        original_build = executor.build_policy
        original_start_reader = executor.start_reader
        original_provision = executor.provision
        original_formal = executor.run_formal_host
        observed: dict[str, int] = {}

        def run_probe(*arguments, **keywords):
            self.clock.sleep(91 * 10)
            return original_probe(*arguments, **keywords)

        def validate_probe(*arguments, **keywords):
            observed["admitted_at_ms"] = self.clock.wall_ms
            self.clock.sleep(5)
            return original_validate(*arguments, **keywords)

        def build_policy(mode, *arguments, **keywords):
            if mode == "formal":
                self.clock.sleep(5)
            return original_build(mode, *arguments, **keywords)

        def start_reader(*arguments, formal, **keywords):
            if formal:
                observed["formal_reader_started_at_ms"] = self.clock.wall_ms
                self.clock.sleep(5)
            return original_start_reader(
                *arguments, formal=formal, **keywords)

        def provision(campaign_id, owner_pid):
            if campaign_id == self.configuration.formal_campaign_id:
                self.clock.sleep(5)
            return original_provision(campaign_id, owner_pid)

        def run_formal(*arguments, **keywords):
            observed["first_collection_started_at_ms"] = self.clock.wall_ms
            HOST_CONTROLLER._validate_formal_first_collection(
                SimpleNamespace(
                    valid_after_ms=(
                        self.configuration.formal_start_ms +
                        LAUNCHER.POLICY_MINIMUM_WARMUP_MS)),
                {"collection_started_at_ms": self.clock.wall_ms},
            )
            return original_formal(*arguments, **keywords)

        executor.run_probe_host = run_probe
        executor.validate_probe = validate_probe
        executor.build_policy = build_policy
        executor.start_reader = start_reader
        executor.provision = provision
        executor.run_formal_host = run_formal
        result = launcher.run()
        self.assertEqual(result["status"], "FORMAL_COMPLETE")
        self.assertEqual(store.receipt["status"], "FORMAL_COMPLETE")
        self.assertEqual(
            observed["formal_reader_started_at_ms"],
            self.configuration.formal_start_ms,
        )
        self.assertLessEqual(
            observed["first_collection_started_at_ms"] -
            observed["admitted_at_ms"],
            LAUNCHER.ADMISSION_MAXIMUM_AGE_MS,
        )

    def test_far_early_and_late_start_reject_before_probe_side_effects(
            self) -> None:
        cases = {
            "early-200m": (
                replace(
                    self.configuration,
                    formal_start_ms=(
                        self.configuration.formal_start_ms +
                        200 * 60 * 1000)),
                self.now_ms,
            ),
            "late": (
                self.configuration,
                self.configuration.formal_start_ms -
                LAUNCHER.PROBE_DISPATCH_LEAD_MS +
                LAUNCHER.FORMAL_START_CLOCK_TOLERANCE_MS + 1,
            ),
        }
        for name, (configuration, observed_now_ms) in cases.items():
            with self.subTest(name=name):
                executor = FakeExecutor(configuration)
                with self.assertRaisesRegex(
                        LAUNCHER.LauncherError,
                        "P1_LAUNCHER_CONFIGURATION_INVALID"):
                    LAUNCHER.Launcher(
                        configuration,
                        executor,
                        FakeStore(),
                        now_ms=observed_now_ms,
                    )
                self.assertEqual(executor.actions, [])

    def test_wall_monotonic_drift_rejects_before_probe_side_effects(
            self) -> None:
        wall_ms = (
            self.configuration.formal_start_ms -
            LAUNCHER.PROBE_DISPATCH_LEAD_MS)
        monotonic_seconds = 100.0
        executor = FakeExecutor(self.configuration)
        launcher = LAUNCHER.Launcher(
            self.configuration,
            executor,
            FakeStore(),
            now_ms=wall_ms,
            _wall_now_ms=lambda: wall_ms,
            _monotonic_clock=lambda: monotonic_seconds,
        )
        wall_ms += LAUNCHER.FORMAL_START_MAXIMUM_CLOCK_DRIFT_MS + 1
        with self.assertRaisesRegex(
                LAUNCHER.LauncherError,
                "P1_LAUNCHER_START_CLOCK_DRIFT"):
            launcher.run()
        self.assertNotIn("prepare", executor.actions)
        self.assertFalse(any(
            action.startswith("start_reader") for action in executor.actions))

    def test_systemd_argv_binds_exact_reader_gateway_and_round(self) -> None:
        _, formal_round = self.configuration.validate(self.now_ms)
        paths = LAUNCHER.RunPaths.derive(
            self.configuration, formal_round)
        reader = LAUNCHER.ProductionExecutor._reader_command(
            self.configuration.formal_campaign_id,
            "hepta-p1-shadow-reader-round102.service",
            "hepta-p1-shadow-admission-round102.service",
            paths.formal_policy, paths.formal_marker, paths, formal=True)
        self.assertEqual(reader[0], "/usr/bin/systemd-run")
        self.assertIn(
            "--unit=hepta-p1-shadow-reader-round102.service", reader)
        self.assertIn("--uid=1000", reader)
        for argument in LAUNCHER.TRANSIENT_ENVIRONMENT_ARGUMENTS:
            self.assertEqual(reader.count(argument), 1)
        self.assertEqual(
            reader.count(LAUNCHER.PAPER_CONFLICTS_PROPERTY), 1)
        self.assertEqual(reader.count("--property=PrivateNetwork=yes"), 1)
        self.assertEqual(
            reader.count("--property=RestrictAddressFamilies=AF_UNIX"), 1)
        self.assertIn(
            "--property=BindsTo=hepta-p1-shadow-admission-round102.service "
            "hepta-tool-gateway@alpha.service", reader)
        self.assertEqual(reader[reader.index("--") + 1], LAUNCHER.READER)
        host = LAUNCHER.ProductionExecutor._host_prefix(
            "hepta-p1-shadow-host-round102.service",
            "hepta-p1-shadow-reader-round102.service",
            "hepta-p1-shadow-admission-round102.service")
        self.assertIn("--wait", host)
        self.assertIn("--pipe", host)
        for argument in LAUNCHER.TRANSIENT_ENVIRONMENT_ARGUMENTS:
            self.assertEqual(host.count(argument), 1)
        self.assertEqual(
            host.count(LAUNCHER.PAPER_CONFLICTS_PROPERTY), 1)
        self.assertNotIn("--property=PrivateNetwork=yes", host)
        self.assertIn(
            "--property=BindsTo=hepta-p1-shadow-admission-round102.service "
            "hepta-p1-shadow-reader-round102.service "
            "hepta-tool-gateway@alpha.service", host)
        self.assertIn(
            "--property=After=hepta-p1-shadow-admission-round102.service "
            "hepta-p1-shadow-reader-round102.service "
            "hepta-tool-gateway@alpha.service", host)
        self.assertEqual(host[host.index("--") + 1], LAUNCHER.HOST)

    def test_verifier_semantic_helper_closure_is_complete_and_exact(
            self) -> None:
        expected = {
            "observer_sha256": LAUNCHER.OBSERVER,
            "market_context_builder_sha256":
                LAUNCHER.MARKET_CONTEXT_BUILDER,
            "market_evidence_normalizer_sha256":
                LAUNCHER.MARKET_EVIDENCE_NORMALIZER,
            "market_official_source_extractor_sha256":
                LAUNCHER.MARKET_OFFICIAL_SOURCE_EXTRACTOR,
            "momentum_strategy_sha256": LAUNCHER.MOMENTUM_STRATEGY,
            "market_history_sha256": LAUNCHER.MARKET_HISTORY,
            "strategy_runner_sha256": LAUNCHER.STRATEGY_RUNNER,
            "strategy_contracts_sha256": LAUNCHER.STRATEGY_CONTRACTS,
            "decision_receipt_validator_sha256":
                LAUNCHER.DECISION_RECEIPT_VALIDATOR,
        }
        self.assertEqual(
            {field: str(LAUNCHER.HELPERS[field]) for field in expected},
            expected)
        self.assertEqual(
            LAUNCHER.HELPER_MODES["strategy_contracts_sha256"],
            frozenset({0o644}))
        self.assertEqual(
            LAUNCHER.HELPERS["gateway_profile_sha256"],
            LAUNCHER.GATEWAY_PROFILE)
        self.assertEqual(
            LAUNCHER.HELPER_MODES["gateway_profile_sha256"],
            frozenset({0o644}))
        self.assertEqual(
            LAUNCHER.HELPERS["trust_domain_runtime_sha256"],
            Path(LAUNCHER.TRUST_DOMAIN_RUNTIME))
        self.assertEqual(
            LAUNCHER.HELPER_MODES["trust_domain_runtime_sha256"],
            frozenset({0o755}))
        for field in expected:
            if field != "strategy_contracts_sha256":
                self.assertEqual(
                    LAUNCHER.HELPER_MODES[field], frozenset({0o755}))

    def test_real_gateway_identity_binds_profile_process_and_reopens(
            self) -> None:
        executor = LAUNCHER.ProductionExecutor()
        status_output = (
            "ActiveState=active\nSubState=running\n" +
            "InvocationID=" + "a" * 32 + "\n" +
            "MainPID=123\nExecMainStartTimestampMonotonic=456\n")
        completed = subprocess.CompletedProcess(
            [LAUNCHER.SYSTEMCTL], 0, stdout=status_output, stderr="")
        profile = mock.Mock(raw=b"profile\n")
        process_profile = mock.Mock(
            pid_directory_metadata=(1, 2, 3), starttime_ticks=789,
            canonical_projection=b"process-profile\n")
        process_identity = mock.Mock(
            pid_directory_metadata=(1, 2, 3), starttime_ticks=789)
        gateway_socket = mock.Mock(metadata=(11, 12))
        events: list[str] = []

        def status(_arguments, _timeout):
            events.append("status")
            return completed

        def profile_read(_path):
            events.append("profile")
            return profile

        def process_profile_read(_pid):
            events.append("process-profile")
            return process_profile

        def socket_read(_path):
            events.append("socket")
            return gateway_socket

        def process_identity_read(_pid):
            events.append("process-identity")
            return process_identity

        executor._run = status
        with mock.patch.object(
                LAUNCHER, "read_alpha_gateway_profile",
                side_effect=profile_read), \
                mock.patch.object(
                    LAUNCHER, "read_alpha_gateway_process_profile",
                    side_effect=process_profile_read), \
                mock.patch.object(
                    LAUNCHER, "read_alpha_gateway_socket",
                    side_effect=socket_read), \
                mock.patch.object(
                    LAUNCHER, "read_alpha_gateway_process_identity",
                    side_effect=process_identity_read), \
                mock.patch.object(
                    LAUNCHER, "_secure_read", return_value=b"domain\n"):
            result = executor.gateway_identity()
        self.assertEqual(events, [
            "profile", "status", "process-profile", "socket", "profile",
            "status", "process-identity", "socket",
        ])
        self.assertEqual(result, {
            "gateway_invocation_id": "a" * 32,
            "gateway_main_pid": 123,
            "gateway_exec_main_start_timestamp_monotonic_us": 456,
            "gateway_socket_device": 11,
            "gateway_socket_inode": 12,
            "domain_config_sha256": LAUNCHER.digest_bytes(b"domain\n"),
            "gateway_profile_sha256": LAUNCHER.digest_bytes(profile.raw),
            "gateway_process_profile_sha256": LAUNCHER.digest_bytes(
                process_profile.canonical_projection),
        })

        changed = mock.Mock(
            pid_directory_metadata=(1, 2, 3), starttime_ticks=790)
        executor._run = lambda _arguments, _timeout: completed
        with mock.patch.object(
                LAUNCHER, "read_alpha_gateway_profile",
                return_value=profile), \
                mock.patch.object(
                    LAUNCHER, "read_alpha_gateway_process_profile",
                    return_value=process_profile), \
                mock.patch.object(
                    LAUNCHER, "read_alpha_gateway_socket",
                    return_value=gateway_socket), \
                mock.patch.object(
                    LAUNCHER, "read_alpha_gateway_process_identity",
                    return_value=changed), \
                self.assertRaisesRegex(
                    LAUNCHER.LauncherError,
                    "P1_LAUNCHER_GATEWAY_CHANGED"):
            executor.gateway_identity()

    def test_broker_deny_all_probe_executes_held_credential_source(self) -> None:
        executor = LAUNCHER.ProductionExecutor()
        status = (
            "ActiveState=active\nSubState=running\n" +
            "InvocationID=" + "a" * 32 + "\nMainPID=123\n" +
            "ExecMainStartTimestampMonotonic=456\nTasksCurrent=1\n" +
            "StatusText=HeptaTrader broker boundary exact deny-all\n")
        calls = []

        def run(arguments, _timeout):
            calls.append(arguments)
            if arguments[0] == LAUNCHER.SYSTEMCTL:
                return subprocess.CompletedProcess(
                    arguments, 0, stdout=status, stderr="")
            return subprocess.CompletedProcess(
                arguments, 0,
                stdout=(
                    "hepta_broker_egress_policy: PASS policy_sha256=" +
                    "b" * 64 +
                    " authorized_connectors=0 authorized_uids= "
                    "protected_ports=4\n"),
                stderr="")

        process = {
            "process_starttime_ticks": 789,
            "cmdline_sha256": "sha256:" + "1" * 64,
            "interpreter_sha256": "sha256:" + "2" * 64,
            "credential_source_sha256": LAUNCHER.digest_bytes(b"source"),
        }
        snapshots = [
            (b"source", (1,)), (b"source", (2,)),
            (b"source", (1,)), (b"source", (2,)),
        ]
        executor._run = run
        with mock.patch.object(
                executor, "_broker_process_evidence",
                return_value=process), \
                mock.patch.object(
                    executor, "_read_anchored_root_file",
                    side_effect=snapshots):
            evidence = executor._broker_activation_evidence()
        probe = [call for call in calls
                 if call[0] == str(LAUNCHER.BROKER_INTERPRETER)][0]
        self.assertEqual(probe[:4], [
            str(LAUNCHER.BROKER_INTERPRETER), "-I", "-S",
            str(LAUNCHER.BROKER_CREDENTIAL_SOURCE)])
        self.assertNotIn(str(LAUNCHER.BROKER_EGRESS_POLICY), probe[:1])
        self.assertEqual(
            evidence["credential_source_sha256"],
            evidence["installed_source_sha256"])

        executor._run = run
        drifting = [
            (b"source", (1,)), (b"source", (2,)),
            (b"source", (3,)), (b"source", (2,)),
        ]
        with mock.patch.object(
                executor, "_broker_process_evidence",
                return_value=process), \
                mock.patch.object(
                    executor, "_read_anchored_root_file",
                    side_effect=drifting), \
                self.assertRaisesRegex(
                    LAUNCHER.LauncherError,
                    "P1_LAUNCHER_BROKER_EPOCH_CHANGED"):
            executor._broker_activation_evidence()

    def test_probe_builder_uses_formal_warmup_anchor(self) -> None:
        _, formal_round = self.configuration.validate(self.now_ms)
        paths = LAUNCHER.RunPaths.derive(
            self.configuration, formal_round)
        executor = LAUNCHER.ProductionExecutor()
        calls = []

        def fake_run(arguments, _timeout):
            calls.append(arguments)
            return subprocess.CompletedProcess(
                arguments, 0, stdout="", stderr="")

        executor._run = fake_run
        policy = policy_document(
            self.configuration, self.configuration.probe_campaign_id)
        marker = LAUNCHER.seal({
            "schema": "hepta.p1-shadow-load-probe-authority-marker.v1",
            "campaign_id": self.configuration.probe_campaign_id,
            "paper_authorized": False,
            "live_authorized": False,
            "mutation_authorized": False,
            "direct_broker_access": False,
        })
        with mock.patch.object(
            LAUNCHER, "_document",
            side_effect=[
                (policy, LAUNCHER.canonical_bytes(policy)),
                (marker, LAUNCHER.canonical_bytes(marker)),
            ]):
            executor.build_policy(
                "load-probe", self.configuration, paths)
        command = calls[0]
        self.assertEqual(command[command.index("--mode") + 1], "load-probe")
        self.assertEqual(
            command[command.index("--start-ms") + 1],
            str(self.configuration.formal_start_ms))
        self.assertEqual(command.count("--gateway-profile"), 1)
        self.assertEqual(
            command[command.index("--gateway-profile") + 1],
            str(LAUNCHER.GATEWAY_PROFILE))
        self.assertNotIn("--admission-receipt", command)

    def test_probe_validator_argv_binds_gateway_profile(self) -> None:
        _, formal_round = self.configuration.validate(self.now_ms)
        paths = LAUNCHER.RunPaths.derive(
            self.configuration, formal_round)
        executor = LAUNCHER.ProductionExecutor()
        calls: list[list[str]] = []

        def fake_run(arguments, _timeout):
            calls.append(arguments)
            return subprocess.CompletedProcess(
                arguments, 0, stdout="", stderr="")

        executor._run = fake_run
        with mock.patch.object(
                LAUNCHER, "_document", return_value=({}, b"{}\n")):
            executor.validate_probe(self.configuration, paths)
        command = calls[0]
        self.assertEqual(command.count("--gateway-profile"), 1)
        self.assertEqual(
            command[command.index("--gateway-profile") + 1],
            str(LAUNCHER.GATEWAY_PROFILE))

    def test_formal_policy_schedule_drives_unique_host_argv(self) -> None:
        _, formal_round = self.configuration.validate(self.now_ms)
        paths = LAUNCHER.RunPaths.derive(
            self.configuration, formal_round)
        policy = policy_document(
            self.configuration, self.configuration.formal_campaign_id)
        policy_contents = LAUNCHER.canonical_bytes(policy)
        artifacts = LAUNCHER.PolicyArtifacts(
            policy=policy,
            policy_file_sha256=LAUNCHER.digest_bytes(policy_contents),
            marker={},
            marker_file_sha256="sha256:" + "8" * 64,
            valid_after_ms=policy["valid_after_ms"],
            maximum_iterations=policy["maximum_iterations"],
        )
        calls = []
        result = {
            "paper_authorized": False,
            "live_authorized": False,
            "mutation_authorized": False,
            "direct_broker_access": False,
        }
        executor = LAUNCHER.ProductionExecutor()

        def fake_run(arguments, _timeout):
            calls.append(arguments)
            return subprocess.CompletedProcess(
                arguments, 0,
                stdout=LAUNCHER.canonical_bytes(result).decode("ascii"),
                stderr="")

        executor._run = fake_run
        with mock.patch.object(
                LAUNCHER, "_document",
                return_value=(policy, policy_contents)):
            executor.run_formal_host(
                self.configuration, paths,
                "hepta-p1-shadow-reader-round102.service", 1,
                "sha256:" + "7" * 64, artifacts)
        command = calls[0]
        warmup_start_ms = self.configuration.formal_start_ms
        decision_window_start_ms = (
            self.configuration.formal_start_ms +
            LAUNCHER.POLICY_MINIMUM_WARMUP_MS)
        self.assertEqual(
            policy["valid_after_ms"], decision_window_start_ms)
        self.assertNotEqual(
            decision_window_start_ms, warmup_start_ms)
        self.assertEqual(
            decision_window_start_ms - warmup_start_ms,
            LAUNCHER.POLICY_MINIMUM_WARMUP_MS)
        self.assertEqual(
            policy["maximum_iterations"], LAUNCHER.FORMAL_ITERATIONS)
        self.assertEqual(
            policy["expires_at_ms"] - policy["valid_after_ms"],
            LAUNCHER.FORMAL_ITERATIONS *
            LAUNCHER.POLICY_SLOT_INTERVAL_MS)
        self.assertEqual(
            (LAUNCHER.FORMAL_ITERATIONS - 1) *
            LAUNCHER.POLICY_SLOT_INTERVAL_MS,
            480 * 60 * 1000)
        self.assertEqual(command.count("--valid-after-ms"), 1)
        self.assertEqual(
            command[command.index("--valid-after-ms") + 1],
            str(policy["valid_after_ms"]))
        self.assertEqual(command.count("--maximum-iterations"), 1)
        self.assertEqual(
            command[command.index("--maximum-iterations") + 1],
            str(policy["maximum_iterations"]))
        for flag in (
            "--campaign-id", "--domain-config", "--start-generation",
            "--policy", "--admission-receipt", "--authority-marker",
            "--watch-snapshot",
        ):
            self.assertEqual(command.count(flag), 1, flag)

    def test_verifier_argv_and_root_private_exclusive_output_are_exact(
            self) -> None:
        _, formal_round = self.configuration.validate(self.now_ms)
        base_paths = LAUNCHER.RunPaths.derive(
            self.configuration, formal_round)
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            private = root / "private"
            private.mkdir(mode=0o700)
            formal_reader = root / "formal-reader"
            formal_reader.mkdir(mode=0o700)
            paths = replace(
                base_paths,
                private_directory=private,
                formal_reader_directory=formal_reader,
                formal_policy=root / "formal-policy.json",
                formal_verified_closure=private / "verified-closure.json",
            )
            closure_document = LAUNCHER.seal({
                "paper_authorized": False,
                "live_authorized": False,
                "mutation_attempted": False,
                "direct_broker_access": False,
            })
            calls = []
            executor = LAUNCHER.ProductionExecutor()

            def fake_run(arguments, timeout):
                calls.append((arguments, timeout))
                return subprocess.CompletedProcess(
                    arguments, 0, stdout="PASS\n", stderr="")

            executor._run = fake_run
            with mock.patch.object(
                    LAUNCHER, "ROOT_UID", new=LAUNCHER.os.getuid()), \
                    mock.patch.object(
                        LAUNCHER, "ROOT_GID", new=LAUNCHER.os.getgid()), \
                    mock.patch.object(
                        LAUNCHER, "_document",
                        return_value=(
                            closure_document,
                            LAUNCHER.canonical_bytes(closure_document))), \
                    mock.patch.object(
                        LAUNCHER, "_secure_read", return_value=b"strategy"):
                artifact = executor.verify_formal_closure(paths)
                with self.assertRaisesRegex(
                        LAUNCHER.LauncherError,
                        "P1_LAUNCHER_VERIFIED_CLOSURE_EXISTS"):
                    executor.verify_formal_closure(paths)
            self.assertEqual(calls, [([
                LAUNCHER.VERIFIER,
                "--artifact-root", str(formal_reader / "observer"),
                "--policy", str(paths.formal_policy),
                "--strategy", str(LAUNCHER.STRATEGY),
                "--output", str(paths.formal_verified_closure),
            ], LAUNCHER.VERIFIER_TIMEOUT_SECONDS)])
            self.assertLess(
                LAUNCHER.VERIFIER_TIMEOUT_SECONDS * 1000,
                LAUNCHER.POLICY_SLOT_INTERVAL_MS,
            )
            self.assertEqual(
                paths.formal_verified_closure.stat().st_mode & 0o777, 0o600)
            self.assertEqual(
                artifact.strategy_file_sha256,
                LAUNCHER.digest_bytes(b"strategy"))

    def test_clean_boundary_allows_only_safe_idle_bootstrap_lock(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            sessions = root / "sessions"
            sessions.mkdir(mode=0o711)
            sessions.chmod(0o711)
            private_parent = root / "hepta-shadow-watch-alpha"
            private_parent.mkdir(mode=0o700)
            private = private_parent / "private"
            private.mkdir(mode=0o700)
            lock = sessions / LAUNCHER.SESSION_BOOTSTRAP_LOCK
            lock.touch(mode=0o600)
            lock.chmod(0o600)
            with mock.patch.multiple(
                    LAUNCHER,
                    WATCH_EXPORT=root / "export",
                    WATCH_SESSIONS=sessions,
                    WATCH_PRIVATE=private,
                    ROOT_UID=LAUNCHER.os.getuid(),
                    ROOT_GID=LAUNCHER.os.getgid(),
                    WATCH_UID=LAUNCHER.os.getuid(),
                    WATCH_GID=LAUNCHER.os.getgid()):
                LAUNCHER.ProductionExecutor._assert_no_residue()

    def test_watch_private_accepts_exact_service_owned_host_shape(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            anchor = Path(temporary)
            parent = anchor / "hepta-shadow-watch-alpha"
            parent.mkdir(mode=0o700)
            private = parent / "private"
            private.mkdir(mode=0o700)
            root_uid = LAUNCHER.os.getuid()
            root_gid = LAUNCHER.os.getgid()
            watch_uid = root_uid + 10000
            watch_gid = root_gid + 10000
            service_identities = {
                (parent.stat().st_dev, parent.stat().st_ino),
                (private.stat().st_dev, private.stat().st_ino),
            }
            real_stat = LAUNCHER.os.stat
            real_fstat = LAUNCHER.os.fstat

            def project(metadata):
                if (metadata.st_dev, metadata.st_ino) not in service_identities:
                    return metadata
                return SimpleNamespace(
                    st_dev=metadata.st_dev,
                    st_ino=metadata.st_ino,
                    st_uid=watch_uid,
                    st_gid=watch_gid,
                    st_mode=metadata.st_mode,
                    st_nlink=metadata.st_nlink,
                    st_size=metadata.st_size,
                    st_mtime_ns=metadata.st_mtime_ns,
                    st_ctime_ns=metadata.st_ctime_ns,
                )

            def projected_stat(path, *args, **kwargs):
                return project(real_stat(path, *args, **kwargs))

            def projected_fstat(descriptor):
                return project(real_fstat(descriptor))

            with mock.patch.multiple(
                    LAUNCHER,
                    WATCH_PRIVATE=private,
                    ROOT_UID=root_uid,
                    ROOT_GID=root_gid,
                    WATCH_UID=watch_uid,
                    WATCH_GID=watch_gid), \
                    mock.patch.object(
                        LAUNCHER.os, "stat", side_effect=projected_stat), \
                    mock.patch.object(
                        LAUNCHER.os, "fstat", side_effect=projected_fstat):
                LAUNCHER.ProductionExecutor.\
                    _assert_service_owned_watch_private_without_authority()

    def test_watch_private_rejects_missing_host_shape(self) -> None:
        for missing in ("parent", "leaf"):
            with self.subTest(missing=missing), \
                    tempfile.TemporaryDirectory() as temporary:
                anchor = Path(temporary)
                parent = anchor / "hepta-shadow-watch-alpha"
                private = parent / "private"
                if missing == "leaf":
                    parent.mkdir(mode=0o700)
                with mock.patch.multiple(
                        LAUNCHER,
                        WATCH_PRIVATE=private,
                        ROOT_UID=LAUNCHER.os.getuid(),
                        ROOT_GID=LAUNCHER.os.getgid(),
                        WATCH_UID=LAUNCHER.os.getuid(),
                        WATCH_GID=LAUNCHER.os.getgid()):
                    with self.assertRaisesRegex(
                            LAUNCHER.LauncherError,
                            "P1_LAUNCHER_AUTHORITY_RESIDUE"):
                        LAUNCHER.ProductionExecutor.\
                            _assert_service_owned_watch_private_without_authority()

    def test_watch_private_rejects_wrong_anchor_parent_or_leaf_mode(self) -> None:
        for component in ("anchor", "parent", "leaf"):
            with self.subTest(component=component), \
                    tempfile.TemporaryDirectory() as temporary:
                anchor = Path(temporary)
                parent = anchor / "hepta-shadow-watch-alpha"
                parent.mkdir(mode=0o700)
                private = parent / "private"
                private.mkdir(mode=0o700)
                {
                    "anchor": anchor,
                    "parent": parent,
                    "leaf": private,
                }[component].chmod(0o777 if component == "anchor" else 0o750)
                with mock.patch.multiple(
                        LAUNCHER,
                        WATCH_PRIVATE=private,
                        ROOT_UID=LAUNCHER.os.getuid(),
                        ROOT_GID=LAUNCHER.os.getgid(),
                        WATCH_UID=LAUNCHER.os.getuid(),
                        WATCH_GID=LAUNCHER.os.getgid()):
                    with self.assertRaisesRegex(
                            LAUNCHER.LauncherError,
                            "P1_LAUNCHER_AUTHORITY_RESIDUE"):
                        LAUNCHER.ProductionExecutor.\
                            _assert_service_owned_watch_private_without_authority()

    def test_watch_private_rejects_wrong_anchor_or_service_owner(self) -> None:
        for component in ("anchor", "service"):
            with self.subTest(component=component), \
                    tempfile.TemporaryDirectory() as temporary:
                anchor = Path(temporary)
                parent = anchor / "hepta-shadow-watch-alpha"
                parent.mkdir(mode=0o700)
                private = parent / "private"
                private.mkdir(mode=0o700)
                current_uid = LAUNCHER.os.getuid()
                current_gid = LAUNCHER.os.getgid()
                with mock.patch.multiple(
                        LAUNCHER,
                        WATCH_PRIVATE=private,
                        ROOT_UID=(
                            current_uid + 1
                            if component == "anchor" else current_uid),
                        ROOT_GID=current_gid,
                        WATCH_UID=(
                            current_uid + 1
                            if component == "service" else current_uid),
                        WATCH_GID=current_gid):
                    with self.assertRaisesRegex(
                            LAUNCHER.LauncherError,
                            "P1_LAUNCHER_AUTHORITY_RESIDUE"):
                        LAUNCHER.ProductionExecutor.\
                            _assert_service_owned_watch_private_without_authority()

    def test_watch_private_rejects_leaf_uid_or_gid_after_parent_passes(
            self) -> None:
        for mutation in ("leaf_uid", "leaf_gid"):
            with self.subTest(mutation=mutation), \
                    tempfile.TemporaryDirectory() as temporary:
                anchor = Path(temporary)
                parent = anchor / "hepta-shadow-watch-alpha"
                parent.mkdir(mode=0o700)
                private = parent / "private"
                private.mkdir(mode=0o700)
                root_uid = LAUNCHER.os.getuid()
                root_gid = LAUNCHER.os.getgid()
                watch_uid = root_uid + 10000
                watch_gid = root_gid + 10000
                parent_identity = (parent.stat().st_dev, parent.stat().st_ino)
                leaf_identity = (private.stat().st_dev, private.stat().st_ino)
                real_stat = LAUNCHER.os.stat
                real_fstat = LAUNCHER.os.fstat
                parent_observed = False
                leaf_observed = False

                def project(metadata):
                    nonlocal parent_observed, leaf_observed
                    identity = (metadata.st_dev, metadata.st_ino)
                    if identity == parent_identity:
                        parent_observed = True
                        uid, gid = watch_uid, watch_gid
                    elif identity == leaf_identity:
                        leaf_observed = True
                        uid = watch_uid + (mutation == "leaf_uid")
                        gid = watch_gid + (mutation == "leaf_gid")
                    else:
                        return metadata
                    return SimpleNamespace(
                        st_dev=metadata.st_dev,
                        st_ino=metadata.st_ino,
                        st_uid=uid,
                        st_gid=gid,
                        st_mode=metadata.st_mode,
                        st_nlink=metadata.st_nlink,
                        st_size=metadata.st_size,
                        st_mtime_ns=metadata.st_mtime_ns,
                        st_ctime_ns=metadata.st_ctime_ns,
                    )

                def projected_stat(path, *args, **kwargs):
                    return project(real_stat(path, *args, **kwargs))

                def projected_fstat(descriptor):
                    return project(real_fstat(descriptor))

                with mock.patch.multiple(
                        LAUNCHER,
                        WATCH_PRIVATE=private,
                        ROOT_UID=root_uid,
                        ROOT_GID=root_gid,
                        WATCH_UID=watch_uid,
                        WATCH_GID=watch_gid), \
                        mock.patch.object(
                            LAUNCHER.os, "stat", side_effect=projected_stat), \
                        mock.patch.object(
                            LAUNCHER.os, "fstat", side_effect=projected_fstat):
                    with self.assertRaisesRegex(
                            LAUNCHER.LauncherError,
                            "P1_LAUNCHER_AUTHORITY_RESIDUE"):
                        LAUNCHER.ProductionExecutor.\
                            _assert_service_owned_watch_private_without_authority()
                self.assertTrue(parent_observed)
                self.assertTrue(leaf_observed)

    def test_watch_private_rejects_parent_and_leaf_inventory(self) -> None:
        for component in ("parent", "leaf"):
            with self.subTest(component=component), \
                    tempfile.TemporaryDirectory() as temporary:
                anchor = Path(temporary)
                parent = anchor / "hepta-shadow-watch-alpha"
                parent.mkdir(mode=0o700)
                private = parent / "private"
                private.mkdir(mode=0o700)
                extra_parent = parent if component == "parent" else private
                (extra_parent / "residue").write_text("authority")
                with mock.patch.multiple(
                        LAUNCHER,
                        WATCH_PRIVATE=private,
                        ROOT_UID=LAUNCHER.os.getuid(),
                        ROOT_GID=LAUNCHER.os.getgid(),
                        WATCH_UID=LAUNCHER.os.getuid(),
                        WATCH_GID=LAUNCHER.os.getgid()):
                    with self.assertRaisesRegex(
                            LAUNCHER.LauncherError,
                            "P1_LAUNCHER_AUTHORITY_RESIDUE"):
                        LAUNCHER.ProductionExecutor.\
                            _assert_service_owned_watch_private_without_authority()

    def test_watch_private_rejects_parent_or_leaf_symlink(self) -> None:
        for component in ("parent", "leaf"):
            with self.subTest(component=component), \
                    tempfile.TemporaryDirectory() as temporary:
                anchor = Path(temporary)
                actual_parent = anchor / "actual-parent"
                actual_parent.mkdir(mode=0o700)
                actual_private = actual_parent / "private"
                actual_private.mkdir(mode=0o700)
                parent = anchor / "hepta-shadow-watch-alpha"
                private = parent / "private"
                if component == "parent":
                    parent.symlink_to(actual_parent, target_is_directory=True)
                else:
                    parent.mkdir(mode=0o700)
                    private.symlink_to(
                        actual_private, target_is_directory=True)
                with mock.patch.multiple(
                        LAUNCHER,
                        WATCH_PRIVATE=private,
                        ROOT_UID=LAUNCHER.os.getuid(),
                        ROOT_GID=LAUNCHER.os.getgid(),
                        WATCH_UID=LAUNCHER.os.getuid(),
                        WATCH_GID=LAUNCHER.os.getgid()):
                    with self.assertRaisesRegex(
                            LAUNCHER.LauncherError,
                            "P1_LAUNCHER_AUTHORITY_RESIDUE"):
                        LAUNCHER.ProductionExecutor.\
                            _assert_service_owned_watch_private_without_authority()

    @staticmethod
    def _replace_watch_private_component(
        anchor: Path,
        parent: Path,
        private: Path,
        component: str,
        *,
        restore: bool,
    ) -> None:
        target = parent if component == "parent" else private
        preserved = anchor / f"held-{component}"
        target.rename(preserved)
        if component == "parent":
            parent.mkdir(mode=0o700)
            private.mkdir(mode=0o700)
        else:
            private.mkdir(mode=0o700)
        if restore:
            if component == "parent":
                private.rmdir()
                parent.rmdir()
            else:
                private.rmdir()
            preserved.rename(target)

    def test_watch_private_rejects_parent_or_leaf_swap_at_reopen(self) -> None:
        for component in ("parent", "leaf"):
            with self.subTest(component=component), \
                    tempfile.TemporaryDirectory() as temporary:
                anchor = Path(temporary)
                parent = anchor / "hepta-shadow-watch-alpha"
                parent.mkdir(mode=0o700)
                private = parent / "private"
                private.mkdir(mode=0o700)
                real_open = (
                    LAUNCHER.ProductionExecutor._open_anchored_directory)
                calls = 0

                def swap_before_reopen(path, reason):
                    nonlocal calls
                    calls += 1
                    if calls == 2:
                        if component == "parent":
                            parent.rename(anchor / "held-parent")
                            parent.mkdir(mode=0o700)
                            (parent / "private").mkdir(mode=0o700)
                        else:
                            private.rename(anchor / "held-private")
                            private.mkdir(mode=0o700)
                    return real_open(path, reason)

                with mock.patch.multiple(
                        LAUNCHER,
                        WATCH_PRIVATE=private,
                        ROOT_UID=LAUNCHER.os.getuid(),
                        ROOT_GID=LAUNCHER.os.getgid(),
                        WATCH_UID=LAUNCHER.os.getuid(),
                        WATCH_GID=LAUNCHER.os.getgid()), \
                        mock.patch.object(
                            LAUNCHER.ProductionExecutor,
                            "_open_anchored_directory",
                            side_effect=swap_before_reopen):
                    with self.assertRaisesRegex(
                            LAUNCHER.LauncherError,
                            "P1_LAUNCHER_AUTHORITY_RESIDUE"):
                        LAUNCHER.ProductionExecutor.\
                            _assert_service_owned_watch_private_without_authority()

    def test_watch_private_rejects_parent_leaf_swap_and_aba_stat_to_open(
            self) -> None:
        for component in ("parent", "leaf"):
            for mutation in ("swap", "aba"):
                with self.subTest(component=component, mutation=mutation), \
                        tempfile.TemporaryDirectory() as temporary:
                    anchor = Path(temporary)
                    parent = anchor / "hepta-shadow-watch-alpha"
                    parent.mkdir(mode=0o700)
                    private = parent / "private"
                    private.mkdir(mode=0o700)
                    target_name = parent.name if component == "parent" \
                        else private.name
                    real_open = LAUNCHER.os.open
                    raced = False

                    def open_with_race(path, flags, *args, **kwargs):
                        nonlocal raced
                        if path == target_name and not raced:
                            raced = True
                            self._replace_watch_private_component(
                                anchor, parent, private, component,
                                restore=mutation == "aba")
                        return real_open(path, flags, *args, **kwargs)

                    with mock.patch.multiple(
                            LAUNCHER,
                            WATCH_PRIVATE=private,
                            ROOT_UID=LAUNCHER.os.getuid(),
                            ROOT_GID=LAUNCHER.os.getgid(),
                            WATCH_UID=LAUNCHER.os.getuid(),
                            WATCH_GID=LAUNCHER.os.getgid()), \
                            mock.patch.object(
                                LAUNCHER.os, "open", side_effect=open_with_race):
                        with self.assertRaisesRegex(
                                LAUNCHER.LauncherError,
                                "P1_LAUNCHER_AUTHORITY_RESIDUE"):
                            LAUNCHER.ProductionExecutor.\
                                _assert_service_owned_watch_private_without_authority()
                    self.assertTrue(raced)

    def test_watch_private_rejects_parent_leaf_swap_and_aba_after_rebound_open(
            self) -> None:
        for component in ("parent", "leaf"):
            for mutation in ("swap", "aba"):
                with self.subTest(component=component, mutation=mutation), \
                        tempfile.TemporaryDirectory() as temporary:
                    anchor = Path(temporary)
                    parent = anchor / "hepta-shadow-watch-alpha"
                    parent.mkdir(mode=0o700)
                    private = parent / "private"
                    private.mkdir(mode=0o700)
                    target_name = parent.name if component == "parent" \
                        else private.name
                    real_open = LAUNCHER.os.open
                    real_stat = LAUNCHER.os.stat
                    target_opens = 0
                    target_stats = 0
                    opens_before_race = 0
                    raced = False

                    def tracking_open(path, flags, *args, **kwargs):
                        nonlocal target_opens
                        descriptor = real_open(path, flags, *args, **kwargs)
                        if path == target_name:
                            target_opens += 1
                        return descriptor

                    def stat_with_race(path, *args, **kwargs):
                        nonlocal target_stats, opens_before_race, raced
                        if path == target_name:
                            target_stats += 1
                            if target_stats == 3:
                                opens_before_race = target_opens
                                raced = True
                                self._replace_watch_private_component(
                                    anchor, parent, private, component,
                                    restore=mutation == "aba")
                        return real_stat(path, *args, **kwargs)

                    with mock.patch.multiple(
                            LAUNCHER,
                            WATCH_PRIVATE=private,
                            ROOT_UID=LAUNCHER.os.getuid(),
                            ROOT_GID=LAUNCHER.os.getgid(),
                            WATCH_UID=LAUNCHER.os.getuid(),
                            WATCH_GID=LAUNCHER.os.getgid()), \
                            mock.patch.object(
                                LAUNCHER.os, "open", side_effect=tracking_open), \
                            mock.patch.object(
                                LAUNCHER.os, "stat", side_effect=stat_with_race):
                        with self.assertRaisesRegex(
                                LAUNCHER.LauncherError,
                                "P1_LAUNCHER_AUTHORITY_RESIDUE"):
                            LAUNCHER.ProductionExecutor.\
                                _assert_service_owned_watch_private_without_authority()
                    self.assertTrue(raced)
                    self.assertEqual(opens_before_race, 2)

    def test_watch_private_rejects_replacement_after_final_inventory(
            self) -> None:
        for component in ("parent", "leaf"):
            for mutation in ("swap", "aba"):
                with self.subTest(component=component, mutation=mutation), \
                        tempfile.TemporaryDirectory() as temporary:
                    anchor = Path(temporary)
                    parent = anchor / "hepta-shadow-watch-alpha"
                    parent.mkdir(mode=0o700)
                    private = parent / "private"
                    private.mkdir(mode=0o700)
                    target_name = parent.name if component == "parent" \
                        else private.name
                    real_open = LAUNCHER.os.open
                    real_listdir = LAUNCHER.os.listdir
                    target_opens = 0
                    rebound_descriptor = -1
                    raced = False

                    def tracking_open(path, flags, *args, **kwargs):
                        nonlocal target_opens, rebound_descriptor
                        descriptor = real_open(path, flags, *args, **kwargs)
                        if path == target_name:
                            target_opens += 1
                            if target_opens == 2:
                                rebound_descriptor = descriptor
                        return descriptor

                    def listdir_with_race(directory):
                        nonlocal raced
                        names = real_listdir(directory)
                        if directory == rebound_descriptor and not raced:
                            raced = True
                            self._replace_watch_private_component(
                                anchor, parent, private, component,
                                restore=mutation == "aba")
                        return names

                    with mock.patch.multiple(
                            LAUNCHER,
                            WATCH_PRIVATE=private,
                            ROOT_UID=LAUNCHER.os.getuid(),
                            ROOT_GID=LAUNCHER.os.getgid(),
                            WATCH_UID=LAUNCHER.os.getuid(),
                            WATCH_GID=LAUNCHER.os.getgid()), \
                            mock.patch.object(
                                LAUNCHER.os, "open", side_effect=tracking_open), \
                            mock.patch.object(
                                LAUNCHER.os, "listdir",
                                side_effect=listdir_with_race):
                        with self.assertRaisesRegex(
                                LAUNCHER.LauncherError,
                                "P1_LAUNCHER_AUTHORITY_RESIDUE"):
                            LAUNCHER.ProductionExecutor.\
                                _assert_service_owned_watch_private_without_authority()
                    self.assertTrue(raced)

    def test_clean_boundary_rejects_unsafe_bootstrap_lock_inventory(
            self) -> None:
        cases = (
            "extra", "symlink", "hardlink", "content", "mode",
            "directory-mode", "owner",
        )
        for case in cases:
            with self.subTest(case=case), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                sessions = root / "sessions"
                sessions.mkdir(mode=0o711)
                sessions.chmod(0o711)
                private_parent = root / "hepta-shadow-watch-alpha"
                private_parent.mkdir(mode=0o700)
                private = private_parent / "private"
                private.mkdir(mode=0o700)
                lock = sessions / LAUNCHER.SESSION_BOOTSTRAP_LOCK
                if case == "symlink":
                    target = root / "target"
                    target.touch(mode=0o600)
                    lock.symlink_to(target)
                else:
                    lock.touch(mode=0o600)
                    lock.chmod(0o600)
                if case == "extra":
                    (sessions / "session.token").write_text("authority")
                elif case == "hardlink":
                    LAUNCHER.os.link(lock, root / "second-link")
                elif case == "content":
                    lock.write_bytes(b"x")
                elif case == "mode":
                    lock.chmod(0o640)
                elif case == "directory-mode":
                    sessions.chmod(0o700)
                expected_uid = (
                    LAUNCHER.os.getuid() + 1
                    if case == "owner" else LAUNCHER.os.getuid())
                with mock.patch.multiple(
                        LAUNCHER,
                        WATCH_EXPORT=root / "export",
                        WATCH_SESSIONS=sessions,
                        WATCH_PRIVATE=private,
                        ROOT_UID=expected_uid,
                        ROOT_GID=LAUNCHER.os.getgid(),
                        WATCH_UID=LAUNCHER.os.getuid(),
                        WATCH_GID=LAUNCHER.os.getgid()):
                    with self.assertRaisesRegex(
                            LAUNCHER.LauncherError,
                            "P1_LAUNCHER_AUTHORITY_RESIDUE"):
                        LAUNCHER.ProductionExecutor._assert_no_residue()

    def test_clean_boundary_rejects_busy_bootstrap_lock(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            sessions = root / "sessions"
            sessions.mkdir(mode=0o711)
            sessions.chmod(0o711)
            private_parent = root / "hepta-shadow-watch-alpha"
            private_parent.mkdir(mode=0o700)
            private = private_parent / "private"
            private.mkdir(mode=0o700)
            lock = sessions / LAUNCHER.SESSION_BOOTSTRAP_LOCK
            lock.touch(mode=0o600)
            lock.chmod(0o600)
            descriptor = LAUNCHER.os.open(lock, LAUNCHER.os.O_RDWR)
            try:
                LAUNCHER.fcntl.flock(
                    descriptor,
                    LAUNCHER.fcntl.LOCK_EX | LAUNCHER.fcntl.LOCK_NB,
                )
                with mock.patch.multiple(
                        LAUNCHER,
                        WATCH_EXPORT=root / "export",
                        WATCH_SESSIONS=sessions,
                        WATCH_PRIVATE=private,
                        ROOT_UID=LAUNCHER.os.getuid(),
                        ROOT_GID=LAUNCHER.os.getgid(),
                        WATCH_UID=LAUNCHER.os.getuid(),
                        WATCH_GID=LAUNCHER.os.getgid()):
                    with self.assertRaisesRegex(
                            LAUNCHER.LauncherError,
                            "P1_LAUNCHER_AUTHORITY_RESIDUE"):
                        LAUNCHER.ProductionExecutor._assert_no_residue()
            finally:
                LAUNCHER.os.close(descriptor)

    def test_clean_boundary_rejects_directory_inventory_drift(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            sessions = root / "sessions"
            sessions.mkdir(mode=0o711)
            sessions.chmod(0o711)
            private_parent = root / "hepta-shadow-watch-alpha"
            private_parent.mkdir(mode=0o700)
            private = private_parent / "private"
            private.mkdir(mode=0o700)
            real_listdir = LAUNCHER.os.listdir
            changed = False

            def drift_after_scan(directory_fd):
                nonlocal changed
                names = real_listdir(directory_fd)
                if not changed:
                    changed = True
                    (sessions / "session.token").write_text("authority")
                return names

            with mock.patch.multiple(
                    LAUNCHER,
                    WATCH_EXPORT=root / "export",
                    WATCH_SESSIONS=sessions,
                    WATCH_PRIVATE=private,
                    ROOT_UID=LAUNCHER.os.getuid(),
                    ROOT_GID=LAUNCHER.os.getgid(),
                    WATCH_UID=LAUNCHER.os.getuid(),
                    WATCH_GID=LAUNCHER.os.getgid()), \
                    mock.patch.object(
                        LAUNCHER.os, "listdir", side_effect=drift_after_scan):
                with self.assertRaisesRegex(
                        LAUNCHER.LauncherError,
                        "P1_LAUNCHER_AUTHORITY_RESIDUE"):
                    LAUNCHER.ProductionExecutor._assert_no_residue()

    def test_clean_boundary_rechecks_lock_after_final_inventory(self) -> None:
        for mutation in ("content", "mode"):
            with self.subTest(mutation=mutation), \
                    tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                sessions = root / "sessions"
                sessions.mkdir(mode=0o711)
                sessions.chmod(0o711)
                private_parent = root / "hepta-shadow-watch-alpha"
                private_parent.mkdir(mode=0o700)
                private = private_parent / "private"
                private.mkdir(mode=0o700)
                lock = sessions / LAUNCHER.SESSION_BOOTSTRAP_LOCK
                lock.touch(mode=0o600)
                lock.chmod(0o600)
                real_listdir = LAUNCHER.os.listdir
                calls = 0

                def drift_during_final_inventory(directory_fd):
                    nonlocal calls
                    names = real_listdir(directory_fd)
                    calls += 1
                    if calls == 2:
                        if mutation == "content":
                            lock.write_bytes(b"x")
                        else:
                            lock.chmod(0o640)
                    return names

                with mock.patch.multiple(
                        LAUNCHER,
                        WATCH_EXPORT=root / "export",
                        WATCH_SESSIONS=sessions,
                        WATCH_PRIVATE=private,
                        ROOT_UID=LAUNCHER.os.getuid(),
                        ROOT_GID=LAUNCHER.os.getgid(),
                        WATCH_UID=LAUNCHER.os.getuid(),
                        WATCH_GID=LAUNCHER.os.getgid()), \
                        mock.patch.object(
                            LAUNCHER.os, "listdir",
                            side_effect=drift_during_final_inventory):
                    with self.assertRaisesRegex(
                            LAUNCHER.LauncherError,
                            "P1_LAUNCHER_AUTHORITY_RESIDUE"):
                        LAUNCHER.ProductionExecutor._assert_no_residue()

    def test_clean_boundary_rejects_symlinked_parent_component(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            actual = root / "actual"
            actual.mkdir()
            sessions = actual / "sessions"
            sessions.mkdir(mode=0o711)
            sessions.chmod(0o711)
            private_parent = root / "hepta-shadow-watch-alpha"
            private_parent.mkdir(mode=0o700)
            private = private_parent / "private"
            private.mkdir(mode=0o700)
            alias = root / "alias"
            alias.symlink_to(actual, target_is_directory=True)
            with mock.patch.multiple(
                    LAUNCHER,
                    WATCH_EXPORT=root / "export",
                    WATCH_SESSIONS=alias / "sessions",
                    WATCH_PRIVATE=private,
                    ROOT_UID=LAUNCHER.os.getuid(),
                    ROOT_GID=LAUNCHER.os.getgid(),
                    WATCH_UID=LAUNCHER.os.getuid(),
                    WATCH_GID=LAUNCHER.os.getgid()):
                with self.assertRaisesRegex(
                        LAUNCHER.LauncherError,
                        "P1_LAUNCHER_AUTHORITY_RESIDUE"):
                    LAUNCHER.ProductionExecutor._assert_no_residue()

    def test_clean_boundary_rejects_private_directory_metadata(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            sessions = root / "sessions"
            sessions.mkdir(mode=0o711)
            sessions.chmod(0o711)
            private_parent = root / "hepta-shadow-watch-alpha"
            private_parent.mkdir(mode=0o700)
            private = private_parent / "private"
            private.mkdir(mode=0o700)
            private.chmod(0o750)
            with mock.patch.multiple(
                    LAUNCHER,
                    WATCH_EXPORT=root / "export",
                    WATCH_SESSIONS=sessions,
                    WATCH_PRIVATE=private,
                    ROOT_UID=LAUNCHER.os.getuid(),
                    ROOT_GID=LAUNCHER.os.getgid(),
                    WATCH_UID=LAUNCHER.os.getuid(),
                    WATCH_GID=LAUNCHER.os.getgid()):
                with self.assertRaisesRegex(
                        LAUNCHER.LauncherError,
                        "P1_LAUNCHER_AUTHORITY_RESIDUE"):
                    LAUNCHER.ProductionExecutor._assert_no_residue()

    def test_clean_boundary_still_rejects_private_snapshot(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            sessions = root / "sessions"
            sessions.mkdir(mode=0o711)
            sessions.chmod(0o711)
            private_parent = root / "hepta-shadow-watch-alpha"
            private_parent.mkdir(mode=0o700)
            private = private_parent / "private"
            private.mkdir(mode=0o700)
            (private / "snapshot.json").write_text("{}")
            with mock.patch.multiple(
                    LAUNCHER,
                    WATCH_EXPORT=root / "export",
                    WATCH_SESSIONS=sessions,
                    WATCH_PRIVATE=private,
                    ROOT_UID=LAUNCHER.os.getuid(),
                    ROOT_GID=LAUNCHER.os.getgid(),
                    WATCH_UID=LAUNCHER.os.getuid(),
                    WATCH_GID=LAUNCHER.os.getgid()):
                with self.assertRaisesRegex(
                        LAUNCHER.LauncherError,
                        "P1_LAUNCHER_AUTHORITY_RESIDUE"):
                    LAUNCHER.ProductionExecutor._assert_no_residue()

    def test_policy_missing_typed_range_and_artifact_drift_fail_closed(self) -> None:
        mutations = {
            "missing": lambda body: body.pop("valid_after_ms"),
            "wrong-type": lambda body: body.__setitem__(
                "maximum_iterations", True),
            "range": lambda body: body.__setitem__(
                "maximum_iterations", 0),
            "schedule-drift": lambda body: body.__setitem__(
                "expires_at_ms", body["expires_at_ms"] + 1),
        }
        for name, mutation in mutations.items():
            with self.subTest(name=name):
                launcher, executor, store = self.launch()
                executor.formal_policy_mutation = mutation
                with self.assertRaises(LAUNCHER.LauncherError):
                    launcher.run()
                self.assertEqual(store.receipt["status"], "FAILED_CLOSED")
        launcher, executor, store = self.launch()
        executor.artifact_schedule_drift = True
        with self.assertRaisesRegex(
                LAUNCHER.LauncherError,
                "P1_LAUNCHER_POLICY_ARTIFACT_DRIFT"):
            launcher.run()
        self.assertEqual(store.receipt["status"], "FAILED_CLOSED")

    def test_early_or_non_iteration_formal_completion_is_rejected(self) -> None:
        launcher, executor, store = self.launch()
        executor.formal_completed_iterations = LAUNCHER.FORMAL_ITERATIONS - 1
        with self.assertRaisesRegex(
                LAUNCHER.LauncherError,
                "P1_LAUNCHER_FORMAL_RESULT_INVALID"):
            launcher.run()
        self.assertEqual(store.receipt["status"], "FAILED_CLOSED")

        launcher, executor, store = self.launch()
        executor.formal_status = "MAXIMUM_RUNTIME_REACHED"
        with self.assertRaisesRegex(
                LAUNCHER.LauncherError,
                "P1_LAUNCHER_FORMAL_RESULT_INVALID"):
            launcher.run()
        self.assertEqual(store.receipt["status"], "FAILED_CLOSED")

    def test_final_uid1000_evidence_mismatch_is_rejected(self) -> None:
        mutations = (
            lambda _status, state: state.__setitem__(
                "completed_iterations", LAUNCHER.FORMAL_ITERATIONS - 1),
            lambda status, _state: status.__setitem__(
                "last_lease_generation", 999),
            lambda _status, state: state.__setitem__("audit_events", []),
        )
        for mutation in mutations:
            launcher, executor, store = self.launch()
            executor.final_evidence_mutation = mutation
            with self.assertRaisesRegex(
                    LAUNCHER.LauncherError,
                    "P1_LAUNCHER_FINAL_READER_EVIDENCE_INVALID"):
                launcher.run()
            self.assertEqual(store.receipt["status"], "FAILED_CLOSED")

    def test_host_reader_completion_object_is_exact_and_bound(self) -> None:
        mutations = (
            lambda completion: completion.__setitem__("reader_pid", 999),
            lambda completion: completion.__setitem__(
                "controller_status_file_sha256", "sha256:" + "0" * 64),
            lambda completion: completion.__setitem__(
                "observer_state_body_sha256", "sha256:" + "0" * 64),
            lambda completion: completion.__setitem__("extra", False),
        )
        for mutation in mutations:
            launcher, executor, store = self.launch()
            executor.reader_completion_mutation = mutation
            with self.assertRaisesRegex(
                    LAUNCHER.LauncherError,
                    "P1_LAUNCHER_READER_COMPLETION_INVALID"):
                launcher.run()
            self.assertEqual(store.receipt["status"], "FAILED_CLOSED")

    def test_post_verifier_reader_death_or_pid_change_is_rejected(self) -> None:
        launcher, executor, store = self.launch()
        executor.reader_active = False
        with self.assertRaisesRegex(
                LAUNCHER.LauncherError,
                "P1_LAUNCHER_READER_IDENTITY_INVALID"):
            launcher.run()
        self.assertEqual(store.receipt["status"], "FAILED_CLOSED")

        launcher, executor, store = self.launch()
        executor.reader_active_pid = 999
        with self.assertRaisesRegex(
                LAUNCHER.LauncherError,
                "P1_LAUNCHER_POST_VERIFIER_READER_EVIDENCE_INVALID"):
            launcher.run()
        self.assertEqual(store.receipt["status"], "FAILED_CLOSED")

    def test_post_verifier_stale_heartbeat_or_observer_drift_is_rejected(
            self) -> None:
        launcher, executor, store = self.launch()
        executor.post_verifier_evidence_mutation = (
            lambda status, _state: status.__setitem__(
                "updated_at_ms",
                time.time_ns() // 1_000_000 -
                LAUNCHER.TERMINAL_HEARTBEAT_MAXIMUM_AGE_MS - 1))
        with self.assertRaisesRegex(
                LAUNCHER.LauncherError,
                "P1_LAUNCHER_POST_VERIFIER_READER_EVIDENCE_INVALID"):
            launcher.run()
        self.assertEqual(store.receipt["status"], "FAILED_CLOSED")

        launcher, executor, store = self.launch()
        executor.post_verifier_evidence_mutation = (
            lambda _status, state: state.__setitem__(
                "sample_count", state["sample_count"] + 1))
        with self.assertRaisesRegex(
                LAUNCHER.LauncherError,
                "P1_LAUNCHER_POST_VERIFIER_READER_EVIDENCE_INVALID"):
            launcher.run()
        self.assertEqual(store.receipt["status"], "FAILED_CLOSED")

    def test_verified_closure_tamper_and_short_span_are_rejected(self) -> None:
        mutations = (
            lambda body: body.__setitem__(
                "completed_iterations", LAUNCHER.FORMAL_ITERATIONS - 1),
            lambda body: body["iterations"][-1].__setitem__(
                "scheduled_at_ms",
                body["iterations"][-1]["scheduled_at_ms"] - 1),
            lambda body: body["iterations"][7].__setitem__(
                "evaluated_at_ms",
                body["iterations"][7]["scheduled_at_ms"] +
                LAUNCHER.POLICY_MAXIMUM_LATENESS_MS + 1),
            lambda body: body["segments"][0].__setitem__(
                "segment_index", 2),
            lambda body: body.__setitem__("policy_file_sha256", "bad"),
            lambda body: body.__setitem__(
                "observer_state_file_sha256", "sha256:" + "0" * 64),
        )
        for mutation in mutations:
            launcher, executor, store = self.launch()
            executor.verified_closure_mutation = mutation
            with self.assertRaisesRegex(
                    LAUNCHER.LauncherError,
                    "P1_LAUNCHER_VERIFIED_CLOSURE_INVALID"):
                launcher.run()
            self.assertEqual(store.receipt["status"], "FAILED_CLOSED")

        launcher, executor, store = self.launch()
        executor.verified_closure_post_mutation = (
            lambda document: document.__setitem__(
                "body_sha256", "sha256:" + "0" * 64))
        with self.assertRaisesRegex(
                LAUNCHER.LauncherError,
                "P1_LAUNCHER_VERIFIED_CLOSURE_INVALID"):
            launcher.run()
        self.assertEqual(store.receipt["status"], "FAILED_CLOSED")

    def test_verifier_failure_is_fail_closed(self) -> None:
        launcher, executor, store = self.launch(
            fail_at="verify_formal_closure")
        with self.assertRaisesRegex(
                LAUNCHER.LauncherError,
                "INJECTED_VERIFY_FORMAL_CLOSURE"):
            launcher.run()
        self.assertEqual(store.receipt["status"], "FAILED_CLOSED")
        self.assertFalse(store.receipt["authority_residue"])
        self.assertFalse(store.receipt["export_residue"])

    def test_final_helper_and_paper_rechecks_are_fail_closed(self) -> None:
        launcher, executor, store = self.launch()
        executor.helper_drift_at = "helper_hashes:5"
        with self.assertRaisesRegex(
                LAUNCHER.LauncherError,
                "P1_LAUNCHER_HELPER_DRIFT_AFTER_FORMAL_HOST"):
            launcher.run()
        self.assertEqual(store.receipt["status"], "FAILED_CLOSED")
        self.assertNotIn("verify_formal_closure", executor.actions)

        launcher, executor, store = self.launch()
        executor.helper_drift_at = "helper_hashes:6"
        with self.assertRaisesRegex(
                LAUNCHER.LauncherError,
                "P1_LAUNCHER_HELPER_DRIFT_AFTER_VERIFIER"):
            launcher.run()
        self.assertEqual(store.receipt["status"], "FAILED_CLOSED")
        self.assertIn("verify_formal_closure", executor.actions)
        self.assertNotIn("assert_reader_active", executor.actions)

        launcher, executor, store = self.launch()
        executor.paper_active_at = "assert_paper_inactive:6"
        with self.assertRaisesRegex(
                LAUNCHER.LauncherError, "FAKE_PAPER_ACTIVE"):
            launcher.run()
        self.assertEqual(store.receipt["status"], "FAILED_CLOSED")
        self.assertIn("verify_formal_closure", executor.actions)
        self.assertNotIn("assert_reader_active", executor.actions)

        launcher, executor, store = self.launch()
        executor.paper_active_at = "assert_paper_inactive:5"
        with self.assertRaisesRegex(
                LAUNCHER.LauncherError, "FAKE_PAPER_ACTIVE"):
            launcher.run()
        self.assertEqual(store.receipt["status"], "FAILED_CLOSED")
        self.assertNotIn("verify_formal_closure", executor.actions)

    def test_paper_activity_and_helper_drift_are_fail_closed(self) -> None:
        launcher, executor, store = self.launch()
        executor.paper_active = True
        with self.assertRaisesRegex(
                LAUNCHER.LauncherError, "FAKE_PAPER_ACTIVE"):
            launcher.run()
        self.assertNotIn("build_policy:load-probe", executor.actions)
        self.assertEqual(store.receipt["status"], "FAILED_CLOSED")

        launcher, executor, store = self.launch()
        executor.helper_drift_at = "helper_hashes:2"
        with self.assertRaisesRegex(
                LAUNCHER.LauncherError,
                "P1_LAUNCHER_HELPER_DRIFT_BEFORE_VALIDATION"):
            launcher.run()
        self.assertEqual(store.receipt["status"], "FAILED_CLOSED")

    def test_real_executor_allows_shared_simulator_sockets(self) -> None:
        executor = LAUNCHER.ProductionExecutor()
        calls = []

        def inactive(arguments, _timeout):
            calls.append(arguments)
            return subprocess.CompletedProcess(
                arguments, 0,
                stdout="ActiveState=inactive\nSubState=dead\n",
                stderr="")

        executor._run = inactive
        shared_sockets = set(LAUNCHER.SHARED_EXECUTION_SOCKET_PATHS)
        with mock.patch.object(
                LAUNCHER.os.path, "lexists",
                side_effect=lambda path: path in shared_sockets) as lexists:
            states = executor.assert_paper_inactive()

        self.assertEqual(set(states), set(LAUNCHER.PAPER_UNITS))
        self.assertEqual(len(calls), len(LAUNCHER.PAPER_UNITS))
        lexists.assert_called_once_with(
            LAUNCHER.PAPER_OPERATOR_SOCKET_PATHS[0])

    def test_real_executor_rejects_unique_campaign_operator_socket(
            self) -> None:
        executor = LAUNCHER.ProductionExecutor()
        executor._run = lambda arguments, _timeout: subprocess.CompletedProcess(
            arguments, 0,
            stdout="ActiveState=inactive\nSubState=dead\n",
            stderr="")
        operator_socket = LAUNCHER.PAPER_OPERATOR_SOCKET_PATHS[0]
        with mock.patch.object(
                LAUNCHER.os.path, "lexists",
                side_effect=lambda path: path == operator_socket):
            with self.assertRaisesRegex(
                    LAUNCHER.LauncherError,
                    "P1_LAUNCHER_PAPER_SOCKET_PRESENT"):
                executor.assert_paper_inactive()

    def test_real_executor_rejects_active_or_malformed_paper_unit_state(
            self) -> None:
        executor = LAUNCHER.ProductionExecutor()
        with mock.patch.object(
                LAUNCHER.os.path, "lexists", return_value=False):
            executor._run = (
                lambda arguments, _timeout: subprocess.CompletedProcess(
                    arguments, 0,
                    stdout="ActiveState=active\nSubState=running\n",
                    stderr=""))
            with self.assertRaisesRegex(
                    LAUNCHER.LauncherError,
                    "P1_LAUNCHER_PAPER_ACTIVE"):
                executor.assert_paper_inactive()

            malformed_outputs = (
                "ActiveState=inactive\n",
                "ActiveState=inactive\nSubState=dead\nUnexpected=value\n",
                "ActiveState=inactive\nActiveState=inactive\nSubState=dead\n",
                "ActiveState=inactive\nSubState\n",
            )
            for stdout in malformed_outputs:
                with self.subTest(stdout=stdout):
                    executor._run = (
                        lambda arguments, _timeout, output=stdout:
                        subprocess.CompletedProcess(
                            arguments, 0, stdout=output, stderr=""))
                    with self.assertRaisesRegex(
                            LAUNCHER.LauncherError,
                            "P1_LAUNCHER_PAPER_UNIT_STATE_INVALID"):
                        executor.assert_paper_inactive()

    def test_outer_unit_exact_identity_and_environment_are_bound(self) -> None:
        command = LAUNCHER.launcher_command(self.configuration)
        environment = " ".join(
            f"{field}={value}"
            for field, value in LAUNCHER.SANITIZED_ENVIRONMENT.items())
        stdout = (
            "ActiveState=active\nSubState=running\n"
            f"InvocationID={'c' * 32}\nMainPID=1234\n"
            "Type=exec\nRestart=no\nRemainAfterExit=no\n"
            "User=root\nGroup=root\n"
            f"ExecStart={{ path={LAUNCHER.LAUNCHER_EXECUTABLE} ; "
            f"argv[]={' '.join(command)} ; ignore_errors=no ; "
            "start_time=[n/a] ; stop_time=[n/a] ; pid=1234 ; "
            "code=(null) ; status=0/0 }\n"
            f"Environment={environment}\n"
            f"Conflicts={' '.join(LAUNCHER.PAPER_UNITS)}\n")
        executor = LAUNCHER.ProductionExecutor()
        executor._run = lambda arguments, _timeout: subprocess.CompletedProcess(
            arguments, 0, stdout=stdout, stderr="")
        with mock.patch.object(LAUNCHER.os, "geteuid", return_value=0), \
                mock.patch.object(LAUNCHER.os, "getegid", return_value=0), \
                mock.patch.object(LAUNCHER, "_secure_read", return_value=b"x"):
            identity = executor.launcher_identity(
                "hepta-p1-shadow-admission-round102.service", 1234,
                self.configuration)
        self.assertEqual(identity["exec_start"], command)
        self.assertEqual(
            identity["environment"], LAUNCHER.SANITIZED_ENVIRONMENT)
        self.assertEqual(identity["conflicts"], list(LAUNCHER.PAPER_UNITS))

        executor._run = lambda arguments, _timeout: subprocess.CompletedProcess(
            arguments, 0,
            stdout=stdout.replace(
                f"Environment={environment}",
                f"Environment={environment} EXTRA=1"),
            stderr="")
        with mock.patch.object(LAUNCHER.os, "geteuid", return_value=0), \
                mock.patch.object(LAUNCHER.os, "getegid", return_value=0):
            with self.assertRaisesRegex(
                    LAUNCHER.LauncherError,
                    "P1_LAUNCHER_UNIT_IDENTITY_INVALID"):
                executor.launcher_identity(
                    "hepta-p1-shadow-admission-round102.service", 1234,
                    self.configuration)

        missing_conflict = stdout.replace(
            f"Conflicts={' '.join(LAUNCHER.PAPER_UNITS)}\n",
            "Conflicts=" + " ".join(LAUNCHER.PAPER_UNITS[:-1]) + "\n",
        )
        executor._run = lambda arguments, _timeout: subprocess.CompletedProcess(
            arguments, 0, stdout=missing_conflict, stderr="")
        with mock.patch.object(LAUNCHER.os, "geteuid", return_value=0), \
                mock.patch.object(LAUNCHER.os, "getegid", return_value=0):
            with self.assertRaisesRegex(
                    LAUNCHER.LauncherError,
                    "P1_LAUNCHER_UNIT_IDENTITY_INVALID"):
                executor.launcher_identity(
                    "hepta-p1-shadow-admission-round102.service", 1234,
                    self.configuration)

    def test_wrong_or_missing_launcher_unit_identity_is_rejected(self) -> None:
        executor = LAUNCHER.ProductionExecutor()
        wrong_pid = subprocess.CompletedProcess(
            [LAUNCHER.SYSTEMCTL], 0,
            stdout=(
                "ActiveState=active\nSubState=running\n"
                f"InvocationID={'c' * 32}\nMainPID=999999\n"
                "Type=exec\nRestart=no\nRemainAfterExit=no\n"
                "User=root\nGroup=root\nExecStart=invalid\nEnvironment=invalid\n"),
            stderr="")
        executor._run = lambda _arguments, _timeout: wrong_pid
        with mock.patch.object(LAUNCHER.os, "geteuid", return_value=0), \
                mock.patch.object(LAUNCHER.os, "getegid", return_value=0):
            with self.assertRaisesRegex(
                    LAUNCHER.LauncherError,
                    "P1_LAUNCHER_UNIT_IDENTITY_INVALID"):
                executor.launcher_identity(
                    "hepta-p1-shadow-admission-round102.service", 1234,
                    self.configuration)
        with self.assertRaisesRegex(
                LAUNCHER.LauncherError,
                "P1_LAUNCHER_UNIT_IDENTITY_INVALID"):
            executor.launcher_identity(
                "wrong.service", 1234, self.configuration)

    def test_subprocess_surface_is_sanitized_and_never_uses_shell(self) -> None:
        completed = subprocess.CompletedProcess(
            [LAUNCHER.SYSTEMCTL], 0, stdout="", stderr="")
        with mock.patch.object(
            LAUNCHER.subprocess, "run", return_value=completed) as invoked:
            LAUNCHER.ProductionExecutor()._run(
                [LAUNCHER.SYSTEMCTL, "is-active", LAUNCHER.GATEWAY_UNIT], 5)
        arguments, keywords = invoked.call_args
        self.assertEqual(arguments[0][0], "/usr/bin/systemctl")
        self.assertNotIn("shell", keywords)
        self.assertTrue(keywords["close_fds"])
        self.assertEqual(keywords["cwd"], "/")
        self.assertEqual(keywords["env"], LAUNCHER.SANITIZED_ENVIRONMENT)


if __name__ == "__main__":
    unittest.main()
