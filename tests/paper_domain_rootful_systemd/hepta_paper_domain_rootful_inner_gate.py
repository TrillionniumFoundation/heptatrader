#!/usr/bin/env python3

"""Exercise the real templated PAPER units inside disposable systemd PID 1."""

from __future__ import annotations

import hashlib
import json
import importlib.util
import os
from pathlib import Path
import re
import signal
import socket
import stat
import subprocess
import sys
import time
from typing import Iterable


SCHEMA = "hepta.paper-domain-rootful-systemd-inner.v2"
MARKER = "HEPTA_PAPER_DOMAIN_ROOTFUL_SYSTEMD_RESULT="
BROKER = "hepta-broker-egress-policy.service"
DOMAIN_UNITS = {
    domain: (
        f"hepta-ib-paper-domain-preflight@{domain}.service",
        f"hepta-execution-ib-paper@{domain}.service",
        f"hepta-execution-ib-paper@{domain}.socket",
        f"hepta-execution-events-ib-paper@{domain}.socket",
    )
    for domain in ("codex-a", "openclaw-b")
}
SOCKETS = {
    domain: (
        Path(f"/run/hepta-execution-{domain}/execution.sock"),
        Path(f"/run/hepta-execution-{domain}/events.sock"),
    )
    for domain in DOMAIN_UNITS
}
OWNER = Path("/run/hepta/ib-paper-host-authority/owner.v1")
NETWORK = Path(
    "/etc/heptatrader/hepta-agent-trust-domain-paper-identities-v1.json")
AUTHORITY = Path(
    "/etc/heptatrader/hepta-ib-paper-domain-authorizations-v1.json")
POLICY = Path("/usr/share/heptatrader/hepta-broker-network-policy-v1.json")
IDENTITIES = Path("/usr/share/heptatrader/hepta-service-identities-v1.json")
HELPER = "/usr/libexec/hepta-broker-egress-policy"
SENTINEL = Path("/run/hepta-paper-domain-rootful-systemd.disposable")
PROTECTED_PORTS = {4001, 4002, 7496, 7497}
# Debian's systemd reports the canonical merged-/usr alias as /lib even though
# the image copies the same inode through /usr/lib.
UNIT_ROOT = Path("/lib/systemd/system")
DROPIN_ROOT = Path("/usr/lib/systemd/system")
BROKER_PAPER_OPT_IN_DROPIN = Path(
    "/run/systemd/system/hepta-broker-egress-policy.service.d/"
    "90-explicit-paper-opt-in-gate.conf")
SERVICE_DROPINS = (
    DROPIN_ROOT /
    "hepta-execution-ib-paper@.service.d/"
    "10-hepta-broker-egress-policy.conf",
    DROPIN_ROOT /
    "hepta-execution-ib-paper@.service.d/90-rootful-gate.conf",
)
SAFE_ENV = {
    "PATH": "/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin",
    "LANG": "C.UTF-8",
    "LC_ALL": "C.UTF-8",
    "SYSTEMD_COLORS": "0",
    "SYSTEMD_PAGER": "",
    "SYSTEMD_PAGERSECURE": "1",
}
MAX_OUTPUT = 2 * 1024 * 1024


class GateFailure(RuntimeError):
    pass


def fail(message: str) -> None:
    raise GateFailure(message)


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
        detail = detail.replace("\n", " | ").strip(" |")
        fail(
            f"command failed rc={completed.returncode}: "
            f"{Path(arguments[0]).name}: {detail}")
    return completed


def systemctl(
        *arguments: str, allowed: Iterable[int] = (0,),
        timeout: float = 45.0) -> subprocess.CompletedProcess[str]:
    expected = set(allowed)
    completed = command(
        ["/usr/bin/systemctl", "--no-pager", "--no-ask-password", *arguments],
        allowed=range(0, 256),
        timeout=timeout,
    )
    if completed.returncode not in expected:
        status_output = ""
        for item in arguments:
            if re.fullmatch(r"[A-Za-z0-9@_.:-]+\.(?:service|socket)", item):
                status = command(
                    [
                        "/usr/bin/systemctl", "--no-pager",
                        "--no-ask-password", "status", "--full", item,
                    ],
                    allowed=range(0, 256),
                )
                status_output += status.stdout + status.stderr
        journal = command(
            [
                "/usr/bin/journalctl", "--no-pager", "-n", "40",
                "--output=short-monotonic",
            ],
            allowed=(0, 1),
        )
        detail = (
            completed.stdout + completed.stderr + "\nstatus=" +
            status_output + "\njournal=" +
            journal.stdout + journal.stderr
        )[-4096:].replace("\n", " | ").strip(" |")
        fail(
            f"systemctl failed rc={completed.returncode}: " + detail)
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
        fail(
            "systemd property set mismatch: requested=" +
            ",".join(sorted(requested)) + " observed=" +
            ",".join(sorted(values)))
    return values


def require_regular_unit_file(path: Path) -> None:
    metadata = os.lstat(path)
    if (
            not stat.S_ISREG(metadata.st_mode) or
            stat.S_ISLNK(metadata.st_mode) or metadata.st_nlink != 1 or
            metadata.st_uid != 0 or metadata.st_gid != 0 or
            stat.S_IMODE(metadata.st_mode) != 0o644):
        fail("loaded systemd source metadata mismatch")


def require_exec_property(
        value: str, arguments: tuple[str, ...], label: str) -> None:
    if not arguments:
        if value:
            fail(f"{label} unexpectedly exists")
        return
    executable = arguments[0]
    argv = " ".join(arguments)
    if (
            value.count("{ path=") != 1 or
            f"path={executable} ;" not in value or
            f"argv[]={argv} ;" not in value or
            "ignore_errors=no" not in value):
        fail(
            f"{label} effective command mismatch: " +
            json.dumps({"expected_argv": argv, "observed": value},
                       sort_keys=True))


