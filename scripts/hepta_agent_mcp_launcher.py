#!/usr/bin/python3

"""Non-setuid identity and environment gate for the installed MCP bridge."""

from __future__ import annotations

import os
from pathlib import Path
import stat
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent))
from hepta_agent_trust_domain import (
    TrustDomainRuntimeError, load_runtime_config,
)

AGENT_UID = 2004
AGENT_GID = 2004
MCP_SERVER = "/usr/libexec/hepta-mcp-server"
DOMAIN_CONFIG_ROOT = Path("/etc/heptatrader/trust-domains")


def default_domain_config_path(uid: int) -> Path:
    return DOMAIN_CONFIG_ROOT / f"uid-{uid}.json"


def fail(reason: str) -> int:
    print("hepta-agent-mcp-launcher: " + reason, file=sys.stderr)
    return 78


def main() -> int:
    if sys.argv != [sys.argv[0]]:
        return fail("argv configuration is forbidden")
    process_identity = (
        os.getuid(), os.geteuid(), os.getgid(), os.getegid())
    if (
            process_identity[0] != process_identity[1] or
            process_identity[2] != process_identity[3]):
        return fail("real/effective Agent identity mismatch")
    if sorted(set(os.getgroups())) not in ([], [process_identity[2]]):
        return fail("supplementary groups are forbidden")
    domain_config = os.environ.get("HEPTA_AGENT_DOMAIN_CONFIG", "")
    compatibility = os.environ.get(
        "HEPTA_AGENT_SINGLE_DOMAIN_COMPAT", "")
    if domain_config and compatibility:
        return fail("trust-domain modes are mutually exclusive")
    if not domain_config and compatibility != "1":
        domain_config = str(default_domain_config_path(os.getuid()))
    if domain_config:
        try:
            domain = load_runtime_config(
                Path(domain_config),
                expected_agent_identity=(
                    process_identity[0], process_identity[2]))
        except (OSError, TrustDomainRuntimeError):
            return fail("trust-domain configuration is unsafe")
        agent_uid = domain["agent_uid"]
        agent_gid = domain["agent_gid"]
        socket_path = domain["socket_path"]
        token_file = domain["token_directory"] + "/session.token"
    elif compatibility == "1":
        agent_uid = AGENT_UID
        agent_gid = AGENT_GID
        socket_path = "/run/hepta-agent/tools.sock"
        token_file = "/run/hepta-agent/session.token"
    else:
        return fail("explicit compatibility mode is invalid")
    if process_identity != (
            agent_uid, agent_uid, agent_gid, agent_gid):
        return fail("configured Agent UID/GID required")
    try:
        metadata = os.lstat(MCP_SERVER)
    except OSError:
        return fail("MCP bridge is missing")
    if (not stat.S_ISREG(metadata.st_mode) or stat.S_ISLNK(metadata.st_mode) or
            metadata.st_uid != 0 or metadata.st_gid != 0 or
            metadata.st_nlink != 1 or stat.S_IMODE(metadata.st_mode) != 0o755):
        return fail("MCP bridge metadata is unsafe")

    environment = {
        "PATH": "/usr/sbin:/usr/bin:/sbin:/bin",
        "LANG": "C.UTF-8",
        "LC_ALL": "C.UTF-8",
        "HEPTA_TOOL_SOCKET": socket_path,
        "HEPTA_TOOL_SESSION_TOKEN_FILE": token_file,
        "HEPTA_TOOL_EXPECTED_UID": str(agent_uid),
    }
    timeout = os.environ.get("HEPTA_MCP_TIMEOUT_SEC")
    if timeout is not None:
        if not timeout.isascii() or not timeout.isdecimal():
            return fail("MCP timeout is invalid")
        seconds = int(timeout, 10)
        if seconds < 1 or seconds > 120:
            return fail("MCP timeout is invalid")
        environment["HEPTA_MCP_TIMEOUT_SEC"] = str(seconds)
    try:
        os.execve(MCP_SERVER, [MCP_SERVER], environment)
    except OSError:
        return fail("MCP bridge exec failed")
    return fail("MCP bridge exec returned")


if __name__ == "__main__":
    raise SystemExit(main())
