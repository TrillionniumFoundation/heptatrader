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

    @property
    def available(self):
        return self.fd >= 0

    def close(self):
        fd, self.fd = self.fd, -1
        if fd >= 0:
            os.close(fd)

    def arm(self, path, *, directory=False):
        self.close()
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
        """Watch a stable set of originals with one fd; any event invalidates it."""
        paths = tuple(dict.fromkeys(Path(path) for path in paths))
        if not paths:
            self.close()
            return False
        if not self.arm(paths[0]):
            return False
        try:
            libc = ctypes.CDLL(None, use_errno=True)
            add = libc.inotify_add_watch
            add.argtypes, add.restype = [ctypes.c_int, ctypes.c_char_p, ctypes.c_uint32], ctypes.c_int
            file_mask = 0x2 | 0x4 | 0x8 | 0x400 | 0x800 | 0x2000 | 0x02000000
            parent_mask = 0x40 | 0x80 | 0x100 | 0x200 | 0x2000 | 0x400 | 0x800 | 0x4 | 0x01000000
            for path in paths[1:]:
                if add(self.fd, os.fsencode(path), file_mask) < 0:
                    self.close()
                    return False
            for parent in dict.fromkeys(path.parent for path in paths):
                if add(self.fd, os.fsencode(parent), parent_mask) < 0:
                    self.close()
                    return False
            # In a set every watched event matters, including lost watches and
            # overflow. The single-file path keeps its existing name filter.
            self._file_wd = self._parent_wd = -1
            self._name = b""
            return True
        except (OSError, AttributeError):
            self.close()
            return False

    def changed(self):
        self.last_events = []
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
