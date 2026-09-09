#!/usr/bin/env python3
"""Read-only release and host preflight for HeptaTrader.

A PASS receipt proves only the checks recorded in that receipt. It never grants
PAPER or LIVE authority and never creates credentials, sessions, firewall
rules, kill switches, users, directories, or Broker orders.
"""
from __future__ import annotations

import argparse
import contextlib
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import platform
import pwd
import re
import secrets
import shutil
import socket
import stat
import sys
import tarfile
import tempfile
from typing import Any, BinaryIO

POLICY_SCHEMA = "heptatrader.preflight-policy.v1"
MANIFEST_SCHEMA = "heptatrader.release-manifest.v1"
RECEIPT_SCHEMA = "heptatrader.preflight-receipt.v1"
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
SOURCE_SHA_RE = re.compile(r"^[0-9a-f]{40}$")
HARD_MAXIMUM_ARCHIVE_MEMBERS = 4096
HARD_MAXIMUM_MEMBER_BYTES = 512 * 1024 * 1024
HARD_MAXIMUM_TOTAL_UNPACKED_BYTES = 2 * 1024 * 1024 * 1024
HARD_PRIVATE_KEY_SUFFIXES = frozenset({".key", ".pem", ".p12", ".pfx", ".jks"})
MANAGED_SUBTREES = (
    "libexec/heptatrader",
    "share/heptatrader",
    "share/doc/heptatrader",
)
MANAGED_PREFIX_DIRECTORIES = (
    ("bin", "hepta"),
    ("lib/systemd/system", "hepta-"),
    ("lib/tmpfiles.d", "hepta-"),
)


class PreflightError(ValueError):
    pass


class DuplicateKeyError(ValueError):
    pass


class LoadedPolicy(dict[str, Any]):
    def __init__(self, value: dict[str, Any], raw_bytes: bytes, path: Path) -> None:
        super().__init__(value)
        self.raw_bytes = raw_bytes
        self.sha256 = hashlib.sha256(raw_bytes).hexdigest()
        self.path = path


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise DuplicateKeyError(f"duplicate JSON key: {key}")
        result[key] = value
    return result


def _reject_constant(value: str) -> None:
    raise ValueError(f"non-finite JSON number: {value}")


def parse_json_bytes(value: bytes, label: str) -> Any:
    try:
        return json.loads(
            value.decode("utf-8"),
            object_pairs_hook=_unique_object,
            parse_constant=_reject_constant,
        )
    except (UnicodeError, json.JSONDecodeError, ValueError) as error:
        raise PreflightError(f"{label}: invalid strict JSON: {error}") from error


def canonical_json(value: Any) -> bytes:
    return (
        json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
        + "\n"
    ).encode("utf-8")


def _absolute_path(path: Path) -> Path:
    return Path(os.path.abspath(os.fspath(path)))


def _directory_flags() -> int:
    return (
        os.O_RDONLY
        | getattr(os, "O_DIRECTORY", 0)
        | getattr(os, "O_CLOEXEC", 0)
        | getattr(os, "O_NOFOLLOW", 0)
    )


def _file_flags() -> int:
    return (
        os.O_RDONLY
        | getattr(os, "O_CLOEXEC", 0)
        | getattr(os, "O_NOFOLLOW", 0)
    )


def _file_identity(metadata: os.stat_result) -> tuple[int, ...]:
    return (
        metadata.st_dev,
        metadata.st_ino,
        metadata.st_mode,
        metadata.st_nlink,
        metadata.st_uid,
        metadata.st_gid,
        metadata.st_size,
        metadata.st_mtime_ns,
        metadata.st_ctime_ns,
    )


def _same_file(left: os.stat_result, right: os.stat_result) -> bool:
    return _file_identity(left) == _file_identity(right)


def _open_directory_absolute(path: Path, label: str) -> int:
    absolute = _absolute_path(path)
    descriptor = os.open("/", _directory_flags())
    try:
        for part in absolute.parts[1:]:
            if part in {"", ".", ".."}:
                raise PreflightError(f"{label}: non-canonical path component")
            following = os.open(part, _directory_flags(), dir_fd=descriptor)
            metadata = os.fstat(following)
            if not stat.S_ISDIR(metadata.st_mode):
                os.close(following)
                raise PreflightError(f"{label}: path component is not a directory")
            os.close(descriptor)
            descriptor = following
        return descriptor
    except Exception:
        os.close(descriptor)
        raise


def _open_relative_directory(root: int, parts: tuple[str, ...], label: str) -> int:
    descriptor = os.dup(root)
    try:
        for part in parts:
            if part in {"", ".", ".."} or "/" in part or "\\" in part:
                raise PreflightError(f"{label}: non-canonical relative component")
            following = os.open(part, _directory_flags(), dir_fd=descriptor)
            metadata = os.fstat(following)
            if not stat.S_ISDIR(metadata.st_mode):
                os.close(following)
                raise PreflightError(f"{label}: component is not a directory")
            os.close(descriptor)
            descriptor = following
        return descriptor
    except Exception:
        os.close(descriptor)
        raise


