#!/usr/bin/env python3

"""Narrow root controller for one finite P1 SHADOW/WATCH campaign.

This controller has no PAPER, LIVE, order, position, or broker mutation path.
It only:

* starts the alpha SHADOW collector one-shot on a 10.000 second absolute
  monotonic cadence;
* rotates the already-provisioned WATCH lease every 3000 seconds through the
  root-owned custodian, using the exact generation committed by the preceding
  rotation; and
* starts one pinned official-source capture 180 seconds before every fixed
  15-minute decision slot and requires its receipt before that slot.

Any missed cadence, failed command, malformed receipt, permission-bearing
receipt, or signal is terminal.  Once operation has begun, every exit path
asks the custodian to close the WATCH transaction.
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
import re
import signal
import stat
import subprocess
import sys
import time
from typing import Any, Callable, Protocol

from hepta_agent_trust_domain import (
    TrustDomainRuntimeError,
    read_alpha_gateway_process_identity,
    read_alpha_gateway_process_profile,
    read_alpha_gateway_profile,
    read_alpha_gateway_socket,
)


ROOT_UID = 0
ROOT_GID = 0
SYSTEMCTL = "/usr/bin/systemctl"
CUSTODIAN = "/usr/libexec/hepta-shadow-watch-custodian"
CAPTURE_HELPER = "/usr/libexec/hepta-official-source-capture"
COLLECTOR_UNIT = "hepta-shadow-watch-collector@alpha.service"
REQUIRED_DOMAIN_CONFIG = Path("/etc/heptatrader/trust-domains/alpha.json")
REQUIRED_GATEWAY_PROFILE = Path("/etc/heptatrader/trust-domains/alpha.env")
REQUIRED_EVIDENCE_ROOT = Path("/var/lib/hepta/market-evidence")
REQUIRED_EXPORT_ROOT = Path("/var/lib/hepta/shadow-observation")
REQUIRED_BOOT_ID = Path("/proc/sys/kernel/random/boot_id")
BOOT_ID_MAXIMUM_BYTES = 128
REQUIRED_AUDIT_JOURNAL = Path(
    "/var/lib/hepta-tool-gateway-alpha/session-audit.jsonl")
REQUIRED_GATEWAY_UNIT = "hepta-tool-gateway@alpha.service"
REQUIRED_GATEWAY_SOCKET = Path("/run/hepta-agent-alpha/tools.sock")
REQUIRED_WATCH_SNAPSHOT = Path(
    "/var/lib/hepta-shadow-watch-alpha/private/snapshot.json")
REQUIRED_RUNTIME_FILES = {
    "collector_sha256": Path("/usr/libexec/hepta-shadow-watch-collector"),
    "exporter_sha256": Path("/usr/libexec/hepta-shadow-watch-exporter"),
    "heptactl_sha256": Path("/usr/bin/heptactl"),
    "gateway_sha256": Path("/usr/libexec/hepta-tool-gatewayd"),
    "custodian_sha256": Path("/usr/libexec/hepta-shadow-watch-custodian"),
    "observer_sha256": Path(
        "/usr/libexec/hepta_bounded_shadow_observer.py"),
}
READER_UNIT = re.compile(r"^hepta-p1-shadow-reader-round[0-9]+\.service$")
CAMPAIGN_ROUND = re.compile(r"(?:^|-)round([0-9]+)(?:-|$)")
ADMISSION_MAXIMUM_AGE_MS = 60_000
MAXIMUM_READER_HEARTBEAT_AGE_MS = 15_000
FINAL_ACKNOWLEDGEMENT_TIMEOUT_NS = 30_000_000_000
FINAL_ACKNOWLEDGEMENT_POLL_NS = 100_000_000

COLLECTOR_INTERVAL_NS = 10_000_000_000
ROTATION_INTERVAL_NS = 3_000_000_000_000
ROTATION_TTL_SECONDS = 3600
DECISION_INTERVAL_MS = 120_000
FORMAL_MAXIMUM_ITERATIONS = 241
FORMAL_MAXIMUM_LATENESS_MS = 60_000
FORMAL_HISTORY_WARMUP_MS = 210 * 60 * 1000
FORMAL_FIRST_COLLECTION_TOLERANCE_MS = 60 * 1000
MAX_START_JITTER_NS = 1_000_000_000
COLLECTOR_TIMEOUT_SECONDS = 9.0
CUSTODIAN_TIMEOUT_SECONDS = 30.0
MAX_JSON_BYTES = 65_536
MAX_COMMAND_OUTPUT = 65_536
CAPTURE_TERMINATE_GRACE_SECONDS = 2.0
LOAD_PROBE_REQUIRED_RUNS = 91
LOAD_PROBE_CADENCE_MS = 10_000
LOAD_PROBE_MAXIMUM_JITTER_MS = 1_000
LOAD_PROBE_MAXIMUM_EXPORT_COMMIT_MS = 9_000
LOAD_PROBE_MAXIMUM_COLLECTOR_NS = 8_500_000_000

IDENTIFIER = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
DIGEST = re.compile(r"^sha256:[0-9a-f]{64}$")
PERMISSION_FIELDS = frozenset({
    "paper_authorized",
    "live_authorized",
    "mutation_authorized",
    "mutation_attempted",
    "direct_broker_access",
})
ADMISSION_FIELDS = frozenset({
    "schema", "version", "status", "campaign_id",
    "prospective_campaign_id", "prospective_policy_path",
    "authority_marker_path", "validated_at_ms",
    "host_receipt_body_sha256",
    "observer_controller_status_body_sha256",
    "observer_state_body_sha256", "history_head_body_sha256",
    "probe_execution_service_epoch",
    "probe_execution_service_fencing_generation",
    "probe_first_collection_started_at_ms", "probe_first_exported_at_ms",
    "probe_first_record_sha256", "probe_first_snapshot_body_sha256",
    "probe_last_collection_started_at_ms", "probe_last_exported_at_ms",
    "probe_last_record_sha256", "probe_last_snapshot_body_sha256",
    "probe_history_record_bytes", "probe_audit_cursor_sequence",
    "probe_audit_expected_previous_sha256", "sample_count",
    "collection_cadence_ms", "maximum_collection_jitter_ms",
    "missed_sample_count", "missed_decision_count", "environment",
    "paper_authorized", "live_authorized", "mutation_authorized",
    "direct_broker_access", "body_sha256",
})


class ControllerError(RuntimeError):
    """Stable fail-closed controller error."""


class ControllerSignal(ControllerError):
    """A terminal signal observed while authority may be active."""

    def __init__(self, signum: int) -> None:
        super().__init__(f"P1_SIGNAL_{signum}")
        self.signum = signum


def _require(condition: bool, reason: str) -> None:
    if not condition:
        raise ControllerError(reason)


def _pairs(values: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in values:
        _require(key not in result, "P1_DUPLICATE_JSON_KEY")
        result[key] = value
    return result


def _strict_json(data: str | bytes, reason: str) -> Any:
    try:
        return json.loads(
            data,
            object_pairs_hook=_pairs,
            parse_constant=lambda _value: (_ for _ in ()).throw(
                ControllerError(reason)),
        )
    except (json.JSONDecodeError, UnicodeError, ValueError) as error:
        raise ControllerError(reason) from error


def _canonical(value: object) -> bytes:
    try:
        return (
            json.dumps(
                value,
                ensure_ascii=True,
                allow_nan=False,
                separators=(",", ":"),
                sort_keys=True,
            )
            + "\n"
        ).encode("ascii")
    except (TypeError, ValueError, UnicodeError) as error:
        raise ControllerError("P1_RESULT_CANONICALIZATION_FAILED") from error


def _reject_permission_surface(value: Any) -> None:
    if isinstance(value, dict):
        for key, child in value.items():
            if key in PERMISSION_FIELDS:
                _require(child is False, "P1_PERMISSION_FLAG_NOT_FALSE")
            _reject_permission_surface(child)
    elif isinstance(value, list):
        for child in value:
            _reject_permission_surface(child)


def _flags_are_explicitly_false(value: dict[str, Any], fields: tuple[str, ...]) -> None:
    _require(
        all(value.get(field) is False for field in fields),
        "P1_PERMISSION_FLAGS_INVALID",
    )
    _reject_permission_surface(value)


def _sha256_file(path: Path) -> str:
    return "sha256:" + hashlib.sha256(
        _secure_read(path, "P1_HELPER_FILE_INVALID", 64 * 1024 * 1024)
    ).hexdigest()


def _secure_read(
    path: Path,
    reason: str,
    maximum_bytes: int,
    *,
    expected_uid: int | None = None,
    expected_gid: int | None = None,
    allowed_modes: frozenset[int] | None = None,
) -> bytes:
    """Read and validate one path through one O_NOFOLLOW descriptor."""

    flags = os.O_RDONLY | os.O_CLOEXEC
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    try:
        descriptor = os.open(path, flags)
    except OSError as error:
        raise ControllerError(reason) from error
    try:
        metadata = os.fstat(descriptor)
        _require(
            stat.S_ISREG(metadata.st_mode) and
            metadata.st_nlink == 1 and
            1 <= metadata.st_size <= maximum_bytes,
            reason,
        )
        _require(
            (expected_uid is None or metadata.st_uid == expected_uid) and
            (expected_gid is None or metadata.st_gid == expected_gid) and
            (allowed_modes is None or
             stat.S_IMODE(metadata.st_mode) in allowed_modes),
            reason,
        )
        chunks: list[bytes] = []
        remaining = metadata.st_size
        while remaining:
            chunk = os.read(descriptor, min(remaining, 1024 * 1024))
            _require(bool(chunk), reason)
            chunks.append(chunk)
            remaining -= len(chunk)
        _require(os.read(descriptor, 1) == b"", reason)
        after = os.fstat(descriptor)
        _require(
            (metadata.st_dev, metadata.st_ino, metadata.st_size,
             metadata.st_mtime_ns, metadata.st_ctime_ns) ==
            (after.st_dev, after.st_ino, after.st_size,
             after.st_mtime_ns, after.st_ctime_ns),
            reason,
        )
        return b"".join(chunks)
    except OSError as error:
        raise ControllerError(reason) from error
    finally:
        os.close(descriptor)


def _read_boot_id(path: Path, reason: str) -> str:
    """Read the canonical Linux boot ID through a rebound anchored path."""

    _require(path == REQUIRED_BOOT_ID, reason)
    directory_flags = os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC
    file_flags = os.O_RDONLY | os.O_NONBLOCK | os.O_CLOEXEC
    no_follow = getattr(os, "O_NOFOLLOW", None)
    _require(no_follow is not None, reason)
    directory_flags |= no_follow
    file_flags |= no_follow

    def stable(metadata: os.stat_result) -> tuple[int, ...]:
        return (
            metadata.st_dev, metadata.st_ino, metadata.st_mode,
            metadata.st_nlink, metadata.st_uid, metadata.st_gid,
            metadata.st_size, metadata.st_mtime_ns, metadata.st_ctime_ns,
        )

    def validate_directory(metadata: os.stat_result) -> None:
        _require(
            stat.S_ISDIR(metadata.st_mode) and
            metadata.st_uid == ROOT_UID and metadata.st_gid == ROOT_GID and
            stat.S_IMODE(metadata.st_mode) & 0o022 == 0,
            reason,
        )

    def open_parent() -> int:
        current_fd: int | None = None
        try:
            current_fd = os.open("/", directory_flags)
            for component in path.parent.parts[1:]:
                validate_directory(os.fstat(current_fd))
                next_fd = os.open(component, directory_flags, dir_fd=current_fd)
                previous_fd = current_fd
                current_fd = next_fd
                os.close(previous_fd)
            validate_directory(os.fstat(current_fd))
            return current_fd
        except Exception:
            if current_fd is not None:
                try:
                    os.close(current_fd)
                except OSError:
                    pass
            raise

    def read_bounded(descriptor: int) -> bytes:
        chunks: list[bytes] = []
        total = 0
        while True:
            chunk = os.read(
                descriptor,
                min(BOOT_ID_MAXIMUM_BYTES - total + 1, 1024 * 1024),
            )
            if not chunk:
                break
            chunks.append(chunk)
            total += len(chunk)
            _require(total <= BOOT_ID_MAXIMUM_BYTES, reason)
        return b"".join(chunks)

    parent_fd: int | None = None
    descriptor: int | None = None
    rebound_parent_fd: int | None = None
    rebound_fd: int | None = None
    completed = False
    try:
        parent_fd = open_parent()
        parent_identity = stable(os.fstat(parent_fd))
        before_entry = os.stat(
            path.name, dir_fd=parent_fd, follow_symlinks=False)
        descriptor = os.open(path.name, file_flags, dir_fd=parent_fd)
        opened = os.fstat(descriptor)
        _require(
            stable(before_entry) == stable(opened) and
            stat.S_ISREG(opened.st_mode) and opened.st_uid == ROOT_UID and
            opened.st_gid == ROOT_GID and
            stat.S_IMODE(opened.st_mode) == 0o444 and
            opened.st_nlink == 1 and opened.st_size == 0 and
            opened.st_dev == os.fstat(parent_fd).st_dev,
            reason,
        )
        contents = read_bounded(descriptor)
        after = os.fstat(descriptor)
        after_entry = os.stat(
            path.name, dir_fd=parent_fd, follow_symlinks=False)
        _require(
            stable(opened) == stable(after) == stable(after_entry) and
            parent_identity == stable(os.fstat(parent_fd)),
            reason,
        )

        rebound_parent_fd = open_parent()
        rebound_parent_identity = stable(os.fstat(rebound_parent_fd))
        rebound_entry = os.stat(
            path.name, dir_fd=rebound_parent_fd, follow_symlinks=False)
        rebound_fd = os.open(
            path.name, file_flags, dir_fd=rebound_parent_fd)
        rebound_opened = os.fstat(rebound_fd)
        _require(
            parent_identity == rebound_parent_identity and
            stable(opened) == stable(rebound_entry) == stable(rebound_opened),
            reason,
        )
        rebound_contents = read_bounded(rebound_fd)
        rebound_after = os.fstat(rebound_fd)
        rebound_after_entry = os.stat(
            path.name, dir_fd=rebound_parent_fd, follow_symlinks=False)
        _require(
            contents == rebound_contents and
            stable(rebound_opened) == stable(rebound_after) ==
            stable(rebound_after_entry) and
            rebound_parent_identity == stable(os.fstat(rebound_parent_fd)),
            reason,
        )
        _require(
            len(contents) == 37 and contents.endswith(b"\n"), reason)
        boot_id = contents[:-1].decode("ascii")
        _require(
            re.fullmatch(
                r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-"
                r"[0-9a-f]{4}-[0-9a-f]{12}",
                boot_id,
            ) is not None,
            reason,
        )
        completed = True
        return boot_id
    except ControllerError:
        raise
    except (OSError, UnicodeError) as error:
        raise ControllerError(reason) from error
    finally:
        cleanup_error: OSError | None = None
        for opened_fd in (rebound_fd, rebound_parent_fd, descriptor, parent_fd):
            if opened_fd is not None:
                try:
                    os.close(opened_fd)
                except OSError as error:
                    if cleanup_error is None:
                        cleanup_error = error
        if cleanup_error is not None and completed:
            raise ControllerError(reason) from cleanup_error


def _secure_document(
    path: Path,
    label: str,
    *,
    root_owned: bool = False,
) -> tuple[dict[str, Any], bytes]:
    contents = _secure_read(
        path,
        f"{label}_FILE_INVALID",
        MAX_JSON_BYTES,
        expected_uid=ROOT_UID if root_owned else None,
        expected_gid=ROOT_GID if root_owned else None,
        allowed_modes=(frozenset({0o600, 0o640, 0o644})
                       if root_owned else None),
    )
    document = _strict_json(contents, f"{label}_JSON_INVALID")
    _require(
        isinstance(document, dict) and _canonical(document) == contents,
        f"{label}_CANONICAL_INVALID",
    )
    body = dict(document)
    claimed = body.pop("body_sha256", None)
    _require(
        isinstance(claimed, str) and DIGEST.fullmatch(claimed) is not None and
        claimed == "sha256:" + hashlib.sha256(_canonical(body)).hexdigest(),
        f"{label}_DIGEST_INVALID",
    )
    return document, contents


def _secure_reader_document(
    path: Path,
    label: str,
    config: "Configuration",
) -> tuple[dict[str, Any], bytes]:
    """Read one canonical uid-1000 reader artifact without following its file."""

    contents = _secure_read(
        path,
        f"{label}_FILE_INVALID",
        MAX_JSON_BYTES,
        expected_uid=config.reader_uid,
        expected_gid=config.reader_gid,
        allowed_modes=frozenset({0o600}),
    )
    document = _strict_json(contents, f"{label}_JSON_INVALID")
    _require(
        isinstance(document, dict) and _canonical(document) == contents,
        f"{label}_CANONICAL_INVALID",
    )
    body = dict(document)
    claimed = body.pop("body_sha256", None)
    _require(
        isinstance(claimed, str) and DIGEST.fullmatch(claimed) is not None and
        claimed == "sha256:" + hashlib.sha256(_canonical(body)).hexdigest(),
        f"{label}_DIGEST_INVALID",
    )
    return document, contents


def _load_probe_environment_binding() -> dict[str, Any]:
    try:
        boot_id = _read_boot_id(
            REQUIRED_BOOT_ID, "P1_LOAD_PROBE_BOOT_ID_INVALID")
        _require(
            re.fullmatch(
                r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-"
                r"[0-9a-f]{4}-[0-9a-f]{12}",
                boot_id,
            ) is not None,
            "P1_LOAD_PROBE_BOOT_ID_INVALID",
        )
        audit_flags = os.O_RDONLY | os.O_CLOEXEC
        if hasattr(os, "O_NOFOLLOW"):
            audit_flags |= os.O_NOFOLLOW
        audit_descriptor = os.open(REQUIRED_AUDIT_JOURNAL, audit_flags)
        try:
            audit_metadata = os.fstat(audit_descriptor)
        finally:
            os.close(audit_descriptor)
        _require(
            stat.S_ISREG(audit_metadata.st_mode) and
            audit_metadata.st_nlink == 1,
            "P1_LOAD_PROBE_AUDIT_JOURNAL_INVALID",
        )
    except (OSError, UnicodeError) as error:
        raise ControllerError("P1_LOAD_PROBE_ENVIRONMENT_INVALID") from error
    binding: dict[str, Any] = {
        "boot_id": boot_id,
        "audit_journal_device": audit_metadata.st_dev,
        "audit_journal_inode": audit_metadata.st_ino,
    }
    for field, path in REQUIRED_RUNTIME_FILES.items():
        binding[field] = _sha256_file(path)
    binding["host_controller_sha256"] = _sha256_file(Path(__file__))
    binding["domain_config_sha256"] = _sha256_file(REQUIRED_DOMAIN_CONFIG)
    try:
        profile_before = read_alpha_gateway_profile(REQUIRED_GATEWAY_PROFILE)
    except TrustDomainRuntimeError as error:
        raise ControllerError("P1_GATEWAY_PROFILE_INVALID") from error
    binding["gateway_profile_sha256"] = (
        "sha256:" + hashlib.sha256(profile_before.raw).hexdigest())
    binding.update(_live_gateway_identity(profile_before))
    return binding


def _live_gateway_identity(profile_before: Any) -> dict[str, Any]:
    def status() -> dict[str, str]:
        completed = CommandExecutor._run(
            [
                SYSTEMCTL, "show", "--no-pager",
                "--property=ActiveState", "--property=SubState",
                "--property=InvocationID", "--property=MainPID",
                "--property=ExecMainStartTimestampMonotonic",
                REQUIRED_GATEWAY_UNIT,
            ],
            5.0,
        )
        parsed: dict[str, str] = {}
        for line in completed.stdout.splitlines():
            key, separator, value = line.partition("=")
            _require(separator == "=" and key not in parsed,
                     "P1_GATEWAY_IDENTITY_INVALID")
            parsed[key] = value
        _require(
            completed.returncode == 0 and completed.stderr == "" and
            parsed.get("ActiveState") == "active" and
            parsed.get("SubState") == "running" and
            re.fullmatch(
                r"[0-9a-f]{32}", parsed.get("InvocationID", "")) is not None and
            parsed.get("MainPID", "").isdigit() and
            int(parsed["MainPID"]) > 1 and
            parsed.get("ExecMainStartTimestampMonotonic", "").isdigit() and
            int(parsed["ExecMainStartTimestampMonotonic"]) > 0,
            "P1_GATEWAY_IDENTITY_INVALID",
        )
        return parsed

    parsed = status()
    try:
        process_profile = read_alpha_gateway_process_profile(
            int(parsed["MainPID"]))
    except TrustDomainRuntimeError as error:
        raise ControllerError("P1_GATEWAY_PROCESS_PROFILE_INVALID") from error
    try:
        socket_before = read_alpha_gateway_socket(REQUIRED_GATEWAY_SOCKET)
        profile_after = read_alpha_gateway_profile(REQUIRED_GATEWAY_PROFILE)
        parsed_after = status()
        process_after = read_alpha_gateway_process_identity(
            int(parsed["MainPID"]))
        socket_after = read_alpha_gateway_socket(REQUIRED_GATEWAY_SOCKET)
    except TrustDomainRuntimeError as error:
        raise ControllerError("P1_GATEWAY_REBIND_INVALID") from error
    _require(
        profile_before == profile_after and parsed_after == parsed and
        process_profile.pid_directory_metadata ==
        process_after.pid_directory_metadata and
        process_profile.starttime_ticks == process_after.starttime_ticks and
        socket_before == socket_after,
        "P1_GATEWAY_IDENTITY_CHANGED",
    )
    return {
        "gateway_invocation_id": parsed["InvocationID"],
        "gateway_main_pid": int(parsed["MainPID"]),
        "gateway_exec_main_start_timestamp_monotonic_us":
            int(parsed["ExecMainStartTimestampMonotonic"]),
        "gateway_socket_device": socket_before.metadata[0],
        "gateway_socket_inode": socket_before.metadata[1],
        "gateway_process_profile_sha256":
            "sha256:" + hashlib.sha256(
                process_profile.canonical_projection).hexdigest(),
    }


def _seal_document(body: dict[str, Any]) -> dict[str, Any]:
    body_sha256 = "sha256:" + hashlib.sha256(_canonical(body)).hexdigest()
    return {**body, "body_sha256": body_sha256}


def _write_root_exclusive(path: Path, document: dict[str, Any]) -> None:
    """Create one durable root-only receipt without path traversal/races."""

    _require(path.is_absolute(), "P1_LOAD_PROBE_RECEIPT_PATH_INVALID")
    parent = path.parent
    try:
        parent_flags = os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC
        if hasattr(os, "O_NOFOLLOW"):
            parent_flags |= os.O_NOFOLLOW
        parent_fd = os.open(parent, parent_flags)
        metadata = os.fstat(parent_fd)
    except OSError as error:
        raise ControllerError("P1_LOAD_PROBE_RECEIPT_DIRECTORY_INVALID") from error
    try:
        _require(
            stat.S_ISDIR(metadata.st_mode) and metadata.st_uid == ROOT_UID and
            metadata.st_gid == ROOT_GID and stat.S_IMODE(metadata.st_mode) == 0o700,
            "P1_LOAD_PROBE_RECEIPT_DIRECTORY_INVALID",
        )
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_CLOEXEC
        if hasattr(os, "O_NOFOLLOW"):
            flags |= os.O_NOFOLLOW
        descriptor = os.open(path.name, flags, 0o600, dir_fd=parent_fd)
        try:
            os.fchmod(descriptor, 0o600)
            contents = _canonical(document)
            offset = 0
            while offset < len(contents):
                offset += os.write(descriptor, contents[offset:])
            os.fsync(descriptor)
            written = os.fstat(descriptor)
            _require(
                stat.S_ISREG(written.st_mode) and written.st_uid == ROOT_UID and
                written.st_gid == ROOT_GID and written.st_nlink == 1 and
                stat.S_IMODE(written.st_mode) == 0o600 and
                written.st_size == len(contents),
                "P1_LOAD_PROBE_RECEIPT_FILE_INVALID",
            )
        finally:
            os.close(descriptor)
        os.fsync(parent_fd)
    except FileExistsError as error:
        raise ControllerError("P1_LOAD_PROBE_RECEIPT_ALREADY_EXISTS") from error
    except OSError as error:
        raise ControllerError("P1_LOAD_PROBE_RECEIPT_WRITE_FAILED") from error
    finally:
        os.close(parent_fd)


def _execution_binding(snapshot: dict[str, Any]) -> tuple[str, int]:
    reads = snapshot.get("reads")
    health = reads.get("system.get_health") if isinstance(reads, dict) else None
    epoch = health.get("execution_service_epoch") if isinstance(health, dict) else None
    fencing = (
        health.get("execution_service_fencing_generation")
        if isinstance(health, dict) else None
    )
    _require(
        isinstance(epoch, str) and 1 <= len(epoch) <= 256 and
        type(fencing) is int and 1 <= fencing < (1 << 64),
        "P1_ADMISSION_SNAPSHOT_EXECUTION_BINDING_INVALID",
    )
    return epoch, fencing


def _is_digest(value: Any) -> bool:
    return isinstance(value, str) and DIGEST.fullmatch(value) is not None


def _validate_formal_first_collection(
    config: "Configuration",
    snapshot: dict[str, Any],
) -> None:
    warmup_start_ms = config.valid_after_ms - FORMAL_HISTORY_WARMUP_MS
    first_collection_started_at_ms = snapshot.get(
        "collection_started_at_ms")
    _require(
        type(first_collection_started_at_ms) is int and
        warmup_start_ms <= first_collection_started_at_ms <=
        warmup_start_ms + FORMAL_FIRST_COLLECTION_TOLERANCE_MS,
        "P1_FORMAL_FIRST_COLLECTION_WINDOW_INVALID",
    )


def _validate_admission_payload(
    admission: dict[str, Any],
    *,
    config: "Configuration",
    expected_environment: dict[str, Any],
    now_ms: int,
) -> tuple[int, str, int]:
    validated_at_ms = admission.get("validated_at_ms")
    epoch = admission.get("probe_execution_service_epoch")
    fencing = admission.get("probe_execution_service_fencing_generation")
    first_started = admission.get("probe_first_collection_started_at_ms")
    first_exported = admission.get("probe_first_exported_at_ms")
    last_started = admission.get("probe_last_collection_started_at_ms")
    last_exported = admission.get("probe_last_exported_at_ms")
    digest_fields = (
        "host_receipt_body_sha256",
        "observer_controller_status_body_sha256",
        "observer_state_body_sha256",
        "history_head_body_sha256",
        "probe_first_record_sha256",
        "probe_first_snapshot_body_sha256",
        "probe_last_record_sha256",
        "probe_last_snapshot_body_sha256",
        "probe_audit_expected_previous_sha256",
    )
    _require(
        set(admission) == ADMISSION_FIELDS and
        admission.get("schema") ==
        "hepta.p1-shadow-load-probe-admission-receipt.v1" and
        type(admission.get("version")) is int and
        admission.get("version") == 1 and admission.get("status") == "GO" and
        isinstance(admission.get("campaign_id"), str) and
        IDENTIFIER.fullmatch(admission["campaign_id"]) is not None and
        admission["campaign_id"] != config.campaign_id and
        admission.get("prospective_campaign_id") == config.campaign_id and
        admission.get("prospective_policy_path") == str(config.policy_path) and
        admission.get("authority_marker_path") ==
        str(config.authority_marker_path) and
        admission.get("environment") == expected_environment and
        type(validated_at_ms) is int and
        0 <= now_ms - validated_at_ms <= ADMISSION_MAXIMUM_AGE_MS and
        isinstance(epoch, str) and 1 <= len(epoch) <= 256 and
        type(fencing) is int and 1 <= fencing < (1 << 64) and
        all(_is_digest(admission.get(field)) for field in digest_fields) and
        admission.get("probe_audit_expected_previous_sha256") ==
        admission.get("probe_last_record_sha256") and
        type(first_started) is int and type(first_exported) is int and
        type(last_started) is int and type(last_exported) is int and
        0 < first_started <= first_exported < last_started <= last_exported and
        first_exported - first_started <=
        LOAD_PROBE_MAXIMUM_EXPORT_COMMIT_MS and
        last_exported - last_started <=
        LOAD_PROBE_MAXIMUM_EXPORT_COMMIT_MS and
        last_exported <= validated_at_ms and
        abs(last_started - (
            first_started +
            (LOAD_PROBE_REQUIRED_RUNS - 1) * LOAD_PROBE_CADENCE_MS)) <=
        LOAD_PROBE_MAXIMUM_JITTER_MS and
        last_started <= last_exported and
        admission.get("sample_count") == LOAD_PROBE_REQUIRED_RUNS and
        admission.get("collection_cadence_ms") == LOAD_PROBE_CADENCE_MS and
        type(admission.get("maximum_collection_jitter_ms")) is int and
        0 <= admission["maximum_collection_jitter_ms"] <=
        LOAD_PROBE_MAXIMUM_JITTER_MS and
        type(admission.get("missed_sample_count")) is int and
        admission.get("missed_sample_count") == 0 and
        type(admission.get("missed_decision_count")) is int and
        admission.get("missed_decision_count") == 0 and
        type(admission.get("probe_history_record_bytes")) is int and
        admission["probe_history_record_bytes"] > 0 and
        admission.get("probe_audit_cursor_sequence") ==
        LOAD_PROBE_REQUIRED_RUNS and
        admission.get("paper_authorized") is False and
        admission.get("live_authorized") is False and
        admission.get("mutation_authorized") is False and
        admission.get("direct_broker_access") is False,
        "P1_FORMAL_ADMISSION_BINDING_INVALID",
    )
    return validated_at_ms, epoch, fencing


def _validate_formal_admission(
    config: "Configuration",
    now_ms: int,
    *,
    require_snapshot: bool = False,
) -> None:
    """Reject every formal start that is not bound to a fresh probe GO."""

    _require(
        config.policy_path is not None and
        config.admission_receipt_path is not None and
        config.authority_marker_path is not None and
        config.watch_snapshot_path is not None and
        config.watch_snapshot_path == REQUIRED_WATCH_SNAPSHOT and
        config.reader_unit is not None and
        config.reader_status_path is not None,
        "P1_FORMAL_ADMISSION_REQUIRED",
    )
    _require(
        Path(sys.argv[0]).is_absolute() and
        Path(sys.argv[0]) == Path(__file__) and
        not Path(sys.argv[0]).is_symlink(),
        "P1_FORMAL_CONTROLLER_PATH_INVALID",
    )
    policy, policy_contents = _secure_document(
        config.policy_path, "P1_FORMAL_POLICY", root_owned=True)
    admission, admission_contents = _secure_document(
        config.admission_receipt_path,
        "P1_FORMAL_ADMISSION",
        root_owned=True,
    )
    marker, _marker_contents = _secure_document(
        config.authority_marker_path,
        "P1_FORMAL_AUTHORITY_MARKER",
        root_owned=True,
    )
    for document in (policy, admission, marker):
        _reject_permission_surface(document)
    marker_created_at_ms = marker.get("marker_created_at_ms")
    expires_at_ms = marker.get("expires_at_ms")
    policy_expires_at_ms = policy.get("expires_at_ms")
    expected_environment = _load_probe_environment_binding()
    validated_at_ms, epoch, fencing = _validate_admission_payload(
        admission,
        config=config,
        expected_environment=expected_environment,
        now_ms=now_ms,
    )
    _require(
        policy.get("schema") ==
        "hepta.strategy-shadow-observation-policy.v1" and
        policy.get("version") == 1 and
        policy.get("campaign_id") == config.campaign_id and
        type(policy.get("valid_after_ms")) is int and
        policy.get("valid_after_ms") == config.valid_after_ms and
        type(policy.get("maximum_iterations")) is int and
        policy.get("maximum_iterations") == config.maximum_iterations and
        config.maximum_iterations == FORMAL_MAXIMUM_ITERATIONS and
        policy.get("slot_interval_ms") == DECISION_INTERVAL_MS and
        policy.get("maximum_lateness_ms") ==
        FORMAL_MAXIMUM_LATENESS_MS and
        policy_expires_at_ms ==
        config.valid_after_ms +
        FORMAL_MAXIMUM_ITERATIONS * DECISION_INTERVAL_MS and
        marker.get("schema") ==
        "hepta.p1-shadow-admission-authority-marker.v1" and
        marker.get("version") == 1 and marker.get("status") == "ACTIVE" and
        marker.get("campaign_id") == config.campaign_id and
        marker.get("policy_path") == str(config.policy_path) and
        marker.get("policy_file_sha256") ==
        "sha256:" + hashlib.sha256(policy_contents).hexdigest() and
        marker.get("policy_body_sha256") == policy.get("body_sha256") and
        marker.get("admission_receipt_path") ==
        str(config.admission_receipt_path) and
        marker.get("admission_receipt_file_sha256") ==
        "sha256:" + hashlib.sha256(admission_contents).hexdigest() and
        marker.get("admission_receipt_body_sha256") ==
        admission.get("body_sha256") and
        marker.get("admitted_at_ms") == validated_at_ms and
        type(marker_created_at_ms) is int and
        validated_at_ms <= marker_created_at_ms <= now_ms and
        marker_created_at_ms - validated_at_ms <=
        ADMISSION_MAXIMUM_AGE_MS and
        type(expires_at_ms) is int and
        type(policy_expires_at_ms) is int and
        expires_at_ms == policy_expires_at_ms and
        now_ms < expires_at_ms and
        isinstance(epoch, str) and 1 <= len(epoch) <= 256 and
        type(fencing) is int and 1 <= fencing < (1 << 64) and
        marker.get("execution_service_epoch") == epoch and
        marker.get("execution_service_fencing_generation") == fencing and
        admission.get("probe_execution_service_epoch") == epoch and
        admission.get("probe_execution_service_fencing_generation") == fencing and
        marker.get("environment") == expected_environment,
        "P1_FORMAL_ADMISSION_BINDING_INVALID",
    )
    if require_snapshot:
        snapshot, _ = _secure_document(
            config.watch_snapshot_path, "P1_FORMAL_WATCH_SNAPSHOT")
        _reject_permission_surface(snapshot)
        current_epoch, current_fencing = _execution_binding(snapshot)
        _require(
            (current_epoch, current_fencing) == (epoch, fencing),
            "P1_FORMAL_EXECUTION_BINDING_DRIFT",
        )
        _validate_formal_first_collection(config, snapshot)
    match = CAMPAIGN_ROUND.search(config.campaign_id)
    _require(
        match is not None and
        config.reader_unit ==
        f"hepta-p1-shadow-reader-round{match.group(1)}.service" and
        READER_UNIT.fullmatch(config.reader_unit) is not None,
        "P1_READER_UNIT_BINDING_INVALID",
    )


def _validate_load_probe_close(
    close_result: dict[str, Any],
    config: "Configuration",
) -> None:
    _flags_are_explicitly_false(
        close_result,
        (
            "paper_authorized",
            "live_authorized",
            "mutation_authorized",
            "direct_broker_access",
        ),
    )
    _require(
        close_result.get("schema") ==
        "hepta.shadow-watch-custodian-closure.v1" and
        close_result.get("campaign_id") == config.campaign_id and
        close_result.get("lease_generation") == config.start_generation and
        close_result.get("authoritative_revoke_outcome") == "ACCEPTED" and
        close_result.get("local_authority_removed") is True and
        close_result.get("export_evidence_removed") is True,
        "P1_LOAD_PROBE_CLOSE_INVALID",
    )


def _validate_formal_close(
    close_result: dict[str, Any],
    config: "Configuration",
    *,
    expected_generation: int | None = None,
) -> None:
    """Accept only authoritative removal or the exact absence proof."""

    _flags_are_explicitly_false(
        close_result,
        (
            "paper_authorized",
            "live_authorized",
            "mutation_authorized",
            "direct_broker_access",
        ),
    )
    no_active = {
        "schema": "hepta.shadow-watch-custodian-status.v1",
        "status": "NO_ACTIVE_TRANSACTION",
        "domain_id": "alpha",
        "paper_authorized": False,
        "live_authorized": False,
        "mutation_authorized": False,
        "direct_broker_access": False,
    }
    if close_result == no_active:
        return
    generation = close_result.get("lease_generation")
    _require(
        close_result.get("schema") ==
        "hepta.shadow-watch-custodian-closure.v1" and
        close_result.get("version") == 1 and
        close_result.get("campaign_id") == config.campaign_id and
        type(generation) is int and
        generation >= config.start_generation and
        (
            expected_generation is None or
            generation == expected_generation
        ) and
        close_result.get("authoritative_revoke_outcome") == "ACCEPTED" and
        close_result.get("local_authority_removed") is True and
        close_result.get("export_evidence_removed") is True,
        "P1_FORMAL_CLOSE_INVALID",
    )


def _load_probe_receipt(
    config: "Configuration",
    loop_result: "LoopResult",
    environment: dict[str, Any],
    close_result: dict[str, Any],
) -> dict[str, Any]:
    _require(
        loop_result.status == "LOAD_PROBE_COMPLETE" and
        loop_result.collector_runs == LOAD_PROBE_REQUIRED_RUNS and
        loop_result.completed_iterations == 0 and
        loop_result.generation == config.start_generation and
        type(loop_result.probe_duration_ms) is int and
        900_000 <= loop_result.probe_duration_ms <= 910_000 and
        type(loop_result.maximum_start_lateness_ms) is int and
        0 <= loop_result.maximum_start_lateness_ms <= 1_000 and
        type(loop_result.maximum_collector_elapsed_ms) is int and
        0 <= loop_result.maximum_collector_elapsed_ms <= 8_500,
        "P1_LOAD_PROBE_RESULT_INVALID",
    )
    _validate_load_probe_close(close_result, config)
    return _seal_document({
        "schema": "hepta.p1-shadow-load-probe-host-receipt.v1",
        "version": 1,
        "status": "LOAD_PROBE_COMPLETE",
        "campaign_id": config.campaign_id,
        "lease_generation": loop_result.generation,
        "collector_runs": loop_result.collector_runs,
        "required_collector_runs": LOAD_PROBE_REQUIRED_RUNS,
        "collection_cadence_ms": COLLECTOR_INTERVAL_NS // 1_000_000,
        "maximum_start_jitter_ms": MAX_START_JITTER_NS // 1_000_000,
        "probe_duration_ms": loop_result.probe_duration_ms,
        "maximum_start_lateness_ms":
            loop_result.maximum_start_lateness_ms,
        "maximum_collector_elapsed_ms":
            loop_result.maximum_collector_elapsed_ms,
        "environment": environment,
        "close_result": close_result,
        "paper_authorized": False,
        "live_authorized": False,
        "mutation_authorized": False,
        "direct_broker_access": False,
    })


@dataclass(frozen=True)
class Configuration:
    campaign_id: str
    domain_config: Path
    start_generation: int
    maximum_runtime_sec: int | None
    valid_after_ms: int
    maximum_iterations: int
    capture_lead_sec: int
    capture_timeout_sec: int
    evidence_root: Path
    export_root: Path
    reader_uid: int
    reader_gid: int
    capture_helper_sha256: str
    load_probe_runs: int | None = None
    load_probe_receipt_output: Path | None = None
    policy_path: Path | None = None
    admission_receipt_path: Path | None = None
    authority_marker_path: Path | None = None
    watch_snapshot_path: Path | None = None
    reader_unit: str | None = None
    reader_status_path: Path | None = None


class CaptureProcess(Protocol):
    returncode: int | None

    def poll(self) -> int | None:
        ...

    def communicate(self, timeout: float | None = None) -> tuple[str, str]:
        ...

    def terminate(self) -> None:
        ...

    def kill(self) -> None:
        ...

    def wait(self, timeout: float | None = None) -> int:
        ...


@dataclass
class CaptureJob:
    iteration: int
    slot_deadline_ns: int
    started_at_ns: int
    timeout_deadline_ns: int
    receipt_path: Path
    process: CaptureProcess


@dataclass(frozen=True)
class LoopResult:
    status: str
    generation: int
    collector_runs: int
    completed_iterations: int
    probe_duration_ms: int | None = None
    maximum_start_lateness_ms: int | None = None
    maximum_collector_elapsed_ms: int | None = None
    reader_completion: dict[str, Any] | None = None


class Clock:
    def monotonic_ns(self) -> int:
        return time.monotonic_ns()

    def time_ns(self) -> int:
        return time.time_ns()

    def sleep(self, seconds: float) -> None:
        time.sleep(seconds)


class CommandExecutor:
    """Exact production subprocess surface."""

    _environment = {
        "PATH": "/usr/sbin:/usr/bin:/sbin:/bin",
        "LANG": "C.UTF-8",
        "LC_ALL": "C.UTF-8",
    }

    @staticmethod
    def _run(arguments: list[str], timeout: float) -> subprocess.CompletedProcess[str]:
        try:
            completed = subprocess.run(
                arguments,
                check=False,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                encoding="utf-8",
                errors="strict",
                env=CommandExecutor._environment,
                close_fds=True,
                timeout=timeout,
            )
        except (OSError, subprocess.SubprocessError, UnicodeError) as error:
            raise ControllerError("P1_COMMAND_FAILED") from error
        _require(
            len(completed.stdout) <= MAX_COMMAND_OUTPUT and
            len(completed.stderr) <= MAX_COMMAND_OUTPUT,
            "P1_COMMAND_OUTPUT_TOO_LARGE",
        )
        return completed

    def collect(self) -> None:
        completed = self._run(
            [SYSTEMCTL, "start", "--wait", COLLECTOR_UNIT],
            COLLECTOR_TIMEOUT_SECONDS,
        )
        _require(completed.returncode == 0, "P1_COLLECTOR_FAILED")

    def assert_reader_active(
        self,
        config: Configuration,
        now_ms: int,
    ) -> None:
        _require(
            config.reader_unit is not None and
            config.reader_status_path is not None,
            "P1_READER_BINDING_MISSING",
        )
        active = self._run(
            [SYSTEMCTL, "is-active", config.reader_unit], 5.0)
        _require(
            active.returncode == 0 and active.stdout == "active\n" and
            active.stderr == "",
            "P1_READER_UNIT_NOT_ACTIVE",
        )
        shown = self._run(
            [
                SYSTEMCTL, "show", "--no-pager",
                "--property=ActiveState", "--property=SubState",
                "--property=Result", "--property=ExecMainStatus",
                config.reader_unit,
            ],
            5.0,
        )
        lines = shown.stdout.splitlines()
        parsed: dict[str, str] = {}
        for line in lines:
            key, separator, value = line.partition("=")
            _require(separator == "=" and key not in parsed,
                     "P1_READER_UNIT_STATUS_INVALID")
            parsed[key] = value
        _require(
            shown.returncode == 0 and shown.stderr == "" and
            parsed == {
                "ActiveState": "active",
                "SubState": "running",
                "Result": "success",
                "ExecMainStatus": "0",
            },
            "P1_READER_UNIT_STATUS_INVALID",
        )
        status, _ = _secure_document(
            config.reader_status_path, "P1_READER_STATUS")
        updated_at_ms = status.get("updated_at_ms")
        _require(
            status.get("schema") ==
            "hepta.p1-shadow-observer-controller-status.v1" and
            status.get("version") == 1 and
            status.get("campaign_id") == config.campaign_id and
            status.get("controller_uid") == config.reader_uid and
            status.get("controller_gid") == config.reader_gid and
            status.get("state") in {
                "WAITING_FOR_EXPORT", "OBSERVING", "RUNNING"
            } and
            status.get("reason") is None and
            type(updated_at_ms) is int and
            0 <= now_ms - updated_at_ms <= MAXIMUM_READER_HEARTBEAT_AGE_MS and
            status.get("paper_authorized") is False and
            status.get("live_authorized") is False and
            status.get("mutation_attempted") is False and
            status.get("direct_broker_access") is False,
            "P1_READER_HEARTBEAT_INVALID",
        )

    def assert_reader_completed(
        self,
        config: Configuration,
        now_ms: int,
    ) -> dict[str, Any]:
        """Verify the uid-1000 reader and observer committed the final slot."""

        _require(
            config.reader_unit is not None and
            config.reader_status_path is not None,
            "P1_READER_BINDING_MISSING",
        )
        shown = self._run(
            [
                SYSTEMCTL, "show", "--no-pager",
                "--property=ActiveState", "--property=SubState",
                "--property=Result", "--property=ExecMainStatus",
                "--property=MainPID", config.reader_unit,
            ],
            5.0,
        )
        parsed: dict[str, str] = {}
        for line in shown.stdout.splitlines():
            key, separator, value = line.partition("=")
            _require(
                separator == "=" and key not in parsed,
                "P1_READER_COMPLETION_UNIT_INVALID",
            )
            parsed[key] = value
        _require(
            shown.returncode == 0 and shown.stderr == "" and
            set(parsed) == {
                "ActiveState", "SubState", "Result", "ExecMainStatus",
                "MainPID",
            } and
            parsed.get("ActiveState") == "active" and
            parsed.get("SubState") == "running" and
            parsed.get("Result") == "success" and
            parsed.get("ExecMainStatus") == "0" and
            parsed.get("MainPID", "").isdigit() and
            int(parsed["MainPID"]) > 1,
            "P1_READER_COMPLETION_UNIT_INVALID",
        )
        reader_pid = int(parsed["MainPID"])
        observer_state_path = (
            config.reader_status_path.parent /
            "observer" / "observer-state.json"
        )
        status, status_contents = _secure_reader_document(
            config.reader_status_path, "P1_READER_COMPLETION_STATUS", config)
        state, state_contents = _secure_reader_document(
            observer_state_path, "P1_READER_COMPLETION_STATE", config)
        _flags_are_explicitly_false(
            status,
            (
                "paper_authorized",
                "live_authorized",
                "mutation_attempted",
                "direct_broker_access",
            ),
        )
        _flags_are_explicitly_false(
            state,
            (
                "paper_authorized",
                "live_authorized",
                "mutation_attempted",
                "direct_broker_access",
            ),
        )

        updated_at_ms = status.get("updated_at_ms")
        status_iterations = status.get("completed_iterations")
        _require(
            status.get("schema") ==
            "hepta.p1-shadow-observer-controller-status.v1" and
            status.get("version") == 1 and
            status.get("campaign_id") == config.campaign_id and
            status.get("controller_uid") == config.reader_uid and
            status.get("controller_gid") == config.reader_gid and
            status.get("controller_pid") == reader_pid and
            status.get("reason") is None and
            type(updated_at_ms) is int and
            0 <= now_ms - updated_at_ms <=
            MAXIMUM_READER_HEARTBEAT_AGE_MS and
            type(status_iterations) is int and
            0 <= status_iterations <= config.maximum_iterations,
            "P1_READER_COMPLETION_STATUS_INVALID",
        )

        state_iterations = state.get("completed_iterations")
        last_generated_at_ms = state.get("last_generated_at_ms")
        _require(
            state.get("schema") ==
            "hepta.bounded-shadow-observer-state.v1" and
            state.get("version") == 1 and
            state.get("campaign_id") == config.campaign_id and
            state.get("maximum_iterations") == config.maximum_iterations and
            state.get("segment_status") == "OPEN" and
            state.get("missed_sample_count") == 0 and
            state.get("missed_decision_count") == 0 and
            type(state.get("sample_count")) is int and
            state["sample_count"] >= 1 and
            type(state_iterations) is int and
            0 <= state_iterations <= config.maximum_iterations and
            type(last_generated_at_ms) is int and
            0 <= now_ms - last_generated_at_ms <=
            MAXIMUM_READER_HEARTBEAT_AGE_MS,
            "P1_READER_COMPLETION_STATE_INVALID",
        )

        if status.get("state") in {"OBSERVING", "RUNNING"}:
            _require(
                status.get("observer_status") in {None, "RUNNING"} and
                state.get("status") in {"RUNNING", "COMPLETE"},
                "P1_READER_COMPLETION_STATUS_INVALID",
            )
            raise ControllerError("P1_READER_COMPLETION_PENDING")

        _require(
            status.get("state") == "TERMINAL" and
            status.get("observer_status") == "COMPLETE" and
            status.get("observer_outcome") == "COMPLETE" and
            state.get("status") == "COMPLETE",
            "P1_READER_COMPLETION_STATUS_INVALID",
        )
        _require(
            status_iterations == config.maximum_iterations and
            state_iterations == config.maximum_iterations,
            "P1_READER_COMPLETION_MISMATCH",
        )
        return {
            "reader_unit": config.reader_unit,
            "reader_pid": reader_pid,
            "acknowledged_at_ms": now_ms,
            "controller_status_file_sha256":
                "sha256:" + hashlib.sha256(status_contents).hexdigest(),
            "controller_status_body_sha256": status["body_sha256"],
            "observer_state_file_sha256":
                "sha256:" + hashlib.sha256(state_contents).hexdigest(),
            "observer_state_body_sha256": state["body_sha256"],
        }

    def rotate(
        self,
        config: Configuration,
        current_generation: int,
    ) -> dict[str, Any]:
        completed = self._run(
            [
                CUSTODIAN,
                "--domain-config", str(config.domain_config),
                "rotate",
                "--campaign-id", config.campaign_id,
                "--current-generation", str(current_generation),
                "--ttl-sec", str(ROTATION_TTL_SECONDS),
            ],
            CUSTODIAN_TIMEOUT_SECONDS,
        )
        _require(completed.returncode == 0, "P1_ROTATION_FAILED")
        result = _strict_json(completed.stdout, "P1_ROTATION_JSON_INVALID")
        _require(isinstance(result, dict), "P1_ROTATION_JSON_INVALID")
        _flags_are_explicitly_false(
            result,
            (
                "paper_authorized",
                "live_authorized",
                "mutation_authorized",
                "direct_broker_access",
            ),
        )
        _require(
            result.get("schema") ==
            "hepta.shadow-watch-custodian-rotation.v1" and
            result.get("status") == "ROTATED" and
            result.get("campaign_id") == config.campaign_id and
            result.get("previous_lease_generation") == current_generation and
            result.get("lease_generation") == current_generation + 1 and
            result.get("previous_authority_outcome") == "ROTATED",
            "P1_ROTATION_BINDING_INVALID",
        )
        return result

    def start_capture(
        self,
        config: Configuration,
        iteration: int,
        receipt_path: Path,
    ) -> CaptureProcess:
        _require(
            _sha256_file(Path(CAPTURE_HELPER)) ==
            config.capture_helper_sha256,
            "P1_CAPTURE_HELPER_DIGEST_MISMATCH",
        )
        arguments = [
            CAPTURE_HELPER,
            "--evidence-root", str(config.evidence_root),
            "--export-root", str(config.export_root),
            "--receipt-output", str(receipt_path),
            "--reader-uid", str(config.reader_uid),
            "--reader-gid", str(config.reader_gid),
            "--capture-helper-sha256", config.capture_helper_sha256,
        ]
        del iteration
        try:
            return subprocess.Popen(
                arguments,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                encoding="utf-8",
                errors="strict",
                env=self._environment,
                close_fds=True,
            )
        except (OSError, subprocess.SubprocessError, UnicodeError) as error:
            raise ControllerError("P1_CAPTURE_START_FAILED") from error

    def close(
        self,
        config: Configuration,
        reason: str,
    ) -> dict[str, Any]:
        completed = self._run(
            [
                CUSTODIAN,
                "--domain-config", str(config.domain_config),
                "close",
                "--reason", reason,
            ],
            CUSTODIAN_TIMEOUT_SECONDS,
        )
        _require(completed.returncode == 0, "P1_CUSTODIAN_CLOSE_FAILED")
        result = _strict_json(completed.stdout, "P1_CLOSE_JSON_INVALID")
        _require(isinstance(result, dict), "P1_CLOSE_JSON_INVALID")
        _reject_permission_surface(result)
        if result.get("status") == "PENDING_EXPIRY":
            _flags_are_explicitly_false(
                result,
                (
                    "paper_authorized",
                    "live_authorized",
                    "mutation_authorized",
                    "direct_broker_access",
                ),
            )
            _require(
                result.get("schema") ==
                "hepta.shadow-watch-custodian-status.v1" and
                result.get("campaign_id") == config.campaign_id and
                type(result.get("lease_generation")) is int and
                result["lease_generation"] >= config.start_generation,
                "P1_CLOSE_BINDING_INVALID",
            )
            completed = self._run(
                [
                    CUSTODIAN,
                    "--domain-config", str(config.domain_config),
                    "reconcile",
                ],
                CUSTODIAN_TIMEOUT_SECONDS,
            )
            _require(
                completed.returncode == 0,
                "P1_CUSTODIAN_RECONCILE_FAILED",
            )
            result = _strict_json(
                completed.stdout, "P1_RECONCILE_JSON_INVALID")
            _require(isinstance(result, dict), "P1_RECONCILE_JSON_INVALID")
            _flags_are_explicitly_false(
                result,
                (
                    "paper_authorized",
                    "live_authorized",
                    "mutation_authorized",
                    "direct_broker_access",
                ),
            )
        if config.load_probe_runs is None:
            _validate_formal_close(result, config)
        else:
            _require(
                result.get("schema") in {
                    "hepta.shadow-watch-custodian-closure.v1",
                    "hepta.shadow-watch-custodian-status.v1",
                },
                "P1_CLOSE_BINDING_INVALID",
            )
        return result


def _validate_configuration(config: Configuration, now_wall_ms: int) -> None:
    _require(os.geteuid() == ROOT_UID and os.getegid() == ROOT_GID,
             "P1_ROOT_REQUIRED")
    _require(
        IDENTIFIER.fullmatch(config.campaign_id) is not None and
        config.domain_config == REQUIRED_DOMAIN_CONFIG and
        type(config.start_generation) is int and
        1 <= config.start_generation < (1 << 64) - 1 and
        (
            config.maximum_runtime_sec is None or
            (
                type(config.maximum_runtime_sec) is int and
                config.maximum_runtime_sec >= 1
            )
        ) and
        type(config.valid_after_ms) is int and
        config.valid_after_ms > 0 and
        type(config.maximum_iterations) is int and
        1 <= config.maximum_iterations <= 10_000 and
        type(config.capture_lead_sec) is int and
        1 <= config.capture_lead_sec < 900 and
        type(config.capture_timeout_sec) is int and
        1 <= config.capture_timeout_sec <
        config.capture_lead_sec - 1 and
        config.evidence_root == REQUIRED_EVIDENCE_ROOT and
        config.export_root == REQUIRED_EXPORT_ROOT and
        config.reader_uid == 1000 and
        config.reader_gid == 1000 and
        DIGEST.fullmatch(config.capture_helper_sha256) is not None and
        (
            config.load_probe_runs is None or
            config.load_probe_runs == LOAD_PROBE_REQUIRED_RUNS
        ) and
        config.reader_unit is not None and
        READER_UNIT.fullmatch(config.reader_unit) is not None and
        config.reader_status_path is not None and
        config.reader_status_path.is_absolute() and
        (
            config.load_probe_runs is None or
            (
                config.load_probe_receipt_output is not None and
                config.load_probe_receipt_output.is_absolute() and
                config.policy_path is None and
                config.admission_receipt_path is None and
                config.authority_marker_path is None and
                config.watch_snapshot_path is None
            )
        ),
        "P1_INPUT_INVALID",
    )
    if config.load_probe_runs is None:
        first_capture_ms = (
            config.valid_after_ms - config.capture_lead_sec * 1000)
        _require(
            first_capture_ms >= now_wall_ms,
            "P1_CAPTURE_WINDOW_ALREADY_OPEN",
        )
        _require(
            _sha256_file(Path(CAPTURE_HELPER)) ==
            config.capture_helper_sha256,
            "P1_CAPTURE_HELPER_DIGEST_MISMATCH",
        )
        _validate_formal_admission(config, now_wall_ms)


def _capture_receipt_path(config: Configuration, iteration: int) -> Path:
    return config.evidence_root / (
        f"p1-{config.campaign_id}-iteration-{iteration:06d}-"
        "official-source-capture.json"
    )


def _read_capture_receipt(
    config: Configuration,
    job: CaptureJob,
) -> dict[str, Any]:
    data = _secure_read(
        job.receipt_path, "P1_CAPTURE_RECEIPT_FILE_INVALID", MAX_JSON_BYTES)
    receipt = _strict_json(data, "P1_CAPTURE_RECEIPT_JSON_INVALID")
    _require(isinstance(receipt, dict), "P1_CAPTURE_RECEIPT_JSON_INVALID")
    _flags_are_explicitly_false(
        receipt,
        (
            "paper_authorized",
            "live_authorized",
            "mutation_attempted",
            "direct_broker_access",
        ),
    )
    _require(
        receipt.get("schema") ==
        "hepta.official-source-root-capture-receipt.v1" and
        receipt.get("status") == "OFFICIAL_CAPTURE_COMPLETE" and
        receipt.get("capture_helper_sha256") ==
        config.capture_helper_sha256 and
        type(receipt.get("observed_at_ms")) is int and
        receipt.get("exported_bundle_path") ==
        str(config.export_root / "official-source-bundle.json"),
        "P1_CAPTURE_RECEIPT_BINDING_INVALID",
    )
    return receipt


def _stop_capture(job: CaptureJob) -> None:
    if job.process.poll() is not None:
        try:
            job.process.communicate(timeout=CAPTURE_TERMINATE_GRACE_SECONDS)
        except (OSError, subprocess.SubprocessError, UnicodeError):
            pass
        return
    try:
        job.process.terminate()
        job.process.wait(timeout=CAPTURE_TERMINATE_GRACE_SECONDS)
    except (OSError, subprocess.SubprocessError):
        try:
            job.process.kill()
            job.process.wait(timeout=CAPTURE_TERMINATE_GRACE_SECONDS)
        except (OSError, subprocess.SubprocessError):
            pass


class Controller:
    def __init__(
        self,
        config: Configuration,
        *,
        clock: Clock | None = None,
        executor: CommandExecutor | None = None,
        expected_environment: dict[str, Any] | None = None,
        environment_provider: Callable[[], dict[str, Any]] | None = None,
    ) -> None:
        _require(
            (expected_environment is None) == (environment_provider is None),
            "P1_ENVIRONMENT_GUARD_INVALID",
        )
        self.config = config
        self.clock = clock if clock is not None else Clock()
        self.executor = executor if executor is not None else CommandExecutor()
        self.expected_environment = (
            None if expected_environment is None else dict(expected_environment))
        self.environment_provider = environment_provider
        self.active_capture: CaptureJob | None = None
        self.formal_revalidated = False

    def _assert_deadline(self, deadline_ns: int, reason: str) -> int:
        now = self.clock.monotonic_ns()
        _require(now <= deadline_ns + MAX_START_JITTER_NS, reason)
        return now

    def _assert_environment(self) -> None:
        if self.expected_environment is None:
            return
        _require(
            self.environment_provider is not None and
            self.environment_provider() == self.expected_environment,
            "P1_CAMPAIGN_ENVIRONMENT_DRIFT",
        )

    def _collect(self) -> None:
        if (
            self.config.load_probe_runs is None and
            self.config.admission_receipt_path is not None and
            not self.formal_revalidated
        ):
            _validate_formal_admission(
                self.config, self.clock.time_ns() // 1_000_000)
        self.executor.assert_reader_active(
            self.config, self.clock.time_ns() // 1_000_000)
        self._assert_environment()
        self.executor.collect()
        self._assert_environment()
        if (
            self.config.load_probe_runs is None and
            self.config.admission_receipt_path is not None and
            not self.formal_revalidated
        ):
            _validate_formal_admission(
                self.config,
                self.clock.time_ns() // 1_000_000,
                require_snapshot=True,
            )
            self.formal_revalidated = True

    def _sleep_until(self, deadline_ns: int) -> None:
        now = self.clock.monotonic_ns()
        if now < deadline_ns:
            self.clock.sleep((deadline_ns - now) / 1_000_000_000)

    def _poll_capture(self) -> int | None:
        job = self.active_capture
        if job is None:
            return None
        result = job.process.poll()
        now = self.clock.monotonic_ns()
        if result is None:
            if now >= job.timeout_deadline_ns:
                _stop_capture(job)
                self.active_capture = None
                raise ControllerError("P1_CAPTURE_TIMEOUT")
            return None
        try:
            stdout, stderr = job.process.communicate(
                timeout=CAPTURE_TERMINATE_GRACE_SECONDS)
        except (OSError, subprocess.SubprocessError, UnicodeError) as error:
            self.active_capture = None
            raise ControllerError("P1_CAPTURE_RESULT_FAILED") from error
        self.active_capture = None
        _require(
            len(stdout) <= MAX_COMMAND_OUTPUT and
            len(stderr) <= MAX_COMMAND_OUTPUT and
            result == 0,
            "P1_CAPTURE_FAILED",
        )
        _require(
            now < job.slot_deadline_ns,
            "P1_CAPTURE_NOT_READY_BEFORE_SLOT",
        )
        _read_capture_receipt(self.config, job)
        return job.iteration

    def _begin_capture(
        self,
        iteration: int,
        slot_deadline_ns: int,
        deadline_ns: int,
    ) -> None:
        _require(self.active_capture is None, "P1_CAPTURE_OVERLAP")
        self._assert_deadline(deadline_ns, "P1_CAPTURE_START_CADENCE_MISSED")
        receipt_path = _capture_receipt_path(self.config, iteration)
        _require(
            not os.path.lexists(receipt_path),
            "P1_CAPTURE_RECEIPT_ALREADY_EXISTS",
        )
        started_at = self.clock.monotonic_ns()
        process = self.executor.start_capture(
            self.config, iteration, receipt_path)
        self.active_capture = CaptureJob(
            iteration=iteration,
            slot_deadline_ns=slot_deadline_ns,
            started_at_ns=started_at,
            timeout_deadline_ns=(
                started_at +
                self.config.capture_timeout_sec * 1_000_000_000
            ),
            receipt_path=receipt_path,
            process=process,
        )

    def _run_load_probe(self) -> LoopResult:
        runs = self.config.load_probe_runs
        _require(
            runs == LOAD_PROBE_REQUIRED_RUNS,
            "P1_LOAD_PROBE_RUN_COUNT_INVALID",
        )
        started_ns = self.clock.monotonic_ns()
        maximum_start_lateness_ns = 0
        maximum_collector_elapsed_ns = 0
        collector_runs = 0
        for collector_index in range(runs):
            deadline_ns = (
                started_ns + collector_index * COLLECTOR_INTERVAL_NS)
            self._sleep_until(deadline_ns)
            actual_start_ns = self._assert_deadline(
                deadline_ns,
                "P1_LOAD_PROBE_CADENCE_MISSED",
            )
            maximum_start_lateness_ns = max(
                maximum_start_lateness_ns,
                actual_start_ns - deadline_ns,
            )
            self._collect()
            finished_ns = self.clock.monotonic_ns()
            elapsed_ns = finished_ns - actual_start_ns
            _require(
                0 <= elapsed_ns <= LOAD_PROBE_MAXIMUM_COLLECTOR_NS,
                "P1_LOAD_PROBE_COLLECTOR_BUDGET_EXCEEDED",
            )
            maximum_collector_elapsed_ns = max(
                maximum_collector_elapsed_ns, elapsed_ns)
            collector_runs += 1
        # Keep the final triplet readable until its next would-be absolute
        # deadline.  A healthy exporter/observer pipeline must commit that
        # sample inside the same ten-second budget; closure then removes it.
        self._sleep_until(started_ns + runs * COLLECTOR_INTERVAL_NS)
        completed_ns = self.clock.monotonic_ns()
        return LoopResult(
            status="LOAD_PROBE_COMPLETE",
            generation=self.config.start_generation,
            collector_runs=collector_runs,
            completed_iterations=0,
            probe_duration_ms=(completed_ns - started_ns) // 1_000_000,
            maximum_start_lateness_ms=(
                maximum_start_lateness_ns + 999_999) // 1_000_000,
            maximum_collector_elapsed_ms=(
                maximum_collector_elapsed_ns + 999_999) // 1_000_000,
        )

    def run(self) -> LoopResult:
        if self.config.load_probe_runs is not None:
            return self._run_load_probe()
        start_mono_ns = self.clock.monotonic_ns()
        start_wall_ms = self.clock.time_ns() // 1_000_000
        collector_index = 0
        rotation_index = 1
        current_generation = self.config.start_generation
        collector_runs = 0
        next_capture_iteration = 1
        next_slot_iteration = 1
        completed_captures: set[int] = set()
        final_ack_deadline_ns: int | None = None
        next_final_ack_poll_ns: int | None = None
        stop_deadline_ns = (
            None
            if self.config.maximum_runtime_sec is None
            else start_mono_ns +
            self.config.maximum_runtime_sec * 1_000_000_000
        )

        def slot_deadline(iteration: int) -> int:
            wall_ms = (
                self.config.valid_after_ms +
                (iteration - 1) * DECISION_INTERVAL_MS
            )
            return start_mono_ns + (wall_ms - start_wall_ms) * 1_000_000

        def capture_deadline(iteration: int) -> int:
            return (
                slot_deadline(iteration) -
                self.config.capture_lead_sec * 1_000_000_000
            )

        try:
            while True:
                collector_deadline = (
                    start_mono_ns +
                    collector_index * COLLECTOR_INTERVAL_NS
                )
                rotation_deadline = (
                    start_mono_ns +
                    rotation_index * ROTATION_INTERVAL_NS
                )
                deadlines = [collector_deadline, rotation_deadline]
                if next_capture_iteration <= self.config.maximum_iterations:
                    deadlines.append(
                        capture_deadline(next_capture_iteration))
                if next_slot_iteration <= self.config.maximum_iterations:
                    deadlines.append(slot_deadline(next_slot_iteration))
                if self.active_capture is not None:
                    deadlines.append(
                        self.active_capture.timeout_deadline_ns)
                if final_ack_deadline_ns is not None:
                    _require(
                        next_final_ack_poll_ns is not None,
                        "P1_READER_COMPLETION_STATE_INVALID",
                    )
                    deadlines.extend(
                        (final_ack_deadline_ns, next_final_ack_poll_ns))
                if stop_deadline_ns is not None:
                    deadlines.append(stop_deadline_ns)
                self._sleep_until(min(deadlines))
                now = self.clock.monotonic_ns()

                completed = self._poll_capture()
                if completed is not None:
                    completed_captures.add(completed)

                if (
                    stop_deadline_ns is not None and
                    now >= stop_deadline_ns
                ):
                    _require(
                        self.active_capture is None,
                        "P1_MAXIMUM_RUNTIME_DURING_CAPTURE",
                    )
                    return LoopResult(
                        status="MAXIMUM_RUNTIME_REACHED",
                        generation=current_generation,
                        collector_runs=collector_runs,
                        completed_iterations=next_slot_iteration - 1,
                    )

                if (
                    next_capture_iteration <=
                    self.config.maximum_iterations and
                    now >= capture_deadline(next_capture_iteration)
                ):
                    iteration = next_capture_iteration
                    self._begin_capture(
                        iteration,
                        slot_deadline(iteration),
                        capture_deadline(iteration),
                    )
                    next_capture_iteration += 1
                    completed = self._poll_capture()
                    if completed is not None:
                        completed_captures.add(completed)
                    now = self.clock.monotonic_ns()

                if now >= collector_deadline:
                    self._assert_deadline(
                        collector_deadline,
                        "P1_COLLECTOR_CADENCE_MISSED",
                    )
                    self._collect()
                    collector_runs += 1
                    collector_index += 1
                    now = self.clock.monotonic_ns()

                if now >= rotation_deadline:
                    self._assert_deadline(
                        rotation_deadline,
                        "P1_ROTATION_CADENCE_MISSED",
                    )
                    result = self.executor.rotate(
                        self.config, current_generation)
                    current_generation = int(result["lease_generation"])
                    rotation_index += 1
                    now = self.clock.monotonic_ns()

                completed = self._poll_capture()
                if completed is not None:
                    completed_captures.add(completed)
                now = self.clock.monotonic_ns()

                if (
                    next_slot_iteration <=
                    self.config.maximum_iterations and
                    now >= slot_deadline(next_slot_iteration)
                ):
                    self._assert_deadline(
                        slot_deadline(next_slot_iteration),
                        "P1_DECISION_SLOT_CADENCE_MISSED",
                    )
                    _require(
                        next_slot_iteration in completed_captures,
                        "P1_CAPTURE_MISSING_AT_DECISION_SLOT",
                    )
                    completed_captures.remove(next_slot_iteration)
                    next_slot_iteration += 1
                    if (
                        next_slot_iteration >
                        self.config.maximum_iterations
                    ):
                        final_ack_deadline_ns = (
                            now + FINAL_ACKNOWLEDGEMENT_TIMEOUT_NS)
                        next_final_ack_poll_ns = now

                if (
                    final_ack_deadline_ns is not None and
                    next_final_ack_poll_ns is not None and
                    now >= next_final_ack_poll_ns
                ):
                    try:
                        reader_completion = self.executor.assert_reader_completed(
                            self.config,
                            self.clock.time_ns() // 1_000_000,
                        )
                    except ControllerError as error:
                        if str(error) != "P1_READER_COMPLETION_PENDING":
                            raise
                    else:
                        acknowledged_ns = self.clock.monotonic_ns()
                        next_collector_ns = (
                            start_mono_ns +
                            collector_index * COLLECTOR_INTERVAL_NS)
                        _require(
                            acknowledged_ns < next_collector_ns,
                            "P1_READER_COMPLETION_CADENCE_BOUND",
                        )
                        return LoopResult(
                            status="ITERATIONS_COMPLETE",
                            generation=current_generation,
                            collector_runs=collector_runs,
                            completed_iterations=
                            self.config.maximum_iterations,
                            reader_completion=reader_completion,
                        )
                    now = self.clock.monotonic_ns()
                    _require(
                        now < final_ack_deadline_ns,
                        "P1_READER_COMPLETION_TIMEOUT",
                    )
                    next_final_ack_poll_ns = min(
                        now + FINAL_ACKNOWLEDGEMENT_POLL_NS,
                        final_ack_deadline_ns,
                    )
        finally:
            if self.active_capture is not None:
                _stop_capture(self.active_capture)
                self.active_capture = None


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        allow_abbrev=False,
        description="Root-only finite P1 SHADOW/WATCH host controller",
    )
    parser.add_argument("--campaign-id", required=True)
    parser.add_argument("--domain-config", required=True, type=Path)
    parser.add_argument("--start-generation", required=True, type=int)
    parser.add_argument("--maximum-runtime-sec", type=int)
    parser.add_argument("--valid-after-ms", required=True, type=int)
    parser.add_argument("--maximum-iterations", required=True, type=int)
    parser.add_argument("--capture-lead-sec", type=int, default=180)
    parser.add_argument("--capture-timeout-sec", type=int, default=150)
    parser.add_argument(
        "--evidence-root", type=Path, default=REQUIRED_EVIDENCE_ROOT)
    parser.add_argument(
        "--export-root", type=Path, default=REQUIRED_EXPORT_ROOT)
    parser.add_argument("--reader-uid", type=int, default=1000)
    parser.add_argument("--reader-gid", type=int, default=1000)
    parser.add_argument("--capture-helper-sha256", required=True)
    parser.add_argument("--load-probe-runs", type=int)
    parser.add_argument("--load-probe-receipt-output", type=Path)
    parser.add_argument("--policy", dest="policy_path", type=Path)
    parser.add_argument(
        "--admission-receipt", dest="admission_receipt_path", type=Path)
    parser.add_argument(
        "--authority-marker", dest="authority_marker_path", type=Path)
    parser.add_argument(
        "--watch-snapshot", dest="watch_snapshot_path", type=Path)
    parser.add_argument("--reader-unit", required=True)
    parser.add_argument("--reader-status", dest="reader_status_path",
                        required=True, type=Path)
    return parser


def _configuration(arguments: argparse.Namespace) -> Configuration:
    return Configuration(
        campaign_id=arguments.campaign_id,
        domain_config=arguments.domain_config,
        start_generation=arguments.start_generation,
        maximum_runtime_sec=arguments.maximum_runtime_sec,
        valid_after_ms=arguments.valid_after_ms,
        maximum_iterations=arguments.maximum_iterations,
        capture_lead_sec=arguments.capture_lead_sec,
        capture_timeout_sec=arguments.capture_timeout_sec,
        evidence_root=arguments.evidence_root,
        export_root=arguments.export_root,
        reader_uid=arguments.reader_uid,
        reader_gid=arguments.reader_gid,
        capture_helper_sha256=arguments.capture_helper_sha256,
        load_probe_runs=arguments.load_probe_runs,
        load_probe_receipt_output=arguments.load_probe_receipt_output,
        policy_path=arguments.policy_path,
        admission_receipt_path=arguments.admission_receipt_path,
        authority_marker_path=arguments.authority_marker_path,
        watch_snapshot_path=arguments.watch_snapshot_path,
        reader_unit=arguments.reader_unit,
        reader_status_path=arguments.reader_status_path,
    )


def _install_signal_handlers() -> dict[int, Any]:
    previous: dict[int, Any] = {}
    observed = False

    def handler(signum: int, _frame: Any) -> None:
        nonlocal observed
        if observed:
            return
        observed = True
        raise ControllerSignal(signum)

    for signum in (signal.SIGTERM, signal.SIGINT, signal.SIGHUP):
        previous[signum] = signal.signal(signum, handler)
    return previous


def _restore_signal_handlers(previous: dict[int, Any]) -> None:
    for signum, handler in previous.items():
        signal.signal(signum, handler)


def _error_code(error: BaseException) -> str:
    message = str(error)
    if re.fullmatch(r"[A-Z0-9_]{3,96}", message):
        return message
    return "P1_CONTROLLER_FAILED"


def main() -> int:
    arguments = _parser().parse_args()
    config = _configuration(arguments)
    executor = CommandExecutor()
    clock = Clock()
    started = False
    custodian_close_required = (
        config.domain_config == REQUIRED_DOMAIN_CONFIG and
        isinstance(config.campaign_id, str) and
        IDENTIFIER.fullmatch(config.campaign_id) is not None and
        type(config.start_generation) is int and
        1 <= config.start_generation < (1 << 64) - 1
    )
    close_result: dict[str, Any] | None = None
    loop_result: LoopResult | None = None
    campaign_environment: dict[str, Any] | None = None
    terminal_error: BaseException | None = None
    previous_handlers: dict[int, Any] = {}
    try:
        _validate_configuration(config, clock.time_ns() // 1_000_000)
        previous_handlers = _install_signal_handlers()
        started = True
        campaign_environment = _load_probe_environment_binding()
        loop_result = Controller(
            config,
            clock=clock,
            executor=executor,
            expected_environment=campaign_environment,
            environment_provider=_load_probe_environment_binding,
        ).run()
        _require(
            _load_probe_environment_binding() == campaign_environment,
            "P1_CAMPAIGN_ENVIRONMENT_DRIFT",
        )
    except BaseException as error:
        terminal_error = error
    finally:
        if previous_handlers:
            # A service-stop signal has already selected the terminal path.
            # Do not let a duplicate signal interrupt exact authority closure.
            for signum in previous_handlers:
                signal.signal(signum, signal.SIG_IGN)
        if started or custodian_close_required:
            reason = (
                "service-stop"
                if terminal_error is None or
                isinstance(terminal_error, ControllerSignal)
                else "operator-request"
            )
            try:
                close_result = executor.close(config, reason)
                if close_result.get("status") == "PENDING_EXPIRY":
                    raise ControllerError("P1_CLOSE_PENDING_EXPIRY")
                if (
                    terminal_error is None and
                    config.load_probe_runs is not None
                ):
                    _validate_load_probe_close(close_result, config)
                elif config.load_probe_runs is None:
                    _validate_formal_close(
                        close_result,
                        config,
                        expected_generation=(
                            None if loop_result is None else
                            loop_result.generation
                        ),
                    )
            except BaseException as close_error:
                close_result = None
                if terminal_error is None:
                    terminal_error = close_error
                else:
                    print(
                        _error_code(close_error),
                        file=sys.stderr,
                    )
        if previous_handlers:
            _restore_signal_handlers(previous_handlers)

    if terminal_error is not None:
        print(_error_code(terminal_error), file=sys.stderr)
        if close_result is not None:
            sys.stdout.buffer.write(_canonical({
                "schema": "hepta.p1-shadow-host-controller-result.v1",
                "status": "FAILED_CLOSED",
                "campaign_id": config.campaign_id,
                "close_result": close_result,
                "paper_authorized": False,
                "live_authorized": False,
                "mutation_authorized": False,
                "direct_broker_access": False,
            }))
        if isinstance(terminal_error, ControllerSignal):
            return 128 + terminal_error.signum
        return 78

    _require(loop_result is not None, "P1_RESULT_MISSING")
    if config.load_probe_runs is not None:
        _require(
            campaign_environment is not None and
            close_result is not None and
            config.load_probe_receipt_output is not None,
            "P1_LOAD_PROBE_RESULT_INVALID",
        )
        try:
            receipt = _load_probe_receipt(
                config, loop_result, campaign_environment, close_result)
            _write_root_exclusive(config.load_probe_receipt_output, receipt)
        except ControllerError as error:
            print(_error_code(error), file=sys.stderr)
            return 78
        sys.stdout.buffer.write(_canonical({
            "schema": "hepta.p1-shadow-load-probe-host-result.v1",
            "status": "LOAD_PROBE_RECEIPT_WRITTEN",
            "campaign_id": config.campaign_id,
            "receipt_path": str(config.load_probe_receipt_output),
            "receipt_body_sha256": receipt["body_sha256"],
            "paper_authorized": False,
            "live_authorized": False,
            "mutation_authorized": False,
            "direct_broker_access": False,
        }))
        return 0
    _require(
        loop_result.status != "ITERATIONS_COMPLETE" or
        (
            isinstance(loop_result.reader_completion, dict) and
            set(loop_result.reader_completion) == {
                "reader_unit", "reader_pid", "acknowledged_at_ms",
                "controller_status_file_sha256",
                "controller_status_body_sha256",
                "observer_state_file_sha256",
                "observer_state_body_sha256",
            }
        ),
        "P1_READER_COMPLETION_RESULT_INVALID",
    )
    formal_result = {
        "schema": "hepta.p1-shadow-host-controller-result.v1",
        "status": loop_result.status,
        "campaign_id": config.campaign_id,
        "lease_generation": loop_result.generation,
        "collector_runs": loop_result.collector_runs,
        "completed_iterations": loop_result.completed_iterations,
        "close_result": close_result,
        "paper_authorized": False,
        "live_authorized": False,
        "mutation_authorized": False,
        "direct_broker_access": False,
    }
    if loop_result.status == "ITERATIONS_COMPLETE":
        formal_result["reader_completion"] = loop_result.reader_completion
    sys.stdout.buffer.write(_canonical(formal_result))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
