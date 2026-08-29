#!/usr/bin/env python3
"""Run the seven frozen P1 safety-soak faults under a root fail-closed gate.

The production path is intentionally unavailable unless ``--run`` is given,
the process is real root, every input is a root-owned canonical 0600 file, and
the exact ``ProductionExecutor`` is in use.  Test executors can exercise the
transaction and recovery state machine, but can only emit ``REHEARSAL_ONLY``
companion receipts.

This helper never grants PAPER or LIVE authority.  The two token/lease faults
operate only on dedicated non-authority fixtures.  CLOCK_STEP uses an isolated
detector fixture and never changes the host wall clock.
"""

from __future__ import annotations

import argparse
import fcntl
import hashlib
import importlib.machinery
import importlib.util
import json
import os
from pathlib import Path
import re
import secrets
import signal
import stat
import subprocess
import sys
import time
from dataclasses import dataclass
from typing import Any, Mapping, Protocol, Sequence

_script_directory = Path(__file__).absolute().parent
_installed_observer_sibling = (
    _script_directory / "hepta-p1-safety-soak-independent-observer")
_source_observer_sibling = (
    _script_directory / "hepta_p1_safety_soak_independent_observer.py")
try:
    _installed_observer_metadata = os.lstat(_installed_observer_sibling)
except FileNotFoundError:
    _observer_path = _source_observer_sibling
else:
    if (stat.S_ISLNK(_installed_observer_metadata.st_mode) or
            not stat.S_ISREG(_installed_observer_metadata.st_mode)):
        raise RuntimeError("P1_FAULT_INJECTOR_OBSERVER_IMAGE_INVALID")
    _observer_path = _installed_observer_sibling
try:
    _observer_metadata = os.lstat(_observer_path)
except OSError as error:
    raise RuntimeError("P1_FAULT_INJECTOR_OBSERVER_LOAD_FAILED") from error
if (stat.S_ISLNK(_observer_metadata.st_mode) or
        not stat.S_ISREG(_observer_metadata.st_mode)):
    raise RuntimeError("P1_FAULT_INJECTOR_OBSERVER_IMAGE_INVALID")
_observer_loader = importlib.machinery.SourceFileLoader(
    "hepta_p1_safety_soak_independent_observer", str(_observer_path))
_observer_spec = importlib.util.spec_from_loader(
    _observer_loader.name, _observer_loader)
if _observer_spec is None:
    raise RuntimeError("P1_FAULT_INJECTOR_OBSERVER_LOAD_FAILED")
OBSERVER = importlib.util.module_from_spec(_observer_spec)
sys.modules[_observer_loader.name] = OBSERVER
_observer_loader.exec_module(OBSERVER)


VERSION = 1
ROOT_UID = 0
ROOT_GID = 0
MAXIMUM_PIN_LIFETIME_MS = 31 * 24 * 60 * 60 * 1000
RECEIPT_LIFETIME_MS = 4 * 60 * 1000
BOUNDARY_RECHECK_INTERVAL_NS = 1_000_000_000
FIXTURE_RECOVERY_LIFETIME_NS = 10 * 60 * 1_000_000_000
CLOCK_FIXTURE_DELTA_MS = 5000
MAXIMUM_JOURNAL_ENTRIES = 256
MAXIMUM_COMMAND_BYTES = 1024 * 1024

INJECTION_SCHEMA = (
    "hepta.p1-safety-soak-root-fault-injection-receipt.v1")
IDENTITY_SCHEMA = "hepta.p1-safety-soak-fault-target-identity.v1"
PINS_SCHEMA = "hepta.p1-safety-soak-root-fault-injector-pins.v1"
FIXTURE_SCHEMA = "hepta.p1-safety-soak-bounded-fault-fixture.v1"
JOURNAL_SCHEMA = "hepta.p1-safety-soak-root-fault-journal-entry.v1"
CLOCK_RESULT_SCHEMA = "hepta.p1-wall-clock-discontinuity-fixture-result.v1"

SYSTEMCTL = "/usr/bin/systemctl"
BROKER_HELPER = Path("/usr/libexec/hepta-broker-egress-policy")
INJECTOR_HELPER = Path(
    "/usr/libexec/hepta-p1-safety-soak-root-fault-injector")
PIN_PRODUCER_HELPER = Path(
    "/usr/libexec/hepta-p1-safety-soak-fault-pin-producer")
OBSERVER_HELPER = Path(
    "/usr/libexec/hepta-p1-safety-soak-independent-observer")
CLOCK_FIXTURE_HELPER = Path(
    "/usr/libexec/hepta-p1-safety-soak-root-fault-injector")
INJECTOR_PRODUCTION_MODE = "PRODUCTION_ROOT_FAULT_INJECTION"
PIN_PRODUCTION_MODE = "PRODUCTION_ROOT_PINNING"
CLOCK_FIXTURE_PATH = Path(
    "/run/hepta-p1-fault-fixture/wall-clock-discontinuity.json")
TOKEN_FIXTURE_PATH = OBSERVER.TOKEN_FAULT_FIXTURE
LEASE_FIXTURE_PATH = OBSERVER.LEASE_FAULT_FIXTURE
GATEWAY_UNIT = OBSERVER.GATEWAY_UNIT
BROKER_UNIT = OBSERVER.BROKER_UNIT

BOUNDARY_FIELDS = (
    "paper_authorized", "live_authorized", "mutation_authorized",
    "direct_broker_access",
)

INJECTION_FIELDS = OBSERVER.INJECTION_FIELDS
IDENTITY_FIELDS = OBSERVER.FAULT_IDENTITY_FIELDS
PLANNED_FAULT_FIELDS = OBSERVER.PLANNED_FAULT_FIELDS

UNIT_CONTRACT_FIELDS = frozenset({
    "role", "unit", "fragment_path", "fragment_file_sha256",
    "executable_file_sha256", "entrypoint_path",
    "entrypoint_file_sha256", "exec_start_sha256", "exec_argv_sha256",
})
PRODUCER_FIELDS = frozenset({"path", "file_sha256"})
REFERENCE_FIELDS = frozenset({"path", "file_sha256", "body_sha256"})
PINS_FIELDS = frozenset({
    "schema", "version", "status", "issued_at_ms", "expires_at_ms",
    "campaign_id", "formal_campaign_id", "boot_id",
    "source_manifest_sha256", "campaign_spec_file_sha256",
    "campaign_spec_body_sha256", "fault_plan_file_sha256",
    "fault_plan_body_sha256", "freeze_bundle", "runtime_manifest", "producer",
    "production_mode", "injector_id", "injector_path",
    "injector_file_sha256", "observer_helper_path",
    "observer_helper_file_sha256", "unit_contracts",
    "broker_helper_path", "broker_helper_file_sha256",
    "clock_fixture_helper_path", "clock_fixture_helper_file_sha256",
    "clock_fixture_path", "token_fixture_path", "lease_fixture_path",
    "journal_directory", "output_directory", *BOUNDARY_FIELDS,
    "body_sha256",
})
FIXTURE_FIELDS = frozenset({
    "schema", "version", "fixture_type", "campaign_id", "generation",
    "expires_boottime_ns", "valid", "nonce_sha256", *BOUNDARY_FIELDS,
    "body_sha256",
})
JOURNAL_FIELDS = frozenset({
    "schema", "version", "campaign_id", "fault_plan_body_sha256",
    "pins_body_sha256", "sequence", "stage", "recorded_at_ms",
    "recorded_boottime_ns", "boot_id", "fault_id", "fault_type",
    "target_id", "planned_injection_boottime_ns", "pre_identity",
    "actual_injection_boottime_ns", "action_evidence_sha256",
    "recovered_boottime_ns", "post_identity", "output_path",
    "output_file_sha256", "output_body_sha256", "previous_body_sha256",
    "boundary_safe", "cleanup_complete", *BOUNDARY_FIELDS,
    "body_sha256",
})
CLOCK_RESULT_FIELDS = frozenset({
    "schema", "version", "operation", "fixture_path", "generation",
    "wall_clock_delta_ms", "host_clock_mutated", "body_sha256",
})

ALLOWED_STAGES = frozenset({
    "PREPARE_INTENT", "PREPARE_RESULT",
    "ACTION_INTENT", "ACTION_RESULT", "RECOVERY_INTENT",
    "RECOVERY_RESULT", "CLEANUP_INTENT", "CLEANUP_RESULT",
    "PUBLISH_INTENT", "PUBLISHED", "FAILED_CLOSED",
})
UNIT_ROLES = frozenset({
    "OBSERVER_PROCESS", "RECORDER_PROCESS", "GATEWAY", "BROKER_POLICY",
})

EXPECTED_FAULT_ORDER = (
    "PROCESS_KILL", "SERVICE_RESTART", "TOKEN_LOSS", "LEASE_EXPIRY",
    "NETWORK_DENY_RELOAD", "EVIDENCE_WRITER_CRASH", "CLOCK_STEP",
)

UNIT_PROPERTIES = (
    "LoadState", "ActiveState", "SubState", "UnitFileState", "MainPID",
    "InvocationID", "ExecMainStartTimestampMonotonic", "NRestarts",
    "FragmentPath", "ExecStart",
)


class InjectorError(RuntimeError):
    """Stable fail-closed injector error."""

    def __init__(self, reason: str):
        super().__init__(reason)
        self.reason = reason


class InjectedCrash(RuntimeError):
    """Test-only representation of a process crash at a journal seam."""


def _require(condition: bool, reason: str) -> None:
    if not condition:
        raise InjectorError(reason)


def _boundary() -> dict[str, bool]:
    return {field: False for field in BOUNDARY_FIELDS}


def _canonical_bytes(value: Any) -> bytes:
    try:
        return (json.dumps(
            value, ensure_ascii=True, allow_nan=False, sort_keys=True,
            separators=(",", ":")) + "\n").encode("ascii")
    except (TypeError, ValueError, UnicodeError) as error:
        raise InjectorError("P1_FAULT_INJECTOR_CANONICALIZATION_FAILED") \
            from error


def _digest_bytes(payload: bytes) -> str:
    return "sha256:" + hashlib.sha256(payload).hexdigest()


def _seal(body: Mapping[str, Any]) -> dict[str, Any]:
    document = dict(body)
    document["body_sha256"] = _digest_bytes(_canonical_bytes(document))
    return document


def _validate_seal(document: Mapping[str, Any], reason: str) -> None:
    claimed = document.get("body_sha256")
    body = dict(document)
    body.pop("body_sha256", None)
    _require(
        isinstance(claimed, str) and
        OBSERVER.DIGEST.fullmatch(claimed) is not None and
        claimed == _digest_bytes(_canonical_bytes(body)), reason)


def _exact(value: Any, fields: frozenset[str], reason: str) \
        -> dict[str, Any]:
    _require(isinstance(value, dict) and set(value) == fields, reason)
    return value


def _identifier(value: Any, reason: str) -> str:
    _require(isinstance(value, str) and
             OBSERVER.IDENTIFIER.fullmatch(value) is not None, reason)
    return value


def _digest(value: Any, reason: str) -> str:
    _require(isinstance(value, str) and
             OBSERVER.DIGEST.fullmatch(value) is not None, reason)
    return value


def _integer(value: Any, reason: str, minimum: int = 0) -> int:
    _require(type(value) is int and value >= minimum, reason)
    return value


def _path(value: Any, reason: str) -> Path:
    _require(isinstance(value, str), reason)
    path = Path(value)
    _require(path.is_absolute() and Path(os.path.normpath(value)) == path and
             path.name not in {"", ".", ".."} and
             all(part not in {"", ".", ".."} for part in path.parts[1:]),
             reason)
    return path


def _reject_authority(value: Any, reason: str) -> None:
    if isinstance(value, dict):
        for key, child in value.items():
            if key in {*BOUNDARY_FIELDS, "paper_test_admission_candidate",
                       "order_submission_authorized", "mutation_attempted"}:
                _require(child is False, reason)
            _reject_authority(child, reason)
    elif isinstance(value, list):
        for child in value:
            _reject_authority(child, reason)


def _trusted_directory(path: Path, uid: int, gid: int, reason: str) \
        -> tuple[int, int, int, int, int]:
    path = _path(str(path), reason)
    descriptor = -1
    allowed_uids = {0, ROOT_UID, uid}
    try:
        flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC
        descriptor = os.open("/", flags)
        root = os.fstat(descriptor)
        _require(
            stat.S_ISDIR(root.st_mode) and root.st_uid in allowed_uids and
            stat.S_IMODE(root.st_mode) & 0o022 == 0, reason)
        metadata = root
        for component in path.parts[1:]:
            before = os.stat(
                component, dir_fd=descriptor, follow_symlinks=False)
            _require(
                stat.S_ISDIR(before.st_mode) and
                before.st_uid in allowed_uids and
                stat.S_IMODE(before.st_mode) & 0o022 == 0, reason)
            child = os.open(component, flags, dir_fd=descriptor)
            opened = os.fstat(child)
            _require(
                (before.st_dev, before.st_ino, before.st_mode,
                 before.st_uid, before.st_gid) ==
                (opened.st_dev, opened.st_ino, opened.st_mode,
                 opened.st_uid, opened.st_gid), reason)
            os.close(descriptor)
            descriptor = child
            metadata = opened
        _require(metadata.st_uid == uid and metadata.st_gid == gid, reason)
        return (
            metadata.st_dev, metadata.st_ino, metadata.st_uid,
            metadata.st_gid, stat.S_IMODE(metadata.st_mode),
        )
    except (OSError, InjectorError) as error:
        if isinstance(error, InjectorError):
            raise
        raise InjectorError(reason) from error
    finally:
        if descriptor >= 0:
            os.close(descriptor)


