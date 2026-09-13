#!/usr/bin/env python3
"""Lossless OFFLINE core-state archive; never rotate/reset a running ledger.

Holds the actual execution and supervisor locks. Keys/fence credentials stay
in separate secret custody. Restore creates a NEW private tree only. These
operations prove bytes/custody, not replay correctness or Broker authority.
"""
from __future__ import annotations

import argparse
from contextlib import ExitStack, contextmanager
import fcntl
import gzip
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import sys
import zlib

SCHEMA = "heptatrader.core-state-archive.v1"
CLEANUP_LOCK = Path("/run/hepta-agent/session-lease-terminal-cleanup.lock")
MAX_JOURNAL = 1 << 30
MAX_LEASE = 2 << 20
CHUNK = 65536
NAMES = {"manifest.json", "journal.jsonl.gz", "lease.bin"}
FLAGS = os.O_RDONLY | os.O_CLOEXEC | os.O_NOFOLLOW | os.O_NONBLOCK


def unique(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate archive field")
        result[key] = value
    return result


def identity(s):
    return (s.st_dev, s.st_ino, s.st_mode, s.st_uid, s.st_gid, s.st_nlink,
            s.st_size, s.st_mtime_ns, s.st_ctime_ns)


@contextmanager
def directory(path: Path, *, private=False):
    # Walk each component without following links. The operator must control
    # ancestor namespaces; descriptors pin all subsequent child operations.
    text = str(path)
    if not path.is_absolute() or text != path.as_posix() or any(x in {".", ".."} for x in text.split("/")):
        raise ValueError("absolute canonical directory required")
    fd = os.open("/", os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC)
    try:
        for part in path.parts[1:]:
            nxt = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC | os.O_NOFOLLOW, dir_fd=fd)
            os.close(fd); fd = nxt
        before = os.fstat(fd)
        if not stat.S_ISDIR(before.st_mode) or private and (stat.S_IMODE(before.st_mode) != 0o700 or before.st_uid != os.geteuid()):
            raise ValueError("unsafe private archive directory")
        yield fd
        after, named = os.fstat(fd), path.stat(follow_symlinks=False)
        if (after.st_dev, after.st_ino, after.st_mode, after.st_uid, after.st_gid) != (before.st_dev, before.st_ino, before.st_mode, before.st_uid, before.st_gid) or (named.st_dev, named.st_ino) != (before.st_dev, before.st_ino):
            raise ValueError("directory identity changed")
    finally:
        os.close(fd)


@contextmanager
def regular(parent, name, maximum, expected_uid=None, expected_gid=None, modes=(0o400, 0o600)):
    if Path(name).name != name or name in {"", ".", ".."}:
        raise ValueError("invalid leaf")
    fd = os.open(name, FLAGS, dir_fd=parent)
    try:
        before = os.fstat(fd)
        if (not stat.S_ISREG(before.st_mode) or before.st_nlink != 1
                or not 0 <= before.st_size <= maximum or stat.S_IMODE(before.st_mode) not in modes
                or expected_uid is not None and before.st_uid != expected_uid
                or expected_gid is not None and before.st_gid != expected_gid):
            raise ValueError("unsafe bounded regular file")
        if identity(before) != identity(os.stat(name, dir_fd=parent, follow_symlinks=False)):
            raise ValueError("input identity changed")
        yield fd, before
        if identity(before) != identity(os.fstat(fd)) or identity(before) != identity(os.stat(name, dir_fd=parent, follow_symlinks=False)):
            raise ValueError("input changed during operation")
    finally:
        os.close(fd)


@contextmanager
def exclusive_lock(path, *, uid, gid, mode):
    with directory(path.parent) as parent:
        # LOCK_EX on an O_RDONLY regular fd is supported by Linux flock. No
        # creation or chmod: a wrong/missing lock can never create authority.
        with regular(parent, path.name, 4096, uid, gid, (mode,)) as (fd, _):
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            try:
                yield
            finally:
                fcntl.flock(fd, fcntl.LOCK_UN)


def copy_stream(source, maximum, output=None):
    digest, total = hashlib.sha256(), 0
    while True:
        data = source.read(min(CHUNK, maximum + 1 - total))
        if not data:
            break
        total += len(data)
        if total > maximum:
            raise ValueError("archive expansion exceeds bound")
        digest.update(data)
        if output is not None:
            output.write(data)
    return {"size": total, "sha256": digest.hexdigest()}


@contextmanager
def new_file(parent, name, *, uid=None, gid=None, mode=0o600):
    fd = os.open(name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC, mode, dir_fd=parent)
    try:
        os.fchmod(fd, mode)
        if uid is not None or gid is not None:
            os.fchown(fd, -1 if uid is None else uid, -1 if gid is None else gid)
        with os.fdopen(fd, "wb", closefd=False) as output:
            yield output
            output.flush(); os.fsync(fd)
    finally:
        os.close(fd)


