#!/usr/bin/env python3

"""Create the inert alpha PAPER kill switch without creating PAPER authority.

This is deliberately narrower than ``hepta_ib_paper_domain_authority``.  It
can only establish the default-engaged marker required by the pre-PAPER P1
observers.  It cannot render an authority manifest, install credentials,
control a unit, reach a broker, or disarm the marker.
"""

from __future__ import annotations

import argparse
import ctypes
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
import stat
import sys
import time
from typing import Any, Callable


SCHEMA = "hepta.p1-paper-kill-switch-bootstrap-receipt.v1"
JOURNAL_SCHEMA = "hepta.p1-paper-kill-switch-bootstrap-journal.v1"
VERSION = 1
ROUND = 114
DOMAIN = "alpha"
IDENTITY = "hepta-ib-exec-alpha"
MARKER_BYTES = b"engaged"
INSTALLED_EXECUTABLE = Path(
    "/usr/libexec/hepta-p1-paper-kill-switch-bootstrap")
RUNTIME_ANCHOR = Path("/run/hepta")
CONTROL_NAME = "ib-paper-control-alpha"
STATE_NAME = "p1-paper-kill-switch-bootstrap-alpha"
CONTROL_PATH = RUNTIME_ANCHOR / CONTROL_NAME
MARKER_PATH = CONTROL_PATH / "kill-switch"
STATE_PATH = RUNTIME_ANCHOR / STATE_NAME
LOCK_NAME = "transaction.lock"
JOURNAL_NAME = "journal.v1.json"
RECEIPT_NAME = "receipt.v1.json"
JOURNAL_TEMP = ".journal.tmp"
MARKER_TEMP = ".marker.tmp"
RECEIPT_TEMP = ".receipt.tmp"
BOOT_ID_PATH = Path("/proc/sys/kernel/random/boot_id")
MAX_DOCUMENT_BYTES = 128 * 1024
SHA256 = re.compile(r"sha256:[0-9a-f]{64}")
PLAIN_SHA256 = re.compile(r"[0-9a-f]{64}")
BOOT_ID = re.compile(
    r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-"
    r"[0-9a-f]{4}-[0-9a-f]{12}")
JOURNAL_STATES = (
    "STARTED", "DIRECTORY_READY", "MARKER_ENGAGED",
    "RECEIPT_PUBLISHED", "COMPLETE",
)
RENAME_NOREPLACE = 1


class BootstrapError(RuntimeError):
    pass


@dataclass(frozen=True)
class BootstrapPaths:
    anchor: Path
    control_name: str = CONTROL_NAME
    state_name: str = STATE_NAME

    @property
    def control(self) -> Path:
        return self.anchor / self.control_name

    @property
    def marker(self) -> Path:
        return self.control / "kill-switch"

    @property
    def state(self) -> Path:
        return self.anchor / self.state_name


@dataclass(frozen=True)
class ProducerEvidence:
    path: str
    sha256: str
    mode: int
    uid: int
    gid: int


@dataclass(frozen=True)
class PaperIdentity:
    name: str
    uid: int
    gid: int
    home: str
    shell: str
    supplementary_gids: tuple[int, ...]


def _fault(stage: str) -> None:
    """In-process-only fault seam used by the offline tests."""
    del stage


def _require(condition: bool, reason: str) -> None:
    if not condition:
        raise BootstrapError(reason)


def _canonical(value: object) -> bytes:
    try:
        return (
            json.dumps(
                value, allow_nan=False, ensure_ascii=True, sort_keys=True,
                separators=(",", ":")) + "\n"
        ).encode("ascii")
    except (TypeError, ValueError, UnicodeError) as error:
        raise BootstrapError("document is not canonical JSON") from error


def _digest_bytes(value: bytes) -> str:
    return "sha256:" + hashlib.sha256(value).hexdigest()


def _body_digest(value: dict[str, object]) -> str:
    body = dict(value)
    body.pop("body_sha256", None)
    return _digest_bytes(_canonical(body))


def _with_body_digest(value: dict[str, object]) -> dict[str, object]:
    result = dict(value)
    result["body_sha256"] = _body_digest(result)
    return result


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON key")
        result[key] = value
    return result


def _strict_json(raw: bytes, label: str) -> dict[str, object]:
    try:
        value = json.loads(
            raw.decode("ascii", errors="strict"),
            object_pairs_hook=_unique_object,
            parse_constant=lambda value: (_ for _ in ()).throw(
                ValueError(value)))
    except (UnicodeError, ValueError, json.JSONDecodeError) as error:
        raise BootstrapError(f"{label} is not strict JSON") from error
    _require(isinstance(value, dict), f"{label} root is not an object")
    _require(raw == _canonical(value), f"{label} is not canonical")
    return value


