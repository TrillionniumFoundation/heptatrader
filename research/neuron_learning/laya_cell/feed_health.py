"""Live collector diagnostics; original quote chains remain the data authority."""
from __future__ import annotations
from datetime import datetime, timezone
import json
import hashlib
import os
from pathlib import Path
import time
from .market_session import session_clock

SCHEMA = "heptatrader.laya-feed-data-health.v1"
UPDATE_SECONDS = 5
STALE_PROGRESS_MS = 15000


def atomic_status(path, value):
    path = Path(path)
    tmp = path.with_name(path.name + ".next")
    raw = json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)
    with tmp.open("w") as out:
        out.write(raw + "\n")
        out.flush()
        os.fsync(out.fileno())
    tmp.chmod(0o600)
    os.replace(tmp, path)



class QuoteProbeBackoff:
    """Bound failed reads while keeping calendar and source authority separate."""
    def __init__(self):
        self.last_reason = None
        self.same_failure_count = 0
        self.calendar_open = None
        self.delay_seconds = 1.0
        self.last_probe_at_ms = None
        self.next_probe_at_ms = None
        self.interval_seconds = 1.0

    def observe(self, kind, data, at_ms, elapsed_seconds=0.0):
        if kind not in ("QUOTE", "FAILURE"):
            return
        calendar = session_clock(datetime.fromtimestamp(at_ms / 1000, timezone.utc))
        is_open = calendar["calendar_regular_session_open"]
        reason = data.get("reason", "UNSPECIFIED_CAPTURE_FAILURE") if kind == "FAILURE" else None
        if reason is None:
            self.same_failure_count = 0
            interval = 1.0
        else:
            self.same_failure_count = (self.same_failure_count + 1
                if reason == self.last_reason and is_open == self.calendar_open else 1)
            interval = float(min(5 if is_open else 30, 2 ** min(self.same_failure_count - 1, 5)))
        self.last_reason = reason
        self.calendar_open = is_open
        self.interval_seconds = interval
        delay = max(0.1, interval - max(0.0, elapsed_seconds))
        opening = calendar.get("next_regular_calendar_open_utc")
        if opening is not None:
            until_open = (round(datetime.fromisoformat(opening).timestamp() * 1000) - at_ms) / 1000
            delay = min(delay, max(0.0, until_open))
        self.delay_seconds = delay
        self.last_probe_at_ms = at_ms
        self.next_probe_at_ms = at_ms + round(delay * 1000)

    def snapshot(self):
        return {"last_probe_at_ms": self.last_probe_at_ms,
                "next_probe_at_ms": self.next_probe_at_ms,
                "interval_seconds": self.interval_seconds,
                "same_failure_count": self.same_failure_count,
                "last_failure_reason": self.last_reason,
                "calendar_open_at_probe": self.calendar_open,
                "diagnostic_only_not_quote_freshness": True}

class CaptureHealth:
    def __init__(self, path, period_sha256, now_ms):
        self.path = str(path)
        self.period_sha256 = period_sha256
        self.started_at_ms = now_ms
        self.counts = {"QUOTE": 0, "FAILURE": 0}
        self.failure_reasons = {}
        self.last_quote = None
        self.last_kind = None
        self.consecutive_failures = 0
        self.first_continuous_failure_ms = None
        self.regular_session_failure_in_streak = False

    def observe(self, kind, data, at_ms):
        if kind not in self.counts:
            return
        self.counts[kind] += 1
        self.last_kind = kind
        if kind == "QUOTE":
            self.last_quote = {
                "observed_at_ms": data["observed_at_ms"],
                "received_at_ms": at_ms,
                "stale_after_ms": min(data["observed_at_ms"] + 5000, data["stale_after_ms"]),
                "source_path": self.path,
            }
            self.consecutive_failures = 0
            self.first_continuous_failure_ms = None
            self.regular_session_failure_in_streak = False
        else:
            reason = data.get("reason", "UNSPECIFIED_CAPTURE_FAILURE")
            self.failure_reasons[reason] = self.failure_reasons.get(reason, 0) + 1
            self.consecutive_failures += 1
            if self.first_continuous_failure_ms is None:
                self.first_continuous_failure_ms = at_ms
            clock = session_clock(datetime.fromtimestamp(at_ms / 1000, timezone.utc))
            self.regular_session_failure_in_streak |= clock["calendar_regular_session_open"]

    def snapshot(self, at_ms, *, completed=False):
        total = sum(self.counts.values())
        q = self.last_quote
        return {
            "schema": SCHEMA, "instrument": "EUR.USD",
            "period_sha256": self.period_sha256,
            "source_path": self.path, "segment_id": Path(self.path).parent.name,
            "started_at_ms": self.started_at_ms, "at_ms": at_ms,
            "collector_pid": os.getpid(), "completed": completed,
            "counts": dict(self.counts), "capture_records": total,
            "valid_capture_ratio": self.counts["QUOTE"] / total if total else None,
            "count_semantics": "CAPTURE_RECORDS_NOT_INDEPENDENT_MARKET_UPDATES",
            "last_capture_kind": self.last_kind,
            "failure_reasons": dict(self.failure_reasons),
            "consecutive_failures": self.consecutive_failures,
            "first_continuous_failure_ms": self.first_continuous_failure_ms,
            "regular_session_failure_in_streak": self.regular_session_failure_in_streak,
            "last_valid_quote": q,
            "quote_fresh_at_snapshot": bool(q and q["observed_at_ms"] <= at_ms < q["stale_after_ms"]),
            "diagnostic_only_not_source_admission": True,
        }


