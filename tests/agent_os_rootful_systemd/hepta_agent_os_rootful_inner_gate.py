#!/usr/bin/env python3

"""Inner offline four-UID Agent OS systemd lifecycle gate."""

from __future__ import annotations

import grp
import hashlib
import importlib.machinery
import importlib.util
import json
import math
import os
from pathlib import Path
import pwd
import re
import signal
import socket
import stat
import subprocess
import sys
import time
from typing import Any, Optional


SCHEMA = "hepta.agent-os-rootful-systemd-e2e-inner.v2"
COMPATIBILITY_SCHEMA = "hepta.agent-os-rootful-systemd-e2e-inner.v1"
CHECKER = "/usr/libexec/check-hepta-agent-os-provisioned-host"
BOOTSTRAP = "/usr/libexec/hepta-agent-session-bootstrap"
CUSTODIAN = "/usr/libexec/hepta-shadow-watch-custodian"
MCP_LAUNCHER = "/usr/libexec/hepta-agent-mcp-launcher"
SHADOW_WATCH_COLLECTOR = "/usr/libexec/hepta-shadow-watch-collector"
INSTALLED_TRUST_DOMAIN = Path("/usr/libexec/hepta_agent_trust_domain.py")
INSTALLED_OBSERVER_CONTROLLER = Path(
    "/usr/libexec/hepta-p1-shadow-observer-controller")
BROKER_NETWORK_PROBE = (
    "/usr/local/libexec/hepta_broker_network_rootful_probe.py")
BROKER_EGRESS_POLICY = "/usr/libexec/hepta-broker-egress-policy"
BROKER_EGRESS_UNIT = "hepta-broker-egress-policy.service"
SENTINEL = Path("/run/hepta-agent-os-rootful-e2e.disposable")
FULL_CHAIN_MARKER = Path(
    "/etc/heptatrader/trust-domains/full-chain.required")
FULL_CHAIN_MARKER_CONTENT = (
    "HEPTA_AGENT_OS_TWO_DOMAIN_FULL_CHAIN_E2E_V1\n")
INSTALLATION_PREFLIGHT = Path(
    "/usr/local/share/hepta-agent-os-e2e/installation-preflight")
SOCKET_UNITS = (
    "hepta-execution-simulator.socket",
    "hepta-execution-events-simulator.socket",
    "hepta-tool-gateway.socket",
    "hepta-tool-session-supervisor.socket",
)
SERVICE_UNITS = (
    "hepta-tool-gateway.service",
    "hepta-execution-simulator.service",
)
SOCKET_PATHS = {
    "tool_socket_inode": (
        Path("/run/hepta-agent/tools.sock"), 2004, 2004),
    "supervisor_socket_inode": (
        Path("/run/hepta-tool-gateway/session-supervisor.sock"), 2001, 2001),
    "execution_socket_inode": (
        Path("/run/hepta-execution/execution.sock"), 2001, 2001),
    "events_socket_inode": (
        Path("/run/hepta-execution/events.sock"), 2001, 2001),
}
ENVIRONMENT = {
    "PATH": "/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin",
    "LANG": "C.UTF-8",
    "LC_ALL": "C.UTF-8",
    "TZ": "UTC",
}
MAX_OUTPUT = 2 * 1024 * 1024
DOMAIN_IDENTITIES = (
    ("codex-a", "hepta-gw-codex-a", 2101, 2101,
     "hepta-agent-codex-a", 2104, 2104,
     "hepta-exec-codex-a", 2111, 2111),
    ("openclaw-b", "hepta-gw-openclaw-b", 2102, 2102,
     "hepta-agent-openclaw-b", 2105, 2105,
     "hepta-exec-openclaw-b", 2112, 2112),
)
DOMAIN_EXECUTION_INSTANCES = (
    ("codex-a", 2101, 2101, 2111, 2111),
    ("openclaw-b", 2102, 2102, 2112, 2112),
)
DOMAIN_READERS = {
    "codex-a": ("hepta-shadow-reader-codex-a", 2121, 2121),
    "openclaw-b": ("hepta-shadow-reader-openclaw-b", 2122, 2122),
}
DOMAIN_CUSTODIAN_TTL_SECONDS = 900
DOMAIN_CUSTODIAN_STATE_ROOT = Path(
    "/var/lib/hepta-shadow-watch-custodian")
CUSTODIAN_CLOSURE_FIELDS = frozenset({
    "schema", "version", "domain_id", "campaign_id", "config_sha256",
    "watch_environment_sha256", "token_directory", "supervisor_socket",
    "agent_uid", "agent_gid", "gateway_uid", "execution_uid",
    "owner_pid", "owner_uid", "owner_gid", "owner_start_ticks",
    "owner_boot_id", "lease_generation", "lease_receipt_body_sha256",
    "fence_token_sha256", "lease_expires_at_ms", "registered_at_ms",
    "close_started_at_ms", "closed_at_ms", "close_reason",
    "authoritative_revoke_outcome", "local_authority_removed",
    "export_evidence_removed", "paper_authorized", "live_authorized",
    "mutation_authorized", "direct_broker_access", "body_sha256",
})
DIGEST = re.compile(r"^sha256:[0-9a-f]{64}$")
WATCH_TOOL_NAMES = frozenset({
    "system.tools.list",
    "system.tools.describe",
    "system.cancel_request",
    "market.get_quote",
    "account.get_summary",
    "portfolio.list_positions",
    "orders.list",
    "risk.get_limits",
    "events.wait",
    "system.get_health",
    "watch.get_snapshot",
})


class GateFailure(RuntimeError):
    pass


def fail(message: str) -> None:
    raise GateFailure(message)


def command(
        arguments: list[str],
        *,
        timeout: int = 60,
        check: bool = True,
) -> subprocess.CompletedProcess[str]:
    completed = subprocess.run(
        arguments,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="strict",
        env=ENVIRONMENT,
        cwd="/",
        close_fds=True,
        timeout=timeout,
        check=False,
    )
    if (
            len(completed.stdout.encode("utf-8")) > MAX_OUTPUT or
            len(completed.stderr.encode("utf-8")) > MAX_OUTPUT):
        fail("bounded command output exceeded")
    if check and completed.returncode != 0:
        fail("required local command failed")
    return completed