def _same_file(left: os.stat_result, right: os.stat_result) -> bool:
    fields = (
        "st_dev", "st_ino", "st_mode", "st_nlink", "st_uid", "st_gid",
        "st_size", "st_mtime_ns", "st_ctime_ns",
    )
    return all(getattr(left, field) == getattr(right, field)
               for field in fields)


def _write_all(descriptor: int, payload: bytes) -> None:
    offset = 0
    while offset < len(payload):
        written = os.write(descriptor, payload[offset:])
        _require(written > 0, "file write was incomplete")
        offset += written


def _read_at(
        directory_fd: int, name: str, *, uid: int, gid: int, mode: int,
        minimum: int = 1, maximum: int = MAX_DOCUMENT_BYTES,
) -> tuple[bytes, os.stat_result]:
    before = os.stat(name, dir_fd=directory_fd, follow_symlinks=False)
    _require(
        stat.S_ISREG(before.st_mode) and not stat.S_ISLNK(before.st_mode) and
        before.st_nlink == 1 and before.st_uid == uid and before.st_gid == gid
        and stat.S_IMODE(before.st_mode) == mode and
        minimum <= before.st_size <= maximum,
        f"{name} metadata mismatch",
    )
    descriptor = os.open(
        name, os.O_RDONLY | os.O_CLOEXEC | getattr(os, "O_NOFOLLOW", 0),
        dir_fd=directory_fd)
    try:
        opened = os.fstat(descriptor)
        payload = bytearray()
        while len(payload) <= maximum:
            chunk = os.read(descriptor, min(8192, maximum + 1 - len(payload)))
            if not chunk:
                break
            payload.extend(chunk)
        after = os.fstat(descriptor)
        path_after = os.stat(
            name, dir_fd=directory_fd, follow_symlinks=False)
    finally:
        os.close(descriptor)
    _require(
        len(payload) <= maximum and _same_file(before, opened) and
        _same_file(opened, after) and _same_file(after, path_after),
        f"{name} changed during secure reopen",
    )
    return bytes(payload), after


def _open_directory(
        path: Path, *, uid: int, gid: int, modes: frozenset[int],
        links: int | None = 2,
) -> tuple[int, os.stat_result]:
    before = os.lstat(path)
    _require(
        stat.S_ISDIR(before.st_mode) and not stat.S_ISLNK(before.st_mode) and
        before.st_uid == uid and before.st_gid == gid and
        stat.S_IMODE(before.st_mode) in modes and
        (links is None or before.st_nlink == links),
        f"{path} directory metadata mismatch",
    )
    descriptor = os.open(
        path, os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC |
        getattr(os, "O_NOFOLLOW", 0))
    opened = os.fstat(descriptor)
    if not _same_file(before, opened):
        os.close(descriptor)
        raise BootstrapError(f"{path} changed while opening")
    return descriptor, opened


def _require_directory_reopen(
        path: Path, descriptor: int, expected: os.stat_result) -> None:
    current = os.fstat(descriptor)
    path_current = os.lstat(path)
    _require(
        _same_file(expected, current) and _same_file(current, path_current),
        f"{path} changed during transaction",
    )


def _metadata_record(path: Path, value: os.stat_result) -> dict[str, object]:
    return {
        "path": str(path), "device": value.st_dev, "inode": value.st_ino,
        "mode": f"{stat.S_IMODE(value.st_mode):04o}",
        "uid": value.st_uid, "gid": value.st_gid,
        "links": value.st_nlink, "mtime_ns": value.st_mtime_ns,
        "ctime_ns": value.st_ctime_ns,
    }


def _rename_noreplace(
        source_fd: int, source: str, target_fd: int, target: str) -> None:
    libc = ctypes.CDLL(None, use_errno=True)
    renameat2 = getattr(libc, "renameat2", None)
    if renameat2 is None:
        raise BootstrapError("renameat2 RENAME_NOREPLACE is unavailable")
    renameat2.argtypes = (
        ctypes.c_int, ctypes.c_char_p, ctypes.c_int, ctypes.c_char_p,
        ctypes.c_uint)
    renameat2.restype = ctypes.c_int
    result = renameat2(
        source_fd, source.encode("ascii"), target_fd,
        target.encode("ascii"), RENAME_NOREPLACE)
    if result != 0:
        error_number = ctypes.get_errno()
        if error_number == errno.EEXIST:
            raise FileExistsError(target)
        raise BootstrapError(
            f"renameat2 RENAME_NOREPLACE failed: errno={error_number}")


