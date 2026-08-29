#!/usr/bin/env python3

"""Exercise WATCH and inert PAPER domains under one disposable systemd PID 1."""

from __future__ import annotations

import hashlib
import importlib.util
import json
import os
from pathlib import Path
import pwd
import grp
import re
import signal
import socket
import stat
import subprocess
import sys
import time
from typing import Callable, Iterable


SCHEMA = "hepta.p1-dual-domain-rootful-inner.v1"
MARKER = "HEPTA_P1_DUAL_DOMAIN_ROOTFUL_RESULT="
DOMAINS = ("codex-a", "openclaw-b")
PLANES = ("WATCH", "PAPER_INERT")
SAFE_ENV = {
    "PATH": "/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin",
    "LANG": "C.UTF-8",
    "LC_ALL": "C.UTF-8",
    "SYSTEMD_COLORS": "0",
    "SYSTEMD_PAGER": "",
    "SYSTEMD_PAGERSECURE": "1",
}
MAX_OUTPUT = 2 * 1024 * 1024
MAX_CHILD_OUTPUT = 64 * 1024
PROTECTED_PORTS = {4001, 4002, 7496, 7497}
SENTINEL = Path("/run/hepta-p1-dual-domain-rootful.disposable")
IDENTITY_PATH = Path("/etc/heptatrader/p1-dual-domain-identities.json")
BOUNDARY_PATH = Path("/etc/heptatrader/p1-dual-domain-boundary.json")

IDENTITIES: list[dict[str, object]] = [
    {
        "plane": "WATCH",
        "domain_id": "codex-a",
        "name": "hepta-p1-watch-codex-a",
        "uid": 2211,
        "gid": 2211,
        "socket": "/run/hepta-p1-dual/watch-codex-a.sock",
        "credential":
            "/etc/heptatrader/credentials/watch/codex-a/lease.fixture",
        "runtime_directory": "/run/hepta-p1-watch-codex-a",
        "state_directory": "/var/lib/hepta-p1-watch-codex-a",
    },
    {
        "plane": "WATCH",
        "domain_id": "openclaw-b",
        "name": "hepta-p1-watch-openclaw-b",
        "uid": 2212,
        "gid": 2212,
        "socket": "/run/hepta-p1-dual/watch-openclaw-b.sock",
        "credential":
            "/etc/heptatrader/credentials/watch/openclaw-b/lease.fixture",
        "runtime_directory": "/run/hepta-p1-watch-openclaw-b",
        "state_directory": "/var/lib/hepta-p1-watch-openclaw-b",
    },
    {
        "plane": "PAPER_INERT",
        "domain_id": "codex-a",
        "name": "hepta-p1-paper-codex-a",
        "uid": 2231,
        "gid": 2231,
        "socket": "/run/hepta-p1-dual/paper-codex-a.sock",
        "credential":
            "/etc/heptatrader/credentials/paper/codex-a/"
            "authorization.fixture",
        "runtime_directory": "/run/hepta-p1-paper-codex-a",
        "state_directory": "/var/lib/hepta-p1-paper-codex-a",
        "control_directory": "/run/hepta-p1-dual/control/paper-codex-a",
        "kill_switch":
            "/run/hepta-p1-dual/control/paper-codex-a/kill-switch",
    },
    {
        "plane": "PAPER_INERT",
        "domain_id": "openclaw-b",
        "name": "hepta-p1-paper-openclaw-b",
        "uid": 2232,
        "gid": 2232,
        "socket": "/run/hepta-p1-dual/paper-openclaw-b.sock",
        "credential":
            "/etc/heptatrader/credentials/paper/openclaw-b/"
            "authorization.fixture",
        "runtime_directory": "/run/hepta-p1-paper-openclaw-b",
        "state_directory": "/var/lib/hepta-p1-paper-openclaw-b",
        "control_directory":
            "/run/hepta-p1-dual/control/paper-openclaw-b",
        "kill_switch":
            "/run/hepta-p1-dual/control/paper-openclaw-b/kill-switch",
    },
]

BOUNDARY = {
    "same_systemd_environment_count": 1,
    "watch_domains": 2,
    "inert_paper_domains": 2,
    "distinct_uids": 4,
    "distinct_gids": 4,
    "kill_switch_state": "engaged",
    "broker_connectors": 0,
    "broker_connections": 0,
    "broker_protocol_messages": 0,
    "paper_orders": 0,
    "paper_authorized": False,
    "live_authorized": False,
    "mutation_authorized": False,
    "direct_broker_access": False,
    "host_bind_mounts": 0,
    "host_systemd_units_touched": 0,
    "host_network_rules_touched": 0,
    "real_credentials": 0,
    "inert_credentials": 4,
}

