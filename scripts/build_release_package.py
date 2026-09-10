#!/usr/bin/env python3
"""Build a deterministic, content-addressed HeptaTrader release archive.

The builder packages an already installed tree. It never reads Broker secrets,
never changes runtime authorization, and always records PAPER/LIVE as false.
"""
from __future__ import annotations

import argparse
from dataclasses import dataclass
import fcntl
import gzip
import hashlib
import io
import json
import os
from pathlib import Path, PurePosixPath
import re
import secrets
import shutil
import stat
import subprocess
import sys
import tarfile
import tempfile
from typing import Any, BinaryIO, Iterable

MANIFEST_SCHEMA = "heptatrader.release-manifest.v1"
RECEIPT_SCHEMA = "heptatrader.release-package-receipt.v1"
ALLOWED_PROFILES = {"core", "ib-paper"}
SHA_RE = re.compile(r"^[0-9a-f]{40}$")
LABEL_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")
PRIVATE_SUFFIXES = {".key", ".pem", ".p12", ".pfx", ".jks"}
MAX_ARCHIVE_MEMBERS = 4096
MAX_FILE_BYTES = 512 * 1024 * 1024
MAX_TOTAL_BYTES = 2 * 1024 * 1024 * 1024
GENERATED_ARCHIVE_PATHS = frozenset({"manifest.json"})
USTAR_NAME_BYTES = 100
USTAR_PREFIX_BYTES = 155


class PackageError(ValueError):
    pass


@dataclass(frozen=True)
class PayloadFile:
    path: str
    snapshot: BinaryIO
    size: int
    mode: int
    sha256: str


def canonical_json(value: Any) -> bytes:
    return (
        json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
        + "\n"
    ).encode("utf-8")


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


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


def _same_inode(left: os.stat_result, right: os.stat_result) -> bool:
    return (
        left.st_dev,
        left.st_ino,
        stat.S_IFMT(left.st_mode),
    ) == (
        right.st_dev,
        right.st_ino,
        stat.S_IFMT(right.st_mode),
    )


def _clear_nonblocking(descriptor: int) -> None:
    nonblocking = getattr(os, "O_NONBLOCK", 0)
    if not nonblocking:
        return
    flags = fcntl.fcntl(descriptor, fcntl.F_GETFL)
    if flags & nonblocking:
        fcntl.fcntl(descriptor, fcntl.F_SETFL, flags & ~nonblocking)


def _regular_file_flags() -> int:
    return (
        os.O_RDONLY
        | getattr(os, "O_CLOEXEC", 0)
        | getattr(os, "O_NOFOLLOW", 0)
        | getattr(os, "O_NONBLOCK", 0)
    )


def _hash_stream(stream: BinaryIO) -> str:
    stream.seek(0)
    digest = hashlib.sha256()
    for chunk in iter(lambda: stream.read(1024 * 1024), b""):
        digest.update(chunk)
    return digest.hexdigest()


def sha256_file(path: Path) -> str:
    descriptor = os.open(path, _regular_file_flags())
    try:
        pinned = os.fstat(descriptor)
        if not stat.S_ISREG(pinned.st_mode) or pinned.st_nlink != 1:
            raise PackageError(f"file is not a regular single-link file: {path}")
        _clear_nonblocking(descriptor)
        with os.fdopen(descriptor, "rb", closefd=True) as stream:
            descriptor = -1
            return _hash_stream(stream)
    finally:
        if descriptor >= 0:
            os.close(descriptor)



def _directory_flags() -> int:
    return (
        os.O_RDONLY
        | getattr(os, "O_DIRECTORY", 0)
        | getattr(os, "O_CLOEXEC", 0)
        | getattr(os, "O_NOFOLLOW", 0)
    )


def _canonical_component(name: str, label: str) -> None:
    # Preserve the canonical forbidden-byte diagnostic before the
    # structural component check so historical hostile contracts
    # keep exercising the same security classification.
    _canonical_path_bytes(name, label)
    if (
        not isinstance(name, str)
        or not name
        or name in {".", ".."}
        or "/" in name
    ):
        raise PackageError(f"{label} is not a canonical path component")


