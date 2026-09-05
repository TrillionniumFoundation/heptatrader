"""Bounded, no-follow reads for external receipt envelopes.

This module establishes file integrity, not the identity of a receipt issuer.
It must never turn an untrusted JSON document into qualification authority.
"""
from __future__ import annotations

import json
import math
import os
from pathlib import Path, PurePosixPath
import stat
from typing import Any

MAX_RECEIPT_BYTES = 4 * 1024 * 1024
MAX_RECEIPT_PATH_BYTES = 4096
MAX_RECEIPT_DIRECTORIES = 64


def unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate JSON key: {key}")
        result[key] = value
    return result


def reject_constant(value: str) -> None:
    raise ValueError(f"non-finite JSON constant: {value}")


def finite_float(value: str) -> float:
    # parse_constant sees NaN/Infinity literals, not valid JSON exponent tokens
    # that overflow the host float (for example 1e999). Reject both spellings.
    number = float(value)
    if not math.isfinite(number):
        raise ValueError("non-finite JSON number")
    return number


def decode_object(data: bytes) -> dict[str, Any]:
    value = json.loads(data.decode("utf-8"), object_pairs_hook=unique_object,
                       parse_constant=reject_constant, parse_float=finite_float)
    if not isinstance(value, dict):
        raise ValueError("receipt root must be an object")
    return value


def _identity(value: os.stat_result) -> tuple[int, ...]:
    return (value.st_dev, value.st_ino, value.st_mode, value.st_nlink,
            value.st_uid, value.st_gid, value.st_size,
            value.st_mtime_ns, value.st_ctime_ns)


def _directory_identity(value: os.stat_result) -> tuple[int, ...]:
    # Unrelated sibling creation changes directory times/link counts, not the
    # selected binding. Permission/ownership changes do invalidate this read.
    return (value.st_dev, value.st_ino, value.st_mode, value.st_uid, value.st_gid)


def _check_directory(value: os.stat_result, protected: bool) -> None:
    if not stat.S_ISDIR(value.st_mode):
        raise ValueError("receipt directory is not a directory")
    # Ancestors outside the selected evidence root may include /tmp. A sticky
    # shared ancestor is allowed; the selected root and descendants never are.
    if value.st_mode & stat.S_IWOTH and (protected or not value.st_mode & stat.S_ISVTX):
        raise ValueError("receipt directory is world-writable")


def _checked_path(root: Path, relative: object) -> tuple[tuple[str, ...], int, str]:
    if not isinstance(relative, str) or not relative:
        raise ValueError("closed state requires qualification_receipt")
    if len(relative) > MAX_RECEIPT_PATH_BYTES:
        raise ValueError("qualification_receipt path exceeds byte limit")
    path = PurePosixPath(relative)
    if (path.is_absolute() or not path.parts or any(p in (".", "..") for p in path.parts)
            or path.as_posix() != relative or "\\" in relative or "\x00" in relative):
        raise ValueError("qualification_receipt path is unsafe")
    # Never resolve root: doing so erases symlinks before O_NOFOLLOW can reject
    # them. A relative root is anchored once to the trusted process's cwd.
    selected = Path(root)
    if selected.anchor not in ("", "/") or ".." in selected.parts or "\x00" in str(selected):
        raise ValueError("receipt root path is unsafe")
    absolute = selected if selected.is_absolute() else Path.cwd() / selected
    directories = absolute.parts[1:] + path.parts[:-1]
    if len(directories) > MAX_RECEIPT_DIRECTORIES:
        raise ValueError("receipt directory depth exceeds limit")
    full_path = absolute / path
    if len(os.fsencode(full_path)) > MAX_RECEIPT_PATH_BYTES:
        raise ValueError("qualification_receipt path exceeds byte limit")
    if any(len(os.fsencode(p)) > 255 for p in (*directories, path.name)):
        raise ValueError("receipt path component exceeds byte limit")
    return directories, len(absolute.parts) - 1, path.name