CHECKS = {
    "real_systemd_pid1_and_private_cgroup",
    "all_four_fixture_units_loaded",
    "watch_and_inert_paper_concurrent_same_boot",
    "uid_gid_sets_pairwise_distinct",
    "watch_socket_cross_domain_denied",
    "paper_socket_cross_domain_denied",
    "watch_paper_socket_cross_plane_denied",
    "watch_credentials_cross_domain_denied",
    "paper_credentials_cross_domain_denied",
    "control_directories_cross_plane_denied",
    "session_tokens_cross_domain_denied",
    "paper_kill_switch_engaged_initially",
    "paper_kill_switch_engaged_through_faults",
    "paper_kill_switch_engaged_finally",
    "watchdog_timeout_restarted_watch",
    "service_crash_restarted_inert_paper",
    "socket_reactivation_remained_inert",
    "stale_generation_rejected",
    "generation_tombstones_bound_cleanup",
    "stopped_socket_paths_removed",
    "all_fixture_units_inactive_after_cleanup",
    "authority_residue_absent_after_cleanup",
    "loopback_only_container_network",
    "zero_broker_ports_and_protocol",
    "zero_orders_and_all_authority_flags_false",
}


class GateFailure(RuntimeError):
    pass


def fail(message: str) -> None:
    raise GateFailure(message)


def unit_name(plane: str, domain: str, suffix: str) -> str:
    leaf = "watch" if plane == "WATCH" else "paper"
    return f"hepta-p1-dual-{leaf}@{domain}.{suffix}"


def identity(plane: str, domain: str) -> dict[str, object]:
    matches = [
        item for item in IDENTITIES
        if item["plane"] == plane and item["domain_id"] == domain
    ]
    if len(matches) != 1:
        fail("fixture identity lookup mismatch")
    return matches[0]


def command(
        arguments: list[str], *, allowed: Iterable[int] = (0,),
        timeout: float = 30.0) -> subprocess.CompletedProcess[str]:
    completed = subprocess.run(
        arguments,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="strict",
        env=SAFE_ENV,
        cwd="/",
        close_fds=True,
        timeout=timeout,
        check=False,
    )
    if (
            len(completed.stdout.encode("utf-8")) > MAX_OUTPUT or
            len(completed.stderr.encode("utf-8")) > MAX_OUTPUT):
        fail("bounded command output exceeded")
    if completed.returncode not in set(allowed):
        detail = (completed.stdout + "\n" + completed.stderr)[-2048:]
        fail(
            f"command failed rc={completed.returncode}: " +
            detail.replace("\n", " | ").strip(" |"))
    return completed


def systemctl(
        *arguments: str, allowed: Iterable[int] = (0,),
        timeout: float = 45.0) -> subprocess.CompletedProcess[str]:
    completed = command(
        ["/usr/bin/systemctl", "--no-pager", "--no-ask-password", *arguments],
        allowed=range(0, 256),
        timeout=timeout,
    )
    if completed.returncode not in set(allowed):
        journal = command(
            ["/usr/bin/journalctl", "--no-pager", "-n", "80",
             "--output=short-monotonic"],
            allowed=(0, 1),
        )
        detail = (
            completed.stdout + completed.stderr + journal.stdout +
            journal.stderr)[-4096:]
        fail(
            f"systemctl failed rc={completed.returncode}: " +
            detail.replace("\n", " | ").strip(" |"))
    return completed


def properties(unit: str, names: Iterable[str]) -> dict[str, str]:
    requested = tuple(names)
    arguments = ["show", "--all", unit]
    for name in requested:
        arguments.extend(("--property", name))
    output = systemctl(*arguments).stdout
    values: dict[str, str] = {}
    for line in output.splitlines():
        if "=" not in line:
            fail("systemd property output malformed")
        name, value = line.split("=", 1)
        if name in values:
            fail("duplicate systemd property")
        values[name] = value
    if set(values) != set(requested):
        fail("systemd property set mismatch")
    return values