def _assert_directory(
    path: Path, identity: tuple[int, int, int, int, int],
    uid: int, gid: int, reason: str,
) -> None:
    _require(_trusted_directory(path, uid, gid, reason) == identity, reason)


def _state_seal(value: Mapping[str, Any]) -> dict[str, Any]:
    document = dict(value)
    document["state_sha256"] = _digest_bytes(_canonical_bytes(document))
    return document


def _read_exact_snapshot(path: Path, uid: int, gid: int) -> OBSERVER.Snapshot:
    try:
        return OBSERVER.load_snapshot(
            path, expected_uid=uid, expected_gid=gid)
    except OBSERVER.ObserverError as error:
        raise InjectorError("P1_FAULT_INJECTOR_INPUT_INVALID") from error


def _assert_snapshot(snapshot: OBSERVER.Snapshot, uid: int, gid: int) -> None:
    current = _read_exact_snapshot(snapshot.path, uid, gid)
    _require(
        current.payload == snapshot.payload and
        OBSERVER._file_identity(current.metadata) ==
            OBSERVER._file_identity(snapshot.metadata),
        "P1_FAULT_INJECTOR_INPUT_DRIFT")


def _read_boot_id() -> str:
    try:
        value = Path("/proc/sys/kernel/random/boot_id").read_text(
            encoding="ascii").strip()
    except (OSError, UnicodeError) as error:
        raise InjectorError("P1_FAULT_INJECTOR_BOOT_ID_UNAVAILABLE") from error
    _require(OBSERVER.BOOT_ID.fullmatch(value) is not None,
             "P1_FAULT_INJECTOR_BOOT_ID_INVALID")
    return value


@dataclass(frozen=True)
class FrozenInputs:
    spec_snapshot: OBSERVER.Snapshot
    plan_snapshot: OBSERVER.Snapshot
    pins_snapshot: OBSERVER.Snapshot
    spec: dict[str, Any]
    faults: tuple[dict[str, Any], ...]
    pins: dict[str, Any]
    owner_uid: int
    owner_gid: int
    input_parent_identities: tuple[
        tuple[int, int, int, int, int], ...]

    def assert_unchanged(self) -> None:
        snapshots = (
            self.spec_snapshot, self.plan_snapshot, self.pins_snapshot)
        _require(len(self.input_parent_identities) == len(snapshots),
                 "P1_FAULT_INJECTOR_INPUT_PARENT_DRIFT")
        for snapshot, parent_identity in zip(
                snapshots, self.input_parent_identities):
            _assert_directory(
                snapshot.path.parent, parent_identity,
                self.owner_uid, self.owner_gid,
                "P1_FAULT_INJECTOR_INPUT_PARENT_DRIFT")
            _assert_snapshot(snapshot, self.owner_uid, self.owner_gid)
            _assert_directory(
                snapshot.path.parent, parent_identity,
                self.owner_uid, self.owner_gid,
                "P1_FAULT_INJECTOR_INPUT_PARENT_DRIFT")


def _unit_names(formal_campaign_id: str) -> dict[str, str]:
    try:
        round_number = OBSERVER._round(formal_campaign_id)
    except OBSERVER.ObserverError as error:
        raise InjectorError("P1_FAULT_INJECTOR_FORMAL_CAMPAIGN_INVALID") \
            from error
    return {
        "OBSERVER_PROCESS":
            f"hepta-p1-shadow-independent-observer-round{round_number}.service",
        "RECORDER_PROCESS":
            f"hepta-p1-shadow-evidence-recorder-round{round_number}.service",
        "GATEWAY": GATEWAY_UNIT,
        "BROKER_POLICY": BROKER_UNIT,
    }


def validate_pins(
    document: dict[str, Any], spec_snapshot: OBSERVER.Snapshot,
    plan_snapshot: OBSERVER.Snapshot, spec: Mapping[str, Any],
    *, now_ms: int, boot_id: str,
) -> dict[str, Any]:
    reason = "P1_FAULT_INJECTOR_PINS_INVALID"
    _exact(document, PINS_FIELDS, reason)
    _validate_seal(document, reason)
    _reject_authority(document, reason)
    _require(
        document.get("schema") == PINS_SCHEMA and
        document.get("version") == VERSION and
        document.get("status") == "FROZEN" and
        document.get("campaign_id") == spec.get("campaign_id") and
        document.get("boot_id") == boot_id and
        document.get("source_manifest_sha256") ==
            spec.get("source_manifest_sha256") and
        document.get("campaign_spec_file_sha256") ==
            spec_snapshot.file_sha256 and
        document.get("campaign_spec_body_sha256") ==
            spec_snapshot.body_sha256 and
        document.get("fault_plan_file_sha256") == plan_snapshot.file_sha256 and
        document.get("fault_plan_body_sha256") == plan_snapshot.body_sha256 and
        document.get("fault_plan_body_sha256") ==
            spec.get("fault_plan_body_sha256") and
        document.get("freeze_bundle") == spec.get("freeze_bundle") and
        document.get("production_mode") == PIN_PRODUCTION_MODE and
        document.get("injector_path") == str(INJECTOR_HELPER) and
        document.get("observer_helper_path") == str(OBSERVER_HELPER), reason)
    producer = _exact(document.get("producer"), PRODUCER_FIELDS, reason)
    _require(producer.get("path") == str(PIN_PRODUCER_HELPER), reason)
    _digest(producer.get("file_sha256"), reason)
    freeze_bundle = _exact(
        document.get("freeze_bundle"), REFERENCE_FIELDS, reason)
    _path(freeze_bundle.get("path"), reason)
    _digest(freeze_bundle.get("file_sha256"), reason)
    _digest(freeze_bundle.get("body_sha256"), reason)
    runtime_manifest = _exact(
        document.get("runtime_manifest"), REFERENCE_FIELDS, reason)
    _path(runtime_manifest.get("path"), reason)
    _digest(runtime_manifest.get("file_sha256"), reason)
    _digest(runtime_manifest.get("body_sha256"), reason)
    _identifier(document.get("injector_id"), reason)
    formal_id = _identifier(document.get("formal_campaign_id"), reason)
    _require(any(
        item.get("campaign_id") == formal_id
        for item in spec.get("formal_campaigns", [])), reason)
    issued = _integer(document.get("issued_at_ms"), reason)
    expires = _integer(document.get("expires_at_ms"), reason, issued + 1)
    _require(
        issued <= now_ms < expires and
        expires - issued <= MAXIMUM_PIN_LIFETIME_MS, reason)
    for field in (
        "source_manifest_sha256", "campaign_spec_file_sha256",
        "campaign_spec_body_sha256", "fault_plan_file_sha256",
        "fault_plan_body_sha256", "broker_helper_file_sha256",
        "clock_fixture_helper_file_sha256", "injector_file_sha256",
        "observer_helper_file_sha256",
    ):
        _digest(document.get(field), reason)
    _require(
        document.get("broker_helper_path") == str(BROKER_HELPER) and
        document.get("clock_fixture_helper_path") ==
            str(CLOCK_FIXTURE_HELPER) and
        document.get("clock_fixture_path") == str(CLOCK_FIXTURE_PATH) and
        document.get("token_fixture_path") == str(TOKEN_FIXTURE_PATH) and
        document.get("lease_fixture_path") == str(LEASE_FIXTURE_PATH), reason)
    expected_units = _unit_names(formal_id)
    contracts = document.get("unit_contracts")
    _require(isinstance(contracts, list) and len(contracts) == 4, reason)
    roles: list[str] = []
    for raw in contracts:
        contract = _exact(raw, UNIT_CONTRACT_FIELDS, reason)
        role = contract.get("role")
        _require(role in UNIT_ROLES and
                 contract.get("unit") == expected_units[role], reason)
        fragment = _path(contract.get("fragment_path"), reason)
        if role in {"OBSERVER_PROCESS", "RECORDER_PROCESS"}:
            _require(fragment.parent == Path("/run/systemd/transient"), reason)
        else:
            _require(fragment.parent in {
                Path("/usr/lib/systemd/system"),
                Path("/etc/systemd/system"),
            }, reason)
        _digest(contract.get("fragment_file_sha256"), reason)
        _digest(contract.get("executable_file_sha256"), reason)
        entrypoint = _path(contract.get("entrypoint_path"), reason)
        _require(entrypoint in {
            Path("/usr/libexec/hepta-p1-safety-soak-observer-worker"),
            Path("/usr/libexec/hepta-p1-safety-soak-recorder-worker"),
            OBSERVER_HELPER, BROKER_HELPER,
        } or role in {"GATEWAY", "BROKER_POLICY"}, reason)
        _digest(contract.get("entrypoint_file_sha256"), reason)
        _digest(contract.get("exec_start_sha256"), reason)
        _digest(contract.get("exec_argv_sha256"), reason)
        roles.append(role)
    _require(sorted(roles) == sorted(UNIT_ROLES), reason)
    for field in ("journal_directory", "output_directory"):
        _path(document.get(field), reason)
    return document


def load_inputs(
    spec_path: Path, plan_path: Path, pins_path: Path, *, owner_uid: int,
    owner_gid: int, expected_campaign_id: str,
    expected_formal_campaign_id: str, expected_boot_id: str,
    expected_source_manifest_sha256: str,
    expected_spec_body_sha256: str, expected_plan_body_sha256: str,
    now_ms: int,
) -> FrozenInputs:
    input_paths = (spec_path, plan_path, pins_path)
    parent_identities = tuple(
        _trusted_directory(
            path.parent, owner_uid, owner_gid,
            "P1_FAULT_INJECTOR_INPUT_PARENT_UNTRUSTED")
        for path in input_paths)
    spec_snapshot = _read_exact_snapshot(spec_path, owner_uid, owner_gid)
    plan_snapshot = _read_exact_snapshot(plan_path, owner_uid, owner_gid)
    pins_snapshot = _read_exact_snapshot(pins_path, owner_uid, owner_gid)
    try:
        spec = OBSERVER.validate_spec(spec_snapshot.document)
        raw_faults = OBSERVER.validate_plan(plan_snapshot.document, spec)
    except OBSERVER.ObserverError as error:
        raise InjectorError("P1_FAULT_INJECTOR_FROZEN_INPUT_INVALID") from error
    _require(
        spec.get("campaign_id") == expected_campaign_id and
        spec.get("source_manifest_sha256") ==
            expected_source_manifest_sha256 and
        spec_snapshot.body_sha256 == expected_spec_body_sha256 and
        plan_snapshot.body_sha256 == expected_plan_body_sha256,
        "P1_FAULT_INJECTOR_EXPLICIT_PIN_DRIFT")
    fault_types = [item["fault_type"] for item in raw_faults]
    _require(
        len(raw_faults) == len(EXPECTED_FAULT_ORDER) and
        len({item["fault_id"] for item in raw_faults}) ==
            len(EXPECTED_FAULT_ORDER) and
        set(fault_types) == set(EXPECTED_FAULT_ORDER),
        "P1_FAULT_INJECTOR_FAULT_SET_INVALID")
    for before, after in zip(raw_faults, raw_faults[1:]):
        _require(
            after["inject_at_boottime_ns"] >
                before["inject_at_boottime_ns"] +
                before["maximum_injection_lateness_ns"] +
                before["maximum_recovery_ns"],
            "P1_FAULT_INJECTOR_FAULT_WINDOWS_OVERLAP")
    pins = validate_pins(
        pins_snapshot.document, spec_snapshot, plan_snapshot, spec,
        now_ms=now_ms, boot_id=expected_boot_id)
    _require(
        pins.get("campaign_id") == expected_campaign_id and
        pins.get("formal_campaign_id") == expected_formal_campaign_id,
        "P1_FAULT_INJECTOR_EXPLICIT_PIN_DRIFT")
    frozen = FrozenInputs(
        spec_snapshot=spec_snapshot, plan_snapshot=plan_snapshot,
        pins_snapshot=pins_snapshot, spec=spec,
        faults=tuple(dict(item) for item in raw_faults), pins=pins,
        owner_uid=owner_uid, owner_gid=owner_gid,
        input_parent_identities=parent_identities)
    frozen.assert_unchanged()
    return frozen


def validate_fixture(
    document: dict[str, Any], *, fixture_type: str, campaign_id: str,
    reason: str,
) -> dict[str, Any]:
    _exact(document, FIXTURE_FIELDS, reason)
    _validate_seal(document, reason)
    _reject_authority(document, reason)
    _require(
        document.get("schema") == FIXTURE_SCHEMA and
        document.get("version") == VERSION and
        document.get("fixture_type") == fixture_type and
        document.get("campaign_id") == campaign_id and
        type(document.get("valid")) is bool, reason)
    _integer(document.get("generation"), reason)
    _integer(document.get("expires_boottime_ns"), reason)
    _digest(document.get("nonce_sha256"), reason)
    return document


def _fixture_document(
    fixture_type: str, campaign_id: str, generation: int,
    expires_boottime_ns: int, valid: bool,
) -> dict[str, Any]:
    nonce = _digest_bytes(secrets.token_bytes(32))
    return _seal({
        "schema": FIXTURE_SCHEMA, "version": VERSION,
        "fixture_type": fixture_type, "campaign_id": campaign_id,
        "generation": generation,
        "expires_boottime_ns": expires_boottime_ns, "valid": valid,
        "nonce_sha256": nonce, **_boundary(),
    })


