#!/usr/bin/env python3

"""Read-only, fail-closed validation of a provisioned execution host.

This checker never invokes systemd, connects to a broker, or mutates the
selected root.  Every filesystem lookup is anchored below ``--root`` and
refuses symlink traversal.
"""

from __future__ import annotations

import argparse
from contextlib import contextmanager
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
import os
from pathlib import Path, PurePosixPath
import re
import stat
import sys
from typing import Callable, Iterator, Optional

from hepta_service_identities import parse_identity_manifest


IDENTITIES = ("hepta-gateway", "hepta-exec", "hepta-ib-exec", "hepta-agent")
IDENTITY_MANIFEST_PATH = "usr/share/heptatrader/hepta-service-identities-v1.json"
PASSWD_PATH = "etc/passwd"
GROUP_PATH = "etc/group"
IB_ENV_PATH = "etc/heptatrader/hepta-execution-ib-paper.env"
GATEWAY_ENV_PATH = "etc/heptatrader/hepta-execution-gateway-paper.env"
SIMULATOR_ENV_PATH = "etc/heptatrader/hepta-execution-simulator.env"
SIMULATOR_FENCE_PATH = (
    "etc/heptatrader/credentials/hepta-execution-simulator-fence"
)
FENCE_PATH = (
    "etc/heptatrader/credentials/hepta-execution-ib-paper-fence"
)
AUTHORIZATION_PATH = (
    "etc/heptatrader/credentials/hepta-ib-paper-authorization"
)
FX_CASH_BASELINE_PATH = (
    "etc/heptatrader/credentials/hepta-fx-cash-baseline"
)
CREDENTIAL_DIRECTORY = "etc/heptatrader/credentials"
CONTROL_DIRECTORY = "run/hepta/ib-paper-control"
KILL_SWITCH_MARKER = CONTROL_DIRECTORY + "/kill-switch"
UNIT_DIRECTORY = "usr/lib/systemd/system"
TMPFILES_PATH = "usr/lib/tmpfiles.d/heptatrader-ib-paper.conf"
EXECUTION_BINARIES = (
    "usr/libexec/hepta-executiond",
    "usr/libexec/hepta-ib-executiond",
)
SYSTEMD_OVERRIDE_DIRECTORIES = (
    "etc/systemd/system",
    "run/systemd/system",
)
CANONICAL_UNITS = frozenset({
    "hepta-execution-simulator.service",
    "hepta-execution-simulator.socket",
    "hepta-execution-events-simulator.socket",
    "hepta-execution-simulator@.service",
    "hepta-execution-simulator@.socket",
    "hepta-execution-events-simulator@.socket",
    "hepta-execution-ib-paper.service",
    "hepta-execution-ib-paper.socket",
    "hepta-execution-events-ib-paper.socket",
    "hepta-execution-ib-paper@.service",
    "hepta-execution-ib-paper@.socket",
    "hepta-execution-events-ib-paper@.socket",
    "hepta-ib-paper-domain-preflight@.service",
})
EXPLICIT_LEGACY_UNITS = frozenset({"ibgateway.service"})

SAFE_ROOT_DIRECTORIES = (
    "etc",
    "etc/heptatrader",
    CREDENTIAL_DIRECTORY,
    "etc/systemd",
    "etc/systemd/system",
    "run",
    "run/hepta",
    "run/systemd",
    "run/systemd/system",
    "usr",
    "usr/lib",
    "usr/libexec",
    "usr/lib/systemd",
    UNIT_DIRECTORY,
    "usr/lib/tmpfiles.d",
    "usr/share",
    "usr/share/heptatrader",
)

IB_ENV_KEYS = frozenset({
    "HEPTA_IB_EXECUTION_MODE",
    "HEPTA_IB_PAPER_ACCOUNT",
    "HEPTA_IB_PAPER_HOST",
    "HEPTA_IB_PAPER_PORT",
    "HEPTA_IB_PAPER_CLIENT_ID",
    "HEPTA_IB_PAPER_MAX_ORDER_QTY",
    "HEPTA_IB_PAPER_MAX_ORDER_NOTIONAL",
    "HEPTA_IB_PAPER_MAX_ORDERS_PER_MINUTE",
    "HEPTA_IB_PAPER_MAX_ACTIVE_ORDERS",
    "HEPTA_IB_PAPER_MAX_GROSS_POSITION",
    "HEPTA_IB_PAPER_QUOTE_CONTRACTS",
    "HEPTA_IB_PAPER_PRIMARY_QUOTE_INSTRUMENT",
    "HEPTA_IB_PAPER_QUOTE_MAX_AGE_MS",
    "HEPTA_IB_EXECUTION_GATEWAY_UID",
    "HEPTA_IB_EXECUTION_GATEWAY_AGENT_ID",
    "HEPTA_IB_EXECUTION_MAX_REQUEST_BYTES",
    "HEPTA_IB_EXECUTION_IO_TIMEOUT_MS",
    "HEPTA_IB_EXECUTION_READINESS_TIMEOUT_MS",
    "HEPTA_IB_EXECUTION_RECONNECT_TIMEOUT_MS",
})
GATEWAY_ENV_KEYS = frozenset({
    "HEPTA_EXECUTION_REMOTE_MODE",
    "HEPTA_EXECUTION_SOCKET",
    "HEPTA_EXECUTION_EVENT_SOCKET",
    "HEPTA_EXECUTION_SERVICE_UID",
    "HEPTA_EXECUTION_IO_TIMEOUT_MS",
    "HEPTA_EXECUTION_MAX_RESPONSE_BYTES",
})
SIMULATOR_ENV_KEYS = frozenset({
    "HEPTA_EXECUTION_GATEWAY_UID",
    "HEPTA_EXECUTION_GATEWAY_AGENT_ID",
    "HEPTA_EXECUTION_MAX_REQUEST_BYTES",
    "HEPTA_EXECUTION_IO_TIMEOUT_MS",
})

TMPFILES_DIRECTIVES = (
    "d /run/hepta/ib-paper-control 0750 root hepta-ib-exec -",
    "f /run/hepta/ib-paper-control/kill-switch 0440 root hepta-ib-exec - engaged",
)

MAX_TEXT_BYTES = 1024 * 1024
UINT32_MAX = (1 << 32) - 1
OwnershipProvider = Callable[[str, os.stat_result], tuple[int, int]]


class ValidationError(RuntimeError):
    """A fail-closed provisioned-host contract violation."""


@dataclass(frozen=True)
class Identity:
    name: str
    uid: int
    gid: int


@dataclass(frozen=True)
class ValidationReport:
    root: Path
    identities: tuple[Identity, ...]
    canonical_unit_count: int
    executable_count: int
    kill_switch_engaged: bool


def fail(message: str) -> None:
    raise ValidationError(message)


def _relative_parts(relative: str) -> tuple[str, ...]:
    path = PurePosixPath(relative)
    if path.is_absolute() or not path.parts:
        fail(f"unsafe root-relative path {relative!r}")
    if any(part in ("", ".", "..") for part in path.parts):
        fail(f"unsafe root-relative path {relative!r}")
    return path.parts


