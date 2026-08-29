#!/usr/bin/env python3
"""Accelerated offline liveness tests for the P1 campaign lifecycle.

No test invokes systemctl, a broker helper, a credential surface, or a real
installed P1 executable.  A disposable fake systemd manager and fake pinned
helpers exercise restart, scheduling, cleanup, and durable recovery seams.
"""

from __future__ import annotations

import importlib.util
import copy
import os
from pathlib import Path
import shutil
import stat
import subprocess
import tempfile
import unittest
from unittest import mock
from typing import Any, Mapping, Sequence


ROOT = Path(__file__).resolve().parents[1]


def _load(name: str, relative: str) -> Any:
    spec = importlib.util.spec_from_file_location(name, ROOT / relative)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    import sys
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


C = _load(
    "hepta_p1_safety_soak_campaign_coordinator_under_test",
    "scripts/hepta_p1_safety_soak_campaign_coordinator.py")
A = _load(
    "hepta_p1_safety_soak_auditor_contract_under_coordinator_test",
    "scripts/hepta_p1_safety_soak_auditor.py")
O = _load(
    "hepta_p1_safety_soak_observer_worker_under_test",
    "scripts/hepta_p1_safety_soak_observer_worker.py")
R = _load(
    "hepta_p1_safety_soak_recorder_worker_under_test",
    "scripts/hepta_p1_safety_soak_recorder_worker.py")

import sys
_scripts_path = str(ROOT / "scripts")
sys.path.insert(0, _scripts_path)
try:
    L = _load(
        "hepta_p1_shadow_admission_launcher_under_coordinator_test",
        "scripts/hepta_p1_shadow_admission_launcher.py")
finally:
    sys.path.remove(_scripts_path)


SHA = "sha256:" + "1" * 64
BOOT = "11111111-2222-3333-4444-555555555555"
CAMPAIGN = "p1-safety-soak-offline-e2e"
FORMAL = "hepta-p1-shadow-soak-round95-20260803"
PROBE = "hepta-p1-shadow-load-probe-round94-20260803"
TRADING_DAYS = [
    "2026-08-03", "2026-08-04", "2026-08-05", "2026-08-06",
    "2026-08-07", "2026-08-10", "2026-08-11", "2026-08-12",
    "2026-08-13", "2026-08-14",
]


class Clock:
    def __init__(self, wall_ms: int = 1_000_000, boot_ns: int = 8_000_000):
        self.wall_ms = wall_ms
        self.boot_ns = boot_ns

    def wall(self) -> int:
        return self.wall_ms

    def boot(self) -> int:
        return self.boot_ns

    def advance(self, milliseconds: int) -> None:
        self.wall_ms += milliseconds
        self.boot_ns += milliseconds * 1_000_000


def _mkdir(path: Path) -> None:
    path.mkdir(mode=0o700)
    os.chmod(path, 0o700)


def _publish(module: Any, path: Path, body: Mapping[str, Any]) -> Any:
    document = module.seal(dict(body))
    return module.publish_noreplace(
        path, document, expected_uid=os.getuid(), expected_gid=os.getgid())


