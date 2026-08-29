#!/usr/bin/env python3

"""Inner half of the disposable rootful effective-systemd gate.

The outer runner builds one immutable image variant, starts systemd as PID 1
without host bind mounts or broker network, and invokes this program exactly
once with ``--mode``.  It emits exactly one machine-readable line and
deliberately excludes credential contents, the raw environment and raw journal
text from that line.
"""

from __future__ import annotations

import errno
import grp
import hashlib
import hmac
import json
import os
from pathlib import Path
import pwd
import re
import socket
import stat
import subprocess
import sys
import threading
import time
from typing import Iterable, Optional


SCHEMA = "hepta.execution-rootful-systemd-inner.v3"
MARKER = "HEPTA_ROOTFUL_SYSTEMD_GATE_RESULT="
CONTAINER_SCOPE = "containerized_effective_systemd_rehearsal"
NATIVE_SCOPE = "native_disposable_vm_rootful_systemd"
VARIANT_PATH = Path("/usr/local/share/hepta-rootful-systemd-gate/variant")
BASE_DIGEST_PATH = Path(
    "/usr/local/share/hepta-rootful-systemd-gate/base-image-digest")
NATIVE_MANIFEST_DIGEST_PATH = Path(
    "/usr/local/share/hepta-rootful-systemd-gate/image-manifest.sha256")
FORMAL_IB_HASH_PATH = Path(
    "/usr/local/share/hepta-rootful-systemd-gate/formal-ibapi.sha256")
SIMULATOR_BINARY = Path("/usr/libexec/hepta-executiond")
IB_BINARY = Path("/usr/libexec/hepta-ib-executiond")
DISABLED_IB_BINARY = Path("/usr/local/libexec/hepta-ib-executiond-disabled")
SANDBOX_BINARY = Path(
    "/usr/local/libexec/hepta_execution_systemd_sandbox_probe")
CLIENT_PROBE = Path("/usr/local/libexec/hepta_execution_systemd_client_probe")
INNER_RUNNER = Path(
    "/usr/local/libexec/hepta_execution_rootful_inner_gate.py")
PREFLIGHT = Path(
    "/usr/local/libexec/check_hepta_execution_provisioned_host.py")

SIM_SERVICE = "hepta-execution-simulator.service"
SIM_SOCKETS = (
    "hepta-execution-simulator.socket",
    "hepta-execution-events-simulator.socket",
)
IB_SERVICE = "hepta-execution-ib-paper.service"
IB_SOCKETS = (
    "hepta-execution-ib-paper.socket",
    "hepta-execution-events-ib-paper.socket",
)
BROKER_SERVICE = "hepta-broker-egress-policy.service"
BROKER_DROPIN = (
    "/usr/lib/systemd/system/hepta-execution-ib-paper.service.d/"
    "10-hepta-broker-egress-policy.conf")
CANONICAL_UNITS = (SIM_SERVICE,) + SIM_SOCKETS + (IB_SERVICE,) + IB_SOCKETS
GENERATOR_DIRECTORIES = (
    Path("/run/systemd/generator.early"),
    Path("/run/systemd/generator"),
    Path("/run/systemd/generator.late"),
)

EXECUTION_SOCKET = Path("/run/hepta-execution/execution.sock")
EVENT_SOCKET = Path("/run/hepta-execution/events.sock")
CONTROL_DIRECTORY = Path("/run/hepta/ib-paper-control")
KILL_SWITCH = CONTROL_DIRECTORY / "kill-switch"
TMPFILES_CONFIG = Path("/usr/lib/tmpfiles.d/heptatrader-ib-paper.conf")
SENTINEL = Path("/run/hepta-rootful-systemd-gate.disposable")

SERVICE_IDENTITIES = {
    "hepta-gateway": (2001, 2001),
    "hepta-exec": (2002, 2002),
    "hepta-ib-exec": (2003, 2003),
    "hepta-agent": (2004, 2004),
}
SIMULATOR_FENCE = Path(
    "/etc/heptatrader/credentials/hepta-execution-simulator-fence")
IB_FENCE = Path(
    "/etc/heptatrader/credentials/hepta-execution-ib-paper-fence")
IB_AUTHORIZATION = Path(
    "/etc/heptatrader/credentials/hepta-ib-paper-authorization")
SOURCE_CREDENTIALS = (SIMULATOR_FENCE, IB_FENCE, IB_AUTHORIZATION)

SYSTEMCTL = "/usr/bin/systemctl"
TMPFILES = "/usr/bin/systemd-tmpfiles"
SETPRIV = "/usr/bin/setpriv"
NSENTER = "/usr/bin/nsenter"
TEST = "/usr/bin/test"
IP = "/usr/sbin/ip"
JOURNALCTL = "/usr/bin/journalctl"
JOURNALD_SOCKET = "systemd-journald.socket"

BROKER_PORTS = frozenset({4001, 4002, 7496, 7497})
OUTPUT_LIMIT = 1024 * 1024
COMMAND_ENV = {
    "PATH": "/usr/sbin:/usr/bin:/sbin:/bin",
    "LANG": "C.UTF-8",
    "LC_ALL": "C.UTF-8",
    "SYSTEMD_COLORS": "0",
    "SYSTEMD_PAGER": "",
    "SYSTEMD_PAGERSECURE": "1",
}


class GateFailure(RuntimeError):
    """A redacted, stable fail-closed error code."""

    def __init__(self, code: str):
        if re.fullmatch(r"[A-Z0-9_]{3,96}", code) is None:
            code = "INVALID_INTERNAL_FAILURE_CODE"
        super().__init__(code)
        self.code = code


def require(condition: bool, code: str) -> None:
    if not condition:
        raise GateFailure(code)


def read_small(path: Path, maximum: int = 4096) -> str:
    flags = os.O_RDONLY | os.O_CLOEXEC | os.O_NOFOLLOW
    try:
        descriptor = os.open(path, flags)
    except OSError:
        raise GateFailure("SAFE_FILE_OPEN_FAILED")
    try:
        metadata = os.fstat(descriptor)
        require(stat.S_ISREG(metadata.st_mode), "SAFE_FILE_NOT_REGULAR")
        require(0 <= metadata.st_size <= maximum, "SAFE_FILE_SIZE_INVALID")
        chunks: list[bytes] = []
        total = 0
        while True:
            chunk = os.read(descriptor, min(65536, maximum + 1 - total))
            if not chunk:
                break
            chunks.append(chunk)
            total += len(chunk)
            require(total <= maximum, "SAFE_FILE_SIZE_INVALID")
        after = os.fstat(descriptor)
        require(
            (metadata.st_dev, metadata.st_ino, metadata.st_size,
             metadata.st_mtime_ns, metadata.st_ctime_ns) ==
            (after.st_dev, after.st_ino, after.st_size,
             after.st_mtime_ns, after.st_ctime_ns),
            "SAFE_FILE_CHANGED")
    finally:
        os.close(descriptor)
    try:
        value = b"".join(chunks).decode("utf-8", errors="strict")
    except UnicodeDecodeError:
        raise GateFailure("SAFE_FILE_ENCODING_INVALID")
    require("\x00" not in value, "SAFE_FILE_NUL")
    return value


def sha256_file(path: Path, maximum: int = 256 * 1024 * 1024,
                *, trusted_proc_exe: bool = False) -> str:
    if trusted_proc_exe:
        require(re.fullmatch(r"/proc/[1-9][0-9]*/exe", str(path)) is not None,
                "PROC_EXE_PATH_INVALID")
    flags = os.O_RDONLY | os.O_CLOEXEC
    if not trusted_proc_exe:
        flags |= os.O_NOFOLLOW
    try:
        descriptor = os.open(path, flags)
    except OSError:
        raise GateFailure("BINARY_OPEN_FAILED")
    digest = hashlib.sha256()
    try:
        before = os.fstat(descriptor)
        require(stat.S_ISREG(before.st_mode), "BINARY_NOT_REGULAR")
        require(64 <= before.st_size <= maximum, "BINARY_SIZE_INVALID")
        require(os.read(descriptor, 4) == b"\x7fELF", "BINARY_NOT_ELF")
        os.lseek(descriptor, 0, os.SEEK_SET)
        total = 0
        while True:
            chunk = os.read(descriptor, 1024 * 1024)
            if not chunk:
                break
            total += len(chunk)
            require(total <= maximum, "BINARY_SIZE_INVALID")
            digest.update(chunk)
        after = os.fstat(descriptor)
        require(
            (before.st_dev, before.st_ino, before.st_mode, before.st_nlink,
             before.st_uid, before.st_gid, before.st_size,
             before.st_mtime_ns, before.st_ctime_ns) ==
            (after.st_dev, after.st_ino, after.st_mode, after.st_nlink,
             after.st_uid, after.st_gid, after.st_size,
             after.st_mtime_ns, after.st_ctime_ns),
            "BINARY_CHANGED")
    finally:
        os.close(descriptor)
    return digest.hexdigest()