@dataclass(frozen=True)
class ActionState:
    actual_boottime_ns: int
    evidence_sha256: str


@dataclass(frozen=True)
class RecoveryState:
    recovered_boottime_ns: int
    post_identity: dict[str, Any]


class Executor(Protocol):
    def clock(self) -> OBSERVER.ClockSample: ...
    def wait_until(self, boottime_ns: int) -> None: ...
    def assert_boundary(self) -> None: ...
    def prepare(
        self, fault: Mapping[str, Any], frozen: FrozenInputs,
    ) -> None: ...
    def pre_identity(
        self, fault: Mapping[str, Any], frozen: FrozenInputs,
    ) -> dict[str, Any]: ...
    def inject(
        self, fault: Mapping[str, Any], pre: Mapping[str, Any],
        frozen: FrozenInputs,
    ) -> ActionState: ...
    def resume_action(
        self, fault: Mapping[str, Any], pre: Mapping[str, Any],
        frozen: FrozenInputs, intent_boottime_ns: int,
    ) -> ActionState: ...
    def recover(
        self, fault: Mapping[str, Any], pre: Mapping[str, Any],
        action: ActionState, frozen: FrozenInputs,
    ) -> RecoveryState: ...
    def fail_close(
        self, fault: Mapping[str, Any], pre: Mapping[str, Any] | None,
        action: ActionState | None, frozen: FrozenInputs,
        intent_boottime_ns: int,
    ) -> RecoveryState | None: ...
    def cleanup(
        self, fault: Mapping[str, Any], recovery: RecoveryState,
        frozen: FrozenInputs,
    ) -> None: ...
    def cleanup_residue(self, frozen: FrozenInputs) -> None: ...
    def after_journal(self, stage: str) -> None: ...


