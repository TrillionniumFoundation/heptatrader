#!/usr/bin/env python3

"""Run the broker-free P1 WATCH/PAPER dual-domain rootful gate.

This is an opt-in, disposable effective-systemd gate.  It builds only from an
already-present digest-pinned image, with build and runtime networking set to
``none``.  The container has a read-only root filesystem, a fixed tmpfs
allowlist, no host bind mounts, no devices and no published ports.  The PAPER
plane is an inert fixture: the gate never stages an IB adapter, an order API,
real credentials or trading authority.

The receipt is deliberately narrower than PAPER admission.  Rehearsal is the
default.  Formal ``--certify`` consumes four root-owned canonical provenance
documents and uses the reviewed isolated builder; only a clean frozen source,
stable provenance, enforcing AppArmor/daemon binding, complete inner evidence
and exact cleanup can produce this gate's GO.  It always leaves PAPER, LIVE,
mutation and direct-broker authorization false.
"""

from __future__ import annotations

import argparse
import ctypes
from dataclasses import dataclass
import errno
import hashlib
import json
import os
from pathlib import Path
import re
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


SCHEMA = "hepta.p1-dual-domain-rootful-gate.v1"
INNER_SCHEMA = "hepta.p1-dual-domain-rootful-inner.v1"
INNER_MARKER = "HEPTA_P1_DUAL_DOMAIN_ROOTFUL_RESULT="
PURPOSE = "p1-dual-domain-rootful-gate"
PURPOSE_LABEL = f"io.hepta.purpose={PURPOSE}"
RUN_LABEL_KEY = "io.hepta.run-id"
APPARMOR_PROFILE = "hepta-systemd-gate"
CERTIFICATION_BLOCKERS = (
    ROOT_REVIEW.CERTIFICATION_BLOCKER,
    "reviewed-base-image-provenance-required",
    "reviewed-apparmor-profile-provenance-required",
    "reviewed-isolated-builder-buildx-provenance-required",
    "reviewed-docker-daemon-apparmor-namespace-boot-provenance-required",
)
REVIEWED_BASE_PROVENANCE_SCHEMA = (
    "hepta.agent-os-rootful-systemd-base-reviewed-provenance.v1")
REVIEWED_BUILDER_PROVENANCE_SCHEMA = (
    "hepta.agent-os-rootful-systemd-isolated-builder-"
    "reviewed-provenance.v1")
REVIEWED_APPARMOR_PROVENANCE_SCHEMA = (
    "hepta.agent-os-rootful-systemd-apparmor-reviewed-provenance.v1")
REVIEWED_DOCKER_NAMESPACE_PROVENANCE_SCHEMA = (
    "hepta.agent-os-rootful-systemd-docker-apparmor-namespace-"
    "reviewed-provenance.v1")
REVIEWED_BASE_KEYS = frozenset({
    "schema", "decision", "issued_at_ms", "expires_at_ms", "image_id",
    "repo_digest", "labels_sha256"})
REVIEWED_BASE_LABELS = {
    "io.hepta.rootful-systemd-base.offline-ready": "true",
    "io.hepta.rootful-systemd-base.version": "1",
}
REVIEWED_BUILDER_KEYS = frozenset({
    "schema", "decision", "issued_at_ms", "expires_at_ms", "image_id",
    "repo_digest", "config_sha256",
    "buildkit_version", "buildx_version", "buildx_binary_sha256",
    "docker_server_version", "docker_server_api_version",
    "docker_server_git_commit"})
REVIEWED_APPARMOR_KEYS = frozenset({
    "schema", "decision", "issued_at_ms", "expires_at_ms", "profile",
    "policy_source_sha256",
    "profile_sha256", "raw_sha256", "raw_abi"})
REVIEWED_DOCKER_NAMESPACE_KEYS = frozenset({
    "schema", "decision", "issued_at_ms", "expires_at_ms",
    "docker_daemon_id", "docker_daemon_pid",
    "docker_daemon_start_time_ticks", "host_boot_id",
    "host_namespace_name", "host_namespace_level",
    "host_namespace_stacked", "daemon_namespace_name",
    "daemon_namespace_level", "daemon_namespace_stacked"})
SEMANTIC_VERSION = re.compile(
    r"^[0-9]+\.[0-9]+\.[0-9]+(?:[-+][0-9A-Za-z._-]+)?$")
BUILDKIT_VERSION = re.compile(
    r"^v[0-9]+\.[0-9]+\.[0-9]+(?:[-+][0-9A-Za-z._-]+)?$")
DOCKER_API_VERSION = re.compile(r"^[1-9][0-9]*\.[0-9]+$")
SAFE_BUILD_ID = re.compile(r"^[0-9A-Za-z][0-9A-Za-z._+-]{0,127}$")
DOCKER_DAEMON_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9:._-]{0,127}$")
APPARMOR_RAW_ABI = re.compile(r"^v[1-9][0-9]{0,2}$")
APPARMOR_POLICY_ENTRY = re.compile(r"^[A-Za-z0-9_.:@+=-]{1,255}$")
MAX_PROVENANCE = 64 * 1024
MAX_PROVENANCE_LIFETIME_MS = 24 * 60 * 60 * 1000
REHEARSAL_REPORT_LIFETIME_MS = 5 * 60 * 1000
MAX_APPARMOR_SCALAR = 4096
MAX_TOOL_BINARY = 256 * 1024 * 1024
APPARMOR_CONTROL_ROOT = Path("/sys/kernel/security/apparmor")
APPARMOR_POLICY_ROOT = APPARMOR_CONTROL_ROOT / "policy"
DOCKER_SOCKET = Path("/run/docker.sock")
BUILDER_ROLE_LABEL = "io.hepta.role"
BUILDER_RUN_LABEL = "io.hepta.run-id"
BUILDER_IMAGE_LABEL = "io.hepta.buildkit-image-id"
BUILDER_NAME_LABEL = "io.hepta.buildx-builder"
BUILDER_DAEMON_ROLE = "isolated-buildkit-daemon"
BUILDER_STATE_ROLE = "isolated-buildkit-state"
BUILDKIT_STATE_DIRECTORY = "/var/lib/buildkit"
PINNED_IMAGE = re.compile(
    r"^[a-z0-9][a-z0-9._/:-]*@sha256:[0-9a-f]{64}$")
CANONICAL_COMMIT = re.compile(r"^[0-9a-f]{40}$")
CANONICAL_IMAGE_ID = re.compile(r"^sha256:[0-9a-f]{64}$")
CANONICAL_BOOT_ID = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-"
    r"[0-9a-f]{4}-[0-9a-f]{12}$")
MAX_INPUT = 4 * 1024 * 1024
MAX_OUTPUT = 4 * 1024 * 1024
MAX_REPORT = 4 * 1024 * 1024
RENAME_NOREPLACE = 1
_LIBC = ctypes.CDLL(None, use_errno=True)
COMMAND_ENV = {
    "PATH": "/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin",
    "LANG": "C",
    "LC_ALL": "C",
    "TZ": "UTC",
}
RUNTIME_CAPABILITIES = (
    "CHOWN", "DAC_OVERRIDE", "FOWNER", "KILL", "MKNOD", "SETGID",
    "SETPCAP", "SETUID", "SYS_ADMIN", "SYS_CHROOT",
)
RUNTIME_TMPFS = {
    "/etc/heptatrader": "rw,nosuid,nodev,noexec,mode=0755,size=8m",
    "/run": "rw,nosuid,nodev,mode=0755,size=64m",
    "/run/lock": "rw,nosuid,nodev,noexec,mode=0755,size=8m",
    "/tmp": "rw,nosuid,nodev,noexec,mode=1777,size=64m",
    "/var/lib": "rw,nosuid,nodev,noexec,mode=0755,size=64m",
    "/var/log": "rw,nosuid,nodev,noexec,mode=0755,size=32m",
    "/var/tmp": "rw,nosuid,nodev,noexec,mode=1777,size=32m",
}

SOURCE_FILES = {
    "scripts/run_hepta_p1_dual_domain_rootful_gate.py":
        ("gate-inputs/runner.py", 0o644),
    "scripts/hepta_rootful_review_closure_consumer.py":
        ("gate-inputs/hepta_rootful_review_closure_consumer.py", 0o644),
    "tests/p1_dual_domain_rootful_systemd/Dockerfile":
        ("tests/p1_dual_domain_rootful_systemd/Dockerfile", 0o644),
    "tests/p1_dual_domain_rootful_systemd/"
    "hepta-p1-dual-domain-systemd-entrypoint":
        ("tests/p1_dual_domain_rootful_systemd/"
         "hepta-p1-dual-domain-systemd-entrypoint", 0o755),
    "tests/p1_dual_domain_rootful_systemd/"
    "hepta-p1-dual-domain-rootful.target":
        ("install-root/usr/lib/systemd/system/"
         "hepta-p1-dual-domain-rootful.target", 0o644),
    "tests/p1_dual_domain_rootful_systemd/"
    "hepta_p1_dual_domain_daemon.py":
        ("install-root/usr/libexec/hepta-p1-dual-domain-daemon", 0o755),
    "tests/p1_dual_domain_rootful_systemd/"
    "hepta_p1_dual_domain_inner_gate.py":
        ("tests/p1_dual_domain_rootful_systemd/"
         "hepta_p1_dual_domain_inner_gate.py", 0o755),
    "tests/p1_dual_domain_rootful_systemd/"
    "hepta-p1-dual-watch@.service":
        ("install-root/usr/lib/systemd/system/"
         "hepta-p1-dual-watch@.service", 0o644),
    "tests/p1_dual_domain_rootful_systemd/"
    "hepta-p1-dual-watch@.socket":
        ("install-root/usr/lib/systemd/system/"
         "hepta-p1-dual-watch@.socket", 0o644),
    "tests/p1_dual_domain_rootful_systemd/"
    "hepta-p1-dual-paper@.service":
        ("install-root/usr/lib/systemd/system/"
         "hepta-p1-dual-paper@.service", 0o644),
    "tests/p1_dual_domain_rootful_systemd/"
    "hepta-p1-dual-paper@.socket":
        ("install-root/usr/lib/systemd/system/"
         "hepta-p1-dual-paper@.socket", 0o644),
}

