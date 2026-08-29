#!/usr/bin/python3

"""Root-only validator/exporter for redacted WATCH snapshots."""

from __future__ import annotations

import argparse
from contextlib import contextmanager
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import sys
import tempfile
import time
from typing import Iterator


MAX_BYTES = 256 * 1024
MAX_WATCH_TTL_SECONDS = 3600
DIGEST_PREFIX = "sha256:"
V1_FIELDS = frozenset({
    "schema", "version", "domain_id", "agent_uid", "generated_at_ms",
    "instrument", "catalog_sha256", "descriptor_sha256", "reads",
    "paper_authorized", "live_authorized", "mutation_attempted",
    "direct_broker_access", "body_sha256",
})
V2_FIELDS = V1_FIELDS | frozenset({
    "collection_started_at_ms", "collection_finished_at_ms",
    "read_finished_at_ms",
})
READ_ORDER = (
    "account.get_summary",
    "portfolio.list_positions",
    "orders.list",
    "risk.get_limits",
    "market.get_quote",
    "system.get_health",
)
LEASE_RECEIPT_FIELDS = frozenset({
    "schema", "version", "domain_id", "agent_id", "agent_uid", "boundary",
    "operation", "lease_generation", "previous_lease_generation",
    "previous_receipt_body_sha256", "accepted", "reason_code",
    "accepted_at_ms", "ttl_seconds", "expires_at_ms", "paper_authorized",
    "live_authorized", "mutation_authorized", "body_sha256",
})
EXPORT_RECEIPT_FIELDS = frozenset({
    "schema", "version", "domain_id", "agent_uid", "reader_uid",
    "reader_gid", "boundary", "lease_generation",
    "lease_receipt_body_sha256", "lease_receipt_file_sha256",
    "snapshot_body_sha256", "snapshot_file_sha256",
    "snapshot_generated_at_ms", "exported_at_ms", "paper_authorized",
    "live_authorized", "mutation_attempted", "direct_broker_access",
    "body_sha256",
})
EXPORT_FILES = (
    "snapshot.json",
    "shadow-watch-lease-receipt.json",
    "shadow-watch-export-receipt.json",
)
COMMIT_NAME = "current.json"
GENERATIONS_NAME = "generations"
GENERATION = re.compile(
    r"^generation-([0-9]{20})-([A-Za-z0-9_-]{8,64})$")
STAGING = re.compile(r"^\.staging-[A-Za-z0-9_-]{8,64}$")
COMMIT_FIELDS = frozenset({
    "schema", "version", "authority_status", "authority_changed_at_ms",
    "close_reason", "commit_sequence", "generation", "domain_id",
    "agent_uid", "reader_uid", "reader_gid", "lease_generation",
    "snapshot_body_sha256", "snapshot_file_sha256",
    "lease_receipt_body_sha256", "lease_receipt_file_sha256",
    "export_receipt_body_sha256", "export_receipt_file_sha256",
    "committed_at_ms", "paper_authorized", "live_authorized",
    "mutation_attempted", "direct_broker_access", "body_sha256",
})


class ExportError(RuntimeError):
    pass


