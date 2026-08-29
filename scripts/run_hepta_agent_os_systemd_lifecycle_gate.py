#!/usr/bin/env python3

"""Exercise the Tool Gateway restart/socket lifecycle with a user systemd.

The production units are system units with fixed identities and fixed /run
paths.  This opt-in gate first verifies their source-level lifecycle contract,
then mirrors only that lifecycle in uniquely named user units under the
caller's XDG runtime directory.  It never starts a production Hepta unit,
touches a broker, provisions a session, or enables PAPER/LIVE authority.
"""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import selectors
import shutil
import signal
import socket
import stat
import subprocess
import sys
import tempfile
import time
from typing import Any
import uuid


SCHEMA = "hepta.agent-os-systemd-lifecycle-gate.v1"
REPOSITORY = Path(__file__).resolve(strict=True).parents[1]
SYSTEMD = REPOSITORY / "systemd"
GATEWAY_SERVICE = SYSTEMD / "hepta-tool-gateway.service"
TOOL_SOCKET = SYSTEMD / "hepta-tool-gateway.socket"
SUPERVISOR_SOCKET = SYSTEMD / "hepta-tool-session-supervisor.socket"
ACTIVATION_REPLY = b"hepta-agent-os-lifecycle-ok\n"
COMMAND_TIMEOUT_SECONDS = 20


class GateError(RuntimeError):
    """Fail-closed lifecycle-gate error."""


def require(condition: bool, message: str) -> None:
    if not condition:
        raise GateError(message)


def production_contract() -> dict[str, bool]:
    service = GATEWAY_SERVICE.read_text(encoding="utf-8", errors="strict")
    tool = TOOL_SOCKET.read_text(encoding="utf-8", errors="strict")
    supervisor = SUPERVISOR_SOCKET.read_text(
        encoding="utf-8", errors="strict")
    expected_service = (
        "Requires=hepta-tool-gateway.socket "
        "hepta-tool-session-supervisor.socket",
        "After=hepta-tool-gateway.socket "
        "hepta-tool-session-supervisor.socket",
        "Sockets=hepta-tool-gateway.socket "
        "hepta-tool-session-supervisor.socket",
        "RuntimeDirectory=hepta-tool-gateway",
        "RuntimeDirectoryPreserve=yes",
        "Restart=on-failure",
    )
    expected_tool = (
        "ListenStream=/run/hepta-agent/tools.sock",
        "Service=hepta-tool-gateway.service",
        "RemoveOnStop=yes",
    )
    expected_supervisor = (
        "ListenStream=/run/hepta-tool-gateway/session-supervisor.sock",
        "Service=hepta-tool-gateway.service",
        "RemoveOnStop=yes",
    )
    for directive in expected_service:
        require(directive in service,
                f"production Gateway service misses {directive}")
    for directive in expected_tool:
        require(directive in tool,
                f"production tool socket misses {directive}")
    for directive in expected_supervisor:
        require(directive in supervisor,
                f"production supervisor socket misses {directive}")
    return {
        "service_requires_both_sockets": True,
        "runtime_directory_preserved": True,
        "socket_units_own_endpoint_removal": True,
    }


def _unit_quote(value: str) -> str:
    return '"' + value.replace("\\", "\\\\").replace('"', '\\"') + '"'


