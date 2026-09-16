#!/usr/bin/env python3
"""Validate and report identifier-free IB authoritative-state observations.

This reader is intentionally read-only.  It accepts only the fixed observation
schema emitted by hepta-ib-executiond, exports fixed-cardinality Prometheus text,
and never treats missing metric families as zero or as trading authority.
"""
from __future__ import annotations

import argparse
from collections import deque
import json
import math
from pathlib import Path
import re
import stat
import sys
import time
from typing import Any

SCHEMA = "heptatrader.ib-runtime-observation.v1"
REPORT_SCHEMA = "heptatrader.ib-runtime-report.v1"
UINT64_MAX = (1 << 64) - 1
MAX_BYTES = 8 << 20
MAX_LINE = 131072
MAX_LINES = 100000
EPOCH = re.compile(r"[A-Za-z0-9_.:-]{1,128}\Z")

UINT_FIELDS = (
    "observed_at_ms", "monotonic_ms", "event_overflow_generation",
    "connection_epoch", "active_generation", "active_orders",
    "active_correlations", "terminal_generation", "terminal_orders",
    "terminal_executions", "terminal_exposure_generation", "risk_generation",
    "account_generation", "positions_generation", "fx_cash_generation",
    "risk_absorbed_exposure_generation", "positions", "exposure_generation",
    "terminal_callbacks_in_flight",
)
BOOL_FIELDS = (
    "connected", "event_stream_authoritative", "active_complete",
    "terminal_complete", "risk_complete", "coherent_risk_complete",
    "account_complete", "positions_complete", "fx_cash_complete",
    "post_fill_risk_reconciliation_pending", "recovery_barrier_complete",
    "new_connection_epoch_required", "terminal_transport_halted",
    "terminal_transport_drain_verified", "callback_lag_metrics_present",
    "callback_conflict_metrics_present", "network_policy_metrics_present",
)
REASON_FIELDS = ("active_reason", "terminal_reason", "risk_reason", "recovery_reason")


