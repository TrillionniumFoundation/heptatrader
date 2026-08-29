from __future__ import annotations

import argparse
from datetime import datetime, timezone
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import secrets
import shlex
import stat
import sys
from typing import Any

from .registry import RegistryError, load_registry
from .sandbox import SandboxError, apply_no_network_filter, close_inherited_descriptors


PYTHON_TARGET = re.compile(
    r"(?:scripts/|\$[A-Z_]+/scripts/)([A-Za-z0-9_.-]+\.py)")
PROJECT_ID = "heptatrader-agent-os"
INVENTORY_RELEASE_VERSION = re.compile(
    r"^[0-9A-Za-z][0-9A-Za-z.+-]{0,126}$")
MAX_SOURCE_BASELINE_BYTES = 64 * 1024 * 1024


def canonical_json(payload: Any) -> bytes:
    return (
        json.dumps(payload, ensure_ascii=True, separators=(",", ":"),
                   sort_keys=True) + "\n"
    ).encode("utf-8")


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        while True:
            chunk = source.read(1024 * 1024)
            if not chunk:
                return digest.hexdigest()
            digest.update(chunk)


def _identity(value: os.stat_result) -> tuple[int, ...]:
    return (
        value.st_dev,
        value.st_ino,
        value.st_mode,
        value.st_nlink,
        value.st_uid,
        value.st_gid,
        value.st_size,
        value.st_mtime_ns,
        value.st_ctime_ns,
    )


def _directory_identity(value: os.stat_result) -> tuple[int, ...]:
    return (
        value.st_dev,
        value.st_ino,
        value.st_mode,
        value.st_uid,
        value.st_gid,
    )


def _anchored_regular_bytes(
        path: Path, label: str, limit: int) -> tuple[os.stat_result, bytes]:
    absolute = Path(os.path.abspath(path))
    if not absolute.is_absolute() or len(absolute.parts) < 2:
        raise RegistryError(f"{label} path is invalid")
    directory_flags = (
        os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) |
        getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    )
    file_flags = (
        os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) |
        getattr(os, "O_NOFOLLOW", 0)
    )
    descriptors: list[int] = []
    components: list[tuple[str, tuple[int, ...]]] = []
    file_descriptor = -1
    try:
        parent_descriptor = os.open("/", directory_flags)
        descriptors.append(parent_descriptor)
        for component in absolute.parts[1:-1]:
            before = os.stat(
                component, dir_fd=parent_descriptor,
                follow_symlinks=False)
            if stat.S_ISLNK(before.st_mode) or not stat.S_ISDIR(before.st_mode):
                raise RegistryError(f"{label} contains a symlink component")
            child = os.open(
                component, directory_flags, dir_fd=parent_descriptor)
            if (_directory_identity(before) !=
                    _directory_identity(os.fstat(child))):
                os.close(child)
                raise RegistryError(
                    f"{label} ancestor changed while opening")
            components.append(
                (component, _directory_identity(before)))
            descriptors.append(child)
            parent_descriptor = child

        before = os.stat(
            absolute.name, dir_fd=parent_descriptor,
            follow_symlinks=False)
        if (stat.S_ISLNK(before.st_mode) or
                not stat.S_ISREG(before.st_mode) or
                before.st_nlink != 1):
            raise RegistryError(
                f"{label} must be a regular non-symlink file")
        if before.st_mode & 0o022:
            raise RegistryError(
                f"{label} must not be group/world writable")
        if before.st_size > limit:
            raise RegistryError(f"{label} exceeds the size limit")
        file_descriptor = os.open(
            absolute.name, file_flags, dir_fd=parent_descriptor)
        opened = os.fstat(file_descriptor)
        if _identity(before) != _identity(opened):
            raise RegistryError(f"{label} changed while opening")
        chunks: list[bytes] = []
        remaining = limit + 1
        while remaining:
            chunk = os.read(
                file_descriptor, min(1024 * 1024, remaining))
            if not chunk:
                break
            chunks.append(chunk)
            remaining -= len(chunk)
        after_descriptor = os.fstat(file_descriptor)
        after_path = os.stat(
            absolute.name, dir_fd=parent_descriptor,
            follow_symlinks=False)
        if (_identity(opened) != _identity(after_descriptor) or
                _identity(after_descriptor) != _identity(after_path)):
            raise RegistryError(f"{label} changed during read")
        payload = b"".join(chunks)
        if len(payload) != opened.st_size:
            raise RegistryError(f"{label} exceeds the size limit")
        for index, (component, expected) in enumerate(components):
            current = os.stat(
                component, dir_fd=descriptors[index],
                follow_symlinks=False)
            if _directory_identity(current) != expected:
                raise RegistryError(f"{label} ancestor changed during read")
        return opened, payload
    except RegistryError:
        raise
    except OSError as error:
        raise RegistryError(f"{label} path is unstable or unsafe") from error
    finally:
        if file_descriptor >= 0:
            os.close(file_descriptor)
        for descriptor in reversed(descriptors):
            os.close(descriptor)


