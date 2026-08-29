#!/usr/bin/env python3
"""Publish the fixed post-freeze, post-recorder P1 fault-injector pins.

The producer is root-only and non-authorizing.  It binds the frozen source
lineage, campaign/runtime manifests, installed producer images, two live
transient worker units, the WATCH gateway, and the deny-all broker policy into
the exact pins contract consumed by the root fault injector.
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass
import hashlib
import importlib.machinery
import importlib.util
import json
import os
from pathlib import Path
import re
import shlex
import stat
import subprocess
import sys
import time
from typing import Any, Mapping, Sequence


_script_directory = Path(__file__).absolute().parent
_installed_sibling = (
    _script_directory / "hepta-p1-safety-soak-root-fault-injector")
_source_sibling = (
    _script_directory / "hepta_p1_safety_soak_root_fault_injector.py")
_injector_path = (_installed_sibling if _installed_sibling.is_file()
                  and not _installed_sibling.is_symlink()
                  else _source_sibling)
_loader = importlib.machinery.SourceFileLoader(
    "hepta_p1_safety_soak_root_fault_injector", str(_injector_path))
_spec = importlib.util.spec_from_loader(_loader.name, _loader)
if _spec is None:
    raise RuntimeError("P1_FAULT_PIN_INJECTOR_LOAD_FAILED")
INJECTOR = importlib.util.module_from_spec(_spec)
sys.modules[_loader.name] = INJECTOR
_loader.exec_module(INJECTOR)
OBSERVER = INJECTOR.OBSERVER


VERSION = 1
ROOT_UID = 0
ROOT_GID = 0
INSTALLED_EXECUTABLE = Path(
    "/usr/libexec/hepta-p1-safety-soak-fault-pin-producer")
PRODUCTION_MODE = "PRODUCTION_ROOT_PINNING"
FREEZE_BUNDLE_SCHEMA = "hepta.p1-safety-soak-freeze-bundle-receipt.v1"
RUNTIME_SCHEMA = "hepta.p1-safety-soak-campaign-runtime.v1"
FREEZER_MODE = "PRODUCTION_ROOT_PREFLIGHT"
SYSTEMCTL = "/usr/bin/systemctl"
MAXIMUM_BYTES = 64 * 1024 * 1024
MAXIMUM_PIN_LIFETIME_MS = 31 * 24 * 60 * 60 * 1000
LAUNCHER_WARMUP_MS = 210 * 60 * 1000
LAUNCHER_EARLY_START_LEAD_MS = 20 * 60 * 1000
POLICY_SLOT_INTERVAL_MS = 2 * 60 * 1000
POLICY_MAXIMUM_ITERATIONS = 241
MAXIMUM_LAUNCH_LATENESS_MS = 15 * 60 * 1000
POST_FORMAL_PROJECTION_GUARD_MS = 20 * 60 * 1000
POST_FORMAL_TEARDOWN_GUARD_MS = 30 * 60 * 1000

REFERENCE_FIELDS = frozenset({"path", "file_sha256", "body_sha256"})
PRODUCER_FIELDS = frozenset({"path", "file_sha256"})
SOURCE_PIN_FIELDS = frozenset({
    "role", "source_path", "installed_path", "file_sha256",
})
RUNTIME_FIELDS = frozenset({
    "schema", "version", "status", "campaign_id", "round", "boot_id",
    "issued_at_ms", "expires_at_ms", "freeze_bundle", "campaign_spec",
    "fault_plan", "pin_formal_campaign_id", "formal_campaigns",
    "observer_cadence_ms", "maximum_slot_lateness_ms", "state_root",
    "raw_observation_directory", "recorder_root",
    "injector_journal_directory", "injector_output_directory",
    "control_directory", "executables", *INJECTOR.BOUNDARY_FIELDS,
    "body_sha256",
})
FORMAL_RUNTIME_FIELDS = frozenset({
    "formal_campaign_id", "probe_campaign_id", "launcher_start_ms",
    "valid_after_ms", "slot_interval_ms", "maximum_iterations",
    "expires_at_ms", "launcher_completion_deadline_ms",
    "projection_deadline_ms", "teardown_deadline_ms", "policy",
    "launcher_receipt_path", "verified_closure_path", "artifact_root",
})

SOURCE_PRODUCER_PATHS = {
    "campaign_freezer": (
        "scripts/hepta_p1_safety_soak_campaign_freezer.py",
        "/usr/libexec/hepta-p1-safety-soak-campaign-freezer"),
    "evidence_recorder": (
        "scripts/hepta_p1_safety_soak_evidence_recorder.py",
        "/usr/libexec/hepta-p1-safety-soak-evidence-recorder"),
    "independent_observer": (
        "scripts/hepta_p1_safety_soak_independent_observer.py",
        "/usr/libexec/hepta-p1-safety-soak-independent-observer"),
    "root_fault_injector": (
        "scripts/hepta_p1_safety_soak_root_fault_injector.py",
        str(INJECTOR.INJECTOR_HELPER)),
    "auditor": (
        "scripts/hepta_p1_safety_soak_auditor.py",
        "/usr/libexec/hepta-p1-safety-soak-auditor"),
    "shadow_admission_launcher": (
        "scripts/hepta_p1_shadow_admission_launcher.py",
        "/usr/libexec/hepta-p1-shadow-admission-launcher"),
    "watch_to_paper_handoff": (
        "scripts/hepta_p1_watch_to_paper_handoff.py",
        "/usr/libexec/hepta-p1-watch-to-paper-handoff"),
    "fault_pin_producer": (
        "scripts/hepta_p1_safety_soak_fault_pin_producer.py",
        str(INSTALLED_EXECUTABLE)),
    "campaign_coordinator": (
        "scripts/hepta_p1_safety_soak_campaign_coordinator.py",
        "/usr/libexec/hepta-p1-safety-soak-campaign-coordinator"),
    "observer_worker": (
        "scripts/hepta_p1_safety_soak_observer_worker.py",
        "/usr/libexec/hepta-p1-safety-soak-observer-worker"),
    "recorder_worker": (
        "scripts/hepta_p1_safety_soak_recorder_worker.py",
        "/usr/libexec/hepta-p1-safety-soak-recorder-worker"),
    "policy_planner": (
        "scripts/hepta_p1_safety_soak_policy_planner.py",
        "/usr/libexec/hepta-p1-safety-soak-policy-planner"),
    "observation_policy_builder": (
        "scripts/build_hepta_p1_observation_policy.py",
        "/usr/libexec/build-hepta-p1-observation-policy"),
    "broker_egress_policy": (
        "scripts/hepta_broker_egress_policy.py",
        "/usr/libexec/hepta-broker-egress-policy"),
}

ROLE_ENTRYPOINTS = {
    "OBSERVER_PROCESS": Path(SOURCE_PRODUCER_PATHS["observer_worker"][1]),
    "RECORDER_PROCESS": Path(SOURCE_PRODUCER_PATHS["recorder_worker"][1]),
    "GATEWAY": Path("/usr/libexec/hepta-tool-gatewayd"),
    "BROKER_POLICY": INJECTOR.BROKER_HELPER,
}


class PinError(RuntimeError):
    def __init__(self, reason: str):
        super().__init__(reason)
        self.reason = reason


def _require(condition: bool, reason: str) -> None:
    if not condition:
        raise PinError(reason)


def canonical_bytes(value: Any) -> bytes:
    try:
        return (json.dumps(
            value, ensure_ascii=True, allow_nan=False, sort_keys=True,
            separators=(",", ":")) + "\n").encode("ascii")
    except (TypeError, ValueError, UnicodeError) as error:
        raise PinError("P1_FAULT_PIN_CANONICALIZATION_FAILED") from error


def digest_bytes(payload: bytes) -> str:
    return "sha256:" + hashlib.sha256(payload).hexdigest()


def _exact(value: Any, fields: frozenset[str], reason: str) \
        -> dict[str, Any]:
    _require(isinstance(value, dict) and set(value) == fields, reason)
    return value


def _digest(value: Any, reason: str) -> str:
    _require(type(value) is str and
             OBSERVER.DIGEST.fullmatch(value) is not None and
             value != "sha256:" + "0" * 64, reason)
    return value


def _path(value: Any, reason: str) -> Path:
    _require(type(value) is str, reason)
    path = Path(value)
    _require(path.is_absolute() and Path(os.path.normpath(value)) == path and
             path.name not in {"", ".", ".."} and
             all(part not in {"", ".", ".."} for part in path.parts[1:]),
             reason)
    return path


def _reference(snapshot: Any) -> dict[str, str]:
    return {
        "path": str(snapshot.path), "file_sha256": snapshot.file_sha256,
        "body_sha256": snapshot.body_sha256,
    }


def _validate_reference(value: Any, reason: str) -> dict[str, str]:
    reference = _exact(value, REFERENCE_FIELDS, reason)
    _path(reference.get("path"), reason)
    _digest(reference.get("file_sha256"), reason)
    _digest(reference.get("body_sha256"), reason)
    return reference


def _reject_authority(value: Any, reason: str) -> None:
    if isinstance(value, dict):
        for key, child in value.items():
            if key in {*INJECTOR.BOUNDARY_FIELDS,
                       "paper_test_admission_candidate",
                       "order_submission_authorized", "mutation_attempted"}:
                _require(child is False, reason)
            _reject_authority(child, reason)
    elif isinstance(value, list):
        for child in value:
            _reject_authority(child, reason)


def _load(path: Path, role: str) -> Any:
    try:
        return OBSERVER.load_snapshot(
            path, expected_uid=ROOT_UID, expected_gid=ROOT_GID,
            modes=frozenset({0o400, 0o600, 0o640, 0o644}))
    except OBSERVER.ObserverError as error:
        raise PinError(f"P1_FAULT_PIN_{role.upper()}_INVALID") from error


def _secure_executable(path: Path, reason: str) -> tuple[bytes, os.stat_result]:
    try:
        return OBSERVER.secure_read(
            path, expected_uid=ROOT_UID, expected_gid=ROOT_GID,
            modes=frozenset({0o500, 0o550, 0o555, 0o700, 0o750, 0o755}),
            maximum=MAXIMUM_BYTES)
    except OBSERVER.ObserverError as error:
        raise PinError(reason) from error


@dataclass(frozen=True)
class ProducerBinding:
    payload: bytes
    metadata: os.stat_result

    @property
    def reference(self) -> dict[str, str]:
        return {
            "path": str(INSTALLED_EXECUTABLE),
            "file_sha256": digest_bytes(self.payload),
        }

    def reopen(self) -> None:
        payload, metadata = _secure_executable(
            INSTALLED_EXECUTABLE, "P1_FAULT_PIN_EXECUTING_IMAGE_DRIFT")
        _require(payload == self.payload and
                 OBSERVER._file_identity(metadata) ==
                    OBSERVER._file_identity(self.metadata),
                 "P1_FAULT_PIN_EXECUTING_IMAGE_DRIFT")


def bind_executing_image() -> ProducerBinding:
    try:
        lexical = Path(__file__).absolute()
        metadata = os.lstat(lexical)
        resolved = lexical.resolve(strict=True)
        installed = INSTALLED_EXECUTABLE.resolve(strict=True)
        _require(not stat.S_ISLNK(metadata.st_mode) and
                 resolved == installed == INSTALLED_EXECUTABLE and
                 os.path.samefile(lexical, INSTALLED_EXECUTABLE),
                 "P1_FAULT_PIN_INSTALLED_IMAGE_REQUIRED")
        payload, reopened = _secure_executable(
            INSTALLED_EXECUTABLE, "P1_FAULT_PIN_INSTALLED_IMAGE_REQUIRED")
        _require(OBSERVER._file_identity(metadata) ==
                 OBSERVER._file_identity(reopened),
                 "P1_FAULT_PIN_INSTALLED_IMAGE_REQUIRED")
    except (OSError, PinError) as error:
        if isinstance(error, PinError):
            raise
        raise PinError("P1_FAULT_PIN_INSTALLED_IMAGE_REQUIRED") from error
    return ProducerBinding(payload, reopened)


def _source_pins(bundle: Mapping[str, Any]) -> dict[str, dict[str, str]]:
    reason = "P1_FAULT_PIN_FREEZE_BUNDLE_INVALID"
    _require(bundle.get("schema") == FREEZE_BUNDLE_SCHEMA and
             bundle.get("version") == VERSION and
             bundle.get("status") == "FROZEN" and
             bundle.get("production_mode") == FREEZER_MODE, reason)
    raw = bundle.get("source_producer_pins")
    _require(isinstance(raw, list) and len(raw) == len(SOURCE_PRODUCER_PATHS),
             reason)
    result: dict[str, dict[str, str]] = {}
    for item in raw:
        _exact(item, SOURCE_PIN_FIELDS, reason)
        role = item.get("role")
        _require(type(role) is str and role in SOURCE_PRODUCER_PATHS and
                 role not in result, reason)
        source_path, installed_path = SOURCE_PRODUCER_PATHS[role]
        _require(item.get("source_path") == source_path and
                 item.get("installed_path") == installed_path, reason)
        _digest(item.get("file_sha256"), reason)
        result[role] = dict(item)
    _require(set(result) == set(SOURCE_PRODUCER_PATHS) and
             raw == sorted(raw, key=lambda item: item["role"]), reason)
    producer = _exact(bundle.get("producer"), PRODUCER_FIELDS, reason)
    _require(producer.get("path") ==
             SOURCE_PRODUCER_PATHS["campaign_freezer"][1] and
             producer.get("file_sha256") ==
                result["campaign_freezer"]["file_sha256"], reason)
    return result


def _validate_runtime(
    runtime: Any, *, spec_snapshot: Any, plan_snapshot: Any,
    freeze_snapshot: Any, spec: Mapping[str, Any], source_pins: Mapping[str, Any],
    formal_campaign_id: str, boot_id: str,
) -> dict[str, Any]:
    reason = "P1_FAULT_PIN_RUNTIME_MANIFEST_INVALID"
    value = _exact(runtime.document, RUNTIME_FIELDS, reason)
    _reject_authority(value, reason)
    _require(
        value.get("schema") == RUNTIME_SCHEMA and value.get("version") == 1 and
        value.get("status") == "FROZEN" and value.get("round") == 114 and
        value.get("campaign_id") == spec.get("campaign_id") and
        value.get("boot_id") == boot_id and
        value.get("pin_formal_campaign_id") == formal_campaign_id and
        value.get("freeze_bundle") == _reference(freeze_snapshot) and
        value.get("campaign_spec") == _reference(spec_snapshot) and
        value.get("fault_plan") == _reference(plan_snapshot), reason)
    issued = value.get("issued_at_ms")
    expires = value.get("expires_at_ms")
    _require(type(issued) is int and type(expires) is int and
             0 <= issued < expires, reason)
    formal = value.get("formal_campaigns")
    _require(isinstance(formal, list) and bool(formal), reason)
    formal_ids: set[str] = set()
    previous_teardown_deadline = 0
    for item in formal:
        _exact(item, FORMAL_RUNTIME_FIELDS, reason)
        identifier = item.get("formal_campaign_id")
        _require(type(identifier) is str and
                 OBSERVER.IDENTIFIER.fullmatch(identifier) is not None and
                 identifier not in formal_ids, reason)
        formal_ids.add(identifier)
        _validate_reference(item.get("policy"), reason)
        for field in ("launcher_receipt_path", "verified_closure_path",
                      "artifact_root"):
            _path(item.get(field), reason)
        launcher_start = item.get("launcher_start_ms")
        valid_after = item.get("valid_after_ms")
        interval = item.get("slot_interval_ms")
        maximum = item.get("maximum_iterations")
        expiry = item.get("expires_at_ms")
        completion_deadline = item.get("launcher_completion_deadline_ms")
        projection_deadline = item.get("projection_deadline_ms")
        teardown_deadline = item.get("teardown_deadline_ms")
        _require(
            all(type(value) is int for value in (
                launcher_start, valid_after, interval, maximum, expiry,
                completion_deadline, projection_deadline,
                teardown_deadline)) and
            launcher_start > 0 and interval == POLICY_SLOT_INTERVAL_MS and
            maximum == POLICY_MAXIMUM_ITERATIONS and
            launcher_start == valid_after - LAUNCHER_WARMUP_MS and
            ((launcher_start + LAUNCHER_WARMUP_MS + interval - 1) //
             interval) * interval == valid_after and
            expiry == valid_after + interval * maximum and
            completion_deadline == expiry + MAXIMUM_LAUNCH_LATENESS_MS and
            projection_deadline ==
                expiry + POST_FORMAL_PROJECTION_GUARD_MS and
            teardown_deadline == expiry + POST_FORMAL_TEARDOWN_GUARD_MS and
            (previous_teardown_deadline == 0 or
             valid_after == (
                (previous_teardown_deadline + LAUNCHER_WARMUP_MS +
                 LAUNCHER_EARLY_START_LEAD_MS) // interval + 1
             ) * interval), reason)
        previous_teardown_deadline = teardown_deadline
    _require(formal_ids == {
        item["campaign_id"] for item in spec.get("formal_campaigns", [])
    } and formal_campaign_id in formal_ids, reason)
    executables = value.get("executables")
    _require(isinstance(executables, dict) and
             set(source_pins).issubset(executables), reason)
    for role, raw_reference in executables.items():
        _require(type(role) is str and
                 OBSERVER.IDENTIFIER.fullmatch(role) is not None, reason)
        reference = _exact(raw_reference, PRODUCER_FIELDS, reason)
        _path(reference.get("path"), reason)
        _digest(reference.get("file_sha256"), reason)
        if role in source_pins:
            _require(reference == {
                "path": source_pins[role]["installed_path"],
                "file_sha256": source_pins[role]["file_sha256"],
            }, reason)
    for field in ("state_root", "raw_observation_directory", "recorder_root",
                  "injector_journal_directory", "injector_output_directory",
                  "control_directory"):
        _path(value.get(field), reason)
    return value


def _load_argv(path: Path, expected_sha: str) -> tuple[list[str], str]:
    reason = "P1_FAULT_PIN_ARGV_INVALID"
    try:
        payload, _metadata = OBSERVER.secure_read(
            path, expected_uid=ROOT_UID, expected_gid=ROOT_GID,
            modes=frozenset({0o400, 0o600, 0o640, 0o644}))
        value = json.loads(payload.decode("ascii", errors="strict"),
                           parse_constant=lambda _raw: (_ for _ in ()).throw(
                               PinError(reason)))
    except PinError:
        raise
    except (OSError, UnicodeError, json.JSONDecodeError, ValueError,
            OBSERVER.ObserverError) as error:
        raise PinError(reason) from error
    _require(isinstance(value, list) and bool(value) and
             all(type(item) is str and item and "\x00" not in item
                 for item in value) and
             payload == canonical_bytes(value) and
             digest_bytes(payload) == _digest(expected_sha, reason), reason)
    return value, digest_bytes(payload)


@dataclass(frozen=True)
class UnitInspection:
    role: str
    unit: str
    fragment_path: Path
    fragment_payload: bytes
    executable_payload: bytes
    entrypoint_path: Path
    entrypoint_payload: bytes
    exec_start: str


def _build_contract(
    inspection: UnitInspection, *, expected_unit: str,
    argv: Sequence[str] | None,
) -> dict[str, str]:
    reason = "P1_FAULT_PIN_UNIT_CONTRACT_INVALID"
    _require(inspection.role in INJECTOR.UNIT_ROLES and
             inspection.unit == expected_unit and
             inspection.entrypoint_path == ROLE_ENTRYPOINTS[inspection.role]
             and bool(inspection.exec_start), reason)
    if inspection.role in {"OBSERVER_PROCESS", "RECORDER_PROCESS"}:
        _require(inspection.fragment_path.parent ==
                 Path("/run/systemd/transient") and argv is not None and
                 list(argv)[0] == str(inspection.entrypoint_path), reason)
        try:
            fragment_text = inspection.fragment_payload.decode(
                "utf-8", errors="strict")
        except UnicodeError as error:
            raise PinError(reason) from error
        _require(
            f"ExecStart={shlex.join(list(argv))}" in
            fragment_text.splitlines(), reason)
        argv_sha = digest_bytes(canonical_bytes(list(argv)))
    else:
        _require(argv is None, reason)
        argv_sha = digest_bytes(inspection.exec_start.encode("utf-8"))
    return {
        "role": inspection.role, "unit": inspection.unit,
        "fragment_path": str(inspection.fragment_path),
        "fragment_file_sha256": digest_bytes(inspection.fragment_payload),
        "executable_file_sha256":
            digest_bytes(inspection.executable_payload),
        "entrypoint_path": str(inspection.entrypoint_path),
        "entrypoint_file_sha256":
            digest_bytes(inspection.entrypoint_payload),
        "exec_start_sha256":
            digest_bytes(inspection.exec_start.encode("utf-8")),
        "exec_argv_sha256": argv_sha,
    }


def build_pins(
    *, spec_snapshot: Any, plan_snapshot: Any, freeze_snapshot: Any,
    runtime_snapshot: Any, formal_campaign_id: str, boot_id: str,
    producer: Mapping[str, str], inspections: Sequence[UnitInspection],
    observer_argv: Sequence[str], recorder_argv: Sequence[str],
    broker_helper_file_sha256: str, now_ms: int,
) -> dict[str, Any]:
    reason = "P1_FAULT_PIN_BUILD_INVALID"
    try:
        spec = OBSERVER.validate_spec(spec_snapshot.document)
        OBSERVER.validate_plan(plan_snapshot.document, spec)
    except OBSERVER.ObserverError as error:
        raise PinError(reason) from error
    _require(spec.get("freeze_bundle") == _reference(freeze_snapshot), reason)
    source_pins = _source_pins(freeze_snapshot.document)
    _require(producer == {
        "path": str(INSTALLED_EXECUTABLE),
        "file_sha256": source_pins["fault_pin_producer"]["file_sha256"],
    }, reason)
    runtime = _validate_runtime(
        runtime_snapshot, spec_snapshot=spec_snapshot,
        plan_snapshot=plan_snapshot, freeze_snapshot=freeze_snapshot,
        spec=spec, source_pins=source_pins,
        formal_campaign_id=formal_campaign_id, boot_id=boot_id)
    runtime_formal_ids = {
        item["formal_campaign_id"] for item in runtime["formal_campaigns"]
    }
    _require(
        {item["formal_campaign_id"]
         for item in plan_snapshot.document["planned_faults"]}
        <= runtime_formal_ids,
        reason)
    expected_units = INJECTOR._unit_names(formal_campaign_id)
    by_role = {item.role: item for item in inspections}
    _require(set(by_role) == set(INJECTOR.UNIT_ROLES) and
             len(by_role) == len(inspections), reason)
    contracts = []
    for role in sorted(INJECTOR.UNIT_ROLES):
        argv = observer_argv if role == "OBSERVER_PROCESS" else \
            recorder_argv if role == "RECORDER_PROCESS" else None
        contracts.append(_build_contract(
            by_role[role], expected_unit=expected_units[role], argv=argv))
    expires = min(
        runtime["expires_at_ms"],
        freeze_snapshot.document["expires_at_ms"],
        now_ms + MAXIMUM_PIN_LIFETIME_MS)
    _require(now_ms < expires, reason)
    result = INJECTOR._seal({
        "schema": INJECTOR.PINS_SCHEMA, "version": VERSION,
        "status": "FROZEN", "issued_at_ms": now_ms,
        "expires_at_ms": expires, "campaign_id": spec["campaign_id"],
        "formal_campaign_id": formal_campaign_id, "boot_id": boot_id,
        "source_manifest_sha256": spec["source_manifest_sha256"],
        "campaign_spec_file_sha256": spec_snapshot.file_sha256,
        "campaign_spec_body_sha256": spec_snapshot.body_sha256,
        "fault_plan_file_sha256": plan_snapshot.file_sha256,
        "fault_plan_body_sha256": plan_snapshot.body_sha256,
        "freeze_bundle": _reference(freeze_snapshot),
        "runtime_manifest": _reference(runtime_snapshot),
        "producer": dict(producer), "production_mode": PRODUCTION_MODE,
        "injector_id": "hepta-p1-root-fault-injector-round114",
        "injector_path": str(INJECTOR.INJECTOR_HELPER),
        "injector_file_sha256":
            source_pins["root_fault_injector"]["file_sha256"],
        "observer_helper_path": str(INJECTOR.OBSERVER_HELPER),
        "observer_helper_file_sha256":
            source_pins["independent_observer"]["file_sha256"],
        "unit_contracts": contracts,
        "broker_helper_path": str(INJECTOR.BROKER_HELPER),
        "broker_helper_file_sha256":
            _digest(broker_helper_file_sha256, reason),
        "clock_fixture_helper_path": str(INJECTOR.CLOCK_FIXTURE_HELPER),
        "clock_fixture_helper_file_sha256":
            source_pins["root_fault_injector"]["file_sha256"],
        "clock_fixture_path": str(INJECTOR.CLOCK_FIXTURE_PATH),
        "token_fixture_path": str(INJECTOR.TOKEN_FIXTURE_PATH),
        "lease_fixture_path": str(INJECTOR.LEASE_FIXTURE_PATH),
        "journal_directory": runtime["injector_journal_directory"],
        "output_directory": runtime["injector_output_directory"],
        **INJECTOR._boundary(),
    })
    INJECTOR.validate_pins(
        result, spec_snapshot, plan_snapshot, spec,
        now_ms=now_ms, boot_id=boot_id)
    return result


def _run(arguments: tuple[str, ...]) -> subprocess.CompletedProcess[bytes]:
    try:
        result = subprocess.run(
            arguments, check=False, stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            env={"PATH": "/usr/sbin:/usr/bin:/sbin:/bin", "LANG": "C",
                 "LC_ALL": "C", "PYTHONNOUSERSITE": "1"},
            cwd="/", timeout=30)
    except (OSError, subprocess.SubprocessError) as error:
        raise PinError("P1_FAULT_PIN_COMMAND_FAILED") from error
    _require(result.returncode == 0 and not result.stderr and
             len(result.stdout) <= MAXIMUM_BYTES,
             "P1_FAULT_PIN_COMMAND_FAILED")
    return result


def _inspect_unit(role: str, unit: str) -> UnitInspection:
    reason = "P1_FAULT_PIN_UNIT_INSPECTION_INVALID"
    properties = INJECTOR.UNIT_PROPERTIES
    result = _run((
        SYSTEMCTL, "show", "--no-pager",
        *(f"--property={item}" for item in properties), unit))
    try:
        fields: dict[str, str] = {}
        for line in result.stdout.decode("utf-8", errors="strict").splitlines():
            key, value = line.split("=", 1)
            _require(key in properties and key not in fields, reason)
            fields[key] = value
        pid = int(fields.get("MainPID", "0"))
        start = int(fields.get("ExecMainStartTimestampMonotonic", "0") or "0")
        restarts = int(fields.get("NRestarts", "0") or "0")
    except (UnicodeError, ValueError) as error:
        raise PinError(reason) from error
    _require(set(fields) == set(properties) and
             fields["LoadState"] == "loaded" and
             fields["ActiveState"] == "active" and
             fields["SubState"] == "running" and pid > 1 and start > 0 and
             restarts >= 0 and fields["FragmentPath"] and
             fields["ExecStart"], reason)
    fragment_path = _path(fields["FragmentPath"], reason)
    try:
        fragment_payload, _fragment_metadata = OBSERVER.secure_read(
            fragment_path, expected_uid=ROOT_UID, expected_gid=ROOT_GID,
            modes=frozenset({0o400, 0o444, 0o600, 0o644}))
        process_target = Path(os.readlink(f"/proc/{pid}/exe"))
        executable_payload, _executable_metadata = OBSERVER.secure_read(
            process_target, expected_uid=ROOT_UID, expected_gid=None,
            modes=frozenset({0o500, 0o550, 0o555, 0o700, 0o750, 0o755}),
            maximum=MAXIMUM_BYTES)
        entrypoint = ROLE_ENTRYPOINTS[role]
        entrypoint_payload, _entrypoint_metadata = _secure_executable(
            entrypoint, reason)
    except (OSError, KeyError, OBSERVER.ObserverError) as error:
        raise PinError(reason) from error
    return UnitInspection(
        role=role, unit=unit, fragment_path=fragment_path,
        fragment_payload=fragment_payload,
        executable_payload=executable_payload,
        entrypoint_path=entrypoint, entrypoint_payload=entrypoint_payload,
        exec_start=fields["ExecStart"])


def produce(
    *, campaign_spec_path: Path, fault_plan_path: Path,
    freeze_bundle_path: Path, runtime_manifest_path: Path,
    formal_campaign_id: str, observer_unit: str, recorder_unit: str,
    observer_argv_path: Path, recorder_argv_path: Path,
    expected_observer_argv_file_sha256: str,
    expected_recorder_argv_file_sha256: str,
    expected_spec_file_sha256: str, expected_spec_body_sha256: str,
    expected_plan_file_sha256: str, expected_plan_body_sha256: str,
    expected_freeze_file_sha256: str, expected_freeze_body_sha256: str,
    expected_runtime_file_sha256: str, expected_runtime_body_sha256: str,
    expected_source_manifest_sha256: str, boot_id: str, output: Path,
) -> dict[str, Any]:
    _require(os.geteuid() == ROOT_UID and os.getegid() == ROOT_GID,
             "P1_FAULT_PIN_ROOT_REQUIRED")
    producer = bind_executing_image()
    actual_boot = INJECTOR._read_boot_id()
    _require(boot_id == actual_boot, "P1_FAULT_PIN_BOOT_ID_DRIFT")
    spec_snapshot = _load(campaign_spec_path, "campaign_spec")
    plan_snapshot = _load(fault_plan_path, "fault_plan")
    freeze_snapshot = _load(freeze_bundle_path, "freeze_bundle")
    runtime_snapshot = _load(runtime_manifest_path, "runtime_manifest")
    expected = (
        (spec_snapshot, expected_spec_file_sha256,
         expected_spec_body_sha256),
        (plan_snapshot, expected_plan_file_sha256,
         expected_plan_body_sha256),
        (freeze_snapshot, expected_freeze_file_sha256,
         expected_freeze_body_sha256),
        (runtime_snapshot, expected_runtime_file_sha256,
         expected_runtime_body_sha256),
    )
    _require(all(item.file_sha256 == _digest(file_sha, "P1_FAULT_PIN_EXPLICIT_PIN_DRIFT")
                 and item.body_sha256 == _digest(body_sha, "P1_FAULT_PIN_EXPLICIT_PIN_DRIFT")
                 for item, file_sha, body_sha in expected),
             "P1_FAULT_PIN_EXPLICIT_PIN_DRIFT")
    _require(spec_snapshot.document.get("source_manifest_sha256") ==
             _digest(expected_source_manifest_sha256,
                     "P1_FAULT_PIN_EXPLICIT_PIN_DRIFT"),
             "P1_FAULT_PIN_EXPLICIT_PIN_DRIFT")
    observer_argv, _observer_argv_sha = _load_argv(
        observer_argv_path, expected_observer_argv_file_sha256)
    recorder_argv, _recorder_argv_sha = _load_argv(
        recorder_argv_path, expected_recorder_argv_file_sha256)
    expected_units = INJECTOR._unit_names(formal_campaign_id)
    _require(observer_unit == expected_units["OBSERVER_PROCESS"] and
             recorder_unit == expected_units["RECORDER_PROCESS"],
             "P1_FAULT_PIN_UNIT_NAME_DRIFT")
    inspections = [
        _inspect_unit("OBSERVER_PROCESS", observer_unit),
        _inspect_unit("RECORDER_PROCESS", recorder_unit),
        _inspect_unit("GATEWAY", expected_units["GATEWAY"]),
        _inspect_unit("BROKER_POLICY", expected_units["BROKER_POLICY"]),
    ]
    broker_payload, _broker_metadata = _secure_executable(
        INJECTOR.BROKER_HELPER, "P1_FAULT_PIN_BROKER_HELPER_INVALID")
    result = build_pins(
        spec_snapshot=spec_snapshot, plan_snapshot=plan_snapshot,
        freeze_snapshot=freeze_snapshot, runtime_snapshot=runtime_snapshot,
        formal_campaign_id=formal_campaign_id, boot_id=boot_id,
        producer=producer.reference, inspections=inspections,
        observer_argv=observer_argv, recorder_argv=recorder_argv,
        broker_helper_file_sha256=digest_bytes(broker_payload),
        now_ms=time.time_ns() // 1_000_000)
    producer.reopen()
    for role, pin in _source_pins(freeze_snapshot.document).items():
        payload, _metadata = _secure_executable(
            Path(pin["installed_path"]),
            "P1_FAULT_PIN_INSTALLED_SOURCE_DRIFT")
        _require(digest_bytes(payload) == pin["file_sha256"],
                 "P1_FAULT_PIN_INSTALLED_SOURCE_DRIFT")
        del role
    try:
        published = OBSERVER.publish_receipt(
            result, output, expected_uid=ROOT_UID, expected_gid=ROOT_GID)
    except OBSERVER.ObserverError as error:
        raise PinError("P1_FAULT_PIN_PUBLISH_FAILED") from error
    producer.reopen()
    return published


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run", action="store_true")
    parser.add_argument("--campaign-spec", required=True, type=Path)
    parser.add_argument("--fault-plan", required=True, type=Path)
    parser.add_argument("--freeze-bundle", required=True, type=Path)
    parser.add_argument("--runtime-manifest", required=True, type=Path)
    parser.add_argument("--formal-campaign-id", required=True)
    parser.add_argument("--observer-unit", required=True)
    parser.add_argument("--recorder-unit", required=True)
    parser.add_argument("--observer-exec-argv-json", required=True, type=Path)
    parser.add_argument("--recorder-exec-argv-json", required=True, type=Path)
    for name in (
        "expected-observer-argv-file-sha256",
        "expected-recorder-argv-file-sha256",
        "expected-spec-file-sha256", "expected-spec-body-sha256",
        "expected-plan-file-sha256", "expected-plan-body-sha256",
        "expected-freeze-file-sha256", "expected-freeze-body-sha256",
        "expected-runtime-file-sha256", "expected-runtime-body-sha256",
        "expected-source-manifest-sha256",
    ):
        parser.add_argument("--" + name, required=True)
    parser.add_argument("--boot-id", required=True)
    parser.add_argument("--output", required=True, type=Path)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    arguments = _parser().parse_args(argv)
    try:
        _require(arguments.run, "P1_FAULT_PIN_EXPLICIT_RUN_REQUIRED")
        result = produce(
            campaign_spec_path=arguments.campaign_spec,
            fault_plan_path=arguments.fault_plan,
            freeze_bundle_path=arguments.freeze_bundle,
            runtime_manifest_path=arguments.runtime_manifest,
            formal_campaign_id=arguments.formal_campaign_id,
            observer_unit=arguments.observer_unit,
            recorder_unit=arguments.recorder_unit,
            observer_argv_path=arguments.observer_exec_argv_json,
            recorder_argv_path=arguments.recorder_exec_argv_json,
            expected_observer_argv_file_sha256=
                arguments.expected_observer_argv_file_sha256,
            expected_recorder_argv_file_sha256=
                arguments.expected_recorder_argv_file_sha256,
            expected_spec_file_sha256=arguments.expected_spec_file_sha256,
            expected_spec_body_sha256=arguments.expected_spec_body_sha256,
            expected_plan_file_sha256=arguments.expected_plan_file_sha256,
            expected_plan_body_sha256=arguments.expected_plan_body_sha256,
            expected_freeze_file_sha256=arguments.expected_freeze_file_sha256,
            expected_freeze_body_sha256=arguments.expected_freeze_body_sha256,
            expected_runtime_file_sha256=
                arguments.expected_runtime_file_sha256,
            expected_runtime_body_sha256=
                arguments.expected_runtime_body_sha256,
            expected_source_manifest_sha256=
                arguments.expected_source_manifest_sha256,
            boot_id=arguments.boot_id, output=arguments.output)
    except (PinError, OSError, ValueError) as error:
        reason = error.reason if isinstance(error, PinError) \
            else "P1_FAULT_PIN_UNEXPECTED_FAILURE"
        print("hepta_p1_safety_soak_fault_pin_producer: FAIL " + reason,
              file=sys.stderr)
        return 2
    sys.stdout.buffer.write(canonical_bytes(result))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
