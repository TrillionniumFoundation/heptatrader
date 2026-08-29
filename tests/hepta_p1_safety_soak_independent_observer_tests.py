#!/usr/bin/env python3

from __future__ import annotations

import copy
from datetime import datetime
import importlib.util
import io
import os
from pathlib import Path
import stat
import sys
import tempfile
import unittest
from unittest import mock
from zoneinfo import ZoneInfo


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / \
    "hepta_p1_safety_soak_independent_observer.py"
SPEC = importlib.util.spec_from_file_location("p1_independent_observer", SCRIPT)
assert SPEC is not None and SPEC.loader is not None
OBSERVER = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = OBSERVER
SPEC.loader.exec_module(OBSERVER)


def load_module(name: str, path: Path):
    module_spec = importlib.util.spec_from_file_location(name, path)
    assert module_spec is not None and module_spec.loader is not None
    module = importlib.util.module_from_spec(module_spec)
    sys.modules[name] = module
    module_spec.loader.exec_module(module)
    return module


RECORDER = load_module(
    "p1_safety_soak_evidence_recorder_contract",
    ROOT / "scripts" / "hepta_p1_safety_soak_evidence_recorder.py")
AUDITOR = load_module(
    "p1_safety_soak_auditor_contract",
    ROOT / "scripts" / "hepta_p1_safety_soak_auditor.py")


SOURCE_SHA = "sha256:" + "1" * 64
POLICY_SHA = "sha256:" + "2" * 64
STRATEGY_SHA = "sha256:" + "3" * 64
FORMAL_POLICY_BODY = "sha256:" + "4" * 64
FORMAL_POLICY_FILE = "sha256:" + "5" * 64
CAMPAIGN_SHA = "sha256:" + "6" * 64
ACTION_SHA = "sha256:" + "7" * 64
CAMPAIGN_ID = "p1-soak-round1"
FORMAL_ID = "p1-formal-round1"
BOOT_ID = "11111111-2222-3333-4444-555555555555"
WALL_MS = 1_800_000_000_000
BOOTTIME_NS = 400_000_000_000_000
TRADING_DAYS = [
    "2026-08-03", "2026-08-04", "2026-08-05", "2026-08-06",
    "2026-08-07", "2026-08-10", "2026-08-11", "2026-08-12",
    "2026-08-13", "2026-08-14",
]
EXTRA_TRADING_DAYS = [
    "2026-08-17", "2026-08-18", "2026-08-19", "2026-08-20",
    "2026-08-21", "2026-08-24", "2026-08-25", "2026-08-26",
    "2026-08-27", "2026-08-28", "2026-08-31",
]
TRADING_ZONE = ZoneInfo(OBSERVER.TRADING_TIMEZONE)


def eligible_schedule(days: list[str]) -> list[int]:
    return [
        int(datetime.fromisoformat(day + "T10:00:00")
            .replace(tzinfo=TRADING_ZONE).timestamp() * 1000) + offset * 60_000
        for day in days for offset in range(20)
    ]


ELIGIBLE_SCHEDULE = eligible_schedule(TRADING_DAYS)


def digest(value: str) -> str:
    return OBSERVER.digest_bytes(value.encode("ascii"))


def predecessor_activation_success(module=OBSERVER) -> dict[str, object]:
    return {
        "receipt_path": module.PREDECESSOR_ACTIVATION_SUCCESS_PATH,
        "receipt_file_sha256":
            module.PREDECESSOR_ACTIVATION_SUCCESS_FILE_SHA256,
        "receipt_body_sha256":
            module.PREDECESSOR_ACTIVATION_SUCCESS_BODY_SHA256,
        "receipt_schema": "hepta.p1-watch-activation-receipt.v3",
        "receipt_version": 3, "receipt_status": "WATCH_GATEWAY_ACTIVATED",
        "receipt_round": 95, "receipt_domain": "alpha",
        "receipt_device": 8, "receipt_inode": 95,
        "receipt_mode": stat.S_IFREG | 0o600, "receipt_nlink": 1,
        "receipt_uid": 0, "receipt_gid": 0, "receipt_bytes": 4096,
        "receipt_mtime_ns": 95_000, "receipt_ctime_ns": 95_001,
    }


def predecessor_activation_failure(module=OBSERVER) -> dict[str, object]:
    return {
        "receipt_path": module.PREDECESSOR_ACTIVATION_FAILURE_PATH,
        "receipt_file_sha256":
            module.PREDECESSOR_ACTIVATION_FAILURE_FILE_SHA256,
        "receipt_body_sha256":
            module.PREDECESSOR_ACTIVATION_FAILURE_BODY_SHA256,
        "receipt_schema": "hepta.p1-watch-activation-failed-receipt.v2",
        "receipt_version": 2, "receipt_revision": 1,
        "receipt_status": "FAILED_CLOSED", "receipt_round": 95,
        "receipt_domain": "alpha", "receipt_reason": "FAILED_TEST_FIXTURE",
        "receipt_device": 8, "receipt_inode": 96,
        "receipt_mode": stat.S_IFREG | 0o600, "receipt_nlink": 1,
        "receipt_uid": 0, "receipt_gid": 0, "receipt_bytes": 4096,
        "receipt_mtime_ns": 96_000, "receipt_ctime_ns": 96_001,
        "journal_path": module.PREDECESSOR_ACTIVATION_FAILURE_JOURNAL_PATH,
        "journal_sha256":
            module.PREDECESSOR_ACTIVATION_FAILURE_JOURNAL_SHA256,
        "journal_record_count": 21, "journal_terminal_phase": "FAILED_CLOSED",
    }


def shadow_install_evidence(module: object, source_sha: str) -> dict:
    return {
        "schema": "hepta.shadow-runtime-install-consumption-evidence.v3",
        "version": 3,
        "receipt_path": module.SHADOW_INSTALL_RECEIPT_PATH,
        "receipt_file_sha256": digest("install-receipt-file"),
        "receipt_body_sha256": digest("install-receipt-body"),
        "manifest_path": module.SHADOW_INSTALL_MANIFEST_PATH,
        "manifest_file_sha256": digest("install-manifest-file"),
        "archive_sha256": digest("install-archive"),
        "source_baseline_sha256": source_sha,
        "installer_sha256": digest("installer"),
        "installed_file_count": 128,
        "installed_paths_sha256": digest("installed-paths"),
        "closure_sha256": digest("install-closure"),
        "transaction_lock": {
            "path": module.SHADOW_INSTALL_LOCK_PATH, "device": 8,
            "inode": 100, "nlink": 1, "uid": 0, "gid": 0,
            "mode": "0600", "size": 0, "mtime_ns": 1, "ctime_ns": 2,
            "created_during_transaction": False, "persistent": True,
            "held_during_transaction": True,
        },
        "default_deny_identity_sha256":
            module.SHADOW_DEFAULT_DENY_IDENTITY_SHA256,
        "lock_mode": "exclusive", "verified_under_lock": True,
        "domain": "alpha", "backup_root": module.SHADOW_INSTALL_BACKUP_ROOT,
        "paper_authorized": False, "live_authorized": False,
        "mutation_attempted": False, "direct_broker_access": False,
        "current_install_pointer_path":
            module.SHADOW_CURRENT_INSTALL_POINTER_PATH,
        "current_install_pointer_file_sha256": digest("install-pointer"),
        "install_generation": 22, "predecessor_install_generation": 21,
        "predecessor_current_install_pointer_file_sha256":
            module.SHADOW_PREDECESSOR_POINTER_SHA256,
    }


def unit(
    name: str, *, active: str = "inactive", sub: str = "dead", pid: int = 0,
    invocation: str = "", start: int = 0, restarts: int = 0,
) -> dict:
    return OBSERVER._state_seal({
        "unit": name, "load_state": "loaded", "active_state": active,
        "sub_state": sub, "unit_file_state": "static", "main_pid": pid,
        "invocation_id": invocation,
        "exec_main_start_timestamp_monotonic_us": start,
        "n_restarts": restarts,
    })


def process(
    pid: int, *, start: int | None = None, exe_device: int = 8,
    exe_inode: int | None = None,
) -> dict:
    return OBSERVER._state_seal({
        "pid": pid, "uid": 1000, "gid": 1000,
        "starttime_ticks": pid * 100 if start is None else start,
        "exe_device": exe_device,
        "exe_inode": pid + 1000 if exe_inode is None else exe_inode,
        "cgroup_sha256": digest(f"cgroup-{pid}"),
    })


def path_item(
    path: Path, *, present: bool = False, file_sha: str | None = None,
    body_sha: str | None = None, file_type: str = "regular",
    uid: int = 0, gid: int = 0, mode: int = 0o600,
    parent_uid: int = 0, parent_gid: int = 0, parent_mode: int = 0o700,
    size: int = 10,
) -> dict:
    common = {
        "path": str(path), "present": present, "parent_device": 8,
        "parent_inode": 20, "parent_uid": parent_uid,
        "parent_gid": parent_gid, "parent_mode": parent_mode,
        "parent_nlink": 2,
    }
    if present:
        common.update({
            "file_type": file_type, "device": 8,
            "inode": abs(hash(str(path))) % 100000 + 100,
            "uid": uid, "gid": gid, "mode": mode, "nlink": 1,
            "size": size, "mtime_ns": 100, "ctime_ns": 100,
            "content_file_sha256": file_sha,
            "content_body_sha256": body_sha,
        })
    else:
        common.update({
            "file_type": None, "device": None, "inode": None, "uid": None,
            "gid": None, "mode": None, "nlink": None, "size": None,
            "mtime_ns": None, "ctime_ns": None,
            "content_file_sha256": None, "content_body_sha256": None,
        })
    return OBSERVER._state_seal(common)