class RootReader:
    """Descriptor-anchored, no-follow reads beneath one filesystem root."""

    def __init__(self, root: Path):
        self.root = Path(os.path.abspath(os.fspath(root)))
        self._fd = -1

    def __enter__(self) -> "RootReader":
        if not hasattr(os, "O_NOFOLLOW") or not hasattr(os, "O_DIRECTORY"):
            fail("O_NOFOLLOW and O_DIRECTORY are required")
        flags = os.O_RDONLY | os.O_CLOEXEC | os.O_DIRECTORY | os.O_NOFOLLOW
        descriptor = -1
        try:
            # O_NOFOLLOW applies only to the final pathname component. Walk
            # the absolute --root from / so an intermediate symlink cannot be
            # used to redirect the fixture or production inspection root.
            descriptor = os.open("/", flags)
            for component in self.root.parts[1:]:
                next_descriptor = os.open(
                    component, flags, dir_fd=descriptor)
                os.close(descriptor)
                descriptor = next_descriptor
        except OSError as error:
            if descriptor >= 0:
                os.close(descriptor)
            fail(f"cannot open a real directory as --root: {error.strerror}")
        self._fd = descriptor
        metadata = os.fstat(self._fd)
        if not stat.S_ISDIR(metadata.st_mode):
            self.close()
            fail("--root must be a real directory")
        return self

    def __exit__(self, _type: object, _value: object,
                 _traceback: object) -> None:
        self.close()

    def close(self) -> None:
        if self._fd >= 0:
            os.close(self._fd)
            self._fd = -1

    @contextmanager
    def _parent(self, relative: str) -> Iterator[tuple[int, str]]:
        parts = _relative_parts(relative)
        descriptor = os.dup(self._fd)
        try:
            directory_flags = (
                os.O_RDONLY | os.O_CLOEXEC | os.O_DIRECTORY | os.O_NOFOLLOW
            )
            for component in parts[:-1]:
                try:
                    next_descriptor = os.open(
                        component, directory_flags, dir_fd=descriptor)
                except OSError as error:
                    fail(f"{relative}: unsafe or missing parent: {error.strerror}")
                os.close(descriptor)
                descriptor = next_descriptor
            yield descriptor, parts[-1]
        finally:
            os.close(descriptor)

    def lstat(self, relative: str) -> os.stat_result:
        """Return pinned metadata without reading file content.

        Linux ``O_PATH`` lets the checker validate root-only credentials while
        holding the exact inode open.  A before/open/after identity comparison
        rejects a pathname replacement instead of accepting stale metadata.
        """
        if not hasattr(os, "O_PATH"):
            fail("O_PATH is required for metadata-only credential validation")
        with self._parent(relative) as (parent, name):
            try:
                before = os.stat(name, dir_fd=parent, follow_symlinks=False)
                descriptor = os.open(
                    name, os.O_PATH | os.O_CLOEXEC | os.O_NOFOLLOW,
                    dir_fd=parent)
            except OSError as error:
                fail(f"{relative}: unsafe or missing inode: {error.strerror}")
            try:
                metadata = os.fstat(descriptor)
                self._require_same_inode(relative, before, metadata)
                current = self._path_stat(parent, name, relative)
                self._require_same_inode(relative, metadata, current)
                return metadata
            finally:
                os.close(descriptor)
        raise AssertionError("unreachable")

    @staticmethod
    def _path_stat(parent: int, name: str, relative: str) -> os.stat_result:
        try:
            return os.stat(name, dir_fd=parent, follow_symlinks=False)
        except OSError as error:
            fail(f"{relative}: pathname changed during validation: "
                 f"{error.strerror}")
        raise AssertionError("unreachable")

    @staticmethod
    def _require_same_inode(relative: str, expected: os.stat_result,
                            observed: os.stat_result) -> None:
        stable_fields = (
            "st_dev", "st_ino", "st_mode", "st_nlink", "st_uid",
            "st_gid", "st_size", "st_mtime_ns", "st_ctime_ns")
        if any(getattr(expected, field) != getattr(observed, field)
               for field in stable_fields):
            fail(f"{relative}: inode or metadata changed during validation")

    @contextmanager
    def open_regular(self, relative: str) -> Iterator[tuple[int, os.stat_result]]:
        with self._parent(relative) as (parent, name):
            try:
                before = os.stat(name, dir_fd=parent, follow_symlinks=False)
                descriptor = os.open(
                    name, os.O_RDONLY | os.O_CLOEXEC | os.O_NOFOLLOW |
                    os.O_NONBLOCK,
                    dir_fd=parent)
            except OSError as error:
                fail(f"{relative}: unsafe or missing file: {error.strerror}")
            try:
                metadata = os.fstat(descriptor)
                if not stat.S_ISREG(before.st_mode) or not stat.S_ISREG(
                        metadata.st_mode):
                    fail(f"{relative}: must be a regular file")
                self._require_same_inode(relative, before, metadata)
                yield descriptor, metadata
            finally:
                try:
                    after = os.fstat(descriptor)
                    self._require_same_inode(relative, metadata, after)
                    current = self._path_stat(parent, name, relative)
                    self._require_same_inode(relative, after, current)
                finally:
                    os.close(descriptor)

    @contextmanager
    def open_directory(self, relative: str) -> Iterator[tuple[int, os.stat_result]]:
        with self._parent(relative) as (parent, name):
            flags = os.O_RDONLY | os.O_CLOEXEC | os.O_DIRECTORY | os.O_NOFOLLOW
            try:
                before = os.stat(name, dir_fd=parent, follow_symlinks=False)
                descriptor = os.open(name, flags, dir_fd=parent)
            except OSError as error:
                fail(f"{relative}: unsafe or missing directory: {error.strerror}")
            try:
                metadata = os.fstat(descriptor)
                if not stat.S_ISDIR(metadata.st_mode):
                    fail(f"{relative}: must be a directory")
                self._require_same_inode(relative, before, metadata)
                yield descriptor, metadata
            finally:
                try:
                    after = os.fstat(descriptor)
                    self._require_same_inode(relative, metadata, after)
                    current = self._path_stat(parent, name, relative)
                    self._require_same_inode(relative, after, current)
                finally:
                    os.close(descriptor)

    def read_text(self, relative: str, maximum: int = MAX_TEXT_BYTES) -> tuple[str, os.stat_result]:
        with self.open_regular(relative) as (descriptor, metadata):
            if metadata.st_size < 0 or metadata.st_size > maximum:
                fail(f"{relative}: file exceeds the {maximum}-byte limit")
            chunks: list[bytes] = []
            total = 0
            while True:
                try:
                    chunk = os.read(descriptor, min(65536, maximum + 1 - total))
                except InterruptedError:
                    continue
                if not chunk:
                    break
                chunks.append(chunk)
                total += len(chunk)
                if total > maximum:
                    fail(f"{relative}: file exceeds the {maximum}-byte limit")
            after = os.fstat(descriptor)
            stable_fields = (
                "st_dev", "st_ino", "st_mode", "st_nlink", "st_uid",
                "st_gid", "st_size", "st_mtime_ns", "st_ctime_ns")
            if any(getattr(metadata, field) != getattr(after, field)
                   for field in stable_fields):
                fail(f"{relative}: metadata changed while reading")
            try:
                text = b"".join(chunks).decode("utf-8", errors="strict")
            except UnicodeDecodeError as error:
                fail(f"{relative}: is not strict UTF-8: byte {error.start}")
            if "\x00" in text:
                fail(f"{relative}: contains a NUL byte")
            return text, metadata

    def read_prefix(self, relative: str, length: int) -> tuple[bytes, os.stat_result]:
        if length <= 0 or length > 4096:
            fail(f"{relative}: invalid binary prefix length")
        with self.open_regular(relative) as (descriptor, metadata):
            chunks: list[bytes] = []
            remaining = length
            while remaining:
                try:
                    chunk = os.read(descriptor, remaining)
                except InterruptedError:
                    continue
                if not chunk:
                    break
                chunks.append(chunk)
                remaining -= len(chunk)
            return b"".join(chunks), metadata

    def list_directory(self, relative: str) -> tuple[list[str], os.stat_result]:
        with self.open_directory(relative) as (descriptor, metadata):
            try:
                names = os.listdir(descriptor)
            except OSError as error:
                fail(f"{relative}: cannot list directory: {error.strerror}")
            if any(not isinstance(name, str) for name in names):
                fail(f"{relative}: directory returned a non-text name")
            return names, metadata


def _owner(relative: str, metadata: os.stat_result,
           provider: Optional[OwnershipProvider]) -> tuple[int, int]:
    if provider is None:
        return metadata.st_uid, metadata.st_gid
    uid, gid = provider(relative, metadata)
    if (not isinstance(uid, int) or isinstance(uid, bool) or uid < 0 or
            not isinstance(gid, int) or isinstance(gid, bool) or gid < 0):
        fail(f"{relative}: test ownership provider returned invalid values")
    return uid, gid


def require_regular(relative: str, metadata: os.stat_result, mode: int,
                    uid: int, gid: int, provider: Optional[OwnershipProvider],
                    *, links: int = 1) -> None:
    if not stat.S_ISREG(metadata.st_mode):
        fail(f"{relative}: must be a regular file")
    actual_mode = stat.S_IMODE(metadata.st_mode)
    if actual_mode != mode:
        fail(f"{relative}: mode must be {mode:04o}, got {actual_mode:04o}")
    actual_uid, actual_gid = _owner(relative, metadata, provider)
    if (actual_uid, actual_gid) != (uid, gid):
        fail(f"{relative}: owner must be {uid}:{gid}, got "
             f"{actual_uid}:{actual_gid}")
    if metadata.st_nlink != links:
        fail(f"{relative}: link count must be {links}, got {metadata.st_nlink}")


def require_directory(relative: str, metadata: os.stat_result, mode: int,
                      uid: int, gid: int,
                      provider: Optional[OwnershipProvider],
                      *, links: Optional[int] = None) -> None:
    if not stat.S_ISDIR(metadata.st_mode):
        fail(f"{relative}: must be a directory")
    actual_mode = stat.S_IMODE(metadata.st_mode)
    if actual_mode != mode:
        fail(f"{relative}: mode must be {mode:04o}, got {actual_mode:04o}")
    actual_uid, actual_gid = _owner(relative, metadata, provider)
    if (actual_uid, actual_gid) != (uid, gid):
        fail(f"{relative}: owner must be {uid}:{gid}, got "
             f"{actual_uid}:{actual_gid}")
    if links is not None and metadata.st_nlink != links:
        fail(f"{relative}: link count must be {links}, got {metadata.st_nlink}")