def _publish_temp(
        directory_fd: int, name: str, payload: bytes, *, uid: int, gid: int,
        mode: int) -> os.stat_result:
    try:
        descriptor = os.open(
            name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_CLOEXEC |
            getattr(os, "O_NOFOLLOW", 0), mode, dir_fd=directory_fd)
    except FileExistsError:
        existing, metadata = _read_at(
            directory_fd, name, uid=uid, gid=gid, mode=mode,
            minimum=len(payload), maximum=len(payload))
        _require(existing == payload, f"{name} temporary content mismatch")
        return metadata
    try:
        os.fchown(descriptor, uid, gid)
        os.fchmod(descriptor, mode)
        _write_all(descriptor, payload)
        os.fsync(descriptor)
        metadata = os.fstat(descriptor)
        _require(
            stat.S_ISREG(metadata.st_mode) and metadata.st_nlink == 1 and
            metadata.st_uid == uid and metadata.st_gid == gid and
            stat.S_IMODE(metadata.st_mode) == mode and
            metadata.st_size == len(payload),
            f"{name} temporary metadata mismatch",
        )
    finally:
        os.close(descriptor)
    os.fsync(directory_fd)
    reopened, result = _read_at(
        directory_fd, name, uid=uid, gid=gid, mode=mode,
        minimum=len(payload), maximum=len(payload))
    _require(reopened == payload, f"{name} temporary reopen mismatch")
    return result


def _replace_journal(
        state_fd: int, document: dict[str, object], *, uid: int, gid: int,
) -> None:
    payload = _canonical(document)
    _publish_temp(
        state_fd, JOURNAL_TEMP, payload, uid=uid, gid=gid, mode=0o600)
    try:
        os.replace(
            JOURNAL_TEMP, JOURNAL_NAME,
            src_dir_fd=state_fd, dst_dir_fd=state_fd)
    except OSError as error:
        raise BootstrapError("journal atomic replace failed") from error
    os.fsync(state_fd)
    reopened, _metadata = _read_at(
        state_fd, JOURNAL_NAME, uid=uid, gid=gid, mode=0o600,
        minimum=len(payload), maximum=len(payload))
    _require(reopened == payload, "journal secure reopen mismatch")


def _new_journal(
        *, boot_id: str, expected_uid: int, expected_gid: int,
        source_baseline_sha256: str, producer: ProducerEvidence,
        control_preexisting: bool, marker_preexisting: bool,
        now_ms: int,
) -> dict[str, object]:
    return _with_body_digest({
        "schema": JOURNAL_SCHEMA, "version": VERSION, "round": ROUND,
        "domain": DOMAIN, "boot_id": boot_id,
        "expected_uid": expected_uid, "expected_gid": expected_gid,
        "source_baseline_sha256": source_baseline_sha256,
        "producer_sha256": producer.sha256,
        "control_preexisting": control_preexisting,
        "marker_preexisting": marker_preexisting,
        "state": "STARTED", "updated_at_ms": now_ms,
    })


def _validate_journal(
        value: dict[str, object], *, boot_id: str, expected_uid: int,
        expected_gid: int, source_baseline_sha256: str,
        producer: ProducerEvidence) -> None:
    _require(set(value) == {
        "schema", "version", "round", "domain", "boot_id",
        "expected_uid", "expected_gid", "source_baseline_sha256",
        "producer_sha256", "control_preexisting", "marker_preexisting",
        "state", "updated_at_ms", "body_sha256",
    }, "journal fields mismatch")
    _require(
        value.get("schema") == JOURNAL_SCHEMA and
        value.get("version") == VERSION and value.get("round") == ROUND and
        value.get("domain") == DOMAIN and value.get("boot_id") == boot_id and
        value.get("expected_uid") == expected_uid and
        value.get("expected_gid") == expected_gid and
        value.get("source_baseline_sha256") == source_baseline_sha256 and
        value.get("producer_sha256") == producer.sha256 and
        type(value.get("control_preexisting")) is bool and
        type(value.get("marker_preexisting")) is bool and
        value.get("state") in JOURNAL_STATES and
        type(value.get("updated_at_ms")) is int and
        value.get("updated_at_ms", -1) >= 0 and
        value.get("body_sha256") == _body_digest(value),
        "journal binding mismatch",
    )