def require_successful_exec_stop_post(value: str) -> None:
    expected = (
        r"\{ path=/usr/bin/python3\.12 ; "
        r"argv\[\]=/usr/bin/python3\.12 \-I \-S "
        r"(?:/run/credentials/hepta\-broker\-egress\-policy\.service|"
        r"\$\{CREDENTIALS_DIRECTORY\})/"
        r"hepta\-broker\-egress\-policy\.py "
        r"\-\-tighten\-deny\-all ; ignore_errors=no ; "
        r"start_time=\[[^\]\r\n]+\] ; stop_time=\[[^\]\r\n]+\] ; "
        r"pid=[1-9][0-9]* ; code=exited ; status=0(?:/0)? \}")
    if re.fullmatch(expected, value) is None:
        fail(
            "broker ExecStopPost execution result mismatch: " +
            json.dumps({"ExecStopPost": value}, sort_keys=True))


def require_unit_contract(
        unit: str, fragment: Path, dropins: tuple[Path, ...],
        start: tuple[str, ...] = (), stop_post: tuple[str, ...] = (),
        ) -> None:
    names = ["LoadState", "FragmentPath", "DropInPaths"]
    if unit.endswith(".service"):
        names.append("ExecStart")
        if stop_post:
            names.append("ExecStopPost")
    values = properties(unit, names)
    if (
            values["LoadState"] != "loaded" or
            values["FragmentPath"] != str(fragment) or
            tuple(values["DropInPaths"].split()) !=
            tuple(str(path) for path in dropins)):
        fail(
            f"{unit}: effective systemd source mismatch: " +
            json.dumps({
                "observed": {
                    "LoadState": values["LoadState"],
                    "FragmentPath": values["FragmentPath"],
                    "DropInPaths": values["DropInPaths"],
                },
                "expected": {
                    "LoadState": "loaded",
                    "FragmentPath": str(fragment),
                    "DropInPaths": " ".join(str(path) for path in dropins),
                },
            }, sort_keys=True))
    require_regular_unit_file(fragment)
    for dropin in dropins:
        require_regular_unit_file(dropin)
    if unit.endswith(".service"):
        require_exec_property(values["ExecStart"], start, unit + " ExecStart")
        if stop_post:
            require_exec_property(
                values["ExecStopPost"], stop_post, unit + " ExecStopPost")


def require_loaded_unit_contracts() -> None:
    require_unit_contract(
        BROKER,
        UNIT_ROOT / "hepta-broker-egress-policy.service",
        (BROKER_PAPER_OPT_IN_DROPIN,),
        (
            "/usr/bin/python3.12", "-I", "-S",
            "${CREDENTIALS_DIRECTORY}/hepta-broker-egress-policy.py",
            "--supervise", "--paper-identities",
            "/etc/heptatrader/"
            "hepta-agent-trust-domain-paper-identities-v1.json",
        ),
        (
            "/usr/bin/python3.12", "-I", "-S",
            "${CREDENTIALS_DIRECTORY}/hepta-broker-egress-policy.py",
            "--tighten-deny-all",
        ),
    )
    for domain, units in DOMAIN_UNITS.items():
        preflight_dropins: tuple[Path, ...] = ()
        preflight_start = (
            "/usr/libexec/hepta-ib-paper-domain-authority",
            "--guard", "--domain", domain,
        )
        if domain == "openclaw-b":
            preflight_dropins = (
                DROPIN_ROOT /
                "hepta-ib-paper-domain-preflight@openclaw-b.service.d/"
                "90-rootful-gate-manifests.conf",
            )
            preflight_start = (
                "/usr/libexec/hepta-ib-paper-domain-authority",
                "--network-identities="
                "/etc/heptatrader/test-openclaw-b-network.json",
                "--authorizations="
                "/etc/heptatrader/test-openclaw-b-authority.json",
                "--guard", "--domain", domain,
            )
        require_unit_contract(
            units[0],
            UNIT_ROOT / "hepta-ib-paper-domain-preflight@.service",
            preflight_dropins,
            preflight_start,
            (
                "/usr/libexec/hepta-ib-paper-domain-authority",
                "--finalize-stop", "--domain", domain,
            ),
        )
        require_unit_contract(
            units[1],
            UNIT_ROOT / "hepta-execution-ib-paper@.service",
            SERVICE_DROPINS,
            ("/usr/libexec/hepta-ib-executiond",),
        )
        require_unit_contract(
            units[2],
            UNIT_ROOT / "hepta-execution-ib-paper@.socket",
            (),
        )
        require_unit_contract(
            units[3],
            UNIT_ROOT / "hepta-execution-events-ib-paper@.socket",
            (),
        )


def wait_not_active(
        units: Iterable[str], timeout: float = 15.0, phase: str = "") -> None:
    expected = tuple(units)
    deadline = time.monotonic() + timeout
    while True:
        states = [
            properties(unit, ("ActiveState",))["ActiveState"]
            for unit in expected
        ]
        if all(
                state not in {
                    "active", "activating", "reloading", "deactivating"}
                for state in states):
            return
        if time.monotonic() >= deadline:
            fail(
                "PAPER unit did not stop within bound" +
                (f" during {phase}" if phase else "") +
                ": " + ",".join(
                    f"{unit}={state}"
                    for unit, state in zip(expected, states)))
        time.sleep(0.1)


def wait_active(units: Iterable[str], timeout: float = 15.0) -> None:
    expected = tuple(units)
    deadline = time.monotonic() + timeout
    while True:
        if all(
                properties(unit, ("ActiveState",))["ActiveState"] == "active"
                for unit in expected):
            return
        if time.monotonic() >= deadline:
            fail("PAPER unit did not become active within bound")
        time.sleep(0.1)


