#!/usr/bin/env python3
"""Offline adversarial tests for the root P1 fault injector."""

from __future__ import annotations

import copy
from datetime import datetime
import fcntl
import importlib.util
import json
import os
from pathlib import Path
import stat
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest import mock
from zoneinfo import ZoneInfo


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
sys.path.insert(0, str(SCRIPTS))


def load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


INJECTOR = load(
    "hepta_p1_safety_soak_root_fault_injector_under_test",
    SCRIPTS / "hepta_p1_safety_soak_root_fault_injector.py")
OBSERVER = INJECTOR.OBSERVER
RECORDER = load(
    "hepta_p1_safety_soak_evidence_recorder_for_injector_test",
    SCRIPTS / "hepta_p1_safety_soak_evidence_recorder.py")
AUDITOR = load(
    "hepta_p1_safety_soak_auditor_for_injector_test",
    SCRIPTS / "hepta_p1_safety_soak_auditor.py")


BOOT_ID = "01234567-89ab-cdef-0123-456789abcdef"
CAMPAIGN_ID = "p1-soak-round101"
FORMAL_ID = "p1-formal-round101"
SOURCE_SHA = "sha256:" + "1" * 64
POLICY_SHA = "sha256:" + "2" * 64
STRATEGY_SHA = "sha256:" + "3" * 64
BASE_BOOT_NS = 1_000_000_000_000
BASE_WALL_MS = 2_000_000_000_000
TRADING_DAYS = [
    "2026-08-03", "2026-08-04", "2026-08-05", "2026-08-06",
    "2026-08-07", "2026-08-10", "2026-08-11", "2026-08-12",
    "2026-08-13", "2026-08-14",
]
TRADING_ZONE = ZoneInfo(OBSERVER.TRADING_TIMEZONE)
ELIGIBLE_SCHEDULE = [
    int(datetime.fromisoformat(day + "T10:00:00")
        .replace(tzinfo=TRADING_ZONE).timestamp() * 1000) + offset * 60_000
    for day in TRADING_DAYS for offset in range(20)
]
FREEZE_REFERENCE = {
    "path": "/evidence/freeze-bundle.json",
    "file_sha256": "sha256:" + "8" * 64,
    "body_sha256": "sha256:" + "9" * 64,
}
RUNTIME_REFERENCE = {
    "path": "/evidence/runtime-manifest.json",
    "file_sha256": "sha256:" + "a" * 64,
    "body_sha256": "sha256:" + "b" * 64,
}


def digest(label: str) -> str:
    return INJECTOR._digest_bytes(label.encode("ascii"))


def state(value: dict) -> dict:
    return INJECTOR._state_seal(value)


def unit(
    name: str, pid: int, invocation: str, start: int,
) -> dict:
    return state({
        "unit": name, "load_state": "loaded", "active_state": "active",
        "sub_state": "running", "unit_file_state": "enabled",
        "main_pid": pid, "invocation_id": invocation,
        "exec_main_start_timestamp_monotonic_us": start,
        "n_restarts": 0,
    })


def process(pid: int, start: int, inode: int = 9001) -> dict:
    return state({
        "pid": pid, "uid": 1000, "gid": 1000,
        "starttime_ticks": start, "exe_device": 8, "exe_inode": inode,
        "cgroup_sha256": digest("cgroup"),
    })


def path_item(path: Path, label: str) -> dict:
    return state({
        "path": str(path), "present": True, "parent_device": 1,
        "parent_inode": 10, "parent_uid": 0, "parent_gid": 0,
        "parent_mode": 0o700, "parent_nlink": 2,
        "file_type": "regular", "device": 1,
        "inode": 100 + sum(label.encode("ascii")), "uid": 0, "gid": 0,
        "mode": 0o600, "nlink": 1, "size": 128,
        "mtime_ns": 100, "ctime_ns": 100,
        "content_file_sha256": digest("file-" + label),
        "content_body_sha256": digest("body-" + label),
    })


def broker(checked: int) -> dict:
    return state({
        "helper_path": str(INJECTOR.BROKER_HELPER),
        "helper_file_sha256": digest("broker-helper"),
        "policy_sha256": digest("deny-policy"),
        "authorized_connector_count": 0, "authorized_uids": [],
        "protected_port_count": 4, "deny_all": True,
        "checked_boottime_ns": checked,
    })


def identity(
    fault: dict, phase: str, observed: int, *, post: bool,
    noop: bool = False, residue: int = 0, clock_delta: int | None = None,
) -> dict:
    fault_type = fault["fault_type"]
    actual_post = post and not noop
    epoch = "epoch-2" if fault_type == "SERVICE_RESTART" and actual_post \
        else "epoch-1"
    units: list[dict] = []
    processes: list[dict] = []
    paths: list[dict] = []
    fixture_generation = None
    fixture_expiry = None
    fixture_valid = None
    if fault_type in {"PROCESS_KILL", "EVIDENCE_WRITER_CRASH"}:
        processes = [process(
            2002 if actual_post else 2001,
            200 if actual_post else 100)]
    elif fault_type == "SERVICE_RESTART":
        units = [unit(
            OBSERVER.GATEWAY_UNIT, 2102 if actual_post else 2101,
            ("b" if actual_post else "a") * 32,
            200 if actual_post else 100)]
    elif fault_type == "NETWORK_DENY_RELOAD":
        units = [unit(
            OBSERVER.BROKER_UNIT, 2202 if actual_post else 2201,
            ("d" if actual_post else "c") * 32,
            200 if actual_post else 100)]
    elif fault_type in {"TOKEN_LOSS", "LEASE_EXPIRY"}:
        fixture_path = OBSERVER.TOKEN_FAULT_FIXTURE \
            if fault_type == "TOKEN_LOSS" else OBSERVER.LEASE_FAULT_FIXTURE
        paths = [path_item(
            fixture_path, ("post" if actual_post else "pre") + fault_type)]
        fixture_generation = 6 if actual_post else 5
        fixture_expiry = (
            fault["inject_at_boottime_ns"] + 10_000_000_000
            if actual_post else
            (fault["inject_at_boottime_ns"]
             if fault_type == "LEASE_EXPIRY" else
             fault["inject_at_boottime_ns"] + 1_000_000_000))
        fixture_valid = True
    delta = None
    if fault_type == "CLOCK_STEP":
        delta = INJECTOR.CLOCK_FIXTURE_DELTA_MS if actual_post else 0
        if clock_delta is not None:
            delta = clock_delta
        paths = [path_item(INJECTOR.CLOCK_FIXTURE_HELPER, "clock-surface")]
    checked = observed - 100
    return INJECTOR._seal({
        "schema": INJECTOR.IDENTITY_SCHEMA, "version": 1, "phase": phase,
        "target_id": fault["target_id"], "boot_id": BOOT_ID,
        "observed_boottime_ns": observed, "service_epoch": epoch,
        "fencing_generation": 7, "lease_generation": 11,
        "systemd_units": units, "processes": processes, "paths": paths,
        "broker_deny_all": broker(checked), "residue_count": residue,
        "wall_clock_delta_ms": delta,
        "fixture_generation": fixture_generation,
        "fixture_expires_boottime_ns": fixture_expiry,
        "fixture_valid": fixture_valid,
    })


