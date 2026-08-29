#!/usr/bin/env python3
"""Build a deterministic passive Agent/Simulator runtime from strict source."""

from __future__ import annotations

import argparse
from dataclasses import dataclass
import io
import json
import os
import pathlib
import re
import secrets
import shutil
import stat
import subprocess
import sys
import tarfile
import tempfile
from typing import Any

import verify_heptatrader_clean_source_bundle as source_verifier
import verify_heptatrader_runtime_package as runtime_verifier
import verify_heptatrader_vendor_overlay_set as vendor_set_verifier


VENDOR_SCHEMA = "hepta.vendor-overlay-set.v1"
VENDOR_ARTIFACT_CLASS = "metadata-only-vendor-overlay-set"
VENDOR_FIELDS = {
    "schema", "release_version", "artifact_class", "source_ref",
    "overlay_count", "overlays", "payload_included",
    "distribution_authorized", "required_by_runtime_package_ids",
    "paper_authorized", "live_authorized",
}
VENDOR_SOURCE_FIELDS = {
    "bundle_sha256", "manifest_sha256", "files_sha256",
    "security_manifest_sha256", "git_head",
}
BUILD_TARGETS = [
    "hepta_tool_gatewayd",
    "heptactl",
    "hepta_sessionctl",
    "hepta_executiond",
]
MAX_VENDOR_DESCRIPTOR_BYTES = 16 * 1024 * 1024


class RuntimeBuildError(ValueError):
    """The runtime package could not be built under the closed boundary."""


def _digest_field(value: Any, label: str) -> str:
    if (not isinstance(value, str) or not value.startswith("sha256:") or
            runtime_verifier.HEX64.fullmatch(value[7:]) is None):
        raise RuntimeBuildError(f"{label} is not a SHA-256 identity")
    return value


def _source_ref(
        source_manifest: dict[str, Any],
        source_result: dict[str, Any],
        source_bundle_bytes: bytes,
        source_manifest_bytes: bytes) -> dict[str, Any]:
    result = {
        "schema": "hepta.clean-source-bundle.v2",
        "bundle_sha256":
            "sha256:" + runtime_verifier.sha256(source_bundle_bytes),
        "manifest_sha256":
            "sha256:" + runtime_verifier.sha256(source_manifest_bytes),
        "files_sha256": "sha256:" + source_manifest["files_sha256"],
        "security_manifest_sha256":
            source_manifest["security_manifest_sha256"],
        "git_head": source_manifest["git_head"],
        "root": source_manifest["root"],
    }
    if (
            source_result.get("bundle_sha256") !=
            result["bundle_sha256"][7:] or
            source_result.get("manifest_sha256") !=
            result["manifest_sha256"][7:] or
            source_result.get("files_sha256") !=
            source_manifest["files_sha256"] or
            source_result.get("git_head") != source_manifest["git_head"] or
            source_result.get("version") != source_manifest["version"]):
        raise RuntimeBuildError(
            "strict-source verifier result does not bind the captured inputs")
    return result


def validate_vendor_descriptor(
        value: Any,
        *,
        source_ref: dict[str, Any],
        source_manifest: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) != VENDOR_FIELDS:
        raise RuntimeBuildError(
            "vendor overlay-set descriptor fields do not match schema")
    if (value["schema"] != VENDOR_SCHEMA or
            value["artifact_class"] != VENDOR_ARTIFACT_CLASS or
            value["release_version"] != source_manifest["version"] or
            value["payload_included"] is not False or
            value["distribution_authorized"] is not False or
            value["required_by_runtime_package_ids"] != [] or
            value["paper_authorized"] is not False or
            value["live_authorized"] is not False):
        raise RuntimeBuildError(
            "vendor overlay-set descriptor grants an unsupported boundary")
    vendor_source = value["source_ref"]
    if (not isinstance(vendor_source, dict) or
            set(vendor_source) != VENDOR_SOURCE_FIELDS):
        raise RuntimeBuildError("vendor overlay-set source_ref is invalid")
    expected_source = {
        key: source_ref[key] for key in VENDOR_SOURCE_FIELDS
    }
    if vendor_source != expected_source:
        raise RuntimeBuildError(
            "vendor overlay-set does not bind the strict-source inputs")
    overlays = value["overlays"]
    if (value["overlay_count"] != 3 or not isinstance(overlays, list) or
            len(overlays) != 3):
        raise RuntimeBuildError(
            "vendor overlay-set must contain the three reviewed metadata rows")
    seen_ids: set[str] = set()
    for overlay in overlays:
        if not isinstance(overlay, dict):
            raise RuntimeBuildError(
                "vendor overlay metadata row is not an object")
        overlay_id = overlay.get("overlay_id")
        if (not isinstance(overlay_id, str) or
                re.fullmatch(r"[a-z0-9][a-z0-9._-]{0,95}", overlay_id) is None or
                overlay_id in seen_ids):
            raise RuntimeBuildError("vendor overlay_id is invalid or duplicate")
        seen_ids.add(overlay_id)
        if (overlay.get("payload_included") is not False or
                overlay.get("distribution_authorized") is not False or
                overlay.get("required_by_runtime_package_ids") != []):
            raise RuntimeBuildError(
                "vendor overlay metadata row grants an unsupported boundary")
    if overlays != sorted(overlays, key=lambda item: item["overlay_id"]):
        raise RuntimeBuildError("vendor overlay rows are not canonical")
    return value


