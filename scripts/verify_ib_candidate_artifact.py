#!/usr/bin/env python3
"""Create and verify a content-addressed IB candidate binary artifact.

This program is trusted qualification infrastructure. It never executes the
candidate binary and it treats every archive byte as hostile input.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import stat
import sys
import tarfile
import tempfile
from typing import Any, BinaryIO

SCHEMA = "heptatrader.ib-candidate-artifact.v1"
BINARY_NAME = "hepta-ib-executiond"
FULL_SHA = re.compile(r"^[0-9a-f]{40}$")
SHA256 = re.compile(r"^[0-9a-f]{64}$")
MAX_ARCHIVE_BYTES = 512 * 1024 * 1024
MAX_BINARY_BYTES = 256 * 1024 * 1024
MAX_MANIFEST_BYTES = 64 * 1024
MAX_SDK_FILES = 100_000
MAX_SDK_BYTES = 8 * 1024 * 1024 * 1024
MANIFEST_KEYS = frozenset(
    {
        "schema",
        "candidate_sha",
        "binary",
        "sdk_tree_sha256",
        "builder_sha256",
        "build_log_sha256",
        "isolation",
    }
)
BINARY_KEYS = frozenset({"name", "sha256", "size", "format"})
ISOLATION_KEYS = frozenset(
    {
        "network_namespace",
        "environment",
        "source_mount",
        "sdk_mount",
        "candidate_output",
    }
)


class ArtifactError(ValueError):
    """Raised when a candidate artifact violates its closed-world contract."""


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ArtifactError(f"duplicate JSON key: {key}")
        result[key] = value
    return result


def _json_load(data: bytes, label: str) -> dict[str, Any]:
    if len(data) > MAX_MANIFEST_BYTES:
        raise ArtifactError(f"{label} exceeds {MAX_MANIFEST_BYTES} bytes")
    try:
        value = json.loads(
            data.decode("utf-8"),
            object_pairs_hook=_unique_object,
            parse_constant=lambda value: (_ for _ in ()).throw(
                ArtifactError(f"non-finite JSON number: {value}")
            ),
        )
    except (UnicodeError, json.JSONDecodeError, ArtifactError) as exc:
        raise ArtifactError(f"invalid {label}: {exc}") from exc
    if not isinstance(value, dict):
        raise ArtifactError(f"{label} must be an object")
    return value


def _canonical_bytes(value: Any) -> bytes:
    return (
        json.dumps(
            value,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
            allow_nan=False,
        ).encode("utf-8")
        + b"\n"
    )


def _sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _sha256_file(path: Path, maximum: int | None = None) -> tuple[str, int]:
    digest = hashlib.sha256()
    size = 0
    with path.open("rb") as stream:
        while True:
            block = stream.read(1024 * 1024)
            if not block:
                break
            size += len(block)
            if maximum is not None and size > maximum:
                raise ArtifactError(f"{path} exceeds {maximum} bytes")
            digest.update(block)
    return digest.hexdigest(), size


def _regular_file(path: Path, label: str, maximum: int | None = None) -> os.stat_result:
    try:
        info = path.lstat()
    except OSError as exc:
        raise ArtifactError(f"{label} cannot be read: {exc}") from exc
    if stat.S_ISLNK(info.st_mode) or not stat.S_ISREG(info.st_mode):
        raise ArtifactError(f"{label} must be a regular non-symlink file")
    if info.st_nlink != 1:
        raise ArtifactError(f"{label} must have exactly one hard link")
    if maximum is not None and info.st_size > maximum:
        raise ArtifactError(f"{label} exceeds {maximum} bytes")
    return info


def _validate_hex(value: object, pattern: re.Pattern[str], label: str) -> str:
    if not isinstance(value, str) or pattern.fullmatch(value) is None:
        raise ArtifactError(f"{label} is not canonical")
    return value


def _safe_relative(path: Path, root: Path) -> str:
    relative = path.relative_to(root).as_posix()
    parsed = PurePosixPath(relative)
    if not parsed.parts or parsed.is_absolute() or ".." in parsed.parts:
        raise ArtifactError(f"unsafe SDK path: {relative}")
    return relative


def hash_tree(root: Path) -> str:
    try:
        root_info = root.lstat()
    except OSError as exc:
        raise ArtifactError(f"SDK root cannot be read: {exc}") from exc
    if stat.S_ISLNK(root_info.st_mode) or not stat.S_ISDIR(root_info.st_mode):
        raise ArtifactError("SDK root must be a non-symlink directory")
    root = root.resolve(strict=True)
    rows: list[bytes] = []
    file_count = 0
    total_size = 0
    for path in sorted(root.rglob("*"), key=lambda item: item.relative_to(root).as_posix()):
        relative = _safe_relative(path, root)
        info = path.lstat()
        if stat.S_ISDIR(info.st_mode):
            rows.append(f"d\0{relative}\0".encode("utf-8"))
            continue
        if stat.S_ISLNK(info.st_mode):
            target = os.readlink(path)
            target_path = PurePosixPath(target)
            if target_path.is_absolute() or ".." in target_path.parts:
                raise ArtifactError(f"SDK symlink escapes root: {relative} -> {target}")
            rows.append(f"l\0{relative}\0{target}\0".encode("utf-8"))
            continue
        if not stat.S_ISREG(info.st_mode):
            raise ArtifactError(f"SDK contains unsupported file type: {relative}")
        file_count += 1
        total_size += info.st_size
        if file_count > MAX_SDK_FILES or total_size > MAX_SDK_BYTES:
            raise ArtifactError("SDK tree exceeds bounded file or byte budget")
        digest, size = _sha256_file(path)
        rows.append(f"f\0{relative}\0{size}\0{digest}\0".encode("utf-8"))
    if file_count == 0:
        raise ArtifactError("SDK tree has no regular files")
    digest = hashlib.sha256()
    for row in rows:
        digest.update(row)
    return digest.hexdigest()


def _validate_manifest(value: dict[str, Any], expected_candidate: str, expected_builder: str) -> None:
    if set(value) != MANIFEST_KEYS:
        raise ArtifactError("manifest keys do not match the closed-world schema")
    if value.get("schema") != SCHEMA:
        raise ArtifactError("manifest schema mismatch")
    if value.get("candidate_sha") != expected_candidate:
        raise ArtifactError("candidate SHA binding mismatch")
    _validate_hex(value.get("candidate_sha"), FULL_SHA, "candidate_sha")
    _validate_hex(value.get("sdk_tree_sha256"), SHA256, "sdk_tree_sha256")
    if value.get("builder_sha256") != expected_builder:
        raise ArtifactError("trusted builder digest mismatch")
    _validate_hex(value.get("builder_sha256"), SHA256, "builder_sha256")
    _validate_hex(value.get("build_log_sha256"), SHA256, "build_log_sha256")
    binary = value.get("binary")
    if not isinstance(binary, dict) or set(binary) != BINARY_KEYS:
        raise ArtifactError("binary manifest is invalid")
    if binary.get("name") != BINARY_NAME or binary.get("format") != "ELF64":
        raise ArtifactError("binary name or format mismatch")
    _validate_hex(binary.get("sha256"), SHA256, "binary.sha256")
    size = binary.get("size")
    if not isinstance(size, int) or isinstance(size, bool) or not (1 <= size <= MAX_BINARY_BYTES):
        raise ArtifactError("binary.size is outside the bounded range")
    isolation = value.get("isolation")
    expected_isolation = {
        "network_namespace": "unshared",
        "environment": "cleared",
        "source_mount": "read-only-archive",
        "sdk_mount": "read-only",
        "candidate_output": "captured-not-replayed",
    }
    if not isinstance(isolation, dict) or set(isolation) != ISOLATION_KEYS:
        raise ArtifactError("isolation manifest is invalid")
    if isolation != expected_isolation:
        raise ArtifactError("candidate isolation claim does not match trusted policy")


def _tar_member(name: str, data: bytes, mode: int) -> tuple[tarfile.TarInfo, BinaryIO]:
    import io

    info = tarfile.TarInfo(name=name)
    info.size = len(data)
    info.mode = mode
    info.uid = 0
    info.gid = 0
    info.uname = ""
    info.gname = ""
    info.mtime = 0
    return info, io.BytesIO(data)


def pack(
    binary_path: Path,
    candidate_sha: str,
    sdk_sha256: str,
    builder_sha256: str,
    build_log_path: Path,
    output: Path,
) -> None:
    _validate_hex(candidate_sha, FULL_SHA, "candidate SHA")
    _validate_hex(sdk_sha256, SHA256, "SDK tree SHA-256")
    _validate_hex(builder_sha256, SHA256, "builder SHA-256")
    info = _regular_file(binary_path, "candidate binary", MAX_BINARY_BYTES)
    binary = binary_path.read_bytes()
    if len(binary) != info.st_size or not binary.startswith(b"\x7fELF"):
        raise ArtifactError("candidate binary is not a stable ELF file")
    binary_digest = _sha256_bytes(binary)
    _regular_file(build_log_path, "captured build log", 128 * 1024 * 1024)
    build_log_digest, _ = _sha256_file(build_log_path)
    manifest = {
        "schema": SCHEMA,
        "candidate_sha": candidate_sha,
        "binary": {
            "name": BINARY_NAME,
            "sha256": binary_digest,
            "size": len(binary),
            "format": "ELF64",
        },
        "sdk_tree_sha256": sdk_sha256,
        "builder_sha256": builder_sha256,
        "build_log_sha256": build_log_digest,
        "isolation": {
            "network_namespace": "unshared",
            "environment": "cleared",
            "source_mount": "read-only-archive",
            "sdk_mount": "read-only",
            "candidate_output": "captured-not-replayed",
        },
    }
    _validate_manifest(manifest, candidate_sha, builder_sha256)
    manifest_bytes = _canonical_bytes(manifest)
    output = output.resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    if output.exists() or output.is_symlink():
        raise ArtifactError("artifact output must not already exist")
    fd, temporary = tempfile.mkstemp(prefix=output.name + ".", dir=output.parent)
    os.close(fd)
    try:
        os.chmod(temporary, 0o600)
        with tarfile.open(temporary, mode="w", format=tarfile.USTAR_FORMAT) as archive:
            manifest_info, manifest_stream = _tar_member("manifest.json", manifest_bytes, 0o400)
            archive.addfile(manifest_info, manifest_stream)
            binary_info, binary_stream = _tar_member(BINARY_NAME, binary, 0o500)
            archive.addfile(binary_info, binary_stream)
        if Path(temporary).stat().st_size > MAX_ARCHIVE_BYTES:
            raise ArtifactError("candidate artifact archive exceeds size budget")
        os.replace(temporary, output)
    except Exception:
        try:
            os.unlink(temporary)
        except OSError:
            pass
        raise


def _read_archive(archive_path: Path, expected_candidate: str, expected_builder: str) -> tuple[dict[str, Any], bytes]:
    _regular_file(archive_path, "candidate artifact archive", MAX_ARCHIVE_BYTES)
    try:
        with tarfile.open(archive_path, mode="r:") as archive:
            members = archive.getmembers()
            names = [member.name for member in members]
            if names != ["manifest.json", BINARY_NAME] or len(set(names)) != len(names):
                raise ArtifactError("archive member set/order is not canonical")
            for member in members:
                parsed = PurePosixPath(member.name)
                if parsed.is_absolute() or ".." in parsed.parts or not member.isfile():
                    raise ArtifactError("archive contains an unsafe member")
                if member.uid != 0 or member.gid != 0 or member.mtime != 0:
                    raise ArtifactError("archive metadata is not canonical")
            manifest_member, binary_member = members
            if manifest_member.size > MAX_MANIFEST_BYTES or binary_member.size > MAX_BINARY_BYTES:
                raise ArtifactError("archive member exceeds size budget")
            manifest_stream = archive.extractfile(manifest_member)
            binary_stream = archive.extractfile(binary_member)
            if manifest_stream is None or binary_stream is None:
                raise ArtifactError("archive member cannot be read")
            manifest_bytes = manifest_stream.read(MAX_MANIFEST_BYTES + 1)
            binary = binary_stream.read(MAX_BINARY_BYTES + 1)
    except (tarfile.TarError, OSError) as exc:
        raise ArtifactError(f"candidate archive cannot be parsed: {exc}") from exc
    manifest = _json_load(manifest_bytes, "candidate manifest")
    _validate_manifest(manifest, expected_candidate, expected_builder)
    binary_meta = manifest["binary"]
    if len(binary) != binary_meta["size"] or _sha256_bytes(binary) != binary_meta["sha256"]:
        raise ArtifactError("candidate binary does not match manifest")
    if not binary.startswith(b"\x7fELF"):
        raise ArtifactError("candidate binary is not ELF")
    return manifest, binary


def verify_and_extract(
    archive_path: Path,
    expected_candidate: str,
    expected_builder: str,
    destination: Path,
) -> None:
    _validate_hex(expected_candidate, FULL_SHA, "expected candidate SHA")
    _validate_hex(expected_builder, SHA256, "expected builder SHA-256")
    manifest, binary = _read_archive(archive_path, expected_candidate, expected_builder)
    destination = destination.resolve()
    parent = destination.parent
    parent.mkdir(parents=True, exist_ok=True)
    if destination.exists() or destination.is_symlink():
        raise ArtifactError("extraction destination must not already exist")
    temporary = Path(tempfile.mkdtemp(prefix=destination.name + ".", dir=parent))
    try:
        os.chmod(temporary, 0o700)
        binary_path = temporary / BINARY_NAME
        descriptor = os.open(
            binary_path,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0),
            0o500,
        )
        try:
            with os.fdopen(descriptor, "wb") as stream:
                stream.write(binary)
                stream.flush()
                os.fsync(stream.fileno())
        except Exception:
            try:
                os.close(descriptor)
            except OSError:
                pass
            raise
        (temporary / "manifest.json").write_bytes(_canonical_bytes(manifest))
        os.chmod(temporary / "manifest.json", 0o400)
        os.replace(temporary, destination)
    except Exception:
        import shutil

        shutil.rmtree(temporary, ignore_errors=True)
        raise


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    subparsers = parser.add_subparsers(dest="command", required=True)

    tree = subparsers.add_parser("hash-tree")
    tree.add_argument("--root", required=True, type=Path)

    pack_parser = subparsers.add_parser("pack")
    pack_parser.add_argument("--binary", required=True, type=Path)
    pack_parser.add_argument("--candidate-sha", required=True)
    pack_parser.add_argument("--sdk-sha256", required=True)
    pack_parser.add_argument("--builder-sha256", required=True)
    pack_parser.add_argument("--build-log", required=True, type=Path)
    pack_parser.add_argument("--output", required=True, type=Path)

    verify_parser = subparsers.add_parser("verify")
    verify_parser.add_argument("--archive", required=True, type=Path)
    verify_parser.add_argument("--expected-candidate-sha", required=True)
    verify_parser.add_argument("--expected-builder-sha256", required=True)
    verify_parser.add_argument("--destination", required=True, type=Path)

    args = parser.parse_args(argv)
    try:
        if args.command == "hash-tree":
            print(hash_tree(args.root))
        elif args.command == "pack":
            pack(
                args.binary,
                args.candidate_sha,
                args.sdk_sha256,
                args.builder_sha256,
                args.build_log,
                args.output,
            )
            print("[IB-CANDIDATE-ARTIFACT] PACKED")
        else:
            verify_and_extract(
                args.archive,
                args.expected_candidate_sha,
                args.expected_builder_sha256,
                args.destination,
            )
            print("[IB-CANDIDATE-ARTIFACT] PASS")
        return 0
    except (ArtifactError, OSError, UnicodeError, ValueError) as exc:
        print(f"[IB-CANDIDATE-ARTIFACT] {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
