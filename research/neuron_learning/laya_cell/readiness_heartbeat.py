"""Conservative stale-source HOLD heartbeats; never a cached READY authority.

A growing FAILURE tail is only a rescan hint. Source bytes, quotes and probe
labels become current only after the existing complete assessor succeeds.
"""
from __future__ import annotations

import copy
import os
from pathlib import Path
import stat
import time

from .integrity import ContractError, digest, sha_file
from .shadow_source import strict

TAIL_BYTES = 65536
MAX_HINT_BYTES = 1024 * 1024


def _version(path):
    st = path.lstat()
    if (not stat.S_ISREG(st.st_mode) or st.st_uid != os.geteuid()
            or st.st_mode & 0o077 or st.st_nlink != 1):
        raise ContractError("READINESS_HEARTBEAT_SOURCE_IDENTITY")
    return (st.st_dev, st.st_ino, st.st_size, st.st_mtime_ns,
            st.st_ctime_ns, st.st_mode, st.st_uid, st.st_nlink)


class StaleReadinessHeartbeat:
    def __init__(self, revalidate_seconds=300):
        if type(revalidate_seconds) is not int or not 30 <= revalidate_seconds <= 3600:
            raise ContractError("READINESS_HEARTBEAT_INTERVAL")
        self.revalidate_ms = revalidate_seconds * 1000
        self._status = None
        self._signature = None
        self._versions = {}
        self._quote_paths = set()
        self._last_now = None
        self._full_at = None
        self._pending_revalidation = False
        self.full_assessments = 0
        self.hold_heartbeats = 0

    def _inventory(self, config):
        root = Path(config["feed_directory"])
        if not root.is_absolute() or root.is_symlink() or root.resolve() != root:
            raise ContractError("SOURCE_FEED_DIRECTORY_IDENTITY")
        quotes = set(root.glob("segment-*/quotes.jsonl"))
        extra = [Path(p) for p in config.get("completed_history_sources", [])]
        if len(extra) > 128 or len(set(extra)) != len(extra) or quotes.intersection(extra):
            raise ContractError("READINESS_HEARTBEAT_SOURCE_POPULATION")
        quotes.update(extra)
        archive = root / "source-archive-v1"
        if archive.is_symlink():
            raise ContractError("SOURCE_ARCHIVE_SYMLINK")
        archived = set(archive.glob("page-*.json"))
        ledger = archive / "ledger.jsonl"
        if ledger.exists() or ledger.is_symlink():
            archived.add(ledger)
        if len(quotes) + len(archived) > 100000:
            raise ContractError("READINESS_HEARTBEAT_SOURCE_BOUND")
        return {p: _version(p) for p in quotes | archived}, quotes

    def _new_quote_hint(self, path, version, latest, previous_size):
        # This bounded appended range is NEVER source evidence. A suspicious
        # or positive hint invokes the full assessor before READY is possible.
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC)
        try:
            st = os.fstat(fd)
            if (st.st_dev, st.st_ino, st.st_size, st.st_mtime_ns,
                    st.st_ctime_ns, st.st_mode, st.st_uid, st.st_nlink) != version:
                return True
            start = max(0, previous_size - TAIL_BYTES)
            length = st.st_size - start
            if length > MAX_HINT_BYTES:
                return True
            os.lseek(fd, start, os.SEEK_SET)
            pieces = []
            remaining = length
            while remaining:
                piece = os.read(fd, min(remaining, TAIL_BYTES))
                if not piece:
                    return True
                pieces.append(piece)
                remaining -= len(piece)
            raw = b"".join(pieces)
        finally:
            os.close(fd)
        if _version(path) != version:
            return True
        lines = raw.splitlines(keepends=True)
        if start and lines:
            lines = lines[1:]
        for raw_line in lines:
            if not raw_line.endswith(b"\n"):
                continue
            try:
                event = strict(raw_line)
            except (ValueError, TypeError, UnicodeError, ContractError):
                return True
            if not isinstance(event, dict):
                return True
            if event.get("kind") == "QUOTE":
                data = event.get("data")
                value = data.get("observed_at_ms") if isinstance(data, dict) else None
                if type(value) is not int or latest is None or value > latest:
                    return True
        return False

    def assess(self, config, assessor, now=None):
        now = int(time.time() * 1000) if now is None else now
        if type(now) is not int or now <= 0:
            raise ContractError("READINESS_HEARTBEAT_CLOCK")
        if self._last_now is not None and now < self._last_now:
            self._status = None
            self._versions = {}
            self._last_now = now
            raise ContractError("READINESS_HEARTBEAT_CLOCK_REGRESSED")
        self._last_now = now
        signature = digest(config)
        if self._signature is not None and signature != self._signature:
            self._status = None
            raise ContractError("READINESS_HEARTBEAT_CONFIG_CHANGED")
        self._signature = signature
        if sha_file(Path(config["period_path"])) != config["period_sha256"]:
            self._status = None
            raise ContractError("RESIDENT_PERIOD_CHANGED")
        versions, quotes = self._inventory(config)
        old = self._status
        latest = old.get("latest_source_quote_ms") if old else None
        stale = latest is None or (type(latest) is int and now - latest > 5000)
        reuse = bool(old and old.get("state") == "HOLD_NEW_FUSION_WINDOW"
                     and stale and self._full_at is not None
                     and 0 <= now - self._full_at < self.revalidate_ms
                     and versions.keys() == self._versions.keys())
        if reuse:
            for path, current in versions.items():
                previous = self._versions[path]
                if current == previous:
                    continue
                if (path not in quotes or current[:2] != previous[:2]
                        or current[2] <= previous[2]
                        or current[5:] != previous[5:]
                        or self._new_quote_hint(path, current, latest, previous[2])):
                    reuse = False
                    break
        if reuse:
            result = copy.deepcopy(old)
            result.update(at_ms=now, latest_quote_age_ms=(now-latest if latest is not None else None),
                          source_asof_ms=self._full_at,
                          source_verified_at_ms=self._full_at,
                          probe_results_at_ms=old["at_ms"],
                          assessment_kind="STALE_HOLD_HEARTBEAT",
                          source_revalidation_pending=(self._pending_revalidation or versions != self._versions),
                          cached_result_may_grant_ready=False)
            result["state"] = "HOLD_NEW_FUSION_WINDOW"
            reasons = list(result.get("reasons", []))
            if "LATEST_AUTHORITATIVE_QUOTE_NOT_FRESH" not in reasons:
                reasons.append("LATEST_AUTHORITATIVE_QUOTE_NOT_FRESH")
            result["reasons"] = reasons
            self._pending_revalidation = result["source_revalidation_pending"]
            self._versions = versions
            self.hold_heartbeats += 1
            return result
        self._status = None
        result = assessor(config, now)
        after, after_quotes = self._inventory(config)
        if after.keys() != versions.keys() or after_quotes != quotes:
            raise ContractError("READINESS_HEARTBEAT_SOURCE_CHANGED_DURING_ASSESSMENT")
        for path, current in after.items():
            before = versions[path]
            if current != before and (path not in quotes
                    or current[:2] != before[:2] or current[2] <= before[2]
                    or current[5:] != before[5:]):
                raise ContractError("READINESS_HEARTBEAT_SOURCE_CHANGED_DURING_ASSESSMENT")
        self._full_at = result.get("at_ms", now)
        self._pending_revalidation = after != versions
        result.update(source_asof_ms=self._full_at,
                      source_verified_at_ms=self._full_at,
                      probe_results_at_ms=self._full_at,
                      assessment_kind="FULL_SOURCE_ASSESSMENT",
                      source_revalidation_pending=self._pending_revalidation,
                      cached_result_may_grant_ready=False)
        self._status = copy.deepcopy(result)
        # Any append during the verified prefix read is checked next cycle.
        self._versions, self._quote_paths = versions, quotes
        self.full_assessments += 1
        return result
