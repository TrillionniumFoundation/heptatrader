#!/usr/bin/env python3

"""Focused seams for the fixed round114 WATCH activation transaction."""

from __future__ import annotations

from contextlib import ExitStack
import copy
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import shutil
import stat
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
SOURCE = ROOT / "scripts/hepta_p1_watch_activation_transaction.py"
SPEC = importlib.util.spec_from_file_location("hepta_watch_activation", SOURCE)
assert SPEC is not None and SPEC.loader is not None
ACTIVATION = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = ACTIVATION
SPEC.loader.exec_module(ACTIVATION)
LAUNCHER_SOURCE = ROOT / "scripts/hepta_p1_shadow_admission_launcher.py"
LAUNCHER_SPEC = importlib.util.spec_from_file_location(
    "hepta_shadow_launcher_contract", LAUNCHER_SOURCE)
assert LAUNCHER_SPEC is not None and LAUNCHER_SPEC.loader is not None
LAUNCHER = importlib.util.module_from_spec(LAUNCHER_SPEC)
sys.modules[LAUNCHER_SPEC.name] = LAUNCHER
LAUNCHER_SPEC.loader.exec_module(LAUNCHER)
PROFILE_SOURCE = ROOT / "scripts/hepta_p1_watch_profile_deployer.py"
PROFILE_SPEC = importlib.util.spec_from_file_location(
    "hepta_profile_artifact_contract_for_activation_tests", PROFILE_SOURCE)
assert PROFILE_SPEC is not None and PROFILE_SPEC.loader is not None
PROFILE_PRODUCER = importlib.util.module_from_spec(PROFILE_SPEC)
sys.modules[PROFILE_SPEC.name] = PROFILE_PRODUCER
PROFILE_SPEC.loader.exec_module(PROFILE_PRODUCER)


SHA = "sha256:" + "1" * 64


def predecessor_evidence() -> dict[str, object]:
    return {
        "receipt_path": str(ACTIVATION.PREDECESSOR_FAILED_RECEIPT_PATH),
        "receipt_file_sha256":
            ACTIVATION.PREDECESSOR_FAILED_RECEIPT_FILE_SHA256,
        "receipt_body_sha256":
            ACTIVATION.PREDECESSOR_FAILED_RECEIPT_BODY_SHA256,
        "receipt_schema":
            "hepta.p1-watch-activation-failed-receipt.v2",
        "receipt_version": 2, "receipt_revision": 1,
        "receipt_status": "FAILED_CLOSED", "receipt_round": 95,
        "receipt_domain": "alpha",
        "receipt_reason": "ACTIVATION_SYSTEMCTL_FAILED",
        "receipt_device": 1, "receipt_inode": 2,
        "receipt_mode": stat.S_IFREG | 0o600, "receipt_nlink": 1,
        "receipt_uid": ACTIVATION.ROOT_UID,
        "receipt_gid": ACTIVATION.ROOT_GID, "receipt_bytes": 1024,
        "receipt_mtime_ns": 1, "receipt_ctime_ns": 1,
        "journal_path": str(ACTIVATION.PREDECESSOR_JOURNAL_ROOT),
        "journal_sha256": ACTIVATION.PREDECESSOR_JOURNAL_SHA256,
        "journal_record_count": 21,
        "journal_terminal_phase": "FAILED_CLOSED",
    }


def predecessor_success_evidence() -> dict[str, object]:
    return {
        "receipt_path": str(ACTIVATION.PREDECESSOR_ACTIVATION_RECEIPT_PATH),
        "receipt_file_sha256":
            ACTIVATION.PREDECESSOR_ACTIVATION_RECEIPT_FILE_SHA256,
        "receipt_body_sha256":
            ACTIVATION.PREDECESSOR_ACTIVATION_RECEIPT_BODY_SHA256,
        "receipt_schema": "hepta.p1-watch-activation-receipt.v3",
        "receipt_version": 3,
        "receipt_status": "WATCH_GATEWAY_ACTIVATED",
        "receipt_round": 95,
        "receipt_domain": "alpha",
        "receipt_device": 1, "receipt_inode": 3,
        "receipt_mode": stat.S_IFREG | 0o600, "receipt_nlink": 1,
        "receipt_uid": ACTIVATION.ROOT_UID,
        "receipt_gid": ACTIVATION.ROOT_GID, "receipt_bytes": 4096,
        "receipt_mtime_ns": 1, "receipt_ctime_ns": 1,
    }


def shadow_install_evidence(
    *,
    uid: int | None = None,
    gid: int | None = None,
    manifest_file_sha256: str = SHA,
    receipt_file_sha256: str = SHA,
) -> dict[str, object]:
    """Return strict, passive round114 install-consumption evidence."""

    return {
        "schema": "hepta.shadow-runtime-install-consumption-evidence.v3",
        "version": 3,
        "receipt_path": str(ACTIVATION.SHADOW_INSTALL_RECEIPT_PATH),
        "receipt_file_sha256": receipt_file_sha256,
        "receipt_body_sha256": SHA,
        "manifest_path": str(ACTIVATION.SHADOW_INSTALL_MANIFEST_PATH),
        "manifest_file_sha256": manifest_file_sha256,
        "current_install_pointer_path":
            str(ACTIVATION.SHADOW_CURRENT_INSTALL_POINTER_PATH),
        "current_install_pointer_file_sha256": SHA,
        "install_generation": ACTIVATION.EXPECTED_SHADOW_INSTALL_GENERATION,
        "predecessor_install_generation":
            ACTIVATION.EXPECTED_PREDECESSOR_SHADOW_INSTALL_GENERATION,
        "predecessor_current_install_pointer_file_sha256":
            ACTIVATION.EXPECTED_PREDECESSOR_CURRENT_INSTALL_POINTER_FILE_SHA256,
        "archive_sha256": SHA,
        "source_baseline_sha256": SHA,
        "installer_sha256": SHA,
        "installed_file_count": ACTIVATION.SHADOW_INSTALL_FILE_COUNT,
        "installed_paths_sha256": SHA,
        "closure_sha256": SHA,
        "transaction_lock": {
            "path": str(ACTIVATION.SHADOW_INSTALL_LOCK_PATH),
            "device": 1,
            "inode": 2,
            "nlink": 1,
            "uid": ACTIVATION.ROOT_UID if uid is None else uid,
            "gid": ACTIVATION.ROOT_GID if gid is None else gid,
            "mode": "0600",
            "size": 0,
            "mtime_ns": 1,
            "ctime_ns": 1,
            "created_during_transaction": True,
            "persistent": True,
            "held_during_transaction": True,
        },
        "default_deny_identity_sha256":
            ACTIVATION.SHADOW_DEFAULT_DENY_IDENTITY_SHA256,
        "lock_mode": "exclusive",
        "verified_under_lock": True,
        "domain": ACTIVATION.DOMAIN,
        "backup_root": str(ACTIVATION.SHADOW_INSTALL_BACKUP_ROOT),
        "paper_authorized": False,
        "live_authorized": False,
        "mutation_attempted": False,
        "direct_broker_access": False,
    }


def broker_evidence() -> dict[str, object]:
    return {
        "unit": ACTIVATION.BROKER_UNIT,
        "active_state": "active", "sub_state": "running", "main_pid": 321,
        "invocation_id": "a" * 32,
        "exec_main_start_timestamp_monotonic_us": 100,
        "process_starttime_ticks": 10,
        "interpreter_path": str(ACTIVATION.PYTHON),
        "interpreter_sha256": SHA,
        "credential_source_path":
            "/run/credentials/hepta-broker-egress-policy.service/"
            "hepta-broker-egress-policy.py",
        "credential_source_sha256": SHA,
        "installed_source_path": str(ACTIVATION.BROKER_HELPER),
        "installed_source_sha256": SHA, "cmdline_sha256": SHA,
        "status_text": "HeptaTrader broker boundary exact deny-all",
        "tasks_current": 1, "deny_all_policy_sha256": SHA,
        "authorized_connectors": 0, "authorized_uids": [],
        "protected_ports": 4, "unit_contract_sha256": SHA,
    }


def gateway_evidence() -> dict[str, object]:
    return {
        "unit": ACTIVATION.GATEWAY_SERVICE,
        "active_state": "active", "sub_state": "running",
        "gateway_main_pid": 456, "gateway_invocation_id": "b" * 32,
        "gateway_exec_main_start_timestamp_monotonic_us": 200,
        "process_starttime_ticks": 20,
        "gateway_executable_path": str(ACTIVATION.GATEWAY_EXECUTABLE),
        "gateway_executable_sha256": SHA, "domain_config_sha256": SHA,
        "gateway_profile_path": str(ACTIVATION.PROFILE_PATH),
        "gateway_profile_sha256": "sha256:" + ACTIVATION.PROFILE_SHA256,
        "gateway_process_profile_sha256": SHA,
        "execution_remote_mode": "SIMULATOR", "tool_account": "SIM",
        "execution_domain_id": "SIM:alpha", "tool_allow_trade": "0",
        "session_templates": "watch",
        "contract_bindings": "EUR.USD|EUR|CASH|IDEALPRO|USD",
        "gateway_socket_path": "/run/hepta-agent-alpha/tools.sock",
        "gateway_socket_device": 1, "gateway_socket_inode": 2,
        "supervisor_socket_path":
            "/run/hepta-tool-gateway-alpha/session-supervisor.sock",
        "supervisor_socket_device": 1, "supervisor_socket_inode": 3,
        "unit_contract_sha256": SHA,
    }


def quarantine_gateway_evidence() -> dict[str, object]:
    manager = {
        unit: {
            "LoadState": "masked", "ActiveState": "inactive",
            "SubState": "dead", "Job": "", "UnitFileState": "masked",
        }
        for unit in ACTIVATION.GATEWAY_UNITS
    }
    masks: dict[str, object] = {}
    inode = 100
    for unit in ACTIVATION.GATEWAY_UNITS:
        masks[unit] = {}
        for scope, root in (
                ("persistent", ACTIVATION.PERSISTENT_SYSTEMD_ROOT),
                ("runtime", ACTIVATION.RUNTIME_SYSTEMD_ROOT)):
            inode += 1
            masks[unit][scope] = {
                "path": str(root / unit), "target": ACTIVATION.MASK_TARGET,
                "device": 1, "inode": inode,
                "mode": stat.S_IFLNK | 0o777, "nlink": 1,
                "uid": ACTIVATION.ROOT_UID, "gid": ACTIVATION.ROOT_GID,
                "bytes": len(ACTIVATION.MASK_TARGET),
                "mtime_ns": 1, "ctime_ns": 1,
            }
    body = {"manager_units": manager, "masks": masks}
    return {
        **body,
        "unit_contract_sha256": ACTIVATION.digest_bytes(
            ACTIVATION.canonical_bytes(body)),
    }


class FakeExecutor:
    def __init__(
        self, *, fail_gateway: bool = False, drift: bool = False,
        paper_active: bool = False, paper_active_after_preflight: bool = False,
        timer_substate: str = "waiting",
        broker_unit_contract_sha256: str = SHA,
        gateway_unit_contract_sha256: str = SHA,
    ):
        self.mutations: list[list[str]] = []
        self.fail_gateway = fail_gateway
        self.drift = drift
        self.paper_active = paper_active
        self.paper_active_after_preflight = paper_active_after_preflight
        self.timer_substate = timer_substate
        self.broker_unit_contract_sha256 = broker_unit_contract_sha256
        self.gateway_unit_contract_sha256 = gateway_unit_contract_sha256
        self.paper_samples = 0
        self.quarantine_called = False
        self.quarantine_evidence: dict[str, object] | None = None

    def preflight(self) -> dict[str, object]:
        return {
            "paper_units": self.attest_paper_inactive(),
            "deny_all": self.deny_all(),
        }

    def mutate(self, arguments: tuple[str, ...]) -> None:
        if self.fail_gateway and arguments == (
                ACTIVATION.SYSTEMCTL, "start", ACTIVATION.GATEWAY_SERVICE):
            raise ACTIVATION.ActivationError("ACTIVATION_SYSTEMCTL_FAILED")
        self.mutations.append(list(arguments))

    def stop_broker(self) -> None:
        return None

    def attest_reconcile_timer(self) -> dict[str, str]:
        raw = {"LoadState": "loaded", "ActiveState": "active",
               "SubState": self.timer_substate, "Job": "",
               "UnitFileState": "enabled"}
        return {
            "unit": ACTIVATION.RECONCILE_TIMER,
            "load_state": "loaded", "active_state": "active",
            "sub_state": self.timer_substate, "job": "",
            "unit_file_state": "enabled",
            "unit_contract_sha256": ACTIVATION.digest_bytes(
                ACTIVATION.canonical_bytes(raw)),
        }

    def attest_paper_inactive(self) -> dict[str, object]:
        self.paper_samples += 1
        if self.paper_active or (
                self.paper_active_after_preflight and self.paper_samples > 1):
            raise ACTIVATION.ActivationError("ACTIVATION_PAPER_ACTIVE")
        return {
            unit: {"ActiveState": "inactive", "SubState": "dead", "Job": ""}
            for unit in ACTIVATION.PAPER_UNITS
        }

    def deny_all(self, *, tighten: bool = False) -> dict[str, object]:
        return {"policy_sha256": SHA, "authorized_connectors": 0,
                "authorized_uids": [], "protected_ports": 4}

    def attest_broker(self) -> dict[str, object]:
        value = broker_evidence()
        value["unit_contract_sha256"] = self.broker_unit_contract_sha256
        return value

    def attest_gateway(self) -> dict[str, object]:
        value = gateway_evidence()
        value["unit_contract_sha256"] = self.gateway_unit_contract_sha256
        if self.drift:
            value["gateway_socket_inode"] = 999
        return value

    def quarantine(self) -> dict[str, object]:
        self.quarantine_called = True
        result = {
            "errors": [],
            "gateway_masked_stopped": quarantine_gateway_evidence(),
            "deny_all": self.deny_all(), "complete": True,
        }
        self.quarantine_evidence = copy.deepcopy(result)
        return result


