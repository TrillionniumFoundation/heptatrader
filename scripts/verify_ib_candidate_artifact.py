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
    "scripts/run_ib_paper_artifact_qualification.sh",
    "scripts/run_ib_paper_campaign.py",
    "scripts/verify_ib_paper_qualification.py",
    "docs/ib-paper-profile-policy-v1.json",
    ".github/retired-workflows/ib-paper-qualification.yml",
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



def _read_builder_evidence(path: Path, maximum: int = 2 * 1024 * 1024,
                           *, private: bool = True) -> bytes:
    fd = os.open(path, os.O_RDONLY | os.O_NONBLOCK | os.O_CLOEXEC | os.O_NOFOLLOW)
    try:
        before = os.fstat(fd)
        if (not stat.S_ISREG(before.st_mode) or before.st_nlink != 1 or
                before.st_uid != os.geteuid() or before.st_size > maximum or
                (private and before.st_mode & 0o077)):
            raise ArtifactError("builder evidence must be a bounded regular owner file")
        data = os.pread(fd, maximum + 1, 0)
        after = os.fstat(fd)
        identity = lambda x: (x.st_dev, x.st_ino, x.st_mode, x.st_nlink, x.st_uid,
                               x.st_size, x.st_mtime_ns, x.st_ctime_ns)
        if len(data) != before.st_size or identity(before) != identity(after):
            raise ArtifactError("builder evidence changed while reading")
        return data
    finally:
        os.close(fd)


def canonical_image_reference(reference: str) -> str:
    if OCI_DIGEST.fullmatch(reference) is None:
        raise ArtifactError("image reference must be digest-pinned")
    name, digest = reference.split("@", 1)
    first = name.split("/", 1)[0]
    if "/" not in name:
        name = "docker.io/library/" + name
    elif "." not in first and ":" not in first and first != "localhost":
        name = "docker.io/" + name
    components = name.split("/")
    if len(components) == 2 and components[0] == "docker.io":
        name = "/".join((components[0], "library", components[1]))
    return name + "@" + digest


def image_identity(inspected: Any, requested: str, engine: str,
                   engine_version: str, engine_sha256: str) -> dict[str, Any]:
    expected = canonical_image_reference(requested)
    if engine not in {"docker", "podman"} or not engine_version.lower().startswith(engine + " version "):
        raise ArtifactError("selected engine and actual engine version disagree")
    if len(engine_version) > 512 or "\n" in engine_version or "\r" in engine_version:
        raise ArtifactError("engine version must be one bounded line")
    _validate_hex(engine_sha256, SHA256, "engine executable SHA-256")
    if not isinstance(inspected, list) or len(inspected) != 1 or not isinstance(inspected[0], dict):
        raise ArtifactError("image inspect must return exactly one image")
    value = inspected[0]
    raw_id = value.get("Id")
    image_id = "sha256:" + raw_id if isinstance(raw_id, str) and SHA256.fullmatch(raw_id) else raw_id
    _validate_hex(image_id, IMAGE_ID, "actual image id")
    digests = value.get("RepoDigests")
    if not isinstance(digests, list) or not digests or len(digests) > 128 or not all(isinstance(x, str) for x in digests):
        raise ArtifactError("actual RepoDigests is missing or invalid")
    matched = next((x for x in digests if canonical_image_reference(x) == expected), None)
    if matched is None:
        raise ArtifactError("local image has no exact requested repository digest")
    return {"schema": "heptatrader.ib-builder-image-identity.v1", "engine": engine,
            "engine_version": engine_version, "engine_executable_sha256": engine_sha256,
            "requested_image_reference": requested, "matched_repo_digest": matched,
            "observed_repo_digests": digests, "raw_image_id": raw_id, "image_id": image_id}