def _safe_regular(path: Path, label: str) -> os.stat_result:
    try:
        before = path.lstat()
    except OSError as error:
        raise RegistryError(f"{label} is unavailable: {path}") from error
    if (stat.S_ISLNK(before.st_mode) or not stat.S_ISREG(before.st_mode) or
            before.st_nlink != 1):
        raise RegistryError(f"{label} must be a regular non-symlink file")
    if before.st_mode & 0o022:
        raise RegistryError(f"{label} must not be group/world writable")
    return before


def _reject_symlink_ancestor(path: Path, label: str) -> None:
    probe = path
    while not probe.exists() and not probe.is_symlink():
        if probe == probe.parent:
            break
        probe = probe.parent
    if (probe.is_symlink() or
            Path(os.path.abspath(probe)) != probe.resolve(strict=True)):
        raise RegistryError(f"{label} contains a symlink component")


def _atomic_private_write(path: Path, payload: bytes) -> None:
    absolute = Path(os.path.abspath(path))
    if (path != absolute or not absolute.name or
            absolute.name in {".", ".."}):
        raise RegistryError("output path must be canonical and absolute")
    directory_flags = (
        os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) |
        getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    )
    directory_identity = lambda value: (
        value.st_dev, value.st_ino, value.st_mode, value.st_uid, value.st_gid,
    )
    publication_identity = lambda value: (
        value.st_dev, value.st_ino, value.st_mode, value.st_nlink,
        value.st_uid, value.st_gid, value.st_size, value.st_mtime_ns,
    )
    descriptors: list[int] = []
    components: list[tuple[str, tuple[int, ...]]] = []
    parent_descriptor = os.open("/", directory_flags)
    descriptors.append(parent_descriptor)
    temporary_name = (
        f".{absolute.name}.{os.getpid()}."
        f"{secrets.token_hex(8)}.tmp")
    try:
        for component in absolute.parent.parts[1:]:
            try:
                before_component = os.stat(
                    component, dir_fd=parent_descriptor,
                    follow_symlinks=False)
            except FileNotFoundError:
                os.mkdir(component, 0o700, dir_fd=parent_descriptor)
                before_component = os.stat(
                    component, dir_fd=parent_descriptor,
                    follow_symlinks=False)
            if (stat.S_ISLNK(before_component.st_mode) or
                    not stat.S_ISDIR(before_component.st_mode)):
                raise RegistryError("output contains an unsafe parent")
            child = os.open(
                component, directory_flags, dir_fd=parent_descriptor)
            if (directory_identity(before_component) !=
                    directory_identity(os.fstat(child))):
                os.close(child)
                raise RegistryError("output parent changed while opening")
            components.append(
                (component, directory_identity(before_component)))
            descriptors.append(child)
            parent_descriptor = child
        parent_metadata = os.fstat(parent_descriptor)
        if (parent_metadata.st_uid != os.geteuid() or
                parent_metadata.st_mode & 0o022):
            raise RegistryError(
                "output parent must be caller-owned and protected")
        try:
            existing = os.stat(
                absolute.name, dir_fd=parent_descriptor,
                follow_symlinks=False)
        except FileNotFoundError:
            existing = None
        if existing is not None and (
                not stat.S_ISREG(existing.st_mode) or
                existing.st_nlink != 1 or
                existing.st_uid != os.geteuid() or
                existing.st_mode & 0o022):
            raise RegistryError("output destination is unsafe")
        flags = (
            os.O_WRONLY | os.O_CREAT | os.O_EXCL |
            getattr(os, "O_CLOEXEC", 0) |
            getattr(os, "O_NOFOLLOW", 0)
        )
        output_descriptor = os.open(
            temporary_name, flags, 0o600, dir_fd=parent_descriptor)
        try:
            os.fchmod(output_descriptor, 0o600)
            offset = 0
            while offset < len(payload):
                offset += os.write(output_descriptor, payload[offset:])
            os.fsync(output_descriptor)
            written = os.fstat(output_descriptor)
        finally:
            os.close(output_descriptor)
        os.replace(
            temporary_name, absolute.name,
            src_dir_fd=parent_descriptor,
            dst_dir_fd=parent_descriptor)
        temporary_name = ""
        published = os.stat(
            absolute.name, dir_fd=parent_descriptor,
            follow_symlinks=False)
        if (publication_identity(written) !=
                publication_identity(published) or
                not stat.S_ISREG(published.st_mode) or
                published.st_nlink != 1 or
                published.st_uid != os.geteuid() or
                stat.S_IMODE(published.st_mode) != 0o600):
            raise RegistryError("output identity drift after publication")
        os.fsync(parent_descriptor)
        for index, (component, expected) in enumerate(components):
            current = os.stat(
                component, dir_fd=descriptors[index],
                follow_symlinks=False)
            if directory_identity(current) != expected:
                raise RegistryError("output parent changed during publication")
    except BaseException:
        if temporary_name:
            try:
                os.unlink(temporary_name, dir_fd=parent_descriptor)
            except FileNotFoundError:
                pass
        raise
    finally:
        for descriptor in reversed(descriptors):
            os.close(descriptor)


