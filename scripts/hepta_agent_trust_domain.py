#!/usr/bin/env python3
"""Strict loader for root-owned Agent trust-domain runtime configuration."""

from __future__ import annotations

from dataclasses import dataclass
import json
import os
from pathlib import Path, PurePosixPath
import re
import stat
from typing import Any


SCHEMA = "hepta.agent-trust-domain-runtime.v1"
DOMAIN_ID = re.compile(r"^[a-z][a-z0-9-]{0,31}$")
IDENTITY = re.compile(r"^[a-z_][a-z0-9_-]{0,31}$")
CONFIG_ROOT = Path("/etc/heptatrader/trust-domains")
ALPHA_GATEWAY_PROFILE_PATH = CONFIG_ROOT / "alpha.env"
ALPHA_GATEWAY_SOCKET_PATH = Path("/run/hepta-agent-alpha/tools.sock")
ALPHA_GATEWAY_PROFILE_ITEMS = (
    ("HEPTA_EXECUTION_REMOTE_MODE", "SIMULATOR"),
    ("HEPTA_EXECUTION_SOCKET", "/run/hepta-execution-alpha/execution.sock"),
    ("HEPTA_EXECUTION_EVENT_SOCKET",
     "/run/hepta-execution-alpha/events.sock"),
    ("HEPTA_EXECUTION_SERVICE_UID", "2111"),
    ("HEPTA_EXECUTION_IO_TIMEOUT_MS", "2500"),
    ("HEPTA_EXECUTION_MAX_RESPONSE_BYTES", "32768"),
    ("HEPTA_TOOL_ACCOUNT", "SIM"),
    ("HEPTA_EXECUTION_DOMAIN_ID", "SIM:alpha"),
    ("HEPTA_TOOL_ALLOW_TRADE", "0"),
    ("HEPTA_TOOL_SESSION_TEMPLATES", "watch"),
    ("HEPTA_TOOL_CONTRACT_BINDINGS", "EUR.USD|EUR|CASH|IDEALPRO|USD"),
    ("HEPTA_TOOL_AGENT_UID", "2104"),
    ("HEPTA_TOOL_SUPERVISOR_UID", "0"),
    ("HEPTA_TOOL_SUPERVISOR_MAX_TTL_SEC", "86400"),
    ("HEPTA_TOOL_SERVER_WORKERS", "4"),
    ("HEPTA_TOOL_SERVER_MAX_PENDING", "32"),
    ("HEPTA_TOOL_SERVER_MAX_CONCURRENT_PER_OWNER", "1"),
    ("HEPTA_TOOL_SERVER_MAX_PENDING_PER_OWNER", "8"),
    ("HEPTA_TOOL_SERVER_INGRESS_WORKERS", "2"),
)
ALPHA_GATEWAY_PROFILE = dict(ALPHA_GATEWAY_PROFILE_ITEMS)
ALPHA_GATEWAY_PROFILE_BYTES = "".join(
    f"{key}={value}\n" for key, value in ALPHA_GATEWAY_PROFILE_ITEMS
).encode("ascii")
ALPHA_GATEWAY_PROCESS_PROFILE = {
    **ALPHA_GATEWAY_PROFILE,
    "HEPTA_TOOL_SOCKET": "/run/hepta-agent-alpha/tools.sock",
    "HEPTA_TOOL_AGENT_ID": "alpha",
    "HEPTA_TOOL_SUPERVISOR_LEASE_STORE":
        "/var/lib/hepta-tool-gateway-alpha/session-leases.hsl2",
    "HEPTA_TOOL_SUPERVISOR_AUDIT_JOURNAL":
        "/var/lib/hepta-tool-gateway-alpha/session-audit.jsonl",
}
ALPHA_GATEWAY_PROCESS_PROFILE_BYTES = (
    json.dumps(
        ALPHA_GATEWAY_PROCESS_PROFILE,
        ensure_ascii=True,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    ) + "\n"
).encode("ascii")
FIELDS = {
    "schema", "version", "domain_id",
    "gateway_name", "gateway_uid", "gateway_group", "gateway_gid",
    "agent_name", "agent_uid", "agent_group", "agent_gid",
    "execution_name", "execution_uid", "execution_group", "execution_gid",
    "connect_group", "connect_group_gid",
    "socket_path", "token_directory", "supervisor_socket",
    "lease_credential_path", "gateway_state_directory",
    "execution_socket", "execution_event_socket",
    "execution_fence_credential_path", "execution_state_directory",
    "execution_gateway_uid", "execution_gateway_agent_id",
    "single_domain_compatibility", "paper_authorized", "live_authorized",
}