def sdk_identity(root: Path, dependency_receipt: Path | None = None) -> dict[str, Any]:
    tree = hash_tree(root)
    _regular(root / "libbid.a", "SDK BID archive", MAX_SDK_BYTES)
    bid, _ = _sha256_file(root / "libbid.a", MAX_SDK_BYTES)
    files = {}
    for path in sorted(root.rglob("*")):
        info = path.lstat()
        if stat.S_ISREG(info.st_mode):
            digest, size = _sha256_file(path)
            files[path.relative_to(root).as_posix()] = {"sha256": digest, "size": size}
    value = {"schema": "heptatrader.ib-builder-sdk-identity.v1", "sdk_tree_sha256": tree,
             "bid_sha256": bid, "files": files, "sdk_version": None,
             "upstream_archive_sha256_claim": None, "dependency_receipt_sha256": None,
             "version_evidence": "UNKNOWN", "upstream_archive_mapping_verified": False,
             "bid_version": None}
    if dependency_receipt is not None:
        raw = _read_builder_evidence(dependency_receipt, private=False)
        receipt = _json_load(raw, "owner dependency receipt")
        if set(receipt) != {"sdk_version", "sdk_sha256", "bid_sha256"}:
            raise ArtifactError("owner dependency receipt keys mismatch")
        version = receipt["sdk_version"]
        if not isinstance(version, str) or re.fullmatch(r"[0-9]+(?:\.[0-9]+){1,3}", version) is None:
            raise ArtifactError("owner SDK version is invalid")
        _validate_hex(receipt["sdk_sha256"], SHA256, "claimed SDK archive SHA-256")
        if receipt["bid_sha256"] != bid:
            raise ArtifactError("owner receipt does not match actual BID archive")
        value.update(sdk_version=version, upstream_archive_sha256_claim=receipt["sdk_sha256"],
                     dependency_receipt_sha256=_sha256_bytes(raw), version_evidence="OWNER_RECEIPT")
    return value


def container_cid(path: Path, proof: dict[str, Any] | None = None) -> tuple[str, dict[str, int]]:
    fd = os.open(path, os.O_RDONLY | os.O_NONBLOCK | os.O_CLOEXEC | os.O_NOFOLLOW)
    try:
        before = os.fstat(fd)
        if not stat.S_ISREG(before.st_mode) or before.st_nlink != 1 or before.st_uid != os.geteuid() or before.st_size > 65:
            raise ArtifactError("container cidfile is not an owner regular file")
        raw = os.pread(fd, 66, 0)
        after = os.fstat(fd)
        fields = ("st_dev", "st_ino", "st_uid", "st_mode", "st_nlink", "st_size", "st_mtime_ns", "st_ctime_ns")
        identity = {key: getattr(before, key) for key in fields}
        if identity != {key: getattr(after, key) for key in fields} or identity != {key: getattr(path.lstat(), key) for key in fields}:
            raise ArtifactError("container cidfile changed while reading")
        cid = raw.decode("ascii").removesuffix("\n")
        _validate_hex(cid, SHA256, "container id")
        if proof is not None and (proof.get("container_id") != cid or proof.get("cidfile_identity") != identity):
            raise ArtifactError("container cidfile differs from original proof")
        return cid, identity
    finally:
        os.close(fd)