def unique(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON key")
        result[key] = value
    return result


def reject_constant(value: str) -> None:
    raise ValueError("non-finite JSON constant")


def finite_float(value: str) -> float:
    parsed = float(value)
    if not math.isfinite(parsed):
        raise ValueError("non-finite JSON number")
    if parsed == 0.0:
        coefficient = value.lower().partition("e")[0]
        if any(digit in coefficient for digit in "123456789"):
            raise ValueError("nonzero JSON number underflow")
    return parsed


def uint(value: Any) -> int:
    if type(value) is not int or not 0 <= value <= UINT64_MAX:
        raise ValueError("expected uint64")
    return value


def validate(sample: Any) -> dict[str, Any]:
    if not isinstance(sample, dict) or sample.get("schema") != SCHEMA:
        raise ValueError("unsupported IB runtime telemetry")
    if (sample.get("authorization_effect") != "NONE" or
            sample.get("paper_authorized") is not False or
            sample.get("live_authorized") is not False):
        raise ValueError("IB runtime observation cannot authorize trading")
    epoch = sample.get("service_epoch")
    if not isinstance(epoch, str) or EPOCH.fullmatch(epoch) is None:
        raise ValueError("invalid service epoch")
    for key in UINT_FIELDS:
        uint(sample.get(key))
    for key in BOOL_FIELDS:
        if type(sample.get(key)) is not bool:
            raise ValueError("invalid IB runtime boolean")
    for key in REASON_FIELDS:
        value = sample.get(key)
        if not isinstance(value, str) or len(value.encode("utf-8")) > 1024:
            raise ValueError("invalid bounded IB runtime reason")
    gross = sample.get("gross_absolute_position")
    if (isinstance(gross, bool) or not isinstance(gross, (int, float)) or
            not math.isfinite(float(gross)) or float(gross) < 0.0):
        raise ValueError("invalid gross absolute position")
    if sample["coherent_risk_complete"] and not all(
            sample[key] for key in ("risk_complete", "account_complete",
                                    "positions_complete", "fx_cash_complete")):
        raise ValueError("coherent risk cannot outlive incomplete legs")
    if sample["active_correlations"] > sample["active_orders"]:
        # Every mapped correlation denotes one active order ID. Multiple
        # correlations for one numeric order ID would be a provenance conflict.
        raise ValueError("active correlation count exceeds active orders")
    if sample["terminal_transport_drain_verified"] and not sample["terminal_transport_halted"]:
        raise ValueError("terminal drain cannot be verified before halt")
    return sample


def read_samples(path: Path) -> list[dict[str, Any]]:
    flags = __import__("os").O_RDONLY | getattr(__import__("os"), "O_NOFOLLOW", 0) | getattr(__import__("os"), "O_NONBLOCK", 0)
    fd = __import__("os").open(path, flags)
    try:
        before = __import__("os").fstat(fd)
        if (not stat.S_ISREG(before.st_mode) or before.st_nlink != 1 or
                before.st_size > MAX_BYTES):
            raise ValueError("requires bounded regular single-link input")
        recent: deque[dict[str, Any]] = deque(maxlen=120)
        total = 0
        with __import__("os").fdopen(fd, "rb", closefd=False) as stream:
            for number in range(MAX_LINES + 1):
                line = stream.readline(MAX_LINE + 1)
                if not line:
                    break
                if number == MAX_LINES or len(line) > MAX_LINE:
                    raise ValueError("IB telemetry input bound exceeded")
                total += len(line)
                if total > MAX_BYTES:
                    raise ValueError("IB telemetry input too large")
                if not line.lstrip().startswith(b"{"):
                    continue
                value = json.loads(line.decode("utf-8"), object_pairs_hook=unique,
                                   parse_constant=reject_constant,
                                   parse_float=finite_float)
                if isinstance(value, dict) and value.get("schema") == SCHEMA:
                    recent.append(validate(value))
        after = __import__("os").fstat(fd)
        named = path.stat(follow_symlinks=False)
        identity = lambda info: (info.st_dev, info.st_ino, info.st_size,
                                 info.st_mtime_ns, info.st_ctime_ns,
                                 info.st_mode, info.st_nlink)
        if identity(before) != identity(after) or identity(after) != identity(named):
            raise ValueError("IB telemetry input changed while reading")
    finally:
        __import__("os").close(fd)
    if not recent:
        raise ValueError("no supported IB runtime observations")
    return list(recent)


def report(samples: list[dict[str, Any]], now_ms: int, max_age_ms: int = 15000) -> dict[str, Any]:
    uint(now_ms); uint(max_age_ms)
    if not max_age_ms or not samples:
        raise ValueError("positive freshness budget required")
    samples = [validate(sample) for sample in samples]
    latest = samples[-1]
    age = now_ms - latest["observed_at_ms"]
    fresh = 0 <= age <= max_age_ms
    alerts: list[dict[str, str]] = []
    def alert(rule: str, severity: str = "P2") -> None:
        if not any(item["rule_id"] == rule for item in alerts):
            alerts.append({"rule_id": rule, "severity": severity})
    if not fresh:
        alert("IB_RUNTIME_TELEMETRY_CLOCK" if age < 0 else "IB_RUNTIME_TELEMETRY_STALE")
    if not latest["connected"]:
        alert("IB_RUNTIME_DISCONNECTED")
    if not latest["event_stream_authoritative"]:
        alert("IB_EVENT_STREAM_INCOMPLETE", "P1")
    if latest["event_overflow_generation"]:
        alert("IB_EVENT_STREAM_OVERFLOW", "P1")
    if not latest["risk_complete"] or not latest["active_complete"]:
        alert("IB_AUTHORITATIVE_SNAPSHOT_INCOMPLETE")
    if latest["post_fill_risk_reconciliation_pending"]:
        alert("IB_POST_FILL_RECONCILIATION_PENDING")
    if latest["new_connection_epoch_required"]:
        alert("IB_NEW_CONNECTION_EPOCH_REQUIRED", "P1")
    if latest["terminal_callbacks_in_flight"] and latest["terminal_transport_halted"]:
        alert("IB_TERMINAL_CALLBACK_DRAIN_PENDING", "P1")
    return {
        "schema": REPORT_SCHEMA,
        "fresh": fresh,
        "sample_age_ms": age,
        "service_epoch": latest["service_epoch"],
        "connection_epoch": latest["connection_epoch"],
        "alerts": alerts,
        "callback_lag_metrics_present": latest["callback_lag_metrics_present"],
        "callback_conflict_metrics_present": latest["callback_conflict_metrics_present"],
        "network_policy_metrics_present": latest["network_policy_metrics_present"],
        "authorization_effect": "NONE",
    }


def prometheus(latest: dict[str, Any], summary: dict[str, Any]) -> str:
    latest = validate(latest)
    values = {
        "hepta_ib_runtime_telemetry_fresh": int(summary["fresh"]),
        "hepta_ib_runtime_connected": int(latest["connected"]),
        "hepta_ib_event_stream_authoritative": int(latest["event_stream_authoritative"]),
        "hepta_ib_event_overflow_generation": latest["event_overflow_generation"],
        "hepta_ib_connection_epoch": latest["connection_epoch"],
        "hepta_ib_active_snapshot_generation": latest["active_generation"],
        "hepta_ib_active_snapshot_complete": int(latest["active_complete"]),
        "hepta_ib_active_orders": latest["active_orders"],
        "hepta_ib_active_correlations": latest["active_correlations"],
        "hepta_ib_terminal_snapshot_generation": latest["terminal_generation"],
        "hepta_ib_terminal_snapshot_complete": int(latest["terminal_complete"]),
        "hepta_ib_terminal_orders": latest["terminal_orders"],
        "hepta_ib_terminal_executions": latest["terminal_executions"],
        "hepta_ib_risk_snapshot_generation": latest["risk_generation"],
        "hepta_ib_account_generation": latest["account_generation"],
        "hepta_ib_positions_generation": latest["positions_generation"],
        "hepta_ib_fx_cash_generation": latest["fx_cash_generation"],
        "hepta_ib_risk_snapshot_complete": int(latest["risk_complete"]),
        "hepta_ib_coherent_risk_complete": int(latest["coherent_risk_complete"]),
        "hepta_ib_account_complete": int(latest["account_complete"]),
        "hepta_ib_positions_complete": int(latest["positions_complete"]),
        "hepta_ib_fx_cash_complete": int(latest["fx_cash_complete"]),
        "hepta_ib_risk_absorbed_exposure_generation": latest["risk_absorbed_exposure_generation"],
        "hepta_ib_gross_absolute_position": float(latest["gross_absolute_position"]),
        "hepta_ib_position_instruments": latest["positions"],
        "hepta_ib_post_fill_reconciliation_pending": int(latest["post_fill_risk_reconciliation_pending"]),
        "hepta_ib_exposure_generation": latest["exposure_generation"],
        "hepta_ib_recovery_barrier_complete": int(latest["recovery_barrier_complete"]),
        "hepta_ib_new_connection_epoch_required": int(latest["new_connection_epoch_required"]),
        "hepta_ib_terminal_transport_halted": int(latest["terminal_transport_halted"]),
        "hepta_ib_terminal_transport_drain_verified": int(latest["terminal_transport_drain_verified"]),
        "hepta_ib_terminal_callbacks_in_flight": latest["terminal_callbacks_in_flight"],
        "hepta_ib_callback_lag_metrics_present": int(latest["callback_lag_metrics_present"]),
        "hepta_ib_callback_conflict_metrics_present": int(latest["callback_conflict_metrics_present"]),
        "hepta_ib_network_policy_metrics_present": int(latest["network_policy_metrics_present"]),
        "hepta_ib_runtime_alerts": len(summary["alerts"]),
    }
    return "\n".join(f"{name} {value}" for name, value in values.items()) + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--now-ms", type=int)
    parser.add_argument("--max-age-ms", type=int, default=15000)
    parser.add_argument("--format", choices=("json", "prometheus"), default="json")
    args = parser.parse_args(argv)
    try:
        samples = read_samples(args.input)
        now = time.time_ns() // 1000000 if args.now_ms is None else args.now_ms
        summary = report(samples, now, args.max_age_ms)
        if args.format == "prometheus":
            print(prometheus(samples[-1], summary), end="")
        else:
            print(json.dumps(summary, sort_keys=True, allow_nan=False))
        return 1 if summary["alerts"] else 0
    except (OSError, ValueError, TypeError, OverflowError, RecursionError):
        print("IB_RUNTIME_TELEMETRY_INPUT_INVALID", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
