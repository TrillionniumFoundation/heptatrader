#!/usr/bin/env python3
"""Build a fail-closed, local/offline HeptaTrader delivery closure.

The closure binds a fixed set of delivery artifacts.  It deliberately cannot
certify broker access, PAPER or LIVE authority, a real systemd/IB run, object
store ingestion, retention enforcement, or source removal.
"""

from __future__ import annotations

import argparse
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
import math
import os
from pathlib import Path, PurePosixPath
import re
import secrets
import stat
import tempfile
from typing import Any, Iterator, Mapping

import run_execution_gateway_soak as soak_runner
import verify_heptatrader_clean_source_bundle as clean_source_verifier


CLOSURE_SCHEMA = "heptatrader.delivery-closure.v1"
SCHEMA = CLOSURE_SCHEMA
CLOSURE_VERSION = 1
VERSION = CLOSURE_VERSION
PROJECT_ID = "heptatrader-agent-os"
LOCAL_OFFLINE_SCOPE = "local-offline-only"
PRODUCTION_TRUST_STATUS = "pending-external"
MAX_CLOSURE_BYTES = 4 * 1024 * 1024
MAX_JSON_ARTIFACT_BYTES = 64 * 1024 * 1024
MAX_ARTIFACT_BYTES = 16 * 1024 * 1024 * 1024
HASH_CHUNK = 1024 * 1024
HEX64 = re.compile(r"^[0-9a-f]{64}$")
RELEASE_TOKEN = re.compile(r"^[0-9A-Za-z][0-9A-Za-z.+-]{0,126}$")
BASELINE_SCHEMA = "hepta.versioned-source-baseline.v1"
CLEAN_SOURCE_SCHEMA = "hepta.clean-source-bundle.v2"
SOAK_SCHEMA = soak_runner.SOAK_SCHEMA
EXPECTED_SOAK_ROUNDS = 8
EMPTY_SHA256 = "sha256:" + hashlib.sha256(b"").hexdigest()

# These roles are a schema constant, not caller-provided labels.  The order is
# canonical and is part of the closure contract.
REQUIRED_ARTIFACT_ROLES = (
    "no-git-soak-ibapi-off",
    "no-git-soak-ibapi-on",
    "source-baseline-manifest",
    "strict-source-bundle",
    "strict-source-bundle-manifest",
    "worktree-soak-ibapi-off",
    "worktree-soak-ibapi-on",
)
ARTIFACT_ROLES = REQUIRED_ARTIFACT_ROLES
JSON_ARTIFACT_ROLES = frozenset(
    set(REQUIRED_ARTIFACT_ROLES) - {"strict-source-bundle"})

SAFETY_BOUNDARIES = {
    "broker_connection_performed": False,
    "live_authorized": False,
    "object_store_ingestion_receipt_certified": False,
    "order_placement_performed": False,
    "paper_authorized": False,
    "real_ib_certified": False,
    "real_systemd_certified": False,
    "source_files_deleted": False,
    "source_removal_authorized": False,
}

PRODUCTION_TRUST_BOUNDARY = {
    "status": PRODUCTION_TRUST_STATUS,
    "key_count": 0,
    "object_store_ingestion_receipt_certified": False,
    "retention_enforcement_certified": False,
    "source_removal_authorized": False,
}

TOP_LEVEL_FIELDS = {
    "schema",
    "version",
    "project_id",
    "round",
    "release_version",
    "generated_at",
    "passed",
    "passed_scope",
    "artifact_roles",
    "artifacts",
    "safety_boundaries",
    "production_trust",
}
ARTIFACT_FIELDS = {"role", "path", "sha256", "size", "mode"}


class DeliveryClosureError(RuntimeError):
    """The requested closure operation is unsafe or violates the schema."""


@dataclass(frozen=True)
class StableRead:
    """Metadata captured while reading one descriptor-stable regular file."""

    identity: tuple[int, ...]
    size: int
    sha256: str
    mode: str
    data: bytes | None


def canonical_json(value: Any) -> bytes:
    """Return deterministic ASCII JSON without a trailing newline."""
    try:
        return json.dumps(
            value,
            ensure_ascii=True,
            separators=(",", ":"),
            sort_keys=True,
            allow_nan=False,
        ).encode("ascii")
    except (TypeError, ValueError) as error:
        raise DeliveryClosureError("closure is not canonical JSON data") from error


def _reject_constant(value: str) -> None:
    raise DeliveryClosureError(
        f"non-finite JSON value is forbidden: {value}")


def _finite_float(value: str) -> float:
    parsed = float(value)
    if not math.isfinite(parsed):
        raise DeliveryClosureError(
            f"non-finite JSON value is forbidden: {value}")
    return parsed


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise DeliveryClosureError(f"duplicate JSON key: {key}")
        result[key] = value
    return result


def strict_json(data: bytes, label: str = "delivery closure") -> Any:
    """Parse UTF-8 JSON while rejecting duplicate keys and non-finite values."""
    try:
        text = data.decode("utf-8", errors="strict")
        return json.loads(
            text,
            object_pairs_hook=_unique_object,
            parse_constant=_reject_constant,
            parse_float=_finite_float,
        )
    except DeliveryClosureError:
        raise
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as error:
        raise DeliveryClosureError(f"{label} is not strict JSON") from error


def _file_identity(value: os.stat_result) -> tuple[int, ...]:
    return (
        value.st_dev,
        value.st_ino,
        value.st_mode,
        value.st_nlink,
        value.st_uid,
        value.st_gid,
        value.st_size,
        value.st_mtime_ns,
        value.st_ctime_ns,
    )


def _directory_identity(value: os.stat_result) -> tuple[int, ...]:
    # Directory mtime/ctime change for unrelated children and are intentionally
    # excluded.  Device/inode/mode/ownership still detect path replacement.
    return (
        value.st_dev,
        value.st_ino,
        value.st_mode,
        value.st_uid,
        value.st_gid,
    )


def _mode_string(metadata: os.stat_result) -> str:
    mode = stat.S_IMODE(metadata.st_mode)
    if mode & 0o022:
        raise DeliveryClosureError(
            "group- or world-writable files are forbidden")
    if mode & 0o7000:
        raise DeliveryClosureError(
            "set-id and sticky artifact modes are forbidden")
    return f"{mode:04o}"


def _validate_trusted_directory(
    metadata: os.stat_result,
    *,
    label: str,
    expected_owner: int | None = None,
) -> int:
    """Require a non-set-id directory that other principals cannot mutate."""
    mode = stat.S_IMODE(metadata.st_mode)
    if (not stat.S_ISDIR(metadata.st_mode) or
            mode & 0o022 or mode & 0o7000):
        raise DeliveryClosureError(
            f"{label} must be a trusted non-writable directory")
    if expected_owner is not None and metadata.st_uid != expected_owner:
        raise DeliveryClosureError(
            f"{label} owner differs from its trust boundary")
    return metadata.st_uid


def _close_descriptors(descriptors: Iterator[int]) -> None:
    """Best-effort close every descriptor without masking an earlier error."""
    for descriptor in descriptors:
        try:
            os.close(descriptor)
        except OSError:
            # close(2) errors have platform-dependent descriptor state.  Do
            # not retry and do not prevent the remaining descriptors from
            # being closed.
            pass


def normalized_relative_path(value: Any, label: str = "artifact path") -> str:
    """Validate one portable, lexical POSIX path below an artifact root."""
    if isinstance(value, Path):
        value = value.as_posix()
    if (not isinstance(value, str) or not value or not value.isascii() or
            "\\" in value or
            any(ord(character) < 0x20 or ord(character) == 0x7f
                for character in value)):
        raise DeliveryClosureError(
            f"{label} is not a normalized relative path")
    candidate = PurePosixPath(value)
    if (candidate.is_absolute() or value in {".", ".."} or
            candidate.as_posix() != value or
            any(part in {"", ".", ".."} for part in candidate.parts)):
        raise DeliveryClosureError(
            f"{label} is not a normalized relative path")
    return value


def _absolute_lexical(path: os.PathLike[str] | str) -> Path:
    try:
        value = os.fspath(path)
    except TypeError as error:
        raise DeliveryClosureError("path is not filesystem-compatible") from error
    if not value or "\0" in value:
        raise DeliveryClosureError("path is empty or contains NUL")
    return Path(os.path.abspath(value))