def command(
    arguments: list[str],
    *,
    label: str,
    allowed: Iterable[int] = (0,),
    timeout: float = 20.0,
) -> subprocess.CompletedProcess[bytes]:
    require(bool(arguments) and arguments[0].startswith("/"),
            "COMMAND_PATH_UNSAFE")
    try:
        completed = subprocess.run(
            arguments,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=COMMAND_ENV,
            timeout=timeout,
            check=False,
        )
    except subprocess.TimeoutExpired:
        raise GateFailure(label + "_TIMEOUT")
    except OSError:
        raise GateFailure(label + "_EXEC_FAILED")
    require(
        len(completed.stdout) <= OUTPUT_LIMIT and
        len(completed.stderr) <= OUTPUT_LIMIT,
        label + "_OUTPUT_LIMIT")
    require(completed.returncode in set(allowed), label + "_FAILED")
    return completed


def systemctl(*arguments: str, label: str = "SYSTEMCTL",
              allowed: Iterable[int] = (0,), timeout: float = 30.0
              ) -> subprocess.CompletedProcess[bytes]:
    return command(
        [SYSTEMCTL, "--no-pager", "--no-ask-password", *arguments],
        label=label, allowed=allowed, timeout=timeout)


def show(unit: str, properties: Iterable[str]) -> dict[str, str]:
    requested = tuple(properties)
    arguments = ["show", unit]
    for key in requested:
        arguments.extend(("--property", key))
    output = systemctl(*arguments, label="SYSTEMCTL_SHOW").stdout
    try:
        text = output.decode("utf-8", errors="strict")
    except UnicodeDecodeError:
        raise GateFailure("SYSTEMCTL_SHOW_ENCODING")
    values: dict[str, str] = {}
    for line in text.splitlines():
        require("=" in line, "SYSTEMCTL_SHOW_SYNTAX")
        key, value = line.split("=", 1)
        require(key in requested and key not in values,
                "SYSTEMCTL_SHOW_FIELD_SET")
        values[key] = value
    require(set(values) == set(requested), "SYSTEMCTL_SHOW_FIELD_SET")
    return values


def wait_until(predicate, code: str, timeout: float = 10.0,
               interval: float = 0.05):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        value = predicate()
        if value:
            return value
        time.sleep(interval)
    raise GateFailure(code)


def exact_stat(path: Path, mode: int, uid: int, gid: int,
               kind: str = "regular") -> os.stat_result:
    try:
        metadata = os.lstat(path)
    except OSError:
        raise GateFailure("FILESYSTEM_CONTRACT_MISSING")
    if kind == "regular":
        require(stat.S_ISREG(metadata.st_mode), "FILESYSTEM_KIND_INVALID")
        require(metadata.st_nlink == 1, "FILESYSTEM_LINK_COUNT_INVALID")
    elif kind == "directory":
        require(stat.S_ISDIR(metadata.st_mode), "FILESYSTEM_KIND_INVALID")
    elif kind == "socket":
        require(stat.S_ISSOCK(metadata.st_mode), "FILESYSTEM_KIND_INVALID")
    else:
        raise GateFailure("FILESYSTEM_KIND_INTERNAL")
    require(stat.S_IMODE(metadata.st_mode) == mode,
            "FILESYSTEM_MODE_INVALID")
    require((metadata.st_uid, metadata.st_gid) == (uid, gid),
            "FILESYSTEM_OWNER_INVALID")
    return metadata


def file_identity(metadata: os.stat_result) -> tuple[int, int]:
    return metadata.st_dev, metadata.st_ino


def socket_snapshot() -> dict[str, tuple[int, int]]:
    gateway_uid, gateway_gid = SERVICE_IDENTITIES["hepta-gateway"]
    exact_stat(Path("/run/hepta-execution"), 0o755, 0, 0, "directory")
    return {
        "execution": file_identity(exact_stat(
            EXECUTION_SOCKET, 0o660, gateway_uid, gateway_gid, "socket")),
        "events": file_identity(exact_stat(
            EVENT_SOCKET, 0o660, gateway_uid, gateway_gid, "socket")),
    }


def assert_paths_absent(paths: Iterable[Path], code: str) -> None:
    for path in paths:
        require(not path.exists() and not path.is_symlink(), code)


def validate_entry_context(mode: str, scope: str) -> dict[str, object]:
    require(os.geteuid() == 0 and os.getegid() == 0, "ROOT_REQUIRED")
    require(mode in {"real", "sandbox", "stub"}, "MODE_INVALID")
    require(scope in {CONTAINER_SCOPE, NATIVE_SCOPE}, "SCOPE_INVALID")
    require(os.environ.get("HEPTA_ROOTFUL_GATE_DISPOSABLE") == "1",
            "DISPOSABLE_ENV_MISSING")
    require(os.environ.get("HEPTA_ROOTFUL_GATE_MODE") == mode,
            "MODE_ENV_MISMATCH")
    require(os.environ.get("HEPTA_ROOTFUL_GATE_SCOPE", CONTAINER_SCOPE) == scope,
            "SCOPE_ENV_MISMATCH")
    baked = read_small(VARIANT_PATH, 32).strip()
    require(baked == mode, "BAKED_VARIANT_MISMATCH")

    sentinel = exact_stat(SENTINEL, 0o400, 0, 0)
    sentinel_id = read_small(SENTINEL, 128).strip()
    require(re.fullmatch(r"[0-9a-f]{32}", sentinel_id) is not None,
            "DISPOSABLE_SENTINEL_MISMATCH")
    supplied_run_id = os.environ.get("HEPTA_ROOTFUL_GATE_RUN_ID")
    if supplied_run_id is not None:
        require(supplied_run_id == sentinel_id,
                "DISPOSABLE_SENTINEL_MISMATCH")
    require(sentinel.st_nlink == 1, "DISPOSABLE_SENTINEL_UNSAFE")
    require(Path("/proc/1/comm").read_text(encoding="ascii").strip() ==
            "systemd", "PID1_NOT_SYSTEMD")
    require(os.readlink("/proc/1/exe") == "/usr/lib/systemd/systemd",
            "PID1_EXECUTABLE_INVALID")
    require(read_small(Path("/proc/1/cgroup"), 1024).strip() == "0::/",
            "CGROUP_NAMESPACE_NOT_PRIVATE")
    require(Path("/sys/fs/cgroup/cgroup.controllers").is_file(),
            "CGROUP_V2_REQUIRED")
    require(not Path("/run/docker.sock").exists() and
            not Path("/var/run/docker.sock").exists(),
            "DOCKER_SOCKET_EXPOSED")

    digest_path = (BASE_DIGEST_PATH if scope == CONTAINER_SCOPE else
                   NATIVE_MANIFEST_DIGEST_PATH)
    digest = read_small(digest_path, 128).strip()
    require(re.fullmatch(r"[0-9a-f]{64}", digest) is not None,
            "BASE_DIGEST_INVALID")
    return {"platform_image_sha256": digest, "scope": scope}


def validate_binary_variant(mode: str) -> dict[str, str]:
    simulator_hash = sha256_file(SIMULATOR_BINARY)
    client_probe_hash = sha256_file(CLIENT_PROBE)
    formal_ib_hash = read_small(FORMAL_IB_HASH_PATH, 128).strip()
    require(re.fullmatch(r"[0-9a-f]{64}", formal_ib_hash) is not None,
            "FORMAL_IB_HASH_INVALID")
    canonical_ib_hash = sha256_file(IB_BINARY)
    if mode == "real":
        require(canonical_ib_hash == sha256_file(DISABLED_IB_BINARY) and
                canonical_ib_hash != formal_ib_hash,
                "REAL_VARIANT_BINARY_MISMATCH")
        executed_kind = "real_simulator_only_ibapi_not_staged"
    elif mode == "sandbox":
        require(canonical_ib_hash == sha256_file(SANDBOX_BINARY) and
                canonical_ib_hash != formal_ib_hash,
                "SANDBOX_VARIANT_BINARY_MISMATCH")
        executed_kind = "no_ibapi_sandbox_probe"
    else:
        require(canonical_ib_hash == sha256_file(DISABLED_IB_BINARY) and
                canonical_ib_hash != formal_ib_hash,
                "STUB_VARIANT_BINARY_MISMATCH")
        executed_kind = "ibapi_disabled_stub"
    return {
        "simulator_sha256": simulator_hash,
        "client_probe_sha256": client_probe_hash,
        "formal_ibapi_sha256": formal_ib_hash,
        "executed_ib_path_sha256": canonical_ib_hash,
        "executed_kind": executed_kind,
    }


