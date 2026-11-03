"""Regular-file accounting with Linux change notifications and safe rescans.

The accounting has the same non-atomic semantics as a directory walk.  Every
directory is watched *before* it is scanned.  File events update its current
size; topology changes, event loss, and watch failures invalidate the snapshot.
No TTL or root-directory mtime is used to assume that child files are unchanged.
"""

from __future__ import annotations

import ctypes
import errno
import os
from pathlib import Path
import stat
import struct
import sys

from .integrity import ContractError


_EVENT = struct.Struct("iIII")
_MODIFY = 0x00000002
_ATTRIB = 0x00000004
_CLOSE_WRITE = 0x00000008
_MOVED_FROM = 0x00000040
_MOVED_TO = 0x00000080
_CREATE = 0x00000100
_DELETE = 0x00000200
_DELETE_SELF = 0x00000400
_MOVE_SELF = 0x00000800
_UNMOUNT = 0x00002000
_OVERFLOW = 0x00004000
_IGNORED = 0x00008000
_ISDIR = 0x40000000
_DONT_FOLLOW = 0x02000000
_MASK = (_MODIFY | _ATTRIB | _CLOSE_WRITE | _MOVED_FROM | _MOVED_TO |
         _CREATE | _DELETE | _DELETE_SELF | _MOVE_SELF | _UNMOUNT)