class ProductionExecutor:
    """The only executor allowed to produce COMPLETE receipts."""

    def __init__(self, frozen: FrozenInputs):
        self.frozen = frozen
        self.host = OBSERVER.ReadOnlyHost()
        self.layout = OBSERVER.Layout()
        self.observer = OBSERVER.IndependentObserver(
            self.host, layout=self.layout)
        self.contracts = {
            item["role"]: dict(item) for item in frozen.pins["unit_contracts"]
        }
        self._verify_executing_image(
            frozen.pins["injector_file_sha256"])
        self._verify_observer_image(
            frozen.pins["observer_helper_file_sha256"])
        self._verify_file(
            PIN_PRODUCER_HELPER, frozen.pins["producer"]["file_sha256"])
        self._verify_file(
            CLOCK_FIXTURE_HELPER,
            frozen.pins["clock_fixture_helper_file_sha256"])
        self._verify_file(
            INJECTOR_HELPER, frozen.pins["injector_file_sha256"])
        self._verify_file(
            OBSERVER_HELPER, frozen.pins["observer_helper_file_sha256"])
        self._verify_file(
            BROKER_HELPER, frozen.pins["broker_helper_file_sha256"])

    @staticmethod
    def _verify_executing_image(expected_sha: str) -> None:
        running = Path(__file__)
        try:
            lexical = running.absolute()
            metadata = os.lstat(lexical)
            resolved = lexical.resolve(strict=True)
            installed = CLOCK_FIXTURE_HELPER.resolve(strict=True)
            same = os.path.samefile(resolved, installed)
        except OSError as error:
            raise InjectorError(
                "P1_FAULT_INJECTOR_EXECUTING_IMAGE_NOT_INSTALLED") from error
        _require(
            not stat.S_ISLNK(metadata.st_mode) and
            resolved == installed == CLOCK_FIXTURE_HELPER and same,
            "P1_FAULT_INJECTOR_EXECUTING_IMAGE_DRIFT")
        try:
            payload, reopened = OBSERVER.secure_read(
                installed, expected_uid=ROOT_UID, expected_gid=ROOT_GID,
                modes=frozenset({0o500, 0o555, 0o700, 0o755}))
        except OBSERVER.ObserverError as error:
            raise InjectorError(
                "P1_FAULT_INJECTOR_EXECUTING_IMAGE_INVALID") from error
        final = os.stat(lexical, follow_symlinks=False)
        _require(
            OBSERVER._file_identity(metadata) ==
                OBSERVER._file_identity(reopened) ==
                OBSERVER._file_identity(final) and
            _digest_bytes(payload) == expected_sha,
            "P1_FAULT_INJECTOR_EXECUTING_IMAGE_DRIFT")

    @staticmethod
    def _verify_observer_image(expected_sha: str) -> None:
        reason = "P1_FAULT_INJECTOR_OBSERVER_IMAGE_DRIFT"
        module_file = getattr(OBSERVER, "__file__", None)
        _require(isinstance(module_file, str) and bool(module_file), reason)
        lexical = Path(module_file).absolute()
        try:
            metadata = os.lstat(lexical)
            resolved = lexical.resolve(strict=True)
            installed = OBSERVER_HELPER.resolve(strict=True)
            same = os.path.samefile(resolved, installed)
        except OSError as error:
            raise InjectorError(
                "P1_FAULT_INJECTOR_OBSERVER_IMAGE_NOT_INSTALLED") from error
        _require(
            not stat.S_ISLNK(metadata.st_mode) and
            resolved == installed == OBSERVER_HELPER and same, reason)
        try:
            payload, reopened = OBSERVER.secure_read(
                installed, expected_uid=ROOT_UID, expected_gid=ROOT_GID,
                modes=frozenset({0o500, 0o555, 0o700, 0o755}))
        except OBSERVER.ObserverError as error:
            raise InjectorError(reason) from error
        final = os.stat(lexical, follow_symlinks=False)
        _require(
            OBSERVER._file_identity(metadata) ==
                OBSERVER._file_identity(reopened) ==
                OBSERVER._file_identity(final) and
            _digest_bytes(payload) == expected_sha,
            reason)

    def clock(self) -> OBSERVER.ClockSample:
        try:
            return self.host.clock()
        except OBSERVER.ObserverError as error:
            raise InjectorError("P1_FAULT_INJECTOR_CLOCK_FAILED") from error

    @staticmethod
    def _run(arguments: tuple[str, ...]) -> subprocess.CompletedProcess[bytes]:
        show_prefix = (
            SYSTEMCTL, "show", "--no-pager",
            *(f"--property={name}" for name in UNIT_PROPERTIES),
        )
        allowed_show = (
            len(arguments) == len(show_prefix) + 1 and
            arguments[:-1] == show_prefix and
            OBSERVER.UNIT_NAME.fullmatch(arguments[-1]) is not None)
        allowed_restart = arguments in {
            (SYSTEMCTL, "restart", GATEWAY_UNIT),
            (SYSTEMCTL, "restart", BROKER_UNIT),
        }
        allowed_broker = arguments in {
            (str(BROKER_HELPER), "--tighten-deny-all"),
            (str(BROKER_HELPER), "--check-deny-all"),
        }
        _require(
            allowed_show or allowed_restart or allowed_broker,
            "P1_FAULT_INJECTOR_COMMAND_NOT_ALLOWLISTED")
        try:
            result = subprocess.run(
                arguments, check=False, stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                env={"PATH": "/usr/sbin:/usr/bin:/sbin:/bin", "LANG": "C",
                     "LC_ALL": "C", "PYTHONNOUSERSITE": "1"},
                cwd="/", timeout=30)
        except (OSError, subprocess.SubprocessError) as error:
            raise InjectorError("P1_FAULT_INJECTOR_COMMAND_FAILED") from error
        _require(
            len(result.stdout) <= MAXIMUM_COMMAND_BYTES and
            len(result.stderr) <= MAXIMUM_COMMAND_BYTES,
            "P1_FAULT_INJECTOR_COMMAND_OUTPUT_TOO_LARGE")
        return result

    def _verify_file(
        self, path: Path, expected_sha: str,
        modes: frozenset[int] = frozenset({0o500, 0o555, 0o700, 0o755}),
    ) -> None:
        try:
            payload, _metadata = OBSERVER.secure_read(
                path, expected_uid=ROOT_UID, expected_gid=ROOT_GID,
                modes=modes)
        except OBSERVER.ObserverError as error:
            raise InjectorError("P1_FAULT_INJECTOR_PINNED_FILE_INVALID") \
                from error
        _require(_digest_bytes(payload) == expected_sha,
                 "P1_FAULT_INJECTOR_PINNED_FILE_DRIFT")

    def _show(self, role: str) -> dict[str, Any]:
        contract = self.contracts[role]
        unit = contract["unit"]
        result = self._run((
            SYSTEMCTL, "show", "--no-pager",
            *(f"--property={name}" for name in UNIT_PROPERTIES), unit))
        _require(result.returncode == 0 and not result.stderr,
                 "P1_FAULT_INJECTOR_SYSTEMD_SHOW_FAILED")
        try:
            fields: dict[str, str] = {}
            for line in result.stdout.decode(
                    "utf-8", errors="strict").splitlines():
                key, raw = line.split("=", 1)
                _require(key in UNIT_PROPERTIES and key not in fields,
                         "P1_FAULT_INJECTOR_SYSTEMD_SHOW_INVALID")
                fields[key] = raw
        except (UnicodeError, ValueError) as error:
            raise InjectorError(
                "P1_FAULT_INJECTOR_SYSTEMD_SHOW_INVALID") from error
        _require(set(fields) == set(UNIT_PROPERTIES) and
                 fields["FragmentPath"] == contract["fragment_path"] and
                 bool(fields["ExecStart"]) and
                 _digest_bytes(fields["ExecStart"].encode("utf-8")) ==
                    contract["exec_start_sha256"],
                 "P1_FAULT_INJECTOR_UNIT_PIN_DRIFT")
        self._verify_file(
            Path(contract["fragment_path"]), contract["fragment_file_sha256"],
            frozenset({0o400, 0o444, 0o600, 0o644}))
        self._verify_file(
            Path(contract["entrypoint_path"]),
            contract["entrypoint_file_sha256"])
        try:
            pid = int(fields["MainPID"])
            start = int(fields["ExecMainStartTimestampMonotonic"] or "0")
            restarts = int(fields["NRestarts"] or "0")
        except ValueError as error:
            raise InjectorError(
                "P1_FAULT_INJECTOR_SYSTEMD_SHOW_INVALID") from error
        invocation = fields["InvocationID"]
        _require(
            fields["LoadState"] == "loaded" and
            fields["ActiveState"] == "active" and
            fields["SubState"] == "running" and pid > 1 and start > 0 and
            restarts >= 0 and OBSERVER.INVOCATION_ID.fullmatch(invocation)
            is not None, "P1_FAULT_INJECTOR_UNIT_NOT_HEALTHY")
        state = _state_seal({
            "unit": unit, "load_state": fields["LoadState"],
            "active_state": fields["ActiveState"],
            "sub_state": fields["SubState"],
            "unit_file_state": fields["UnitFileState"], "main_pid": pid,
            "invocation_id": invocation,
            "exec_main_start_timestamp_monotonic_us": start,
            "n_restarts": restarts,
        })
        process = self.host.process(pid)
        self._verify_process_executable(
            pid, process, contract["executable_file_sha256"])
        return {"unit": state, "process": process}

    def _verify_process_executable(
        self, pid: int, process: Mapping[str, Any], expected_sha: str,
    ) -> None:
        try:
            target = Path(os.readlink(f"/proc/{pid}/exe"))
            _require(target.is_absolute(),
                     "P1_FAULT_INJECTOR_PROCESS_EXECUTABLE_INVALID")
            payload, metadata = OBSERVER.secure_read(
                target, expected_uid=ROOT_UID, expected_gid=None,
                modes=frozenset({0o500, 0o550, 0o555, 0o700, 0o750, 0o755}),
                maximum=64 * 1024 * 1024)
        except (OSError, OBSERVER.ObserverError) as error:
            raise InjectorError(
                "P1_FAULT_INJECTOR_PROCESS_EXECUTABLE_INVALID") from error
        _require(
            metadata.st_dev == process["exe_device"] and
            metadata.st_ino == process["exe_inode"] and
            _digest_bytes(payload) == expected_sha,
            "P1_FAULT_INJECTOR_PROCESS_EXECUTABLE_DRIFT")

    def _context(
        self, fault: Mapping[str, Any],
    ) -> tuple[str, int, int, list[dict[str, Any]]]:
        sample = self.clock()
        try:
            marker, status, lease, marker_value, _status, _environment, \
                generation = self.observer._service_documents(
                    self.frozen.spec,
                    fault["formal_campaign_id"], sample)
        except OBSERVER.ObserverError as error:
            raise InjectorError("P1_FAULT_INJECTOR_WATCH_CONTEXT_INVALID") \
                from error
        return (
            marker_value["execution_service_epoch"],
            marker_value["execution_service_fencing_generation"], generation,
            sorted([
                marker.path_identity, status.path_identity,
                lease.path_identity,
            ], key=lambda item: item["path"]),
        )

    def _broker(self) -> dict[str, Any]:
        try:
            value = self.host.broker()
        except OBSERVER.ObserverError as error:
            raise InjectorError("P1_FAULT_INJECTOR_BROKER_CHECK_FAILED") \
                from error
        _require(
            value["helper_file_sha256"] ==
                self.frozen.pins["broker_helper_file_sha256"],
            "P1_FAULT_INJECTOR_BROKER_HELPER_DRIFT")
        return value

    def _fixture(self, fault_type: str) -> tuple[Path, dict[str, Any]]:
        path = TOKEN_FIXTURE_PATH if fault_type == "TOKEN_LOSS" \
            else LEASE_FIXTURE_PATH
        snapshot = _read_exact_snapshot(path, ROOT_UID, ROOT_GID)
        fixture = validate_fixture(
            snapshot.document, fixture_type=fault_type,
            campaign_id=self.frozen.spec["campaign_id"],
            reason="P1_FAULT_INJECTOR_FIXTURE_INVALID")
        return path, fixture

    def _identity(
        self, fault: Mapping[str, Any], phase: str, *,
        wall_clock_delta_ms: int | None = None,
    ) -> dict[str, Any]:
        fault_type = fault["fault_type"]
        sample = self.clock()
        epoch, fence, lease, paths = self._context(fault)
        units: list[dict[str, Any]] = []
        processes: list[dict[str, Any]] = []
        fixture_generation: int | None = None
        fixture_expiry: int | None = None
        fixture_valid: bool | None = None
        if fault_type in {"PROCESS_KILL", "EVIDENCE_WRITER_CRASH"}:
            role = "OBSERVER_PROCESS" if fault_type == "PROCESS_KILL" \
                else "RECORDER_PROCESS"
            observed = self._show(role)
            processes = [observed["process"]]
        elif fault_type == "SERVICE_RESTART":
            units = [self._show("GATEWAY")["unit"]]
        elif fault_type == "NETWORK_DENY_RELOAD":
            units = [self._show("BROKER_POLICY")["unit"]]
        elif fault_type in {"TOKEN_LOSS", "LEASE_EXPIRY"}:
            path, fixture = self._fixture(fault_type)
            paths.append(self.host.path(path, "json"))
            paths.sort(key=lambda item: item["path"])
            fixture_generation = fixture["generation"]
            fixture_expiry = fixture["expires_boottime_ns"]
            fixture_valid = fixture["valid"]
        broker = self._broker()
        document = _seal({
            "schema": IDENTITY_SCHEMA, "version": VERSION, "phase": phase,
            "target_id": fault["target_id"], "boot_id": sample.boot_id,
            "observed_boottime_ns": sample.boottime_ns,
            "service_epoch": epoch, "fencing_generation": fence,
            "lease_generation": lease, "systemd_units": units,
            "processes": processes, "paths": paths,
            "broker_deny_all": broker, "residue_count": 0,
            "wall_clock_delta_ms": wall_clock_delta_ms,
            "fixture_generation": fixture_generation,
            "fixture_expires_boottime_ns": fixture_expiry,
            "fixture_valid": fixture_valid,
        })
        try:
            return OBSERVER.validate_fault_identity(
                document, phase, fault["target_id"], fault_type,
                "P1_FAULT_INJECTOR_IDENTITY_INVALID")
        except OBSERVER.ObserverError as error:
            raise InjectorError("P1_FAULT_INJECTOR_IDENTITY_INVALID") \
                from error

    def wait_until(self, boottime_ns: int) -> None:
        while True:
            sample = self.clock()
            if sample.boottime_ns >= boottime_ns:
                return
            self.assert_boundary()
            remaining = boottime_ns - sample.boottime_ns
            time.sleep(min(1.0, remaining / 1_000_000_000))

    def assert_boundary(self) -> None:
        try:
            broker = self._broker()
            kill = self.host.path(self.layout.kill_switch, "bytes")
            paper = [self.host.unit(unit) for unit in OBSERVER.PAPER_UNITS]
        except OBSERVER.ObserverError as error:
            raise InjectorError("P1_FAULT_INJECTOR_BOUNDARY_UNCERTAIN") \
                from error
        _require(
            broker["deny_all"] is True and
            broker["authorized_connector_count"] == 0 and
            broker["authorized_uids"] == [] and
            broker["protected_port_count"] > 0 and
            kill["present"] is True and
            kill["content_file_sha256"] == _digest_bytes(b"engaged") and
            all(item["active_state"] == "inactive" and
                item["main_pid"] == 0 for item in paper),
            "P1_FAULT_INJECTOR_BOUNDARY_LOST")

    def pre_identity(
        self, fault: Mapping[str, Any], frozen: FrozenInputs,
    ) -> dict[str, Any]:
        del frozen
        delta = 0 if fault["fault_type"] == "CLOCK_STEP" else None
        identity = self._identity(fault, "PRE", wall_clock_delta_ms=delta)
        if fault["fault_type"] in {"TOKEN_LOSS", "LEASE_EXPIRY"}:
            _require(identity["fixture_valid"] is True,
                     "P1_FAULT_INJECTOR_FIXTURE_NOT_VALID")
        if fault["fault_type"] == "LEASE_EXPIRY":
            _require(
                identity["fixture_expires_boottime_ns"] ==
                    fault["inject_at_boottime_ns"],
                "P1_FAULT_INJECTOR_LEASE_FIXTURE_EXPIRY_DRIFT")
        return identity

    def prepare(
        self, fault: Mapping[str, Any], frozen: FrozenInputs,
    ) -> None:
        fault_type = fault["fault_type"]
        if fault_type in {"TOKEN_LOSS", "LEASE_EXPIRY"}:
            path = TOKEN_FIXTURE_PATH if fault_type == "TOKEN_LOSS" \
                else LEASE_FIXTURE_PATH
            if path.exists():
                _path_value, fixture = self._fixture(fault_type)
                _require(
                    fixture["valid"] is True and
                    (fault_type != "LEASE_EXPIRY" or
                     fixture["expires_boottime_ns"] ==
                        fault["inject_at_boottime_ns"]),
                    "P1_FAULT_INJECTOR_FIXTURE_PREPARE_DRIFT")
            else:
                expiry = fault["inject_at_boottime_ns"] \
                    if fault_type == "LEASE_EXPIRY" else \
                    fault["inject_at_boottime_ns"] + \
                    FIXTURE_RECOVERY_LIFETIME_NS
                document = _fixture_document(
                    fault_type, frozen.spec["campaign_id"], 1, expiry, True)
                self._write_fixture(path, document, replace=False)
        elif fault_type == "CLOCK_STEP":
            if CLOCK_FIXTURE_PATH.exists():
                status = self._clock_command("STATUS")
                _require(status["wall_clock_delta_ms"] == 0,
                         "P1_FAULT_INJECTOR_CLOCK_FIXTURE_RESIDUE")
            else:
                document = _seal({
                    "schema": CLOCK_RESULT_SCHEMA, "version": VERSION,
                    "operation": "RESET",
                    "fixture_path": str(CLOCK_FIXTURE_PATH),
                    "generation": 0, "wall_clock_delta_ms": 0,
                    "host_clock_mutated": False,
                })
                self._write_fixture(
                    CLOCK_FIXTURE_PATH, document, replace=False)
        self.assert_boundary()

    def _write_fixture(
        self, path: Path, document: dict[str, Any], *, replace: bool,
        expected_old: Mapping[str, Any] | None = None,
    ) -> None:
        payload = _canonical_bytes(document)
        parent_identity = _trusted_directory(
            path.parent, ROOT_UID, ROOT_GID,
            "P1_FAULT_INJECTOR_FIXTURE_PARENT_UNTRUSTED")
        temporary = f".{path.name}.hepta-p1-fault-injector.tmp"
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | \
            os.O_CLOEXEC
        descriptor: int | None = None
        parent_fd: int | None = None
        old_identity: tuple[int, ...] | None = None
        try:
            parent_fd = OBSERVER._open_directory(
                path.parent, "P1_FAULT_INJECTOR_FIXTURE_PARENT_UNTRUSTED")
            self._sweep_fixture_temp(parent_fd, temporary)
            if replace:
                _require(expected_old is not None,
                         "P1_FAULT_INJECTOR_FIXTURE_OLD_BINDING_REQUIRED")
                before = os.stat(
                    path.name, dir_fd=parent_fd, follow_symlinks=False)
                _require(stat.S_ISREG(before.st_mode),
                         "P1_FAULT_INJECTOR_FIXTURE_REPLACE_INVALID")
                old_identity = OBSERVER._file_identity(before)
                old_snapshot = _read_exact_snapshot(
                    path, ROOT_UID, ROOT_GID)
                _require(
                    old_snapshot.document == expected_old and
                    old_snapshot.body_sha256 == expected_old.get(
                        "body_sha256"),
                    "P1_FAULT_INJECTOR_FIXTURE_OLD_BODY_DRIFT")
            else:
                _require(expected_old is None,
                         "P1_FAULT_INJECTOR_FIXTURE_OLD_BINDING_INVALID")
            descriptor = os.open(
                temporary, flags, 0o600, dir_fd=parent_fd)
            os.fchmod(descriptor, 0o600)
            os.fchown(descriptor, ROOT_UID, ROOT_GID)
            written = 0
            while written < len(payload):
                count = os.write(descriptor, payload[written:])
                _require(count > 0, "P1_FAULT_INJECTOR_FIXTURE_WRITE_FAILED")
                written += count
            os.fsync(descriptor)
            if replace:
                current = os.stat(
                    path.name, dir_fd=parent_fd, follow_symlinks=False)
                _require(
                    OBSERVER._file_identity(current) == old_identity,
                    "P1_FAULT_INJECTOR_FIXTURE_REPLACE_DRIFT")
                os.rename(
                    temporary, path.name, src_dir_fd=parent_fd,
                    dst_dir_fd=parent_fd)
            else:
                OBSERVER._rename_noreplace(
                    parent_fd, temporary, path.name)
            os.fsync(parent_fd)
            _assert_directory(
                path.parent, parent_identity, ROOT_UID, ROOT_GID,
                "P1_FAULT_INJECTOR_FIXTURE_PARENT_DRIFT")
            snapshot = _read_exact_snapshot(path, ROOT_UID, ROOT_GID)
            _require(snapshot.document == document,
                     "P1_FAULT_INJECTOR_FIXTURE_REOPEN_FAILED")
        except (OSError, InjectorError, OBSERVER.ObserverError) as error:
            try:
                if parent_fd is not None:
                    os.unlink(temporary, dir_fd=parent_fd)
            except OSError:
                pass
            if isinstance(error, OBSERVER.ObserverError):
                raise InjectorError(
                    "P1_FAULT_INJECTOR_FIXTURE_PUBLISH_FAILED") from error
            raise
        finally:
            if descriptor is not None:
                os.close(descriptor)
            if parent_fd is not None:
                os.close(parent_fd)

    @staticmethod
    def _sweep_fixture_temp(parent_fd: int, temporary: str) -> None:
        try:
            metadata = os.stat(
                temporary, dir_fd=parent_fd, follow_symlinks=False)
        except FileNotFoundError:
            return
        _require(
            stat.S_ISREG(metadata.st_mode) and metadata.st_nlink == 1 and
            metadata.st_uid == ROOT_UID and metadata.st_gid == ROOT_GID and
            stat.S_IMODE(metadata.st_mode) == 0o600,
            "P1_FAULT_INJECTOR_FIXTURE_TEMP_INVALID")
        try:
            os.unlink(temporary, dir_fd=parent_fd)
            os.fsync(parent_fd)
        except OSError as error:
            raise InjectorError(
                "P1_FAULT_INJECTOR_FIXTURE_TEMP_CLEANUP_FAILED") from error

    def cleanup_residue(self, frozen: FrozenInputs) -> None:
        del frozen
        for fixture in (
            TOKEN_FIXTURE_PATH, LEASE_FIXTURE_PATH, CLOCK_FIXTURE_PATH,
        ):
            try:
                parent = OBSERVER._open_directory(
                    fixture.parent,
                    "P1_FAULT_INJECTOR_FIXTURE_PARENT_UNTRUSTED")
            except OBSERVER.ObserverError as error:
                raise InjectorError(
                    "P1_FAULT_INJECTOR_FIXTURE_PARENT_UNTRUSTED") from error
            try:
                self._sweep_fixture_temp(
                    parent,
                    f".{fixture.name}.hepta-p1-fault-injector.tmp")
            finally:
                os.close(parent)
        self.assert_boundary()

    def _clock_command(self, operation: str) -> dict[str, Any]:
        _require(operation in {"INJECT", "STATUS", "RESET"},
                 "P1_FAULT_INJECTOR_CLOCK_FIXTURE_INVALID")
        current: dict[str, Any] | None = None
        if CLOCK_FIXTURE_PATH.exists():
            snapshot = _read_exact_snapshot(
                CLOCK_FIXTURE_PATH, ROOT_UID, ROOT_GID)
            current = snapshot.document
            _exact(current, CLOCK_RESULT_FIELDS,
                   "P1_FAULT_INJECTOR_CLOCK_FIXTURE_INVALID")
            _validate_seal(
                current, "P1_FAULT_INJECTOR_CLOCK_FIXTURE_INVALID")
            _require(
                current.get("schema") == CLOCK_RESULT_SCHEMA and
                current.get("version") == VERSION and
                current.get("fixture_path") == str(CLOCK_FIXTURE_PATH) and
                current.get("host_clock_mutated") is False,
                "P1_FAULT_INJECTOR_CLOCK_FIXTURE_INVALID")
        _require(current is not None,
                 "P1_FAULT_INJECTOR_CLOCK_FIXTURE_NOT_PREPARED")
        generation = _integer(
            current.get("generation"),
            "P1_FAULT_INJECTOR_CLOCK_FIXTURE_INVALID")
        current_delta = current.get("wall_clock_delta_ms")
        _require(type(current_delta) is int,
                 "P1_FAULT_INJECTOR_CLOCK_FIXTURE_INVALID")
        if operation == "STATUS":
            document = _seal({
                **{key: value for key, value in current.items()
                   if key not in {"operation", "body_sha256"}},
                "operation": "STATUS",
            })
        else:
            if operation == "INJECT":
                _require(current_delta == 0,
                         "P1_FAULT_INJECTOR_CLOCK_FIXTURE_RESIDUE")
                delta = CLOCK_FIXTURE_DELTA_MS
            else:
                delta = 0
            document = _seal({
                "schema": CLOCK_RESULT_SCHEMA, "version": VERSION,
                "operation": operation,
                "fixture_path": str(CLOCK_FIXTURE_PATH),
                "generation": generation + 1,
                "wall_clock_delta_ms": delta,
                "host_clock_mutated": False,
            })
            self._write_fixture(
                CLOCK_FIXTURE_PATH, document, replace=True,
                expected_old=current)
        _exact(document, CLOCK_RESULT_FIELDS,
               "P1_FAULT_INJECTOR_CLOCK_FIXTURE_INVALID")
        _validate_seal(document, "P1_FAULT_INJECTOR_CLOCK_FIXTURE_INVALID")
        _require(
            document.get("schema") == CLOCK_RESULT_SCHEMA and
            document.get("version") == VERSION and
            document.get("operation") == operation and
            document.get("fixture_path") == str(CLOCK_FIXTURE_PATH) and
            type(document.get("generation")) is int and
            document["generation"] >= 0 and
            type(document.get("wall_clock_delta_ms")) is int and
            document.get("host_clock_mutated") is False,
            "P1_FAULT_INJECTOR_CLOCK_FIXTURE_INVALID")
        return document

    def inject(
        self, fault: Mapping[str, Any], pre: Mapping[str, Any],
        frozen: FrozenInputs,
    ) -> ActionState:
        frozen.assert_unchanged()
        self.assert_boundary()
        fault_type = fault["fault_type"]
        sample = self.clock()
        planned = fault["inject_at_boottime_ns"]
        _require(
            planned <= sample.boottime_ns <=
                planned + fault["maximum_injection_lateness_ns"],
            "P1_FAULT_INJECTOR_INJECTION_WINDOW_MISSED")
        evidence: dict[str, Any] = {
            "fault_id": fault["fault_id"], "fault_type": fault_type,
            "target_id": fault["target_id"], "boot_id": sample.boot_id,
            "actual_boottime_ns": sample.boottime_ns,
        }
        if fault_type in {"PROCESS_KILL", "EVIDENCE_WRITER_CRASH"}:
            process = pre["processes"][0]
            role = "OBSERVER_PROCESS" if fault_type == "PROCESS_KILL" \
                else "RECORDER_PROCESS"
            current = self._show(role)
            _require(
                (current["process"]["pid"],
                 current["process"]["starttime_ticks"]) ==
                (process["pid"], process["starttime_ticks"]),
                "P1_FAULT_INJECTOR_PROCESS_IDENTITY_DRIFT")
            try:
                os.kill(process["pid"], signal.SIGKILL)
            except OSError as error:
                raise InjectorError("P1_FAULT_INJECTOR_PROCESS_KILL_FAILED") \
                    from error
            evidence["operation"] = "SIGKILL_PINNED_MAIN_PID"
        elif fault_type == "SERVICE_RESTART":
            result = self._run((SYSTEMCTL, "restart", GATEWAY_UNIT))
            _require(result.returncode == 0 and not result.stderr,
                     "P1_FAULT_INJECTOR_GATEWAY_RESTART_FAILED")
            evidence["operation"] = "RESTART_PINNED_WATCH_GATEWAY"
        elif fault_type in {"TOKEN_LOSS", "LEASE_EXPIRY"}:
            path, fixture = self._fixture(fault_type)
            _require(
                fixture["generation"] == pre["fixture_generation"] and
                fixture["valid"] is True,
                "P1_FAULT_INJECTOR_FIXTURE_IDENTITY_DRIFT")
            invalid = _fixture_document(
                fault_type, frozen.spec["campaign_id"], fixture["generation"],
                fixture["expires_boottime_ns"], False)
            self._write_fixture(
                path, invalid, replace=True, expected_old=fixture)
            evidence["operation"] = "INVALIDATE_BOUNDED_FAULT_FIXTURE"
            evidence["fixture_generation"] = fixture["generation"]
        elif fault_type == "NETWORK_DENY_RELOAD":
            tightened = self._run((str(BROKER_HELPER), "--tighten-deny-all"))
            _require(tightened.returncode == 0 and not tightened.stderr,
                     "P1_FAULT_INJECTOR_DENY_RELOAD_FAILED")
            restarted = self._run((SYSTEMCTL, "restart", BROKER_UNIT))
            _require(restarted.returncode == 0 and not restarted.stderr,
                     "P1_FAULT_INJECTOR_DENY_RELOAD_FAILED")
            evidence["operation"] = "RELOAD_PINNED_DENY_ALL_POLICY"
        else:
            _require(fault_type == "CLOCK_STEP",
                     "P1_FAULT_INJECTOR_FAULT_TYPE_INVALID")
            result = self._clock_command("INJECT")
            _require(
                result["wall_clock_delta_ms"] == CLOCK_FIXTURE_DELTA_MS and
                100 <= abs(result["wall_clock_delta_ms"]) <= 60_000,
                "P1_FAULT_INJECTOR_CLOCK_FIXTURE_INVALID")
            evidence["operation"] = "INJECT_ISOLATED_CLOCK_DETECTOR_FIXTURE"
            evidence["fixture_result_body_sha256"] = result["body_sha256"]
        self.assert_boundary()
        return ActionState(
            actual_boottime_ns=sample.boottime_ns,
            evidence_sha256=_digest_bytes(_canonical_bytes(evidence)))

    def _transition_happened(
        self, fault: Mapping[str, Any], pre: Mapping[str, Any],
    ) -> bool:
        fault_type = fault["fault_type"]
        if fault_type in {"PROCESS_KILL", "EVIDENCE_WRITER_CRASH"}:
            role = "OBSERVER_PROCESS" if fault_type == "PROCESS_KILL" \
                else "RECORDER_PROCESS"
            current = self._show(role)["process"]
            before = pre["processes"][0]
            return (current["pid"], current["starttime_ticks"]) != (
                before["pid"], before["starttime_ticks"])
        if fault_type == "SERVICE_RESTART":
            return self._show("GATEWAY")["unit"]["invocation_id"] != \
                pre["systemd_units"][0]["invocation_id"]
        if fault_type == "NETWORK_DENY_RELOAD":
            return self._show("BROKER_POLICY")["unit"]["invocation_id"] != \
                pre["systemd_units"][0]["invocation_id"]
        if fault_type in {"TOKEN_LOSS", "LEASE_EXPIRY"}:
            _path_value, fixture = self._fixture(fault_type)
            return fixture["valid"] is False or \
                fixture["generation"] != pre["fixture_generation"]
        result = self._clock_command("STATUS")
        return result["wall_clock_delta_ms"] != 0

    def resume_action(
        self, fault: Mapping[str, Any], pre: Mapping[str, Any],
        frozen: FrozenInputs, intent_boottime_ns: int,
    ) -> ActionState:
        self.assert_boundary()
        if not self._transition_happened(fault, pre):
            return self.inject(fault, pre, frozen)
        evidence = {
            "fault_id": fault["fault_id"], "fault_type": fault["fault_type"],
            "operation": "RECOVERED_AFTER_ACTION_INTENT",
            "intent_boottime_ns": intent_boottime_ns,
        }
        return ActionState(
            actual_boottime_ns=intent_boottime_ns,
            evidence_sha256=_digest_bytes(_canonical_bytes(evidence)))

    def recover(
        self, fault: Mapping[str, Any], pre: Mapping[str, Any],
        action: ActionState, frozen: FrozenInputs,
    ) -> RecoveryState:
        deadline = action.actual_boottime_ns + fault["maximum_recovery_ns"]
        fault_type = fault["fault_type"]
        if fault_type in {"TOKEN_LOSS", "LEASE_EXPIRY"}:
            path, fixture = self._fixture(fault_type)
            _require(fixture["valid"] is False,
                     "P1_FAULT_INJECTOR_FIXTURE_ACTION_NOT_OBSERVED")
            sample = self.clock()
            recovered_fixture = _fixture_document(
                fault_type, frozen.spec["campaign_id"],
                pre["fixture_generation"] + 1,
                sample.boottime_ns + FIXTURE_RECOVERY_LIFETIME_NS, True)
            self._write_fixture(
                path, recovered_fixture, replace=True,
                expected_old=fixture)
        while True:
            sample = self.clock()
            _require(sample.boottime_ns <= deadline,
                     "P1_FAULT_INJECTOR_RECOVERY_TIMEOUT")
            try:
                if fault_type == "CLOCK_STEP":
                    status = self._clock_command("STATUS")
                    delta = status["wall_clock_delta_ms"]
                    _require(100 <= abs(delta) <= 60_000,
                             "P1_FAULT_INJECTOR_CLOCK_FIXTURE_INVALID")
                    post = self._identity(
                        fault, "POST", wall_clock_delta_ms=delta)
                else:
                    post = self._identity(fault, "POST")
                self.assert_boundary()
                return RecoveryState(
                    recovered_boottime_ns=sample.boottime_ns,
                    post_identity=post)
            except InjectorError:
                if self.clock().boottime_ns >= deadline:
                    raise
                self.assert_boundary()
                time.sleep(0.1)

    def cleanup(
        self, fault: Mapping[str, Any], recovery: RecoveryState,
        frozen: FrozenInputs,
    ) -> None:
        del recovery, frozen
        if fault["fault_type"] == "CLOCK_STEP":
            reset = self._clock_command("RESET")
            _require(reset["wall_clock_delta_ms"] == 0,
                     "P1_FAULT_INJECTOR_CLOCK_FIXTURE_RESIDUE")
            status = self._clock_command("STATUS")
            _require(status["wall_clock_delta_ms"] == 0,
                     "P1_FAULT_INJECTOR_CLOCK_FIXTURE_RESIDUE")
        self.assert_boundary()

    def _start_pinned_role(self, role: str) -> None:
        _require(role in UNIT_ROLES,
                 "P1_FAULT_INJECTOR_RECOVERY_TARGET_INVALID")
        unit = self.contracts[role]["unit"]
        arguments = (SYSTEMCTL, "start", unit)
        try:
            result = subprocess.run(
                arguments, check=False, stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                env={"PATH": "/usr/sbin:/usr/bin:/sbin:/bin", "LANG": "C",
                     "LC_ALL": "C", "PYTHONNOUSERSITE": "1"},
                cwd="/", timeout=30)
        except (OSError, subprocess.SubprocessError) as error:
            raise InjectorError(
                "P1_FAULT_INJECTOR_FAIL_CLOSE_START_FAILED") from error
        _require(
            result.returncode == 0 and not result.stderr and
            len(result.stdout) <= MAXIMUM_COMMAND_BYTES,
            "P1_FAULT_INJECTOR_FAIL_CLOSE_START_FAILED")

    def fail_close(
        self, fault: Mapping[str, Any], pre: Mapping[str, Any] | None,
        action: ActionState | None, frozen: FrozenInputs,
        intent_boottime_ns: int,
    ) -> RecoveryState | None:
        """Best-effort idempotent cleanup; it never performs a new fault."""

        fault_type = fault["fault_type"]
        post: dict[str, Any] | None = None
        recovered_ns = self.clock().boottime_ns
        if fault_type in {"TOKEN_LOSS", "LEASE_EXPIRY"} and pre is not None:
            path = TOKEN_FIXTURE_PATH if fault_type == "TOKEN_LOSS" \
                else LEASE_FIXTURE_PATH
            needs_recovery = True
            if path.exists():
                try:
                    _path_value, fixture = self._fixture(fault_type)
                    needs_recovery = not (
                        fixture["valid"] is True and
                        fixture["generation"] ==
                            pre["fixture_generation"] + 1)
                except InjectorError:
                    needs_recovery = True
            if needs_recovery:
                now = self.clock().boottime_ns
                document = _fixture_document(
                    fault_type, frozen.spec["campaign_id"],
                    pre["fixture_generation"] + 1,
                    now + FIXTURE_RECOVERY_LIFETIME_NS, True)
                if path.exists():
                    _path_value, old_fixture = self._fixture(fault_type)
                    self._write_fixture(
                        path, document, replace=True,
                        expected_old=old_fixture)
                else:
                    self._write_fixture(path, document, replace=False)
            post = self._identity(fault, "POST")
            recovered_ns = self.clock().boottime_ns
        elif fault_type == "CLOCK_STEP" and pre is not None:
            if CLOCK_FIXTURE_PATH.exists():
                status = self._clock_command("STATUS")
                if status["wall_clock_delta_ms"] != 0:
                    post = self._identity(
                        fault, "POST",
                        wall_clock_delta_ms=status["wall_clock_delta_ms"])
                    recovered_ns = self.clock().boottime_ns
                    reset = self._clock_command("RESET")
                    _require(reset["wall_clock_delta_ms"] == 0,
                             "P1_FAULT_INJECTOR_CLOCK_FIXTURE_RESIDUE")
        elif pre is not None:
            role = {
                "PROCESS_KILL": "OBSERVER_PROCESS",
                "EVIDENCE_WRITER_CRASH": "RECORDER_PROCESS",
                "SERVICE_RESTART": "GATEWAY",
                "NETWORK_DENY_RELOAD": "BROKER_POLICY",
            }[fault_type]
            if fault_type == "NETWORK_DENY_RELOAD":
                tightened = self._run(
                    (str(BROKER_HELPER), "--tighten-deny-all"))
                _require(tightened.returncode == 0 and not tightened.stderr,
                         "P1_FAULT_INJECTOR_FAIL_CLOSE_DENY_FAILED")
            try:
                happened = self._transition_happened(fault, pre)
            except InjectorError:
                self._start_pinned_role(role)
                happened = self._transition_happened(fault, pre)
            if happened:
                post = self._identity(fault, "POST")
                recovered_ns = self.clock().boottime_ns
        self.assert_boundary()
        if post is None:
            return None
        actual = action.actual_boottime_ns if action is not None \
            else intent_boottime_ns
        evidence = action.evidence_sha256 if action is not None else \
            _digest_bytes(_canonical_bytes({
                "fault_id": fault["fault_id"],
                "operation": "FAIL_CLOSE_FROM_ACTION_INTENT",
                "intent_boottime_ns": intent_boottime_ns,
            }))
        candidate_action = ActionState(actual, evidence)
        candidate = RecoveryState(recovered_ns, post)
        _validate_transition(fault, pre, candidate_action, candidate)
        return candidate

    def after_journal(self, stage: str) -> None:
        del stage