def _write_private_copy(path: pathlib.Path, data: bytes) -> None:
    descriptor = os.open(
        path, os.O_WRONLY | os.O_CREAT | os.O_EXCL |
        getattr(os, "O_CLOEXEC", 0), 0o600)
    try:
        view = memoryview(data)
        while view:
            written = os.write(descriptor, view)
            if written <= 0:
                raise RuntimeBuildError("failed to write captured input")
            view = view[written:]
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _safe_extract_source(
        bundle_bytes: bytes,
        manifest_bytes: bytes,
        manifest: dict[str, Any],
        destination: pathlib.Path) -> pathlib.Path:
    root_name = runtime_verifier.canonical_relative(
        manifest["root"], "strict-source root")
    root = destination / root_name
    root.mkdir(mode=0o755)
    prefix = root_name + "/"
    internal = prefix + ".hepta/source-bundle-manifest.json"
    expected = {item["path"]: item for item in manifest["files"]}
    seen: set[str] = set()
    internal_count = 0
    try:
        archive = tarfile.open(fileobj=io.BytesIO(bundle_bytes), mode="r:")
    except tarfile.TarError as error:
        raise RuntimeBuildError("strict-source bundle is not a plain tar") from error
    with archive:
        for member in archive.getmembers():
            runtime_verifier.canonical_relative(
                member.name, "strict-source tar member")
            if (not member.isfile() or member.type not in {
                    tarfile.REGTYPE, tarfile.AREGTYPE} or member.linkname or
                    member.pax_headers or member.uid != 0 or member.gid != 0 or
                    member.uname != "root" or member.gname != "root" or
                    member.mtime != 0 or member.devmajor != 0 or
                    member.devminor != 0):
                raise RuntimeBuildError(
                    f"unsafe strict-source tar metadata: {member.name}")
            extracted = archive.extractfile(member)
            if extracted is None:
                raise RuntimeBuildError(
                    f"unreadable strict-source member: {member.name}")
            data = extracted.read()
            if member.name == internal:
                internal_count += 1
                if member.mode != 0o644 or data != manifest_bytes:
                    raise RuntimeBuildError(
                        "strict-source internal manifest drift")
                continue
            if not member.name.startswith(prefix):
                raise RuntimeBuildError(
                    "strict-source member escapes canonical root")
            relative = member.name[len(prefix):]
            record = expected.get(relative)
            if record is None or relative in seen:
                raise RuntimeBuildError(
                    f"unregistered strict-source member: {relative}")
            if (member.mode != int(record["mode"], 8) or
                    member.size != len(data) or
                    member.size != record["size"] or
                    runtime_verifier.sha256(data) != record["sha256"]):
                raise RuntimeBuildError(
                    f"strict-source payload drift: {relative}")
            target = root.joinpath(*pathlib.PurePosixPath(relative).parts)
            target.parent.mkdir(parents=True, exist_ok=True, mode=0o755)
            descriptor = os.open(
                target, os.O_WRONLY | os.O_CREAT | os.O_EXCL |
                getattr(os, "O_CLOEXEC", 0) |
                getattr(os, "O_NOFOLLOW", 0), member.mode)
            try:
                view = memoryview(data)
                while view:
                    written = os.write(descriptor, view)
                    if written <= 0:
                        raise RuntimeBuildError(
                            f"failed to extract strict source: {relative}")
                    view = view[written:]
                os.fchmod(descriptor, member.mode)
            finally:
                os.close(descriptor)
            seen.add(relative)
    if internal_count != 1 or seen != set(expected):
        raise RuntimeBuildError("strict-source extraction closure is incomplete")
    if (root / ".git").exists() or (root / ".git").is_symlink():
        raise RuntimeBuildError("Git metadata entered strict-source extraction")
    return root


def _minimal_build_environment(temporary: pathlib.Path) -> dict[str, str]:
    home = temporary / "home"
    cache = temporary / "xdg-cache"
    config = temporary / "xdg-config"
    tmp = temporary / "tmp"
    for path in (home, cache, config, tmp):
        path.mkdir(mode=0o700, exist_ok=True)
        path.chmod(0o700)
    return {
        "PATH": os.environ.get(
            "PATH", "/usr/local/bin:/usr/bin:/bin"),
        "HOME": str(home),
        "XDG_CACHE_HOME": str(cache),
        "XDG_CONFIG_HOME": str(config),
        "TMPDIR": str(tmp),
        "LANG": "C",
        "LC_ALL": "C",
        "TZ": "UTC",
        "SOURCE_DATE_EPOCH": "0",
        "CCACHE_DISABLE": "1",
    }


def _run(
        command: list[str],
        *,
        environment: dict[str, str],
        label: str) -> None:
    previous_umask = os.umask(0o022)
    try:
        result = subprocess.run(
            command, check=False, stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            text=True, encoding="utf-8", errors="replace",
            env=environment)
    finally:
        os.umask(previous_umask)
    if result.returncode != 0:
        output = result.stdout[-12000:]
        raise RuntimeBuildError(
            f"{label} failed with status {result.returncode}:\n{output}")