@contextlib.contextmanager
def _open_pinned_regular(path: Path, label: str) -> Any:
    absolute = _absolute_path(path)
    if not absolute.name or absolute.name in {".", ".."}:
        raise PreflightError(f"{label}: regular-file path required")
    directory = _open_directory_absolute(absolute.parent, f"{label} parent")
    descriptor = -1
    stream: BinaryIO | None = None
    try:
        descriptor = os.open(absolute.name, _file_flags(), dir_fd=directory)
        pinned = os.fstat(descriptor)
        current = os.stat(absolute.name, dir_fd=directory, follow_symlinks=False)
        if (
            not stat.S_ISREG(pinned.st_mode)
            or pinned.st_nlink != 1
            or not _same_file(pinned, current)
        ):
            raise PreflightError(
                f"{label} must be a stable regular non-symlink single-link file"
            )
        stream = os.fdopen(descriptor, "rb", closefd=True)
        descriptor = -1
        yield stream, pinned, directory, absolute.name
    finally:
        if stream is not None:
            stream.close()
        elif descriptor >= 0:
            os.close(descriptor)
        os.close(directory)


@contextlib.contextmanager
def _open_pinned_regular_at(root: int, relative: str, label: str) -> Any:
    parts = _canonical_member_name(relative)
    if not parts:
        raise PreflightError(f"{label}: regular-file path required")
    directory = _open_relative_directory(root, tuple(parts[:-1]), f"{label} parent")
    descriptor = -1
    stream: BinaryIO | None = None
    try:
        descriptor = os.open(parts[-1], _file_flags(), dir_fd=directory)
        pinned = os.fstat(descriptor)
        current = os.stat(parts[-1], dir_fd=directory, follow_symlinks=False)
        if (
            not stat.S_ISREG(pinned.st_mode)
            or pinned.st_nlink != 1
            or not _same_file(pinned, current)
        ):
            raise PreflightError(
                f"{label} must be a stable regular non-symlink single-link file"
            )
        stream = os.fdopen(descriptor, "rb", closefd=True)
        descriptor = -1
        yield stream, pinned, directory, parts[-1]
    finally:
        if stream is not None:
            stream.close()
        elif descriptor >= 0:
            os.close(descriptor)
        os.close(directory)


def _assert_stable_file(
    stream: BinaryIO,
    pinned: os.stat_result,
    directory: int,
    name: str,
    label: str,
) -> None:
    try:
        current = os.stat(name, dir_fd=directory, follow_symlinks=False)
    except OSError as error:
        raise PreflightError(f"{label} path changed while being read: {error}") from error
    after = os.fstat(stream.fileno())
    if not _same_file(pinned, after) or not _same_file(pinned, current):
        raise PreflightError(f"{label} identity changed while being read")


def _hash_stream(stream: BinaryIO) -> str:
    stream.seek(0)
    digest = hashlib.sha256()
    for chunk in iter(lambda: stream.read(1024 * 1024), b""):
        digest.update(chunk)
    return digest.hexdigest()


def sha256_file(path: Path) -> str:
    with _open_pinned_regular(path, "file") as (
        stream,
        pinned,
        directory,
        name,
    ):
        digest = _hash_stream(stream)
        _assert_stable_file(stream, pinned, directory, name, "file")
        return digest


def _read_bounded(stream: BinaryIO, maximum: int, label: str) -> bytes:
    chunks: list[bytes] = []
    total = 0
    while True:
        chunk = stream.read(min(1024 * 1024, maximum - total + 1))
        if not chunk:
            break
        total += len(chunk)
        if total > maximum:
            raise PreflightError(f"{label}: exceeds size bound")
        chunks.append(chunk)
    return b"".join(chunks)


def _load_policy(path: Path) -> LoadedPolicy:
    absolute = _absolute_path(path)
    with _open_pinned_regular(absolute, "policy") as (
        stream,
        pinned,
        directory,
        name,
    ):
        raw_bytes = _read_bounded(stream, 4 * 1024 * 1024, str(absolute))
        value = parse_json_bytes(raw_bytes, str(absolute))
        _assert_stable_file(stream, pinned, directory, name, "policy")
    expected_fields = {
        "schema",
        "maximum_archive_members",
        "maximum_member_bytes",
        "maximum_total_unpacked_bytes",
        "private_key_suffixes",
        "profiles",
    }
    if not isinstance(value, dict) or set(value) != expected_fields:
        raise PreflightError("preflight policy fields are not canonical")
    if value.get("schema") != POLICY_SCHEMA:
        raise PreflightError("unsupported preflight policy")
    ceilings = {
        "maximum_archive_members": HARD_MAXIMUM_ARCHIVE_MEMBERS,
        "maximum_member_bytes": HARD_MAXIMUM_MEMBER_BYTES,
        "maximum_total_unpacked_bytes": HARD_MAXIMUM_TOTAL_UNPACKED_BYTES,
    }
    for key, hard_maximum in ceilings.items():
        item = value.get(key)
        if (
            not isinstance(item, int)
            or isinstance(item, bool)
            or item <= 0
            or item > hard_maximum
        ):
            raise PreflightError(f"policy bound exceeds compiled ceiling: {key}")
    suffixes = value.get("private_key_suffixes")
    if (
        not isinstance(suffixes, list)
        or any(not isinstance(item, str) or not item.startswith(".") for item in suffixes)
        or not HARD_PRIVATE_KEY_SUFFIXES.issubset({item.lower() for item in suffixes})
    ):
        raise PreflightError("private-key suffix policy is invalid or weaker than compiled policy")
    profiles = value.get("profiles")
    if not isinstance(profiles, dict) or set(profiles) != {"core", "ib-paper"}:
        raise PreflightError("preflight policy profiles are not canonical")
    return LoadedPolicy(value, raw_bytes, absolute)