def read_json(path: Path, maximum: int = 1024 * 1024) -> object:
    descriptor = os.open(
        path, os.O_RDONLY | os.O_CLOEXEC | getattr(os, "O_NOFOLLOW", 0))
    try:
        metadata = os.fstat(descriptor)
        if (
                not stat.S_ISREG(metadata.st_mode) or metadata.st_nlink != 1 or
                metadata.st_size < 1 or metadata.st_size > maximum):
            fail("JSON input metadata mismatch")
        raw = os.read(descriptor, maximum + 1)
    finally:
        os.close(descriptor)
    if len(raw) > maximum:
        fail("JSON input exceeds bound")
    try:
        return json.loads(raw)
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise GateFailure("JSON input malformed") from error


def require_static_contract() -> str:
    if os.geteuid() != 0 or os.getpid() == 1:
        fail("inner gate root/non-PID1 contract mismatch")
    run_id = SENTINEL.read_text(encoding="ascii", errors="strict").strip()
    if re.fullmatch(r"[0-9a-f]{32}", run_id) is None:
        fail("disposable run sentinel malformed")
    if Path("/proc/1/exe").resolve() != Path("/usr/lib/systemd/systemd"):
        fail("container PID1 is not systemd")
    if Path("/proc/1/cgroup").read_text(
            encoding="ascii", errors="strict").strip() != "0::/":
        fail("PID1 private cgroup contract mismatch")
    provisioned = read_json(IDENTITY_PATH)
    if provisioned != {
            "schema": "hepta.p1-dual-domain-identities.v1",
            "paper_authorized": False,
            "live_authorized": False,
            "mutation_authorized": False,
            "direct_broker_access": False,
            "identities": IDENTITIES}:
        fail("provisioned identity contract mismatch")
    if read_json(BOUNDARY_PATH) != BOUNDARY:
        fail("provisioned boundary contract mismatch")
    return run_id


def require_identities() -> None:
    uids: set[int] = set()
    gids: set[int] = set()
    names: set[str] = set()
    for record in IDENTITIES:
        name = str(record["name"])
        uid = int(record["uid"])
        gid = int(record["gid"])
        account = pwd.getpwnam(name)
        group = grp.getgrnam(name)
        if (
                account.pw_uid != uid or account.pw_gid != gid or
                account.pw_dir != "/nonexistent" or
                account.pw_shell != "/usr/sbin/nologin" or
                group.gr_gid != gid or group.gr_mem):
            fail("fixture account/group binding mismatch")
        names.add(name)
        uids.add(uid)
        gids.add(gid)
    if len(names) != 4 or len(uids) != 4 or len(gids) != 4:
        fail("fixture identities are not pairwise distinct")


def require_unit_contracts() -> None:
    for plane in PLANES:
        for domain in DOMAINS:
            record = identity(plane, domain)
            service = unit_name(plane, domain, "service")
            sock = unit_name(plane, domain, "socket")
            service_values = properties(service, (
                "LoadState", "FragmentPath", "Type", "NotifyAccess",
                "WatchdogUSec", "User", "Group", "Restart",
                "PrivateNetwork", "NoNewPrivileges", "ProtectSystem",
                "RestrictAddressFamilies"))
            fragment = Path(service_values["FragmentPath"])
            metadata = os.lstat(fragment)
            if (
                    service_values["LoadState"] != "loaded" or
                    service_values["Type"] != "notify" or
                    service_values["NotifyAccess"] != "main" or
                    service_values["WatchdogUSec"] != "1s" or
                    service_values["User"] != record["name"] or
                    service_values["Group"] != record["name"] or
                    service_values["Restart"] != "on-failure" or
                    service_values["PrivateNetwork"] != "yes" or
                    service_values["NoNewPrivileges"] != "yes" or
                    service_values["ProtectSystem"] != "strict" or
                    service_values["RestrictAddressFamilies"] != "AF_UNIX" or
                    not stat.S_ISREG(metadata.st_mode) or
                    metadata.st_uid != 0 or metadata.st_gid != 0 or
                    stat.S_IMODE(metadata.st_mode) != 0o644):
                fail("effective service contract mismatch")
            socket_values = properties(
                sock, ("LoadState", "FragmentPath", "Listen"))
            socket_fragment = Path(socket_values["FragmentPath"])
            socket_metadata = os.lstat(socket_fragment)
            if (
                    socket_values["LoadState"] != "loaded" or
                    str(record["socket"]) not in socket_values["Listen"] or
                    not stat.S_ISREG(socket_metadata.st_mode) or
                    socket_metadata.st_uid != 0 or socket_metadata.st_gid != 0 or
                    stat.S_IMODE(socket_metadata.st_mode) != 0o644):
                fail("effective socket contract mismatch")