def read_receipt(root: Path, relative: object) -> dict[str, Any]:
    """Read a bounded historical envelope through retained, no-follow FDs.

    The selected root, its ancestors and the relative receipt path are walked
    from /, without symlink resolution. Validate bindings before AND after JSON
    decoding. Close every owned FD even when a close fails; never retry close.
    This is not an atomic filesystem snapshot or authentication of an issuer.
    """
    directories, root_depth, leaf = _checked_path(root, relative)
    for name in ("O_NOFOLLOW", "O_DIRECTORY", "O_NONBLOCK"):
        if not hasattr(os, name):
            raise ValueError(f"secure receipt reads require {name}")
    if os.open not in os.supports_dir_fd or os.stat not in os.supports_dir_fd:
        raise ValueError("secure receipt reads require directory-relative syscalls")
    if os.stat not in os.supports_follow_symlinks:
        raise ValueError("secure receipt reads require no-follow metadata checks")

    # Allocate ownership slots BEFORE any syscall acquires a descriptor.
    # Assignment into these slots cannot grow a list during exception cleanup.
    descriptors = [-1] * (len(directories) + 2)
    directory_info: list[os.stat_result | None] = [None] * (len(directories) + 1)
    used = 0
    directory_flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
    directory_flags |= getattr(os, "O_CLOEXEC", 0)
    try:
        fd = os.open("/", directory_flags)
        descriptors[used] = fd
        used += 1
        info = os.fstat(fd)
        _check_directory(info, root_depth == 0)
        directory_info[0] = info
        for depth, component in enumerate(directories, 1):
            fd = os.open(component, directory_flags, dir_fd=descriptors[depth - 1])
            descriptors[used] = fd
            used += 1
            info = os.fstat(fd)
            _check_directory(info, depth >= root_depth)
            directory_info[depth] = info
        parent = descriptors[used - 1]
        flags = os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK
        flags |= getattr(os, "O_CLOEXEC", 0)
        fd = os.open(leaf, flags, dir_fd=parent)
        descriptors[used] = fd
        used += 1
        before = os.fstat(fd)
        if not stat.S_ISREG(before.st_mode):
            raise ValueError("qualification_receipt must be a regular file")
        if before.st_nlink != 1:
            raise ValueError("qualification_receipt must have exactly one hard link")
        if before.st_mode & stat.S_IWOTH:
            raise ValueError("qualification_receipt must not be world-writable")
        if not 0 < before.st_size <= MAX_RECEIPT_BYTES:
            raise ValueError("qualification_receipt size is invalid")

        # Short reads cannot create an unbounded number of retained Python byte
        # objects. Allocate the captured size once, then probe for an extra byte.
        data = bytearray(before.st_size)
        total = 0
        while total < before.st_size:
            try:
                chunk = os.read(fd, min(65536, before.st_size - total))
            except InterruptedError:
                continue
            if not chunk:
                raise ValueError("receipt length changed during read")
            data[total:total + len(chunk)] = chunk
            total += len(chunk)
        while True:
            try:
                extra = os.read(fd, 1)
                break
            except InterruptedError:
                continue
        if extra:
            raise ValueError("receipt length changed during read")

        def revalidate() -> None:
            after = os.fstat(fd)
            current = os.stat(leaf, dir_fd=parent, follow_symlinks=False)
            if _identity(before) != _identity(after) or _identity(before) != _identity(current):
                raise ValueError("receipt identity changed during read")
            for depth, captured in enumerate(directory_info):
                held = os.fstat(descriptors[depth])
                named = (os.stat(directories[depth - 1], dir_fd=descriptors[depth - 1], follow_symlinks=False)
                         if depth else os.stat("/", follow_symlinks=False))
                if (captured is None or _directory_identity(held) != _directory_identity(captured)
                        or _directory_identity(named) != _directory_identity(captured)):
                    raise ValueError("receipt directory binding changed during read")
                _check_directory(held, depth >= root_depth)
        revalidate()
        result = decode_object(bytes(data))
        revalidate()
        return result
    except OSError as exc:
        raise ValueError(f"secure receipt read failed: {exc}") from exc
    finally:
        close_error = None
        while used:
            used -= 1
            try:
                os.close(descriptors[used])
            except OSError as exc:
                # Linux releases the descriptor even when close reports an I/O
                # error. A retry could close an unrelated reused FD. Continue
                # cleaning all other FDs, and suppress any successful return.
                if close_error is None:
                    close_error = exc
        if close_error is not None:
            raise ValueError("secure receipt close failed") from close_error