def _source_baseline_binding(
        root: Path, source_baseline: Path,
        source_baseline_artifact_root: Path) -> dict[str, Any]:
    canonical_root = Path(os.path.abspath(root))
    if (not canonical_root.is_dir() or
            canonical_root != canonical_root.resolve(strict=True)):
        raise RegistryError("repository root is unsafe")
    root_identity = _directory_identity(canonical_root.lstat())
    if source_baseline.is_absolute():
        canonical_path = Path(os.path.abspath(source_baseline))
    else:
        if (not source_baseline.parts or
                ".." in source_baseline.parts or
                "." in source_baseline.parts or
                source_baseline.as_posix() != str(source_baseline)):
            raise RegistryError(
                "source baseline must be a normalized repository path")
        canonical_path = canonical_root / source_baseline
    try:
        canonical_path.relative_to(canonical_root)
    except ValueError as error:
        raise RegistryError(
            "source baseline escapes the repository root") from error
    if source_baseline_artifact_root.is_absolute():
        artifact_root = Path(
            os.path.abspath(source_baseline_artifact_root))
    else:
        if (not source_baseline_artifact_root.parts or
                ".." in source_baseline_artifact_root.parts or
                "." in source_baseline_artifact_root.parts or
                source_baseline_artifact_root.as_posix() !=
                str(source_baseline_artifact_root)):
            raise RegistryError(
                "source baseline artifact root must be a normalized "
                "repository path")
        artifact_root = canonical_root / source_baseline_artifact_root
    try:
        artifact_root.relative_to(canonical_root)
    except ValueError as error:
        raise RegistryError(
            "source baseline artifact root escapes the repository") from error
    if (not artifact_root.is_dir() or
            artifact_root != artifact_root.resolve(strict=True)):
        raise RegistryError("source baseline artifact root is unsafe")
    artifact_root_metadata = artifact_root.lstat()
    if (artifact_root_metadata.st_uid != os.geteuid() or
            artifact_root_metadata.st_mode & 0o022):
        raise RegistryError(
            "source baseline artifact root must be caller-owned and protected")
    artifact_root_identity = _directory_identity(artifact_root_metadata)
    try:
        logical_path = canonical_path.relative_to(artifact_root).as_posix()
    except ValueError as error:
        raise RegistryError(
            "source baseline is outside its declared artifact root") from error
    if not logical_path or logical_path == ".":
        raise RegistryError("source baseline artifact path is invalid")
    metadata, payload = _anchored_regular_bytes(
        canonical_path, "source baseline", MAX_SOURCE_BASELINE_BYTES)
    if _directory_identity(canonical_root.lstat()) != root_identity:
        raise RegistryError(
            "repository root changed during source baseline binding")
    if (_directory_identity(artifact_root.lstat()) !=
            artifact_root_identity):
        raise RegistryError(
            "source baseline artifact root changed during binding")
    if metadata.st_mode & 0o7000:
        raise RegistryError("source baseline has unsafe special mode bits")
    return {
        "path": logical_path,
        "sha256": hashlib.sha256(payload).hexdigest(),
        "size": len(payload),
        "mode": format(stat.S_IMODE(metadata.st_mode), "04o"),
    }