def _verify_cache(build: pathlib.Path, source: pathlib.Path) -> None:
    cache_path = build / "CMakeCache.txt"
    if not cache_path.is_file() or cache_path.is_symlink():
        raise RuntimeBuildError("fresh build did not produce CMakeCache.txt")
    values: dict[str, list[str]] = {}
    for line in cache_path.read_text(
            encoding="utf-8", errors="strict").splitlines():
        if not line or line.startswith(("//", "#")) or "=" not in line:
            continue
        key_and_type, value = line.split("=", 1)
        key = key_and_type.split(":", 1)[0]
        values.setdefault(key, []).append(value)
    expected = {
        "CMAKE_BUILD_TYPE": "Release",
        "CMAKE_INSTALL_PREFIX": "/usr",
        "CMAKE_SKIP_RPATH": "ON",
        "CMAKE_BUILD_RPATH_USE_ORIGIN": "OFF",
        "HEPTA_ENABLE_IBAPI": "OFF",
        "HEPTA_ENABLE_LEGACY_0DTE_BRIDGE": "OFF",
        "HEPTA_BUILD_LEGACY_MONOLITH": "OFF",
        "HEPTA_BUILD_LEGACY_SIMULATOR": "OFF",
    }
    for key, value in expected.items():
        if values.get(key) != [value]:
            raise RuntimeBuildError(
                f"fresh build cache boundary drift: {key}")
    home = values.get("CMAKE_HOME_DIRECTORY", [])
    if home != [str(source.resolve(strict=True))]:
        raise RuntimeBuildError("fresh build is not bound to extracted source")


def _run_fresh_build(
        source: pathlib.Path,
        temporary: pathlib.Path,
        *,
        cmake: str,
        jobs: int) -> tuple[pathlib.Path, pathlib.Path]:
    build = temporary / "build"
    agent = temporary / "agent-stage"
    execution = temporary / "execution-stage"
    if any(path.exists() for path in (build, agent, execution)):
        raise RuntimeBuildError("fresh build workspace was unexpectedly reused")
    environment = _minimal_build_environment(temporary)
    _run([
        cmake, "-S", str(source), "-B", str(build),
        "-DCMAKE_BUILD_TYPE=Release",
        "-DCMAKE_INSTALL_PREFIX=/usr",
        "-DBUILD_TESTING=OFF",
        "-DHEPTA_ENABLE_IBAPI=OFF",
        "-DHEPTA_ENABLE_LEGACY_0DTE_BRIDGE=OFF",
        "-DHEPTA_BUILD_LEGACY_MONOLITH=OFF",
        "-DHEPTA_BUILD_LEGACY_SIMULATOR=OFF",
        "-DCMAKE_SKIP_RPATH=ON",
        "-DCMAKE_BUILD_RPATH_USE_ORIGIN=OFF",
    ], environment=environment, label="fresh Release configure")
    _verify_cache(build, source)
    _run([
        cmake, "--build", str(build), "--parallel", str(jobs),
        "--target", *BUILD_TARGETS,
    ], environment=environment, label="fresh Release build")
    for component, destination in (
            ("hepta-agent-os-runtime", agent),
            ("hepta-execution-runtime", execution)):
        install_environment = dict(environment)
        install_environment["DESTDIR"] = str(destination)
        _run([
            cmake, "--install", str(build), "--prefix", "/usr",
            "--component", component,
        ], environment=install_environment,
            label=f"fresh {component} install")
    return agent, execution


def _verify_stage_semantics(
        source: pathlib.Path,
        agent: pathlib.Path,
        execution: pathlib.Path,
        *,
        environment: dict[str, str]) -> None:
    agent_checker = source / "tests/check_hepta_agent_os_install_tree.py"
    execution_checker = source / "tests/check_hepta_execution_install_tree.py"
    if (not agent_checker.is_file() or agent_checker.is_symlink() or
            not execution_checker.is_file() or execution_checker.is_symlink()):
        raise RuntimeBuildError(
            "strict source lacks reviewed runtime semantic validators")
    agent_program = (
        "import pathlib,runpy,sys;"
        "module=runpy.run_path(sys.argv[1]);"
        "module['verify'](pathlib.Path(sys.argv[2]))")
    execution_program = (
        "import pathlib,runpy,sys;"
        "module=runpy.run_path(sys.argv[1]);"
        "module['verify_tree'](pathlib.Path(sys.argv[2]),False)")
    _run([
        sys.executable, "-I", "-c", agent_program,
        str(agent_checker), str(agent),
    ], environment=environment, label="Agent component semantic validation")
    _run([
        sys.executable, "-I", "-c", execution_program,
        str(execution_checker), str(execution),
    ], environment=environment,
        label="Execution component semantic validation")


def _parent_directories(paths: set[str]) -> set[str]:
    directories: set[str] = set()
    for relative in paths:
        current = pathlib.PurePosixPath(relative).parent
        while current != pathlib.PurePosixPath("."):
            directories.add(current.as_posix())
            current = current.parent
    return directories


def _stage_identity(metadata: os.stat_result) -> tuple[int, ...]:
    return (
        metadata.st_dev, metadata.st_ino, metadata.st_mode,
        metadata.st_nlink, metadata.st_uid, metadata.st_gid,
        metadata.st_size, metadata.st_mtime_ns, metadata.st_ctime_ns,
    )


