#!/usr/bin/env python3
"""Strict consumer for the externally signed rootful review closure.

This module is deliberately small enough to be installed beside each rootful
gate runner.  It does not observe Docker or AppArmor on behalf of a gate.  It
only binds the signed review bundle, proves that the fixed installed verifier
is byte-for-byte the verifier from the externally pinned source commit,
invokes that verifier without a shell, and keeps every input reopenable until
the gate has completed its own independent current-state checks.
"""

from __future__ import annotations

import argparse
import base64
from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
import re
import signal
import stat
import subprocess
import time
from typing import Any, Mapping, Optional, Sequence


SCHEMA = "hepta.rootful-systemd-review-closure-verification.v1"
ENVIRONMENT_FINGERPRINT_SCHEMA = (
    "hepta.rootful-systemd-environment-fingerprint.v1")
REVIEW_CLOSURE_SCHEMA = (
    "hepta.agent-os-rootful-systemd-environment-review-closure.v1")
REQUEST_SCHEMA = (
    "hepta.agent-os-rootful-systemd-environment-review-request.v1")
AUTHORIZATION_ENVELOPE_SCHEMA = (
    "hepta.agent-os-rootful-systemd-environment-review-authorization-"
    "envelope.v1")
AUTHORIZATION_PAYLOAD_SCHEMA = (
    "hepta.agent-os-rootful-systemd-environment-review-authorization.v1")
OUTPUT_SCHEMAS = {
    "base": "hepta.agent-os-rootful-systemd-base-reviewed-provenance.v1",
    "builder": (
        "hepta.agent-os-rootful-systemd-isolated-builder-"
        "reviewed-provenance.v1"),
    "apparmor": (
        "hepta.agent-os-rootful-systemd-apparmor-reviewed-provenance.v1"),
    "docker_namespace": (
        "hepta.agent-os-rootful-systemd-docker-apparmor-namespace-"
        "reviewed-provenance.v1"),
}
OUTPUT_FILENAMES = {
    "base": "reviewed-base-image-provenance.v1.json",
    "builder": "reviewed-isolated-builder-provenance.v1.json",
    "apparmor": "reviewed-apparmor-provenance.v1.json",
    "docker_namespace":
        "reviewed-docker-apparmor-namespace-provenance.v1.json",
}
INSTALLED_VERIFIER = Path(
    "/usr/libexec/hepta-rootful-systemd-environment-provenance")
VERIFIER_SOURCE_RELATIVE = Path(
    "scripts/hepta_rootful_systemd_environment_provenance.py")
GIT = Path("/usr/bin/git")
MAX_JSON = 4 * 1024 * 1024
MAX_EXECUTABLE = 512 * 1024 * 1024
MAX_OUTPUT = 1024 * 1024
MAX_CLOCK_SKEW_MS = 5 * 1000
PINNED_IMAGE = re.compile(
    r"^[a-z0-9][a-z0-9._/:-]*@sha256:[0-9a-f]{64}$")
DIGEST = re.compile(r"^sha256:[0-9a-f]{64}$")
COMMIT = re.compile(r"^[0-9a-f]{40}$")
NONCE = re.compile(r"^[0-9a-f]{64}$")
IMAGE_ID = re.compile(r"^sha256:[0-9a-f]{64}$")
SEMVER = re.compile(
    r"^[0-9]+\.[0-9]+\.[0-9]+(?:[-+][0-9A-Za-z._-]+)?$")
BUILDKIT_VERSION = re.compile(
    r"^v[0-9]+\.[0-9]+\.[0-9]+(?:[-+][0-9A-Za-z._-]+)?$")
DOCKER_API_VERSION = re.compile(r"^[1-9][0-9]*\.[0-9]+$")
BUILD_ID = re.compile(r"^[0-9A-Za-z][0-9A-Za-z._+-]{0,127}$")
DAEMON_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9:._-]{0,127}$")
BOOT_ID = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-"
    r"[0-9a-f]{12}$")
RAW_ABI = re.compile(r"^v[1-9][0-9]{0,2}$")
REVIEWER_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:@+-]{0,127}$")
PROFILE_NAME = "hepta-systemd-gate"
REVIEW_AUTHORITY = "EXTERNAL_INDEPENDENT_ROOTFUL_ENVIRONMENT_REVIEW"
SAFE_ENVIRONMENT = {
    "PATH": "/usr/sbin:/usr/bin:/sbin:/bin", "LANG": "C",
    "LC_ALL": "C", "TZ": "UTC", "PYTHONNOUSERSITE": "1",
}
FALSE_AUTHORITY = {
    "paper_authorized": False,
    "live_authorized": False,
    "mutation_authorized": False,
    "direct_broker_access": False,
    "order_submission_authorized": False,
}
AUTHORITY_FIELDS = frozenset(FALSE_AUTHORITY)
CERTIFICATION_BLOCKER = (
    "externally-signed-rootful-environment-review-closure-required")

REFERENCE_FIELDS = frozenset({"path", "sha256"})
REQUEST_REFERENCE_FIELDS = frozenset({
    "path", "file_sha256", "request_sha256", "nonce"})
AUTHORIZATION_REFERENCE_FIELDS = frozenset({
    "path", "file_sha256", "signed_payload_sha256", "signature_sha256"})
OUTPUT_REFERENCE_FIELDS = frozenset({"path", "file_sha256", "schema"})
CLOSURE_FIELDS = frozenset({
    "schema", "version", "status", "issued_at_ms", "expires_at_ms",
    "base_image_reference", "buildkit_image_reference", "review_authority",
    "reviewer_id", "request_reference", "authorization_reference",
    "producer", "trust_bindings", "outputs", *AUTHORITY_FIELDS,
    "closure_sha256",
})
REQUEST_FIELDS = frozenset({
    "schema", "version", "status", "observation_mode", "observed_at_ms",
    "expires_at_ms", "nonce", "base_image_reference",
    "buildkit_image_reference", "observations", "trust_bindings",
    "go_eligible", *AUTHORITY_FIELDS, "request_sha256",
})
AUTHORIZATION_ENVELOPE_FIELDS = frozenset({
    "schema", "version", "payload", "signature_base64"})
