#!/usr/bin/env python3
"""Hermetic tests for the fixed P1 fault-pin producer."""

from __future__ import annotations

import copy
from datetime import datetime
import importlib.util
import os
from pathlib import Path
import stat
import sys
import unittest
from zoneinfo import ZoneInfo


ROOT = Path(__file__).resolve().parents[1]
PATH = ROOT / "scripts" / "hepta_p1_safety_soak_fault_pin_producer.py"
SPEC = importlib.util.spec_from_file_location("p1_fault_pin_producer", PATH)
assert SPEC is not None and SPEC.loader is not None
PIN = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = PIN
SPEC.loader.exec_module(PIN)
O = PIN.OBSERVER
I = PIN.INJECTOR

BOOT_ID = "12345678-1234-1234-1234-123456789abc"
CAMPAIGN_ID = "p1-safety-soak-round95"
FORMAL_ID = "formal-round1"
NOW_MS = 1_800_000_000_000
TRADING_DAYS = [
    "2026-08-03", "2026-08-04", "2026-08-05", "2026-08-06",
    "2026-08-07", "2026-08-10", "2026-08-11", "2026-08-12",
    "2026-08-13", "2026-08-14",
]
TRADING_ZONE = ZoneInfo(O.TRADING_TIMEZONE)
ELIGIBLE_SCHEDULE = [
    int(datetime.fromisoformat(day + "T10:00:00")
        .replace(tzinfo=TRADING_ZONE).timestamp() * 1000) + offset * 60_000
    for day in TRADING_DAYS for offset in range(20)
]


def digest(label: str) -> str:
    return PIN.digest_bytes(label.encode("ascii"))


def snapshot(path: str, document: dict) -> object:
    payload = PIN.canonical_bytes(document)
    metadata = os.stat_result((
        stat.S_IFREG | 0o600, 1, 1, 1, 0, 0, len(payload), 1, 1, 1))
    return O.Snapshot(
        Path(path), payload, metadata, document,
        PIN.digest_bytes(payload), document["body_sha256"])


def reference(value: object) -> dict[str, str]:
    return {
        "path": str(value.path), "file_sha256": value.file_sha256,
        "body_sha256": value.body_sha256,
    }