class TrustDomainRuntimeError(RuntimeError):
    pass


StableMetadata = tuple[int, int, int, int, int, int, int, int, int]


@dataclass(frozen=True)
class GatewayProfileRead:
    raw: bytes
    values: dict[str, str]
    metadata: StableMetadata
    parent_metadata: StableMetadata


@dataclass(frozen=True)
class GatewayProcessProfileRead:
    canonical_projection: bytes
    values: dict[str, str]
    metadata: StableMetadata
    pid_directory_metadata: StableMetadata
    starttime_ticks: int


@dataclass(frozen=True)
class GatewayProcessIdentityRead:
    pid_directory_metadata: StableMetadata
    stat_metadata: StableMetadata
    starttime_ticks: int


@dataclass(frozen=True)
class GatewaySocketRead:
    metadata: StableMetadata
    parent_metadata: StableMetadata


def _stable_metadata(metadata: os.stat_result) -> StableMetadata:
    return (
        metadata.st_dev, metadata.st_ino, metadata.st_mode,
        metadata.st_nlink, metadata.st_uid, metadata.st_gid,
        metadata.st_size, metadata.st_mtime_ns, metadata.st_ctime_ns,
    )


def _open_anchored_directory(
        path: Path, *, expected_uid: int | None,
        expected_gid: int | None) -> int:
    if not path.is_absolute():
        raise TrustDomainRuntimeError("TRUST_DOMAIN_ANCHORED_PATH_INVALID")
    flags = os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    descriptor = os.open("/", flags)
    try:
        root_metadata = os.fstat(descriptor)
        if (not stat.S_ISDIR(root_metadata.st_mode) or
                (expected_uid is not None and
                 root_metadata.st_uid != expected_uid) or
                (expected_gid is not None and
                 root_metadata.st_gid != expected_gid) or
                ((expected_uid is not None or expected_gid is not None) and
                 stat.S_IMODE(root_metadata.st_mode) & 0o022)):
            raise TrustDomainRuntimeError(
                "TRUST_DOMAIN_ANCHORED_ANCESTOR_UNSAFE")
        for component in path.parts[1:]:
            child = os.open(component, flags, dir_fd=descriptor)
            metadata = os.fstat(child)
            if (not stat.S_ISDIR(metadata.st_mode) or
                    (expected_uid is not None and
                     metadata.st_uid != expected_uid) or
                    (expected_gid is not None and
                     metadata.st_gid != expected_gid) or
                    ((expected_uid is not None or expected_gid is not None) and
                     stat.S_IMODE(metadata.st_mode) & 0o022)):
                os.close(child)
                raise TrustDomainRuntimeError(
                    "TRUST_DOMAIN_ANCHORED_ANCESTOR_UNSAFE")
            os.close(descriptor)
            descriptor = child
        return descriptor
    except BaseException:
        os.close(descriptor)
        raise