EXPECTED_IDENTITIES: list[dict[str, object]] = [
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

EXPECTED_CHECKS = frozenset({
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
})

EXPECTED_BOUNDARY = {
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

EXPECTED_FAULTS = {
    "watchdog_timeout": ("WATCH", "codex-a"),
    "service_crash_restart": ("PAPER_INERT", "openclaw-b"),
    "socket_reactivation": ("PAPER_INERT", "codex-a"),
}

_DOCKER_CONFIG: Optional[tempfile.TemporaryDirectory[str]] = None


@dataclass(frozen=True)
class CertificationRequest:
    buildkit_image: str
    buildx_binary_sha256: str
    reviewed_base_path: Path
    reviewed_base_sha256: str
    reviewed_builder_path: Path
    reviewed_builder_sha256: str
    reviewed_apparmor_path: Path
    reviewed_apparmor_sha256: str
    reviewed_docker_namespace_path: Path
    reviewed_docker_namespace_sha256: str
    environment_review: Optional[ROOT_REVIEW.ReviewClosureInputs] = None


@dataclass(frozen=True)
class RootProvenanceDocument:
    kind: str
    path: Path
    document_sha256: str
    body: dict[str, object]
    metadata: tuple[int, ...]

    def report_record(self) -> dict[str, object]:
        return {
            **self.body,
            "document_sha256": self.document_sha256,
            "root_owned": True,
            "canonical_json": True,
            "mode": "0400",
            "identity_sha256": "sha256:" + hashlib.sha256(
                json.dumps(
                    list(self.metadata), separators=(",", ":")
                ).encode("ascii")
            ).hexdigest(),
        }


class GateError(RuntimeError):
    """A fail-closed gate error."""


def fail(message: str) -> None:
    raise GateError(message)


def repository_root() -> Path:
    return Path(__file__).resolve(strict=True).parents[1]


def require_pinned_image(value: str) -> str:
    if PINNED_IMAGE.fullmatch(value) is None:
        fail("--base-image must be exact lowercase name@sha256:<64 hex>")
    return value


def require_expected_commit(value: str) -> str:
    if CANONICAL_COMMIT.fullmatch(value) is None:
        fail("--expected-source-commit must be 40 lowercase hex characters")
    return value


def architecture_matches(image_architecture: str, host_architecture: str) -> bool:
    aliases = {"x86_64": "amd64", "aarch64": "arm64"}
    return (
        image_architecture == host_architecture or
        aliases.get(host_architecture) == image_architecture)


def command(
        arguments: list[str], *, timeout: int = 120,
        check: bool = True) -> subprocess.CompletedProcess[str]:
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
        close_fds=True,
    )
    try:
        output, _unused = process.communicate(timeout=timeout)
    except BaseException:
        if process.poll() is None:
            try:
                os.killpg(process.pid, signal.SIGTERM)
                process.wait(timeout=5)
            except (OSError, subprocess.TimeoutExpired):
                if process.poll() is None:
                    os.killpg(process.pid, signal.SIGKILL)
                    process.wait()
        raise
    if len(output.encode("utf-8", errors="replace")) > MAX_OUTPUT:
        fail("bounded command output exceeded")
    completed = subprocess.CompletedProcess(
        arguments, process.returncode, output, None)
    if check and completed.returncode != 0:
        tail = output[-2048:].replace("\n", " | ")
        fail(f"command failed rc={completed.returncode}: {tail}")
    return completed


def initialize_docker_config() -> None:
    global _DOCKER_CONFIG
    if _DOCKER_CONFIG is not None:
        fail("Docker configuration already initialized")
    _DOCKER_CONFIG = tempfile.TemporaryDirectory(
        prefix="hepta-p1-dual-domain-docker-")
    os.chmod(_DOCKER_CONFIG.name, 0o700)


def cleanup_docker_config() -> None:
    global _DOCKER_CONFIG
    holder = _DOCKER_CONFIG
    _DOCKER_CONFIG = None
    if holder is not None:
        holder.cleanup()


def docker_cli(*arguments: str) -> list[str]:
    if _DOCKER_CONFIG is None:
        fail("Docker configuration is not initialized")
    return [
        "docker", "--config", _DOCKER_CONFIG.name,
        "--host=unix:///run/docker.sock", *arguments,
    ]


def object_owned(
        value: object, run_id: str,
        expected_image_id: Optional[str] = None) -> bool:
    if not isinstance(value, dict):
        return False
    config = value.get("Config")
    if not isinstance(config, dict):
        return False
    labels = config.get("Labels")
    if (
            not isinstance(labels, dict) or
            labels.get("io.hepta.purpose") != PURPOSE or
            labels.get(RUN_LABEL_KEY) != run_id):
        return False
    return (
        expected_image_id is None or value.get("Id") == expected_image_id)


def read_stable(path: Path) -> tuple[bytes, dict[str, object]]:
    descriptor = os.open(
        path, os.O_RDONLY | os.O_CLOEXEC | getattr(os, "O_NOFOLLOW", 0))
    try:
        before = os.fstat(descriptor)
        if (
                not stat.S_ISREG(before.st_mode) or before.st_nlink != 1 or
                before.st_size < 1 or before.st_size > MAX_INPUT or
                stat.S_IMODE(before.st_mode) & 0o002):
            fail(f"unsafe input metadata: {path}")
        chunks: list[bytes] = []
        total = 0
        while True:
            chunk = os.read(descriptor, min(65536, MAX_INPUT + 1 - total))
            if not chunk:
                break
            chunks.append(chunk)
            total += len(chunk)
            if total > MAX_INPUT:
                fail(f"input exceeds bound: {path}")
        after = os.fstat(descriptor)
    finally:
        os.close(descriptor)
    fields = (
        "st_dev", "st_ino", "st_mode", "st_nlink", "st_uid", "st_gid",
        "st_size", "st_mtime_ns", "st_ctime_ns")
    if any(getattr(before, field) != getattr(after, field) for field in fields):
        fail(f"input changed while reading: {path}")
    raw = b"".join(chunks)
    return raw, {
        "sha256": hashlib.sha256(raw).hexdigest(),
        "size": len(raw),
        "mode": format(stat.S_IMODE(before.st_mode), "04o"),
    }


def write_exact(path: Path, raw: bytes, mode: int) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor = os.open(
        path,
        os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_CLOEXEC |
        getattr(os, "O_NOFOLLOW", 0),
        mode,
    )
    try:
        view = memoryview(raw)
        while view:
            written = os.write(descriptor, view)
            if written <= 0:
                fail("short context write")
            view = view[written:]
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
    os.chmod(path, mode)


def canonical_json(value: object) -> bytes:
    return (
        json.dumps(value, sort_keys=True, separators=(",", ":")) + "\n"
    ).encode("utf-8")


def reject_duplicate_json_keys(
        pairs: list[tuple[str, object]]) -> dict[str, object]:
    record: dict[str, object] = {}
    for key, value in pairs:
        if key in record:
            fail("JSON object contains duplicate field: " + key)
        record[key] = value
    return record


def canonical_sha256(value: object) -> str:
    return "sha256:" + hashlib.sha256(canonical_json(value)).hexdigest()


def require_sha256(value: str, label: str) -> str:
    if re.fullmatch(r"sha256:[0-9a-f]{64}", value) is None:
        fail(label + " must be sha256:<64 lowercase hex>")
    return value


PROVENANCE_FILE_FIELDS = (
    "st_dev", "st_ino", "st_mode", "st_nlink", "st_uid", "st_gid",
    "st_size", "st_mtime_ns", "st_ctime_ns",
)
PROVENANCE_DIRECTORY_FIELDS = (
    "st_dev", "st_ino", "st_mode", "st_nlink", "st_uid", "st_gid",
    "st_mtime_ns", "st_ctime_ns",
)


def metadata_identity(
        metadata: os.stat_result, fields: tuple[str, ...]) -> tuple[int, ...]:
    return tuple(int(getattr(metadata, field)) for field in fields)


def validate_provenance_directory_metadata(
        metadata: os.stat_result, kind: str) -> tuple[int, ...]:
    if (
            not stat.S_ISDIR(metadata.st_mode) or metadata.st_nlink < 1 or
            metadata.st_uid != 0 or metadata.st_gid != 0 or
            stat.S_IMODE(metadata.st_mode) & 0o022):
        fail(kind + " provenance ancestor is not a fixed root-owned directory")
    return metadata_identity(metadata, PROVENANCE_DIRECTORY_FIELDS)


def validate_provenance_file_metadata(
        metadata: os.stat_result, kind: str) -> tuple[int, ...]:
    if (
            not stat.S_ISREG(metadata.st_mode) or metadata.st_nlink != 1 or
            metadata.st_uid != 0 or metadata.st_gid != 0 or
            stat.S_IMODE(metadata.st_mode) != 0o400 or
            metadata.st_size < 2 or metadata.st_size > MAX_PROVENANCE):
        fail(kind + " provenance metadata is not root:root 0400 regular")
    return metadata_identity(metadata, PROVENANCE_FILE_FIELDS)


def read_provenance_descriptor(
        descriptor: int, *, expected_size: int, kind: str) -> bytes:
    chunks: list[bytes] = []
    total = 0
    while True:
        chunk = os.read(
            descriptor, min(8192, MAX_PROVENANCE + 1 - total))
        if not chunk:
            break
        chunks.append(chunk)
        total += len(chunk)
        if total > MAX_PROVENANCE:
            fail(kind + " provenance exceeds bound")
    if total != expected_size:
        fail(kind + " provenance size changed while reading")
    return b"".join(chunks)


def read_anchored_root_provenance(
        path: Path, *, kind: str) -> tuple[bytes, tuple[int, ...]]:
    path_value = os.fspath(path)
    if (
            not isinstance(path, Path) or not path.is_absolute() or
            path_value != os.path.normpath(path_value) or
            path.name in ("", ".", "..") or
            any(part in ("", ".", "..") for part in path.parts[1:])):
        fail(kind + " provenance path must be absolute lexical-canonical")
    directory_descriptors: list[int] = []
    directory_links: list[tuple[int, str, int, tuple[int, ...]]] = []
    file_descriptor: Optional[int] = None
    reopened_descriptor: Optional[int] = None
    try:
        root_descriptor = os.open(
            "/", os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC |
            getattr(os, "O_NOFOLLOW", 0))
        directory_descriptors.append(root_descriptor)
        validate_provenance_directory_metadata(
            os.fstat(root_descriptor), kind)
        current = root_descriptor
        for component in path.parts[1:-1]:
            before = os.stat(
                component, dir_fd=current, follow_symlinks=False)
            before_identity = validate_provenance_directory_metadata(
                before, kind)
            opened = os.open(
                component,
                os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC |
                getattr(os, "O_NOFOLLOW", 0),
                dir_fd=current)
            directory_descriptors.append(opened)
            opened_identity = validate_provenance_directory_metadata(
                os.fstat(opened), kind)
            named_after = validate_provenance_directory_metadata(
                os.stat(component, dir_fd=current, follow_symlinks=False), kind)
            if not (
                    before_identity == opened_identity == named_after):
                fail(kind + " provenance ancestor changed while anchoring")
            directory_links.append(
                (current, component, opened, opened_identity))
            current = opened

        final_name = path.name
        named_before = os.stat(
            final_name, dir_fd=current, follow_symlinks=False)
        file_identity = validate_provenance_file_metadata(named_before, kind)
        file_descriptor = os.open(
            final_name,
            os.O_RDONLY | os.O_CLOEXEC | getattr(os, "O_NOFOLLOW", 0),
            dir_fd=current)
        opened_identity = validate_provenance_file_metadata(
            os.fstat(file_descriptor), kind)
        named_opened = validate_provenance_file_metadata(
            os.stat(final_name, dir_fd=current, follow_symlinks=False), kind)
        if not file_identity == opened_identity == named_opened:
            fail(kind + " provenance path changed while opening")
        raw = read_provenance_descriptor(
            file_descriptor, expected_size=named_before.st_size, kind=kind)
        read_identity = validate_provenance_file_metadata(
            os.fstat(file_descriptor), kind)
        if read_identity != file_identity:
            fail(kind + " provenance file changed while reading")

        reopened_descriptor = os.open(
            final_name,
            os.O_RDONLY | os.O_CLOEXEC | getattr(os, "O_NOFOLLOW", 0),
            dir_fd=current)
        reopened_identity = validate_provenance_file_metadata(
            os.fstat(reopened_descriptor), kind)
        reopened_raw = read_provenance_descriptor(
            reopened_descriptor, expected_size=named_before.st_size, kind=kind)
        named_reopened = validate_provenance_file_metadata(
            os.stat(final_name, dir_fd=current, follow_symlinks=False), kind)
        if (
                reopened_identity != file_identity or
                named_reopened != file_identity or reopened_raw != raw):
            fail(kind + " provenance changed across anchored reopen")
        for parent, component, opened, expected in directory_links:
            if (
                    validate_provenance_directory_metadata(
                        os.fstat(opened), kind) != expected or
                    validate_provenance_directory_metadata(
                        os.stat(
                            component, dir_fd=parent,
                            follow_symlinks=False), kind) != expected):
                fail(kind + " provenance ancestor changed after reopen")
        return raw, file_identity
    except GateError:
        raise
    except OSError as error:
        raise GateError(kind + " provenance anchored open failed") from error
    finally:
        if reopened_descriptor is not None:
            os.close(reopened_descriptor)
        if file_descriptor is not None:
            os.close(file_descriptor)
        for descriptor in reversed(directory_descriptors):
            os.close(descriptor)


def read_root_canonical_provenance(
        path: Path, expected_sha256: str, *, kind: str,
        expected_schema: str, expected_keys: frozenset[str],
        ) -> RootProvenanceDocument:
    require_sha256(expected_sha256, kind + " provenance digest")
    if os.geteuid() != 0:
        fail("certifying provenance consumption requires effective UID 0")
    raw, metadata = read_anchored_root_provenance(path, kind=kind)
    observed_sha256 = "sha256:" + hashlib.sha256(raw).hexdigest()
    if observed_sha256 != expected_sha256:
        fail(kind + " provenance document digest mismatch")
    try:
        document = json.loads(
            raw.decode("utf-8", errors="strict"),
            object_pairs_hook=reject_duplicate_json_keys)
    except GateError:
        raise
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise GateError(kind + " provenance JSON is invalid") from error
    if (
            not isinstance(document, dict) or set(document) != expected_keys or
            document.get("schema") != expected_schema or
            document.get("decision") != "GO" or
            canonical_json(document) != raw):
        fail(kind + " provenance canonical contract mismatch")
    return RootProvenanceDocument(
        kind=kind,
        path=path,
        document_sha256=expected_sha256,
        body=document,
        metadata=metadata,
    )


def validate_provenance_time(
        body: dict[str, object], *, now_ms: int) -> None:
    issued = body.get("issued_at_ms")
    expires = body.get("expires_at_ms")
    if (
            type(issued) is not int or type(expires) is not int or
            issued < 0 or issued > now_ms or expires <= issued or
            now_ms >= expires or
            expires - issued > MAX_PROVENANCE_LIFETIME_MS):
        fail("reviewed provenance is outside its bounded validity window")


def validate_base_provenance(document: RootProvenanceDocument) -> None:
    body = document.body
    string_fields = REVIEWED_BASE_KEYS - {"issued_at_ms", "expires_at_ms"}
    if (
            any(type(body.get(field)) is not str for field in string_fields) or
            CANONICAL_IMAGE_ID.fullmatch(str(body.get("image_id", ""))) is None or
            PINNED_IMAGE.fullmatch(str(body.get("repo_digest", ""))) is None or
            re.fullmatch(
                r"sha256:[0-9a-f]{64}",
                str(body.get("labels_sha256", ""))) is None or
            body.get("labels_sha256") !=
            canonical_object_sha256(REVIEWED_BASE_LABELS)):
        fail("reviewed base provenance values are invalid")


def validate_builder_provenance(document: RootProvenanceDocument) -> None:
    body = document.body
    string_fields = REVIEWED_BUILDER_KEYS - {"issued_at_ms", "expires_at_ms"}
    if any(type(body.get(field)) is not str for field in string_fields):
        fail("reviewed builder provenance values are not strings")
    if (
            CANONICAL_IMAGE_ID.fullmatch(str(body["image_id"])) is None or
            PINNED_IMAGE.fullmatch(str(body["repo_digest"])) is None or
            any(re.fullmatch(r"sha256:[0-9a-f]{64}", str(body[field])) is None
                for field in ("config_sha256", "buildx_binary_sha256")) or
            BUILDKIT_VERSION.fullmatch(str(body["buildkit_version"])) is None or
            SEMANTIC_VERSION.fullmatch(str(body["buildx_version"])) is None or
            SEMANTIC_VERSION.fullmatch(
                str(body["docker_server_version"])) is None or
            DOCKER_API_VERSION.fullmatch(
                str(body["docker_server_api_version"])) is None or
            SAFE_BUILD_ID.fullmatch(
                str(body["docker_server_git_commit"])) is None):
        fail("reviewed builder provenance values are invalid")


def validate_apparmor_provenance(document: RootProvenanceDocument) -> None:
    body = document.body
    string_fields = REVIEWED_APPARMOR_KEYS - {"issued_at_ms", "expires_at_ms"}
    if any(type(body.get(field)) is not str for field in string_fields):
        fail("reviewed AppArmor provenance values are not strings")
    if (
            body.get("profile") != APPARMOR_PROFILE or
            any(re.fullmatch(r"sha256:[0-9a-f]{64}", str(body[field])) is None
                for field in (
                    "policy_source_sha256", "profile_sha256", "raw_sha256")) or
            APPARMOR_RAW_ABI.fullmatch(str(body["raw_abi"])) is None):
        fail("reviewed AppArmor provenance values are invalid")


def validate_docker_namespace_provenance(
        document: RootProvenanceDocument) -> None:
    body = document.body
    string_fields = (
        "schema", "decision", "docker_daemon_id", "host_boot_id",
        "host_namespace_name", "daemon_namespace_name")
    integer_fields = (
        "issued_at_ms", "expires_at_ms",
        "docker_daemon_pid", "docker_daemon_start_time_ticks",
        "host_namespace_level", "daemon_namespace_level")
    boolean_fields = (
        "host_namespace_stacked", "daemon_namespace_stacked")
    if (
            any(type(body.get(field)) is not str for field in string_fields) or
            any(type(body.get(field)) is not int for field in integer_fields) or
            any(type(body.get(field)) is not bool for field in boolean_fields)):
        fail("reviewed Docker namespace provenance field types are invalid")
    if (
            DOCKER_DAEMON_ID.fullmatch(str(body["docker_daemon_id"])) is None or
            body["docker_daemon_pid"] <= 1 or
            body["docker_daemon_pid"] > 4_194_304 or
            body["docker_daemon_start_time_ticks"] <= 0 or
            CANONICAL_BOOT_ID.fullmatch(str(body["host_boot_id"])) is None or
            body["host_namespace_name"] != "root" or
            body["host_namespace_level"] != 0 or
            body["host_namespace_stacked"] is not False or
            body["daemon_namespace_name"] != "root" or
            body["daemon_namespace_level"] != 0 or
            body["daemon_namespace_stacked"] is not False):
        fail("reviewed Docker namespace provenance values are invalid")


def load_certification_provenance(
        request: CertificationRequest, *, now_ms: Optional[int] = None,
        ) -> dict[str, RootProvenanceDocument]:
    observed_at_ms = (
        int(time.time() * 1000) if now_ms is None else now_ms)
    if type(observed_at_ms) is not int or observed_at_ms < 0:
        fail("certification provenance observation time is invalid")
    specs = {
        "base": (
            request.reviewed_base_path, request.reviewed_base_sha256,
            REVIEWED_BASE_PROVENANCE_SCHEMA, REVIEWED_BASE_KEYS,
            validate_base_provenance),
        "builder": (
            request.reviewed_builder_path, request.reviewed_builder_sha256,
            REVIEWED_BUILDER_PROVENANCE_SCHEMA, REVIEWED_BUILDER_KEYS,
            validate_builder_provenance),
        "apparmor": (
            request.reviewed_apparmor_path, request.reviewed_apparmor_sha256,
            REVIEWED_APPARMOR_PROVENANCE_SCHEMA, REVIEWED_APPARMOR_KEYS,
            validate_apparmor_provenance),
        "docker_namespace": (
            request.reviewed_docker_namespace_path,
            request.reviewed_docker_namespace_sha256,
            REVIEWED_DOCKER_NAMESPACE_PROVENANCE_SCHEMA,
            REVIEWED_DOCKER_NAMESPACE_KEYS,
            validate_docker_namespace_provenance),
    }
    paths = [os.fspath(value[0]) for value in specs.values()]
    digests = [str(value[1]) for value in specs.values()]
    if (
            len(set(paths)) != len(specs) or
            len(set(digests)) != len(specs)):
        fail("four independent provenance paths and document pins are required")
    documents: dict[str, RootProvenanceDocument] = {}
    for kind, (path, digest, schema, keys, validator) in specs.items():
        document = read_root_canonical_provenance(
            path, digest, kind=kind,
            expected_schema=schema, expected_keys=keys)
        validator(document)
        validate_provenance_time(document.body, now_ms=observed_at_ms)
        documents[kind] = document
    file_identities = {
        (document.metadata[0], document.metadata[1])
        for document in documents.values()
    }
    if len(file_identities) != len(documents):
        fail("four provenance paths must resolve to distinct root-owned files")
    if (
            documents["builder"].body["repo_digest"] !=
            require_pinned_image(request.buildkit_image) or
            documents["builder"].body["buildx_binary_sha256"] !=
            require_sha256(request.buildx_binary_sha256, "buildx binary digest")):
        fail("reviewed builder provenance does not bind requested toolchain")
    return documents


def certification_request_from_values(
        *, certify: bool, buildkit_image: Optional[str],
        buildx_binary_sha256: Optional[str],
        reviewed_base_path: Optional[Path], reviewed_base_sha256: Optional[str],
        reviewed_builder_path: Optional[Path],
        reviewed_builder_sha256: Optional[str],
        reviewed_apparmor_path: Optional[Path],
        reviewed_apparmor_sha256: Optional[str],
        reviewed_docker_namespace_path: Optional[Path],
        reviewed_docker_namespace_sha256: Optional[str],
        environment_review: Optional[ROOT_REVIEW.ReviewClosureInputs] = None,
        ) -> Optional[CertificationRequest]:
    values = (
        buildkit_image, buildx_binary_sha256,
        reviewed_base_path, reviewed_base_sha256,
        reviewed_builder_path, reviewed_builder_sha256,
        reviewed_apparmor_path, reviewed_apparmor_sha256,
        reviewed_docker_namespace_path, reviewed_docker_namespace_sha256,
        environment_review)
    if not certify:
        if any(value is not None for value in values):
            fail("certifying provenance arguments require explicit --certify")
        return None
    if any(value is None for value in values):
        fail(
            "--certify requires the signed review closure, all four "
            "provenance documents, and tool pins")
    assert environment_review is not None
    return CertificationRequest(
        buildkit_image=require_pinned_image(str(buildkit_image)),
        buildx_binary_sha256=require_sha256(
            str(buildx_binary_sha256), "buildx binary digest"),
        reviewed_base_path=reviewed_base_path,
        reviewed_base_sha256=require_sha256(
            str(reviewed_base_sha256), "reviewed base provenance digest"),
        reviewed_builder_path=reviewed_builder_path,
        reviewed_builder_sha256=require_sha256(
            str(reviewed_builder_sha256), "reviewed builder provenance digest"),
        reviewed_apparmor_path=reviewed_apparmor_path,
        reviewed_apparmor_sha256=require_sha256(
            str(reviewed_apparmor_sha256), "AppArmor provenance digest"),
        reviewed_docker_namespace_path=reviewed_docker_namespace_path,
        reviewed_docker_namespace_sha256=require_sha256(
            str(reviewed_docker_namespace_sha256),
            "Docker namespace provenance digest"),
        environment_review=environment_review,
    )


def verify_environment_review_for_request(
        request: CertificationRequest, *, base_image: str,
        source_commit: str) -> ROOT_REVIEW.VerificationSession:
    if request.environment_review is None:
        fail("signed environment review closure inputs are missing")
    try:
        session = ROOT_REVIEW.verify_review_closure(
            inputs=request.environment_review, base_image=base_image,
            buildkit_image=request.buildkit_image,
            repository_root=repository_root(),
            expected_source_commit=source_commit)
    except ROOT_REVIEW.ReviewClosureError as error:
        raise GateError(str(error)) from error
    bindings = {
        "base": (request.reviewed_base_path, request.reviewed_base_sha256),
        "builder": (
            request.reviewed_builder_path, request.reviewed_builder_sha256),
        "apparmor": (
            request.reviewed_apparmor_path, request.reviewed_apparmor_sha256),
        "docker_namespace": (
            request.reviewed_docker_namespace_path,
            request.reviewed_docker_namespace_sha256),
    }
    for kind, (path, digest) in bindings.items():
        reference = session.output_reference(kind)
        if reference["path"] != str(path) or reference["file_sha256"] != digest:
            fail("signed review closure does not bind certifying " + kind)
    return session


def read_kernel_scalar(path: Path, *, maximum: int = MAX_APPARMOR_SCALAR) -> str:
    descriptor = os.open(
        path, os.O_RDONLY | os.O_CLOEXEC | getattr(os, "O_NOFOLLOW", 0))
    try:
        before = os.fstat(descriptor)
        if (
                not stat.S_ISREG(before.st_mode) or before.st_nlink != 1 or
                before.st_uid != 0 or before.st_gid != 0 or
                stat.S_IMODE(before.st_mode) not in (0o444, 0o644) or
                before.st_size < 0 or before.st_size > maximum):
            fail("kernel evidence scalar metadata mismatch: " + str(path))
        chunks: list[bytes] = []
        total = 0
        while True:
            chunk = os.read(descriptor, min(1024, maximum + 1 - total))
            if not chunk:
                break
            chunks.append(chunk)
            total += len(chunk)
            if total > maximum:
                fail("kernel evidence scalar exceeds bound")
        after = os.fstat(descriptor)
    finally:
        os.close(descriptor)
    fields = (
        "st_dev", "st_ino", "st_mode", "st_nlink", "st_uid", "st_gid",
        "st_mtime_ns", "st_ctime_ns")
    if any(getattr(before, field) != getattr(after, field) for field in fields):
        fail("kernel evidence scalar changed while reading")
    raw = b"".join(chunks)
    if (
            not raw or not raw.endswith(b"\n") or raw.count(b"\n") != 1 or
            b"\x00" in raw):
        fail("kernel evidence scalar is not one canonical line")
    try:
        value = raw[:-1].decode("ascii", errors="strict")
    except UnicodeDecodeError as error:
        raise GateError("kernel evidence scalar is not ASCII") from error
    if not value or value != value.strip():
        fail("kernel evidence scalar value is invalid")
    return value


def validate_loaded_apparmor(
        provenance: RootProvenanceDocument) -> dict[str, object]:
    if provenance.kind != "apparmor":
        fail("reviewed AppArmor provenance is required")
    enabled = read_kernel_scalar(
        Path("/sys/module/apparmor/parameters/enabled"))
    if enabled != "Y":
        fail("AppArmor kernel enforcement is not enabled")
    namespace_values = {
        "name": read_kernel_scalar(APPARMOR_CONTROL_ROOT / ".ns_name"),
        "level": read_kernel_scalar(APPARMOR_CONTROL_ROOT / ".ns_level"),
        "stacked": read_kernel_scalar(APPARMOR_CONTROL_ROOT / ".ns_stacked"),
        "legacy_stacked": read_kernel_scalar(
            APPARMOR_CONTROL_ROOT / ".stacked"),
    }
    if namespace_values != {
            "name": "root", "level": "0", "stacked": "no",
            "legacy_stacked": "no"}:
        fail("AppArmor is not in the unstacked root namespace")
    profiles_root = APPARMOR_POLICY_ROOT / "profiles"
    raw_root = APPARMOR_POLICY_ROOT / "raw_data"
    try:
        entries = sorted(os.scandir(profiles_root), key=lambda item: item.name)
    except OSError as error:
        raise GateError("cannot enumerate loaded AppArmor profiles") from error
    if not entries or len(entries) > 4096:
        fail("loaded AppArmor profile inventory outside bound")
    names: list[str] = []
    matching: list[Path] = []
    for entry in entries:
        if APPARMOR_POLICY_ENTRY.fullmatch(entry.name) is None:
            fail("loaded AppArmor profile entry name is invalid")
        entry_path = profiles_root / entry.name
        metadata = os.lstat(entry_path)
        if (
                not stat.S_ISDIR(metadata.st_mode) or metadata.st_uid != 0 or
                metadata.st_gid != 0 or stat.S_IMODE(metadata.st_mode) != 0o755):
            fail("loaded AppArmor profile directory metadata mismatch")
        profile_name = read_kernel_scalar(entry_path / "name")
        names.append(profile_name)
        if profile_name == APPARMOR_PROFILE:
            matching.append(entry_path)
    if len(matching) != 1 or names.count(APPARMOR_PROFILE) != 1:
        fail("required AppArmor profile is not uniquely loaded")
    entry = matching[0]
    values = {
        field: read_kernel_scalar(entry / field)
        for field in ("name", "mode", "attach", "learning_count", "sha256")
    }
    if (
            values["name"] != APPARMOR_PROFILE or
            values["mode"] != "enforce" or
            values["attach"] != APPARMOR_PROFILE or
            values["learning_count"] != "0" or
            re.fullmatch(r"[0-9a-f]{64}", values["sha256"]) is None):
        fail("loaded AppArmor profile is not exact enforcing policy")
    try:
        raw_data_target = os.readlink(entry / "raw_data")
        raw_sha_target = os.readlink(entry / "raw_sha256")
        raw_abi_target = os.readlink(entry / "raw_abi")
    except OSError as error:
        raise GateError("AppArmor raw policy links are unavailable") from error
    match = re.fullmatch(
        r"\.\./\.\./raw_data/([1-9][0-9]{0,19})/raw_data",
        raw_data_target)
    if match is None:
        fail("AppArmor raw policy link is malformed")
    raw_id = match.group(1)
    if (
            raw_sha_target != f"../../raw_data/{raw_id}/sha256" or
            raw_abi_target != f"../../raw_data/{raw_id}/abi"):
        fail("AppArmor raw policy link set is inconsistent")
    raw_sha = read_kernel_scalar(raw_root / raw_id / "sha256")
    raw_abi = read_kernel_scalar(raw_root / raw_id / "abi")
    if (
            re.fullmatch(r"[0-9a-f]{64}", raw_sha) is None or
            APPARMOR_RAW_ABI.fullmatch(raw_abi) is None or
            provenance.body["profile_sha256"] !=
            "sha256:" + values["sha256"] or
            provenance.body["raw_sha256"] != "sha256:" + raw_sha or
            provenance.body["raw_abi"] != raw_abi):
        fail("reviewed AppArmor provenance does not bind loaded policy")
    return {
        "profile": APPARMOR_PROFILE,
        "mode": "enforce",
        "attach": APPARMOR_PROFILE,
        "learning_count": 0,
        "profile_sha256": "sha256:" + values["sha256"],
        "raw_sha256": "sha256:" + raw_sha,
        "raw_abi": raw_abi,
        "raw_data_id": raw_id,
        "profile_inventory_count": len(entries),
        "profile_inventory_sha256": canonical_sha256(names),
        "namespace": {"name": "root", "level": 0, "stacked": False},
        "reviewed_provenance": provenance.report_record(),
    }


def current_host_boot_id() -> str:
    value = read_kernel_scalar(Path("/proc/sys/kernel/random/boot_id"))
    if CANONICAL_BOOT_ID.fullmatch(value) is None:
        fail("host boot ID is not canonical")
    return value


def docker_daemon_process_record(pid: int) -> dict[str, object]:
    if type(pid) is not int or pid <= 1 or pid > 4_194_304:
        fail("Docker daemon PID outside bound")
    process_root = Path(f"/proc/{pid}")
    metadata = os.lstat(process_root)
    if (
            not stat.S_ISDIR(metadata.st_mode) or metadata.st_uid != 0 or
            metadata.st_gid != 0):
        fail("Docker daemon process is not root-owned")
    comm = read_kernel_scalar(process_root / "comm")
    stat_line = read_kernel_scalar(process_root / "stat")
    prefix = f"{pid} ("
    closing = stat_line.rfind(") ")
    if not stat_line.startswith(prefix) or closing <= len(prefix):
        fail("Docker daemon stat record malformed")
    fields = stat_line[closing + 2:].split()
    if (
            comm != "dockerd" or len(fields) < 20 or
            re.fullmatch(r"[1-9][0-9]*", fields[19]) is None):
        fail("Docker daemon process identity mismatch")
    return {
        "pid": pid,
        "start_time_ticks": int(fields[19], 10),
        "comm": "dockerd",
        "process_inode": metadata.st_ino,
    }


def docker_daemon_id() -> str:
    output = command(docker_cli(
        "info", "--format", "{{json .ID}}"), timeout=30).stdout
    if not output.endswith("\n") or output.count("\n") != 1:
        fail("Docker daemon ID response framing mismatch")
    try:
        value = json.loads(output)
    except json.JSONDecodeError as error:
        raise GateError("Docker daemon ID response malformed") from error
    if not isinstance(value, str) or DOCKER_DAEMON_ID.fullmatch(value) is None:
        fail("Docker daemon ID response invalid")
    return value


def validate_local_docker_socket() -> dict[str, object]:
    try:
        before = os.lstat(DOCKER_SOCKET)
        after = os.lstat(DOCKER_SOCKET)
    except OSError as error:
        raise GateError("local Docker socket is unavailable") from error
    fields = ("st_dev", "st_ino", "st_mode", "st_nlink", "st_uid", "st_gid")
    if (
            any(getattr(before, field) != getattr(after, field)
                for field in fields) or
            not stat.S_ISSOCK(before.st_mode) or before.st_nlink != 1 or
            before.st_uid != 0 or stat.S_IMODE(before.st_mode) & 0o002):
        fail("local Docker socket ownership/identity is unsafe")
    return {
        "device": before.st_dev,
        "inode": before.st_ino,
        "mode": format(stat.S_IMODE(before.st_mode), "04o"),
        "uid": 0,
        "gid": before.st_gid,
        "owner_root": True,
        "world_writable": False,
    }


def validate_docker_namespace_binding(
        provenance: RootProvenanceDocument,
        apparmor: dict[str, object]) -> dict[str, object]:
    if provenance.kind != "docker_namespace":
        fail("reviewed Docker namespace provenance is required")
    if apparmor.get("namespace") != {
            "name": "root", "level": 0, "stacked": False}:
        fail("loaded AppArmor namespace evidence is incomplete")
    body = provenance.body
    before = docker_daemon_process_record(int(body["docker_daemon_pid"]))
    boot_before = current_host_boot_id()
    daemon_id = docker_daemon_id()
    after = docker_daemon_process_record(int(body["docker_daemon_pid"]))
    boot_after = current_host_boot_id()
    if (
            before != after or boot_before != boot_after or
            before["start_time_ticks"] !=
            body["docker_daemon_start_time_ticks"] or
            boot_before != body["host_boot_id"] or
            daemon_id != body["docker_daemon_id"]):
        fail("Docker daemon/AppArmor namespace/boot binding drifted")
    return {
        "docker_daemon_id": daemon_id,
        "docker_daemon_pid": before["pid"],
        "docker_daemon_start_time_ticks": before["start_time_ticks"],
        "docker_daemon_comm": "dockerd",
        "docker_daemon_process_inode": before["process_inode"],
        "host_boot_id": boot_before,
        "host_namespace": {"name": "root", "level": 0, "stacked": False},
        "daemon_namespace": {
            "name": body["daemon_namespace_name"],
            "level": body["daemon_namespace_level"],
            "stacked": body["daemon_namespace_stacked"],
        },
        "same_apparmor_namespace_attested": True,
        "reviewed_provenance": provenance.report_record(),
    }


def canonical_object_sha256(value: object) -> str:
    try:
        raw = json.dumps(
            value, sort_keys=True, separators=(",", ":"),
            ensure_ascii=True, allow_nan=False).encode("ascii")
    except (TypeError, ValueError, UnicodeEncodeError) as error:
        raise GateError("value is not canonical JSON") from error
    return "sha256:" + hashlib.sha256(raw).hexdigest()


def hash_root_owned_executable(path: Path) -> str:
    descriptor = os.open(
        path, os.O_RDONLY | os.O_CLOEXEC | getattr(os, "O_NOFOLLOW", 0))
    try:
        before = os.fstat(descriptor)
        if (
                not stat.S_ISREG(before.st_mode) or before.st_nlink != 1 or
                before.st_uid != 0 or before.st_gid != 0 or
                before.st_mode & (stat.S_IWGRP | stat.S_IWOTH) or
                not before.st_mode & stat.S_IXUSR or
                before.st_size < 1 or before.st_size > MAX_TOOL_BINARY):
            fail("buildx binary root ownership/mode/size mismatch")
        digest = hashlib.sha256()
        total = 0
        while True:
            chunk = os.read(descriptor, 1024 * 1024)
            if not chunk:
                break
            total += len(chunk)
            if total > MAX_TOOL_BINARY:
                fail("buildx binary exceeds evidence bound")
            digest.update(chunk)
        after = os.fstat(descriptor)
    finally:
        os.close(descriptor)
    named = os.lstat(path)
    fields = (
        "st_dev", "st_ino", "st_mode", "st_nlink", "st_uid", "st_gid",
        "st_size", "st_mtime_ns", "st_ctime_ns")
    if (
            total != before.st_size or
            any(getattr(before, field) != getattr(after, field) or
                getattr(before, field) != getattr(named, field)
                for field in fields)):
        fail("buildx binary changed while hashing")
    return "sha256:" + digest.hexdigest()


def validate_reviewed_base_image(
        record: dict[str, object], reference: str,
        provenance: RootProvenanceDocument) -> dict[str, object]:
    if provenance.kind != "base":
        fail("reviewed base provenance is required")
    reference = require_pinned_image(reference)
    config = record.get("Config")
    repo_digests = record.get("RepoDigests")
    if (
            not isinstance(config, dict) or
            not isinstance(repo_digests, list) or
            reference not in repo_digests or
            len(repo_digests) != len(set(repo_digests)) or
            any(not isinstance(item, str) or PINNED_IMAGE.fullmatch(item) is None
                for item in repo_digests) or
            CANONICAL_IMAGE_ID.fullmatch(str(record.get("Id", ""))) is None or
            record.get("Os") != "linux" or
            record.get("Architecture") != "amd64"):
        fail("reviewed base image inspect contract mismatch")
    on_build = config.get("OnBuild")
    volumes = config.get("Volumes")
    labels = config.get("Labels")
    if (
            "OnBuild" not in config or on_build not in (None, []) or
            volumes not in (None, {}) or
            labels != REVIEWED_BASE_LABELS or
            any(not isinstance(key, str) or not isinstance(value, str)
                for key, value in labels.items())):
        fail("reviewed base inherited config is unsafe")
    body = provenance.body
    labels_sha256 = canonical_object_sha256(labels)
    if (
            body["image_id"] != record["Id"] or
            body["repo_digest"] != reference or
            body["labels_sha256"] != labels_sha256):
        fail("reviewed base provenance does not bind inspected image")
    return {
        "reference": reference,
        "id": record["Id"],
        "repo_digests": sorted(repo_digests),
        "os": "linux",
        "architecture": record["Architecture"],
        "declared_volumes": 0,
        "onbuild_instructions": 0,
        "labels_sha256": labels_sha256,
        "production_approved": True,
        "reviewed_provenance": provenance.report_record(),
    }


def validate_reviewed_buildkit_image(
        record: dict[str, object], reference: str,
        provenance: RootProvenanceDocument) -> dict[str, object]:
    if provenance.kind != "builder":
        fail("reviewed builder provenance is required")
    reference = require_pinned_image(reference)
    config = record.get("Config")
    repo_digests = record.get("RepoDigests")
    if (
            not isinstance(config, dict) or
            not isinstance(repo_digests, list) or
            reference not in repo_digests or
            len(repo_digests) != len(set(repo_digests)) or
            any(not isinstance(item, str) or PINNED_IMAGE.fullmatch(item) is None
                for item in repo_digests) or
            CANONICAL_IMAGE_ID.fullmatch(str(record.get("Id", ""))) is None or
            record.get("Os") != "linux" or
            record.get("Architecture") != "amd64"):
        fail("reviewed BuildKit image inspect contract mismatch")
    if (
            "OnBuild" not in config or
            config.get("OnBuild") not in (None, []) or
            config.get("Volumes") not in (None, {}) or
            config.get("ExposedPorts") not in (None, {}) or
            config.get("Entrypoint") not in (
                ["buildkitd"], ["/usr/bin/buildkitd"],
                ["/usr/local/bin/buildkitd"])):
        fail("reviewed BuildKit image config is unsafe")
    labels = config.get("Labels") or {}
    if (
            not isinstance(labels, dict) or
            any(not isinstance(key, str) or not isinstance(value, str)
                for key, value in labels.items()) or
            {"io.hepta.purpose", BUILDER_ROLE_LABEL, BUILDER_RUN_LABEL,
             BUILDER_IMAGE_LABEL, BUILDER_NAME_LABEL}.intersection(labels)):
        fail("reviewed BuildKit labels collide with gate ownership")
    config_sha256 = canonical_object_sha256(config)
    body = provenance.body
    if (
            body["image_id"] != record["Id"] or
            body["repo_digest"] != reference or
            body["config_sha256"] != config_sha256):
        fail("reviewed builder provenance does not bind BuildKit image")
    image_id = str(record["Id"])
    return {
        "reference": reference,
        "id": image_id,
        "bare_id": image_id.removeprefix("sha256:"),
        "repo_digests": sorted(repo_digests),
        "os": "linux",
        "architecture": record["Architecture"],
        "config_sha256": config_sha256,
        "config_labels": dict(labels),
        "entrypoint": list(config["Entrypoint"]),
        "production_approved": True,
        "reviewed_provenance": provenance.report_record(),
    }


def inspect_single_image(reference: str) -> dict[str, object]:
    try:
        records = json.loads(command(docker_cli(
            "image", "inspect", reference), timeout=30).stdout,
            object_pairs_hook=reject_duplicate_json_keys)
    except json.JSONDecodeError as error:
        raise GateError("Docker image inspect JSON invalid") from error
    if (
            not isinstance(records, list) or len(records) != 1 or
            not isinstance(records[0], dict)):
        fail("Docker image inspect cardinality mismatch")
    return records[0]


def inspect_buildx_toolchain(
        expected_binary_sha256: str,
        provenance: RootProvenanceDocument) -> dict[str, object]:
    require_sha256(expected_binary_sha256, "buildx binary digest")
    try:
        plugins = json.loads(command(docker_cli(
            "info", "--format", "{{json .ClientInfo.Plugins}}"),
            timeout=30).stdout,
            object_pairs_hook=reject_duplicate_json_keys)
        server = json.loads(command(docker_cli(
            "version", "--format", "{{json .Server}}"),
            timeout=30).stdout,
            object_pairs_hook=reject_duplicate_json_keys)
    except json.JSONDecodeError as error:
        raise GateError("Docker/buildx toolchain JSON invalid") from error
    if not isinstance(plugins, list) or not isinstance(server, dict):
        fail("Docker/buildx toolchain inventory malformed")
    matches = [
        item for item in plugins
        if isinstance(item, dict) and item.get("Name") == "buildx"]
    if len(matches) != 1:
        fail("exactly one Docker buildx plugin is required")
    plugin = matches[0]
    path_value = plugin.get("Path")
    version = plugin.get("Version")
    if (
            not isinstance(path_value, str) or not path_value.startswith("/") or
            not isinstance(version, str) or
            SEMANTIC_VERSION.fullmatch(version) is None):
        fail("buildx plugin identity/version malformed")
    path = Path(path_value)
    observed_sha256 = hash_root_owned_executable(path)
    if observed_sha256 != expected_binary_sha256:
        fail("buildx binary root ownership/digest mismatch")
    version_output = command(docker_cli("buildx", "version"), timeout=30).stdout
    match = re.fullmatch(
        r"github\.com/docker/buildx ([0-9]+\.[0-9]+\.[0-9]+)"
        r"(?: [^\r\n]+)?\n?", version_output)
    if match is None or match.group(1) != version:
        fail("buildx command version does not bind selected plugin")
    body = provenance.body
    if (
            body["buildx_version"] != version or
            body["buildx_binary_sha256"] != observed_sha256 or
            body["docker_server_version"] != server.get("Version") or
            body["docker_server_api_version"] != server.get("ApiVersion") or
            body["docker_server_git_commit"] != server.get("GitCommit")):
        fail("reviewed builder provenance does not bind active toolchain")
    return {
        "buildx_path_sha256": "sha256:" + hashlib.sha256(
            path_value.encode("utf-8")).hexdigest(),
        "buildx_version": version,
        "buildx_binary_sha256": observed_sha256,
        "docker_server_version": server.get("Version"),
        "docker_server_api_version": server.get("ApiVersion"),
        "docker_server_git_commit": server.get("GitCommit"),
        "reviewed": True,
    }


def isolated_builder_names(run_id: str) -> dict[str, str]:
    if re.fullmatch(r"[0-9a-f]{32}", run_id) is None:
        fail("isolated builder run ID invalid")
    builder = "hepta-p1-dual-isolated-" + run_id
    node = builder + "0"
    container = "buildx_buildkit_" + node
    return {
        "builder": builder,
        "node": node,
        "container": container,
        "volume": container + "_state",
    }


def builder_metadata_path(builder_name: str) -> Path:
    if (
            _DOCKER_CONFIG is None or
            re.fullmatch(
                r"hepta-p1-dual-isolated-[0-9a-f]{32}", builder_name) is None):
        fail("isolated builder metadata identity invalid")
    return Path(_DOCKER_CONFIG.name) / "buildx" / "instances" / builder_name


def builder_metadata_exists(builder_name: str) -> bool:
    path = builder_metadata_path(builder_name)
    try:
        metadata = os.lstat(path)
    except FileNotFoundError:
        return False
    if (
            not stat.S_ISREG(metadata.st_mode) or metadata.st_nlink != 1 or
            metadata.st_uid != os.geteuid() or
            metadata.st_mode & (stat.S_IWGRP | stat.S_IWOTH)):
        fail("private buildx builder metadata ownership is unsafe")
    return True


def require_builder_metadata_absent(builder_name: str) -> None:
    if os.path.lexists(builder_metadata_path(builder_name)):
        fail("private buildx builder metadata residue remains")


def builder_labels(
        run_id: str, builder_name: str, image_id: str,
        role: str) -> dict[str, str]:
    if (
            builder_name != isolated_builder_names(run_id)["builder"] or
            CANONICAL_IMAGE_ID.fullmatch(image_id) is None or
            role not in (BUILDER_DAEMON_ROLE, BUILDER_STATE_ROLE)):
        fail("isolated builder ownership label arguments invalid")
    return {
        "io.hepta.purpose": PURPOSE,
        BUILDER_ROLE_LABEL: role,
        BUILDER_RUN_LABEL: run_id,
        BUILDER_IMAGE_LABEL: image_id,
        BUILDER_NAME_LABEL: builder_name,
    }


def create_isolated_builder_arguments(
        names: dict[str, str], run_id: str,
        buildkit: dict[str, object]) -> dict[str, list[str]]:
    if names != isolated_builder_names(run_id):
        fail("isolated builder object names invalid")
    image_id = str(buildkit.get("id", ""))
    bare_id = str(buildkit.get("bare_id", ""))
    if image_id != "sha256:" + bare_id:
        fail("isolated builder exact image ID malformed")
    state_labels = builder_labels(
        run_id, names["builder"], image_id, BUILDER_STATE_ROLE)
    daemon_labels = builder_labels(
        run_id, names["builder"], image_id, BUILDER_DAEMON_ROLE)
    volume = ["volume", "create", "--driver=local"]
    for key, value in state_labels.items():
        volume.extend(("--label", f"{key}={value}"))
    volume.append(names["volume"])
    container = [
        "container", "create", "--pull=never", "--network=none",
        "--privileged", "--init", "--restart=no",
        "--name", names["container"], "--mount",
        "type=volume,source=" + names["volume"] +
        ",target=" + BUILDKIT_STATE_DIRECTORY,
    ]
    for key, value in daemon_labels.items():
        container.extend(("--label", f"{key}={value}"))
    container.append(bare_id)
    buildx = [
        "buildx", "create", "--name", names["builder"],
        "--node", names["node"], "--driver", "docker-container",
        "--driver-opt", "image=" + bare_id +
        ",network=none,restart-policy=no,default-load=false,"
        "provenance-add-gha=false",
        "--platform", "linux/amd64",
    ]
    return {"volume": volume, "container": container, "buildx": buildx}


def validate_builder_volume_record(
        record: object, names: dict[str, str], run_id: str,
        image_id: str) -> dict[str, object]:
    expected = builder_labels(
        run_id, names["builder"], image_id, BUILDER_STATE_ROLE)
    if (
            not isinstance(record, dict) or
            record.get("Name") != names["volume"] or
            record.get("Driver") != "local" or
            record.get("Scope") != "local" or
            record.get("Labels") != expected or
            record.get("Options") not in (None, {}) or
            not isinstance(record.get("Mountpoint"), str) or
            not str(record["Mountpoint"]).startswith("/")):
        fail("isolated builder volume inspect mismatch")
    return {
        "name": names["volume"], "driver": "local", "scope": "local",
        "labels": expected,
        "mountpoint_sha256": "sha256:" + hashlib.sha256(
            str(record["Mountpoint"]).encode("utf-8")).hexdigest(),
    }


def validate_builder_container_record(
        record: object, names: dict[str, str], run_id: str,
        buildkit: dict[str, object], *, running: Optional[bool],
        ) -> dict[str, object]:
    if not isinstance(record, dict):
        fail("isolated builder container inspect malformed")
    host = record.get("HostConfig")
    config = record.get("Config")
    state = record.get("State")
    mounts = record.get("Mounts")
    image_id = str(buildkit["id"])
    bare_id = str(buildkit["bare_id"])
    ownership = builder_labels(
        run_id, names["builder"], image_id, BUILDER_DAEMON_ROLE)
    image_labels = buildkit.get("config_labels")
    if (
            not isinstance(host, dict) or not isinstance(config, dict) or
            not isinstance(state, dict) or not isinstance(mounts, list) or
            not isinstance(image_labels, dict) or
            len(mounts) != 1 or record.get("Name") != "/" + names["container"] or
            record.get("Image") != image_id or config.get("Image") != bare_id or
            config.get("Labels") != {**image_labels, **ownership} or
            (running is not None and state.get("Running") is not running)):
        fail("isolated builder container identity mismatch")
    restart = host.get("RestartPolicy") or {}
    if (
            host.get("NetworkMode") != "none" or
            host.get("Privileged") is not True or host.get("Init") is not True or
            host.get("AutoRemove") is not False or
            host.get("ReadonlyRootfs") is not False or
            restart.get("Name") != "no" or
            host.get("Binds") not in (None, []) or
            host.get("Tmpfs") not in (None, {}) or
            host.get("VolumesFrom") not in (None, []) or
            host.get("Devices") not in (None, []) or
            host.get("DeviceRequests") not in (None, []) or
            host.get("PortBindings") not in (None, {}) or
            host.get("PublishAllPorts") is not False):
        fail("isolated builder namespace/bind/device boundary mismatch")
    mount = mounts[0]
    if (
            not isinstance(mount, dict) or mount.get("Type") != "volume" or
            mount.get("Name") != names["volume"] or
            mount.get("Destination") != BUILDKIT_STATE_DIRECTORY or
            mount.get("Driver") != "local" or
            mount.get("RW") is not True):
        fail("isolated builder state mount mismatch")
    return {
        "container_id": record.get("Id"),
        "name": names["container"],
        "network_mode": "none", "privileged": True,
        "bind_mounts": 0, "devices": 0, "published_ports": 0,
        "running": state.get("Running"), "labels": ownership,
    }


def inspect_json_list(
        arguments: list[str], label: str, *, check: bool = True,
        ) -> tuple[subprocess.CompletedProcess[str], Optional[dict[str, object]]]:
    completed = command(arguments, check=check, timeout=30)
    if completed.returncode != 0:
        return completed, None
    try:
        values = json.loads(
            completed.stdout, object_pairs_hook=reject_duplicate_json_keys)
    except json.JSONDecodeError as error:
        raise GateError(label + " inspect JSON invalid") from error
    if (
            not isinstance(values, list) or len(values) != 1 or
            not isinstance(values[0], dict)):
        fail(label + " inspect cardinality mismatch")
    return completed, values[0]


def require_inspect_absent(arguments: list[str], label: str) -> None:
    completed = command(arguments, check=False, timeout=20)
    if (
            completed.returncode != 1 or
            "no such" not in completed.stdout.lower()):
        fail(label + " absence could not be established")


def require_builder_absent(names: dict[str, str]) -> None:
    for arguments, label in (
            (docker_cli("container", "inspect", names["container"]),
             "builder container"),
            (docker_cli("volume", "inspect", names["volume"]),
             "builder volume")):
        require_inspect_absent(arguments, label)


def validate_buildx_runtime(
        output: str, names: dict[str, str],
        provenance: RootProvenanceDocument) -> dict[str, object]:
    records: list[object] = []
    for line in output.splitlines():
        if not line:
            continue
        try:
            records.append(json.loads(
                line, object_pairs_hook=reject_duplicate_json_keys))
        except json.JSONDecodeError as error:
            raise GateError("buildx runtime inventory JSON invalid") from error
    matches = [
        item for item in records
        if isinstance(item, dict) and item.get("Name") == names["builder"]]
    if len(matches) != 1:
        fail("buildx isolated builder inventory cardinality mismatch")
    record = matches[0]
    nodes = record.get("Nodes")
    if (
            record.get("Driver") != "docker-container" or
            not isinstance(nodes, list) or len(nodes) != 1 or
            not isinstance(nodes[0], dict) or
            nodes[0].get("Name") != names["node"] or
            nodes[0].get("Status") != "running" or
            nodes[0].get("Version") != provenance.body["buildkit_version"]):
        fail("buildx runtime driver/node/version mismatch")
    return {
        "builder": names["builder"], "node": names["node"],
        "driver": "docker-container", "status": "running",
        "buildkit_version": nodes[0]["Version"],
    }


def create_isolated_builder(
        run_id: str, buildkit: dict[str, object],
        provenance: RootProvenanceDocument) -> dict[str, object]:
    names = isolated_builder_names(run_id)
    require_builder_absent(names)
    require_builder_metadata_absent(names["builder"])
    arguments = create_isolated_builder_arguments(
        names, run_id, buildkit)
    volume_output = command(docker_cli(*arguments["volume"]), timeout=30).stdout
    if volume_output.strip() != names["volume"]:
        fail("isolated builder volume create identity mismatch")
    _completed, volume_raw = inspect_json_list(
        docker_cli("volume", "inspect", names["volume"]), "builder volume")
    if volume_raw is None:
        fail("isolated builder volume missing")
    volume = validate_builder_volume_record(
        volume_raw, names, run_id, str(buildkit["id"]))
    created = command(
        docker_cli(*arguments["container"]), check=False, timeout=90)
    if created.returncode != 0:
        fail("exact preloaded BuildKit image unavailable to pull-never create")
    container_id = created.stdout.strip()
    if re.fullmatch(r"[0-9a-f]{64}", container_id) is None:
        fail("isolated builder container ID invalid")
    _completed, stopped_raw = inspect_json_list(
        docker_cli("container", "inspect", container_id),
        "builder container")
    if stopped_raw is None:
        fail("isolated builder container missing")
    stopped = validate_builder_container_record(
        stopped_raw, names, run_id, buildkit, running=False)
    created_builder = command(
        docker_cli(*arguments["buildx"]), timeout=60).stdout.strip()
    if created_builder != names["builder"]:
        fail("buildx create returned unexpected builder identity")
    if not builder_metadata_exists(names["builder"]):
        fail("buildx create did not persist private builder metadata")
    command(docker_cli("container", "start", container_id), timeout=90)
    _completed, running_raw = inspect_json_list(
        docker_cli("container", "inspect", container_id),
        "builder container")
    if running_raw is None:
        fail("running isolated builder missing")
    running = validate_builder_container_record(
        running_raw, names, run_id, buildkit, running=True)
    runtime = validate_buildx_runtime(command(docker_cli(
        "buildx", "ls", "--format", "{{json .}}"),
        timeout=60).stdout, names, provenance)
    return {
        "names": names, "container_id": container_id,
        "volume": volume, "container_before_start": stopped,
        "container_running": running, "runtime": runtime,
    }


def cleanup_isolated_builder(
        builder: dict[str, object], run_id: str,
        buildkit: dict[str, object], *, allow_partial: bool = False,
        ) -> dict[str, object]:
    names = builder.get("names")
    if not isinstance(names, dict) or names != isolated_builder_names(run_id):
        fail("isolated builder cleanup names invalid")
    expected_id = builder.get("container_id")
    inspected, record = inspect_json_list(
        docker_cli("container", "inspect", names["container"]),
        "builder container", check=False)
    if inspected.returncode == 0:
        if (
                record is None or
                (expected_id is not None and record.get("Id") != expected_id)):
            fail("isolated builder cleanup container identity drifted")
        validate_builder_container_record(
            record, names, run_id, buildkit, running=None)
        expected_id = record.get("Id")
    elif inspected.returncode != 1:
        fail("isolated builder cleanup container inspect failed")
    volume_inspected, volume_record = inspect_json_list(
        docker_cli("volume", "inspect", names["volume"]),
        "builder volume", check=False)
    if volume_inspected.returncode == 0:
        if volume_record is None:
            fail("isolated builder cleanup volume malformed")
        validate_builder_volume_record(
            volume_record, names, run_id, str(buildkit["id"]))
    elif volume_inspected.returncode != 1:
        fail("isolated builder cleanup volume inspect failed")
    metadata_present = builder_metadata_exists(names["builder"])
    removed = (
        command(docker_cli(
            "buildx", "rm", "--force", names["builder"]),
            check=False, timeout=120)
        if metadata_present else
        subprocess.CompletedProcess([], 1, "builder metadata absent", None))
    used_fallback = False
    if removed.returncode != 0:
        if inspected.returncode == 0:
            command(docker_cli(
                "container", "rm", "--force", str(expected_id)), timeout=60)
        if volume_inspected.returncode == 0:
            command(docker_cli("volume", "rm", names["volume"]), timeout=60)
        used_fallback = True
        if not allow_partial:
            fail("isolated buildx builder removal failed")
    require_inspect_absent(
        docker_cli("container", "inspect", names["container"]),
        "isolated builder container")
    require_inspect_absent(
        docker_cli("volume", "inspect", names["volume"]),
        "isolated builder volume")
    require_builder_metadata_absent(names["builder"])
    image_after = inspect_single_image(str(buildkit["id"]))
    if image_after.get("Id") != buildkit["id"]:
        fail("reviewed BuildKit image disappeared during cleanup")
    return {
        "buildx_rm": ("fallback" if used_fallback else "completed"),
        "container_absent": True,
        "state_volume_absent": True, "cache_cleanup": "state-volume-removed",
        "private_builder_metadata_absent": True,
        "buildkit_image_retained": True,
    }


def stage_context(
        root: Path, context: Path,
        ) -> tuple[dict[str, dict[str, object]], dict[str, str]]:
    records: dict[str, dict[str, object]] = {}
    for relative, (destination, mode) in SOURCE_FILES.items():
        raw, record = read_stable(root / relative)
        write_exact(context / destination, raw, mode)
        records[relative] = record

    generated: dict[str, tuple[bytes, int]] = {
        "identities.json": (canonical_json({
            "schema": "hepta.p1-dual-domain-identities.v1",
            "paper_authorized": False,
            "live_authorized": False,
            "mutation_authorized": False,
            "direct_broker_access": False,
            "identities": EXPECTED_IDENTITIES,
        }), 0o444),
        "watch-codex-a.credential": (
            b"INERT_P1_WATCH_FIXTURE_NO_EXTERNAL_AUTHORITY\n", 0o440),
        "watch-openclaw-b.credential": (
            b"INERT_P1_WATCH_FIXTURE_NO_EXTERNAL_AUTHORITY\n", 0o440),
        "paper-codex-a.credential": (
            b"INERT_P1_PAPER_FIXTURE_NO_BROKER_CREDENTIAL\n", 0o440),
        "paper-openclaw-b.credential": (
            b"INERT_P1_PAPER_FIXTURE_NO_BROKER_CREDENTIAL\n", 0o440),
        "boundary.json": (canonical_json(EXPECTED_BOUNDARY), 0o444),
    }
    for name, (raw, mode) in generated.items():
        write_exact(context / "provision-root" / name, raw, mode)
    return records, {
        name: hashlib.sha256(raw).hexdigest()
        for name, (raw, _mode) in sorted(generated.items())
    }


def build_arguments(
        base: str, tag: str, context: Path, iidfile: Path,
        run_id: str, *, builder_name: Optional[str] = None) -> list[str]:
    arguments = (
        [
            "buildx", "build", "--builder", builder_name,
            "--load", "--platform", "linux/amd64", "--provenance=false",
        ]
        if builder_name is not None else ["build"]
    )
    arguments.extend([
        "--pull=false", "--network=none", "--no-cache",
        "--label", PURPOSE_LABEL,
        "--label", f"{RUN_LABEL_KEY}={run_id}",
        "--build-arg", f"BASE_IMAGE={base}",
        "--file",
        str(context / "tests/p1_dual_domain_rootful_systemd/Dockerfile"),
        "--iidfile", str(iidfile),
        "--tag", tag,
        str(context),
    ])
    return arguments


def create_arguments(
        image_id: str, name: str, run_id: str) -> list[str]:
    arguments = [
        "create", "--name", name,
        "--label", PURPOSE_LABEL,
        "--label", f"{RUN_LABEL_KEY}={run_id}",
        "--hostname", "hepta-p1-dual-domain-systemd",
        "--network", "none",
        "--cgroupns", "private",
        "--ipc", "private",
        "--read-only",
    ]
    for path, options in RUNTIME_TMPFS.items():
        arguments.extend(("--tmpfs", f"{path}:{options}"))
    arguments.extend(("--cap-drop", "ALL"))
    for capability in RUNTIME_CAPABILITIES:
        arguments.extend(("--cap-add", capability))
    arguments.extend((
        "--security-opt", "no-new-privileges",
        "--security-opt", f"apparmor={APPARMOR_PROFILE}",
        "--pids-limit", "256",
        "--memory", "768m",
        "--cpus", "2",
        "--stop-signal", "SIGRTMIN+3",
        "--stop-timeout", "20",
        "--env", "HEPTA_P1_DUAL_DOMAIN_DISPOSABLE=1",
        "--env", f"HEPTA_P1_DUAL_DOMAIN_RUN_ID={run_id}",
        image_id,
    ))
    return arguments


def validate_container_inspect_record(
        value: object, *, container_id: str, image_id: str,
        name: str, run_id: str) -> None:
    if not isinstance(value, dict):
        fail("container inspect is not an object")
    host = value.get("HostConfig")
    config = value.get("Config")
    mounts = value.get("Mounts")
    if (
            not isinstance(host, dict) or not isinstance(config, dict) or
            not isinstance(mounts, list)):
        fail("container inspect sections are malformed")
    tmpfs = host.get("Tmpfs") or {}
    restart = host.get("RestartPolicy") or {}
    if (
            value.get("Id") != container_id or
            value.get("Name") != "/" + name or
            value.get("Image") != image_id or
            value.get("AppArmorProfile") != APPARMOR_PROFILE or
            config.get("Image") != image_id or
            config.get("Hostname") != "hepta-p1-dual-domain-systemd" or
            config.get("User") != "0:0" or
            config.get("WorkingDir") != "/" or
            config.get("Entrypoint") != [
                "/usr/local/libexec/"
                "hepta-p1-dual-domain-systemd-entrypoint"] or
            config.get("Cmd") not in (None, []) or
            config.get("ExposedPorts") not in (None, {}) or
            config.get("Volumes") not in (None, {}) or
            config.get("StopSignal") != "SIGRTMIN+3" or
            not object_owned(value, run_id) or
            host.get("Privileged") is not False or
            host.get("ReadonlyRootfs") is not True or
            host.get("NetworkMode") != "none" or
            host.get("CgroupnsMode") != "private" or
            host.get("IpcMode") != "private" or
            set(host.get("SecurityOpt") or []) != {
                "no-new-privileges", f"apparmor={APPARMOR_PROFILE}"} or
            host.get("PidsLimit") != 256 or
            host.get("Memory") != 768 * 1024 * 1024 or
            host.get("NanoCpus") != 2_000_000_000 or
            host.get("PublishAllPorts") is not False or
            host.get("PortBindings") not in (None, {}) or
            host.get("Binds") not in (None, []) or
            host.get("Devices") not in (None, []) or
            host.get("DeviceRequests") not in (None, []) or
            host.get("DeviceCgroupRules") not in (None, []) or
            host.get("Links") not in (None, []) or
            restart != {"Name": "no", "MaximumRetryCount": 0} or
            set(tmpfs) != set(RUNTIME_TMPFS) or
            any(tmpfs.get(path) != options
                for path, options in RUNTIME_TMPFS.items()) or
            any(
                mount.get("Type") != "tmpfs" or
                mount.get("Destination") not in RUNTIME_TMPFS
                for mount in mounts) or
            set(host.get("CapDrop") or []) != {"ALL"} or
            set(host.get("CapAdd") or []) != {
                "CAP_" + item for item in RUNTIME_CAPABILITIES}):
        fail("container isolation inspect mismatch")
    environment = config.get("Env")
    if not isinstance(environment, list):
        fail("container environment is malformed")
    parsed: dict[str, str] = {}
    for item in environment:
        if not isinstance(item, str) or "=" not in item:
            fail("container environment entry is malformed")
        key, content = item.split("=", 1)
        if key in parsed:
            fail("duplicate container environment key")
        parsed[key] = content
    if (
            parsed.get("HEPTA_P1_DUAL_DOMAIN_DISPOSABLE") != "1" or
            parsed.get("HEPTA_P1_DUAL_DOMAIN_RUN_ID") != run_id):
        fail("container gate environment mismatch")
    forbidden = re.compile(
        r"(?:SECRET|TOKEN|PASSWORD|CREDENTIAL|AUTHORIZATION|BROKER)",
        re.IGNORECASE)
    if any(forbidden.search(key) is not None for key in parsed):
        fail("secret/broker-shaped container environment key present")


def validate_fault(name: str, value: object) -> None:
    if not isinstance(value, dict) or set(value) != {
            "plane", "domain_id", "before_pid", "after_pid",
            "before_generation", "after_generation",
            "tombstone_generation", "restart_observed",
            "stale_generation_rejected"}:
        fail(f"inner fault evidence malformed: {name}")
    plane, domain = EXPECTED_FAULTS[name]
    integers = (
        value["before_pid"], value["after_pid"],
        value["before_generation"], value["after_generation"],
        value["tombstone_generation"])
    if (
            value.get("plane") != plane or
            value.get("domain_id") != domain or
            any(type(item) is not int or item <= 0 for item in integers) or
            value["before_pid"] == value["after_pid"] or
            value["after_generation"] != value["before_generation"] + 1 or
            value["tombstone_generation"] != value["before_generation"] or
            value.get("restart_observed") is not True or
            value.get("stale_generation_rejected") is not True):
        fail(f"inner fault evidence mismatch: {name}")


def validate_inner(output: str, *, expected_run_id: str) -> dict[str, object]:
    lines = [line for line in output.splitlines() if line]
    if len(lines) != 1 or not lines[0].startswith(INNER_MARKER):
        fail("inner gate output framing mismatch")
    try:
        value = json.loads(lines[0][len(INNER_MARKER):])
    except json.JSONDecodeError as error:
        raise GateError("inner result is invalid JSON") from error
    if (
            not isinstance(value, dict) or set(value) != {
                "schema", "passed", "run_id", "checks", "boot",
                "identities", "faults", "inventory", "boundary"} or
            value.get("schema") != INNER_SCHEMA or
            value.get("passed") is not True or
            value.get("run_id") != expected_run_id):
        fail("inner result contract mismatch")
    checks = value.get("checks")
    if (
            not isinstance(checks, dict) or set(checks) != EXPECTED_CHECKS or
            any(result is not True for result in checks.values())):
        fail("inner check set mismatch")
    boot = value.get("boot")
    if (
            not isinstance(boot, dict) or set(boot) != {
                "boot_id", "pid1_cgroup", "systemd"} or
            not isinstance(boot.get("systemd"), str) or
            not boot["systemd"] or
            CANONICAL_BOOT_ID.fullmatch(str(boot.get("boot_id", ""))) is None or
            boot.get("pid1_cgroup") != "0::/"):
        fail("inner boot evidence mismatch")
    if value.get("identities") != EXPECTED_IDENTITIES:
        fail("inner identity evidence mismatch")
    faults = value.get("faults")
    if not isinstance(faults, dict) or set(faults) != set(EXPECTED_FAULTS):
        fail("inner fault evidence set mismatch")
    for name, record in faults.items():
        validate_fault(name, record)
    inventory = value.get("inventory")
    if not isinstance(inventory, dict) or set(inventory) != {
            "immutable_file_count", "immutable_file_inventory_sha256",
            "inert_daemon_sha256", "forbidden_ib_api_payloads",
            "protected_broker_sockets", "network_interfaces"}:
        fail("inner immutable inventory exact-field mismatch")
    if (
            type(inventory.get("immutable_file_count")) is not int or
            inventory["immutable_file_count"] <= 0 or
            re.fullmatch(
                r"[0-9a-f]{64}",
                str(inventory.get("immutable_file_inventory_sha256", "")))
            is None or
            re.fullmatch(
                r"[0-9a-f]{64}",
                str(inventory.get("inert_daemon_sha256", ""))) is None or
            inventory.get("forbidden_ib_api_payloads") != 0 or
            inventory.get("protected_broker_sockets") != 0 or
            inventory.get("network_interfaces") != ["lo"]):
        fail("inner immutable inventory value mismatch")
    if value.get("boundary") != EXPECTED_BOUNDARY:
        fail("inner authority/broker boundary mismatch")
    return value


def safe_report_path(argument: Path) -> Path:
    path = Path(os.path.abspath(argument))
    if (
            path != Path(os.path.normpath(os.fspath(path))) or
            re.fullmatch(
                r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}\.json",
                path.name) is None):
        fail("report path parent/name is unsafe")
    try:
        os.lstat(path)
    except FileNotFoundError:
        pass
    else:
        fail("report output already exists")
    descriptor, _identity = open_report_parent(path.parent)
    os.close(descriptor)
    return path