def _read_component(
        root: pathlib.Path,
        expected: dict[str, int],
        label: str) -> dict[str, tuple[int, bytes]]:
    try:
        root_before = root.lstat()
    except OSError as error:
        raise RuntimeBuildError(f"{label} staging root is unavailable") from error
    if (not stat.S_ISDIR(root_before.st_mode) or
            stat.S_ISLNK(root_before.st_mode) or
            stat.S_IMODE(root_before.st_mode) != 0o755):
        raise RuntimeBuildError(f"{label} staging root is unsafe")
    files: dict[str, tuple[int, bytes]] = {}
    directories: set[str] = set()
    for current, names, filenames in os.walk(
            root, topdown=True, followlinks=False):
        current_path = pathlib.Path(current)
        for name in names:
            path = current_path / name
            relative = path.relative_to(root).as_posix()
            metadata = path.lstat()
            if (not stat.S_ISDIR(metadata.st_mode) or
                    stat.S_ISLNK(metadata.st_mode) or
                    stat.S_IMODE(metadata.st_mode) != 0o755):
                raise RuntimeBuildError(
                    f"{label} contains unsafe directory: {relative}")
            directories.add(relative)
        for name in filenames:
            path = current_path / name
            relative = path.relative_to(root).as_posix()
            before = path.lstat()
            if (not stat.S_ISREG(before.st_mode) or
                    stat.S_ISLNK(before.st_mode) or before.st_nlink != 1):
                raise RuntimeBuildError(
                    f"{label} contains unsafe file: {relative}")
            mode = stat.S_IMODE(before.st_mode)
            if expected.get(relative) != mode:
                raise RuntimeBuildError(
                    f"{label} contains unapproved file or mode: {relative}")
            descriptor = os.open(
                path, os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) |
                getattr(os, "O_NOFOLLOW", 0))
            try:
                opened = os.fstat(descriptor)
                if _stage_identity(before) != _stage_identity(opened):
                    raise RuntimeBuildError(
                        f"{label} file changed before open: {relative}")
                chunks: list[bytes] = []
                remaining = opened.st_size
                while remaining:
                    chunk = os.read(
                        descriptor, min(1024 * 1024, remaining))
                    if not chunk:
                        raise RuntimeBuildError(
                            f"{label} file was truncated: {relative}")
                    chunks.append(chunk)
                    remaining -= len(chunk)
                if os.read(descriptor, 1):
                    raise RuntimeBuildError(
                        f"{label} file grew while reading: {relative}")
                data = b"".join(chunks)
                after = os.fstat(descriptor)
            finally:
                os.close(descriptor)
            try:
                final = path.lstat()
            except OSError as error:
                raise RuntimeBuildError(
                    f"{label} file disappeared while reading: "
                    f"{relative}") from error
            if (_stage_identity(opened) != _stage_identity(after) or
                    _stage_identity(after) != _stage_identity(final) or
                    final.st_nlink != 1 or
                    stat.S_IMODE(final.st_mode) != mode or
                    len(data) != opened.st_size):
                raise RuntimeBuildError(
                    f"{label} file changed while reading: {relative}")
            files[relative] = (mode, data)
    if set(files) != set(expected):
        raise RuntimeBuildError(
            f"{label} file closure drift missing="
            f"{sorted(set(expected) - set(files))} unexpected="
            f"{sorted(set(files) - set(expected))}")
    if directories != _parent_directories(set(expected)):
        raise RuntimeBuildError(f"{label} directory closure drift")
    try:
        root_after = root.lstat()
    except OSError as error:
        raise RuntimeBuildError(
            f"{label} staging root disappeared") from error
    if _stage_identity(root_before) != _stage_identity(root_after):
        raise RuntimeBuildError(f"{label} staging root changed while reading")
    return files


def _merge_components(
        agent: dict[str, tuple[int, bytes]],
        execution: dict[str, tuple[int, bytes]]) -> dict[str, tuple[int, bytes]]:
    merged = dict(agent)
    for path, payload in execution.items():
        previous = merged.get(path)
        if previous is not None and previous != payload:
            raise RuntimeBuildError(
                f"component duplicate differs in bytes or mode: {path}")
        merged[path] = payload
    if (set(merged) != set(runtime_verifier.PRODUCT_FILES) or
            len(merged) != runtime_verifier.PRODUCT_FILE_COUNT):
        raise RuntimeBuildError(
            "merged runtime product closure is not exactly "
            f"{runtime_verifier.PRODUCT_FILE_COUNT} files")
    for relative, (_, data) in merged.items():
        lowered = relative.lower()
        release_dependency = relative in {
            *runtime_verifier.RELEASE_VALIDATION_COMPANION_FILES,
            *runtime_verifier.RELEASE_VALIDATION_PACKAGE_FILES,
        }
        if not release_dependency and any(part in lowered for part in (
                "/include/", "/cmake/", "sdk", "ib-paper", "ctp",
                "third_party", "prebuilt", "secret", "credential")):
            raise RuntimeBuildError(
                f"forbidden runtime payload path entered package: {relative}")
        if relative.startswith(("etc/", "run/", "var/")):
            raise RuntimeBuildError(
                f"host-state path entered runtime package: {relative}")
    try:
        runtime_verifier.validate_systemd_semantics({
            path: data for path, (_, data) in merged.items()
        })
        runtime_verifier.validate_default_deny_identity_source({
            path: data for path, (_, data) in merged.items()
        })
    except runtime_verifier.RuntimePackageError as error:
        raise RuntimeBuildError(
            f"staged runtime systemd contract is unsafe: {error}") from error
    return merged


