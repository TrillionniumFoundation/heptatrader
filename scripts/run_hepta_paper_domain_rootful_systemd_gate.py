#!/usr/bin/env python3

"""Run the disposable effective-systemd PAPER-domain lifecycle gate.

The gate instantiates the production templated service, preflight and socket
units with a broker-free inert executable.  It never mounts host paths,
publishes ports, stages IBAPI or reads a real credential.  The host systemd and
nftables namespaces are not touched.
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
import secrets
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


LEGACY_SCHEMA = "hepta.paper-domain-rootful-systemd-gate.v1"
SCHEMA = "hepta.paper-domain-rootful-systemd-gate.v2"
INNER_SCHEMA = "hepta.paper-domain-rootful-systemd-inner.v2"
INNER_MARKER = "HEPTA_PAPER_DOMAIN_ROOTFUL_SYSTEMD_RESULT="
PURPOSE = "paper-domain-rootful-systemd-gate"
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
FRESHNESS_KEYS = frozenset({"issued_at_ms", "expires_at_ms"})
REVIEWED_BASE_KEYS = frozenset({
    "schema", "decision", *FRESHNESS_KEYS,
    "image_id", "repo_digest", "labels_sha256"})
REVIEWED_BUILDER_KEYS = frozenset({
    "schema", "decision", *FRESHNESS_KEYS,
    "image_id", "repo_digest", "config_sha256", "buildkit_version",
    "buildx_version", "buildx_binary_sha256", "docker_server_version",
    "docker_server_api_version", "docker_server_git_commit"})
REVIEWED_APPARMOR_KEYS = frozenset({
    "schema", "decision", *FRESHNESS_KEYS,
    "profile", "policy_source_sha256", "profile_sha256", "raw_sha256",
    "raw_abi"})
REVIEWED_DOCKER_NAMESPACE_KEYS = frozenset({
    "schema", "decision", *FRESHNESS_KEYS,
    "docker_daemon_id", "docker_daemon_pid",
    "docker_daemon_start_time_ticks", "host_boot_id",
    "host_namespace_name", "host_namespace_level", "host_namespace_stacked",
    "daemon_namespace_name", "daemon_namespace_level",
    "daemon_namespace_stacked"})
REVIEWED_BASE_LABELS = {
    "io.hepta.rootful-systemd-base.offline-ready": "true",
    "io.hepta.rootful-systemd-base.version": "1",
}
SEMANTIC_VERSION = re.compile(
    r"^[0-9]+\.[0-9]+\.[0-9]+(?:[-+][0-9A-Za-z._-]+)?$")
BUILDKIT_VERSION = re.compile(
    r"^v[0-9]+\.[0-9]+\.[0-9]+(?:[-+][0-9A-Za-z._-]+)?$")
DOCKER_API_VERSION = re.compile(r"^[1-9][0-9]*\.[0-9]+$")
SAFE_BUILD_ID = re.compile(r"^[0-9A-Za-z][0-9A-Za-z._+-]{0,127}$")
DOCKER_DAEMON_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9:._-]{0,127}$")
APPARMOR_RAW_ABI = re.compile(r"^v[1-9][0-9]{0,2}$")
APPARMOR_POLICY_ENTRY = re.compile(r"^[A-Za-z0-9_.:@+=-]{1,255}$")
CANONICAL_COMMIT = re.compile(r"^[0-9a-f]{40}$")
CANONICAL_IMAGE_ID = re.compile(r"^sha256:[0-9a-f]{64}$")
CANONICAL_BOOT_ID = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-"
    r"[0-9a-f]{4}-[0-9a-f]{12}$")
MAX_PROVENANCE = 64 * 1024
MAX_PROVENANCE_LIFETIME_MS = 24 * 60 * 60 * 1000
REPORT_LIFETIME_MS = 60 * 60 * 1000
MAX_APPARMOR_SCALAR = 4096
MAX_TOOL_BINARY = 256 * 1024 * 1024
APPARMOR_CONTROL_ROOT = Path("/sys/kernel/security/apparmor")
APPARMOR_POLICY_ROOT = APPARMOR_CONTROL_ROOT / "policy"
DOCKER_SOCKET = Path("/run/docker.sock")
RENAME_NOREPLACE = 1
_LIBC = ctypes.CDLL(None, use_errno=True)
PINNED_IMAGE = re.compile(
    r"^[a-z0-9][a-z0-9._/:-]*@sha256:[0-9a-f]{64}$")
MAX_INPUT = 4 * 1024 * 1024
MAX_OUTPUT = 4 * 1024 * 1024
MAX_REPORT = 4 * 1024 * 1024
COMMAND_ENV = {
    "PATH": "/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin",
    "LANG": "C",
    "LC_ALL": "C",
    "TZ": "UTC",
}
RUNTIME_CAPABILITIES = (
    "CHOWN", "DAC_OVERRIDE", "FOWNER", "KILL", "MKNOD", "NET_ADMIN",
    "SETGID", "SETPCAP", "SETUID", "SYS_ADMIN", "SYS_CHROOT",
)
RUNTIME_TMPFS = {
    "/etc/heptatrader": "rw,nosuid,nodev,noexec,mode=0755,size=8m",
    "/usr/share/heptatrader": "rw,nosuid,nodev,noexec,mode=0755,size=8m",
    "/run": "rw,nosuid,nodev,mode=0755,size=64m",
    "/run/lock": "rw,nosuid,nodev,noexec,mode=0755,size=8m",
    "/tmp": "rw,nosuid,nodev,noexec,mode=1777,size=64m",
    "/var/lib/hepta-ib-execution-codex-a":
        "rw,nosuid,nodev,noexec,mode=0700,size=16m",
    "/var/lib/hepta-ib-execution-openclaw-b":
        "rw,nosuid,nodev,noexec,mode=0700,size=16m",
    "/var/log": "rw,nosuid,nodev,noexec,mode=0755,size=32m",
    "/var/tmp": "rw,nosuid,nodev,noexec,mode=1777,size=32m",
}
BUILDKIT_STATE_DIRECTORY = "/var/lib/buildkit"
BUILDER_ROLE_LABEL = "io.hepta.role"
BUILDER_RUN_LABEL = "io.hepta.run-id"
BUILDER_IMAGE_LABEL = "io.hepta.buildkit-image-id"
BUILDER_NAME_LABEL = "io.hepta.buildx-builder"
BUILDER_DAEMON_ROLE = "isolated-buildkit-daemon"
BUILDER_STATE_ROLE = "isolated-buildkit-state"
SOURCE_FILES = {
    "scripts/run_hepta_paper_domain_rootful_systemd_gate.py":
        ("gate-inputs/runner.py", 0o644),
    "scripts/hepta_rootful_review_closure_consumer.py":
        ("gate-inputs/hepta_rootful_review_closure_consumer.py", 0o644),
    "scripts/hepta_broker_egress_policy.py":
        ("install-root/usr/libexec/hepta-broker-egress-policy", 0o755),
    "scripts/hepta_ib_paper_domain_authority.py":
        ("install-root/usr/libexec/hepta-ib-paper-domain-authority", 0o755),
    "systemd/hepta-broker-egress-policy.service":
        ("install-root/usr/lib/systemd/system/"
         "hepta-broker-egress-policy.service", 0o644),
    "systemd/hepta-ib-paper-domain-preflight@.service":
        ("install-root/usr/lib/systemd/system/"
         "hepta-ib-paper-domain-preflight@.service", 0o644),
    "systemd/hepta-execution-ib-paper@.service":
        ("install-root/usr/lib/systemd/system/"
         "hepta-execution-ib-paper@.service", 0o644),
    "systemd/hepta-execution-ib-paper@.socket":
        ("install-root/usr/lib/systemd/system/"
         "hepta-execution-ib-paper@.socket", 0o644),
    "systemd/hepta-execution-events-ib-paper@.socket":
        ("install-root/usr/lib/systemd/system/"
         "hepta-execution-events-ib-paper@.socket", 0o644),
    "systemd/hepta-execution-ib-paper@.service.d/"
    "10-hepta-broker-egress-policy.conf":
        ("install-root/usr/lib/systemd/system/"
         "hepta-execution-ib-paper@.service.d/"
         "10-hepta-broker-egress-policy.conf", 0o644),
    "systemd/hepta-broker-network-policy-v1.json":
        ("provision-root/hepta-broker-network-policy-v1.json", 0o644),
    "systemd/hepta-service-identities-v1.json":
        ("provision-root/hepta-service-identities-v1.json", 0o644),
    "tests/paper_domain_rootful_systemd/"
    "hepta_paper_inert_execution_stub.py":
        ("install-root/usr/libexec/hepta-ib-executiond", 0o755),
    "tests/paper_domain_rootful_systemd/Dockerfile":
        ("tests/paper_domain_rootful_systemd/Dockerfile", 0o644),
    "tests/paper_domain_rootful_systemd/"
    "hepta-paper-domain-systemd-entrypoint":
        ("tests/paper_domain_rootful_systemd/"
         "hepta-paper-domain-systemd-entrypoint", 0o755),
    "tests/paper_domain_rootful_systemd/"
    "hepta-paper-domain-rootful-systemd.target":
        ("tests/paper_domain_rootful_systemd/"
         "hepta-paper-domain-rootful-systemd.target", 0o644),
    "tests/paper_domain_rootful_systemd/"
    "hepta_paper_domain_rootful_inner_gate.py":
        ("tests/paper_domain_rootful_systemd/"
         "hepta_paper_domain_rootful_inner_gate.py", 0o755),
}
EXPECTED_CHECKS = {
    "real_templated_units_loaded",
    "preflight_manual_start_refused_before_authority",
    "socket_manual_start_refused_before_authority",
    "broker_guard_started_under_systemd",
    "idle_concurrent_cold_start_has_one_authority",
    "domain_b_full_composition_active",
    "domain_a_full_composition_active",
    "second_domain_flock_rejected_without_listener",
    "daemon_sigkill_restarts_under_same_authority",
    "startup_failure_hits_composition_start_limit_and_reclaims_all",
    "systemd_exec_stop_post_is_input_independent_deny_all",
    "stopped_socket_cannot_reactivate_daemon",
}
EXPECTED_BOUNDARY = {
    "paper_unit_instances_observed": 8,
    "broker_policy_unit_observed": 1,
    "domain_compositions_observed": 2,
    "max_concurrent_inert_execution_stub_processes": 1,
    "ib_api_binaries": 0,
    "real_broker_connections": 0,
    "broker_protocol_messages": 0,
    "real_credentials": 0,
    "inert_credential_fixtures": 4,
    "paper_orders": 0,
    "live_authorized": False,
    "host_systemd_units_touched": 0,
    "host_nft_tables_touched": 0,
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
    expected_input_manifest_sha256: str
    expected_runner_sha256: str
    environment_review: Optional[ROOT_REVIEW.ReviewClosureInputs] = None


@dataclass(frozen=True)
class RootProvenanceDocument:
    kind: str
    path: Path
    document_sha256: str
    body: dict[str, object]
    metadata: tuple[int, ...]
    mode: str

    def report_record(self) -> dict[str, object]:
        return {
            **self.body,
            "path": os.fspath(self.path),
            "document_sha256": self.document_sha256,
            "root_owned": True,
            "canonical_json": True,
            "mode": self.mode,
            "device": self.metadata[0],
            "inode": self.metadata[1],
            "nlink": self.metadata[3],
            "uid": self.metadata[4],
            "gid": self.metadata[5],
            "identity_sha256": "sha256:" + hashlib.sha256(
                json.dumps(
                    list(self.metadata), separators=(",", ":")
                ).encode("ascii")
            ).hexdigest(),
        }


class GateError(RuntimeError):
    pass


def fail(message: str) -> None:
    raise GateError(message)


def repository_root() -> Path:
    return Path(__file__).resolve(strict=True).parents[1]


def require_pinned_image(value: str) -> str:
    if PINNED_IMAGE.fullmatch(value) is None:
        fail("--base-image must be exact name@sha256:<64 lowercase hex>")
    return value


def require_sha256(value: str, label: str) -> str:
    if re.fullmatch(r"sha256:[0-9a-f]{64}", value) is None:
        fail(label + " must be sha256:<64 lowercase hex>")
    return value


def require_expected_commit(value: str) -> str:
    if CANONICAL_COMMIT.fullmatch(value) is None:
        fail("--expected-source-commit must be 40 lowercase hex characters")
    return value


def architecture_matches(image_architecture: str, host_architecture: str) -> bool:
    aliases = {
        "x86_64": "amd64",
        "aarch64": "arm64",
    }
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
        prefix="hepta-paper-domain-systemd-docker-")
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
    labels = value.get("Config", {}).get("Labels")
    if (
            not isinstance(labels, dict) or
            labels.get("io.hepta.purpose") != PURPOSE or
            labels.get(RUN_LABEL_KEY) != run_id):
        return False
    return (
        expected_image_id is None or
        value.get("Id") == expected_image_id)


def validate_container_inspect_record(
        value: object, *, container_id: str, image_id: str,
        name: str, run_id: str) -> None:
    if not isinstance(value, dict):
        fail("container inspect is not an object")
    host = value.get("HostConfig")
    config = value.get("Config")
    mounts = value.get("Mounts")
    if (
            not isinstance(host, dict) or
            not isinstance(config, dict) or
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
            config.get("Hostname") != "hepta-paper-domain-systemd" or
            config.get("User") != "0:0" or
            config.get("WorkingDir") != "/" or
            config.get("Entrypoint") != [
                "/usr/local/libexec/hepta-paper-domain-systemd-entrypoint"] or
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
            host.get("PidsLimit") != 512 or
            host.get("Memory") != 1024 * 1024 * 1024 or
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
            set(host.get("CapAdd") or []) !=
            {"CAP_" + item for item in RUNTIME_CAPABILITIES}):
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
            parsed.get("HEPTA_PAPER_DOMAIN_SYSTEMD_DISPOSABLE") != "1" or
            parsed.get("HEPTA_PAPER_DOMAIN_SYSTEMD_RUN_ID") != run_id):
        fail("container gate environment mismatch")
    forbidden = re.compile(
        r"(?:SECRET|TOKEN|PASSWORD|CREDENTIAL|AUTHORIZATION|FENCE|BROKER)",
        re.IGNORECASE)
    if any(
            forbidden.search(key) is not None
            for key in parsed):
        fail("secret/broker-shaped container environment key present")


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
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            fail("JSON object contains duplicate field: " + key)
        result[key] = value
    return result


def canonical_sha256(value: object) -> str:
    return "sha256:" + hashlib.sha256(canonical_json(value)).hexdigest()


def canonical_object_sha256(value: object) -> str:
    try:
        raw = json.dumps(
            value, sort_keys=True, separators=(",", ":"),
            ensure_ascii=True, allow_nan=False).encode("ascii")
    except (TypeError, ValueError, UnicodeEncodeError) as error:
        raise GateError("value is not canonical JSON") from error
    return "sha256:" + hashlib.sha256(raw).hexdigest()


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
        metadata: os.stat_result, kind: str) -> tuple[tuple[int, ...], str]:
    mode = stat.S_IMODE(metadata.st_mode)
    if (
            not stat.S_ISREG(metadata.st_mode) or metadata.st_nlink != 1 or
            metadata.st_uid != 0 or metadata.st_gid != 0 or
            mode not in (0o400, 0o600) or
            metadata.st_size < 2 or metadata.st_size > MAX_PROVENANCE):
        fail(kind + " provenance metadata is not root:root 0400/0600 regular")
    return metadata_identity(metadata, PROVENANCE_FILE_FIELDS), format(mode, "04o")


def read_provenance_descriptor(
        descriptor: int, *, expected_size: int, kind: str) -> bytes:
    chunks: list[bytes] = []
    total = 0
    while True:
        chunk = os.read(descriptor, min(8192, MAX_PROVENANCE + 1 - total))
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
        path: Path, *, kind: str) -> tuple[bytes, tuple[int, ...], str]:
    """Open every component relative to / and prove all names remain bound."""

    value = os.fspath(path)
    if (
            not isinstance(path, Path) or not path.is_absolute() or
            value.startswith("//") or
            value != os.path.normpath(value) or path.name in ("", ".", "..") or
            any(part in ("", ".", "..") for part in path.parts[1:])):
        fail(kind + " provenance path must be absolute lexical-canonical")
    directories: list[int] = []
    links: list[tuple[int, str, int, tuple[int, ...]]] = []
    descriptor: Optional[int] = None
    reopened: Optional[int] = None
    try:
        root_fd = os.open(
            "/", os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC |
            getattr(os, "O_NOFOLLOW", 0))
        directories.append(root_fd)
        validate_provenance_directory_metadata(os.fstat(root_fd), kind)
        current = root_fd
        for component in path.parts[1:-1]:
            before = os.stat(component, dir_fd=current, follow_symlinks=False)
            before_id = validate_provenance_directory_metadata(before, kind)
            opened = os.open(
                component,
                os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC |
                getattr(os, "O_NOFOLLOW", 0), dir_fd=current)
            directories.append(opened)
            opened_id = validate_provenance_directory_metadata(
                os.fstat(opened), kind)
            named_after = validate_provenance_directory_metadata(
                os.stat(component, dir_fd=current, follow_symlinks=False), kind)
            if not (before_id == opened_id == named_after):
                fail(kind + " provenance ancestor changed while anchoring")
            links.append((current, component, opened, opened_id))
            current = opened
        final = path.name
        named_before, mode_before = validate_provenance_file_metadata(
            os.stat(final, dir_fd=current, follow_symlinks=False), kind)
        descriptor = os.open(
            final, os.O_RDONLY | os.O_CLOEXEC |
            getattr(os, "O_NOFOLLOW", 0), dir_fd=current)
        opened_id, opened_mode = validate_provenance_file_metadata(
            os.fstat(descriptor), kind)
        if named_before != opened_id or mode_before != opened_mode:
            fail(kind + " provenance final name changed while opening")
        raw = read_provenance_descriptor(
            descriptor, expected_size=opened_id[6], kind=kind)
        after_id, after_mode = validate_provenance_file_metadata(
            os.fstat(descriptor), kind)
        named_after, named_mode = validate_provenance_file_metadata(
            os.stat(final, dir_fd=current, follow_symlinks=False), kind)
        reopened = os.open(
            final, os.O_RDONLY | os.O_CLOEXEC |
            getattr(os, "O_NOFOLLOW", 0), dir_fd=current)
        reopened_id, reopened_mode = validate_provenance_file_metadata(
            os.fstat(reopened), kind)
        raw_reopened = read_provenance_descriptor(
            reopened, expected_size=reopened_id[6], kind=kind)
        reopened_after, reopened_after_mode = validate_provenance_file_metadata(
            os.fstat(reopened), kind)
        if not (
                named_before == opened_id == after_id == named_after ==
                reopened_id == reopened_after and
                mode_before == opened_mode == after_mode == named_mode ==
                reopened_mode == reopened_after_mode and raw == raw_reopened):
            fail(kind + " provenance file changed across secure reopen")
        for parent_fd, component, child_fd, expected in links:
            named = validate_provenance_directory_metadata(
                os.stat(component, dir_fd=parent_fd, follow_symlinks=False), kind)
            opened = validate_provenance_directory_metadata(
                os.fstat(child_fd), kind)
            if named != expected or opened != expected:
                fail(kind + " provenance ancestor drifted after read")
        return raw, opened_id, opened_mode
    except FileNotFoundError as error:
        raise GateError(kind + " provenance is missing") from error
    finally:
        if reopened is not None:
            os.close(reopened)
        if descriptor is not None:
            os.close(descriptor)
        for item in reversed(directories):
            os.close(item)


def read_root_canonical_provenance(
        path: Path, expected_sha256: str, *, kind: str,
        expected_schema: str, expected_keys: frozenset[str],
        now_ms: int) -> RootProvenanceDocument:
    expected_sha256 = require_sha256(expected_sha256, kind + " provenance pin")
    raw, metadata, mode = read_anchored_root_provenance(path, kind=kind)
    observed = "sha256:" + hashlib.sha256(raw).hexdigest()
    if observed != expected_sha256:
        fail(kind + " provenance document pin mismatch")
    try:
        body = json.loads(raw, object_pairs_hook=reject_duplicate_json_keys)
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise GateError(kind + " provenance is invalid JSON") from error
    if (
            not isinstance(body, dict) or set(body) != expected_keys or
            body.get("schema") != expected_schema or
            body.get("decision") != "GO" or canonical_json(body) != raw):
        fail(kind + " provenance canonical exact-field contract mismatch")
    issued = body.get("issued_at_ms")
    expires = body.get("expires_at_ms")
    if (
            type(issued) is not int or type(expires) is not int or
            issued <= 0 or expires <= issued or
            expires - issued > MAX_PROVENANCE_LIFETIME_MS or
            issued > now_ms or expires <= now_ms):
        fail(kind + " provenance is expired, future-dated, or too long-lived")
    return RootProvenanceDocument(
        kind=kind, path=path, document_sha256=observed,
        body=body, metadata=metadata, mode=mode)


def validate_base_provenance(document: RootProvenanceDocument) -> None:
    body = document.body
    if (
            CANONICAL_IMAGE_ID.fullmatch(str(body.get("image_id", ""))) is None or
            PINNED_IMAGE.fullmatch(str(body.get("repo_digest", ""))) is None or
            re.fullmatch(
                r"sha256:[0-9a-f]{64}", str(body.get("labels_sha256", "")))
            is None):
        fail("reviewed base provenance values are invalid")


def validate_builder_provenance(document: RootProvenanceDocument) -> None:
    body = document.body
    if (
            CANONICAL_IMAGE_ID.fullmatch(str(body.get("image_id", ""))) is None or
            PINNED_IMAGE.fullmatch(str(body.get("repo_digest", ""))) is None or
            any(re.fullmatch(r"sha256:[0-9a-f]{64}", str(body.get(field, "")))
                is None for field in ("config_sha256", "buildx_binary_sha256")) or
            BUILDKIT_VERSION.fullmatch(
                str(body.get("buildkit_version", ""))) is None or
            SEMANTIC_VERSION.fullmatch(
                str(body.get("buildx_version", ""))) is None or
            SEMANTIC_VERSION.fullmatch(
                str(body.get("docker_server_version", ""))) is None or
            DOCKER_API_VERSION.fullmatch(
                str(body.get("docker_server_api_version", ""))) is None or
            SAFE_BUILD_ID.fullmatch(
                str(body.get("docker_server_git_commit", ""))) is None):
        fail("reviewed builder provenance values are invalid")


def validate_apparmor_provenance(document: RootProvenanceDocument) -> None:
    body = document.body
    if (
            body.get("profile") != APPARMOR_PROFILE or
            any(re.fullmatch(r"sha256:[0-9a-f]{64}", str(body.get(field, "")))
                is None for field in (
                    "policy_source_sha256", "profile_sha256", "raw_sha256")) or
            APPARMOR_RAW_ABI.fullmatch(str(body.get("raw_abi", ""))) is None):
        fail("reviewed AppArmor provenance values are invalid")


def validate_docker_namespace_provenance(
        document: RootProvenanceDocument) -> None:
    body = document.body
    if (
            DOCKER_DAEMON_ID.fullmatch(
                str(body.get("docker_daemon_id", ""))) is None or
            type(body.get("docker_daemon_pid")) is not int or
            not 1 < int(body["docker_daemon_pid"]) <= 4_194_304 or
            type(body.get("docker_daemon_start_time_ticks")) is not int or
            int(body["docker_daemon_start_time_ticks"]) <= 0 or
            CANONICAL_BOOT_ID.fullmatch(
                str(body.get("host_boot_id", ""))) is None or
            body.get("host_namespace_name") != "root" or
            body.get("host_namespace_level") != 0 or
            body.get("host_namespace_stacked") is not False or
            body.get("daemon_namespace_name") != "root" or
            body.get("daemon_namespace_level") != 0 or
            body.get("daemon_namespace_stacked") is not False):
        fail("reviewed Docker namespace provenance values are invalid")


def load_certification_provenance(
        request: CertificationRequest, *, now_ms: int,
        ) -> dict[str, RootProvenanceDocument]:
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
    paths = [os.fspath(item[0]) for item in specs.values()]
    pins = [str(item[1]) for item in specs.values()]
    if len(set(paths)) != 4 or len(set(pins)) != 4:
        fail("four independent provenance paths and document pins are required")
    documents: dict[str, RootProvenanceDocument] = {}
    for kind, (path, digest, schema, keys, validator) in specs.items():
        document = read_root_canonical_provenance(
            path, digest, kind=kind, expected_schema=schema,
            expected_keys=keys, now_ms=now_ms)
        validator(document)
        documents[kind] = document
    if len({(item.metadata[0], item.metadata[1]) for item in documents.values()}) != 4:
        fail("four provenance paths must resolve to distinct files/inodes")
    if (
            documents["builder"].body["repo_digest"] !=
            request.buildkit_image or
            documents["builder"].body["buildx_binary_sha256"] !=
            request.buildx_binary_sha256):
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
        expected_input_manifest_sha256: Optional[str],
        expected_runner_sha256: Optional[str],
        environment_review: Optional[
            ROOT_REVIEW.ReviewClosureInputs] = None,
        ) -> Optional[CertificationRequest]:
    values = (
        buildkit_image, buildx_binary_sha256,
        reviewed_base_path, reviewed_base_sha256,
        reviewed_builder_path, reviewed_builder_sha256,
        reviewed_apparmor_path, reviewed_apparmor_sha256,
        reviewed_docker_namespace_path, reviewed_docker_namespace_sha256,
        expected_input_manifest_sha256, expected_runner_sha256,
        environment_review)
    if not certify:
        if any(value is not None for value in values):
            fail("certification-only pins/provenance require explicit --certify")
        return None
    if os.geteuid() != 0:
        fail("--certify requires effective UID 0")
    if any(value is None for value in values):
        fail(
            "--certify requires the signed review closure plus all "
            "provenance, tool, and source input pins")
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
        expected_input_manifest_sha256=require_sha256(
            str(expected_input_manifest_sha256), "input manifest digest"),
        expected_runner_sha256=require_sha256(
            str(expected_runner_sha256), "runner digest"),
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
    if not raw or not raw.endswith(b"\n") or raw.count(b"\n") != 1 or b"\0" in raw:
        fail("kernel evidence scalar is not one canonical line")
    try:
        result = raw[:-1].decode("ascii", errors="strict")
    except UnicodeDecodeError as error:
        raise GateError("kernel evidence scalar is not ASCII") from error
    if not result or result != result.strip():
        fail("kernel evidence scalar value is invalid")
    return result


def validate_loaded_apparmor(
        provenance: RootProvenanceDocument) -> dict[str, object]:
    if read_kernel_scalar(Path("/sys/module/apparmor/parameters/enabled")) != "Y":
        fail("AppArmor kernel enforcement is not enabled")
    namespace = {
        "name": read_kernel_scalar(APPARMOR_CONTROL_ROOT / ".ns_name"),
        "level": read_kernel_scalar(APPARMOR_CONTROL_ROOT / ".ns_level"),
        "stacked": read_kernel_scalar(APPARMOR_CONTROL_ROOT / ".ns_stacked"),
        "legacy_stacked": read_kernel_scalar(APPARMOR_CONTROL_ROOT / ".stacked"),
    }
    if namespace != {
            "name": "root", "level": "0", "stacked": "no",
            "legacy_stacked": "no"}:
        fail("AppArmor is not in the unstacked root namespace")
    profiles_root = APPARMOR_POLICY_ROOT / "profiles"
    raw_root = APPARMOR_POLICY_ROOT / "raw_data"
    entries = sorted(os.scandir(profiles_root), key=lambda item: item.name)
    if not entries or len(entries) > 4096:
        fail("loaded AppArmor profile inventory outside bound")
    names: list[str] = []
    matches: list[Path] = []
    for entry in entries:
        if APPARMOR_POLICY_ENTRY.fullmatch(entry.name) is None:
            fail("loaded AppArmor profile entry name is invalid")
        metadata = os.lstat(entry.path)
        if (
                not stat.S_ISDIR(metadata.st_mode) or metadata.st_uid != 0 or
                metadata.st_gid != 0 or stat.S_IMODE(metadata.st_mode) != 0o755):
            fail("loaded AppArmor profile directory metadata mismatch")
        name = read_kernel_scalar(Path(entry.path) / "name")
        names.append(name)
        if name == APPARMOR_PROFILE:
            matches.append(Path(entry.path))
    if len(matches) != 1 or names.count(APPARMOR_PROFILE) != 1:
        fail("required AppArmor profile is not uniquely loaded")
    entry = matches[0]
    values = {
        field: read_kernel_scalar(entry / field)
        for field in ("name", "mode", "attach", "learning_count", "sha256")}
    if (
            values["name"] != APPARMOR_PROFILE or values["mode"] != "enforce" or
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
        r"\.\./\.\./raw_data/([1-9][0-9]{0,19})/raw_data", raw_data_target)
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
            provenance.body["profile_sha256"] != "sha256:" + values["sha256"] or
            provenance.body["raw_sha256"] != "sha256:" + raw_sha or
            provenance.body["raw_abi"] != raw_abi):
        fail("reviewed AppArmor provenance does not bind loaded policy")
    return {
        "profile": APPARMOR_PROFILE, "mode": "enforce",
        "attach": APPARMOR_PROFILE, "learning_count": 0,
        "profile_sha256": "sha256:" + values["sha256"],
        "raw_sha256": "sha256:" + raw_sha, "raw_abi": raw_abi,
        "raw_data_id": raw_id, "profile_inventory_count": len(entries),
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
    closing = stat_line.rfind(") ")
    if not stat_line.startswith(f"{pid} (") or closing <= len(str(pid)) + 2:
        fail("Docker daemon stat record malformed")
    fields = stat_line[closing + 2:].split()
    if (
            comm != "dockerd" or len(fields) < 20 or
            re.fullmatch(r"[1-9][0-9]*", fields[19]) is None):
        fail("Docker daemon process identity mismatch")
    return {
        "pid": pid, "start_time_ticks": int(fields[19], 10),
        "comm": "dockerd", "process_inode": metadata.st_ino,
    }


def docker_daemon_id() -> str:
    raw = command(docker_cli("info", "--format", "{{json .ID}}"), timeout=30).stdout
    if not raw.endswith("\n") or raw.count("\n") != 1:
        fail("Docker daemon ID response framing mismatch")
    try:
        value = json.loads(raw)
    except json.JSONDecodeError as error:
        raise GateError("Docker daemon ID response malformed") from error
    if not isinstance(value, str) or DOCKER_DAEMON_ID.fullmatch(value) is None:
        fail("Docker daemon ID response invalid")
    return value


def validate_local_docker_socket() -> dict[str, object]:
    before = os.lstat(DOCKER_SOCKET)
    after = os.lstat(DOCKER_SOCKET)
    fields = ("st_dev", "st_ino", "st_mode", "st_nlink", "st_uid", "st_gid")
    if (
            any(getattr(before, field) != getattr(after, field) for field in fields) or
            not stat.S_ISSOCK(before.st_mode) or before.st_nlink != 1 or
            before.st_uid != 0 or stat.S_IMODE(before.st_mode) & 0o002):
        fail("local Docker socket ownership/identity is unsafe")
    return {
        "device": before.st_dev, "inode": before.st_ino,
        "mode": format(stat.S_IMODE(before.st_mode), "04o"),
        "uid": 0, "gid": before.st_gid,
        "owner_root": True, "world_writable": False,
    }


def validate_docker_namespace_binding(
        provenance: RootProvenanceDocument,
        apparmor: dict[str, object]) -> dict[str, object]:
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
            before["start_time_ticks"] != body["docker_daemon_start_time_ticks"] or
            boot_before != body["host_boot_id"] or
            daemon_id != body["docker_daemon_id"]):
        fail("Docker daemon/AppArmor namespace/boot binding drifted")
    return {
        "docker_daemon_id": daemon_id, "docker_daemon_pid": before["pid"],
        "docker_daemon_start_time_ticks": before["start_time_ticks"],
        "docker_daemon_comm": "dockerd",
        "docker_daemon_process_inode": before["process_inode"],
        "host_boot_id": boot_before,
        "host_namespace": {"name": "root", "level": 0, "stacked": False},
        "daemon_namespace": {
            "name": body["daemon_namespace_name"],
            "level": body["daemon_namespace_level"],
            "stacked": body["daemon_namespace_stacked"]},
        "same_apparmor_namespace_attested": True,
        "reviewed_provenance": provenance.report_record(),
    }


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
    fields = PROVENANCE_FILE_FIELDS
    if (
            total != before.st_size or
            any(getattr(before, field) != getattr(after, field) or
                getattr(before, field) != getattr(named, field)
                for field in fields)):
        fail("buildx binary changed while hashing")
    return "sha256:" + digest.hexdigest()


def inspect_single_image(reference: str) -> dict[str, object]:
    try:
        values = json.loads(
            command(docker_cli("image", "inspect", reference), timeout=30).stdout,
            object_pairs_hook=reject_duplicate_json_keys)
    except json.JSONDecodeError as error:
        raise GateError("Docker image inspect JSON invalid") from error
    if (
            not isinstance(values, list) or len(values) != 1 or
            not isinstance(values[0], dict)):
        fail("Docker image inspect cardinality mismatch")
    return values[0]


def validate_reviewed_base_image(
        record: dict[str, object], reference: str,
        provenance: RootProvenanceDocument) -> dict[str, object]:
    reference = require_pinned_image(reference)
    config = record.get("Config")
    repo_digests = record.get("RepoDigests")
    if (
            not isinstance(config, dict) or not isinstance(repo_digests, list) or
            reference not in repo_digests or len(repo_digests) != len(set(repo_digests)) or
            any(not isinstance(item, str) or PINNED_IMAGE.fullmatch(item) is None
                for item in repo_digests) or
            CANONICAL_IMAGE_ID.fullmatch(str(record.get("Id", ""))) is None or
            record.get("Os") != "linux" or record.get("Architecture") != "amd64"):
        fail("reviewed base image inspect contract mismatch")
    labels = config.get("Labels")
    if (
            "OnBuild" not in config or config.get("OnBuild") not in (None, []) or
            config.get("Volumes") not in (None, {}) or
            labels != REVIEWED_BASE_LABELS):
        fail("reviewed base inherited config is unsafe")
    labels_sha256 = canonical_object_sha256(labels)
    if (
            provenance.body["image_id"] != record["Id"] or
            provenance.body["repo_digest"] != reference or
            provenance.body["labels_sha256"] != labels_sha256):
        fail("reviewed base provenance does not bind inspected image")
    return {
        "reference": reference, "id": record["Id"],
        "repo_digests": sorted(repo_digests), "os": "linux",
        "architecture": record["Architecture"], "declared_volumes": 0,
        "onbuild_instructions": 0, "labels_sha256": labels_sha256,
        "production_approved": True,
        "reviewed_provenance": provenance.report_record(),
    }


def validate_reviewed_buildkit_image(
        record: dict[str, object], reference: str,
        provenance: RootProvenanceDocument) -> dict[str, object]:
    reference = require_pinned_image(reference)
    config = record.get("Config")
    repo_digests = record.get("RepoDigests")
    if (
            not isinstance(config, dict) or not isinstance(repo_digests, list) or
            reference not in repo_digests or len(repo_digests) != len(set(repo_digests)) or
            any(not isinstance(item, str) or PINNED_IMAGE.fullmatch(item) is None
                for item in repo_digests) or
            CANONICAL_IMAGE_ID.fullmatch(str(record.get("Id", ""))) is None or
            record.get("Os") != "linux" or record.get("Architecture") != "amd64"):
        fail("reviewed BuildKit image inspect contract mismatch")
    if (
            "OnBuild" not in config or config.get("OnBuild") not in (None, []) or
            config.get("Volumes") not in (None, {}) or
            config.get("ExposedPorts") not in (None, {}) or
            config.get("Entrypoint") not in (
                ["buildkitd"], ["/usr/bin/buildkitd"],
                ["/usr/local/bin/buildkitd"])):
        fail("reviewed BuildKit image config is unsafe")
    labels = config.get("Labels") or {}
    if (
            not isinstance(labels, dict) or
            {"io.hepta.purpose", BUILDER_ROLE_LABEL, BUILDER_RUN_LABEL,
             BUILDER_IMAGE_LABEL, BUILDER_NAME_LABEL}.intersection(labels)):
        fail("reviewed BuildKit labels collide with gate ownership")
    config_sha256 = canonical_object_sha256(config)
    if (
            provenance.body["image_id"] != record["Id"] or
            provenance.body["repo_digest"] != reference or
            provenance.body["config_sha256"] != config_sha256):
        fail("reviewed builder provenance does not bind BuildKit image")
    image_id = str(record["Id"])
    return {
        "reference": reference, "id": image_id,
        "bare_id": image_id.removeprefix("sha256:"),
        "repo_digests": sorted(repo_digests), "os": "linux",
        "architecture": record["Architecture"],
        "config_sha256": config_sha256, "config_labels": dict(labels),
        "entrypoint": list(config["Entrypoint"]),
        "production_approved": True,
        "reviewed_provenance": provenance.report_record(),
    }


def inspect_buildx_toolchain(
        expected_binary_sha256: str,
        provenance: RootProvenanceDocument) -> dict[str, object]:
    try:
        plugins = json.loads(command(docker_cli(
            "info", "--format", "{{json .ClientInfo.Plugins}}"),
            timeout=30).stdout, object_pairs_hook=reject_duplicate_json_keys)
        server = json.loads(command(docker_cli(
            "version", "--format", "{{json .Server}}"), timeout=30).stdout,
            object_pairs_hook=reject_duplicate_json_keys)
    except json.JSONDecodeError as error:
        raise GateError("Docker/buildx toolchain JSON invalid") from error
    matches = [
        item for item in plugins
        if isinstance(item, dict) and item.get("Name") == "buildx"]
    if len(matches) != 1 or not isinstance(server, dict):
        fail("exactly one Docker buildx plugin is required")
    plugin = matches[0]
    path_value = plugin.get("Path")
    version = plugin.get("Version")
    if (
            not isinstance(path_value, str) or not path_value.startswith("/") or
            not isinstance(version, str) or
            SEMANTIC_VERSION.fullmatch(version) is None):
        fail("buildx plugin identity/version malformed")
    observed = hash_root_owned_executable(Path(path_value))
    if observed != expected_binary_sha256:
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
            body["buildx_binary_sha256"] != observed or
            body["docker_server_version"] != server.get("Version") or
            body["docker_server_api_version"] != server.get("ApiVersion") or
            body["docker_server_git_commit"] != server.get("GitCommit")):
        fail("reviewed builder provenance does not bind active toolchain")
    return {
        "buildx_path_sha256": "sha256:" + hashlib.sha256(
            path_value.encode("utf-8")).hexdigest(),
        "buildx_version": version, "buildx_binary_sha256": observed,
        "docker_server_version": server.get("Version"),
        "docker_server_api_version": server.get("ApiVersion"),
        "docker_server_git_commit": server.get("GitCommit"), "reviewed": True,
    }


def isolated_builder_names(run_id: str) -> dict[str, str]:
    if re.fullmatch(r"[0-9a-f]{32}", run_id) is None:
        fail("isolated builder run ID invalid")
    builder = "hepta-paper-domain-isolated-" + run_id
    node = builder + "0"
    container = "buildx_buildkit_" + node
    return {
        "builder": builder, "node": node, "container": container,
        "volume": container + "_state"}


def builder_metadata_path(builder_name: str) -> Path:
    if (
            _DOCKER_CONFIG is None or
            re.fullmatch(
                r"hepta-paper-domain-isolated-[0-9a-f]{32}", builder_name)
            is None):
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
        "io.hepta.purpose": PURPOSE, BUILDER_ROLE_LABEL: role,
        BUILDER_RUN_LABEL: run_id, BUILDER_IMAGE_LABEL: image_id,
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
    volume = ["volume", "create", "--driver=local"]
    for key, value in builder_labels(
            run_id, names["builder"], image_id,
            BUILDER_STATE_ROLE).items():
        volume.extend(("--label", f"{key}={value}"))
    volume.append(names["volume"])
    container = [
        "container", "create", "--pull=never", "--network=none",
        "--privileged", "--init", "--restart=no", "--name",
        names["container"], "--mount",
        "type=volume,source=" + names["volume"] +
        ",target=" + BUILDKIT_STATE_DIRECTORY,
    ]
    for key, value in builder_labels(
            run_id, names["builder"], image_id,
            BUILDER_DAEMON_ROLE).items():
        container.extend(("--label", f"{key}={value}"))
    container.append(bare_id)
    buildx = [
        "buildx", "create", "--name", names["builder"], "--node",
        names["node"], "--driver", "docker-container", "--driver-opt",
        "image=" + bare_id +
        ",network=none,restart-policy=no,default-load=false,"
        "provenance-add-gha=false", "--platform", "linux/amd64",
    ]
    return {"volume": volume, "container": container, "buildx": buildx}


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
    if completed.returncode != 1 or "no such" not in completed.stdout.lower():
        fail(label + " absence could not be established")


def validate_builder_volume_record(
        record: object, names: dict[str, str], run_id: str,
        image_id: str) -> dict[str, object]:
    expected = builder_labels(
        run_id, names["builder"], image_id, BUILDER_STATE_ROLE)
    if (
            not isinstance(record, dict) or record.get("Name") != names["volume"] or
            record.get("Driver") != "local" or record.get("Scope") != "local" or
            record.get("Labels") != expected or record.get("Options") not in (None, {}) or
            not isinstance(record.get("Mountpoint"), str) or
            not str(record["Mountpoint"]).startswith("/")):
        fail("isolated builder volume inspect mismatch")
    return {
        "name": names["volume"], "driver": "local", "scope": "local",
        "labels": expected, "mountpoint_sha256": "sha256:" + hashlib.sha256(
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
            not isinstance(image_labels, dict) or len(mounts) != 1 or
            record.get("Name") != "/" + names["container"] or
            record.get("Image") != image_id or config.get("Image") != bare_id or
            config.get("Labels") != {**image_labels, **ownership} or
            (running is not None and state.get("Running") is not running)):
        fail("isolated builder container identity mismatch")
    restart = host.get("RestartPolicy") or {}
    if (
            host.get("NetworkMode") != "none" or host.get("Privileged") is not True or
            host.get("Init") is not True or host.get("AutoRemove") is not False or
            host.get("ReadonlyRootfs") is not False or restart.get("Name") != "no" or
            host.get("Binds") not in (None, []) or host.get("Tmpfs") not in (None, {}) or
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
            mount.get("Driver") != "local" or mount.get("RW") is not True):
        fail("isolated builder state mount mismatch")
    return {
        "container_id": record.get("Id"), "name": names["container"],
        "network_mode": "none", "privileged": True, "bind_mounts": 0,
        "devices": 0, "published_ports": 0,
        "running": state.get("Running"), "labels": ownership,
    }


def validate_buildx_runtime(
        output: str, names: dict[str, str],
        provenance: RootProvenanceDocument) -> dict[str, object]:
    records: list[object] = []
    for line in output.splitlines():
        if line:
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
            not isinstance(nodes[0], dict) or nodes[0].get("Name") != names["node"] or
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
    for arguments, label in (
            (docker_cli("container", "inspect", names["container"]),
             "builder container"),
            (docker_cli("volume", "inspect", names["volume"]),
             "builder volume")):
        require_inspect_absent(arguments, label)
    require_builder_metadata_absent(names["builder"])
    arguments = create_isolated_builder_arguments(names, run_id, buildkit)
    output = command(docker_cli(*arguments["volume"]), timeout=30).stdout
    if output.strip() != names["volume"]:
        fail("isolated builder volume create identity mismatch")
    _completed, volume_raw = inspect_json_list(
        docker_cli("volume", "inspect", names["volume"]), "builder volume")
    if volume_raw is None:
        fail("isolated builder volume missing")
    volume = validate_builder_volume_record(
        volume_raw, names, run_id, str(buildkit["id"]))
    created = command(docker_cli(*arguments["container"]), check=False, timeout=90)
    if created.returncode != 0:
        fail("exact preloaded BuildKit image unavailable to pull-never create")
    container_id = created.stdout.strip()
    if re.fullmatch(r"[0-9a-f]{64}", container_id) is None:
        fail("isolated builder container ID invalid")
    _completed, stopped_raw = inspect_json_list(
        docker_cli("container", "inspect", container_id), "builder container")
    if stopped_raw is None:
        fail("isolated builder container missing")
    stopped = validate_builder_container_record(
        stopped_raw, names, run_id, buildkit, running=False)
    created_builder = command(
        docker_cli(*arguments["buildx"]), timeout=60).stdout.strip()
    if created_builder != names["builder"] or not builder_metadata_exists(
            names["builder"]):
        fail("buildx create did not bind private builder metadata")
    command(docker_cli("container", "start", container_id), timeout=90)
    _completed, running_raw = inspect_json_list(
        docker_cli("container", "inspect", container_id), "builder container")
    if running_raw is None:
        fail("running isolated builder missing")
    running = validate_builder_container_record(
        running_raw, names, run_id, buildkit, running=True)
    runtime = validate_buildx_runtime(command(docker_cli(
        "buildx", "ls", "--format", "{{json .}}"), timeout=60).stdout,
        names, provenance)
    return {
        "names": names, "container_id": container_id, "volume": volume,
        "container_before_start": stopped, "container_running": running,
        "runtime": runtime,
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
        if record is None or (
                expected_id is not None and record.get("Id") != expected_id):
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
        command(docker_cli("buildx", "rm", "--force", names["builder"]),
                check=False, timeout=120)
        if metadata_present else
        subprocess.CompletedProcess([], 1, "builder metadata absent", None))
    fallback = False
    if removed.returncode != 0:
        if inspected.returncode == 0:
            command(docker_cli(
                "container", "rm", "--force", str(expected_id)), timeout=60)
        if volume_inspected.returncode == 0:
            command(docker_cli("volume", "rm", names["volume"]), timeout=60)
        fallback = True
        if not allow_partial:
            fail("isolated buildx builder removal failed")
    require_inspect_absent(
        docker_cli("container", "inspect", names["container"]),
        "isolated builder container")
    require_inspect_absent(
        docker_cli("volume", "inspect", names["volume"]),
        "isolated builder volume")
    require_builder_metadata_absent(names["builder"])
    if inspect_single_image(str(buildkit["id"])).get("Id") != buildkit["id"]:
        fail("reviewed BuildKit image disappeared during cleanup")
    return {
        "buildx_rm": "fallback" if fallback else "completed",
        "container_absent": True, "state_volume_absent": True,
        "cache_cleanup": "state-volume-removed",
        "private_builder_metadata_absent": True,
        "buildkit_image_retained": True,
    }


def network_manifest(policy_raw: bytes, domain: str, uid: int) -> bytes:
    return canonical_json({
        "schema": "hepta.agent-trust-domain-paper-identities.v1",
        "version": 1,
        "source_policy_sha256":
            "sha256:" + hashlib.sha256(policy_raw).hexdigest(),
        "paper_authorized": True,
        "live_authorized": False,
        "identities": [{
            "domain_id": domain,
            "identity": f"hepta-ib-exec-{domain}",
            "uid": uid,
            "gid": uid,
            "role": "ib-paper-execution-authority",
        }],
    })


def authority_manifest(network_raw: bytes, domain: str, uid: int) -> bytes:
    control = f"/run/hepta/ib-paper-control-{domain}"
    return canonical_json({
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
    })


def stage_context(
        root: Path, context: Path
) -> tuple[dict[str, dict[str, object]], dict[str, str]]:
    records: dict[str, dict[str, object]] = {}
    for relative, (destination, mode) in SOURCE_FILES.items():
        raw, record = read_stable(root / relative)
        write_exact(context / destination, raw, mode)
        records[relative] = record

    policy = (context / "provision-root/"
              "hepta-broker-network-policy-v1.json").read_bytes()
    generated: dict[str, bytes] = {}
    for domain, uid, suffix in (
            ("codex-a", 2121, "a"),
            ("openclaw-b", 2122, "b")):
        network = network_manifest(policy, domain, uid)
        generated[f"network-{suffix}.json"] = network
        generated[f"authority-{suffix}.json"] = authority_manifest(
            network, domain, uid)
        generated[f"{domain}.ib-paper.env"] = (
            f"HEPTA_IB_EXECUTION_GATEWAY_AGENT_ID={domain}\n"
            "HEPTA_PAPER_STUB_MODE=hold\n"
        ).encode("ascii")
    generated["inert-fence"] = (
        b"INERT_ROOTFUL_SYSTEMD_FIXTURE_NO_TRADING_AUTHORITY\n")
    generated["inert-authorization"] = (
        b"INERT_ROOTFUL_SYSTEMD_FIXTURE_NO_BROKER_CREDENTIAL\n")
    for name, raw in generated.items():
        write_exact(
            context / "provision-root" / name,
            raw,
            0o600 if name.endswith(".json") else 0o400,
        )

    systemd_root = context / "install-root/usr/lib/systemd/system"
    service_dropin = (
        "[Service]\n"
        "RestartSec=100ms\n"
    ).encode("ascii")
    write_exact(
        systemd_root /
        "hepta-execution-ib-paper@.service.d/90-rootful-gate.conf",
        service_dropin,
        0o644,
    )
    b_preflight_dropin = (
        "[Service]\n"
        "# Give codex-a deterministic first acquisition while both cold-start\n"
        "# transactions are live; the pure authority test covers the narrower\n"
        "# pre-tombstone lock-owner interleaving directly.\n"
        "ExecStartPre=/usr/bin/sleep 0.1\n"
        "ExecStart=\n"
        "ExecStart=/usr/libexec/hepta-ib-paper-domain-authority "
        "--network-identities=/etc/heptatrader/test-openclaw-b-network.json "
        "--authorizations=/etc/heptatrader/test-openclaw-b-authority.json "
        "--guard --domain openclaw-b\n"
    ).encode("ascii")
    write_exact(
        systemd_root /
        "hepta-ib-paper-domain-preflight@openclaw-b.service.d/"
        "90-rootful-gate-manifests.conf",
        b_preflight_dropin,
        0o644,
    )
    generated["service-start-limit-dropin.conf"] = service_dropin
    generated["domain-b-preflight-dropin.conf"] = b_preflight_dropin
    generated_hashes = {
        name: hashlib.sha256(raw).hexdigest()
        for name, raw in sorted(generated.items())
    }
    return records, generated_hashes


def build_arguments(
        base: str, tag: str, context: Path, iidfile: Path,
        run_id: str, *, builder_name: Optional[str] = None) -> list[str]:
    arguments = (
        ["buildx", "build", "--builder", builder_name, "--load",
         "--platform", "linux/amd64", "--provenance=false"]
        if builder_name is not None else ["build"])
    arguments.extend([
        "--pull=false", "--network=none", "--no-cache",
        "--label", PURPOSE_LABEL,
        "--label", f"{RUN_LABEL_KEY}={run_id}",
        "--build-arg", f"BASE_IMAGE={base}",
        "--file",
        str(context / "tests/paper_domain_rootful_systemd/Dockerfile"),
        "--iidfile", str(iidfile),
        "--tag", tag,
        str(context),
    ])
    return arguments


def create_arguments(
        image_id: str, name: str, run_id: str) -> list[str]:
    arguments = [
        "create", "--name", name, "--label", PURPOSE_LABEL,
        "--label", f"{RUN_LABEL_KEY}={run_id}",
        "--hostname", "hepta-paper-domain-systemd",
        "--network", "none", "--cgroupns", "private", "--ipc", "private",
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
        "--pids-limit", "512",
        "--memory", "1g",
        "--cpus", "2",
        "--stop-signal", "SIGRTMIN+3",
        "--stop-timeout", "20",
        "--env", "HEPTA_PAPER_DOMAIN_SYSTEMD_DISPOSABLE=1",
        "--env", f"HEPTA_PAPER_DOMAIN_SYSTEMD_RUN_ID={run_id}",
        image_id,
    ))
    return arguments


def validate_inner(
        output: str, *, expected_run_id: str) -> dict[str, object]:
    lines = [line for line in output.splitlines() if line]
    if len(lines) != 1 or not lines[0].startswith(INNER_MARKER):
        fail("inner gate output framing mismatch")
    try:
        value = json.loads(lines[0][len(INNER_MARKER):])
    except json.JSONDecodeError as error:
        raise GateError("inner result is invalid JSON") from error
    if (
            not isinstance(value, dict) or set(value) !=
            {"schema", "passed", "run_id", "checks", "versions", "boot",
             "boundary"} or
            value.get("schema") != INNER_SCHEMA or
            value.get("passed") is not True or
            value.get("run_id") != expected_run_id):
        fail("inner result contract mismatch")
    checks = value.get("checks")
    if (
            not isinstance(checks, dict) or set(checks) != EXPECTED_CHECKS or
            any(result is not True for result in checks.values())):
        fail("inner check set mismatch")
    versions = value.get("versions")
    if (
            not isinstance(versions, dict) or
            set(versions) != {
                "systemd", "nft", "kernel", "architecture", "cgroup",
                "immutable_file_count",
                "immutable_file_inventory_sha256",
                "package_count", "package_inventory_sha256"} or
            any(not isinstance(item, str) or not item for item in versions.values())):
        fail("inner version evidence mismatch")
    for name in ("immutable_file_count", "package_count"):
        if (
                not versions[name].isdecimal() or
                int(versions[name], 10) <= 0):
            fail(
                "inner inventory count evidence mismatch: " +
                json.dumps(
                    {"field": name, "value": versions[name]},
                    sort_keys=True))
    for name in (
            "immutable_file_inventory_sha256",
            "package_inventory_sha256"):
        if re.fullmatch(r"[0-9a-f]{64}", versions[name]) is None:
            fail("inner inventory digest evidence mismatch")
    boot = value.get("boot")
    if (
            not isinstance(boot, dict) or
            set(boot) != {"boot_id", "pid1_cgroup"} or
            CANONICAL_BOOT_ID.fullmatch(str(boot.get("boot_id", ""))) is None or
            boot.get("pid1_cgroup") != "0::/"):
        fail("inner boot/cgroup evidence mismatch")
    if value.get("boundary") != EXPECTED_BOUNDARY:
        fail("inner boundary mismatch")
    return value


def safe_report_path(argument: Path) -> Path:
    path = argument
    value = os.fspath(path)
    if (
            not path.is_absolute() or value != os.path.normpath(value) or
            value.startswith("//") or
            path.name in ("", ".", "..") or
            any(part in ("", ".", "..") for part in path.parts[1:]) or
            re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}\.json",
                         path.name) is None):
        fail("report path must be absolute lexical-canonical JSON path")
    parent = _open_anchored_directory(path.parent)
    try:
        metadata = os.fstat(parent)
        if (
                not stat.S_ISDIR(metadata.st_mode) or
                metadata.st_uid != os.geteuid() or
                stat.S_IMODE(metadata.st_mode) & 0o022):
            fail("report path parent is untrusted")
        try:
            os.stat(path.name, dir_fd=parent, follow_symlinks=False)
        except FileNotFoundError:
            pass
        else:
            fail("report already exists; publication is no-replace")
    finally:
        os.close(parent)
    return path


def _open_anchored_directory(path: Path) -> int:
    if not path.is_absolute() or os.fspath(path) != os.path.normpath(os.fspath(path)):
        fail("report parent is not canonical")
    descriptor = os.open(
        "/", os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC |
        getattr(os, "O_NOFOLLOW", 0))
    try:
        for component in path.parts[1:]:
            before = os.stat(component, dir_fd=descriptor, follow_symlinks=False)
            child = os.open(
                component,
                os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC |
                getattr(os, "O_NOFOLLOW", 0), dir_fd=descriptor)
            after = os.fstat(child)
            fields = PROVENANCE_DIRECTORY_FIELDS
            if (
                    not stat.S_ISDIR(after.st_mode) or
                    any(getattr(before, field) != getattr(after, field)
                        for field in fields)):
                os.close(child)
                fail("report parent ancestor rebound")
            os.close(descriptor)
            descriptor = child
        return descriptor
    except BaseException:
        os.close(descriptor)
        raise


def _rename_noreplace(parent: int, source: str, destination: str) -> None:
    function = getattr(_LIBC, "renameat2", None)
    if function is None:
        fail("renameat2 RENAME_NOREPLACE is unavailable")
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
            fail("report already exists; publication is no-replace")
        fail("report RENAME_NOREPLACE failed")


def atomic_report(path: Path, report: dict[str, object]) -> None:
    validate_report(report)
    payload = canonical_json(report)
    if len(payload) > MAX_REPORT:
        fail("report exceeds bound")
    directory = _open_anchored_directory(path.parent)
    temporary = "." + path.name + ".tmp-" + secrets.token_hex(16)
    descriptor: Optional[int] = None
    renamed = False
    try:
        parent_before = os.fstat(directory)
        if (
                parent_before.st_uid != os.geteuid() or
                stat.S_IMODE(parent_before.st_mode) & 0o022):
            fail("report parent is untrusted")
        descriptor = os.open(
            temporary,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_CLOEXEC |
            getattr(os, "O_NOFOLLOW", 0), 0o600, dir_fd=directory)
        os.fchmod(descriptor, 0o600)
        view = memoryview(payload)
        while view:
            written = os.write(descriptor, view)
            if written <= 0:
                fail("short report write")
            view = view[written:]
        os.fsync(descriptor)
        prepared = os.fstat(descriptor)
        if (
                not stat.S_ISREG(prepared.st_mode) or prepared.st_nlink != 1 or
                prepared.st_uid != os.geteuid() or
                stat.S_IMODE(prepared.st_mode) != 0o600 or
                prepared.st_size != len(payload)):
            fail("prepared report metadata mismatch")
        os.fsync(directory)
        _rename_noreplace(directory, temporary, path.name)
        renamed = True
        os.fsync(directory)
        parent_after = os.fstat(directory)
        parent_binding_fields = (
            "st_dev", "st_ino", "st_mode", "st_nlink", "st_uid", "st_gid")
        if metadata_identity(parent_before, parent_binding_fields) != \
                metadata_identity(parent_after, parent_binding_fields):
            fail("report parent drifted during publication")
    finally:
        if descriptor is not None:
            os.close(descriptor)
        if not renamed:
            try:
                os.unlink(temporary, dir_fd=directory)
                os.fsync(directory)
            except OSError:
                pass
        os.close(directory)
    committed, metadata, mode = read_anchored_root_provenance(
        path, kind="published report") if os.geteuid() == 0 else \
        read_anchored_owned_report(path)
    if committed != payload or mode != "0600":
        fail("published report secure reopen mismatch")
    try:
        restored = json.loads(
            committed, object_pairs_hook=reject_duplicate_json_keys)
    except json.JSONDecodeError as error:
        raise GateError("published report JSON invalid") from error
    validate_report(restored)
    if metadata[6] != len(payload):
        fail("published report reopened size mismatch")


def read_anchored_owned_report(
        path: Path) -> tuple[bytes, tuple[int, ...], str]:
    parent = _open_anchored_directory(path.parent)
    descriptor: Optional[int] = None
    try:
        descriptor = os.open(
            path.name, os.O_RDONLY | os.O_CLOEXEC |
            getattr(os, "O_NOFOLLOW", 0), dir_fd=parent)
        before = os.fstat(descriptor)
        if (
                not stat.S_ISREG(before.st_mode) or before.st_nlink != 1 or
                before.st_uid != os.geteuid() or
                stat.S_IMODE(before.st_mode) != 0o600 or
                before.st_size < 2 or before.st_size > MAX_REPORT):
            fail("published report metadata mismatch")
        raw = read_provenance_descriptor(
            descriptor, expected_size=before.st_size, kind="published report")
        after = os.fstat(descriptor)
        named = os.stat(path.name, dir_fd=parent, follow_symlinks=False)
        identity = metadata_identity(before, PROVENANCE_FILE_FIELDS)
        if (
                identity != metadata_identity(after, PROVENANCE_FILE_FIELDS) or
                identity != metadata_identity(named, PROVENANCE_FILE_FIELDS)):
            fail("published report changed across reopen")
        return raw, identity, "0600"
    finally:
        if descriptor is not None:
            os.close(descriptor)
        os.close(parent)


def require_source_lineage(
        source_tree_clean: bool, allow_dirty_rehearsal: bool) -> None:
    if not source_tree_clean and not allow_dirty_rehearsal:
        fail(
            "source lineage is not clean and fully versioned; "
            "dirty execution requires --allow-dirty-rehearsal")


def require_external_input_pins(
        input_manifest_sha256: str, runner_sha256: str,
        request: CertificationRequest) -> None:
    if (
            require_sha256(input_manifest_sha256, "observed input manifest") !=
            request.expected_input_manifest_sha256 or
            require_sha256(runner_sha256, "observed runner") !=
            request.expected_runner_sha256):
        fail("externally pinned input manifest/runner digest mismatch")


def execute(
        base: str, expected_source_commit: str, *,
        allow_dirty_rehearsal: bool = False,
        certification_request: Optional[CertificationRequest] = None,
        ) -> dict[str, object]:
    started_mono = time.monotonic()
    started_at_ms = time.time_ns() // 1_000_000
    root = repository_root()
    expected_source_commit = require_expected_commit(expected_source_commit)
    source_commit = command(
        ["git", "-C", str(root), "rev-parse", "HEAD"], timeout=15
    ).stdout.strip()
    if source_commit != expected_source_commit:
        fail("source commit does not match externally pinned commit")
    tracked_tree_clean = command(
        ["git", "-C", str(root), "status", "--porcelain=v1",
         "--untracked-files=all"], check=False, timeout=30).stdout == ""
    all_inputs_versioned = all(command(
        ["git", "-C", str(root), "ls-files", "--error-unmatch", relative],
        check=False, timeout=10).returncode == 0 for relative in SOURCE_FILES)
    source_tree_clean = tracked_tree_clean and all_inputs_versioned
    require_source_lineage(source_tree_clean, allow_dirty_rehearsal)
    if certification_request is not None and (
            os.geteuid() != 0 or not source_tree_clean):
        fail("certification requires root and a clean fully versioned source")
    base = require_pinned_image(base)
    if certification_request is not None and (
            certification_request.reviewed_base_path ==
            certification_request.reviewed_builder_path):
        fail("certification provenance paths are not independent")
    environment_review_session = (
        verify_environment_review_for_request(
            certification_request, base_image=base,
            source_commit=source_commit)
        if certification_request is not None else None)

    run_id = uuid.uuid4().hex
    tag = f"hepta/paper-domain-rootful-systemd:{run_id}"
    name = f"hepta-paper-domain-systemd-{run_id}"
    image_id: Optional[str] = None
    container_id: Optional[str] = None
    input_before: dict[str, dict[str, object]] = {}
    generated_hashes: dict[str, str] = {}
    inner: Optional[dict[str, object]] = None
    server: dict[str, object] = {}
    docker_info: dict[str, object] = {}
    base_record: dict[str, object] = {}
    base_id = ""
    provenance_before: dict[str, RootProvenanceDocument] = {}
    provenance_after: dict[str, RootProvenanceDocument] = {}
    reviewed_base: Optional[dict[str, object]] = None
    reviewed_buildkit: Optional[dict[str, object]] = None
    buildx_toolchain: Optional[dict[str, object]] = None
    isolated_builder: Optional[dict[str, object]] = None
    builder_cleanup: Optional[dict[str, object]] = None
    builder_attempted = False
    builder_names = isolated_builder_names(run_id)
    apparmor_before: Optional[dict[str, object]] = None
    apparmor_after: Optional[dict[str, object]] = None
    namespace_before: Optional[dict[str, object]] = None
    namespace_after: Optional[dict[str, object]] = None
    socket_before: Optional[dict[str, object]] = None
    socket_after: Optional[dict[str, object]] = None
    runtime_container_absent = False
    runtime_image_tag_absent = False
    runtime_image_id_absent = False

    if certification_request is not None:
        provenance_before = load_certification_provenance(
            certification_request, now_ms=started_at_ms)
    initialize_docker_config()
    try:
        if certification_request is not None:
            socket_before = validate_local_docker_socket()
        try:
            server = json.loads(command(docker_cli(
                "version", "--format", "{{json .Server}}"), timeout=30).stdout,
                object_pairs_hook=reject_duplicate_json_keys)
            docker_info = json.loads(command(docker_cli(
                "info", "--format", "{{json .}}"), timeout=30).stdout,
                object_pairs_hook=reject_duplicate_json_keys)
        except json.JSONDecodeError as error:
            raise GateError("Docker runtime evidence JSON invalid") from error
        if (
                not isinstance(server, dict) or not server.get("Version") or
                not isinstance(docker_info, dict) or
                docker_info.get("CgroupVersion") != "2" or
                not isinstance(docker_info.get("Architecture"), str) or
                not isinstance(docker_info.get("OperatingSystem"), str) or
                not isinstance(docker_info.get("DefaultRuntime"), str) or
                not isinstance(docker_info.get("SecurityOptions"), list) or
                not any(isinstance(item, str) and "apparmor" in item.lower()
                        for item in docker_info.get("SecurityOptions", []))):
            fail("Docker runtime/AppArmor evidence invalid")
        base_record = inspect_single_image(base)
        base_id = str(base_record.get("Id", ""))
        repo_digests = base_record.get("RepoDigests")
        if (
                CANONICAL_IMAGE_ID.fullmatch(base_id) is None or
                base_record.get("Os") != "linux" or
                not architecture_matches(
                    str(base_record.get("Architecture", "")),
                    str(docker_info.get("Architecture", ""))) or
                not isinstance(repo_digests, list) or base not in repo_digests):
            fail("preloaded base identity/platform/digest evidence invalid")

        if certification_request is not None:
            reviewed_base = validate_reviewed_base_image(
                base_record, base, provenance_before["base"])
            apparmor_before = validate_loaded_apparmor(
                provenance_before["apparmor"])
            namespace_before = validate_docker_namespace_binding(
                provenance_before["docker_namespace"], apparmor_before)
            buildx_toolchain = inspect_buildx_toolchain(
                certification_request.buildx_binary_sha256,
                provenance_before["builder"])
            reviewed_buildkit = validate_reviewed_buildkit_image(
                inspect_single_image(certification_request.buildkit_image),
                certification_request.buildkit_image,
                provenance_before["builder"])

        try:
            with tempfile.TemporaryDirectory(
                    prefix="hepta-paper-domain-systemd-context-") as temporary:
                context = Path(temporary)
                input_before, generated_hashes = stage_context(root, context)
                input_manifest_sha256 = canonical_sha256(input_before)
                runner_sha256 = "sha256:" + str(input_before[
                    "scripts/run_hepta_paper_domain_rootful_systemd_gate.py"
                ]["sha256"])
                if certification_request is not None:
                    require_external_input_pins(
                        input_manifest_sha256, runner_sha256,
                        certification_request)
                iidfile = context / ".image-id"
                builder_name: Optional[str] = None
                if certification_request is not None:
                    if reviewed_buildkit is None:
                        fail("reviewed BuildKit record missing")
                    builder_attempted = True
                    isolated_builder = create_isolated_builder(
                        run_id, reviewed_buildkit,
                        provenance_before["builder"])
                    builder_name = str(isolated_builder["names"]["builder"])
                command(docker_cli(*build_arguments(
                    base, tag, context, iidfile, run_id,
                    builder_name=builder_name)), timeout=900)
                image_id = iidfile.read_text(
                    encoding="ascii", errors="strict").strip()
                if CANONICAL_IMAGE_ID.fullmatch(image_id) is None:
                    fail("built image ID invalid")
                image_inspect = inspect_single_image(tag)
                if not object_owned(
                        image_inspect, run_id, expected_image_id=image_id):
                    fail("built image ownership labels mismatch")
                created = command(docker_cli(*create_arguments(
                    image_id, name, run_id)), timeout=60).stdout.strip()
                if re.fullmatch(r"[0-9a-f]{64}", created) is None:
                    fail("container ID invalid")
                container_id = created
                _completed, inspected = inspect_json_list(
                    docker_cli("container", "inspect", container_id),
                    "runtime container")
                if inspected is None:
                    fail("runtime container inspect missing")
                validate_container_inspect_record(
                    inspected, container_id=container_id, image_id=image_id,
                    name=name, run_id=run_id)
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
                    "/usr/local/libexec/hepta_paper_domain_rootful_inner_gate.py"),
                    check=False, timeout=240)
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
                    fail("outer/inner boot/cgroup binding mismatch")
                command(docker_cli(
                    "stop", "--time", "20", container_id), timeout=45)
        finally:
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
                        "rm", "--force", container_id), check=False,
                        timeout=30).returncode != 0:
                    cleanup_errors.append("container-remove")
                else:
                    try:
                        require_inspect_absent(docker_cli(
                            "container", "inspect", container_id),
                            "runtime container")
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
                            "runtime image tag")
                        require_inspect_absent(
                            docker_cli("image", "inspect", image_id),
                            "runtime image ID")
                        runtime_image_tag_absent = True
                        runtime_image_id_absent = True
                    except GateError:
                        cleanup_errors.append("image-residue")
            if builder_attempted:
                if reviewed_buildkit is None:
                    fail("BuildKit cleanup record missing")
                builder_cleanup = cleanup_isolated_builder(
                    isolated_builder or {
                        "names": builder_names, "container_id": None},
                    run_id, reviewed_buildkit,
                    allow_partial=isolated_builder is None)
            if certification_request is not None:
                socket_after = validate_local_docker_socket()
                apparmor_after = validate_loaded_apparmor(
                    provenance_before["apparmor"])
                namespace_after = validate_docker_namespace_binding(
                    provenance_before["docker_namespace"], apparmor_after)
                completed_probe_ms = time.time_ns() // 1_000_000
                provenance_after = load_certification_provenance(
                    certification_request, now_ms=completed_probe_ms)
                if (
                        provenance_after != provenance_before or
                        socket_after != socket_before or
                        apparmor_after != apparmor_before or
                        namespace_after != namespace_before):
                    fail("provenance/AppArmor/Docker identity drifted across gate")
            if cleanup_errors:
                fail("disposable cleanup failed: " + ",".join(cleanup_errors))
    finally:
        cleanup_docker_config()

    if inner is None or not (
            runtime_container_absent and runtime_image_tag_absent and
            runtime_image_id_absent):
        fail("inner result or runtime cleanup evidence is incomplete")
    input_after = {
        relative: read_stable(root / relative)[1] for relative in SOURCE_FILES}
    if input_after != input_before:
        fail("gate inputs changed during execution")
    input_manifest_sha256 = canonical_sha256(input_after)
    runner_sha256 = "sha256:" + str(input_after[
        "scripts/run_hepta_paper_domain_rootful_systemd_gate.py"]["sha256"])
    certifying = certification_request is not None
    if certifying and (
            reviewed_base is None or reviewed_buildkit is None or
            buildx_toolchain is None or isolated_builder is None or
            builder_cleanup is None or socket_before is None or
            socket_after is None or apparmor_before is None or
            apparmor_after is None or namespace_before is None or
            namespace_after is None or provenance_after != provenance_before):
        fail("certification evidence closure is incomplete")
    environment_review_record: Optional[dict[str, object]] = None
    if certifying:
        if environment_review_session is None:
            fail("signed environment review closure is missing")
        try:
            environment_review_session.reopen_at_gate_end()
            environment_review_record = environment_review_session.report_record()
        except ROOT_REVIEW.ReviewClosureError as error:
            raise GateError(str(error)) from error
    completed_at_ms = time.time_ns() // 1_000_000
    expiry_candidates = [completed_at_ms + REPORT_LIFETIME_MS]
    if certifying:
        expiry_candidates.extend(
            int(item.body["expires_at_ms"]) for item in provenance_after.values())
        expiry_candidates.append(int(environment_review_record["expires_at_ms"]))
    expires_at_ms = min(expiry_candidates)
    if expires_at_ms <= completed_at_ms:
        fail("certification evidence expired before receipt publication")
    decision = "GO" if certifying else "REHEARSAL_ONLY"
    report = {
        "schema": SCHEMA, "run_id": run_id, "decision": decision,
        "passed": certifying, "rehearsal_passed": True,
        "certification_ready": certifying,
        "certification_blockers": [] if certifying else list(CERTIFICATION_BLOCKERS),
        "scope": "broker-free-paper-domain-rootful-prerequisite-only",
        "started_at_ms": started_at_ms, "completed_at_ms": completed_at_ms,
        "expires_at_ms": expires_at_ms,
        "duration_ms": int((time.monotonic() - started_mono) * 1000),
        "paper_test_admission_candidate": False,
        "paper_authorized": False, "live_authorized": False,
        "mutation_authorized": False, "direct_broker_access": False,
        "order_submission_authorized": False,
        "lineage": {
            "source_commit": source_commit,
            "expected_source_commit": expected_source_commit,
            "source_tree_clean": source_tree_clean,
            "all_inputs_versioned": all_inputs_versioned,
            "inputs_stable": True, "final_lineage": source_tree_clean,
            "input_manifest_sha256": input_manifest_sha256,
            "expected_input_manifest_sha256": (
                certification_request.expected_input_manifest_sha256
                if certifying else None),
            "runner_sha256": runner_sha256,
            "expected_runner_sha256": (
                certification_request.expected_runner_sha256
                if certifying else None),
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
            "base_image_reference": base, "base_image_id": base_id,
            "base_image_os": base_record.get("Os", ""),
            "base_image_architecture": base_record.get("Architecture", ""),
            "systemd": inner["versions"]["systemd"],
            "nft": inner["versions"]["nft"],
            "container_kernel": inner["versions"]["kernel"],
            "container_architecture": inner["versions"]["architecture"],
            "container_cgroup": inner["versions"]["cgroup"],
            "container_boot_id": inner["boot"]["boot_id"],
            "container_pid1_cgroup": inner["boot"]["pid1_cgroup"],
            "immutable_file_count": inner["versions"]["immutable_file_count"],
            "immutable_file_inventory_sha256":
                inner["versions"]["immutable_file_inventory_sha256"],
            "package_count": inner["versions"]["package_count"],
            "package_inventory_sha256":
                inner["versions"]["package_inventory_sha256"],
        },
        "container": {
            "image_id": image_id, "network_mode": "none",
            "read_only_rootfs": True, "private_cgroup_namespace": True,
            "privileged": False, "bind_mounts": 0, "published_ports": 0,
            "devices": 0, "device_requests": 0, "links": 0,
            "tmpfs_allowlist": RUNTIME_TMPFS,
            "apparmor_profile": APPARMOR_PROFILE,
            "capabilities": list(RUNTIME_CAPABILITIES),
        },
        "disposable_cleanup": {
            "container_absent": True, "image_tag_absent": True,
            "image_id_absent": True,
        },
        "certification": {
            "requested": certifying, "eligible": certifying,
            "provenance": (
                {key: item.report_record()
                 for key, item in sorted(provenance_before.items())}
                if certifying else None),
            "provenance_reopened_equal": certifying,
            "reviewed_base": reviewed_base,
            "reviewed_buildkit": reviewed_buildkit,
            "buildx_toolchain": buildx_toolchain,
            "isolated_builder": isolated_builder,
            "isolated_builder_cleanup": builder_cleanup,
            "docker_socket_before": socket_before,
            "docker_socket_after": socket_after,
            "docker_socket_records_equal": certifying,
            "apparmor_before": apparmor_before,
            "apparmor_after": apparmor_after,
            "apparmor_records_equal": certifying,
            "docker_namespace_before": namespace_before,
            "docker_namespace_after": namespace_after,
            "docker_namespace_records_equal": certifying,
        },
        "environment_review_closure": environment_review_record,
        "inner": inner,
        "boundary": {
            "host_root_sentinel_required": False,
            "host_systemd_units_touched": 0,
            "host_nft_tables_touched": 0,
            "real_broker_connections": 0, "broker_protocol_messages": 0,
            "real_credentials": 0, "paper_orders": 0,
            "paper_units_instantiated": 8, "inert_stub_only": True,
            "fixture_local_authority_only": True,
            "paper_test_admission_candidate": False,
            "paper_authorized": False, "live_authorized": False,
            "mutation_authorized": False, "direct_broker_access": False,
            "order_submission_authorized": False,
        },
    }
    return seal_report(report)


REPORT_FIELDS = frozenset({
    "schema", "run_id", "decision", "passed", "rehearsal_passed",
    "certification_ready", "certification_blockers", "scope",
    "started_at_ms", "completed_at_ms", "expires_at_ms", "duration_ms",
    "paper_test_admission_candidate", "paper_authorized", "live_authorized",
    "mutation_authorized", "direct_broker_access",
    "order_submission_authorized", "lineage", "inputs",
    "generated_input_sha256", "platform", "container",
    "disposable_cleanup", "certification", "environment_review_closure",
    "inner", "boundary",
    "body_sha256",
})


def seal_report(report: dict[str, object]) -> dict[str, object]:
    if "body_sha256" in report:
        fail("report is already sealed")
    result = dict(report)
    result["body_sha256"] = canonical_sha256(result)
    return validate_report(result)


def validate_report(value: object) -> dict[str, object]:
    if (
            not isinstance(value, dict) or set(value) != REPORT_FIELDS or
            value.get("schema") != SCHEMA or
            value.get("schema") == LEGACY_SCHEMA or
            re.fullmatch(r"[0-9a-f]{32}", str(value.get("run_id", ""))) is None or
            value.get("scope") !=
            "broker-free-paper-domain-rootful-prerequisite-only"):
        fail("PAPER-domain v2 report exact-field/schema mismatch")
    body = {key: item for key, item in value.items() if key != "body_sha256"}
    if (
            re.fullmatch(
                r"sha256:[0-9a-f]{64}", str(value.get("body_sha256", "")))
            is None or canonical_sha256(body) != value["body_sha256"]):
        fail("PAPER-domain v2 report body digest mismatch")
    for field in (
            "paper_test_admission_candidate", "paper_authorized",
            "live_authorized", "mutation_authorized", "direct_broker_access",
            "order_submission_authorized"):
        if value.get(field) is not False:
            fail("report attempted to grant authority: " + field)
    started = value.get("started_at_ms")
    completed = value.get("completed_at_ms")
    expires = value.get("expires_at_ms")
    duration = value.get("duration_ms")
    if (
            any(type(item) is not int for item in
                (started, completed, expires, duration)) or
            not 0 < int(started) <= int(completed) < int(expires) or
            int(expires) - int(completed) > REPORT_LIFETIME_MS or
            int(duration) < 0):
        fail("report time/freshness contract mismatch")
    decision = value.get("decision")
    blockers = value.get("certification_blockers")
    if decision not in {"GO", "NO_GO", "REHEARSAL_ONLY"}:
        fail("report decision invalid")
    if not isinstance(blockers, list) or any(
            not isinstance(item, str) or not item for item in blockers):
        fail("report certification blockers malformed")
    if decision == "GO":
        if (
                value.get("passed") is not True or
                value.get("rehearsal_passed") is not True or
                value.get("certification_ready") is not True or blockers != []):
            fail("GO promotion flags mismatch")
    elif decision == "REHEARSAL_ONLY":
        if (
                value.get("passed") is not False or
                value.get("rehearsal_passed") is not True or
                value.get("certification_ready") is not False or
                blockers != list(CERTIFICATION_BLOCKERS)):
            fail("rehearsal-only report promotion mismatch")
    else:
        if (
                value.get("passed") is not False or
                value.get("certification_ready") is not False or not blockers):
            fail("NO_GO report state mismatch")

    lineage = value.get("lineage")
    if not isinstance(lineage, dict) or set(lineage) != {
            "source_commit", "expected_source_commit", "source_tree_clean",
            "all_inputs_versioned", "inputs_stable", "final_lineage",
            "input_manifest_sha256", "expected_input_manifest_sha256",
            "runner_sha256", "expected_runner_sha256"}:
        fail("report lineage exact-field mismatch")
    if (
            CANONICAL_COMMIT.fullmatch(
                str(lineage.get("source_commit", ""))) is None or
            lineage.get("source_commit") != lineage.get("expected_source_commit") or
            any(type(lineage.get(field)) is not bool for field in (
                "source_tree_clean", "all_inputs_versioned", "inputs_stable",
                "final_lineage")) or
            lineage.get("inputs_stable") is not True or
            any(re.fullmatch(r"sha256:[0-9a-f]{64}", str(lineage.get(field, "")))
                is None for field in ("input_manifest_sha256", "runner_sha256"))):
        fail("report lineage values mismatch")
    if decision == "GO" and (
            lineage.get("source_tree_clean") is not True or
            lineage.get("all_inputs_versioned") is not True or
            lineage.get("final_lineage") is not True or
            lineage.get("input_manifest_sha256") !=
            lineage.get("expected_input_manifest_sha256") or
            lineage.get("runner_sha256") != lineage.get("expected_runner_sha256")):
        fail("GO report lacks frozen externally pinned lineage")
    if decision != "GO" and (
            lineage.get("expected_input_manifest_sha256") is not None or
            lineage.get("expected_runner_sha256") is not None):
        fail("non-certifying report contains certification input pins")

    inputs = value.get("inputs")
    if not isinstance(inputs, dict) or set(inputs) != set(SOURCE_FILES):
        fail("report source input inventory mismatch")
    for record in inputs.values():
        if (
                not isinstance(record, dict) or
                set(record) != {"sha256", "size", "mode"} or
                re.fullmatch(r"[0-9a-f]{64}", str(record.get("sha256", ""))) is None or
                type(record.get("size")) is not int or record["size"] <= 0 or
                record.get("mode") not in {"0400", "0600", "0644", "0755"}):
            fail("report source input record malformed")
    if canonical_sha256(inputs) != lineage["input_manifest_sha256"]:
        fail("report input manifest digest mismatch")
    runner = inputs["scripts/run_hepta_paper_domain_rootful_systemd_gate.py"]
    if "sha256:" + str(runner["sha256"]) != lineage["runner_sha256"]:
        fail("report runner digest mismatch")
    generated = value.get("generated_input_sha256")
    if (
            not isinstance(generated, dict) or not generated or
            any(not isinstance(key, str) or
                re.fullmatch(r"[0-9a-f]{64}", str(item)) is None
                for key, item in generated.items())):
        fail("generated input inventory malformed")

    platform = value.get("platform")
    if not isinstance(platform, dict) or set(platform) != {
            "host_kernel", "host_architecture", "docker_client",
            "docker_server_version", "docker_server_api_version",
            "docker_server_os", "docker_server_architecture",
            "docker_cgroup_driver", "docker_cgroup_version",
            "docker_default_runtime", "docker_security_options",
            "base_image_reference", "base_image_id", "base_image_os",
            "base_image_architecture", "systemd", "nft", "container_kernel",
            "container_architecture", "container_cgroup", "container_boot_id",
            "container_pid1_cgroup", "immutable_file_count",
            "immutable_file_inventory_sha256", "package_count",
            "package_inventory_sha256"}:
        fail("platform evidence exact-field mismatch")
    if (
            any(not isinstance(platform.get(field), str) or not platform[field]
                for field in set(platform) - {"docker_security_options"}) or
            not isinstance(platform.get("docker_security_options"), list) or
            not any(isinstance(item, str) and "apparmor" in item.lower()
                    for item in platform["docker_security_options"]) or
            platform.get("docker_cgroup_version") != "2" or
            PINNED_IMAGE.fullmatch(
                str(platform.get("base_image_reference", ""))) is None or
            CANONICAL_IMAGE_ID.fullmatch(
                str(platform.get("base_image_id", ""))) is None or
            platform.get("base_image_os") != "linux" or
            CANONICAL_BOOT_ID.fullmatch(
                str(platform.get("container_boot_id", ""))) is None or
            platform.get("container_pid1_cgroup") != "0::/" or
            platform.get("container_cgroup") != "v2-private"):
        fail("platform evidence values mismatch")

    container = value.get("container")
    if not isinstance(container, dict) or set(container) != {
            "image_id", "network_mode", "read_only_rootfs",
            "private_cgroup_namespace", "privileged", "bind_mounts",
            "published_ports", "devices", "device_requests", "links",
            "tmpfs_allowlist", "apparmor_profile", "capabilities"}:
        fail("runtime container exact-field mismatch")
    if (
            CANONICAL_IMAGE_ID.fullmatch(str(container.get("image_id", ""))) is None or
            container.get("network_mode") != "none" or
            container.get("read_only_rootfs") is not True or
            container.get("private_cgroup_namespace") is not True or
            container.get("privileged") is not False or
            any(container.get(field) != 0 for field in (
                "bind_mounts", "published_ports", "devices",
                "device_requests", "links")) or
            container.get("tmpfs_allowlist") != RUNTIME_TMPFS or
            container.get("apparmor_profile") != APPARMOR_PROFILE or
            container.get("capabilities") != list(RUNTIME_CAPABILITIES)):
        fail("runtime container boundary mismatch")
    cleanup = value.get("disposable_cleanup")
    if cleanup != {
            "container_absent": True, "image_tag_absent": True,
            "image_id_absent": True}:
        fail("runtime disposable cleanup mismatch")
    inner = value.get("inner")
    if not isinstance(inner, dict):
        fail("inner result missing")
    validate_inner(
        INNER_MARKER + json.dumps(inner, sort_keys=True, separators=(",", ":")),
        expected_run_id=str(value["run_id"]))
    if value.get("boundary") != {
            "host_root_sentinel_required": False,
            "host_systemd_units_touched": 0, "host_nft_tables_touched": 0,
            "real_broker_connections": 0, "broker_protocol_messages": 0,
            "real_credentials": 0, "paper_orders": 0,
            "paper_units_instantiated": 8, "inert_stub_only": True,
            "fixture_local_authority_only": True,
            "paper_test_admission_candidate": False,
            "paper_authorized": False, "live_authorized": False,
            "mutation_authorized": False, "direct_broker_access": False,
            "order_submission_authorized": False}:
        fail("outer authority/broker boundary mismatch")
    certification = value.get("certification")
    if not isinstance(certification, dict) or set(certification) != {
            "requested", "eligible", "provenance", "provenance_reopened_equal",
            "reviewed_base", "reviewed_buildkit", "buildx_toolchain",
            "isolated_builder", "isolated_builder_cleanup",
            "docker_socket_before", "docker_socket_after",
            "docker_socket_records_equal", "apparmor_before", "apparmor_after",
            "apparmor_records_equal", "docker_namespace_before",
            "docker_namespace_after", "docker_namespace_records_equal"}:
        fail("certification evidence exact-field mismatch")
    evidence_names = (
        "provenance", "reviewed_base", "reviewed_buildkit", "buildx_toolchain",
        "isolated_builder", "isolated_builder_cleanup", "docker_socket_before",
        "docker_socket_after", "apparmor_before", "apparmor_after",
        "docker_namespace_before", "docker_namespace_after")
    equality_names = (
        "provenance_reopened_equal", "docker_socket_records_equal",
        "apparmor_records_equal", "docker_namespace_records_equal")
    environment_review = value.get("environment_review_closure")
    if decision == "GO":
        try:
            ROOT_REVIEW.validate_verification_record(
                environment_review, now_ms=int(completed))
        except ROOT_REVIEW.ReviewClosureError as error:
            raise GateError(str(error)) from error
        assert isinstance(environment_review, dict)
        if (
                certification.get("requested") is not True or
                certification.get("eligible") is not True or
                any(certification.get(field) is None for field in evidence_names) or
                any(certification.get(field) is not True for field in equality_names) or
                certification.get("docker_socket_before") !=
                certification.get("docker_socket_after") or
                certification.get("apparmor_before") !=
                certification.get("apparmor_after") or
                certification.get("docker_namespace_before") !=
                certification.get("docker_namespace_after")):
            fail("GO certification evidence incomplete or drifted")
        builder = certification["isolated_builder"]
        builder_cleanup = certification["isolated_builder_cleanup"]
        if (
                not isinstance(builder, dict) or
                not isinstance(builder.get("container_running"), dict) or
                builder["container_running"].get("network_mode") != "none" or
                builder["container_running"].get("bind_mounts") != 0 or
                builder["container_running"].get("devices") != 0 or
                builder["container_running"].get("published_ports") != 0 or
                not isinstance(builder_cleanup, dict) or
                any(builder_cleanup.get(field) is not True for field in (
                    "container_absent", "state_volume_absent",
                    "private_builder_metadata_absent", "buildkit_image_retained"))):
            fail("isolated builder boundary/cleanup mismatch")
        apparmor = certification["apparmor_before"]
        if (
                not isinstance(apparmor, dict) or
                apparmor.get("profile") != APPARMOR_PROFILE or
                apparmor.get("mode") != "enforce"):
            fail("GO AppArmor evidence is not enforcing")
        provenance = certification["provenance"]
        if not isinstance(provenance, dict) or set(provenance) != {
                "base", "builder", "apparmor", "docker_namespace"}:
            fail("GO provenance inventory mismatch")
        specifications = {
            "base": (REVIEWED_BASE_PROVENANCE_SCHEMA, REVIEWED_BASE_KEYS),
            "builder": (REVIEWED_BUILDER_PROVENANCE_SCHEMA, REVIEWED_BUILDER_KEYS),
            "apparmor": (REVIEWED_APPARMOR_PROVENANCE_SCHEMA,
                         REVIEWED_APPARMOR_KEYS),
            "docker_namespace": (REVIEWED_DOCKER_NAMESPACE_PROVENANCE_SCHEMA,
                                 REVIEWED_DOCKER_NAMESPACE_KEYS),
        }
        document_pins: set[str] = set()
        identities: set[str] = set()
        paths: set[str] = set()
        inode_keys: set[tuple[int, int]] = set()
        for kind, (schema, body_keys) in specifications.items():
            record = provenance[kind]
            metadata_keys = {
                "path", "document_sha256", "root_owned", "canonical_json",
                "mode", "device", "inode", "nlink", "uid", "gid",
                "identity_sha256"}
            if (
                    not isinstance(record, dict) or
                    set(record) != body_keys | metadata_keys or
                    record.get("schema") != schema or
                    record.get("decision") != "GO" or
                    record.get("root_owned") is not True or
                    record.get("canonical_json") is not True or
                    record.get("mode") not in {"0400", "0600"} or
                    not isinstance(record.get("path"), str) or
                    not str(record["path"]).startswith("/") or
                    str(record["path"]).startswith("//") or
                    os.path.normpath(str(record["path"])) != record["path"] or
                    any(type(record.get(field)) is not int for field in (
                        "device", "inode", "nlink", "uid", "gid")) or
                    record.get("device", -1) < 0 or
                    record.get("inode", 0) <= 0 or
                    record.get("nlink") != 1 or record.get("uid") != 0 or
                    record.get("gid") != 0 or
                    canonical_sha256({key: record[key] for key in body_keys}) !=
                    record.get("document_sha256") or
                    re.fullmatch(r"sha256:[0-9a-f]{64}", str(
                        record.get("identity_sha256", ""))) is None or
                    type(record.get("issued_at_ms")) is not int or
                    type(record.get("expires_at_ms")) is not int or
                    record["issued_at_ms"] > started or
                    record["expires_at_ms"] <= completed or
                    record["expires_at_ms"] - record["issued_at_ms"] >
                    MAX_PROVENANCE_LIFETIME_MS):
                fail("GO provenance record mismatch: " + kind)
            document_pins.add(str(record["document_sha256"]))
            identities.add(str(record["identity_sha256"]))
            paths.add(str(record["path"]))
            inode_keys.add((int(record["device"]), int(record["inode"])))
        if (
                len(document_pins) != 4 or len(identities) != 4 or
                len(paths) != 4 or len(inode_keys) != 4):
            fail("GO provenance documents are not four-way independent")
        reviewed_base = certification["reviewed_base"]
        reviewed_buildkit = certification["reviewed_buildkit"]
        toolchain = certification["buildx_toolchain"]
        if (
                not isinstance(reviewed_base, dict) or
                reviewed_base.get("reference") != platform["base_image_reference"] or
                reviewed_base.get("id") != platform["base_image_id"] or
                reviewed_base.get("reviewed_provenance") != provenance["base"] or
                not isinstance(reviewed_buildkit, dict) or
                reviewed_buildkit.get("reviewed_provenance") != provenance["builder"] or
                not isinstance(toolchain, dict) or toolchain.get("reviewed") is not True or
                toolchain.get("buildx_binary_sha256") !=
                provenance["builder"].get("buildx_binary_sha256")):
            fail("GO reviewed image/toolchain provenance binding mismatch")
        if (
                environment_review.get("source_commit") !=
                    lineage["source_commit"] or
                environment_review.get("base_image_reference") !=
                    platform["base_image_reference"] or
                environment_review.get("buildkit_image_reference") !=
                    provenance["builder"].get("repo_digest") or
                any(environment_review["outputs"][kind]["file_sha256"] !=
                    provenance[kind]["document_sha256"]
                    for kind in provenance) or
                value["expires_at_ms"] != min(
                    int(environment_review["expires_at_ms"]),
                    *(int(provenance[kind]["expires_at_ms"])
                      for kind in provenance),
                    int(completed) + REPORT_LIFETIME_MS)):
            fail("GO report is not bound to the signed review closure")
    else:
        if (
                environment_review is not None or
                certification.get("requested") is not False or
                certification.get("eligible") is not False or
                any(certification.get(field) is not None for field in evidence_names) or
                any(certification.get(field) is not False for field in equality_names)):
            fail("non-certifying report contains promotable evidence")
    return value


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--run", action="store_true")
    parser.add_argument("--allow-dirty-rehearsal", action="store_true")
    parser.add_argument("--certify", action="store_true")
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
    parser.add_argument("--expected-source-commit", required=True)
    parser.add_argument("--expected-input-manifest-sha256")
    parser.add_argument("--expected-runner-sha256")
    ROOT_REVIEW.add_arguments(parser)
    parser.add_argument("--report", type=Path, required=True)
    arguments = parser.parse_args()
    try:
        report_path = safe_report_path(arguments.report)
        if not arguments.run:
            print(
                "hepta_paper_domain_rootful_systemd_gate: disabled; "
                "pass --run explicitly",
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
            expected_input_manifest_sha256=
                arguments.expected_input_manifest_sha256,
            expected_runner_sha256=arguments.expected_runner_sha256,
            environment_review=environment_review,
        )
        report = execute(
            arguments.base_image, arguments.expected_source_commit,
            allow_dirty_rehearsal=arguments.allow_dirty_rehearsal,
            certification_request=certification_request)
        atomic_report(report_path, report)
    except (
            GateError, OSError, ValueError, subprocess.SubprocessError
            ) as error:
        print(
            "hepta_paper_domain_rootful_systemd_gate: FAIL: " +
            (str(error) or type(error).__name__)[:2048],
            file=sys.stderr,
        )
        return 1
    print(
        "hepta_paper_domain_rootful_systemd_gate: " +
        str(report["decision"]) +
        " paper_units=8 broker_connections=0 orders=0 "
        "paper_test_admission_candidate=0")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
