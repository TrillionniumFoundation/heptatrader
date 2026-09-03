"""Bounded, no-follow reads for external receipt envelopes.

This module establishes file integrity, not the identity of a receipt issuer.
It must never turn an untrusted JSON document into qualification authority.
"""
from __future__ import annotations

import json
import os
from pathlib import Path, PurePosixPath
import stat
from typing import Any

MAX_RECEIPT_BYTES = 4 * 1024 * 1024


def unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate JSON key: {key}")
        result[key] = value
    return result


def reject_constant(value: str) -> None:
    raise ValueError(f"non-finite JSON constant: {value}")


def decode_object(data: bytes) -> dict[str, Any]:
    value = json.loads(data.decode("utf-8"), object_pairs_hook=unique_object,
                       parse_constant=reject_constant)
    if not isinstance(value, dict):
        raise ValueError("receipt root must be an object")
    return value


def _identity(value: os.stat_result) -> tuple[int, ...]:
    return (value.st_dev, value.st_ino, value.st_mode, value.st_nlink,
            value.st_size, value.st_mtime_ns, value.st_ctime_ns)


def read_receipt(root: Path, relative: object) -> dict[str, Any]:
    """Read one regular, single-link receipt while retaining directory FDs.

    Reject symlinks in *every* component, even those resolving inside root.
    O_NONBLOCK prevents a substituted FIFO from hanging the verifier. Path
    bindings and descriptor metadata are rechecked after the bounded read.
    Unsupported platforms fail closed rather than falling back to Path.read.
    """
    if not isinstance(relative, str) or not relative:
        raise ValueError("closed state requires qualification_receipt")
    path = PurePosixPath(relative)
    if (path.is_absolute() or not path.parts or any(p in (".", "..") for p in path.parts)
            or path.as_posix() != relative or "\\" in relative or "\x00" in relative):
        raise ValueError("qualification_receipt path is unsafe")
    for name in ("O_NOFOLLOW", "O_DIRECTORY", "O_NONBLOCK"):
        if not hasattr(os, name):
            raise ValueError(f"secure receipt reads require {name}")
    if os.open not in os.supports_dir_fd or os.stat not in os.supports_dir_fd:
        raise ValueError("secure receipt reads require directory-relative syscalls")

    root = root.resolve(strict=True)
    descriptors: list[int] = []
    bindings: list[tuple[int, str, int, int]] = []
    directory_flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
    directory_flags |= getattr(os, "O_CLOEXEC", 0)
    try:
        root_fd = os.open(root, directory_flags)
        descriptors.append(root_fd)
        root_info = os.fstat(root_fd)
        parent = root_fd
        for component in path.parts[:-1]:
            fd = os.open(component, directory_flags, dir_fd=parent)
            descriptors.append(fd)
            info = os.fstat(fd)
            if info.st_mode & stat.S_IWOTH:
                raise ValueError("receipt parent directory is world-writable")
            bindings.append((parent, component, info.st_dev, info.st_ino))
            parent = fd

        flags = os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK
        flags |= getattr(os, "O_CLOEXEC", 0)
        fd = os.open(path.name, flags, dir_fd=parent)
        descriptors.append(fd)
        before = os.fstat(fd)
        if not stat.S_ISREG(before.st_mode):
            raise ValueError("qualification_receipt must be a regular file")
        if before.st_nlink != 1:
            raise ValueError("qualification_receipt must have exactly one hard link")
        if before.st_mode & stat.S_IWOTH:
            raise ValueError("qualification_receipt must not be world-writable")
        if not 0 < before.st_size <= MAX_RECEIPT_BYTES:
            raise ValueError("qualification_receipt size is invalid")
        chunks: list[bytes] = []
        total = 0
        while total <= MAX_RECEIPT_BYTES:
            chunk = os.read(fd, min(65536, MAX_RECEIPT_BYTES + 1 - total))
            if not chunk:
                break
            chunks.append(chunk)
            total += len(chunk)
        if total != before.st_size or total > MAX_RECEIPT_BYTES:
            raise ValueError("receipt length changed during read")
        after = os.fstat(fd)
        current = os.stat(path.name, dir_fd=parent, follow_symlinks=False)
        if _identity(before) != _identity(after) or _identity(before) != _identity(current):
            raise ValueError("receipt identity changed during read")
        for parent_fd, name, device, inode in bindings:
            info = os.stat(name, dir_fd=parent_fd, follow_symlinks=False)
            if (not stat.S_ISDIR(info.st_mode) or (info.st_dev, info.st_ino) != (device, inode)
                    or info.st_mode & stat.S_IWOTH):
                raise ValueError("receipt directory binding changed during read")
        current_root = root.stat(follow_symlinks=False)
        if (not stat.S_ISDIR(current_root.st_mode)
                or (current_root.st_dev, current_root.st_ino) != (root_info.st_dev, root_info.st_ino)):
            raise ValueError("receipt root binding changed during read")
        return decode_object(b"".join(chunks))
    except OSError as exc:
        raise ValueError(f"secure receipt read failed: {exc}") from exc
    finally:
        for fd in reversed(descriptors):
            os.close(fd)
