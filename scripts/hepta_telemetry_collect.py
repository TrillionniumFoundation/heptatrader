#!/usr/bin/env python3
"""Bounded, read-only journald collection into the existing atomic text publisher.

No Broker socket, credentials, execution request, listener or notification I/O.
The optional timer must be enabled by the host operator, never by packaging.
"""
from __future__ import annotations

import argparse
from collections import deque
from contextlib import contextmanager
import fcntl
import io
import json
import os
from pathlib import Path
import re
import selectors
import signal
import subprocess
import sys
import time
from typing import Callable

import hepta_oms_report as metrics

PROFILES = {
    "simulator": {"oms": "hepta-execution-simulator.service", "gateway": "hepta-tool-gateway.service"},
    "ib-paper": {"oms": "hepta-execution-ib-paper.service", "gateway": "hepta-tool-gateway.service"},
}
MAX_BYTES = 8 << 20
MAX_ROWS = 4096
MAX_ROW_BYTES = 131072
TIMEOUT_SECONDS = 4.0
IDENTITY = re.compile(r"[0-9a-f]{32}\Z")


def bounded_command(argv: list[str], *, timeout: float = TIMEOUT_SECONDS,
                    max_bytes: int = MAX_BYTES) -> bytes:
    """Read without communicate()'s unbounded capture; kill/reap on every failure.

    argv injection exists only as this internal test seam. The CLI has no
    executable, shell, environment or timeout override. Production uses the
    fixed journalctl command below with a minimal environment.
    """
    if not argv or not Path(argv[0]).is_absolute() or not 0 < timeout <= TIMEOUT_SECONDS:
        raise ValueError("invalid collection command")
    if type(max_bytes) is not int or not 0 < max_bytes <= MAX_BYTES:
        raise ValueError("invalid collection bound")
    child = subprocess.Popen(argv, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                             stderr=subprocess.DEVNULL, start_new_session=True,
                             env={"PATH": "/usr/bin:/bin", "LANG": "C", "LC_ALL": "C",
                                  "SYSTEMD_PAGER": "", "SYSTEMD_COLORS": "0"})
    assert child.stdout is not None
    deadline = time.monotonic() + timeout
    data = bytearray()
    reaped = False
    try:
        os.set_blocking(child.stdout.fileno(), False)
        with selectors.DefaultSelector() as selector:
            selector.register(child.stdout, selectors.EVENT_READ)
            while True:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise TimeoutError("collection deadline exceeded")
                if not selector.select(remaining):
                    raise TimeoutError("collection deadline exceeded")
                block = os.read(child.stdout.fileno(), min(65536, max_bytes + 1 - len(data)))
                if not block:
                    break
                data.extend(block)
                if len(data) > max_bytes:
                    raise ValueError("collection output exceeded bound")
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise TimeoutError("collection deadline exceeded")
        code = child.wait(timeout=remaining)
        reaped = True
        if code != 0:
            raise ValueError("collection command failed")
        return bytes(data)
    finally:
        # Even a successfully exited leader may leave a child holding stdout.
        # Kill only the process group created for this exact subprocess.
        if not reaped:
            try:
                os.killpg(child.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
        child.stdout.close()
        child.wait()


def read_journal(unit: str) -> bytes:
    if unit not in {value for profile in PROFILES.values() for value in profile.values()}:
        raise ValueError("unapproved collection unit")
    return bounded_command([
        "/usr/bin/journalctl", "--no-pager", "--quiet", "--boot=0", "--output=json",
        "--output-fields=MESSAGE,_SYSTEMD_UNIT,_SYSTEMD_INVOCATION_ID,_BOOT_ID",
        f"--lines={MAX_ROWS}", f"--unit={unit}",
    ])


def journal_samples(data: bytes, unit: str, kind: str) -> list[dict]:
    if kind not in {"oms", "gateway"} or not isinstance(data, bytes) or len(data) > MAX_BYTES:
        raise ValueError("invalid journal snapshot")
    if data and not data.endswith(b"\n"):
        raise ValueError("incomplete journal snapshot")
    schema = metrics.SCHEMA if kind == "oms" else metrics.GATEWAY_SCHEMA
    validator = metrics.validate if kind == "oms" else metrics.validate_gateway
    recent: deque[dict] = deque(maxlen=120)
    incarnation = None
    epoch = None
    stream = io.BytesIO(data)
    for number in range(MAX_ROWS + 1):
        row = stream.readline(MAX_ROW_BYTES + 1)
        if not row:
            break
        if number == MAX_ROWS:
            raise ValueError("too many journal records")
        if len(row) > MAX_ROW_BYTES:
            raise ValueError("oversized journal record")
        envelope = json.loads(row.decode("utf-8"), object_pairs_hook=metrics.unique,
                              parse_constant=metrics.reject_constant, parse_float=metrics.finite_float)
        if not isinstance(envelope, dict):
            raise ValueError("invalid journal envelope")
        # --unit also returns system-manager messages. Only a trusted journald
        # _SYSTEMD_UNIT producer match can contribute daemon telemetry.
        if envelope.get("_SYSTEMD_UNIT") != unit:
            continue
        boot, invocation = envelope.get("_BOOT_ID"), envelope.get("_SYSTEMD_INVOCATION_ID")
        if not all(isinstance(value, str) and IDENTITY.fullmatch(value) for value in (boot, invocation)):
            raise ValueError("missing journal producer incarnation")
        current = (boot, invocation)
        if incarnation != current:
            recent.clear()
            epoch = None
            incarnation = current
        message = envelope.get("MESSAGE")
        if not isinstance(message, str) or not message.lstrip().startswith("{"):
            continue
        if len(message.encode("utf-8")) > metrics.MAX_LINE:
            raise ValueError("oversized daemon observation")
        sample = json.loads(message, object_pairs_hook=metrics.unique,
                            parse_constant=metrics.reject_constant, parse_float=metrics.finite_float)
        if not isinstance(sample, dict) or sample.get("schema") != schema:
            continue
        validator(sample)
        if not sample.get("service_epoch"):
            raise ValueError("daemon incarnation required")
        if epoch is not None and sample["service_epoch"] != epoch:
            raise ValueError("multiple daemon epochs in one invocation")
        epoch = sample["service_epoch"]
        recent.append(sample)
    if not recent:
        raise ValueError("no observations from latest daemon invocation")
    return list(recent)


@contextmanager
def collection_lock(directory: Path):
    """Serialize observation AND publication, not merely the final rename."""
    directory_fd = metrics._open_metrics_directory(directory)
    fd = None
    try:
        name = ".hepta_collection.lock"
        fd = os.open(name, os.O_RDWR | os.O_CREAT | os.O_NONBLOCK | os.O_NOFOLLOW | os.O_CLOEXEC,
                     0o600, dir_fd=directory_fd)
        before = metrics._metric_leaf(directory_fd, name, 0o600)
        if before != metrics._metric_file_identity(os.fstat(fd)):
            raise ValueError("collection lock changed")
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        def verify():
            current_fd = metrics._open_metrics_directory(directory)
            try:
                old, current = os.fstat(directory_fd), os.fstat(current_fd)
                if (old.st_dev, old.st_ino) != (current.st_dev, current.st_ino):
                    raise ValueError("collection namespace changed")
                if metrics._metric_leaf(directory_fd, name, 0o600) != before:
                    raise ValueError("collection lock changed")
            finally:
                os.close(current_fd)
        yield verify
    finally:
        if fd is not None:
            os.close(fd)
        os.close(directory_fd)


def collect_kind(directory: Path, kind: str, unit: str,
                 reader: Callable[[str], bytes] = read_journal,
                 guard: Callable[[], None] | None = None) -> int:
    if not any(profile.get(kind) == unit for profile in PROFILES.values()):
        raise ValueError("invalid kind/unit pair")
    try:
        samples = journal_samples(reader(unit), unit, kind)
        # Measure now AFTER potentially slow collection. Starting-time freshness
        # can hide a delayed collector; there is no user-provided clock override.
        now_ms = time.time_ns() // 1000000
        summary = (metrics.report if kind == "oms" else metrics.gateway_report)(samples, now_ms)
        text = (metrics.prometheus if kind == "oms" else metrics.gateway_prometheus)(samples[-1], summary)
        text += metrics.collection_metrics(kind, now_ms, samples[-1])
        result = int(bool(summary["alerts"]))
    except (OSError, ValueError, TypeError, OverflowError, RecursionError, subprocess.TimeoutExpired):
        text = metrics.collection_metrics(kind, time.time_ns() // 1000000)
        result = 2
    if guard is not None:
        guard()
    metrics.publish_metrics(directory, kind, text)
    return result


def collect_profile(directory: Path, profile: str,
                    reader: Callable[[str], bytes] = read_journal) -> int:
    if profile not in PROFILES:
        raise ValueError("unknown collection profile")
    result = 0
    with collection_lock(directory) as guard:
        for kind, unit in PROFILES[profile].items():
            try:
                guard()
                status = collect_kind(directory, kind, unit, reader, guard)
            except (OSError, ValueError):
                status = 2
            # Never print raw journald output, messages, paths or exception text.
            print(f"HEPTA_COLLECTION_{kind.upper()}_{('OK', 'ALERT', 'FAILED')[status]}")
            result = max(result, status)
    return result


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--profile", required=True, choices=tuple(PROFILES))
    parser.add_argument("--output-dir", required=True, type=Path)
    args = parser.parse_args(argv)
    try:
        return collect_profile(args.output_dir, args.profile)
    except (OSError, ValueError):
        print("HEPTA_COLLECTION_LOCK_OR_NAMESPACE_FAILED", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
