#!/usr/bin/env python3
"""Read-only OMS telemetry report/Prometheus text; no network or trading action.

Input is a consistent regular-file export of ONE daemon's JSON log messages.
This is not a log follower, journal replay validator, or notification daemon.
"""
from __future__ import annotations

import argparse
from collections import deque
import hashlib
import json
import math
import os
from pathlib import Path
import re
import stat
import sys
import time

SCHEMA = "heptatrader.oms-capacity.v1"
BOUNDS_NS = (1000, 10000, 100000, 1000000, 5000000, 10000000,
             50000000, 100000000, 1000000000, 10000000000)
LATENCIES = ("append_latency", "data_sync_latency", "replay_validation_latency")
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
    return metric


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
    epoch = sample.get("service_epoch")
    if epoch is not None and (not isinstance(epoch, str) or re.fullmatch(r"[A-Za-z0-9_.:-]{1,128}", epoch) is None):
        raise ValueError("invalid service epoch")
    uint(sample.get("monotonic_ms", 0))
    for name in LATENCIES:
        metric = sample.get(name)
        if metric is not None:
            validate_latency(metric)
    return sample


def read_samples(path: Path, *, schema=SCHEMA, validator=validate):
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
    latencies = {}
    for name in LATENCIES:
        metric = latest.get(name)
        if metric is not None:
            latencies[name] = {"samples": metric["samples"], "saturated": metric["saturated"],
                               "p99_upper_ns": quantile_upper(metric),
                               "p999_upper_ns": quantile_upper(metric, 999, 1000) if metric["samples"] >= 1000 else None}
            if metric["saturated"]:
                alert("OMS_METRIC_SATURATED", "P2")
    return {"schema": "heptatrader.oms-operational-report.v1", "fresh": fresh, "sample_age_ms": age,
            "capacity_status": latest["status"], "known": latest["known"], "service_epoch": latest.get("service_epoch"),
            "trend": trend, "latencies": latencies, "alerts": alerts, "authorization_effect": "NONE"}


def prometheus(latest, summary):
    # Fixed names, no account/order labels or unbounded reason strings.
    lines = [f"hepta_oms_telemetry_fresh {int(summary['fresh'])}",
             f"hepta_oms_capacity_known {int(latest['known'])}",
             f"hepta_oms_writer_poisoned {int(latest['write_poisoned'])}",
             f"hepta_oms_alerts {len(summary['alerts'])}"]
    if latest["known"]:
        for key in ("bytes", "records", "max_bytes", "max_records", "byte_headroom", "record_headroom", "pending_records"):
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
    return "\n".join(lines) + "\n"


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--now-ms", type=int, default=None)
    parser.add_argument("--max-age-ms", type=int, default=15000)
    parser.add_argument("--planning-seconds", type=int, default=0, help="explicit planning horizon; 0 disables forecast alert")
    parser.add_argument("--format", choices=("json", "prometheus"), default="json")
    args = parser.parse_args(argv)
    try:
        samples, digest = read_samples(args.input)
        summary = report(samples, int(time.time() * 1000) if args.now_ms is None else args.now_ms,
                         args.max_age_ms, args.planning_seconds)
        summary["input_sha256"] = digest
        print(prometheus(samples[-1], summary) if args.format == "prometheus" else
              json.dumps(summary, sort_keys=True, allow_nan=False), end="\n" if args.format == "json" else "")
        return 1 if summary["alerts"] else 0
    except (OSError, ValueError, TypeError, OverflowError, RecursionError):
        # Do not print attacker-controlled lines, secret values or paths.
        print("OMS_TELEMETRY_INPUT_INVALID", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