def _read_alpha_gateway_profile(
        path: Path, *, expected_uid: int, expected_gid: int,
        safe_ancestor_uid: int | None,
        safe_ancestor_gid: int | None) -> GatewayProfileRead:
    expected_path = CONFIG_ROOT / "alpha.env"
    if path != expected_path:
        raise TrustDomainRuntimeError("TRUST_DOMAIN_GATEWAY_PROFILE_PATH_UNSAFE")
    parent_fd: int | None = None
    profile_fd: int | None = None
    rebound_fd: int | None = None
    try:
        parent_fd = _open_anchored_directory(
            path.parent,
            expected_uid=safe_ancestor_uid,
            expected_gid=safe_ancestor_gid,
        )
        parent_before = os.fstat(parent_fd)
        before = os.stat(path.name, dir_fd=parent_fd, follow_symlinks=False)
        flags = os.O_RDONLY | os.O_CLOEXEC
        if hasattr(os, "O_NOFOLLOW"):
            flags |= os.O_NOFOLLOW
        profile_fd = os.open(path.name, flags, dir_fd=parent_fd)
        opened = os.fstat(profile_fd)
        if (not stat.S_ISREG(opened.st_mode) or
                opened.st_uid != expected_uid or opened.st_gid != expected_gid or
                stat.S_IMODE(opened.st_mode) != 0o644 or opened.st_nlink != 1 or
                opened.st_size != len(ALPHA_GATEWAY_PROFILE_BYTES) or
                _stable_metadata(before) != _stable_metadata(opened)):
            raise TrustDomainRuntimeError(
                "TRUST_DOMAIN_GATEWAY_PROFILE_METADATA_UNSAFE")
        remaining = opened.st_size
        chunks: list[bytes] = []
        while remaining:
            chunk = os.read(profile_fd, remaining)
            if not chunk:
                raise TrustDomainRuntimeError(
                    "TRUST_DOMAIN_GATEWAY_PROFILE_CHANGED")
            chunks.append(chunk)
            remaining -= len(chunk)
        if os.read(profile_fd, 1) != b"":
            raise TrustDomainRuntimeError(
                "TRUST_DOMAIN_GATEWAY_PROFILE_CHANGED")
        after = os.fstat(profile_fd)
        rebound_fd = _open_anchored_directory(
            path.parent,
            expected_uid=safe_ancestor_uid,
            expected_gid=safe_ancestor_gid,
        )
        parent_after = os.fstat(rebound_fd)
        final = os.stat(path.name, dir_fd=rebound_fd, follow_symlinks=False)
        if (not (_stable_metadata(opened) == _stable_metadata(after) ==
                 _stable_metadata(final)) or
                _stable_metadata(parent_before) !=
                _stable_metadata(parent_after)):
            raise TrustDomainRuntimeError(
                "TRUST_DOMAIN_GATEWAY_PROFILE_CHANGED")
        contents = b"".join(chunks)
        if contents != ALPHA_GATEWAY_PROFILE_BYTES:
            raise TrustDomainRuntimeError(
                "TRUST_DOMAIN_GATEWAY_PROFILE_CONTENT_INVALID")
        return GatewayProfileRead(
            raw=contents,
            values=dict(ALPHA_GATEWAY_PROFILE),
            metadata=_stable_metadata(opened),
            parent_metadata=_stable_metadata(parent_before),
        )
    except TrustDomainRuntimeError:
        raise
    except OSError as error:
        raise TrustDomainRuntimeError(
            "TRUST_DOMAIN_GATEWAY_PROFILE_READ_FAILED") from error
    finally:
        for descriptor in (rebound_fd, profile_fd, parent_fd):
            if descriptor is not None:
                os.close(descriptor)


def read_alpha_gateway_profile(path: Path) -> GatewayProfileRead:
    return _read_alpha_gateway_profile(
        path,
        expected_uid=0,
        expected_gid=0,
        safe_ancestor_uid=0,
        safe_ancestor_gid=0,
    )


def _parse_alpha_gateway_process_environment(
        contents: bytes) -> dict[str, str]:
    if (not contents or len(contents) > 65_536 or
            not contents.endswith(b"\0")):
        raise TrustDomainRuntimeError(
            "TRUST_DOMAIN_GATEWAY_PROCESS_ENVIRONMENT_INVALID")
    hepta: dict[str, str] = {}
    try:
        entries = contents.split(b"\0")
        if entries[-1] != b"":
            raise UnicodeError
        for raw in entries[:-1]:
            key_bytes, separator, value_bytes = raw.partition(b"=")
            if not key_bytes.startswith(b"HEPTA_"):
                continue
            if separator != b"=":
                raise UnicodeError
            key = key_bytes.decode("ascii", errors="strict")
            value = value_bytes.decode("ascii", errors="strict")
            if key in hepta:
                raise UnicodeError
            hepta[key] = value
    except UnicodeError as error:
        raise TrustDomainRuntimeError(
            "TRUST_DOMAIN_GATEWAY_PROCESS_ENVIRONMENT_INVALID") from error
    if hepta != ALPHA_GATEWAY_PROCESS_PROFILE:
        raise TrustDomainRuntimeError(
            "TRUST_DOMAIN_GATEWAY_PROCESS_PROFILE_MISMATCH")
    return hepta


