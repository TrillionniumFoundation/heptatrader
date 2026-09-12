#!/usr/bin/env python3
"""Read-only release and host preflight for HeptaTrader.

A PASS receipt proves only the checks recorded in that receipt. It never grants
PAPER or LIVE authority and never creates credentials, sessions, firewall
rules, kill switches, users, directories, or Broker orders.
"""
from __future__ import annotations

import argparse
import contextlib
import fcntl
from dataclasses import dataclass
from datetime import datetime, timezone
import gzip
import hashlib
import ipaddress
import json
import os
from pathlib import Path, PurePosixPath
import platform
import pwd
import re
import secrets
import shutil
import socket
import stat
import sys
import tempfile
from typing import Any, BinaryIO

POLICY_SCHEMA = "heptatrader.preflight-policy.v1"
MANIFEST_SCHEMA = "heptatrader.release-manifest.v1"
RECEIPT_SCHEMA = "heptatrader.preflight-receipt.v1"
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
SOURCE_SHA_RE = re.compile(r"^[0-9a-f]{40}$")
RELEASE_LABEL_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")
HARD_MAXIMUM_ARCHIVE_MEMBERS = 4096
HARD_MAXIMUM_MEMBER_BYTES = 512 * 1024 * 1024
HARD_MAXIMUM_TOTAL_UNPACKED_BYTES = 2 * 1024 * 1024 * 1024
TAR_BLOCK_BYTES = 512
TAR_RECORD_BYTES = 20 * TAR_BLOCK_BYTES
HARD_MAXIMUM_EXTENSION_METADATA_BYTES = 0
GENERATED_ARCHIVE_PATHS = frozenset({"manifest.json"})
HARD_MAXIMUM_COMPRESSED_ARCHIVE_BYTES = (
    HARD_MAXIMUM_TOTAL_UNPACKED_BYTES
    + HARD_MAXIMUM_ARCHIVE_MEMBERS * 1024
    + 32 * 1024 * 1024
)
HARD_PRIVATE_KEY_SUFFIXES = frozenset({".key", ".pem", ".p12", ".pfx", ".jks"})
HARD_ALLOWED_BROKER_HOSTS = frozenset({"127.0.0.1", "::1"})
HARD_ALLOWED_BROKER_PORTS = frozenset({4002, 7497})
CANONICAL_IB_PAPER_KILL_SWITCH_PATH = "/run/hepta/ib-paper-control/kill-switch"
CANONICAL_IB_PAPER_KILL_SWITCH_CONTENT = b"engaged"
CANONICAL_IB_PAPER_CONTROL_DIRECTORY_MODE = 0o750
CANONICAL_IB_PAPER_KILL_SWITCH_MODE = 0o440
MANAGED_SUBTREES = (
    "libexec/heptatrader",
    "share/heptatrader",
    "share/doc/heptatrader",
)
MANAGED_PREFIX_DIRECTORIES = (
    ("bin", "hepta"),
    ("lib/systemd/system", "hepta-"),
    ("lib/tmpfiles.d", "hepta-"),
)


class PreflightError(ValueError):
    pass


class DuplicateKeyError(ValueError):
    pass


class LoadedPolicy(dict[str, Any]):
    def __init__(self, value: dict[str, Any], raw_bytes: bytes, path: Path) -> None:
        super().__init__(value)
        self.raw_bytes = raw_bytes
        self.sha256 = hashlib.sha256(raw_bytes).hexdigest()
        self.path = path


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise DuplicateKeyError(f"duplicate JSON key: {key}")
        result[key] = value
    return result


def _reject_constant(value: str) -> None:
    raise ValueError(f"non-finite JSON number: {value}")


def parse_json_bytes(value: bytes, label: str) -> Any:
    try:
        return json.loads(
            value.decode("utf-8"),
            object_pairs_hook=_unique_object,
            parse_constant=_reject_constant,
        )
    except (UnicodeError, json.JSONDecodeError, ValueError) as error:
        raise PreflightError(f"{label}: invalid strict JSON: {error}") from error


def canonical_json(value: Any) -> bytes:
    return (
        json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
        + "\n"
    ).encode("utf-8")


def _absolute_path(path: Path) -> Path:
    return Path(os.path.abspath(os.fspath(path)))


def _directory_flags() -> int:
    return (
        os.O_RDONLY
        | getattr(os, "O_DIRECTORY", 0)
        | getattr(os, "O_CLOEXEC", 0)
        | getattr(os, "O_NOFOLLOW", 0)
    )


def _file_flags() -> int:
    return (
        os.O_RDONLY
        | getattr(os, "O_CLOEXEC", 0)
        | getattr(os, "O_NOFOLLOW", 0)
        | getattr(os, "O_NONBLOCK", 0)
    )


def _file_identity(metadata: os.stat_result) -> tuple[int, ...]:
    return (
        metadata.st_dev,
        metadata.st_ino,
        metadata.st_mode,
        metadata.st_nlink,
        metadata.st_uid,
        metadata.st_gid,
        metadata.st_size,
        metadata.st_mtime_ns,
        metadata.st_ctime_ns,
    )


def _same_file(left: os.stat_result, right: os.stat_result) -> bool:
    return _file_identity(left) == _file_identity(right)


def _same_inode(left: os.stat_result, right: os.stat_result) -> bool:
    return (
        left.st_dev,
        left.st_ino,
        stat.S_IFMT(left.st_mode),
    ) == (
        right.st_dev,
        right.st_ino,
        stat.S_IFMT(right.st_mode),
    )


def _clear_nonblocking(descriptor: int) -> None:
    nonblocking = getattr(os, "O_NONBLOCK", 0)
    if not nonblocking:
        return
    flags = fcntl.fcntl(descriptor, fcntl.F_GETFL)
    if flags & nonblocking:
        fcntl.fcntl(descriptor, fcntl.F_SETFL, flags & ~nonblocking)


def _open_directory_absolute(path: Path, label: str) -> int:
    absolute = _absolute_path(path)
    descriptor = os.open("/", _directory_flags())
    try:
        for part in absolute.parts[1:]:
            if part in {"", ".", ".."}:
                raise PreflightError(f"{label}: non-canonical path component")
            following = os.open(part, _directory_flags(), dir_fd=descriptor)
            metadata = os.fstat(following)
            if not stat.S_ISDIR(metadata.st_mode):
                os.close(following)
                raise PreflightError(f"{label}: path component is not a directory")
            os.close(descriptor)
            descriptor = following
        return descriptor
    except Exception:
        os.close(descriptor)
        raise


def _open_relative_directory(root: int, parts: tuple[str, ...], label: str) -> int:
    descriptor = os.dup(root)
    try:
        for part in parts:
            if part in {"", ".", ".."} or "/" in part or "\\" in part:
                raise PreflightError(f"{label}: non-canonical relative component")
            following = os.open(part, _directory_flags(), dir_fd=descriptor)
            metadata = os.fstat(following)
            if not stat.S_ISDIR(metadata.st_mode):
                os.close(following)
                raise PreflightError(f"{label}: component is not a directory")
            os.close(descriptor)
            descriptor = following
        return descriptor
    except Exception:
        os.close(descriptor)
        raise


