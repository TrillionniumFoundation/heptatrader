"""Bounded, process-local SHA256 reuse for unchanged regular files.

The cache never persists trust across a process restart.  Inode identity and
nanosecond modification *and change* times are checked on every lookup, so an
atomic replacement and a same-size rewrite with restored mtime both invalidate
the entry.  A file that changes while it is read is rejected, not cached.
"""

from __future__ import annotations

from collections import OrderedDict
from dataclasses import dataclass
import hashlib
import os
from pathlib import Path
import stat
import threading

from .integrity import ContractError, _regular_stream


@dataclass(frozen=True)
class FileIdentity:
    device: int
    inode: int
    size: int
    mtime_ns: int
    ctime_ns: int
    mode: int
    uid: int
    gid: int

    @classmethod
    def from_stat(cls, value: os.stat_result) -> "FileIdentity":
        if not stat.S_ISREG(value.st_mode):
            raise ContractError("HASH_FILE_TYPE")
        return cls(value.st_dev, value.st_ino, value.st_size,
                   value.st_mtime_ns, value.st_ctime_ns, value.st_mode,
                   value.st_uid, value.st_gid)


class FileDigestCache:
    """Reuse digests only while the currently named file retains its identity.

    Like a direct hash, a result describes the file at verification time; it is
    not a lock against a subsequent writer.  Callers must retain their existing
    immutable-release/publication discipline.
    """

    def __init__(self, max_entries: int = 4096) -> None:
        if max_entries < 1:
            raise ValueError("max_entries must be positive")
        self.max_entries = max_entries
        self._entries: OrderedDict[str, tuple[FileIdentity, str]] = OrderedDict()
        self._lock = threading.RLock()
        self._hits = 0
        self._misses = 0
        self._bytes_read = 0

    def sha256(self, path: str | os.PathLike[str]) -> str:
        # Do not resolve symlinks into a separate cache key: replacing the
        # caller's path must cause its present target to be checked each time.
        key = os.path.abspath(os.fspath(path))
        with self._lock:
            try:
                identity = FileIdentity.from_stat(os.stat(key))
            except (OSError, ValueError):
                self._entries.pop(key, None)
                raise
            previous = self._entries.get(key)
            if previous is not None and previous[0] == identity:
                self._hits += 1
                self._entries.move_to_end(key)
                return previous[1]
            self._entries.pop(key, None)
            self._misses += 1
            digest = hashlib.sha256()
            with _regular_stream(Path(key), "HASH_FILE_TYPE") as (handle, metadata):
                opened = FileIdentity.from_stat(metadata)
                if opened != identity:
                    raise ContractError(f"HASH_FILE_CHANGED_BEFORE_READ:{key}")
                while chunk := handle.read(1024 * 1024):
                    self._bytes_read += len(chunk)
                    digest.update(chunk)
                finished = FileIdentity.from_stat(os.fstat(handle.fileno()))
            named = FileIdentity.from_stat(os.stat(key))
            if finished != opened or named != opened:
                raise ContractError(f"HASH_FILE_CHANGED_DURING_READ:{key}")
            value = digest.hexdigest()
            self._entries[key] = (opened, value)
            self._entries.move_to_end(key)
            while len(self._entries) > self.max_entries:
                self._entries.popitem(last=False)
            return value

    def clear(self) -> None:
        with self._lock:
            self._entries.clear()

    def stats(self) -> dict[str, int]:
        with self._lock:
            return {"hits": self._hits, "misses": self._misses,
                    "bytes_read": self._bytes_read,
                    "entries": len(self._entries)}