def _open_directory_absolute(
    path: Path, label: str
) -> tuple[int, os.stat_result, Path]:
    absolute = Path(os.path.abspath(os.fspath(path)))
    descriptor = os.open("/", _directory_flags())
    try:
        for part in absolute.parts[1:]:
            _canonical_component(part, label)
            following = os.open(
                part, _directory_flags(), dir_fd=descriptor
            )
            metadata = os.fstat(following)
            if not stat.S_ISDIR(metadata.st_mode):
                os.close(following)
                raise PackageError(
                    f"{label} component is not a directory: {part}"
                )
            os.close(descriptor)
            descriptor = following
        pinned = os.fstat(descriptor)
        current = os.stat(absolute, follow_symlinks=False)
        if (
            not stat.S_ISDIR(pinned.st_mode)
            or not _same_file(pinned, current)
        ):
            raise PackageError(
                f"{label} is not a stable no-follow directory"
            )
        return descriptor, pinned, absolute
    except Exception:
        os.close(descriptor)
        raise


def _reopen_directory_chain(
    root_path: Path,
    root_pinned: os.stat_result,
    chain: tuple[tuple[str, os.stat_result], ...],
    relative: str,
) -> int:
    descriptor, fresh_root, _ = _open_directory_absolute(
        root_path, "install root confirmation"
    )
    try:
        if not _same_file(root_pinned, fresh_root):
            raise PackageError(
                "install root identity changed during payload snapshot"
            )
        for name, expected in chain:
            following = os.open(
                name, _directory_flags(), dir_fd=descriptor
            )
            actual = os.fstat(following)
            if (
                not stat.S_ISDIR(actual.st_mode)
                or not _same_file(expected, actual)
            ):
                os.close(following)
                raise PackageError(
                    "payload directory topology changed during "
                    f"snapshot: {relative}"
                )
            os.close(descriptor)
            descriptor = following
        return descriptor
    except Exception:
        os.close(descriptor)
        raise


def _validate_directory_namespace(
    root_path: Path,
    root_pinned: os.stat_result,
    directory: int,
    directory_pinned: os.stat_result,
    chain: tuple[tuple[str, os.stat_result], ...],
    relative: str,
) -> None:
    try:
        held = os.fstat(directory)
        if not _same_file(directory_pinned, held):
            raise PackageError(
                "payload directory descriptor changed during "
                f"snapshot: {relative}"
            )
        confirmation = _reopen_directory_chain(
            root_path, root_pinned, chain, relative
        )
        try:
            if not _same_file(
                directory_pinned, os.fstat(confirmation)
            ):
                raise PackageError(
                    "payload directory path changed during "
                    f"snapshot: {relative}"
                )
        finally:
            os.close(confirmation)
    except OSError as error:
        raise PackageError(
            "payload directory namespace changed during "
            f"snapshot: {relative}: {error}"
        ) from error


def _validate_snapshot_namespace(
    root_path: Path,
    root_pinned: os.stat_result,
    directory: int,
    directory_pinned: os.stat_result,
    chain: tuple[tuple[str, os.stat_result], ...],
    name: str,
    pinned: os.stat_result,
    relative: str,
) -> None:
    try:
        held_directory = os.fstat(directory)
        held_leaf = os.stat(
            name, dir_fd=directory, follow_symlinks=False
        )
        if (
            not _same_file(directory_pinned, held_directory)
            or not _same_file(pinned, held_leaf)
        ):
            raise PackageError(
                "payload leaf or parent path changed after "
                f"snapshot: {relative}"
            )
        confirmation = _reopen_directory_chain(
            root_path, root_pinned, chain, relative
        )
        try:
            fresh_leaf = os.stat(
                name,
                dir_fd=confirmation,
                follow_symlinks=False,
            )
            if (
                not _same_file(
                    directory_pinned,
                    os.fstat(confirmation),
                )
                or not _same_file(pinned, fresh_leaf)
            ):
                raise PackageError(
                    "payload namespace changed after snapshot: "
                    f"{relative}"
                )
        finally:
            os.close(confirmation)
    except OSError as error:
        raise PackageError(
            "payload namespace changed after snapshot: "
            f"{relative}: {error}"
        ) from error