def validate_nss() -> None:
    observed_uids: set[int] = set()
    observed_gids: set[int] = set()
    shadow = read_small(Path("/etc/shadow"), 1024 * 1024)
    shadow_passwords: dict[str, str] = {}
    for line in shadow.splitlines():
        fields = line.split(":")
        if len(fields) >= 2:
            shadow_passwords[fields[0]] = fields[1]
    for name, expected in SERVICE_IDENTITIES.items():
        uid, gid = expected
        account = pwd.getpwnam(name)
        group = grp.getgrnam(name)
        require((account.pw_uid, account.pw_gid, group.gr_gid) ==
                (uid, gid, gid), "NSS_NUMERIC_IDENTITY_MISMATCH")
        require(account.pw_uid != 0 and account.pw_gid != 0,
                "NSS_ROOT_IDENTITY_FORBIDDEN")
        require(account.pw_shell in {"/usr/sbin/nologin", "/sbin/nologin"}
                and account.pw_dir == "/nonexistent",
                "NSS_LOGIN_CONTRACT_INVALID")
        require(os.getgrouplist(name, gid) == [gid],
                "NSS_SUPPLEMENTARY_GROUP_PRESENT")
        password = shadow_passwords.get(name, "")
        require(password.startswith("!") or password.startswith("*"),
                "NSS_ACCOUNT_NOT_LOCKED")
        require(uid not in observed_uids and gid not in observed_gids,
                "NSS_IDENTITY_NOT_DISTINCT")
        observed_uids.add(uid)
        observed_gids.add(gid)
        passwd_result = command(
            ["/usr/bin/getent", "passwd", name], label="GETENT_PASSWD")
        group_result = command(
            ["/usr/bin/getent", "group", name], label="GETENT_GROUP")
        require(passwd_result.stdout.count(b"\n") == 1 and
                group_result.stdout.count(b"\n") == 1,
                "NSS_GETENT_CARDINALITY")
    for account in pwd.getpwall():
        if account.pw_uid in observed_uids:
            require(account.pw_name in SERVICE_IDENTITIES,
                    "NSS_UID_ALIAS_PRESENT")
    for group in grp.getgrall():
        if group.gr_gid in observed_gids:
            require(group.gr_name in SERVICE_IDENTITIES and not group.gr_mem,
                    "NSS_GID_ALIAS_OR_MEMBER_PRESENT")


def validate_effective_units() -> None:
    expected_users = {
        SIM_SERVICE: "hepta-exec",
        IB_SERVICE: "hepta-ib-exec",
    }
    for unit in CANONICAL_UNITS:
        properties = show(unit, (
            "LoadState", "ActiveState", "UnitFileState", "FragmentPath",
            "DropInPaths", "Transient"))
        require(properties["LoadState"] == "loaded",
                "EFFECTIVE_UNIT_NOT_LOADED")
        require(properties["ActiveState"] == "inactive",
                "EFFECTIVE_UNIT_PREACTIVE")
        require(properties["UnitFileState"] == "static",
                "EFFECTIVE_UNIT_NOT_STATIC")
        require(properties["FragmentPath"] ==
                "/usr/lib/systemd/system/" + unit,
                "EFFECTIVE_FRAGMENT_PATH_INVALID")
        expected_dropin = BROKER_DROPIN if unit == IB_SERVICE else ""
        require(properties["DropInPaths"] == expected_dropin and
                properties["Transient"] == "no",
                "EFFECTIVE_OVERRIDE_OR_TRANSIENT_PRESENT")
    for service, user in expected_users.items():
        values = show(service, (
            "User", "Group", "KillMode", "PrivateNetwork",
            "NoNewPrivileges", "ProtectSystem", "ProtectHome",
            "Wants", "Requires", "BindsTo", "PartOf", "OnFailure",
            "TriggeredBy", "ExecStart"))
        require(values["User"] == user and values["Group"] == user,
                "EFFECTIVE_SERVICE_IDENTITY_INVALID")
        require(values["KillMode"] == "control-group",
                "EFFECTIVE_KILLMODE_INVALID")
        require(values["NoNewPrivileges"] == "yes" and
                values["ProtectSystem"] == "strict" and
                values["ProtectHome"] == "yes",
                "EFFECTIVE_SERVICE_HARDENING_INVALID")
        required_exec = "/usr/libexec/hepta-executiond" if service == SIM_SERVICE \
            else "/usr/libexec/hepta-ib-executiond"
        exec_start = values["ExecStart"]
        expected_prefix = ("{ path=" + required_exec +
                           " ; argv[]=" + required_exec + " ;")
        require(exec_start.startswith(expected_prefix) and
                exec_start.count("{ path=") == 1 and
                exec_start.count(required_exec) == 2,
                "EFFECTIVE_EXECSTART_INVALID")
        expected_binds_to = (
            BROKER_SERVICE if service == IB_SERVICE else "")
        require(
            values["Wants"] == "" and
            values["BindsTo"] == expected_binds_to and
            values["PartOf"] == "" and values["OnFailure"] == "",
                "EFFECTIVE_CALLBACK_OR_DEPENDENCY_PRESENT")
        expected_sockets = set(SIM_SOCKETS if service == SIM_SERVICE
                               else IB_SOCKETS)
        require(set(values["TriggeredBy"].split()) == expected_sockets,
                "EFFECTIVE_SOCKET_BINDING_INVALID")
        expected_hepta_requires = expected_sockets
        observed_hepta_requires = {
            value for value in values["Requires"].split()
            if value.startswith("hepta-")}
        require(observed_hepta_requires == expected_hepta_requires,
                "EFFECTIVE_HEPTA_DEPENDENCY_INVALID")
        expected_private = "yes" if service == SIM_SERVICE else "no"
        require(values["PrivateNetwork"] == expected_private,
                "EFFECTIVE_NETWORK_NAMESPACE_INVALID")

    for socket_unit in SIM_SOCKETS + IB_SOCKETS:
        expected_service = (SIM_SERVICE if socket_unit in SIM_SOCKETS
                            else IB_SERVICE)
        values = show(socket_unit, (
            "Triggers", "Wants", "Requires", "BindsTo", "PartOf",
            "OnFailure"))
        require(set(values["Triggers"].split()) == {expected_service},
                "EFFECTIVE_SOCKET_SERVICE_INVALID")
        observed_hepta_dependencies = {
            value for key in ("Wants", "Requires", "BindsTo", "PartOf",
                              "OnFailure")
            for value in values[key].split() if value.startswith("hepta-")}
        expected_dependencies = (
            {IB_SERVICE} if socket_unit in IB_SOCKETS else set())
        require(observed_hepta_dependencies == expected_dependencies and
                values["PartOf"] ==
                (IB_SERVICE if socket_unit in IB_SOCKETS else ""),
                "EFFECTIVE_SOCKET_DEPENDENCY_PRESENT")

    broker = show(BROKER_SERVICE, (
        "LoadState", "ActiveState", "UnitFileState", "FragmentPath",
        "DropInPaths", "Transient"))
    require(
        broker["LoadState"] == "loaded" and
        broker["ActiveState"] == "inactive" and
        broker["UnitFileState"] in {"disabled", "enabled"} and
        broker["FragmentPath"] ==
        "/usr/lib/systemd/system/hepta-broker-egress-policy.service" and
        broker["DropInPaths"] == "" and broker["Transient"] == "no",
        "EFFECTIVE_BROKER_GUARD_INVALID")


def is_execution_unit_name(name: str) -> bool:
    return (name in CANONICAL_UNITS or
            name.startswith("hepta-execution-") or
            name.startswith("hepta-ib-scalping") or
            name.startswith("hepta-openclaw-"))


def validate_no_generator_drift() -> None:
    for directory in GENERATOR_DIRECTORIES:
        try:
            entries = list(os.scandir(directory))
        except FileNotFoundError:
            continue
        except OSError:
            raise GateFailure("GENERATOR_DIRECTORY_UNREADABLE")
        for entry in entries:
            name = entry.name
            if (is_execution_unit_name(name) or
                    any(name == unit + suffix for unit in CANONICAL_UNITS
                        for suffix in (".d", ".wants", ".requires"))):
                raise GateFailure("EXECUTION_GENERATOR_ARTIFACT_PRESENT")
            if entry.is_symlink():
                try:
                    target = os.readlink(entry.path)
                except OSError:
                    raise GateFailure("GENERATOR_SYMLINK_UNREADABLE")
                if is_execution_unit_name(Path(target).name):
                    raise GateFailure("EXECUTION_GENERATOR_ALIAS_PRESENT")
            if (not entry.is_dir(follow_symlinks=False) or
                    not name.endswith((".wants", ".requires"))):
                continue
            try:
                children = list(os.scandir(entry.path))
            except OSError:
                raise GateFailure("GENERATOR_DEPENDENCY_DIRECTORY_UNREADABLE")
            for child in children:
                if is_execution_unit_name(child.name):
                    raise GateFailure("EXECUTION_GENERATOR_LINK_PRESENT")
                if child.is_symlink():
                    try:
                        target = os.readlink(child.path)
                    except OSError:
                        raise GateFailure("GENERATOR_SYMLINK_UNREADABLE")
                    if is_execution_unit_name(Path(target).name):
                        raise GateFailure("EXECUTION_GENERATOR_LINK_PRESENT")


def validate_journal_available() -> None:
    values = show(JOURNALD_SOCKET, (
        "LoadState", "ActiveState", "SubState"))
    require(values["LoadState"] == "loaded" and
            values["ActiveState"] == "active" and
            values["SubState"] in {"listening", "running"},
            "JOURNAL_SOCKET_NOT_READY")
    command([JOURNALCTL, "--sync"], label="JOURNAL_SYNC", timeout=10.0)


def source_credential_metadata() -> dict[str, tuple[int, int, int, int, int]]:
    snapshots: dict[str, tuple[int, int, int, int, int]] = {}
    for path in SOURCE_CREDENTIALS:
        metadata = exact_stat(path, 0o400, 0, 0)
        require(1 <= metadata.st_size <= 256,
                "SOURCE_CREDENTIAL_SIZE_INVALID")
        snapshots[path.name] = (
            metadata.st_dev, metadata.st_ino, metadata.st_mode,
            metadata.st_size, metadata.st_mtime_ns)
    return snapshots