def build_fixture_units(
        base: str, runtime_root: Path) -> tuple[
            dict[str, str], dict[str, str]]:
    require(base.startswith("hepta-agent-os-lifecycle-"),
            "fixture unit prefix is outside the reviewed namespace")
    require("/" not in base and len(base) <= 80,
            "fixture unit prefix is invalid")
    service_name = f"{base}.service"
    tool_name = f"{base}-tool.socket"
    supervisor_name = f"{base}-supervisor.socket"
    gateway_directory = runtime_root / base
    agent_directory = runtime_root / f"{base}-agent"
    tool_path = agent_directory / "tools.sock"
    supervisor_path = gateway_directory / "session-supervisor.sock"
    executable = Path(sys.executable).resolve(strict=True)
    runner = Path(__file__).resolve(strict=True)
    units = {
        service_name: (
            "[Unit]\n"
            f"Requires={tool_name} {supervisor_name}\n"
            f"After={tool_name} {supervisor_name}\n\n"
            "[Service]\n"
            "Type=simple\n"
            f"ExecStart={_unit_quote(str(executable))} "
            f"{_unit_quote(str(runner))} --fixture-service\n"
            f"Sockets={tool_name} {supervisor_name}\n"
            f"RuntimeDirectory={base}\n"
            "RuntimeDirectoryMode=0700\n"
            "RuntimeDirectoryPreserve=yes\n"
            "Restart=on-failure\n"
            "RestartSec=100ms\n"
            "TimeoutStopSec=10s\n"
            "NoNewPrivileges=yes\n"
            "PrivateTmp=yes\n"
        ),
        tool_name: (
            "[Socket]\n"
            f"ListenStream={tool_path}\n"
            "FileDescriptorName=hepta-tool\n"
            "SocketMode=0600\n"
            "DirectoryMode=0700\n"
            f"Service={service_name}\n"
            "Accept=no\n"
            "RemoveOnStop=yes\n"
        ),
        supervisor_name: (
            "[Socket]\n"
            f"ListenStream={supervisor_path}\n"
            "FileDescriptorName=hepta-supervisor\n"
            "SocketMode=0600\n"
            "DirectoryMode=0700\n"
            f"Service={service_name}\n"
            "Accept=no\n"
            "RemoveOnStop=yes\n"
        ),
    }
    paths = {
        "tool": str(tool_path),
        "supervisor": str(supervisor_path),
        "gateway_directory": str(gateway_directory),
        "agent_directory": str(agent_directory),
    }
    return units, paths


def _fixture_service() -> int:
    try:
        listen_pid = int(os.environ.get("LISTEN_PID", "-1"))
        listen_fds = int(os.environ.get("LISTEN_FDS", "-1"))
    except ValueError:
        return 78
    names = os.environ.get("LISTEN_FDNAMES", "").split(":")
    if (listen_pid != os.getpid() or listen_fds != 2 or
            sorted(names) != ["hepta-supervisor", "hepta-tool"]):
        return 78

    stopping = False

    def stop(_signum: int, _frame: object) -> None:
        nonlocal stopping
        stopping = True

    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    selector = selectors.DefaultSelector()
    listeners: list[socket.socket] = []
    try:
        for descriptor in range(3, 3 + listen_fds):
            listener = socket.socket(fileno=descriptor)
            listener.setblocking(False)
            listeners.append(listener)
            selector.register(listener, selectors.EVENT_READ)
        while not stopping:
            for key, _mask in selector.select(timeout=0.2):
                listener = key.fileobj
                try:
                    connection, _address = listener.accept()
                except BlockingIOError:
                    continue
                with connection:
                    connection.settimeout(2)
                    connection.sendall(ACTIVATION_REPLY)
    finally:
        selector.close()
        for listener in listeners:
            listener.close()
    return 0


def _systemctl(
        executable: str, *arguments: str, check: bool = True,
        timeout: int = COMMAND_TIMEOUT_SECONDS) -> subprocess.CompletedProcess[str]:
    completed = subprocess.run(
        [executable, "--user", "--no-pager", "--no-ask-password", *arguments],
        check=False, text=True, stdout=subprocess.PIPE,
        stderr=subprocess.PIPE, timeout=timeout)
    if check and completed.returncode != 0:
        raise GateError(
            "systemctl --user " + " ".join(arguments) + " failed: " +
            (completed.stdout + completed.stderr).strip()[:2000])
    return completed


def user_systemd_availability() -> tuple[str | None, str]:
    executable = shutil.which("systemctl")
    if executable is None:
        return None, "systemctl is unavailable"
    runtime = os.environ.get("XDG_RUNTIME_DIR", "")
    if not runtime:
        return None, "XDG_RUNTIME_DIR is unset"
    try:
        runtime_root = Path(runtime).resolve(strict=True)
        metadata = runtime_root.stat()
    except OSError as error:
        return None, f"XDG_RUNTIME_DIR is unusable: {error}"
    if (not stat.S_ISDIR(metadata.st_mode) or
            metadata.st_uid != os.geteuid()):
        return None, "XDG_RUNTIME_DIR is not an owned directory"
    try:
        probe = _systemctl(
            executable, "show-environment", check=False, timeout=10)
    except (OSError, subprocess.TimeoutExpired) as error:
        return None, f"user systemd probe failed: {error}"
    if probe.returncode != 0:
        detail = (probe.stdout + probe.stderr).strip()[:300]
        return None, "user systemd manager is unavailable: " + detail
    return executable, ""