def _pairs(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ExportError("WATCH_EXPORT_DUPLICATE_KEY")
        result[key] = value
    return result


def _canonical(document: object) -> bytes:
    return json.dumps(
        document, ensure_ascii=True, allow_nan=False, sort_keys=True,
        separators=(",", ":")).encode("ascii") + b"\n"


def _timestamp(value: object) -> int | None:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        return None
    return value


def _digest(value: object) -> bool:
    if not isinstance(value, str) or not value.startswith(DIGEST_PREFIX):
        return False
    suffix = value[len(DIGEST_PREFIX):]
    return len(suffix) == 64 and all(character in "0123456789abcdef"
                                     for character in suffix)


def _digest_bytes(contents: bytes) -> str:
    return DIGEST_PREFIX + hashlib.sha256(contents).hexdigest()


def _identity(metadata: os.stat_result) -> tuple[object, ...]:
    return (
        metadata.st_dev, metadata.st_ino, metadata.st_mode,
        metadata.st_nlink, metadata.st_uid, metadata.st_gid,
        metadata.st_size, metadata.st_mtime_ns, metadata.st_ctime_ns,
    )


def _read_root_export(
        path: Path,
        *,
        owner_uid: int,
        reader_gid: int,
        label: str,
) -> tuple[dict[str, object], bytes]:
    descriptor = os.open(
        path,
        os.O_RDONLY | os.O_CLOEXEC | getattr(os, "O_NOFOLLOW", 0),
    )
    try:
        before = os.fstat(descriptor)
        if (
                not stat.S_ISREG(before.st_mode) or before.st_nlink != 1 or
                stat.S_IMODE(before.st_mode) not in {0o400, 0o440} or
                (before.st_uid, before.st_gid) !=
                (owner_uid, reader_gid) or
                not 2 <= before.st_size <= MAX_BYTES):
            raise ExportError(label + "_METADATA_INVALID")
        chunks: list[bytes] = []
        remaining = MAX_BYTES + 1
        while remaining:
            chunk = os.read(descriptor, min(65_536, remaining))
            if not chunk:
                break
            chunks.append(chunk)
            remaining -= len(chunk)
        contents = b"".join(chunks)
        after = os.fstat(descriptor)
        path_after = os.stat(path, follow_symlinks=False)
    finally:
        os.close(descriptor)
    if (
            len(contents) > MAX_BYTES or len(contents) != before.st_size or
            _identity(before) != _identity(after) or
            _identity(after) != _identity(path_after)):
        raise ExportError(label + "_CHANGED")
    try:
        document = json.loads(
            contents.decode("ascii", errors="strict"),
            object_pairs_hook=_pairs,
            parse_constant=lambda _: (_ for _ in ()).throw(
                ExportError(label + "_NON_FINITE")),
        )
    except (UnicodeError, json.JSONDecodeError, ValueError) as error:
        raise ExportError(label + "_JSON_INVALID") from error
    if not isinstance(document, dict) or contents != _canonical(document):
        raise ExportError(label + "_NOT_CANONICAL")
    return document, contents


def _valid_lease_receipt(
        document: object, domain_id: str, agent_uid: int) -> bool:
    if not isinstance(document, dict) or set(document) != LEASE_RECEIPT_FIELDS:
        return False
    generation = document.get("lease_generation")
    accepted_at_ms = _timestamp(document.get("accepted_at_ms"))
    ttl_seconds = document.get("ttl_seconds")
    expires_at_ms = _timestamp(document.get("expires_at_ms"))
    operation = document.get("operation")
    if (
            document.get("schema") !=
            "hepta.shadow-watch-lease-receipt.v1" or
            document.get("version") != 1 or
            document.get("domain_id") != domain_id or
            document.get("agent_id") != domain_id or
            document.get("agent_uid") != agent_uid or
            document.get("boundary") != "WATCH" or
            operation not in {"PROVISION", "ROTATE"} or
            isinstance(generation, bool) or
            not isinstance(generation, int) or generation < 1 or
            document.get("accepted") is not True or
            document.get("reason_code") != "OK" or
            accepted_at_ms is None or
            isinstance(ttl_seconds, bool) or
            not isinstance(ttl_seconds, int) or
            not 60 <= ttl_seconds <= MAX_WATCH_TTL_SECONDS or
            expires_at_ms != accepted_at_ms + ttl_seconds * 1000 or
            document.get("paper_authorized") is not False or
            document.get("live_authorized") is not False or
            document.get("mutation_authorized") is not False):
        return False
    previous_generation = document.get("previous_lease_generation")
    previous_digest = document.get("previous_receipt_body_sha256")
    if operation == "PROVISION":
        if previous_generation is not None or previous_digest is not None:
            return False
    elif (
            isinstance(previous_generation, bool) or
            not isinstance(previous_generation, int) or
            previous_generation != generation - 1 or
            not _digest(previous_digest)):
        return False
    claimed = document.get("body_sha256")
    body = dict(document)
    body.pop("body_sha256")
    actual = DIGEST_PREFIX + hashlib.sha256(_canonical(body)).hexdigest()
    return _digest(claimed) and claimed == actual


def _reader_publish(
        destination: Path,
        contents: bytes,
        reader_gid: int,
        *,
    require_root: bool,
    prefix: str,
    directory_mode: int = 0o750,
) -> None:
    owner_uid = 0 if require_root else os.geteuid()
    _ensure_owned_directory(
        destination.parent,
        owner_uid=owner_uid,
        reader_gid=reader_gid,
        mode=directory_mode,
    )
    temporary: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
                mode="wb",
                dir=destination.parent,
                prefix=prefix,
                delete=False) as file:
            temporary = Path(file.name)
            file.write(contents)
            file.flush()
            os.fsync(file.fileno())
            os.fchown(file.fileno(), owner_uid, reader_gid)
            os.fchmod(file.fileno(), 0o440)
        os.replace(temporary, destination)
        temporary = None
        directory_fd = os.open(
            destination.parent,
            os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC |
            getattr(os, "O_NOFOLLOW", 0),
        )
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
    finally:
        if temporary is not None:
            try:
                temporary.unlink()
            except OSError:
                pass


def _remove_reader_output(
        destination: Path,
        reader_gid: int,
        *,
        require_root: bool,
) -> None:
    try:
        metadata = os.lstat(destination)
    except FileNotFoundError:
        return
    owner_uid = 0 if require_root else os.geteuid()
    if (
            not stat.S_ISREG(metadata.st_mode) or
            stat.S_ISLNK(metadata.st_mode) or metadata.st_nlink != 1 or
            metadata.st_uid != owner_uid or metadata.st_gid != reader_gid or
            stat.S_IMODE(metadata.st_mode) != 0o440):
        raise ExportError("WATCH_LEASE_EXPORT_DESTINATION_UNSAFE")
    os.unlink(destination)
    directory_fd = os.open(
        destination.parent,
        os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC |
        getattr(os, "O_NOFOLLOW", 0),
    )
    try:
        os.fsync(directory_fd)
    finally:
        os.close(directory_fd)