def stable_read(
    path: os.PathLike[str] | str,
    *,
    limit: int | None,
    capture: bool,
    require_trusted_parent: bool = False,
) -> StableRead:
    """Read a regular file through an anchored, no-follow descriptor walk."""
    absolute = _absolute_lexical(path)
    parts = absolute.parts
    if len(parts) < 2 or absolute == Path("/"):
        raise DeliveryClosureError(f"file path is invalid: {path}")
    if (limit is not None and
            (not isinstance(limit, int) or isinstance(limit, bool) or
             limit < 0)):
        raise DeliveryClosureError("stable-read size limit is invalid")

    directory_flags = (
        os.O_RDONLY |
        getattr(os, "O_DIRECTORY", 0) |
        getattr(os, "O_CLOEXEC", 0) |
        getattr(os, "O_NOFOLLOW", 0)
    )
    file_flags = (
        os.O_RDONLY |
        getattr(os, "O_NONBLOCK", 0) |
        getattr(os, "O_CLOEXEC", 0) |
        getattr(os, "O_NOFOLLOW", 0)
    )
    descriptors: list[int] = []
    components: list[tuple[str, tuple[int, ...]]] = []
    file_descriptor = -1
    try:
        parent_descriptor = os.open("/", directory_flags)
        descriptors.append(parent_descriptor)
        for component in parts[1:-1]:
            before = os.stat(
                component,
                dir_fd=parent_descriptor,
                follow_symlinks=False,
            )
            if (stat.S_ISLNK(before.st_mode) or
                    not stat.S_ISDIR(before.st_mode)):
                raise DeliveryClosureError(
                    f"path ancestor is not a no-follow directory: {path}")
            child_descriptor = os.open(
                component, directory_flags, dir_fd=parent_descriptor)
            descriptors.append(child_descriptor)
            opened = os.fstat(child_descriptor)
            if _directory_identity(before) != _directory_identity(opened):
                raise DeliveryClosureError(
                    f"path ancestor changed while opening: {path}")
            components.append((component, _directory_identity(before)))
            parent_descriptor = child_descriptor

        parent_owner: int | None = None
        if require_trusted_parent:
            parent_owner = _validate_trusted_directory(
                os.fstat(parent_descriptor),
                label="file parent",
            )

        name = parts[-1]
        before = os.stat(
            name, dir_fd=parent_descriptor, follow_symlinks=False)
        if (stat.S_ISLNK(before.st_mode) or
                not stat.S_ISREG(before.st_mode)):
            raise DeliveryClosureError(
                f"path is not a no-follow regular file: {path}")
        if before.st_nlink != 1:
            raise DeliveryClosureError(
                f"file must have exactly one hard link: {path}")
        if parent_owner is not None and before.st_uid != parent_owner:
            raise DeliveryClosureError(
                f"file owner differs from trusted parent: {path}")
        mode = _mode_string(before)
        if limit is not None and before.st_size > limit:
            raise DeliveryClosureError(f"file exceeds size limit: {path}")

        file_descriptor = os.open(
            name, file_flags, dir_fd=parent_descriptor)
        opened = os.fstat(file_descriptor)
        if _file_identity(before) != _file_identity(opened):
            raise DeliveryClosureError(
                f"file changed while opening: {path}")

        digest = hashlib.sha256()
        chunks: list[bytes] = []
        size = 0
        while True:
            chunk = os.read(file_descriptor, HASH_CHUNK)
            if not chunk:
                break
            digest.update(chunk)
            size += len(chunk)
            if limit is not None and size > limit:
                raise DeliveryClosureError(
                    f"file exceeds size limit: {path}")
            if capture:
                chunks.append(chunk)

        after_descriptor = os.fstat(file_descriptor)
        after_path = os.stat(
            name, dir_fd=parent_descriptor, follow_symlinks=False)
        if (_file_identity(opened) != _file_identity(after_descriptor) or
                _file_identity(after_descriptor) !=
                _file_identity(after_path) or
                size != opened.st_size):
            raise DeliveryClosureError(
                f"file changed during read: {path}")

        for index, (component, expected) in enumerate(components):
            current = os.stat(
                component,
                dir_fd=descriptors[index],
                follow_symlinks=False,
            )
            if _directory_identity(current) != expected:
                raise DeliveryClosureError(
                    f"path ancestor changed during read: {path}")

        return StableRead(
            identity=_file_identity(opened),
            size=size,
            sha256=digest.hexdigest(),
            mode=mode,
            data=b"".join(chunks) if capture else None,
        )
    except OSError as error:
        raise DeliveryClosureError(
            f"path is unsafe or unstable: {path}") from error
    finally:
        if file_descriptor >= 0:
            _close_descriptors(iter((file_descriptor,)))
        _close_descriptors(reversed(descriptors))


def stable_artifact(
    artifact_root: os.PathLike[str] | str,
    relative_path: Any,
) -> StableRead:
    relative = normalized_relative_path(relative_path)
    root = _absolute_lexical(artifact_root)
    return stable_read(
        root / Path(relative),
        limit=MAX_ARTIFACT_BYTES,
        capture=False,
        require_trusted_parent=True,
    )


def _normalize_generated_at(value: Any) -> str:
    if not isinstance(value, str) or not value or not value.isascii():
        raise DeliveryClosureError(
            "generated_at must be an ASCII RFC3339 timestamp")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as error:
        raise DeliveryClosureError(
            "generated_at must be an RFC3339 timestamp") from error
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise DeliveryClosureError(
            "generated_at must include a timezone")
    normalized = parsed.astimezone(timezone.utc).isoformat().replace(
        "+00:00", "Z")
    if value != normalized:
        raise DeliveryClosureError(
            "generated_at must be normalized UTC RFC3339")
    return value


def _validate_release_identity(
    project_id: Any,
    round_number: Any,
    release_version: Any,
) -> None:
    if project_id != PROJECT_ID or not isinstance(project_id, str):
        raise DeliveryClosureError("unsupported delivery closure project")
    if (not isinstance(round_number, int) or
            isinstance(round_number, bool) or round_number <= 0):
        raise DeliveryClosureError("round must be a positive integer")
    if (not isinstance(release_version, str) or
            not release_version.isascii() or
            RELEASE_TOKEN.fullmatch(release_version) is None or
            not release_version.endswith(f"-round{round_number}")):
        raise DeliveryClosureError(
            "release_version must be a safe token ending in the exact round")


def validate_contract_structure(value: Any) -> dict[str, Any]:
    """Validate the exact static schema, without reading bound artifacts."""
    if not isinstance(value, dict) or set(value) != TOP_LEVEL_FIELDS:
        raise DeliveryClosureError(
            "delivery closure fields do not exactly match schema")
    if (value["schema"] != CLOSURE_SCHEMA or
            value["version"] != CLOSURE_VERSION or
            not isinstance(value["version"], int) or
            isinstance(value["version"], bool)):
        raise DeliveryClosureError("unsupported delivery closure schema")
    _validate_release_identity(
        value["project_id"], value["round"], value["release_version"])
    _normalize_generated_at(value["generated_at"])
    if value["passed"] is not True:
        raise DeliveryClosureError(
            "a delivery closure may only be published after all checks pass")
    if value["passed_scope"] != LOCAL_OFFLINE_SCOPE:
        raise DeliveryClosureError(
            "passed may only represent the local/offline scope")
    if value["artifact_roles"] != list(REQUIRED_ARTIFACT_ROLES):
        raise DeliveryClosureError(
            "artifact roles do not exactly match the fixed schema roles")

    artifacts = value["artifacts"]
    if (not isinstance(artifacts, list) or
            len(artifacts) != len(REQUIRED_ARTIFACT_ROLES)):
        raise DeliveryClosureError(
            "artifact bindings do not match the fixed role count")
    seen_paths: set[str] = set()
    for expected_role, artifact in zip(
            REQUIRED_ARTIFACT_ROLES, artifacts, strict=True):
        if not isinstance(artifact, dict) or set(artifact) != ARTIFACT_FIELDS:
            raise DeliveryClosureError(
                "artifact fields do not exactly match schema")
        if artifact["role"] != expected_role:
            raise DeliveryClosureError(
                "artifact bindings are not in fixed canonical role order")
        relative = normalized_relative_path(artifact["path"])
        if relative in seen_paths:
            raise DeliveryClosureError(
                "one artifact path cannot satisfy multiple fixed roles")
        seen_paths.add(relative)
        if (not isinstance(artifact["sha256"], str) or
                HEX64.fullmatch(artifact["sha256"]) is None):
            raise DeliveryClosureError("artifact sha256 is invalid")
        if (not isinstance(artifact["size"], int) or
                isinstance(artifact["size"], bool) or
                artifact["size"] < 0 or
                artifact["size"] > MAX_ARTIFACT_BYTES):
            raise DeliveryClosureError("artifact size is invalid")
        mode = artifact["mode"]
        if (not isinstance(mode, str) or
                re.fullmatch(r"0[0-7]{3}", mode) is None or
                int(mode, 8) & 0o022 or
                int(mode, 8) & 0o7000):
            raise DeliveryClosureError("artifact mode is invalid or unsafe")

    boundaries = value["safety_boundaries"]
    if (not isinstance(boundaries, dict) or
            set(boundaries) != set(SAFETY_BOUNDARIES) or
            any(boundaries[field] is not False
                for field in SAFETY_BOUNDARIES)):
        raise DeliveryClosureError(
            "delivery closure safety boundary drift")

    trust = value["production_trust"]
    if not isinstance(trust, dict) or set(trust) != set(
            PRODUCTION_TRUST_BOUNDARY):
        raise DeliveryClosureError(
            "production trust fields do not exactly match schema")
    if (trust["status"] != PRODUCTION_TRUST_STATUS or
            not isinstance(trust["status"], str) or
            not isinstance(trust["key_count"], int) or
            isinstance(trust["key_count"], bool) or
            trust["key_count"] != 0):
        raise DeliveryClosureError(
            "production trust must remain pending-external with key_count=0")
    for field in (
            "object_store_ingestion_receipt_certified",
            "retention_enforcement_certified",
            "source_removal_authorized"):
        if trust[field] is not False:
            raise DeliveryClosureError(
                "pending external trust cannot certify receipt, retention, "
                "or source removal")
    return value