def _manifest_and_payloads(
        merged: dict[str, tuple[int, bytes]],
        *,
        source_ref: dict[str, Any],
        vendor_descriptor: dict[str, Any],
        vendor_descriptor_bytes: bytes,
        release_version: str,
        source_root: str) -> tuple[dict[str, Any], bytes]:
    records = [
        runtime_verifier.file_record(path, merged[path][0], merged[path][1])
        for path in sorted(merged)
    ]
    elf_identities = {
        (
            record["payload"]["class"],
            record["payload"]["endian"],
            record["payload"]["machine"],
        )
        for record in records if record["payload"]["kind"] == "elf"
    }
    if len(elf_identities) != 1:
        raise RuntimeBuildError(
            "fresh components do not share one portable ELF target")
    elf_class, endian, machine = next(iter(elf_identities))
    target = {
        "os": "linux",
        "elf_class": elf_class,
        "endian": endian,
        "machine": machine,
    }
    root = f"heptatrader-runtime-{release_version}-linux-{machine}"
    runtime_verifier.canonical_relative(root, "runtime package root")
    if source_ref["root"] != source_root:
        raise RuntimeBuildError("runtime source root binding drift")
    manifest = {
        "schema": runtime_verifier.SCHEMA,
        "package_class": runtime_verifier.PACKAGE_CLASS,
        "release_version": release_version,
        "root": root,
        "source_ref": source_ref,
        "vendor_ref": {
            "schema": VENDOR_SCHEMA,
            "descriptor_sha256":
                "sha256:" + runtime_verifier.sha256(vendor_descriptor_bytes),
            "release_version": vendor_descriptor["release_version"],
            "overlay_count": vendor_descriptor["overlay_count"],
            "required_overlay_ids": [],
        },
        "target": target,
        "boundary": runtime_verifier.BOUNDARY,
        "file_count": runtime_verifier.PRODUCT_FILE_COUNT,
        "files_sha256":
            "sha256:" + runtime_verifier.sha256(
                runtime_verifier.canonical_json(records)),
        "files": records,
    }
    runtime_verifier.validate_manifest(manifest)
    manifest_bytes = runtime_verifier.canonical_json(manifest) + b"\n"
    return manifest, manifest_bytes


def _tar_bytes(
        manifest: dict[str, Any],
        manifest_bytes: bytes,
        merged: dict[str, tuple[int, bytes]]) -> bytes:
    payloads: dict[str, tuple[int, bytes]] = {
        f"{manifest['root']}/{path}": value
        for path, value in merged.items()
    }
    payloads[
        f"{manifest['root']}/{runtime_verifier.INTERNAL_MANIFEST}"
    ] = (0o644, manifest_bytes)
    output = io.BytesIO()
    with tarfile.open(
            fileobj=output, mode="w:",
            format=tarfile.USTAR_FORMAT) as archive:
        for name in sorted(payloads):
            mode, data = payloads[name]
            info = tarfile.TarInfo(name)
            info.type = tarfile.REGTYPE
            info.mode = mode
            info.uid = 0
            info.gid = 0
            info.uname = "root"
            info.gname = "root"
            info.mtime = 0
            info.size = len(data)
            info.linkname = ""
            info.devmajor = 0
            info.devminor = 0
            info.pax_headers = {}
            archive.addfile(info, io.BytesIO(data))
    return output.getvalue()


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


def _validate_output_parent(
        descriptor: int, label: str) -> os.stat_result:
    parent = os.fstat(descriptor)
    if (not stat.S_ISDIR(parent.st_mode) or
            parent.st_uid != os.geteuid() or
            stat.S_IMODE(parent.st_mode) & 0o022):
        raise RuntimeBuildError(
            f"{label} output parent must be owned and not "
            "group/world-writable")
    return parent


@dataclass
class _OutputAnchor:
    absolute: pathlib.Path
    parent: int
    name: str
    ancestors: tuple[tuple[int, ...], ...]
    label: str


@dataclass
class _Publication:
    anchor: _OutputAnchor
    identity: tuple[int, ...]
    digest: str
    size: int


def _open_output_anchor(
        path: pathlib.Path, label: str) -> _OutputAnchor:
    absolute = pathlib.Path(os.path.abspath(path))
    if absolute.name in {"", ".", ".."}:
        raise RuntimeBuildError(f"{label} output filename is unsafe")
    descriptor = -1
    try:
        descriptor = os.open("/", _directory_flags())
        ancestors = [_directory_identity(os.fstat(descriptor))]
        for component in absolute.parent.parts[1:]:
            before = os.stat(
                component, dir_fd=descriptor, follow_symlinks=False)
            if not stat.S_ISDIR(before.st_mode):
                raise RuntimeBuildError(
                    f"{label} output parent path is unsafe")
            child = os.open(
                component, _directory_flags(), dir_fd=descriptor)
            opened = os.fstat(child)
            if _directory_identity(before) != _directory_identity(opened):
                os.close(child)
                raise RuntimeBuildError(
                    f"{label} output parent path is unsafe")
            os.close(descriptor)
            descriptor = child
            ancestors.append(_directory_identity(opened))
        _validate_output_parent(descriptor, label)
        return _OutputAnchor(
            absolute=absolute, parent=descriptor, name=absolute.name,
            ancestors=tuple(ancestors), label=label)
    except OSError as error:
        if descriptor >= 0:
            os.close(descriptor)
        raise RuntimeBuildError(
            f"{label} output parent path is unavailable or unsafe") from error
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