def key_digest(path, allowed_uids=None):
    with directory(path.parent) as parent, regular(parent, path.name, 65) as (fd, info), os.fdopen(fd, "rb", closefd=False) as source:
        if allowed_uids is not None and info.st_uid not in allowed_uids:
            raise ValueError("unexpected key custodian")
        data = source.read(66)
        if len(data) not in (32, 64, 65):
            raise ValueError("unsupported key input size")
        return hashlib.sha256(data).hexdigest()


def snapshot(execution_state, lease, key, output, execution_uid, gateway_uid, gid, *, cleanup_lock=CLEANUP_LOCK, cleanup_uid=0, cleanup_gid=0, cleanup_mode=0o644):
    # The lease lock prevents the real Gateway's shared-lifetime lease store;
    # the derived execution lock prevents the real core daemon's exclusive
    # lifetime owner. Every lock is acquired before data reads/output creation.
    with ExitStack() as stack:
        stack.enter_context(exclusive_lock(cleanup_lock, uid=cleanup_uid, gid=cleanup_gid, mode=cleanup_mode))
        stack.enter_context(exclusive_lock(execution_state / "execution-runtime.lock", uid=execution_uid, gid=gid, mode=0o600))
        e = stack.enter_context(directory(execution_state))
        em = os.fstat(e)
        if em.st_uid != execution_uid or em.st_gid != gid or stat.S_IMODE(em.st_mode) != 0o700:
            raise ValueError("unsafe execution state")
        # Distinct service lock names are an operator-error boundary. A mixed
        # state directory must not label an IB ledger as a core-only archive.
        try:
            os.stat("ib-paper-runtime.lock", dir_fd=e, follow_symlinks=False)
        except FileNotFoundError:
            pass
        else:
            raise ValueError("IB or mixed state is outside the core archive scope")
        g = stack.enter_context(directory(lease.parent))
        gm = os.fstat(g)
        if gm.st_uid != gateway_uid or gm.st_gid != gid or stat.S_IMODE(gm.st_mode) != 0o700:
            raise ValueError("unsafe gateway state")
        jfd, ji = stack.enter_context(regular(e, "oms-journal.jsonl", MAX_JOURNAL, execution_uid, gid, (0o600,)))
        lfd, li = stack.enter_context(regular(g, lease.name, MAX_LEASE, gateway_uid, gid, (0o600,)))
        kd = key_digest(key, {0, gateway_uid})
        with directory(output.parent, private=True) as parent:
            os.mkdir(output.name, 0o700, dir_fd=parent)  # exclusive: never replace
            with directory(output, private=True) as dest:
                with os.fdopen(jfd, "rb", closefd=False) as source, new_file(dest, "journal.jsonl.gz") as raw:
                    with gzip.GzipFile(filename="", mode="wb", fileobj=raw, mtime=0) as compressed:
                        journal = copy_stream(source, MAX_JOURNAL, compressed)
                with os.fdopen(lfd, "rb", closefd=False) as source, new_file(dest, "lease.bin") as target:
                    lease_info = copy_stream(source, MAX_LEASE, target)
                # Recheck the external key AND pinned source leaves before the
                # manifest. A committed snapshot never mixes observed versions.
                if key_digest(key, {0, gateway_uid}) != kd or identity(ji) != identity(os.fstat(jfd)) or identity(li) != identity(os.fstat(lfd)):
                    raise ValueError("source/key changed before archive commit")
                if identity(ji) != identity(os.stat("oms-journal.jsonl", dir_fd=e, follow_symlinks=False)) or identity(li) != identity(os.stat(lease.name, dir_fd=g, follow_symlinks=False)):
                    raise ValueError("source path changed")
                manifest = {"schema": SCHEMA, "profile": "core", "key_sha256": kd,
                    "execution_uid": execution_uid, "gateway_uid": gateway_uid, "gid": gid,
                    "journal": journal, "lease": lease_info, "authorization_effect": "NONE"}
                with new_file(dest, "manifest.json") as target:
                    target.write((json.dumps(manifest, sort_keys=True) + "\n").encode())
                os.fsync(dest)
            os.fsync(parent)
    return manifest


def load_manifest(parent):
    if set(os.listdir(parent)) != NAMES:
        raise ValueError("archive namespace is incomplete or contains extra files")
    with regular(parent, "manifest.json", 65536, os.geteuid()) as (fd, _), os.fdopen(fd, "rb", closefd=False) as stream:
        value = json.loads(stream.read(65537), object_pairs_hook=unique)
    fields = {"schema", "profile", "key_sha256", "execution_uid", "gateway_uid", "gid", "journal", "lease", "authorization_effect"}
    if not isinstance(value, dict) or set(value) != fields or value["schema"] != SCHEMA or value["profile"] != "core" or value["authorization_effect"] != "NONE":
        raise ValueError("invalid archive manifest")
    for k in ("execution_uid", "gateway_uid", "gid"):
        if type(value[k]) is not int or not 0 <= value[k] < 2**32 - 1:
            raise ValueError("invalid custody")
    if not isinstance(value["key_sha256"], str) or re.fullmatch(r"[0-9a-f]{64}", value["key_sha256"]) is None:
        raise ValueError("invalid key binding")
    for name, limit in (("journal", MAX_JOURNAL), ("lease", MAX_LEASE)):
        item = value[name]
        if not isinstance(item, dict) or set(item) != {"size", "sha256"} or type(item["size"]) is not int or not 0 <= item["size"] <= limit or not isinstance(item["sha256"], str) or re.fullmatch(r"[0-9a-f]{64}", item["sha256"]) is None:
            raise ValueError("invalid bounded payload descriptor")
    return value


