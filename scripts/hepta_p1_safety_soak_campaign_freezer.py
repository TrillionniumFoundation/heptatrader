#!/usr/bin/env python3
"""Root-only, non-authorizing P1 campaign anchor freezer.

The freezer derives the five inputs consumed by the P1 evidence recorder from
one clean source baseline, the exact strategy package, and a set of already
sealed formal SHADOW policies.  It publishes the five anchors and one bundle
receipt by a single no-replace directory rename.  It never starts services,
touches credentials, or accesses a broker.
"""

from __future__ import annotations

import argparse
import ctypes
from dataclasses import dataclass
from datetime import datetime, timezone
import errno
import hashlib
import json
import os
from pathlib import Path
import re
import secrets
import stat
import sys
import time
from typing import Any, Mapping, Sequence
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError


VERSION = 1
ROUND = 114
ROOT_UID = 0
ROOT_GID = 0
INSTALLED_EXECUTABLE = Path(
    "/usr/libexec/hepta-p1-safety-soak-campaign-freezer")
PRODUCTION_MODE = "PRODUCTION_ROOT_PREFLIGHT"
BUNDLE_SCHEMA = "hepta.p1-safety-soak-freeze-bundle-receipt.v1"
SOURCE_ANCHOR_SCHEMA = "hepta.p1-safety-soak-frozen-source.v1"
POLICY_ANCHOR_SCHEMA = "hepta.p1-safety-soak-frozen-policy.v1"
STRATEGY_ANCHOR_SCHEMA = "hepta.p1-safety-soak-frozen-strategy.v1"
SCHEDULE_SCHEMA = "hepta.p1-safety-soak-frozen-schedule.v1"
FAULT_SCHEDULE_SCHEMA = "hepta.p1-safety-soak-frozen-fault-schedule.v1"
FORMAL_POLICY_SCHEMA = "hepta.strategy-shadow-observation-policy.v1"
SOURCE_BASELINE_SCHEMA = "hepta.versioned-source-baseline.v1"
STRATEGY_SCHEMA = "hepta.confirmed-momentum-strategy.v2"
CALENDAR_SCHEMA = "hepta.p1-safety-soak-reviewed-trading-calendar.v1"
CALENDAR_ID = "EURUSD_NY_CORE_2026"
CALENDAR_VERSION = "v1"
CALENDAR_TIMEZONE = "America/New_York"
CALENDAR_EXCLUDED_DAYS_2026 = frozenset({
    "2026-01-01", "2026-01-19", "2026-02-16", "2026-04-03",
    "2026-05-25", "2026-06-19", "2026-07-03", "2026-09-07",
    "2026-11-26", "2026-12-25",
})

MAXIMUM_INPUT_BYTES = 64 * 1024 * 1024
MAXIMUM_OUTPUT_BYTES = 16 * 1024 * 1024
MAXIMUM_CLOCK_SKEW_MS = 30 * 1000
MINIMUM_TRADING_DAYS = 10
MAXIMUM_TRADING_DAYS = 20
MINIMUM_ELIGIBLE_DECISIONS = 200
MINIMUM_COMPLETE_PPM = 990_001
MINIMUM_BOOTTIME_DURATION_NS = 72 * 60 * 60 * 1_000_000_000
MAXIMUM_CHECKPOINT_GAP_NS = 15 * 60 * 1_000_000_000
MAXIMUM_DECISION_LATENESS_MS = 15 * 60 * 1000
MAXIMUM_FAULT_INJECTION_LATENESS_NS = 30 * 1_000_000_000
MAXIMUM_FAULT_RECOVERY_NS = 5 * 60 * 1_000_000_000
RETENTION_AFTER_CAMPAIGN_MS = 24 * 60 * 60 * 1000
LAUNCHER_WARMUP_MS = 210 * 60 * 1000
LAUNCHER_EARLY_START_LEAD_MS = 20 * 60 * 1000
POLICY_SLOT_INTERVAL_MS = 2 * 60 * 1000
POLICY_MAXIMUM_ITERATIONS = 241
POLICY_MAXIMUM_LATENESS_MS = 60 * 1000
MAXIMUM_LAUNCH_LATENESS_MS = 15 * 60 * 1000
POST_FORMAL_PROJECTION_GUARD_MS = 20 * 60 * 1000
POST_FORMAL_TEARDOWN_GUARD_MS = 30 * 60 * 1000

DIGEST = re.compile(r"sha256:[0-9a-f]{64}")
COMMIT = re.compile(r"[0-9a-f]{40}")
IDENTIFIER = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,127}")
DOMAIN = re.compile(r"[a-z][a-z0-9-]{0,31}")
BOOT_ID = re.compile(
    r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-"
    r"[0-9a-f]{4}-[0-9a-f]{12}")
NOFOLLOW = getattr(os, "O_NOFOLLOW", 0)
CLOEXEC = getattr(os, "O_CLOEXEC", 0)
NONBLOCK = getattr(os, "O_NONBLOCK", 0)
DIRECTORY_FLAGS = os.O_RDONLY | os.O_DIRECTORY | NOFOLLOW | CLOEXEC
READ_FLAGS = os.O_RDONLY | NOFOLLOW | CLOEXEC | NONBLOCK
CREATE_FLAGS = os.O_WRONLY | os.O_CREAT | os.O_EXCL | NOFOLLOW | CLOEXEC
RENAME_NOREPLACE = 1
_LIBC = ctypes.CDLL(None, use_errno=True)

BOUNDARY_FIELDS = (
    "paper_authorized", "live_authorized", "mutation_authorized",
    "direct_broker_access",
)
PRODUCER_FIELDS = frozenset({"path", "file_sha256"})
REFERENCE_FIELDS = frozenset({"path", "file_sha256", "body_sha256"})
FORMAL_REFERENCE_FIELDS = frozenset({
    "campaign_id", "path", "file_sha256", "body_sha256",
    "launcher_start_ms", "launcher_dispatch_at_ms",
    "valid_after_ms", "expires_at_ms", "slot_interval_ms",
    "maximum_iterations", "launcher_completion_deadline_ms",
    "projection_deadline_ms", "teardown_deadline_ms",
})
STRATEGY_FILE_FIELDS = frozenset({
    "role", "path", "file_sha256", "body_sha256",
})
SOURCE_PRODUCER_PIN_FIELDS = frozenset({
    "role", "source_path", "installed_path", "file_sha256",
})
SOURCE_PRODUCER_PATHS = {
    "campaign_freezer": (
        "scripts/hepta_p1_safety_soak_campaign_freezer.py",
        str(INSTALLED_EXECUTABLE)),
    "evidence_recorder": (
        "scripts/hepta_p1_safety_soak_evidence_recorder.py",
        "/usr/libexec/hepta-p1-safety-soak-evidence-recorder"),
    "independent_observer": (
        "scripts/hepta_p1_safety_soak_independent_observer.py",
        "/usr/libexec/hepta-p1-safety-soak-independent-observer"),
    "root_fault_injector": (
        "scripts/hepta_p1_safety_soak_root_fault_injector.py",
        "/usr/libexec/hepta-p1-safety-soak-root-fault-injector"),
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
        "/usr/libexec/hepta-p1-safety-soak-fault-pin-producer"),
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
CALENDAR_WINDOW_FIELDS = frozenset({"opens_at_ms", "closes_at_ms"})
CALENDAR_SESSION_FIELDS = frozenset({
    "trading_day", "opens_at_ms", "closes_at_ms", "maintenance_windows",
})
CALENDAR_FIELDS = frozenset({
    "schema", "version", "status", "freeze_id", "producer",
    "production_mode", "calendar_id", "calendar_version",
    "calendar_source_sha256", "trading_timezone", "sessions",
    "issued_at_ms", "expires_at_ms",
    *BOUNDARY_FIELDS, "body_sha256",
})
PLANNED_FAULT_FIELDS = frozenset({
    "fault_id", "fault_type", "target_id", "formal_campaign_id",
    "inject_at_boottime_ns",
    "maximum_injection_lateness_ns", "maximum_recovery_ns",
})
FAULT_TYPES = (
    "PROCESS_KILL", "SERVICE_RESTART", "TOKEN_LOSS", "LEASE_EXPIRY",
    "NETWORK_DENY_RELOAD", "EVIDENCE_WRITER_CRASH", "CLOCK_STEP",
)
FAULT_TARGET_IDS = {
    "PROCESS_KILL": "p1-independent-observer-process",
    "SERVICE_RESTART": "watch-execution-gateway",
    "TOKEN_LOSS": "fault-fixture-watch-session-token",
    "LEASE_EXPIRY": "fault-fixture-watch-lease",
    "NETWORK_DENY_RELOAD": "broker-egress-deny-policy",
    "EVIDENCE_WRITER_CRASH": "p1-safety-soak-evidence-recorder",
    "CLOCK_STEP": "wall-clock-discontinuity-detector",
}
OUTPUT_NAMES = {
    "source_anchor": "source-anchor.json",
    "policy_anchor": "policy-anchor.json",
    "strategy_anchor": "strategy-anchor.json",
    "frozen_schedule": "schedule.json",
    "frozen_fault_schedule": "fault-schedule.json",
    "trading_calendar": "reviewed-trading-calendar.json",
}
ANCHOR_ROLES = frozenset({
    "source_anchor", "policy_anchor", "strategy_anchor", "frozen_schedule",
    "frozen_fault_schedule",
})
RUNTIME_FILES = {
    "evaluator": "hepta_eurusd_confirmed_momentum_strategy.py",
    "context_builder": "hepta_market_context_builder.py",
    "normalizer": "hepta_market_evidence_normalizer.py",
    "contracts": "hepta_strategy_contracts.py",
}