def _journal_document(
    frozen: FrozenInputs, sequence: int, stage: str,
    fault: Mapping[str, Any], sample: OBSERVER.ClockSample,
    previous: str | None, *, pre: Mapping[str, Any] | None = None,
    actual_ns: int | None = None, action_sha: str | None = None,
    recovered_ns: int | None = None, post: Mapping[str, Any] | None = None,
    output_path: str | None = None, output_file_sha: str | None = None,
    output_body_sha: str | None = None, cleanup_complete: bool = False,
) -> dict[str, Any]:
    return _seal({
        "schema": JOURNAL_SCHEMA, "version": VERSION,
        "campaign_id": frozen.spec["campaign_id"],
        "fault_plan_body_sha256": frozen.plan_snapshot.body_sha256,
        "pins_body_sha256": frozen.pins_snapshot.body_sha256,
        "sequence": sequence, "stage": stage,
        "recorded_at_ms": sample.wall_ms,
        "recorded_boottime_ns": sample.boottime_ns,
        "boot_id": sample.boot_id, "fault_id": fault["fault_id"],
        "fault_type": fault["fault_type"], "target_id": fault["target_id"],
        "planned_injection_boottime_ns": fault["inject_at_boottime_ns"],
        "pre_identity": None if pre is None else dict(pre),
        "actual_injection_boottime_ns": actual_ns,
        "action_evidence_sha256": action_sha,
        "recovered_boottime_ns": recovered_ns,
        "post_identity": None if post is None else dict(post),
        "output_path": output_path, "output_file_sha256": output_file_sha,
        "output_body_sha256": output_body_sha,
        "previous_body_sha256": previous, "boundary_safe": True,
        "cleanup_complete": cleanup_complete, **_boundary(),
    })


