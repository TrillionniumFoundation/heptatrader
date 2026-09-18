#!/usr/bin/env python3
"""Validate, report and atomically publish identifier-free IB runtime state."""
from __future__ import annotations

import argparse
from collections import deque
import fcntl
import json
import math
import os
from pathlib import Path
import re
import secrets
import stat
import sys
import time
from typing import Any, Callable

import hepta_oms_report as base_metrics

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
        raise ValueError("active correlation count exceeds active orders")
    if sample["terminal_transport_drain_verified"] and not sample["terminal_transport_halted"]:
        raise ValueError("terminal drain cannot be verified before halt")
    if sample["callback_lag_metrics_present"]:
        base_metrics.validate_latency(sample.get("callback_queue_lag"))
    elif "callback_queue_lag" in sample:
        raise ValueError("callback lag payload without presence")
    if sample["callback_conflict_metrics_present"]:
        uint(sample.get("callback_conflicts_total"))
        if type(sample.get("callback_conflict_metrics_saturated")) is not bool:
            raise ValueError("invalid callback conflict saturation")
    elif ("callback_conflicts_total" in sample or
          "callback_conflict_metrics_saturated" in sample):
        raise ValueError("callback conflict payload without presence")
    for presence, valid, age in (
            ("quote_age_metrics_present", "primary_quote_age_valid",
             "primary_quote_age_ms"),
            ("snapshot_age_metrics_present", "authoritative_snapshot_age_valid",
             "authoritative_snapshot_age_ms")):
        present = sample.get(presence, False)
        if type(present) is not bool:
            raise ValueError("invalid IB age-metric presence")
        if present:
            if type(sample.get(valid)) is not bool:
                raise ValueError("invalid IB age validity")
            uint(sample.get(age))
            if not sample[valid] and sample[age] != 0:
                raise ValueError("unknown IB age cannot carry a value")
    reconciliation_present = sample.get(
        "broker_reconciliation_duration_metrics_present", False)
    if type(reconciliation_present) is not bool:
        raise ValueError("invalid reconciliation-metric presence")
    if reconciliation_present:
        base_metrics.validate_latency(
            sample.get("broker_reconciliation_duration"))
    for presence, latency in (
            ("broker_reconnect_duration_metrics_present",
             "broker_reconnect_duration"),
            ("broker_reconnect_refresh_duration_metrics_present",
             "broker_reconnect_refresh_duration")):
        present = sample.get(presence, False)
        if type(present) is not bool:
            raise ValueError("invalid reconnect latency presence")
        if present:
            base_metrics.validate_latency(sample.get(latency))
    return sample


def read_samples(path: Path) -> list[dict[str, Any]]:
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0)
    fd = os.open(path, flags)
    try:
        before = os.fstat(fd)
        if (not stat.S_ISREG(before.st_mode) or before.st_nlink != 1 or
                before.st_size > MAX_BYTES):
            raise ValueError("requires bounded regular single-link input")
        recent: deque[dict[str, Any]] = deque(maxlen=120)
        total = 0
        with os.fdopen(fd, "rb", closefd=False) as stream:
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
        after = os.fstat(fd)
        named = path.stat(follow_symlinks=False)
        identity = lambda info: (info.st_dev, info.st_ino, info.st_size,
                                 info.st_mtime_ns, info.st_ctime_ns,
                                 info.st_mode, info.st_nlink)
        if identity(before) != identity(after) or identity(after) != identity(named):
            raise ValueError("IB telemetry input changed while reading")
    finally:
        os.close(fd)
    if not recent:
        raise ValueError("no supported IB runtime observations")
    return list(recent)


def observed_condition_duration_ms(
        samples: list[dict[str, Any]],
        predicate: Callable[[dict[str, Any]], bool]) -> int:
    """Return a conservative continuous-duration lower bound from recent samples.

    A service or connection epoch boundary resets the interval. Monotonic clock
    regression also resets it rather than manufacturing negative or cross-process
    duration. The result is zero when the latest sample does not satisfy the
    condition; it never claims time before the oldest retained matching sample.
    """
    if not samples or not predicate(samples[-1]):
        return 0
    latest = samples[-1]
    latest_mono = uint(latest["monotonic_ms"])
    earliest = latest_mono
    previous = latest_mono
    for sample in reversed(samples[:-1]):
        if (sample["service_epoch"] != latest["service_epoch"] or
                sample["connection_epoch"] != latest["connection_epoch"] or
                not predicate(sample)):
            break
        current = uint(sample["monotonic_ms"])
        if current > previous:
            break
        earliest = current
        previous = current
    return latest_mono - earliest