def _valid_v2_times(document: dict[str, object]) -> bool:
    started = _timestamp(document.get("collection_started_at_ms"))
    finished = _timestamp(document.get("collection_finished_at_ms"))
    generated = _timestamp(document.get("generated_at_ms"))
    read_times = document.get("read_finished_at_ms")
    reads = document.get("reads")
    if (
            started is None or finished is None or generated is None or
            not started <= finished <= generated or
            not isinstance(read_times, dict) or
            not isinstance(reads, dict) or
            set(read_times) != set(READ_ORDER) or
            set(reads) != set(READ_ORDER)):
        return False
    previous = started
    for tool in READ_ORDER:
        observed = _timestamp(read_times.get(tool))
        if observed is None or not previous <= observed <= finished:
            return False
        previous = observed
    return True


def _valid_contract(document: object, agent_uid: int) -> bool:
    if not isinstance(document, dict):
        return False
    schema = document.get("schema")
    version = document.get("version")
    if schema == "hepta.shadow-watch-snapshot.v1" and version == 1:
        fields_valid = set(document) == V1_FIELDS
        times_valid = _timestamp(document.get("generated_at_ms")) is not None
    elif schema == "hepta.shadow-watch-snapshot.v2" and version == 2:
        fields_valid = set(document) == V2_FIELDS
        times_valid = _valid_v2_times(document)
    else:
        return False
    return (
        fields_valid and times_valid and
        document.get("agent_uid") == agent_uid and
        document.get("paper_authorized") is False and
        document.get("live_authorized") is False and
        document.get("mutation_attempted") is False and
        document.get("direct_broker_access") is False
    )


def export(source: Path, destination: Path, agent_uid: int, agent_gid: int,
           reader_uid: int, reader_gid: int, *,
           require_root: bool = True,
           _directory_mode: int = 0o750) -> dict[str, object]:
    if require_root and os.geteuid() != 0:
        raise ExportError("WATCH_EXPORT_ROOT_REQUIRED")
    if any(
            isinstance(value, bool) or not isinstance(value, int) or value <= 0
            for value in (agent_uid, agent_gid, reader_uid, reader_gid)):
        raise ExportError("WATCH_EXPORT_IDENTITY_INVALID")
    metadata = os.lstat(source)
    if (not stat.S_ISREG(metadata.st_mode) or stat.S_ISLNK(metadata.st_mode) or
            metadata.st_nlink != 1 or stat.S_IMODE(metadata.st_mode) != 0o600 or
            (metadata.st_uid, metadata.st_gid) != (agent_uid, agent_gid) or
            metadata.st_size > MAX_BYTES):
        raise ExportError("WATCH_EXPORT_SOURCE_METADATA_INVALID")
    descriptor = os.open(
        source, os.O_RDONLY | os.O_CLOEXEC | getattr(os, "O_NOFOLLOW", 0))
    try:
        contents = os.read(descriptor, MAX_BYTES + 1)
        opened = os.fstat(descriptor)
    finally:
        os.close(descriptor)
    if len(contents) > MAX_BYTES or (opened.st_dev, opened.st_ino) != (
            metadata.st_dev, metadata.st_ino):
        raise ExportError("WATCH_EXPORT_SOURCE_CHANGED")
    try:
        document = json.loads(
            contents.decode("ascii", errors="strict"), object_pairs_hook=_pairs,
            parse_constant=lambda _: (_ for _ in ()).throw(
                ExportError("WATCH_EXPORT_NON_FINITE")))
    except (UnicodeError, json.JSONDecodeError, ValueError) as error:
        raise ExportError("WATCH_EXPORT_JSON_INVALID") from error
    if not _valid_contract(document, agent_uid):
        raise ExportError("WATCH_EXPORT_CONTRACT_INVALID")
    body = dict(document)
    expected = body.pop("body_sha256")
    actual = "sha256:" + hashlib.sha256(_canonical(body)).hexdigest()
    if expected != actual:
        raise ExportError("WATCH_EXPORT_DIGEST_INVALID")

    _reader_publish(
        destination,
        _canonical(document),
        reader_gid,
        require_root=require_root,
        prefix=".snapshot-",
        directory_mode=_directory_mode,
    )
    return document


