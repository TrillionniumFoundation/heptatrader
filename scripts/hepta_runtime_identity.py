#!/usr/bin/env python3
"""Read-only configured and process-bound runtime identities.

A configured path is never treated as proof of the executable currently loaded.
No broker, control, service mutation, or trading operation is performed.
"""
from __future__ import annotations
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import shlex
import stat
import subprocess
import time

import activate_prepared_driver as activation
import install_decision_attribution as attribution

UNITS = (
    "heptatrader-execution.service",
    "heptatrader-paper-driver.service",
    "heptatrader-crypto-driver.service",
    "heptatrader-laya-trainer.service",
    "heptatrader-laya-evaluator.service",
    "heptatrader-laya-feed.service",
    "heptatrader-learning-source-health.service",
    "heptatrader-pnl.service",
    "heptatrader-original-price-observer.service",
)
MAX_EXECUTABLE_BYTES = 256 * 1024 * 1024


def executable_identity(path, *, process_link=False):
    flags = os.O_RDONLY | os.O_CLOEXEC
    if not process_link:
        flags |= os.O_NOFOLLOW
    fd = os.open(path, flags)
    try:
        before = os.fstat(fd)
        if not stat.S_ISREG(before.st_mode) or not 0 < before.st_size <= MAX_EXECUTABLE_BYTES:
            raise ValueError("EXECUTABLE_FILE_BOUND")
        checksum = hashlib.sha256()
        remaining = before.st_size
        while remaining:
            raw = os.read(fd, min(1024 * 1024, remaining))
            if not raw:
                raise ValueError("EXECUTABLE_SHORT_READ")
            checksum.update(raw); remaining -= len(raw)
        after = os.fstat(fd)
        if ((before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns, before.st_ctime_ns)
                != (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns, after.st_ctime_ns)):
            raise ValueError("EXECUTABLE_CHANGED_DURING_INSPECTION")
        return {"sha256": checksum.hexdigest(), "bytes": before.st_size,
                "device": before.st_dev, "inode": before.st_ino}
    finally:
        os.close(fd)


def configured_executable(exec_start):
    paths = re.findall(r"(?:^|[\s{;])path=([^\s;]+)", exec_start)
    if len(paths) != 1 or not Path(paths[0]).is_absolute():
        raise ValueError("CONFIGURED_EXECUTABLE_AMBIGUOUS")
    # Python venv executables are symlinks. Resolve the configured executable
    # explicitly; the actual loaded image is still read through /proc/PID/exe.
    path = Path(paths[0]).resolve(strict=True)
    return {"path": paths[0], "resolved_path": str(path), **executable_identity(path)}


def pythonpath(raw):
    for entry in raw.split(b"\0"):
        if entry.startswith(b"PYTHONPATH="):
            return entry[len(b"PYTHONPATH="):].decode().split(":")
    return []


def configured_pythonpath(unit):
    raw = subprocess.check_output(
        ["systemctl", "--user", "show", unit, "-p", "Environment", "--value"],
        timeout=15, text=True)
    for value in shlex.split(raw):
        if value.startswith("PYTHONPATH="):
            return value.split("=", 1)[1].split(":")
    return []



def configuration_identity(argv):
    if argv.count("--config") != 1:
        return None
    index = argv.index("--config") + 1
    if index >= len(argv):
        raise ValueError("CONFIGURATION_ARGUMENT_MISSING")
    path = Path(argv[index])
    raw = attribution.read_private(path, 1048576)
    value = attribution.strict(raw)
    result = {"path": str(path), "sha256": hashlib.sha256(raw).hexdigest(),
              "schema": value.get("schema"), "runtime_directory": value.get("runtime_directory")}
    original = value.get("original_config")
    if original is not None:
        raw = attribution.read_private(Path(original), 1048576)
        digest = hashlib.sha256(raw).hexdigest()
        resident = attribution.strict(raw)
        result["original_configuration"] = {
            "path": original, "sha256": digest,
            "guard_pin_matches": digest == value.get("original_config_sha256"),
            "schema": resident.get("schema"), "runtime_directory": resident.get("runtime_directory")}
    return result


def inspect_unit(unit):
    first = attribution.show(unit)
    configured = {"exec_start": first.get("ExecStart", ""),
                  "pythonpath": configured_pythonpath(unit)}
    try:
        configured["executable"] = configured_executable(configured["exec_start"])
    except (OSError, ValueError) as exc:
        configured["executable_error"] = str(exc)
    result = {"unit": unit, "service": first, "configured": configured,
              "running": None, "configured_matches_loaded_executable": None,
              "trade_authority": False, "service_changed": False}
    command = re.findall(r"argv\[\]=(.*?);", configured["exec_start"])
    if len(command) == 1:
        try:
            configured["configuration"] = configuration_identity(shlex.split(command[0]))
        except (OSError, ValueError, RuntimeError) as exc:
            configured["configuration_error"] = str(exc)
    pid = first.get("MainPID", "")
    if first.get("ActiveState") != "active" or not pid.isdigit() or int(pid) <= 0:
        result["status"] = "NOT_RUNNING"
        return result
    key = activation.pid_key(pid)
    if key is None:
        raise RuntimeError("PROCESS_CHANGED_DURING_INSPECTION")
    argv = [v.decode() for v in activation.proc_bytes(pid, "cmdline").rstrip(b"\0").split(b"\0")]
    environment_path = pythonpath(activation.proc_bytes(pid, "environ", 262144))
    link = Path("/proc") / pid / "exe"
    loaded_path = os.readlink(link)
    loaded = {"path": loaded_path, **executable_identity(link, process_link=True)}
    final = attribution.show(unit)
    if (activation.pid_key(pid) != key or
            any(first.get(k) != final.get(k) for k in ("MainPID", "ActiveState", "ExecStart"))):
        raise RuntimeError("PROCESS_CHANGED_DURING_INSPECTION")
    result["running"] = {"pid": int(pid), "start_ticks": key[1], "argv": argv,
                         "pythonpath": environment_path, "executable": loaded}
    expected = configured.get("executable")
    result["configured_matches_loaded_executable"] = (
        expected["sha256"] == loaded["sha256"] if expected else None)
    try:
        result["running"]["configuration"] = configuration_identity(argv)
    except (OSError, ValueError, RuntimeError) as exc:
        result["running"]["configuration_error"] = str(exc)
    result["configured_matches_process_pythonpath"] = configured["pythonpath"] == environment_path
    result["status"] = "PROCESS_IDENTITY_OBSERVED"
    result["python_code_identity_basis"] = "PROCESS_ARGV_AND_START_ENVIRONMENT"
    result["python_in_memory_module_bytes_verified"] = False
    return result


def inspect():
    units = {}
    for unit in UNITS:
        try:
            units[unit] = inspect_unit(unit)
        except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as exc:
            units[unit] = {"unit": unit, "status": "IDENTITY_UNAVAILABLE", "error": str(exc)}
    return {"schema": "heptatrader.runtime-identity.v1", "at_ms": int(time.time() * 1000),
            "units": units, "trade_authority": False, "service_changed": False}


def main():
    argparse.ArgumentParser(description=__doc__).parse_args()
    print(json.dumps(inspect(), sort_keys=True, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
