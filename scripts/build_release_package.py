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
import shutil
import stat
import subprocess
import sys
import tarfile
import tempfile
from typing import Any, Iterable

MANIFEST_SCHEMA = "heptatrader.release-manifest.v1"
RECEIPT_SCHEMA = "heptatrader.release-package-receipt.v1"
ALLOWED_PROFILES = {"core", "ib-paper"}
SHA_RE = re.compile(r"^[0-9a-f]{40}$")
LABEL_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")
PRIVATE_SUFFIXES = {".key", ".pem", ".p12", ".pfx", ".jks"}
MAX_FILE_BYTES = 512 * 1024 * 1024
MAX_TOTAL_BYTES = 2 * 1024 * 1024 * 1024


class PackageError(ValueError):
    pass


@dataclass(frozen=True)
class PayloadFile:
    path: str
    source: Path
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


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


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


def collect_payload(root: Path) -> list[PayloadFile]:
    root = root.resolve(strict=True)
    if not root.is_dir() or root.is_symlink():
        raise PackageError("install root must be a real directory")

    payload: list[PayloadFile] = []
    total = 0
    for directory, names, files in os.walk(root, topdown=True, followlinks=False):
        directory_path = Path(directory)
        for name in list(names):
            child = directory_path / name
            metadata = child.lstat()
            if stat.S_ISLNK(metadata.st_mode):
                raise PackageError(f"symlinked directory is forbidden: {child}")
            if not stat.S_ISDIR(metadata.st_mode):
                raise PackageError(f"special directory entry is forbidden: {child}")
        names.sort()
        files.sort()
        for name in files:
            source = directory_path / name
            metadata = source.lstat()
            relative = canonical_relative(source, root)
            if _looks_private(relative):
                raise PackageError(f"private-key or secret-like path is forbidden: {relative}")
            if not stat.S_ISREG(metadata.st_mode) or source.is_symlink():
                raise PackageError(f"only regular files may be packaged: {relative}")
            if metadata.st_nlink != 1:
                raise PackageError(f"hard-linked file is forbidden: {relative}")
            if metadata.st_mode & (stat.S_ISUID | stat.S_ISGID):
                raise PackageError(f"setuid/setgid file is forbidden: {relative}")
            if metadata.st_size > MAX_FILE_BYTES:
                raise PackageError(f"file exceeds release size bound: {relative}")
            total += metadata.st_size
            if total > MAX_TOTAL_BYTES:
                raise PackageError("release payload exceeds total size bound")
            payload.append(
                PayloadFile(
                    path=relative,
                    source=source,
                    size=metadata.st_size,
                    mode=_file_mode(metadata),
                    sha256=sha256_file(source),
                )
            )
    if not payload:
        raise PackageError("install root is empty")
    return sorted(payload, key=lambda item: item.path)


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
    raw_tar = io.BytesIO()
    with tarfile.open(fileobj=raw_tar, mode="w", format=tarfile.GNU_FORMAT) as archive:
        archive.addfile(
            _tar_info(f"{root_name}/manifest.json", len(manifest_bytes), 0o644, epoch),
            io.BytesIO(manifest_bytes),
        )
        for item in payload:
            with item.source.open("rb") as stream:
                archive.addfile(
                    _tar_info(f"{root_name}/{item.path}", item.size, item.mode, epoch),
                    stream,
                )
    compressed = io.BytesIO()
    with gzip.GzipFile(
        filename="", mode="wb", compresslevel=9, fileobj=compressed, mtime=epoch
    ) as output:
        output.write(raw_tar.getvalue())
    return compressed.getvalue(), sha256_bytes(manifest_bytes)


def _ensure_new_outputs(paths: Iterable[Path]) -> None:
    for path in paths:
        if path.exists() or path.is_symlink():
            raise PackageError(f"refusing to replace existing output: {path}")
        path.parent.mkdir(parents=True, exist_ok=True)


def _write_atomic(path: Path, content: bytes, mode: int) -> None:
    descriptor, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    temporary_path = Path(temporary)
    try:
        os.fchmod(descriptor, mode)
        with os.fdopen(descriptor, "wb") as stream:
            descriptor = -1
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary_path, path)
        directory = os.open(path.parent, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    except Exception:
        if descriptor >= 0:
            os.close(descriptor)
        try:
            temporary_path.unlink()
        except OSError:
            pass
        raise


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
    output = output.resolve()
    if output.suffix not in {".gz", ".tgz"}:
        raise PackageError("release output must end in .tar.gz or .tgz")
    digest_path = Path(str(output) + ".sha256")
    receipt_path = Path(str(output) + ".receipt.json")
    _ensure_new_outputs((output, digest_path, receipt_path))

    payload = collect_payload(install_root)
    manifest = build_manifest(payload, version, profile, source_sha, source_date_epoch)
    archive, manifest_sha256 = archive_bytes(
        payload, manifest, version, profile, source_date_epoch
    )
    package_sha256 = sha256_bytes(archive)
    receipt = {
        "schema": RECEIPT_SCHEMA,
        "version": version,
        "profile": profile,
        "source_sha": source_sha,
        "source_date_epoch": source_date_epoch,
        "package_file": output.name,
        "package_sha256": package_sha256,
        "manifest_sha256": manifest_sha256,
        "file_count": len(payload),
        "authorization_effect": "NONE",
        "paper_authorized": False,
        "live_authorized": False,
    }

    written: list[Path] = []
    try:
        _write_atomic(output, archive, 0o644)
        written.append(output)
        _write_atomic(
            digest_path,
            f"{package_sha256}  {output.name}\n".encode("ascii"),
            0o644,
        )
        written.append(digest_path)
        _write_atomic(receipt_path, canonical_json(receipt), 0o600)
        written.append(receipt_path)
    except Exception:
        for path in reversed(written):
            try:
                path.unlink()
            except OSError:
                pass
        raise
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