class FakeSystemd:
    def __init__(self, root: Path, clock: Clock):
        self.root = root
        self.clock = clock
        self.units: dict[str, dict[str, str]] = {}
        self.starts: list[tuple[str, list[str]]] = []
        self.properties: dict[str, list[str]] = {}
        self.calls: list[list[str]] = []
        self.next_pid = 4000

    @staticmethod
    def missing() -> dict[str, str]:
        value = {
            "LoadState": "not-found", "ActiveState": "inactive",
            "SubState": "dead", "MainPID": "0", "InvocationID": "",
            "ExecMainStartTimestampMonotonic": "0", "NRestarts": "0",
            "FragmentPath": "", "Result": "", "ExecMainStatus": "",
            "UnitFileState": "not-found", "Job": "",
        }
        for field in C.ProductionAdapter.UNIT_PROPERTIES:
            value.setdefault(field, "")
        for field in (
            "NoNewPrivileges", "PrivateTmp", "PrivateDevices",
            "ProtectHome", "ProtectHostname", "ProtectKernelTunables",
            "ProtectKernelModules", "ProtectKernelLogs",
            "ProtectControlGroups", "ProtectClock", "RestrictNamespaces",
            "RestrictSUIDSGID", "RestrictRealtime", "LockPersonality",
            "MemoryDenyWriteExecute", "RemoveIPC",
        ):
            value[field] = "no"
        value["ProtectSystem"] = "no"
        return value

    def show_unit(self, unit: str) -> Mapping[str, str]:
        return dict(self.units.get(unit, self.missing()))

    def start_transient(
        self, unit: str, argv: Sequence[str], properties: Sequence[str],
        environment: Mapping[str, str],
    ) -> None:
        del environment
        self.next_pid += 1
        self.starts.append((unit, list(argv)))
        self.properties[unit] = list(properties)
        value = self.missing()
        value.update({
            "LoadState": "loaded", "ActiveState": "active",
            "SubState": "running", "MainPID": str(self.next_pid),
            "InvocationID": f"fake-{self.next_pid}",
            "ExecMainStartTimestampMonotonic": str(self.clock.boot_ns // 1000),
            "NRestarts": "0",
            "FragmentPath": f"/run/systemd/transient/{unit}",
            "Result": "", "ExecMainStatus": "",
            "UnitFileState": "transient", "Job": "",
        })
        for raw in properties:
            key, separator, item = raw.partition("=")
            if separator != "=" or key not in value:
                continue
            if key == "IPAddressDeny" and item == "any":
                item = "0.0.0.0/0 ::/0"
            elif key == "CapabilityBoundingSet":
                item = " ".join(word.lower() for word in item.split())
            if key in {"PartOf", "BindsTo", "After", "ReadWritePaths"} and \
                    value[key]:
                value[key] += " " + item
            else:
                value[key] = item
        self.units[unit] = value

    def start_unit(self, unit: str) -> None:
        value = self.units[unit]
        self.next_pid += 1
        value.update({
            "ActiveState": "active", "SubState": "running",
            "MainPID": str(self.next_pid),
            "InvocationID": f"fake-{self.next_pid}",
            "NRestarts": str(int(value["NRestarts"]) + 1),
        })

    def automatic_restart(self, unit: str) -> None:
        self.start_unit(unit)

    def complete(self, unit: str, success: bool = True) -> None:
        value = self.units[unit]
        value.update({
            "ActiveState": "inactive", "SubState": "dead", "MainPID": "0",
            "Result": "success" if success else "exit-code",
            "ExecMainStatus": "0" if success else "4",
        })

    def stop_unit(self, unit: str) -> None:
        if unit in self.units:
            self.complete(unit)

    def unit_enabled_state(self, unit: str) -> str:
        return self.units.get(unit, self.missing())["UnitFileState"]

    def unit_contract_state(self, unit: str) -> Mapping[str, str]:
        value = self.units.get(unit, self.missing())
        return {
            "LoadState": value["LoadState"],
            "UnitFileState": value["UnitFileState"],
            "FragmentPath": value["FragmentPath"],
            "DropInPaths": "",
        }

    def sleep(self, seconds: float) -> None:
        self.clock.advance(max(1, int(seconds * 1000)))

    def run(self, argv: Sequence[str], timeout_seconds: int) -> C.CommandResult:
        del timeout_seconds
        values = list(argv)
        self.calls.append(values)
        if values[0] == C.PIN_PRODUCER:
            output = Path(values[values.index("--output") + 1])
            _publish(C, output, {
                "schema": "fake.pins.v1", "version": 1,
                "status": "COMPLETE",
                **C.boundary(),
            })
        elif values[0] == C.AUDITOR:
            output = Path(values[values.index("--output") + 1])
            runtime_snapshot = C._secure_read(
                self.root / "state" / "runtime-manifest.json",
                expected_uid=os.getuid(), expected_gid=os.getgid(),
                modes=frozenset({0o600}))
            runtime = runtime_snapshot.document
            spec = C._secure_read(
                Path(runtime["campaign_spec"]["path"]),
                expected_uid=os.getuid(), expected_gid=os.getgid(),
                modes=frozenset({0o600}))
            freeze = C._open_reference(
                runtime["freeze_bundle"], expected_uid=os.getuid(),
                expected_gid=os.getgid(), reason="TEST_FREEZE")
            plan = C._open_reference(
                runtime["fault_plan"], expected_uid=os.getuid(),
                expected_gid=os.getgid(), reason="TEST_PLAN")
            duration = C.MINIMUM_BOOTTIME_DURATION_NS
            origin = runtime["formal_campaigns"][0][
                "launcher_dispatch_at_ms"]
            end = runtime["formal_campaigns"][-1]["teardown_deadline_ms"]
            cadence = runtime["observer_cadence_ms"]
            final_slot = (end - origin + cadence - 1) // cadence
            _publish(C, output, {
                "schema": C.AUDIT_SCHEMA, "version": 1,
                "phase": "P1_SHADOW", "verdict": "GO",
                "campaign_id": runtime["campaign_id"],
                "domain_id": "alpha",
                "independent_auditor_id": "offline-auditor",
                "audited_at_ms": self.clock.wall_ms,
                "campaign_spec_file_sha256": spec.file_sha256,
                "campaign_spec_body_sha256": spec.body_sha256,
                "campaign_runtime": {
                    **C._reference(runtime_snapshot),
                    "schema": C.CAMPAIGN_RUNTIME_SCHEMA,
                },
                "freeze_bundle": runtime["freeze_bundle"],
                "producer": runtime["executables"]["auditor"],
                "production_mode": "PRODUCTION_ROOT_AUDIT",
                "source_manifest_sha256":
                    spec.document["source_manifest_sha256"],
                "policy_sha256": SHA, "strategy_sha256": SHA,
                "evaluated_interval": {
                    "clock_id": "CLOCK_BOOTTIME", "boot_id": BOOT,
                    "start_boottime_ns": self.clock.boot_ns,
                    "end_boottime_ns": self.clock.boot_ns + duration,
                    "duration_ns": duration,
                    "maximum_checkpoint_gap_ns": 1_000_000_000,
                    "consecutive": True,
                    "continuity_origin_ms": origin,
                    "continuity_end_ms": end,
                    "continuity_final_slot": final_slot,
                },
                "counts": {
                    "launcher_receipts": 1, "verified_closures": 1,
                    "continuity_checkpoints": 2,
                    "declared_trading_days": len(TRADING_DAYS),
                    "observed_trading_days": len(TRADING_DAYS),
                    "scheduled_decisions": 200,
                    "decision_receipts": 200, "eligible_decisions": 200,
                    "complete_eligible_decisions": 200,
                    "incomplete_eligible_decisions": 0,
                    "catch_up_decisions": 0, "planned_faults": 7,
                    "fault_results": 7, "authority_snapshots": 2,
                    "cleanup_snapshots": 9,
                },
                "completeness": {
                    "numerator": 200, "denominator": 200,
                    "ppm": 1_000_000,
                    "strictly_greater_than_99_percent": True,
                },
                "checked_artifacts": sorted([{
                    "role": role, "path": str(snapshot.path),
                    "file_sha256": snapshot.file_sha256,
                    "body_sha256": snapshot.body_sha256,
                } for role, snapshot in (
                    ("campaign_spec", spec), ("fault_plan", plan),
                    ("freeze_bundle", freeze),
                )], key=lambda item: (item["role"], item["path"])),
                "failed_invariants": [],
                "exposure_summary": {
                    "evidence_present": True,
                    "maximum_connector_count": 0,
                    "maximum_authorized_uid_count": 0,
                    "maximum_paper_unit_active_count": 0,
                    "campaign_socket_ever_present": False,
                    "kill_switch_continuously_engaged": True,
                    "local_boundary_uncertain": False,
                    "scope": "LOCAL_HOST_BOUNDARY_ONLY",
                    "authoritative_account_state_observed": False,
                },
                "cleanup_status": {
                    "required_subject_count": 9,
                    "verified_subject_count": 9, "complete": True,
                },
                "p1_safety_soak_gate_satisfied": True,
                "paper_test_admission_candidate": False,
                "safest_allowed_next_action":
                    "CONTINUE_REMAINING_PAPER_ADMISSION_GATES",
                **C.boundary(),
            })
        return C.CommandResult(0, b"{}\n", b"")


class FakeObserverRunner:
    def __init__(self, module: Any, campaign: str):
        self.module = module
        self.campaign = campaign
        self.calls: list[list[str]] = []

    def run(self, argv: Sequence[str], timeout_seconds: int) -> Any:
        del timeout_seconds
        values = list(argv)
        self.calls.append(values)
        output = Path(values[values.index("--output") + 1])
        command = values[2]
        if command == "campaign-continuity":
            schema = O.CAMPAIGN_CONTINUITY_SCHEMA
        else:
            schema = {
                "service": O.SERVICE_SCHEMA,
                "authority": O.AUTHORITY_SCHEMA,
                "fault": O.FAULT_SCHEMA,
                "cleanup": O.CLEANUP_SCHEMA,
            }[command]
        _publish(O.C, output, {
            "schema": schema, "version": 1, "status": "COMPLETE",
            "campaign_id": self.campaign,
            "production_mode": "PRODUCTION_ROOT_OBSERVER",
            "observation_evidence": {"broker_deny_all": {
                "helper_path": O.BROKER_EGRESS_POLICY,
                "helper_file_sha256": SHA,
            }},
            **O.C.boundary(),
        })
        return O.C.CommandResult(0, b"", b"")

    @staticmethod
    def sleep(_seconds: float) -> None:
        return


class FakeRecorderRunner:
    def __init__(self):
        self.calls: list[list[str]] = []

    def run(self, argv: Sequence[str], timeout_seconds: int) -> Any:
        del timeout_seconds
        self.calls.append(list(argv))
        return R.C.CommandResult(0, b"{}\n", b"")

    @staticmethod
    def sleep(_seconds: float) -> None:
        return


class Fixture:
    def __init__(self, base: Path, clock: Clock):
        self.base = base
        self.clock = clock
        self.state = base / "state"
        _mkdir(self.state)
        for name in (
            "coordinator-journal", "recorder", "raw-observations",
            "injector-journal", "injection-receipts", "control",
        ):
            _mkdir(self.state / name)
        for name in ("continuity", "service", "authority", "fault", "cleanup"):
            _mkdir(self.state / "raw-observations" / name)
        for name in (
            "observer-requests", "observer-acks", "recorder-requests",
            "recorder-acks",
        ):
            _mkdir(self.state / "control" / name)
        _mkdir(self.state / "recorder" / "journal")
        self.formal_receipt = base / "launcher-receipt.json"
        self.closure = base / "verified-closure.json"
        self.artifact_root = base / "artifacts"
        _mkdir(self.artifact_root)
        self.spec = _publish(C, self.state / "recorder" / "campaign-spec.json", {
            "schema": C.SPEC_SCHEMA, "version": 1, "campaign_id": CAMPAIGN,
            "domain_id": "alpha", "independent_auditor_id":
                "offline-auditor",
            "source_manifest_sha256": SHA,
            "policy_sha256": SHA, "strategy_sha256": SHA,
            "declared_trading_days": TRADING_DAYS,
            "scheduled_decision_count": 200,
            "minimum_eligible_decisions": 200,
            "eligible_scheduled_at_ms": list(range(200)),
            "minimum_complete_ppm": 990_001,
            "minimum_boottime_duration_ns":
                C.MINIMUM_BOOTTIME_DURATION_NS,
            "maximum_checkpoint_gap_ns": 15 * 60 * 1_000_000_000,
            "maximum_decision_lateness_ms": 60_000,
            **C.boundary(),
        })
        self.plan = _publish(C, self.state / "recorder" / "fault-plan.json", {
            "schema": C.PLAN_SCHEMA, "version": 1, "campaign_id": CAMPAIGN,
            **C.boundary(),
        })
        self.bundle = _publish(C, base / "freeze.json", {
            "schema": C.FREEZE_SCHEMA, "version": 1,
            "campaign_id": CAMPAIGN, **C.boundary(),
        })
        executables = {
            role: {"path": path, "file_sha256": SHA}
            for role, path in C.ROLE_PATHS.items()
        }
        interval = C.POLICY_SLOT_INTERVAL_MS
        start = (
            (clock.wall_ms + C.LAUNCHER_EARLY_START_LEAD_MS +
             C.LAUNCHER_WARMUP_MS) // interval + 1
        ) * interval
        launcher_start = start - C.LAUNCHER_WARMUP_MS
        launcher_dispatch = launcher_start - C.LAUNCHER_EARLY_START_LEAD_MS
        formal_expiry = start + C.POLICY_MAXIMUM_ITERATIONS * interval
        runtime_document = C.seal({
            "schema": C.RUNTIME_SCHEMA, "version": 1, "status": "FROZEN",
            "campaign_id": CAMPAIGN, "round": 95, "boot_id": BOOT,
            "issued_at_ms": clock.wall_ms - 1000,
            "expires_at_ms": formal_expiry + 2 * 60 * 60 * 1000,
            "freeze_bundle": C._reference(self.bundle),
            "campaign_spec": C._reference(self.spec),
            "fault_plan": C._reference(self.plan),
            "pin_formal_campaign_id": FORMAL,
            "formal_campaigns": [{
                "formal_campaign_id": FORMAL, "probe_campaign_id": PROBE,
                "launcher_start_ms": launcher_start,
                "launcher_dispatch_at_ms": launcher_dispatch,
                "valid_after_ms": start,
                "expires_at_ms": formal_expiry,
                "launcher_completion_deadline_ms":
                    formal_expiry + C.MAXIMUM_LAUNCH_LATENESS_MS,
                "projection_deadline_ms":
                    formal_expiry + C.POST_FORMAL_PROJECTION_GUARD_MS,
                "teardown_deadline_ms":
                    formal_expiry + C.POST_FORMAL_TEARDOWN_GUARD_MS,
                "slot_interval_ms": interval,
                "maximum_iterations": C.POLICY_MAXIMUM_ITERATIONS,
                "policy": {"path": str(base / "policy.json"),
                           "file_sha256": SHA, "body_sha256": SHA},
                "launcher_receipt_path": str(self.formal_receipt),
                "verified_closure_path": str(self.closure),
                "artifact_root": str(self.artifact_root),
            }],
            "observer_cadence_ms": 1000,
            "maximum_slot_lateness_ms": 500,
            "state_root": str(self.state),
            "raw_observation_directory": str(
                self.state / "raw-observations"),
            "recorder_root": str(self.state / "recorder"),
            "injector_journal_directory": str(
                self.state / "injector-journal"),
            "injector_output_directory": str(
                self.state / "injection-receipts"),
            "control_directory": str(self.state / "control"),
            "executables": executables, **C.boundary(),
        })
        self.runtime = C.publish_noreplace(
            self.state / "runtime-manifest.json", runtime_document,
            expected_uid=os.getuid(), expected_gid=os.getgid())
        # Constructors do not consult launch-contract/bundle contents after
        # snapshots are bound; minimal canonical values keep the test focused.
        self.contract = _publish(C, base / "contract.json", {
            "schema": "fake.contract.v1", **C.boundary(),
        })

    def coordinator(self, adapter: FakeSystemd,
                    crash_after: str | None = None) -> Any:
        return C.CampaignCoordinator(
            self.contract, self.bundle, self.runtime, adapter,
            expected_uid=os.getuid(), expected_gid=os.getgid(),
            wall_clock=self.clock.wall, boot_clock=self.clock.boot,
            crash_after_event=crash_after)


def _continuity_coordinator(
    fixture: Fixture, clock: Clock, manager: FakeSystemd, *,
    raw_mutation: tuple[int, str, Any] | None = None,
    checkpoint_mutation: tuple[int, str, Any] | None = None,
    omitted_checkpoint: int | None = None,
    duplicate_projection: int | None = None,
) -> Any:
    """Publish a three-slot non-grid continuity chain for coordinator tests."""

    runtime_value = copy.deepcopy(fixture.runtime.document)
    runtime_value.pop("body_sha256")
    origin = runtime_value["formal_campaigns"][0]["launcher_dispatch_at_ms"]
    cadence = 1000
    end = origin + 1500
    runtime_value["formal_campaigns"][-1]["teardown_deadline_ms"] = end
    runtime_value["observer_cadence_ms"] = cadence
    runtime_value["maximum_slot_lateness_ms"] = 500
    runtime = _publish(
        C, fixture.state / "short-runtime-manifest.json", runtime_value)
    coordinator = C.CampaignCoordinator(
        fixture.contract, fixture.bundle, runtime, manager,
        expected_uid=os.getuid(), expected_gid=os.getgid(),
        wall_clock=clock.wall, boot_clock=clock.boot)
    observer_path = fixture.state / "observer-worker-journal"
    recorder_path = fixture.state / "recorder-worker-journal"
    _mkdir(observer_path)
    _mkdir(recorder_path)
    _mkdir(fixture.state / "recorder" / "checkpoints")
    observer = C.Journal(
        observer_path, CAMPAIGN, expected_uid=os.getuid(),
        expected_gid=os.getgid(), wall_clock=clock.wall,
        boot_clock=clock.boot)
    recorder = C.Journal(
        recorder_path, CAMPAIGN, expected_uid=os.getuid(),
        expected_gid=os.getgid(), wall_clock=clock.wall,
        boot_clock=clock.boot)
    runtime_reference = {
        **C._reference(runtime), "schema": C.CAMPAIGN_RUNTIME_SCHEMA,
    }
    previous_checkpoint: str | None = None
    identity = {"fixture": "frozen"}
    dynamic = {"fixture": "dynamic"}
    for slot in range(3):
        scheduled = min(origin + slot * cadence, end)
        raw_body = {
            "schema": C.CAMPAIGN_CONTINUITY_OBSERVATION_SCHEMA,
            "version": 1, "status": "COMPLETE",
            "observed_at_ms": scheduled,
            "expires_at_ms": scheduled + 60_000,
            "campaign_id": CAMPAIGN, "observer_id": "fixture-observer",
            "observation_complete": True, "clock_id": "CLOCK_BOOTTIME",
            "boot_id": BOOT, "observed_boottime_ns": 10_000_000 + slot,
            "source_manifest_sha256": SHA, "policy_sha256": SHA,
            "strategy_sha256": SHA,
            "freeze_bundle": runtime_value["freeze_bundle"],
            "campaign_runtime": runtime_reference,
            "continuity_slot_index": slot,
            "continuity_scheduled_at_ms": scheduled,
            "continuity_origin_ms": origin, "continuity_end_ms": end,
            "continuity_cadence_ms": cadence, "continuity_final_slot": 2,
            "continuity_is_final": slot == 2, "catch_up": False,
            "activation_receipt": identity,
            "activation_receipt_document": identity,
            "lease_receipt": identity, "lease_receipt_document": identity,
            "lease_generation": 1, "previous_lease_generation": 0,
            "previous_lease_receipt_body_sha256": SHA,
            "gateway_identity": dynamic,
            "gateway_process_identity": dynamic,
            "gateway_executable_identity": identity,
            "gateway_profile_identity": identity,
            "gateway_domain_config_identity": identity,
            "supervisor_socket_identity": dynamic,
            "custodian_identity": identity,
            "collector_timer_identity": identity,
            "activation_reconcile_timer_identity": identity,
            "tool_socket_identity": dynamic,
            "transition_fault_id": None, "persistent_stack_ok": True,
            "lease_chain_ok": True, "connector_count": 0,
            "authorized_uids": [], "paper_unit_active_count": 0,
            "campaign_socket_present": False,
            "kill_switch_engaged": True, "zero_exposure": True,
            "producer": {"path": C.OBSERVER, "file_sha256": SHA},
            "production_mode": "PRODUCTION_ROOT_OBSERVER",
            "observation_evidence": {}, **C.boundary(),
        }
        if raw_mutation is not None and raw_mutation[0] == slot:
            raw_body[raw_mutation[1]] = raw_mutation[2]
        raw = _publish(
            C, fixture.state / "raw-observations" / "continuity" /
            f"{slot:08d}.json", raw_body)
        observer.append("CAMPAIGN_CONTINUITY_SLOT", "COMMITTED", {
            "first_slot": slot, "last_slot": slot,
            "scheduled_at_ms": scheduled, "origin_ms": origin,
            "end_ms": end, "cadence_ms": cadence,
            "maximum_slot_lateness_ms": 500, "final_slot": 2,
            "transition_fault_id": None,
            "continuity_observation": C._reference(raw), "catch_up": False,
        })
        if omitted_checkpoint == slot:
            continue
        checkpoint_body = {
            "schema": C.CONTINUITY_CHECKPOINT_SCHEMA, "version": 1,
            "campaign_id": CAMPAIGN, "sequence": slot,
            **{
                field: raw.document[field]
                for field in C.CONTINUITY_CHECKPOINT_COPY_FIELDS
            },
            "previous_checkpoint_body_sha256": previous_checkpoint,
            "observer_receipt": {
                **C._reference(raw),
                "schema": C.CAMPAIGN_CONTINUITY_OBSERVATION_SCHEMA,
            },
        }
        if checkpoint_mutation is not None and checkpoint_mutation[0] == slot:
            checkpoint_body[checkpoint_mutation[1]] = checkpoint_mutation[2]
        checkpoint = _publish(
            C, fixture.state / "recorder" / "checkpoints" /
            f"{slot:08d}.json", checkpoint_body)
        previous_checkpoint = checkpoint.body_sha256
        projection_details = {
            "kind": "continuity", "input_file_sha256": raw.file_sha256,
            "outputs": [{
                "role": "continuity_checkpoint", "path": str(checkpoint.path),
                "file_sha256": checkpoint.file_sha256,
                "body_sha256": checkpoint.body_sha256,
                "schema": C.CONTINUITY_CHECKPOINT_SCHEMA, "sealed": True,
            }], "unsafe": False,
        }
        recorder.append(
            "PROJECT_OBSERVATION", "COMMITTED", projection_details)
        if duplicate_projection == slot:
            recorder.append(
                "PROJECT_OBSERVATION", "COMMITTED", projection_details)
    coordinator._worker_journals = {
        "observer": observer, "recorder": recorder,
    }
    return coordinator


class LauncherScheduleContractTests(unittest.TestCase):
    def test_expired_contract_is_usable_only_for_terminal_recovery(self) \
            -> None:
        issued_at_ms = 2_000_000_000_000
        expires_at_ms = issued_at_ms + 60_000
        contract = C.seal({
            "schema": C.CONTRACT_SCHEMA, "version": 1,
            "status": "FROZEN", "campaign_id": CAMPAIGN,
            "freeze_bundle": {
                "path": "/tmp/freeze.json", "file_sha256": SHA,
                "body_sha256": SHA,
            },
            "state_root": "/var/lib/hepta/p1-safety-soak/" + CAMPAIGN,
            "pin_formal_campaign_id": FORMAL,
            "observer_cadence_ms": 1000,
            "maximum_slot_lateness_ms": 500,
            "poll_interval_ms": 100,
            "issued_at_ms": issued_at_ms,
            "expires_at_ms": expires_at_ms,
            **C.boundary(),
        })
        C.validate_launch_contract(contract, issued_at_ms)
        with self.assertRaisesRegex(
                C.CoordinatorError, "P1_COORDINATOR_LAUNCH_CONTRACT_INVALID"):
            C.validate_launch_contract(contract, expires_at_ms)
        C.validate_launch_contract(
            contract, expires_at_ms, require_current=False)

    def test_real_launcher_alignment_inverts_exactly_once(self) -> None:
        self.assertEqual(C.LAUNCHER_WARMUP_MS,
                         L.POLICY_MINIMUM_WARMUP_MS)
        self.assertEqual(C.LAUNCHER_EARLY_START_LEAD_MS,
                         L.PROBE_DISPATCH_LEAD_MS)
        slot = L.POLICY_SLOT_INTERVAL_MS
        valid_after = ((2_000_000_000_000 + slot - 1) // slot) * slot
        launcher_start = C.launcher_start_for_policy(valid_after, slot)
        self.assertEqual(valid_after - launcher_start,
                         L.POLICY_MINIMUM_WARMUP_MS)
        configuration = L.LaunchConfiguration(
            probe_campaign_id=PROBE, formal_campaign_id=FORMAL,
            formal_start_ms=launcher_start)
        configuration.validate(
            launcher_start - C.LAUNCHER_EARLY_START_LEAD_MS)
        binding = {
            "schema": "hepta.strategy-shadow-observation-campaign.v1",
            "campaign_id": FORMAL, "valid_after_ms": valid_after,
            "expires_at_ms": valid_after +
                L.FORMAL_ITERATIONS * slot,
            "slot_interval_ms": slot,
            "maximum_iterations": L.FORMAL_ITERATIONS,
            "maximum_lateness_ms": L.POLICY_MAXIMUM_LATENESS_MS,
            "shadow_only": True, "paper_authorized": False,
            "live_authorized": False, "mutation_attempted": False,
            "direct_broker_access": False,
        }
        policy = L.seal({
            "schema": "hepta.strategy-shadow-observation-policy.v1",
            "version": 1, "campaign_id": FORMAL,
            "campaign_sha256": L.digest_bytes(L.canonical_bytes(binding)),
            "strategy_id": "eurusd-confirmed-momentum",
            "strategy_version": "v1", "strategy_sha256": SHA,
            "valid_after_ms": valid_after,
            "expires_at_ms": binding["expires_at_ms"],
            "slot_interval_ms": slot,
            "maximum_iterations": L.FORMAL_ITERATIONS,
            "maximum_lateness_ms": L.POLICY_MAXIMUM_LATENESS_MS,
            "shadow_only": True, "paper_authorized": False,
            "live_authorized": False, "mutation_attempted": False,
            "direct_broker_access": False,
        })
        self.assertEqual(
            L._validated_policy_schedule(configuration, FORMAL, policy),
            (valid_after, L.FORMAL_ITERATIONS))
        with self.assertRaises(C.CoordinatorError):
            C.launcher_start_for_policy(valid_after + 1, slot)
        with self.assertRaises(L.LauncherError):
            configuration.validate(
                launcher_start + L.FORMAL_START_CLOCK_TOLERANCE_MS + 1)

    def test_freeze_consumer_rejects_overlap_and_dispatch_before_teardown(self) \
            -> None:
        now_ms = 2_000_000_000_000
        interval = L.POLICY_SLOT_INTERVAL_MS
        first_valid = ((now_ms + C.LAUNCHER_EARLY_START_LEAD_MS +
                        C.LAUNCHER_WARMUP_MS + interval - 1) //
                       interval) * interval
        first_expiry = first_valid + C.POLICY_MAXIMUM_ITERATIONS * interval
        second_valid = C.required_valid_after_after_teardown(
            first_expiry + C.POST_FORMAL_TEARDOWN_GUARD_MS, interval)
        reference = {
            "path": "/tmp/frozen.json", "file_sha256": SHA,
            "body_sha256": SHA,
        }
        def frozen_formal(campaign_id: str, valid_after: int) -> dict[str, Any]:
            launcher_start = valid_after - C.LAUNCHER_WARMUP_MS
            expiry = valid_after + C.POLICY_MAXIMUM_ITERATIONS * interval
            return {
                "campaign_id": campaign_id, **reference,
                "launcher_start_ms": launcher_start,
                "launcher_dispatch_at_ms":
                    launcher_start - C.LAUNCHER_EARLY_START_LEAD_MS,
                "valid_after_ms": valid_after,
                "expires_at_ms": expiry,
                "slot_interval_ms": interval,
                "maximum_iterations": C.POLICY_MAXIMUM_ITERATIONS,
                "launcher_completion_deadline_ms":
                    expiry + C.MAXIMUM_LAUNCH_LATENESS_MS,
                "projection_deadline_ms":
                    expiry + C.POST_FORMAL_PROJECTION_GUARD_MS,
                "teardown_deadline_ms":
                    expiry + C.POST_FORMAL_TEARDOWN_GUARD_MS,
            }
        formals = [
            frozen_formal(FORMAL, first_valid),
            frozen_formal(
                "hepta-p1-shadow-soak-round96-20260804", second_valid),
        ]
        document = C.seal({
            "schema": C.FREEZE_SCHEMA, "version": 1, "status": "FROZEN",
            "production_mode": "PRODUCTION_ROOT_PREFLIGHT", "round": 95,
            "boot_id": BOOT, "issued_at_ms": now_ms,
            "expires_at_ms":
                formals[-1]["teardown_deadline_ms"] + interval,
            "campaign_id": CAMPAIGN, "source_manifest_sha256": SHA,
            "declared_trading_days": TRADING_DAYS,
            "eligible_scheduled_at_ms": list(range(200)),
            "formal_policies": formals, "planned_faults": [{}] * 7,
            "source_producer_pins": [{
                "role": role, "source_path": f"scripts/{role}.py",
                "installed_path": path, "file_sha256": SHA,
            } for role, path in C.ROLE_PATHS.items()],
            "anchors": {role: reference for role in (
                "source_anchor", "policy_anchor", "strategy_anchor",
                "frozen_schedule", "frozen_fault_schedule")},
            **C.boundary(),
        })
        C.validate_freeze_bundle(document, now_ms)
        with self.assertRaisesRegex(
                C.CoordinatorError, "P1_COORDINATOR_FREEZE_BUNDLE_INVALID"):
            C.validate_freeze_bundle(document, document["expires_at_ms"])
        C.validate_freeze_bundle(
            document, document["expires_at_ms"], require_current=False)
        bad = copy.deepcopy(document)
        bad.pop("body_sha256")
        bad_second = bad["formal_policies"][1]
        bad_valid = second_valid + interval
        bad_start = bad_valid - C.LAUNCHER_WARMUP_MS
        bad_expiry = bad_valid + C.POLICY_MAXIMUM_ITERATIONS * interval
        bad_second["launcher_start_ms"] = bad_start
        bad_second["launcher_dispatch_at_ms"] = (
            bad_start - C.LAUNCHER_EARLY_START_LEAD_MS)
        bad_second["valid_after_ms"] = bad_valid
        bad_second["expires_at_ms"] = bad_expiry
        bad_second["launcher_completion_deadline_ms"] = (
            bad_expiry + C.MAXIMUM_LAUNCH_LATENESS_MS)
        bad_second["projection_deadline_ms"] = (
            bad_expiry + C.POST_FORMAL_PROJECTION_GUARD_MS)
        bad_second["teardown_deadline_ms"] = (
            bad_expiry + C.POST_FORMAL_TEARDOWN_GUARD_MS)
        bad = C.seal(bad)
        with self.assertRaisesRegex(
                C.CoordinatorError,
                "(?:FREEZE_BUNDLE_INVALID|LAUNCH_ANCHOR_INVALID)"):
            C.validate_freeze_bundle(bad, now_ms)


def _publish_ack(module: Any, queue: Any, target: str,
                 request: Mapping[str, Any], outputs: list[dict[str, Any]],
                 now_ms: int) -> None:
    entries = queue._load_chain(target, "acks")
    document = module.seal({
        "schema": module.ACK_SCHEMA, "version": 1,
        "campaign_id": CAMPAIGN, "sequence": len(entries),
        "request_id": request["request_id"], "worker": target,
        "action": request["action"], "status": "COMPLETE",
        "completed_at_ms": now_ms, "outputs": outputs,
        "previous_body_sha256": (
            None if not entries else entries[-1].body_sha256),
        **module.boundary(),
    })
    module.publish_noreplace(
        queue._directory(target, "acks") / f"{len(entries):08d}.json",
        document, expected_uid=os.getuid(), expected_gid=os.getgid())


class ProductionAdapterWatchdogTests(unittest.TestCase):
    def test_slow_fixed_command_pulses_watchdog_until_completion(self) -> None:
        class SlowProcess:
            def __init__(self) -> None:
                self.returncode: int | None = None
                self.communications = 0

            def communicate(self, timeout: float) -> tuple[bytes, bytes]:
                self.communications += 1
                if self.communications == 1:
                    raise subprocess.TimeoutExpired(["/bin/true"], timeout)
                self.returncode = 0
                return b"bounded\n", b""

            def poll(self) -> int | None:
                return self.returncode

            def kill(self) -> None:
                self.returncode = -9

        process = SlowProcess()
        with mock.patch.object(C.subprocess, "Popen", return_value=process), \
                mock.patch.object(C, "_sd_notify") as notify:
            result = C.ProductionAdapter._command(["/bin/true"], 30)
        self.assertEqual(result, C.CommandResult(0, b"bounded\n", b""))
        notify.assert_called_once_with(
            "WATCHDOG=1\nSTATUS=waiting for fixed child command")


class CoordinatorLivenessTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.base = Path(self.temporary.name)
        os.chmod(self.base, 0o700)
        self.clock = Clock()
        self.fixture = Fixture(self.base, self.clock)
        self.executable_patch = mock.patch.object(
            C, "_secure_executable", return_value=b"pinned")
        self.executable_patch.start()

    def tearDown(self) -> None:
        self.executable_patch.stop()
        self.temporary.cleanup()

    def test_every_continuity_raw_is_exactly_projected_and_cross_bound(
            self) -> None:
        manager = FakeSystemd(self.base, self.clock)
        coordinator = _continuity_coordinator(
            self.fixture, self.clock, manager)
        self.assertTrue(coordinator.campaign_continuity_complete())

    def test_continuity_rejects_raw_grid_checkpoint_and_projection_drift(
            self) -> None:
        cases = (
            {"raw_mutation": (1, "continuity_cadence_ms", 999)},
            {"checkpoint_mutation": (
                1, "continuity_scheduled_at_ms", 123)},
            {"omitted_checkpoint": 1},
            {"duplicate_projection": 1},
        )
        for index, options in enumerate(cases):
            with self.subTest(case=index):
                child = self.base / f"continuity-adversarial-{index}"
                _mkdir(child)
                clock = Clock()
                fixture = Fixture(child, clock)
                manager = FakeSystemd(child, clock)
                coordinator = _continuity_coordinator(
                    fixture, clock, manager, **options)
                clock.wall_ms = (
                    coordinator.runtime["formal_campaigns"][-1][
                        "teardown_deadline_ms"] +
                    coordinator.runtime["maximum_slot_lateness_ms"] +
                    C.CONTINUITY_PROJECTION_GUARD_MS + 1)
                with self.assertRaisesRegex(
                        C.CoordinatorError,
                        "P1_COORDINATOR_CAMPAIGN_CONTINUITY_INCOMPLETE"):
                    coordinator.campaign_continuity_complete()

    def test_worker_intent_crash_recovers_without_duplicate_start(self) -> None:
        manager = FakeSystemd(self.base, self.clock)
        observer_argv = [C.OBSERVER_WORKER, "--run", "--runtime-manifest",
                         str(self.fixture.runtime.path),
                         "--expected-runtime-manifest-file-sha256",
                         self.fixture.runtime.file_sha256]
        crashed = self.fixture.coordinator(
            manager, "START_WORKER:INTENT")
        with self.assertRaises(C.InjectedCrash):
            crashed.ensure_worker("observer", observer_argv)
        self.assertEqual(manager.starts, [])
        restarted = self.fixture.coordinator(manager)
        restarted.ensure_worker("observer", observer_argv)
        self.assertEqual(len(manager.starts), 1)
        # A systemd restart changes process identity, but the immutable unit
        # fragment and coordinator transition are reattached, not recreated.
        unit = restarted._unit_names()["observer"]
        manager.complete(unit, success=False)
        manager.automatic_restart(unit)
        again = self.fixture.coordinator(manager)
        again.ensure_worker("observer", observer_argv)
        self.assertEqual(len(manager.starts), 1)
        self.assertEqual(manager.units[unit]["NRestarts"], "1")

    def test_running_worker_with_durable_failure_is_rejected(self) -> None:
        manager = FakeSystemd(self.base, self.clock)
        coordinator = self.fixture.coordinator(manager)
        for worker, executable in (
            ("observer", C.OBSERVER_WORKER),
            ("recorder", C.RECORDER_WORKER),
        ):
            argv = [
                executable, "--run", "--runtime-manifest",
                str(self.fixture.runtime.path),
                "--expected-runtime-manifest-file-sha256",
                self.fixture.runtime.file_sha256,
            ]
            coordinator.ensure_worker(worker, argv)
            _mkdir(self.fixture.state / f"{worker}-worker-journal")
        worker_journal = C.Journal(
            self.fixture.state / "observer-worker-journal", CAMPAIGN,
            expected_uid=os.getuid(), expected_gid=os.getgid(),
            wall_clock=self.clock.wall, boot_clock=self.clock.boot)
        worker_journal.append("WORKER", "FAILED_CLOSED", {
            "worker": "observer", "reason": "TEST_FAILURE",
            "catch_up": False,
        })
        with self.assertRaisesRegex(
                C.CoordinatorError, "WORKER_FAILED_CLOSED"):
            coordinator.assert_workers_healthy()

    def test_launcher_restart_reattaches_and_missed_slot_never_catches_up(self) -> None:
        manager = FakeSystemd(self.base, self.clock)
        coordinator = self.fixture.coordinator(manager)
        formal = self.fixture.runtime.document["formal_campaigns"][0]
        self.clock.wall_ms = formal["launcher_dispatch_at_ms"]
        self.assertEqual(coordinator.launch_formal_if_due(formal), "RUNNING")
        unit = coordinator._launcher_unit(FORMAL)
        launcher_argv = manager.starts[-1][1]
        frozen_start = int(launcher_argv[
            launcher_argv.index("--formal-start-ms") + 1])
        self.assertEqual(frozen_start, formal["launcher_start_ms"])
        self.assertNotEqual(frozen_start, formal["valid_after_ms"])
        self.assertEqual(
            formal["valid_after_ms"] - frozen_start,
            C.LAUNCHER_WARMUP_MS)  # probe dispatch lead stays separate
        # Simulate coordinator death while the separate transient launcher
        # remains active.  The new process supervises that exact unit.
        resumed = self.fixture.coordinator(manager)
        self.assertEqual(resumed.launch_formal_if_due(formal), "RUNNING")
        _publish(C, self.fixture.formal_receipt, {
            "schema": "fake.launcher.v1", **C.boundary(),
        })
        _publish(C, self.fixture.closure, {
            "schema": "fake.closure.v1", **C.boundary(),
        })
        manager.complete(unit)
        self.assertEqual(resumed.launch_formal_if_due(formal), "COMPLETE")

        # A separate disposable campaign whose slot is already late records a
        # MISSED terminal fact and performs no launcher start.
        late_dir = self.base / "late"
        _mkdir(late_dir)
        late_clock = Clock(self.clock.wall_ms + 10_000)
        late_fixture = Fixture(late_dir, late_clock)
        late_formal = late_fixture.runtime.document["formal_campaigns"][0]
        late_clock.wall_ms = (
            late_formal["launcher_start_ms"] -
            C.LAUNCHER_MINIMUM_EXEC_MARGIN_MS + 1)
        late_manager = FakeSystemd(late_dir, late_clock)
        late = late_fixture.coordinator(late_manager)
        with self.assertRaisesRegex(
                C.CoordinatorError, "P1_COORDINATOR_LAUNCH_SLOT_MISSED"):
            late.launch_formal_if_due(
                late_formal)
        self.assertEqual(late_manager.starts, [])
        entry = late.journal.entries[-1].document
        self.assertEqual(entry["status"], "MISSED")
        self.assertFalse(entry["details"]["catch_up"])

    def test_early_dispatch_window_precedes_process_now_boundary(self) -> None:
        manager = FakeSystemd(self.base, self.clock)
        coordinator = self.fixture.coordinator(manager)
        formal = self.fixture.runtime.document["formal_campaigns"][0]
        dispatch = (formal["launcher_start_ms"] -
                    C.LAUNCHER_EARLY_START_LEAD_MS)
        self.clock.wall_ms = dispatch - 1
        self.assertEqual(coordinator.launch_formal_if_due(formal), "WAITING")
        self.assertEqual(manager.starts, [])
        self.clock.wall_ms = dispatch
        self.assertEqual(coordinator.launch_formal_if_due(formal), "RUNNING")
        configuration = L.LaunchConfiguration(
            probe_campaign_id=PROBE, formal_campaign_id=FORMAL,
            formal_start_ms=formal["launcher_start_ms"])
        configuration.validate(self.clock.wall_ms)

        late_dir = self.base / "late-dispatch"
        _mkdir(late_dir)
        late_clock = Clock()
        late_fixture = Fixture(late_dir, late_clock)
        late_formal = late_fixture.runtime.document["formal_campaigns"][0]
        late_clock.wall_ms = (
            late_formal["launcher_start_ms"] -
            C.LAUNCHER_MINIMUM_EXEC_MARGIN_MS + 1)
        late_manager = FakeSystemd(late_dir, late_clock)
        with self.assertRaisesRegex(
                C.CoordinatorError, "LAUNCH_SLOT_MISSED"):
            late_fixture.coordinator(late_manager).launch_formal_if_due(
                late_formal)
        self.assertEqual(late_manager.starts, [])

    def test_frozen_three_stage_deadlines_allow_late_closure_but_are_bounded(
            self) -> None:
        manager = FakeSystemd(self.base, self.clock)
        coordinator = self.fixture.coordinator(manager)
        formal = self.fixture.runtime.document["formal_campaigns"][0]
        self.clock.wall_ms = formal["expires_at_ms"] + 1
        coordinator.assert_formal_deadline(formal, "LAUNCHER")
        coordinator.assert_formal_deadline(formal, "PROJECTION")
        coordinator.assert_formal_deadline(formal, "CLEANUP")
        self.clock.wall_ms = formal["projection_deadline_ms"]
        coordinator.assert_formal_deadline(formal, "PROJECTION")
        self.clock.advance(1)
        with self.assertRaisesRegex(C.CoordinatorError,
                                    "PROJECTION_DEADLINE"):
            coordinator.assert_formal_deadline(formal, "PROJECTION")
        self.clock.wall_ms = formal["teardown_deadline_ms"]
        coordinator.assert_formal_deadline(formal, "CLEANUP")
        self.clock.advance(1)
        with self.assertRaisesRegex(C.CoordinatorError,
                                    "LAUNCHER_CLEANUP_DEADLINE"):
            coordinator.assert_formal_deadline(formal, "CLEANUP")

    def test_unexpected_exception_is_durable_and_stops_every_owned_unit(self) \
            -> None:
        manager = FakeSystemd(self.base, self.clock)
        coordinator = self.fixture.coordinator(manager)
        argv = [C.OBSERVER_WORKER, "--run", "--runtime-manifest",
                str(self.fixture.runtime.path),
                "--expected-runtime-manifest-file-sha256",
                self.fixture.runtime.file_sha256]
        coordinator.ensure_worker("observer", argv)
        reason = C.fail_closed_after_unexpected(
            coordinator, RuntimeError("adapter splice"))
        self.assertEqual(reason, "P1_COORDINATOR_UNEXPECTED_RUNTIMEERROR")
        terminal = coordinator.journal.entries[-1].document
        self.assertEqual(terminal["status"], "FAILED_CLOSED")
        self.assertEqual(terminal["details"]["reason"], reason)
        self.assertIsNone(terminal["details"]["cleanup_error"])
        self.assertTrue(all(
            manager.show_unit(unit)["ActiveState"] == "inactive"
            for unit in coordinator.owned_units()))
        self.assertFalse((self.fixture.state / "terminal-receipt.json").exists())

    def test_failure_intent_crash_recovers_before_any_transition(self) -> None:
        manager = FakeSystemd(self.base, self.clock)
        coordinator = self.fixture.coordinator(
            manager, "CAMPAIGN_FAILURE:INTENT")
        argv = [
            C.OBSERVER_WORKER, "--run", "--runtime-manifest",
            str(self.fixture.runtime.path),
            "--expected-runtime-manifest-file-sha256",
            self.fixture.runtime.file_sha256,
        ]
        coordinator.ensure_worker("observer", argv)
        reason = "P1_COORDINATOR_UNEXPECTED_RUNTIMEERROR"
        with self.assertRaises(C.InjectedCrash):
            C.fail_closed_after_unexpected(
                coordinator, RuntimeError("adapter splice"))
        self.assertFalse(coordinator.journal.failed)
        intent = coordinator.journal.entries[-1].document
        self.assertEqual(
            (intent["event"], intent["status"]),
            ("CAMPAIGN_FAILURE", "INTENT"))
        self.assertEqual(intent["details"]["reason"], reason)

        restarted = self.fixture.coordinator(manager)
        self.assertEqual(restarted.recover_failure_intent(), reason)
        self.assertTrue(restarted.journal.failed)
        self.assertTrue(all(
            manager.show_unit(unit)["ActiveState"] == "inactive"
            for unit in restarted.owned_units()))
        self.assertEqual(restarted.recover_failure_intent(), reason)
        with self.assertRaisesRegex(
                C.CoordinatorError, "ALREADY_FAILED_CLOSED"):
            restarted.journal.append("AFTER_FAILURE", "COMMITTED", {})

    def test_production_restart_recovers_failure_before_freezer_or_append(
            self) -> None:
        manager = FakeSystemd(self.base, self.clock)
        crashed = self.fixture.coordinator(
            manager, "CAMPAIGN_FAILURE:INTENT")
        argv = [
            C.OBSERVER_WORKER, "--run", "--runtime-manifest",
            str(self.fixture.runtime.path),
            "--expected-runtime-manifest-file-sha256",
            self.fixture.runtime.file_sha256,
        ]
        crashed.ensure_worker("observer", argv)
        with self.assertRaises(C.InjectedCrash):
            C.fail_closed_after_unexpected(
                crashed, RuntimeError("adapter splice"))

        contract_path = Path(
            "/etc/heptatrader/p1-safety-soak/" + CAMPAIGN + ".json")
        contract = {
            "campaign_id": CAMPAIGN,
            "pin_formal_campaign_id": FORMAL,
            "freeze_bundle": C._reference(self.fixture.bundle),
            "state_root": str(self.fixture.state),
        }
        bundle_document = {
            "campaign_id": CAMPAIGN,
            "boot_id": BOOT,
            "formal_policies": [{"campaign_id": FORMAL}],
            "source_producer_pins": [
                {"role": role, "file_sha256": SHA}
                for role in C.ROLE_PATHS
            ],
        }
        bundle = C.Snapshot(
            path=self.fixture.bundle.path,
            payload=self.fixture.bundle.payload,
            document=bundle_document,
            file_sha256=self.fixture.bundle.file_sha256,
            body_sha256=self.fixture.bundle.body_sha256,
            metadata=self.fixture.bundle.metadata,
        )
        original_secure_read = C._secure_read

        def secure_read(path: Path, **keywords: Any) -> Any:
            if path == contract_path:
                return self.fixture.contract
            return original_secure_read(path, **keywords)

        appended: list[tuple[str, str]] = []
        original_append = C.Journal.append

        def append(
            journal: Any, event: str, status: str,
            details: Mapping[str, Any],
        ) -> Any:
            appended.append((event, status))
            return original_append(journal, event, status, details)

        freezer = mock.Mock(side_effect=AssertionError(
            "recorder freezer ran before failure recovery"))
        contract_validator = mock.Mock(return_value=contract)
        bundle_validator = mock.Mock()
        starts_before = list(manager.starts)
        with mock.patch.object(C, "ROOT_UID", os.getuid()), \
                mock.patch.object(C, "ROOT_GID", os.getgid()), \
                mock.patch.object(C, "_secure_read", side_effect=secure_read), \
                mock.patch.object(
                    C, "validate_launch_contract", contract_validator), \
                mock.patch.object(C, "_open_reference", return_value=bundle), \
                mock.patch.object(
                    C, "validate_freeze_bundle", bundle_validator), \
                mock.patch.object(C, "_read_boot_id", return_value=BOOT), \
                mock.patch.object(C, "_bind_installed_image"), \
                mock.patch.object(C, "_ensure_layout"), \
                mock.patch.object(
                    C, "_open_runtime_for_terminal_recovery",
                    return_value=self.fixture.runtime), \
                mock.patch.object(C, "ProductionAdapter", return_value=manager), \
                mock.patch.object(C, "_run_recorder_freeze", freezer), \
                mock.patch.object(C.Journal, "append", new=append), \
                self.assertRaisesRegex(
                    C.CoordinatorError,
                    "P1_COORDINATOR_UNEXPECTED_RUNTIMEERROR"):
            C.run_production(contract_path)

        freezer.assert_not_called()
        self.assertEqual(contract_validator.call_count, 1)
        self.assertIs(
            contract_validator.call_args.kwargs.get("require_current"), False)
        self.assertEqual(bundle_validator.call_count, 1)
        self.assertIs(
            bundle_validator.call_args.kwargs.get("require_current"), False)
        self.assertEqual(manager.starts, starts_before)
        self.assertEqual(appended[0], ("CLOSE_OWNED_UNITS", "INTENT"))
        self.assertNotIn(("RECORDER_FREEZE", "INTENT"), appended)
        self.assertEqual(appended[-1], ("CAMPAIGN", "FAILED_CLOSED"))
        self.assertTrue(all(
            manager.show_unit(unit)["ActiveState"] == "inactive"
            for unit in crashed.owned_units()))

    def test_expired_running_restart_fails_closed_before_freezer(self) -> None:
        manager = FakeSystemd(self.base, self.clock)
        coordinator = self.fixture.coordinator(manager)
        argv = [
            C.OBSERVER_WORKER, "--run", "--runtime-manifest",
            str(self.fixture.runtime.path),
            "--expected-runtime-manifest-file-sha256",
            self.fixture.runtime.file_sha256,
        ]
        coordinator.ensure_worker("observer", argv)
        contract_path = Path(
            "/etc/heptatrader/p1-safety-soak/" + CAMPAIGN + ".json")
        contract = {
            "campaign_id": CAMPAIGN,
            "pin_formal_campaign_id": FORMAL,
            "freeze_bundle": C._reference(self.fixture.bundle),
            "state_root": str(self.fixture.state),
        }
        bundle = C.Snapshot(
            path=self.fixture.bundle.path,
            payload=self.fixture.bundle.payload,
            document={
                "campaign_id": CAMPAIGN,
                "boot_id": BOOT,
                "formal_policies": [{"campaign_id": FORMAL}],
                "source_producer_pins": [
                    {"role": role, "file_sha256": SHA}
                    for role in C.ROLE_PATHS
                ],
            },
            file_sha256=self.fixture.bundle.file_sha256,
            body_sha256=self.fixture.bundle.body_sha256,
            metadata=self.fixture.bundle.metadata,
        )
        original_secure_read = C._secure_read

        def secure_read(path: Path, **keywords: Any) -> Any:
            if path == contract_path:
                return self.fixture.contract
            return original_secure_read(path, **keywords)

        contract_modes: list[bool] = []
        bundle_modes: list[bool] = []

        def validate_contract(
            _document: Mapping[str, Any], _now_ms: int, *,
            require_current: bool = True,
        ) -> Mapping[str, Any]:
            contract_modes.append(require_current)
            if require_current:
                raise C.CoordinatorError(
                    "P1_COORDINATOR_LAUNCH_CONTRACT_INVALID")
            return contract

        def validate_bundle(
            _document: Mapping[str, Any], _now_ms: int, *,
            require_current: bool = True,
        ) -> Mapping[str, Any]:
            bundle_modes.append(require_current)
            return bundle.document

        appended: list[tuple[str, str]] = []
        original_append = C.Journal.append

        def append(
            journal: Any, event: str, status: str,
            details: Mapping[str, Any],
        ) -> Any:
            appended.append((event, status))
            return original_append(journal, event, status, details)

        freezer = mock.Mock(side_effect=AssertionError(
            "recorder freezer ran for stale RUNNING recovery"))
        starts_before = list(manager.starts)
        with mock.patch.object(C, "ROOT_UID", os.getuid()), \
                mock.patch.object(C, "ROOT_GID", os.getgid()), \
                mock.patch.object(C, "_secure_read", side_effect=secure_read), \
                mock.patch.object(
                    C, "validate_launch_contract", side_effect=validate_contract), \
                mock.patch.object(C, "_open_reference", return_value=bundle), \
                mock.patch.object(
                    C, "validate_freeze_bundle", side_effect=validate_bundle), \
                mock.patch.object(C, "_read_boot_id", return_value=BOOT), \
                mock.patch.object(C, "_bind_installed_image"), \
                mock.patch.object(C, "_ensure_layout"), \
                mock.patch.object(
                    C, "_open_runtime_for_terminal_recovery",
                    return_value=self.fixture.runtime), \
                mock.patch.object(C, "ProductionAdapter", return_value=manager), \
                mock.patch.object(C, "_run_recorder_freeze", freezer), \
                mock.patch.object(C.Journal, "append", new=append), \
                self.assertRaisesRegex(
                    C.CoordinatorError,
                    "P1_COORDINATOR_LAUNCH_CONTRACT_INVALID"):
            C.run_production(contract_path)

        freezer.assert_not_called()
        self.assertEqual(contract_modes, [False, True])
        self.assertEqual(bundle_modes, [False])
        self.assertEqual(manager.starts, starts_before)
        self.assertEqual(appended[0], ("CAMPAIGN_FAILURE", "INTENT"))
        self.assertEqual(appended[-1], ("CAMPAIGN", "FAILED_CLOSED"))
        self.assertTrue(coordinator.journal.refresh() is None)
        self.assertTrue(coordinator.journal.failed)
        self.assertTrue(all(
            manager.show_unit(unit)["ActiveState"] == "inactive"
            for unit in coordinator.owned_units()))

    def test_all_terminal_signal_handlers_latch_before_cleanup(self) -> None:
        for signum in (C.signal.SIGINT, C.signal.SIGTERM, C.signal.SIGHUP):
            with self.subTest(signum=signum), \
                    mock.patch.object(C.signal, "signal") as install:
                install.return_value = C.signal.SIG_DFL
                previous = C._install_signal_handlers()
                handler = next(
                    call.args[1] for call in install.call_args_list
                    if call.args[0] == signum)
                with self.assertRaises(C.CoordinatorSignal) as raised:
                    handler(signum, None)
                self.assertEqual(raised.exception.signum, signum)
                self.assertIsNone(handler(signum, None))
                self.assertEqual(
                    set(previous), {
                        C.signal.SIGINT, C.signal.SIGTERM, C.signal.SIGHUP})

    def test_cleanup_stop_failure_never_emits_complete_terminal(self) -> None:
        class StopFailure(FakeSystemd):
            def stop_unit(self, unit: str) -> None:
                raise RuntimeError("injected stop failure")

        manager = StopFailure(self.base, self.clock)
        coordinator = self.fixture.coordinator(manager)
        argv = [C.OBSERVER_WORKER, "--run", "--runtime-manifest",
                str(self.fixture.runtime.path),
                "--expected-runtime-manifest-file-sha256",
                self.fixture.runtime.file_sha256]
        coordinator.ensure_worker("observer", argv)
        C.fail_closed_after_unexpected(coordinator, ValueError("boom"))
        terminal = coordinator.journal.entries[-1].document
        self.assertEqual(terminal["status"], "FAILED_CLOSED")
        self.assertEqual(terminal["details"]["cleanup_error"], "RuntimeError")
        self.assertIsNone(
            terminal["details"]["owned_unit_closure_body_sha256"])
        self.assertFalse((self.fixture.state / "terminal-receipt.json").exists())

    def test_terminal_publish_crash_is_recovered_before_any_new_transition(
            self) -> None:
        manager = FakeSystemd(self.base, self.clock)
        coordinator = self.fixture.coordinator(manager)
        decisions = self.fixture.state / "recorder" / "decisions"
        _mkdir(decisions)
        for index in range(C.MINIMUM_ELIGIBLE_DECISIONS):
            _publish(C, decisions / f"{index + 1:08d}.json", {
                "schema": "fake.decision.v1", "sequence": index + 1,
                **C.boundary(),
            })
        audit = self.fixture.state / "final-audit-receipt.json"
        manager.run([C.AUDITOR, "--output", str(audit)], 1)
        closure = coordinator.close_owned_units()
        coordinator.assert_forbidden_units_inert("TERMINAL")

        original_append = coordinator._append

        def crash_before_commit(
            event: str, status_value: str, details: Mapping[str, Any],
        ) -> Any:
            if event == "TERMINAL" and status_value == "COMMITTED":
                raise C.InjectedCrash("post-publish-pre-commit")
            return original_append(event, status_value, details)

        with mock.patch.object(
                coordinator, "_append", side_effect=crash_before_commit), \
                self.assertRaises(C.InjectedCrash):
            coordinator.terminal_receipt(audit, closure)
        output = self.fixture.state / "terminal-receipt.json"
        self.assertTrue(output.exists())
        self.assertEqual(
            coordinator.journal.entries[-1].document["status"], "INTENT")

        restarted = self.fixture.coordinator(manager)
        self.assertEqual(restarted.recover_terminal_receipt(), output)
        self.assertTrue(restarted.journal.complete)
        self.assertEqual(restarted.recover_terminal_receipt(), output)
        with self.assertRaisesRegex(
                C.CoordinatorError, "ALREADY_FAILED_CLOSED"):
            restarted.journal.append("AFTER_TERMINAL", "COMMITTED", {})

    def test_effective_worker_capability_and_address_family_drift_rejected(
            self) -> None:
        manager = FakeSystemd(self.base, self.clock)
        coordinator = self.fixture.coordinator(manager)
        argv = [
            C.OBSERVER_WORKER, "--run", "--runtime-manifest",
            str(self.fixture.runtime.path),
            "--expected-runtime-manifest-file-sha256",
            self.fixture.runtime.file_sha256,
        ]
        coordinator.ensure_worker("observer", argv)
        unit = coordinator._unit_names()["observer"]
        original_caps = manager.units[unit]["CapabilityBoundingSet"]
        original_families = manager.units[unit]["RestrictAddressFamilies"]
        manager.units[unit]["CapabilityBoundingSet"] = (
            original_caps + " cap_net_raw")
        with self.assertRaisesRegex(
                C.CoordinatorError, "TRANSIENT_HARDENING_DRIFT"):
            coordinator.assert_workers_healthy()
        manager.units[unit]["CapabilityBoundingSet"] = original_caps
        manager.units[unit]["RestrictAddressFamilies"] = (
            original_families + " AF_INET")
        with self.assertRaisesRegex(
                C.CoordinatorError, "TRANSIENT_HARDENING_DRIFT"):
            coordinator.assert_workers_healthy()

    def test_final_audit_no_go_and_halt_are_rejected_even_when_resealed(self) \
            -> None:
        manager = FakeSystemd(self.base, self.clock)
        valid_path = self.base / "valid-audit.json"
        manager.run([C.AUDITOR, "--output", str(valid_path)], 1)
        valid = C._secure_read(
            valid_path, expected_uid=os.getuid(), expected_gid=os.getgid(),
            modes=frozenset({0o600}))
        spec = C._open_reference(
            self.fixture.runtime.document["campaign_spec"],
            expected_uid=os.getuid(), expected_gid=os.getgid(), reason="TEST")
        C.validate_final_audit(
            valid, self.fixture.runtime, self.fixture.bundle, spec)
        for verdict in ("NO_GO", "HALT"):
            body = copy.deepcopy(valid.document)
            body.pop("body_sha256")
            body["verdict"] = verdict
            forged = _publish(C, self.base / f"{verdict.lower()}.json",
                              body)
            with self.assertRaisesRegex(
                    C.CoordinatorError, "FINAL_AUDIT_INVALID"):
                C.validate_final_audit(
                    forged, self.fixture.runtime,
                    self.fixture.bundle, spec)

    def test_final_audit_accepts_199_of_200_but_rejects_exactly_99_percent(
            self) -> None:
        manager = FakeSystemd(self.base, self.clock)
        valid_path = self.base / "valid-completeness-audit.json"
        manager.run([C.AUDITOR, "--output", str(valid_path)], 1)
        valid = C._secure_read(
            valid_path, expected_uid=os.getuid(), expected_gid=os.getgid(),
            modes=frozenset({0o600}))
        spec = C._open_reference(
            self.fixture.runtime.document["campaign_spec"],
            expected_uid=os.getuid(), expected_gid=os.getgid(), reason="TEST")

        accepted_body = copy.deepcopy(valid.document)
        accepted_body.pop("body_sha256")
        accepted_body["counts"]["complete_eligible_decisions"] = 199
        accepted_body["counts"]["incomplete_eligible_decisions"] = 1
        accepted_body["completeness"] = {
            "numerator": 199, "denominator": 200, "ppm": 995_000,
            "strictly_greater_than_99_percent": True,
        }
        accepted = _publish(
            C, self.base / "accepted-199-of-200.json", accepted_body)
        C.validate_final_audit(
            accepted, self.fixture.runtime, self.fixture.bundle, spec)

        rejected_body = copy.deepcopy(accepted.document)
        rejected_body.pop("body_sha256")
        rejected_body["counts"]["complete_eligible_decisions"] = 198
        rejected_body["counts"]["incomplete_eligible_decisions"] = 2
        rejected_body["completeness"] = {
            "numerator": 198, "denominator": 200, "ppm": 990_000,
            "strictly_greater_than_99_percent": False,
        }
        rejected = _publish(
            C, self.base / "rejected-198-of-200.json", rejected_body)
        with self.assertRaisesRegex(
                C.CoordinatorError, "FINAL_AUDIT_INVALID"):
            C.validate_final_audit(
                rejected, self.fixture.runtime, self.fixture.bundle, spec)

        wrong_eligible_body = copy.deepcopy(valid.document)
        wrong_eligible_body.pop("body_sha256")
        wrong_eligible_body["counts"].update({
            "eligible_decisions": 201,
            "complete_eligible_decisions": 201,
            "incomplete_eligible_decisions": 0,
        })
        wrong_eligible_body["completeness"] = {
            "numerator": 201, "denominator": 201, "ppm": 1_000_000,
            "strictly_greater_than_99_percent": True,
        }
        wrong_eligible = _publish(
            C, self.base / "rejected-eligible-count-drift.json",
            wrong_eligible_body)
        with self.assertRaisesRegex(
                C.CoordinatorError, "FINAL_AUDIT_INVALID"):
            C.validate_final_audit(
                wrong_eligible, self.fixture.runtime,
                self.fixture.bundle, spec)

    def test_final_audit_schema_and_local_exposure_match_real_auditor(
            self) -> None:
        self.assertEqual(C.AUDIT_RECEIPT_FIELDS, A.AUDIT_RECEIPT_FIELDS)
        self.assertEqual(
            C.AUDIT_INTERVAL_FIELDS, A.EVALUATED_INTERVAL_FIELDS)
        self.assertEqual(C.AUDIT_COUNTS_FIELDS, A.COUNTS_FIELDS)
        self.assertEqual(
            C.AUDIT_COMPLETENESS_FIELDS, A.COMPLETENESS_FIELDS)
        self.assertEqual(C.AUDIT_EXPOSURE_FIELDS, A.EXPOSURE_SUMMARY_FIELDS)
        self.assertEqual(C.AUDIT_CLEANUP_FIELDS, A.CLEANUP_STATUS_FIELDS)

        manager = FakeSystemd(self.base, self.clock)
        valid_path = self.base / "valid-real-schema-audit.json"
        manager.run([C.AUDITOR, "--output", str(valid_path)], 1)
        valid = C._secure_read(
            valid_path, expected_uid=os.getuid(), expected_gid=os.getgid(),
            modes=frozenset({0o600}))
        A.validate_audit_receipt(valid.document)
        spec = C._open_reference(
            self.fixture.runtime.document["campaign_spec"],
            expected_uid=os.getuid(), expected_gid=os.getgid(), reason="TEST")
        C.validate_final_audit(
            valid, self.fixture.runtime, self.fixture.bundle, spec)

        unsafe_values = {
            "evidence_present": False,
            "maximum_connector_count": 1,
            "maximum_authorized_uid_count": 1,
            "maximum_paper_unit_active_count": 1,
            "campaign_socket_ever_present": True,
            "kill_switch_continuously_engaged": False,
            "local_boundary_uncertain": True,
            "scope": "AUTHORITATIVE_ACCOUNT_STATE",
            "authoritative_account_state_observed": True,
        }
        for field, unsafe in unsafe_values.items():
            body = copy.deepcopy(valid.document)
            body.pop("body_sha256")
            body["exposure_summary"][field] = unsafe
            forged = _publish(
                C, self.base / f"unsafe-exposure-{field}.json", body)
            with self.subTest(field=field), self.assertRaisesRegex(
                    C.CoordinatorError, "FINAL_AUDIT_INVALID"):
                C.validate_final_audit(
                    forged, self.fixture.runtime, self.fixture.bundle, spec)

    def test_final_audit_continuity_anchors_are_runtime_derived(self) -> None:
        manager = FakeSystemd(self.base, self.clock)
        valid_path = self.base / "valid-anchor-audit.json"
        manager.run([C.AUDITOR, "--output", str(valid_path)], 1)
        valid = C._secure_read(
            valid_path, expected_uid=os.getuid(), expected_gid=os.getgid(),
            modes=frozenset({0o600}))
        spec = C._open_reference(
            self.fixture.runtime.document["campaign_spec"],
            expected_uid=os.getuid(), expected_gid=os.getgid(), reason="TEST")
        for field in (
                "continuity_origin_ms", "continuity_end_ms",
                "continuity_final_slot"):
            body = copy.deepcopy(valid.document)
            body.pop("body_sha256")
            body["evaluated_interval"][field] += 1
            forged = _publish(
                C, self.base / f"anchor-drift-{field}.json", body)
            with self.subTest(field=field), self.assertRaisesRegex(
                    C.CoordinatorError, "FINAL_AUDIT_INVALID"):
                C.validate_final_audit(
                    forged, self.fixture.runtime, self.fixture.bundle, spec)

    def test_enabled_or_active_forbidden_surface_fails_preflight(self) -> None:
        forbidden = C.FORBIDDEN_EXECUTION_UNITS[0]
        for enabled, active, substate, pid in (
            ("enabled", "inactive", "dead", "0"),
            ("disabled", "active", "running", "9001"),
        ):
            manager = FakeSystemd(self.base, self.clock)
            value = manager.missing()
            value.update({
                "LoadState": "loaded", "UnitFileState": enabled,
                "ActiveState": active, "SubState": substate,
                "MainPID": pid,
            })
            manager.units[forbidden] = value
            with self.subTest(enabled=enabled, active=active), \
                    self.assertRaisesRegex(
                        C.CoordinatorError, "UNIT_NOT_INERT"):
                self.fixture.coordinator(manager).assert_forbidden_units_inert(
                    "PREFLIGHT")

    def test_disposable_fake_systemd_campaign_liveness(self) -> None:
        manager = FakeSystemd(self.base, self.clock)
        coordinator = self.fixture.coordinator(manager)
        observer_argv = [C.OBSERVER_WORKER, "--run", "--runtime-manifest",
                         str(self.fixture.runtime.path),
                         "--expected-runtime-manifest-file-sha256",
                         self.fixture.runtime.file_sha256]
        recorder_argv = [C.RECORDER_WORKER, "--run", "--runtime-manifest",
                         str(self.fixture.runtime.path),
                         "--expected-runtime-manifest-file-sha256",
                         self.fixture.runtime.file_sha256]
        coordinator.ensure_worker("observer", observer_argv)
        coordinator.ensure_worker("recorder", recorder_argv)
        observer_json = self.fixture.state / "observer-exec-argv.json"
        recorder_json = self.fixture.state / "recorder-exec-argv.json"
        C.publish_json_array(
            observer_json, observer_argv, expected_uid=os.getuid(),
            expected_gid=os.getgid())
        C.publish_json_array(
            recorder_json, recorder_argv, expected_uid=os.getuid(),
            expected_gid=os.getgid())
        pins = coordinator.produce_pins(observer_json, recorder_json)
        coordinator.ensure_injector(pins)

        formal = self.fixture.runtime.document["formal_campaigns"][0]
        self.clock.wall_ms = formal["launcher_dispatch_at_ms"]
        self.assertEqual(coordinator.launch_formal_if_due(formal), "RUNNING")
        _publish(C, self.fixture.formal_receipt, {
            "schema": "fake.launcher.v1", **C.boundary(),
        })
        _publish(C, self.fixture.closure, {
            "schema": "fake.closure.v1", **C.boundary(),
        })
        manager.complete(coordinator._launcher_unit(FORMAL))
        self.assertEqual(coordinator.launch_formal_if_due(formal), "COMPLETE")
        projection = coordinator.request_projection(formal)
        _publish_ack(C, coordinator.control, "recorder", projection,
                     [{"path": "projected"}], self.clock.wall_ms)
        self.assertTrue(coordinator.projection_complete(formal))

        # Seven immutable fake receipts stand in for the injector's bounded
        # fault windows; the injector unit itself ran exactly once.
        for index in range(7):
            _publish(C, self.fixture.state / "injection-receipts" /
                     f"{index:08d}.json", {
                "schema": "fake.injection.v1", "index": index,
                **C.boundary(),
            })
        manager.complete(coordinator._unit_names()["injector"])
        self.assertTrue(coordinator.injector_complete())

        cleanup_request_id = coordinator.request_final_cleanup()
        request = coordinator.control._load_chain(
            "observer", "requests")[-1].document
        raw_cleanup = _publish(
            C, self.fixture.state / "raw-observations" / "cleanup" /
            "final.json", {
                "schema": O.CLEANUP_SCHEMA, "campaign_id": CAMPAIGN,
                "production_mode": "PRODUCTION_ROOT_OBSERVER",
                **C.boundary(),
            })
        _publish_ack(C, coordinator.control, "observer", request,
                     [C._reference(raw_cleanup)], self.clock.wall_ms)
        self.assertFalse(coordinator.final_cleanup_complete(cleanup_request_id))
        drain = coordinator.control._load_chain(
            "recorder", "requests")[-1].document
        _publish_ack(C, coordinator.control, "recorder", drain,
                     [{"path": "cleanup-projected"}], self.clock.wall_ms)
        self.assertTrue(coordinator.final_cleanup_complete(cleanup_request_id))

        # Materialize the actual minimum, not a lowered test threshold.
        decisions = self.fixture.state / "recorder" / "decisions"
        _mkdir(decisions)
        for index in range(C.MINIMUM_ELIGIBLE_DECISIONS):
            _publish(C, decisions / f"{index + 1:08d}.json", {
                "schema": "fake.decision.v1", "sequence": index + 1,
                **C.boundary(),
            })
        audit = coordinator.run_audit()
        audit_argv = next(
            values for values in reversed(manager.calls)
            if values and values[0] == C.AUDITOR)
        self.assertEqual(
            audit_argv[audit_argv.index("--campaign-runtime") + 1],
            str(self.fixture.runtime.path))
        closure = coordinator.close_owned_units()
        coordinator.assert_forbidden_units_inert("TERMINAL")
        terminal = coordinator.terminal_receipt(audit, closure)
        value = C._secure_read(
            terminal, expected_uid=os.getuid(), expected_gid=os.getgid(),
            modes=frozenset({0o600})).document
        self.assertEqual(value["eligible_decision_receipt_count"], 200)
        self.assertEqual(value["fault_receipt_count"], 7)
        self.assertFalse(value["paper_handoff_authorized"])
        injector_starts = [item for item in manager.starts
                           if item[0] == coordinator._unit_names()["injector"]]
        self.assertEqual(len(injector_starts), 1)
        owner = coordinator._coordinator_unit()
        for unit, properties in manager.properties.items():
            with self.subTest(transient_unit=unit):
                self.assertIn(f"PartOf={owner}", properties)
                self.assertIn(f"BindsTo={owner}", properties)
                self.assertIn(f"After={owner}", properties)
                self.assertIn("NoNewPrivileges=yes", properties)
                self.assertIn("PrivateDevices=yes", properties)
                self.assertIn("RestrictNamespaces=yes", properties)
                self.assertIn("MemoryDenyWriteExecute=yes", properties)
                self.assertIn("AmbientCapabilities=", properties)
                self.assertIn("ProtectSystem=strict", properties)
                self.assertIn("ProtectClock=yes", properties)
                self.assertIn("ProtectKernelLogs=yes", properties)
                self.assertIn("IPAddressDeny=any", properties)
        units = coordinator._unit_names()
        self.assertIn(
            "RestrictAddressFamilies=AF_UNIX AF_NETLINK",
            manager.properties[units["observer"]])
        self.assertIn(
            "RestrictAddressFamilies=AF_UNIX",
            manager.properties[units["recorder"]])
        for broker_helper_caller in (
            units["observer"], units["injector"],
            coordinator._launcher_unit(FORMAL),
        ):
            properties = manager.properties[broker_helper_caller]
            self.assertIn(
                "RestrictAddressFamilies=AF_UNIX AF_NETLINK", properties)
            capability = next(
                item for item in properties
                if item.startswith("CapabilityBoundingSet="))
            self.assertIn("CAP_NET_ADMIN", capability)
            self.assertNotIn("AF_INET", " ".join(properties))
        events = [item.document["event"] for item in coordinator.journal.entries]
        self.assertLess(events.index("RUN_FINAL_AUDIT"),
                        events.index("CLOSE_OWNED_UNITS"))
        self.assertLess(events.index("CLOSE_OWNED_UNITS"),
                        events.index("ASSERT_EXECUTION_BOUNDARY_TERMINAL"))
        self.assertLess(events.index("ASSERT_EXECUTION_BOUNDARY_TERMINAL"),
                        events.index("TERMINAL"))
        self.assertTrue(all(
            manager.show_unit(unit)["ActiveState"] == "inactive"
            for unit in coordinator.owned_units()))

    def test_journal_tamper_is_rejected(self) -> None:
        manager = FakeSystemd(self.base, self.clock)
        coordinator = self.fixture.coordinator(manager)
        coordinator.journal.append("TEST", "COMMITTED", {"safe": True})
        path = self.fixture.state / "coordinator-journal" / "00000000.json"
        descriptor = os.open(path, os.O_WRONLY | os.O_TRUNC)
        try:
            os.write(descriptor, b'{"tampered":true}\n')
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
        with self.assertRaises(C.CoordinatorError):
            self.fixture.coordinator(manager)


class ObserverWorkerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.base = Path(self.temporary.name)
        os.chmod(self.base, 0o700)
        self.clock = Clock()
        self.fixture = Fixture(self.base, self.clock)
        self.runner = FakeObserverRunner(O, CAMPAIGN)
        self.patch = mock.patch.object(
            O.C, "_secure_executable", return_value=b"pinned")
        self.secure_executable = self.patch.start()

    def tearDown(self) -> None:
        self.patch.stop()
        self.temporary.cleanup()

    def worker(self) -> Any:
        return O.ObserverWorker(
            self.fixture.runtime, self.runner,
            expected_uid=os.getuid(), expected_gid=os.getgid(),
            wall_clock=self.clock.wall, boot_clock=self.clock.boot)

    def test_missed_slots_are_one_range_and_current_slot_only(self) -> None:
        runtime = self.fixture.runtime.document
        origin = runtime["formal_campaigns"][0]["valid_after_ms"]
        self.clock.wall_ms = origin + 5 * runtime["observer_cadence_ms"]
        worker = self.worker()
        worker.sample_once()
        values = [item.document for item in worker.journal.entries]
        missed = [item for item in values if item["status"] == "MISSED"]
        self.assertEqual(len(missed), 1)
        self.assertEqual(
            (missed[0]["details"]["first_slot"],
             missed[0]["details"]["last_slot"]), (0, 4))
        self.assertFalse(missed[0]["details"]["catch_up"])
        self.assertEqual(len(self.runner.calls), 2)  # service + authority only

    def test_runtime_failure_is_durably_terminal(self) -> None:
        worker = self.worker()
        self.clock.wall_ms = self.fixture.runtime.document[
            "formal_campaigns"][0]["launcher_dispatch_at_ms"]
        with mock.patch.object(
                worker, "sample_once",
                side_effect=O.WorkerError("TEST_OBSERVER_FAILURE")):
            with self.assertRaisesRegex(O.WorkerError, "TEST_OBSERVER_FAILURE"):
                worker.run_forever()
        self.assertTrue(worker.journal.failed)
        terminal = worker.journal.entries[-1].document
        self.assertEqual(
            terminal["details"]["reason"], "TEST_OBSERVER_FAILURE")
        self.assertFalse(terminal["details"]["catch_up"])

    def test_campaign_continuity_baseline_uses_nonformal_observer(self) -> None:
        worker = self.worker()
        formal = self.fixture.runtime.document["formal_campaigns"][0]
        self.clock.wall_ms = formal["launcher_dispatch_at_ms"]
        self.assertLess(self.clock.wall_ms, formal["valid_after_ms"])
        worker.sample_campaign_continuity_once()
        self.assertEqual(len(self.runner.calls), 1)
        argv = self.runner.calls[0]
        self.assertEqual(argv[:3], [O.OBSERVER, "--run",
                                   "campaign-continuity"])
        self.assertNotIn("--formal-campaign-id", argv)
        self.assertEqual(
            Path(argv[argv.index("--campaign-runtime") + 1]),
            self.fixture.runtime.path)
        self.assertEqual(
            argv[argv.index("--continuity-slot-index") + 1], "0")
        entry = worker.journal.entries[-1].document
        self.assertEqual(entry["status"], "COMMITTED")
        self.assertEqual(entry["details"]["scheduled_at_ms"],
                         formal["launcher_dispatch_at_ms"])
        self.assertFalse(entry["details"]["catch_up"])
        broker_checks = [
            call for call in self.secure_executable.call_args_list
            if call.args[0] == Path(O.BROKER_EGRESS_POLICY) and
            call.args[1] == SHA
        ]
        self.assertEqual(len(broker_checks), 2)  # before and after helper

    def test_observer_helper_surface_rejects_argv_and_broker_pin_widening(
            self) -> None:
        worker = self.worker()
        output = self.fixture.state / "raw-observations" / "continuity" / \
            "00000000.json"
        safe = [
            O.OBSERVER, "--run", "campaign-continuity",
            "--campaign-spec", self.fixture.runtime.document[
                "campaign_spec"]["path"],
            "--campaign-runtime", str(self.fixture.runtime.path),
            "--continuity-slot-index", "0", "--output", str(output),
        ]
        worker._validate_helper_argv(
            safe, output, O.CAMPAIGN_CONTINUITY_SCHEMA)
        for widened in (
            safe + ["--broker-address", "127.0.0.1:4002"],
            [*safe, "--continuity-slot-index", "0"],
            [*safe[:2], "authority", *safe[3:]],
        ):
            with self.subTest(argv=widened), self.assertRaisesRegex(
                    O.WorkerError, "HELPER_ARGV_INVALID"):
                worker._validate_helper_argv(
                    widened, output, O.CAMPAIGN_CONTINUITY_SCHEMA)
        worker.broker_pin = {
            "path": "/usr/libexec/unpinned-broker-helper",
            "file_sha256": SHA,
        }
        with self.assertRaisesRegex(
                O.WorkerError, "BROKER_HELPER_ARGV_DRIFT"):
            worker._validate_helper_argv(
                safe, output, O.CAMPAIGN_CONTINUITY_SCHEMA)

    def test_campaign_continuity_gap_fails_closed_without_catch_up(self) -> None:
        worker = self.worker()
        runtime = self.fixture.runtime.document
        origin = runtime["formal_campaigns"][0]["launcher_dispatch_at_ms"]
        self.clock.wall_ms = origin + 5 * runtime["observer_cadence_ms"]
        with self.assertRaisesRegex(
                O.WorkerError, "CAMPAIGN_CONTINUITY_MISSED"):
            worker.sample_campaign_continuity_once()
        self.assertEqual(self.runner.calls, [])
        self.assertTrue(worker.journal.failed)
        missed = worker.journal.entries[-2].document
        self.assertEqual(missed["status"], "MISSED")
        self.assertEqual(
            (missed["details"]["first_slot"],
             missed["details"]["last_slot"]), (0, 4))
        self.assertFalse(missed["details"]["catch_up"])
        self.assertEqual(worker.journal.entries[-1].document["status"],
                         "FAILED_CLOSED")

    def test_campaign_continuity_has_distinct_exact_teardown_anchor(self) -> None:
        worker = self.worker()
        origin, end, cadence = worker._continuity_bounds()
        final_slot = worker._continuity_final_slot()
        self.assertEqual(worker._continuity_scheduled_at(0), origin)
        self.assertEqual(worker._continuity_scheduled_at(final_slot), end)
        if (end - origin) % cadence:
            self.assertLess(
                worker._continuity_scheduled_at(final_slot - 1), end)

    def test_non_aligned_final_slot_invokes_exact_teardown_index(self) -> None:
        document = copy.deepcopy(self.fixture.runtime.document)
        document.pop("body_sha256")
        formal = document["formal_campaigns"][0]
        origin = formal["launcher_dispatch_at_ms"]
        end = formal["teardown_deadline_ms"]
        document["observer_cadence_ms"] = (end - origin) // 2 + 1
        runtime = O.C.publish_noreplace(
            self.fixture.state / "runtime-manifest-final-test.json",
            O.C.seal(document), expected_uid=os.getuid(),
            expected_gid=os.getgid())
        worker = O.ObserverWorker(
            runtime, self.runner, expected_uid=os.getuid(),
            expected_gid=os.getgid(), wall_clock=self.clock.wall,
            boot_clock=self.clock.boot)
        self.assertEqual(worker._continuity_final_slot(), 2)
        for slot in range(3):
            self.clock.wall_ms = worker._continuity_scheduled_at(slot)
            worker.sample_campaign_continuity_once()
        argv = self.runner.calls[-1]
        self.assertEqual(
            argv[argv.index("--continuity-slot-index") + 1], "2")
        self.assertEqual(
            Path(argv[argv.index("--campaign-runtime") + 1]), runtime.path)
        final = worker.journal.entries[-1].document["details"]
        self.assertEqual(final["scheduled_at_ms"], end)
        self.assertEqual(final["final_slot"], 2)

    def test_gateway_restart_transition_is_consumed_per_stream(self) -> None:
        worker = self.worker()
        origin = self.fixture.runtime.document[
            "formal_campaigns"][0]["launcher_dispatch_at_ms"]
        self.clock.wall_ms = origin
        worker.sample_campaign_continuity_once()
        fault_id = "fault-service-restart"
        _publish(O.C, self.fixture.state / "injection-receipts" /
                 "00000000.json", {
            "schema": O.INJECTION_SCHEMA, "version": 1,
            "status": "COMPLETE", "campaign_id": CAMPAIGN,
            "fault_id": fault_id, "fault_type": "SERVICE_RESTART",
            **O.C.boundary(),
        })
        worker.observe_faults()
        self.clock.advance(
            self.fixture.runtime.document["observer_cadence_ms"])
        worker.sample_campaign_continuity_once()
        first = self.runner.calls[-1]
        self.assertEqual(
            first[first.index("--transition-fault-id") + 1], fault_id)
        self.clock.advance(self.fixture.runtime.document["observer_cadence_ms"])
        worker.sample_campaign_continuity_once()
        self.assertNotIn("--transition-fault-id", self.runner.calls[-1])

    def test_restart_fault_is_consumed_once_by_each_independent_stream(self) \
            -> None:
        fault_id = "fault-both-streams"
        _publish(O.C, self.fixture.state / "injection-receipts" /
                 "00000000.json", {
            "schema": O.INJECTION_SCHEMA, "version": 1,
            "status": "COMPLETE", "campaign_id": CAMPAIGN,
            "fault_id": fault_id, "fault_type": "SERVICE_RESTART",
            **O.C.boundary(),
        })
        worker = self.worker()
        worker.observe_faults()
        formal = self.fixture.runtime.document["formal_campaigns"][0]
        self.clock.wall_ms = formal["launcher_dispatch_at_ms"]
        worker.sample_campaign_continuity_once()
        continuity_argv = self.runner.calls[-1]
        continuity_count = len(self.runner.calls)
        worker.sample_campaign_continuity_once()
        self.assertEqual(len(self.runner.calls), continuity_count)
        self.clock.wall_ms = formal["valid_after_ms"]
        worker.sample_once()
        service_argv = next(
            argv for argv in reversed(self.runner.calls)
            if len(argv) > 2 and argv[2] == "service")
        for argv in (continuity_argv, service_argv):
            self.assertEqual(
                argv[argv.index("--transition-fault-id") + 1], fault_id)
        prior_count = len(self.runner.calls)
        worker.sample_once()
        self.assertEqual(len(self.runner.calls), prior_count)

    def test_restart_preserves_intent_selected_transition(self) -> None:
        first_fault = "fault-selected-before-crash"
        second_fault = "fault-arrived-after-crash"
        worker = self.worker()
        for index, fault_id in enumerate((first_fault, second_fault)):
            if index == 1:
                break
            _publish(O.C, self.fixture.state / "injection-receipts" /
                     f"{index:08d}.json", {
                "schema": O.INJECTION_SCHEMA, "version": 1,
                "status": "COMPLETE", "campaign_id": CAMPAIGN,
                "fault_id": fault_id, "fault_type": "SERVICE_RESTART",
                **O.C.boundary(),
            })
        worker.observe_faults()
        origin, end, cadence = worker._continuity_bounds()
        final_slot = worker._continuity_final_slot()
        worker._event("CAMPAIGN_CONTINUITY_SLOT", "INTENT", {
            "first_slot": 0, "last_slot": 0,
            "scheduled_at_ms": origin, "origin_ms": origin, "end_ms": end,
            "cadence_ms": cadence,
            "maximum_slot_lateness_ms":
                self.fixture.runtime.document["maximum_slot_lateness_ms"],
            "final_slot": final_slot,
            "transition_fault_id": first_fault,
        })
        _publish(O.C, self.fixture.state / "injection-receipts" /
                 "00000001.json", {
            "schema": O.INJECTION_SCHEMA, "version": 1,
            "status": "COMPLETE", "campaign_id": CAMPAIGN,
            "fault_id": second_fault, "fault_type": "SERVICE_RESTART",
            **O.C.boundary(),
        })
        worker.observe_faults()
        restarted = self.worker()
        self.clock.wall_ms = origin
        restarted.sample_campaign_continuity_once()
        argv = self.runner.calls[-1]
        self.assertEqual(
            argv[argv.index("--transition-fault-id") + 1], first_fault)

    def test_reordered_continuity_commit_is_rejected(self) -> None:
        worker = self.worker()
        origin, end, cadence = worker._continuity_bounds()
        worker._event("CAMPAIGN_CONTINUITY_SLOT", "COMMITTED", {
            "first_slot": 1, "last_slot": 1,
            "scheduled_at_ms": origin + cadence,
            "origin_ms": origin, "end_ms": end, "cadence_ms": cadence,
            "maximum_slot_lateness_ms":
                self.fixture.runtime.document["maximum_slot_lateness_ms"],
            "final_slot": worker._continuity_final_slot(),
            "transition_fault_id": None,
            "continuity_observation": {
                "path": "/fake", "file_sha256": SHA, "body_sha256": SHA},
            "catch_up": False,
        })
        self.clock.wall_ms = origin
        with self.assertRaisesRegex(O.WorkerError, "CONTINUITY_ORDER_INVALID"):
            worker.sample_campaign_continuity_once()

    def test_duplicate_continuity_commit_is_rejected(self) -> None:
        worker = self.worker()
        origin, end, cadence = worker._continuity_bounds()
        details = {
            "first_slot": 0, "last_slot": 0, "scheduled_at_ms": origin,
            "origin_ms": origin, "end_ms": end, "cadence_ms": cadence,
            "maximum_slot_lateness_ms":
                self.fixture.runtime.document["maximum_slot_lateness_ms"],
            "final_slot": worker._continuity_final_slot(),
            "transition_fault_id": None,
            "continuity_observation": {
                "path": "/fake", "file_sha256": SHA, "body_sha256": SHA},
            "catch_up": False,
        }
        worker._event("CAMPAIGN_CONTINUITY_SLOT", "COMMITTED", details)
        worker._event("CAMPAIGN_CONTINUITY_SLOT", "COMMITTED", details)
        self.clock.wall_ms = origin
        with self.assertRaisesRegex(O.WorkerError, "CONTINUITY_ORDER_INVALID"):
            worker.sample_campaign_continuity_once()

    def test_exactly_once_seven_faults_and_final_cleanup(self) -> None:
        fault_types = (
            "PROCESS_KILL", "SERVICE_RESTART", "TOKEN_LOSS", "LEASE_EXPIRY",
            "NETWORK_DENY_RELOAD", "EVIDENCE_WRITER_CRASH", "CLOCK_STEP",
        )
        for index, fault_type in enumerate(fault_types, 1):
            _publish(O.C, self.fixture.state / "injection-receipts" /
                     f"{index:08d}.json", {
                "schema": O.INJECTION_SCHEMA, "version": 1,
                "status": "COMPLETE", "campaign_id": CAMPAIGN,
                "fault_id": f"fault-{index:02d}", "fault_type": fault_type,
                **O.C.boundary(),
            })
        worker = self.worker()
        worker.observe_faults()
        self.assertEqual(len(self.runner.calls), 14)  # fault + cleanup each
        worker.observe_faults()
        self.assertEqual(len(self.runner.calls), 14)
        commits = [
            item.document for item in worker.journal.entries
            if item.document["event"] == "OBSERVE_FAULT" and
            item.document["status"] == "COMMITTED"
        ]
        self.assertEqual(len(commits), 7)

        request = worker.control.publish(
            "observer", "CLEANUP", {
                "subject_type": "FINAL", "subject_id": CAMPAIGN,
                "formal_campaign_id": None,
                "fault_injection_receipt": None,
            }, now_ms=self.clock.wall_ms,
            deadline_ms=self.clock.wall_ms + 10_000)
        worker.process_requests()
        ack = worker.control.ack("observer", request["request_id"])
        self.assertIsNotNone(ack)
        self.assertEqual(ack["status"], "COMPLETE")


class RecorderWorkerRecoveryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.base = Path(self.temporary.name)
        os.chmod(self.base, 0o700)
        self.clock = Clock()
        self.fixture = Fixture(self.base, self.clock)
        self.runner = FakeRecorderRunner()
        self.patch = mock.patch.object(
            R.C, "_secure_executable", return_value=b"pinned")
        self.patch.start()

    def tearDown(self) -> None:
        self.patch.stop()
        self.temporary.cleanup()

    def test_post_recorder_pre_worker_commit_crash_recovers_from_wal_chain(self) -> None:
        observation = _publish(
            R.C, self.fixture.state / "raw-observations" / "cleanup" /
            "00000000.json", {
                "schema": R.CLEANUP_SCHEMA, "version": 1,
                "campaign_id": CAMPAIGN, "status": "COMPLETE",
                "production_mode": "PRODUCTION_ROOT_OBSERVER",
                "expires_at_ms": self.clock.wall_ms + 10_000,
                **R.C.boundary(),
            })
        worker = R.RecorderWorker(
            self.fixture.runtime, self.runner,
            expected_uid=os.getuid(), expected_gid=os.getgid(),
            wall_clock=self.clock.wall, boot_clock=self.clock.boot)
        worker.journal.append("PROJECT_OBSERVATION", "INTENT", {
            "kind": "cleanup", "input": R.C._reference(observation),
            "input_file_sha256": observation.file_sha256,
        })
        recorder_entry = R.C.seal({
            "schema": R.RECORDER_JOURNAL_SCHEMA, "version": 1,
            "campaign_id": CAMPAIGN, "sequence": 0,
            "operation": "RECORD_CLEANUP", "recorded_at_ms": self.clock.wall_ms,
            "transaction_reference": {},
            "inputs": [{
                **R.C._reference(observation), "role": "cleanup_observation",
                "schema": R.CLEANUP_SCHEMA, "sealed": True,
            }],
            "outputs": [{"path": "already-projected"}],
            "previous_entry_body_sha256": None, **R.C.boundary(),
        })
        R.C.publish_noreplace(
            self.fixture.state / "recorder" / "journal" / "00000000.json",
            recorder_entry, expected_uid=os.getuid(), expected_gid=os.getgid())

        restarted = R.RecorderWorker(
            self.fixture.runtime, self.runner,
            expected_uid=os.getuid(), expected_gid=os.getgid(),
            wall_clock=self.clock.wall, boot_clock=self.clock.boot)
        restarted.drain_observations()
        commits = [
            item.document for item in restarted.journal.entries
            if item.document["event"] == "PROJECT_OBSERVATION" and
            item.document["status"] == "COMMITTED"
        ]
        self.assertEqual(len(commits), 1)
        self.assertEqual(self.runner.calls, [])  # no replay subprocess

    def test_only_campaign_continuity_projects_a_checkpoint(self) -> None:
        observation = _publish(
            R.C, self.fixture.state / "raw-observations" / "continuity" /
            "00000000.json", {
                "schema": R.CAMPAIGN_CONTINUITY_SCHEMA, "version": 1,
                "campaign_id": CAMPAIGN, "status": "COMPLETE",
                "production_mode": "PRODUCTION_ROOT_OBSERVER",
                "expires_at_ms": self.clock.wall_ms + 10_000,
                **R.C.boundary(),
            })
        worker = R.RecorderWorker(
            self.fixture.runtime, self.runner,
            expected_uid=os.getuid(), expected_gid=os.getgid(),
            wall_clock=self.clock.wall, boot_clock=self.clock.boot)
        committed = [{"path": "projected-checkpoint"}]
        with mock.patch.object(
                worker, "_committed_input",
                side_effect=[None, committed]), mock.patch.object(
                    worker, "_run_recorder",
                    return_value=R.C.CommandResult(0, b"{}\n", b"")) as run:
            worker.drain_observations()
        argv = run.call_args.args[0]
        self.assertEqual(argv[:3], [R.RECORDER, "--run", "checkpoint"])
        self.assertEqual(
            Path(argv[argv.index("--observation") + 1]), observation.path)
        entry = worker.journal.entries[-1].document
        self.assertEqual(entry["details"]["kind"], "continuity")

    def test_formal_service_receipt_is_clock_anchor_not_checkpoint(self) -> None:
        for kind, schema in (
            ("service", R.SERVICE_SCHEMA),
            ("authority", R.AUTHORITY_SCHEMA),
        ):
            _publish(R.C, self.fixture.state / "raw-observations" / kind /
                     "formal-00000000.json", {
                "schema": schema, "version": 1, "campaign_id": CAMPAIGN,
                "status": "COMPLETE",
                "production_mode": "PRODUCTION_ROOT_OBSERVER",
                "expires_at_ms": self.clock.wall_ms + 10_000,
                **R.C.boundary(),
            })
        worker = R.RecorderWorker(
            self.fixture.runtime, self.runner,
            expected_uid=os.getuid(), expected_gid=os.getgid(),
            wall_clock=self.clock.wall, boot_clock=self.clock.boot)
        with mock.patch.object(worker, "_project_observation") as project:
            worker.drain_observations()
        self.assertEqual(project.call_count, 1)
        self.assertEqual(project.call_args.args[0], "authority")

    def test_recovery_runs_once_and_half_published_observation_pair_waits(self) \
            -> None:
        _publish(R.C, self.fixture.state / "raw-observations" / "service" /
                 "00000000.json", {
            "schema": R.SERVICE_SCHEMA, "version": 1,
            "campaign_id": CAMPAIGN,
            "production_mode": "PRODUCTION_ROOT_OBSERVER",
            "clock_id": "CLOCK_BOOTTIME", "boot_id": BOOT,
            "observed_at_ms": self.clock.wall_ms,
            "observed_boottime_ns": self.clock.boot_ns,
            "expires_at_ms": self.clock.wall_ms + 10_000,
            **R.C.boundary(),
        })
        worker = R.RecorderWorker(
            self.fixture.runtime, self.runner,
            expected_uid=os.getuid(), expected_gid=os.getgid(),
            wall_clock=self.clock.wall, boot_clock=self.clock.boot)
        worker.step()
        worker.step()
        recover = [values for values in self.runner.calls
                   if len(values) > 2 and values[2] == "recover"]
        self.assertEqual(len(recover), 1)
        projected = [values for values in self.runner.calls
                     if len(values) > 2 and values[2] != "recover"]
        self.assertEqual(projected, [])

    def test_projection_clock_is_post_decision_same_boot_pre_expiry_and_unique(
            self) -> None:
        formal = self.fixture.runtime.document["formal_campaigns"][0]
        final_evaluated = formal["expires_at_ms"] - 2_000
        closure = _publish(R.C, self.base / "clock-closure.json", {
            "schema": R.VERIFIED_CLOSURE_SCHEMA, "version": 1,
            "campaign_id": FORMAL,
            "iterations": [{"evaluated_at_ms": final_evaluated}],
            **R.C.boundary(),
        })
        service = self.fixture.state / "raw-observations" / "service"
        for name, boot_id, observed_at in (
            ("00000000.json", "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee",
             final_evaluated + 500),
            ("00000001.json", BOOT, formal["expires_at_ms"] + 1),
            ("00000002.json", BOOT, final_evaluated + 1_000),
        ):
            _publish(R.C, service / name, {
                "schema": R.SERVICE_SCHEMA, "version": 1,
                "campaign_id": CAMPAIGN,
                "production_mode": "PRODUCTION_ROOT_OBSERVER",
                "clock_id": "CLOCK_BOOTTIME", "boot_id": boot_id,
                "observed_at_ms": observed_at,
                "observed_boottime_ns": self.clock.boot_ns,
                "expires_at_ms": observed_at + 4 * 60 * 1000,
                **R.C.boundary(),
            })
        worker = R.RecorderWorker(
            self.fixture.runtime, self.runner,
            expected_uid=os.getuid(), expected_gid=os.getgid(),
            wall_clock=self.clock.wall, boot_clock=self.clock.boot)
        selected = worker._select_projection_clock(closure)
        self.assertIsNotNone(selected)
        self.assertEqual(selected.path.name, "00000002.json")

        recorder_entry = R.C.seal({
            "schema": R.RECORDER_JOURNAL_SCHEMA, "version": 1,
            "campaign_id": CAMPAIGN, "sequence": 0,
            "operation": "PROJECT_DECISIONS",
            "inputs": [{
                **R.C._reference(selected),
                "role": "decision_clock_observation",
            }],
            "outputs": [{"path": "projected"}],
            "previous_entry_body_sha256": None, **R.C.boundary(),
        })
        R.C.publish_noreplace(
            self.fixture.state / "recorder" / "journal" / "00000000.json",
            recorder_entry, expected_uid=os.getuid(), expected_gid=os.getgid())
        self.assertIsNone(worker._select_projection_clock(closure))

        descriptor = os.open(selected.path, os.O_WRONLY | os.O_TRUNC)
        try:
            os.write(descriptor, b'{"replaced":true}\n')
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
        with self.assertRaises((R.WorkerError, R.C.CoordinatorError)):
            worker._select_projection_clock(closure)

    def test_projection_request_after_frozen_deadline_is_rejected(self) -> None:
        formal = self.fixture.runtime.document["formal_campaigns"][0]
        closure = _publish(R.C, self.base / "late-closure.json", {
            "schema": R.VERIFIED_CLOSURE_SCHEMA, "version": 1,
            "campaign_id": FORMAL,
            "iterations": [{"evaluated_at_ms": formal["expires_at_ms"] - 1}],
            **R.C.boundary(),
        })
        worker = R.RecorderWorker(
            self.fixture.runtime, self.runner,
            expected_uid=os.getuid(), expected_gid=os.getgid(),
            wall_clock=self.clock.wall, boot_clock=self.clock.boot)
        self.clock.wall_ms = formal["projection_deadline_ms"] + 1
        request = {
            "request_id": "late-projection", "deadline_ms":
                formal["projection_deadline_ms"],
            "arguments": {
                "formal_campaign_id": FORMAL,
                "verified_closure": R.C._reference(closure),
                "artifact_root": str(self.fixture.artifact_root),
            },
        }
        with self.assertRaisesRegex(
                R.WorkerError, "PROJECTION_DEADLINE_INVALID"):
            worker._process_projection(request)


class SystemdContractTests(unittest.TestCase):
    def test_units_pin_root_notify_restart_watchdog_and_fixed_run_images(self) -> None:
        files = {
            "coordinator": ROOT / "systemd" /
                "hepta-p1-safety-soak-campaign@.service",
            "observer": ROOT / "systemd" /
                "hepta-p1-safety-soak-observer-worker@.service",
            "recorder": ROOT / "systemd" /
                "hepta-p1-safety-soak-recorder-worker@.service",
        }
        for role, path in files.items():
            text = path.read_text(encoding="utf-8")
            self.assertIn("Type=notify", text, role)
            self.assertIn("NotifyAccess=main", text, role)
            self.assertIn("User=root", text, role)
            self.assertIn("Group=root", text, role)
            self.assertIn("Restart=on-failure", text, role)
            self.assertIn("WatchdogSec=", text, role)
            self.assertIn(" --run ", text, role)
            self.assertIn("NoNewPrivileges=yes", text, role)
        target = (ROOT / "systemd" /
                  "hepta-p1-safety-soak@.target").read_text(encoding="utf-8")
        self.assertIn("hepta-p1-safety-soak-campaign@%i.service", target)

    def test_effective_fragments_have_no_enable_surface_or_weakening_reset(
            self) -> None:
        paths = {
            "coordinator": ROOT / "systemd" /
                "hepta-p1-safety-soak-campaign@.service",
            "observer": ROOT / "systemd" /
                "hepta-p1-safety-soak-observer-worker@.service",
            "recorder": ROOT / "systemd" /
                "hepta-p1-safety-soak-recorder-worker@.service",
            "target": ROOT / "systemd" / "hepta-p1-safety-soak@.target",
        }
        parsed = {role: C._unit_sections(path.read_bytes())
                  for role, path in paths.items()}
        for role, sections in parsed.items():
            self.assertNotIn("Install", sections, role)
        expected_caps = {
            "coordinator": "",
            "observer": (
                "CAP_DAC_READ_SEARCH CAP_SYS_PTRACE CAP_NET_ADMIN"),
            "recorder": "CAP_DAC_READ_SEARCH",
        }
        for role in ("coordinator", "observer", "recorder"):
            service = parsed[role]["Service"]
            self.assertEqual(service["CapabilityBoundingSet"],
                             [expected_caps[role]])
            self.assertEqual(service["AmbientCapabilities"], [""])
            for key, expected in (
                ("NoNewPrivileges", "yes"), ("PrivateDevices", "yes"),
                ("ProtectHostname", "yes"),
                ("RestrictNamespaces", "yes"),
                ("MemoryDenyWriteExecute", "yes"),
                ("KeyringMode", "private"), ("RemoveIPC", "yes"),
                ("RestrictAddressFamilies",
                 "AF_UNIX AF_NETLINK" if role == "observer" else "AF_UNIX"),
                ("IPAddressDeny", "any"),
            ):
                self.assertEqual(service[key], [expected], (role, key))
        for role in ("coordinator", "target"):
            conflicts = {
                unit for value in parsed[role]["Unit"]["Conflicts"]
                for unit in value.split()
            }
            self.assertEqual(conflicts, set(C.FORBIDDEN_EXECUTION_UNITS))

    def test_static_fragment_digest_drift_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "unit.service"
            payload = b"[Unit]\nDescription=frozen\n"
            descriptor = os.open(
                path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            try:
                os.write(descriptor, payload)
                os.fchmod(descriptor, 0o600)
                os.fsync(descriptor)
            finally:
                os.close(descriptor)
            self.assertEqual(C._secure_static_file(
                path, C.digest_bytes(payload), expected_uid=os.getuid(),
                expected_gid=os.getgid()), payload)
            with self.assertRaisesRegex(
                    C.CoordinatorError, "STATIC_FRAGMENT_INVALID"):
                C._secure_static_file(
                    path, SHA, expected_uid=os.getuid(),
                    expected_gid=os.getgid())

    @unittest.skipUnless(shutil.which("systemd-analyze"),
                         "systemd-analyze unavailable")
    def test_systemd_analyze_verify_accepts_all_four_fragments(self) -> None:
        names = (
            "hepta-p1-safety-soak-campaign@.service",
            "hepta-p1-safety-soak-observer-worker@.service",
            "hepta-p1-safety-soak-recorder-worker@.service",
            "hepta-p1-safety-soak@.target",
        )
        replacements = (
            b"/usr/libexec/hepta-p1-safety-soak-campaign-coordinator",
            b"/usr/libexec/hepta-p1-safety-soak-observer-worker",
            b"/usr/libexec/hepta-p1-safety-soak-recorder-worker",
        )
        with tempfile.TemporaryDirectory() as temporary:
            for name in names:
                payload = (ROOT / "systemd" / name).read_bytes()
                for executable in replacements:
                    payload = payload.replace(executable, b"/bin/true")
                staged = Path(temporary) / name
                descriptor = os.open(
                    staged, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
                try:
                    os.write(descriptor, payload)
                    os.fsync(descriptor)
                finally:
                    os.close(descriptor)
                result = subprocess.run(
                    [shutil.which("systemd-analyze"), "verify", str(staged)],
                    stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE, check=False, timeout=30,
                    env={"PATH": "/usr/sbin:/usr/bin:/sbin:/bin",
                         "LANG": "C"})
                self.assertEqual(
                    result.returncode, 0,
                    result.stderr.decode("utf-8", errors="replace"))


if __name__ == "__main__":
    unittest.main()