@contextlib.contextmanager
def _open_pinned_regular(path: Path, label: str) -> Any:
    absolute = _absolute_path(path)
    if not absolute.name or absolute.name in {".", ".."}:
        raise PreflightError(f"{label}: regular-file path required")
    directory = _open_directory_absolute(absolute.parent, f"{label} parent")
    descriptor = -1
    stream: BinaryIO | None = None
    try:
        descriptor = os.open(absolute.name, _file_flags(), dir_fd=directory)
        pinned = os.fstat(descriptor)
        if not stat.S_ISREG(pinned.st_mode) or pinned.st_nlink != 1:
            raise PreflightError(
                f"{label} must be a stable regular non-symlink single-link file"
            )
        _clear_nonblocking(descriptor)
        current = os.stat(absolute.name, dir_fd=directory, follow_symlinks=False)
        if not _same_file(pinned, current):
            raise PreflightError(
                f"{label} must be a stable regular non-symlink single-link file"
            )
        stream = os.fdopen(descriptor, "rb", closefd=True)
        descriptor = -1
        yield stream, pinned, directory, absolute.name
    finally:
        if stream is not None:
            stream.close()
        elif descriptor >= 0:
            os.close(descriptor)
        os.close(directory)


@contextlib.contextmanager
def _open_pinned_regular_at(root: int, relative: str, label: str) -> Any:
    parts = _canonical_member_name(relative)
    if not parts:
        raise PreflightError(f"{label}: regular-file path required")
    directory = _open_relative_directory(root, tuple(parts[:-1]), f"{label} parent")
    descriptor = -1
    stream: BinaryIO | None = None
    try:
        descriptor = os.open(parts[-1], _file_flags(), dir_fd=directory)
        pinned = os.fstat(descriptor)
        if not stat.S_ISREG(pinned.st_mode) or pinned.st_nlink != 1:
            raise PreflightError(
                f"{label} must be a stable regular non-symlink single-link file"
            )
        _clear_nonblocking(descriptor)
        current = os.stat(parts[-1], dir_fd=directory, follow_symlinks=False)
        if not _same_file(pinned, current):
            raise PreflightError(
                f"{label} must be a stable regular non-symlink single-link file"
            )
        stream = os.fdopen(descriptor, "rb", closefd=True)
        descriptor = -1
        yield stream, pinned, directory, parts[-1]
    finally:
        if stream is not None:
            stream.close()
        elif descriptor >= 0:
            os.close(descriptor)
        os.close(directory)


def _assert_stable_file(
    stream: BinaryIO,
    pinned: os.stat_result,
    directory: int,
    name: str,
    label: str,
) -> None:
    try:
        current = os.stat(name, dir_fd=directory, follow_symlinks=False)
    except OSError as error:
        raise PreflightError(f"{label} path changed while being read: {error}") from error
    after = os.fstat(stream.fileno())
    if not _same_file(pinned, after) or not _same_file(pinned, current):
        raise PreflightError(f"{label} identity changed while being read")


def _hash_stream(stream: BinaryIO) -> str:
    stream.seek(0)
    digest = hashlib.sha256()
    for chunk in iter(lambda: stream.read(1024 * 1024), b""):
        digest.update(chunk)
    return digest.hexdigest()


def sha256_file(path: Path) -> str:
    with _open_pinned_regular(path, "file") as (
        stream,
        pinned,
        directory,
        name,
    ):
        digest = _hash_stream(stream)
        _assert_stable_file(stream, pinned, directory, name, "file")
        return digest


def _read_bounded(stream: BinaryIO, maximum: int, label: str) -> bytes:
    chunks: list[bytes] = []
    total = 0
    while True:
        chunk = stream.read(min(1024 * 1024, maximum - total + 1))
        if not chunk:
            break
        total += len(chunk)
        if total > maximum:
            raise PreflightError(f"{label}: exceeds size bound")
        chunks.append(chunk)
    return b"".join(chunks)


def _canonical_string_array(value: Any, label: str) -> list[str]:
    if (
        not isinstance(value, list)
        or not value
        or any(
            not isinstance(item, str)
            or not item
            or item.strip() != item
            for item in value
        )
        or value != sorted(value)
        or len(value) != len(set(value))
    ):
        raise PreflightError(f"{label} must be a sorted unique string array")
    return value


def _validate_profile_policy(profiles: dict[str, Any]) -> None:
    core = profiles.get("core")
    paper = profiles.get("ib-paper")
    if not isinstance(core, dict) or set(core) != {
        "required_package_paths",
        "required_host_commands",
    }:
        raise PreflightError("core preflight profile fields are not canonical")
    if not isinstance(paper, dict) or set(paper) != {
        "required_package_paths",
        "required_host_commands",
        "allowed_broker_hosts",
        "allowed_broker_ports",
    }:
        raise PreflightError("ib-paper preflight profile fields are not canonical")
    for name, profile in (("core", core), ("ib-paper", paper)):
        _canonical_string_array(
            profile.get("required_package_paths"),
            f"{name}.required_package_paths",
        )
        _canonical_string_array(
            profile.get("required_host_commands"),
            f"{name}.required_host_commands",
        )

    hosts = _canonical_string_array(
        paper.get("allowed_broker_hosts"),
        "ib-paper.allowed_broker_hosts",
    )
    for host in hosts:
        try:
            parsed = ipaddress.ip_address(host)
        except ValueError as error:
            raise PreflightError(
                "broker policy hosts must be literal IP addresses"
            ) from error
        if str(parsed) != host:
            raise PreflightError(
                "broker policy hosts must use canonical literal IP form"
            )
    ports = paper.get("allowed_broker_ports")
    if (
        not isinstance(ports, list)
        or not ports
        or any(
            not isinstance(port, int)
            or isinstance(port, bool)
            or port < 1
            or port > 65535
            for port in ports
        )
        or ports != sorted(ports)
        or len(ports) != len(set(ports))
    ):
        raise PreflightError(
            "ib-paper.allowed_broker_ports must be a sorted unique port array"
        )


def _load_policy(path: Path) -> LoadedPolicy:
    absolute = _absolute_path(path)
    with _open_pinned_regular(absolute, "policy") as (
        stream,
        pinned,
        directory,
        name,
    ):
        raw_bytes = _read_bounded(stream, 4 * 1024 * 1024, str(absolute))
        value = parse_json_bytes(raw_bytes, str(absolute))
        _assert_stable_file(stream, pinned, directory, name, "policy")
    expected_fields = {
        "schema",
        "maximum_archive_members",
        "maximum_member_bytes",
        "maximum_total_unpacked_bytes",
        "private_key_suffixes",
        "profiles",
    }
    if not isinstance(value, dict) or set(value) != expected_fields:
        raise PreflightError("preflight policy fields are not canonical")
    if value.get("schema") != POLICY_SCHEMA:
        raise PreflightError("unsupported preflight policy")
    ceilings = {
        "maximum_archive_members": HARD_MAXIMUM_ARCHIVE_MEMBERS,
        "maximum_member_bytes": HARD_MAXIMUM_MEMBER_BYTES,
        "maximum_total_unpacked_bytes": HARD_MAXIMUM_TOTAL_UNPACKED_BYTES,
    }
    for key, hard_maximum in ceilings.items():
        item = value.get(key)
        if (
            not isinstance(item, int)
            or isinstance(item, bool)
            or item <= 0
            or item > hard_maximum
        ):
            raise PreflightError(f"policy bound exceeds compiled ceiling: {key}")
    suffixes = value.get("private_key_suffixes")
    if (
        not isinstance(suffixes, list)
        or any(not isinstance(item, str) or not item.startswith(".") for item in suffixes)
        or not HARD_PRIVATE_KEY_SUFFIXES.issubset({item.lower() for item in suffixes})
    ):
        raise PreflightError("private-key suffix policy is invalid or weaker than compiled policy")
    profiles = value.get("profiles")
    if not isinstance(profiles, dict) or set(profiles) != {"core", "ib-paper"}:
        raise PreflightError("preflight policy profiles are not canonical")
    _validate_profile_policy(profiles)
    return LoadedPolicy(value, raw_bytes, absolute)


