"""Local file change observation shared by source and PnL readers.

A watch is an invalidation hint, not an independent content signature.
Unsupported or lost observations require callers to revalidate their bytes.
"""
from __future__ import annotations

import ctypes
import os
from pathlib import Path
import struct
import sys


class _ChangeWatch:
    def __init__(self):
        self.fd = -1
        self._file_wd = self._parent_wd = -1
        self._name = b""
        self.last_events = []
        self._many_files = self._many_parents = None
        self._changed_paths = set()
        self._coverage_lost = False

    @property
    def available(self):
        return self.fd >= 0

    def close(self):
        fd, self.fd = self.fd, -1
        self._many_files = self._many_parents = None
        if fd >= 0:
            os.close(fd)

    def arm(self, path, *, directory=False):
        self.close()
        self._coverage_lost = False
        if sys.platform != 'linux':
            return False
        try:
            libc = ctypes.CDLL(None, use_errno=True)
            init = libc.inotify_init1
            init.argtypes, init.restype = [ctypes.c_int], ctypes.c_int
            add = libc.inotify_add_watch
            add.argtypes, add.restype = [ctypes.c_int, ctypes.c_char_p, ctypes.c_uint32], ctypes.c_int
            self.fd = init(os.O_NONBLOCK | os.O_CLOEXEC)
            if self.fd < 0:
                return False
            file_mask = 0x2 | 0x4 | 0x8 | 0x400 | 0x800 | 0x2000 | 0x02000000
            if directory:
                file_mask |= 0x40 | 0x80 | 0x100 | 0x200 | 0x01000000
            parent_mask = 0x40 | 0x80 | 0x100 | 0x200 | 0x400 | 0x800 | 0x2000 | 0x01000000
            self._file_wd = add(self.fd, os.fsencode(path), file_mask)
            self._parent_wd = add(self.fd, os.fsencode(Path(path).parent), parent_mask)
            self._name = os.fsencode(Path(path).name)
            if self._file_wd < 0 or self._parent_wd < 0:
                self.close()
                return False
            return True
        except (OSError, AttributeError):
            self.close()
            return False


    def arm_many(self, paths):
        """Watch a stable set with one fd; changed() keeps its conservative API."""
        paths = tuple(dict.fromkeys(Path(path) for path in paths))
        if not paths:
            self.close()
            return False
        if not self.arm(paths[0]):
            return False
        self._many_files = {self._file_wd: {str(paths[0])}}
        self._many_parents = {}
        try:
            libc = ctypes.CDLL(None, use_errno=True)
            add = libc.inotify_add_watch
            add.argtypes, add.restype = [ctypes.c_int, ctypes.c_char_p, ctypes.c_uint32], ctypes.c_int
            file_mask = 0x2 | 0x4 | 0x8 | 0x400 | 0x800 | 0x2000 | 0x02000000
            parent_mask = 0x40 | 0x80 | 0x100 | 0x200 | 0x2000 | 0x400 | 0x800 | 0x4 | 0x01000000
            for path in paths[1:]:
                wd = add(self.fd, os.fsencode(path), file_mask)
                if wd < 0:
                    self.close()
                    return False
                self._many_files.setdefault(wd, set()).add(str(path))
            parent_paths = {}
            for path in paths:
                parent_paths.setdefault(path.parent, {}).setdefault(
                    os.fsencode(path.name), set()).add(str(path))
            for parent, members in parent_paths.items():
                wd = add(self.fd, os.fsencode(parent), parent_mask)
                if wd < 0:
                    self.close()
                    return False
                names = self._many_parents.setdefault(wd, {})
                for name, targets in members.items():
                    names.setdefault(name, set()).update(targets)
            # The original boolean API still notices every watched event.
            # Sealed readers may use changed_paths() for a stable set instead.
            self._file_wd = self._parent_wd = -1
            self._name = b""
            return True
        except (OSError, AttributeError):
            self.close()
            return False

    def _observe_many(self, wd, mask, name):
        if self._many_files is None:
            return
        # Unmount, overflow, ignored watches and directory/file moves or
        # deletion lose continuous coverage. Never localize those events.
        if mask & (0x400 | 0x800 | 0x2000 | 0x4000 | 0x8000):
            self._coverage_lost = True
        elif wd in self._many_files:
            self._changed_paths.update(self._many_files[wd])
        elif wd in self._many_parents:
            names = self._many_parents[wd]
            if name:
                targets = names.get(name, ())
                # A watched basename may now name a different inode while an
                # open reader keeps the old inode's watch alive. A new receipt
                # must not inherit that obsolete watch: rearm the whole set.
                if targets and mask & (0x40 | 0x80 | 0x100 | 0x200):
                    self._coverage_lost = True
                else:
                    self._changed_paths.update(targets)
            elif mask & 0x4:
                for paths in names.values():
                    self._changed_paths.update(paths)
            else:
                self._coverage_lost = True
        else:
            self._coverage_lost = True

    def changed_paths(self):
        """Affected set members, or None when continuous coverage is uncertain.

        This does not prove content. Callers must still bind original SHA
        receipts to current identities and enforce their revalidation deadline.
        """
        if self._many_files is None:
            return None
        self.changed()
        if not self.available or self._coverage_lost:
            return None
        return frozenset(self._changed_paths)

    def changed(self):
        self.last_events = []
        self._changed_paths = set()
        if self.fd < 0:
            return True
        found = False
        try:
            for _ in range(16):
                try:
                    data = os.read(self.fd, 65536)
                except BlockingIOError:
                    return found
                if not data:
                    self.close()
                    return True
                offset = 0
                while offset < len(data):
                    if len(data) - offset < 16:
                        self.close()
                        return True
                    wd, mask, _cookie, length = struct.unpack_from("iIII", data, offset)
                    end = offset + 16 + length
                    if end > len(data):
                        self.close()
                        return True
                    name = data[offset + 16:end].split(b"\0", 1)[0]
                    self._observe_many(wd, mask, name)
                    if (wd != self._parent_wd or not name or name == self._name
                            or mask & (0x400 | 0x800 | 0x2000 | 0x4000 | 0x8000)):
                        found = True
                        self.last_events.append((wd == self._file_wd, mask, _cookie, name))
                    offset = end
            self.close()
            return True
        except OSError:
            self.close()
            return True
