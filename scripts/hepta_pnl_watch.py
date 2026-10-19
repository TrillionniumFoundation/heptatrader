"""Bounded in-process byte reuse for the read-only local OMS observer.

Any write event, inode/ctime change or lost watch requires complete source
bytes again. Unsupported systems always reread.
"""
from __future__ import annotations

import os
from pathlib import Path
import stat

from hepta_pnl_accounting import EvidenceError


# Keep the existing private import stable for PnL consumers.
from hepta_change_watch import _ChangeWatch


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