def wrapper_inventory(
        root: Path, registry: dict[str, Any], *,
        round_number: int | None = None,
        release_version: str | None = None,
        source_baseline: Path | None = None,
        source_baseline_artifact_root: Path | None = None) -> dict[str, Any]:
    identity_values = (
        round_number, release_version, source_baseline,
        source_baseline_artifact_root)
    if any(value is not None for value in identity_values):
        if any(value is None for value in identity_values):
            raise RegistryError(
                "inventory v2 requires round, release version, and "
                "source baseline plus its artifact root together")
        if type(round_number) is not int or round_number <= 0:
            raise RegistryError("inventory round must be a positive integer")
        if (not isinstance(release_version, str) or
                not release_version.isascii() or
                INVENTORY_RELEASE_VERSION.fullmatch(
                    release_version) is None or
                not release_version.endswith(f"-round{round_number}")):
            raise RegistryError(
                "inventory release version must match its round")
        assert source_baseline is not None
        assert source_baseline_artifact_root is not None
        baseline_binding = _source_baseline_binding(
            root, source_baseline, source_baseline_artifact_root)
    else:
        baseline_binding = None
    canonical = {
        wrapper
        for job in registry["jobs"].values()
        for wrapper in job["compatibility_wrappers"]
    }
    records = []
    for path in sorted(root.iterdir(), key=lambda candidate: candidate.name):
        if (not path.is_file() or path.is_symlink() or
                path.suffix not in {".sh", ".ps1"}):
            continue
        data = path.read_bytes()
        text = data.decode("utf-8", errors="replace")
        targets = sorted(set(PYTHON_TARGET.findall(text)))
        target_exists = (
            len(targets) == 1 and (root / "scripts" / targets[0]).is_file()
        )
        if target_exists:
            lifecycle = "compat"
        else:
            lifecycle = "archive"
        records.append({
            "path": path.name,
            "sha256": hashlib.sha256(data).hexdigest(),
            "size": len(data),
            "lifecycle": lifecycle,
            "python_targets": targets,
        })
    for relative in sorted(canonical):
        path = root / relative
        metadata = _safe_regular(path, "generated compatibility wrapper")
        data = path.read_bytes()
        records.append({
            "path": relative,
            "sha256": hashlib.sha256(data).hexdigest(),
            "size": metadata.st_size,
            "lifecycle": "canonical",
            "python_targets": ["hepta_ops.py"],
        })
    records.sort(key=lambda record: record["path"])
    implementations = []
    for path in sorted((root / "scripts").glob("openclaw_fx_*.py")):
        if path.is_symlink() or not path.is_file():
            continue
        implementations.append({
            "path": path.relative_to(root).as_posix(),
            "sha256": sha256(path),
            "size": path.stat().st_size,
            "lifecycle": "compat",
        })
    implementation_tests = []
    for path in sorted((root / "scripts").glob("test_openclaw_fx_*.py")):
        if path.is_symlink() or not path.is_file():
            continue
        implementation_tests.append({
            "path": path.relative_to(root).as_posix(),
            "sha256": sha256(path),
            "size": path.stat().st_size,
            "lifecycle": "archive",
        })
    counts = {
        lifecycle: sum(record["lifecycle"] == lifecycle for record in records)
        for lifecycle in ("canonical", "compat", "archive")
    }
    inventory = {
        "schema": "hepta.ops-inventory.v1",
        "wrapper_count": len(records),
        "wrapper_counts": counts,
        "implementation_count": len(implementations),
        "implementation_test_count": len(implementation_tests),
        "wrappers": records,
        "implementations": implementations,
        "implementation_tests": implementation_tests,
    }
    if baseline_binding is not None:
        inventory.update({
            "schema": "hepta.ops-inventory.v2",
            "version": 2,
            "project_id": PROJECT_ID,
            "round": round_number,
            "release_version": release_version,
            "source_baseline": baseline_binding,
        })
    return inventory


