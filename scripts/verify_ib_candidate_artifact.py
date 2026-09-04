#!/usr/bin/env python3
"""Create and verify a closed-world IB candidate artifact and provenance.

Candidate code is never executed by this module. The binary artifact binds the
exact source, an immutable SDK snapshot, a digest-pinned OCI rootfs/toolchain,
trusted builder files and explicit cgroup/filesystem resource policy.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import stat
import sys
import tarfile
import tempfile
from typing import Any, BinaryIO

SCHEMA = "heptatrader.ib-candidate-artifact.v2"
BUILDER_SCHEMA = "heptatrader.ib-builder-provenance.v1"
BINARY_NAME = "hepta-ib-executiond"
FULL_SHA = re.compile(r"^[0-9a-f]{40}$")
SHA256 = re.compile(r"^[0-9a-f]{64}$")
OCI_DIGEST = re.compile(r"^[a-z0-9][a-z0-9._/-]*@sha256:[0-9a-f]{64}$")
IMAGE_ID = re.compile(r"^sha256:[0-9a-f]{64}$")
MAX_ARCHIVE_BYTES = 512 * 1024 * 1024
MAX_BINARY_BYTES = 256 * 1024 * 1024
MAX_MANIFEST_BYTES = 128 * 1024
MAX_SDK_FILES = 100_000
MAX_SDK_BYTES = 8 * 1024 * 1024 * 1024
MAX_TRUSTED_FILES = 32
TRUSTED_BUILDER_FILES = (
    "scripts/build_ib_candidate_artifact.sh",
    "scripts/verify_ib_candidate_artifact.py",
    "scripts/verify_qualification_candidate.py",
    "scripts/github_qualification_evidence.py",
    ".github/workflows/ib-paper-qualification.yml",
)
MANIFEST_KEYS = frozenset(
    {
        "schema",
        "candidate_sha",
        "binary",
        "sdk_tree_sha256",
        "build_log_sha256",
        "builder",
        "isolation",
    }
)
BINARY_KEYS = frozenset({"name", "sha256", "size", "format"})
BUILDER_KEYS = frozenset(
    {
        "schema",
        "bundle_sha256",
        "image_reference",
        "image_id",
        "toolchain_sha256",
        "resource_policy_sha256",
        "trusted_files",
    }
)
ISOLATION = {
    "network": "none",
    "environment": "cleared",
    "rootfs": "read-only-digest-pinned-oci",
    "source_mount": "read-only-git-archive",
    "sdk_mount": "read-only-stable-snapshot",
    "writable_filesystem": "dedicated-size-bounded-mount",
    "resource_control": "oci-cgroup-memory-cpu-pids-plus-tmpfs-limits",
    "candidate_output": "captured-not-replayed",
}


class ArtifactError(ValueError):
    pass


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    value: dict[str, Any] = {}
    for key, item in pairs:
        if key in value:
            raise ArtifactError(f"duplicate JSON key: {key}")
        value[key] = item
    return value


def _canonical_bytes(value: Any) -> bytes:
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    ).encode("utf-8") + b"\n"


def _json_load(data: bytes, label: str, maximum: int = MAX_MANIFEST_BYTES) -> dict[str, Any]:
    if len(data) > maximum:
        raise ArtifactError(f"{label} exceeds {maximum} bytes")
    try:
        value = json.loads(
            data.decode("utf-8"),
            object_pairs_hook=_unique_object,
            parse_constant=lambda item: (_ for _ in ()).throw(
                ArtifactError(f"non-finite JSON number: {item}")
            ),
        )
    except (UnicodeError, json.JSONDecodeError, ArtifactError) as exc:
        raise ArtifactError(f"invalid {label}: {exc}") from exc
    if not isinstance(value, dict):
        raise ArtifactError(f"{label} must be an object")
    return value


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


def _regular(path: Path, label: str, maximum: int | None = None) -> os.stat_result:
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
        raise ArtifactError(f"unsafe tree path: {relative}")
    return relative


def hash_tree(root: Path) -> str:
    try:
        info = root.lstat()
    except OSError as exc:
        raise ArtifactError(f"tree root cannot be read: {exc}") from exc
    if stat.S_ISLNK(info.st_mode) or not stat.S_ISDIR(info.st_mode):
        raise ArtifactError("tree root must be a non-symlink directory")
    root = root.resolve(strict=True)
    rows: list[bytes] = []
    count = 0
    total = 0
    for path in sorted(root.rglob("*"), key=lambda item: item.relative_to(root).as_posix()):
        relative = _safe_relative(path, root)
        item = path.lstat()
        if stat.S_ISDIR(item.st_mode):
            rows.append(f"d\0{relative}\0".encode())
        elif stat.S_ISLNK(item.st_mode):
            target = os.readlink(path)
            parsed = PurePosixPath(target)
            if parsed.is_absolute() or ".." in parsed.parts:
                raise ArtifactError(f"tree symlink escapes root: {relative} -> {target}")
            rows.append(f"l\0{relative}\0{target}\0".encode())
        elif stat.S_ISREG(item.st_mode):
            count += 1
            total += item.st_size
            if count > MAX_SDK_FILES or total > MAX_SDK_BYTES:
                raise ArtifactError("tree exceeds bounded file or byte budget")
            digest, size = _sha256_file(path)
            rows.append(f"f\0{relative}\0{size}\0{digest}\0".encode())
        else:
            raise ArtifactError(f"tree contains unsupported file type: {relative}")
    if count == 0:
        raise ArtifactError("tree has no regular files")
    digest = hashlib.sha256()
    for row in rows:
        digest.update(row)
    return digest.hexdigest()


def snapshot_tree(source: Path, destination: Path) -> str:
    source = source.resolve(strict=True)
    if source.is_symlink() or not source.is_dir():
        raise ArtifactError("snapshot source must be a non-symlink directory")
    destination = destination.resolve()
    if destination.exists() or destination.is_symlink():
        raise ArtifactError("snapshot destination must not exist")
    before = hash_tree(source)
    destination.mkdir(parents=True, mode=0o700)
    try:
        for path in sorted(source.rglob("*"), key=lambda item: item.relative_to(source).as_posix()):
            relative = Path(_safe_relative(path, source))
            target = destination / relative
            info = path.lstat()
            if stat.S_ISDIR(info.st_mode):
                target.mkdir(mode=0o700)
            elif stat.S_ISLNK(info.st_mode):
                link = os.readlink(path)
                parsed = PurePosixPath(link)
                if parsed.is_absolute() or ".." in parsed.parts:
                    raise ArtifactError(f"SDK symlink escapes root: {relative} -> {link}")
                target.symlink_to(link)
            elif stat.S_ISREG(info.st_mode):
                target.parent.mkdir(parents=True, exist_ok=True)
                with path.open("rb") as src, target.open("xb") as dst:
                    shutil.copyfileobj(src, dst, 1024 * 1024)
                target.chmod(0o444)
            else:
                raise ArtifactError(f"SDK contains unsupported file type: {relative}")
        for directory in sorted(
            [item for item in destination.rglob("*") if item.is_dir()],
            key=lambda item: len(item.parts),
            reverse=True,
        ):
            directory.chmod(0o555)
        destination.chmod(0o555)
        after_source = hash_tree(source)
        snapshot = hash_tree(destination)
        if before != after_source or before != snapshot:
            raise ArtifactError("SDK source changed during snapshot or snapshot digest mismatched")
        return snapshot
    except Exception:
        shutil.rmtree(destination, ignore_errors=True)
        raise


def _trusted_file_digests(root: Path) -> dict[str, str]:
    root = root.resolve(strict=True)
    result: dict[str, str] = {}
    for relative in TRUSTED_BUILDER_FILES:
        path = root / relative
        _regular(path, f"trusted builder file {relative}", 8 * 1024 * 1024)
        result[relative] = _sha256_file(path)[0]
    return result


def _builder_body(value: dict[str, Any]) -> dict[str, Any]:
    return {key: value[key] for key in sorted(BUILDER_KEYS - {"bundle_sha256"})}


def validate_builder(value: Any, *, trusted_root: Path | None = None, expected_image: str | None = None) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) != BUILDER_KEYS:
        raise ArtifactError("builder provenance keys do not match the closed schema")
    if value.get("schema") != BUILDER_SCHEMA:
        raise ArtifactError("builder provenance schema mismatch")
    bundle = _validate_hex(value.get("bundle_sha256"), SHA256, "builder.bundle_sha256")
    image = value.get("image_reference")
    if not isinstance(image, str) or OCI_DIGEST.fullmatch(image) is None:
        raise ArtifactError("builder.image_reference is not a digest-pinned OCI reference")
    if expected_image is not None and image != expected_image:
        raise ArtifactError("builder image reference mismatch")
    _validate_hex(value.get("image_id"), IMAGE_ID, "builder.image_id")
    _validate_hex(value.get("toolchain_sha256"), SHA256, "builder.toolchain_sha256")
    _validate_hex(value.get("resource_policy_sha256"), SHA256, "builder.resource_policy_sha256")
    trusted = value.get("trusted_files")
    if not isinstance(trusted, dict) or len(trusted) > MAX_TRUSTED_FILES:
        raise ArtifactError("builder.trusted_files is invalid")
    for path, digest in trusted.items():
        if path not in TRUSTED_BUILDER_FILES or not isinstance(digest, str) or SHA256.fullmatch(digest) is None:
            raise ArtifactError("builder.trusted_files contains an invalid entry")
    if set(trusted) != set(TRUSTED_BUILDER_FILES):
        raise ArtifactError("builder.trusted_files does not bind the complete trusted builder set")
    expected_bundle = _sha256_bytes(_canonical_bytes(_builder_body(value)))
    if bundle != expected_bundle:
        raise ArtifactError("builder bundle digest mismatch")
    if trusted_root is not None and trusted != _trusted_file_digests(trusted_root):
        raise ArtifactError("builder trusted-file digests do not match trusted main")
    return value


def create_builder_provenance(
    trusted_root: Path,
    image_reference: str,
    image_id: str,
    toolchain_sha256: str,
    resource_policy_sha256: str,
) -> dict[str, Any]:
    if OCI_DIGEST.fullmatch(image_reference) is None:
        raise ArtifactError("builder image must be pinned by sha256 digest")
    _validate_hex(image_id, IMAGE_ID, "builder image id")
    _validate_hex(toolchain_sha256, SHA256, "toolchain SHA-256")
    _validate_hex(resource_policy_sha256, SHA256, "resource policy SHA-256")
    value: dict[str, Any] = {
        "schema": BUILDER_SCHEMA,
        "bundle_sha256": "0" * 64,
        "image_reference": image_reference,
        "image_id": image_id,
        "toolchain_sha256": toolchain_sha256,
        "resource_policy_sha256": resource_policy_sha256,
        "trusted_files": _trusted_file_digests(trusted_root),
    }
    value["bundle_sha256"] = _sha256_bytes(_canonical_bytes(_builder_body(value)))
    validate_builder(value, trusted_root=trusted_root, expected_image=image_reference)
    return value


def _validate_manifest(value: dict[str, Any], candidate: str, expected_builder: str | None = None, *, trusted_root: Path | None = None, expected_image: str | None = None) -> None:
    if set(value) != MANIFEST_KEYS:
        raise ArtifactError("manifest keys do not match the closed-world schema")
    if value.get("schema") != SCHEMA:
        raise ArtifactError("manifest schema mismatch")
    if value.get("candidate_sha") != candidate:
        raise ArtifactError("candidate SHA binding mismatch")
    _validate_hex(candidate, FULL_SHA, "candidate_sha")
    _validate_hex(value.get("sdk_tree_sha256"), SHA256, "sdk_tree_sha256")
    _validate_hex(value.get("build_log_sha256"), SHA256, "build_log_sha256")
    binary = value.get("binary")
    if not isinstance(binary, dict) or set(binary) != BINARY_KEYS:
        raise ArtifactError("binary manifest is invalid")
    if binary.get("name") != BINARY_NAME or binary.get("format") != "ELF64":
        raise ArtifactError("binary name or format mismatch")
    _validate_hex(binary.get("sha256"), SHA256, "binary.sha256")
    size = binary.get("size")
    if not isinstance(size, int) or isinstance(size, bool) or not 1 <= size <= MAX_BINARY_BYTES:
        raise ArtifactError("binary.size is outside the bounded range")
    builder = validate_builder(value.get("builder"), trusted_root=trusted_root, expected_image=expected_image)
    if expected_builder is not None and builder["bundle_sha256"] != expected_builder:
        raise ArtifactError("trusted builder bundle digest mismatch")
    if value.get("isolation") != ISOLATION:
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
    builder_provenance: dict[str, Any] | None = None,
) -> None:
    _validate_hex(candidate_sha, FULL_SHA, "candidate SHA")
    _validate_hex(sdk_sha256, SHA256, "SDK tree SHA-256")
    _validate_hex(builder_sha256, SHA256, "builder bundle SHA-256")
    info = _regular(binary_path, "candidate binary", MAX_BINARY_BYTES)
    binary = binary_path.read_bytes()
    if len(binary) != info.st_size or not binary.startswith(b"\x7fELF"):
        raise ArtifactError("candidate binary is not a stable ELF file")
    _regular(build_log_path, "captured build log", 128 * 1024 * 1024)
    build_log_digest, _ = _sha256_file(build_log_path)
    if builder_provenance is None:
        # Test-only compatibility path. Production CLI always supplies a complete
        # provenance document and the verifier can require a trusted root/image.
        trusted_files = {path: builder_sha256 for path in TRUSTED_BUILDER_FILES}
        builder_provenance = {
            "schema": BUILDER_SCHEMA,
            "bundle_sha256": "0" * 64,
            "image_reference": f"test-builder@sha256:{builder_sha256}",
            "image_id": f"sha256:{builder_sha256}",
            "toolchain_sha256": builder_sha256,
            "resource_policy_sha256": builder_sha256,
            "trusted_files": trusted_files,
        }
        builder_provenance["bundle_sha256"] = _sha256_bytes(
            _canonical_bytes(_builder_body(builder_provenance))
        )
        builder_sha256 = builder_provenance["bundle_sha256"]
    builder = validate_builder(builder_provenance)
    if builder["bundle_sha256"] != builder_sha256:
        raise ArtifactError("builder provenance does not match supplied bundle digest")
    manifest = {
        "schema": SCHEMA,
        "candidate_sha": candidate_sha,
        "binary": {
            "name": BINARY_NAME,
            "sha256": _sha256_bytes(binary),
            "size": len(binary),
            "format": "ELF64",
        },
        "sdk_tree_sha256": sdk_sha256,
        "build_log_sha256": build_log_digest,
        "builder": builder,
        "isolation": dict(ISOLATION),
    }
    _validate_manifest(manifest, candidate_sha, builder_sha256)
    manifest_bytes = _canonical_bytes(manifest)
    output = output.resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    if output.exists() or output.is_symlink():
        raise ArtifactError("artifact output must not already exist")
    descriptor, temporary = tempfile.mkstemp(prefix=output.name + ".", dir=output.parent)
    os.close(descriptor)
    try:
        os.chmod(temporary, 0o600)
        with tarfile.open(temporary, mode="w", format=tarfile.USTAR_FORMAT) as archive:
            info_manifest, stream_manifest = _tar_member("manifest.json", manifest_bytes, 0o400)
            archive.addfile(info_manifest, stream_manifest)
            info_binary, stream_binary = _tar_member(BINARY_NAME, binary, 0o500)
            archive.addfile(info_binary, stream_binary)
        if Path(temporary).stat().st_size > MAX_ARCHIVE_BYTES:
            raise ArtifactError("candidate artifact archive exceeds size budget")
        os.replace(temporary, output)
    except Exception:
        try:
            os.unlink(temporary)
        except OSError:
            pass
        raise


def _read_archive(
    archive_path: Path,
    expected_candidate: str,
    expected_builder: str | None,
    trusted_root: Path | None,
    expected_image: str | None,
) -> tuple[dict[str, Any], bytes]:
    _regular(archive_path, "candidate artifact archive", MAX_ARCHIVE_BYTES)
    try:
        with tarfile.open(archive_path, mode="r:") as archive:
            members = archive.getmembers()
            if [item.name for item in members] != ["manifest.json", BINARY_NAME]:
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
    _validate_manifest(
        manifest,
        expected_candidate,
        expected_builder,
        trusted_root=trusted_root,
        expected_image=expected_image,
    )
    binary_meta = manifest["binary"]
    if len(binary) != binary_meta["size"] or _sha256_bytes(binary) != binary_meta["sha256"]:
        raise ArtifactError("candidate binary does not match manifest")
    if not binary.startswith(b"\x7fELF"):
        raise ArtifactError("candidate binary is not ELF")
    return manifest, binary


def verify_and_extract(
    archive_path: Path,
    expected_candidate: str,
    expected_builder: str | None,
    destination: Path,
    *,
    trusted_root: Path | None = None,
    expected_image: str | None = None,
) -> None:
    _validate_hex(expected_candidate, FULL_SHA, "expected candidate SHA")
    if expected_builder is not None:
        _validate_hex(expected_builder, SHA256, "expected builder SHA-256")
    manifest, binary = _read_archive(
        archive_path, expected_candidate, expected_builder, trusted_root, expected_image
    )
    destination = destination.resolve()
    destination.parent.mkdir(parents=True, exist_ok=True)
    if destination.exists() or destination.is_symlink():
        raise ArtifactError("extraction destination must not already exist")
    temporary = Path(tempfile.mkdtemp(prefix=destination.name + ".", dir=destination.parent))
    try:
        os.chmod(temporary, 0o700)
        binary_path = temporary / BINARY_NAME
        descriptor = os.open(
            binary_path,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0),
            0o500,
        )
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(binary)
            stream.flush()
            os.fsync(stream.fileno())
        (temporary / "manifest.json").write_bytes(_canonical_bytes(manifest))
        os.chmod(temporary / "manifest.json", 0o400)
        os.replace(temporary, destination)
    except Exception:
        shutil.rmtree(temporary, ignore_errors=True)
        raise


def _write_private(path: Path, data: bytes) -> None:
    path = path.resolve()
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists() or path.is_symlink():
        raise ArtifactError(f"output already exists: {path}")
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0), 0o600)
    with os.fdopen(descriptor, "wb") as stream:
        stream.write(data)
        stream.flush()
        os.fsync(stream.fileno())


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    subparsers = parser.add_subparsers(dest="command", required=True)
    tree = subparsers.add_parser("hash-tree")
    tree.add_argument("--root", required=True, type=Path)
    snapshot = subparsers.add_parser("snapshot-tree")
    snapshot.add_argument("--source", required=True, type=Path)
    snapshot.add_argument("--destination", required=True, type=Path)
    provenance = subparsers.add_parser("builder-provenance")
    provenance.add_argument("--trusted-root", required=True, type=Path)
    provenance.add_argument("--image-reference", required=True)
    provenance.add_argument("--image-id", required=True)
    provenance.add_argument("--toolchain-sha256", required=True)
    provenance.add_argument("--resource-policy-sha256", required=True)
    provenance.add_argument("--output", required=True, type=Path)
    pack_parser = subparsers.add_parser("pack")
    pack_parser.add_argument("--binary", required=True, type=Path)
    pack_parser.add_argument("--candidate-sha", required=True)
    pack_parser.add_argument("--sdk-sha256", required=True)
    pack_parser.add_argument("--builder-provenance", required=True, type=Path)
    pack_parser.add_argument("--build-log", required=True, type=Path)
    pack_parser.add_argument("--output", required=True, type=Path)
    verify_parser = subparsers.add_parser("verify")
    verify_parser.add_argument("--archive", required=True, type=Path)
    verify_parser.add_argument("--expected-candidate-sha", required=True)
    verify_parser.add_argument("--expected-builder-sha256")
    verify_parser.add_argument("--expected-builder-image")
    verify_parser.add_argument("--trusted-root", type=Path)
    verify_parser.add_argument("--destination", required=True, type=Path)
    args = parser.parse_args(argv)
    try:
        if args.command == "hash-tree":
            print(hash_tree(args.root))
        elif args.command == "snapshot-tree":
            print(snapshot_tree(args.source, args.destination))
        elif args.command == "builder-provenance":
            value = create_builder_provenance(
                args.trusted_root,
                args.image_reference,
                args.image_id,
                args.toolchain_sha256,
                args.resource_policy_sha256,
            )
            _write_private(args.output, _canonical_bytes(value))
            print(value["bundle_sha256"])
        elif args.command == "pack":
            provenance_value = _json_load(args.builder_provenance.read_bytes(), "builder provenance")
            builder = validate_builder(provenance_value)
            pack(
                args.binary,
                args.candidate_sha,
                args.sdk_sha256,
                builder["bundle_sha256"],
                args.build_log,
                args.output,
                builder,
            )
            print("[IB-CANDIDATE-ARTIFACT] PACKED")
        else:
            verify_and_extract(
                args.archive,
                args.expected_candidate_sha,
                args.expected_builder_sha256,
                args.destination,
                trusted_root=args.trusted_root,
                expected_image=args.expected_builder_image,
            )
            print("[IB-CANDIDATE-ARTIFACT] PASS")
        return 0
    except (ArtifactError, OSError, UnicodeError, ValueError, json.JSONDecodeError) as exc:
        print(f"[IB-CANDIDATE-ARTIFACT] {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
