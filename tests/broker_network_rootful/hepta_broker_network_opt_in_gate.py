#!/usr/bin/env python3

"""Explicit rootful network-only policy and authority lifecycle gate.

The gate uses inert loopback sentinels.  It installs no IB binary, PAPER unit,
credential, or account configuration and sends no broker protocol message.
Two root-owned, default-engaged kill-switch fixtures exist only so the
independent authority guard can prove its lifetime host lease.
"""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import time
from typing import Optional


SCHEMA = "hepta.broker-network-opt-in-rootful.v3"
MARKER = "HEPTA_BROKER_NETWORK_OPT_IN_ROOTFUL_RESULT="
HELPER = "/usr/libexec/hepta-broker-egress-policy"
AUTHORITY_HELPER = "/usr/libexec/hepta-ib-paper-domain-authority"
POLICY = Path("/usr/share/heptatrader/hepta-broker-network-policy-v1.json")
IDENTITIES = Path("/usr/share/heptatrader/hepta-service-identities-v1.json")
OPT_IN = Path("/run/hepta-broker-network-opt-in-v1.json")
SECOND_OPT_IN = Path("/run/hepta-broker-network-second-opt-in-v1.json")
OVERFULL_OPT_IN = Path(
    "/run/hepta-broker-network-overfull-opt-in-v1.json")
ABSENT = Path("/run/hepta-broker-network-no-paper-authority.json")
CORRUPT_POLICY = Path("/run/hepta-broker-network-corrupt-policy.json")
CORRUPT_IDENTITIES = Path(
    "/run/hepta-broker-network-corrupt-identities.json")
AUTHORITY = Path("/run/hepta-ib-paper-authority-codex-a-v1.json")
SECOND_AUTHORITY = Path(
    "/run/hepta-ib-paper-authority-openclaw-b-v1.json")
HOST_LOCK_DIRECTORY = Path("/run/hepta/ib-paper-host-authority")
PORTS = (4001, 4002, 7496, 7497)
MODEL_PORT = 38443
FIXED_IB = (2003, 2003)
DOMAIN_IB = ((2121, 2121),)
SECOND_DOMAIN_IB = ((2122, 2122),)
AGENTS = ((2004, 2004), (2104, 2104), (2105, 2105))
DENIED = (
    *AGENTS,
    (2001, 2001),
    (2002, 2002),
    (2101, 2101),
    (2102, 2102),
    (2111, 2111),
    (2112, 2112),
)
SAFE_ENV = {
    "PATH": "/usr/sbin:/usr/bin:/sbin:/bin",
    "LANG": "C",
    "LC_ALL": "C",
}
MAX_PROCESS_OUTPUT = 1024 * 1024
ACTIVE_NOTIFY_PROCESSES: list["NotifyProcess"] = []


class GateFailure(RuntimeError):
    pass


class Sentinel:
    def __init__(self, family: int, port: int):
        self.family = family
        self.socket = socket.socket(family, socket.SOCK_STREAM)
        self.socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        if family == socket.AF_INET6:
            self.socket.setsockopt(socket.IPPROTO_IPV6, socket.IPV6_V6ONLY, 1)
        self.socket.bind(
            ("::1", port) if family == socket.AF_INET6
            else ("127.0.0.1", port))
        self.socket.listen(32)
        self.socket.setblocking(False)
        self.accepted = 0

    def drain(self) -> None:
        while True:
            try:
                connection, _address = self.socket.accept()
            except BlockingIOError:
                return
            except OSError:
                return
            with connection:
                self.accepted += 1

    def start(self) -> None:
        pass

    def close(self) -> None:
        self.socket.close()