class FakeExecutor:
    def __init__(
        self, *, boundary_loss: bool = False, noop: bool = False,
        residue: bool = False, late_ns: int = 0,
        recovery_extra_ns: int = 1_000_000_000,
        crash_after_action: bool = False, crash_stage: str | None = None,
        crash_after_recovery: bool = False,
        crash_after_cleanup: bool = False,
        lose_boundary_after_action: bool = False,
    ):
        self.boottime_ns = BASE_BOOT_NS
        self.boundary_loss = boundary_loss
        self.noop = noop
        self.residue = residue
        self.late_ns = late_ns
        self.recovery_extra_ns = recovery_extra_ns
        self.crash_after_action = crash_after_action
        self.crash_stage = crash_stage
        self.crash_after_recovery = crash_after_recovery
        self.crash_after_cleanup = crash_after_cleanup
        self.lose_boundary_after_action = lose_boundary_after_action
        self.crashed = False
        self.actions: dict[str, INJECTOR.ActionState] = {}
        self.recoveries: dict[str, INJECTOR.RecoveryState] = {}
        self.action_calls: dict[str, int] = {}
        self.recovery_calls: dict[str, int] = {}
        self.cleanup_calls: dict[str, int] = {}
        self.subprocess_calls = 0

    def clock(self):
        return OBSERVER.ClockSample(
            wall_ms=BASE_WALL_MS +
                (self.boottime_ns - BASE_BOOT_NS) // 1_000_000,
            boottime_ns=self.boottime_ns, boot_id=BOOT_ID)

    def wait_until(self, boottime_ns: int) -> None:
        self.boottime_ns = max(self.boottime_ns, boottime_ns + self.late_ns)

    def assert_boundary(self) -> None:
        if self.boundary_loss:
            raise INJECTOR.InjectorError("P1_FAULT_INJECTOR_BOUNDARY_LOST")

    def prepare(self, fault, frozen):
        del fault, frozen

    def pre_identity(self, fault, frozen):
        del frozen
        return identity(
            fault, "PRE", self.boottime_ns, post=False)

    def inject(self, fault, pre, frozen):
        del pre, frozen
        fault_id = fault["fault_id"]
        self.action_calls[fault_id] = self.action_calls.get(fault_id, 0) + 1
        action = INJECTOR.ActionState(
            actual_boottime_ns=self.boottime_ns,
            evidence_sha256=digest("action-" + fault_id))
        self.actions[fault_id] = action
        if self.lose_boundary_after_action:
            self.boundary_loss = True
        if self.crash_after_action and not self.crashed:
            self.crashed = True
            raise INJECTOR.InjectedCrash("after-action")
        return action

    def resume_action(self, fault, pre, frozen, intent_boottime_ns):
        del intent_boottime_ns
        action = self.actions.get(fault["fault_id"])
        if action is not None:
            return action
        return self.inject(fault, pre, frozen)

    def recover(self, fault, pre, action, frozen):
        del pre, frozen
        existing = self.recoveries.get(fault["fault_id"])
        if existing is not None:
            return existing
        self.recovery_calls[fault["fault_id"]] = \
            self.recovery_calls.get(fault["fault_id"], 0) + 1
        self.boottime_ns = max(
            self.boottime_ns,
            action.actual_boottime_ns + self.recovery_extra_ns)
        recovery = INJECTOR.RecoveryState(
            recovered_boottime_ns=self.boottime_ns,
            post_identity=identity(
                fault, "POST", self.boottime_ns, post=True,
                noop=self.noop, residue=1 if self.residue else 0))
        self.recoveries[fault["fault_id"]] = recovery
        if self.crash_after_recovery and not self.crashed:
            self.crashed = True
            raise INJECTOR.InjectedCrash("after-recovery")
        return recovery

    def cleanup(self, fault, recovery, frozen):
        del recovery, frozen
        self.cleanup_calls[fault["fault_id"]] = \
            self.cleanup_calls.get(fault["fault_id"], 0) + 1
        if self.crash_after_cleanup and not self.crashed:
            self.crashed = True
            raise INJECTOR.InjectedCrash("after-cleanup")

    def cleanup_residue(self, frozen):
        del frozen
        self.boundary_loss = False

    def fail_close(
        self, fault, pre, action, frozen, intent_boottime_ns,
    ):
        del frozen
        self.boundary_loss = False
        self.noop = False
        self.residue = False
        if pre is None or fault["fault_id"] not in self.actions:
            return None
        existing = self.recoveries.get(fault["fault_id"])
        if existing is not None:
            return existing
        active = action or INJECTOR.ActionState(
            intent_boottime_ns, digest("fail-close-action"))
        return self.recover(fault, pre, active, None)

    def after_journal(self, stage: str) -> None:
        if self.crash_stage == stage and not self.crashed:
            self.crashed = True
            raise INJECTOR.InjectedCrash("after-" + stage)