def wait_state(
        unit: str, expected: set[str], timeout: float = 20.0) -> dict[str, str]:
    deadline = time.monotonic() + timeout
    while True:
        values = properties(
            unit, ("ActiveState", "SubState", "MainPID", "NRestarts",
                   "ControlGroup"))
        if values["ActiveState"] in expected:
            return values
        if time.monotonic() >= deadline:
            fail(f"unit state timeout: {unit}={values['ActiveState']}")
        time.sleep(0.05)


def wait_restarted(
        plane: str, domain: str, previous_pid: int,
        previous_generation: int, timeout: float = 20.0,
        ) -> tuple[dict[str, str], dict[str, object]]:
    service = unit_name(plane, domain, "service")
    deadline = time.monotonic() + timeout
    last_error = ""
    while time.monotonic() < deadline:
        values = properties(
            service, ("ActiveState", "MainPID", "NRestarts", "ControlGroup"))
        try:
            pid = int(values["MainPID"], 10)
        except ValueError:
            pid = 0
        if values["ActiveState"] == "active" and pid > 1 and pid != previous_pid:
            try:
                result = request_as(
                    identity(plane, domain), {"command": "ping"})
                if result.get("generation") == previous_generation + 1:
                    return values, result
            except GateFailure as error:
                last_error = str(error)
        time.sleep(0.05)
    fail("service did not restart with next generation: " + last_error)


def drop_identity(uid: int, gid: int) -> None:
    os.setgroups([])
    os.setgid(gid)
    os.setuid(uid)


def child_boolean(uid: int, gid: int, operation: Callable[[], None]) -> bool:
    pid = os.fork()
    if pid == 0:
        try:
            signal.alarm(5)
            drop_identity(uid, gid)
            operation()
        except BaseException:
            os._exit(1)
        os._exit(0)
    _pid, status = os.waitpid(pid, 0)
    return os.WIFEXITED(status) and os.WEXITSTATUS(status) == 0


def readable_as(record: dict[str, object], path: Path) -> bool:
    def operation() -> None:
        descriptor = os.open(
            path, os.O_RDONLY | os.O_CLOEXEC | getattr(os, "O_NOFOLLOW", 0))
        try:
            if not os.read(descriptor, 1):
                raise OSError("empty")
        finally:
            os.close(descriptor)
    return child_boolean(int(record["uid"]), int(record["gid"]), operation)


def connectable_as(record: dict[str, object], path: Path) -> bool:
    def operation() -> None:
        client = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        try:
            client.settimeout(2)
            client.connect(str(path))
            client.sendall(b'{"command":"ping"}\n')
            raw = client.recv(4096)
            if not raw.endswith(b"\n"):
                raise OSError("probe response framing")
        finally:
            client.close()
    return child_boolean(int(record["uid"]), int(record["gid"]), operation)


def request_as(
        record: dict[str, object], request: dict[str, object],
        ) -> dict[str, object]:
    read_fd, write_fd = os.pipe2(os.O_CLOEXEC)
    pid = os.fork()
    if pid == 0:
        os.close(read_fd)
        try:
            signal.alarm(8)
            drop_identity(int(record["uid"]), int(record["gid"]))
            client = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            try:
                client.settimeout(6)
                client.connect(str(record["socket"]))
                raw_request = (
                    json.dumps(request, sort_keys=True, separators=(",", ":")) +
                    "\n").encode("utf-8")
                client.sendall(raw_request)
                raw = b""
                while b"\n" not in raw:
                    chunk = client.recv(min(4096, MAX_CHILD_OUTPUT + 1 - len(raw)))
                    if not chunk:
                        break
                    raw += chunk
                    if len(raw) > MAX_CHILD_OUTPUT:
                        raise OSError("response bound")
            finally:
                client.close()
            value = json.loads(raw)
            wrapper = {"ok": True, "value": value}
        except BaseException as error:
            wrapper = {"ok": False, "error": type(error).__name__}
        encoded = json.dumps(
            wrapper, sort_keys=True, separators=(",", ":")).encode("utf-8")
        try:
            os.write(write_fd, encoded)
        finally:
            os.close(write_fd)
        os._exit(0)
    os.close(write_fd)
    chunks: list[bytes] = []
    total = 0
    while True:
        chunk = os.read(read_fd, min(4096, MAX_CHILD_OUTPUT + 1 - total))
        if not chunk:
            break
        chunks.append(chunk)
        total += len(chunk)
        if total > MAX_CHILD_OUTPUT:
            os.kill(pid, signal.SIGKILL)
            os.waitpid(pid, 0)
            fail("identity child response exceeded bound")
    os.close(read_fd)
    _pid, status = os.waitpid(pid, 0)
    if not os.WIFEXITED(status) or os.WEXITSTATUS(status) != 0:
        fail("identity child did not exit cleanly")
    try:
        wrapper = json.loads(b"".join(chunks))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise GateFailure("identity child response malformed") from error
    if (
            not isinstance(wrapper, dict) or wrapper.get("ok") is not True or
            not isinstance(wrapper.get("value"), dict)):
        fail("identity socket request failed: " + str(wrapper.get("error", "")))
    return wrapper["value"]