def _canonical_member_name(name: str) -> tuple[str, ...]:
    if not name or "\\" in name or "\x00" in name:
        raise PreflightError(f"archive contains a non-canonical path: {name!r}")
    path = PurePosixPath(name)
    if (
        path.is_absolute()
        or path.as_posix() != name
        or any(part in {"", ".", ".."} for part in path.parts)
    ):
        raise PreflightError(f"archive path escapes or is non-canonical: {name!r}")
    return path.parts


@dataclass(frozen=True)
class _CanonicalTarMember:
    name: str
    size: int
    mode: int
    uid: int
    gid: int
    mtime: int


def _tar_text(field: bytes, label: str, *, allow_empty: bool = False) -> str:
    marker = field.find(b"\0")
    if marker < 0:
        raw = field
    else:
        raw = field[:marker]
        if any(field[marker:]):
            raise PreflightError(f"{label}: non-canonical NUL padding")
    if not raw and not allow_empty:
        raise PreflightError(f"{label}: empty field")
    if any(byte < 0x20 or byte == 0x7F for byte in raw):
        raise PreflightError(f"{label}: control byte is forbidden")
    try:
        return raw.decode("utf-8", "strict")
    except UnicodeError as error:
        raise PreflightError(f"{label}: invalid UTF-8") from error


def _tar_octal(field: bytes, label: str, *, allow_empty: bool = False) -> int:
    digits = field.rstrip(b"\0 ")
    suffix = field[len(digits):]
    if any(byte not in (0, 0x20) for byte in suffix):
        raise PreflightError(f"{label}: invalid numeric terminator")
    if not digits:
        if allow_empty:
            return 0
        raise PreflightError(f"{label}: empty numeric field")
    if any(byte < ord("0") or byte > ord("7") for byte in digits):
        raise PreflightError(f"{label}: non-octal or base-256 value is forbidden")
    return int(digits, 8)


def _parse_canonical_tar_header(block: bytes) -> _CanonicalTarMember:
    if len(block) != TAR_BLOCK_BYTES:
        raise PreflightError("archive header is truncated")
    if block == b"\0" * TAR_BLOCK_BYTES:
        raise PreflightError("internal zero-header misuse")

    checksum = _tar_octal(block[148:156], "tar checksum")
    normalized = bytearray(block)
    normalized[148:156] = b" " * 8
    if sum(normalized) != checksum:
        raise PreflightError("archive header checksum mismatch")

    if block[257:263] != b"ustar\0" or block[263:265] != b"00":
        raise PreflightError(
            "archive must use canonical USTAR without extension records"
        )
    typeflag = block[156:157]
    if typeflag in {b"x", b"g", b"L", b"K"}:
        raise PreflightError(
            "archive extension metadata is forbidden; compiled limit is zero"
        )
    if typeflag not in {b"0", b"\0"}:
        raise PreflightError(
            "links, directories and special archive entries are forbidden"
        )

    name = _tar_text(block[0:100], "tar name")
    prefix = _tar_text(block[345:500], "tar prefix", allow_empty=True)
    full_name = f"{prefix}/{name}" if prefix else name
    _canonical_member_name(full_name)
    if _tar_text(block[157:257], "tar linkname", allow_empty=True):
        raise PreflightError("regular archive member has a link target")
    if _tar_text(block[265:297], "tar uname") != "root":
        raise PreflightError("archive user name is not normalized")
    if _tar_text(block[297:329], "tar gname") != "root":
        raise PreflightError("archive group name is not normalized")
    if _tar_octal(block[329:337], "tar device major", allow_empty=True) != 0:
        raise PreflightError("archive device major is nonzero")
    if _tar_octal(block[337:345], "tar device minor", allow_empty=True) != 0:
        raise PreflightError("archive device minor is nonzero")

    member = _CanonicalTarMember(
        name=full_name,
        mode=_tar_octal(block[100:108], "tar mode"),
        uid=_tar_octal(block[108:116], "tar uid"),
        gid=_tar_octal(block[116:124], "tar gid"),
        size=_tar_octal(block[124:136], "tar size"),
        mtime=_tar_octal(block[136:148], "tar mtime"),
    )
    if member.uid != 0 or member.gid != 0:
        raise PreflightError(
            f"archive ownership is not normalized: {member.name}"
        )
    if member.mode & ~0o777:
        raise PreflightError(
            "archive contains elevated or non-canonical mode bits: "
            f"{member.name}"
        )
    if member.mtime <= 0:
        raise PreflightError(f"archive timestamp is invalid: {member.name}")
    return member


def _tar_stream_limit(maximum_members: int, maximum_total: int) -> int:
    # One 512-byte header plus at most 511 bytes of padding per regular
    # member, followed by tar end markers and record padding.
    return maximum_total + maximum_members * 1024 + TAR_RECORD_BYTES


def _compressed_archive_limit(maximum_members: int, maximum_total: int) -> int:
    # Deflate can be slightly larger than its input. Keep a bounded
    # allowance while remaining aligned with the admissible tar stream.
    return min(
        HARD_MAXIMUM_COMPRESSED_ARCHIVE_BYTES,
        _tar_stream_limit(maximum_members, maximum_total)
        + 16 * 1024 * 1024,
    )


def _open_gzip_stream(stream: BinaryIO) -> gzip.GzipFile:
    return gzip.GzipFile(fileobj=stream, mode="rb")


