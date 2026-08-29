#!/usr/bin/env python3

"""Run the offline four-identity Agent OS systemd E2E rehearsal.

The gate only creates disposable, purpose-labelled Docker objects.  It never
bind-mounts host paths, never publishes a port, always uses ``network=none``,
and stages neither an IB adapter nor PAPER/LIVE units.  The runtime session is
WATCH-only and all MCP calls are read-only.
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass, field
import ctypes
import hashlib
import json
import os
from pathlib import Path
import re
import resource
import signal
import stat
import subprocess
import sys
import tempfile
import time
from typing import Any, Optional
import uuid

sys.path.insert(0, str(Path(__file__).resolve(strict=True).parent))
import hepta_rootful_review_closure_consumer as ROOT_REVIEW


SCHEMA = "hepta.agent-os-rootful-systemd-e2e-gate.v1"
INNER_SCHEMA = "hepta.agent-os-rootful-systemd-e2e-inner.v2"
PURPOSE = "agent-os-rootful-systemd-e2e-gate"
PURPOSE_LABEL = f"io.hepta.purpose={PURPOSE}"
BASE_HOLDER_ROLE = "base-rootfs-snapshot-holder"
BUILT_IMAGE_ROLE = "offline-rootful-systemd-runtime"
ROLE_LABEL_KEY = "io.hepta.role"
RUN_ID_LABEL_KEY = "io.hepta.run-id"
BASE_IMAGE_ID_LABEL_KEY = "io.hepta.base-image-id"
BASE_ROOTFS_SHA256_LABEL_KEY = "io.hepta.base-rootfs-sha256"
BASE_CONSTRUCTION_LABEL_KEY = "io.hepta.base-construction"
BASE_CONSTRUCTION_VERSION = "docker-export-scratch-add-v1"
BUILDER_DAEMON_ROLE = "isolated-buildkit-daemon"
BUILDER_STATE_ROLE = "isolated-buildkit-state"
BUILDKIT_IMAGE_ID_LABEL_KEY = "io.hepta.buildkit-image-id"
BUILDX_BUILDER_LABEL_KEY = "io.hepta.buildx-builder"
BUILDKIT_STATE_DIRECTORY = "/var/lib/buildkit"
APPARMOR_PROFILE = "hepta-systemd-gate"
APPARMOR_ATTACH = APPARMOR_PROFILE
APPARMOR_SECURITY_ROOT = Path("/sys/kernel/security")
APPARMOR_CONTROL_ROOT = APPARMOR_SECURITY_ROOT / "apparmor"
APPARMOR_POLICY_MAGIC_LINK = APPARMOR_CONTROL_ROOT / "policy"
SECURITYFS_MAGIC = 0x73636673
AAFS_MAGIC = 0x5A3C69F0
PINNED_IMAGE = re.compile(
    r"^[a-z0-9][a-z0-9._/:-]*@sha256:[0-9a-f]{64}$")
CANONICAL_SHA256 = re.compile(r"^sha256:[0-9a-f]{64}$")
BARE_SHA256 = re.compile(r"^[0-9a-f]{64}$")
APPARMOR_RAW_ABI = re.compile(r"^v[1-9][0-9]{0,2}$")
APPARMOR_POLICY_ENTRY = re.compile(r"^[A-Za-z0-9_.:@+=-]{1,255}$")
APPARMOR_RAW_DATA_ID = re.compile(r"^[1-9][0-9]{0,19}$")
APPARMOR_MAGIC_LINK_TARGET = re.compile(
    r"^apparmorfs:\[([1-9][0-9]{0,19})\]$")
DOCKER_DAEMON_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9:._-]{0,127}$")
BOOT_ID = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-"
    r"[0-9a-f]{4}-[0-9a-f]{12}$")
REVIEWED_BASE_PROVENANCE_SCHEMA = (
    "hepta.agent-os-rootful-systemd-base-reviewed-provenance.v1")
REVIEWED_BASE_PROVENANCE_KEYS = frozenset({
    "schema", "decision", "issued_at_ms", "expires_at_ms", "image_id",
    "repo_digest", "labels_sha256",
})
REVIEWED_BUILDER_PROVENANCE_SCHEMA = (
    "hepta.agent-os-rootful-systemd-isolated-builder-"
    "reviewed-provenance.v1")
REVIEWED_BUILDER_PROVENANCE_KEYS = frozenset({
    "schema", "decision", "issued_at_ms", "expires_at_ms", "image_id",
    "repo_digest", "config_sha256",
    "buildkit_version", "buildx_version", "buildx_binary_sha256",
    "docker_server_version", "docker_server_api_version",
    "docker_server_git_commit",
})
SEMANTIC_VERSION = re.compile(
    r"^[0-9]+\.[0-9]+\.[0-9]+(?:[-+][0-9A-Za-z._-]+)?$")
BUILDKIT_VERSION = re.compile(
    r"^v[0-9]+\.[0-9]+\.[0-9]+(?:[-+][0-9A-Za-z._-]+)?$")
DOCKER_API_VERSION = re.compile(r"^[1-9][0-9]*\.[0-9]+$")
SAFE_BUILD_ID = re.compile(r"^[0-9A-Za-z][0-9A-Za-z._+-]{0,127}$")
REVIEWED_BASE_LABELS = {
    "io.hepta.rootful-systemd-base.offline-ready": "true",
    "io.hepta.rootful-systemd-base.version": "1",
}
CANDIDATE_BASE_LABEL_KEYS = frozenset({
    "org.trillionnium.root-linux.base-manifest",
    "org.trillionnium.root-linux.builder-contract",
    "org.trillionnium.root-linux.production-approved",
})
REVIEWED_APPARMOR_PROVENANCE_SCHEMA = (
    "hepta.agent-os-rootful-systemd-apparmor-reviewed-provenance.v1")
REVIEWED_APPARMOR_PROVENANCE_KEYS = frozenset({
    "schema", "decision", "issued_at_ms", "expires_at_ms", "profile",
    "policy_source_sha256",
    "profile_sha256", "raw_sha256", "raw_abi",
})
REVIEWED_DOCKER_APPARMOR_NAMESPACE_PROVENANCE_SCHEMA = (
    "hepta.agent-os-rootful-systemd-docker-apparmor-namespace-"
    "reviewed-provenance.v1")
REVIEWED_DOCKER_APPARMOR_NAMESPACE_PROVENANCE_KEYS = frozenset({
    "schema", "decision", "issued_at_ms", "expires_at_ms",
    "docker_daemon_id", "docker_daemon_pid",
    "docker_daemon_start_time_ticks", "host_boot_id",
    "host_namespace_name", "host_namespace_level",
    "host_namespace_stacked", "daemon_namespace_name",
    "daemon_namespace_level", "daemon_namespace_stacked",
})
MAX_COMMAND_OUTPUT = 4 * 1024 * 1024
MAX_REPORT_BYTES = 4 * 1024 * 1024
MAX_APPARMOR_POLICY_ENTRIES = 4096
MAX_APPARMOR_SCALAR_BYTES = 4096
MAX_APPARMOR_RAW_DATA_BYTES = 16 * 1024 * 1024
MAX_REVIEWED_BASE_PROVENANCE_BYTES = 16 * 1024
MAX_REVIEWED_BUILDER_PROVENANCE_BYTES = 16 * 1024
MAX_REVIEWED_APPARMOR_PROVENANCE_BYTES = 16 * 1024
MAX_REVIEWED_DOCKER_APPARMOR_NAMESPACE_PROVENANCE_BYTES = 16 * 1024
MAX_BASE_ROOTFS_TAR_BYTES = 8 * 1024 * 1024 * 1024
COMMAND_ENV = {
    "PATH": "/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin",
    "LANG": "C",
    "LC_ALL": "C",
    "TZ": "UTC",
}
RUNTIME_CAPABILITIES = frozenset({
    "AUDIT_WRITE", "BPF", "CHOWN", "DAC_OVERRIDE", "FOWNER", "FSETID",
    "KILL", "MKNOD", "NET_ADMIN", "NET_BIND_SERVICE", "PERFMON",
    "SETFCAP", "SETGID", "SETPCAP", "SETUID", "SYS_ADMIN", "SYS_CHROOT",
    "SYS_PTRACE",
})
RUNTIME_TMPFS = {
    "/etc/heptatrader":
        "rw,nosuid,nodev,noexec,mode=0755,size=4m",
    "/run": "rw,nosuid,nodev,mode=0755,size=64m",
    "/run/lock": "rw,nosuid,nodev,noexec,mode=0755,size=8m",
    "/tmp": "rw,nosuid,nodev,noexec,mode=1777,size=128m",
    "/var/lib": "rw,nosuid,nodev,noexec,mode=0755,size=512m",
    "/var/log": "rw,nosuid,nodev,noexec,mode=0755,size=64m",
    "/var/tmp": "rw,nosuid,nodev,noexec,mode=1777,size=64m",
}
PLACEHOLDER_LEASE_KEY = (
    b"HEPTA_AGENT_OS_UNPROVISIONED_SUPERVISOR_LEASE_V1\n")
FULL_CHAIN_MARKER = b"HEPTA_AGENT_OS_TWO_DOMAIN_FULL_CHAIN_E2E_V1\n"
DOCKER_SOCKET = Path("/run/docker.sock")
_DOCKER_CONFIG: Optional[tempfile.TemporaryDirectory[str]] = None
CERTIFICATION_BLOCKERS = (ROOT_REVIEW.CERTIFICATION_BLOCKER,)


class GateError(RuntimeError):
    """A fail-closed gate error."""


@dataclass
class Progress:
    phase: str = "local_input_validation"
    docker_api_touched: bool = False
    image_build_started: bool = False
    container_start_attempted: bool = False
    inner_gate_started: bool = False
    owned_docker_objects_cleanup_complete: bool = False
    builder_cache_cleanup_complete: bool = False
    completed_checks: list[str] = field(default_factory=list)
    apparmor: Optional[dict[str, Any]] = None
    apparmor_provenance: Optional["ReviewedAppArmorProvenance"] = None
    docker_apparmor_namespace: Optional[dict[str, Any]] = None
    docker_apparmor_namespace_provenance: Optional[
        "ReviewedDockerAppArmorNamespaceProvenance"] = None
    environment_review_session: Optional[
        ROOT_REVIEW.VerificationSession] = None


@dataclass(frozen=True)
class ReviewedBaseProvenance:
    """A digest-pinned external GO decision for one exact local base image."""

    document_sha256: str
    image_id: str
    repo_digest: str
    labels_sha256: str
    issued_at_ms: int
    expires_at_ms: int

    def report_record(self) -> dict[str, str]:
        return {
            "schema": REVIEWED_BASE_PROVENANCE_SCHEMA,
            "decision": "GO",
            "document_sha256": self.document_sha256,
            "image_id": self.image_id,
            "repo_digest": self.repo_digest,
            "labels_sha256": self.labels_sha256,
            "issued_at_ms": self.issued_at_ms,
            "expires_at_ms": self.expires_at_ms,
        }


@dataclass(frozen=True)
class ReviewedBuilderProvenance:
    """External GO binding one exact local BuildKit/buildx toolchain."""

    document_sha256: str
    image_id: str
    repo_digest: str
    config_sha256: str
    buildkit_version: str
    buildx_version: str
    buildx_binary_sha256: str
    docker_server_version: str
    docker_server_api_version: str
    docker_server_git_commit: str
    issued_at_ms: int
    expires_at_ms: int

    def report_record(self) -> dict[str, str]:
        return {
            "schema": REVIEWED_BUILDER_PROVENANCE_SCHEMA,
            "decision": "GO",
            "document_sha256": self.document_sha256,
            "image_id": self.image_id,
            "repo_digest": self.repo_digest,
            "config_sha256": self.config_sha256,
            "buildkit_version": self.buildkit_version,
            "buildx_version": self.buildx_version,
            "buildx_binary_sha256": self.buildx_binary_sha256,
            "docker_server_version": self.docker_server_version,
            "docker_server_api_version": self.docker_server_api_version,
            "docker_server_git_commit": self.docker_server_git_commit,
            "issued_at_ms": self.issued_at_ms,
            "expires_at_ms": self.expires_at_ms,
        }


@dataclass(frozen=True)
class ReviewedAppArmorProvenance:
    """A digest-pinned external GO decision for one exact AppArmor policy."""

    document_sha256: str
    profile: str
    policy_source_sha256: str
    profile_sha256: str
    raw_sha256: str
    raw_abi: str
    issued_at_ms: int
    expires_at_ms: int

    def report_record(self) -> dict[str, str]:
        return {
            "schema": REVIEWED_APPARMOR_PROVENANCE_SCHEMA,
            "decision": "GO",
            "document_sha256": self.document_sha256,
            "profile": self.profile,
            "policy_source_sha256": self.policy_source_sha256,
            "profile_sha256": self.profile_sha256,
            "raw_sha256": self.raw_sha256,
            "raw_abi": self.raw_abi,
            "issued_at_ms": self.issued_at_ms,
            "expires_at_ms": self.expires_at_ms,
        }


@dataclass(frozen=True)
class ReviewedDockerAppArmorNamespaceProvenance:
    """External GO binding one Docker daemon process to the host AA namespace."""

    document_sha256: str
    docker_daemon_id: str
    docker_daemon_pid: int
    docker_daemon_start_time_ticks: int
    host_boot_id: str
    host_namespace_name: str
    host_namespace_level: int
    host_namespace_stacked: bool
    daemon_namespace_name: str
    daemon_namespace_level: int
    daemon_namespace_stacked: bool
    issued_at_ms: int
    expires_at_ms: int

    def report_record(self) -> dict[str, Any]:
        return {
            "schema":
                REVIEWED_DOCKER_APPARMOR_NAMESPACE_PROVENANCE_SCHEMA,
            "decision": "GO",
            "document_sha256": self.document_sha256,
            "docker_daemon_id": self.docker_daemon_id,
            "docker_daemon_pid": self.docker_daemon_pid,
            "docker_daemon_start_time_ticks":
                self.docker_daemon_start_time_ticks,
            "host_boot_id": self.host_boot_id,
            "host_namespace_name": self.host_namespace_name,
            "host_namespace_level": self.host_namespace_level,
            "host_namespace_stacked": self.host_namespace_stacked,
            "daemon_namespace_name": self.daemon_namespace_name,
            "daemon_namespace_level": self.daemon_namespace_level,
            "daemon_namespace_stacked": self.daemon_namespace_stacked,
            "issued_at_ms": self.issued_at_ms,
            "expires_at_ms": self.expires_at_ms,
        }


@dataclass
class AppArmorKernelAnchor:
    """One open, revalidatable descriptor for the kernel AAFS policy mount."""

    descriptor: int
    policy_root: Path
    record: dict[str, Any]

    def close(self) -> None:
        descriptor = self.descriptor
        self.descriptor = -1
        if descriptor >= 0:
            os.close(descriptor)


def fail(message: str) -> None:
    raise GateError(message)


def repository_root() -> Path:
    return Path(__file__).resolve(strict=True).parents[1]


def require_pinned_image(value: str) -> str:
    if (type(value) is not str or
            PINNED_IMAGE.fullmatch(value) is None):
        fail("--base-image must be a registry reference with an exact sha256 digest")
    return value


def initialize_docker_config() -> Path:
    global _DOCKER_CONFIG
    if _DOCKER_CONFIG is not None:
        fail("isolated Docker configuration initialized twice")
    _DOCKER_CONFIG = tempfile.TemporaryDirectory(
        prefix="hepta-agent-os-e2e-docker-config-")
    path = Path(_DOCKER_CONFIG.name)
    os.chmod(path, 0o700)
    return path


def cleanup_docker_config() -> None:
    global _DOCKER_CONFIG
    holder = _DOCKER_CONFIG
    _DOCKER_CONFIG = None
    if holder is not None:
        holder.cleanup()


def docker_cli(*arguments: str) -> list[str]:
    if _DOCKER_CONFIG is None:
        fail("isolated Docker configuration is not initialized")
    return [
        "docker", "--config", _DOCKER_CONFIG.name,
        "--host=unix:///run/docker.sock", *arguments,
    ]


def command(
        arguments: list[str],
        *,
        timeout: int = 120,
        check: bool = True,
) -> subprocess.CompletedProcess[str]:
    process = subprocess.Popen(
        arguments,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
        errors="replace",
        env=COMMAND_ENV,
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
    if len(stdout.encode("utf-8", errors="replace")) > MAX_COMMAND_OUTPUT:
        fail(f"bounded command output exceeded: {arguments[0]}")
    if check and completed.returncode != 0:
        fail(f"command failed rc={completed.returncode}: {arguments[0]}")
    return completed


def read_regular_file(
        path: Path,
        *,
        maximum: int = 512 * 1024 * 1024,
        executable: bool = False,
) -> tuple[os.stat_result, bytes, str]:
    try:
        descriptor = os.open(
            path, os.O_RDONLY | os.O_CLOEXEC | getattr(os, "O_NOFOLLOW", 0))
    except OSError as error:
        fail(f"cannot securely open {path}: {error.strerror}")
    digest = hashlib.sha256()
    chunks: list[bytes] = []
    total = 0
    try:
        before = os.fstat(descriptor)
        if not stat.S_ISREG(before.st_mode) or before.st_nlink != 1:
            fail(f"input must be a single-link regular file: {path}")
        if before.st_size <= 0 or before.st_size > maximum:
            fail(f"input size outside reviewed bound: {path}")
        if executable and not before.st_mode & stat.S_IXUSR:
            fail(f"input is not executable: {path}")
        while True:
            chunk = os.read(descriptor, min(1024 * 1024, maximum + 1 - total))
            if not chunk:
                break
            chunks.append(chunk)
            digest.update(chunk)
            total += len(chunk)
            if total > maximum:
                fail(f"input size outside reviewed bound: {path}")
        after = os.fstat(descriptor)
    finally:
        os.close(descriptor)
    fields = (
        "st_dev", "st_ino", "st_mode", "st_nlink", "st_uid", "st_gid",
        "st_size", "st_mtime_ns", "st_ctime_ns")
    if (total != before.st_size or
            any(getattr(before, field) != getattr(after, field)
                for field in fields)):
        fail(f"input changed while reading: {path}")
    contents = b"".join(chunks)
    if executable and (
            len(contents) < 64 or
            contents[:6] != b"\x7fELF\x02\x01" or
            contents[18:20] != b"\x3e\x00"):
        fail(f"expected Linux amd64 ELF executable: {path}")
    return before, contents, digest.hexdigest()


def validate_private_directory(path: Path) -> os.stat_result:
    try:
        metadata = os.lstat(path)
    except OSError as error:
        fail(f"cannot inspect private staging directory: {error.strerror}")
    if (
            not stat.S_ISDIR(metadata.st_mode) or
            metadata.st_uid != os.geteuid() or
            stat.S_IMODE(metadata.st_mode) != 0o700):
        fail("base rootfs staging directory is not private and owner-bound")
    return metadata


def stable_private_rootfs_tar(
        path: Path,
        *,
        maximum: int = MAX_BASE_ROOTFS_TAR_BYTES,
) -> dict[str, Any]:
    if type(maximum) is not int or maximum <= 0:
        fail("base rootfs tar bound is invalid")
    validate_private_directory(path.parent)
    try:
        descriptor = os.open(
            path,
            os.O_RDONLY | os.O_CLOEXEC | getattr(os, "O_NOFOLLOW", 0),
        )
    except OSError as error:
        fail(f"cannot securely open base rootfs tar: {error.strerror}")
    digest = hashlib.sha256()
    total = 0
    fields = (
        "st_dev", "st_ino", "st_mode", "st_nlink", "st_uid", "st_gid",
        "st_size", "st_mtime_ns", "st_ctime_ns",
    )
    try:
        before = os.fstat(descriptor)
        if (
                not stat.S_ISREG(before.st_mode) or
                before.st_nlink != 1 or
                before.st_uid != os.geteuid() or
                stat.S_IMODE(before.st_mode) != 0o600):
            fail("base rootfs tar is not a private owner-bound regular file")
        if (
                before.st_size <= 0 or
                before.st_size > maximum or
                before.st_size % 512 != 0):
            fail("base rootfs tar size is outside the stable archive bound")
        while True:
            chunk = os.read(descriptor, 1024 * 1024)
            if not chunk:
                break
            total += len(chunk)
            if total > maximum:
                fail("base rootfs tar exceeded its streaming bound")
            digest.update(chunk)
        after = os.fstat(descriptor)
        try:
            named = os.lstat(path)
        except OSError as error:
            fail(
                "base rootfs tar disappeared during metadata recheck: "
                f"{error.strerror}")
    finally:
        os.close(descriptor)
    if (
            total != before.st_size or
            any(getattr(before, field) != getattr(after, field)
                for field in fields) or
            any(getattr(after, field) != getattr(named, field)
                for field in fields)):
        fail("base rootfs tar changed while it was being attested")
    return {
        "path": path.name,
        "sha256": "sha256:" + digest.hexdigest(),
        "size": total,
        "mode": "0600",
        "uid": before.st_uid,
        "device": before.st_dev,
        "inode": before.st_ino,
    }


def verify_private_rootfs_tar_unchanged(
        path: Path,
        expected: dict[str, Any],
        *,
        maximum: int = MAX_BASE_ROOTFS_TAR_BYTES,
) -> None:
    if type(expected) is not dict:
        fail("base rootfs tar attestation is missing")
    if stable_private_rootfs_tar(path, maximum=maximum) != expected:
        fail("base rootfs tar changed after its source attestation")


def _terminate_process(process: subprocess.Popen[Any]) -> None:
    if process.poll() is not None:
        return
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


def stream_docker_export(
        arguments: list[str],
        destination: Path,
        *,
        maximum: int = MAX_BASE_ROOTFS_TAR_BYTES,
        timeout: int = 900,
) -> dict[str, Any]:
    """Stream one Docker export into a private, kernel-size-bounded file."""

    if type(maximum) is not int or maximum <= 0:
        fail("base rootfs export bound is invalid")
    validate_private_directory(destination.parent)
    try:
        descriptor = os.open(
            destination,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_CLOEXEC |
            getattr(os, "O_NOFOLLOW", 0),
            0o600,
        )
    except OSError as error:
        fail(f"cannot create private base rootfs tar: {error.strerror}")
    process: Optional[subprocess.Popen[str]] = None
    created = os.fstat(descriptor)
    identity_fields = (
        "st_dev", "st_ino", "st_mode", "st_nlink", "st_uid", "st_gid",
    )
    try:
        os.fchmod(descriptor, 0o600)
        created = os.fstat(descriptor)
        if (
                not stat.S_ISREG(created.st_mode) or
                created.st_nlink != 1 or
                created.st_uid != os.geteuid() or
                stat.S_IMODE(created.st_mode) != 0o600 or
                created.st_size != 0):
            fail("new base rootfs tar does not have private stable metadata")
        _soft_limit, hard_limit = resource.getrlimit(resource.RLIMIT_FSIZE)
        effective_limit = maximum
        if hard_limit != resource.RLIM_INFINITY:
            effective_limit = min(effective_limit, hard_limit)
        if effective_limit <= 0:
            fail("process file-size limit cannot contain the base rootfs tar")

        def apply_export_limit() -> None:
            resource.setrlimit(
                resource.RLIMIT_FSIZE, (effective_limit, effective_limit))

        process = subprocess.Popen(
            arguments,
            stdin=subprocess.DEVNULL,
            stdout=descriptor,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
            env=COMMAND_ENV,
            start_new_session=True,
            preexec_fn=apply_export_limit,
        )
        try:
            _unused, stderr = process.communicate(timeout=timeout)
        except subprocess.TimeoutExpired as error:
            _terminate_process(process)
            raise GateError("Docker base rootfs export timed out") from error
        except BaseException:
            _terminate_process(process)
            raise
        stderr = stderr or ""
        if len(stderr.encode("utf-8", errors="replace")) > MAX_COMMAND_OUTPUT:
            fail("Docker base rootfs export diagnostics exceeded their bound")
        if process.returncode != 0:
            fail(
                "exact inspected base image could not be exported from its "
                "owned holder")
        os.fsync(descriptor)
        exported = os.fstat(descriptor)
        if (
                any(getattr(created, field) != getattr(exported, field)
                    for field in identity_fields) or
                exported.st_size <= 0 or exported.st_size > maximum):
            fail("base rootfs export metadata or size escaped its bound")
    finally:
        if process is not None:
            _terminate_process(process)
        os.close(descriptor)
    record = stable_private_rootfs_tar(destination, maximum=maximum)
    if (
            record["device"] != created.st_dev or
            record["inode"] != created.st_ino):
        fail("base rootfs tar identity changed after Docker export")
    return record


def stable_record(path: Path, *, executable: bool = False) -> dict[str, Any]:
    metadata, _contents, digest = read_regular_file(
        path, executable=executable)
    try:
        display = path.resolve(strict=True).relative_to(
            repository_root()).as_posix()
    except ValueError:
        display = path.name
    return {
        "path": display,
        "sha256": digest,
        "size": metadata.st_size,
        "mode": format(stat.S_IMODE(metadata.st_mode), "04o"),
        "device": metadata.st_dev,
        "inode": metadata.st_ino,
    }


def write_private(path: Path, contents: bytes, mode: int) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor = os.open(
        path,
        os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_CLOEXEC,
        mode,
    )
    try:
        view = memoryview(contents)
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
    after, _again, after_digest = read_regular_file(source)
    fields = (
        "st_dev", "st_ino", "st_mode", "st_nlink", "st_uid", "st_gid",
        "st_size", "st_mtime_ns", "st_ctime_ns")
    if (digest != after_digest or
            any(getattr(before, field) != getattr(after, field)
                for field in fields) or
            read_regular_file(destination)[2] != digest):
        fail(f"input changed while staging: {source}")


def parse_cmake_cache(build: Path) -> dict[str, str]:
    contents = read_regular_file(
        build / "CMakeCache.txt", maximum=8 * 1024 * 1024)[1]
    try:
        text = contents.decode("utf-8", errors="strict")
    except UnicodeDecodeError:
        fail("CMake cache is not valid UTF-8")
    values: dict[str, str] = {}
    for line in text.splitlines():
        if not line or line.startswith(("#", "//")) or "=" not in line:
            continue
        left, value = line.split("=", 1)
        if ":" not in left:
            continue
        key, _kind = left.split(":", 1)
        if key in values:
            fail(f"duplicate CMake cache key: {key}")
        values[key] = value
    return values


def find_binary(build: Path, name: str) -> Path:
    candidates = (
        build / "bin/Release" / name,
        build / "HeptaTrade" / name,
        build / name,
    )
    matches: list[Path] = []
    for candidate in candidates:
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
            fail(f"build artifact escaped build tree: {candidate}")
        matches.append(resolved)
    if len(set(matches)) != 1:
        fail(f"expected one {name!r} in {build}, found {len(set(matches))}")
    result = next(iter(set(matches)))
    read_regular_file(result, executable=True)
    return result


def validate_build(
        argument: Path,
) -> tuple[Path, dict[str, Path], dict[str, Any]]:
    build = argument.resolve(strict=True)
    if not build.is_dir() or build.is_symlink():
        fail("build directory must be a real directory")
    values = parse_cmake_cache(build)
    try:
        source = Path(values.get("CMAKE_HOME_DIRECTORY", "")).resolve(
            strict=True)
    except OSError:
        fail("CMAKE_HOME_DIRECTORY is invalid")
    if source != repository_root():
        fail("build source tree does not match this repository")
    expected = {
        "CMAKE_BUILD_TYPE": "Release",
        "BUILD_TESTING": "ON",
        "CMAKE_EXPORT_COMPILE_COMMANDS": "ON",
        "HEPTA_ENABLE_LEGACY_0DTE_BRIDGE": "OFF",
        "HEPTA_ENABLE_IBAPI": "OFF",
    }
    for key, value in expected.items():
        if values.get(key, "").upper() != value.upper():
            fail(f"{key} must be exactly {value}")
    binaries = {
        name: find_binary(build, name)
        for name in (
            "hepta-executiond", "hepta-tool-gatewayd",
            "hepta-sessionctl", "heptactl")
    }
    return build, binaries, {
        "path": build.relative_to(repository_root()).as_posix(),
        "cmake_cache_sha256": stable_record(
            build / "CMakeCache.txt")["sha256"],
        "compile_commands_sha256": stable_record(
            build / "compile_commands.json")["sha256"],
        "build_type": "Release",
        "ibapi_enabled": False,
        "legacy_bridge_enabled": False,
    }


def staged_sources(
        binaries: dict[str, Path],
) -> dict[str, tuple[Path, int]]:
    root = repository_root()
    return {
        "usr/bin/heptactl": (binaries["heptactl"], 0o755),
        "usr/bin/hepta-sessionctl": (
            binaries["hepta-sessionctl"], 0o755),
        "usr/libexec/hepta-executiond": (
            binaries["hepta-executiond"], 0o755),
        "usr/libexec/hepta-tool-gatewayd": (
            binaries["hepta-tool-gatewayd"], 0o755),
        "usr/libexec/hepta-mcp-server": (
            root / "adapters/mcp/hepta_mcp_server.py", 0o755),
        "usr/libexec/hepta-agent-mcp-launcher": (
            root / "scripts/hepta_agent_mcp_launcher.py", 0o755),
        "usr/libexec/hepta_agent_trust_domain.py": (
            root / "scripts/hepta_agent_trust_domain.py", 0o755),
        "usr/libexec/hepta-agent-session-bootstrap": (
            root / "scripts/hepta_agent_session_bootstrap.py", 0o755),
        "usr/libexec/hepta-paper-receipt-contracts": (
            root / "scripts/hepta_paper_receipt_contracts.py", 0o755),
        "usr/libexec/hepta-shadow-watch-collector": (
            root / "scripts/hepta_shadow_watch_collector.py", 0o755),
        "usr/libexec/hepta-shadow-watch-exporter": (
            root / "scripts/hepta_shadow_watch_exporter.py", 0o755),
        "usr/libexec/hepta-shadow-watch-custodian": (
            root / "scripts/hepta_shadow_watch_custodian.py", 0o755),
        "usr/libexec/hepta-shadow-host-installer": (
            root / "scripts/hepta_shadow_host_installer.py", 0o755),
        "usr/libexec/hepta-p1-shadow-host-controller": (
            root / "scripts/hepta_p1_shadow_host_controller.py", 0o755),
        "usr/libexec/hepta-p1-load-probe-validator": (
            root / "scripts/hepta_p1_load_probe_validator.py", 0o755),
        "usr/libexec/build-hepta-p1-observation-policy": (
            root / "scripts/build_hepta_p1_observation_policy.py", 0o755),
        "usr/libexec/hepta-p1-shadow-observer-controller": (
            root / "scripts/hepta_p1_shadow_observer_controller.py", 0o755),
        "usr/libexec/hepta-p1-shadow-admission-launcher": (
            root / "scripts/hepta_p1_shadow_admission_launcher.py", 0o755),
        "usr/libexec/hepta-p1-watch-profile-deployer": (
            root / "scripts/hepta_p1_watch_profile_deployer.py", 0o755),
        "usr/libexec/hepta-p1-watch-activation-transaction": (
            root / "scripts/hepta_p1_watch_activation_transaction.py",
            0o755),
        "usr/libexec/hepta-bounded-shadow-closure-verifier": (
            root / "scripts/hepta_bounded_shadow_closure_verifier.py", 0o755),
        "usr/libexec/hepta-official-source-capture": (
            root / "scripts/hepta_official_source_capture.py", 0o755),
        "usr/libexec/hepta_bounded_shadow_observer.py": (
            root / "scripts/hepta_bounded_shadow_observer.py", 0o755),
        "usr/libexec/hepta_market_context_builder.py": (
            root / "scripts/hepta_market_context_builder.py", 0o755),
        "usr/libexec/hepta_market_evidence_normalizer.py": (
            root / "scripts/hepta_market_evidence_normalizer.py", 0o755),
        "usr/libexec/hepta_market_official_source_extractor.py": (
            root / "scripts/hepta_market_official_source_extractor.py", 0o755),
        "usr/libexec/hepta_eurusd_confirmed_momentum_strategy.py": (
            root / "scripts/hepta_eurusd_confirmed_momentum_strategy.py",
            0o755),
        "usr/libexec/hepta_shadow_market_history.py": (
            root / "scripts/hepta_shadow_market_history.py", 0o755),
        "usr/libexec/hepta_strategy_shadow_runner.py": (
            root / "scripts/hepta_strategy_shadow_runner.py", 0o755),
        "usr/libexec/hepta_strategy_contracts.py": (
            root / "scripts/hepta_strategy_contracts.py", 0o644),
        "usr/libexec/validate_hepta_strategy_decision_receipt.py": (
            root / "scripts/validate_hepta_strategy_decision_receipt.py",
            0o755),
        "usr/share/heptatrader/strategies/"
        "eurusd-confirmed-momentum-shadow-v2.json": (
            root / "strategies/eurusd-confirmed-momentum-shadow-v2.json",
            0o644),
        "usr/libexec/hepta-broker-egress-policy": (
            root / "scripts/hepta_broker_egress_policy.py", 0o755),
        "usr/libexec/check-hepta-agent-os-provisioned-host": (
            root / "scripts/check_hepta_agent_os_provisioned_host.py", 0o755),
        "usr/lib/systemd/system/hepta-execution-simulator.service": (
            root / "systemd/hepta-execution-simulator.service", 0o644),
        "usr/lib/systemd/system/hepta-execution-simulator.socket": (
            root / "systemd/hepta-execution-simulator.socket", 0o644),
        "usr/lib/systemd/system/"
        "hepta-execution-events-simulator.socket": (
            root / "systemd/hepta-execution-events-simulator.socket", 0o644),
        "usr/lib/systemd/system/hepta-execution-simulator@.service": (
            root / "systemd/hepta-execution-simulator@.service", 0o644),
        "usr/lib/systemd/system/hepta-execution-simulator@.socket": (
            root / "systemd/hepta-execution-simulator@.socket", 0o644),
        "usr/lib/systemd/system/"
        "hepta-execution-events-simulator@.socket": (
            root / "systemd/hepta-execution-events-simulator@.socket", 0o644),
        "usr/lib/systemd/system/hepta-tool-gateway.service": (
            root / "systemd/hepta-tool-gateway.service", 0o644),
        "usr/lib/systemd/system/hepta-tool-gateway.socket": (
            root / "systemd/hepta-tool-gateway.socket", 0o644),
        "usr/lib/systemd/system/hepta-tool-session-supervisor.socket": (
            root / "systemd/hepta-tool-session-supervisor.socket", 0o644),
        "usr/lib/systemd/system/hepta-shadow-watch-collector@.service": (
            root / "systemd/hepta-shadow-watch-collector@.service", 0o644),
        "usr/lib/systemd/system/hepta-shadow-watch-collector@.timer": (
            root / "systemd/hepta-shadow-watch-collector@.timer", 0o644),
        "usr/lib/systemd/system/hepta-shadow-watch-export@.service": (
            root / "systemd/hepta-shadow-watch-export@.service", 0o644),
        "usr/lib/systemd/system/hepta-tool-gateway@.service": (
            root / "systemd/hepta-tool-gateway@.service", 0o644),
        "usr/lib/systemd/system/hepta-tool-gateway@.socket": (
            root / "systemd/hepta-tool-gateway@.socket", 0o644),
        "usr/lib/systemd/system/hepta-tool-session-supervisor@.socket": (
            root / "systemd/hepta-tool-session-supervisor@.socket", 0o644),
        "usr/lib/systemd/system/hepta-shadow-watch-custodian@.service": (
            root / "systemd/hepta-shadow-watch-custodian@.service", 0o644),
        "usr/lib/systemd/system/"
        "hepta-shadow-watch-custodian-reconcile@.service": (
            root / "systemd/"
            "hepta-shadow-watch-custodian-reconcile@.service", 0o644),
        "usr/lib/systemd/system/"
        "hepta-shadow-watch-custodian-reconcile@.timer": (
            root / "systemd/"
            "hepta-shadow-watch-custodian-reconcile@.timer", 0o644),
        "usr/lib/systemd/system/hepta-broker-egress-policy.service": (
            root / "systemd/hepta-broker-egress-policy.service", 0o644),
        "usr/lib/systemd/system/hepta-p1-watch-activation.service": (
            root / "systemd/hepta-p1-watch-activation.service", 0o644),
        "usr/lib/systemd/system/"
        "hepta-p1-watch-activation-reconcile.service": (
            root / "systemd/hepta-p1-watch-activation-reconcile.service",
            0o644),
        "usr/lib/systemd/system/"
        "hepta-p1-watch-activation-reconcile.timer": (
            root / "systemd/hepta-p1-watch-activation-reconcile.timer",
            0o644),
        "usr/lib/systemd/system/hepta-tool-gateway.service.d/"
        "10-hepta-broker-egress-policy.conf": (
            root / "systemd/hepta-tool-gateway.service.d/"
            "10-hepta-broker-egress-policy.conf", 0o644),
        "usr/lib/systemd/system/hepta-tool-gateway@.service.d/"
        "10-hepta-broker-egress-policy.conf": (
            root / "systemd/hepta-tool-gateway@.service.d/"
            "10-hepta-broker-egress-policy.conf", 0o644),
        "usr/lib/tmpfiles.d/heptatrader-agent-os.conf": (
            root / "tmpfiles.d/heptatrader-agent-os.conf", 0o644),
        "usr/share/heptatrader/hepta-service-identities-v1.json": (
            root / "systemd/hepta-service-identities-v1.json", 0o644),
        "usr/share/heptatrader/hepta-broker-network-policy-v1.json": (
            root / "systemd/hepta-broker-network-policy-v1.json", 0o644),
        "usr/share/heptatrader/plugins/heptatrader-agent-os/"
        ".codex-plugin/plugin.json": (
            root / "plugins/heptatrader-agent-os/.codex-plugin/plugin.json",
            0o644),
        "usr/share/heptatrader/plugins/heptatrader-agent-os/.mcp.json": (
            root / "plugins/heptatrader-agent-os/.mcp.json", 0o644),
        "usr/share/heptatrader/plugins/heptatrader-agent-os/README.md": (
            root / "plugins/heptatrader-agent-os/README.md", 0o644),
        "usr/share/heptatrader/.agents/plugins/marketplace.json": (
            root / ".agents/plugins/marketplace.json", 0o644),
        "usr/share/doc/heptatrader/"
        "AGENT-NATIVE-TRADING-OS-ARCHITECTURE.md": (
            root / "docs/AGENT-NATIVE-TRADING-OS-ARCHITECTURE.md", 0o644),
        "usr/share/doc/heptatrader/RUNBOOK-STARTUP.md": (
            root / "docs/RUNBOOK-STARTUP.md", 0o644),
        "usr/share/doc/heptatrader/BROKER-NETWORK-ISOLATION.md": (
            root / "docs/BROKER-NETWORK-ISOLATION.md", 0o644),
        "usr/share/doc/heptatrader/examples/"
        "hepta-agent-host-identity.conf.example": (
            root / "systemd/hepta-agent-host-identity.conf.example", 0o644),
        "usr/share/doc/heptatrader/examples/"
        "hepta-agent-broker-egress-policy.conf.example": (
            root / "systemd/hepta-agent-broker-egress-policy.conf.example",
            0o644),
        "usr/share/doc/heptatrader/examples/"
        "hepta-tool-gateway.env.example": (
            root / "systemd/hepta-tool-gateway.env.example", 0o644),
        "usr/share/doc/heptatrader/examples/"
        "hepta-tool-gateway-domain.env.example": (
            root / "systemd/hepta-tool-gateway-domain.env.example", 0o644),
        "usr/share/doc/heptatrader/examples/"
        "hepta-shadow-watch-domain.env.example": (
            root / "systemd/hepta-shadow-watch-domain.env.example", 0o644),
        "etc/heptatrader/hepta-tool-gateway.env": (
            root / "systemd/hepta-tool-gateway.env.example", 0o644),
        "etc/heptatrader/hepta-execution-simulator.env": (
            root / "systemd/hepta-execution-simulator.env.example", 0o644),
        "etc/heptatrader/"
        "hepta-agent-trust-domain-paper-identities-v1.json": (
            root / "systemd/"
            "hepta-agent-trust-domain-paper-identities-v1.json.example",
            0o600),
        "usr/local/share/hepta-agent-os-e2e/provisioning/"
        "hepta-tool-gateway.env": (
            root / "systemd/hepta-tool-gateway.env.example", 0o644),
        "usr/local/share/hepta-agent-os-e2e/provisioning/"
        "hepta-execution-simulator.env": (
            root / "systemd/hepta-execution-simulator.env.example", 0o644),
        "usr/local/share/hepta-agent-os-e2e/provisioning/"
        "hepta-agent-trust-domain-paper-identities-v1.json": (
            root / "systemd/"
            "hepta-agent-trust-domain-paper-identities-v1.json.example",
            0o600),
    }


def gate_sources() -> dict[str, tuple[Path, int]]:
    root = repository_root()
    directory = root / "tests/agent_os_rootful_systemd"
    return {
        "gate-inputs/run_hepta_agent_os_rootful_systemd_e2e_gate.py": (
            root / "scripts/run_hepta_agent_os_rootful_systemd_e2e_gate.py",
            0o644),
        "gate-inputs/hepta_rootful_review_closure_consumer.py": (
            root / "scripts/hepta_rootful_review_closure_consumer.py", 0o644),
        "tests/agent_os_rootful_systemd/Dockerfile": (
            directory / "Dockerfile", 0o644),
        "tests/agent_os_rootful_systemd/hepta-agent-os-systemd-entrypoint": (
            directory / "hepta-agent-os-systemd-entrypoint", 0o755),
        "tests/agent_os_rootful_systemd/"
        "hepta-agent-os-rootful-e2e.target": (
            directory / "hepta-agent-os-rootful-e2e.target", 0o644),
        "tests/agent_os_rootful_systemd/"
        "hepta_agent_os_rootful_inner_gate.py": (
            directory / "hepta_agent_os_rootful_inner_gate.py", 0o755),
        "tests/agent_os_rootful_systemd/"
        "hepta_broker_network_rootful_probe.py": (
            directory / "hepta_broker_network_rootful_probe.py", 0o755),
    }


def provisioning_sources() -> set[Path]:
    root = repository_root()
    return {
        root / "scripts/check_hepta_agent_trust_domains.py",
        root / "systemd/hepta-agent-trust-domain-policy-v1.json",
        root / "systemd/"
        "hepta-agent-trust-domain-paper-identities-v1.json.example",
        root / "systemd/hepta-service-identities-v1.json",
        root / "tests/fixtures/hepta-agent-trust-domains-v1.json",
    }


def provision_context(
        context: Path,
        binaries: dict[str, Path],
) -> tuple[list[dict[str, Any]], set[str]]:
    install_root = context / "install-root"
    install_root.mkdir(mode=0o700)
    sources = staged_sources(binaries)
    for relative, (source, mode) in sources.items():
        copy_stable_file(source, install_root / relative, mode)

    write_private(
        install_root / "etc/heptatrader/hepta-supervisor-lease.key",
        PLACEHOLDER_LEASE_KEY,
        0o400,
    )
    write_private(
        install_root / "etc/heptatrader/credentials/"
        "hepta-execution-simulator-fence",
        b"HFC1\nfencing_token=7719001\ngeneration=19\n",
        0o400,
    )
    write_private(
        install_root / "usr/local/share/hepta-agent-os-e2e/provisioning/"
        "hepta-execution-simulator-fence",
        b"HFC1\nfencing_token=7719001\ngeneration=19\n",
        0o400,
    )
    domain_bindings = (
        ("codex-a", 2101, 2104, 2121, 7719101),
        ("openclaw-b", 2102, 2105, 2122, 7719102),
    )
    fixture = json.loads(read_regular_file(
        repository_root() /
        "tests/fixtures/hepta-agent-trust-domains-v1.json",
        maximum=1024 * 1024)[1].decode("utf-8", errors="strict"))
    domains = {
        item["domain_id"]: item
        for item in fixture.get("domains", [])
        if isinstance(item, dict) and isinstance(item.get("domain_id"), str)
    }
    if set(domains) != {"codex-a", "openclaw-b"}:
        fail("two-domain fixture identity mismatch")
    sys.path.insert(0, str(repository_root() / "scripts"))
    import check_hepta_agent_trust_domains as trust_domains
    validated = trust_domains.validate(
        trust_domains.DEFAULT_POLICY,
        trust_domains.DEFAULT_FIXTURE,
        trust_domains.IDENTITIES)
    staged = trust_domains.expected_staging_files(validated)
    provisioning_root = (
        install_root / "usr/local/share/hepta-agent-os-e2e/provisioning")
    write_private(
        provisioning_root / "full-trust-domain-chain.required",
        FULL_CHAIN_MARKER,
        0o444,
    )
    for (
            domain_id, gateway_uid, agent_uid, reader_uid, fencing_token,
    ) in domain_bindings:
        domain = domains[domain_id]
        if (
                domain.get("gateway_uid") != gateway_uid or
                domain.get("agent_uid") != agent_uid):
            fail("two-domain fixture UID binding mismatch")
        for leaf, mode in (
                (f"{domain_id}.json", 0o600),
                (f"uid-{agent_uid}.json", 0o640),
                (f"{domain_id}.env", 0o644),
                (f"{domain_id}.execution.env", 0o644),
                (f"{domain_id}.agent-host.conf", 0o644)):
            relative = f"etc/heptatrader/trust-domains/{leaf}"
            expected = staged.get(relative)
            if expected is None or expected[1] != mode:
                fail("generated trust-domain staging contract mismatch")
            write_private(
                provisioning_root / "trust-domains" / leaf,
                expected[0],
                mode,
            )
        write_private(
            provisioning_root / "trust-domains" /
            f"{domain_id}.shadow-watch.env",
            (
                f"HEPTA_SHADOW_AGENT_UID={agent_uid}\n"
                f"HEPTA_SHADOW_AGENT_GID={agent_uid}\n"
                f"HEPTA_SHADOW_READER_UID={reader_uid}\n"
                f"HEPTA_SHADOW_READER_GID={reader_uid}\n"
            ).encode("ascii"),
            0o600,
        )
        write_private(
            install_root / "usr/local/share/hepta-agent-os-e2e/"
            f"provisioning/{domain_id}.execution-fence",
            (
                "HFC1\n"
                f"fencing_token={fencing_token}\n"
                "generation=19\n"
            ).encode("ascii"),
            0o400,
        )

    for relative, (source, mode) in gate_sources().items():
        copy_stable_file(source, context / relative, mode)

    expected_install = set(sources) | {
        "etc/heptatrader/hepta-supervisor-lease.key",
        "etc/heptatrader/credentials/hepta-execution-simulator-fence",
        "usr/local/share/hepta-agent-os-e2e/provisioning/"
        "hepta-execution-simulator-fence",
    }
    expected_install.add(
        "usr/local/share/hepta-agent-os-e2e/provisioning/"
        "full-trust-domain-chain.required")
    for (
            domain_id, _gateway_uid, agent_uid, _reader_uid, _fencing_token,
    ) in domain_bindings:
        expected_install.update({
            "usr/local/share/hepta-agent-os-e2e/provisioning/"
            f"{domain_id}.execution-fence",
            "usr/local/share/hepta-agent-os-e2e/provisioning/"
            f"trust-domains/{domain_id}.json",
            "usr/local/share/hepta-agent-os-e2e/provisioning/"
            f"trust-domains/uid-{agent_uid}.json",
            "usr/local/share/hepta-agent-os-e2e/provisioning/"
            f"trust-domains/{domain_id}.env",
            "usr/local/share/hepta-agent-os-e2e/provisioning/"
            f"trust-domains/{domain_id}.execution.env",
            "usr/local/share/hepta-agent-os-e2e/provisioning/"
            f"trust-domains/{domain_id}.agent-host.conf",
            "usr/local/share/hepta-agent-os-e2e/provisioning/"
            f"trust-domains/{domain_id}.shadow-watch.env",
        })
    observed_install: set[str] = set()
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
            observed_install.add(path.relative_to(install_root).as_posix())
    if observed_install != expected_install:
        fail("staged install tree exact file allowlist mismatch")

    input_paths = sorted(
        {source for source, _mode in sources.values()} |
        {source for source, _mode in gate_sources().values()} |
        provisioning_sources(),
        key=lambda path: str(path),
    )
    executable_inputs = set(binaries.values())
    records = [
        stable_record(path, executable=path in executable_inputs)
        for path in input_paths
    ]
    return records, expected_install


def reject_duplicate_json_keys(
        pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    record: dict[str, Any] = {}
    for key, value in pairs:
        if key in record:
            fail(f"JSON object contains duplicate field: {key}")
        record[key] = value
    return record


def canonical_base_labels_sha256(labels: dict[str, str]) -> str:
    encoded = json.dumps(
        labels, sort_keys=True, separators=(",", ":"),
        ensure_ascii=True,
    ).encode("ascii")
    return "sha256:" + hashlib.sha256(encoded).hexdigest()


def validate_reviewed_provenance_freshness(
        document: dict[str, Any]) -> tuple[int, int]:
    issued = document.get("issued_at_ms")
    expires = document.get("expires_at_ms")
    now_ms = time.time_ns() // 1_000_000
    if (
            type(issued) is not int or type(expires) is not int or
            issued > now_ms or expires <= now_ms or expires <= issued or
            expires - issued > 24 * 60 * 60 * 1000):
        fail("reviewed provenance is outside its bounded validity window")
    return issued, expires


def load_reviewed_base_provenance(
        path: Path,
        expected_sha256: str,
) -> ReviewedBaseProvenance:
    if (type(expected_sha256) is not str or
            CANONICAL_SHA256.fullmatch(expected_sha256) is None):
        fail(
            "--reviewed-base-provenance-sha256 must be "
            "sha256:<64 lowercase hex>")
    _metadata, contents, observed_digest = read_regular_file(
        path, maximum=MAX_REVIEWED_BASE_PROVENANCE_BYTES)
    if expected_sha256 != "sha256:" + observed_digest:
        fail("reviewed base provenance document digest mismatch")
    try:
        document = json.loads(
            contents.decode("utf-8", errors="strict"),
            object_pairs_hook=reject_duplicate_json_keys,
        )
    except GateError:
        raise
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise GateError(
            "reviewed base provenance document is invalid JSON") from error
    if (type(document) is not dict or
            set(document) != REVIEWED_BASE_PROVENANCE_KEYS):
        fail("reviewed base provenance document field inventory is invalid")
    issued, expires = validate_reviewed_provenance_freshness(document)
    if (document["schema"] != REVIEWED_BASE_PROVENANCE_SCHEMA or
            document["decision"] != "GO"):
        fail("reviewed base provenance does not contain a formal GO decision")
    if (type(document["image_id"]) is not str or
            CANONICAL_SHA256.fullmatch(document["image_id"]) is None):
        fail("reviewed base provenance image ID is not canonical")
    if (type(document["repo_digest"]) is not str or
            PINNED_IMAGE.fullmatch(document["repo_digest"]) is None):
        fail("reviewed base provenance RepoDigest is not canonical")
    if (type(document["labels_sha256"]) is not str or
            CANONICAL_SHA256.fullmatch(document["labels_sha256"]) is None):
        fail("reviewed base provenance label digest is not canonical")
    return ReviewedBaseProvenance(
        document_sha256=expected_sha256,
        image_id=document["image_id"],
        repo_digest=document["repo_digest"],
        labels_sha256=document["labels_sha256"],
        issued_at_ms=issued, expires_at_ms=expires,
    )


def reviewed_base_provenance_from_arguments(
        path: Optional[Path],
        expected_sha256: Optional[str],
        *,
        allow_candidate: bool,
) -> Optional[ReviewedBaseProvenance]:
    if type(allow_candidate) is not bool:
        fail("candidate base opt-in must be an explicit boolean")
    if (path is None) != (expected_sha256 is None):
        fail(
            "--reviewed-base-provenance and "
            "--reviewed-base-provenance-sha256 must be supplied together")
    if path is None:
        if not allow_candidate:
            fail(
                "a digest-pinned external reviewed base provenance GO "
                "is required")
        return None
    if not isinstance(path, Path):
        fail("--reviewed-base-provenance must be a filesystem path")
    if expected_sha256 is None:
        fail("reviewed base provenance digest binding is missing")
    return load_reviewed_base_provenance(path, expected_sha256)


def load_reviewed_builder_provenance(
        path: Path,
        expected_sha256: str,
) -> ReviewedBuilderProvenance:
    if (type(expected_sha256) is not str or
            CANONICAL_SHA256.fullmatch(expected_sha256) is None):
        fail(
            "--reviewed-builder-provenance-sha256 must be "
            "sha256:<64 lowercase hex>")
    _metadata, contents, observed_digest = read_regular_file(
        path, maximum=MAX_REVIEWED_BUILDER_PROVENANCE_BYTES)
    if expected_sha256 != "sha256:" + observed_digest:
        fail("reviewed builder provenance document digest mismatch")
    try:
        document = json.loads(
            contents.decode("utf-8", errors="strict"),
            object_pairs_hook=reject_duplicate_json_keys,
        )
    except GateError:
        raise
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise GateError(
            "reviewed builder provenance document is invalid JSON") from error
    if (type(document) is not dict or
            set(document) != REVIEWED_BUILDER_PROVENANCE_KEYS):
        fail("reviewed builder provenance field inventory is invalid")
    issued, expires = validate_reviewed_provenance_freshness(document)
    if (document["schema"] != REVIEWED_BUILDER_PROVENANCE_SCHEMA or
            document["decision"] != "GO"):
        fail("reviewed builder provenance does not contain a formal GO decision")
    if (type(document["image_id"]) is not str or
            CANONICAL_SHA256.fullmatch(document["image_id"]) is None):
        fail("reviewed builder provenance image ID is not canonical")
    if (type(document["repo_digest"]) is not str or
            PINNED_IMAGE.fullmatch(document["repo_digest"]) is None):
        fail("reviewed builder provenance RepoDigest is not canonical")
    for field in ("config_sha256", "buildx_binary_sha256"):
        if (type(document[field]) is not str or
                CANONICAL_SHA256.fullmatch(document[field]) is None):
            fail(f"reviewed builder provenance {field} is not canonical")
    if BUILDKIT_VERSION.fullmatch(document["buildkit_version"]) is None:
        fail("reviewed builder provenance BuildKit version is invalid")
    if SEMANTIC_VERSION.fullmatch(document["buildx_version"]) is None:
        fail("reviewed builder provenance buildx version is invalid")
    if SEMANTIC_VERSION.fullmatch(document["docker_server_version"]) is None:
        fail("reviewed builder provenance Docker server version is invalid")
    if DOCKER_API_VERSION.fullmatch(
            document["docker_server_api_version"]) is None:
        fail("reviewed builder provenance Docker API version is invalid")
    if SAFE_BUILD_ID.fullmatch(document["docker_server_git_commit"]) is None:
        fail("reviewed builder provenance Docker GitCommit is invalid")
    return ReviewedBuilderProvenance(
        document_sha256=expected_sha256,
        image_id=document["image_id"],
        repo_digest=document["repo_digest"],
        config_sha256=document["config_sha256"],
        buildkit_version=document["buildkit_version"],
        buildx_version=document["buildx_version"],
        buildx_binary_sha256=document["buildx_binary_sha256"],
        docker_server_version=document["docker_server_version"],
        docker_server_api_version=document["docker_server_api_version"],
        docker_server_git_commit=document["docker_server_git_commit"],
        issued_at_ms=issued, expires_at_ms=expires,
    )


def reviewed_builder_provenance_from_arguments(
        path: Optional[Path],
        expected_sha256: Optional[str],
        expected_buildx_binary_sha256: Optional[str],
        *,
        allow_candidate: bool,
) -> Optional[ReviewedBuilderProvenance]:
    if type(allow_candidate) is not bool:
        fail("candidate builder opt-in must be an explicit boolean")
    if (
            type(expected_buildx_binary_sha256) is not str or
            CANONICAL_SHA256.fullmatch(
                expected_buildx_binary_sha256) is None):
        fail(
            "--buildx-binary-sha256 must be "
            "sha256:<64 lowercase hex>")
    if (path is None) != (expected_sha256 is None):
        fail(
            "--reviewed-builder-provenance and "
            "--reviewed-builder-provenance-sha256 must be supplied together")
    if path is None:
        if not allow_candidate:
            fail(
                "a digest-pinned external reviewed isolated-builder "
                "provenance GO is required")
        return None
    if not isinstance(path, Path):
        fail("--reviewed-builder-provenance must be a filesystem path")
    if expected_sha256 is None:
        fail("reviewed builder provenance digest binding is missing")
    provenance = load_reviewed_builder_provenance(path, expected_sha256)
    if provenance.buildx_binary_sha256 != expected_buildx_binary_sha256:
        fail(
            "reviewed builder provenance does not bind the configured "
            "buildx binary digest")
    return provenance


def builder_execution_record(
        reviewed_provenance: Optional[ReviewedBuilderProvenance],
        *,
        allow_candidate: bool,
) -> dict[str, Any]:
    if type(allow_candidate) is not bool:
        fail("candidate builder opt-in must be an explicit boolean")
    if reviewed_provenance is None and not allow_candidate:
        fail(
            "formal execution requires reviewed isolated-builder "
            "provenance")
    if reviewed_provenance is not None and allow_candidate:
        fail("reviewed builder provenance cannot be downgraded to candidate")
    return {
        "mode": (
            "reviewed-isolated-buildx"
            if reviewed_provenance is not None
            else "explicit-isolated-buildx-candidate"
        ),
        "isolated": True,
        "cache_reuse": "disabled",
        "builder_cache_cleanup": "pending",
        "preloaded_image_only": True,
        "reviewed_provenance": (
            reviewed_provenance.report_record()
            if reviewed_provenance is not None else None
        ),
        "production_eligible": reviewed_provenance is not None,
    }


def load_reviewed_apparmor_provenance(
        path: Path,
        expected_sha256: str,
) -> ReviewedAppArmorProvenance:
    if (type(expected_sha256) is not str or
            CANONICAL_SHA256.fullmatch(expected_sha256) is None):
        fail(
            "--apparmor-provenance-sha256 must be "
            "sha256:<64 lowercase hex>")
    _metadata, contents, observed_digest = read_regular_file(
        path, maximum=MAX_REVIEWED_APPARMOR_PROVENANCE_BYTES)
    if expected_sha256 != "sha256:" + observed_digest:
        fail("AppArmor provenance document digest mismatch")
    try:
        document = json.loads(
            contents.decode("utf-8", errors="strict"),
            object_pairs_hook=reject_duplicate_json_keys,
        )
    except GateError:
        raise
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise GateError(
            "AppArmor provenance document is invalid JSON") from error
    if (type(document) is not dict or
            set(document) != REVIEWED_APPARMOR_PROVENANCE_KEYS):
        fail("AppArmor provenance document field inventory is invalid")
    issued, expires = validate_reviewed_provenance_freshness(document)
    if (document["schema"] != REVIEWED_APPARMOR_PROVENANCE_SCHEMA or
            document["decision"] != "GO"):
        fail("AppArmor provenance does not contain a formal GO decision")
    if document["profile"] != APPARMOR_PROFILE:
        fail("AppArmor provenance profile identity is invalid")
    for field in (
            "policy_source_sha256", "profile_sha256", "raw_sha256"):
        if (type(document[field]) is not str or
                CANONICAL_SHA256.fullmatch(document[field]) is None):
            fail(f"AppArmor provenance {field} is not canonical")
    if APPARMOR_RAW_ABI.fullmatch(document["raw_abi"]) is None:
        fail("AppArmor provenance raw ABI is not canonical")
    return ReviewedAppArmorProvenance(
        document_sha256=expected_sha256,
        profile=document["profile"],
        policy_source_sha256=document["policy_source_sha256"],
        profile_sha256=document["profile_sha256"],
        raw_sha256=document["raw_sha256"],
        raw_abi=document["raw_abi"],
        issued_at_ms=issued, expires_at_ms=expires,
    )


def reviewed_apparmor_provenance_from_arguments(
        path: Optional[Path],
        expected_sha256: Optional[str],
) -> ReviewedAppArmorProvenance:
    if (path is None) != (expected_sha256 is None):
        fail(
            "--apparmor-provenance and "
            "--apparmor-provenance-sha256 must be supplied together")
    if path is None:
        fail(
            "a digest-pinned external AppArmor provenance GO is required")
    if not isinstance(path, Path):
        fail("--apparmor-provenance must be a filesystem path")
    if expected_sha256 is None:
        fail("AppArmor provenance digest binding is missing")
    return load_reviewed_apparmor_provenance(path, expected_sha256)


def load_reviewed_docker_apparmor_namespace_provenance(
        path: Path,
        expected_sha256: str,
) -> ReviewedDockerAppArmorNamespaceProvenance:
    if (type(expected_sha256) is not str or
            CANONICAL_SHA256.fullmatch(expected_sha256) is None):
        fail(
            "--docker-apparmor-namespace-provenance-sha256 must be "
            "sha256:<64 lowercase hex>")
    _metadata, contents, observed_digest = read_regular_file(
        path,
        maximum=MAX_REVIEWED_DOCKER_APPARMOR_NAMESPACE_PROVENANCE_BYTES,
    )
    if expected_sha256 != "sha256:" + observed_digest:
        fail("Docker AppArmor namespace provenance document digest mismatch")
    try:
        document = json.loads(
            contents.decode("utf-8", errors="strict"),
            object_pairs_hook=reject_duplicate_json_keys,
        )
    except GateError:
        raise
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise GateError(
            "Docker AppArmor namespace provenance is invalid JSON") from error
    if (
            type(document) is not dict or
            set(document) !=
                REVIEWED_DOCKER_APPARMOR_NAMESPACE_PROVENANCE_KEYS):
        fail(
            "Docker AppArmor namespace provenance field inventory is invalid")
    issued, expires = validate_reviewed_provenance_freshness(document)
    string_fields = (
        "schema", "decision", "docker_daemon_id", "host_boot_id",
        "host_namespace_name", "daemon_namespace_name",
    )
    integer_fields = (
        "issued_at_ms", "expires_at_ms", "docker_daemon_pid",
        "docker_daemon_start_time_ticks",
        "host_namespace_level", "daemon_namespace_level",
    )
    boolean_fields = (
        "host_namespace_stacked", "daemon_namespace_stacked",
    )
    if (
            any(type(document[field]) is not str for field in string_fields) or
            any(type(document[field]) is not int for field in integer_fields) or
            any(type(document[field]) is not bool for field in boolean_fields)):
        fail("Docker AppArmor namespace provenance field types are invalid")
    if (
            document["schema"] !=
                REVIEWED_DOCKER_APPARMOR_NAMESPACE_PROVENANCE_SCHEMA or
            document["decision"] != "GO"):
        fail(
            "Docker AppArmor namespace provenance lacks a formal GO decision")
    if DOCKER_DAEMON_ID.fullmatch(document["docker_daemon_id"]) is None:
        fail("Docker daemon ID in namespace provenance is invalid")
    if (
            document["docker_daemon_pid"] <= 1 or
            document["docker_daemon_pid"] > 4_194_304 or
            document["docker_daemon_start_time_ticks"] <= 0 or
            BOOT_ID.fullmatch(document["host_boot_id"]) is None):
        fail("Docker daemon process identity in provenance is invalid")
    if (
            document["host_namespace_name"] != "root" or
            document["host_namespace_level"] != 0 or
            document["host_namespace_stacked"] or
            document["daemon_namespace_name"] !=
                document["host_namespace_name"] or
            document["daemon_namespace_level"] !=
                document["host_namespace_level"] or
            document["daemon_namespace_stacked"] !=
                document["host_namespace_stacked"]):
        fail(
            "Docker daemon provenance does not attest the current root "
            "AppArmor namespace")
    return ReviewedDockerAppArmorNamespaceProvenance(
        document_sha256=expected_sha256,
        docker_daemon_id=document["docker_daemon_id"],
        docker_daemon_pid=document["docker_daemon_pid"],
        docker_daemon_start_time_ticks=
            document["docker_daemon_start_time_ticks"],
        host_boot_id=document["host_boot_id"],
        host_namespace_name=document["host_namespace_name"],
        host_namespace_level=document["host_namespace_level"],
        host_namespace_stacked=document["host_namespace_stacked"],
        daemon_namespace_name=document["daemon_namespace_name"],
        daemon_namespace_level=document["daemon_namespace_level"],
        daemon_namespace_stacked=document["daemon_namespace_stacked"],
        issued_at_ms=issued, expires_at_ms=expires,
    )


def reviewed_docker_apparmor_namespace_provenance_from_arguments(
        path: Optional[Path],
        expected_sha256: Optional[str],
) -> ReviewedDockerAppArmorNamespaceProvenance:
    if (path is None) != (expected_sha256 is None):
        fail(
            "--docker-apparmor-namespace-provenance and "
            "--docker-apparmor-namespace-provenance-sha256 must be supplied "
            "together")
    if path is None:
        fail(
            "a digest-pinned Docker daemon AppArmor namespace provenance GO "
            "is required")
    if not isinstance(path, Path):
        fail(
            "--docker-apparmor-namespace-provenance must be a filesystem path")
    if expected_sha256 is None:
        fail("Docker AppArmor namespace provenance digest binding is missing")
    return load_reviewed_docker_apparmor_namespace_provenance(
        path, expected_sha256)


def validate_base_image_record(
        record: dict[str, Any],
        image: str,
        *,
        allow_candidate: bool,
        reviewed_provenance: Optional[ReviewedBaseProvenance] = None,
) -> dict[str, Any]:
    if type(allow_candidate) is not bool:
        fail("candidate base opt-in must be an explicit boolean")
    image = require_pinned_image(image)
    if type(record) is not dict:
        fail("Docker base image inspection record must be an object")
    image_id = record.get("Id")
    os_name = record.get("Os")
    architecture = record.get("Architecture")
    if (type(image_id) is not str or
            CANONICAL_SHA256.fullmatch(image_id) is None or
            type(os_name) is not str or os_name != "linux" or
            type(architecture) is not str or architecture != "amd64"):
        fail("base image must be a canonical Linux amd64 image")

    repo_digests_value = record.get("RepoDigests")
    if (type(repo_digests_value) is not list or
            not repo_digests_value or
            any(type(value) is not str or
                PINNED_IMAGE.fullmatch(value) is None
                for value in repo_digests_value) or
            len(set(repo_digests_value)) != len(repo_digests_value)):
        fail("base image RepoDigests inventory is invalid")
    repo_digests = sorted(repo_digests_value)
    if image not in repo_digests:
        fail("local base image does not attest the exact requested RepoDigest")

    config = record.get("Config")
    if type(config) is not dict:
        fail("base image Config must be an object")
    if "OnBuild" not in config:
        fail("base image Config is missing the OnBuild inventory")
    on_build = config["OnBuild"]
    if on_build is not None and (
            type(on_build) is not list or on_build):
        fail("base image must not contain inherited ONBUILD instructions")
    volumes = config.get("Volumes")
    if volumes is not None and (
            type(volumes) is not dict or volumes):
        fail("base image must not declare inherited volumes")
    labels = config.get("Labels")
    if (type(labels) is not dict or
            any(type(key) is not str or type(value) is not str
                for key, value in labels.items())):
        fail("base image Labels must be a string-to-string object")

    reviewed_label_inventory = set(REVIEWED_BASE_LABELS)
    if set(labels) == reviewed_label_inventory:
        if labels != REVIEWED_BASE_LABELS:
            fail("reviewed base image label values are invalid")
        if type(reviewed_provenance) is not ReviewedBaseProvenance:
            fail(
                "reviewed base image requires digest-pinned external "
                "provenance")
        if (CANONICAL_SHA256.fullmatch(
                reviewed_provenance.document_sha256) is None or
                reviewed_provenance.image_id != image_id or
                reviewed_provenance.repo_digest != image or
                reviewed_provenance.labels_sha256 !=
                canonical_base_labels_sha256(labels)):
            fail("reviewed base provenance does not bind the inspected image")
        base_class = "reviewed-offline-ready"
        production_approved = True
        production_status = "external-reviewed-go"
        provenance_record: Optional[dict[str, str]] = (
            reviewed_provenance.report_record())
    elif set(labels) == CANDIDATE_BASE_LABEL_KEYS:
        if not allow_candidate:
            fail(
                "candidate base image requires explicit "
                "--allow-candidate-base")
        if reviewed_provenance is not None:
            fail("reviewed GO provenance cannot be applied to a candidate base")
        if (
                labels[
                    "org.trillionnium.root-linux.builder-contract"] !=
                "bookworm-content-addressed-candidate-v1" or
                labels[
                    "org.trillionnium.root-linux.production-approved"] !=
                "false" or
                CANONICAL_SHA256.fullmatch(labels[
                    "org.trillionnium.root-linux.base-manifest"]) is None):
            fail("candidate base image label values are invalid")
        base_class = "explicit-development-candidate"
        production_approved = False
        production_status = "non-production-candidate"
        provenance_record = None
    else:
        fail("base image label inventory is not exactly allowed")
    return {
        "reference": image,
        "id": image_id,
        "repo_digests": repo_digests,
        "os": "linux",
        "architecture": "amd64",
        "declared_volumes": 0,
        "base_class": base_class,
        "production_approved": production_approved,
        "production_status": production_status,
        "reviewed_provenance": provenance_record,
    }


def ensure_base_image(
        image: str,
        *,
        allow_candidate: bool,
        reviewed_provenance: Optional[ReviewedBaseProvenance] = None,
) -> dict[str, Any]:
    inspected = command(docker_cli("image", "inspect", image), timeout=30)
    try:
        records = json.loads(
            inspected.stdout, object_pairs_hook=reject_duplicate_json_keys)
    except json.JSONDecodeError as error:
        raise GateError("Docker base image inspection is invalid JSON") from error
    if type(records) is not list or len(records) != 1:
        fail("Docker base image inspection cardinality mismatch")
    return validate_base_image_record(
        records[0], image, allow_candidate=allow_candidate,
        reviewed_provenance=reviewed_provenance)


def canonical_json_sha256(value: Any) -> str:
    try:
        encoded = json.dumps(
            value, sort_keys=True, separators=(",", ":"),
            ensure_ascii=True, allow_nan=False).encode("ascii")
    except (TypeError, ValueError, UnicodeEncodeError) as error:
        raise GateError("value is not canonical JSON") from error
    return "sha256:" + hashlib.sha256(encoded).hexdigest()


def validate_buildkit_image_record(
        record: dict[str, Any],
        image: str,
        *,
        reviewed_provenance: Optional[ReviewedBuilderProvenance],
        allow_candidate: bool,
) -> dict[str, Any]:
    if type(allow_candidate) is not bool:
        fail("candidate builder opt-in must be an explicit boolean")
    image = require_pinned_image(image)
    if type(record) is not dict:
        fail("BuildKit image inspection record must be an object")
    image_id = record.get("Id")
    if (
            type(image_id) is not str or
            CANONICAL_SHA256.fullmatch(image_id) is None or
            record.get("Os") != "linux" or
            record.get("Architecture") != "amd64"):
        fail("BuildKit image must be a canonical Linux amd64 image")
    repo_digests = record.get("RepoDigests")
    if (
            type(repo_digests) is not list or not repo_digests or
            any(type(value) is not str or
                PINNED_IMAGE.fullmatch(value) is None
                for value in repo_digests) or
            len(repo_digests) != len(set(repo_digests)) or
            image not in repo_digests):
        fail("BuildKit image does not attest the exact requested RepoDigest")
    config = record.get("Config")
    if type(config) is not dict or "OnBuild" not in config:
        fail("BuildKit image Config or OnBuild inventory is invalid")
    if config["OnBuild"] is not None and (
            type(config["OnBuild"]) is not list or config["OnBuild"]):
        fail("BuildKit image must not contain inherited ONBUILD instructions")
    volumes = config.get("Volumes")
    if volumes is not None and (
            type(volumes) is not dict or volumes):
        fail("BuildKit image must not declare inherited volumes")
    exposed_ports = config.get("ExposedPorts")
    if exposed_ports is not None and (
            type(exposed_ports) is not dict or exposed_ports):
        fail("BuildKit image must not declare exposed ports")
    entrypoint = config.get("Entrypoint")
    if (
            type(entrypoint) is not list or len(entrypoint) != 1 or
            entrypoint[0] not in {
                "buildkitd", "/usr/bin/buildkitd",
                "/usr/local/bin/buildkitd",
            }):
        fail("BuildKit image entrypoint is not the reviewed daemon")
    labels = config.get("Labels")
    if labels is None:
        labels = {}
    if (
            type(labels) is not dict or
            any(type(key) is not str or type(value) is not str
                for key, value in labels.items())):
        fail("BuildKit image labels must be a string-to-string object")
    reserved = {
        "io.hepta.purpose", ROLE_LABEL_KEY, RUN_ID_LABEL_KEY,
        BUILDKIT_IMAGE_ID_LABEL_KEY, BUILDX_BUILDER_LABEL_KEY,
    }
    if reserved.intersection(labels):
        fail("BuildKit image labels collide with gate ownership labels")
    config_sha256 = canonical_json_sha256(config)
    if reviewed_provenance is None:
        if not allow_candidate:
            fail("reviewed BuildKit image provenance GO is required")
        production_status = "non-production-candidate"
    else:
        if allow_candidate:
            fail("reviewed BuildKit image cannot run in candidate mode")
        if (
                reviewed_provenance.image_id != image_id or
                reviewed_provenance.repo_digest != image or
                reviewed_provenance.config_sha256 != config_sha256):
            fail(
                "reviewed builder provenance does not bind the inspected "
                "BuildKit image")
        production_status = "external-reviewed-go"
    return {
        "reference": image,
        "id": image_id,
        "bare_id": image_id.removeprefix("sha256:"),
        "repo_digests": sorted(repo_digests),
        "config_sha256": config_sha256,
        "config_labels": dict(labels),
        "entrypoint": list(entrypoint),
        "production_status": production_status,
        "production_approved": reviewed_provenance is not None,
    }


def ensure_buildkit_image(
        image: str,
        *,
        reviewed_provenance: Optional[ReviewedBuilderProvenance],
        allow_candidate: bool,
) -> dict[str, Any]:
    inspected = command(docker_cli("image", "inspect", image), timeout=30)
    try:
        records = json.loads(
            inspected.stdout, object_pairs_hook=reject_duplicate_json_keys)
    except json.JSONDecodeError as error:
        raise GateError(
            "Docker BuildKit image inspection is invalid JSON") from error
    if type(records) is not list or len(records) != 1:
        fail("Docker BuildKit image inspection cardinality mismatch")
    return validate_buildkit_image_record(
        records[0],
        image,
        reviewed_provenance=reviewed_provenance,
        allow_candidate=allow_candidate,
    )


def validate_buildx_toolchain(
        plugin_records: Any,
        version_output: str,
        server_record: Any,
        expected_binary_sha256: str,
        reviewed_provenance: Optional[ReviewedBuilderProvenance],
) -> dict[str, Any]:
    if (
            type(expected_binary_sha256) is not str or
            CANONICAL_SHA256.fullmatch(expected_binary_sha256) is None):
        fail("configured buildx binary digest is not canonical")
    if type(plugin_records) is not list:
        fail("Docker CLI plugin inventory is invalid")
    matches = [
        record for record in plugin_records
        if type(record) is dict and record.get("Name") == "buildx"
    ]
    if len(matches) != 1:
        fail("Docker CLI must expose exactly one buildx plugin")
    plugin = matches[0]
    path_value = plugin.get("Path")
    version = plugin.get("Version")
    if (
            type(path_value) is not str or not path_value.startswith("/") or
            type(version) is not str or
            SEMANTIC_VERSION.fullmatch(version) is None):
        fail("buildx plugin path or version is invalid")
    path = Path(path_value)
    metadata, _contents, observed_digest = read_regular_file(
        path, maximum=256 * 1024 * 1024, executable=True)
    observed_sha256 = "sha256:" + observed_digest
    if (
            stat.S_ISLNK(metadata.st_mode) or metadata.st_nlink != 1 or
            metadata.st_uid != 0 or metadata.st_gid != 0 or
            metadata.st_mode & (stat.S_IWGRP | stat.S_IWOTH)):
        fail("buildx plugin binary ownership or mode is unsafe")
    if observed_sha256 != expected_binary_sha256:
        fail("buildx plugin binary digest mismatch")
    match = re.fullmatch(
        r"github\.com/docker/buildx ([0-9]+\.[0-9]+\.[0-9]+)"
        r"(?: [^\r\n]+)?\n?",
        version_output,
    )
    if match is None or match.group(1) != version:
        fail("buildx command version does not bind the selected plugin")
    if type(server_record) is not dict:
        fail("Docker server version record is invalid")
    server_version = server_record.get("Version")
    server_api = server_record.get("ApiVersion")
    server_git_commit = server_record.get("GitCommit")
    if (
            type(server_version) is not str or
            SEMANTIC_VERSION.fullmatch(server_version) is None or
            type(server_api) is not str or
            DOCKER_API_VERSION.fullmatch(server_api) is None or
            type(server_git_commit) is not str or
            SAFE_BUILD_ID.fullmatch(server_git_commit) is None):
        fail("Docker server version binding is invalid")
    if reviewed_provenance is not None and (
            reviewed_provenance.buildx_version != version or
            reviewed_provenance.buildx_binary_sha256 != observed_sha256 or
            reviewed_provenance.docker_server_version != server_version or
            reviewed_provenance.docker_server_api_version != server_api or
            reviewed_provenance.docker_server_git_commit !=
                server_git_commit):
        fail(
            "reviewed builder provenance does not bind the active "
            "buildx/Docker toolchain")
    return {
        "buildx_path": str(path),
        "buildx_version": version,
        "buildx_binary_sha256": observed_sha256,
        "docker_server_version": server_version,
        "docker_server_api_version": server_api,
        "docker_server_git_commit": server_git_commit,
        "reviewed": reviewed_provenance is not None,
    }


def inspect_buildx_toolchain(
        expected_binary_sha256: str,
        reviewed_provenance: Optional[ReviewedBuilderProvenance],
) -> dict[str, Any]:
    plugins_output = command(docker_cli(
        "info", "--format", "{{json .ClientInfo.Plugins}}"), timeout=30).stdout
    server_output = command(docker_cli(
        "version", "--format", "{{json .Server}}"), timeout=30).stdout
    version_output = command(docker_cli("buildx", "version"), timeout=30).stdout
    try:
        plugins = json.loads(
            plugins_output, object_pairs_hook=reject_duplicate_json_keys)
        server = json.loads(
            server_output, object_pairs_hook=reject_duplicate_json_keys)
    except json.JSONDecodeError as error:
        raise GateError(
            "Docker/buildx toolchain inspection is invalid JSON") from error
    return validate_buildx_toolchain(
        plugins, version_output, server, expected_binary_sha256,
        reviewed_provenance)


def require_run_id(run_id: str) -> str:
    if type(run_id) is not str or re.fullmatch(r"[0-9a-f]{32}", run_id) is None:
        fail("disposable Docker run ID is not canonical")
    return run_id


def isolated_builder_names(run_id: str) -> dict[str, str]:
    run_id = require_run_id(run_id)
    builder = f"hepta-isolated-{run_id}"
    node = f"{builder}0"
    container = f"buildx_buildkit_{node}"
    return {
        "builder": builder,
        "node": node,
        "container": container,
        "volume": container + "_state",
    }


def isolated_builder_labels(
        run_id: str,
        builder_name: str,
        buildkit_image_id: str,
        *,
        role: str,
) -> dict[str, str]:
    names = isolated_builder_names(run_id)
    if builder_name != names["builder"]:
        fail("isolated builder ownership name is invalid")
    if (
            type(buildkit_image_id) is not str or
            CANONICAL_SHA256.fullmatch(buildkit_image_id) is None):
        fail("isolated builder image ID is invalid")
    if role not in {BUILDER_DAEMON_ROLE, BUILDER_STATE_ROLE}:
        fail("isolated builder ownership role is invalid")
    return {
        "io.hepta.purpose": PURPOSE,
        ROLE_LABEL_KEY: role,
        RUN_ID_LABEL_KEY: run_id,
        BUILDKIT_IMAGE_ID_LABEL_KEY: buildkit_image_id,
        BUILDX_BUILDER_LABEL_KEY: builder_name,
    }


def docker_builder_volume_create_arguments(
        names: dict[str, str],
        run_id: str,
        buildkit_image_id: str,
) -> list[str]:
    if names != isolated_builder_names(run_id):
        fail("isolated builder object names are invalid")
    arguments = docker_cli("volume", "create", "--driver=local")
    labels = isolated_builder_labels(
        run_id, names["builder"], buildkit_image_id,
        role=BUILDER_STATE_ROLE)
    for key, value in labels.items():
        arguments.extend(["--label", f"{key}={value}"])
    arguments.append(names["volume"])
    return arguments


def docker_builder_container_create_arguments(
        names: dict[str, str],
        run_id: str,
        buildkit_image: dict[str, Any],
) -> list[str]:
    if names != isolated_builder_names(run_id):
        fail("isolated builder object names are invalid")
    image_id = buildkit_image.get("id")
    bare_id = buildkit_image.get("bare_id")
    image_labels = buildkit_image.get("config_labels")
    if (
            type(image_id) is not str or
            CANONICAL_SHA256.fullmatch(image_id) is None or
            type(bare_id) is not str or
            BARE_SHA256.fullmatch(bare_id) is None or
            image_id != "sha256:" + bare_id or
            type(image_labels) is not dict):
        fail("isolated builder exact local image record is invalid")
    ownership = isolated_builder_labels(
        run_id, names["builder"], image_id, role=BUILDER_DAEMON_ROLE)
    if set(ownership).intersection(image_labels):
        fail("BuildKit labels collide with isolated builder ownership")
    arguments = docker_cli(
        "container", "create",
        "--pull=never",
        "--network=none",
        "--privileged",
        "--init",
        "--restart=no",
        "--name", names["container"],
        "--mount",
        (
            "type=volume,source=" + names["volume"] +
            ",target=" + BUILDKIT_STATE_DIRECTORY
        ),
    )
    for key, value in ownership.items():
        arguments.extend(["--label", f"{key}={value}"])
    # A bare exact image ID is deliberately not a registry reference. The
    # reviewed buildx/Engine pair must reject it locally on any pull fallback.
    arguments.append(bare_id)
    return arguments


def docker_buildx_create_arguments(
        names: dict[str, str],
        run_id: str,
        buildkit_image_id: str,
) -> list[str]:
    if names != isolated_builder_names(run_id):
        fail("isolated builder object names are invalid")
    if (
            type(buildkit_image_id) is not str or
            CANONICAL_SHA256.fullmatch(buildkit_image_id) is None):
        fail("isolated builder image ID is invalid")
    bare_id = buildkit_image_id.removeprefix("sha256:")
    return docker_cli(
        "buildx", "create",
        "--name", names["builder"],
        "--node", names["node"],
        "--driver", "docker-container",
        "--driver-opt",
        (
            "image=" + bare_id +
            ",network=none,restart-policy=no,default-load=false,"
            "provenance-add-gha=false"
        ),
        "--platform", "linux/amd64",
    )


def validate_builder_volume_record(
        record: Any,
        *,
        names: dict[str, str],
        run_id: str,
        buildkit_image_id: str,
) -> dict[str, Any]:
    if names != isolated_builder_names(run_id) or type(record) is not dict:
        fail("isolated builder volume inspection is invalid")
    expected_labels = isolated_builder_labels(
        run_id, names["builder"], buildkit_image_id,
        role=BUILDER_STATE_ROLE)
    labels = record.get("Labels")
    options = record.get("Options")
    if (
            record.get("Name") != names["volume"] or
            record.get("Driver") != "local" or
            record.get("Scope") != "local" or
            labels != expected_labels or
            options not in (None, {}) or
            type(record.get("Mountpoint")) is not str or
            not record["Mountpoint"].startswith("/")):
        fail("isolated builder volume ownership or driver mismatch")
    return {
        "name": names["volume"],
        "driver": "local",
        "scope": "local",
        "labels": expected_labels,
        "mountpoint_sha256": (
            "sha256:" +
            hashlib.sha256(record["Mountpoint"].encode("utf-8")).hexdigest()
        ),
    }


def validate_builder_container_record(
        record: Any,
        *,
        container_id: str,
        names: dict[str, str],
        run_id: str,
        buildkit_image: dict[str, Any],
        expected_running: Optional[bool],
) -> dict[str, Any]:
    if (
            names != isolated_builder_names(run_id) or
            type(container_id) is not str or
            BARE_SHA256.fullmatch(container_id) is None or
            type(record) is not dict):
        fail("isolated builder container inspection is invalid")
    image_id = buildkit_image.get("id")
    bare_id = buildkit_image.get("bare_id")
    image_labels = buildkit_image.get("config_labels")
    if (
            type(image_id) is not str or
            CANONICAL_SHA256.fullmatch(image_id) is None or
            type(bare_id) is not str or
            type(image_labels) is not dict):
        fail("isolated builder image record is invalid")
    ownership = isolated_builder_labels(
        run_id, names["builder"], image_id, role=BUILDER_DAEMON_ROLE)
    expected_labels = {**image_labels, **ownership}
    host = record.get("HostConfig")
    config = record.get("Config")
    state = record.get("State")
    mounts = record.get("Mounts")
    if (
            type(host) is not dict or type(config) is not dict or
            type(state) is not dict or type(mounts) is not list or
            len(mounts) != 1):
        fail("isolated builder container configuration is invalid")
    if (
            record.get("Id") != container_id or
            record.get("Name") != "/" + names["container"] or
            record.get("Image") != image_id or
            config.get("Image") != bare_id or
            config.get("Labels") != expected_labels):
        fail("isolated builder container identity or ownership mismatch")
    if expected_running is not None and (
            state.get("Running") is not expected_running):
        fail("isolated builder running state mismatch")
    restart = host.get("RestartPolicy")
    if (
            host.get("NetworkMode") != "none" or
            host.get("Privileged") is not True or
            host.get("AutoRemove") is not False or
            host.get("Init") is not True or
            host.get("ReadonlyRootfs") is not False or
            type(restart) is not dict or restart.get("Name") != "no"):
        fail("isolated builder namespace or lifecycle contract mismatch")

    def empty_or_none(value: Any, expected_type: type[Any]) -> bool:
        return value is None or (
            type(value) is expected_type and len(value) == 0)

    if (
            not empty_or_none(host.get("Binds"), list) or
            not empty_or_none(host.get("Tmpfs"), dict) or
            not empty_or_none(host.get("VolumesFrom"), list) or
            not empty_or_none(host.get("Devices"), list) or
            not empty_or_none(host.get("DeviceRequests"), list) or
            not empty_or_none(host.get("PortBindings"), dict) or
            host.get("PublishAllPorts") is not False):
        fail("isolated builder must not have binds, devices, tmpfs, or ports")
    mount = mounts[0]
    if (
            type(mount) is not dict or mount.get("Type") != "volume" or
            mount.get("Name") != names["volume"] or
            mount.get("Destination") != BUILDKIT_STATE_DIRECTORY or
            mount.get("Driver") != "local" or
            mount.get("RW") is not True):
        fail("isolated builder state volume binding mismatch")
    return {
        "container_id": container_id,
        "name": names["container"],
        "image_id": image_id,
        "builder": names["builder"],
        "node": names["node"],
        "state_volume": names["volume"],
        "network_mode": "none",
        "privileged": True,
        "bind_mounts": 0,
        "published_ports": 0,
        "running": state.get("Running"),
        "labels": ownership,
    }


def inspect_builder_volume(
        names: dict[str, str],
        run_id: str,
        buildkit_image_id: str,
) -> dict[str, Any]:
    inspected = command(
        docker_cli("volume", "inspect", names["volume"]), timeout=30)
    try:
        records = json.loads(
            inspected.stdout, object_pairs_hook=reject_duplicate_json_keys)
    except json.JSONDecodeError as error:
        raise GateError(
            "isolated builder volume inspection is invalid JSON") from error
    if type(records) is not list or len(records) != 1:
        fail("isolated builder volume inspection cardinality mismatch")
    return validate_builder_volume_record(
        records[0],
        names=names,
        run_id=run_id,
        buildkit_image_id=buildkit_image_id,
    )


def inspect_builder_container(
        container_id: str,
        names: dict[str, str],
        run_id: str,
        buildkit_image: dict[str, Any],
        *,
        expected_running: Optional[bool],
) -> dict[str, Any]:
    inspected = command(
        docker_cli("container", "inspect", container_id), timeout=30)
    try:
        records = json.loads(
            inspected.stdout, object_pairs_hook=reject_duplicate_json_keys)
    except json.JSONDecodeError as error:
        raise GateError(
            "isolated builder container inspection is invalid JSON") from error
    if type(records) is not list or len(records) != 1:
        fail("isolated builder container inspection cardinality mismatch")
    return validate_builder_container_record(
        records[0],
        container_id=container_id,
        names=names,
        run_id=run_id,
        buildkit_image=buildkit_image,
        expected_running=expected_running,
    )


def builder_metadata_path(builder_name: str) -> Path:
    if (
            _DOCKER_CONFIG is None or type(builder_name) is not str or
            re.fullmatch(r"hepta-isolated-[0-9a-f]{32}", builder_name) is None):
        fail("isolated buildx metadata identity is invalid")
    return Path(_DOCKER_CONFIG.name) / "buildx" / "instances" / builder_name


def require_builder_metadata_absent(builder_name: str) -> None:
    path = builder_metadata_path(builder_name)
    if os.path.lexists(path):
        fail("isolated buildx builder metadata residue remains")


def require_builder_volume_absent(volume_name: str) -> None:
    inspected = command(
        docker_cli("volume", "inspect", volume_name),
        check=False,
        timeout=20,
    )
    if inspected.returncode == 0:
        fail(f"refusing to reuse existing Docker volume: {volume_name}")
    expected_patterns = (
        rf"^\[\]\nError response from daemon: get "
        rf"{re.escape(volume_name)}: no such volume\n$",
        rf"^\[\]\nError response from daemon: no such volume: "
        rf"{re.escape(volume_name)}\n$",
    )
    if (
            inspected.returncode != 1 or
            not any(re.fullmatch(pattern, inspected.stdout)
                    for pattern in expected_patterns)):
        fail(f"could not prove Docker volume absence: {volume_name}")


def validate_buildx_runtime_record(
        output: str,
        *,
        names: dict[str, str],
        reviewed_provenance: Optional[ReviewedBuilderProvenance],
) -> dict[str, Any]:
    records: list[Any] = []
    for line in output.splitlines():
        if not line:
            continue
        try:
            records.append(json.loads(
                line, object_pairs_hook=reject_duplicate_json_keys))
        except json.JSONDecodeError as error:
            raise GateError(
                "buildx builder inventory is invalid JSON") from error
    matches = [
        record for record in records
        if type(record) is dict and record.get("Name") == names["builder"]
    ]
    if len(matches) != 1:
        fail("buildx isolated builder inventory cardinality mismatch")
    record = matches[0]
    nodes = record.get("Nodes")
    if (
            record.get("Driver") != "docker-container" or
            type(nodes) is not list or len(nodes) != 1 or
            type(nodes[0]) is not dict or
            nodes[0].get("Name") != names["node"] or
            nodes[0].get("Status") != "running"):
        fail("buildx isolated builder driver or node identity mismatch")
    buildkit_version = nodes[0].get("Version")
    if (
            type(buildkit_version) is not str or
            BUILDKIT_VERSION.fullmatch(buildkit_version) is None):
        fail("buildx isolated builder BuildKit version is invalid")
    if (
            reviewed_provenance is not None and
            reviewed_provenance.buildkit_version != buildkit_version):
        fail(
            "reviewed builder provenance does not bind the running "
            "BuildKit version")
    return {
        "builder": names["builder"],
        "node": names["node"],
        "driver": "docker-container",
        "status": "running",
        "buildkit_version": buildkit_version,
    }


def buildx_cache_record(builder_name: str) -> dict[str, Any]:
    output = command(docker_cli(
        "buildx", "du",
        "--builder", builder_name,
        "--format", "{{json .}}",
    ), timeout=60).stdout
    record_count = 0
    for line in output.splitlines():
        if not line:
            continue
        try:
            record = json.loads(
                line, object_pairs_hook=reject_duplicate_json_keys)
        except json.JSONDecodeError as error:
            raise GateError(
                "isolated builder cache inventory is invalid JSON") from error
        if type(record) is not dict:
            fail("isolated builder cache inventory record is invalid")
        record_count += 1
    return {
        "record_count": record_count,
        "inventory_sha256": (
            "sha256:" + hashlib.sha256(output.encode("utf-8")).hexdigest()
        ),
    }


def create_isolated_builder(
        names: dict[str, str],
        run_id: str,
        buildkit_image: dict[str, Any],
        reviewed_provenance: Optional[ReviewedBuilderProvenance],
) -> dict[str, Any]:
    require_docker_absent("container", names["container"])
    require_builder_volume_absent(names["volume"])
    require_builder_metadata_absent(names["builder"])
    volume = command(docker_builder_volume_create_arguments(
        names, run_id, buildkit_image["id"]), timeout=30)
    if volume.stdout.strip() != names["volume"]:
        fail("Docker volume create returned an unexpected identity")
    volume_record = inspect_builder_volume(
        names, run_id, buildkit_image["id"])
    created = command(docker_builder_container_create_arguments(
        names, run_id, buildkit_image), check=False, timeout=90)
    if created.returncode != 0:
        fail(
            "preloaded exact BuildKit image disappeared before its "
            "pull-never container was created")
    container_id = created.stdout.strip()
    if BARE_SHA256.fullmatch(container_id) is None:
        fail("isolated builder container create returned an invalid ID")
    stopped_record = inspect_builder_container(
        container_id,
        names,
        run_id,
        buildkit_image,
        expected_running=False,
    )
    created_builder = command(docker_buildx_create_arguments(
        names, run_id, buildkit_image["id"]), timeout=60)
    if created_builder.stdout.strip() != names["builder"]:
        fail("buildx create returned an unexpected builder identity")
    if not builder_metadata_exists(names["builder"]):
        fail("buildx create did not persist private builder metadata")
    command(docker_cli(
        "container", "start", container_id), timeout=90)
    running_record = inspect_builder_container(
        container_id,
        names,
        run_id,
        buildkit_image,
        expected_running=True,
    )
    runtime = validate_buildx_runtime_record(
        command(docker_cli(
            "buildx", "ls", "--format", "{{json .}}"), timeout=60).stdout,
        names=names,
        reviewed_provenance=reviewed_provenance,
    )
    return {
        "names": dict(names),
        "container_id": container_id,
        "volume": volume_record,
        "container_before_start": stopped_record,
        "container_running": running_record,
        "runtime": runtime,
    }


def stop_isolated_builder(
        builder: dict[str, Any],
        run_id: str,
        buildkit_image: dict[str, Any],
) -> dict[str, Any]:
    names = builder["names"]
    container_id = builder["container_id"]
    inspect_builder_container(
        container_id, names, run_id, buildkit_image,
        expected_running=True)
    command(docker_cli(
        "container", "stop", "--time", "10", container_id), timeout=45)
    return inspect_builder_container(
        container_id, names, run_id, buildkit_image,
        expected_running=False)


def base_holder_labels(run_id: str) -> dict[str, str]:
    run_id = require_run_id(run_id)
    return {
        "io.hepta.purpose": PURPOSE,
        ROLE_LABEL_KEY: BASE_HOLDER_ROLE,
        RUN_ID_LABEL_KEY: run_id,
    }


def built_image_labels(
        run_id: str,
        base_image_id: str,
        rootfs_sha256: str,
) -> dict[str, str]:
    run_id = require_run_id(run_id)
    if (type(base_image_id) is not str or
            CANONICAL_SHA256.fullmatch(base_image_id) is None):
        fail("built image source image ID is not canonical")
    if (type(rootfs_sha256) is not str or
            CANONICAL_SHA256.fullmatch(rootfs_sha256) is None):
        fail("built image base rootfs digest is not canonical")
    return {
        "io.hepta.purpose": PURPOSE,
        ROLE_LABEL_KEY: BUILT_IMAGE_ROLE,
        RUN_ID_LABEL_KEY: run_id,
        BASE_IMAGE_ID_LABEL_KEY: base_image_id,
        BASE_ROOTFS_SHA256_LABEL_KEY: rootfs_sha256,
        BASE_CONSTRUCTION_LABEL_KEY: BASE_CONSTRUCTION_VERSION,
    }


def docker_base_holder_create_arguments(
        image_id: str,
        name: str,
        run_id: str,
) -> list[str]:
    if (type(image_id) is not str or
            CANONICAL_SHA256.fullmatch(image_id) is None):
        fail("base holder requires the exact inspected image ID")
    if (
            type(name) is not str or
            re.fullmatch(
                r"hepta-agent-os-base-rootfs-[0-9a-f]{32}", name) is None):
        fail("base holder name is not owned by this gate")
    labels = base_holder_labels(run_id)
    arguments = docker_cli(
        "container", "create",
        "--pull=never",
        "--network=none",
        "--read-only",
        "--name", name,
    )
    for key, value in labels.items():
        arguments.extend(["--label", f"{key}={value}"])
    arguments.extend(["--entrypoint=/bin/true", image_id])
    return arguments


def validate_base_holder_inspect_record(
        record: dict[str, Any],
        *,
        container_id: str,
        name: str,
        image_id: str,
        run_id: str,
) -> dict[str, Any]:
    if (
            type(container_id) is not str or
            re.fullmatch(r"[0-9a-f]{64}", container_id) is None or
            type(image_id) is not str or
            CANONICAL_SHA256.fullmatch(image_id) is None):
        fail("base holder expected identity is not canonical")
    expected_labels = base_holder_labels(run_id)
    if type(record) is not dict:
        fail("base holder inspection record must be an object")
    host = record.get("HostConfig")
    config = record.get("Config")
    mounts = record.get("Mounts")
    if type(host) is not dict or type(config) is not dict:
        fail("base holder inspection configuration is invalid")
    if (
            record.get("Id") != container_id or
            record.get("Name") != "/" + name or
            record.get("Image") != image_id or
            config.get("Image") != image_id):
        fail("base holder exact image or container identity mismatch")
    if (
            host.get("NetworkMode") != "none" or
            host.get("ReadonlyRootfs") is not True or
            host.get("Privileged") is not False):
        fail("base holder namespace or privilege contract mismatch")

    def empty_or_none(value: Any, allowed_type: type[Any]) -> bool:
        return value is None or (
            type(value) is allowed_type and len(value) == 0)

    if (
            not empty_or_none(host.get("Binds"), list) or
            not empty_or_none(host.get("Tmpfs"), dict) or
            not empty_or_none(host.get("VolumesFrom"), list) or
            not empty_or_none(host.get("Devices"), list) or
            not empty_or_none(host.get("DeviceRequests"), list) or
            not empty_or_none(host.get("PortBindings"), dict) or
            host.get("PublishAllPorts") is not False or
            type(mounts) is not list or mounts):
        fail("base holder must not have mounts, volumes, devices, or ports")
    volumes = config.get("Volumes")
    if not empty_or_none(volumes, dict):
        fail("base holder inherited an undeclared volume")
    labels = config.get("Labels")
    if (
            type(labels) is not dict or
            any(type(key) is not str or type(value) is not str
                for key, value in labels.items()) or
            any(labels.get(key) != value
                for key, value in expected_labels.items())):
        fail("base holder ownership labels mismatch")
    if config.get("Entrypoint") != ["/bin/true"]:
        fail("base holder entrypoint contract mismatch")
    return {
        "container_id": container_id,
        "name": name,
        "image_id": image_id,
        "purpose": PURPOSE,
        "role": BASE_HOLDER_ROLE,
        "run_id": run_id,
        "network_mode": "none",
        "read_only_rootfs": True,
        "mounts": 0,
        "volumes": 0,
    }


def inspect_base_holder(
        container_id: str,
        name: str,
        image_id: str,
        run_id: str,
) -> dict[str, Any]:
    inspected = command(
        docker_cli("container", "inspect", container_id), timeout=30)
    try:
        records = json.loads(
            inspected.stdout, object_pairs_hook=reject_duplicate_json_keys)
    except json.JSONDecodeError as error:
        raise GateError("base holder inspection is invalid JSON") from error
    if type(records) is not list or len(records) != 1:
        fail("base holder inspection cardinality mismatch")
    return validate_base_holder_inspect_record(
        records[0],
        container_id=container_id,
        name=name,
        image_id=image_id,
        run_id=run_id,
    )


def create_base_holder(
        image_id: str,
        name: str,
        run_id: str,
) -> tuple[str, dict[str, Any]]:
    created = command(
        docker_base_holder_create_arguments(image_id, name, run_id),
        check=False,
        timeout=90,
    )
    if created.returncode != 0:
        fail(
            "exact inspected base image disappeared before its local "
            "pull-never holder was created")
    container_id = created.stdout.strip()
    if re.fullmatch(r"[0-9a-f]{64}", container_id) is None:
        fail("Docker base holder creation returned a non-canonical ID")
    return container_id, inspect_base_holder(
        container_id, name, image_id, run_id)


def docker_base_export_arguments(container_id: str) -> list[str]:
    if (type(container_id) is not str or
            re.fullmatch(r"[0-9a-f]{64}", container_id) is None):
        fail("base holder export requires a canonical container ID")
    return docker_cli("container", "export", container_id)


def export_base_rootfs(
        container_id: str,
        destination: Path,
) -> dict[str, Any]:
    return stream_docker_export(
        docker_base_export_arguments(container_id),
        destination,
        maximum=MAX_BASE_ROOTFS_TAR_BYTES,
        timeout=900,
    )


def require_exact_image_id_present(image_id: str) -> None:
    if (type(image_id) is not str or
            CANONICAL_SHA256.fullmatch(image_id) is None):
        fail("exact base image presence check requires a canonical ID")
    inspected = command(
        docker_cli("image", "inspect", image_id), check=False, timeout=30)
    if inspected.returncode != 0:
        fail("exact inspected base image disappeared during local construction")
    try:
        records = json.loads(
            inspected.stdout, object_pairs_hook=reject_duplicate_json_keys)
    except json.JSONDecodeError as error:
        raise GateError(
            "exact base image presence inspection is invalid JSON") from error
    if (
            type(records) is not list or len(records) != 1 or
            type(records[0]) is not dict or
            records[0].get("Id") != image_id):
        fail("exact inspected base image identity changed during construction")


def docker_build_arguments(
        context: Path,
        tag: str,
        base_reference: str,
        base_image_id: str,
        rootfs_sha256: str,
        run_id: str,
        builder_name: str,
) -> list[str]:
    base_reference = require_pinned_image(base_reference)
    if builder_name != isolated_builder_names(run_id)["builder"]:
        fail("Docker build requires the exact isolated builder")
    labels = built_image_labels(run_id, base_image_id, rootfs_sha256)
    arguments = docker_cli(
        "buildx", "build",
        "--builder", builder_name,
        "--load",
        "--network=none",
        "--no-cache",
        "--platform", "linux/amd64",
    )
    for key, value in labels.items():
        arguments.extend(["--label", f"{key}={value}"])
    arguments.extend([
        "--build-arg", f"BASE_IMAGE={base_reference}",
        "--file",
        str(context / "tests/agent_os_rootful_systemd/Dockerfile"),
        "--tag", tag,
        str(context),
    ])
    return arguments


def validate_owned_built_image_record(
        record: dict[str, Any],
        *,
        tag: str,
        expected_labels: dict[str, str],
        expected_id: Optional[str] = None,
) -> dict[str, Any]:
    if type(expected_labels) is not dict:
        fail("built image ownership label attestation is invalid")
    run_id = expected_labels.get(RUN_ID_LABEL_KEY)
    if (
            type(tag) is not str or type(run_id) is not str or
            tag != f"hepta/agent-os-rootful-e2e:{run_id}" or
            type(record) is not dict):
        fail("built image inspection record must be an object")
    image_id = record.get("Id")
    config = record.get("Config")
    if (
            type(image_id) is not str or
            CANONICAL_SHA256.fullmatch(image_id) is None or
            (expected_id is not None and image_id != expected_id) or
            type(config) is not dict or
            config.get("Labels") != expected_labels):
        fail("built image source and ownership binding mismatch")
    if record.get("RepoTags") != [tag] or record.get("RepoDigests") != []:
        fail("built image has unexpected tag or RepoDigest residue")
    return {
        "id": image_id,
        "labels": expected_labels,
        "repo_tags": [tag],
        "repo_digests": [],
    }


def validate_built_image_record(
        record: dict[str, Any],
        *,
        tag: str,
        base_image_id: str,
        rootfs_sha256: str,
        run_id: str,
) -> dict[str, Any]:
    return validate_owned_built_image_record(
        record,
        tag=tag,
        expected_labels=built_image_labels(
            run_id, base_image_id, rootfs_sha256),
    )


def docker_run_arguments(image_id: str, name: str, run_id: str) -> list[str]:
    arguments = docker_cli(
        "run", "--detach", "--rm", "--pull=never",
        "--name", name,
        "--label", PURPOSE_LABEL,
        "--hostname", "hepta-agent-os-e2e",
        "--network=none",
        "--cgroupns=private",
        "--ipc=private",
        "--read-only",
    )
    for destination, options in RUNTIME_TMPFS.items():
        arguments.extend(["--tmpfs", f"{destination}:{options}"])
    arguments.extend([
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
        "--env", "HEPTA_AGENT_OS_E2E_DISPOSABLE=1",
        "--env", f"HEPTA_AGENT_OS_E2E_RUN_ID={run_id}",
        image_id,
    ])
    return arguments


def validate_container_inspect_record(
        inspected: dict[str, Any],
        *,
        container_id: str,
        name: str,
        image_id: str,
        run_id: str,
) -> dict[str, Any]:
    host = inspected.get("HostConfig") or {}
    config = inspected.get("Config") or {}
    if (inspected.get("Id") != container_id or
            inspected.get("Name") != "/" + name or
            inspected.get("Image") != image_id):
        fail("disposable container identity mismatch")
    if host.get("Privileged") is not False:
        fail("disposable container must not be privileged")
    if host.get("ReadonlyRootfs") is not True:
        fail("disposable container rootfs must be read-only")
    if host.get("NetworkMode") != "none":
        fail("disposable container must use network=none")
    if host.get("Binds"):
        fail("disposable container must not have bind mounts")
    if host.get("PortBindings") or host.get("PublishAllPorts") is not False:
        fail("disposable container must not publish ports")
    if host.get("PidMode") != "" or host.get("IpcMode") != "private":
        fail("disposable container namespace contract mismatch")
    if host.get("CgroupnsMode") != "private":
        fail("disposable container must use a private cgroup namespace")
    if set((host.get("Tmpfs") or {}).keys()) != set(RUNTIME_TMPFS):
        fail("disposable container tmpfs allowlist mismatch")
    if any(
            (host.get("Tmpfs") or {}).get(path) != options
            for path, options in RUNTIME_TMPFS.items()):
        fail("disposable container tmpfs options mismatch")
    mounts = inspected.get("Mounts") or []
    if any(
            mount.get("Type") != "tmpfs" or
            mount.get("Destination") not in RUNTIME_TMPFS
            for mount in mounts):
        fail("disposable container has a host-backed or unexpected mount")
    if set(host.get("CapDrop") or []) != {"ALL"}:
        fail("disposable container capability drop contract mismatch")
    if set(host.get("CapAdd") or []) != RUNTIME_CAPABILITIES:
        fail("disposable container capability allowlist mismatch")
    security_options = set(host.get("SecurityOpt") or [])
    no_new_privileges = security_options & {
        "no-new-privileges", "no-new-privileges=true"}
    if len(no_new_privileges) != 1:
        fail("disposable container security options mismatch")
    if security_options != {
            next(iter(no_new_privileges)),
            f"apparmor={APPARMOR_PROFILE}"}:
        fail("disposable container security options mismatch")
    if inspected.get("AppArmorProfile") != APPARMOR_PROFILE:
        fail("disposable container AppArmor profile mismatch")
    if (host.get("Devices") or host.get("DeviceRequests") or
            host.get("VolumesFrom") or host.get("Links") or
            host.get("ExtraHosts") or host.get("Dns") or
            host.get("CgroupParent") or
            (host.get("RestartPolicy") or {}).get("Name") not in {"", "no"}):
        fail("disposable container host-resource contract mismatch")
    labels = config.get("Labels") or {}
    environment = config.get("Env") or []
    if labels.get("io.hepta.purpose") != PURPOSE:
        fail("disposable container purpose label mismatch")
    if (
            "HEPTA_AGENT_OS_E2E_DISPOSABLE=1" not in environment or
            f"HEPTA_AGENT_OS_E2E_RUN_ID={run_id}" not in environment):
        fail("disposable container sentinel environment mismatch")
    if config.get("User") not in {"", "0", "0:0"}:
        fail("disposable container must enter as root")
    return {
        "container_id": container_id,
        "image_id": image_id,
        "network_mode": "none",
        "read_only_rootfs": True,
        "bind_mounts": 0,
        "published_ports": 0,
        "privileged": False,
        "apparmor_profile": APPARMOR_PROFILE,
        "private_cgroup_namespace": True,
    }


def validate_container(
        container_id: str,
        name: str,
        image_id: str,
        run_id: str,
) -> dict[str, Any]:
    raw = command(
        docker_cli("container", "inspect", container_id), timeout=30).stdout
    try:
        records = json.loads(raw)
    except json.JSONDecodeError as error:
        raise GateError("container inspection is invalid JSON") from error
    if not isinstance(records, list) or len(records) != 1:
        fail("container inspection cardinality mismatch")
    return validate_container_inspect_record(
        records[0],
        container_id=container_id,
        name=name,
        image_id=image_id,
        run_id=run_id,
    )


def parse_inner_result(stdout: str) -> dict[str, Any]:
    prefix = "HEPTA_AGENT_OS_ROOTFUL_E2E_RESULT="
    lines = [line for line in stdout.splitlines() if line.startswith(prefix)]
    if len(lines) != 1:
        fail("inner gate must emit exactly one result record")
    try:
        result = json.loads(lines[0][len(prefix):])
    except json.JSONDecodeError as error:
        raise GateError("inner gate result is invalid JSON") from error
    expected_top = {
        "schema", "profile", "passed", "identities", "checks", "lifecycle",
        "boundary"}
    if not isinstance(result, dict) or set(result) != expected_top:
        fail("inner gate top-level contract mismatch")
    if (
            result.get("schema") != INNER_SCHEMA or
            result.get("profile") !=
            "two-domain-agent-gateway-execution-watch" or
            result.get("passed") is not True):
        fail("inner gate did not pass")
    expected_identities = {
        "agent_uid": 2004,
        "gateway_uid": 2001,
        "simulator_execution_uid": 2002,
        "ib_execution_uid_reserved_not_started": 2003,
        "trust_domains": {
            "codex-a": {
                "gateway_uid": 2101,
                "agent_uid": 2104,
                "execution_uid": 2111,
                "reader_uid": 2121,
            },
            "openclaw-b": {
                "gateway_uid": 2102,
                "agent_uid": 2105,
                "execution_uid": 2112,
                "reader_uid": 2122,
            },
        },
    }
    if result.get("identities") != expected_identities:
        fail("inner gate identity contract mismatch")
    checks = result.get("checks")
    expected_checks = {
        "systemd_pid1", "network_none_loopback_only",
        "no_host_mount_or_docker_socket", "fixed_identity_isolation",
        "two_domain_execution_identity_isolation",
        "two_domain_execution_socket_cross_access_denied",
        "two_domain_execution_authorities_started_and_stopped",
        "two_domain_runtime_configs_root_owned_regular",
        "two_domain_agent_host_dropins_isolated",
        "two_agent_gateway_execution_watch_chains",
        "two_domain_uid_config_cross_rejected",
        "two_domain_token_cross_rejected",
        "two_domain_account_binding_cross_rejected",
        "two_domain_execution_binding_cross_rejected",
        "two_domain_gateway_socket_cross_rejected",
        "two_domain_watch_restart_fails_closed",
        "two_domain_collector_typed_terminal",
        "two_domain_watch_sessions_revoked",
        "two_domain_custodian_reader_identity_isolation",
        "two_domain_watch_environments_root_owned_private",
        "two_domain_custodian_services_monitored",
        "two_domain_custodian_reconcile_timers_enabled",
        "two_domain_custodian_rotation_bound",
        "two_domain_custodian_sigkill_crash_closed",
        "two_domain_custodian_closure_receipts_exact",
        "two_domain_custodian_authority_residue_absent",
        "uid1000_observer_reads_uid2101_proc_stat",
        "broker_network_policy_active",
        "broker_watchdog_timeout_observed",
        "broker_watchdog_timeout_stop_contract",
        "broker_watchdog_gateway_binds_to_stop",
        "broker_watchdog_deny_all_persisted",
        "broker_watchdog_watch_terminalized",
        "broker_watchdog_clean_restart",
        "agent_ib_ports_denied", "gateway_ib_ports_denied",
        "ib_execution_ib_ports_denied", "agent_model_egress_preserved",
        "ib_paper_surface_absent", "installation_preflight",
        "simulator_dual_socket_activation", "gateway_dual_socket_activation",
        "root_watch_bootstrap", "uid_2004_mcp_initialize",
        "uid_2004_exact_watch_tool_list", "uid_2004_read_only_probes",
        "gateway_service_socket_reactivation",
        "simulator_service_socket_reactivation",
        "socket_stop_removes_paths", "socket_restart_recreates_paths",
        "watch_restart_fails_closed", "runtime_preflight_after_restart",
        "watch_session_revoked",
        "all_runtime_paths_removed",
    }
    if (
            not isinstance(checks, dict) or set(checks) != expected_checks or
            any(value is not True for value in checks.values())):
        fail("inner gate check contract mismatch")
    lifecycle = result.get("lifecycle")
    if (
            not isinstance(lifecycle, dict) or
            set(lifecycle) != {
                "watch_generation", "initial", "service_reactivation",
                "socket_reactivation", "trust_domains"}):
        fail("inner gate lifecycle contract mismatch")
    generation = lifecycle.get("watch_generation")
    if (not isinstance(generation, int) or isinstance(generation, bool) or
            generation < 1):
        fail("inner gate WATCH generation is invalid")
    for phase in ("initial", "service_reactivation", "socket_reactivation"):
        record = lifecycle.get(phase)
        if (
                not isinstance(record, dict) or
                set(record) != {
                    "gateway_pid", "simulator_pid", "tool_socket_inode",
                    "supervisor_socket_inode", "execution_socket_inode",
                    "events_socket_inode"} or
                any(not isinstance(value, int) or isinstance(value, bool) or
                    value <= 0 for value in record.values())):
            fail(f"inner gate {phase} lifecycle record is invalid")
    initial = lifecycle["initial"]
    services = lifecycle["service_reactivation"]
    sockets = lifecycle["socket_reactivation"]
    if (
            initial["gateway_pid"] == services["gateway_pid"] or
            initial["simulator_pid"] == services["simulator_pid"] or
            services["gateway_pid"] == sockets["gateway_pid"] or
            services["simulator_pid"] == sockets["simulator_pid"]):
        fail("inner gate service PIDs did not change across activation")
    for key in (
            "tool_socket_inode", "supervisor_socket_inode",
            "execution_socket_inode", "events_socket_inode"):
        if initial[key] != services[key] or services[key] == sockets[key]:
            fail("inner gate socket inode lifecycle mismatch")
    domain_lifecycle = lifecycle.get("trust_domains")
    if (
            not isinstance(domain_lifecycle, dict) or
            set(domain_lifecycle) != {"codex-a", "openclaw-b"}):
        fail("inner gate trust-domain lifecycle contract mismatch")
    domain_fields = {
        "watch_generation", "gateway_pid", "simulator_pid",
        "custodian_pid", "reader_owner_pid",
        "custodian_crash_generation", "custodian_restart_count",
        "closure_receipt_count",
        "tool_socket_inode", "supervisor_socket_inode",
        "execution_socket_inode", "events_socket_inode",
    }
    for domain_id, record in domain_lifecycle.items():
        if (
                not isinstance(record, dict) or set(record) != domain_fields or
                any(type(value) is not int or value <= 0
                    for value in record.values())):
            fail(f"inner gate {domain_id} lifecycle record is invalid")
    identity_fields = {
        "gateway_pid", "simulator_pid", "custodian_pid",
        "reader_owner_pid", "tool_socket_inode",
        "supervisor_socket_inode", "execution_socket_inode",
        "events_socket_inode",
    }
    for field in identity_fields:
        if (
                domain_lifecycle["codex-a"][field] ==
                domain_lifecycle["openclaw-b"][field]):
            fail("inner gate trust-domain runtime identity was reused")
    boundary = result.get("boundary")
    expected_boundary = {
        "container_network": "none",
        "real_broker_connections": 0,
        "paper_orders": 0,
        "paper_authorized": False,
        "live_authorized": False,
        "ib_adapter_staged": False,
        "host_hepta_units_started": False,
        "host_bind_mounts": 0,
        "raw_session_token_recorded": False,
    }
    if boundary != expected_boundary:
        fail("inner gate crossed the offline WATCH-only boundary")
    return result


def require_docker_absent(kind: str, reference: str) -> None:
    if kind not in {"container", "image"}:
        fail("unsupported Docker absence object kind")
    inspected = command(
        docker_cli(kind, "inspect", reference), check=False, timeout=20)
    if inspected.returncode == 0:
        fail(f"refusing to reuse existing Docker {kind}: {reference}")
    expected = (
        "[]\n"
        "Error response from daemon: No such "
        f"{kind}: {reference}\n"
    )
    if inspected.returncode != 1 or inspected.stdout != expected:
        fail(f"could not prove Docker {kind} absence: {reference}")


def cleanup_container(
        name: str,
        expected_id: Optional[str],
        expected_image_id: Optional[str],
        *,
        expected_role: Optional[str] = None,
        expected_run_id: Optional[str] = None,
) -> None:
    if (expected_role is None) != (expected_run_id is None):
        fail("container cleanup role and run ownership must be paired")
    reference = expected_id or name
    inspected = command(
        docker_cli("container", "inspect", reference),
        check=False,
        timeout=20,
    )
    if inspected.returncode == 1:
        require_docker_absent("container", name)
        return
    if inspected.returncode != 0 or expected_image_id is None:
        fail("could not safely identify disposable container")
    try:
        records = json.loads(
            inspected.stdout, object_pairs_hook=reject_duplicate_json_keys)
    except json.JSONDecodeError as error:
        raise GateError(
            "disposable container cleanup inspection invalid") from error
    if type(records) is not list or len(records) != 1:
        fail("disposable container cleanup inspection invalid")
    record = records[0]
    if type(record) is not dict:
        fail("disposable container cleanup inspection invalid")
    config = record.get("Config")
    if type(config) is not dict or type(config.get("Labels")) is not dict:
        fail("disposable container cleanup inspection invalid")
    labels = config["Labels"]
    if (
            record.get("Image") != expected_image_id or
            labels.get("io.hepta.purpose") != PURPOSE or
            (expected_role is not None and
             labels.get(ROLE_LABEL_KEY) != expected_role) or
            (expected_run_id is not None and
             labels.get(RUN_ID_LABEL_KEY) != expected_run_id) or
            (expected_id is not None and record.get("Id") != expected_id) or
            record.get("Name") != "/" + name):
        fail("disposable container cleanup ownership mismatch")
    command(docker_cli("rm", "--force", record["Id"]), timeout=30)
    require_docker_absent("container", name)


def builder_metadata_exists(builder_name: str) -> bool:
    path = builder_metadata_path(builder_name)
    try:
        metadata = path.lstat()
    except FileNotFoundError:
        return False
    if (
            not stat.S_ISREG(metadata.st_mode) or
            metadata.st_nlink != 1 or
            metadata.st_uid != os.geteuid() or
            metadata.st_mode & (stat.S_IWGRP | stat.S_IWOTH)):
        fail("private buildx builder metadata ownership is unsafe")
    return True


def cleanup_isolated_builder(
        names: dict[str, str],
        run_id: str,
        buildkit_image: dict[str, Any],
        expected_container_id: Optional[str],
) -> dict[str, Any]:
    image_id = buildkit_image.get("id")
    if (
            names != isolated_builder_names(run_id) or
            type(image_id) is not str or
            CANONICAL_SHA256.fullmatch(image_id) is None):
        fail("isolated builder cleanup identity is invalid")

    container_inspection = command(
        docker_cli("container", "inspect", names["container"]),
        check=False,
        timeout=20,
    )
    owned_container_id: Optional[str] = None
    if container_inspection.returncode == 0:
        try:
            container_records = json.loads(
                container_inspection.stdout,
                object_pairs_hook=reject_duplicate_json_keys,
            )
        except json.JSONDecodeError as error:
            raise GateError(
                "isolated builder cleanup container inspection is invalid"
            ) from error
        if (
                type(container_records) is not list or
                len(container_records) != 1 or
                type(container_records[0]) is not dict or
                type(container_records[0].get("Id")) is not str):
            fail("isolated builder cleanup container inspection is invalid")
        owned_container_id = container_records[0]["Id"]
        if (
                expected_container_id is not None and
                owned_container_id != expected_container_id):
            fail("isolated builder cleanup container ID drifted")
        validate_builder_container_record(
            container_records[0],
            container_id=owned_container_id,
            names=names,
            run_id=run_id,
            buildkit_image=buildkit_image,
            expected_running=None,
        )
    elif container_inspection.returncode == 1:
        require_docker_absent("container", names["container"])
    else:
        fail("could not safely inspect isolated builder container for cleanup")

    volume_inspection = command(
        docker_cli("volume", "inspect", names["volume"]),
        check=False,
        timeout=20,
    )
    volume_present = volume_inspection.returncode == 0
    if volume_present:
        try:
            volume_records = json.loads(
                volume_inspection.stdout,
                object_pairs_hook=reject_duplicate_json_keys,
            )
        except json.JSONDecodeError as error:
            raise GateError(
                "isolated builder cleanup volume inspection is invalid"
            ) from error
        if type(volume_records) is not list or len(volume_records) != 1:
            fail("isolated builder cleanup volume inspection is invalid")
        validate_builder_volume_record(
            volume_records[0],
            names=names,
            run_id=run_id,
            buildkit_image_id=image_id,
        )
    elif volume_inspection.returncode == 1:
        require_builder_volume_absent(names["volume"])
    else:
        fail("could not safely inspect isolated builder volume for cleanup")

    metadata_present = builder_metadata_exists(names["builder"])
    buildx_rm_result = "not-created"
    buildx_rm_failure: Optional[str] = None
    if metadata_present:
        removed = command(docker_cli(
            "buildx", "rm", "--force", names["builder"]),
            check=False,
            timeout=120,
        )
        if removed.returncode == 0:
            buildx_rm_result = "completed"
        else:
            buildx_rm_result = "failed"
            buildx_rm_failure = removed.stdout[:256]

    remaining_container = command(
        docker_cli("container", "inspect", names["container"]),
        check=False,
        timeout=20,
    )
    recovered_container = False
    if remaining_container.returncode == 0:
        try:
            records = json.loads(
                remaining_container.stdout,
                object_pairs_hook=reject_duplicate_json_keys,
            )
        except json.JSONDecodeError as error:
            raise GateError(
                "isolated builder fallback container inspection is invalid"
            ) from error
        if (
                type(records) is not list or len(records) != 1 or
                type(records[0]) is not dict or
                type(records[0].get("Id")) is not str):
            fail("isolated builder fallback container inspection is invalid")
        fallback_id = records[0]["Id"]
        validate_builder_container_record(
            records[0],
            container_id=fallback_id,
            names=names,
            run_id=run_id,
            buildkit_image=buildkit_image,
            expected_running=None,
        )
        if (
                owned_container_id is not None and
                fallback_id != owned_container_id):
            fail("isolated builder fallback container ID drifted")
        command(docker_cli(
            "container", "rm", "--force", fallback_id), timeout=60)
        recovered_container = True
    elif remaining_container.returncode != 1:
        fail("isolated builder container absence could not be established")
    require_docker_absent("container", names["container"])

    remaining_volume = command(
        docker_cli("volume", "inspect", names["volume"]),
        check=False,
        timeout=20,
    )
    recovered_volume = False
    if remaining_volume.returncode == 0:
        try:
            records = json.loads(
                remaining_volume.stdout,
                object_pairs_hook=reject_duplicate_json_keys,
            )
        except json.JSONDecodeError as error:
            raise GateError(
                "isolated builder fallback volume inspection is invalid"
            ) from error
        if type(records) is not list or len(records) != 1:
            fail("isolated builder fallback volume inspection is invalid")
        validate_builder_volume_record(
            records[0],
            names=names,
            run_id=run_id,
            buildkit_image_id=image_id,
        )
        command(docker_cli(
            "volume", "rm", names["volume"]), timeout=60)
        recovered_volume = True
    elif remaining_volume.returncode != 1:
        fail("isolated builder volume absence could not be established")
    require_builder_volume_absent(names["volume"])
    require_builder_metadata_absent(names["builder"])
    require_exact_image_id_present(image_id)
    if buildx_rm_failure is not None:
        fail(
            "buildx isolated builder removal failed after exact object "
            f"recovery: {buildx_rm_failure}")
    return {
        "buildx_rm": buildx_rm_result,
        "container_absent": True,
        "state_volume_absent": True,
        "private_builder_metadata_absent": True,
        "exact_container_fallback": recovered_container,
        "exact_volume_fallback": recovered_volume,
        "cache_cleanup": "state-volume-removed",
    }


def cleanup_image(
        tag: str,
        expected_id: Optional[str],
        expected_labels: Optional[dict[str, str]] = None,
) -> dict[str, Any]:
    def parse_inspection(stdout: str) -> dict[str, Any]:
        try:
            records = json.loads(
                stdout, object_pairs_hook=reject_duplicate_json_keys)
        except json.JSONDecodeError as error:
            raise GateError(
                "disposable image cleanup inspection invalid") from error
        if (
                type(records) is not list or len(records) != 1 or
                type(records[0]) is not dict):
            fail("disposable image cleanup inspection invalid")
        return records[0]

    inspected = command(
        docker_cli("image", "inspect", tag), check=False, timeout=20)
    if inspected.returncode == 1:
        require_docker_absent("image", tag)
        if expected_id is not None:
            exact = command(
                docker_cli("image", "inspect", expected_id),
                check=False,
                timeout=20,
            )
            if exact.returncode == 0:
                parse_inspection(exact.stdout)
                fail(
                    "built image tag disappeared while exact image residue "
                    "remains")
            require_docker_absent("image", expected_id)
            fail(
                "built image tag disappeared before ownership-checked "
                "cleanup")
        if expected_labels is not None:
            fail(
                "built image tag disappeared after build; exact image "
                "residue is unattested")
        return {
            "tag_absent": True,
            "exact_image_id_absent": "not-created",
        }
    if inspected.returncode != 0 or expected_labels is None:
        fail("could not safely identify disposable image")
    owned = validate_owned_built_image_record(
        parse_inspection(inspected.stdout),
        tag=tag,
        expected_labels=expected_labels,
        expected_id=expected_id,
    )
    image_id = owned["id"]
    exact = command(
        docker_cli("image", "inspect", image_id),
        check=False,
        timeout=20,
    )
    if exact.returncode != 0:
        if exact.returncode == 1:
            require_docker_absent("image", image_id)
        fail("built image exact ID disappeared before cleanup")
    validate_owned_built_image_record(
        parse_inspection(exact.stdout),
        tag=tag,
        expected_labels=expected_labels,
        expected_id=image_id,
    )
    command(docker_cli("image", "rm", tag), timeout=60)
    require_docker_absent("image", tag)
    require_docker_absent("image", image_id)
    return {
        "tag_absent": True,
        "exact_image_id_absent": True,
    }


def cleanup_gate_docker_objects(
        *,
        runtime_name: str,
        runtime_id: Optional[str],
        built_tag: str,
        built_image_id: Optional[str],
        built_labels: Optional[dict[str, str]],
        holder_name: str,
        holder_id: Optional[str],
        base_image_id: str,
        run_id: str,
) -> dict[str, Any]:
    cleanup_errors: list[Exception] = []
    cleanup_results: dict[str, Any] = {}
    cleanup_operations = (
        (
            "runtime_container",
            lambda: cleanup_container(
                runtime_name, runtime_id, built_image_id),
        ),
        (
            "built_image",
            lambda: cleanup_image(
                built_tag, built_image_id, built_labels),
        ),
        (
            "base_holder",
            lambda: cleanup_container(
                holder_name,
                holder_id,
                base_image_id,
                expected_role=BASE_HOLDER_ROLE,
                expected_run_id=run_id,
            ),
        ),
    )
    for object_name, cleanup_operation in cleanup_operations:
        try:
            evidence = cleanup_operation()
            cleanup_results[object_name] = (
                {"absent": True} if evidence is None else evidence)
        except Exception as error:
            cleanup_errors.append(error)
    if cleanup_errors:
        raise GateError(
            "disposable Docker cleanup failed or ownership drifted: "
            f"{cleanup_errors[0]}"
        ) from cleanup_errors[0]
    return cleanup_results


_POLICY_METADATA_FIELDS = (
    "st_dev", "st_ino", "st_mode", "st_nlink", "st_uid", "st_gid",
    "st_size", "st_mtime_ns", "st_ctime_ns",
)


def _filesystem_magic(descriptor: int) -> int:
    if type(descriptor) is not int or descriptor < 0:
        fail("filesystem descriptor is invalid")
    buffer = ctypes.create_string_buffer(256)
    libc = ctypes.CDLL(None, use_errno=True)
    fstatfs = libc.fstatfs
    fstatfs.argtypes = [ctypes.c_int, ctypes.c_void_p]
    fstatfs.restype = ctypes.c_int
    if fstatfs(descriptor, ctypes.byref(buffer)) != 0:
        error_number = ctypes.get_errno()
        fail(
            "cannot identify AppArmor policy filesystem: "
            f"{os.strerror(error_number)}")
    return int.from_bytes(
        buffer.raw[:ctypes.sizeof(ctypes.c_long)],
        byteorder=sys.byteorder,
        signed=False,
    )


def _policy_metadata(metadata: os.stat_result) -> tuple[int, ...]:
    return tuple(
        int(getattr(metadata, field)) for field in _POLICY_METADATA_FIELDS)


def _require_fixed_root_directory(
        path: Path,
        *,
        expected_mode: int,
) -> tuple[int, ...]:
    try:
        metadata = os.lstat(path)
    except OSError as error:
        fail(f"cannot inspect fixed AppArmor parent: {error.strerror}")
    if (
            not stat.S_ISDIR(metadata.st_mode) or
            metadata.st_uid != 0 or metadata.st_gid != 0 or
            metadata.st_nlink < 2 or
            stat.S_IMODE(metadata.st_mode) != expected_mode):
        fail("fixed AppArmor parent metadata is invalid")
    return _policy_metadata(metadata)


def _open_fixed_directory(path: Path) -> int:
    try:
        return os.open(
            path,
            os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC |
            getattr(os, "O_NOFOLLOW", 0),
        )
    except OSError as error:
        fail(f"cannot securely open fixed AppArmor parent: {error.strerror}")


def _apparmor_namespace_record() -> dict[str, Any]:
    values: dict[str, str] = {}
    metadata: dict[str, tuple[int, ...]] = {}
    for field in (".ns_name", ".ns_level", ".ns_stacked", ".stacked"):
        value, identity = _read_policy_scalar(
            APPARMOR_CONTROL_ROOT / field,
            expected_uid=0,
            expected_gid=0,
        )
        values[field] = value
        metadata[field] = identity
    if (
            values[".ns_name"] != "root" or
            values[".ns_level"] != "0" or
            values[".ns_stacked"] != "no" or
            values[".stacked"] != "no"):
        fail("current AppArmor namespace is not the unstacked root namespace")
    return {
        "name": "root",
        "level": 0,
        "stacked": False,
        "field_metadata_sha256": "sha256:" + hashlib.sha256(
            json.dumps(
                {key: list(value) for key, value in sorted(metadata.items())},
                sort_keys=True,
                separators=(",", ":"),
            ).encode("ascii")
        ).hexdigest(),
    }


def _apparmor_kernel_anchor_record(
        policy_descriptor: int,
) -> dict[str, Any]:
    security_metadata = _require_fixed_root_directory(
        APPARMOR_SECURITY_ROOT, expected_mode=0o755)
    control_metadata = _require_fixed_root_directory(
        APPARMOR_CONTROL_ROOT, expected_mode=0o755)
    security_descriptor = _open_fixed_directory(APPARMOR_SECURITY_ROOT)
    control_descriptor = _open_fixed_directory(APPARMOR_CONTROL_ROOT)
    try:
        if _filesystem_magic(security_descriptor) != SECURITYFS_MAGIC:
            fail("fixed AppArmor parent is not securityfs")
        if _filesystem_magic(control_descriptor) != SECURITYFS_MAGIC:
            fail("AppArmor control directory is not on securityfs")
        security_stat = os.fstat(security_descriptor)
        control_stat = os.fstat(control_descriptor)
    finally:
        os.close(control_descriptor)
        os.close(security_descriptor)
    if (
            _policy_metadata(security_stat) != security_metadata or
            _policy_metadata(control_stat) != control_metadata or
            security_stat.st_dev != control_stat.st_dev):
        fail("AppArmor securityfs parent identity is inconsistent")

    try:
        magic_before = os.lstat(APPARMOR_POLICY_MAGIC_LINK)
        magic_target = os.readlink(APPARMOR_POLICY_MAGIC_LINK)
        magic_after = os.lstat(APPARMOR_POLICY_MAGIC_LINK)
    except OSError as error:
        fail(f"cannot inspect AppArmor policy magic link: {error.strerror}")
    magic_identity = _policy_metadata(magic_before)
    target_match = APPARMOR_MAGIC_LINK_TARGET.fullmatch(magic_target)
    if (
            not stat.S_ISLNK(magic_before.st_mode) or
            magic_before.st_uid != 0 or magic_before.st_gid != 0 or
            magic_before.st_nlink != 1 or
            stat.S_IMODE(magic_before.st_mode) != 0o444 or
            magic_identity != _policy_metadata(magic_after) or
            target_match is None):
        fail("AppArmor policy magic link identity is invalid")

    try:
        policy_stat = os.fstat(policy_descriptor)
    except OSError as error:
        fail(f"cannot inspect open AppArmor policy mount: {error.strerror}")
    if (
            not stat.S_ISDIR(policy_stat.st_mode) or
            policy_stat.st_uid != 0 or policy_stat.st_gid != 0 or
            policy_stat.st_nlink < 2 or
            stat.S_IMODE(policy_stat.st_mode) != 0o755 or
            policy_stat.st_dev == security_stat.st_dev or
            _filesystem_magic(policy_descriptor) != AAFS_MAGIC):
        fail("AppArmor policy descriptor is not the kernel AAFS mount")
    try:
        verification_descriptor = os.open(
            APPARMOR_POLICY_MAGIC_LINK,
            os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC,
        )
    except OSError as error:
        fail(
            "cannot re-open fixed AppArmor AAFS policy mount: "
            f"{error.strerror}")
    try:
        verification_stat = os.fstat(verification_descriptor)
        verification_magic = _filesystem_magic(verification_descriptor)
    finally:
        os.close(verification_descriptor)
    if (
            verification_stat.st_dev != policy_stat.st_dev or
            verification_stat.st_ino != policy_stat.st_ino or
            verification_magic != AAFS_MAGIC):
        fail("AppArmor policy magic link changed while it was anchored")
    return {
        "securityfs_magic": f"0x{SECURITYFS_MAGIC:08x}",
        "aafs_magic": f"0x{AAFS_MAGIC:08x}",
        "securityfs_device": security_stat.st_dev,
        "securityfs_inode": security_stat.st_ino,
        "control_inode": control_stat.st_ino,
        "policy_device": policy_stat.st_dev,
        "policy_inode": policy_stat.st_ino,
        "policy_magic_link": magic_target,
        "policy_magic_link_id": int(target_match.group(1)),
        "policy_magic_link_inode": magic_before.st_ino,
        "namespace": _apparmor_namespace_record(),
    }


def _open_apparmor_kernel_anchor() -> AppArmorKernelAnchor:
    _require_fixed_root_directory(APPARMOR_SECURITY_ROOT, expected_mode=0o755)
    _require_fixed_root_directory(APPARMOR_CONTROL_ROOT, expected_mode=0o755)
    try:
        descriptor = os.open(
            APPARMOR_POLICY_MAGIC_LINK,
            os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC,
        )
    except OSError as error:
        fail(f"cannot open fixed AppArmor AAFS policy mount: {error.strerror}")
    try:
        record = _apparmor_kernel_anchor_record(descriptor)
    except Exception:
        os.close(descriptor)
        raise
    return AppArmorKernelAnchor(
        descriptor=descriptor,
        policy_root=Path(f"/proc/self/fd/{descriptor}"),
        record=record,
    )


def _require_policy_directory(
        path: Path,
        *,
        expected_uid: int,
        expected_gid: int,
) -> tuple[int, ...]:
    try:
        metadata = os.lstat(path)
    except OSError as error:
        fail(f"cannot inspect AppArmor policy directory: {error.strerror}")
    if (
            not stat.S_ISDIR(metadata.st_mode) or
            metadata.st_uid != expected_uid or
            metadata.st_gid != expected_gid or
            metadata.st_nlink < 2 or
            stat.S_IMODE(metadata.st_mode) != 0o755):
        fail("AppArmor policy directory metadata is invalid")
    return _policy_metadata(metadata)


def _read_policy_scalar(
        path: Path,
        *,
        expected_uid: int,
        expected_gid: int,
        expected_mode: int = 0o444,
) -> tuple[str, tuple[int, ...]]:
    if type(expected_mode) is not int or expected_mode not in (0o444, 0o644):
        fail("AppArmor scalar mode contract is invalid")
    try:
        descriptor = os.open(
            path,
            os.O_RDONLY | os.O_CLOEXEC | getattr(os, "O_NOFOLLOW", 0),
        )
    except OSError as error:
        fail(f"cannot securely open AppArmor policy field: {error.strerror}")
    chunks: list[bytes] = []
    total = 0
    try:
        before = os.fstat(descriptor)
        if (
                not stat.S_ISREG(before.st_mode) or before.st_nlink != 1 or
                before.st_uid != expected_uid or
                before.st_gid != expected_gid or
                stat.S_IMODE(before.st_mode) != expected_mode or
                before.st_size < 0 or
                before.st_size > MAX_APPARMOR_SCALAR_BYTES):
            fail("AppArmor policy field metadata is invalid")
        while True:
            chunk = os.read(
                descriptor,
                min(1024, MAX_APPARMOR_SCALAR_BYTES + 1 - total),
            )
            if not chunk:
                break
            chunks.append(chunk)
            total += len(chunk)
            if total > MAX_APPARMOR_SCALAR_BYTES:
                fail("AppArmor policy field exceeds its evidence bound")
        after = os.fstat(descriptor)
    except OSError as error:
        fail(f"cannot read AppArmor policy field: {error.strerror}")
    finally:
        os.close(descriptor)
    try:
        named = os.lstat(path)
    except OSError as error:
        fail(f"AppArmor policy field disappeared: {error.strerror}")
    identity = _policy_metadata(before)
    if (
            identity != _policy_metadata(after) or
            identity != _policy_metadata(named) or
            (before.st_size != 0 and total != before.st_size)):
        fail("AppArmor policy field changed while it was read")
    contents = b"".join(chunks)
    if (
            not contents or not contents.endswith(b"\n") or
            contents.count(b"\n") != 1 or b"\x00" in contents):
        fail("AppArmor policy field is not one canonical line")
    try:
        value = contents[:-1].decode("ascii", errors="strict")
    except UnicodeDecodeError as error:
        raise GateError(
            "AppArmor policy field encoding is invalid") from error
    if (
            not value or value != value.strip(" ") or
            any(ord(character) < 0x20 or ord(character) > 0x7e
                for character in value)):
        fail("AppArmor policy field contains invalid characters")
    return value, identity


def _require_policy_symlink(
        path: Path,
        *,
        expected_uid: int,
        expected_gid: int,
        expected_mode: int,
) -> tuple[str, tuple[int, ...]]:
    try:
        before = os.lstat(path)
        target = os.readlink(path)
        after = os.lstat(path)
    except OSError as error:
        fail(f"cannot inspect AppArmor policy symlink: {error.strerror}")
    identity = _policy_metadata(before)
    if (
            not stat.S_ISLNK(before.st_mode) or before.st_nlink != 1 or
            before.st_uid != expected_uid or
            before.st_gid != expected_gid or
            stat.S_IMODE(before.st_mode) != expected_mode or
            identity != _policy_metadata(after) or
            type(target) is not str or not target or len(target) > 255 or
            any(ord(character) < 0x21 or ord(character) > 0x7e
                for character in target)):
        fail("AppArmor policy symlink metadata is invalid")
    return target, identity


def _require_raw_policy_blob(
        path: Path,
        *,
        expected_uid: int,
        expected_gid: int,
) -> tuple[int, ...]:
    try:
        metadata = os.lstat(path)
    except OSError as error:
        fail(f"cannot inspect AppArmor raw policy data: {error.strerror}")
    if (
            not stat.S_ISREG(metadata.st_mode) or metadata.st_nlink != 1 or
            metadata.st_uid != expected_uid or
            metadata.st_gid != expected_gid or
            stat.S_IMODE(metadata.st_mode) != 0o444 or
            metadata.st_size <= 0 or
            metadata.st_size > MAX_APPARMOR_RAW_DATA_BYTES):
        fail("AppArmor raw policy data metadata is invalid")
    return _policy_metadata(metadata)


def _policy_entry_inventory(
        profiles_path: Path,
        *,
        expected_uid: int,
        expected_gid: int,
) -> tuple[list[str], dict[str, tuple[int, ...]]]:
    try:
        entries = list(os.scandir(profiles_path))
    except OSError as error:
        fail(f"cannot enumerate AppArmor policy profiles: {error.strerror}")
    if not entries or len(entries) > MAX_APPARMOR_POLICY_ENTRIES:
        fail("AppArmor policy profile inventory is outside its bound")
    names: list[str] = []
    metadata: dict[str, tuple[int, ...]] = {}
    for entry in entries:
        name = entry.name
        if (
                type(name) is not str or
                APPARMOR_POLICY_ENTRY.fullmatch(name) is None or
                name in metadata):
            fail("AppArmor policy profile directory name is invalid")
        entry_path = profiles_path / name
        metadata[name] = _require_policy_directory(
            entry_path,
            expected_uid=expected_uid,
            expected_gid=expected_gid,
        )
        names.append(name)
    names.sort()
    return names, metadata


def _policy_inventory_sha256(names: list[str]) -> str:
    encoded = json.dumps(
        names, ensure_ascii=True, separators=(",", ":"),
    ).encode("ascii")
    return "sha256:" + hashlib.sha256(encoded).hexdigest()


def _validate_apparmor_policy_tree(
        provenance: ReviewedAppArmorProvenance,
        *,
        policy_root: Path,
        expected_uid: int = 0,
        expected_gid: int = 0,
        expected_symlink_mode: int = 0o444,
) -> dict[str, Any]:
    if type(provenance) is not ReviewedAppArmorProvenance:
        fail("digest-pinned external AppArmor provenance GO is required")
    if (
            not isinstance(policy_root, Path) or
            type(expected_uid) is not int or expected_uid < 0 or
            type(expected_gid) is not int or expected_gid < 0 or
            type(expected_symlink_mode) is not int or
            expected_symlink_mode not in (0o444, 0o777)):
        fail("AppArmor policy validation arguments are invalid")
    profiles_path = policy_root / "profiles"
    raw_root = policy_root / "raw_data"
    watched: dict[Path, tuple[int, ...]] = {}
    watched[profiles_path] = _require_policy_directory(
        profiles_path,
        expected_uid=expected_uid,
        expected_gid=expected_gid,
    )
    watched[raw_root] = _require_policy_directory(
        raw_root,
        expected_uid=expected_uid,
        expected_gid=expected_gid,
    )
    entries_before, entry_metadata = _policy_entry_inventory(
        profiles_path,
        expected_uid=expected_uid,
        expected_gid=expected_gid,
    )
    matching: list[tuple[str, Path]] = []
    for entry_name in entries_before:
        entry_path = profiles_path / entry_name
        profile_name, _metadata = _read_policy_scalar(
            entry_path / "name",
            expected_uid=expected_uid,
            expected_gid=expected_gid,
        )
        if profile_name == APPARMOR_PROFILE:
            matching.append((entry_name, entry_path))
    if len(matching) != 1:
        fail("required AppArmor policy profile is not uniquely present")
    entry_name, entry_path = matching[0]
    watched[entry_path] = entry_metadata[entry_name]

    scalar_values: dict[str, str] = {}
    for field in (
            "name", "mode", "attach", "learning_count", "sha256"):
        value, metadata = _read_policy_scalar(
            entry_path / field,
            expected_uid=expected_uid,
            expected_gid=expected_gid,
        )
        scalar_values[field] = value
        watched[entry_path / field] = metadata
    if scalar_values["name"] != APPARMOR_PROFILE:
        fail("AppArmor profile identity changed during validation")
    if scalar_values["mode"] != "enforce":
        fail("required AppArmor policy profile is not enforcing")
    if scalar_values["attach"] != APPARMOR_ATTACH:
        fail("AppArmor profile attachment identity is invalid")
    if scalar_values["learning_count"] != "0":
        fail("AppArmor profile has active learning events")
    if BARE_SHA256.fullmatch(scalar_values["sha256"]) is None:
        fail("AppArmor profile digest is not canonical")

    symlinks: dict[str, str] = {}
    for field in ("raw_data", "raw_sha256", "raw_abi"):
        target, metadata = _require_policy_symlink(
            entry_path / field,
            expected_uid=expected_uid,
            expected_gid=expected_gid,
            expected_mode=expected_symlink_mode,
        )
        symlinks[field] = target
        watched[entry_path / field] = metadata
    raw_match = re.fullmatch(
        r"\.\./\.\./raw_data/([1-9][0-9]{0,19})/raw_data",
        symlinks["raw_data"],
    )
    if raw_match is None:
        fail("AppArmor raw_data symlink target is invalid")
    raw_id = raw_match.group(1)
    if APPARMOR_RAW_DATA_ID.fullmatch(raw_id) is None:
        fail("AppArmor raw policy identifier is invalid")
    if symlinks["raw_sha256"] != f"../../raw_data/{raw_id}/sha256":
        fail("AppArmor raw_sha256 symlink target is inconsistent")
    if symlinks["raw_abi"] != f"../../raw_data/{raw_id}/abi":
        fail("AppArmor raw_abi symlink target is inconsistent")

    raw_directory = raw_root / raw_id
    watched[raw_directory] = _require_policy_directory(
        raw_directory,
        expected_uid=expected_uid,
        expected_gid=expected_gid,
    )
    watched[raw_directory / "raw_data"] = _require_raw_policy_blob(
        raw_directory / "raw_data",
        expected_uid=expected_uid,
        expected_gid=expected_gid,
    )
    raw_sha256, metadata = _read_policy_scalar(
        raw_directory / "sha256",
        expected_uid=expected_uid,
        expected_gid=expected_gid,
    )
    watched[raw_directory / "sha256"] = metadata
    raw_abi, metadata = _read_policy_scalar(
        raw_directory / "abi",
        expected_uid=expected_uid,
        expected_gid=expected_gid,
    )
    watched[raw_directory / "abi"] = metadata
    if BARE_SHA256.fullmatch(raw_sha256) is None:
        fail("AppArmor raw policy digest is not canonical")
    if APPARMOR_RAW_ABI.fullmatch(raw_abi) is None:
        fail("AppArmor raw policy ABI is not canonical")

    entries_after, entry_metadata_after = _policy_entry_inventory(
        profiles_path,
        expected_uid=expected_uid,
        expected_gid=expected_gid,
    )
    if (
            entries_after != entries_before or
            entry_metadata_after != entry_metadata):
        fail("AppArmor policy profile inventory changed during validation")
    for path, expected in watched.items():
        try:
            observed = _policy_metadata(os.lstat(path))
        except OSError as error:
            fail(f"AppArmor policy evidence disappeared: {error.strerror}")
        if observed != expected:
            fail("AppArmor policy evidence changed during validation")

    profile_sha256 = "sha256:" + scalar_values["sha256"]
    canonical_raw_sha256 = "sha256:" + raw_sha256
    if (
            provenance.profile != APPARMOR_PROFILE or
            provenance.profile_sha256 != profile_sha256 or
            provenance.raw_sha256 != canonical_raw_sha256 or
            provenance.raw_abi != raw_abi):
        fail("AppArmor provenance does not bind the loaded policy")
    return {
        "profile": APPARMOR_PROFILE,
        "mode": "enforce",
        "attach": APPARMOR_ATTACH,
        "learning_count": 0,
        "profile_sha256": profile_sha256,
        "raw_sha256": canonical_raw_sha256,
        "raw_abi": raw_abi,
        "raw_data_id": raw_id,
        "raw_data_size": watched[raw_directory / "raw_data"][6],
        "policy_entry": entry_name,
        "profile_inventory_count": len(entries_before),
        "profile_inventory_sha256": _policy_inventory_sha256(entries_before),
        "policy_content_attested": True,
        "reviewed_provenance": provenance.report_record(),
    }


def validate_apparmor_policy(
        provenance: ReviewedAppArmorProvenance,
) -> dict[str, Any]:
    anchor = _open_apparmor_kernel_anchor()
    try:
        record = _validate_apparmor_policy_tree(
            provenance,
            policy_root=anchor.policy_root,
            expected_uid=0,
            expected_gid=0,
            expected_symlink_mode=0o444,
        )
        anchor_after = _apparmor_kernel_anchor_record(anchor.descriptor)
        if anchor_after != anchor.record:
            fail("AppArmor kernel AAFS anchor changed during validation")
        return {
            **record,
            "kernel_anchor": anchor.record,
            "kernel_aafs_attested": True,
        }
    finally:
        anchor.close()


def validate_local_docker_socket() -> dict[str, Any]:
    try:
        metadata = os.lstat(DOCKER_SOCKET)
    except OSError as error:
        fail(f"local Docker socket is unavailable: {error.strerror}")
    if (
            not stat.S_ISSOCK(metadata.st_mode) or metadata.st_nlink != 1 or
            metadata.st_uid != 0 or stat.S_IMODE(metadata.st_mode) & 0o002):
        fail("local Docker socket metadata is unsafe")
    client = command(["docker", "--version"], timeout=15).stdout.strip()
    if not client.startswith("Docker version "):
        fail("Docker CLI identity is invalid")
    return {
        "socket_owner_root": True,
        "socket_world_writable": False,
        "client": client[:128],
    }


def _docker_daemon_process_record(pid: int) -> dict[str, Any]:
    if type(pid) is not int or pid <= 1 or pid > 4_194_304:
        fail("Docker daemon PID is outside its strict bound")
    process_directory = Path(f"/proc/{pid}")
    try:
        process_metadata = os.lstat(process_directory)
    except OSError as error:
        fail(f"cannot inspect attested Docker daemon PID: {error.strerror}")
    if (
            not stat.S_ISDIR(process_metadata.st_mode) or
            process_metadata.st_uid != 0 or process_metadata.st_gid != 0 or
            stat.S_IMODE(process_metadata.st_mode) != 0o555):
        fail("attested Docker daemon process metadata is invalid")
    stat_line, stat_metadata = _read_policy_scalar(
        process_directory / "stat",
        expected_uid=0,
        expected_gid=0,
    )
    comm, comm_metadata = _read_policy_scalar(
        process_directory / "comm",
        expected_uid=0,
        expected_gid=0,
        expected_mode=0o644,
    )
    prefix = f"{pid} ("
    closing = stat_line.rfind(") ")
    if not stat_line.startswith(prefix) or closing <= len(prefix):
        fail("Docker daemon process stat record is malformed")
    fields = stat_line[closing + 2:].split(" ")
    if (
            len(fields) < 20 or any(not field for field in fields) or
            len(fields[0]) != 1 or
            not fields[0].isalpha() or
            re.fullmatch(r"[1-9][0-9]*", fields[19]) is None or
            comm != "dockerd"):
        fail("attested Docker daemon process identity is invalid")
    return {
        "pid": pid,
        "start_time_ticks": int(fields[19]),
        "comm": "dockerd",
        "process_inode": process_metadata.st_ino,
        "stat_metadata_sha256": "sha256:" + hashlib.sha256(
            json.dumps(
                [list(stat_metadata), list(comm_metadata)],
                separators=(",", ":"),
            ).encode("ascii")
        ).hexdigest(),
    }


def _current_boot_id() -> str:
    boot_id, _metadata = _read_policy_scalar(
        Path("/proc/sys/kernel/random/boot_id"),
        expected_uid=0,
        expected_gid=0,
    )
    if BOOT_ID.fullmatch(boot_id) is None:
        fail("host boot ID is not canonical")
    return boot_id


def _docker_daemon_id() -> str:
    completed = command(
        docker_cli("info", "--format", "{{json .ID}}"),
        timeout=30,
    )
    output = completed.stdout
    if (
            not output.endswith("\n") or output.count("\n") != 1 or
            len(output.encode("utf-8")) > 1024):
        fail("Docker daemon ID response is not one bounded JSON line")
    try:
        daemon_id = json.loads(output)
    except json.JSONDecodeError as error:
        raise GateError("Docker daemon ID response is invalid JSON") from error
    if (
            type(daemon_id) is not str or
            DOCKER_DAEMON_ID.fullmatch(daemon_id) is None):
        fail("Docker daemon ID response is invalid")
    return daemon_id


def validate_docker_apparmor_namespace_binding(
        provenance: ReviewedDockerAppArmorNamespaceProvenance,
        apparmor_record: dict[str, Any],
) -> dict[str, Any]:
    if type(provenance) is not ReviewedDockerAppArmorNamespaceProvenance:
        fail("Docker daemon AppArmor namespace provenance GO is required")
    if type(apparmor_record) is not dict:
        fail("kernel AppArmor policy record is required")
    try:
        namespace = apparmor_record["kernel_anchor"]["namespace"]
    except (KeyError, TypeError) as error:
        raise GateError(
            "kernel AppArmor namespace record is incomplete") from error
    expected_namespace = {
        "name": provenance.host_namespace_name,
        "level": provenance.host_namespace_level,
        "stacked": provenance.host_namespace_stacked,
    }
    if (
            type(namespace) is not dict or
            {key: namespace.get(key) for key in expected_namespace} !=
                expected_namespace):
        fail("Docker namespace provenance does not bind the host AA namespace")
    process_before = _docker_daemon_process_record(
        provenance.docker_daemon_pid)
    boot_before = _current_boot_id()
    daemon_id = _docker_daemon_id()
    process_after = _docker_daemon_process_record(
        provenance.docker_daemon_pid)
    boot_after = _current_boot_id()
    if (
            process_before != process_after or boot_before != boot_after or
            process_before["start_time_ticks"] !=
                provenance.docker_daemon_start_time_ticks or
            boot_before != provenance.host_boot_id or
            daemon_id != provenance.docker_daemon_id):
        fail("Docker daemon process or AppArmor namespace binding drifted")
    return {
        "docker_daemon_id": daemon_id,
        "docker_daemon_pid": process_before["pid"],
        "docker_daemon_start_time_ticks":
            process_before["start_time_ticks"],
        "docker_daemon_comm": process_before["comm"],
        "docker_daemon_process_inode": process_before["process_inode"],
        "docker_daemon_process_metadata_sha256":
            process_before["stat_metadata_sha256"],
        "host_boot_id": boot_before,
        "host_namespace": expected_namespace,
        "daemon_namespace": {
            "name": provenance.daemon_namespace_name,
            "level": provenance.daemon_namespace_level,
            "stacked": provenance.daemon_namespace_stacked,
        },
        "same_apparmor_namespace_attested": True,
        "reviewed_provenance": provenance.report_record(),
    }


def validate_report_path(path: Path, build: Path) -> Path:
    root = repository_root()
    absolute = Path(os.path.abspath(path))
    parent = absolute.parent.resolve(strict=True)
    allowed = parent in {root / "runtime-logs", build}
    if (
            not allowed or
            re.fullmatch(
                r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}\.json",
                absolute.name) is None):
        fail("report path is outside the explicit output allowlist")
    metadata = os.lstat(parent)
    if (
            not stat.S_ISDIR(metadata.st_mode) or
            stat.S_IMODE(metadata.st_mode) & 0o022):
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
    if report.get("schema") == SCHEMA and "decision" in report:
        review = report.get("environment_review_closure")
        expected_commit = (
            str(review.get("source_commit"))
            if isinstance(review, dict) else "0" * 40)
        validate_environment_review_report_binding(
            report, expected_source_commit=expected_commit)
    payload = (
        json.dumps(report, indent=2, sort_keys=True, ensure_ascii=True) +
        "\n").encode("utf-8")
    if len(payload) > MAX_REPORT_BYTES:
        fail("gate report exceeds size limit")
    parent = os.open(
        path.parent,
        os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC |
        getattr(os, "O_NOFOLLOW", 0),
    )
    temporary = "." + path.name + ".tmp-" + uuid.uuid4().hex
    descriptor: Optional[int] = None
    try:
        descriptor = os.open(
            temporary,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_CLOEXEC,
            0o600,
            dir_fd=parent,
        )
        view = memoryview(payload)
        while view:
            written = os.write(descriptor, view)
            if written <= 0:
                fail("short report write")
            view = view[written:]
        os.fsync(descriptor)
        os.close(descriptor)
        descriptor = None
        os.chmod(temporary, 0o600, dir_fd=parent, follow_symlinks=False)
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


def failure_report(error: Exception, progress: Progress) -> dict[str, Any]:
    no_container = not progress.container_start_attempted
    zero_or_unknown: int | str = 0 if no_container else "unknown"
    false_or_unknown: bool | str = False if no_container else "unknown"
    return {
        "schema": SCHEMA,
        "passed": False,
        "decision": "NO_GO",
        "certification_ready": False,
        "certification_blockers": list(CERTIFICATION_BLOCKERS),
        "certification_level": "offline-disposable-container-rehearsal",
        "production_eligible": False,
        "environment_review_closure": None,
        "error_type": type(error).__name__,
        "error": str(error).replace(str(repository_root()), ".")[:512],
        "failure_stage": {
            "phase": progress.phase,
            "docker_api_touched": progress.docker_api_touched,
            "image_build_started": progress.image_build_started,
            "container_start_attempted": progress.container_start_attempted,
            "inner_gate_started": progress.inner_gate_started,
            "owned_docker_objects_cleanup_complete":
                progress.owned_docker_objects_cleanup_complete,
            "completed_checks": list(progress.completed_checks),
        },
        "builder_cache_cleanup": (
            "state-volume-removed"
            if progress.builder_cache_cleanup_complete
            else "incomplete-or-not-created"
        ),
        "boundary": {
            "host_hepta_units_started": False,
            "host_bind_mounts": 0,
            "real_broker_connections": zero_or_unknown,
            "paper_orders": zero_or_unknown,
            "paper_authorized": false_or_unknown,
            "live_authorized": false_or_unknown,
            "ib_adapter_staged": False,
        },
    }


def persist_post_cleanup_attestations(
        report: dict[str, Any],
        progress: Progress,
        *,
        apparmor_after: dict[str, Any],
        docker_namespace_after: dict[str, Any],
) -> None:
    if (
            type(report) is not dict or report.get("passed") is not True or
            report.get("owned_docker_objects_cleanup_complete") is not True or
            not progress.owned_docker_objects_cleanup_complete or
            type(apparmor_after) is not dict or
            type(docker_namespace_after) is not dict or
            progress.apparmor != apparmor_after or
            progress.docker_apparmor_namespace != docker_namespace_after or
            "apparmor_revalidated" not in progress.completed_checks or
            "docker_apparmor_namespace_revalidated" not in
                progress.completed_checks):
        fail("post-cleanup attestation cannot be persisted without exact proof")
    report["apparmor_post_cleanup"] = apparmor_after
    report["apparmor_revalidated"] = True
    report["apparmor_records_equal"] = True
    report["docker_apparmor_namespace_post_cleanup"] = (
        docker_namespace_after)
    report["docker_apparmor_namespace_revalidated"] = True
    report["docker_apparmor_namespace_records_equal"] = True
    report["completed_checks"] = list(progress.completed_checks)


def verify_environment_review_for_arguments(
        args: argparse.Namespace, *, base_image: str,
        buildkit_image: str) -> ROOT_REVIEW.VerificationSession:
    if args.environment_review is None:
        fail("certification requires the signed environment review closure")
    expected_commit = args.expected_source_commit
    if not isinstance(expected_commit, str) or re.fullmatch(
            r"[0-9a-f]{40}", expected_commit) is None:
        fail("certification requires an exact external source commit pin")
    root = repository_root()
    head = command(
        ["git", "-C", str(root), "rev-parse", "HEAD"], timeout=15
    ).stdout.strip()
    status = command(
        ["git", "-C", str(root), "status", "--porcelain=v1",
         "--untracked-files=all"], timeout=30).stdout
    if os.geteuid() != 0 or head != expected_commit or status != "":
        fail("certification requires root and clean externally pinned source")
    try:
        session = ROOT_REVIEW.verify_review_closure(
            inputs=args.environment_review, base_image=base_image,
            buildkit_image=buildkit_image, repository_root=root,
            expected_source_commit=expected_commit)
    except ROOT_REVIEW.ReviewClosureError as error:
        raise GateError(str(error)) from error
    bindings = {
        "base": (
            args.reviewed_base_provenance,
            args.reviewed_base_provenance_sha256),
        "builder": (
            args.reviewed_builder_provenance,
            args.reviewed_builder_provenance_sha256),
        "apparmor": (
            args.apparmor_provenance, args.apparmor_provenance_sha256),
        "docker_namespace": (
            args.docker_apparmor_namespace_provenance,
            args.docker_apparmor_namespace_provenance_sha256),
    }
    for kind, (path, digest) in bindings.items():
        reference = session.output_reference(kind)
        if reference["path"] != str(path) or reference["file_sha256"] != digest:
            fail("signed review closure does not bind certifying " + kind)
    return session


def validate_environment_review_report_binding(
        report: dict[str, Any], *, expected_source_commit: str) -> None:
    record = report.get("environment_review_closure")
    if report.get("decision") != "GO":
        if record is not None or report.get("certification_ready") is not False:
            fail("non-certifying Agent OS report contains review closure evidence")
        return
    try:
        ROOT_REVIEW.validate_verification_record(record)
    except ROOT_REVIEW.ReviewClosureError as error:
        raise GateError(str(error)) from error
    if not isinstance(record, dict):
        fail("certifying Agent OS report lacks review closure evidence")
    base = report.get("base_image")
    builder = report.get("builder")
    buildkit = builder.get("buildkit_image") if isinstance(builder, dict) else None
    if (
            report.get("passed") is not True or
            report.get("production_eligible") is not True or
            report.get("certification_ready") is not True or
            record.get("source_commit") != expected_source_commit or
            not isinstance(base, dict) or
            record.get("base_image_reference") != base.get("reference") or
            not isinstance(buildkit, dict) or
            record.get("buildkit_image_reference") != buildkit.get("reference")):
        fail("Agent OS GO is not exactly bound to the signed review closure")


def execute(args: argparse.Namespace, progress: Progress) -> dict[str, Any]:
    started = time.monotonic()
    base = require_pinned_image(args.base_image)
    buildkit = require_pinned_image(args.buildkit_image)
    certifying = bool(getattr(args, "certify", False))
    if certifying and (args.allow_candidate_base or args.allow_candidate_builder):
        fail("certification cannot use candidate base or builder semantics")
    if certifying:
        progress.environment_review_session = (
            verify_environment_review_for_arguments(
                args, base_image=base, buildkit_image=buildkit))
    apparmor_provenance = reviewed_apparmor_provenance_from_arguments(
        args.apparmor_provenance,
        args.apparmor_provenance_sha256,
    )
    progress.apparmor_provenance = apparmor_provenance
    docker_namespace_provenance = (
        reviewed_docker_apparmor_namespace_provenance_from_arguments(
            args.docker_apparmor_namespace_provenance,
            args.docker_apparmor_namespace_provenance_sha256,
        )
    )
    progress.docker_apparmor_namespace_provenance = (
        docker_namespace_provenance)
    reviewed_provenance = reviewed_base_provenance_from_arguments(
        args.reviewed_base_provenance,
        args.reviewed_base_provenance_sha256,
        allow_candidate=args.allow_candidate_base,
    )
    reviewed_builder_provenance = (
        reviewed_builder_provenance_from_arguments(
            args.reviewed_builder_provenance,
            args.reviewed_builder_provenance_sha256,
            args.buildx_binary_sha256,
            allow_candidate=args.allow_candidate_builder,
        )
    )
    progress.phase = "isolated_builder_attestation"
    builder_record = builder_execution_record(
        reviewed_builder_provenance,
        allow_candidate=args.allow_candidate_builder,
    )
    progress.completed_checks.append("isolated_builder_contract")
    build, binaries, build_record = validate_build(args.build_dir)
    input_paths = sorted(
        {source for source, _mode in staged_sources(binaries).values()} |
        {source for source, _mode in gate_sources().values()} |
        provisioning_sources(),
        key=lambda path: str(path),
    )
    executable_inputs = set(binaries.values())
    inputs_before = [
        stable_record(path, executable=path in executable_inputs)
        for path in input_paths
    ]
    progress.completed_checks.append("local_inputs")

    apparmor_record = validate_apparmor_policy(apparmor_provenance)
    progress.apparmor = apparmor_record
    progress.completed_checks.append("apparmor_policy_attested")
    docker_socket = validate_local_docker_socket()
    initialize_docker_config()
    progress.docker_api_touched = True
    docker_namespace_record = validate_docker_apparmor_namespace_binding(
        docker_namespace_provenance,
        apparmor_record,
    )
    progress.docker_apparmor_namespace = docker_namespace_record
    progress.completed_checks.append("docker_apparmor_namespace_attested")
    buildx_toolchain_record = inspect_buildx_toolchain(
        args.buildx_binary_sha256,
        reviewed_builder_provenance,
    )
    progress.completed_checks.append("buildx_toolchain_attested")
    buildkit_record = ensure_buildkit_image(
        buildkit,
        reviewed_provenance=reviewed_builder_provenance,
        allow_candidate=args.allow_candidate_builder,
    )
    progress.completed_checks.append("pinned_local_buildkit")
    base_record = ensure_base_image(
        base,
        allow_candidate=args.allow_candidate_base,
        reviewed_provenance=reviewed_provenance,
    )
    progress.completed_checks.append("pinned_local_base")

    run_id = uuid.uuid4().hex
    tag = f"hepta/agent-os-rootful-e2e:{run_id}"
    name = f"hepta-agent-os-e2e-{run_id}"
    holder_name = f"hepta-agent-os-base-rootfs-{run_id}"
    builder_names = isolated_builder_names(run_id)
    image_id: Optional[str] = None
    container_id: Optional[str] = None
    holder_id: Optional[str] = None
    result: Optional[dict[str, Any]] = None
    container_record: Optional[dict[str, Any]] = None
    holder_record: Optional[dict[str, Any]] = None
    rootfs_record: Optional[dict[str, Any]] = None
    image_cleanup_labels: Optional[dict[str, str]] = None
    built_image_record: Optional[dict[str, Any]] = None
    cleanup_record: Optional[dict[str, Any]] = None
    isolated_builder_record: Optional[dict[str, Any]] = None
    builder_cache_before_cleanup: Optional[dict[str, Any]] = None
    builder_stopped_record: Optional[dict[str, Any]] = None
    builder_cleanup_record: Optional[dict[str, Any]] = None
    require_docker_absent("image", tag)
    require_docker_absent("container", name)
    require_docker_absent("container", holder_name)
    try:
        with tempfile.TemporaryDirectory(
                prefix="hepta-agent-os-e2e-context-") as temporary:
            context = Path(temporary)
            os.chmod(context, 0o700)
            validate_private_directory(context)

            progress.phase = "local_base_rootfs_snapshot"
            holder_id, holder_record = create_base_holder(
                base_record["id"], holder_name, run_id)
            rootfs_path = context / "base-rootfs.tar"
            rootfs_record = export_base_rootfs(holder_id, rootfs_path)
            verify_private_rootfs_tar_unchanged(
                rootfs_path, rootfs_record)
            progress.completed_checks.append("local_base_rootfs_snapshot")

            staged_records, _allowlist = provision_context(context, binaries)
            if staged_records != inputs_before:
                fail("staged input record set changed before Docker build")
            verify_private_rootfs_tar_unchanged(
                rootfs_path, rootfs_record)

            progress.phase = "isolated_builder_setup"
            isolated_builder_record = create_isolated_builder(
                builder_names,
                run_id,
                buildkit_record,
                reviewed_builder_provenance,
            )
            builder_record.update({
                "toolchain": buildx_toolchain_record,
                "buildkit_image": buildkit_record,
                "objects": isolated_builder_record,
            })
            progress.completed_checks.append("isolated_builder_started")

            progress.phase = "offline_image_build"
            progress.image_build_started = True
            image_cleanup_labels = built_image_labels(
                run_id, base_record["id"], rootfs_record["sha256"])
            command(docker_build_arguments(
                context,
                tag,
                base,
                base_record["id"],
                rootfs_record["sha256"],
                run_id,
                builder_names["builder"],
            ), timeout=900)
            verify_private_rootfs_tar_unchanged(
                rootfs_path, rootfs_record)
            inspected_output = command(
                docker_cli("image", "inspect", tag), timeout=30).stdout
            try:
                inspected_records = json.loads(
                    inspected_output,
                    object_pairs_hook=reject_duplicate_json_keys,
                )
            except json.JSONDecodeError as error:
                raise GateError(
                    "built image inspection is invalid JSON") from error
            if (
                    type(inspected_records) is not list or
                    len(inspected_records) != 1):
                fail("built image inspection cardinality mismatch")
            built_image_record = validate_built_image_record(
                inspected_records[0],
                tag=tag,
                base_image_id=base_record["id"],
                rootfs_sha256=rootfs_record["sha256"],
                run_id=run_id,
            )
            image_id = built_image_record["id"]
            builder_cache_before_cleanup = buildx_cache_record(
                builder_names["builder"])
            builder_stopped_record = stop_isolated_builder(
                isolated_builder_record,
                run_id,
                buildkit_record,
            )
            progress.completed_checks.append("isolated_builder_stopped")
            require_exact_image_id_present(base_record["id"])
            require_exact_image_id_present(buildkit_record["id"])
            if inspect_base_holder(
                    holder_id, holder_name, base_record["id"], run_id
                    ) != holder_record:
                fail("base holder identity changed during the local build")
            cleanup_container(
                holder_name,
                holder_id,
                base_record["id"],
                expected_role=BASE_HOLDER_ROLE,
                expected_run_id=run_id,
            )
            progress.completed_checks.append("offline_image_build")

            progress.phase = "container_start"
            progress.container_start_attempted = True
            run = command(
                docker_run_arguments(image_id, name, run_id),
                timeout=90,
            )
            container_id = run.stdout.strip()
            if re.fullmatch(r"[0-9a-f]{64}", container_id) is None:
                fail("Docker run did not return a canonical container ID")
            container_record = validate_container(
                container_id, name, image_id, run_id)
            progress.completed_checks.append("container_isolation")

            progress.phase = "systemd_pid1_ready"
            deadline = time.monotonic() + 45
            while True:
                ready = command(docker_cli(
                    "exec", container_id,
                    "systemctl", "show", "--property=Version", "--value",
                ), check=False, timeout=10)
                if ready.returncode == 0 and ready.stdout.strip():
                    break
                if time.monotonic() >= deadline:
                    fail("systemd PID 1 did not become ready")
                time.sleep(0.25)
            progress.completed_checks.append("systemd_pid1")

            progress.phase = "inner_four_uid_watch_e2e"
            progress.inner_gate_started = True
            inner = command(docker_cli(
                "exec", container_id,
                "python3",
                "/usr/local/libexec/hepta_agent_os_rootful_inner_gate.py",
            ), check=False, timeout=360)
            if inner.returncode != 0:
                fail(f"inner Agent OS E2E gate exited {inner.returncode}")
            result = parse_inner_result(inner.stdout)
            progress.completed_checks.append("four_uid_watch_runtime")

            progress.phase = "container_stop"
            command(
                docker_cli("stop", "--time", "20", container_id),
                timeout=45,
            )
    finally:
        cleanup_errors: list[Exception] = []
        try:
            builder_cleanup_record = cleanup_isolated_builder(
                builder_names,
                run_id,
                buildkit_record,
                (
                    isolated_builder_record.get("container_id")
                    if isolated_builder_record is not None else None
                ),
            )
            progress.builder_cache_cleanup_complete = True
            progress.completed_checks.append("isolated_builder_cache_removed")
        except Exception as error:
            cleanup_errors.append(error)
        try:
            cleanup_record = cleanup_gate_docker_objects(
                runtime_name=name,
                runtime_id=container_id,
                built_tag=tag,
                built_image_id=image_id,
                built_labels=image_cleanup_labels,
                holder_name=holder_name,
                holder_id=holder_id,
                base_image_id=base_record["id"],
                run_id=run_id,
            )
        except Exception as error:
            cleanup_errors.append(error)
        if cleanup_errors:
            raise GateError(
                "isolated builder or disposable runtime cleanup failed: "
                f"{cleanup_errors[0]}") from cleanup_errors[0]
        progress.owned_docker_objects_cleanup_complete = True

    if (
            result is None or container_record is None or image_id is None or
            holder_record is None or rootfs_record is None or
            image_cleanup_labels is None or built_image_record is None or
            cleanup_record is None or isolated_builder_record is None or
            builder_cache_before_cleanup is None or
            builder_stopped_record is None or
            builder_cleanup_record is None):
        fail("gate completed without a validated inner result")
    inputs_after = [
        stable_record(path, executable=path in executable_inputs)
        for path in input_paths
    ]
    if inputs_after != inputs_before:
        fail("gate inputs changed during execution")
    progress.phase = "complete"
    builder_record["builder_cache_before_cleanup"] = (
        builder_cache_before_cleanup)
    builder_record["builder_stopped"] = builder_stopped_record
    builder_record["builder_cache_cleanup"] = "state-volume-removed"
    builder_record["cleanup"] = builder_cleanup_record
    builder_record["cleanup_complete"] = True
    return {
        "schema": SCHEMA,
        "passed": True,
        "decision": "GO" if certifying else "REHEARSAL_ONLY",
        "certification_ready": certifying,
        "certification_blockers": (
            [] if certifying else list(CERTIFICATION_BLOCKERS)),
        "certification_level": (
            "externally-reviewed-rootful-systemd-certification"
            if certifying else
            "non-production-offline-disposable-container-rehearsal"),
        "production_eligible": certifying,
        "environment_review_closure": None,
        "duration_ms": int((time.monotonic() - started) * 1000),
        "build": build_record,
        "builder": builder_record,
        "base_image": base_record,
        "docker_host": docker_socket,
        "apparmor": apparmor_record,
        "docker_apparmor_namespace": docker_namespace_record,
        "image": {
            "id": image_id,
            "purpose": PURPOSE,
            "role": BUILT_IMAGE_ROLE,
            "run_id": run_id,
            "build_network": "none",
            "cache_reuse": "disabled",
            "builder_cache_cleanup": "state-volume-removed",
            "source_image_id": base_record["id"],
            "base_rootfs_sha256": rootfs_record["sha256"],
            "base_rootfs_size": rootfs_record["size"],
            "base_construction_version": BASE_CONSTRUCTION_VERSION,
            "labels": image_cleanup_labels,
            "repo_tags": built_image_record["repo_tags"],
            "repo_digests": built_image_record["repo_digests"],
        },
        "base_holder": holder_record,
        "container": container_record,
        "inner": result,
        "inputs": inputs_before,
        "input_stability": True,
        "owned_docker_objects_cleanup_complete": True,
        "owned_docker_objects_cleanup": cleanup_record,
        "boundary": {
            "host_hepta_units_started": False,
            "host_bind_mounts": 0,
            "real_broker_connections": 0,
            "paper_orders": 0,
            "paper_authorized": False,
            "live_authorized": False,
            "ib_adapter_staged": False,
            "container_network": "none",
        },
    }


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "offline disposable four-UID Agent OS systemd E2E gate"))
    parser.add_argument("--build-dir", type=Path, required=True)
    parser.add_argument("--run", action="store_true")
    parser.add_argument("--certify", action="store_true")
    parser.add_argument("--expected-source-commit")
    parser.add_argument("--base-image", required=True)
    parser.add_argument(
        "--buildkit-image",
        required=True,
        help=(
            "preloaded BuildKit image pinned as an exact RepoDigest; "
            "the runner never pulls it"),
    )
    parser.add_argument(
        "--reviewed-base-provenance",
        type=Path,
        help=(
            "external strict JSON GO record binding the reviewed base image; "
            "required unless an explicit development candidate is allowed"),
    )
    parser.add_argument(
        "--reviewed-base-provenance-sha256",
        help=(
            "independent sha256:<64 lowercase hex> binding for "
            "--reviewed-base-provenance"),
    )
    parser.add_argument(
        "--reviewed-builder-provenance",
        type=Path,
        help=(
            "external strict JSON GO record binding the exact BuildKit "
            "image, buildx binary/version, and Docker server semantics"),
    )
    parser.add_argument(
        "--reviewed-builder-provenance-sha256",
        help=(
            "independent sha256:<64 lowercase hex> binding for "
            "--reviewed-builder-provenance"),
    )
    parser.add_argument(
        "--buildx-binary-sha256",
        required=True,
        help=(
            "independent sha256:<64 lowercase hex> binding for the "
            "Docker-selected buildx plugin binary"),
    )
    parser.add_argument(
        "--apparmor-provenance",
        type=Path,
        help=(
            "external strict JSON GO record binding the exact loaded "
            "hepta-systemd-gate AppArmor policy; always required"),
    )
    parser.add_argument(
        "--apparmor-provenance-sha256",
        help=(
            "independent sha256:<64 lowercase hex> binding for "
            "--apparmor-provenance"),
    )
    parser.add_argument(
        "--docker-apparmor-namespace-provenance",
        type=Path,
        help=(
            "external strict JSON GO record binding the exact Docker daemon "
            "process to the current root AppArmor namespace; always required"),
    )
    parser.add_argument(
        "--docker-apparmor-namespace-provenance-sha256",
        help=(
            "independent sha256:<64 lowercase hex> binding for "
            "--docker-apparmor-namespace-provenance"),
    )
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument(
        "--allow-candidate-base",
        action="store_true",
        help=(
            "explicitly allow the content-addressed development base; "
            "the report remains a non-production container rehearsal"),
    )
    parser.add_argument(
        "--allow-candidate-builder",
        action="store_true",
        help=(
            "explicitly allow an unreviewed but digest-pinned, preloaded "
            "isolated BuildKit image; never production eligible"),
    )
    ROOT_REVIEW.add_arguments(parser)
    arguments = parser.parse_args(argv)
    if not arguments.run:
        print(
            "hepta_agent_os_rootful_e2e_gate: disabled; pass --run explicitly",
            file=sys.stderr)
        return 78
    try:
        arguments.environment_review = ROOT_REVIEW.inputs_from_arguments(
            arguments, certify=arguments.certify)
    except ROOT_REVIEW.ReviewClosureError as error:
        print(
            "hepta_agent_os_rootful_e2e_gate: unsafe inputs: " + str(error),
            file=sys.stderr)
        return 2
    try:
        build = arguments.build_dir.resolve(strict=True)
        safe_report = validate_report_path(arguments.report, build)
    except Exception as error:
        print(
            "hepta_agent_os_rootful_e2e_gate: unsafe inputs: " + str(error),
            file=sys.stderr,
        )
        return 2
    progress = Progress()
    report: dict[str, Any]
    exit_code = 0
    execution_error: Optional[Exception] = None
    try:
        report = execute(arguments, progress)
    except Exception as error:
        execution_error = error
        exit_code = 1
        report = failure_report(error, progress)
    if (
            progress.apparmor is not None and
            progress.apparmor_provenance is not None):
        try:
            apparmor_after = validate_apparmor_policy(
                progress.apparmor_provenance)
            if apparmor_after != progress.apparmor:
                fail("AppArmor policy contract changed during the gate")
            progress.completed_checks.append("apparmor_revalidated")
            docker_namespace_after: Optional[dict[str, Any]] = None
            if (
                    progress.docker_apparmor_namespace is not None and
                    progress.docker_apparmor_namespace_provenance is not None):
                docker_namespace_after = (
                    validate_docker_apparmor_namespace_binding(
                        progress.docker_apparmor_namespace_provenance,
                        apparmor_after,
                    )
                )
                if (
                        docker_namespace_after !=
                        progress.docker_apparmor_namespace):
                    fail(
                        "Docker AppArmor namespace binding changed during "
                        "the gate")
                progress.completed_checks.append(
                    "docker_apparmor_namespace_revalidated")
            elif execution_error is None:
                fail("Docker AppArmor namespace proof is missing")
            if execution_error is not None:
                report = failure_report(execution_error, progress)
            else:
                if docker_namespace_after is None:
                    fail("Docker AppArmor namespace revalidation is missing")
                persist_post_cleanup_attestations(
                    report,
                    progress,
                    apparmor_after=apparmor_after,
                    docker_namespace_after=docker_namespace_after,
                )
        except Exception as error:
            execution_error = error
            exit_code = 1
            report = failure_report(error, progress)
    elif exit_code == 0:
        execution_error = GateError(
            "AppArmor post-cleanup revalidation state is missing")
        exit_code = 1
        report = failure_report(execution_error, progress)
    if exit_code == 0 and arguments.certify:
        try:
            if progress.environment_review_session is None:
                fail("certifying environment review session is missing")
            progress.environment_review_session.reopen_at_gate_end()
            record = progress.environment_review_session.report_record()
            ROOT_REVIEW.validate_verification_record(record)
            report["environment_review_closure"] = record
            progress.completed_checks.append(
                "environment_review_closure_reopened")
            report["completed_checks"] = list(progress.completed_checks)
            validate_environment_review_report_binding(
                report, expected_source_commit=arguments.expected_source_commit)
        except (GateError, ROOT_REVIEW.ReviewClosureError) as error:
            execution_error = error
            exit_code = 1
            report = failure_report(error, progress)
    try:
        cleanup_docker_config()
    except Exception as error:
        exit_code = 1
        report = failure_report(error, progress)
        report["error"] = "isolated Docker configuration cleanup failed"
    try:
        atomic_report(safe_report, report)
    except Exception as error:
        print(
            "hepta_agent_os_rootful_e2e_gate: report failure: " + str(error),
            file=sys.stderr,
        )
        return 2
    if exit_code:
        print("hepta_agent_os_rootful_e2e_gate: FAIL", file=sys.stderr)
        return exit_code
    print(
        "hepta_agent_os_rootful_e2e_gate: PASS "
        "level=" + str(report["certification_level"]) + " "
        "agent_uid=2004 gateway_uid=2001 simulator_uid=2002 "
        "ib_uid_reserved=2003 paper_authorized=false live_authorized=false")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
