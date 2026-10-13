"""Tiny disposable Linux cgroup failure/recovery proof, never a training worker.

This process is launched ONLY inside a new transient user systemd unit.
Parent records actual kernel counters while a bounded child is OOM-killed,
then a DIFFERENT unit proves fresh recovery with no swap usage. No broker,
model, GPU, source writer, existing service or production Guard mutation.
"""
from __future__ import annotations
import argparse
import json
import os
from pathlib import Path
import signal
import stat
import subprocess
import sys
import time

from historical_training.common import EvidenceError

SCHEMA = "heptatrader.train-v2-swapless-fault-recovery.v1"
MIB = 1024 * 1024
GIB = 1024 * MIB
MODES = {
    "fault": {"max": 128 * MIB, "high": 128 * MIB,
              "child_mib": 256, "expected_oom": True},
    "recover": {"max": 6 * GIB, "high": 5 * GIB,
                "child_mib": 64, "expected_oom": False},
}
PREFIX = "ht-trainv2-cg-"


def _integer(raw):
    value = raw.strip()
    if not value.isdecimal():
        raise EvidenceError("FAULT_CGROUP_NOT_NUMERIC")
    return int(value)


def _cgroup():
    """Only the current transient unit under THIS authenticated user slice."""
    lines = Path("/proc/self/cgroup").read_text().splitlines()
    roots = [row[3:] for row in lines if row.startswith("0::/")]
    if len(roots) != 1:
        raise EvidenceError("FAULT_NOT_IN_UNIFIED_CGROUP")
    relative = Path(roots[0])
    if (not relative.is_absolute() or ".." in relative.parts
            or not relative.name.startswith(PREFIX)
            or not relative.name.endswith(".service")
            or "/app.slice/" not in str(relative)
            or not str(relative).startswith("/user.slice/")):
        raise EvidenceError("FAULT_NOT_IN_NEW_ISOLATED_SERVICE")
    root = Path("/sys/fs/cgroup") / str(relative).lstrip("/")
    if root.is_symlink() or not root.is_dir():
        raise EvidenceError("FAULT_CGROUP_KERNEL_PATH_INVALID")
    return relative, root


def _cgroup_value(root, name):
    file = root / name
    if file.is_symlink() or not file.is_file():
        raise EvidenceError("FAULT_CGROUP_KERNEL_FILE_MISSING:" + name)
    return file.read_text().strip()


def _events(root):
    pairs = {}
    for line in _cgroup_value(root, "memory.events").splitlines():
        key, _, value = line.partition(" ")
        pairs[key] = _integer(value)
    if "oom_kill" not in pairs or "oom" not in pairs:
        raise EvidenceError("FAULT_MEMORY_EVENTS_MISSING")
    return pairs


def _kernel_limits(root, mode):
    cap = MODES[mode]
    output = {name: _integer(_cgroup_value(root, name))
              for name in ("memory.max", "memory.high",
                           "memory.swap.max", "memory.swap.current",
                           "memory.current", "pids.max", "memory.oom.group")}
    cpu = _cgroup_value(root, "cpu.max")
    if (output["memory.max"] != cap["max"]
            or output["memory.high"] != cap["high"]
            or output["memory.swap.max"] != 0
            or output["memory.swap.current"] != 0
            or output["memory.oom.group"] != 0
            or not 1 <= output["pids.max"] <= 32
            or cpu != "100000 100000"):
        raise EvidenceError("FAULT_KERNEL_RESOURCE_BOUND_MISMATCH")
    return {**output, "cpu.max": cpu}