def _canonical_uint(value: str, key: str, minimum: int,
                    maximum: int) -> int:
    if not re.fullmatch(r"0|[1-9][0-9]*", value):
        fail(f"{key}: must be a canonical unsigned decimal integer")
    parsed = int(value, 10)
    if parsed < minimum or parsed > maximum:
        fail(f"{key}: must be in [{minimum}, {maximum}]")
    return parsed


def _canonical_agent_id(value: str, key: str) -> str:
    if (len(value) > 32 or
            re.fullmatch(r"[a-z][a-z0-9-]*", value) is None):
        fail(f"{key} must be a canonical trust-domain Agent ID")
    return value


def _positive_decimal(value: str, key: str, maximum: str) -> Decimal:
    if not re.fullmatch(
            r"(?:[0-9]+(?:\.[0-9]*)?|\.[0-9]+)(?:[eE][+-]?[0-9]+)?",
            value):
        fail(f"{key}: must be a positive finite decimal")
    try:
        parsed = Decimal(value)
    except InvalidOperation:
        fail(f"{key}: must be a positive finite decimal")
    if not parsed.is_finite() or parsed <= 0 or parsed > Decimal(maximum):
        fail(f"{key}: must be positive and no greater than {maximum}")
    return parsed


def _parse_environment(text: str, relative: str,
                       expected_keys: frozenset[str]) -> dict[str, str]:
    values: dict[str, str] = {}
    for line_number, raw_line in enumerate(text.splitlines(), 1):
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if "=" not in line:
            fail(f"{relative}:{line_number}: invalid environment assignment")
        key, value = line.split("=", 1)
        if not re.fullmatch(r"[A-Z][A-Z0-9_]*", key):
            fail(f"{relative}:{line_number}: invalid environment key {key!r}")
        if value != value.strip() or not value:
            fail(f"{relative}:{line_number}: empty or padded environment value")
        if key in values:
            fail(f"{relative}:{line_number}: duplicate environment key {key!r}")
        values[key] = value
    if set(values) != expected_keys:
        missing = sorted(expected_keys - set(values))
        unexpected = sorted(set(values) - expected_keys)
        fail(f"{relative}: exact key allowlist mismatch; missing={missing}, "
             f"unexpected={unexpected}")
    return values


def _parse_identities(passwd_text: str, group_text: str) -> tuple[Identity, ...]:
    passwd_records: dict[str, tuple[int, int]] = {}
    passwd_names: set[str] = set()
    uid_names: dict[int, list[str]] = {}
    for line_number, raw_line in enumerate(passwd_text.splitlines(), 1):
        if not raw_line or raw_line.startswith("#"):
            continue
        fields = raw_line.split(":")
        if len(fields) != 7:
            fail(f"{PASSWD_PATH}:{line_number}: expected seven fields")
        name = fields[0]
        if not name or name in passwd_names:
            fail(f"{PASSWD_PATH}:{line_number}: empty or duplicate account name")
        passwd_names.add(name)
        uid = _canonical_uint(fields[2], f"{PASSWD_PATH}:{name}:uid", 0,
                              UINT32_MAX)
        gid = _canonical_uint(fields[3], f"{PASSWD_PATH}:{name}:gid", 0,
                              UINT32_MAX)
        uid_names.setdefault(uid, []).append(name)
        if name in IDENTITIES:
            if uid == 0 or gid == 0:
                fail(f"{PASSWD_PATH}:{name}: service UID/GID must be nonzero")
            passwd_records[name] = (uid, gid)

    group_records: dict[str, int] = {}
    group_names: set[str] = set()
    gid_names: dict[int, list[str]] = {}
    for line_number, raw_line in enumerate(group_text.splitlines(), 1):
        if not raw_line or raw_line.startswith("#"):
            continue
        fields = raw_line.split(":")
        if len(fields) != 4:
            fail(f"{GROUP_PATH}:{line_number}: expected four fields")
        name = fields[0]
        if not name or name in group_names:
            fail(f"{GROUP_PATH}:{line_number}: empty or duplicate group name")
        group_names.add(name)
        gid = _canonical_uint(
            fields[2], f"{GROUP_PATH}:{name}:gid", 0, UINT32_MAX)
        gid_names.setdefault(gid, []).append(name)
        members = fields[3].split(",") if fields[3] else []
        if any(not member for member in members):
            fail(f"{GROUP_PATH}:{line_number}: malformed supplementary members")
        inherited = sorted(set(members) & set(IDENTITIES))
        if inherited:
            fail(f"{GROUP_PATH}:{line_number}: service identities must not "
                 f"inherit supplementary groups: {inherited}")
        if name in IDENTITIES:
            if gid == 0:
                fail(f"{GROUP_PATH}:{name}: service GID must be nonzero")
            group_records[name] = gid

    missing_users = sorted(set(IDENTITIES) - set(passwd_records))
    missing_groups = sorted(set(IDENTITIES) - set(group_records))
    if missing_users or missing_groups:
        fail(f"required identities missing; users={missing_users}, "
             f"groups={missing_groups}")

    identities = tuple(
        Identity(name, passwd_records[name][0], passwd_records[name][1])
        for name in IDENTITIES
    )
    uids = [identity.uid for identity in identities]
    gids = [identity.gid for identity in identities]
    if len(set(uids)) != len(uids):
        fail("hepta service UIDs must be mutually distinct")
    if len(set(gids)) != len(gids):
        fail("hepta service GIDs must be mutually distinct")
    for identity in identities:
        if group_records[identity.name] != identity.gid:
            fail(f"{identity.name}: primary GID must resolve to its same-name group")
        uid_aliases = sorted(
            name for name in uid_names[identity.uid] if name != identity.name)
        if uid_aliases:
            fail(f"{identity.name}: UID {identity.uid} is aliased by "
                 f"{uid_aliases}")
        gid_aliases = sorted(
            name for name in gid_names[identity.gid] if name != identity.name)
        if gid_aliases:
            fail(f"{identity.name}: GID {identity.gid} is aliased by "
                 f"{gid_aliases}")
    return identities


