#!/usr/bin/env python3
"""Read-only OMS journal metrics and alert derivation.

The collector deliberately has no broker, gateway, or execution socket access.
It reads a bounded, stable regular file and emits Prometheus text plus JSON
alerts suitable for a node-exporter textfile collector or an operator runbook.
"""

from __future__ import annotations

import argparse
from collections import Counter
import json
import os
from pathlib import Path
import stat
import sys
import tempfile

MAX_LINE_BYTES = 1_048_576
DEFAULT_MAX_FILE_BYTES = 64 * 1024 * 1024
UNCERTAIN_EVENTS = {
    "place_outcome_uncertain",
    "flatten_outcome_uncertain",
    "cancel_outcome_uncertain",
    "execution_projection_failed",
}
TERMINAL_EVENTS = {
    "reject",
    "flatten_reject",
    "flatten_noop",
    "order_owner_reconciled_terminal",
    "execution_command_resolved",
    "cancel_command_resolved",
}
TERMINAL_STATUSES = {
    "cancelled",
    "canceled",
    "filled",
    "inactive",
    "rejected",
    "api cancelled",
    "apicancelled",
}


def stable_open(path: Path, max_bytes: int) -> tuple[int, os.stat_result]:
    before = path.lstat()
    if stat.S_ISLNK(before.st_mode) or not stat.S_ISREG(before.st_mode):
        raise RuntimeError("journal must be a regular non-symlink file")
    if before.st_uid not in (0, os.geteuid()):
        raise RuntimeError("journal has an untrusted owner")
    if stat.S_IMODE(before.st_mode) & 0o077:
        raise RuntimeError("journal must not be accessible by group or world")
    if before.st_size > max_bytes:
        raise RuntimeError("journal exceeds configured size bound")
    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(path, flags)
    opened = os.fstat(descriptor)
    identity = lambda value: (
        value.st_dev,
        value.st_ino,
        value.st_mode,
        value.st_uid,
        value.st_size,
        value.st_mtime_ns,
    )
    if identity(before) != identity(opened):
        os.close(descriptor)
        raise RuntimeError("journal changed before open")
    return descriptor, opened


def read_records(path: Path, max_bytes: int) -> tuple[list[dict], int]:
    descriptor, opened = stable_open(path, max_bytes)
    records: list[dict] = []
    malformed = 0
    with os.fdopen(descriptor, "rb", closefd=True) as source:
        for raw in source:
            if len(raw) > MAX_LINE_BYTES:
                malformed += 1
                continue
            try:
                record = json.loads(raw.decode("utf-8", errors="strict"))
            except (UnicodeDecodeError, json.JSONDecodeError):
                malformed += 1
                continue
            if not isinstance(record, dict) or not isinstance(record.get("event"), str):
                malformed += 1
                continue
            records.append(record)
        after = os.fstat(source.fileno())
        if (
            opened.st_dev,
            opened.st_ino,
            opened.st_size,
            opened.st_mtime_ns,
        ) != (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns):
            raise RuntimeError("journal changed while being collected")
    return records, malformed


def label(value: object) -> str:
    # Keep cardinality and parser work bounded even if a damaged journal
    # contains an unexpectedly large label value.
    text = str(value)[:128]
    return text.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n")