def _advance_journal(
        state_fd: int, journal: dict[str, object], state: str, now_ms: int,
        *, uid: int, gid: int) -> dict[str, object]:
    _require(state in JOURNAL_STATES, "journal target state invalid")
    current_index = JOURNAL_STATES.index(str(journal["state"]))
    target_index = JOURNAL_STATES.index(state)
    if target_index <= current_index:
        return journal
    result = dict(journal)
    result["state"] = state
    result["updated_at_ms"] = now_ms
    result["body_sha256"] = _body_digest(result)
    _replace_journal(state_fd, result, uid=uid, gid=gid)
    return result


def _scan_control(
        paths: BootstrapPaths, *, owner_uid: int, owner_gid: int,
        expected_gid: int, allow_incomplete: bool,
) -> tuple[int, os.stat_result, os.stat_result | None] | None:
    try:
        metadata = os.lstat(paths.control)
    except FileNotFoundError:
        return None
    modes = frozenset({0o750})
    gids = {expected_gid}
    if allow_incomplete:
        modes = frozenset({0o700, 0o750})
        gids.add(owner_gid)
    _require(
        stat.S_ISDIR(metadata.st_mode) and
        not stat.S_ISLNK(metadata.st_mode) and metadata.st_nlink == 2 and
        metadata.st_uid == owner_uid and metadata.st_gid in gids and
        stat.S_IMODE(metadata.st_mode) in modes and
        not (stat.S_IMODE(metadata.st_mode) == 0o750 and
             metadata.st_gid != expected_gid),
        "control directory metadata mismatch",
    )
    descriptor = os.open(
        paths.control, os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC |
        getattr(os, "O_NOFOLLOW", 0))
    opened = os.fstat(descriptor)
    if not _same_file(metadata, opened):
        os.close(descriptor)
        raise BootstrapError("control directory changed while opening")
    try:
        names = sorted(os.listdir(descriptor))
    except BaseException:
        os.close(descriptor)
        raise
    _require(names in ([], ["kill-switch"]),
             "control directory contains extra entries")
    marker: os.stat_result | None = None
    if names:
        raw, marker = _read_at(
            descriptor, "kill-switch", uid=owner_uid, gid=expected_gid,
            mode=0o440, minimum=len(MARKER_BYTES), maximum=len(MARKER_BYTES))
        _require(raw == MARKER_BYTES, "kill-switch content mismatch")
    return descriptor, opened, marker


def _ensure_state_directory(
        paths: BootstrapPaths, *, owner_uid: int, owner_gid: int,
) -> tuple[int, os.stat_result]:
    try:
        os.mkdir(paths.state, 0o700)
    except FileExistsError:
        pass
    try:
        os.chown(paths.state, owner_uid, owner_gid, follow_symlinks=False)
        os.chmod(paths.state, 0o700, follow_symlinks=False)
    except OSError as error:
        raise BootstrapError("state directory provisioning failed") from error
    return _open_directory(
        paths.state, uid=owner_uid, gid=owner_gid,
        modes=frozenset({0o700}))


def _state_inventory(state_fd: int, *, terminal: bool) -> None:
    names = set(os.listdir(state_fd))
    required = {LOCK_NAME}
    allowed = {
        LOCK_NAME, JOURNAL_NAME, RECEIPT_NAME, JOURNAL_TEMP,
        MARKER_TEMP, RECEIPT_TEMP,
    }
    _require(required <= names and names <= allowed,
             "state directory contains unexpected entries")
    if terminal:
        _require(names == {LOCK_NAME, JOURNAL_NAME, RECEIPT_NAME},
                 "terminal state directory contains transaction residue")


def _lock(state_fd: int, *, uid: int, gid: int) -> int:
    descriptor = os.open(
        LOCK_NAME, os.O_RDWR | os.O_CREAT | os.O_CLOEXEC |
        getattr(os, "O_NOFOLLOW", 0), 0o600, dir_fd=state_fd)
    metadata = os.fstat(descriptor)
    _require(
        stat.S_ISREG(metadata.st_mode) and metadata.st_nlink == 1 and
        metadata.st_uid == uid and metadata.st_gid == gid and
        stat.S_IMODE(metadata.st_mode) == 0o600,
        "transaction lock metadata mismatch",
    )
    fcntl.flock(descriptor, fcntl.LOCK_EX)
    return descriptor


