#!/usr/bin/env python3
"""Strict offline structural/replay verifier for HeptaTrader OMS JSONL."""
from __future__ import annotations

import argparse
from collections import Counter
import json
import math
import os
import stat
from pathlib import Path
import sys
from typing import Any

CURRENT_SCHEMA = 4
DEFAULT_MAX_BYTES = 64 * 1024 * 1024
DEFAULT_MAX_RECORDS = 65536
DEFAULT_MAX_RECORD_BYTES = 256 * 1024

NUMERIC_FIELDS = (
    "qty",
    "price",
    "broker_remaining_quantity",
    "broker_market_cap_price",
)
STRING_FIELDS = (
    "event",
    "client_req_id",
    "instrument",
    "side",
    "status",
    "reason",
    "source",
    "trace_id",
    "req_id",
    "risk_code",
    "venue",
    "strategy",
    "account",
    "event_id",
    "execution_domain",
    "request_hash",
    "venue_correlation_id",
    "broker_callback_type",
    "broker_service_epoch",
    "broker_message",
    "broker_advanced_order_reject_json",
    "broker_why_held",
    "broker_execution_id",
)


class JournalError(ValueError):
    pass


def unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise JournalError(f"duplicate key {key!r}")
        result[key] = value
    return result


def reject_constant(value: str) -> None:
    raise JournalError(f"non-finite JSON constant {value}")


def require_integer(event: dict[str, Any], key: str, line: int) -> int:
    value = event.get(key)
    if isinstance(value, bool) or not isinstance(value, int):
        raise JournalError(f"line {line}: {key} must be an integer")
    return value


def validate_event(value: Any, line: int) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise JournalError(f"line {line}: record must be a JSON object")
    schema = value.get("schema_version", 1)
    if isinstance(schema, bool) or not isinstance(schema, int) or schema < 1:
        raise JournalError(f"line {line}: invalid schema_version")
    if schema > CURRENT_SCHEMA:
        raise JournalError(
            f"line {line}: schema_version {schema} is newer than supported {CURRENT_SCHEMA}"
        )
    event_type = value.get("event")
    if not isinstance(event_type, str) or not event_type:
        raise JournalError(f"line {line}: event must be a non-empty string")
    require_integer(value, "ts_ms", line)
    order_id = value.get("order_id", -1)
    if isinstance(order_id, bool) or not isinstance(order_id, int):
        raise JournalError(f"line {line}: order_id must be an integer")
    for key in STRING_FIELDS:
        field = value.get(key, "")
        if not isinstance(field, str):
            raise JournalError(f"line {line}: {key} must be a string")
    for key in NUMERIC_FIELDS:
        field = value.get(key, 0.0)
        if isinstance(field, bool) or not isinstance(field, (int, float)):
            raise JournalError(f"line {line}: {key} must be numeric")
        if not math.isfinite(float(field)):
            raise JournalError(f"line {line}: {key} must be finite")
    for key in ("broker_connection_epoch", "broker_request_id", "broker_error_code"):
        field = value.get(key, 0)
        if isinstance(field, bool) or not isinstance(field, int):
            raise JournalError(f"line {line}: {key} must be an integer")
    if value.get("broker_connection_epoch", 0) < 0:
        raise JournalError(f"line {line}: broker_connection_epoch must be non-negative")
    value["_line"] = line
    return value


