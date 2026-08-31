#!/usr/bin/env python3
"""Build a deterministic, permission-safe tar.gz from an install tree."""

from __future__ import annotations

import argparse
import gzip
import os
from pathlib import Path, PurePosixPath
import stat
import sys
import tarfile
import tempfile

MAX_GZIP_EPOCH = 0xFFFFFFFF


def parse_epoch(value: str | None) -> int:
    raw = value if value is not None else os.environ.get("SOURCE_DATE_EPOCH", "0")
    if not raw or not raw.isascii() or not raw.isdigit():
        raise ValueError("SOURCE_DATE_EPOCH must be a non-negative decimal integer")
    epoch = int(raw, 10)
    if epoch > MAX_GZIP_EPOCH:
        raise ValueError("SOURCE_DATE_EPOCH exceeds the deterministic gzip range")
    return epoch


def canonical_prefix(value: str) -> str:
    if not value or value.startswith("/") or "\\" in value:
        raise ValueError("archive prefix must be a non-empty relative POSIX path")
    parsed = PurePosixPath(value)
    if any(part in ("", ".", "..") for part in parsed.parts):
        raise ValueError("archive prefix cannot contain empty or dot components")
    canonical = parsed.as_posix()
    if canonical != value.rstrip("/"):
        raise ValueError("archive prefix must be canonical")
    return canonical


def validate_root(root: Path) -> os.stat_result:
    try:
        metadata = root.lstat()
    except FileNotFoundError as error:
        raise ValueError(f"install root does not exist: {root}") from error
    if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISDIR(metadata.st_mode):
        raise ValueError("install root must be a non-symlink directory")
    if stat.S_IMODE(metadata.st_mode) & 0o022:
        raise ValueError(
            f"install root is group/world writable: {stat.S_IMODE(metadata.st_mode):04o}"
        )
    return metadata


def scan(root: Path) -> list[tuple[str, Path, os.stat_result]]:
    entries: list[tuple[str, Path, os.stat_result]] = []
    for path in sorted(
        root.rglob("*"), key=lambda item: item.relative_to(root).as_posix()
    ):
        metadata = path.lstat()
        relative = path.relative_to(root).as_posix()
        mode = stat.S_IMODE(metadata.st_mode)
        if stat.S_ISLNK(metadata.st_mode):
            raise ValueError(f"symlink is forbidden in archive input: {relative}")
        if stat.S_ISDIR(metadata.st_mode):
            if mode & 0o022:
                raise ValueError(
                    f"archive input directory is replaceable: {relative} mode={mode:04o}"
                )
        elif stat.S_ISREG(metadata.st_mode):
            if mode & 0o022:
                raise ValueError(
                    f"archive input file is group/world writable: {relative} mode={mode:04o}"
                )
        else:
            raise ValueError(f"special file is forbidden in archive input: {relative}")
        entries.append((relative, path, metadata))
    return entries


def normalized_info(name: str, metadata: os.stat_result, epoch: int) -> tarfile.TarInfo:
    info = tarfile.TarInfo(name)
    info.uid = 0
    info.gid = 0
    info.uname = "root"
    info.gname = "root"
    info.mtime = epoch
    info.mode = stat.S_IMODE(metadata.st_mode)
    info.pax_headers = {}
    if stat.S_ISDIR(metadata.st_mode):
        info.type = tarfile.DIRTYPE
        info.size = 0
    elif stat.S_ISREG(metadata.st_mode):
        info.type = tarfile.REGTYPE
        info.size = metadata.st_size
    else:
        raise ValueError(f"unsupported archive entry type: {name}")
    return info


def build_archive(root: Path, output: Path, prefix: str, epoch: int) -> None:
    root_metadata = validate_root(root)
    entries = scan(root)
    output.parent.mkdir(parents=True, exist_ok=True)
    output_absolute = Path(os.path.abspath(os.fspath(output)))
    try:
        output_absolute.relative_to(root)
    except ValueError:
        pass
    else:
        raise ValueError("archive output must not be inside the install tree")

    descriptor, temporary_name = tempfile.mkstemp(
        prefix=output.name + ".", suffix=".tmp", dir=output.parent
    )
    os.close(descriptor)
    temporary = Path(temporary_name)
    try:
        with temporary.open("wb") as raw:
            with gzip.GzipFile(
                filename="",
                mode="wb",
                compresslevel=9,
                fileobj=raw,
                mtime=epoch,
            ) as compressed:
                with tarfile.open(
                    fileobj=compressed,
                    mode="w",
                    format=tarfile.GNU_FORMAT,
                ) as archive:
                    archive.addfile(normalized_info(prefix, root_metadata, epoch))
                    for relative, path, metadata in entries:
                        name = (PurePosixPath(prefix) / relative).as_posix()
                        info = normalized_info(name, metadata, epoch)
                        if stat.S_ISREG(metadata.st_mode):
                            with path.open("rb") as source:
                                archive.addfile(info, source)
                        else:
                            archive.addfile(info)
            raw.flush()
            os.fsync(raw.fileno())
        os.chmod(temporary, 0o644)
        os.replace(temporary, output)
        directory_fd = os.open(output.parent, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
    finally:
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--prefix", default="usr")
    parser.add_argument("--source-date-epoch")
    args = parser.parse_args()

    root = Path(os.path.abspath(os.fspath(args.root)))
    try:
        prefix = canonical_prefix(args.prefix)
        epoch = parse_epoch(args.source_date_epoch)
        build_archive(root, args.output, prefix, epoch)
    except (OSError, ValueError, tarfile.TarError) as error:
        print(f"ERROR: unable to build deterministic release archive: {error}", file=sys.stderr)
        return 1
    print(f"deterministic archive PASS: {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