def feed_status(progress, previous_quote, prior_open_failure, at_ms):
    clock = session_clock(datetime.fromtimestamp(at_ms / 1000, timezone.utc))
    if progress:
        q = progress.get("last_valid_quote") or previous_quote
        captures = progress.get("counts", {})
        unavailable = progress.get("last_capture_kind") != "QUOTE"
        stale = not 0 <= at_ms - progress["at_ms"] <= STALE_PROGRESS_MS
        if progress.get("last_valid_quote"):
            prior_open_failure = progress.get("regular_session_failure_in_streak", False)
        else:
            prior_open_failure |= progress.get("regular_session_failure_in_streak", False)
        state = "PROGRESS_STALE" if stale else ("CAPTURE_UNAVAILABLE" if unavailable else "QUOTE_CAPTURE_OBSERVED")
    else:
        q = previous_quote
        captures = {"QUOTE": 0, "FAILURE": 0}
        state = "AWAITING_COLLECTOR_PROGRESS"
    fresh = bool(q and q["observed_at_ms"] <= at_ms < q["stale_after_ms"])
    if state == "QUOTE_CAPTURE_OBSERVED" and not fresh:
        state = "QUOTE_CAPTURE_EXPIRED"
    return {
        "schema": SCHEMA, "at_ms": at_ms, "data_state": state,
        "quote_fresh_now": fresh,
        "current_segment_counts": captures, "last_valid_quote": q,
        "quote_age_ms": at_ms - q["observed_at_ms"] if q else None,
        "prior_regular_session_outage_unrecovered": bool(prior_open_failure),
        "outage_history_assessment": "KNOWN_OPEN_FAILURE_UNRECOVERED" if prior_open_failure else "NO_OPEN_FAILURE_ESTABLISHED",
        "last_valid_quote_unknown": q is None,
        "startup_history_scan_limit_segments": 200,
        "calendar": clock,
        "collector_progress": progress,
        "diagnostic_only_not_source_admission": True,
        "data_ready_from_calendar": False,
    }


def historical_last_quote(feed_root, period_sha256):
    """Bounded authenticated startup diagnostics, including later empty segments."""
    from .shadow_source import read_source, strict, digest
    def original_start(receipt):
        # Recovery segments have descriptive names. Order by authenticated
        # original HEADER time, never lexicographic names or file mtime.
        try:
            fd = os.open(receipt.parent / "quotes.jsonl", os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC)
            with os.fdopen(fd, "rb") as stream:
                raw = stream.readline(65537)
            if len(raw) > 65536:
                return -1
            event = strict(raw)
            if (event.get("kind") != "HEADER" or event.get("sequence") != 0
                or event.get("previous") != "0" * 64
                or type(event.get("at_ms")) is not int
                or event.get("sha256") != digest({k: v for k, v in event.items() if k != "sha256"})):
                return -1
            return event["at_ms"]
        except (OSError, ValueError, TypeError):
            return -1
    failed_open = False
    receipts = Path(feed_root).glob("segment-*/collector-receipt.json")
    for receipt in sorted(receipts, key=original_start, reverse=True)[:200]:
        try:
            summary = strict(receipt.read_bytes())
            # Once a later open-session failure is known, a quote-free segment
            # cannot change either the last-good quote or the outage boolean.
            if failed_open and summary.get("counts", {}).get("QUOTE", 0) == 0:
                continue
            source = receipt.parent / "quotes.jsonl"
            header, quotes, prefix = read_source(source)
            if header["period_sha256"] != period_sha256:
                continue
            raw = source.read_bytes()[:prefix["bytes"]]
            if hashlib.sha256(raw).hexdigest() != prefix["sha256"]:
                continue
            q = max(quotes, key=lambda v: v["known_at_ms"]) if quotes else None
            boundary = q["known_at_ms"] if q else -1
            for line in raw.splitlines():
                event = strict(line)
                if event["kind"] == "FAILURE" and event["at_ms"] > boundary:
                    clock = session_clock(datetime.fromtimestamp(event["at_ms"] / 1000, timezone.utc))
                    failed_open |= clock["calendar_regular_session_open"]
            if q:
                return {
                    "observed_at_ms": q["t"], "received_at_ms": q["known_at_ms"],
                    "stale_after_ms": q["t"] + 5000, "source_path": str(source),
                    "source_prefix_sha256": prefix["sha256"],
                    "source_chain_head": prefix["chain_head"],
                }, failed_open
        except (OSError, ValueError, KeyError, TypeError):
            continue
    return None, failed_open