def fence_generation(path: Path) -> int:
    lines = read_small(path, 256).splitlines()
    require(len(lines) == 3 and lines[0] == "HFC1" and
            lines[1].startswith("fencing_token=") and
            lines[2].startswith("generation="),
            "FENCE_CREDENTIAL_SYNTAX_INVALID")
    token = lines[1].split("=", 1)[1]
    generation = lines[2].split("=", 1)[1]
    require(token.isascii() and token.isdecimal() and token[0] != "0" and
            generation.isascii() and generation.isdecimal() and
            generation[0] != "0", "FENCE_CREDENTIAL_SYNTAX_INVALID")
    parsed = int(generation, 10)
    require(0 < parsed <= 0xFFFFFFFFFFFFFFFF,
            "FENCE_CREDENTIAL_GENERATION_INVALID")
    return parsed


def assert_source_credentials_unreadable() -> None:
    for name in ("hepta-exec", "hepta-ib-exec"):
        uid, gid = SERVICE_IDENTITIES[name]
        for path in SOURCE_CREDENTIALS:
            completed = command([
                SETPRIV, "--reuid", str(uid), "--regid", str(gid),
                "--clear-groups", TEST, "-r", str(path),
            ], label="SOURCE_CREDENTIAL_DAC", allowed=(1,))
            require(completed.returncode == 1,
                    "SOURCE_CREDENTIAL_READABLE_BY_SERVICE")


def initialize_tmpfiles() -> tuple[int, int, int, int, int]:
    require(not CONTROL_DIRECTORY.exists() and not KILL_SWITCH.exists(),
            "TMPFILES_CONTROL_NOT_INITIAL_EMPTY")
    command([TMPFILES, "--create", str(TMPFILES_CONFIG)],
            label="TMPFILES_CREATE")
    exact_stat(CONTROL_DIRECTORY, 0o750, 0,
               SERVICE_IDENTITIES["hepta-ib-exec"][1], "directory")
    first = exact_stat(KILL_SWITCH, 0o440, 0,
                       SERVICE_IDENTITIES["hepta-ib-exec"][1])
    require(read_small(KILL_SWITCH, 64) == "engaged",
            "KILL_SWITCH_NOT_ENGAGED")
    identity = (first.st_dev, first.st_ino, first.st_mode,
                first.st_size, first.st_mtime_ns)
    command([TMPFILES, "--create", str(TMPFILES_CONFIG)],
            label="TMPFILES_SECOND_CREATE")
    second = os.lstat(KILL_SWITCH)
    require(identity == (second.st_dev, second.st_ino, second.st_mode,
                         second.st_size, second.st_mtime_ns),
            "TMPFILES_NOT_IDEMPOTENT")
    uid, gid = SERVICE_IDENTITIES["hepta-ib-exec"]
    readonly = command([
        SETPRIV, "--reuid", str(uid), "--regid", str(gid),
        "--clear-groups", "/usr/bin/python3", str(INNER_RUNNER),
        "--readonly-probe", str(KILL_SWITCH),
    ], label="KILL_SWITCH_DAC_PROBE")
    require(readonly.returncode == 0,
            "KILL_SWITCH_DAC_MUTATION_ALLOWED")
    return identity


def run_preflight() -> None:
    completed = command(
        ["/usr/bin/python3", str(PREFLIGHT), "--root", "/"],
        label="PROVISIONED_HOST_PREFLIGHT", timeout=30.0)
    require(completed.stderr == b"" and
            completed.stdout.startswith(
                b"hepta_execution_provisioned_host: PASS ") and
            completed.stdout.count(b"\n") == 1,
            "PROVISIONED_HOST_PREFLIGHT_CONTRACT")


def parse_proc_status(pid: int, uid: int, gid: int) -> None:
    text = read_small(Path(f"/proc/{pid}/status"), 65536)
    fields: dict[str, str] = {}
    for line in text.splitlines():
        if ":" in line:
            key, value = line.split(":", 1)
            fields[key] = value.strip()
    require(fields.get("Uid", "").split() == [str(uid)] * 4,
            "PROCESS_UID_MISMATCH")
    require(fields.get("Gid", "").split() == [str(gid)] * 4,
            "PROCESS_GID_MISMATCH")
    groups = [int(value) for value in fields.get("Groups", "").split()]
    require(set(groups).issubset({gid}) and len(groups) <= 1,
            "PROCESS_SUPPLEMENTARY_GROUP_PRESENT")
    for key in ("CapInh", "CapPrm", "CapEff", "CapBnd", "CapAmb"):
        value = fields.get(key, "")
        require(re.fullmatch(r"0+", value) is not None,
                "PROCESS_CAPABILITY_PRESENT")
    require(fields.get("NoNewPrivs") == "1" and
            fields.get("Seccomp") == "2",
            "PROCESS_SANDBOX_STATUS_INVALID")


def parse_activation_environment(pid: int) -> None:
    try:
        contents = Path(f"/proc/{pid}/environ").read_bytes()
    except OSError:
        raise GateFailure("PROCESS_ENVIRONMENT_UNREADABLE")
    require(len(contents) <= 256 * 1024,
            "PROCESS_ENVIRONMENT_OVERSIZE")
    selected: dict[str, str] = {}
    for item in contents.split(b"\0"):
        if b"=" not in item:
            continue
        key, value = item.split(b"=", 1)
        if key in {b"LISTEN_PID", b"LISTEN_FDS", b"LISTEN_FDNAMES"}:
            try:
                selected[key.decode("ascii")] = value.decode("ascii")
            except UnicodeDecodeError:
                raise GateFailure("ACTIVATION_ENVIRONMENT_INVALID")
    require(selected == {
        "LISTEN_PID": str(pid),
        "LISTEN_FDS": "2",
        "LISTEN_FDNAMES": "execution:events",
    }, "ACTIVATION_ENVIRONMENT_INVALID")


def validate_activated_fds(pid: int,
                           sockets: dict[str, tuple[int, int]]) -> None:
    # A pathname's filesystem inode and the listening socket object's sockfs
    # inode are intentionally different on Linux.  Bind each inherited FD to
    # the manager-owned listener through its socket:[N] identity and the
    # canonical /proc/net/unix row instead of comparing unlike inode classes.
    del sockets  # Path ownership/mode/inode stability is checked separately.
    expected = {
        3: str(EXECUTION_SOCKET),
        4: str(EVENT_SOCKET),
    }
    observed_links: dict[int, str] = {}
    for descriptor in expected:
        try:
            target = os.readlink(f"/proc/{pid}/fd/{descriptor}")
        except OSError:
            raise GateFailure("ACTIVATED_FD_MISSING")
        require(re.fullmatch(r"socket:\[[1-9][0-9]*\]", target) is not None,
                "ACTIVATED_FD_NOT_SOCKET")
        observed_links[descriptor] = target

    table = read_small(Path("/proc/net/unix"), 1024 * 1024)
    lines = table.splitlines()
    require(bool(lines) and lines[0].split() == [
        "Num", "RefCount", "Protocol", "Flags", "Type", "St", "Inode",
        "Path"], "PROC_NET_UNIX_HEADER_INVALID")
    rows: dict[int, list[list[str]]] = {}
    for line in lines[1:]:
        fields = line.split(maxsplit=7)
        require(len(fields) in {7, 8} and fields[6].isascii() and
                fields[6].isdecimal(), "PROC_NET_UNIX_ROW_INVALID")
        rows.setdefault(int(fields[6], 10), []).append(fields)

    for descriptor, path in expected.items():
        inode = int(observed_links[descriptor][8:-1], 10)
        matches = rows.get(inode, [])
        require(len(matches) == 1, "ACTIVATED_FD_SOCKET_IDENTITY_AMBIGUOUS")
        fields = matches[0]
        require(len(fields) == 8 and fields[7] == path and
                fields[2] == "00000000" and fields[3] == "00010000" and
                fields[4] == "0001" and fields[5] == "01",
                "ACTIVATED_FD_LISTENER_MISMATCH")
        try:
            after = os.readlink(f"/proc/{pid}/fd/{descriptor}")
        except OSError:
            raise GateFailure("ACTIVATED_FD_CHANGED")
        require(after == observed_links[descriptor], "ACTIVATED_FD_CHANGED")


def main_pid(unit: str) -> int:
    values = show(unit, ("ActiveState", "SubState", "MainPID"))
    try:
        pid = int(values["MainPID"], 10)
    except ValueError:
        return 0
    if values["ActiveState"] != "active" or pid <= 1:
        return 0
    return pid


def wait_main_pid(unit: str) -> int:
    return int(wait_until(lambda: main_pid(unit),
                          "SERVICE_DID_NOT_REACH_ACTIVE", 15.0))


def parse_mountinfo(pid: int) -> list[tuple[str, set[str]]]:
    text = read_small(Path(f"/proc/{pid}/mountinfo"), 1024 * 1024)
    mounts: list[tuple[str, set[str]]] = []
    for line in text.splitlines():
        fields = line.split()
        require(len(fields) >= 10 and "-" in fields,
                "MOUNTINFO_SYNTAX_INVALID")
        mountpoint = re.sub(
            r"\\([0-7]{3})",
            lambda match: chr(int(match.group(1), 8)), fields[4])
        mounts.append((mountpoint, set(fields[5].split(","))))
    return mounts