def fixture() -> dict:
    source_pins = [{
        "role": role, "source_path": source,
        "installed_path": installed, "file_sha256": digest("source-" + role),
    } for role, (source, installed) in sorted(PIN.SOURCE_PRODUCER_PATHS.items())]
    freeze_document = I._seal({
        "schema": PIN.FREEZE_BUNDLE_SCHEMA, "version": 1,
        "status": "FROZEN", "production_mode": PIN.FREEZER_MODE,
        "producer": {
            "path": PIN.SOURCE_PRODUCER_PATHS["campaign_freezer"][1],
            "file_sha256": next(item["file_sha256"] for item in source_pins
                                if item["role"] == "campaign_freezer"),
        },
        "source_producer_pins": source_pins,
        "issued_at_ms": NOW_MS - 1000,
        "expires_at_ms": NOW_MS + 30 * 24 * 60 * 60 * 1000,
        **I._boundary(),
    })
    freeze = snapshot("/evidence/freeze-bundle.json", freeze_document)
    faults = [{
        "fault_id": "fault-process-kill", "fault_type": "PROCESS_KILL",
        "target_id": O.FAULT_TARGET_IDS["PROCESS_KILL"],
        "formal_campaign_id": FORMAL_ID,
        "inject_at_boottime_ns": 10_000_000_000,
        "maximum_injection_lateness_ns": 5_000_000_000,
        "maximum_recovery_ns": 10_000_000_000,
    }]
    plan_document = I._seal({
        "schema": O.FAULT_PLAN_SCHEMA, "version": 1,
        "campaign_id": CAMPAIGN_ID,
        "source_manifest_sha256": digest("source-manifest"),
        "policy_sha256": digest("policy"),
        "strategy_sha256": digest("strategy"),
        "planned_faults": faults, **I._boundary(),
    })
    plan = snapshot("/evidence/fault-plan.json", plan_document)
    spec_document = I._seal({
        "schema": O.SPEC_SCHEMA, "version": 1,
        "campaign_id": CAMPAIGN_ID, "domain_id": "alpha",
        "source_manifest_sha256": digest("source-manifest"),
        "policy_sha256": digest("policy"), "strategy_id": "strategy-v1",
        "strategy_version": "v1", "strategy_sha256": digest("strategy"),
        "formal_campaigns": [{
            "campaign_id": FORMAL_ID, "campaign_sha256": digest("campaign"),
            "policy_body_sha256": digest("policy-body"),
            "policy_file_sha256": digest("policy-file"),
        }],
        "declared_trading_days": list(TRADING_DAYS),
        "trading_timezone": O.TRADING_TIMEZONE,
        "trading_calendar_sha256": digest("calendar"),
        "eligible_scheduled_at_ms": list(ELIGIBLE_SCHEDULE),
        "scheduled_decision_count": O.MINIMUM_ELIGIBLE_DECISIONS,
        "minimum_eligible_decisions": O.MINIMUM_ELIGIBLE_DECISIONS,
        "minimum_complete_ppm": O.MINIMUM_COMPLETE_PPM,
        "minimum_boottime_duration_ns": O.MINIMUM_BOOTTIME_DURATION_NS,
        "maximum_checkpoint_gap_ns": O.MAXIMUM_CHECKPOINT_GAP_NS,
        "maximum_decision_lateness_ms": O.MAXIMUM_DECISION_LATENESS_MS,
        "fault_plan_body_sha256": plan.body_sha256,
        "independent_auditor_id": "independent-auditor",
        "frozen_at_ms": NOW_MS - 1000,
        "freeze_bundle": reference(freeze), **I._boundary(),
    })
    spec = snapshot("/evidence/campaign-spec.json", spec_document)
    slot_interval_ms = PIN.POLICY_SLOT_INTERVAL_MS
    maximum_iterations = PIN.POLICY_MAXIMUM_ITERATIONS
    valid_after_ms = (
        (NOW_MS + PIN.LAUNCHER_WARMUP_MS +
         PIN.LAUNCHER_EARLY_START_LEAD_MS) // slot_interval_ms + 1
    ) * slot_interval_ms
    expires_at_ms = valid_after_ms + slot_interval_ms * maximum_iterations
    formal_entry = {
        "formal_campaign_id": FORMAL_ID, "probe_campaign_id": "probe-round1",
        "launcher_start_ms": valid_after_ms - PIN.LAUNCHER_WARMUP_MS,
        "valid_after_ms": valid_after_ms,
        "slot_interval_ms": slot_interval_ms,
        "maximum_iterations": maximum_iterations,
        "expires_at_ms": expires_at_ms,
        "launcher_completion_deadline_ms":
            expires_at_ms + PIN.MAXIMUM_LAUNCH_LATENESS_MS,
        "projection_deadline_ms":
            expires_at_ms + PIN.POST_FORMAL_PROJECTION_GUARD_MS,
        "teardown_deadline_ms":
            expires_at_ms + PIN.POST_FORMAL_TEARDOWN_GUARD_MS,
        "policy": {
            "path": "/evidence/policy.json",
            "file_sha256": digest("policy-file"),
            "body_sha256": digest("policy-body"),
        },
        "launcher_receipt_path": "/evidence/launcher.json",
        "verified_closure_path": "/evidence/closure.json",
        "artifact_root": "/evidence/formal-round1",
    }
    runtime_document = I._seal({
        "schema": PIN.RUNTIME_SCHEMA, "version": 1, "status": "FROZEN",
        "campaign_id": CAMPAIGN_ID, "round": 114, "boot_id": BOOT_ID,
        "issued_at_ms": NOW_MS - 500,
        "expires_at_ms": NOW_MS + 20 * 24 * 60 * 60 * 1000,
        "freeze_bundle": reference(freeze), "campaign_spec": reference(spec),
        "fault_plan": reference(plan), "pin_formal_campaign_id": FORMAL_ID,
        "formal_campaigns": [formal_entry], "observer_cadence_ms": 1000,
        "maximum_slot_lateness_ms": 900000,
        "state_root": "/var/lib/hepta/p1-safety-soak/campaign",
        "raw_observation_directory": "/var/lib/hepta/p1/raw",
        "recorder_root": "/var/lib/hepta/p1/recorder",
        "injector_journal_directory": "/var/lib/hepta/p1/journal",
        "injector_output_directory": "/var/lib/hepta/p1/output",
        "control_directory": "/run/hepta/p1",
        "executables": {
            item["role"]: {
                "path": item["installed_path"],
                "file_sha256": item["file_sha256"],
            } for item in source_pins
        },
        **I._boundary(),
    })
    runtime = snapshot("/evidence/runtime.json", runtime_document)
    units = I._unit_names(FORMAL_ID)
    observer_argv = [
        str(PIN.ROLE_ENTRYPOINTS["OBSERVER_PROCESS"]), "--run",
        "--runtime-manifest", str(runtime.path),
        "--expected-runtime-manifest-file-sha256", runtime.file_sha256,
    ]
    recorder_argv = [
        str(PIN.ROLE_ENTRYPOINTS["RECORDER_PROCESS"]), "--run",
        "--runtime-manifest", str(runtime.path),
        "--expected-runtime-manifest-file-sha256", runtime.file_sha256,
    ]
    inspections = []
    for role in sorted(I.UNIT_ROLES):
        argv = observer_argv if role == "OBSERVER_PROCESS" else \
            recorder_argv if role == "RECORDER_PROCESS" else None
        fragment = (f"[Service]\nExecStart={PIN.shlex.join(argv)}\n"
                    if argv is not None else "[Service]\nExecStart=/fixed\n")
        inspections.append(PIN.UnitInspection(
            role=role, unit=units[role],
            fragment_path=(Path("/run/systemd/transient") /
                           units[role]
                           if role in {"OBSERVER_PROCESS", "RECORDER_PROCESS"}
                           else Path("/usr/lib/systemd/system") / units[role]),
            fragment_payload=fragment.encode("ascii"),
            executable_payload=("python-" + role).encode("ascii"),
            entrypoint_path=PIN.ROLE_ENTRYPOINTS[role],
            entrypoint_payload=("entrypoint-" + role).encode("ascii"),
            exec_start="systemd-show-" + role))
    producer = {
        "path": str(PIN.INSTALLED_EXECUTABLE),
        "file_sha256": next(item["file_sha256"] for item in source_pins
                            if item["role"] == "fault_pin_producer"),
    }
    return {
        "spec_snapshot": spec, "plan_snapshot": plan,
        "freeze_snapshot": freeze, "runtime_snapshot": runtime,
        "formal_campaign_id": FORMAL_ID, "boot_id": BOOT_ID,
        "producer": producer, "inspections": inspections,
        "observer_argv": observer_argv, "recorder_argv": recorder_argv,
        "broker_helper_file_sha256": digest("broker-helper"),
        "now_ms": NOW_MS,
    }