def wait_restarted(unit: str, previous_pid: int, timeout: float = 45.0) -> int:
    deadline = time.monotonic() + timeout
    while True:
        values = properties(unit, ("ActiveState", "MainPID"))
        try:
            observed_pid = int(values["MainPID"], 10)
        except ValueError as error:
            raise GateFailure("restarted daemon PID malformed") from error
        if (
                values["ActiveState"] == "active" and
                observed_pid > 1 and observed_pid != previous_pid):
            return observed_pid
        if time.monotonic() >= deadline:
            diagnostics = properties(
                unit,
                (
                    "ActiveState", "SubState", "Result", "MainPID",
                    "NRestarts", "StartLimitBurst",
                    "StartLimitIntervalUSec",
                ),
            )
            domain_match = re.fullmatch(
                r"hepta-execution-ib-paper@([A-Za-z0-9_-]+)\.service",
                unit,
            )
            preflight_unit = (
                "hepta-ib-paper-domain-preflight@" +
                domain_match.group(1) + ".service"
                if domain_match is not None else "")
            preflight_diagnostics = (
                properties(
                    preflight_unit,
                    (
                        "ActiveState", "SubState", "Result", "MainPID",
                        "NRestarts", "StartLimitBurst",
                        "StartLimitIntervalUSec",
                    ),
                )
                if preflight_unit else {})
            journal = command(
                [
                    "/usr/bin/journalctl", "--no-pager", "--lines=80",
                    "--output=cat", "--unit=" + unit,
                    *(("--unit=" + preflight_unit,) if preflight_unit else ()),
                ],
                allowed=(0, 1),
            ).stdout
            fail(
                "PAPER daemon did not restart within bound: " +
                json.dumps(
                    {
                        "properties": diagnostics,
                        "preflight_properties": preflight_diagnostics,
                        "journal_tail": journal[-4096:],
                    },
                    sort_keys=True,
                ))
        time.sleep(0.1)


def require_paths_absent(domain: str) -> None:
    for path in SOCKETS[domain]:
        if path.exists() or path.is_symlink():
            fail("stopped PAPER socket path remains")


def require_at_most_one_socket_domain() -> None:
    observed = [
        domain for domain in DOMAIN_UNITS
        if any(path.exists() or path.is_symlink() for path in SOCKETS[domain])
    ]
    if len(observed) > 1:
        fail("concurrent cold start exposed two PAPER socket domains")


def require_socket_paths(domain: str, uid: int, gid: int) -> None:
    for path in SOCKETS[domain]:
        metadata = os.lstat(path)
        if (
                not stat.S_ISSOCK(metadata.st_mode) or
                stat.S_IMODE(metadata.st_mode) != 0o600 or
                metadata.st_uid != uid or metadata.st_gid != gid):
            fail("PAPER socket metadata mismatch")


def require_owner(domain: str | None, phase: str = "") -> None:
    if domain is None:
        if OWNER.exists() or OWNER.is_symlink():
            fail(
                "PAPER owner tombstone was not cleared" +
                (f" after {phase}" if phase else ""))
        return
    value = OWNER.read_text(encoding="ascii", errors="strict")
    expected = f"HEPTA_IB_PAPER_OWNER_V1\n{domain}\n"
    if value != expected:
        fail(
            "PAPER owner tombstone mismatch: "
            f"expected={domain}")


def read_owner_domain() -> str | None:
    if not OWNER.exists():
        return None
    text = OWNER.read_text(encoding="ascii", errors="strict")
    lines = text.splitlines()
    if (
            len(lines) != 2 or text[-1:] != "\n" or
            lines[0] != "HEPTA_IB_PAPER_OWNER_V1" or
            lines[1] not in DOMAIN_UNITS):
        fail("PAPER owner tombstone is malformed")
    return lines[1]


def require_inert_credentials() -> int:
    root = Path("/etc/heptatrader/credentials")
    expected_directories = {
        root: 0o755,
        root / "trust-domains": 0o755,
        root / "trust-domains/codex-a": 0o700,
        root / "trust-domains/openclaw-b": 0o700,
    }
    expected_files = {
        root / f"trust-domains/{domain}/{name}": payload
        for domain in DOMAIN_UNITS
        for name, payload in (
            (
                "hepta-execution-ib-paper-fence",
                b"INERT_ROOTFUL_SYSTEMD_FIXTURE_NO_TRADING_AUTHORITY\n",
            ),
            (
                "hepta-ib-paper-authorization",
                b"INERT_ROOTFUL_SYSTEMD_FIXTURE_NO_BROKER_CREDENTIAL\n",
            ),
        )
    }
    observed = {root, *root.rglob("*")}
    if observed != set(expected_directories) | set(expected_files):
        fail("credential fixture allowlist mismatch")
    for directory, expected_mode in expected_directories.items():
        metadata = os.lstat(directory)
        if (
                not stat.S_ISDIR(metadata.st_mode) or
                stat.S_ISLNK(metadata.st_mode) or
                metadata.st_uid != 0 or metadata.st_gid != 0 or
                stat.S_IMODE(metadata.st_mode) != expected_mode):
            fail("credential fixture directory metadata mismatch")
    for path, payload in expected_files.items():
        metadata = os.lstat(path)
        if (
                not stat.S_ISREG(metadata.st_mode) or
                stat.S_ISLNK(metadata.st_mode) or metadata.st_nlink != 1 or
                metadata.st_uid != 0 or metadata.st_gid != 0 or
                stat.S_IMODE(metadata.st_mode) != 0o400 or
                path.read_bytes() != payload):
            fail("inert credential fixture mismatch")
    return len(expected_files)