def broker(
    *, connectors: int = 0, uids: list[int] | None = None,
    checked: int = BOOTTIME_NS,
) -> dict:
    values = [] if uids is None else uids
    return OBSERVER._state_seal({
        "helper_path": str(OBSERVER.BROKER_HELPER),
        "helper_file_sha256": digest("helper"),
        "policy_sha256": digest("deny-all-policy"),
        "authorized_connector_count": connectors,
        "authorized_uids": values, "protected_port_count": 4,
        "deny_all": connectors == 0 and not values,
        "checked_boottime_ns": checked,
    })


class FakeHost:
    def __init__(self, layout: OBSERVER.Layout):
        self.layout = layout
        self.sample = OBSERVER.ClockSample(WALL_MS, BOOTTIME_NS, BOOT_ID)
        gateway_invocation = "a" * 32
        reader_invocation = "b" * 32
        host_invocation = "c" * 32
        self.units: dict[str, dict] = {
            OBSERVER.GATEWAY_UNIT: unit(
                OBSERVER.GATEWAY_UNIT, active="active", sub="running",
                pid=2101, invocation=gateway_invocation, start=101),
            "hepta-shadow-watch-custodian@alpha.service": unit(
                "hepta-shadow-watch-custodian@alpha.service",
                active="active", sub="running", pid=2102,
                invocation="d" * 32, start=102),
            "hepta-shadow-watch-collector@alpha.timer": unit(
                "hepta-shadow-watch-collector@alpha.timer",
                active="active", sub="waiting"),
            "hepta-p1-watch-activation-reconcile.timer": unit(
                "hepta-p1-watch-activation-reconcile.timer",
                active="active", sub="waiting"),
            "hepta-p1-shadow-reader-round1.service": unit(
                "hepta-p1-shadow-reader-round1.service",
                active="active", sub="running", pid=2201,
                invocation=reader_invocation, start=103),
            "hepta-p1-shadow-host-round1.service": unit(
                "hepta-p1-shadow-host-round1.service",
                active="active", sub="running", pid=2202,
                invocation=host_invocation, start=104),
            OBSERVER.BROKER_UNIT: unit(
                OBSERVER.BROKER_UNIT, active="active", sub="running",
                pid=2301, invocation="e" * 32, start=105),
        }
        for name in OBSERVER.PAPER_UNITS:
            self.units[name] = unit(name)
        for name in OBSERVER.WATCH_UNITS:
            self.units.setdefault(name, unit(name))
        self.processes = {
            pid: process(pid) for pid in (2101, 2102, 2201, 2202, 2301)
        }
        self.paths: dict[str, dict] = {}
        self.documents: dict[str, OBSERVER.ObservedDocument] = {}
        self.broker_value = broker()
        self._add_service_documents(gateway_invocation)
        self.paths[str(layout.kill_switch)] = path_item(
            layout.kill_switch, present=True,
            file_sha=OBSERVER.digest_bytes(b"engaged"))
        self.paths[str(layout.campaign_socket)] = path_item(
            layout.campaign_socket)
        executable = path_item(
            OBSERVER.GATEWAY_EXECUTABLE, present=True,
            file_sha=digest("gateway-executable"))
        profile = path_item(
            OBSERVER.GATEWAY_PROFILE, present=True,
            file_sha=digest("gateway-profile"))
        domain = path_item(
            OBSERVER.GATEWAY_DOMAIN_CONFIG, present=True,
            file_sha=digest("gateway-domain"))
        tool_socket = path_item(
            OBSERVER.GATEWAY_TOOL_SOCKET, present=True, file_type="socket")
        supervisor_socket = path_item(
            OBSERVER.GATEWAY_SUPERVISOR_SOCKET, present=True,
            file_type="socket")
        for item in (
                executable, profile, domain, tool_socket,
                supervisor_socket):
            self.paths[item["path"]] = item
        self.processes[2101] = process(
            2101, exe_device=executable["device"],
            exe_inode=executable["inode"])
        activation = self.documents[str(layout.activation_receipt)].document
        body = {key: copy.deepcopy(value) for key, value in activation.items()
                if key != "body_sha256"}
        body["gateway_after"] = {
            "unit": OBSERVER.GATEWAY_UNIT,
            "active_state": "active", "sub_state": "running",
            "gateway_main_pid": 2101,
            "gateway_invocation_id": gateway_invocation,
            "gateway_exec_main_start_timestamp_monotonic_us": 101,
            "process_starttime_ticks": 210100,
            "gateway_executable_path": str(OBSERVER.GATEWAY_EXECUTABLE),
            "gateway_executable_sha256": digest("gateway-executable"),
            "domain_config_sha256": digest("gateway-domain"),
            "gateway_profile_path": str(OBSERVER.GATEWAY_PROFILE),
            "gateway_profile_sha256": digest("gateway-profile"),
            "gateway_process_profile_sha256": digest("process-profile"),
            "execution_remote_mode": "SIMULATOR", "tool_account": "SIM",
            "execution_domain_id": "SIM:alpha", "tool_allow_trade": "0",
            "session_templates": "watch",
            "contract_bindings": "EUR.USD|EUR|CASH|IDEALPRO|USD",
            "gateway_socket_path": str(OBSERVER.GATEWAY_TOOL_SOCKET),
            "gateway_socket_device": tool_socket["device"],
            "gateway_socket_inode": tool_socket["inode"],
            "supervisor_socket_path": str(
                OBSERVER.GATEWAY_SUPERVISOR_SOCKET),
            "supervisor_socket_device": supervisor_socket["device"],
            "supervisor_socket_inode": supervisor_socket["inode"],
            "unit_contract_sha256": digest("gateway-unit-contract"),
        }
        self._document(layout.activation_receipt, OBSERVER.seal(body))

    def _document(
        self, path: Path, document: dict, *, uid: int = 0, gid: int = 0,
        mode: int = 0o600, parent_uid: int = 0, parent_gid: int = 0,
        parent_mode: int = 0o700,
    ) -> None:
        payload = OBSERVER.canonical_bytes(document)
        identity = path_item(
            path, present=True, file_sha=OBSERVER.digest_bytes(payload),
            body_sha=document["body_sha256"], uid=uid, gid=gid, mode=mode,
            parent_uid=parent_uid, parent_gid=parent_gid,
            parent_mode=parent_mode, size=len(payload))
        self.documents[str(path)] = OBSERVER.ObservedDocument(
            path, document, OBSERVER.digest_bytes(payload),
            document["body_sha256"], identity)

    def publish_export(
        self, lease: dict, *, sequence: int | None = None,
        update_status: bool = True,
    ) -> OBSERVER.CommittedExport:
        if sequence is None:
            sequence = getattr(self, "export_sequence", 0) + 1
        self.export_sequence = sequence
        generation = (
            f"generation-{sequence:020d}-fixture{sequence:08d}")
        generation_root = (
            self.layout.export_generations / generation)
        snapshot = OBSERVER.seal({
            "schema": "hepta.shadow-watch-snapshot.v1", "version": 1,
            "domain_id": "alpha", "agent_uid": lease["agent_uid"],
            "generated_at_ms": WALL_MS - 2_000, "instrument": "EUR.USD",
            "catalog_sha256": digest("catalog"),
            "descriptor_sha256": digest("descriptor"), "reads": {},
            "paper_authorized": False, "live_authorized": False,
            "mutation_attempted": False, "direct_broker_access": False,
        })
        snapshot_payload = OBSERVER.canonical_bytes(snapshot)
        lease_payload = OBSERVER.canonical_bytes(lease)
        receipt = OBSERVER.seal({
            "schema": "hepta.shadow-watch-export-receipt.v1", "version": 1,
            "domain_id": "alpha", "agent_uid": lease["agent_uid"],
            "reader_uid": 1000, "reader_gid": 1000,
            "boundary": "WATCH_EXPORT",
            "lease_generation": lease["lease_generation"],
            "lease_receipt_body_sha256": lease["body_sha256"],
            "lease_receipt_file_sha256":
                OBSERVER.digest_bytes(lease_payload),
            "snapshot_body_sha256": snapshot["body_sha256"],
            "snapshot_file_sha256":
                OBSERVER.digest_bytes(snapshot_payload),
            "snapshot_generated_at_ms": snapshot["generated_at_ms"],
            "exported_at_ms": WALL_MS - 1_000 + sequence,
            "paper_authorized": False, "live_authorized": False,
            "mutation_attempted": False, "direct_broker_access": False,
        })
        receipt_payload = OBSERVER.canonical_bytes(receipt)
        committed_at_ms = WALL_MS - 900 + sequence
        commit = OBSERVER.seal({
            "schema": "hepta.shadow-watch-export-commit.v1", "version": 1,
            "authority_status": "ACTIVE",
            "authority_changed_at_ms": committed_at_ms,
            "close_reason": None, "commit_sequence": sequence,
            "generation": generation, "domain_id": "alpha",
            "agent_uid": lease["agent_uid"], "reader_uid": 1000,
            "reader_gid": 1000,
            "lease_generation": lease["lease_generation"],
            "snapshot_body_sha256": snapshot["body_sha256"],
            "snapshot_file_sha256":
                OBSERVER.digest_bytes(snapshot_payload),
            "lease_receipt_body_sha256": lease["body_sha256"],
            "lease_receipt_file_sha256":
                OBSERVER.digest_bytes(lease_payload),
            "export_receipt_body_sha256": receipt["body_sha256"],
            "export_receipt_file_sha256":
                OBSERVER.digest_bytes(receipt_payload),
            "committed_at_ms": committed_at_ms,
            "paper_authorized": False, "live_authorized": False,
            "mutation_attempted": False, "direct_broker_access": False,
        })
        paths_and_documents = (
            (self.layout.export_commit, commit),
            (generation_root / OBSERVER.EXPORT_FILES[0], snapshot),
            (generation_root / OBSERVER.EXPORT_FILES[1], lease),
            (generation_root / OBSERVER.EXPORT_FILES[2], receipt),
        )
        for path, document in paths_and_documents:
            self._document(
                path, document, uid=0, gid=1000, mode=0o440,
                parent_uid=0, parent_gid=1000, parent_mode=0o750)
        bundle = OBSERVER.validate_committed_export(
            *(self.documents[str(path)] for path, _ in paths_and_documents),
            export_root=self.layout.export_root)
        self.export_bundle = bundle
        if update_status:
            status_path = self.layout.controller_status(FORMAL_ID)
            observed = self.documents[str(status_path)]
            body = copy.deepcopy(observed.document)
            body.pop("body_sha256")
            body["last_export_receipt_body_sha256"] = \
                bundle.receipt.body_sha256
            body["last_snapshot_body_sha256"] = bundle.snapshot.body_sha256
            body["last_lease_generation"] = bundle.lease.document[
                "lease_generation"]
            self._document(status_path, OBSERVER.seal(body))
        return bundle

    def _add_service_documents(self, gateway_invocation: str) -> None:
        environment = {
            "boot_id": BOOT_ID, "audit_journal_device": 8,
            "audit_journal_inode": 9, "collector_sha256": digest("collector"),
            "exporter_sha256": digest("exporter"),
            "heptactl_sha256": digest("heptactl"),
            "gateway_sha256": digest("gateway"),
            "custodian_sha256": digest("custodian"),
            "observer_sha256": digest("observer"),
            "host_controller_sha256": digest("host"),
            "domain_config_sha256": digest("domain"),
            "gateway_profile_sha256": digest("profile"),
            "gateway_process_profile_sha256": digest("process-profile"),
            "gateway_invocation_id": gateway_invocation,
            "gateway_main_pid": 2101,
            "gateway_exec_main_start_timestamp_monotonic_us": 101,
            "gateway_socket_device": 8, "gateway_socket_inode": 90,
        }
        marker = OBSERVER.seal({
            "schema": "hepta.p1-shadow-admission-authority-marker.v1",
            "version": 1, "status": "ACTIVE", "campaign_id": FORMAL_ID,
            "policy_path": "/var/lib/hepta/policy.json",
            "policy_file_sha256": FORMAL_POLICY_FILE,
            "policy_body_sha256": FORMAL_POLICY_BODY,
            "admission_receipt_path": "/var/lib/hepta/admission.json",
            "admission_receipt_file_sha256": digest("admission-file"),
            "admission_receipt_body_sha256": digest("admission-body"),
            "admitted_at_ms": WALL_MS - 20_000,
            "marker_created_at_ms": WALL_MS - 20_000,
            "expires_at_ms": WALL_MS + 300_000,
            "execution_service_epoch": "epoch-1",
            "execution_service_fencing_generation": 7,
            "environment": environment, **OBSERVER._boundary(),
        })
        status = OBSERVER.seal({
            "schema": "hepta.p1-shadow-observer-controller-status.v1",
            "version": 1, "campaign_id": FORMAL_ID,
            "controller_pid": 2201, "controller_uid": 1000,
            "controller_gid": 1000, "state": "RUNNING",
            "started_at_ms": WALL_MS - 20_000,
            "updated_at_ms": WALL_MS - 1_000, "observer_invocations": 3,
            "last_export_receipt_body_sha256": digest("export"),
            "last_snapshot_body_sha256": digest("snapshot"),
            "last_lease_generation": 11,
            "locked_execution_service_epoch": "epoch-1",
            "locked_execution_service_fencing_generation": 7,
            "observer_status": "RUNNING", "observer_outcome": "RUNNING",
            "completed_iterations": 2, "reason": None,
            "paper_authorized": False, "live_authorized": False,
            "mutation_attempted": False, "direct_broker_access": False,
        })
        lease = OBSERVER.seal({
            "schema": "hepta.shadow-watch-lease-receipt.v1", "version": 1,
            "domain_id": "alpha", "agent_id": "alpha", "agent_uid": 2104,
            "boundary": "WATCH", "operation": "ROTATE",
            "lease_generation": 11, "previous_lease_generation": 10,
            "previous_receipt_body_sha256": digest("previous-lease"),
            "accepted": True, "reason_code": "OK",
            "accepted_at_ms": WALL_MS - 20_000, "ttl_seconds": 3600,
            "expires_at_ms": WALL_MS + 3_580_000,
            "paper_authorized": False, "live_authorized": False,
            "mutation_authorized": False,
        })
        self._document(self.layout.formal_marker(1), marker)
        self._document(self.layout.controller_status(FORMAL_ID), status)
        self.publish_export(lease)
        activation = {
            field: None for field in OBSERVER.ACTIVATION_RECEIPT_FIELDS
            if field != "body_sha256"
        }
        activation.update({
            "schema": "hepta.p1-watch-activation-receipt.v4",
            "version": 4, "status": "WATCH_GATEWAY_ACTIVATED",
            "round": 114, "domain": "alpha",
            "started_at_ms": WALL_MS - 100_000,
            "completed_at_ms": WALL_MS - 90_000, "boot_id": BOOT_ID,
            "gateway_activated": True, "gateway_profile_loaded": True,
            "gateway_contract_binding_loaded": True,
            "broker_loaded_source_attested": True,
            "broker_deny_all_continuity_attested": True,
            "kill_switch_engaged": True,
            "watch_authority_provisioned": False,
            "campaign_launched": False,
            "admission_prerequisite_satisfied": True,
            "paper_prerequisite_satisfied": False,
            "paper_authorized": False, "live_authorized": False,
            "mutation_attempted": False, "direct_broker_access": False,
            "shadow_install_evidence":
                shadow_install_evidence(OBSERVER, SOURCE_SHA),
            "predecessor_activation_success": predecessor_activation_success(),
            "predecessor_activation_failure": predecessor_activation_failure(),
        })
        self._document(
            self.layout.activation_receipt, OBSERVER.seal(activation))

    def clock(self) -> OBSERVER.ClockSample:
        return self.sample

    def unit(self, name: str) -> dict:
        return copy.deepcopy(self.units[name])

    def process(self, pid: int) -> dict:
        return copy.deepcopy(self.processes[pid])

    def path(self, path: Path, content: str | None = None) -> dict:
        del content
        return copy.deepcopy(self.paths.get(str(path), path_item(path)))

    def document(self, path: Path, *, expected_uid: int) \
            -> OBSERVER.ObservedDocument:
        del expected_uid
        return copy.deepcopy(self.documents[str(path)])

    def committed_export(self, export_root: Path) \
            -> OBSERVER.CommittedExport:
        if export_root != self.layout.export_root:
            raise OBSERVER.ObserverError("P1_OBSERVER_EXPORT_COMMIT_INVALID")
        return copy.deepcopy(self.export_bundle)

    def broker(self) -> dict:
        return copy.deepcopy(self.broker_value)


