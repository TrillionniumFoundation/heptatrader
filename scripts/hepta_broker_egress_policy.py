#!/usr/bin/env python3

"""Install the versioned HeptaTrader broker-port egress boundary.

The policy is deliberately narrow: it does not remove Agent internet access.
It rejects locally generated TCP traffic to the reviewed IB API ports unless
the socket is owned by the exact IB Execution UID bound by the service-identity
manifest.  The command never removes the table; a stopped unit therefore does
not silently reopen broker access.
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
import signal
import socket
import stat
import subprocess
import sys
import threading
import time
from typing import Any, Callable, Optional, Sequence


DEFAULT_POLICY = Path(
    "/usr/share/heptatrader/hepta-broker-network-policy-v1.json")
DEFAULT_IDENTITIES = Path(
    "/usr/share/heptatrader/hepta-service-identities-v1.json")
DEFAULT_PAPER_IDENTITIES = Path(
    "/etc/heptatrader/"
    "hepta-agent-trust-domain-paper-identities-v1.json")
NFT_CANDIDATES = (Path("/usr/sbin/nft"), Path("/sbin/nft"))
EXPECTED_PORTS = (4001, 4002, 7496, 7497)
EXPECTED_IDENTITY = "hepta-ib-exec"
EXPECTED_ROLE = "ib-paper-execution-authority"
PAPER_IDENTITY_SCHEMA = "hepta.agent-trust-domain-paper-identities.v1"
PAPER_IDENTITY_PATH = str(DEFAULT_PAPER_IDENTITIES)
EXPECTED_FAMILY = "inet"
EXPECTED_TABLE = "hepta_broker_egress_v1"
EXPECTED_CHAIN = "output"
GUARD_CHAIN = "ib_guard"
# No cross-domain account/client-id/risk aggregation exists yet.  A host may
# therefore opt in at most one templated PAPER authority.  A templated
# authority replaces, rather than extends, the fixed compatibility authority.
MAX_PAPER_IDENTITIES = 1
MAX_SOURCE_BYTES = 64 * 1024
MAX_NFT_OUTPUT_BYTES = 1024 * 1024
DEFAULT_POLL_INTERVAL_SECONDS = 0.25
BOUNDARY_RUNTIME_DIRECTORY = Path("/run/hepta-broker-egress-policy")
BOUNDARY_LOCK_NAME = "generation.lock"
BOUNDARY_LEDGER_NAME = "generation-state.v1.json"
BOUNDARY_RECEIPT_NAME = "current-boundary.v1.json"
BOUNDARY_RECEIPT_SCHEMA = "hepta.broker-egress-current-boundary.v1"
BOUNDARY_LEDGER_SCHEMA = "hepta.broker-egress-generation-state.v1"
# The local-paper guardian is loaded as a systemd credential and is itself
# hashed as part of the one-shot activation handoff.  The hardened guardian
# image is currently ~256 KiB; retaining the old 64 KiB boundary made every
# legitimate activation fail closed with "activation artifact metadata is
# unsafe" before the broker policy could be evaluated.  Keep a bounded upper
# limit while allowing the installed helper image and future small growth.
BOUNDARY_MAXIMUM_BYTES = 512 * 1024
BOUNDARY_MAXIMUM_AGE_MS = 2_000
BOOT_ID_PATH = Path("/proc/sys/kernel/random/boot_id")
HOST_AUTHORITY_DIRECTORY = Path("/run/hepta/ib-paper-host-authority")
HOST_AUTHORITY_LEASE_NAME = "lease.lock"
HOST_AUTHORITY_OWNER_NAME = "owner.v1"
ACTIVATION_RESERVATION_SCHEMA = (
    "hepta.local-paper-broker-activation-reservation.v1")
ACTIVATION_CONSUMED_SCHEMA = (
    "hepta.local-paper-broker-activation-consumed.v1")
ACTIVATION_INTENT_SCHEMA = (
    "hepta.local-paper-broker-activation-commit-intent.v1")
ACTIVATION_INTENT_NAME_PREFIX = "activation-commit-intent."
ACTIVATION_CONSUMED_NAME_PREFIX = "activation-consumed."
ACTIVATION_CONSUMED_NAME_SUFFIX = ".v1.json"
BROKER_START_PERMIT_PATH = Path(
    "/run/hepta-local-paper-control/alpha/broker-start-permit.json")
BROKER_START_PERMIT_SCHEMA = "hepta.local-paper-broker-start-permit.v1"
BROKER_DROP_IN_PATH = Path(
    "/etc/systemd/system/hepta-broker-egress-policy.service.d/"
    "20-local-paper.conf")
LOCAL_CONTROL_CREDENTIAL_NAME = "hepta-local-paper-control.py"
ACTIVATION_TTL_MS = 45_000
BOOT_ID = re.compile(
    r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}")
# Kernel mutations stay on the original short deadline.  The one read-only
# live-table query performed by each watchdog sample gets modest scheduling
# slack for a busy host, but remains bounded.  A failed sample followed by a
# complete deny-all transaction is therefore bounded by two query deadlines
# plus three mutation deadlines (12 seconds), below the 15-second service
# watchdog.  There are no retries and no stale samples are reused.
NFT_COMMAND_TIMEOUT_SECONDS = 2
NFT_QUERY_TIMEOUT_SECONDS = 3
SAFE_ENVIRONMENT = {
    "PATH": "/usr/sbin:/usr/bin:/sbin:/bin",
    "LANG": "C",
    "LC_ALL": "C",
}


class PolicyError(RuntimeError):
    """A fail-closed policy or installation error."""


@dataclass(frozen=True)
class BrokerConnector:
    domain_id: str
    identity: str
    uid: int
    gid: int
    role: str


@dataclass(frozen=True)
class BrokerNetworkPolicy:
    family: str
    table: str
    chain: str
    ports: tuple[int, ...]
    authorized_connectors: tuple[BrokerConnector, ...]
    source_sha256: str
    identity_manifest_sha256: str
    effective_sha256: str

    @property
    def authorized_uids(self) -> tuple[int, ...]:
        return tuple(connector.uid for connector in self.authorized_connectors)


@dataclass(frozen=True)
class SourceFingerprint:
    path: Path
    present: bool
    device: int = 0
    inode: int = 0
    mode: int = 0
    links: int = 0
    uid: int = 0
    gid: int = 0
    size: int = 0
    mtime_ns: int = 0
    ctime_ns: int = 0
    sha256: str = ""
    installed_mode: Optional[int] = None


@dataclass(frozen=True)
class LoadedPolicy:
    policy: BrokerNetworkPolicy
    fixed_only: BrokerNetworkPolicy
    deny_all: BrokerNetworkPolicy
    fingerprints: tuple[SourceFingerprint, ...]
    explicit_deny_all_authorization: bool = False


@dataclass(frozen=True)
class BoundaryReceipt:
    document: dict[str, Any]
    payload: bytes
    file_sha256: str


ACTIVATION_PERMIT_FIELDS = {
    "schema", "version", "issued_at_ms", "expires_at_ms", "boot_id",
    "guardian_pid", "guardian_start_ticks", "guardian_exe_sha256",
    "guardian_argv_sha256", "control_image_sha256", "guardian_request_id",
    "domain", "transaction_id", "operation", "phase", "request_sha256",
    "target_identity_manifest_sha256", "target_drop_in_sha256",
}
ACTIVATION_RESERVATION_FIELDS = {
    "schema", "version", "status", "activation_id", "issued_at_ms",
    "expires_at_ms", "boot_id", "guardian_pid", "guardian_start_ticks",
    "guardian_exe_sha256", "guardian_argv_sha256", "control_image_sha256",
    "guardian_request_id", "domain", "transaction_id", "operation", "phase",
    "request_sha256", "target_identity_manifest_sha256",
    "target_drop_in_sha256", "broker_start_permit_file_sha256",
    "broker_start_permit_body_sha256", "required_pre_activation_boundary",
    "paper_only", "live_authorized",
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
}
ACTIVATION_INTENT_FIELDS = {
    "schema", "version", "status", "activation_id", "recorded_at_ms",
    "boot_id", "reservation_file_sha256", "reservation_body_sha256",
    "broker_start_permit_file_sha256", "broker_start_permit_body_sha256",
    "target_identity_manifest_sha256", "target_drop_in_sha256",
    "required_pre_activation_boundary",
    "pre_activation_boundary_state_sha256", "paper_authorized",
    "live_authorized",
}


Runner = Callable[
    [Sequence[str], Optional[bytes]], subprocess.CompletedProcess[bytes]]


def _reject_duplicate_keys(
        pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate JSON key: {key}")
        result[key] = value
    return result


def _reject_non_finite(value: str) -> Any:
    raise ValueError(f"non-finite JSON number: {value}")


def _read_stable_regular(
        path: Path, *, require_installed_metadata: bool,
        installed_mode: Optional[int] = None) -> bytes:
    try:
        before = os.lstat(path)
    except FileNotFoundError:
        raise
    except OSError as error:
        raise PolicyError(f"cannot inspect {path}: {error.strerror}") from error
    if (
            not stat.S_ISREG(before.st_mode) or
            stat.S_ISLNK(before.st_mode) or
            before.st_nlink != 1 or
            before.st_size < 2 or
            before.st_size > MAX_SOURCE_BYTES or
            stat.S_IMODE(before.st_mode) & 0o002 or
            (require_installed_metadata and (
                (before.st_uid, before.st_gid) != (0, 0) or
                stat.S_IMODE(before.st_mode) & 0o020 or
                (installed_mode is not None and
                 stat.S_IMODE(before.st_mode) != installed_mode)))):
        raise PolicyError(f"{path}: unsafe policy source metadata")
    flags = os.O_RDONLY | os.O_CLOEXEC | getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(path, flags)
    try:
        opened = os.fstat(descriptor)
        chunks: list[bytes] = []
        total = 0
        while True:
            chunk = os.read(
                descriptor, min(65536, MAX_SOURCE_BYTES + 1 - total))
            if not chunk:
                break
            chunks.append(chunk)
            total += len(chunk)
            if total > MAX_SOURCE_BYTES:
                raise PolicyError(f"{path}: policy source exceeds size limit")
        after = os.fstat(descriptor)
    finally:
        os.close(descriptor)
    stable_fields = (
        "st_dev", "st_ino", "st_mode", "st_nlink", "st_uid", "st_gid",
        "st_size", "st_mtime_ns", "st_ctime_ns")
    if any(
            getattr(before, field) != getattr(opened, field) or
            getattr(opened, field) != getattr(after, field)
            for field in stable_fields):
        raise PolicyError(f"{path}: policy source changed while reading")
    return b"".join(chunks)


def _read_fingerprinted(
        path: Path, *, require_installed_metadata: bool,
        installed_mode: Optional[int], optional: bool = False,
) -> tuple[Optional[bytes], SourceFingerprint]:
    try:
        before = os.lstat(path)
    except FileNotFoundError:
        if optional:
            return None, SourceFingerprint(
                path=path, present=False, installed_mode=installed_mode)
        raise
    raw = _read_stable_regular(
        path,
        require_installed_metadata=require_installed_metadata,
        installed_mode=installed_mode)
    after = os.lstat(path)
    fields = (
        "st_dev", "st_ino", "st_mode", "st_nlink", "st_uid", "st_gid",
        "st_size", "st_mtime_ns", "st_ctime_ns")
    if any(getattr(before, field) != getattr(after, field) for field in fields):
        raise PolicyError(f"{path}: policy source changed around stable read")
    return raw, SourceFingerprint(
        path=path,
        present=True,
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
        installed_mode=installed_mode,
    )


def source_fingerprint_matches(
        expected: SourceFingerprint, *,
        require_installed_metadata: bool = True) -> bool:
    try:
        _raw, observed = _read_fingerprinted(
            expected.path,
            require_installed_metadata=require_installed_metadata,
            installed_mode=expected.installed_mode,
            optional=not expected.present)
    except (FileNotFoundError, OSError, PolicyError):
        return False
    return observed == expected


def _strict_json(raw: bytes, label: str) -> dict[str, Any]:
    try:
        text = raw.decode("utf-8", errors="strict")
        value = json.loads(
            text,
            object_pairs_hook=_reject_duplicate_keys,
            parse_constant=_reject_non_finite)
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as error:
        raise PolicyError(f"{label}: invalid strict JSON: {error}") from error
    if not isinstance(value, dict):
        raise PolicyError(f"{label}: root must be an object")
    return value


def _canonical_json(value: Any) -> bytes:
    try:
        return (json.dumps(
            value, ensure_ascii=True, allow_nan=False, sort_keys=True,
            separators=(",", ":")) + "\n").encode("ascii")
    except (TypeError, ValueError, UnicodeError) as error:
        raise PolicyError("boundary receipt is not canonical JSON") from error


def _sha256(raw: bytes) -> str:
    return "sha256:" + hashlib.sha256(raw).hexdigest()


def _sealed_json(document: dict[str, Any]) -> bytes:
    if "body_sha256" in document:
        raise PolicyError("boundary receipt body hash is duplicated")
    body = _canonical_json(document)
    sealed = dict(document)
    sealed["body_sha256"] = _sha256(body)
    return _canonical_json(sealed)


def _validate_sealed_json(
        raw: bytes, *, schema: str, expected_fields: set[str],
        label: str) -> dict[str, Any]:
    if not 2 <= len(raw) <= BOUNDARY_MAXIMUM_BYTES:
        raise PolicyError(f"{label}: size is outside bounds")
    document = _strict_json(raw, label)
    if set(document) != expected_fields | {"body_sha256"}:
        raise PolicyError(f"{label}: fields mismatch")
    if document.get("schema") != schema or document.get("version") != 1:
        raise PolicyError(f"{label}: schema/version mismatch")
    claimed = document.get("body_sha256")
    if (
            not isinstance(claimed, str) or
            re.fullmatch(r"sha256:[0-9a-f]{64}", claimed) is None):
        raise PolicyError(f"{label}: body hash is invalid")
    body = dict(document)
    del body["body_sha256"]
    if claimed != _sha256(_canonical_json(body)):
        raise PolicyError(f"{label}: body hash mismatch")
    if raw != _canonical_json(document):
        raise PolicyError(f"{label}: JSON is not canonical")
    return document


def _read_proc_value(path: Path, maximum: int) -> bytes:
    try:
        before = os.lstat(path)
        if (
                not stat.S_ISREG(before.st_mode) or
                stat.S_ISLNK(before.st_mode) or before.st_nlink != 1):
            raise PolicyError("kernel identity source metadata mismatch")
        descriptor = os.open(
            path, os.O_RDONLY | os.O_CLOEXEC | os.O_NONBLOCK |
            getattr(os, "O_NOFOLLOW", 0))
        opened = os.fstat(descriptor)
        payload = bytearray()
        while len(payload) <= maximum:
            chunk = os.read(
                descriptor, min(4096, maximum + 1 - len(payload)))
            if not chunk:
                break
            payload.extend(chunk)
        after = os.fstat(descriptor)
        os.close(descriptor)
    except OSError as error:
        raise PolicyError("kernel identity source cannot be read") from error
    fields = ("st_dev", "st_ino", "st_mode", "st_nlink", "st_uid", "st_gid")
    if (
            not payload or len(payload) > maximum or
            any(getattr(before, field) != getattr(opened, field) or
                getattr(opened, field) != getattr(after, field)
                for field in fields)):
        raise PolicyError("kernel identity source changed while reading")
    return bytes(payload)


def _read_boot_id(path: Path = BOOT_ID_PATH) -> str:
    raw = _read_proc_value(path, 64)
    try:
        value = raw.decode("ascii", errors="strict")
    except UnicodeError as error:
        raise PolicyError("current boot ID is invalid") from error
    if (
            len(value) != 37 or not value.endswith("\n") or
            BOOT_ID.fullmatch(value[:-1]) is None or
            value[:-1] == "00000000-0000-0000-0000-000000000000"):
        raise PolicyError("current boot ID is invalid")
    return value[:-1]


def _process_start_ticks(pid: int, proc_root: Path = Path("/proc")) -> int:
    if not isinstance(pid, int) or isinstance(pid, bool) or pid <= 0:
        raise PolicyError("boundary publisher PID is invalid")
    path = proc_root / str(pid) / "stat"
    raw = _read_proc_value(path, 64 * 1024)
    try:
        text = raw.decode("ascii", errors="strict")
        end = text.rfind(") ")
        if end <= 0:
            raise ValueError
        value = int(text[end + 2:].split()[19], 10)
    except (UnicodeError, ValueError, IndexError) as error:
        raise PolicyError("boundary publisher start time is invalid") from error
    if value <= 0:
        raise PolicyError("boundary publisher start time is invalid")
    return value


def _directory_metadata(
        path: Path, *, expected_uid: int, expected_gid: int) -> tuple[int, ...]:
    try:
        metadata = os.lstat(path)
    except OSError as error:
        raise PolicyError("boundary runtime directory is unavailable") from error
    if (
            not stat.S_ISDIR(metadata.st_mode) or
            stat.S_ISLNK(metadata.st_mode) or metadata.st_nlink < 2 or
            metadata.st_uid != expected_uid or metadata.st_gid != expected_gid or
            stat.S_IMODE(metadata.st_mode) != 0o700):
        raise PolicyError("boundary runtime directory metadata mismatch")
    return (
        metadata.st_dev, metadata.st_ino, metadata.st_mode,
        metadata.st_uid, metadata.st_gid)


def _open_runtime_directory(
        path: Path, *, expected_uid: int, expected_gid: int) -> int:
    identity = _directory_metadata(
        path, expected_uid=expected_uid, expected_gid=expected_gid)
    try:
        descriptor = os.open(
            path, os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC |
            getattr(os, "O_NOFOLLOW", 0))
    except OSError as error:
        raise PolicyError("boundary runtime directory cannot be opened") from error
    opened = os.fstat(descriptor)
    if (
            (opened.st_dev, opened.st_ino, opened.st_mode,
             opened.st_uid, opened.st_gid) != identity):
        os.close(descriptor)
        raise PolicyError("boundary runtime directory changed while opening")
    return descriptor


def _validate_private_metadata(
        metadata: os.stat_result, *, expected_uid: int, expected_gid: int,
        empty: bool = False) -> None:
    if (
            not stat.S_ISREG(metadata.st_mode) or metadata.st_nlink != 1 or
            metadata.st_uid != expected_uid or metadata.st_gid != expected_gid or
            stat.S_IMODE(metadata.st_mode) != 0o600 or
            (empty and metadata.st_size != 0) or
            (not empty and not 2 <= metadata.st_size <= BOUNDARY_MAXIMUM_BYTES)):
        raise PolicyError("boundary private file metadata mismatch")


def _open_generation_lock(
        directory: int, *, expected_uid: int, expected_gid: int) -> int:
    flags = os.O_RDWR | os.O_CLOEXEC | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(BOUNDARY_LOCK_NAME, flags, dir_fd=directory)
    except FileNotFoundError:
        try:
            created = os.open(
                BOUNDARY_LOCK_NAME,
                flags | os.O_CREAT | os.O_EXCL, 0o600, dir_fd=directory)
            os.fchmod(created, 0o600)
            os.fsync(created)
            os.fsync(directory)
            descriptor = created
        except FileExistsError:
            descriptor = os.open(BOUNDARY_LOCK_NAME, flags, dir_fd=directory)
        except OSError as error:
            raise PolicyError("boundary generation lock creation failed") from error
    except OSError as error:
        raise PolicyError("boundary generation lock open failed") from error
    try:
        _validate_private_metadata(
            os.fstat(descriptor), expected_uid=expected_uid,
            expected_gid=expected_gid, empty=True)
        named = os.stat(
            BOUNDARY_LOCK_NAME, dir_fd=directory, follow_symlinks=False)
        _validate_private_metadata(
            named, expected_uid=expected_uid, expected_gid=expected_gid,
            empty=True)
        opened = os.fstat(descriptor)
        if (opened.st_dev, opened.st_ino) != (named.st_dev, named.st_ino):
            raise PolicyError("boundary generation lock identity mismatch")
        return descriptor
    except Exception:
        os.close(descriptor)
        raise


def _read_private_at(
        directory: int, name: str, *, expected_uid: int,
        expected_gid: int) -> bytes:
    try:
        before = os.stat(name, dir_fd=directory, follow_symlinks=False)
        _validate_private_metadata(
            before, expected_uid=expected_uid, expected_gid=expected_gid)
        descriptor = os.open(
            name, os.O_RDONLY | os.O_CLOEXEC | os.O_NONBLOCK |
            getattr(os, "O_NOFOLLOW", 0), dir_fd=directory)
    except OSError as error:
        raise PolicyError(f"boundary file {name} cannot be opened") from error
    try:
        opened = os.fstat(descriptor)
        _validate_private_metadata(
            opened, expected_uid=expected_uid, expected_gid=expected_gid)
        payload = bytearray()
        while len(payload) <= BOUNDARY_MAXIMUM_BYTES:
            chunk = os.read(
                descriptor,
                min(65536, BOUNDARY_MAXIMUM_BYTES + 1 - len(payload)))
            if not chunk:
                break
            payload.extend(chunk)
        after = os.fstat(descriptor)
    finally:
        os.close(descriptor)
    fields = (
        "st_dev", "st_ino", "st_mode", "st_nlink", "st_uid", "st_gid",
        "st_size", "st_mtime_ns", "st_ctime_ns")
    if (
            len(payload) > BOUNDARY_MAXIMUM_BYTES or
            any(getattr(before, field) != getattr(opened, field) or
                getattr(opened, field) != getattr(after, field)
                for field in fields)):
        raise PolicyError(f"boundary file {name} changed while reading")
    return bytes(payload)


def _atomic_replace_private(
        directory: int, name: str, payload: bytes, *, expected_uid: int,
        expected_gid: int) -> None:
    if not 2 <= len(payload) <= BOUNDARY_MAXIMUM_BYTES:
        raise PolicyError("boundary publication payload is outside bounds")
    try:
        existing = os.stat(name, dir_fd=directory, follow_symlinks=False)
    except FileNotFoundError:
        pass
    except OSError as error:
        raise PolicyError("boundary publication target cannot be inspected") from error
    else:
        _validate_private_metadata(
            existing, expected_uid=expected_uid, expected_gid=expected_gid)
    temporary = f".{name}.{os.getpid()}.{time.time_ns()}"
    descriptor = -1
    try:
        descriptor = os.open(
            temporary,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_CLOEXEC |
            getattr(os, "O_NOFOLLOW", 0), 0o600, dir_fd=directory)
        os.fchmod(descriptor, 0o600)
        offset = 0
        while offset < len(payload):
            count = os.write(descriptor, payload[offset:])
            if count <= 0:
                raise PolicyError("boundary publication write was incomplete")
            offset += count
        os.fsync(descriptor)
        _validate_private_metadata(
            os.fstat(descriptor), expected_uid=expected_uid,
            expected_gid=expected_gid)
        os.close(descriptor)
        descriptor = -1
        os.replace(temporary, name, src_dir_fd=directory, dst_dir_fd=directory)
        os.fsync(directory)
        committed = _read_private_at(
            directory, name, expected_uid=expected_uid,
            expected_gid=expected_gid)
        if committed != payload:
            raise PolicyError("boundary publication verification failed")
    except OSError as error:
        raise PolicyError("boundary publication failed") from error
    finally:
        if descriptor >= 0:
            os.close(descriptor)
        try:
            os.unlink(temporary, dir_fd=directory)
        except FileNotFoundError:
            pass


def _fingerprint_document(value: SourceFingerprint) -> dict[str, Any]:
    return {
        "path": str(value.path), "present": value.present,
        "device": value.device, "inode": value.inode, "mode": value.mode,
        "links": value.links, "uid": value.uid, "gid": value.gid,
        "size": value.size, "mtime_ns": value.mtime_ns,
        "ctime_ns": value.ctime_ns,
        "sha256": ("sha256:" + value.sha256) if value.sha256 else None,
        "installed_mode": value.installed_mode,
    }


def _boundary_state_document(
        policy: BrokerNetworkPolicy,
        fingerprints: tuple[SourceFingerprint, ...]) -> dict[str, Any]:
    state = "DENY_ALL" if not policy.authorized_connectors else "ACTIVE"
    semantics = {
        "state": state, "family": policy.family, "table": policy.table,
        "chain": policy.chain, "guard_chain": GUARD_CHAIN,
        "protected_tcp_destination_ports": list(policy.ports),
        "authorized_connectors": [
            {
                "domain_id": item.domain_id, "identity": item.identity,
                "uid": item.uid, "gid": item.gid, "role": item.role,
            } for item in policy.authorized_connectors],
        "source_policy_sha256": "sha256:" + policy.source_sha256,
        "identity_manifest_sha256":
            "sha256:" + policy.identity_manifest_sha256,
        "effective_policy_sha256": "sha256:" + policy.effective_sha256,
        "source_fingerprints": [
            _fingerprint_document(item) for item in fingerprints],
    }
    semantics["table_semantic_sha256"] = _sha256(_canonical_json(semantics))
    state_body = {
        "state": state,
        "effective_policy_sha256": semantics["effective_policy_sha256"],
        "table_semantic_sha256": semantics["table_semantic_sha256"],
        "source_fingerprints": semantics["source_fingerprints"],
    }
    semantics["state_sha256"] = _sha256(_canonical_json(state_body))
    return semantics


BOUNDARY_LEDGER_FIELDS = {
    "schema", "version", "boot_id", "generation", "state_sha256",
    "publisher_pid", "publisher_start_ticks",
}
BOUNDARY_RECEIPT_FIELDS = {
    "schema", "version", "status", "boot_id", "generation",
    "publisher_pid", "publisher_start_ticks", "observed_at_ms",
    "observed_monotonic_ns", "state", "family", "table", "chain",
    "guard_chain", "protected_tcp_destination_ports",
    "protected_port_count", "authorized_connector_count",
    "authorized_uids", "authorized_connectors", "paper_authorized",
    "live_authorized", "source_policy_sha256",
    "identity_manifest_sha256", "effective_policy_sha256",
    "table_semantic_sha256", "state_sha256", "source_fingerprints",
}


class BoundaryReceiptPublisher:
    def __init__(
            self, runtime_directory: Path = BOUNDARY_RUNTIME_DIRECTORY, *,
            expected_uid: int = 0, expected_gid: int = 0,
            boot_id_path: Path = BOOT_ID_PATH,
            proc_root: Path = Path("/proc"),
            require_installed_metadata: bool = True) -> None:
        self.runtime_directory = runtime_directory
        self.expected_uid = expected_uid
        self.expected_gid = expected_gid
        self.boot_id_path = boot_id_path
        self.proc_root = proc_root
        self.require_installed_metadata = require_installed_metadata

    def publish(
            self, policy: BrokerNetworkPolicy,
            fingerprints: tuple[SourceFingerprint, ...]) -> BoundaryReceipt:
        if len(fingerprints) != 3 or any(
                not source_fingerprint_matches(
                    item,
                    require_installed_metadata=self.require_installed_metadata)
                for item in fingerprints):
            raise PolicyError("boundary receipt source fingerprints drifted")
        directory = _open_runtime_directory(
            self.runtime_directory, expected_uid=self.expected_uid,
            expected_gid=self.expected_gid)
        lock = -1
        try:
            lock = _open_generation_lock(
                directory, expected_uid=self.expected_uid,
                expected_gid=self.expected_gid)
            fcntl.flock(lock, fcntl.LOCK_EX)
            boot_id = _read_boot_id(self.boot_id_path)
            pid = os.getpid()
            start_ticks = _process_start_ticks(pid, self.proc_root)
            state = _boundary_state_document(policy, fingerprints)
            generation = 1
            try:
                ledger_raw = _read_private_at(
                    directory, BOUNDARY_LEDGER_NAME,
                    expected_uid=self.expected_uid,
                    expected_gid=self.expected_gid)
            except PolicyError as error:
                try:
                    os.stat(
                        BOUNDARY_LEDGER_NAME, dir_fd=directory,
                        follow_symlinks=False)
                except FileNotFoundError:
                    pass
                else:
                    raise error
            else:
                ledger = _validate_sealed_json(
                    ledger_raw, schema=BOUNDARY_LEDGER_SCHEMA,
                    expected_fields=BOUNDARY_LEDGER_FIELDS,
                    label="boundary generation ledger")
                old_generation = ledger.get("generation")
                if (
                        not isinstance(old_generation, int) or
                        isinstance(old_generation, bool) or
                        old_generation <= 0 or old_generation >= (1 << 63)):
                    raise PolicyError("boundary generation ledger is invalid")
                if ledger.get("boot_id") == boot_id:
                    changed = (
                        ledger.get("state_sha256") != state["state_sha256"] or
                        ledger.get("publisher_pid") != pid or
                        ledger.get("publisher_start_ticks") != start_ticks)
                    generation = old_generation + (1 if changed else 0)
                else:
                    generation = 1
            ledger_payload = _sealed_json({
                "schema": BOUNDARY_LEDGER_SCHEMA, "version": 1,
                "boot_id": boot_id, "generation": generation,
                "state_sha256": state["state_sha256"],
                "publisher_pid": pid, "publisher_start_ticks": start_ticks,
            })
            _atomic_replace_private(
                directory, BOUNDARY_LEDGER_NAME, ledger_payload,
                expected_uid=self.expected_uid, expected_gid=self.expected_gid)
            now_ms = time.time_ns() // 1_000_000
            now_monotonic = time.monotonic_ns()
            document = {
                "schema": BOUNDARY_RECEIPT_SCHEMA, "version": 1,
                "status": "EXACT_" + state["state"], "boot_id": boot_id,
                "generation": generation, "publisher_pid": pid,
                "publisher_start_ticks": start_ticks,
                "observed_at_ms": now_ms,
                "observed_monotonic_ns": now_monotonic,
                **state,
                "protected_port_count": len(policy.ports),
                "authorized_connector_count":
                    len(policy.authorized_connectors),
                "authorized_uids": list(policy.authorized_uids),
                "paper_authorized": bool(policy.authorized_connectors),
                "live_authorized": False,
            }
            payload = _sealed_json(document)
            _atomic_replace_private(
                directory, BOUNDARY_RECEIPT_NAME, payload,
                expected_uid=self.expected_uid, expected_gid=self.expected_gid)
            return BoundaryReceipt(document | {
                "body_sha256": _sha256(_canonical_json(document))}, payload,
                _sha256(payload))
        finally:
            if lock >= 0:
                try:
                    fcntl.flock(lock, fcntl.LOCK_UN)
                finally:
                    os.close(lock)
            os.close(directory)


def _stable_activation_file(
        path: Path, *, modes: set[int], expected_uid: int = 0,
        expected_gid: int = 0) -> bytes:
    try:
        before = os.lstat(path)
        descriptor = os.open(
            path, os.O_RDONLY | os.O_CLOEXEC | os.O_NONBLOCK |
            getattr(os, "O_NOFOLLOW", 0))
    except OSError as error:
        raise PolicyError("broker activation artifact is unavailable") from error
    try:
        opened = os.fstat(descriptor)
        if (
                not stat.S_ISREG(opened.st_mode) or opened.st_nlink != 1 or
                opened.st_uid != expected_uid or opened.st_gid != expected_gid or
                stat.S_IMODE(opened.st_mode) not in modes or
                not 2 <= opened.st_size <= BOUNDARY_MAXIMUM_BYTES):
            raise PolicyError("broker activation artifact metadata is unsafe")
        payload = bytearray()
        while len(payload) <= BOUNDARY_MAXIMUM_BYTES:
            chunk = os.read(
                descriptor,
                min(65536, BOUNDARY_MAXIMUM_BYTES + 1 - len(payload)))
            if not chunk:
                break
            payload.extend(chunk)
        after = os.fstat(descriptor)
        named = os.lstat(path)
        fields = (
            "st_dev", "st_ino", "st_mode", "st_nlink", "st_uid", "st_gid",
            "st_size", "st_mtime_ns", "st_ctime_ns")
        if (
                len(payload) > BOUNDARY_MAXIMUM_BYTES or
                any(getattr(before, field) != getattr(opened, field) or
                    getattr(opened, field) != getattr(after, field) or
                    getattr(after, field) != getattr(named, field)
                    for field in fields)):
            raise PolicyError("broker activation artifact changed while reading")
        return bytes(payload)
    finally:
        os.close(descriptor)


def _hash_open_file(
        path: Path, *, expected_uid: int = 0, expected_gid: int = 0) -> str:
    return _sha256(_stable_activation_file(
        path, modes={0o400, 0o440, 0o600, 0o644, 0o755},
        expected_uid=expected_uid, expected_gid=expected_gid))


def _guardian_identity_is_current(
        reservation: dict[str, Any], *, proc_root: Path = Path("/proc")) -> bool:
    pid = reservation.get("guardian_pid")
    if not isinstance(pid, int) or isinstance(pid, bool) or pid <= 0:
        return False
    try:
        if _process_start_ticks(pid, proc_root) != reservation.get(
                "guardian_start_ticks"):
            return False
        executable = proc_root / str(pid) / "exe"
        descriptor = os.open(executable, os.O_RDONLY | os.O_CLOEXEC)
        try:
            digest = hashlib.sha256()
            total = 0
            while total <= 256 * 1024 * 1024:
                chunk = os.read(
                    descriptor,
                    min(1024 * 1024, 256 * 1024 * 1024 + 1 - total))
                if not chunk:
                    break
                total += len(chunk)
                digest.update(chunk)
            if total > 256 * 1024 * 1024:
                return False
        finally:
            os.close(descriptor)
        argv = _read_proc_value(proc_root / str(pid) / "cmdline", 64 * 1024)
        cgroup = _read_proc_value(proc_root / str(pid) / "cgroup", 64 * 1024)
        values = argv.split(b"\0")
        if values and values[-1] == b"":
            values.pop()
        decoded = [value.decode("utf-8", errors="strict") for value in values]
        script = Path(decoded[3]) if len(decoded) == 7 else Path("/")
        return bool(
            reservation.get("guardian_exe_sha256") ==
                "sha256:" + digest.hexdigest() and
            reservation.get("guardian_argv_sha256") == _sha256(argv) and
            argv.endswith(b"\0") and
            decoded[:3] == ["/usr/bin/python3.12", "-I", "-S"] and
            script.name == LOCAL_CONTROL_CREDENTIAL_NAME and
            "credentials" in script.parts and
            decoded[4:] == ["guardian", "--domain", "alpha"] and
            b"hepta-local-paper-authority@alpha.service" in cgroup)
    except (OSError, PolicyError, UnicodeError, ValueError, IndexError):
        return False


def _validate_activation_reservation(
        reservation_raw: bytes, permit_raw: bytes, *, boot_id_path: Path,
        paper_identity_path: Path, drop_in_path: Path,
        control_image_path: Path, proc_root: Path,
        now_ms: Optional[int] = None, expected_uid: int = 0,
        expected_gid: int = 0,
        allow_consumed_replay: bool = False) \
        -> tuple[dict[str, Any], dict[str, Any]]:
    reservation = _validate_sealed_json(
        reservation_raw, schema=ACTIVATION_RESERVATION_SCHEMA,
        expected_fields=ACTIVATION_RESERVATION_FIELDS,
        label="broker activation reservation")
    permit = _validate_sealed_json(
        permit_raw, schema=BROKER_START_PERMIT_SCHEMA,
        expected_fields=ACTIVATION_PERMIT_FIELDS,
        label="broker activation permit")
    current_ms = time.time_ns() // 1_000_000 if now_ms is None else now_ms
    copied = (
        "issued_at_ms", "expires_at_ms", "boot_id", "guardian_pid",
        "guardian_start_ticks", "guardian_exe_sha256",
        "guardian_argv_sha256", "control_image_sha256",
        "guardian_request_id", "domain", "transaction_id", "operation",
        "phase", "request_sha256", "target_identity_manifest_sha256",
        "target_drop_in_sha256")
    activation_id = reservation.get("activation_id")
    operation = reservation.get("operation")
    phase = reservation.get("phase")
    suffix = (
        "START_BROKER_RECOVERY" if operation == "ENABLE_RECOVERY" else
        "START_BROKER_LOCAL_PAPER")
    if (
            not isinstance(current_ms, int) or isinstance(current_ms, bool) or
            not isinstance(activation_id, str) or
            re.fullmatch(r"[0-9a-f]{32}", activation_id) is None or
            reservation.get("status") != "PENDING_BROKER_ACTIVE" or
            any(reservation.get(field) != permit.get(field) for field in copied) or
            not isinstance(reservation.get("issued_at_ms"), int) or
            not isinstance(reservation.get("expires_at_ms"), int) or
            reservation["expires_at_ms"] - reservation["issued_at_ms"] !=
                ACTIVATION_TTL_MS or
            (not allow_consumed_replay and not (
                reservation["issued_at_ms"] <= current_ms <
                    reservation["expires_at_ms"])) or
            reservation.get("boot_id") != _read_boot_id(boot_id_path) or
            operation not in {"ENABLE", "ENABLE_RECOVERY"} or
            not isinstance(phase, str) or not phase.endswith(suffix) or
            reservation.get("domain") != "alpha" or
            reservation.get("required_pre_activation_boundary") !=
                "DENY_ALL" or
            reservation.get("paper_only") is not True or
            reservation.get("live_authorized") is not False or
            reservation.get("broker_start_permit_file_sha256") !=
                _sha256(permit_raw) or
            reservation.get("broker_start_permit_body_sha256") !=
                permit.get("body_sha256") or
            reservation.get("target_identity_manifest_sha256") !=
                _hash_open_file(
                    paper_identity_path, expected_uid=expected_uid,
                    expected_gid=expected_gid) or
            reservation.get("target_drop_in_sha256") !=
                _hash_open_file(
                    drop_in_path, expected_uid=expected_uid,
                    expected_gid=expected_gid) or
            reservation.get("control_image_sha256") !=
                _hash_open_file(
                    control_image_path, expected_uid=expected_uid,
                    expected_gid=expected_gid) or
            (not allow_consumed_replay and not _guardian_identity_is_current(
                reservation, proc_root=proc_root))):
        raise PolicyError("broker activation reservation is invalid")
    return reservation, permit


def _reconstruct_activation_permit(
        reservation: dict[str, Any]) -> bytes:
    body: dict[str, Any] = {
        "schema": BROKER_START_PERMIT_SCHEMA, "version": 1,
    }
    for field in ACTIVATION_PERMIT_FIELDS - {"schema", "version"}:
        if field not in reservation:
            raise PolicyError("broker activation permit cannot be reconstructed")
        body[field] = reservation[field]
    payload = _sealed_json(body)
    document = _validate_sealed_json(
        payload, schema=BROKER_START_PERMIT_SCHEMA,
        expected_fields=ACTIVATION_PERMIT_FIELDS,
        label="broker activation reconstructed permit")
    if (
            document.get("body_sha256") !=
                reservation.get("broker_start_permit_body_sha256") or
            _sha256(payload) !=
                reservation.get("broker_start_permit_file_sha256")):
        raise PolicyError("broker activation reconstructed permit conflicts")
    return payload


def _publish_activation_consumed(
        directory: int, name: str, payload: bytes, *, expected_uid: int,
        expected_gid: int) -> None:
    if not 2 <= len(payload) <= BOUNDARY_MAXIMUM_BYTES:
        raise PolicyError("broker activation tombstone is outside bounds")
    try:
        existing = _read_private_at(
            directory, name, expected_uid=expected_uid,
            expected_gid=expected_gid)
    except PolicyError:
        try:
            os.stat(name, dir_fd=directory, follow_symlinks=False)
        except FileNotFoundError:
            existing = None
        else:
            raise
    if existing is not None:
        if existing != payload:
            raise PolicyError("broker activation tombstone conflicts")
        return
    descriptor = -1
    try:
        descriptor = os.open(
            name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_CLOEXEC |
            getattr(os, "O_NOFOLLOW", 0), 0o600, dir_fd=directory)
        os.fchmod(descriptor, 0o600)
        os.fchown(descriptor, expected_uid, expected_gid)
        offset = 0
        while offset < len(payload):
            written = os.write(descriptor, payload[offset:])
            if written <= 0:
                raise PolicyError("broker activation tombstone write failed")
            offset += written
        os.fsync(descriptor)
        os.fsync(directory)
    except OSError as error:
        raise PolicyError("broker activation tombstone publication failed") from error
    finally:
        if descriptor >= 0:
            os.close(descriptor)
    if _read_private_at(
            directory, name, expected_uid=expected_uid,
            expected_gid=expected_gid) != payload:
        raise PolicyError("broker activation tombstone verification failed")


def _optional_private_at(
        directory: int, name: str, *, expected_uid: int,
        expected_gid: int) -> Optional[bytes]:
    try:
        return _read_private_at(
            directory, name, expected_uid=expected_uid,
            expected_gid=expected_gid)
    except PolicyError:
        try:
            os.stat(name, dir_fd=directory, follow_symlinks=False)
        except FileNotFoundError:
            return None
        raise


def _unlink_exact_private_path(
        path: Path, expected: bytes, *, expected_uid: int = 0,
        expected_gid: int = 0) -> None:
    current = _stable_activation_file(
        path, modes={0o600}, expected_uid=expected_uid,
        expected_gid=expected_gid)
    if current != expected:
        raise PolicyError("broker activation artifact changed before removal")
    try:
        parent = os.open(
            path.parent, os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC |
            getattr(os, "O_NOFOLLOW", 0))
        try:
            os.unlink(path.name, dir_fd=parent)
            os.fsync(parent)
        finally:
            os.close(parent)
    except OSError as error:
        raise PolicyError("broker activation artifact removal failed") from error


def apply_authorizing_policy_guarded(
        policy: BrokerNetworkPolicy, fingerprints: tuple[SourceFingerprint, ...],
        runner: Runner, publisher: BoundaryReceiptPublisher, *,
        host_authority_directory: Path = HOST_AUTHORITY_DIRECTORY,
        expected_uid: int = 0, expected_gid: int = 0,
        require_activation_reservation: Optional[bool] = None,
        pre_activation_policy: Optional[BrokerNetworkPolicy] = None,
        permit_path: Path = BROKER_START_PERMIT_PATH,
        paper_identity_path: Path = DEFAULT_PAPER_IDENTITIES,
        drop_in_path: Path = BROKER_DROP_IN_PATH,
        control_image_path: Optional[Path] = None,
        boot_id_path: Path = BOOT_ID_PATH,
        proc_root: Path = Path("/proc"),
        now_ms: Optional[int] = None) -> BoundaryReceipt:
    """Commit an ACTIVE kernel policy under the host authority CAS lock.

    Authorization writers may stage inputs before this call, but the kernel
    boundary cannot become ACTIVE while any terminal/admission owner holds the
    shared host lease.  The exact ACTIVE receipt is published before the lock
    is released, closing the verifier's final-boundary/ACK race at the actual
    authority boundary.
    """
    if not policy.authorized_connectors:
        raise PolicyError("authorizing policy guard requires an active policy")
    if require_activation_reservation is None:
        require_activation_reservation = any(
            item.domain_id != "default" for item in policy.authorized_connectors)
    try:
        directory = os.open(
            host_authority_directory,
            os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC |
            getattr(os, "O_NOFOLLOW", 0))
    except OSError as error:
        raise PolicyError("host authority directory is unavailable") from error
    lease = -1
    try:
        metadata = os.fstat(directory)
        if (
                not stat.S_ISDIR(metadata.st_mode) or
                metadata.st_uid != expected_uid or
                metadata.st_gid != expected_gid or
                stat.S_IMODE(metadata.st_mode) != 0o700):
            raise PolicyError("host authority directory is unsafe")
        lease = os.open(
            HOST_AUTHORITY_LEASE_NAME,
            os.O_RDONLY | os.O_CLOEXEC | getattr(os, "O_NOFOLLOW", 0),
            dir_fd=directory)
        lease_metadata = os.fstat(lease)
        if (
                not stat.S_ISREG(lease_metadata.st_mode) or
                lease_metadata.st_nlink != 1 or lease_metadata.st_size != 0 or
                lease_metadata.st_uid != expected_uid or
                lease_metadata.st_gid != expected_gid or
                stat.S_IMODE(lease_metadata.st_mode) != 0o600):
            raise PolicyError("host authority lease is unsafe")
        try:
            fcntl.flock(lease, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as error:
            raise PolicyError("host authority lease is busy") from error

        def require_owner_absent() -> None:
            try:
                os.stat(
                    HOST_AUTHORITY_OWNER_NAME, dir_fd=directory,
                    follow_symlinks=False)
            except FileNotFoundError:
                return
            except OSError as error:
                raise PolicyError("host authority owner is unsafe") from error
            raise PolicyError("host authority owner blocks broker activation")

        if not require_activation_reservation:
            require_owner_absent()
            apply_policy(policy, runner)
            require_owner_absent()
            receipt = publisher.publish(policy, fingerprints)
            require_owner_absent()
            return receipt

        if pre_activation_policy is None or control_image_path is None:
            if control_image_path is None:
                credentials = os.environ.get("CREDENTIALS_DIRECTORY", "")
                if not credentials or not Path(credentials).is_absolute():
                    raise PolicyError(
                        "broker activation control credential is unavailable")
                control_image_path = (
                    Path(credentials) / LOCAL_CONTROL_CREDENTIAL_NAME)
            if pre_activation_policy is None:
                raise PolicyError(
                    "broker activation predecessor policy is unavailable")
        reservation_raw = _read_private_at(
            directory, HOST_AUTHORITY_OWNER_NAME,
            expected_uid=expected_uid, expected_gid=expected_gid)
        reservation_preview = _validate_sealed_json(
            reservation_raw, schema=ACTIVATION_RESERVATION_SCHEMA,
            expected_fields=ACTIVATION_RESERVATION_FIELDS,
            label="broker activation reservation")
        preview_id = reservation_preview.get("activation_id")
        if not isinstance(preview_id, str) or re.fullmatch(
                r"[0-9a-f]{32}", preview_id) is None:
            raise PolicyError("broker activation reservation identity is invalid")
        intent_name = (
            ACTIVATION_INTENT_NAME_PREFIX + preview_id +
            ACTIVATION_CONSUMED_NAME_SUFFIX)
        tombstone_name = (
            ACTIVATION_CONSUMED_NAME_PREFIX + preview_id +
            ACTIVATION_CONSUMED_NAME_SUFFIX)
        consumed_existing_raw = _optional_private_at(
            directory, tombstone_name, expected_uid=expected_uid,
            expected_gid=expected_gid)
        consumed_preview: Optional[dict[str, Any]] = None
        if consumed_existing_raw is not None:
            consumed_preview = _validate_sealed_json(
                consumed_existing_raw, schema=ACTIVATION_CONSUMED_SCHEMA,
                expected_fields=ACTIVATION_CONSUMED_FIELDS,
                label="broker activation tombstone")
            if (
                    consumed_preview.get("status") !=
                        "ACTIVE_BOUNDARY_COMMITTED" or
                    consumed_preview.get("activation_id") != preview_id or
                    consumed_preview.get("boot_id") !=
                        reservation_preview.get("boot_id") or
                    consumed_preview.get("reservation_file_sha256") !=
                        _sha256(reservation_raw) or
                    consumed_preview.get("reservation_body_sha256") !=
                        reservation_preview.get("body_sha256") or
                    consumed_preview.get(
                        "broker_start_permit_file_sha256") !=
                        reservation_preview.get(
                            "broker_start_permit_file_sha256") or
                    consumed_preview.get(
                        "broker_start_permit_body_sha256") !=
                        reservation_preview.get(
                            "broker_start_permit_body_sha256")):
                raise PolicyError(
                    "broker activation tombstone does not bind reservation")
        permit_present = True
        try:
            permit_raw = _stable_activation_file(
                permit_path, modes={0o600}, expected_uid=expected_uid,
                expected_gid=expected_gid)
        except PolicyError:
            try:
                os.lstat(permit_path)
            except FileNotFoundError:
                if consumed_existing_raw is None:
                    raise PolicyError("broker activation permit is unavailable")
                permit_present = False
                permit_raw = _reconstruct_activation_permit(
                    reservation_preview)
            else:
                raise
        try:
            reservation, permit = _validate_activation_reservation(
                reservation_raw, permit_raw, boot_id_path=boot_id_path,
                paper_identity_path=paper_identity_path,
                drop_in_path=drop_in_path, control_image_path=control_image_path,
                proc_root=proc_root, now_ms=now_ms, expected_uid=expected_uid,
                expected_gid=expected_gid,
                allow_consumed_replay=consumed_preview is not None)
        except PolicyError:
            # Invalid/expired PENDING authority must never strand an already
            # committed kernel allowlist.  Tighten exact target/ambiguous
            # states before propagating the fail-closed validation error.
            if consumed_preview is None:
                try:
                    raw = _read_active_policy_json(policy, runner)
                    try:
                        verify_active_policy_json(pre_activation_policy, raw)
                    except PolicyError:
                        apply_policy(pre_activation_policy, runner)
                except PolicyError:
                    try:
                        apply_policy(pre_activation_policy, runner)
                    except PolicyError:
                        pass
            raise

        activation_id = reservation["activation_id"]
        if activation_id != preview_id:
            raise PolicyError("broker activation reservation identity drifted")
        intent_raw = _optional_private_at(
            directory, intent_name, expected_uid=expected_uid,
            expected_gid=expected_gid)
        intent: Optional[dict[str, Any]] = None
        consumed_existing: Optional[dict[str, Any]] = None
        if intent_raw is not None:
            intent = _validate_sealed_json(
                intent_raw, schema=ACTIVATION_INTENT_SCHEMA,
                expected_fields=ACTIVATION_INTENT_FIELDS,
                label="broker activation intent")
        if consumed_existing_raw is not None:
            assert consumed_preview is not None
            consumed_existing = consumed_preview
        if intent is not None and intent.get("status") != (
                "DENY_ALL_PREDECESSOR_FROZEN"):
            raise PolicyError("broker activation intent status is invalid")
        if consumed_existing is not None and consumed_existing.get(
                "status") != "ACTIVE_BOUNDARY_COMMITTED":
            raise PolicyError("broker activation tombstone status is invalid")

        common_evidence = {
            "activation_id": activation_id,
            "boot_id": reservation["boot_id"],
            "reservation_file_sha256": _sha256(reservation_raw),
            "reservation_body_sha256": reservation["body_sha256"],
            "broker_start_permit_file_sha256": _sha256(permit_raw),
            "broker_start_permit_body_sha256": permit["body_sha256"],
            "target_identity_manifest_sha256":
                reservation["target_identity_manifest_sha256"],
            "target_drop_in_sha256": reservation["target_drop_in_sha256"],
            "required_pre_activation_boundary": "DENY_ALL",
            "paper_authorized": True, "live_authorized": False,
        }
        for existing, label in (
                (intent, "broker activation intent"),
                (consumed_existing, "broker activation tombstone")):
            if existing is not None and any(
                    existing.get(field) != value
                    for field, value in common_evidence.items()):
                raise PolicyError(label + " does not bind the reservation")

        # Read once, then accept only exact DENY_ALL or the exact target.  A
        # prior process may have crashed immediately after the kernel commit.
        # In that case a durable intent/tombstone is mandatory before the
        # already-active target can be resumed.  Any third state is tightened
        # to DENY_ALL and this activation attempt fails closed.
        live = _read_active_policy_json(policy, runner)
        live_is_deny = False
        live_is_target = False
        try:
            verify_active_policy_json(pre_activation_policy, live)
            live_is_deny = True
        except PolicyError:
            try:
                verify_active_policy_json(policy, live)
                live_is_target = True
            except PolicyError:
                try:
                    apply_policy(pre_activation_policy, runner)
                finally:
                    raise PolicyError(
                        "broker activation live predecessor is ambiguous")
        if live_is_target and intent is None and consumed_existing is None:
            try:
                apply_policy(pre_activation_policy, runner)
            finally:
                raise PolicyError(
                    "broker activation target lacks durable predecessor intent")

        if live_is_deny:
            if consumed_existing is not None:
                # The one-shot permit was already consumed.  A committed
                # replay may monitor an exact target and republish its current
                # receipt, but can never use reconstructed/expired authority
                # to transition DENY_ALL back to ACTIVE.
                raise PolicyError(
                    "broker activation consumed replay cannot reauthorize")
            predecessor = publisher.publish(
                pre_activation_policy, fingerprints)
            if (
                    predecessor.document.get("state") != "DENY_ALL" or
                    predecessor.document.get("status") != "EXACT_DENY_ALL" or
                    predecessor.document.get("paper_authorized") is not False or
                    predecessor.document.get("live_authorized") is not False):
                raise PolicyError("broker activation predecessor is not deny-all")
            predecessor_sha = predecessor.document["state_sha256"]
            if intent is not None and intent.get(
                    "pre_activation_boundary_state_sha256") != predecessor_sha:
                raise PolicyError("broker activation predecessor intent drifted")
            if consumed_existing is not None and consumed_existing.get(
                    "pre_activation_boundary_state_sha256") != predecessor_sha:
                raise PolicyError("broker activation tombstone predecessor drifted")
            if intent is None and consumed_existing is None:
                intent_raw = _sealed_json({
                    "schema": ACTIVATION_INTENT_SCHEMA, "version": 1,
                    "status": "DENY_ALL_PREDECESSOR_FROZEN",
                    "recorded_at_ms": (
                        time.time_ns() // 1_000_000
                        if now_ms is None else now_ms),
                    **common_evidence,
                    "pre_activation_boundary_state_sha256": predecessor_sha,
                })
                _publish_activation_consumed(
                    directory, intent_name, intent_raw,
                    expected_uid=expected_uid, expected_gid=expected_gid)
                intent = _validate_sealed_json(
                    intent_raw, schema=ACTIVATION_INTENT_SCHEMA,
                    expected_fields=ACTIVATION_INTENT_FIELDS,
                    label="broker activation intent")
            apply_policy(policy, runner)
        else:
            source = intent if intent is not None else consumed_existing
            assert source is not None
            predecessor_sha = str(
                source["pre_activation_boundary_state_sha256"])
        if _read_private_at(
                directory, HOST_AUTHORITY_OWNER_NAME,
                expected_uid=expected_uid,
                expected_gid=expected_gid) != reservation_raw:
            raise PolicyError("broker activation reservation drifted")
        if permit_present and _stable_activation_file(
                permit_path, modes={0o600}, expected_uid=expected_uid,
                expected_gid=expected_gid) != permit_raw:
            raise PolicyError("broker activation permit drifted")

        if _read_private_at(
                directory, HOST_AUTHORITY_OWNER_NAME,
                expected_uid=expected_uid,
                expected_gid=expected_gid) != reservation_raw:
            raise PolicyError("broker activation reservation drifted")
        receipt = publisher.publish(policy, fingerprints)
        if (
                receipt.document.get("state") != "ACTIVE" or
                receipt.document.get("status") != "EXACT_ACTIVE" or
                receipt.document.get("paper_authorized") is not True or
                receipt.document.get("live_authorized") is not False):
            raise PolicyError("broker activation receipt is not active")

        consumed_at_ms = (
            consumed_existing["consumed_at_ms"]
            if consumed_existing is not None else
            (time.time_ns() // 1_000_000 if now_ms is None else now_ms))
        consumed = _sealed_json({
            "schema": ACTIVATION_CONSUMED_SCHEMA, "version": 1,
            "status": "ACTIVE_BOUNDARY_COMMITTED",
            "consumed_at_ms": consumed_at_ms,
            **common_evidence,
            "guardian_request_id": reservation["guardian_request_id"],
            "transaction_id": reservation["transaction_id"],
            "operation": reservation["operation"],
            "phase": reservation["phase"],
            "request_sha256": reservation["request_sha256"],
            "domain": reservation["domain"],
            "target_identity_manifest_sha256":
                reservation["target_identity_manifest_sha256"],
            "target_drop_in_sha256": reservation["target_drop_in_sha256"],
            "control_image_sha256": reservation["control_image_sha256"],
            "pre_activation_boundary_state_sha256":
                predecessor_sha,
            "active_boundary_status": "EXACT_ACTIVE",
            "active_boundary_state_sha256": receipt.document["state_sha256"],
        })
        document = _validate_sealed_json(
            consumed, schema=ACTIVATION_CONSUMED_SCHEMA,
            expected_fields=ACTIVATION_CONSUMED_FIELDS,
            label="broker activation tombstone")
        if document.get("body_sha256") is None:
            raise PolicyError("broker activation tombstone is invalid")
        if consumed_existing_raw is not None and consumed != consumed_existing_raw:
            raise PolicyError("broker activation tombstone replay drifted")
        _publish_activation_consumed(
            directory, tombstone_name, consumed,
            expected_uid=expected_uid, expected_gid=expected_gid)

        # Handoff is one-way: the active receipt and tombstone are durable
        # before either bearer is removed.  Local control consumes the
        # tombstone only after it reacquires this same lease.
        if permit_present:
            _unlink_exact_private_path(
                permit_path, permit_raw, expected_uid=expected_uid,
                expected_gid=expected_gid)
        if intent_raw is not None:
            current_intent = _read_private_at(
                directory, intent_name, expected_uid=expected_uid,
                expected_gid=expected_gid)
            if current_intent != intent_raw:
                raise PolicyError("broker activation intent drifted")
            os.unlink(intent_name, dir_fd=directory)
            os.fsync(directory)
        # Preserve the reservation owner.  Local control validates the
        # consumed handoff without removing either artifact; the Execution
        # preflight then atomically replaces this exact owner with its runtime
        # owner and removes the consumed tombstone under the same host lock.
        # At no point may ACTIVE exist with an owner-absent window.
        if _read_private_at(
                directory, HOST_AUTHORITY_OWNER_NAME,
                expected_uid=expected_uid,
                expected_gid=expected_gid) != reservation_raw:
            raise PolicyError("broker activation reservation drifted")
        return receipt
    finally:
        if lease >= 0:
            try:
                fcntl.flock(lease, fcntl.LOCK_UN)
            finally:
                os.close(lease)
        os.close(directory)


def load_current_boundary_receipt(
        runtime_directory: Path = BOUNDARY_RUNTIME_DIRECTORY, *,
        expected_uid: int = 0, expected_gid: int = 0,
        boot_id_path: Path = BOOT_ID_PATH,
        now_ms: Optional[int] = None,
        now_monotonic_ns: Optional[int] = None,
        require_installed_metadata: bool = True,
        proc_root: Path = Path("/proc")) -> BoundaryReceipt:
    directory = _open_runtime_directory(
        runtime_directory, expected_uid=expected_uid, expected_gid=expected_gid)
    lock = -1
    try:
        lock = _open_generation_lock(
            directory, expected_uid=expected_uid, expected_gid=expected_gid)
        fcntl.flock(lock, fcntl.LOCK_SH)
        ledger_raw = _read_private_at(
            directory, BOUNDARY_LEDGER_NAME, expected_uid=expected_uid,
            expected_gid=expected_gid)
        receipt_raw = _read_private_at(
            directory, BOUNDARY_RECEIPT_NAME, expected_uid=expected_uid,
            expected_gid=expected_gid)
        ledger = _validate_sealed_json(
            ledger_raw, schema=BOUNDARY_LEDGER_SCHEMA,
            expected_fields=BOUNDARY_LEDGER_FIELDS,
            label="boundary generation ledger")
        receipt = _validate_sealed_json(
            receipt_raw, schema=BOUNDARY_RECEIPT_SCHEMA,
            expected_fields=BOUNDARY_RECEIPT_FIELDS,
            label="current boundary receipt")
        current_boot = _read_boot_id(boot_id_path)
        current_ms = time.time_ns() // 1_000_000 if now_ms is None else now_ms
        current_monotonic = time.monotonic_ns() \
            if now_monotonic_ns is None else now_monotonic_ns
        for value, label in (
                (current_ms, "current wall time"),
                (current_monotonic, "current monotonic time")):
            if not isinstance(value, int) or isinstance(value, bool) or value < 0:
                raise PolicyError(f"{label} is invalid")
        if (
                ledger.get("boot_id") != current_boot or
                receipt.get("boot_id") != current_boot or
                receipt.get("generation") != ledger.get("generation") or
                receipt.get("state_sha256") != ledger.get("state_sha256") or
                receipt.get("publisher_pid") != ledger.get("publisher_pid") or
                receipt.get("publisher_start_ticks") !=
                    ledger.get("publisher_start_ticks") or
                not isinstance(receipt.get("generation"), int) or
                isinstance(receipt.get("generation"), bool) or
                receipt["generation"] <= 0 or
                not isinstance(receipt.get("observed_at_ms"), int) or
                not isinstance(receipt.get("observed_monotonic_ns"), int) or
                not receipt["observed_at_ms"] <= current_ms or
                current_ms - receipt["observed_at_ms"] >
                    BOUNDARY_MAXIMUM_AGE_MS or
                not receipt["observed_monotonic_ns"] <= current_monotonic or
                current_monotonic - receipt["observed_monotonic_ns"] >
                    BOUNDARY_MAXIMUM_AGE_MS * 1_000_000):
            raise PolicyError("current boundary receipt is stale or rolled back")
        publisher_pid = receipt.get("publisher_pid")
        publisher_start = receipt.get("publisher_start_ticks")
        if (
                not isinstance(publisher_pid, int) or
                isinstance(publisher_pid, bool) or publisher_pid <= 0 or
                not isinstance(publisher_start, int) or
                isinstance(publisher_start, bool) or publisher_start <= 0):
            raise PolicyError("current boundary publisher identity is invalid")
        try:
            current_start = _process_start_ticks(publisher_pid, proc_root)
        except PolicyError as error:
            raise PolicyError("current boundary publisher is not alive") from error
        if current_start != publisher_start:
            raise PolicyError("current boundary publisher identity was reused")
        fingerprints = receipt.get("source_fingerprints")
        if not isinstance(fingerprints, list) or len(fingerprints) != 3:
            raise PolicyError("current boundary source fingerprints are invalid")
        observed_fingerprints: list[SourceFingerprint] = []
        fingerprint_fields = {
            "path", "present", "device", "inode", "mode", "links", "uid",
            "gid", "size", "mtime_ns", "ctime_ns", "sha256",
            "installed_mode",
        }
        for value in fingerprints:
            if not isinstance(value, dict) or set(value) != fingerprint_fields:
                raise PolicyError(
                    "current boundary source fingerprints are invalid")
            digest = value.get("sha256")
            if digest is not None and (
                    not isinstance(digest, str) or
                    re.fullmatch(r"sha256:[0-9a-f]{64}", digest) is None):
                raise PolicyError(
                    "current boundary source fingerprints are invalid")
            try:
                fingerprint = SourceFingerprint(
                    path=Path(value["path"]), present=value["present"],
                    device=value["device"], inode=value["inode"],
                    mode=value["mode"], links=value["links"], uid=value["uid"],
                    gid=value["gid"], size=value["size"],
                    mtime_ns=value["mtime_ns"], ctime_ns=value["ctime_ns"],
                    sha256="" if digest is None else digest.removeprefix(
                        "sha256:"),
                    installed_mode=value["installed_mode"])
            except (KeyError, TypeError, ValueError) as error:
                raise PolicyError(
                    "current boundary source fingerprints are invalid") from error
            if (
                    type(fingerprint.present) is not bool or
                    not source_fingerprint_matches(
                        fingerprint,
                        require_installed_metadata=require_installed_metadata)):
                raise PolicyError("current boundary source fingerprints drifted")
            observed_fingerprints.append(fingerprint)
        state = receipt.get("state")
        connectors = receipt.get("authorized_connectors")
        connector_count = receipt.get("authorized_connector_count")
        uids = receipt.get("authorized_uids")
        if (
                state not in {"DENY_ALL", "ACTIVE"} or
                receipt.get("status") != "EXACT_" + str(state) or
                not isinstance(connectors, list) or
                not isinstance(connector_count, int) or
                isinstance(connector_count, bool) or
                connector_count != len(connectors) or
                not isinstance(uids, list) or
                any(not isinstance(uid, int) or isinstance(uid, bool) or uid <= 0
                    for uid in uids) or
                uids != sorted(set(uids)) or
                (state == "DENY_ALL" and (
                    connectors or uids or receipt.get("paper_authorized") is not False)) or
                (state == "ACTIVE" and (
                    not connectors or not uids or
                    receipt.get("paper_authorized") is not True))):
            raise PolicyError("current boundary authority state is invalid")
        semantics = {
            "state": state, "family": receipt.get("family"),
            "table": receipt.get("table"), "chain": receipt.get("chain"),
            "guard_chain": receipt.get("guard_chain"),
            "protected_tcp_destination_ports":
                receipt.get("protected_tcp_destination_ports"),
            "authorized_connectors": connectors,
            "source_policy_sha256": receipt.get("source_policy_sha256"),
            "identity_manifest_sha256":
                receipt.get("identity_manifest_sha256"),
            "effective_policy_sha256":
                receipt.get("effective_policy_sha256"),
            "source_fingerprints": fingerprints,
        }
        table_digest = _sha256(_canonical_json(semantics))
        state_digest = _sha256(_canonical_json({
            "state": state,
            "effective_policy_sha256": receipt.get("effective_policy_sha256"),
            "table_semantic_sha256": table_digest,
            "source_fingerprints": fingerprints,
        }))
        if (
                receipt.get("table_semantic_sha256") != table_digest or
                receipt.get("state_sha256") != state_digest):
            raise PolicyError("current boundary semantic digest mismatch")
        if (
                receipt.get("protected_tcp_destination_ports") !=
                    list(EXPECTED_PORTS) or
                receipt.get("protected_port_count") != len(EXPECTED_PORTS) or
                receipt.get("family") != EXPECTED_FAMILY or
                receipt.get("table") != EXPECTED_TABLE or
                receipt.get("chain") != EXPECTED_CHAIN or
                receipt.get("guard_chain") != GUARD_CHAIN or
                receipt.get("live_authorized") is not False):
            raise PolicyError("current boundary fixed contract mismatch")
        return BoundaryReceipt(receipt, receipt_raw, _sha256(receipt_raw))
    finally:
        if lock >= 0:
            try:
                fcntl.flock(lock, fcntl.LOCK_UN)
            finally:
                os.close(lock)
        os.close(directory)


def _fixed_connector(
        document: dict[str, Any], identity: str) -> BrokerConnector:
    if set(document) != {"schema", "identities"}:
        raise PolicyError("identity manifest root fields mismatch")
    if document.get("schema") != "hepta.service-identities.v1":
        raise PolicyError("identity manifest schema mismatch")
    identities = document.get("identities")
    if not isinstance(identities, dict) or identity not in identities:
        raise PolicyError("authorized identity is absent from identity manifest")
    record = identities[identity]
    if not isinstance(record, dict) or set(record) != {"uid", "gid", "role"}:
        raise PolicyError("authorized identity record fields mismatch")
    uid = record.get("uid")
    gid = record.get("gid")
    if (
            not isinstance(uid, int) or isinstance(uid, bool) or
            uid <= 0 or uid > (1 << 32) - 1 or
            not isinstance(gid, int) or isinstance(gid, bool) or
            gid <= 0 or gid > (1 << 32) - 1 or
            record.get("role") != EXPECTED_ROLE):
        raise PolicyError("authorized identity manifest record is invalid")
    return BrokerConnector(
        domain_id="default",
        identity=identity,
        uid=uid,
        gid=gid,
        role=EXPECTED_ROLE,
    )


def _uint32(value: Any, label: str) -> int:
    if (
            not isinstance(value, int) or isinstance(value, bool) or
            value <= 0 or value > (1 << 32) - 1):
        raise PolicyError(f"{label}: invalid positive uint32")
    return value


def _paper_connectors(
        raw: Optional[bytes], policy_sha256: str) -> tuple[BrokerConnector, ...]:
    if raw is None:
        return ()
    document = _strict_json(raw, "PAPER identity manifest")
    if set(document) != {
            "schema", "version", "source_policy_sha256",
            "paper_authorized", "live_authorized", "identities"}:
        raise PolicyError("PAPER identity manifest fields mismatch")
    if (
            document.get("schema") != PAPER_IDENTITY_SCHEMA or
            not isinstance(document.get("version"), int) or
            isinstance(document.get("version"), bool) or
            document.get("version") != 1 or
            document.get("source_policy_sha256") !=
            "sha256:" + policy_sha256 or
            document.get("live_authorized") is not False):
        raise PolicyError("PAPER identity manifest fixed contract mismatch")
    paper_authorized = document.get("paper_authorized")
    identities = document.get("identities")
    if (
            not isinstance(paper_authorized, bool) or
            not isinstance(identities, list) or
            len(identities) > MAX_PAPER_IDENTITIES or
            (paper_authorized and not identities) or
            (not paper_authorized and identities)):
        raise PolicyError("PAPER identity authorization/list mismatch")

    connectors: list[BrokerConnector] = []
    for index, record in enumerate(identities):
        label = f"PAPER identity manifest identities[{index}]"
        if (
                not isinstance(record, dict) or
                set(record) != {
                    "domain_id", "identity", "uid", "gid", "role"}):
            raise PolicyError(f"{label}: fields mismatch")
        domain_id = record.get("domain_id")
        identity = record.get("identity")
        if (
                not isinstance(domain_id, str) or
                re.fullmatch(r"[a-z][a-z0-9-]{0,31}", domain_id) is None or
                domain_id == "default" or
                identity != f"hepta-ib-exec-{domain_id}" or
                len(identity.encode("ascii")) > 32 or
                record.get("role") != EXPECTED_ROLE):
            raise PolicyError(
                f"{label}: dedicated IB Execution identity mismatch")
        uid = _uint32(record.get("uid"), label + ".uid")
        gid = _uint32(record.get("gid"), label + ".gid")
        if uid != gid:
            raise PolicyError(
                f"{label}: dedicated IB Execution UID/GID mismatch")
        if uid in {2001, 2002, 2003, 2004} or gid in {
                2001, 2002, 2003, 2004}:
            raise PolicyError(f"{label}: compatibility identity collision")
        connectors.append(BrokerConnector(
            domain_id=domain_id,
            identity=identity,
            uid=uid,
            gid=gid,
            role=EXPECTED_ROLE,
        ))

    if connectors != sorted(connectors, key=lambda item: item.domain_id):
        raise PolicyError("PAPER connector records are not domain-sorted")
    for field, values in (
            ("domain", [item.domain_id for item in connectors]),
            ("identity", [item.identity for item in connectors]),
            ("uid", [item.uid for item in connectors]),
            ("gid", [item.gid for item in connectors])):
        if len(set(values)) != len(values):
            raise PolicyError(f"PAPER connector {field} values are not unique")
    return tuple(connectors)


def _require_explicit_deny_all_authorization(
        raw: Optional[bytes], policy_sha256: str) -> None:
    if raw is None:
        raise PolicyError(
            "deny-all supervisor requires an explicit PAPER identity manifest")
    # Reuse the complete manifest validator before applying the narrower
    # activation-time contract.  In particular, the manifest must remain
    # source-policy bound and must not carry a LIVE authorization bit.
    connectors = _paper_connectors(raw, policy_sha256)
    document = _strict_json(raw, "PAPER identity manifest")
    if (
            document.get("paper_authorized") is not False or
            document.get("live_authorized") is not False or
            document.get("identities") != [] or
            connectors):
        raise PolicyError(
            "deny-all supervisor requires paper_authorized=false, "
            "live_authorized=false, and identities=[]")


def _effective_sha256(
        policy_raw: bytes, identity_raw: bytes,
        paper_identity_raw: Optional[bytes]) -> str:
    digest = hashlib.sha256()
    for label, raw in (
            (b"policy", policy_raw),
            (b"identity", identity_raw),
            (b"paper", paper_identity_raw)):
        digest.update(label)
        digest.update(b"\x00")
        if raw is None:
            digest.update(b"absent")
        else:
            digest.update(str(len(raw)).encode("ascii"))
            digest.update(b"\x00")
            digest.update(raw)
        digest.update(b"\x00")
    return digest.hexdigest()


def parse_policy(
        policy_raw: bytes,
        identity_raw: bytes,
        paper_identity_raw: Optional[bytes] = None,
) -> BrokerNetworkPolicy:
    document = _strict_json(policy_raw, "broker network policy")
    expected_fields = {
        "schema",
        "version",
        "family",
        "table",
        "chain",
        "protected_tcp_destination_ports",
        "authorized_connectors",
        "paper_identity_manifest",
        "default_for_protected_ports",
        "preserve_other_egress",
        "identity_manifest_sha256",
    }
    if set(document) != expected_fields:
        raise PolicyError("broker network policy fields mismatch")
    if (
            document.get("schema") != "hepta.broker-network-policy.v1" or
            not isinstance(document.get("version"), int) or
            isinstance(document.get("version"), bool) or
            document.get("version") != 1 or
            document.get("family") != EXPECTED_FAMILY or
            document.get("table") != EXPECTED_TABLE or
            document.get("chain") != EXPECTED_CHAIN or
            document.get("default_for_protected_ports") != "reject" or
            document.get("preserve_other_egress") is not True):
        raise PolicyError("broker network policy fixed contract mismatch")

    ports = document.get("protected_tcp_destination_ports")
    if (
            not isinstance(ports, list) or
            any(
                not isinstance(port, int) or isinstance(port, bool)
                for port in ports) or
            tuple(ports) != EXPECTED_PORTS):
        raise PolicyError("protected IB TCP port set mismatch")

    connectors = document.get("authorized_connectors")
    if (
            not isinstance(connectors, list) or len(connectors) != 1 or
            not isinstance(connectors[0], dict) or
            set(connectors[0]) != {
                "domain_id", "identity", "uid", "gid", "role"} or
            connectors[0].get("domain_id") != "default" or
            connectors[0].get("identity") != EXPECTED_IDENTITY or
            connectors[0].get("role") != EXPECTED_ROLE):
        raise PolicyError("authorized broker connector mismatch")
    paper_manifest = document.get("paper_identity_manifest")
    if paper_manifest != {
            "path": PAPER_IDENTITY_PATH,
            "schema": PAPER_IDENTITY_SCHEMA,
            "required": False,
            "max_identities": MAX_PAPER_IDENTITIES,
            "default_paper_authorized": False,
            }:
        raise PolicyError("PAPER identity manifest policy mismatch")

    identity_digest = hashlib.sha256(identity_raw).hexdigest()
    declared_digest = document.get("identity_manifest_sha256")
    if (
            not isinstance(declared_digest, str) or
            declared_digest != "sha256:" + identity_digest):
        raise PolicyError("identity manifest digest binding mismatch")
    identities = _strict_json(identity_raw, "identity manifest")
    fixed = _fixed_connector(identities, EXPECTED_IDENTITY)
    declared_fixed = connectors[0]
    if (
            declared_fixed["uid"] != fixed.uid or
            declared_fixed["gid"] != fixed.gid):
        raise PolicyError(
            "authorized connector UID/GID differs from manifest")
    source_sha256 = hashlib.sha256(policy_raw).hexdigest()
    opted_in = _paper_connectors(paper_identity_raw, source_sha256)
    # Fixed compatibility and per-domain PAPER are mutually exclusive
    # network-authority modes.  Keeping UID 2003 in a domain allowlist would
    # leave a second unbound broker path on a per-domain host.
    all_connectors = opted_in if opted_in else (fixed,)
    all_uids = [connector.uid for connector in all_connectors]
    all_gids = [connector.gid for connector in all_connectors]
    if (
            len(set(all_uids)) != len(all_uids) or
            len(set(all_gids)) != len(all_gids)):
        raise PolicyError(
            "PAPER connector UID/GID collides with compatibility identity")

    return BrokerNetworkPolicy(
        family=EXPECTED_FAMILY,
        table=EXPECTED_TABLE,
        chain=EXPECTED_CHAIN,
        ports=EXPECTED_PORTS,
        authorized_connectors=all_connectors,
        source_sha256=source_sha256,
        identity_manifest_sha256=identity_digest,
        effective_sha256=_effective_sha256(
            policy_raw, identity_raw, paper_identity_raw),
    )


def load_policy_bundle(
        policy_path: Path,
        identity_path: Path,
        paper_identity_path: Path = DEFAULT_PAPER_IDENTITIES,
        *,
        require_installed_metadata: bool = True,
        require_explicit_deny_all_authorization: bool = False,
) -> LoadedPolicy:
    policy_raw, policy_fingerprint = _read_fingerprinted(
        policy_path,
        require_installed_metadata=require_installed_metadata,
        installed_mode=0o644)
    identity_raw, identity_fingerprint = _read_fingerprinted(
        identity_path,
        require_installed_metadata=require_installed_metadata,
        installed_mode=0o644)
    paper_identity_raw, paper_fingerprint = _read_fingerprinted(
        paper_identity_path,
        require_installed_metadata=require_installed_metadata,
        installed_mode=0o600,
        optional=True)
    if policy_raw is None or identity_raw is None:
        raise PolicyError("required broker policy source disappeared")
    source_sha256 = hashlib.sha256(policy_raw).hexdigest()
    if require_explicit_deny_all_authorization:
        _require_explicit_deny_all_authorization(
            paper_identity_raw, source_sha256)
    fixed_only = parse_policy(policy_raw, identity_raw, None)
    return LoadedPolicy(
        policy=parse_policy(
            policy_raw, identity_raw, paper_identity_raw),
        fixed_only=fixed_only,
        deny_all=_deny_all_policy(fixed_only),
        fingerprints=(
            policy_fingerprint, identity_fingerprint, paper_fingerprint),
        explicit_deny_all_authorization=(
            require_explicit_deny_all_authorization),
    )


def load_policy(
        policy_path: Path,
        identity_path: Path,
        paper_identity_path: Path = DEFAULT_PAPER_IDENTITIES,
        *,
        require_installed_metadata: bool = True,
) -> BrokerNetworkPolicy:
    return load_policy_bundle(
        policy_path,
        identity_path,
        paper_identity_path,
        require_installed_metadata=require_installed_metadata,
    ).policy


def load_fixed_only_policy(
        policy_path: Path, identity_path: Path, *,
        require_installed_metadata: bool = True) -> BrokerNetworkPolicy:
    return parse_policy(
        _read_stable_regular(
            policy_path,
            require_installed_metadata=require_installed_metadata,
            installed_mode=0o644),
        _read_stable_regular(
            identity_path,
            require_installed_metadata=require_installed_metadata,
            installed_mode=0o644),
        None,
    )


def _emergency_deny_all_policy() -> BrokerNetworkPolicy:
    # This fallback must remain constructible after every mutable manifest has
    # disappeared or become corrupt.  ExecStopPost and watchdog failure paths
    # cannot safely depend on the same inputs whose drift triggered shutdown.
    # The normal parser already requires these exact constants, so the
    # emergency policy is not a second configurable policy surface.
    source_digest = hashlib.sha256(
        b"hepta-broker-emergency-source-v1").hexdigest()
    identity_digest = hashlib.sha256(
        b"hepta-broker-emergency-identity-v1").hexdigest()
    digest = hashlib.sha256()
    digest.update(b"hepta-broker-deny-all-v1\x00")
    digest.update(EXPECTED_FAMILY.encode("ascii"))
    digest.update(b"\x00")
    digest.update(EXPECTED_TABLE.encode("ascii"))
    digest.update(b"\x00")
    digest.update(EXPECTED_CHAIN.encode("ascii"))
    digest.update(b"\x00")
    digest.update(",".join(
        str(port) for port in EXPECTED_PORTS).encode("ascii"))
    return BrokerNetworkPolicy(
        family=EXPECTED_FAMILY,
        table=EXPECTED_TABLE,
        chain=EXPECTED_CHAIN,
        ports=EXPECTED_PORTS,
        authorized_connectors=(),
        source_sha256=source_digest,
        identity_manifest_sha256=identity_digest,
        effective_sha256=digest.hexdigest(),
    )


def _deny_all_policy(
        fixed_policy: BrokerNetworkPolicy) -> BrokerNetworkPolicy:
    del fixed_policy
    return _emergency_deny_all_policy()


def load_deny_all_policy(
        policy_path: Path, identity_path: Path, *,
        require_installed_metadata: bool = True) -> BrokerNetworkPolicy:
    # Keep the arguments for CLI/API compatibility, but intentionally do not
    # read them: deny-all is the recovery path for missing or corrupt inputs.
    del policy_path, identity_path, require_installed_metadata
    return _emergency_deny_all_policy()


def render_transaction(
        policy: BrokerNetworkPolicy, *, replace_existing: bool = True) -> bytes:
    if (
            policy.family != EXPECTED_FAMILY or
            policy.table != EXPECTED_TABLE or
            policy.chain != EXPECTED_CHAIN or
            policy.ports != EXPECTED_PORTS or
            any(
                connector.uid <= 0 or connector.role != EXPECTED_ROLE
                for connector in policy.authorized_connectors) or
            len(set(policy.authorized_uids)) !=
            len(policy.authorized_connectors) or
            re.fullmatch(r"[0-9a-f]{64}", policy.effective_sha256) is None):
        raise PolicyError("refusing to render a noncanonical policy")
    connectors = policy.authorized_connectors
    if connectors:
        compatibility = (
            len(connectors) == 1 and
            connectors[0].identity == EXPECTED_IDENTITY and
            connectors[0].domain_id == "default")
        templated = (
            len(connectors) == 1 and
            connectors[0].domain_id != "default" and
            connectors[0].identity ==
            f"hepta-ib-exec-{connectors[0].domain_id}")
        if not compatibility and not templated:
            raise PolicyError("refusing to render mixed broker authorities")
    ports = ", ".join(str(port) for port in policy.ports)
    uids = policy.authorized_uids
    lines = [
        *((f"delete table {policy.family} {policy.table}",)
          if replace_existing else ()),
        f"add table {policy.family} {policy.table}",
        f"add chain {policy.family} {policy.table} {policy.chain} "
        "{ type filter hook output priority filter; policy accept; }",
        f"add chain {policy.family} {policy.table} {GUARD_CHAIN}",
        f"add rule {policy.family} {policy.table} {policy.chain} "
        f"fib daddr type local meta l4proto tcp "
        f"tcp dport {{ {ports} }} "
        f"jump {GUARD_CHAIN} comment "
        f'"heptatrader-ib-ports:{policy.effective_sha256}"',
    ]
    if uids:
        uid_expression = (
            str(uids[0]) if len(uids) == 1 else
            "{ " + ", ".join(str(uid) for uid in uids) + " }")
        lines.append(
            f"add rule {policy.family} {policy.table} {GUARD_CHAIN} "
            f"meta skuid {uid_expression} counter return "
            f'comment "heptatrader-ib-uids:{policy.effective_sha256}"')
    lines.append(
        f"add rule {policy.family} {policy.table} {GUARD_CHAIN} counter "
        "reject with tcp reset comment "
        f'"heptatrader-ib-default-reject:{policy.effective_sha256}"')
    return ("\n".join(lines) + "\n").encode("ascii")


def _run_nft(binary: Path, arguments: Sequence[str],
             standard_input: Optional[bytes]) -> subprocess.CompletedProcess[bytes]:
    timeout = (
        NFT_QUERY_TIMEOUT_SECONDS
        if tuple(arguments[:3]) == ("--json", "list", "table")
        else NFT_COMMAND_TIMEOUT_SECONDS)
    try:
        completed = subprocess.run(
            [str(binary), *arguments],
            input=standard_input,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=SAFE_ENVIRONMENT,
            cwd="/",
            close_fds=True,
            timeout=timeout,
            check=False,
        )
    except (OSError, subprocess.SubprocessError) as error:
        raise PolicyError("nftables command execution failed") from error
    if (
            len(completed.stdout) > MAX_NFT_OUTPUT_BYTES or
            len(completed.stderr) > MAX_NFT_OUTPUT_BYTES):
        raise PolicyError("nftables command output exceeded limit")
    return completed


def _trusted_nft_binary() -> Path:
    for candidate in NFT_CANDIDATES:
        try:
            metadata = os.lstat(candidate)
        except FileNotFoundError:
            continue
        if (
                stat.S_ISREG(metadata.st_mode) and
                not stat.S_ISLNK(metadata.st_mode) and
                metadata.st_uid == 0 and
                metadata.st_gid == 0 and
                stat.S_IMODE(metadata.st_mode) & 0o111 and
                not stat.S_IMODE(metadata.st_mode) & 0o022):
            return candidate
    raise PolicyError("trusted root-owned nft binary is unavailable")


def validate_os_identities(policy: BrokerNetworkPolicy) -> None:
    all_accounts = pwd.getpwall()
    all_groups = grp.getgrall()
    for connector in policy.authorized_connectors:
        try:
            account = pwd.getpwnam(connector.identity)
            account_by_uid = pwd.getpwuid(connector.uid)
            group = grp.getgrnam(connector.identity)
            group_by_gid = grp.getgrgid(connector.gid)
        except KeyError as error:
            raise PolicyError(
                "authorized broker OS identity is not provisioned") from error
        if (
                account.pw_name != connector.identity or
                account_by_uid.pw_name != connector.identity or
                account.pw_uid != connector.uid or
                account.pw_gid != connector.gid or
                account.pw_dir != "/nonexistent" or
                not account.pw_shell.endswith("/nologin") or
                [item.pw_name for item in all_accounts
                 if item.pw_uid == connector.uid] != [connector.identity] or
                group.gr_name != connector.identity or
                group_by_gid.gr_name != connector.identity or
                group.gr_gid != connector.gid or
                [item.gr_name for item in all_groups
                 if item.gr_gid == connector.gid] != [connector.identity] or
                group.gr_mem or
                set(os.getgrouplist(
                    connector.identity, connector.gid)) != {connector.gid}):
            raise PolicyError(
                "authorized broker OS identity metadata mismatch")


def _exact_object(
        value: Any, keys: set[str], label: str) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) != keys:
        raise PolicyError(f"live nft {label} fields mismatch")
    return value


def _nft_match(
        value: Any, left: dict[str, Any], right: Any,
        label: str) -> None:
    expression = _exact_object(value, {"match"}, label)
    match = _exact_object(
        expression["match"], {"op", "left", "right"}, label + " match")
    if (
            match["op"] != "==" or match["left"] != left or
            match["right"] != right):
        raise PolicyError(f"live nft {label} semantics mismatch")


def _nft_counter(value: Any, label: str) -> None:
    expression = _exact_object(value, {"counter"}, label)
    counter = _exact_object(
        expression["counter"], {"packets", "bytes"}, label + " counter")
    if any(
            not isinstance(counter[key], int) or
            isinstance(counter[key], bool) or counter[key] < 0
            for key in ("packets", "bytes")):
        raise PolicyError(f"live nft {label} counter is invalid")


def _verify_rule_header(
        rule: Any, policy: BrokerNetworkPolicy, chain: str,
        comment: str, label: str) -> list[Any]:
    if not isinstance(rule, dict):
        raise PolicyError(f"live nft {label} is not an object")
    allowed = {"family", "table", "chain", "expr", "comment", "handle"}
    if set(rule) not in (
            {"family", "table", "chain", "expr", "comment"},
            allowed):
        raise PolicyError(f"live nft {label} fields mismatch")
    if (
            rule.get("family") != policy.family or
            rule.get("table") != policy.table or
            rule.get("chain") != chain or
            rule.get("comment") != comment or
            ("handle" in rule and (
                not isinstance(rule["handle"], int) or
                isinstance(rule["handle"], bool) or rule["handle"] <= 0)) or
            not isinstance(rule.get("expr"), list)):
        raise PolicyError(f"live nft {label} header mismatch")
    return rule["expr"]


def verify_active_policy_json(
        policy: BrokerNetworkPolicy, raw: bytes) -> None:
    document = _strict_json(raw, "live nft table JSON")
    if set(document) != {"nftables"}:
        raise PolicyError("live nft JSON root fields mismatch")
    records = document["nftables"]
    if not isinstance(records, list):
        raise PolicyError("live nft records must be a list")
    tables: list[dict[str, Any]] = []
    chains: list[dict[str, Any]] = []
    rules: list[dict[str, Any]] = []
    metainfo_count = 0
    for index, record in enumerate(records):
        if not isinstance(record, dict) or len(record) != 1:
            raise PolicyError(
                f"live nft record[{index}] is not a tagged object")
        kind, value = next(iter(record.items()))
        if kind == "metainfo":
            metainfo_count += 1
            if metainfo_count > 1 or not isinstance(value, dict):
                raise PolicyError("live nft metainfo multiplicity mismatch")
        elif kind == "table":
            tables.append(value)
        elif kind == "chain":
            chains.append(value)
        elif kind == "rule":
            rules.append(value)
        else:
            raise PolicyError(f"live nft contains unexpected {kind!r} object")
    expected_rule_count = 3 if policy.authorized_uids else 2
    if (
            len(tables) != 1 or len(chains) != 2 or
            len(rules) != expected_rule_count):
        raise PolicyError("live nft table object counts mismatch")

    table = tables[0]
    if not isinstance(table, dict) or set(table) not in (
            {"family", "name"}, {"family", "name", "handle"}):
        raise PolicyError("live nft table fields mismatch")
    if (
            table.get("family") != policy.family or
            table.get("name") != policy.table or
            ("handle" in table and (
                not isinstance(table["handle"], int) or
                isinstance(table["handle"], bool) or table["handle"] <= 0))):
        raise PolicyError("live nft table identity mismatch")

    by_name: dict[str, dict[str, Any]] = {}
    for chain in chains:
        if not isinstance(chain, dict) or not isinstance(
                chain.get("name"), str) or chain["name"] in by_name:
            raise PolicyError("live nft chain identity mismatch")
        by_name[chain["name"]] = chain
    if set(by_name) != {policy.chain, GUARD_CHAIN}:
        raise PolicyError("live nft chain allowlist mismatch")
    base = by_name[policy.chain]
    if set(base) not in (
            {"family", "table", "name", "type", "hook", "prio", "policy"},
            {
                "family", "table", "name", "type", "hook", "prio",
                "policy", "handle",
            }) or (
                base.get("family"), base.get("table"), base.get("name"),
                base.get("type"), base.get("hook"), base.get("prio"),
                base.get("policy")) != (
                    policy.family, policy.table, policy.chain,
                    "filter", "output", 0, "accept"):
        raise PolicyError("live nft base chain semantics mismatch")
    guard = by_name[GUARD_CHAIN]
    if set(guard) not in (
            {"family", "table", "name"},
            {"family", "table", "name", "handle"}) or (
                guard.get("family"), guard.get("table"), guard.get("name")
                ) != (policy.family, policy.table, GUARD_CHAIN):
        raise PolicyError("live nft guard chain semantics mismatch")
    for chain in (base, guard):
        if "handle" in chain and (
                not isinstance(chain["handle"], int) or
                isinstance(chain["handle"], bool) or chain["handle"] <= 0):
            raise PolicyError("live nft chain handle is invalid")

    comments = {
        f"heptatrader-ib-ports:{policy.effective_sha256}",
        f"heptatrader-ib-default-reject:{policy.effective_sha256}",
    }
    if policy.authorized_uids:
        comments.add(f"heptatrader-ib-uids:{policy.effective_sha256}")
    by_comment: dict[str, dict[str, Any]] = {}
    for rule in rules:
        if not isinstance(rule, dict) or rule.get("comment") in by_comment:
            raise PolicyError("live nft rule comment identity mismatch")
        by_comment[rule.get("comment")] = rule
    if set(by_comment) != comments:
        raise PolicyError("live nft rule/digest comment allowlist mismatch")

    port_comment = f"heptatrader-ib-ports:{policy.effective_sha256}"
    expressions = _verify_rule_header(
        by_comment[port_comment], policy, policy.chain,
        port_comment, "protected-port rule")
    if len(expressions) not in (3, 4):
        raise PolicyError("live nft protected-port rule length mismatch")
    _nft_match(
        expressions[0],
        {"fib": {"result": "type", "flags": ["daddr"]}},
        "local", "protected-port local destination")
    if len(expressions) == 4:
        _nft_match(
            expressions[1], {"meta": {"key": "l4proto"}}, "tcp",
            "protected-port l4proto")
        port_index = 2
    else:
        # nft 1.0.x canonicalizes the explicit l4proto match into the
        # protocol-qualified tcp payload expression.
        port_index = 1
    _nft_match(
        expressions[port_index],
        {"payload": {"protocol": "tcp", "field": "dport"}},
        {"set": list(policy.ports)}, "protected-port set")
    if expressions[port_index + 1] != {"jump": {"target": GUARD_CHAIN}}:
        raise PolicyError("live nft protected-port jump mismatch")

    if policy.authorized_uids:
        uid_comment = f"heptatrader-ib-uids:{policy.effective_sha256}"
        expressions = _verify_rule_header(
            by_comment[uid_comment], policy, GUARD_CHAIN,
            uid_comment, "authorized-UID rule")
        if len(expressions) != 3:
            raise PolicyError("live nft authorized-UID rule length mismatch")
        expected_uid: Any = (
            policy.authorized_uids[0] if len(policy.authorized_uids) == 1
            else {"set": list(policy.authorized_uids)})
        _nft_match(
            expressions[0], {"meta": {"key": "skuid"}}, expected_uid,
            "authorized-UID set")
        _nft_counter(expressions[1], "authorized-UID")
        if expressions[2] != {"return": None}:
            raise PolicyError("live nft authorized-UID return mismatch")

    reject_comment = (
        f"heptatrader-ib-default-reject:{policy.effective_sha256}")
    expressions = _verify_rule_header(
        by_comment[reject_comment], policy, GUARD_CHAIN,
        reject_comment, "default-reject rule")
    if len(expressions) == 3:
        _nft_match(
            expressions[0], {"meta": {"key": "l4proto"}}, "tcp",
            "default-reject l4proto")
        reject_index = 1
    elif len(expressions) == 2:
        reject_index = 0
    else:
        raise PolicyError("live nft default-reject rule length mismatch")
    _nft_counter(expressions[reject_index], "default-reject")
    if expressions[reject_index + 1] != {"reject": {"type": "tcp reset"}}:
        raise PolicyError("live nft default-reject semantics mismatch")


def _read_active_policy_json(
        policy: BrokerNetworkPolicy, runner: Runner) -> bytes:
    completed = runner(
        ("--json", "list", "table", policy.family, policy.table), None)
    if completed.returncode != 0 or completed.stderr:
        raise PolicyError("live nft broker table is unavailable")
    return completed.stdout


def verify_active_policy(
        policy: BrokerNetworkPolicy, runner: Runner) -> None:
    verify_active_policy_json(policy, _read_active_policy_json(policy, runner))


def verify_guarded_policy(
        loaded: LoadedPolicy, runner: Runner) -> BrokerNetworkPolicy:
    errors: list[str] = []
    candidates = (
        (loaded.deny_all, _emergency_deny_all_policy(), loaded.policy)
        if loaded.policy.authorized_connectors and
        loaded.policy.authorized_connectors[0].domain_id != "default"
        else (loaded.policy, _emergency_deny_all_policy()))
    if any(
            (candidate.family, candidate.table) !=
            (loaded.policy.family, loaded.policy.table)
            for candidate in candidates):
        raise PolicyError("guarded broker policy candidate identity mismatch")
    # All candidates describe mutually exclusive exact states of the same
    # kernel table. Read that table once per watchdog sample, then compare the
    # immutable bytes in memory. Re-running the same nft query for every
    # candidate amplified a 250 ms guard cadence to as many as twelve nft
    # subprocesses per second and could spuriously hit the bounded command
    # deadline under host CPU pressure. A failed single read remains
    # fail-closed; no stale sample is retained or retried.
    raw = _read_active_policy_json(loaded.policy, runner)
    for candidate in candidates:
        try:
            verify_active_policy_json(candidate, raw)
            return candidate
        except PolicyError as error:
            errors.append(str(error))
    raise PolicyError(
        "live nft broker table is neither guarded base nor active domain: " +
        "; ".join(errors))


def apply_policy(
        policy: BrokerNetworkPolicy,
        runner: Runner,
) -> None:
    table = (policy.family, policy.table)
    probe = runner(("list", "table", *table), None)
    transaction = render_transaction(
        policy, replace_existing=probe.returncode == 0)
    checked = runner(("--check", "--file", "-"), transaction)
    if checked.returncode != 0:
        raise PolicyError("nftables rejected the broker policy transaction")
    applied = runner(("--file", "-"), transaction)
    if applied.returncode != 0:
        raise PolicyError("nftables failed to apply the broker policy")
    verify_active_policy(policy, runner)


class SystemdNotifier:
    def __init__(self, *, required: bool):
        value = os.environ.get("NOTIFY_SOCKET", "")
        if not value:
            if required:
                raise PolicyError("Type=notify guard requires NOTIFY_SOCKET")
            self._address: Optional[str] = None
            return
        if "\x00" in value or len(value.encode("utf-8")) > 107:
            raise PolicyError("NOTIFY_SOCKET is invalid")
        self._address = "\x00" + value[1:] if value.startswith("@") else value

    def send(self, message: str) -> None:
        if self._address is None:
            return
        if (
                not message or "\x00" in message or
                len(message.encode("utf-8")) > 4096):
            raise PolicyError("sd_notify message is invalid")
        channel = socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM)
        try:
            channel.settimeout(1)
            channel.connect(self._address)
            channel.sendall(message.encode("utf-8"))
        except OSError as error:
            raise PolicyError("sd_notify delivery failed") from error
        finally:
            channel.close()


def _watchdog_poll_interval() -> float:
    raw = os.environ.get("WATCHDOG_USEC", "")
    if not raw:
        return DEFAULT_POLL_INTERVAL_SECONDS
    if not raw.isdecimal():
        raise PolicyError("WATCHDOG_USEC is invalid")
    watchdog_seconds = int(raw) / 1_000_000
    if watchdog_seconds <= 0:
        raise PolicyError("WATCHDOG_USEC is invalid")
    return max(
        0.05, min(DEFAULT_POLL_INTERVAL_SECONDS, watchdog_seconds / 4))


def _ignore_boundary_publication(
        _policy: BrokerNetworkPolicy,
        _fingerprints: tuple[SourceFingerprint, ...]) -> None:
    return None


def _reload_policy_bundle_for_emergency(
        loaded: LoadedPolicy) -> LoadedPolicy:
    """Re-read source fingerprints before publishing a fail-close receipt.

    The guardian can atomically replace the PAPER identity manifest while an
    ACTIVE supervisor is unwinding.  The live nft policy is already tightened
    to DENY_ALL at this point, but the supervisor's startup snapshot then
    contains the old inode/digest tuple and a receipt publication would reject
    it as drift.  Re-reading all three sources binds the replacement to a
    fresh, validated bundle; a failed re-read still fails closed.
    """
    if len(loaded.fingerprints) != 3:
        raise PolicyError("emergency policy source fingerprint count mismatch")
    return load_policy_bundle(
        loaded.fingerprints[0].path,
        loaded.fingerprints[1].path,
        loaded.fingerprints[2].path,
        require_installed_metadata=True,
        require_explicit_deny_all_authorization=(
            loaded.explicit_deny_all_authorization),
    )


def supervise_policy(
        loaded: LoadedPolicy,
        runner: Runner,
        notifier: SystemdNotifier,
        stop_event: threading.Event,
        *,
        poll_interval: float,
        source_checker: Callable[[SourceFingerprint], bool] =
        source_fingerprint_matches,
        boundary_publisher: Callable[
            [BrokerNetworkPolicy, tuple[SourceFingerprint, ...]], None] =
        _ignore_boundary_publication,
) -> None:
    if poll_interval < 0.01 or poll_interval > 5:
        raise PolicyError("broker guard poll interval is outside safety bounds")
    drift = ""
    try:
        observed_policy = verify_guarded_policy(loaded, runner)
        boundary_publisher(observed_policy, loaded.fingerprints)
        notifier.send(
            "READY=1\n"
            "STATUS=HeptaTrader broker boundary active and exact\n"
            "WATCHDOG=1")
        while not stop_event.wait(poll_interval):
            if any(
                    not source_checker(fingerprint)
                    for fingerprint in loaded.fingerprints):
                if stop_event.is_set():
                    break
                drift = "policy input path/inode/digest drift"
                break
            # A status update is useful while a bounded query is in flight,
            # but watchdog credit is granted only after that sample has
            # passed exact live-table verification.
            notifier.send(
                "STATUS=HeptaTrader broker boundary validating")
            observed_policy = verify_guarded_policy(loaded, runner)
            boundary_publisher(observed_policy, loaded.fingerprints)
            notifier.send(
                "WATCHDOG=1\n"
                "STATUS=HeptaTrader broker boundary exact")
    except PolicyError as error:
        # A SIGTERM can arrive while the bounded live-table query is in
        # flight.  The stop path below still installs exact DENY_ALL; do not
        # report that expected shutdown as an input-drift incident.
        if not stop_event.is_set():
            drift = str(error)
    try:
        notifier.send(
            "STOPPING=1\n"
            "STATUS=HeptaTrader broker boundary revoking all broker authority")
    except PolicyError as error:
        if not drift:
            drift = str(error)
    tightening_error = ""
    try:
        apply_policy(loaded.deny_all, runner)
        try:
            boundary_publisher(loaded.deny_all, loaded.fingerprints)
        except PolicyError as error:
            # The only recoverable publication failure here is the expected
            # guardian replacement race.  The kernel is already DENY_ALL;
            # refresh and revalidate the source bundle, then publish the
            # fail-close receipt with its exact current fingerprints.  Do not
            # retry any nft mutation or swallow another policy error.
            if str(error) != "boundary receipt source fingerprints drifted":
                raise
            refreshed = _reload_policy_bundle_for_emergency(loaded)
            boundary_publisher(refreshed.deny_all, refreshed.fingerprints)
    except PolicyError as error:
        tightening_error = str(error)
    if tightening_error:
        raise PolicyError(
            "deny-all emergency tightening failed: " + tightening_error)
    if drift:
        raise PolicyError(
            "broker guard detected drift and installed deny-all policy: " +
            drift)


def supervise_deny_all_policy(
        loaded: LoadedPolicy,
        runner: Runner,
        notifier: SystemdNotifier,
        stop_event: threading.Event,
        *,
        poll_interval: float,
        source_checker: Callable[[SourceFingerprint], bool] =
        source_fingerprint_matches,
        boundary_publisher: Callable[
            [BrokerNetworkPolicy, tuple[SourceFingerprint, ...]], None] =
        _ignore_boundary_publication,
) -> None:
    if poll_interval < 0.01 or poll_interval > 5:
        raise PolicyError("broker guard poll interval is outside safety bounds")
    if not loaded.explicit_deny_all_authorization:
        raise PolicyError(
            "deny-all supervisor authorization state was not validated")
    deny_all = loaded.deny_all
    if (
            deny_all != _emergency_deny_all_policy() or
            deny_all.authorized_connectors):
        raise PolicyError("deny-all supervisor policy is not exact deny-all")

    drift = ""
    try:
        # This is deliberately the supervisor's first kernel mutation.  It
        # revokes every connector before readiness or watchdog credit exists.
        apply_policy(deny_all, runner)
        if any(
                not source_checker(fingerprint)
                for fingerprint in loaded.fingerprints):
            raise PolicyError(
                "policy input path/inode/digest drift before readiness")
        verify_active_policy(deny_all, runner)
        boundary_publisher(deny_all, loaded.fingerprints)
        notifier.send(
            "READY=1\n"
            "STATUS=HeptaTrader broker boundary exact deny-all\n"
            "WATCHDOG=1")
        while not stop_event.wait(poll_interval):
            if any(
                    not source_checker(fingerprint)
                    for fingerprint in loaded.fingerprints):
                drift = "policy input path/inode/digest drift"
                break
            # Never grant watchdog credit before the live table has passed an
            # exact deny-all verification for this sample.
            verify_active_policy(deny_all, runner)
            boundary_publisher(deny_all, loaded.fingerprints)
            notifier.send(
                "WATCHDOG=1\n"
                "STATUS=HeptaTrader broker boundary exact deny-all")
    except PolicyError as error:
        drift = str(error)
    try:
        notifier.send(
            "STOPPING=1\n"
            "STATUS=HeptaTrader broker boundary enforcing exact deny-all")
    except PolicyError as error:
        if not drift:
            drift = str(error)

    tightening_error = ""
    try:
        apply_policy(deny_all, runner)
        if all(source_checker(item) for item in loaded.fingerprints):
            boundary_publisher(deny_all, loaded.fingerprints)
    except PolicyError as error:
        tightening_error = str(error)
    if tightening_error:
        raise PolicyError(
            "deny-all emergency tightening failed: " + tightening_error)
    if drift:
        raise PolicyError(
            "broker deny-all supervisor detected drift and reinstalled "
            "deny-all policy: " + drift)


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
    parser = argparse.ArgumentParser(
        description="apply the versioned HeptaTrader broker egress boundary")
    parser.add_argument(
        "--policy", type=Path, default=DEFAULT_POLICY,
        help="root-owned broker network policy JSON")
    parser.add_argument(
        "--identity-manifest", type=Path, default=DEFAULT_IDENTITIES,
        help="root-owned service identity manifest JSON")
    parser.add_argument(
        "--paper-identities", type=Path, default=DEFAULT_PAPER_IDENTITIES,
        help=("optional root-owned explicit per-domain PAPER identity "
              "authorization manifest"))
    actions = parser.add_mutually_exclusive_group()
    actions.add_argument(
        "--render", action="store_true",
        help="validate inputs and print the deterministic nft transaction")
    actions.add_argument(
        "--supervise", action="store_true",
        help="apply, notify readiness, monitor, and fail closed to deny-all")
    actions.add_argument(
        "--supervise-deny-all", action="store_true",
        help=("install exact deny-all, notify readiness, and monitor only "
              "that exact deny-all state"))
    actions.add_argument(
        "--check-active", action="store_true",
        help="validate that the live nft table exactly matches the inputs")
    actions.add_argument(
        "--check-deny-all", action="store_true",
        help="validate that the live nft table has no broker authority")
    actions.add_argument(
        "--read-current-boundary", action="store_true",
        help=("read the fresh root-owned supervisor boundary receipt; this "
              "does not replace a direct live-table check"))
    actions.add_argument(
        "--tighten-fixed-only", action="store_true",
        help="atomically replace the live table with fixed-only authority")
    actions.add_argument(
        "--tighten-deny-all", action="store_true",
        help="atomically replace the live table with no broker authority")
    actions.add_argument(
        "--activate-paper-domain", action="store_true",
        help="atomically activate the one manifest-bound PAPER domain")
    parser.add_argument(
        "--domain",
        help="canonical domain required by --activate-paper-domain")
    arguments = parser.parse_args(argv)
    policy: Optional[BrokerNetworkPolicy] = None
    try:
        if arguments.read_current_boundary:
            if arguments.domain is not None:
                raise PolicyError(
                    "--domain is invalid with --read-current-boundary")
            if os.geteuid() != 0 or os.getegid() != 0:
                raise PolicyError("reading the broker boundary requires root")
            receipt = load_current_boundary_receipt()
            sys.stdout.buffer.write(receipt.payload)
            return 0
        if (
                not arguments.activate_paper_domain and
                arguments.domain is not None):
            raise PolicyError(
                "--domain is valid only with --activate-paper-domain")
        if arguments.tighten_fixed_only:
            policy = load_fixed_only_policy(
                arguments.policy,
                arguments.identity_manifest,
                require_installed_metadata=True)
            loaded: Optional[LoadedPolicy] = None
        elif arguments.tighten_deny_all or arguments.check_deny_all:
            policy = load_deny_all_policy(
                arguments.policy,
                arguments.identity_manifest,
                require_installed_metadata=True)
            loaded = None
        else:
            loaded = load_policy_bundle(
                arguments.policy,
                arguments.identity_manifest,
                arguments.paper_identities,
                require_installed_metadata=not arguments.render,
                require_explicit_deny_all_authorization=(
                    arguments.supervise_deny_all))
            policy = (
                loaded.deny_all
                if arguments.supervise_deny_all else loaded.policy)
            if arguments.activate_paper_domain:
                connectors = policy.authorized_connectors
                if (
                        len(connectors) != 1 or
                        connectors[0].domain_id == "default" or
                        arguments.domain != connectors[0].domain_id):
                    raise PolicyError(
                        "domain activation is not bound to the sole "
                        "templated PAPER identity")
        if arguments.render:
            sys.stdout.buffer.write(render_transaction(policy))
            return 0
        if os.geteuid() != 0 or os.getegid() != 0:
            raise PolicyError("applying the broker policy requires root")
        validate_os_identities(policy)
        binary = _trusted_nft_binary()

        def runner(
                nft_arguments: Sequence[str],
                standard_input: Optional[bytes],
        ) -> subprocess.CompletedProcess[bytes]:
            return _run_nft(binary, nft_arguments, standard_input)

        publisher = BoundaryReceiptPublisher()

        def publish_boundary(
                observed_policy: BrokerNetworkPolicy,
                fingerprints: tuple[SourceFingerprint, ...]) -> None:
            publisher.publish(observed_policy, fingerprints)

        if arguments.check_active or arguments.check_deny_all:
            verify_active_policy(policy, runner)
        elif (
                arguments.tighten_fixed_only or
                arguments.tighten_deny_all or
                arguments.activate_paper_domain):
            publication_loaded = loaded
            if publication_loaded is None:
                publication_loaded = load_policy_bundle(
                    arguments.policy, arguments.identity_manifest,
                    arguments.paper_identities,
                    require_installed_metadata=True)
            if policy.authorized_connectors:
                apply_authorizing_policy_guarded(
                    policy, publication_loaded.fingerprints, runner,
                    publisher,
                    pre_activation_policy=publication_loaded.deny_all)
            else:
                apply_policy(policy, runner)
                publish_boundary(policy, publication_loaded.fingerprints)
        elif arguments.supervise or arguments.supervise_deny_all:
            if loaded is None:
                raise PolicyError("supervisor policy bundle is unavailable")
            notifier = SystemdNotifier(required=True)
            stop_event = threading.Event()
            previous_handlers = _install_stop_handlers(stop_event)
            try:
                if arguments.supervise_deny_all:
                    supervise_deny_all_policy(
                        loaded, runner, notifier, stop_event,
                        poll_interval=_watchdog_poll_interval(),
                        boundary_publisher=publish_boundary)
                else:
                    # A templated local/P1 start arrives with one exact
                    # activation reservation and permit.  Consume that
                    # handoff in this same long-lived root process before it
                    # announces READY; starting in DENY_ALL and merely
                    # supervising both candidates would never commit ACTIVE.
                    initial = policy
                    if policy.authorized_connectors:
                        apply_authorizing_policy_guarded(
                            policy, loaded.fingerprints, runner, publisher,
                            pre_activation_policy=loaded.deny_all)
                    else:
                        apply_policy(policy, runner)
                    supervise_policy(
                        loaded, runner, notifier, stop_event,
                        poll_interval=_watchdog_poll_interval(),
                        boundary_publisher=publish_boundary)
            finally:
                _restore_stop_handlers(previous_handlers)
            policy = loaded.deny_all
        else:
            if policy.authorized_connectors:
                if loaded is None:
                    raise PolicyError(
                        "authorizing policy source bundle is unavailable")
                apply_authorizing_policy_guarded(
                    policy, loaded.fingerprints, runner, publisher,
                    pre_activation_policy=loaded.deny_all)
            else:
                apply_policy(policy, runner)
    except (PolicyError, OSError, ValueError) as error:
        print(f"hepta_broker_egress_policy: FAIL: {error}", file=sys.stderr)
        return 1
    if policy is None:
        print(
            "hepta_broker_egress_policy: FAIL: "
            "validated policy is unavailable",
            file=sys.stderr)
        return 1
    print(
        "hepta_broker_egress_policy: PASS "
        f"policy_sha256={policy.source_sha256} "
        f"authorized_connectors={len(policy.authorized_connectors)} "
        f"authorized_uids={','.join(str(uid) for uid in policy.authorized_uids)} "
        "protected_ports=4")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