def export_lease_receipt(
        source: Path,
        destination: Path,
        domain_id: str,
        agent_uid: int,
        agent_gid: int,
        reader_uid: int,
        reader_gid: int,
        *,
        require_root: bool = True,
        _directory_mode: int = 0o750,
) -> dict[str, object]:
    if require_root and os.geteuid() != 0:
        raise ExportError("WATCH_LEASE_EXPORT_ROOT_REQUIRED")
    if (
            not isinstance(domain_id, str) or not domain_id or
            any(
                isinstance(value, bool) or not isinstance(value, int) or
                value <= 0
                for value in (agent_uid, agent_gid, reader_uid, reader_gid))):
        raise ExportError("WATCH_LEASE_EXPORT_IDENTITY_INVALID")
    if os.path.abspath(source) == os.path.abspath(destination):
        raise ExportError("WATCH_LEASE_EXPORT_PATH_INVALID")
    _remove_reader_output(
        destination, reader_gid, require_root=require_root)
    owner_uid = 0 if require_root else os.geteuid()
    before = os.lstat(source)
    if (
            not stat.S_ISREG(before.st_mode) or
            stat.S_ISLNK(before.st_mode) or before.st_nlink != 1 or
            stat.S_IMODE(before.st_mode) != 0o440 or
            (before.st_uid, before.st_gid) != (owner_uid, agent_gid) or
            not 2 <= before.st_size <= MAX_BYTES):
        raise ExportError("WATCH_LEASE_EXPORT_SOURCE_METADATA_INVALID")
    descriptor = os.open(
        source, os.O_RDONLY | os.O_CLOEXEC | getattr(os, "O_NOFOLLOW", 0))
    try:
        contents = os.read(descriptor, MAX_BYTES + 1)
        after = os.fstat(descriptor)
        path_after = os.stat(source, follow_symlinks=False)
    finally:
        os.close(descriptor)
    identity = lambda item: (
        item.st_dev, item.st_ino, item.st_mode, item.st_nlink,
        item.st_uid, item.st_gid, item.st_size,
        item.st_mtime_ns, item.st_ctime_ns,
    )
    if (
            len(contents) > MAX_BYTES or len(contents) != before.st_size or
            identity(before) != identity(after) or
            identity(after) != identity(path_after)):
        raise ExportError("WATCH_LEASE_EXPORT_SOURCE_CHANGED")
    try:
        document = json.loads(
            contents.decode("ascii", errors="strict"),
            object_pairs_hook=_pairs,
            parse_constant=lambda _: (_ for _ in ()).throw(
                ExportError("WATCH_LEASE_EXPORT_NON_FINITE")),
        )
    except (UnicodeError, json.JSONDecodeError, ValueError) as error:
        raise ExportError("WATCH_LEASE_EXPORT_JSON_INVALID") from error
    if contents != _canonical(document):
        raise ExportError("WATCH_LEASE_EXPORT_NOT_CANONICAL")
    if not _valid_lease_receipt(document, domain_id, agent_uid):
        raise ExportError("WATCH_LEASE_EXPORT_CONTRACT_INVALID")
    try:
        _reader_publish(
            destination,
            contents,
            reader_gid,
            require_root=require_root,
            prefix=".watch-lease-receipt-",
            directory_mode=_directory_mode,
        )
    except BaseException:
        try:
            _remove_reader_output(
                destination, reader_gid, require_root=require_root)
        except BaseException:
            pass
        raise
    published = destination.read_bytes()
    if published != contents:
        raise ExportError("WATCH_LEASE_EXPORT_DIGEST_DRIFT")
    return document