def report_directory_identity(metadata: os.stat_result) -> tuple[int, ...]:
    return (
        metadata.st_dev, metadata.st_ino, metadata.st_mode,
        metadata.st_nlink, metadata.st_uid, metadata.st_gid)


def open_report_parent(path: Path) -> tuple[int, tuple[int, ...]]:
    path = Path(os.path.abspath(path))
    if path != Path(os.path.normpath(os.fspath(path))):
        fail("report parent path is not canonical")
    flags = (
        os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC |
        getattr(os, "O_NOFOLLOW", 0))
    descriptor = os.open("/", flags)
    try:
        components = path.parts[1:]
        for index, component in enumerate(components):
            if component in {"", ".", ".."} or "/" in component:
                fail("unsafe report parent component")
            child = os.open(component, flags, dir_fd=descriptor)
            os.close(descriptor)
            descriptor = child
            metadata = os.fstat(descriptor)
            mode = stat.S_IMODE(metadata.st_mode)
            final = index == len(components) - 1
            sticky_root_ancestor = (
                not final and metadata.st_uid == 0 and
                bool(mode & stat.S_ISVTX))
            if (
                    not stat.S_ISDIR(metadata.st_mode) or
                    metadata.st_uid not in {0, os.geteuid()} or
                    (mode & 0o022 and not sticky_root_ancestor) or
                    (final and metadata.st_uid != os.geteuid()) or
                    (final and mode & 0o022)):
                fail("report parent directory is not trusted")
        metadata = os.fstat(descriptor)
        return descriptor, report_directory_identity(metadata)
    except BaseException:
        os.close(descriptor)
        raise