def _validate_ib_environment(values: dict[str, str], gateway_uid: int) -> None:
    if values["HEPTA_IB_EXECUTION_MODE"] != "PAPER":
        fail("HEPTA_IB_EXECUTION_MODE must be exactly PAPER")
    account = values["HEPTA_IB_PAPER_ACCOUNT"]
    if (len(account) < 3 or len(account) > 18 or
            re.fullmatch(r"DU[0-9]+", account) is None):
        fail("HEPTA_IB_PAPER_ACCOUNT must be a strict DU account")
    if values["HEPTA_IB_PAPER_HOST"] not in ("127.0.0.1", "::1"):
        fail("HEPTA_IB_PAPER_HOST must be an IP loopback literal")
    port = _canonical_uint(values["HEPTA_IB_PAPER_PORT"],
                           "HEPTA_IB_PAPER_PORT", 1, 65535)
    if port not in (7497, 4002):
        fail("HEPTA_IB_PAPER_PORT must be a reviewed PAPER port (7497 or 4002)")
    _canonical_uint(values["HEPTA_IB_PAPER_CLIENT_ID"],
                    "HEPTA_IB_PAPER_CLIENT_ID", 1, 65535)
    _positive_decimal(values["HEPTA_IB_PAPER_MAX_ORDER_QTY"],
                      "HEPTA_IB_PAPER_MAX_ORDER_QTY", "25000")
    _positive_decimal(values["HEPTA_IB_PAPER_MAX_ORDER_NOTIONAL"],
                      "HEPTA_IB_PAPER_MAX_ORDER_NOTIONAL", "250000")
    _canonical_uint(values["HEPTA_IB_PAPER_MAX_ORDERS_PER_MINUTE"],
                    "HEPTA_IB_PAPER_MAX_ORDERS_PER_MINUTE", 1, 30)
    _canonical_uint(values["HEPTA_IB_PAPER_MAX_ACTIVE_ORDERS"],
                    "HEPTA_IB_PAPER_MAX_ACTIVE_ORDERS", 1, 50)
    _positive_decimal(values["HEPTA_IB_PAPER_MAX_GROSS_POSITION"],
                      "HEPTA_IB_PAPER_MAX_GROSS_POSITION", "100000")
    instruments: set[str] = set()
    records = values["HEPTA_IB_PAPER_QUOTE_CONTRACTS"].split(";")
    if not 1 <= len(records) <= 64:
        fail("HEPTA_IB_PAPER_QUOTE_CONTRACTS must contain 1 to 64 contracts")
    for record in records:
        fields = record.split("|")
        if len(fields) != 5:
            fail("HEPTA_IB_PAPER_QUOTE_CONTRACTS must use "
                 "instrument|symbol|CASH|exchange|currency records")
        instrument, symbol, security_type, exchange, currency = fields
        bounded = (
            (instrument, 128), (symbol, 64), (exchange, 32), (currency, 16))
        if any(re.fullmatch(r"[A-Za-z0-9._-]+", field) is None or
               len(field) > maximum for field, maximum in bounded):
            fail("HEPTA_IB_PAPER_QUOTE_CONTRACTS contains a "
                 "non-canonical contract field")
        if security_type != "CASH" or instrument != f"{symbol}.{currency}":
            fail("HEPTA_IB_PAPER_QUOTE_CONTRACTS must bind exact CASH "
                 "symbol.currency identities")
        if instrument in instruments:
            fail("HEPTA_IB_PAPER_QUOTE_CONTRACTS contains a duplicate instrument")
        instruments.add(instrument)
    primary = values["HEPTA_IB_PAPER_PRIMARY_QUOTE_INSTRUMENT"]
    if primary not in instruments:
        fail("HEPTA_IB_PAPER_PRIMARY_QUOTE_INSTRUMENT must select an exact "
             "reviewed quote contract")
    _canonical_uint(values["HEPTA_IB_PAPER_QUOTE_MAX_AGE_MS"],
                    "HEPTA_IB_PAPER_QUOTE_MAX_AGE_MS", 100, 60000)
    configured_gateway_uid = _canonical_uint(
        values["HEPTA_IB_EXECUTION_GATEWAY_UID"],
        "HEPTA_IB_EXECUTION_GATEWAY_UID", 1, UINT32_MAX)
    if configured_gateway_uid != gateway_uid:
        fail("HEPTA_IB_EXECUTION_GATEWAY_UID must exactly resolve to hepta-gateway")
    _canonical_agent_id(
        values["HEPTA_IB_EXECUTION_GATEWAY_AGENT_ID"],
        "HEPTA_IB_EXECUTION_GATEWAY_AGENT_ID")
    _canonical_uint(values["HEPTA_IB_EXECUTION_MAX_REQUEST_BYTES"],
                    "HEPTA_IB_EXECUTION_MAX_REQUEST_BYTES", 1024, 32768)
    _canonical_uint(values["HEPTA_IB_EXECUTION_IO_TIMEOUT_MS"],
                    "HEPTA_IB_EXECUTION_IO_TIMEOUT_MS", 1, 30000)
    _canonical_uint(values["HEPTA_IB_EXECUTION_READINESS_TIMEOUT_MS"],
                    "HEPTA_IB_EXECUTION_READINESS_TIMEOUT_MS", 100, 30000)
    reconnect_timeout = _canonical_uint(
        values["HEPTA_IB_EXECUTION_RECONNECT_TIMEOUT_MS"],
        "HEPTA_IB_EXECUTION_RECONNECT_TIMEOUT_MS", 1000, 300000)
    if reconnect_timeout < int(values["HEPTA_IB_EXECUTION_READINESS_TIMEOUT_MS"]):
        fail("HEPTA_IB_EXECUTION_RECONNECT_TIMEOUT_MS must be at least the "
             "readiness timeout")


def _validate_gateway_environment(values: dict[str, str], service_uid: int) -> None:
    if values["HEPTA_EXECUTION_REMOTE_MODE"] != "PAPER":
        fail("HEPTA_EXECUTION_REMOTE_MODE must be exactly PAPER")
    if values["HEPTA_EXECUTION_SOCKET"] != "/run/hepta-execution/execution.sock":
        fail("HEPTA_EXECUTION_SOCKET must use the fixed execution socket")
    if values["HEPTA_EXECUTION_EVENT_SOCKET"] != "/run/hepta-execution/events.sock":
        fail("HEPTA_EXECUTION_EVENT_SOCKET must use the fixed event socket")
    configured_service_uid = _canonical_uint(
        values["HEPTA_EXECUTION_SERVICE_UID"],
        "HEPTA_EXECUTION_SERVICE_UID", 1, UINT32_MAX)
    if configured_service_uid != service_uid:
        fail("HEPTA_EXECUTION_SERVICE_UID must exactly resolve to hepta-ib-exec")
    _canonical_uint(values["HEPTA_EXECUTION_IO_TIMEOUT_MS"],
                    "HEPTA_EXECUTION_IO_TIMEOUT_MS", 100, 30000)
    _canonical_uint(values["HEPTA_EXECUTION_MAX_RESPONSE_BYTES"],
                    "HEPTA_EXECUTION_MAX_RESPONSE_BYTES", 1024, 1048576)


def _validate_simulator_environment(values: dict[str, str], gateway_uid: int) -> None:
    configured_gateway_uid = _canonical_uint(
        values["HEPTA_EXECUTION_GATEWAY_UID"],
        "HEPTA_EXECUTION_GATEWAY_UID", 1, UINT32_MAX)
    if configured_gateway_uid != gateway_uid:
        fail("Simulator HEPTA_EXECUTION_GATEWAY_UID must exactly resolve "
             "to hepta-gateway")
    _canonical_agent_id(
        values["HEPTA_EXECUTION_GATEWAY_AGENT_ID"],
        "HEPTA_EXECUTION_GATEWAY_AGENT_ID")
    _canonical_uint(values["HEPTA_EXECUTION_MAX_REQUEST_BYTES"],
                    "HEPTA_EXECUTION_MAX_REQUEST_BYTES", 1024, 32768)
    _canonical_uint(values["HEPTA_EXECUTION_IO_TIMEOUT_MS"],
                    "HEPTA_EXECUTION_IO_TIMEOUT_MS", 1, 30000)


def _validate_tmpfiles(text: str, relative: str) -> None:
    directives = tuple(
        line.strip() for line in text.splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    )
    if directives != TMPFILES_DIRECTIVES:
        fail(f"{relative}: exact tmpfiles directives mismatch")


UnitSettings = dict[str, dict[str, list[str]]]
ExpectedUnitSettings = dict[str, dict[str, tuple[str, ...]]]


UNIT_DOCUMENTATION = (
    "file:/usr/share/doc/heptatrader/"
    "AGENT-NATIVE-TRADING-OS-ARCHITECTURE.md"
)

