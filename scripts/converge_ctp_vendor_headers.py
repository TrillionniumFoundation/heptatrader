#!/usr/bin/env python3
"""Converge byte-identical CTP headers behind platform compatibility paths."""

from __future__ import annotations

import argparse
import hashlib
import os
from pathlib import Path
import secrets
import stat


HEADER_NAMES = (
    "ThostFtdcMdApi.h",
    "ThostFtdcTraderApi.h",
    "ThostFtdcUserApiDataType.h",
    "ThostFtdcUserApiStruct.h",
)
PLATFORM_DIRECTORIES = (
    "Interface/CTPTradeApi32",
    "Interface/CTPTradeApi64",
    "Interface/CTPTradeApiLinux",
)
CANONICAL_DIRECTORY = "third_party/ctp/6.7.7/include"


class ConvergenceError(RuntimeError):
    pass


def forwarder(name: str) -> bytes:
    return (
        "#pragma once\n"
        f"#include \"../../third_party/ctp/6.7.7/include/{name}\"\n"
    ).encode("ascii")


def _identity(value: os.stat_result) -> tuple[int, ...]:
    return (
        value.st_dev, value.st_ino, value.st_mode, value.st_uid,
        value.st_gid, value.st_nlink, value.st_size, value.st_mtime_ns,
        value.st_ctime_ns,
    )


def safe_regular_asset(value: os.stat_result) -> bool:
    """Accept restrictive checkout umasks without accepting mutable aliases."""
    mode = stat.S_IMODE(value.st_mode)
    forbidden = 0o7000 | 0o0111 | 0o0022
    return (
        stat.S_ISREG(value.st_mode) and value.st_nlink == 1 and
        bool(mode & stat.S_IRUSR) and not bool(mode & forbidden)
    )


def _relative_parts(relative: str) -> tuple[str, ...]:
    path = Path(relative)
    if (not relative or "\0" in relative or "\\" in relative or
            path.is_absolute() or path.as_posix() != relative or
            any(part in {"", ".", ".."} for part in path.parts)):
        raise ConvergenceError(f"vendor path is not canonical: {relative!r}")
    return path.parts


def _open_parent(
        root: Path, relative: str) -> tuple[list[int], int, str]:
    parts = _relative_parts(relative)
    root_path = Path(os.path.abspath(root))
    before = root_path.lstat()
    if stat.S_ISLNK(before.st_mode) or not stat.S_ISDIR(before.st_mode):
        raise ConvergenceError("vendor root is unsafe")
    directory_flags = (
        os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) |
        getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    )
    root_descriptor = os.open(root_path, directory_flags)
    descriptors = [root_descriptor]
    try:
        if _identity(before) != _identity(os.fstat(root_descriptor)):
            raise ConvergenceError("vendor root changed while opening")
        parent_descriptor = root_descriptor
        for component in parts[:-1]:
            try:
                metadata = os.stat(
                    component, dir_fd=parent_descriptor,
                    follow_symlinks=False)
            except OSError as error:
                raise ConvergenceError(
                    f"vendor path component is missing or unsafe: "
                    f"{relative}") from error
            if (stat.S_ISLNK(metadata.st_mode) or
                    not stat.S_ISDIR(metadata.st_mode)):
                raise ConvergenceError(
                    f"vendor path component is unsafe: {relative}")
            try:
                child_descriptor = os.open(
                    component, directory_flags, dir_fd=parent_descriptor)
            except OSError as error:
                raise ConvergenceError(
                    f"vendor path component is missing or unsafe: "
                    f"{relative}") from error
            descriptors.append(child_descriptor)
            if _identity(metadata) != _identity(os.fstat(child_descriptor)):
                raise ConvergenceError(
                    f"vendor path component changed while opening: {relative}")
            parent_descriptor = child_descriptor
        return descriptors, parent_descriptor, parts[-1]
    except BaseException:
        for descriptor in reversed(descriptors):
            os.close(descriptor)
        raise


def stable_relative_asset(
        root: Path, relative: str) -> tuple[os.stat_result, bytes]:
    descriptors, parent_descriptor, leaf = _open_parent(root, relative)
    descriptor = -1
    try:
        before = os.stat(
            leaf, dir_fd=parent_descriptor, follow_symlinks=False)
        if not safe_regular_asset(before):
            raise ConvergenceError(
                f"vendor asset is unsafe: {relative}")
        flags = (
            os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) |
            getattr(os, "O_NOFOLLOW", 0)
        )
        descriptor = os.open(
            leaf, flags, dir_fd=parent_descriptor)
        opened = os.fstat(descriptor)
        chunks = []
        while True:
            chunk = os.read(descriptor, 1024 * 1024)
            if not chunk:
                break
            chunks.append(chunk)
        after = os.stat(
            leaf, dir_fd=parent_descriptor, follow_symlinks=False)
        if (not safe_regular_asset(opened) or
                not safe_regular_asset(after) or
                _identity(before) != _identity(opened) or
                _identity(opened) != _identity(after)):
            raise ConvergenceError(
                f"vendor header changed during read: {relative}")
        return opened, b"".join(chunks)
    finally:
        if descriptor >= 0:
            os.close(descriptor)
        for parent in reversed(descriptors):
            os.close(parent)


def stable_relative_bytes(root: Path, relative: str) -> bytes:
    return stable_relative_asset(root, relative)[1]