class FaultPinProducerTests(unittest.TestCase):
    def test_builds_exact_injector_contract(self) -> None:
        values = fixture()
        result = PIN.build_pins(**values)
        self.assertEqual(result["schema"], I.PINS_SCHEMA)
        self.assertEqual(result["production_mode"], PIN.PRODUCTION_MODE)
        self.assertEqual(result["freeze_bundle"],
                         reference(values["freeze_snapshot"]))
        self.assertEqual(len(result["unit_contracts"]), 4)
        I.validate_pins(
            result, values["spec_snapshot"], values["plan_snapshot"],
            values["spec_snapshot"].document, now_ms=NOW_MS, boot_id=BOOT_ID)

    def test_manual_pin_producer_is_rejected(self) -> None:
        values = fixture()
        values["producer"] = {
            "path": "/tmp/manual", "file_sha256": digest("manual")}
        with self.assertRaisesRegex(PIN.PinError, "BUILD_INVALID"):
            PIN.build_pins(**values)

    def test_runtime_freeze_reference_drift_is_rejected(self) -> None:
        values = fixture()
        runtime = values["runtime_snapshot"]
        body = copy.deepcopy(runtime.document)
        body.pop("body_sha256")
        body["freeze_bundle"]["body_sha256"] = digest("drift")
        values["runtime_snapshot"] = snapshot(
            runtime.path.as_posix(), I._seal(body))
        with self.assertRaisesRegex(PIN.PinError, "RUNTIME_MANIFEST_INVALID"):
            PIN.build_pins(**values)

    def test_worker_fragment_must_bind_exact_argv(self) -> None:
        values = fixture()
        inspections = list(values["inspections"])
        index = next(index for index, item in enumerate(inspections)
                     if item.role == "OBSERVER_PROCESS")
        first = inspections[index]
        inspections[index] = PIN.UnitInspection(
            **{**first.__dict__, "fragment_payload": b"[Service]\nExecStart=/wrong\n"})
        values["inspections"] = inspections
        with self.assertRaisesRegex(PIN.PinError, "UNIT_CONTRACT_INVALID"):
            PIN.build_pins(**values)

    def test_duplicate_unit_role_is_rejected(self) -> None:
        values = fixture()
        values["inspections"] = [
            values["inspections"][0], *values["inspections"][:-1]]
        with self.assertRaisesRegex(PIN.PinError, "BUILD_INVALID"):
            PIN.build_pins(**values)

    def test_expired_runtime_cannot_issue_pins(self) -> None:
        values = fixture()
        values["now_ms"] = NOW_MS + 25 * 24 * 60 * 60 * 1000
        with self.assertRaisesRegex(PIN.PinError, "BUILD_INVALID"):
            PIN.build_pins(**values)

    def test_cli_requires_explicit_run(self) -> None:
        required = [
            "--campaign-spec", "/missing/spec", "--fault-plan", "/missing/plan",
            "--freeze-bundle", "/missing/freeze", "--runtime-manifest",
            "/missing/runtime", "--formal-campaign-id", FORMAL_ID,
            "--observer-unit", "observer", "--recorder-unit", "recorder",
            "--observer-exec-argv-json", "/missing/observer-argv",
            "--recorder-exec-argv-json", "/missing/recorder-argv",
        ]
        for name in (
            "expected-observer-argv-file-sha256",
            "expected-recorder-argv-file-sha256",
            "expected-spec-file-sha256", "expected-spec-body-sha256",
            "expected-plan-file-sha256", "expected-plan-body-sha256",
            "expected-freeze-file-sha256", "expected-freeze-body-sha256",
            "expected-runtime-file-sha256", "expected-runtime-body-sha256",
            "expected-source-manifest-sha256",
        ):
            required.extend(("--" + name, digest(name)))
        required.extend(("--boot-id", BOOT_ID, "--output", "/missing/output"))
        self.assertEqual(PIN.main(required), 2)


if __name__ == "__main__":
    unittest.main()
