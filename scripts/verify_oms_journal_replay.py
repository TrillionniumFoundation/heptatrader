#!/usr/bin/env python3
"""Strict offline structural/replay verifier for HeptaTrader OMS JSONL."""
from __future__ import annotations

import argparse
from collections import Counter
import json
import math
from pathlib import Path
import sys
from typing import Any

CURRENT_SCHEMA = 4
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


def load_events(path: Path) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    try:
        with path.open("r", encoding="utf-8") as stream:
            for line_number, raw in enumerate(stream, 1):
                text = raw.rstrip("\r\n")
                if not text:
                    continue
                try:
                    value = json.loads(
                        text,
                        object_pairs_hook=unique_object,
                        parse_constant=reject_constant,
                    )
                except (json.JSONDecodeError, UnicodeError, JournalError) as error:
                    raise JournalError(f"line {line_number}: invalid JSON: {error}") from error
                events.append(validate_event(value, line_number))
    except OSError as error:
        raise JournalError(f"cannot read {path}: {error}") from error
    if not events:
        raise JournalError("journal contains no events")
    return events


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
    args = parser.parse_args(argv)
    if args.minimum_schema < 1 or args.minimum_schema > CURRENT_SCHEMA:
        parser.error(f"--minimum-schema must be between 1 and {CURRENT_SCHEMA}")

    try:
        events = load_events(args.journal)
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