def _validate_complete_payload_namespace(
    root_path: Path,
    root_pinned: os.stat_result,
    directories: list[
        tuple[
            tuple[tuple[str, os.stat_result], ...],
            os.stat_result,
            str,
        ]
    ],
    leaves: list[
        tuple[
            tuple[tuple[str, os.stat_result], ...],
            str,
            os.stat_result,
            str,
        ]
    ],
) -> None:
    for chain, expected, relative in directories:
        confirmation = _reopen_directory_chain(
            root_path, root_pinned, chain, relative
        )
        try:
            if not _same_file(expected, os.fstat(confirmation)):
                raise PackageError(
                    "payload directory changed before namespace "
                    f"commit: {relative}"
                )
        finally:
            os.close(confirmation)
    for chain, name, expected, relative in leaves:
        confirmation = _reopen_directory_chain(
            root_path, root_pinned, chain, relative
        )
        try:
            current = os.stat(
                name,
                dir_fd=confirmation,
                follow_symlinks=False,
            )
            if not _same_file(expected, current):
                raise PackageError(
                    "payload leaf changed before namespace commit: "
                    f"{relative}"
                )
        except OSError as error:
            raise PackageError(
                "payload leaf namespace changed before commit: "
                f"{relative}: {error}"
            ) from error
        finally:
            os.close(confirmation)


def _snapshot_source(
    root_path: Path,
    root_pinned: os.stat_result,
    directory: int,
    directory_pinned: os.stat_result,
    chain: tuple[tuple[str, os.stat_result], ...],
    name: str,
    observed: os.stat_result,
    relative: str,
) -> tuple[BinaryIO, os.stat_result, str]:
    descriptor = -1
    snapshot: BinaryIO | None = None
    try:
        descriptor = os.open(
            name,
            _regular_file_flags(),
            dir_fd=directory,
        )
        pinned = os.fstat(descriptor)
        current = os.stat(
            name, dir_fd=directory, follow_symlinks=False
        )
        if (
            not stat.S_ISREG(pinned.st_mode)
            or pinned.st_nlink != 1
            or pinned.st_dev != directory_pinned.st_dev
            or not _same_file(observed, pinned)
            or not _same_file(pinned, current)
        ):
            raise PackageError(
                "payload is not one stable regular single-link "
                f"file: {relative}"
            )
        _clear_nonblocking(descriptor)
        if pinned.st_mode & (stat.S_ISUID | stat.S_ISGID):
            raise PackageError(
                f"setuid/setgid file is forbidden: {relative}"
            )
        if pinned.st_size > MAX_FILE_BYTES:
            raise PackageError(
                f"file exceeds release size bound: {relative}"
            )

        snapshot = tempfile.TemporaryFile(mode="w+b")
        digest = hashlib.sha256()
        total = 0
        while True:
            try:
                chunk = os.read(descriptor, 1024 * 1024)
            except InterruptedError:
                continue
            if not chunk:
                break
            total += len(chunk)
            if total > MAX_FILE_BYTES:
                raise PackageError(
                    f"file exceeds release size bound: {relative}"
                )
            digest.update(chunk)
            snapshot.write(chunk)
        after = os.fstat(descriptor)
        if (
            not _same_file(pinned, after)
            or total != pinned.st_size
        ):
            raise PackageError(
                "file changed while being snapshotted: "
                f"{relative}"
            )
        snapshot.flush()
        _validate_snapshot_namespace(
            root_path,
            root_pinned,
            directory,
            directory_pinned,
            chain,
            name,
            pinned,
            relative,
        )
        snapshot.seek(0)
        return snapshot, pinned, digest.hexdigest()
    except Exception:
        if snapshot is not None:
            snapshot.close()
        raise
    finally:
        if descriptor >= 0:
            os.close(descriptor)


def close_payload(payload: Iterable[PayloadFile]) -> None:
    for item in payload:
        try:
            item.snapshot.close()
        except OSError:
            pass


def validate_identity(version: str, profile: str, source_sha: str, epoch: int) -> None:
    if LABEL_RE.fullmatch(version) is None:
        raise PackageError("release version must be a bounded canonical label")
    if profile not in ALLOWED_PROFILES:
        raise PackageError(f"unsupported release profile: {profile}")
    if SHA_RE.fullmatch(source_sha) is None:
        raise PackageError("source SHA must be exactly 40 lowercase hexadecimal characters")
    if isinstance(epoch, bool) or epoch <= 0:
        raise PackageError("SOURCE_DATE_EPOCH must be a positive integer")