class TreeUsageCache:
    def __init__(self, root: str | os.PathLike[str], max_watches: int = 65536):
        self.root = Path(os.path.abspath(os.fspath(root)))
        self.max_watches = max_watches
        self._fd = -1
        self._libc = None
        self._watches: dict[int, Path] = {}
        self._file_watches: dict[int, Path] = {}
        self._files: dict[Path, int] = {}
        self._root_identity: tuple[int, int] | None = None
        self._valid = False
        self._total = 0
        self.full_scans = 0
        self.incremental_stats = 0
        self.cache_hits = 0
        self.watch_failures = 0
        self.hardlink_fallbacks = 0
        if sys.platform == "linux":
            self._libc = ctypes.CDLL(None, use_errno=True)
            self._libc.inotify_init1.argtypes = [ctypes.c_int]
            self._libc.inotify_init1.restype = ctypes.c_int
            self._libc.inotify_add_watch.argtypes = [ctypes.c_int, ctypes.c_char_p, ctypes.c_uint32]
            self._libc.inotify_add_watch.restype = ctypes.c_int

    def close(self) -> None:
        if self._fd >= 0:
            os.close(self._fd)
        self._fd = -1
        self._watches.clear()
        self._file_watches.clear()
        self._valid = False

    def __del__(self):
        try:
            self.close()
        except OSError:
            pass

    def _identity(self) -> tuple[int, int] | None:
        try:
            value = self.root.stat()
        except FileNotFoundError:
            return None
        if not stat.S_ISDIR(value.st_mode):
            raise NotADirectoryError(self.root)
        return value.st_dev, value.st_ino

    def _watch(self, directory: Path) -> None:
        if self._fd < 0:
            return
        if len(self._watches) + len(self._file_watches) >= self.max_watches:
            raise OSError(errno.ENOSPC, "Tree usage watch bound reached")
        wd = self._libc.inotify_add_watch(self._fd, os.fsencode(directory), _MASK)
        if wd < 0:
            code = ctypes.get_errno()
            raise OSError(code, os.strerror(code), directory)
        self._watches[wd] = directory

    def _watch_file(self, path: Path) -> None:
        if self._fd < 0:
            return
        if len(self._watches) + len(self._file_watches) >= self.max_watches:
            raise OSError(errno.ENOSPC, "Tree usage watch bound reached")
        # Directory watches cannot see an outside hardlink's write or creation.
        # An inode ATTRIB watch detects new link-count changes. Multiply linked
        # files then use the original full-walk policy.
        wd = self._libc.inotify_add_watch(
            self._fd, os.fsencode(path), _ATTRIB | _DELETE_SELF | _MOVE_SELF | _UNMOUNT | _DONT_FOLLOW)
        if wd < 0:
            code = ctypes.get_errno()
            raise OSError(code, os.strerror(code), path)
        old = self._file_watches.get(wd)
        if old is not None and old != path:
            try:
                previous = old.lstat()
                current = path.lstat()
            except FileNotFoundError:
                pass  # The inode watch follows a regular-file rename.
            else:
                if (previous.st_dev, previous.st_ino) == (current.st_dev, current.st_ino):
                    raise OSError(errno.EMLINK, "Concurrent hardlink topology change")
        self._file_watches[wd] = path

    def _regular_size(self, path: Path, value: os.stat_result) -> int | None:
        if value.st_nlink != 1:
            if self._fd >= 0:
                self.hardlink_fallbacks += 1
                self.close()
            return value.st_size
        self._watch_file(path)
        # Watch before observing the authoritative size/link count, so an
        # outside link created during watch setup cannot be lost.
        try:
            value = path.lstat()
        except FileNotFoundError:
            return None
        if stat.S_ISLNK(value.st_mode):
            self.close()
            raise ContractError('STATE_TREE_SYMLINK')
        if not stat.S_ISREG(value.st_mode):
            return None
        if value.st_nlink != 1 and self._fd >= 0:
            self.hardlink_fallbacks += 1
            self.close()
        return value.st_size

    def _scan(self, watching: bool = True) -> None:
        self.close()
        if watching and self._libc is not None:
            self._fd = self._libc.inotify_init1(os.O_NONBLOCK | os.O_CLOEXEC)
            if self._fd < 0:
                self.watch_failures += 1
        self.full_scans += 1
        identity = self._identity()
        files: dict[Path, int] = {}
        pending = [self.root] if identity is not None else []
        try:
            while pending:
                directory = pending.pop()
                self._watch(directory)
                try:
                    with os.scandir(directory) as entries:
                        for entry in entries:
                            try:
                                value = entry.stat(follow_symlinks=False)
                            except FileNotFoundError:
                                continue
                            path = Path(entry.path)
                            if stat.S_ISLNK(value.st_mode):
                                raise ContractError('STATE_TREE_SYMLINK')
                            if stat.S_ISDIR(value.st_mode):
                                pending.append(path)
                            elif stat.S_ISREG(value.st_mode):
                                size = self._regular_size(path, value)
                                if size is not None:
                                    files[path] = size
                except FileNotFoundError:
                    # A parent watch records deletion during the walk.
                    continue
        except ContractError:
            self.close()
            raise
        except OSError:
            self.close()
            if watching:
                self.watch_failures += 1
                self._scan(watching=False)
                return
            raise
        self._files = files
        self._total = sum(files.values())
        self._root_identity = identity
        self._valid = self._fd >= 0 and identity is not None

    def _changes(self) -> set[Path] | None:
        dirty: set[Path] = set()
        total_read = 0
        while True:
            try:
                data = os.read(self._fd, 65536)
            except BlockingIOError:
                return dirty
            except OSError:
                return None
            if not data:
                return None
            total_read += len(data)
            if total_read > 4 * 1024 * 1024:
                return None
            offset = 0
            while offset < len(data):
                if len(data) - offset < _EVENT.size:
                    return None
                wd, mask, _cookie, length = _EVENT.unpack_from(data, offset)
                offset += _EVENT.size
                if offset + length > len(data):
                    return None
                name = os.fsdecode(data[offset:offset + length].split(b"\0", 1)[0])
                offset += length
                if mask & (_OVERFLOW | _UNMOUNT):
                    return None
                file = self._file_watches.get(wd)
                if file is not None:
                    dirty.add(file)
                    if mask & _IGNORED:
                        del self._file_watches[wd]
                    continue
                if mask & (_IGNORED | _DELETE_SELF | _MOVE_SELF | _ISDIR):
                    return None
                directory = self._watches.get(wd)
                if directory is None or not name:
                    return None
                dirty.add(directory / name)

    def usage(self) -> tuple[int, int]:
        if not self._valid or self._identity() != self._root_identity:
            self._scan()
        for _ in range(2):
            if not self._valid:
                return self._total, len(self._files)
            dirty = self._changes()
            if dirty is None or self._identity() != self._root_identity:
                self._scan()
                continue
            if not dirty:
                self.cache_hits += 1
            for path in dirty:
                self.incremental_stats += 1
                try:
                    value = path.lstat()
                except FileNotFoundError:
                    value = None
                old = self._files.pop(path, 0)
                self._total -= old
                if value is not None and stat.S_ISLNK(value.st_mode):
                    self.close()
                    raise ContractError('STATE_TREE_SYMLINK')
                if value is not None and stat.S_ISREG(value.st_mode):
                    try:
                        size = self._regular_size(path, value)
                    except OSError:
                        self.watch_failures += 1
                        self._scan(watching=False)
                        return self._total, len(self._files)
                    if not self._valid:
                        self._scan(watching=False)
                        return self._total, len(self._files)
                    if size is not None:
                        self._files[path] = size
                        self._total += size
                elif value is not None and stat.S_ISDIR(value.st_mode):
                    self._scan()
                    break
            else:
                return self._total, len(self._files)
        # A continuously changing topology cannot safely reuse the watcher
        # snapshot. Preserve ordinary full-scan semantics for this observation.
        self._scan(watching=False)
        return self._total, len(self._files)

    def stats(self) -> dict[str, int | bool]:
        return {"full_scans": self.full_scans,
                "incremental_stats": self.incremental_stats,
                "cache_hits": self.cache_hits,
                "watch_failures": self.watch_failures,
                "hardlink_fallbacks": self.hardlink_fallbacks,
                "watch_budget": self.max_watches,
                "watching": self._valid,
                "watches": len(self._watches) + len(self._file_watches),
                "directory_watches": len(self._watches),
                "file_watches": len(self._file_watches)}