def rename_noreplace(parent: int, source: str, destination: str) -> None:
    function = getattr(_LIBC, "renameat2", None)
    if function is None:
        fail("renameat2 is unavailable for report publication")
    function.argtypes = (
        ctypes.c_int, ctypes.c_char_p, ctypes.c_int, ctypes.c_char_p,
        ctypes.c_uint)
    function.restype = ctypes.c_int
    ctypes.set_errno(0)
    if function(
            parent, os.fsencode(source), parent, os.fsencode(destination),
            RENAME_NOREPLACE) != 0:
        number = ctypes.get_errno()
        if number == errno.EEXIST:
            fail("report output already exists")
        fail("report RENAME_NOREPLACE publication failed")


def atomic_report(path: Path, report: dict[str, object]) -> None:
    validate_report(report)
    payload = canonical_json(report)
    if len(payload) > MAX_REPORT:
        fail("report exceeds bound")
    directory, parent_identity = open_report_parent(path.parent)
    temporary = "." + path.name + ".tmp-" + uuid.uuid4().hex
    descriptor: Optional[int] = None
    renamed = False
    try:
        try:
            os.stat(path.name, dir_fd=directory, follow_symlinks=False)
        except FileNotFoundError:
            pass
        else:
            fail("report output already exists")
        descriptor = os.open(
            temporary,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_CLOEXEC |
            getattr(os, "O_NOFOLLOW", 0),
            0o600, dir_fd=directory)
        view = memoryview(payload)
        while view:
            written = os.write(descriptor, view)
            if written <= 0:
                fail("short report write")
            view = view[written:]
        os.fsync(descriptor)
        metadata = os.fstat(descriptor)
        if (
                not stat.S_ISREG(metadata.st_mode) or metadata.st_nlink != 1 or
                metadata.st_uid != os.geteuid() or
                stat.S_IMODE(metadata.st_mode) != 0o600 or
                metadata.st_size != len(payload)):
            fail("prepared report metadata mismatch")
        if report_directory_identity(os.fstat(directory)) != parent_identity:
            fail("report parent changed before publication")
        rename_noreplace(directory, temporary, path.name)
        renamed = True
        os.fsync(directory)
        if report_directory_identity(os.fstat(directory)) != parent_identity:
            fail("report parent changed after publication")
        reopened = os.open(
            path.name,
            os.O_RDONLY | os.O_CLOEXEC | getattr(os, "O_NOFOLLOW", 0),
            dir_fd=directory)
        try:
            reopened_metadata = os.fstat(reopened)
            chunks: list[bytes] = []
            remaining = len(payload) + 1
            while remaining:
                chunk = os.read(reopened, min(65536, remaining))
                if not chunk:
                    break
                chunks.append(chunk)
                remaining -= len(chunk)
            if (
                    b"".join(chunks) != payload or
                    not stat.S_ISREG(reopened_metadata.st_mode) or
                    reopened_metadata.st_nlink != 1 or
                    reopened_metadata.st_uid != os.geteuid() or
                    stat.S_IMODE(reopened_metadata.st_mode) != 0o600):
                fail("published report secure reopen failed")
        finally:
            os.close(reopened)
    finally:
        if descriptor is not None:
            os.close(descriptor)
        if not renamed:
            try:
                os.unlink(temporary, dir_fd=directory)
                os.fsync(directory)
            except FileNotFoundError:
                pass
        os.close(directory)