# This is intentionally an exact, per-unit contract rather than a subset of
# security-sensitive settings.  systemd has many directives that can add
# activation edges or execute helper programs; accepting an otherwise
# well-formed extra directive here would make the passive-install guarantee
# impossible to establish with this static gate.
CANONICAL_UNIT_SETTINGS: dict[str, ExpectedUnitSettings] = {
    "hepta-execution-simulator.service": {
        "Unit": {
            "Description": (
                "HeptaTrader Simulator-only execution authority",),
            "Documentation": (UNIT_DOCUMENTATION,),
            "Requires": (
                "hepta-execution-simulator.socket "
                "hepta-execution-events-simulator.socket",),
            "After": (
                "hepta-execution-simulator.socket "
                "hepta-execution-events-simulator.socket",),
            "Conflicts": (
                "hepta-execution-ib-paper.service "
                "hepta-execution-ib-paper.socket "
                "hepta-execution-events-ib-paper.socket",),
        },
        "Service": {
            "Type": ("simple",),
            "Sockets": (
                "hepta-execution-simulator.socket "
                "hepta-execution-events-simulator.socket",),
            "User": ("hepta-exec",),
            "Group": ("hepta-exec",),
            "WorkingDirectory": ("/",),
            "ExecStart": ("/usr/libexec/hepta-executiond",),
            "Environment": ("HEPTA_EXECUTION_SERVICE_MODE=SIMULATOR",),
            "EnvironmentFile": (
                "/etc/heptatrader/hepta-execution-simulator.env",),
            "LoadCredential": (
                "hepta-execution-fence:/etc/heptatrader/credentials/"
                "hepta-execution-simulator-fence",),
            "StateDirectory": ("hepta-execution",),
            "StateDirectoryMode": ("0700",),
            "UMask": ("0077",),
            "Restart": ("on-failure",),
            "RestartSec": ("2s",),
            "TimeoutStopSec": ("10s",),
            "KillMode": ("control-group",),
            "NoNewPrivileges": ("yes",),
            "PrivateTmp": ("yes",),
            "PrivateDevices": ("yes",),
            "PrivateNetwork": ("yes",),
            "ProtectSystem": ("strict",),
            "ProtectHome": ("yes",),
            "ProtectKernelTunables": ("yes",),
            "ProtectKernelModules": ("yes",),
            "ProtectKernelLogs": ("yes",),
            "ProtectControlGroups": ("yes",),
            "ProtectClock": ("yes",),
            "ProtectHostname": ("yes",),
            "ProtectProc": ("invisible",),
            "ProcSubset": ("pid",),
            "RestrictSUIDSGID": ("yes",),
            "RestrictRealtime": ("yes",),
            "RestrictNamespaces": ("yes",),
            "LockPersonality": ("yes",),
            "MemoryDenyWriteExecute": ("yes",),
            "RemoveIPC": ("yes",),
            "CapabilityBoundingSet": ("",),
            "AmbientCapabilities": ("",),
            "RestrictAddressFamilies": ("AF_UNIX",),
            "IPAddressDeny": ("any",),
            "SystemCallArchitectures": ("native",),
            "SystemCallFilter": ("@system-service",),
            "SystemCallErrorNumber": ("EPERM",),
            "ReadWritePaths": ("/var/lib/hepta-execution",),
            "StandardOutput": ("journal",),
            "StandardError": ("journal",),
        },
    },
    "hepta-execution-simulator.socket": {
        "Unit": {
            "Description": (
                "HeptaTrader Simulator-only execution authority socket",),
            "Documentation": (UNIT_DOCUMENTATION,),
            "Conflicts": (
                "hepta-execution-ib-paper.service "
                "hepta-execution-ib-paper.socket "
                "hepta-execution-events-ib-paper.socket",),
        },
        "Socket": {
            "ListenStream": ("/run/hepta-execution/execution.sock",),
            "Accept": ("no",),
            "Backlog": ("16",),
            "SocketUser": ("hepta-gateway",),
            "SocketGroup": ("hepta-gateway",),
            "SocketMode": ("0660",),
            "DirectoryMode": ("0755",),
            "FileDescriptorName": ("execution",),
            "Service": ("hepta-execution-simulator.service",),
            "RemoveOnStop": ("yes",),
        },
    },
    "hepta-execution-events-simulator.socket": {
        "Unit": {
            "Description": (
                "HeptaTrader Simulator-only execution event feed socket",),
            "Documentation": (UNIT_DOCUMENTATION,),
            "Conflicts": (
                "hepta-execution-ib-paper.service "
                "hepta-execution-ib-paper.socket "
                "hepta-execution-events-ib-paper.socket",),
        },
        "Socket": {
            "ListenStream": ("/run/hepta-execution/events.sock",),
            "Accept": ("no",),
            "Backlog": ("32",),
            "SocketUser": ("hepta-gateway",),
            "SocketGroup": ("hepta-gateway",),
            "SocketMode": ("0660",),
            "DirectoryMode": ("0755",),
            "FileDescriptorName": ("events",),
            "Service": ("hepta-execution-simulator.service",),
            "RemoveOnStop": ("yes",),
        },
    },
    "hepta-execution-simulator@.service": {
        "Unit": {
            "Description": (
                "HeptaTrader trust-domain Simulator execution authority (%i)",),
            "Documentation": (UNIT_DOCUMENTATION,),
            "Requires": (
                "hepta-execution-simulator@%i.socket "
                "hepta-execution-events-simulator@%i.socket",),
            "After": (
                "hepta-execution-simulator@%i.socket "
                "hepta-execution-events-simulator@%i.socket",),
            "Conflicts": (
                "hepta-execution-ib-paper.service "
                "hepta-execution-ib-paper.socket "
                "hepta-execution-events-ib-paper.socket "
                "hepta-execution-ib-paper@%i.service "
                "hepta-execution-ib-paper@%i.socket "
                "hepta-execution-events-ib-paper@%i.socket",),
        },
        "Service": {
            "Type": ("simple",),
            "Sockets": (
                "hepta-execution-simulator@%i.socket "
                "hepta-execution-events-simulator@%i.socket",),
            "User": ("hepta-exec-%i",),
            "Group": ("hepta-exec-%i",),
            "WorkingDirectory": ("/",),
            "ExecStart": ("/usr/libexec/hepta-executiond",),
            "Environment": ("HEPTA_EXECUTION_SERVICE_MODE=SIMULATOR",),
            "EnvironmentFile": (
                "/etc/heptatrader/trust-domains/%i.execution.env",),
            "LoadCredential": (
                "hepta-execution-fence:/etc/heptatrader/credentials/"
                "trust-domains/%i/hepta-execution-simulator-fence",),
            "StateDirectory": ("hepta-execution-%i",),
            "StateDirectoryMode": ("0700",),
            "UMask": ("0077",),
            "Restart": ("on-failure",),
            "RestartSec": ("2s",),
            "TimeoutStopSec": ("10s",),
            "KillMode": ("control-group",),
            "NoNewPrivileges": ("yes",),
            "PrivateTmp": ("yes",),
            "PrivateDevices": ("yes",),
            "PrivateNetwork": ("yes",),
            "ProtectSystem": ("strict",),
            "ProtectHome": ("yes",),
            "ProtectKernelTunables": ("yes",),
            "ProtectKernelModules": ("yes",),
            "ProtectKernelLogs": ("yes",),
            "ProtectControlGroups": ("yes",),
            "ProtectClock": ("yes",),
            "ProtectHostname": ("yes",),
            "ProtectProc": ("invisible",),
            "ProcSubset": ("pid",),
            "RestrictSUIDSGID": ("yes",),
            "RestrictRealtime": ("yes",),
            "RestrictNamespaces": ("yes",),
            "LockPersonality": ("yes",),
            "MemoryDenyWriteExecute": ("yes",),
            "RemoveIPC": ("yes",),
            "CapabilityBoundingSet": ("",),
            "AmbientCapabilities": ("",),
            "RestrictAddressFamilies": ("AF_UNIX",),
            "IPAddressDeny": ("any",),
            "SystemCallArchitectures": ("native",),
            "SystemCallFilter": ("@system-service",),
            "SystemCallErrorNumber": ("EPERM",),
            "ReadWritePaths": ("/var/lib/hepta-execution-%i",),
            "StandardOutput": ("journal",),
            "StandardError": ("journal",),
        },
    },
    "hepta-execution-simulator@.socket": {
        "Unit": {
            "Description": (
                "HeptaTrader trust-domain Simulator execution socket (%i)",),
            "Documentation": (UNIT_DOCUMENTATION,),
            "Conflicts": (
                "hepta-execution-ib-paper.service "
                "hepta-execution-ib-paper.socket "
                "hepta-execution-events-ib-paper.socket "
                "hepta-execution-ib-paper@%i.service "
                "hepta-execution-ib-paper@%i.socket "
                "hepta-execution-events-ib-paper@%i.socket",),
        },
        "Socket": {
            "ListenStream": (
                "/run/hepta-execution-%i/execution.sock",),
            "Accept": ("no",),
            "Backlog": ("16",),
            "SocketUser": ("hepta-gw-%i",),
            "SocketGroup": ("hepta-gw-%i",),
            "SocketMode": ("0600",),
            "DirectoryMode": ("0711",),
            "FileDescriptorName": ("execution",),
            "Service": ("hepta-execution-simulator@%i.service",),
            "RemoveOnStop": ("yes",),
        },
    },
    "hepta-execution-events-simulator@.socket": {
        "Unit": {
            "Description": (
                "HeptaTrader trust-domain Simulator event feed socket (%i)",),
            "Documentation": (UNIT_DOCUMENTATION,),
            "Conflicts": (
                "hepta-execution-ib-paper.service "
                "hepta-execution-ib-paper.socket "
                "hepta-execution-events-ib-paper.socket "
                "hepta-execution-ib-paper@%i.service "
                "hepta-execution-ib-paper@%i.socket "
                "hepta-execution-events-ib-paper@%i.socket",),
        },
        "Socket": {
            "ListenStream": ("/run/hepta-execution-%i/events.sock",),
            "Accept": ("no",),
            "Backlog": ("32",),
            "SocketUser": ("hepta-gw-%i",),
            "SocketGroup": ("hepta-gw-%i",),
            "SocketMode": ("0600",),
            "DirectoryMode": ("0711",),
            "FileDescriptorName": ("events",),
            "Service": ("hepta-execution-simulator@%i.service",),
            "RemoveOnStop": ("yes",),
        },
    },
    "hepta-execution-ib-paper.service": {
        "Unit": {
            "Description": ("HeptaTrader IB PAPER execution authority",),
            "Documentation": (UNIT_DOCUMENTATION,),
            "Requires": (
                "hepta-execution-ib-paper.socket "
                "hepta-execution-events-ib-paper.socket",),
            "After": (
                "hepta-execution-ib-paper.socket "
                "hepta-execution-events-ib-paper.socket network.target",),
            "Conflicts": (
                "hepta-execution-simulator.service "
                "hepta-execution-simulator.socket "
                "hepta-execution-events-simulator.socket",),
        },
        "Service": {
            "Type": ("simple",),
            "Sockets": (
                "hepta-execution-ib-paper.socket "
                "hepta-execution-events-ib-paper.socket",),
            "User": ("hepta-ib-exec",),
            "Group": ("hepta-ib-exec",),
            "WorkingDirectory": ("/",),
            "ExecStart": ("/usr/libexec/hepta-ib-executiond",),
            "Environment": (
                "HEPTA_IB_PAPER_CONTROL_DIRECTORY="
                "/run/hepta/ib-paper-control",),
            "EnvironmentFile": (
                "/etc/heptatrader/hepta-execution-ib-paper.env",),
            "LoadCredential": (
                "hepta-execution-fence:/etc/heptatrader/credentials/"
                "hepta-execution-ib-paper-fence",
                "hepta-ib-paper-authorization:/etc/heptatrader/credentials/"
                "hepta-ib-paper-authorization",
                "hepta-fx-cash-baseline:/etc/heptatrader/credentials/"
                "hepta-fx-cash-baseline"),
            "StateDirectory": ("hepta-ib-execution",),
            "StateDirectoryMode": ("0700",),
            "UMask": ("0077",),
            "Restart": ("on-failure",),
            "RestartSec": ("2s",),
            "TimeoutStartSec": ("240s",),
            "TimeoutStopSec": ("35s",),
            "KillMode": ("control-group",),
            "NoNewPrivileges": ("yes",),
            "PrivateTmp": ("yes",),
            "PrivateDevices": ("yes",),
            "ProtectSystem": ("strict",),
            "ProtectHome": ("yes",),
            "ProtectKernelTunables": ("yes",),
            "ProtectKernelModules": ("yes",),
            "ProtectKernelLogs": ("yes",),
            "ProtectControlGroups": ("yes",),
            "ProtectClock": ("yes",),
            "ProtectHostname": ("yes",),
            "ProtectProc": ("invisible",),
            "ProcSubset": ("pid",),
            "RestrictSUIDSGID": ("yes",),
            "RestrictRealtime": ("yes",),
            "RestrictNamespaces": ("yes",),
            "LockPersonality": ("yes",),
            "MemoryDenyWriteExecute": ("yes",),
            "RemoveIPC": ("yes",),
            "CapabilityBoundingSet": ("",),
            "AmbientCapabilities": ("",),
            "RestrictAddressFamilies": ("AF_UNIX AF_INET AF_INET6",),
            "IPAddressDeny": ("any",),
            "IPAddressAllow": ("127.0.0.0/8", "::1/128"),
            "SystemCallArchitectures": ("native",),
            "SystemCallFilter": ("@system-service",),
            "SystemCallErrorNumber": ("EPERM",),
            "ReadWritePaths": ("/var/lib/hepta-ib-execution",),
            "ReadOnlyPaths": ("/run/hepta/ib-paper-control",),
            "StandardOutput": ("journal",),
            "StandardError": ("journal",),
        },
    },
    "hepta-execution-ib-paper.socket": {
        "Unit": {
            "Description": (
                "HeptaTrader IB PAPER execution authority socket",),
            "Documentation": (UNIT_DOCUMENTATION,),
            "Conflicts": (
                "hepta-execution-simulator.service "
                "hepta-execution-simulator.socket "
                "hepta-execution-events-simulator.socket",),
            "PartOf": ("hepta-execution-ib-paper.service",),
        },
        "Socket": {
            "ListenStream": ("/run/hepta-execution/execution.sock",),
            "Accept": ("no",),
            "Backlog": ("16",),
            "SocketUser": ("hepta-gateway",),
            "SocketGroup": ("hepta-gateway",),
            "SocketMode": ("0660",),
            "DirectoryMode": ("0755",),
            "FileDescriptorName": ("execution",),
            "Service": ("hepta-execution-ib-paper.service",),
            "RemoveOnStop": ("yes",),
        },
    },
    "hepta-execution-events-ib-paper.socket": {
        "Unit": {
            "Description": (
                "HeptaTrader IB PAPER execution event feed socket",),
            "Documentation": (UNIT_DOCUMENTATION,),
            "Conflicts": (
                "hepta-execution-simulator.service "
                "hepta-execution-simulator.socket "
                "hepta-execution-events-simulator.socket",),
            "PartOf": ("hepta-execution-ib-paper.service",),
        },
        "Socket": {
            "ListenStream": ("/run/hepta-execution/events.sock",),
            "Accept": ("no",),
            "Backlog": ("32",),
            "SocketUser": ("hepta-gateway",),
            "SocketGroup": ("hepta-gateway",),
            "SocketMode": ("0660",),
            "DirectoryMode": ("0755",),
            "FileDescriptorName": ("events",),
            "Service": ("hepta-execution-ib-paper.service",),
            "RemoveOnStop": ("yes",),
        },
    },
    "hepta-execution-ib-paper@.service": {
        "Unit": {
            "Description": (
                "HeptaTrader trust-domain IB PAPER execution authority (%i)",),
            "Documentation": (UNIT_DOCUMENTATION,),
            "StartLimitIntervalSec": ("1800s",),
            "StartLimitBurst": ("5",),
            "Requires": (
                "hepta-execution-ib-paper@%i.socket "
                "hepta-execution-events-ib-paper@%i.socket",),
            "BindsTo": (
                "hepta-ib-paper-domain-preflight@%i.service",),
            "After": (
                "hepta-execution-ib-paper@%i.socket "
                "hepta-execution-events-ib-paper@%i.socket "
                "hepta-ib-paper-domain-preflight@%i.service network.target",),
            "Conflicts": (
                "hepta-execution-simulator@%i.service "
                "hepta-execution-simulator@%i.socket "
                "hepta-execution-events-simulator@%i.socket "
                "hepta-execution-ib-paper.service "
                "hepta-execution-ib-paper.socket "
                "hepta-execution-events-ib-paper.socket",),
        },
        "Service": {
            "Type": ("simple",),
            "Sockets": (
                "hepta-execution-ib-paper@%i.socket "
                "hepta-execution-events-ib-paper@%i.socket",),
            "User": ("hepta-ib-exec-%i",),
            "Group": ("hepta-ib-exec-%i",),
            "WorkingDirectory": ("/",),
            "ExecStart": ("/usr/libexec/hepta-ib-executiond",),
            "Environment": (
                "HEPTA_IB_PAPER_CONTROL_DIRECTORY="
                "/run/hepta/ib-paper-control-%i",),
            "EnvironmentFile": (
                "/etc/heptatrader/trust-domains/%i.ib-paper.env",),
            "LoadCredential": (
                "hepta-execution-fence:/etc/heptatrader/credentials/"
                "trust-domains/%i/hepta-execution-ib-paper-fence",
                "hepta-ib-paper-authorization:/etc/heptatrader/credentials/"
                "trust-domains/%i/hepta-ib-paper-authorization",
                "hepta-fx-cash-baseline:/etc/heptatrader/credentials/"
                "trust-domains/%i/hepta-fx-cash-baseline"),
            "StateDirectory": ("hepta-ib-execution-%i",),
            "StateDirectoryMode": ("0700",),
            "UMask": ("0077",),
            "Restart": ("on-failure",),
            "RestartPreventExitStatus": ("9",),
            "RestartSec": ("2s",),
            "TimeoutStartSec": ("240s",),
            "TimeoutStopSec": ("35s",),
            "KillMode": ("control-group",),
            "NoNewPrivileges": ("yes",),
            "PrivateTmp": ("yes",),
            "PrivateDevices": ("yes",),
            "ProtectSystem": ("strict",),
            "ProtectHome": ("yes",),
            "ProtectKernelTunables": ("yes",),
            "ProtectKernelModules": ("yes",),
            "ProtectKernelLogs": ("yes",),
            "ProtectControlGroups": ("yes",),
            "ProtectClock": ("yes",),
            "ProtectHostname": ("yes",),
            "ProtectProc": ("invisible",),
            "ProcSubset": ("pid",),
            "RestrictSUIDSGID": ("yes",),
            "RestrictRealtime": ("yes",),
            "RestrictNamespaces": ("yes",),
            "LockPersonality": ("yes",),
            "MemoryDenyWriteExecute": ("yes",),
            "RemoveIPC": ("yes",),
            "CapabilityBoundingSet": ("",),
            "AmbientCapabilities": ("",),
            "RestrictAddressFamilies": ("AF_UNIX AF_INET AF_INET6",),
            "IPAddressDeny": ("any",),
            "IPAddressAllow": ("127.0.0.0/8", "::1/128"),
            "SystemCallArchitectures": ("native",),
            "SystemCallFilter": ("@system-service",),
            "SystemCallErrorNumber": ("EPERM",),
            "ReadWritePaths": ("/var/lib/hepta-ib-execution-%i",),
            "ReadOnlyPaths": ("/run/hepta/ib-paper-control-%i",),
            "StandardOutput": ("journal",),
            "StandardError": ("journal",),
        },
    },
    "hepta-execution-ib-paper@.socket": {
        "Unit": {
            "Description": (
                "HeptaTrader trust-domain IB PAPER execution socket (%i)",),
            "Documentation": (UNIT_DOCUMENTATION,),
            "Conflicts": (
                "hepta-execution-simulator@%i.service "
                "hepta-execution-simulator@%i.socket "
                "hepta-execution-events-simulator@%i.socket "
                "hepta-execution-ib-paper.service "
                "hepta-execution-ib-paper.socket "
                "hepta-execution-events-ib-paper.socket",),
            "BindsTo": (
                "hepta-ib-paper-domain-preflight@%i.service",),
            "After": (
                "hepta-ib-paper-domain-preflight@%i.service",),
            "PartOf": ("hepta-execution-ib-paper@%i.service",),
            "StopWhenUnneeded": ("yes",),
            "RefuseManualStart": ("yes",),
        },
        "Socket": {
            "ListenStream": ("/run/hepta-execution-%i/execution.sock",),
            "Accept": ("no",),
            "Backlog": ("16",),
            "SocketUser": ("hepta-gw-%i",),
            "SocketGroup": ("hepta-gw-%i",),
            "SocketMode": ("0600",),
            "DirectoryMode": ("0711",),
            "FileDescriptorName": ("execution",),
            "Service": ("hepta-execution-ib-paper@%i.service",),
            "RemoveOnStop": ("yes",),
        },
    },
    "hepta-execution-events-ib-paper@.socket": {
        "Unit": {
            "Description": (
                "HeptaTrader trust-domain IB PAPER event feed socket (%i)",),
            "Documentation": (UNIT_DOCUMENTATION,),
            "Conflicts": (
                "hepta-execution-simulator@%i.service "
                "hepta-execution-simulator@%i.socket "
                "hepta-execution-events-simulator@%i.socket "
                "hepta-execution-ib-paper.service "
                "hepta-execution-ib-paper.socket "
                "hepta-execution-events-ib-paper.socket",),
            "BindsTo": (
                "hepta-ib-paper-domain-preflight@%i.service",),
            "After": (
                "hepta-ib-paper-domain-preflight@%i.service",),
            "PartOf": ("hepta-execution-ib-paper@%i.service",),
            "StopWhenUnneeded": ("yes",),
            "RefuseManualStart": ("yes",),
        },
        "Socket": {
            "ListenStream": ("/run/hepta-execution-%i/events.sock",),
            "Accept": ("no",),
            "Backlog": ("32",),
            "SocketUser": ("hepta-gw-%i",),
            "SocketGroup": ("hepta-gw-%i",),
            "SocketMode": ("0600",),
            "DirectoryMode": ("0711",),
            "FileDescriptorName": ("events",),
            "Service": ("hepta-execution-ib-paper@%i.service",),
            "RemoveOnStop": ("yes",),
        },
    },
    "hepta-ib-paper-domain-preflight@.service": {
        "Unit": {
            "Description": (
                "HeptaTrader per-domain IB PAPER authority preflight (%i)",),
            "Documentation": (
                "file:/usr/share/doc/heptatrader/"
                "BROKER-NETWORK-ISOLATION.md",),
            "StartLimitIntervalSec": ("1800s",),
            "StartLimitBurst": ("5",),
            "BindsTo": (
                "hepta-broker-egress-policy.service "
                "hepta-execution-ib-paper@%i.service",),
            "After": ("hepta-broker-egress-policy.service",),
            "Before": (
                "hepta-execution-ib-paper@%i.service "
                "hepta-execution-ib-paper@%i.socket "
                "hepta-execution-events-ib-paper@%i.socket",),
            "PartOf": ("hepta-execution-ib-paper@%i.service",),
            "StopWhenUnneeded": ("yes",),
            "RefuseManualStart": ("yes",),
        },
        "Service": {
            "Type": ("notify",),
            "NotifyAccess": ("main",),
            "User": ("root",),
            "Group": ("root",),
            "ExecStart": (
                "/usr/libexec/hepta-ib-paper-domain-authority "
                "--guard --domain %i",),
            "ExecStopPost": (
                "/usr/libexec/hepta-ib-paper-domain-authority "
                "--finalize-stop --domain %i",),
            "UMask": ("0077",),
            "WatchdogSec": ("15s",),
            "TimeoutStopSec": ("30s",),
            "WatchdogSignal": ("SIGTERM",),
            "KillSignal": ("SIGTERM",),
            "Restart": ("no",),
            "RuntimeDirectory": ("hepta/ib-paper-host-authority",),
            "RuntimeDirectoryMode": ("0700",),
            "RuntimeDirectoryPreserve": ("yes",),
            "NoNewPrivileges": ("yes",),
            "PrivateTmp": ("yes",),
            "PrivateDevices": ("yes",),
            "ProtectSystem": ("strict",),
            "ProtectHome": ("yes",),
            "ProtectKernelTunables": ("yes",),
            "ProtectKernelModules": ("yes",),
            "ProtectKernelLogs": ("yes",),
            "ProtectControlGroups": ("yes",),
            "ProtectClock": ("yes",),
            "ProtectHostname": ("yes",),
            "RestrictSUIDSGID": ("yes",),
            "RestrictRealtime": ("yes",),
            "RestrictNamespaces": ("yes",),
            "LockPersonality": ("yes",),
            "MemoryDenyWriteExecute": ("yes",),
            "CapabilityBoundingSet": ("CAP_NET_ADMIN",),
            "AmbientCapabilities": ("",),
            "RestrictAddressFamilies": ("AF_UNIX AF_NETLINK",),
            "ReadOnlyPaths": (
                "/usr/share/heptatrader /etc/heptatrader /run/hepta",),
            "ReadWritePaths": (
                "/run/hepta/ib-paper-host-authority",),
            "StandardOutput": ("journal",),
            "StandardError": ("journal",),
        },
    },
}


