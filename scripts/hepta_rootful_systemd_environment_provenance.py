#!/usr/bin/env python3
"""Produce externally reviewed rootful-systemd environment provenance.

This fixed-installed, root-only tool never loads policy, starts a container,
or contacts a broker.  It first observes the already-installed environment and
publishes a short-lived canonical review request.  A separate reviewer signs
an authorization containing the exact request nonce, request digest, and full
candidate observation with the fixed Ed25519 trust key.  A later production
invocation securely reopens every input, verifies the signature with the fixed
OpenSSL executable, observes the environment twice, and publishes the four
Version 1 GO documents consumed by the rootful systemd gates.

The optional offline-candidate mode exists only for deterministic rehearsal.
Offline or injected observations are permanently marked NO_GO and cannot be
used by the production publisher.
"""

from __future__ import annotations

import argparse
import base64
import ctypes
from dataclasses import dataclass
import errno
import hashlib
import io
import json
import os
from pathlib import Path, PurePosixPath
import re
import secrets
import stat
import subprocess
import sys
import tarfile
import tempfile
import time
from typing import Any, Mapping, Sequence


VERSION = 1
ROOT_UID = 0
ROOT_GID = 0
PROFILE_NAME = "hepta-systemd-gate"

INSTALLED_EXECUTABLE = Path(
    "/usr/libexec/hepta-rootful-systemd-environment-provenance")
DOCKER_CLI = Path("/usr/bin/docker")
OPENSSL = Path("/usr/bin/openssl")
VERIFICATION_KEY = Path(
    "/etc/heptatrader/rootful-systemd-review-ed25519.pub")
APPARMOR_POLICY_SOURCE = Path(
    "/usr/share/heptatrader/systemd/hepta-systemd-gate.apparmor")
APPARMOR_ENABLED = Path("/sys/module/apparmor/parameters/enabled")
APPARMOR_ROOT = Path("/sys/kernel/security/apparmor")
APPARMOR_POLICY_ROOT = APPARMOR_ROOT / "policy"
DOCKER_SOCKET = Path("/run/docker.sock")
PROC_ROOT = Path("/proc")

REQUEST_SCHEMA = (
    "hepta.agent-os-rootful-systemd-environment-review-request.v1")
AUTHORIZATION_ENVELOPE_SCHEMA = (
    "hepta.agent-os-rootful-systemd-environment-review-authorization-"
    "envelope.v1")
AUTHORIZATION_PAYLOAD_SCHEMA = (
    "hepta.agent-os-rootful-systemd-environment-review-authorization.v1")
OFFLINE_OBSERVATION_SCHEMA = (
    "hepta.agent-os-rootful-systemd-environment-offline-observation.v1")
BASE_SCHEMA = (
    "hepta.agent-os-rootful-systemd-base-reviewed-provenance.v1")
BUILDER_SCHEMA = (
    "hepta.agent-os-rootful-systemd-isolated-builder-reviewed-provenance.v1")
APPARMOR_SCHEMA = (
    "hepta.agent-os-rootful-systemd-apparmor-reviewed-provenance.v1")
DOCKER_NAMESPACE_SCHEMA = (
    "hepta.agent-os-rootful-systemd-docker-apparmor-namespace-reviewed-"
    "provenance.v1")
REVIEW_CLOSURE_SCHEMA = (
    "hepta.agent-os-rootful-systemd-environment-review-closure.v1")

PRODUCTION_OBSERVATION_MODE = "PRODUCTION_ROOT_DIRECT_OBSERVATION"
OFFLINE_OBSERVATION_MODE = "OFFLINE_FAKE_NO_GO"
REVIEW_AUTHORITY = "EXTERNAL_INDEPENDENT_ROOTFUL_ENVIRONMENT_REVIEW"
SIGNATURE_ALGORITHM = "ED25519"

MAX_JSON = 4 * 1024 * 1024
MAX_COMMAND_OUTPUT = 16 * 1024 * 1024
MAX_EXECUTABLE = 512 * 1024 * 1024
MAX_IMAGE_SAVE = 2 * 1024 * 1024 * 1024
MAX_LAYER_MEMBER = 512 * 1024 * 1024
MAX_REQUEST_LIFETIME_MS = 60 * 60 * 1000
MAX_AUTHORIZATION_LIFETIME_MS = 60 * 60 * 1000
MAX_CLOCK_SKEW_MS = 5 * 1000
RENAME_NOREPLACE = 1

DIGEST = re.compile(r"sha256:[0-9a-f]{64}")
BARE_DIGEST = re.compile(r"[0-9a-f]{64}")
PINNED_IMAGE = re.compile(
    r"[a-z0-9][a-z0-9._/:-]*@sha256:[0-9a-f]{64}")
IMAGE_ID = re.compile(r"sha256:[0-9a-f]{64}")
SEMVER = re.compile(
    r"[0-9]+\.[0-9]+\.[0-9]+(?:[-+][0-9A-Za-z._-]+)?")
BUILDKIT_VERSION = re.compile(
    r"v[0-9]+\.[0-9]+\.[0-9]+(?:[-+][0-9A-Za-z._-]+)?")
DOCKER_API_VERSION = re.compile(r"[1-9][0-9]*\.[0-9]+")
BUILD_ID = re.compile(r"[0-9A-Za-z][0-9A-Za-z._+-]{0,127}")
DAEMON_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9:._-]{0,127}")
BOOT_ID = re.compile(
    r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-"
    r"[0-9a-f]{12}")
NONCE = re.compile(r"[0-9a-f]{64}")
RAW_ABI = re.compile(r"v[1-9][0-9]{0,2}")
POLICY_ENTRY = re.compile(r"[A-Za-z0-9_.:@+=-]{1,255}")
REVIEWER_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:@+-]{0,127}")

FALSE_AUTHORITY = {
    "paper_authorized": False,
    "live_authorized": False,
    "mutation_authorized": False,
    "direct_broker_access": False,
    "order_submission_authorized": False,
}
AUTHORITY_FIELDS = frozenset(FALSE_AUTHORITY)

BASE_KEYS = frozenset({
    "schema", "decision", "issued_at_ms", "expires_at_ms", "image_id",
    "repo_digest", "labels_sha256",
})
BUILDER_KEYS = frozenset({
    "schema", "decision", "issued_at_ms", "expires_at_ms", "image_id",
    "repo_digest", "config_sha256", "buildkit_version", "buildx_version",
    "buildx_binary_sha256", "docker_server_version",
    "docker_server_api_version", "docker_server_git_commit",
})
APPARMOR_KEYS = frozenset({
    "schema", "decision", "issued_at_ms", "expires_at_ms", "profile",
    "policy_source_sha256", "profile_sha256", "raw_sha256", "raw_abi",
})
DOCKER_NAMESPACE_KEYS = frozenset({
    "schema", "decision", "issued_at_ms", "expires_at_ms",
    "docker_daemon_id", "docker_daemon_pid",
    "docker_daemon_start_time_ticks", "host_boot_id",
    "host_namespace_name", "host_namespace_level",
    "host_namespace_stacked", "daemon_namespace_name",
    "daemon_namespace_level", "daemon_namespace_stacked",
})

BASE_LABELS = {
    "io.hepta.rootful-systemd-base.offline-ready": "true",
    "io.hepta.rootful-systemd-base.version": "1",
}
BUILDER_RESERVED_LABELS = frozenset({
    "io.hepta.purpose", "io.hepta.role", "io.hepta.run-id",
    "io.hepta.buildkit-image-id", "io.hepta.buildx-builder",
})

REQUEST_FIELDS = frozenset({
    "schema", "version", "status", "observation_mode", "observed_at_ms",
    "expires_at_ms", "nonce", "base_image_reference",
    "buildkit_image_reference", "observations", "trust_bindings",
    "go_eligible", *AUTHORITY_FIELDS, "request_sha256",
})
AUTHORIZATION_ENVELOPE_FIELDS = frozenset({
    "schema", "version", "payload", "signature_base64",
})
AUTHORIZATION_PAYLOAD_FIELDS = frozenset({
    "schema", "version", "decision", "review_authority", "reviewer_id",
    "issued_at_ms", "expires_at_ms", "nonce", "request_sha256",
    "base_image_reference", "buildkit_image_reference", "observations",
    "trust_bindings", *AUTHORITY_FIELDS,
})
OFFLINE_FIELDS = frozenset({
    "schema", "version", "base_image_reference",
    "buildkit_image_reference", "observations", "trust_bindings",
    *AUTHORITY_FIELDS,
})

OBSERVATION_TOP_FIELDS = frozenset({
    "base_image", "isolated_builder", "apparmor", "docker_namespace",
})
BASE_OBSERVATION_FIELDS = frozenset({
    "image_id", "repo_digest", "repo_digests", "labels_sha256", "os",
    "architecture", "declared_volumes", "onbuild_instructions",
})
BUILDER_OBSERVATION_FIELDS = frozenset({
    "image_id", "repo_digest", "repo_digests", "config_sha256", "os",
    "architecture", "entrypoint", "buildkit_binary_path",
    "buildkit_binary_sha256", "buildkit_version", "buildx_path",
    "buildx_path_sha256", "buildx_binary_sha256", "buildx_version",
    "docker_server_version", "docker_server_api_version",
    "docker_server_git_commit",
})
APPARMOR_OBSERVATION_FIELDS = frozenset({
    "profile", "mode", "attach", "learning_count",
    "policy_source_sha256", "profile_sha256", "raw_sha256", "raw_abi",
    "raw_data_id", "namespace_name", "namespace_level",
    "namespace_stacked", "profile_inventory_sha256",
})
DOCKER_OBSERVATION_FIELDS = frozenset({
    "docker_daemon_id", "docker_daemon_pid",
    "docker_daemon_start_time_ticks", "docker_daemon_exe_sha256",
    "host_boot_id", "host_namespace_name", "host_namespace_level",
    "host_namespace_stacked", "daemon_namespace_name",
    "daemon_namespace_level", "daemon_namespace_stacked",
    "daemon_apparmor_current", "self_user_namespace_inode",
    "daemon_user_namespace_inode",
})
TRUST_BINDING_FIELDS = frozenset({
    "producer", "docker_cli", "signature_verifier", "verification_key",
    "apparmor_policy_source",
})
REFERENCE_FIELDS = frozenset({"path", "sha256"})
REQUEST_REFERENCE_FIELDS = frozenset({
    "path", "file_sha256", "request_sha256", "nonce",
})
AUTHORIZATION_REFERENCE_FIELDS = frozenset({
    "path", "file_sha256", "signed_payload_sha256", "signature_sha256",
})
OUTPUT_REFERENCE_FIELDS = frozenset({"path", "file_sha256", "schema"})
REVIEW_CLOSURE_FIELDS = frozenset({
    "schema", "version", "status", "issued_at_ms", "expires_at_ms",
    "base_image_reference", "buildkit_image_reference", "review_authority",
    "reviewer_id", "request_reference", "authorization_reference",
    "producer", "trust_bindings", "outputs", *AUTHORITY_FIELDS,
    "closure_sha256",
})

OUTPUT_FILENAMES = {
    "base": "reviewed-base-image-provenance.v1.json",
    "builder": "reviewed-isolated-builder-provenance.v1.json",
    "apparmor": "reviewed-apparmor-provenance.v1.json",
    "docker_namespace":
        "reviewed-docker-apparmor-namespace-provenance.v1.json",
}
REVIEW_CLOSURE_FILENAME = "review-closure.v1.json"

NOFOLLOW = getattr(os, "O_NOFOLLOW", 0)
CLOEXEC = getattr(os, "O_CLOEXEC", 0)
NONBLOCK = getattr(os, "O_NONBLOCK", 0)
DIRECTORY_FLAGS = os.O_RDONLY | os.O_DIRECTORY | NOFOLLOW | CLOEXEC
READ_FLAGS = os.O_RDONLY | NOFOLLOW | CLOEXEC | NONBLOCK
CREATE_FLAGS = os.O_WRONLY | os.O_CREAT | os.O_EXCL | NOFOLLOW | CLOEXEC
LIBC = ctypes.CDLL(None, use_errno=True)
SAFE_ENVIRONMENT = {
    "PATH": "/usr/sbin:/usr/bin:/sbin:/bin", "LANG": "C",
    "LC_ALL": "C", "TZ": "UTC", "PYTHONNOUSERSITE": "1",
}
CLI_RUN_TOKEN = object()


class ProvenanceError(RuntimeError):
    """Stable fail-closed producer error."""

    def __init__(self, reason: str):
        super().__init__(reason)
        self.reason = reason


def require(condition: bool, reason: str) -> None:
    if not condition:
        raise ProvenanceError(reason)