def systemctl(*arguments: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    return command(
        ["/usr/bin/systemctl", *arguments],
        timeout=90,
        check=check,
    )


def unit_active(unit: str) -> bool:
    result = systemctl("is-active", "--quiet", unit, check=False)
    if result.returncode not in (0, 3):
        fail("systemd active-state query failed")
    return result.returncode == 0


def wait_active(unit: str, expected: bool, timeout: float = 20.0) -> None:
    deadline = time.monotonic() + timeout
    while unit_active(unit) is not expected:
        if time.monotonic() >= deadline:
            fail("systemd unit did not reach required state")
        time.sleep(0.1)


def wait_process_state(
        pid: int, expected: str, timeout: float = 3.0) -> None:
    deadline = time.monotonic() + timeout
    while True:
        try:
            status = Path(f"/proc/{pid}/status").read_text(
                encoding="utf-8", errors="strict")
        except FileNotFoundError:
            fail("service process disappeared before the expected state")
        states = [
            line.split(":", 1)[1].strip()
            for line in status.splitlines()
            if line.startswith("State:")]
        if len(states) != 1:
            fail("service process state is malformed")
        if states[0].split(" ", 1)[0] == expected:
            return
        if time.monotonic() >= deadline:
            fail("service process did not reach the expected state")
        time.sleep(0.02)


def show_properties(unit: str, names: tuple[str, ...]) -> dict[str, str]:
    arguments = ["show", unit, "--no-pager"]
    for name in names:
        arguments.extend(["--property", name])
    output = systemctl(*arguments).stdout
    values: dict[str, str] = {}
    for line in output.splitlines():
        if "=" not in line:
            fail("systemd property output is malformed")
        key, value = line.split("=", 1)
        if key in values:
            fail("systemd property output contains duplicates")
        values[key] = value
    if set(values) != set(names):
        fail("systemd property output field mismatch")
    return values


def require_identity(
        name: str, uid: int, gid: int, *,
        group_members: tuple[str, ...] = (),
        supplementary: tuple[int, ...] = ()) -> None:
    try:
        user = pwd.getpwnam(name)
        group = grp.getgrnam(name)
    except KeyError as error:
        raise GateFailure("fixed service identity is missing") from error
    if (
            user.pw_uid != uid or user.pw_gid != gid or
            group.gr_gid != gid or user.pw_dir != "/nonexistent" or
            not user.pw_shell.endswith("/nologin") or
            tuple(sorted(group.gr_mem)) != tuple(sorted(group_members)) or
            sorted(set(os.getgrouplist(name, gid))) !=
            sorted({gid, *supplementary})):
        fail("fixed service identity isolation mismatch")


def validate_fixed_identities() -> None:
    identities = (
        ("hepta-gateway", 2001, 2001),
        ("hepta-exec", 2002, 2002),
        ("hepta-ib-exec", 2003, 2003),
        ("hepta-agent", 2004, 2004),
    )
    for name, uid, gid in identities:
        require_identity(name, uid, gid)
    domain_identities: list[tuple[str, int, int]] = []
    for (
            _domain_id, gateway_name, gateway_uid, gateway_gid,
            agent_name, agent_uid, agent_gid,
            execution_name, execution_uid, execution_gid,
    ) in DOMAIN_IDENTITIES:
        require_identity(gateway_name, gateway_uid, gateway_gid)
        require_identity(agent_name, agent_uid, agent_gid)
        require_identity(execution_name, execution_uid, execution_gid)
        domain_identities.extend((
            (gateway_name, gateway_uid, gateway_gid),
            (agent_name, agent_uid, agent_gid),
            (execution_name, execution_uid, execution_gid),
        ))
    reader_identities: list[tuple[str, int, int]] = []
    for reader_name, reader_uid, reader_gid in DOMAIN_READERS.values():
        require_identity(reader_name, reader_uid, reader_gid)
        reader_identities.append((reader_name, reader_uid, reader_gid))
    all_identities = (*identities, *domain_identities, *reader_identities)
    if len({uid for _name, uid, _gid in all_identities}) != len(all_identities):
        fail("fixed service UID collision")
    if len({gid for _name, _uid, gid in all_identities}) != len(all_identities):
        fail("fixed service GID collision")


def validate_platform() -> None:
    if (
            os.geteuid() != 0 or os.getegid() != 0 or
            Path("/proc/1/comm").read_text(
                encoding="utf-8", errors="strict").strip() != "systemd"):
        fail("inner gate requires systemd PID 1 and container root")
    sentinel = SENTINEL.read_text(
        encoding="ascii", errors="strict").strip()
    if (
            len(sentinel) != 32 or
            any(character not in "0123456789abcdef" for character in sentinel)):
        fail("disposable container sentinel is invalid")
    if Path("/run/docker.sock").exists() or Path("/var/run/docker.sock").exists():
        fail("Docker socket leaked into the disposable container")
    mount = command([
        "/usr/bin/findmnt", "-rn", "-T", "/etc/heptatrader",
        "-o", "FSTYPE,TARGET",
    ]).stdout.strip()
    if mount != "tmpfs /etc/heptatrader":
        fail("runtime provisioning tree is not an isolated tmpfs")
    links = command(["/usr/sbin/ip", "-json", "link", "show"]).stdout
    try:
        interfaces = json.loads(links)
    except json.JSONDecodeError as error:
        raise GateFailure("network interface inventory is invalid") from error
    if (
            not isinstance(interfaces, list) or len(interfaces) != 1 or
            interfaces[0].get("ifname") != "lo"):
        fail("network=none container contains a non-loopback interface")


def validate_no_ib_surface() -> None:
    forbidden = (
        Path("/usr/libexec/hepta-ib-executiond"),
        Path("/usr/lib/systemd/system/hepta-execution-ib-paper.service"),
        Path("/usr/lib/systemd/system/hepta-execution-ib-paper.socket"),
        Path("/usr/lib/systemd/system/"
             "hepta-execution-events-ib-paper.socket"),
    )
    if any(path.exists() or path.is_symlink() for path in forbidden):
        fail("IB/PAPER runtime surface is present")
    for entry in Path("/proc").iterdir():
        if not entry.name.isdecimal():
            continue
        try:
            if entry.stat(follow_symlinks=False).st_uid == 2003:
                fail("reserved IB execution identity owns a running process")
        except (FileNotFoundError, PermissionError):
            continue


def socket_inodes() -> dict[str, int]:
    result: dict[str, int] = {}
    for name, (path, uid, gid) in SOCKET_PATHS.items():
        metadata = os.lstat(path)
        if (
                not stat.S_ISSOCK(metadata.st_mode) or
                stat.S_IMODE(metadata.st_mode) != 0o660 or
                metadata.st_uid != uid or metadata.st_gid != gid):
            fail("systemd socket metadata mismatch")
        result[name] = metadata.st_ino
    return result


def require_paths_absent() -> None:
    for path, _uid, _gid in SOCKET_PATHS.values():
        try:
            os.lstat(path)
        except FileNotFoundError:
            continue
        fail("stopped systemd socket path remains present")


def process_identity(
        pid: int, uid: int, gid: int,
        supplementary: tuple[int, ...] = ()) -> None:
    if pid <= 1:
        fail("service MainPID is invalid")
    status = Path(f"/proc/{pid}/status").read_text(
        encoding="utf-8", errors="strict")
    fields: dict[str, str] = {}
    for line in status.splitlines():
        if ":" in line:
            key, value = line.split(":", 1)
            fields[key] = value.strip()
    expected_ids = f"{uid}\t{uid}\t{uid}\t{uid}"
    expected_gids = f"{gid}\t{gid}\t{gid}\t{gid}"
    groups = set(fields.get("Groups", "").split())
    expected_supplementary = {str(item) for item in supplementary}
    if (
            fields.get("Uid") != expected_ids or
            fields.get("Gid") != expected_gids or
            groups not in (
                expected_supplementary,
                expected_supplementary | {str(gid)})):
        fail("service process identity mismatch")


def service_pid(
        unit: str, uid: int, gid: int,
        supplementary: tuple[int, ...] = ()) -> int:
    values = show_properties(unit, ("ActiveState", "MainPID"))
    if values["ActiveState"] != "active":
        fail("service is not active")
    try:
        pid = int(values["MainPID"], 10)
    except ValueError as error:
        raise GateFailure("service MainPID is malformed") from error
    process_identity(pid, uid, gid, supplementary)
    return pid


def validate_uid1000_observer_reads_uid2101_proc_stat(pid: int) -> None:
    read_fd, write_fd = os.pipe()
    child = os.fork()
    if child == 0:
        os.close(read_fd)
        try:
            os.setgroups([])
            os.setgid(1000)
            os.setuid(1000)
            if (
                    os.getuid() != 1000 or os.geteuid() != 1000 or
                    os.getgid() != 1000 or os.getegid() != 1000 or
                    os.getgroups()):
                os._exit(1)

            trust_loader = importlib.machinery.SourceFileLoader(
                "hepta_agent_trust_domain", str(INSTALLED_TRUST_DOMAIN))
            trust_spec = importlib.util.spec_from_loader(
                trust_loader.name, trust_loader)
            if trust_spec is None:
                os._exit(1)
            trust_module = importlib.util.module_from_spec(trust_spec)
            sys.modules[trust_loader.name] = trust_module
            trust_loader.exec_module(trust_module)

            observer_loader = importlib.machinery.SourceFileLoader(
                "installed_hepta_p1_shadow_observer_controller",
                str(INSTALLED_OBSERVER_CONTROLLER),
            )
            observer_spec = importlib.util.spec_from_loader(
                observer_loader.name, observer_loader)
            if observer_spec is None:
                os._exit(1)
            observer_module = importlib.util.module_from_spec(observer_spec)
            sys.modules[observer_loader.name] = observer_module
            observer_loader.exec_module(observer_module)
            reader = getattr(
                observer_module, "read_alpha_gateway_process_identity", None)
            if not callable(reader):
                os._exit(1)
            identity = reader(pid)
            pid_metadata = identity.pid_directory_metadata
            stat_metadata = identity.stat_metadata
            if not (
                    len(pid_metadata) == 9 and
                    stat.S_ISDIR(pid_metadata[2]) and
                    stat.S_IMODE(pid_metadata[2]) == 0o555 and
                    pid_metadata[3] >= 2 and
                    pid_metadata[4:7] == (2101, 2101, 0) and
                    len(stat_metadata) == 9 and
                    stat.S_ISREG(stat_metadata[2]) and
                    stat.S_IMODE(stat_metadata[2]) == 0o444 and
                    stat_metadata[3:7] == (1, 2101, 2101, 0) and
                    type(identity.starttime_ticks) is int and
                    identity.starttime_ticks > 0):
                os._exit(1)
            os.write(write_fd, b"PASS\n")
            os._exit(0)
        except BaseException:
            os._exit(1)

    os.close(write_fd)
    proof = bytearray()
    try:
        while len(proof) <= 16:
            chunk = os.read(read_fd, 17 - len(proof))
            if not chunk:
                break
            proof.extend(chunk)
    finally:
        os.close(read_fd)
    waited_pid, status_code = os.waitpid(child, 0)
    if (
            waited_pid != child or not os.WIFEXITED(status_code) or
            os.WEXITSTATUS(status_code) != 0 or bytes(proof) != b"PASS\n"):
        fail("UID 1000 observer could not read UID 2101 proc stat")


def require_unit_contracts() -> None:
    simulator = show_properties(
        "hepta-execution-simulator.service",
        (
            "User", "Group", "PrivateNetwork", "NoNewPrivileges",
            "RestrictAddressFamilies", "Sockets",
        ),
    )
    gateway = show_properties(
        "hepta-tool-gateway.service",
        (
            "User", "Group", "PrivateNetwork", "NoNewPrivileges",
            "RestrictAddressFamilies", "Sockets",
        ),
    )
    if (
            simulator["User"] != "hepta-exec" or
            simulator["Group"] != "hepta-exec" or
            gateway["User"] != "hepta-gateway" or
            gateway["Group"] != "hepta-gateway"):
        fail("systemd service identity contract mismatch")
    for values in (simulator, gateway):
        if (
                values["PrivateNetwork"] != "yes" or
                values["NoNewPrivileges"] != "yes" or
                values["RestrictAddressFamilies"] != "AF_UNIX"):
            fail("systemd service isolation contract mismatch")
    if set(simulator["Sockets"].split()) != {
            "hepta-execution-simulator.socket",
            "hepta-execution-events-simulator.socket"}:
        fail("Simulator dual-socket binding mismatch")
    if set(gateway["Sockets"].split()) != {
            "hepta-tool-gateway.socket",
            "hepta-tool-session-supervisor.socket"}:
        fail("Gateway dual-socket binding mismatch")


def _connect_as(path: Path, uid: int, gid: int) -> bool:
    pid = os.fork()
    if pid == 0:
        try:
            os.setgroups([])
            os.setgid(gid)
            os.setuid(uid)
            client = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            try:
                client.settimeout(3.0)
                client.connect(str(path))
            finally:
                client.close()
        except (OSError, ValueError):
            os._exit(1)
        os._exit(0)
    completed, status = os.waitpid(pid, 0)
    if completed != pid or not os.WIFEXITED(status):
        fail("domain socket access probe child failed")
    return os.WEXITSTATUS(status) == 0


def validate_domain_execution_instances() -> None:
    socket_units: list[str] = []
    service_units: list[str] = []
    for domain_id, _gateway_uid, _gateway_gid, _exec_uid, _exec_gid in (
            DOMAIN_EXECUTION_INSTANCES):
        socket_units.extend((
            f"hepta-execution-simulator@{domain_id}.socket",
            f"hepta-execution-events-simulator@{domain_id}.socket",
        ))
        service_units.append(
            f"hepta-execution-simulator@{domain_id}.service")
    systemctl("start", *socket_units)
    for unit in socket_units:
        wait_active(unit, True)
    for unit in service_units:
        if unit_active(unit):
            fail("domain socket start eagerly launched an Execution service")

    paths: list[Path] = []
    for (
            domain_id, gateway_uid, gateway_gid, execution_uid, execution_gid,
    ) in DOMAIN_EXECUTION_INSTANCES:
        command_path = Path(
            f"/run/hepta-execution-{domain_id}/execution.sock")
        event_path = Path(f"/run/hepta-execution-{domain_id}/events.sock")
        paths.extend((command_path, event_path))
        for path in (command_path, event_path):
            metadata = os.lstat(path)
            if (
                    not stat.S_ISSOCK(metadata.st_mode) or
                    stat.S_IMODE(metadata.st_mode) != 0o600 or
                    metadata.st_uid != gateway_uid or
                    metadata.st_gid != gateway_gid):
                fail("domain Execution socket inode isolation mismatch")
        other = (
            DOMAIN_EXECUTION_INSTANCES[1]
            if domain_id == DOMAIN_EXECUTION_INSTANCES[0][0]
            else DOMAIN_EXECUTION_INSTANCES[0])
        for path in (command_path, event_path):
            if _connect_as(path, other[1], other[2]):
                fail("cross-domain Gateway opened a foreign Execution socket")
            if _connect_as(path, 2001, 2001):
                fail("shared compatibility Gateway opened a domain socket")
            if not _connect_as(path, gateway_uid, gateway_gid):
                fail("matching domain Gateway could not open Execution socket")
        service = f"hepta-execution-simulator@{domain_id}.service"
        wait_active(service, True)
        service_pid(service, execution_uid, execution_gid)

    systemctl("stop", *service_units, *reversed(socket_units))
    for unit in (*service_units, *socket_units):
        wait_active(unit, False)
    for path in paths:
        if path.exists() or path.is_symlink():
            fail("domain Execution socket remained after stop")


def full_chain_required() -> bool:
    try:
        contents = _regular_root_file(FULL_CHAIN_MARKER, 0o400)
    except FileNotFoundError:
        return False
    try:
        marker = contents.decode("ascii", errors="strict")
    except UnicodeDecodeError as error:
        raise GateFailure(
            "full trust-domain chain marker is not ASCII") from error
    if marker != FULL_CHAIN_MARKER_CONTENT:
        fail("full trust-domain chain marker is unsafe")
    return True


def validate_broker_network_policy() -> None:
    completed = command(
        ["/usr/bin/python3", BROKER_NETWORK_PROBE],
        timeout=90,
        check=False,
    )
    prefix = "HEPTA_BROKER_NETWORK_ROOTFUL_RESULT="
    lines = [
        line for line in completed.stdout.splitlines()
        if line.startswith(prefix)]
    if (
            completed.returncode != 0 or completed.stderr or len(lines) != 1):
        fail("broker network rootful probe failed")
    try:
        result = json.loads(lines[0][len(prefix):])
    except json.JSONDecodeError as error:
        raise GateFailure(
            "broker network rootful result is invalid") from error
    if (
            not isinstance(result, dict) or
            set(result) != {
                "schema", "passed", "checks", "identities", "boundary"} or
            result.get("schema") !=
            "hepta.broker-network-rootful-probe.v2" or
            result.get("passed") is not True or
            result.get("checks") != {
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
            } or
            result.get("identities") != {
                "agent_uid": 2004,
                "gateway_uid": 2001,
                "simulator_execution_uid": 2002,
                "ib_execution_uid": 2003,
                "domain_agent_uids": [2104, 2105],
                "domain_gateway_uids": [2101, 2102],
                "domain_simulator_execution_uids": [2111, 2112],
            } or
            result.get("boundary") != {
                "sentinel_only": True,
                "real_broker_connections": 0,
                "broker_protocol_messages": 0,
                "paper_orders": 0,
                "paper_authorized": False,
                "live_authorized": False,
            }):
        fail("broker network rootful result contract mismatch")


def _require_broker_deny_all() -> None:
    expected = re.compile(
        r"^hepta_broker_egress_policy: PASS "
        r"policy_sha256=[0-9a-f]{64} "
        r"authorized_connectors=0 authorized_uids= protected_ports=4\n$")
    for attempt in range(2):
        state = show_properties(
            BROKER_EGRESS_UNIT, ("ActiveState", "Result", "MainPID"))
        if (
                state["ActiveState"] != "failed" or
                state["Result"] != "watchdog" or
                state["MainPID"] != "0"):
            fail("broker policy did not remain failed during deny-all proof")
        checked = command(
            [BROKER_EGRESS_POLICY, "--check-deny-all"],
            timeout=30,
            check=False,
        )
        if (
                checked.returncode != 0 or checked.stderr or
                expected.fullmatch(checked.stdout) is None):
            fail("watchdog failure did not retain exact broker deny-all")
        if attempt == 0:
            time.sleep(0.5)


def validate_broker_watchdog_fail_closed(
        generation: int, generation_holder: list[int]) -> int:
    policy_contract = show_properties(
        BROKER_EGRESS_UNIT,
        (
            "ActiveState", "Result", "MainPID", "WatchdogUSec",
            "TimeoutStopUSec", "Restart", "NRestarts",
        ),
    )
    gateway_contract = show_properties(
        "hepta-tool-gateway.service", ("ActiveState", "BindsTo", "After"))
    if (
            policy_contract["ActiveState"] != "active" or
            policy_contract["Result"] != "success" or
            policy_contract["WatchdogUSec"] != "15s" or
            policy_contract["TimeoutStopUSec"] != "30s" or
            policy_contract["Restart"] != "no" or
            policy_contract["NRestarts"] != "0" or
            BROKER_EGRESS_UNIT not in
            set(gateway_contract["BindsTo"].split()) or
            BROKER_EGRESS_UNIT not in
            set(gateway_contract["After"].split()) or
            gateway_contract["ActiveState"] != "active"):
        fail("effective broker watchdog/Gateway binding contract mismatch")
    try:
        policy_pid = int(policy_contract["MainPID"], 10)
    except ValueError as error:
        raise GateFailure("broker policy MainPID is malformed") from error
    process_identity(policy_pid, 0, 0)
    gateway_pid = service_pid(
        "hepta-tool-gateway.service", 2001, 2001)

    fault_started = time.monotonic()
    os.kill(policy_pid, signal.SIGSTOP)
    wait_process_state(policy_pid, "T")

    deadline = fault_started + 65.0
    failed: Optional[dict[str, str]] = None
    while time.monotonic() < deadline:
        observed = show_properties(
            BROKER_EGRESS_UNIT,
            (
                "ActiveState", "SubState", "Result", "MainPID",
                "NRestarts",
            ),
        )
        if observed["ActiveState"] == "failed":
            failed = observed
            break
        time.sleep(0.1)
    if failed is None:
        fail("real broker watchdog did not terminate the stopped main process")
    fault_elapsed = time.monotonic() - fault_started
    if (
            failed != {
                "ActiveState": "failed",
                "SubState": "failed",
                "Result": "watchdog",
                "MainPID": "0",
                "NRestarts": "0",
            } or
            fault_elapsed < 10.0 or fault_elapsed > 60.0):
        fail("broker watchdog/TimeoutStop failure chain drifted")

    wait_active("hepta-tool-gateway.service", False, timeout=20.0)
    gateway_stopped = show_properties(
        "hepta-tool-gateway.service", ("ActiveState", "MainPID"))
    if (
            gateway_stopped["ActiveState"] not in {"inactive", "failed"} or
            gateway_stopped["MainPID"] != "0" or
            Path(f"/proc/{gateway_pid}").exists()):
        fail("Gateway BindsTo did not stop the active Gateway process")
    _require_broker_deny_all()

    systemctl("reset-failed", BROKER_EGRESS_UNIT)
    systemctl("start", BROKER_EGRESS_UNIT)
    wait_active(BROKER_EGRESS_UNIT, True)
    restarted_policy_pid = service_pid(BROKER_EGRESS_UNIT, 0, 0)
    if restarted_policy_pid == policy_pid:
        fail("broker policy restart reused the stopped main process")
    restarted_contract = show_properties(
        BROKER_EGRESS_UNIT,
        (
            "ActiveState", "Result", "WatchdogUSec", "TimeoutStopUSec",
            "Restart", "NRestarts",
        ),
    )
    if restarted_contract != {
            "ActiveState": "active",
            "Result": "success",
            "WatchdogUSec": "15s",
            "TimeoutStopUSec": "30s",
            "Restart": "no",
            "NRestarts": "0",
            }:
        fail("broker policy did not cleanly restore its watchdog contract")
    if unit_active("hepta-tool-gateway.service"):
        fail("broker policy restart unexpectedly bypassed socket activation")

    # The old token is retained only long enough to prove that the restarted
    # Gateway durably fenced it.  This probe socket-activates a fresh Gateway
    # and must return the exact terminal session reason.
    require_watch_restart_fenced()
    wait_active("hepta-tool-gateway.service", True)
    if service_pid(
            "hepta-tool-gateway.service", 2001, 2001) == gateway_pid:
        fail("watchdog recovery reused the stopped Gateway process")
    revoke_watch(generation)
    fresh_generation = bootstrap_watch(generation_holder)
    runtime_preflight(attempts=8)
    validate_broker_network_policy()
    return fresh_generation


def _domain_records() -> dict[str, dict[str, int | str]]:
    records: dict[str, dict[str, int | str]] = {}
    for (
            domain_id, gateway_name, gateway_uid, gateway_gid,
            agent_name, agent_uid, agent_gid,
            execution_name, execution_uid, execution_gid,
    ) in DOMAIN_IDENTITIES:
        reader_name, reader_uid, reader_gid = DOMAIN_READERS[domain_id]
        records[domain_id] = {
            "domain_id": domain_id,
            "gateway_name": gateway_name,
            "gateway_uid": gateway_uid,
            "gateway_gid": gateway_gid,
            "agent_name": agent_name,
            "agent_uid": agent_uid,
            "agent_gid": agent_gid,
            "execution_name": execution_name,
            "execution_uid": execution_uid,
            "execution_gid": execution_gid,
            "reader_name": reader_name,
            "reader_uid": reader_uid,
            "reader_gid": reader_gid,
        }
    return records


def _regular_root_file(path: Path, mode: int, gid: int = 0) -> bytes:
    before = os.lstat(path)
    if (
            not stat.S_ISREG(before.st_mode) or
            stat.S_ISLNK(before.st_mode) or
            before.st_uid != 0 or before.st_gid != gid or before.st_nlink != 1 or
            stat.S_IMODE(before.st_mode) != mode or
            before.st_size < 1 or before.st_size > 65536):
        fail("trust-domain runtime artifact metadata is unsafe")
    descriptor = os.open(
        path, os.O_RDONLY | os.O_CLOEXEC | getattr(os, "O_NOFOLLOW", 0))
    try:
        opened = os.fstat(descriptor)
        contents = bytearray()
        while len(contents) <= 65536:
            chunk = os.read(descriptor, 8192)
            if not chunk:
                break
            contents.extend(chunk)
        after = os.fstat(descriptor)
    finally:
        os.close(descriptor)
    identity = lambda item: (
        item.st_dev, item.st_ino, item.st_mode, item.st_nlink,
        item.st_uid, item.st_gid, item.st_size,
        item.st_mtime_ns, item.st_ctime_ns,
    )
    if (
            identity(before) != identity(opened) or
            identity(opened) != identity(after) or
            len(contents) != before.st_size):
        fail("trust-domain runtime artifact changed while reading")
    return bytes(contents)


def _environment(path: Path, mode: int = 0o644) -> dict[str, str]:
    try:
        lines = _regular_root_file(path, mode).decode(
            "ascii", errors="strict").splitlines()
    except UnicodeDecodeError as error:
        raise GateFailure("trust-domain environment is not ASCII") from error
    values: dict[str, str] = {}
    for line in lines:
        if (
                not line or line.startswith("#") or line.count("=") != 1):
            fail("trust-domain environment is malformed")
        key, value = line.split("=", 1)
        if not key or not value or key in values:
            fail("trust-domain environment is malformed")
        values[key] = value
    return values


def validate_domain_runtime_artifacts() -> None:
    for domain_id, record in _domain_records().items():
        agent_uid = int(record["agent_uid"])
        domain_path = Path(
            f"/etc/heptatrader/trust-domains/{domain_id}.json")
        uid_path = Path(
            f"/etc/heptatrader/trust-domains/uid-{agent_uid}.json")
        domain_contents = _regular_root_file(domain_path, 0o600)
        uid_contents = _regular_root_file(
            uid_path, 0o640, int(record["agent_gid"]))
        domain_metadata = os.lstat(domain_path)
        uid_metadata = os.lstat(uid_path)
        if (
                domain_contents != uid_contents or
                (domain_metadata.st_dev, domain_metadata.st_ino) ==
                (uid_metadata.st_dev, uid_metadata.st_ino)):
            fail("per-UID runtime config is aliased or drifted")
        try:
            runtime = json.loads(domain_contents.decode(
                "utf-8", errors="strict"))
        except (UnicodeDecodeError, json.JSONDecodeError) as error:
            raise GateFailure(
                "trust-domain runtime config is invalid") from error
        required = {
            "schema": "hepta.agent-trust-domain-runtime.v1",
            "version": 1,
            "domain_id": domain_id,
            "gateway_name": record["gateway_name"],
            "gateway_uid": record["gateway_uid"],
            "gateway_gid": record["gateway_gid"],
            "agent_name": record["agent_name"],
            "agent_uid": agent_uid,
            "agent_gid": record["agent_gid"],
            "execution_name": record["execution_name"],
            "execution_uid": record["execution_uid"],
            "execution_gid": record["execution_gid"],
            "execution_gateway_uid": record["gateway_uid"],
            "execution_gateway_agent_id": domain_id,
            "single_domain_compatibility": False,
            "paper_authorized": False,
            "live_authorized": False,
        }
        if (
                not isinstance(runtime, dict) or
                any(runtime.get(key) != value
                    for key, value in required.items())):
            fail("trust-domain runtime identity binding drifted")

        dropin_path = Path(
            f"/etc/heptatrader/trust-domains/{domain_id}.agent-host.conf")
        dropin = _regular_root_file(dropin_path, 0o644).decode(
            "utf-8", errors="strict")
        expected_dropin = (
            "# Apply to exactly one reviewed Codex/OpenClaw host service.\n"
            "# This fragment grants neither broker access nor trading authority.\n"
            "[Unit]\n"
            "BindsTo=hepta-broker-egress-policy.service\n"
            "After=hepta-broker-egress-policy.service\n"
            "\n"
            "[Service]\n"
            f"User={record['agent_name']}\n"
            f"Group={record['agent_name']}\n"
            "SupplementaryGroups=\n"
            "UMask=0077\n"
            "NoNewPrivileges=yes\n"
            "CapabilityBoundingSet=\n"
            "AmbientCapabilities=\n"
            "RestrictNamespaces=yes\n"
            "RestrictAddressFamilies=AF_UNIX AF_INET AF_INET6\n"
            "Environment=HEPTA_AGENT_DOMAIN_CONFIG="
            f"/etc/heptatrader/trust-domains/uid-{agent_uid}.json\n"
        )
        if dropin != expected_dropin:
            fail("per-domain Agent host drop-in drifted")

        gateway = _environment(Path(
            f"/etc/heptatrader/trust-domains/{domain_id}.env"))
        execution = _environment(Path(
            f"/etc/heptatrader/trust-domains/{domain_id}.execution.env"))
        watch = _environment(
            Path(
                f"/etc/heptatrader/trust-domains/"
                f"{domain_id}.shadow-watch.env"),
            0o600,
        )
        if (
                gateway.get("HEPTA_TOOL_AGENT_UID") != str(agent_uid) or
                gateway.get("HEPTA_TOOL_ACCOUNT") != "SIM" or
                gateway.get("HEPTA_EXECUTION_DOMAIN_ID") !=
                f"SIM:{domain_id}" or
                gateway.get("HEPTA_EXECUTION_SOCKET") !=
                f"/run/hepta-execution-{domain_id}/execution.sock" or
                gateway.get("HEPTA_EXECUTION_EVENT_SOCKET") !=
                f"/run/hepta-execution-{domain_id}/events.sock" or
                gateway.get("HEPTA_TOOL_ALLOW_TRADE") != "0" or
                gateway.get("HEPTA_TOOL_SESSION_TEMPLATES") != "watch" or
                gateway.get("HEPTA_TOOL_CONTRACT_BINDINGS") !=
                "EUR.USD|EUR|CASH|IDEALPRO|USD" or
                execution != {
                    "HEPTA_EXECUTION_GATEWAY_UID":
                        str(record["gateway_uid"]),
                    "HEPTA_EXECUTION_GATEWAY_AGENT_ID": domain_id,
                    "HEPTA_EXECUTION_MAX_REQUEST_BYTES": "16384",
                    "HEPTA_EXECUTION_IO_TIMEOUT_MS": "2500",
                } or
                watch != {
                    "HEPTA_SHADOW_AGENT_UID":
                        str(record["agent_uid"]),
                    "HEPTA_SHADOW_AGENT_GID":
                        str(record["agent_gid"]),
                    "HEPTA_SHADOW_READER_UID":
                        str(record["reader_uid"]),
                    "HEPTA_SHADOW_READER_GID":
                        str(record["reader_gid"]),
                }):
            fail("trust-domain Gateway/Execution binding drifted")


def _run_as_identity(
        arguments: list[str], uid: int, gid: int, *,
        input_text: str = "", environment: Optional[dict[str, str]] = None,
        timeout: int = 45) -> subprocess.CompletedProcess[str]:
    def drop_identity() -> None:
        os.setgroups([])
        os.setgid(gid)
        os.setuid(uid)

    completed = subprocess.run(
        arguments,
        input=input_text,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="strict",
        env=environment or ENVIRONMENT,
        cwd="/",
        close_fds=True,
        timeout=timeout,
        check=False,
        preexec_fn=drop_identity,
    )
    if (
            len(completed.stdout.encode("utf-8")) > MAX_OUTPUT or
            len(completed.stderr.encode("utf-8")) > MAX_OUTPUT):
        fail("identity probe output exceeded the bound")
    return completed


def _mcp_requests() -> str:
    requests = (
        {
            "jsonrpc": "2.0", "id": 1, "method": "initialize",
            "params": {"protocolVersion": "2025-03-26"},
        },
        {
            "jsonrpc": "2.0", "id": 2, "method": "tools/list",
            "params": {},
        },
        {
            "jsonrpc": "2.0", "id": 3, "method": "tools/call",
            "params": {
                "name": "system.get_health", "arguments": {},
            },
        },
        {
            "jsonrpc": "2.0", "id": 4, "method": "tools/call",
            "params": {
                "name": "account.get_summary", "arguments": {},
            },
        },
        {
            "jsonrpc": "2.0", "id": 5, "method": "tools/call",
            "params": {
                "name": "watch.get_snapshot",
                "arguments": {"instrument": "EUR.USD"},
            },
        },
    )
    return "".join(
        json.dumps(item, sort_keys=True, separators=(",", ":")) + "\n"
        for item in requests)


def _mcp_completion(
        record: dict[str, int | str], *,
        environment: Optional[dict[str, str]] = None,
        command_path: str = MCP_LAUNCHER) -> subprocess.CompletedProcess[str]:
    child_environment = dict(ENVIRONMENT)
    child_environment["HEPTA_MCP_TIMEOUT_SEC"] = "10"
    if environment:
        child_environment.update(environment)
    return _run_as_identity(
        [command_path],
        int(record["agent_uid"]),
        int(record["agent_gid"]),
        input_text=_mcp_requests(),
        environment=child_environment,
    )


def _tool_payload(
        response: dict[str, Any], tool_name: str) -> dict[str, Any]:
    called = response.get("result")
    if (
            not isinstance(called, dict) or
            called.get("isError") is not False or
            not isinstance(called.get("structuredContent"), dict)):
        fail("domain MCP read call was rejected")
    envelope = called["structuredContent"]
    if (
            envelope.get("status") != "ok" or
            envelope.get("tool") != tool_name or
            envelope.get("reason_code") != "" or
            not isinstance(envelope.get("payload"), dict)):
        fail("domain MCP read envelope is invalid")
    return envelope["payload"]


def parse_domain_mcp(
        completed: subprocess.CompletedProcess[str],
        domain_id: str) -> dict[str, Any]:
    if completed.returncode != 0 or completed.stderr:
        fail("domain MCP launcher failed")
    lines = completed.stdout.splitlines()
    if len(lines) != 5:
        fail("domain MCP response count is invalid")
    try:
        responses = [json.loads(line) for line in lines]
    except json.JSONDecodeError as error:
        raise GateFailure("domain MCP response is invalid JSON") from error
    for expected_id, response in enumerate(responses, 1):
        if (
                not isinstance(response, dict) or
                response.get("jsonrpc") != "2.0" or
                response.get("id") != expected_id or
                "error" in response or
                not isinstance(response.get("result"), dict)):
            fail("domain MCP request failed")
    if (
            (responses[0]["result"].get("serverInfo") or {}).get("name") !=
            "heptatrader"):
        fail("domain MCP initialization identity mismatch")
    tools = responses[1]["result"].get("tools")
    if (
            not isinstance(tools, list) or
            {item.get("name") for item in tools if isinstance(item, dict)} !=
            WATCH_TOOL_NAMES or len(tools) != len(WATCH_TOOL_NAMES)):
        fail("domain MCP WATCH tool surface drifted")
    health = _tool_payload(responses[2], "system.get_health")
    account = _tool_payload(responses[3], "account.get_summary")
    snapshot = _tool_payload(responses[4], "watch.get_snapshot")
    snapshot_tools = {
        "system.get_health", "account.get_summary",
        "portfolio.list_positions", "orders.list", "risk.get_limits",
        "market.get_quote",
    }
    reads = snapshot.get("reads")
    descriptors = snapshot.get("descriptors")
    finished = snapshot.get("read_finished_at_ms")
    quote = reads.get("market.get_quote") if isinstance(reads, dict) else None
    if (
            health.get("gateway_ready") is not True or
            health.get("remote_execution_ready") is not True or
            health.get("execution_mode") != "SIMULATOR" or
            health.get("paper_template_enabled") is not False or
            account.get("account") != "SIM" or
            set(snapshot) != {
                "schema", "catalog", "descriptors", "reads",
                "read_finished_at_ms"} or
            snapshot.get("schema") != "hepta.watch-read-set.v1" or
            not isinstance(snapshot.get("catalog"), dict) or
            not isinstance(descriptors, dict) or
            set(descriptors) != snapshot_tools or
            not isinstance(reads, dict) or set(reads) != snapshot_tools or
            not isinstance(finished, dict) or set(finished) != snapshot_tools or
            not all(type(value) is int and value > 0
                    for value in finished.values()) or
            not isinstance(quote, dict) or
            quote.get("instrument") != "EUR.USD" or
            quote.get("authoritative") is not True or
            quote.get("stale") is not False or
            type(quote.get("bid")) not in (int, float) or
            type(quote.get("ask")) not in (int, float) or
            not math.isfinite(quote["bid"]) or
            not math.isfinite(quote["ask"]) or
            quote["bid"] <= 0 or quote["ask"] < quote["bid"]):
        fail("domain MCP remote Execution identity is not ready")
    return {
        "domain_id": domain_id,
        "execution_service_epoch": health.get("execution_service_epoch"),
        "execution_service_fencing_generation":
            health.get("execution_service_fencing_generation"),
        "account": account["account"],
    }


def domain_mcp_probe(record: dict[str, int | str]) -> dict[str, Any]:
    return parse_domain_mcp(
        _mcp_completion(record), str(record["domain_id"]))


def _bootstrap_domain(
        record: dict[str, int | str], operation: str, *,
        generation: Optional[int] = None,
        agent_id: Optional[str] = None) -> subprocess.CompletedProcess[str]:
    arguments = [
        BOOTSTRAP,
        "--domain-config",
        f"/etc/heptatrader/trust-domains/{record['domain_id']}.json",
        operation,
    ]
    if operation == "provision-watch":
        arguments.extend([
            "--agent-id", agent_id or str(record["domain_id"]),
            "--session-id", f"{record['domain_id']}-rootful-e2e",
            "--ttl-sec", "900",
        ])
    else:
        if generation is None:
            fail("domain revoke generation is missing")
        arguments.extend(["--generation", str(generation)])
    return command(arguments, timeout=30, check=False)


def provision_domain_watch(
        record: dict[str, int | str],
        generation_holder: dict[str, int]) -> int:
    completed = _bootstrap_domain(record, "provision-watch")
    if (
            completed.returncode != 0 or completed.stderr or
            len(completed.stdout.splitlines()) != 1):
        fail("domain WATCH bootstrap failed")
    try:
        result = json.loads(completed.stdout)
    except json.JSONDecodeError as error:
        raise GateFailure("domain WATCH bootstrap output is invalid") from error
    expected_fields = {
        "schema", "accepted", "operation", "trust_domain", "peer_uid",
        "lease_generation", "paper_authorized", "live_authorized",
    }
    if (
            not isinstance(result, dict) or set(result) != expected_fields or
            result.get("schema") != "hepta.agent-session-bootstrap.v1" or
            result.get("accepted") is not True or
            result.get("operation") != "provision-watch" or
            result.get("trust_domain") != record["domain_id"] or
            result.get("peer_uid") != record["agent_uid"] or
            result.get("paper_authorized") is not False or
            result.get("live_authorized") is not False):
        fail("domain WATCH bootstrap authority contract mismatch")
    generation = result.get("lease_generation")
    if type(generation) is not int or generation < 1:
        fail("domain WATCH generation is invalid")
    generation_holder[str(record["domain_id"])] = generation
    return generation


def revoke_domain_watch(
        record: dict[str, int | str], generation: int) -> None:
    completed = _bootstrap_domain(
        record, "revoke", generation=generation)
    if (
            completed.returncode != 0 or completed.stderr or
            len(completed.stdout.splitlines()) != 1):
        fail("domain WATCH revoke failed")
    result = json.loads(completed.stdout)
    if (
            result.get("schema") != "hepta.agent-session-bootstrap.v1" or
            result.get("accepted") is not True or
            result.get("operation") != "revoke" or
            result.get("trust_domain") != record["domain_id"] or
            result.get("peer_uid") != record["agent_uid"] or
            result.get("lease_generation") != generation or
            result.get("paper_authorized") is not False or
            result.get("live_authorized") is not False):
        fail("domain WATCH revoke authority contract mismatch")


def _custodian_command(
        record: dict[str, int | str],
        *arguments: str,
) -> dict[str, Any]:
    completed = command([
        CUSTODIAN,
        "--domain-config",
        f"/etc/heptatrader/trust-domains/{record['domain_id']}.json",
        *arguments,
    ], timeout=45, check=False)
    if (
            completed.returncode != 0 or completed.stderr or
            len(completed.stdout.splitlines()) != 1):
        fail("WATCH custodian command failed")
    try:
        result = json.loads(completed.stdout)
    except json.JSONDecodeError as error:
        raise GateFailure("WATCH custodian output is invalid") from error
    canonical = (
        json.dumps(
            result, ensure_ascii=True, allow_nan=False,
            sort_keys=True, separators=(",", ":"),
        ) + "\n"
    )
    if not isinstance(result, dict) or completed.stdout != canonical:
        fail("WATCH custodian output is not canonical")
    return result


def _spawn_reader_owner(record: dict[str, int | str]) -> int:
    reader, writer = os.pipe()
    pid = os.fork()
    if pid == 0:
        try:
            os.close(reader)
            os.setgroups([])
            os.setgid(int(record["reader_gid"]))
            os.setuid(int(record["reader_uid"]))
            if os.write(writer, b"R") != 1:
                os._exit(1)
            os.close(writer)
            while True:
                signal.pause()
        except BaseException:
            os._exit(1)
    os.close(writer)
    try:
        ready = os.read(reader, 2)
    finally:
        os.close(reader)
    if ready != b"R":
        try:
            os.waitpid(pid, 0)
        except ChildProcessError:
            pass
        fail("WATCH reader owner failed to start")
    process_identity(
        pid, int(record["reader_uid"]), int(record["reader_gid"]))
    return pid


def _stop_reader_owner(pid: int) -> None:
    try:
        os.kill(pid, signal.SIGTERM)
    except ProcessLookupError:
        pass
    deadline = time.monotonic() + 3.0
    while True:
        try:
            completed, _status = os.waitpid(pid, os.WNOHANG)
        except ChildProcessError:
            return
        if completed == pid:
            return
        if time.monotonic() >= deadline:
            try:
                os.kill(pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            try:
                os.waitpid(pid, 0)
            except ChildProcessError:
                pass
            return
        time.sleep(0.02)


def _validate_custodian_registration(
        result: dict[str, Any],
        record: dict[str, int | str],
        campaign_id: str,
) -> None:
    if (
            result.get("schema") !=
            "hepta.shadow-watch-custodian-registration.v1" or
            result.get("status") != "REGISTERED" or
            result.get("domain_id") != record["domain_id"] or
            result.get("campaign_id") != campaign_id or
            result.get("lease_generation") != 1 or
            type(result.get("lease_expires_at_ms")) is not int or
            result.get("paper_authorized") is not False or
            result.get("live_authorized") is not False or
            result.get("mutation_authorized") is not False or
            result.get("direct_broker_access") is not False or
            DIGEST.fullmatch(str(result.get("state_body_sha256"))) is None or
            DIGEST.fullmatch(
                str(result.get("preparing_state_body_sha256"))) is None):
        fail("WATCH custodian registration contract mismatch")


def _validate_custodian_rotation(
        result: dict[str, Any],
        record: dict[str, int | str],
        campaign_id: str,
) -> None:
    if (
            result.get("schema") !=
            "hepta.shadow-watch-custodian-rotation.v1" or
            result.get("status") != "ROTATED" or
            result.get("domain_id") != record["domain_id"] or
            result.get("campaign_id") != campaign_id or
            result.get("previous_lease_generation") != 1 or
            result.get("lease_generation") != 2 or
            result.get("previous_authority_outcome") != "ROTATED" or
            type(result.get("lease_expires_at_ms")) is not int or
            result.get("paper_authorized") is not False or
            result.get("live_authorized") is not False or
            result.get("mutation_authorized") is not False or
            result.get("direct_broker_access") is not False or
            DIGEST.fullmatch(
                str(result.get("lease_receipt_body_sha256"))) is None or
            DIGEST.fullmatch(str(result.get("state_body_sha256"))) is None):
        fail("WATCH custodian rotation contract mismatch")


def _custodian_runtime_evidence(
        record: dict[str, int | str],
) -> dict[str, Any]:
    domain_id = str(record["domain_id"])
    runtime = Path(f"/run/hepta-agent-{domain_id}/sessions")
    fence = _regular_root_file(
        runtime / ".session-fence.token", 0o600)
    receipt_bytes = _regular_root_file(
        runtime / "shadow-watch-lease-receipt.json",
        0o440,
        int(record["agent_gid"]),
    )
    try:
        receipt = json.loads(receipt_bytes.decode(
            "ascii", errors="strict"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise GateFailure(
            "WATCH rotation receipt is invalid") from error
    if (
            not isinstance(receipt, dict) or
            receipt.get("schema") !=
            "hepta.shadow-watch-lease-receipt.v1" or
            receipt.get("operation") != "ROTATE" or
            receipt.get("lease_generation") != 2 or
            receipt.get("previous_lease_generation") != 1 or
            receipt.get("accepted") is not True or
            receipt.get("paper_authorized") is not False or
            receipt.get("live_authorized") is not False or
            receipt.get("mutation_authorized") is not False or
            DIGEST.fullmatch(str(receipt.get("body_sha256"))) is None):
        fail("WATCH rotation receipt binding mismatch")
    return {
        "fence_bytes": fence,
        "fence_sha256": "sha256:" + hashlib.sha256(fence).hexdigest(),
        "lease_receipt_body_sha256": receipt["body_sha256"],
        "config_sha256": "sha256:" + hashlib.sha256(_regular_root_file(
            Path(
                f"/etc/heptatrader/trust-domains/{domain_id}.json"),
            0o600,
        )).hexdigest(),
        "watch_environment_sha256":
            "sha256:" + hashlib.sha256(_regular_root_file(
                Path(
                    f"/etc/heptatrader/trust-domains/"
                    f"{domain_id}.shadow-watch.env"),
                0o600,
            )).hexdigest(),
    }


def _validate_custodian_closure(
        record: dict[str, int | str],
        campaign_id: str,
        owner_pid: int,
        evidence: dict[str, Any],
) -> int:
    domain_id = str(record["domain_id"])
    state = DOMAIN_CUSTODIAN_STATE_ROOT / domain_id
    closure_directory = state / "closures"
    paths = sorted(closure_directory.iterdir())
    expected = closure_directory / f"{campaign_id}.json"
    if paths != [expected]:
        fail("WATCH custodian closure inventory mismatch")
    contents = _regular_root_file(expected, 0o600)
    try:
        closure = json.loads(contents.decode("ascii", errors="strict"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise GateFailure(
            "WATCH custodian closure is invalid") from error
    canonical = (
        json.dumps(
            closure, ensure_ascii=True, allow_nan=False,
            sort_keys=True, separators=(",", ":"),
        ) + "\n"
    ).encode("ascii")
    body = dict(closure) if isinstance(closure, dict) else {}
    claimed = body.pop("body_sha256", None)
    body_digest = "sha256:" + hashlib.sha256((
        json.dumps(
            body, ensure_ascii=True, allow_nan=False,
            sort_keys=True, separators=(",", ":"),
        ) + "\n"
    ).encode("ascii")).hexdigest()
    if (
            not isinstance(closure, dict) or
            set(closure) != CUSTODIAN_CLOSURE_FIELDS or
            contents != canonical or claimed != body_digest or
            closure.get("schema") !=
            "hepta.shadow-watch-custodian-closure.v1" or
            closure.get("version") != 1 or
            closure.get("domain_id") != domain_id or
            closure.get("campaign_id") != campaign_id or
            closure.get("config_sha256") != evidence["config_sha256"] or
            closure.get("watch_environment_sha256") !=
            evidence["watch_environment_sha256"] or
            closure.get("token_directory") !=
            f"/run/hepta-agent-{domain_id}/sessions" or
            closure.get("supervisor_socket") !=
            f"/run/hepta-tool-gateway-{domain_id}/"
            "session-supervisor.sock" or
            closure.get("agent_uid") != record["agent_uid"] or
            closure.get("agent_gid") != record["agent_gid"] or
            closure.get("gateway_uid") != record["gateway_uid"] or
            closure.get("execution_uid") != record["execution_uid"] or
            closure.get("owner_pid") != owner_pid or
            closure.get("owner_uid") != record["reader_uid"] or
            closure.get("owner_gid") != record["reader_gid"] or
            closure.get("lease_generation") != 2 or
            closure.get("lease_receipt_body_sha256") !=
            evidence["lease_receipt_body_sha256"] or
            closure.get("fence_token_sha256") !=
            evidence["fence_sha256"] or
            closure.get("close_reason") not in {
                "service-stop", "service-stop-post", "custodian-restart"} or
            closure.get("authoritative_revoke_outcome") != "ACCEPTED" or
            closure.get("local_authority_removed") is not True or
            closure.get("export_evidence_removed") is not True or
            closure.get("paper_authorized") is not False or
            closure.get("live_authorized") is not False or
            closure.get("mutation_authorized") is not False or
            closure.get("direct_broker_access") is not False or
            evidence["fence_bytes"] in contents):
        fail("WATCH custodian closure contract mismatch")
    for field in (
            "registered_at_ms", "close_started_at_ms", "closed_at_ms",
            "lease_expires_at_ms", "owner_start_ticks"):
        if type(closure.get(field)) is not int:
            fail("WATCH custodian closure numeric contract mismatch")
    if not (
            0 <= closure["registered_at_ms"] <=
            closure["close_started_at_ms"] <= closure["closed_at_ms"] <
            closure["lease_expires_at_ms"]):
        fail("WATCH custodian closure time contract mismatch")
    return len(paths)


def _require_custodian_authority_absent(
        record: dict[str, int | str],
) -> None:
    domain_id = str(record["domain_id"])
    runtime = Path(f"/run/hepta-agent-{domain_id}/sessions")
    forbidden = {
        "session.token", ".session-fence.token",
        "shadow-watch-lease-receipt.json",
    }
    if runtime.exists():
        for path in runtime.iterdir():
            if (
                    path.name in forbidden or
                    path.name.startswith(".session-token-") or
                    path.name.startswith(".session-fence-")):
                fail("WATCH custodian left local authority residue")
    export = Path(f"/run/hepta-shadow-watch-export-{domain_id}")
    if export.exists() or export.is_symlink():
        fail("WATCH custodian left exported evidence residue")
    transaction = (
        DOMAIN_CUSTODIAN_STATE_ROOT / domain_id / "transaction.json")
    if transaction.exists() or transaction.is_symlink():
        fail("WATCH custodian left an active transaction")


def validate_two_domain_custodian_crash(
        records: dict[str, dict[str, int | str]],
        generations: dict[str, int],
        generation_holder: dict[str, int],
) -> dict[str, dict[str, int]]:
    for domain_id, record in records.items():
        revoke_domain_watch(record, generations[domain_id])
        generation_holder.pop(domain_id, None)

    owners: dict[str, int] = {}
    metrics: dict[str, dict[str, int]] = {}
    campaigns = {
        domain_id: f"{domain_id}-rootful-custodian-crash"
        for domain_id in records
    }
    evidence: dict[str, dict[str, Any]] = {}
    try:
        for domain_id, record in records.items():
            owners[domain_id] = _spawn_reader_owner(record)
            provisioned = _custodian_command(
                record,
                "provision",
                "--campaign-id", campaigns[domain_id],
                "--owner-pid", str(owners[domain_id]),
                "--owner-uid", str(record["reader_uid"]),
                "--ttl-sec", str(DOMAIN_CUSTODIAN_TTL_SECONDS),
            )
            _validate_custodian_registration(
                provisioned, record, campaigns[domain_id])
            generation_holder[domain_id] = 1

        for domain_id in records:
            timer = (
                f"hepta-shadow-watch-custodian-reconcile@"
                f"{domain_id}.timer")
            service = f"hepta-shadow-watch-custodian@{domain_id}.service"
            systemctl("enable", "--runtime", "--now", timer)
            systemctl("start", service)
            timer_state = show_properties(
                timer, ("ActiveState", "UnitFileState", "Triggers"))
            service_state = show_properties(
                service,
                ("ActiveState", "MainPID", "Restart", "NRestarts"))
            if (
                    timer_state["ActiveState"] != "active" or
                    timer_state["UnitFileState"] != "enabled-runtime" or
                    f"hepta-shadow-watch-custodian-reconcile@"
                    f"{domain_id}.service" not in
                    timer_state["Triggers"].split() or
                    service_state["ActiveState"] != "active" or
                    service_state["Restart"] != "on-failure" or
                    service_state["NRestarts"] != "0"):
                fail("WATCH custodian systemd monitor contract mismatch")
            custodian_pid = service_pid(service, 0, 0)
            metrics[domain_id] = {
                "custodian_pid": custodian_pid,
                "reader_owner_pid": owners[domain_id],
                "custodian_crash_generation": 2,
            }

        for domain_id, record in records.items():
            rotated = _custodian_command(
                record,
                "rotate",
                "--campaign-id", campaigns[domain_id],
                "--current-generation", "1",
                "--ttl-sec", str(DOMAIN_CUSTODIAN_TTL_SECONDS),
            )
            _validate_custodian_rotation(
                rotated, record, campaigns[domain_id])
            generation_holder[domain_id] = 2
            evidence[domain_id] = _custodian_runtime_evidence(record)
            domain_mcp_probe(record)

        for domain_id in records:
            service = f"hepta-shadow-watch-custodian@{domain_id}.service"
            systemctl(
                "kill", "--kill-whom=main", "--signal=SIGKILL", service)

        for domain_id, record in records.items():
            state = DOMAIN_CUSTODIAN_STATE_ROOT / domain_id
            closure = state / "closures" / f"{campaigns[domain_id]}.json"
            transaction = state / "transaction.json"
            deadline = time.monotonic() + 45.0
            while transaction.exists() or not closure.exists():
                if time.monotonic() >= deadline:
                    fail("WATCH custodian crash did not converge")
                time.sleep(0.1)
            service = f"hepta-shadow-watch-custodian@{domain_id}.service"
            deadline = time.monotonic() + 20.0
            while True:
                service_state = show_properties(
                    service, ("ActiveState", "MainPID", "NRestarts"))
                if (
                        service_state["ActiveState"] in {
                            "inactive", "failed"} and
                        service_state["MainPID"] == "0"):
                    break
                if time.monotonic() >= deadline:
                    fail("WATCH custodian service did not terminalize")
                time.sleep(0.1)
            try:
                restart_count = int(service_state["NRestarts"], 10)
            except ValueError as error:
                raise GateFailure(
                    "WATCH custodian restart count is malformed") from error
            if restart_count < 1:
                fail("WATCH custodian SIGKILL did not exercise restart")
            metrics[domain_id]["custodian_restart_count"] = restart_count
            metrics[domain_id]["closure_receipt_count"] = (
                _validate_custodian_closure(
                    record,
                    campaigns[domain_id],
                    owners[domain_id],
                    evidence[domain_id],
                )
            )
            _require_custodian_authority_absent(record)
            generation_holder.pop(domain_id, None)

        for domain_id in records:
            systemctl(
                "disable", "--runtime", "--now",
                f"hepta-shadow-watch-custodian-reconcile@"
                f"{domain_id}.timer")
        return metrics
    finally:
        for pid in owners.values():
            _stop_reader_owner(pid)


def require_domain_watch_restart_fenced(
        record: dict[str, int | str]) -> None:
    domain_id = str(record["domain_id"])
    completed = _run_as_identity([
        "/usr/bin/heptactl",
        "--socket", f"/run/hepta-agent-{domain_id}/tools.sock",
        "--token-file",
        f"/run/hepta-agent-{domain_id}/sessions/session.token",
        "tools", "list",
    ], int(record["agent_uid"]), int(record["agent_gid"]))
    try:
        result = json.loads(completed.stdout)
    except json.JSONDecodeError as error:
        raise GateFailure(
            "restarted domain WATCH rejection was not typed") from error
    if (
            completed.returncode != 3 or completed.stderr or
            not isinstance(result, dict) or
            set(result) != {
                "status", "tool", "reason_code", "detail", "order_id",
                "payload"} or
            result.get("status") != "permission_denied" or
            result.get("tool") != "system.tools.list" or
            result.get("reason_code") != "SESSION_NOT_FOUND" or
            result.get("order_id") != -1 or
            result.get("payload") is not None):
        fail("domain Gateway restart did not fence prior WATCH authority")


def require_domain_collector_restart_terminal(
        record: dict[str, int | str]) -> None:
    domain_id = str(record["domain_id"])
    output = Path(
        f"/run/hepta-agent-{domain_id}/sessions/"
        "rootful-restart-terminal-probe.json")
    output.unlink(missing_ok=True)
    completed = _run_as_identity([
        SHADOW_WATCH_COLLECTOR,
        "--domain-config",
        f"/etc/heptatrader/trust-domains/uid-{record['agent_uid']}.json",
        "--output", str(output),
        "--instrument", "EUR.USD",
    ], int(record["agent_uid"]), int(record["agent_gid"]))
    if (
            completed.returncode != 78 or completed.stdout or
            completed.stderr != (
                "hepta_shadow_watch_collector: FAIL: "
                "WATCH_SESSION_AUTHORITY_NOT_FOUND\n") or
            output.exists()):
        fail("restarted domain collector did not emit the typed terminal")


def _domain_socket_paths(
        record: dict[str, int | str]) -> dict[str, tuple[Path, int, int]]:
    domain_id = str(record["domain_id"])
    return {
        "tool_socket_inode": (
            Path(f"/run/hepta-agent-{domain_id}/tools.sock"),
            int(record["agent_uid"]), int(record["agent_gid"])),
        "supervisor_socket_inode": (
            Path(
                f"/run/hepta-tool-gateway-{domain_id}/"
                "session-supervisor.sock"),
            int(record["gateway_uid"]), int(record["gateway_gid"])),
        "execution_socket_inode": (
            Path(f"/run/hepta-execution-{domain_id}/execution.sock"),
            int(record["gateway_uid"]), int(record["gateway_gid"])),
        "events_socket_inode": (
            Path(f"/run/hepta-execution-{domain_id}/events.sock"),
            int(record["gateway_uid"]), int(record["gateway_gid"])),
    }


def _domain_socket_inodes(
        record: dict[str, int | str]) -> dict[str, int]:
    inodes: dict[str, int] = {}
    for label, (path, uid, gid) in _domain_socket_paths(record).items():
        metadata = os.lstat(path)
        if (
                not stat.S_ISSOCK(metadata.st_mode) or
                stat.S_IMODE(metadata.st_mode) != 0o600 or
                metadata.st_uid != uid or metadata.st_gid != gid):
            fail("full-chain trust-domain socket metadata mismatch")
        inodes[label] = metadata.st_ino
    return inodes


def _assert_negative_mcp(
        completed: subprocess.CompletedProcess[str],
        domain_id: str, label: str) -> None:
    try:
        parse_domain_mcp(completed, domain_id)
    except GateFailure:
        return
    fail(f"{label} unexpectedly reached a valid domain MCP chain")


def _write_override(path: Path, contents: bytes) -> None:
    path.parent.mkdir(parents=True, mode=0o755)
    descriptor = os.open(
        path,
        os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_CLOEXEC |
        getattr(os, "O_NOFOLLOW", 0),
        0o644,
    )
    try:
        if os.write(descriptor, contents) != len(contents):
            fail("systemd mismatch override short write")
        os.fsync(descriptor)
        os.fchmod(descriptor, 0o644)
        metadata = os.fstat(descriptor)
        if (
                not stat.S_ISREG(metadata.st_mode) or metadata.st_uid != 0 or
                metadata.st_gid != 0 or metadata.st_nlink != 1):
            fail("systemd mismatch override metadata is unsafe")
    finally:
        os.close(descriptor)


def validate_gateway_binding_rejection(
        record: dict[str, int | str], key: str, value: str,
        generation: int, generation_holder: dict[str, int]) -> int:
    domain_id = str(record["domain_id"])
    unit = f"hepta-tool-gateway@{domain_id}.service"
    directory = Path(f"/run/systemd/system/{unit}.d")
    override = directory / "90-hepta-binding-negative.conf"
    if directory.exists() or override.exists() or override.is_symlink():
        fail("systemd mismatch override path already exists")
    _write_override(
        override,
        f"[Service]\nEnvironment={key}={value}\n".encode("ascii"),
    )
    try:
        systemctl("daemon-reload")
        systemctl("restart", unit, check=False)
        _assert_negative_mcp(
            _mcp_completion(record), domain_id,
            f"cross {key} binding")
    finally:
        try:
            override.unlink()
            directory.rmdir()
        except OSError as error:
            raise GateFailure(
                "systemd mismatch override cleanup failed") from error
        systemctl("daemon-reload")
        systemctl("restart", unit)
        wait_active(unit, True)
    require_domain_watch_restart_fenced(record)
    revoke_domain_watch(record, generation)
    fresh_generation = provision_domain_watch(record, generation_holder)
    domain_mcp_probe(record)
    return fresh_generation


def validate_cross_domain_token_rejection(
        source: dict[str, int | str],
        foreign: dict[str, int | str]) -> None:
    source_root = Path(
        f"/run/hepta-agent-{source['domain_id']}/sessions")
    foreign_token = Path(
        f"/run/hepta-agent-{foreign['domain_id']}/sessions/session.token")
    cross = source_root / ".cross-domain-token"
    if cross.exists() or cross.is_symlink():
        fail("cross-domain token fixture already exists")
    before = os.lstat(foreign_token)
    if (
            not stat.S_ISREG(before.st_mode) or
            stat.S_ISLNK(before.st_mode) or
            before.st_uid != int(foreign["agent_uid"]) or
            before.st_gid != int(foreign["agent_gid"]) or
            before.st_nlink != 1 or stat.S_IMODE(before.st_mode) != 0o600 or
            before.st_size < 24 or before.st_size > 512):
        fail("foreign domain token metadata is unsafe")
    source_descriptor = os.open(
        foreign_token,
        os.O_RDONLY | os.O_CLOEXEC | getattr(os, "O_NOFOLLOW", 0))
    try:
        opened = os.fstat(source_descriptor)
        contents = os.read(source_descriptor, 513)
        after = os.fstat(source_descriptor)
    finally:
        os.close(source_descriptor)
    identity = lambda item: (
        item.st_dev, item.st_ino, item.st_mode, item.st_nlink,
        item.st_uid, item.st_gid, item.st_size,
        item.st_mtime_ns, item.st_ctime_ns,
    )
    if (
            identity(before) != identity(opened) or
            identity(opened) != identity(after) or
            len(contents) != before.st_size):
        fail("foreign domain token changed while reading")
    descriptor = os.open(
        cross,
        os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_CLOEXEC |
        getattr(os, "O_NOFOLLOW", 0),
        0o600,
    )
    try:
        if os.write(descriptor, contents) != len(contents):
            fail("cross-domain token fixture short write")
        os.fchown(
            descriptor, int(source["agent_uid"]), int(source["agent_gid"]))
        os.fchmod(descriptor, 0o600)
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
    try:
        completed = _run_as_identity([
            "/usr/bin/heptactl",
            "--socket",
            f"/run/hepta-agent-{source['domain_id']}/tools.sock",
            "--token-file", str(cross),
            "tools", "list",
        ], int(source["agent_uid"]), int(source["agent_gid"]))
        try:
            result = json.loads(completed.stdout)
        except json.JSONDecodeError as error:
            raise GateFailure(
                "foreign domain token rejection was not a typed result"
            ) from error
        if (
                completed.returncode != 3 or completed.stderr or
                not isinstance(result, dict) or
                set(result) != {
                    "status", "tool", "reason_code", "detail", "order_id",
                    "payload"} or
                result.get("status") != "permission_denied" or
                result.get("tool") != "system.tools.list" or
                result.get("reason_code") != "SESSION_NOT_FOUND" or
                result.get("order_id") != -1 or
                result.get("payload") is not None):
            fail("foreign domain token did not reach the expected rejection")
    finally:
        cross.unlink()


def validate_two_domain_full_chain(
        generation_holder: dict[str, int]) -> dict[str, dict[str, Any]]:
    records = _domain_records()
    validate_domain_runtime_artifacts()
    socket_units: list[str] = []
    service_units: list[str] = []
    for domain_id in records:
        socket_units.extend((
            f"hepta-execution-simulator@{domain_id}.socket",
            f"hepta-execution-events-simulator@{domain_id}.socket",
            f"hepta-tool-gateway@{domain_id}.socket",
            f"hepta-tool-session-supervisor@{domain_id}.socket",
        ))
        service_units.extend((
            f"hepta-tool-gateway@{domain_id}.service",
            f"hepta-execution-simulator@{domain_id}.service",
        ))
    systemctl("start", *socket_units)
    for unit in socket_units:
        wait_active(unit, True)
    for unit in service_units:
        if unit_active(unit):
            fail("full-chain socket start eagerly launched a service")

    domain_list = list(records.values())
    for record in domain_list:
        paths = _domain_socket_paths(record)
        other = (
            domain_list[1] if record is domain_list[0] else domain_list[0])
        tool_path = paths["tool_socket_inode"][0]
        supervisor_path = paths["supervisor_socket_inode"][0]
        if (
                _connect_as(
                    tool_path, int(other["agent_uid"]),
                    int(other["agent_gid"])) or
                _connect_as(
                    tool_path, int(record["gateway_uid"]),
                    int(record["gateway_gid"])) or
                _connect_as(
                    supervisor_path, int(record["agent_uid"]),
                    int(record["agent_gid"]))):
            fail("cross UID/domain opened a protected Gateway socket")
        wrong = _bootstrap_domain(
            record, "provision-watch",
            agent_id=str(other["domain_id"]))
        if (
                wrong.returncode != 78 or wrong.stdout or
                "SESSION_SUPERVISOR_REJECTED" not in wrong.stderr):
            fail("cross-domain bootstrap did not fail closed")

    generations = {
        domain_id: provision_domain_watch(record, generation_holder)
        for domain_id, record in records.items()
    }
    probes = {
        domain_id: domain_mcp_probe(record)
        for domain_id, record in records.items()
    }
    epochs = {
        probe["execution_service_epoch"] for probe in probes.values()}
    if None in epochs or len(epochs) != len(probes):
        fail("trust-domain Execution service epochs are not isolated")

    first, second = domain_list
    cross_config = _mcp_completion(first, environment={
        "HEPTA_AGENT_DOMAIN_CONFIG":
            f"/etc/heptatrader/trust-domains/"
            f"uid-{second['agent_uid']}.json",
    })
    if (
            cross_config.returncode != 78 or cross_config.stdout or
            "trust-domain configuration is unsafe" not in cross_config.stderr):
        fail("foreign per-UID runtime config did not fail closed")
    validate_cross_domain_token_rejection(first, second)
    validate_cross_domain_token_rejection(second, first)

    first_id = str(first["domain_id"])
    generations[first_id] = validate_gateway_binding_rejection(
        first, "HEPTA_EXECUTION_DOMAIN_ID",
        f"SIM:{second['domain_id']}", generations[first_id],
        generation_holder)
    generations[first_id] = validate_gateway_binding_rejection(
        first, "HEPTA_TOOL_ACCOUNT", "OTHER", generations[first_id],
        generation_holder)

    # Exercise the same restart-fence/tombstone/exact-revoke/fresh-provision
    # sequence independently for every templated trust domain.
    for domain_id, record in records.items():
        gateway_unit = f"hepta-tool-gateway@{domain_id}.service"
        previous_pid = service_pid(
            gateway_unit,
            int(record["gateway_uid"]), int(record["gateway_gid"]))
        systemctl("restart", gateway_unit)
        wait_active(gateway_unit, True)
        if service_pid(
                gateway_unit,
                int(record["gateway_uid"]),
                int(record["gateway_gid"])) == previous_pid:
            fail("domain Gateway restart retained the old process")
        require_domain_watch_restart_fenced(record)
        require_domain_collector_restart_terminal(record)
        revoke_domain_watch(record, generations[domain_id])
        generations[domain_id] = provision_domain_watch(
            record, generation_holder)
        domain_mcp_probe(record)

    custodian_metrics = validate_two_domain_custodian_crash(
        records, generations, generation_holder)
    generations = {
        domain_id: provision_domain_watch(record, generation_holder)
        for domain_id, record in records.items()
    }
    for record in records.values():
        domain_mcp_probe(record)

    lifecycle: dict[str, dict[str, Any]] = {}
    for domain_id, record in records.items():
        gateway_unit = f"hepta-tool-gateway@{domain_id}.service"
        execution_unit = f"hepta-execution-simulator@{domain_id}.service"
        wait_active(gateway_unit, True)
        wait_active(execution_unit, True)
        lifecycle[domain_id] = {
            "watch_generation": generations[domain_id],
            **custodian_metrics[domain_id],
            "gateway_pid": service_pid(
                gateway_unit,
                int(record["gateway_uid"]), int(record["gateway_gid"])),
            "simulator_pid": service_pid(
                execution_unit,
                int(record["execution_uid"]), int(record["execution_gid"])),
            **_domain_socket_inodes(record),
        }

    codex_a_gateway_pid = service_pid(
        "hepta-tool-gateway@codex-a.service", 2101, 2101)
    validate_uid1000_observer_reads_uid2101_proc_stat(codex_a_gateway_pid)

    for domain_id, record in records.items():
        revoke_domain_watch(record, generations[domain_id])
        generation_holder.pop(domain_id, None)
    systemctl("stop", *service_units, *reversed(socket_units))
    for unit in (*service_units, *socket_units):
        wait_active(unit, False)
    for record in records.values():
        for path, _uid, _gid in _domain_socket_paths(record).values():
            if path.exists() or path.is_symlink():
                fail("full-chain domain socket remained after stop")
        token = Path(
            f"/run/hepta-agent-{record['domain_id']}/sessions/session.token")
        if token.exists() or token.is_symlink():
            fail("full-chain domain token remained after revoke")
    return lifecycle


def bootstrap_watch(generation_holder: list[int]) -> int:
    completed = command([
        BOOTSTRAP,
        "--single-domain-compat",
        "provision-watch",
        "--agent-id", "codex-agent-os-e2e",
        "--session-id", "offline-four-uid-e2e",
        "--ttl-sec", "900",
    ], timeout=30)
    if completed.stderr or len(completed.stdout.splitlines()) != 1:
        fail("WATCH bootstrap output contract mismatch")
    try:
        result = json.loads(completed.stdout)
    except json.JSONDecodeError as error:
        raise GateFailure("WATCH bootstrap result is invalid") from error
    expected_fields = {
        "schema", "accepted", "operation", "trust_domain", "peer_uid",
        "lease_generation", "paper_authorized", "live_authorized"}
    if (
            not isinstance(result, dict) or set(result) != expected_fields or
            result.get("schema") != "hepta.agent-session-bootstrap.v1" or
            result.get("accepted") is not True or
            result.get("operation") != "provision-watch" or
            result.get("trust_domain") != "default" or
            result.get("peer_uid") != 2004 or
            result.get("paper_authorized") is not False or
            result.get("live_authorized") is not False):
        fail("WATCH bootstrap authority contract mismatch")
    generation = result.get("lease_generation")
    if (
            not isinstance(generation, int) or isinstance(generation, bool) or
            generation < 1):
        fail("WATCH bootstrap generation is invalid")
    generation_holder[:] = [generation]
    return generation


def revoke_watch(generation: int) -> None:
    completed = command([
        BOOTSTRAP, "--single-domain-compat",
        "revoke", "--generation", str(generation),
    ], timeout=30)
    if completed.stderr or len(completed.stdout.splitlines()) != 1:
        fail("WATCH revoke output contract mismatch")
    try:
        result = json.loads(completed.stdout)
    except json.JSONDecodeError as error:
        raise GateFailure("WATCH revoke result is invalid") from error
    if (
            result.get("schema") != "hepta.agent-session-bootstrap.v1" or
            result.get("accepted") is not True or
            result.get("operation") != "revoke" or
            result.get("trust_domain") != "default" or
            result.get("peer_uid") != 2004 or
            result.get("lease_generation") != generation or
            result.get("paper_authorized") is not False or
            result.get("live_authorized") is not False):
        fail("WATCH revoke authority contract mismatch")
    if Path("/run/hepta-agent/session.token").exists():
        fail("revoked WATCH token remains published")


def runtime_preflight(*, attempts: int = 1) -> None:
    last: Optional[subprocess.CompletedProcess[str]] = None
    for attempt in range(attempts):
        last = command([CHECKER], timeout=45, check=False)
        if (
                last.returncode == 0 and not last.stderr and
                last.stdout.startswith(
                    "hepta_agent_os_provisioned_host: PASS ") and
                "mode=runtime" in last.stdout and
                "runtime_probe=passed" in last.stdout and
                "agent_uid=2004" in last.stdout and
                "gateway_uid=2001" in last.stdout and
                "paper_authorized=false" in last.stdout and
                "live_authorized=false" in last.stdout):
            return
        if attempt + 1 < attempts:
            time.sleep(0.35)
    del last
    fail("UID 2004 MCP runtime preflight did not pass")


def require_watch_restart_fenced() -> None:
    completed = _run_as_identity([
        "/usr/bin/heptactl",
        "--socket", "/run/hepta-agent/tools.sock",
        "--token-file", "/run/hepta-agent/session.token",
        "tools", "list",
    ], 2004, 2004)
    try:
        result = json.loads(completed.stdout)
    except json.JSONDecodeError as error:
        raise GateFailure(
            "restarted WATCH rejection was not a typed result") from error
    if (
            completed.returncode != 3 or completed.stderr or
            not isinstance(result, dict) or
            set(result) != {
                "status", "tool", "reason_code", "detail", "order_id",
                "payload"} or
            result.get("status") != "permission_denied" or
            result.get("tool") != "system.tools.list" or
            result.get("reason_code") != "SESSION_NOT_FOUND" or
            result.get("order_id") != -1 or
            result.get("payload") is not None):
        fail("Gateway restart did not fence the previous WATCH authority")


def negative_runtime_preflight() -> None:
    completed = command([CHECKER], timeout=30, check=False)
    if (
            completed.returncode != 78 or completed.stdout or
            not completed.stderr.startswith(
                "hepta_agent_os_provisioned_host: FAIL: ")):
        fail("runtime preflight did not fail closed with sockets stopped")


def lifecycle_record() -> dict[str, int]:
    wait_active("hepta-tool-gateway.service", True)
    wait_active("hepta-execution-simulator.service", True)
    record = {
        "gateway_pid": service_pid(
            "hepta-tool-gateway.service", 2001, 2001),
        "simulator_pid": service_pid(
            "hepta-execution-simulator.service", 2002, 2002),
    }
    record.update(socket_inodes())
    return record


def stop_services() -> None:
    systemctl("stop", "hepta-tool-gateway.service")
    wait_active("hepta-tool-gateway.service", False)
    systemctl("stop", "hepta-execution-simulator.service")
    wait_active("hepta-execution-simulator.service", False)


def stop_sockets() -> None:
    systemctl("stop", *reversed(SOCKET_UNITS))
    for unit in SOCKET_UNITS:
        wait_active(unit, False)


def start_sockets() -> None:
    systemctl("start", *SOCKET_UNITS)
    for unit in SOCKET_UNITS:
        wait_active(unit, True)
    for unit in SERVICE_UNITS:
        if unit_active(unit):
            fail("socket start eagerly launched a service")


def best_effort_cleanup(
        generation: Optional[int],
        domain_generations: dict[str, int]) -> bool:
    cleanup_complete = True
    custodian_services = [
        f"hepta-shadow-watch-custodian@{item[0]}.service"
        for item in DOMAIN_EXECUTION_INSTANCES
    ]
    custodian_timers = [
        f"hepta-shadow-watch-custodian-reconcile@{item[0]}.timer"
        for item in DOMAIN_EXECUTION_INSTANCES
    ]
    try:
        stopped_custodians = subprocess.run(
            ["/usr/bin/systemctl", "stop",
             *custodian_timers, *custodian_services],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            env=ENVIRONMENT,
            cwd="/",
            timeout=30,
            check=False,
        )
        cleanup_complete = (
            stopped_custodians.returncode == 0 and cleanup_complete)
    except (OSError, subprocess.SubprocessError):
        cleanup_complete = False
    for domain_id in _domain_records():
        try:
            closed = subprocess.run(
                [
                    CUSTODIAN,
                    "--domain-config",
                    f"/etc/heptatrader/trust-domains/{domain_id}.json",
                    "close", "--reason", "operator-request",
                ],
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                env=ENVIRONMENT,
                cwd="/",
                timeout=20,
                check=False,
            )
            cleanup_complete = (
                closed.returncode == 0 and cleanup_complete)
        except (OSError, subprocess.SubprocessError):
            cleanup_complete = False
    if generation is not None and Path(
            "/run/hepta-agent/session.token").is_file():
        try:
            revoked = subprocess.run(
                [BOOTSTRAP, "--single-domain-compat",
                 "revoke", "--generation", str(generation)],
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                env=ENVIRONMENT,
                cwd="/",
                timeout=20,
                check=False,
            )
            cleanup_complete = revoked.returncode == 0
        except (OSError, subprocess.SubprocessError):
            cleanup_complete = False
    for domain_id, domain_generation in domain_generations.items():
        record = _domain_records().get(domain_id)
        if record is None:
            cleanup_complete = False
            continue
        token = Path(
            f"/run/hepta-agent-{domain_id}/sessions/session.token")
        if not token.is_file():
            continue
        try:
            revoked = subprocess.run(
                [
                    BOOTSTRAP, "--domain-config",
                    f"/etc/heptatrader/trust-domains/"
                    f"{record['domain_id']}.json",
                    "revoke", "--generation", str(domain_generation),
                ],
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                env=ENVIRONMENT,
                cwd="/",
                timeout=20,
                check=False,
            )
            cleanup_complete = (
                revoked.returncode == 0 and cleanup_complete)
        except (OSError, subprocess.SubprocessError):
            cleanup_complete = False
    try:
        stopped = subprocess.run(
            ["/usr/bin/systemctl", "stop",
             *(f"hepta-execution-simulator@{item[0]}.service"
               for item in DOMAIN_EXECUTION_INSTANCES),
             *(f"hepta-execution-simulator@{item[0]}.socket"
               for item in DOMAIN_EXECUTION_INSTANCES),
             *(f"hepta-execution-events-simulator@{item[0]}.socket"
               for item in DOMAIN_EXECUTION_INSTANCES),
             *(f"hepta-tool-gateway@{item[0]}.service"
               for item in DOMAIN_EXECUTION_INSTANCES),
             *(f"hepta-tool-gateway@{item[0]}.socket"
               for item in DOMAIN_EXECUTION_INSTANCES),
             *(f"hepta-tool-session-supervisor@{item[0]}.socket"
               for item in DOMAIN_EXECUTION_INSTANCES),
             *SERVICE_UNITS, *SOCKET_UNITS],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            env=ENVIRONMENT,
            cwd="/",
            timeout=30,
            check=False,
        )
        cleanup_complete = stopped.returncode == 0 and cleanup_complete
    except (OSError, subprocess.SubprocessError):
        cleanup_complete = False
    return cleanup_complete


def execute(
        generation_holder: list[int],
        domain_generation_holder: Optional[dict[str, int]] = None,
        ) -> dict[str, Any]:
    if domain_generation_holder is None:
        domain_generation_holder = {}
    validate_platform()
    validate_fixed_identities()
    validate_no_ib_surface()
    if INSTALLATION_PREFLIGHT.read_text(
            encoding="ascii", errors="strict") != (
            "HEPTA_AGENT_OS_INSTALLATION_PREFLIGHT_V1\n"):
        fail("image installation preflight marker is invalid")

    systemctl("daemon-reload")
    full_chain = full_chain_required()
    if full_chain:
        validate_broker_network_policy()
    command([
        "/usr/bin/systemd-tmpfiles", "--create",
        "/usr/lib/tmpfiles.d/heptatrader-agent-os.conf",
    ])
    require_unit_contracts()
    validate_domain_execution_instances()
    domain_lifecycle = (
        validate_two_domain_full_chain(domain_generation_holder)
        if full_chain else None)
    start_sockets()
    initial_inodes = socket_inodes()
    generation = bootstrap_watch(generation_holder)
    runtime_preflight(attempts=8)
    initial = lifecycle_record()
    if any(initial[key] != value for key, value in initial_inodes.items()):
        fail("manager socket inode changed during initial activation")
    if full_chain:
        generation = validate_broker_watchdog_fail_closed(
            generation, generation_holder)

    stop_services()
    if socket_inodes() != initial_inodes:
        fail("manager socket inode changed while only services were stopped")
    require_watch_restart_fenced()
    revoke_watch(generation)
    generation = bootstrap_watch(generation_holder)
    runtime_preflight(attempts=8)
    service_reactivation = lifecycle_record()
    if (
            service_reactivation["gateway_pid"] == initial["gateway_pid"] or
            service_reactivation["simulator_pid"] == initial["simulator_pid"]):
        fail("socket activation did not restart both services")
    if any(
            service_reactivation[key] != initial[key]
            for key in SOCKET_PATHS):
        fail("service activation unexpectedly recreated a manager socket")

    stop_services()
    stop_sockets()
    require_paths_absent()
    negative_runtime_preflight()

    start_sockets()
    restarted_inodes = socket_inodes()
    if any(restarted_inodes[key] == service_reactivation[key]
           for key in SOCKET_PATHS):
        fail("socket restart did not create fresh socket inodes")
    require_watch_restart_fenced()
    revoke_watch(generation)
    generation = bootstrap_watch(generation_holder)
    runtime_preflight(attempts=8)
    socket_reactivation = lifecycle_record()
    if (
            socket_reactivation["gateway_pid"] ==
            service_reactivation["gateway_pid"] or
            socket_reactivation["simulator_pid"] ==
            service_reactivation["simulator_pid"]):
        fail("fresh socket activation did not restart both services")
    if any(
            socket_reactivation[key] != restarted_inodes[key]
            for key in SOCKET_PATHS):
        fail("fresh socket inode changed during runtime activation")

    revoke_watch(generation)
    stop_services()
    stop_sockets()
    require_paths_absent()
    for relative in (
            "/run/hepta-agent/session.token",
            "/run/hepta-agent/tools.sock",
            "/run/hepta-tool-gateway/session-supervisor.sock",
            "/run/hepta-execution/execution.sock",
            "/run/hepta-execution/events.sock"):
        if Path(relative).exists():
            fail("runtime path remained after final cleanup")
    validate_no_ib_surface()

    checks = {
        "systemd_pid1": True,
        "network_none_loopback_only": True,
        "no_host_mount_or_docker_socket": True,
        "fixed_identity_isolation": True,
        "ib_paper_surface_absent": True,
        "installation_preflight": True,
        "simulator_dual_socket_activation": True,
        "gateway_dual_socket_activation": True,
        "root_watch_bootstrap": True,
        "uid_2004_mcp_initialize": True,
        "uid_2004_exact_watch_tool_list": True,
        "uid_2004_read_only_probes": True,
        "gateway_service_socket_reactivation": True,
        "simulator_service_socket_reactivation": True,
        "socket_stop_removes_paths": True,
        "socket_restart_recreates_paths": True,
        "watch_restart_fails_closed": True,
        "runtime_preflight_after_restart": True,
        "watch_session_revoked": True,
        "all_runtime_paths_removed": True,
    }
    if full_chain:
        checks.update({
            "two_domain_execution_identity_isolation": True,
            "two_domain_execution_socket_cross_access_denied": True,
            "two_domain_execution_authorities_started_and_stopped": True,
            "two_domain_runtime_configs_root_owned_regular": True,
            "two_domain_agent_host_dropins_isolated": True,
            "two_agent_gateway_execution_watch_chains": True,
            "two_domain_uid_config_cross_rejected": True,
            "two_domain_token_cross_rejected": True,
            "two_domain_account_binding_cross_rejected": True,
            "two_domain_execution_binding_cross_rejected": True,
            "two_domain_gateway_socket_cross_rejected": True,
            "two_domain_watch_restart_fails_closed": True,
            "two_domain_collector_typed_terminal": True,
            "two_domain_watch_sessions_revoked": True,
            "two_domain_custodian_reader_identity_isolation": True,
            "two_domain_watch_environments_root_owned_private": True,
            "two_domain_custodian_services_monitored": True,
            "two_domain_custodian_reconcile_timers_enabled": True,
            "two_domain_custodian_rotation_bound": True,
            "two_domain_custodian_sigkill_crash_closed": True,
            "two_domain_custodian_closure_receipts_exact": True,
            "two_domain_custodian_authority_residue_absent": True,
            "uid1000_observer_reads_uid2101_proc_stat": True,
            "broker_network_policy_active": True,
            "broker_watchdog_timeout_observed": True,
            "broker_watchdog_timeout_stop_contract": True,
            "broker_watchdog_gateway_binds_to_stop": True,
            "broker_watchdog_deny_all_persisted": True,
            "broker_watchdog_watch_terminalized": True,
            "broker_watchdog_clean_restart": True,
            "agent_ib_ports_denied": True,
            "gateway_ib_ports_denied": True,
            "ib_execution_ib_ports_denied": True,
            "agent_model_egress_preserved": True,
        })
    result = {
        "schema": SCHEMA if full_chain else COMPATIBILITY_SCHEMA,
        "passed": True,
        "identities": {
            "agent_uid": 2004,
            "gateway_uid": 2001,
            "simulator_execution_uid": 2002,
            "ib_execution_uid_reserved_not_started": 2003,
        },
        "checks": checks,
        "lifecycle": {
            "watch_generation": generation,
            "initial": initial,
            "service_reactivation": service_reactivation,
            "socket_reactivation": socket_reactivation,
        },
        "boundary": {
            "container_network": "none",
            "real_broker_connections": 0,
            "paper_orders": 0,
            "paper_authorized": False,
            "live_authorized": False,
            "ib_adapter_staged": False,
            "host_hepta_units_started": False,
            "host_bind_mounts": 0,
            "raw_session_token_recorded": False,
        },
    }
    if full_chain:
        result["profile"] = "two-domain-agent-gateway-execution-watch"
        result["identities"]["trust_domains"] = {
            domain_id: {
                "gateway_uid": int(record["gateway_uid"]),
                "agent_uid": int(record["agent_uid"]),
                "execution_uid": int(record["execution_uid"]),
                "reader_uid": int(record["reader_uid"]),
            }
            for domain_id, record in _domain_records().items()
        }
        result["lifecycle"]["trust_domains"] = domain_lifecycle
    return result


def main() -> int:
    generation_holder: list[int] = []
    domain_generation_holder: dict[str, int] = {}
    try:
        result = execute(generation_holder, domain_generation_holder)
    except (
            GateFailure, OSError, ValueError, json.JSONDecodeError,
            subprocess.SubprocessError) as error:
        cleanup_complete = best_effort_cleanup(
            generation_holder[0] if generation_holder else None,
            domain_generation_holder)
        message = str(error)
        if not message or len(message) > 160:
            message = type(error).__name__
        if not cleanup_complete:
            message = "runtime failure; best-effort cleanup incomplete"
        print(
            "hepta_agent_os_rootful_inner_gate: FAIL: " + message,
            file=sys.stderr,
        )
        return 1
    print(
        "HEPTA_AGENT_OS_ROOTFUL_E2E_RESULT=" +
        json.dumps(result, sort_keys=True, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