def require_source_lineage(
        source_tree_clean: bool, allow_dirty_rehearsal: bool) -> None:
    if not source_tree_clean and not allow_dirty_rehearsal:
        fail(
            "source lineage is not clean and fully versioned; dirty execution "
            "requires --allow-dirty-rehearsal and cannot produce GO")


PROVENANCE_REPORT_METADATA_KEYS = frozenset({
    "document_sha256", "root_owned", "canonical_json", "mode",
    "identity_sha256",
})


def validate_report_provenance_inventory(
        value: object, *, started_at_ms: int,
        completed_at_ms: int) -> dict[str, dict[str, object]]:
    if not isinstance(value, dict) or set(value) != {
            "base", "builder", "apparmor", "docker_namespace"}:
        fail("certifying provenance inventory mismatch")
    specifications = {
        "base": (
            REVIEWED_BASE_PROVENANCE_SCHEMA, REVIEWED_BASE_KEYS,
            validate_base_provenance),
        "builder": (
            REVIEWED_BUILDER_PROVENANCE_SCHEMA, REVIEWED_BUILDER_KEYS,
            validate_builder_provenance),
        "apparmor": (
            REVIEWED_APPARMOR_PROVENANCE_SCHEMA, REVIEWED_APPARMOR_KEYS,
            validate_apparmor_provenance),
        "docker_namespace": (
            REVIEWED_DOCKER_NAMESPACE_PROVENANCE_SCHEMA,
            REVIEWED_DOCKER_NAMESPACE_KEYS,
            validate_docker_namespace_provenance),
    }
    bodies: dict[str, dict[str, object]] = {}
    for kind, (schema, body_keys, validator) in specifications.items():
        record = value[kind]
        if (
                not isinstance(record, dict) or
                set(record) != body_keys | PROVENANCE_REPORT_METADATA_KEYS or
                record.get("schema") != schema or
                record.get("decision") != "GO" or
                record.get("root_owned") is not True or
                record.get("canonical_json") is not True or
                record.get("mode") != "0400" or
                re.fullmatch(
                    r"sha256:[0-9a-f]{64}",
                    str(record.get("document_sha256", ""))) is None or
                canonical_sha256(
                    {key: record[key] for key in body_keys}) !=
                    record.get("document_sha256") or
                re.fullmatch(
                    r"sha256:[0-9a-f]{64}",
                    str(record.get("identity_sha256", ""))) is None):
            fail("certifying root provenance record mismatch")
        body = {key: record[key] for key in body_keys}
        validate_provenance_time(body, now_ms=started_at_ms)
        if completed_at_ms >= int(body["expires_at_ms"]):
            fail("certifying provenance expired during the gate")
        validator(RootProvenanceDocument(
            kind=kind, path=Path("/"),
            document_sha256=str(record["document_sha256"]),
            body=body, metadata=()))
        bodies[kind] = body
    return bodies