def _read_descriptor(descriptor: int, size: int, label: str) -> bytes:
    os.lseek(descriptor, 0, os.SEEK_SET)
    chunks: list[bytes] = []
    remaining = size
    while remaining:
        chunk = os.read(descriptor, min(1024 * 1024, remaining))
        if not chunk:
            raise RuntimeBuildError(f"{label} publication was truncated")
        chunks.append(chunk)
        remaining -= len(chunk)
    if os.read(descriptor, 1):
        raise RuntimeBuildError(f"{label} publication grew unexpectedly")
    return b"".join(chunks)


def _validate_published_metadata(
        metadata: os.stat_result, size: int, label: str,
        *, links: int = 1) -> None:
    if (not stat.S_ISREG(metadata.st_mode) or
            stat.S_IMODE(metadata.st_mode) != 0o600 or
            metadata.st_uid != os.geteuid() or
            metadata.st_nlink != links or
            metadata.st_size != size):
        raise RuntimeBuildError(
            f"{label} publication identity or mode is unsafe")


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
    """Re-traverse the requested absolute path without following any link."""
    anchor = publication.anchor
    descriptor = -1
    file_descriptor = -1
    try:
        descriptor = os.open("/", _directory_flags())
        if _directory_identity(os.fstat(descriptor)) != anchor.ancestors[0]:
            raise RuntimeBuildError(
                f"{anchor.label} output root identity changed")
        for index, component in enumerate(anchor.absolute.parent.parts[1:], 1):
            before = os.stat(
                component, dir_fd=descriptor, follow_symlinks=False)
            if (not stat.S_ISDIR(before.st_mode) or
                    _directory_identity(before) != anchor.ancestors[index]):
                raise RuntimeBuildError(
                    f"{anchor.label} output ancestor identity changed")
            child = os.open(
                component, _directory_flags(), dir_fd=descriptor)
            opened = os.fstat(child)
            if _directory_identity(opened) != anchor.ancestors[index]:
                os.close(child)
                raise RuntimeBuildError(
                    f"{anchor.label} output ancestor changed while opening")
            os.close(descriptor)
            descriptor = child
        _validate_output_parent(descriptor, anchor.label)
        before = os.stat(
            anchor.name, dir_fd=descriptor, follow_symlinks=False)
        file_descriptor = os.open(
            anchor.name,
            os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) |
            getattr(os, "O_NOFOLLOW", 0),
            dir_fd=descriptor)
        opened = os.fstat(file_descriptor)
        _validate_published_metadata(
            opened, publication.size, anchor.label)
        if (_publication_identity(before) != publication.identity or
                _publication_identity(opened) != publication.identity):
            raise RuntimeBuildError(
                f"{anchor.label} requested path does not name published inode")
        observed = _read_descriptor(
            file_descriptor, publication.size, anchor.label)
        after_descriptor = os.fstat(file_descriptor)
        after_path = os.stat(
            anchor.name, dir_fd=descriptor, follow_symlinks=False)
        if (_publication_identity(after_descriptor) != publication.identity or
                _publication_identity(after_path) != publication.identity or
                runtime_verifier.sha256(observed) != publication.digest):
            raise RuntimeBuildError(
                f"{anchor.label} requested publication changed while reading")
    except OSError as error:
        raise RuntimeBuildError(
            f"{anchor.label} requested output path is unavailable or unsafe"
        ) from error
    finally:
        if file_descriptor >= 0:
            os.close(file_descriptor)
        if descriptor >= 0:
            os.close(descriptor)


def _rollback_publication(publication: _Publication) -> None:
    anchor = publication.anchor
    _unlink_exact_inode(
        anchor.parent, anchor.name,
        (publication.identity[0], publication.identity[1]))
    os.fsync(anchor.parent)


def _close_publication(publication: _Publication) -> None:
    os.close(publication.anchor.parent)


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


