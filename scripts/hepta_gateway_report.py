#!/usr/bin/env python3
"""Read-only Gateway telemetry from a bounded stable local log export.

No socket, session token, broker access or notification side effect. Exit 1
means observed alerts, 2 means invalid/missing evidence, never healthy zero.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import re
import sys
import time

import hepta_oms_report as common

SCHEMA = "heptatrader.gateway-observation.v1"
STATUSES = ("ok", "permission_denied", "invalid_tool", "rejected", "duplicate", "uncertain", "error", "unknown")
LATENCIES = ("ingress_latency", "queue_latency", "dispatch_latency", "reply_latency")
GAUGES = ("pending_connections", "active_requests", "ready_owners", "worker_limit", "pending_limit")
COUNTERS = ("queue_rejections", "owner_rejections", "deadline_rejections", "cancelled_requests",
            "response_attempts", "response_writes", "response_write_failures")


def validate(sample):
    if not isinstance(sample, dict) or sample.get("schema") != SCHEMA or sample.get("authorization_effect") != "NONE":
        raise ValueError("unsupported gateway observation")
    epoch = sample.get("service_epoch")
    if not isinstance(epoch, str) or re.fullmatch(r"[A-Za-z0-9_.:-]{1,128}", epoch) is None:
        raise ValueError("invalid gateway incarnation")
    for field in GAUGES + COUNTERS + ("observed_at_ms", "monotonic_ms"):
        common.uint(sample.get(field))
    if not 1 <= sample["worker_limit"] <= 64 or not 1 <= sample["pending_limit"] <= 1024:
        raise ValueError("invalid configured server limits")
    if type(sample.get("running")) is not bool or type(sample.get("saturated")) is not bool:
        raise ValueError("invalid observation presence")
    counts = sample.get("result_counts")
    if not isinstance(counts, dict) or set(counts) != set(STATUSES):
        raise ValueError("invalid fixed result inventory")
    for v in counts.values():
        common.uint(v)
    for name in LATENCIES:
        common.validate_latency(sample.get(name))
    if not sample["saturated"]:
        if sum(counts.values()) != sample["response_attempts"] or sample["response_writes"] + sample["response_write_failures"] != sample["response_attempts"]:
            raise ValueError("reply accounting mismatch")
        reply = sample["reply_latency"]
        if not reply["saturated"] and reply["samples"] != sample["response_attempts"]:
            raise ValueError("reply sample mismatch")
    queue, dispatch = sample["queue_latency"], sample["dispatch_latency"]
    if not queue["saturated"] and not dispatch["saturated"] and queue["samples"] != dispatch["samples"]:
        raise ValueError("dispatch queue accounting mismatch")
    return sample


def report(samples, now_ms, max_age_ms=15000):
    common.uint(now_ms); common.uint(max_age_ms)
    if not samples or not max_age_ms:
        raise ValueError("freshness budget and actual observations required")
    values = [validate(v) for v in samples]
    latest = values[-1]
    age = now_ms - latest["observed_at_ms"]
    fresh = 0 <= age <= max_age_ms
    alerts = []
    def alert(rule):
        alerts.append({"rule_id": rule, "severity": "P2"})
    if not fresh:
        alert("GATEWAY_TELEMETRY_CLOCK" if age < 0 else "GATEWAY_TELEMETRY_STALE")
    if not latest["running"]:
        alert("GATEWAY_NOT_RUNNING")
    if latest["pending_connections"] >= latest["pending_limit"]:
        alert("GATEWAY_PENDING_SATURATED")
    if latest["active_requests"] >= latest["worker_limit"]:
        alert("GATEWAY_WORKERS_SATURATED")
    if latest["saturated"] or any(latest[n]["saturated"] for n in LATENCIES):
        alert("GATEWAY_METRIC_SATURATED")
    delta = None
    if fresh and len(values) >= 2:
        previous = values[-2]
        elapsed = latest["monotonic_ms"] - previous["monotonic_ms"]
        if (not previous["saturated"] and not latest["saturated"]
                and previous["service_epoch"] == latest["service_epoch"]
                and 0 < elapsed <= max_age_ms
                and all(latest[n] >= previous[n] for n in COUNTERS)
                and all(latest["result_counts"][n] >= previous["result_counts"][n] for n in STATUSES)
                and all(latest[n] == previous[n] for n in ("worker_limit", "pending_limit"))):
            delta = {n: latest[n] - previous[n] for n in COUNTERS}
            delta["window_ms"] = elapsed
            if delta["response_write_failures"]:
                alert("GATEWAY_REPLY_WRITE_FAILURE")
            if delta["queue_rejections"] or delta["owner_rejections"]:
                alert("GATEWAY_BACKPRESSURE")
    latencies = {n: {"samples": latest[n]["samples"],
                     "p99_upper_ns": common.quantile_upper(latest[n]),
                     "p999_upper_ns": common.quantile_upper(latest[n], 999, 1000) if latest[n]["samples"] >= 1000 else None}
                 for n in LATENCIES}
    return {"schema": "heptatrader.gateway-report.v1", "fresh": fresh, "sample_age_ms": age,
            "service_epoch": latest["service_epoch"], "interval": delta,
            "latencies": latencies, "alerts": alerts, "authorization_effect": "NONE"}


def prometheus(latest, summary):
    # Only reviewed static identifiers are emitted, never source labels or values.
    lines = ["# TYPE hepta_gateway_telemetry_fresh gauge", f"hepta_gateway_telemetry_fresh {int(summary['fresh'])}"]
    for name in GAUGES:
        lines.extend((f"# TYPE hepta_gateway_{name} gauge", f"hepta_gateway_{name} {latest[name]}"))
    lines.extend(("# TYPE hepta_gateway_running gauge", f"hepta_gateway_running {int(latest['running'])}"))
    if not latest["saturated"]:
        for name in COUNTERS:
            lines.extend((f"# TYPE hepta_gateway_{name}_total counter", f"hepta_gateway_{name}_total {latest[name]}"))
        lines.append("# TYPE hepta_gateway_results_total counter")
        lines.extend(f'hepta_gateway_results_total{{status="{status}"}} {latest["result_counts"][status]}' for status in STATUSES)
    for name in LATENCIES:
        value = latest[name]
        if value["saturated"] or "bucket_counts" not in value:
            continue
        metric = "hepta_gateway_" + name + "_seconds"
        lines.append(f"# TYPE {metric} histogram")
        cumulative = 0
        for i, count in enumerate(value["bucket_counts"]):
            cumulative += count
            bound = str(common.BOUNDS_NS[i] / 1e9) if i < len(common.BOUNDS_NS) else "+Inf"
            lines.append(f'{metric}_bucket{{le="{bound}"}} {cumulative}')
        lines.extend((f"{metric}_count {value['samples']}", f"{metric}_sum {value['total_ns'] / 1e9}"))
    return "\n".join(lines) + "\n"


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--now-ms", type=int)
    parser.add_argument("--max-age-ms", type=int, default=15000)
    parser.add_argument("--format", choices=("json", "prometheus"), default="json")
    args = parser.parse_args(argv)
    try:
        samples, digest = common.read_samples(args.input, schema=SCHEMA, validator=validate)
        summary = report(samples, int(time.time() * 1000) if args.now_ms is None else args.now_ms, args.max_age_ms)
        summary["input_sha256"] = digest
        print(prometheus(samples[-1], summary) if args.format == "prometheus" else json.dumps(summary, sort_keys=True),
              end="" if args.format == "prometheus" else "\n")
        return 1 if summary["alerts"] else 0
    except (OSError, ValueError, TypeError, OverflowError, RecursionError):
        print("GATEWAY_TELEMETRY_INPUT_INVALID", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
