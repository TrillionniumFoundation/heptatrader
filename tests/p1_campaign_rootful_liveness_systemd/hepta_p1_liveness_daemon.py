#!/usr/bin/env python3
"""Broker-free daemons for the disposable P1 systemd liveness fixture."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import signal
import socket
import sys
import time
from typing import Any


STATE = Path("/var/lib/hepta-p1-liveness")
CONTROL = Path("/run/hepta-p1-liveness")
BOUNDARY = {
    "paper_test_admission_candidate": False,
    "paper_authorized": False,
    "live_authorized": False,
    "mutation_authorized": False,
    "direct_broker_access": False,
    "order_submission_authorized": False,
}
stopping = False


def canonical(value: object) -> bytes:
    return (json.dumps(
        value, ensure_ascii=True, allow_nan=False, sort_keys=True,
        separators=(",", ":")) + "\n").encode("ascii")


def seal(value: dict[str, Any]) -> dict[str, Any]:
    result = dict(value)
    result["body_sha256"] = "sha256:" + hashlib.sha256(
        canonical(result)).hexdigest()
    return result


def notify(value: str) -> None:
    address = os.environ.get("NOTIFY_SOCKET")
    if not address:
        raise RuntimeError("notify socket missing")
    if address.startswith("@"):
        address = "\0" + address[1:]
    with socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM) as channel:
        channel.connect(address)
        channel.sendall(value.encode("utf-8"))


def publish(path: Path, value: dict[str, Any]) -> None:
    payload = canonical(value)
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    descriptor = os.open(
        path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_CLOEXEC |
        getattr(os, "O_NOFOLLOW", 0), 0o600)
    try:
        os.fchmod(descriptor, 0o600)
        view = memoryview(payload)
        while view:
            written = os.write(descriptor, view)
            if written <= 0:
                raise RuntimeError("short write")
            view = view[written:]
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
    parent = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC)
    try:
        os.fsync(parent)
    finally:
        os.close(parent)


def identity(generation: int) -> dict[str, Any]:
    return seal({
        "schema": "hepta.p1-liveness-fixture-process.v1",
        "generation": generation,
        "pid": os.getpid(),
        "invocation_id": os.environ.get("INVOCATION_ID", ""),
        "boot_id": Path("/proc/sys/kernel/random/boot_id").read_text(
            encoding="ascii").strip(),
        "started_boottime_ns": time.clock_gettime_ns(time.CLOCK_BOOTTIME),
        **BOUNDARY,
    })


def stop(_signum: int, _frame: object) -> None:
    global stopping
    stopping = True


def watchdog_fixture() -> int:
    first = STATE / "watchdog-first.json"
    recovered = STATE / "watchdog-recovered.json"
    if not first.exists():
        publish(first, identity(1))
        notify("READY=1\nSTATUS=fixture deliberately withholding watchdog")
        while True:
            time.sleep(1)
    if not recovered.exists():
        publish(recovered, identity(2))
    notify("READY=1\nWATCHDOG=1\nSTATUS=watchdog restart recovered")
    while not stopping:
        notify("WATCHDOG=1")
        time.sleep(0.2)
    notify("STOPPING=1")
    return 0


def failing_worker() -> int:
    journal = STATE / "worker-journal" / "00000000.json"
    if journal.exists():
        # A durable terminal is never replayed or converted into catch-up.
        return 4
    notify("READY=1\nWATCHDOG=1\nSTATUS=worker waiting for injected failure")
    trigger = CONTROL / "trigger-worker-failure"
    while not stopping:
        if trigger.exists():
            publish(journal, seal({
                "schema": "hepta.p1-liveness-fixture-journal-entry.v1",
                "sequence": 0,
                "event": "WORKER",
                "status": "FAILED_CLOSED",
                "reason": "DISPOSABLE_ROOTFUL_INJECTION",
                "catch_up": False,
                "previous_body_sha256": None,
                "recorded_boottime_ns":
                    time.clock_gettime_ns(time.CLOCK_BOOTTIME),
                **BOUNDARY,
            }))
            # A separate inner verifier must acknowledge observing this exact
            # invocation still active after the durable fsync.  Continue
            # watchdog pulses while waiting; timeout remains fail-closed.
            acknowledgement = CONTROL / "ack-worker-terminal-observed"
            deadline = time.monotonic() + 10.0
            while not acknowledgement.exists() and time.monotonic() < deadline:
                notify("WATCHDOG=1\nSTATUS=waiting for durable-terminal ack")
                time.sleep(0.1)
            if not acknowledgement.exists():
                return 4
            notify("STOPPING=1\nSTATUS=durable failed closed")
            return 4
        notify("WATCHDOG=1")
        time.sleep(0.1)
    notify("STOPPING=1")
    return 0


def coordinator_fixture() -> int:
    worker = STATE / "worker-journal" / "00000000.json"
    journal = STATE / "coordinator-journal" / "00000000.json"
    if journal.exists():
        return 4
    notify("READY=1\nWATCHDOG=1\nSTATUS=coordinator supervising worker")
    while not stopping:
        if worker.exists():
            publish(journal, seal({
                "schema": "hepta.p1-liveness-fixture-journal-entry.v1",
                "sequence": 0,
                "event": "CAMPAIGN",
                "status": "FAILED_CLOSED",
                "reason": "PINNED_WORKER_FAILED_CLOSED",
                "catch_up": False,
                "owned_cleanup_required": True,
                "previous_body_sha256": None,
                "recorded_boottime_ns":
                    time.clock_gettime_ns(time.CLOCK_BOOTTIME),
                **BOUNDARY,
            }))
            notify("STOPPING=1\nSTATUS=campaign failed closed")
            return 4
        notify("WATCHDOG=1")
        time.sleep(0.1)
    notify("STOPPING=1")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(allow_abbrev=False)
    parser.add_argument(
        "--run", required=True,
        choices=("watchdog", "worker", "coordinator"))
    arguments = parser.parse_args()
    if os.geteuid() != 0 or os.getegid() != 0:
        return 125
    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    if arguments.run == "watchdog":
        return watchdog_fixture()
    if arguments.run == "worker":
        return failing_worker()
    return coordinator_fixture()


if __name__ == "__main__":
    raise SystemExit(main())