def shim_bytes(job_id: str, wrapper_name: str) -> bytes:
    quoted_job = shlex.quote(job_id)
    quoted_wrapper = shlex.quote(wrapper_name)
    return (
        "#!/bin/sh\n"
        "set -eu\n"
        "unset BASH_ENV ENV CDPATH\n"
        "export PYTHONDONTWRITEBYTECODE=1 PYTHONNOUSERSITE=1\n"
        'case "$0" in\n'
        '  */*) HEPTA_SHIM_DIR=${0%/*} ;;\n'
        '  *) HEPTA_SHIM_DIR=. ;;\n'
        'esac\n'
        'CDPATH= cd -- "$HEPTA_SHIM_DIR/../.."\n'
        'ROOT=$PWD\n'
        'unset HEPTA_SHIM_DIR\n'
        f'exec /usr/bin/python3 "$ROOT/scripts/hepta_ops.py" '
        f'--root "$ROOT" run --compat-wrapper {quoted_wrapper} '
        f'{quoted_job} -- "$@"\n'
    ).encode("utf-8")


def _safe_output_directory(path: Path, check: bool) -> Path:
    _reject_symlink_ancestor(path, "shim output")
    if path.is_symlink():
        raise RegistryError("shim output must not be a symlink")
    if not path.exists():
        if check:
            raise RegistryError("shim output is unavailable in check mode")
        path.mkdir(parents=True)
    absolute = Path(os.path.abspath(path))
    resolved = path.resolve(strict=True)
    if absolute != resolved:
        raise RegistryError("shim output contains a symlink component")
    metadata = path.lstat()
    if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISDIR(metadata.st_mode):
        raise RegistryError("shim output must be a regular directory")
    return resolved


