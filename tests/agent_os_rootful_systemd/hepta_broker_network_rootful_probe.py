#!/usr/bin/env python3

"""Disposable-host positive/negative broker-port network gate.

This probe binds inert TCP sentinels. It never speaks an IB protocol, imports
an IB SDK, connects to a broker, or issues a trading request.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import threading
import time


SCHEMA = "hepta.broker-network-rootful-probe.v2"
MARKER = "HEPTA_BROKER_NETWORK_ROOTFUL_RESULT="
POLICY_SERVICE = "hepta-broker-egress-policy.service"
PROTECTED_PORTS = (4001, 4002, 7496, 7497)
MODEL_EGRESS_SENTINEL_PORT = 38443
AGENT_IDENTITY = (2004, 2004)
GATEWAY_IDENTITY = (2001, 2001)
SIMULATOR_EXECUTION_IDENTITY = (2002, 2002)
IB_EXECUTION_IDENTITY = (2003, 2003)
DOMAIN_AGENT_IDENTITIES = ((2104, 2104), (2105, 2105))
DOMAIN_GATEWAY_IDENTITIES = ((2101, 2101), (2102, 2102))
DOMAIN_SIMULATOR_EXECUTION_IDENTITIES = ((2111, 2111), (2112, 2112))
DENIED_IDENTITIES = (
    AGENT_IDENTITY,
    GATEWAY_IDENTITY,
    SIMULATOR_EXECUTION_IDENTITY,
    IB_EXECUTION_IDENTITY,
    *DOMAIN_AGENT_IDENTITIES,
    *DOMAIN_GATEWAY_IDENTITIES,
    *DOMAIN_SIMULATOR_EXECUTION_IDENTITIES,
)
SAFE_ENVIRONMENT = {
    "PATH": "/usr/sbin:/usr/bin:/sbin:/bin",
    "LANG": "C",
    "LC_ALL": "C",
    "SYSTEMD_COLORS": "0",
    "SYSTEMD_PAGER": "",
    "SYSTEMD_PAGERSECURE": "1",
}


class ProbeFailure(RuntimeError):
    pass


class Sentinel:
    def __init__(self, port: int):
        self.port = port
        self.socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.socket.bind(("127.0.0.1", port))
        self.socket.listen(16)
        self.socket.settimeout(0.1)
        self.stop_event = threading.Event()
        self.lock = threading.Lock()
        self.accepted = 0
        self.thread = threading.Thread(target=self._serve, daemon=True)

    def _serve(self) -> None:
        while not self.stop_event.is_set():
            try:
                connection, _address = self.socket.accept()
            except socket.timeout:
                continue
            except OSError:
                return
            with connection, self.lock:
                self.accepted += 1

    def start(self) -> None:
        self.thread.start()

    def close(self) -> None:
        self.stop_event.set()
        self.socket.close()
        self.thread.join(timeout=2.0)


def command(arguments: list[str]) -> subprocess.CompletedProcess[str]:
    completed = subprocess.run(
        arguments,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="strict",
        env=SAFE_ENVIRONMENT,
        cwd="/",
        close_fds=True,
        timeout=30,
        check=False,
    )
    if (
            len(completed.stdout.encode("utf-8")) > 1024 * 1024 or
            len(completed.stderr.encode("utf-8")) > 1024 * 1024):
        raise ProbeFailure("bounded command output exceeded")
    return completed


def require_policy_active() -> None:
    started = command([
        "/usr/bin/systemctl", "--no-pager", "--no-ask-password",
        "start", POLICY_SERVICE,
    ])
    if started.returncode != 0:
        raise ProbeFailure("broker network policy service failed to start")
    shown = command([
        "/usr/bin/systemctl", "--no-pager", "--no-ask-password",
        "show", POLICY_SERVICE,
        "--property=ActiveState", "--property=Result",
    ])
    if shown.returncode != 0:
        raise ProbeFailure("broker network policy service state unavailable")
    values: dict[str, str] = {}
    for line in shown.stdout.splitlines():
        if line.count("=") != 1:
            raise ProbeFailure("broker network policy service state malformed")
        key, value = line.split("=", 1)
        if key in values:
            raise ProbeFailure("broker network policy service state duplicated")
        values[key] = value
    if values != {"ActiveState": "active", "Result": "success"}:
        raise ProbeFailure("broker network policy service is not active")


def connect_as(uid: int, gid: int, port: int) -> bool:
    child = os.fork()
    if child == 0:
        try:
            os.setgroups([])
            os.setgid(gid)
            os.setuid(uid)
            client = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            try:
                client.settimeout(1.5)
                client.connect(("127.0.0.1", port))
            finally:
                client.close()
        except (OSError, ValueError):
            os._exit(1)
        os._exit(0)
    completed, status = os.waitpid(child, 0)
    if completed != child or not os.WIFEXITED(status):
        raise ProbeFailure("network identity probe child failed")
    return os.WEXITSTATUS(status) == 0


def wait_accepts(sentinel: Sentinel, expected: int) -> None:
    deadline = time.monotonic() + 3.0
    while sentinel.accepted != expected:
        if sentinel.accepted > expected:
            raise ProbeFailure("sentinel accepted an unauthorized connection")
        if time.monotonic() >= deadline:
            raise ProbeFailure("sentinel did not accept authorized connection")
        time.sleep(0.02)


def execute() -> dict[str, object]:
    if (
            os.geteuid() != 0 or os.getegid() != 0 or
            Path("/proc/1/comm").read_text(
                encoding="ascii", errors="strict").strip() != "systemd"):
        raise ProbeFailure("rootful probe requires disposable systemd PID 1")
    if (
            Path("/run/docker.sock").exists() or
            Path("/var/run/docker.sock").exists()):
        raise ProbeFailure("Docker socket leaked into rootful probe")

    sentinels = {
        port: Sentinel(port)
        for port in (*PROTECTED_PORTS, MODEL_EGRESS_SENTINEL_PORT)
    }
    for sentinel in sentinels.values():
        sentinel.start()
    try:
        require_policy_active()
        for port in PROTECTED_PORTS:
            sentinel = sentinels[port]
            for uid, gid in DENIED_IDENTITIES:
                if connect_as(uid, gid, port):
                    raise ProbeFailure(
                        "non-authority UID reached a protected broker port")
            wait_accepts(sentinel, 0)

        model = sentinels[MODEL_EGRESS_SENTINEL_PORT]
        for uid, gid in (AGENT_IDENTITY, *DOMAIN_AGENT_IDENTITIES):
            if not connect_as(uid, gid, MODEL_EGRESS_SENTINEL_PORT):
                raise ProbeFailure(
                    "broker policy blocked non-broker Agent model egress")
        wait_accepts(model, 1 + len(DOMAIN_AGENT_IDENTITIES))
    finally:
        for sentinel in sentinels.values():
            sentinel.close()

    return {
        "schema": SCHEMA,
        "passed": True,
        "checks": {
            "policy_service_active": True,
            "agent_uid_all_ib_ports_denied": True,
            "gateway_uid_all_ib_ports_denied": True,
            "simulator_execution_uid_all_ib_ports_denied": True,
            "domain_agent_uids_all_ib_ports_denied": True,
            "domain_gateway_uids_all_ib_ports_denied": True,
            "domain_simulator_execution_uids_all_ib_ports_denied": True,
            "ib_execution_uid_all_ib_ports_denied": True,
            "agent_non_broker_model_egress_allowed": True,
            "domain_agent_non_broker_model_egress_allowed": True,
            "protected_port_count": 4,
        },
        "identities": {
            "agent_uid": AGENT_IDENTITY[0],
            "gateway_uid": GATEWAY_IDENTITY[0],
            "simulator_execution_uid": SIMULATOR_EXECUTION_IDENTITY[0],
            "ib_execution_uid": IB_EXECUTION_IDENTITY[0],
            "domain_agent_uids": [
                item[0] for item in DOMAIN_AGENT_IDENTITIES],
            "domain_gateway_uids": [
                item[0] for item in DOMAIN_GATEWAY_IDENTITIES],
            "domain_simulator_execution_uids": [
                item[0] for item in DOMAIN_SIMULATOR_EXECUTION_IDENTITIES],
        },
        "boundary": {
            "sentinel_only": True,
            "real_broker_connections": 0,
            "broker_protocol_messages": 0,
            "paper_orders": 0,
            "paper_authorized": False,
            "live_authorized": False,
        },
    }


def main() -> int:
    try:
        result = execute()
    except (
            ProbeFailure, OSError, ValueError, subprocess.SubprocessError
            ) as error:
        message = str(error)
        if not message or len(message) > 180:
            message = type(error).__name__
        print("hepta_broker_network_rootful_probe: FAIL: " + message,
              file=sys.stderr)
        return 1
    print(MARKER + json.dumps(
        result, sort_keys=True, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
