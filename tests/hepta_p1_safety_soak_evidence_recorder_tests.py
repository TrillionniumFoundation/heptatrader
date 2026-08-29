#!/usr/bin/env python3

"""Offline fake-evidence tests for the P1 safety-soak recorder."""

from __future__ import annotations

import copy
from datetime import date, datetime, timedelta, timezone
import importlib.util
import os
from pathlib import Path
import stat
import sys
import tempfile
import unittest
from unittest import mock
from zoneinfo import ZoneInfo


ROOT = Path(__file__).resolve().parents[1]


def import_script(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


RECORDER = import_script(
    "hepta_p1_safety_soak_evidence_recorder",
    ROOT / "scripts/hepta_p1_safety_soak_evidence_recorder.py")
AUDITOR = import_script(
    "hepta_p1_safety_soak_auditor_for_recorder_tests",
    ROOT / "scripts/hepta_p1_safety_soak_auditor.py")

CAMPAIGN_ID = "p1-safety-soak-round95"
DOMAIN_ID = "alpha"
STRATEGY_ID = "eurusd-confirmed-momentum"
STRATEGY_VERSION = "v2"
BOOT_ID = "00000000-0000-0000-0000-000000000001"
SOURCE_SHA = RECORDER.digest_bytes(b"frozen-source")
POLICY_SHA = RECORDER.digest_bytes(b"frozen-policy")
STRATEGY_SHA = RECORDER.digest_bytes(b"frozen-strategy")
START_BOOTTIME_NS = 1_000_000_000_000
NOW_MS = int(datetime(
    2026, 8, 2, 12, 0, tzinfo=timezone.utc).timestamp() * 1000)
GAP_NS = 15 * 60 * 1_000_000_000
FORMAL_SEGMENTS = 22


def digest(label: str) -> str:
    return RECORDER.digest_bytes(label.encode("ascii"))


def predecessor_activation_success(module=RECORDER) -> dict[str, object]:
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


def predecessor_activation_failure(module=RECORDER) -> dict[str, object]:
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


def state_seal(body: dict) -> dict:
    return {
        **body,
        "state_sha256": RECORDER.digest_bytes(RECORDER.canonical_bytes(body)),
    }


def identity_lists(observed_boottime_ns: int) -> dict:
    return {
        "systemd_units": [state_seal({
            "unit": "hepta-test-observer.service", "load_state": "loaded",
            "active_state": "active", "sub_state": "running",
            "unit_file_state": "transient", "main_pid": 1000,
            "invocation_id": "0" * 32,
            "exec_main_start_timestamp_monotonic_us":
                observed_boottime_ns // 1000,
            "n_restarts": 0,
        })],
        "processes": [state_seal({
            "pid": 1000, "uid": 0, "gid": 0, "starttime_ticks": 100,
            "exe_device": 1, "exe_inode": 2,
            "cgroup_sha256": digest("observer-cgroup"),
        })],
        "paths": [state_seal({
            "path": "/run/hepta-test-observer-state", "present": False,
            "parent_device": 1, "parent_inode": 2, "parent_uid": 0,
            "parent_gid": 0, "parent_mode": 0o700, "parent_nlink": 2,
            "file_type": None, "device": None, "inode": None,
            "uid": None, "gid": None, "mode": None, "nlink": None,
            "size": None, "mtime_ns": None, "ctime_ns": None,
            "content_file_sha256": None, "content_body_sha256": None,
        })],
        "broker_deny_all": state_seal({
            "helper_path": "/usr/libexec/hepta-broker-egress-policy",
            "helper_file_sha256": digest("broker-helper"),
            "policy_sha256": digest("broker-policy"),
            "authorized_connector_count": 0, "authorized_uids": [],
            "protected_port_count": 2, "deny_all": True,
            "checked_boottime_ns": observed_boottime_ns,
        }),
    }


def observation_evidence(
    kind: str, boot_id: str, observed_boottime_ns: int,
    fault_injection_receipt: dict | None = None,
) -> dict:
    return RECORDER.seal({
        "schema": RECORDER.OBSERVATION_EVIDENCE_SCHEMA, "version": 1,
        "kind": kind, "boot_id": boot_id,
        "observed_boottime_ns": observed_boottime_ns,
        **identity_lists(observed_boottime_ns),
        "fault_injection_receipt": fault_injection_receipt,
    })


def fault_target_identity(
    phase: str, target_id: str, fault_type: str, observed_boottime_ns: int,
    boot_id: str,
) -> dict:
    post = phase == "POST"
    identities = identity_lists(observed_boottime_ns)
    if fault_type in {"SERVICE_RESTART", "NETWORK_DENY_RELOAD"}:
        unit = dict(identities["systemd_units"][0])
        unit.pop("state_sha256")
        unit["unit"] = (
            "hepta-tool-gateway@alpha.service"
            if fault_type == "SERVICE_RESTART" else
            "hepta-broker-egress-policy.service")
        identities["systemd_units"] = [state_seal(unit)]
    if fault_type in {"PROCESS_KILL", "EVIDENCE_WRITER_CRASH"} and post:
        process = dict(identities["processes"][0])
        process.pop("state_sha256")
        process.update({"pid": 1001, "starttime_ticks": 101})
        identities["processes"] = [state_seal(process)]
    if fault_type in {"SERVICE_RESTART", "NETWORK_DENY_RELOAD"} and post:
        unit = dict(identities["systemd_units"][0])
        unit.pop("state_sha256")
        unit.update({
            "main_pid": 1001, "invocation_id": "1" * 32,
            "exec_main_start_timestamp_monotonic_us":
                observed_boottime_ns // 1000,
        })
        identities["systemd_units"] = [state_seal(unit)]
    fixture_generation = None
    fixture_expiry = None
    fixture_valid = None
    if fault_type in RECORDER.FAULT_FIXTURE_PATHS:
        fixture = state_seal({
            "path": RECORDER.FAULT_FIXTURE_PATHS[fault_type],
            "present": True, "parent_device": 1, "parent_inode": 2,
            "parent_uid": 0, "parent_gid": 0, "parent_mode": 0o700,
            "parent_nlink": 2, "file_type": "regular", "device": 1,
            "inode": 11 if post else 10, "uid": 0, "gid": 0,
            "mode": 0o100600, "nlink": 1, "size": 100,
            "mtime_ns": observed_boottime_ns,
            "ctime_ns": observed_boottime_ns,
            "content_file_sha256": digest(
                f"{fault_type}-fixture-file-{phase}"),
            "content_body_sha256": digest(
                f"{fault_type}-fixture-body-{phase}"),
        })
        identities["paths"] = [fixture]
        fixture_generation = 2 if post else 1
        fixture_expiry = observed_boottime_ns + (
            60 * 1_000_000_000 if post or fault_type == "TOKEN_LOSS" else 1)
        fixture_valid = True
    if fault_type == "CLOCK_STEP":
        unit = dict(identities["systemd_units"][0])
        unit.pop("state_sha256")
        unit["exec_main_start_timestamp_monotonic_us"] = \
            START_BOOTTIME_NS // 1000
        identities["systemd_units"] = [state_seal(unit)]
    return RECORDER.seal({
        "schema": RECORDER.FAULT_TARGET_IDENTITY_SCHEMA, "version": 1,
        "phase": phase, "target_id": target_id, "boot_id": boot_id,
        "observed_boottime_ns": observed_boottime_ns,
        "service_epoch": (
            "epoch-2" if fault_type == "SERVICE_RESTART" and post else
            "epoch-1"),
        "fencing_generation": 7, "lease_generation": 11,
        **identities, "residue_count": 0,
        "wall_clock_delta_ms": (
            100 if fault_type == "CLOCK_STEP" and post else
            0 if fault_type == "CLOCK_STEP" else None),
        "fixture_generation": fixture_generation,
        "fixture_expires_boottime_ns": fixture_expiry,
        "fixture_valid": fixture_valid,
    })


class FakeClock:
    def __init__(self) -> None:
        self.wall_ms = NOW_MS
        self.boottime_ns = START_BOOTTIME_NS
        self.boot_id = BOOT_ID

    def __call__(self) -> RECORDER.ClockSample:
        return RECORDER.ClockSample(
            self.wall_ms, self.boottime_ns, self.boot_id)

    def advance(self, nanoseconds: int) -> None:
        self.boottime_ns += nanoseconds
        self.wall_ms += nanoseconds // 1_000_000


def trading_days() -> list[str]:
    result: list[str] = []
    cursor = date(2026, 8, 3)
    while len(result) < RECORDER.MINIMUM_TRADING_DAYS:
        if (cursor.weekday() < 5 and cursor.isoformat() not in
                RECORDER.CALENDAR_EXCLUDED_DAYS_2026):
            result.append(cursor.isoformat())
        cursor += timedelta(days=1)
    return result


class Fixture:
    def __init__(self, temporary: str):
        self.base = Path(temporary).resolve()
        self.root = self.base / "recorder"
        self.inputs = self.base / "inputs"
        self.root.mkdir(mode=0o700)
        self.inputs.mkdir(mode=0o700)
        self.uid = os.geteuid()
        self.gid = os.getegid()
        self.clock = FakeClock()
        self.recorder = RECORDER.Recorder(
            self.root, expected_uid=self.uid, clock=self.clock)
        self.paths: dict[str, Path] = {}
        self.formal_documents: list[dict] = []
        self.formal_paths: list[Path] = []
        self.eligible_schedule: list[int] = []
        self.all_slots: list[int] = []
        first_valid_after = int(datetime(
            2026, 8, 3, 9, 0,
            tzinfo=ZoneInfo(RECORDER.CALENDAR_TIMEZONE)).timestamp() * 1000)
        self.fault_injection = (
            START_BOOTTIME_NS + (first_valid_after - NOW_MS) * 1_000_000 +
            2 * 60 * 60 * 1_000_000_000)
        fault_types = [
            "EVIDENCE_WRITER_CRASH", "PROCESS_KILL", "SERVICE_RESTART",
            "TOKEN_LOSS", "LEASE_EXPIRY", "NETWORK_DENY_RELOAD",
            "CLOCK_STEP",
        ]
        self.planned_faults = [{
            "fault_id": f"fault-{index}",
            "fault_type": fault_type,
            "target_id": RECORDER.FAULT_TARGET_IDS[fault_type],
            "formal_campaign_id": "formal-01",
            "inject_at_boottime_ns":
                self.fault_injection + (index - 1) * 2 * 60 *
                    1_000_000_000,
            "maximum_injection_lateness_ns": 5 * 1_000_000_000,
            "maximum_recovery_ns": 60 * 1_000_000_000,
        } for index, fault_type in enumerate(fault_types, start=1)]
        self._build_freeze_inputs()

    def write(
        self, name: str, document: dict, *, sealed: bool = True,
    ) -> Path:
        path = self.inputs / f"{name}.json"
        value = RECORDER.seal(document) if sealed else document
        path.write_bytes(RECORDER.canonical_bytes(value))
        path.chmod(0o600)
        self.paths[name] = path
        return path

    def rewrite(self, path: Path, document: dict, *, sealed: bool = True) -> None:
        value = RECORDER.seal(document) if sealed else document
        path.write_bytes(RECORDER.canonical_bytes(value))
        path.chmod(0o600)

    @staticmethod
    def boundary() -> dict[str, bool]:
        return {
            "paper_authorized": False,
            "live_authorized": False,
            "mutation_authorized": False,
            "direct_broker_access": False,
        }

    def _build_freeze_inputs(self) -> None:
        days = trading_days()
        freeze_id = "a" * 32
        freezer_producer = {
            "path": str(RECORDER.FREEZER_EXECUTABLE),
            "file_sha256": digest("source-campaign_freezer"),
        }
        freezer_common = {
            "freeze_id": freeze_id, "producer": freezer_producer,
            "production_mode": RECORDER.FREEZER_PRODUCTION_MODE,
        }
        last_day = date.fromisoformat(days[-1])
        retention_expiry = int(datetime(
            last_day.year, last_day.month, last_day.day, 23, 0,
            tzinfo=timezone.utc).timestamp() * 1000) + 7 * 24 * 60 * 60 * 1000
        self.write("source-anchor", {
            "schema": RECORDER.SOURCE_ANCHOR_SCHEMA,
            "version": 1, "status": "FROZEN",
            "source_manifest_sha256": SOURCE_SHA,
            "source_frozen": True, "clean_source": True,
            "frozen_at_ms": NOW_MS, "expires_at_ms": retention_expiry,
            **freezer_common, **self.boundary(),
        })
        self.write("policy-anchor", {
            "schema": RECORDER.POLICY_ANCHOR_SCHEMA,
            "version": 1, "status": "FROZEN",
            "policy_sha256": POLICY_SHA, "policy_frozen": True,
            "frozen_at_ms": NOW_MS, "expires_at_ms": retention_expiry,
            **freezer_common, **self.boundary(),
        })
        self.write("strategy-anchor", {
            "schema": RECORDER.STRATEGY_ANCHOR_SCHEMA,
            "version": 1, "status": "FROZEN",
            "strategy_id": STRATEGY_ID,
            "strategy_version": STRATEGY_VERSION,
            "strategy_sha256": STRATEGY_SHA, "strategy_frozen": True,
            "frozen_at_ms": NOW_MS, "expires_at_ms": retention_expiry,
            **freezer_common, **self.boundary(),
        })

        interval = RECORDER.POLICY_SLOT_INTERVAL_MS
        maximum = RECORDER.POLICY_MAXIMUM_ITERATIONS
        valid_after = int(datetime(
            2026, 8, 3, 9, 0,
            tzinfo=ZoneInfo(RECORDER.CALENDAR_TIMEZONE)).timestamp() * 1000)
        previous_teardown = 0
        for day_index in range(FORMAL_SEGMENTS):
            if previous_teardown:
                valid_after = (
                    (previous_teardown + RECORDER.LAUNCHER_WARMUP_MS +
                     RECORDER.LAUNCHER_EARLY_START_LEAD_MS) // interval + 1
                ) * interval
            campaign_id = f"formal-{day_index + 1:02d}"
            expires = valid_after + maximum * interval
            campaign_binding = {
                "schema": "hepta.strategy-shadow-observation-campaign.v1",
                "campaign_id": campaign_id,
                "valid_after_ms": valid_after,
                "expires_at_ms": expires,
                "slot_interval_ms": interval,
                "maximum_iterations": maximum,
                "maximum_lateness_ms": RECORDER.POLICY_MAXIMUM_LATENESS_MS,
                "shadow_only": True,
                "paper_authorized": False,
                "live_authorized": False,
                "mutation_attempted": False,
                "direct_broker_access": False,
            }
            policy = RECORDER.seal({
                "schema": RECORDER.FORMAL_POLICY_SCHEMA,
                "version": 1, "campaign_id": campaign_id,
                "campaign_sha256": RECORDER.digest_bytes(
                    RECORDER.canonical_bytes(campaign_binding)),
                "strategy_id": STRATEGY_ID,
                "strategy_version": STRATEGY_VERSION,
                "strategy_sha256": STRATEGY_SHA,
                "valid_after_ms": valid_after,
                "expires_at_ms": expires,
                "slot_interval_ms": interval,
                "maximum_iterations": maximum,
                "maximum_lateness_ms": RECORDER.POLICY_MAXIMUM_LATENESS_MS,
                "shadow_only": True,
                "paper_authorized": False,
                "live_authorized": False,
                "mutation_attempted": False,
                "direct_broker_access": False,
            })
            path = self.inputs / f"formal-policy-{day_index:02d}.json"
            path.write_bytes(RECORDER.canonical_bytes(policy))
            path.chmod(0o600)
            self.formal_documents.append(policy)
            self.formal_paths.append(path)
            slots = [valid_after + offset * interval
                     for offset in range(maximum)]
            self.all_slots.extend(slots)
            previous_teardown = (
                expires + RECORDER.POST_FORMAL_TEARDOWN_GUARD_MS)

        sessions = RECORDER._expected_calendar_sessions(
            self.all_slots, "TEST_CALENDAR_INVALID")
        self.eligible_schedule = [
            slot for slot in self.all_slots
            if any(
                session["opens_at_ms"] <= slot < session["closes_at_ms"] and
                not any(
                    window["opens_at_ms"] <= slot < window["closes_at_ms"]
                    for window in session["maintenance_windows"])
                for session in sessions)
        ]

        self.write("schedule", {
            "schema": RECORDER.SCHEDULE_SCHEMA,
            "version": 1, "status": "FROZEN",
            "campaign_id": CAMPAIGN_ID, "domain_id": DOMAIN_ID,
            "declared_trading_days": days,
            "trading_timezone": RECORDER.CALENDAR_TIMEZONE,
            "eligible_scheduled_at_ms": self.eligible_schedule,
            "minimum_eligible_decisions": 200,
            "minimum_complete_ppm": 990_001,
            "minimum_boottime_duration_ns":
                RECORDER.MINIMUM_BOOTTIME_DURATION_NS,
            "maximum_checkpoint_gap_ns": GAP_NS,
            "maximum_decision_lateness_ms":
                RECORDER.POLICY_MAXIMUM_LATENESS_MS,
            "independent_auditor_id": "independent-p1-auditor",
            "frozen_at_ms": NOW_MS, "expires_at_ms": retention_expiry,
            **freezer_common, **self.boundary(),
        })
        self.write("fault-schedule", {
            "schema": RECORDER.FAULT_SCHEDULE_SCHEMA,
            "version": 1, "status": "FROZEN",
            "campaign_id": CAMPAIGN_ID, "boot_id": BOOT_ID,
            "frozen_boottime_ns": START_BOOTTIME_NS,
            "planned_faults": self.planned_faults,
            "frozen_at_ms": NOW_MS, "expires_at_ms": retention_expiry,
            **freezer_common, **self.boundary(),
        })

        calendar = RECORDER.seal({
            "schema": RECORDER.CALENDAR_SCHEMA, "version": 1,
            "status": "FROZEN", "freeze_id": freeze_id,
            "producer": freezer_producer,
            "production_mode": RECORDER.FREEZER_PRODUCTION_MODE,
            "calendar_id": RECORDER.CALENDAR_ID,
            "calendar_version": RECORDER.CALENDAR_VERSION,
            "calendar_source_sha256": RECORDER._calendar_source_sha256(),
            "trading_timezone": RECORDER.CALENDAR_TIMEZONE,
            "sessions": sessions, "issued_at_ms": NOW_MS,
            "expires_at_ms": retention_expiry, **self.boundary(),
        })
        calendar_path = self.inputs / "reviewed-trading-calendar.json"
        calendar_path.write_bytes(RECORDER.canonical_bytes(calendar))
        calendar_path.chmod(0o600)
        self.paths["trading-calendar"] = calendar_path

        def reference(path: Path) -> dict[str, str]:
            payload = path.read_bytes()
            document, body_sha = RECORDER._strict_document(
                payload, "TEST_REFERENCE", sealed=True)
            del document
            return {
                "path": str(path),
                "file_sha256": RECORDER.digest_bytes(payload),
                "body_sha256": body_sha,
            }

        anchor_paths = {
            "source_anchor": self.paths["source-anchor"],
            "policy_anchor": self.paths["policy-anchor"],
            "strategy_anchor": self.paths["strategy-anchor"],
            "frozen_schedule": self.paths["schedule"],
            "frozen_fault_schedule": self.paths["fault-schedule"],
        }
        formal_records = []
        for document, path in zip(self.formal_documents, self.formal_paths):
            launcher_start = document["valid_after_ms"] - \
                RECORDER.LAUNCHER_WARMUP_MS
            expiry = document["expires_at_ms"]
            formal_records.append({
                "campaign_id": document["campaign_id"], **reference(path),
                "launcher_start_ms": launcher_start,
                "launcher_dispatch_at_ms": launcher_start -
                    RECORDER.LAUNCHER_EARLY_START_LEAD_MS,
                "valid_after_ms": document["valid_after_ms"],
                "expires_at_ms": expiry,
                "slot_interval_ms": document["slot_interval_ms"],
                "maximum_iterations": document["maximum_iterations"],
                "launcher_completion_deadline_ms":
                    expiry + RECORDER.MAXIMUM_LAUNCH_LATENESS_MS,
                "projection_deadline_ms":
                    expiry + RECORDER.POST_FORMAL_PROJECTION_GUARD_MS,
                "teardown_deadline_ms":
                    expiry + RECORDER.POST_FORMAL_TEARDOWN_GUARD_MS,
            })
        source_pins = [{
            "role": role, "source_path": source_path,
            "installed_path": installed_path,
            "file_sha256": (freezer_producer["file_sha256"]
                            if role == "campaign_freezer"
                            else digest("source-" + role)),
        } for role, (source_path, installed_path) in sorted(
            RECORDER.FREEZE_SOURCE_PRODUCER_PATHS.items())]
        strategy_files = [{
            "role": role, "path": f"/frozen/strategy/{role}",
            "file_sha256": digest("strategy-file-" + role),
            "body_sha256": digest("strategy-body-" + role),
        } for role in (
            "config", "evaluator", "context_builder", "normalizer",
            "contracts")]
        bundle = RECORDER.seal({
            "schema": RECORDER.FREEZE_BUNDLE_SCHEMA, "version": 1,
            "status": "FROZEN", "round": 114, "freeze_id": freeze_id,
            "issued_at_ms": NOW_MS, "expires_at_ms": retention_expiry,
            "campaign_id": CAMPAIGN_ID, "domain_id": DOMAIN_ID,
            "producer": freezer_producer,
            "production_mode": RECORDER.FREEZER_PRODUCTION_MODE,
            "boot_id": BOOT_ID, "frozen_boottime_ns": START_BOOTTIME_NS,
            "source_baseline": {
                "path": "/frozen/source-baseline.json",
                "file_sha256": digest("source-baseline-file"),
                "body_sha256": digest("source-baseline-body"),
            },
            "source_manifest_sha256": SOURCE_SHA,
            "source_producer_pins": source_pins,
            "policy_sha256": POLICY_SHA, "formal_policies": formal_records,
            "strategy_id": STRATEGY_ID,
            "strategy_version": STRATEGY_VERSION,
            "strategy_sha256": STRATEGY_SHA,
            "strategy_files": strategy_files,
            "trading_calendar": reference(calendar_path),
            "calendar_id": RECORDER.CALENDAR_ID,
            "calendar_version": RECORDER.CALENDAR_VERSION,
            "calendar_source_sha256": RECORDER._calendar_source_sha256(),
            "declared_trading_days": days,
            "trading_timezone": RECORDER.CALENDAR_TIMEZONE,
            "trading_calendar_sha256": calendar["body_sha256"],
            "eligible_scheduled_at_ms": self.eligible_schedule,
            "scheduled_decision_count": len(self.all_slots),
            "planned_faults": self.planned_faults,
            "anchors": {role: reference(path)
                        for role, path in anchor_paths.items()},
            **self.boundary(),
        })
        self.write("freeze-bundle", {
            key: value for key, value in bundle.items()
            if key != "body_sha256"
        })

    def freeze(self):
        return self.recorder.freeze(
            source_anchor_path=self.paths["source-anchor"],
            policy_anchor_path=self.paths["policy-anchor"],
            strategy_anchor_path=self.paths["strategy-anchor"],
            formal_policy_paths=self.formal_paths,
            schedule_path=self.paths["schedule"],
            fault_schedule_path=self.paths["fault-schedule"],
            freeze_bundle_path=self.paths["freeze-bundle"],
        )

    def lineage(self) -> dict:
        return {
            "source_manifest_sha256": SOURCE_SHA,
            "policy_sha256": POLICY_SHA,
            "strategy_sha256": STRATEGY_SHA,
        }

    def observer_header(self, schema: str, observer: str) -> dict:
        return {
            "schema": schema, "version": 1, "status": "COMPLETE",
            "observed_at_ms": self.clock.wall_ms,
            "expires_at_ms": self.clock.wall_ms + 60_000,
            "campaign_id": CAMPAIGN_ID, "observer_id": observer,
            "observation_complete": True, "clock_id": "CLOCK_BOOTTIME",
            "boot_id": self.clock.boot_id, **self.lineage(),
            "producer": {
                "path": str(RECORDER.OBSERVER_EXECUTABLE),
                "file_sha256": digest("source-independent_observer"),
            },
            "production_mode": RECORDER.OBSERVER_PRODUCTION_MODE,
            **self.boundary(),
        }

    def service_observation(
        self, name: str, *, epoch: str = "epoch-1", fence: int = 7,
        lease: int = 11, transition: str | None = None,
        continuity_ok: bool = True,
    ) -> Path:
        return self.write(name, {
            **self.observer_header(
                RECORDER.SERVICE_OBSERVATION_SCHEMA,
                "independent-service-observer"),
            "observed_boottime_ns": self.clock.boottime_ns,
            "service_epoch": epoch, "fencing_generation": fence,
            "lease_generation": lease, "transition_fault_id": transition,
            "continuity_ok": continuity_ok,
            "audit_ok": True, "cleanup_ok": True,
            "observation_evidence": observation_evidence(
                "SERVICE", self.clock.boot_id, self.clock.boottime_ns),
        })

    @staticmethod
    def continuity_unit(
        name: str, *, substate: str, pid: int = 0,
        invocation: str = "",
    ) -> dict:
        return state_seal({
            "unit": name, "load_state": "loaded", "active_state": "active",
            "sub_state": substate, "unit_file_state": "static",
            "main_pid": pid, "invocation_id": invocation,
            "exec_main_start_timestamp_monotonic_us":
                100 if pid else 0,
            "n_restarts": 0,
        })

    @staticmethod
    def continuity_path(
        path: str, *, file_type: str = "socket", inode: int = 10,
        file_sha256: str | None = None, body_sha256: str | None = None,
        uid: int = 0, gid: int = 0, mode: int = 0o660,
        parent_uid: int = 0, parent_gid: int = 0,
        parent_mode: int = 0o700,
    ) -> dict:
        return state_seal({
            "path": path, "present": True, "parent_device": 8,
            "parent_inode": 9, "parent_uid": parent_uid,
            "parent_gid": parent_gid,
            "parent_mode": parent_mode, "parent_nlink": 2,
            "file_type": file_type, "device": 8, "inode": inode,
            "uid": uid, "gid": gid, "mode": mode, "nlink": 1,
            "size": 0, "mtime_ns": 1, "ctime_ns": 1,
            "content_file_sha256": file_sha256,
            "content_body_sha256": body_sha256,
        })

    @staticmethod
    def artifact_reference(path: str, document: dict) -> dict:
        payload = RECORDER.canonical_bytes(document)
        return {
            "path": path, "file_sha256": RECORDER.digest_bytes(payload),
            "body_sha256": document["body_sha256"],
            "schema": document["schema"],
        }

    def continuity_lease(self, generation: int = 11) -> dict:
        previous_body = digest("lease-generation-10")
        if generation > 11:
            previous_body = self.continuity_lease(
                generation - 1)["body_sha256"]
        return RECORDER.seal({
            "schema": "hepta.shadow-watch-lease-receipt.v1", "version": 1,
            "domain_id": "alpha", "agent_id": "alpha", "agent_uid": 2104,
            "boundary": "WATCH", "operation": "ROTATE",
            "lease_generation": generation,
            "previous_lease_generation": generation - 1,
            "previous_receipt_body_sha256": previous_body,
            "accepted": True, "reason_code": "OK",
            "accepted_at_ms": NOW_MS - 1000, "ttl_seconds": 3_000_000,
            "expires_at_ms": NOW_MS + 30 * 24 * 60 * 60 * 1000,
            "paper_authorized": False, "live_authorized": False,
            "mutation_authorized": False,
        })

    def continuity_export(
        self, lease: dict, sequence: int,
    ) -> tuple[dict, dict, dict, dict, dict, list[dict]]:
        export_root = Path("/run/hepta-shadow-watch-export-alpha")
        generation = f"generation-{sequence:020d}-fixture{sequence:08d}"
        generation_root = export_root / "generations" / generation
        snapshot = RECORDER.seal({
            "schema": "hepta.shadow-watch-snapshot.v1", "version": 1,
            "domain_id": "alpha", "agent_uid": 2104,
            "generated_at_ms": self.clock.wall_ms - 2_000,
            "paper_authorized": False, "live_authorized": False,
            "mutation_attempted": False, "direct_broker_access": False,
        })
        snapshot_reference = self.artifact_reference(
            str(generation_root / "snapshot.json"), snapshot)
        lease_reference = self.artifact_reference(
            str(generation_root / "shadow-watch-lease-receipt.json"),
            lease)
        export_receipt = RECORDER.seal({
            "schema": "hepta.shadow-watch-export-receipt.v1", "version": 1,
            "domain_id": "alpha", "agent_uid": 2104,
            "reader_uid": self.uid, "reader_gid": self.gid,
            "lease_generation": lease["lease_generation"],
            "snapshot_body_sha256": snapshot["body_sha256"],
            "lease_receipt_body_sha256": lease["body_sha256"],
            "paper_authorized": False, "live_authorized": False,
            "mutation_attempted": False, "direct_broker_access": False,
        })
        export_reference = self.artifact_reference(
            str(generation_root / "shadow-watch-export-receipt.json"),
            export_receipt)
        committed_at_ms = self.clock.wall_ms - 500
        commit = RECORDER.seal({
            "schema": "hepta.shadow-watch-export-commit.v1", "version": 1,
            "authority_status": "ACTIVE",
            "authority_changed_at_ms": committed_at_ms,
            "close_reason": None, "commit_sequence": sequence,
            "generation": generation, "domain_id": "alpha",
            "agent_uid": 2104, "reader_uid": self.uid,
            "reader_gid": self.gid,
            "lease_generation": lease["lease_generation"],
            "snapshot_body_sha256": snapshot_reference["body_sha256"],
            "snapshot_file_sha256": snapshot_reference["file_sha256"],
            "lease_receipt_body_sha256": lease_reference["body_sha256"],
            "lease_receipt_file_sha256": lease_reference["file_sha256"],
            "export_receipt_body_sha256": export_reference["body_sha256"],
            "export_receipt_file_sha256": export_reference["file_sha256"],
            "committed_at_ms": committed_at_ms,
            "paper_authorized": False, "live_authorized": False,
            "mutation_attempted": False, "direct_broker_access": False,
        })
        commit_reference = self.artifact_reference(
            str(export_root / "current.json"), commit)
        references = (
            commit_reference, snapshot_reference, lease_reference,
            export_reference)
        identities = [
            self.continuity_path(
                reference["path"], file_type="regular", inode=30 + index,
                file_sha256=reference["file_sha256"],
                body_sha256=reference["body_sha256"], uid=0, gid=self.gid,
                mode=0o440, parent_uid=0, parent_gid=self.gid,
                parent_mode=0o750)
            for index, reference in enumerate(references)
        ]
        return (
            commit_reference, commit, snapshot_reference, lease_reference,
            export_reference, identities)

    def campaign_runtime(self) -> tuple[Path, dict]:
        existing = self.paths.get("campaign-runtime")
        if existing is not None:
            document, _body = RECORDER._strict_document(
                existing.read_bytes(), "TEST_RUNTIME", sealed=True)
            return existing, document
        spec = RECORDER.load_snapshot(
            self.recorder.spec_path, "campaign_spec",
            expected_uid=self.uid)
        plan = RECORDER.load_snapshot(
            self.recorder.fault_plan_path, "fault_plan",
            expected_uid=self.uid)
        freeze = RECORDER.load_snapshot(
            self.paths["freeze-bundle"], "freeze_bundle",
            expected_uid=self.uid)
        formal_by_id = {
            item["campaign_id"]: item
            for item in freeze.document["formal_policies"]
        }
        formals = []
        for index, (policy, policy_path) in enumerate(zip(
                self.formal_documents, self.formal_paths), start=1):
            frozen = formal_by_id[policy["campaign_id"]]
            formals.append({
                "formal_campaign_id": policy["campaign_id"],
                "probe_campaign_id": f"probe-{index:02d}",
                "launcher_start_ms": frozen["launcher_start_ms"],
                "launcher_dispatch_at_ms":
                    frozen["launcher_dispatch_at_ms"],
                "valid_after_ms": frozen["valid_after_ms"],
                "slot_interval_ms": frozen["slot_interval_ms"],
                "maximum_iterations": frozen["maximum_iterations"],
                "expires_at_ms": frozen["expires_at_ms"],
                "launcher_completion_deadline_ms":
                    frozen["launcher_completion_deadline_ms"],
                "projection_deadline_ms": frozen["projection_deadline_ms"],
                "teardown_deadline_ms": frozen["teardown_deadline_ms"],
                "policy": {
                    "path": str(policy_path),
                    "file_sha256": frozen["file_sha256"],
                    "body_sha256": frozen["body_sha256"],
                },
                "launcher_receipt_path": str(
                    self.inputs / f"launcher-{index:02d}.json"),
                "verified_closure_path": str(
                    self.inputs / f"closure-{index:02d}.json"),
                "artifact_root": str(self.inputs / f"formal-{index:02d}"),
            })
        runtime = RECORDER.seal({
            "schema": RECORDER.CAMPAIGN_RUNTIME_SCHEMA, "version": 1,
            "status": "FROZEN", "campaign_id": CAMPAIGN_ID, "round": 114,
            "boot_id": BOOT_ID, "issued_at_ms": NOW_MS,
            "expires_at_ms": freeze.document["expires_at_ms"],
            "freeze_bundle": copy.deepcopy(spec.document["freeze_bundle"]),
            "campaign_spec": {
                "path": str(spec.path), "file_sha256": spec.file_sha256,
                "body_sha256": spec.body_sha256,
            },
            "fault_plan": {
                "path": str(plan.path), "file_sha256": plan.file_sha256,
                "body_sha256": plan.body_sha256,
            },
            "pin_formal_campaign_id": formals[0]["formal_campaign_id"],
            "formal_campaigns": formals,
            "observer_cadence_ms": GAP_NS // 1_000_000,
            "maximum_slot_lateness_ms": 30_000,
            "state_root": str(self.base / "runtime-state"),
            "raw_observation_directory": str(self.base / "raw"),
            "recorder_root": str(self.root),
            "injector_journal_directory": str(self.base / "inject-journal"),
            "injector_output_directory": str(self.base / "inject-output"),
            "control_directory": str(self.base / "control"),
            "executables": {
                "independent_observer": {
                    "path": str(RECORDER.OBSERVER_EXECUTABLE),
                    "file_sha256": digest("source-independent_observer"),
                },
            },
            **self.boundary(),
        })
        return self.write("campaign-runtime", {
            key: value for key, value in runtime.items()
            if key != "body_sha256"
        }), runtime

    def campaign_continuity_observation(
        self, name: str, *, transition: str | None = None,
        gateway_invocation: str = "a" * 32, lease_generation: int = 11,
        slot_index: int | None = None, commit_sequence: int | None = None,
        previous_lease_receipt_body_sha256: str | None = None,
    ) -> Path:
        runtime_path, runtime = self.campaign_runtime()
        origin = runtime["formal_campaigns"][0]["launcher_dispatch_at_ms"]
        end = runtime["formal_campaigns"][-1]["teardown_deadline_ms"]
        cadence = runtime["observer_cadence_ms"]
        if slot_index is None:
            if self.clock.wall_ms < origin:
                delta_ms = origin - self.clock.wall_ms
                self.clock.wall_ms = origin
                self.clock.boottime_ns += delta_ms * 1_000_000
                slot_index = 0
            else:
                slot_index = (self.clock.wall_ms - origin) // cadence
        scheduled = min(origin + slot_index * cadence, end)
        final_slot = (end - origin + cadence - 1) // cadence
        lease = self.continuity_lease(lease_generation)
        if previous_lease_receipt_body_sha256 is not None:
            lease_body = dict(lease)
            lease_body.pop("body_sha256")
            lease_body["previous_receipt_body_sha256"] = \
                previous_lease_receipt_body_sha256
            lease = RECORDER.seal(lease_body)
        export_sequence = (
            slot_index + 1 if commit_sequence is None else commit_sequence)
        (export_commit, export_commit_document, export_snapshot,
         lease_reference, export_receipt,
         export_identities) = self.continuity_export(
             lease, export_sequence)
        gateway = self.continuity_unit(
            "hepta-tool-gateway@alpha.service", substate="running",
            pid=2101, invocation=gateway_invocation)
        custodian = self.continuity_unit(
            "hepta-shadow-watch-custodian@alpha.service",
            substate="running", pid=2102, invocation="b" * 32)
        collector = self.continuity_unit(
            "hepta-shadow-watch-collector@alpha.timer", substate="waiting")
        reconcile = self.continuity_unit(
            "hepta-p1-watch-activation-reconcile.timer", substate="waiting")
        gateway_process = state_seal({
            "pid": 2101, "uid": 1000, "gid": 1000,
            "starttime_ticks": 210100, "exe_device": 8, "exe_inode": 20,
            "cgroup_sha256": digest("gateway-cgroup"),
        })
        gateway_executable = self.continuity_path(
            str(RECORDER.GATEWAY_EXECUTABLE), file_type="regular", inode=20,
            file_sha256=digest("gateway-executable"))
        gateway_profile = self.continuity_path(
            str(RECORDER.GATEWAY_PROFILE), file_type="regular", inode=21,
            file_sha256=digest("gateway-profile"))
        gateway_domain = self.continuity_path(
            str(RECORDER.GATEWAY_DOMAIN_CONFIG), file_type="regular",
            inode=22, file_sha256=digest("gateway-domain"))
        tool_socket = self.continuity_path(
            str(RECORDER.GATEWAY_TOOL_SOCKET), inode=23)
        supervisor_socket = self.continuity_path(
            str(RECORDER.GATEWAY_SUPERVISOR_SOCKET), inode=24)
        gateway_after = {
            "unit": "hepta-tool-gateway@alpha.service",
            "active_state": "active", "sub_state": "running",
            "gateway_main_pid": 2101,
            "gateway_invocation_id": "a" * 32,
            "gateway_exec_main_start_timestamp_monotonic_us": 100,
            "process_starttime_ticks": 210100,
            "gateway_executable_path": str(RECORDER.GATEWAY_EXECUTABLE),
            "gateway_executable_sha256": digest("gateway-executable"),
            "domain_config_sha256": digest("gateway-domain"),
            "gateway_profile_path": str(RECORDER.GATEWAY_PROFILE),
            "gateway_profile_sha256": digest("gateway-profile"),
            "gateway_process_profile_sha256": digest("process-profile"),
            "execution_remote_mode": "SIMULATOR", "tool_account": "SIM",
            "execution_domain_id": "SIM:alpha", "tool_allow_trade": "0",
            "session_templates": "watch",
            "contract_bindings": "EUR.USD|EUR|CASH|IDEALPRO|USD",
            "gateway_socket_path": str(RECORDER.GATEWAY_TOOL_SOCKET),
            "gateway_socket_device": 8, "gateway_socket_inode": 23,
            "supervisor_socket_path": str(
                RECORDER.GATEWAY_SUPERVISOR_SOCKET),
            "supervisor_socket_device": 8,
            "supervisor_socket_inode": 24,
            "unit_contract_sha256": digest("gateway-unit-contract"),
        }
        activation_body = {
            field: None for field in RECORDER.ACTIVATION_RECEIPT_FIELDS
            if field != "body_sha256"
        }
        activation_body.update({
            "schema": "hepta.p1-watch-activation-receipt.v4",
            "version": 4, "status": "WATCH_GATEWAY_ACTIVATED",
            "round": 114, "domain": "alpha",
            "boot_id": self.clock.boot_id, "gateway_activated": True,
            "broker_deny_all_continuity_attested": True,
            "kill_switch_engaged": True,
            "watch_authority_provisioned": False,
            "gateway_after": gateway_after,
            "paper_authorized": False, "live_authorized": False,
            "mutation_attempted": False, "direct_broker_access": False,
            "shadow_install_evidence":
                shadow_install_evidence(RECORDER, SOURCE_SHA),
            "predecessor_activation_success": predecessor_activation_success(),
            "predecessor_activation_failure": predecessor_activation_failure(),
        })
        activation = RECORDER.seal(activation_body)
        broker_state = state_seal({
            "helper_path": "/usr/libexec/hepta-broker-egress-policy",
            "helper_file_sha256": digest("broker-helper"),
            "policy_sha256": digest("broker-policy"),
            "authorized_connector_count": 0, "authorized_uids": [],
            "protected_port_count": 4, "deny_all": True,
            "checked_boottime_ns": self.clock.boottime_ns,
        })
        evidence = RECORDER.seal({
            "schema": RECORDER.OBSERVATION_EVIDENCE_SCHEMA, "version": 1,
            "kind": "CAMPAIGN_CONTINUITY", "boot_id": self.clock.boot_id,
            "observed_boottime_ns": self.clock.boottime_ns,
            "systemd_units": sorted(
                [gateway, custodian, collector, reconcile],
                key=lambda item: item["unit"]),
            "processes": [gateway_process],
            "paths": sorted([
                gateway_executable, gateway_profile, gateway_domain,
                tool_socket, supervisor_socket, *export_identities,
            ], key=lambda item: item["path"]),
            "broker_deny_all": broker_state,
            "fault_injection_receipt": None,
        })
        return self.write(name, {
            **self.observer_header(
                RECORDER.CAMPAIGN_CONTINUITY_OBSERVATION_SCHEMA,
                "campaign-continuity-observer"),
            "observed_boottime_ns": self.clock.boottime_ns,
            "freeze_bundle": {
                "path": str(self.paths["freeze-bundle"]),
                "file_sha256": RECORDER.digest_bytes(
                    self.paths["freeze-bundle"].read_bytes()),
                "body_sha256": RECORDER._strict_document(
                    self.paths["freeze-bundle"].read_bytes(),
                    "TEST_FREEZE", sealed=True)[1],
            },
            "campaign_runtime": self.artifact_reference(
                str(runtime_path), runtime),
            "continuity_slot_index": slot_index,
            "continuity_scheduled_at_ms": scheduled,
            "continuity_origin_ms": origin,
            "continuity_end_ms": end,
            "continuity_cadence_ms": cadence,
            "continuity_final_slot": final_slot,
            "continuity_is_final": slot_index == final_slot,
            "catch_up": False,
            "activation_receipt": self.artifact_reference(
                "/evidence/activation-receipt.json", activation),
            "activation_receipt_document": activation,
            "export_commit": export_commit,
            "export_commit_document": export_commit_document,
            "export_snapshot": export_snapshot,
            "lease_receipt": lease_reference,
            "lease_receipt_document": lease,
            "export_receipt": export_receipt,
            "lease_generation": lease_generation,
            "previous_lease_generation": lease_generation - 1,
            "previous_lease_receipt_body_sha256":
                lease["previous_receipt_body_sha256"],
            "gateway_identity": gateway,
            "gateway_process_identity": gateway_process,
            "gateway_executable_identity": gateway_executable,
            "gateway_profile_identity": gateway_profile,
            "gateway_domain_config_identity": gateway_domain,
            "supervisor_socket_identity": supervisor_socket,
            "custodian_identity": custodian,
            "collector_timer_identity": collector,
            "activation_reconcile_timer_identity": reconcile,
            "tool_socket_identity": tool_socket,
            "transition_fault_id": transition,
            "persistent_stack_ok": True, "lease_chain_ok": True,
            "connector_count": 0, "authorized_uids": [],
            "paper_unit_active_count": 0,
            "campaign_socket_present": False,
            "kill_switch_engaged": True, "zero_exposure": True,
            "observation_evidence": evidence,
        })

    def authority_observation(
        self, name: str, *, connector_count: int = 0,
        uncertain: bool = False,
    ) -> Path:
        evidence = observation_evidence(
            "AUTHORITY", self.clock.boot_id, self.clock.boottime_ns)
        evidence_body = {
            key: copy.deepcopy(value) for key, value in evidence.items()
            if key != "body_sha256"
        }
        broker_body = {
            key: copy.deepcopy(value)
            for key, value in evidence_body["broker_deny_all"].items()
            if key != "state_sha256"
        }
        broker_body.update({
            "authorized_connector_count": connector_count,
            "deny_all": connector_count == 0,
        })
        evidence_body["broker_deny_all"] = state_seal(broker_body)
        evidence = RECORDER.seal(evidence_body)
        local_safe = connector_count == 0 and not uncertain
        return self.write(name, {
            **self.observer_header(
                RECORDER.AUTHORITY_OBSERVATION_SCHEMA,
                "independent-authority-observer"),
            "observed_boottime_ns": self.clock.boottime_ns,
            "connector_count": connector_count, "authorized_uids": [],
            "paper_unit_active_count": 0,
            "campaign_socket_present": False,
            "kill_switch_engaged": True,
            "local_boundary_safe": local_safe,
            "local_boundary_uncertain": uncertain,
            "observation_scope": "LOCAL_HOST_BOUNDARY_ONLY",
            "authoritative_account_state_observed": False,
            "observation_evidence": evidence,
        })

    def cleanup_observation(
        self, name: str, *, subject_type: str = "FINAL",
        subject_id: str = CAMPAIGN_ID, complete: bool = True,
    ) -> Path:
        return self.write(name, {
            **self.observer_header(
                RECORDER.CLEANUP_OBSERVATION_SCHEMA,
                "independent-cleanup-observer"),
            "observed_boottime_ns": self.clock.boottime_ns,
            "subject_type": subject_type, "subject_id": subject_id,
            "watch_authority_count": 0, "export_residue_count": 0,
            "session_authority_count": 0, "paper_unit_active_count": 0,
            "campaign_socket_present": False,
            "cleanup_complete": complete,
            "cleanup_uncertain": not complete,
            "errors": [] if complete else ["CLEANUP_FAILED"],
            "observation_evidence": observation_evidence(
                "CLEANUP", self.clock.boot_id, self.clock.boottime_ns),
        })

    def fault_observation(self, name: str, *, safe: bool = True) -> Path:
        planned = self.planned_faults[0]
        recovered = self.fault_injection + 1_000_000_000
        if self.clock.boottime_ns < recovered:
            self.clock.advance(recovered - self.clock.boottime_ns)
        companion_path = self.write(f"{name}-injection", {
            "schema": RECORDER.FAULT_INJECTION_SCHEMA, "version": 1,
            "status": "COMPLETE", "issued_at_ms": self.clock.wall_ms,
            "expires_at_ms": self.clock.wall_ms + 60_000,
            "campaign_id": CAMPAIGN_ID, **self.lineage(),
            "fault_id": "fault-1", "fault_type": "EVIDENCE_WRITER_CRASH",
            "target_id": planned["target_id"],
            "clock_id": "CLOCK_BOOTTIME", "boot_id": self.clock.boot_id,
            "planned_injection_boottime_ns": self.fault_injection,
            "actual_injection_boottime_ns": self.fault_injection,
            "recovered_boottime_ns": recovered,
            "maximum_recovery_ns": planned["maximum_recovery_ns"],
            "injector_id": "root-p1-fault-injector",
            "injector_uid": 0, "injector_gid": 0,
            "injection_scope": "P1_DECLARED_FAULT_ONLY",
            "action_receipt_sha256": digest(f"{name}-action"),
            "pre_identity": fault_target_identity(
                "PRE", planned["target_id"], planned["fault_type"],
                self.fault_injection - 1, self.clock.boot_id),
            "post_identity": fault_target_identity(
                "POST", planned["target_id"], planned["fault_type"],
                recovered, self.clock.boot_id),
            "injection_performed": True, "recovery_complete": safe,
            "cleanup_complete": safe, "authority_failure": not safe,
            "audit_failure": False, "cleanup_failure": not safe,
            "producer": {
                "path": str(RECORDER.FAULT_INJECTOR_EXECUTABLE),
                "file_sha256": digest("source-root_fault_injector"),
            },
            "production_mode": RECORDER.FAULT_INJECTOR_PRODUCTION_MODE,
            "pins_reference": {
                "path": "/evidence/fault-injector-pins.json",
                "file_sha256": digest("fault-pins-file"),
                "body_sha256": digest("fault-pins-body"),
            },
            "journal_predecessor_sequence": 8,
            "journal_predecessor_body_sha256": digest("fault-journal-head"),
            **self.boundary(),
        })
        companion = RECORDER.load_snapshot(
            companion_path, "fault_injection_receipt",
            expected_uid=self.uid)
        companion_reference = {
            "path": str(companion.path),
            "file_sha256": companion.file_sha256,
            "body_sha256": companion.body_sha256,
            "schema": companion.document["schema"],
        }
        return self.write(name, {
            **self.observer_header(
                RECORDER.FAULT_OBSERVATION_SCHEMA,
                "independent-fault-observer"),
            "fault_id": "fault-1",
            "fault_type": "EVIDENCE_WRITER_CRASH",
            "target_id": RECORDER.FAULT_TARGET_IDS[
                "EVIDENCE_WRITER_CRASH"],
            "injection_boottime_ns": self.fault_injection,
            "recovered_boottime_ns": recovered,
            "recovery_verified": safe, "cleanup_verified": safe,
            "authority_failure": not safe, "audit_failure": False,
            "cleanup_failure": not safe,
            "observation_evidence": observation_evidence(
                "FAULT", self.clock.boot_id, recovered,
                companion_reference),
        })

    def closure_and_decisions(
        self, *, catch_up: bool = False,
    ) -> tuple[Path, list[Path]]:
        policy = self.formal_documents[0]
        policy_path = self.formal_paths[0]
        policy_file_sha = RECORDER.digest_bytes(policy_path.read_bytes())
        maximum = policy["maximum_iterations"]
        actual_paths: list[Path] = []
        actual_file_shas: list[str] = []
        for index in range(1, maximum + 1):
            scheduled = policy["valid_after_ms"] + \
                (index - 1) * policy["slot_interval_ms"]
            actual = {
                "schema": "hepta.autonomous-paper-decision-receipt.v1",
                "campaign_id": policy["campaign_id"],
                "strategy_id": STRATEGY_ID,
                "strategy_version": STRATEGY_VERSION,
                "strategy_sha256": STRATEGY_SHA,
                "decision_id": f"decision-{index:04d}",
                "cycle_id": None,
                "started_at_ms": scheduled,
                "finished_at_ms": scheduled + 500,
                "paper_only": True,
                "live_authorized": False,
                "shadow_only": True,
                "information_packet_sha256": digest(f"packet-{index}"),
                "catalog_sha256": digest("catalog"),
                "descriptor_sha256": digest("descriptor"),
                "preflight_sha256": None,
                "regime": "unknown",
                "setup_gates": [],
                "risk_challenges": ["NO_SETUP"],
                "evidence_refs": [
                    policy["campaign_sha256"], policy_file_sha],
                "conflicts": [],
                "decision": "NO_TRADE",
                "reason_codes": ["NO_SETUP"],
                "trade_intent": None,
                "trade_intent_sha256": None,
                "campaign_open_request_id": None,
                "campaign_close_request_id": None,
                "mutation_attempted": False,
                "direct_broker_access": False,
                "final_outcome": "NO_TRADE",
            }
            path = self.write(
                f"actual-decision-{index:04d}", actual, sealed=False)
            actual_paths.append(path)
            actual_file_shas.append(RECORDER.digest_bytes(path.read_bytes()))

        iterations: list[dict] = []
        for index in range(1, maximum + 1):
            scheduled = policy["valid_after_ms"] + \
                (index - 1) * policy["slot_interval_ms"]
            iteration = {
                field: digest(f"iteration-{index}-{field}")
                for field in RECORDER.VERIFIED_ITERATION_FIELDS
            }
            iteration.update({
                "iteration": index,
                "segment_index": 1,
                "scheduled_at_ms": scheduled,
                "evaluated_at_ms": scheduled + (
                    RECORDER.POLICY_MAXIMUM_LATENESS_MS + 1
                    if catch_up and index == 1 else 1_000),
                "source_first_sequence": index,
                "source_last_sequence": index,
                "source_record_count": 1,
                "source_total_record_count": index,
                "source_window_truncated": index > 1,
                "source_predecessor_record_sha256": (
                    None if index == 1 else digest(f"predecessor-{index}")),
                "materialization_window_ms": 60_000,
                "materialization_maximum_records": 100,
                "source_attestation": {
                    "receipt_body_sha256": digest(f"receipt-body-{index}"),
                    "receipt_file_sha256": digest(f"receipt-file-{index}"),
                    "extractor_code_sha256": digest("extractor"),
                    "semantic_output_sha256": digest(f"semantic-{index}"),
                    "completeness_sha256": digest(f"complete-{index}"),
                    "raw_payloads_verified": True,
                },
                "decision_receipt_file_sha256": actual_file_shas[index - 1],
                "final_outcome": "NO_TRADE",
                "residual_evidence": ["retained-evidence"],
            })
            iterations.append(iteration)
        closure = RECORDER.seal({
            "schema": RECORDER.VERIFIED_CLOSURE_SCHEMA,
            "version": 1,
            "campaign_id": policy["campaign_id"],
            "campaign_sha256": policy["campaign_sha256"],
            "policy_body_sha256": policy["body_sha256"],
            "policy_file_sha256": policy_file_sha,
            "strategy_id": STRATEGY_ID,
            "strategy_version": STRATEGY_VERSION,
            "strategy_sha256": STRATEGY_SHA,
            "strategy_file_sha256": digest("strategy-file"),
            "observer_state_body_sha256": digest("observer-body"),
            "observer_state_file_sha256": digest("observer-file"),
            "strategy_state_file_sha256": digest("strategy-state"),
            "final_audit_body_sha256": digest("audit-body"),
            "final_audit_file_sha256": digest("audit-file"),
            "verified_at_ms": iterations[-1]["evaluated_at_ms"] + 1,
            "completed_iterations": maximum,
            "maximum_iterations": maximum,
            "segment_count": 1,
            "segments": [{
                "segment_index": 1, "record_count": maximum,
                "history_head_sha256": digest("history-head"),
                "source_sha256": digest("history-source"),
                "history_record_bytes": 10,
                "history_index_bytes": 5,
                "history_storage_bytes": 15,
                "audit_sha256": digest("history-audit"),
            }],
            "iteration_count": maximum,
            "iterations": iterations,
            "residual_evidence": ["retained-evidence"],
            "complete_revalidation": False,
            "closure_status":
                "VERIFIED_WITH_RETAINED_EVIDENCE_RESIDUALS",
            "paper_authorized": False,
            "live_authorized": False,
            "mutation_attempted": False,
            "direct_broker_access": False,
        })
        path = self.inputs / "verified-closure.json"
        path.write_bytes(RECORDER.canonical_bytes(closure))
        path.chmod(0o600)
        return path, actual_paths

    def decision_clock_observation(self, closure: Path) -> Path:
        document, _body = RECORDER._strict_document(
            closure.read_bytes(), "TEST_CLOSURE", sealed=True)
        observed_at = document["iterations"][-1]["evaluated_at_ms"] + 1
        self.clock.wall_ms = observed_at
        self.clock.boottime_ns = START_BOOTTIME_NS + \
            (observed_at - NOW_MS) * 1_000_000
        return self.service_observation("decision-clock-observation")


class P1SafetySoakEvidenceRecorderTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.fixture = Fixture(self.temporary.name)

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def test_activation_predecessor_lineage_is_exact(self) -> None:
        success = predecessor_activation_success()
        failure = predecessor_activation_failure()
        RECORDER._validate_activation_predecessor_lineage(
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
                with self.assertRaises(RECORDER.RecorderError):
                    RECORDER._validate_activation_predecessor_lineage(
                        changed if original is success else success,
                        changed if original is failure else failure,
                        "TEST_PREDECESSOR_INVALID")

    def assertReason(self, expected: str, callback) -> None:
        with self.assertRaises(RECORDER.RecorderError) as caught:
            callback()
        self.assertEqual(caught.exception.reason, expected)

    def auditor_spec(self):
        spec_artifact = AUDITOR.load_artifact(
            self.fixture.recorder.spec_path, "campaign_spec")
        return spec_artifact, AUDITOR.validate_spec(spec_artifact)

    def test_directory_anchor_ignores_unrelated_child_link_count_churn(self):
        parent = Path(self.temporary.name) / "anchor-parent"
        parent.mkdir()
        before = parent.stat()
        (parent / "unrelated-child").mkdir()
        after = parent.stat()
        self.assertEqual(
            RECORDER._directory_identity(before),
            RECORDER._directory_identity(after))

    def test_freeze_outputs_exact_independently_accepted_contracts(self) -> None:
        spec, plan = self.fixture.freeze()
        self.assertEqual(
            spec["scheduled_decision_count"],
            FORMAL_SEGMENTS *
            RECORDER.POLICY_MAXIMUM_ITERATIONS)
        self.assertGreaterEqual(
            len(spec["eligible_scheduled_at_ms"]),
            RECORDER.MINIMUM_ELIGIBLE_DECISIONS)
        self.assertEqual(
            len(spec["declared_trading_days"]),
            RECORDER.MINIMUM_TRADING_DAYS)
        self.assertEqual(plan["planned_faults"][0]["fault_id"], "fault-1")
        spec_artifact, validated_spec = self.auditor_spec()
        plan_artifact = AUDITOR.load_artifact(
            self.fixture.recorder.fault_plan_path, "fault_plan")
        validated_plan = AUDITOR.validate_fault_plan(
            plan_artifact, validated_spec)
        self.assertEqual(len(validated_plan), 7)
        self.assertEqual(
            spec_artifact.body_sha256, spec["body_sha256"])
        self.assertEqual(
            stat.S_IMODE(self.fixture.recorder.spec_path.stat().st_mode),
            0o600)
        for field in RECORDER.BOUNDARY_FIELDS:
            self.assertIs(spec[field], False)
            self.assertIs(plan[field], False)

    def test_freeze_is_idempotent_but_different_frozen_input_fails(self) -> None:
        expected = self.fixture.freeze()
        self.assertEqual(self.fixture.freeze(), expected)
        schedule_path = self.fixture.paths["schedule"]
        body, _ = RECORDER._strict_document(
            schedule_path.read_bytes(), "TEST", sealed=True)
        body.pop("body_sha256")
        body["independent_auditor_id"] = "different-auditor"
        self.fixture.rewrite(schedule_path, body)
        self.assertReason(
            "P1_RECORDER_REFERENCE_DRIFT",
            self.fixture.freeze)

    def test_freeze_requires_every_fault_type_and_exact_target(self) -> None:
        for drift in (
            "missing", "duplicate-type", "wrong-target",
            "excessive-lateness", "overlap", "wrong-formal",
            "outside-formal",
        ):
            with self.subTest(drift=drift), \
                    tempfile.TemporaryDirectory() as temporary:
                fixture = Fixture(temporary)
                path = fixture.paths["fault-schedule"]
                document, _ = RECORDER._strict_document(
                    path.read_bytes(), "TEST", sealed=True)
                body = copy.deepcopy(document)
                body.pop("body_sha256")
                if drift == "missing":
                    body["planned_faults"].pop()
                else:
                    if drift == "duplicate-type":
                        body["planned_faults"][-1]["fault_type"] = \
                            body["planned_faults"][0]["fault_type"]
                        body["planned_faults"][-1]["target_id"] = \
                            body["planned_faults"][0]["target_id"]
                    else:
                        if drift == "wrong-target":
                            body["planned_faults"][0]["target_id"] = \
                                "unrelated-process"
                        elif drift == "excessive-lateness":
                            body["planned_faults"][0][
                                "maximum_injection_lateness_ns"] = \
                                RECORDER.MAXIMUM_FAULT_INJECTION_LATENESS_NS + 1
                        elif drift == "overlap":
                            body["planned_faults"][1][
                                "inject_at_boottime_ns"] = \
                                body["planned_faults"][0][
                                    "inject_at_boottime_ns"] + \
                                60 * 1_000_000_000
                        elif drift == "wrong-formal":
                            body["planned_faults"][0][
                                "formal_campaign_id"] = "formal-02"
                        else:
                            body["planned_faults"][-1][
                                "inject_at_boottime_ns"] += \
                                30 * 24 * 60 * 60 * 1_000_000_000
                fixture.rewrite(path, body)
                self.assertReason(
                    "P1_RECORDER_FREEZE_BUNDLE_INVALID",
                    fixture.freeze)

    def test_freeze_rejects_nonexact_or_self_authorizing_inputs(self) -> None:
        path = self.fixture.paths["schedule"]
        document, _ = RECORDER._strict_document(
            path.read_bytes(), "TEST", sealed=True)
        body = dict(document)
        body.pop("body_sha256")
        body["paper_authorized"] = True
        self.fixture.rewrite(path, body)
        self.assertReason(
            "P1_RECORDER_FROZEN_SCHEDULE_INVALID", self.fixture.freeze)

    def test_exact_schedule_not_receipt_self_report_drives_eligibility(self) -> None:
        self.fixture.freeze()
        closure, decisions = self.fixture.closure_and_decisions()
        clock_observation = self.fixture.decision_clock_observation(closure)
        wrappers = self.fixture.recorder.project_decisions(
            closure, decisions, clock_observation)
        self.assertEqual(len(wrappers), RECORDER.POLICY_MAXIMUM_ITERATIONS)
        eligible = set(self.fixture.eligible_schedule)
        self.assertTrue(any(item["eligible"] for item in wrappers))
        self.assertTrue(any(not item["eligible"] for item in wrappers))
        self.assertTrue(all(
            item["eligible"] == (item["scheduled_at_ms"] in eligible)
            for item in wrappers))
        spec_artifact, spec = self.auditor_spec()
        del spec_artifact
        closure_artifact = AUDITOR.load_artifact(
            closure, "verified_closure")
        self.assertEqual(
            AUDITOR.validate_verified_closure(closure_artifact, spec)[
                "campaign_id"],
            self.fixture.formal_documents[0]["campaign_id"])
        for index, wrapper in enumerate(wrappers):
            artifact = AUDITOR.Artifact.from_document(
                "decision_receipt", f"/fake/decision-{index}.json", wrapper)
            restored = AUDITOR.validate_decision(artifact, spec)
            self.assertEqual(restored, wrapper)
        journal = sorted(self.fixture.recorder.journal_path.glob("*.json"))
        project_entry = RECORDER.load_snapshot(
            journal[-1], "journal", expected_uid=self.fixture.uid).document
        input_by_role = {item["role"]: item for item in project_entry["inputs"]}
        self.assertIn("verified_closure", input_by_role)
        self.assertIn("actual_decision_0", input_by_role)
        self.assertEqual(
            input_by_role["verified_closure"]["file_sha256"],
            RECORDER.digest_bytes(closure.read_bytes()))

    def test_expired_sealed_projection_anchor_is_historical_not_current(self):
        self.fixture.freeze()
        closure, decisions = self.fixture.closure_and_decisions()
        clock_observation = self.fixture.decision_clock_observation(closure)
        formal_expiry = self.fixture.formal_documents[0]["expires_at_ms"]
        projection_at = formal_expiry + 5 * 60_000
        self.fixture.clock.wall_ms = projection_at
        self.fixture.clock.boottime_ns = START_BOOTTIME_NS + \
            (projection_at - NOW_MS) * 1_000_000

        wrappers = self.fixture.recorder.project_decisions(
            closure, decisions, clock_observation)
        self.assertEqual(len(wrappers), RECORDER.POLICY_MAXIMUM_ITERATIONS)
        self.assertReason(
            "P1_RECORDER_CAMPAIGN_CONTINUITY_OBSERVATION_INVALID",
            lambda: self.fixture.recorder.checkpoint(clock_observation))

    def test_projection_anchor_cross_boot_is_rejected(self) -> None:
        self.fixture.freeze()
        closure, decisions = self.fixture.closure_and_decisions()
        clock_observation = self.fixture.decision_clock_observation(closure)
        self.fixture.clock.boot_id = \
            "00000000-0000-0000-0000-000000000002"
        self.assertReason(
            "P1_RECORDER_SERVICE_OBSERVATION_INVALID",
            lambda: self.fixture.recorder.project_decisions(
                closure, decisions, clock_observation))

    def test_projection_anchor_observed_after_formal_expiry_is_rejected(self):
        self.fixture.freeze()
        closure, decisions = self.fixture.closure_and_decisions()
        formal_expiry = self.fixture.formal_documents[0]["expires_at_ms"]
        observed_at = formal_expiry + 1
        self.fixture.clock.wall_ms = observed_at
        self.fixture.clock.boottime_ns = START_BOOTTIME_NS + \
            (observed_at - NOW_MS) * 1_000_000
        clock_observation = self.fixture.service_observation(
            "post-formal-expiry-clock-observation")
        self.assertReason(
            "P1_RECORDER_DECISION_CLOCK_BINDING_INVALID",
            lambda: self.fixture.recorder.project_decisions(
                closure, decisions, clock_observation))

    def test_projection_after_frozen_deadline_is_rejected(self) -> None:
        self.fixture.freeze()
        closure, decisions = self.fixture.closure_and_decisions()
        clock_observation = self.fixture.decision_clock_observation(closure)
        formal_expiry = self.fixture.formal_documents[0]["expires_at_ms"]
        projection_at = formal_expiry + \
            RECORDER.POST_FORMAL_PROJECTION_GUARD_MS + 1
        self.fixture.clock.wall_ms = projection_at
        self.fixture.clock.boottime_ns = START_BOOTTIME_NS + \
            (projection_at - NOW_MS) * 1_000_000
        self.assertReason(
            "P1_RECORDER_DECISION_PROJECTION_DEADLINE_EXCEEDED",
            lambda: self.fixture.recorder.project_decisions(
                closure, decisions, clock_observation))

    def test_projection_anchor_replacement_before_commit_is_rejected(self):
        self.fixture.freeze()
        closure, decisions = self.fixture.closure_and_decisions()
        clock_observation = self.fixture.decision_clock_observation(closure)
        original_execute = self.fixture.recorder._execute

        def replace_anchor_then_execute(**arguments):
            document, _body = RECORDER._strict_document(
                clock_observation.read_bytes(), "TEST_CLOCK", sealed=True)
            changed = dict(document)
            changed.pop("body_sha256")
            changed["service_epoch"] = "replacement-epoch"
            self.fixture.rewrite(clock_observation, changed)
            return original_execute(**arguments)

        with mock.patch.object(
                self.fixture.recorder, "_execute",
                side_effect=replace_anchor_then_execute):
            self.assertReason(
                "P1_RECORDER_INPUT_DRIFT",
                lambda: self.fixture.recorder.project_decisions(
                    closure, decisions, clock_observation))
        self.assertEqual(list(
            (self.fixture.root / "decisions").glob("*.json")), [])

    def test_catch_up_projection_is_forbidden_not_backfilled(self) -> None:
        self.fixture.freeze()
        closure, decisions = self.fixture.closure_and_decisions(catch_up=True)
        clock_observation = self.fixture.decision_clock_observation(closure)
        self.assertReason(
            "P1_RECORDER_CATCH_UP_FORBIDDEN",
            lambda: self.fixture.recorder.project_decisions(
                closure, decisions, clock_observation))
        self.assertEqual(list(
            (self.fixture.root / "decisions").glob("*.json")), [])

    def test_checkpoint_consumes_persistent_identity_and_lease_chain(
        self,
    ) -> None:
        self.fixture.freeze()
        first_path = self.fixture.campaign_continuity_observation(
            "continuity-0")
        first = self.fixture.recorder.checkpoint(first_path)
        self.fixture.clock.advance(GAP_NS)
        second_path = self.fixture.campaign_continuity_observation(
            "continuity-1")
        second = self.fixture.recorder.checkpoint(second_path)
        self.assertEqual(first["sequence"], 0)
        self.assertIsNone(first["previous_checkpoint_body_sha256"])
        self.assertEqual(second["sequence"], 1)
        self.assertEqual(
            second["previous_checkpoint_body_sha256"],
            first["body_sha256"])
        self.assertEqual(second["clock_id"], "CLOCK_BOOTTIME")
        self.assertTrue(second["persistent_stack_ok"])
        self.assertTrue(second["lease_chain_ok"])
        self.assertTrue(second["zero_exposure"])
        self.assertGreater(
            second["export_commit_document"]["commit_sequence"],
            first["export_commit_document"]["commit_sequence"])
        self.assertNotEqual(
            second["lease_receipt"]["path"], first["lease_receipt"]["path"])
        self.assertEqual(
            second["lease_receipt"]["body_sha256"],
            first["lease_receipt"]["body_sha256"])
        _spec_artifact, spec = self.auditor_spec()
        for index, document in enumerate((first, second)):
            artifact = AUDITOR.Artifact.from_document(
                "continuity_checkpoint", f"/fake/checkpoint-{index}.json",
                document)
            self.assertEqual(
                AUDITOR.validate_checkpoint(artifact, spec), document)

    def test_checkpoint_rejects_commit_replay_and_wrong_rotate_predecessor(
            self) -> None:
        self.fixture.freeze()
        first = self.fixture.recorder.checkpoint(
            self.fixture.campaign_continuity_observation("continuity-0"))
        self.fixture.clock.advance(GAP_NS)
        replay = self.fixture.campaign_continuity_observation(
            "continuity-replayed-commit", commit_sequence=
            first["export_commit_document"]["commit_sequence"])
        self.assertReason(
            "P1_RECORDER_CHECKPOINT_LEASE_CHAIN_GAP",
            lambda: self.fixture.recorder.checkpoint(replay))

        with tempfile.TemporaryDirectory() as temporary:
            fixture = Fixture(temporary)
            fixture.freeze()
            prior = fixture.recorder.checkpoint(
                fixture.campaign_continuity_observation("continuity-0"))
            fixture.clock.advance(GAP_NS)
            wrong = fixture.campaign_continuity_observation(
                "continuity-wrong-rotate", lease_generation=12,
                previous_lease_receipt_body_sha256=digest("wrong-predecessor"))
            self.assertNotEqual(
                prior["lease_receipt"]["body_sha256"],
                digest("wrong-predecessor"))
            self.assertReason(
                "P1_RECORDER_CHECKPOINT_LEASE_CHAIN_GAP",
                lambda: fixture.recorder.checkpoint(wrong))

    def test_checkpoint_rejects_unsafe_export_observation_metadata(
            self) -> None:
        self.fixture.freeze()
        path = self.fixture.campaign_continuity_observation(
            "continuity-unsafe-export-metadata")
        observation, _body = RECORDER._strict_document(
            path.read_bytes(), "TEST_OBSERVATION", sealed=True)
        body = copy.deepcopy(observation)
        body.pop("body_sha256")
        evidence = copy.deepcopy(body["observation_evidence"])
        evidence.pop("body_sha256")
        commit_path = body["export_commit"]["path"]
        identity = next(
            item for item in evidence["paths"]
            if item["path"] == commit_path)
        identity_body = dict(identity)
        identity_body.pop("state_sha256")
        identity_body["mode"] = 0o600
        evidence["paths"][evidence["paths"].index(identity)] = \
            state_seal(identity_body)
        body["observation_evidence"] = RECORDER.seal(evidence)
        self.fixture.rewrite(path, body)
        self.assertReason(
            "P1_RECORDER_CAMPAIGN_CONTINUITY_OBSERVATION_INVALID",
            lambda: self.fixture.recorder.checkpoint(path))

    def test_checkpoint_rejects_activation_install_source_lineage_mismatch(
            self) -> None:
        self.fixture.freeze()
        path = self.fixture.campaign_continuity_observation(
            "continuity-source-mismatch")
        observation, _ = RECORDER._strict_document(
            path.read_bytes(), "TEST_OBSERVATION", sealed=True)
        body = copy.deepcopy(observation)
        body.pop("body_sha256")
        activation = copy.deepcopy(body["activation_receipt_document"])
        activation.pop("body_sha256")
        activation["shadow_install_evidence"] = copy.deepcopy(
            activation["shadow_install_evidence"])
        activation["shadow_install_evidence"]["source_baseline_sha256"] = \
            digest("wrong-source-manifest")
        activation = RECORDER.seal(activation)
        body["activation_receipt_document"] = activation
        body["activation_receipt"] = self.fixture.artifact_reference(
            "/evidence/activation-receipt.json", activation)
        self.fixture.rewrite(path, body)
        self.assertReason(
            "P1_RECORDER_CAMPAIGN_CONTINUITY_OBSERVATION_INVALID",
            lambda: self.fixture.recorder.checkpoint(path))

    def test_checkpoint_gap_generation_and_boot_drift_fail_closed(self) -> None:
        self.fixture.freeze()
        self.fixture.recorder.checkpoint(
            self.fixture.campaign_continuity_observation("continuity-0"))
        self.fixture.clock.advance(GAP_NS + 1)
        gap_path = self.fixture.campaign_continuity_observation(
            "continuity-gap")
        self.assertReason(
            "P1_RECORDER_CHECKPOINT_GAP",
            lambda: self.fixture.recorder.checkpoint(gap_path))

        with tempfile.TemporaryDirectory() as temporary:
            fixture = Fixture(temporary)
            fixture.freeze()
            fixture.recorder.checkpoint(
                fixture.campaign_continuity_observation("continuity-0"))
            fixture.clock.advance(GAP_NS)
            generation = fixture.campaign_continuity_observation(
                "continuity-generation", lease_generation=13)
            self.assertReason(
                "P1_RECORDER_CHECKPOINT_LEASE_CHAIN_GAP",
                lambda: fixture.recorder.checkpoint(generation))

        with tempfile.TemporaryDirectory() as temporary:
            fixture = Fixture(temporary)
            fixture.freeze()
            fixture.recorder.checkpoint(
                fixture.campaign_continuity_observation("continuity-0"))
            fixture.clock.advance(GAP_NS)
            fixture.clock.boot_id = "00000000-0000-0000-0000-000000000002"
            boot = fixture.campaign_continuity_observation("continuity-boot")
            self.assertReason(
                "P1_RECORDER_CAMPAIGN_RUNTIME_INVALID",
                lambda: fixture.recorder.checkpoint(boot))

    def test_checkpoint_grid_rejects_duplicate_and_skipped_slots(self) -> None:
        for slot_delta in (0, 2):
            with self.subTest(slot_delta=slot_delta), \
                    tempfile.TemporaryDirectory() as temporary:
                fixture = Fixture(temporary)
                fixture.freeze()
                fixture.recorder.checkpoint(
                    fixture.campaign_continuity_observation("continuity-0"))
                fixture.clock.advance(slot_delta * GAP_NS)
                duplicate_or_gap = fixture.campaign_continuity_observation(
                    f"continuity-repeat-{slot_delta}",
                    slot_index=slot_delta)
                self.assertReason(
                    "P1_RECORDER_CHECKPOINT_GRID_GAP_OR_DUPLICATE",
                    lambda: fixture.recorder.checkpoint(duplicate_or_gap))

    def test_gateway_identity_change_requires_verified_restart(self) -> None:
        self.fixture.freeze()
        self.fixture.recorder.checkpoint(
            self.fixture.campaign_continuity_observation("continuity-0"))
        self.fixture.clock.advance(GAP_NS)
        changed = self.fixture.campaign_continuity_observation(
            "continuity-changed", gateway_invocation="c" * 32)
        self.assertReason(
            "P1_RECORDER_CHECKPOINT_TRANSITION_UNBOUND",
            lambda: self.fixture.recorder.checkpoint(changed))

        with tempfile.TemporaryDirectory() as temporary:
            fixture = Fixture(temporary)
            fixture.freeze()
            fixture.recorder.checkpoint(
                fixture.campaign_continuity_observation("continuity-0"))
            fixture.clock.advance(GAP_NS)
            spurious = fixture.campaign_continuity_observation(
                "continuity-spurious", transition="fault-3")
            self.assertReason(
                "P1_RECORDER_CHECKPOINT_TRANSITION_SPURIOUS",
                lambda: fixture.recorder.checkpoint(spurious))

    def test_checkpoint_inner_boot_fence_remains_fail_closed(self) -> None:
        self.fixture.freeze()
        first = self.fixture.recorder.checkpoint(
            self.fixture.campaign_continuity_observation("continuity-0"))
        self.fixture.clock.advance(GAP_NS)
        next_path = self.fixture.campaign_continuity_observation(
            "continuity-1", slot_index=1)
        next_observation, _body = RECORDER._strict_document(
            next_path.read_bytes(), "TEST_OBSERVATION", sealed=True)
        changed = copy.deepcopy(first)
        changed["boot_id"] = "00000000-0000-0000-0000-000000000002"
        self.assertReason(
            "P1_RECORDER_CHECKPOINT_BOOT_DRIFT",
            lambda: RECORDER._validate_checkpoint_clock_predecessor(
                changed, next_observation, GAP_NS))

    def test_fault_result_is_plan_bound_and_independently_accepted(self) -> None:
        self.fixture.freeze()
        self.fixture.clock.advance(11 * 60 * 1_000_000_000)
        observation = self.fixture.fault_observation("fault-result")
        result = self.fixture.recorder.record_fault(observation)
        _spec_artifact, spec = self.auditor_spec()
        artifact = AUDITOR.Artifact.from_document(
            "fault_result", "/fake/fault-result.json", result)
        self.assertEqual(AUDITOR.validate_fault_result(artifact, spec), result)
        self.assertFalse(RECORDER.evidence_is_unsafe(result))
        journal_path = sorted(
            self.fixture.recorder.journal_path.glob("*.json"))[-1]
        journal = RECORDER.load_snapshot(
            journal_path, "journal", expected_uid=self.fixture.uid).document
        observer_ref = next(
            item for item in journal["inputs"]
            if item["role"] == "fault_observation")
        self.assertEqual(
            observer_ref["file_sha256"],
            RECORDER.digest_bytes(observation.read_bytes()))
        self.assertEqual(
            observer_ref["body_sha256"],
            RECORDER._strict_document(
                observation.read_bytes(), "TEST", sealed=True)[1])

    def test_all_fault_types_require_non_noop_target_transition(self) -> None:
        for planned in self.fixture.planned_faults:
            with self.subTest(fault_type=planned["fault_type"]):
                actual = planned["inject_at_boottime_ns"]
                recovered = actual + 1_000_000_000
                pre = fault_target_identity(
                    "PRE", planned["target_id"], planned["fault_type"],
                    actual - 1, BOOT_ID)
                post = fault_target_identity(
                    "POST", planned["target_id"], planned["fault_type"],
                    recovered, BOOT_ID)
                RECORDER._require_fault_target_transition(
                    pre, post, fault_type=planned["fault_type"],
                    actual_ns=actual, recovered_ns=recovered,
                    recovery_complete=True, domain_id=DOMAIN_ID,
                    reason="VALID_TRANSITION")
                no_op = copy.deepcopy(pre)
                no_op.pop("body_sha256")
                no_op["phase"] = "POST"
                no_op["observed_boottime_ns"] = recovered
                no_op = RECORDER.seal(no_op)
                self.assertReason(
                    "NOOP_TRANSITION",
                    lambda pre=pre, no_op=no_op, planned=planned,
                           actual=actual, recovered=recovered:
                        RECORDER._require_fault_target_transition(
                            pre, no_op, fault_type=planned["fault_type"],
                            actual_ns=actual, recovered_ns=recovered,
                            recovery_complete=True,
                            domain_id=DOMAIN_ID,
                            reason="NOOP_TRANSITION"))

    def test_unplanned_or_drifted_fault_is_rejected(self) -> None:
        self.fixture.freeze()
        self.fixture.clock.advance(11 * 60 * 1_000_000_000)
        path = self.fixture.fault_observation("fault-drift")
        document, _ = RECORDER._strict_document(
            path.read_bytes(), "TEST", sealed=True)
        body = dict(document)
        body.pop("body_sha256")
        body["fault_type"] = "TOKEN_LOSS"
        body["target_id"] = RECORDER.FAULT_TARGET_IDS["TOKEN_LOSS"]
        self.fixture.rewrite(path, body)
        self.assertReason(
            "P1_RECORDER_FAULT_PLAN_DRIFT",
            lambda: self.fixture.recorder.record_fault(path))

        with tempfile.TemporaryDirectory() as temporary:
            fixture = Fixture(temporary)
            fixture.freeze()
            fixture.clock.advance(11 * 60 * 1_000_000_000)
            wrong_target = fixture.fault_observation("fault-wrong-target")
            document, _ = RECORDER._strict_document(
                wrong_target.read_bytes(), "TEST", sealed=True)
            body = dict(document)
            body.pop("body_sha256")
            body["target_id"] = "unrelated-process"
            fixture.rewrite(wrong_target, body)
            self.assertReason(
                "P1_RECORDER_FAULT_OBSERVATION_INVALID",
                lambda: fixture.recorder.record_fault(wrong_target))

    def test_authority_is_only_copied_from_observer_and_unsafe_is_preserved(
            self) -> None:
        self.fixture.freeze()
        observer = self.fixture.authority_observation(
            "authority-unsafe", connector_count=1)
        snapshot = self.fixture.recorder.record_authority(observer)
        self.assertEqual(snapshot["connector_count"], 1)
        self.assertTrue(RECORDER.evidence_is_unsafe(snapshot))
        for field in RECORDER.BOUNDARY_FIELDS:
            self.assertIs(snapshot[field], False)
        _spec_artifact, spec = self.auditor_spec()
        artifact = AUDITOR.Artifact.from_document(
            "authority_snapshot", "/fake/authority.json", snapshot)
        self.assertEqual(
            AUDITOR.validate_authority_snapshot(artifact, spec), snapshot)
        journal_path = sorted(
            self.fixture.recorder.journal_path.glob("*.json"))[-1]
        journal = RECORDER.load_snapshot(
            journal_path, "journal", expected_uid=self.fixture.uid).document
        observer_ref = next(
            item for item in journal["inputs"]
            if item["role"] == "authority_observation")
        self.assertEqual(observer_ref["path"], str(observer))
        self.assertTrue(observer_ref["sealed"])

    def test_observer_replay_is_rejected_not_treated_as_new_evidence(self) -> None:
        self.fixture.freeze()
        observer = self.fixture.authority_observation("authority-once")
        first = self.fixture.recorder.record_authority(observer)
        self.assertEqual(first["sequence"], 0)
        self.assertReason(
            "P1_RECORDER_AUTHORITY_OBSERVATION_REPLAY",
            lambda: self.fixture.recorder.record_authority(observer))
        self.assertEqual(len(list(
            (self.fixture.root / "authority-snapshots").glob("*.json"))), 1)

    def test_raw_observer_cannot_self_authorize(self) -> None:
        self.fixture.freeze()
        observer = self.fixture.authority_observation("authority-forged")
        document, _ = RECORDER._strict_document(
            observer.read_bytes(), "TEST", sealed=True)
        body = dict(document)
        body.pop("body_sha256")
        body["paper_authorized"] = True
        self.fixture.rewrite(observer, body)
        self.assertReason(
            "P1_RECORDER_AUTHORITY_OBSERVATION_INVALID",
            lambda: self.fixture.recorder.record_authority(observer))

    def test_production_owner_contract_rejects_non_expected_uid(self) -> None:
        path = self.fixture.paths["source-anchor"]
        self.assertReason(
            "P1_RECORDER_INPUT_PARENT_UNTRUSTED",
            lambda: RECORDER.secure_read(
                path, expected_uid=self.fixture.uid + 1))

    def test_cleanup_is_observer_bound_and_duplicate_subject_fails(self) -> None:
        self.fixture.freeze()
        path = self.fixture.cleanup_observation("cleanup-final")
        snapshot = self.fixture.recorder.record_cleanup(path)
        _spec_artifact, spec = self.auditor_spec()
        artifact = AUDITOR.Artifact.from_document(
            "cleanup_snapshot", "/fake/cleanup.json", snapshot)
        self.assertEqual(
            AUDITOR.validate_cleanup_snapshot(artifact, spec), snapshot)
        self.fixture.clock.advance(1_000_000_000)
        duplicate = self.fixture.cleanup_observation("cleanup-final-again")
        self.assertReason(
            "P1_RECORDER_CLEANUP_SUBJECT_DUPLICATE",
            lambda: self.fixture.recorder.record_cleanup(duplicate))

    def test_unsafe_observer_result_is_recorded_but_never_authorizes(self) -> None:
        self.fixture.freeze()
        self.fixture.clock.advance(11 * 60 * 1_000_000_000)
        result = self.fixture.recorder.record_fault(
            self.fixture.fault_observation("fault-unsafe", safe=False))
        self.assertTrue(RECORDER.evidence_is_unsafe(result))
        self.assertTrue(result["authority_failure"])
        for field in RECORDER.BOUNDARY_FIELDS:
            self.assertIs(result[field], False)

    def test_expired_observer_cannot_create_output(self) -> None:
        self.fixture.freeze()
        path = self.fixture.authority_observation("authority-expired")
        document, _ = RECORDER._strict_document(
            path.read_bytes(), "TEST", sealed=True)
        body = dict(document)
        body.pop("body_sha256")
        body["expires_at_ms"] = self.fixture.clock.wall_ms
        self.fixture.rewrite(path, body)
        self.assertReason(
            "P1_RECORDER_AUTHORITY_OBSERVATION_INVALID",
            lambda: self.fixture.recorder.record_authority(path))
        self.assertEqual(list(
            (self.fixture.root / "authority-snapshots").glob("*.json")), [])

    def test_prepare_output_journal_crash_is_recovered(self) -> None:
        self.fixture.freeze()
        observation = self.fixture.campaign_continuity_observation(
            "continuity-crash")
        with mock.patch.object(
                self.fixture.recorder, "_append_journal",
                side_effect=RECORDER.RecorderError("SIMULATED_CRASH")):
            self.assertReason(
                "SIMULATED_CRASH",
                lambda: self.fixture.recorder.checkpoint(observation))
        output = self.fixture.root / "checkpoints" / "00000000.json"
        self.assertTrue(output.exists())
        self.fixture.recorder.recover()
        restored = RECORDER.load_snapshot(
            output, "checkpoint", expected_uid=self.fixture.uid).document
        self.assertEqual(restored["sequence"], 0)
        operations = [
            RECORDER.load_snapshot(
                path, "journal", expected_uid=self.fixture.uid
            ).document["operation"]
            for path in sorted(
                self.fixture.recorder.journal_path.glob("*.json"))
        ]
        self.assertEqual(operations, ["FREEZE", "CHECKPOINT"])

    def test_two_output_freeze_recovers_after_first_business_rename(self) -> None:
        real_publish = self.fixture.recorder._existing_or_publish
        crashed = False

        def crash_before_spec(document, path, role):
            nonlocal crashed
            if role == "campaign_spec" and not crashed:
                crashed = True
                raise RECORDER.RecorderError("SIMULATED_SPEC_CRASH")
            return real_publish(document, path, role)

        with mock.patch.object(
                self.fixture.recorder, "_existing_or_publish",
                side_effect=crash_before_spec):
            self.assertReason("SIMULATED_SPEC_CRASH", self.fixture.freeze)
        self.assertTrue(self.fixture.recorder.fault_plan_path.exists())
        self.assertFalse(self.fixture.recorder.spec_path.exists())
        self.fixture.recorder.recover()
        self.assertTrue(self.fixture.recorder.spec_path.exists())
        spec_artifact, spec = self.auditor_spec()
        self.assertEqual(spec_artifact.document["campaign_id"], CAMPAIGN_ID)
        plan = AUDITOR.load_artifact(
            self.fixture.recorder.fault_plan_path, "fault_plan")
        self.assertEqual(len(AUDITOR.validate_fault_plan(plan, spec)), 7)

    def test_committed_output_drift_and_sequence_gap_fail_recovery(self) -> None:
        self.fixture.freeze()
        self.fixture.recorder.checkpoint(
            self.fixture.campaign_continuity_observation("continuity-0"))
        output = self.fixture.root / "checkpoints" / "00000000.json"
        document, _ = RECORDER._strict_document(
            output.read_bytes(), "TEST", sealed=True)
        body = dict(document)
        body.pop("body_sha256")
        body["lease_generation"] = body["lease_generation"] + 1
        self.fixture.rewrite(output, body)
        self.assertReason(
            "P1_RECORDER_REFERENCE_DRIFT", self.fixture.recorder.recover)

    def test_secure_file_metadata_and_canonical_json_are_enforced(self) -> None:
        path = self.fixture.paths["source-anchor"]
        path.chmod(0o644)
        self.assertReason(
            "P1_RECORDER_INPUT_METADATA_INVALID", self.fixture.freeze)

        with tempfile.TemporaryDirectory() as temporary:
            fixture = Fixture(temporary)
            payload = fixture.paths["source-anchor"].read_bytes()
            fixture.paths["source-anchor"].write_bytes(b" " + payload)
            fixture.paths["source-anchor"].chmod(0o600)
            self.assertReason(
                "P1_RECORDER_SOURCE_ANCHOR_INVALID", fixture.freeze)

    def test_unrelated_parent_directory_activity_does_not_flake_identity(self) -> None:
        path = self.fixture.paths["source-anchor"]
        real_read = RECORDER.os.read
        touched = False

        def touching_read(descriptor: int, count: int) -> bytes:
            nonlocal touched
            payload = real_read(descriptor, count)
            if not touched:
                touched = True
                sibling = self.fixture.inputs / "unrelated"
                sibling.write_bytes(b"unrelated")
                sibling.unlink()
            return payload

        with mock.patch.object(RECORDER.os, "read", side_effect=touching_read):
            payload, _metadata = RECORDER.secure_read(
                path, expected_uid=self.fixture.uid)
        self.assertEqual(payload, path.read_bytes())

    def test_lock_prevents_concurrent_recorder_mutation(self) -> None:
        other = RECORDER.Recorder(
            self.fixture.root, expected_uid=self.fixture.uid,
            clock=self.fixture.clock)
        with self.fixture.recorder._exclusive():
            self.assertReason("P1_RECORDER_LOCK_BUSY", other.recover)


if __name__ == "__main__":
    unittest.main()