def _canonical_path_bytes(value: str, label: str) -> bytes:
    try:
        encoded = value.encode("utf-8", "strict")
    except UnicodeError as error:
        raise PackageError(f"{label} is not UTF-8: {value!r}") from error
    if "\\" in value or any(
        byte < 0x20 or byte == 0x7F for byte in encoded
    ):
        raise PackageError(
            f"{label} contains forbidden path bytes: {value!r}"
        )
    return encoded


def canonical_relative(path: Path, root: Path) -> str:
    try:
        relative = path.relative_to(root)
    except ValueError as error:
        raise PackageError(f"path escaped install root: {path}") from error
    value = relative.as_posix()
    _canonical_path_bytes(value, "package path")
    parsed = PurePosixPath(value)
    if (
        not value
        or parsed.is_absolute()
        or any(part in {"", ".", ".."} for part in parsed.parts)
        or parsed.as_posix() != value
    ):
        raise PackageError(f"non-canonical package path: {value!r}")
    return value


def _looks_private(relative: str) -> bool:
    path = PurePosixPath(relative)
    name = path.name.lower()
    parts = {part.lower() for part in path.parts}
    if any(part == ".git" for part in parts):
        return True
    if name in {".env", "id_rsa", "id_ed25519", "authorized_keys"}:
        return True
    if any(name.endswith(suffix) for suffix in PRIVATE_SUFFIXES):
        return True
    if name.endswith(".env") and not name.endswith(".env.example"):
        return True
    return False


def _file_mode(metadata: os.stat_result) -> int:
    return 0o755 if metadata.st_mode & 0o111 else 0o644


def _is_path_prefix(prefix: str, candidate: str) -> bool:
    return candidate.startswith(prefix + "/")


def _reject_generated_namespace_collision(relative: str) -> None:
    for generated in GENERATED_ARCHIVE_PATHS:
        if (
            relative == generated
            or _is_path_prefix(generated, relative)
            or _is_path_prefix(relative, generated)
        ):
            raise PackageError(
                "payload path collides with generated archive namespace: "
                f"{relative}"
            )


def _admit_payload_path(relative: str, admitted: set[str]) -> None:
    _canonical_path_bytes(relative, "payload path")
    _reject_generated_namespace_collision(relative)
    for existing in admitted:
        if (
            relative == existing
            or _is_path_prefix(existing, relative)
            or _is_path_prefix(relative, existing)
        ):
            raise PackageError(
                "payload file/directory prefix collision: "
                f"{existing!r} versus {relative!r}"
            )
    admitted.add(relative)


def _validate_ustar_name(name: str) -> None:
    encoded = _canonical_path_bytes(name, "archive path")
    if len(encoded) <= USTAR_NAME_BYTES:
        return
    parts = name.split("/")
    for index in range(1, len(parts)):
        prefix = "/".join(parts[:index]).encode("utf-8")
        suffix = "/".join(parts[index:]).encode("utf-8")
        if (
            len(prefix) <= USTAR_PREFIX_BYTES
            and len(suffix) <= USTAR_NAME_BYTES
        ):
            return
    raise PackageError(f"archive path is not representable in USTAR: {name}")


def _validate_archive_member_names(names: list[str]) -> None:
    if len(names) != len(set(names)):
        raise PackageError("archive member names are not globally unique")
    members = set(names)
    for name in names:
        parts = name.split("/")
        for index in range(1, len(parts)):
            prefix = "/".join(parts[:index])
            if prefix in members:
                raise PackageError(
                    "archive file/directory prefix collision: "
                    f"{prefix!r} versus {name!r}"
                )
        _validate_ustar_name(name)