def require_readonly_mount(pid: int, path: str) -> None:
    candidates = [item for item in parse_mountinfo(pid)
                  if path == item[0] or path.startswith(item[0].rstrip("/") + "/")]
    require(bool(candidates), "READONLY_MOUNT_NOT_FOUND")
    mountpoint, options = max(candidates, key=lambda item: len(item[0]))
    del mountpoint
    require("ro" in options and "rw" not in options,
            "MOUNT_NOT_READONLY")


def validate_sensitive_mount_isolation() -> None:
    forbidden_roots = (
        "/etc/heptatrader", "/usr", "/run/hepta-execution",
        "/run/hepta/ib-paper-control", "/var/lib/hepta-execution",
        "/var/lib/hepta-ib-execution")
    for mountpoint, _options in parse_mountinfo(1):
        for root in forbidden_roots:
            require(not (mountpoint == root or
                         mountpoint.startswith(root.rstrip("/") + "/")),
                    "SENSITIVE_HOST_MOUNT_PRESENT")


def validate_mounted_credentials(pid: int, unit: str,
                                 credentials: dict[str, Path], uid: int, gid: int,
                                 control_directory: bool = False) -> None:
    credential_directory = f"/run/credentials/{unit}"
    require_readonly_mount(pid, credential_directory)
    proc_root = Path(f"/proc/{pid}/root")
    for name, source_path in credentials.items():
        path = proc_root / credential_directory.lstrip("/") / name
        metadata = os.lstat(path)
        require(stat.S_ISREG(metadata.st_mode) and
                stat.S_IMODE(metadata.st_mode) == 0o400 and
                metadata.st_nlink == 1 and
                metadata.st_uid in {0, uid} and
                1 <= metadata.st_size <= 256,
                "MOUNTED_CREDENTIAL_CONTRACT_INVALID")
        readable = command([
            NSENTER, "--target", str(pid), "--mount", "--",
            SETPRIV, "--reuid", str(uid), "--regid", str(gid),
            "--clear-groups", TEST, "-r",
            credential_directory + "/" + name,
        ], label="MOUNTED_CREDENTIAL_READ")
        require(readable.returncode == 0,
                "MOUNTED_CREDENTIAL_NOT_READABLE")
        readonly = command([
            NSENTER, "--target", str(pid), "--mount", "--",
            "/usr/bin/python3", str(INNER_RUNNER), "--readonly-probe",
            credential_directory + "/" + name,
        ], label="MOUNTED_CREDENTIAL_READONLY")
        require(readonly.returncode == 0,
                "MOUNTED_CREDENTIAL_MUTABLE")
        require(hmac.compare_digest(
                    read_small(path, 256), read_small(source_path, 256)),
                "MOUNTED_CREDENTIAL_SOURCE_MISMATCH")
    if control_directory:
        require_readonly_mount(pid, str(CONTROL_DIRECTORY))
        readonly = command([
            NSENTER, "--target", str(pid), "--mount", "--",
            "/usr/bin/python3", str(INNER_RUNNER), "--readonly-probe",
            str(KILL_SWITCH),
        ], label="MOUNTED_KILL_SWITCH_READONLY")
        require(readonly.returncode == 0,
                "MOUNTED_KILL_SWITCH_MUTABLE")


def validate_state_directory(path: Path, uid: int, gid: int,
                             journal_required: bool = True) -> None:
    exact_stat(path, 0o700, uid, gid, "directory")
    journal = path / "oms-journal.jsonl"
    if journal_required:
        metadata = exact_stat(journal, 0o600, uid, gid)
        require(metadata.st_size >= 0, "JOURNAL_SIZE_INVALID")


def run_client_probe(server_uid: int, plane: str = "both",
                     *, expected_exit: int = 0,
                     domain: str = "SIMULATOR") -> Optional[dict[str, object]]:
    gateway_uid, gateway_gid = SERVICE_IDENTITIES["hepta-gateway"]
    arguments = [
        SETPRIV, "--reuid", str(gateway_uid), "--regid", str(gateway_gid),
        "--clear-groups", str(CLIENT_PROBE),
        "--server-uid", str(server_uid),
        "--execution-domain", domain,
        "--agent-id", "rootful-gate-agent",
        "--session-id", "rootful-gate-session",
        "--plane", plane,
        "--timeout-ms", "250",
    ]
    completed = command(arguments, label="CLIENT_PROBE",
                        allowed=(expected_exit,), timeout=5.0)
    if expected_exit != 0:
        return None
    require(completed.stderr == b"", "CLIENT_PROBE_STDERR")
    try:
        lines = completed.stdout.decode("utf-8", errors="strict").splitlines()
    except UnicodeDecodeError:
        raise GateFailure("CLIENT_PROBE_ENCODING")
    require(len(lines) == 2 and
            lines[1] == "execution_systemd_client_probe: PASS" and
            lines[0].startswith("execution_systemd_client_probe_evidence: "),
            "CLIENT_PROBE_MARKER_CONTRACT")
    try:
        evidence = json.loads(lines[0].split(": ", 1)[1])
    except (ValueError, TypeError):
        raise GateFailure("CLIENT_PROBE_EVIDENCE_JSON")
    expected_keys = {
        "schema", "plane", "service_epoch", "service_fencing_generation",
        "mutation_identity_ok", "event_identity_ok", "shared_identity_ok",
        "wait_status", "mutation_requests",
    }
    require(isinstance(evidence, dict) and set(evidence) == expected_keys,
            "CLIENT_PROBE_EVIDENCE_FIELDS")
    require(evidence["schema"] ==
            "hepta.execution-systemd-client-probe.v1" and
            evidence["plane"] == plane and
            re.fullmatch(r"hexec-v6-[0-9a-f]{32}",
                         str(evidence["service_epoch"])) is not None and
            isinstance(evidence["service_fencing_generation"], int) and
            evidence["service_fencing_generation"] > 0 and
            evidence["mutation_requests"] == 0,
            "CLIENT_PROBE_EVIDENCE_VALUES")
    if plane == "both":
        require(evidence["mutation_identity_ok"] is True and
                evidence["event_identity_ok"] is True and
                evidence["shared_identity_ok"] is True and
                evidence["wait_status"] == "timeout",
                "CLIENT_PROBE_DUAL_PLANE_INVALID")
    return evidence


def root_peer_rejected(server_uid: int) -> None:
    arguments = [
        str(CLIENT_PROBE), "--server-uid", str(server_uid),
        "--execution-domain", "SIMULATOR", "--agent-id", "root-peer",
        "--session-id", "root-peer", "--plane", "execution",
        "--timeout-ms", "250",
    ]
    command(arguments, label="ROOT_PEER_REJECTION", allowed=(3,), timeout=5.0)


def socket_dac_rejected(path: Path, name: str) -> None:
    uid, gid = SERVICE_IDENTITIES[name]
    command([
        SETPRIV, "--reuid", str(uid), "--regid", str(gid),
        "--clear-groups", "/usr/bin/python3", str(INNER_RUNNER),
        "--socket-denied-probe", str(path),
    ], label="SOCKET_DAC_REJECTION")


def cgroup_path(unit: str) -> Path:
    value = show(unit, ("ControlGroup",))["ControlGroup"]
    require(value.startswith("/system.slice/") and ".." not in value,
            "CONTROL_GROUP_PATH_INVALID")
    path = Path("/sys/fs/cgroup") / value.lstrip("/")
    require(path.is_dir(), "CONTROL_GROUP_MISSING")
    return path


def cgroup_pids(path: Path) -> set[int]:
    observed: set[int] = set()
    if not path.exists():
        return observed
    for procs in path.rglob("cgroup.procs"):
        text = read_small(procs, 1024 * 1024)
        for line in text.splitlines():
            if line:
                require(line.isascii() and line.isdecimal(),
                        "CGROUP_PROCS_INVALID")
                observed.add(int(line, 10))
    return observed


def stop_service_and_assert_cgroup(unit: str, captured: set[int]) -> None:
    systemctl("stop", unit, label="SERVICE_STOP", timeout=25.0)
    for pid in captured:
        wait_until(lambda pid=pid: not Path(f"/proc/{pid}").exists(),
                   "CGROUP_PROCESS_SURVIVED_STOP", 5.0)
    value = show(unit, ("ActiveState", "MainPID"))
    require(value["ActiveState"] == "inactive" and value["MainPID"] == "0",
            "SERVICE_NOT_INACTIVE_AFTER_STOP")


def start_socket_pair(pair: tuple[str, str]) -> dict[str, tuple[int, int]]:
    assert_paths_absent((EXECUTION_SOCKET, EVENT_SOCKET),
                        "SOCKET_PATH_PREEXISTED")
    systemctl("start", *pair, label="SOCKET_START")
    service = SIM_SERVICE if pair == SIM_SOCKETS else IB_SERVICE
    require(show(service, ("ActiveState",))["ActiveState"] == "inactive",
            "SOCKET_START_IMPLICITLY_STARTED_SERVICE")
    return socket_snapshot()