def container_identity(inspected: Any, intent: dict[str, Any], cidfile: Path,
                       proof: dict[str, Any] | None = None,
                       adopt_missing_cid: bool = False) -> dict[str, Any]:
    import datetime
    import time
    if (intent.get("cid_file") != str(cidfile) or
            re.fullmatch(r"[0-9a-f]{32}", str(intent.get("operation"))) is None or
            intent.get("phase") not in {"toolchain", "configure", "compile", "probe"} or
            type(intent.get("created_at_ms")) is not int or
            type(intent.get("sequence")) is not int or intent["sequence"] < 1 or
            intent.get("engine") not in {"docker", "podman"} or
            intent.get("name") != "hepta-ib-build-"+str(intent.get("operation"))+"-"+str(intent.get("sequence"))):
        raise ArtifactError("container creation intent is invalid")
    if not isinstance(inspected, list) or len(inspected) != 1 or not isinstance(inspected[0], dict):
        raise ArtifactError("container inspect must contain exactly one object")
    value = inspected[0]
    cid = _validate_hex(value.get("Id"), SHA256, "inspected container id")
    name = value.get("Name")
    if not isinstance(name, str) or name.removeprefix("/") != intent.get("name"):
        raise ArtifactError("container does not match its exact random name")
    image = value.get("Image")
    if isinstance(image, str) and SHA256.fullmatch(image): image = "sha256:" + image
    config = value.get("Config")
    state = value.get("State")
    if not isinstance(config, dict) or not isinstance(state, dict):
        raise ArtifactError("container configuration/state is missing")
    labels = config.get("Labels", {})
    expected_labels = {"io.heptatrader.ib-builder.operation": intent["operation"],
                       "io.heptatrader.ib-builder.phase": intent["phase"],
                       "io.heptatrader.ib-builder.sequence": str(intent["sequence"])}
    if (value.get("Id") != cid or image != intent["image_id"] or
            not isinstance(labels, dict) or any(labels.get(k) != v for k, v in expected_labels.items())):
        raise ArtifactError("container identity/labels do not match this build intent")
    _validate_hex(intent.get("image_id"), IMAGE_ID, "intended container image")
    created = value.get("Created")
    if not isinstance(created, str): raise ArtifactError("container creation time is missing")
    created_value = datetime.datetime.fromisoformat(created.replace("Z", "+00:00"))
    if created_value.tzinfo is None: raise ArtifactError("container creation time lacks a timezone")
    created_ms = int(created_value.timestamp() * 1000)
    if not intent["created_at_ms"] - 1000 <= created_ms <= int(time.time() * 1000) + 1000:
        raise ArtifactError("container creation time predates build intent")
    running = state.get("Running")
    if type(running) is not bool: raise ArtifactError("container running state is missing")
    if adopt_missing_cid and proof is None and not cidfile.exists() and not cidfile.is_symlink():
        # Only a fresh exact-name/label/image/creation match above can recover
        # a create whose CLI died before publishing its private cidfile.
        _write_private(cidfile, (cid + "\n").encode("ascii"))
    recorded_cid, file_identity = container_cid(cidfile, proof)
    if recorded_cid != cid:
        raise ArtifactError("inspected container differs from its cidfile")
    result = {"container_id": cid, "name": intent["name"], "image_id": image, "labels": expected_labels,
              "created": created, "cidfile_identity": file_identity}
    if proof is not None and result != proof:
        raise ArtifactError("container differs from its pre-start proof")
    status = state.get("Status")
    exit_code = state.get("ExitCode")
    oom = state.get("OOMKilled")
    error = state.get("Error")
    success = (not running and status == "exited" and type(exit_code) is int and
               exit_code == 0 and type(oom) is bool and not oom and isinstance(error, str) and not error)
    completion = {"running": running, "status": status if isinstance(status, str) else None,
                  "exit_code": exit_code if type(exit_code) is int else None,
                  "oom_killed": oom if type(oom) is bool else None,
                  "has_error": not isinstance(error, str) or bool(error), "success": success}
    return {**result, "running": running, "completion": completion}


BUILDER_EVIDENCE_FILES = ("builder-provenance.json", "resource-policy.json", "toolchain.txt",
                          "image-identity.json", "sdk-provenance.json")


MAX_DIAGNOSTIC_LOG_BYTES = 1024 * 1024
BUILD_PHASES = frozenset({"setup", "source", "sdk", "toolchain", "configure",
                         "compile", "probe", "integrity", "package", "completed"})