class _BoundedTarReader:
    def __init__(
        self,
        stream: BinaryIO,
        *,
        maximum_members: int,
        maximum_member_bytes: int,
        maximum_total_bytes: int,
        maximum_stream_bytes: int,
    ) -> None:
        self.stream = stream
        self.maximum_members = maximum_members
        self.maximum_member_bytes = maximum_member_bytes
        self.maximum_total_bytes = maximum_total_bytes
        self.maximum_stream_bytes = maximum_stream_bytes
        self.member_count = 0
        self.total_member_bytes = 0
        self.stream_bytes = 0
        self._pending: _CanonicalTarMember | None = None
        self._ended = False

    def _read_exact(self, size: int, label: str) -> bytes:
        if size < 0 or self.stream_bytes + size > self.maximum_stream_bytes:
            raise PreflightError("decompressed tar stream exceeds compiled bound")
        chunks: list[bytes] = []
        remaining = size
        while remaining:
            try:
                chunk = self.stream.read(remaining)
            except (OSError, EOFError, gzip.BadGzipFile) as error:
                raise PreflightError(f"{label}: decompression failed: {error}") from error
            if not chunk:
                raise PreflightError(f"{label}: truncated archive")
            self.stream_bytes += len(chunk)
            remaining -= len(chunk)
            chunks.append(chunk)
        return b"".join(chunks)

    def _read_tail_zeros(self) -> None:
        while True:
            remaining_budget = self.maximum_stream_bytes - self.stream_bytes
            request = min(64 * 1024, remaining_budget + 1)
            try:
                chunk = self.stream.read(request)
            except (OSError, EOFError, gzip.BadGzipFile) as error:
                raise PreflightError(
                    f"tar trailer decompression failed: {error}"
                ) from error
            if not chunk:
                return
            self.stream_bytes += len(chunk)
            if self.stream_bytes > self.maximum_stream_bytes:
                raise PreflightError(
                    "decompressed tar stream exceeds compiled bound"
                )
            if any(chunk):
                raise PreflightError("archive has nonzero trailing data")

    def next_member(self) -> _CanonicalTarMember | None:
        if self._ended:
            return None
        if self._pending is not None:
            raise PreflightError("previous archive member was not consumed")
        block = self._read_exact(TAR_BLOCK_BYTES, "tar header")
        if block == b"\0" * TAR_BLOCK_BYTES:
            second = self._read_exact(TAR_BLOCK_BYTES, "tar end marker")
            if second != b"\0" * TAR_BLOCK_BYTES:
                raise PreflightError("archive has only one zero end marker")
            self._read_tail_zeros()
            self._ended = True
            return None

        member = _parse_canonical_tar_header(block)
        self.member_count += 1
        if self.member_count > self.maximum_members:
            raise PreflightError("archive member count is outside policy")
        if member.size < 0 or member.size > self.maximum_member_bytes:
            raise PreflightError(
                f"archive member size is outside policy: {member.name}"
            )
        projected = self.total_member_bytes + member.size
        if projected > self.maximum_total_bytes:
            raise PreflightError("archive unpacked size exceeds policy")
        self.total_member_bytes = projected
        self._pending = member
        return member

    def consume(
        self,
        member: _CanonicalTarMember,
        *,
        retain_limit: int | None = None,
    ) -> tuple[str, bytes | None]:
        if self._pending is not member:
            raise PreflightError("archive member consumption order changed")
        if retain_limit is not None and member.size > retain_limit:
            raise PreflightError(f"{member.name}: exceeds retained-data bound")
        digest = hashlib.sha256()
        retained: list[bytes] | None = [] if retain_limit is not None else None
        remaining = member.size
        while remaining:
            chunk = self._read_exact(
                min(1024 * 1024, remaining), member.name
            )
            remaining -= len(chunk)
            digest.update(chunk)
            if retained is not None:
                retained.append(chunk)
        padding = (-member.size) % TAR_BLOCK_BYTES
        if padding:
            pad = self._read_exact(padding, f"{member.name} padding")
            if any(pad):
                raise PreflightError(
                    f"archive member padding is nonzero: {member.name}"
                )
        self._pending = None
        return digest.hexdigest(), (
            b"".join(retained) if retained is not None else None
        )

def _generated_archive_namespace_collision(path: str) -> bool:
    return any(
        path == generated
        or path.startswith(generated + "/")
        or generated.startswith(path + "/")
        for generated in GENERATED_ARCHIVE_PATHS
    )


def _validate_complete_archive_namespace(paths: list[str]) -> None:
    """Reject any file path that is an ancestor of another archive file."""
    namespace = set(GENERATED_ARCHIVE_PATHS)
    namespace.update(paths)
    for candidate in sorted(namespace):
        parts = candidate.split("/")
        for depth in range(1, len(parts)):
            ancestor = "/".join(parts[:depth])
            if ancestor in namespace:
                raise PreflightError(
                    "manifest archive namespace contains a file/directory "
                    "prefix collision: "
                    f"{ancestor!r} versus {candidate!r}"
                )


def _check_manifest_shape(manifest: Any, profile: str) -> list[dict[str, Any]]:
    required = {
        "schema",
        "version",
        "profile",
        "source_sha",
        "source_date_epoch",
        "created_by",
        "authorization",
        "files",
    }
    if not isinstance(manifest, dict) or set(manifest) != required:
        raise PreflightError("release manifest fields are not canonical")
    if manifest.get("schema") != MANIFEST_SCHEMA:
        raise PreflightError("release manifest schema mismatch")
    if manifest.get("profile") != profile:
        raise PreflightError("release manifest profile mismatch")
    version = manifest.get("version")
    if not isinstance(version, str) or RELEASE_LABEL_RE.fullmatch(version) is None:
        raise PreflightError("release version must be a bounded canonical label")
    if SOURCE_SHA_RE.fullmatch(str(manifest.get("source_sha", ""))) is None:
        raise PreflightError("release source SHA is invalid")
    epoch = manifest.get("source_date_epoch")
    if not isinstance(epoch, int) or isinstance(epoch, bool) or epoch <= 0:
        raise PreflightError("release source epoch is invalid")
    if manifest.get("created_by") != "scripts/build_release_package.py":
        raise PreflightError("release builder identity mismatch")
    authorization = manifest.get("authorization")
    if authorization != {
        "effect": "NONE",
        "paper_authorized": False,
        "live_authorized": False,
    }:
        raise PreflightError("release archive attempted to claim authorization")
    files = manifest.get("files")
    if not isinstance(files, list) or not files:
        raise PreflightError("release manifest file list is empty")
    paths: list[str] = []
    for index, item in enumerate(files):
        if not isinstance(item, dict) or set(item) != {"path", "size", "mode", "sha256"}:
            raise PreflightError(f"manifest file[{index}] fields are invalid")
        path = item.get("path")
        if not isinstance(path, str):
            raise PreflightError(f"manifest file[{index}] path is invalid")
        parts = _canonical_member_name(path)
        if _generated_archive_namespace_collision(path):
            raise PreflightError(
                "manifest payload collides with generated archive namespace: "
                f"{path}"
            )
        if len(parts) < 1:
            raise PreflightError(f"manifest file[{index}] path is empty")
        size = item.get("size")
        mode = item.get("mode")
        digest = item.get("sha256")
        if not isinstance(size, int) or isinstance(size, bool) or size < 0:
            raise PreflightError(f"manifest file[{index}] size is invalid")
        if mode not in {0o644, 0o755}:
            raise PreflightError(f"manifest file[{index}] mode is invalid")
        if not isinstance(digest, str) or SHA256_RE.fullmatch(digest) is None:
            raise PreflightError(f"manifest file[{index}] digest is invalid")
        paths.append(path)
    if paths != sorted(paths) or len(paths) != len(set(paths)):
        raise PreflightError("manifest paths must be unique and byte sorted")
    _validate_complete_archive_namespace(paths)
    return files


def _private_path(path: str, suffixes: set[str]) -> bool:
    name = PurePosixPath(path).name.lower()
    if name in {".env", "id_rsa", "id_ed25519", "authorized_keys"}:
        return True
    if name.endswith(".env") and not name.endswith(".env.example"):
        return True
    return any(name.endswith(suffix) for suffix in suffixes)


