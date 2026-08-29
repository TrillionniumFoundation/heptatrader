#!/usr/bin/env python3

"""Narrow uid-1000 controller for one P1 SHADOW observer campaign."""

from __future__ import annotations

import argparse
from contextlib import contextmanager
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import signal
import stat
import subprocess
import sys
import tempfile
import time
from typing import Any, Callable, Iterator

from hepta_agent_trust_domain import (
    ALPHA_GATEWAY_PROCESS_PROFILE_BYTES,
    TrustDomainRuntimeError,
    read_alpha_gateway_process_identity,
    read_alpha_gateway_profile,
    read_alpha_gateway_socket,
)


EXPECTED_UID = 1000
EXPECTED_GID = 1000
ROOT_UID = 0
ROOT_GID = 0
EXPORT_FILES = (
    "snapshot.json",
    "shadow-watch-lease-receipt.json",
    "shadow-watch-export-receipt.json",
)
EXPORT_COMMIT_NAME = "current.json"
EXPORT_GENERATIONS_NAME = "generations"
EXPORT_GENERATION = re.compile(
    r"generation-([0-9]{20})-([A-Za-z0-9_-]{8,64})")
DIGEST = re.compile(r"sha256:[0-9a-f]{64}")
IDENTIFIER = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,127}")
PASS_LINE = re.compile(
    r"^hepta_bounded_shadow_observer: PASS "
    r"outcome=([A-Z_]+) status=([A-Z_]+) iterations=([0-9]+)$")
FAIL_LINE = re.compile(
    r"^hepta_bounded_shadow_observer: FAIL "
    r"([A-Z][A-Z0-9_]{0,127})$")
MAXIMUM_JSON_BYTES = 4 * 1024 * 1024
ADMISSION_MAXIMUM_AGE_MS = 60_000
LOAD_PROBE_MARKER_LIFETIME_MS = 20 * 60 * 1000
TERMINAL_HEARTBEAT_SECONDS = 5.0
EXPORT_LOSS_GRACE_SECONDS = 5.0
SYSTEMCTL = "/usr/bin/systemctl"
GATEWAY_UNIT = "hepta-tool-gateway@alpha.service"
GATEWAY_SOCKET = Path("/run/hepta-agent-alpha/tools.sock")
GATEWAY_PROFILE = Path("/etc/heptatrader/trust-domains/alpha.env")
BOOT_ID_PATH = Path("/proc/sys/kernel/random/boot_id")
BOOT_ID_MAXIMUM_BYTES = 128
RUNTIME_FILES = {
    "collector_sha256": Path("/usr/libexec/hepta-shadow-watch-collector"),
    "exporter_sha256": Path("/usr/libexec/hepta-shadow-watch-exporter"),
    "heptactl_sha256": Path("/usr/bin/heptactl"),
    "gateway_sha256": Path("/usr/libexec/hepta-tool-gatewayd"),
    "custodian_sha256": Path("/usr/libexec/hepta-shadow-watch-custodian"),
    "observer_sha256": Path("/usr/libexec/hepta_bounded_shadow_observer.py"),
    "host_controller_sha256":
        Path("/usr/libexec/hepta-p1-shadow-host-controller"),
}
BOOT_ID = re.compile(
    r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-"
    r"[0-9a-f]{4}-[0-9a-f]{12}")
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
ROOT_ATTESTED_ENVIRONMENT_FIELDS = frozenset({
    "audit_journal_device", "audit_journal_inode", "domain_config_sha256",
    "gateway_process_profile_sha256",
})
FORMAL_MARKER_FIELDS = frozenset({
    "schema", "version", "status", "campaign_id", "policy_path",
    "policy_file_sha256", "policy_body_sha256", "admission_receipt_path",
    "admission_receipt_file_sha256", "admission_receipt_body_sha256",
    "admitted_at_ms", "marker_created_at_ms", "expires_at_ms",
    "execution_service_epoch", "execution_service_fencing_generation",
    "environment", "paper_authorized", "live_authorized",
    "mutation_authorized", "direct_broker_access", "body_sha256",
})
LOAD_PROBE_MARKER_FIELDS = frozenset({
    "schema", "version", "status", "scope", "mode", "campaign_id",
    "policy_path", "policy_file_sha256", "policy_body_sha256",
    "marker_created_at_ms", "expires_at_ms", "execution_binding_status",
    "execution_service_epoch", "execution_service_fencing_generation",
    "environment", "paper_authorized", "live_authorized",
    "mutation_authorized", "direct_broker_access", "body_sha256",
})
EXPORT_FIELDS = frozenset({
    "schema", "version", "domain_id", "agent_uid", "reader_uid",
    "reader_gid", "boundary", "lease_generation",
    "lease_receipt_body_sha256", "lease_receipt_file_sha256",
    "snapshot_body_sha256", "snapshot_file_sha256",
    "snapshot_generated_at_ms", "exported_at_ms", "paper_authorized",
    "live_authorized", "mutation_attempted", "direct_broker_access",
    "body_sha256",
})
EXPORT_COMMIT_FIELDS = frozenset({
    "schema", "version", "authority_status", "authority_changed_at_ms",
    "close_reason", "commit_sequence", "generation", "domain_id",
    "agent_uid", "reader_uid", "reader_gid", "lease_generation",
    "snapshot_body_sha256", "snapshot_file_sha256",
    "lease_receipt_body_sha256", "lease_receipt_file_sha256",
    "export_receipt_body_sha256", "export_receipt_file_sha256",
    "committed_at_ms", "paper_authorized", "live_authorized",
    "mutation_attempted", "direct_broker_access", "body_sha256",
})


class ControllerError(RuntimeError):
    """Stable controller failure."""


class TripletNotReady(ControllerError):
    """A root publisher is between atomic member publications."""


class ExportAuthorityEnded(ControllerError):
    """The root custodian explicitly ended this export authority."""

    def __init__(self, status: str) -> None:
        super().__init__("P1_CONTROLLER_EXPORT_AUTHORITY_ENDED_" + status)
        self.status = status