def _read_proc_entry(
        directory_fd: int, name: str, *, expected_uid: int,
        expected_gid: int, expected_mode: int,
        maximum_bytes: int) -> tuple[bytes, StableMetadata]:
    descriptor: int | None = None
    try:
        before = os.stat(name, dir_fd=directory_fd, follow_symlinks=False)
        flags = os.O_RDONLY | os.O_CLOEXEC
        if hasattr(os, "O_NOFOLLOW"):
            flags |= os.O_NOFOLLOW
        descriptor = os.open(name, flags, dir_fd=directory_fd)
        opened = os.fstat(descriptor)
        if (not stat.S_ISREG(opened.st_mode) or opened.st_nlink != 1 or
                opened.st_uid != expected_uid or opened.st_gid != expected_gid or
                stat.S_IMODE(opened.st_mode) != expected_mode or
                opened.st_size != 0 or
                _stable_metadata(before) != _stable_metadata(opened)):
            raise TrustDomainRuntimeError(
                "TRUST_DOMAIN_GATEWAY_PROCESS_METADATA_UNSAFE")
        contents = bytearray()
        while len(contents) <= maximum_bytes:
            chunk = os.read(
                descriptor, min(8192, maximum_bytes + 1 - len(contents)))
            if not chunk:
                break
            contents.extend(chunk)
        after = os.fstat(descriptor)
        final = os.stat(name, dir_fd=directory_fd, follow_symlinks=False)
        if (len(contents) > maximum_bytes or
                not (_stable_metadata(opened) == _stable_metadata(after) ==
                     _stable_metadata(final))):
            raise TrustDomainRuntimeError(
                "TRUST_DOMAIN_GATEWAY_PROCESS_CHANGED")
        return bytes(contents), _stable_metadata(opened)
    finally:
        if descriptor is not None:
            os.close(descriptor)


def _parse_proc_starttime(contents: bytes, pid: int) -> int:
    try:
        prefix = str(pid).encode("ascii") + b" ("
        if not contents.startswith(prefix) or not contents.endswith(b"\n"):
            raise ValueError
        close = contents.rfind(b") ")
        if close < len(prefix):
            raise ValueError
        fields = contents[close + 2:-1].split(b" ")
        if len(fields) < 20 or not fields[19].isdigit():
            raise ValueError
        starttime = int(fields[19])
        if starttime <= 0:
            raise ValueError
        return starttime
    except (UnicodeError, ValueError) as error:
        raise TrustDomainRuntimeError(
            "TRUST_DOMAIN_GATEWAY_PROCESS_STAT_INVALID") from error


def _open_gateway_pid_directory(
        proc_fd: int, pid: int, *, expected_uid: int,
        expected_gid: int) -> tuple[int, StableMetadata]:
    flags = os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    descriptor = os.open(str(pid), flags, dir_fd=proc_fd)
    metadata = os.fstat(descriptor)
    if (not stat.S_ISDIR(metadata.st_mode) or
            metadata.st_uid != expected_uid or
            metadata.st_gid != expected_gid or
            stat.S_IMODE(metadata.st_mode) != 0o555 or
            metadata.st_nlink < 2 or metadata.st_size != 0):
        os.close(descriptor)
        raise TrustDomainRuntimeError(
            "TRUST_DOMAIN_GATEWAY_PROCESS_METADATA_UNSAFE")
    return descriptor, _stable_metadata(metadata)


def _read_alpha_gateway_process_profile(
        pid: int, *, expected_uid: int,
        expected_gid: int) -> GatewayProcessProfileRead:
    if type(pid) is not int or pid <= 1:
        raise TrustDomainRuntimeError("TRUST_DOMAIN_GATEWAY_PROCESS_INVALID")
    proc_fd: int | None = None
    pid_fd: int | None = None
    rebound_pid_fd: int | None = None
    try:
        proc_fd = _open_anchored_directory(
            Path("/proc"), expected_uid=0, expected_gid=0)
        pid_fd, pid_before = _open_gateway_pid_directory(
            proc_fd, pid,
            expected_uid=expected_uid,
            expected_gid=expected_gid,
        )
        stat_contents, _stat_metadata = _read_proc_entry(
            pid_fd, "stat",
            expected_uid=expected_uid,
            expected_gid=expected_gid,
            expected_mode=0o444,
            maximum_bytes=4096,
        )
        starttime = _parse_proc_starttime(stat_contents, pid)
        environ_contents, environ_metadata = _read_proc_entry(
            pid_fd, "environ",
            expected_uid=expected_uid,
            expected_gid=expected_gid,
            expected_mode=0o400,
            maximum_bytes=65_536,
        )
        rebound_pid_fd, pid_after = _open_gateway_pid_directory(
            proc_fd, pid,
            expected_uid=expected_uid,
            expected_gid=expected_gid,
        )
        rebound_stat, _ = _read_proc_entry(
            rebound_pid_fd, "stat",
            expected_uid=expected_uid,
            expected_gid=expected_gid,
            expected_mode=0o444,
            maximum_bytes=4096,
        )
        if (pid_before != pid_after or
                starttime != _parse_proc_starttime(rebound_stat, pid)):
            raise TrustDomainRuntimeError(
                "TRUST_DOMAIN_GATEWAY_PROCESS_CHANGED")
        values = _parse_alpha_gateway_process_environment(environ_contents)
        return GatewayProcessProfileRead(
            canonical_projection=ALPHA_GATEWAY_PROCESS_PROFILE_BYTES,
            values=values,
            metadata=environ_metadata,
            pid_directory_metadata=pid_before,
            starttime_ticks=starttime,
        )
    except TrustDomainRuntimeError:
        raise
    except OSError as error:
        raise TrustDomainRuntimeError(
            "TRUST_DOMAIN_GATEWAY_PROCESS_READ_FAILED") from error
    finally:
        for descriptor in (rebound_pid_fd, pid_fd, proc_fd):
            if descriptor is not None:
                os.close(descriptor)