AUTHORIZATION_PAYLOAD_FIELDS = frozenset({
    "schema", "version", "decision", "review_authority", "reviewer_id",
    "issued_at_ms", "expires_at_ms", "nonce", "request_sha256",
    "base_image_reference", "buildkit_image_reference", "observations",
    "trust_bindings", *AUTHORITY_FIELDS,
})
RECORD_FIELDS = frozenset({
    "schema", "status", "verified_at_ms", "expires_at_ms",
    "source_commit", "base_image_reference", "buildkit_image_reference",
    "output_directory", "verifier", "closure", "request",
    "authorization", "outputs", "invocation", "environment_fingerprint",
    "reopened_after_invocation", "reopened_at_gate_end",
    *AUTHORITY_FIELDS,
})
FILE_RECORD_FIELDS = frozenset({
    "path", "file_sha256", "mode", "uid", "gid", "identity_sha256"})
VERIFIER_RECORD_FIELDS = FILE_RECORD_FIELDS | frozenset({
    "source_path", "source_file_sha256", "source_commit"})
INVOCATION_FIELDS = frozenset({
    "argv_sha256", "stdout_sha256", "returncode", "duration_ms",
    "exact_success_output", "no_shell"})
ENVIRONMENT_FINGERPRINT_FIELDS = frozenset({
    "schema", "source_commit", "verifier_file_sha256",
    "verifier_source_file_sha256", "review_authority", "reviewer_id",
    "observations", "trust_bindings", "body_sha256",
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
TRUST_BINDING_PATHS = {
    "producer": str(INSTALLED_VERIFIER),
    "docker_cli": "/usr/bin/docker",
    "signature_verifier": "/usr/bin/openssl",
    "verification_key":
        "/etc/heptatrader/rootful-systemd-review-ed25519.pub",
    "apparmor_policy_source":
        "/usr/share/heptatrader/systemd/hepta-systemd-gate.apparmor",
}
BASE_LABELS = {
    "io.hepta.rootful-systemd-base.offline-ready": "true",
    "io.hepta.rootful-systemd-base.version": "1",
}


class ReviewClosureError(RuntimeError):
    """Stable fail-closed review-closure consumer error."""


def fail(message: str) -> None:
    raise ReviewClosureError(message)


def canonical_bytes(value: Any) -> bytes:
    try:
        return (json.dumps(
            value, sort_keys=True, separators=(",", ":"), ensure_ascii=True,
            allow_nan=False) + "\n").encode("ascii")
    except (TypeError, ValueError, UnicodeError) as error:
        raise ReviewClosureError("review closure canonicalization failed") from error


def sha256_bytes(payload: bytes) -> str:
    return "sha256:" + hashlib.sha256(payload).hexdigest()


def duplicate_rejecting_pairs(
        pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            fail("review closure JSON contains a duplicate field")
        result[key] = value
    return result


def strict_json(payload: bytes, label: str) -> dict[str, Any]:
    try:
        value = json.loads(
            payload.decode("ascii", errors="strict"),
            object_pairs_hook=duplicate_rejecting_pairs,
            parse_constant=lambda _value: fail(label + " contains non-finite JSON"),
        )
    except ReviewClosureError:
        raise
    except (UnicodeError, json.JSONDecodeError) as error:
        raise ReviewClosureError(label + " is not strict JSON") from error
    if type(value) is not dict or canonical_bytes(value) != payload:
        fail(label + " is not a canonical JSON object")
    return value


def file_identity(metadata: os.stat_result) -> tuple[int, ...]:
    return (
        metadata.st_dev, metadata.st_ino, metadata.st_mode,
        metadata.st_nlink, metadata.st_uid, metadata.st_gid,
        metadata.st_size, metadata.st_mtime_ns, metadata.st_ctime_ns,
    )


def identity_sha256(identity: tuple[int, ...]) -> str:
    return sha256_bytes(json.dumps(
        list(identity), separators=(",", ":")).encode("ascii"))


@dataclass(frozen=True)
class BoundFile:
    path: Path
    payload: bytes
    identity: tuple[int, ...]
    expected_uid: int
    expected_gid: int
    modes: frozenset[int]
    maximum: int
    require_root_parent: bool

    def reopen(self) -> None:
        current = bind_file(
            self.path, expected_uid=self.expected_uid,
            expected_gid=self.expected_gid, modes=self.modes,
            maximum=self.maximum,
            require_root_parent=self.require_root_parent)
        if current.payload != self.payload or current.identity != self.identity:
            fail("review closure bound input changed")

    def record(self) -> dict[str, Any]:
        return {
            "path": str(self.path), "file_sha256": sha256_bytes(self.payload),
            "mode": format(stat.S_IMODE(self.identity[2]), "04o"),
            "uid": self.identity[4], "gid": self.identity[5],
            "identity_sha256": identity_sha256(self.identity),
        }


def _canonical_absolute(path: Path) -> Path:
    if not isinstance(path, Path) or not path.is_absolute():
        fail("review closure input path must be absolute")
    normalized = Path(os.path.normpath(os.fspath(path)))
    if normalized != path or path.name in {"", ".", ".."}:
        fail("review closure input path is not canonical")
    return path


def _open_parent(path: Path, *, require_root_parent: bool) -> int:
    flags = (
        os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC |
        getattr(os, "O_NOFOLLOW", 0))
    descriptor = os.open("/", flags)
    try:
        for component in path.parent.parts[1:]:
            before = os.stat(
                component, dir_fd=descriptor, follow_symlinks=False)
            child = os.open(component, flags, dir_fd=descriptor)
            opened = os.fstat(child)
            after = os.stat(
                component, dir_fd=descriptor, follow_symlinks=False)
            if (
                    not stat.S_ISDIR(opened.st_mode) or
                    file_identity(before)[:6] != file_identity(opened)[:6] or
                    file_identity(opened)[:6] != file_identity(after)[:6]):
                fail("review closure path ancestor changed")
            if require_root_parent and (
                    opened.st_uid != 0 or opened.st_gid != 0 or
                    stat.S_IMODE(opened.st_mode) & 0o022):
                fail("review closure path ancestor is not root-owned and fixed")
            os.close(descriptor)
            descriptor = child
        return descriptor
    except BaseException:
        os.close(descriptor)
        raise


def bind_file(
        path: Path, *, expected_uid: int, expected_gid: int,
        modes: frozenset[int], maximum: int,
        require_root_parent: bool = True) -> BoundFile:
    path = _canonical_absolute(path)
    parent = _open_parent(path, require_root_parent=require_root_parent)
    descriptor = -1
    try:
        named_before = os.stat(
            path.name, dir_fd=parent, follow_symlinks=False)
        descriptor = os.open(
            path.name,
            os.O_RDONLY | os.O_CLOEXEC | getattr(os, "O_NOFOLLOW", 0),
            dir_fd=parent)
        opened = os.fstat(descriptor)
        if (
                not stat.S_ISREG(opened.st_mode) or opened.st_nlink != 1 or
                opened.st_uid != expected_uid or opened.st_gid != expected_gid or
                stat.S_IMODE(opened.st_mode) not in modes or
                not 0 < opened.st_size <= maximum or
                file_identity(named_before) != file_identity(opened)):
            fail("review closure file metadata is unsafe: " + str(path))
        payload = bytearray()
        while len(payload) <= maximum:
            chunk = os.read(
                descriptor, min(65536, maximum + 1 - len(payload)))
            if not chunk:
                break
            payload.extend(chunk)
        after = os.fstat(descriptor)
        named_after = os.stat(
            path.name, dir_fd=parent, follow_symlinks=False)
        if (
                not 0 < len(payload) <= maximum or
                file_identity(opened) != file_identity(after) or
                file_identity(after) != file_identity(named_after)):
            fail("review closure file changed while reading: " + str(path))
        return BoundFile(
            path, bytes(payload), file_identity(opened), expected_uid,
            expected_gid, modes, maximum, require_root_parent)
    except ReviewClosureError:
        raise
    except OSError as error:
        raise ReviewClosureError(
            "review closure file cannot be securely opened: " + str(path)
        ) from error
    finally:
        if descriptor >= 0:
            os.close(descriptor)
        os.close(parent)


@dataclass(frozen=True)
class ReviewClosureInputs:
    closure_path: Path
    request_path: Path
    authorization_path: Path
    output_directory: Path


def add_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--rootful-review-closure", type=Path)
    parser.add_argument("--rootful-review-request", type=Path)
    parser.add_argument("--rootful-review-authorization", type=Path)
    parser.add_argument("--rootful-review-output-directory", type=Path)


def inputs_from_values(
        *, certify: bool, closure_path: Optional[Path],
        request_path: Optional[Path], authorization_path: Optional[Path],
        output_directory: Optional[Path]) -> Optional[ReviewClosureInputs]:
    values = (closure_path, request_path, authorization_path, output_directory)
    if not certify:
        if any(value is not None for value in values):
            fail("review closure inputs require explicit certification mode")
        return None
    if any(value is None for value in values):
        fail("certification requires the complete signed review closure bundle")
    assert closure_path is not None and request_path is not None
    assert authorization_path is not None and output_directory is not None
    return ReviewClosureInputs(
        _canonical_absolute(closure_path), _canonical_absolute(request_path),
        _canonical_absolute(authorization_path),
        _canonical_absolute(output_directory))


def inputs_from_arguments(
        arguments: argparse.Namespace, *, certify: bool
        ) -> Optional[ReviewClosureInputs]:
    return inputs_from_values(
        certify=certify,
        closure_path=arguments.rootful_review_closure,
        request_path=arguments.rootful_review_request,
        authorization_path=arguments.rootful_review_authorization,
        output_directory=arguments.rootful_review_output_directory)


def _run_bounded(argv: Sequence[str], *, timeout: int = 120) -> tuple[int, bytes, int]:
    started = time.monotonic_ns()
    process = subprocess.Popen(
        list(argv), stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT, env=SAFE_ENVIRONMENT, close_fds=True,
        start_new_session=True, shell=False)
    try:
        output, _unused = process.communicate(timeout=timeout)
    except BaseException:
        if process.poll() is None:
            try:
                os.killpg(process.pid, signal.SIGTERM)
                process.wait(timeout=3)
            except (OSError, subprocess.TimeoutExpired):
                if process.poll() is None:
                    try:
                        os.killpg(process.pid, signal.SIGKILL)
                    except OSError:
                        pass
                    process.wait()
        raise
    if len(output) > MAX_OUTPUT:
        fail("review closure verifier output exceeded its bound")
    return (
        process.returncode, output,
        max(0, (time.monotonic_ns() - started) // 1_000_000))


def _git_blob(
        repository_root: Path, expected_commit: str
        ) -> tuple[bytes, str]:
    if COMMIT.fullmatch(expected_commit) is None:
        fail("review closure verifier source commit is not canonical")
    relative = VERIFIER_SOURCE_RELATIVE.as_posix()
    code, tree_output, _duration = _run_bounded([
        str(GIT), "-C", str(repository_root), "ls-tree", expected_commit,
        "--", relative,
    ], timeout=30)
    try:
        tree_text = tree_output.decode("ascii", errors="strict").strip()
    except UnicodeError as error:
        raise ReviewClosureError("verifier git tree output is invalid") from error
    match = re.fullmatch(
        r"(100755) blob ([0-9a-f]{40,64})\t" + re.escape(relative),
        tree_text)
    if code != 0 or match is None:
        fail("pinned source commit does not contain the executable verifier")
    code, blob, _duration = _run_bounded([
        str(GIT), "-C", str(repository_root), "cat-file", "blob", match.group(2),
    ], timeout=30)
    if code != 0 or not 0 < len(blob) <= MAX_EXECUTABLE:
        fail("cannot read verifier blob from pinned source commit")
    return blob, match.group(2)


def _validate_false_authority(value: Mapping[str, Any], label: str) -> None:
    if any(value.get(field) is not expected
           for field, expected in FALSE_AUTHORITY.items()):
        fail(label + " attempted to grant operational authority")


def _canonical_object_sha256(value: Any) -> str:
    try:
        payload = json.dumps(
            value, sort_keys=True, separators=(",", ":"),
            ensure_ascii=True, allow_nan=False).encode("ascii")
    except (TypeError, ValueError, UnicodeError) as error:
        raise ReviewClosureError(
            "environment fingerprint canonicalization failed") from error
    return sha256_bytes(payload)


def _validate_reference(value: Any, label: str) -> dict[str, str]:
    if type(value) is not dict or set(value) != REFERENCE_FIELDS:
        fail(label + " exact-field contract mismatch")
    path = value.get("path")
    digest = value.get("sha256")
    if (
            type(path) is not str or not path.startswith("/") or
            os.path.normpath(path) != path or
            DIGEST.fullmatch(str(digest)) is None):
        fail(label + " value contract mismatch")
    return value


def _validate_trust_bindings(value: Any) -> dict[str, dict[str, str]]:
    if type(value) is not dict or set(value) != TRUST_BINDING_FIELDS:
        fail("environment trust binding exact-field contract mismatch")
    result: dict[str, dict[str, str]] = {}
    for key in TRUST_BINDING_FIELDS:
        reference = _validate_reference(
            value.get(key), "environment trust binding " + key)
        if reference.get("path") != TRUST_BINDING_PATHS[key]:
            fail("environment trust binding fixed path mismatch")
        result[key] = reference
    return result


def _validate_observations(value: Any) -> dict[str, Any]:
    if type(value) is not dict or set(value) != OBSERVATION_TOP_FIELDS:
        fail("environment observation exact-field contract mismatch")
    base = value.get("base_image")
    if type(base) is not dict or set(base) != BASE_OBSERVATION_FIELDS:
        fail("base-image observation exact-field contract mismatch")
    repo_digests = base.get("repo_digests")
    if (
            IMAGE_ID.fullmatch(str(base.get("image_id", ""))) is None or
            PINNED_IMAGE.fullmatch(str(base.get("repo_digest", ""))) is None or
            type(repo_digests) is not list or
            repo_digests != sorted(repo_digests) or
            len(repo_digests) != len(set(repo_digests)) or
            base.get("repo_digest") not in repo_digests or
            any(type(item) is not str or PINNED_IMAGE.fullmatch(item) is None
                for item in repo_digests) or
            base.get("labels_sha256") !=
                _canonical_object_sha256(BASE_LABELS) or
            base.get("os") != "linux" or base.get("architecture") != "amd64" or
            base.get("declared_volumes") != 0 or
            base.get("onbuild_instructions") != 0):
        fail("base-image observation value contract mismatch")

    builder = value.get("isolated_builder")
    if type(builder) is not dict or set(builder) != BUILDER_OBSERVATION_FIELDS:
        fail("isolated-builder observation exact-field contract mismatch")
    builder_repo_digests = builder.get("repo_digests")
    buildx_path = builder.get("buildx_path")
    if (
            IMAGE_ID.fullmatch(str(builder.get("image_id", ""))) is None or
            PINNED_IMAGE.fullmatch(
                str(builder.get("repo_digest", ""))) is None or
            type(builder_repo_digests) is not list or
            builder_repo_digests != sorted(builder_repo_digests) or
            len(builder_repo_digests) != len(set(builder_repo_digests)) or
            builder.get("repo_digest") not in builder_repo_digests or
            any(type(item) is not str or PINNED_IMAGE.fullmatch(item) is None
                for item in builder_repo_digests) or
            any(DIGEST.fullmatch(str(builder.get(field, ""))) is None
                for field in (
                    "config_sha256", "buildkit_binary_sha256",
                    "buildx_path_sha256", "buildx_binary_sha256")) or
            builder.get("os") != "linux" or
            builder.get("architecture") != "amd64" or
            builder.get("entrypoint") not in (
                ["buildkitd"], ["/usr/bin/buildkitd"],
                ["/usr/local/bin/buildkitd"]) or
            builder.get("buildkit_binary_path") not in (
                "/usr/bin/buildkitd", "/usr/local/bin/buildkitd") or
            BUILDKIT_VERSION.fullmatch(
                str(builder.get("buildkit_version", ""))) is None or
            type(buildx_path) is not str or not buildx_path.startswith("/") or
            sha256_bytes(buildx_path.encode("utf-8")) !=
                builder.get("buildx_path_sha256") or
            SEMVER.fullmatch(str(builder.get("buildx_version", ""))) is None or
            SEMVER.fullmatch(
                str(builder.get("docker_server_version", ""))) is None or
            DOCKER_API_VERSION.fullmatch(str(
                builder.get("docker_server_api_version", ""))) is None or
            BUILD_ID.fullmatch(str(
                builder.get("docker_server_git_commit", ""))) is None):
        fail("isolated-builder observation value contract mismatch")

    apparmor = value.get("apparmor")
    if type(apparmor) is not dict or set(apparmor) != APPARMOR_OBSERVATION_FIELDS:
        fail("AppArmor observation exact-field contract mismatch")
    if (
            apparmor.get("profile") != PROFILE_NAME or
            apparmor.get("mode") != "enforce" or
            apparmor.get("attach") != PROFILE_NAME or
            apparmor.get("learning_count") != 0 or
            any(DIGEST.fullmatch(str(apparmor.get(field, ""))) is None
                for field in (
                    "policy_source_sha256", "profile_sha256", "raw_sha256",
                    "profile_inventory_sha256")) or
            RAW_ABI.fullmatch(str(apparmor.get("raw_abi", ""))) is None or
            re.fullmatch(r"[1-9][0-9]{0,19}", str(
                apparmor.get("raw_data_id", ""))) is None or
            apparmor.get("namespace_name") != "root" or
            apparmor.get("namespace_level") != 0 or
            apparmor.get("namespace_stacked") is not False):
        fail("AppArmor observation value contract mismatch")

    docker = value.get("docker_namespace")
    if type(docker) is not dict or set(docker) != DOCKER_OBSERVATION_FIELDS:
        fail("Docker namespace observation exact-field contract mismatch")
    daemon_pid = docker.get("docker_daemon_pid")
    daemon_ticks = docker.get("docker_daemon_start_time_ticks")
    self_userns = docker.get("self_user_namespace_inode")
    if (
            DAEMON_ID.fullmatch(str(docker.get("docker_daemon_id", ""))) is None or
            type(daemon_pid) is not int or not 1 < daemon_pid <= 4_194_304 or
            type(daemon_ticks) is not int or daemon_ticks <= 0 or
            DIGEST.fullmatch(str(
                docker.get("docker_daemon_exe_sha256", ""))) is None or
            BOOT_ID.fullmatch(str(docker.get("host_boot_id", ""))) is None or
            docker.get("host_namespace_name") != "root" or
            docker.get("host_namespace_level") != 0 or
            docker.get("host_namespace_stacked") is not False or
            docker.get("daemon_namespace_name") != "root" or
            docker.get("daemon_namespace_level") != 0 or
            docker.get("daemon_namespace_stacked") is not False or
            docker.get("daemon_apparmor_current") != "unconfined" or
            type(self_userns) is not int or self_userns <= 0 or
            docker.get("daemon_user_namespace_inode") != self_userns):
        fail("Docker namespace observation value contract mismatch")
    return value


def build_environment_fingerprint(
        *, source_commit: str, verifier_file_sha256: str,
        verifier_source_file_sha256: str, review_authority: str,
        reviewer_id: str, observations: Any,
        trust_bindings: Any) -> dict[str, Any]:
    body = {
        "schema": ENVIRONMENT_FINGERPRINT_SCHEMA,
        "source_commit": source_commit,
        "verifier_file_sha256": verifier_file_sha256,
        "verifier_source_file_sha256": verifier_source_file_sha256,
        "review_authority": review_authority,
        "reviewer_id": reviewer_id,
        "observations": observations,
        "trust_bindings": trust_bindings,
    }
    result = {**body, "body_sha256": sha256_bytes(canonical_bytes(body))}
    return validate_environment_fingerprint(result)


def validate_environment_fingerprint(value: Any) -> dict[str, Any]:
    if type(value) is not dict or set(value) != ENVIRONMENT_FINGERPRINT_FIELDS:
        fail("environment fingerprint exact-field contract mismatch")
    body = dict(value)
    claimed = body.pop("body_sha256", None)
    observations = _validate_observations(value.get("observations"))
    trust = _validate_trust_bindings(value.get("trust_bindings"))
    if (
            value.get("schema") != ENVIRONMENT_FINGERPRINT_SCHEMA or
            COMMIT.fullmatch(str(value.get("source_commit", ""))) is None or
            DIGEST.fullmatch(str(
                value.get("verifier_file_sha256", ""))) is None or
            DIGEST.fullmatch(str(
                value.get("verifier_source_file_sha256", ""))) is None or
            value.get("verifier_file_sha256") !=
                value.get("verifier_source_file_sha256") or
            value.get("verifier_file_sha256") !=
                trust["producer"]["sha256"] or
            value.get("review_authority") != REVIEW_AUTHORITY or
            type(value.get("reviewer_id")) is not str or
            REVIEWER_ID.fullmatch(value["reviewer_id"]) is None or
            DIGEST.fullmatch(str(claimed)) is None or
            claimed != sha256_bytes(canonical_bytes(body))):
        fail("environment fingerprint value or canonical seal mismatch")
    return value


def _validate_closure(
        closure: dict[str, Any], *, now_ms: int,
        inputs: ReviewClosureInputs, base_image: str,
        buildkit_image: str) -> None:
    if set(closure) != CLOSURE_FIELDS:
        fail("review closure exact-field contract mismatch")
    _validate_false_authority(closure, "review closure")
    issued = closure.get("issued_at_ms")
    expires = closure.get("expires_at_ms")
    body = dict(closure)
    claimed = body.pop("closure_sha256", None)
    trust = _validate_trust_bindings(closure.get("trust_bindings"))
    producer = _validate_reference(
        closure.get("producer"), "review closure producer")
    if (
            closure.get("schema") != REVIEW_CLOSURE_SCHEMA or
            closure.get("version") != 1 or
            closure.get("status") != "EXTERNALLY_REVIEWED_GO_CLOSED" or
            type(issued) is not int or type(expires) is not int or
            issued > now_ms + MAX_CLOCK_SKEW_MS or expires <= now_ms or
            expires <= issued or expires - issued > 60 * 60 * 1000 or
            closure.get("base_image_reference") != base_image or
            closure.get("buildkit_image_reference") != buildkit_image or
            closure.get("review_authority") != REVIEW_AUTHORITY or
            type(closure.get("reviewer_id")) is not str or
            REVIEWER_ID.fullmatch(closure["reviewer_id"]) is None or
            producer != trust["producer"] or
            DIGEST.fullmatch(str(claimed)) is None or
            claimed != sha256_bytes(canonical_bytes(body))):
        fail("review closure status, lifetime, lineage, or digest is invalid")
    request = closure.get("request_reference")
    authorization = closure.get("authorization_reference")
    outputs = closure.get("outputs")
    if (
            type(request) is not dict or set(request) != REQUEST_REFERENCE_FIELDS or
            request.get("path") != str(inputs.request_path) or
            DIGEST.fullmatch(str(request.get("file_sha256", ""))) is None or
            DIGEST.fullmatch(str(request.get("request_sha256", ""))) is None or
            NONCE.fullmatch(str(request.get("nonce", ""))) is None or
            type(authorization) is not dict or
            set(authorization) != AUTHORIZATION_REFERENCE_FIELDS or
            authorization.get("path") != str(inputs.authorization_path) or
            any(DIGEST.fullmatch(str(authorization.get(field, ""))) is None
                for field in (
                    "file_sha256", "signed_payload_sha256", "signature_sha256")) or
            type(outputs) is not dict or set(outputs) != set(OUTPUT_FILENAMES)):
        fail("review closure reference contract mismatch")
    for key, filename in OUTPUT_FILENAMES.items():
        reference = outputs.get(key)
        if (
                type(reference) is not dict or
                set(reference) != OUTPUT_REFERENCE_FIELDS or
                reference.get("path") != str(inputs.output_directory / filename) or
                reference.get("schema") != OUTPUT_SCHEMAS[key] or
                DIGEST.fullmatch(str(reference.get("file_sha256", ""))) is None):
            fail("review closure output reference mismatch")


def _expected_stdout(closure: Mapping[str, Any]) -> bytes:
    return (
        "REVIEW_CLOSURE=EXTERNALLY_REVIEWED_GO_CLOSED\n"
        "REVIEW_CLOSURE_SHA256=" + str(closure["closure_sha256"]) + "\n"
        "PAPER_AUTHORIZED=false\n"
        "LIVE_AUTHORIZED=false\n"
        "MUTATION_AUTHORIZED=false\n"
        "DIRECT_BROKER_ACCESS=false\n"
        "ORDER_SUBMISSION_AUTHORIZED=false\n"
    ).encode("ascii")


@dataclass
class VerificationSession:
    inputs: ReviewClosureInputs
    bindings: dict[str, BoundFile]
    documents: dict[str, dict[str, Any]]
    source_commit: str
    base_image: str
    buildkit_image: str
    verified_at_ms: int
    invocation: dict[str, Any]
    reopened_after_invocation: bool
    reopened_at_gate_end: bool = False

    def _reopen_all(self) -> None:
        for binding in self.bindings.values():
            binding.reopen()
        now_ms = time.time_ns() // 1_000_000
        _validate_closure(
            self.documents["closure"], now_ms=now_ms, inputs=self.inputs,
            base_image=self.base_image, buildkit_image=self.buildkit_image)
        if now_ms >= int(self.documents["closure"]["expires_at_ms"]):
            fail("review closure expired before gate evidence closure")

    def reopen_at_gate_end(self) -> None:
        if self.reopened_at_gate_end:
            fail("review closure gate-end reopen was attempted twice")
        self._reopen_all()
        self.reopened_at_gate_end = True

    def output_reference(self, kind: str) -> dict[str, Any]:
        if kind not in OUTPUT_FILENAMES:
            fail("unknown review closure output kind")
        return dict(self.documents["closure"]["outputs"][kind])

    def report_record(self) -> dict[str, Any]:
        if not self.reopened_at_gate_end:
            fail("review closure was not securely reopened at gate end")
        closure = self.documents["closure"]
        request = self.documents["request"]
        authorization = self.documents["authorization"]
        payload = authorization["payload"]
        verifier_record = self.bindings["verifier"].record()
        verifier_record.update({
            "source_path": str(self.bindings["source"].path),
            "source_file_sha256": sha256_bytes(
                self.bindings["source"].payload),
            "source_commit": self.source_commit,
        })
        fingerprint = build_environment_fingerprint(
            source_commit=self.source_commit,
            verifier_file_sha256=verifier_record["file_sha256"],
            verifier_source_file_sha256=
                verifier_record["source_file_sha256"],
            review_authority=payload["review_authority"],
            reviewer_id=payload["reviewer_id"],
            observations=payload["observations"],
            trust_bindings=payload["trust_bindings"],
        )
        record = {
            "schema": SCHEMA,
            "status": "VERIFIED_EXTERNALLY_SIGNED_REVIEW_CLOSURE",
            "verified_at_ms": self.verified_at_ms,
            "expires_at_ms": closure["expires_at_ms"],
            "source_commit": self.source_commit,
            "base_image_reference": self.base_image,
            "buildkit_image_reference": self.buildkit_image,
            "output_directory": str(self.inputs.output_directory),
            "verifier": verifier_record,
            "closure": {
                **self.bindings["closure"].record(),
                "closure_sha256": closure["closure_sha256"],
                "review_authority": closure["review_authority"],
                "reviewer_id": closure["reviewer_id"],
            },
            "request": {
                **self.bindings["request"].record(),
                "request_sha256": request["request_sha256"],
                "nonce": request["nonce"],
            },
            "authorization": {
                **self.bindings["authorization"].record(),
                "signed_payload_sha256": sha256_bytes(canonical_bytes(payload)),
                "signature_sha256": sha256_bytes(base64.b64decode(
                    authorization["signature_base64"].encode("ascii"),
                    validate=True)),
                "review_authority": payload["review_authority"],
                "reviewer_id": payload["reviewer_id"],
            },
            "outputs": {
                key: {
                    **self.bindings["output_" + key].record(),
                    "schema": self.documents["output_" + key]["schema"],
                }
                for key in OUTPUT_FILENAMES
            },
            "environment_fingerprint": fingerprint,
            "invocation": dict(self.invocation),
            "reopened_after_invocation": self.reopened_after_invocation,
            "reopened_at_gate_end": self.reopened_at_gate_end,
            **FALSE_AUTHORITY,
        }
        return validate_verification_record(record, now_ms=self.verified_at_ms)


def verify_review_closure(
        *, inputs: ReviewClosureInputs, base_image: str,
        buildkit_image: str, repository_root: Path,
        expected_source_commit: str) -> VerificationSession:
    if os.geteuid() != 0:
        fail("certifying review closure verification requires euid 0")
    if PINNED_IMAGE.fullmatch(base_image) is None or PINNED_IMAGE.fullmatch(
            buildkit_image) is None:
        fail("review closure requires exact digest-pinned images")
    repository_root = repository_root.resolve(strict=True)
    source_path = (repository_root / VERIFIER_SOURCE_RELATIVE).resolve(strict=True)
    if source_path != repository_root / VERIFIER_SOURCE_RELATIVE:
        fail("review closure verifier source path escaped the repository")
    source_metadata = os.stat(source_path, follow_symlinks=False)
    source_binding = bind_file(
        source_path, expected_uid=source_metadata.st_uid,
        expected_gid=source_metadata.st_gid,
        modes=frozenset({stat.S_IMODE(source_metadata.st_mode)}),
        maximum=MAX_EXECUTABLE, require_root_parent=False)
    commit_blob, _blob_id = _git_blob(repository_root, expected_source_commit)
    if source_binding.payload != commit_blob:
        fail("review closure verifier source drifted from pinned commit")
    verifier = bind_file(
        INSTALLED_VERIFIER, expected_uid=0, expected_gid=0,
        modes=frozenset({0o755}), maximum=MAX_EXECUTABLE)
    if verifier.payload != commit_blob:
        fail("installed review closure verifier differs from pinned source")

    bindings: dict[str, BoundFile] = {
        "source": source_binding,
        "verifier": verifier,
        "closure": bind_file(
            inputs.closure_path, expected_uid=0, expected_gid=0,
            modes=frozenset({0o400}), maximum=MAX_JSON),
        "request": bind_file(
            inputs.request_path, expected_uid=0, expected_gid=0,
            modes=frozenset({0o400, 0o600}), maximum=MAX_JSON),
        "authorization": bind_file(
            inputs.authorization_path, expected_uid=0, expected_gid=0,
            modes=frozenset({0o400, 0o600}), maximum=MAX_JSON),
    }
    for key, filename in OUTPUT_FILENAMES.items():
        bindings["output_" + key] = bind_file(
            inputs.output_directory / filename,
            expected_uid=0, expected_gid=0, modes=frozenset({0o400}),
            maximum=MAX_JSON)
    documents = {
        key: strict_json(binding.payload, "review closure " + key)
        for key, binding in bindings.items()
        if key not in {"source", "verifier"}
    }
    now_ms = time.time_ns() // 1_000_000
    closure = documents["closure"]
    request = documents["request"]
    authorization = documents["authorization"]
    _validate_closure(
        closure, now_ms=now_ms, inputs=inputs, base_image=base_image,
        buildkit_image=buildkit_image)
    if set(request) != REQUEST_FIELDS or request.get("schema") != REQUEST_SCHEMA:
        fail("review request exact-field/schema mismatch")
    _validate_false_authority(request, "review request")
    request_body = dict(request)
    request_sha256 = request_body.pop("request_sha256", None)
    observations = _validate_observations(request.get("observations"))
    trust_bindings = _validate_trust_bindings(request.get("trust_bindings"))
    if (
            request.get("version") != 1 or
            request.get("status") != "REVIEW_REQUIRED" or
            request.get("observation_mode") !=
                "PRODUCTION_ROOT_DIRECT_OBSERVATION" or
            request.get("go_eligible") is not True or
            type(request.get("observed_at_ms")) is not int or
            type(request.get("expires_at_ms")) is not int or
            request["observed_at_ms"] > now_ms + MAX_CLOCK_SKEW_MS or
            request["expires_at_ms"] <= now_ms or
            request["expires_at_ms"] <= request["observed_at_ms"] or
            request["expires_at_ms"] - request["observed_at_ms"] >
                60 * 60 * 1000 or
            request_sha256 != sha256_bytes(canonical_bytes(request_body)) or
            request.get("request_sha256") !=
                closure["request_reference"]["request_sha256"] or
            request.get("nonce") != closure["request_reference"]["nonce"] or
            sha256_bytes(bindings["request"].payload) !=
                closure["request_reference"]["file_sha256"] or
            request.get("base_image_reference") != base_image or
            request.get("buildkit_image_reference") != buildkit_image or
            observations["base_image"]["repo_digest"] != base_image or
            observations["isolated_builder"]["repo_digest"] !=
                buildkit_image or
            trust_bindings != closure.get("trust_bindings") or
            trust_bindings["producer"]["sha256"] !=
                sha256_bytes(verifier.payload)):
        fail("review request is not bound by the closure")
    if (
            set(authorization) != AUTHORIZATION_ENVELOPE_FIELDS or
            authorization.get("schema") != AUTHORIZATION_ENVELOPE_SCHEMA or
            authorization.get("version") != 1 or
            type(authorization.get("payload")) is not dict or
            set(authorization["payload"]) != AUTHORIZATION_PAYLOAD_FIELDS):
        fail("review authorization envelope contract mismatch")
    payload = authorization["payload"]
    _validate_false_authority(payload, "review authorization")
    try:
        signature = base64.b64decode(
            str(authorization.get("signature_base64", "")).encode("ascii"),
            validate=True)
    except (UnicodeError, ValueError, base64.binascii.Error) as error:
        raise ReviewClosureError("review authorization signature is invalid") from error
    if (
            len(signature) != 64 or
            payload.get("schema") != AUTHORIZATION_PAYLOAD_SCHEMA or
            payload.get("version") != 1 or
            payload.get("decision") != "GO" or
            payload.get("review_authority") != REVIEW_AUTHORITY or
            type(payload.get("reviewer_id")) is not str or
            REVIEWER_ID.fullmatch(payload["reviewer_id"]) is None or
            type(payload.get("issued_at_ms")) is not int or
            type(payload.get("expires_at_ms")) is not int or
            payload["issued_at_ms"] < request["observed_at_ms"] or
            payload["issued_at_ms"] > now_ms + MAX_CLOCK_SKEW_MS or
            payload["expires_at_ms"] <= now_ms or
            payload["expires_at_ms"] <= payload["issued_at_ms"] or
            payload["expires_at_ms"] - payload["issued_at_ms"] >
                60 * 60 * 1000 or
            payload["expires_at_ms"] > request["expires_at_ms"] or
            payload.get("nonce") != request["nonce"] or
            payload.get("request_sha256") != request["request_sha256"] or
            payload.get("base_image_reference") != base_image or
            payload.get("buildkit_image_reference") != buildkit_image or
            payload.get("observations") != request.get("observations") or
            payload.get("trust_bindings") != request.get("trust_bindings") or
            payload.get("review_authority") != closure.get("review_authority") or
            payload.get("reviewer_id") != closure.get("reviewer_id") or
            sha256_bytes(bindings["authorization"].payload) !=
                closure["authorization_reference"]["file_sha256"] or
            sha256_bytes(canonical_bytes(payload)) !=
                closure["authorization_reference"]["signed_payload_sha256"] or
            sha256_bytes(signature) !=
                closure["authorization_reference"]["signature_sha256"]):
        fail("review authorization is not exactly bound by the closure")
    for key in OUTPUT_FILENAMES:
        output = documents["output_" + key]
        reference = closure["outputs"][key]
        if (
                output.get("schema") != OUTPUT_SCHEMAS[key] or
                output.get("decision") != "GO" or
                output.get("issued_at_ms") != closure["issued_at_ms"] or
                output.get("expires_at_ms") != closure["expires_at_ms"] or
                sha256_bytes(bindings["output_" + key].payload) !=
                    reference["file_sha256"]):
            fail("review closure output document is not exactly bound")

    argv = (
        str(INSTALLED_VERIFIER), "--run", "--verify-closure",
        "--review-closure", str(inputs.closure_path),
        "--request", str(inputs.request_path),
        "--authorization", str(inputs.authorization_path),
        "--output-directory", str(inputs.output_directory),
        "--base-image", base_image, "--buildkit-image", buildkit_image,
    )
    code, stdout, duration_ms = _run_bounded(argv, timeout=180)
    if code != 0 or stdout != _expected_stdout(closure):
        fail("fixed review closure verifier did not return exact success")
    for binding in bindings.values():
        binding.reopen()
    invocation = {
        "argv_sha256": sha256_bytes(canonical_bytes(list(argv))),
        "stdout_sha256": sha256_bytes(stdout), "returncode": code,
        "duration_ms": duration_ms, "exact_success_output": True,
        "no_shell": True,
    }
    return VerificationSession(
        inputs=inputs, bindings=bindings, documents=documents,
        source_commit=expected_source_commit, base_image=base_image,
        buildkit_image=buildkit_image, verified_at_ms=now_ms,
        invocation=invocation, reopened_after_invocation=True)


def validate_verification_record(
        value: object, *, now_ms: Optional[int] = None) -> dict[str, Any]:
    if type(value) is not dict or set(value) != RECORD_FIELDS:
        fail("review closure verification record exact-field mismatch")
    _validate_false_authority(value, "review closure verification record")
    now = time.time_ns() // 1_000_000 if now_ms is None else now_ms
    if (
            value.get("schema") != SCHEMA or
            value.get("status") !=
                "VERIFIED_EXTERNALLY_SIGNED_REVIEW_CLOSURE" or
            type(value.get("verified_at_ms")) is not int or
            type(value.get("expires_at_ms")) is not int or
            value["verified_at_ms"] > now + MAX_CLOCK_SKEW_MS or
            value["expires_at_ms"] <= value["verified_at_ms"] or
            value["expires_at_ms"] <= now or
            value.get("reopened_after_invocation") is not True or
            value.get("reopened_at_gate_end") is not True or
            COMMIT.fullmatch(str(value.get("source_commit", ""))) is None or
            PINNED_IMAGE.fullmatch(
                str(value.get("base_image_reference", ""))) is None or
            PINNED_IMAGE.fullmatch(
                str(value.get("buildkit_image_reference", ""))) is None or
            not isinstance(value.get("output_directory"), str) or
            not str(value["output_directory"]).startswith("/")):
        fail("review closure verification record values are invalid")
    verifier = value.get("verifier")
    if (
            type(verifier) is not dict or
            set(verifier) != VERIFIER_RECORD_FIELDS or
            verifier.get("path") != str(INSTALLED_VERIFIER) or
            verifier.get("source_commit") != value["source_commit"] or
            verifier.get("file_sha256") != verifier.get("source_file_sha256") or
            verifier.get("mode") != "0755" or verifier.get("uid") != 0 or
            verifier.get("gid") != 0 or
            any(DIGEST.fullmatch(str(verifier.get(field, ""))) is None
                for field in (
                    "file_sha256", "source_file_sha256", "identity_sha256")) or
            not isinstance(verifier.get("source_path"), str) or
            not str(verifier["source_path"]).startswith("/") or
            os.path.normpath(str(verifier["source_path"])) !=
                verifier["source_path"]):
        fail("review closure verifier binding record is invalid")
    expected_extended_fields = {
        "closure": frozenset({
            "closure_sha256", "review_authority", "reviewer_id"}),
        "request": frozenset({"request_sha256", "nonce"}),
        "authorization": frozenset({
            "signed_payload_sha256", "signature_sha256",
            "review_authority", "reviewer_id"}),
    }
    for label in ("closure", "request", "authorization"):
        record = value.get(label)
        if (
                not isinstance(record, dict) or
                set(record) != FILE_RECORD_FIELDS | expected_extended_fields[label] or
                not isinstance(record.get("path"), str) or
                not str(record["path"]).startswith("/") or
                os.path.normpath(str(record["path"])) != record["path"] or
                any(DIGEST.fullmatch(str(record.get(field, ""))) is None
                    for field in ("file_sha256", "identity_sha256")) or
                record.get("uid") != 0 or record.get("gid") != 0):
            fail("review closure input binding record is invalid: " + label)
    if (
            DIGEST.fullmatch(str(value["closure"].get(
                "closure_sha256", ""))) is None or
            DIGEST.fullmatch(str(value["request"].get(
                "request_sha256", ""))) is None or
            NONCE.fullmatch(str(value["request"].get("nonce", ""))) is None or
            any(DIGEST.fullmatch(str(value["authorization"].get(field, "")))
                is None for field in (
                    "signed_payload_sha256", "signature_sha256"))):
        fail("review closure signed reference record is invalid")
    if value["closure"].get("mode") != "0400":
        fail("review closure file mode record is invalid")
    if value["request"].get("mode") not in {"0400", "0600"} or value[
            "authorization"].get("mode") not in {"0400", "0600"}:
        fail("review request/authorization mode record is invalid")
    fingerprint = validate_environment_fingerprint(
        value.get("environment_fingerprint"))
    if (
            fingerprint.get("source_commit") != value["source_commit"] or
            fingerprint.get("verifier_file_sha256") !=
                verifier["file_sha256"] or
            fingerprint.get("verifier_source_file_sha256") !=
                verifier["source_file_sha256"] or
            fingerprint.get("review_authority") !=
                value["closure"].get("review_authority") or
            fingerprint.get("review_authority") !=
                value["authorization"].get("review_authority") or
            fingerprint.get("reviewer_id") !=
                value["closure"].get("reviewer_id") or
            fingerprint.get("reviewer_id") !=
                value["authorization"].get("reviewer_id") or
            fingerprint["observations"]["base_image"]["repo_digest"] !=
                value["base_image_reference"] or
            fingerprint["observations"]["isolated_builder"][
                "repo_digest"] != value["buildkit_image_reference"]):
        fail("environment fingerprint is not bound to verification record")
    outputs = value.get("outputs")
    if type(outputs) is not dict or set(outputs) != set(OUTPUT_FILENAMES):
        fail("review closure output verification inventory is invalid")
    for key, record in outputs.items():
        if (
                type(record) is not dict or
                set(record) != FILE_RECORD_FIELDS | frozenset({"schema"}) or
                record.get("schema") != OUTPUT_SCHEMAS[key] or
                record.get("mode") != "0400" or record.get("uid") != 0 or
                record.get("gid") != 0 or
                any(DIGEST.fullmatch(str(record.get(field, ""))) is None
                    for field in ("file_sha256", "identity_sha256")) or
                record.get("path") != str(
                    Path(value["output_directory"]) / OUTPUT_FILENAMES[key])):
            fail("review closure output verification record is invalid")
    invocation = value.get("invocation")
    if (
            type(invocation) is not dict or set(invocation) != INVOCATION_FIELDS or
            invocation.get("returncode") != 0 or
            invocation.get("exact_success_output") is not True or
            invocation.get("no_shell") is not True or
            type(invocation.get("duration_ms")) is not int or
            invocation["duration_ms"] < 0 or
            any(DIGEST.fullmatch(str(invocation.get(field, ""))) is None
                for field in ("argv_sha256", "stdout_sha256"))):
        fail("review closure verifier invocation record is invalid")
    return value