def canonical_bytes(document: Any) -> bytes:
    return (
        json.dumps(
            document,
            ensure_ascii=True,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        + "\n"
    ).encode("ascii")


def digest_bytes(contents: bytes) -> str:
    return "sha256:" + hashlib.sha256(contents).hexdigest()


def _stable_file_identity(metadata: os.stat_result) -> tuple[int, ...]:
    return (
        metadata.st_dev, metadata.st_ino, metadata.st_mode,
        metadata.st_nlink, metadata.st_uid, metadata.st_gid,
        metadata.st_size, metadata.st_mtime_ns, metadata.st_ctime_ns,
    )


def _secure_read(
    path: Path,
    label: str,
    maximum_bytes: int = MAXIMUM_JSON_BYTES,
    *,
    expected_uid: int | None = None,
    expected_gid: int | None = None,
    allowed_modes: frozenset[int] | None = None,
) -> bytes:
    flags = os.O_RDONLY | os.O_CLOEXEC
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    try:
        descriptor = os.open(path, flags)
    except OSError as error:
        raise ControllerError(f"{label}_FILE_INVALID") from error
    try:
        metadata = os.fstat(descriptor)
        if (
                not stat.S_ISREG(metadata.st_mode) or metadata.st_nlink != 1 or
                not 1 <= metadata.st_size <= maximum_bytes or
                (expected_uid is not None and metadata.st_uid != expected_uid) or
                (expected_gid is not None and metadata.st_gid != expected_gid) or
                (allowed_modes is not None and
                 stat.S_IMODE(metadata.st_mode) not in allowed_modes)):
            raise ControllerError(f"{label}_FILE_INVALID")
        chunks: list[bytes] = []
        remaining = metadata.st_size
        while remaining:
            chunk = os.read(descriptor, min(remaining, 1024 * 1024))
            if not chunk:
                raise ControllerError(f"{label}_FILE_INVALID")
            chunks.append(chunk)
            remaining -= len(chunk)
        if os.read(descriptor, 1) != b"":
            raise ControllerError(f"{label}_FILE_INVALID")
        after = os.fstat(descriptor)
        path_after = os.stat(path, follow_symlinks=False)
        if (
                _stable_file_identity(metadata) !=
                _stable_file_identity(after) or
                _stable_file_identity(after) !=
                _stable_file_identity(path_after)):
            raise ControllerError(f"{label}_FILE_INVALID")
        return b"".join(chunks)
    except OSError as error:
        raise ControllerError(f"{label}_FILE_INVALID") from error
    finally:
        os.close(descriptor)


def _read_boot_id(path: Path, reason: str) -> str:
    """Read the canonical Linux boot ID through a rebound anchored path."""

    if path != BOOT_ID_PATH:
        raise ControllerError(reason)
    directory_flags = os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC
    file_flags = os.O_RDONLY | os.O_NONBLOCK | os.O_CLOEXEC
    no_follow = getattr(os, "O_NOFOLLOW", None)
    if no_follow is None:
        raise ControllerError(reason)
    directory_flags |= no_follow
    file_flags |= no_follow

    def stable(metadata: os.stat_result) -> tuple[int, ...]:
        return (
            metadata.st_dev, metadata.st_ino, metadata.st_mode,
            metadata.st_nlink, metadata.st_uid, metadata.st_gid,
            metadata.st_size, metadata.st_mtime_ns, metadata.st_ctime_ns,
        )

    def validate_directory(metadata: os.stat_result) -> None:
        if not (
                stat.S_ISDIR(metadata.st_mode) and metadata.st_uid == ROOT_UID and
                metadata.st_gid == ROOT_GID and
                stat.S_IMODE(metadata.st_mode) & 0o022 == 0):
            raise ControllerError(reason)

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
            if total > BOOT_ID_MAXIMUM_BYTES:
                raise ControllerError(reason)
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
        if not (
                stable(before_entry) == stable(opened) and
                stat.S_ISREG(opened.st_mode) and opened.st_uid == ROOT_UID and
                opened.st_gid == ROOT_GID and
                stat.S_IMODE(opened.st_mode) == 0o444 and
                opened.st_nlink == 1 and opened.st_size == 0 and
                opened.st_dev == os.fstat(parent_fd).st_dev):
            raise ControllerError(reason)
        contents = read_bounded(descriptor)
        after = os.fstat(descriptor)
        after_entry = os.stat(
            path.name, dir_fd=parent_fd, follow_symlinks=False)
        if not (
                stable(opened) == stable(after) == stable(after_entry) and
                parent_identity == stable(os.fstat(parent_fd))):
            raise ControllerError(reason)

        rebound_parent_fd = open_parent()
        rebound_parent_identity = stable(os.fstat(rebound_parent_fd))
        rebound_entry = os.stat(
            path.name, dir_fd=rebound_parent_fd, follow_symlinks=False)
        rebound_fd = os.open(
            path.name, file_flags, dir_fd=rebound_parent_fd)
        rebound_opened = os.fstat(rebound_fd)
        if not (
                parent_identity == rebound_parent_identity and
                stable(opened) == stable(rebound_entry) ==
                stable(rebound_opened)):
            raise ControllerError(reason)
        rebound_contents = read_bounded(rebound_fd)
        rebound_after = os.fstat(rebound_fd)
        rebound_after_entry = os.stat(
            path.name, dir_fd=rebound_parent_fd, follow_symlinks=False)
        if not (
                contents == rebound_contents and
                stable(rebound_opened) == stable(rebound_after) ==
                stable(rebound_after_entry) and
                rebound_parent_identity == stable(os.fstat(rebound_parent_fd))):
            raise ControllerError(reason)
        if len(contents) != 37 or not contents.endswith(b"\n"):
            raise ControllerError(reason)
        boot_id = contents[:-1].decode("ascii")
        if BOOT_ID.fullmatch(boot_id) is None:
            raise ControllerError(reason)
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


def _live_gateway_identity(
        profile_before: Any,
        gateway_socket: Path = GATEWAY_SOCKET) -> dict[str, Any]:
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
            raise ControllerError(
                "P1_CONTROLLER_GATEWAY_IDENTITY_INVALID") from error
        parsed: dict[str, str] = {}
        for line in completed.stdout.splitlines():
            key, separator, value = line.partition("=")
            if separator != "=" or key in parsed:
                raise ControllerError(
                    "P1_CONTROLLER_GATEWAY_IDENTITY_INVALID")
            parsed[key] = value
        if not (
                completed.returncode == 0 and completed.stderr == "" and
                parsed.get("ActiveState") == "active" and
                parsed.get("SubState") == "running" and
                re.fullmatch(
                    r"[0-9a-f]{32}", parsed.get("InvocationID", "")) and
                parsed.get("MainPID", "").isdigit() and
                int(parsed["MainPID"]) > 1 and
                parsed.get("ExecMainStartTimestampMonotonic", "").isdigit() and
                int(parsed["ExecMainStartTimestampMonotonic"]) > 0):
            raise ControllerError("P1_CONTROLLER_GATEWAY_IDENTITY_INVALID")
        return parsed

    parsed = status()
    try:
        process_before = read_alpha_gateway_process_identity(
            int(parsed["MainPID"]))
        socket_before = read_alpha_gateway_socket(gateway_socket)
        profile_after = read_alpha_gateway_profile(GATEWAY_PROFILE)
        parsed_after = status()
        process_after = read_alpha_gateway_process_identity(
            int(parsed["MainPID"]))
        socket_after = read_alpha_gateway_socket(gateway_socket)
    except TrustDomainRuntimeError as error:
        raise ControllerError("P1_CONTROLLER_GATEWAY_REBIND_INVALID") from error
    if (profile_before != profile_after or parsed_after != parsed or
            process_before.pid_directory_metadata !=
            process_after.pid_directory_metadata or
            process_before.starttime_ticks != process_after.starttime_ticks or
            socket_before != socket_after):
        raise ControllerError("P1_CONTROLLER_GATEWAY_IDENTITY_CHANGED")
    return {
        "gateway_invocation_id": parsed["InvocationID"],
        "gateway_main_pid": int(parsed["MainPID"]),
        "gateway_exec_main_start_timestamp_monotonic_us":
            int(parsed["ExecMainStartTimestampMonotonic"]),
        "gateway_socket_device": socket_before.metadata[0],
        "gateway_socket_inode": socket_before.metadata[1],
    }


def _valid_root_attestation(environment: Any) -> bool:
    return (
        isinstance(environment, dict) and
        type(environment.get("audit_journal_device")) is int and
        0 <= environment["audit_journal_device"] < (1 << 64) and
        type(environment.get("audit_journal_inode")) is int and
        1 <= environment["audit_journal_inode"] < (1 << 64) and
        isinstance(environment.get("domain_config_sha256"), str) and
        DIGEST.fullmatch(environment["domain_config_sha256"]) is not None and
        isinstance(environment.get("gateway_process_profile_sha256"), str) and
        environment["gateway_process_profile_sha256"] ==
        digest_bytes(ALPHA_GATEWAY_PROCESS_PROFILE_BYTES)
    )


def current_environment_binding(
    *,
    audit_journal_device: int,
    audit_journal_inode: int,
    domain_config_sha256: str,
    gateway_process_profile_sha256: str,
) -> dict[str, Any]:
    root_attestation = {
        "audit_journal_device": audit_journal_device,
        "audit_journal_inode": audit_journal_inode,
        "domain_config_sha256": domain_config_sha256,
        "gateway_process_profile_sha256": gateway_process_profile_sha256,
    }
    if not _valid_root_attestation(root_attestation):
        raise ControllerError("P1_CONTROLLER_ENVIRONMENT_INVALID")
    try:
        boot_id = _read_boot_id(
            BOOT_ID_PATH, "P1_CONTROLLER_BOOT_ID_FILE_INVALID")
    except (OSError, UnicodeError) as error:
        raise ControllerError("P1_CONTROLLER_ENVIRONMENT_INVALID") from error
    if BOOT_ID.fullmatch(boot_id) is None:
        raise ControllerError("P1_CONTROLLER_ENVIRONMENT_INVALID")
    binding: dict[str, Any] = {
        "boot_id": boot_id,
        **root_attestation,
    }
    for name, path in RUNTIME_FILES.items():
        binding[name] = digest_bytes(_secure_read(
            path, "P1_CONTROLLER_RUNTIME", 64 * 1024 * 1024))
    try:
        profile_before = read_alpha_gateway_profile(GATEWAY_PROFILE)
    except TrustDomainRuntimeError as error:
        raise ControllerError("P1_CONTROLLER_GATEWAY_PROFILE_INVALID") from error
    binding["gateway_profile_sha256"] = digest_bytes(profile_before.raw)
    binding.update(_live_gateway_identity(profile_before))
    if set(binding) != ENVIRONMENT_FIELDS:
        raise ControllerError("P1_CONTROLLER_ENVIRONMENT_INVALID")
    return binding


def _pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ControllerError("P1_CONTROLLER_DUPLICATE_JSON_KEY")
        result[key] = value
    return result


def _document(contents: bytes, label: str) -> dict[str, Any]:
    try:
        document = json.loads(contents, object_pairs_hook=_pairs)
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ControllerError(f"{label}_INVALID") from error
    if not isinstance(document, dict) or canonical_bytes(document) != contents:
        raise ControllerError(f"{label}_CANONICAL_INVALID")
    body = dict(document)
    claimed = body.pop("body_sha256", None)
    if (
            not isinstance(claimed, str) or
            DIGEST.fullmatch(claimed) is None or
            claimed != digest_bytes(canonical_bytes(body))):
        raise ControllerError(f"{label}_DIGEST_INVALID")
    return document


def _document_path(
    path: Path,
    label: str,
    *,
    expected_uid: int | None = None,
) -> tuple[dict[str, Any], bytes]:
    contents = _secure_read(
        path,
        label,
        expected_uid=expected_uid,
        expected_gid=0 if expected_uid == 0 else None,
        allowed_modes=(frozenset({0o640, 0o644})
                       if expected_uid == 0 else None),
    )
    return _document(contents, label), contents


def _load_authority_marker(
    marker_path: Path,
    policy_path: Path,
    campaign_id: str,
    *,
    now_ms: int,
    expected_uid: int = 0,
    require_fresh_admission: bool = True,
) -> dict[str, Any]:
    marker, _ = _document_path(
        marker_path, "P1_CONTROLLER_AUTHORITY_MARKER",
        expected_uid=expected_uid)
    policy, policy_contents = _document_path(
        policy_path, "P1_CONTROLLER_POLICY", expected_uid=expected_uid)
    common = (
        type(marker.get("version")) is int and
        marker.get("version") == 1 and marker.get("status") == "ACTIVE" and
        marker.get("campaign_id") == campaign_id and
        marker.get("policy_path") == str(policy_path) and
        marker.get("policy_file_sha256") == digest_bytes(policy_contents) and
        marker.get("policy_body_sha256") == policy.get("body_sha256") and
        policy.get("schema") ==
        "hepta.strategy-shadow-observation-policy.v1" and
        policy.get("version") == 1 and
        policy.get("campaign_id") == campaign_id and
        isinstance(marker.get("environment"), dict) and
        set(marker["environment"]) == ENVIRONMENT_FIELDS and
        _valid_root_attestation(marker["environment"]) and
        marker.get("paper_authorized") is False and
        marker.get("live_authorized") is False and
        marker.get("mutation_authorized") is False and
        marker.get("direct_broker_access") is False
    )
    epoch = marker.get("execution_service_epoch")
    fencing = marker.get("execution_service_fencing_generation")
    if marker.get("schema") == "hepta.p1-shadow-admission-authority-marker.v1":
        admitted = marker.get("admitted_at_ms")
        created = marker.get("marker_created_at_ms")
        expires = marker.get("expires_at_ms")
        policy_expires = policy.get("expires_at_ms")
        valid = (
            set(marker) == FORMAL_MARKER_FIELDS and common and
            isinstance(epoch, str) and 1 <= len(epoch) <= 256 and
            type(fencing) is int and 1 <= fencing < (1 << 64) and
            type(now_ms) is int and type(admitted) is int and
            type(created) is int and type(expires) is int and
            type(policy_expires) is int and
            0 < admitted <= created <= now_ms < expires and
            created - admitted <= ADMISSION_MAXIMUM_AGE_MS and
            expires == policy_expires and
            (not require_fresh_admission or
             now_ms - admitted <= ADMISSION_MAXIMUM_AGE_MS))
    elif marker.get("schema") == (
            "hepta.p1-shadow-load-probe-authority-marker.v1"):
        created = marker.get("marker_created_at_ms")
        expires = marker.get("expires_at_ms")
        binding_status = marker.get("execution_binding_status")
        pending = (
            binding_status == "PENDING_FIRST_SNAPSHOT" and
            epoch is None and fencing is None)
        valid = (
            set(marker) == LOAD_PROBE_MARKER_FIELDS and common and
            marker.get("scope") == "LOAD_PROBE" and
            marker.get("mode") == "LOAD_PROBE" and
            type(created) is int and type(expires) is int and
            created <= now_ms <= expires and
            expires - created == LOAD_PROBE_MARKER_LIFETIME_MS and pending)
    else:
        valid = False
    if not valid:
        raise ControllerError("P1_CONTROLLER_AUTHORITY_MARKER_INVALID")
    return marker


def _assert_live_environment(
    marker: dict[str, Any],
    provider: Callable[[], dict[str, Any]] | None,
) -> None:
    try:
        environment = marker.get("environment")
        if provider is None:
            if not _valid_root_attestation(environment):
                raise ControllerError(
                    "P1_CONTROLLER_AUTHORITY_MARKER_INVALID")
            current = current_environment_binding(
                audit_journal_device=environment["audit_journal_device"],
                audit_journal_inode=environment["audit_journal_inode"],
                domain_config_sha256=environment["domain_config_sha256"],
                gateway_process_profile_sha256=
                    environment["gateway_process_profile_sha256"],
            )
        else:
            current = provider()
    except ControllerError:
        raise
    except Exception as error:
        raise ControllerError("P1_CONTROLLER_ENVIRONMENT_INVALID") from error
    if set(current) != ENVIRONMENT_FIELDS or current != marker.get("environment"):
        raise ControllerError("P1_CONTROLLER_GATEWAY_IDENTITY_DRIFT")


def _snapshot_execution_binding(snapshot: dict[str, Any]) -> tuple[str, int]:
    reads = snapshot.get("reads")
    health = reads.get("system.get_health") if isinstance(reads, dict) else None
    epoch = health.get("execution_service_epoch") if isinstance(health, dict) else None
    fencing = (
        health.get("execution_service_fencing_generation")
        if isinstance(health, dict) else None)
    if not (
            isinstance(epoch, str) and 1 <= len(epoch) <= 256 and
            type(fencing) is int and 1 <= fencing < (1 << 64)):
        raise ControllerError("P1_CONTROLLER_SNAPSHOT_EXECUTION_BINDING_INVALID")
    return epoch, fencing


def _assert_observer_continuity(
    artifact_root: Path,
    *,
    campaign_id: str,
    observer_outcome: str,
) -> None:
    if observer_outcome == "SEGMENT_CLOSED":
        raise ControllerError("P1_CONTROLLER_SEGMENT_CLOSED")
    try:
        state = _document(
            _secure_read(
                artifact_root / "observer-state.json",
                "P1_CONTROLLER_OBSERVER_STATE"),
            "P1_CONTROLLER_OBSERVER_STATE",
        )
    except ControllerError as error:
        raise ControllerError(
            "P1_CONTROLLER_OBSERVER_STATE_MISSING") from error
    if (
            state.get("schema") !=
            "hepta.bounded-shadow-observer-state.v1" or
            state.get("version") != 1 or
            state.get("campaign_id") != campaign_id or
            state.get("paper_authorized") is not False or
            state.get("live_authorized") is not False or
            state.get("mutation_attempted") is not False or
            state.get("direct_broker_access") is not False):
        raise ControllerError("P1_CONTROLLER_OBSERVER_STATE_INVALID")
    missed_sample_count = state.get("missed_sample_count")
    if (
            type(missed_sample_count) is not int or
            missed_sample_count < 0):
        raise ControllerError("P1_CONTROLLER_MISSED_SAMPLE_COUNT_INVALID")
    if missed_sample_count != 0:
        raise ControllerError(
            "P1_CONTROLLER_MISSED_SAMPLE_COUNT_NONZERO")
    if state.get("segment_status") == "CLOSED":
        raise ControllerError("P1_CONTROLLER_SEGMENT_CLOSED")
    if state.get("segment_status") != "OPEN":
        raise ControllerError("P1_CONTROLLER_SEGMENT_STATUS_INVALID")


def _directory_identity(metadata: os.stat_result) -> tuple[int, ...]:
    return (
        metadata.st_dev, metadata.st_ino, metadata.st_mode,
        metadata.st_uid, metadata.st_gid,
    )


def _stable_directory_identity(metadata: os.stat_result) -> tuple[int, ...]:
    return (
        metadata.st_dev, metadata.st_ino, metadata.st_mode,
        metadata.st_uid, metadata.st_gid,
        metadata.st_mtime_ns, metadata.st_ctime_ns,
    )


def _validate_reader_directory(
        path: Path,
        metadata: os.stat_result,
        *,
        label: str,
) -> None:
    if (
            not stat.S_ISDIR(metadata.st_mode) or
            stat.S_ISLNK(metadata.st_mode) or
            metadata.st_uid != ROOT_UID or metadata.st_gid != EXPECTED_GID or
            stat.S_IMODE(metadata.st_mode) != 0o750):
        raise ControllerError(label + "_INVALID")


def _valid_active_commit(commit: dict[str, Any]) -> bool:
    sequence = commit.get("commit_sequence")
    generation = commit.get("generation")
    return (
        set(commit) == EXPORT_COMMIT_FIELDS and
        commit.get("schema") == "hepta.shadow-watch-export-commit.v1" and
        commit.get("version") == 1 and
        commit.get("authority_status") == "ACTIVE" and
        type(commit.get("authority_changed_at_ms")) is int and
        int(commit["authority_changed_at_ms"]) >= 0 and
        commit.get("close_reason") is None and
        type(sequence) is int and 1 <= sequence < (1 << 64) and
        isinstance(generation, str) and
        EXPORT_GENERATION.fullmatch(generation) is not None and
        int(EXPORT_GENERATION.fullmatch(generation).group(1)) == sequence and
        isinstance(commit.get("domain_id"), str) and
        bool(commit["domain_id"]) and
        type(commit.get("agent_uid")) is int and commit["agent_uid"] > 0 and
        commit.get("reader_uid") == EXPECTED_UID and
        commit.get("reader_gid") == EXPECTED_GID and
        type(commit.get("lease_generation")) is int and
        commit["lease_generation"] >= 1 and
        all(
            isinstance(commit.get(field), str) and
            DIGEST.fullmatch(commit[field]) is not None
            for field in (
                "snapshot_body_sha256", "snapshot_file_sha256",
                "lease_receipt_body_sha256", "lease_receipt_file_sha256",
                "export_receipt_body_sha256", "export_receipt_file_sha256",
            )) and
        type(commit.get("committed_at_ms")) is int and
        commit["committed_at_ms"] >= 0 and
        commit.get("paper_authorized") is False and
        commit.get("live_authorized") is False and
        commit.get("mutation_attempted") is False and
        commit.get("direct_broker_access") is False
    )


def _commit_authority_status(commit: dict[str, Any]) -> str:
    status_value = commit.get("authority_status")
    if (
            set(commit) != EXPORT_COMMIT_FIELDS or
            commit.get("schema") != "hepta.shadow-watch-export-commit.v1" or
            commit.get("version") != 1 or
            status_value not in {"ACTIVE", "CLOSING", "CLOSED"} or
            type(commit.get("authority_changed_at_ms")) is not int or
            commit["authority_changed_at_ms"] < 0 or
            type(commit.get("commit_sequence")) is not int or
            not 1 <= commit["commit_sequence"] < (1 << 64) or
            not isinstance(commit.get("domain_id"), str) or
            not commit["domain_id"] or
            type(commit.get("agent_uid")) is not int or
            commit["agent_uid"] <= 0 or
            commit.get("reader_uid") != EXPECTED_UID or
            commit.get("reader_gid") != EXPECTED_GID or
            commit.get("paper_authorized") is not False or
            commit.get("live_authorized") is not False or
            commit.get("mutation_attempted") is not False or
            commit.get("direct_broker_access") is not False):
        raise ControllerError("P1_CONTROLLER_EXPORT_COMMIT_INVALID")
    if status_value != "ACTIVE":
        if (
                commit.get("generation") is not None or
                type(commit.get("lease_generation")) is not int or
                commit["lease_generation"] < 1 or
                not isinstance(
                    commit.get("lease_receipt_body_sha256"), str) or
                DIGEST.fullmatch(commit["lease_receipt_body_sha256"])
                is None or
                any(
                    commit.get(field) is not None
                    for field in (
                        "snapshot_body_sha256", "snapshot_file_sha256",
                        "lease_receipt_file_sha256",
                        "export_receipt_body_sha256",
                        "export_receipt_file_sha256", "committed_at_ms",
                    )) or
                not isinstance(commit.get("close_reason"), str) or
                not commit["close_reason"]):
            raise ControllerError("P1_CONTROLLER_EXPORT_COMMIT_INVALID")
    return str(status_value)


def read_stable_triplet(export_directory: Path) -> dict[str, Any] | None:
    flags = (
        os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC |
        getattr(os, "O_NOFOLLOW", 0)
    )
    try:
        directory_fd = os.open(export_directory, flags)
    except FileNotFoundError:
        return None
    except OSError as error:
        raise ControllerError(
            "P1_CONTROLLER_EXPORT_DIRECTORY_INVALID") from error
    try:
        opened = os.fstat(directory_fd)
        named = export_directory.lstat()
        _validate_reader_directory(
            export_directory, opened,
            label="P1_CONTROLLER_EXPORT_DIRECTORY")
        if _directory_identity(opened) != _directory_identity(named):
            raise ControllerError("P1_CONTROLLER_EXPORT_DIRECTORY_CHANGED")
        fcntl.flock(directory_fd, fcntl.LOCK_SH)
        locked_directory = os.fstat(directory_fd)
        current_path = export_directory / EXPORT_COMMIT_NAME
        try:
            first_current_before = current_path.lstat()
        except FileNotFoundError:
            return None
        try:
            first_commit_contents = _secure_read(
                current_path,
                "P1_CONTROLLER_EXPORT_COMMIT",
                expected_uid=ROOT_UID,
                expected_gid=EXPECTED_GID,
                allowed_modes=frozenset({0o440}),
            )
            first_current_after = current_path.lstat()
        except ControllerError as error:
            if (
                    str(error) == "P1_CONTROLLER_EXPORT_COMMIT_FILE_INVALID" and
                    not os.path.lexists(current_path)):
                return None
            raise
        commit = _document(
            first_commit_contents, "P1_CONTROLLER_EXPORT_COMMIT")
        status_value = _commit_authority_status(commit)
        if status_value != "ACTIVE":
            raise ExportAuthorityEnded(status_value)
        if not _valid_active_commit(commit):
            raise ControllerError("P1_CONTROLLER_EXPORT_COMMIT_INVALID")
        generations = export_directory / EXPORT_GENERATIONS_NAME
        try:
            generations_metadata = generations.lstat()
        except OSError as error:
            raise TripletNotReady(
                "P1_CONTROLLER_EXPORT_GENERATION_NOT_READY") from error
        _validate_reader_directory(
            generations, generations_metadata,
            label="P1_CONTROLLER_EXPORT_GENERATIONS")
        generation = generations / str(commit["generation"])
        try:
            generation_before = generation.lstat()
        except OSError as error:
            raise TripletNotReady(
                "P1_CONTROLLER_EXPORT_GENERATION_NOT_READY") from error
        _validate_reader_directory(
            generation, generation_before,
            label="P1_CONTROLLER_EXPORT_GENERATION")
        if set(os.listdir(generation)) != set(EXPORT_FILES):
            raise ControllerError(
                "P1_CONTROLLER_EXPORT_GENERATION_INVENTORY_INVALID")
        paths = tuple(generation / name for name in EXPORT_FILES)
        contents = tuple(
            _secure_read(
                path,
                label,
                expected_uid=ROOT_UID,
                expected_gid=EXPECTED_GID,
                allowed_modes=frozenset({0o440}),
            )
            for path, label in zip(
                paths,
                (
                    "P1_CONTROLLER_SNAPSHOT",
                    "P1_CONTROLLER_LEASE",
                    "P1_CONTROLLER_EXPORT",
                ),
                strict=True,
            )
        )
        second_commit_contents = _secure_read(
            current_path,
            "P1_CONTROLLER_EXPORT_COMMIT",
            expected_uid=ROOT_UID,
            expected_gid=EXPECTED_GID,
            allowed_modes=frozenset({0o440}),
        )
        second_current_after = current_path.lstat()
        generation_after = generation.lstat()
        final_directory = os.fstat(directory_fd)
        if (
                first_commit_contents != second_commit_contents or
                _stable_file_identity(first_current_before) !=
                _stable_file_identity(first_current_after) or
                _stable_file_identity(first_current_after) !=
                _stable_file_identity(second_current_after) or
                _directory_identity(generation_before) !=
                _directory_identity(generation_after) or
                _stable_directory_identity(locked_directory) !=
                _stable_directory_identity(final_directory)):
            raise TripletNotReady(
                "P1_CONTROLLER_EXPORT_COMMIT_IN_PROGRESS")
    except OSError as error:
        raise ControllerError("P1_CONTROLLER_EXPORT_READ_FAILED") from error
    finally:
        try:
            fcntl.flock(directory_fd, fcntl.LOCK_UN)
        finally:
            os.close(directory_fd)

    snapshot_contents, lease_contents, export_contents = contents
    snapshot = _document(snapshot_contents, "P1_CONTROLLER_SNAPSHOT")
    lease = _document(lease_contents, "P1_CONTROLLER_LEASE")
    export = _document(export_contents, "P1_CONTROLLER_EXPORT")
    if (
            set(export) != EXPORT_FIELDS or
            export.get("schema") != "hepta.shadow-watch-export-receipt.v1" or
            export.get("version") != 1 or
            export.get("boundary") != "WATCH_EXPORT" or
            export.get("reader_uid") != EXPECTED_UID or
            export.get("reader_gid") != EXPECTED_GID or
            export.get("paper_authorized") is not False or
            export.get("live_authorized") is not False or
            export.get("mutation_attempted") is not False or
            export.get("direct_broker_access") is not False):
        raise ControllerError("P1_CONTROLLER_EXPORT_CONTRACT_INVALID")
    if (
            commit.get("snapshot_file_sha256") !=
            digest_bytes(snapshot_contents) or
            commit.get("lease_receipt_file_sha256") !=
            digest_bytes(lease_contents) or
            commit.get("export_receipt_file_sha256") !=
            digest_bytes(export_contents) or
            commit.get("snapshot_body_sha256") !=
            snapshot.get("body_sha256") or
            commit.get("lease_receipt_body_sha256") !=
            lease.get("body_sha256") or
            commit.get("export_receipt_body_sha256") !=
            export.get("body_sha256") or
            export.get("snapshot_file_sha256") !=
            digest_bytes(snapshot_contents) or
            export.get("lease_receipt_file_sha256") !=
            digest_bytes(lease_contents) or
            export.get("snapshot_body_sha256") !=
            snapshot.get("body_sha256") or
            export.get("lease_receipt_body_sha256") !=
            lease.get("body_sha256") or
            export.get("snapshot_generated_at_ms") !=
            snapshot.get("generated_at_ms") or
            export.get("lease_generation") !=
            lease.get("lease_generation") or
            commit.get("lease_generation") != export.get("lease_generation") or
            commit.get("domain_id") != export.get("domain_id") or
            export.get("domain_id") != snapshot.get("domain_id") or
            export.get("domain_id") != lease.get("domain_id") or
            commit.get("agent_uid") != export.get("agent_uid") or
            export.get("agent_uid") != snapshot.get("agent_uid") or
            export.get("agent_uid") != lease.get("agent_uid")):
        raise ControllerError("P1_CONTROLLER_EXPORT_BINDING_INVALID")
    epoch, fencing = _snapshot_execution_binding(snapshot)
    return {
        "identity": commit["body_sha256"],
        "export_receipt_identity": export["body_sha256"],
        "commit_sequence": commit["commit_sequence"],
        "generation": commit["generation"],
        "snapshot_body_sha256": snapshot["body_sha256"],
        "lease_generation": export["lease_generation"],
        "snapshot_generated_at_ms": export["snapshot_generated_at_ms"],
        "exported_at_ms": export["exported_at_ms"],
        "execution_service_epoch": epoch,
        "execution_service_fencing_generation": fencing,
        "paths": paths,
        "contents": contents,
    }


def _status_body(
    *,
    campaign_id: str,
    state: str,
    started_at_ms: int,
    invocations: int,
    last_triplet: dict[str, Any] | None,
    observer_status: str | None,
    observer_outcome: str | None,
    completed_iterations: int,
    reason: str | None,
) -> dict[str, Any]:
    return {
        "schema": "hepta.p1-shadow-observer-controller-status.v1",
        "version": 1,
        "campaign_id": campaign_id,
        "controller_pid": os.getpid(),
        "controller_uid": os.geteuid(),
        "controller_gid": os.getegid(),
        "state": state,
        "started_at_ms": started_at_ms,
        "updated_at_ms": time.time_ns() // 1_000_000,
        "observer_invocations": invocations,
        "last_export_receipt_body_sha256": (
            None if last_triplet is None else
            last_triplet["export_receipt_identity"]),
        "last_snapshot_body_sha256": (
            None if last_triplet is None else
            last_triplet["snapshot_body_sha256"]),
        "last_lease_generation": (
            None if last_triplet is None else
            last_triplet["lease_generation"]),
        "locked_execution_service_epoch": (
            None if last_triplet is None else
            last_triplet["execution_service_epoch"]),
        "locked_execution_service_fencing_generation": (
            None if last_triplet is None else
            last_triplet["execution_service_fencing_generation"]),
        "observer_status": observer_status,
        "observer_outcome": observer_outcome,
        "completed_iterations": completed_iterations,
        "reason": reason,
        "paper_authorized": False,
        "live_authorized": False,
        "mutation_attempted": False,
        "direct_broker_access": False,
    }


def atomic_status(path: Path, body: dict[str, Any]) -> None:
    document = {**body, "body_sha256": digest_bytes(canonical_bytes(body))}
    contents = canonical_bytes(document)
    parent = path.parent
    parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    parent_metadata = parent.stat()
    if (
            parent.is_symlink() or
            not stat.S_ISDIR(parent_metadata.st_mode) or
            parent_metadata.st_uid != EXPECTED_UID or
            parent_metadata.st_gid != EXPECTED_GID or
            stat.S_IMODE(parent_metadata.st_mode) & 0o077):
        raise ControllerError("P1_CONTROLLER_STATUS_DIRECTORY_INVALID")
    descriptor, name = tempfile.mkstemp(prefix=".p1-status-", dir=parent)
    temporary = Path(name)
    try:
        os.fchmod(descriptor, 0o600)
        with os.fdopen(descriptor, "wb", closefd=True) as output:
            output.write(contents)
            output.flush()
            os.fsync(output.fileno())
        os.replace(temporary, path)
        os.chmod(path, 0o600)
        directory_fd = os.open(parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
    finally:
        temporary.unlink(missing_ok=True)


def atomic_failure_status(
    path: Path,
    *,
    campaign_id: str,
    reason: str,
) -> None:
    body: dict[str, Any] | None = None
    try:
        existing = _document(
            _secure_read(path, "P1_CONTROLLER_EXISTING_STATUS"),
            "P1_CONTROLLER_EXISTING_STATUS")
        if (
                existing.get("schema") ==
                "hepta.p1-shadow-observer-controller-status.v1" and
                existing.get("campaign_id") == campaign_id and
                existing.get("controller_pid") == os.getpid()):
            body = {
                key: value for key, value in existing.items()
                if key != "body_sha256"
            }
            body.update({
                "state": "FAILED",
                "updated_at_ms": time.time_ns() // 1_000_000,
                "reason": reason,
            })
    except (ControllerError, OSError):
        body = None
    if body is None:
        body = _status_body(
            campaign_id=campaign_id,
            state="FAILED",
            started_at_ms=time.time_ns() // 1_000_000,
            invocations=0,
            last_triplet=None,
            observer_status=None,
            observer_outcome=None,
            completed_iterations=0,
            reason=reason,
        )
    atomic_status(path, body)


def _is_within(path: Path, directory: Path) -> bool:
    try:
        return os.path.commonpath(
            (str(path.resolve()), str(directory.resolve()))
        ) == str(directory.resolve())
    except ValueError:
        return False


@contextmanager
def _pinned_triplet(
        export_directory: Path,
        triplet: dict[str, Any],
) -> Iterator[tuple[Path, Path, Path]]:
    """Hold the cooperative generation lock across observer consumption."""

    expected_contents = triplet.get("contents")
    if (
            not isinstance(expected_contents, tuple) or
            len(expected_contents) != 3 or
            not all(isinstance(item, bytes) for item in expected_contents)):
        raise ControllerError("P1_CONTROLLER_EXPORT_CONTENTS_INVALID")
    flags = (
        os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC |
        getattr(os, "O_NOFOLLOW", 0)
    )
    try:
        descriptor = os.open(export_directory, flags)
    except OSError as error:
        raise TripletNotReady(
            "P1_CONTROLLER_EXPORT_PIN_FAILED") from error
    try:
        opened = os.fstat(descriptor)
        named = export_directory.lstat()
        _validate_reader_directory(
            export_directory, opened,
            label="P1_CONTROLLER_EXPORT_DIRECTORY")
        if _directory_identity(opened) != _directory_identity(named):
            raise TripletNotReady("P1_CONTROLLER_EXPORT_PIN_FAILED")
        fcntl.flock(descriptor, fcntl.LOCK_SH)
        commit_contents = _secure_read(
            export_directory / EXPORT_COMMIT_NAME,
            "P1_CONTROLLER_EXPORT_COMMIT",
            expected_uid=ROOT_UID,
            expected_gid=EXPECTED_GID,
            allowed_modes=frozenset({0o440}),
        )
        commit = _document(
            commit_contents, "P1_CONTROLLER_EXPORT_COMMIT")
        if (
                _commit_authority_status(commit) != "ACTIVE" or
                not _valid_active_commit(commit) or
                commit.get("body_sha256") != triplet.get("identity") or
                commit.get("generation") != triplet.get("generation")):
            raise TripletNotReady("P1_CONTROLLER_EXPORT_PIN_DRIFT")
        generation = (
            export_directory / EXPORT_GENERATIONS_NAME /
            str(commit["generation"]))
        generation_metadata = generation.lstat()
        _validate_reader_directory(
            generation, generation_metadata,
            label="P1_CONTROLLER_EXPORT_GENERATION")
        if set(os.listdir(generation)) != set(EXPORT_FILES):
            raise ControllerError(
                "P1_CONTROLLER_EXPORT_GENERATION_INVENTORY_INVALID")
        paths = tuple(generation / name for name in EXPORT_FILES)
        if paths != triplet.get("paths"):
            raise TripletNotReady("P1_CONTROLLER_EXPORT_PIN_DRIFT")
        observed_contents = tuple(
            _secure_read(
                path,
                "P1_CONTROLLER_PINNED_EXPORT",
                expected_uid=ROOT_UID,
                expected_gid=EXPECTED_GID,
                allowed_modes=frozenset({0o440}),
            )
            for path in paths
        )
        if observed_contents != expected_contents:
            raise TripletNotReady("P1_CONTROLLER_EXPORT_PIN_DRIFT")
        yield paths
    finally:
        try:
            fcntl.flock(descriptor, fcntl.LOCK_UN)
        finally:
            os.close(descriptor)


def _invoke_observer(
    *,
    observer: Path,
    campaign_id: str,
    policy: Path,
    strategy: Path,
    triplet: tuple[Path, Path, Path],
    source_bundle: Path,
    artifact_root: Path,
    timeout_seconds: int,
) -> tuple[str, str, int]:
    command = [
        str(observer),
        "--campaign-id", campaign_id,
        "--policy", str(policy),
        "--strategy", str(strategy),
        "--snapshot", str(triplet[0]),
        "--watch-lease-receipt", str(triplet[1]),
        "--watch-export-receipt", str(triplet[2]),
        "--source-bundle", str(source_bundle),
        "--artifact-root", str(artifact_root),
    ]
    environment = {
        "PATH": "/usr/bin:/bin",
        "LANG": "C",
        "LC_ALL": "C",
        "PYTHONNOUSERSITE": "1",
    }
    try:
        result = subprocess.run(
            command,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=timeout_seconds,
            check=False,
            cwd="/",
            env=environment,
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        raise ControllerError("P1_CONTROLLER_OBSERVER_EXEC_FAILED") from error
    if result.returncode != 0:
        failure_lines = [
            line for line in result.stderr.splitlines() if line
        ]
        failure = (
            FAIL_LINE.fullmatch(failure_lines[0])
            if len(failure_lines) == 1 else None
        )
        reason_suffix = (
            "" if failure is None else "_" + failure.group(1)
        )
        raise ControllerError(
            "P1_CONTROLLER_OBSERVER_FAILED_"
            + str(result.returncode)
            + reason_suffix)
    parsed = None
    for line in result.stdout.splitlines():
        match = PASS_LINE.fullmatch(line)
        if match is not None:
            parsed = match
    if parsed is None:
        raise ControllerError("P1_CONTROLLER_OBSERVER_OUTPUT_INVALID")
    return parsed.group(1), parsed.group(2), int(parsed.group(3))


def run_controller(
    *,
    campaign_id: str,
    policy: Path,
    strategy: Path,
    export_directory: Path,
    source_bundle: Path,
    artifact_root: Path,
    status_path: Path,
    observer: Path,
    authority_marker: Path,
    poll_ms: int = 250,
    observer_timeout_seconds: int = 300,
    _maximum_polls: int | None = None,
    _sleeper: Callable[[float], None] = time.sleep,
    _expected_marker_uid: int = 0,
    _environment_provider: Callable[[], dict[str, Any]] | None = None,
    _terminal_hold_stop_requested: Callable[[], bool] | None = None,
) -> int:
    if os.geteuid() != EXPECTED_UID or os.getegid() != EXPECTED_GID:
        raise ControllerError("P1_CONTROLLER_IDENTITY_INVALID")
    if IDENTIFIER.fullmatch(campaign_id) is None:
        raise ControllerError("P1_CONTROLLER_CAMPAIGN_ID_INVALID")
    if _is_within(status_path, artifact_root):
        raise ControllerError("P1_CONTROLLER_STATUS_INSIDE_ARTIFACT_ROOT")
    if isinstance(poll_ms, bool) or not 50 <= poll_ms <= 10_000:
        raise ControllerError("P1_CONTROLLER_POLL_INVALID")
    if (
            isinstance(observer_timeout_seconds, bool) or
            not 1 <= observer_timeout_seconds <= 3600):
        raise ControllerError("P1_CONTROLLER_TIMEOUT_INVALID")
    initial_marker = _load_authority_marker(
        authority_marker,
        policy,
        campaign_id,
        now_ms=time.time_ns() // 1_000_000,
        expected_uid=_expected_marker_uid,
    )
    _assert_live_environment(initial_marker, _environment_provider)
    marker_identity = initial_marker["body_sha256"]
    locked_execution_binding: tuple[str, int] | None = None
    if initial_marker.get("execution_binding_status") != "PENDING_FIRST_SNAPSHOT":
        locked_execution_binding = (
            initial_marker["execution_service_epoch"],
            initial_marker["execution_service_fencing_generation"],
        )
    started_at_ms = time.time_ns() // 1_000_000
    invocations = 0
    last_triplet: dict[str, Any] | None = None
    observer_status: str | None = None
    observer_outcome: str | None = None
    completed_iterations = 0
    unavailable_since: float | None = None
    last_heartbeat = time.monotonic()
    atomic_status(
        status_path,
        _status_body(
            campaign_id=campaign_id,
            state="WAITING_FOR_EXPORT",
            started_at_ms=started_at_ms,
            invocations=invocations,
            last_triplet=last_triplet,
            observer_status=observer_status,
            observer_outcome=observer_outcome,
            completed_iterations=completed_iterations,
            reason=None,
        ),
    )
    polls = 0
    while True:
        polls += 1
        marker = _load_authority_marker(
            authority_marker,
            policy,
            campaign_id,
            now_ms=time.time_ns() // 1_000_000,
            expected_uid=_expected_marker_uid,
            require_fresh_admission=False,
        )
        if marker.get("body_sha256") != marker_identity:
            raise ControllerError("P1_CONTROLLER_AUTHORITY_MARKER_DRIFT")
        transient = False
        try:
            triplet = read_stable_triplet(export_directory)
        except ExportAuthorityEnded as ended:
            atomic_status(
                status_path,
                _status_body(
                    campaign_id=campaign_id,
                    state="ABORTED",
                    started_at_ms=started_at_ms,
                    invocations=invocations,
                    last_triplet=last_triplet,
                    observer_status=observer_status,
                    observer_outcome=observer_outcome,
                    completed_iterations=completed_iterations,
                    reason=str(ended),
                ),
            )
            return 78
        except TripletNotReady:
            triplet = None
            transient = True
        if transient or triplet is None:
            now = time.monotonic()
            unavailable_since = (
                now if unavailable_since is None else unavailable_since)
            if now - unavailable_since > EXPORT_LOSS_GRACE_SECONDS:
                raise ControllerError("P1_CONTROLLER_EXPORT_TRIPLET_LOST")
        elif triplet is not None:
            unavailable_since = None
            current_binding = (
                triplet["execution_service_epoch"],
                triplet["execution_service_fencing_generation"],
            )
            if locked_execution_binding is None:
                locked_execution_binding = current_binding
            elif current_binding != locked_execution_binding:
                raise ControllerError(
                    "P1_CONTROLLER_EXECUTION_BINDING_DRIFT")
        if triplet is not None and (
                last_triplet is None or
                triplet["identity"] != last_triplet["identity"]):
            _assert_live_environment(marker, _environment_provider)
            attempted_invocations = invocations + 1
            atomic_status(
                status_path,
                _status_body(
                    campaign_id=campaign_id,
                    state="OBSERVING",
                    started_at_ms=started_at_ms,
                    invocations=attempted_invocations,
                    last_triplet=triplet,
                    observer_status=observer_status,
                    observer_outcome=observer_outcome,
                    completed_iterations=completed_iterations,
                    reason=None,
                ),
            )
            try:
                confirmed_triplet = read_stable_triplet(export_directory)
            except ExportAuthorityEnded as ended:
                atomic_status(
                    status_path,
                    _status_body(
                        campaign_id=campaign_id,
                        state="ABORTED",
                        started_at_ms=started_at_ms,
                        invocations=invocations,
                        last_triplet=last_triplet,
                        observer_status=observer_status,
                        observer_outcome=observer_outcome,
                        completed_iterations=completed_iterations,
                        reason=str(ended),
                    ),
                )
                return 78
            except TripletNotReady:
                confirmed_triplet = None
            if (
                    confirmed_triplet is None or
                    confirmed_triplet["identity"] != triplet["identity"]):
                atomic_status(
                    status_path,
                    _status_body(
                        campaign_id=campaign_id,
                        state=(
                            "WAITING_FOR_EXPORT"
                            if last_triplet is None else "RUNNING"),
                        started_at_ms=started_at_ms,
                        invocations=invocations,
                        last_triplet=last_triplet,
                        observer_status=observer_status,
                        observer_outcome=observer_outcome,
                        completed_iterations=completed_iterations,
                        reason=None,
                    ),
                )
                if _maximum_polls is not None and polls >= _maximum_polls:
                    return 0
                _sleeper(poll_ms / 1000)
                continue
            triplet = confirmed_triplet
            confirmed_binding = (
                triplet["execution_service_epoch"],
                triplet["execution_service_fencing_generation"],
            )
            if (
                    locked_execution_binding is None or
                    confirmed_binding != locked_execution_binding):
                raise ControllerError(
                    "P1_CONTROLLER_EXECUTION_BINDING_DRIFT")
            _assert_live_environment(marker, _environment_provider)
            with _pinned_triplet(
                    export_directory, triplet) as observer_triplet:
                observer_outcome, observer_status, completed_iterations = (
                    _invoke_observer(
                        observer=observer,
                        campaign_id=campaign_id,
                        policy=policy,
                        strategy=strategy,
                        triplet=observer_triplet,
                        source_bundle=source_bundle,
                        artifact_root=artifact_root,
                        timeout_seconds=observer_timeout_seconds,
                    )
                )
            _assert_observer_continuity(
                artifact_root,
                campaign_id=campaign_id,
                observer_outcome=observer_outcome,
            )
            invocations = attempted_invocations
            last_triplet = triplet
            state = (
                "RUNNING" if observer_status == "RUNNING" else "TERMINAL")
            atomic_status(
                status_path,
                _status_body(
                    campaign_id=campaign_id,
                    state=state,
                    started_at_ms=started_at_ms,
                    invocations=invocations,
                    last_triplet=last_triplet,
                    observer_status=observer_status,
                    observer_outcome=observer_outcome,
                    completed_iterations=completed_iterations,
                    reason=None,
                ),
            )
            last_heartbeat = time.monotonic()
            if observer_status != "RUNNING":
                if observer_status != "COMPLETE":
                    return 78
                if initial_marker.get("schema") != (
                        "hepta.p1-shadow-admission-authority-marker.v1"):
                    return 0
                while True:
                    if (
                            _terminal_hold_stop_requested is not None and
                            _terminal_hold_stop_requested()):
                        return 0
                    _sleeper(TERMINAL_HEARTBEAT_SECONDS)
                    terminal_marker = _load_authority_marker(
                        authority_marker,
                        policy,
                        campaign_id,
                        now_ms=time.time_ns() // 1_000_000,
                        expected_uid=_expected_marker_uid,
                        require_fresh_admission=False,
                    )
                    if terminal_marker.get("body_sha256") != marker_identity:
                        raise ControllerError(
                            "P1_CONTROLLER_AUTHORITY_MARKER_DRIFT")
                    _assert_live_environment(
                        terminal_marker, _environment_provider)
                    atomic_status(
                        status_path,
                        _status_body(
                            campaign_id=campaign_id,
                            state="TERMINAL",
                            started_at_ms=started_at_ms,
                            invocations=invocations,
                            last_triplet=last_triplet,
                            observer_status="COMPLETE",
                            observer_outcome=observer_outcome,
                            completed_iterations=completed_iterations,
                            reason=None,
                        ),
                    )
        elif time.monotonic() - last_heartbeat >= 5:
            atomic_status(
                status_path,
                _status_body(
                    campaign_id=campaign_id,
                    state=(
                        "WAITING_FOR_EXPORT"
                        if last_triplet is None else "RUNNING"),
                    started_at_ms=started_at_ms,
                    invocations=invocations,
                    last_triplet=last_triplet,
                    observer_status=observer_status,
                    observer_outcome=observer_outcome,
                    completed_iterations=completed_iterations,
                    reason=None,
                ),
            )
            last_heartbeat = time.monotonic()
        if _maximum_polls is not None and polls >= _maximum_polls:
            return 0
        _sleeper(poll_ms / 1000)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--campaign-id", required=True)
    parser.add_argument("--policy", type=Path, required=True)
    parser.add_argument("--strategy", type=Path, required=True)
    parser.add_argument("--export-directory", type=Path, required=True)
    parser.add_argument("--source-bundle", type=Path, required=True)
    parser.add_argument("--artifact-root", type=Path, required=True)
    parser.add_argument("--status", type=Path, required=True)
    parser.add_argument(
        "--observer", type=Path,
        default=Path("/usr/libexec/hepta_bounded_shadow_observer.py"))
    parser.add_argument("--poll-ms", type=int, default=250)
    parser.add_argument("--observer-timeout-seconds", type=int, default=300)
    parser.add_argument("--authority-marker", type=Path, required=True)
    arguments = parser.parse_args()
    try:
        return run_controller(
            campaign_id=arguments.campaign_id,
            policy=arguments.policy.resolve(strict=True),
            strategy=arguments.strategy.resolve(strict=True),
            export_directory=arguments.export_directory.resolve(strict=False),
            source_bundle=arguments.source_bundle.resolve(strict=False),
            artifact_root=arguments.artifact_root.resolve(strict=False),
            status_path=arguments.status.resolve(strict=False),
            observer=arguments.observer.resolve(strict=True),
            authority_marker=arguments.authority_marker,
            poll_ms=arguments.poll_ms,
            observer_timeout_seconds=arguments.observer_timeout_seconds,
        )
    except (ControllerError, OSError, ValueError) as error:
        try:
            if (
                    "arguments" in locals() and
                    not _is_within(arguments.status, arguments.artifact_root)):
                atomic_failure_status(
                    arguments.status.resolve(strict=False),
                    campaign_id=arguments.campaign_id,
                    reason=str(error),
                )
        except Exception:
            pass
        print(f"hepta_p1_shadow_observer_controller: FAIL {error}", file=sys.stderr)
        return 78


if __name__ == "__main__":
    raise SystemExit(main())