def read_records(path: Path, *, max_bytes: int = DEFAULT_MAX_BYTES,
                 max_records: int = DEFAULT_MAX_RECORDS,
                 max_record_bytes: int = DEFAULT_MAX_RECORD_BYTES,
                 observations: dict[str, Any] | None = None):
    """Bounded read-only parser. Never opens a special file for blocking I/O.

    Callers must consume to EOF before publishing a successful summary. The
    iterator is for offline diagnostics, not an incremental runtime projector.
    """
    for name, value, ceiling in (("bytes", max_bytes, 1024**3),
                                 ("records", max_records, 1000000),
                                 ("record bytes", max_record_bytes, 1024**2)):
        if type(value) is not int or not 1 <= value <= ceiling:
            raise JournalError(f"invalid {name} budget")
    metrics = observations if observations is not None else {}
    metrics.update(bytes=0, records=0, largest_record_bytes=0,
                   limits={"bytes": max_bytes, "records": max_records,
                           "record_bytes": max_record_bytes})
    fd = -1
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC)
        before = os.fstat(fd)
        if not stat.S_ISREG(before.st_mode):
            raise JournalError("journal must be a regular non-symlink file")
        if before.st_size > max_bytes:
            raise JournalError("OMS_REPLAY_BYTE_LIMIT")
        with os.fdopen(fd, "rb") as stream:
            fd = -1
            for number in range(1, max_records + 2):
                raw = stream.readline(max_record_bytes + 2)
                if not raw:
                    break
                metrics["bytes"] += len(raw)
                if metrics["bytes"] > max_bytes:
                    raise JournalError("OMS_REPLAY_BYTE_LIMIT")
                if number > max_records:
                    raise JournalError("OMS_REPLAY_RECORD_COUNT_LIMIT")
                length = len(raw) - (1 if raw.endswith(b"\n") else 0)
                if length > max_record_bytes:
                    raise JournalError("OMS_REPLAY_RECORD_BYTE_LIMIT")
                if not raw.endswith(b"\n"):
                    raise JournalError("OMS_REPLAY_TORN_RECORD")
                if length == 0:
                    raise JournalError(f"line {number}: empty record")
                try:
                    value = json.loads(raw[:-1].decode("utf-8"),
                                       object_pairs_hook=unique_object,
                                       parse_constant=reject_constant)
                except (json.JSONDecodeError, UnicodeError, JournalError) as error:
                    raise JournalError(f"line {number}: invalid JSON: {error}") from error
                metrics["records"] = number
                metrics["largest_record_bytes"] = max(metrics["largest_record_bytes"], length)
                yield validate_event(value, number)
            after = os.fstat(stream.fileno())
            current = os.stat(path, follow_symlinks=False)
            identity = lambda st: (st.st_dev, st.st_ino, st.st_size, st.st_mtime_ns, st.st_ctime_ns)
            if identity(before) != identity(after) or identity(after) != identity(current):
                raise JournalError("OMS_REPLAY_SNAPSHOT_CHANGED")
    except (OSError, OverflowError) as error:
        raise JournalError(f"cannot safely read journal: {error}") from error
    finally:
        if fd >= 0:
            os.close(fd)
    if not metrics["records"]:
        raise JournalError("journal contains no events")
    metrics["warning_at_80_percent"] = (
        metrics["bytes"] * 5 >= max_bytes * 4 or
        metrics["records"] * 5 >= max_records * 4 or
        metrics["largest_record_bytes"] * 5 >= max_record_bytes * 4)
    metrics["paper_authorized"] = False
    metrics["live_authorized"] = False


def load_events(path: Path, **budgets: Any) -> list[dict[str, Any]]:
    return list(read_records(path, **budgets))


def dedup_key(event: dict[str, Any]) -> tuple[Any, ...]:
    event_id = event.get("event_id", "")
    if event_id:
        return ("event_id", event_id)
    return (
        "compat",
        event.get("event", ""),
        event.get("ts_ms", 0),
        event.get("order_id", -1),
        event.get("req_id") or event.get("client_req_id", ""),
        event.get("status", ""),
        event.get("reason", ""),
        event.get("source", ""),
    )