def require_no_ib_api_payload() -> dict[str, object]:
    execution_stub = Path("/usr/libexec/hepta-ib-executiond")
    metadata = os.lstat(execution_stub)
    raw = execution_stub.read_bytes()
    if (
            not stat.S_ISREG(metadata.st_mode) or
            stat.S_ISLNK(metadata.st_mode) or metadata.st_nlink != 1 or
            metadata.st_uid != 0 or metadata.st_gid != 0 or
            stat.S_IMODE(metadata.st_mode) != 0o755):
        fail("inert execution stub metadata mismatch")
    for token in (
            b"import ibapi", b"EClientSocket", b"placeOrder(",
            b"reqIds(", b"trade.place_order"):
        if token in raw:
            fail("IB API/order protocol token found in inert stub")
    if importlib.util.find_spec("ibapi") is not None:
        fail("Python ibapi package is present in disposable image")
    forbidden_name = re.compile(
        r"^(?:ibapi|eclientsocket|ewrapper|eclient|twsapi|"
        r"libibapi|libtwsapi)(?:[._-].*|\\.(?:h|hpp|so|a|py))?$",
        re.IGNORECASE,
    )
    excluded = {
        Path("/dev"), Path("/proc"), Path("/run"), Path("/sys"),
        Path("/tmp"), Path("/var/log"), Path("/var/tmp"),
        Path("/var/lib/hepta-ib-execution-codex-a"),
        Path("/var/lib/hepta-ib-execution-openclaw-b"),
        Path("/etc/heptatrader"), Path("/usr/share/heptatrader"),
    }
    observed: list[str] = []
    inventory = hashlib.sha256()
    inventory_count = 0
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
                observed.append(str(child))
        directories[:] = kept
        for name in sorted(files):
            path = current_path / name
            if forbidden_name.fullmatch(name):
                observed.append(str(path))
            metadata = os.lstat(path)
            inventory_count += 1
            if inventory_count > 200_000:
                fail("immutable image file inventory exceeds bound")
            inventory.update(str(path).encode("utf-8", errors="strict"))
            inventory.update(b"\0")
            inventory.update(
                (
                    f"{metadata.st_mode:o}:{metadata.st_uid}:"
                    f"{metadata.st_gid}:{metadata.st_size}\n"
                ).encode("ascii"))
    if observed:
        fail("IB API binary/header payload found in disposable image")
    observed_packages = command([
        "/usr/bin/dpkg-query", "-W",
        "-f=${binary:Package}=${Version}\\n",
    ]).stdout.splitlines()
    if (
            not observed_packages or len(observed_packages) > 10_000 or
            len(set(observed_packages)) != len(observed_packages) or
            any(
                re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9+.:~-]*=[^=\r\n]+",
                             package) is None
                for package in observed_packages)):
        fail("base package inventory is not canonical")
    packages = sorted(observed_packages)
    package_raw = ("\n".join(packages) + "\n").encode("utf-8")
    return {
        "ib_api_binaries": 0,
        "immutable_file_count": inventory_count,
        "immutable_file_inventory_sha256": inventory.hexdigest(),
        "package_count": len(packages),
        "package_inventory_sha256": hashlib.sha256(package_raw).hexdigest(),
    }


def protected_tcp_socket_count() -> int:
    count = 0
    for path in (Path("/proc/net/tcp"), Path("/proc/net/tcp6")):
        lines = path.read_text(
            encoding="ascii", errors="strict").splitlines()[1:]
        for line in lines:
            fields = line.split()
            if len(fields) < 3:
                fail("kernel TCP socket table row malformed")
            try:
                local_port = int(fields[1].rsplit(":", 1)[1], 16)
                remote_port = int(fields[2].rsplit(":", 1)[1], 16)
            except (IndexError, ValueError) as error:
                raise GateFailure("kernel TCP socket port malformed") from error
            if local_port in PROTECTED_PORTS or remote_port in PROTECTED_PORTS:
                count += 1
    if count:
        fail("broker-port socket exists in disposable container")
    return count


def require_exact_inert_process(domain: str, uid: int) -> int:
    pids: list[int] = []
    for entry in Path("/proc").iterdir():
        if not entry.name.isdecimal():
            continue
        try:
            arguments = (entry / "cmdline").read_bytes().split(b"\0")
        except (FileNotFoundError, PermissionError, ProcessLookupError):
            continue
        if b"/usr/libexec/hepta-ib-executiond" in arguments:
            pids.append(int(entry.name, 10))
    if len(pids) != 1:
        fail("inert execution process count mismatch")
    status = (Path("/proc") / str(pids[0]) / "status").read_text(
        encoding="ascii", errors="strict")
    uid_line = next(
        (line for line in status.splitlines() if line.startswith("Uid:")),
        "")
    gid_line = next(
        (line for line in status.splitlines() if line.startswith("Gid:")),
        "")
    if (
            uid_line.split()[1:] != [str(uid)] * 4 or
            gid_line.split()[1:] != [str(uid)] * 4):
        fail("inert execution process identity mismatch")
    marker = Path(f"/var/lib/hepta-ib-execution-{domain}/inert-stub.started")
    if not marker.is_file():
        fail("inert execution process marker missing")
    return len(pids)


def require_zero_order_state() -> int:
    order_tokens = (b"place_order", b"placeOrder", b"order_id", b"orderId")
    count = 0
    for domain in DOMAIN_UNITS:
        root = Path(f"/var/lib/hepta-ib-execution-{domain}")
        if not root.exists():
            continue
        for path in root.rglob("*"):
            metadata = os.lstat(path)
            if stat.S_ISDIR(metadata.st_mode):
                continue
            if (
                    not stat.S_ISREG(metadata.st_mode) or
                    stat.S_ISLNK(metadata.st_mode) or
                    path.name != "inert-stub.started"):
                fail("unexpected execution state artifact")
            raw = path.read_bytes()
            if any(token in raw for token in order_tokens):
                count += 1
    if count:
        fail("order-shaped state found in inert execution fixture")
    return count


def check_network(*arguments: str) -> None:
    completed = command([HELPER, *arguments], allowed=(0, 1))
    if completed.returncode != 0:
        live = command(
            [
                "/usr/sbin/nft", "--json", "list", "table", "inet",
                "hepta_broker_egress_v1",
            ],
            allowed=(0, 1),
        )
        detail = (
            completed.stdout + completed.stderr + "\nlive=" +
            live.stdout + live.stderr
        )[-4096:].replace("\n", " | ").strip(" |")
        fail("broker network verification failed: " + detail)
    if completed.stderr:
        fail("broker network check emitted stderr")