def capture_builder_diagnostics(build_log: Path, candidate_sha: str,
                                phase: str, exit_code: int, output: Path,
                                evidence_root: Path | None = None) -> None:
    """Retain inert, bounded build output, never a qualification receipt.

    Only the compiler/probe log from the broker-disabled container is accepted.
    Optional fixed-name metadata binds the image, toolchain, resource policy
    and SDK/BID inventory. No source/SDK bytes, environment or credentials are copied.
    The log is not replayed on stdout: candidate text cannot issue Actions commands.
    """
    _validate_hex(candidate_sha, FULL_SHA, "candidate SHA")
    if phase not in BUILD_PHASES or type(exit_code) is not int or not 0 <= exit_code <= 255:
        raise ArtifactError("invalid builder diagnostic phase or exit code")
    data = b""
    source_bytes = None
    try:
        fd = os.open(build_log, os.O_RDONLY | os.O_NONBLOCK | os.O_CLOEXEC | os.O_NOFOLLOW)
    except FileNotFoundError:
        pass  # Setup can fail before the compiler log is created.
    else:
        try:
            before = os.fstat(fd)
            if (not stat.S_ISREG(before.st_mode) or before.st_nlink != 1 or
                    before.st_uid != os.geteuid() or before.st_mode & 0o077):
                raise ArtifactError("builder diagnostic log must be a private regular file")
            source_bytes = before.st_size
            offset = max(0, source_bytes - MAX_DIAGNOSTIC_LOG_BYTES)
            remaining = source_bytes - offset
            chunks = []
            while remaining:
                chunk = os.pread(fd, min(65536, remaining), offset)
                if not chunk:
                    raise ArtifactError("builder diagnostic log changed while reading")
                chunks.append(chunk)
                offset += len(chunk)
                remaining -= len(chunk)
            after = os.fstat(fd)
            identity = lambda info: (info.st_dev, info.st_ino, info.st_mode,
                                     info.st_nlink, info.st_uid, info.st_gid,
                                     info.st_size, info.st_mtime_ns, info.st_ctime_ns)
            if identity(before) != identity(after):
                raise ArtifactError("builder diagnostic log changed while reading")
            data = b"".join(chunks)
        finally:
            os.close(fd)
    status = {
        "schema": "heptatrader.ib-builder-diagnostics.v1",
        "candidate_sha": candidate_sha, "phase": phase, "exit_code": exit_code,
        "source_log_bytes": source_bytes, "retained_log_bytes": len(data),
        "log_truncated": source_bytes is not None and source_bytes > len(data),
        "retained_log_sha256": _sha256_bytes(data),
    }
    evidence = {}
    if evidence_root is not None:
        for name in (*BUILDER_EVIDENCE_FILES, "container-lifecycle.json"):
            try:
                evidence[name] = _read_builder_evidence(evidence_root / name)
            except FileNotFoundError:
                pass  # An early phase may not have produced this identity yet.
        if phase == "completed" and exit_code == 0 and not set(BUILDER_EVIDENCE_FILES).issubset(evidence):
            raise ArtifactError("completed build must retain all bound builder evidence")
        if "builder-provenance.json" in evidence:
            builder = validate_builder(_json_load(evidence["builder-provenance.json"], "builder evidence"))
            for name, field in (("resource-policy.json", "resource_policy_sha256"),
                                ("toolchain.txt", "toolchain_sha256")):
                if name not in evidence or _sha256_bytes(evidence[name]) != builder[field]:
                    raise ArtifactError("retained evidence does not match builder provenance")
            policy = _json_load(evidence["resource-policy.json"], "resource policy")
            for name, field in (("image-identity.json", "image_identity_sha256"),
                                ("sdk-provenance.json", "sdk_identity_sha256")):
                if name not in evidence or _sha256_bytes(evidence[name]) != policy.get(field):
                    raise ArtifactError("retained input identity does not match resource policy")
            image = _json_load(evidence["image-identity.json"], "image identity")
            if (image.get("image_id") != builder["image_id"] or
                    image.get("requested_image_reference") != builder["image_reference"] or
                    image.get("engine") != policy.get("engine")):
                raise ArtifactError("retained image identity does not match builder")
        status["evidence_sha256"] = {name: _sha256_bytes(raw) for name, raw in evidence.items()}
    # Do not resolve the final component: even a dangling symlink must be refused.
    output.parent.mkdir(parents=True, exist_ok=True)
    os.mkdir(output, 0o700)
    directory = os.open(output, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC)
    try:
        for name, value in (("candidate-build.log", data),
                            ("build-status.json", _canonical_bytes(status)), *evidence.items()):
            descriptor = os.open(name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                                 0o600, dir_fd=directory)
            with os.fdopen(descriptor, "wb") as stream:
                stream.write(value)
    finally:
        os.close(directory)


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
    diagnostics = subparsers.add_parser("diagnostics")
    diagnostics.add_argument("--build-log", required=True, type=Path)
    diagnostics.add_argument("--candidate-sha", required=True)
    diagnostics.add_argument("--phase", required=True, choices=sorted(BUILD_PHASES))
    diagnostics.add_argument("--exit-code", required=True, type=int)
    diagnostics.add_argument("--output", required=True, type=Path)
    diagnostics.add_argument("--evidence-root", type=Path)
    image = subparsers.add_parser("image-identity")
    image.add_argument("--inspect", required=True, type=Path)
    image.add_argument("--reference", required=True)
    image.add_argument("--engine", required=True, choices=("docker", "podman"))
    image.add_argument("--engine-version", required=True)
    image.add_argument("--engine-sha256", required=True)
    image.add_argument("--output", required=True, type=Path)
    sdk = subparsers.add_parser("sdk-identity")
    sdk.add_argument("--root", required=True, type=Path)
    sdk.add_argument("--dependency-receipt", type=Path)
    sdk.add_argument("--output", required=True, type=Path)
    cid = subparsers.add_parser("container-id")
    cid.add_argument("--cid-file", required=True, type=Path)
    cid.add_argument("--proof", type=Path)
    owned = subparsers.add_parser("container-identity")
    owned.add_argument("--cid-file", required=True, type=Path)
    owned.add_argument("--intent", required=True, type=Path)
    owned.add_argument("--inspect", required=True, type=Path)
    owned.add_argument("--proof", type=Path)
    owned.add_argument("--output", type=Path)
    owned.add_argument("--adopt-missing-cid", action="store_true")
    owned.add_argument("--require-success", action="store_true")
    owned.add_argument("--result-output", type=Path)
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
        elif args.command == "diagnostics":
            capture_builder_diagnostics(args.build_log, args.candidate_sha,
                                        args.phase, args.exit_code, args.output, args.evidence_root)
        elif args.command == "container-id":
            proof_value = _json_load(_read_builder_evidence(args.proof), "container proof") if args.proof else None
            print(container_cid(args.cid_file, proof_value)[0])
        elif args.command == "container-identity":
            intent_value = _json_load(_read_builder_evidence(args.intent), "container intent")
            proof_value = _json_load(_read_builder_evidence(args.proof), "container proof") if args.proof else None
            inspected = json.loads(_read_builder_evidence(args.inspect), object_pairs_hook=_unique_object)
            value = container_identity(inspected, intent_value, args.cid_file, proof_value, args.adopt_missing_cid)
            if args.output:
                _write_private(args.output, _canonical_bytes({k:v for k,v in value.items() if k not in {"running", "completion"}}))
            if args.result_output:
                _write_private(args.result_output, _canonical_bytes({"container_id": value["container_id"], "completion": value["completion"]}))
            if args.require_success and not value["completion"]["success"]:
                raise ArtifactError("owned workload completion was not a clean exit0")
            print("true" if value["running"] else "false")
        elif args.command == "image-identity":
            inspected = json.loads(_read_builder_evidence(args.inspect), object_pairs_hook=_unique_object)
            value = image_identity(inspected, args.reference, args.engine, args.engine_version, args.engine_sha256)
            _write_private(args.output, _canonical_bytes(value))
            print(value["image_id"])
        elif args.command == "sdk-identity":
            _write_private(args.output, _canonical_bytes(sdk_identity(args.root, args.dependency_receipt)))
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