class NotifyProcess:
    """Small sd_notify harness for a direct, non-systemd guard process."""

    def __init__(self, name: str, arguments: list[str]):
        self.path = Path(f"/run/hepta-{name}.notify")
        if self.path.exists() or self.path.is_symlink():
            raise GateFailure("notify socket path was not clean")
        self.channel = socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM)
        self.channel.bind(str(self.path))
        self.channel.settimeout(0.1)
        environment = dict(SAFE_ENV)
        environment.update({
            "NOTIFY_SOCKET": str(self.path),
            "WATCHDOG_USEC": "1000000",
        })
        self.closed = False
        try:
            self.process = subprocess.Popen(
                arguments,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                env=environment,
                cwd="/",
                close_fds=True,
                start_new_session=True,
            )
        except BaseException:
            self._close_notify()
            raise
        ACTIVE_NOTIFY_PROCESSES.append(self)

    def drain_pending(self) -> None:
        while not self.closed:
            try:
                self.channel.recv(4096, socket.MSG_DONTWAIT)
            except (BlockingIOError, socket.timeout):
                return
            except OSError:
                return

    def _close_notify(self) -> None:
        if self.closed:
            return
        self.closed = True
        try:
            ACTIVE_NOTIFY_PROCESSES.remove(self)
        except ValueError:
            pass
        self.channel.close()
        try:
            self.path.unlink()
        except FileNotFoundError:
            pass

    def _bounded_communicate(
            self, timeout: float) -> tuple[bytes, bytes]:
        try:
            output, error = self.process.communicate(timeout=timeout)
        except subprocess.TimeoutExpired as exception:
            raise GateFailure("guard did not exit within the bound") from exception
        if len(output) > MAX_PROCESS_OUTPUT or len(error) > MAX_PROCESS_OUTPUT:
            raise GateFailure("guard output exceeded bound")
        return output, error

    def wait_ready(self) -> None:
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            if self.process.poll() is not None:
                self._bounded_communicate(1)
                self._close_notify()
                raise GateFailure("guard exited before READY")
            try:
                message = self.channel.recv(4096)
            except socket.timeout:
                continue
            if b"READY=1" in message.splitlines():
                return
        raise GateFailure("guard did not notify READY within the bound")

    def wait_exit(
            self, expected_returncode: int,
            *, stderr_contains: Optional[bytes] = None) -> None:
        output, error = self._bounded_communicate(10)
        self._close_notify()
        if self.process.returncode != expected_returncode:
            raise GateFailure("guard returned an unexpected status")
        if stderr_contains is not None and stderr_contains not in error:
            raise GateFailure("guard did not report the expected rejection")
        if expected_returncode == 0 and error:
            raise GateFailure("clean guard stop emitted stderr")
        if expected_returncode != 0 and output:
            raise GateFailure("failed guard emitted stdout")

    def stop(self) -> None:
        if self.process.poll() is None:
            self.process.terminate()
        self.wait_exit(0)

    def crash(self) -> None:
        if self.process.poll() is None:
            self.process.kill()
        self.wait_exit(-9)

    def abort(self) -> None:
        if self.process.poll() is None:
            self.process.kill()
            try:
                self.process.wait(timeout=3)
            except subprocess.TimeoutExpired:
                pass
        self._close_notify()


def drain_notifications() -> None:
    for process in tuple(ACTIVE_NOTIFY_PROCESSES):
        process.drain_pending()


def command(
        arguments: list[str], *,
        environment: Optional[dict[str, str]] = None,
) -> subprocess.CompletedProcess[str]:
    drain_notifications()
    selected_environment = dict(SAFE_ENV)
    if environment:
        selected_environment.update(environment)
    completed = subprocess.run(
        arguments,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="strict",
        env=selected_environment,
        cwd="/",
        close_fds=True,
        timeout=30,
        check=False,
    )
    if (
            len(completed.stdout.encode("utf-8")) > 1024 * 1024 or
            len(completed.stderr.encode("utf-8")) > 1024 * 1024):
        raise GateFailure("command output exceeded bound")
    return completed


def apply(manifest: Path, expected_uids: str) -> None:
    completed = command(
        [HELPER, "--paper-identities", str(manifest)])
    if (
            completed.returncode != 0 or completed.stderr or
            f"authorized_uids={expected_uids}" not in completed.stdout):
        raise GateFailure("broker policy application failed")


def check_active(manifest: Path, expected_uids: str) -> None:
    completed = command([
        HELPER, "--paper-identities", str(manifest), "--check-active"])
    if (
            completed.returncode != 0 or completed.stderr or
            f"authorized_uids={expected_uids}" not in completed.stdout):
        raise GateFailure("active broker policy is not exact")