def validate_journal(
    document: dict[str, Any], frozen: FrozenInputs,
    previous: str | None, sequence: int,
) -> dict[str, Any]:
    reason = "P1_FAULT_INJECTOR_JOURNAL_INVALID"
    _exact(document, JOURNAL_FIELDS, reason)
    _validate_seal(document, reason)
    _reject_authority(document, reason)
    _require(
        document.get("schema") == JOURNAL_SCHEMA and
        document.get("version") == VERSION and
        document.get("campaign_id") == frozen.spec["campaign_id"] and
        document.get("fault_plan_body_sha256") ==
            frozen.plan_snapshot.body_sha256 and
        document.get("pins_body_sha256") == frozen.pins_snapshot.body_sha256 and
        document.get("sequence") == sequence and
        document.get("previous_body_sha256") == previous and
        document.get("stage") in ALLOWED_STAGES and
        document.get("boot_id") == frozen.pins["boot_id"] and
        document.get("boundary_safe") is True and
        type(document.get("cleanup_complete")) is bool, reason)
    fault = next((item for item in frozen.faults
                  if item["fault_id"] == document.get("fault_id")), None)
    _require(
        fault is not None and document.get("fault_type") == fault["fault_type"]
        and document.get("target_id") == fault["target_id"] and
        document.get("planned_injection_boottime_ns") ==
            fault["inject_at_boottime_ns"], reason)
    _integer(document.get("recorded_at_ms"), reason)
    _integer(document.get("recorded_boottime_ns"), reason)
    for field in (
        "actual_injection_boottime_ns", "recovered_boottime_ns",
    ):
        value = document.get(field)
        _require(value is None or (type(value) is int and value >= 0), reason)
    for field in (
        "action_evidence_sha256", "output_file_sha256", "output_body_sha256",
    ):
        value = document.get(field)
        _require(value is None or (isinstance(value, str) and
                 OBSERVER.DIGEST.fullmatch(value) is not None), reason)
    output = document.get("output_path")
    _require(output is None or str(_path(output, reason)) == output, reason)
    return document


class Journal:
    def __init__(self, frozen: FrozenInputs):
        self.frozen = frozen
        self.path = Path(frozen.pins["journal_directory"])
        self.uid = frozen.owner_uid
        self.gid = frozen.owner_gid
        self.identity = _trusted_directory(
            self.path, self.uid, self.gid,
            "P1_FAULT_INJECTOR_JOURNAL_PARENT_UNTRUSTED")
        self.entries: list[dict[str, Any]] = []
        self._load()

    def _load(self) -> None:
        _assert_directory(
            self.path, self.identity, self.uid, self.gid,
            "P1_FAULT_INJECTOR_JOURNAL_PARENT_DRIFT")
        names = sorted(
            item.name for item in self.path.iterdir()
            if re.fullmatch(r"[0-9]{8}\.json", item.name))
        unknown = [
            item.name for item in self.path.iterdir()
            if item.name != ".injector.lock" and
            not re.fullmatch(r"[0-9]{8}\.json", item.name)
        ]
        _require(not unknown and len(names) <= MAXIMUM_JOURNAL_ENTRIES,
                 "P1_FAULT_INJECTOR_JOURNAL_INVENTORY_INVALID")
        previous: str | None = None
        for index, name in enumerate(names, 1):
            _require(name == f"{index:08d}.json",
                     "P1_FAULT_INJECTOR_JOURNAL_SEQUENCE_GAP")
            snapshot = _read_exact_snapshot(
                self.path / name, self.uid, self.gid)
            entry = validate_journal(
                snapshot.document, self.frozen, previous, index)
            self.entries.append(entry)
            previous = entry["body_sha256"]
        _assert_directory(
            self.path, self.identity, self.uid, self.gid,
            "P1_FAULT_INJECTOR_JOURNAL_PARENT_DRIFT")

    def record(
        self, executor: Executor, stage: str, fault: Mapping[str, Any],
        **values: Any,
    ) -> dict[str, Any]:
        _require(stage in ALLOWED_STAGES,
                 "P1_FAULT_INJECTOR_JOURNAL_STAGE_INVALID")
        executor.assert_boundary()
        self.frozen.assert_unchanged()
        _assert_directory(
            self.path, self.identity, self.uid, self.gid,
            "P1_FAULT_INJECTOR_JOURNAL_PARENT_DRIFT")
        sample = executor.clock()
        previous = self.entries[-1]["body_sha256"] if self.entries else None
        document = _journal_document(
            self.frozen, len(self.entries) + 1, stage, fault, sample,
            previous, **values)
        output = self.path / f"{len(self.entries) + 1:08d}.json"
        try:
            OBSERVER.publish_receipt(
                document, output, expected_uid=self.uid,
                expected_gid=self.gid)
        except OBSERVER.ObserverError as error:
            raise InjectorError("P1_FAULT_INJECTOR_JOURNAL_PUBLISH_FAILED") \
                from error
        self.entries.append(document)
        executor.assert_boundary()
        executor.after_journal(stage)
        return document

    def latest(self, fault_id: str) -> dict[str, Any] | None:
        matches = [entry for entry in self.entries
                   if entry["fault_id"] == fault_id]
        return matches[-1] if matches else None

    def by_stage(self, fault_id: str, stage: str) -> dict[str, Any] | None:
        matches = [entry for entry in self.entries
                   if entry["fault_id"] == fault_id and
                   entry["stage"] == stage]
        return matches[-1] if matches else None


