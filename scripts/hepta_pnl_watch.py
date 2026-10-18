"""Bounded in-process byte reuse for the read-only local OMS observer.

Any write event, inode/ctime change or lost watch requires complete source
bytes again. Unsupported systems always reread.
"""
from __future__ import annotations

import ctypes
import os
from pathlib import Path
import stat
import struct
import sys

from hepta_pnl_accounting import EvidenceError


class _ChangeWatch:
    def __init__(self):
        self.fd = -1
        self._file_wd = self._parent_wd = -1
        self._name = b""

    @property
    def available(self):
        return self.fd >= 0

    def close(self):
        fd, self.fd = self.fd, -1
        if fd >= 0:
            os.close(fd)

    def arm(self, path):
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

    def changed(self):
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
                    offset = end
            self.close()
            return True
        except OSError:
            self.close()
            return True


def _version(path):
    st = Path(path).lstat()
    if (not stat.S_ISREG(st.st_mode) or st.st_uid != os.geteuid()
            or st.st_nlink != 1 or st.st_mode & 0o077):
        raise EvidenceError('UNSAFE_OR_OVERSIZE_INPUT:' + Path(path).name)
    return (st.st_dev, st.st_ino, st.st_size, st.st_mtime_ns,
            st.st_ctime_ns, st.st_mode, st.st_uid, st.st_nlink)


class StableJournalBytes:
    def __init__(self, read_private, clock, *, reverify_ms=60000):
        self._read_private, self._clock = read_private, clock
        self._watch = _ChangeWatch()
        self._path = None
        self._version = None
        self._raw = None
        self._fingerprint = None
        self._read_at = None
        self._last_clock = None
        self.reverify_ms = reverify_ms
        self.hits = 0
        self.full_reads = 0
        self.full_bytes_read = 0

    def close(self):
        self._watch.close()
        self._version = None

    def read(self, path, limit):
        path = Path(path)
        now = self._clock()
        before = _version(path)
        if before[2] > limit:
            raise EvidenceError('UNSAFE_OR_OVERSIZE_INPUT:' + path.name)
        regressed = self._last_clock is not None and now < self._last_clock
        self._last_clock = now
        changed = self._watch.changed()
        if (not regressed and self._path == path and before == self._version
                and self._watch.available and not changed
                and self._read_at is not None and 0 <= now-self._read_at < self.reverify_ms):
            self.hits += 1
            return self._raw, self._fingerprint
        self._version = None
        self._watch.arm(path)
        before = _version(path)
        raw, fingerprint = self._read_private(path, limit)
        after = _version(path)
        during = self._watch.changed() if self._watch.available else False
        if before != after or fingerprint != before[:4] or during:
            self._watch.close()
            raise EvidenceError('PNL_JOURNAL_CHANGED_DURING_READ')
        self._path, self._version = path, after
        self._raw, self._fingerprint = raw, fingerprint
        self._read_at = now
        self.full_reads += 1
        self.full_bytes_read += len(raw)
        return raw, fingerprint

    def verify(self, path):
        if (self._path != Path(path) or self._version != _version(path)
                or (self._watch.available and self._watch.changed())):
            self._version = None
            self._watch.close()
            raise EvidenceError('PNL_COLLECTION_LEDGER_OR_PERIOD_CHANGED')