def atomic_write(path: Path, payload: bytes, mode: int = 0o600) -> None:
    temporary = path.with_name(path.name + ".gate-replacement")
    descriptor = os.open(
        temporary,
        os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_CLOEXEC |
        getattr(os, "O_NOFOLLOW", 0),
        mode,
    )
    try:
        view = memoryview(payload)
        while view:
            written = os.write(descriptor, view)
            if written <= 0:
                fail("short atomic input write")
            view = view[written:]
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
    os.replace(temporary, path)
    directory = os.open(
        path.parent, os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC)
    try:
        os.fsync(directory)
    finally:
        os.close(directory)


def install_explicit_paper_supervisor_dropin() -> None:
    fragment = UNIT_ROOT / "hepta-broker-egress-policy.service"
    require_regular_unit_file(fragment)
    source = fragment.read_text(encoding="utf-8", errors="strict")
    shipped_contract = (
        "LoadCredential=hepta-broker-egress-policy.py:"
        "/usr/libexec/hepta-broker-egress-policy",
        "ExecStart=/usr/bin/python3.12 -I -S "
        "${CREDENTIALS_DIRECTORY}/hepta-broker-egress-policy.py "
        "--supervise-deny-all --paper-identities "
        "/etc/heptatrader/"
        "hepta-agent-trust-domain-paper-identities-v1.json",
        "ExecStopPost=/usr/bin/python3.12 -I -S "
        "${CREDENTIALS_DIRECTORY}/hepta-broker-egress-policy.py "
        "--tighten-deny-all",
    )
    lines = source.splitlines()
    if any(lines.count(value) != 1 for value in shipped_contract):
        fail("shipped broker service is not credential-bound deny-all")
    if " --supervise --paper-identities " in source:
        fail("shipped broker service unexpectedly opts in to PAPER")

    parent = BROKER_PAPER_OPT_IN_DROPIN.parent
    parent.mkdir(parents=True, mode=0o755, exist_ok=False)
    metadata = os.lstat(parent)
    if (
            not stat.S_ISDIR(metadata.st_mode) or
            stat.S_ISLNK(metadata.st_mode) or
            metadata.st_uid != 0 or metadata.st_gid != 0 or
            stat.S_IMODE(metadata.st_mode) != 0o755):
        fail("PAPER opt-in drop-in directory metadata mismatch")
    payload = (
        "[Service]\n"
        "ExecStart=\n"
        "ExecStart=/usr/bin/python3.12 -I -S "
        "${CREDENTIALS_DIRECTORY}/hepta-broker-egress-policy.py "
        "--supervise --paper-identities "
        "/etc/heptatrader/"
        "hepta-agent-trust-domain-paper-identities-v1.json\n"
        "ExecStopPost=\n"
        "ExecStopPost=/usr/bin/python3.12 -I -S "
        "${CREDENTIALS_DIRECTORY}/hepta-broker-egress-policy.py "
        "--tighten-deny-all\n"
    ).encode("ascii")
    atomic_write(BROKER_PAPER_OPT_IN_DROPIN, payload, 0o644)
    systemctl("daemon-reload")


def set_stub_mode(domain: str, mode: str) -> None:
    if domain not in DOMAIN_UNITS or mode not in {"hold", "fail"}:
        fail("invalid inert stub configuration")
    path = Path(f"/etc/heptatrader/trust-domains/{domain}.ib-paper.env")
    payload = (
        f"HEPTA_IB_EXECUTION_GATEWAY_AGENT_ID={domain}\n"
        f"HEPTA_PAPER_STUB_MODE={mode}\n"
    ).encode("ascii")
    atomic_write(path, payload, 0o644)


def replace_paper_manifests(
        network_source: Path, authority_source: Path) -> None:
    for source in (network_source, authority_source):
        metadata = os.lstat(source)
        if (
                not stat.S_ISREG(metadata.st_mode) or
                stat.S_ISLNK(metadata.st_mode) or metadata.st_nlink != 1 or
                metadata.st_uid != 0 or metadata.st_gid != 0 or
                stat.S_IMODE(metadata.st_mode) != 0o600 or
                metadata.st_size < 1 or metadata.st_size > 1024 * 1024):
            fail("replacement PAPER manifest metadata mismatch")
    atomic_write(NETWORK, network_source.read_bytes())
    atomic_write(AUTHORITY, authority_source.read_bytes())


def validate_platform() -> tuple[str, dict[str, str], dict[str, str]]:
    if (
            os.geteuid() != 0 or os.getegid() != 0 or
            Path("/proc/1/comm").read_text(
                encoding="ascii", errors="strict").strip() != "systemd"):
        fail("gate requires container root and systemd PID 1")
    token = SENTINEL.read_text(encoding="ascii", errors="strict").strip()
    if re.fullmatch(r"[0-9a-f]{32}", token) is None:
        fail("disposable sentinel invalid")
    if (
            Path("/run/docker.sock").exists() or
            Path("/var/run/docker.sock").exists()):
        fail("Docker socket leaked into container")
    links = json.loads(command(["/usr/sbin/ip", "-json", "link", "show"]).stdout)
    if (
            not isinstance(links, list) or len(links) != 1 or
            links[0].get("ifname") != "lo"):
        fail("container is not loopback-only")
    systemd = command(["/usr/bin/systemctl", "--version"]).stdout.splitlines()[0]
    nft = command(["/usr/sbin/nft", "--version"]).stdout.strip()
    kernel = os.uname().release
    boot_id = Path("/proc/sys/kernel/random/boot_id").read_text(
        encoding="ascii", errors="strict").strip()
    pid1_cgroup = Path("/proc/1/cgroup").read_text(
        encoding="ascii", errors="strict").strip()
    if (
            re.fullmatch(
                r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-"
                r"[0-9a-f]{4}-[0-9a-f]{12}", boot_id) is None or
            pid1_cgroup != "0::/"):
        fail("container boot/cgroup evidence invalid")
    return token, {
        "systemd": systemd,
        "nft": nft,
        "kernel": kernel,
        "architecture": os.uname().machine,
        "cgroup": "v2-private",
    }, {"boot_id": boot_id, "pid1_cgroup": pid1_cgroup}