def _canonical_member_name(name: str) -> tuple[str, ...]:
    if not name or "\\" in name or "\x00" in name:
        raise PreflightError(f"archive contains a non-canonical path: {name!r}")
    path = PurePosixPath(name)
    if (
        path.is_absolute()
        or path.as_posix() != name.rstrip("/")
        or any(part in {"", ".", ".."} for part in path.parts)
    ):
        raise PreflightError(f"archive path escapes or is non-canonical: {name!r}")
    return path.parts


def _hash_member(archive: tarfile.TarFile, member: tarfile.TarInfo, maximum: int) -> str:
    stream = archive.extractfile(member)
    if stream is None:
        raise PreflightError(f"archive member is unreadable: {member.name}")
    digest = hashlib.sha256()
    total = 0
    with stream:
        while True:
            chunk = stream.read(1024 * 1024)
            if not chunk:
                break
            total += len(chunk)
            if total > maximum:
                raise PreflightError(f"archive member exceeds size bound: {member.name}")
            digest.update(chunk)
    if total != member.size:
        raise PreflightError(f"archive member size changed while reading: {member.name}")
    return digest.hexdigest()


def _read_member_bytes(
    archive: tarfile.TarFile, member: tarfile.TarInfo, maximum: int
) -> bytes:
    stream = archive.extractfile(member)
    if stream is None:
        raise PreflightError(f"archive member is unreadable: {member.name}")
    with stream:
        value = _read_bounded(stream, maximum, member.name)
    if len(value) != member.size:
        raise PreflightError(f"archive member size changed while reading: {member.name}")
    return value


def _check_manifest_shape(manifest: Any, profile: str) -> list[dict[str, Any]]:
    required = {
        "schema",
        "version",
        "profile",
        "source_sha",
        "source_date_epoch",
        "created_by",
        "authorization",
        "files",
    }
    if not isinstance(manifest, dict) or set(manifest) != required:
        raise PreflightError("release manifest fields are not canonical")
    if manifest.get("schema") != MANIFEST_SCHEMA:
        raise PreflightError("release manifest schema mismatch")
    if manifest.get("profile") != profile:
        raise PreflightError("release manifest profile mismatch")
    if not isinstance(manifest.get("version"), str) or not manifest["version"]:
        raise PreflightError("release version is missing")
    if SOURCE_SHA_RE.fullmatch(str(manifest.get("source_sha", ""))) is None:
        raise PreflightError("release source SHA is invalid")
    epoch = manifest.get("source_date_epoch")
    if not isinstance(epoch, int) or isinstance(epoch, bool) or epoch <= 0:
        raise PreflightError("release source epoch is invalid")
    if manifest.get("created_by") != "scripts/build_release_package.py":
        raise PreflightError("release builder identity mismatch")
    authorization = manifest.get("authorization")
    if authorization != {
        "effect": "NONE",
        "paper_authorized": False,
        "live_authorized": False,
    }:
        raise PreflightError("release archive attempted to claim authorization")
    files = manifest.get("files")
    if not isinstance(files, list) or not files:
        raise PreflightError("release manifest file list is empty")
    paths: list[str] = []
    for index, item in enumerate(files):
        if not isinstance(item, dict) or set(item) != {"path", "size", "mode", "sha256"}:
            raise PreflightError(f"manifest file[{index}] fields are invalid")
        path = item.get("path")
        if not isinstance(path, str):
            raise PreflightError(f"manifest file[{index}] path is invalid")
        parts = _canonical_member_name(path)
        if len(parts) < 1:
            raise PreflightError(f"manifest file[{index}] path is empty")
        size = item.get("size")
        mode = item.get("mode")
        digest = item.get("sha256")
        if not isinstance(size, int) or isinstance(size, bool) or size < 0:
            raise PreflightError(f"manifest file[{index}] size is invalid")
        if mode not in {0o644, 0o755}:
            raise PreflightError(f"manifest file[{index}] mode is invalid")
        if not isinstance(digest, str) or SHA256_RE.fullmatch(digest) is None:
            raise PreflightError(f"manifest file[{index}] digest is invalid")
        paths.append(path)
    if paths != sorted(paths) or len(paths) != len(set(paths)):
        raise PreflightError("manifest paths must be unique and byte sorted")
    return files