def read_alpha_gateway_process_profile(
        pid: int) -> GatewayProcessProfileRead:
    return _read_alpha_gateway_process_profile(
        pid, expected_uid=2101, expected_gid=2101)


def _read_alpha_gateway_process_identity(
        pid: int, *, expected_uid: int,
        expected_gid: int) -> GatewayProcessIdentityRead:
    if type(pid) is not int or pid <= 1:
        raise TrustDomainRuntimeError("TRUST_DOMAIN_GATEWAY_PROCESS_INVALID")
    proc_fd: int | None = None
    pid_fd: int | None = None
    rebound_pid_fd: int | None = None
    try:
        proc_fd = _open_anchored_directory(
            Path("/proc"), expected_uid=0, expected_gid=0)
        pid_fd, pid_before = _open_gateway_pid_directory(
            proc_fd, pid,
            expected_uid=expected_uid,
            expected_gid=expected_gid,
        )
        contents, stat_metadata = _read_proc_entry(
            pid_fd, "stat",
            expected_uid=expected_uid,
            expected_gid=expected_gid,
            expected_mode=0o444,
            maximum_bytes=4096,
        )
        starttime = _parse_proc_starttime(contents, pid)
        rebound_pid_fd, pid_after = _open_gateway_pid_directory(
            proc_fd, pid,
            expected_uid=expected_uid,
            expected_gid=expected_gid,
        )
        rebound_contents, _ = _read_proc_entry(
            rebound_pid_fd, "stat",
            expected_uid=expected_uid,
            expected_gid=expected_gid,
            expected_mode=0o444,
            maximum_bytes=4096,
        )
        if (pid_before != pid_after or
                starttime != _parse_proc_starttime(rebound_contents, pid)):
            raise TrustDomainRuntimeError(
                "TRUST_DOMAIN_GATEWAY_PROCESS_CHANGED")
        return GatewayProcessIdentityRead(
            pid_directory_metadata=pid_before,
            stat_metadata=stat_metadata,
            starttime_ticks=starttime,
        )
    except TrustDomainRuntimeError:
        raise
    except OSError as error:
        raise TrustDomainRuntimeError(
            "TRUST_DOMAIN_GATEWAY_PROCESS_READ_FAILED") from error
    finally:
        for descriptor in (rebound_pid_fd, pid_fd, proc_fd):
            if descriptor is not None:
                os.close(descriptor)


def read_alpha_gateway_process_identity(
        pid: int) -> GatewayProcessIdentityRead:
    return _read_alpha_gateway_process_identity(
        pid, expected_uid=2101, expected_gid=2101)


def _read_alpha_gateway_socket(
        path: Path, *, expected_uid: int,
        expected_gid: int) -> GatewaySocketRead:
    if path != ALPHA_GATEWAY_SOCKET_PATH:
        raise TrustDomainRuntimeError("TRUST_DOMAIN_GATEWAY_SOCKET_PATH_UNSAFE")
    parent_fd: int | None = None
    socket_fd: int | None = None
    rebound_fd: int | None = None
    try:
        parent_fd = _open_anchored_directory(
            path.parent, expected_uid=0, expected_gid=0)
        parent_before = os.fstat(parent_fd)
        before = os.stat(path.name, dir_fd=parent_fd, follow_symlinks=False)
        flags = getattr(os, "O_PATH", os.O_RDONLY) | os.O_CLOEXEC
        if hasattr(os, "O_NOFOLLOW"):
            flags |= os.O_NOFOLLOW
        socket_fd = os.open(path.name, flags, dir_fd=parent_fd)
        opened = os.fstat(socket_fd)
        if (not stat.S_ISSOCK(opened.st_mode) or opened.st_nlink != 1 or
                opened.st_uid != expected_uid or opened.st_gid != expected_gid or
                stat.S_IMODE(opened.st_mode) != 0o600 or
                opened.st_size != 0 or
                _stable_metadata(before) != _stable_metadata(opened)):
            raise TrustDomainRuntimeError(
                "TRUST_DOMAIN_GATEWAY_SOCKET_METADATA_UNSAFE")
        rebound_fd = _open_anchored_directory(
            path.parent, expected_uid=0, expected_gid=0)
        parent_after = os.fstat(rebound_fd)
        final = os.stat(path.name, dir_fd=rebound_fd, follow_symlinks=False)
        if (_stable_metadata(opened) != _stable_metadata(final) or
                _stable_metadata(parent_before) !=
                _stable_metadata(parent_after)):
            raise TrustDomainRuntimeError("TRUST_DOMAIN_GATEWAY_SOCKET_CHANGED")
        return GatewaySocketRead(
            metadata=_stable_metadata(opened),
            parent_metadata=_stable_metadata(parent_before),
        )
    except TrustDomainRuntimeError:
        raise
    except OSError as error:
        raise TrustDomainRuntimeError(
            "TRUST_DOMAIN_GATEWAY_SOCKET_READ_FAILED") from error
    finally:
        for descriptor in (rebound_fd, socket_fd, parent_fd):
            if descriptor is not None:
                os.close(descriptor)