def export_binding_receipt(
        snapshot_path: Path,
        lease_receipt_path: Path,
        destination: Path,
        agent_uid: int,
        reader_uid: int,
        reader_gid: int,
        *,
        exported_at_ms: int | None = None,
        require_root: bool = True,
        _directory_mode: int = 0o750,
) -> dict[str, object]:
    """Commit the exact root-published snapshot/lease pair for a reader."""

    if require_root and os.geteuid() != 0:
        raise ExportError("WATCH_BINDING_EXPORT_ROOT_REQUIRED")
    if any(
            isinstance(value, bool) or not isinstance(value, int) or value <= 0
            for value in (agent_uid, reader_uid, reader_gid)):
        raise ExportError("WATCH_BINDING_EXPORT_IDENTITY_INVALID")
    owner_uid = 0 if require_root else os.geteuid()
    snapshot, snapshot_contents = _read_root_export(
        snapshot_path,
        owner_uid=owner_uid,
        reader_gid=reader_gid,
        label="WATCH_BINDING_SNAPSHOT",
    )
    lease, lease_contents = _read_root_export(
        lease_receipt_path,
        owner_uid=owner_uid,
        reader_gid=reader_gid,
        label="WATCH_BINDING_LEASE",
    )
    domain_id = snapshot.get("domain_id")
    if (
            not isinstance(domain_id, str) or not domain_id or
            not _valid_contract(snapshot, agent_uid) or
            not _valid_lease_receipt(lease, domain_id, agent_uid)):
        raise ExportError("WATCH_BINDING_EXPORT_CONTRACT_INVALID")
    snapshot_body = dict(snapshot)
    snapshot_claimed = snapshot_body.pop("body_sha256", None)
    lease_body = dict(lease)
    lease_claimed = lease_body.pop("body_sha256", None)
    if (
            snapshot_claimed != _digest_bytes(_canonical(snapshot_body)) or
            lease_claimed != _digest_bytes(_canonical(lease_body))):
        raise ExportError("WATCH_BINDING_EXPORT_DIGEST_INVALID")
    generated_at_ms = _timestamp(snapshot.get("generated_at_ms"))
    accepted_at_ms = _timestamp(lease.get("accepted_at_ms"))
    expires_at_ms = _timestamp(lease.get("expires_at_ms"))
    quote = (
        snapshot.get("reads", {}).get("market.get_quote")
        if isinstance(snapshot.get("reads"), dict) else None
    )
    stale_after_ms = (
        _timestamp(quote.get("stale_after_ms"))
        if isinstance(quote, dict) else None
    )
    exported = (
        time.time_ns() // 1_000_000
        if exported_at_ms is None else exported_at_ms
    )
    if (
            generated_at_ms is None or accepted_at_ms is None or
            expires_at_ms is None or stale_after_ms is None or
            isinstance(exported, bool) or not isinstance(exported, int) or
            not max(generated_at_ms, accepted_at_ms) <= exported <=
            min(expires_at_ms, stale_after_ms)):
        raise ExportError("WATCH_BINDING_EXPORT_TIME_INVALID")
    body = {
        "schema": "hepta.shadow-watch-export-receipt.v1",
        "version": 1,
        "domain_id": domain_id,
        "agent_uid": agent_uid,
        "reader_uid": reader_uid,
        "reader_gid": reader_gid,
        "boundary": "WATCH_EXPORT",
        "lease_generation": lease["lease_generation"],
        "lease_receipt_body_sha256": lease_claimed,
        "lease_receipt_file_sha256": _digest_bytes(lease_contents),
        "snapshot_body_sha256": snapshot_claimed,
        "snapshot_file_sha256": _digest_bytes(snapshot_contents),
        "snapshot_generated_at_ms": generated_at_ms,
        "exported_at_ms": exported,
        "paper_authorized": False,
        "live_authorized": False,
        "mutation_attempted": False,
        "direct_broker_access": False,
    }
    document = {**body, "body_sha256": _digest_bytes(_canonical(body))}
    _remove_reader_output(
        destination,
        reader_gid,
        require_root=require_root,
    )
    _reader_publish(
        destination,
        _canonical(document),
        reader_gid,
        require_root=require_root,
        prefix=".watch-export-receipt-",
        directory_mode=_directory_mode,
    )
    published, published_contents = _read_root_export(
        destination,
        owner_uid=owner_uid,
        reader_gid=reader_gid,
        label="WATCH_BINDING_RECEIPT",
    )
    if (
            published != document or
            published_contents != _canonical(document) or
            set(published) != EXPORT_RECEIPT_FIELDS):
        raise ExportError("WATCH_BINDING_EXPORT_PUBLISH_INVALID")
    return document


def _fault(stage: str) -> None:
    """A narrow deterministic seam used to prove every publish crash point."""

    if os.environ.get("HEPTA_SHADOW_EXPORTER_FAULT_STAGE") == stage:
        raise ExportError("WATCH_EXPORT_FAULT_" + stage.upper().replace(".", "_"))


def _fsync_directory(path: Path) -> None:
    descriptor = os.open(
        path,
        os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC |
        getattr(os, "O_NOFOLLOW", 0),
    )
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _directory_identity(metadata: os.stat_result) -> tuple[object, ...]:
    return (
        metadata.st_dev, metadata.st_ino, metadata.st_mode,
        metadata.st_uid, metadata.st_gid,
    )


def _ensure_owned_directory(
        directory: Path,
        *,
        owner_uid: int,
        reader_gid: int,
        mode: int,
) -> None:
    created = False
    try:
        directory.mkdir(mode=mode)
        created = True
    except FileExistsError:
        pass
    try:
        descriptor = os.open(
            directory,
            os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC |
            getattr(os, "O_NOFOLLOW", 0),
        )
    except OSError as error:
        raise ExportError("WATCH_EXPORT_DIRECTORY_UNSAFE") from error
    try:
        if created:
            os.fchown(descriptor, owner_uid, reader_gid)
            os.fchmod(descriptor, mode)
        opened = os.fstat(descriptor)
        named = directory.lstat()
        if (
                not stat.S_ISDIR(opened.st_mode) or
                _directory_identity(opened) != _directory_identity(named) or
                opened.st_uid != owner_uid or opened.st_gid != reader_gid or
                stat.S_IMODE(opened.st_mode) != mode):
            raise ExportError("WATCH_EXPORT_DIRECTORY_UNSAFE")
    finally:
        os.close(descriptor)