def install_shims(
        output: Path, registry: dict[str, Any], check: bool) -> int:
    output = _safe_output_directory(output, check)
    output_before = output.lstat()
    directory_identity = lambda value: (
        value.st_dev, value.st_ino, value.st_mode, value.st_uid, value.st_gid,
    )
    file_identity = lambda value: (
        value.st_dev, value.st_ino, value.st_mode, value.st_nlink,
        value.st_uid, value.st_gid, value.st_size, value.st_mtime_ns,
        value.st_ctime_ns,
    )
    descriptor = os.open(
        output,
        os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) |
        getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0),
    )

    def read_expected(name: str, content: bytes) -> None:
        try:
            before = os.stat(
                name, dir_fd=descriptor, follow_symlinks=False)
        except FileNotFoundError as error:
            raise RegistryError(f"generated shim drift: {name}") from error
        if (not stat.S_ISREG(before.st_mode) or before.st_nlink != 1 or
                stat.S_IMODE(before.st_mode) != 0o755 or
                before.st_uid != os.geteuid()):
            raise RegistryError(f"generated shim drift: {name}")
        read_flags = (
            os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) |
            getattr(os, "O_NOFOLLOW", 0)
        )
        source = os.open(name, read_flags, dir_fd=descriptor)
        try:
            opened = os.fstat(source)
            chunks: list[bytes] = []
            while True:
                chunk = os.read(source, 65536)
                if not chunk:
                    break
                chunks.append(chunk)
            after_descriptor = os.fstat(source)
        finally:
            os.close(source)
        after_path = os.stat(
            name, dir_fd=descriptor, follow_symlinks=False)
        if (file_identity(before) != file_identity(opened) or
                file_identity(opened) != file_identity(after_descriptor) or
                file_identity(after_descriptor) != file_identity(after_path) or
                b"".join(chunks) != content):
            raise RegistryError(f"generated shim drift: {name}")

    try:
        if (directory_identity(output_before) !=
                directory_identity(os.fstat(descriptor))):
            raise RegistryError("shim output changed while opening")
        fcntl.flock(descriptor, fcntl.LOCK_EX)
        expected: set[str] = set()
        for job_id, job in registry["jobs"].items():
            for relative in job["compatibility_wrappers"]:
                name = Path(relative).name
                expected.add(name)
                content = shim_bytes(job_id, name)
                if check:
                    read_expected(name, content)
                    continue
                flags = (
                    os.O_CREAT | os.O_EXCL | os.O_WRONLY |
                    getattr(os, "O_CLOEXEC", 0) |
                    getattr(os, "O_NOFOLLOW", 0)
                )
                temporary_name = (
                    f".{name}.{os.getpid()}."
                    f"{secrets.token_hex(8)}.tmp")
                temporary_descriptor = os.open(
                    temporary_name, flags, 0o755, dir_fd=descriptor)
                try:
                    os.fchmod(temporary_descriptor, 0o755)
                    offset = 0
                    while offset < len(content):
                        offset += os.write(
                            temporary_descriptor, content[offset:])
                    os.fsync(temporary_descriptor)
                finally:
                    os.close(temporary_descriptor)
                try:
                    try:
                        existing = os.stat(
                            name, dir_fd=descriptor,
                            follow_symlinks=False)
                    except FileNotFoundError:
                        existing = None
                    if existing is not None and (
                            not stat.S_ISREG(existing.st_mode) or
                            existing.st_nlink != 1 or
                            existing.st_uid != os.geteuid() or
                            existing.st_mode & 0o022):
                        raise RegistryError(
                            f"generated shim destination is unsafe: {name}")
                    os.replace(
                        temporary_name, name,
                        src_dir_fd=descriptor, dst_dir_fd=descriptor)
                    temporary_name = ""
                    read_expected(name, content)
                except BaseException:
                    if temporary_name:
                        try:
                            os.unlink(temporary_name, dir_fd=descriptor)
                        except FileNotFoundError:
                            pass
                    raise
        unexpected = sorted(
            name for name in os.listdir(descriptor)
            if name not in expected
        )
        if unexpected:
            raise RegistryError(
                "unexpected generated shim entries: " + ", ".join(unexpected))
        os.fsync(descriptor)
        if (directory_identity(output.lstat()) !=
                directory_identity(output_before) or
                directory_identity(os.fstat(descriptor)) !=
                directory_identity(output_before)):
            raise RegistryError("shim output changed during operation")
    finally:
        os.close(descriptor)
    return len(expected)


