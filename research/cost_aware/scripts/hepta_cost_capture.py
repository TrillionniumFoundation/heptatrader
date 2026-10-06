"""Private append-only candidate/quote evidence; no trading API.

Failed recording prevents NEW entries in the adapter, never a safety close.
Files are capped, never pruned. Hash chaining is integrity evidence, not vendor
source authentication or cryptographic trading authority.
"""
from __future__ import annotations
from pathlib import Path
import fcntl
import hashlib
import json
import os
import stat
import time
import threading
from hepta_cost_policy import CostError, canonical, strict_loads, POLICY_VERSION

SCHEMA = "heptatrader.cost-capture.v1"
MAX_FILE_BYTES = 64 * 1024 * 1024
EVENTS = {"HEADER", "QUOTE", "CANDIDATE", "ENTRY_CHECK", "EXIT_CHECK", "SETTLEMENT", "MODEL_FAILURE", "SAMPLING_FAILURE", "MODEL_SLOT_SKIPPED"}
MAX_RECORD_BYTES = 128 * 1024
QUOTE_FIELDS = ("source", "authoritative", "instrument", "subscription_id", "subscription_state",
                "observed_at_ms", "stale_after_ms", "stale", "bid", "ask")


class ResetHold(CostError):
    """Explicit operator stop: observing or enabling a new driver is forbidden."""


def require_no_reset_hold(home=None):
    # Presence is sufficient, even for an unreadable or dangling marker. Reading
    # its text is unnecessary and may expose account details. Never remove it.
    root = Path.home() if home is None else Path(home)
    marker = root / ".local/state/heptatrader/RESET-STOPPED.json"
    try:
        marker.lstat()
    except FileNotFoundError:
        return
    except OSError as exc:
        raise ResetHold("COST_RESET_HOLD_UNREADABLE") from exc
    raise ResetHold("COST_RESET_HOLD_ACTIVE")


def private_directory(path):
    path = Path(path)
    path.mkdir(parents=True, exist_ok=True, mode=0o700)
    info = path.lstat()
    if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.geteuid() or info.st_mode & 0o077:
        raise CostError("CAPTURE_DIRECTORY_UNSAFE")


def read_capture(path):
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC)
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode) or info.st_size > MAX_FILE_BYTES:
            raise CostError("CAPTURE_INPUT_BOUND_OR_TYPE")
        chunks, remaining = [], info.st_size
        while remaining:
            block = os.read(fd, min(remaining, 1024 * 1024))
            if not block:
                raise CostError("CAPTURE_SHORT_READ")
            chunks.append(block)
            remaining -= len(block)
        raw = b"".join(chunks)
        current = os.fstat(fd)
        if (current.st_ino, current.st_size, current.st_mtime_ns) != (info.st_ino, info.st_size, info.st_mtime_ns):
            raise CostError("CAPTURE_CHANGED_DURING_READ_COPY_A_PREFIX_FIRST")
    finally:
        os.close(fd)
    if not raw or not raw.endswith(b"\n"):
        raise CostError("CAPTURE_TRUNCATED_TAIL")
    previous, events, last_time = "0" * 64, [], 0
    for sequence, line in enumerate(raw.splitlines()):
        if len(line) > MAX_RECORD_BYTES:
            raise CostError("CAPTURE_RECORD_TOO_LARGE")
        value = strict_loads(line)
        if not isinstance(value, dict) or set(value) != {"schema", "sequence", "previous", "at_ms", "event", "data", "sha256"}:
            raise CostError("CAPTURE_SCHEMA_INVALID")
        body = {key: val for key, val in value.items() if key != "sha256"}
        if (body["schema"] != SCHEMA or type(body["sequence"]) is not int or body["sequence"] != sequence or
                body["previous"] != previous or hashlib.sha256(canonical(body).encode()).hexdigest() != value["sha256"]):
            raise CostError("CAPTURE_CHAIN_INVALID")
        if type(body["at_ms"]) is not int or body["at_ms"] <= 0 or body["at_ms"] < last_time:
            raise CostError("CAPTURE_CLOCK_REGRESSION")
        if not isinstance(body["event"], str) or body["event"] not in EVENTS or not isinstance(body["data"], dict):
            raise CostError("CAPTURE_EVENT_SCHEMA_INVALID")
        if sequence == 0 and (body["event"] != "HEADER" or body["data"].get("policy_version") != POLICY_VERSION or
                              body["data"].get("source_kind") not in ("IB_CAPTURE", "SYNTHETIC") or
                              body["data"].get("instrument") not in ("BTC.USD", "EUR.USD") or
                              body["data"].get("position_neutral_forecasts_required") is not True):
            raise CostError("CAPTURE_HEADER_REQUIRED")
        if sequence and body["event"] == "HEADER":
            raise CostError("CAPTURE_DUPLICATE_HEADER")
        previous, last_time = value["sha256"], body["at_ms"]
        events.append(value)
    return {"events": events, "input_sha256": hashlib.sha256(raw).hexdigest(), "bytes": len(raw),
            "source_kind": events[0]["data"].get("source_kind"), "complete_prefix": True,
            "live_execution_proven": False}