def _ensure_control_directory(
        paths: BootstrapPaths, anchor_fd: int, *, owner_uid: int,
        owner_gid: int, expected_gid: int,
) -> tuple[int, os.stat_result, os.stat_result | None]:
    observed = _scan_control(
        paths, owner_uid=owner_uid, owner_gid=owner_gid,
        expected_gid=expected_gid, allow_incomplete=True)
    if observed is None:
        try:
            os.mkdir(paths.control_name, 0o700, dir_fd=anchor_fd)
        except OSError as error:
            raise BootstrapError("control directory create failed") from error
        observed = _scan_control(
            paths, owner_uid=owner_uid, owner_gid=owner_gid,
            expected_gid=expected_gid, allow_incomplete=True)
        assert observed is not None
    descriptor, _before, marker = observed
    try:
        current = os.fstat(descriptor)
        if current.st_uid != owner_uid or current.st_gid != expected_gid:
            os.fchown(descriptor, owner_uid, expected_gid)
        current = os.fstat(descriptor)
        if stat.S_IMODE(current.st_mode) != 0o750:
            os.fchmod(descriptor, 0o750)
        os.fsync(descriptor)
    except BaseException:
        os.close(descriptor)
        raise
    os.fsync(anchor_fd)
    final = os.fstat(descriptor)
    path_final = os.lstat(paths.control)
    _require(
        _same_file(final, path_final) and final.st_uid == owner_uid and
        final.st_gid == expected_gid and
        stat.S_IMODE(final.st_mode) == 0o750 and final.st_nlink == 2,
        "control directory final metadata mismatch",
    )
    return descriptor, final, marker


def _ensure_marker(
        state_fd: int, control_fd: int, *, owner_uid: int,
        expected_gid: int) -> os.stat_result:
    try:
        raw, metadata = _read_at(
            control_fd, "kill-switch", uid=owner_uid, gid=expected_gid,
            mode=0o440, minimum=len(MARKER_BYTES), maximum=len(MARKER_BYTES))
        _require(raw == MARKER_BYTES, "kill-switch content mismatch")
        return metadata
    except FileNotFoundError:
        pass
    _publish_temp(
        state_fd, MARKER_TEMP, MARKER_BYTES, uid=owner_uid,
        gid=expected_gid, mode=0o440)
    _rename_noreplace(state_fd, MARKER_TEMP, control_fd, "kill-switch")
    os.fsync(control_fd)
    raw, metadata = _read_at(
        control_fd, "kill-switch", uid=owner_uid, gid=expected_gid,
        mode=0o440, minimum=len(MARKER_BYTES), maximum=len(MARKER_BYTES))
    _require(raw == MARKER_BYTES, "kill-switch publication mismatch")
    return metadata


def _receipt_document(
        *, boot_id: str, source_baseline_sha256: str,
        producer: ProducerEvidence, identity: PaperIdentity,
        paths: BootstrapPaths, directory: os.stat_result,
        marker: os.stat_result, journal: dict[str, object],
        completed_at_ms: int,
) -> dict[str, object]:
    return _with_body_digest({
        "schema": SCHEMA, "version": VERSION, "round": ROUND,
        "domain": DOMAIN, "operation": "ENSURE_ENGAGED_NON_AUTHORIZING",
        "status": "COMPLETE", "completed_at_ms": completed_at_ms,
        "boot_id": boot_id,
        "source_baseline_sha256": source_baseline_sha256,
        "producer": {
            "path": producer.path, "sha256": producer.sha256,
            "mode": f"{producer.mode:04o}", "uid": producer.uid,
            "gid": producer.gid,
        },
        "paper_identity": {
            "name": identity.name, "uid": identity.uid,
            "gid": identity.gid,
            "supplementary_gids": list(identity.supplementary_gids),
        },
        "control_directory": _metadata_record(paths.control, directory),
        "kill_switch_marker": {
            **_metadata_record(paths.marker, marker),
            "size": marker.st_size, "sha256": _digest_bytes(MARKER_BYTES),
            "state": "engaged",
        },
        "transaction": {
            "state_directory": str(paths.state),
            "journal_path": str(paths.state / JOURNAL_NAME),
            "journal_status": "RECEIPT_PUBLISHED_THEN_COMPLETE",
            "publication_order": (
                "JOURNAL_THEN_DIRECTORY_THEN_MARKER_NOREPLACE_THEN_"
                "RECEIPT_NOREPLACE_THEN_JOURNAL_COMPLETE"),
        },
        "created": {
            "control_directory": not bool(journal["control_preexisting"]),
            "kill_switch_marker": not bool(journal["marker_preexisting"]),
        },
        "authorization_manifest_created": False,
        "credential_created_or_accessed": False,
        "unit_created_enabled_or_started": False,
        "connector_created_or_accessed": False,
        "paper_test_admission_candidate": False,
        "paper_authorized": False, "live_authorized": False,
        "mutation_authorized": False, "direct_broker_access": False,
        "order_submission_authorized": False,
    })