def collect_payload(root: Path) -> list[PayloadFile]:
    root_descriptor = -1
    payload: list[PayloadFile] = []
    admitted_paths: set[str] = set()
    directory_pins: list[
        tuple[
            tuple[tuple[str, os.stat_result], ...],
            os.stat_result,
            str,
        ]
    ] = []
    leaf_pins: list[
        tuple[
            tuple[tuple[str, os.stat_result], ...],
            str,
            os.stat_result,
            str,
        ]
    ] = []
    total = 0
    try:
        root_descriptor, root_pinned, root_path = (
            _open_directory_absolute(root, "install root")
        )
        if root_path == Path("/"):
            raise PackageError(
                "filesystem root cannot be a release install root"
            )
        directory_pins.append((tuple(), root_pinned, "."))

        def visit(
            directory: int,
            directory_pinned: os.stat_result,
            chain: tuple[tuple[str, os.stat_result], ...],
            parts: tuple[str, ...],
        ) -> None:
            nonlocal total
            relative_directory = "/".join(parts) or "."
            _validate_directory_namespace(
                root_path,
                root_pinned,
                directory,
                directory_pinned,
                chain,
                relative_directory,
            )
            try:
                with os.scandir(directory) as iterator:
                    entries = [
                        (
                            entry.name,
                            entry.stat(follow_symlinks=False),
                        )
                        for entry in iterator
                    ]
            except OSError as error:
                raise PackageError(
                    "cannot enumerate stable install directory "
                    f"{relative_directory}: {error}"
                ) from error

            for name, observed in sorted(
                entries, key=lambda item: item[0]
            ):
                _canonical_component(name, "payload path")
                child_parts = parts + (name,)
                relative = "/".join(child_parts)
                _canonical_path_bytes(relative, "payload path")
                if stat.S_ISDIR(observed.st_mode):
                    _reject_generated_namespace_collision(relative)
                    child = -1
                    try:
                        child = os.open(
                            name,
                            _directory_flags(),
                            dir_fd=directory,
                        )
                        pinned = os.fstat(child)
                        current = os.stat(
                            name,
                            dir_fd=directory,
                            follow_symlinks=False,
                        )
                        if (
                            not stat.S_ISDIR(pinned.st_mode)
                            or pinned.st_dev
                            != directory_pinned.st_dev
                            or not _same_file(observed, pinned)
                            or not _same_file(pinned, current)
                        ):
                            raise PackageError(
                                "payload directory identity changed "
                                f"before traversal: {relative}"
                            )
                        child_chain = chain + ((name, pinned),)
                        directory_pins.append(
                            (child_chain, pinned, relative)
                        )
                        visit(
                            child,
                            pinned,
                            child_chain,
                            child_parts,
                        )
                        after = os.fstat(child)
                        current_after = os.stat(
                            name,
                            dir_fd=directory,
                            follow_symlinks=False,
                        )
                        if (
                            not _same_file(pinned, after)
                            or not _same_file(
                                pinned, current_after
                            )
                        ):
                            raise PackageError(
                                "payload directory changed during "
                                f"traversal: {relative}"
                            )
                        _validate_directory_namespace(
                            root_path,
                            root_pinned,
                            child,
                            pinned,
                            child_chain,
                            relative,
                        )
                    except OSError as error:
                        raise PackageError(
                            "payload directory namespace changed: "
                            f"{relative}: {error}"
                        ) from error
                    finally:
                        if child >= 0:
                            os.close(child)
                    continue

                _admit_payload_path(relative, admitted_paths)
                if (
                    len(admitted_paths)
                    + len(GENERATED_ARCHIVE_PATHS)
                    > MAX_ARCHIVE_MEMBERS
                ):
                    raise PackageError(
                        "release archive exceeds member-count bound "
                        "including generated metadata"
                    )
                if _looks_private(relative):
                    raise PackageError(
                        "private-key or secret-like path is "
                        f"forbidden: {relative}"
                    )
                if (
                    not stat.S_ISREG(observed.st_mode)
                    or observed.st_nlink != 1
                ):
                    raise PackageError(
                        "only regular single-link files may be "
                        f"packaged: {relative}"
                    )
                snapshot, pinned, digest = _snapshot_source(
                    root_path,
                    root_pinned,
                    directory,
                    directory_pinned,
                    chain,
                    name,
                    observed,
                    relative,
                )
                total += pinned.st_size
                if total > MAX_TOTAL_BYTES:
                    snapshot.close()
                    raise PackageError(
                        "release payload exceeds total size bound"
                    )
                payload.append(
                    PayloadFile(
                        path=relative,
                        snapshot=snapshot,
                        size=pinned.st_size,
                        mode=_file_mode(pinned),
                        sha256=digest,
                    )
                )
                leaf_pins.append(
                    (chain, name, pinned, relative)
                )

            _validate_directory_namespace(
                root_path,
                root_pinned,
                directory,
                directory_pinned,
                chain,
                relative_directory,
            )

        visit(
            root_descriptor,
            root_pinned,
            tuple(),
            tuple(),
        )
        if not payload:
            raise PackageError("install root is empty")
        _validate_complete_payload_namespace(
            root_path,
            root_pinned,
            directory_pins,
            leaf_pins,
        )
        return sorted(payload, key=lambda item: item.path)
    except Exception:
        close_payload(payload)
        raise
    finally:
        if root_descriptor >= 0:
            os.close(root_descriptor)