def profile_preflight(exec_main_pid: int) -> dict[str, object]:
    gateway = {}
    masks = {}
    for unit in ACTIVATION.GATEWAY_UNITS:
        gateway[unit] = {
            "LoadState": "masked", "ActiveState": "inactive",
            "SubState": "dead", "Job": "", "UnitFileState": "masked",
        }
        masks[unit] = {
            "persistent": {"path": f"/etc/systemd/system/{unit}",
                           "target": "/dev/null"},
            "runtime": {"path": f"/run/systemd/system/{unit}",
                        "target": "/dev/null"},
        }
    broker = {
        "Id": ACTIVATION.BROKER_UNIT, "Names": ACTIVATION.BROKER_UNIT,
        "LoadState": "loaded", "ActiveState": "inactive",
        "SubState": "dead", "UnitFileState": "enabled",
        "FragmentPath":
            "/usr/lib/systemd/system/hepta-broker-egress-policy.service",
        "SourcePath": "", "DropInPaths": "", "NeedDaemonReload": "yes",
        "Job": "", "MainPID": 0, "ExecMainPID": exec_main_pid,
        "ControlPID": 0,
    }
    return {
        "gateway_units": gateway, "gateway_masks": masks,
        "gateway_unit_closure": {}, "systemd_manager": {},
        "manager_unit_contracts": {unit: {} for unit in ACTIVATION.GATEWAY_UNITS},
        "broker_egress_unit": broker,
        "broker_egress_check": {
            "helper_path": str(ACTIVATION.BROKER_HELPER),
            "helper_sha256": SHA, "helper_bytes": 1,
            "argv": ["--check-deny-all"], "policy_sha256": SHA,
            "authorized_connectors": 0, "authorized_uids": [],
            "protected_ports": 4, "status": "PASS",
        },
        "paper_units": {
            unit: {"LoadState": "loaded", "ActiveState": "inactive",
                   "SubState": "dead", "Job": ""}
            for unit in ACTIVATION.PAPER_UNITS
        },
        "campaign_policy_count": 0, "kill_switch_engaged": True,
        "watch_boundary": {}, "broker_egress_deny_all_observed": True,
    }


def profile_receipt(
    exec_main_pid: int,
    *,
    install_evidence: dict[str, object] | None = None,
    uid: int = 0,
    gid: int = 0,
) -> bytes:
    before = profile_preflight(exec_main_pid)
    def file_evidence(
        path: Path, sha256: str, size: int, mode: int, inode: int,
    ) -> dict[str, object]:
        return {
            "path": str(path), "sha256": sha256, "bytes": size,
            "device": 1, "inode": inode,
            "mode": stat.S_IFREG | mode, "nlink": 1,
            "uid": uid, "gid": gid, "mtime_ns": 1, "ctime_ns": 1,
        }
    target = file_evidence(
        ACTIVATION.PROFILE_PATH, "sha256:" + ACTIVATION.PROFILE_SHA256,
        len(ACTIVATION.PROFILE_PAYLOAD), 0o644, 2)
    legacy_receipt = {
        **file_evidence(
            ACTIVATION.LEGACY_PROFILE_RECEIPT_PATH,
            ACTIVATION.LEGACY_PROFILE_RECEIPT_FILE_SHA256,
            ACTIVATION.LEGACY_PROFILE_RECEIPT_BYTES, 0o600, 3),
        "body_sha256": ACTIVATION.LEGACY_PROFILE_RECEIPT_BODY_SHA256,
    }
    predecessor_profile_receipt = {
        **file_evidence(
            ACTIVATION.PREDECESSOR_PROFILE_RECEIPT_PATH,
            ACTIVATION.PREDECESSOR_PROFILE_RECEIPT_FILE_SHA256,
            ACTIVATION.PREDECESSOR_PROFILE_RECEIPT_BYTES, 0o600, 6),
        "body_sha256":
            ACTIVATION.PREDECESSOR_PROFILE_RECEIPT_BODY_SHA256,
    }
    transition_receipt = {
        **file_evidence(
            ACTIVATION.PROFILE_TRANSITION_RECEIPT_PATH,
            "sha256:" + "7" * 64, 4096, 0o600, 7),
        "body_sha256": "sha256:" + "8" * 64,
    }
    body: dict[str, object] = {
        field: None for field in ACTIVATION.PROFILE_RECEIPT_FIELDS
        if field != "body_sha256"
    }
    body.update({
        "schema": "hepta.p1-watch-profile-deployment-receipt.v8",
        "version": 8, "status": "OFFLINE_PASSIVE_WATCH_PROFILE_REATTESTED",
        "round": 114, "domain": "alpha", "started_at_ms": 1,
        "finished_at_ms": 2, "target_path": str(ACTIVATION.PROFILE_PATH),
        "receipt_staging_path": str(ACTIVATION.PROFILE_RECEIPT_STAGING_PATH),
        "target_before": target,
        "target_after": copy.deepcopy(target),
        "target_final": copy.deepcopy(target),
        "legacy_receipt": legacy_receipt,
        "legacy_backup": file_evidence(
            ACTIVATION.LEGACY_PROFILE_BACKUP_PATH,
            "sha256:" + ACTIVATION.LEGACY_PROFILE_SHA256,
            ACTIVATION.LEGACY_PROFILE_BYTES, 0o600, 4),
        "legacy_retained_target": file_evidence(
            ACTIVATION.LEGACY_PROFILE_RETAINED_TARGET_PATH,
            "sha256:" + ACTIVATION.LEGACY_PROFILE_SHA256,
            ACTIVATION.LEGACY_PROFILE_BYTES, 0o644, 5),
        "preflight_before": before,
        "preflight_after": copy.deepcopy(before),
        "preflight_final": copy.deepcopy(before),
        "profile_content_changed": False,
        "target_written": False,
        "target_replaced": False,
        "services_started": False, "services_stopped": False,
        "services_restarted": False, "campaign_launched": False,
        "paper_authorized": False, "live_authorized": False,
        "mutation_attempted": False, "direct_broker_access": False,
        "activation_receipt_eligible": False,
        "preflight_reusable_for_activation": False,
        "broker_loaded_source_attested": False,
        "broker_deny_all_continuity_attested": False,
        "fresh_activation_transaction_required": True,
        "predecessor_profile_receipt": predecessor_profile_receipt,
        "dormant_paper_to_watch_transition_receipt": transition_receipt,
        "shadow_install_evidence": copy.deepcopy(
            shadow_install_evidence()
            if install_evidence is None else install_evidence),
    })
    return ACTIVATION.canonical_bytes(ACTIVATION.seal(body))