def _artifact_mapping(
    artifact_paths: Mapping[str, os.PathLike[str] | str],
) -> dict[str, str]:
    if not isinstance(artifact_paths, Mapping):
        raise DeliveryClosureError("artifact paths must be a role mapping")
    if set(artifact_paths) != set(REQUIRED_ARTIFACT_ROLES):
        missing = sorted(set(REQUIRED_ARTIFACT_ROLES) - set(artifact_paths))
        extra = sorted(set(artifact_paths) - set(REQUIRED_ARTIFACT_ROLES))
        raise DeliveryClosureError(
            f"artifact roles must be fixed; missing={missing} extra={extra}")
    normalized: dict[str, str] = {}
    seen: set[str] = set()
    for role in REQUIRED_ARTIFACT_ROLES:
        relative = normalized_relative_path(
            artifact_paths[role], f"{role} artifact path")
        if relative in seen:
            raise DeliveryClosureError(
                "one artifact path cannot satisfy multiple fixed roles")
        seen.add(relative)
        normalized[role] = relative
    return normalized


def build_closure(
    artifact_root: os.PathLike[str] | str,
    artifact_paths: Mapping[str, os.PathLike[str] | str],
    *,
    round_number: int,
    release_version: str,
    generated_at: str | None = None,
    project_id: str = PROJECT_ID,
) -> dict[str, Any]:
    """Build a closure only after the fixed evidence lineage validates."""
    _validate_release_identity(project_id, round_number, release_version)
    if generated_at is None:
        generated_at = (
            datetime.now(timezone.utc)
            .isoformat()
            .replace("+00:00", "Z")
        )
    generated_at = _normalize_generated_at(generated_at)
    paths = _artifact_mapping(artifact_paths)

    artifacts: list[dict[str, Any]] = []
    with open_artifact_set(artifact_root, paths) as opened:
        validate_delivery_evidence(
            opened,
            round_number=round_number,
            release_version=release_version,
        )
        for role in REQUIRED_ARTIFACT_ROLES:
            captured = opened[role].snapshot
            artifacts.append({
                "role": role,
                "path": paths[role],
                "sha256": captured.sha256,
                "size": captured.size,
                "mode": captured.mode,
            })

    closure = {
        "schema": CLOSURE_SCHEMA,
        "version": CLOSURE_VERSION,
        "project_id": project_id,
        "round": round_number,
        "release_version": release_version,
        "generated_at": generated_at,
        "passed": True,
        "passed_scope": LOCAL_OFFLINE_SCOPE,
        "artifact_roles": list(REQUIRED_ARTIFACT_ROLES),
        "artifacts": artifacts,
        "safety_boundaries": dict(SAFETY_BOUNDARIES),
        "production_trust": dict(PRODUCTION_TRUST_BOUNDARY),
    }
    return validate_contract_structure(closure)


build_delivery_closure = build_closure


@dataclass(frozen=True)
class AnchoredDirectory:
    """A no-follow directory descriptor with a revalidatable absolute path."""

    path: Path
    descriptors: tuple[int, ...]
    components: tuple[tuple[str, tuple[int, ...]], ...]

    @property
    def descriptor(self) -> int:
        return self.descriptors[-1]

    def verify(self) -> None:
        try:
            for index, (component, expected) in enumerate(self.components):
                current = os.stat(
                    component,
                    dir_fd=self.descriptors[index],
                    follow_symlinks=False,
                )
                if _directory_identity(current) != expected:
                    raise DeliveryClosureError(
                        f"directory changed while anchored: {self.path}")
        except OSError as error:
            raise DeliveryClosureError(
                f"directory path changed while anchored: {self.path}") from error


@contextmanager
def _anchored_directory(
    path: os.PathLike[str] | str,
) -> Iterator[AnchoredDirectory]:
    """Yield a no-follow directory fd and revalidate its full path."""
    absolute = _absolute_lexical(path)
    directory_flags = (
        os.O_RDONLY |
        getattr(os, "O_DIRECTORY", 0) |
        getattr(os, "O_CLOEXEC", 0) |
        getattr(os, "O_NOFOLLOW", 0)
    )
    descriptors: list[int] = []
    components: list[tuple[str, tuple[int, ...]]] = []
    try:
        try:
            current = os.open("/", directory_flags)
            descriptors.append(current)
            for component in absolute.parts[1:]:
                before = os.stat(
                    component, dir_fd=current, follow_symlinks=False)
                if (stat.S_ISLNK(before.st_mode) or
                        not stat.S_ISDIR(before.st_mode)):
                    raise DeliveryClosureError(
                        f"output directory path is unsafe: {path}")
                child = os.open(
                    component, directory_flags, dir_fd=current)
                descriptors.append(child)
                opened = os.fstat(child)
                if (_directory_identity(before) !=
                        _directory_identity(opened)):
                    raise DeliveryClosureError(
                        f"output directory changed while opening: {path}")
                components.append((
                    component, _directory_identity(before)))
                current = child
            anchored = AnchoredDirectory(
                path=absolute,
                descriptors=tuple(descriptors),
                components=tuple(components),
            )
            anchored.verify()
        except OSError as error:
            raise DeliveryClosureError(
                f"output directory path is unsafe or unstable: "
                f"{path}") from error
        yield anchored
    finally:
        _close_descriptors(reversed(descriptors))


@dataclass(frozen=True)
class OpenedArtifact:
    """One artifact kept open for the lifetime of a semantic evidence check."""

    role: str
    relative_path: str
    descriptor: int
    snapshot: StableRead


def _read_artifact_descriptor(
    descriptor: int,
    *,
    path: str,
    limit: int,
    capture: bool,
) -> StableRead:
    before = os.fstat(descriptor)
    if (not stat.S_ISREG(before.st_mode) or before.st_nlink != 1 or
            before.st_size < 0 or before.st_size > limit):
        raise DeliveryClosureError(
            f"artifact descriptor is not a bounded single-link file: {path}")
    mode = _mode_string(before)
    os.lseek(descriptor, 0, os.SEEK_SET)
    digest = hashlib.sha256()
    chunks: list[bytes] = []
    size = 0
    while True:
        chunk = os.read(descriptor, HASH_CHUNK)
        if not chunk:
            break
        digest.update(chunk)
        size += len(chunk)
        if size > limit:
            raise DeliveryClosureError(
                f"artifact exceeds size limit: {path}")
        if capture:
            chunks.append(chunk)
    after = os.fstat(descriptor)
    if _file_identity(before) != _file_identity(after) or size != before.st_size:
        raise DeliveryClosureError(
            f"artifact changed while reading descriptor: {path}")
    return StableRead(
        identity=_file_identity(before),
        size=size,
        sha256=digest.hexdigest(),
        mode=mode,
        data=b"".join(chunks) if capture else None,
    )