def build_manifest(
    payload: Iterable[PayloadFile], version: str, profile: str, source_sha: str, epoch: int
) -> dict[str, Any]:
    files = [
        {
            "path": item.path,
            "size": item.size,
            "mode": item.mode,
            "sha256": item.sha256,
        }
        for item in payload
    ]
    return {
        "schema": MANIFEST_SCHEMA,
        "version": version,
        "profile": profile,
        "source_sha": source_sha,
        "source_date_epoch": epoch,
        "created_by": "scripts/build_release_package.py",
        "authorization": {
            "effect": "NONE",
            "paper_authorized": False,
            "live_authorized": False,
        },
        "files": files,
    }


def _tar_info(name: str, size: int, mode: int, epoch: int) -> tarfile.TarInfo:
    info = tarfile.TarInfo(name=name)
    info.size = size
    info.mode = mode
    info.mtime = epoch
    info.uid = 0
    info.gid = 0
    info.uname = "root"
    info.gname = "root"
    return info


def archive_bytes(
    payload: list[PayloadFile], manifest: dict[str, Any], version: str, profile: str, epoch: int
) -> tuple[bytes, str]:
    root_name = f"heptatrader-{version}-{profile}"
    manifest_bytes = canonical_json(manifest)
    if len(payload) + len(GENERATED_ARCHIVE_PATHS) > MAX_ARCHIVE_MEMBERS:
        raise PackageError(
            "release archive exceeds member-count bound "
            "including generated metadata"
        )
    if len(manifest_bytes) > MAX_FILE_BYTES:
        raise PackageError("generated release manifest exceeds member-size bound")
    total_unpacked = len(manifest_bytes) + sum(item.size for item in payload)
    if total_unpacked > MAX_TOTAL_BYTES:
        raise PackageError(
            "release archive exceeds total unpacked-size bound "
            "including generated metadata"
        )
    manifest_name = f"{root_name}/manifest.json"
    member_names = [manifest_name] + [
        f"{root_name}/{item.path}" for item in payload
    ]
    _validate_archive_member_names(member_names)
    raw_tar = io.BytesIO()
    with tarfile.open(fileobj=raw_tar, mode="w", format=tarfile.USTAR_FORMAT) as archive:
        archive.addfile(
            _tar_info(manifest_name, len(manifest_bytes), 0o644, epoch),
            io.BytesIO(manifest_bytes),
        )
        for item in payload:
            before = os.fstat(item.snapshot.fileno())
            if before.st_size != item.size or _hash_stream(item.snapshot) != item.sha256:
                raise PackageError(f"payload snapshot changed before archive: {item.path}")
            item.snapshot.seek(0)
            archive.addfile(
                _tar_info(f"{root_name}/{item.path}", item.size, item.mode, epoch),
                item.snapshot,
            )
            after = os.fstat(item.snapshot.fileno())
            if (
                not _same_file(before, after)
                or after.st_size != item.size
                or _hash_stream(item.snapshot) != item.sha256
            ):
                raise PackageError(f"payload snapshot changed during archive: {item.path}")
            item.snapshot.seek(0)
    compressed = io.BytesIO()
    with gzip.GzipFile(
        filename="", mode="wb", compresslevel=9, fileobj=compressed, mtime=epoch
    ) as output:
        output.write(raw_tar.getvalue())
    return compressed.getvalue(), sha256_bytes(manifest_bytes)


def _absolute_output(path: Path) -> Path:
    absolute = Path(os.path.abspath(os.fspath(path)))
    if absolute.name in {"", ".", ".."}:
        raise PackageError(f"invalid output path: {path}")
    return absolute


