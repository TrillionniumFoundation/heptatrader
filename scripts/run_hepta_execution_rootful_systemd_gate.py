#!/usr/bin/env python3

"""Run the Hepta execution effective-systemd rehearsal in disposable containers.

The runner never mounts host paths into a container, never publishes a port,
never uses the host network or a privileged container, and never executes the
real IBAPI daemon.  Reviewed runtime files are descriptor-copied from exact
allowlists; no build-tree install script is executed.  The formal IBAPI ELF is
hashed outside the build context and is never staged into a runnable image.
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass, field
import hashlib
import json
import os
from pathlib import Path
import re
import signal
import stat
import struct
import subprocess
import sys
import tempfile
import time
from typing import Any, Optional
import uuid


SCHEMA = "hepta.execution-rootful-systemd-gate.v1"
INNER_SCHEMA = "hepta.execution-rootful-systemd-inner.v3"
CONTAINER_SCOPE = "containerized_effective_systemd_rehearsal"
PURPOSE_LABEL = "io.hepta.purpose=rootful-systemd-gate"
MAX_COMMAND_OUTPUT = 2 * 1024 * 1024
MAX_REPORT_BYTES = 4 * 1024 * 1024
PINNED_IMAGE = re.compile(
    r"^[A-Za-z0-9][A-Za-z0-9._/:@-]*@sha256:[0-9a-f]{64}$")
VARIANTS = ("real", "sandbox", "stub")
DISPOSABLE_HOST_SENTINEL = Path(
    "/etc/heptatrader/hepta-rootful-systemd-gate.disposable")
DISPOSABLE_HOST_SENTINEL_HEADER = "HEPTA_DISPOSABLE_ROOTFUL_GATE_V1"
APPARMOR_PROFILE = "hepta-systemd-gate"
RUNTIME_CAPABILITIES = frozenset({
    "AUDIT_WRITE", "BPF", "CHOWN", "DAC_OVERRIDE", "FOWNER", "FSETID",
    "KILL", "MKNOD", "NET_ADMIN", "NET_BIND_SERVICE", "PERFMON",
    "SETFCAP", "SETGID", "SETPCAP", "SETUID", "SYS_ADMIN", "SYS_CHROOT",
    "SYS_PTRACE",
})
COMMAND_ENV = {
    "PATH": "/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin",
    "LANG": "C",
    "LC_ALL": "C",
    "TZ": "UTC",
}
DOCKER_SOCKET = Path("/run/docker.sock")
_DOCKER_CONFIG_HOLDER: Optional[tempfile.TemporaryDirectory[str]] = None


class GateError(RuntimeError):
    """A fail-closed rootful systemd gate error."""


@dataclass
class GateProgress:
    """Monotonic public progress used to make failure evidence precise."""

    phase: str = "local_input_validation"
    docker_api_touched: bool = False
    image_build_started: bool = False
    container_start_attempted: bool = False
    completed_variants: list[str] = field(default_factory=list)


def fail(message: str) -> None:
    raise GateError(message)


def failure_report(error: Exception, progress: GateProgress) -> dict[str, Any]:
    no_container_execution = not progress.container_start_attempted
    zero_or_unknown: int | str = 0 if no_container_execution else "unknown"
    false_or_unknown: bool | str = (
        False if no_container_execution else "unknown")
    public_error = str(error).replace(str(repository_root()), ".")[:512]
    return {
        "schema": SCHEMA,
        "passed": False,
        "certification_level": "containerized-effective-systemd-rehearsal",
        "error_type": type(error).__name__,
        "error": public_error,
        "failure_stage": {
            "phase": progress.phase,
            "docker_api_touched": progress.docker_api_touched,
            "image_build_started": progress.image_build_started,
            "container_start_attempted": progress.container_start_attempted,
            "completed_variants": list(progress.completed_variants),
        },
        "boundary": {
            "real_ibapi_elf_executed": false_or_unknown,
            "real_broker_connections": zero_or_unknown,
            "paper_orders": zero_or_unknown,
            "live_enabled": false_or_unknown,
            "host_hepta_units_started_by_runner": False,
            "user_configured_host_bind_mounts": 0,
            "host_etc_run_usr_bind_mounts": 0,
            "real_ibapi_broker_unreachable":
                "not_run_requires_separate_authorization",
        },
    }


def initialize_docker_config() -> Path:
    global _DOCKER_CONFIG_HOLDER
    if _DOCKER_CONFIG_HOLDER is not None:
        fail("Docker configuration isolation was initialized twice")
    _DOCKER_CONFIG_HOLDER = tempfile.TemporaryDirectory(
        prefix="hepta-rootful-gate-docker-config-")
    path = Path(_DOCKER_CONFIG_HOLDER.name)
    os.chmod(path, 0o700)
    return path


def cleanup_docker_config() -> None:
    global _DOCKER_CONFIG_HOLDER
    holder = _DOCKER_CONFIG_HOLDER
    _DOCKER_CONFIG_HOLDER = None
    if holder is not None:
        holder.cleanup()


def docker_cli(*arguments: str,
               config: Optional[Path] = None) -> list[str]:
    selected = config
    if selected is None:
        if _DOCKER_CONFIG_HOLDER is None:
            fail("isolated Docker configuration is not initialized")
        selected = Path(_DOCKER_CONFIG_HOLDER.name)
    return [
        "docker", "--config", str(selected),
        "--host=unix:///run/docker.sock", *arguments,
    ]


def repository_root() -> Path:
    return Path(__file__).resolve(strict=True).parents[1]


def read_regular_file(
    path: Path,
    *,
    maximum: int = 512 * 1024 * 1024,
    executable: bool = False,
) -> tuple[os.stat_result, bytes, str]:
    """Read one no-follow, single-link regular file from a stable descriptor."""
    try:
        descriptor = os.open(
            path, os.O_RDONLY | os.O_CLOEXEC | os.O_NOFOLLOW)
    except OSError as error:
        fail(f"cannot securely open input {path}: {error.strerror}")
    digest = hashlib.sha256()
    chunks: list[bytes] = []
    total = 0
    try:
        before = os.fstat(descriptor)
        if not stat.S_ISREG(before.st_mode) or before.st_nlink != 1:
            fail(f"input must be a single-link regular file: {path}")
        if before.st_size <= 0 or before.st_size > maximum:
            fail(f"input size outside reviewed range: {path}")
        if executable and not before.st_mode & stat.S_IXUSR:
            fail(f"input is not executable: {path}")
        while True:
            chunk = os.read(descriptor, min(1024 * 1024, maximum + 1 - total))
            if not chunk:
                break
            total += len(chunk)
            if total > maximum:
                fail(f"input size outside reviewed range: {path}")
            digest.update(chunk)
            chunks.append(chunk)
        after = os.fstat(descriptor)
        fields = (
            "st_dev", "st_ino", "st_mode", "st_nlink", "st_uid", "st_gid",
            "st_size", "st_mtime_ns", "st_ctime_ns")
        if any(getattr(before, field) != getattr(after, field)
               for field in fields) or total != before.st_size:
            fail(f"input changed while reading: {path}")
    finally:
        os.close(descriptor)
    return before, b"".join(chunks), digest.hexdigest()


def sha256_file(path: Path) -> str:
    return read_regular_file(path)[2]


def read_virtual_text(path: Path, maximum: int) -> str:
    try:
        descriptor = os.open(path, os.O_RDONLY | os.O_CLOEXEC | os.O_NOFOLLOW)
    except OSError as error:
        fail(f"cannot securely open host evidence {path}: {error.strerror}")
    try:
        before = os.fstat(descriptor)
        if not stat.S_ISREG(before.st_mode):
            fail(f"host evidence is not regular: {path}")
        chunks: list[bytes] = []
        total = 0
        while True:
            chunk = os.read(descriptor, min(65536, maximum + 1 - total))
            if not chunk:
                break
            chunks.append(chunk)
            total += len(chunk)
            if total > maximum:
                fail(f"host evidence changed or exceeded its bound: {path}")
        content = b"".join(chunks)
        after = os.fstat(descriptor)
        if (before.st_dev, before.st_ino,
                before.st_mode, before.st_uid, before.st_gid) != (
                after.st_dev, after.st_ino, after.st_mode, after.st_uid,
                after.st_gid):
            fail(f"host evidence changed or exceeded its bound: {path}")
    finally:
        os.close(descriptor)
    try:
        text = content.decode("utf-8", errors="strict")
    except UnicodeDecodeError:
        fail(f"host evidence is not UTF-8: {path}")
    if "\x00" in text:
        fail(f"host evidence contains NUL: {path}")
    return text


def report_path(path: Path) -> str:
    try:
        return path.resolve(strict=True).relative_to(repository_root()).as_posix()
    except (OSError, ValueError):
        return path.name


def stable_file(path: Path, *, executable: bool = False) -> dict[str, Any]:
    before, _contents, digest = read_regular_file(
        path, executable=executable)
    return {
        "path": report_path(path),
        "sha256": digest,
        "size": before.st_size,
        "device": before.st_dev,
        "inode": before.st_ino,
        "mode": format(stat.S_IMODE(before.st_mode), "04o"),
    }


def command(
    arguments: list[str],
    *,
    timeout: int = 120,
    check: bool = True,
    environment: Optional[dict[str, str]] = None,
) -> subprocess.CompletedProcess[str]:
    process = subprocess.Popen(
        arguments,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
        errors="replace",
        env=COMMAND_ENV if environment is None else environment,
        start_new_session=True,
    )
    try:
        stdout, _ = process.communicate(timeout=timeout)
    except BaseException:
        if process.poll() is None:
            try:
                os.killpg(process.pid, signal.SIGTERM)
                process.wait(timeout=5)
            except (OSError, subprocess.TimeoutExpired):
                if process.poll() is None:
                    try:
                        os.killpg(process.pid, signal.SIGKILL)
                    except OSError:
                        pass
                    process.wait()
        raise
    completed = subprocess.CompletedProcess(
        arguments, process.returncode, stdout, None)
    if len(completed.stdout.encode("utf-8", errors="replace")) > MAX_COMMAND_OUTPUT:
        fail(f"bounded command output exceeded: {arguments[0]}")
    if check and completed.returncode != 0:
        fail(f"command failed rc={completed.returncode}: {arguments[0]}")
    return completed


def install_signal_handlers() -> None:
    def interrupt(signum: int, _frame: Any) -> None:
        # A second ordinary termination signal must not interrupt ownership-
        # checked cleanup. CTest's hard kill remains the final outer bound.
        signal.signal(signal.SIGTERM, signal.SIG_IGN)
        signal.signal(signal.SIGINT, signal.SIG_IGN)
        name = signal.Signals(signum).name
        fail(f"gate interrupted by {name}")

    signal.signal(signal.SIGTERM, interrupt)
    signal.signal(signal.SIGINT, interrupt)


def parse_cmake_cache(build: Path) -> dict[str, str]:
    cache = build / "CMakeCache.txt"
    try:
        text = read_regular_file(cache, maximum=8 * 1024 * 1024)[1].decode(
            "utf-8", errors="strict")
    except UnicodeDecodeError:
        fail(f"CMake cache is not valid UTF-8: {cache}")
    values: dict[str, str] = {}
    for raw in text.splitlines():
        if not raw or raw.startswith(("#", "//")) or "=" not in raw:
            continue
        left, value = raw.split("=", 1)
        if ":" not in left:
            continue
        key, _kind = left.split(":", 1)
        if key in values:
            fail(f"duplicate CMake cache key: {key}")
        values[key] = value
    return values


def validate_build(build_argument: Path, *, ibapi: bool) -> tuple[Path, dict[str, Any]]:
    root = repository_root()
    build = build_argument.resolve(strict=True)
    if not build.is_dir() or build.is_symlink():
        fail(f"build directory must be a real directory: {build}")
    values = parse_cmake_cache(build)
    try:
        configured_source = Path(
            values.get("CMAKE_HOME_DIRECTORY", "")).resolve(strict=True)
    except OSError:
        fail(f"{build}: CMAKE_HOME_DIRECTORY is not a real source tree")
    if configured_source != root:
        fail(f"{build}: CMAKE_HOME_DIRECTORY must be exactly {root}")
    expected = {
        "CMAKE_BUILD_TYPE": "Release",
        "BUILD_TESTING": "ON",
        "CMAKE_EXPORT_COMPILE_COMMANDS": "ON",
        "HEPTA_ENABLE_LEGACY_0DTE_BRIDGE": "OFF",
        "HEPTA_ENABLE_IBAPI": "ON" if ibapi else "OFF",
    }
    for key, required in expected.items():
        if values.get(key, "").upper() != required.upper():
            fail(f"{build}: {key} must be exactly {required}")
    compile_commands = build / "compile_commands.json"
    cache_record = stable_file(build / "CMakeCache.txt")
    compile_record = stable_file(compile_commands)
    return build, {
        "path": report_path(build),
        "cmake_cache_sha256": cache_record["sha256"],
        "compile_commands_sha256": compile_record["sha256"],
        "build_type": "Release",
        "ibapi_enabled": ibapi,
        "generator": values.get("CMAKE_GENERATOR", ""),
        "compiler": Path(values.get("CMAKE_CXX_COMPILER", "")).name,
    }


def find_binary(build: Path, name: str) -> Path:
    relative_candidates = (
        Path("bin/Release") / name,
        Path("tests") / name,
        Path("HeptaTrade") / name,
        Path(name),
    )
    matches: list[Path] = []
    for relative in relative_candidates:
        candidate = build / relative
        try:
            metadata = os.lstat(candidate)
        except FileNotFoundError:
            continue
        if not stat.S_ISREG(metadata.st_mode) or metadata.st_nlink != 1:
            fail(f"unsafe build artifact candidate: {candidate}")
        resolved = candidate.resolve(strict=True)
        try:
            resolved.relative_to(build)
        except ValueError:
            fail(f"build artifact escaped its build tree: {candidate}")
        matches.append(resolved)
    unique = set(matches)
    if len(unique) != 1:
        fail(f"expected one {name!r} in {build}, found {len(unique)}")
    path = next(iter(unique))
    _metadata, contents, _digest = read_regular_file(path, executable=True)
    if not contents.startswith(b"\x7fELF"):
        fail(f"expected ELF executable: {path}")
    elf_header = command(
        ["readelf", "--file-header", str(path)], timeout=30).stdout
    if ("Class:                             ELF64" not in elf_header or
            "Data:                              2's complement, little endian"
            not in elf_header or
            "Machine:                           Advanced Micro Devices X86-64"
            not in elf_header):
        fail(f"expected native Linux amd64 ELF executable: {path}")
    return path


def validate_broker_free_artifacts(
    client_source: Path,
    sandbox_source: Path,
    sandbox_probe: Path,
    disabled_stub: Path,
) -> None:
    client_text = client_source.read_text(encoding="utf-8", errors="strict")
    for forbidden in (
            "PlaceIbOrder(", "CancelIbOrder(", "FenceSessionOwner(",
            "ReleaseSessionOwnerFence(", "ReconcileAuthoritativeState("):
        if forbidden in client_text:
            fail(f"read-only client probe contains forbidden call: {forbidden}")
    sandbox_text = sandbox_source.read_text(encoding="utf-8", errors="strict")
    for forbidden in (
            "adapter_ib", "EClientSocket", "PlaceIbOrder(", "CancelIbOrder("):
        if forbidden in sandbox_text:
            fail(f"sandbox probe contains broker/mutation surface: {forbidden}")
    for artifact in (sandbox_probe, disabled_stub):
        symbols = command(
            ["nm", "--demangle", str(artifact)],
            timeout=30).stdout
        if "EClientSocket" in symbols or "EClient::" in symbols:
            fail(f"broker-free artifact contains real IBAPI symbols: {artifact.name}")
        needed = command(
            ["readelf", "--dynamic", str(artifact)], timeout=30).stdout
        if any(name in needed for name in ("libtws", "libibapi")):
            fail(f"broker-free artifact has an unexpected dynamic dependency: "
                 f"{artifact.name}")
        if artifact == sandbox_probe and any(
                name in needed for name in ("libcrypto", "libssl")):
            fail("sandbox probe has an unexpected crypto dependency")


def canonical_double(value: float) -> str:
    bits = struct.unpack("=Q", struct.pack("=d", value))[0]
    return format(bits, "016x")


def paper_authorization_credential() -> str:
    fields = (
        ("profile_version", "3"),
        ("account", "DU999999"),
        ("host", "127.0.0.1"),
        ("port", "4002"),
        ("client_id", "701"),
        ("control_directory", "/run/hepta/ib-paper-control"),
        ("allowed_security_types", "CASH,STK"),
        ("allowed_order_types", "MKT"),
        ("max_order_quantity", canonical_double(1000.0)),
        ("max_order_notional", canonical_double(250000.0)),
        ("max_orders_per_minute", "2"),
        ("max_active_orders", "3"),
        ("max_gross_position", canonical_double(5000.0)),
    )
    canonical = "".join(
        f"{name}={len(value)}:{value};" for name, value in fields)
    return "PAPER-V3:sha256:" + hashlib.sha256(
        canonical.encode("ascii")).hexdigest()


def write_private(path: Path, content: bytes, mode: int) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor = os.open(
        path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_CLOEXEC, mode)
    try:
        view = memoryview(content)
        while view:
            written = os.write(descriptor, view)
            if written <= 0:
                fail(f"short write: {path}")
            view = view[written:]
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
    os.chmod(path, mode)


def copy_stable_file(source: Path, destination: Path, mode: int) -> None:
    before, contents, digest = read_regular_file(source)
    write_private(destination, contents, mode)
    after, _after_contents, after_digest = read_regular_file(source)
    fields = (
        "st_dev", "st_ino", "st_mode", "st_nlink", "st_uid", "st_gid",
        "st_size", "st_mtime_ns", "st_ctime_ns")
    if (digest != after_digest or
            any(getattr(before, field) != getattr(after, field)
                for field in fields) or
            sha256_file(destination) != digest):
        fail(f"input changed while staging: {source}")


def provision_context(
    context: Path,
    real_simulator: Path,
    real_ib: Path,
    disabled_stub: Path,
    client_probe: Path,
    sandbox_probe: Path,
) -> None:
    root = repository_root()
    install_root = context / "install-root"
    install_root.mkdir(mode=0o700)

    # Never execute a build tree's cmake_install.cmake as host root.  Stage the
    # reviewed runtime bundle from an exact, descriptor-copied allowlist.
    staged_sources = {
        "usr/share/heptatrader/hepta-service-identities-v1.json":
            (root / "systemd/hepta-service-identities-v1.json", 0o644),
        "usr/libexec/hepta-mcp-server":
            (root / "adapters/mcp/hepta_mcp_server.py", 0o755),
        "usr/libexec/hepta-agent-mcp-launcher":
            (root / "scripts/hepta_agent_mcp_launcher.py", 0o755),
        "usr/libexec/hepta-broker-egress-policy":
            (root / "scripts/hepta_broker_egress_policy.py", 0o755),
        "usr/libexec/hepta-ib-paper-domain-authority":
            (root / "scripts/hepta_ib_paper_domain_authority.py", 0o755),
        "usr/libexec/hepta-executiond": (real_simulator, 0o755),
        # The formal IBAPI ELF is never copied into the Docker build context.
        # Every runnable image starts from the disconnected adapter stub.
        "usr/libexec/hepta-ib-executiond": (disabled_stub, 0o755),
        "usr/lib/systemd/system/hepta-execution-simulator.service":
            (root / "systemd/hepta-execution-simulator.service", 0o644),
        "usr/lib/systemd/system/hepta-execution-simulator.socket":
            (root / "systemd/hepta-execution-simulator.socket", 0o644),
        "usr/lib/systemd/system/hepta-execution-events-simulator.socket":
            (root / "systemd/hepta-execution-events-simulator.socket", 0o644),
        "usr/lib/systemd/system/hepta-execution-simulator@.service":
            (root / "systemd/hepta-execution-simulator@.service", 0o644),
        "usr/lib/systemd/system/hepta-execution-simulator@.socket":
            (root / "systemd/hepta-execution-simulator@.socket", 0o644),
        "usr/lib/systemd/system/hepta-execution-events-simulator@.socket":
            (root / "systemd/hepta-execution-events-simulator@.socket", 0o644),
        "usr/lib/systemd/system/hepta-execution-ib-paper.service":
            (root / "systemd/hepta-execution-ib-paper.service", 0o644),
        "usr/lib/systemd/system/hepta-execution-ib-paper.socket":
            (root / "systemd/hepta-execution-ib-paper.socket", 0o644),
        "usr/lib/systemd/system/hepta-execution-events-ib-paper.socket":
            (root / "systemd/hepta-execution-events-ib-paper.socket", 0o644),
        "usr/lib/systemd/system/hepta-execution-ib-paper@.service":
            (root / "systemd/hepta-execution-ib-paper@.service", 0o644),
        "usr/lib/systemd/system/hepta-execution-ib-paper@.socket":
            (root / "systemd/hepta-execution-ib-paper@.socket", 0o644),
        "usr/lib/systemd/system/hepta-execution-events-ib-paper@.socket":
            (root / "systemd/hepta-execution-events-ib-paper@.socket", 0o644),
        "usr/lib/systemd/system/hepta-ib-paper-domain-preflight@.service":
            (root / "systemd/hepta-ib-paper-domain-preflight@.service", 0o644),
        "usr/lib/systemd/system/hepta-broker-egress-policy.service":
            (root / "systemd/hepta-broker-egress-policy.service", 0o644),
        "usr/lib/systemd/system/hepta-execution-ib-paper.service.d/"
        "10-hepta-broker-egress-policy.conf":
            (root / "systemd/hepta-execution-ib-paper.service.d/"
             "10-hepta-broker-egress-policy.conf", 0o644),
        "usr/lib/systemd/system/hepta-execution-ib-paper@.service.d/"
        "10-hepta-broker-egress-policy.conf":
            (root / "systemd/hepta-execution-ib-paper@.service.d/"
             "10-hepta-broker-egress-policy.conf", 0o644),
        "usr/lib/tmpfiles.d/heptatrader-ib-paper.conf":
            (root / "tmpfiles.d/heptatrader-ib-paper.conf", 0o644),
        "usr/share/doc/heptatrader/AGENT-NATIVE-TRADING-OS-ARCHITECTURE.md":
            (root / "docs/AGENT-NATIVE-TRADING-OS-ARCHITECTURE.md", 0o644),
        "usr/share/doc/heptatrader/BROKER-NETWORK-ISOLATION.md":
            (root / "docs/BROKER-NETWORK-ISOLATION.md", 0o644),
        "usr/share/heptatrader/hepta-broker-network-policy-v1.json":
            (root / "systemd/hepta-broker-network-policy-v1.json", 0o644),
        "usr/share/doc/heptatrader/examples/"
        "hepta-execution-simulator.env.example":
            (root / "systemd/hepta-execution-simulator.env.example", 0o644),
        "usr/share/doc/heptatrader/examples/"
        "hepta-execution-ib-paper.env.example":
            (root / "systemd/hepta-execution-ib-paper.env.example", 0o644),
        "usr/share/doc/heptatrader/examples/"
        "hepta-execution-gateway-paper.env.example":
            (root / "systemd/hepta-execution-gateway-paper.env.example", 0o644),
        "usr/share/doc/heptatrader/examples/"
        "hepta-execution-ib-paper-domain.env.example":
            (root / "systemd/hepta-execution-ib-paper-domain.env.example", 0o644),
        "usr/share/doc/heptatrader/examples/"
        "hepta-execution-gateway-paper-domain.env.example":
            (root / "systemd/hepta-execution-gateway-paper-domain.env.example", 0o644),
    }
    for relative, (source, mode) in staged_sources.items():
        destination = install_root / relative
        copy_stable_file(source, destination, mode)
        parent = destination.parent
        while parent != install_root:
            os.chmod(parent, 0o755)
            parent = parent.parent

    observed_files: set[str] = set()
    for directory, child_directories, files in os.walk(
            install_root, followlinks=False):
        for name in child_directories:
            metadata = os.lstat(Path(directory) / name)
            if not stat.S_ISDIR(metadata.st_mode):
                fail("staged install tree contains a non-directory ancestor")
        for name in files:
            path = Path(directory) / name
            metadata = os.lstat(path)
            if not stat.S_ISREG(metadata.st_mode) or metadata.st_nlink != 1:
                fail("staged install tree contains an unsafe file")
            observed_files.add(path.relative_to(install_root).as_posix())
    if observed_files != set(staged_sources):
        fail("staged install tree exact file allowlist mismatch")

    heptatrader = install_root / "etc/heptatrader"
    credentials = heptatrader / "credentials"
    credentials.mkdir(parents=True, mode=0o700)
    os.chmod(heptatrader, 0o755)
    os.chmod(credentials, 0o700)
    write_private(
        heptatrader / "hepta-execution-simulator.env",
        ("HEPTA_EXECUTION_GATEWAY_UID=2001\n"
         "HEPTA_EXECUTION_GATEWAY_AGENT_ID=codex-agent-os-e2e\n"
         "HEPTA_EXECUTION_MAX_REQUEST_BYTES=16384\n"
         "HEPTA_EXECUTION_IO_TIMEOUT_MS=2500\n").encode("ascii"), 0o644)
    write_private(
        heptatrader / "hepta-execution-ib-paper.env",
        ("HEPTA_IB_EXECUTION_MODE=PAPER\n"
         "HEPTA_IB_PAPER_ACCOUNT=DU999999\n"
         "HEPTA_IB_PAPER_HOST=127.0.0.1\n"
         "HEPTA_IB_PAPER_PORT=4002\n"
         "HEPTA_IB_PAPER_CLIENT_ID=701\n"
         "HEPTA_IB_PAPER_MAX_ORDER_QTY=1000\n"
         "HEPTA_IB_PAPER_MAX_ORDER_NOTIONAL=250000\n"
         "HEPTA_IB_PAPER_MAX_ORDERS_PER_MINUTE=2\n"
         "HEPTA_IB_PAPER_MAX_ACTIVE_ORDERS=3\n"
         "HEPTA_IB_PAPER_MAX_GROSS_POSITION=5000\n"
         "HEPTA_IB_EXECUTION_GATEWAY_UID=2001\n"
         "HEPTA_IB_EXECUTION_GATEWAY_AGENT_ID=codex-agent-os-e2e\n"
         "HEPTA_IB_EXECUTION_MAX_REQUEST_BYTES=16384\n"
         "HEPTA_IB_EXECUTION_IO_TIMEOUT_MS=2500\n"
         "HEPTA_IB_EXECUTION_READINESS_TIMEOUT_MS=1000\n").encode("ascii"),
        0o644)
    write_private(
        heptatrader / "hepta-execution-gateway-paper.env",
        ("HEPTA_EXECUTION_REMOTE_MODE=PAPER\n"
         "HEPTA_EXECUTION_SOCKET=/run/hepta-execution/execution.sock\n"
         "HEPTA_EXECUTION_EVENT_SOCKET=/run/hepta-execution/events.sock\n"
         "HEPTA_EXECUTION_SERVICE_UID=2003\n"
         "HEPTA_EXECUTION_IO_TIMEOUT_MS=2500\n"
         "HEPTA_EXECUTION_MAX_RESPONSE_BYTES=32768\n").encode("ascii"),
        0o644)
    hfc = b"HFC1\nfencing_token=7719001\ngeneration=19\n"
    write_private(credentials / "hepta-execution-simulator-fence", hfc, 0o400)
    write_private(credentials / "hepta-execution-ib-paper-fence", hfc, 0o400)
    write_private(
        credentials / "hepta-ib-paper-authorization",
        (paper_authorization_credential() + "\n").encode("ascii"), 0o400)

    artifacts = context / "artifacts"
    artifacts.mkdir(mode=0o700)
    write_private(
        artifacts / "formal-ibapi.sha256",
        (sha256_file(real_ib) + "\n").encode("ascii"), 0o400)
    for source, name in (
        (disabled_stub, "hepta-ib-executiond-disabled"),
        (client_probe, "hepta_execution_systemd_client_probe"),
        (sandbox_probe, "hepta_execution_systemd_sandbox_probe"),
    ):
        copy_stable_file(source, artifacts / name, 0o755)

    (context / "scripts").mkdir(mode=0o700)
    copy_stable_file(
        root / "scripts/check_hepta_execution_provisioned_host.py",
        context / "scripts/check_hepta_execution_provisioned_host.py", 0o755)
    destination = context / "tests/rootful_systemd"
    destination.mkdir(parents=True, mode=0o700)
    for name, mode in (
        ("Dockerfile", 0o644),
        ("hepta-systemd-entrypoint", 0o755),
        ("hepta-rootful-systemd-gate.target", 0o644),
        ("hepta_execution_rootful_inner_gate.py", 0o755),
        ("hepta_agent_os_identity_permissions.py", 0o755),
    ):
        source = (root / "tests/hepta_agent_os_identity_permissions.py"
                  if name == "hepta_agent_os_identity_permissions.py"
                  else root / "tests/rootful_systemd" / name)
        copy_stable_file(source, destination / name, mode)


def require_pinned_image(value: str) -> str:
    if PINNED_IMAGE.fullmatch(value) is None:
        fail("--base-image must include an exact sha256 digest")
    return value


def disposable_host_sentinel_content(machine_id: str, boot_id: str,
                                     docker_daemon_id: str) -> bytes:
    return (
        DISPOSABLE_HOST_SENTINEL_HEADER + "\n" +
        "machine_id=" + machine_id + "\n" +
        "boot_id=" + boot_id + "\n" +
        "docker_daemon_id=" + docker_daemon_id + "\n").encode("ascii")


def disposable_host_sentinel_record(
        metadata: os.stat_result, contents: bytes, path: Path,
        expected_machine_id: str, expected_boot_id: str
        ) -> dict[str, Any]:
    try:
        text = contents.decode("ascii", errors="strict")
    except UnicodeDecodeError:
        fail("root-owned disposable-host sentinel encoding is invalid")
    lines = text.splitlines()
    values: dict[str, str] = {}
    if len(lines) == 4 and text.endswith("\n"):
        for line in lines[1:]:
            if "=" not in line:
                continue
            key, value = line.split("=", 1)
            if key in values:
                fail("root-owned disposable-host sentinel has duplicate fields")
            values[key] = value
    machine_id = values.get("machine_id", "")
    boot_id = values.get("boot_id", "")
    docker_daemon_id = values.get("docker_daemon_id", "")
    if (metadata.st_uid != 0 or metadata.st_gid != 0 or
            stat.S_IMODE(metadata.st_mode) != 0o400 or
            not stat.S_ISREG(metadata.st_mode) or metadata.st_nlink != 1 or
            not lines or lines[0] != DISPOSABLE_HOST_SENTINEL_HEADER or
            set(values) != {"machine_id", "boot_id", "docker_daemon_id"} or
            re.fullmatch(r"[0-9a-f]{32}", machine_id) is None or
            re.fullmatch(r"[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}",
                         boot_id) is None or
            re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9:._-]{15,127}",
                         docker_daemon_id) is None or
            machine_id != expected_machine_id or boot_id != expected_boot_id):
        fail("root-owned disposable-host sentinel contract is not satisfied")
    return {
        "path": str(path),
        "root_owned": True,
        "mode": "0400",
        "single_link": True,
        "contract": DISPOSABLE_HOST_SENTINEL_HEADER,
        "machine_id": machine_id,
        "boot_id": boot_id,
        "docker_daemon_id": docker_daemon_id,
        "device": metadata.st_dev,
        "inode": metadata.st_ino,
    }


def validate_local_host_context() -> dict[str, Any]:
    if os.geteuid() != 0 or os.getegid() != 0:
        fail("root is required on the disposable gate host")
    pid1_executable = os.readlink("/proc/1/exe")
    if (pid1_executable != os.readlink("/proc/1/exe") or
            pid1_executable not in {
                "/usr/lib/systemd/systemd", "/lib/systemd/systemd"} or
            read_virtual_text(Path("/proc/1/comm"), 64).strip() != "systemd"):
        fail("disposable gate must run directly on a systemd host")
    container_check = command(
        ["systemd-detect-virt", "--container", "--quiet"],
        check=False, timeout=10)
    if container_check.returncode != 1:
        fail("disposable gate must not run from inside a container")
    docker_service = command(
        ["systemctl", "is-active", "--quiet", "docker.service"],
        check=False, timeout=10)
    if docker_service.returncode != 0:
        fail("docker.service must already be active before the gate")
    try:
        machine_id = read_regular_file(
            Path("/etc/machine-id"), maximum=128)[1].decode(
                "ascii", errors="strict").strip()
    except UnicodeDecodeError:
        fail("host machine-id encoding is invalid")
    boot_id = read_virtual_text(
        Path("/proc/sys/kernel/random/boot_id"), 128).strip()
    if (re.fullmatch(r"[0-9a-f]{32}", machine_id) is None or
            re.fullmatch(r"[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}",
                         boot_id) is None):
        fail("host machine/boot identity is invalid")
    try:
        run_metadata = os.lstat("/run")
        socket_metadata = os.lstat(DOCKER_SOCKET)
    except OSError as error:
        fail(f"local Docker socket is unavailable: {error.strerror}")
    if (not stat.S_ISDIR(run_metadata.st_mode) or run_metadata.st_uid != 0 or
            stat.S_IMODE(run_metadata.st_mode) & 0o022 or
            not stat.S_ISSOCK(socket_metadata.st_mode) or
            socket_metadata.st_uid != 0 or socket_metadata.st_nlink != 1 or
            stat.S_IMODE(socket_metadata.st_mode) & 0o002):
        fail("local Docker socket or /run ownership contract is unsafe")
    mountinfo = read_virtual_text(Path("/proc/self/mountinfo"), 4 * 1024 * 1024)
    for line in mountinfo.splitlines():
        fields = line.split()
        if len(fields) >= 5 and fields[4] in {
                "/run/docker.sock", "/var/run/docker.sock"}:
            fail("Docker socket must not be a single-file bind mount")
    return {
        "machine_id": machine_id,
        "boot_id": boot_id,
        "kernel_release": os.uname().release,
        "systemd_pid1": True,
        "containerized_runner": False,
        "docker_service_preexisting_active": True,
        "docker_socket_device": socket_metadata.st_dev,
        "docker_socket_inode": socket_metadata.st_ino,
    }


def validate_disposable_host_sentinel(
        expected_machine_id: str, expected_boot_id: str) -> dict[str, Any]:
    descriptors: list[int] = []
    try:
        parent = os.open(
            "/", os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC | os.O_NOFOLLOW)
        descriptors.append(parent)
        for component in ("etc", "heptatrader"):
            parent = os.open(
                component,
                os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC | os.O_NOFOLLOW,
                dir_fd=parent)
            descriptors.append(parent)
            metadata = os.fstat(parent)
            if (not stat.S_ISDIR(metadata.st_mode) or metadata.st_uid != 0 or
                    metadata.st_gid != 0 or
                    stat.S_IMODE(metadata.st_mode) & 0o022):
                fail("disposable-host sentinel ancestor is unsafe")
        descriptor = os.open(
            DISPOSABLE_HOST_SENTINEL.name,
            os.O_RDONLY | os.O_CLOEXEC | os.O_NOFOLLOW, dir_fd=parent)
        descriptors.append(descriptor)
        before = os.fstat(descriptor)
        contents = os.read(descriptor, 513)
        after = os.fstat(descriptor)
        fields = (
            "st_dev", "st_ino", "st_mode", "st_nlink", "st_uid", "st_gid",
            "st_size", "st_mtime_ns", "st_ctime_ns")
        if (any(getattr(before, field) != getattr(after, field)
                for field in fields) or len(contents) != before.st_size):
            fail("disposable-host sentinel changed while reading")
        return disposable_host_sentinel_record(
            before, contents, DISPOSABLE_HOST_SENTINEL,
            expected_machine_id, expected_boot_id)
    except OSError as error:
        fail(f"cannot securely open disposable-host sentinel: {error.strerror}")
    finally:
        for descriptor in reversed(descriptors):
            os.close(descriptor)


def apparmor_enforcing_record(contents: bytes) -> dict[str, Any]:
    try:
        lines = contents.decode("utf-8", errors="strict").splitlines()
    except UnicodeDecodeError:
        fail("AppArmor profile list encoding is invalid")
    expected = APPARMOR_PROFILE + " (enforce)"
    if lines.count(expected) != 1:
        fail("required AppArmor profile is not uniquely enforcing")
    return {"profile": APPARMOR_PROFILE, "enforcing": True}


def validate_apparmor_enforcing() -> dict[str, Any]:
    profile_path = Path("/sys/kernel/security/apparmor/profiles")
    try:
        descriptor = os.open(
            profile_path, os.O_RDONLY | os.O_CLOEXEC | os.O_NOFOLLOW)
    except OSError as error:
        fail(f"cannot prove AppArmor enforcing state: {error.strerror}")
    try:
        chunks: list[bytes] = []
        total = 0
        while True:
            chunk = os.read(descriptor, min(65536, 2 * 1024 * 1024 + 1 - total))
            if not chunk:
                break
            total += len(chunk)
            if total > 2 * 1024 * 1024:
                fail("AppArmor profile list exceeds the evidence bound")
            chunks.append(chunk)
    finally:
        os.close(descriptor)
    return apparmor_enforcing_record(b"".join(chunks))


def current_docker_daemon_id() -> str:
    value = command(docker_cli(
        "info", "--format", "{{.ID}}"), timeout=30).stdout.strip()
    if re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9:._-]{15,127}", value) is None:
        fail("Docker daemon ID is invalid")
    return value


def revalidate_gate_host(expected_host: dict[str, Any],
                         expected_sentinel: dict[str, Any],
                         expected_apparmor: dict[str, Any],
                         expected_docker_daemon_id: str) -> None:
    observed_host = validate_local_host_context()
    if observed_host != expected_host:
        fail("disposable host identity changed during the gate")
    if validate_disposable_host_sentinel(
            observed_host["machine_id"], observed_host["boot_id"]) != \
            expected_sentinel:
        fail("disposable-host sentinel identity changed during the gate")
    if validate_apparmor_enforcing() != expected_apparmor:
        fail("AppArmor enforcing contract changed during the gate")
    if current_docker_daemon_id() != expected_docker_daemon_id:
        fail("Docker daemon identity changed during the gate")


def ensure_base_image(image: str) -> dict[str, Any]:
    inspected = command(
        docker_cli("image", "inspect", image), check=False, timeout=30)
    if inspected.returncode != 0:
        fail("pinned base image is absent; preload it outside the gate")
    record = json.loads(command(
        docker_cli("image", "inspect", image), timeout=30).stdout)[0]
    expected_digest = image.rsplit("@sha256:", 1)[1]
    if (not isinstance(record.get("Id"), str) or
            re.fullmatch(r"sha256:[0-9a-f]{64}", record["Id"]) is None or
            record.get("Os") != "linux" or record.get("Architecture") !=
            "amd64"):
        fail("base image must be a canonical Linux amd64 image")
    repo_digests = sorted(record.get("RepoDigests") or [])
    if not any(value.endswith("@sha256:" + expected_digest)
               for value in repo_digests):
        fail("local base image does not attest the requested registry digest")
    config = record.get("Config") or {}
    if config.get("OnBuild") not in (None, []):
        fail("base image must not contain inherited ONBUILD instructions")
    labels = config.get("Labels") or {}
    if (labels.get("io.hepta.rootful-systemd-base.version") != "1" or
            labels.get("io.hepta.rootful-systemd-base.offline-ready") !=
            "true"):
        fail("base image is not a reviewed offline-ready Hepta gate base")
    return {
        "reference": image,
        "id": record.get("Id", ""),
        "repo_digests": repo_digests,
        "architecture": record.get("Architecture", ""),
        "os": record.get("Os", ""),
        "gate_base_version": "1",
        "offline_ready": True,
    }


def docker_run_arguments(image: str, name: str, mode: str,
                         *, config: Optional[Path] = None) -> list[str]:
    if mode not in VARIANTS:
        fail(f"unknown variant: {mode}")
    return docker_cli(
        "run", "--detach", "--rm", "--pull=never",
        "--name", name,
        "--label", PURPOSE_LABEL,
        "--hostname", "hepta-rootful-gate",
        "--network=none",
        "--cgroupns=private",
        "--ipc=private",
        "--read-only",
        "--tmpfs", "/run:rw,nosuid,nodev,mode=0755,size=64m",
        "--tmpfs", "/run/lock:rw,nosuid,nodev,noexec,mode=0755,size=8m",
        "--tmpfs", "/tmp:rw,nosuid,nodev,noexec,mode=1777,size=128m",
        "--tmpfs", "/var/lib:rw,nosuid,nodev,noexec,mode=0755,size=512m",
        "--tmpfs", "/var/log:rw,nosuid,nodev,noexec,mode=0755,size=64m",
        "--tmpfs", "/var/tmp:rw,nosuid,nodev,noexec,mode=1777,size=64m",
        "--cap-drop=ALL",
        "--cap-add=AUDIT_WRITE",
        "--cap-add=BPF",
        "--cap-add=CHOWN",
        "--cap-add=DAC_OVERRIDE",
        "--cap-add=FOWNER",
        "--cap-add=FSETID",
        "--cap-add=KILL",
        "--cap-add=MKNOD",
        "--cap-add=NET_ADMIN",
        "--cap-add=NET_BIND_SERVICE",
        "--cap-add=PERFMON",
        "--cap-add=SETFCAP",
        "--cap-add=SETGID",
        "--cap-add=SETPCAP",
        "--cap-add=SETUID",
        "--cap-add=SYS_ADMIN",
        "--cap-add=SYS_CHROOT",
        "--cap-add=SYS_PTRACE",
        "--security-opt=no-new-privileges=true",
        f"--security-opt=apparmor={APPARMOR_PROFILE}",
        "--pids-limit=512",
        "--memory=2g",
        "--cpus=2",
        "--stop-signal=SIGRTMIN+3",
        "--stop-timeout=20",
        "--env", "HEPTA_ROOTFUL_GATE_DISPOSABLE=1",
        "--env", f"HEPTA_ROOTFUL_GATE_MODE={mode}",
        image, config=config)


def validate_container_inspect(
        container_id: str, name: str, mode: str,
        expected_image_id: str) -> dict[str, Any]:
    raw = command(docker_cli("inspect", container_id), timeout=30).stdout
    inspected = json.loads(raw)[0]
    host = inspected.get("HostConfig") or {}
    config = inspected.get("Config") or {}
    if inspected.get("Image") != expected_image_id:
        fail("disposable gate container image identity mismatch")
    if (inspected.get("Id") != container_id or
            inspected.get("Name") != "/" + name):
        fail("disposable gate container ID/name contract mismatch")
    if host.get("Privileged") is not False:
        fail("disposable gate container must not be privileged")
    if host.get("ReadonlyRootfs") is not True:
        fail("disposable gate container rootfs must be read-only")
    if host.get("NetworkMode") != "none":
        fail("disposable gate container must use network=none")
    if host.get("Binds"):
        fail("disposable gate container must not have bind mounts")
    mounts = inspected.get("Mounts") or []
    if any(mount.get("Type") != "tmpfs" for mount in mounts):
        fail("disposable gate container must not have host-backed mounts")
    allowed_tmpfs = {
        "/run", "/run/lock", "/tmp", "/var/lib", "/var/log", "/var/tmp"}
    observed_tmpfs = set((host.get("Tmpfs") or {}).keys())
    if observed_tmpfs != allowed_tmpfs:
        fail("disposable gate tmpfs allowlist mismatch")
    if any(mount.get("Destination") not in allowed_tmpfs for mount in mounts):
        fail("disposable gate contains an unexpected tmpfs mount")
    if host.get("PortBindings"):
        fail("disposable gate container must not publish ports")
    if host.get("PublishAllPorts") is not False:
        fail("disposable gate container publish-all must be disabled")
    if host.get("PidMode") != "":
        fail("disposable gate container must use a private PID namespace")
    if host.get("IpcMode") != "private" or host.get("UTSMode") != "":
        fail("disposable gate IPC/UTS namespace contract mismatch")
    if host.get("CgroupnsMode") != "private":
        fail("disposable gate container must use a private cgroup namespace")
    if inspected.get("AppArmorProfile") != APPARMOR_PROFILE:
        fail("disposable gate container AppArmor profile mismatch")
    security_options = set(host.get("SecurityOpt") or [])
    no_new_privileges = security_options & {
        "no-new-privileges", "no-new-privileges=true"}
    if len(no_new_privileges) != 1:
        fail("disposable gate container no-new-privileges is missing")
    if f"apparmor={APPARMOR_PROFILE}" not in security_options:
        fail("disposable gate container AppArmor option is missing")
    if security_options != {
            next(iter(no_new_privileges)),
            f"apparmor={APPARMOR_PROFILE}"}:
        fail("disposable gate container security option allowlist mismatch")
    if set(host.get("CapAdd") or []) != RUNTIME_CAPABILITIES:
        fail("disposable gate capability allowlist mismatch")
    if set(host.get("CapDrop") or []) != {"ALL"}:
        fail("disposable gate capability drop contract mismatch")
    if (host.get("AutoRemove") is not True or host.get("Devices") or
            host.get("DeviceRequests") or host.get("VolumesFrom") or
            host.get("Links") or host.get("CgroupParent") or
            host.get("ExtraHosts") or host.get("Dns") or
            host.get("DnsOptions") or host.get("DnsSearch") or
            (host.get("RestartPolicy") or {}).get("Name") not in {"", "no"}):
        fail("disposable gate host-resource contract mismatch")
    if (host.get("Memory") != 2 * 1024 * 1024 * 1024 or
            host.get("NanoCpus") != 2_000_000_000 or
            host.get("PidsLimit") != 512):
        fail("disposable gate resource limit contract mismatch")
    labels = config.get("Labels") or {}
    if labels.get("io.hepta.purpose") != "rootful-systemd-gate":
        fail("disposable gate purpose label missing")
    expected_env = f"HEPTA_ROOTFUL_GATE_MODE={mode}"
    if expected_env not in (config.get("Env") or []):
        fail("disposable gate mode sentinel missing")
    if "HEPTA_ROOTFUL_GATE_DISPOSABLE=1" not in (config.get("Env") or []):
        fail("disposable gate host sentinel environment is missing")
    if config.get("User") not in {"", "0", "0:0"}:
        fail("disposable gate entrypoint must start as container root")
    if (config.get("WorkingDir") != "/" or
            config.get("Cmd") not in (None, []) or
            config.get("Healthcheck") not in (
                None, {}, {"Test": ["NONE"]}) or
            config.get("StopSignal") != "SIGRTMIN+3"):
        fail("disposable gate inherited process contract is unsafe")
    if config.get("Entrypoint") != [
            "/usr/local/libexec/hepta-systemd-entrypoint"]:
        fail("disposable gate entrypoint contract mismatch")
    parsed_environment: dict[str, str] = {}
    for item in config.get("Env") or []:
        if not isinstance(item, str) or "=" not in item:
            fail("disposable gate environment syntax is invalid")
        key, value = item.split("=", 1)
        if key in parsed_environment:
            fail("disposable gate environment contains duplicate keys")
        parsed_environment[key] = value
    if (parsed_environment.get("HEPTA_ROOTFUL_GATE_DISPOSABLE") != "1" or
            parsed_environment.get("HEPTA_ROOTFUL_GATE_MODE") != mode):
        fail("disposable gate environment sentinel mismatch")
    for key in (
            "LD_PRELOAD", "LD_AUDIT", "LD_LIBRARY_PATH", "PYTHONPATH",
            "PYTHONHOME", "BASH_ENV", "ENV"):
        if parsed_environment.get(key) != "":
            fail("disposable gate inherited loader environment is unsafe")
    for key, value in parsed_environment.items():
        upper = key.upper()
        if (value and any(word in upper for word in (
                "TOKEN", "SECRET", "PASSWORD", "CREDENTIAL"))):
            fail("disposable gate inherited sensitive environment is unsafe")
    return {
        "container_id": inspected.get("Id", ""),
        "image_id": inspected.get("Image", ""),
        "network_mode": "none",
        "read_only_rootfs": True,
        "privileged": False,
        "host_binds": 0,
        "published_ports": 0,
        "cgroup_namespace": "private",
        "apparmor_profile": APPARMOR_PROFILE,
    }


def parse_inner_result(
        output: str, mode: str,
        expected_scope: str = CONTAINER_SCOPE) -> dict[str, Any]:
    prefix = "HEPTA_ROOTFUL_SYSTEMD_GATE_RESULT="
    lines = output.splitlines()
    if len(lines) != 1 or not lines[0].startswith(prefix):
        fail("inner gate must emit exactly one result line")
    matches = [lines[0][len(prefix):]]
    try:
        result = json.loads(matches[0])
    except json.JSONDecodeError as error:
        fail(f"inner gate emitted invalid JSON: {error.msg}")
    expected_top = {
        "schema", "mode", "passed", "checks", "platform", "metrics",
        "boundary"}
    if not isinstance(result, dict) or result.get("schema") != INNER_SCHEMA:
        fail("inner gate schema mismatch")
    if set(result) != expected_top:
        fail("inner gate top-level field set mismatch")
    if result.get("mode") != mode or result.get("passed") is not True:
        fail(f"inner gate failed for {mode}")
    checks = result.get("checks")
    platform = result.get("platform")
    metrics = result.get("metrics")
    if not all(isinstance(value, dict)
               for value in (checks, platform, metrics)):
        fail("inner gate evidence sections are invalid")
    expected_checks = {
        "disposable_sentinel", "provisioned_host_preflight",
        "effective_units_static", "effective_units_no_dropins",
        "effective_units_no_generators", "journal_available",
        "nss_numeric_uid_isolation", "sensitive_host_bind_mounts_absent",
        "tmpfiles_default_engaged",
        "tmpfiles_idempotent", "source_credentials_root_0400",
        "source_credentials_service_unreadable",
        "kill_switch_stable_engaged", "binary_inputs_stable",
        "credential_content_recorded", "credential_hash_recorded",
        "raw_environment_recorded", "raw_journal_recorded",
        "mode_evidence"}
    if set(checks) != expected_checks:
        fail("inner gate check field set mismatch")
    true_checks = expected_checks - {
        "credential_content_recorded", "credential_hash_recorded",
        "raw_environment_recorded", "raw_journal_recorded", "mode_evidence"}
    if (any(checks[key] is not True for key in true_checks) or
            any(checks[key] is not False for key in (
                "credential_content_recorded", "credential_hash_recorded",
                "raw_environment_recorded", "raw_journal_recorded")) or
            not isinstance(checks["mode_evidence"], dict)):
        fail("inner gate check values are invalid")
    expected_platform = {
        "scope", "platform_image_sha256", "systemd_pid1",
        "pid1_cgroup_v2_root"}
    if (set(platform) != expected_platform or
            platform.get("scope") != expected_scope or
            not isinstance(platform.get("platform_image_sha256"), str) or
            re.fullmatch(r"[0-9a-f]{64}",
                         platform["platform_image_sha256"]) is None or
            platform.get("systemd_pid1") is not True or
            platform.get("pid1_cgroup_v2_root") is not True):
        fail("inner gate platform contract mismatch")
    expected_metrics = {
        "simulator_sha256", "client_probe_sha256", "formal_ibapi_sha256",
        "executed_ib_path_sha256", "executed_kind"}
    if set(metrics) != expected_metrics:
        fail("inner gate metrics field set mismatch")
    for key in ("simulator_sha256", "client_probe_sha256",
                "formal_ibapi_sha256",
                "executed_ib_path_sha256"):
        if (not isinstance(metrics.get(key), str) or
                re.fullmatch(r"[0-9a-f]{64}", metrics[key]) is None):
            fail("inner gate binary digest contract mismatch")
    expected_kind = {
        "real": "real_simulator_only_ibapi_not_staged",
        "sandbox": "no_ibapi_sandbox_probe",
        "stub": "ibapi_disabled_stub",
    }[mode]
    if metrics.get("executed_kind") != expected_kind:
        fail("inner gate executed-kind contract mismatch")
    if (metrics["formal_ibapi_sha256"] ==
            metrics["executed_ib_path_sha256"]):
        fail("inner gate formal/executed binary identity mismatch")
    boundary = result.get("boundary")
    expected_boundary = {
        "real_ibapi_elf_executed", "real_broker_connections",
        "paper_orders", "live_enabled", "real_ibapi_broker_unreachable"}
    if not isinstance(boundary, dict) or set(boundary) != expected_boundary:
        fail("inner gate boundary is missing")
    if (type(boundary.get("paper_orders")) is not int or
            boundary.get("paper_orders") != 0 or
            boundary.get("live_enabled") is not False):
        fail("inner gate crossed the no-order/no-LIVE boundary")
    if boundary.get("real_ibapi_elf_executed") is not False:
        fail("inner gate executed the real IBAPI ELF")
    if (type(boundary.get("real_broker_connections")) is not int or
            boundary.get("real_broker_connections") != 0 or
            boundary.get("real_ibapi_broker_unreachable") !=
            "not_run_requires_separate_authorization"):
        fail("inner gate broker boundary drifted")

    expected_mode_fields = {
        "real": {
            "simulator_socket_activation", "dual_socket_shared_identity",
            "service_epoch_changed_on_restart",
            "fencing_generation_stable_on_restart",
            "credential_generation_consumed",
            "manager_socket_inode_stable_until_socket_stop",
            "socket_inode_recreated_after_socket_restart",
            "peer_uid_rejection", "credential_mount_read_only",
            "credential_copy_matches_source",
            "private_network_loopback_only",
            "killmode_control_group_cleanup", "real_ibapi_elf_executed"},
        "sandbox": {
            "canonical_ib_units_with_test_probe",
            "two_credentials_mounted_read_only",
            "credential_copies_match_sources",
            "kill_switch_engaged_and_read_only", "loopback_allowed",
            "nonloopback_control_path_reachable",
            "nonloopback_denied_by_systemd_ip_policy",
            "killmode_control_group_removed_sigterm_ignoring_descendant",
            "paper_stop_closed_command_and_event_sockets",
            "paper_socket_activation_did_not_restart_service",
            "clean_paper_stop_preserved_broker_guard",
            "mutation_requests", "real_ibapi_elf_executed"},
        "stub": {
            "canonical_ib_units_with_ibapi_disabled_stub",
            "adapter_failure_reason", "mutation_plane_never_ready",
            "event_plane_never_ready", "order_journal_bytes",
            "configured_endpoint_connections", "real_broker_connections",
            "paper_stop_closed_command_and_event_sockets",
            "paper_socket_activation_did_not_restart_service",
            "clean_paper_stop_preserved_broker_guard",
            "real_ibapi_elf_executed"},
    }[mode]
    mode_evidence = checks["mode_evidence"]
    if set(mode_evidence) != expected_mode_fields:
        fail("inner gate mode-evidence field set mismatch")
    if mode == "real":
        if (mode_evidence["real_ibapi_elf_executed"] is not False or
                any(mode_evidence[key] is not True
                    for key in expected_mode_fields - {
                        "real_ibapi_elf_executed"})):
            fail("inner real-mode evidence values are invalid")
    elif mode == "sandbox":
        if (mode_evidence["real_ibapi_elf_executed"] is not False or
                type(mode_evidence["mutation_requests"]) is not int or
                mode_evidence["mutation_requests"] != 0 or
                any(mode_evidence[key] is not True
                    for key in expected_mode_fields - {
                        "real_ibapi_elf_executed", "mutation_requests"})):
            fail("inner sandbox-mode evidence values are invalid")
    else:
        expected_stub_evidence = {
                "canonical_ib_units_with_ibapi_disabled_stub": True,
                "adapter_failure_reason": "IB_PAPER_ADAPTER_CONNECT_FAILED",
                "mutation_plane_never_ready": True,
                "event_plane_never_ready": True,
                "order_journal_bytes": 0,
                "configured_endpoint_connections": 0,
                "real_broker_connections": 0,
                "paper_stop_closed_command_and_event_sockets": True,
                "paper_socket_activation_did_not_restart_service": True,
                "clean_paper_stop_preserved_broker_guard": True,
                "real_ibapi_elf_executed": False}
        if any(type(mode_evidence[key]) is not type(value) or
               mode_evidence[key] != value
               for key, value in expected_stub_evidence.items()):
            fail("inner stub-mode evidence values are invalid")
    return result


def validate_report_path(path: Path, ibapi_build_argument: Path) -> Path:
    root = repository_root()
    build_root = ibapi_build_argument.resolve(strict=True)
    build_metadata = os.lstat(build_root)
    if (not stat.S_ISDIR(build_metadata.st_mode) or
            stat.S_IMODE(build_metadata.st_mode) & 0o022):
        fail("report build root is unsafe")
    for forbidden in (Path("/etc"), Path("/usr"), Path("/run")):
        try:
            build_root.relative_to(forbidden)
        except ValueError:
            continue
        fail("report build root overlaps a forbidden host tree")

    absolute = Path(os.path.abspath(path))
    if absolute.parent == root / "runtime-logs":
        parent = absolute.parent.resolve(strict=True)
        allowed = parent == root / "runtime-logs"
    elif (absolute.parent == build_root and absolute.name ==
          "execution-rootful-systemd-gate.json"):
        parent = build_root
        allowed = True
    else:
        parent = absolute.parent
        allowed = False
    if (not allowed or absolute.parent != parent or
            re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}\.json",
                         absolute.name) is None):
        fail("report path is outside the explicit output allowlist")
    parent_metadata = os.lstat(parent)
    if (not stat.S_ISDIR(parent_metadata.st_mode) or
            stat.S_IMODE(parent_metadata.st_mode) & 0o022):
        fail("report directory is unsafe")
    try:
        target = os.lstat(absolute)
    except FileNotFoundError:
        target = None
    if target is not None and (
            not stat.S_ISREG(target.st_mode) or target.st_nlink != 1 or
            stat.S_IMODE(target.st_mode) != 0o600):
        fail("existing report target is unsafe")
    return absolute


def atomic_report(path: Path, report: dict[str, Any]) -> None:
    payload = (json.dumps(
        report, ensure_ascii=True, indent=2, sort_keys=True) + "\n").encode("utf-8")
    if len(payload) > MAX_REPORT_BYTES:
        fail("rootful gate report exceeds the size limit")
    parent = os.open(
        path.parent,
        os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC | os.O_NOFOLLOW)
    temporary = "." + path.name + ".tmp-" + uuid.uuid4().hex
    descriptor: Optional[int] = None
    try:
        try:
            original = os.stat(
                path.name, dir_fd=parent, follow_symlinks=False)
        except FileNotFoundError:
            original = None
        descriptor = os.open(
            temporary,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_CLOEXEC,
            0o600, dir_fd=parent)
        view = memoryview(payload)
        while view:
            count = os.write(descriptor, view)
            if count <= 0:
                fail("short report write")
            view = view[count:]
        os.fsync(descriptor)
        os.close(descriptor)
        descriptor = None
        os.chmod(temporary, 0o600, dir_fd=parent, follow_symlinks=False)
        try:
            current = os.stat(
                path.name, dir_fd=parent, follow_symlinks=False)
        except FileNotFoundError:
            current = None
        identity_fields = ("st_dev", "st_ino", "st_mode", "st_nlink",
                           "st_uid", "st_gid")
        if ((original is None) != (current is None) or
                (original is not None and current is not None and any(
                    getattr(original, field) != getattr(current, field)
                    for field in identity_fields))):
            fail("report target changed before atomic publication")
        os.replace(
            temporary, path.name, src_dir_fd=parent, dst_dir_fd=parent)
        os.fsync(parent)
    finally:
        if descriptor is not None:
            os.close(descriptor)
        try:
            os.unlink(temporary, dir_fd=parent)
        except FileNotFoundError:
            pass
        os.close(parent)


def require_docker_object_absent(kind: str, reference: str) -> None:
    inspected = command(
        docker_cli(kind, "inspect", reference), check=False, timeout=20)
    if inspected.returncode == 0:
        fail(f"refusing to reuse an existing Docker {kind}: {reference}")
    if inspected.returncode != 1:
        fail(f"could not prove Docker {kind} absence: {reference}")


def cleanup_container(
        name: str, expected_id: Optional[str],
        expected_image_id: Optional[str]) -> None:
    if expected_id is None:
        inspected_by_name = command(
            docker_cli("container", "inspect", name),
            check=False, timeout=20)
        if inspected_by_name.returncode == 1:
            return
        if inspected_by_name.returncode != 0 or expected_image_id is None:
            fail(f"could not safely identify disposable container: {name}")
        fallback = json.loads(inspected_by_name.stdout)[0]
        fallback_labels = (fallback.get("Config") or {}).get("Labels") or {}
        fallback_id = fallback.get("Id")
        if (not isinstance(fallback_id, str) or
                fallback.get("Image") != expected_image_id or
                fallback_labels.get("io.hepta.purpose") !=
                "rootful-systemd-gate"):
            fail(f"disposable container fallback ownership mismatch: {name}")
        command(docker_cli("rm", "--force", fallback_id), timeout=30)
        require_docker_object_absent("container", fallback_id)
        require_docker_object_absent("container", name)
        return
    inspected = command(
        docker_cli("container", "inspect", expected_id),
        check=False, timeout=20)
    if inspected.returncode == 1:
        require_docker_object_absent("container", name)
        return
    if inspected.returncode != 0 or expected_image_id is None:
        fail(f"could not safely identify disposable container: {name}")
    record = json.loads(inspected.stdout)[0]
    labels = (record.get("Config") or {}).get("Labels") or {}
    if (record.get("Id") != expected_id or
            record.get("Image") != expected_image_id or
            labels.get("io.hepta.purpose") != "rootful-systemd-gate"):
        fail(f"disposable container ownership mismatch: {name}")
    command(docker_cli("rm", "--force", expected_id), timeout=30)
    require_docker_object_absent("container", expected_id)
    require_docker_object_absent("container", name)


def cleanup_image(tag: str, expected_id: Optional[str]) -> None:
    if expected_id is None:
        inspected_by_tag = command(
            docker_cli("image", "inspect", tag), check=False, timeout=20)
        if inspected_by_tag.returncode == 1:
            return
        if inspected_by_tag.returncode != 0:
            fail(f"could not safely identify disposable image: {tag}")
        fallback = json.loads(inspected_by_tag.stdout)[0]
        fallback_labels = (fallback.get("Config") or {}).get("Labels") or {}
        fallback_id = fallback.get("Id")
        if (not isinstance(fallback_id, str) or
                fallback_labels.get("io.hepta.purpose") !=
                "rootful-systemd-gate"):
            fail(f"disposable image fallback ownership mismatch: {tag}")
        command(docker_cli("image", "rm", fallback_id), timeout=60)
        require_docker_object_absent("image", fallback_id)
        require_docker_object_absent("image", tag)
        return
    inspected = command(
        docker_cli("image", "inspect", expected_id),
        check=False, timeout=20)
    if inspected.returncode == 1:
        require_docker_object_absent("image", tag)
        return
    if inspected.returncode != 0:
        fail(f"could not safely identify disposable image: {tag}")
    record = json.loads(inspected.stdout)[0]
    labels = (record.get("Config") or {}).get("Labels") or {}
    if (record.get("Id") != expected_id or
            labels.get("io.hepta.purpose") != "rootful-systemd-gate"):
        fail(f"disposable image ownership mismatch: {tag}")
    command(docker_cli("image", "rm", expected_id), timeout=60)
    require_docker_object_absent("image", expected_id)
    require_docker_object_absent("image", tag)


def execute(args: argparse.Namespace, progress: GateProgress) -> dict[str, Any]:
    root = repository_root()
    ib_build, ib_record = validate_build(args.ibapi_build_dir, ibapi=True)
    disabled_build, disabled_record = validate_build(
        args.ib_disabled_build_dir, ibapi=False)
    real_simulator = find_binary(ib_build, "hepta-executiond")
    real_ib = find_binary(ib_build, "hepta-ib-executiond")
    disabled_stub = find_binary(
        disabled_build, "hepta-ib-executiond-disabled")
    client_probe = find_binary(
        ib_build, "hepta_execution_systemd_client_probe")
    sandbox_probe = find_binary(
        ib_build, "hepta_execution_systemd_sandbox_probe")
    validate_broker_free_artifacts(
        root / "tests/execution_systemd_client_probe.cpp",
        root / "tests/execution_systemd_sandbox_probe.cpp",
        sandbox_probe, disabled_stub)

    input_paths = [
        Path(__file__).resolve(strict=True),
        root / "CMakeLists.txt",
        root / "HeptaTrade/CMakeLists.txt",
        root / "scripts/check_hepta_execution_provisioned_host.py",
        root / "tests/CMakeLists.txt",
        root / "tests/execution_systemd_client_probe.cpp",
        root / "tests/execution_systemd_sandbox_probe.cpp",
        root / "tests/rootful_systemd/Dockerfile",
        root / "tests/rootful_systemd/hepta-systemd-entrypoint",
        root / "tests/rootful_systemd/hepta-rootful-systemd-gate.target",
        root / "tests/rootful_systemd/hepta_execution_rootful_inner_gate.py",
        root / "tests/hepta_agent_os_identity_permissions.py",
        root / "adapters/mcp/hepta_mcp_server.py",
        root / "scripts/hepta_agent_mcp_launcher.py",
        root / "docs/AGENT-NATIVE-TRADING-OS-ARCHITECTURE.md",
        root / "systemd/hepta-execution-simulator.service",
        root / "systemd/hepta-execution-simulator.socket",
        root / "systemd/hepta-execution-events-simulator.socket",
        root / "systemd/hepta-execution-ib-paper.service",
        root / "systemd/hepta-execution-ib-paper.socket",
        root / "systemd/hepta-execution-events-ib-paper.socket",
        root / "systemd/hepta-execution-ib-paper@.service",
        root / "systemd/hepta-execution-ib-paper@.socket",
        root / "systemd/hepta-execution-events-ib-paper@.socket",
        root / "systemd/hepta-execution-simulator.env.example",
        root / "systemd/hepta-execution-ib-paper.env.example",
        root / "systemd/hepta-execution-gateway-paper.env.example",
        root / "systemd/hepta-execution-ib-paper-domain.env.example",
        root / "systemd/hepta-execution-gateway-paper-domain.env.example",
        root / "tmpfiles.d/heptatrader-ib-paper.conf",
        ib_build / "CMakeCache.txt",
        ib_build / "compile_commands.json",
        disabled_build / "CMakeCache.txt",
        disabled_build / "compile_commands.json",
        real_simulator, real_ib, disabled_stub, client_probe, sandbox_probe,
    ]
    inputs_before = [stable_file(
        path, executable=path in {
            real_simulator, real_ib, disabled_stub, client_probe, sandbox_probe})
        for path in input_paths]

    progress.phase = "local_host_validation"
    local_host = validate_local_host_context()
    progress.phase = "disposable_host_validation"
    disposable_host = validate_disposable_host_sentinel(
        local_host["machine_id"], local_host["boot_id"])
    progress.phase = "host_policy_validation"
    host_apparmor = validate_apparmor_enforcing()
    base = require_pinned_image(args.base_image)
    initialize_docker_config()
    progress.phase = "docker_readonly_preflight"
    progress.docker_api_touched = True
    try:
        docker_server = json.loads(command(docker_cli(
            "version", "--format", "{{json .Server}}"),
            timeout=30).stdout)
    except json.JSONDecodeError:
        fail("Docker server version is not valid JSON")
    if not isinstance(docker_server, dict) or not docker_server.get("Version"):
        fail("Docker server is unavailable")
    docker_daemon_id = current_docker_daemon_id()
    if docker_daemon_id != disposable_host["docker_daemon_id"]:
        fail("disposable-host sentinel is bound to a different Docker daemon")
    docker_platform = command(docker_cli(
        "info", "--format",
        "{{.CgroupDriver}}|{{.CgroupVersion}}|{{.OSType}}|{{.Architecture}}"),
        timeout=30).stdout.strip().split("|")
    if (len(docker_platform) != 4 or docker_platform[0] != "systemd" or
            docker_platform[1] != "2" or docker_platform[2] != "linux" or
            docker_platform[3] not in {"x86_64", "amd64"}):
        fail("Docker must use Linux amd64, cgroup v2 and the systemd driver")
    try:
        docker_security = json.loads(command(docker_cli(
            "info", "--format", "{{json .SecurityOptions}}"),
            timeout=30).stdout)
    except json.JSONDecodeError:
        fail("Docker security options are not valid JSON")
    required_docker_security = {
        "name=apparmor", "name=seccomp,profile=builtin", "name=cgroupns"}
    if (not isinstance(docker_security, list) or
            not required_docker_security.issubset(set(docker_security)) or
            any("rootless" in value or "userns" in value
                for value in docker_security if isinstance(value, str))):
        fail("Docker AppArmor/seccomp/private-cgroup support is required")
    revalidate_gate_host(
        local_host, disposable_host, host_apparmor, docker_daemon_id)
    base_record = ensure_base_image(base)

    run_id = uuid.uuid4().hex
    image_tags = [f"hepta/rootful-systemd-gate:{run_id}-{mode}"
                  for mode in VARIANTS]
    container_names = [f"hepta-rootful-gate-{run_id}-{mode}"
                       for mode in VARIANTS]
    results: dict[str, Any] = {}
    image_records: dict[str, Any] = {}
    container_ids: dict[str, str] = {}
    image_ids: dict[str, str] = {}
    started = time.monotonic()
    for tag in image_tags:
        require_docker_object_absent("image", tag)
    for name in container_names:
        require_docker_object_absent("container", name)
    try:
        with tempfile.TemporaryDirectory(
                prefix="hepta-rootful-systemd-context-") as temporary:
            context = Path(temporary)
            provision_context(
                context, real_simulator, real_ib, disabled_stub, client_probe,
                sandbox_probe)
            dockerfile = context / "tests/rootful_systemd/Dockerfile"
            for mode, tag in zip(VARIANTS, image_tags):
                revalidate_gate_host(
                    local_host, disposable_host, host_apparmor,
                    docker_daemon_id)
                progress.phase = f"image_build_{mode}"
                progress.image_build_started = True
                command(docker_cli(
                    "build", "--pull=false", "--network=none",
                    "--label", PURPOSE_LABEL,
                    "--build-arg", f"BASE_IMAGE={base}",
                    "--build-arg", f"EXECUTION_VARIANT={mode}",
                    "--file", str(dockerfile), "--tag", tag, str(context)),
                    timeout=900)
                inspected = json.loads(command(
                    docker_cli("image", "inspect", tag),
                    timeout=30).stdout)[0]
                image_records[mode] = {
                    "id": inspected.get("Id", ""),
                    "size": inspected.get("Size", 0),
                }
                image_id = image_records[mode]["id"]
                labels = (inspected.get("Config") or {}).get("Labels") or {}
                if (not isinstance(image_id, str) or not image_id.startswith(
                        "sha256:") or
                        labels.get("io.hepta.purpose") !=
                        "rootful-systemd-gate"):
                    fail(f"built image ownership contract failed for {mode}")
                image_ids[mode] = image_id

            for mode, tag, name in zip(VARIANTS, image_tags, container_names):
                revalidate_gate_host(
                    local_host, disposable_host, host_apparmor,
                    docker_daemon_id)
                progress.phase = f"container_start_{mode}"
                progress.container_start_attempted = True
                run_result = command(docker_run_arguments(
                    image_ids[mode], name, mode), timeout=60)
                container_id = run_result.stdout.strip()
                if (len(container_id) != 64 or
                        re.fullmatch(r"[0-9a-f]{64}", container_id) is None):
                    fail(f"docker run did not return a canonical ID for {mode}")
                container_ids[mode] = container_id
                container_record = validate_container_inspect(
                    container_id, name, mode, image_ids[mode])
                container_id = container_record["container_id"]
                if (not isinstance(container_id, str) or
                        len(container_id) != 64 or
                        re.fullmatch(r"[0-9a-f]{64}", container_id) is None):
                    fail(f"container identity contract failed for {mode}")
                deadline = time.monotonic() + 30.0
                while True:
                    ready = command(docker_cli(
                        "exec", container_id, "systemctl", "show",
                        "--property=Version", "--value"),
                        check=False, timeout=10)
                    if ready.returncode == 0 and ready.stdout.strip():
                        break
                    if time.monotonic() >= deadline:
                        fail(f"systemd PID 1 did not become ready in {mode}")
                    time.sleep(0.25)
                inner = command(docker_cli(
                    "exec", container_id,
                    "python3",
                    "/usr/local/libexec/hepta_execution_rootful_inner_gate.py",
                    "--mode", mode), timeout=240, check=False)
                if inner.returncode != 0:
                    fail(f"inner gate exited {inner.returncode} for {mode}")
                parsed_inner = parse_inner_result(inner.stdout, mode)
                expected_base_digest = base.rsplit("@sha256:", 1)[1]
                if (parsed_inner["platform"]["platform_image_sha256"] !=
                        expected_base_digest):
                    fail(f"inner/outer base-image digest mismatch for {mode}")
                expected_metrics = {
                    "simulator_sha256": sha256_file(real_simulator),
                    "client_probe_sha256": sha256_file(client_probe),
                    "formal_ibapi_sha256": sha256_file(real_ib),
                    "executed_ib_path_sha256": {
                        "real": sha256_file(disabled_stub),
                        "sandbox": sha256_file(sandbox_probe),
                        "stub": sha256_file(disabled_stub),
                    }[mode],
                    "executed_kind": {
                        "real": "real_simulator_only_ibapi_not_staged",
                        "sandbox": "no_ibapi_sandbox_probe",
                        "stub": "ibapi_disabled_stub",
                    }[mode],
                }
                if parsed_inner["metrics"] != expected_metrics:
                    fail(f"inner/outer binary digest mismatch for {mode}")
                results[mode] = {
                    "container": container_record,
                    "inner": parsed_inner,
                }
                progress.completed_variants.append(mode)
                progress.phase = f"container_stop_{mode}"
                command(docker_cli(
                    "stop", "--time", "20", container_id),
                        timeout=40)
    finally:
        cleanup_failed = False
        for mode, name in reversed(list(zip(VARIANTS, container_names))):
            try:
                cleanup_container(
                    name, container_ids.get(mode), image_ids.get(mode))
            except Exception:
                cleanup_failed = True
        for mode, tag in reversed(list(zip(VARIANTS, image_tags))):
            try:
                cleanup_image(tag, image_ids.get(mode))
            except Exception:
                cleanup_failed = True
        if cleanup_failed:
            fail("disposable Docker cleanup failed or ownership drifted")

    inputs_after = [stable_file(
        path, executable=path in {
            real_simulator, real_ib, disabled_stub, client_probe, sandbox_probe})
        for path in input_paths]
    if inputs_after != inputs_before:
        fail("rootful gate inputs changed during execution")
    revalidate_gate_host(
        local_host, disposable_host, host_apparmor, docker_daemon_id)
    progress.phase = "complete"
    return {
        "schema": SCHEMA,
        "passed": True,
        "certification_level": "containerized-effective-systemd-rehearsal",
        "duration_ms": int((time.monotonic() - started) * 1000),
        "builds": {"ibapi_on": ib_record, "ibapi_off": disabled_record},
        "disposable_host": {
            "path": disposable_host["path"],
            "root_owned": True,
            "mode": "0400",
            "single_link": True,
            "contract": DISPOSABLE_HOST_SENTINEL_HEADER,
            "machine_id_bound": True,
            "boot_id_bound": True,
            "docker_daemon_id_bound": True,
        },
        "local_host": {
            "kernel_release": local_host["kernel_release"],
            "systemd_pid1": True,
            "containerized_runner": False,
            "local_docker_socket": True,
            "docker_service_preexisting_active": True,
        },
        "host_apparmor": host_apparmor,
        "docker_server": {
            "version": docker_server.get("Version", ""),
            "api_version": docker_server.get("ApiVersion", ""),
            "daemon_id_bound_to_sentinel": True,
        },
        "docker_security": sorted(docker_security),
        "docker_platform": {
            "cgroup_driver": docker_platform[0],
            "cgroup_version": 2,
            "os": docker_platform[2],
            "architecture": docker_platform[3],
        },
        "dockerfile_run_network": "none",
        "base_pull_performed": False,
        "base_image": base_record,
        "images": image_records,
        "variants": results,
        "inputs": inputs_before,
        "input_stability": True,
        "boundary": {
            "real_ibapi_elf_outer_hashed_not_staged": True,
            "real_ibapi_elf_executed": False,
            "real_broker_connections": 0,
            "paper_orders": 0,
            "live_enabled": False,
            "host_hepta_units_started_by_runner": False,
            "user_configured_host_bind_mounts": 0,
            "host_etc_run_usr_bind_mounts": 0,
            "real_ibapi_broker_unreachable":
                "not_run_requires_separate_authorization",
            "final_disposable_vm_gate": "not_satisfied_by_docker",
            "apparmor_policy_content_attested": False,
        },
    }


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description="disposable containerized Hepta effective-systemd pre-gate")
    parser.add_argument("--ibapi-build-dir", type=Path, required=True)
    parser.add_argument("--ib-disabled-build-dir", type=Path, required=True)
    parser.add_argument("--base-image", required=True)
    parser.add_argument("--report", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        safe_report = validate_report_path(args.report, args.ibapi_build_dir)
    except Exception as error:
        print(f"hepta_rootful_systemd_gate: unsafe report path: {error}",
              file=sys.stderr)
        return 2
    install_signal_handlers()
    progress = GateProgress()
    report: dict[str, Any]
    exit_code = 0
    try:
        report = execute(args, progress)
    except Exception as error:
        exit_code = 1
        report = failure_report(error, progress)
    try:
        cleanup_docker_config()
    except Exception as cleanup_error:
        exit_code = 1
        report = failure_report(cleanup_error, progress)
        report["error"] = "isolated Docker configuration cleanup failed"
    try:
        atomic_report(safe_report, report)
    except Exception as report_error:
        print(f"hepta_rootful_systemd_gate: report failure: {report_error}",
              file=sys.stderr)
        return 2
    if exit_code:
        print("hepta_rootful_systemd_gate: FAIL", file=sys.stderr)
        return exit_code
    print("hepta_rootful_systemd_gate: PASS "
          "level=containerized-effective-systemd-rehearsal")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
