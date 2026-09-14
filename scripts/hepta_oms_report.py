#!/usr/bin/env python3
"""Read-only OMS telemetry report/Prometheus text; no network or trading action.

Input is a consistent regular-file export of ONE daemon's JSON log messages.
This is not a log follower, journal replay validator, or notification daemon.
"""
from __future__ import annotations

import argparse
from collections import deque
import hashlib
import fcntl
import json
import math
import os
from pathlib import Path
import re
import stat
import secrets
import sys
import time

SCHEMA = "heptatrader.oms-capacity.v1"
GATEWAY_SCHEMA = "heptatrader.gateway-metrics.v1"
GATEWAY_LATENCIES = ("queue_wait_latency", "execution_latency", "response_write_latency")
GATEWAY_COUNTERS = ("responses_delivered", "response_write_failures", "queue_backpressure_rejections",
                    "owner_backpressure_rejections", "deadline_rejections", "cancelled_requests")
GATEWAY_GAUGES = ("pending_connections", "active_requests", "ready_owners", "max_pending_connections")
RESULT_NAMES = ("ok", "permission_denied", "invalid_tool", "rejected", "duplicate", "uncertain", "error")
BOUNDS_NS = (1000, 10000, 100000, 1000000, 5000000, 10000000,
             50000000, 100000000, 1000000000, 10000000000)
LATENCIES = ("append_latency", "data_sync_latency", "replay_validation_latency")
EXECUTION_OPERATIONS = ("place", "cancel", "flatten")
EXECUTION_RESULTS = ("accepted", "rejected", "duplicate", "uncertain", "exception")
EXECUTION_GAUGES = ("retained_commands", "order_owners", "fenced_owners",
                    "recovery_only_owners", "retained_send_attempts")
EXECUTION_LATENCIES = ("place_latency", "cancel_latency", "flatten_latency", "recovery_latency")
EXECUTION_TIMING_EXTENSION = tuple(name + suffix for name in EXECUTION_LATENCIES[:3]
                                   for suffix in ("_lock_wait", "_total"))
UINT64_MAX = (1 << 64) - 1
MAX_BYTES, MAX_LINE, MAX_LINES = 64 << 20, 65536, 100000


def unique(pairs):
    obj = {}
    for key, value in pairs:
        if key in obj:
            raise ValueError("duplicate JSON field")
        obj[key] = value
    return obj


def finite_float(text):
    value = float(text)
    significand = text.lower().split("e", 1)[0]
    if not math.isfinite(value) or value == 0 and any(c in "123456789" for c in significand):
        raise ValueError("unrepresentable JSON number")
    return value


def reject_constant(_):
    raise ValueError("non-finite JSON value")


def uint(value):
    if type(value) is not int or not 0 <= value <= UINT64_MAX:
        raise ValueError("invalid unsigned metric")
    return value


def validate_latency(metric):
    if not isinstance(metric, dict) or type(metric.get("saturated")) is not bool:
        raise ValueError("invalid latency summary")
    for key in ("samples", "total_ns", "max_ns", "last_ns"):
        uint(metric.get(key))
    if metric["last_ns"] > metric["max_ns"] or metric["max_ns"] > metric["total_ns"]:
        raise ValueError("inconsistent latency summary")
    if not metric["samples"] and any(metric[k] for k in ("total_ns", "max_ns", "last_ns")):
        raise ValueError("empty latency summary has observations")
    buckets = metric.get("bucket_counts")
    if buckets is not None:
        if not isinstance(buckets, list) or len(buckets) != len(BOUNDS_NS) + 1:
            raise ValueError("invalid latency bucket inventory")
        if metric.get("bucket_upper_ns") != list(BOUNDS_NS) + [None]:
            raise ValueError("unsupported histogram boundaries")
        for count in buckets:
            uint(count)
        if not metric["saturated"] and sum(buckets) != metric["samples"]:
            raise ValueError("histogram sample accounting mismatch")