class CampaignLock:
    def __init__(self, journal_path: Path, uid: int, gid: int):
        self.path = journal_path / ".injector.lock"
        self.uid = uid
        self.gid = gid
        self.descriptor: int | None = None

    def __enter__(self) -> "CampaignLock":
        flags = os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW | os.O_CLOEXEC
        try:
            self.descriptor = os.open(self.path, flags, 0o600)
            os.fchmod(self.descriptor, 0o600)
            if os.geteuid() == ROOT_UID:
                os.fchown(self.descriptor, self.uid, self.gid)
            metadata = os.fstat(self.descriptor)
            _require(
                stat.S_ISREG(metadata.st_mode) and metadata.st_nlink == 1 and
                metadata.st_uid == self.uid and metadata.st_gid == self.gid and
                stat.S_IMODE(metadata.st_mode) == 0o600,
                "P1_FAULT_INJECTOR_LOCK_INVALID")
            fcntl.flock(
                self.descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except (OSError, InjectorError) as error:
            if self.descriptor is not None:
                os.close(self.descriptor)
                self.descriptor = None
            if isinstance(error, InjectorError):
                raise
            raise InjectorError("P1_FAULT_INJECTOR_LOCK_BUSY") from error
        return self

    def __exit__(self, *_args: Any) -> None:
        if self.descriptor is not None:
            try:
                fcntl.flock(self.descriptor, fcntl.LOCK_UN)
            finally:
                os.close(self.descriptor)
                self.descriptor = None


def _receipt(
    frozen: FrozenInputs, fault: Mapping[str, Any], pre: Mapping[str, Any],
    action: ActionState, recovery: RecoveryState, *, status: str,
    issued_at_ms: int, journal_predecessor: Mapping[str, Any],
) -> dict[str, Any]:
    _validate_transition(fault, pre, action, recovery)
    expires = min(
        issued_at_ms + RECEIPT_LIFETIME_MS, frozen.pins["expires_at_ms"])
    _require(expires > issued_at_ms,
             "P1_FAULT_INJECTOR_RECEIPT_WINDOW_EXPIRED")
    document = _seal({
        "schema": INJECTION_SCHEMA, "version": VERSION, "status": status,
        "issued_at_ms": issued_at_ms, "expires_at_ms": expires,
        "campaign_id": frozen.spec["campaign_id"],
        "source_manifest_sha256": frozen.spec["source_manifest_sha256"],
        "policy_sha256": frozen.spec["policy_sha256"],
        "strategy_sha256": frozen.spec["strategy_sha256"],
        "fault_id": fault["fault_id"], "fault_type": fault["fault_type"],
        "target_id": fault["target_id"], "clock_id": "CLOCK_BOOTTIME",
        "boot_id": frozen.pins["boot_id"],
        "planned_injection_boottime_ns": fault["inject_at_boottime_ns"],
        "actual_injection_boottime_ns": action.actual_boottime_ns,
        "recovered_boottime_ns": recovery.recovered_boottime_ns,
        "maximum_recovery_ns": fault["maximum_recovery_ns"],
        "injector_id": frozen.pins["injector_id"],
        "injector_uid": ROOT_UID, "injector_gid": ROOT_GID,
        "injection_scope": "P1_DECLARED_FAULT_ONLY",
        "action_receipt_sha256": action.evidence_sha256,
        "pre_identity": dict(pre),
        "post_identity": dict(recovery.post_identity),
        "injection_performed": True, "recovery_complete": True,
        "cleanup_complete": True, "authority_failure": False,
        "audit_failure": False, "cleanup_failure": False,
        "producer": {
            "path": str(INJECTOR_HELPER),
            "file_sha256": frozen.pins["injector_file_sha256"],
        },
        "production_mode": (
            INJECTOR_PRODUCTION_MODE if status == "COMPLETE"
            else "INJECTED_REHEARSAL"),
        "pins_reference": {
            "path": str(frozen.pins_snapshot.path),
            "file_sha256": frozen.pins_snapshot.file_sha256,
            "body_sha256": frozen.pins_snapshot.body_sha256,
        },
        "journal_predecessor_sequence": journal_predecessor["sequence"],
        "journal_predecessor_body_sha256":
            journal_predecessor["body_sha256"],
        **_boundary(),
    })
    _exact(document, INJECTION_FIELDS,
           "P1_FAULT_INJECTOR_RECEIPT_FIELDS_INVALID")
    _validate_seal(document, "P1_FAULT_INJECTOR_RECEIPT_INVALID")
    _reject_authority(document, "P1_FAULT_INJECTOR_RECEIPT_AUTHORITY_INVALID")
    return document


def _validate_transition(
    fault: Mapping[str, Any], pre: Mapping[str, Any], action: ActionState,
    recovery: RecoveryState,
) -> None:
    reason = "P1_FAULT_INJECTOR_TRANSITION_INVALID"
    try:
        before = OBSERVER.validate_fault_identity(
            pre, "PRE", fault["target_id"], fault["fault_type"], reason)
        after = OBSERVER.validate_fault_identity(
            recovery.post_identity, "POST", fault["target_id"],
            fault["fault_type"], reason)
        OBSERVER.IndependentObserver._validate_fault_transition(
            None, fault["fault_type"], before, after,
            actual_ns=action.actual_boottime_ns,
            recovered_ns=recovery.recovered_boottime_ns,
            recovery_complete=True, reason=reason)
    except OBSERVER.ObserverError as error:
        raise InjectorError(reason) from error
    _require(
        before["boot_id"] == after["boot_id"] and
        before["observed_boottime_ns"] <= action.actual_boottime_ns <=
            recovery.recovered_boottime_ns <=
            after["observed_boottime_ns"] and
        after["residue_count"] == 0 and
        all(
            identity["broker_deny_all"] is not None and
            identity["broker_deny_all"]["deny_all"] is True and
            identity["broker_deny_all"]["authorized_connector_count"] == 0 and
            identity["broker_deny_all"]["authorized_uids"] == [] and
            identity["broker_deny_all"]["protected_port_count"] > 0
            for identity in (before, after)),
        reason)


def _output_path(frozen: FrozenInputs, index: int, fault: Mapping[str, Any]) \
        -> Path:
    return Path(frozen.pins["output_directory"]) / \
        f"{index:02d}-{fault['fault_id']}.json"


def _stage_path(output: Path, body_sha256: str) -> Path:
    _digest(body_sha256, "P1_FAULT_INJECTOR_STAGE_DIGEST_INVALID")
    return output.parent / (
        f".{output.name}.{body_sha256.removeprefix('sha256:')}.staged")


def _require_output_absent(output: Path) -> None:
    parent = OBSERVER._open_directory(
        output.parent, "P1_FAULT_INJECTOR_OUTPUT_PARENT_UNTRUSTED")
    try:
        try:
            os.stat(output.name, dir_fd=parent, follow_symlinks=False)
        except FileNotFoundError:
            return
        raise InjectorError("P1_FAULT_INJECTOR_OUTPUT_ALREADY_EXISTS")
    except OSError as error:
        raise InjectorError("P1_FAULT_INJECTOR_OUTPUT_CHECK_FAILED") \
            from error
    finally:
        os.close(parent)


def _stage_receipt(
    document: dict[str, Any], output: Path, uid: int, gid: int,
) -> OBSERVER.Snapshot:
    staged = _stage_path(output, document["body_sha256"])
    if staged.exists():
        snapshot = _read_exact_snapshot(staged, uid, gid)
        _require(snapshot.document == document,
                 "P1_FAULT_INJECTOR_STAGED_OUTPUT_DRIFT")
        return snapshot
    try:
        OBSERVER.publish_receipt(
            document, staged, expected_uid=uid, expected_gid=gid)
    except OBSERVER.ObserverError as error:
        raise InjectorError("P1_FAULT_INJECTOR_OUTPUT_STAGE_FAILED") \
            from error
    return _read_exact_snapshot(staged, uid, gid)


def _commit_staged(
    document: dict[str, Any], output: Path, uid: int, gid: int,
) -> OBSERVER.Snapshot:
    staged = _stage_path(output, document["body_sha256"])
    if output.exists():
        snapshot = _read_exact_snapshot(output, uid, gid)
        _require(snapshot.document == document,
                 "P1_FAULT_INJECTOR_OUTPUT_REPLAY_DRIFT")
        _require(not staged.exists(),
                 "P1_FAULT_INJECTOR_STAGED_OUTPUT_RESIDUE")
        return snapshot
    staged_snapshot = _read_exact_snapshot(staged, uid, gid)
    _require(staged_snapshot.document == document,
             "P1_FAULT_INJECTOR_STAGED_OUTPUT_DRIFT")
    parent_identity = _trusted_directory(
        output.parent, uid, gid,
        "P1_FAULT_INJECTOR_OUTPUT_PARENT_UNTRUSTED")
    try:
        parent = OBSERVER._open_directory(
            output.parent, "P1_FAULT_INJECTOR_OUTPUT_PARENT_UNTRUSTED")
        try:
            OBSERVER._rename_noreplace(parent, staged.name, output.name)
            os.fsync(parent)
        finally:
            os.close(parent)
    except OBSERVER.ObserverError as error:
        raise InjectorError("P1_FAULT_INJECTOR_OUTPUT_COMMIT_FAILED") \
            from error
    _assert_directory(
        output.parent, parent_identity, uid, gid,
        "P1_FAULT_INJECTOR_OUTPUT_PARENT_DRIFT")
    committed = _read_exact_snapshot(output, uid, gid)
    _require(committed.document == document and not staged.exists(),
             "P1_FAULT_INJECTOR_OUTPUT_COMMIT_REOPEN_FAILED")
    return committed


def _discard_staged(
    frozen: FrozenInputs, fault: Mapping[str, Any], journal: Journal,
) -> None:
    publish = journal.by_stage(fault["fault_id"], "PUBLISH_INTENT")
    if publish is None or publish["output_body_sha256"] is None:
        return
    output = Path(publish["output_path"])
    _require(not output.exists(),
             "P1_FAULT_INJECTOR_UNCOMMITTED_COMPLETE_OUTPUT")
    staged = _stage_path(output, publish["output_body_sha256"])
    if not staged.exists():
        return
    snapshot = _read_exact_snapshot(
        staged, frozen.owner_uid, frozen.owner_gid)
    _require(snapshot.body_sha256 == publish["output_body_sha256"],
             "P1_FAULT_INJECTOR_STAGED_OUTPUT_DRIFT")
    parent = OBSERVER._open_directory(
        staged.parent, "P1_FAULT_INJECTOR_OUTPUT_PARENT_UNTRUSTED")
    try:
        before = os.stat(staged.name, dir_fd=parent, follow_symlinks=False)
        _require(OBSERVER._file_identity(before) ==
                 OBSERVER._file_identity(snapshot.metadata),
                 "P1_FAULT_INJECTOR_STAGED_OUTPUT_DRIFT")
        os.unlink(staged.name, dir_fd=parent)
        os.fsync(parent)
    except OSError as error:
        raise InjectorError("P1_FAULT_INJECTOR_STAGE_CLEANUP_FAILED") \
            from error
    finally:
        os.close(parent)


def _sweep_publish_temps(path: Path, uid: int, gid: int) -> None:
    identity = _trusted_directory(
        path, uid, gid, "P1_FAULT_INJECTOR_PUBLISH_PARENT_UNTRUSTED")
    parent = OBSERVER._open_directory(
        path, "P1_FAULT_INJECTOR_PUBLISH_PARENT_UNTRUSTED")
    try:
        for name in os.listdir(parent):
            if re.fullmatch(r"\..+\.observer-[0-9a-f]{32}\.tmp", name) is None:
                continue
            metadata = os.stat(name, dir_fd=parent, follow_symlinks=False)
            _require(
                stat.S_ISREG(metadata.st_mode) and metadata.st_nlink == 1 and
                metadata.st_uid == uid and metadata.st_gid == gid and
                stat.S_IMODE(metadata.st_mode) == 0o600,
                "P1_FAULT_INJECTOR_PUBLISH_TEMP_INVALID")
            os.unlink(name, dir_fd=parent)
        os.fsync(parent)
    except OSError as error:
        raise InjectorError("P1_FAULT_INJECTOR_PUBLISH_TEMP_CLEANUP_FAILED") \
            from error
    finally:
        os.close(parent)
    _assert_directory(
        path, identity, uid, gid,
        "P1_FAULT_INJECTOR_PUBLISH_PARENT_DRIFT")


def _restore_action(entry: Mapping[str, Any]) -> ActionState:
    return ActionState(
        actual_boottime_ns=entry["actual_injection_boottime_ns"],
        evidence_sha256=entry["action_evidence_sha256"])


def _restore_recovery(entry: Mapping[str, Any]) -> RecoveryState:
    return RecoveryState(
        recovered_boottime_ns=entry["recovered_boottime_ns"],
        post_identity=dict(entry["post_identity"]))


def _require_pins_live(
    frozen: FrozenInputs, executor: Executor,
    fault: Mapping[str, Any] | None = None,
) -> OBSERVER.ClockSample:
    sample = executor.clock()
    _require(
        sample.boot_id == frozen.pins["boot_id"] and
        sample.wall_ms < frozen.pins["expires_at_ms"],
        "P1_FAULT_INJECTOR_PINS_EXPIRED")
    if fault is not None:
        end_ns = (
            fault["inject_at_boottime_ns"] +
            fault["maximum_injection_lateness_ns"] +
            fault["maximum_recovery_ns"])
        remaining_ms = max(0, end_ns - sample.boottime_ns + 999_999) // \
            1_000_000
        _require(
            sample.wall_ms + remaining_ms < frozen.pins["expires_at_ms"],
            "P1_FAULT_INJECTOR_PIN_LIFETIME_TOO_SHORT")
    return sample


def _record_failed_closed(
    journal: Journal, executor: Executor, frozen: FrozenInputs,
    fault: Mapping[str, Any], pre: Mapping[str, Any] | None,
    action: ActionState | None, recovery: RecoveryState | None,
) -> None:
    latest = journal.latest(fault["fault_id"])
    if latest is not None and latest["stage"] == "FAILED_CLOSED":
        return
    journal.record(
        executor, "FAILED_CLOSED", fault, pre=pre,
        actual_ns=(action.actual_boottime_ns if action is not None else None),
        action_sha=(action.evidence_sha256 if action is not None else None),
        recovered_ns=(recovery.recovered_boottime_ns
                      if recovery is not None else None),
        post=(recovery.post_identity if recovery is not None else None),
        cleanup_complete=True)


def reconcile_failure(frozen: FrozenInputs, executor: Executor) -> None:
    """Clean any nonterminal prior state and irreversibly fail it closed."""

    journal_dir = Path(frozen.pins["journal_directory"])
    output_dir = Path(frozen.pins["output_directory"])
    _trusted_directory(
        journal_dir, frozen.owner_uid, frozen.owner_gid,
        "P1_FAULT_INJECTOR_JOURNAL_PARENT_UNTRUSTED")
    _trusted_directory(
        output_dir, frozen.owner_uid, frozen.owner_gid,
        "P1_FAULT_INJECTOR_OUTPUT_PARENT_UNTRUSTED")
    with CampaignLock(journal_dir, frozen.owner_uid, frozen.owner_gid):
        _sweep_publish_temps(
            journal_dir, frozen.owner_uid, frozen.owner_gid)
        _sweep_publish_temps(
            output_dir, frozen.owner_uid, frozen.owner_gid)
        journal = Journal(frozen)
        if not journal.entries:
            executor.assert_boundary()
            return
        latest = journal.entries[-1]
        if latest["stage"] in {"PUBLISHED", "FAILED_CLOSED"}:
            executor.assert_boundary()
            return
        fault = next(item for item in frozen.faults
                     if item["fault_id"] == latest["fault_id"])
        intent = journal.by_stage(fault["fault_id"], "ACTION_INTENT")
        if intent is None:
            _discard_staged(frozen, fault, journal)
            executor.cleanup_residue(frozen)
            executor.assert_boundary()
            _record_failed_closed(
                journal, executor, frozen, fault, None, None, None)
            return
        pre = dict(intent["pre_identity"])
        action_entry = journal.by_stage(fault["fault_id"], "ACTION_RESULT")
        action = _restore_action(action_entry) \
            if action_entry is not None else None
        recovery_entry = journal.by_stage(
            fault["fault_id"], "RECOVERY_RESULT")
        if recovery_entry is not None:
            recovery = _restore_recovery(recovery_entry)
            if journal.by_stage(
                    fault["fault_id"], "CLEANUP_RESULT") is None:
                executor.cleanup(fault, recovery, frozen)
        else:
            recovery = executor.fail_close(
                fault, pre, action, frozen, intent["recorded_boottime_ns"])
            if recovery is not None:
                executor.cleanup(fault, recovery, frozen)
        _discard_staged(frozen, fault, journal)
        executor.cleanup_residue(frozen)
        executor.assert_boundary()
        _record_failed_closed(
            journal, executor, frozen, fault, pre, action, recovery)


def run_campaign(
    frozen: FrozenInputs, executor: Executor, *, run_requested: bool,
    effective_uid: int, effective_gid: int,
) -> list[dict[str, Any]]:
    certifying = (
        run_requested and effective_uid == ROOT_UID and
        effective_gid == ROOT_GID and type(executor) is ProductionExecutor)
    if run_requested and not certifying:
        raise InjectorError("P1_FAULT_INJECTOR_PRODUCTION_AUTHORITY_REQUIRED")
    status = "COMPLETE" if certifying else "REHEARSAL_ONLY"
    output_dir = Path(frozen.pins["output_directory"])
    output_identity = _trusted_directory(
        output_dir, frozen.owner_uid, frozen.owner_gid,
        "P1_FAULT_INJECTOR_OUTPUT_PARENT_UNTRUSTED")
    journal_dir = Path(frozen.pins["journal_directory"])
    for planned_fault in frozen.faults:
        _require_pins_live(frozen, executor, planned_fault)
    with CampaignLock(journal_dir, frozen.owner_uid, frozen.owner_gid):
        journal = Journal(frozen)
        initial_entry_count = len(journal.entries)
        all_published = bool(journal.entries) and all(
            journal.latest(item["fault_id"]) is not None and
            journal.latest(item["fault_id"])["stage"] == "PUBLISHED"
            for item in frozen.faults
        )
        needs_commit_recovery = all_published and any(
            not _output_path(frozen, index, fault).exists()
            for index, fault in enumerate(frozen.faults, 1))
        if all_published and not needs_commit_recovery:
            raise InjectorError("P1_FAULT_INJECTOR_REPLAY")
        receipts: list[dict[str, Any]] = []
        for index, fault in enumerate(frozen.faults, 1):
            latest = journal.latest(fault["fault_id"])
            if latest is not None and latest["stage"] == "FAILED_CLOSED":
                raise InjectorError("P1_FAULT_INJECTOR_PREVIOUSLY_FAILED_CLOSED")
            if latest is not None and latest["stage"] == "PUBLISHED":
                output = _output_path(frozen, index, fault)
                published = latest
                staged = _stage_path(
                    output, published["output_body_sha256"])
                source = output if output.exists() else staged
                snapshot = _read_exact_snapshot(
                    source, frozen.owner_uid, frozen.owner_gid)
                _require(
                    snapshot.file_sha256 == published["output_file_sha256"] and
                    snapshot.body_sha256 == published["output_body_sha256"],
                    "P1_FAULT_INJECTOR_PUBLISHED_OUTPUT_DRIFT")
                snapshot = _commit_staged(
                    snapshot.document, output, frozen.owner_uid,
                    frozen.owner_gid)
                receipts.append(snapshot.document)
                continue
            if (latest is not None and
                    latest["sequence"] <= initial_entry_count):
                # No prior-invocation nonterminal transaction may be promoted.
                # Reconcile under the same lock to a terminal FAILED_CLOSED.
                intent = journal.by_stage(fault["fault_id"], "ACTION_INTENT")
                pre = dict(intent["pre_identity"]) if intent is not None \
                    else None
                action_entry = journal.by_stage(
                    fault["fault_id"], "ACTION_RESULT")
                action = _restore_action(action_entry) \
                    if action_entry is not None else None
                recovery_entry = journal.by_stage(
                    fault["fault_id"], "RECOVERY_RESULT")
                if recovery_entry is not None:
                    recovery = _restore_recovery(recovery_entry)
                    if journal.by_stage(
                            fault["fault_id"], "CLEANUP_RESULT") is None:
                        executor.cleanup(fault, recovery, frozen)
                elif intent is not None:
                    recovery = executor.fail_close(
                        fault, pre, action, frozen,
                        intent["recorded_boottime_ns"])
                    if recovery is not None:
                        executor.cleanup(fault, recovery, frozen)
                else:
                    recovery = None
                _discard_staged(frozen, fault, journal)
                executor.cleanup_residue(frozen)
                executor.assert_boundary()
                _record_failed_closed(
                    journal, executor, frozen, fault, pre, action, recovery)
                raise InjectorError(
                    "P1_FAULT_INJECTOR_PRIOR_NONTERMINAL_FAILED_CLOSED")
            prepare_result = journal.by_stage(
                fault["fault_id"], "PREPARE_RESULT")
            if prepare_result is None:
                _require_pins_live(frozen, executor, fault)
                if journal.by_stage(
                        fault["fault_id"], "PREPARE_INTENT") is None:
                    journal.record(executor, "PREPARE_INTENT", fault)
                executor.prepare(fault, frozen)
                journal.record(executor, "PREPARE_RESULT", fault)
            intent = journal.by_stage(fault["fault_id"], "ACTION_INTENT")
            if intent is None:
                executor.wait_until(fault["inject_at_boottime_ns"])
                now = executor.clock()
                _require(
                    now.boot_id == frozen.pins["boot_id"] and
                    fault["inject_at_boottime_ns"] <= now.boottime_ns <=
                        fault["inject_at_boottime_ns"] +
                        fault["maximum_injection_lateness_ns"],
                    "P1_FAULT_INJECTOR_INJECTION_WINDOW_MISSED")
                executor.assert_boundary()
                _require_pins_live(frozen, executor, fault)
                frozen.assert_unchanged()
                pre = executor.pre_identity(fault, frozen)
                intent = journal.record(
                    executor, "ACTION_INTENT", fault, pre=pre)
            else:
                pre = dict(intent["pre_identity"])
            action_entry = journal.by_stage(fault["fault_id"], "ACTION_RESULT")
            if action_entry is None:
                _require_pins_live(frozen, executor, fault)
                action = executor.resume_action(
                    fault, pre, frozen, intent["recorded_boottime_ns"])
                _require(
                    fault["inject_at_boottime_ns"] <=
                        action.actual_boottime_ns <=
                        fault["inject_at_boottime_ns"] +
                        fault["maximum_injection_lateness_ns"],
                    "P1_FAULT_INJECTOR_INJECTION_WINDOW_MISSED")
                action_entry = journal.record(
                    executor, "ACTION_RESULT", fault, pre=pre,
                    actual_ns=action.actual_boottime_ns,
                    action_sha=action.evidence_sha256)
            else:
                action = _restore_action(action_entry)
            recovery_entry = journal.by_stage(
                fault["fault_id"], "RECOVERY_RESULT")
            if recovery_entry is None:
                recovery_intent = journal.by_stage(
                    fault["fault_id"], "RECOVERY_INTENT")
                if recovery_intent is None:
                    journal.record(
                        executor, "RECOVERY_INTENT", fault, pre=pre,
                        actual_ns=action.actual_boottime_ns,
                        action_sha=action.evidence_sha256)
                try:
                    _require_pins_live(frozen, executor)
                except InjectorError:
                    recovery = executor.fail_close(
                        fault, pre, action, frozen,
                        intent["recorded_boottime_ns"])
                    if recovery is not None:
                        executor.cleanup(fault, recovery, frozen)
                    _record_failed_closed(
                        journal, executor, frozen, fault, pre, action,
                        recovery)
                    raise
                if (recovery_intent is not None and
                        recovery_intent["sequence"] <= initial_entry_count):
                    recovery = executor.fail_close(
                        fault, pre, action, frozen,
                        intent["recorded_boottime_ns"])
                    if recovery is None:
                        _record_failed_closed(
                            journal, executor, frozen, fault, pre, action,
                            None)
                        raise InjectorError(
                            "P1_FAULT_INJECTOR_RECOVERY_EVIDENCE_LOST")
                else:
                    recovery = executor.recover(fault, pre, action, frozen)
                _require(
                    action.actual_boottime_ns <=
                        recovery.recovered_boottime_ns <=
                        action.actual_boottime_ns +
                        fault["maximum_recovery_ns"],
                    "P1_FAULT_INJECTOR_RECOVERY_TIMEOUT")
                recovery_entry = journal.record(
                    executor, "RECOVERY_RESULT", fault, pre=pre,
                    actual_ns=action.actual_boottime_ns,
                    action_sha=action.evidence_sha256,
                    recovered_ns=recovery.recovered_boottime_ns,
                    post=recovery.post_identity, cleanup_complete=False)
            else:
                recovery = _restore_recovery(recovery_entry)
            cleanup_result = journal.by_stage(
                fault["fault_id"], "CLEANUP_RESULT")
            if cleanup_result is None:
                if journal.by_stage(
                        fault["fault_id"], "CLEANUP_INTENT") is None:
                    journal.record(
                        executor, "CLEANUP_INTENT", fault, pre=pre,
                        actual_ns=action.actual_boottime_ns,
                        action_sha=action.evidence_sha256,
                        recovered_ns=recovery.recovered_boottime_ns,
                        post=recovery.post_identity,
                        cleanup_complete=False)
                executor.cleanup(fault, recovery, frozen)
                journal.record(
                    executor, "CLEANUP_RESULT", fault, pre=pre,
                    actual_ns=action.actual_boottime_ns,
                    action_sha=action.evidence_sha256,
                    recovered_ns=recovery.recovered_boottime_ns,
                    post=recovery.post_identity, cleanup_complete=True)
            output = _output_path(frozen, index, fault)
            try:
                _require_pins_live(frozen, executor)
            except InjectorError:
                _record_failed_closed(
                    journal, executor, frozen, fault, pre, action, recovery)
                raise
            _require(output.parent == output_dir,
                     "P1_FAULT_INJECTOR_OUTPUT_PATH_INVALID")
            _assert_directory(
                output_dir, output_identity, frozen.owner_uid,
                frozen.owner_gid, "P1_FAULT_INJECTOR_OUTPUT_PARENT_DRIFT")
            issued = recovery_entry["recorded_at_ms"]
            journal_predecessor = journal.by_stage(
                fault["fault_id"], "CLEANUP_RESULT")
            _require(journal_predecessor is not None and
                     journal.entries[-1] is journal_predecessor,
                     "P1_FAULT_INJECTOR_JOURNAL_PREDECESSOR_INVALID")
            document = _receipt(
                frozen, fault, pre, action, recovery, status=status,
                issued_at_ms=issued,
                journal_predecessor=journal_predecessor)
            publish_intent = journal.by_stage(
                fault["fault_id"], "PUBLISH_INTENT")
            if publish_intent is None:
                _require_output_absent(output)
                journal.record(
                    executor, "PUBLISH_INTENT", fault, pre=pre,
                    actual_ns=action.actual_boottime_ns,
                    action_sha=action.evidence_sha256,
                    recovered_ns=recovery.recovered_boottime_ns,
                    post=recovery.post_identity, output_path=str(output),
                    output_body_sha=document["body_sha256"],
                    cleanup_complete=True)
            executor.assert_boundary()
            _assert_directory(
                output_dir, output_identity, frozen.owner_uid,
                frozen.owner_gid, "P1_FAULT_INJECTOR_OUTPUT_PARENT_DRIFT")
            staged_snapshot = _stage_receipt(
                document, output, frozen.owner_uid, frozen.owner_gid)
            executor.assert_boundary()
            _assert_directory(
                output_dir, output_identity, frozen.owner_uid,
                frozen.owner_gid, "P1_FAULT_INJECTOR_OUTPUT_PARENT_DRIFT")
            journal.record(
                executor, "PUBLISHED", fault, pre=pre,
                actual_ns=action.actual_boottime_ns,
                action_sha=action.evidence_sha256,
                recovered_ns=recovery.recovered_boottime_ns,
                post=recovery.post_identity, output_path=str(output),
                output_file_sha=staged_snapshot.file_sha256,
                output_body_sha=staged_snapshot.body_sha256,
                cleanup_complete=True)
            snapshot = _commit_staged(
                document, output, frozen.owner_uid, frozen.owner_gid)
            executor.assert_boundary()
            receipts.append(document)
        _require(len(receipts) == len(EXPECTED_FAULT_ORDER),
                 "P1_FAULT_INJECTOR_RECEIPT_SET_INCOMPLETE")
        return receipts


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--campaign-spec", type=Path, required=True)
    parser.add_argument("--fault-plan", type=Path, required=True)
    parser.add_argument("--pins", type=Path, required=True)
    parser.add_argument("--campaign-id", required=True)
    parser.add_argument("--formal-campaign-id", required=True)
    parser.add_argument("--boot-id", required=True)
    parser.add_argument("--source-manifest-sha256", required=True)
    parser.add_argument("--campaign-spec-body-sha256", required=True)
    parser.add_argument("--fault-plan-body-sha256", required=True)
    parser.add_argument("--run", action="store_true")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    arguments = _parser().parse_args(argv)
    frozen: FrozenInputs | None = None
    executor: ProductionExecutor | None = None
    try:
        _require(arguments.run, "P1_FAULT_INJECTOR_EXPLICIT_RUN_REQUIRED")
        _require(os.geteuid() == ROOT_UID and os.getegid() == ROOT_GID,
                 "P1_FAULT_INJECTOR_ROOT_REQUIRED")
        actual_boot = _read_boot_id()
        _require(arguments.boot_id == actual_boot,
                 "P1_FAULT_INJECTOR_BOOT_ID_DRIFT")
        now_ms = time.time_ns() // 1_000_000
        frozen = load_inputs(
            arguments.campaign_spec, arguments.fault_plan, arguments.pins,
            owner_uid=ROOT_UID, owner_gid=ROOT_GID,
            expected_campaign_id=arguments.campaign_id,
            expected_formal_campaign_id=arguments.formal_campaign_id,
            expected_boot_id=arguments.boot_id,
            expected_source_manifest_sha256=
                arguments.source_manifest_sha256,
            expected_spec_body_sha256=arguments.campaign_spec_body_sha256,
            expected_plan_body_sha256=arguments.fault_plan_body_sha256,
            now_ms=now_ms)
        executor = ProductionExecutor(frozen)
        receipts = run_campaign(
            frozen, executor, run_requested=True,
            effective_uid=os.geteuid(), effective_gid=os.getegid())
        print(
            "hepta_p1_safety_soak_root_fault_injector: PASS "
            f"receipts={len(receipts)} campaign_id={arguments.campaign_id}")
        return 0
    except InjectorError as error:
        if frozen is not None and executor is not None:
            try:
                reconcile_failure(frozen, executor)
            except Exception:
                pass
        print(
            "hepta_p1_safety_soak_root_fault_injector: FAIL " + error.reason,
            file=sys.stderr)
        return 1
    except Exception:
        if frozen is not None and executor is not None:
            try:
                reconcile_failure(frozen, executor)
            except Exception:
                pass
        print(
            "hepta_p1_safety_soak_root_fault_injector: FAIL "
            "P1_FAULT_INJECTOR_UNEXPECTED_FAILURE", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