@contextmanager
def _locked_export_directory(
        directory: Path,
        reader_gid: int,
        *,
        require_root: bool,
) -> Iterator[None]:
    owner_uid = 0 if require_root else os.geteuid()
    _ensure_owned_directory(
        directory,
        owner_uid=owner_uid,
        reader_gid=reader_gid,
        mode=0o750,
    )
    descriptor = os.open(
        directory,
        os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC |
        getattr(os, "O_NOFOLLOW", 0),
    )
    try:
        opened = os.fstat(descriptor)
        named = directory.lstat()
        if (
                not stat.S_ISDIR(opened.st_mode) or
                _directory_identity(opened) != _directory_identity(named) or
                opened.st_uid != owner_uid or opened.st_gid != reader_gid or
                stat.S_IMODE(opened.st_mode) != 0o750):
            raise ExportError("WATCH_EXPORT_DIRECTORY_UNSAFE")
        fcntl.flock(descriptor, fcntl.LOCK_EX)
        locked = os.fstat(descriptor)
        named = directory.lstat()
        if _directory_identity(locked) != _directory_identity(named):
            raise ExportError("WATCH_EXPORT_DIRECTORY_CHANGED")
        yield
    finally:
        try:
            fcntl.flock(descriptor, fcntl.LOCK_UN)
        finally:
            os.close(descriptor)


def _valid_commit_document(document: object) -> bool:
    if not isinstance(document, dict) or set(document) != COMMIT_FIELDS:
        return False
    body = dict(document)
    claimed = body.pop("body_sha256", None)
    sequence = document.get("commit_sequence")
    status_value = document.get("authority_status")
    active = status_value == "ACTIVE"
    generation = document.get("generation")
    binding_fields = (
        "lease_generation", "snapshot_body_sha256", "snapshot_file_sha256",
        "lease_receipt_body_sha256", "lease_receipt_file_sha256",
        "export_receipt_body_sha256", "export_receipt_file_sha256",
        "committed_at_ms",
    )
    if active:
        binding_valid = (
            isinstance(generation, str) and GENERATION.fullmatch(generation)
            is not None and
            int(GENERATION.fullmatch(generation).group(1)) == sequence and
            type(document.get("lease_generation")) is int and
            int(document["lease_generation"]) >= 1 and
            all(_digest(document.get(field)) for field in binding_fields[1:7]) and
            _timestamp(document.get("committed_at_ms")) is not None and
            document.get("close_reason") is None
        )
    else:
        binding_valid = (
            status_value in {"CLOSING", "CLOSED"} and generation is None and
            type(document.get("lease_generation")) is int and
            int(document["lease_generation"]) >= 1 and
            _digest(document.get("lease_receipt_body_sha256")) and
            all(
                document.get(field) is None
                for field in (
                    "snapshot_body_sha256", "snapshot_file_sha256",
                    "lease_receipt_file_sha256",
                    "export_receipt_body_sha256",
                    "export_receipt_file_sha256", "committed_at_ms",
                )) and
            isinstance(document.get("close_reason"), str) and
            bool(document["close_reason"])
        )
    return (
        document.get("schema") == "hepta.shadow-watch-export-commit.v1" and
        document.get("version") == 1 and binding_valid and
        type(sequence) is int and 1 <= sequence < (1 << 64) and
        _timestamp(document.get("authority_changed_at_ms")) is not None and
        isinstance(document.get("domain_id"), str) and
        bool(document["domain_id"]) and
        all(
            type(document.get(field)) is int and int(document[field]) > 0
            for field in ("agent_uid", "reader_uid", "reader_gid")) and
        document.get("paper_authorized") is False and
        document.get("live_authorized") is False and
        document.get("mutation_attempted") is False and
        document.get("direct_broker_access") is False and
        _digest(claimed) and claimed == _digest_bytes(_canonical(body))
    )


def _next_commit_sequence(
        export_directory: Path,
        *,
        owner_uid: int,
        reader_gid: int,
        domain_id: str,
        agent_uid: int,
        reader_uid: int,
        lease: dict[str, object],
) -> int:
    current = export_directory / COMMIT_NAME
    try:
        document, _contents = _read_root_export(
            current,
            owner_uid=owner_uid,
            reader_gid=reader_gid,
            label="WATCH_EXPORT_COMMIT",
        )
    except FileNotFoundError:
        return 1
    if (
            not _valid_commit_document(document) or
            document.get("domain_id") != domain_id or
            document.get("agent_uid") != agent_uid or
            document.get("reader_uid") != reader_uid or
            document.get("reader_gid") != reader_gid):
        raise ExportError("WATCH_EXPORT_COMMIT_INVALID")
    sequence = int(document["commit_sequence"])
    if sequence >= (1 << 64) - 1:
        raise ExportError("WATCH_EXPORT_COMMIT_SEQUENCE_EXHAUSTED")
    status_value = document["authority_status"]
    same_authority = (
        status_value == "ACTIVE" and
        lease.get("lease_generation") == document.get("lease_generation") and
        lease.get("body_sha256") ==
        document.get("lease_receipt_body_sha256")
    )
    rotation = (
        status_value == "ACTIVE" and
        lease.get("operation") == "ROTATE" and
        lease.get("previous_lease_generation") ==
        document.get("lease_generation") and
        lease.get("previous_receipt_body_sha256") ==
        document.get("lease_receipt_body_sha256") and
        lease.get("lease_generation") ==
        int(document["lease_generation"]) + 1
    )
    next_campaign = (
        status_value == "CLOSED" and
        lease.get("operation") == "PROVISION" and
        lease.get("body_sha256") !=
        document.get("lease_receipt_body_sha256") and
        type(lease.get("accepted_at_ms")) is int and
        int(lease["accepted_at_ms"]) >=
        int(document["authority_changed_at_ms"])
    )
    if not (same_authority or rotation or next_campaign):
        raise ExportError("WATCH_EXPORT_AUTHORITY_ENDED_OR_STALE")
    return sequence + 1


