#!/usr/bin/env python3
"""Apply deadline resources and observe the real services without trading RPCs.

MemoryLow is reclaim protection, not an exclusive RAM or GPU reservation.
Original research admission rules and registered outcomes remain untouched.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
from pathlib import Path
import subprocess
import time

MIB = 1024 ** 2
GIB = 1024 ** 3
PROFILE = {
    "heptatrader-gateway.service": {"CPUWeight": "1000", "MemoryLow": str(64 * MIB)},
    "heptatrader-execution.service": {"CPUWeight": "1000", "MemoryLow": str(64 * MIB)},
    "heptatrader-paper-driver.service": {"CPUWeight": "1000", "MemoryLow": str(128 * MIB)},
    "ibgateway.service": {"CPUWeight": "1000", "MemoryLow": str(512 * MIB)},
    "heptatrader-current100k-forward-predictor.service": {
        "CPUWeight": "500", "CPUQuota": "400%", "MemoryLow": str(768 * MIB)},
    "heptatrader-laya-evaluator.service": {
        "CPUWeight": "500", "CPUQuota": "400%", "MemoryLow": str(768 * MIB)},
    "heptatrader-laya-feed.service": {
        "CPUWeight": "500", "CPUQuota": "100%", "MemoryLow": str(64 * MIB)},
    "heptatrader-learning-source-health.service": {
        "CPUWeight": "200", "CPUQuota": "100%", "MemoryLow": str(128 * MIB)},
}
OBSERVE_UNITS = tuple(PROFILE) + ("heptatrader-laya-trainer.service",)
PROPERTIES = ("MainPID", "ActiveState", "SubState", "ControlGroup", "CPUWeight",
              "CPUQuotaPerSecUSec", "MemoryCurrent", "MemoryLow", "MemoryMax",
              "MemorySwapCurrent", "NRestarts")


def run(argv):
    result = subprocess.run(argv, text=True, capture_output=True, timeout=20)
    if result.returncode:
        raise RuntimeError(f"{argv}: {result.stderr.strip()}")
    return result.stdout


def show(unit, *, system=False):
    argv = ["systemctl"] + ([] if system else ["--user"])
    raw = run(argv + ["show", unit] + [arg for name in PROPERTIES for arg in ("-p", name)])
    return dict(line.split("=", 1) for line in raw.splitlines() if "=" in line)


def counters(path):
    try:
        return {k.rstrip(":"): int(v) for k, v in
                (line.split() for line in path.read_text().splitlines())}
    except (OSError, ValueError):
        return None


def psi(path):
    try:
        return {parts[0]: {k: float(v) if k != "total" else int(v)
                for k, v in (p.split("=") for p in parts[1:])}
                for parts in (line.split() for line in path.read_text().splitlines())}
    except (OSError, ValueError):
        return None


def snapshot():
    value = {"at_ms": time.time_ns() // 1000000, "monotonic_ns": time.monotonic_ns(),
             "utc": dt.datetime.now(dt.timezone.utc).isoformat(), "units": {},
             "host_pressure": {r: psi(Path("/proc/pressure") / r) for r in ("cpu", "memory", "io")},
             "broker_calls": 0, "counter_semantics": "CUMULATIVE_WITH_CGROUP_INODE_AND_PID"}
    for unit in OBSERVE_UNITS:
        props = show(unit)
        group = props.get("ControlGroup")
        row = {"properties": props}
        if group:
            cg = Path("/sys/fs/cgroup") / group.lstrip("/")
            try:
                st = cg.stat()
                row["cgroup_identity"] = [st.st_dev, st.st_ino]
                row["cpu"] = counters(cg / "cpu.stat")
                row["memory_events"] = counters(cg / "memory.events")
                row["memory_pressure"] = psi(cg / "memory.pressure")
                row["io_pressure"] = psi(cg / "io.pressure")
                row["cpu_max"] = (cg / "cpu.max").read_text().strip()
                row["effective_memory_low"] = int((cg / "memory.low").read_text())
            except OSError:
                row["cgroup_disappeared_during_sample"] = True
        pid = int(props.get("MainPID") or 0)
        if pid:
            try:
                before = (Path("/proc") / str(pid) / "stat").read_text().rsplit(")", 1)[1].split()[19]
                io = counters(Path("/proc") / str(pid) / "io")
                after = (Path("/proc") / str(pid) / "stat").read_text().rsplit(")", 1)[1].split()[19]
                if before == after:
                    row["process_identity"] = [pid, before]
                    row["process_io"] = io
            except OSError:
                row["process_disappeared_during_sample"] = True
        value["units"][unit] = row
    return value


def save(path, obj):
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    temporary = path.with_name(path.name + ".tmp")
    with temporary.open("w") as stream:
        json.dump(obj, stream, sort_keys=True)
        stream.write("\n")
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temporary, path)


def apply(output):
    """Use normal systemd properties; restart no process and preserve a rollback receipt."""
    before = snapshot()
    uid = os.getuid()
    parents = [(True, "user.slice"), (True, f"user-{uid}.slice"),
               (True, f"user@{uid}.service"), (False, "app.slice")]
    actions = [(system, unit, {"MemoryLow": str(3 * GIB)}) for system, unit in parents]
    actions += [(False, unit, values) for unit, values in PROFILE.items()]
    receipt = {"profile": "HEPTATRADER_DEADLINE_RESOURCES_V1", "before": before,
               "actions": [], "restart_requested": False, "trade_calls": 0,
               "exclusive_capacity_guaranteed": False, "admission_rules_changed": False}
    # Publish the original properties before the first mutation, including ancestry.
    for system, unit, values in actions:
        old = show(unit, system=system)
        restore = {}
        for key in values:
            if key == "CPUQuota":
                group = old.get("ControlGroup")
                if not group:
                    raise RuntimeError(f"Cannot bind current quota for {unit}")
                quota, period = (Path("/sys/fs/cgroup") / group.lstrip("/") / "cpu.max").read_text().split()
                restore[key] = "" if quota == "max" else f"{int(quota) / int(period) * 100:g}%"
            else:
                restore[key] = "" if old.get(key) == "[not set]" else old[key]
        receipt["actions"].append({"system": system, "unit": unit,
                                   "desired": values, "restore": restore, "applied": False})
    save(output, receipt)
    try:
        for action in receipt["actions"]:
            argv = (["sudo", "-n", "systemctl"] if action["system"] else ["systemctl", "--user"])
            run(argv + ["set-property", action["unit"]] +
                [f"{key}={value}" for key, value in action["desired"].items()])
            action["applied"] = True
            save(output, receipt)
        receipt["after"] = snapshot()
        receipt["parent_readback"] = {unit: show(unit, system=system) for system, unit in parents}
        for unit, desired in PROFILE.items():
            row = receipt["after"]["units"][unit]
            if row["properties"]["CPUWeight"] != desired["CPUWeight"]:
                raise RuntimeError(f"CPU weight readback failed: {unit}")
            if int(row["properties"]["MemoryLow"]) != int(desired["MemoryLow"]):
                raise RuntimeError(f"Memory protection readback failed: {unit}")
            if "effective_memory_low" in row and row["effective_memory_low"] != int(desired["MemoryLow"]):
                raise RuntimeError(f"Kernel memory protection readback failed: {unit}")
            if "CPUQuota" in desired:
                wanted = float(desired["CPUQuota"][:-1])
                if "cpu_max" in row:
                    quota, period = row["cpu_max"].split()
                    valid = quota != "max" and int(quota) / int(period) * 100 == wanted
                else:
                    valid = row["properties"]["CPUQuotaPerSecUSec"] in (f"{wanted / 100:g}s", f"{wanted / 100:.6f}s")
                if not valid:
                    raise RuntimeError(f"CPU quota readback failed: {unit}")
        for unit, props in receipt["parent_readback"].items():
            if int(props["MemoryLow"]) != 3 * GIB:
                raise RuntimeError(f"Parent memory protection readback failed: {unit}")
        receipt["verified"] = True
    except Exception as exc:
        receipt["error"] = str(exc)
        receipt["rollback_errors"] = []
        for action in reversed(receipt["actions"]):
            if not action["applied"]:
                continue
            argv = (["sudo", "-n", "systemctl"] if action["system"] else ["systemctl", "--user"])
            try:
                run(argv + ["set-property", action["unit"]] +
                    [f"{key}={value}" for key, value in action["restore"].items()])
                action["rolled_back"] = True
            except Exception as rollback_error:
                receipt["rollback_errors"].append(str(rollback_error))
        save(output, receipt)
        raise
    save(output, receipt)
    return receipt


def interval(previous, current):
    duration = (current["monotonic_ns"] - previous["monotonic_ns"]) / 1e9
    result = {"seconds": duration, "units": {}}
    for unit, new in current["units"].items():
        old = previous["units"].get(unit, {})
        row = {}
        if new.get("cgroup_identity") is not None and new.get("cgroup_identity") == old.get("cgroup_identity"):
            for key in ("usage_usec", "nr_periods", "nr_throttled", "throttled_usec"):
                a, b = old.get("cpu") or {}, new.get("cpu") or {}
                if key in a and key in b and b[key] >= a[key]:
                    row[key] = b[key] - a[key]
        if new.get("process_identity") is not None and new.get("process_identity") == old.get("process_identity"):
            for key in ("rchar", "wchar", "read_bytes", "write_bytes"):
                a, b = old.get("process_io") or {}, new.get("process_io") or {}
                if key in a and key in b and b[key] >= a[key]:
                    row[key] = b[key] - a[key]
        if row:
            result["units"][unit] = row
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--samples", type=int, default=1)
    parser.add_argument("--interval", type=float, default=30)
    args = parser.parse_args()
    if args.apply:
        if args.output.exists():
            parser.error("Apply receipt already exists; inspect it instead of replaying")
        result = apply(args.output)
        print(json.dumps({"output": str(args.output), "verified": result["verified"]}))
        return
    if not 1 <= args.samples <= 2000 or not 1 <= args.interval <= 300:
        parser.error("Bounded samples/interval required")
    args.output.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    previous = None
    with args.output.open("x") as stream:
        for index in range(args.samples):
            current = snapshot()
            if previous is not None:
                current["interval"] = interval(previous, current)
            stream.write(json.dumps(current, sort_keys=True) + "\n")
            stream.flush()
            previous = current
            if index + 1 < args.samples:
                time.sleep(args.interval)
    print(json.dumps({"output": str(args.output), "samples": args.samples}))


if __name__ == "__main__":
    main()