def _validate_receipt(
        value: dict[str, object], *, expected: dict[str, object]) -> None:
    _require(set(value) == set(expected), "receipt fields mismatch")
    _require(value == expected, "receipt binding mismatch")
    _require(value.get("body_sha256") == _body_digest(value),
             "receipt body digest mismatch")


def _publish_receipt(
        state_fd: int, payload: bytes, *, uid: int, gid: int) -> None:
    _publish_temp(
        state_fd, RECEIPT_TEMP, payload, uid=uid, gid=gid, mode=0o400)
    _rename_noreplace(state_fd, RECEIPT_TEMP, state_fd, RECEIPT_NAME)
    os.fsync(state_fd)
    reopened, _metadata = _read_at(
        state_fd, RECEIPT_NAME, uid=uid, gid=gid, mode=0o400,
        minimum=len(payload), maximum=len(payload))
    _require(reopened == payload, "receipt secure reopen mismatch")


def _default_identity(expected_uid: int, expected_gid: int) -> PaperIdentity:
    try:
        account = pwd.getpwnam(IDENTITY)
        account_by_uid = pwd.getpwuid(expected_uid)
        group = grp.getgrnam(IDENTITY)
        group_by_gid = grp.getgrgid(expected_gid)
        supplementary = tuple(sorted(set(os.getgrouplist(
            IDENTITY, account.pw_gid))))
    except KeyError as error:
        raise BootstrapError("dedicated alpha PAPER identity is missing") from error
    _require(
        account.pw_name == IDENTITY and account_by_uid.pw_name == IDENTITY and
        account.pw_uid == expected_uid and account.pw_gid == expected_gid and
        group.gr_name == IDENTITY and group_by_gid.gr_name == IDENTITY and
        group.gr_gid == expected_gid and expected_uid == expected_gid and
        account.pw_dir == "/nonexistent" and
        account.pw_shell.endswith("/nologin") and not group.gr_mem and
        supplementary == (expected_gid,) and
        [item.pw_name for item in pwd.getpwall()
         if item.pw_uid == expected_uid] == [IDENTITY] and
        [item.gr_name for item in grp.getgrall()
         if item.gr_gid == expected_gid] == [IDENTITY],
        "dedicated alpha PAPER identity metadata mismatch",
    )
    return PaperIdentity(
        IDENTITY, expected_uid, expected_gid, account.pw_dir,
        account.pw_shell, supplementary)