def _parse_unit_settings(text: str, relative: str) -> UnitSettings:
    sections: UnitSettings = {}
    current = ""
    for line_number, raw_line in enumerate(text.splitlines(), 1):
        line = raw_line.strip()
        if not line or line.startswith("#") or line.startswith(";"):
            continue
        if line.startswith("[") and line.endswith("]"):
            section = line[1:-1]
            if re.fullmatch(r"[A-Za-z][A-Za-z0-9]*", section) is None:
                fail(f"{relative}:{line_number}: invalid unit section")
            if section in sections:
                fail(f"{relative}:{line_number}: duplicate unit section "
                     f"[{section}]")
            sections[section] = {}
            current = section
            continue
        if not current or "=" not in line:
            fail(f"{relative}:{line_number}: invalid unit directive")
        key, value = line.split("=", 1)
        if re.fullmatch(r"[A-Za-z][A-Za-z0-9]*", key) is None:
            fail(f"{relative}:{line_number}: invalid unit directive name")
        sections[current].setdefault(key, []).append(value)
    return sections


def _require_unit_value(settings: UnitSettings, relative: str, section: str,
                        key: str, expected: tuple[str, ...]) -> None:
    observed = tuple(settings.get(section, {}).get(key, []))
    if observed != expected:
        fail(f"{relative}: [{section}] {key} must be exactly "
             f"{list(expected)}, got {list(observed)}")