def validate_execution(value):
    if not isinstance(value, dict):
        raise ValueError("invalid execution telemetry")
    for flag in ("metrics_saturated", "mutation_blocked"):
        if type(value.get(flag)) is not bool:
            raise ValueError("invalid execution telemetry presence")
    for key in EXECUTION_GAUGES:
        uint(value.get(key))
    results = value.get("results")
    if not isinstance(results, list) or len(results) != len(EXECUTION_OPERATIONS):
        raise ValueError("invalid execution operation inventory")
    for row, name in zip(results, EXECUTION_LATENCIES):
        if not isinstance(row, list) or len(row) != len(EXECUTION_RESULTS):
            raise ValueError("invalid execution result inventory")
        for count in row:
            uint(count)
        latency = value.get(name)
        validate_latency(latency)
        if not value["metrics_saturated"] and not latency["saturated"] and sum(row) != latency["samples"]:
            raise ValueError("execution result/latency accounting mismatch")
    validate_latency(value.get("recovery_latency"))
    for name in EXECUTION_LATENCIES[:3]:
        pair = (name + "_lock_wait", name + "_total")
        if not any(key in value for key in pair):
            continue  # Old/idle producer: absence is not a measured zero.
        for key in pair:
            validate_latency(value.get(key))
        held, wait, total = value[name], value[pair[0]], value[pair[1]]
        if not any(metric["saturated"] for metric in (held, wait, total)):
            if held["samples"] != wait["samples"] or held["samples"] != total["samples"]:
                raise ValueError("execution timing sample accounting mismatch")
            if total["total_ns"] != held["total_ns"] + wait["total_ns"]:
                raise ValueError("execution timing scope accounting mismatch")
            if total["last_ns"] != held["last_ns"] + wait["last_ns"]:
                raise ValueError("execution timing last-sample accounting mismatch")
    return value