def telemetry(wrapper: str) -> None:
    print(
        f"hepta-ops: compatibility wrapper '{wrapper}' is deprecated; "
        "use hepta-ops run",
        file=sys.stderr,
    )
    destination = os.environ.get("HEPTA_OPS_TELEMETRY", "")
    if not destination:
        return
    path = Path(os.path.abspath(destination))
    if (path.name != "compat-wrapper-usage.jsonl" or
            path == Path("/") or not path.is_absolute()):
        raise RegistryError(
            "telemetry path must end in compat-wrapper-usage.jsonl")
    directory_flags = (
        os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) |
        getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    )
    directory_identity = lambda value: (
        value.st_dev, value.st_ino, value.st_mode, value.st_uid, value.st_gid,
    )
    descriptors: list[int] = []
    components: list[tuple[str, tuple[int, ...]]] = []
    parent_descriptor = os.open("/", directory_flags)
    descriptors.append(parent_descriptor)
    try:
        for component in path.parent.parts[1:]:
            before_component = os.stat(
                component, dir_fd=parent_descriptor,
                follow_symlinks=False)
            if (stat.S_ISLNK(before_component.st_mode) or
                    not stat.S_ISDIR(before_component.st_mode)):
                raise RegistryError("telemetry contains an unsafe parent")
            child = os.open(
                component, directory_flags, dir_fd=parent_descriptor)
            if (directory_identity(before_component) !=
                    directory_identity(os.fstat(child))):
                os.close(child)
                raise RegistryError("telemetry parent changed while opening")
            components.append(
                (component, directory_identity(before_component)))
            descriptors.append(child)
            parent_descriptor = child
        private_parent = os.fstat(parent_descriptor)
        if (private_parent.st_uid != os.geteuid() or
                stat.S_IMODE(private_parent.st_mode) != 0o700):
            raise RegistryError(
                "telemetry parent must be caller-owned mode 0700")
        before: os.stat_result | None
        try:
            before = os.stat(
                path.name, dir_fd=parent_descriptor,
                follow_symlinks=False)
        except FileNotFoundError:
            before = None
        if before is not None and (
                not stat.S_ISREG(before.st_mode) or before.st_nlink != 1 or
                before.st_uid != os.geteuid() or
                stat.S_IMODE(before.st_mode) != 0o600):
            raise RegistryError("telemetry must be a protected regular file")
        payload = canonical_json({
            "schema": "hepta.ops-compat-telemetry.v1",
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "wrapper": wrapper,
        })
        flags = (
            os.O_APPEND | os.O_CREAT | os.O_WRONLY |
            getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
        )
        descriptor = os.open(
            path.name, flags, 0o600, dir_fd=parent_descriptor)
        try:
            if before is None:
                os.fchmod(descriptor, 0o600)
            opened = os.fstat(descriptor)
            if (not stat.S_ISREG(opened.st_mode) or opened.st_nlink != 1 or
                    opened.st_uid != os.geteuid() or
                    stat.S_IMODE(opened.st_mode) != 0o600):
                raise RegistryError(
                    "telemetry must be a protected regular file")
            if before is not None and _identity(before) != _identity(opened):
                raise RegistryError("telemetry changed during open")
            offset = 0
            while offset < len(payload):
                offset += os.write(descriptor, payload[offset:])
            os.fsync(descriptor)
            after_descriptor = os.fstat(descriptor)
        finally:
            os.close(descriptor)
        after_path = os.stat(
            path.name, dir_fd=parent_descriptor,
            follow_symlinks=False)
        if _identity(after_descriptor) != _identity(after_path):
            raise RegistryError("telemetry changed during append")
        for index, (component, expected) in enumerate(components):
            current = os.stat(
                component, dir_fd=descriptors[index],
                follow_symlinks=False)
            if directory_identity(current) != expected:
                raise RegistryError("telemetry parent changed during append")
    finally:
        for opened_descriptor in reversed(descriptors):
            os.close(opened_descriptor)


def _resolve_executable(root: Path, relative: str) -> Path:
    candidate = root / relative
    metadata = _safe_regular(candidate, "job executable")
    executable = candidate.resolve(strict=True)
    if (root not in executable.parents or
            Path(os.path.abspath(candidate)) != executable):
        raise RegistryError("job executable escapes repository root")
    after = executable.lstat()
    if _identity(metadata) != _identity(after):
        raise RegistryError("job executable changed during resolution")
    return executable


def run_job(
        root: Path, job_id: str, job: dict[str, Any],
        user_arguments: list[str], compatibility_wrapper: str) -> int:
    if job["lifecycle"] == "archive":
        raise RegistryError("archive jobs cannot be executed")
    if user_arguments and not job["allow_user_arguments"]:
        raise RegistryError("job does not accept user arguments")
    if job["paper_authorized"] or job["live_authorized"]:
        raise RegistryError("hepta-ops registry cannot authorize trading")
    executable = _resolve_executable(root, job["executable"])
    if compatibility_wrapper:
        expected = {
            Path(wrapper).name for wrapper in job["compatibility_wrappers"]
        }
        if compatibility_wrapper not in expected:
            raise RegistryError("compatibility wrapper is not bound to job")
        telemetry(compatibility_wrapper)
    arguments = [str(executable), *job["arguments"], *user_arguments]
    environment = os.environ.copy()
    for name in ("PYTHONHOME", "PYTHONPATH", "PYTHONSTARTUP"):
        environment.pop(name, None)
    environment["PYTHONDONTWRITEBYTECODE"] = "1"
    environment["PYTHONNOUSERSITE"] = "1"
    environment["PATH"] = "/usr/sbin:/usr/bin:/sbin:/bin"
    close_inherited_descriptors()
    apply_no_network_filter()
    os.execve(sys.executable, [sys.executable, *arguments], environment)
    return 70


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(prog="hepta-ops")
    result.add_argument(
        "--root", type=Path,
        default=Path(__file__).resolve().parents[1])
    result.add_argument("--registry", type=Path)
    commands = result.add_subparsers(dest="command", required=True)
    commands.add_parser("status")
    report = commands.add_parser("report")
    report.add_argument("--output", type=Path)
    report.add_argument("--round", dest="round_number", type=int)
    report.add_argument("--release-version")
    report.add_argument("--source-baseline", type=Path)
    report.add_argument("--source-baseline-artifact-root", type=Path)
    install = commands.add_parser("install")
    install.add_argument("--output", type=Path, required=True)
    install.add_argument("--check", action="store_true")
    run = commands.add_parser("run")
    run.add_argument("job_id")
    run.add_argument("--compat-wrapper", default="")
    run.add_argument("arguments", nargs=argparse.REMAINDER)
    release = commands.add_parser("release")
    release_commands = release.add_subparsers(dest="release_command", required=True)
    release_check = release_commands.add_parser(
        "check", help="native/Linux release phase check (dev, rc, or paper)")
    release_check.add_argument("arguments", nargs=argparse.REMAINDER)
    return result