def _open_anchored_artifact(
    anchor: AnchoredDirectory,
    *,
    role: str,
    relative_path: str,
    expected_owner: int,
) -> OpenedArtifact:
    parts = PurePosixPath(relative_path).parts
    directory_flags = (
        os.O_RDONLY |
        getattr(os, "O_DIRECTORY", 0) |
        getattr(os, "O_CLOEXEC", 0) |
        getattr(os, "O_NOFOLLOW", 0)
    )
    file_flags = (
        os.O_RDONLY |
        getattr(os, "O_NONBLOCK", 0) |
        getattr(os, "O_CLOEXEC", 0) |
        getattr(os, "O_NOFOLLOW", 0)
    )
    parent_descriptor = anchor.descriptor
    child_descriptors: list[int] = []
    components: list[tuple[int, str, tuple[int, ...]]] = []
    descriptor = -1
    try:
        for component in parts[:-1]:
            before = os.stat(
                component,
                dir_fd=parent_descriptor,
                follow_symlinks=False,
            )
            if (stat.S_ISLNK(before.st_mode) or
                    not stat.S_ISDIR(before.st_mode)):
                raise DeliveryClosureError(
                    f"artifact ancestor is unsafe: {relative_path}")
            _validate_trusted_directory(
                before,
                label=f"artifact ancestor {relative_path}",
                expected_owner=expected_owner,
            )
            child = os.open(
                component, directory_flags, dir_fd=parent_descriptor)
            child_descriptors.append(child)
            opened = os.fstat(child)
            if _directory_identity(before) != _directory_identity(opened):
                raise DeliveryClosureError(
                    f"artifact ancestor changed while opening: "
                    f"{relative_path}")
            components.append((
                parent_descriptor,
                component,
                _directory_identity(before),
            ))
            parent_descriptor = child

        name = parts[-1]
        before = os.stat(
            name, dir_fd=parent_descriptor, follow_symlinks=False)
        if (stat.S_ISLNK(before.st_mode) or
                not stat.S_ISREG(before.st_mode)):
            raise DeliveryClosureError(
                f"artifact is not a no-follow regular file: {relative_path}")
        if before.st_nlink != 1:
            raise DeliveryClosureError(
                f"artifact must have exactly one hard link: {relative_path}")
        if before.st_uid != expected_owner:
            raise DeliveryClosureError(
                f"artifact owner differs from its trust boundary: "
                f"{relative_path}")
        _mode_string(before)
        limit = (
            MAX_JSON_ARTIFACT_BYTES
            if role in JSON_ARTIFACT_ROLES
            else min(
                MAX_ARTIFACT_BYTES,
                clean_source_verifier.MAX_BUNDLE_BYTES,
            )
        )
        if before.st_size > limit:
            raise DeliveryClosureError(
                f"artifact exceeds size limit: {relative_path}")
        descriptor = os.open(
            name, file_flags, dir_fd=parent_descriptor)
        opened = os.fstat(descriptor)
        if _file_identity(before) != _file_identity(opened):
            raise DeliveryClosureError(
                f"artifact changed while opening: {relative_path}")
        snapshot = _read_artifact_descriptor(
            descriptor,
            path=relative_path,
            limit=limit,
            capture=role in JSON_ARTIFACT_ROLES,
        )
        after_path = os.stat(
            name, dir_fd=parent_descriptor, follow_symlinks=False)
        if _file_identity(after_path) != snapshot.identity:
            raise DeliveryClosureError(
                f"artifact path changed during read: {relative_path}")
        for parent, component, expected in components:
            current = os.stat(
                component, dir_fd=parent, follow_symlinks=False)
            if _directory_identity(current) != expected:
                raise DeliveryClosureError(
                    f"artifact ancestor changed during read: "
                    f"{relative_path}")
        return OpenedArtifact(
            role=role,
            relative_path=relative_path,
            descriptor=descriptor,
            snapshot=snapshot,
        )
    except OSError as error:
        if descriptor >= 0:
            _close_descriptors(iter((descriptor,)))
            descriptor = -1
        raise DeliveryClosureError(
            f"artifact path is unsafe or unstable: {relative_path}") from error
    except Exception:
        if descriptor >= 0:
            _close_descriptors(iter((descriptor,)))
            descriptor = -1
        raise
    finally:
        _close_descriptors(reversed(child_descriptors))


def _anchored_artifact_identity(
    anchor: AnchoredDirectory,
    relative_path: str,
    expected_owner: int,
) -> tuple[int, ...]:
    parts = PurePosixPath(relative_path).parts
    directory_flags = (
        os.O_RDONLY |
        getattr(os, "O_DIRECTORY", 0) |
        getattr(os, "O_CLOEXEC", 0) |
        getattr(os, "O_NOFOLLOW", 0)
    )
    parent_descriptor = anchor.descriptor
    descriptors: list[int] = []
    try:
        for component in parts[:-1]:
            before = os.stat(
                component,
                dir_fd=parent_descriptor,
                follow_symlinks=False,
            )
            if (stat.S_ISLNK(before.st_mode) or
                    not stat.S_ISDIR(before.st_mode)):
                raise DeliveryClosureError(
                    f"artifact ancestor is unsafe: {relative_path}")
            _validate_trusted_directory(
                before,
                label=f"artifact ancestor {relative_path}",
                expected_owner=expected_owner,
            )
            child = os.open(
                component, directory_flags, dir_fd=parent_descriptor)
            descriptors.append(child)
            opened = os.fstat(child)
            if _directory_identity(before) != _directory_identity(opened):
                raise DeliveryClosureError(
                    f"artifact ancestor changed while revalidating: "
                    f"{relative_path}")
            parent_descriptor = child
        metadata = os.stat(
            parts[-1],
            dir_fd=parent_descriptor,
            follow_symlinks=False,
        )
        if (stat.S_ISLNK(metadata.st_mode) or
                not stat.S_ISREG(metadata.st_mode)):
            raise DeliveryClosureError(
                f"artifact path is no longer a regular file: {relative_path}")
        if metadata.st_uid != expected_owner:
            raise DeliveryClosureError(
                f"artifact owner differs from its trust boundary: "
                f"{relative_path}")
        return _file_identity(metadata)
    except OSError as error:
        raise DeliveryClosureError(
            f"artifact path changed during evidence validation: "
            f"{relative_path}") from error
    finally:
        _close_descriptors(reversed(descriptors))


def _revalidate_opened_artifacts(
    anchor: AnchoredDirectory,
    opened: Mapping[str, OpenedArtifact],
    expected_owner: int,
) -> None:
    anchor.verify()
    for role in REQUIRED_ARTIFACT_ROLES:
        artifact = opened[role]
        current_descriptor = os.fstat(artifact.descriptor)
        if _file_identity(current_descriptor) != artifact.snapshot.identity:
            raise DeliveryClosureError(
                f"artifact descriptor changed during evidence validation: "
                f"{role}")
        current_path = _anchored_artifact_identity(
            anchor, artifact.relative_path, expected_owner)
        if current_path != artifact.snapshot.identity:
            raise DeliveryClosureError(
                f"artifact path changed during evidence validation: {role}")
    anchor.verify()


@contextmanager
def open_artifact_set(
    artifact_root: os.PathLike[str] | str,
    artifact_paths: Mapping[str, os.PathLike[str] | str],
) -> Iterator[dict[str, OpenedArtifact]]:
    """Open the complete fixed-role set below one private anchored root."""
    paths = _artifact_mapping(artifact_paths)
    opened: dict[str, OpenedArtifact] = {}
    with _anchored_directory(artifact_root) as anchor:
        root_metadata = os.fstat(anchor.descriptor)
        root_owner = _validate_trusted_directory(
            root_metadata,
            label="artifact root",
        )
        try:
            seen_inodes: set[tuple[int, int]] = set()
            for role in REQUIRED_ARTIFACT_ROLES:
                artifact = _open_anchored_artifact(
                    anchor,
                    role=role,
                    relative_path=paths[role],
                    expected_owner=root_owner,
                )
                inode = (
                    artifact.snapshot.identity[0],
                    artifact.snapshot.identity[1],
                )
                if inode in seen_inodes:
                    _close_descriptors(iter((artifact.descriptor,)))
                    raise DeliveryClosureError(
                        "one artifact inode cannot satisfy multiple roles")
                seen_inodes.add(inode)
                opened[role] = artifact
            _revalidate_opened_artifacts(anchor, opened, root_owner)
            yield opened
            _revalidate_opened_artifacts(anchor, opened, root_owner)
        finally:
            _close_descriptors(iter(
                artifact.descriptor for artifact in opened.values()))


BASELINE_FIELDS = {
    "blocked_reason",
    "clean_checkout_certified",
    "excluded_unsafe_tree",
    "generated_at",
    "git_head",
    "live_authorized",
    "paper_authorized",
    "release_authorized",
    "schema",
    "source_baseline_frozen",
    "source_manifest",
    "version",
    "worktree_status_entry_count",
}
SOAK_FIELDS = {
    "all_invariants_certified",
    "binary_inputs",
    "build_dir",
    "completed_rounds",
    "evidence_contracts",
    "expected_invariants_per_round",
    "generated_at_unix_ms",
    "git_head",
    "limits",
    "minimum_observed_processes",
    "passed",
    "provenance",
    "requested_rounds",
    "rounds",
    "schema",
    "soak_profile",
}
REQUIRED_SOAK_INVARIANTS = dict(soak_runner.SOAK_EXPECTED_INVARIANTS)
SOAK_BINARY_NAMES = tuple(soak_runner.SOAK_BINARY_NAMES)
SOAK_EVIDENCE_CONTRACTS = tuple(soak_runner.SOAK_EVIDENCE_CONTRACTS)
SOAK_MINIMUM_OBSERVED_PROCESSES = dict(
    soak_runner.SOAK_MINIMUM_OBSERVED_PROCESSES)
SOAK_DEFAULT_LIMITS = dict(soak_runner.SOAK_DEFAULT_LIMITS)
SOAK_SOURCE_BINARY_BINDING = soak_runner.SOAK_SOURCE_BINARY_BINDING

FILE_RECORD_FIELDS = {"mode", "path", "sha256", "size"}
SOAK_ROUND_FIELDS = {
    "checks",
    "no_orphan_descendants",
    "passed",
    "process_tree_observed",
    "resource_growth_within_limit",
    "round",
    "runner_growth",
}
SOAK_RUNNER_GROWTH_FIELDS = {"fds", "rss_kb", "threads"}
SOAK_CHECK_FIELDS = {
    "binary",
    "duration_ms",
    "evidence_contract_satisfied",
    "evidence_fields",
    "evidence_line_count",
    "evidence_observed",
    "evidence_parse_error",
    "evidence_prefix",
    "exit_code",
    "expected_evidence_fields",
    "high_water",
    "mismatched_evidence_fields",
    "missing_evidence_fields",
    "output_limit_exceeded",
    "output_sha256",
    "output_size_bytes",
    "output_tail_redacted",
    "passed",
    "pinned_binary",
    "post_cleanup_process_group_members",
    "process_group_cleanup_succeeded",
    "process_resources_within_limit",
    "remaining_process_group_members",
    "timed_out",
    "unexpected_evidence_fields",
}
SOAK_HIGH_WATER_FIELDS = {"fds", "processes", "rss_kb", "threads"}