def report(samples: list[dict[str, Any]], now_ms: int, max_age_ms: int = 15000) -> dict[str, Any]:
    uint(now_ms); uint(max_age_ms)
    if not max_age_ms or not samples:
        raise ValueError("positive freshness budget required")
    samples = [validate(sample) for sample in samples]
    latest = samples[-1]
    age = now_ms - latest["observed_at_ms"]
    fresh = 0 <= age <= max_age_ms
    post_fill_pending_ms = observed_condition_duration_ms(
        samples, lambda sample: sample["post_fill_risk_reconciliation_pending"])
    snapshot_incomplete_ms = observed_condition_duration_ms(
        samples, lambda sample: not sample["risk_complete"] or not sample["active_complete"])
    terminal_drain_pending_ms = observed_condition_duration_ms(
        samples, lambda sample: sample["terminal_transport_halted"] and
        sample["terminal_callbacks_in_flight"] > 0)
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
        "schema": REPORT_SCHEMA, "fresh": fresh, "sample_age_ms": age,
        "service_epoch": latest["service_epoch"],
        "connection_epoch": latest["connection_epoch"], "alerts": alerts,
        "post_fill_reconciliation_pending_observed_ms": post_fill_pending_ms,
        "authoritative_snapshot_incomplete_observed_ms": snapshot_incomplete_ms,
        "terminal_callback_drain_pending_observed_ms": terminal_drain_pending_ms,
        "callback_lag_metrics_present": latest["callback_lag_metrics_present"],
        "callback_conflict_metrics_present": latest["callback_conflict_metrics_present"],
        "network_policy_metrics_present": latest["network_policy_metrics_present"],
        "quote_age_metrics_present": latest.get("quote_age_metrics_present", False),
        "snapshot_age_metrics_present": latest.get("snapshot_age_metrics_present", False),
        "broker_reconciliation_duration_metrics_present": latest.get(
            "broker_reconciliation_duration_metrics_present", False),
        "broker_reconnect_duration_metrics_present": latest.get(
            "broker_reconnect_duration_metrics_present", False),
        "broker_reconnect_refresh_duration_metrics_present": latest.get(
            "broker_reconnect_refresh_duration_metrics_present", False),
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
        "hepta_ib_post_fill_reconciliation_pending_observed_ms": summary["post_fill_reconciliation_pending_observed_ms"],
        "hepta_ib_authoritative_snapshot_incomplete_observed_ms": summary["authoritative_snapshot_incomplete_observed_ms"],
        "hepta_ib_terminal_callback_drain_pending_observed_ms": summary["terminal_callback_drain_pending_observed_ms"],
        "hepta_ib_exposure_generation": latest["exposure_generation"],
        "hepta_ib_recovery_barrier_complete": int(latest["recovery_barrier_complete"]),
        "hepta_ib_new_connection_epoch_required": int(latest["new_connection_epoch_required"]),
        "hepta_ib_terminal_transport_halted": int(latest["terminal_transport_halted"]),
        "hepta_ib_terminal_transport_drain_verified": int(latest["terminal_transport_drain_verified"]),
        "hepta_ib_terminal_callbacks_in_flight": latest["terminal_callbacks_in_flight"],
        "hepta_ib_callback_lag_metrics_present": int(latest["callback_lag_metrics_present"]),
        "hepta_ib_callback_conflict_metrics_present": int(latest["callback_conflict_metrics_present"]),
        "hepta_ib_network_policy_metrics_present": int(latest["network_policy_metrics_present"]),
        "hepta_ib_quote_age_metrics_present": int(
            latest.get("quote_age_metrics_present", False)),
        "hepta_ib_snapshot_age_metrics_present": int(
            latest.get("snapshot_age_metrics_present", False)),
        "hepta_ib_broker_reconciliation_duration_metrics_present": int(
            latest.get("broker_reconciliation_duration_metrics_present", False)),
        "hepta_ib_broker_reconnect_duration_metrics_present": int(
            latest.get("broker_reconnect_duration_metrics_present", False)),
        "hepta_ib_broker_reconnect_refresh_duration_metrics_present": int(
            latest.get("broker_reconnect_refresh_duration_metrics_present", False)),
        "hepta_ib_runtime_alerts": len(summary["alerts"]),
    }
    lines = [f"{name} {value}" for name, value in values.items()]
    if latest["callback_lag_metrics_present"]:
        latency = latest["callback_queue_lag"]
        lines.append(
            "hepta_ib_callback_lag_metrics_saturated " +
            str(int(latency["saturated"])))
        if not latency["saturated"] and "bucket_counts" in latency:
            metric = "hepta_ib_callback_queue_lag_seconds"
            lines.append(f"# TYPE {metric} histogram")
            cumulative = 0
            for index, count in enumerate(latency["bucket_counts"]):
                cumulative += count
                upper = (str(base_metrics.BOUNDS_NS[index] / 1e9)
                         if index < len(base_metrics.BOUNDS_NS) else "+Inf")
                lines.append(f'{metric}_bucket{{le="{upper}"}} {cumulative}')
            lines.append(f"{metric}_count {latency['samples']}")
            lines.append(f"{metric}_sum {latency['total_ns'] / 1e9}")
    if latest["callback_conflict_metrics_present"]:
        saturated = latest["callback_conflict_metrics_saturated"]
        lines.append(
            "hepta_ib_callback_conflict_metrics_saturated " +
            str(int(saturated)))
        if not saturated:
            lines.append("# TYPE hepta_ib_callback_conflicts_total counter")
            lines.append(
                "hepta_ib_callback_conflicts_total " +
                str(latest["callback_conflicts_total"]))
    if latest.get("quote_age_metrics_present", False):
        lines.append(
            "hepta_ib_primary_quote_age_valid " +
            str(int(latest["primary_quote_age_valid"])))
        if latest["primary_quote_age_valid"]:
            lines.append(
                "hepta_ib_primary_quote_age_ms " +
                str(latest["primary_quote_age_ms"]))
    if latest.get("snapshot_age_metrics_present", False):
        lines.append(
            "hepta_ib_authoritative_snapshot_age_valid " +
            str(int(latest["authoritative_snapshot_age_valid"])))
        if latest["authoritative_snapshot_age_valid"]:
            lines.append(
                "hepta_ib_authoritative_snapshot_age_ms " +
                str(latest["authoritative_snapshot_age_ms"]))
    if latest.get("broker_reconciliation_duration_metrics_present", False):
        latency = latest["broker_reconciliation_duration"]
        lines.append(
            "hepta_ib_broker_reconciliation_duration_metrics_saturated " +
            str(int(latency["saturated"])))
        if not latency["saturated"] and "bucket_counts" in latency:
            metric = "hepta_ib_broker_reconciliation_duration_seconds"
            lines.append(f"# TYPE {metric} histogram")
            cumulative = 0
            for index, count in enumerate(latency["bucket_counts"]):
                cumulative += count
                upper = (str(base_metrics.BOUNDS_NS[index] / 1e9)
                         if index < len(base_metrics.BOUNDS_NS) else "+Inf")
                lines.append(f'{metric}_bucket{{le="{upper}"}} {cumulative}')
            lines.append(f"{metric}_count {latency['samples']}")
            lines.append(f"{metric}_sum {latency['total_ns'] / 1e9}")
    for presence, field, metric in (
            ("broker_reconnect_duration_metrics_present",
             "broker_reconnect_duration",
             "hepta_ib_broker_reconnect_duration_seconds"),
            ("broker_reconnect_refresh_duration_metrics_present",
             "broker_reconnect_refresh_duration",
             "hepta_ib_broker_reconnect_refresh_duration_seconds")):
        if not latest.get(presence, False):
            continue
        latency = latest[field]
        lines.append(metric + "_metrics_saturated " +
                     str(int(latency["saturated"])))
        if not latency["saturated"] and "bucket_counts" in latency:
            lines.append(f"# TYPE {metric} histogram")
            cumulative = 0
            for index, count in enumerate(latency["bucket_counts"]):
                cumulative += count
                upper = (str(base_metrics.BOUNDS_NS[index] / 1e9)
                         if index < len(base_metrics.BOUNDS_NS) else "+Inf")
                lines.append(f'{metric}_bucket{{le="{upper}"}} {cumulative}')
            lines.append(f"{metric}_count {latency['samples']}")
            lines.append(f"{metric}_sum {latency['total_ns'] / 1e9}")
    return "\n".join(lines) + "\n"