def manual_socket_refused(domain: str) -> None:
    for unit in DOMAIN_UNITS[domain][2:]:
        result = systemctl("start", unit, allowed=(1, 4, 5))
        if result.returncode == 0:
            fail("manual PAPER socket start was accepted")
    require_paths_absent(domain)


def manual_preflight_refused(domain: str) -> None:
    result = systemctl(
        "start", DOMAIN_UNITS[domain][0], allowed=(1, 4, 5))
    if result.returncode == 0:
        fail("manual PAPER preflight start was accepted")
    wait_not_active(
        (DOMAIN_UNITS[domain][0],),
        phase="manual preflight refusal")
    require_paths_absent(domain)
    require_owner(None, "manual preflight refusal")


def spawn_systemctl_start(unit: str) -> subprocess.Popen[str]:
    return subprocess.Popen(
        [
            "/usr/bin/systemctl", "--no-pager", "--no-ask-password",
            "start", "--no-block", unit,
        ],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="strict",
        env=SAFE_ENV,
        cwd="/",
        close_fds=True,
    )


def concurrent_cold_start() -> str:
    require_owner(None, "before concurrent cold start")
    for domain in DOMAIN_UNITS:
        require_paths_absent(domain)
    first = spawn_systemctl_start(DOMAIN_UNITS["codex-a"][1])
    second = spawn_systemctl_start(DOMAIN_UNITS["openclaw-b"][1])
    for process in (first, second):
        stdout, stderr = process.communicate(timeout=10)
        if (
                process.returncode not in {0, 1, 4, 5} or
                len((stdout + stderr).encode("utf-8")) > MAX_OUTPUT):
            fail("concurrent systemd start process failed unexpectedly")
    deadline = time.monotonic() + 2
    winner: str | None = None
    while time.monotonic() < deadline:
        require_at_most_one_socket_domain()
        winner = read_owner_domain()
        if winner is not None:
            break
        time.sleep(0.002)
    if winner not in DOMAIN_UNITS:
        fail("concurrent cold start never established one host owner")
    loser = next(domain for domain in DOMAIN_UNITS if domain != winner)
    deadline = time.monotonic() + 10
    while True:
        require_at_most_one_socket_domain()
        winner_states = [
            properties(unit, ("ActiveState",))["ActiveState"]
            for unit in DOMAIN_UNITS[winner]
        ]
        loser_states = [
            properties(unit, ("ActiveState",))["ActiveState"]
            for unit in DOMAIN_UNITS[loser]
        ]
        if (
                all(state == "active" for state in winner_states) and
                all(state not in {
                    "active", "activating", "reloading", "deactivating"}
                    for state in loser_states)):
            break
        if time.monotonic() >= deadline:
            fail(
                "concurrent cold-start winner/loser did not settle: " +
                f"winner={winner}:{winner_states} "
                f"loser={loser}:{loser_states}")
        time.sleep(0.01)
    require_owner(winner)
    require_socket_paths(
        winner, 2101 if winner == "codex-a" else 2102,
        2101 if winner == "codex-a" else 2102)
    require_paths_absent(loser)
    winner_network = (
        NETWORK if winner == "codex-a" else
        Path("/etc/heptatrader/test-openclaw-b-network.json"))
    check_network(
        "--paper-identities", str(winner_network), "--check-active")
    systemctl(
        "stop",
        DOMAIN_UNITS["codex-a"][1],
        DOMAIN_UNITS["openclaw-b"][1],
        DOMAIN_UNITS["codex-a"][0],
        DOMAIN_UNITS["openclaw-b"][0],
        allowed=(0, 1, 5),
    )
    wait_not_active(
        (*DOMAIN_UNITS["codex-a"], *DOMAIN_UNITS["openclaw-b"]),
        phase="concurrent cold-start cleanup")
    for domain in DOMAIN_UNITS:
        require_paths_absent(domain)
    require_owner(None, "concurrent cold-start cleanup")
    check_network("--check-deny-all")
    systemctl("restart", BROKER)
    wait_active((BROKER,))
    check_network("--check-deny-all")
    return winner


def start_domain(domain: str) -> None:
    systemctl("start", DOMAIN_UNITS[domain][1])
    wait_active(DOMAIN_UNITS[domain])


def require_daemon_sigkill_restart(domain: str, uid: int) -> None:
    daemon = properties(
        DOMAIN_UNITS[domain][1], ("MainPID", "ActiveState"))
    try:
        daemon_pid = int(daemon["MainPID"], 10)
    except ValueError as error:
        raise GateFailure("daemon PID malformed") from error
    if daemon["ActiveState"] != "active" or daemon_pid <= 1:
        fail("inert PAPER daemon not active")
    os.kill(daemon_pid, signal.SIGKILL)
    wait_restarted(DOMAIN_UNITS[domain][1], daemon_pid)
    wait_active(DOMAIN_UNITS[domain])
    require_socket_paths(domain, uid, uid)
    require_owner(domain)
    check_network(
        "--paper-identities", str(NETWORK), "--check-active")


def stop_domain(domain: str) -> None:
    systemctl("stop", DOMAIN_UNITS[domain][1], allowed=(0, 5))
    wait_not_active(DOMAIN_UNITS[domain], phase="explicit domain stop")
    require_paths_absent(domain)
    require_owner(None, "explicit domain stop")