def _private_path(path: str, suffixes: set[str]) -> bool:
    name = PurePosixPath(path).name.lower()
    if name in {".env", "id_rsa", "id_ed25519", "authorized_keys"}:
        return True
    if name.endswith(".env") and not name.endswith(".env.example"):
        return True
    return any(name.endswith(suffix) for suffix in suffixes)


def inspect_archive(
    artifact: Path, expected_sha256: str, policy: LoadedPolicy, profile: str
) -> tuple[dict[str, Any], str, str]:
    if not isinstance(policy, LoadedPolicy):
        raise PreflightError("artifact inspection requires a descriptor-pinned policy")
    if SHA256_RE.fullmatch(expected_sha256) is None:
        raise PreflightError("expected artifact SHA-256 is not canonical")
    maximum_members = policy["maximum_archive_members"]
    maximum_member = policy["maximum_member_bytes"]
    maximum_total = policy["maximum_total_unpacked_bytes"]

    with _open_pinned_regular(artifact, "artifact") as (
        artifact_stream,
        metadata,
        directory,
        name,
    ):
        actual_sha256 = _hash_stream(artifact_stream)
        if actual_sha256 != expected_sha256:
            raise PreflightError("artifact SHA-256 mismatch")
        artifact_stream.seek(0)
        try:
            archive = tarfile.open(fileobj=artifact_stream, mode="r:gz")
        except (tarfile.TarError, OSError) as error:
            raise PreflightError(
                f"artifact is not a valid gzip tar archive: {error}"
            ) from error
        with archive:
            members = archive.getmembers()
            if not members or len(members) > maximum_members:
                raise PreflightError("archive member count is outside policy")
            seen: set[str] = set()
            roots: set[str] = set()
            files_by_relative: dict[str, tarfile.TarInfo] = {}
            manifest_member: tarfile.TarInfo | None = None
            total = 0
            for member in members:
                parts = _canonical_member_name(member.name)
                roots.add(parts[0])
                if member.name in seen:
                    raise PreflightError(f"duplicate archive member: {member.name}")
                seen.add(member.name)
                if not (member.isdir() or member.isreg()):
                    raise PreflightError(
                        f"links and special archive entries are forbidden: {member.name}"
                    )
                if member.uid != 0 or member.gid != 0:
                    raise PreflightError(
                        f"archive ownership is not normalized: {member.name}"
                    )
                if member.mode & ~0o777:
                    raise PreflightError(
                        f"archive contains elevated or non-canonical mode bits: {member.name}"
                    )
                if member.mtime <= 0:
                    raise PreflightError(f"archive timestamp is invalid: {member.name}")
                if member.isreg():
                    if member.size < 0 or member.size > maximum_member:
                        raise PreflightError(
                            f"archive member size is outside policy: {member.name}"
                        )
                    total += member.size
                    if total > maximum_total:
                        raise PreflightError("archive unpacked size exceeds policy")
            if len(roots) != 1:
                raise PreflightError("archive must have exactly one top-level directory")
            root_name = next(iter(roots))
            if not root_name.startswith("heptatrader-"):
                raise PreflightError("archive root name is not canonical")

            for member in members:
                if not member.isreg():
                    continue
                prefix = root_name + "/"
                if not member.name.startswith(prefix):
                    raise PreflightError("archive member escaped the package root")
                relative = member.name[len(prefix):]
                if relative == "manifest.json":
                    if manifest_member is not None:
                        raise PreflightError("archive contains multiple manifests")
                    manifest_member = member
                else:
                    files_by_relative[relative] = member
            if manifest_member is None:
                raise PreflightError("release manifest is missing")
            manifest_bytes = _read_member_bytes(
                archive, manifest_member, 4 * 1024 * 1024
            )
            manifest = parse_json_bytes(manifest_bytes, "manifest")
            manifest_files = _check_manifest_shape(manifest, profile)
            if manifest["source_date_epoch"] != manifest_member.mtime:
                raise PreflightError(
                    "manifest and archive timestamp identity mismatch"
                )

            expected_paths = {item["path"] for item in manifest_files}
            if set(files_by_relative) != expected_paths:
                missing = sorted(expected_paths - set(files_by_relative))
                extra = sorted(set(files_by_relative) - expected_paths)
                raise PreflightError(
                    f"archive payload differs from manifest: missing={missing}, extra={extra}"
                )
            suffixes = {item.lower() for item in policy["private_key_suffixes"]}
            retained_bytes: dict[str, bytes] = {}
            retain = {
                "share/heptatrader/preflight-policy-v1.json",
                "share/heptatrader/heptatrader-build-info.json",
            }
            for item in manifest_files:
                path = item["path"]
                if _private_path(path, suffixes):
                    raise PreflightError(
                        f"private-key or secret-like path is forbidden: {path}"
                    )
                member = files_by_relative[path]
                if member.size != item["size"]:
                    raise PreflightError(f"payload size mismatch: {path}")
                if member.mode != item["mode"]:
                    raise PreflightError(f"payload mode mismatch: {path}")
                if member.mtime != manifest["source_date_epoch"]:
                    raise PreflightError(f"payload timestamp mismatch: {path}")
                if path in retain:
                    value = _read_member_bytes(archive, member, maximum_member)
                    digest = hashlib.sha256(value).hexdigest()
                    retained_bytes[path] = value
                else:
                    digest = _hash_member(archive, member, maximum_member)
                if digest != item["sha256"]:
                    raise PreflightError(f"payload digest mismatch: {path}")

            selected = policy["profiles"].get(profile)
            if not isinstance(selected, dict):
                raise PreflightError(
                    f"profile is absent from preflight policy: {profile}"
                )
            required = selected.get("required_package_paths")
            if (
                not isinstance(required, list)
                or any(not isinstance(item, str) for item in required)
                or required != sorted(required)
                or len(required) != len(set(required))
            ):
                raise PreflightError("required package path policy is invalid")
            absent = sorted(set(required) - expected_paths)
            if absent:
                raise PreflightError(
                    "required package files are missing: " + ", ".join(absent)
                )

            policy_path = "share/heptatrader/preflight-policy-v1.json"
            packaged_policy_bytes = retained_bytes.get(policy_path)
            if packaged_policy_bytes is None:
                raise PreflightError("packaged preflight policy is missing")
            if hashlib.sha256(packaged_policy_bytes).hexdigest() != policy.sha256:
                raise PreflightError(
                    "effective preflight policy does not match the policy bound in the package"
                )
            packaged_policy = parse_json_bytes(
                packaged_policy_bytes, "packaged preflight policy"
            )
            if packaged_policy != dict(policy):
                raise PreflightError(
                    "effective and packaged preflight policy semantics differ"
                )

            build_info_path = "share/heptatrader/heptatrader-build-info.json"
            build_info_bytes = retained_bytes.get(build_info_path)
            if build_info_bytes is None:
                raise PreflightError("installed build metadata is missing")
            build_info = parse_json_bytes(
                build_info_bytes, "installed build metadata"
            )
            if not isinstance(build_info, dict):
                raise PreflightError(
                    "installed build metadata must be an object"
                )
            if (
                build_info.get("release_label") != manifest["version"]
                or build_info.get("paper_authorized") is not False
                or build_info.get("live_authorized") is not False
            ):
                raise PreflightError(
                    "installed build metadata does not match the release identity"
                )
            if profile == "ib-paper" and build_info.get("ib_api_compiled") is not True:
                raise PreflightError(
                    "IB PAPER package was not built with the IB API"
                )
            if profile == "core" and build_info.get("ib_api_compiled") is not False:
                raise PreflightError(
                    "core package unexpectedly contains an IB-enabled build"
                )

        _assert_stable_file(
            artifact_stream, metadata, directory, name, "artifact"
        )
    return (
        manifest,
        actual_sha256,
        hashlib.sha256(manifest_bytes).hexdigest(),
    )