def stop_socket_pair(pair: tuple[str, str]) -> None:
    systemctl("stop", *pair, label="SOCKET_STOP")
    assert_paths_absent((EXECUTION_SOCKET, EVENT_SOCKET),
                        "SOCKET_PATH_SURVIVED_STOP")


def assert_paper_sockets_closed_without_reactivation() -> None:
    for unit in IB_SOCKETS:
        values = show(unit, ("ActiveState", "SubState"))
        require(
            values["ActiveState"] == "inactive" and
            values["SubState"] == "dead",
            "PAPER_SOCKET_NOT_INACTIVE_AFTER_SERVICE_STOP")
    assert_paths_absent(
        (EXECUTION_SOCKET, EVENT_SOCKET),
        "PAPER_SOCKET_PATH_SURVIVED_SERVICE_STOP")
    before = show(IB_SERVICE, (
        "ActiveState", "MainPID", "ExecMainStartTimestampMonotonic",
        "NRestarts"))
    for path in (EXECUTION_SOCKET, EVENT_SOCKET):
        client = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        try:
            client.settimeout(0.25)
            try:
                client.connect(str(path))
            except OSError as error:
                require(
                    error.errno in {errno.ENOENT, errno.ECONNREFUSED},
                    "PAPER_CLOSED_SOCKET_PROBE_UNEXPECTED")
            else:
                raise GateFailure("PAPER_CLOSED_SOCKET_ACCEPTED")
        finally:
            client.close()
    time.sleep(0.25)
    after = show(IB_SERVICE, (
        "ActiveState", "MainPID", "ExecMainStartTimestampMonotonic",
        "NRestarts"))
    require(
        before == after and after["ActiveState"] == "inactive" and
        after["MainPID"] == "0",
        "PAPER_SOCKET_REACTIVATED_SERVICE")
    require(
        show(BROKER_SERVICE, ("ActiveState", "SubState")) ==
        {"ActiveState": "active", "SubState": "running"},
        "CLEAN_PAPER_STOP_DID_NOT_PRESERVE_BROKER_GUARD")


def proc_ports() -> set[int]:
    ports: set[int] = set()
    for path in (Path("/proc/net/tcp"), Path("/proc/net/tcp6")):
        text = read_small(path, 1024 * 1024)
        for line in text.splitlines()[1:]:
            fields = line.split()
            if len(fields) < 3:
                continue
            for endpoint in fields[1:3]:
                if ":" in endpoint:
                    try:
                        ports.add(int(endpoint.rsplit(":", 1)[1], 16))
                    except ValueError:
                        raise GateFailure("PROC_NET_SYNTAX_INVALID")
    return ports


def assert_no_broker_ports() -> None:
    require(not (proc_ports() & BROKER_PORTS),
            "BROKER_PORT_ACTIVITY_DETECTED")


def validate_simulator_process(pid: int,
                               sockets: dict[str, tuple[int, int]],
                               binary_hash: str) -> None:
    uid, gid = SERVICE_IDENTITIES["hepta-exec"]
    parse_proc_status(pid, uid, gid)
    require(sha256_file(Path(f"/proc/{pid}/exe"),
                        trusted_proc_exe=True) == binary_hash,
            "SIMULATOR_EXECUTED_BINARY_MISMATCH")
    parse_activation_environment(pid)
    validate_activated_fds(pid, sockets)
    validate_mounted_credentials(
        pid, SIM_SERVICE,
        {"hepta-execution-fence": SIMULATOR_FENCE}, uid, gid)
    validate_state_directory(Path("/var/lib/hepta-execution"), uid, gid)
    require(os.stat(f"/proc/{pid}/ns/net").st_ino !=
            os.stat("/proc/1/ns/net").st_ino,
            "SIMULATOR_PRIVATE_NETWORK_MISSING")
    links = command([
        NSENTER, "--target", str(pid), "--net", "--", IP, "-j", "link",
        "show",
    ], label="SIMULATOR_NETWORK_INSPECTION")
    try:
        interfaces = json.loads(links.stdout.decode("utf-8", errors="strict"))
    except (UnicodeDecodeError, ValueError):
        raise GateFailure("SIMULATOR_NETWORK_EVIDENCE_INVALID")
    require(isinstance(interfaces, list) and len(interfaces) == 1 and
            interfaces[0].get("ifname") == "lo",
            "SIMULATOR_NONLOOPBACK_INTERFACE_PRESENT")


def run_real_mode(binary_hashes: dict[str, str],
                  expected_generation: int) -> dict[str, object]:
    require(show(IB_SERVICE, ("ExecMainStartTimestampMonotonic",))[
        "ExecMainStartTimestampMonotonic"] == "0",
        "REAL_MODE_IB_WAS_PREVIOUSLY_EXECUTED")
    first_sockets = start_socket_pair(SIM_SOCKETS)
    first_identity = run_client_probe(
        SERVICE_IDENTITIES["hepta-exec"][0], "both")
    require(first_identity is not None, "SIMULATOR_IDENTITY_MISSING")
    first_pid = wait_main_pid(SIM_SERVICE)
    validate_simulator_process(
        first_pid, first_sockets, binary_hashes["simulator_sha256"])
    require(socket_snapshot() == first_sockets,
            "SOCKET_INODE_CHANGED_DURING_ACTIVATION")
    root_peer_rejected(SERVICE_IDENTITIES["hepta-exec"][0])
    socket_dac_rejected(EXECUTION_SOCKET, "hepta-exec")
    first_cgroup = cgroup_path(SIM_SERVICE)
    first_pids = cgroup_pids(first_cgroup)
    require(first_pid in first_pids, "SIMULATOR_MAIN_NOT_IN_CGROUP")
    stop_service_and_assert_cgroup(SIM_SERVICE, first_pids)
    require(socket_snapshot() == first_sockets,
            "MANAGER_SOCKET_CHANGED_ON_SERVICE_STOP")
    stop_socket_pair(SIM_SOCKETS)

    second_sockets = start_socket_pair(SIM_SOCKETS)
    require(second_sockets["execution"] != first_sockets["execution"] and
            second_sockets["events"] != first_sockets["events"],
            "SOCKET_INODE_NOT_RECREATED")
    second_identity = run_client_probe(
        SERVICE_IDENTITIES["hepta-exec"][0], "both")
    require(second_identity is not None, "SIMULATOR_RESTART_IDENTITY_MISSING")
    require(second_identity["service_epoch"] !=
            first_identity["service_epoch"] and
            second_identity["service_fencing_generation"] ==
            first_identity["service_fencing_generation"] ==
            expected_generation,
            "SIMULATOR_RESTART_IDENTITY_CONTRACT")
    second_pid = wait_main_pid(SIM_SERVICE)
    validate_simulator_process(
        second_pid, second_sockets, binary_hashes["simulator_sha256"])
    second_cgroup = cgroup_path(SIM_SERVICE)
    second_pids = cgroup_pids(second_cgroup)
    require(second_pid in second_pids, "SIMULATOR_RESTART_CGROUP_INVALID")
    stop_service_and_assert_cgroup(SIM_SERVICE, second_pids)
    stop_socket_pair(SIM_SOCKETS)
    require(show(IB_SERVICE, ("ExecMainStartTimestampMonotonic",))[
        "ExecMainStartTimestampMonotonic"] == "0",
        "REAL_MODE_IB_EXECUTED")
    return {
        "simulator_socket_activation": True,
        "dual_socket_shared_identity": True,
        "service_epoch_changed_on_restart": True,
        "fencing_generation_stable_on_restart": True,
        "credential_generation_consumed": True,
        "manager_socket_inode_stable_until_socket_stop": True,
        "socket_inode_recreated_after_socket_restart": True,
        "peer_uid_rejection": True,
        "credential_mount_read_only": True,
        "credential_copy_matches_source": True,
        "private_network_loopback_only": True,
        "killmode_control_group_cleanup": True,
        "real_ibapi_elf_executed": False,
    }


class SentinelServer:
    def __init__(self, address: str, port: int):
        self._socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self._socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self._socket.bind((address, port))
        self._socket.listen(4)
        self._socket.settimeout(0.1)
        self._stop = threading.Event()
        self._lock = threading.Lock()
        self._accepts = 0
        self._thread = threading.Thread(target=self._run, daemon=True)

    def start(self) -> None:
        self._thread.start()

    def _run(self) -> None:
        while not self._stop.is_set():
            try:
                connection, _ = self._socket.accept()
            except socket.timeout:
                continue
            except OSError:
                break
            with connection:
                with self._lock:
                    self._accepts += 1

    @property
    def accepts(self) -> int:
        with self._lock:
            return self._accepts

    def close(self) -> None:
        self._stop.set()
        self._socket.close()
        self._thread.join(timeout=2.0)