def _publish_new_private(
        path: pathlib.Path, data: bytes, label: str) -> _Publication:
    """Publish without overwrite and retain the parent dirfd for rollback."""
    anchor = _open_output_anchor(path, label)
    temporary = (
        f".{anchor.name}.{os.getpid()}.{secrets.token_hex(12)}.tmp")
    descriptor = -1
    temp_inode: tuple[int, int] | None = None
    linked = False
    try:
        descriptor = os.open(
            temporary,
            os.O_RDWR | os.O_CREAT | os.O_EXCL |
            getattr(os, "O_CLOEXEC", 0) |
            getattr(os, "O_NOFOLLOW", 0),
            0o600, dir_fd=anchor.parent)
        view = memoryview(data)
        while view:
            written = os.write(descriptor, view)
            if written <= 0:
                raise RuntimeBuildError(f"failed to publish {label}")
            view = view[written:]
        os.fchmod(descriptor, 0o600)
        os.fsync(descriptor)
        temp_metadata = os.fstat(descriptor)
        _validate_published_metadata(temp_metadata, len(data), label)
        temp_inode = (temp_metadata.st_dev, temp_metadata.st_ino)
        observed = _read_descriptor(descriptor, len(data), label)
        digest = runtime_verifier.sha256(data)
        if runtime_verifier.sha256(observed) != digest:
            raise RuntimeBuildError(f"{label} temp digest drift")
        temp_path_metadata = os.stat(
            temporary, dir_fd=anchor.parent, follow_symlinks=False)
        if (_publication_identity(temp_metadata) !=
                _publication_identity(temp_path_metadata)):
            raise RuntimeBuildError(f"{label} temp inode changed before rename")
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
            raise RuntimeBuildError(
                f"{label} output already exists") from error
        linked = True
        after_link = os.fstat(descriptor)
        linked_path = os.stat(
            anchor.name, dir_fd=anchor.parent, follow_symlinks=False)
        _validate_published_metadata(
            after_link, len(data), label, links=2)
        if _publication_identity(after_link) != _publication_identity(
                linked_path):
            raise RuntimeBuildError(
                f"{label} no-overwrite link did not bind the temp inode")
        if not _unlink_exact_inode(
                anchor.parent, temporary, temp_inode):
            raise RuntimeBuildError(
                f"{label} temporary name changed after publication")
        temporary = ""
        published = os.fstat(descriptor)
        final_path = os.stat(
            anchor.name, dir_fd=anchor.parent, follow_symlinks=False)
        _validate_published_metadata(published, len(data), label)
        if _publication_identity(published) != _publication_identity(
                final_path):
            raise RuntimeBuildError(
                f"{label} published path is not the temp inode")
        os.fsync(anchor.parent)
        if (_directory_identity(_validate_output_parent(
                anchor.parent, label)) != anchor.ancestors[-1]):
            raise RuntimeBuildError(
                f"{label} anchored output parent identity changed")
        publication = _Publication(
            anchor=anchor, identity=_publication_identity(published),
            digest=digest, size=len(data))
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
            raise RuntimeBuildError(
                f"{label} exact-inode rollback failed: {cleanup_error}"
            ) from error
        if isinstance(error, OSError):
            raise RuntimeBuildError(
                f"cannot publish {label}: {error}") from error
        raise
    finally:
        if descriptor >= 0:
            os.close(descriptor)


def _write_new_private(path: pathlib.Path, data: bytes, label: str) -> None:
    publication = _publish_new_private(path, data, label)
    _close_publication(publication)


def _publish_runtime_outputs(
        output_package: pathlib.Path, package_bytes: bytes,
        output_manifest: pathlib.Path, manifest_bytes: bytes,
) -> dict[str, Any]:
    publications: list[_Publication] = []
    try:
        publications.append(_publish_new_private(
            output_package, package_bytes, "runtime package"))
        publications.append(_publish_new_private(
            output_manifest, manifest_bytes, "runtime manifest"))
        for publication in publications:
            _verify_requested_publication(publication)
        verification = runtime_verifier.verify_package(
            output_package, output_manifest)
        for publication in publications:
            _verify_requested_publication(publication)
        return verification
    except BaseException as error:
        rollback_errors: list[OSError] = []
        for publication in reversed(publications):
            try:
                _rollback_publication(publication)
            except OSError as rollback_error:
                rollback_errors.append(rollback_error)
        if rollback_errors:
            raise RuntimeBuildError(
                "runtime output exact-inode rollback failed: " +
                "; ".join(str(item) for item in rollback_errors)
            ) from error
        raise
    finally:
        for publication in publications:
            _close_publication(publication)


def package_staged_components(
        agent_root: pathlib.Path,
        execution_root: pathlib.Path,
        *,
        source_ref: dict[str, Any],
        source_manifest: dict[str, Any],
        vendor_descriptor: dict[str, Any],
        vendor_descriptor_bytes: bytes) -> tuple[bytes, bytes, dict[str, Any]]:
    """Fixture seam after fresh build/install; not exposed by the CLI."""
    decoded_vendor = runtime_verifier.strict_json(
        vendor_descriptor_bytes, "vendor overlay-set descriptor")
    expected_vendor_bytes = (
        json.dumps(
            vendor_descriptor, ensure_ascii=True, indent=2,
            sort_keys=True, allow_nan=False) + "\n").encode("utf-8")
    if (decoded_vendor != vendor_descriptor or
            vendor_descriptor_bytes != expected_vendor_bytes):
        raise RuntimeBuildError(
            "vendor overlay-set bytes are not canonical or do not match")
    validate_vendor_descriptor(
        vendor_descriptor, source_ref=source_ref,
        source_manifest=source_manifest)
    agent = _read_component(
        agent_root, runtime_verifier.AGENT_FILES, "Agent component")
    execution = _read_component(
        execution_root, runtime_verifier.EXECUTION_FILES,
        "Execution component")
    merged = _merge_components(agent, execution)
    manifest, manifest_bytes = _manifest_and_payloads(
        merged, source_ref=source_ref,
        vendor_descriptor=vendor_descriptor,
        vendor_descriptor_bytes=vendor_descriptor_bytes,
        release_version=source_manifest["version"],
        source_root=source_manifest["root"])
    package_bytes = _tar_bytes(manifest, manifest_bytes, merged)
    return package_bytes, manifest_bytes, manifest


