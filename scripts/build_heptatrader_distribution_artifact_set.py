#!/usr/bin/env python3
"""Build a deterministic passive HeptaTrader distribution artifact set."""

from __future__ import annotations

import argparse
from dataclasses import dataclass
import hashlib
import os
from pathlib import Path
import secrets
import stat
from typing import Any

import verify_heptatrader_distribution_artifact_set as verifier


class DistributionArtifactSetBuildError(RuntimeError):
    pass


def _directory_flags() -> int:
    return (
        os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) |
        getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    )


def _directory_identity(metadata: os.stat_result) -> tuple[int, ...]:
    return (
        metadata.st_dev, metadata.st_ino, metadata.st_mode,
        metadata.st_uid, metadata.st_gid,
    )


def _validate_parent(descriptor: int) -> os.stat_result:
    parent = os.fstat(descriptor)
    if (not stat.S_ISDIR(parent.st_mode) or
            parent.st_uid != os.geteuid() or
            stat.S_IMODE(parent.st_mode) & 0o022):
        raise DistributionArtifactSetBuildError(
            "output parent must be owned and not group/world-writable")
    return parent


@dataclass
class _OutputAnchor:
    absolute: Path
    parent: int
    name: str
    ancestors: tuple[tuple[int, ...], ...]


@dataclass
class _Publication:
    anchor: _OutputAnchor
    identity: tuple[int, ...]
    digest: bytes
    size: int


def _open_anchor(path: Path) -> _OutputAnchor:
    absolute = Path(os.path.abspath(path))
    if (verifier.FILENAME.fullmatch(absolute.name) is None or
            not absolute.name.endswith(".json")):
        raise DistributionArtifactSetBuildError(
            "output filename must be a safe JSON filename")
    descriptor = -1
    try:
        descriptor = os.open("/", _directory_flags())
        ancestors = [_directory_identity(os.fstat(descriptor))]
        for component in absolute.parent.parts[1:]:
            before = os.stat(
                component, dir_fd=descriptor, follow_symlinks=False)
            if not stat.S_ISDIR(before.st_mode):
                raise DistributionArtifactSetBuildError(
                    "output parent is not a directory")
            child = os.open(
                component, _directory_flags(), dir_fd=descriptor)
            opened = os.fstat(child)
            if _directory_identity(before) != _directory_identity(opened):
                os.close(child)
                raise DistributionArtifactSetBuildError(
                    "output parent changed while opening")
            os.close(descriptor)
            descriptor = child
            ancestors.append(_directory_identity(opened))
        _validate_parent(descriptor)
        return _OutputAnchor(
            absolute=absolute, parent=descriptor, name=absolute.name,
            ancestors=tuple(ancestors))
    except OSError as error:
        if descriptor >= 0:
            os.close(descriptor)
        raise DistributionArtifactSetBuildError(
            "output parent path is unavailable or unsafe") from error
    except BaseException:
        if descriptor >= 0:
            os.close(descriptor)
        raise


def _publication_identity(metadata: os.stat_result) -> tuple[int, ...]:
    return (
        metadata.st_dev, metadata.st_ino, metadata.st_mode,
        metadata.st_nlink, metadata.st_uid, metadata.st_gid,
        metadata.st_size,
    )


def _read_descriptor(descriptor: int, size: int) -> bytes:
    os.lseek(descriptor, 0, os.SEEK_SET)
    chunks: list[bytes] = []
    remaining = size
    while remaining:
        chunk = os.read(descriptor, min(1024 * 1024, remaining))
        if not chunk:
            raise DistributionArtifactSetBuildError(
                "temporary artifact set was truncated")
        chunks.append(chunk)
        remaining -= len(chunk)
    if os.read(descriptor, 1):
        raise DistributionArtifactSetBuildError(
            "temporary artifact set grew unexpectedly")
    return b"".join(chunks)


def _validate_publication(
        metadata: os.stat_result, size: int, *, links: int = 1) -> None:
    if (not stat.S_ISREG(metadata.st_mode) or
            stat.S_IMODE(metadata.st_mode) != 0o600 or
            metadata.st_uid != os.geteuid() or
            metadata.st_nlink != links or metadata.st_size != size):
        raise DistributionArtifactSetBuildError(
            "artifact-set publication identity or mode is unsafe")


def _unlink_exact_inode(
        parent: int, name: str, identity: tuple[int, int]) -> bool:
    try:
        metadata = os.stat(name, dir_fd=parent, follow_symlinks=False)
    except FileNotFoundError:
        return False
    if (not stat.S_ISREG(metadata.st_mode) or
            (metadata.st_dev, metadata.st_ino) != identity):
        return False
    os.unlink(name, dir_fd=parent)
    return True