def _open_output_directory(path: Path) -> tuple[int, os.stat_result]:
    path.parent.mkdir(parents=True, exist_ok=True)
    flags = (
        os.O_RDONLY
        | getattr(os, "O_DIRECTORY", 0)
        | getattr(os, "O_CLOEXEC", 0)
        | getattr(os, "O_NOFOLLOW", 0)
    )
    directory = os.open(path.parent, flags)
    try:
        pinned = os.fstat(directory)
        current = os.stat(path.parent, follow_symlinks=False)
        if (
            not stat.S_ISDIR(pinned.st_mode)
            or not _same_inode(pinned, current)
        ):
            raise PackageError(
                f"output parent is not a stable directory: {path.parent}"
            )
        if (
            pinned.st_uid != os.geteuid()
            or stat.S_IMODE(pinned.st_mode) & 0o022
        ):
            raise PackageError(
                "output directory must be operator-custodied: owned by "
                "the current user and not group/world writable"
            )
        return directory, pinned
    except Exception:
        os.close(directory)
        raise


def _open_anonymous_staging(directory: int, mode: int) -> int:
    anonymous = getattr(os, "O_TMPFILE", 0)
    if not anonymous:
        raise PackageError(
            "output filesystem lacks anonymous inode-bound staging"
        )
    try:
        descriptor = os.open(
            ".",
            os.O_RDWR
            | anonymous
            | getattr(os, "O_CLOEXEC", 0),
            mode,
            dir_fd=directory,
        )
    except OSError as error:
        raise PackageError(
            "output filesystem does not support anonymous "
            "inode-bound staging"
        ) from error
    metadata = os.fstat(descriptor)
    if not stat.S_ISREG(metadata.st_mode) or metadata.st_nlink != 0:
        os.close(descriptor)
        raise PackageError(
            "anonymous publication staging is not an unlinked regular file"
        )
    os.fchmod(descriptor, mode)
    return descriptor


def _write_all(descriptor: int, content: bytes) -> None:
    remaining = memoryview(content)
    while remaining:
        try:
            count = os.write(descriptor, remaining)
        except InterruptedError:
            continue
        if count <= 0:
            raise PackageError("publication staging write made no progress")
        remaining = remaining[count:]


def _descriptor_sha256(descriptor: int) -> str:
    digest = hashlib.sha256()
    offset = 0
    while True:
        chunk = os.pread(descriptor, 1024 * 1024, offset)
        if not chunk:
            return digest.hexdigest()
        digest.update(chunk)
        offset += len(chunk)


def _publish_noreplace(
    directory: int, descriptor: int, final_name: str
) -> None:
    # The source is the still-open anonymous inode, never a mutable
    # staging pathname. Destination creation remains atomic/no-replace.
    os.link(
        f"/proc/self/fd/{descriptor}",
        final_name,
        dst_dir_fd=directory,
        follow_symlinks=True,
    )


def _validate_published_output(
    directory: int,
    descriptor: int,
    final_name: str,
    content: bytes,
    mode: int,
) -> None:
    pinned = os.fstat(descriptor)
    published = os.stat(
        final_name, dir_fd=directory, follow_symlinks=False
    )
    if (
        not stat.S_ISREG(published.st_mode)
        or published.st_nlink != 1
        or not _same_inode(pinned, published)
        or published.st_size != len(content)
        or stat.S_IMODE(published.st_mode) != mode
        or _descriptor_sha256(descriptor) != sha256_bytes(content)
    ):
        raise PackageError(
            "published output identity, bytes or mode differ from "
            "the fsynced staging inode"
        )


def _ensure_new_outputs(paths: Iterable[Path]) -> None:
    for candidate in paths:
        path = _absolute_output(candidate)
        path.parent.mkdir(parents=True, exist_ok=True)
        try:
            path.lstat()
        except FileNotFoundError:
            continue
        raise PackageError(f"refusing to replace existing output: {path}")


def _write_atomic(path: Path, content: bytes, mode: int) -> None:
    path = _absolute_output(path)
    directory, pinned_directory = _open_output_directory(path)
    descriptor = -1
    published = False
    try:
        descriptor = _open_anonymous_staging(directory, mode)
        _write_all(descriptor, content)
        os.fsync(descriptor)
        try:
            _publish_noreplace(directory, descriptor, path.name)
            published = True
        except FileExistsError as error:
            raise PackageError(
                f"refusing to replace concurrently created output: {path}"
            ) from error
        _validate_published_output(
            directory, descriptor, path.name, content, mode
        )
        current_directory = os.stat(
            path.parent, follow_symlinks=False
        )
        if not _same_inode(pinned_directory, current_directory):
            raise PackageError(
                "output directory identity changed during publication"
            )
        os.fsync(directory)
    except Exception:
        # Roll back only the exact inode published by this writer.
        # Never unlink a replacement supplied by another publisher.
        if published and descriptor >= 0:
            try:
                current = os.stat(
                    path.name,
                    dir_fd=directory,
                    follow_symlinks=False,
                )
                if _same_inode(os.fstat(descriptor), current):
                    os.unlink(path.name, dir_fd=directory)
                    os.fsync(directory)
            except OSError:
                pass
        raise
    finally:
        if descriptor >= 0:
            os.close(descriptor)
        os.close(directory)