def validate_response(
        value: dict[str, object], record: dict[str, object],
        expected_status: str = "ok") -> int:
    if set(value) != {
            "schema", "status", "plane", "domain_id", "generation",
            "kill_switch", "paper_authorized", "live_authorized",
            "mutation_authorized", "direct_broker_access",
            "broker_connections", "paper_orders"}:
        fail("daemon response exact-field mismatch")
    expected_kill = "engaged" if record["plane"] == "PAPER_INERT" else "n/a"
    if (
            value.get("schema") !=
            "hepta.p1-dual-domain-daemon-response.v1" or
            value.get("status") != expected_status or
            value.get("plane") != record["plane"] or
            value.get("domain_id") != record["domain_id"] or
            type(value.get("generation")) is not int or
            value["generation"] <= 0 or
            value.get("kill_switch") != expected_kill or
            value.get("paper_authorized") is not False or
            value.get("live_authorized") is not False or
            value.get("mutation_authorized") is not False or
            value.get("direct_broker_access") is not False or
            value.get("broker_connections") != 0 or
            value.get("paper_orders") != 0):
        fail("daemon response boundary mismatch")
    return int(value["generation"])


def require_process_identity(record: dict[str, object], pid: int) -> str:
    status = Path(f"/proc/{pid}/status").read_text(
        encoding="ascii", errors="strict")
    observed: dict[str, list[int]] = {}
    for line in status.splitlines():
        if line.startswith("Uid:") or line.startswith("Gid:"):
            key, values = line.split(":", 1)
            observed[key] = [int(item, 10) for item in values.split()]
    if (
            observed.get("Uid") != [int(record["uid"])] * 4 or
            observed.get("Gid") != [int(record["gid"])] * 4):
        fail("effective service process identity mismatch")
    cgroup = Path(f"/proc/{pid}/cgroup").read_text(
        encoding="ascii", errors="strict").strip()
    if not cgroup.startswith("0::/system.slice/"):
        fail("effective service cgroup mismatch")
    return cgroup


def require_socket(record: dict[str, object]) -> None:
    metadata = os.lstat(str(record["socket"]))
    if (
            not stat.S_ISSOCK(metadata.st_mode) or
            stat.S_IMODE(metadata.st_mode) != 0o600 or
            metadata.st_uid != record["uid"] or
            metadata.st_gid != record["gid"]):
        fail("socket ownership/mode mismatch")


def require_private_files() -> None:
    for record in IDENTITIES:
        credential = Path(str(record["credential"]))
        metadata = os.lstat(credential)
        expected = (
            b"INERT_P1_WATCH_FIXTURE_NO_EXTERNAL_AUTHORITY\n"
            if record["plane"] == "WATCH" else
            b"INERT_P1_PAPER_FIXTURE_NO_BROKER_CREDENTIAL\n")
        if (
                not stat.S_ISREG(metadata.st_mode) or metadata.st_nlink != 1 or
                metadata.st_uid != 0 or metadata.st_gid != record["gid"] or
                stat.S_IMODE(metadata.st_mode) != 0o440 or
                credential.read_bytes() != expected):
            fail("inert credential metadata/content mismatch")
        if not readable_as(record, credential):
            fail("matching identity cannot read its inert fixture")
        for other in IDENTITIES:
            if other is not record and readable_as(other, credential):
                fail("foreign identity can read another credential")
        if record["plane"] == "PAPER_INERT":
            kill_switch = Path(str(record["kill_switch"]))
            control = Path(str(record["control_directory"]))
            control_meta = os.lstat(control)
            switch_meta = os.lstat(kill_switch)
            if (
                    not stat.S_ISDIR(control_meta.st_mode) or
                    control_meta.st_uid != 0 or
                    control_meta.st_gid != record["gid"] or
                    stat.S_IMODE(control_meta.st_mode) != 0o750 or
                    not stat.S_ISREG(switch_meta.st_mode) or
                    switch_meta.st_uid != 0 or
                    switch_meta.st_gid != record["gid"] or
                    stat.S_IMODE(switch_meta.st_mode) != 0o440 or
                    kill_switch.read_bytes() != b"engaged\n" or
                    not readable_as(record, kill_switch)):
                fail("PAPER control/kill-switch contract mismatch")
            for other in IDENTITIES:
                if other is not record and readable_as(other, kill_switch):
                    fail("foreign identity can read PAPER control directory")