def _verify_requested_publication(publication: _Publication) -> None:
    anchor = publication.anchor
    descriptor = -1
    file_descriptor = -1
    try:
        descriptor = os.open("/", _directory_flags())
        if _directory_identity(os.fstat(descriptor)) != anchor.ancestors[0]:
            raise DistributionArtifactSetBuildError(
                "output root identity changed")
        for index, component in enumerate(anchor.absolute.parent.parts[1:], 1):
            before = os.stat(
                component, dir_fd=descriptor, follow_symlinks=False)
            if (not stat.S_ISDIR(before.st_mode) or
                    _directory_identity(before) != anchor.ancestors[index]):
                raise DistributionArtifactSetBuildError(
                    "output ancestor identity changed")
            child = os.open(
                component, _directory_flags(), dir_fd=descriptor)
            opened = os.fstat(child)
            if _directory_identity(opened) != anchor.ancestors[index]:
                os.close(child)
                raise DistributionArtifactSetBuildError(
                    "output ancestor changed while opening")
            os.close(descriptor)
            descriptor = child
        _validate_parent(descriptor)
        before = os.stat(
            anchor.name, dir_fd=descriptor, follow_symlinks=False)
        file_descriptor = os.open(
            anchor.name,
            os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) |
            getattr(os, "O_NOFOLLOW", 0),
            dir_fd=descriptor)
        opened = os.fstat(file_descriptor)
        _validate_publication(opened, publication.size)
        if (_publication_identity(before) != publication.identity or
                _publication_identity(opened) != publication.identity):
            raise DistributionArtifactSetBuildError(
                "requested output does not name the published inode")
        observed = _read_descriptor(file_descriptor, publication.size)
        after_descriptor = os.fstat(file_descriptor)
        after_path = os.stat(
            anchor.name, dir_fd=descriptor, follow_symlinks=False)
        if (_publication_identity(after_descriptor) != publication.identity or
                _publication_identity(after_path) != publication.identity or
                hashlib.sha256(observed).digest() != publication.digest):
            raise DistributionArtifactSetBuildError(
                "requested publication changed while reading")
    except OSError as error:
        raise DistributionArtifactSetBuildError(
            "requested output path is unavailable or unsafe") from error
    finally:
        if file_descriptor >= 0:
            os.close(file_descriptor)
        if descriptor >= 0:
            os.close(descriptor)


def _cleanup_failed_publish(
        anchor: _OutputAnchor, temp_inode: tuple[int, int] | None,
        linked: bool, temporary: str) -> OSError | None:
    cleanup_error: OSError | None = None
    if temp_inode is not None:
        for name in (
                anchor.name if linked else "",
                temporary):
            if not name:
                continue
            try:
                _unlink_exact_inode(anchor.parent, name, temp_inode)
            except OSError as error:
                if cleanup_error is None:
                    cleanup_error = error
    try:
        os.fsync(anchor.parent)
    except OSError as error:
        if cleanup_error is None:
            cleanup_error = error
    try:
        os.close(anchor.parent)
    except OSError as error:
        if cleanup_error is None:
            cleanup_error = error
    return cleanup_error