def _active_state(executable: str, unit: str) -> str:
    result = _systemctl(
        executable, "show", "--property=ActiveState", "--value", unit,
        check=False)
    if result.returncode != 0:
        return "not-found"
    return result.stdout.strip()


def _wait_state(
        executable: str, unit: str, expected: set[str],
        timeout: float = 10.0) -> str:
    deadline = time.monotonic() + timeout
    last = ""
    while time.monotonic() < deadline:
        last = _active_state(executable, unit)
        if last in expected:
            return last
        time.sleep(0.1)
    raise GateError(
        f"{unit} did not reach {sorted(expected)}; last state={last!r}")


def _socket_identity(path: Path) -> tuple[int, int]:
    try:
        metadata = path.lstat()
    except FileNotFoundError as error:
        raise GateError(f"socket endpoint is absent: {path}") from error
    require(stat.S_ISSOCK(metadata.st_mode),
            f"socket endpoint is not a Unix socket: {path}")
    require(metadata.st_nlink == 1,
            f"socket endpoint link count is not one: {path}")
    return metadata.st_dev, metadata.st_ino


def _activate(path: Path) -> None:
    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as client:
            client.settimeout(10)
            client.connect(str(path))
            reply = client.recv(128)
    except OSError as error:
        raise GateError(f"socket activation failed for {path}: {error}") from error
    require(reply == ACTIVATION_REPLY,
            f"socket activation reply mismatch for {path}")


def _remove_runtime_unit_links(
        runtime_root: Path, unit_paths: list[Path]) -> None:
    unit_root = runtime_root / "systemd" / "user"
    for target in unit_paths:
        link = unit_root / target.name
        try:
            metadata = link.lstat()
        except FileNotFoundError:
            continue
        require(stat.S_ISLNK(metadata.st_mode),
                f"refusing to remove non-symlink runtime unit path: {link}")
        require(Path(os.readlink(link)) == target,
                f"refusing to remove unexpected runtime unit link: {link}")
        link.unlink()