def package_install_root(
    install_root: Path,
    output: Path,
    *,
    version: str,
    profile: str,
    source_sha: str,
    source_date_epoch: int,
) -> dict[str, Any]:
    validate_identity(version, profile, source_sha, source_date_epoch)
    output = _absolute_output(output)
    if output.suffix not in {".gz", ".tgz"}:
        raise PackageError("release output must end in .tar.gz or .tgz")
    digest_path = Path(str(output) + ".sha256")
    receipt_path = Path(str(output) + ".receipt.json")
    _ensure_new_outputs((output, digest_path, receipt_path))

    payload: list[PayloadFile] = []
    try:
        payload = collect_payload(install_root)
        manifest = build_manifest(
            payload, version, profile, source_sha, source_date_epoch
        )
        archive, manifest_sha256 = archive_bytes(
            payload, manifest, version, profile, source_date_epoch
        )
    finally:
        close_payload(payload)

    package_sha256 = sha256_bytes(archive)
    digest_bytes = f"{package_sha256}  {output.name}\n".encode("ascii")
    receipt = {
        "schema": RECEIPT_SCHEMA,
        "version": version,
        "profile": profile,
        "source_sha": source_sha,
        "source_date_epoch": source_date_epoch,
        "package_file": output.name,
        "package_sha256": package_sha256,
        "manifest_sha256": manifest_sha256,
        "file_count": len(manifest["files"]),
        "authorization_effect": "NONE",
        "paper_authorized": False,
        "live_authorized": False,
    }

    # Publish each immutable output with an atomic no-replace link. If a later
    # publication loses a race, retain earlier outputs rather than performing
    # an unsafe pathname-based rollback that could delete another writer's file.
    _write_atomic(output, archive, 0o644)
    _write_atomic(digest_path, digest_bytes, 0o644)
    _write_atomic(receipt_path, canonical_json(receipt), 0o600)
    return receipt


def install_from_build(build_dir: Path) -> Path:
    build_dir = build_dir.resolve(strict=True)
    if not build_dir.is_dir() or build_dir.is_symlink():
        raise PackageError("build directory must be a real directory")
    temporary = Path(tempfile.mkdtemp(prefix="heptatrader-release-stage-"))
    install_root = temporary / "usr"
    command = ["cmake", "--install", str(build_dir), "--prefix", str(install_root)]
    result = subprocess.run(
        command,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        timeout=300,
        check=False,
    )
    if result.returncode:
        shutil.rmtree(temporary, ignore_errors=True)
        raise PackageError(f"cmake install failed ({result.returncode}):\n{result.stdout[-4000:]}")
    if not install_root.is_dir():
        shutil.rmtree(temporary, ignore_errors=True)
        raise PackageError(
            "cmake install did not create the expected staging tree:\n"
            + result.stdout[-4000:]
        )
    return install_root


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--build-dir", type=Path)
    source.add_argument("--install-root", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--version", required=True)
    parser.add_argument("--profile", choices=sorted(ALLOWED_PROFILES), required=True)
    parser.add_argument("--source-sha", required=True)
    parser.add_argument("--source-date-epoch", type=int, required=True)
    args = parser.parse_args(argv)

    temporary_root: Path | None = None
    try:
        if args.build_dir is not None:
            install_root = install_from_build(args.build_dir)
            temporary_root = install_root.parent
        else:
            install_root = args.install_root
        receipt = package_install_root(
            install_root,
            args.output,
            version=args.version,
            profile=args.profile,
            source_sha=args.source_sha,
            source_date_epoch=args.source_date_epoch,
        )
    except (OSError, PackageError, subprocess.SubprocessError) as error:
        print(f"[RELEASE] {error}", file=sys.stderr)
        return 1
    finally:
        if temporary_root is not None:
            shutil.rmtree(temporary_root, ignore_errors=True)
    print(canonical_json(receipt).decode("utf-8"), end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