def inspect_archive(
    artifact: Path, expected_sha256: str, policy: LoadedPolicy, profile: str
) -> tuple[dict[str, Any], str, str]:
    if not isinstance(policy, LoadedPolicy):
        raise PreflightError(
            "artifact inspection requires a descriptor-pinned policy"
        )
    if SHA256_RE.fullmatch(expected_sha256) is None:
        raise PreflightError("expected artifact SHA-256 is not canonical")
    maximum_members = policy["maximum_archive_members"]
    maximum_member = policy["maximum_member_bytes"]
    maximum_total = policy["maximum_total_unpacked_bytes"]
    maximum_stream = _tar_stream_limit(maximum_members, maximum_total)
    maximum_compressed = _compressed_archive_limit(
        maximum_members, maximum_total
    )

    with _open_pinned_regular(artifact, "artifact") as (
        artifact_stream,
        metadata,
        directory,
        name,
    ):
        if metadata.st_size <= 0 or metadata.st_size > maximum_compressed:
            raise PreflightError(
                "artifact compressed size is outside compiled bound"
            )
        actual_sha256 = _hash_stream(artifact_stream)
        if actual_sha256 != expected_sha256:
            raise PreflightError("artifact SHA-256 mismatch")
        artifact_stream.seek(0)

        try:
            with _open_gzip_stream(artifact_stream) as gzip_stream:
                reader = _BoundedTarReader(
                    gzip_stream,
                    maximum_members=maximum_members,
                    maximum_member_bytes=maximum_member,
                    maximum_total_bytes=maximum_total,
                    maximum_stream_bytes=maximum_stream,
                )
                manifest_member = reader.next_member()
                if manifest_member is None:
                    raise PreflightError("release archive is empty")
                manifest_parts = _canonical_member_name(
                    manifest_member.name
                )
                if (
                    len(manifest_parts) != 2
                    or manifest_parts[1] != "manifest.json"
                ):
                    raise PreflightError(
                        "release manifest must be the first archive member"
                    )
                root_name = manifest_parts[0]
                if not root_name.startswith("heptatrader-"):
                    raise PreflightError(
                        "archive root name is not canonical"
                    )
                if manifest_member.mode != 0o644:
                    raise PreflightError("release manifest mode is invalid")
                manifest_sha256, manifest_bytes = reader.consume(
                    manifest_member, retain_limit=4 * 1024 * 1024
                )
                if manifest_bytes is None:
                    raise PreflightError("release manifest is unreadable")
                manifest = parse_json_bytes(manifest_bytes, "manifest")
                manifest_files = _check_manifest_shape(manifest, profile)
                # Bind root/version/profile before any payload is consumed,
                # using the SAME admitted descriptor and manifest already read.
                expected_root = f"heptatrader-{manifest['version']}-{profile}"
                if root_name != expected_root:
                    raise PreflightError(
                        "archive root identity does not match manifest version/profile: "
                        f"expected={expected_root!r}, observed={root_name!r}"
                    )
                if (
                    manifest["source_date_epoch"]
                    != manifest_member.mtime
                ):
                    raise PreflightError(
                        "manifest and archive timestamp identity mismatch"
                    )
                if len(manifest_files) + 1 > maximum_members:
                    raise PreflightError(
                        "manifest inventory exceeds archive member policy"
                    )

                suffixes = {
                    item.lower()
                    for item in policy["private_key_suffixes"]
                }
                retained_bytes: dict[str, bytes] = {}
                retain_limits = {
                    "share/heptatrader/preflight-policy-v1.json": 4
                    * 1024
                    * 1024,
                    "share/heptatrader/heptatrader-build-info.json": 64
                    * 1024,
                }
                for item in manifest_files:
                    relative = item["path"]
                    member = reader.next_member()
                    if member is None:
                        raise PreflightError(
                            f"archive payload is missing: {relative}"
                        )
                    expected_name = f"{root_name}/{relative}"
                    if member.name != expected_name:
                        raise PreflightError(
                            "archive payload order or identity differs from "
                            f"manifest: expected={expected_name}, "
                            f"observed={member.name}"
                        )
                    if _private_path(relative, suffixes):
                        raise PreflightError(
                            "private-key or secret-like path is forbidden: "
                            f"{relative}"
                        )
                    if member.size != item["size"]:
                        raise PreflightError(
                            f"payload size mismatch: {relative}"
                        )
                    if member.mode != item["mode"]:
                        raise PreflightError(
                            f"payload mode mismatch: {relative}"
                        )
                    if member.mtime != manifest["source_date_epoch"]:
                        raise PreflightError(
                            f"payload timestamp mismatch: {relative}"
                        )
                    digest, value = reader.consume(
                        member,
                        retain_limit=retain_limits.get(relative),
                    )
                    if value is not None:
                        retained_bytes[relative] = value
                    if digest != item["sha256"]:
                        raise PreflightError(
                            f"payload digest mismatch: {relative}"
                        )

                if reader.next_member() is not None:
                    raise PreflightError(
                        "archive contains payload not present in manifest"
                    )

                selected = policy["profiles"].get(profile)
                if not isinstance(selected, dict):
                    raise PreflightError(
                        "profile is absent from preflight policy: "
                        f"{profile}"
                    )
                required = selected.get("required_package_paths")
                if (
                    not isinstance(required, list)
                    or any(
                        not isinstance(item, str) for item in required
                    )
                    or required != sorted(required)
                    or len(required) != len(set(required))
                ):
                    raise PreflightError(
                        "required package path policy is invalid"
                    )
                expected_paths = {
                    item["path"] for item in manifest_files
                }
                absent = sorted(set(required) - expected_paths)
                if absent:
                    raise PreflightError(
                        "required package files are missing: "
                        + ", ".join(absent)
                    )

                policy_path = (
                    "share/heptatrader/preflight-policy-v1.json"
                )
                packaged_policy_bytes = retained_bytes.get(policy_path)
                if packaged_policy_bytes is None:
                    raise PreflightError(
                        "packaged preflight policy is missing"
                    )
                if (
                    hashlib.sha256(packaged_policy_bytes).hexdigest()
                    != policy.sha256
                ):
                    raise PreflightError(
                        "effective preflight policy does not match the "
                        "policy bound in the package"
                    )
                packaged_policy = parse_json_bytes(
                    packaged_policy_bytes, "packaged preflight policy"
                )
                if packaged_policy != dict(policy):
                    raise PreflightError(
                        "effective and packaged preflight policy "
                        "semantics differ"
                    )

                build_info_path = (
                    "share/heptatrader/heptatrader-build-info.json"
                )
                build_info_bytes = retained_bytes.get(build_info_path)
                if build_info_bytes is None:
                    raise PreflightError(
                        "installed build metadata is missing"
                    )
                build_info = parse_json_bytes(
                    build_info_bytes, "installed build metadata"
                )
                if not isinstance(build_info, dict):
                    raise PreflightError(
                        "installed build metadata must be an object"
                    )
                if (
                    build_info.get("release_label")
                    != manifest["version"]
                    or build_info.get("paper_authorized") is not False
                    or build_info.get("live_authorized") is not False
                ):
                    raise PreflightError(
                        "installed build metadata does not match the "
                        "release identity"
                    )
                if (
                    profile == "ib-paper"
                    and build_info.get("ib_api_compiled") is not True
                ):
                    raise PreflightError(
                        "IB PAPER package was not built with the IB API"
                    )
                if (
                    profile == "core"
                    and build_info.get("ib_api_compiled") is not False
                ):
                    raise PreflightError(
                        "core package unexpectedly contains an "
                        "IB-enabled build"
                    )
        except PreflightError:
            raise
        except (OSError, EOFError, gzip.BadGzipFile) as error:
            raise PreflightError(
                "artifact is not a valid bounded gzip/USTAR archive: "
                f"{error}"
            ) from error

        _assert_stable_file(
            artifact_stream, metadata, directory, name, "artifact"
        )
    return manifest, actual_sha256, manifest_sha256

def _machine_id_digest(root: Path) -> str:
    try:
        root_descriptor = _open_directory_absolute(root, "host root")
    except (OSError, PreflightError):
        return ""
    try:
        for relative in ("etc/machine-id", "var/lib/dbus/machine-id"):
            try:
                with _open_pinned_regular_at(
                    root_descriptor, relative, "machine identity"
                ) as (stream, pinned, directory, name):
                    value = _read_bounded(stream, 4096, relative).strip()
                    _assert_stable_file(
                        stream, pinned, directory, name, "machine identity"
                    )
                    if value:
                        return hashlib.sha256(value).hexdigest()
            except (OSError, PreflightError):
                continue
        return ""
    finally:
        os.close(root_descriptor)