def parse_sandbox_evidence(path: Path) -> dict[str, str]:
    text = read_small(path, 4096)
    values: dict[str, str] = {}
    for line in text.splitlines():
        require(line.count("=") == 1, "SANDBOX_EVIDENCE_SYNTAX")
        key, value = line.split("=", 1)
        require(key not in values and re.fullmatch(r"[a-z_]+", key) is not None,
                "SANDBOX_EVIDENCE_SYNTAX")
        values[key] = value
    expected = {
        "schema", "mode", "pid", "euid", "egid", "gateway_uid",
        "supplementary_groups_safe", "supplementary_group_count",
        "listen_fds", "execution_fd", "event_fd", "socket_contract",
        "credential_count", "credentials_readable", "credentials_read_only",
        "kill_switch", "network", "descendant_pid", "descendant_sigterm",
        "real_ibapi_linked", "mutation_requests",
    }
    require(set(values) == expected, "SANDBOX_EVIDENCE_FIELD_SET")
    require(values["schema"] ==
            "hepta.execution-systemd-sandbox-probe.v1" and
            values["mode"] == "IB_PAPER" and
            values["euid"] == "2003" and values["egid"] == "2003" and
            values["gateway_uid"] == "2001" and
            values["supplementary_groups_safe"] == "true" and
            values["supplementary_group_count"] in {"0", "1"} and
            values["listen_fds"] == "2" and
            {values["execution_fd"], values["event_fd"]} == {"3", "4"} and
            values["socket_contract"] == "verified" and
            values["credential_count"] == "2" and
            values["credentials_readable"] == "true" and
            values["credentials_read_only"] == "true" and
            values["kill_switch"] == "engaged" and
            values["network"] == "loopback_allowed_nonloopback_denied" and
            values["descendant_sigterm"] == "ignored" and
            values["real_ibapi_linked"] == "false" and
            values["mutation_requests"] == "0",
            "SANDBOX_EVIDENCE_VALUE_CONTRACT")
    require(values["pid"].isdecimal() and
            values["descendant_pid"].isdecimal() and
            int(values["pid"]) > 1 and int(values["descendant_pid"]) > 1 and
            values["pid"] != values["descendant_pid"],
            "SANDBOX_EVIDENCE_PID_INVALID")
    return values


def validate_networkless_container() -> None:
    links = command([IP, "-j", "link", "show"], label="NETWORK_LINKS")
    routes = command([IP, "-j", "route", "show", "table", "main"],
                     label="NETWORK_ROUTES")
    try:
        link_values = json.loads(links.stdout.decode("utf-8", errors="strict"))
        route_values = json.loads(routes.stdout.decode("utf-8", errors="strict"))
    except (UnicodeDecodeError, ValueError):
        raise GateFailure("NETWORK_NAMESPACE_JSON_INVALID")
    require(isinstance(link_values, list) and len(link_values) == 1 and
            link_values[0].get("ifname") == "lo" and
            isinstance(route_values, list) and not route_values,
            "CONTAINER_NETWORK_NOT_HERMETIC")


def run_sandbox_mode(binary_hashes: dict[str, str]) -> dict[str, object]:
    del binary_hashes
    validate_networkless_container()
    command([IP, "link", "set", "lo", "up"], label="LOOPBACK_UP")
    command([IP, "address", "add", "192.0.2.1/32", "dev", "lo"],
            label="NONLOOPBACK_SENTINEL_ADDRESS")
    loopback = SentinelServer("127.0.0.1", 38081)
    nonloopback = SentinelServer("192.0.2.1", 38082)
    loopback.start()
    nonloopback.start()
    try:
        with socket.create_connection(("127.0.0.1", 38081), timeout=1.0):
            pass
        with socket.create_connection(("192.0.2.1", 38082), timeout=1.0):
            pass
        wait_until(lambda: loopback.accepts == 1 and
                   nonloopback.accepts == 1,
                   "NETWORK_CONTROL_SENTINEL_NOT_REACHABLE", 3.0)
        loopback_control_accepts = loopback.accepts
        nonloopback_control_accepts = nonloopback.accepts
        sockets = start_socket_pair(IB_SOCKETS)
        systemctl("start", "--no-block", IB_SERVICE,
                  label="SANDBOX_SERVICE_START")
        evidence_path = Path(
            "/var/lib/hepta-ib-execution/"
            "execution-systemd-sandbox-probe.evidence")
        wait_until(evidence_path.is_file, "SANDBOX_EVIDENCE_NOT_PUBLISHED", 15.0)
        evidence = parse_sandbox_evidence(evidence_path)
        pid = wait_main_pid(IB_SERVICE)
        require(pid == int(evidence["pid"]), "SANDBOX_MAIN_PID_MISMATCH")
        uid, gid = SERVICE_IDENTITIES["hepta-ib-exec"]
        parse_proc_status(pid, uid, gid)
        parse_activation_environment(pid)
        validate_activated_fds(pid, sockets)
        require(sha256_file(Path(f"/proc/{pid}/exe"),
                            trusted_proc_exe=True) ==
                sha256_file(SANDBOX_BINARY),
                "SANDBOX_EXECUTED_BINARY_MISMATCH")
        validate_mounted_credentials(
            pid, IB_SERVICE,
            {
                "hepta-execution-fence": IB_FENCE,
                "hepta-ib-paper-authorization": IB_AUTHORIZATION,
            },
            uid, gid, control_directory=True)
        validate_state_directory(
            Path("/var/lib/hepta-ib-execution"), uid, gid,
            journal_required=False)
        wait_until(lambda: loopback.accepts > loopback_control_accepts,
                   "LOOPBACK_SENTINEL_NOT_ACCEPTED", 3.0)
        require(nonloopback.accepts == nonloopback_control_accepts,
                "NONLOOPBACK_SENTINEL_ACCEPTED")
        group = cgroup_path(IB_SERVICE)
        processes = cgroup_pids(group)
        descendant = int(evidence["descendant_pid"])
        require(pid in processes and descendant in processes and
                len(processes) >= 2,
                "SANDBOX_DESCENDANT_NOT_IN_CGROUP")
        stop_service_and_assert_cgroup(IB_SERVICE, processes)
        assert_paper_sockets_closed_without_reactivation()
        require(nonloopback.accepts == nonloopback_control_accepts,
                "NONLOOPBACK_SENTINEL_ACCEPTED")
    finally:
        loopback.close()
        nonloopback.close()
        command([IP, "address", "del", "192.0.2.1/32", "dev", "lo"],
                label="NONLOOPBACK_SENTINEL_CLEANUP", allowed=(0, 2))
    return {
        "canonical_ib_units_with_test_probe": True,
        "two_credentials_mounted_read_only": True,
        "credential_copies_match_sources": True,
        "kill_switch_engaged_and_read_only": True,
        "loopback_allowed": True,
        "nonloopback_control_path_reachable": True,
        "nonloopback_denied_by_systemd_ip_policy": True,
        "killmode_control_group_removed_sigterm_ignoring_descendant": True,
        "paper_stop_closed_command_and_event_sockets": True,
        "paper_socket_activation_did_not_restart_service": True,
        "clean_paper_stop_preserved_broker_guard": True,
        "mutation_requests": 0,
        "real_ibapi_elf_executed": False,
    }


def journal_has_adapter_failure() -> bool:
    completed = command([
        JOURNALCTL, "--no-pager", "--quiet", "--unit", IB_SERVICE,
        "--grep", "^IB PAPER runtime startup rejected: "
        "IB_PAPER_ADAPTER_CONNECT_FAILED$",
    ], label="STUB_JOURNAL_QUERY", allowed=(0, 1), timeout=10.0)
    return completed.returncode == 0


def stub_failure_observed() -> bool:
    values = show(IB_SERVICE, (
        "ExecMainCode", "ExecMainStatus", "Result", "NRestarts"))
    return (values["ExecMainCode"] == "1" and
            values["ExecMainStatus"] == "6" and
            values["Result"] in {"exit-code", "start-limit-hit"})


def run_stub_mode(binary_hashes: dict[str, str]) -> dict[str, object]:
    del binary_hashes
    endpoint = SentinelServer("127.0.0.1", 4002)
    endpoint.start()
    try:
        start_socket_pair(IB_SOCKETS)
        # Each single-plane read-only probe queues through a manager-owned
        # socket. The IB-disabled composition reaches the adapter connect seam,
        # exits 6 and can never publish a Ready identity on either plane.
        run_client_probe(SERVICE_IDENTITIES["hepta-ib-exec"][0], "execution",
                         expected_exit=3, domain="PAPER")
        run_client_probe(SERVICE_IDENTITIES["hepta-ib-exec"][0], "event",
                         expected_exit=4, domain="PAPER")
        wait_until(stub_failure_observed,
                   "STUB_ADAPTER_FAILURE_NOT_OBSERVED", 15.0)
        wait_until(journal_has_adapter_failure,
                   "STUB_ADAPTER_FAILURE_REASON_MISSING", 10.0)
        # The failed daemon never accepts the two queued identity probes.  Stop
        # both manager listeners and the restartable service in one systemd
        # transaction so a still-readable listener cannot reactivate it.
        systemctl("stop", IB_SERVICE, label="STUB_STACK_STOP", timeout=20.0)
        assert_paper_sockets_closed_without_reactivation()
        values = show(IB_SERVICE, ("ActiveState", "MainPID"))
        require(values == {"ActiveState": "inactive", "MainPID": "0"},
                "STUB_SERVICE_NOT_STOPPED")
        state = Path("/var/lib/hepta-ib-execution")
        uid, gid = SERVICE_IDENTITIES["hepta-ib-exec"]
        exact_stat(state, 0o700, uid, gid, "directory")
        journal = exact_stat(state / "oms-journal.jsonl", 0o600, uid, gid)
        require(journal.st_size == 0, "STUB_ORDER_JOURNAL_NOT_EMPTY")
        require(endpoint.accepts == 0,
                "STUB_CONFIGURED_ENDPOINT_CONNECTION_DETECTED")
    finally:
        endpoint.close()
    assert_no_broker_ports()
    return {
        "canonical_ib_units_with_ibapi_disabled_stub": True,
        "adapter_failure_reason": "IB_PAPER_ADAPTER_CONNECT_FAILED",
        "mutation_plane_never_ready": True,
        "event_plane_never_ready": True,
        "order_journal_bytes": 0,
        "configured_endpoint_connections": 0,
        "paper_stop_closed_command_and_event_sockets": True,
        "paper_socket_activation_did_not_restart_service": True,
        "clean_paper_stop_preserved_broker_guard": True,
        "real_broker_connections": 0,
        "real_ibapi_elf_executed": False,
    }


