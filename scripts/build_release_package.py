#!/usr/bin/env python3
"""Build a deterministic, content-addressed HeptaTrader release archive.

The builder packages an already installed tree. It never reads Broker secrets,
never changes runtime authorization, and always records PAPER/LIVE as false.
"""
from __future__ import annotations

import argparse
from dataclasses import dataclass
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


def _hash_stream(stream: BinaryIO) -> str:
    stream.seek(0)
    digest = hashlib.sha256()
    for chunk in iter(lambda: stream.read(1024 * 1024), b""):
        digest.update(chunk)
    return digest.hexdigest()


def sha256_file(path: Path) -> str:
    with path.open("rb") as stream:
        return _hash_stream(stream)


def _snapshot_source(
    source: Path, observed: os.stat_result, relative: str
) -> tuple[BinaryIO, os.stat_result, str]:
    directory_flags = (
        os.O_RDONLY
        | getattr(os, "O_DIRECTORY", 0)
        | getattr(os, "O_CLOEXEC", 0)
        | getattr(os, "O_NOFOLLOW", 0)
    )
    file_flags = (
        os.O_RDONLY
        | getattr(os, "O_CLOEXEC", 0)
        | getattr(os, "O_NOFOLLOW", 0)
    )
    directory = os.open(source.parent, directory_flags)
    descriptor = -1
    snapshot: BinaryIO | None = None
    try:
        descriptor = os.open(source.name, file_flags, dir_fd=directory)
        pinned = os.fstat(descriptor)
        if not _same_file(observed, pinned):
            raise PackageError(f"file identity changed before snapshot: {relative}")
        if not stat.S_ISREG(pinned.st_mode) or pinned.st_nlink != 1:
            raise PackageError(f"payload is not a regular single-link file: {relative}")
        if pinned.st_mode & (stat.S_ISUID | stat.S_ISGID):
            raise PackageError(f"setuid/setgid file is forbidden: {relative}")
        if pinned.st_size > MAX_FILE_BYTES:
            raise PackageError(f"file exceeds release size bound: {relative}")

        snapshot = tempfile.TemporaryFile(mode="w+b")
        digest = hashlib.sha256()
        total = 0
        with os.fdopen(descriptor, "rb", closefd=True) as source_stream:
            descriptor = -1
            while True:
                chunk = source_stream.read(1024 * 1024)
                if not chunk:
                    break
                total += len(chunk)
                if total > MAX_FILE_BYTES:
                    raise PackageError(f"file exceeds release size bound: {relative}")
                digest.update(chunk)
                snapshot.write(chunk)
            after = os.fstat(source_stream.fileno())
        if not _same_file(pinned, after) or total != pinned.st_size:
            raise PackageError(f"file changed while being snapshotted: {relative}")
        snapshot.flush()
        snapshot.seek(0)
        return snapshot, pinned, digest.hexdigest()
    except Exception:
        if snapshot is not None:
            snapshot.close()
        raise
    finally:
        if descriptor >= 0:
            os.close(descriptor)
        os.close(directory)


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


def canonical_relative(path: Path, root: Path) -> str:
    try:
        relative = path.relative_to(root)
    except ValueError as error:
        raise PackageError(f"path escaped install root: {path}") from error
    value = relative.as_posix()
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
    try:
        encoded = name.encode("utf-8", "strict")
    except UnicodeError as error:
        raise PackageError(f"archive path is not UTF-8: {name!r}") from error
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
    root = root.resolve(strict=True)
    if not root.is_dir() or root.is_symlink():
        raise PackageError("install root must be a real directory")

    payload: list[PayloadFile] = []
    admitted_paths: set[str] = set()
    total = 0
    try:
        for directory, names, files in os.walk(root, topdown=True, followlinks=False):
            directory_path = Path(directory)
            for name in list(names):
                child = directory_path / name
                metadata = child.lstat()
                if stat.S_ISLNK(metadata.st_mode):
                    raise PackageError(f"symlinked directory is forbidden: {child}")
                if not stat.S_ISDIR(metadata.st_mode):
                    raise PackageError(f"special directory entry is forbidden: {child}")
                relative_directory = canonical_relative(child, root)
                _reject_generated_namespace_collision(relative_directory)
            names.sort()
            files.sort()
            for name in files:
                source = directory_path / name
                observed = source.lstat()
                relative = canonical_relative(source, root)
                _admit_payload_path(relative, admitted_paths)
                if _looks_private(relative):
                    raise PackageError(
                        f"private-key or secret-like path is forbidden: {relative}"
                    )
                if not stat.S_ISREG(observed.st_mode) or stat.S_ISLNK(observed.st_mode):
                    raise PackageError(f"only regular files may be packaged: {relative}")
                if observed.st_nlink != 1:
                    raise PackageError(f"hard-linked file is forbidden: {relative}")
                snapshot, pinned, digest = _snapshot_source(
                    source, observed, relative
                )
                total += pinned.st_size
                if total > MAX_TOTAL_BYTES:
                    snapshot.close()
                    raise PackageError("release payload exceeds total size bound")
                payload.append(
                    PayloadFile(
                        path=relative,
                        snapshot=snapshot,
                        size=pinned.st_size,
                        mode=_file_mode(pinned),
                        sha256=digest,
                    )
                )
        if not payload:
            raise PackageError("install root is empty")
        return sorted(payload, key=lambda item: item.path)
    except Exception:
        close_payload(payload)
        raise


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


def _open_output_directory(path: Path) -> int:
    path.parent.mkdir(parents=True, exist_ok=True)
    flags = (
        os.O_RDONLY
        | getattr(os, "O_DIRECTORY", 0)
        | getattr(os, "O_CLOEXEC", 0)
        | getattr(os, "O_NOFOLLOW", 0)
    )
    directory = os.open(path.parent, flags)
    if not stat.S_ISDIR(os.fstat(directory).st_mode):
        os.close(directory)
        raise PackageError(f"output parent is not a directory: {path.parent}")
    return directory


def _publish_noreplace(directory: int, temporary_name: str, final_name: str) -> None:
    os.link(
        temporary_name,
        final_name,
        src_dir_fd=directory,
        dst_dir_fd=directory,
        follow_symlinks=False,
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
    directory = _open_output_directory(path)
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
                    mode,
                    dir_fd=directory,
                )
                temporary_name = candidate
                break
            except FileExistsError:
                continue
        if descriptor < 0:
            raise PackageError("could not allocate a private output staging file")
        os.fchmod(descriptor, mode)
        with os.fdopen(descriptor, "wb", closefd=True) as stream:
            descriptor = -1
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        try:
            _publish_noreplace(directory, temporary_name, path.name)
        except FileExistsError as error:
            raise PackageError(
                f"refusing to replace concurrently created output: {path}"
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