def build_runtime_package(
        source_bundle_path: pathlib.Path,
        source_manifest_path: pathlib.Path,
        vendor_descriptor_path: pathlib.Path,
        output_package: pathlib.Path,
        output_manifest: pathlib.Path,
        *,
        cmake: str = "cmake",
        jobs: int = 2) -> dict[str, Any]:
    if not isinstance(jobs, int) or isinstance(jobs, bool) or not 1 <= jobs <= 64:
        raise RuntimeBuildError("parallel job count must be between 1 and 64")
    source_bundle_bytes = runtime_verifier.stable_private_bytes(
        source_bundle_path, "strict-source bundle",
        source_verifier.MAX_BUNDLE_BYTES)
    source_manifest_bytes = runtime_verifier.stable_private_bytes(
        source_manifest_path, "strict-source manifest",
        source_verifier.MAX_MANIFEST_BYTES)
    vendor_descriptor_bytes = runtime_verifier.stable_private_bytes(
        vendor_descriptor_path, "vendor overlay-set descriptor",
        MAX_VENDOR_DESCRIPTOR_BYTES)
    source_manifest_value = runtime_verifier.strict_json(
        source_manifest_bytes, "strict-source manifest")
    if not isinstance(source_manifest_value, dict):
        raise RuntimeBuildError("strict-source manifest must be an object")
    source_manifest = source_manifest_value
    vendor_value = runtime_verifier.strict_json(
        vendor_descriptor_bytes, "vendor overlay-set descriptor")

    with tempfile.TemporaryDirectory(
            prefix="heptatrader-runtime-package-") as directory:
        temporary = pathlib.Path(directory)
        captured_bundle = temporary / "strict-source-bundle.tar"
        captured_manifest = temporary / "strict-source-manifest.json"
        captured_vendor = temporary / "vendor-overlay-set.json"
        _write_private_copy(captured_bundle, source_bundle_bytes)
        _write_private_copy(captured_manifest, source_manifest_bytes)
        _write_private_copy(captured_vendor, vendor_descriptor_bytes)
        try:
            source_result = source_verifier.verify_bundle(
                captured_bundle, captured_manifest)
        except SystemExit as error:
            raise RuntimeBuildError(
                "captured strict-source bundle verification failed") from error
        source_ref = _source_ref(
            source_manifest, source_result,
            source_bundle_bytes, source_manifest_bytes)
        vendor_descriptor = validate_vendor_descriptor(
            vendor_value, source_ref=source_ref,
            source_manifest=source_manifest)
        try:
            vendor_report = vendor_set_verifier.verify_vendor_overlay_set(
                captured_bundle, captured_manifest, captured_vendor)
        except vendor_set_verifier.VendorOverlaySetVerificationError as error:
            raise RuntimeBuildError(
                "captured vendor overlay-set verification failed") from error
        if (vendor_report.get("overlay_set_sha256") !=
                runtime_verifier.sha256(vendor_descriptor_bytes) or
                vendor_report.get("source_ref") !=
                {key: source_ref[key] for key in VENDOR_SOURCE_FIELDS} or
                vendor_report.get("overlay_count") != 3 or
                vendor_report.get("release_version") !=
                source_manifest["version"]):
            raise RuntimeBuildError(
                "vendor overlay-set verifier crossed the captured lineage")
        extracted = temporary / "source"
        extracted.mkdir(mode=0o700)
        source_root = _safe_extract_source(
            source_bundle_bytes, source_manifest_bytes,
            source_manifest, extracted)
        agent_root, execution_root = _run_fresh_build(
            source_root, temporary, cmake=cmake, jobs=jobs)
        _verify_stage_semantics(
            source_root, agent_root, execution_root,
            environment=_minimal_build_environment(temporary))
        package_bytes, manifest_bytes, manifest = package_staged_components(
            agent_root, execution_root, source_ref=source_ref,
            source_manifest=source_manifest,
            vendor_descriptor=vendor_descriptor,
            vendor_descriptor_bytes=vendor_descriptor_bytes)
    output_package = pathlib.Path(output_package)
    output_manifest = pathlib.Path(output_manifest)
    if os.path.abspath(output_package) == os.path.abspath(output_manifest):
        raise RuntimeBuildError(
            "runtime package and manifest outputs must be distinct")
    verification = _publish_runtime_outputs(
        output_package, package_bytes, output_manifest, manifest_bytes)
    return {
        "schema": runtime_verifier.SCHEMA,
        "release_version": manifest["release_version"],
        "file_count": runtime_verifier.PRODUCT_FILE_COUNT,
        "package_sha256": verification["package_sha256"],
        "manifest_sha256": verification["manifest_sha256"],
        "source_ref": manifest["source_ref"],
        "vendor_ref": manifest["vendor_ref"],
        "target": manifest["target"],
        "boundary": manifest["boundary"],
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--source-bundle", required=True, type=pathlib.Path)
    parser.add_argument(
        "--source-manifest", required=True, type=pathlib.Path)
    parser.add_argument(
        "--vendor-overlay-set", required=True, type=pathlib.Path)
    parser.add_argument(
        "--output-package", required=True, type=pathlib.Path)
    parser.add_argument(
        "--output-manifest", required=True, type=pathlib.Path)
    parser.add_argument("--cmake", default="cmake")
    parser.add_argument("--jobs", type=int, default=2)
    args = parser.parse_args()
    result = build_runtime_package(
        args.source_bundle, args.source_manifest,
        args.vendor_overlay_set, args.output_package,
        args.output_manifest, cmake=args.cmake, jobs=args.jobs)
    print(
        "PASS: built hepta.runtime-package.v1 "
        f"release={result['release_version']} files={result['file_count']} "
        f"package_sha256={result['package_sha256']}")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (
            RuntimeBuildError,
            runtime_verifier.RuntimePackageError) as error:
        print(f"FAIL: {error}", file=os.sys.stderr)
        raise SystemExit(1)