def _publish_new_private(path: Path, payload: bytes) -> _Publication:
    anchor = _open_anchor(path)
    temporary = (
        f".{anchor.name}.{os.getpid()}.{secrets.token_hex(12)}.tmp")
    descriptor = -1
    temp_inode: tuple[int, int] | None = None
    linked = False
    try:
        flags = (
            os.O_RDWR | os.O_CREAT | os.O_EXCL |
            getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
        )
        descriptor = os.open(
            temporary, flags, 0o600, dir_fd=anchor.parent)
        view = memoryview(payload)
        while view:
            written = os.write(descriptor, view)
            if written <= 0:
                raise DistributionArtifactSetBuildError(
                    "short write while publishing artifact set")
            view = view[written:]
        os.fchmod(descriptor, 0o600)
        os.fsync(descriptor)
        temp_metadata = os.fstat(descriptor)
        _validate_publication(temp_metadata, len(payload))
        temp_inode = (temp_metadata.st_dev, temp_metadata.st_ino)
        observed = _read_descriptor(descriptor, len(payload))
        digest = hashlib.sha256(payload).digest()
        if hashlib.sha256(observed).digest() != digest:
            raise DistributionArtifactSetBuildError(
                "temporary artifact-set digest drift")
        temp_path_metadata = os.stat(
            temporary, dir_fd=anchor.parent, follow_symlinks=False)
        if (_publication_identity(temp_path_metadata) !=
                _publication_identity(temp_metadata)):
            raise DistributionArtifactSetBuildError(
                "temporary artifact-set inode changed before publication")
        try:
            os.link(
                temporary, anchor.name,
                src_dir_fd=anchor.parent, dst_dir_fd=anchor.parent,
                follow_symlinks=False)
        except FileExistsError as error:
            try:
                raced = os.stat(
                    anchor.name, dir_fd=anchor.parent,
                    follow_symlinks=False)
                linked = (
                    stat.S_ISREG(raced.st_mode) and
                    (raced.st_dev, raced.st_ino) == temp_inode)
            except FileNotFoundError:
                linked = False
            raise DistributionArtifactSetBuildError(
                "distribution artifact-set output already exists") from error
        linked = True
        after_link = os.fstat(descriptor)
        linked_path = os.stat(
            anchor.name, dir_fd=anchor.parent, follow_symlinks=False)
        _validate_publication(after_link, len(payload), links=2)
        if _publication_identity(after_link) != _publication_identity(
                linked_path):
            raise DistributionArtifactSetBuildError(
                "no-overwrite link did not bind the temp inode")
        if not _unlink_exact_inode(
                anchor.parent, temporary, temp_inode):
            raise DistributionArtifactSetBuildError(
                "temporary artifact-set name changed after publication")
        temporary = ""
        published = os.fstat(descriptor)
        final_path = os.stat(
            anchor.name, dir_fd=anchor.parent, follow_symlinks=False)
        _validate_publication(published, len(payload))
        if _publication_identity(published) != _publication_identity(
                final_path):
            raise DistributionArtifactSetBuildError(
                "published output is not the temp inode")
        os.fsync(anchor.parent)
        if (_directory_identity(_validate_parent(anchor.parent)) !=
                anchor.ancestors[-1]):
            raise DistributionArtifactSetBuildError(
                "anchored output parent identity changed")
        publication = _Publication(
            anchor=anchor, identity=_publication_identity(published),
            digest=digest, size=len(payload))
        _verify_requested_publication(publication)
        return publication
    except BaseException as error:
        if temp_inode is None and descriptor >= 0:
            try:
                metadata = os.fstat(descriptor)
                if stat.S_ISREG(metadata.st_mode):
                    temp_inode = (metadata.st_dev, metadata.st_ino)
            except OSError:
                pass
        cleanup_error = _cleanup_failed_publish(
            anchor, temp_inode, linked, temporary)
        if cleanup_error is not None:
            raise DistributionArtifactSetBuildError(
                "distribution artifact-set exact-inode rollback failed: "
                f"{cleanup_error}") from error
        if isinstance(error, OSError):
            raise DistributionArtifactSetBuildError(
                f"cannot publish distribution artifact set: {error}") from error
        raise
    finally:
        if descriptor >= 0:
            os.close(descriptor)


def publish_private_json(path: Path, document: Any) -> None:
    payload = verifier.canonical_json(document) + b"\n"
    publication = _publish_new_private(path, payload)
    os.close(publication.anchor.parent)


def build_and_publish(
        strict_source_tar: Path, source_manifest: Path,
        vendor_overlay_set: Path, runtime_tar: Path,
        runtime_manifest: Path, output: Path) -> dict[str, Any]:
    output_absolute = Path(os.path.abspath(output))
    inputs = {
        Path(os.path.abspath(path))
        for path in (
            strict_source_tar, source_manifest, vendor_overlay_set,
            runtime_tar, runtime_manifest)
    }
    if output_absolute in inputs:
        raise DistributionArtifactSetBuildError(
            "output must not alias an input artifact")
    document = verifier.build_artifact_set(
        strict_source_tar, source_manifest, vendor_overlay_set,
        runtime_tar, runtime_manifest)
    publish_private_json(output_absolute, document)
    return document


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Build a deterministic HeptaTrader distribution set")
    parser.add_argument("--strict-source-tar", type=Path, required=True)
    parser.add_argument("--source-manifest", type=Path, required=True)
    parser.add_argument("--vendor-overlay-set", type=Path, required=True)
    parser.add_argument("--runtime-tar", type=Path, required=True)
    parser.add_argument("--runtime-manifest", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    document = build_and_publish(
        args.strict_source_tar, args.source_manifest,
        args.vendor_overlay_set, args.runtime_tar,
        args.runtime_manifest, args.output)
    print(
        "PASS: "
        f"{document['release_version']} "
        f"{len(document['artifacts'])} artifacts "
        f"scope={document['scope']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