def read_payload(parent, name, expected, output=None):
    compressed = name == "journal.jsonl.gz"
    limit = MAX_JOURNAL + (1 << 20) if compressed else MAX_LEASE
    with regular(parent, name, limit, os.geteuid()) as (fd, _), os.fdopen(fd, "rb", closefd=False) as raw:
        if compressed:
            with gzip.GzipFile(fileobj=raw, mode="rb") as stream:
                result = copy_stream(stream, expected["size"], output)
        else:
            result = copy_stream(raw, expected["size"], output)
    if result != expected:
        raise ValueError("archive bytes do not match manifest")


def verify(archive, key):
    with directory(archive, private=True) as parent:
        value = load_manifest(parent)
        if key_digest(key, {0, value["gateway_uid"]}) != value["key_sha256"]:
            raise ValueError("wrong separately held key")
        read_payload(parent, "journal.jsonl.gz", value["journal"])
        read_payload(parent, "lease.bin", value["lease"])
        if load_manifest(parent) != value:
            raise ValueError("archive manifest changed")
    return value


def restore(archive, key, output, execution_uid, gateway_uid, gid):
    value = verify(archive, key)  # validate EVERY payload before any output
    if (value["execution_uid"], value["gateway_uid"], value["gid"]) != (execution_uid, gateway_uid, gid):
        raise ValueError("restore identities differ from archive")
    with directory(archive, private=True) as source, directory(output.parent, private=True) as parent:
        if load_manifest(source) != value:
            raise ValueError("archive changed before restore")
        os.mkdir(output.name, 0o700, dir_fd=parent)
        with directory(output, private=True) as dest:
            for family, uid, leaf, payload, descriptor in (
                ("execution", execution_uid, "oms-journal.jsonl", "journal.jsonl.gz", "journal"),
                ("gateway", gateway_uid, "leases", "lease.bin", "lease")):
                os.mkdir(family, 0o700, dir_fd=dest)
                fd = os.open(family, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=dest)
                try:
                    with new_file(fd, leaf, uid=uid, gid=gid) as target:
                        read_payload(source, payload, value[descriptor], target)
                    os.fchown(fd, uid, gid); os.fchmod(fd, 0o700); os.fsync(fd)
                finally:
                    os.close(fd)
            if load_manifest(source) != value or key_digest(key, {0, value["gateway_uid"]}) != value["key_sha256"]:
                raise ValueError("archive/key changed during restore")
            with new_file(dest, "RESTORED.json") as target:
                target.write((json.dumps(value, sort_keys=True) + "\n").encode())
            os.fsync(dest)
        os.fsync(parent)
    return value


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("operation", choices=("snapshot", "verify", "restore"))
    parser.add_argument("--archive", type=Path)
    parser.add_argument("--execution-state", type=Path)
    parser.add_argument("--lease", type=Path)
    parser.add_argument("--key", type=Path, required=True)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--execution-uid", type=int)
    parser.add_argument("--gateway-uid", type=int)
    parser.add_argument("--gid", type=int)
    args = parser.parse_args(argv)
    try:
        if os.geteuid() != 0:
            raise ValueError("operational core archive requires an authorized root custodian")
        if args.operation != "verify":
            if args.output is None or any(type(n) is not int or not 0 < n < 2**32 - 1 for n in (args.execution_uid, args.gateway_uid, args.gid)) or args.execution_uid == args.gateway_uid:
                raise ValueError("explicit distinct core identities and new output required")
        if args.operation == "snapshot":
            if args.execution_state is None or args.lease is None:
                raise ValueError("explicit stopped state required")
            value = snapshot(args.execution_state, args.lease, args.key, args.output, args.execution_uid, args.gateway_uid, args.gid)
        else:
            if args.archive is None:
                raise ValueError("explicit archive required")
            value = verify(args.archive, args.key) if args.operation == "verify" else restore(args.archive, args.key, args.output, args.execution_uid, args.gateway_uid, args.gid)
        print(json.dumps({"operation": args.operation, "result": "PASS", "journal_bytes": value["journal"]["size"],
                          "lease_bytes": value["lease"]["size"], "authorization_effect": "NONE",
                          "replay_validated": False, "broker_qualified": False}, sort_keys=True))
        return 0
    except (OSError, ValueError, TypeError, OverflowError, RecursionError, EOFError, zlib.error):
        # Never print protected contents, keys, account identifiers or paths.
        print("CORE_STATE_ARCHIVE_REJECTED", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