def bootstrap(
        *, paths: BootstrapPaths, expected_uid: int, expected_gid: int,
        source_baseline_sha256: str, producer: ProducerEvidence,
        boot_id: str, identity: PaperIdentity,
        owner_uid: int = 0, owner_gid: int = 0,
        now_ms: Callable[[], int] = lambda: time.time_ns() // 1_000_000,
) -> dict[str, object]:
    _require(
        type(expected_uid) is int and type(expected_gid) is int and
        1 <= expected_uid <= 4_294_967_295 and expected_uid == expected_gid,
        "expected PAPER uid/gid mismatch")
    _require(
        identity == PaperIdentity(
            IDENTITY, expected_uid, expected_gid, "/nonexistent",
            identity.shell, (expected_gid,)) and
        identity.shell.endswith("/nologin"),
        "PAPER identity binding mismatch")
    _require(SHA256.fullmatch(source_baseline_sha256) is not None,
             "source baseline digest invalid")
    _require(PLAIN_SHA256.fullmatch(producer.sha256) is not None,
             "producer digest invalid")
    _require(
        producer.mode == 0o755 and producer.uid == owner_uid and
        producer.gid == owner_gid,
        "fixed producer metadata mismatch")
    _require(BOOT_ID.fullmatch(boot_id) is not None, "boot id invalid")
    _require(
        paths.control_name == CONTROL_NAME and paths.state_name == STATE_NAME,
        "bootstrap path/domain override is forbidden")

    anchor_fd, anchor_before = _open_directory(
        paths.anchor, uid=owner_uid, gid=owner_gid,
        modes=frozenset({0o755}), links=None)
    state_fd = -1
    lock_fd = -1
    control_fd = -1
    try:
        state_fd, state_before = _ensure_state_directory(
            paths, owner_uid=owner_uid, owner_gid=owner_gid)
        lock_fd = _lock(state_fd, uid=owner_uid, gid=owner_gid)
        _state_inventory(state_fd, terminal=False)

        journal: dict[str, object]
        try:
            journal_raw, _journal_metadata = _read_at(
                state_fd, JOURNAL_NAME, uid=owner_uid, gid=owner_gid,
                mode=0o600, minimum=2)
        except FileNotFoundError:
            existing = _scan_control(
                paths, owner_uid=owner_uid, owner_gid=owner_gid,
                expected_gid=expected_gid, allow_incomplete=False)
            control_preexisting = existing is not None
            marker_preexisting = existing is not None and existing[2] is not None
            if existing is not None:
                os.close(existing[0])
            journal = _new_journal(
                boot_id=boot_id, expected_uid=expected_uid,
                expected_gid=expected_gid,
                source_baseline_sha256=source_baseline_sha256,
                producer=producer,
                control_preexisting=control_preexisting,
                marker_preexisting=marker_preexisting,
                now_ms=now_ms())
            payload = _canonical(journal)
            _publish_temp(
                state_fd, JOURNAL_TEMP, payload, uid=owner_uid,
                gid=owner_gid, mode=0o600)
            _rename_noreplace(
                state_fd, JOURNAL_TEMP, state_fd, JOURNAL_NAME)
            os.fsync(state_fd)
            _fault("after_journal")
        else:
            journal = _strict_json(journal_raw, "bootstrap journal")
        _validate_journal(
            journal, boot_id=boot_id, expected_uid=expected_uid,
            expected_gid=expected_gid,
            source_baseline_sha256=source_baseline_sha256,
            producer=producer)

        control_fd, directory, _marker_before = _ensure_control_directory(
            paths, anchor_fd, owner_uid=owner_uid, owner_gid=owner_gid,
            expected_gid=expected_gid)
        journal = _advance_journal(
            state_fd, journal, "DIRECTORY_READY", now_ms(),
            uid=owner_uid, gid=owner_gid)
        _fault("after_directory")

        marker = _ensure_marker(
            state_fd, control_fd, owner_uid=owner_uid,
            expected_gid=expected_gid)
        # Marker publication changes the directory timestamps; the receipt
        # binds the post-publication directory, not the earlier staging view.
        directory = os.fstat(control_fd)
        journal = _advance_journal(
            state_fd, journal, "MARKER_ENGAGED", now_ms(),
            uid=owner_uid, gid=owner_gid)
        _fault("after_marker")

        completed_at_ms = now_ms()
        expected_receipt = _receipt_document(
            boot_id=boot_id,
            source_baseline_sha256=source_baseline_sha256,
            producer=producer, identity=identity, paths=paths,
            directory=directory, marker=marker, journal=journal,
            completed_at_ms=completed_at_ms)
        try:
            receipt_raw, _receipt_metadata = _read_at(
                state_fd, RECEIPT_NAME, uid=owner_uid, gid=owner_gid,
                mode=0o400, minimum=2)
        except FileNotFoundError:
            _publish_receipt(
                state_fd, _canonical(expected_receipt), uid=owner_uid,
                gid=owner_gid)
            receipt = expected_receipt
        else:
            receipt = _strict_json(receipt_raw, "bootstrap receipt")
            # Preserve the committed receipt timestamp across idempotent retry.
            committed_at = receipt.get("completed_at_ms")
            _require(type(committed_at) is int and committed_at >= 0,
                     "receipt completion time invalid")
            expected_receipt = _receipt_document(
                boot_id=boot_id,
                source_baseline_sha256=source_baseline_sha256,
                producer=producer, identity=identity, paths=paths,
                directory=directory, marker=marker, journal=journal,
                completed_at_ms=committed_at)
            _validate_receipt(receipt, expected=expected_receipt)
        journal = _advance_journal(
            state_fd, journal, "RECEIPT_PUBLISHED", now_ms(),
            uid=owner_uid, gid=owner_gid)
        _fault("after_receipt")
        journal = _advance_journal(
            state_fd, journal, "COMPLETE", now_ms(),
            uid=owner_uid, gid=owner_gid)
        _fault("after_complete")

        _validate_receipt(receipt, expected=expected_receipt)
        receipt_raw, _receipt_metadata = _read_at(
            state_fd, RECEIPT_NAME, uid=owner_uid, gid=owner_gid,
            mode=0o400, minimum=2)
        _require(receipt_raw == _canonical(receipt),
                 "receipt changed after journal completion")
        marker_raw, marker_final = _read_at(
            control_fd, "kill-switch", uid=owner_uid, gid=expected_gid,
            mode=0o440, minimum=7, maximum=7)
        _require(marker_raw == MARKER_BYTES and _same_file(marker, marker_final),
                 "kill-switch changed after receipt publication")
        control_committed = os.fstat(control_fd)
        state_committed = os.fstat(state_fd)
        anchor_committed = os.fstat(anchor_fd)
        _require_directory_reopen(
            paths.control, control_fd, control_committed)
        _require_directory_reopen(paths.state, state_fd, state_committed)
        _require_directory_reopen(paths.anchor, anchor_fd, anchor_committed)
        _state_inventory(state_fd, terminal=True)
        _require(sorted(os.listdir(control_fd)) == ["kill-switch"],
                 "terminal control inventory mismatch")
        return receipt
    finally:
        if control_fd >= 0:
            os.close(control_fd)
        if lock_fd >= 0:
            try:
                fcntl.flock(lock_fd, fcntl.LOCK_UN)
            finally:
                os.close(lock_fd)
        if state_fd >= 0:
            os.close(state_fd)
        os.close(anchor_fd)