FORMAL_POLICY_FIELDS = frozenset({
    "schema", "version", "campaign_id", "campaign_sha256",
    "strategy_id", "strategy_version", "strategy_sha256",
    "valid_after_ms", "expires_at_ms", "slot_interval_ms",
    "maximum_iterations", "maximum_lateness_ms", "shadow_only",
    "paper_authorized", "live_authorized", "mutation_attempted",
    "direct_broker_access", "body_sha256",
})
SOURCE_BASELINE_FIELDS = frozenset({
    "schema", "version", "generated_at", "git_head", "source_manifest",
    "source_baseline_frozen", "clean_checkout_certified",
    "release_authorized", "paper_authorized", "live_authorized",
    "worktree_status_entry_count", "blocked_reason", "excluded_unsafe_tree",
})
SOURCE_MANIFEST_FIELDS = frozenset({"file_count", "sha256", "files"})
ANCHOR_COMMON_FIELDS = frozenset({
    "freeze_id", "producer", "production_mode", *BOUNDARY_FIELDS,
    "body_sha256",
})
SOURCE_ANCHOR_FIELDS = ANCHOR_COMMON_FIELDS | frozenset({
    "schema", "version", "status", "source_manifest_sha256",
    "source_frozen", "clean_source", "frozen_at_ms", "expires_at_ms",
})
POLICY_ANCHOR_FIELDS = ANCHOR_COMMON_FIELDS | frozenset({
    "schema", "version", "status", "policy_sha256", "policy_frozen",
    "frozen_at_ms", "expires_at_ms",
})
STRATEGY_ANCHOR_FIELDS = ANCHOR_COMMON_FIELDS | frozenset({
    "schema", "version", "status", "strategy_id", "strategy_version",
    "strategy_sha256", "strategy_frozen", "frozen_at_ms", "expires_at_ms",
})
SCHEDULE_FIELDS = ANCHOR_COMMON_FIELDS | frozenset({
    "schema", "version", "status", "campaign_id", "domain_id",
    "declared_trading_days", "trading_timezone",
    "eligible_scheduled_at_ms", "minimum_eligible_decisions",
    "minimum_complete_ppm", "minimum_boottime_duration_ns",
    "maximum_checkpoint_gap_ns", "maximum_decision_lateness_ms",
    "independent_auditor_id", "frozen_at_ms", "expires_at_ms",
})
FAULT_SCHEDULE_FIELDS = ANCHOR_COMMON_FIELDS | frozenset({
    "schema", "version", "status", "campaign_id", "boot_id",
    "frozen_boottime_ns", "planned_faults", "frozen_at_ms", "expires_at_ms",
})
BUNDLE_FIELDS = frozenset({
    "schema", "version", "status", "round", "freeze_id", "issued_at_ms",
    "expires_at_ms", "campaign_id", "domain_id", "producer",
    "production_mode", "boot_id", "frozen_boottime_ns",
    "source_baseline", "source_manifest_sha256", "source_producer_pins",
    "policy_sha256",
    "formal_policies", "strategy_id", "strategy_version",
    "strategy_sha256", "strategy_files", "trading_calendar",
    "calendar_id", "calendar_version", "calendar_source_sha256",
    "declared_trading_days",
    "trading_timezone", "trading_calendar_sha256",
    "eligible_scheduled_at_ms", "scheduled_decision_count",
    "planned_faults", "anchors", *BOUNDARY_FIELDS, "body_sha256",
})


class FreezeError(RuntimeError):
    """Stable fail-closed campaign-freeze failure."""

    def __init__(self, reason: str):
        super().__init__(reason)
        self.reason = reason


@dataclass(frozen=True)
class Snapshot:
    path: Path
    payload: bytes
    metadata: os.stat_result
    document: dict[str, Any]
    file_sha256: str
    body_sha256: str


@dataclass(frozen=True)
class ProducerBinding:
    path: Path
    payload: bytes
    metadata: os.stat_result

    @property
    def reference(self) -> dict[str, str]:
        return {"path": str(self.path), "file_sha256": digest_bytes(self.payload)}

    def reopen(self) -> None:
        payload, metadata = secure_read(
            self.path, expected_uid=ROOT_UID, expected_gid=ROOT_GID,
            modes=frozenset({0o755}), maximum=MAXIMUM_INPUT_BYTES)
        _require(payload == self.payload and
                 file_identity(metadata) == file_identity(self.metadata),
                 "P1_FREEZER_EXECUTING_IMAGE_DRIFT")


@dataclass(frozen=True)
class FrozenBundle:
    receipt: dict[str, Any]
    documents: Mapping[str, dict[str, Any]]
    inputs: tuple[Snapshot, ...]


def _require(condition: bool, reason: str) -> None:
    if not condition:
        raise FreezeError(reason)


def canonical_bytes(value: Any) -> bytes:
    try:
        return (json.dumps(
            value, ensure_ascii=True, allow_nan=False, sort_keys=True,
            separators=(",", ":")) + "\n").encode("ascii")
    except (TypeError, ValueError, UnicodeError) as error:
        raise FreezeError("P1_FREEZER_CANONICALIZATION_FAILED") from error


def digest_bytes(payload: bytes) -> str:
    return "sha256:" + hashlib.sha256(payload).hexdigest()


def seal(body: Mapping[str, Any]) -> dict[str, Any]:
    value = dict(body)
    value["body_sha256"] = digest_bytes(canonical_bytes(value))
    return value


def _exact(value: Any, fields: frozenset[str], reason: str) -> dict[str, Any]:
    _require(isinstance(value, dict) and set(value) == fields, reason)
    return value


def _digest(value: Any, reason: str) -> str:
    _require(type(value) is str and DIGEST.fullmatch(value) is not None and
             value != "sha256:" + "0" * 64, reason)
    return value


def _identifier(value: Any, pattern: re.Pattern[str], reason: str) -> str:
    _require(type(value) is str and pattern.fullmatch(value) is not None, reason)
    return value


def _integer(value: Any, reason: str, minimum: int = 0) -> int:
    _require(type(value) is int and value >= minimum, reason)
    return value


def required_valid_after_after_teardown(
    previous_teardown_ms: int, slot_interval_ms: int,
) -> int:
    """Return the first aligned policy start whose dispatch is strictly next."""

    _require(type(previous_teardown_ms) is int and previous_teardown_ms >= 0 and
             type(slot_interval_ms) is int and slot_interval_ms > 0,
             "P1_FREEZER_FORMAL_ADJACENCY_INVALID")
    return (
        (previous_teardown_ms + LAUNCHER_WARMUP_MS +
         LAUNCHER_EARLY_START_LEAD_MS) // slot_interval_ms + 1
    ) * slot_interval_ms


def _boundary() -> dict[str, bool]:
    return {field: False for field in BOUNDARY_FIELDS}