class Fixture:
    def __init__(self):
        self.temporary = tempfile.TemporaryDirectory(dir=ROOT)
        self.root = Path(self.temporary.name)
        self.root.chmod(0o700)
        self.uid = os.geteuid()
        self.gid = os.getegid()
        self.journal = self.root / "journal"
        self.output = self.root / "output"
        self.journal.mkdir(mode=0o700)
        self.output.mkdir(mode=0o700)
        self.plan = self.make_plan()
        self.spec = self.make_spec(self.plan["body_sha256"])
        self.spec_path = self.write("spec.json", self.spec)
        self.plan_path = self.write("plan.json", self.plan)
        spec_sha = digest(self.spec_path.read_bytes().decode("ascii"))
        plan_sha = digest(self.plan_path.read_bytes().decode("ascii"))
        self.pins = self.make_pins(spec_sha, plan_sha)
        self.pins_path = self.write("pins.json", self.pins)

    def close(self):
        self.temporary.cleanup()

    def write(self, name: str, document: dict, mode: int = 0o600) -> Path:
        path = self.root / name
        path.write_bytes(INJECTOR._canonical_bytes(document))
        path.chmod(mode)
        return path

    @staticmethod
    def make_plan() -> dict:
        planned = []
        for index, fault_type in enumerate(INJECTOR.EXPECTED_FAULT_ORDER):
            planned.append({
                "fault_id": f"fault-{index + 1}-{fault_type.lower()}",
                "fault_type": fault_type,
                "target_id": OBSERVER.FAULT_TARGET_IDS[fault_type],
                "formal_campaign_id": FORMAL_ID,
                "inject_at_boottime_ns":
                    BASE_BOOT_NS + (index + 1) * 10_000_000_000,
                "maximum_injection_lateness_ns": 1_000_000_000,
                "maximum_recovery_ns": 2_000_000_000,
            })
        return INJECTOR._seal({
            "schema": OBSERVER.FAULT_PLAN_SCHEMA, "version": 1,
            "campaign_id": CAMPAIGN_ID,
            "source_manifest_sha256": SOURCE_SHA,
            "policy_sha256": POLICY_SHA, "strategy_sha256": STRATEGY_SHA,
            "planned_faults": planned, **INJECTOR._boundary(),
        })

    @staticmethod
    def make_spec(plan_sha: str) -> dict:
        return INJECTOR._seal({
            "schema": OBSERVER.SPEC_SCHEMA, "version": 1,
            "campaign_id": CAMPAIGN_ID, "domain_id": "alpha",
            "source_manifest_sha256": SOURCE_SHA,
            "policy_sha256": POLICY_SHA, "strategy_id": "strategy-v1",
            "strategy_version": "1", "strategy_sha256": STRATEGY_SHA,
            "formal_campaigns": [{
                "campaign_id": FORMAL_ID,
                "campaign_sha256": digest("formal"),
                "policy_body_sha256": digest("formal-policy-body"),
                "policy_file_sha256": digest("formal-policy-file"),
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
            "fault_plan_body_sha256": plan_sha,
            "independent_auditor_id": "independent-auditor-v1",
            "frozen_at_ms": BASE_WALL_MS - 1000,
            "freeze_bundle": dict(FREEZE_REFERENCE),
            **INJECTOR._boundary(),
        })

    def make_pins(self, spec_file_sha: str, plan_file_sha: str) -> dict:
        names = INJECTOR._unit_names(FORMAL_ID)
        fragments = {
            "OBSERVER_PROCESS":
                f"/run/systemd/transient/{names['OBSERVER_PROCESS']}",
            "RECORDER_PROCESS":
                f"/run/systemd/transient/{names['RECORDER_PROCESS']}",
            "GATEWAY": "/usr/lib/systemd/system/hepta-tool-gateway@.service",
            "BROKER_POLICY":
                "/usr/lib/systemd/system/hepta-broker-egress-policy.service",
        }
        contracts = [{
            "role": role, "unit": names[role],
            "fragment_path": fragments[role],
            "fragment_file_sha256": digest("fragment-" + role),
            "executable_file_sha256": digest("executable-" + role),
            "entrypoint_path": (
                "/usr/libexec/hepta-p1-safety-soak-observer-worker"
                if role == "OBSERVER_PROCESS" else
                "/usr/libexec/hepta-p1-safety-soak-recorder-worker"
                if role == "RECORDER_PROCESS" else
                "/usr/libexec/hepta-tool-gatewayd"
                if role == "GATEWAY" else str(INJECTOR.BROKER_HELPER)),
            "entrypoint_file_sha256": digest("entrypoint-" + role),
            "exec_start_sha256": digest("exec-start-" + role),
            "exec_argv_sha256": digest("exec-argv-" + role),
        } for role in sorted(names)]
        return INJECTOR._seal({
            "schema": INJECTOR.PINS_SCHEMA, "version": 1,
            "status": "FROZEN", "issued_at_ms": BASE_WALL_MS - 1000,
            "expires_at_ms": BASE_WALL_MS + 86_400_000,
            "campaign_id": CAMPAIGN_ID,
            "formal_campaign_id": FORMAL_ID, "boot_id": BOOT_ID,
            "source_manifest_sha256": SOURCE_SHA,
            "campaign_spec_file_sha256": spec_file_sha,
            "campaign_spec_body_sha256": self.spec["body_sha256"],
            "fault_plan_file_sha256": plan_file_sha,
            "fault_plan_body_sha256": self.plan["body_sha256"],
            "freeze_bundle": dict(FREEZE_REFERENCE),
            "runtime_manifest": dict(RUNTIME_REFERENCE),
            "producer": {
                "path": str(INJECTOR.PIN_PRODUCER_HELPER),
                "file_sha256": digest("pin-producer"),
            },
            "production_mode": INJECTOR.PIN_PRODUCTION_MODE,
            "injector_id": "root-p1-fault-injector",
            "injector_path": str(INJECTOR.INJECTOR_HELPER),
            "injector_file_sha256": digest("injector"),
            "observer_helper_path": str(INJECTOR.OBSERVER_HELPER),
            "observer_helper_file_sha256": digest("observer-helper"),
            "unit_contracts": contracts,
            "broker_helper_path": str(INJECTOR.BROKER_HELPER),
            "broker_helper_file_sha256": digest("broker-helper"),
            "clock_fixture_helper_path":
                str(INJECTOR.CLOCK_FIXTURE_HELPER),
            "clock_fixture_helper_file_sha256": digest("clock-helper"),
            "clock_fixture_path": str(INJECTOR.CLOCK_FIXTURE_PATH),
            "token_fixture_path": str(INJECTOR.TOKEN_FIXTURE_PATH),
            "lease_fixture_path": str(INJECTOR.LEASE_FIXTURE_PATH),
            "journal_directory": str(self.journal),
            "output_directory": str(self.output),
            **INJECTOR._boundary(),
        })

    def load(self, **overrides):
        values = {
            "owner_uid": self.uid, "owner_gid": self.gid,
            "expected_campaign_id": CAMPAIGN_ID,
            "expected_formal_campaign_id": FORMAL_ID,
            "expected_boot_id": BOOT_ID,
            "expected_source_manifest_sha256": SOURCE_SHA,
            "expected_spec_body_sha256": self.spec["body_sha256"],
            "expected_plan_body_sha256": self.plan["body_sha256"],
            "now_ms": BASE_WALL_MS,
        }
        values.update(overrides)
        return INJECTOR.load_inputs(
            self.spec_path, self.plan_path, self.pins_path, **values)


class RootFaultInjectorTests(unittest.TestCase):
    def setUp(self):
        self.fixture = Fixture()

    def tearDown(self):
        self.fixture.close()

    def run_rehearsal(self, executor=None):
        frozen = self.fixture.load()
        executor = FakeExecutor() if executor is None else executor
        receipts = INJECTOR.run_campaign(
            frozen, executor, run_requested=False,
            effective_uid=self.fixture.uid, effective_gid=self.fixture.gid)
        return frozen, executor, receipts

    def test_rehearsal_runs_exactly_seven_without_subprocess(self):
        fake = FakeExecutor()
        with mock.patch.object(
                subprocess, "run",
                side_effect=AssertionError("subprocess forbidden")):
            _frozen, fake, receipts = self.run_rehearsal(fake)
        self.assertEqual(len(receipts), 7)
        self.assertEqual(
            {item["fault_type"] for item in receipts},
            set(INJECTOR.EXPECTED_FAULT_ORDER))
        self.assertTrue(all(item["status"] == "REHEARSAL_ONLY"
                            for item in receipts))
        self.assertEqual(sum(fake.action_calls.values()), 7)

    def test_production_context_follows_each_fault_formal_not_pin_identity(self):
        formal_ids = ["formal-segment-a", "formal-segment-b"]

        class SwitchingObserver:
            def __init__(inner_self):
                inner_self.observed: list[str] = []

            def _service_documents(
                    inner_self, _spec, formal_campaign_id, _sample):
                inner_self.observed.append(formal_campaign_id)
                marker = SimpleNamespace(path_identity={
                    "path": f"/marker/{formal_campaign_id}"})
                status = SimpleNamespace(path_identity={
                    "path": f"/status/{formal_campaign_id}"})
                lease = SimpleNamespace(path_identity={
                    "path": f"/lease/{formal_campaign_id}"})
                marker_value = {
                    "execution_service_epoch": formal_campaign_id,
                    "execution_service_fencing_generation": 7,
                }
                return marker, status, lease, marker_value, {}, {}, 11

        executor = object.__new__(INJECTOR.ProductionExecutor)
        executor.frozen = SimpleNamespace(spec={"campaign_id": CAMPAIGN_ID})
        executor.observer = SwitchingObserver()
        executor.clock = lambda: OBSERVER.ClockSample(
            wall_ms=BASE_WALL_MS, boottime_ns=BASE_BOOT_NS,
            boot_id=BOOT_ID)
        faults = [
            {"formal_campaign_id": formal_ids[index % 2]}
            for index in range(7)
        ]
        contexts = [executor._context(fault) for fault in faults]
        self.assertEqual(
            executor.observer.observed,
            [fault["formal_campaign_id"] for fault in faults])
        self.assertEqual(
            [item[0] for item in contexts],
            [fault["formal_campaign_id"] for fault in faults])

    def test_every_rehearsal_receipt_is_non_authorizing_and_0600(self):
        frozen, _fake, receipts = self.run_rehearsal()
        for index, (fault, receipt) in enumerate(
                zip(frozen.faults, receipts), 1):
            self.assertEqual(set(receipt), INJECTOR.INJECTION_FIELDS)
            for field in INJECTOR.BOUNDARY_FIELDS:
                self.assertIs(receipt[field], False)
            path = INJECTOR._output_path(frozen, index, fault)
            self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)

    def test_fake_or_forged_executor_cannot_promote(self):
        frozen = self.fixture.load()

        class Forged(INJECTOR.ProductionExecutor):
            pass

        forged = object.__new__(Forged)
        with self.assertRaisesRegex(
                INJECTOR.InjectorError, "PRODUCTION_AUTHORITY_REQUIRED"):
            INJECTOR.run_campaign(
                frozen, forged, run_requested=True,
                effective_uid=0, effective_gid=0)

    def test_nonroot_cannot_promote_exact_production_executor(self):
        frozen = self.fixture.load()
        production = object.__new__(INJECTOR.ProductionExecutor)
        with self.assertRaisesRegex(
                INJECTOR.InjectorError, "PRODUCTION_AUTHORITY_REQUIRED"):
            INJECTOR.run_campaign(
                frozen, production, run_requested=True,
                effective_uid=1000, effective_gid=1000)

    def test_no_run_cli_fails_before_subprocess_or_input_read(self):
        with mock.patch.object(
                subprocess, "run",
                side_effect=AssertionError("subprocess forbidden")):
            result = INJECTOR.main([
                "--campaign-spec", "/missing/spec", "--fault-plan",
                "/missing/plan", "--pins", "/missing/pins",
                "--campaign-id", CAMPAIGN_ID, "--formal-campaign-id",
                FORMAL_ID, "--boot-id", BOOT_ID,
                "--source-manifest-sha256", SOURCE_SHA,
                "--campaign-spec-body-sha256", digest("spec"),
                "--fault-plan-body-sha256", digest("plan"),
            ])
        self.assertEqual(result, 1)

    def test_shared_companion_contract_accepts_complete_receipts(self):
        frozen, _fake, rehearsals = self.run_rehearsal()
        for fault, rehearsal in zip(frozen.faults, rehearsals):
            complete = INJECTOR._seal({
                **{key: value for key, value in rehearsal.items()
                   if key != "body_sha256"},
                "status": "COMPLETE",
                "production_mode": INJECTOR.INJECTOR_PRODUCTION_MODE,
            })
            sample = OBSERVER.ClockSample(
                wall_ms=complete["issued_at_ms"],
                boottime_ns=complete["post_identity"]["observed_boottime_ns"],
                boot_id=BOOT_ID)
            RECORDER.validate_fault_injection_receipt(
                complete, frozen.spec, fault, sample)
            before = OBSERVER.validate_fault_identity(
                complete["pre_identity"], "PRE", fault["target_id"],
                fault["fault_type"], "compat")
            after = OBSERVER.validate_fault_identity(
                complete["post_identity"], "POST", fault["target_id"],
                fault["fault_type"], "compat")
            OBSERVER.IndependentObserver._validate_fault_transition(
                None, fault["fault_type"], before, after,
                actual_ns=complete["actual_injection_boottime_ns"],
                recovered_ns=complete["recovered_boottime_ns"],
                recovery_complete=True, reason="compat")

    def test_auditor_contract_accepts_complete_receipt_projection(self):
        frozen, _fake, rehearsals = self.run_rehearsal()
        fault = frozen.faults[0]
        rehearsal = rehearsals[0]
        complete = INJECTOR._seal({
            **{key: value for key, value in rehearsal.items()
               if key != "body_sha256"}, "status": "COMPLETE",
            "production_mode": INJECTOR.INJECTOR_PRODUCTION_MODE})
        artifact = AUDITOR.Artifact.from_document(
            "fault_injection", str(
                INJECTOR._output_path(frozen, 1, fault)), complete)
        spec_artifact = AUDITOR.Artifact.from_document(
            "campaign_spec", str(self.fixture.spec_path), frozen.spec)
        spec = AUDITOR.Spec(
            artifact=spec_artifact, campaign_id=CAMPAIGN_ID,
            domain_id="alpha", source_manifest_sha256=SOURCE_SHA,
            policy_sha256=POLICY_SHA, strategy_id="strategy-v1",
            strategy_version="1", strategy_sha256=STRATEGY_SHA,
            formal_campaigns=tuple(frozen.spec["formal_campaigns"]),
            declared_trading_days=tuple(TRADING_DAYS),
            trading_timezone=TRADING_ZONE,
            trading_calendar_sha256=digest("calendar"),
            eligible_scheduled_at_ms=tuple(ELIGIBLE_SCHEDULE),
            scheduled_decision_count=OBSERVER.MINIMUM_ELIGIBLE_DECISIONS,
            minimum_eligible_decisions=OBSERVER.MINIMUM_ELIGIBLE_DECISIONS,
            minimum_complete_ppm=OBSERVER.MINIMUM_COMPLETE_PPM,
            minimum_boottime_duration_ns=
                OBSERVER.MINIMUM_BOOTTIME_DURATION_NS,
            maximum_checkpoint_gap_ns=OBSERVER.MAXIMUM_CHECKPOINT_GAP_NS,
            maximum_decision_lateness_ms=
                OBSERVER.MAXIMUM_DECISION_LATENESS_MS,
            fault_plan_body_sha256=frozen.plan_snapshot.body_sha256,
            independent_auditor_id="independent-auditor-v1",
            freeze_bundle=dict(FREEZE_REFERENCE))
        raw = {
            "boot_id": BOOT_ID,
            "observed_at_ms": complete["issued_at_ms"],
            "injection_boottime_ns": fault["inject_at_boottime_ns"],
            "recovered_boottime_ns": complete["recovered_boottime_ns"],
            "recovery_verified": True, "cleanup_verified": True,
            "authority_failure": False, "audit_failure": False,
            "cleanup_failure": False,
        }
        AUDITOR.validate_fault_injection_artifact(
            artifact, spec, fault, raw)

    def test_noop_transition_is_rejected_before_publication(self):
        with self.assertRaisesRegex(INJECTOR.InjectorError, "TRANSITION_INVALID"):
            self.run_rehearsal(FakeExecutor(noop=True))
        self.assertEqual(list(self.fixture.output.iterdir()), [])

    def test_cleanup_residue_is_rejected_before_publication(self):
        with self.assertRaisesRegex(INJECTOR.InjectorError, "TRANSITION_INVALID"):
            self.run_rehearsal(FakeExecutor(residue=True))
        self.assertEqual(list(self.fixture.output.iterdir()), [])

    def test_late_window_is_rejected(self):
        with self.assertRaisesRegex(INJECTOR.InjectorError, "WINDOW_MISSED"):
            self.run_rehearsal(FakeExecutor(late_ns=1_000_000_001))

    def test_recovery_deadline_is_enforced(self):
        with self.assertRaisesRegex(INJECTOR.InjectorError, "RECOVERY_TIMEOUT"):
            self.run_rehearsal(
                FakeExecutor(recovery_extra_ns=2_000_000_001))

    def test_boundary_loss_prevents_any_journal_or_output(self):
        with self.assertRaisesRegex(INJECTOR.InjectorError, "BOUNDARY_LOST"):
            self.run_rehearsal(FakeExecutor(boundary_loss=True))
        self.assertEqual(list(self.fixture.output.iterdir()), [])

    def test_ambiguous_crash_after_action_fails_closed_without_replay(self):
        frozen = self.fixture.load()
        fake = FakeExecutor(crash_after_action=True)
        with self.assertRaises(INJECTOR.InjectedCrash):
            INJECTOR.run_campaign(
                frozen, fake, run_requested=False,
                effective_uid=self.fixture.uid,
                effective_gid=self.fixture.gid)
        with self.assertRaisesRegex(
                INJECTOR.InjectorError, "PRIOR_NONTERMINAL_FAILED_CLOSED"):
            INJECTOR.run_campaign(
                frozen, fake, run_requested=False,
                effective_uid=self.fixture.uid,
                effective_gid=self.fixture.gid)
        self.assertEqual(sum(fake.action_calls.values()), 1)
        journal = INJECTOR.Journal(frozen)
        self.assertEqual(journal.entries[-1]["stage"], "FAILED_CLOSED")
        self.assertTrue(journal.entries[-1]["cleanup_complete"])

    def test_every_prior_nonterminal_journal_seam_fails_closed(self):
        stages = (
            "PREPARE_INTENT", "PREPARE_RESULT", "ACTION_INTENT",
            "ACTION_RESULT", "RECOVERY_INTENT", "RECOVERY_RESULT",
            "CLEANUP_INTENT", "CLEANUP_RESULT", "PUBLISH_INTENT",
        )
        for stage in stages:
            with self.subTest(stage=stage):
                local = Fixture()
                try:
                    frozen = local.load()
                    fake = FakeExecutor(crash_stage=stage)
                    with self.assertRaises(INJECTOR.InjectedCrash):
                        INJECTOR.run_campaign(
                            frozen, fake, run_requested=False,
                            effective_uid=local.uid,
                            effective_gid=local.gid)
                    with self.assertRaisesRegex(
                            INJECTOR.InjectorError,
                            "PRIOR_NONTERMINAL_FAILED_CLOSED"):
                        INJECTOR.run_campaign(
                            frozen, fake, run_requested=False,
                            effective_uid=local.uid,
                            effective_gid=local.gid)
                    reopened = INJECTOR.Journal(frozen)
                    self.assertEqual(
                        reopened.entries[-1]["stage"], "FAILED_CLOSED")
                    self.assertTrue(
                        reopened.entries[-1]["cleanup_complete"])
                    self.assertFalse(fake.boundary_loss)
                    self.assertEqual(list(local.output.glob("*.json")), [])
                    self.assertEqual(
                        list(local.output.glob(".*.staged")), [])
                finally:
                    local.close()

    def test_crash_after_recovery_mutation_fails_closed_without_replay(self):
        frozen = self.fixture.load()
        fake = FakeExecutor(crash_after_recovery=True)
        with self.assertRaises(INJECTOR.InjectedCrash):
            INJECTOR.run_campaign(
                frozen, fake, run_requested=False,
                effective_uid=self.fixture.uid,
                effective_gid=self.fixture.gid)
        with self.assertRaisesRegex(
                INJECTOR.InjectorError, "PRIOR_NONTERMINAL_FAILED_CLOSED"):
            INJECTOR.run_campaign(
                frozen, fake, run_requested=False,
                effective_uid=self.fixture.uid,
                effective_gid=self.fixture.gid)
        self.assertEqual(sum(fake.action_calls.values()), 1)
        self.assertEqual(sum(fake.recovery_calls.values()), 1)

    def test_crash_after_cleanup_mutation_fails_closed(self):
        frozen = self.fixture.load()
        fake = FakeExecutor(crash_after_cleanup=True)
        with self.assertRaises(INJECTOR.InjectedCrash):
            INJECTOR.run_campaign(
                frozen, fake, run_requested=False,
                effective_uid=self.fixture.uid,
                effective_gid=self.fixture.gid)
        with self.assertRaisesRegex(
                INJECTOR.InjectorError, "PRIOR_NONTERMINAL_FAILED_CLOSED"):
            INJECTOR.run_campaign(
                frozen, fake, run_requested=False,
                effective_uid=self.fixture.uid,
                effective_gid=self.fixture.gid)
        self.assertGreaterEqual(sum(fake.cleanup_calls.values()), 2)

    def test_durable_published_commit_recovers_final_no_replace_output(self):
        frozen = self.fixture.load()
        fake = FakeExecutor(crash_stage="PUBLISHED")
        with self.assertRaises(INJECTOR.InjectedCrash):
            INJECTOR.run_campaign(
                frozen, fake, run_requested=False,
                effective_uid=self.fixture.uid,
                effective_gid=self.fixture.gid)
        first = INJECTOR._output_path(frozen, 1, frozen.faults[0])
        self.assertFalse(first.exists())
        receipts = INJECTOR.run_campaign(
            frozen, fake, run_requested=False,
            effective_uid=self.fixture.uid, effective_gid=self.fixture.gid)
        self.assertEqual(len(receipts), 7)
        self.assertTrue(first.exists())
        self.assertEqual(stat.S_IMODE(first.stat().st_mode), 0o600)

    def test_crash_after_staging_before_commit_decision_discards_stage(self):
        frozen = self.fixture.load()
        fake = FakeExecutor()
        original = INJECTOR._stage_receipt
        crashed = False

        def stage_then_crash(*args, **kwargs):
            nonlocal crashed
            snapshot = original(*args, **kwargs)
            if not crashed:
                crashed = True
                raise INJECTOR.InjectedCrash("after-staging")
            return snapshot

        with mock.patch.object(
                INJECTOR, "_stage_receipt", side_effect=stage_then_crash):
            with self.assertRaises(INJECTOR.InjectedCrash):
                INJECTOR.run_campaign(
                    frozen, fake, run_requested=False,
                    effective_uid=self.fixture.uid,
                    effective_gid=self.fixture.gid)
        self.assertTrue(list(self.fixture.output.glob(".*.staged")))
        with self.assertRaisesRegex(
                INJECTOR.InjectorError, "PRIOR_NONTERMINAL_FAILED_CLOSED"):
            INJECTOR.run_campaign(
                frozen, fake, run_requested=False,
                effective_uid=self.fixture.uid,
                effective_gid=self.fixture.gid)
        self.assertEqual(list(self.fixture.output.iterdir()), [])

    def test_main_exception_reconcile_sweeps_stage_and_records_failed_closed(
            self):
        frozen = self.fixture.load()
        fake = FakeExecutor()
        original = INJECTOR._stage_receipt
        crashed = False

        def stage_then_crash(*args, **kwargs):
            nonlocal crashed
            snapshot = original(*args, **kwargs)
            if not crashed:
                crashed = True
                raise INJECTOR.InjectedCrash("after-staging")
            return snapshot

        with mock.patch.object(
                INJECTOR, "_stage_receipt", side_effect=stage_then_crash):
            with self.assertRaises(INJECTOR.InjectedCrash):
                INJECTOR.run_campaign(
                    frozen, fake, run_requested=False,
                    effective_uid=self.fixture.uid,
                    effective_gid=self.fixture.gid)
        self.assertTrue(list(self.fixture.output.glob(".*.staged")))
        arguments = [
            "--campaign-spec", str(self.fixture.spec_path),
            "--fault-plan", str(self.fixture.plan_path),
            "--pins", str(self.fixture.pins_path),
            "--campaign-id", CAMPAIGN_ID,
            "--formal-campaign-id", FORMAL_ID,
            "--boot-id", BOOT_ID,
            "--source-manifest-sha256", SOURCE_SHA,
            "--campaign-spec-body-sha256", self.fixture.spec["body_sha256"],
            "--fault-plan-body-sha256", self.fixture.plan["body_sha256"],
            "--run",
        ]
        with mock.patch.object(INJECTOR, "_read_boot_id", return_value=BOOT_ID), \
                mock.patch.object(INJECTOR, "load_inputs", return_value=frozen), \
                mock.patch.object(
                    INJECTOR, "ProductionExecutor", return_value=fake), \
                mock.patch.object(
                    INJECTOR, "run_campaign",
                    side_effect=INJECTOR.InjectorError("TEST_MAIN_FAILURE")), \
                mock.patch.object(INJECTOR.os, "geteuid", return_value=0), \
                mock.patch.object(INJECTOR.os, "getegid", return_value=0):
            self.assertEqual(INJECTOR.main(arguments), 1)
        self.assertEqual(list(self.fixture.output.iterdir()), [])
        journal = INJECTOR.Journal(frozen)
        self.assertEqual(journal.entries[-1]["stage"], "FAILED_CLOSED")
        self.assertTrue(journal.entries[-1]["cleanup_complete"])

    def test_completed_campaign_replay_is_rejected(self):
        frozen, fake, _receipts = self.run_rehearsal()
        with self.assertRaisesRegex(INJECTOR.InjectorError, "REPLAY"):
            INJECTOR.run_campaign(
                frozen, fake, run_requested=False,
                effective_uid=self.fixture.uid,
                effective_gid=self.fixture.gid)

    def test_existing_output_with_different_body_fails_closed(self):
        frozen = self.fixture.load()
        first = INJECTOR._output_path(frozen, 1, frozen.faults[0])
        first.write_bytes(INJECTOR._canonical_bytes(INJECTOR._seal({
            "unrelated": True, **INJECTOR._boundary()})))
        first.chmod(0o600)
        with self.assertRaises(INJECTOR.InjectorError):
            INJECTOR.run_campaign(
                frozen, FakeExecutor(), run_requested=False,
                effective_uid=self.fixture.uid,
                effective_gid=self.fixture.gid)

    def test_symlink_output_is_not_followed(self):
        frozen = self.fixture.load()
        target = self.fixture.root / "target"
        target.write_text("safe", encoding="ascii")
        first = INJECTOR._output_path(frozen, 1, frozen.faults[0])
        first.symlink_to(target)
        with self.assertRaises(INJECTOR.InjectorError):
            INJECTOR.run_campaign(
                frozen, FakeExecutor(), run_requested=False,
                effective_uid=self.fixture.uid,
                effective_gid=self.fixture.gid)
        self.assertEqual(target.read_text(encoding="ascii"), "safe")

    def test_input_mode_must_be_0600(self):
        self.fixture.pins_path.chmod(0o644)
        with self.assertRaisesRegex(INJECTOR.InjectorError, "INPUT_INVALID"):
            self.fixture.load()

    def test_writable_input_ancestor_is_rejected_before_snapshot_load(self):
        unsafe = self.fixture.root / "unsafe-inputs"
        unsafe.mkdir(mode=0o777)
        unsafe.chmod(0o777)
        paths = []
        for source in (
            self.fixture.spec_path, self.fixture.plan_path,
            self.fixture.pins_path,
        ):
            target = unsafe / source.name
            target.write_bytes(source.read_bytes())
            target.chmod(0o600)
            paths.append(target)
        with self.assertRaisesRegex(
                INJECTOR.InjectorError, "INPUT_PARENT_UNTRUSTED"):
            INJECTOR.load_inputs(
                *paths, owner_uid=self.fixture.uid,
                owner_gid=self.fixture.gid,
                expected_campaign_id=CAMPAIGN_ID,
                expected_formal_campaign_id=FORMAL_ID,
                expected_boot_id=BOOT_ID,
                expected_source_manifest_sha256=SOURCE_SHA,
                expected_spec_body_sha256=self.fixture.spec["body_sha256"],
                expected_plan_body_sha256=self.fixture.plan["body_sha256"],
                now_ms=BASE_WALL_MS)

    def test_world_writable_output_parent_is_rejected(self):
        self.fixture.output.chmod(0o777)
        with self.assertRaisesRegex(INJECTOR.InjectorError, "PARENT_UNTRUSTED"):
            self.run_rehearsal()

    def test_writable_ancestor_of_pinned_directories_is_rejected(self):
        unsafe = self.fixture.root / "unsafe"
        unsafe.mkdir(mode=0o777)
        unsafe.chmod(0o777)
        journal = unsafe / "journal"
        output = unsafe / "output"
        journal.mkdir(mode=0o700)
        output.mkdir(mode=0o700)
        pins = copy.deepcopy(self.fixture.pins)
        pins["journal_directory"] = str(journal)
        pins["output_directory"] = str(output)
        pins = INJECTOR._seal({
            key: value for key, value in pins.items() if key != "body_sha256"})
        self.fixture.pins_path.write_bytes(INJECTOR._canonical_bytes(pins))
        frozen = self.fixture.load()
        with self.assertRaisesRegex(
                INJECTOR.InjectorError, "PARENT_UNTRUSTED"):
            INJECTOR.run_campaign(
                frozen, FakeExecutor(), run_requested=False,
                effective_uid=self.fixture.uid,
                effective_gid=self.fixture.gid)

    def test_output_parent_drift_at_publish_intent_is_rejected(self):
        frozen = self.fixture.load()

        class Drifter(FakeExecutor):
            def after_journal(inner_self, stage):
                if stage == "PUBLISH_INTENT":
                    self.fixture.output.chmod(0o777)
                super().after_journal(stage)

        try:
            with self.assertRaisesRegex(
                    INJECTOR.InjectorError, "OUTPUT_PARENT"):
                INJECTOR.run_campaign(
                    frozen, Drifter(), run_requested=False,
                    effective_uid=self.fixture.uid,
                    effective_gid=self.fixture.gid)
        finally:
            self.fixture.output.chmod(0o700)

    def test_journal_parent_drift_between_entries_is_rejected(self):
        frozen = self.fixture.load()

        class Drifter(FakeExecutor):
            def after_journal(inner_self, stage):
                if stage == "PREPARE_INTENT":
                    self.fixture.journal.chmod(0o777)
                super().after_journal(stage)

        try:
            with self.assertRaisesRegex(
                    INJECTOR.InjectorError, "JOURNAL_PARENT"):
                INJECTOR.run_campaign(
                    frozen, Drifter(), run_requested=False,
                    effective_uid=self.fixture.uid,
                    effective_gid=self.fixture.gid)
        finally:
            self.fixture.journal.chmod(0o700)

    def test_arbitrary_fixture_path_pin_is_rejected(self):
        pins = copy.deepcopy(self.fixture.pins)
        pins["token_fixture_path"] = "/tmp/not-a-token-fixture"
        pins = INJECTOR._seal({key: value for key, value in pins.items()
                               if key != "body_sha256"})
        self.fixture.pins_path.write_bytes(INJECTOR._canonical_bytes(pins))
        with self.assertRaisesRegex(INJECTOR.InjectorError, "PINS_INVALID"):
            self.fixture.load()

    def test_arbitrary_unit_target_is_rejected(self):
        plan = copy.deepcopy(self.fixture.plan)
        plan["planned_faults"][0]["target_id"] = "arbitrary-target"
        plan = INJECTOR._seal({key: value for key, value in plan.items()
                               if key != "body_sha256"})
        self.fixture.plan_path.write_bytes(INJECTOR._canonical_bytes(plan))
        with self.assertRaises(INJECTOR.InjectorError):
            self.fixture.load(
                expected_plan_body_sha256=plan["body_sha256"])

    def test_missing_fault_type_is_rejected(self):
        plan = copy.deepcopy(self.fixture.plan)
        plan["planned_faults"].pop()
        plan = INJECTOR._seal({key: value for key, value in plan.items()
                               if key != "body_sha256"})
        self.fixture.plan_path.write_bytes(INJECTOR._canonical_bytes(plan))
        with self.assertRaises(INJECTOR.InjectorError):
            self.fixture.load(
                expected_plan_body_sha256=plan["body_sha256"])

    def test_overlapping_fault_windows_are_rejected(self):
        plan = copy.deepcopy(self.fixture.plan)
        first = plan["planned_faults"][0]
        plan["planned_faults"][1]["inject_at_boottime_ns"] = \
            first["inject_at_boottime_ns"] + 1
        plan = INJECTOR._seal({key: value for key, value in plan.items()
                               if key != "body_sha256"})
        self.fixture.plan_path.write_bytes(INJECTOR._canonical_bytes(plan))
        with self.assertRaises(INJECTOR.InjectorError):
            self.fixture.load(
                expected_plan_body_sha256=plan["body_sha256"])

    def test_stale_pins_are_rejected(self):
        with self.assertRaisesRegex(INJECTOR.InjectorError, "PINS_INVALID"):
            self.fixture.load(now_ms=self.fixture.pins["expires_at_ms"])

    def test_pin_lifetime_must_cover_every_fault_recovery_window(self):
        pins = copy.deepcopy(self.fixture.pins)
        pins["expires_at_ms"] = BASE_WALL_MS + 1000
        pins = INJECTOR._seal({key: value for key, value in pins.items()
                               if key != "body_sha256"})
        self.fixture.pins_path.write_bytes(INJECTOR._canonical_bytes(pins))
        frozen = self.fixture.load()
        with self.assertRaisesRegex(
                INJECTOR.InjectorError, "PIN_LIFETIME_TOO_SHORT"):
            INJECTOR.run_campaign(
                frozen, FakeExecutor(), run_requested=False,
                effective_uid=self.fixture.uid,
                effective_gid=self.fixture.gid)
        self.assertEqual(list(self.fixture.journal.iterdir()), [])

    def test_explicit_source_pin_drift_is_rejected(self):
        with self.assertRaisesRegex(INJECTOR.InjectorError, "PIN_DRIFT"):
            self.fixture.load(
                expected_source_manifest_sha256=digest("wrong-source"))

    def test_persistent_lock_rejects_parallel_runner(self):
        frozen = self.fixture.load()
        lock_path = self.fixture.journal / ".injector.lock"
        descriptor = os.open(
            lock_path, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
        try:
            os.fchmod(descriptor, 0o600)
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
            with self.assertRaisesRegex(INJECTOR.InjectorError, "LOCK_BUSY"):
                INJECTOR.run_campaign(
                    frozen, FakeExecutor(), run_requested=False,
                    effective_uid=self.fixture.uid,
                    effective_gid=self.fixture.gid)
        finally:
            fcntl.flock(descriptor, fcntl.LOCK_UN)
            os.close(descriptor)

    def test_journal_tamper_is_detected_on_reopen(self):
        frozen = self.fixture.load()
        fake = FakeExecutor(crash_after_action=True)
        with self.assertRaises(INJECTOR.InjectedCrash):
            INJECTOR.run_campaign(
                frozen, fake, run_requested=False,
                effective_uid=self.fixture.uid,
                effective_gid=self.fixture.gid)
        first = self.fixture.journal / "00000001.json"
        document = json.loads(first.read_text(encoding="ascii"))
        document["recorded_at_ms"] += 1
        first.write_text(json.dumps(document), encoding="ascii")
        first.chmod(0o600)
        with self.assertRaises(INJECTOR.InjectorError):
            INJECTOR.run_campaign(
                frozen, fake, run_requested=False,
                effective_uid=self.fixture.uid,
                effective_gid=self.fixture.gid)

    def test_production_command_allowlist_rejects_arbitrary_command(self):
        with mock.patch.object(
                subprocess, "run",
                side_effect=AssertionError("must not be invoked")):
            with self.assertRaisesRegex(INJECTOR.InjectorError, "NOT_ALLOWLISTED"):
                INJECTOR.ProductionExecutor._run(("/bin/sh", "-c", "true"))

    def test_source_tree_copy_cannot_be_a_certifying_executing_image(self):
        with self.assertRaisesRegex(
                INJECTOR.InjectorError,
                "EXECUTING_IMAGE_(?:NOT_INSTALLED|DRIFT)"):
            INJECTOR.ProductionExecutor._verify_executing_image(
                digest("source-copy"))

    def test_observer_module_must_be_fixed_installed_pinned_image(self):
        with self.assertRaisesRegex(
                INJECTOR.InjectorError,
                "OBSERVER_IMAGE_(?:NOT_INSTALLED|DRIFT)"):
            INJECTOR.ProductionExecutor._verify_observer_image(
                digest("source-observer"))

    def test_pythonpath_shadow_observer_is_never_imported(self):
        shadow = self.fixture.root / "shadow"
        shadow.mkdir(mode=0o700)
        marker = self.fixture.root / "shadow-executed"
        malicious = shadow / "hepta_p1_safety_soak_independent_observer.py"
        malicious.write_text(
            "from pathlib import Path\n"
            f"Path({str(marker)!r}).write_text('executed')\n",
            encoding="ascii")
        code = (
            "import importlib.util,sys\n"
            "spec=importlib.util.spec_from_file_location('isolated_injector',"
            "sys.argv[1])\n"
            "module=importlib.util.module_from_spec(spec)\n"
            "sys.modules[spec.name]=module\n"
            "spec.loader.exec_module(module)\n"
            "print(module.OBSERVER.__file__)\n")
        environment = dict(os.environ)
        environment["PYTHONPATH"] = str(shadow)
        result = subprocess.run(
            [sys.executable, "-c", code, str(
                SCRIPTS / "hepta_p1_safety_soak_root_fault_injector.py")],
            check=False, capture_output=True, text=True,
            env=environment, cwd=str(ROOT), timeout=30)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertFalse(marker.exists())
        self.assertEqual(
            Path(result.stdout.strip()).resolve(),
            (SCRIPTS /
             "hepta_p1_safety_soak_independent_observer.py").resolve())

    def test_broker_observation_must_match_frozen_helper_pin(self):
        frozen = self.fixture.load()

        class Host:
            @staticmethod
            def broker():
                value = broker(BASE_BOOT_NS)
                body = {key: item for key, item in value.items()
                        if key != "state_sha256"}
                body["helper_file_sha256"] = digest("tampered-helper")
                return state(body)

        production = object.__new__(INJECTOR.ProductionExecutor)
        production.frozen = frozen
        production.host = Host()
        with self.assertRaisesRegex(
                INJECTOR.InjectorError, "BROKER_HELPER_DRIFT"):
            production._broker()

    def test_initial_fixture_publication_is_noreplace(self):
        target = self.fixture.root / "bounded-fixture.json"
        old = INJECTOR._seal({"old": True, **INJECTOR._boundary()})
        new = INJECTOR._seal({"new": True, **INJECTOR._boundary()})
        target.write_bytes(INJECTOR._canonical_bytes(old))
        target.chmod(0o600)
        production = object.__new__(INJECTOR.ProductionExecutor)
        with mock.patch.object(INJECTOR, "ROOT_UID", self.fixture.uid), \
                mock.patch.object(INJECTOR, "ROOT_GID", self.fixture.gid):
            with self.assertRaises(Exception):
                production._write_fixture(target, new, replace=False)
        self.assertEqual(
            json.loads(target.read_text(encoding="ascii")), old)

    def test_fixture_replace_binds_secure_reopened_old_body(self):
        target = self.fixture.root / "bounded-fixture.json"
        old = INJECTOR._seal({"old": True, **INJECTOR._boundary()})
        wrong = INJECTOR._seal({"wrong": True, **INJECTOR._boundary()})
        new = INJECTOR._seal({"new": True, **INJECTOR._boundary()})
        target.write_bytes(INJECTOR._canonical_bytes(old))
        target.chmod(0o600)
        production = object.__new__(INJECTOR.ProductionExecutor)
        with mock.patch.object(INJECTOR, "ROOT_UID", self.fixture.uid), \
                mock.patch.object(INJECTOR, "ROOT_GID", self.fixture.gid):
            with self.assertRaisesRegex(
                    INJECTOR.InjectorError, "OLD_BODY_DRIFT"):
                production._write_fixture(
                    target, new, replace=True, expected_old=wrong)
        self.assertEqual(
            json.loads(target.read_text(encoding="ascii")), old)

    def test_clock_step_is_bounded_isolated_and_never_host_clock(self):
        source = (SCRIPTS /
                  "hepta_p1_safety_soak_root_fault_injector.py").read_text(
                      encoding="utf-8")
        self.assertEqual(INJECTOR.CLOCK_FIXTURE_DELTA_MS, 5000)
        self.assertIn("INJECT_ISOLATED_CLOCK_DETECTOR_FIXTURE", source)
        self.assertIn("host_clock_mutated", source)
        for forbidden in (
            "clock_settime", "timedatectl", "date --set", "date -s",
        ):
            self.assertNotIn(forbidden, source)


if __name__ == "__main__":
    unittest.main()