def collection_metrics(now_ms: int, latest: dict[str, Any] | None = None) -> str:
    uint(now_ms)
    lines = ["# TYPE hepta_ib_collector_success gauge",
             f"hepta_ib_collector_success {int(latest is not None)}",
             "# TYPE hepta_ib_collector_timestamp_seconds gauge",
             f"hepta_ib_collector_timestamp_seconds {now_ms // 1000}.{now_ms % 1000:03d}"]
    if latest is None:
        lines.append("hepta_ib_runtime_telemetry_fresh 0")
    else:
        observed = uint(latest["observed_at_ms"])
        lines += ["# TYPE hepta_ib_sample_timestamp_seconds gauge",
                  f"hepta_ib_sample_timestamp_seconds {observed // 1000}.{observed % 1000:03d}"]
    return "\n".join(lines) + "\n"


def publish_metrics(directory: Path, text: str) -> None:
    data = text.encode("utf-8")
    if not data or len(data) > 1 << 20 or not data.endswith(b"\n"):
        raise ValueError("invalid IB metrics output bound")
    directory_fd = base_metrics._open_metrics_directory(directory)
    lock_fd = temporary_fd = None
    temporary = None
    try:
        name, lock_name = "hepta_ib.prom", ".hepta_ib.lock"
        lock_fd = os.open(lock_name, os.O_RDWR | os.O_CREAT | os.O_NONBLOCK |
                          os.O_NOFOLLOW | os.O_CLOEXEC, 0o600, dir_fd=directory_fd)
        locked = base_metrics._metric_leaf(directory_fd, lock_name, 0o600)
        if locked != base_metrics._metric_file_identity(os.fstat(lock_fd)):
            raise ValueError("IB metrics lock identity changed")
        fcntl.flock(lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        previous = base_metrics._metric_leaf(directory_fd, name, 0o644)
        temporary = f".hepta_ib.{secrets.token_hex(16)}.tmp"
        temporary_fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL |
                               os.O_NOFOLLOW | os.O_CLOEXEC, 0o600, dir_fd=directory_fd)
        os.fchmod(temporary_fd, 0o644)
        offset = 0
        while offset < len(data):
            written = os.write(temporary_fd, data[offset:])
            if written <= 0:
                raise OSError("short IB metrics write")
            offset += written
        os.fsync(temporary_fd)
        if base_metrics._metric_leaf(directory_fd, temporary, 0o644) != base_metrics._metric_file_identity(os.fstat(temporary_fd)):
            raise ValueError("IB metrics temporary identity changed")
        check_fd = base_metrics._open_metrics_directory(directory)
        try:
            old, current = os.fstat(directory_fd), os.fstat(check_fd)
            if (old.st_dev, old.st_ino) != (current.st_dev, current.st_ino):
                raise ValueError("IB metrics namespace changed")
        finally:
            os.close(check_fd)
        if (base_metrics._metric_leaf(directory_fd, name, 0o644) != previous or
                base_metrics._metric_leaf(directory_fd, lock_name, 0o600) != locked):
            raise ValueError("IB metrics output or lock changed")
        os.replace(temporary, name, src_dir_fd=directory_fd, dst_dir_fd=directory_fd)
        temporary = None
        os.fsync(directory_fd)
    finally:
        if temporary is not None and temporary_fd is not None:
            try:
                named = os.stat(temporary, dir_fd=directory_fd, follow_symlinks=False)
                pinned = os.fstat(temporary_fd)
                if (named.st_dev, named.st_ino) == (pinned.st_dev, pinned.st_ino):
                    os.unlink(temporary, dir_fd=directory_fd)
            except OSError:
                pass
        if temporary_fd is not None:
            os.close(temporary_fd)
        if lock_fd is not None:
            os.close(lock_fd)
        os.close(directory_fd)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--now-ms", type=int)
    parser.add_argument("--max-age-ms", type=int, default=15000)
    parser.add_argument("--format", choices=("json", "prometheus"), default="json")
    parser.add_argument("--output-dir", type=Path)
    args = parser.parse_args(argv)
    if args.output_dir is not None and (args.format != "prometheus" or args.now_ms is not None):
        parser.error("publication requires --format prometheus and the real wall clock")
    now = time.time_ns() // 1000000 if args.now_ms is None else args.now_ms
    try:
        samples = read_samples(args.input)
        summary = report(samples, now, args.max_age_ms)
        output = prometheus(samples[-1], summary) if args.format == "prometheus" else json.dumps(summary, sort_keys=True, allow_nan=False)
        result = 1 if summary["alerts"] else 0
        if args.output_dir is not None:
            output += collection_metrics(now, samples[-1])
    except (OSError, ValueError, TypeError, OverflowError, RecursionError):
        print("IB_RUNTIME_TELEMETRY_INPUT_INVALID", file=sys.stderr)
        if args.output_dir is None:
            return 2
        result = 2
        output = collection_metrics(now)
    if args.output_dir is not None:
        try:
            publish_metrics(args.output_dir, output)
        except (OSError, ValueError):
            print("IB_METRICS_PUBLICATION_FAILED", file=sys.stderr)
            return 2
    if result != 2:
        print(output, end="\n" if args.format == "json" else "")
    return result


if __name__ == "__main__":
    raise SystemExit(main())