def canonical_bytes(value: Any) -> bytes:
    try:
        return (json.dumps(
            value, ensure_ascii=True, allow_nan=False, sort_keys=True,
            separators=(",", ":")) + "\n").encode("ascii")
    except (TypeError, ValueError, UnicodeError) as error:
        raise ProvenanceError("PROVENANCE_CANONICALIZATION_FAILED") from error


def digest_bytes(payload: bytes) -> str:
    return "sha256:" + hashlib.sha256(payload).hexdigest()


def canonical_object_sha256(value: Any) -> str:
    try:
        payload = json.dumps(
            value, ensure_ascii=True, allow_nan=False, sort_keys=True,
            separators=(",", ":")).encode("ascii")
    except (TypeError, ValueError, UnicodeError) as error:
        raise ProvenanceError("PROVENANCE_CANONICALIZATION_FAILED") from error
    return digest_bytes(payload)


def strict_object(payload: bytes, reason: str) -> dict[str, Any]:
    def unique(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in pairs:
            require(key not in result, reason)
            result[key] = value
        return result

    try:
        value = json.loads(
            payload.decode("ascii", errors="strict"),
            object_pairs_hook=unique,
            parse_float=lambda _value: (_ for _ in ()).throw(
                ProvenanceError(reason)),
            parse_constant=lambda _value: (_ for _ in ()).throw(
                ProvenanceError(reason)),
        )
    except ProvenanceError:
        raise
    except (UnicodeError, json.JSONDecodeError) as error:
        raise ProvenanceError(reason) from error
    require(type(value) is dict, reason)
    return value


def identity(metadata: os.stat_result) -> tuple[int, ...]:
    return (
        metadata.st_dev, metadata.st_ino, metadata.st_mode,
        metadata.st_nlink, metadata.st_uid, metadata.st_gid,
        metadata.st_size, metadata.st_mtime_ns, metadata.st_ctime_ns,
    )


def directory_identity(metadata: os.stat_result) -> tuple[int, ...]:
    """Return stable identity fields; child churn may change ``st_nlink``."""

    return (
        metadata.st_dev, metadata.st_ino, metadata.st_mode,
        metadata.st_uid, metadata.st_gid,
    )


def canonical_path(path: Path, reason: str) -> Path:
    require(isinstance(path, Path) and path.is_absolute(), reason)
    normalized = Path(os.path.normpath(os.fspath(path)))
    require(normalized == path and path.name not in {"", ".", ".."}, reason)
    return path


def open_directory(path: Path, reason: str) -> int:
    path = canonical_path(path, reason)
    descriptor = -1
    try:
        descriptor = os.open("/", DIRECTORY_FLAGS)
        for component in path.parts[1:]:
            before = os.stat(
                component, dir_fd=descriptor, follow_symlinks=False)
            child = os.open(component, DIRECTORY_FLAGS, dir_fd=descriptor)
            opened = os.fstat(child)
            after = os.stat(
                component, dir_fd=descriptor, follow_symlinks=False)
            require(
                stat.S_ISDIR(opened.st_mode) and
                directory_identity(before) == directory_identity(opened) ==
                    directory_identity(after), reason)
            os.close(descriptor)
            descriptor = child
        return descriptor
    except (OSError, ProvenanceError) as error:
        if descriptor >= 0:
            os.close(descriptor)
        if isinstance(error, ProvenanceError):
            raise
        raise ProvenanceError(reason) from error


def trusted_directory(
    descriptor: int, reason: str, *, expected_uid: int,
    expected_gid: int, exact_mode: int | None = None,
) -> tuple[int, ...]:
    try:
        metadata = os.fstat(descriptor)
    except OSError as error:
        raise ProvenanceError(reason) from error
    mode = stat.S_IMODE(metadata.st_mode)
    require(
        stat.S_ISDIR(metadata.st_mode) and metadata.st_nlink >= 1 and
        metadata.st_uid == expected_uid and metadata.st_gid == expected_gid and
        (mode == exact_mode if exact_mode is not None else mode & 0o022 == 0),
        reason)
    return directory_identity(metadata)


def secure_read(
    path: Path, reason: str, *, expected_uid: int, expected_gid: int,
    modes: frozenset[int], maximum: int = MAX_JSON,
) -> tuple[bytes, tuple[int, ...], tuple[int, ...]]:
    path = canonical_path(path, reason)
    parent = open_directory(path.parent, reason)
    try:
        parent_identity = trusted_directory(
            parent, reason, expected_uid=expected_uid,
            expected_gid=expected_gid)
        before = os.stat(path.name, dir_fd=parent, follow_symlinks=False)
        descriptor = os.open(path.name, READ_FLAGS, dir_fd=parent)
        try:
            opened = os.fstat(descriptor)
            require(
                stat.S_ISREG(opened.st_mode) and opened.st_nlink == 1 and
                opened.st_uid == expected_uid and
                opened.st_gid == expected_gid and
                stat.S_IMODE(opened.st_mode) in modes and
                0 < opened.st_size <= maximum and
                identity(before) == identity(opened), reason)
            payload = bytearray()
            while len(payload) <= maximum:
                chunk = os.read(
                    descriptor, min(65536, maximum + 1 - len(payload)))
                if not chunk:
                    break
                payload.extend(chunk)
            after = os.fstat(descriptor)
            named = os.stat(
                path.name, dir_fd=parent, follow_symlinks=False)
            require(
                0 < len(payload) <= maximum and
                identity(opened) == identity(after) == identity(named) and
                parent_identity == trusted_directory(
                    parent, reason, expected_uid=expected_uid,
                    expected_gid=expected_gid), reason)
            return bytes(payload), identity(opened), parent_identity
        finally:
            os.close(descriptor)
    except ProvenanceError:
        raise
    except OSError as error:
        raise ProvenanceError(reason) from error
    finally:
        os.close(parent)


@dataclass(frozen=True)
class FileBinding:
    path: Path
    payload: bytes
    metadata_identity: tuple[int, ...]
    parent_identity: tuple[int, ...]
    expected_uid: int
    expected_gid: int
    modes: frozenset[int]
    maximum: int
    document: dict[str, Any] | None = None
    executing: bool = False

    @property
    def reference(self) -> dict[str, str]:
        return {"path": str(self.path), "sha256": digest_bytes(self.payload)}

    def reopen(self, reason: str = "PROVENANCE_BOUND_INPUT_DRIFT") -> None:
        if self.executing:
            lexical = Path(__file__).absolute()
            try:
                require(
                    lexical == self.path and not lexical.is_symlink() and
                    lexical.resolve(strict=True) == self.path and
                    os.path.samefile(lexical, self.path), reason)
            except OSError as error:
                raise ProvenanceError(reason) from error
        payload, metadata, parent = secure_read(
            self.path, reason, expected_uid=self.expected_uid,
            expected_gid=self.expected_gid, modes=self.modes,
            maximum=self.maximum)
        require(
            payload == self.payload and metadata == self.metadata_identity and
            parent == self.parent_identity, reason)
        if self.document is not None:
            require(strict_object(payload, reason) == self.document, reason)


def bind_file(
    path: Path, reason: str, *, expected_uid: int, expected_gid: int,
    modes: frozenset[int], maximum: int, parse_document: bool = False,
    executing: bool = False,
) -> FileBinding:
    path = canonical_path(path, reason)
    if executing:
        lexical = Path(__file__).absolute()
        try:
            require(
                lexical == path and not lexical.is_symlink() and
                lexical.resolve(strict=True) == path and
                os.path.samefile(lexical, path), reason)
        except OSError as error:
            raise ProvenanceError(reason) from error
    payload, metadata, parent = secure_read(
        path, reason, expected_uid=expected_uid, expected_gid=expected_gid,
        modes=modes, maximum=maximum)
    document = strict_object(payload, reason) if parse_document else None
    if document is not None:
        require(payload == canonical_bytes(document), reason)
    binding = FileBinding(
        path, payload, metadata, parent, expected_uid, expected_gid, modes,
        maximum, document, executing)
    binding.reopen(reason)
    return binding


@dataclass(frozen=True)
class CommandResult:
    stdout: bytes
    stderr: bytes
    returncode: int


class CommandExecutor:
    """Interface used by read-only observation code."""

    production = False

    def run(
        self, arguments: Sequence[str], *, timeout: int,
        maximum: int = MAX_COMMAND_OUTPUT,
    ) -> CommandResult:
        raise NotImplementedError


class ProductionExecutor(CommandExecutor):
    """Fixed production subprocess executor; it has no shell or network API."""

    production = True

    def __init__(self, docker_binding: FileBinding) -> None:
        self._docker_binding = docker_binding

    def run(
        self, arguments: Sequence[str], *, timeout: int,
        maximum: int = MAX_COMMAND_OUTPUT,
    ) -> CommandResult:
        require(
            type(arguments) in (tuple, list) and bool(arguments) and
            arguments[0] == str(DOCKER_CLI) and
            all(type(item) is str and "\x00" not in item for item in arguments),
            "PROVENANCE_COMMAND_INVALID")
        self._docker_binding.reopen()
        try:
            completed = subprocess.run(
                tuple(arguments), check=False, stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                env=SAFE_ENVIRONMENT, cwd="/", timeout=timeout)
        except (OSError, subprocess.SubprocessError) as error:
            raise ProvenanceError("PROVENANCE_COMMAND_FAILED") from error
        require(
            len(completed.stdout) <= maximum and
            len(completed.stderr) <= maximum,
            "PROVENANCE_COMMAND_OUTPUT_TOO_LARGE")
        self._docker_binding.reopen()
        return CommandResult(
            completed.stdout, completed.stderr, completed.returncode)

    def save_image(self, image_id: str) -> tempfile._TemporaryFileWrapper[Any] | Any:
        require(IMAGE_ID.fullmatch(image_id) is not None,
                "PROVENANCE_BUILDKIT_IMAGE_INVALID")
        self._docker_binding.reopen()
        temporary = tempfile.TemporaryFile(prefix="hepta-buildkit-image-")
        try:
            try:
                completed = subprocess.run(
                    (str(DOCKER_CLI), "image", "save", image_id),
                    check=False, stdin=subprocess.DEVNULL, stdout=temporary,
                    stderr=subprocess.PIPE, env=SAFE_ENVIRONMENT, cwd="/",
                    timeout=120)
            except (OSError, subprocess.SubprocessError) as error:
                raise ProvenanceError(
                    "PROVENANCE_BUILDKIT_IMAGE_SAVE_FAILED") from error
            size = os.fstat(temporary.fileno()).st_size
            require(
                completed.returncode == 0 and completed.stderr == b"" and
                0 < size <= MAX_IMAGE_SAVE,
                "PROVENANCE_BUILDKIT_IMAGE_SAVE_FAILED")
            temporary.seek(0)
            self._docker_binding.reopen()
            return temporary
        except BaseException:
            temporary.close()
            raise


def command_json(
    executor: CommandExecutor, arguments: Sequence[str], *, timeout: int = 30,
) -> Any:
    result = executor.run(arguments, timeout=timeout)
    require(
        result.returncode == 0 and result.stderr == b"" and
        0 < len(result.stdout) <= MAX_COMMAND_OUTPUT,
        "PROVENANCE_DOCKER_COMMAND_FAILED")
    try:
        return json.loads(
            result.stdout.decode("utf-8", errors="strict"),
            object_pairs_hook=lambda pairs: _unique_pairs(
                pairs, "PROVENANCE_DOCKER_JSON_INVALID"),
            parse_constant=lambda _value: (_ for _ in ()).throw(
                ProvenanceError("PROVENANCE_DOCKER_JSON_INVALID")),
        )
    except ProvenanceError:
        raise
    except (UnicodeError, json.JSONDecodeError) as error:
        raise ProvenanceError("PROVENANCE_DOCKER_JSON_INVALID") from error


def _unique_pairs(
    pairs: list[tuple[str, Any]], reason: str,
) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        require(key not in result, reason)
        result[key] = value
    return result


def validate_ed25519_public_key(payload: bytes) -> None:
    reason = "PROVENANCE_VERIFICATION_KEY_INVALID"
    require(
        payload.startswith(b"-----BEGIN PUBLIC KEY-----\n") and
        payload.endswith(b"-----END PUBLIC KEY-----\n") and
        b"\r" not in payload, reason)
    lines = payload.splitlines()
    require(
        len(lines) >= 3 and lines[0] == b"-----BEGIN PUBLIC KEY-----" and
        lines[-1] == b"-----END PUBLIC KEY-----" and
        all(0 < len(line) <= 64 for line in lines[1:-1]), reason)
    try:
        der = base64.b64decode(b"".join(lines[1:-1]), validate=True)
    except (ValueError, base64.binascii.Error) as error:
        raise ProvenanceError(reason) from error
    # RFC 8410 SubjectPublicKeyInfo: id-Ed25519 plus a 32-byte public key.
    require(
        len(der) == 44 and
        der[:12].hex() == "302a300506032b6570032100", reason)


def write_memfd(name: str, payload: bytes) -> int:
    reason = "PROVENANCE_MEMFD_FAILED"
    require(0 < len(payload) <= MAX_JSON, reason)
    try:
        descriptor = os.memfd_create(name, os.MFD_CLOEXEC)
        offset = 0
        while offset < len(payload):
            count = os.write(descriptor, payload[offset:])
            require(count > 0, reason)
            offset += count
        os.lseek(descriptor, 0, os.SEEK_SET)
        return descriptor
    except (AttributeError, OSError) as error:
        raise ProvenanceError(reason) from error


@dataclass(frozen=True)
class SignatureCertification:
    payload_sha256: str
    signature_sha256: str
    secret: object


class ProductionContext:
    """Root-only fixed executable, source, and trust-key bindings."""

    __slots__ = (
        "producer", "docker", "openssl", "verification_key",
        "apparmor_source", "executor", "_certification_secret",
    )

    def __init__(self) -> None:
        require(
            os.geteuid() == ROOT_UID and os.getegid() == ROOT_GID,
            "PROVENANCE_ROOT_REQUIRED")
        executable_modes = frozenset({0o500, 0o555, 0o700, 0o755})
        trust_modes = frozenset({0o400, 0o440, 0o444})
        source_modes = frozenset({0o400, 0o440, 0o444, 0o600, 0o640, 0o644})
        self.producer = bind_file(
            INSTALLED_EXECUTABLE, "PROVENANCE_EXECUTING_IMAGE_INVALID",
            expected_uid=ROOT_UID, expected_gid=ROOT_GID,
            modes=executable_modes, maximum=MAX_EXECUTABLE, executing=True)
        self.docker = bind_file(
            DOCKER_CLI, "PROVENANCE_DOCKER_CLI_INVALID",
            expected_uid=ROOT_UID, expected_gid=ROOT_GID,
            modes=executable_modes, maximum=MAX_EXECUTABLE)
        self.openssl = bind_file(
            OPENSSL, "PROVENANCE_OPENSSL_INVALID",
            expected_uid=ROOT_UID, expected_gid=ROOT_GID,
            modes=executable_modes, maximum=MAX_EXECUTABLE)
        self.verification_key = bind_file(
            VERIFICATION_KEY, "PROVENANCE_VERIFICATION_KEY_INVALID",
            expected_uid=ROOT_UID, expected_gid=ROOT_GID,
            modes=trust_modes, maximum=64 * 1024)
        validate_ed25519_public_key(self.verification_key.payload)
        self.apparmor_source = bind_file(
            APPARMOR_POLICY_SOURCE, "PROVENANCE_APPARMOR_SOURCE_INVALID",
            expected_uid=ROOT_UID, expected_gid=ROOT_GID,
            modes=source_modes, maximum=MAX_JSON)
        self.executor = ProductionExecutor(self.docker)
        self._certification_secret = object()
        self.reopen()

    @property
    def trust_bindings(self) -> dict[str, dict[str, str]]:
        return {
            "producer": self.producer.reference,
            "docker_cli": self.docker.reference,
            "signature_verifier": self.openssl.reference,
            "verification_key": self.verification_key.reference,
            "apparmor_policy_source": self.apparmor_source.reference,
        }

    def reopen(self) -> None:
        self.producer.reopen()
        self.docker.reopen()
        self.openssl.reopen()
        self.verification_key.reopen()
        self.apparmor_source.reopen()
        validate_ed25519_public_key(self.verification_key.payload)

    def verify_signature(
        self, payload: bytes, signature: bytes,
    ) -> SignatureCertification:
        reason = "PROVENANCE_AUTHORIZATION_SIGNATURE_INVALID"
        require(len(signature) == 64, reason)
        self.reopen()
        payload_fd = write_memfd("hepta-rootful-review", payload)
        signature_fd = write_memfd("hepta-rootful-review-signature", signature)
        key_fd = write_memfd(
            "hepta-rootful-review-key", self.verification_key.payload)
        try:
            arguments = (
                str(OPENSSL), "pkeyutl", "-verify", "-pubin", "-inkey",
                f"/proc/self/fd/{key_fd}", "-rawin", "-in",
                f"/proc/self/fd/{payload_fd}", "-sigfile",
                f"/proc/self/fd/{signature_fd}",
            )
            try:
                result = subprocess.run(
                    arguments, check=False, stdin=subprocess.DEVNULL,
                    stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                    pass_fds=(payload_fd, signature_fd, key_fd),
                    env=SAFE_ENVIRONMENT, cwd="/", timeout=15)
            except (OSError, subprocess.SubprocessError) as error:
                raise ProvenanceError(reason) from error
        finally:
            os.close(payload_fd)
            os.close(signature_fd)
            os.close(key_fd)
        require(
            result.returncode == 0 and result.stderr == b"" and
            result.stdout == b"Signature Verified Successfully\n", reason)
        self.reopen()
        return SignatureCertification(
            digest_bytes(payload), digest_bytes(signature),
            self._certification_secret)

    def certifies(
        self, certification: SignatureCertification, payload: bytes,
        signature: bytes,
    ) -> bool:
        return (
            type(certification) is SignatureCertification and
            certification.secret is self._certification_secret and
            certification.payload_sha256 == digest_bytes(payload) and
            certification.signature_sha256 == digest_bytes(signature)
        )


def require_digest(value: Any, reason: str) -> str:
    require(type(value) is str and DIGEST.fullmatch(value) is not None, reason)
    return value


def require_pinned_image(value: Any, reason: str) -> str:
    require(
        type(value) is str and PINNED_IMAGE.fullmatch(value) is not None,
        reason)
    return value


def read_bounded_file(
    path: Path, reason: str, *, maximum: int = 4096,
    require_root: bool = True,
) -> tuple[bytes, tuple[int, ...]]:
    """Read procfs/sysfs/AAFS evidence with before/open/after identity."""
    require(path.is_absolute(), reason)
    descriptor = -1
    try:
        before = os.lstat(path)
        descriptor = os.open(path, READ_FLAGS)
        opened = os.fstat(descriptor)
        require(
            stat.S_ISREG(opened.st_mode) and opened.st_nlink == 1 and
            (not require_root or
             (opened.st_uid == ROOT_UID and opened.st_gid == ROOT_GID)) and
            identity(before) == identity(opened), reason)
        payload = bytearray()
        while len(payload) <= maximum:
            chunk = os.read(
                descriptor, min(4096, maximum + 1 - len(payload)))
            if not chunk:
                break
            payload.extend(chunk)
        after = os.fstat(descriptor)
        named = os.lstat(path)
        require(
            0 < len(payload) <= maximum and
            identity(opened) == identity(after) == identity(named), reason)
        return bytes(payload), identity(opened)
    except ProvenanceError:
        raise
    except OSError as error:
        raise ProvenanceError(reason) from error
    finally:
        if descriptor >= 0:
            os.close(descriptor)


def read_scalar(path: Path, reason: str) -> str:
    payload, _metadata = read_bounded_file(path, reason)
    try:
        value = payload.decode("ascii", errors="strict")
    except UnicodeError as error:
        raise ProvenanceError(reason) from error
    require(
        value.endswith("\n") and value.count("\n") == 1 and "\r" not in value
        and "\x00" not in value, reason)
    return value[:-1]


def hash_bound_executable(path: Path, reason: str) -> tuple[str, FileBinding]:
    binding = bind_file(
        canonical_path(path, reason), reason, expected_uid=ROOT_UID,
        expected_gid=ROOT_GID,
        modes=frozenset({0o500, 0o555, 0o700, 0o755}),
        maximum=MAX_EXECUTABLE)
    return digest_bytes(binding.payload), binding


def inspect_image(
    executor: CommandExecutor, reference: str,
) -> dict[str, Any]:
    reference = require_pinned_image(reference, "PROVENANCE_IMAGE_INVALID")
    result = command_json(
        executor, (str(DOCKER_CLI), "image", "inspect", reference))
    require(
        type(result) is list and len(result) == 1 and
        type(result[0]) is dict, "PROVENANCE_IMAGE_INSPECT_INVALID")
    return result[0]


def validate_image_common(
    record: Mapping[str, Any], reference: str, reason: str,
) -> tuple[str, list[str], dict[str, Any]]:
    image_id = record.get("Id")
    repo_digests = record.get("RepoDigests")
    config = record.get("Config")
    require(
        type(image_id) is str and IMAGE_ID.fullmatch(image_id) is not None and
        type(repo_digests) is list and reference in repo_digests and
        len(repo_digests) == len(set(repo_digests)) and
        all(type(item) is str and PINNED_IMAGE.fullmatch(item) is not None
            for item in repo_digests) and
        type(config) is dict and record.get("Os") == "linux" and
        record.get("Architecture") == "amd64", reason)
    return image_id, sorted(repo_digests), config


def observe_base_image(
    executor: CommandExecutor, reference: str,
) -> dict[str, Any]:
    record = inspect_image(executor, reference)
    image_id, repo_digests, config = validate_image_common(
        record, reference, "PROVENANCE_BASE_IMAGE_INVALID")
    labels = config.get("Labels")
    onbuild = config.get("OnBuild")
    volumes = config.get("Volumes")
    require(
        "OnBuild" in config and onbuild in (None, []) and
        volumes in (None, {}) and labels == BASE_LABELS,
        "PROVENANCE_BASE_IMAGE_INVALID")
    return {
        "image_id": image_id,
        "repo_digest": reference,
        "repo_digests": repo_digests,
        "labels_sha256": canonical_object_sha256(labels),
        "os": "linux", "architecture": "amd64",
        "declared_volumes": 0, "onbuild_instructions": 0,
    }


def _safe_tar_path(name: str, reason: str) -> PurePosixPath:
    require(type(name) is str and "\x00" not in name, reason)
    stripped = name.removeprefix("./").rstrip("/")
    if stripped in {"", "."}:
        return PurePosixPath(".")
    candidate = PurePosixPath(stripped)
    require(
        not candidate.is_absolute() and
        "." not in candidate.parts and ".." not in candidate.parts and
        candidate.as_posix() == stripped, reason)
    return candidate


def _read_tar_member(
    archive: tarfile.TarFile, member: tarfile.TarInfo, reason: str,
    *, maximum: int,
) -> bytes:
    require(member.isreg() and 0 < member.size <= maximum, reason)
    stream = archive.extractfile(member)
    require(stream is not None, reason)
    payload = stream.read(maximum + 1)
    require(len(payload) == member.size and len(payload) <= maximum, reason)
    return payload


def extract_buildkit_binary(
    image_archive: Any, *, image_id: str, binary_path: str,
) -> str:
    """Hash the effective BuildKit entrypoint directly from image layers."""
    reason = "PROVENANCE_BUILDKIT_BINARY_INVALID"
    require(IMAGE_ID.fullmatch(image_id) is not None, reason)
    target = _safe_tar_path(binary_path.removeprefix("/"), reason)
    image_archive.seek(0)
    try:
        outer = tarfile.open(fileobj=image_archive, mode="r:*")
    except (tarfile.TarError, OSError) as error:
        raise ProvenanceError(reason) from error
    with outer:
        members = outer.getmembers()
        require(0 < len(members) <= 100000, reason)
        by_name: dict[str, tarfile.TarInfo] = {}
        for member in members:
            path = _safe_tar_path(member.name, reason)
            if path == PurePosixPath("."):
                continue
            name = path.as_posix()
            require(name not in by_name, reason)
            by_name[name] = member
        manifest_member = by_name.get("manifest.json")
        require(manifest_member is not None, reason)
        manifest_payload = _read_tar_member(
            outer, manifest_member, reason, maximum=MAX_JSON)
        try:
            manifests = json.loads(manifest_payload.decode("utf-8"))
        except (UnicodeError, json.JSONDecodeError) as error:
            raise ProvenanceError(reason) from error
        require(type(manifests) is list and len(manifests) == 1 and
                type(manifests[0]) is dict, reason)
        manifest = manifests[0]
        config_name = manifest.get("Config")
        layers = manifest.get("Layers")
        require(
            type(config_name) is str and
            config_name == image_id.removeprefix("sha256:") + ".json" and
            type(layers) is list and 0 < len(layers) <= 512 and
            all(type(layer) is str and layer in by_name for layer in layers),
            reason)
        selected: bytes | None = None
        for layer_name in layers:
            layer_member = by_name[layer_name]
            require(layer_member.isreg() and layer_member.size <= MAX_IMAGE_SAVE,
                    reason)
            layer_stream = outer.extractfile(layer_member)
            require(layer_stream is not None, reason)
            try:
                layer = tarfile.open(fileobj=layer_stream, mode="r|*")
                for member in layer:
                    path = _safe_tar_path(member.name, reason)
                    if path == PurePosixPath("."):
                        continue
                    parent = path.parent
                    whiteout = parent / (".wh." + target.name)
                    opaque = target.parent / ".wh..wh..opq"
                    if path == whiteout or path == opaque:
                        selected = None
                    if path == target:
                        require(member.isreg() and member.size <= MAX_LAYER_MEMBER,
                                reason)
                        stream = layer.extractfile(member)
                        require(stream is not None, reason)
                        candidate = stream.read(MAX_LAYER_MEMBER + 1)
                        require(
                            len(candidate) == member.size and
                            0 < len(candidate) <= MAX_LAYER_MEMBER, reason)
                        selected = candidate
            except (tarfile.TarError, OSError) as error:
                raise ProvenanceError(reason) from error
        require(selected is not None, reason)
        return digest_bytes(selected)


def observe_buildx_toolchain(
    executor: CommandExecutor,
) -> dict[str, Any]:
    plugins = command_json(
        executor, (str(DOCKER_CLI), "info", "--format",
                   "{{json .ClientInfo.Plugins}}"))
    server = command_json(
        executor, (str(DOCKER_CLI), "version", "--format",
                   "{{json .Server}}"))
    require(type(plugins) is list and type(server) is dict,
            "PROVENANCE_BUILDX_INVALID")
    matches = [
        item for item in plugins
        if type(item) is dict and item.get("Name") == "buildx"]
    require(len(matches) == 1, "PROVENANCE_BUILDX_INVALID")
    plugin = matches[0]
    path_value = plugin.get("Path")
    version = plugin.get("Version")
    require(
        type(path_value) is str and path_value.startswith("/") and
        type(version) is str and SEMVER.fullmatch(version) is not None,
        "PROVENANCE_BUILDX_INVALID")
    observed_sha256, binding = hash_bound_executable(
        Path(path_value), "PROVENANCE_BUILDX_BINARY_INVALID")
    result = executor.run(
        (str(DOCKER_CLI), "buildx", "version"), timeout=30)
    require(
        result.returncode == 0 and result.stderr == b"" and
        len(result.stdout) <= 4096, "PROVENANCE_BUILDX_INVALID")
    try:
        output = result.stdout.decode("utf-8", errors="strict")
    except UnicodeError as error:
        raise ProvenanceError("PROVENANCE_BUILDX_INVALID") from error
    match = re.fullmatch(
        r"github\.com/docker/buildx ([0-9]+\.[0-9]+\.[0-9]+"
        r"(?:[-+][0-9A-Za-z._-]+)?)(?: [^\r\n]+)?\n?", output)
    require(match is not None and match.group(1) == version,
            "PROVENANCE_BUILDX_INVALID")
    server_version = server.get("Version")
    server_api = server.get("ApiVersion")
    server_commit = server.get("GitCommit")
    require(
        type(server_version) is str and
        SEMVER.fullmatch(server_version) is not None and
        type(server_api) is str and
        DOCKER_API_VERSION.fullmatch(server_api) is not None and
        type(server_commit) is str and
        BUILD_ID.fullmatch(server_commit) is not None,
        "PROVENANCE_DOCKER_SERVER_INVALID")
    binding.reopen()
    return {
        "buildx_path": path_value,
        "buildx_path_sha256": digest_bytes(path_value.encode("utf-8")),
        "buildx_binary_sha256": observed_sha256,
        "buildx_version": version,
        "docker_server_version": server_version,
        "docker_server_api_version": server_api,
        "docker_server_git_commit": server_commit,
    }


def observe_builder(
    executor: CommandExecutor, reference: str,
) -> dict[str, Any]:
    record = inspect_image(executor, reference)
    image_id, repo_digests, config = validate_image_common(
        record, reference, "PROVENANCE_BUILDKIT_IMAGE_INVALID")
    entrypoint = config.get("Entrypoint")
    require(
        "OnBuild" in config and config.get("OnBuild") in (None, []) and
        config.get("Volumes") in (None, {}) and
        config.get("ExposedPorts") in (None, {}) and
        entrypoint in (["buildkitd"], ["/usr/bin/buildkitd"],
                       ["/usr/local/bin/buildkitd"]),
        "PROVENANCE_BUILDKIT_IMAGE_INVALID")
    labels = config.get("Labels") or {}
    require(
        type(labels) is dict and
        all(type(key) is str and type(value) is str
            for key, value in labels.items()) and
        not BUILDER_RESERVED_LABELS.intersection(labels),
        "PROVENANCE_BUILDKIT_IMAGE_INVALID")
    version = labels.get("org.opencontainers.image.version")
    require(
        type(version) is str and BUILDKIT_VERSION.fullmatch(version) is not None,
        "PROVENANCE_BUILDKIT_VERSION_INVALID")
    binary_path = entrypoint[0]
    if binary_path == "buildkitd":
        binary_path = "/usr/bin/buildkitd"
    require(type(executor) is ProductionExecutor,
            "PROVENANCE_FAKE_EXECUTOR_CANNOT_OBSERVE_GO")
    archive = executor.save_image(image_id)
    try:
        binary_sha256 = extract_buildkit_binary(
            archive, image_id=image_id, binary_path=binary_path)
    finally:
        archive.close()
    toolchain = observe_buildx_toolchain(executor)
    return {
        "image_id": image_id, "repo_digest": reference,
        "repo_digests": repo_digests,
        "config_sha256": canonical_object_sha256(config),
        "os": "linux", "architecture": "amd64",
        "entrypoint": list(entrypoint),
        "buildkit_binary_path": binary_path,
        "buildkit_binary_sha256": binary_sha256,
        "buildkit_version": version,
        **toolchain,
    }


def stable_readlink(path: Path, reason: str) -> str:
    try:
        before = os.lstat(path)
        target = os.readlink(path)
        after = os.lstat(path)
    except OSError as error:
        raise ProvenanceError(reason) from error
    require(
        stat.S_ISLNK(before.st_mode) and identity(before) == identity(after) and
        type(target) is str and 0 < len(target) <= 4096 and "\x00" not in target,
        reason)
    return target


def observe_apparmor(source: FileBinding) -> dict[str, Any]:
    reason = "PROVENANCE_APPARMOR_INVALID"
    source.reopen(reason)
    require(read_scalar(APPARMOR_ENABLED, reason) == "Y", reason)
    namespace = {
        "name": read_scalar(APPARMOR_ROOT / ".ns_name", reason),
        "level": read_scalar(APPARMOR_ROOT / ".ns_level", reason),
        "stacked": read_scalar(APPARMOR_ROOT / ".ns_stacked", reason),
        "legacy_stacked": read_scalar(APPARMOR_ROOT / ".stacked", reason),
    }
    require(namespace == {
        "name": "root", "level": "0", "stacked": "no",
        "legacy_stacked": "no",
    }, reason)
    profiles = APPARMOR_POLICY_ROOT / "profiles"
    raw_root = APPARMOR_POLICY_ROOT / "raw_data"
    try:
        before = os.lstat(profiles)
        entries = sorted(os.scandir(profiles), key=lambda item: item.name)
    except OSError as error:
        raise ProvenanceError(reason) from error
    require(
        stat.S_ISDIR(before.st_mode) and before.st_uid == ROOT_UID and
        before.st_gid == ROOT_GID and 0 < len(entries) <= 4096, reason)
    names: list[str] = []
    matching: list[Path] = []
    for entry in entries:
        require(POLICY_ENTRY.fullmatch(entry.name) is not None, reason)
        entry_path = profiles / entry.name
        metadata = os.lstat(entry_path)
        require(
            stat.S_ISDIR(metadata.st_mode) and metadata.st_uid == ROOT_UID and
            metadata.st_gid == ROOT_GID, reason)
        name = read_scalar(entry_path / "name", reason)
        names.append(name)
        if name == PROFILE_NAME:
            matching.append(entry_path)
    after = os.lstat(profiles)
    require(identity(before) == identity(after) and len(matching) == 1 and
            names.count(PROFILE_NAME) == 1, reason)
    entry = matching[0]
    values = {
        field: read_scalar(entry / field, reason)
        for field in ("name", "mode", "attach", "learning_count", "sha256")
    }
    require(
        values["name"] == PROFILE_NAME and values["mode"] == "enforce" and
        values["attach"] == PROFILE_NAME and
        values["learning_count"] == "0" and
        BARE_DIGEST.fullmatch(values["sha256"]) is not None, reason)
    raw_link = stable_readlink(entry / "raw_data", reason)
    raw_sha_link = stable_readlink(entry / "raw_sha256", reason)
    raw_abi_link = stable_readlink(entry / "raw_abi", reason)
    match = re.fullmatch(
        r"\.\./\.\./raw_data/([1-9][0-9]{0,19})/raw_data", raw_link)
    require(match is not None, reason)
    raw_id = match.group(1)
    require(
        raw_sha_link == f"../../raw_data/{raw_id}/sha256" and
        raw_abi_link == f"../../raw_data/{raw_id}/abi", reason)
    raw_sha = read_scalar(raw_root / raw_id / "sha256", reason)
    raw_abi = read_scalar(raw_root / raw_id / "abi", reason)
    require(
        BARE_DIGEST.fullmatch(raw_sha) is not None and
        RAW_ABI.fullmatch(raw_abi) is not None, reason)
    source.reopen(reason)
    return {
        "profile": PROFILE_NAME, "mode": "enforce", "attach": PROFILE_NAME,
        "learning_count": 0,
        "policy_source_sha256": digest_bytes(source.payload),
        "profile_sha256": "sha256:" + values["sha256"],
        "raw_sha256": "sha256:" + raw_sha, "raw_abi": raw_abi,
        "raw_data_id": raw_id, "namespace_name": "root",
        "namespace_level": 0, "namespace_stacked": False,
        "profile_inventory_sha256": digest_bytes(canonical_bytes(names)),
    }


def _proc_scalar(pid: int, name: str, reason: str) -> str:
    return read_scalar(PROC_ROOT / str(pid) / name, reason)


def parse_process_stat(pid: int, value: str, reason: str) -> int:
    prefix = f"{pid} ("
    closing = value.rfind(") ")
    require(value.startswith(prefix) and closing > len(prefix), reason)
    fields = value[closing + 2:].split()
    require(
        len(fields) >= 20 and re.fullmatch(r"[1-9][0-9]*", fields[19])
        is not None, reason)
    return int(fields[19], 10)


def discover_dockerd() -> tuple[int, int]:
    reason = "PROVENANCE_DOCKER_DAEMON_INVALID"
    try:
        entries = list(os.scandir(PROC_ROOT))
    except OSError as error:
        raise ProvenanceError(reason) from error
    matches: list[tuple[int, int]] = []
    for entry in entries:
        if not entry.name.isdigit():
            continue
        pid = int(entry.name, 10)
        if pid <= 1 or pid > 4_194_304:
            continue
        try:
            metadata = os.lstat(PROC_ROOT / entry.name)
            if (not stat.S_ISDIR(metadata.st_mode) or
                    metadata.st_uid != ROOT_UID or
                    metadata.st_gid != ROOT_GID):
                continue
            if _proc_scalar(pid, "comm", reason) != "dockerd":
                continue
            stat_value = _proc_scalar(pid, "stat", reason)
            matches.append((pid, parse_process_stat(pid, stat_value, reason)))
        except ProvenanceError:
            continue
    require(len(matches) == 1, reason)
    return matches[0]


def namespace_inode(path: Path, reason: str) -> int:
    target = stable_readlink(path, reason)
    match = re.fullmatch(r"user:\[([1-9][0-9]*)\]", target)
    require(match is not None, reason)
    return int(match.group(1), 10)


def apparmor_current(pid: int, reason: str) -> str:
    primary = PROC_ROOT / str(pid) / "attr" / "apparmor" / "current"
    legacy = PROC_ROOT / str(pid) / "attr" / "current"
    try:
        return read_scalar(primary, reason)
    except ProvenanceError:
        return read_scalar(legacy, reason)


def observe_docker_namespace(
    executor: CommandExecutor, apparmor: Mapping[str, Any],
) -> dict[str, Any]:
    reason = "PROVENANCE_DOCKER_NAMESPACE_INVALID"
    require(type(executor) is ProductionExecutor, reason)
    require(
        {key: apparmor.get(key) for key in (
            "namespace_name", "namespace_level", "namespace_stacked")} ==
        {"namespace_name": "root", "namespace_level": 0,
         "namespace_stacked": False}, reason)
    try:
        socket_before = os.lstat(DOCKER_SOCKET)
        socket_after = os.lstat(DOCKER_SOCKET)
    except OSError as error:
        raise ProvenanceError(reason) from error
    require(
        identity(socket_before) == identity(socket_after) and
        stat.S_ISSOCK(socket_before.st_mode) and socket_before.st_nlink == 1 and
        socket_before.st_uid == ROOT_UID and
        not stat.S_IMODE(socket_before.st_mode) & 0o002, reason)
    pid, start_ticks = discover_dockerd()
    process_root = PROC_ROOT / str(pid)
    boot_before = read_scalar(PROC_ROOT / "sys/kernel/random/boot_id", reason)
    require(BOOT_ID.fullmatch(boot_before) is not None, reason)
    daemon_id_result = command_json(
        executor, (str(DOCKER_CLI), "info", "--format", "{{json .ID}}"))
    require(
        type(daemon_id_result) is str and
        DAEMON_ID.fullmatch(daemon_id_result) is not None, reason)
    daemon_current = apparmor_current(pid, reason)
    require(daemon_current == "unconfined", reason)
    self_user = namespace_inode(PROC_ROOT / "self/ns/user", reason)
    daemon_user = namespace_inode(process_root / "ns/user", reason)
    require(self_user == daemon_user, reason)
    executable_target = stable_readlink(process_root / "exe", reason)
    require(executable_target.startswith("/") and not executable_target.endswith(
        " (deleted)"), reason)
    daemon_exe_sha256, daemon_exe = hash_bound_executable(
        Path(executable_target), reason)
    pid_after, ticks_after = discover_dockerd()
    boot_after = read_scalar(PROC_ROOT / "sys/kernel/random/boot_id", reason)
    require(
        (pid_after, ticks_after) == (pid, start_ticks) and
        boot_after == boot_before and apparmor_current(pid, reason) ==
        daemon_current and
        namespace_inode(process_root / "ns/user", reason) == daemon_user,
        reason)
    daemon_exe.reopen(reason)
    return {
        "docker_daemon_id": daemon_id_result,
        "docker_daemon_pid": pid,
        "docker_daemon_start_time_ticks": start_ticks,
        "docker_daemon_exe_sha256": daemon_exe_sha256,
        "host_boot_id": boot_before,
        "host_namespace_name": "root", "host_namespace_level": 0,
        "host_namespace_stacked": False,
        "daemon_namespace_name": "root", "daemon_namespace_level": 0,
        "daemon_namespace_stacked": False,
        "daemon_apparmor_current": daemon_current,
        "self_user_namespace_inode": self_user,
        "daemon_user_namespace_inode": daemon_user,
    }


class ProductionObserver:
    """Direct read-only observer.  It cannot load policy or run containers."""

    def __init__(self, context: ProductionContext) -> None:
        require(type(context) is ProductionContext,
                "PROVENANCE_PRODUCTION_CONTEXT_REQUIRED")
        self.context = context

    def observe(
        self, *, base_reference: str, buildkit_reference: str,
    ) -> dict[str, Any]:
        require(type(self.context.executor) is ProductionExecutor,
                "PROVENANCE_FAKE_EXECUTOR_CANNOT_OBSERVE_GO")
        self.context.reopen()
        base = observe_base_image(self.context.executor, base_reference)
        self.context.reopen()
        builder = observe_builder(self.context.executor, buildkit_reference)
        self.context.reopen()
        apparmor = observe_apparmor(self.context.apparmor_source)
        self.context.reopen()
        docker_namespace = observe_docker_namespace(
            self.context.executor, apparmor)
        self.context.reopen()
        observations = {
            "base_image": base, "isolated_builder": builder,
            "apparmor": apparmor, "docker_namespace": docker_namespace,
        }
        validate_observations(observations)
        return observations


def _exact_object(value: Any, fields: frozenset[str], reason: str) -> dict[str, Any]:
    require(type(value) is dict and set(value) == fields, reason)
    return value


def _false_authority(value: Mapping[str, Any], reason: str) -> None:
    require(all(value.get(field) is False for field in AUTHORITY_FIELDS), reason)


def validate_reference(value: Any, reason: str) -> dict[str, str]:
    value = _exact_object(value, REFERENCE_FIELDS, reason)
    path = value.get("path")
    digest = value.get("sha256")
    require(
        type(path) is str and Path(path).is_absolute() and
        os.path.normpath(path) == path and type(digest) is str and
        DIGEST.fullmatch(digest) is not None, reason)
    return {"path": path, "sha256": digest}


def validate_trust_bindings(value: Any) -> dict[str, dict[str, str]]:
    reason = "PROVENANCE_TRUST_BINDINGS_INVALID"
    value = _exact_object(value, TRUST_BINDING_FIELDS, reason)
    result = {key: validate_reference(value[key], reason) for key in value}
    require(
        result["producer"]["path"] == str(INSTALLED_EXECUTABLE) and
        result["docker_cli"]["path"] == str(DOCKER_CLI) and
        result["signature_verifier"]["path"] == str(OPENSSL) and
        result["verification_key"]["path"] == str(VERIFICATION_KEY) and
        result["apparmor_policy_source"]["path"] ==
            str(APPARMOR_POLICY_SOURCE), reason)
    return result


def validate_observations(value: Any) -> dict[str, Any]:
    reason = "PROVENANCE_OBSERVATION_INVALID"
    observations = _exact_object(value, OBSERVATION_TOP_FIELDS, reason)
    base = _exact_object(
        observations["base_image"], BASE_OBSERVATION_FIELDS, reason)
    require(
        type(base["image_id"]) is str and
        IMAGE_ID.fullmatch(base["image_id"]) is not None and
        type(base["repo_digest"]) is str and
        PINNED_IMAGE.fullmatch(base["repo_digest"]) is not None and
        type(base["repo_digests"]) is list and
        base["repo_digests"] == sorted(base["repo_digests"]) and
        len(base["repo_digests"]) == len(set(base["repo_digests"])) and
        base["repo_digest"] in base["repo_digests"] and
        all(type(item) is str and PINNED_IMAGE.fullmatch(item) is not None
            for item in base["repo_digests"]) and
        base["labels_sha256"] == canonical_object_sha256(BASE_LABELS) and
        base["os"] == "linux" and base["architecture"] == "amd64" and
        base["declared_volumes"] == 0 and
        base["onbuild_instructions"] == 0, reason)

    builder = _exact_object(
        observations["isolated_builder"], BUILDER_OBSERVATION_FIELDS, reason)
    require(
        type(builder["image_id"]) is str and
        IMAGE_ID.fullmatch(builder["image_id"]) is not None and
        type(builder["repo_digest"]) is str and
        PINNED_IMAGE.fullmatch(builder["repo_digest"]) is not None and
        type(builder["repo_digests"]) is list and
        builder["repo_digests"] == sorted(builder["repo_digests"]) and
        len(builder["repo_digests"]) == len(set(builder["repo_digests"])) and
        builder["repo_digest"] in builder["repo_digests"] and
        all(type(item) is str and PINNED_IMAGE.fullmatch(item) is not None
            for item in builder["repo_digests"]) and
        all(type(builder[field]) is str and
            DIGEST.fullmatch(builder[field]) is not None
            for field in ("config_sha256", "buildkit_binary_sha256",
                          "buildx_path_sha256", "buildx_binary_sha256")) and
        builder["os"] == "linux" and builder["architecture"] == "amd64" and
        builder["entrypoint"] in (["buildkitd"], ["/usr/bin/buildkitd"],
                                  ["/usr/local/bin/buildkitd"]) and
        builder["buildkit_binary_path"] in
            ("/usr/bin/buildkitd", "/usr/local/bin/buildkitd") and
        type(builder["buildkit_version"]) is str and
        BUILDKIT_VERSION.fullmatch(builder["buildkit_version"]) is not None and
        type(builder["buildx_path"]) is str and
        builder["buildx_path"].startswith("/") and
        digest_bytes(builder["buildx_path"].encode("utf-8")) ==
            builder["buildx_path_sha256"] and
        type(builder["buildx_version"]) is str and
        SEMVER.fullmatch(builder["buildx_version"]) is not None and
        type(builder["docker_server_version"]) is str and
        SEMVER.fullmatch(builder["docker_server_version"]) is not None and
        type(builder["docker_server_api_version"]) is str and
        DOCKER_API_VERSION.fullmatch(
            builder["docker_server_api_version"]) is not None and
        type(builder["docker_server_git_commit"]) is str and
        BUILD_ID.fullmatch(builder["docker_server_git_commit"]) is not None,
        reason)

    apparmor = _exact_object(
        observations["apparmor"], APPARMOR_OBSERVATION_FIELDS, reason)
    require(
        apparmor["profile"] == PROFILE_NAME and
        apparmor["mode"] == "enforce" and
        apparmor["attach"] == PROFILE_NAME and
        apparmor["learning_count"] == 0 and
        all(type(apparmor[field]) is str and
            DIGEST.fullmatch(apparmor[field]) is not None
            for field in ("policy_source_sha256", "profile_sha256",
                          "raw_sha256", "profile_inventory_sha256")) and
        type(apparmor["raw_abi"]) is str and
        RAW_ABI.fullmatch(apparmor["raw_abi"]) is not None and
        type(apparmor["raw_data_id"]) is str and
        re.fullmatch(r"[1-9][0-9]{0,19}", apparmor["raw_data_id"]) is not None
        and apparmor["namespace_name"] == "root" and
        apparmor["namespace_level"] == 0 and
        apparmor["namespace_stacked"] is False, reason)

    docker = _exact_object(
        observations["docker_namespace"], DOCKER_OBSERVATION_FIELDS, reason)
    require(
        type(docker["docker_daemon_id"]) is str and
        DAEMON_ID.fullmatch(docker["docker_daemon_id"]) is not None and
        type(docker["docker_daemon_pid"]) is int and
        1 < docker["docker_daemon_pid"] <= 4_194_304 and
        type(docker["docker_daemon_start_time_ticks"]) is int and
        docker["docker_daemon_start_time_ticks"] > 0 and
        type(docker["docker_daemon_exe_sha256"]) is str and
        DIGEST.fullmatch(docker["docker_daemon_exe_sha256"]) is not None and
        type(docker["host_boot_id"]) is str and
        BOOT_ID.fullmatch(docker["host_boot_id"]) is not None and
        docker["host_namespace_name"] == "root" and
        docker["host_namespace_level"] == 0 and
        docker["host_namespace_stacked"] is False and
        docker["daemon_namespace_name"] == "root" and
        docker["daemon_namespace_level"] == 0 and
        docker["daemon_namespace_stacked"] is False and
        docker["daemon_apparmor_current"] == "unconfined" and
        type(docker["self_user_namespace_inode"]) is int and
        docker["self_user_namespace_inode"] > 0 and
        docker["daemon_user_namespace_inode"] ==
            docker["self_user_namespace_inode"], reason)
    return observations


def request_digest(document_without_digest: Mapping[str, Any]) -> str:
    return digest_bytes(canonical_bytes(document_without_digest))


def build_request(
    *, observations: dict[str, Any], trust_bindings: dict[str, Any],
    base_reference: str, buildkit_reference: str, observation_mode: str,
    now_ms: int, nonce: str,
) -> dict[str, Any]:
    reason = "PROVENANCE_REQUEST_INVALID"
    validate_observations(observations)
    validate_trust_bindings(trust_bindings)
    base_reference = require_pinned_image(base_reference, reason)
    buildkit_reference = require_pinned_image(buildkit_reference, reason)
    require(
        observations["base_image"]["repo_digest"] == base_reference and
        observations["isolated_builder"]["repo_digest"] ==
            buildkit_reference and type(now_ms) is int and now_ms >= 0 and
        type(nonce) is str and NONCE.fullmatch(nonce) is not None and
        observation_mode in
            (PRODUCTION_OBSERVATION_MODE, OFFLINE_OBSERVATION_MODE), reason)
    eligible = observation_mode == PRODUCTION_OBSERVATION_MODE
    body = {
        "schema": REQUEST_SCHEMA, "version": VERSION,
        "status": "REVIEW_REQUIRED" if eligible else "NO_GO",
        "observation_mode": observation_mode, "observed_at_ms": now_ms,
        "expires_at_ms": now_ms + MAX_REQUEST_LIFETIME_MS, "nonce": nonce,
        "base_image_reference": base_reference,
        "buildkit_image_reference": buildkit_reference,
        "observations": observations, "trust_bindings": trust_bindings,
        "go_eligible": eligible, **FALSE_AUTHORITY,
    }
    result = {**body, "request_sha256": request_digest(body)}
    validate_request(result, now_ms=now_ms)
    return result


def validate_request(
    document: Any, *, now_ms: int, require_production: bool = False,
) -> dict[str, Any]:
    reason = "PROVENANCE_REQUEST_INVALID"
    document = _exact_object(document, REQUEST_FIELDS, reason)
    require(document["schema"] == REQUEST_SCHEMA and
            document["version"] == VERSION, reason)
    _false_authority(document, reason)
    mode = document["observation_mode"]
    require(mode in (PRODUCTION_OBSERVATION_MODE, OFFLINE_OBSERVATION_MODE),
            reason)
    eligible = mode == PRODUCTION_OBSERVATION_MODE
    require(
        document["status"] == ("REVIEW_REQUIRED" if eligible else "NO_GO") and
        document["go_eligible"] is eligible and
        (not require_production or eligible), reason)
    observed = document["observed_at_ms"]
    expires = document["expires_at_ms"]
    require(
        type(now_ms) is int and now_ms >= 0 and type(observed) is int and
        type(expires) is int and observed >= 0 and
        observed <= now_ms + MAX_CLOCK_SKEW_MS and expires > observed and
        expires - observed <= MAX_REQUEST_LIFETIME_MS and now_ms < expires and
        type(document["nonce"]) is str and
        NONCE.fullmatch(document["nonce"]) is not None and
        type(document["request_sha256"]) is str and
        DIGEST.fullmatch(document["request_sha256"]) is not None, reason)
    base = require_pinned_image(document["base_image_reference"], reason)
    builder = require_pinned_image(document["buildkit_image_reference"], reason)
    observations = validate_observations(document["observations"])
    validate_trust_bindings(document["trust_bindings"])
    require(
        observations["base_image"]["repo_digest"] == base and
        observations["isolated_builder"]["repo_digest"] == builder, reason)
    unsigned = dict(document)
    claimed = unsigned.pop("request_sha256")
    require(claimed == request_digest(unsigned), reason)
    return document


def validate_offline_document(document: Any) -> dict[str, Any]:
    reason = "PROVENANCE_OFFLINE_OBSERVATION_INVALID"
    document = _exact_object(document, OFFLINE_FIELDS, reason)
    require(document["schema"] == OFFLINE_OBSERVATION_SCHEMA and
            document["version"] == VERSION, reason)
    _false_authority(document, reason)
    base = require_pinned_image(document["base_image_reference"], reason)
    builder = require_pinned_image(document["buildkit_image_reference"], reason)
    observations = validate_observations(document["observations"])
    validate_trust_bindings(document["trust_bindings"])
    require(observations["base_image"]["repo_digest"] == base and
            observations["isolated_builder"]["repo_digest"] == builder,
            reason)
    return document


@dataclass(frozen=True)
class SignedAuthorization:
    binding: FileBinding
    payload: dict[str, Any]
    payload_bytes: bytes
    signature: bytes


def bind_json_document(
    path: Path, reason: str, *, modes: frozenset[int],
) -> FileBinding:
    return bind_file(
        path, reason, expected_uid=ROOT_UID, expected_gid=ROOT_GID,
        modes=modes, maximum=MAX_JSON, parse_document=True)


def parse_authorization(binding: FileBinding) -> SignedAuthorization:
    reason = "PROVENANCE_AUTHORIZATION_INVALID"
    envelope = binding.document
    require(type(envelope) is dict and
            set(envelope) == AUTHORIZATION_ENVELOPE_FIELDS and
            envelope["schema"] == AUTHORIZATION_ENVELOPE_SCHEMA and
            envelope["version"] == VERSION and
            type(envelope["payload"]) is dict and
            type(envelope["signature_base64"]) is str, reason)
    payload = envelope["payload"]
    require(set(payload) == AUTHORIZATION_PAYLOAD_FIELDS, reason)
    try:
        signature = base64.b64decode(
            envelope["signature_base64"].encode("ascii"), validate=True)
    except (UnicodeError, ValueError, base64.binascii.Error) as error:
        raise ProvenanceError(reason) from error
    require(
        len(signature) == 64 and
        base64.b64encode(signature).decode("ascii") ==
            envelope["signature_base64"], reason)
    return SignedAuthorization(
        binding, payload, canonical_bytes(payload), signature)


def validate_authorization_payload(
    payload: Any, *, request: Mapping[str, Any], now_ms: int,
) -> dict[str, Any]:
    reason = "PROVENANCE_AUTHORIZATION_INVALID"
    payload = _exact_object(payload, AUTHORIZATION_PAYLOAD_FIELDS, reason)
    _false_authority(payload, reason)
    issued = payload["issued_at_ms"]
    expires = payload["expires_at_ms"]
    require(
        payload["schema"] == AUTHORIZATION_PAYLOAD_SCHEMA and
        payload["version"] == VERSION and payload["decision"] == "GO" and
        payload["review_authority"] == REVIEW_AUTHORITY and
        type(payload["reviewer_id"]) is str and
        REVIEWER_ID.fullmatch(payload["reviewer_id"]) is not None and
        type(issued) is int and type(expires) is int and
        issued >= request["observed_at_ms"] and
        issued <= now_ms + MAX_CLOCK_SKEW_MS and expires > issued and
        expires - issued <= MAX_AUTHORIZATION_LIFETIME_MS and
        expires <= request["expires_at_ms"] and now_ms < expires and
        payload["nonce"] == request["nonce"] and
        payload["request_sha256"] == request["request_sha256"] and
        payload["base_image_reference"] == request["base_image_reference"] and
        payload["buildkit_image_reference"] ==
            request["buildkit_image_reference"] and
        payload["observations"] == request["observations"] and
        payload["trust_bindings"] == request["trust_bindings"], reason)
    validate_observations(payload["observations"])
    validate_trust_bindings(payload["trust_bindings"])
    return payload


def assemble_go_documents(
    *, observations: Mapping[str, Any], issued_at_ms: int,
    expires_at_ms: int,
) -> dict[str, dict[str, Any]]:
    reason = "PROVENANCE_GO_DOCUMENT_INVALID"
    validate_observations(observations)
    require(
        type(issued_at_ms) is int and issued_at_ms >= 0 and
        type(expires_at_ms) is int and expires_at_ms > issued_at_ms and
        expires_at_ms - issued_at_ms <= 24 * 60 * 60 * 1000, reason)
    base = observations["base_image"]
    builder = observations["isolated_builder"]
    apparmor = observations["apparmor"]
    docker = observations["docker_namespace"]
    freshness = {
        "decision": "GO", "issued_at_ms": issued_at_ms,
        "expires_at_ms": expires_at_ms,
    }
    documents = {
        "base": {
            "schema": BASE_SCHEMA, **freshness,
            "image_id": base["image_id"],
            "repo_digest": base["repo_digest"],
            "labels_sha256": base["labels_sha256"],
        },
        "builder": {
            "schema": BUILDER_SCHEMA, **freshness,
            "image_id": builder["image_id"],
            "repo_digest": builder["repo_digest"],
            "config_sha256": builder["config_sha256"],
            "buildkit_version": builder["buildkit_version"],
            "buildx_version": builder["buildx_version"],
            "buildx_binary_sha256": builder["buildx_binary_sha256"],
            "docker_server_version": builder["docker_server_version"],
            "docker_server_api_version":
                builder["docker_server_api_version"],
            "docker_server_git_commit":
                builder["docker_server_git_commit"],
        },
        "apparmor": {
            "schema": APPARMOR_SCHEMA, **freshness,
            "profile": apparmor["profile"],
            "policy_source_sha256": apparmor["policy_source_sha256"],
            "profile_sha256": apparmor["profile_sha256"],
            "raw_sha256": apparmor["raw_sha256"],
            "raw_abi": apparmor["raw_abi"],
        },
        "docker_namespace": {
            "schema": DOCKER_NAMESPACE_SCHEMA, **freshness,
            "docker_daemon_id": docker["docker_daemon_id"],
            "docker_daemon_pid": docker["docker_daemon_pid"],
            "docker_daemon_start_time_ticks":
                docker["docker_daemon_start_time_ticks"],
            "host_boot_id": docker["host_boot_id"],
            "host_namespace_name": docker["host_namespace_name"],
            "host_namespace_level": docker["host_namespace_level"],
            "host_namespace_stacked": docker["host_namespace_stacked"],
            "daemon_namespace_name": docker["daemon_namespace_name"],
            "daemon_namespace_level": docker["daemon_namespace_level"],
            "daemon_namespace_stacked":
                docker["daemon_namespace_stacked"],
        },
    }
    require(
        set(documents["base"]) == BASE_KEYS and
        set(documents["builder"]) == BUILDER_KEYS and
        set(documents["apparmor"]) == APPARMOR_KEYS and
        set(documents["docker_namespace"]) == DOCKER_NAMESPACE_KEYS, reason)
    return documents


def assemble_review_closure(
    *, request_binding: FileBinding, authorization: SignedAuthorization,
    trust_bindings: Mapping[str, Any], output_directory: Path,
    documents: Mapping[str, dict[str, Any]], issued_at_ms: int,
    expires_at_ms: int,
) -> dict[str, Any]:
    reason = "PROVENANCE_REVIEW_CLOSURE_INVALID"
    request = request_binding.document
    require(type(request) is dict and set(documents) == set(OUTPUT_FILENAMES),
            reason)
    validate_request(request, now_ms=issued_at_ms, require_production=True)
    validate_authorization_payload(
        authorization.payload, request=request, now_ms=issued_at_ms)
    validate_trust_bindings(trust_bindings)
    require(
        trust_bindings == request["trust_bindings"] ==
            authorization.payload["trust_bindings"] and
        issued_at_ms >= authorization.payload["issued_at_ms"] and
        issued_at_ms >= request["observed_at_ms"] and
        expires_at_ms == authorization.payload["expires_at_ms"], reason)
    output_directory = canonical_path(output_directory, reason)
    output_references = {
        key: {
            "path": str(output_directory / OUTPUT_FILENAMES[key]),
            "file_sha256": digest_bytes(canonical_bytes(documents[key])),
            "schema": documents[key]["schema"],
        }
        for key in ("base", "builder", "apparmor", "docker_namespace")
    }
    body = {
        "schema": REVIEW_CLOSURE_SCHEMA, "version": VERSION,
        "status": "EXTERNALLY_REVIEWED_GO_CLOSED",
        "issued_at_ms": issued_at_ms, "expires_at_ms": expires_at_ms,
        "base_image_reference": request["base_image_reference"],
        "buildkit_image_reference": request["buildkit_image_reference"],
        "review_authority": authorization.payload["review_authority"],
        "reviewer_id": authorization.payload["reviewer_id"],
        "request_reference": {
            "path": str(request_binding.path),
            "file_sha256": digest_bytes(request_binding.payload),
            "request_sha256": request["request_sha256"],
            "nonce": request["nonce"],
        },
        "authorization_reference": {
            "path": str(authorization.binding.path),
            "file_sha256": digest_bytes(authorization.binding.payload),
            "signed_payload_sha256":
                digest_bytes(authorization.payload_bytes),
            "signature_sha256": digest_bytes(authorization.signature),
        },
        "producer": trust_bindings["producer"],
        "trust_bindings": trust_bindings,
        "outputs": output_references,
        **FALSE_AUTHORITY,
    }
    result = {**body, "closure_sha256": digest_bytes(canonical_bytes(body))}
    validate_review_closure(result, now_ms=issued_at_ms)
    return result


def validate_review_closure(
    document: Any, *, now_ms: int,
) -> dict[str, Any]:
    reason = "PROVENANCE_REVIEW_CLOSURE_INVALID"
    document = _exact_object(document, REVIEW_CLOSURE_FIELDS, reason)
    _false_authority(document, reason)
    issued = document["issued_at_ms"]
    expires = document["expires_at_ms"]
    require(
        document["schema"] == REVIEW_CLOSURE_SCHEMA and
        document["version"] == VERSION and
        document["status"] == "EXTERNALLY_REVIEWED_GO_CLOSED" and
        type(issued) is int and issued >= 0 and type(expires) is int and
        issued <= now_ms + MAX_CLOCK_SKEW_MS and expires > issued and
        expires - issued <=
            MAX_AUTHORIZATION_LIFETIME_MS and now_ms < expires and
        document["review_authority"] == REVIEW_AUTHORITY and
        type(document["reviewer_id"]) is str and
        REVIEWER_ID.fullmatch(document["reviewer_id"]) is not None and
        type(document["base_image_reference"]) is str and
        PINNED_IMAGE.fullmatch(document["base_image_reference"]) is not None and
        type(document["buildkit_image_reference"]) is str and
        PINNED_IMAGE.fullmatch(
            document["buildkit_image_reference"]) is not None, reason)
    request_reference = _exact_object(
        document["request_reference"], REQUEST_REFERENCE_FIELDS, reason)
    authorization_reference = _exact_object(
        document["authorization_reference"],
        AUTHORIZATION_REFERENCE_FIELDS, reason)
    for reference in (request_reference, authorization_reference):
        require(
            type(reference["path"]) is str and
            Path(reference["path"]).is_absolute() and
            os.path.normpath(reference["path"]) == reference["path"] and
            type(reference["file_sha256"]) is str and
            DIGEST.fullmatch(reference["file_sha256"]) is not None, reason)
    require(
        type(request_reference["request_sha256"]) is str and
        DIGEST.fullmatch(request_reference["request_sha256"]) is not None and
        type(request_reference["nonce"]) is str and
        NONCE.fullmatch(request_reference["nonce"]) is not None and
        type(authorization_reference["signed_payload_sha256"]) is str and
        DIGEST.fullmatch(
            authorization_reference["signed_payload_sha256"]) is not None and
        type(authorization_reference["signature_sha256"]) is str and
        DIGEST.fullmatch(
            authorization_reference["signature_sha256"]) is not None, reason)
    producer = validate_reference(document["producer"], reason)
    trust = validate_trust_bindings(document["trust_bindings"])
    require(producer == trust["producer"], reason)
    outputs = _exact_object(
        document["outputs"], frozenset(OUTPUT_FILENAMES), reason)
    schemas = {
        "base": BASE_SCHEMA, "builder": BUILDER_SCHEMA,
        "apparmor": APPARMOR_SCHEMA,
        "docker_namespace": DOCKER_NAMESPACE_SCHEMA,
    }
    for key, reference in outputs.items():
        reference = _exact_object(reference, OUTPUT_REFERENCE_FIELDS, reason)
        require(
            type(reference["path"]) is str and
            Path(reference["path"]).is_absolute() and
            os.path.normpath(reference["path"]) == reference["path"] and
            Path(reference["path"]).name == OUTPUT_FILENAMES[key] and
            type(reference["file_sha256"]) is str and
            DIGEST.fullmatch(reference["file_sha256"]) is not None and
            reference["schema"] == schemas[key], reason)
    claimed = document["closure_sha256"]
    require(type(claimed) is str and DIGEST.fullmatch(claimed) is not None,
            reason)
    body = dict(document)
    del body["closure_sha256"]
    require(claimed == digest_bytes(canonical_bytes(body)), reason)
    return document


def rename_noreplace(parent: int, source: str, destination: str) -> None:
    reason = "PROVENANCE_OUTPUT_RENAME_FAILED"
    function = getattr(LIBC, "renameat2", None)
    require(function is not None, reason)
    function.argtypes = (
        ctypes.c_int, ctypes.c_char_p, ctypes.c_int, ctypes.c_char_p,
        ctypes.c_uint)
    function.restype = ctypes.c_int
    ctypes.set_errno(0)
    result = function(
        parent, os.fsencode(source), parent, os.fsencode(destination),
        RENAME_NOREPLACE)
    if result != 0:
        number = ctypes.get_errno()
        if number == errno.EEXIST:
            raise ProvenanceError("PROVENANCE_OUTPUT_ALREADY_EXISTS")
        raise ProvenanceError(reason) from OSError(number, os.strerror(number))


def publish_one(
    path: Path, document: dict[str, Any], *, final_mode: int,
) -> None:
    reason = "PROVENANCE_OUTPUT_PUBLISH_FAILED"
    path = canonical_path(path, reason)
    require(final_mode in (0o400, 0o600), reason)
    payload = canonical_bytes(document)
    require(0 < len(payload) <= MAX_JSON, reason)
    parent = open_directory(path.parent, reason)
    temporary = "." + path.name + ".hepta-provenance-" + secrets.token_hex(16)
    descriptor = -1
    renamed = False
    try:
        parent_identity = trusted_directory(
            parent, reason, expected_uid=ROOT_UID, expected_gid=ROOT_GID)
        try:
            os.stat(path.name, dir_fd=parent, follow_symlinks=False)
        except FileNotFoundError:
            pass
        else:
            raise ProvenanceError("PROVENANCE_OUTPUT_ALREADY_EXISTS")
        descriptor = os.open(temporary, CREATE_FLAGS, 0o600, dir_fd=parent)
        os.fchmod(descriptor, 0o600)
        os.fchown(descriptor, ROOT_UID, ROOT_GID)
        offset = 0
        while offset < len(payload):
            count = os.write(descriptor, payload[offset:])
            require(count > 0, reason)
            offset += count
        # Provenance consumers require immutable-to-group root:root 0400.
        os.fchmod(descriptor, final_mode)
        os.fsync(descriptor)
        metadata = os.fstat(descriptor)
        require(
            stat.S_ISREG(metadata.st_mode) and metadata.st_nlink == 1 and
            metadata.st_uid == ROOT_UID and metadata.st_gid == ROOT_GID and
            stat.S_IMODE(metadata.st_mode) == final_mode and
            metadata.st_size == len(payload), reason)
        os.fsync(parent)
        require(parent_identity == trusted_directory(
            parent, reason, expected_uid=ROOT_UID, expected_gid=ROOT_GID),
            reason)
        rename_noreplace(parent, temporary, path.name)
        renamed = True
        os.fsync(parent)
    except ProvenanceError:
        raise
    except OSError as error:
        raise ProvenanceError(reason) from error
    finally:
        if descriptor >= 0:
            os.close(descriptor)
        if not renamed:
            try:
                os.unlink(temporary, dir_fd=parent)
                os.fsync(parent)
            except OSError:
                pass
        os.close(parent)
    committed, _metadata, _parent = secure_read(
        path, "PROVENANCE_OUTPUT_POST_VERIFY_FAILED",
        expected_uid=ROOT_UID, expected_gid=ROOT_GID,
        modes=frozenset({final_mode}), maximum=MAX_JSON)
    require(committed == payload and
            strict_object(committed, "PROVENANCE_OUTPUT_POST_VERIFY_FAILED") ==
            document, "PROVENANCE_OUTPUT_POST_VERIFY_FAILED")


def validate_output_directory(path: Path) -> tuple[int, tuple[int, ...]]:
    reason = "PROVENANCE_OUTPUT_DIRECTORY_INVALID"
    path = canonical_path(path, reason)
    descriptor = open_directory(path, reason)
    try:
        expected = trusted_directory(
            descriptor, reason, expected_uid=ROOT_UID, expected_gid=ROOT_GID,
            exact_mode=0o700)
    except BaseException:
        os.close(descriptor)
        raise
    return descriptor, expected


def publish_go_documents(
    output_directory: Path, documents: Mapping[str, dict[str, Any]], *,
    reopen_hook: Any = None,
) -> dict[str, str]:
    reason = "PROVENANCE_OUTPUT_DIRECTORY_INVALID"
    require(set(documents) == set(OUTPUT_FILENAMES), reason)
    descriptor, directory_before = validate_output_directory(output_directory)
    try:
        for filename in OUTPUT_FILENAMES.values():
            try:
                os.stat(filename, dir_fd=descriptor, follow_symlinks=False)
            except FileNotFoundError:
                continue
            raise ProvenanceError("PROVENANCE_OUTPUT_ALREADY_EXISTS")
    finally:
        os.close(descriptor)
    digests: dict[str, str] = {}
    for key in ("base", "builder", "apparmor", "docker_namespace"):
        if reopen_hook is not None:
            reopen_hook()
        path = output_directory / OUTPUT_FILENAMES[key]
        publish_one(path, documents[key], final_mode=0o400)
        digests[key] = digest_bytes(canonical_bytes(documents[key]))
        if reopen_hook is not None:
            reopen_hook()
    descriptor, directory_after = validate_output_directory(output_directory)
    os.close(descriptor)
    require(directory_after == directory_before, reason)
    for key, filename in OUTPUT_FILENAMES.items():
        payload, _metadata, _parent = secure_read(
            output_directory / filename,
            "PROVENANCE_OUTPUT_POST_VERIFY_FAILED", expected_uid=ROOT_UID,
            expected_gid=ROOT_GID, modes=frozenset({0o400}), maximum=MAX_JSON)
        require(payload == canonical_bytes(documents[key]) and
                digest_bytes(payload) == digests[key],
                "PROVENANCE_OUTPUT_POST_VERIFY_FAILED")
    return digests


def publish_reviewed_bundle(
    output_directory: Path, documents: Mapping[str, dict[str, Any]],
    closure: dict[str, Any], *, reopen_hook: Any,
) -> dict[str, str]:
    reason = "PROVENANCE_OUTPUT_DIRECTORY_INVALID"
    validate_review_closure(
        closure, now_ms=time.time_ns() // 1_000_000)
    descriptor, _identity = validate_output_directory(output_directory)
    try:
        for filename in (*OUTPUT_FILENAMES.values(), REVIEW_CLOSURE_FILENAME):
            try:
                os.stat(filename, dir_fd=descriptor, follow_symlinks=False)
            except FileNotFoundError:
                continue
            raise ProvenanceError("PROVENANCE_OUTPUT_ALREADY_EXISTS")
    finally:
        os.close(descriptor)
    digests = publish_go_documents(
        output_directory, documents, reopen_hook=reopen_hook)
    reopen_hook()
    closure_path = output_directory / REVIEW_CLOSURE_FILENAME
    publish_one(closure_path, closure, final_mode=0o400)
    reopen_hook()
    payload, _metadata, _parent = secure_read(
        closure_path, "PROVENANCE_OUTPUT_POST_VERIFY_FAILED",
        expected_uid=ROOT_UID, expected_gid=ROOT_GID,
        modes=frozenset({0o400}), maximum=MAX_JSON)
    require(
        payload == canonical_bytes(closure) and
        strict_object(payload, "PROVENANCE_OUTPUT_POST_VERIFY_FAILED") ==
            closure, "PROVENANCE_OUTPUT_POST_VERIFY_FAILED")
    digests["review_closure"] = digest_bytes(payload)
    return digests


def verify_review_closure(
    *, closure_path: Path, request_path: Path, authorization_path: Path,
    output_directory: Path, base_reference: str, buildkit_reference: str,
    _run_token: object | None = None,
) -> dict[str, Any]:
    """Independently verify signature closure and recompute all four v1 docs."""
    require(_run_token is CLI_RUN_TOKEN, "PROVENANCE_EXPLICIT_RUN_REQUIRED")
    context = ProductionContext()
    closure_binding = bind_json_document(
        closure_path, "PROVENANCE_REVIEW_CLOSURE_INVALID",
        modes=frozenset({0o400}))
    now_ms = time.time_ns() // 1_000_000
    closure = validate_review_closure(
        closure_binding.document, now_ms=now_ms)
    request_path = canonical_path(
        request_path, "PROVENANCE_REVIEW_CLOSURE_INVALID")
    authorization_path = canonical_path(
        authorization_path, "PROVENANCE_REVIEW_CLOSURE_INVALID")
    output_directory = canonical_path(
        output_directory, "PROVENANCE_REVIEW_CLOSURE_INVALID")
    require(
        closure["request_reference"]["path"] == str(request_path) and
        closure["authorization_reference"]["path"] ==
            str(authorization_path) and
        closure["base_image_reference"] == base_reference and
        closure["buildkit_image_reference"] == buildkit_reference and
        closure["trust_bindings"] == context.trust_bindings and
        closure["producer"] == context.trust_bindings["producer"] and
        all(closure["outputs"][key]["path"] == str(
                output_directory / OUTPUT_FILENAMES[key])
            for key in OUTPUT_FILENAMES),
        "PROVENANCE_REVIEW_CLOSURE_INVALID")
    request_binding = bind_json_document(
        request_path, "PROVENANCE_REQUEST_INVALID",
        modes=frozenset({0o400, 0o600}))
    authorization_binding = bind_json_document(
        authorization_path, "PROVENANCE_AUTHORIZATION_INVALID",
        modes=frozenset({0o400, 0o600}))
    authorization = parse_authorization(authorization_binding)
    request = validate_request(
        request_binding.document, now_ms=now_ms, require_production=True)
    validate_authorization_payload(
        authorization.payload, request=request, now_ms=now_ms)
    require(
        digest_bytes(request_binding.payload) ==
            closure["request_reference"]["file_sha256"] and
        request["request_sha256"] ==
            closure["request_reference"]["request_sha256"] and
        request["nonce"] == closure["request_reference"]["nonce"] and
        digest_bytes(authorization.binding.payload) ==
            closure["authorization_reference"]["file_sha256"] and
        digest_bytes(authorization.payload_bytes) ==
            closure["authorization_reference"]["signed_payload_sha256"] and
        digest_bytes(authorization.signature) ==
            closure["authorization_reference"]["signature_sha256"] and
        closure["review_authority"] ==
            authorization.payload["review_authority"] and
        closure["reviewer_id"] == authorization.payload["reviewer_id"],
        "PROVENANCE_REVIEW_CLOSURE_INVALID")
    require(
        closure["issued_at_ms"] >= authorization.payload["issued_at_ms"] and
        closure["issued_at_ms"] >= request["observed_at_ms"] and
        closure["expires_at_ms"] == authorization.payload["expires_at_ms"],
        "PROVENANCE_REVIEW_CLOSURE_INVALID")
    certification = context.verify_signature(
        authorization.payload_bytes, authorization.signature)
    require(context.certifies(
        certification, authorization.payload_bytes, authorization.signature),
        "PROVENANCE_AUTHORIZATION_SIGNATURE_INVALID")
    expected = assemble_go_documents(
        observations=authorization.payload["observations"],
        issued_at_ms=closure["issued_at_ms"],
        expires_at_ms=closure["expires_at_ms"])
    output_bindings: list[FileBinding] = []
    for key, filename in OUTPUT_FILENAMES.items():
        binding = bind_json_document(
            output_directory / filename, "PROVENANCE_GO_DOCUMENT_INVALID",
            modes=frozenset({0o400}))
        output_bindings.append(binding)
        require(
            binding.document == expected[key] and
            digest_bytes(binding.payload) ==
                closure["outputs"][key]["file_sha256"] and
            closure["outputs"][key]["schema"] == expected[key]["schema"],
            "PROVENANCE_GO_DOCUMENT_INVALID")
    request_binding.reopen()
    authorization.binding.reopen()
    closure_binding.reopen()
    for binding in output_bindings:
        binding.reopen()
    context.reopen()
    final_certification = context.verify_signature(
        authorization.payload_bytes, authorization.signature)
    require(context.certifies(
        final_certification, authorization.payload_bytes,
        authorization.signature),
        "PROVENANCE_AUTHORIZATION_SIGNATURE_INVALID")
    return {
        "status": closure["status"],
        "closure_sha256": closure["closure_sha256"],
        "reviewer_id": closure["reviewer_id"],
        "output_file_sha256s": {
            key: closure["outputs"][key]["file_sha256"]
            for key in OUTPUT_FILENAMES
        },
        **FALSE_AUTHORITY,
    }


def issue_review_request(
    *, base_reference: str, buildkit_reference: str, output: Path,
    _run_token: object | None = None,
) -> dict[str, Any]:
    require(_run_token is CLI_RUN_TOKEN, "PROVENANCE_EXPLICIT_RUN_REQUIRED")
    context = ProductionContext()
    observer = ProductionObserver(context)
    require(type(observer) is ProductionObserver and
            type(context.executor) is ProductionExecutor,
            "PROVENANCE_FAKE_EXECUTOR_CANNOT_OBSERVE_GO")
    observations = observer.observe(
        base_reference=base_reference, buildkit_reference=buildkit_reference)
    context.reopen()
    now_ms = time.time_ns() // 1_000_000
    request = build_request(
        observations=observations, trust_bindings=context.trust_bindings,
        base_reference=base_reference, buildkit_reference=buildkit_reference,
        observation_mode=PRODUCTION_OBSERVATION_MODE, now_ms=now_ms,
        nonce=secrets.token_hex(32))
    context.reopen()
    publish_one(output, request, final_mode=0o600)
    context.reopen()
    return request


def issue_offline_candidate(
    *, offline_observation: Path, output: Path,
    _run_token: object | None = None,
) -> dict[str, Any]:
    require(_run_token is CLI_RUN_TOKEN, "PROVENANCE_EXPLICIT_RUN_REQUIRED")
    # Offline mode still binds the fixed installed producer and trust material.
    # It deliberately does not invoke Docker or touch AppArmor policy controls.
    context = ProductionContext()
    binding = bind_json_document(
        offline_observation, "PROVENANCE_OFFLINE_OBSERVATION_INVALID",
        modes=frozenset({0o400, 0o600}))
    document = validate_offline_document(binding.document)
    require(document["trust_bindings"] == context.trust_bindings,
            "PROVENANCE_OFFLINE_OBSERVATION_INVALID")
    binding.reopen()
    context.reopen()
    now_ms = time.time_ns() // 1_000_000
    request = build_request(
        observations=document["observations"],
        trust_bindings=document["trust_bindings"],
        base_reference=document["base_image_reference"],
        buildkit_reference=document["buildkit_image_reference"],
        observation_mode=OFFLINE_OBSERVATION_MODE, now_ms=now_ms,
        nonce=secrets.token_hex(32))
    binding.reopen()
    context.reopen()
    publish_one(output, request, final_mode=0o600)
    binding.reopen()
    context.reopen()
    return request


def produce_go(
    *, request_path: Path, authorization_path: Path, output_directory: Path,
    base_reference: str, buildkit_reference: str,
    _run_token: object | None = None,
) -> tuple[dict[str, dict[str, Any]], dict[str, str]]:
    require(_run_token is CLI_RUN_TOKEN, "PROVENANCE_EXPLICIT_RUN_REQUIRED")
    context = ProductionContext()
    observer = ProductionObserver(context)
    require(type(observer) is ProductionObserver and
            type(context.executor) is ProductionExecutor,
            "PROVENANCE_FAKE_EXECUTOR_CANNOT_EMIT_GO")
    request_binding = bind_json_document(
        request_path, "PROVENANCE_REQUEST_INVALID",
        modes=frozenset({0o400, 0o600}))
    authorization_binding = bind_json_document(
        authorization_path, "PROVENANCE_AUTHORIZATION_INVALID",
        modes=frozenset({0o400, 0o600}))
    authorization = parse_authorization(authorization_binding)

    def revalidate(*, verify: bool = False) -> SignatureCertification | None:
        now = time.time_ns() // 1_000_000
        request = validate_request(
            request_binding.document, now_ms=now, require_production=True)
        require(
            request["base_image_reference"] == base_reference and
            request["buildkit_image_reference"] == buildkit_reference and
            request["trust_bindings"] == context.trust_bindings,
            "PROVENANCE_REQUEST_INVALID")
        validate_authorization_payload(
            authorization.payload, request=request, now_ms=now)
        request_binding.reopen()
        authorization.binding.reopen()
        context.reopen()
        if verify:
            certification = context.verify_signature(
                authorization.payload_bytes, authorization.signature)
            require(context.certifies(
                certification, authorization.payload_bytes,
                authorization.signature),
                "PROVENANCE_AUTHORIZATION_SIGNATURE_INVALID")
            return certification
        return None

    revalidate(verify=True)
    first = observer.observe(
        base_reference=base_reference, buildkit_reference=buildkit_reference)
    revalidate(verify=True)
    second = observer.observe(
        base_reference=base_reference, buildkit_reference=buildkit_reference)
    revalidate(verify=True)
    request = request_binding.document
    require(
        first == second == request["observations"] ==
            authorization.payload["observations"],
        "PROVENANCE_ENVIRONMENT_DRIFTED_SINCE_REVIEW")
    completed_at_ms = time.time_ns() // 1_000_000
    validate_request(request, now_ms=completed_at_ms, require_production=True)
    validate_authorization_payload(
        authorization.payload, request=request, now_ms=completed_at_ms)
    documents = assemble_go_documents(
        observations=second, issued_at_ms=completed_at_ms,
        expires_at_ms=authorization.payload["expires_at_ms"])
    closure = assemble_review_closure(
        request_binding=request_binding, authorization=authorization,
        trust_bindings=context.trust_bindings,
        output_directory=output_directory, documents=documents,
        issued_at_ms=completed_at_ms,
        expires_at_ms=authorization.payload["expires_at_ms"])

    def reopen_all() -> None:
        revalidate(verify=False)

    digests = publish_reviewed_bundle(
        output_directory, documents, closure, reopen_hook=reopen_all)
    revalidate(verify=True)
    return {**documents, "review_closure": closure}, digests


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description=__doc__)
    result.add_argument("--run", action="store_true", required=True)
    operation = result.add_mutually_exclusive_group(required=True)
    operation.add_argument("--issue-review-request", action="store_true")
    operation.add_argument("--offline-candidate", action="store_true")
    operation.add_argument("--produce-go", action="store_true")
    operation.add_argument("--verify-closure", action="store_true")
    result.add_argument("--base-image")
    result.add_argument("--buildkit-image")
    result.add_argument("--candidate-output", type=Path)
    result.add_argument("--offline-observation", type=Path)
    result.add_argument("--request", type=Path)
    result.add_argument("--authorization", type=Path)
    result.add_argument("--output-directory", type=Path)
    result.add_argument("--review-closure", type=Path)
    return result


def main(argv: Sequence[str] | None = None) -> int:
    arguments = parser().parse_args(argv)
    try:
        if arguments.issue_review_request:
            require(
                arguments.base_image is not None and
                arguments.buildkit_image is not None and
                arguments.candidate_output is not None and
                arguments.offline_observation is None and
                arguments.request is None and arguments.authorization is None
                and arguments.output_directory is None and
                arguments.review_closure is None,
                "PROVENANCE_ARGUMENT_INVALID")
            request = issue_review_request(
                base_reference=arguments.base_image,
                buildkit_reference=arguments.buildkit_image,
                output=arguments.candidate_output, _run_token=CLI_RUN_TOKEN)
            print("STATUS=" + request["status"])
            print("REQUEST_SHA256=" + request["request_sha256"])
        elif arguments.offline_candidate:
            require(
                arguments.offline_observation is not None and
                arguments.candidate_output is not None and
                arguments.base_image is None and
                arguments.buildkit_image is None and arguments.request is None
                and arguments.authorization is None and
                arguments.output_directory is None and
                arguments.review_closure is None,
                "PROVENANCE_ARGUMENT_INVALID")
            request = issue_offline_candidate(
                offline_observation=arguments.offline_observation,
                output=arguments.candidate_output, _run_token=CLI_RUN_TOKEN)
            print("STATUS=" + request["status"])
            print("GO_ELIGIBLE=false")
        elif arguments.produce_go:
            require(
                arguments.request is not None and
                arguments.authorization is not None and
                arguments.output_directory is not None and
                arguments.base_image is not None and
                arguments.buildkit_image is not None and
                arguments.candidate_output is None and
                arguments.offline_observation is None and
                arguments.review_closure is None,
                "PROVENANCE_ARGUMENT_INVALID")
            _documents, digests = produce_go(
                request_path=arguments.request,
                authorization_path=arguments.authorization,
                output_directory=arguments.output_directory,
                base_reference=arguments.base_image,
                buildkit_reference=arguments.buildkit_image,
                _run_token=CLI_RUN_TOKEN)
            print("ENVIRONMENT_PROVENANCE=GO")
            for key in ("base", "builder", "apparmor", "docker_namespace"):
                print(key.upper() + "_SHA256=" + digests[key])
            print("REVIEW_CLOSURE_SHA256=" + digests["review_closure"])
        else:
            require(
                arguments.review_closure is not None and
                arguments.request is not None and
                arguments.authorization is not None and
                arguments.output_directory is not None and
                arguments.base_image is not None and
                arguments.buildkit_image is not None and
                arguments.candidate_output is None and
                arguments.offline_observation is None,
                "PROVENANCE_ARGUMENT_INVALID")
            result = verify_review_closure(
                closure_path=arguments.review_closure,
                request_path=arguments.request,
                authorization_path=arguments.authorization,
                output_directory=arguments.output_directory,
                base_reference=arguments.base_image,
                buildkit_reference=arguments.buildkit_image,
                _run_token=CLI_RUN_TOKEN)
            print("REVIEW_CLOSURE=" + result["status"])
            print("REVIEW_CLOSURE_SHA256=" + result["closure_sha256"])
        print("PAPER_AUTHORIZED=false")
        print("LIVE_AUTHORIZED=false")
        print("MUTATION_AUTHORIZED=false")
        print("DIRECT_BROKER_ACCESS=false")
        print("ORDER_SUBMISSION_AUTHORIZED=false")
        return 0
    except ProvenanceError as error:
        print(
            "hepta_rootful_systemd_environment_provenance: FAIL " +
            error.reason, file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