def _machine_id_digest(root: Path) -> str:
    try:
        root_descriptor = _open_directory_absolute(root, "host root")
    except (OSError, PreflightError):
        return ""
    try:
        for relative in ("etc/machine-id", "var/lib/dbus/machine-id"):
            try:
                with _open_pinned_regular_at(
                    root_descriptor, relative, "machine identity"
                ) as (stream, pinned, directory, name):
                    value = _read_bounded(stream, 4096, relative).strip()
                    _assert_stable_file(
                        stream, pinned, directory, name, "machine identity"
                    )
                    if value:
                        return hashlib.sha256(value).hexdigest()
            except (OSError, PreflightError):
                continue
        return ""
    finally:
        os.close(root_descriptor)


def _is_managed_package_path(path: str) -> bool:
    if any(path == root or path.startswith(root + "/") for root in MANAGED_SUBTREES):
        return True
    return any(
        path.startswith(directory + "/")
        and PurePosixPath(path).parent.as_posix() == directory
        and PurePosixPath(path).name.startswith(prefix)
        for directory, prefix in MANAGED_PREFIX_DIRECTORIES
    )


def _scan_subtree(
    root: int, relative_root: str, result: set[str], errors: list[str]
) -> None:
    parts = _canonical_member_name("usr/" + relative_root)
    try:
        start = _open_relative_directory(root, tuple(parts), relative_root)
    except FileNotFoundError:
        return
    except (OSError, PreflightError) as error:
        errors.append(f"{relative_root}: {error}")
        return

    def visit(descriptor: int, logical: str) -> None:
        try:
            with os.scandir(descriptor) as entries:
                ordered = sorted(list(entries), key=lambda item: item.name)
            for entry in ordered:
                path = f"{logical}/{entry.name}"
                metadata = entry.stat(follow_symlinks=False)
                if stat.S_ISDIR(metadata.st_mode):
                    try:
                        child = os.open(
                            entry.name, _directory_flags(), dir_fd=descriptor
                        )
                    except OSError as error:
                        errors.append(f"{path}: {error}")
                        continue
                    try:
                        visit(child, path)
                    finally:
                        os.close(child)
                elif stat.S_ISREG(metadata.st_mode):
                    result.add(path)
                else:
                    errors.append(
                        f"{path}: managed entry is not a regular non-symlink file"
                    )
        except OSError as error:
            errors.append(f"{logical}: {error}")

    try:
        visit(start, relative_root)
    finally:
        os.close(start)


