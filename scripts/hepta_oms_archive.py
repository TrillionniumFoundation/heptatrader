#!/usr/bin/env python3
"""Explicit stopped-state, lossless OMS gzip repack/restore. No online rotation.

All records and their exact decoded bytes are retained. A successful maintenance
operation proves byte preservation, not economic reconciliation or permission to
restart. Stop every writer independently, especially pre-lock versions.
"""
from __future__ import annotations

import argparse
import fcntl
import hashlib
import json
import os
from pathlib import Path
import stat
import sys
import uuid
import zlib
from contextlib import contextmanager
from typing import Callable

from verify_oms_journal_replay import validated_raw_records, JournalError


class MaintenanceError(ValueError):
    pass


def identity(st: os.stat_result) -> tuple:
    return (st.st_dev, st.st_ino, st.st_size, st.st_mode, st.st_nlink,
            st.st_uid, st.st_gid, st.st_mtime_ns, st.st_ctime_ns)


def private_file(st: os.stat_result) -> None:
    if (not stat.S_ISREG(st.st_mode) or st.st_uid != os.geteuid() or
            stat.S_IMODE(st.st_mode) != 0o600 or st.st_nlink != 1):
        raise MaintenanceError("OMS_MAINTENANCE_UNSAFE_FILE")


@contextmanager
def trusted_parent(path: Path):
    """Open every ancestor no-follow. Root sticky ancestors permit /tmp fixtures.

    The leaf parent must belong to the service UID and be mode 0700; it is the
    exclusively controlled stopped-state namespace, not arbitrary user storage.
    """
    if not path.is_absolute() or any(p in ("", ".", "..") for p in str(path).split("/")[1:]):
        raise MaintenanceError("OMS_MAINTENANCE_ABSOLUTE_CANONICAL_PATH_REQUIRED")
    fd = os.open("/", os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC)
    try:
        for component in path.parts[1:-1]:
            next_fd = os.open(component, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC,
                              dir_fd=fd)
            os.close(fd)
            fd = next_fd
            meta = os.fstat(fd)
            sticky_root = meta.st_uid == 0 and bool(meta.st_mode & stat.S_ISVTX)
            if meta.st_uid not in (0, os.geteuid()) or (meta.st_mode & 0o022 and not sticky_root):
                raise MaintenanceError("OMS_MAINTENANCE_UNTRUSTED_PARENT")
        parent = os.fstat(fd)
        if parent.st_uid != os.geteuid() or stat.S_IMODE(parent.st_mode) != 0o700:
            raise MaintenanceError("OMS_MAINTENANCE_PRIVATE_STATE_DIRECTORY_REQUIRED")
        yield fd
    finally:
        os.close(fd)


def write_all(fd: int, data: bytes) -> None:
    view = memoryview(data)
    while view:
        n = os.write(fd, view)
        if n <= 0:
            raise OSError("short journal write")
        view = view[n:]


