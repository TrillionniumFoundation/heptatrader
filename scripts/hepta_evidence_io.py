#!/usr/bin/env python3
"""Bounded, descriptor-based evidence I/O shared by PAPER tools.

Checks protect a concrete boundary: evidence supplied by a separate process is
never followed through links, reparsed from different bytes, or read unbounded.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import stat
import tempfile
from typing import Any

MAX_JSON_BYTES = 4 * 1024 * 1024


class EvidenceError(ValueError):
    pass


def unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise EvidenceError(f'duplicate JSON key: {key}')
        result[key] = value
    return result


def reject_constant(value):
    raise EvidenceError(f'non-finite JSON number: {value}')


def loads(data: bytes | str) -> Any:
    try:
        return json.loads(data, object_pairs_hook=unique_object, parse_constant=reject_constant)
    except (ValueError, UnicodeError) as exc:
        raise EvidenceError(f'invalid JSON: {exc}') from exc


def _read_fd(fd: int, limit: int) -> bytes:
    initial = os.fstat(fd)
    if not stat.S_ISREG(initial.st_mode) or initial.st_nlink != 1:
        raise EvidenceError('expected a regular single-link file')
    if initial.st_size > limit:
        raise EvidenceError('evidence exceeds byte limit')
    chunks = []
    size = 0
    while True:
        data = os.read(fd, min(65536, limit + 1 - size))
        if not data:
            break
        chunks.append(data)
        size += len(data)
        if size > limit:
            raise EvidenceError('evidence exceeds byte limit')
    final = os.fstat(fd)
    if (initial.st_dev, initial.st_ino, initial.st_size, initial.st_mtime_ns, initial.st_ctime_ns) != (
        final.st_dev, final.st_ino, final.st_size, final.st_mtime_ns, final.st_ctime_ns
    ) or size != initial.st_size:
        raise EvidenceError('evidence changed while reading')
    return b''.join(chunks)


def read_bytes(path: Path, limit: int = MAX_JSON_BYTES) -> bytes:
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    try:
        return _read_fd(fd, limit)
    finally:
        os.close(fd)


def read_relative(root: Path, relative: str, limit: int = MAX_JSON_BYTES) -> bytes:
    if not isinstance(relative, str) or not relative or '\\' in relative or any(
        c < ' ' or c == '\x7f' for c in relative
    ):
        raise EvidenceError('invalid evidence path')
    parts = relative.split('/')
    if any(part in ('', '.', '..') for part in parts):
        raise EvidenceError('non-canonical evidence path')
    directory = os.open(root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        for part in parts[:-1]:
            child = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=directory)
            os.close(directory)
            directory = child
        fd = os.open(parts[-1], os.O_RDONLY | os.O_NONBLOCK | os.O_NOFOLLOW, dir_fd=directory)
        try:
            return _read_fd(fd, limit)
        finally:
            os.close(fd)
    finally:
        os.close(directory)


def load_json(path: Path, limit: int = MAX_JSON_BYTES) -> Any:
    return loads(read_bytes(path, limit))


def sha256_file(path: Path) -> str:
    return hashlib.sha256(read_bytes(path, 256 * 1024 * 1024)).hexdigest()


def canonical_bytes(value: Any) -> bytes:
    return (json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False) + '\n').encode()


def write_json(path: Path, value: Any, *, replace: bool = False) -> None:
    """Durably publish complete JSON; default publication never replaces a leaf."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(dir=path.parent, prefix='.evidence-')
    temporary = Path(name)
    try:
        with os.fdopen(fd, 'wb') as stream:
            stream.write(canonical_bytes(value))
            stream.flush()
            os.fsync(stream.fileno())
        if replace:
            os.replace(temporary, path)
        else:
            # link is an atomic no-replace publication. Remove the temporary
            # name before readers are admitted; the final file has one link.
            os.link(temporary, path, follow_symlinks=False)
            temporary.unlink()
        directory = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        temporary.unlink(missing_ok=True)


def controller_digest() -> str:
    """Bind the portable harness, its imports, wrappers and stage policy as one unit."""
    root = Path(__file__).resolve().parents[1]
    paths = (
        'scripts/hepta_evidence_io.py', 'scripts/verify_ib_paper_rollout.py',
        'scripts/hepta_paper_campaign.py', 'scripts/hepta_ib_paper_harness.py',
        'scripts/hepta_paper_rollout_host.py',
        'scripts/run_ib_paper_artifact_rollout.sh', 'scripts/hepta_campaign_evidence.sh',
        'docs/ib-paper-rollout-policy-v1.json',
    )
    return hashlib.sha256(canonical_bytes({path: sha256_file(root/path) for path in paths})).hexdigest()