def all_units_inactive() -> bool:
    for unit in CANONICAL_UNITS:
        if show(unit, ("ActiveState",))["ActiveState"] != "inactive":
            return False
    return True


def cleanup(mode: str) -> None:
    # stop never activates a unit.  It is safe to stop both families in this
    # disposable guest, including the real variant where the IB service was
    # never started.
    systemctl("stop", *SIM_SOCKETS, *IB_SOCKETS, SIM_SERVICE, IB_SERVICE,
              BROKER_SERVICE, label="CLEANUP_STACK",
              allowed=(0, 5), timeout=30.0)
    assert_paths_absent((EXECUTION_SOCKET, EVENT_SOCKET),
                        "CLEANUP_SOCKET_PATH_SURVIVED")
    del mode


def assert_marker_stable(
        expected: tuple[int, int, int, int, int]) -> None:
    observed = os.lstat(KILL_SWITCH)
    require(expected == (observed.st_dev, observed.st_ino, observed.st_mode,
                         observed.st_size, observed.st_mtime_ns),
            "KILL_SWITCH_CHANGED_DURING_GATE")
    require(read_small(KILL_SWITCH, 64) == "engaged",
            "KILL_SWITCH_DISENGAGED")


def run_gate(mode: str, scope: str) -> dict[str, object]:
    base = validate_entry_context(mode, scope)
    binary_hashes = validate_binary_variant(mode)
    before_hashes = dict(binary_hashes)
    assert_no_broker_ports()
    validate_nss()
    validate_sensitive_mount_isolation()
    marker_identity = initialize_tmpfiles()
    credential_before = source_credential_metadata()
    assert_source_credentials_unreadable()
    run_preflight()
    validate_no_generator_drift()
    validate_effective_units()
    validate_journal_available()

    if mode == "real":
        mode_evidence = run_real_mode(
            binary_hashes, fence_generation(SIMULATOR_FENCE))
    elif mode == "sandbox":
        mode_evidence = run_sandbox_mode(binary_hashes)
    else:
        mode_evidence = run_stub_mode(binary_hashes)

    require(all_units_inactive(), "UNIT_ACTIVE_AT_GATE_END")
    assert_paths_absent((EXECUTION_SOCKET, EVENT_SOCKET),
                        "SOCKET_PRESENT_AT_GATE_END")
    assert_marker_stable(marker_identity)
    require(source_credential_metadata() == credential_before,
            "SOURCE_CREDENTIAL_CHANGED")
    require(validate_binary_variant(mode) == before_hashes,
            "BINARY_INPUT_CHANGED")
    assert_no_broker_ports()

    return {
        "schema": SCHEMA,
        "mode": mode,
        "passed": True,
        "checks": {
            "disposable_sentinel": True,
            "provisioned_host_preflight": True,
            "effective_units_static": True,
            "effective_units_no_dropins": True,
            "effective_units_no_generators": True,
            "journal_available": True,
            "nss_numeric_uid_isolation": True,
            "sensitive_host_bind_mounts_absent": True,
            "tmpfiles_default_engaged": True,
            "tmpfiles_idempotent": True,
            "source_credentials_root_0400": True,
            "source_credentials_service_unreadable": True,
            "kill_switch_stable_engaged": True,
            "binary_inputs_stable": True,
            "credential_content_recorded": False,
            "credential_hash_recorded": False,
            "raw_environment_recorded": False,
            "raw_journal_recorded": False,
            "mode_evidence": mode_evidence,
        },
        "platform": {
            "scope": base["scope"],
            "platform_image_sha256": base["platform_image_sha256"],
            "systemd_pid1": True,
            "pid1_cgroup_v2_root": True,
        },
        "metrics": {
            "simulator_sha256": binary_hashes["simulator_sha256"],
            "client_probe_sha256": binary_hashes["client_probe_sha256"],
            "formal_ibapi_sha256": binary_hashes["formal_ibapi_sha256"],
            "executed_ib_path_sha256":
                binary_hashes["executed_ib_path_sha256"],
            "executed_kind": binary_hashes["executed_kind"],
        },
        "boundary": {
            "real_ibapi_elf_executed": False,
            "real_broker_connections": 0,
            "paper_orders": 0,
            "live_enabled": False,
            "real_ibapi_broker_unreachable":
                "not_run_requires_separate_authorization",
        },
    }


def readonly_probe(paths: list[str]) -> int:
    if not paths:
        return 2
    allowed_errors = {errno.EACCES, errno.EPERM, errno.EROFS}
    for raw_path in paths:
        if not raw_path.startswith("/") or "\x00" in raw_path:
            return 2
        path = Path(raw_path)
        operations = (
            lambda: os.open(path, os.O_WRONLY | os.O_CLOEXEC | os.O_NOFOLLOW),
            lambda: os.chmod(path, 0o600, follow_symlinks=False),
            lambda: os.unlink(path),
            lambda: os.rename(path, path.with_name(path.name + ".mutation-probe")),
        )
        for operation in operations:
            try:
                result = operation()
            except OSError as error:
                if error.errno not in allowed_errors:
                    return 1
                continue
            if isinstance(result, int):
                os.close(result)
            return 1
    return 0


def socket_denied_probe(raw_path: str) -> int:
    if not raw_path.startswith("/") or "\x00" in raw_path:
        return 2
    client = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    client.settimeout(1.0)
    try:
        client.connect(raw_path)
    except OSError as error:
        return 0 if error.errno in {errno.EACCES, errno.EPERM} else 1
    finally:
        client.close()
    return 1


def safe_boundary() -> dict[str, object]:
    return {
        "real_ibapi_elf_executed": "unknown",
        "real_broker_connections": "unknown",
        "paper_orders": "unknown",
        "live_enabled": "unknown",
        "real_ibapi_broker_unreachable":
            "not_run_requires_separate_authorization",
    }


def failure_report(mode: str, code: str) -> dict[str, object]:
    safe_mode = mode if mode in {"real", "sandbox", "stub"} else "invalid"
    return {
        "schema": SCHEMA,
        "mode": safe_mode,
        "passed": False,
        "checks": {
            "error_code": code,
            "credential_content_recorded": False,
            "credential_hash_recorded": False,
            "raw_environment_recorded": False,
            "raw_journal_recorded": False,
        },
        "platform": {},
        "metrics": {},
        "boundary": safe_boundary(),
    }


def main() -> int:
    if len(sys.argv) >= 2:
        if sys.argv[1] == "--readonly-probe":
            return readonly_probe(sys.argv[2:])
        if sys.argv[1] == "--socket-denied-probe" and len(sys.argv) == 3:
            return socket_denied_probe(sys.argv[2])

    if len(sys.argv) == 3 and sys.argv[1] == "--mode":
        mode = sys.argv[2]
        scope = CONTAINER_SCOPE
    elif (len(sys.argv) == 5 and sys.argv[1] == "--mode" and
          sys.argv[3] == "--scope"):
        mode = sys.argv[2]
        scope = sys.argv[4]
    else:
        mode = "invalid"
        scope = "invalid"

    report: dict[str, object]
    exit_code = 1
    cleanup_error = False
    try:
        require(mode in {"real", "sandbox", "stub"}, "MODE_ARGUMENT_INVALID")
        require(scope in {CONTAINER_SCOPE, NATIVE_SCOPE},
                "SCOPE_ARGUMENT_INVALID")
        report = run_gate(mode, scope)
        exit_code = 0
    except GateFailure as error:
        report = failure_report(mode, error.code)
    except BaseException as error:  # Never serialize a possibly sensitive text.
        code = "UNEXPECTED_" + re.sub(
            r"[^A-Z0-9_]", "_", type(error).__name__.upper())[:64]
        report = failure_report(mode, code)
    finally:
        if mode in {"real", "sandbox", "stub"}:
            try:
                cleanup(mode)
            except BaseException:
                cleanup_error = True
        if cleanup_error:
            report["passed"] = False
            checks = report.get("checks")
            if isinstance(checks, dict):
                checks["error_code"] = "CLEANUP_FAILED"
            exit_code = 1
        encoded = json.dumps(
            report, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
        print(MARKER + encoded, flush=True)
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