def tighten_deny_all(
        policy: Path = POLICY,
        identities: Path = IDENTITIES) -> None:
    completed = command([
        HELPER, "--policy", str(policy),
        "--identity-manifest", str(identities),
        "--tighten-deny-all"])
    if (
            completed.returncode != 0 or completed.stderr or
            "authorized_connectors=0" not in completed.stdout or
            "authorized_uids= " not in completed.stdout):
        raise GateFailure(
            "independent deny-all lifecycle hook failed "
            f"rc={completed.returncode} "
            f"stdout={completed.stdout[-512:]!r} "
            f"stderr={completed.stderr[-512:]!r}")


def check_deny_all() -> None:
    completed = command([HELPER, "--check-deny-all"])
    if (
            completed.returncode != 0 or completed.stderr or
            "authorized_connectors=0" not in completed.stdout):
        raise GateFailure("deny-all broker policy is not exact")


def finalize_authority(domain: str, result: str) -> None:
    completed = command(
        [AUTHORITY_HELPER, "--finalize-stop", "--domain", domain],
        environment={"SERVICE_RESULT": result})
    if (
            completed.returncode != 0 or completed.stderr or
            "domain_authority=revoked" not in completed.stdout):
        raise GateFailure("authority lifecycle finalizer failed")


def start_broker_guard(name: str, manifest: Path) -> NotifyProcess:
    process = NotifyProcess(name, [
        HELPER, "--paper-identities", str(manifest), "--supervise"])
    try:
        process.wait_ready()
    except BaseException:
        process.abort()
        raise
    return process


def start_authority_guard(
        name: str, network: Path, authority: Path,
        domain: str) -> NotifyProcess:
    process = NotifyProcess(name, [
        AUTHORITY_HELPER,
        "--network-identities", str(network),
        "--authorizations", str(authority),
        "--guard", "--domain", domain,
    ])
    try:
        process.wait_ready()
    except BaseException:
        process.abort()
        raise
    return process


def reject_overfull(manifest: Path) -> None:
    completed = command(
        [HELPER, "--paper-identities", str(manifest)])
    if (
            completed.returncode == 0 or completed.stdout or
            "authorization/list mismatch" not in completed.stderr):
        raise GateFailure("second PAPER domain did not fail closed")


def opt_in_bytes(records: list[tuple[str, int]]) -> bytes:
    policy_raw = POLICY.read_bytes()
    identities = [
        {
            "domain_id": domain,
            "identity": f"hepta-ib-exec-{domain}",
            "uid": uid,
            "gid": uid,
            "role": "ib-paper-execution-authority",
        }
        for domain, uid in records
    ]
    return (
        json.dumps({
            "schema": "hepta.agent-trust-domain-paper-identities.v1",
            "version": 1,
            "source_policy_sha256":
                "sha256:" + hashlib.sha256(policy_raw).hexdigest(),
            "paper_authorized": bool(records),
            "live_authorized": False,
            "identities": identities,
        }, sort_keys=True, separators=(",", ":")) + "\n").encode("utf-8")


def write_exact(path: Path, raw: bytes, mode: int = 0o600) -> None:
    descriptor = os.open(
        path,
        os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_CLOEXEC |
        getattr(os, "O_NOFOLLOW", 0),
        mode,
    )
    try:
        offset = 0
        while offset < len(raw):
            written = os.write(descriptor, raw[offset:])
            if written <= 0:
                raise GateFailure("opt-in manifest write failed")
            offset += written
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def write_opt_in(path: Path, records: list[tuple[str, int]]) -> None:
    write_exact(path, opt_in_bytes(records))


def replace_opt_in(path: Path, records: list[tuple[str, int]]) -> None:
    replacement = path.with_name(path.name + ".replacement")
    write_exact(replacement, opt_in_bytes(records))
    os.replace(replacement, path)
    directory = os.open(
        path.parent, os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC)
    try:
        os.fsync(directory)
    finally:
        os.close(directory)