def require_session_isolation() -> None:
    for record in IDENTITIES:
        runtime = Path(str(record["runtime_directory"]))
        token = runtime / "session.token"
        active = runtime / "active-generation.json"
        metadata = os.lstat(runtime)
        token_meta = os.lstat(token)
        active_meta = os.lstat(active)
        if (
                not stat.S_ISDIR(metadata.st_mode) or
                stat.S_IMODE(metadata.st_mode) != 0o700 or
                metadata.st_uid != record["uid"] or
                metadata.st_gid != record["gid"] or
                not stat.S_ISREG(token_meta.st_mode) or
                stat.S_IMODE(token_meta.st_mode) != 0o600 or
                token_meta.st_uid != record["uid"] or
                token_meta.st_gid != record["gid"] or
                not stat.S_ISREG(active_meta.st_mode) or
                stat.S_IMODE(active_meta.st_mode) != 0o600 or
                not readable_as(record, token)):
            fail("session token/runtime metadata mismatch")
        for other in IDENTITIES:
            if other is not record and readable_as(other, token):
                fail("foreign identity can read another session token")


def require_cross_socket_denial() -> None:
    for record in IDENTITIES:
        if not connectable_as(record, Path(str(record["socket"]))):
            fail("matching identity cannot connect to its socket")
        for other in IDENTITIES:
            if other is record:
                continue
            if connectable_as(other, Path(str(record["socket"]))):
                fail("foreign identity can connect to another plane/domain socket")


def require_kill_switches() -> None:
    for record in IDENTITIES:
        if record["plane"] != "PAPER_INERT":
            continue
        path = Path(str(record["kill_switch"]))
        if path.read_bytes() != b"engaged\n":
            fail("PAPER kill switch changed state")


def tombstone_generation(record: dict[str, object]) -> int:
    path = Path(str(record["state_directory"])) / "generation.tombstone.json"
    value = read_json(path, maximum=4096)
    if (
            not isinstance(value, dict) or set(value) != {
                "schema", "plane", "domain_id", "generation",
                "authority_empty"} or
            value.get("schema") !=
            "hepta.p1-dual-domain-generation-tombstone.v1" or
            value.get("plane") != record["plane"] or
            value.get("domain_id") != record["domain_id"] or
            type(value.get("generation")) is not int or
            value["generation"] <= 0 or
            value.get("authority_empty") is not True):
        fail("generation tombstone contract mismatch")
    return int(value["generation"])


def fault_record(
        record: dict[str, object], before_pid: int, after_pid: int,
        before_generation: int, after_generation: int,
        tombstone: int) -> dict[str, object]:
    stale = request_as(record, {
        "command": "assert_generation",
        "generation": before_generation,
    })
    validate_response(stale, record, "stale-generation-rejected")
    return {
        "plane": record["plane"],
        "domain_id": record["domain_id"],
        "before_pid": before_pid,
        "after_pid": after_pid,
        "before_generation": before_generation,
        "after_generation": after_generation,
        "tombstone_generation": tombstone,
        "restart_observed": (
            before_pid != after_pid and
            after_generation == before_generation + 1),
        "stale_generation_rejected": True,
    }


def protected_tcp_socket_count() -> int:
    count = 0
    for path in (Path("/proc/net/tcp"), Path("/proc/net/tcp6")):
        for line in path.read_text(
                encoding="ascii", errors="strict").splitlines()[1:]:
            fields = line.split()
            if len(fields) < 3:
                fail("kernel TCP table row malformed")
            local = int(fields[1].rsplit(":", 1)[1], 16)
            remote = int(fields[2].rsplit(":", 1)[1], 16)
            if local in PROTECTED_PORTS or remote in PROTECTED_PORTS:
                count += 1
    return count