def validate(sample):
    if not isinstance(sample, dict) or sample.get("schema") != SCHEMA:
        raise ValueError("unsupported telemetry schema")
    if sample.get("authorization_effect") != "NONE":
        raise ValueError("invalid telemetry authority claim")
    for key in ("known", "write_poisoned"):
        if type(sample.get(key)) is not bool:
            raise ValueError("invalid telemetry presence")
    for key in ("observed_at_ms", "max_bytes", "max_records", "pending_records", "queue_depth", "buffered_depth"):
        uint(sample.get(key))
    if sample["pending_records"] != sample["queue_depth"] + sample["buffered_depth"]:
        raise ValueError("pending record accounting mismatch")
    if sample.get("status") not in {"OK", "WARNING", "EXCEEDED", "UNKNOWN"}:
        raise ValueError("invalid capacity status")
    if sample["known"]:
        if sample["write_poisoned"] or not sample["max_bytes"] or not sample["max_records"]:
            raise ValueError("invalid known capacity")
        for key in ("bytes", "records", "byte_headroom", "record_headroom"):
            uint(sample.get(key))
        projected = uint(sample["records"] + sample["pending_records"])
        if (sample["byte_headroom"] != max(0, sample["max_bytes"] - sample["bytes"])
                or sample["record_headroom"] != max(0, sample["max_records"] - projected)):
            raise ValueError("headroom accounting mismatch")
        expected = ("EXCEEDED" if sample["bytes"] > sample["max_bytes"] or projected > sample["max_records"]
                    else "WARNING" if sample["bytes"] >= sample["max_bytes"] - sample["max_bytes"] // 5
                    or projected >= sample["max_records"] - sample["max_records"] // 5 else "OK")
        if sample["status"] != expected:
            raise ValueError("capacity status mismatch")
    elif sample["status"] != "UNKNOWN" or any(sample.get(k) is not None for k in ("bytes", "records", "byte_headroom", "record_headroom")):
        raise ValueError("unknown capacity is not observed zero")
    queue_fields = ("pending_bytes", "max_pending_bytes", "max_pending_records", "queue_capacity_rejections")
    if any(k in sample for k in queue_fields):
        for key in queue_fields:
            uint(sample.get(key))
        if not sample["max_pending_bytes"] or not sample["max_pending_records"]:
            raise ValueError("invalid queue budgets")
        if (sample["pending_bytes"] > sample["max_pending_bytes"] or
                sample["pending_records"] > sample["max_pending_records"]):
            raise ValueError("buffer occupancy exceeds configured hard budget")
    epoch = sample.get("service_epoch")
    if epoch is not None and (not isinstance(epoch, str) or re.fullmatch(r"[A-Za-z0-9_.:-]{1,128}", epoch) is None):
        raise ValueError("invalid service epoch")
    uint(sample.get("monotonic_ms", 0))
    for name in LATENCIES:
        metric = sample.get(name)
        if metric is None:  # Prior artifact has no histogram; never synthesize zeros.
            continue
        validate_latency(metric)
    if "execution_metrics" in sample:
        if not epoch or not sample.get("monotonic_ms"):
            raise ValueError("execution metrics require a service incarnation and clock")
        validate_execution(sample["execution_metrics"])
    return sample


def read_samples(path: Path, kind="oms"):
    schema = GATEWAY_SCHEMA if kind == "gateway" else SCHEMA
    validator = validate_gateway if kind == "gateway" else validate
    fd = os.open(path, os.O_RDONLY | os.O_NONBLOCK | os.O_NOFOLLOW | os.O_CLOEXEC)
    recent, digest = deque(maxlen=120), hashlib.sha256()
    try:
        before = os.fstat(fd)
        if not stat.S_ISREG(before.st_mode) or before.st_nlink != 1 or before.st_size > MAX_BYTES:
            raise ValueError("requires a bounded regular single-link snapshot")
        with os.fdopen(fd, "rb", closefd=False) as stream:
            total = 0
            for number in range(MAX_LINES + 1):
                line = stream.readline(MAX_LINE + 1)
                if not line:
                    break
                if number == MAX_LINES or len(line) > MAX_LINE:
                    raise ValueError("telemetry snapshot bounds exceeded")
                total += len(line)
                if total > MAX_BYTES:
                    raise ValueError("telemetry snapshot too large")
                digest.update(line)
                if not line.lstrip().startswith(b"{"):
                    continue
                value = json.loads(line.decode("utf-8"), object_pairs_hook=unique,
                                   parse_constant=reject_constant, parse_float=finite_float)
                if isinstance(value, dict) and value.get("schema") == schema:
                    recent.append(validator(value))
        after, named = os.fstat(fd), path.stat(follow_symlinks=False)
        identity = lambda s: (s.st_dev, s.st_ino, s.st_size, s.st_mtime_ns, s.st_ctime_ns, s.st_mode, s.st_nlink)
        if identity(before) != identity(after) or identity(after) != identity(named):
            raise ValueError("telemetry snapshot changed while reading")
    finally:
        os.close(fd)
    if not recent:
        raise ValueError("no supported observations")
    return list(recent), digest.hexdigest()


def quantile_upper(metric, numerator=99, denominator=100):
    """Bucket upper bound, NOT a measured/interpolated exact percentile."""
    if not metric or metric["saturated"] or not metric["samples"] or "bucket_counts" not in metric:
        return None
    rank, cumulative = (metric["samples"] * numerator + denominator - 1) // denominator, 0
    for index, count in enumerate(metric["bucket_counts"]):
        cumulative += count
        if cumulative >= rank:
            return BOUNDS_NS[index] if index < len(BOUNDS_NS) else None
    return None


def report(samples, now_ms, max_age_ms=15000, planning_seconds=0):
    uint(now_ms); uint(max_age_ms); uint(planning_seconds)
    if not max_age_ms or not samples:
        raise ValueError("positive freshness budget and observations required")
    samples = [validate(s) for s in samples]
    latest = samples[-1]
    alerts = []
    def alert(rule, severity):
        alerts.append({"rule_id": rule, "severity": severity})
    age = now_ms - latest["observed_at_ms"]
    fresh = 0 <= age <= max_age_ms
    if not fresh:
        alert("OMS_TELEMETRY_CLOCK" if age < 0 else "OMS_TELEMETRY_STALE", "P2")
    if latest["write_poisoned"]:
        alert("OMS_WRITER_POISONED", "P1")
    if not latest["known"]:
        alert("OMS_CAPACITY_UNKNOWN", "P2")
    elif latest["status"] in {"WARNING", "EXCEEDED"}:
        alert("OMS_CAPACITY_" + latest["status"], "P2")
    trend = {"window_ms": None, "bytes_per_second": None, "records_per_second": None, "estimated_headroom_seconds": None}
    # Only contiguous, complete, monotonic samples from the SAME service
    # incarnation and policy. Never connect counters across restart/reset/gaps.
    window = []
    for sample in reversed(samples):
        if (not sample["known"] or not sample.get("service_epoch") or
                sample.get("service_epoch") != latest.get("service_epoch") or
                (sample["max_bytes"], sample["max_records"]) != (latest["max_bytes"], latest["max_records"])):
            break
        if window:
            newer = window[-1]
            delta = newer.get("monotonic_ms", 0) - sample.get("monotonic_ms", 0)
            if delta <= 0 or delta > max_age_ms or sample["bytes"] > newer["bytes"] or sample["records"] > newer["records"]:
                break
        window.append(sample)
    if fresh and len(window) >= 2:
        elapsed = latest["monotonic_ms"] - window[-1]["monotonic_ms"]
        rates = [(latest[k] - window[-1][k]) * 1000 / elapsed for k in ("bytes", "records")]
        estimates = [latest[k] / rate for k, rate in zip(("byte_headroom", "record_headroom"), rates) if rate > 0]
        trend = {"window_ms": elapsed, "bytes_per_second": rates[0], "records_per_second": rates[1],
                 "estimated_headroom_seconds": min(estimates) if estimates else None}
        if planning_seconds and estimates and min(estimates) <= planning_seconds:
            alert("OMS_HEADROOM_PLANNING", "P2")
    if latest.get("pending_bytes", 0) >= latest.get("max_pending_bytes", UINT64_MAX):
        alert("OMS_PENDING_QUEUE_FULL", "P2")
    if latest.get("pending_records", 0) >= latest.get("max_pending_records", UINT64_MAX):
        alert("OMS_PENDING_QUEUE_FULL", "P2")
    if len(window) >= 2 and latest.get("queue_capacity_rejections", 0) > window[-1].get("queue_capacity_rejections", 0):
        alert("OMS_PENDING_QUEUE_REJECTED", "P2")
    latencies = {}
    for name in LATENCIES:
        metric = latest.get(name)
        if metric is not None:
            latencies[name] = {"samples": metric["samples"], "saturated": metric["saturated"],
                               "p99_upper_ns": quantile_upper(metric),
                               "p999_upper_ns": quantile_upper(metric, 999, 1000) if metric["samples"] >= 1000 else None}
            if metric["saturated"]:
                alert("OMS_METRIC_SATURATED", "P2")
    execution = latest.get("execution_metrics")
    if execution is not None:
        # A deliberate terminal/maintenance fence is not necessarily an incident.
        # Export the block gauge; existing writer/recovery evidence owns severity.
        if execution["metrics_saturated"] or any(execution[k]["saturated"] for k in EXECUTION_LATENCIES + EXECUTION_TIMING_EXTENSION if k in execution):
            alert("EXECUTION_METRIC_SATURATED", "P2")
    return {"schema": "heptatrader.oms-operational-report.v1", "fresh": fresh, "sample_age_ms": age,
            "capacity_status": latest["status"], "known": latest["known"], "service_epoch": latest.get("service_epoch"),
            "trend": trend, "latencies": latencies, "execution_present": execution is not None,
            "alerts": alerts, "authorization_effect": "NONE"}


def prometheus(latest, summary):
    # Fixed names, no account/order labels or unbounded reason strings.
    lines = [f"hepta_oms_telemetry_fresh {int(summary['fresh'])}",
             f"hepta_oms_capacity_known {int(latest['known'])}",
             f"hepta_oms_writer_poisoned {int(latest['write_poisoned'])}",
             f"hepta_oms_alerts {len(summary['alerts'])}"]
    if latest["known"]:
        for key in ("bytes", "records", "max_bytes", "max_records", "byte_headroom", "record_headroom", "pending_records"):
            lines.append(f"hepta_oms_{key} {latest[key]}")
    for key in ("pending_bytes", "max_pending_bytes", "max_pending_records", "queue_capacity_rejections"):
        if key in latest:
            lines.append(f"hepta_oms_{key} {latest[key]}")
    for name in LATENCIES:
        value = latest.get(name)
        if not value or value["saturated"] or "bucket_counts" not in value:
            continue
        metric = "hepta_oms_" + name + "_seconds"
        lines.append(f"# TYPE {metric} histogram")
        cumulative = 0
        for i, count in enumerate(value["bucket_counts"]):
            cumulative += count
            bound = str(BOUNDS_NS[i] / 1e9) if i < len(BOUNDS_NS) else "+Inf"
            lines.append(f'{metric}_bucket{{le="{bound}"}} {cumulative}')
        lines += [f"{metric}_count {value['samples']}", f"{metric}_sum {value['total_ns'] / 1e9}"]
    execution = latest.get("execution_metrics")
    lines.append(f"hepta_execution_metrics_present {int(execution is not None)}")
    if execution is not None:
        lines.append(f"hepta_execution_mutation_blocked {int(execution['mutation_blocked'])}")
        lines.append(f"hepta_execution_metrics_saturated {int(execution['metrics_saturated'])}")
        for key in EXECUTION_GAUGES:
            lines.append(f"hepta_execution_{key} {execution[key]}")
        if not execution["metrics_saturated"]:
            lines.append("# TYPE hepta_execution_commands_total counter")
            for op, row in zip(EXECUTION_OPERATIONS, execution["results"]):
                for result, count in zip(EXECUTION_RESULTS, row):
                    lines.append(f'hepta_execution_commands_total{{operation="{op}",result="{result}"}} {count}')
        for operation, name in zip(EXECUTION_OPERATIONS, EXECUTION_LATENCIES):
            lines.append(f'hepta_execution_operation_timing_present{{operation="{operation}"}} {int(name + "_total" in execution)}')
        for name in EXECUTION_LATENCIES + EXECUTION_TIMING_EXTENSION:
            if name not in execution:
                continue  # An older producer did not observe this scope.
            value = execution[name]
            if value["saturated"] or "bucket_counts" not in value:
                continue
            metric = "hepta_execution_" + name + "_seconds"
            lines.append(f"# TYPE {metric} histogram")
            cumulative = 0
            for i, count in enumerate(value["bucket_counts"]):
                cumulative += count
                bound = str(BOUNDS_NS[i] / 1e9) if i < len(BOUNDS_NS) else "+Inf"
                lines.append(f'{metric}_bucket{{le="{bound}"}} {cumulative}')
            lines += [f"{metric}_count {value['samples']}", f"{metric}_sum {value['total_ns'] / 1e9}"]
    return "\n".join(lines) + "\n"


def validate_gateway(sample):
    if not isinstance(sample, dict) or sample.get("schema") != GATEWAY_SCHEMA:
        raise ValueError("unsupported gateway telemetry")
    if sample.get("authorization_effect") != "NONE" or type(sample.get("metrics_saturated")) is not bool:
        raise ValueError("invalid gateway telemetry authority/presence")
    epoch = sample.get("service_epoch")
    if not isinstance(epoch, str) or re.fullmatch(r"[A-Za-z0-9_.:-]{1,128}", epoch) is None:
        raise ValueError("invalid gateway epoch")
    for key in GATEWAY_COUNTERS + GATEWAY_GAUGES + ("observed_at_ms", "monotonic_ms"):
        uint(sample.get(key))
    if not sample["max_pending_connections"]:
        raise ValueError("missing gateway queue bound")
    results = sample.get("results")
    if not isinstance(results, list) or len(results) != len(RESULT_NAMES):
        raise ValueError("invalid fixed gateway result inventory")
    for count in results:
        uint(count)
    for name in GATEWAY_LATENCIES:
        validate_latency(sample.get(name))
    if not sample["metrics_saturated"]:
        total = sample["responses_delivered"] + sample["response_write_failures"]
        if sum(results) != total:
            raise ValueError("gateway response accounting mismatch")
        latency = sample["response_write_latency"]
        if not latency["saturated"] and latency["samples"] != total:
            raise ValueError("gateway delivery measurement mismatch")
    return sample


def gateway_report(samples, now_ms, max_age_ms=15000):
    uint(now_ms); uint(max_age_ms)
    if not max_age_ms or not samples:
        raise ValueError("positive freshness budget required")
    samples = [validate_gateway(s) for s in samples]
    last = samples[-1]
    age = now_ms - last["observed_at_ms"]
    fresh = 0 <= age <= max_age_ms
    alerts = []
    def alert(rule):
        alerts.append({"rule_id": rule, "severity": "P2"})
    if not fresh:
        alert("GATEWAY_TELEMETRY_CLOCK" if age < 0 else "GATEWAY_TELEMETRY_STALE")
    if last["metrics_saturated"]:
        alert("GATEWAY_METRICS_SATURATED")
    if last["pending_connections"] >= last["max_pending_connections"]:
        alert("GATEWAY_QUEUE_SATURATED")
    deltas = None
    if fresh and len(samples) >= 2 and not last["metrics_saturated"]:
        prev = samples[-2]
        interval = last["monotonic_ms"] - prev["monotonic_ms"]
        if (last["service_epoch"] == prev["service_epoch"] and not prev["metrics_saturated"] and
                0 < interval <= max_age_ms and
                all(last[k] >= prev[k] for k in GATEWAY_COUNTERS)):
            deltas = {k: last[k] - prev[k] for k in GATEWAY_COUNTERS}
            if deltas["response_write_failures"]:
                alert("GATEWAY_RESPONSE_WRITE_FAILURE")
            if deltas["queue_backpressure_rejections"] or deltas["owner_backpressure_rejections"]:
                alert("GATEWAY_BACKPRESSURE")
    return {"schema": "heptatrader.gateway-operational-report.v1", "fresh": fresh,
            "sample_age_ms": age, "service_epoch": last["service_epoch"],
            "interval_deltas": deltas,
            "latencies": {k: {"samples": last[k]["samples"],
                "p99_upper_ns": quantile_upper(last[k]),
                "p999_upper_ns": quantile_upper(last[k], 999, 1000) if last[k]["samples"] >= 1000 else None}
                for k in GATEWAY_LATENCIES},
            "alerts": alerts, "authorization_effect": "NONE"}


def gateway_prometheus(latest, summary):
    lines = [f"hepta_gateway_telemetry_fresh {int(summary['fresh'])}",
             f"hepta_gateway_metrics_saturated {int(latest['metrics_saturated'])}"]
    for key in GATEWAY_GAUGES:
        lines += [f"# TYPE hepta_gateway_{key} gauge", f"hepta_gateway_{key} {latest[key]}"]
    if not latest["metrics_saturated"]:
        for key in GATEWAY_COUNTERS:
            lines += [f"# TYPE hepta_gateway_{key}_total counter", f"hepta_gateway_{key}_total {latest[key]}"]
        lines.append("# TYPE hepta_gateway_results_total counter")
        for name, count in zip(RESULT_NAMES, latest["results"]):
            lines.append(f'hepta_gateway_results_total{{status="{name}"}} {count}')
    for key in GATEWAY_LATENCIES:
        metric = latest[key]
        if metric["saturated"] or "bucket_counts" not in metric:
            continue
        name = "hepta_gateway_" + key + "_seconds"
        lines.append(f"# TYPE {name} histogram")
        cumulative = 0
        for index, count in enumerate(metric["bucket_counts"]):
            cumulative += count
            upper = str(BOUNDS_NS[index] / 1e9) if index < len(BOUNDS_NS) else "+Inf"
            lines.append(f'{name}_bucket{{le="{upper}"}} {cumulative}')
        lines += [f"{name}_count {metric['samples']}", f"{name}_sum {metric['total_ns'] / 1e9}"]
    return "\n".join(lines) + "\n"



def _open_metrics_directory(path: Path) -> int:
    """Pin a caller-owned namespace; do not create/chmod host directories."""
    if not path.is_absolute() or ".." in path.parts:
        raise ValueError("absolute metrics directory required")
    flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC
    fd = os.open("/", flags)
    try:
        for index, part in enumerate(path.parts[1:], 1):
            child = os.open(part, flags, dir_fd=fd)
            os.close(fd)
            fd = child
            info = os.fstat(fd)
            final = index == len(path.parts) - 1
            sticky_root = not final and info.st_uid == 0 and info.st_mode & stat.S_ISVTX
            if info.st_uid not in {0, os.geteuid()} or info.st_mode & 0o022 and not sticky_root:
                raise ValueError("untrusted metrics directory")
        info = os.fstat(fd)
        if info.st_uid != os.geteuid() or info.st_mode & 0o022:
            raise ValueError("metrics directory must be privately writable by publisher")
        return fd
    except BaseException:
        os.close(fd)
        raise


def _metric_file_identity(info):
    return (info.st_dev, info.st_ino, info.st_mode, info.st_uid, info.st_gid,
            info.st_nlink, info.st_size, info.st_mtime_ns, info.st_ctime_ns)


def _metric_leaf(directory_fd, name, mode):
    try:
        info = os.stat(name, dir_fd=directory_fd, follow_symlinks=False)
    except FileNotFoundError:
        return None
    if (not stat.S_ISREG(info.st_mode) or info.st_uid != os.geteuid() or
            info.st_nlink != 1 or stat.S_IMODE(info.st_mode) != mode):
        raise ValueError("unsafe metrics output or lock")
    return _metric_file_identity(info)


def publish_metrics(directory: Path, kind: str, text: str) -> None:
    """Replace one fixed textfile atomically under a nonblocking writer lock.

    Failure after replace/fsync is uncertain: never claim the old file survived.
    This is a telemetry output, not journal state or a trading-authority receipt.
    """
    if kind not in {"oms", "gateway"} or not isinstance(text, str):
        raise ValueError("invalid metrics publication")
    data = text.encode("utf-8")
    if not data or len(data) > 1 << 20 or not data.endswith(b"\n"):
        raise ValueError("invalid metrics output bound")
    directory_fd = _open_metrics_directory(directory)
    lock_fd = temporary_fd = None
    temporary = None
    try:
        name, lock_name = f"hepta_{kind}.prom", f".hepta_{kind}.lock"
        lock_fd = os.open(lock_name, os.O_RDWR | os.O_CREAT | os.O_NONBLOCK |
                          os.O_NOFOLLOW | os.O_CLOEXEC, 0o600, dir_fd=directory_fd)
        locked = _metric_leaf(directory_fd, lock_name, 0o600)
        if locked != _metric_file_identity(os.fstat(lock_fd)):
            raise ValueError("metrics lock identity changed")
        fcntl.flock(lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        previous = _metric_leaf(directory_fd, name, 0o644)
        temporary = f".hepta_{kind}.{secrets.token_hex(16)}.tmp"
        temporary_fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL |
                               os.O_NOFOLLOW | os.O_CLOEXEC, 0o600, dir_fd=directory_fd)
        os.fchmod(temporary_fd, 0o644)
        offset = 0
        while offset < len(data):
            written = os.write(temporary_fd, data[offset:])
            if written <= 0:
                raise OSError("short metrics write")
            offset += written
        os.fsync(temporary_fd)
        if _metric_leaf(directory_fd, temporary, 0o644) != _metric_file_identity(os.fstat(temporary_fd)):
            raise ValueError("metrics temporary identity changed")
        check_fd = _open_metrics_directory(directory)
        try:
            old, current = os.fstat(directory_fd), os.fstat(check_fd)
            if (old.st_dev, old.st_ino) != (current.st_dev, current.st_ino):
                raise ValueError("metrics namespace changed")
        finally:
            os.close(check_fd)
        if (_metric_leaf(directory_fd, name, 0o644) != previous or
                _metric_leaf(directory_fd, lock_name, 0o600) != locked):
            raise ValueError("metrics output or lock changed")
        os.replace(temporary, name, src_dir_fd=directory_fd, dst_dir_fd=directory_fd)
        temporary = None
        os.fsync(directory_fd)
    finally:
        # Do not unlink the lock: another process may already hold its inode.
        # Never remove a substituted temporary or an already published output.
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


def collection_metrics(kind: str, now_ms: int, latest=None) -> str:
    """Collection success is not runtime health; timestamps expose dead collectors."""
    if kind not in {"oms", "gateway"}:
        raise ValueError("invalid metrics kind")
    uint(now_ms)
    prefix = f"hepta_{kind}"
    lines = [f"# TYPE {prefix}_collector_success gauge",
             f"{prefix}_collector_success {int(latest is not None)}",
             f"# TYPE {prefix}_collector_timestamp_seconds gauge",
             f"{prefix}_collector_timestamp_seconds {now_ms // 1000}.{now_ms % 1000:03d}"]
    if latest is None:
        # Drop the old healthy series on malformed/missing input. Unknown
        # positions, latency or capacity must never become synthetic zeros.
        lines.append(f"{prefix}_telemetry_fresh 0")
    else:
        observed = uint(latest["observed_at_ms"])
        lines += [f"# TYPE {prefix}_sample_timestamp_seconds gauge",
                  f"{prefix}_sample_timestamp_seconds {observed // 1000}.{observed % 1000:03d}"]
    return "\n".join(lines) + "\n"


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--kind", choices=("oms", "gateway"), default="oms")
    parser.add_argument("--now-ms", type=int, default=None)
    parser.add_argument("--max-age-ms", type=int, default=15000)
    parser.add_argument("--planning-seconds", type=int, default=0, help="explicit planning horizon; 0 disables forecast alert")
    parser.add_argument("--format", choices=("json", "prometheus"), default="json")
    parser.add_argument("--output-dir", type=Path, default=None,
                        help="atomically publish hepta_<kind>.prom in an existing trusted directory")
    args = parser.parse_args(argv)
    if args.output_dir is not None and (args.format != "prometheus" or args.now_ms is not None):
        parser.error("publication requires --format prometheus and the real wall clock")
    now = int(time.time() * 1000) if args.now_ms is None else args.now_ms
    try:
        samples, digest = read_samples(args.input, args.kind)
        if args.kind == "gateway":
            if args.planning_seconds:
                raise ValueError("OMS planning does not apply to gateway")
            summary = gateway_report(samples, now, args.max_age_ms)
            render = gateway_prometheus
        else:
            summary = report(samples, now, args.max_age_ms, args.planning_seconds)
            render = prometheus
        summary["input_sha256"] = digest
        output = render(samples[-1], summary) if args.format == "prometheus" else json.dumps(summary, sort_keys=True, allow_nan=False)
        result = 1 if summary["alerts"] else 0
        if args.output_dir is not None:
            output += collection_metrics(args.kind, now, samples[-1])
    except (OSError, ValueError, TypeError, OverflowError, RecursionError):
        # Do not print attacker-controlled lines, secret values or paths.
        print("GATEWAY_TELEMETRY_INPUT_INVALID" if args.kind == "gateway" else "OMS_TELEMETRY_INPUT_INVALID", file=sys.stderr)
        if args.output_dir is None:
            return 2
        result = 2
        output = collection_metrics(args.kind, now)
    if args.output_dir is not None:
        try:
            publish_metrics(args.output_dir, args.kind, output)
        except (OSError, ValueError):
            print("METRICS_PUBLICATION_FAILED", file=sys.stderr)
            return 2
    if result != 2:
        print(output, end="\n" if args.format == "json" else "")
    return result


if __name__ == "__main__":
    raise SystemExit(main())