class ObserverFixture(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.root.chmod(0o700)
        self.layout = OBSERVER.Layout(
            state_base=self.root / "state",
            export_root=self.root / "export",
            sessions_root=self.root / "sessions",
            watch_private=self.root / "watch-private",
            custodian_transaction=self.root / "custodian.json",
            kill_switch=self.root / "kill-switch",
            campaign_socket=self.root / "campaign.sock",
            activation_receipt=self.root / "activation-receipt.json")
        self.host = FakeHost(self.layout)
        self.expected_uid = os.geteuid()
        self.expected_gid = os.getegid()
        self.observer = OBSERVER.IndependentObserver(
            self.host, layout=self.layout, expected_uid=self.expected_uid,
            expected_gid=self.expected_gid)
        self.plan = self._plan()
        self.spec = self._spec(self.plan["body_sha256"])
        self.spec_path = self.write("campaign-spec.json", self.spec)
        self.plan_path = self.write("fault-plan.json", self.plan)
        self.runtime = self._runtime()
        self.runtime_path = self.write("campaign-runtime.json", self.runtime)

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def write(self, name: str, document: dict) -> Path:
        path = self.root / name
        path.write_bytes(OBSERVER.canonical_bytes(document))
        path.chmod(0o600)
        return path

    @staticmethod
    def _plan() -> dict:
        return OBSERVER.seal({
            "schema": OBSERVER.FAULT_PLAN_SCHEMA, "version": 1,
            "campaign_id": CAMPAIGN_ID,
            "source_manifest_sha256": SOURCE_SHA,
            "policy_sha256": POLICY_SHA, "strategy_sha256": STRATEGY_SHA,
            "planned_faults": [{
                "fault_id": "fault-service-restart",
                "fault_type": "SERVICE_RESTART",
                "target_id": "watch-execution-gateway",
                "formal_campaign_id": FORMAL_ID,
                "inject_at_boottime_ns": BOOTTIME_NS - 10_000_000_000,
                "maximum_injection_lateness_ns": 5_000_000_000,
                "maximum_recovery_ns": 10_000_000_000,
            }], **OBSERVER._boundary(),
        })

    @staticmethod
    def _spec(fault_plan_sha: str) -> dict:
        return OBSERVER.seal({
            "schema": OBSERVER.SPEC_SCHEMA, "version": 1,
            "campaign_id": CAMPAIGN_ID, "domain_id": "alpha",
            "source_manifest_sha256": SOURCE_SHA,
            "policy_sha256": POLICY_SHA, "strategy_id": "strategy-v1",
            "strategy_version": "1", "strategy_sha256": STRATEGY_SHA,
            "formal_campaigns": [{
                "campaign_id": FORMAL_ID, "campaign_sha256": CAMPAIGN_SHA,
                "policy_body_sha256": FORMAL_POLICY_BODY,
                "policy_file_sha256": FORMAL_POLICY_FILE,
            }],
            "declared_trading_days": list(TRADING_DAYS),
            "trading_timezone": OBSERVER.TRADING_TIMEZONE,
            "trading_calendar_sha256": digest("calendar"),
            "eligible_scheduled_at_ms": list(ELIGIBLE_SCHEDULE),
            "scheduled_decision_count": OBSERVER.MINIMUM_ELIGIBLE_DECISIONS,
            "minimum_eligible_decisions": OBSERVER.MINIMUM_ELIGIBLE_DECISIONS,
            "minimum_complete_ppm": OBSERVER.MINIMUM_COMPLETE_PPM,
            "minimum_boottime_duration_ns":
                OBSERVER.MINIMUM_BOOTTIME_DURATION_NS,
            "maximum_checkpoint_gap_ns": OBSERVER.MAXIMUM_CHECKPOINT_GAP_NS,
            "maximum_decision_lateness_ms":
                OBSERVER.MAXIMUM_DECISION_LATENESS_MS,
            "fault_plan_body_sha256": fault_plan_sha,
            "independent_auditor_id": "independent-auditor-v1",
            "frozen_at_ms": WALL_MS - 100_000,
            "freeze_bundle": {
                "path": "/evidence/freeze-bundle-receipt.json",
                "file_sha256": digest("freeze-bundle-file"),
                "body_sha256": digest("freeze-bundle-body"),
            },
            **OBSERVER._boundary(),
        })

    def _runtime(self) -> dict:
        dispatch = WALL_MS
        launcher_start = dispatch + OBSERVER.LAUNCHER_EARLY_START_LEAD_MS
        valid_after = launcher_start + OBSERVER.LAUNCHER_WARMUP_MS
        interval = OBSERVER.POLICY_SLOT_INTERVAL_MS
        formal_expiry = (
            valid_after + OBSERVER.POLICY_MAXIMUM_ITERATIONS * interval)
        completion = formal_expiry + 15 * 60 * 1000
        projection = formal_expiry + 20 * 60 * 1000
        teardown = formal_expiry + 30 * 60 * 1000
        return OBSERVER.seal({
            "schema": OBSERVER.CAMPAIGN_RUNTIME_SCHEMA, "version": 1,
            "status": "FROZEN", "campaign_id": CAMPAIGN_ID, "round": 114,
            "boot_id": BOOT_ID, "issued_at_ms": WALL_MS - 1_000,
            "expires_at_ms": teardown + 60 * 60 * 1000,
            "freeze_bundle": copy.deepcopy(self.spec["freeze_bundle"]),
            "campaign_spec": {
                "path": str(self.spec_path),
                "file_sha256": OBSERVER.digest_bytes(
                    OBSERVER.canonical_bytes(self.spec)),
                "body_sha256": self.spec["body_sha256"],
            },
            "fault_plan": {
                "path": str(self.plan_path),
                "file_sha256": OBSERVER.digest_bytes(
                    OBSERVER.canonical_bytes(self.plan)),
                "body_sha256": self.plan["body_sha256"],
            },
            "pin_formal_campaign_id": FORMAL_ID,
            "formal_campaigns": [{
                "formal_campaign_id": FORMAL_ID,
                "probe_campaign_id": "p1-probe-round1",
                "launcher_start_ms": launcher_start,
                "launcher_dispatch_at_ms": dispatch,
                "valid_after_ms": valid_after,
                "slot_interval_ms": interval,
                "maximum_iterations": OBSERVER.POLICY_MAXIMUM_ITERATIONS,
                "expires_at_ms": formal_expiry,
                "launcher_completion_deadline_ms": completion,
                "projection_deadline_ms": projection,
                "teardown_deadline_ms": teardown,
                "policy": {
                    "path": "/evidence/formal-policy.json",
                    "file_sha256": FORMAL_POLICY_FILE,
                    "body_sha256": FORMAL_POLICY_BODY,
                },
                "launcher_receipt_path": "/evidence/launcher.json",
                "verified_closure_path": "/evidence/closure.json",
                "artifact_root": "/evidence/formal",
            }],
            "observer_cadence_ms": 1_000,
            "maximum_slot_lateness_ms": 100,
            "state_root": str(self.root / "runtime-state"),
            "raw_observation_directory": str(self.root / "raw"),
            "recorder_root": str(self.root / "recorder"),
            "injector_journal_directory": str(self.root / "journal"),
            "injector_output_directory": str(self.root / "injector"),
            "control_directory": str(self.root / "control"),
            "executables": {
                "independent_observer": {
                    "path": str(OBSERVER.INSTALLED_EXECUTABLE),
                    "file_sha256": digest("observer-executable"),
                },
            },
            **OBSERVER._boundary(),
        })

    def fault_identity(
        self, phase: str, *, epoch: str, unit_value: dict,
        observed: int,
    ) -> dict:
        service_paths = [
            copy.deepcopy(self.host.documents[str(path)].path_identity)
            for path in (
                self.layout.formal_marker(1),
                self.layout.controller_status(FORMAL_ID),
                self.host.export_bundle.lease.path,
            )
        ]
        return OBSERVER.seal({
            "schema": OBSERVER.FAULT_IDENTITY_SCHEMA, "version": 1,
            "phase": phase, "target_id": "watch-execution-gateway",
            "boot_id": BOOT_ID, "observed_boottime_ns": observed,
            "service_epoch": epoch, "fencing_generation": 7,
            "lease_generation": 11, "systemd_units": [unit_value],
            "processes": [],
            "paths": sorted(service_paths, key=lambda item: item["path"]),
            "broker_deny_all": broker(checked=observed - 1_000_000_000),
            "residue_count": 0, "wall_clock_delta_ms": None,
            "fixture_generation": None,
            "fixture_expires_boottime_ns": None, "fixture_valid": None,
        })

    def retire_watch(self) -> None:
        for name in OBSERVER.WATCH_UNITS:
            self.host.units[name] = unit(name)

    def injection(self, **changes: object) -> dict:
        pre_unit = unit(
            OBSERVER.GATEWAY_UNIT, active="active", sub="running", pid=2001,
            invocation="8" * 32, start=80)
        post_unit = unit(
            OBSERVER.GATEWAY_UNIT, active="active", sub="running", pid=2101,
            invocation="a" * 32, start=101)
        pre = self.fault_identity(
            "PRE", epoch="epoch-0", unit_value=pre_unit,
            observed=BOOTTIME_NS - 11_000_000_000)
        post = self.fault_identity(
            "POST", epoch="epoch-1", unit_value=post_unit,
            observed=BOOTTIME_NS - 1_000_000_000)
        body = {
            "schema": OBSERVER.INJECTION_SCHEMA, "version": 1,
            "status": "COMPLETE", "issued_at_ms": WALL_MS - 1_000,
            "expires_at_ms": WALL_MS + 60_000, "campaign_id": CAMPAIGN_ID,
            "source_manifest_sha256": SOURCE_SHA,
            "policy_sha256": POLICY_SHA, "strategy_sha256": STRATEGY_SHA,
            "fault_id": "fault-service-restart",
            "fault_type": "SERVICE_RESTART",
            "target_id": "watch-execution-gateway",
            "clock_id": "CLOCK_BOOTTIME", "boot_id": BOOT_ID,
            "planned_injection_boottime_ns": BOOTTIME_NS - 10_000_000_000,
            "actual_injection_boottime_ns": BOOTTIME_NS - 9_000_000_000,
            "recovered_boottime_ns": BOOTTIME_NS - 1_000_000_000,
            "maximum_recovery_ns": 10_000_000_000,
            "injector_id": "independent-root-fault-operator",
            "injector_uid": 0, "injector_gid": 0,
            "injection_scope": "P1_DECLARED_FAULT_ONLY",
            "action_receipt_sha256": ACTION_SHA, "pre_identity": pre,
            "post_identity": post, "injection_performed": True,
            "recovery_complete": True, "cleanup_complete": True,
            "authority_failure": False, "audit_failure": False,
            "cleanup_failure": False,
            "producer": {
                "path": (
                    "/usr/libexec/"
                    "hepta-p1-safety-soak-root-fault-injector"),
                "file_sha256": "sha256:" + "8" * 64,
            },
            "production_mode": "PRODUCTION_ROOT_FAULT_INJECTION",
            "pins_reference": {
                "path": "/evidence/fault-injector-pins.json",
                "file_sha256": "sha256:" + "9" * 64,
                "body_sha256": "sha256:" + "a" * 64,
            },
            "journal_predecessor_sequence": 1,
            "journal_predecessor_body_sha256": "sha256:" + "b" * 64,
            **OBSERVER._boundary(),
        }
        body.update(changes)
        return OBSERVER.seal(body)

    def auditor_spec(self) -> object:
        artifact = AUDITOR.Artifact.from_document(
            "campaign_spec", str(self.spec_path), self.spec)
        return AUDITOR.Spec(
            artifact=artifact, campaign_id=CAMPAIGN_ID, domain_id="alpha",
            source_manifest_sha256=SOURCE_SHA, policy_sha256=POLICY_SHA,
            strategy_id="strategy-v1", strategy_version="1",
            strategy_sha256=STRATEGY_SHA,
            formal_campaigns=tuple(copy.deepcopy(
                self.spec["formal_campaigns"])),
            declared_trading_days=tuple(
                self.spec["declared_trading_days"]),
            trading_timezone=TRADING_ZONE,
            trading_calendar_sha256=self.spec["trading_calendar_sha256"],
            eligible_scheduled_at_ms=tuple(
                self.spec["eligible_scheduled_at_ms"]),
            scheduled_decision_count=self.spec["scheduled_decision_count"],
            minimum_eligible_decisions=self.spec["minimum_eligible_decisions"],
            minimum_complete_ppm=self.spec["minimum_complete_ppm"],
            minimum_boottime_duration_ns=
                self.spec["minimum_boottime_duration_ns"],
            maximum_checkpoint_gap_ns=
                self.spec["maximum_checkpoint_gap_ns"],
            maximum_decision_lateness_ms=
                self.spec["maximum_decision_lateness_ms"],
            fault_plan_body_sha256=self.spec["fault_plan_body_sha256"],
            independent_auditor_id=self.spec["independent_auditor_id"])

    @staticmethod
    def reseal_identity(identity: dict, **changes: object) -> dict:
        body = {key: copy.deepcopy(value) for key, value in identity.items()
                if key != "body_sha256"}
        body.update(changes)
        return OBSERVER.seal(body)

    def validate_transition(
        self, fault_type: str, pre: dict, post: dict, *,
        recovery_complete: bool = True,
        actual_ns: int = BOOTTIME_NS - 9_000_000_000,
        recovered_ns: int = BOOTTIME_NS - 1_000_000_000,
    ) -> None:
        self.observer._validate_fault_transition(
            fault_type, pre, post, actual_ns=actual_ns,
            recovered_ns=recovered_ns,
            recovery_complete=recovery_complete, reason="INVALID")


class IndependentObserverTests(ObserverFixture):
    def test_activation_predecessor_lineage_is_exact(self) -> None:
        success = predecessor_activation_success()
        failure = predecessor_activation_failure()
        OBSERVER._validate_activation_predecessor_lineage(
            success, failure, "TEST_PREDECESSOR_INVALID")
        mutations = (
            ("success-file", success, "receipt_file_sha256", digest("bad")),
            ("success-schema", success, "receipt_schema", "tampered.v3"),
            ("failure-journal", failure, "journal_sha256", digest("bad")),
            ("round86-ancestor-binding", failure, "receipt_body_sha256",
             digest("bad")),
        )
        for label, original, field, value in mutations:
            with self.subTest(label=label):
                changed = copy.deepcopy(original)
                changed[field] = value
                with self.assertRaises(OBSERVER.ObserverError):
                    OBSERVER._validate_activation_predecessor_lineage(
                        changed if original is success else success,
                        changed if original is failure else failure,
                        "TEST_PREDECESSOR_INVALID")

    def test_campaign_spec_rejects_short_p1_thresholds(self) -> None:
        mutations = (
            ("declared_trading_days", TRADING_DAYS[:9]),
            ("declared_trading_days", TRADING_DAYS + EXTRA_TRADING_DAYS),
            ("eligible_scheduled_at_ms", ELIGIBLE_SCHEDULE[:199]),
            ("scheduled_decision_count", 199),
            ("minimum_eligible_decisions", 199),
            ("minimum_complete_ppm", OBSERVER.MINIMUM_COMPLETE_PPM - 1),
            ("minimum_boottime_duration_ns",
             OBSERVER.MINIMUM_BOOTTIME_DURATION_NS - 1),
            ("maximum_checkpoint_gap_ns",
             OBSERVER.MAXIMUM_CHECKPOINT_GAP_NS + 1),
            ("maximum_decision_lateness_ms",
             OBSERVER.MAXIMUM_DECISION_LATENESS_MS + 1),
        )
        OBSERVER.validate_spec(self.spec)
        for field, value in mutations:
            with self.subTest(field=field, value=value):
                body = copy.deepcopy(self.spec)
                body.pop("body_sha256")
                body[field] = value
                with self.assertRaisesRegex(
                        OBSERVER.ObserverError,
                        "P1_OBSERVER_CAMPAIGN_SPEC_INVALID"):
                    OBSERVER.validate_spec(OBSERVER.seal(body))

    def test_campaign_spec_accepts_p1_day_boundaries(self) -> None:
        for days in (TRADING_DAYS, TRADING_DAYS + EXTRA_TRADING_DAYS[:10]):
            body = copy.deepcopy(self.spec)
            body.pop("body_sha256")
            body["declared_trading_days"] = days
            body["eligible_scheduled_at_ms"] = eligible_schedule(days)
            body["scheduled_decision_count"] = len(
                body["eligible_scheduled_at_ms"])
            OBSERVER.validate_spec(OBSERVER.seal(body))

    def test_directory_anchor_ignores_unrelated_child_link_count_churn(self):
        parent = self.root / "anchor-parent"
        parent.mkdir()
        before = parent.stat()
        (parent / "unrelated-child").mkdir()
        after = parent.stat()
        self.assertEqual(
            OBSERVER._directory_identity(before),
            OBSERVER._directory_identity(after))

    def test_service_observation_binds_units_processes_and_paths(self) -> None:
        receipt = self.observer.service(self.spec_path, FORMAL_ID)
        self.assertEqual(receipt["schema"], OBSERVER.SERVICE_SCHEMA)
        self.assertTrue(receipt["continuity_ok"])
        self.assertTrue(receipt["audit_ok"])
        self.assertTrue(receipt["cleanup_ok"])
        evidence = receipt["observation_evidence"]
        self.assertGreaterEqual(len(evidence["systemd_units"]), 5)
        self.assertGreaterEqual(len(evidence["processes"]), 4)
        self.assertGreaterEqual(len(evidence["paths"]), 4)
        OBSERVER.validate_evidence(evidence, "SERVICE", "INVALID")

    def test_committed_export_rejects_pointer_to_wrong_snapshot(self) -> None:
        bundle = self.host.export_bundle
        body = copy.deepcopy(bundle.commit.document)
        body.pop("body_sha256")
        body["snapshot_file_sha256"] = digest("wrong-snapshot-file")
        changed = OBSERVER.seal(body)
        self.host._document(
            bundle.commit.path, changed, uid=0, gid=1000, mode=0o440,
            parent_uid=0, parent_gid=1000, parent_mode=0o750)
        with self.assertRaisesRegex(
                OBSERVER.ObserverError,
                "P1_OBSERVER_EXPORT_BINDING_INVALID"):
            OBSERVER.validate_committed_export(
                self.host.documents[str(bundle.commit.path)], bundle.snapshot,
                bundle.lease, bundle.receipt,
                export_root=self.layout.export_root)

    def test_campaign_continuity_needs_no_formal_marker_or_reader(self) -> None:
        self.host.documents.pop(str(self.layout.formal_marker(1)))
        self.host.documents.pop(str(
            self.layout.controller_status(FORMAL_ID)))
        self.host.units["hepta-p1-shadow-reader-round1.service"] = unit(
            "hepta-p1-shadow-reader-round1.service")
        receipt = self.observer.campaign_continuity(
            self.spec_path, self.runtime_path, 0)
        self.assertEqual(
            receipt["schema"], OBSERVER.CAMPAIGN_CONTINUITY_SCHEMA)
        self.assertTrue(receipt["persistent_stack_ok"])
        self.assertTrue(receipt["lease_chain_ok"])
        self.assertTrue(receipt["zero_exposure"])
        self.assertEqual(receipt["freeze_bundle"], self.spec["freeze_bundle"])
        self.assertFalse(OBSERVER.observation_is_unsafe(receipt))

    def test_campaign_continuity_preserves_stack_or_exposure_failure(self):
        self.host.units[OBSERVER.GATEWAY_UNIT] = unit(OBSERVER.GATEWAY_UNIT)
        with self.assertRaisesRegex(
                OBSERVER.ObserverError, "CONTINUITY_BINDING_INVALID"):
            self.observer.campaign_continuity(
                self.spec_path, self.runtime_path, 0)

        self.host.units[OBSERVER.GATEWAY_UNIT] = unit(
            OBSERVER.GATEWAY_UNIT, active="active", sub="running",
            pid=2101, invocation="a" * 32, start=101)
        self.host.broker_value = broker(connectors=1, uids=[2301])
        receipt = self.observer.campaign_continuity(
            self.spec_path, self.runtime_path, 0)
        self.assertFalse(receipt["zero_exposure"])
        self.assertTrue(OBSERVER.observation_is_unsafe(receipt))

    def test_campaign_continuity_activation_tamper_fails_closed(self) -> None:
        observed = self.host.documents[str(self.layout.activation_receipt)]
        body = copy.deepcopy(observed.document)
        body.pop("body_sha256")
        body["gateway_activated"] = False
        self.host._document(
            self.layout.activation_receipt, OBSERVER.seal(body))
        with self.assertRaisesRegex(
                OBSERVER.ObserverError, "CONTINUITY_BINDING_INVALID"):
            self.observer.campaign_continuity(
                self.spec_path, self.runtime_path, 0)

    def test_campaign_continuity_activation_source_lineage_mismatch_fails_closed(
            self) -> None:
        observed = self.host.documents[str(self.layout.activation_receipt)]
        body = copy.deepcopy(observed.document)
        body.pop("body_sha256")
        body["shadow_install_evidence"] = copy.deepcopy(
            body["shadow_install_evidence"])
        body["shadow_install_evidence"]["source_baseline_sha256"] = \
            digest("wrong-source-manifest")
        self.host._document(
            self.layout.activation_receipt, OBSERVER.seal(body))
        with self.assertRaisesRegex(
                OBSERVER.ObserverError, "CONTINUITY_BINDING_INVALID"):
            self.observer.campaign_continuity(
                self.spec_path, self.runtime_path, 0)

    def test_initial_gateway_process_and_socket_must_match_activation(self):
        observed = self.host.documents[str(self.layout.activation_receipt)]
        for field, replacement in (
                ("gateway_main_pid", 9999),
                ("gateway_socket_inode", 9999)):
            with self.subTest(field=field):
                body = copy.deepcopy(observed.document)
                body.pop("body_sha256")
                body["gateway_after"][field] = replacement
                self.host._document(
                    self.layout.activation_receipt, OBSERVER.seal(body))
                with self.assertRaisesRegex(
                        OBSERVER.ObserverError,
                        "CONTINUITY_BINDING_INVALID"):
                    self.observer.campaign_continuity(
                        self.spec_path, self.runtime_path, 0)
                self.host._document(
                    self.layout.activation_receipt, observed.document)

    def test_continuity_grid_rejects_early_and_late_dispatch(self) -> None:
        for wall_ms in (WALL_MS - 1, WALL_MS + 101):
            with self.subTest(wall_ms=wall_ms):
                self.host.sample = OBSERVER.ClockSample(
                    wall_ms, BOOTTIME_NS, BOOT_ID)
                with self.assertRaisesRegex(
                        OBSERVER.ObserverError,
                        "CONTINUITY_BINDING_INVALID"):
                    self.observer.campaign_continuity(
                        self.spec_path, self.runtime_path, 0)

    def test_non_aligned_final_slot_is_exact_teardown_anchor(self) -> None:
        body = copy.deepcopy(self.runtime)
        body.pop("body_sha256")
        body["formal_campaigns"][0]["teardown_deadline_ms"] += 500
        runtime = OBSERVER.seal(body)
        runtime_path = self.write("campaign-runtime-nonaligned.json", runtime)
        origin = runtime["formal_campaigns"][0]["launcher_dispatch_at_ms"]
        end = runtime["formal_campaigns"][0]["teardown_deadline_ms"]
        cadence = runtime["observer_cadence_ms"]
        final_slot = (end - origin + cadence - 1) // cadence
        lease = copy.deepcopy(
            self.host.export_bundle.lease.document)
        lease.pop("body_sha256")
        lease["expires_at_ms"] = end + 60_000
        lease["accepted_at_ms"] = (
            lease["expires_at_ms"] - lease["ttl_seconds"] * 1000)
        self.host.publish_export(OBSERVER.seal(lease))
        self.host.sample = OBSERVER.ClockSample(
            end, BOOTTIME_NS + (end - WALL_MS) * 1_000_000, BOOT_ID)
        self.host.broker_value = broker(
            checked=self.host.sample.boottime_ns)
        receipt = self.observer.campaign_continuity(
            self.spec_path, runtime_path, final_slot)
        self.assertEqual(receipt["continuity_scheduled_at_ms"], end)
        self.assertEqual(receipt["continuity_final_slot"], final_slot)
        self.assertTrue(receipt["continuity_is_final"])

    def test_observed_service_failure_is_published_as_unsafe_fact(self) -> None:
        self.host.units[OBSERVER.GATEWAY_UNIT] = unit(OBSERVER.GATEWAY_UNIT)
        self.host.processes.pop(2101)
        receipt = self.observer.service(self.spec_path, FORMAL_ID)
        self.assertFalse(receipt["continuity_ok"])
        self.assertTrue(OBSERVER.observation_is_unsafe(receipt))

    def test_service_fence_drift_is_rejected(self) -> None:
        status = self.host.documents[
            str(self.layout.controller_status(FORMAL_ID))].document
        status["locked_execution_service_fencing_generation"] = 8
        status = OBSERVER.seal({key: value for key, value in status.items()
                                if key != "body_sha256"})
        self.host._document(self.layout.controller_status(FORMAL_ID), status)
        with self.assertRaisesRegex(
                OBSERVER.ObserverError, "SERVICE_BINDING_INVALID"):
            self.observer.service(self.spec_path, FORMAL_ID)

    def test_authority_observation_is_safe_without_querying_orders(self) -> None:
        receipt = self.observer.authority(self.spec_path)
        self.assertEqual(receipt["connector_count"], 0)
        self.assertTrue(receipt["kill_switch_engaged"])
        self.assertTrue(receipt["local_boundary_safe"])
        self.assertFalse(receipt["local_boundary_uncertain"])
        self.assertEqual(
            receipt["observation_scope"], "LOCAL_HOST_BOUNDARY_ONLY")
        self.assertFalse(receipt["authoritative_account_state_observed"])
        self.assertFalse(OBSERVER.observation_is_unsafe(receipt))

    def test_dangerous_connector_exposure_is_preserved_and_unsafe(self) -> None:
        self.host.broker_value = broker(connectors=1, uids=[2301])
        receipt = self.observer.authority(self.spec_path)
        self.assertEqual(receipt["connector_count"], 1)
        self.assertEqual(receipt["authorized_uids"], [2301])
        self.assertTrue(OBSERVER.observation_is_unsafe(receipt))

    def test_active_paper_unit_is_not_hidden(self) -> None:
        name = OBSERVER.PAPER_UNITS[0]
        self.host.units[name] = unit(
            name, active="active", sub="running", pid=2401,
            invocation="f" * 32, start=106)
        self.host.processes[2401] = process(2401)
        receipt = self.observer.authority(self.spec_path)
        self.assertEqual(receipt["connector_count"], 0)
        self.assertEqual(receipt["paper_unit_active_count"], 1)
        self.assertFalse(receipt["local_boundary_safe"])
        self.assertTrue(OBSERVER.observation_is_unsafe(receipt))

    def test_final_cleanup_zero_residue(self) -> None:
        self.retire_watch()
        receipt = self.observer.cleanup(
            self.spec_path, subject_type="FINAL", subject_id=CAMPAIGN_ID)
        self.assertTrue(receipt["cleanup_complete"])
        self.assertFalse(OBSERVER.observation_is_unsafe(receipt))

    def test_cleanup_residue_is_preserved_and_unsafe(self) -> None:
        self.retire_watch()
        self.host.paths[str(self.layout.token)] = path_item(
            self.layout.token, present=True)
        receipt = self.observer.cleanup(
            self.spec_path, subject_type="FINAL", subject_id=CAMPAIGN_ID)
        self.assertEqual(receipt["session_authority_count"], 1)
        self.assertIn("SESSION_AUTHORITY_RESIDUE", receipt["errors"])
        self.assertTrue(OBSERVER.observation_is_unsafe(receipt))

    def test_fault_receipt_is_reopened_and_post_identity_reobserved(self) \
            -> None:
        injection_path = self.write("injection.json", self.injection())
        receipt = self.observer.fault(
            self.spec_path, self.plan_path, injection_path)
        self.assertTrue(receipt["recovery_verified"])
        self.assertTrue(receipt["cleanup_verified"])
        reference = receipt["observation_evidence"][
            "fault_injection_receipt"]
        self.assertEqual(reference["path"], str(injection_path))
        self.assertEqual(reference["schema"], OBSERVER.INJECTION_SCHEMA)
        self.assertFalse(OBSERVER.observation_is_unsafe(receipt))

    def test_expired_fault_receipt_is_rejected(self) -> None:
        injection = self.injection(expires_at_ms=WALL_MS)
        path = self.write("expired.json", injection)
        with self.assertRaisesRegex(OBSERVER.ObserverError, "RECEIPT_INVALID"):
            self.observer.fault(self.spec_path, self.plan_path, path)

    def test_tampered_fault_receipt_is_rejected(self) -> None:
        injection = self.injection()
        injection["cleanup_complete"] = False
        path = self.write("tampered.json", injection)
        with self.assertRaises(OBSERVER.ObserverError):
            self.observer.fault(self.spec_path, self.plan_path, path)

    def test_fault_boot_drift_is_rejected(self) -> None:
        path = self.write(
            "boot-drift.json",
            self.injection(boot_id="aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"))
        with self.assertRaisesRegex(OBSERVER.ObserverError, "RECEIPT_INVALID"):
            self.observer.fault(self.spec_path, self.plan_path, path)

    def test_fault_fence_drift_is_rejected(self) -> None:
        injection = self.injection()
        post = copy.deepcopy(injection["post_identity"])
        post["fencing_generation"] = 8
        injection = self.injection(pre_identity=injection["pre_identity"],
                                   post_identity=OBSERVER.seal({
                                       key: value for key, value in post.items()
                                       if key != "body_sha256"}))
        path = self.write("fence-drift.json", injection)
        with self.assertRaisesRegex(OBSERVER.ObserverError, "RECEIPT_INVALID"):
            self.observer.fault(self.spec_path, self.plan_path, path)

    def test_fault_lease_generation_drift_is_rejected(self) -> None:
        injection = self.injection()
        post = copy.deepcopy(injection["post_identity"])
        post["lease_generation"] = 12
        post = OBSERVER.seal({key: value for key, value in post.items()
                              if key != "body_sha256"})
        path = self.write(
            "generation-drift.json", self.injection(post_identity=post))
        with self.assertRaisesRegex(
                OBSERVER.ObserverError,
                "RECEIPT_INVALID|POST_IDENTITY_DRIFT"):
            self.observer.fault(self.spec_path, self.plan_path, path)

    def test_late_fault_injection_is_rejected(self) -> None:
        path = self.write("late.json", self.injection(
            actual_injection_boottime_ns=BOOTTIME_NS - 1_000_000_000))
        with self.assertRaisesRegex(OBSERVER.ObserverError, "RECEIPT_INVALID"):
            self.observer.fault(self.spec_path, self.plan_path, path)

    def test_self_authorizing_fault_receipt_is_rejected(self) -> None:
        injection = self.injection()
        body = {key: value for key, value in injection.items()
                if key != "body_sha256"}
        body["mutation_authorized"] = True
        path = self.write("self-authority.json", OBSERVER.seal(body))
        with self.assertRaises(OBSERVER.ObserverError):
            self.observer.fault(self.spec_path, self.plan_path, path)

    def test_fault_post_identity_drift_is_rejected(self) -> None:
        injection = self.injection()
        self.host.units[OBSERVER.GATEWAY_UNIT] = unit(
            OBSERVER.GATEWAY_UNIT, active="active", sub="running", pid=2101,
            invocation="9" * 32, start=101)
        path = self.write("post-drift.json", injection)
        with self.assertRaisesRegex(OBSERVER.ObserverError, "POST_IDENTITY_DRIFT"):
            self.observer.fault(self.spec_path, self.plan_path, path)

    def test_process_kill_and_writer_crash_require_process_transition(self) \
            -> None:
        for fault_type in ("PROCESS_KILL", "EVIDENCE_WRITER_CRASH"):
            with self.subTest(fault_type=fault_type):
                pre = self.fault_identity(
                    "PRE", epoch="epoch-1",
                    unit_value=self.host.units[OBSERVER.GATEWAY_UNIT],
                    observed=BOOTTIME_NS - 10_000_000_000)
                post = self.fault_identity(
                    "POST", epoch="epoch-1",
                    unit_value=self.host.units[OBSERVER.GATEWAY_UNIT],
                    observed=BOOTTIME_NS - 1_000_000_000)
                pre = self.reseal_identity(pre, processes=[
                    OBSERVER._state_seal({
                        **{key: value for key, value in process(2001).items()
                           if key != "state_sha256"},
                        "exe_device": 8, "exe_inode": 9999,
                    })])
                post = self.reseal_identity(post, processes=[
                    OBSERVER._state_seal({
                        **{key: value for key, value in process(2002).items()
                           if key != "state_sha256"},
                        "exe_device": 8, "exe_inode": 9999,
                    })])
                self.validate_transition(fault_type, pre, post)
                with self.assertRaises(OBSERVER.ObserverError):
                    self.validate_transition(fault_type, pre, pre)

    def test_token_loss_requires_fixture_validity_and_content_transition(self) \
            -> None:
        pre = self.fault_identity(
            "PRE", epoch="epoch-1",
            unit_value=self.host.units[OBSERVER.GATEWAY_UNIT],
            observed=BOOTTIME_NS - 10_000_000_000)
        post = self.fault_identity(
            "POST", epoch="epoch-1",
            unit_value=self.host.units[OBSERVER.GATEWAY_UNIT],
            observed=BOOTTIME_NS - 1_000_000_000)
        pre_paths = [*pre["paths"], path_item(
            OBSERVER.TOKEN_FAULT_FIXTURE, present=True,
            file_sha=digest("token-pre"), body_sha=digest("token-body-pre"))]
        post_paths = [*post["paths"], path_item(
            OBSERVER.TOKEN_FAULT_FIXTURE, present=True,
            file_sha=digest("token-post"), body_sha=digest("token-body-post"))]
        pre = self.reseal_identity(
            pre, paths=sorted(pre_paths, key=lambda item: item["path"]),
            fixture_generation=5,
            fixture_expires_boottime_ns=BOOTTIME_NS + 1_000_000_000,
            fixture_valid=True)
        post = self.reseal_identity(
            post, lease_generation=12,
            paths=sorted(post_paths, key=lambda item: item["path"]),
            fixture_generation=6,
            fixture_expires_boottime_ns=BOOTTIME_NS + 1_000_000_000,
            fixture_valid=True)
        self.validate_transition("TOKEN_LOSS", pre, post)
        bad = self.reseal_identity(post, fixture_valid=False)
        with self.assertRaises(OBSERVER.ObserverError):
            self.validate_transition("TOKEN_LOSS", pre, bad)

    def test_lease_expiry_requires_expiry_crossing_and_stable_generation(self) \
            -> None:
        expiry = BOOTTIME_NS - 9_500_000_000
        pre = self.fault_identity(
            "PRE", epoch="epoch-1",
            unit_value=self.host.units[OBSERVER.GATEWAY_UNIT],
            observed=BOOTTIME_NS - 10_000_000_000)
        post = self.fault_identity(
            "POST", epoch="epoch-1",
            unit_value=self.host.units[OBSERVER.GATEWAY_UNIT],
            observed=BOOTTIME_NS - 1_000_000_000)
        pre_paths = [*pre["paths"], path_item(
            OBSERVER.LEASE_FAULT_FIXTURE, present=True,
            file_sha=digest("lease-pre"), body_sha=digest("lease-body-pre"))]
        post_paths = [*post["paths"], path_item(
            OBSERVER.LEASE_FAULT_FIXTURE, present=True,
            file_sha=digest("lease-post"), body_sha=digest("lease-body-post"))]
        pre = self.reseal_identity(
            pre, paths=sorted(pre_paths, key=lambda item: item["path"]),
            fixture_generation=5, fixture_expires_boottime_ns=expiry,
            fixture_valid=True)
        post = self.reseal_identity(
            post, paths=sorted(post_paths, key=lambda item: item["path"]),
            fixture_generation=6,
            fixture_expires_boottime_ns=BOOTTIME_NS + 1_000_000_000,
            fixture_valid=True)
        self.validate_transition("LEASE_EXPIRY", pre, post)
        bad = self.reseal_identity(post, fixture_generation=5)
        with self.assertRaises(OBSERVER.ObserverError):
            self.validate_transition("LEASE_EXPIRY", pre, bad)

    def test_network_reload_requires_unit_transition_and_deny_all_continuity(
            self) -> None:
        before_unit = unit(
            OBSERVER.BROKER_UNIT, active="active", sub="running", pid=2300,
            invocation="1" * 32, start=100)
        after_unit = unit(
            OBSERVER.BROKER_UNIT, active="active", sub="running", pid=2301,
            invocation="2" * 32, start=200)
        pre = self.fault_identity(
            "PRE", epoch="epoch-1", unit_value=before_unit,
            observed=BOOTTIME_NS - 10_000_000_000)
        post = self.fault_identity(
            "POST", epoch="epoch-1", unit_value=after_unit,
            observed=BOOTTIME_NS - 1_000_000_000)
        pre = self.reseal_identity(
            pre, systemd_units=[before_unit],
            broker_deny_all=broker(checked=BOOTTIME_NS - 11_000_000_000))
        post = self.reseal_identity(
            post, systemd_units=[after_unit],
            broker_deny_all=broker(checked=BOOTTIME_NS - 2_000_000_000))
        self.validate_transition("NETWORK_DENY_RELOAD", pre, post)
        bad_broker = broker(connectors=1, checked=BOOTTIME_NS)
        bad = self.reseal_identity(post, broker_deny_all=bad_broker)
        with self.assertRaises(OBSERVER.ObserverError):
            self.validate_transition("NETWORK_DENY_RELOAD", pre, bad)

    def test_clock_step_requires_wall_delta_and_substantive_state_stability(
            self) -> None:
        current_unit = self.host.units[OBSERVER.GATEWAY_UNIT]
        pre = self.fault_identity(
            "PRE", epoch="epoch-1", unit_value=current_unit,
            observed=BOOTTIME_NS - 10_000_000_000)
        post = self.fault_identity(
            "POST", epoch="epoch-1", unit_value=current_unit,
            observed=BOOTTIME_NS - 1_000_000_000)
        pre = self.reseal_identity(
            pre, wall_clock_delta_ms=0,
            broker_deny_all=broker(checked=BOOTTIME_NS - 11_000_000_000))
        post = self.reseal_identity(
            post, wall_clock_delta_ms=5000,
            broker_deny_all=broker(checked=BOOTTIME_NS - 2_000_000_000))
        self.validate_transition("CLOCK_STEP", pre, post)
        bad = self.reseal_identity(post, wall_clock_delta_ms=0)
        with self.assertRaises(OBSERVER.ObserverError):
            self.validate_transition("CLOCK_STEP", pre, bad)
        too_small = self.reseal_identity(
            post, wall_clock_delta_ms=OBSERVER.MINIMUM_CLOCK_STEP_MS - 1)
        with self.assertRaises(OBSERVER.ObserverError):
            self.validate_transition("CLOCK_STEP", pre, too_small)
        too_large = self.reseal_identity(
            post, wall_clock_delta_ms=OBSERVER.MAXIMUM_CLOCK_STEP_MS + 1)
        with self.assertRaises(OBSERVER.ObserverError):
            self.validate_transition("CLOCK_STEP", pre, too_large)

    def test_any_fault_noop_identity_is_rejected(self) -> None:
        pre = self.fault_identity(
            "PRE", epoch="epoch-1",
            unit_value=self.host.units[OBSERVER.GATEWAY_UNIT],
            observed=BOOTTIME_NS - 10_000_000_000)
        post = self.reseal_identity(
            pre, phase="POST", observed_boottime_ns=BOOTTIME_NS - 1)
        with self.assertRaises(OBSERVER.ObserverError):
            self.validate_transition("PROCESS_KILL", pre, post)

    def test_plan_target_drift_is_rejected(self) -> None:
        plan = copy.deepcopy(self.plan)
        body = {key: value for key, value in plan.items()
                if key != "body_sha256"}
        body["planned_faults"][0]["target_id"] = "wrong-target"
        bad = OBSERVER.seal(body)
        spec = self._spec(bad["body_sha256"])
        spec_path = self.write("bad-spec.json", spec)
        plan_path = self.write("bad-plan.json", bad)
        injection_path = self.write("target-injection.json", self.injection())
        with self.assertRaisesRegex(OBSERVER.ObserverError, "PLAN_INVALID"):
            self.observer.fault(spec_path, plan_path, injection_path)

    def test_plan_enforces_fault_bounds_and_nonoverlap(self) -> None:
        first_injection = BOOTTIME_NS - 300_000_000_000
        first = {
            "fault_id": "fault-service-restart",
            "fault_type": "SERVICE_RESTART",
            "target_id": "watch-execution-gateway",
            "formal_campaign_id": FORMAL_ID,
            "inject_at_boottime_ns": first_injection,
            "maximum_injection_lateness_ns": 5_000_000_000,
            "maximum_recovery_ns": 60_000_000_000,
        }
        second = {
            "fault_id": "fault-network-reload",
            "fault_type": "NETWORK_DENY_RELOAD",
            "target_id": "broker-egress-deny-policy",
            "formal_campaign_id": FORMAL_ID,
            "inject_at_boottime_ns": first_injection + 120_000_000_000,
            "maximum_injection_lateness_ns": 5_000_000_000,
            "maximum_recovery_ns": 60_000_000_000,
        }

        def make_plan(faults: list[dict]) -> tuple[dict, dict]:
            plan = OBSERVER.seal({
                "schema": OBSERVER.FAULT_PLAN_SCHEMA, "version": 1,
                "campaign_id": CAMPAIGN_ID,
                "source_manifest_sha256": SOURCE_SHA,
                "policy_sha256": POLICY_SHA,
                "strategy_sha256": STRATEGY_SHA,
                "planned_faults": faults, **OBSERVER._boundary(),
            })
            return plan, self._spec(plan["body_sha256"])

        valid, spec = make_plan([first, second])
        self.assertEqual(
            OBSERVER.validate_plan(valid, spec), [first, second])
        cases = []
        excessive_lateness = copy.deepcopy(first)
        excessive_lateness["maximum_injection_lateness_ns"] = \
            30_000_000_001
        cases.append([excessive_lateness])
        excessive_recovery = copy.deepcopy(first)
        excessive_recovery["maximum_recovery_ns"] = 300_000_000_001
        cases.append([excessive_recovery])
        overlap = copy.deepcopy(second)
        overlap["inject_at_boottime_ns"] = (
            first_injection + first["maximum_injection_lateness_ns"] +
            first["maximum_recovery_ns"])
        cases.append([first, overlap])
        for faults in cases:
            with self.subTest(faults=faults):
                bad, bad_spec = make_plan(faults)
                with self.assertRaisesRegex(
                        OBSERVER.ObserverError, "FAULT_PLAN_INVALID"):
                    OBSERVER.validate_plan(bad, bad_spec)

    def test_fault_claimed_cleanup_rejects_residue(self) -> None:
        injection = self.injection()
        post = self.reseal_identity(
            injection["post_identity"], residue_count=1)
        path = self.write(
            "cleanup-residue.json", self.injection(post_identity=post))
        with self.assertRaisesRegex(OBSERVER.ObserverError, "RECEIPT_INVALID"):
            self.observer.fault(self.spec_path, self.plan_path, path)

    def test_fault_broker_exposure_is_rejected(self) -> None:
        injection = self.injection()
        post = self.reseal_identity(
            injection["post_identity"],
            broker_deny_all=broker(
                connectors=1, uids=[2301],
                checked=BOOTTIME_NS - 2_000_000_000))
        path = self.write(
            "fault-broker-exposure.json",
            self.injection(post_identity=post))
        with self.assertRaisesRegex(OBSERVER.ObserverError, "RECEIPT_INVALID"):
            self.observer.fault(self.spec_path, self.plan_path, path)

    def test_rehearsal_producer_cannot_cross_recorder_trust_boundary(self) -> None:
        recorder_sample = RECORDER.ClockSample(
            WALL_MS, BOOTTIME_NS, BOOT_ID)
        service = self.observer.service(self.spec_path, FORMAL_ID)
        authority = self.observer.authority(self.spec_path)
        injection_document = self.injection()
        injection_path = self.write(
            "cross-contract-injection.json", injection_document)
        fault = self.observer.fault(
            self.spec_path, self.plan_path, injection_path)
        self.retire_watch()
        cleanup = self.observer.cleanup(
            self.spec_path, subject_type="FINAL", subject_id=CAMPAIGN_ID)
        validations = (
            (service, RECORDER.validate_service_observation),
            (authority, RECORDER.validate_authority_observation),
            (fault, RECORDER.validate_fault_observation),
            (cleanup, RECORDER.validate_cleanup_observation),
        )
        for document, validator in validations:
            self.assertEqual(
                document["production_mode"], OBSERVER.REHEARSAL_MODE)
            with self.assertRaisesRegex(
                    RECORDER.RecorderError, "OBSERVATION_INVALID"):
                validator(document, self.spec, recorder_sample)

    def test_publish_is_noreplace_and_canonical(self) -> None:
        document = self.observer.authority(self.spec_path)
        output = self.root / "output.json"
        published = OBSERVER.publish_receipt(
            document, output, expected_uid=self.expected_uid,
            expected_gid=self.expected_gid)
        self.assertEqual(published, document)
        self.assertEqual(output.stat().st_mode & 0o777, 0o600)
        self.assertEqual(output.stat().st_gid, self.expected_gid)
        self.assertEqual(output.read_bytes(), OBSERVER.canonical_bytes(document))
        with self.assertRaisesRegex(OBSERVER.ObserverError, "ALREADY_EXISTS"):
            OBSERVER.publish_receipt(
                document, output, expected_uid=self.expected_uid,
                expected_gid=self.expected_gid)

    def test_input_group_mismatch_is_rejected(self) -> None:
        with self.assertRaisesRegex(
                OBSERVER.ObserverError, "INPUT_PARENT_UNTRUSTED"):
            OBSERVER.load_snapshot(
                self.spec_path, expected_uid=self.expected_uid,
                expected_gid=self.expected_gid + 1)

    def test_symlink_output_parent_is_rejected(self) -> None:
        document = self.observer.authority(self.spec_path)
        real = self.root / "real"
        real.mkdir(mode=0o700)
        link = self.root / "link"
        link.symlink_to(real, target_is_directory=True)
        with self.assertRaises(OBSERVER.ObserverError):
            OBSERVER.publish_receipt(
                document, link / "receipt.json",
                expected_uid=self.expected_uid,
                expected_gid=self.expected_gid)
        self.assertFalse((real / "receipt.json").exists())

    def test_cli_rejects_non_root_before_observation(self) -> None:
        output = self.root / "never.json"
        with mock.patch.object(OBSERVER.os, "geteuid", return_value=1000), \
                mock.patch.object(OBSERVER.os, "getegid", return_value=1000), \
                mock.patch("sys.stderr", new_callable=io.StringIO) as stderr:
            result = OBSERVER.main([
                "--run", "authority", "--campaign-spec", str(self.spec_path),
                "--output", str(output),
            ])
        self.assertEqual(result, 1)
        self.assertIn("P1_OBSERVER_ROOT_REQUIRED", stderr.getvalue())
        self.assertFalse(output.exists())

    def test_read_only_command_allowlist_rejects_mutation(self) -> None:
        with self.assertRaisesRegex(OBSERVER.ObserverError, "NOT_ALLOWLISTED"):
            OBSERVER.ReadOnlyHost._run((
                OBSERVER.SYSTEMCTL, "restart", OBSERVER.GATEWAY_UNIT))

    def test_evidence_rejects_duplicate_unit_identity(self) -> None:
        receipt = self.observer.authority(self.spec_path)
        evidence = copy.deepcopy(receipt["observation_evidence"])
        evidence["systemd_units"].append(evidence["systemd_units"][0])
        evidence = OBSERVER.seal({key: value for key, value in evidence.items()
                                  if key != "body_sha256"})
        with self.assertRaises(OBSERVER.ObserverError):
            OBSERVER.validate_evidence(evidence, "AUTHORITY", "INVALID")


if __name__ == "__main__":
    unittest.main()
