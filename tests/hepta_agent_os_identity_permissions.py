#!/usr/bin/env python3

"""Offline fixed-UID permission and installed MCP identity fixture."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import socket
import stat
import subprocess
import tempfile


AGENT_UID = 2004
AGENT_GID = 2004
GATEWAY_UID = 2001
GATEWAY_GID = 2001


def require(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def permitted(mode: int, owner: int, group: int, uid: int, gid: int,
              mask: int) -> bool:
    if uid == 0:
        return True
    shift = 6 if uid == owner else (3 if gid == group else 0)
    return ((mode >> shift) & mask) == mask


def static_contract(root: Path) -> None:
    tool_socket = (
        root / "systemd/hepta-tool-gateway.socket").read_text(encoding="utf-8")
    supervisor_socket = (
        root / "systemd/hepta-tool-session-supervisor.socket").read_text(
            encoding="utf-8")
    service = (
        root / "systemd/hepta-tool-gateway.service").read_text(encoding="utf-8")
    tmpfiles = (
        root / "tmpfiles.d/heptatrader-agent-os.conf").read_text(encoding="utf-8")
    require("ListenStream=/run/hepta-agent/tools.sock" in tool_socket,
            "tool socket must be in the Agent runtime parent")
    require("DirectoryMode=0711" in tool_socket,
            "tool socket parent must permit traversal without directory writes")
    require("SocketUser=hepta-agent" in tool_socket and
            "SocketGroup=hepta-agent" in tool_socket and
            "SocketMode=0600" in tool_socket,
            "tool socket leaf identity/mode mismatch")
    require(
        "ListenStream=/run/hepta-tool-gateway/session-supervisor.sock"
        in supervisor_socket and "DirectoryMode=0700" in supervisor_socket and
        "SocketUser=hepta-gateway" in supervisor_socket and
        "SocketGroup=hepta-gateway" in supervisor_socket and
        "SocketMode=0600" in supervisor_socket and
        "Service=hepta-tool-gateway.service" in supervisor_socket and
        "RemoveOnStop=yes" in supervisor_socket,
        "supervisor socket isolation mismatch")
    require(
        "Environment=HEPTA_TOOL_SOCKET=/run/hepta-agent/tools.sock" in service,
        "Gateway socket environment path mismatch")
    for required in (
        "Requires=hepta-tool-gateway.socket hepta-tool-session-supervisor.socket",
        "After=hepta-tool-gateway.socket hepta-tool-session-supervisor.socket",
        "RuntimeDirectory=hepta-tool-gateway",
        "RuntimeDirectoryMode=0700",
        "RuntimeDirectoryPreserve=yes",
        "Restart=on-failure",
    ):
        require(required in service,
                f"Gateway restart/reactivation contract misses {required}")
    require(
        "Service=hepta-tool-gateway.service" in tool_socket and
        "RemoveOnStop=yes" in tool_socket,
        "tool socket reactivation/removal ownership mismatch")
    require(
        "d /run/hepta-agent 0711 root root - -" in tmpfiles,
        "root-owned Agent runtime parent declaration missing")
    require(
        "f /run/hepta-agent/session-lease-terminal-cleanup.lock 0644 "
        "root root - -" in tmpfiles,
        "root-owned supervisor cleanup interlock declaration missing")

    require(permitted(0o711, 0, 0, AGENT_UID, AGENT_GID, 0o1),
            "Agent cannot traverse its root-owned runtime parent")
    require(not permitted(0o711, 0, 0, AGENT_UID, AGENT_GID, 0o2),
            "Agent must not write its root-owned runtime parent")
    require(permitted(0o600, AGENT_UID, AGENT_GID,
                      AGENT_UID, AGENT_GID, 0o6),
            "Agent cannot access its tool socket/token leaf")
    require(not permitted(0o600, AGENT_UID, AGENT_GID,
                          GATEWAY_UID, GATEWAY_GID, 0o2),
            "Gateway must not connect to the Agent socket")
    require(not permitted(0o700, GATEWAY_UID, GATEWAY_GID,
                          AGENT_UID, AGENT_GID, 0o1),
            "Agent must not traverse the supervisor directory")


def _connect(path: Path, expected: bool) -> None:
    connected = False
    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as client:
            client.connect(str(path))
            connected = True
    except PermissionError:
        connected = False
    require(connected == expected, f"unexpected connect access for {path}")


def _read(path: Path, expected: bool) -> None:
    opened = False
    try:
        descriptor = os.open(
            path, os.O_RDONLY | os.O_CLOEXEC | getattr(os, "O_NOFOLLOW", 0))
        try:
            require(os.read(descriptor, 128).startswith(b"offline-agent-token-"),
                    "token content fixture mismatch")
            opened = True
        finally:
            os.close(descriptor)
    except PermissionError:
        opened = False
    require(opened == expected, f"unexpected read access for {path}")


def _child(uid: int, gid: int, callback) -> None:
    pid = os.fork()
    if pid == 0:
        try:
            os.setgroups([])
            os.setgid(gid)
            os.setuid(uid)
            callback()
        except BaseException:
            os._exit(1)
        os._exit(0)
    waited, status = os.waitpid(pid, 0)
    require(waited == pid and os.WIFEXITED(status) and os.WEXITSTATUS(status) == 0,
            f"UID {uid} permission probe failed")


def _launcher_preexec(uid: int, gid: int):
    def drop() -> None:
        os.setgroups([])
        os.setgid(gid)
        os.setuid(uid)
    return drop


def installed_launcher_smoke() -> None:
    launcher = Path("/usr/libexec/hepta-agent-mcp-launcher")
    server = Path("/usr/libexec/hepta-mcp-server")
    if not launcher.exists() or not server.exists():
        return
    request = (
        '{"jsonrpc":"2.0","id":1,"method":"initialize",'
        '"params":{"protocolVersion":"2025-03-26"}}\n')
    accepted = subprocess.run(
        [str(launcher)], input=request, stdout=subprocess.PIPE,
        stderr=subprocess.PIPE, text=True, check=False, timeout=10,
        preexec_fn=_launcher_preexec(AGENT_UID, AGENT_GID))
    require(accepted.returncode == 0 and not accepted.stderr,
            "UID 2004 launcher smoke failed")
    response = json.loads(accepted.stdout)
    require(response["result"]["serverInfo"]["name"] == "heptatrader",
            "installed MCP initialize response mismatch")
    rejected = subprocess.run(
        [str(launcher)], input=request, stdout=subprocess.PIPE,
        stderr=subprocess.PIPE, text=True, check=False, timeout=10,
        preexec_fn=_launcher_preexec(GATEWAY_UID, GATEWAY_GID))
    require(rejected.returncode == 78 and not rejected.stdout and
            "fixed hepta-agent UID/GID required" in rejected.stderr,
            "Gateway UID was not rejected by MCP launcher")


def root_permission_probe() -> None:
    require(os.geteuid() == 0, "root permission probe requires root")
    with tempfile.TemporaryDirectory(prefix="hepta-agent-uid-probe-") as name:
        root = Path(name)
        root.chmod(0o755)
        agent = root / "hepta-agent"
        gateway = root / "hepta-tool-gateway"
        agent.mkdir(mode=0o711)
        gateway.mkdir(mode=0o700)
        os.chown(agent, 0, 0)
        os.chown(gateway, GATEWAY_UID, GATEWAY_GID)

        token = agent / "session.token"
        token.write_bytes(b"offline-agent-token-0000000001\n")
        token.chmod(0o600)
        os.chown(token, AGENT_UID, AGENT_GID)

        tool_path = agent / "tools.sock"
        supervisor_path = gateway / "session-supervisor.sock"
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as tool_listener, \
                socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as supervisor:
            tool_listener.bind(str(tool_path))
            tool_listener.listen(4)
            tool_path.chmod(0o600)
            os.chown(tool_path, AGENT_UID, AGENT_GID)
            supervisor.bind(str(supervisor_path))
            supervisor.listen(4)
            supervisor_path.chmod(0o600)
            os.chown(supervisor_path, GATEWAY_UID, GATEWAY_GID)

            _child(AGENT_UID, AGENT_GID, lambda: (
                _read(token, True),
                _connect(tool_path, True),
                _connect(supervisor_path, False),
            ))
            _child(GATEWAY_UID, GATEWAY_GID, lambda: (
                _read(token, False),
                _connect(tool_path, False),
                _connect(supervisor_path, True),
            ))
            # The explicit bootstrap is root-operated: root must reach the
            # gateway-private supervisor while no Agent UID can.
            _read(token, True)
            _connect(supervisor_path, True)
    installed_launcher_smoke()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--require-root", action="store_true")
    parser.add_argument("--root-probe-only", action="store_true")
    arguments = parser.parse_args()
    repository = Path(__file__).resolve().parents[1]
    if not arguments.root_probe_only:
        static_contract(repository)
    else:
        require(arguments.require_root,
                "--root-probe-only is valid only with --require-root")
    if os.geteuid() == 0:
        root_permission_probe()
        print("hepta_agent_os_identity_root_probe: PASS")
    elif arguments.require_root:
        raise AssertionError("--require-root requested outside a root fixture")
    else:
        print("hepta_agent_os_identity_root_probe: SKIP (root fixture required)")
    print("hepta_agent_os_identity_permissions: PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