def stable_relative_directory(
        root: Path, relative: str) -> dict[str, os.stat_result]:
    descriptors, parent_descriptor, leaf = _open_parent(root, relative)
    directory_descriptor = -1
    try:
        before = os.stat(
            leaf, dir_fd=parent_descriptor, follow_symlinks=False)
        if stat.S_ISLNK(before.st_mode) or not stat.S_ISDIR(before.st_mode):
            raise ConvergenceError(
                f"vendor directory is unsafe: {relative}")
        flags = (
            os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) |
            getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
        )
        directory_descriptor = os.open(
            leaf, flags, dir_fd=parent_descriptor)
        opened = os.fstat(directory_descriptor)
        if _identity(before) != _identity(opened):
            raise ConvergenceError(
                f"vendor directory changed while opening: {relative}")
        result = {
            name: os.stat(
                name, dir_fd=directory_descriptor, follow_symlinks=False)
            for name in os.listdir(directory_descriptor)
        }
        after = os.stat(
            leaf, dir_fd=parent_descriptor, follow_symlinks=False)
        if _identity(opened) != _identity(after):
            raise ConvergenceError(
                f"vendor directory changed during listing: {relative}")
        return result
    finally:
        if directory_descriptor >= 0:
            os.close(directory_descriptor)
        for parent in reversed(descriptors):
            os.close(parent)


def atomic_write_relative(
        root: Path, relative: str, payload: bytes,
        expected_current: bytes) -> None:
    if stable_relative_bytes(root, relative) != expected_current:
        raise ConvergenceError(
            f"vendor header changed before apply: {relative}")
    descriptors, parent_descriptor, leaf = _open_parent(root, relative)
    temporary_name = ""
    descriptor = -1
    try:
        flags = (
            os.O_WRONLY | os.O_CREAT | os.O_EXCL |
            getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
        )
        for _ in range(32):
            temporary_name = (
                f".{leaf}.{os.getpid()}.{secrets.token_hex(8)}.tmp")
            try:
                descriptor = os.open(
                    temporary_name, flags, 0o644,
                    dir_fd=parent_descriptor)
                break
            except FileExistsError:
                temporary_name = ""
        else:
            raise ConvergenceError(
                f"could not allocate vendor temporary file: {relative}")
        offset = 0
        while offset < len(payload):
            offset += os.write(descriptor, payload[offset:])
        os.fchmod(descriptor, 0o644)
        os.fsync(descriptor)
        os.close(descriptor)
        descriptor = -1
        os.replace(
            temporary_name, leaf,
            src_dir_fd=parent_descriptor, dst_dir_fd=parent_descriptor)
        temporary_name = ""
        os.fsync(parent_descriptor)
    finally:
        if descriptor >= 0:
            os.close(descriptor)
        if temporary_name:
            try:
                os.unlink(temporary_name, dir_fd=parent_descriptor)
            except FileNotFoundError:
                pass
        for parent in reversed(descriptors):
            os.close(parent)


def verify_forwarders(root: Path) -> int:
    count = 0
    for directory in PLATFORM_DIRECTORIES:
        for name in HEADER_NAMES:
            relative = f"{directory}/{name}"
            if stable_relative_bytes(root, relative) != forwarder(name):
                raise ConvergenceError(
                    f"compatibility forwarder drift: {relative}")
            count += 1
    return count


def converge(root: Path, apply: bool) -> dict[str, str]:
    digests: dict[str, str] = {}
    replacements: list[tuple[str, bytes, bytes]] = []
    for name in HEADER_NAMES:
        canonical_relative = f"{CANONICAL_DIRECTORY}/{name}"
        payload = stable_relative_bytes(root, canonical_relative)
        digest = hashlib.sha256(payload).hexdigest()
        digests[name] = digest
        for directory in PLATFORM_DIRECTORIES:
            source_relative = f"{directory}/{name}"
            expected = forwarder(name)
            source_payload = stable_relative_bytes(root, source_relative)
            if source_payload == expected:
                continue
            if not apply:
                raise ConvergenceError(
                    f"compatibility forwarder drift: "
                    f"{source_relative}")
            if hashlib.sha256(source_payload).hexdigest() != digest:
                raise ConvergenceError(
                    f"legacy header differs from reviewed canonical: "
                    f"{source_relative}")
            replacements.append(
                (source_relative, source_payload, expected))
    # Applying is deliberately a second phase: no compatibility path is
    # modified until every canonical and legacy payload has passed preflight.
    for relative, current, expected in replacements:
        atomic_write_relative(root, relative, expected, current)
    return digests


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--root", type=Path,
        default=Path(__file__).resolve().parents[1])
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--check", action="store_true")
    parser.add_argument(
        "--check-forwarders-only", action="store_true",
        help=(
            "validate redistributable compatibility forwarders without "
            "requiring the separately controlled vendor overlay"))
    args = parser.parse_args()
    if sum((args.apply, args.check, args.check_forwarders_only)) != 1:
        raise SystemExit(
            "exactly one of --apply, --check, or "
            "--check-forwarders-only is required")
    root = Path(os.path.abspath(args.root))
    if args.check_forwarders_only:
        count = verify_forwarders(root)
        print(
            f"PASS: {count} CTP compatibility forwarders; "
            "external canonical overlay not inspected")
        return 0
    digests = converge(root, args.apply)
    if args.check:
        pass
    print(
        f"PASS: {len(HEADER_NAMES)} canonical headers, "
        f"{len(HEADER_NAMES) * len(PLATFORM_DIRECTORIES)} compatibility paths")
    for name, digest in sorted(digests.items()):
        print(f"{digest}  {CANONICAL_DIRECTORY}/{name}")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (ConvergenceError, OSError) as error:
        print(f"ctp-header-convergence: {error}", file=os.sys.stderr)
        raise SystemExit(78)
