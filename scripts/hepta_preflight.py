#!/usr/bin/env python3
"""Single public HeptaTrader preflight entry point."""
from __future__ import annotations

import fcntl
import os
from pathlib import Path
import stat
import sys

_WRAPPER_NAME = __name__
_WRAPPER_FILE = __file__


def _clear_nonblocking(descriptor: int) -> None:
    nonblocking = getattr(os, "O_NONBLOCK", 0)
    if not nonblocking:
        return
    flags = fcntl.fcntl(descriptor, fcntl.F_GETFL)
    if flags & nonblocking:
        fcntl.fcntl(descriptor, fcntl.F_SETFL, flags & ~nonblocking)


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


def _inode_identity(value: os.stat_result) -> tuple[int, int, int]:
    return (value.st_dev, value.st_ino, stat.S_IFMT(value.st_mode))


def _file_identity(value: os.stat_result) -> tuple[int, ...]:
    return (
        value.st_dev,
        value.st_ino,
        value.st_mode,
        value.st_nlink,
        value.st_uid,
        value.st_gid,
        value.st_size,
        value.st_mtime_ns,
        value.st_ctime_ns,
    )


def _trusted_directory(metadata: os.stat_result) -> bool:
    return (
        stat.S_ISDIR(metadata.st_mode)
        and metadata.st_uid in {0, os.geteuid()}
        and stat.S_IMODE(metadata.st_mode) & 0o022 == 0
    )


def _open_absolute_directory(path: Path) -> int:
    absolute = Path(os.path.abspath(os.fspath(path)))
    descriptor = os.open("/", _directory_flags())
    try:
        for part in absolute.parts[1:]:
            if part in {"", ".", ".."}:
                raise OSError("non-canonical directory component")
            following = os.open(part, _directory_flags(), dir_fd=descriptor)
            metadata = os.fstat(following)
            if not stat.S_ISDIR(metadata.st_mode):
                os.close(following)
                raise OSError("path component is not a directory")
            os.close(descriptor)
            descriptor = following
        return descriptor
    except Exception:
        os.close(descriptor)
        raise


def _open_relative_directory(root: int, parts: tuple[str, ...]) -> int:
    descriptor = os.dup(root)
    try:
        for part in parts:
            if part in {"", ".", ".."} or "/" in part or "\\" in part:
                raise OSError("non-canonical relative component")
            following = os.open(part, _directory_flags(), dir_fd=descriptor)
            metadata = os.fstat(following)
            if not _trusted_directory(metadata):
                os.close(following)
                raise OSError("untrusted or writable core parent directory")
            os.close(descriptor)
            descriptor = following
        return descriptor
    except Exception:
        os.close(descriptor)
        raise


def _read_candidate(anchor_path: Path, relative_parts: tuple[str, ...]) -> tuple[Path, bytes]:
    if not relative_parts:
        raise OSError("missing core leaf")
    anchor = _open_absolute_directory(anchor_path)
    parent = -1
    confirmation = -1
    leaf = -1
    try:
        anchor_metadata = os.fstat(anchor)
        if not _trusted_directory(anchor_metadata):
            raise OSError("untrusted or writable core anchor directory")
        parent = _open_relative_directory(anchor, relative_parts[:-1])
        parent_metadata = os.fstat(parent)
        leaf_name = relative_parts[-1]
        leaf = os.open(leaf_name, _file_flags(), dir_fd=parent)
        pinned = os.fstat(leaf)
        if (
            not stat.S_ISREG(pinned.st_mode)
            or pinned.st_nlink != 1
            or pinned.st_uid not in {0, os.geteuid()}
            or stat.S_IMODE(pinned.st_mode) & 0o022
        ):
            raise OSError("untrusted core leaf")
        current = os.stat(leaf_name, dir_fd=parent, follow_symlinks=False)
        if _file_identity(pinned) != _file_identity(current):
            raise OSError("core leaf identity changed before read")

        _clear_nonblocking(leaf)
        chunks: list[bytes] = []
        while True:
            chunk = os.read(leaf, 1024 * 1024)
            if not chunk:
                break
            chunks.append(chunk)

        after = os.fstat(leaf)
        current = os.stat(leaf_name, dir_fd=parent, follow_symlinks=False)
        confirmation = _open_absolute_directory(anchor_path)
        confirmed_anchor = os.fstat(confirmation)
        if (
            _file_identity(pinned) != _file_identity(after)
            or _file_identity(pinned) != _file_identity(current)
            or _inode_identity(anchor_metadata) != _inode_identity(confirmed_anchor)
        ):
            raise OSError("core path identity changed while reading")
        confirmed_parent = _open_relative_directory(confirmation, relative_parts[:-1])
        try:
            if _inode_identity(parent_metadata) != _inode_identity(os.fstat(confirmed_parent)):
                raise OSError("core parent directory changed while reading")
        finally:
            os.close(confirmed_parent)

        logical = Path(os.path.abspath(os.fspath(anchor_path))).joinpath(*relative_parts)
        return logical, b"".join(chunks)
    finally:
        if leaf >= 0:
            os.close(leaf)
        if confirmation >= 0:
            os.close(confirmation)
        if parent >= 0:
            os.close(parent)
        os.close(anchor)


def _read_stable_core() -> tuple[Path, bytes]:
    wrapper = Path(os.path.abspath(_WRAPPER_FILE))
    directory = wrapper.parent
    candidates = (
        # Source-tree execution: the scripts directory itself is the trust anchor.
        (directory, ("hepta_preflight_core.py",)),
        # Installed tree: <prefix>/bin/hepta-preflight and
        # <prefix>/libexec/heptatrader/hepta-preflight-core.py.
        (
            directory.parent,
            ("libexec", "heptatrader", "hepta-preflight-core.py"),
        ),
    )
    failures: list[str] = []
    for anchor, relative in candidates:
        try:
            return _read_candidate(anchor, relative)
        except OSError as error:
            failures.append(f"{anchor.joinpath(*relative)}: {error}")
    raise RuntimeError("preflight core is unavailable: " + "; ".join(failures))


_CORE_PATH, _CORE_BYTES = _read_stable_core()
_CORE_MODULE_NAME = "_hepta_preflight_core"
_WRAPPER_MODULE = sys.modules[_WRAPPER_NAME]
sys.modules[_CORE_MODULE_NAME] = _WRAPPER_MODULE
globals()["__name__"] = _CORE_MODULE_NAME
globals()["__file__"] = str(_CORE_PATH)
try:
    exec(compile(_CORE_BYTES, str(_CORE_PATH), "exec"), globals(), globals())
finally:
    globals()["__name__"] = _WRAPPER_NAME
    globals()["__file__"] = _WRAPPER_FILE
    sys.modules.pop(_CORE_MODULE_NAME, None)


if _WRAPPER_NAME == "__main__":
    raise SystemExit(main())