def _scan_prefix_directory(
    root: int,
    relative_directory: str,
    prefix: str,
    result: set[str],
    errors: list[str],
) -> None:
    parts = _canonical_member_name("usr/" + relative_directory)
    try:
        descriptor = _open_relative_directory(
            root, tuple(parts), relative_directory
        )
    except FileNotFoundError:
        return
    except (OSError, PreflightError) as error:
        errors.append(f"{relative_directory}: {error}")
        return
    try:
        with os.scandir(descriptor) as entries:
            ordered = sorted(list(entries), key=lambda item: item.name)
        for entry in ordered:
            if not entry.name.startswith(prefix):
                continue
            path = f"{relative_directory}/{entry.name}"
            metadata = entry.stat(follow_symlinks=False)
            if stat.S_ISREG(metadata.st_mode):
                result.add(path)
            else:
                errors.append(
                    f"{path}: managed entry is not a regular non-symlink file"
                )
    except OSError as error:
        errors.append(f"{relative_directory}: {error}")
    finally:
        os.close(descriptor)


def _scan_managed_installed_paths(root: int) -> tuple[set[str], list[str]]:
    result: set[str] = set()
    errors: list[str] = []
    for relative_root in MANAGED_SUBTREES:
        _scan_subtree(root, relative_root, result, errors)
    for directory, prefix in MANAGED_PREFIX_DIRECTORIES:
        _scan_prefix_directory(root, directory, prefix, result, errors)
    return result, errors


def _installed_metadata_problem(
    metadata: os.stat_result,
    item: dict[str, Any],
    expected_uid: int,
    expected_gid: int,
    path: str,
) -> str | None:
    if not stat.S_ISREG(metadata.st_mode) or metadata.st_nlink != 1:
        return f"{path}: not a regular non-symlink single-link file"
    if metadata.st_uid != expected_uid or metadata.st_gid != expected_gid:
        return (
            f"{path}: owner mismatch, expected {expected_uid}:{expected_gid}, "
            f"found {metadata.st_uid}:{metadata.st_gid}"
        )
    if stat.S_IMODE(metadata.st_mode) != item["mode"]:
        return (
            f"{path}: mode mismatch, expected {oct(item['mode'])}, "
            f"found {oct(stat.S_IMODE(metadata.st_mode))}"
        )
    if metadata.st_size != item["size"]:
        return (
            f"{path}: size mismatch, expected {item['size']}, "
            f"found {metadata.st_size}"
        )
    return None