def _is_managed_package_path(path: str) -> bool:
    if any(path == root or path.startswith(root + "/") for root in MANAGED_SUBTREES):
        return True
    return any(
        path.startswith(directory + "/")
        and PurePosixPath(path).parent.as_posix() == directory
        and PurePosixPath(path).name.startswith(prefix)
        for directory, prefix in MANAGED_PREFIX_DIRECTORIES
    )


def _scan_subtree(
    root: int, relative_root: str, result: set[str], errors: list[str]
) -> None:
    parts = _canonical_member_name("usr/" + relative_root)
    try:
        start = _open_relative_directory(root, tuple(parts), relative_root)
    except FileNotFoundError:
        return
    except (OSError, PreflightError) as error:
        errors.append(f"{relative_root}: {error}")
        return

    def visit(descriptor: int, logical: str) -> None:
        try:
            with os.scandir(descriptor) as entries:
                ordered = sorted(list(entries), key=lambda item: item.name)
            for entry in ordered:
                path = f"{logical}/{entry.name}"
                metadata = entry.stat(follow_symlinks=False)
                if stat.S_ISDIR(metadata.st_mode):
                    try:
                        child = os.open(
                            entry.name, _directory_flags(), dir_fd=descriptor
                        )
                    except OSError as error:
                        errors.append(f"{path}: {error}")
                        continue
                    try:
                        visit(child, path)
                    finally:
                        os.close(child)
                elif stat.S_ISREG(metadata.st_mode):
                    result.add(path)
                else:
                    errors.append(
                        f"{path}: managed entry is not a regular non-symlink file"
                    )
        except OSError as error:
            errors.append(f"{logical}: {error}")

    try:
        visit(start, relative_root)
    finally:
        os.close(start)


def _scan_prefix_directory(
    root: int,
    relative_directory: str,
    prefix: str,
    result: set[str],
    errors: list[str],
) -> None:
    parts = _canonical_member_name("usr/" + relative_directory)
    try:
        descriptor = _open_relative_directory(
            root, tuple(parts), relative_directory
        )
    except FileNotFoundError:
        return
    except (OSError, PreflightError) as error:
        errors.append(f"{relative_directory}: {error}")
        return
    try:
        with os.scandir(descriptor) as entries:
            ordered = sorted(list(entries), key=lambda item: item.name)
        for entry in ordered:
            if not entry.name.startswith(prefix):
                continue
            path = f"{relative_directory}/{entry.name}"
            metadata = entry.stat(follow_symlinks=False)
            if stat.S_ISREG(metadata.st_mode):
                result.add(path)
            else:
                errors.append(
                    f"{path}: managed entry is not a regular non-symlink file"
                )
    except OSError as error:
        errors.append(f"{relative_directory}: {error}")
    finally:
        os.close(descriptor)


def _scan_managed_installed_paths(root: int) -> tuple[set[str], list[str]]:
    result: set[str] = set()
    errors: list[str] = []
    for relative_root in MANAGED_SUBTREES:
        _scan_subtree(root, relative_root, result, errors)
    for directory, prefix in MANAGED_PREFIX_DIRECTORIES:
        _scan_prefix_directory(root, directory, prefix, result, errors)
    return result, errors


def _installed_metadata_problem(
    metadata: os.stat_result,
    item: dict[str, Any],
    expected_uid: int,
    expected_gid: int,
    path: str,
) -> str | None:
    if not stat.S_ISREG(metadata.st_mode) or metadata.st_nlink != 1:
        return f"{path}: not a regular non-symlink single-link file"
    if metadata.st_uid != expected_uid or metadata.st_gid != expected_gid:
        return (
            f"{path}: owner mismatch, expected {expected_uid}:{expected_gid}, "
            f"found {metadata.st_uid}:{metadata.st_gid}"
        )
    if stat.S_IMODE(metadata.st_mode) != item["mode"]:
        return (
            f"{path}: mode mismatch, expected {oct(item['mode'])}, "
            f"found {oct(stat.S_IMODE(metadata.st_mode))}"
        )
    if metadata.st_size != item["size"]:
        return (
            f"{path}: size mismatch, expected {item['size']}, "
            f"found {metadata.st_size}"
        )
    return None


def _verify_installed_tree(
    host_root: Path,
    manifest: dict[str, Any],
    policy: LoadedPolicy,
    profile: str,
) -> list[str]:
    errors: list[str] = []
    try:
        root = _open_directory_absolute(host_root, "host root")
    except (OSError, PreflightError) as error:
        return [f"host root: {error}"]
    try:
        root_metadata = os.fstat(root)
        expected_uid = root_metadata.st_uid
        expected_gid = root_metadata.st_gid
        files = manifest.get("files")
        if not isinstance(files, list):
            return ["release manifest file inventory is unavailable"]
        manifest_by_path = {
            item["path"]: item
            for item in files
            if isinstance(item, dict) and isinstance(item.get("path"), str)
        }
        if len(manifest_by_path) != len(files):
            return ["release manifest file inventory is invalid"]
        unmanaged = sorted(
            path for path in manifest_by_path if not _is_managed_package_path(path)
        )
        if unmanaged:
            errors.append(
                "package contains paths outside managed install namespaces: "
                + ", ".join(unmanaged)
            )

        actual, scan_errors = _scan_managed_installed_paths(root)
        errors.extend(scan_errors)
        expected = set(manifest_by_path)
        missing = sorted(expected - actual)
        extra = sorted(actual - expected)
        if missing:
            errors.append("installed package files are missing: " + ", ".join(missing))
        if extra:
            errors.append("unexpected managed package files are installed: " + ", ".join(extra))

        build_info_bytes: bytes | None = None
        for path in sorted(expected & actual):
            item = manifest_by_path[path]
            try:
                with _open_pinned_regular_at(
                    root, "usr/" + path, f"installed {path}"
                ) as (stream, pinned, directory, name):
                    problem = _installed_metadata_problem(
                        pinned, item, expected_uid, expected_gid, path
                    )
                    if problem:
                        errors.append(problem)
                        continue
                    digest = _hash_stream(stream)
                    if digest != item["sha256"]:
                        errors.append(f"{path}: installed payload digest mismatch")
                    if path == "share/heptatrader/heptatrader-build-info.json":
                        stream.seek(0)
                        build_info_bytes = _read_bounded(
                            stream, 64 * 1024, "installed build metadata"
                        )
                    _assert_stable_file(
                        stream, pinned, directory, name, f"installed {path}"
                    )
            except (OSError, PreflightError) as error:
                errors.append(f"{path}: {error}")

        if build_info_bytes is None:
            errors.append("installed build metadata could not be verified")
        else:
            try:
                build_info = parse_json_bytes(
                    build_info_bytes, "installed host build metadata"
                )
                if (
                    not isinstance(build_info, dict)
                    or build_info.get("release_label") != manifest.get("version")
                    or build_info.get("paper_authorized") is not False
                    or build_info.get("live_authorized") is not False
                    or (
                        profile == "core"
                        and build_info.get("ib_api_compiled") is not False
                    )
                    or (
                        profile == "ib-paper"
                        and build_info.get("ib_api_compiled") is not True
                    )
                ):
                    errors.append(
                        "installed host build metadata does not match the approved artifact"
                    )
            except PreflightError as error:
                errors.append(str(error))
        return errors
    finally:
        os.close(root)