def _remove_generation_tree(
        path: Path,
        *,
        owner_uid: int,
        reader_gid: int,
) -> None:
    metadata = path.lstat()
    if (
            not stat.S_ISDIR(metadata.st_mode) or stat.S_ISLNK(metadata.st_mode) or
            metadata.st_uid != owner_uid or metadata.st_gid != reader_gid or
            stat.S_IMODE(metadata.st_mode) not in {0o700, 0o750}):
        raise ExportError("WATCH_EXPORT_GENERATION_UNSAFE")
    names = sorted(os.listdir(path))
    if not set(names).issubset(set(EXPORT_FILES)):
        raise ExportError("WATCH_EXPORT_GENERATION_INVENTORY_UNSAFE")
    for name in names:
        _remove_reader_output(
            path / name,
            reader_gid,
            require_root=owner_uid == 0,
        )
    os.rmdir(path)


def _cleanup_after_commit(
        export_directory: Path,
        generation: str,
        *,
        owner_uid: int,
        reader_gid: int,
) -> None:
    generations = export_directory / GENERATIONS_NAME
    for name in sorted(os.listdir(generations)):
        if name == generation:
            continue
        if GENERATION.fullmatch(name) is None and STAGING.fullmatch(name) is None:
            raise ExportError("WATCH_EXPORT_GENERATION_INVENTORY_UNSAFE")
        _remove_generation_tree(
            generations / name,
            owner_uid=owner_uid,
            reader_gid=reader_gid,
        )
    for name in EXPORT_FILES:
        legacy = export_directory / name
        if os.path.lexists(legacy):
            _remove_reader_output(
                legacy,
                reader_gid,
                require_root=owner_uid == 0,
            )
    _fsync_directory(generations)
    _fsync_directory(export_directory)