def _json_artifact(
    opened: Mapping[str, OpenedArtifact],
    role: str,
) -> dict[str, Any]:
    data = opened[role].snapshot.data
    if data is None:
        raise DeliveryClosureError(
            f"JSON artifact was not captured: {role}")
    parsed = strict_json(data, role)
    if not isinstance(parsed, dict):
        raise DeliveryClosureError(
            f"JSON artifact must be an object: {role}")
    return parsed


def _validate_manifest_records(
    value: Any,
    *,
    label: str,
    portable_modes: bool,
) -> dict[str, Any]:
    if (not isinstance(value, dict) or
            set(value) != {"file_count", "files", "sha256"}):
        raise DeliveryClosureError(
            f"{label} source manifest fields are invalid")
    records = value["files"]
    if (not isinstance(records, list) or
            not isinstance(value["file_count"], int) or
            isinstance(value["file_count"], bool) or
            value["file_count"] <= 0 or
            len(records) != value["file_count"]):
        raise DeliveryClosureError(
            f"{label} source manifest count is invalid")
    seen: set[str] = set()
    normalized_records: list[dict[str, Any]] = []
    for record in records:
        if not isinstance(record, dict) or set(record) != {
                "mode", "path", "sha256", "size"}:
            raise DeliveryClosureError(
                f"{label} source manifest record is invalid")
        relative = normalized_relative_path(
            record["path"], f"{label} source path")
        if relative in seen:
            raise DeliveryClosureError(
                f"{label} source manifest contains duplicate paths")
        seen.add(relative)
        mode = record["mode"]
        digest = record["sha256"]
        size = record["size"]
        if (not isinstance(mode, str) or
                re.fullmatch(r"0[0-7]{3}", mode) is None or
                not isinstance(digest, str) or
                not digest.startswith("sha256:") or
                HEX64.fullmatch(digest[7:]) is None or
                not isinstance(size, int) or isinstance(size, bool) or
                size < 0):
            raise DeliveryClosureError(
                f"{label} source manifest metadata is invalid")
        if int(mode, 8) & 0o7002:
            raise DeliveryClosureError(
                f"{label} source manifest mode is unsafe")
        portable_mode = "0755" if int(mode, 8) & 0o111 else "0644"
        if portable_modes and mode != portable_mode:
            raise DeliveryClosureError(
                f"{label} source manifest mode is not portable")
        normalized = dict(record)
        normalized["mode"] = portable_mode
        normalized_records.append(normalized)
    canonical = canonical_json(records)
    if value["sha256"] != "sha256:" + hashlib.sha256(canonical).hexdigest():
        raise DeliveryClosureError(
            f"{label} source manifest digest is invalid")
    normalized_canonical = canonical_json(normalized_records)
    return {
        "file_count": len(normalized_records),
        "files": normalized_records,
        "sha256":
            "sha256:" +
            hashlib.sha256(normalized_canonical).hexdigest(),
    }


def _validate_baseline(
    baseline: dict[str, Any],
    *,
    round_number: int,
    release_version: str,
) -> dict[str, Any]:
    if set(baseline) != BASELINE_FIELDS:
        raise DeliveryClosureError(
            "source baseline fields do not exactly match schema")
    if (baseline["schema"] != BASELINE_SCHEMA or
            baseline["version"] != release_version or
            baseline["source_baseline_frozen"] is not True or
            baseline["release_authorized"] is not False or
            baseline["paper_authorized"] is not False or
            baseline["live_authorized"] is not False or
            baseline["excluded_unsafe_tree"] !=
            "compat/unsafe-direct-broker"):
        raise DeliveryClosureError(
            "source baseline safety or release boundary drift")
    _validate_release_identity(
        PROJECT_ID, round_number, baseline["version"])
    git_head = baseline["git_head"]
    if (not isinstance(git_head, str) or
            re.fullmatch(r"[0-9a-f]{40}", git_head) is None):
        raise DeliveryClosureError("source baseline Git identity is invalid")
    status_count = baseline["worktree_status_entry_count"]
    clean = baseline["clean_checkout_certified"]
    if (not isinstance(baseline["generated_at"], str) or
            not baseline["generated_at"] or
            not isinstance(status_count, int) or
            isinstance(status_count, bool) or status_count < 0 or
            not isinstance(clean, bool)):
        raise DeliveryClosureError(
            "source baseline worktree evidence is invalid")
    if clean:
        if status_count != 0 or baseline["blocked_reason"] is not None:
            raise DeliveryClosureError(
                "clean source baseline evidence is inconsistent")
    elif (status_count == 0 or baseline["blocked_reason"] !=
            "VERSION_CONTROL_COMMIT_REQUIRED"):
        raise DeliveryClosureError(
            "dirty source baseline evidence is inconsistent")
    source_manifest = _validate_manifest_records(
        baseline["source_manifest"],
        label="baseline",
        portable_modes=True,
    )
    if source_manifest != baseline["source_manifest"]:
        raise DeliveryClosureError(
            "source baseline is not in canonical portable form")
    return {
        "git_head": git_head,
        "source_manifest": source_manifest,
        "clean_checkout_certified": clean,
        "release_authorized": False,
        "blocked_reason": baseline["blocked_reason"],
    }


def _write_private_copy(
    artifact: OpenedArtifact,
    destination: Path,
) -> None:
    flags = (
        os.O_WRONLY |
        os.O_CREAT |
        os.O_EXCL |
        getattr(os, "O_CLOEXEC", 0) |
        getattr(os, "O_NOFOLLOW", 0)
    )
    descriptor = os.open(destination, flags, 0o600)
    digest = hashlib.sha256()
    size = 0
    try:
        os.fchmod(descriptor, 0o600)
        os.lseek(artifact.descriptor, 0, os.SEEK_SET)
        while True:
            chunk = os.read(artifact.descriptor, HASH_CHUNK)
            if not chunk:
                break
            digest.update(chunk)
            size += len(chunk)
            _write_all(descriptor, chunk)
        os.fsync(descriptor)
    finally:
        _close_descriptors(iter((descriptor,)))
    current = os.fstat(artifact.descriptor)
    if (_file_identity(current) != artifact.snapshot.identity or
            size != artifact.snapshot.size or
            digest.hexdigest() != artifact.snapshot.sha256):
        raise DeliveryClosureError(
            f"artifact changed while preparing semantic verification: "
            f"{artifact.role}")


def _verify_clean_source_pair(
    opened: Mapping[str, OpenedArtifact],
) -> dict[str, Any]:
    with tempfile.TemporaryDirectory(
            prefix="hepta-delivery-clean-source-") as temporary:
        root = Path(temporary)
        bundle = root / "strict-source.tar"
        manifest = root / "strict-source.manifest.json"
        _write_private_copy(opened["strict-source-bundle"], bundle)
        _write_private_copy(
            opened["strict-source-bundle-manifest"], manifest)
        try:
            return clean_source_verifier.verify_bundle(bundle, manifest)
        except SystemExit as error:
            raise DeliveryClosureError(
                f"strict source bundle verification failed: {error}") from error
        except (OSError, ValueError, RuntimeError) as error:
            raise DeliveryClosureError(
                "strict source bundle verification failed") from error


def _require_exact_json(value: Any, expected: Any, label: str) -> None:
    if canonical_json(value) != canonical_json(expected):
        raise DeliveryClosureError(f"{label} drift")


def _require_integer(
    value: Any,
    *,
    label: str,
    minimum: int = 0,
    maximum: int | None = None,
) -> int:
    if (not isinstance(value, int) or isinstance(value, bool) or
            value < minimum or
            (maximum is not None and value > maximum)):
        raise DeliveryClosureError(f"{label} is invalid")
    return value


def _require_sha256(value: Any, label: str) -> str:
    if (not isinstance(value, str) or not value.startswith("sha256:") or
            HEX64.fullmatch(value[7:]) is None):
        raise DeliveryClosureError(f"{label} is not a canonical sha256")
    return value


def _validate_soak_file_record(
    value: Any,
    *,
    label: str,
    expected_path: str,
    executable: bool,
) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) != FILE_RECORD_FIELDS:
        raise DeliveryClosureError(f"{label} file record fields are invalid")
    relative = normalized_relative_path(value["path"], f"{label} path")
    if relative != expected_path:
        raise DeliveryClosureError(f"{label} path drift")
    mode = value["mode"]
    if (not isinstance(mode, str) or
            re.fullmatch(r"0[0-7]{3}", mode) is None):
        raise DeliveryClosureError(f"{label} mode is invalid")
    numeric_mode = int(mode, 8)
    if numeric_mode & 0o7002 or (executable and not numeric_mode & 0o111):
        raise DeliveryClosureError(f"{label} mode is unsafe")
    _require_sha256(value["sha256"], f"{label} digest")
    _require_integer(
        value["size"],
        label=f"{label} size",
        minimum=1,
        maximum=MAX_ARTIFACT_BYTES,
    )
    return value