def require_exact_repo_digests(value: object, expected: str) -> None:
    if (
            not isinstance(value, list) or not value or
            value != sorted(set(value)) or expected not in value or
            any(not isinstance(item, str) or PINNED_IMAGE.fullmatch(item) is None
                for item in value)):
        fail("certifying reviewed image RepoDigests mismatch")


def validate_certifying_report_evidence(
        certification: dict[str, object], *, run_id: str,
        platform: dict[str, object], started_at_ms: int,
        completed_at_ms: int) -> None:
    provenance = certification["provenance"]
    bodies = validate_report_provenance_inventory(
        provenance, started_at_ms=started_at_ms,
        completed_at_ms=completed_at_ms)

    reviewed_base = certification["reviewed_base"]
    if (
            not isinstance(reviewed_base, dict) or set(reviewed_base) != {
                "reference", "id", "repo_digests", "os", "architecture",
                "declared_volumes", "onbuild_instructions", "labels_sha256",
                "production_approved", "reviewed_provenance"}):
        fail("certifying reviewed base exact-field mismatch")
    require_exact_repo_digests(
        reviewed_base["repo_digests"], str(reviewed_base["reference"]))
    if (
            reviewed_base["reference"] != bodies["base"]["repo_digest"] or
            reviewed_base["id"] != bodies["base"]["image_id"] or
            reviewed_base["labels_sha256"] !=
            bodies["base"]["labels_sha256"] or
            reviewed_base["reference"] !=
            platform.get("base_image_reference") or
            reviewed_base["id"] != platform.get("base_image_id") or
            reviewed_base["os"] != "linux" or
            reviewed_base["os"] != platform.get("base_image_os") or
            reviewed_base["architecture"] != "amd64" or
            reviewed_base["architecture"] !=
            platform.get("base_image_architecture") or
            reviewed_base["declared_volumes"] != 0 or
            reviewed_base["onbuild_instructions"] != 0 or
            reviewed_base["production_approved"] is not True or
            reviewed_base["reviewed_provenance"] != provenance["base"]):
        fail("certifying reviewed base binding mismatch")

    reviewed_buildkit = certification["reviewed_buildkit"]
    if (
            not isinstance(reviewed_buildkit, dict) or
            set(reviewed_buildkit) != {
                "reference", "id", "bare_id", "repo_digests", "os",
                "architecture", "config_sha256", "config_labels",
                "entrypoint", "production_approved", "reviewed_provenance"}):
        fail("certifying reviewed BuildKit exact-field mismatch")
    require_exact_repo_digests(
        reviewed_buildkit["repo_digests"],
        str(reviewed_buildkit["reference"]))
    buildkit_labels = reviewed_buildkit["config_labels"]
    if (
            reviewed_buildkit["reference"] !=
            bodies["builder"]["repo_digest"] or
            reviewed_buildkit["id"] != bodies["builder"]["image_id"] or
            reviewed_buildkit["bare_id"] !=
            str(reviewed_buildkit["id"]).removeprefix("sha256:") or
            reviewed_buildkit["config_sha256"] !=
            bodies["builder"]["config_sha256"] or
            reviewed_buildkit["os"] != "linux" or
            reviewed_buildkit["architecture"] != "amd64" or
            not isinstance(buildkit_labels, dict) or
            any(not isinstance(key, str) or not isinstance(value, str)
                for key, value in buildkit_labels.items()) or
            {"io.hepta.purpose", BUILDER_ROLE_LABEL, BUILDER_RUN_LABEL,
             BUILDER_IMAGE_LABEL, BUILDER_NAME_LABEL}.intersection(
                 buildkit_labels) or
            reviewed_buildkit["entrypoint"] not in (
                ["buildkitd"], ["/usr/bin/buildkitd"],
                ["/usr/local/bin/buildkitd"]) or
            reviewed_buildkit["production_approved"] is not True or
            reviewed_buildkit["reviewed_provenance"] != provenance["builder"]):
        fail("certifying reviewed BuildKit binding mismatch")

    toolchain = certification["buildx_toolchain"]
    if (
            not isinstance(toolchain, dict) or set(toolchain) != {
                "buildx_path_sha256", "buildx_version",
                "buildx_binary_sha256", "docker_server_version",
                "docker_server_api_version", "docker_server_git_commit",
                "reviewed"} or
            re.fullmatch(
                r"sha256:[0-9a-f]{64}",
                str(toolchain.get("buildx_path_sha256", ""))) is None or
            toolchain.get("buildx_version") !=
            bodies["builder"]["buildx_version"] or
            toolchain.get("buildx_binary_sha256") !=
            bodies["builder"]["buildx_binary_sha256"] or
            toolchain.get("docker_server_version") !=
            bodies["builder"]["docker_server_version"] or
            toolchain.get("docker_server_api_version") !=
            bodies["builder"]["docker_server_api_version"] or
            toolchain.get("docker_server_git_commit") !=
            bodies["builder"]["docker_server_git_commit"] or
            toolchain.get("docker_server_version") !=
            platform.get("docker_server_version") or
            toolchain.get("docker_server_api_version") !=
            platform.get("docker_server_api_version") or
            toolchain.get("reviewed") is not True):
        fail("certifying Docker/buildx toolchain binding mismatch")

    docker_socket = certification["docker_socket_before"]
    if (
            not isinstance(docker_socket, dict) or set(docker_socket) != {
                "device", "inode", "mode", "uid", "gid", "owner_root",
                "world_writable"} or
            type(docker_socket.get("device")) is not int or
            docker_socket["device"] < 0 or
            type(docker_socket.get("inode")) is not int or
            docker_socket["inode"] <= 0 or
            re.fullmatch(r"0[0-7]{3}", str(docker_socket.get("mode", "")))
            is None or
            int(str(docker_socket["mode"]), 8) & 0o002 or
            docker_socket.get("uid") != 0 or
            type(docker_socket.get("gid")) is not int or
            docker_socket["gid"] < 0 or
            docker_socket.get("owner_root") is not True or
            docker_socket.get("world_writable") is not False):
        fail("certifying Docker socket identity mismatch")

    isolated_builder = certification["isolated_builder"]
    if (
            not isinstance(isolated_builder, dict) or
            set(isolated_builder) != {
                "names", "container_id", "volume", "container_before_start",
                "container_running", "runtime"}):
        fail("certifying isolated builder exact-field mismatch")
    names = isolated_builder["names"]
    expected_names = isolated_builder_names(run_id)
    if names != expected_names or re.fullmatch(
            r"[0-9a-f]{64}",
            str(isolated_builder.get("container_id", ""))) is None:
        fail("certifying isolated builder identity mismatch")
    image_id = str(reviewed_buildkit["id"])
    volume = isolated_builder["volume"]
    expected_state_labels = builder_labels(
        run_id, expected_names["builder"], image_id, BUILDER_STATE_ROLE)
    if (
            not isinstance(volume, dict) or set(volume) != {
                "name", "driver", "scope", "labels", "mountpoint_sha256"} or
            volume.get("name") != expected_names["volume"] or
            volume.get("driver") != "local" or volume.get("scope") != "local" or
            volume.get("labels") != expected_state_labels or
            re.fullmatch(
                r"sha256:[0-9a-f]{64}",
                str(volume.get("mountpoint_sha256", ""))) is None):
        fail("certifying isolated builder volume mismatch")
    expected_daemon_labels = builder_labels(
        run_id, expected_names["builder"], image_id, BUILDER_DAEMON_ROLE)
    for field, running in (
            ("container_before_start", False), ("container_running", True)):
        record = isolated_builder[field]
        if (
                not isinstance(record, dict) or set(record) != {
                    "container_id", "name", "network_mode", "privileged",
                    "bind_mounts", "devices", "published_ports", "running",
                    "labels"} or
                record.get("container_id") != isolated_builder["container_id"] or
                record.get("name") != expected_names["container"] or
                record.get("network_mode") != "none" or
                record.get("privileged") is not True or
                any(record.get(key) != 0 for key in (
                    "bind_mounts", "devices", "published_ports")) or
                record.get("running") is not running or
                record.get("labels") != expected_daemon_labels):
            fail("certifying isolated builder container mismatch")
    runtime = isolated_builder["runtime"]
    if (
            not isinstance(runtime, dict) or set(runtime) != {
                "builder", "node", "driver", "status", "buildkit_version"} or
            runtime.get("builder") != expected_names["builder"] or
            runtime.get("node") != expected_names["node"] or
            runtime.get("driver") != "docker-container" or
            runtime.get("status") != "running" or
            runtime.get("buildkit_version") !=
            bodies["builder"]["buildkit_version"]):
        fail("certifying isolated builder runtime mismatch")

    apparmor = certification["apparmor_before"]
    if (
            not isinstance(apparmor, dict) or set(apparmor) != {
                "profile", "mode", "attach", "learning_count",
                "profile_sha256", "raw_sha256", "raw_abi", "raw_data_id",
                "profile_inventory_count", "profile_inventory_sha256",
                "namespace", "reviewed_provenance"} or
            apparmor.get("profile") != APPARMOR_PROFILE or
            apparmor.get("mode") != "enforce" or
            apparmor.get("attach") != APPARMOR_PROFILE or
            apparmor.get("learning_count") != 0 or
            apparmor.get("profile_sha256") !=
            bodies["apparmor"]["profile_sha256"] or
            apparmor.get("raw_sha256") != bodies["apparmor"]["raw_sha256"] or
            apparmor.get("raw_abi") != bodies["apparmor"]["raw_abi"] or
            re.fullmatch(
                r"[1-9][0-9]{0,19}", str(apparmor.get("raw_data_id", "")))
            is None or
            type(apparmor.get("profile_inventory_count")) is not int or
            apparmor["profile_inventory_count"] <= 0 or
            re.fullmatch(
                r"sha256:[0-9a-f]{64}",
                str(apparmor.get("profile_inventory_sha256", ""))) is None or
            apparmor.get("namespace") != {
                "name": "root", "level": 0, "stacked": False} or
            apparmor.get("reviewed_provenance") != provenance["apparmor"]):
        fail("certifying AppArmor evidence binding mismatch")

    namespace = certification["docker_namespace_before"]
    if (
            not isinstance(namespace, dict) or set(namespace) != {
                "docker_daemon_id", "docker_daemon_pid",
                "docker_daemon_start_time_ticks", "docker_daemon_comm",
                "docker_daemon_process_inode", "host_boot_id",
                "host_namespace", "daemon_namespace",
                "same_apparmor_namespace_attested", "reviewed_provenance"} or
            namespace.get("docker_daemon_id") !=
            bodies["docker_namespace"]["docker_daemon_id"] or
            namespace.get("docker_daemon_pid") !=
            bodies["docker_namespace"]["docker_daemon_pid"] or
            namespace.get("docker_daemon_start_time_ticks") !=
            bodies["docker_namespace"]["docker_daemon_start_time_ticks"] or
            namespace.get("docker_daemon_comm") != "dockerd" or
            type(namespace.get("docker_daemon_process_inode")) is not int or
            namespace["docker_daemon_process_inode"] <= 0 or
            namespace.get("host_boot_id") !=
            bodies["docker_namespace"]["host_boot_id"] or
            namespace.get("host_namespace") != {
                "name": "root", "level": 0, "stacked": False} or
            namespace.get("daemon_namespace") != {
                "name": "root", "level": 0, "stacked": False} or
            namespace.get("same_apparmor_namespace_attested") is not True or
            namespace.get("reviewed_provenance") !=
            provenance["docker_namespace"]):
        fail("certifying Docker namespace evidence binding mismatch")