def _reject_authority(value: Any, reason: str) -> None:
    if isinstance(value, dict):
        for key, child in value.items():
            if key in {*BOUNDARY_FIELDS, "mutation_attempted",
                       "order_submission_authorized"}:
                _require(child is False, reason)
            _reject_authority(child, reason)
    elif isinstance(value, list):
        for child in value:
            _reject_authority(child, reason)


def _validate_seal(value: dict[str, Any], reason: str) -> str:
    body = dict(value)
    claimed = _digest(body.pop("body_sha256", None), reason)
    _require(claimed == digest_bytes(canonical_bytes(body)), reason)
    return claimed


def strict_document(payload: bytes, reason: str) -> dict[str, Any]:
    def unique(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in pairs:
            _require(key not in result, reason)
            result[key] = value
        return result

    try:
        value = json.loads(
            payload.decode("ascii", errors="strict"),
            object_pairs_hook=unique,
            parse_constant=lambda _raw: (_ for _ in ()).throw(
                FreezeError(reason)))
    except FreezeError:
        raise
    except (UnicodeError, json.JSONDecodeError, ValueError) as error:
        raise FreezeError(reason) from error
    _require(isinstance(value, dict), reason)
    return value


def file_identity(value: os.stat_result) -> tuple[int, ...]:
    return (
        value.st_dev, value.st_ino, value.st_uid, value.st_gid, value.st_mode,
        value.st_nlink, value.st_size, value.st_mtime_ns, value.st_ctime_ns,
    )


def directory_identity(value: os.stat_result) -> tuple[int, ...]:
    return (
        value.st_dev, value.st_ino, value.st_uid, value.st_gid,
        stat.S_IMODE(value.st_mode),
    )


def canonical_path(path: Path, reason: str) -> Path:
    _require(path.is_absolute(), reason)
    normalized = Path(os.path.normpath(os.fspath(path)))
    _require(normalized == path and path.name not in {"", ".", ".."} and
             all(part not in {"", ".", ".."} for part in path.parts[1:]),
             reason)
    return normalized


def open_trusted_directory(
    path: Path, *, expected_uid: int, expected_gid: int, reason: str,
) -> tuple[int, tuple[tuple[int, ...], ...]]:
    path = canonical_path(path, reason)
    descriptor = os.open("/", DIRECTORY_FLAGS)
    identities: list[tuple[int, ...]] = [directory_identity(os.fstat(descriptor))]
    try:
        for component in path.parts[1:]:
            before = os.stat(component, dir_fd=descriptor,
                             follow_symlinks=False)
            child = os.open(component, DIRECTORY_FLAGS, dir_fd=descriptor)
            opened = os.fstat(child)
            mode = stat.S_IMODE(opened.st_mode)
            _require(
                stat.S_ISDIR(opened.st_mode) and opened.st_nlink >= 1 and
                opened.st_uid in {ROOT_UID, expected_uid} and
                not mode & 0o022 and
                directory_identity(before) == directory_identity(opened),
                reason)
            os.close(descriptor)
            descriptor = child
            identities.append(directory_identity(opened))
        leaf = os.fstat(descriptor)
        _require(leaf.st_uid == expected_uid and leaf.st_gid == expected_gid,
                 reason)
        return descriptor, tuple(identities)
    except BaseException:
        os.close(descriptor)
        raise


def secure_read(
    path: Path, *, expected_uid: int, expected_gid: int,
    modes: frozenset[int] = frozenset({0o400, 0o600}),
    maximum: int = MAXIMUM_INPUT_BYTES,
) -> tuple[bytes, os.stat_result]:
    path = canonical_path(path, "P1_FREEZER_INPUT_PATH_INVALID")
    parent, ancestors = open_trusted_directory(
        path.parent, expected_uid=expected_uid, expected_gid=expected_gid,
        reason="P1_FREEZER_INPUT_ANCESTOR_UNTRUSTED")
    descriptor: int | None = None
    rebound: int | None = None
    reopened: int | None = None
    try:
        before = os.stat(path.name, dir_fd=parent, follow_symlinks=False)
        descriptor = os.open(path.name, READ_FLAGS, dir_fd=parent)
        opened = os.fstat(descriptor)
        _require(
            stat.S_ISREG(opened.st_mode) and opened.st_nlink == 1 and
            opened.st_uid == expected_uid and opened.st_gid == expected_gid and
            stat.S_IMODE(opened.st_mode) in modes and
            0 < opened.st_size <= maximum and
            file_identity(before) == file_identity(opened),
            "P1_FREEZER_INPUT_METADATA_INVALID")
        chunks: list[bytes] = []
        total = 0
        while total <= maximum:
            chunk = os.read(descriptor, min(65536, maximum + 1 - total))
            if not chunk:
                break
            chunks.append(chunk)
            total += len(chunk)
        payload = b"".join(chunks)
        _require(0 < len(payload) <= maximum,
                 "P1_FREEZER_INPUT_SIZE_INVALID")
        rebound, rebound_ancestors = open_trusted_directory(
            path.parent, expected_uid=expected_uid, expected_gid=expected_gid,
            reason="P1_FREEZER_INPUT_ANCESTOR_REBOUND")
        reopened = os.open(path.name, READ_FLAGS, dir_fd=rebound)
        reopened_metadata = os.fstat(reopened)
        reopened_payload = bytearray()
        while len(reopened_payload) <= maximum:
            chunk = os.read(
                reopened, min(65536, maximum + 1 - len(reopened_payload)))
            if not chunk:
                break
            reopened_payload.extend(chunk)
        _require(
            ancestors == rebound_ancestors and
            file_identity(opened) == file_identity(os.fstat(descriptor)) ==
                file_identity(reopened_metadata) and
            payload == bytes(reopened_payload),
            "P1_FREEZER_INPUT_SECURE_REOPEN_MISMATCH")
        return payload, opened
    except FreezeError:
        raise
    except OSError as error:
        raise FreezeError("P1_FREEZER_INPUT_READ_FAILED") from error
    finally:
        for item in (reopened, rebound, descriptor, parent):
            if item is not None:
                os.close(item)


def load_snapshot(
    path: Path, *, expected_uid: int, expected_gid: int,
    sealed: bool, modes: frozenset[int] = frozenset({0o400, 0o600}),
) -> Snapshot:
    payload, metadata = secure_read(
        path, expected_uid=expected_uid, expected_gid=expected_gid, modes=modes)
    document = strict_document(payload, "P1_FREEZER_INPUT_JSON_INVALID")
    if sealed:
        _require(payload == canonical_bytes(document),
                 "P1_FREEZER_INPUT_NOT_CANONICAL")
        body_sha = _validate_seal(document, "P1_FREEZER_INPUT_SEAL_INVALID")
    else:
        _require("body_sha256" not in document,
                 "P1_FREEZER_INPUT_UNEXPECTED_SEAL")
        body_sha = digest_bytes(canonical_bytes(document))
    _reject_authority(document, "P1_FREEZER_INPUT_AUTHORITY_NOT_FALSE")
    return Snapshot(
        path, payload, metadata, document, digest_bytes(payload), body_sha)


def reference(snapshot: Snapshot) -> dict[str, str]:
    return {
        "path": str(snapshot.path), "file_sha256": snapshot.file_sha256,
        "body_sha256": snapshot.body_sha256,
    }


def bind_executing_image() -> ProducerBinding:
    try:
        executing = Path(__file__)
        _require(not executing.is_symlink() and
                 executing.resolve(strict=True) == INSTALLED_EXECUTABLE and
                 os.path.samefile(executing, INSTALLED_EXECUTABLE),
                 "P1_FREEZER_INSTALLED_IMAGE_REQUIRED")
        payload, metadata = secure_read(
            INSTALLED_EXECUTABLE, expected_uid=ROOT_UID, expected_gid=ROOT_GID,
            modes=frozenset({0o755}), maximum=MAXIMUM_INPUT_BYTES)
    except (OSError, FreezeError):
        raise FreezeError("P1_FREEZER_INSTALLED_IMAGE_REQUIRED")
    return ProducerBinding(INSTALLED_EXECUTABLE, payload, metadata)


def validate_source_baseline(
    snapshot: Snapshot, expected_file_sha256: str,
) -> tuple[str, list[dict[str, str]]]:
    reason = "P1_FREEZER_SOURCE_BASELINE_INVALID"
    value = _exact(snapshot.document, SOURCE_BASELINE_FIELDS, reason)
    _require(snapshot.file_sha256 == _digest(expected_file_sha256, reason) and
             value.get("schema") == SOURCE_BASELINE_SCHEMA and
             type(value.get("version")) is str and bool(value["version"]) and
             type(value.get("generated_at")) is str and
             COMMIT.fullmatch(str(value.get("git_head", ""))) is not None and
             value.get("source_baseline_frozen") is True and
             value.get("clean_checkout_certified") is True and
             value.get("release_authorized") is False and
             value.get("paper_authorized") is False and
             value.get("live_authorized") is False and
             value.get("worktree_status_entry_count") == 0 and
             value.get("blocked_reason") is None and
             value.get("excluded_unsafe_tree") == "compat/unsafe-direct-broker",
             reason)
    manifest = _exact(value.get("source_manifest"), SOURCE_MANIFEST_FIELDS,
                      reason)
    files = manifest.get("files")
    _require(isinstance(files, list) and bool(files) and
             manifest.get("file_count") == len(files), reason)
    calculated = digest_bytes(json.dumps(
        files, ensure_ascii=True, allow_nan=False, sort_keys=True,
        separators=(",", ":")).encode("ascii"))
    _require(_digest(manifest.get("sha256"), reason) == calculated, reason)
    by_path: dict[str, Mapping[str, Any]] = {}
    for item in files:
        _require(isinstance(item, dict) and type(item.get("path")) is str and
                 type(item.get("sha256")) is str, reason)
        by_path[item["path"]] = item
    pins: list[dict[str, str]] = []
    for role, (source_path, installed_path) in sorted(
            SOURCE_PRODUCER_PATHS.items()):
        _require(source_path in by_path, reason)
        file_sha = _digest(by_path[source_path].get("sha256"), reason)
        pins.append({
            "role": role, "source_path": source_path,
            "installed_path": installed_path, "file_sha256": file_sha,
        })
    return calculated, pins


def validate_strategy(
    config: Snapshot, runtime: Mapping[str, Snapshot],
) -> tuple[str, str, str, list[dict[str, str]]]:
    reason = "P1_FREEZER_STRATEGY_INVALID"
    value = config.document
    _require(value.get("schema") == STRATEGY_SCHEMA and
             value.get("paper_only") is True and
             value.get("live_authorized") is False, reason)
    strategy_id = _identifier(value.get("strategy_id"), IDENTIFIER, reason)
    strategy_version = _identifier(
        value.get("strategy_version"), IDENTIFIER, reason)
    files = [{
        "role": "config", **reference(config),
    }]
    files.extend({"role": role, **reference(snapshot)}
                 for role, snapshot in sorted(runtime.items()))
    package = {
        "schema": "hepta.strategy-package-binding.v3",
        "strategy_id": strategy_id,
        "strategy_version": strategy_version,
        "config_sha256": config.file_sha256,
        "evaluator_sha256": runtime["evaluator"].file_sha256,
        "builder_sha256": runtime["context_builder"].file_sha256,
        "normalizer_sha256": runtime["normalizer"].file_sha256,
        "contracts_sha256": runtime["contracts"].file_sha256,
    }
    return strategy_id, strategy_version, digest_bytes(canonical_bytes(package)), files


def validate_formal_policy(
    snapshot: Snapshot, *, strategy_id: str, strategy_version: str,
    strategy_sha256: str, now_ms: int,
) -> tuple[dict[str, Any], list[int], dict[str, Any]]:
    reason = "P1_FREEZER_FORMAL_POLICY_INVALID"
    value = _exact(snapshot.document, FORMAL_POLICY_FIELDS, reason)
    _validate_seal(value, reason)
    _require(
        value.get("schema") == FORMAL_POLICY_SCHEMA and
        value.get("version") == VERSION and
        value.get("strategy_id") == strategy_id and
        value.get("strategy_version") == strategy_version and
        value.get("strategy_sha256") == strategy_sha256 and
        value.get("shadow_only") is True and
        value.get("paper_authorized") is False and
        value.get("live_authorized") is False and
        value.get("mutation_attempted") is False and
        value.get("direct_broker_access") is False, reason)
    campaign = _identifier(value.get("campaign_id"), IDENTIFIER, reason)
    valid_after = _integer(value.get("valid_after_ms"), reason, now_ms + 1)
    expires = _integer(value.get("expires_at_ms"), reason, valid_after + 1)
    interval = _integer(value.get("slot_interval_ms"), reason, 1)
    maximum = _integer(value.get("maximum_iterations"), reason, 1)
    lateness = _integer(value.get("maximum_lateness_ms"), reason)
    _require(
        interval == POLICY_SLOT_INTERVAL_MS and
        maximum == POLICY_MAXIMUM_ITERATIONS and
        lateness == POLICY_MAXIMUM_LATENESS_MS and
        expires == valid_after + interval * maximum,
        reason)
    launcher_start = valid_after - LAUNCHER_WARMUP_MS
    launcher_dispatch = launcher_start - LAUNCHER_EARLY_START_LEAD_MS
    _require(
        launcher_start > 0 and launcher_dispatch > now_ms and
        ((launcher_start + LAUNCHER_WARMUP_MS + interval - 1) //
         interval) * interval == valid_after,
        reason)
    binding = {
        "schema": "hepta.strategy-shadow-observation-campaign.v1",
        "campaign_id": campaign, "valid_after_ms": valid_after,
        "expires_at_ms": expires, "slot_interval_ms": interval,
        "maximum_iterations": maximum, "maximum_lateness_ms": lateness,
        "shadow_only": True, "paper_authorized": False,
        "live_authorized": False, "mutation_attempted": False,
        "direct_broker_access": False,
    }
    _require(value.get("campaign_sha256") ==
             digest_bytes(canonical_bytes(binding)), reason)
    slots = [valid_after + offset * interval for offset in range(maximum)]
    record = {
        "campaign_id": campaign, **reference(snapshot),
        "launcher_start_ms": launcher_start,
        "launcher_dispatch_at_ms": launcher_dispatch,
        "valid_after_ms": valid_after, "expires_at_ms": expires,
        "slot_interval_ms": interval, "maximum_iterations": maximum,
        "launcher_completion_deadline_ms":
            expires + MAXIMUM_LAUNCH_LATENESS_MS,
        "projection_deadline_ms":
            expires + POST_FORMAL_PROJECTION_GUARD_MS,
        "teardown_deadline_ms":
            expires + POST_FORMAL_TEARDOWN_GUARD_MS,
    }
    return value, slots, record


def build_calendar(
    *, all_slots: Sequence[int], now_ms: int, expires_at_ms: int,
    freeze_id: str, producer: Mapping[str, str], timezone_name: str,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """Build the source-coded conservative 2026 EURUSD session contract."""

    reason = "P1_FREEZER_TRADING_CALENDAR_INVALID"
    _require(timezone_name == CALENDAR_TIMEZONE, reason)
    zone = ZoneInfo(CALENDAR_TIMEZONE)
    candidate_days = sorted({
        datetime.fromtimestamp(slot / 1000, tz=timezone.utc)
        .astimezone(zone).date() for slot in all_slots
    })
    days = [item for item in candidate_days
            if item.year == 2026 and item.weekday() < 5 and
            item.isoformat() not in CALENDAR_EXCLUDED_DAYS_2026]
    _require(MINIMUM_TRADING_DAYS <= len(days) <= MAXIMUM_TRADING_DAYS,
             reason)
    sessions: list[dict[str, Any]] = []
    for day in days:
        opens = datetime(
            day.year, day.month, day.day, 9, 0, tzinfo=zone)
        closes = datetime(
            day.year, day.month, day.day, 16, 0, tzinfo=zone)
        maintenance_start = datetime(
            day.year, day.month, day.day, 12, 0, tzinfo=zone)
        maintenance_end = datetime(
            day.year, day.month, day.day, 12, 15, tzinfo=zone)
        sessions.append({
            "trading_day": day.isoformat(),
            "opens_at_ms": int(opens.timestamp() * 1000),
            "closes_at_ms": int(closes.timestamp() * 1000),
            "maintenance_windows": [{
                "opens_at_ms": int(maintenance_start.timestamp() * 1000),
                "closes_at_ms": int(maintenance_end.timestamp() * 1000),
            }],
        })
    source_contract = {
        "schema": "hepta.p1-safety-soak-calendar-rule.v1",
        "calendar_id": CALENDAR_ID, "calendar_version": CALENDAR_VERSION,
        "instrument": "EURUSD", "year": 2026,
        "trading_timezone": CALENDAR_TIMEZONE,
        "core_open_local": "09:00", "core_close_local": "16:00",
        "maintenance_open_local": "12:00",
        "maintenance_close_local": "12:15",
        "excluded_days": sorted(CALENDAR_EXCLUDED_DAYS_2026),
    }
    document = seal({
        "schema": CALENDAR_SCHEMA, "version": VERSION, "status": "FROZEN",
        "freeze_id": freeze_id, "producer": dict(producer),
        "production_mode": PRODUCTION_MODE,
        "calendar_id": CALENDAR_ID, "calendar_version": CALENDAR_VERSION,
        "calendar_source_sha256": digest_bytes(canonical_bytes(source_contract)),
        "trading_timezone": CALENDAR_TIMEZONE, "sessions": sessions,
        "issued_at_ms": now_ms, "expires_at_ms": expires_at_ms,
        **_boundary(),
    })
    _exact(document, CALENDAR_FIELDS, reason)
    _validate_seal(document, reason)
    return document, sessions


def eligible_slots_from_calendar(
    all_slots: Sequence[int], sessions: Sequence[Mapping[str, Any]],
) -> tuple[list[int], list[str]]:
    eligible: list[int] = []
    observed_days: set[str] = set()
    for slot in all_slots:
        for session in sessions:
            if not (session["opens_at_ms"] <= slot < session["closes_at_ms"]):
                continue
            if any(window["opens_at_ms"] <= slot < window["closes_at_ms"]
                   for window in session["maintenance_windows"]):
                continue
            eligible.append(slot)
            observed_days.add(str(session["trading_day"]))
            break
    days = [str(item["trading_day"]) for item in sessions]
    _require(observed_days == set(days),
             "P1_FREEZER_CALENDAR_DAY_WITHOUT_ELIGIBLE_SLOT")
    return eligible, days


def derive_faults(
    *, formal_records: Sequence[Mapping[str, Any]], now_ms: int,
    boottime_ns: int,
) -> list[dict[str, Any]]:
    reason = "P1_FREEZER_FAULT_WINDOW_INVALID"
    _require(bool(formal_records), reason)
    assigned: dict[int, list[tuple[int, str]]] = {}
    for index, fault_type in enumerate(FAULT_TYPES, start=1):
        formal_index = min(
            (index - 1) * len(formal_records) // len(FAULT_TYPES),
            len(formal_records) - 1)
        assigned.setdefault(formal_index, []).append((index, fault_type))
    faults: list[dict[str, Any]] = []
    guard_ns = (MAXIMUM_FAULT_INJECTION_LATENESS_NS +
                MAXIMUM_FAULT_RECOVERY_NS)
    for formal_index, items in assigned.items():
        formal = formal_records[formal_index]
        start_ns = boottime_ns + (
            formal["valid_after_ms"] - now_ms) * 1_000_000
        end_ns = boottime_ns + (
            formal["expires_at_ms"] - now_ms) * 1_000_000
        usable = end_ns - start_ns - guard_ns
        _require(now_ms < formal["valid_after_ms"] and
                 usable > len(items) * guard_ns, reason)
        for position, (index, fault_type) in enumerate(items, start=1):
            injection = start_ns + usable * position // (len(items) + 1)
            faults.append({
                "fault_id": f"p1-fault-{index:02d}-{fault_type.lower()}",
                "fault_type": fault_type,
                "target_id": FAULT_TARGET_IDS[fault_type],
                "formal_campaign_id": formal["campaign_id"],
                "inject_at_boottime_ns": injection,
                "maximum_injection_lateness_ns":
                    MAXIMUM_FAULT_INJECTION_LATENESS_NS,
                "maximum_recovery_ns": MAXIMUM_FAULT_RECOVERY_NS,
            })
    faults.sort(key=lambda item: item["inject_at_boottime_ns"])
    for before, after in zip(faults, faults[1:]):
        _require(
            after["inject_at_boottime_ns"] >
            before["inject_at_boottime_ns"] +
            before["maximum_injection_lateness_ns"] +
            before["maximum_recovery_ns"], reason)
    return faults


def build_bundle(
    *, source_baseline: Snapshot, strategy_config: Snapshot,
    strategy_runtime: Mapping[str, Snapshot],
    formal_policies: Sequence[Snapshot],
    expected_source_baseline_file_sha256: str, campaign_id: str,
    domain_id: str, trading_timezone: str, independent_auditor_id: str,
    producer: Mapping[str, str], now_ms: int, boottime_ns: int, boot_id: str,
    freeze_id: str | None = None,
) -> tuple[dict[str, dict[str, Any]], dict[str, Any]]:
    reason = "P1_FREEZER_BUILD_INVALID"
    campaign = _identifier(campaign_id, IDENTIFIER, reason)
    domain = _identifier(domain_id, DOMAIN, reason)
    auditor = _identifier(independent_auditor_id, IDENTIFIER, reason)
    _require(type(now_ms) is int and now_ms >= 0 and
             type(boottime_ns) is int and boottime_ns >= 0 and
             BOOT_ID.fullmatch(boot_id) is not None and
             isinstance(producer, Mapping) and set(producer) == PRODUCER_FIELDS and
             producer.get("path") == str(INSTALLED_EXECUTABLE), reason)
    _digest(producer.get("file_sha256"), reason)
    identity = freeze_id or secrets.token_hex(16)
    _require(re.fullmatch(r"[0-9a-f]{32}", identity) is not None, reason)
    source_manifest_sha, source_producer_pins = validate_source_baseline(
        source_baseline, expected_source_baseline_file_sha256)
    freezer_pin = next(item for item in source_producer_pins
                       if item["role"] == "campaign_freezer")
    _require(freezer_pin["file_sha256"] == producer["file_sha256"],
             "P1_FREEZER_EXECUTING_IMAGE_SOURCE_DRIFT")
    strategy_id, strategy_version, strategy_sha, strategy_files = \
        validate_strategy(strategy_config, strategy_runtime)
    _require(bool(formal_policies), reason)
    validated = [validate_formal_policy(
        item, strategy_id=strategy_id, strategy_version=strategy_version,
        strategy_sha256=strategy_sha, now_ms=now_ms)
        for item in formal_policies]
    validated.sort(key=lambda item: item[1][0])
    formal_records = [item[2] for item in validated]
    _require(len({item["campaign_id"] for item in formal_records}) ==
             len(formal_records), reason)
    for previous, current in zip(formal_records, formal_records[1:]):
        _require(
            current["valid_after_ms"] ==
                required_valid_after_after_teardown(
                    previous["teardown_deadline_ms"],
                    current["slot_interval_ms"]),
            "P1_FREEZER_FORMAL_ADJACENCY_INVALID")
    all_slots = [slot for _value, slots, _record in validated for slot in slots]
    _require(all_slots == sorted(set(all_slots)) and
             len(all_slots) >= MINIMUM_ELIGIBLE_DECISIONS and
             now_ms < all_slots[0], "P1_FREEZER_SCHEDULE_INVALID")
    maximum_campaign_expiry = max(
        item[0]["expires_at_ms"] for item in validated)
    expiry = maximum_campaign_expiry + RETENTION_AFTER_CAMPAIGN_MS
    calendar_document, sessions = build_calendar(
        all_slots=all_slots, now_ms=now_ms, expires_at_ms=expiry,
        freeze_id=identity, producer=producer,
        timezone_name=trading_timezone)
    calendar_id = calendar_document["calendar_id"]
    calendar_version = calendar_document["calendar_version"]
    calendar_source_sha = calendar_document["calendar_source_sha256"]
    eligible_slots, days = eligible_slots_from_calendar(all_slots, sessions)
    _require(len(eligible_slots) >= MINIMUM_ELIGIBLE_DECISIONS,
             "P1_FREEZER_ELIGIBLE_DECISIONS_BELOW_MINIMUM")
    policy_set = {
        "schema": "hepta.p1-safety-soak-formal-policy-set.v1",
        "formal_policies": [{key: record[key] for key in (
            "campaign_id", "file_sha256", "body_sha256")}
            for record in formal_records],
        "minimum_complete_ppm": MINIMUM_COMPLETE_PPM,
        "minimum_eligible_decisions": MINIMUM_ELIGIBLE_DECISIONS,
        "minimum_boottime_duration_ns": MINIMUM_BOOTTIME_DURATION_NS,
        "maximum_checkpoint_gap_ns": MAXIMUM_CHECKPOINT_GAP_NS,
        "maximum_decision_lateness_ms": MAXIMUM_DECISION_LATENESS_MS,
    }
    policy_sha = digest_bytes(canonical_bytes(policy_set))
    calendar_sha = calendar_document["body_sha256"]
    faults = derive_faults(
        formal_records=formal_records, now_ms=now_ms,
        boottime_ns=boottime_ns)
    common = {
        "freeze_id": identity, "producer": dict(producer),
        "production_mode": PRODUCTION_MODE, **_boundary(),
    }
    documents = {
        "source_anchor": seal({
            "schema": SOURCE_ANCHOR_SCHEMA, "version": VERSION,
            "status": "FROZEN", "source_manifest_sha256": source_manifest_sha,
            "source_frozen": True, "clean_source": True,
            "frozen_at_ms": now_ms, "expires_at_ms": expiry, **common,
        }),
        "policy_anchor": seal({
            "schema": POLICY_ANCHOR_SCHEMA, "version": VERSION,
            "status": "FROZEN", "policy_sha256": policy_sha,
            "policy_frozen": True, "frozen_at_ms": now_ms,
            "expires_at_ms": expiry, **common,
        }),
        "strategy_anchor": seal({
            "schema": STRATEGY_ANCHOR_SCHEMA, "version": VERSION,
            "status": "FROZEN", "strategy_id": strategy_id,
            "strategy_version": strategy_version,
            "strategy_sha256": strategy_sha, "strategy_frozen": True,
            "frozen_at_ms": now_ms, "expires_at_ms": expiry, **common,
        }),
        "frozen_schedule": seal({
            "schema": SCHEDULE_SCHEMA, "version": VERSION,
            "status": "FROZEN", "campaign_id": campaign,
            "domain_id": domain, "declared_trading_days": days,
            "trading_timezone": trading_timezone,
            "eligible_scheduled_at_ms": eligible_slots,
            "minimum_eligible_decisions": MINIMUM_ELIGIBLE_DECISIONS,
            "minimum_complete_ppm": MINIMUM_COMPLETE_PPM,
            "minimum_boottime_duration_ns": MINIMUM_BOOTTIME_DURATION_NS,
            "maximum_checkpoint_gap_ns": MAXIMUM_CHECKPOINT_GAP_NS,
            "maximum_decision_lateness_ms": MAXIMUM_DECISION_LATENESS_MS,
            "independent_auditor_id": auditor, "frozen_at_ms": now_ms,
            "expires_at_ms": expiry, **common,
        }),
        "frozen_fault_schedule": seal({
            "schema": FAULT_SCHEDULE_SCHEMA, "version": VERSION,
            "status": "FROZEN", "campaign_id": campaign,
            "boot_id": boot_id, "frozen_boottime_ns": boottime_ns,
            "planned_faults": faults, "frozen_at_ms": now_ms,
            "expires_at_ms": expiry, **common,
        }),
        "trading_calendar": calendar_document,
    }
    field_sets = {
        "source_anchor": SOURCE_ANCHOR_FIELDS,
        "policy_anchor": POLICY_ANCHOR_FIELDS,
        "strategy_anchor": STRATEGY_ANCHOR_FIELDS,
        "frozen_schedule": SCHEDULE_FIELDS,
        "frozen_fault_schedule": FAULT_SCHEDULE_FIELDS,
        "trading_calendar": CALENDAR_FIELDS,
    }
    for role, document in documents.items():
        _exact(document, field_sets[role], "P1_FREEZER_ANCHOR_FIELDS_INVALID")
        _validate_seal(document, "P1_FREEZER_ANCHOR_SEAL_INVALID")
    anchor_refs = {
        role: {
            "path": "", "file_sha256": digest_bytes(canonical_bytes(document)),
            "body_sha256": document["body_sha256"],
        } for role, document in documents.items() if role in ANCHOR_ROLES
    }
    receipt = seal({
        "schema": BUNDLE_SCHEMA, "version": VERSION, "status": "FROZEN",
        "round": ROUND, "freeze_id": identity, "issued_at_ms": now_ms,
        "expires_at_ms": expiry, "campaign_id": campaign,
        "domain_id": domain, "producer": dict(producer),
        "production_mode": PRODUCTION_MODE, "boot_id": boot_id,
        "frozen_boottime_ns": boottime_ns,
        "source_baseline": reference(source_baseline),
        "source_manifest_sha256": source_manifest_sha,
        "source_producer_pins": source_producer_pins,
        "policy_sha256": policy_sha, "formal_policies": formal_records,
        "strategy_id": strategy_id, "strategy_version": strategy_version,
        "strategy_sha256": strategy_sha, "strategy_files": strategy_files,
        "trading_calendar": {
            "path": "",
            "file_sha256": digest_bytes(canonical_bytes(calendar_document)),
            "body_sha256": calendar_document["body_sha256"],
        },
        "calendar_id": calendar_id, "calendar_version": calendar_version,
        "calendar_source_sha256": calendar_source_sha,
        "declared_trading_days": days, "trading_timezone": trading_timezone,
        "trading_calendar_sha256": calendar_sha,
        "eligible_scheduled_at_ms": eligible_slots,
        "scheduled_decision_count": len(all_slots),
        "planned_faults": faults, "anchors": anchor_refs, **_boundary(),
    })
    return documents, receipt


def _rename_noreplace(parent: int, source: str, destination: str) -> None:
    function = getattr(_LIBC, "renameat2", None)
    _require(function is not None, "P1_FREEZER_RENAMEAT2_UNAVAILABLE")
    function.argtypes = (
        ctypes.c_int, ctypes.c_char_p, ctypes.c_int, ctypes.c_char_p,
        ctypes.c_uint)
    function.restype = ctypes.c_int
    ctypes.set_errno(0)
    if function(parent, os.fsencode(source), parent, os.fsencode(destination),
                RENAME_NOREPLACE) != 0:
        number = ctypes.get_errno()
        if number == errno.EEXIST:
            raise FreezeError("P1_FREEZER_BUNDLE_ALREADY_EXISTS")
        raise FreezeError("P1_FREEZER_BUNDLE_RENAME_FAILED")


def _write_at(parent: int, name: str, payload: bytes, uid: int, gid: int) -> None:
    descriptor = os.open(name, CREATE_FLAGS, 0o600, dir_fd=parent)
    try:
        os.fchmod(descriptor, 0o600)
        os.fchown(descriptor, uid, gid)
        offset = 0
        while offset < len(payload):
            count = os.write(descriptor, payload[offset:])
            _require(count > 0, "P1_FREEZER_OUTPUT_WRITE_FAILED")
            offset += count
        os.fsync(descriptor)
        metadata = os.fstat(descriptor)
        _require(stat.S_ISREG(metadata.st_mode) and metadata.st_nlink == 1 and
                 metadata.st_uid == uid and metadata.st_gid == gid and
                 stat.S_IMODE(metadata.st_mode) == 0o600 and
                 metadata.st_size == len(payload),
                 "P1_FREEZER_OUTPUT_METADATA_INVALID")
    finally:
        os.close(descriptor)


def publish_bundle(
    *, target: Path, documents: Mapping[str, dict[str, Any]],
    receipt: dict[str, Any], expected_uid: int, expected_gid: int,
) -> dict[str, Any]:
    target = canonical_path(target, "P1_FREEZER_OUTPUT_PATH_INVALID")
    _require(set(documents) == set(OUTPUT_NAMES),
             "P1_FREEZER_OUTPUT_SET_INVALID")
    parent, parent_ancestors = open_trusted_directory(
        target.parent, expected_uid=expected_uid, expected_gid=expected_gid,
        reason="P1_FREEZER_OUTPUT_ANCESTOR_UNTRUSTED")
    temporary = f".{target.name}.p1-freezer-{secrets.token_hex(16)}.tmp"
    temp_descriptor: int | None = None
    renamed = False
    try:
        try:
            os.stat(target.name, dir_fd=parent, follow_symlinks=False)
        except FileNotFoundError:
            pass
        else:
            raise FreezeError("P1_FREEZER_BUNDLE_ALREADY_EXISTS")
        os.mkdir(temporary, 0o700, dir_fd=parent)
        temp_descriptor = os.open(temporary, DIRECTORY_FLAGS, dir_fd=parent)
        os.fchmod(temp_descriptor, 0o700)
        os.fchown(temp_descriptor, expected_uid, expected_gid)
        final_receipt = dict(receipt)
        anchors: dict[str, dict[str, str]] = {}
        trading_calendar_ref: dict[str, str] | None = None
        for role, filename in OUTPUT_NAMES.items():
            payload = canonical_bytes(documents[role])
            _require(len(payload) <= MAXIMUM_OUTPUT_BYTES,
                     "P1_FREEZER_OUTPUT_TOO_LARGE")
            _write_at(temp_descriptor, filename, payload,
                      expected_uid, expected_gid)
            output_reference = {
                "path": str(target / filename),
                "file_sha256": digest_bytes(payload),
                "body_sha256": documents[role]["body_sha256"],
            }
            if role in ANCHOR_ROLES:
                anchors[role] = output_reference
            else:
                _require(role == "trading_calendar" and
                         trading_calendar_ref is None,
                         "P1_FREEZER_OUTPUT_SET_INVALID")
                trading_calendar_ref = output_reference
        _require(trading_calendar_ref is not None,
                 "P1_FREEZER_OUTPUT_SET_INVALID")
        body = dict(final_receipt)
        body.pop("body_sha256", None)
        body["anchors"] = anchors
        body["trading_calendar"] = trading_calendar_ref
        final_receipt = seal(body)
        validate_bundle_receipt(final_receipt)
        receipt_payload = canonical_bytes(final_receipt)
        _write_at(temp_descriptor, "freeze-bundle-receipt.json",
                  receipt_payload, expected_uid, expected_gid)
        os.fsync(temp_descriptor)
        rebound_parent, rebound_ancestors = open_trusted_directory(
            target.parent, expected_uid=expected_uid, expected_gid=expected_gid,
            reason="P1_FREEZER_OUTPUT_ANCESTOR_REBOUND")
        try:
            _require(parent_ancestors == rebound_ancestors,
                     "P1_FREEZER_OUTPUT_ANCESTOR_REBOUND")
        finally:
            os.close(rebound_parent)
        _rename_noreplace(parent, temporary, target.name)
        renamed = True
        os.fsync(parent)
    except FreezeError:
        raise
    except OSError as error:
        raise FreezeError("P1_FREEZER_OUTPUT_PUBLISH_FAILED") from error
    finally:
        if temp_descriptor is not None:
            os.close(temp_descriptor)
        if not renamed:
            try:
                if temp_descriptor is None:
                    temp_descriptor = os.open(
                        temporary, DIRECTORY_FLAGS, dir_fd=parent)
                for filename in (*OUTPUT_NAMES.values(),
                                 "freeze-bundle-receipt.json"):
                    try:
                        os.unlink(filename, dir_fd=temp_descriptor)
                    except FileNotFoundError:
                        pass
                os.rmdir(temporary, dir_fd=parent)
                os.fsync(parent)
            except OSError:
                pass
        os.close(parent)
    receipt_path = target / "freeze-bundle-receipt.json"
    committed = load_snapshot(
        receipt_path, expected_uid=expected_uid, expected_gid=expected_gid,
        sealed=True)
    validate_bundle_receipt(committed.document)
    for role, filename in OUTPUT_NAMES.items():
        anchor = load_snapshot(
            target / filename, expected_uid=expected_uid,
            expected_gid=expected_gid, sealed=True)
        expected_reference = (
            committed.document["anchors"][role]
            if role in ANCHOR_ROLES else
            committed.document["trading_calendar"])
        _require(reference(anchor) == expected_reference and
                 anchor.document == documents[role],
                 "P1_FREEZER_OUTPUT_POST_VERIFY_FAILED")
    return committed.document


def validate_bundle_receipt(value: dict[str, Any]) -> None:
    reason = "P1_FREEZER_BUNDLE_RECEIPT_INVALID"
    _exact(value, BUNDLE_FIELDS, reason)
    _validate_seal(value, reason)
    _reject_authority(value, reason)
    _require(value.get("schema") == BUNDLE_SCHEMA and
             value.get("version") == VERSION and value.get("status") == "FROZEN" and
             value.get("round") == ROUND and
             re.fullmatch(r"[0-9a-f]{32}", str(value.get("freeze_id", ""))) is not None and
             value.get("production_mode") == PRODUCTION_MODE and
             BOOT_ID.fullmatch(str(value.get("boot_id", ""))) is not None,
             reason)
    producer = _exact(value.get("producer"), PRODUCER_FIELDS, reason)
    _require(producer.get("path") == str(INSTALLED_EXECUTABLE), reason)
    _digest(producer.get("file_sha256"), reason)
    issued = _integer(value.get("issued_at_ms"), reason)
    expires = _integer(value.get("expires_at_ms"), reason, issued + 1)
    del expires
    for field in ("source_manifest_sha256", "policy_sha256",
                  "strategy_sha256", "trading_calendar_sha256",
                  "calendar_source_sha256"):
        _digest(value.get(field), reason)
    _identifier(value.get("campaign_id"), IDENTIFIER, reason)
    _identifier(value.get("domain_id"), DOMAIN, reason)
    _identifier(value.get("strategy_id"), IDENTIFIER, reason)
    _identifier(value.get("strategy_version"), IDENTIFIER, reason)
    _identifier(value.get("calendar_id"), IDENTIFIER, reason)
    _identifier(value.get("calendar_version"), IDENTIFIER, reason)
    _integer(value.get("frozen_boottime_ns"), reason)
    _exact(value.get("source_baseline"), REFERENCE_FIELDS, reason)
    _exact(value.get("trading_calendar"), REFERENCE_FIELDS, reason)
    producer_pins = value.get("source_producer_pins")
    _require(isinstance(producer_pins, list) and
             len(producer_pins) == len(SOURCE_PRODUCER_PATHS), reason)
    seen_roles: set[str] = set()
    for item in producer_pins:
        _exact(item, SOURCE_PRODUCER_PIN_FIELDS, reason)
        role = item.get("role")
        _require(type(role) is str and role in SOURCE_PRODUCER_PATHS and
                 role not in seen_roles, reason)
        seen_roles.add(role)
        source_path, installed_path = SOURCE_PRODUCER_PATHS[role]
        _require(item.get("source_path") == source_path and
                 item.get("installed_path") == installed_path, reason)
        _digest(item.get("file_sha256"), reason)
    _require(seen_roles == set(SOURCE_PRODUCER_PATHS) and
             producer_pins == sorted(
                 producer_pins, key=lambda item: item["role"]), reason)
    freezer_pin = next(item for item in producer_pins
                       if item["role"] == "campaign_freezer")
    _require(freezer_pin["file_sha256"] == producer["file_sha256"], reason)
    formal = value.get("formal_policies")
    _require(isinstance(formal, list) and bool(formal), reason)
    previous_teardown = 0
    for item in formal:
        _exact(item, FORMAL_REFERENCE_FIELDS, reason)
        _identifier(item.get("campaign_id"), IDENTIFIER, reason)
        for field in ("file_sha256", "body_sha256"):
            _digest(item.get(field), reason)
        _require(type(item.get("path")) is str and
                 Path(item["path"]).is_absolute(), reason)
        launcher_start = _integer(item.get("launcher_start_ms"), reason, 1)
        dispatch = _integer(item.get("launcher_dispatch_at_ms"), reason, 1)
        valid_after = _integer(item.get("valid_after_ms"), reason, 1)
        interval = _integer(item.get("slot_interval_ms"), reason, 1)
        maximum = _integer(item.get("maximum_iterations"), reason, 1)
        expiry = _integer(item.get("expires_at_ms"), reason, valid_after + 1)
        completion = _integer(
            item.get("launcher_completion_deadline_ms"), reason, expiry)
        projection = _integer(
            item.get("projection_deadline_ms"), reason, completion)
        teardown = _integer(
            item.get("teardown_deadline_ms"), reason, projection)
        _require(
            launcher_start == valid_after - LAUNCHER_WARMUP_MS and
            dispatch == launcher_start - LAUNCHER_EARLY_START_LEAD_MS and
            interval == POLICY_SLOT_INTERVAL_MS and
            maximum == POLICY_MAXIMUM_ITERATIONS and
            expiry == valid_after + interval * maximum and
            completion == expiry + MAXIMUM_LAUNCH_LATENESS_MS and
            projection == expiry + POST_FORMAL_PROJECTION_GUARD_MS and
            teardown == expiry + POST_FORMAL_TEARDOWN_GUARD_MS and
            (previous_teardown == 0 or
             valid_after == required_valid_after_after_teardown(
                 previous_teardown, interval)), reason)
        previous_teardown = teardown
    strategy_files = value.get("strategy_files")
    _require(isinstance(strategy_files, list) and
             {item.get("role") for item in strategy_files} ==
             {"config", *RUNTIME_FILES}, reason)
    for item in strategy_files:
        _exact(item, STRATEGY_FILE_FIELDS, reason)
    days = value.get("declared_trading_days")
    eligible = value.get("eligible_scheduled_at_ms")
    _require(isinstance(days, list) and
             MINIMUM_TRADING_DAYS <= len(days) <= MAXIMUM_TRADING_DAYS and
             days == sorted(set(days)) and isinstance(eligible, list) and
             len(eligible) >= MINIMUM_ELIGIBLE_DECISIONS and
             eligible == sorted(set(eligible)) and
             type(value.get("scheduled_decision_count")) is int and
             value["scheduled_decision_count"] >= len(eligible), reason)
    faults = value.get("planned_faults")
    _require(isinstance(faults, list) and len(faults) == len(FAULT_TYPES), reason)
    formal_by_id = {item["campaign_id"]: item for item in formal}
    frozen_at_ms = _integer(value.get("issued_at_ms"), reason)
    frozen_boottime_ns = _integer(value.get("frozen_boottime_ns"), reason)
    fault_types: list[str] = []
    for item in faults:
        fault = _exact(item, PLANNED_FAULT_FIELDS, reason)
        fault_type = fault.get("fault_type")
        bound_formal = formal_by_id.get(fault.get("formal_campaign_id"))
        injection = _integer(
            fault.get("inject_at_boottime_ns"), reason, frozen_boottime_ns)
        lateness = _integer(
            fault.get("maximum_injection_lateness_ns"), reason)
        recovery = _integer(fault.get("maximum_recovery_ns"), reason)
        _require(
            fault_type in FAULT_TYPES and
            fault.get("target_id") == FAULT_TARGET_IDS[fault_type] and
            bound_formal is not None and
            injection >= frozen_boottime_ns + (
                bound_formal["valid_after_ms"] - frozen_at_ms) * 1_000_000 and
            injection + lateness + recovery < frozen_boottime_ns + (
                bound_formal["expires_at_ms"] - frozen_at_ms) * 1_000_000,
            reason)
        fault_types.append(fault_type)
    _require(set(fault_types) == set(FAULT_TYPES) and
             len(fault_types) == len(set(fault_types)), reason)
    anchors = value.get("anchors")
    _require(isinstance(anchors, dict) and
             set(anchors) == set(ANCHOR_ROLES), reason)
    for item in anchors.values():
        _exact(item, REFERENCE_FIELDS, reason)


def freeze_campaign(
    *, source_baseline_path: Path, expected_source_baseline_file_sha256: str,
    strategy_config_path: Path, strategy_runtime_directory: Path,
    formal_policy_paths: Sequence[Path],
    campaign_id: str, domain_id: str,
    trading_timezone: str, independent_auditor_id: str, output_bundle: Path,
) -> dict[str, Any]:
    _require(os.geteuid() == ROOT_UID and os.getegid() == ROOT_GID,
             "P1_FREEZER_ROOT_REQUIRED")
    producer = bind_executing_image()
    now_ms = time.time_ns() // 1_000_000
    boottime_ns = time.clock_gettime_ns(time.CLOCK_BOOTTIME)
    try:
        boot_id = Path("/proc/sys/kernel/random/boot_id").read_text(
            encoding="ascii").strip()
    except OSError as error:
        raise FreezeError("P1_FREEZER_BOOT_ID_UNAVAILABLE") from error
    inputs: list[Snapshot] = []
    source = load_snapshot(
        source_baseline_path, expected_uid=ROOT_UID, expected_gid=ROOT_GID,
        sealed=False, modes=frozenset({0o400, 0o600, 0o644}))
    inputs.append(source)
    strategy = load_snapshot(
        strategy_config_path, expected_uid=ROOT_UID, expected_gid=ROOT_GID,
        sealed=False, modes=frozenset({0o400, 0o600, 0o644}))
    inputs.append(strategy)
    runtime: dict[str, Snapshot] = {}
    for role, filename in RUNTIME_FILES.items():
        item = load_snapshot(
            strategy_runtime_directory / filename, expected_uid=ROOT_UID,
            expected_gid=ROOT_GID, sealed=False,
            modes=frozenset({0o400, 0o500, 0o600, 0o644, 0o755}))
        runtime[role] = item
        inputs.append(item)
    policies = [load_snapshot(
        path, expected_uid=ROOT_UID, expected_gid=ROOT_GID, sealed=True)
        for path in formal_policy_paths]
    inputs.extend(policies)
    _require(len({item.path for item in inputs}) == len(inputs),
             "P1_FREEZER_INPUT_ALIAS")
    documents, receipt = build_bundle(
        source_baseline=source, strategy_config=strategy,
        strategy_runtime=runtime, formal_policies=policies,
        expected_source_baseline_file_sha256=
            expected_source_baseline_file_sha256,
        campaign_id=campaign_id, domain_id=domain_id,
        trading_timezone=trading_timezone,
        independent_auditor_id=independent_auditor_id,
        producer=producer.reference, now_ms=now_ms, boottime_ns=boottime_ns,
        boot_id=boot_id)
    producer.reopen()
    for item in inputs:
        payload, metadata = secure_read(
            item.path, expected_uid=ROOT_UID, expected_gid=ROOT_GID,
            modes=frozenset({stat.S_IMODE(item.metadata.st_mode)}))
        _require(payload == item.payload and
                 file_identity(metadata) == file_identity(item.metadata),
                 "P1_FREEZER_INPUT_DRIFT")
    result = publish_bundle(
        target=output_bundle, documents=documents, receipt=receipt,
        expected_uid=ROOT_UID, expected_gid=ROOT_GID)
    producer.reopen()
    return result


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run", action="store_true")
    parser.add_argument("--source-baseline", required=True, type=Path)
    parser.add_argument("--expected-source-baseline-file-sha256", required=True)
    parser.add_argument("--strategy-config", required=True, type=Path)
    parser.add_argument("--strategy-runtime-directory", required=True, type=Path)
    parser.add_argument("--formal-policy", required=True, action="append", type=Path)
    parser.add_argument("--campaign-id", required=True)
    parser.add_argument("--domain-id", required=True)
    parser.add_argument("--trading-timezone", required=True)
    parser.add_argument("--independent-auditor-id", required=True)
    parser.add_argument("--output-bundle", required=True, type=Path)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    arguments = _parser().parse_args(argv)
    try:
        _require(arguments.run, "P1_FREEZER_EXPLICIT_RUN_REQUIRED")
        receipt = freeze_campaign(
            source_baseline_path=arguments.source_baseline,
            expected_source_baseline_file_sha256=
                arguments.expected_source_baseline_file_sha256,
            strategy_config_path=arguments.strategy_config,
            strategy_runtime_directory=arguments.strategy_runtime_directory,
            formal_policy_paths=arguments.formal_policy,
            campaign_id=arguments.campaign_id, domain_id=arguments.domain_id,
            trading_timezone=arguments.trading_timezone,
            independent_auditor_id=arguments.independent_auditor_id,
            output_bundle=arguments.output_bundle)
    except FreezeError as error:
        print("hepta_p1_safety_soak_campaign_freezer: FAIL " + error.reason,
              file=sys.stderr)
        return 2
    sys.stdout.buffer.write(canonical_bytes(receipt))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