def _validate_soak_limits(value: Any, role: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise DeliveryClosureError(f"{role} soak limits are invalid")
    _require_exact_json(value, SOAK_DEFAULT_LIMITS, f"{role} soak limits")
    timeout = value["timeout_sec_per_binary"]
    if (not isinstance(timeout, float) or not math.isfinite(timeout) or
            timeout <= 0.0):
        raise DeliveryClosureError(f"{role} soak timeout limit is invalid")
    for field in set(SOAK_DEFAULT_LIMITS) - {"timeout_sec_per_binary"}:
        _require_integer(
            value[field],
            label=f"{role} soak limit {field}",
            minimum=1,
        )
    return value


def _validate_soak(
    report: dict[str, Any],
    *,
    role: str,
    expected_ibapi: bool,
    expect_source_bundle: bool,
    baseline: dict[str, Any],
    bundle_manifest: dict[str, Any],
    manifest_artifact: OpenedArtifact,
) -> dict[str, Any]:
    if set(report) != SOAK_FIELDS or report["schema"] != SOAK_SCHEMA:
        raise DeliveryClosureError(
            f"{role} soak fields or schema are invalid")
    _require_integer(
        report["generated_at_unix_ms"],
        label=f"{role} soak generated_at_unix_ms",
        minimum=1,
        maximum=(1 << 63) - 1,
    )
    build_dir = normalized_relative_path(
        report["build_dir"], f"{role} soak build_dir")
    # Delivery closures are certification artifacts, not PR smoke evidence.
    # Only the named eight-round profiles can satisfy this older closure
    # contract; a two-round report must remain a non-admission diagnostic.
    soak_profile = report["soak_profile"]
    if soak_profile not in {"release", "nightly"}:
        raise DeliveryClosureError(
            f"{role} soak profile is not a release certification profile")
    if soak_runner.SoakProfile.resolve(soak_profile).rounds != EXPECTED_SOAK_ROUNDS:
        raise DeliveryClosureError(f"{role} soak profile/round policy drift")
    limits = _validate_soak_limits(report["limits"], role)
    rounds = report["rounds"]
    if (report["passed"] is not True or
            report["all_invariants_certified"] is not True or
            not isinstance(rounds, list) or
            len(rounds) != EXPECTED_SOAK_ROUNDS):
        raise DeliveryClosureError(
            f"{role} soak did not certify all 8 rounds")
    if (_require_integer(
            report["requested_rounds"],
            label=f"{role} requested rounds",
            minimum=1,
            maximum=EXPECTED_SOAK_ROUNDS) != EXPECTED_SOAK_ROUNDS or
            _require_integer(
                report["completed_rounds"],
                label=f"{role} completed rounds",
                minimum=1,
                maximum=EXPECTED_SOAK_ROUNDS) != EXPECTED_SOAK_ROUNDS):
        raise DeliveryClosureError(
            f"{role} soak did not complete all 8 rounds")

    provenance = report["provenance"]
    if (not isinstance(provenance, dict) or
            set(provenance) != {
                "inputs_stable", "post_run", "post_snapshot_error",
                "pre_run", "source_binary_binding"} or
            provenance["inputs_stable"] is not True or
            provenance["post_snapshot_error"] != "" or
            provenance["source_binary_binding"] !=
            SOAK_SOURCE_BINARY_BINDING):
        raise DeliveryClosureError(
            f"{role} soak inputs are not stable")
    _require_exact_json(
        provenance["post_run"],
        provenance["pre_run"],
        f"{role} pre/post input snapshot",
    )
    snapshot = provenance["pre_run"]
    if (not isinstance(snapshot, dict) or
            set(snapshot) != {"binaries", "git_head", "provenance"} or
            snapshot["git_head"] != baseline["git_head"] or
            report["git_head"] != baseline["git_head"]):
        raise DeliveryClosureError(
            f"{role} soak Git identity drift")
    inputs = snapshot["provenance"]
    if (not isinstance(inputs, dict) or
            set(inputs) != {
                "build_configuration", "runner", "source_bundle",
                "source_manifest", "tracked_diff_sha256",
                "tracked_worktree_status_sha256"}):
        raise DeliveryClosureError(
            f"{role} soak provenance fields are invalid")
    build = inputs["build_configuration"]
    if (not isinstance(build, dict) or
            set(build) != {
                "build_type", "cmake_cache", "compile_commands",
                "cxx_compiler_name", "generator", "ibapi_enabled",
                "legacy_0dte_bridge_enabled", "legacy_monolith_built",
                "legacy_simulator_built"} or
            build.get("build_type") != "Release" or
            build.get("ibapi_enabled") is not expected_ibapi or
            build.get("legacy_0dte_bridge_enabled") is not False or
            build.get("legacy_monolith_built") is not False or
            build.get("legacy_simulator_built") is not False):
        raise DeliveryClosureError(
            f"{role} soak build profile drift")
    for field in ("generator", "cxx_compiler_name"):
        value = build[field]
        if (not isinstance(value, str) or not value or
                not value.isascii() or len(value) > 256 or
                any(ord(character) < 0x20 or ord(character) == 0x7f
                    for character in value)):
            raise DeliveryClosureError(
                f"{role} soak build {field} is invalid")
    if "/" in build["cxx_compiler_name"] or "\\" in build["cxx_compiler_name"]:
        raise DeliveryClosureError(
            f"{role} soak compiler name must be a basename")
    _validate_soak_file_record(
        build["cmake_cache"],
        label=f"{role} CMake cache",
        expected_path=f"{build_dir}/CMakeCache.txt",
        executable=False,
    )
    compile_commands = build["compile_commands"]
    if compile_commands != "not-enabled":
        _validate_soak_file_record(
            compile_commands,
            label=f"{role} compile commands",
            expected_path=f"{build_dir}/compile_commands.json",
            executable=False,
        )

    observed_source = _validate_manifest_records(
        inputs["source_manifest"],
        label=role,
        portable_modes=expect_source_bundle,
    )
    if observed_source != baseline["source_manifest"]:
        raise DeliveryClosureError(
            f"{role} soak source baseline drift")
    runner = inputs["runner"]
    baseline_runner = next((
        record for record in baseline["source_manifest"]["files"]
        if record["path"] == "scripts/run_execution_gateway_soak.py"
    ), None)
    _validate_soak_file_record(
        runner,
        label=f"{role} soak runner",
        expected_path="scripts/run_execution_gateway_soak.py",
        executable=False,
    )
    if (baseline_runner is None or
            runner["path"] != baseline_runner["path"] or
            runner["sha256"] != baseline_runner["sha256"] or
            runner["size"] != baseline_runner["size"]):
        raise DeliveryClosureError(
            f"{role} soak runner/source binding drift")

    tracked_diff = _require_sha256(
        inputs["tracked_diff_sha256"], f"{role} tracked diff")
    tracked_status = _require_sha256(
        inputs["tracked_worktree_status_sha256"],
        f"{role} tracked worktree status")
    source_bundle = inputs["source_bundle"]
    if expect_source_bundle:
        expected_bundle = {
            "file_count": bundle_manifest["file_count"],
            "files_sha256": bundle_manifest["files_sha256"],
            "git_head": baseline["git_head"],
            "manifest": {
                "mode": "0644",
                "path": ".hepta/source-bundle-manifest.json",
                "sha256":
                    "sha256:" + manifest_artifact.snapshot.sha256,
                "size": manifest_artifact.snapshot.size,
            },
        }
        _require_exact_json(
            source_bundle,
            expected_bundle,
            f"{role} no-Git source bundle binding",
        )
        if tracked_diff != EMPTY_SHA256 or tracked_status != EMPTY_SHA256:
            raise DeliveryClosureError(
                f"{role} no-Git provenance is not clean")
    elif source_bundle is not None:
        raise DeliveryClosureError(
            f"{role} worktree soak unexpectedly claims a source bundle")
    elif tracked_diff == EMPTY_SHA256 and tracked_status == EMPTY_SHA256:
        raise DeliveryClosureError(
            f"{role} worktree provenance unexpectedly claims clean Git state")

    invariants = report["expected_invariants_per_round"]
    if not isinstance(invariants, dict):
        raise DeliveryClosureError(f"{role} soak invariants are invalid")
    _require_exact_json(
        invariants,
        REQUIRED_SOAK_INVARIANTS,
        f"{role} soak invariant contract",
    )
    binary_inputs = report["binary_inputs"]
    contracts = report["evidence_contracts"]
    minimums = report["minimum_observed_processes"]
    if (not isinstance(binary_inputs, dict) or
            set(binary_inputs) != set(SOAK_BINARY_NAMES) or
            not isinstance(contracts, list) or
            not isinstance(minimums, dict)):
        raise DeliveryClosureError(
            f"{role} soak binary evidence set is incomplete")
    _require_exact_json(
        contracts,
        list(SOAK_EVIDENCE_CONTRACTS),
        f"{role} soak evidence contracts",
    )
    _require_exact_json(
        minimums,
        SOAK_MINIMUM_OBSERVED_PROCESSES,
        f"{role} soak minimum observed processes",
    )
    _require_exact_json(
        snapshot["binaries"],
        binary_inputs,
        f"{role} soak snapshot binary inputs",
    )
    for binary in SOAK_BINARY_NAMES:
        _validate_soak_file_record(
            binary_inputs[binary],
            label=f"{role} binary {binary}",
            expected_path=f"{build_dir}/tests/{binary}",
            executable=True,
        )

    for index, result in enumerate(rounds, start=1):
        if not isinstance(result, dict) or set(result) != SOAK_ROUND_FIELDS:
            raise DeliveryClosureError(
                f"{role} soak round {index} is incomplete")
        checks = result["checks"]
        if (result["round"] != index or
                not isinstance(result["round"], int) or
                isinstance(result["round"], bool) or
                result["passed"] is not True or
                result["no_orphan_descendants"] is not True or
                result["process_tree_observed"] is not True or
                result["resource_growth_within_limit"] is not True or
                not isinstance(checks, list) or
                len(checks) != len(SOAK_BINARY_NAMES)):
            raise DeliveryClosureError(
                f"{role} soak round {index} is incomplete")
        growth = result["runner_growth"]
        if (not isinstance(growth, dict) or
                set(growth) != SOAK_RUNNER_GROWTH_FIELDS):
            raise DeliveryClosureError(
                f"{role} soak round {index} runner growth is invalid")
        for resource, limit_name in (
                ("fds", "max_runner_fd_growth"),
                ("threads", "max_runner_thread_growth"),
                ("rss_kb", "max_runner_rss_growth_kb")):
            value = growth[resource]
            if (not isinstance(value, int) or isinstance(value, bool) or
                    value > limits[limit_name]):
                raise DeliveryClosureError(
                    f"{role} soak round {index} runner growth exceeds limits")

        for expected_binary, contract, check in zip(
                SOAK_BINARY_NAMES, contracts, checks, strict=True):
            if not isinstance(check, dict) or set(check) != SOAK_CHECK_FIELDS:
                raise DeliveryClosureError(
                    f"{role} soak round {index} check fields drift")
            expected_record = binary_inputs[expected_binary]
            expected_path = expected_record["path"]
            _require_exact_json(
                check["pinned_binary"],
                expected_record,
                f"{role} round {index} pinned binary {expected_binary}",
            )
            if (check["binary"] != expected_path or
                    check["passed"] is not True or
                    not isinstance(check["exit_code"], int) or
                    isinstance(check["exit_code"], bool) or
                    check["exit_code"] != 0 or
                    check["timed_out"] is not False or
                    check["output_limit_exceeded"] is not False or
                    check["evidence_contract_satisfied"] is not True or
                    check["evidence_observed"] is not True or
                    check["evidence_parse_error"] != "" or
                    not isinstance(check["evidence_line_count"], int) or
                    isinstance(check["evidence_line_count"], bool) or
                    check["evidence_line_count"] != 1 or
                    check["evidence_prefix"] != contract["prefix"] or
                    check["missing_evidence_fields"] != [] or
                    check["mismatched_evidence_fields"] != {} or
                    check["unexpected_evidence_fields"] != [] or
                    check["process_group_cleanup_succeeded"] is not True or
                    check["process_resources_within_limit"] is not True or
                    check["remaining_process_group_members"] != [] or
                    check["post_cleanup_process_group_members"] != []):
                raise DeliveryClosureError(
                    f"{role} soak round {index} contains a failed check")
            _require_exact_json(
                check["expected_evidence_fields"],
                contract["fields"],
                f"{role} round {index} expected evidence {expected_binary}",
            )
            _require_exact_json(
                check["evidence_fields"],
                contract["fields"],
                f"{role} round {index} observed evidence {expected_binary}",
            )
            _require_sha256(
                check["output_sha256"],
                f"{role} round {index} output digest {expected_binary}",
            )
            _require_integer(
                check["output_size_bytes"],
                label=(
                    f"{role} round {index} output size {expected_binary}"),
                maximum=limits["max_output_bytes_per_binary"],
            )
            _require_integer(
                check["duration_ms"],
                label=f"{role} round {index} duration {expected_binary}",
                maximum=int(
                    (limits["timeout_sec_per_binary"] + 5.0) * 1000),
            )
            output_tail = check["output_tail_redacted"]
            if (not isinstance(output_tail, str) or
                    len(output_tail.encode("utf-8")) >
                    4 * soak_runner.OUTPUT_TAIL_BYTES):
                raise DeliveryClosureError(
                    f"{role} round {index} output tail is invalid")
            high_water = check["high_water"]
            if (not isinstance(high_water, dict) or
                    set(high_water) != SOAK_HIGH_WATER_FIELDS):
                raise DeliveryClosureError(
                    f"{role} round {index} high-water fields drift")
            for resource in SOAK_HIGH_WATER_FIELDS:
                _require_integer(
                    high_water[resource],
                    label=(
                        f"{role} round {index} {expected_binary} "
                        f"high-water {resource}"),
                )
            if (high_water["fds"] >
                    limits["max_process_tree_fds"] or
                    high_water["threads"] >
                    limits["max_process_tree_threads"] or
                    high_water["rss_kb"] >
                    limits["max_process_tree_rss_kb"] or
                    high_water["processes"] <
                    minimums[expected_binary]):
                raise DeliveryClosureError(
                    f"{role} round {index} process evidence exceeds limits")
    return {
        "expected_invariants_per_round": invariants,
        "binary_names": list(SOAK_BINARY_NAMES),
        "ibapi_enabled": expected_ibapi,
        "source_bundle_present": expect_source_bundle,
    }


def validate_delivery_evidence(
    opened: Mapping[str, OpenedArtifact],
    *,
    round_number: int,
    release_version: str,
) -> dict[str, Any]:
    """Validate the exact seven artifacts as one causal delivery lineage."""
    baseline_document = _json_artifact(
        opened, "source-baseline-manifest")
    baseline = _validate_baseline(
        baseline_document,
        round_number=round_number,
        release_version=release_version,
    )
    bundle_manifest = _json_artifact(
        opened, "strict-source-bundle-manifest")
    clean_source = _verify_clean_source_pair(opened)
    if (bundle_manifest.get("schema") != CLEAN_SOURCE_SCHEMA or
            bundle_manifest.get("version") != release_version or
            bundle_manifest.get("git_head") != baseline["git_head"] or
            bundle_manifest.get("paper_authorized") is not False or
            bundle_manifest.get("live_authorized") is not False or
            bundle_manifest.get("security_manifest_sha256") !=
            baseline["source_manifest"]["sha256"] or
            bundle_manifest.get("security_manifest_file_count") !=
            baseline["source_manifest"]["file_count"] or
            clean_source.get("version") != release_version or
            clean_source.get("git_head") != baseline["git_head"] or
            clean_source.get("bundle_sha256") !=
            opened["strict-source-bundle"].snapshot.sha256 or
            clean_source.get("manifest_sha256") !=
            opened["strict-source-bundle-manifest"].snapshot.sha256):
        raise DeliveryClosureError(
            "strict source bundle/baseline lineage drift")
    bundle_files = bundle_manifest.get("files")
    baseline_path = (
        "release-manifests/heptatrader-agent-os-v" +
        release_version + "/manifest.json")
    baseline_entry = next((
        record for record in bundle_files
        if isinstance(record, dict) and record.get("path") == baseline_path
    ), None) if isinstance(bundle_files, list) else None
    if (not isinstance(baseline_entry, dict) or
            baseline_entry.get("sha256") !=
            opened["source-baseline-manifest"].snapshot.sha256 or
            baseline_entry.get("size") !=
            opened["source-baseline-manifest"].snapshot.size):
        raise DeliveryClosureError(
            "external and bundled source baselines differ")

    soak_specs = (
        ("worktree-soak-ibapi-off", False, False),
        ("worktree-soak-ibapi-on", True, False),
        ("no-git-soak-ibapi-off", False, True),
        ("no-git-soak-ibapi-on", True, True),
    )
    soak_summaries: dict[str, dict[str, Any]] = {}
    for role, ibapi_enabled, source_bundle_present in soak_specs:
        soak_summaries[role] = _validate_soak(
            _json_artifact(opened, role),
            role=role,
            expected_ibapi=ibapi_enabled,
            expect_source_bundle=source_bundle_present,
            baseline=baseline,
            bundle_manifest=bundle_manifest,
            manifest_artifact=opened[
                "strict-source-bundle-manifest"],
        )
    reference = soak_summaries["worktree-soak-ibapi-off"]
    for role, summary in soak_summaries.items():
        if (summary["expected_invariants_per_round"] !=
                reference["expected_invariants_per_round"] or
                summary["binary_names"] != reference["binary_names"]):
            raise DeliveryClosureError(
                f"soak evidence contract lineage drift: {role}")
    return {
        "git_head": baseline["git_head"],
        "source_manifest_sha256":
            baseline["source_manifest"]["sha256"],
        "source_manifest_file_count":
            baseline["source_manifest"]["file_count"],
        "bundle_sha256":
            opened["strict-source-bundle"].snapshot.sha256,
        "bundle_manifest_sha256":
            opened["strict-source-bundle-manifest"].snapshot.sha256,
        "clean_checkout_certified":
            baseline["clean_checkout_certified"],
        "release_authorized": False,
        "blocked_reason": baseline["blocked_reason"],
    }


def _write_all(descriptor: int, payload: bytes) -> None:
    offset = 0
    while offset < len(payload):
        written = os.write(descriptor, payload[offset:])
        if written <= 0:
            raise DeliveryClosureError("short write while publishing closure")
        offset += written


def _unlink_owned_file(
    directory_descriptor: int,
    name: str,
    inode: tuple[int, int],
) -> bool:
    """Unlink only the exact inode created by this publication attempt."""
    try:
        current = os.stat(
            name,
            dir_fd=directory_descriptor,
            follow_symlinks=False,
        )
    except FileNotFoundError:
        return True
    except OSError:
        return False
    if (current.st_dev, current.st_ino) != inode:
        # The owned link is already gone.  Never unlink a replacement.
        return True
    try:
        os.unlink(name, dir_fd=directory_descriptor)
        return True
    except OSError:
        return False


def write_closure(
    output_path: os.PathLike[str] | str,
    closure: Mapping[str, Any],
    *,
    validator: Callable[[Mapping[str, Any]], None] | None =
        validate_contract_structure,
    max_bytes: int = MAX_CLOSURE_BYTES,
) -> Path:
    """Atomically publish one new 0600 closure and refuse every overwrite."""
    if validator is not None:
        validator(closure)
    payload = canonical_json(closure) + b"\n"
    if len(payload) > max_bytes:
        raise DeliveryClosureError("delivery closure exceeds size limit")

    output = _absolute_lexical(output_path)
    if output == Path("/") or not output.name:
        raise DeliveryClosureError("delivery closure output path is invalid")
    name = output.name
    temporary_name = f".hepta-delivery-closure-{secrets.token_hex(16)}.tmp"
    descriptor = -1
    temporary_created = False
    temporary_inode: tuple[int, int] | None = None
    published = False
    published_inode: tuple[int, int] | None = None
    with _anchored_directory(output.parent) as anchored:
        directory_descriptor = anchored.descriptor
        _validate_trusted_directory(
            os.fstat(directory_descriptor),
            label="closure output directory",
            expected_owner=os.geteuid(),
        )
        try:
            try:
                os.stat(
                    name,
                    dir_fd=directory_descriptor,
                    follow_symlinks=False,
                )
            except FileNotFoundError:
                pass
            else:
                raise DeliveryClosureError(
                    f"refusing to overwrite existing output: {output}")

            flags = (
                os.O_WRONLY |
                os.O_CREAT |
                os.O_EXCL |
                getattr(os, "O_CLOEXEC", 0) |
                getattr(os, "O_NOFOLLOW", 0)
            )
            descriptor = os.open(
                temporary_name, flags, 0o600, dir_fd=directory_descriptor)
            temporary_created = True
            created_metadata = os.fstat(descriptor)
            temporary_inode = (
                created_metadata.st_dev, created_metadata.st_ino)
            os.fchmod(descriptor, 0o600)
            _write_all(descriptor, payload)
            os.fsync(descriptor)
            before_publish = os.fstat(descriptor)
            temporary_metadata = os.stat(
                temporary_name,
                dir_fd=directory_descriptor,
                follow_symlinks=False,
            )
            if (_file_identity(before_publish) !=
                    _file_identity(temporary_metadata) or
                    not stat.S_ISREG(before_publish.st_mode) or
                    stat.S_IMODE(before_publish.st_mode) != 0o600 or
                    before_publish.st_nlink != 1 or
                    before_publish.st_size != len(payload)):
                raise DeliveryClosureError(
                    "temporary closure changed before publication")

            try:
                os.link(
                    temporary_name,
                    name,
                    src_dir_fd=directory_descriptor,
                    dst_dir_fd=directory_descriptor,
                    follow_symlinks=False,
                )
            except FileExistsError as error:
                raise DeliveryClosureError(
                    f"refusing to overwrite existing output: {output}") from error
            published = True
            published_inode = (
                before_publish.st_dev, before_publish.st_ino)
            published_metadata = os.stat(
                name,
                dir_fd=directory_descriptor,
                follow_symlinks=False,
            )
            after_publish = os.fstat(descriptor)
            if (_file_identity(published_metadata) !=
                    _file_identity(after_publish)):
                raise DeliveryClosureError(
                    "published closure identity changed")

            if (temporary_inode is None or
                    not _unlink_owned_file(
                        directory_descriptor,
                        temporary_name,
                        temporary_inode)):
                raise DeliveryClosureError(
                    "temporary closure cleanup failed before commit")
            temporary_created = False
            final_metadata = os.stat(
                name,
                dir_fd=directory_descriptor,
                follow_symlinks=False,
            )
            committed_descriptor = os.fstat(descriptor)
            if (not stat.S_ISREG(final_metadata.st_mode) or
                    final_metadata.st_nlink != 1 or
                    stat.S_IMODE(final_metadata.st_mode) != 0o600 or
                    final_metadata.st_size != len(payload) or
                    _file_identity(final_metadata) !=
                    _file_identity(committed_descriptor)):
                raise DeliveryClosureError(
                    "published closure metadata is unsafe")
            anchored.verify()
            os.fsync(directory_descriptor)
            anchored.verify()
            post_fsync_descriptor = os.fstat(descriptor)
            post_fsync_path = os.stat(
                name,
                dir_fd=directory_descriptor,
                follow_symlinks=False,
            )
            if (_file_identity(post_fsync_descriptor) !=
                    _file_identity(committed_descriptor) or
                    _file_identity(post_fsync_path) !=
                    _file_identity(committed_descriptor)):
                raise DeliveryClosureError(
                    "published closure changed during directory fsync")
        except Exception as error:
            rollback_failures: list[str] = []
            cleanup_attempted = False
            if published and published_inode is not None:
                cleanup_attempted = True
                if not _unlink_owned_file(
                        directory_descriptor, name, published_inode):
                    rollback_failures.append("final")
                else:
                    published = False
            if temporary_created:
                cleanup_attempted = True
                if (temporary_inode is None or
                        not _unlink_owned_file(
                            directory_descriptor,
                            temporary_name,
                            temporary_inode)):
                    rollback_failures.append("temporary")
                else:
                    temporary_created = False
            if cleanup_attempted:
                try:
                    os.fsync(directory_descriptor)
                except OSError:
                    rollback_failures.append("directory-fsync")
            if rollback_failures:
                raise DeliveryClosureError(
                    "atomic closure publication failed and rollback is "
                    f"incomplete ({','.join(rollback_failures)}): "
                    f"{output}") from error
            if isinstance(error, OSError):
                raise DeliveryClosureError(
                    f"atomic closure publication failed: {output}") from error
            raise
        finally:
            if temporary_created and temporary_inode is not None:
                _unlink_owned_file(
                    directory_descriptor,
                    temporary_name,
                    temporary_inode,
                )
            if descriptor >= 0:
                _close_descriptors(iter((descriptor,)))
    return output


def write_private_json(
    output_path: os.PathLike[str] | str,
    document: Mapping[str, Any],
    *,
    max_bytes: int,
) -> Path:
    """Publish a canonical private JSON document through the shared inode gate."""
    if (not isinstance(max_bytes, int) or isinstance(max_bytes, bool) or
            max_bytes < 1):
        raise DeliveryClosureError("private JSON size limit is invalid")
    return write_closure(
        output_path,
        document,
        validator=None,
        max_bytes=max_bytes,
    )


write_delivery_closure = write_closure


def _artifact_argument(value: str) -> tuple[str, str]:
    role, separator, path = value.partition("=")
    if not separator or not role or not path:
        raise argparse.ArgumentTypeError(
            "artifact must be ROLE=NORMALIZED_RELATIVE_PATH")
    return role, path


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Build a local/offline HeptaTrader delivery closure")
    parser.add_argument("--artifact-root", type=Path, required=True)
    parser.add_argument(
        "--artifact",
        action="append",
        type=_artifact_argument,
        default=[],
        metavar="ROLE=PATH",
        help="fixed-role artifact binding; provide each schema role once",
    )
    parser.add_argument("--round", dest="round_number", type=int, required=True)
    parser.add_argument("--release-version", required=True)
    parser.add_argument("--generated-at")
    parser.add_argument("--output", type=Path, required=True)
    arguments = parser.parse_args()

    artifact_paths: dict[str, str] = {}
    for role, path in arguments.artifact:
        if role in artifact_paths:
            raise DeliveryClosureError(
                f"duplicate artifact role argument: {role}")
        artifact_paths[role] = path
    closure = build_closure(
        arguments.artifact_root,
        artifact_paths,
        round_number=arguments.round_number,
        release_version=arguments.release_version,
        generated_at=arguments.generated_at,
    )
    output = write_closure(arguments.output, closure)
    print(
        f"WROTE: {output} scope={LOCAL_OFFLINE_SCOPE} "
        "passed=true "
        f"artifacts={len(REQUIRED_ARTIFACT_ROLES)}")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (DeliveryClosureError, OSError) as error:
        print(f"delivery-closure: {error}", file=os.sys.stderr)
        raise SystemExit(78)