def execute() -> dict[str, object]:
    run_id, versions, boot = validate_platform()
    checks: dict[str, bool] = {}
    inert_credentials = require_inert_credentials()
    image_scan = require_no_ib_api_payload()
    versions.update({
        "immutable_file_count":
            str(image_scan["immutable_file_count"]),
        "immutable_file_inventory_sha256":
            str(image_scan["immutable_file_inventory_sha256"]),
        "package_count": str(image_scan["package_count"]),
        "package_inventory_sha256":
            str(image_scan["package_inventory_sha256"]),
    })
    ib_api_binaries = int(image_scan["ib_api_binaries"])
    protected_connections = protected_tcp_socket_count()

    install_explicit_paper_supervisor_dropin()
    require_loaded_unit_contracts()
    for domain in DOMAIN_UNITS:
        require_paths_absent(domain)
    checks["real_templated_units_loaded"] = True

    manual_preflight_refused("codex-a")
    checks["preflight_manual_start_refused_before_authority"] = True

    manual_socket_refused("codex-a")
    checks["socket_manual_start_refused_before_authority"] = True

    systemctl("start", BROKER)
    wait_active((BROKER,))
    check_network("--check-deny-all")
    checks["broker_guard_started_under_systemd"] = True

    concurrent_cold_start()
    checks["idle_concurrent_cold_start_has_one_authority"] = True

    systemctl("stop", BROKER, allowed=(0, 5))
    wait_not_active((BROKER,), phase="before domain B positive composition")
    replace_paper_manifests(
        Path("/etc/heptatrader/test-openclaw-b-network.json"),
        Path("/etc/heptatrader/test-openclaw-b-authority.json"))
    systemctl("start", BROKER)
    wait_active((BROKER,))
    check_network("--check-deny-all")
    set_stub_mode("openclaw-b", "hold")
    start_domain("openclaw-b")
    require_socket_paths("openclaw-b", 2102, 2102)
    require_owner("openclaw-b")
    check_network(
        "--paper-identities", str(NETWORK), "--check-active")
    max_inert_processes = require_exact_inert_process("openclaw-b", 2122)
    protected_connections += protected_tcp_socket_count()
    checks["domain_b_full_composition_active"] = True
    require_daemon_sigkill_restart("openclaw-b", 2102)
    stop_domain("openclaw-b")
    check_network("--check-deny-all")

    systemctl("stop", BROKER, allowed=(0, 5))
    wait_not_active((BROKER,), phase="before restoring domain A manifests")
    replace_paper_manifests(
        Path("/opt/hepta-paper-gate/provision/network-a.json"),
        Path("/opt/hepta-paper-gate/provision/authority-a.json"))
    systemctl("start", BROKER)
    wait_active((BROKER,))
    check_network("--check-deny-all")

    set_stub_mode("codex-a", "hold")
    start_domain("codex-a")
    require_socket_paths("codex-a", 2101, 2101)
    require_owner("codex-a")
    check_network(
        "--paper-identities", str(NETWORK), "--check-active")
    max_inert_processes = max(
        max_inert_processes,
        require_exact_inert_process("codex-a", 2121))
    protected_connections += protected_tcp_socket_count()
    checks["domain_a_full_composition_active"] = True

    started = time.monotonic()
    systemctl(
        "start", "--no-block", DOMAIN_UNITS["openclaw-b"][1],
        allowed=(0, 1))
    wait_not_active(
        DOMAIN_UNITS["openclaw-b"], phase="second-domain rejection")
    if time.monotonic() - started > 5:
        fail("competing PAPER domain rejection exceeded bound")
    require_paths_absent("openclaw-b")
    require_owner("codex-a")
    wait_active(DOMAIN_UNITS["codex-a"])
    require_socket_paths("codex-a", 2101, 2101)
    checks["second_domain_flock_rejected_without_listener"] = True

    require_daemon_sigkill_restart("codex-a", 2101)
    checks["daemon_sigkill_restarts_under_same_authority"] = True

    stop_domain("codex-a")
    check_network("--check-deny-all")

    systemctl("reset-failed", *DOMAIN_UNITS["codex-a"], allowed=(0, 1))
    set_stub_mode("codex-a", "fail")
    command(["/usr/bin/journalctl", "--sync"])
    cursor_output = command([
        "/usr/bin/journalctl", "--no-pager", "--lines=0",
        "--show-cursor",
    ]).stdout
    cursor_lines = [
        line for line in cursor_output.splitlines()
        if line.startswith("-- cursor: ")]
    other_cursor_lines = [
        line for line in cursor_output.splitlines()
        if line and not line.startswith("-- cursor: ")]
    if (
            len(cursor_lines) != 1 or
            other_cursor_lines not in ([], ["-- No entries --"]) or
            re.fullmatch(
                r"-- cursor: ([^\s\r\n]+)", cursor_lines[0]) is None):
        fail(
            "systemd journal cursor evidence malformed: " +
            cursor_output[-2048:].replace("\n", " | "))
    probe_cursor = cursor_lines[0][len("-- cursor: "):]
    systemctl(
        "start", "--no-block", DOMAIN_UNITS["codex-a"][1],
        allowed=(0, 1, 5))
    wait_not_active(
        DOMAIN_UNITS["codex-a"], timeout=10,
        phase="startup failure")
    require_paths_absent("codex-a")
    require_owner(None, "startup failure")
    marker = Path(
        "/var/lib/hepta-ib-execution-codex-a/inert-stub.started")
    marker_before = os.lstat(marker)
    blocked = systemctl(
        "start", DOMAIN_UNITS["codex-a"][1],
        allowed=(0, 1, 4, 5))
    command(["/usr/bin/journalctl", "--sync"])
    start_limit_journal = command([
        "/usr/bin/journalctl", "--no-pager",
        "--after-cursor=" + probe_cursor,
        "--output=cat",
    ]).stdout
    preflight_prefix = DOMAIN_UNITS["codex-a"][0] + ": "
    if (
            preflight_prefix +
            "Start request repeated too quickly." not in
            start_limit_journal or
            preflight_prefix +
            "Failed with result 'exit-code'." not in
            start_limit_journal or
            "Dependency failed for " + DOMAIN_UNITS["codex-a"][1] not in
            start_limit_journal):
        fail(
            "systemd start-limit journal evidence missing: " +
            start_limit_journal[-4096:].replace("\n", " | "))
    marker_after = os.lstat(marker)
    start_limit = properties(
        DOMAIN_UNITS["codex-a"][1],
        (
            "ActiveState", "SubState", "Result", "NRestarts",
            "StartLimitBurst", "StartLimitIntervalUSec"))
    time.sleep(0.5)
    stable_marker = os.lstat(marker)
    stable_restart_count = properties(
        DOMAIN_UNITS["codex-a"][1], ("NRestarts",))["NRestarts"]
    preflight_limit = properties(
        DOMAIN_UNITS["codex-a"][0],
        (
            "ActiveState", "Result", "StartLimitBurst",
            "StartLimitIntervalUSec"))
    if (
            start_limit["NRestarts"] != "2" or
            start_limit["StartLimitBurst"] != "5" or
            start_limit["StartLimitIntervalUSec"] != "30min" or
            preflight_limit["ActiveState"] not in {"failed", "inactive"} or
            preflight_limit["Result"] != "exit-code" or
            preflight_limit["StartLimitBurst"] != "5" or
            preflight_limit["StartLimitIntervalUSec"] != "30min" or
            stable_restart_count != start_limit["NRestarts"] or
            (marker_before.st_ino, marker_before.st_mtime_ns) !=
            (marker_after.st_ino, marker_after.st_mtime_ns) or
            (marker_after.st_ino, marker_after.st_mtime_ns) !=
            (stable_marker.st_ino, stable_marker.st_mtime_ns)):
        fail("daemon failure did not exercise systemd start-limit: " +
             json.dumps({
                 "properties": start_limit,
                 "preflight_properties": preflight_limit,
                 "start_stdout": blocked.stdout,
                 "start_stderr": blocked.stderr,
                 "journal_tail": start_limit_journal[-4096:],
             }, sort_keys=True))
    check_network("--check-deny-all")
    checks[
        "startup_failure_hits_composition_start_limit_and_reclaims_all"
    ] = True

    wait_not_active(
        (*DOMAIN_UNITS["codex-a"], *DOMAIN_UNITS["openclaw-b"]),
        phase="before isolated broker finalizer")
    require_owner(None, "before isolated broker finalizer")
    for domain in DOMAIN_UNITS:
        require_paths_absent(domain)
    systemctl("reset-failed", BROKER, allowed=(0, 1))
    systemctl("start", BROKER)
    wait_active((BROKER,))
    check_network(
        "--paper-identities", str(NETWORK),
        "--activate-paper-domain", "--domain", "codex-a")
    check_network(
        "--paper-identities", str(NETWORK), "--check-active")
    broker_pid_text = properties(BROKER, ("MainPID", "ActiveState"))
    try:
        broker_pid = int(broker_pid_text["MainPID"], 10)
    except ValueError as error:
        raise GateFailure("broker guard PID malformed") from error
    if broker_pid_text["ActiveState"] != "active" or broker_pid <= 1:
        fail("broker guard not active")
    atomic_write(POLICY, b"{", 0o644)
    atomic_write(IDENTITIES, b"{", 0o644)
    os.kill(broker_pid, signal.SIGKILL)
    wait_not_active((BROKER,), phase="broker guard SIGKILL")
    result = properties(
        BROKER, ("ActiveState", "Result", "ExecStopPost"))
    if (
            result["ActiveState"] != "failed" or
            result["Result"] != "signal"):
        fail(
            "broker ExecStopPost execution result mismatch: " +
            json.dumps(result, sort_keys=True))
    require_successful_exec_stop_post(result["ExecStopPost"])
    for domain in DOMAIN_UNITS:
        wait_not_active(
            DOMAIN_UNITS[domain],
            phase="isolated broker finalizer")
        require_paths_absent(domain)
    require_owner(None, "isolated broker finalizer")
    check_network("--check-deny-all")
    checks["systemd_exec_stop_post_is_input_independent_deny_all"] = True

    manual_socket_refused("codex-a")
    for path in SOCKETS["codex-a"]:
        client = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        try:
            try:
                client.connect(str(path))
            except OSError:
                pass
            else:
                fail("stopped socket path reactivated PAPER")
        finally:
            client.close()
    wait_not_active(
        DOMAIN_UNITS["codex-a"], phase="post-cleanup socket probe")
    checks["stopped_socket_cannot_reactivate_daemon"] = True
    protected_connections += protected_tcp_socket_count()
    paper_orders = require_zero_order_state()

    return {
        "schema": SCHEMA,
        "passed": True,
        "run_id": run_id,
        "checks": checks,
        "versions": versions,
        "boot": boot,
        "boundary": {
            "paper_unit_instances_observed":
                sum(len(units) for units in DOMAIN_UNITS.values()),
            "broker_policy_unit_observed": 1,
            "domain_compositions_observed": 2,
            "max_concurrent_inert_execution_stub_processes":
                max_inert_processes,
            "ib_api_binaries": ib_api_binaries,
            "real_broker_connections": protected_connections,
            "broker_protocol_messages": 0,
            "real_credentials": 0,
            "inert_credential_fixtures": inert_credentials,
            "paper_orders": paper_orders,
            "live_authorized": False,
            "host_systemd_units_touched": 0,
            "host_nft_tables_touched": 0,
        },
    }


def main() -> int:
    try:
        result = execute()
    except (
            GateFailure, OSError, ValueError, subprocess.SubprocessError
            ) as error:
        message = str(error) or type(error).__name__
        print(
            "hepta_paper_domain_rootful_inner_gate: FAIL: " +
            message[:2048],
            file=sys.stderr,
        )
        return 1
    print(MARKER + json.dumps(
        result, sort_keys=True, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