def validate_report(report: object) -> dict[str, object]:
    """Validate the exact outer receipt before it can be published."""
    if not isinstance(report, dict) or set(report) != {
            "schema", "run_id", "decision", "passed", "rehearsal_passed",
            "certification_ready", "certification_blockers", "scope",
            "started_at_ms", "completed_at_ms", "expires_at_ms",
            "body_sha256", "paper_test_admission_candidate",
            "paper_admission_authorized", "paper_authorized",
            "live_authorized", "mutation_authorized", "direct_broker_access",
            "order_submission_authorized", "duration_ms",
            "lineage", "inputs", "generated_input_sha256", "platform",
            "container", "disposable_cleanup", "certification",
            "environment_review_closure", "inner", "boundary"}:
        fail("outer report exact-field contract mismatch")
    certification = report.get("certification")
    if not isinstance(certification, dict) or set(certification) != {
            "requested", "eligible", "provenance",
            "provenance_reopened_equal", "reviewed_base",
            "reviewed_buildkit", "buildx_toolchain", "isolated_builder",
            "isolated_builder_cleanup", "docker_socket_before",
            "docker_socket_after", "docker_socket_records_equal",
            "apparmor_before", "apparmor_after",
            "apparmor_records_equal", "docker_namespace_before",
            "docker_namespace_after", "docker_namespace_records_equal"}:
        fail("outer report certification exact-field mismatch")
    certifying = certification.get("requested") is True
    environment_review = report.get("environment_review_closure")
    started_at_ms = report.get("started_at_ms")
    completed_at_ms = report.get("completed_at_ms")
    expires_at_ms = report.get("expires_at_ms")
    expected_body = dict(report)
    expected_body.pop("body_sha256", None)
    if (
            report.get("schema") != SCHEMA or
            re.fullmatch(r"[0-9a-f]{32}", str(report.get("run_id", "")))
            is None or
            report.get("decision") != ("GO" if certifying else "REHEARSAL_ONLY") or
            report.get("passed") is not certifying or
            report.get("rehearsal_passed") is not True or
            report.get("certification_ready") is not certifying or
            report.get("certification_blockers") !=
            ([] if certifying else list(CERTIFICATION_BLOCKERS)) or
            report.get("scope") !=
            "broker-free-p1-dual-domain-rootful-prerequisite-only" or
            type(started_at_ms) is not int or started_at_ms < 0 or
            type(completed_at_ms) is not int or
            completed_at_ms < started_at_ms or
            type(expires_at_ms) is not int or
            expires_at_ms <= completed_at_ms or
            expires_at_ms - started_at_ms > MAX_PROVENANCE_LIFETIME_MS or
            report.get("body_sha256") != canonical_sha256(expected_body) or
            report.get("paper_test_admission_candidate") is not False or
            report.get("paper_admission_authorized") is not False or
            report.get("paper_authorized") is not False or
            report.get("live_authorized") is not False or
            report.get("mutation_authorized") is not False or
            report.get("direct_broker_access") is not False or
            report.get("order_submission_authorized") is not False or
            type(report.get("duration_ms")) is not int or
            report["duration_ms"] != completed_at_ms - started_at_ms):
        fail("outer report decision/authority contract mismatch")
    lineage = report.get("lineage")
    if not isinstance(lineage, dict) or set(lineage) != {
            "source_commit", "expected_source_commit", "source_tree_clean",
            "all_inputs_versioned", "inputs_stable", "final_lineage",
            "input_manifest_sha256", "runner_sha256"}:
        fail("outer report lineage exact-field mismatch")
    if (
            CANONICAL_COMMIT.fullmatch(str(lineage.get("source_commit", "")))
            is None or
            lineage.get("source_commit") !=
            lineage.get("expected_source_commit") or
            type(lineage.get("source_tree_clean")) is not bool or
            type(lineage.get("all_inputs_versioned")) is not bool or
            lineage.get("inputs_stable") is not True or
            type(lineage.get("final_lineage")) is not bool or
            lineage.get("final_lineage") != lineage.get("source_tree_clean") or
            (certifying and (
                lineage.get("source_tree_clean") is not True or
                lineage.get("all_inputs_versioned") is not True or
                lineage.get("final_lineage") is not True)) or
            re.fullmatch(
                r"[0-9a-f]{64}",
                str(lineage.get("input_manifest_sha256", ""))) is None or
            re.fullmatch(
                r"[0-9a-f]{64}",
                str(lineage.get("runner_sha256", ""))) is None):
        fail("outer report lineage value mismatch")
    inputs = report.get("inputs")
    if not isinstance(inputs, dict) or set(inputs) != set(SOURCE_FILES):
        fail("outer report input set mismatch")
    for record in inputs.values():
        if (
                not isinstance(record, dict) or set(record) != {
                    "sha256", "size", "mode"} or
                re.fullmatch(
                    r"[0-9a-f]{64}", str(record.get("sha256", ""))) is None or
                type(record.get("size")) is not int or record["size"] <= 0 or
                re.fullmatch(r"0[0-7]{3}", str(record.get("mode", ""))) is None):
            fail("outer report input record mismatch")
    if (
            hashlib.sha256(canonical_json(inputs)).hexdigest() !=
            lineage["input_manifest_sha256"] or
            inputs["scripts/run_hepta_p1_dual_domain_rootful_gate.py"]
            ["sha256"] != lineage["runner_sha256"]):
        fail("outer report input manifest/runner binding mismatch")
    generated = report.get("generated_input_sha256")
    if (
            not isinstance(generated, dict) or set(generated) != {
                "identities.json", "boundary.json",
                "watch-codex-a.credential",
                "watch-openclaw-b.credential",
                "paper-codex-a.credential",
                "paper-openclaw-b.credential"} or
            any(re.fullmatch(r"[0-9a-f]{64}", str(value)) is None
                for value in generated.values())):
        fail("outer report generated-input evidence mismatch")
    platform = report.get("platform")
    if not isinstance(platform, dict) or set(platform) != {
            "host_kernel", "host_architecture", "docker_client",
            "docker_server_version", "docker_server_api_version",
            "docker_server_os", "docker_server_architecture",
            "docker_cgroup_driver", "docker_cgroup_version",
            "docker_default_runtime", "docker_security_options",
            "base_image_reference", "base_image_id", "base_image_os",
            "base_image_architecture", "systemd", "container_boot_id",
            "container_pid1_cgroup"}:
        fail("outer report platform exact-field mismatch")
    if (
            require_pinned_image(str(platform.get("base_image_reference", "")))
            != platform["base_image_reference"] or
            CANONICAL_IMAGE_ID.fullmatch(
                str(platform.get("base_image_id", ""))) is None or
            CANONICAL_BOOT_ID.fullmatch(
                str(platform.get("container_boot_id", ""))) is None or
            platform.get("container_pid1_cgroup") != "0::/" or
            platform.get("docker_cgroup_version") != "2" or
            not isinstance(platform.get("docker_security_options"), list)):
        fail("outer report platform value mismatch")
    container = report.get("container")
    if not isinstance(container, dict) or set(container) != {
            "image_id", "network_mode", "read_only_rootfs",
            "private_cgroup_namespace", "privileged", "bind_mounts",
            "published_ports", "devices", "device_requests", "links",
            "tmpfs_allowlist", "capabilities", "apparmor_profile"}:
        fail("outer report container exact-field mismatch")
    if (
            CANONICAL_IMAGE_ID.fullmatch(
                str(container.get("image_id", ""))) is None or
            container.get("network_mode") != "none" or
            container.get("read_only_rootfs") is not True or
            container.get("private_cgroup_namespace") is not True or
            container.get("privileged") is not False or
            any(container.get(name) != 0 for name in (
                "bind_mounts", "published_ports", "devices",
                "device_requests", "links")) or
            container.get("tmpfs_allowlist") != RUNTIME_TMPFS or
            container.get("capabilities") != list(RUNTIME_CAPABILITIES) or
            container.get("apparmor_profile") != APPARMOR_PROFILE):
        fail("outer report container isolation value mismatch")
    disposable_cleanup = report.get("disposable_cleanup")
    if (
            not isinstance(disposable_cleanup, dict) or
            disposable_cleanup != {
                "container_absent": True,
                "image_tag_absent": True,
                "image_id_absent": True,
            }):
        fail("outer report disposable cleanup mismatch")
    inner = report.get("inner")
    if not isinstance(inner, dict):
        fail("outer report inner evidence malformed")
    validate_inner(
        INNER_MARKER + json.dumps(
            inner, sort_keys=True, separators=(",", ":")),
        expected_run_id=str(report["run_id"]),
    )
    if certification.get("eligible") is not certifying:
        fail("outer report certification eligibility mismatch")
    evidence_fields = (
        "provenance", "reviewed_base", "reviewed_buildkit",
        "buildx_toolchain", "isolated_builder", "isolated_builder_cleanup",
        "docker_socket_before", "docker_socket_after", "apparmor_before",
        "apparmor_after", "docker_namespace_before", "docker_namespace_after")
    equality_fields = (
        "provenance_reopened_equal", "docker_socket_records_equal",
        "apparmor_records_equal",
        "docker_namespace_records_equal")
    if not certifying:
        if (
                environment_review is not None or
                expires_at_ms !=
                completed_at_ms + REHEARSAL_REPORT_LIFETIME_MS or
                any(certification.get(field) is not None
                    for field in evidence_fields) or
                any(certification.get(field) is not False
                    for field in equality_fields)):
            fail("rehearsal report contains certifying evidence claims")
    else:
        try:
            ROOT_REVIEW.validate_verification_record(
                environment_review, now_ms=completed_at_ms)
        except ROOT_REVIEW.ReviewClosureError as error:
            raise GateError(str(error)) from error
        assert isinstance(environment_review, dict)
        if (
                any(not isinstance(certification.get(field), dict)
                    for field in evidence_fields) or
                any(certification.get(field) is not True
                    for field in equality_fields) or
                certification["docker_socket_before"] !=
                certification["docker_socket_after"] or
                certification["apparmor_before"] !=
                certification["apparmor_after"] or
                certification["docker_namespace_before"] !=
                certification["docker_namespace_after"]):
            fail("certifying report evidence closure mismatch")
        provenance = certification["provenance"]
        if not isinstance(provenance, dict) or set(provenance) != {
                "base", "builder", "apparmor", "docker_namespace"}:
            fail("certifying provenance inventory mismatch")
        validate_certifying_report_evidence(
            certification, run_id=str(report["run_id"]), platform=platform,
            started_at_ms=started_at_ms,
            completed_at_ms=completed_at_ms)
        provenance_bodies = validate_report_provenance_inventory(
            provenance, started_at_ms=started_at_ms,
            completed_at_ms=completed_at_ms)
        if (
                environment_review.get("source_commit") !=
                    lineage["source_commit"] or
                environment_review.get("base_image_reference") !=
                    platform["base_image_reference"] or
                environment_review.get("buildkit_image_reference") !=
                    provenance_bodies["builder"]["repo_digest"] or
                any(environment_review["outputs"][kind]["file_sha256"] !=
                    provenance[kind]["document_sha256"]
                    for kind in provenance_bodies)):
            fail("certifying report is not bound to the signed review closure")
        if report["expires_at_ms"] != min(
                min(int(record["expires_at_ms"])
                    for record in provenance_bodies.values()),
                int(environment_review["expires_at_ms"])):
            fail("certifying report expiry does not bind provenance")
        expected_schemas = {
            "base": REVIEWED_BASE_PROVENANCE_SCHEMA,
            "builder": REVIEWED_BUILDER_PROVENANCE_SCHEMA,
            "apparmor": REVIEWED_APPARMOR_PROVENANCE_SCHEMA,
            "docker_namespace": REVIEWED_DOCKER_NAMESPACE_PROVENANCE_SCHEMA,
        }
        for kind, record in provenance.items():
            if (
                    not isinstance(record, dict) or
                    record.get("schema") != expected_schemas[kind] or
                    record.get("decision") != "GO" or
                    record.get("root_owned") is not True or
                    record.get("canonical_json") is not True or
                    record.get("mode") != "0400" or
                    re.fullmatch(
                        r"sha256:[0-9a-f]{64}",
                        str(record.get("document_sha256", ""))) is None or
                    re.fullmatch(
                        r"sha256:[0-9a-f]{64}",
                        str(record.get("identity_sha256", ""))) is None):
                fail("certifying root provenance record mismatch")
        cleanup = certification["isolated_builder_cleanup"]
        if (
                set(cleanup) != {
                    "buildx_rm", "container_absent", "state_volume_absent",
                    "private_builder_metadata_absent", "cache_cleanup",
                    "buildkit_image_retained"} or
                cleanup.get("buildx_rm") != "completed" or
                cleanup.get("container_absent") is not True or
                cleanup.get("state_volume_absent") is not True or
                cleanup.get("private_builder_metadata_absent") is not True or
                cleanup.get("cache_cleanup") != "state-volume-removed" or
                cleanup.get("buildkit_image_retained") is not True):
            fail("certifying isolated builder cleanup is incomplete")
        if (
                certification["reviewed_base"].get("production_approved")
                is not True or
                certification["reviewed_buildkit"].get("production_approved")
                is not True or
                certification["buildx_toolchain"].get("reviewed") is not True or
                certification["apparmor_before"].get("mode") != "enforce" or
                certification["docker_namespace_before"].get(
                    "same_apparmor_namespace_attested") is not True):
            fail("certifying reviewed evidence values are incomplete")
    if report.get("boundary") != EXPECTED_BOUNDARY:
        fail("outer report boundary mismatch")
    return report