def transform(path: Path, mode: str, *, stopped: bool = False,
              max_bytes: int = 64 * 1024 * 1024, max_records: int = 65536,
              max_record_bytes: int = 262144,
              checkpoint: Callable[[str], None] | None = None) -> dict:
    """Checkpoint is an in-process test seam; there is no production CLI fault flag."""
    if mode not in ("inspect", "compact", "expand"):
        raise MaintenanceError("OMS_MAINTENANCE_BAD_OPERATION")
    if mode != "inspect" and not stopped:
        raise MaintenanceError("OMS_MAINTENANCE_STOP_ALL_WRITERS_REQUIRED")
    checkpoint = checkpoint or (lambda phase: None)
    budgets = dict(max_bytes=max_bytes, max_records=max_records, max_record_bytes=max_record_bytes)
    with trusted_parent(path) as parent:
        fd = os.open(path.name, os.O_RDONLY | os.O_NONBLOCK | os.O_NOFOLLOW | os.O_CLOEXEC, dir_fd=parent)
        target = -1
        temp = None
        published = False
        temp_identity = None
        try:
            private_file(os.fstat(fd))
            # Even inspect excludes cooperating writers; never reports a moving
            # live snapshot as a successful offline maintenance result.
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            before = os.fstat(fd)
            private_file(before)
            def revalidate_source():
                if (identity(os.fstat(fd)) != identity(before) or
                        identity(os.stat(path.name, dir_fd=parent, follow_symlinks=False)) != identity(before)):
                    raise MaintenanceError("OMS_MAINTENANCE_SNAPSHOT_CHANGED")
                named_parent = os.stat(path.parent, follow_symlinks=False)
                held_parent = os.fstat(parent)
                if ((named_parent.st_dev, named_parent.st_ino) != (held_parent.st_dev, held_parent.st_ino) or
                        held_parent.st_uid != os.geteuid() or stat.S_IMODE(held_parent.st_mode) != 0o700):
                    raise MaintenanceError("OMS_MAINTENANCE_PARENT_CHANGED")
            revalidate_source()
            if mode != "inspect":
                temp = f".{path.name}.oms-rewrite-{uuid.uuid4().hex}"
                target = os.open(temp, os.O_RDWR | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC,
                                 0o600, dir_fd=parent)
                os.fchmod(target, 0o600)
                fcntl.flock(target, fcntl.LOCK_EX | fcntl.LOCK_NB)
                meta = os.fstat(target)
                temp_identity = (meta.st_dev, meta.st_ino)
            encoder = zlib.compressobj(6, zlib.DEFLATED, 31) if mode == "compact" else None
            digest = hashlib.sha256()
            observations: dict = {}
            for raw, _ in validated_raw_records(fd, before.st_size, observations=observations, **budgets):
                digest.update(raw)
                if target >= 0:
                    write_all(target, encoder.compress(raw) if encoder else raw)
            if encoder:
                write_all(target, encoder.flush(zlib.Z_FINISH))
            revalidate_source()
            if target >= 0:
                os.fsync(target)
                encoded = os.fstat(target)
                private_file(encoded)
                check = hashlib.sha256()
                result_observations: dict = {}
                for raw, _ in validated_raw_records(target, encoded.st_size, observations=result_observations, **budgets):
                    check.update(raw)
                if (check.digest() != digest.digest() or
                        result_observations["records"] != observations["records"]):
                    raise MaintenanceError("OMS_MAINTENANCE_BYTE_IDENTITY_MISMATCH")
                checkpoint("prepared")
                revalidate_source()
                if identity(os.fstat(target)) != identity(encoded) or identity(os.stat(temp, dir_fd=parent, follow_symlinks=False)) != identity(encoded):
                    raise MaintenanceError("OMS_MAINTENANCE_OUTPUT_CHANGED")
                os.replace(temp, path.name, src_dir_fd=parent, dst_dir_fd=parent)
                published = True
                checkpoint("replaced")
                # Hold locks on BOTH old and replacement inodes until durable.
                os.fsync(parent)
                now = os.stat(path.name, dir_fd=parent, follow_symlinks=False)
                held = os.fstat(target)
                private_file(now)
                if identity(now) != identity(held) or (now.st_dev, now.st_ino) != (encoded.st_dev, encoded.st_ino):
                    raise MaintenanceError("OMS_MAINTENANCE_PUBLISHED_IDENTITY_CHANGED")
                output_size = now.st_size
            else:
                output_size = before.st_size
            return {"schema": "heptatrader.oms-maintenance.v1", "result": "PASS",
                    "operation": mode, "records": observations["records"],
                    "logical_bytes": observations["bytes"], "logical_sha256": digest.hexdigest(),
                    "input_storage_bytes": before.st_size, "output_storage_bytes": output_size,
                    "authorization_effect": "NONE"}
        except BaseException as error:
            if published:
                # Rename may already be visible; never claim the old file is
                # current or blindly roll it back after directory sync failure.
                raise MaintenanceError("OMS_MAINTENANCE_DURABILITY_UNCERTAIN_INSPECT_CURRENT_FILE") from error
            raise
        finally:
            if target >= 0:
                os.close(target)
            if temp and not published:
                try:
                    remaining = os.stat(temp, dir_fd=parent, follow_symlinks=False)
                    if (remaining.st_dev, remaining.st_ino) == temp_identity:
                        os.unlink(temp, dir_fd=parent)
                except FileNotFoundError:
                    pass
            os.close(fd)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--journal", type=Path, required=True)
    parser.add_argument("--operation", choices=("inspect", "compact", "expand"), default="inspect")
    parser.add_argument("--stopped-state", action="store_true",
                        help="acknowledge independently stopping ALL writers, including older unlocked binaries")
    parser.add_argument("--max-bytes", type=int, default=64 * 1024 * 1024)
    parser.add_argument("--max-records", type=int, default=65536)
    parser.add_argument("--max-record-bytes", type=int, default=262144)
    args = parser.parse_args(argv)
    try:
        result = transform(args.journal, args.operation, stopped=args.stopped_state,
                           max_bytes=args.max_bytes, max_records=args.max_records,
                           max_record_bytes=args.max_record_bytes)
    except (OSError, ValueError, OverflowError) as error:
        # No journal payload or path-derived identifiers go into diagnostics.
        code = str(error) if isinstance(error, MaintenanceError) else type(error).__name__
        print(json.dumps({"result": "FAIL", "reason": code, "authorization_effect": "NONE"}), file=sys.stderr)
        return 1
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
