#!/usr/bin/env python3

"""Fail-closed admission validator for one finite P1 SHADOW load probe.

The validator is read-only.  It never provisions WATCH, creates a campaign
policy, starts a service, or calls a PAPER/LIVE/broker surface.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import subprocess
import sys
import time
from typing import Any

from hepta_agent_trust_domain import (
    TrustDomainRuntimeError,
    read_alpha_gateway_process_identity,
    read_alpha_gateway_process_profile,
    read_alpha_gateway_profile,
    read_alpha_gateway_socket,
)


REQUIRED_RUNS = 91
CADENCE_MS = 10_000
MAXIMUM_JITTER_MS = 1_000
MAXIMUM_COLLECTOR_MS = 8_500
MAXIMUM_EXPORT_COMMIT_MS = 9_000
MAXIMUM_QUOTE_GAP_MS = 15_000
MAXIMUM_RECEIPT_AGE_MS = 60_000
EXPECTED_UID = 1000
EXPECTED_GID = 1000
ROOT_UID = 0
ROOT_GID = 0
MAXIMUM_JSON_BYTES = 4 * 1024 * 1024
SYSTEMCTL = "/usr/bin/systemctl"
GATEWAY_UNIT = "hepta-tool-gateway@alpha.service"
GATEWAY_SOCKET = Path("/run/hepta-agent-alpha/tools.sock")
BOOT_ID_PATH = Path("/proc/sys/kernel/random/boot_id")
BOOT_ID_MAXIMUM_BYTES = 128

DIGEST = re.compile(r"sha256:[0-9a-f]{64}")
IDENTIFIER = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,127}")
BOOT_ID = re.compile(
    r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-"
    r"[0-9a-f]{4}-[0-9a-f]{12}")
PERMISSION_FIELDS = frozenset({
    "paper_authorized",
    "live_authorized",
    "mutation_authorized",
    "mutation_attempted",
    "direct_broker_access",
})

HOST_FIELDS = frozenset({
    "schema", "version", "status", "campaign_id", "lease_generation",
    "collector_runs", "required_collector_runs", "collection_cadence_ms",
    "maximum_start_jitter_ms", "probe_duration_ms",
    "maximum_start_lateness_ms", "maximum_collector_elapsed_ms",
    "environment", "close_result", "paper_authorized", "live_authorized",
    "mutation_authorized", "direct_broker_access", "body_sha256",
})
ENVIRONMENT_FIELDS = frozenset({
    "boot_id", "audit_journal_device", "audit_journal_inode",
    "collector_sha256", "exporter_sha256", "heptactl_sha256",
    "gateway_sha256", "custodian_sha256", "observer_sha256",
    "host_controller_sha256", "domain_config_sha256",
    "gateway_profile_sha256", "gateway_process_profile_sha256",
    "gateway_invocation_id", "gateway_main_pid",
    "gateway_exec_main_start_timestamp_monotonic_us",
    "gateway_socket_device", "gateway_socket_inode",
})
CONTROLLER_FIELDS = frozenset({
    "schema", "version", "campaign_id", "controller_pid",
    "controller_uid", "controller_gid", "state", "started_at_ms",
    "updated_at_ms", "observer_invocations",
    "last_export_receipt_body_sha256", "last_snapshot_body_sha256",
    "last_lease_generation", "locked_execution_service_epoch",
    "locked_execution_service_fencing_generation",
    "observer_status", "observer_outcome",
    "completed_iterations", "reason", "paper_authorized",
    "live_authorized", "mutation_attempted", "direct_broker_access",
    "body_sha256",
})
OBSERVER_FIELDS = frozenset({
    "schema", "version", "campaign_id", "campaign_sha256",
    "policy_sha256", "policy_body_sha256", "strategy_id",
    "strategy_version", "strategy_sha256", "status",
    "collection_cadence_ms", "maximum_collection_jitter_ms",
    "valid_after_ms", "expires_at_ms", "slot_interval_ms",
    "maximum_iterations", "maximum_lateness_ms", "segment_index",
    "segment_status", "segment_record_count", "segment_history_head_sha256",
    "last_collection_started_at_ms", "last_generated_at_ms",
    "last_snapshot_body_sha256", "last_watch_generation",
    "last_lease_receipt_body_sha256", "last_lease_receipt_file_sha256",
    "completed_iterations", "last_receipt_sha256", "missed_sample_count",
    "missed_decision_count", "sample_count", "accounted_payload_bytes",
    "accounted_payload_files", "accounted_payload_accumulator",
    "last_storage_audit_sample_count", "last_storage_audit_accumulator",
    "final_audit_receipt_sha256", "final_audit_segment_count",
    "audit_events", "paper_authorized", "live_authorized",
    "mutation_attempted", "direct_broker_access", "body_sha256",
})
RECORD_FIELDS = frozenset({
    "schema", "version", "sequence", "cadence_ms", "maximum_jitter_ms",
    "domain_id", "agent_uid", "instrument", "collection_started_at_ms",
    "collection_finished_at_ms", "generated_at_ms", "quote_read_finished_at_ms",
    "quote_changed", "quote", "catalog_sha256", "descriptor_sha256",
    "execution_service_epoch", "execution_service_fencing_generation",
    "snapshot_body_sha256", "snapshot_file_sha256", "watch_generation",
    "watch_lease_operation", "watch_lease_previous_generation",
    "watch_lease_previous_receipt_body_sha256",
    "watch_lease_receipt_body_sha256", "watch_lease_receipt_file_sha256",
    "watch_lease_accepted_at_ms", "watch_lease_expires_at_ms",
    "watch_lease_ttl_seconds", "watch_export_receipt_body_sha256",
    "watch_export_receipt_file_sha256", "watch_exported_at_ms",
    "watch_export_reader_uid", "watch_export_reader_gid",
    "previous_record_sha256", "record_sha256",
})
QUOTE_FIELDS = frozenset({
    "bid", "ask", "observed_at_ms", "stale_after_ms", "source",
    "authoritative", "stale",
})
HEAD_FIELDS = frozenset({
    "schema", "version", "record_schema", "record_count",
    "first_record_sha256", "last_record_sha256", "last_record_name",
    "last_record_file_sha256", "last_previous_record_sha256",
    "last_snapshot_body_sha256", "last_snapshot_file_sha256",
    "cadence_ms", "maximum_jitter_ms", "history_record_bytes",
    "audit_cursor_sequence", "audit_expected_previous_sha256",
    "body_sha256",
})


class ValidationError(RuntimeError):
    """Stable load-probe admission failure."""


def _require(condition: bool, reason: str) -> None:
    if not condition:
        raise ValidationError(reason)


def _pairs(values: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in values:
        _require(key not in result, "P1_LOAD_PROBE_DUPLICATE_JSON_KEY")
        result[key] = value
    return result


def canonical_bytes(value: Any) -> bytes:
    try:
        return (
            json.dumps(
                value,
                ensure_ascii=True,
                allow_nan=False,
                sort_keys=True,
                separators=(",", ":"),
            )
            + "\n"
        ).encode("ascii")
    except (TypeError, ValueError, UnicodeError) as error:
        raise ValidationError("P1_LOAD_PROBE_CANONICALIZATION_FAILED") from error


def digest_bytes(contents: bytes) -> str:
    return "sha256:" + hashlib.sha256(contents).hexdigest()


def _secure_read(
    path: Path,
    label: str,
    maximum_bytes: int,
    *,
    expected_uid: int | None = None,
    expected_gid: int | None = None,
    allowed_modes: frozenset[int] | None = None,
) -> tuple[bytes, os.stat_result]:
    flags = os.O_RDONLY | os.O_CLOEXEC
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    try:
        descriptor = os.open(path, flags)
    except OSError as error:
        raise ValidationError(f"{label}_FILE_INVALID") from error
    try:
        metadata = os.fstat(descriptor)
        _require(
            stat.S_ISREG(metadata.st_mode) and
            metadata.st_nlink == 1 and
            1 <= metadata.st_size <= maximum_bytes and
            (expected_uid is None or metadata.st_uid == expected_uid) and
            (expected_gid is None or metadata.st_gid == expected_gid) and
            (allowed_modes is None or
             stat.S_IMODE(metadata.st_mode) in allowed_modes),
            f"{label}_FILE_INVALID",
        )
        chunks: list[bytes] = []
        remaining = metadata.st_size
        while remaining:
            chunk = os.read(descriptor, min(remaining, 1024 * 1024))
            _require(bool(chunk), f"{label}_FILE_INVALID")
            chunks.append(chunk)
            remaining -= len(chunk)
        _require(os.read(descriptor, 1) == b"", f"{label}_FILE_INVALID")
        after = os.fstat(descriptor)
        _require(
            (metadata.st_dev, metadata.st_ino, metadata.st_size,
             metadata.st_mtime_ns, metadata.st_ctime_ns) ==
            (after.st_dev, after.st_ino, after.st_size,
             after.st_mtime_ns, after.st_ctime_ns),
            f"{label}_FILE_INVALID",
        )
        return b"".join(chunks), metadata
    except OSError as error:
        raise ValidationError(f"{label}_FILE_INVALID") from error
    finally:
        os.close(descriptor)


def _read_boot_id(path: Path, reason: str) -> str:
    """Read the canonical Linux boot ID through a rebound anchored path."""

    _require(path == BOOT_ID_PATH, reason)
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
        _require(BOOT_ID.fullmatch(boot_id) is not None, reason)
        completed = True
        return boot_id
    except ValidationError:
        raise
    except (OSError, UnicodeError) as error:
        raise ValidationError(reason) from error
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
            raise ValidationError(reason) from cleanup_error


def _read_document(
    path: Path,
    label: str,
    *,
    root_owned: bool = False,
    expected_root_uid: int = 0,
    expected_root_gid: int = 0,
) -> tuple[dict[str, Any], bytes]:
    contents, _ = _secure_read(
        path,
        label,
        MAXIMUM_JSON_BYTES,
        expected_uid=expected_root_uid if root_owned else None,
        expected_gid=expected_root_gid if root_owned else None,
        allowed_modes=(frozenset({0o600, 0o640, 0o644})
                       if root_owned else None),
    )
    try:
        document = json.loads(contents, object_pairs_hook=_pairs)
    except (UnicodeError, json.JSONDecodeError, ValueError) as error:
        raise ValidationError(f"{label}_JSON_INVALID") from error
    _require(
        isinstance(document, dict) and canonical_bytes(document) == contents,
        f"{label}_CANONICAL_INVALID",
    )
    return document, contents


def _validate_body_digest(document: dict[str, Any], label: str) -> None:
    body = dict(document)
    claimed = body.pop("body_sha256", None)
    _require(
        isinstance(claimed, str) and DIGEST.fullmatch(claimed) is not None and
        claimed == digest_bytes(canonical_bytes(body)),
        f"{label}_DIGEST_INVALID",
    )


def _validate_record_digest(document: dict[str, Any], label: str) -> None:
    body = dict(document)
    claimed = body.pop("record_sha256", None)
    _require(
        isinstance(claimed, str) and DIGEST.fullmatch(claimed) is not None and
        claimed == digest_bytes(canonical_bytes(body)),
        f"{label}_DIGEST_INVALID",
    )


def _is_digest(value: Any) -> bool:
    return isinstance(value, str) and DIGEST.fullmatch(value) is not None


def _reject_permission_surface(value: Any) -> None:
    if isinstance(value, dict):
        for key, child in value.items():
            if key in PERMISSION_FIELDS:
                _require(child is False, "P1_LOAD_PROBE_PERMISSION_NOT_FALSE")
            _reject_permission_surface(child)
    elif isinstance(value, list):
        for child in value:
            _reject_permission_surface(child)


def _regular_sha256(path: Path) -> str:
    contents, _ = _secure_read(
        path, "P1_LOAD_PROBE_RUNTIME", 64 * 1024 * 1024)
    return digest_bytes(contents)


def _write_root_exclusive(path: Path, document: dict[str, Any]) -> None:
    _require(path.is_absolute(), "P1_LOAD_PROBE_OUTPUT_PATH_INVALID")
    flags = os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    try:
        parent_fd = os.open(path.parent, flags)
        parent = os.fstat(parent_fd)
    except OSError as error:
        raise ValidationError("P1_LOAD_PROBE_OUTPUT_DIRECTORY_INVALID") from error
    try:
        _require(
            stat.S_ISDIR(parent.st_mode) and parent.st_uid == 0 and
            parent.st_gid == 0 and stat.S_IMODE(parent.st_mode) == 0o700,
            "P1_LOAD_PROBE_OUTPUT_DIRECTORY_INVALID",
        )
        create_flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_CLOEXEC
        if hasattr(os, "O_NOFOLLOW"):
            create_flags |= os.O_NOFOLLOW
        descriptor = os.open(path.name, create_flags, 0o600, dir_fd=parent_fd)
        try:
            os.fchmod(descriptor, 0o600)
            contents = canonical_bytes(document)
            offset = 0
            while offset < len(contents):
                offset += os.write(descriptor, contents[offset:])
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
        os.fsync(parent_fd)
    except FileExistsError as error:
        raise ValidationError("P1_LOAD_PROBE_OUTPUT_EXISTS") from error
    except OSError as error:
        raise ValidationError("P1_LOAD_PROBE_OUTPUT_WRITE_FAILED") from error
    finally:
        os.close(parent_fd)


def current_environment_binding(
    *,
    boot_id_path: Path,
    audit_journal: Path,
    collector: Path,
    exporter: Path,
    heptactl: Path,
    gateway: Path,
    custodian: Path,
    observer: Path,
    host_controller: Path,
    domain_config: Path,
    gateway_profile: Path,
    gateway_socket: Path = GATEWAY_SOCKET,
) -> dict[str, Any]:
    try:
        boot_id = _read_boot_id(
            boot_id_path, "P1_LOAD_PROBE_BOOT_ID_FILE_INVALID")
        audit_flags = os.O_RDONLY | os.O_CLOEXEC
        if hasattr(os, "O_NOFOLLOW"):
            audit_flags |= os.O_NOFOLLOW
        audit_descriptor = os.open(audit_journal, audit_flags)
        try:
            audit_metadata = os.fstat(audit_descriptor)
        finally:
            os.close(audit_descriptor)
    except (OSError, UnicodeError) as error:
        raise ValidationError("P1_LOAD_PROBE_ENVIRONMENT_INVALID") from error
    _require(
        BOOT_ID.fullmatch(boot_id) is not None and
        stat.S_ISREG(audit_metadata.st_mode) and
        audit_metadata.st_nlink == 1,
        "P1_LOAD_PROBE_ENVIRONMENT_INVALID",
    )
    binding = {
        "boot_id": boot_id,
        "audit_journal_device": audit_metadata.st_dev,
        "audit_journal_inode": audit_metadata.st_ino,
        "collector_sha256": _regular_sha256(collector),
        "exporter_sha256": _regular_sha256(exporter),
        "heptactl_sha256": _regular_sha256(heptactl),
        "gateway_sha256": _regular_sha256(gateway),
        "custodian_sha256": _regular_sha256(custodian),
        "observer_sha256": _regular_sha256(observer),
        "host_controller_sha256": _regular_sha256(host_controller),
        "domain_config_sha256": _regular_sha256(domain_config),
    }
    try:
        profile_before = read_alpha_gateway_profile(gateway_profile)
    except TrustDomainRuntimeError as error:
        raise ValidationError(
            "P1_LOAD_PROBE_GATEWAY_PROFILE_INVALID") from error
    binding["gateway_profile_sha256"] = digest_bytes(profile_before.raw)
    binding.update(_live_gateway_identity(
        gateway_socket, gateway_profile, profile_before))
    return binding


def _live_gateway_identity(
        gateway_socket: Path, gateway_profile: Path,
        profile_before: Any) -> dict[str, Any]:
    def status() -> dict[str, str]:
        try:
            completed = subprocess.run(
                [
                    SYSTEMCTL, "show", "--no-pager",
                    "--property=ActiveState", "--property=SubState",
                    "--property=InvocationID", "--property=MainPID",
                    "--property=ExecMainStartTimestampMonotonic", GATEWAY_UNIT,
                ],
                check=False,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                encoding="utf-8",
                errors="strict",
                timeout=5,
                env={"PATH": "/usr/bin:/bin", "LANG": "C", "LC_ALL": "C"},
            )
        except (OSError, subprocess.SubprocessError, UnicodeError) as error:
            raise ValidationError(
                "P1_LOAD_PROBE_GATEWAY_IDENTITY_INVALID") from error
        parsed: dict[str, str] = {}
        for line in completed.stdout.splitlines():
            key, separator, value = line.partition("=")
            _require(separator == "=" and key not in parsed,
                     "P1_LOAD_PROBE_GATEWAY_IDENTITY_INVALID")
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
            "P1_LOAD_PROBE_GATEWAY_IDENTITY_INVALID",
        )
        return parsed

    parsed = status()
    try:
        process_profile = read_alpha_gateway_process_profile(
            int(parsed["MainPID"]))
    except TrustDomainRuntimeError as error:
        raise ValidationError(
            "P1_LOAD_PROBE_GATEWAY_PROCESS_PROFILE_INVALID") from error
    try:
        socket_before = read_alpha_gateway_socket(gateway_socket)
        profile_after = read_alpha_gateway_profile(gateway_profile)
        parsed_after = status()
        process_after = read_alpha_gateway_process_identity(
            int(parsed["MainPID"]))
        socket_after = read_alpha_gateway_socket(gateway_socket)
    except TrustDomainRuntimeError as error:
        raise ValidationError(
            "P1_LOAD_PROBE_GATEWAY_REBIND_INVALID") from error
    _require(
        profile_before == profile_after and parsed_after == parsed and
        process_profile.pid_directory_metadata ==
        process_after.pid_directory_metadata and
        process_profile.starttime_ticks == process_after.starttime_ticks and
        socket_before == socket_after,
        "P1_LOAD_PROBE_GATEWAY_IDENTITY_CHANGED",
    )
    return {
        "gateway_invocation_id": parsed["InvocationID"],
        "gateway_main_pid": int(parsed["MainPID"]),
        "gateway_exec_main_start_timestamp_monotonic_us":
            int(parsed["ExecMainStartTimestampMonotonic"]),
        "gateway_socket_device": socket_before.metadata[0],
        "gateway_socket_inode": socket_before.metadata[1],
        "gateway_process_profile_sha256": digest_bytes(
            process_profile.canonical_projection),
    }


def _validate_host_receipt(
    receipt: dict[str, Any],
    *,
    campaign_id: str,
    environment: dict[str, Any],
    now_ms: int,
) -> None:
    _require(set(receipt) == HOST_FIELDS, "P1_LOAD_PROBE_HOST_FIELDS_INVALID")
    _validate_body_digest(receipt, "P1_LOAD_PROBE_HOST")
    _reject_permission_surface(receipt)
    _require(
        receipt.get("schema") ==
        "hepta.p1-shadow-load-probe-host-receipt.v1" and
        receipt.get("version") == 1 and
        receipt.get("status") == "LOAD_PROBE_COMPLETE" and
        receipt.get("campaign_id") == campaign_id and
        type(receipt.get("lease_generation")) is int and
        receipt.get("lease_generation") >= 1 and
        receipt.get("collector_runs") == REQUIRED_RUNS and
        receipt.get("required_collector_runs") == REQUIRED_RUNS and
        receipt.get("collection_cadence_ms") == CADENCE_MS and
        receipt.get("maximum_start_jitter_ms") == MAXIMUM_JITTER_MS and
        type(receipt.get("probe_duration_ms")) is int and
        900_000 <= receipt["probe_duration_ms"] <= 910_000 and
        type(receipt.get("maximum_start_lateness_ms")) is int and
        0 <= receipt["maximum_start_lateness_ms"] <= MAXIMUM_JITTER_MS and
        type(receipt.get("maximum_collector_elapsed_ms")) is int and
        0 <= receipt["maximum_collector_elapsed_ms"] <=
        MAXIMUM_COLLECTOR_MS and
        isinstance(receipt.get("environment"), dict) and
        set(receipt["environment"]) == ENVIRONMENT_FIELDS and
        receipt["environment"] == environment,
        "P1_LOAD_PROBE_HOST_BINDING_INVALID",
    )
    close = receipt.get("close_result")
    _require(isinstance(close, dict), "P1_LOAD_PROBE_CLOSE_INVALID")
    _validate_body_digest(close, "P1_LOAD_PROBE_CLOSE")
    _reject_permission_surface(close)
    closed_at_ms = close.get("closed_at_ms")
    _require(
        close.get("schema") == "hepta.shadow-watch-custodian-closure.v1" and
        close.get("campaign_id") == campaign_id and
        close.get("lease_generation") == receipt.get("lease_generation") and
        close.get("authoritative_revoke_outcome") == "ACCEPTED" and
        close.get("local_authority_removed") is True and
        close.get("export_evidence_removed") is True and
        type(closed_at_ms) is int and
        0 <= now_ms - closed_at_ms <= MAXIMUM_RECEIPT_AGE_MS,
        "P1_LOAD_PROBE_CLOSE_INVALID",
    )


def _validate_controller_status(
    status: dict[str, Any],
    *,
    campaign_id: str,
    lease_generation: int,
) -> None:
    _require(
        set(status) == CONTROLLER_FIELDS,
        "P1_LOAD_PROBE_CONTROLLER_FIELDS_INVALID",
    )
    _validate_body_digest(status, "P1_LOAD_PROBE_CONTROLLER")
    _reject_permission_surface(status)
    terminal_after_close = (
        status.get("state") == "FAILED" and
        status.get("reason") == "P1_CONTROLLER_EXPORT_TRIPLET_LOST")
    _require(
        status.get("schema") ==
        "hepta.p1-shadow-observer-controller-status.v1" and
        status.get("version") == 1 and
        status.get("campaign_id") == campaign_id and
        status.get("controller_uid") == EXPECTED_UID and
        status.get("controller_gid") == EXPECTED_GID and
        status.get("observer_invocations") == REQUIRED_RUNS and
        status.get("last_lease_generation") == lease_generation and
        status.get("observer_status") == "RUNNING" and
        status.get("completed_iterations") == 0 and
        (
            (
                status.get("state") == "RUNNING" and
                status.get("reason") is None
            ) or terminal_after_close
        ),
        "P1_LOAD_PROBE_CONTROLLER_BINDING_INVALID",
    )


def _validate_observer_state(
    state: dict[str, Any],
    *,
    campaign_id: str,
    lease_generation: int,
) -> None:
    _require(
        set(state) == OBSERVER_FIELDS,
        "P1_LOAD_PROBE_OBSERVER_FIELDS_INVALID",
    )
    _validate_body_digest(state, "P1_LOAD_PROBE_OBSERVER")
    _reject_permission_surface(state)
    events = state.get("audit_events")
    _require(
        state.get("schema") == "hepta.bounded-shadow-observer-state.v1" and
        state.get("version") == 1 and
        state.get("campaign_id") == campaign_id and
        state.get("status") == "RUNNING" and
        state.get("collection_cadence_ms") == CADENCE_MS and
        state.get("maximum_collection_jitter_ms") == MAXIMUM_JITTER_MS and
        state.get("segment_index") == 1 and
        state.get("segment_status") == "OPEN" and
        state.get("segment_record_count") == REQUIRED_RUNS and
        state.get("sample_count") == REQUIRED_RUNS and
        state.get("missed_sample_count") == 0 and
        state.get("missed_decision_count") == 0 and
        state.get("completed_iterations") == 0 and
        state.get("last_watch_generation") == lease_generation and
        state.get("final_audit_receipt_sha256") is None and
        state.get("final_audit_segment_count") == 0 and
        isinstance(events, list) and len(events) == 1 and
        isinstance(events[0], dict) and
        events[0].get("sequence") == 1 and
        events[0].get("event") == "OBSERVATION_STARTED" and
        events[0].get("reason") is None,
        "P1_LOAD_PROBE_OBSERVER_BINDING_INVALID",
    )


def _history_paths(artifact_root: Path) -> tuple[Path, list[Path]]:
    try:
        metadata = artifact_root.lstat()
    except OSError as error:
        raise ValidationError("P1_LOAD_PROBE_ARTIFACT_ROOT_INVALID") from error
    _require(
        stat.S_ISDIR(metadata.st_mode) and not stat.S_ISLNK(metadata.st_mode),
        "P1_LOAD_PROBE_ARTIFACT_ROOT_INVALID",
    )
    segments = sorted((artifact_root / "segments").glob("segment-*"))
    _require(
        segments == [artifact_root / "segments" / "segment-000001"],
        "P1_LOAD_PROBE_SEGMENT_SET_INVALID",
    )
    history = segments[0] / "history"
    records = sorted(history.glob("record-*.json"))
    expected = [
        history / f"record-{sequence:020d}.json"
        for sequence in range(1, REQUIRED_RUNS + 1)
    ]
    _require(records == expected, "P1_LOAD_PROBE_RECORD_SET_INVALID")
    return history, records


def _validate_history(
    artifact_root: Path,
    *,
    state: dict[str, Any],
    lease_generation: int,
) -> dict[str, Any]:
    history, paths = _history_paths(artifact_root)
    documents: list[dict[str, Any]] = []
    contents_list: list[bytes] = []
    first_started: int | None = None
    binding: tuple[Any, ...] | None = None
    previous: dict[str, Any] | None = None
    for sequence, path in enumerate(paths, start=1):
        record, contents = _read_document(
            path, "P1_LOAD_PROBE_HISTORY_RECORD")
        _require(
            set(record) == RECORD_FIELDS,
            "P1_LOAD_PROBE_RECORD_FIELDS_INVALID",
        )
        _validate_record_digest(record, "P1_LOAD_PROBE_HISTORY_RECORD")
        _reject_permission_surface(record)
        quote = record.get("quote")
        started = record.get("collection_started_at_ms")
        finished = record.get("collection_finished_at_ms")
        generated = record.get("generated_at_ms")
        exported = record.get("watch_exported_at_ms")
        _require(
            record.get("schema") == "hepta.shadow-market-history-record.v3" and
            record.get("version") == 3 and
            record.get("sequence") == sequence and
            record.get("cadence_ms") == CADENCE_MS and
            record.get("maximum_jitter_ms") == MAXIMUM_JITTER_MS and
            record.get("watch_generation") == lease_generation and
            record.get("watch_lease_operation") == "PROVISION" and
            record.get("watch_lease_previous_generation") is None and
            record.get("watch_lease_previous_receipt_body_sha256") is None and
            record.get("watch_export_reader_uid") == EXPECTED_UID and
            record.get("watch_export_reader_gid") == EXPECTED_GID and
            type(started) is int and type(finished) is int and
            type(generated) is int and type(exported) is int and
            started <= finished <= generated and
            0 <= finished - started <= MAXIMUM_COLLECTOR_MS and
            0 <= exported - started <= MAXIMUM_EXPORT_COMMIT_MS and
            isinstance(quote, dict) and set(quote) == QUOTE_FIELDS and
            quote.get("authoritative") is True and
            quote.get("stale") is False and
            quote.get("source") == "SIMULATOR" and
            isinstance(record.get("quote_changed"), bool) and
            type(quote.get("observed_at_ms")) is int and
            _is_digest(record.get("catalog_sha256")) and
            isinstance(record.get("descriptor_sha256"), dict) and
            bool(record["descriptor_sha256"]) and
            all(
                isinstance(name, str) and _is_digest(digest)
                for name, digest in record["descriptor_sha256"].items()
            ) and
            _is_digest(record.get("snapshot_body_sha256")) and
            _is_digest(record.get("snapshot_file_sha256")) and
            _is_digest(record.get("watch_lease_receipt_body_sha256")) and
            _is_digest(record.get("watch_lease_receipt_file_sha256")) and
            _is_digest(record.get("watch_export_receipt_body_sha256")) and
            _is_digest(record.get("watch_export_receipt_file_sha256")),
            "P1_LOAD_PROBE_RECORD_BINDING_INVALID",
        )
        current_binding = (
            record.get("domain_id"), record.get("agent_uid"),
            record.get("instrument"), record.get("catalog_sha256"),
            record.get("descriptor_sha256"),
            record.get("execution_service_epoch"),
            record.get("execution_service_fencing_generation"),
            record.get("watch_lease_receipt_body_sha256"),
            record.get("watch_lease_receipt_file_sha256"),
            record.get("watch_lease_accepted_at_ms"),
            record.get("watch_lease_expires_at_ms"),
            record.get("watch_lease_ttl_seconds"),
        )
        if binding is None:
            binding = current_binding
            first_started = started
            _require(
                record.get("previous_record_sha256") is None and
                record.get("quote_changed") is True,
                "P1_LOAD_PROBE_RECORD_CHAIN_INVALID",
            )
        else:
            _require(
                current_binding == binding and previous is not None and
                record.get("previous_record_sha256") ==
                previous.get("record_sha256"),
                "P1_LOAD_PROBE_RECORD_CHAIN_INVALID",
            )
            started_delta = started - previous["collection_started_at_ms"]
            quote_delta = (
                quote["observed_at_ms"] -
                previous["quote"]["observed_at_ms"])
            _require(
                CADENCE_MS - MAXIMUM_JITTER_MS <= started_delta <=
                CADENCE_MS + MAXIMUM_JITTER_MS and
                (
                    (
                        record.get("quote_changed") is True and
                        0 < quote_delta <= MAXIMUM_QUOTE_GAP_MS
                    ) or
                    (
                        record.get("quote_changed") is False and
                        quote_delta == 0 and
                        quote == previous["quote"]
                    )
                ),
                "P1_LOAD_PROBE_CADENCE_INVALID",
            )
        assert first_started is not None
        absolute_deviation = abs(
            started - (first_started + (sequence - 1) * CADENCE_MS))
        _require(
            absolute_deviation <= MAXIMUM_JITTER_MS,
            "P1_LOAD_PROBE_ABSOLUTE_CADENCE_INVALID",
        )
        documents.append(record)
        contents_list.append(contents)
        previous = record

    head, _head_contents = _read_document(
        history / "history-head.json", "P1_LOAD_PROBE_HISTORY_HEAD")
    _require(
        set(head) == HEAD_FIELDS,
        "P1_LOAD_PROBE_HISTORY_HEAD_FIELDS_INVALID",
    )
    _validate_body_digest(head, "P1_LOAD_PROBE_HISTORY_HEAD")
    first = documents[0]
    last = documents[-1]
    _require(
        head.get("schema") == "hepta.shadow-market-history-head.v1" and
        head.get("version") == 1 and
        head.get("record_schema") ==
        "hepta.shadow-market-history-record.v3" and
        head.get("record_count") == REQUIRED_RUNS and
        head.get("cadence_ms") == CADENCE_MS and
        head.get("maximum_jitter_ms") == MAXIMUM_JITTER_MS and
        head.get("first_record_sha256") == first["record_sha256"] and
        head.get("last_record_sha256") == last["record_sha256"] and
        head.get("last_record_name") == paths[-1].name and
        head.get("last_record_file_sha256") ==
        digest_bytes(contents_list[-1]) and
        head.get("last_previous_record_sha256") ==
        last["previous_record_sha256"] and
        head.get("last_snapshot_body_sha256") ==
        last["snapshot_body_sha256"] and
        head.get("last_snapshot_file_sha256") ==
        last["snapshot_file_sha256"] and
        head.get("history_record_bytes") ==
        sum(map(len, contents_list)) and
        head.get("history_record_bytes") > 0 and
        head.get("audit_cursor_sequence") == REQUIRED_RUNS and
        head.get("audit_expected_previous_sha256") ==
        last["record_sha256"] and
        state.get("segment_history_head_sha256") == head["body_sha256"] and
        state.get("last_collection_started_at_ms") ==
        last["collection_started_at_ms"] and
        state.get("last_generated_at_ms") == last["generated_at_ms"] and
        state.get("last_snapshot_body_sha256") ==
        last["snapshot_body_sha256"] and
        state.get("last_lease_receipt_body_sha256") ==
        last["watch_lease_receipt_body_sha256"] and
        state.get("last_lease_receipt_file_sha256") ==
        last["watch_lease_receipt_file_sha256"],
        "P1_LOAD_PROBE_HISTORY_BINDING_INVALID",
    )
    _require(
        state.get("accounted_payload_files") == REQUIRED_RUNS and
        type(state.get("accounted_payload_bytes")) is int and
        state.get("accounted_payload_bytes") == head["history_record_bytes"] and
        state.get("last_storage_audit_sample_count") == 64 and
        _is_digest(state.get("accounted_payload_accumulator")) and
        _is_digest(state.get("last_storage_audit_accumulator")),
        "P1_LOAD_PROBE_HISTORY_COUNTER_INVALID",
    )
    return {"head": head, "first_record": first, "last_record": last}


def validate(
    *,
    campaign_id: str,
    prospective_campaign_id: str,
    prospective_policy_path: Path,
    authority_marker_path: Path,
    host_receipt_path: Path,
    controller_status_path: Path,
    observer_state_path: Path,
    artifact_root: Path,
    environment: dict[str, Any],
    now_ms: int,
    _expected_root_uid: int = 0,
    _expected_root_gid: int = 0,
) -> dict[str, Any]:
    _require(
        IDENTIFIER.fullmatch(campaign_id) is not None and
        IDENTIFIER.fullmatch(prospective_campaign_id) is not None and
        prospective_campaign_id != campaign_id and
        prospective_policy_path.is_absolute() and
        authority_marker_path.is_absolute() and
        not os.path.lexists(prospective_policy_path) and
        not os.path.lexists(authority_marker_path) and
        type(now_ms) is int and now_ms > 0,
        "P1_LOAD_PROBE_INPUT_INVALID",
    )
    host, _ = _read_document(
        host_receipt_path,
        "P1_LOAD_PROBE_HOST",
        root_owned=True,
        expected_root_uid=_expected_root_uid,
        expected_root_gid=_expected_root_gid,
    )
    controller, _ = _read_document(
        controller_status_path, "P1_LOAD_PROBE_CONTROLLER")
    observer, _ = _read_document(
        observer_state_path, "P1_LOAD_PROBE_OBSERVER")
    _validate_host_receipt(
        host,
        campaign_id=campaign_id,
        environment=environment,
        now_ms=now_ms,
    )
    lease_generation = host["lease_generation"]
    _validate_controller_status(
        controller,
        campaign_id=campaign_id,
        lease_generation=lease_generation,
    )
    _validate_observer_state(
        observer,
        campaign_id=campaign_id,
        lease_generation=lease_generation,
    )
    history = _validate_history(
        artifact_root,
        state=observer,
        lease_generation=lease_generation,
    )
    head = history["head"]
    first_record = history["first_record"]
    last_record = history["last_record"]
    _require(
        controller.get("last_snapshot_body_sha256") ==
        observer.get("last_snapshot_body_sha256") and
        controller.get("last_export_receipt_body_sha256") ==
        last_record.get("watch_export_receipt_body_sha256") and
        controller.get("locked_execution_service_epoch") ==
        first_record.get("execution_service_epoch") and
        controller.get("locked_execution_service_fencing_generation") ==
        first_record.get("execution_service_fencing_generation"),
        "P1_LOAD_PROBE_CONTROLLER_HISTORY_BINDING_INVALID",
    )
    body = {
        "schema": "hepta.p1-shadow-load-probe-admission-receipt.v1",
        "version": 1,
        "status": "GO",
        "campaign_id": campaign_id,
        "prospective_campaign_id": prospective_campaign_id,
        "prospective_policy_path": str(prospective_policy_path),
        "authority_marker_path": str(authority_marker_path),
        "validated_at_ms": now_ms,
        "host_receipt_body_sha256": host["body_sha256"],
        "observer_controller_status_body_sha256":
            controller["body_sha256"],
        "observer_state_body_sha256": observer["body_sha256"],
        "history_head_body_sha256": head["body_sha256"],
        "probe_execution_service_epoch":
            first_record["execution_service_epoch"],
        "probe_execution_service_fencing_generation":
            first_record["execution_service_fencing_generation"],
        "probe_first_collection_started_at_ms":
            first_record["collection_started_at_ms"],
        "probe_first_exported_at_ms": first_record["watch_exported_at_ms"],
        "probe_first_record_sha256": first_record["record_sha256"],
        "probe_first_snapshot_body_sha256":
            first_record["snapshot_body_sha256"],
        "probe_last_collection_started_at_ms":
            last_record["collection_started_at_ms"],
        "probe_last_exported_at_ms": last_record["watch_exported_at_ms"],
        "probe_last_record_sha256": last_record["record_sha256"],
        "probe_last_snapshot_body_sha256":
            last_record["snapshot_body_sha256"],
        "probe_history_record_bytes": head["history_record_bytes"],
        "probe_audit_cursor_sequence": head["audit_cursor_sequence"],
        "probe_audit_expected_previous_sha256":
            head["audit_expected_previous_sha256"],
        "sample_count": REQUIRED_RUNS,
        "collection_cadence_ms": CADENCE_MS,
        "maximum_collection_jitter_ms": MAXIMUM_JITTER_MS,
        "missed_sample_count": 0,
        "missed_decision_count": 0,
        "environment": environment,
        "paper_authorized": False,
        "live_authorized": False,
        "mutation_authorized": False,
        "direct_broker_access": False,
    }
    return {**body, "body_sha256": digest_bytes(canonical_bytes(body))}


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(allow_abbrev=False)
    parser.add_argument("--campaign-id", required=True)
    parser.add_argument("--prospective-campaign-id", required=True)
    parser.add_argument("--prospective-policy", required=True, type=Path)
    parser.add_argument("--authority-marker", required=True, type=Path)
    parser.add_argument("--host-receipt", required=True, type=Path)
    parser.add_argument("--observer-controller-status", required=True, type=Path)
    parser.add_argument("--observer-state", required=True, type=Path)
    parser.add_argument("--artifact-root", required=True, type=Path)
    parser.add_argument(
        "--boot-id", type=Path,
        default=BOOT_ID_PATH)
    parser.add_argument("--audit-journal", required=True, type=Path)
    parser.add_argument("--collector", required=True, type=Path)
    parser.add_argument("--exporter", required=True, type=Path)
    parser.add_argument("--heptactl", required=True, type=Path)
    parser.add_argument("--gateway", required=True, type=Path)
    parser.add_argument("--custodian", required=True, type=Path)
    parser.add_argument("--observer", required=True, type=Path)
    parser.add_argument("--host-controller", required=True, type=Path)
    parser.add_argument("--domain-config", required=True, type=Path)
    parser.add_argument("--gateway-profile", required=True, type=Path)
    parser.add_argument("--gateway-socket", type=Path, default=GATEWAY_SOCKET)
    parser.add_argument("--output", required=True, type=Path)
    return parser


def main() -> int:
    arguments = _parser().parse_args()
    try:
        _require(
            os.geteuid() == 0 and os.getegid() == 0,
            "P1_LOAD_PROBE_ROOT_REQUIRED",
        )
        environment = current_environment_binding(
            boot_id_path=arguments.boot_id,
            audit_journal=arguments.audit_journal,
            collector=arguments.collector,
            exporter=arguments.exporter,
            heptactl=arguments.heptactl,
            gateway=arguments.gateway,
            custodian=arguments.custodian,
            observer=arguments.observer,
            host_controller=arguments.host_controller,
            domain_config=arguments.domain_config,
            gateway_profile=arguments.gateway_profile,
            gateway_socket=arguments.gateway_socket,
        )
        receipt = validate(
            campaign_id=arguments.campaign_id,
            prospective_campaign_id=arguments.prospective_campaign_id,
            prospective_policy_path=
                arguments.prospective_policy.resolve(strict=False),
            authority_marker_path=
                arguments.authority_marker.resolve(strict=False),
            host_receipt_path=arguments.host_receipt,
            controller_status_path=arguments.observer_controller_status,
            observer_state_path=arguments.observer_state,
            artifact_root=arguments.artifact_root,
            environment=environment,
            now_ms=time.time_ns() // 1_000_000,
        )
        _write_root_exclusive(arguments.output, receipt)
    except (ValidationError, OSError, UnicodeError, ValueError) as error:
        reason = str(error)
        if re.fullmatch(r"[A-Z0-9_]{3,128}", reason) is None:
            reason = "P1_LOAD_PROBE_VALIDATION_FAILED"
        print(f"hepta-p1-load-probe-validator: FAIL {reason}", file=sys.stderr)
        return 78
    sys.stdout.buffer.write(canonical_bytes({
        "schema": "hepta.p1-shadow-load-probe-admission-result.v1",
        "status": "ADMISSION_RECEIPT_WRITTEN",
        "receipt_path": str(arguments.output),
        "receipt_body_sha256": receipt["body_sha256"],
        "paper_authorized": False,
        "live_authorized": False,
        "mutation_authorized": False,
        "direct_broker_access": False,
    }))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