def derive(records: list[dict], malformed: int) -> tuple[str, list[dict]]:
    events: Counter[str] = Counter()
    risk_codes: Counter[str] = Counter()
    broker_errors: Counter[str] = Counter()
    duplicate_event_ids = 0
    seen_event_ids: set[str] = set()
    last_event_ms = 0
    latest_orders: dict[int, tuple[str, str]] = {}

    for record in records:
        event = record.get("event", "")
        events[event] += 1
        risk_code = record.get("risk_code")
        if isinstance(risk_code, str) and risk_code:
            risk_codes[risk_code] += 1
        broker_error = record.get("broker_error_code")
        if isinstance(broker_error, int) and not isinstance(broker_error, bool) and broker_error:
            broker_errors[str(broker_error)] += 1
        event_id = record.get("event_id")
        if isinstance(event_id, str) and event_id:
            if event_id in seen_event_ids:
                duplicate_event_ids += 1
            seen_event_ids.add(event_id)
        timestamp = record.get("ts_ms")
        if isinstance(timestamp, int) and not isinstance(timestamp, bool):
            last_event_ms = max(last_event_ms, timestamp)
        order_id = record.get("order_id")
        if isinstance(order_id, int) and not isinstance(order_id, bool) and order_id >= 0:
            status = record.get("status")
            latest_orders[order_id] = (
                event,
                status.lower() if isinstance(status, str) else "",
            )

    active_orders = sum(
        1
        for event, status in latest_orders.values()
        if event not in TERMINAL_EVENTS and status not in TERMINAL_STATUSES
    )
    uncertain = sum(events[item] for item in UNCERTAIN_EVENTS)

    lines = [
        "# HELP heptatrader_oms_events_total OMS journal events by type.",
        "# TYPE heptatrader_oms_events_total counter",
    ]
    for event, count in sorted(events.items()):
        lines.append(f'heptatrader_oms_events_total{{event="{label(event)}"}} {count}')
    lines.extend(
        [
            "# HELP heptatrader_oms_risk_blocks_total Risk decisions by stable code.",
            "# TYPE heptatrader_oms_risk_blocks_total counter",
        ]
    )
    for code, count in sorted(risk_codes.items()):
        lines.append(f'heptatrader_oms_risk_blocks_total{{risk_code="{label(code)}"}} {count}')
    lines.extend(
        [
            "# HELP heptatrader_oms_broker_errors_total Broker callback errors by code.",
            "# TYPE heptatrader_oms_broker_errors_total counter",
        ]
    )
    for code, count in sorted(broker_errors.items()):
        lines.append(f'heptatrader_oms_broker_errors_total{{code="{label(code)}"}} {count}')
    lines.extend(
        [
            "# HELP heptatrader_oms_malformed_lines_total Malformed bounded journal lines.",
            "# TYPE heptatrader_oms_malformed_lines_total gauge",
            f"heptatrader_oms_malformed_lines_total {malformed}",
            "# HELP heptatrader_oms_duplicate_event_ids_total Duplicate event identifiers.",
            "# TYPE heptatrader_oms_duplicate_event_ids_total gauge",
            f"heptatrader_oms_duplicate_event_ids_total {duplicate_event_ids}",
            "# HELP heptatrader_oms_outcome_uncertain_total Unresolved uncertain outcomes.",
            "# TYPE heptatrader_oms_outcome_uncertain_total gauge",
            f"heptatrader_oms_outcome_uncertain_total {uncertain}",
            "# HELP heptatrader_oms_active_orders Derived non-terminal order count.",
            "# TYPE heptatrader_oms_active_orders gauge",
            f"heptatrader_oms_active_orders {active_orders}",
            "# HELP heptatrader_oms_last_event_timestamp_seconds Last observed OMS event time.",
            "# TYPE heptatrader_oms_last_event_timestamp_seconds gauge",
            f"heptatrader_oms_last_event_timestamp_seconds {last_event_ms / 1000.0:.3f}",
        ]
    )

    alerts = []
    if malformed:
        alerts.append(
            {
                "severity": "P1",
                "rule": "OMS_JOURNAL_MALFORMED",
                "value": malformed,
                "message": "OMS journal contains malformed or oversized records",
            }
        )
    if duplicate_event_ids:
        alerts.append(
            {
                "severity": "P1",
                "rule": "OMS_DUPLICATE_EVENT_ID",
                "value": duplicate_event_ids,
                "message": "OMS journal contains duplicate event identifiers",
            }
        )
    if uncertain:
        alerts.append(
            {
                "severity": "P1",
                "rule": "OMS_OUTCOME_UNCERTAIN",
                "value": uncertain,
                "message": "One or more execution outcomes require reconciliation",
            }
        )
    if broker_errors.get("201", 0):
        alerts.append(
            {
                "severity": "P1",
                "rule": "IB_ORDER_REJECTED_201",
                "value": broker_errors["201"],
                "message": "IB reported order rejection error 201",
            }
        )
    return "\n".join(lines) + "\n", alerts


def atomic_write(path: Path, data: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    parent = path.parent.lstat()
    if stat.S_ISLNK(parent.st_mode) or not stat.S_ISDIR(parent.st_mode):
        raise RuntimeError("output parent must be a regular directory")
    if parent.st_uid not in (0, os.geteuid()):
        raise RuntimeError("output parent has an untrusted owner")
    if stat.S_IMODE(parent.st_mode) & 0o022:
        raise RuntimeError("output parent must not be group/world writable")
    descriptor, temporary = tempfile.mkstemp(prefix=path.name + ".", dir=str(path.parent))
    try:
        os.fchmod(descriptor, 0o600)
        with os.fdopen(descriptor, "w", encoding="utf-8", closefd=True) as output:
            output.write(data)
            output.flush()
            os.fsync(output.fileno())
        os.replace(temporary, path)
        directory = os.open(path.parent, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    except Exception:
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass
        raise


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--journal", type=Path, required=True)
    parser.add_argument("--metrics-output", type=Path)
    parser.add_argument("--alerts-output", type=Path)
    parser.add_argument("--max-file-bytes", type=int, default=DEFAULT_MAX_FILE_BYTES)
    parser.add_argument("--require-events", action="store_true")
    parser.add_argument(
        "--fail-on-p1",
        action="store_true",
        help="return a failing exit status when a P1 alert is emitted",
    )
    args = parser.parse_args()

    if args.max_file_bytes < 1:
        print("max-file-bytes must be positive", file=sys.stderr)
        return 2
    try:
        records, malformed = read_records(args.journal, args.max_file_bytes)
        metrics, alerts = derive(records, malformed)
        if args.require_events and not records:
            alerts.append(
                {
                    "severity": "P1",
                    "rule": "OMS_NO_EVENTS",
                    "value": 0,
                    "message": "OMS journal contains no valid events",
                }
            )
        if args.metrics_output:
            atomic_write(args.metrics_output, metrics)
        else:
            sys.stdout.write(metrics)
        if args.alerts_output:
            atomic_write(
                args.alerts_output,
                json.dumps(alerts, indent=2, sort_keys=True) + "\n",
            )
    except (OSError, RuntimeError, ValueError) as error:
        print(f"hepta-observability: {error}", file=sys.stderr)
        return 1
    return (
        1
        if args.fail_on_p1 and any(item["severity"] == "P1" for item in alerts)
        else 0
    )


if __name__ == "__main__":
    raise SystemExit(main())