def replay(events: list[dict[str, Any]]) -> tuple[Counter[str], dict[int, dict[str, Any]], int]:
    counts: Counter[str] = Counter()
    orders: dict[int, dict[str, Any]] = {}
    seen: set[tuple[Any, ...]] = set()
    duplicates = 0
    for event in events:
        key = dedup_key(event)
        if key in seen:
            duplicates += 1
            continue
        seen.add(key)
        event_type = event["event"]
        counts[event_type] += 1
        order_id = event.get("order_id", -1)
        if order_id <= 0:
            continue
        state = orders.setdefault(order_id, {})
        state["last_event"] = event_type
        state["last_ts_ms"] = event["ts_ms"]
        request_id = event.get("req_id") or event.get("client_req_id", "")
        if request_id:
            state["req_id"] = request_id
        if event.get("venue_correlation_id"):
            state["venue_correlation_id"] = event["venue_correlation_id"]
        if event.get("broker_execution_id"):
            state["broker_execution_id"] = event["broker_execution_id"]
        if event_type in {"place_sent", "broker_order_accepted"}:
            state["sent_or_accepted"] = True
        if event_type in {"status", "broker_order_status", "broker_completed_order"}:
            state["last_status"] = event.get("status", "")
            state["remaining_quantity"] = event.get("broker_remaining_quantity", 0.0)
        if event_type == "broker_execution":
            state["economic_execution_observed"] = bool(
                event.get("broker_execution_id")
                and float(event.get("qty", 0.0)) > 0.0
                and float(event.get("price", 0.0)) > 0.0
            )
        if event_type in {"cancel", "cancel_command_resolved"}:
            state["cancel_observed"] = True
        if event_type in {"reject", "risk_blocked", "broker_error"}:
            state["last_reject_reason"] = (
                event.get("risk_code") or event.get("reason") or event.get("broker_message", "")
            )
    return counts, orders, duplicates


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Strictly validate and summarize an OMS journal"
    )
    parser.add_argument("--journal", type=Path, default=Path("runtime-logs/oms_journal.jsonl"))
    parser.add_argument("--minimum-schema", type=int, default=1)
    parser.add_argument("--require-event", action="append", default=[])
    parser.add_argument("--max-bytes", type=int, default=DEFAULT_MAX_BYTES)
    parser.add_argument("--max-records", type=int, default=DEFAULT_MAX_RECORDS)
    parser.add_argument("--max-record-bytes", type=int, default=DEFAULT_MAX_RECORD_BYTES)
    parser.add_argument("--capacity-json", action="store_true",
                        help="stream a bounded read-only capacity summary; no order identities")
    args = parser.parse_args(argv)
    if args.minimum_schema < 1 or args.minimum_schema > CURRENT_SCHEMA:
        parser.error(f"--minimum-schema must be between 1 and {CURRENT_SCHEMA}")

    try:
        budgets = dict(max_bytes=args.max_bytes, max_records=args.max_records,
                       max_record_bytes=args.max_record_bytes)
        if args.capacity_json:
            metrics: dict[str, Any] = {}
            seen_events = set()
            for event in read_records(args.journal, observations=metrics, **budgets):
                if event.get("schema_version", 1) < args.minimum_schema:
                    raise JournalError("record below required minimum schema")
                if event["event"] in args.require_event:
                    seen_events.add(event["event"])
            if set(args.require_event) - seen_events:
                raise JournalError("required event types missing")
            print(json.dumps(metrics, sort_keys=True, separators=(",", ":")))
            return 0
        events = load_events(args.journal, **budgets)
        below = [event["_line"] for event in events if event.get("schema_version", 1) < args.minimum_schema]
        if below:
            raise JournalError(
                "events below minimum schema at lines " + ",".join(map(str, below[:20]))
            )
        counts, orders, duplicates = replay(events)
        missing = sorted(set(args.require_event) - set(counts))
        if missing:
            raise JournalError("required event types missing: " + ", ".join(missing))
    except JournalError as error:
        print(f"[OMS] FAIL: {error}", file=sys.stderr)
        return 2

    schema_counts = Counter(event.get("schema_version", 1) for event in events)
    print(f"events_total={len(events)}")
    print(f"events_unique={sum(counts.values())}")
    print(f"duplicates_skipped={duplicates}")
    print("schema_counts=" + json.dumps(dict(sorted(schema_counts.items())), separators=(",", ":")))
    print("event_counts=" + json.dumps(dict(sorted(counts.items())), separators=(",", ":")))
    print(f"replayed_orders={len(orders)}")
    print("[OMS] PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