def _validate_installed_unit(name: str, text: str, relative: str) -> None:
    settings = _parse_unit_settings(text, relative)
    if "Install" in settings:
        fail(f"{relative}: must not contain an [Install] section")
    expected = CANONICAL_UNIT_SETTINGS.get(name)
    if expected is None:
        fail(f"{relative}: no canonical exact unit contract for {name!r}")

    observed_sections = set(settings)
    expected_sections = set(expected)
    if observed_sections != expected_sections:
        missing = sorted(expected_sections - observed_sections)
        unexpected = sorted(observed_sections - expected_sections)
        fail(f"{relative}: exact unit section allowlist mismatch; "
             f"missing={missing}, unexpected={unexpected}")

    for section, expected_directives in expected.items():
        observed_directives = settings[section]
        observed_keys = set(observed_directives)
        expected_keys = set(expected_directives)
        if observed_keys != expected_keys:
            missing = sorted(expected_keys - observed_keys)
            unexpected = sorted(observed_keys - expected_keys)
            fail(f"{relative}: [{section}] exact directive allowlist mismatch; "
                 f"missing={missing}, unexpected={unexpected}")
        for key, expected_values in expected_directives.items():
            _require_unit_value(
                settings, relative, section, key, expected_values)


def _legacy_unit(name: str) -> bool:
    if re.fullmatch(r"[^/]+\.(?:service|socket|target|timer|path)", name) is None:
        return False
    if name in EXPLICIT_LEGACY_UNITS:
        return True
    if name.startswith(("hepta-openclaw-", "hepta-ib-scalping")):
        return True
    return name.startswith("hepta-execution-") and name not in CANONICAL_UNITS