def _verify_installed_tree(
    host_root: Path,
    manifest: dict[str, Any],
    policy: LoadedPolicy,
    profile: str,
) -> list[str]:
    errors: list[str] = []
    try:
        root = _open_directory_absolute(host_root, "host root")
    except (OSError, PreflightError) as error:
        return [f"host root: {error}"]
    try:
        root_metadata = os.fstat(root)
        expected_uid = root_metadata.st_uid
        expected_gid = root_metadata.st_gid
        files = manifest.get("files")
        if not isinstance(files, list):
            return ["release manifest file inventory is unavailable"]
        manifest_by_path = {
            item["path"]: item
            for item in files
            if isinstance(item, dict) and isinstance(item.get("path"), str)
        }
        if len(manifest_by_path) != len(files):
            return ["release manifest file inventory is invalid"]
        unmanaged = sorted(
            path for path in manifest_by_path if not _is_managed_package_path(path)
        )
        if unmanaged:
            errors.append(
                "package contains paths outside managed install namespaces: "
                + ", ".join(unmanaged)
            )

        actual, scan_errors = _scan_managed_installed_paths(root)
        errors.extend(scan_errors)
        expected = set(manifest_by_path)
        missing = sorted(expected - actual)
        extra = sorted(actual - expected)
        if missing:
            errors.append("installed package files are missing: " + ", ".join(missing))
        if extra:
            errors.append("unexpected managed package files are installed: " + ", ".join(extra))

        build_info_bytes: bytes | None = None
        for path in sorted(expected & actual):
            item = manifest_by_path[path]
            try:
                with _open_pinned_regular_at(
                    root, "usr/" + path, f"installed {path}"
                ) as (stream, pinned, directory, name):
                    problem = _installed_metadata_problem(
                        pinned, item, expected_uid, expected_gid, path
                    )
                    if problem:
                        errors.append(problem)
                        continue
                    digest = _hash_stream(stream)
                    if digest != item["sha256"]:
                        errors.append(f"{path}: installed payload digest mismatch")
                    if path == "share/heptatrader/heptatrader-build-info.json":
                        stream.seek(0)
                        build_info_bytes = _read_bounded(
                            stream, 64 * 1024, "installed build metadata"
                        )
                    _assert_stable_file(
                        stream, pinned, directory, name, f"installed {path}"
                    )
            except (OSError, PreflightError) as error:
                errors.append(f"{path}: {error}")

        if build_info_bytes is None:
            errors.append("installed build metadata could not be verified")
        else:
            try:
                build_info = parse_json_bytes(
                    build_info_bytes, "installed host build metadata"
                )
                if (
                    not isinstance(build_info, dict)
                    or build_info.get("release_label") != manifest.get("version")
                    or build_info.get("paper_authorized") is not False
                    or build_info.get("live_authorized") is not False
                    or (
                        profile == "core"
                        and build_info.get("ib_api_compiled") is not False
                    )
                    or (
                        profile == "ib-paper"
                        and build_info.get("ib_api_compiled") is not True
                    )
                ):
                    errors.append(
                        "installed host build metadata does not match the approved artifact"
                    )
            except PreflightError as error:
                errors.append(str(error))
        return errors
    finally:
        os.close(root)


def _safe_kill_switch(path: Path) -> str | None:
    if not path.is_absolute():
        return "kill-switch path must be absolute"
    try:
        metadata = path.lstat()
    except OSError as error:
        return str(error)
    if path.is_symlink() or not stat.S_ISREG(metadata.st_mode) or metadata.st_nlink != 1:
        return "kill-switch marker must be a regular single-link file"
    if metadata.st_uid != 0:
        return "kill-switch marker must be owned by root"
    if metadata.st_mode & 0o022:
        return "kill-switch marker must not be group/world writable"
    return None


def _publish_noreplace(
    directory: int, temporary_name: str, final_name: str
) -> None:
    os.link(
        temporary_name,
        final_name,
        src_dir_fd=directory,
        dst_dir_fd=directory,
        follow_symlinks=False,
    )


def _write_private_receipt(path: Path, value: dict[str, Any]) -> None:
    path = _absolute_path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    directory = os.open(
        path.parent,
        os.O_RDONLY
        | getattr(os, "O_DIRECTORY", 0)
        | getattr(os, "O_CLOEXEC", 0)
        | getattr(os, "O_NOFOLLOW", 0),
    )
    descriptor = -1
    temporary_name = ""
    try:
        for _ in range(32):
            candidate = f".{path.name}.{os.getpid()}.{secrets.token_hex(8)}"
            try:
                descriptor = os.open(
                    candidate,
                    os.O_WRONLY
                    | os.O_CREAT
                    | os.O_EXCL
                    | getattr(os, "O_CLOEXEC", 0)
                    | getattr(os, "O_NOFOLLOW", 0),
                    0o600,
                    dir_fd=directory,
                )
                temporary_name = candidate
                break
            except FileExistsError:
                continue
        if descriptor < 0:
            raise PreflightError(
                "could not allocate a private receipt staging file"
            )
        os.fchmod(descriptor, 0o600)
        with os.fdopen(descriptor, "wb", closefd=True) as stream:
            descriptor = -1
            stream.write(canonical_json(value))
            stream.flush()
            os.fsync(stream.fileno())
        try:
            _publish_noreplace(directory, temporary_name, path.name)
        except FileExistsError as error:
            raise PreflightError(
                f"refusing to replace concurrently created receipt: {path}"
            ) from error
        os.unlink(temporary_name, dir_fd=directory)
        temporary_name = ""
        os.fsync(directory)
    finally:
        if descriptor >= 0:
            os.close(descriptor)
        if temporary_name:
            try:
                os.unlink(temporary_name, dir_fd=directory)
            except OSError:
                pass
        os.close(directory)