def _safe_kill_switch(
    host_root: Path,
    path: Path,
    expected_group_gid: int,
    *,
    expected_owner_uid: int = 0,
) -> str | None:
    if os.fspath(path) != CANONICAL_IB_PAPER_KILL_SWITCH_PATH:
        return (
            "kill-switch path must be exactly "
            + CANONICAL_IB_PAPER_KILL_SWITCH_PATH
        )
    if (
        not isinstance(expected_owner_uid, int)
        or isinstance(expected_owner_uid, bool)
        or expected_owner_uid < 0
        or not isinstance(expected_group_gid, int)
        or isinstance(expected_group_gid, bool)
        or expected_group_gid < 0
    ):
        return "kill-switch owner/group identity is invalid"

    root_descriptor = -1
    directory = -1
    marker = -1
    confirmation = -1
    parts = ("run", "hepta", "ib-paper-control")
    try:
        root_descriptor = _open_directory_absolute(
            host_root, "host root"
        )
        directory = _open_relative_directory(
            root_descriptor,
            parts,
            "kill-switch control directory",
        )
        directory_metadata = os.fstat(directory)
        if (
            not stat.S_ISDIR(directory_metadata.st_mode)
            or directory_metadata.st_uid != expected_owner_uid
            or directory_metadata.st_gid != expected_group_gid
            or stat.S_IMODE(directory_metadata.st_mode)
            != CANONICAL_IB_PAPER_CONTROL_DIRECTORY_MODE
            or directory_metadata.st_nlink != 2
        ):
            return (
                "kill-switch control directory must be a stable "
                "0750 owner/group-bound directory with no subdirectories"
            )

        marker = os.open(
            "kill-switch",
            _file_flags(),
            dir_fd=directory,
        )
        pinned = os.fstat(marker)
        current = os.stat(
            "kill-switch",
            dir_fd=directory,
            follow_symlinks=False,
        )
        if (
            not stat.S_ISREG(pinned.st_mode)
            or pinned.st_nlink != 1
            or pinned.st_uid != expected_owner_uid
            or pinned.st_gid != expected_group_gid
            or stat.S_IMODE(pinned.st_mode)
            != CANONICAL_IB_PAPER_KILL_SWITCH_MODE
            or pinned.st_dev != directory_metadata.st_dev
            or not _same_inode(pinned, current)
        ):
            return (
                "kill-switch marker must be the canonical stable "
                "0440 owner/group-bound regular single-link file"
            )
        _clear_nonblocking(marker)
        with os.fdopen(marker, "rb", closefd=True) as stream:
            marker = -1
            content = _read_bounded(
                stream,
                len(CANONICAL_IB_PAPER_KILL_SWITCH_CONTENT),
                "kill-switch marker",
            )
            if content != CANONICAL_IB_PAPER_KILL_SWITCH_CONTENT:
                return (
                    "kill-switch marker must contain the exact "
                    "engaged-state representation"
                )
            _assert_stable_file(
                stream,
                pinned,
                directory,
                "kill-switch",
                "kill-switch marker",
            )

        confirmation = _open_relative_directory(
            root_descriptor,
            parts,
            "kill-switch control directory confirmation",
        )
        confirmed = os.fstat(confirmation)
        if not _same_file(directory_metadata, confirmed):
            return (
                "kill-switch control directory identity changed "
                "during validation"
            )
        return None
    except (OSError, PreflightError) as error:
        return f"kill-switch marker validation failed: {error}"
    finally:
        if marker >= 0:
            os.close(marker)
        if confirmation >= 0:
            os.close(confirmation)
        if directory >= 0:
            os.close(directory)
        if root_descriptor >= 0:
            os.close(root_descriptor)


def _open_receipt_output_directory(
    path: Path,
) -> tuple[int, os.stat_result]:
    path.parent.mkdir(parents=True, exist_ok=True)
    directory = os.open(
        path.parent,
        os.O_RDONLY
        | getattr(os, "O_DIRECTORY", 0)
        | getattr(os, "O_CLOEXEC", 0)
        | getattr(os, "O_NOFOLLOW", 0),
    )
    try:
        pinned = os.fstat(directory)
        current = os.stat(path.parent, follow_symlinks=False)
        if (
            not stat.S_ISDIR(pinned.st_mode)
            or not _same_inode(pinned, current)
        ):
            raise PreflightError(
                f"receipt parent is not a stable directory: {path.parent}"
            )
        if (
            pinned.st_uid != os.geteuid()
            or stat.S_IMODE(pinned.st_mode) & 0o022
        ):
            raise PreflightError(
                "receipt output directory must be operator-custodied: "
                "owned by the current user and not group/world writable"
            )
        return directory, pinned
    except Exception:
        os.close(directory)
        raise


def _open_anonymous_receipt(directory: int) -> int:
    anonymous = getattr(os, "O_TMPFILE", 0)
    if not anonymous:
        raise PreflightError(
            "receipt filesystem lacks anonymous inode-bound staging"
        )
    try:
        descriptor = os.open(
            ".",
            os.O_RDWR
            | anonymous
            | getattr(os, "O_CLOEXEC", 0),
            0o600,
            dir_fd=directory,
        )
    except OSError as error:
        raise PreflightError(
            "receipt filesystem does not support anonymous "
            "inode-bound staging"
        ) from error
    metadata = os.fstat(descriptor)
    if not stat.S_ISREG(metadata.st_mode) or metadata.st_nlink != 0:
        os.close(descriptor)
        raise PreflightError(
            "anonymous receipt staging is not an unlinked regular file"
        )
    os.fchmod(descriptor, 0o600)
    return descriptor


def _write_all_descriptor(descriptor: int, content: bytes) -> None:
    remaining = memoryview(content)
    while remaining:
        try:
            count = os.write(descriptor, remaining)
        except InterruptedError:
            continue
        if count <= 0:
            raise PreflightError(
                "receipt staging write made no progress"
            )
        remaining = remaining[count:]


def _descriptor_sha256(descriptor: int) -> str:
    digest = hashlib.sha256()
    offset = 0
    while True:
        chunk = os.pread(descriptor, 1024 * 1024, offset)
        if not chunk:
            return digest.hexdigest()
        digest.update(chunk)
        offset += len(chunk)


def _publish_noreplace(
    directory: int, descriptor: int, final_name: str
) -> None:
    os.link(
        f"/proc/self/fd/{descriptor}",
        final_name,
        dst_dir_fd=directory,
        follow_symlinks=True,
    )


def _write_private_receipt(path: Path, value: dict[str, Any]) -> None:
    path = _absolute_path(path)
    content = canonical_json(value)
    directory, pinned_directory = _open_receipt_output_directory(path)
    descriptor = -1
    published = False
    try:
        descriptor = _open_anonymous_receipt(directory)
        _write_all_descriptor(descriptor, content)
        os.fsync(descriptor)
        try:
            _publish_noreplace(directory, descriptor, path.name)
            published = True
        except FileExistsError as error:
            raise PreflightError(
                f"refusing to replace concurrently created receipt: {path}"
            ) from error
        pinned = os.fstat(descriptor)
        current = os.stat(
            path.name, dir_fd=directory, follow_symlinks=False
        )
        if (
            not stat.S_ISREG(current.st_mode)
            or current.st_nlink != 1
            or not _same_inode(pinned, current)
            or current.st_size != len(content)
            or stat.S_IMODE(current.st_mode) != 0o600
            or _descriptor_sha256(descriptor)
            != hashlib.sha256(content).hexdigest()
        ):
            raise PreflightError(
                "published receipt identity, bytes or mode differ "
                "from the fsynced staging inode"
            )
        current_directory = os.stat(
            path.parent, follow_symlinks=False
        )
        if not _same_inode(pinned_directory, current_directory):
            raise PreflightError(
                "receipt output directory identity changed during publication"
            )
        os.fsync(directory)
    except Exception:
        if published and descriptor >= 0:
            try:
                current = os.stat(
                    path.name,
                    dir_fd=directory,
                    follow_symlinks=False,
                )
                if _same_inode(os.fstat(descriptor), current):
                    os.unlink(path.name, dir_fd=directory)
                    os.fsync(directory)
            except OSError:
                pass
        raise
    finally:
        if descriptor >= 0:
            os.close(descriptor)
        os.close(directory)