def require_broker_free_image() -> dict[str, object]:
    interfaces = sorted(os.listdir("/sys/class/net"))
    if interfaces != ["lo"]:
        fail("container network namespace is not loopback-only")
    protected_sockets = protected_tcp_socket_count()
    if protected_sockets != 0:
        fail("protected broker port exists")
    if Path("/usr/libexec/hepta-ib-executiond").exists():
        fail("IB execution adapter exists in broker-free fixture")
    if importlib.util.find_spec("ibapi") is not None:
        fail("IB API Python package exists in broker-free fixture")
    daemon = Path("/usr/libexec/hepta-p1-dual-domain-daemon").read_bytes()
    for token in (
            b"import ibapi", b"EClientSocket", b"placeOrder(", b"reqIds(",
            b"trade.place_order", b"AF_INET", b"SOCK_DGRAM, 0"):
        if token in daemon:
            fail("broker protocol/network token found in inert daemon")
    forbidden_name = re.compile(
        r"^(?:ibapi|eclientsocket|ewrapper|eclient|twsapi|libibapi|"
        r"libtwsapi|ib_gateway_adapter)(?:[._-].*|\.(?:h|hpp|so|a|py))?$",
        re.IGNORECASE,
    )
    excluded = {
        Path("/dev"), Path("/proc"), Path("/run"), Path("/sys"),
        Path("/tmp"), Path("/var"), Path("/etc/heptatrader"),
    }
    inventory = hashlib.sha256()
    count = 0
    forbidden_paths: list[str] = []
    for current, directories, files in os.walk(
            "/", topdown=True, followlinks=False):
        current_path = Path(current)
        kept: list[str] = []
        for name in sorted(directories):
            child = current_path / name
            if child in excluded:
                continue
            kept.append(name)
            if forbidden_name.fullmatch(name):
                forbidden_paths.append(str(child))
        directories[:] = kept
        for name in sorted(files):
            path = current_path / name
            metadata = os.lstat(path)
            count += 1
            if count > 200_000:
                fail("immutable image inventory exceeds bound")
            inventory.update(str(path).encode("utf-8", errors="strict"))
            inventory.update(b"\0")
            inventory.update(
                (f"{metadata.st_mode:o}:{metadata.st_uid}:{metadata.st_gid}:"
                 f"{metadata.st_size}\n").encode("ascii"))
            if forbidden_name.fullmatch(name):
                forbidden_paths.append(str(path))
    if forbidden_paths:
        fail("IB API/adapter payload name exists in broker-free image")
    return {
        "immutable_file_count": count,
        "immutable_file_inventory_sha256": inventory.hexdigest(),
        "inert_daemon_sha256": hashlib.sha256(daemon).hexdigest(),
        "forbidden_ib_api_payloads": 0,
        "protected_broker_sockets": protected_sockets,
        "network_interfaces": interfaces,
    }


def final_cleanup() -> None:
    services = [
        unit_name(plane, domain, "service")
        for plane in PLANES for domain in DOMAINS
    ]
    sockets = [
        unit_name(plane, domain, "socket")
        for plane in PLANES for domain in DOMAINS
    ]
    systemctl("stop", *services)
    systemctl("stop", *sockets)
    for unit in services + sockets:
        wait_state(unit, {"inactive", "failed"})
    for record in IDENTITIES:
        path = Path(str(record["socket"]))
        if path.exists() or path.is_symlink():
            fail("stopped socket path remains")
        runtime = Path(str(record["runtime_directory"]))
        if runtime.exists():
            for name in ("session.token", "active-generation.json"):
                if (runtime / name).exists() or (runtime / name).is_symlink():
                    fail("runtime authority residue remains")
        state = Path(str(record["state_directory"]))
        allowed = {"generation.counter", "generation.tombstone.json"}
        observed = {item.name for item in state.iterdir()}
        if observed != allowed:
            fail("state residue allowlist mismatch")
        tombstone_generation(record)
    require_kill_switches()