class TransactionFixture(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory(dir=ROOT)
        self.root = Path(self.temporary.name)
        uid, gid = os.geteuid(), os.getegid()
        self.shadow_evidence = shadow_install_evidence(uid=uid, gid=gid)
        self.shadow_current_evidence = copy.deepcopy(self.shadow_evidence)
        self.shadow_binding = object()
        self.profile_binding = SimpleNamespace(document={})
        self.profile_binding_validation_count = 0
        self.shadow_guard_held = False
        self.shadow_guard_events: list[str] = []
        self.shadow_quarantine_guard = object()
        self.shadow_quarantine_guard_held = False
        self.shadow_quarantine_guard_events: list[str] = []
        self.state = self.root / "state"
        self.journal = self.state / "journal"
        self.receipts = self.root / "receipts"
        self.state.mkdir(mode=0o700)
        self.journal.mkdir(mode=0o700)
        self.receipts.mkdir(mode=0o700)
        self.profile_receipt = self.root / "profile-receipt.json"
        self.profile_receipt.write_bytes(profile_receipt(
            0, install_evidence=self.shadow_evidence, uid=uid, gid=gid))
        self.profile_receipt.chmod(0o600)
        self.profile_binding.document = json.loads(
            self.profile_receipt.read_bytes())
        self.boot = self.root / "boot-id"
        self.boot.write_bytes(
            b"91dd39d7-0a1b-4c1c-9c47-4f7a5402a293\n")
        self.boot.chmod(0o444)
        self.stack = ExitStack()
        for name, value in {
            "ROOT_UID": uid, "ROOT_GID": gid,
            "STATE_ROOT": self.state, "JOURNAL_ROOT": self.journal,
            "PREPARED_RECEIPT_PATH": self.state / ".prepared",
            "LOCK_PATH": self.root / ".lock",
            "PROFILE_RECEIPT_PATH": self.profile_receipt,
            "ACTIVATION_RECEIPT_PATH": self.receipts / "active.json",
            "LEGACY_ACTIVATION_RECEIPT_PATH":
                self.receipts / "legacy-active-v1.json",
            "LEGACY_ACTIVATION_RECEIPT_V2_PATH":
                self.receipts / "legacy-active-v2.json",
            "PREDECESSOR_ACTIVATION_RECEIPT_PATH":
                self.receipts / "predecessor-success-v3.json",
            "PREDECESSOR_FAILED_RECEIPT_PATH":
                self.receipts / "predecessor-failed-v2.json",
            "PREDECESSOR_JOURNAL_ROOT":
                self.root / "predecessor-journal",
            "FAILED_RECEIPT_PATH": self.receipts / "failed.json",
            "FAILED_RECEIPT_REPLACEMENT_PATH":
                self.receipts / ".failed.replacement",
            "FAILED_RECEIPT_PENDING_ARCHIVE_PATH":
                self.receipts / "failed.pending.json",
            "BOOT_ID_PATH": self.boot,
        }.items():
            self.stack.enter_context(mock.patch.object(ACTIVATION, name, value))

        def acquire_shadow(
            expected: dict[str, object],
        ) -> object:
            if expected != self.shadow_evidence or self.shadow_guard_held:
                raise ACTIVATION.ActivationError(
                    "ACTIVATION_SHADOW_INSTALL_INVALID")
            self.shadow_guard_held = True
            self.shadow_guard_events.append("acquire")
            return self.shadow_binding

        def validate_shadow(binding: object) -> dict[str, object]:
            if binding is not self.shadow_binding or not self.shadow_guard_held:
                raise ACTIVATION.ActivationError(
                    "ACTIVATION_SHADOW_INSTALL_REBOUND")
            self.shadow_guard_events.append("validate")
            return copy.deepcopy(self.shadow_current_evidence)

        def release_shadow(binding: object) -> None:
            if binding is not self.shadow_binding or not self.shadow_guard_held:
                raise ACTIVATION.ActivationError(
                    "ACTIVATION_SHADOW_INSTALL_RELEASE_FAILED")
            self.shadow_guard_events.append("release")
            self.shadow_guard_held = False

        self.stack.enter_context(mock.patch.object(
            ACTIVATION, "acquire_shadow_install_binding", acquire_shadow))
        self.stack.enter_context(mock.patch.object(
            ACTIVATION, "validate_shadow_install_binding", validate_shadow))
        self.stack.enter_context(mock.patch.object(
            ACTIVATION, "release_shadow_install_binding", release_shadow))

        def acquire_profile_binding(
            payload: bytes,
            _metadata: os.stat_result,
            binding: object,
        ) -> object:
            if (
                    binding is not self.shadow_binding or
                    payload != self.profile_receipt.read_bytes() or
                    not self.shadow_guard_held):
                raise ACTIVATION.ActivationError(
                    "ACTIVATION_PROFILE_ARTIFACT_INVALID")
            return self.profile_binding

        def validate_profile_binding(
            binding: object,
            shadow_binding: object,
        ) -> None:
            if (
                    binding is not self.profile_binding or
                    shadow_binding is not self.shadow_binding or
                    not self.shadow_guard_held):
                raise ACTIVATION.ActivationError(
                    "ACTIVATION_PROFILE_ARTIFACT_REBOUND")
            self.profile_binding_validation_count += 1

        self.stack.enter_context(mock.patch.object(
            ACTIVATION, "acquire_profile_artifact_binding",
            acquire_profile_binding))
        self.stack.enter_context(mock.patch.object(
            ACTIVATION, "validate_profile_artifact_binding",
            validate_profile_binding))

        def acquire_quarantine_guard() -> object:
            if self.shadow_quarantine_guard_held:
                raise ACTIVATION.ActivationError(
                    "ACTIVATION_SHADOW_QUARANTINE_GUARD_INVALID")
            self.shadow_quarantine_guard_held = True
            self.shadow_quarantine_guard_events.append("acquire")
            return self.shadow_quarantine_guard

        def validate_quarantine_guard(guard: object) -> None:
            if (
                    guard is not self.shadow_quarantine_guard or
                    not self.shadow_quarantine_guard_held):
                raise ACTIVATION.ActivationError(
                    "ACTIVATION_SHADOW_QUARANTINE_GUARD_REBOUND")
            self.shadow_quarantine_guard_events.append("validate")

        def release_quarantine_guard(guard: object) -> None:
            if (
                    guard is not self.shadow_quarantine_guard or
                    not self.shadow_quarantine_guard_held):
                raise ACTIVATION.ActivationError(
                    "ACTIVATION_SHADOW_QUARANTINE_GUARD_RELEASE_FAILED")
            self.shadow_quarantine_guard_events.append("release")
            self.shadow_quarantine_guard_held = False

        self.stack.enter_context(mock.patch.object(
            ACTIVATION, "acquire_shadow_install_quarantine_guard",
            acquire_quarantine_guard))
        self.stack.enter_context(mock.patch.object(
            ACTIVATION, "validate_shadow_install_quarantine_guard",
            validate_quarantine_guard))
        self.stack.enter_context(mock.patch.object(
            ACTIVATION, "release_shadow_install_quarantine_guard",
            release_quarantine_guard))
        self.stack.enter_context(mock.patch.object(
            ACTIVATION, "prepare_state_directories", lambda: None))
        self.boundary = {
            "export_absent": True, "sessions_authority_count": 0,
            "private_authority_count": 0,
            "custodian_transaction_absent": True,
            "session_bootstrap_idle_lock_observed": True,
        }
        self.stack.enter_context(mock.patch.object(
            ACTIVATION, "validate_local_boundaries",
            lambda: copy.deepcopy(self.boundary)))
        self.stack.enter_context(mock.patch.object(
            ACTIVATION, "validate_post_activation_paper_boundary",
            lambda: {"profile_sha256":
                     "sha256:" + ACTIVATION.PROFILE_SHA256,
                     "kill_switch_engaged": True,
                     "campaign_policy_count": 0}))
        quarantined = [
            ACTIVATION.StaleBundleValidation(
                number, "QUARANTINE", (), (), {
                    "round": number, "status": "QUARANTINED",
                    "bundle_sha256": bundle,
                    "terminal_receipt_sha256": terminal,
                    "quarantine_root":
                        "/var/lib/hepta/p1-admission/quarantine/"
                        f"activation-round114/round{number}"})
            for number, bundle, terminal in (
                (110,
                 "sha256:39bb0f9f47dec45435d5aaed5613fe9799922b0c8a66e3dbbc967e9927ef7ea9",
                 "sha256:3fe92cd29c23b78166fc557be2f88c29df1a41aec716958a3061331b3a1e6a35"),
                (112,
                 "sha256:6a0d351ae12ecc2da7279f941759f1b03c96112abf47bf36b915c229f0057439",
                 "sha256:a0c61e38581f8918d540d7940bea2ebfe49e9a8263a40b2a3a95130f59e5c24d"),
            )
        ]
        self.stack.enter_context(mock.patch.object(
            ACTIVATION, "validate_stale_bundles", lambda: quarantined))
        self.stack.enter_context(mock.patch.object(
            ACTIVATION, "quarantine_stale_bundles",
            lambda values: [copy.deepcopy(item.evidence) for item in values]))
        real_secure_read = ACTIVATION.secure_read

        def portable_secure_read(path: Path, reason: str, **kwargs):
            if path == self.boot:
                kwargs["procfs_parent"] = False
            return real_secure_read(path, reason, **kwargs)

        self.stack.enter_context(mock.patch.object(
            ACTIVATION, "secure_read", portable_secure_read))
        self.predecessor_evidence = predecessor_evidence()
        self.predecessor_success_evidence = predecessor_success_evidence()

        def validate_predecessor_success(
            expected: dict[str, object] | None = None,
        ) -> dict[str, object]:
            current = copy.deepcopy(self.predecessor_success_evidence)
            if expected is not None and expected != current:
                raise ACTIVATION.ActivationError(
                    "ACTIVATION_PREDECESSOR_SUCCESS_REBOUND")
            return current

        def validate_predecessor(
            expected: dict[str, object] | None = None,
        ) -> dict[str, object]:
            current = copy.deepcopy(self.predecessor_evidence)
            if expected is not None and expected != current:
                raise ACTIVATION.ActivationError(
                    "ACTIVATION_PREDECESSOR_FAILED_RECEIPT_REBOUND")
            return current

        self.stack.enter_context(mock.patch.object(
            ACTIVATION, "predecessor_activation_success_evidence",
            validate_predecessor_success))
        self.stack.enter_context(mock.patch.object(
            ACTIVATION, "predecessor_activation_failure_evidence",
            validate_predecessor))

    def tearDown(self) -> None:
        self.stack.close()
        self.temporary.cleanup()

    def _append_quarantine_prefix(
        self,
        length: int,
        *,
        reason: str = "ACTIVATION_SYSTEMCTL_FAILED",
    ) -> None:
        journal = ACTIVATION.Journal(self.journal)
        journal.append("PREPARED", {})
        evidence = (
            {"reason": reason},
            {"evidence": quarantine_gateway_evidence()},
            {"evidence": FakeExecutor().deny_all()},
            copy.deepcopy(self.boundary),
            {"complete": True},
        )
        for phase, value in zip(
                ACTIVATION.QUARANTINE_PHASES[:length],
                evidence[:length], strict=True):
            journal.append(phase, value)

    def _assert_quarantine_prefix_recovery(self, length: int) -> None:
        self._append_quarantine_prefix(length)
        expected_reason = (
            "ACTIVATION_SYSTEMCTL_FAILED" if length else
            "ACTIVATION_INCOMPLETE_TRANSACTION")
        self.assertEqual(
            ACTIVATION.reconcile(FakeExecutor()), "FAILED_CLOSED")
        records = ACTIVATION.Journal(self.journal).load()
        self.assertEqual(
            [record.phase for record in records],
            ["PREPARED", *ACTIVATION.QUARANTINE_PHASES])
        terminal = ACTIVATION.validate_failed_receipt(
            ACTIVATION.FAILED_RECEIPT_PATH.read_bytes())
        self.assertEqual(terminal["status"], "FAILED_CLOSED")
        self.assertEqual(terminal["reason"], expected_reason)
        self.assertEqual(terminal["revision"], 1)

    def _write_pending_failure(
        self,
        *,
        reason: str = "ACTIVATION_SYSTEMCTL_FAILED",
    ) -> tuple[bytes, dict[str, object]]:
        quarantine = {
            "errors": ["ACTIVATION_WATCH_AUTHORITY_PRESENT"],
            "deny_all": FakeExecutor().deny_all(), "complete": False,
        }
        document = ACTIVATION._failure_receipt(
            reason, quarantine,
            predecessor_activation_success=
                self.predecessor_success_evidence,
            predecessor_activation_failure=self.predecessor_evidence)
        payload = ACTIVATION.canonical_bytes(document)
        ACTIVATION._write_exclusive(ACTIVATION.FAILED_RECEIPT_PATH, payload)
        return payload, document

    def _assert_activation_quarantine_crash_recovery(self, length: int) -> None:
        class SimulatedProcessCrash(BaseException):
            pass

        original_append = ACTIVATION.Journal.append
        crash_phase = ACTIVATION.QUARANTINE_PHASES[length - 1]

        def append_then_crash(
            journal: ACTIVATION.Journal,
            phase: str,
            evidence: dict[str, object],
        ):
            record = original_append(journal, phase, evidence)
            if phase == crash_phase:
                raise SimulatedProcessCrash()
            return record

        with mock.patch.object(
                ACTIVATION.Journal, "append", append_then_crash):
            with self.assertRaises(SimulatedProcessCrash):
                ACTIVATION.activate(FakeExecutor(fail_gateway=True))
        self.assertFalse(ACTIVATION.FAILED_RECEIPT_PATH.exists())
        self.assertEqual(
            ACTIVATION.reconcile(FakeExecutor()), "FAILED_CLOSED")
        records = ACTIVATION.Journal(self.journal).load()
        phases = [record.phase for record in records]
        first = phases.index("QUARANTINE_INTENT")
        self.assertEqual(
            phases[first:], list(ACTIVATION.QUARANTINE_PHASES))
        terminal = ACTIVATION.validate_failed_receipt(
            ACTIVATION.FAILED_RECEIPT_PATH.read_bytes())
        self.assertEqual(terminal["reason"], "ACTIVATION_SYSTEMCTL_FAILED")

    def test_activation_quarantine_crash_after_prefix_1_recovers(self) -> None:
        self._assert_activation_quarantine_crash_recovery(1)

    def test_activation_quarantine_crash_after_prefix_2_recovers(self) -> None:
        self._assert_activation_quarantine_crash_recovery(2)

    def test_activation_quarantine_crash_after_prefix_3_recovers(self) -> None:
        self._assert_activation_quarantine_crash_recovery(3)

    def test_activation_quarantine_crash_after_prefix_4_recovers(self) -> None:
        self._assert_activation_quarantine_crash_recovery(4)

    def test_activation_quarantine_crash_after_prefix_5_recovers(self) -> None:
        self._assert_activation_quarantine_crash_recovery(5)

    def test_reconcile_quarantine_prefix_0(self) -> None:
        self._assert_quarantine_prefix_recovery(0)

    def test_reconcile_quarantine_prefix_1(self) -> None:
        self._assert_quarantine_prefix_recovery(1)

    def test_reconcile_quarantine_prefix_2(self) -> None:
        self._assert_quarantine_prefix_recovery(2)

    def test_reconcile_quarantine_prefix_3(self) -> None:
        self._assert_quarantine_prefix_recovery(3)

    def test_reconcile_quarantine_prefix_4(self) -> None:
        self._assert_quarantine_prefix_recovery(4)

    def test_reconcile_quarantine_prefix_5(self) -> None:
        self._assert_quarantine_prefix_recovery(5)

    def test_pending_failure_recovers_with_bound_prior_inode_and_body(self) -> None:
        pending_payload, pending = self._write_pending_failure()
        self.assertEqual(
            ACTIVATION.reconcile(FakeExecutor()), "FAILED_CLOSED")
        terminal = ACTIVATION.validate_failed_receipt(
            ACTIVATION.FAILED_RECEIPT_PATH.read_bytes())
        archived = ACTIVATION.validate_failed_receipt(
            ACTIVATION.FAILED_RECEIPT_PENDING_ARCHIVE_PATH.read_bytes())
        previous = terminal["previous_failed_receipt"]
        self.assertEqual(terminal["status"], "FAILED_CLOSED")
        self.assertEqual(terminal["revision"], 2)
        self.assertEqual(previous["file_sha256"],
                         ACTIVATION.digest_bytes(pending_payload))
        self.assertEqual(previous["body_sha256"], pending["body_sha256"])
        self.assertEqual(archived, pending)
        self.assertFalse(ACTIVATION.FAILED_RECEIPT_REPLACEMENT_PATH.exists())

    def test_pending_failure_exchange_crash_resumes_without_absence_window(
            self) -> None:
        self._write_pending_failure()

        def crash(phase: str) -> None:
            if phase == "AFTER_EXCHANGE":
                raise ACTIVATION.ActivationError("ACTIVATION_TEST_CRASH")

        with mock.patch.object(
                ACTIVATION, "FAILURE_REPLACEMENT_SEAM_HOOK", crash):
            with self.assertRaises(ACTIVATION.ActivationError):
                ACTIVATION.reconcile(FakeExecutor())
        terminal = ACTIVATION.validate_failed_receipt(
            ACTIVATION.FAILED_RECEIPT_PATH.read_bytes())
        self.assertEqual(terminal["status"], "FAILED_CLOSED")
        self.assertTrue(ACTIVATION.FAILED_RECEIPT_REPLACEMENT_PATH.exists())
        self.assertFalse(
            ACTIVATION.FAILED_RECEIPT_PENDING_ARCHIVE_PATH.exists())
        self.assertEqual(
            ACTIVATION.reconcile(FakeExecutor()), "FAILED_CLOSED")
        self.assertFalse(ACTIVATION.FAILED_RECEIPT_REPLACEMENT_PATH.exists())
        self.assertTrue(
            ACTIVATION.FAILED_RECEIPT_PENDING_ARCHIVE_PATH.exists())

    def test_pending_failure_pre_exchange_crash_resumes(self) -> None:
        self._write_pending_failure()

        def crash(phase: str) -> None:
            if phase == "AFTER_REPLACEMENT_WRITE":
                raise ACTIVATION.ActivationError("ACTIVATION_TEST_CRASH")

        with mock.patch.object(
                ACTIVATION, "FAILURE_REPLACEMENT_SEAM_HOOK", crash):
            with self.assertRaises(ACTIVATION.ActivationError):
                ACTIVATION.reconcile(FakeExecutor())
        pending = ACTIVATION.validate_failed_receipt(
            ACTIVATION.FAILED_RECEIPT_PATH.read_bytes())
        candidate = ACTIVATION.validate_failed_receipt(
            ACTIVATION.FAILED_RECEIPT_REPLACEMENT_PATH.read_bytes())
        self.assertEqual(pending["status"], "PENDING_EXPIRY")
        self.assertEqual(candidate["status"], "FAILED_CLOSED")
        self.assertEqual(
            ACTIVATION.reconcile(FakeExecutor()), "FAILED_CLOSED")
        self.assertFalse(ACTIVATION.FAILED_RECEIPT_REPLACEMENT_PATH.exists())

    def test_pending_failure_remains_pending_until_authority_is_empty(
            self) -> None:
        self._write_pending_failure()
        with mock.patch.object(
                ACTIVATION, "validate_local_boundaries",
                side_effect=ACTIVATION.ActivationError(
                    "ACTIVATION_WATCH_AUTHORITY_PRESENT")):
            with self.assertRaises(ACTIVATION.ActivationError):
                ACTIVATION.reconcile(FakeExecutor())
        pending = ACTIVATION.validate_failed_receipt(
            ACTIVATION.FAILED_RECEIPT_PATH.read_bytes())
        self.assertEqual(pending["status"], "PENDING_EXPIRY")
        self.assertFalse(
            ACTIVATION.FAILED_RECEIPT_PENDING_ARCHIVE_PATH.exists())
        self.assertEqual(
            ACTIVATION.reconcile(FakeExecutor()), "FAILED_CLOSED")

    def test_recovered_failure_with_forged_prior_binding_never_passes(
            self) -> None:
        pending_payload, pending = self._write_pending_failure()
        _, metadata = ACTIVATION.secure_read(
            ACTIVATION.FAILED_RECEIPT_PATH,
            "ACTIVATION_FAILED_RECEIPT_INVALID")
        evidence = ACTIVATION._previous_failed_receipt_evidence(
            pending_payload, pending, metadata)
        os.rename(
            ACTIVATION.FAILED_RECEIPT_PATH,
            ACTIVATION.FAILED_RECEIPT_PENDING_ARCHIVE_PATH)
        evidence["file_sha256"] = "sha256:" + "f" * 64
        terminal = ACTIVATION._failure_receipt(
            pending["reason"], {
                "errors": [], "deny_all": FakeExecutor().deny_all(),
                "complete": True},
            revision=2, previous_failed_receipt=evidence,
            predecessor_activation_success=
                self.predecessor_success_evidence,
            predecessor_activation_failure=self.predecessor_evidence)
        ACTIVATION._write_exclusive(
            ACTIVATION.FAILED_RECEIPT_PATH,
            ACTIVATION.canonical_bytes(terminal))
        executor = FakeExecutor()
        with self.assertRaisesRegex(
                ACTIVATION.ActivationError,
                "ACTIVATION_FAILED_RECEIPT_INVALID"):
            ACTIVATION.reconcile(executor)
        self.assertTrue(executor.quarantine_called)

    def test_invalid_gateway_attestation_cannot_skip_to_broker_phase(
            self) -> None:
        self._append_quarantine_prefix(0)

        class InvalidGatewayExecutor(FakeExecutor):
            def quarantine(self) -> dict[str, object]:
                result = super().quarantine()
                result["errors"] = [
                    "ACTIVATION_GATEWAY_QUARANTINE_INVALID"]
                result["gateway_masked_stopped"] = None
                result["complete"] = False
                return result

        with self.assertRaisesRegex(
                ACTIVATION.ActivationError,
                "ACTIVATION_QUARANTINE_INCOMPLETE"):
            ACTIVATION.reconcile(InvalidGatewayExecutor())
        self.assertEqual(
            [record.phase for record in
             ACTIVATION.Journal(self.journal).load()],
            ["PREPARED", "QUARANTINE_INTENT"])

    def test_happy_receipt_status_fresh_sequence_and_reconcile(self) -> None:
        executor = FakeExecutor()
        receipt = ACTIVATION.activate(executor)
        self.assertEqual(
            receipt["schema"], "hepta.p1-watch-activation-receipt.v4")
        self.assertEqual(receipt["version"], 4)
        self.assertEqual(receipt["round"], 114)
        self.assertEqual(
            receipt["predecessor_activation_success"],
            self.predecessor_success_evidence)
        self.assertEqual(
            receipt["predecessor_activation_failure"],
            self.predecessor_evidence)
        self.assertEqual(receipt["shadow_install_evidence"],
                         self.shadow_evidence)
        self.assertEqual(receipt["status"], "WATCH_GATEWAY_ACTIVATED")
        self.assertTrue(receipt["fresh_activation_transaction"])
        self.assertEqual(set(receipt), ACTIVATION.RECEIPT_FIELDS)
        journal = ACTIVATION.Journal(self.journal)
        self.assertEqual(
            [record.phase for record in journal.load()],
            list(ACTIVATION.ACTIVATION_PHASES))
        self.assertEqual(receipt["journal_sha256"], journal.digest())
        self.assertEqual(executor.mutations, [
            [ACTIVATION.SYSTEMCTL, "enable", "--now",
             ACTIVATION.RECONCILE_TIMER],
            [ACTIVATION.SYSTEMCTL, "daemon-reload"],
            [ACTIVATION.SYSTEMCTL, "start", ACTIVATION.BROKER_UNIT],
            [ACTIVATION.SYSTEMCTL, "unmask", *ACTIVATION.GATEWAY_UNITS],
            [ACTIVATION.SYSTEMCTL, "unmask", "--runtime",
             *ACTIVATION.GATEWAY_UNITS],
            [ACTIVATION.SYSTEMCTL, "daemon-reload"],
            [ACTIVATION.SYSTEMCTL, "start", ACTIVATION.GATEWAY_SERVICE],
        ])
        self.assertEqual(
            ACTIVATION.reconcile(FakeExecutor()),
            "WATCH_GATEWAY_ACTIVATED")
        profile_payload = self.profile_receipt.read_bytes()
        profile_document = json.loads(profile_payload)
        with mock.patch.object(
                LAUNCHER, "PROFILE_DEPLOYMENT_RECEIPT", self.profile_receipt), \
                mock.patch.object(
                    LAUNCHER, "PROFILE_DEPLOYMENT_RECEIPT_STAGING",
                    ACTIVATION.PROFILE_RECEIPT_STAGING_PATH), \
                mock.patch.object(
                    LAUNCHER, "PREDECESSOR_ACTIVATION_SUCCESS_RECEIPT",
                    ACTIVATION.PREDECESSOR_ACTIVATION_RECEIPT_PATH), \
                mock.patch.object(
                    LAUNCHER, "PREDECESSOR_ACTIVATION_FAILED_RECEIPT",
                    ACTIVATION.PREDECESSOR_FAILED_RECEIPT_PATH), \
                mock.patch.object(
                    LAUNCHER, "PREDECESSOR_ACTIVATION_JOURNAL",
                    ACTIVATION.PREDECESSOR_JOURNAL_ROOT), \
                mock.patch.object(LAUNCHER, "ROOT_UID", os.geteuid()), \
                mock.patch.object(LAUNCHER, "ROOT_GID", os.getegid()):
            LAUNCHER.ProductionExecutor._validate_activation_receipt(
                receipt, receipt_contents=ACTIVATION.canonical_bytes(receipt),
                profile_receipt=profile_document,
                profile_receipt_contents=profile_payload,
                boot_id=receipt["boot_id"],
                predecessor_activation_success=
                    self.predecessor_success_evidence,
                predecessor_activation_failure=self.predecessor_evidence)

    def test_shadow_guard_is_outer_to_prepare_and_inner_lock(self) -> None:
        events: list[str] = []
        acquire_shadow = ACTIVATION.acquire_shadow_install_binding
        prepare = ACTIVATION.prepare_state_directories
        acquire_inner = ACTIVATION.acquire_lock

        def observed_shadow(expected: dict[str, object]) -> object:
            events.append("shadow")
            return acquire_shadow(expected)

        def observed_prepare() -> None:
            self.assertTrue(self.shadow_guard_held)
            events.append("prepare")
            prepare()

        def observed_inner() -> int:
            self.assertTrue(self.shadow_guard_held)
            events.append("inner")
            return acquire_inner()

        with mock.patch.object(
                ACTIVATION, "acquire_shadow_install_binding",
                observed_shadow), mock.patch.object(
                    ACTIVATION, "prepare_state_directories",
                    observed_prepare), mock.patch.object(
                        ACTIVATION, "acquire_lock", observed_inner):
            ACTIVATION.activate(FakeExecutor())
        self.assertLess(events.index("shadow"), events.index("prepare"))
        self.assertLess(events.index("prepare"), events.index("inner"))
        self.assertFalse(self.shadow_guard_held)

    def test_shadow_guard_acquire_failure_precedes_all_mutation(self) -> None:
        executor = FakeExecutor()
        prepare = mock.Mock()
        with mock.patch.object(
                ACTIVATION, "acquire_shadow_install_binding",
                side_effect=ACTIVATION.ActivationError(
                    "ACTIVATION_SHADOW_INSTALL_INVALID")), \
                mock.patch.object(
                    ACTIVATION, "prepare_state_directories", prepare):
            with self.assertRaisesRegex(
                    ACTIVATION.ActivationError,
                    "ACTIVATION_SHADOW_INSTALL_INVALID"):
                ACTIVATION.activate(executor)
        prepare.assert_not_called()
        self.assertEqual(executor.mutations, [])
        self.assertFalse(ACTIVATION.LOCK_PATH.exists())
        self.assertFalse(ACTIVATION.ACTIVATION_RECEIPT_PATH.exists())
        self.assertFalse(ACTIVATION.FAILED_RECEIPT_PATH.exists())

    def test_shadow_quarantine_guard_acquire_failure_blocks_quarantine(
            self) -> None:
        self._append_quarantine_prefix(0)
        executor = FakeExecutor()
        prepare = mock.Mock()
        with mock.patch.object(
                ACTIVATION, "acquire_shadow_install_binding",
                side_effect=ACTIVATION.ActivationError(
                    "ACTIVATION_SHADOW_INSTALL_INVALID")), \
                mock.patch.object(
                    ACTIVATION, "acquire_shadow_install_quarantine_guard",
                    side_effect=ACTIVATION.ActivationError(
                        "ACTIVATION_SHADOW_QUARANTINE_GUARD_INVALID")), \
                mock.patch.object(
                    ACTIVATION, "prepare_state_directories", prepare):
            with self.assertRaisesRegex(
                    ACTIVATION.ActivationError,
                    "ACTIVATION_SHADOW_QUARANTINE_GUARD_INVALID"):
                ACTIVATION.reconcile(executor)
        prepare.assert_not_called()
        self.assertFalse(executor.quarantine_called)
        self.assertFalse(ACTIVATION.LOCK_PATH.exists())
        self.assertFalse(ACTIVATION.FAILED_RECEIPT_PATH.exists())

    def test_shadow_quarantine_guard_is_outer_to_quarantine_lock(self) -> None:
        self._append_quarantine_prefix(0)
        events: list[str] = []
        acquire_guard = ACTIVATION.acquire_shadow_install_quarantine_guard
        release_guard = ACTIVATION.release_shadow_install_quarantine_guard
        prepare = ACTIVATION.prepare_state_directories
        acquire_inner = ACTIVATION.acquire_lock
        fixture = self

        class GuardedExecutor(FakeExecutor):
            def quarantine(inner_self) -> dict[str, object]:
                fixture.assertTrue(fixture.shadow_quarantine_guard_held)
                events.append("quarantine")
                return super().quarantine()

        def observed_guard() -> object:
            events.append("guard")
            return acquire_guard()

        def observed_prepare() -> None:
            self.assertTrue(self.shadow_quarantine_guard_held)
            events.append("prepare")
            prepare()

        def observed_inner() -> int:
            self.assertTrue(self.shadow_quarantine_guard_held)
            events.append("inner")
            return acquire_inner()

        def observed_release(guard: object) -> None:
            self.assertTrue(self.shadow_quarantine_guard_held)
            events.append("release")
            release_guard(guard)

        executor = GuardedExecutor()
        with mock.patch.object(
                ACTIVATION, "acquire_shadow_install_binding",
                side_effect=ACTIVATION.ActivationError(
                    "ACTIVATION_SHADOW_INSTALL_INVALID")), \
                mock.patch.object(
                    ACTIVATION, "acquire_shadow_install_quarantine_guard",
                    observed_guard), mock.patch.object(
                    ACTIVATION, "prepare_state_directories",
                    observed_prepare), mock.patch.object(
                    ACTIVATION, "acquire_lock", observed_inner), \
                mock.patch.object(
                    ACTIVATION, "release_shadow_install_quarantine_guard",
                    observed_release):
            with self.assertRaisesRegex(
                    ACTIVATION.ActivationError,
                    "ACTIVATION_SHADOW_INSTALL_INVALID"):
                ACTIVATION.reconcile(executor)
        self.assertLess(events.index("guard"), events.index("prepare"))
        self.assertLess(events.index("prepare"), events.index("inner"))
        self.assertLess(events.index("inner"), events.index("quarantine"))
        self.assertLess(events.index("quarantine"), events.index("release"))
        self.assertTrue(executor.quarantine_called)
        self.assertFalse(self.shadow_quarantine_guard_held)

    def test_shadow_quarantine_guard_rebind_in_final_window_fails_closed(
            self) -> None:
        self._append_quarantine_prefix(0)
        validations = 0
        publication_guard_state: list[bool] = []
        validate_guard = ACTIVATION.validate_shadow_install_quarantine_guard
        original_write = ACTIVATION._write_exclusive

        def drift_after_quarantine(guard: object) -> None:
            nonlocal validations
            validations += 1
            if validations >= 4:
                self.assertTrue(self.shadow_quarantine_guard_held)
                raise ACTIVATION.ActivationError(
                    "ACTIVATION_SHADOW_QUARANTINE_GUARD_REBOUND")
            validate_guard(guard)

        def observed_write(
            path: Path,
            payload: bytes,
            mode: int = 0o600,
        ) -> None:
            if path == ACTIVATION.FAILED_RECEIPT_PATH:
                publication_guard_state.append(
                    self.shadow_quarantine_guard_held)
            original_write(path, payload, mode)

        executor = FakeExecutor()
        with mock.patch.object(
                ACTIVATION, "acquire_shadow_install_binding",
                side_effect=ACTIVATION.ActivationError(
                    "ACTIVATION_SHADOW_INSTALL_INVALID")), \
                mock.patch.object(
                    ACTIVATION, "validate_shadow_install_quarantine_guard",
                    drift_after_quarantine), mock.patch.object(
                    ACTIVATION, "_write_exclusive", observed_write):
            with self.assertRaisesRegex(
                    ACTIVATION.ActivationError,
                    "ACTIVATION_SHADOW_QUARANTINE_GUARD_REBOUND"):
                ACTIVATION.reconcile(executor)
        self.assertTrue(executor.quarantine_called)
        self.assertEqual(publication_guard_state, [True])
        self.assertGreaterEqual(validations, 4)
        self.assertEqual(
            self.shadow_quarantine_guard_events[-1], "release")
        self.assertFalse(self.shadow_quarantine_guard_held)

    def test_shadow_evidence_mismatch_fails_before_prepare(self) -> None:
        drift = copy.deepcopy(self.shadow_evidence)
        drift["manifest_file_sha256"] = "sha256:" + "2" * 64
        prepare = mock.Mock()
        with mock.patch.object(
                ACTIVATION, "validate_shadow_install_binding",
                return_value=drift), mock.patch.object(
                    ACTIVATION, "prepare_state_directories", prepare):
            with self.assertRaisesRegex(
                    ACTIVATION.ActivationError,
                    "ACTIVATION_PROFILE_RECEIPT_INVALID"):
                ACTIVATION.activate(FakeExecutor())
        prepare.assert_not_called()
        self.assertFalse(self.shadow_guard_held)
        self.assertFalse(ACTIVATION.LOCK_PATH.exists())

    def test_shadow_guard_is_held_through_activation_receipt_publication(
            self) -> None:
        original_write = ACTIVATION._write_exclusive
        publication_guard_state: list[bool] = []

        def observed_write(
            path: Path,
            payload: bytes,
            mode: int = 0o600,
        ) -> None:
            if path == ACTIVATION.ACTIVATION_RECEIPT_PATH:
                publication_guard_state.append(self.shadow_guard_held)
            original_write(path, payload, mode)

        with mock.patch.object(
                ACTIVATION, "_write_exclusive", observed_write):
            ACTIVATION.activate(FakeExecutor())
        self.assertEqual(publication_guard_state, [True])
        self.assertEqual(self.shadow_guard_events[-1], "release")
        self.assertFalse(self.shadow_guard_held)

    def test_active_reconcile_rejects_current_evidence_profile_mismatch(
            self) -> None:
        ACTIVATION.activate(FakeExecutor())
        self.shadow_current_evidence = copy.deepcopy(self.shadow_evidence)
        self.shadow_current_evidence["receipt_file_sha256"] = (
            "sha256:" + "2" * 64)
        executor = FakeExecutor()
        with self.assertRaisesRegex(
                ACTIVATION.ActivationError,
                "ACTIVATION_PROFILE_RECEIPT_INVALID"):
            ACTIVATION.reconcile(executor)
        self.assertEqual(executor.mutations, [])
        self.assertTrue(executor.quarantine_called)
        self.assertIsNotNone(executor.quarantine_evidence)
        assert executor.quarantine_evidence is not None
        self.assertTrue(ACTIVATION._valid_gateway_quarantine(
            executor.quarantine_evidence["gateway_masked_stopped"]))
        self.assertTrue(ACTIVATION._valid_deny_all(
            executor.quarantine_evidence["deny_all"]))
        gateway_quarantine = executor.quarantine_evidence[
            "gateway_masked_stopped"]
        assert isinstance(gateway_quarantine, dict)
        self.assertTrue(all(
            state["ActiveState"] == "inactive" and
            state["SubState"] == "dead"
            for state in gateway_quarantine["manager_units"].values()))
        self.assertTrue(all(
            member[scope]["target"] == ACTIVATION.MASK_TARGET
            for member in gateway_quarantine["masks"].values()
            for scope in ("persistent", "runtime")))
        terminal = ACTIVATION.validate_failed_receipt(
            ACTIVATION.FAILED_RECEIPT_PATH.read_bytes())
        self.assertEqual(
            terminal["reason"], "ACTIVATION_PROFILE_RECEIPT_INVALID")
        self.assertEqual(terminal["status"], "FAILED_CLOSED")
        self.assertEqual(
            [record.phase for record in
             ACTIVATION.Journal(self.journal).load()][-5:],
            list(ACTIVATION.QUARANTINE_PHASES))
        self.assertFalse(self.shadow_guard_held)

    def test_active_reconcile_rejects_activation_receipt_evidence_mismatch(
            self) -> None:
        ACTIVATION.activate(FakeExecutor())
        receipt = json.loads(ACTIVATION.ACTIVATION_RECEIPT_PATH.read_bytes())
        receipt.pop("body_sha256")
        mismatched = copy.deepcopy(self.shadow_evidence)
        mismatched["manifest_file_sha256"] = "sha256:" + "2" * 64
        receipt["shadow_install_evidence"] = mismatched
        ACTIVATION.ACTIVATION_RECEIPT_PATH.write_bytes(
            ACTIVATION.canonical_bytes(ACTIVATION.seal(receipt)))
        executor = FakeExecutor()
        with self.assertRaisesRegex(
                ACTIVATION.ActivationError,
                "ACTIVATION_RUNTIME_DRIFT_RECEIPT"):
            ACTIVATION.reconcile(executor)
        self.assertTrue(executor.quarantine_called)
        self.assertFalse(self.shadow_guard_held)

    def test_activation_crash_path_quarantines(self) -> None:
        executor = FakeExecutor(fail_gateway=True)
        with self.assertRaisesRegex(
                ACTIVATION.ActivationError, "ACTIVATION_SYSTEMCTL_FAILED"):
            ACTIVATION.activate(executor)
        self.assertTrue(executor.quarantine_called)
        self.assertFalse(ACTIVATION.ACTIVATION_RECEIPT_PATH.exists())
        self.assertTrue(ACTIVATION.FAILED_RECEIPT_PATH.exists())
        self.assertEqual(
            ACTIVATION.Journal(self.journal).load()[-1].phase,
            "FAILED_CLOSED")

    def test_reconcile_runtime_drift_quarantines(self) -> None:
        ACTIVATION.activate(FakeExecutor())
        drift = FakeExecutor(drift=True)
        with self.assertRaisesRegex(
                ACTIVATION.ActivationError,
                "ACTIVATION_RUNTIME_DRIFT_GATEWAY"):
            ACTIVATION.reconcile(drift)
        self.assertTrue(drift.quarantine_called)
        self.assertTrue(ACTIVATION.FAILED_RECEIPT_PATH.exists())
        self.assertEqual(
            ACTIVATION.Journal(self.journal).load()[-1].phase,
            "FAILED_CLOSED")

    def test_reconcile_accepts_timer_running_to_waiting_transition(self) -> None:
        ACTIVATION.activate(FakeExecutor(timer_substate="running"))
        executor = FakeExecutor(timer_substate="waiting")
        self.assertEqual(
            ACTIVATION.reconcile(executor), "WATCH_GATEWAY_ACTIVATED")
        self.assertFalse(executor.quarantine_called)

    def test_reconcile_accepts_broker_manager_rendering_hash_drift(self) -> None:
        ACTIVATION.activate(FakeExecutor())
        executor = FakeExecutor(broker_unit_contract_sha256="sha256:" + "2" * 64)
        self.assertEqual(
            ACTIVATION.reconcile(executor), "WATCH_GATEWAY_ACTIVATED")
        self.assertFalse(executor.quarantine_called)

    def test_reconcile_accepts_gateway_manager_rendering_hash_drift(self) -> None:
        ACTIVATION.activate(FakeExecutor())
        executor = FakeExecutor(
            gateway_unit_contract_sha256="sha256:" + "2" * 64)
        self.assertEqual(
            ACTIVATION.reconcile(executor), "WATCH_GATEWAY_ACTIVATED")
        self.assertFalse(executor.quarantine_called)

    def test_postcommit_watch_authority_does_not_trigger_quarantine(self) -> None:
        ACTIVATION.activate(FakeExecutor())
        with mock.patch.object(
                ACTIVATION, "validate_local_boundaries",
                side_effect=ACTIVATION.ActivationError("WATCH_AUTHORITY_PRESENT")):
            executor = FakeExecutor()
            self.assertEqual(
                ACTIVATION.reconcile(executor), "WATCH_GATEWAY_ACTIVATED")
            self.assertFalse(executor.quarantine_called)

    def test_postcommit_paper_drift_quarantines(self) -> None:
        ACTIVATION.activate(FakeExecutor())
        paper = FakeExecutor(paper_active=True)
        with self.assertRaisesRegex(
                ACTIVATION.ActivationError,
                "ACTIVATION_RUNTIME_DRIFT_PAPER_UNITS"):
            ACTIVATION.reconcile(paper)
        self.assertTrue(paper.quarantine_called)

    def test_postcommit_kill_switch_drift_quarantines(self) -> None:
        ACTIVATION.activate(FakeExecutor())
        executor = FakeExecutor()
        with mock.patch.object(
                ACTIVATION, "validate_post_activation_paper_boundary",
                side_effect=ACTIVATION.ActivationError(
                    "ACTIVATION_KILL_SWITCH_INVALID")):
            with self.assertRaisesRegex(
                    ACTIVATION.ActivationError, "ACTIVATION_RUNTIME_DRIFT"):
                ACTIVATION.reconcile(executor)
        self.assertTrue(executor.quarantine_called)

    def test_preflight_to_commit_paper_drift_quarantines(self) -> None:
        executor = FakeExecutor(paper_active_after_preflight=True)
        with self.assertRaisesRegex(
                ACTIVATION.ActivationError, "ACTIVATION_PAPER_ACTIVE"):
            ACTIVATION.activate(executor)
        self.assertTrue(executor.quarantine_called)

    def test_legacy_v1_activation_receipt_blocks_fresh_activation(self) -> None:
        ACTIVATION.LEGACY_ACTIVATION_RECEIPT_PATH.write_bytes(b"legacy\n")
        executor = FakeExecutor()
        with self.assertRaisesRegex(
                ACTIVATION.ActivationError, "ACTIVATION_ALREADY_TERMINAL"):
            ACTIVATION.activate(executor)
        self.assertTrue(executor.quarantine_called)
        self.assertFalse(ACTIVATION.ACTIVATION_RECEIPT_PATH.exists())
        self.assertTrue(ACTIVATION.FAILED_RECEIPT_PATH.exists())

    def test_reconcile_observes_only_round114_state(self) -> None:
        ACTIVATION.LEGACY_ACTIVATION_RECEIPT_PATH.write_bytes(b"legacy\n")
        executor = FakeExecutor()
        self.assertEqual(ACTIVATION.reconcile(executor), "NO_TRANSACTION")
        self.assertFalse(executor.quarantine_called)
        self.assertFalse(ACTIVATION.FAILED_RECEIPT_PATH.exists())

    def test_legacy_v2_activation_receipt_blocks_fresh_activation(self) -> None:
        ACTIVATION.LEGACY_ACTIVATION_RECEIPT_V2_PATH.write_bytes(b"legacy\n")
        executor = FakeExecutor()
        with self.assertRaisesRegex(
                ACTIVATION.ActivationError, "ACTIVATION_ALREADY_TERMINAL"):
            ACTIVATION.activate(executor)
        self.assertTrue(executor.quarantine_called)
        self.assertFalse(ACTIVATION.ACTIVATION_RECEIPT_PATH.exists())
        self.assertTrue(ACTIVATION.FAILED_RECEIPT_PATH.exists())

    def test_round95_success_predecessor_is_not_terminal_poison(self) -> None:
        ACTIVATION.PREDECESSOR_ACTIVATION_RECEIPT_PATH.write_bytes(
            b"exact-predecessor-is-validated-by-bound-evidence\n")
        receipt = ACTIVATION.activate(FakeExecutor())
        self.assertEqual(receipt["round"], 114)

    def test_predecessor_success_and_failure_shapes_are_strict(self) -> None:
        ACTIVATION.validate_predecessor_activation_success_evidence(
            self.predecessor_success_evidence)
        ACTIVATION.validate_predecessor_activation_failure_evidence(
            self.predecessor_evidence)
        forged_success = copy.deepcopy(self.predecessor_success_evidence)
        forged_success["receipt_inode"] = 0
        with self.assertRaisesRegex(
                ACTIVATION.ActivationError,
                "ACTIVATION_PREDECESSOR_SUCCESS_EVIDENCE_INVALID"):
            ACTIVATION.validate_predecessor_activation_success_evidence(
                forged_success)
        forged_failure = copy.deepcopy(self.predecessor_evidence)
        forged_failure["journal_record_count"] = 20
        with self.assertRaisesRegex(
                ACTIVATION.ActivationError,
                "ACTIVATION_PREDECESSOR_EVIDENCE_INVALID"):
            ACTIVATION.validate_predecessor_activation_failure_evidence(
                forged_failure)

    def test_predecessor_success_rebound_mid_transaction_fails_closed(
            self) -> None:
        fixture = self

        class ReboundExecutor(FakeExecutor):
            def mutate(self, arguments: tuple[str, ...]) -> None:
                super().mutate(arguments)
                fixture.predecessor_success_evidence["receipt_inode"] += 1

        executor = ReboundExecutor()
        with self.assertRaisesRegex(
                ACTIVATION.ActivationError,
                "ACTIVATION_PREDECESSOR_SUCCESS_REBOUND"):
            ACTIVATION.activate(executor)
        self.assertTrue(executor.quarantine_called)
        self.assertFalse(ACTIVATION.ACTIVATION_RECEIPT_PATH.exists())

    def test_predecessor_failure_rebound_mid_transaction_fails_closed(
            self) -> None:
        fixture = self

        class ReboundExecutor(FakeExecutor):
            def mutate(self, arguments: tuple[str, ...]) -> None:
                super().mutate(arguments)
                fixture.predecessor_evidence["receipt_inode"] += 1

        executor = ReboundExecutor()
        with self.assertRaisesRegex(
                ACTIVATION.ActivationError,
                "ACTIVATION_PREDECESSOR_FAILED_RECEIPT_REBOUND"):
            ACTIVATION.activate(executor)
        self.assertTrue(executor.quarantine_called)
        self.assertFalse(ACTIVATION.ACTIVATION_RECEIPT_PATH.exists())

    def test_reconcile_corrupt_active_receipt_quarantines(self) -> None:
        ACTIVATION.activate(FakeExecutor())
        ACTIVATION.ACTIVATION_RECEIPT_PATH.write_bytes(b"{}\n")
        drift = FakeExecutor()
        with self.assertRaisesRegex(
                ACTIVATION.ActivationError, "ACTIVATION_RUNTIME_DRIFT"):
            ACTIVATION.reconcile(drift)
        self.assertTrue(drift.quarantine_called)
        self.assertTrue(ACTIVATION.FAILED_RECEIPT_PATH.exists())
        self.assertEqual(
            ACTIVATION.Journal(self.journal).load()[-1].phase,
            "FAILED_CLOSED")

    def test_reconcile_corrupt_journal_quarantines(self) -> None:
        ACTIVATION.activate(FakeExecutor())
        first = sorted(self.journal.iterdir())[0]
        first.write_bytes(b"{}\n")
        drift = FakeExecutor()
        with self.assertRaisesRegex(
                ACTIVATION.ActivationError, "ACTIVATION_STATE_INVALID"):
            ACTIVATION.reconcile(drift)
        self.assertTrue(drift.quarantine_called)
        failed = ACTIVATION.FAILED_RECEIPT_PATH.read_bytes()
        self.assertEqual(
            ACTIVATION.validate_failed_receipt(failed)["status"],
            "FAILED_CLOSED")

    def test_reconcile_forged_failed_residue_never_reports_pass(self) -> None:
        ACTIVATION.FAILED_RECEIPT_PATH.write_bytes(b"{}\n")
        ACTIVATION.FAILED_RECEIPT_PATH.chmod(0o600)
        drift = FakeExecutor()
        with self.assertRaisesRegex(
                ACTIVATION.ActivationError,
                "ACTIVATION_FAILED_RECEIPT_INVALID"):
            ACTIVATION.reconcile(drift)
        self.assertTrue(drift.quarantine_called)


class ProductionQuarantineTests(unittest.TestCase):
    @staticmethod
    def _manager_state() -> dict[str, str]:
        return {
            "LoadState": "masked", "ActiveState": "inactive",
            "SubState": "dead", "Job": "", "UnitFileState": "masked",
        }

    @staticmethod
    def _mask_snapshots() -> tuple[dict[str, object], dict[str, object]]:
        masks = copy.deepcopy(quarantine_gateway_evidence()["masks"])
        identities: dict[str, object] = {}
        for unit in ACTIVATION.GATEWAY_UNITS:
            identities[unit] = {}
            for scope in ("persistent", "runtime"):
                member = masks[unit][scope]
                identities[unit][scope] = (
                    member["device"], member["inode"], member["mode"],
                    member["nlink"], member["uid"], member["gid"],
                    member["bytes"], member["mtime_ns"], member["ctime_ns"],
                )
        return masks, identities

    @staticmethod
    def _deny_all(*, tighten: bool = False) -> dict[str, object]:
        del tighten
        return {"policy_sha256": SHA, "authorized_connectors": 0,
                "authorized_uids": [], "protected_ports": 4}

    def test_quarantine_attests_exact_manager_and_dual_masks(self) -> None:
        executor = ACTIVATION.ProductionExecutor()
        masks = self._mask_snapshots()
        with mock.patch.object(executor, "mutate", return_value=None), \
                mock.patch.object(
                    executor, "_show", return_value=self._manager_state()), \
                mock.patch.object(
                    executor, "deny_all", side_effect=self._deny_all), \
                mock.patch.object(
                    ACTIVATION, "_gateway_masks_state", return_value=masks):
            result = executor.quarantine()
        self.assertTrue(result["complete"])
        self.assertEqual(result["errors"], [])
        self.assertTrue(ACTIVATION._valid_gateway_quarantine(
            result["gateway_masked_stopped"]))

    def test_quarantine_rc_zero_rejects_active_unmasked_or_pending_job(
            self) -> None:
        variants = (
            {"ActiveState": "active", "SubState": "running"},
            {"LoadState": "loaded", "UnitFileState": "enabled"},
            {"Job": "77"},
        )
        for mutation in variants:
            with self.subTest(mutation=mutation):
                executor = ACTIVATION.ProductionExecutor()
                state = {**self._manager_state(), **mutation}
                with mock.patch.object(
                        executor, "mutate", return_value=None), \
                        mock.patch.object(
                            executor, "_show", return_value=state), \
                        mock.patch.object(
                            executor, "deny_all", side_effect=self._deny_all), \
                        mock.patch.object(
                            ACTIVATION, "_gateway_masks_state",
                            return_value=self._mask_snapshots()):
                    result = executor.quarantine()
                self.assertFalse(result["complete"])
                self.assertIn(
                    "ACTIVATION_GATEWAY_QUARANTINE_INVALID",
                    result["errors"])

    def test_quarantine_manager_rebind_is_rejected(self) -> None:
        executor = ACTIVATION.ProductionExecutor()
        calls = 0

        def show(_unit: str, _names: tuple[str, ...]) -> dict[str, str]:
            nonlocal calls
            calls += 1
            state = self._manager_state()
            if calls == len(ACTIVATION.GATEWAY_UNITS) + 1:
                state = {**state, "Job": "99"}
            return state

        with mock.patch.object(executor, "mutate", return_value=None), \
                mock.patch.object(executor, "_show", side_effect=show), \
                mock.patch.object(
                    executor, "deny_all", side_effect=self._deny_all), \
                mock.patch.object(
                    ACTIVATION, "_gateway_masks_state",
                    return_value=self._mask_snapshots()):
            result = executor.quarantine()
        self.assertFalse(result["complete"])
        self.assertIn(
            "ACTIVATION_GATEWAY_QUARANTINE_INVALID", result["errors"])

    def test_quarantine_mask_replacement_before_final_sample_is_rejected(
            self) -> None:
        with tempfile.TemporaryDirectory(dir=ROOT) as temporary:
            root = Path(temporary)
            persistent = root / "persistent"
            runtime = root / "runtime"
            persistent.mkdir(mode=0o755)
            runtime.mkdir(mode=0o755)
            for unit in ACTIVATION.GATEWAY_UNITS:
                (persistent / unit).symlink_to(ACTIVATION.MASK_TARGET)
                (runtime / unit).symlink_to(ACTIVATION.MASK_TARGET)
            executor = ACTIVATION.ProductionExecutor()
            replaced = False

            def seam(phase: str) -> None:
                nonlocal replaced
                if phase != "BEFORE_MANAGER_AFTER" or replaced:
                    return
                replaced = True
                path = runtime / ACTIVATION.GATEWAY_UNITS[0]
                held = runtime / ".held-mask"
                path.rename(held)
                path.symlink_to(ACTIVATION.MASK_TARGET)

            uid, gid = os.geteuid(), os.getegid()
            with mock.patch.object(ACTIVATION, "ROOT_UID", uid), \
                    mock.patch.object(ACTIVATION, "ROOT_GID", gid), \
                    mock.patch.object(
                        ACTIVATION, "PERSISTENT_SYSTEMD_ROOT", persistent), \
                    mock.patch.object(
                        ACTIVATION, "RUNTIME_SYSTEMD_ROOT", runtime), \
                    mock.patch.object(
                        ACTIVATION, "QUARANTINE_ATTESTATION_SEAM_HOOK", seam), \
                    mock.patch.object(executor, "mutate", return_value=None), \
                    mock.patch.object(
                        executor, "_show", return_value=self._manager_state()), \
                    mock.patch.object(
                        executor, "deny_all", side_effect=self._deny_all):
                result = executor.quarantine()
            self.assertTrue(replaced)
            self.assertFalse(result["complete"])
            self.assertIn(
                "ACTIVATION_GATEWAY_QUARANTINE_INVALID", result["errors"])


class ReceiptTests(unittest.TestCase):
    def test_historical_exec_main_pid_is_offline_only_but_accepted(self) -> None:
        document = ACTIVATION.validate_profile_receipt(
            profile_receipt(1108253))
        self.assertEqual(
            document["preflight_before"]["broker_egress_unit"]["ExecMainPID"],
            1108253)
        with self.assertRaisesRegex(
                ACTIVATION.ActivationError,
                "ACTIVATION_PROFILE_RECEIPT_INVALID"):
            ACTIVATION.validate_profile_receipt(profile_receipt(1))

    def test_forged_profile_receipt_and_frozen_source_are_rejected(self) -> None:
        payload = bytearray(profile_receipt(0))
        payload[payload.index(b'"round":114') + len(b'"round":11')] = ord("5")
        with self.assertRaises(ACTIVATION.ActivationError):
            ACTIVATION.validate_profile_receipt(bytes(payload))
        with tempfile.TemporaryDirectory(dir=ROOT) as temporary:
            root = Path(temporary)
            installed = root / "installed.py"
            credential = root / "credential.py"
            installed.write_bytes(b"installed\n")
            credential.write_bytes(b"forged\n")
            installed.chmod(0o755)
            credential.chmod(0o400)
            metadata = os.stat(installed)
            with ExitStack() as stack:
                stack.enter_context(mock.patch.object(
                    ACTIVATION, "ROOT_UID", os.geteuid()))
                stack.enter_context(mock.patch.object(
                    ACTIVATION, "ROOT_GID", os.getegid()))
                stack.enter_context(mock.patch.object(
                    ACTIVATION, "CREDENTIALS_DIRECTORY", str(root)))
                stack.enter_context(mock.patch.object(
                    ACTIVATION, "PROFILE_DEPLOYER", installed))
                stack.enter_context(mock.patch.object(
                    ACTIVATION, "PROFILE_DEPLOYER_RUNTIME_SOURCE", credential))
                with self.assertRaisesRegex(
                        ACTIVATION.ActivationError,
                        "ACTIVATION_PROFILE_DEPLOYER_SOURCE_INVALID"):
                    ACTIVATION._validate_with_frozen_profile_deployer(
                        profile_receipt(0), metadata,
                        shadow_install_evidence())

    def test_profile_transition_receipt_evidence_is_mandatory_and_exact(
            self) -> None:
        self.assertEqual(
            ACTIVATION.PROFILE_TRANSITION_RECEIPT_SCHEMA,
            PROFILE_PRODUCER.ROUND114_TRANSITION_RECEIPT_SCHEMA)
        self.assertEqual(
            ACTIVATION.PROFILE_TRANSITION_RECEIPT_VERSION,
            PROFILE_PRODUCER.ROUND114_TRANSITION_RECEIPT_VERSION)
        self.assertEqual(
            ACTIVATION.PROFILE_TRANSITION_RECEIPT_FIELDS,
            PROFILE_PRODUCER.ROUND114_TRANSITION_RECEIPT_FIELDS)
        self.assertEqual(
            ACTIVATION.PROFILE_TRANSITION_PREIMAGE_PATH,
            PROFILE_PRODUCER.ROUND114_TRANSITION_PREIMAGE_PATH)
        self.assertEqual(
            ACTIVATION.PROFILE_TRANSITION_PREIMAGE_SCHEMA,
            PROFILE_PRODUCER.ROUND114_TRANSITION_PREIMAGE_SCHEMA)
        self.assertEqual(
            ACTIVATION.PROFILE_TRANSITION_PREIMAGE_VERSION,
            PROFILE_PRODUCER.ROUND114_TRANSITION_PREIMAGE_VERSION)
        self.assertEqual(
            ACTIVATION.PROFILE_TRANSITION_PREIMAGE_FIELDS,
            PROFILE_PRODUCER.ROUND114_TRANSITION_PREIMAGE_FIELDS)
        valid = json.loads(profile_receipt(0))
        transition = valid["dormant_paper_to_watch_transition_receipt"]
        self.assertEqual(
            transition["path"], str(ACTIVATION.PROFILE_TRANSITION_RECEIPT_PATH))
        ACTIVATION.validate_profile_receipt(ACTIVATION.canonical_bytes(valid))
        for field, replacement in (
                ("path", "/tmp/forged-transition.json"),
                ("sha256", "sha256:" + "z" * 64),
                ("body_sha256", "sha256:" + "z" * 64),
                ("mode", stat.S_IFREG | 0o644),
                ("nlink", 2), ("uid", 1), ("gid", 1), ("bytes", 0)):
            forged = copy.deepcopy(valid)
            forged["dormant_paper_to_watch_transition_receipt"][field] = (
                replacement)
            forged = ACTIVATION.seal({
                key: value for key, value in forged.items()
                if key != "body_sha256"})
            with self.subTest(field=field), self.assertRaisesRegex(
                    ACTIVATION.ActivationError,
                    "ACTIVATION_PROFILE_RECEIPT_INVALID"):
                ACTIVATION.validate_profile_receipt(
                    ACTIVATION.canonical_bytes(forged))
        missing = copy.deepcopy(valid)
        del missing["dormant_paper_to_watch_transition_receipt"]
        missing = ACTIVATION.seal({
            key: value for key, value in missing.items()
            if key != "body_sha256"})
        with self.assertRaisesRegex(
                ACTIVATION.ActivationError,
                "ACTIVATION_PROFILE_RECEIPT_INVALID"):
            ACTIVATION.validate_profile_receipt(
                ACTIVATION.canonical_bytes(missing))

    def test_profile_deployer_missing_round114_apis_is_rejected(self) -> None:
        transition_fields = repr(
            set(ACTIVATION.PROFILE_TRANSITION_RECEIPT_FIELDS))
        preimage_fields = repr(set(ACTIVATION.PROFILE_TRANSITION_PREIMAGE_FIELDS))
        source = "\n".join((
            "from pathlib import Path",
            "ROUND114_RECEIPT_SCHEMA = " +
                repr("hepta.p1-watch-profile-deployment-receipt.v8"),
            "ROUND114_RECEIPT_VERSION = 8",
            "ROUND114_RECEIPT_PATH = Path(" +
                repr(str(ACTIVATION.PROFILE_RECEIPT_PATH)) + ")",
            "ROUND114_TRANSITION_RECEIPT_SCHEMA = " +
                repr(ACTIVATION.PROFILE_TRANSITION_RECEIPT_SCHEMA),
            "ROUND114_TRANSITION_RECEIPT_VERSION = 2",
            "ROUND114_TRANSITION_RECEIPT_PATH = Path(" +
                repr(str(ACTIVATION.PROFILE_TRANSITION_RECEIPT_PATH)) + ")",
            "ROUND114_TRANSITION_RECEIPT_FIELDS = frozenset(" +
                transition_fields + ")",
            "ROUND114_TRANSITION_PREIMAGE_SCHEMA = " +
                repr(ACTIVATION.PROFILE_TRANSITION_PREIMAGE_SCHEMA),
            "ROUND114_TRANSITION_PREIMAGE_VERSION = 1",
            "ROUND114_TRANSITION_PREIMAGE_PATH = Path(" +
                repr(str(ACTIVATION.PROFILE_TRANSITION_PREIMAGE_PATH)) + ")",
            "ROUND114_TRANSITION_PREIMAGE_FIELDS = frozenset(" +
                preimage_fields + ")",
            "ROUND114_RECEIPT_FIELDS = frozenset(" +
                repr(set(ACTIVATION.PROFILE_RECEIPT_FIELDS)) + ")",
            "ROUND95_RECEIPT_PATH = Path(" +
                repr(str(ACTIVATION.PREDECESSOR_PROFILE_RECEIPT_PATH)) + ")",
            "ROUND95_RECEIPT_FILE_SHA256 = " +
                repr(ACTIVATION.PREDECESSOR_PROFILE_RECEIPT_FILE_SHA256),
            "LEGACY_RECEIPT_FILE_SHA256 = " +
                repr(ACTIVATION.LEGACY_PROFILE_RECEIPT_FILE_SHA256),
        )).encode("ascii")
        binding = SimpleNamespace(profile_deployer_payload=source)
        with mock.patch.object(
                ACTIVATION, "validate_shadow_install_binding",
                return_value={}):
            with self.assertRaisesRegex(
                    ACTIVATION.ActivationError,
                    "ACTIVATION_PROFILE_DEPLOYER_SOURCE_INVALID"):
                ACTIVATION._load_verified_profile_deployer(binding)
        missing_preimage_field = source.decode("ascii").replace(
            transition_fields,
            repr(set(ACTIVATION.PROFILE_TRANSITION_RECEIPT_FIELDS) -
                 {"preimage_evidence"})) + "\n" + "\n".join((
                     "def validate_round114_receipt(*_args): return ({}, '')",
                     "def validate_round114_receipt_state_binding(*_args): pass",
                 ))
        binding = SimpleNamespace(
            profile_deployer_payload=missing_preimage_field.encode("ascii"))
        with mock.patch.object(
                ACTIVATION, "validate_shadow_install_binding",
                return_value={}):
            with self.assertRaisesRegex(
                    ACTIVATION.ActivationError,
                    "ACTIVATION_PROFILE_DEPLOYER_SOURCE_INVALID"):
                ACTIVATION._load_verified_profile_deployer(binding)

    def test_manager_epoch_must_rebind_exactly(self) -> None:
        fields = {"MainPID": "10", "InvocationID": "a" * 32}
        ACTIVATION.ProductionExecutor._require_manager_rebind(
            fields, dict(fields), "REBOUND")
        with self.assertRaisesRegex(ACTIVATION.ActivationError, "REBOUND"):
            ACTIVATION.ProductionExecutor._require_manager_rebind(
                fields, {**fields, "MainPID": "11"}, "REBOUND")

    def test_actual_profile_artifacts_are_reopened_and_inode_drift_fails(
            self) -> None:
        with tempfile.TemporaryDirectory(dir=ROOT) as temporary:
            root = Path(temporary)
            uid, gid = os.geteuid(), os.getegid()
            target = root / "alpha.env"
            receipt = root / "round114.json"
            new_payload = b"new-profile\n"
            receipt_payload = b"round114-receipt\n"
            target.write_bytes(new_payload)
            target.chmod(0o644)
            receipt.write_bytes(receipt_payload)
            receipt.chmod(0o600)
            evidence = {"fixture": "install-v3"}
            document = {"shadow_install_evidence": evidence}
            target_identity = ACTIVATION.stable_identity(os.stat(target))
            artifacts = SimpleNamespace(target_identity=target_identity)

            class Snapshot:
                def __init__(self, payload: bytes, metadata: os.stat_result):
                    self.payload = payload
                    self.metadata = metadata

            def validate_current(snapshot, expected):
                self.assertEqual(snapshot.payload, receipt_payload)
                self.assertEqual(expected, evidence)
                return document, ACTIVATION.digest_bytes(receipt_payload)

            def require_unchanged(current, _expected_sha):
                if (
                        current is not artifacts or
                        ACTIVATION.stable_identity(os.stat(target)) !=
                            target_identity):
                    raise RuntimeError("profile artifact rebound")
                return current

            module = SimpleNamespace(
                FileSnapshot=Snapshot,
                validate_round114_receipt=validate_current,
                read_rebind_artifacts=lambda _sha: artifacts,
                validate_round114_receipt_state_binding=
                    lambda current, bound: self.assertTrue(
                        current is document and bound is artifacts),
                require_rebind_artifacts_unchanged=require_unchanged,
            )
            with ExitStack() as stack:
                stack.enter_context(mock.patch.object(
                    ACTIVATION, "ROOT_UID", uid))
                stack.enter_context(mock.patch.object(
                    ACTIVATION, "ROOT_GID", gid))
                stack.enter_context(mock.patch.object(
                    ACTIVATION, "PROFILE_RECEIPT_PATH", receipt))
                shadow_binding = object()
                stack.enter_context(mock.patch.object(
                    ACTIVATION, "validate_shadow_install_binding",
                    return_value=evidence))
                binding = ACTIVATION._bind_profile_artifacts_with_module(
                    module, receipt_payload, os.stat(receipt), evidence)
                ACTIVATION.validate_profile_artifact_binding(
                    binding, shadow_binding)
                replacement = root / "replacement"
                replacement.write_bytes(new_payload)
                replacement.chmod(0o644)
                replacement.replace(target)
                with self.assertRaisesRegex(
                        ACTIVATION.ActivationError,
                        "ACTIVATION_PROFILE_ARTIFACT_REBOUND"):
                    ACTIVATION.validate_profile_artifact_binding(
                        binding, shadow_binding)


class ShadowInstallQuarantineGuardTests(unittest.TestCase):
    def _patch_guard_fixture(self, lock_path: Path) -> ExitStack:
        stack = ExitStack()
        stack.enter_context(mock.patch.object(
            ACTIVATION, "ROOT_UID", os.geteuid()))
        stack.enter_context(mock.patch.object(
            ACTIVATION, "ROOT_GID", os.getegid()))
        stack.enter_context(mock.patch.object(
            ACTIVATION, "SHADOW_INSTALL_LOCK_PATH", lock_path))
        return stack

    def test_guard_is_local_never_reads_or_executes_installer_payload(
            self) -> None:
        with tempfile.TemporaryDirectory(dir=ROOT) as temporary:
            lock_path = Path(temporary) / ".shadow-runtime-install.lock"
            lock_path.write_bytes(b"")
            lock_path.chmod(0o600)
            with self._patch_guard_fixture(lock_path), \
                    mock.patch.object(
                        ACTIVATION, "secure_read",
                        side_effect=AssertionError(
                            "quarantine guard read rejected payload")), \
                    mock.patch.object(
                        ACTIVATION.importlib.util, "module_from_spec",
                        side_effect=AssertionError(
                            "quarantine guard executed rejected payload")):
                guard = ACTIVATION.acquire_shadow_install_quarantine_guard()
                try:
                    ACTIVATION.validate_shadow_install_quarantine_guard(guard)
                    with self.assertRaisesRegex(
                            ACTIVATION.ActivationError,
                            "ACTIVATION_SHADOW_QUARANTINE_GUARD_INVALID"):
                        ACTIVATION.acquire_shadow_install_quarantine_guard()
                finally:
                    ACTIVATION.release_shadow_install_quarantine_guard(guard)
                reacquired = (
                    ACTIVATION.acquire_shadow_install_quarantine_guard())
                ACTIVATION.release_shadow_install_quarantine_guard(reacquired)

    def test_guard_rejects_missing_symlink_and_wrong_metadata(self) -> None:
        with tempfile.TemporaryDirectory(dir=ROOT) as temporary:
            root = Path(temporary)
            lock_path = root / ".shadow-runtime-install.lock"
            with self._patch_guard_fixture(lock_path):
                with self.assertRaisesRegex(
                        ACTIVATION.ActivationError,
                        "ACTIVATION_SHADOW_QUARANTINE_GUARD_INVALID"):
                    ACTIVATION.acquire_shadow_install_quarantine_guard()
                target = root / "target"
                target.write_bytes(b"")
                target.chmod(0o600)
                lock_path.symlink_to(target.name)
                with self.assertRaisesRegex(
                        ACTIVATION.ActivationError,
                        "ACTIVATION_SHADOW_QUARANTINE_GUARD_INVALID"):
                    ACTIVATION.acquire_shadow_install_quarantine_guard()
                lock_path.unlink()
                lock_path.write_bytes(b"nonempty")
                lock_path.chmod(0o600)
                with self.assertRaisesRegex(
                        ACTIVATION.ActivationError,
                        "ACTIVATION_SHADOW_QUARANTINE_GUARD_INVALID"):
                    ACTIVATION.acquire_shadow_install_quarantine_guard()

    def test_guard_rejects_named_inode_replacement_and_closes_old_lock(
            self) -> None:
        with tempfile.TemporaryDirectory(dir=ROOT) as temporary:
            root = Path(temporary)
            lock_path = root / ".shadow-runtime-install.lock"
            lock_path.write_bytes(b"")
            lock_path.chmod(0o600)
            with self._patch_guard_fixture(lock_path):
                guard = ACTIVATION.acquire_shadow_install_quarantine_guard()
                saved = root / "saved-lock"
                lock_path.rename(saved)
                lock_path.write_bytes(b"")
                lock_path.chmod(0o600)
                with self.assertRaisesRegex(
                        ACTIVATION.ActivationError,
                        "ACTIVATION_SHADOW_QUARANTINE_GUARD_REBOUND"):
                    ACTIVATION.validate_shadow_install_quarantine_guard(guard)
                with self.assertRaisesRegex(
                        ACTIVATION.ActivationError,
                        "ACTIVATION_SHADOW_QUARANTINE_GUARD_RELEASE_FAILED"):
                    ACTIVATION.release_shadow_install_quarantine_guard(guard)
                replacement = (
                    ACTIVATION.acquire_shadow_install_quarantine_guard())
                ACTIVATION.release_shadow_install_quarantine_guard(replacement)


class BoundaryTests(unittest.TestCase):
    @staticmethod
    def _private_state(root: Path) -> tuple[Path, Path]:
        state = root / "hepta-shadow-watch-alpha"
        state.mkdir(mode=0o700)
        private = state / "private"
        private.mkdir(mode=0o700)
        return state, private

    @staticmethod
    def _patch_private(
        stack: ExitStack,
        private: Path,
        *,
        watch_uid: int | None = None,
        watch_gid: int | None = None,
    ) -> tuple[int, int]:
        uid, gid = os.geteuid(), os.getegid()
        for name, value in {
            "ROOT_UID": uid,
            "ROOT_GID": gid,
            "WATCH_UID": uid if watch_uid is None else watch_uid,
            "WATCH_GID": gid if watch_gid is None else watch_gid,
            "WATCH_PRIVATE": private,
        }.items():
            stack.enter_context(mock.patch.object(ACTIVATION, name, value))
        return uid, gid

    def test_session_bootstrap_lock_must_be_idle_and_is_held(self) -> None:
        with tempfile.TemporaryDirectory(dir=ROOT) as temporary:
            root = Path(temporary)
            profile = root / "alpha.env"
            profile.write_bytes(ACTIVATION.PROFILE_PAYLOAD)
            profile.chmod(0o644)
            kill_parent = root / "paper-control"
            kill_parent.mkdir(mode=0o750)
            kill_parent.chmod(0o750)
            self.assertEqual(stat.S_IMODE(kill_parent.stat().st_mode), 0o750)
            kill_switch = kill_parent / "kill-switch"
            kill_switch.write_bytes(b"engaged")
            kill_switch.chmod(0o440)
            policies = root / "policies"
            policies.mkdir(mode=0o755)
            policies.chmod(0o755)
            self.assertEqual(stat.S_IMODE(policies.stat().st_mode), 0o755)
            sessions = root / "sessions"
            sessions.mkdir(mode=0o711)
            sessions.chmod(0o711)
            self.assertEqual(stat.S_IMODE(sessions.stat().st_mode), 0o711)
            idle = sessions / ".session-bootstrap.lock"
            idle.write_bytes(b"")
            idle.chmod(0o600)
            _state, private = self._private_state(root)
            uid, gid = os.geteuid(), os.getegid()
            with ExitStack() as stack:
                for name, value in {
                    "ROOT_UID": uid, "ROOT_GID": gid,
                    "WATCH_UID": uid, "WATCH_GID": gid,
                    "PAPER_CONTROL_GID": gid, "PROFILE_PATH": profile,
                    "KILL_SWITCH_PATH": kill_switch,
                    "PAPER_POLICY_ROOT": policies,
                    "WATCH_SESSIONS": sessions, "WATCH_PRIVATE": private,
                    "WATCH_EXPORT": root / "absent-export",
                    "WATCH_CUSTODIAN_TRANSACTION": root / "absent-transaction",
                }.items():
                    stack.enter_context(mock.patch.object(
                        ACTIVATION, name, value))
                held = os.open(idle, os.O_RDWR)
                try:
                    import fcntl
                    fcntl.flock(held, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    with self.assertRaisesRegex(
                            ACTIVATION.ActivationError,
                            "ACTIVATION_SESSION_BOOTSTRAP_BUSY"):
                        ACTIVATION.validate_local_boundaries()
                    fcntl.flock(held, fcntl.LOCK_UN)
                    evidence = ACTIVATION.validate_local_boundaries()
                    self.assertEqual(evidence, {
                        "export_absent": True,
                        "sessions_authority_count": 0,
                        "private_authority_count": 0,
                        "custodian_transaction_absent": True,
                        "session_bootstrap_idle_lock_observed": True,
                    })
                finally:
                    os.close(held)

    def test_watch_private_accepts_exact_service_owned_host_shape(self) -> None:
        with tempfile.TemporaryDirectory(dir=ROOT) as temporary:
            _state, private = self._private_state(Path(temporary))
            with ExitStack() as stack:
                self._patch_private(stack, private)
                ACTIVATION._validate_watch_private_directory()

    def test_watch_private_rejects_wrong_owner_or_mode(self) -> None:
        for mutation in (
            "parent_uid", "parent_gid", "parent_mode", "private_mode",
        ):
            with self.subTest(mutation=mutation), \
                    tempfile.TemporaryDirectory(dir=ROOT) as temporary:
                state, private = self._private_state(Path(temporary))
                uid, gid = os.geteuid(), os.getegid()
                watch_uid = uid + 1 if mutation == "parent_uid" else uid
                watch_gid = gid + 1 if mutation == "parent_gid" else gid
                if mutation == "parent_mode":
                    state.chmod(0o750)
                elif mutation == "private_mode":
                    private.chmod(0o750)
                with ExitStack() as stack:
                    self._patch_private(
                        stack, private,
                        watch_uid=watch_uid, watch_gid=watch_gid)
                    with self.assertRaisesRegex(
                            ACTIVATION.ActivationError,
                            "ACTIVATION_AUTHORITY_RESIDUE"):
                        ACTIVATION._validate_watch_private_directory()

    def test_watch_private_rejects_independent_leaf_uid_or_gid(self) -> None:
        for mutation in ("private_uid", "private_gid"):
            with self.subTest(mutation=mutation), \
                    tempfile.TemporaryDirectory(dir=ROOT) as temporary:
                state, private = self._private_state(Path(temporary))
                private_inode = private.stat().st_ino
                real_fstat = os.fstat
                leaf_observed = False

                def fstat_with_wrong_leaf_owner(descriptor: int):
                    nonlocal leaf_observed
                    metadata = real_fstat(descriptor)
                    if metadata.st_ino != private_inode:
                        return metadata
                    leaf_observed = True
                    values = {
                        field: getattr(metadata, field)
                        for field in (
                            "st_dev", "st_ino", "st_mode", "st_nlink",
                            "st_uid", "st_gid", "st_size", "st_mtime_ns",
                            "st_ctime_ns",
                        )
                    }
                    if mutation == "private_uid":
                        values["st_uid"] += 1
                    else:
                        values["st_gid"] += 1
                    return SimpleNamespace(**values)

                with ExitStack() as stack:
                    uid, gid = self._patch_private(stack, private)
                    self.assertEqual(
                        (state.stat().st_uid, state.stat().st_gid),
                        (uid, gid))
                    stack.enter_context(mock.patch.object(
                        ACTIVATION.os, "fstat",
                        side_effect=fstat_with_wrong_leaf_owner))
                    with self.assertRaisesRegex(
                            ACTIVATION.ActivationError,
                            "ACTIVATION_AUTHORITY_RESIDUE"):
                        ACTIVATION._validate_watch_private_directory()
                self.assertTrue(leaf_observed)

    def test_watch_private_rejects_extra_entries_and_symlinks(self) -> None:
        for mutation in (
            "parent_extra", "private_extra", "parent_symlink",
            "private_symlink",
        ):
            with self.subTest(mutation=mutation), \
                    tempfile.TemporaryDirectory(dir=ROOT) as temporary:
                root = Path(temporary)
                state, private = self._private_state(root)
                selected = private
                if mutation == "parent_extra":
                    (state / "private-hidden").mkdir(mode=0o700)
                elif mutation == "private_extra":
                    (private / "snapshot.json").write_bytes(b"{}\n")
                elif mutation == "parent_symlink":
                    alias = root / "hepta-shadow-watch-alias"
                    alias.symlink_to(state.name, target_is_directory=True)
                    selected = alias / "private"
                else:
                    preserved = state / "private-preserved"
                    private.rename(preserved)
                    private.symlink_to(
                        preserved.name, target_is_directory=True)
                with ExitStack() as stack:
                    self._patch_private(stack, selected)
                    with self.assertRaisesRegex(
                            ACTIVATION.ActivationError,
                            "ACTIVATION_AUTHORITY_RESIDUE"):
                        ACTIVATION._validate_watch_private_directory()

    def test_watch_private_rejects_parent_leaf_swaps_and_aba(self) -> None:
        for mutation in (
            "parent_swap", "parent_aba", "private_swap", "private_aba",
        ):
            with self.subTest(mutation=mutation), \
                    tempfile.TemporaryDirectory(dir=ROOT) as temporary:
                root = Path(temporary)
                state, private = self._private_state(root)
                state_inode = state.stat().st_ino
                private_inode = private.stat().st_ino
                real_listdir = os.listdir
                raced = False

                def listdir_with_race(target: object) -> list[str]:
                    nonlocal raced
                    names = real_listdir(target)
                    if raced or type(target) is not int:
                        return names
                    inode = os.fstat(target).st_ino
                    if mutation in {"parent_swap", "parent_aba"} and \
                            inode == state_inode:
                        raced = True
                        preserved = root / "hepta-shadow-watch-preserved"
                        state.rename(preserved)
                        if mutation == "parent_swap":
                            replacement, _ = self._private_state(root)
                            self.assertNotEqual(
                                replacement.stat().st_ino, state_inode)
                        else:
                            preserved.rename(state)
                    elif mutation in {"private_swap", "private_aba"} and \
                            inode == private_inode:
                        raced = True
                        preserved = state / "private-preserved"
                        private.rename(preserved)
                        if mutation == "private_swap":
                            private.mkdir(mode=0o700)
                        else:
                            preserved.rename(private)
                    return names

                with ExitStack() as stack:
                    self._patch_private(stack, private)
                    stack.enter_context(mock.patch.object(
                        ACTIVATION.os, "listdir", side_effect=listdir_with_race))
                    with self.assertRaisesRegex(
                            ACTIVATION.ActivationError,
                            "ACTIVATION_AUTHORITY_RESIDUE"):
                        ACTIVATION._validate_watch_private_directory()
                self.assertTrue(raced)

    def test_watch_private_rejects_canonical_reopen_swaps_and_aba(self) -> None:
        for mutation in (
            "parent_swap", "parent_aba", "private_swap", "private_aba",
        ):
            with self.subTest(mutation=mutation), \
                    tempfile.TemporaryDirectory(dir=ROOT) as temporary:
                root = Path(temporary)
                state, private = self._private_state(root)
                state_inode = state.stat().st_ino
                private_inode = private.stat().st_ino
                real_open = ACTIVATION.open_anchored_directory
                parent_opens = 0
                raced = False

                def open_with_canonical_race(
                    path: Path,
                    *,
                    leaf_policy: tuple[int, int, int] | None = None,
                    procfs: bool = False,
                ) -> int:
                    nonlocal parent_opens, raced
                    if path != state:
                        return real_open(
                            path, leaf_policy=leaf_policy, procfs=procfs)
                    parent_opens += 1
                    if parent_opens == 2 and mutation.startswith("parent_"):
                        raced = True
                        preserved = root / "hepta-shadow-watch-preserved"
                        state.rename(preserved)
                        if mutation == "parent_swap":
                            replacement, _ = self._private_state(root)
                            self.assertNotEqual(
                                replacement.stat().st_ino, state_inode)
                        else:
                            preserved.rename(state)
                    descriptor = real_open(
                        path, leaf_policy=leaf_policy, procfs=procfs)
                    if parent_opens == 3 and mutation.startswith("private_"):
                        raced = True
                        preserved = state / "private-preserved"
                        private.rename(preserved)
                        if mutation == "private_swap":
                            private.mkdir(mode=0o700)
                            self.assertNotEqual(
                                private.stat().st_ino, private_inode)
                        else:
                            preserved.rename(private)
                    return descriptor

                with ExitStack() as stack:
                    self._patch_private(stack, private)
                    stack.enter_context(mock.patch.object(
                        ACTIVATION, "open_anchored_directory",
                        side_effect=open_with_canonical_race))
                    with self.assertRaisesRegex(
                            ACTIVATION.ActivationError,
                            "ACTIVATION_AUTHORITY_RESIDUE"):
                        ACTIVATION._validate_watch_private_directory()
                self.assertTrue(raced)
                self.assertEqual(
                    parent_opens,
                    2 if mutation.startswith("parent_") else 3)


class StaleBundleTests(unittest.TestCase):
    def _document(self, values: dict[str, object]) -> bytes:
        return ACTIVATION.canonical_bytes(ACTIVATION.seal(values))

    def _fixture(self, root: Path):
        source_base = root / "p1-admission"
        source_base.mkdir(mode=0o700)
        sources = {
            110: tuple(source_base / name for name in (
                "private110", "public110", "probe109", "soak110")),
            112: tuple(source_base / name for name in (
                "private112", "public112", "probe111", "soak112")),
        }
        hashes: dict[int, dict[tuple[int, str], str]] = {}
        for number, paths in sources.items():
            probe = number - 1
            probe_id = f"hepta-p1-shadow-load-probe-round{probe}-20260801"
            formal_id = f"hepta-p1-shadow-soak-round{number}-20260801"
            for index, path in enumerate(paths):
                path.mkdir(mode=0o755 if index == 1 else 0o700)
                path.chmod(0o755 if index == 1 else 0o700)
                if index in (2, 3):
                    (path / "observer").mkdir(mode=0o700)
            documents = {
                (0, "launcher-receipt.json"): {
                    "schema": "hepta.p1-shadow-admission-launcher-receipt.v1",
                    "version": 1, "status": "FAILED_CLOSED",
                    "domain_id": "alpha", "probe_campaign_id": probe_id,
                    "formal_campaign_id": formal_id,
                    "reason": "P1_LAUNCHER_COMMAND_REJECTED",
                    "authority_residue": False, "export_residue": False,
                    "cleanup_errors": [], "paper_authorized": False,
                    "live_authorized": False, "mutation_authorized": False,
                    "direct_broker_access": False,
                },
                (0, "launcher-state.json"): {
                    "schema": "hepta.p1-shadow-admission-launcher-state.v1",
                    "version": 1, "status": "STARTING", "domain_id": "alpha",
                    "probe_campaign_id": probe_id, "formal_campaign_id": formal_id,
                    "paper_authorized": False, "live_authorized": False,
                    "mutation_authorized": False, "direct_broker_access": False,
                },
                (1, "load-probe-authority-marker.json"): {
                    "schema": "hepta.p1-shadow-load-probe-authority-marker.v1",
                    "version": 1, "status": "ACTIVE", "scope": "LOAD_PROBE",
                    "mode": "LOAD_PROBE", "campaign_id": probe_id,
                    "execution_binding_status": "PENDING_FIRST_SNAPSHOT",
                    "paper_authorized": False, "live_authorized": False,
                    "mutation_authorized": False, "direct_broker_access": False,
                },
                (1, "load-probe-policy.json"): {
                    "schema": "hepta.strategy-shadow-observation-policy.v1",
                    "version": 1, "campaign_id": probe_id,
                    "paper_authorized": False, "live_authorized": False,
                    "mutation_attempted": False, "direct_broker_access": False,
                },
                (2, "controller-status.json"): {
                    "schema": "hepta.p1-shadow-observer-controller-status.v1",
                    "version": 1, "campaign_id": probe_id,
                    "state": "FAILED" if number == 110 else "WAITING_FOR_EXPORT",
                    "reason": "P1_CONTROLLER_ENVIRONMENT_INVALID"
                        if number == 110 else None,
                    "paper_authorized": False, "live_authorized": False,
                    "mutation_attempted": False, "direct_broker_access": False,
                },
            }
            hashes[number] = {}
            for (index, name), document in documents.items():
                payload = self._document(document)
                path = paths[index] / name
                path.write_bytes(payload)
                path.chmod(0o644 if index == 1 else 0o600)
                hashes[number][(index, name)] = hashlib.sha256(payload).hexdigest()
        quarantine = source_base / "quarantine" / "activation-round86"
        destinations = {
            number: tuple(
                quarantine / f"round{number}" / f"bundle-{index}"
                for index in range(4))
            for number in (110, 112)
        }
        return source_base, sources, destinations, quarantine, hashes

    def test_stale_digest_and_noreplace_rename(self) -> None:
        with tempfile.TemporaryDirectory(dir=ROOT) as temporary:
            base, sources, destinations, quarantine, hashes = self._fixture(
                Path(temporary))
            uid, gid = os.geteuid(), os.getegid()
            policies = ((uid, gid, 0o700), (uid, gid, 0o755),
                        (uid, gid, 0o700), (uid, gid, 0o700))
            with ExitStack() as stack:
                for name, value in {
                    "ROOT_UID": uid, "ROOT_GID": gid,
                    "STALE_ROOT_POLICIES": policies,
                    "STALE_FILE_SHA256": hashes,
                    "STALE_QUARANTINE_ROOT": quarantine,
                }.items():
                    stack.enter_context(mock.patch.object(ACTIVATION, name, value))
                stack.enter_context(mock.patch.object(
                    ACTIVATION, "stale_paths", lambda: sources))
                stack.enter_context(mock.patch.object(
                    ACTIVATION, "stale_quarantine_paths", lambda: destinations))
                before = ACTIVATION.validate_stale_bundles()
                digests = [item.evidence["bundle_sha256"] for item in before]
                evidence = ACTIVATION.quarantine_stale_bundles(before)
                self.assertEqual([item["status"] for item in evidence],
                                 ["QUARANTINED", "QUARANTINED"])
                self.assertEqual([item["bundle_sha256"] for item in evidence],
                                 digests)
                self.assertTrue(all(
                    not path.exists() for paths in sources.values() for path in paths))
                self.assertTrue(all(
                    path.exists() for paths in destinations.values() for path in paths))

    def test_stale_root_replacement_and_parent_symlink_fail_closed(self) -> None:
        with tempfile.TemporaryDirectory(dir=ROOT) as temporary:
            root = Path(temporary)
            _base, sources, destinations, quarantine, hashes = self._fixture(root)
            uid, gid = os.geteuid(), os.getegid()
            policies = ((uid, gid, 0o700), (uid, gid, 0o755),
                        (uid, gid, 0o700), (uid, gid, 0o700))
            with ExitStack() as stack:
                for name, value in {
                    "ROOT_UID": uid, "ROOT_GID": gid,
                    "STALE_ROOT_POLICIES": policies,
                    "STALE_FILE_SHA256": hashes,
                    "STALE_QUARANTINE_ROOT": quarantine,
                }.items():
                    stack.enter_context(mock.patch.object(ACTIVATION, name, value))
                stack.enter_context(mock.patch.object(
                    ACTIVATION, "stale_paths", lambda: sources))
                stack.enter_context(mock.patch.object(
                    ACTIVATION, "stale_quarantine_paths", lambda: destinations))
                validated = ACTIVATION.validate_stale_bundles()
                original = sources[110][0]
                held = original.with_name("held-private110")
                original.rename(held)
                shutil.copytree(held, original)
                with self.assertRaisesRegex(
                        ACTIVATION.ActivationError,
                        "ACTIVATION_STALE_BUNDLE_REBOUND"):
                    ACTIVATION.quarantine_stale_bundles(validated)
            real = root / "real"
            real.mkdir(mode=0o700)
            (root / "link").symlink_to(real, target_is_directory=True)
            with mock.patch.object(ACTIVATION, "ROOT_UID", uid), \
                    mock.patch.object(ACTIVATION, "ROOT_GID", gid):
                with self.assertRaises(ACTIVATION.ActivationError):
                    ACTIVATION.open_anchored_directory(root / "link")


class UnitContractTests(unittest.TestCase):
    def test_gateway_proc_snapshot_uses_kernel_root_owned_projection(
            self) -> None:
        executor = ACTIVATION.ProductionExecutor()
        fields = {
            "LoadState": "loaded", "ActiveState": "active",
            "SubState": "running", "MainPID": "4242",
            "InvocationID": "a" * 32,
            "ExecMainStartTimestampMonotonic": "12345",
            "ExecStart": "/usr/libexec/hepta-tool-gatewayd",
            "EnvironmentFiles": str(ACTIVATION.PROFILE_PATH),
            "BindsTo": ACTIVATION.BROKER_UNIT,
            "After": ACTIVATION.BROKER_UNIT,
        }
        values = dict(ACTIVATION.PROFILE_ITEMS)
        values.update({
            "HEPTA_TOOL_SOCKET": "/run/hepta-agent-alpha/tools.sock",
            "HEPTA_TOOL_AGENT_ID": "alpha",
            "HEPTA_TOOL_SUPERVISOR_LEASE_STORE":
                "/var/lib/hepta-tool-gateway-alpha/session-leases.hsl2",
            "HEPTA_TOOL_SUPERVISOR_AUDIT_JOURNAL":
                "/var/lib/hepta-tool-gateway-alpha/session-audit.jsonl",
        })
        environ = b"\0".join(
            f"{key}={value}".encode("ascii")
            for key, value in values.items()) + b"\0"
        pre_exec_environ = b"PATH=/usr/bin\0"
        with mock.patch.object(executor, "_show", return_value=fields), \
                mock.patch.object(
                    ACTIVATION, "_proc_payload_snapshot",
                    side_effect=[
                        ({"environ": pre_exec_environ}, 99),
                        ({"environ": environ}, 99),
                    ]) as snapshot, \
                mock.patch.object(ACTIVATION.time, "sleep") as sleep, \
                mock.patch.object(
                    ACTIVATION, "secure_read", return_value=(b"payload", None)), \
                mock.patch.object(
                    ACTIVATION, "_socket_identity",
                    side_effect=((1, 2), (3, 4))):
            evidence = executor.attest_gateway()
        self.assertEqual(snapshot.call_count, 2)
        snapshot.assert_called_with(
            4242, ("environ",), uid=ACTIVATION.ROOT_UID,
            gid=ACTIVATION.ROOT_GID)
        sleep.assert_called_once_with(0.05)
        self.assertEqual(evidence["gateway_main_pid"], 4242)

    def test_reconcile_runtime_drift_reports_exact_component(self) -> None:
        receipt = {
            "broker_after": broker_evidence(),
            "gateway_after": gateway_evidence(),
            "reconcile_timer": FakeExecutor().attest_reconcile_timer(),
            "paper_units": FakeExecutor().attest_paper_inactive(),
        }
        broker = broker_evidence()
        broker["main_pid"] = 999
        gateway = gateway_evidence()
        gateway["gateway_socket_inode"] = 999
        timer = FakeExecutor().attest_reconcile_timer()
        timer["active_state"] = "inactive"
        cases = (
            ("BROKER", "attest_broker", broker),
            ("GATEWAY", "attest_gateway", gateway),
            ("RECONCILE_TIMER", "attest_reconcile_timer", timer),
            ("PAPER_UNITS", "attest_paper_inactive", {}),
            ("BROKER_DENY_ALL", "deny_all", {
                "policy_sha256": "sha256:" + "2" * 64,
                "authorized_connectors": 0, "authorized_uids": [],
                "protected_ports": 4,
            }),
        )
        for component, method, observed in cases:
            with self.subTest(component=component):
                executor = FakeExecutor()
                with mock.patch.object(
                        executor, method, return_value=observed), \
                        self.assertRaisesRegex(
                            ACTIVATION.ActivationError,
                            "ACTIVATION_RUNTIME_DRIFT_" + component):
                    ACTIVATION._validate_reconcile_runtime_evidence(
                        executor, receipt)

    def test_reconcile_runtime_evidence_accepts_transient_broker_sample(
            self) -> None:
        expected = broker_evidence()
        transient = copy.deepcopy(expected)
        transient["tasks_current"] = 2
        receipt = {
            "broker_after": expected,
            "gateway_after": gateway_evidence(),
            "reconcile_timer": FakeExecutor().attest_reconcile_timer(),
            "paper_units": FakeExecutor().attest_paper_inactive(),
        }
        executor = FakeExecutor()
        with mock.patch.object(
                executor, "attest_broker",
                side_effect=[transient, expected]), \
                mock.patch.object(ACTIVATION.time, "sleep") as sleep:
            ACTIVATION._validate_reconcile_runtime_evidence(executor, receipt)
        sleep.assert_called_once_with(0.05)

    def test_reconcile_timer_accepts_waiting_or_running(self) -> None:
        for substate in ("waiting", "running"):
            executor = ACTIVATION.ProductionExecutor()
            fields = {
                "LoadState": "loaded", "ActiveState": "active",
                "SubState": substate, "Job": "", "UnitFileState": "enabled",
            }
            with mock.patch.object(
                    executor, "_show", side_effect=[fields, fields]):
                evidence = executor.attest_reconcile_timer()
            self.assertEqual(evidence["sub_state"], substate)

    def test_reconcile_timer_rejects_inactive_state(self) -> None:
        executor = ACTIVATION.ProductionExecutor()
        fields = {
            "LoadState": "loaded", "ActiveState": "inactive",
            "SubState": "dead", "Job": "", "UnitFileState": "enabled",
        }
        with mock.patch.object(executor, "_show", side_effect=[fields, fields]):
            with self.assertRaisesRegex(
                    ACTIVATION.ActivationError,
                    "ACTIVATION_RECONCILE_TIMER_NOT_ARMED"):
                executor.attest_reconcile_timer()

    def test_units_use_only_credential_backed_fixed_entrypoints(self) -> None:
        activation = (ROOT / "systemd/hepta-p1-watch-activation.service").read_text()
        reconcile = (ROOT / "systemd/hepta-p1-watch-activation-reconcile.service").read_text()
        timer = (ROOT / "systemd/hepta-p1-watch-activation-reconcile.timer").read_text()
        for payload, action in ((activation, "activate"), (reconcile, "reconcile")):
            self.assertNotIn("[Install]", payload)
            self.assertIn("LoadCredential=hepta-p1-watch-activation-transaction.py:", payload)
            self.assertIn("LoadCredential=hepta-p1-watch-profile-deployer.py:", payload)
            self.assertIn("LoadCredential=hepta-shadow-host-installer.py:", payload)
            self.assertIn("Environment=HEPTA_ACTIVATION_REQUIRE_CREDENTIALS=1", payload)
            self.assertIn(
                "ExecStart=/usr/bin/python3.12 -I -S "
                "${CREDENTIALS_DIRECTORY}/"
                f"hepta-p1-watch-activation-transaction.py {action}", payload)
        self.assertNotIn("Before=hepta-tool-gateway@alpha.service", activation)
        self.assertIn(
            "After=local-fs.target systemd-remount-fs.service", activation)
        self.assertNotIn("Requires=", activation)
        self.assertIn(
            "After=local-fs.target systemd-remount-fs.service "
            "hepta-p1-watch-activation.service", reconcile)
        self.assertIn("OnActiveSec=30s", timer)
        self.assertNotIn("OnBootSec=", timer)
        self.assertNotIn("Persistent=", timer)
        self.assertIn("[Install]", timer)
        self.assertIn(
            "Unit=hepta-p1-watch-activation-reconcile.service", timer)


if __name__ == "__main__":
    unittest.main()