def authority_bytes(
        network_raw: bytes, domain: str, uid: int) -> bytes:
    control = f"/run/hepta/ib-paper-control-{domain}"
    document = {
        "schema": "hepta.ib-paper-domain-authorizations.v1",
        "version": 1,
        "network_identity_manifest_sha256":
            "sha256:" + hashlib.sha256(network_raw).hexdigest(),
        "paper_authorized": True,
        "live_authorized": False,
        "authorizations": [{
            "domain_id": domain,
            "identity": f"hepta-ib-exec-{domain}",
            "uid": uid,
            "gid": uid,
            "control_directory": control,
            "kill_switch_marker": control + "/kill-switch",
            "control_directory_mode": "0750",
            "kill_switch_mode": "0440",
            "kill_switch_initial_state": "engaged",
        }],
    }
    return (
        json.dumps(document, sort_keys=True, separators=(",", ":")) +
        "\n").encode("utf-8")


def provision_runtime_fixture(domain: str, gid: int) -> None:
    root = Path("/run/hepta")
    root.mkdir(mode=0o755, exist_ok=True)
    os.chown(root, 0, 0)
    os.chmod(root, 0o755)
    control = root / f"ib-paper-control-{domain}"
    control.mkdir(mode=0o750)
    os.chown(control, 0, gid)
    os.chmod(control, 0o750)
    marker = control / "kill-switch"
    descriptor = os.open(
        marker,
        os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_CLOEXEC |
        getattr(os, "O_NOFOLLOW", 0),
        0o440,
    )
    try:
        os.fchown(descriptor, 0, gid)
        os.fchmod(descriptor, 0o440)
        if os.write(descriptor, b"engaged") != len(b"engaged"):
            raise GateFailure("kill-switch fixture write failed")
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def provision_host_lock_directory() -> None:
    HOST_LOCK_DIRECTORY.mkdir(mode=0o700)
    os.chown(HOST_LOCK_DIRECTORY, 0, 0)
    os.chmod(HOST_LOCK_DIRECTORY, 0o700)


def connect_as(uid: int, gid: int, family: int, port: int) -> bool:
    drain_notifications()
    child = os.fork()
    if child == 0:
        try:
            os.setgroups([])
            os.setgid(gid)
            os.setuid(uid)
            client = socket.socket(family, socket.SOCK_STREAM)
            try:
                client.settimeout(1.5)
                client.connect(
                    ("::1", port) if family == socket.AF_INET6
                    else ("127.0.0.1", port))
            finally:
                client.close()
        except (OSError, ValueError):
            os._exit(1)
        os._exit(0)
    completed, status = os.waitpid(child, 0)
    if completed != child or not os.WIFEXITED(status):
        raise GateFailure("identity connection child failed")
    return os.WEXITSTATUS(status) == 0


def wait_accepts(sentinel: Sentinel, expected: int) -> None:
    deadline = time.monotonic() + 3
    while sentinel.accepted != expected:
        drain_notifications()
        sentinel.drain()
        if sentinel.accepted > expected:
            raise GateFailure("sentinel accepted an unauthorized connection")
        if time.monotonic() >= deadline:
            raise GateFailure("sentinel did not accept authorized connection")
        time.sleep(0.02)


def deny(
        identities: tuple[tuple[int, int], ...],
        family: int,
        port: int) -> None:
    for uid, gid in identities:
        if connect_as(uid, gid, family, port):
            raise GateFailure("unauthorized UID reached protected port")


def allow(
        identities: tuple[tuple[int, int], ...],
        family: int,
        port: int) -> None:
    for uid, gid in identities:
        if not connect_as(uid, gid, family, port):
            raise GateFailure("authorized UID could not reach protected port")


def assert_boundary(
        sentinels: dict[tuple[int, int], Sentinel],
        denied: tuple[tuple[int, int], ...],
        allowed: tuple[tuple[int, int], ...]) -> None:
    for family in (socket.AF_INET, socket.AF_INET6):
        for port in PORTS:
            sentinel = sentinels[(family, port)]
            before = sentinel.accepted
            deny(denied, family, port)
            allow(allowed, family, port)
            wait_accepts(sentinel, before + len(allowed))