class Capture:
    def __init__(self, path, instrument, *, source_kind="IB_CAPTURE", min_free_bytes=4 * 1024 ** 3):
        if source_kind not in ("IB_CAPTURE", "SYNTHETIC") or instrument not in ("BTC.USD", "EUR.USD"):
            raise CostError("CAPTURE_SOURCE_KIND_INVALID")
        self._thread_lock = threading.RLock()
        self.path = Path(path)
        private_directory(self.path.parent)
        self.fd = os.open(self.path, os.O_RDWR | os.O_APPEND | os.O_CREAT | os.O_NOFOLLOW | os.O_CLOEXEC, 0o600)
        self.sequence, self.previous, self.last_time = 0, "0" * 64, 0
        self.min_free_bytes, self.instrument, self.source_kind = min_free_bytes, instrument, source_kind
        self.poisoned = False
        try:
            info = os.fstat(self.fd)
            if not stat.S_ISREG(info.st_mode) or info.st_uid != os.geteuid() or info.st_nlink != 1 or info.st_mode & 0o077:
                raise CostError("CAPTURE_FILE_UNSAFE")
            fcntl.flock(self.fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            if info.st_size:
                old = read_capture(self.path)
                header = old["events"][0]["data"]
                if header.get("instrument") != instrument or header.get("source_kind") != source_kind:
                    raise CostError("CAPTURE_SCOPE_CHANGED")
                end = old["events"][-1]
                self.sequence, self.previous, self.last_time = end["sequence"] + 1, end["sha256"], end["at_ms"]
            else:
                self.append("HEADER", {"instrument": instrument, "source_kind": source_kind,
                            "policy_version": POLICY_VERSION, "position_neutral_forecasts_required": True}, at_ms=1)
                parent = os.open(self.path.parent, os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC)
                try:
                    os.fsync(parent)
                finally:
                    os.close(parent)
        except BaseException:
            self.close()
            raise

    def append(self, event, data, *, at_ms=None):
        with self._thread_lock:
            return self._append_locked(event, data, at_ms=at_ms)

    def _append_locked(self, event, data, *, at_ms=None):
        if self.fd is None or self.poisoned:
            raise CostError("CAPTURE_POISONED")
        if not isinstance(event, str) or event not in EVENTS or not isinstance(data, dict):
            raise CostError("CAPTURE_EVENT_SCHEMA_INVALID")
        if (self.sequence == 0) != (event == "HEADER"):
            raise CostError("CAPTURE_HEADER_ORDER_INVALID")
        at_ms = int(time.time() * 1000) if at_ms is None else at_ms
        if type(at_ms) is not int or at_ms <= 0 or at_ms < self.last_time:
            raise CostError("CAPTURE_CLOCK_REGRESSION")
        info, path_info = os.fstat(self.fd), self.path.lstat()
        if (info.st_ino, info.st_dev) != (path_info.st_ino, path_info.st_dev) or not stat.S_ISREG(path_info.st_mode):
            self.poisoned = True
            raise CostError("CAPTURE_PATH_REPLACED")
        if info.st_uid != os.geteuid() or info.st_nlink != 1 or info.st_mode & 0o077:
            self.poisoned = True
            raise CostError("CAPTURE_FILE_PERMISSIONS_CHANGED")
        body = {"schema": SCHEMA, "sequence": self.sequence, "previous": self.previous,
                "at_ms": at_ms, "event": event, "data": data}
        checksum = hashlib.sha256(canonical(body).encode()).hexdigest()
        raw = (canonical({**body, "sha256": checksum}) + "\n").encode()
        volume = os.statvfs(self.path.parent)
        if len(raw) > MAX_RECORD_BYTES or info.st_size + len(raw) > MAX_FILE_BYTES:
            raise CostError("CAPTURE_STORAGE_BOUND")
        if volume.f_bavail * volume.f_frsize < self.min_free_bytes + len(raw):
            raise CostError("CAPTURE_DISK_RESERVE")
        try:
            offset = 0
            while offset < len(raw):
                written = os.write(self.fd, raw[offset:])
                if written <= 0:
                    raise OSError("capture short write")
                offset += written
            os.fsync(self.fd)
        except BaseException:
            self.poisoned = True
            raise
        self.sequence += 1
        self.previous, self.last_time = checksum, at_ms
        return checksum

    def close(self):
        if getattr(self, "fd", None) is not None:
            os.close(self.fd)
            self.fd = None

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()