def _read_installed_producer(expected_sha256: str) -> ProducerEvidence:
    _require(PLAIN_SHA256.fullmatch(expected_sha256) is not None,
             "expected installed producer digest invalid")
    before = os.lstat(INSTALLED_EXECUTABLE)
    _require(
        stat.S_ISREG(before.st_mode) and not stat.S_ISLNK(before.st_mode) and
        before.st_nlink == 1 and before.st_uid == 0 and before.st_gid == 0 and
        stat.S_IMODE(before.st_mode) == 0o755 and
        2 <= before.st_size <= 4 * 1024 * 1024,
        "fixed installed producer metadata mismatch")
    descriptor = os.open(
        INSTALLED_EXECUTABLE,
        os.O_RDONLY | os.O_CLOEXEC | getattr(os, "O_NOFOLLOW", 0))
    try:
        opened = os.fstat(descriptor)
        digest = hashlib.sha256()
        total = 0
        while True:
            chunk = os.read(descriptor, 65536)
            if not chunk:
                break
            total += len(chunk)
            _require(total <= 4 * 1024 * 1024,
                     "fixed installed producer exceeds size limit")
            digest.update(chunk)
        after = os.fstat(descriptor)
        path_after = os.lstat(INSTALLED_EXECUTABLE)
    finally:
        os.close(descriptor)
    _require(
        _same_file(before, opened) and _same_file(opened, after) and
        _same_file(after, path_after) and digest.hexdigest() == expected_sha256,
        "fixed installed producer digest/reopen mismatch")
    return ProducerEvidence(
        str(INSTALLED_EXECUTABLE), expected_sha256, 0o755, 0, 0)


def _read_boot_id() -> str:
    try:
        value = BOOT_ID_PATH.read_text(encoding="ascii").strip()
    except (OSError, UnicodeError) as error:
        raise BootstrapError("boot id read failed") from error
    _require(BOOT_ID.fullmatch(value) is not None, "boot id invalid")
    return value


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Establish the non-authorizing alpha PAPER kill switch")
    parser.add_argument("--run", action="store_true")
    parser.add_argument("--expected-paper-uid", type=int, required=True)
    parser.add_argument("--expected-paper-gid", type=int, required=True)
    parser.add_argument(
        "--expected-source-baseline-sha256", required=True)
    parser.add_argument("--expected-installed-sha256", required=True)
    return parser


def main(argv: list[str] | None = None) -> int:
    arguments = _parser().parse_args(argv)
    try:
        _require(arguments.run, "explicit --run is required")
        _require(os.geteuid() == 0 and os.getegid() == 0,
                 "real root uid/gid is required")
        _require(Path(__file__) == INSTALLED_EXECUTABLE,
                 "fixed installed producer path is required")
        producer = _read_installed_producer(
            arguments.expected_installed_sha256)
        identity = _default_identity(
            arguments.expected_paper_uid, arguments.expected_paper_gid)
        receipt = bootstrap(
            paths=BootstrapPaths(RUNTIME_ANCHOR),
            expected_uid=arguments.expected_paper_uid,
            expected_gid=arguments.expected_paper_gid,
            source_baseline_sha256=(
                arguments.expected_source_baseline_sha256),
            producer=producer, boot_id=_read_boot_id(), identity=identity)
    except (BootstrapError, FileNotFoundError, OSError) as error:
        print(f"ERROR: {error}", file=sys.stderr)
        return 1
    sys.stdout.buffer.write(_canonical(receipt))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