def execute() -> dict[str, object]:
    if os.geteuid() != 0 or os.getegid() != 0:
        raise GateFailure("network-only gate requires container root")
    if (
            Path("/run/docker.sock").exists() or
            Path("/var/run/docker.sock").exists()):
        raise GateFailure("Docker socket leaked into gate")
    if {name for _index, name in socket.if_nameindex()} != {"lo"}:
        raise GateFailure("network-only container is not loopback-only")
    for forbidden in (
            "/usr/libexec/hepta-ib-executiond",
            "/usr/lib/systemd/system/hepta-execution-ib-paper.service",
            "/etc/heptatrader/credentials"):
        path = Path(forbidden)
        if path.exists() or path.is_symlink():
            raise GateFailure("IB/PAPER runtime surface leaked into gate")
    runtime_inputs = (
        OPT_IN, SECOND_OPT_IN, OVERFULL_OPT_IN, ABSENT,
        AUTHORITY, SECOND_AUTHORITY, CORRUPT_POLICY, CORRUPT_IDENTITIES)
    if any(path.exists() or path.is_symlink() for path in runtime_inputs):
        raise GateFailure("network manifest runtime path is not clean")

    sentinels = {
        (family, port): Sentinel(family, port)
        for family in (socket.AF_INET, socket.AF_INET6)
        for port in (*PORTS, MODEL_PORT)
    }
    for sentinel in sentinels.values():
        sentinel.start()
    active_guards: list[NotifyProcess] = []
    try:
        apply(ABSENT, "2003")
        check_active(ABSENT, "2003")
        assert_boundary(
            sentinels, (*DENIED, *DOMAIN_IB, *SECOND_DOMAIN_IB),
            (FIXED_IB,))

        write_opt_in(
            OVERFULL_OPT_IN,
            [("codex-a", 2121), ("openclaw-b", 2122)])
        reject_overfull(OVERFULL_OPT_IN)
        check_active(ABSENT, "2003")
        assert_boundary(
            sentinels, (*DOMAIN_IB, *SECOND_DOMAIN_IB), (FIXED_IB,))

        write_opt_in(OPT_IN, [("codex-a", 2121)])
        apply(OPT_IN, "2121")
        check_active(OPT_IN, "2121")
        assert_boundary(
            sentinels, (*DENIED, FIXED_IB, *SECOND_DOMAIN_IB),
            DOMAIN_IB)

        apply(ABSENT, "2003")
        check_active(ABSENT, "2003")
        assert_boundary(
            sentinels, (*DENIED, *DOMAIN_IB, *SECOND_DOMAIN_IB),
            (FIXED_IB,))
        OPT_IN.unlink()

        # A live table deletion must be observed by the long-lived guard.
        write_opt_in(OPT_IN, [("codex-a", 2121)])
        guard = start_broker_guard("broker-flush", OPT_IN)
        active_guards.append(guard)
        check_deny_all()
        deleted = command([
            "/usr/sbin/nft", "delete", "table", "inet",
            "hepta_broker_egress_v1"])
        if deleted.returncode != 0 or deleted.stdout or deleted.stderr:
            raise GateFailure("inert nft table flush failed")
        guard.wait_exit(1, stderr_contains=b"installed deny-all policy")
        active_guards.remove(guard)
        check_deny_all()
        assert_boundary(
            sentinels,
            (*DENIED, FIXED_IB, *DOMAIN_IB, *SECOND_DOMAIN_IB), ())
        OPT_IN.unlink()

        # Atomic manifest replacement/revocation must also tighten immediately.
        write_opt_in(OPT_IN, [("codex-a", 2121)])
        guard = start_broker_guard("broker-revoke", OPT_IN)
        active_guards.append(guard)
        check_deny_all()
        replace_opt_in(OPT_IN, [])
        guard.wait_exit(1, stderr_contains=b"installed deny-all policy")
        active_guards.remove(guard)
        check_deny_all()
        assert_boundary(
            sentinels,
            (*DENIED, FIXED_IB, *DOMAIN_IB, *SECOND_DOMAIN_IB), ())
        OPT_IN.unlink()

        # A clean broker stop revokes every broker authority before returning.
        write_opt_in(OPT_IN, [("codex-a", 2121)])
        guard = start_broker_guard("broker-clean-stop", OPT_IN)
        active_guards.append(guard)
        guard.stop()
        active_guards.remove(guard)
        check_deny_all()
        OPT_IN.unlink()

        # SIGKILL bypasses in-process cleanup.  Even if both required
        # manifests are simultaneously unreadable/corrupt, the exact
        # ExecStopPost action must remain an input-independent deny-all
        # boundary.
        write_opt_in(OPT_IN, [("codex-a", 2121)])
        guard = start_broker_guard("broker-sigkill", OPT_IN)
        active_guards.append(guard)
        guard.crash()
        active_guards.remove(guard)
        write_exact(CORRUPT_POLICY, b"{")
        write_exact(CORRUPT_IDENTITIES, b"{")
        tighten_deny_all(CORRUPT_POLICY, CORRUPT_IDENTITIES)
        check_deny_all()
        CORRUPT_POLICY.unlink()
        CORRUPT_IDENTITIES.unlink()
        OPT_IN.unlink()

        # Separate valid A/B configurations still serialize on one host lease.
        network_a = opt_in_bytes([("codex-a", 2121)])
        network_b = opt_in_bytes([("openclaw-b", 2122)])
        write_exact(OPT_IN, network_a)
        write_exact(SECOND_OPT_IN, network_b)
        write_exact(AUTHORITY, authority_bytes(network_a, "codex-a", 2121))
        write_exact(
            SECOND_AUTHORITY,
            authority_bytes(network_b, "openclaw-b", 2122))
        provision_runtime_fixture("codex-a", 2121)
        provision_runtime_fixture("openclaw-b", 2122)
        provision_host_lock_directory()

        broker_a = start_broker_guard("broker-a", OPT_IN)
        active_guards.append(broker_a)
        authority_a = start_authority_guard(
            "authority-a", OPT_IN, AUTHORITY, "codex-a")
        active_guards.append(authority_a)
        check_active(OPT_IN, "2121")
        assert_boundary(
            sentinels, (*DENIED, FIXED_IB, *SECOND_DOMAIN_IB), DOMAIN_IB)
        authority_b_rejected = NotifyProcess("authority-b-rejected", [
            AUTHORITY_HELPER,
            "--network-identities", str(SECOND_OPT_IN),
            "--authorizations", str(SECOND_AUTHORITY),
            "--guard", "--domain", "openclaw-b",
        ])
        try:
            authority_b_rejected.wait_exit(
                1, stderr_contains=b"another host PAPER authority")
        finally:
            authority_b_rejected.abort()
        foreign_finalize_started = time.monotonic()
        finalize_authority("openclaw-b", "exit-code")
        if time.monotonic() - foreign_finalize_started > 1:
            raise GateFailure(
                "foreign-domain ExecStopPost did not return immediately")
        check_active(OPT_IN, "2121")
        authority_a.crash()
        active_guards.remove(authority_a)
        # SIGKILL releases flock, but the persistent owner tombstone must
        # reject B until A's exact ExecStopPost finalizer obtains the same
        # lease, installs deny-all and clears A's owner.
        authority_b_race = NotifyProcess("authority-b-race", [
            AUTHORITY_HELPER,
            "--network-identities", str(SECOND_OPT_IN),
            "--authorizations", str(SECOND_AUTHORITY),
            "--guard", "--domain", "openclaw-b",
        ])
        try:
            authority_b_race.wait_exit(
                1, stderr_contains=b"finalization is incomplete")
        finally:
            authority_b_race.abort()
        finalize_authority("codex-a", "signal")
        check_deny_all()
        if broker_a.process.poll() is not None:
            output, error = broker_a._bounded_communicate(1)
            raise GateFailure(
                "broker guard stopped after domain revocation "
                f"rc={broker_a.process.returncode} "
                f"stdout={output[-512:]!r} stderr={error[-512:]!r}")
        broker_a.stop()
        active_guards.remove(broker_a)

        broker_b = start_broker_guard("broker-b", SECOND_OPT_IN)
        active_guards.append(broker_b)
        authority_b = start_authority_guard(
            "authority-b", SECOND_OPT_IN, SECOND_AUTHORITY, "openclaw-b")
        active_guards.append(authority_b)
        check_active(SECOND_OPT_IN, "2122")
        assert_boundary(
            sentinels, (*DENIED, FIXED_IB, *DOMAIN_IB),
            SECOND_DOMAIN_IB)
        authority_b.stop()
        active_guards.remove(authority_b)
        check_deny_all()
        if broker_b.process.poll() is not None:
            output, error = broker_b._bounded_communicate(1)
            raise GateFailure(
                "clean domain stop terminated the WATCH broker guard "
                f"rc={broker_b.process.returncode} "
                f"stdout={output[-512:]!r} stderr={error[-512:]!r}")
        broker_b.stop()
        active_guards.remove(broker_b)
        assert_boundary(
            sentinels,
            (*DENIED, FIXED_IB, *DOMAIN_IB, *SECOND_DOMAIN_IB), ())

        for family in (socket.AF_INET, socket.AF_INET6):
            model_sentinel = sentinels[(family, MODEL_PORT)]
            for identity in AGENTS:
                allow((identity,), family, MODEL_PORT)
            wait_accepts(model_sentinel, len(AGENTS))
    finally:
        for guard in reversed(active_guards):
            guard.abort()
        tighten_deny_all()
        for path in runtime_inputs:
            try:
                path.unlink()
            except FileNotFoundError:
                pass
        for sentinel in sentinels.values():
            sentinel.close()

    return {
        "schema": SCHEMA,
        "passed": True,
        "checks": {
            "fixed_only_default": True,
            "all_agent_gateway_simulator_uids_denied": True,
            "domain_ib_uids_denied_before_opt_in": True,
            "second_domain_manifest_rejected_without_policy_change": True,
            "one_domain_ib_uid_allowed_after_exact_opt_in": True,
            "second_domain_ib_uid_denied_during_opt_in": True,
            "domain_ib_uids_denied_after_revocation": True,
            "fixed_ib_uid_disabled_in_templated_mode": True,
            "agent_non_broker_egress_preserved": True,
            "nft_syntax_checked_and_applied": True,
            "exact_live_nft_json_verified": True,
            "broker_guard_detects_table_flush_and_tightens": True,
            "broker_guard_detects_manifest_replacement_and_tightens": True,
            "authority_guard_holds_lifetime_host_lease": True,
            "second_domain_rejected_while_first_guard_active": True,
            "foreign_domain_exec_stop_post_is_noop": True,
            "second_domain_guard_allowed_after_first_stops": True,
            "clean_broker_guard_stop_revokes_all": True,
            "broker_exec_stop_post_revokes_all_after_sigkill": True,
            "authority_exec_stop_post_revokes_after_sigkill": True,
            "authority_sigkill_tombstone_blocks_competing_start": True,
            "authority_clean_stop_revokes_domain_preserves_broker_guard": True,
            "ipv4_and_ipv6_loopback_enforced": True,
        },
        "identities": {
            "fixed_ib_uid": 2003,
            "authorized_domain_ib_uid": 2121,
            "rejected_second_domain_ib_uid": 2122,
            "agent_uids": [2004, 2104, 2105],
            "gateway_uids": [2001, 2101, 2102],
            "simulator_uids": [2002, 2111, 2112],
        },
        "boundary": {
            "network_only": True,
            "inert_loopback_sentinels": True,
            "loopback_families": ["ipv4", "ipv6"],
            "real_broker_connections": 0,
            "broker_protocol_messages": 0,
            "ib_binaries": 0,
            "paper_units": 0,
            "credentials": 0,
            "default_engaged_kill_switch_fixtures": 2,
            "paper_orders": 0,
            "live_authorized": False,
        },
    }


def main() -> int:
    try:
        result = execute()
    except (
            GateFailure, OSError, ValueError, subprocess.SubprocessError
            ) as error:
        message = str(error)
        if not message:
            message = type(error).__name__
        elif len(message) > 2048:
            message = message[:2045] + "..."
        print(
            "hepta_broker_network_opt_in_rootful: FAIL: " + message,
            file=sys.stderr)
        return 1
    print(MARKER + json.dumps(
        result, sort_keys=True, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