def publish_triplet(
        source: Path,
        destination: Path,
        agent_uid: int,
        agent_gid: int,
        reader_uid: int,
        reader_gid: int,
        lease_receipt_source: Path,
        lease_receipt_destination: Path,
        export_receipt_destination: Path,
        *,
        require_root: bool = True,
) -> tuple[dict[str, object], dict[str, object], dict[str, object],
           dict[str, object]]:
    """Crash-atomically publish one hash-bound WATCH export generation."""

    destinations = (
        destination, lease_receipt_destination, export_receipt_destination)
    export_directory = destination.parent
    if (
            tuple(path.name for path in destinations) != EXPORT_FILES or
            any(path.parent != export_directory for path in destinations)):
        raise ExportError("WATCH_EXPORT_TRIPLET_PATH_INVALID")
    owner_uid = 0 if require_root else os.geteuid()
    with _locked_export_directory(
            export_directory, reader_gid, require_root=require_root):
        generations = export_directory / GENERATIONS_NAME
        _ensure_owned_directory(
            generations,
            owner_uid=owner_uid,
            reader_gid=reader_gid,
            mode=0o750,
        )
        staging = Path(tempfile.mkdtemp(prefix=".staging-", dir=generations))
        os.chown(staging, owner_uid, reader_gid)
        os.chmod(staging, 0o700)
        committed = False
        try:
            snapshot = export(
                source,
                staging / EXPORT_FILES[0],
                agent_uid,
                agent_gid,
                reader_uid,
                reader_gid,
                require_root=require_root,
                _directory_mode=0o700,
            )
            _fault("after_snapshot")
            lease = export_lease_receipt(
                lease_receipt_source,
                staging / EXPORT_FILES[1],
                str(snapshot["domain_id"]),
                agent_uid,
                agent_gid,
                reader_uid,
                reader_gid,
                require_root=require_root,
                _directory_mode=0o700,
            )
            _fault("after_lease")
            binding = export_binding_receipt(
                staging / EXPORT_FILES[0],
                staging / EXPORT_FILES[1],
                staging / EXPORT_FILES[2],
                agent_uid,
                reader_uid,
                reader_gid,
                require_root=require_root,
                _directory_mode=0o700,
            )
            _fault("after_binding")
            sequence = _next_commit_sequence(
                export_directory,
                owner_uid=owner_uid,
                reader_gid=reader_gid,
                domain_id=str(snapshot["domain_id"]),
                agent_uid=agent_uid,
                reader_uid=reader_uid,
                lease=lease,
            )
            suffix = staging.name.removeprefix(".staging-")
            generation = f"generation-{sequence:020d}-{suffix}"
            if GENERATION.fullmatch(generation) is None:
                raise ExportError("WATCH_EXPORT_GENERATION_INVALID")
            for name in EXPORT_FILES:
                path = staging / name
                metadata = path.lstat()
                if (
                        not stat.S_ISREG(metadata.st_mode) or
                        metadata.st_nlink != 1 or metadata.st_uid != owner_uid or
                        metadata.st_gid != reader_gid or
                        stat.S_IMODE(metadata.st_mode) != 0o440):
                    raise ExportError("WATCH_EXPORT_GENERATION_FILE_UNSAFE")
            os.chmod(staging, 0o750)
            _fsync_directory(staging)
            _fault("after_generation_fsync")
            final = generations / generation
            os.rename(staging, final)
            committed = True
            _fsync_directory(generations)
            _fault("after_generation_commit")
            snapshot_contents = (final / EXPORT_FILES[0]).read_bytes()
            lease_contents = (final / EXPORT_FILES[1]).read_bytes()
            binding_contents = (final / EXPORT_FILES[2]).read_bytes()
            now_ms = time.time_ns() // 1_000_000
            body = {
                "schema": "hepta.shadow-watch-export-commit.v1",
                "version": 1,
                "authority_status": "ACTIVE",
                "authority_changed_at_ms": now_ms,
                "close_reason": None,
                "commit_sequence": sequence,
                "generation": generation,
                "domain_id": snapshot["domain_id"],
                "agent_uid": agent_uid,
                "reader_uid": reader_uid,
                "reader_gid": reader_gid,
                "lease_generation": lease["lease_generation"],
                "snapshot_body_sha256": snapshot["body_sha256"],
                "snapshot_file_sha256": _digest_bytes(snapshot_contents),
                "lease_receipt_body_sha256": lease["body_sha256"],
                "lease_receipt_file_sha256": _digest_bytes(lease_contents),
                "export_receipt_body_sha256": binding["body_sha256"],
                "export_receipt_file_sha256": _digest_bytes(binding_contents),
                "committed_at_ms": now_ms,
                "paper_authorized": False,
                "live_authorized": False,
                "mutation_attempted": False,
                "direct_broker_access": False,
            }
            commit = {**body, "body_sha256": _digest_bytes(_canonical(body))}
            if not _valid_commit_document(commit):
                raise ExportError("WATCH_EXPORT_COMMIT_BUILD_INVALID")
            _reader_publish(
                export_directory / COMMIT_NAME,
                _canonical(commit),
                reader_gid,
                require_root=require_root,
                prefix=".current-",
            )
            _fault("after_pointer_commit")
            _cleanup_after_commit(
                export_directory,
                generation,
                owner_uid=owner_uid,
                reader_gid=reader_gid,
            )
            _fault("after_cleanup")
            return snapshot, lease, binding, commit
        finally:
            if not committed and staging.exists():
                _remove_generation_tree(
                    staging,
                    owner_uid=owner_uid,
                    reader_gid=reader_gid,
                )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--destination", type=Path, required=True)
    parser.add_argument("--agent-uid", type=int, required=True)
    parser.add_argument("--agent-gid", type=int, required=True)
    parser.add_argument("--reader-uid", type=int, required=True)
    parser.add_argument("--reader-gid", type=int, required=True)
    parser.add_argument("--lease-receipt-source", type=Path)
    parser.add_argument("--lease-receipt-destination", type=Path)
    parser.add_argument("--export-receipt-destination", type=Path)
    arguments = parser.parse_args()
    try:
        lease_source = arguments.lease_receipt_source
        lease_destination = arguments.lease_receipt_destination
        export_destination = arguments.export_receipt_destination
        if any(value is None for value in (
                lease_source, lease_destination, export_destination)):
            raise ExportError("WATCH_ATOMIC_EXPORT_ARGUMENTS_REQUIRED")
        assert lease_source is not None
        assert lease_destination is not None
        assert export_destination is not None
        (document, lease_document, binding_document,
         _commit_document) = publish_triplet(
            arguments.source,
            arguments.destination,
            arguments.agent_uid,
            arguments.agent_gid,
            arguments.reader_uid,
            arguments.reader_gid,
            lease_source,
            lease_destination,
            export_destination,
            require_root=True,
        )
    except (ExportError, OSError, ValueError) as error:
        print("hepta_shadow_watch_exporter: FAIL: " + str(error), file=sys.stderr)
        return 78
    result = {
        "status": "ok", "body_sha256": document["body_sha256"],
        "mutation_attempted": False, "live_authorized": False,
    }
    result["watch_lease_receipt_body_sha256"] = (
        lease_document["body_sha256"])
    result["watch_export_receipt_body_sha256"] = (
        binding_document["body_sha256"])
    print(json.dumps(result, sort_keys=True, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