def read_alpha_gateway_socket(
        path: Path = ALPHA_GATEWAY_SOCKET_PATH) -> GatewaySocketRead:
    return _read_alpha_gateway_socket(
        path, expected_uid=2104, expected_gid=2104)


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise TrustDomainRuntimeError("TRUST_DOMAIN_DUPLICATE_JSON_KEY")
        result[key] = value
    return result


def _runtime_path(value: Any, label: str, *, socket: bool = False) -> str:
    if not isinstance(value, str) or "\\" in value or "\0" in value:
        raise TrustDomainRuntimeError(f"TRUST_DOMAIN_{label}_INVALID")
    path = PurePosixPath(value)
    if (not path.is_absolute() or path.as_posix() != value or
            any(part in {"", ".", ".."} for part in path.parts) or
            not value.startswith("/run/hepta-")):
        raise TrustDomainRuntimeError(f"TRUST_DOMAIN_{label}_INVALID")
    if socket and len(value.encode("utf-8")) > 107:
        raise TrustDomainRuntimeError(f"TRUST_DOMAIN_{label}_INVALID")
    return value


def _fixed_path(value: Any, label: str, prefix: str) -> str:
    if not isinstance(value, str) or "\\" in value or "\0" in value:
        raise TrustDomainRuntimeError(f"TRUST_DOMAIN_{label}_INVALID")
    path = PurePosixPath(value)
    if (not path.is_absolute() or path.as_posix() != value or
            any(part in {"", ".", ".."} for part in path.parts) or
            not value.startswith(prefix)):
        raise TrustDomainRuntimeError(f"TRUST_DOMAIN_{label}_INVALID")
    return value


def _identity_number(value: Any, label: str) -> int:
    if (isinstance(value, bool) or not isinstance(value, int) or
            value < 1 or value > 4_294_967_295):
        raise TrustDomainRuntimeError(f"TRUST_DOMAIN_{label}_INVALID")
    return value


def _validate_config_ancestors() -> None:
    for directory in (CONFIG_ROOT.parent, CONFIG_ROOT):
        metadata = directory.lstat()
        if (
                stat.S_ISLNK(metadata.st_mode) or
                not stat.S_ISDIR(metadata.st_mode) or
                metadata.st_uid != 0 or metadata.st_gid != 0 or
                stat.S_IMODE(metadata.st_mode) & 0o022):
            raise TrustDomainRuntimeError(
                "TRUST_DOMAIN_CONFIG_ANCESTOR_UNSAFE")


def _validate_metadata(
        path: Path, *, expected_gid: int, expected_mode: int,
) -> bytes:
    before = path.lstat()
    if (stat.S_ISLNK(before.st_mode) or not stat.S_ISREG(before.st_mode) or
            before.st_uid != 0 or before.st_gid != expected_gid or
            before.st_nlink != 1 or
            stat.S_IMODE(before.st_mode) != expected_mode or
            before.st_size < 2 or before.st_size > 65_536):
        raise TrustDomainRuntimeError("TRUST_DOMAIN_CONFIG_METADATA_UNSAFE")
    descriptor = os.open(
        path, os.O_RDONLY | os.O_CLOEXEC | getattr(os, "O_NOFOLLOW", 0))
    try:
        opened = os.fstat(descriptor)
        data = bytearray()
        while len(data) <= 65_536:
            chunk = os.read(descriptor, 8192)
            if not chunk:
                break
            data.extend(chunk)
        after = os.fstat(descriptor)
    finally:
        os.close(descriptor)
    identity = lambda item: (
        item.st_dev, item.st_ino, item.st_mode, item.st_nlink,
        item.st_uid, item.st_gid, item.st_size,
        item.st_mtime_ns, item.st_ctime_ns,
    )
    if (identity(before) != identity(opened) or
            identity(opened) != identity(after) or len(data) != before.st_size):
        raise TrustDomainRuntimeError("TRUST_DOMAIN_CONFIG_CHANGED")
    return bytes(data)