def run_gate(executable: str) -> dict[str, Any]:
    contract = production_contract()
    runtime_root = Path(os.environ["XDG_RUNTIME_DIR"]).resolve(strict=True)
    base = "hepta-agent-os-lifecycle-" + uuid.uuid4().hex[:16]
    units, raw_paths = build_fixture_units(base, runtime_root)
    names = list(units)
    service_name = names[0]
    tool_name = names[1]
    supervisor_name = names[2]
    tool_path = Path(raw_paths["tool"])
    supervisor_path = Path(raw_paths["supervisor"])
    directories = (
        Path(raw_paths["gateway_directory"]),
        Path(raw_paths["agent_directory"]),
    )
    unit_paths: list[Path] = []
    checks: dict[str, bool] = dict(contract)
    try:
        with tempfile.TemporaryDirectory(
                prefix="hepta-agent-os-lifecycle-units-") as directory:
            unit_root = Path(directory)
            for name, contents in units.items():
                path = unit_root / name
                path.write_text(contents, encoding="utf-8")
                path.chmod(0o600)
                unit_paths.append(path)
            _systemctl(
                executable, "link", "--runtime",
                *[str(path) for path in unit_paths])
            _systemctl(executable, "daemon-reload")
            _systemctl(executable, "start", tool_name, supervisor_name)
            _wait_state(executable, tool_name, {"active"})
            _wait_state(executable, supervisor_name, {"active"})
            original = {
                "tool": _socket_identity(tool_path),
                "supervisor": _socket_identity(supervisor_path),
            }

            _systemctl(executable, "start", service_name)
            _wait_state(executable, service_name, {"active"})
            _systemctl(executable, "restart", service_name)
            _wait_state(executable, service_name, {"active"})
            after_restart = {
                "tool": _socket_identity(tool_path),
                "supervisor": _socket_identity(supervisor_path),
            }
            require(after_restart == original,
                    "service restart replaced a manager-owned socket inode")
            checks["service_restart_keeps_socket_inodes"] = True

            _systemctl(executable, "stop", service_name)
            _wait_state(executable, service_name, {"inactive"})
            _wait_state(executable, tool_name, {"active"})
            _wait_state(executable, supervisor_name, {"active"})
            require(_socket_identity(tool_path) == original["tool"] and
                    _socket_identity(supervisor_path) == original["supervisor"],
                    "service-only stop changed a manager-owned socket inode")
            _activate(tool_path)
            _wait_state(executable, service_name, {"active"})
            require(_socket_identity(tool_path) == original["tool"] and
                    _socket_identity(supervisor_path) == original["supervisor"],
                    "socket reactivation changed a manager-owned socket inode")
            checks["service_only_stop_leaves_activation_ready"] = True

            _systemctl(
                executable, "stop",
                service_name, tool_name, supervisor_name)
            _wait_state(executable, service_name, {"inactive"})
            _wait_state(executable, tool_name, {"inactive"})
            _wait_state(executable, supervisor_name, {"inactive"})
            require(not tool_path.exists() and not supervisor_path.exists(),
                    "complete shutdown left a socket endpoint behind")
            require(directories[0].is_dir(),
                    "RuntimeDirectoryPreserve=yes did not retain its directory")
            require(not any(directories[0].iterdir()),
                    "preserved Gateway runtime directory is not empty")
            checks["complete_shutdown_removes_socket_paths"] = True
            checks["preserved_empty_directory_is_not_liveness"] = True
    finally:
        _systemctl(
            executable, "stop", *names, check=False, timeout=10)
        try:
            _remove_runtime_unit_links(runtime_root, unit_paths)
        finally:
            _systemctl(executable, "daemon-reload", check=False, timeout=10)
        for path in (tool_path, supervisor_path):
            try:
                path.unlink()
            except FileNotFoundError:
                pass
        for path in reversed(directories):
            try:
                path.rmdir()
            except FileNotFoundError:
                pass
            except OSError:
                # A non-empty directory is evidence of a failed cleanup and
                # must not be removed recursively by this host-local gate.
                pass
        residuals = []
        unit_root = runtime_root / "systemd" / "user"
        for path in (
                *[unit_root / unit_path.name for unit_path in unit_paths],
                tool_path, supervisor_path, *directories):
            try:
                path.lstat()
            except FileNotFoundError:
                continue
            residuals.append(str(path))
        require(
            not residuals,
            "lifecycle fixture cleanup left residual paths: " +
            ", ".join(residuals))
    required_checks = {
        "service_requires_both_sockets",
        "runtime_directory_preserved",
        "socket_units_own_endpoint_removal",
        "service_restart_keeps_socket_inodes",
        "service_only_stop_leaves_activation_ready",
        "complete_shutdown_removes_socket_paths",
        "preserved_empty_directory_is_not_liveness",
    }
    require(set(checks) == required_checks and all(checks.values()),
            "lifecycle gate check closure is incomplete")
    return {
        "schema": SCHEMA,
        "passed": True,
        "scope": "isolated-user-systemd-lifecycle",
        "checks": checks,
        "boundary": {
            "production_hepta_units_started": False,
            "broker_connections": 0,
            "sessions_provisioned": 0,
            "paper_enabled": False,
            "live_enabled": False,
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Run the opt-in Agent OS systemd lifecycle gate")
    parser.add_argument(
        "--run", action="store_true",
        help="explicitly run the isolated user-systemd fixture")
    parser.add_argument(
        "--require", action="store_true",
        help="fail instead of skipping when user systemd is unavailable")
    parser.add_argument("--fixture-service", action="store_true",
                        help=argparse.SUPPRESS)
    arguments = parser.parse_args()
    if arguments.fixture_service:
        return _fixture_service()
    if arguments.require and not arguments.run:
        parser.error("--require requires --run")
    if not arguments.run:
        print(
            "hepta_agent_os_systemd_lifecycle_gate: SKIP "
            "(opt in with --run)")
        return 0
    executable, reason = user_systemd_availability()
    if executable is None:
        if arguments.require:
            raise GateError(reason)
        print(
            "hepta_agent_os_systemd_lifecycle_gate: SKIP "
            f"({reason})")
        return 0
    result = run_gate(executable)
    print(json.dumps(result, sort_keys=True, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (GateError, OSError, subprocess.TimeoutExpired) as error:
        print(
            "hepta_agent_os_systemd_lifecycle_gate: FAIL: " + str(error),
            file=sys.stderr)
        raise SystemExit(1)