def run() -> dict[str, object]:
    run_id = require_static_contract()
    require_identities()
    require_unit_contracts()
    require_private_files()
    inventory = require_broker_free_image()

    socket_units = [
        unit_name(plane, domain, "socket")
        for plane in PLANES for domain in DOMAINS
    ]
    systemctl("start", *socket_units)
    initial: dict[tuple[str, str], tuple[int, int, int]] = {}
    cgroups: set[str] = set()
    for record in IDENTITIES:
        result = request_as(record, {"command": "ping"})
        generation = validate_response(result, record)
        service = unit_name(
            str(record["plane"]), str(record["domain_id"]), "service")
        values = wait_state(service, {"active"})
        pid = int(values["MainPID"], 10)
        if pid <= 1:
            fail("fixture service MainPID invalid")
        cgroups.add(require_process_identity(record, pid))
        require_socket(record)
        initial[(str(record["plane"]), str(record["domain_id"]))] = (
            pid, generation, int(values["NRestarts"], 10))
    if len(cgroups) != 4:
        fail("fixture services do not have distinct cgroups")
    require_session_isolation()
    require_cross_socket_denial()
    require_kill_switches()

    faults: dict[str, dict[str, object]] = {}

    watch = identity("WATCH", "codex-a")
    before_pid, before_generation, before_restarts = initial[
        ("WATCH", "codex-a")]
    stopped = request_as(watch, {"command": "stop_watchdog"})
    validate_response(stopped, watch, "watchdog-stopped")
    values, restarted = wait_restarted(
        "WATCH", "codex-a", before_pid, before_generation)
    after_pid = int(values["MainPID"], 10)
    after_generation = validate_response(restarted, watch)
    if int(values["NRestarts"], 10) <= before_restarts:
        fail("watchdog restart counter did not increase")
    tombstone = tombstone_generation(watch)
    faults["watchdog_timeout"] = fault_record(
        watch, before_pid, after_pid, before_generation,
        after_generation, tombstone)
    require_kill_switches()

    paper_b = identity("PAPER_INERT", "openclaw-b")
    before_pid, before_generation, before_restarts = initial[
        ("PAPER_INERT", "openclaw-b")]
    crashing = request_as(paper_b, {"command": "crash"})
    validate_response(crashing, paper_b, "crashing")
    values, restarted = wait_restarted(
        "PAPER_INERT", "openclaw-b", before_pid, before_generation)
    after_pid = int(values["MainPID"], 10)
    after_generation = validate_response(restarted, paper_b)
    if int(values["NRestarts"], 10) <= before_restarts:
        fail("crash restart counter did not increase")
    tombstone = tombstone_generation(paper_b)
    faults["service_crash_restart"] = fault_record(
        paper_b, before_pid, after_pid, before_generation,
        after_generation, tombstone)
    require_kill_switches()

    paper_a = identity("PAPER_INERT", "codex-a")
    before_pid, before_generation, _before_restarts = initial[
        ("PAPER_INERT", "codex-a")]
    paper_service = unit_name("PAPER_INERT", "codex-a", "service")
    paper_socket = unit_name("PAPER_INERT", "codex-a", "socket")
    systemctl("stop", paper_service)
    wait_state(paper_service, {"inactive"})
    if wait_state(paper_socket, {"active"})["ActiveState"] != "active":
        fail("socket did not remain active for reactivation")
    tombstone = tombstone_generation(paper_a)
    restarted = request_as(paper_a, {"command": "ping"})
    values = wait_state(paper_service, {"active"})
    after_pid = int(values["MainPID"], 10)
    after_generation = validate_response(restarted, paper_a)
    faults["socket_reactivation"] = fault_record(
        paper_a, before_pid, after_pid, before_generation,
        after_generation, tombstone)
    require_kill_switches()

    final_cleanup()

    checks = {name: True for name in sorted(CHECKS)}
    systemd_version = command(["/usr/bin/systemctl", "--version"]).stdout.splitlines()[0]
    boot_id = Path("/proc/sys/kernel/random/boot_id").read_text(
        encoding="ascii", errors="strict").strip()
    if re.fullmatch(
            r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-"
            r"[0-9a-f]{4}-[0-9a-f]{12}", boot_id) is None:
        fail("container boot ID malformed")
    return {
        "schema": SCHEMA,
        "passed": True,
        "run_id": run_id,
        "checks": checks,
        "boot": {
            "boot_id": boot_id,
            "pid1_cgroup": "0::/",
            "systemd": systemd_version,
        },
        "identities": IDENTITIES,
        "faults": faults,
        "inventory": inventory,
        "boundary": BOUNDARY,
    }


def main() -> int:
    try:
        result = run()
    except (
            GateFailure, OSError, ValueError, subprocess.SubprocessError
            ) as error:
        print(
            "hepta_p1_dual_domain_inner_gate: FAIL: " +
            (str(error) or type(error).__name__)[:2048],
            file=sys.stderr,
        )
        return 1
    print(MARKER + json.dumps(
        result, sort_keys=True, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