def load_runtime_config(
        path: Path, *, require_root_metadata: bool = True,
        expected_agent_identity: tuple[int, int] | None = None,
) -> dict[str, Any]:
    logical = path.absolute()
    logical_metadata = logical.lstat()
    if stat.S_ISLNK(logical_metadata.st_mode):
        raise TrustDomainRuntimeError("TRUST_DOMAIN_CONFIG_METADATA_UNSAFE")
    path = logical.resolve(strict=True)
    if path != logical:
        raise TrustDomainRuntimeError("TRUST_DOMAIN_CONFIG_PATH_FORBIDDEN")
    if require_root_metadata:
        try:
            path.relative_to(CONFIG_ROOT)
        except ValueError as error:
            raise TrustDomainRuntimeError(
                "TRUST_DOMAIN_CONFIG_PATH_FORBIDDEN") from error
        _validate_config_ancestors()
        if expected_agent_identity is None:
            if path.name.startswith("uid-"):
                raise TrustDomainRuntimeError(
                    "TRUST_DOMAIN_CONFIG_PROFILE_FORBIDDEN")
            data = _validate_metadata(
                path, expected_gid=0, expected_mode=0o600)
        else:
            expected_uid, expected_gid = expected_agent_identity
            _identity_number(expected_uid, "EXPECTED_AGENT_UID")
            _identity_number(expected_gid, "EXPECTED_AGENT_GID")
            if path != CONFIG_ROOT / f"uid-{expected_uid}.json":
                raise TrustDomainRuntimeError(
                    "TRUST_DOMAIN_CONFIG_PROFILE_FORBIDDEN")
            data = _validate_metadata(
                path, expected_gid=expected_gid, expected_mode=0o640)
    else:
        metadata = path.lstat()
        if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISREG(metadata.st_mode):
            raise TrustDomainRuntimeError("TRUST_DOMAIN_CONFIG_METADATA_UNSAFE")
        data = path.read_bytes()
    try:
        document = json.loads(
            data.decode("utf-8", errors="strict"),
            object_pairs_hook=_unique_object)
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise TrustDomainRuntimeError(
            "TRUST_DOMAIN_CONFIG_INVALID_JSON") from error
    if not isinstance(document, dict) or set(document) != FIELDS:
        raise TrustDomainRuntimeError("TRUST_DOMAIN_CONFIG_FIELDS_INVALID")
    if document["schema"] != SCHEMA or document["version"] != 1:
        raise TrustDomainRuntimeError("TRUST_DOMAIN_CONFIG_VERSION_UNSUPPORTED")
    domain_id = document["domain_id"]
    if not isinstance(domain_id, str) or DOMAIN_ID.fullmatch(domain_id) is None:
        raise TrustDomainRuntimeError("TRUST_DOMAIN_ID_INVALID")
    for field in (
            "gateway_name", "gateway_group", "agent_name", "agent_group",
            "execution_name", "execution_group", "connect_group"):
        value = document[field]
        if not isinstance(value, str) or IDENTITY.fullmatch(value) is None:
            raise TrustDomainRuntimeError(
                f"TRUST_DOMAIN_{field.upper()}_INVALID")
    for field in (
            "gateway_uid", "gateway_gid", "agent_uid", "agent_gid",
            "execution_uid", "execution_gid", "connect_group_gid",
            "execution_gateway_uid"):
        _identity_number(document[field], field.upper())
    if (
            document["gateway_name"] != f"hepta-gw-{domain_id}" or
            document["gateway_group"] != f"hepta-gw-{domain_id}" or
            document["agent_name"] != f"hepta-agent-{domain_id}" or
            document["agent_group"] != f"hepta-agent-{domain_id}" or
            document["execution_name"] != f"hepta-exec-{domain_id}" or
            document["execution_group"] != f"hepta-exec-{domain_id}"):
        raise TrustDomainRuntimeError("TRUST_DOMAIN_IDENTITY_NAME_DRIFT")
    if (
            document["connect_group"] != document["gateway_group"] or
            document["connect_group_gid"] != document["gateway_gid"]):
        raise TrustDomainRuntimeError("TRUST_DOMAIN_CONNECT_GROUP_DRIFT")
    if (
            len({
                document["gateway_uid"], document["agent_uid"],
                document["execution_uid"],
            }) != 3 or
            len({
                document["gateway_gid"], document["agent_gid"],
                document["execution_gid"],
            }) != 3):
        raise TrustDomainRuntimeError("TRUST_DOMAIN_IDENTITY_REUSE")
    socket_path = _runtime_path(
        document["socket_path"], "SOCKET_PATH", socket=True)
    token_directory = _runtime_path(
        document["token_directory"], "TOKEN_DIRECTORY")
    supervisor_socket = _runtime_path(
        document["supervisor_socket"], "SUPERVISOR_SOCKET", socket=True)
    execution_socket = _runtime_path(
        document["execution_socket"], "EXECUTION_SOCKET", socket=True)
    execution_event_socket = _runtime_path(
        document["execution_event_socket"], "EXECUTION_EVENT_SOCKET",
        socket=True)
    execution_fence_credential_path = _fixed_path(
        document["execution_fence_credential_path"],
        "EXECUTION_FENCE_CREDENTIAL_PATH",
        "/etc/heptatrader/credentials/trust-domains/")
    lease_credential_path = _fixed_path(
        document["lease_credential_path"], "LEASE_CREDENTIAL_PATH",
        "/etc/heptatrader/credentials/trust-domains/")
    gateway_state_directory = _fixed_path(
        document["gateway_state_directory"], "GATEWAY_STATE_DIRECTORY",
        "/var/lib/hepta-tool-gateway-")
    execution_state_directory = _fixed_path(
        document["execution_state_directory"], "EXECUTION_STATE_DIRECTORY",
        "/var/lib/hepta-execution-")
    execution_gateway_agent_id = document["execution_gateway_agent_id"]
    if (
            not isinstance(execution_gateway_agent_id, str) or
            DOMAIN_ID.fullmatch(execution_gateway_agent_id) is None):
        raise TrustDomainRuntimeError(
            "TRUST_DOMAIN_EXECUTION_GATEWAY_AGENT_ID_INVALID")
    if document["single_domain_compatibility"] is not False:
        raise TrustDomainRuntimeError("TRUST_DOMAIN_COMPATIBILITY_FORBIDDEN")
    if (document["paper_authorized"] is not False or
            document["live_authorized"] is not False):
        raise TrustDomainRuntimeError("TRUST_DOMAIN_TRADING_AUTHORITY_FORBIDDEN")
    expected_root = f"/run/hepta-agent-{domain_id}"
    if (socket_path != expected_root + "/tools.sock" or
            token_directory != expected_root + "/sessions" or
            supervisor_socket !=
            f"/run/hepta-tool-gateway-{domain_id}/session-supervisor.sock" or
            lease_credential_path !=
            f"/etc/heptatrader/credentials/trust-domains/{domain_id}/"
            "hepta-supervisor-lease.key" or
            gateway_state_directory !=
            f"/var/lib/hepta-tool-gateway-{domain_id}" or
            execution_socket !=
            f"/run/hepta-execution-{domain_id}/execution.sock" or
            execution_event_socket !=
            f"/run/hepta-execution-{domain_id}/events.sock" or
            execution_fence_credential_path !=
            f"/etc/heptatrader/credentials/trust-domains/{domain_id}/"
            "hepta-execution-simulator-fence" or
            execution_state_directory !=
            f"/var/lib/hepta-execution-{domain_id}"):
        raise TrustDomainRuntimeError("TRUST_DOMAIN_RUNTIME_PATH_DRIFT")
    if (
            document["execution_gateway_uid"] != document["gateway_uid"] or
            execution_gateway_agent_id != domain_id):
        raise TrustDomainRuntimeError("TRUST_DOMAIN_EXECUTION_BINDING_DRIFT")
    if require_root_metadata:
        if expected_agent_identity is None:
            if path != CONFIG_ROOT / f"{domain_id}.json":
                raise TrustDomainRuntimeError(
                    "TRUST_DOMAIN_CONFIG_PROFILE_FORBIDDEN")
        elif (
                document["agent_uid"], document["agent_gid"]
        ) != expected_agent_identity:
            raise TrustDomainRuntimeError(
                "TRUST_DOMAIN_CONFIG_AGENT_IDENTITY_MISMATCH")
    return document