def execute(
        base: str, expected_source_commit: str, *,
        allow_dirty_rehearsal: bool = False,
        certification_request: Optional[CertificationRequest] = None,
        ) -> dict[str, object]:
    if certification_request is not None and allow_dirty_rehearsal:
        fail("certification cannot enable dirty rehearsal semantics")
    started_at_ms = int(time.time() * 1000)
    root = repository_root()
    base = require_pinned_image(base)
    expected_source_commit = require_expected_commit(expected_source_commit)
    source_commit = command(
        ["git", "-C", str(root), "rev-parse", "HEAD"], timeout=15
    ).stdout.strip()
    if source_commit != expected_source_commit:
        fail("source commit does not match the external commit pin")
    tracked_tree_clean = command(
        ["git", "-C", str(root), "status", "--porcelain",
         "--untracked-files=no"],
        check=False,
        timeout=30,
    ).stdout == ""
    all_inputs_versioned = all(
        command(
            ["git", "-C", str(root), "ls-files", "--error-unmatch", relative],
            check=False,
            timeout=10,
        ).returncode == 0
        for relative in SOURCE_FILES
    )
    source_tree_clean = tracked_tree_clean and all_inputs_versioned
    require_source_lineage(source_tree_clean, allow_dirty_rehearsal)
    if certification_request is not None and not source_tree_clean:
        fail("--certify requires a clean, fully versioned frozen source tree")
    environment_review_session = (
        verify_environment_review_for_request(
            certification_request, base_image=base,
            source_commit=source_commit)
        if certification_request is not None else None)
    provenance_before = (
        load_certification_provenance(
            certification_request, now_ms=started_at_ms)
        if certification_request is not None else {})
    docker_socket_before = (
        validate_local_docker_socket()
        if certification_request is not None else None)

    initialize_docker_config()
    run_id = uuid.uuid4().hex
    tag = f"hepta/p1-dual-domain-rootful:{run_id}"
    name = f"hepta-p1-dual-domain-{run_id}"
    image_id: Optional[str] = None
    container_id: Optional[str] = None
    input_before: dict[str, dict[str, object]] = {}
    generated_hashes: dict[str, str] = {}
    inner: Optional[dict[str, object]] = None
    server: dict[str, object] = {}
    docker_info: dict[str, object] = {}
    base_record: dict[str, object] = {}
    base_id = ""
    reviewed_base_record: Optional[dict[str, object]] = None
    buildkit_record: Optional[dict[str, object]] = None
    buildx_toolchain: Optional[dict[str, object]] = None
    isolated_builder: Optional[dict[str, object]] = None
    builder_attempted = False
    builder_names = isolated_builder_names(run_id)
    builder_cleanup: Optional[dict[str, object]] = None
    apparmor_before: Optional[dict[str, object]] = None
    apparmor_after: Optional[dict[str, object]] = None
    docker_namespace_before: Optional[dict[str, object]] = None
    docker_namespace_after: Optional[dict[str, object]] = None
    docker_socket_after: Optional[dict[str, object]] = None
    provenance_after: dict[str, RootProvenanceDocument] = {}
    runtime_container_absent = False
    runtime_image_tag_absent = False
    runtime_image_id_absent = False
    try:
        server_raw = command(docker_cli(
            "version", "--format", "{{json .Server}}"), timeout=30).stdout
        server = json.loads(
            server_raw, object_pairs_hook=reject_duplicate_json_keys)
        if not isinstance(server, dict) or not server.get("Version"):
            fail("Docker server evidence invalid")
        docker_info = json.loads(
            command(
                docker_cli("info", "--format", "{{json .}}"),
                timeout=30).stdout,
            object_pairs_hook=reject_duplicate_json_keys)
        if (
                not isinstance(docker_info, dict) or
                docker_info.get("CgroupVersion") != "2" or
                not isinstance(docker_info.get("Architecture"), str) or
                not isinstance(docker_info.get("OperatingSystem"), str) or
                not isinstance(docker_info.get("DefaultRuntime"), str) or
                not isinstance(docker_info.get("SecurityOptions"), list) or
                not any(
                    isinstance(option, str) and "apparmor" in option.lower()
                    for option in docker_info.get("SecurityOptions", []))):
            fail("Docker runtime evidence invalid")
        base_inspect = json.loads(
            command(
                docker_cli("image", "inspect", base), timeout=30).stdout,
            object_pairs_hook=reject_duplicate_json_keys)
        if not isinstance(base_inspect, list) or len(base_inspect) != 1:
            fail("preloaded base image inspect cardinality mismatch")
        base_record = base_inspect[0]
        if not isinstance(base_record, dict):
            fail("preloaded base image inspect record malformed")
        base_id = str(base_record.get("Id", ""))
        repo_digests = base_record.get("RepoDigests")
        if (
                CANONICAL_IMAGE_ID.fullmatch(base_id) is None or
                base_record.get("Os") != "linux" or
                not architecture_matches(
                    str(base_record.get("Architecture", "")),
                    str(docker_info.get("Architecture", ""))) or
                not isinstance(repo_digests, list) or
                base not in repo_digests):
            fail("preloaded base identity/platform/digest evidence invalid")

        if certification_request is not None:
            reviewed_base_record = validate_reviewed_base_image(
                base_record, base, provenance_before["base"])
            apparmor_before = validate_loaded_apparmor(
                provenance_before["apparmor"])
            docker_namespace_before = validate_docker_namespace_binding(
                provenance_before["docker_namespace"], apparmor_before)
            buildx_toolchain = inspect_buildx_toolchain(
                certification_request.buildx_binary_sha256,
                provenance_before["builder"])
            buildkit_inspect = inspect_single_image(
                certification_request.buildkit_image)
            buildkit_record = validate_reviewed_buildkit_image(
                buildkit_inspect, certification_request.buildkit_image,
                provenance_before["builder"])

        with tempfile.TemporaryDirectory(
                prefix="hepta-p1-dual-domain-context-") as temporary:
            context = Path(temporary)
            input_before, generated_hashes = stage_context(root, context)
            iidfile = context / ".image-id"
            builder_name: Optional[str] = None
            if certification_request is not None:
                if buildkit_record is None:
                    fail("reviewed BuildKit record missing")
                builder_attempted = True
                isolated_builder = create_isolated_builder(
                    run_id, buildkit_record, provenance_before["builder"])
                names = isolated_builder.get("names")
                if not isinstance(names, dict):
                    fail("isolated builder names missing")
                builder_name = str(names["builder"])
            command(docker_cli(*build_arguments(
                base, tag, context, iidfile, run_id,
                builder_name=builder_name)), timeout=900)
            image_id = iidfile.read_text(
                encoding="ascii", errors="strict").strip()
            if CANONICAL_IMAGE_ID.fullmatch(image_id) is None:
                fail("built image ID invalid")
            image_inspect = json.loads(
                command(
                    docker_cli("image", "inspect", tag), timeout=30).stdout,
                object_pairs_hook=reject_duplicate_json_keys)
            if (
                    not isinstance(image_inspect, list) or
                    len(image_inspect) != 1 or
                    not object_owned(
                        image_inspect[0], run_id,
                        expected_image_id=image_id)):
                fail("built image ownership labels mismatch")
            created = command(docker_cli(*create_arguments(
                image_id, name, run_id)), timeout=60).stdout.strip()
            if re.fullmatch(r"[0-9a-f]{64}", created) is None:
                fail("container ID invalid")
            container_id = created
            inspected_values = json.loads(
                command(docker_cli(
                    "container", "inspect", container_id),
                    timeout=30).stdout,
                object_pairs_hook=reject_duplicate_json_keys)
            if (
                    not isinstance(inspected_values, list) or
                    len(inspected_values) != 1):
                fail("container inspect cardinality mismatch")
            validate_container_inspect_record(
                inspected_values[0], container_id=container_id,
                image_id=image_id, name=name, run_id=run_id)
            command(docker_cli("start", container_id), timeout=60)
            deadline = time.monotonic() + 45
            while True:
                ready = command(docker_cli(
                    "exec", container_id, "systemctl", "show",
                    "--property=Version", "--value"),
                    check=False, timeout=10)
                if ready.returncode == 0 and ready.stdout.strip():
                    break
                if time.monotonic() >= deadline:
                    logs = command(docker_cli(
                        "logs", container_id), check=False,
                        timeout=10).stdout[-2048:]
                    fail("systemd PID 1 not ready: " + logs.replace("\n", " | "))
                time.sleep(0.25)
            result = command(docker_cli(
                "exec", container_id, "python3",
                "/usr/local/libexec/hepta_p1_dual_domain_inner_gate.py"),
                check=False,
                timeout=240)
            if result.returncode != 0:
                fail("inner gate failed: " + result.stdout[-2048:].replace(
                    "\n", " | "))
            inner = validate_inner(result.stdout, expected_run_id=run_id)
            observed_boot = command(docker_cli(
                "exec", container_id, "cat",
                "/proc/sys/kernel/random/boot_id"), timeout=15).stdout.strip()
            observed_cgroup = command(docker_cli(
                "exec", container_id, "cat", "/proc/1/cgroup"),
                timeout=15).stdout.strip()
            if (
                    observed_boot != inner["boot"]["boot_id"] or
                    observed_cgroup != inner["boot"]["pid1_cgroup"]):
                fail("outer/inner boot or PID1 cgroup evidence mismatch")
            command(docker_cli(
                "stop", "--time", "20", container_id), timeout=45)
    finally:
        try:
            cleanup_errors: list[str] = []
            if container_id is not None:
                inspected = command(docker_cli(
                    "container", "inspect", container_id),
                    check=False, timeout=30)
                owned = False
                if inspected.returncode == 0:
                    try:
                        values = json.loads(
                            inspected.stdout,
                            object_pairs_hook=reject_duplicate_json_keys)
                        owned = (
                            isinstance(values, list) and len(values) == 1 and
                            object_owned(values[0], run_id))
                    except json.JSONDecodeError:
                        owned = False
                if not owned:
                    cleanup_errors.append("container-ownership")
                elif command(docker_cli(
                        "rm", "--force", container_id),
                        check=False, timeout=30).returncode != 0:
                    cleanup_errors.append("container-remove")
                else:
                    try:
                        require_inspect_absent(
                            docker_cli(
                                "container", "inspect", container_id),
                            "disposable runtime container")
                        runtime_container_absent = True
                    except GateError:
                        cleanup_errors.append("container-residue")
            if image_id is not None:
                inspected = command(docker_cli(
                    "image", "inspect", tag), check=False, timeout=30)
                owned = False
                if inspected.returncode == 0:
                    try:
                        values = json.loads(
                            inspected.stdout,
                            object_pairs_hook=reject_duplicate_json_keys)
                        owned = (
                            isinstance(values, list) and len(values) == 1 and
                            object_owned(
                                values[0], run_id,
                                expected_image_id=image_id))
                    except json.JSONDecodeError:
                        owned = False
                if not owned:
                    cleanup_errors.append("image-ownership")
                elif command(docker_cli(
                        "image", "rm", tag), check=False,
                        timeout=60).returncode != 0:
                    cleanup_errors.append("image-remove")
                else:
                    try:
                        require_inspect_absent(
                            docker_cli("image", "inspect", tag),
                            "disposable runtime image tag")
                        runtime_image_tag_absent = True
                        require_inspect_absent(
                            docker_cli("image", "inspect", image_id),
                            "disposable runtime image ID")
                        runtime_image_id_absent = True
                    except GateError:
                        cleanup_errors.append("image-residue")
            if builder_attempted:
                if buildkit_record is None:
                    fail("BuildKit cleanup record missing")
                builder_cleanup = cleanup_isolated_builder(
                    isolated_builder or {
                        "names": builder_names, "container_id": None},
                    run_id, buildkit_record,
                    allow_partial=isolated_builder is None)
            if certification_request is not None:
                docker_socket_after = validate_local_docker_socket()
                apparmor_after = validate_loaded_apparmor(
                    provenance_before["apparmor"])
                docker_namespace_after = validate_docker_namespace_binding(
                    provenance_before["docker_namespace"], apparmor_after)
                provenance_after = load_certification_provenance(
                    certification_request,
                    now_ms=int(time.time() * 1000))
                if provenance_after != provenance_before:
                    fail("root-owned certification provenance drifted")
                if (
                        docker_socket_after != docker_socket_before or
                        apparmor_after != apparmor_before or
                        docker_namespace_after != docker_namespace_before):
                    fail("AppArmor/Docker daemon binding drifted across gate")
            if cleanup_errors:
                fail("disposable cleanup failed: " + ",".join(cleanup_errors))
        finally:
            cleanup_docker_config()

    if inner is None:
        fail("inner result missing")
    if not (
            runtime_container_absent and runtime_image_tag_absent and
            runtime_image_id_absent):
        fail("disposable runtime cleanup evidence is incomplete")
    input_after = {
        relative: read_stable(root / relative)[1]
        for relative in SOURCE_FILES
    }
    if input_after != input_before:
        fail("gate inputs changed during execution")
    input_manifest_sha256 = hashlib.sha256(canonical_json(input_after)).hexdigest()
    certifying = certification_request is not None
    if certifying and (
            reviewed_base_record is None or buildkit_record is None or
            buildx_toolchain is None or isolated_builder is None or
            builder_cleanup is None or apparmor_before is None or
            apparmor_after is None or docker_namespace_before is None or
            docker_namespace_after is None or docker_socket_before is None or
            docker_socket_after is None or
            provenance_after != provenance_before):
        fail("certifying evidence closure is incomplete")
    environment_review_record: Optional[dict[str, object]] = None
    if certifying:
        if environment_review_session is None:
            fail("signed environment review closure is missing")
        try:
            environment_review_session.reopen_at_gate_end()
            environment_review_record = environment_review_session.report_record()
        except ROOT_REVIEW.ReviewClosureError as error:
            raise GateError(str(error)) from error
    decision = "GO" if certifying else "REHEARSAL_ONLY"
    completed_at_ms = int(time.time() * 1000)
    expires_at_ms = (
        min(
            min(int(document.body["expires_at_ms"])
                for document in provenance_before.values()),
            int(environment_review_record["expires_at_ms"]))
        if certifying else
        completed_at_ms + REHEARSAL_REPORT_LIFETIME_MS)
    certification = {
        "requested": certifying,
        "eligible": certifying,
        "provenance": (
            {key: document.report_record()
             for key, document in sorted(provenance_before.items())}
            if certifying else None),
        "provenance_reopened_equal": certifying,
        "reviewed_base": reviewed_base_record,
        "reviewed_buildkit": buildkit_record,
        "buildx_toolchain": buildx_toolchain,
        "isolated_builder": isolated_builder,
        "isolated_builder_cleanup": builder_cleanup,
        "docker_socket_before": docker_socket_before,
        "docker_socket_after": docker_socket_after,
        "docker_socket_records_equal": certifying,
        "apparmor_before": apparmor_before,
        "apparmor_after": apparmor_after,
        "apparmor_records_equal": certifying,
        "docker_namespace_before": docker_namespace_before,
        "docker_namespace_after": docker_namespace_after,
        "docker_namespace_records_equal": certifying,
    }
    report = {
        "schema": SCHEMA,
        "run_id": run_id,
        "decision": decision,
        "passed": certifying,
        "rehearsal_passed": True,
        "certification_ready": certifying,
        "certification_blockers": (
            [] if certifying else list(CERTIFICATION_BLOCKERS)),
        "scope": "broker-free-p1-dual-domain-rootful-prerequisite-only",
        "started_at_ms": started_at_ms,
        "completed_at_ms": completed_at_ms,
        "expires_at_ms": expires_at_ms,
        "body_sha256": "",
        "paper_test_admission_candidate": False,
        "paper_admission_authorized": False,
        "paper_authorized": False,
        "live_authorized": False,
        "mutation_authorized": False,
        "direct_broker_access": False,
        "order_submission_authorized": False,
        "duration_ms": completed_at_ms - started_at_ms,
        "lineage": {
            "source_commit": source_commit,
            "expected_source_commit": expected_source_commit,
            "source_tree_clean": source_tree_clean,
            "all_inputs_versioned": all_inputs_versioned,
            "inputs_stable": True,
            "final_lineage": source_tree_clean,
            "input_manifest_sha256": input_manifest_sha256,
            "runner_sha256": input_after[
                "scripts/run_hepta_p1_dual_domain_rootful_gate.py"]["sha256"],
        },
        "inputs": input_after,
        "generated_input_sha256": generated_hashes,
        "platform": {
            "host_kernel": os.uname().release,
            "host_architecture": os.uname().machine,
            "docker_client": command(["docker", "--version"]).stdout.strip(),
            "docker_server_version": server.get("Version", ""),
            "docker_server_api_version": server.get("ApiVersion", ""),
            "docker_server_os": docker_info.get("OperatingSystem", ""),
            "docker_server_architecture": docker_info.get("Architecture", ""),
            "docker_cgroup_driver": docker_info.get("CgroupDriver", ""),
            "docker_cgroup_version": docker_info.get("CgroupVersion", ""),
            "docker_default_runtime": docker_info.get("DefaultRuntime", ""),
            "docker_security_options": sorted(
                docker_info.get("SecurityOptions", [])),
            "base_image_reference": base,
            "base_image_id": base_id,
            "base_image_os": base_record.get("Os", ""),
            "base_image_architecture": base_record.get("Architecture", ""),
            "systemd": inner["boot"]["systemd"],
            "container_boot_id": inner["boot"]["boot_id"],
            "container_pid1_cgroup": inner["boot"]["pid1_cgroup"],
        },
        "container": {
            "image_id": image_id,
            "network_mode": "none",
            "read_only_rootfs": True,
            "private_cgroup_namespace": True,
            "privileged": False,
            "bind_mounts": 0,
            "published_ports": 0,
            "devices": 0,
            "device_requests": 0,
            "links": 0,
            "tmpfs_allowlist": RUNTIME_TMPFS,
            "capabilities": list(RUNTIME_CAPABILITIES),
            "apparmor_profile": APPARMOR_PROFILE,
        },
        "disposable_cleanup": {
            "container_absent": True,
            "image_tag_absent": True,
            "image_id_absent": True,
        },
        "certification": certification,
        "environment_review_closure": environment_review_record,
        "inner": inner,
        "boundary": EXPECTED_BOUNDARY,
    }
    body = dict(report)
    body.pop("body_sha256")
    report["body_sha256"] = canonical_sha256(body)
    return validate_report(report)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--run", action="store_true")
    parser.add_argument("--allow-dirty-rehearsal", action="store_true")
    parser.add_argument(
        "--certify", action="store_true",
        help="request formal GO; requires all reviewed provenance inputs")
    parser.add_argument("--base-image", required=True)
    parser.add_argument("--buildkit-image")
    parser.add_argument("--buildx-binary-sha256")
    parser.add_argument("--reviewed-base-provenance", type=Path)
    parser.add_argument("--reviewed-base-provenance-sha256")
    parser.add_argument("--reviewed-builder-provenance", type=Path)
    parser.add_argument("--reviewed-builder-provenance-sha256")
    parser.add_argument("--apparmor-provenance", type=Path)
    parser.add_argument("--apparmor-provenance-sha256")
    parser.add_argument(
        "--docker-apparmor-namespace-provenance", type=Path)
    parser.add_argument(
        "--docker-apparmor-namespace-provenance-sha256")
    ROOT_REVIEW.add_arguments(parser)
    parser.add_argument("--expected-source-commit", required=True)
    parser.add_argument("--report", type=Path, required=True)
    arguments = parser.parse_args()
    try:
        report_path = safe_report_path(arguments.report)
        if not arguments.run:
            print(
                "hepta_p1_dual_domain_rootful_gate: disabled; pass --run "
                "explicitly",
                file=sys.stderr,
            )
            return 78
        if arguments.certify and arguments.allow_dirty_rehearsal:
            fail("--certify cannot be combined with --allow-dirty-rehearsal")
        try:
            environment_review = ROOT_REVIEW.inputs_from_arguments(
                arguments, certify=arguments.certify)
        except ROOT_REVIEW.ReviewClosureError as error:
            raise GateError(str(error)) from error
        certification_request = certification_request_from_values(
            certify=arguments.certify,
            buildkit_image=arguments.buildkit_image,
            buildx_binary_sha256=arguments.buildx_binary_sha256,
            reviewed_base_path=arguments.reviewed_base_provenance,
            reviewed_base_sha256=arguments.reviewed_base_provenance_sha256,
            reviewed_builder_path=arguments.reviewed_builder_provenance,
            reviewed_builder_sha256=
                arguments.reviewed_builder_provenance_sha256,
            reviewed_apparmor_path=arguments.apparmor_provenance,
            reviewed_apparmor_sha256=arguments.apparmor_provenance_sha256,
            reviewed_docker_namespace_path=
                arguments.docker_apparmor_namespace_provenance,
            reviewed_docker_namespace_sha256=
                arguments.docker_apparmor_namespace_provenance_sha256,
            environment_review=environment_review,
        )
        report = execute(
            arguments.base_image,
            arguments.expected_source_commit,
            allow_dirty_rehearsal=arguments.allow_dirty_rehearsal,
            certification_request=certification_request,
        )
        atomic_report(report_path, report)
    except (
            GateError, OSError, ValueError, subprocess.SubprocessError
            ) as error:
        print(
            "hepta_p1_dual_domain_rootful_gate: FAIL: " +
            (str(error) or type(error).__name__)[:2048],
            file=sys.stderr,
        )
        return 1
    status = str(report["decision"])
    print(
        "hepta_p1_dual_domain_rootful_gate: " + status +
        " watch_domains=2 inert_paper_domains=2 broker_connections=0 "
        "orders=0 paper_admission_authorized=0")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