def main(argv: list[str] | None = None) -> int:
    # Keep the user-facing spelling ergonomic while retaining the registry
    # sandbox used by ``run``.  Options after ``release check`` belong to the
    # checked job and are forwarded verbatim.
    raw_argv = list(sys.argv[1:] if argv is None else argv)
    if len(raw_argv) >= 2 and raw_argv[0:2] == ["release", "check"]:
        return main(["run", "release.check", "--", *raw_argv[2:]])
    args = parser().parse_args(argv)
    root = args.root.resolve(strict=True)
    registry_path = args.registry or root / "ops" / "hepta-ops-v1.json"
    if not registry_path.is_absolute():
        registry_path = root / registry_path
    registry = load_registry(registry_path)
    if args.command == "status":
        jobs = []
        for job_id, job in registry["jobs"].items():
            available = True
            try:
                _resolve_executable(root, job["executable"])
            except (RegistryError, OSError):
                available = False
            jobs.append({
                "job_id": job_id,
                "lifecycle": job["lifecycle"],
                "executable": job["executable"],
                "available": available,
                "network_allowed": job["network_allowed"],
                "network_isolation": "linux-seccomp",
                "paper_authorized": job["paper_authorized"],
                "live_authorized": job["live_authorized"],
            })
        print(canonical_json({
            "schema": "hepta.ops-status.v1",
            "passed": all(job["available"] for job in jobs),
            "jobs": jobs,
        }).decode(), end="")
        return 0 if all(job["available"] for job in jobs) else 1
    if args.command == "report":
        report = wrapper_inventory(
            root, registry,
            round_number=args.round_number,
            release_version=args.release_version,
            source_baseline=args.source_baseline,
            source_baseline_artifact_root=
                args.source_baseline_artifact_root)
        payload = canonical_json(report)
        if args.output:
            output = args.output if args.output.is_absolute() else root / args.output
            _atomic_private_write(output, payload)
        print(payload.decode(), end="")
        return 0
    if args.command == "install":
        count = install_shims(
            args.output if args.output.is_absolute() else root / args.output,
            registry,
            args.check,
        )
        print(f"PASS: {count} compatibility wrappers")
        return 0
    if args.command == "run":
        if args.job_id not in registry["jobs"]:
            raise RegistryError(f"unknown job: {args.job_id}")
        user_arguments = args.arguments
        if user_arguments[:1] == ["--"]:
            user_arguments = user_arguments[1:]
        return run_job(
            root, args.job_id, registry["jobs"][args.job_id],
            user_arguments, args.compat_wrapper)
    if args.command == "release" and args.release_command == "check":
        user_arguments = args.arguments
        if user_arguments[:1] == ["--"]:
            user_arguments = user_arguments[1:]
        if "release.check" not in registry["jobs"]:
            raise RegistryError("release.check job is missing from registry")
        return run_job(
            root, "release.check", registry["jobs"]["release.check"],
            user_arguments, "")
    raise RegistryError("unsupported command")


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (RegistryError, SandboxError, OSError) as error:
        print(f"hepta-ops: {error}", file=sys.stderr)
        raise SystemExit(78)