def run_preflight(args: argparse.Namespace) -> dict[str, Any]:
    checks: list[dict[str, str]] = []

    def record(check_id: str, status_value: str, detail: str) -> None:
        checks.append({"id": check_id, "status": status_value, "detail": detail})

    manifest: dict[str, Any] = {}
    package_sha256 = ""
    manifest_sha256 = ""
    artifact_admitted = False
    policy = _load_policy(_absolute_path(args.policy))
    try:
        manifest, package_sha256, manifest_sha256 = inspect_archive(
            _absolute_path(args.artifact), args.expected_sha256, policy, args.profile
        )
        artifact_admitted = True
        record("artifact.integrity", "PASS", "digest, archive, manifest and payload verified")
    except (OSError, PreflightError) as error:
        record("artifact.integrity", "FAIL", str(error))

    selected = policy["profiles"].get(args.profile, {})
    required = selected.get("required_package_paths", []) if isinstance(selected, dict) else []
    if args.artifact_only:
        record("host.static", "SKIP", "artifact-only mode requested")
    elif not manifest:
        record("host.static", "FAIL", "host checks require a valid release manifest")
    else:
        host_errors: list[str] = []
        execution_group_gid: int | None = None
        host_root = _absolute_path(args.host_root)
        if platform.system() != "Linux":
            host_errors.append("host platform is not Linux")
        for command in selected.get("required_host_commands", []):
            if shutil.which(command) is None:
                host_errors.append(f"required command is missing: {command}")
        host_errors.extend(_verify_installed_tree(host_root, manifest, policy, args.profile))
        if args.profile == "ib-paper":
            if args.execution_uid is None or args.gateway_uid is None:
                host_errors.append("IB PAPER static preflight requires execution and Gateway UIDs")
            elif (
                args.execution_uid <= 0
                or args.gateway_uid <= 0
                or args.execution_uid == args.gateway_uid
            ):
                host_errors.append("IB PAPER execution and Gateway UIDs must be distinct positive values")
            else:
                for uid in (args.execution_uid, args.gateway_uid):
                    try:
                        account = pwd.getpwuid(uid)
                        if uid == args.execution_uid:
                            execution_group_gid = account.pw_gid
                    except KeyError:
                        host_errors.append(f"host UID does not exist: {uid}")
            if args.kill_switch_path is None:
                host_errors.append("IB PAPER static preflight requires a kill-switch marker path")
            elif execution_group_gid is None:
                host_errors.append(
                    "IB PAPER execution primary group is unavailable"
                )
            else:
                problem = _safe_kill_switch(
                    host_root,
                    args.kill_switch_path,
                    execution_group_gid,
                )
                if problem:
                    host_errors.append(problem)
        if host_errors:
            record("host.static", "FAIL", "; ".join(host_errors))
        else:
            record("host.static", "PASS", "installed bytes, modes, ownership, inventory and identity boundaries match the approved artifact")

    if args.probe_broker:
        if not artifact_admitted:
            record(
                "broker.reachability",
                "FAIL",
                "Broker probing requires successful artifact and policy admission",
            )
        elif args.profile != "ib-paper":
            record("broker.reachability", "FAIL", "Broker probing is valid only for ib-paper")
        elif (
            args.broker_host not in HARD_ALLOWED_BROKER_HOSTS
            or args.broker_port not in HARD_ALLOWED_BROKER_PORTS
        ):
            record(
                "broker.reachability",
                "FAIL",
                "Broker endpoint is outside the compiled PAPER endpoint boundary",
            )
        else:
            hosts = set(selected.get("allowed_broker_hosts", []))
            ports = set(selected.get("allowed_broker_ports", []))
            if args.broker_host not in hosts or args.broker_port not in ports:
                record("broker.reachability", "FAIL", "Broker endpoint is outside the PAPER policy")
            else:
                try:
                    with socket.create_connection(
                        (args.broker_host, args.broker_port), timeout=args.broker_timeout
                    ):
                        pass
                    record(
                        "broker.reachability",
                        "PASS",
                        "bounded TCP reachability succeeded; this is not account-mode or qualification evidence",
                    )
                except OSError as error:
                    record("broker.reachability", "FAIL", str(error))
    else:
        record("broker.reachability", "SKIP", "no Broker probe requested")

    result = "FAIL" if any(item["status"] == "FAIL" for item in checks) else "PASS"
    host_root = _absolute_path(args.host_root) if not args.artifact_only else Path("/")
    receipt = {
        "schema": RECEIPT_SCHEMA,
        "checked_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "result": result,
        "scope": "ARTIFACT_ONLY" if args.artifact_only else "STATIC_HOST_PRECHECK",
        "profile": args.profile,
        "artifact": {
            "file": args.artifact.name,
            "sha256": package_sha256,
            "manifest_sha256": manifest_sha256,
            "policy_sha256": policy.sha256,
            "source_sha": manifest.get("source_sha", ""),
            "version": manifest.get("version", ""),
        },
        "host": {
            "system": platform.system(),
            "machine": platform.machine(),
            "kernel": platform.release(),
            "machine_id_sha256": _machine_id_digest(host_root),
        },
        "checks": checks,
        "authorization_effect": "NONE",
        "paper_authorized": False,
        "live_authorized": False,
    }
    return receipt


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--artifact", type=Path, required=True)
    parser.add_argument("--expected-sha256", required=True)
    parser.add_argument("--profile", choices=("core", "ib-paper"), required=True)
    parser.add_argument("--policy", type=Path, required=True)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--artifact-only", action="store_true")
    parser.add_argument("--host-root", type=Path, default=Path("/"))
    parser.add_argument("--execution-uid", type=int)
    parser.add_argument("--gateway-uid", type=int)
    parser.add_argument("--kill-switch-path", type=Path)
    parser.add_argument("--probe-broker", action="store_true")
    parser.add_argument("--broker-host", default="127.0.0.1")
    parser.add_argument("--broker-port", type=int, default=4002)
    parser.add_argument("--broker-timeout", type=float, default=2.0)
    args = parser.parse_args(argv)

    try:
        receipt = run_preflight(args)
        if args.output is not None:
            _write_private_receipt(args.output, receipt)
    except (OSError, PreflightError, ValueError) as error:
        print(f"[PREFLIGHT] {error}", file=sys.stderr)
        return 2
    print(canonical_json(receipt).decode("utf-8"), end="")
    return 0 if receipt["result"] == "PASS" else 1


if __name__ == "__main__":
    print(
        "[PREFLIGHT] private implementation module; "
        "use hepta-preflight",
        file=sys.stderr,
    )
    raise SystemExit(2)
