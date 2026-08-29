#!/usr/bin/env python3

"""Validate and render per-domain IB PAPER kill-switch provisioning.

It binds a separate PAPER authority manifest to the exact five-field
broker-network identity manifest, renders the root-controlled default-engaged
tmpfiles fragment, and can hold a root-only host-wide authority lease while
continuously validating one domain before and during its service lifetime.
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass
import errno
import fcntl
import grp
import hashlib
import json
import os
from pathlib import Path
import pwd
import re
import secrets
import signal
import socket
import stat
import subprocess
import sys
import threading
import time
from typing import Any, Callable, Optional


DEFAULT_NETWORK_IDENTITIES = Path(
    "/etc/heptatrader/"
    "hepta-agent-trust-domain-paper-identities-v1.json")
DEFAULT_AUTHORIZATIONS = Path(
    "/etc/heptatrader/hepta-ib-paper-domain-authorizations-v1.json")
NETWORK_SCHEMA = "hepta.agent-trust-domain-paper-identities.v1"
AUTHORITY_SCHEMA = "hepta.ib-paper-domain-authorizations.v1"
ROLE = "ib-paper-execution-authority"
DOMAIN = re.compile(r"[a-z][a-z0-9-]{0,17}")
# Until account-level aggregate risk, client-id allocation, and a cross-domain
# kill switch exist, one host can authorize at most one templated PAPER domain.
MAX_DOMAINS = 1
MAX_BYTES = 64 * 1024
NETWORK_HELPER = Path("/usr/libexec/hepta-broker-egress-policy")
NETWORK_POLICY = Path(
    "/usr/share/heptatrader/hepta-broker-network-policy-v1.json")
SERVICE_IDENTITIES = Path(
    "/usr/share/heptatrader/hepta-service-identities-v1.json")
HOST_LOCK_DIRECTORY = Path("/run/hepta/ib-paper-host-authority")
HOST_LOCK_PATH = HOST_LOCK_DIRECTORY / "lease.lock"
HOST_OWNER_PATH = HOST_LOCK_DIRECTORY / "owner.v1"
BOOT_ID_PATH = Path("/proc/sys/kernel/random/boot_id")
BROKER_DROP_IN_PATH = Path(
    "/etc/systemd/system/hepta-broker-egress-policy.service.d/"
    "20-local-paper.conf")
BROKER_START_PERMIT_PATH = Path(
    "/run/hepta-local-paper-control/alpha/broker-start-permit.json")
ACTIVATION_RESERVATION_SCHEMA = (
    "hepta.local-paper-broker-activation-reservation.v1")
ACTIVATION_CONSUMED_SCHEMA = (
    "hepta.local-paper-broker-activation-consumed.v1")
RUNTIME_OWNER_SCHEMA = "hepta.ib-paper-runtime-owner.v1"
RUNTIME_OWNER_STATUS = "ACTIVE_RUNTIME_GUARD"
ACTIVATION_CONSUMED_PREFIX = "activation-consumed."
ACTIVATION_INTENT_PREFIX = "activation-commit-intent."
ACTIVATION_ARTIFACT_SUFFIX = ".v1.json"
HOST_OWNER_MAX_BYTES = 4096
SHA256 = re.compile(r"sha256:[0-9a-f]{64}")
BOOT_ID = re.compile(
    r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}")
ACTIVATION_RESERVATION_FIELDS = {
    "schema", "version", "status", "activation_id", "issued_at_ms",
    "expires_at_ms", "boot_id", "guardian_pid", "guardian_start_ticks",
    "guardian_exe_sha256", "guardian_argv_sha256", "control_image_sha256",
    "guardian_request_id", "domain", "transaction_id", "operation", "phase",
    "request_sha256", "target_identity_manifest_sha256",
    "target_drop_in_sha256", "broker_start_permit_file_sha256",
    "broker_start_permit_body_sha256", "required_pre_activation_boundary",
    "paper_only", "live_authorized", "body_sha256",
}
ACTIVATION_CONSUMED_FIELDS = {
    "schema", "version", "status", "activation_id", "consumed_at_ms",
    "boot_id", "reservation_file_sha256", "reservation_body_sha256",
    "broker_start_permit_file_sha256", "broker_start_permit_body_sha256",
    "guardian_request_id", "transaction_id", "operation", "phase",
    "request_sha256", "domain", "target_identity_manifest_sha256",
    "target_drop_in_sha256", "control_image_sha256",
    "required_pre_activation_boundary",
    "pre_activation_boundary_state_sha256", "active_boundary_status",
    "active_boundary_state_sha256", "paper_authorized", "live_authorized",
    "body_sha256",
}
RUNTIME_OWNER_FIELDS = {
    "schema", "version", "status", "adopted_at_ms", "boot_id", "domain",
    "activation_id", "transaction_id", "operation", "phase",
    "guardian_request_id", "request_sha256", "reservation_file_sha256",
    "reservation_body_sha256", "activation_consumed_file_sha256",
    "activation_consumed_body_sha256", "broker_start_permit_file_sha256",
    "broker_start_permit_body_sha256",
    "pre_activation_boundary_state_sha256", "active_boundary_state_sha256",
    "target_identity_manifest_sha256", "target_drop_in_sha256",
    "execution_identity", "execution_uid", "execution_gid",
    "control_directory", "kill_switch_marker", "guard_pid",
    "guard_start_ticks", "guard_exe_sha256", "guard_argv_sha256",
    "mutation_scope", "paper_authorized", "live_authorized", "body_sha256",
}
DEFAULT_POLL_INTERVAL_SECONDS = 0.25
MAX_COMMAND_OUTPUT = 1024 * 1024
SAFE_ENVIRONMENT = {
    "PATH": "/usr/sbin:/usr/bin:/sbin:/bin",
    "LANG": "C",
    "LC_ALL": "C",
}
OPERATOR_RUNTIME_ROOT = Path("/run/hepta/ib-paper-one-shot")
OPERATOR_RECEIPT_ROOT = Path("/var/lib/hepta-ib-paper-one-shot")
OPERATOR_SCHEMA = "hepta.ib-paper-one-shot-operator.v1"
OPERATOR_CYCLE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,95}")
OPERATOR_DIGEST = re.compile(r"sha256:[0-9a-f]{64}")
MIN_OPERATOR_TTL_SECONDS = 5
MAX_OPERATOR_TTL_SECONDS = 20


class AuthorityError(RuntimeError):
    pass


class LeaseBusyError(AuthorityError):
    """The valid host lease is currently held by another authority."""


@dataclass(frozen=True)
class DomainAuthority:
    domain_id: str
    identity: str
    uid: int
    gid: int
    control_directory: str
    kill_switch_marker: str


@dataclass(frozen=True)
class ManifestFingerprint:
    path: Path
    device: int
    inode: int
    mode: int
    links: int
    uid: int
    gid: int
    size: int
    mtime_ns: int
    ctime_ns: int
    sha256: str


@dataclass(frozen=True)
class OperatorPaths:
    runtime_root: Path
    receipt_root: Path


def _canonical_json(value: Any) -> bytes:
    try:
        return (json.dumps(
            value, ensure_ascii=True, separators=(",", ":"),
            sort_keys=True, allow_nan=False) + "\n").encode("ascii")
    except (TypeError, ValueError) as error:
        raise AuthorityError("operator state is not canonical JSON") from error


def _operator_identifiers(cycle_id: str, intent_sha256: str) -> str:
    if OPERATOR_CYCLE.fullmatch(cycle_id) is None:
        raise AuthorityError("operator cycle id is invalid")
    if OPERATOR_DIGEST.fullmatch(intent_sha256) is None:
        raise AuthorityError("operator intent digest is invalid")
    return hashlib.sha256(
        (cycle_id + "\0" + intent_sha256).encode("ascii")).hexdigest()[:20]


def _secure_operator_directory(
        path: Path, *, uid: int = 0, gid: int = 0) -> int:
    path.mkdir(mode=0o700, parents=False, exist_ok=True)
    metadata = os.lstat(path)
    if (
            not stat.S_ISDIR(metadata.st_mode) or
            stat.S_ISLNK(metadata.st_mode) or metadata.st_nlink != 2 or
            metadata.st_uid != uid or metadata.st_gid != gid or
            stat.S_IMODE(metadata.st_mode) != 0o700):
        raise AuthorityError("operator directory metadata mismatch")
    descriptor = os.open(
        path, os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC |
        getattr(os, "O_NOFOLLOW", 0))
    opened = os.fstat(descriptor)
    if opened.st_dev != metadata.st_dev or opened.st_ino != metadata.st_ino:
        os.close(descriptor)
        raise AuthorityError("operator directory changed while opening")
    return descriptor


def _write_private_json(directory_fd: int, name: str, value: Any) -> None:
    temporary = f".{name}.{os.getpid()}.{time.time_ns()}"
    payload = _canonical_json(value)
    descriptor = os.open(
        temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_CLOEXEC,
        0o600, dir_fd=directory_fd)
    try:
        offset = 0
        while offset < len(payload):
            written = os.write(descriptor, payload[offset:])
            if written <= 0:
                raise AuthorityError("operator state write was incomplete")
            offset += written
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
    os.replace(temporary, name, src_dir_fd=directory_fd, dst_dir_fd=directory_fd)
    os.fsync(directory_fd)


def _read_private_json(
        directory_fd: int, name: str, *, uid: int = 0, gid: int = 0,
) -> dict[str, Any]:
    metadata = os.stat(name, dir_fd=directory_fd, follow_symlinks=False)
    if (
            not stat.S_ISREG(metadata.st_mode) or metadata.st_nlink != 1 or
            stat.S_IMODE(metadata.st_mode) != 0o600 or
            metadata.st_uid != uid or metadata.st_gid != gid or
            metadata.st_size < 2 or metadata.st_size > MAX_BYTES):
        raise AuthorityError("operator state metadata mismatch")
    descriptor = os.open(
        name, os.O_RDONLY | os.O_CLOEXEC | getattr(os, "O_NOFOLLOW", 0),
        dir_fd=directory_fd)
    try:
        opened = os.fstat(descriptor)
        raw = bytearray()
        while len(raw) <= MAX_BYTES:
            chunk = os.read(descriptor, min(8192, MAX_BYTES + 1 - len(raw)))
            if not chunk:
                break
            raw.extend(chunk)
        after = os.fstat(descriptor)
    finally:
        os.close(descriptor)
    fields = (
        "st_dev", "st_ino", "st_mode", "st_nlink", "st_uid", "st_gid",
        "st_size", "st_mtime_ns", "st_ctime_ns")
    if (
            len(raw) > MAX_BYTES or
            any(getattr(metadata, field) != getattr(opened, field) or
                getattr(opened, field) != getattr(after, field)
                for field in fields)):
        raise AuthorityError("operator state changed while reading")
    return _strict_json(bytes(raw), "operator state")


def _operator_timer_unit(domain: str, identity: str) -> str:
    return f"hepta-ib-paper-reengage-{domain}-{identity}"


def _arm_operator_watchdog(
        item: DomainAuthority, cycle_id: str, intent_sha256: str,
        ttl_seconds: int, identity: str,
        *, executable: Path = Path("/usr/libexec/hepta-ib-paper-domain-authority"),
        runner: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run,
) -> str:
    unit = _operator_timer_unit(item.domain_id, identity)
    command = [
        "/usr/bin/systemd-run", "--quiet", "--collect", f"--unit={unit}",
        f"--on-active={ttl_seconds}s", "--timer-property=AccuracySec=1s",
        "--property=User=root", "--property=Group=root",
        "--property=UMask=0077", "--property=NoNewPrivileges=yes",
        "--property=PrivateTmp=yes", "--property=ProtectSystem=strict",
        "--property=ProtectHome=yes", "--property=RestrictAddressFamilies=AF_UNIX",
        f"--property=ReadWritePaths={item.control_directory} "
        f"{OPERATOR_RUNTIME_ROOT} {OPERATOR_RECEIPT_ROOT}",
        str(executable), "--operator-reengage", "--domain", item.domain_id,
        "--cycle-id", cycle_id, "--intent-sha256", intent_sha256,
        "--operator-source", "watchdog",
    ]
    try:
        runner(command, env=SAFE_ENVIRONMENT, text=True, capture_output=True,
               timeout=10, check=True)
        runner(["/usr/bin/systemctl", "is-active", unit + ".timer"],
               env=SAFE_ENVIRONMENT, text=True, capture_output=True,
               timeout=5, check=True)
    except (OSError, subprocess.CalledProcessError, subprocess.TimeoutExpired) as error:
        raise AuthorityError("operator re-engage watchdog did not arm") from error
    return unit


def _marker_descriptor(
        item: DomainAuthority, *, uid: int = 0,
) -> tuple[int, os.stat_result]:
    directory = Path(item.control_directory)
    metadata = os.lstat(directory)
    if (
            not stat.S_ISDIR(metadata.st_mode) or metadata.st_nlink != 2 or
            metadata.st_uid != uid or metadata.st_gid != item.gid or
            stat.S_IMODE(metadata.st_mode) != 0o750):
        raise AuthorityError("PAPER control directory metadata mismatch")
    descriptor = os.open(
        directory, os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC |
        getattr(os, "O_NOFOLLOW", 0))
    opened = os.fstat(descriptor)
    if opened.st_dev != metadata.st_dev or opened.st_ino != metadata.st_ino:
        os.close(descriptor)
        raise AuthorityError("PAPER control directory changed while opening")
    return descriptor, metadata


def _engaged_marker(
        directory_fd: int, item: DomainAuthority, *, uid: int = 0,
) -> os.stat_result:
    marker = os.stat("kill-switch", dir_fd=directory_fd, follow_symlinks=False)
    if (
            not stat.S_ISREG(marker.st_mode) or marker.st_nlink != 1 or
            marker.st_uid != uid or marker.st_gid != item.gid or
            stat.S_IMODE(marker.st_mode) != 0o440):
        raise AuthorityError("PAPER kill-switch marker metadata mismatch")
    descriptor = os.open(
        "kill-switch", os.O_RDONLY | os.O_CLOEXEC |
        getattr(os, "O_NOFOLLOW", 0), dir_fd=directory_fd)
    try:
        if os.fstat(descriptor).st_ino != marker.st_ino or os.read(descriptor, 8) != b"engaged":
            raise AuthorityError("PAPER kill-switch marker content mismatch")
    finally:
        os.close(descriptor)
    return marker


def _restore_engaged_marker(
        directory_fd: int, item: DomainAuthority, *, uid: int = 0) -> None:
    try:
        _engaged_marker(directory_fd, item, uid=uid)
        return
    except FileNotFoundError:
        pass
    temporary = f".kill-switch.{os.getpid()}.{time.time_ns()}"
    descriptor = os.open(
        temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_CLOEXEC,
        0o400, dir_fd=directory_fd)
    try:
        if os.write(descriptor, b"engaged") != len(b"engaged"):
            raise AuthorityError("PAPER kill-switch restore was incomplete")
        os.fchown(descriptor, uid, item.gid)
        os.fchmod(descriptor, 0o440)
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
    try:
        os.link(
            temporary, "kill-switch", src_dir_fd=directory_fd,
            dst_dir_fd=directory_fd, follow_symlinks=False)
    finally:
        os.unlink(temporary, dir_fd=directory_fd)
    os.fsync(directory_fd)
    _engaged_marker(directory_fd, item, uid=uid)


def operator_disarm(
        item: DomainAuthority, cycle_id: str, intent_sha256: str,
        ttl_seconds: int, *,
        paths: OperatorPaths = OperatorPaths(
            OPERATOR_RUNTIME_ROOT, OPERATOR_RECEIPT_ROOT),
        watchdog: Callable[..., str] = _arm_operator_watchdog,
        now_ms: Callable[[], int] = lambda: time.time_ns() // 1_000_000,
        root_uid: int = 0, root_gid: int = 0,
) -> dict[str, Any]:
    identity = _operator_identifiers(cycle_id, intent_sha256)
    if ttl_seconds < MIN_OPERATOR_TTL_SECONDS or ttl_seconds > MAX_OPERATOR_TTL_SECONDS:
        raise AuthorityError("operator TTL is outside the 5-20 second bound")
    runtime_fd = _secure_operator_directory(
        paths.runtime_root, uid=root_uid, gid=root_gid)
    receipt_fd = _secure_operator_directory(
        paths.receipt_root, uid=root_uid, gid=root_gid)
    control_fd = -1
    lock_fd = os.open("operator.lock", os.O_RDWR | os.O_CREAT, 0o600,
                      dir_fd=runtime_fd)
    try:
        fcntl.flock(lock_fd, fcntl.LOCK_EX)
        control_fd, _directory = _marker_descriptor(item, uid=root_uid)
        marker = _engaged_marker(control_fd, item, uid=root_uid)
        state_name = f"{item.domain_id}.json"
        started = now_ms()
        state = {
            "schema": OPERATOR_SCHEMA, "domain": item.domain_id,
            "cycle_id": cycle_id, "intent_sha256": intent_sha256,
            "identity": identity, "status": "watchdog_arming",
            "started_at_ms": started,
            "deadline_at_ms": started + ttl_seconds * 1000,
            "ttl_seconds": ttl_seconds,
            "marker_device": marker.st_dev, "marker_inode": marker.st_ino,
            "timer_unit": None,
        }
        _write_private_json(runtime_fd, state_name, state)
        timer_unit = watchdog(item, cycle_id, intent_sha256, ttl_seconds, identity)
        state.update(status="watchdog_armed", timer_unit=timer_unit)
        _write_private_json(runtime_fd, state_name, state)
        current = os.stat("kill-switch", dir_fd=control_fd, follow_symlinks=False)
        if current.st_dev != marker.st_dev or current.st_ino != marker.st_ino:
            raise AuthorityError("PAPER kill-switch changed before disarm")
        os.unlink("kill-switch", dir_fd=control_fd)
        os.fsync(control_fd)
        try:
            os.stat("kill-switch", dir_fd=control_fd, follow_symlinks=False)
        except FileNotFoundError:
            pass
        else:
            raise AuthorityError("PAPER kill-switch disarm did not commit")
        state["status"] = "disarmed"
        state["disarmed_at_ms"] = now_ms()
        _write_private_json(runtime_fd, state_name, state)
        _write_private_json(receipt_fd, f"{identity}.json", state)
        return state
    finally:
        if control_fd >= 0:
            os.close(control_fd)
        fcntl.flock(lock_fd, fcntl.LOCK_UN)
        os.close(lock_fd)
        os.close(receipt_fd)
        os.close(runtime_fd)


def operator_reengage(
        item: DomainAuthority, cycle_id: str, intent_sha256: str,
        *, source: str = "operator",
        paths: OperatorPaths = OperatorPaths(
            OPERATOR_RUNTIME_ROOT, OPERATOR_RECEIPT_ROOT),
        now_ms: Callable[[], int] = lambda: time.time_ns() // 1_000_000,
        timer_stopper: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run,
        root_uid: int = 0, root_gid: int = 0,
) -> dict[str, Any]:
    identity = _operator_identifiers(cycle_id, intent_sha256)
    if source not in {"operator", "watchdog"}:
        raise AuthorityError("operator re-engage source is invalid")
    runtime_fd = _secure_operator_directory(
        paths.runtime_root, uid=root_uid, gid=root_gid)
    receipt_fd = _secure_operator_directory(
        paths.receipt_root, uid=root_uid, gid=root_gid)
    lock_fd = os.open("operator.lock", os.O_RDWR | os.O_CREAT, 0o600,
                      dir_fd=runtime_fd)
    control_fd = -1
    try:
        fcntl.flock(lock_fd, fcntl.LOCK_EX)
        control_fd, _directory = _marker_descriptor(item, uid=root_uid)
        _restore_engaged_marker(control_fd, item, uid=root_uid)
        state = _read_private_json(
            runtime_fd, f"{item.domain_id}.json",
            uid=root_uid, gid=root_gid)
        if (
                state.get("schema") != OPERATOR_SCHEMA or
                state.get("cycle_id") != cycle_id or
                state.get("intent_sha256") != intent_sha256 or
                state.get("identity") != identity):
            raise AuthorityError("operator lease binding mismatch")
        state["status"] = "engaged"
        state["reengaged_at_ms"] = now_ms()
        state["reengage_source"] = source
        _write_private_json(runtime_fd, f"{item.domain_id}.json", state)
        _write_private_json(receipt_fd, f"{identity}.json", state)
        if source == "operator" and isinstance(state.get("timer_unit"), str):
            try:
                timer_stopper(
                    ["/usr/bin/systemctl", "stop", state["timer_unit"] + ".timer"],
                    env=SAFE_ENVIRONMENT, text=True, capture_output=True,
                    timeout=5, check=False)
            except (OSError, subprocess.TimeoutExpired):
                pass
        return state
    finally:
        if control_fd >= 0:
            os.close(control_fd)
        fcntl.flock(lock_fd, fcntl.LOCK_UN)
        os.close(lock_fd)
        os.close(receipt_fd)
        os.close(runtime_fd)


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate JSON key: {key}")
        result[key] = value
    return result


def _reject_constant(value: str) -> None:
    raise ValueError(f"non-finite JSON number: {value}")


def _strict_json(raw: bytes, label: str) -> dict[str, Any]:
    try:
        value = json.loads(
            raw.decode("utf-8", errors="strict"),
            object_pairs_hook=_unique_object,
            parse_constant=_reject_constant)
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as error:
        raise AuthorityError(f"{label}: invalid strict JSON: {error}") from error
    if not isinstance(value, dict):
        raise AuthorityError(f"{label}: root must be an object")
    return value


def _read_stable(
        path: Path, *, installed: bool, expected_mode: int = 0o600) -> bytes:
    before = os.lstat(path)
    if (
            not stat.S_ISREG(before.st_mode) or
            stat.S_ISLNK(before.st_mode) or
            before.st_nlink != 1 or
            before.st_size < 2 or
            before.st_size > MAX_BYTES or
            stat.S_IMODE(before.st_mode) & 0o002 or
            (installed and (
                before.st_uid != 0 or before.st_gid != 0 or
                stat.S_IMODE(before.st_mode) != expected_mode))):
        raise AuthorityError(f"{path}: unsafe source metadata")
    descriptor = os.open(
        path, os.O_RDONLY | os.O_CLOEXEC | getattr(os, "O_NOFOLLOW", 0))
    try:
        opened = os.fstat(descriptor)
        chunks: list[bytes] = []
        total = 0
        while True:
            chunk = os.read(descriptor, min(8192, MAX_BYTES + 1 - total))
            if not chunk:
                break
            chunks.append(chunk)
            total += len(chunk)
            if total > MAX_BYTES:
                raise AuthorityError(f"{path}: source exceeds size limit")
        after = os.fstat(descriptor)
    finally:
        os.close(descriptor)
    fields = (
        "st_dev", "st_ino", "st_mode", "st_nlink", "st_uid", "st_gid",
        "st_size", "st_mtime_ns", "st_ctime_ns")
    if any(
            getattr(before, field) != getattr(opened, field) or
            getattr(opened, field) != getattr(after, field)
            for field in fields):
        raise AuthorityError(f"{path}: source changed while reading")
    return b"".join(chunks)


def _read_manifest_snapshot(
        path: Path, *, installed: bool
) -> tuple[bytes, ManifestFingerprint]:
    before = os.lstat(path)
    raw = _read_stable(path, installed=installed)
    after = os.lstat(path)
    fields = (
        "st_dev", "st_ino", "st_mode", "st_nlink", "st_uid", "st_gid",
        "st_size", "st_mtime_ns", "st_ctime_ns")
    if any(getattr(before, field) != getattr(after, field) for field in fields):
        raise AuthorityError(f"{path}: source changed around stable read")
    return raw, ManifestFingerprint(
        path=path,
        device=after.st_dev,
        inode=after.st_ino,
        mode=after.st_mode,
        links=after.st_nlink,
        uid=after.st_uid,
        gid=after.st_gid,
        size=after.st_size,
        mtime_ns=after.st_mtime_ns,
        ctime_ns=after.st_ctime_ns,
        sha256=hashlib.sha256(raw).hexdigest(),
    )


def manifest_fingerprint_matches(
        expected: ManifestFingerprint) -> bool:
    try:
        _raw, observed = _read_manifest_snapshot(
            expected.path, installed=True)
    except (AuthorityError, FileNotFoundError, OSError):
        return False
    return observed == expected


def _uint32(value: Any, label: str) -> int:
    if (
            not isinstance(value, int) or isinstance(value, bool) or
            value < 1 or value > 4_294_967_295):
        raise AuthorityError(f"{label}: invalid positive uint32")
    return value


def parse_authorities(
        network_raw: bytes, authority_raw: bytes) -> tuple[DomainAuthority, ...]:
    network = _strict_json(network_raw, "broker network identity manifest")
    if set(network) != {
            "schema", "version", "source_policy_sha256",
            "paper_authorized", "live_authorized", "identities"}:
        raise AuthorityError("broker network identity manifest fields mismatch")
    if (
            network.get("schema") != NETWORK_SCHEMA or
            not isinstance(network.get("version"), int) or
            isinstance(network.get("version"), bool) or
            network.get("version") != 1 or
            not isinstance(network.get("paper_authorized"), bool) or
            network.get("live_authorized") is not False or
            not isinstance(network.get("identities"), list) or
            len(network["identities"]) > MAX_DOMAINS):
        raise AuthorityError("broker network identity manifest contract mismatch")

    authority = _strict_json(authority_raw, "PAPER authority manifest")
    if set(authority) != {
            "schema", "version", "network_identity_manifest_sha256",
            "paper_authorized", "live_authorized", "authorizations"}:
        raise AuthorityError("PAPER authority manifest fields mismatch")
    if (
            authority.get("schema") != AUTHORITY_SCHEMA or
            not isinstance(authority.get("version"), int) or
            isinstance(authority.get("version"), bool) or
            authority.get("version") != 1 or
            authority.get("network_identity_manifest_sha256") !=
            "sha256:" + hashlib.sha256(network_raw).hexdigest() or
            authority.get("paper_authorized") is not
            network["paper_authorized"] or
            authority.get("live_authorized") is not False or
            not isinstance(authority.get("authorizations"), list) or
            len(authority["authorizations"]) > MAX_DOMAINS):
        raise AuthorityError("PAPER authority manifest contract mismatch")

    network_records = network["identities"]
    authority_records = authority["authorizations"]
    authorized = network["paper_authorized"]
    if (
            (authorized and (
                not network_records or not authority_records or
                len(network_records) != len(authority_records))) or
            (not authorized and (network_records or authority_records))):
        raise AuthorityError("PAPER authorization/list mismatch")

    identities: dict[str, tuple[str, int, int]] = {}
    for index, record in enumerate(network_records):
        label = f"network identities[{index}]"
        if (
                not isinstance(record, dict) or
                set(record) != {
                    "domain_id", "identity", "uid", "gid", "role"}):
            raise AuthorityError(f"{label}: strict five-field record required")
        domain_id = record.get("domain_id")
        identity = record.get("identity")
        uid = _uint32(record.get("uid"), label + ".uid")
        gid = _uint32(record.get("gid"), label + ".gid")
        if (
                not isinstance(domain_id, str) or
                DOMAIN.fullmatch(domain_id) is None or
                identity != f"hepta-ib-exec-{domain_id}" or
                record.get("role") != ROLE or uid != gid):
            raise AuthorityError(f"{label}: dedicated identity mismatch")
        if domain_id in identities:
            raise AuthorityError("network identity domain is duplicated")
        identities[domain_id] = (identity, uid, gid)
    if list(identities) != sorted(identities):
        raise AuthorityError("network identity records are not domain-sorted")

    result: list[DomainAuthority] = []
    for index, record in enumerate(authority_records):
        label = f"authorizations[{index}]"
        if (
                not isinstance(record, dict) or
                set(record) != {
                    "domain_id", "identity", "uid", "gid",
                    "control_directory", "kill_switch_marker",
                    "control_directory_mode", "kill_switch_mode",
                    "kill_switch_initial_state"}):
            raise AuthorityError(f"{label}: fields mismatch")
        domain_id = record.get("domain_id")
        if not isinstance(domain_id, str) or domain_id not in identities:
            raise AuthorityError(f"{label}: domain is not network-authorized")
        identity, uid, gid = identities[domain_id]
        control = f"/run/hepta/ib-paper-control-{domain_id}"
        marker = control + "/kill-switch"
        if (
                record.get("identity") != identity or
                record.get("uid") != uid or record.get("gid") != gid or
                record.get("control_directory") != control or
                record.get("kill_switch_marker") != marker or
                record.get("control_directory_mode") != "0750" or
                record.get("kill_switch_mode") != "0440" or
                record.get("kill_switch_initial_state") != "engaged"):
            raise AuthorityError(f"{label}: default-engaged contract mismatch")
        result.append(DomainAuthority(
            domain_id=domain_id,
            identity=identity,
            uid=uid,
            gid=gid,
            control_directory=control,
            kill_switch_marker=marker,
        ))
    if [item.domain_id for item in result] != sorted(identities):
        raise AuthorityError(
            "PAPER authority records do not exactly match sorted identities")
    return tuple(result)


def render_tmpfiles(authorities: tuple[DomainAuthority, ...]) -> bytes:
    if not authorities:
        raise AuthorityError(
            "tmpfiles rendering requires explicit PAPER authorization")
    lines = [
        "# Generated from digest-bound explicit per-domain PAPER authority.",
        "# Applying this fragment is a separate privileged action.",
    ]
    for item in authorities:
        lines.extend((
            f"d {item.control_directory} 0750 root {item.identity} -",
            f"f {item.kill_switch_marker} 0440 root "
            f"{item.identity} - engaged",
        ))
    return ("\n".join(lines) + "\n").encode("ascii")


def _validate_identity(item: DomainAuthority) -> None:
    try:
        account = pwd.getpwnam(item.identity)
        account_by_uid = pwd.getpwuid(item.uid)
        group = grp.getgrnam(item.identity)
        group_by_gid = grp.getgrgid(item.gid)
    except KeyError as error:
        raise AuthorityError("PAPER OS identity is not provisioned") from error
    if (
            account.pw_name != item.identity or
            account_by_uid.pw_name != item.identity or
            account.pw_uid != item.uid or account.pw_gid != item.gid or
            account.pw_dir != "/nonexistent" or
            not account.pw_shell.endswith("/nologin") or
            [entry.pw_name for entry in pwd.getpwall()
             if entry.pw_uid == item.uid] != [item.identity] or
            group.gr_name != item.identity or
            group_by_gid.gr_name != item.identity or
            group.gr_gid != item.gid or group.gr_mem or
            [entry.gr_name for entry in grp.getgrall()
             if entry.gr_gid == item.gid] != [item.identity] or
            set(os.getgrouplist(item.identity, item.gid)) != {item.gid}):
        raise AuthorityError("PAPER OS identity metadata mismatch")


def _validate_runtime(
        item: DomainAuthority, *, require_initial_engaged: bool,
        ownership_provider: Callable[
            [Path, os.stat_result], tuple[int, int]
        ] = lambda _path, metadata: (metadata.st_uid, metadata.st_gid),
) -> None:
    _validate_identity(item)
    directory_path = Path(item.control_directory)
    marker_path = Path(item.kill_switch_marker)
    if directory_path.resolve(strict=True) != directory_path:
        raise AuthorityError("PAPER control directory contains an alias")
    directory = os.lstat(directory_path)
    directory_uid, directory_gid = ownership_provider(
        directory_path, directory)
    if (
            not stat.S_ISDIR(directory.st_mode) or
            directory_uid != 0 or directory_gid != item.gid or
            stat.S_IMODE(directory.st_mode) != 0o750 or
            directory.st_nlink != 2):
        raise AuthorityError("PAPER control directory metadata mismatch")
    try:
        marker = os.lstat(marker_path)
    except FileNotFoundError as error:
        if require_initial_engaged:
            raise AuthorityError(
                "PAPER kill-switch is not initially engaged") from error
        current = os.lstat(directory_path)
        if (
                current.st_dev != directory.st_dev or
                current.st_ino != directory.st_ino or
                current.st_mode != directory.st_mode or
                current.st_nlink != directory.st_nlink):
            raise AuthorityError(
                "PAPER control directory changed during disarmed check")
        return
    marker_uid, marker_gid = ownership_provider(marker_path, marker)
    if (
            not stat.S_ISREG(marker.st_mode) or marker.st_nlink != 1 or
            marker_uid != 0 or marker_gid != item.gid or
            stat.S_IMODE(marker.st_mode) != 0o440 or
            marker.st_dev != directory.st_dev):
        raise AuthorityError("PAPER kill-switch marker metadata mismatch")
    if _read_stable(
            marker_path, installed=False) != b"engaged":
        raise AuthorityError("PAPER kill-switch marker content mismatch")


def validate_runtime(
        item: DomainAuthority, *,
        ownership_provider: Callable[
            [Path, os.stat_result], tuple[int, int]
        ] = lambda _path, metadata: (metadata.st_uid, metadata.st_gid),
) -> None:
    _validate_runtime(
        item, require_initial_engaged=True,
        ownership_provider=ownership_provider)


def validate_runtime_lifecycle(
        item: DomainAuthority, *,
        ownership_provider: Callable[
            [Path, os.stat_result], tuple[int, int]
        ] = lambda _path, metadata: (metadata.st_uid, metadata.st_gid),
) -> None:
    _validate_runtime(
        item, require_initial_engaged=False,
        ownership_provider=ownership_provider)


def load(
        network_path: Path, authority_path: Path, *,
        installed: bool) -> tuple[DomainAuthority, ...]:
    return parse_authorities(
        _read_stable(network_path, installed=installed),
        _read_stable(authority_path, installed=installed),
    )


def load_with_fingerprints(
        network_path: Path, authority_path: Path,
) -> tuple[tuple[DomainAuthority, ...],
           tuple[ManifestFingerprint, ManifestFingerprint]]:
    network_raw, network_fingerprint = _read_manifest_snapshot(
        network_path, installed=True)
    authority_raw, authority_fingerprint = _read_manifest_snapshot(
        authority_path, installed=True)
    return (
        parse_authorities(network_raw, authority_raw),
        (network_fingerprint, authority_fingerprint),
    )


class SystemdNotifier:
    def __init__(self, *, required: bool):
        value = os.environ.get("NOTIFY_SOCKET", "")
        if not value:
            if required:
                raise AuthorityError("Type=notify guard requires NOTIFY_SOCKET")
            self._address: Optional[str] = None
            return
        if "\x00" in value or len(value.encode("utf-8")) > 107:
            raise AuthorityError("NOTIFY_SOCKET is invalid")
        self._address = "\x00" + value[1:] if value.startswith("@") else value

    def send(self, message: str) -> None:
        if self._address is None:
            return
        if (
                not message or "\x00" in message or
                len(message.encode("utf-8")) > 4096):
            raise AuthorityError("sd_notify message is invalid")
        channel = socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM)
        try:
            channel.settimeout(1)
            channel.connect(self._address)
            channel.sendall(message.encode("utf-8"))
        except OSError as error:
            raise AuthorityError("sd_notify delivery failed") from error
        finally:
            channel.close()


def _watchdog_poll_interval() -> float:
    raw = os.environ.get("WATCHDOG_USEC", "")
    if not raw:
        return DEFAULT_POLL_INTERVAL_SECONDS
    if not raw.isdecimal() or int(raw) <= 0:
        raise AuthorityError("WATCHDOG_USEC is invalid")
    return max(
        0.05,
        min(DEFAULT_POLL_INTERVAL_SECONDS, int(raw) / 4_000_000))


def acquire_host_lease(
        path: Path = HOST_LOCK_PATH,
        *,
        nonblocking: bool = True,
        ownership_provider: Callable[
            [Path, os.stat_result], tuple[int, int]
        ] = lambda _path, metadata: (metadata.st_uid, metadata.st_gid),
) -> int:
    directory_path = path.parent
    if directory_path.resolve(strict=True) != directory_path:
        raise AuthorityError("host authority lock directory contains an alias")
    directory = os.lstat(directory_path)
    directory_uid, directory_gid = ownership_provider(
        directory_path, directory)
    if (
            not stat.S_ISDIR(directory.st_mode) or
            directory_uid != 0 or directory_gid != 0 or
            stat.S_IMODE(directory.st_mode) != 0o700):
        raise AuthorityError("host authority lock directory metadata mismatch")
    descriptor = os.open(
        path,
        os.O_RDWR | os.O_CREAT | os.O_CLOEXEC |
        getattr(os, "O_NOFOLLOW", 0),
        0o600,
    )
    try:
        metadata = os.fstat(descriptor)
        lock_uid, lock_gid = ownership_provider(path, metadata)
        if (
                not stat.S_ISREG(metadata.st_mode) or metadata.st_nlink != 1 or
                lock_uid != 0 or lock_gid != 0 or
                stat.S_IMODE(metadata.st_mode) != 0o600):
            raise AuthorityError("host authority lock metadata mismatch")
        try:
            operation = fcntl.LOCK_EX
            if nonblocking:
                operation |= fcntl.LOCK_NB
            fcntl.flock(descriptor, operation)
        except OSError as error:
            if error.errno in (errno.EACCES, errno.EAGAIN):
                raise LeaseBusyError(
                    "another host PAPER authority already holds the lease"
                ) from error
            raise
    except BaseException:
        os.close(descriptor)
        raise
    return descriptor


def _sha256(raw: bytes) -> str:
    return "sha256:" + hashlib.sha256(raw).hexdigest()


def _sealed_json(body: dict[str, Any]) -> bytes:
    if "body_sha256" in body:
        raise AuthorityError("host authority document contains duplicate seal")
    document = {**body, "body_sha256": _sha256(_canonical_json(body))}
    return _canonical_json(document)


def _validate_sealed_json(
        raw: bytes, *, fields: set[str], schema: str, label: str,
) -> dict[str, Any]:
    if not 2 <= len(raw) <= HOST_OWNER_MAX_BYTES:
        raise AuthorityError(f"{label} size is outside bounds")
    document = _strict_json(raw, label)
    if (
            set(document) != fields or document.get("schema") != schema or
            document.get("version") != 1 or raw != _canonical_json(document)):
        raise AuthorityError(f"{label} contract mismatch")
    body = dict(document)
    claimed = body.pop("body_sha256", None)
    if (
            not isinstance(claimed, str) or SHA256.fullmatch(claimed) is None or
            claimed != _sha256(_canonical_json(body))):
        raise AuthorityError(f"{label} seal mismatch")
    return document


def _metadata_identity(metadata: os.stat_result) -> tuple[int, ...]:
    return tuple(int(getattr(metadata, field)) for field in (
        "st_dev", "st_ino", "st_mode", "st_nlink", "st_uid", "st_gid",
        "st_size", "st_mtime_ns", "st_ctime_ns"))


def _read_private_artifact(
        path: Path, *, expected_uid: int = 0, expected_gid: int = 0,
        maximum: int = HOST_OWNER_MAX_BYTES,
) -> bytes:
    descriptor = -1
    try:
        before = os.lstat(path)
        descriptor = os.open(
            path, os.O_RDONLY | os.O_CLOEXEC | os.O_NONBLOCK |
            getattr(os, "O_NOFOLLOW", 0))
        opened = os.fstat(descriptor)
        if (
                _metadata_identity(before) != _metadata_identity(opened) or
                not stat.S_ISREG(opened.st_mode) or opened.st_nlink != 1 or
                opened.st_uid != expected_uid or opened.st_gid != expected_gid or
                stat.S_IMODE(opened.st_mode) != 0o600 or
                not 2 <= opened.st_size <= maximum):
            raise AuthorityError("host authority artifact metadata mismatch")
        payload = bytearray()
        while len(payload) <= maximum:
            chunk = os.read(
                descriptor, min(4096, maximum + 1 - len(payload)))
            if not chunk:
                break
            payload.extend(chunk)
        after = os.fstat(descriptor)
        named = os.lstat(path)
        if (
                len(payload) > maximum or len(payload) != opened.st_size or
                _metadata_identity(opened) != _metadata_identity(after) or
                _metadata_identity(after) != _metadata_identity(named)):
            raise AuthorityError("host authority artifact changed while reading")
        return bytes(payload)
    except FileNotFoundError:
        raise
    except OSError as error:
        raise AuthorityError("host authority artifact is unavailable") from error
    finally:
        if descriptor >= 0:
            os.close(descriptor)


def _read_boot_id(path: Path = BOOT_ID_PATH) -> str:
    try:
        value = path.read_text(encoding="ascii").strip()
    except (OSError, UnicodeError) as error:
        raise AuthorityError("host boot identity is unavailable") from error
    if BOOT_ID.fullmatch(value) is None or value == (
            "00000000-0000-0000-0000-000000000000"):
        raise AuthorityError("host boot identity is invalid")
    return value


def _hash_open_descriptor(descriptor: int, maximum: int = 256 << 20) -> str:
    digest = hashlib.sha256()
    total = 0
    os.lseek(descriptor, 0, os.SEEK_SET)
    while total <= maximum:
        chunk = os.read(descriptor, min(1 << 20, maximum + 1 - total))
        if not chunk:
            break
        total += len(chunk)
        digest.update(chunk)
    if total > maximum:
        raise AuthorityError("runtime owner executable exceeds size bound")
    return "sha256:" + digest.hexdigest()


def _process_identity(pid: int) -> dict[str, Any]:
    if not isinstance(pid, int) or isinstance(pid, bool) or pid <= 0:
        raise AuthorityError("runtime owner process identity is invalid")
    try:
        stat_text = Path(f"/proc/{pid}/stat").read_text(encoding="ascii")
        right = stat_text.rfind(")")
        fields = stat_text[right + 2:].split()
        if right <= 0 or len(fields) < 20:
            raise ValueError("short proc stat")
        start_ticks = int(fields[19], 10)
        executable = os.open(
            f"/proc/{pid}/exe", os.O_RDONLY | os.O_CLOEXEC)
        try:
            executable_sha256 = _hash_open_descriptor(executable)
        finally:
            os.close(executable)
        argv = Path(f"/proc/{pid}/cmdline").read_bytes()
    except (OSError, UnicodeError, ValueError) as error:
        raise AuthorityError("runtime owner process identity unavailable") from error
    if start_ticks <= 0 or not argv or not argv.endswith(b"\0"):
        raise AuthorityError("runtime owner process identity is invalid")
    return {
        "pid": pid, "start_ticks": start_ticks,
        "exe_sha256": executable_sha256, "argv_sha256": _sha256(argv),
    }


def _validate_host_authority_directory(
        path: Path, *, expected_uid: int, expected_gid: int,
) -> int:
    descriptor = os.open(
        path, os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC |
        getattr(os, "O_NOFOLLOW", 0))
    metadata = os.fstat(descriptor)
    named = os.lstat(path)
    if (
            _metadata_identity(metadata) != _metadata_identity(named) or
            not stat.S_ISDIR(metadata.st_mode) or metadata.st_nlink < 2 or
            metadata.st_uid != expected_uid or metadata.st_gid != expected_gid or
            stat.S_IMODE(metadata.st_mode) != 0o700):
        os.close(descriptor)
        raise AuthorityError("host authority directory metadata mismatch")
    return descriptor


def _activation_artifact_path(root: Path, prefix: str, activation_id: str) -> Path:
    if re.fullmatch(r"[0-9a-f]{32}", activation_id) is None:
        raise AuthorityError("broker activation identifier is invalid")
    return root / (prefix + activation_id + ACTIVATION_ARTIFACT_SUFFIX)


def _validate_activation_handoff(
        item: DomainAuthority, network_fingerprint: ManifestFingerprint,
        *, owner_path: Path, drop_in_path: Path, boot_id_path: Path,
        permit_path: Path, expected_uid: int, expected_gid: int,
) -> tuple[dict[str, Any], bytes, dict[str, Any], bytes, Path]:
    reservation_raw = _read_private_artifact(
        owner_path, expected_uid=expected_uid, expected_gid=expected_gid)
    reservation = _validate_sealed_json(
        reservation_raw, fields=ACTIVATION_RESERVATION_FIELDS,
        schema=ACTIVATION_RESERVATION_SCHEMA,
        label="broker activation reservation")
    activation_id = reservation.get("activation_id")
    if not isinstance(activation_id, str):
        raise AuthorityError("broker activation reservation is invalid")
    consumed_path = _activation_artifact_path(
        owner_path.parent, ACTIVATION_CONSUMED_PREFIX, activation_id)
    consumed_raw = _read_private_artifact(
        consumed_path, expected_uid=expected_uid, expected_gid=expected_gid)
    consumed = _validate_sealed_json(
        consumed_raw, fields=ACTIVATION_CONSUMED_FIELDS,
        schema=ACTIVATION_CONSUMED_SCHEMA,
        label="broker activation consumed receipt")
    copied = (
        "activation_id", "boot_id", "guardian_request_id", "transaction_id",
        "operation", "phase", "request_sha256", "domain",
        "target_identity_manifest_sha256", "target_drop_in_sha256",
        "control_image_sha256", "required_pre_activation_boundary",
        "broker_start_permit_file_sha256",
        "broker_start_permit_body_sha256")
    issued_at_ms = reservation.get("issued_at_ms")
    expires_at_ms = reservation.get("expires_at_ms")
    consumed_at_ms = consumed.get("consumed_at_ms")
    current_boot = _read_boot_id(boot_id_path)
    try:
        drop_in_raw = _read_stable(
            drop_in_path, installed=True, expected_mode=0o644)
    except (FileNotFoundError, OSError) as error:
        raise AuthorityError("broker activation drop-in is unavailable") from error
    digest_fields = (
        "guardian_exe_sha256", "guardian_argv_sha256", "control_image_sha256",
        "request_sha256", "target_identity_manifest_sha256",
        "target_drop_in_sha256", "broker_start_permit_file_sha256",
        "broker_start_permit_body_sha256")
    if (
            reservation.get("status") != "PENDING_BROKER_ACTIVE" or
            reservation.get("domain") != item.domain_id or
            reservation.get("operation") not in {"ENABLE", "ENABLE_RECOVERY"} or
            not isinstance(reservation.get("phase"), str) or
            not reservation["phase"].endswith(
                "START_BROKER_RECOVERY" if reservation["operation"] ==
                "ENABLE_RECOVERY" else "START_BROKER_LOCAL_PAPER") or
            not isinstance(issued_at_ms, int) or isinstance(issued_at_ms, bool) or
            not isinstance(expires_at_ms, int) or isinstance(expires_at_ms, bool) or
            # Keep the reservation window aligned with local-paper-control
            # and broker-egress-policy.  The old 15s value rejected the
            # current bounded 45s broker handoff before activation.
            expires_at_ms - issued_at_ms != 45_000 or
            reservation.get("boot_id") != current_boot or
            reservation.get("required_pre_activation_boundary") != "DENY_ALL" or
            reservation.get("paper_only") is not True or
            reservation.get("live_authorized") is not False or
            any(not isinstance(reservation.get(field), str) or
                SHA256.fullmatch(reservation[field]) is None
                for field in digest_fields) or
            reservation.get("target_identity_manifest_sha256") !=
                "sha256:" + network_fingerprint.sha256 or
            reservation.get("target_drop_in_sha256") != _sha256(drop_in_raw) or
            any(consumed.get(field) != reservation.get(field) for field in copied) or
            consumed.get("status") != "ACTIVE_BOUNDARY_COMMITTED" or
            consumed.get("reservation_file_sha256") !=
                _sha256(reservation_raw) or
            consumed.get("reservation_body_sha256") !=
                reservation.get("body_sha256") or
            not isinstance(consumed_at_ms, int) or isinstance(consumed_at_ms, bool) or
            not issued_at_ms <= consumed_at_ms <= expires_at_ms or
            consumed.get("active_boundary_status") != "EXACT_ACTIVE" or
            consumed.get("paper_authorized") is not True or
            consumed.get("live_authorized") is not False or
            any(not isinstance(consumed.get(field), str) or
                SHA256.fullmatch(consumed[field]) is None for field in (
                    "pre_activation_boundary_state_sha256",
                    "active_boundary_state_sha256")) or
            consumed.get("pre_activation_boundary_state_sha256") ==
                consumed.get("active_boundary_state_sha256")):
        raise AuthorityError("broker activation handoff binding is invalid")
    if permit_path.exists() or permit_path.is_symlink():
        raise AuthorityError("broker activation permit was not consumed")
    intent_path = _activation_artifact_path(
        owner_path.parent, ACTIVATION_INTENT_PREFIX, activation_id)
    if intent_path.exists() or intent_path.is_symlink():
        raise AuthorityError("broker activation intent was not consumed")
    try:
        activation_names = sorted(
            name for name in os.listdir(owner_path.parent)
            if name.startswith(ACTIVATION_CONSUMED_PREFIX))
    except OSError as error:
        raise AuthorityError("broker activation handoff directory unreadable") from error
    if activation_names != [consumed_path.name]:
        raise AuthorityError("broker activation consumed receipt set is ambiguous")
    return reservation, reservation_raw, consumed, consumed_raw, consumed_path


def adopt_runtime_owner(
        item: DomainAuthority, network_fingerprint: ManifestFingerprint,
        owner_path: Path = HOST_OWNER_PATH, *,
        drop_in_path: Path = BROKER_DROP_IN_PATH,
        boot_id_path: Path = BOOT_ID_PATH,
        permit_path: Path = BROKER_START_PERMIT_PATH,
        expected_uid: int = 0, expected_gid: int = 0,
        now_ms: Optional[int] = None,
        process_identity_provider: Callable[[int], dict[str, Any]] =
            _process_identity,
) -> tuple[dict[str, Any], bytes]:
    (reservation, reservation_raw, consumed, consumed_raw,
     consumed_path) = _validate_activation_handoff(
         item, network_fingerprint, owner_path=owner_path,
         drop_in_path=drop_in_path, boot_id_path=boot_id_path,
         permit_path=permit_path, expected_uid=expected_uid,
         expected_gid=expected_gid)
    process = process_identity_provider(os.getpid())
    timestamp = time.time_ns() // 1_000_000 if now_ms is None else now_ms
    if (
            not isinstance(timestamp, int) or isinstance(timestamp, bool) or
            timestamp <= 0 or
            any(not isinstance(process.get(field), int) or
                isinstance(process[field], bool) or process[field] <= 0
                for field in ("pid", "start_ticks")) or
            any(not isinstance(process.get(field), str) or
                SHA256.fullmatch(process[field]) is None
                for field in ("exe_sha256", "argv_sha256"))):
        raise AuthorityError("runtime owner process binding is invalid")
    body = {
        "schema": RUNTIME_OWNER_SCHEMA, "version": 1,
        "status": RUNTIME_OWNER_STATUS, "adopted_at_ms": timestamp,
        "boot_id": reservation["boot_id"], "domain": item.domain_id,
        "activation_id": reservation["activation_id"],
        "transaction_id": reservation["transaction_id"],
        "operation": reservation["operation"], "phase": reservation["phase"],
        "guardian_request_id": reservation["guardian_request_id"],
        "request_sha256": reservation["request_sha256"],
        "reservation_file_sha256": _sha256(reservation_raw),
        "reservation_body_sha256": reservation["body_sha256"],
        "activation_consumed_file_sha256": _sha256(consumed_raw),
        "activation_consumed_body_sha256": consumed["body_sha256"],
        "broker_start_permit_file_sha256":
            reservation["broker_start_permit_file_sha256"],
        "broker_start_permit_body_sha256":
            reservation["broker_start_permit_body_sha256"],
        "pre_activation_boundary_state_sha256":
            consumed["pre_activation_boundary_state_sha256"],
        "active_boundary_state_sha256":
            consumed["active_boundary_state_sha256"],
        "target_identity_manifest_sha256":
            reservation["target_identity_manifest_sha256"],
        "target_drop_in_sha256": reservation["target_drop_in_sha256"],
        "execution_identity": item.identity, "execution_uid": item.uid,
        "execution_gid": item.gid, "control_directory": item.control_directory,
        "kill_switch_marker": item.kill_switch_marker,
        "guard_pid": process["pid"],
        "guard_start_ticks": process["start_ticks"],
        "guard_exe_sha256": process["exe_sha256"],
        "guard_argv_sha256": process["argv_sha256"],
        "mutation_scope": "PAPER_DOMAIN_EGRESS_GUARD_ONLY",
        "paper_authorized": True, "live_authorized": False,
    }
    runtime_raw = _sealed_json(body)
    runtime = _validate_sealed_json(
        runtime_raw, fields=RUNTIME_OWNER_FIELDS, schema=RUNTIME_OWNER_SCHEMA,
        label="PAPER runtime owner")
    directory = _validate_host_authority_directory(
        owner_path.parent, expected_uid=expected_uid, expected_gid=expected_gid)
    temporary = (
        f".{owner_path.name}.{os.getpid()}.{secrets.token_hex(8)}.tmp")
    temporary_descriptor = -1
    try:
        temporary_descriptor = os.open(
            temporary,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_CLOEXEC |
            getattr(os, "O_NOFOLLOW", 0), 0o600, dir_fd=directory)
        os.fchmod(temporary_descriptor, 0o600)
        os.fchown(temporary_descriptor, expected_uid, expected_gid)
        view = memoryview(runtime_raw)
        while view:
            written = os.write(temporary_descriptor, view)
            if written <= 0:
                raise AuthorityError("PAPER runtime owner write was short")
            view = view[written:]
        os.fsync(temporary_descriptor)
        if _read_private_artifact(
                owner_path, expected_uid=expected_uid,
                expected_gid=expected_gid) != reservation_raw:
            raise AuthorityError("broker activation reservation changed before adopt")
        os.replace(
            temporary, owner_path.name,
            src_dir_fd=directory, dst_dir_fd=directory)
        os.fsync(directory)
        if _read_private_artifact(
                owner_path, expected_uid=expected_uid,
                expected_gid=expected_gid) != runtime_raw:
            raise AuthorityError("PAPER runtime owner adoption verification failed")
        if _read_private_artifact(
                consumed_path, expected_uid=expected_uid,
                expected_gid=expected_gid) != consumed_raw:
            raise AuthorityError("broker activation consumed receipt changed")
        os.unlink(consumed_path.name, dir_fd=directory)
        os.fsync(directory)
        try:
            os.stat(
                consumed_path.name, dir_fd=directory, follow_symlinks=False)
        except FileNotFoundError:
            pass
        else:
            raise AuthorityError("broker activation consumed receipt retained")
    except OSError as error:
        raise AuthorityError("PAPER runtime owner adoption failed") from error
    finally:
        if temporary_descriptor >= 0:
            os.close(temporary_descriptor)
        try:
            os.unlink(temporary, dir_fd=directory)
        except FileNotFoundError:
            pass
        os.close(directory)
    return runtime, runtime_raw


def _read_owner(path: Path = HOST_OWNER_PATH) -> Optional[str]:
    try:
        raw = _read_private_artifact(path)
    except FileNotFoundError:
        return None
    owner = _validate_sealed_json(
        raw, fields=RUNTIME_OWNER_FIELDS, schema=RUNTIME_OWNER_SCHEMA,
        label="PAPER runtime owner")
    if (
            owner.get("status") != RUNTIME_OWNER_STATUS or
            not isinstance(owner.get("domain"), str) or
            DOMAIN.fullmatch(owner["domain"]) is None or
            owner.get("boot_id") != _read_boot_id() or
            owner.get("paper_authorized") is not True or
            owner.get("live_authorized") is not False or
            owner.get("mutation_scope") != "PAPER_DOMAIN_EGRESS_GUARD_ONLY"):
        raise AuthorityError("PAPER runtime owner content mismatch")
    return owner["domain"]


def clear_owner_tombstone(
        domain: str, path: Path = HOST_OWNER_PATH,
        *, missing_ok: bool = False, expected_payload: Optional[bytes] = None,
) -> None:
    try:
        raw = _read_private_artifact(path)
    except FileNotFoundError:
        if missing_ok:
            return
        raise AuthorityError("PAPER runtime owner is missing")
    owner = _validate_sealed_json(
        raw, fields=RUNTIME_OWNER_FIELDS, schema=RUNTIME_OWNER_SCHEMA,
        label="PAPER runtime owner")
    if owner.get("domain") != domain:
        raise AuthorityError("PAPER runtime owner belongs to another domain")
    if expected_payload is not None and raw != expected_payload:
        raise AuthorityError("PAPER runtime owner changed before cleanup")
    directory = _validate_host_authority_directory(
        path.parent, expected_uid=0, expected_gid=0)
    try:
        if _read_private_artifact(path) != raw:
            raise AuthorityError("PAPER runtime owner changed before cleanup")
        os.unlink(path.name, dir_fd=directory)
        os.fsync(directory)
    finally:
        os.close(directory)


def _network_command(
        network_path: Path, action: str, domain: Optional[str] = None,
) -> subprocess.CompletedProcess[bytes]:
    arguments = [
        str(NETWORK_HELPER),
        "--policy", str(NETWORK_POLICY),
        "--identity-manifest", str(SERVICE_IDENTITIES),
    ]
    if action == "check":
        arguments.extend((
            "--paper-identities", str(network_path),
            "--check-active",
        ))
    elif action == "activate":
        if not isinstance(domain, str) or DOMAIN.fullmatch(domain) is None:
            raise AuthorityError("network activation domain is invalid")
        arguments.extend((
            "--paper-identities", str(network_path),
            "--activate-paper-domain",
            "--domain", domain,
        ))
    elif action == "revoke":
        arguments.append("--tighten-deny-all")
    elif action == "check-deny-all":
        arguments.append("--check-deny-all")
    else:
        raise AuthorityError("invalid network helper action")
    try:
        completed = subprocess.run(
            arguments,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=SAFE_ENVIRONMENT,
            cwd="/",
            close_fds=True,
            timeout=10,
            check=False,
        )
    except (OSError, subprocess.SubprocessError) as error:
        raise AuthorityError("broker network helper execution failed") from error
    if (
            len(completed.stdout) > MAX_COMMAND_OUTPUT or
            len(completed.stderr) > MAX_COMMAND_OUTPUT):
        raise AuthorityError("broker network helper output exceeded bound")
    return completed


def verify_live_network(network_path: Path) -> None:
    completed = _network_command(network_path, "check")
    if completed.returncode != 0 or completed.stderr:
        raise AuthorityError(
            "live broker network table does not exactly authorize this domain")


def activate_live_network(network_path: Path, domain: str) -> None:
    completed = _network_command(network_path, "activate", domain)
    if completed.returncode != 0 or completed.stderr:
        raise AuthorityError("domain broker network activation failed")


def revoke_live_network() -> None:
    completed = _network_command(DEFAULT_NETWORK_IDENTITIES, "revoke")
    if completed.returncode == 0 and not completed.stderr:
        return
    verified = _network_command(
        DEFAULT_NETWORK_IDENTITIES, "check-deny-all")
    if verified.returncode != 0 or verified.stderr:
        raise AuthorityError("deny-all broker network revocation failed")


def finalize_stop(
        domain: str,
        *,
        lock_path: Path = HOST_LOCK_PATH,
        owner_path: Path = HOST_OWNER_PATH,
        network_revoker: Callable[[], None] = revoke_live_network,
        lease_acquirer: Callable[..., int] = acquire_host_lease,
        owner_reader: Callable[[Path], Optional[str]] = _read_owner,
        owner_clearer: Callable[..., None] = clear_owner_tombstone,
) -> None:
    try:
        observed_owner = owner_reader(owner_path)
    except (AuthorityError, OSError, ValueError) as owner_error:
        try:
            network_revoker()
        except (AuthorityError, OSError, ValueError) as revoke_error:
            raise AuthorityError(
                "owner metadata invalid and emergency broker revocation "
                "failed: " + str(revoke_error)) from revoke_error
        raise owner_error
    if observed_owner is not None and observed_owner != domain:
        # ExecStopPost also runs for a rejected competing unit.  It must not
        # wait behind, revoke, or clear the authority owned by another domain.
        return
    nonblocking = observed_owner is None
    try:
        lease = lease_acquirer(lock_path, nonblocking=nonblocking)
    except LeaseBusyError:
        if nonblocking:
            # The winner owns the valid lock but has not yet published its
            # tombstone. Owner creation precedes egress activation under this
            # same lease, so the rejected finalizer neither waits nor revokes.
            return
        raise
    except (AuthorityError, OSError, ValueError) as lease_error:
        try:
            network_revoker()
        except (AuthorityError, OSError, ValueError) as revoke_error:
            raise AuthorityError(
                "lease metadata invalid and emergency broker revocation "
                "failed: " + str(revoke_error)) from revoke_error
        raise lease_error
    try:
        network_revoker()
        owner_clearer(domain, owner_path, missing_ok=True)
    finally:
        try:
            fcntl.flock(lease, fcntl.LOCK_UN)
        finally:
            os.close(lease)


def guard_authority(
        item: DomainAuthority,
        fingerprints: tuple[ManifestFingerprint, ManifestFingerprint],
        notifier: SystemdNotifier,
        stop_event: threading.Event,
        *,
        lock_path: Path = HOST_LOCK_PATH,
        poll_interval: float = DEFAULT_POLL_INTERVAL_SECONDS,
        fingerprint_checker: Callable[[ManifestFingerprint], bool] =
        manifest_fingerprint_matches,
        startup_runtime_checker: Callable[
            [DomainAuthority], None] = validate_runtime,
        runtime_checker: Callable[
            [DomainAuthority], None] = validate_runtime_lifecycle,
        network_activator: Optional[Callable[[Path, str], None]] = None,
        network_checker: Callable[[Path], None] = verify_live_network,
        network_revoker: Callable[[], None] = revoke_live_network,
        lease_acquirer: Callable[[Path], int] = acquire_host_lease,
        owner_adopter: Callable[
            [DomainAuthority, ManifestFingerprint, Path],
            Optional[tuple[dict[str, Any], bytes]]] = adopt_runtime_owner,
        owner_creator: Optional[Callable[[str, Path], None]] = None,
        owner_clearer: Callable[..., None] = clear_owner_tombstone,
) -> None:
    if poll_interval < 0.01 or poll_interval > 5:
        raise AuthorityError(
            "PAPER authority guard poll interval is outside safety bounds")
    # Kept as an injected compatibility seam for older unit tests.  Production
    # deliberately never invokes an activation callback from this preflight.
    del network_activator
    lease = lease_acquirer(lock_path)
    owner_path = lock_path.with_name(HOST_OWNER_PATH.name)
    drift = ""
    owner_created = False
    runtime_owner_payload: Optional[bytes] = None
    try:
        try:
            startup_runtime_checker(item)
            # Local control and the broker have already committed the exact
            # ACTIVE boundary while retaining the activation reservation as the
            # host owner.  Verify that boundary before and after the atomic
            # reservation -> runtime-owner CAS.  This preflight never grants or
            # re-grants egress authority.
            network_checker(fingerprints[0].path)
            if owner_creator is None:
                adopted = owner_adopter(item, fingerprints[0], owner_path)
            else:
                owner_creator(item.domain_id, owner_path)
                adopted = None
            owner_created = True
            if adopted is not None:
                _runtime_owner, runtime_owner_payload = adopted
            network_checker(fingerprints[0].path)
            notifier.send(
                "READY=1\n"
                f"STATUS=HeptaTrader PAPER host lease held by {item.domain_id}\n"
                "WATCHDOG=1")
            while not stop_event.wait(poll_interval):
                if any(
                        not fingerprint_checker(fingerprint)
                        for fingerprint in fingerprints):
                    if stop_event.is_set():
                        break
                    drift = "PAPER manifest path/inode/digest drift"
                    break
                try:
                    notifier.send(
                        "WATCHDOG=1\n"
                        f"STATUS=HeptaTrader PAPER host lease validating "
                        f"{item.domain_id}")
                    runtime_checker(item)
                    notifier.send(
                        "WATCHDOG=1\n"
                        f"STATUS=HeptaTrader PAPER network boundary validating "
                        f"{item.domain_id}")
                    network_checker(fingerprints[0].path)
                except (AuthorityError, OSError, ValueError):
                    if stop_event.is_set():
                        break
                    raise
                notifier.send(
                    "WATCHDOG=1\n"
                    f"STATUS=HeptaTrader PAPER host lease held by "
                    f"{item.domain_id}")
        except (AuthorityError, OSError, ValueError) as error:
            # systemd may deliver SIGTERM while a bounded verification call is
            # in flight.  Once the stop request is observable, that verifier's
            # failure is not authority drift: the common clean-stop path below
            # still installs DENY_ALL before retiring the exact runtime owner.
            if not stop_event.is_set():
                drift = str(error)
        if drift:
            try:
                notifier.send(
                    "STOPPING=1\n"
                    "STATUS=HeptaTrader PAPER authority drift; "
                    "revoking domain broker authority")
            except AuthorityError:
                pass
            try:
                network_revoker()
            except (AuthorityError, OSError, ValueError) as error:
                raise AuthorityError(
                    "PAPER authority drift and emergency revocation failed: " +
                    str(error)) from error
            if owner_created:
                if runtime_owner_payload is None:
                    owner_clearer(item.domain_id, owner_path)
                else:
                    owner_clearer(
                        item.domain_id, owner_path,
                        expected_payload=runtime_owner_payload)
                owner_created = False
            raise AuthorityError(
                "PAPER authority guard detected drift and revoked "
                "domain broker authority: " + drift)
        try:
            notifier.send(
                "STOPPING=1\n"
                "STATUS=HeptaTrader PAPER authority stopping cleanly; "
                "revoking domain broker authority")
        except AuthorityError:
            pass
        network_revoker()
        if owner_created:
            if runtime_owner_payload is None:
                owner_clearer(item.domain_id, owner_path)
            else:
                owner_clearer(
                    item.domain_id, owner_path,
                    expected_payload=runtime_owner_payload)
            owner_created = False
    finally:
        # A failed revocation deliberately leaves the tombstone in /run.  No
        # competing domain may start until ExecStopPost obtains this same
        # flock, installs deny-all and clears the exact owner.
        del owner_created
        try:
            fcntl.flock(lease, fcntl.LOCK_UN)
        finally:
            os.close(lease)


def _install_stop_handlers(stop_event: threading.Event) -> dict[int, Any]:
    previous: dict[int, Any] = {}

    def request_stop(_signal_number: int, _frame: Any) -> None:
        stop_event.set()

    for signal_number in (
            signal.SIGTERM, signal.SIGINT, signal.SIGHUP, signal.SIGABRT):
        previous[signal_number] = signal.getsignal(signal_number)
        signal.signal(signal_number, request_stop)
    return previous


def _restore_stop_handlers(previous: dict[int, Any]) -> None:
    for signal_number, handler in previous.items():
        signal.signal(signal_number, handler)


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--network-identities", type=Path,
        default=DEFAULT_NETWORK_IDENTITIES)
    parser.add_argument(
        "--authorizations", type=Path, default=DEFAULT_AUTHORIZATIONS)
    actions = parser.add_mutually_exclusive_group(required=True)
    actions.add_argument("--render-tmpfiles", action="store_true")
    actions.add_argument("--check-runtime", action="store_true")
    actions.add_argument("--guard", action="store_true")
    actions.add_argument("--operator-disarm", action="store_true")
    actions.add_argument("--operator-reengage", action="store_true")
    actions.add_argument(
        "--finalize-stop", action="store_true",
        help=("obtain the host lease, revoke all broker authority and clear "
              "the exact crash-owner tombstone"))
    parser.add_argument("--domain")
    parser.add_argument("--cycle-id")
    parser.add_argument("--intent-sha256")
    parser.add_argument("--operator-ttl-sec", type=int, default=20)
    parser.add_argument(
        "--operator-source", choices=("operator", "watchdog"),
        default="operator")
    arguments = parser.parse_args(argv)
    try:
        if arguments.finalize_stop:
            if (
                    not isinstance(arguments.domain, str) or
                    DOMAIN.fullmatch(arguments.domain) is None):
                raise AuthorityError(
                    "stop finalizer requires one canonical --domain")
            if os.geteuid() != 0 or os.getegid() != 0:
                raise AuthorityError("stop finalization requires root")
            service_result = os.environ.get("SERVICE_RESULT", "")
            finalize_stop(arguments.domain)
            print(
                "hepta_ib_paper_domain_authority: PASS "
                f"domain={arguments.domain} "
                f"stop_result={service_result or 'missing'} "
                "domain_authority=revoked")
            return 0
        operator_action = arguments.operator_disarm or arguments.operator_reengage
        if arguments.guard:
            authorities, fingerprints = load_with_fingerprints(
                arguments.network_identities,
                arguments.authorizations)
        else:
            authorities = load(
                arguments.network_identities,
                arguments.authorizations,
                installed=arguments.check_runtime or operator_action)
            fingerprints = None
        if arguments.render_tmpfiles:
            if arguments.domain is not None:
                raise AuthorityError(
                    "--domain is invalid with --render-tmpfiles")
            sys.stdout.buffer.write(render_tmpfiles(authorities))
            return 0
        if (
                not isinstance(arguments.domain, str) or
                DOMAIN.fullmatch(arguments.domain) is None):
            raise AuthorityError(
                "runtime authority action requires one canonical --domain")
        selected = [
            item for item in authorities
            if item.domain_id == arguments.domain]
        if len(selected) != 1:
            raise AuthorityError(
                "requested domain is not explicitly PAPER-authorized")
        if os.geteuid() != 0 or os.getegid() != 0:
            raise AuthorityError("runtime validation requires root")
        if operator_action:
            if not isinstance(arguments.cycle_id, str):
                raise AuthorityError("operator action requires --cycle-id")
            if not isinstance(arguments.intent_sha256, str):
                raise AuthorityError("operator action requires --intent-sha256")
            if arguments.operator_disarm:
                validate_runtime(selected[0])
                state = operator_disarm(
                    selected[0], arguments.cycle_id,
                    arguments.intent_sha256, arguments.operator_ttl_sec)
            else:
                validate_runtime_lifecycle(selected[0])
                state = operator_reengage(
                    selected[0], arguments.cycle_id,
                    arguments.intent_sha256, source=arguments.operator_source)
            print(_canonical_json({
                "status": state["status"], "domain": arguments.domain,
                "cycle_id": arguments.cycle_id,
                "intent_sha256": arguments.intent_sha256,
                "deadline_at_ms": state["deadline_at_ms"],
            }).decode("ascii"), end="")
            return 0
        if arguments.check_runtime:
            validate_runtime(selected[0])
        else:
            if fingerprints is None:
                raise AuthorityError(
                    "guard manifest fingerprints are unavailable")
            notifier = SystemdNotifier(required=True)
            stop_event = threading.Event()
            previous_handlers = _install_stop_handlers(stop_event)
            try:
                guard_authority(
                    selected[0],
                    fingerprints,
                    notifier,
                    stop_event,
                    poll_interval=_watchdog_poll_interval())
            finally:
                _restore_stop_handlers(previous_handlers)
    except (AuthorityError, OSError, ValueError) as error:
        print(
            f"hepta_ib_paper_domain_authority: FAIL: {error}",
            file=sys.stderr)
        return 1
    print(
        "hepta_ib_paper_domain_authority: PASS "
        f"domain={arguments.domain} kill_switch=engaged")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