def _validate_no_systemd_overrides(reader: RootReader, relative: str) -> None:
    names, _ = reader.list_directory(relative)
    canonical_dropins = {name + ".d" for name in CANONICAL_UNITS}
    forbidden = sorted(
        name for name in names
        if name in CANONICAL_UNITS or name in canonical_dropins or
        _legacy_unit(name))
    if forbidden:
        fail(f"{relative}: execution unit overrides/legacy units present: "
             f"{forbidden}")

    # Static units deliberately have no [Install], so explicit wants/requires
    # links are also a provisioning contract violation. Inspect only real
    # directories; an unrelated symlink is not followed.
    for name in sorted(names):
        if not name.endswith((".wants", ".requires")):
            continue
        child_relative = relative + "/" + name
        metadata = reader.lstat(child_relative)
        if not stat.S_ISDIR(metadata.st_mode):
            continue
        children, _ = reader.list_directory(child_relative)
        activated = sorted(
            child for child in children
            if child in CANONICAL_UNITS or _legacy_unit(child))
        if activated:
            fail(f"{child_relative}: execution units must not be statically "
                 f"activated: {activated}")


def validate_root(
        root: Path,
        *,
        _ownership_provider_for_tests: Optional[OwnershipProvider] = None,
) -> ValidationReport:
    """Validate one already provisioned root without changing it.

    The private ownership-provider seam exists only so unprivileged fixture
    tests can model root-owned inodes.  The command-line production path never
    accepts or installs such a provider.
    """
    with RootReader(root) as reader:
        for relative in SAFE_ROOT_DIRECTORIES:
            with reader.open_directory(relative) as (_, metadata):
                actual_uid, actual_gid = _owner(
                    relative, metadata, _ownership_provider_for_tests)
                if (actual_uid, actual_gid) != (0, 0):
                    fail(f"{relative}: security ancestor owner must be 0:0")
                if stat.S_IMODE(metadata.st_mode) & 0o022:
                    fail(f"{relative}: security ancestor must not be "
                         "group/world writable")

        for relative in SYSTEMD_OVERRIDE_DIRECTORIES:
            _validate_no_systemd_overrides(reader, relative)

        manifest_text, manifest_metadata = reader.read_text(
            IDENTITY_MANIFEST_PATH, 65536)
        require_regular(IDENTITY_MANIFEST_PATH, manifest_metadata, 0o644, 0, 0,
                        _ownership_provider_for_tests)
        try:
            identity_manifest = parse_identity_manifest(
                manifest_text.encode("utf-8", errors="strict"))
        except ValueError as error:
            fail(f"{IDENTITY_MANIFEST_PATH}: {error}")

        passwd_text, passwd_metadata = reader.read_text(PASSWD_PATH)
        require_regular(PASSWD_PATH, passwd_metadata, 0o644, 0, 0,
                        _ownership_provider_for_tests)
        group_text, group_metadata = reader.read_text(GROUP_PATH)
        require_regular(GROUP_PATH, group_metadata, 0o644, 0, 0,
                        _ownership_provider_for_tests)
        identities = _parse_identities(passwd_text, group_text)
        by_name = {identity.name: identity for identity in identities}
        for name, expected in identity_manifest["identities"].items():
            observed = by_name[name]
            if (observed.uid, observed.gid) != (expected["uid"], expected["gid"]):
                fail(f"{name}: UID/GID must match the versioned identity manifest")

        ib_text, ib_metadata = reader.read_text(IB_ENV_PATH, 65536)
        require_regular(IB_ENV_PATH, ib_metadata, 0o644, 0, 0,
                        _ownership_provider_for_tests)
        ib_values = _parse_environment(ib_text, IB_ENV_PATH, IB_ENV_KEYS)
        _validate_ib_environment(ib_values, by_name["hepta-gateway"].uid)

        gateway_text, gateway_metadata = reader.read_text(
            GATEWAY_ENV_PATH, 65536)
        require_regular(GATEWAY_ENV_PATH, gateway_metadata, 0o644, 0, 0,
                        _ownership_provider_for_tests)
        gateway_values = _parse_environment(
            gateway_text, GATEWAY_ENV_PATH, GATEWAY_ENV_KEYS)
        _validate_gateway_environment(
            gateway_values, by_name["hepta-ib-exec"].uid)

        simulator_text, simulator_metadata = reader.read_text(
            SIMULATOR_ENV_PATH, 65536)
        require_regular(SIMULATOR_ENV_PATH, simulator_metadata, 0o644, 0, 0,
                        _ownership_provider_for_tests)
        simulator_values = _parse_environment(
            simulator_text, SIMULATOR_ENV_PATH, SIMULATOR_ENV_KEYS)
        _validate_simulator_environment(
            simulator_values, by_name["hepta-gateway"].uid)

        with reader.open_directory(CREDENTIAL_DIRECTORY) as (_, credential_dir):
            require_directory(
                CREDENTIAL_DIRECTORY, credential_dir, 0o700, 0, 0,
                _ownership_provider_for_tests, links=2)

        for credential in (
                SIMULATOR_FENCE_PATH, FENCE_PATH, AUTHORIZATION_PATH,
                FX_CASH_BASELINE_PATH):
            metadata = reader.lstat(credential)
            require_regular(credential, metadata, 0o400, 0, 0,
                            _ownership_provider_for_tests)
            if metadata.st_size < 1 or metadata.st_size > 256:
                fail(f"{credential}: size must be in [1, 256] bytes")

        with reader.open_directory(CONTROL_DIRECTORY) as (_, control_metadata):
            require_directory(
                CONTROL_DIRECTORY, control_metadata, 0o750, 0,
                by_name["hepta-ib-exec"].gid,
                _ownership_provider_for_tests, links=2)
        marker_metadata = reader.lstat(KILL_SWITCH_MARKER)
        require_regular(
            KILL_SWITCH_MARKER, marker_metadata, 0o440, 0,
            by_name["hepta-ib-exec"].gid,
            _ownership_provider_for_tests)
        if marker_metadata.st_dev != control_metadata.st_dev:
            fail(f"{KILL_SWITCH_MARKER}: must be on the control directory device")

        unit_names, _ = reader.list_directory(UNIT_DIRECTORY)
        legacy = sorted(name for name in unit_names if _legacy_unit(name))
        if legacy:
            fail(f"{UNIT_DIRECTORY}: legacy/noncanonical units present: {legacy}")
        missing_units = sorted(CANONICAL_UNITS - set(unit_names))
        if missing_units:
            fail(f"{UNIT_DIRECTORY}: canonical units missing: {missing_units}")
        for name in sorted(CANONICAL_UNITS):
            relative = UNIT_DIRECTORY + "/" + name
            text, metadata = reader.read_text(relative, 262144)
            require_regular(relative, metadata, 0o644, 0, 0,
                            _ownership_provider_for_tests)
            _validate_installed_unit(name, text, relative)

        tmpfiles_text, tmpfiles_metadata = reader.read_text(
            TMPFILES_PATH, 65536)
        require_regular(TMPFILES_PATH, tmpfiles_metadata, 0o644, 0, 0,
                        _ownership_provider_for_tests)
        _validate_tmpfiles(tmpfiles_text, TMPFILES_PATH)

        for executable in EXECUTION_BINARIES:
            prefix, metadata = reader.read_prefix(executable, 4)
            require_regular(executable, metadata, 0o755, 0, 0,
                            _ownership_provider_for_tests)
            if metadata.st_size < 64 or metadata.st_size > 256 * 1024 * 1024:
                fail(f"{executable}: ELF size outside the reviewed range")
            if prefix != b"\x7fELF":
                fail(f"{executable}: must be an ELF executable")

    return ValidationReport(
        root=Path(os.path.abspath(os.fspath(root))),
        identities=identities,
        canonical_unit_count=len(CANONICAL_UNITS),
        executable_count=len(EXECUTION_BINARIES),
        kill_switch_engaged=True,
    )


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description="strict read-only Hepta execution provisioned-host preflight")
    parser.add_argument(
        "--root", type=Path, default=Path("/"),
        help="filesystem root to inspect (default: /; never modified)")
    args = parser.parse_args(argv)
    try:
        report = validate_root(args.root)
    except ValidationError as error:
        print(f"hepta_execution_provisioned_host: FAIL: {error}", file=sys.stderr)
        return 1
    except Exception as error:  # A surprising condition must also fail closed.
        print("hepta_execution_provisioned_host: FAIL: unexpected "
              f"{type(error).__name__}: {error}", file=sys.stderr)
        return 1
    print("hepta_execution_provisioned_host: PASS "
          f"root={report.root} identities={len(report.identities)} "
          f"units={report.canonical_unit_count} "
          f"executables={report.executable_count} kill_switch=engaged")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