def _child(mib):
    # All code executes inside the same bounded cgroup, with no other service
    # touched. Explicit page writes actually charge anonymous resident memory.
    code = (
        "import sys\n"
        "n=int(sys.argv[1])*1048576\n"
        "x=bytearray(n)\n"
        "for i in range(0,n,4096): x[i]=1\n"
        "print('CHILD_TOUCHED_BYTES', n, flush=True)\n"
    )
    try:
        return subprocess.run(
            [sys.executable, "-I", "-B", "-c", code, str(mib)],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
            timeout=8, check=False)
    except subprocess.TimeoutExpired:
        # Do not erase a failed resource experiment. It must still receive
        # a durable FAIL_PRESERVE receipt with unchanged actual kernel counters.
        return subprocess.CompletedProcess(
            args=["bounded-child-timeout"], returncode=124,
            stdout="", stderr="FAULT_CHILD_TIMEOUT_NO_OOM_ATTESTATION")


def probe(mode):
    if mode not in MODES:
        raise EvidenceError("FAULT_UNREGISTERED_MODE")
    unit, root = _cgroup()
    before = _kernel_limits(root, mode)
    first = _events(root)
    child = _child(MODES[mode]["child_mib"])
    after = _kernel_limits(root, mode)
    last = _events(root)
    delta = last["oom_kill"] - first["oom_kill"]
    max_peak_raw = _cgroup_value(root, "memory.peak")
    swap_peak_raw = _cgroup_value(root, "memory.swap.peak")
    mem_peak, swap_peak = _integer(max_peak_raw), _integer(swap_peak_raw)
    if mode == "fault":
        correct = (child.returncode == -signal.SIGKILL and delta >= 1
                   and last["oom"] > first["oom"])
    else:
        correct = (child.returncode == 0 and delta == 0
                   and "CHILD_TOUCHED_BYTES" in child.stdout
                   and mem_peak <= MODES[mode]["max"])
    if swap_peak != 0:
        correct = False
    report = {
        "schema": SCHEMA, "mode": mode,
        "observed_at_ms": time.time_ns() // 1_000_000,
        "unit_path": str(unit),
        "unit_pid": os.getpid(),
        "actual_kernel_before": before,
        "actual_kernel_after": after,
        "memory_events_before": first,
        "memory_events_after": last,
        "actual_oom_kill_delta": delta,
        "memory_peak_bytes": mem_peak,
        "memory_swap_peak_bytes": swap_peak,
        "child_requested_mib": MODES[mode]["child_mib"],
        "child_returncode": child.returncode,
        "child_timeout": child.returncode == 124,
        "child_stdout_marker": "CHILD_TOUCHED_BYTES" in child.stdout,
        "expected_isolated_child_oom": MODES[mode]["expected_oom"],
        "actual_kernel_verdict": "PASS" if correct else "FAIL_PRESERVE",
        "original_training_guard_changed": False,
        "source_admission_changed": False,
        "gpu_used": False,
        "model_loaded": False,
        "new_source_obtained": False,
        "broker_queries_sent": 0,
        "broker_orders_sent": 0,
        "automatic_training": False,
        "trade_authority": False,
    }
    return report


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--mode", choices=tuple(MODES), required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    os.umask(0o077)
    # Ensure failures are durable, not interpreted as implicit success.
    output = args.output
    if output.exists() or output.is_symlink():
        raise EvidenceError("FAULT_IMMUTABLE_NEW_OUTPUT_REQUIRED")
    result = probe(args.mode)
    output.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    raw = (json.dumps(result, sort_keys=True, indent=2) + "\n").encode()
    fd = os.open(output, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, "wb") as stream:
        stream.write(raw)
        stream.flush()
        os.fsync(stream.fileno())
    folder = os.open(output.parent, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        os.fsync(folder)
    finally:
        os.close(folder)
    print(json.dumps({
        "mode": args.mode,
        "result": result["actual_kernel_verdict"],
        "isolated_child_oom_kills": result["actual_oom_kill_delta"],
        "actual_swap_peak_bytes": result["memory_swap_peak_bytes"],
        "original_guard_modified": False,
        "broker_orders_sent": 0,
    }, sort_keys=True), flush=True)
    return 0 if result["actual_kernel_verdict"] == "PASS" else 2


if __name__ == "__main__":
    raise SystemExit(main())