def run_preflight(args: argparse.Namespace) -> dict[str, Any]:
    checks: list[dict[str, str]] = []

    def record(check_id: str, status_value: str, detail: str) -> None:
        checks.append({"id": check_id, "status": status_value, "detail": detail})

    manifest: dict[str, Any] = {}
    package_sha256 = ""
    manifest_sha256 = ""
    policy = _load_policy(_absolute_path(args.policy))
    try:
        manifest, package_sha256, manifest_sha256 = inspect_archive(
            _absolute_path(args.artifact), args.expected_sha256, policy, args.profile
        )
        record("artifact.integrity", "PASS", "digest, archive, manifest and payload verified")
    except (OSError, PreflightError, tarfile.TarError) as error:
        record("artifact.integrity", "FAIL", str(error))

    selected = policy["profiles"].get(args.profile, {})
    required = selected.get("required_package_paths", []) if isinstance(selected, dict) else []
    if args.artifact_only:
        record("host.static", "SKIP", "artifact-only mode requested")
    elif not manifest:
        record("host.static", "FAIL", "host checks require a valid release manifest")
    else:
        host_errors: list[str] = []
        host_root = _absolute_path(args.host_root)
        if platform.system() != "Linux":
            host_errors.append("host platform is not Linux")
        for command in selected.get("required_host_commands", []):
            if shutil.which(command) is None:
                host_errors.append(f"required command is missing: {command}")
        host_errors.extend(_verify_installed_tree(host_root, manifest, policy, args.profile))
        if args.profile == "ib-paper":
            if args.execution_uid is None or args.gateway_uid is None:
                host_errors.append("IB PAPER static preflight requires execution and Gateway UIDs")
            elif (
                args.execution_uid <= 0
                or args.gateway_uid <= 0
                or args.execution_uid == args.gateway_uid
            ):
                host_errors.append("IB PAPER execution and Gateway UIDs must be distinct positive values")
            else:
                for uid in (args.execution_uid, args.gateway_uid):
                    try:
                        pwd.getpwuid(uid)
                    except KeyError:
                        host_errors.append(f"host UID does not exist: {uid}")
            if args.kill_switch_path is None:
                host_errors.append("IB PAPER static preflight requires a kill-switch marker path")
            else:
                problem = _safe_kill_switch(args.kill_switch_path)
                if problem:
                    host_errors.append(problem)
        if host_errors:
            record("host.static", "FAIL", "; ".join(host_errors))
        else:
            record("host.static", "PASS", "installed bytes, modes, ownership, inventory and identity boundaries match the approved artifact")

    if args.probe_broker:
        if args.profile != "ib-paper":
            record("broker.reachability", "FAIL", "Broker probing is valid only for ib-paper")
        else:
            hosts = set(selected.get("allowed_broker_hosts", []))
            ports = set(selected.get("allowed_broker_ports", []))
            if args.broker_host not in hosts or args.broker_port not in ports:
                record("broker.reachability", "FAIL", "Broker endpoint is outside the PAPER policy")
            else:
                try:
                    with socket.create_connection(
                        (args.broker_host, args.broker_port), timeout=args.broker_timeout
                    ):
                        pass
                    record(
                        "broker.reachability",
                        "PASS",
                        "bounded TCP reachability succeeded; this is not account-mode or qualification evidence",
                    )
                except OSError as error:
                    record("broker.reachability", "FAIL", str(error))
    else:
        record("broker.reachability", "SKIP", "no Broker probe requested")

    result = "FAIL" if any(item["status"] == "FAIL" for item in checks) else "PASS"
    host_root = _absolute_path(args.host_root) if not args.artifact_only else Path("/")
    receipt = {
        "schema": RECEIPT_SCHEMA,
        "checked_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "result": result,
        "scope": "ARTIFACT_ONLY" if args.artifact_only else "STATIC_HOST_PRECHECK",
        "profile": args.profile,
        "artifact": {
            "file": args.artifact.name,
            "sha256": package_sha256,
            "manifest_sha256": manifest_sha256,
            "policy_sha256": policy.sha256,
            "source_sha": manifest.get("source_sha", ""),
            "version": manifest.get("version", ""),
        },
        "host": {
            "system": platform.system(),
            "machine": platform.machine(),
            "kernel": platform.release(),
            "machine_id_sha256": _machine_id_digest(host_root),
        },
        "checks": checks,
        "authorization_effect": "NONE",
        "paper_authorized": False,
        "live_authorized": False,
    }
    return receipt


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--artifact", type=Path, required=True)
    parser.add_argument("--expected-sha256", required=True)
    parser.add_argument("--profile", choices=("core", "ib-paper"), required=True)
    parser.add_argument("--policy", type=Path, required=True)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--artifact-only", action="store_true")
    parser.add_argument("--host-root", type=Path, default=Path("/"))
    parser.add_argument("--execution-uid", type=int)
    parser.add_argument("--gateway-uid", type=int)
    parser.add_argument("--kill-switch-path", type=Path)
    parser.add_argument("--probe-broker", action="store_true")
    parser.add_argument("--broker-host", default="127.0.0.1")
    parser.add_argument("--broker-port", type=int, default=4002)
    parser.add_argument("--broker-timeout", type=float, default=2.0)
    args = parser.parse_args(argv)

    try:
        receipt = run_preflight(args)
        if args.output is not None:
            _write_private_receipt(args.output, receipt)
    except (OSError, PreflightError, ValueError) as error:
        print(f"[PREFLIGHT] {error}", file=sys.stderr)
        return 2
    print(canonical_json(receipt).decode("utf-8"), end="")
    return 0 if receipt["result"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
