#!/usr/bin/env python3
"""Materialize and attest one fail-closed Round38 same-host recovery."""

from __future__ import annotations

import argparse
import ctypes
from datetime import datetime, timezone
import hashlib
import os
from pathlib import Path
import re
import stat
import subprocess
import sys
import uuid
from typing import Any, Callable


SCRIPT_DIRECTORY = Path(__file__).resolve(strict=True).parent
sys.path.insert(0, str(SCRIPT_DIRECTORY))

import build_heptatrader_delivery_closure as common  # noqa: E402
import build_heptatrader_engineering_closure as closure  # noqa: E402


SCHEMA = "hepta.round38-recovery-materialization-receipt.v1"
VERSION = 1
TOOL_SOURCE = "scripts/verify_heptatrader_recovery_materialization.py"
ENGINEERING_CLOSURE_SOURCE = (
    "scripts/build_heptatrader_engineering_closure.py")
DELIVERY_CLOSURE_SOURCE = "scripts/build_heptatrader_delivery_closure.py"
DEFAULT_RECEIPT_NAME = "round38-recovery-materialization-receipt.json"
HEX64 = re.compile(r"^[0-9a-f]{64}$")
RECEIPT_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,126}\.json$")
MAX_MANIFEST_BYTES = 16 * 1024 * 1024
MAX_RECEIPT_BYTES = 256 * 1024
GIT_ENVIRONMENT = dict(closure.GIT_ENVIRONMENT)
FORBIDDEN_GIT_ENVIRONMENT = (
    "GIT_ALTERNATE_OBJECT_DIRECTORIES",
    "GIT_COMMON_DIR",
    "GIT_DIR",
    "GIT_OBJECT_DIRECTORY",
    "GIT_WORK_TREE",
)


class RecoveryMaterializationError(RuntimeError):
    """The recovery inputs or materialized result are unsafe or incomplete."""


def _rename_directory_noreplace(
    parent_descriptor: int,
    source_name: str,
    target_name: str,
) -> None:
    try:
        library = ctypes.CDLL(None, use_errno=True)
        renameat2 = library.renameat2
    except (OSError, AttributeError) as error:
        raise RecoveryMaterializationError(
            "renameat2 is unavailable for atomic root publication") from error
    renameat2.argtypes = (
        ctypes.c_int,
        ctypes.c_char_p,
        ctypes.c_int,
        ctypes.c_char_p,
        ctypes.c_uint,
    )
    renameat2.restype = ctypes.c_int
    result = renameat2(
        parent_descriptor,
        os.fsencode(source_name),
        parent_descriptor,
        os.fsencode(target_name),
        1,  # RENAME_NOREPLACE
    )
    if result != 0:
        error_number = ctypes.get_errno()
        raise OSError(
            error_number,
            os.strerror(error_number),
            target_name,
        )


def _absolute(path: Path, label: str) -> Path:
    raw = os.fspath(path)
    if not raw or "\0" in raw:
        raise RecoveryMaterializationError(f"{label} path is invalid")
    return Path(os.path.abspath(raw))


def _snapshot(
    path: Path,
    *,
    label: str,
    limit: int,
    capture: bool,
) -> common.StableRead:
    try:
        return common.stable_read(
            path,
            limit=limit,
            capture=capture,
            require_trusted_parent=True,
        )
    except common.DeliveryClosureError as error:
        raise RecoveryMaterializationError(
            f"{label} failed stable read: {error}") from error


def _document(snapshot: common.StableRead, label: str) -> dict[str, Any]:
    assert snapshot.data is not None
    try:
        return closure._strict_document(snapshot.data, label)
    except closure.EngineeringClosureError as error:
        raise RecoveryMaterializationError(str(error)) from error


def _expected_digest(value: str, label: str) -> str:
    if not isinstance(value, str) or HEX64.fullmatch(value) is None:
        raise RecoveryMaterializationError(
            f"{label} expected SHA-256 is invalid")
    return value


def _input_bindings(
    bundle: Path,
    ref_manifest: Path,
    delta_manifest: Path,
    delta_payload: Path,
    expected_sha256: dict[str, str],
) -> tuple[
        dict[str, tuple[Path, common.StableRead]],
        dict[str, Any],
        dict[str, Any],
]:
    paths = {
        "rescue-bundle": (
            _absolute(bundle, "rescue bundle"),
            closure.MAX_ARTIFACT_BYTES,
            False,
        ),
        "rescue-ref-manifest": (
            _absolute(ref_manifest, "rescue ref manifest"),
            MAX_MANIFEST_BYTES,
            True,
        ),
        "rescue-delta-manifest": (
            _absolute(delta_manifest, "rescue delta manifest"),
            MAX_MANIFEST_BYTES,
            True,
        ),
        "rescue-delta-payload": (
            _absolute(delta_payload, "rescue delta payload"),
            256 * 1024 * 1024,
            False,
        ),
    }
    if set(expected_sha256) != set(paths):
        raise RecoveryMaterializationError(
            "recovery expected checksum closure is incomplete")
    bindings: dict[str, tuple[Path, common.StableRead]] = {}
    for role, (path, limit, capture) in paths.items():
        expected = _expected_digest(expected_sha256[role], role)
        snapshot = _snapshot(
            path, label=role, limit=limit, capture=capture)
        if snapshot.sha256 != expected:
            raise RecoveryMaterializationError(
                f"{role} differs from its independent expected checksum")
        bindings[role] = (path, snapshot)
    ref_document = _document(
        bindings["rescue-ref-manifest"][1],
        "rescue ref manifest")
    delta_document = _document(
        bindings["rescue-delta-manifest"][1],
        "rescue delta manifest")
    if ref_document.get("bundle_sha256") != expected_sha256["rescue-bundle"]:
        raise RecoveryMaterializationError(
            "rescue ref manifest and independent bundle checksum differ")
    return bindings, ref_document, delta_document


def _run_git(
    arguments: list[str],
    *,
    label: str,
    text: bool = False,
    timeout: int = 120,
) -> bytes | str:
    try:
        run = subprocess.run(
            ["git", *arguments],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            env=GIT_ENVIRONMENT,
            timeout=timeout,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        raise RecoveryMaterializationError(f"{label} failed") from error
    if run.returncode != 0:
        raise RecoveryMaterializationError(f"{label} failed")
    if not text:
        return run.stdout
    try:
        return run.stdout.decode("ascii", errors="strict").strip()
    except UnicodeDecodeError as error:
        raise RecoveryMaterializationError(
            f"{label} output is not ASCII") from error


def _reject_git_environment_redirection() -> None:
    for name in FORBIDDEN_GIT_ENVIRONMENT:
        if os.environ.get(name):
            raise RecoveryMaterializationError(
                f"unsafe Git environment redirection is set: {name}")


def _create_private_root(
    path: Path,
) -> tuple[Path, dict[str, int | str]]:
    root = _absolute(path, "materialization root")
    parent = root.parent
    try:
        if parent.resolve(strict=True) != parent:
            raise RecoveryMaterializationError(
                "materialization parent contains a symlink")
        parent_metadata = os.lstat(parent)
        common._validate_trusted_directory(
            parent_metadata,
            label="materialization parent",
            expected_owner=os.geteuid(),
        )
    except (FileNotFoundError, common.DeliveryClosureError) as error:
        raise RecoveryMaterializationError(
            f"materialization parent is unsafe: {error}") from error
    parent_descriptor = os.open(
        parent,
        os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC |
        getattr(os, "O_NOFOLLOW", 0),
    )
    staging = (
        f".{root.name}.create-{os.getpid()}-{uuid.uuid4().hex}")
    root_descriptor = -1
    staging_exists = False
    published = False
    created_identity: dict[str, int | str] | None = None
    try:
        opened_parent = os.fstat(parent_descriptor)
        if (opened_parent.st_dev != parent_metadata.st_dev or
                opened_parent.st_ino != parent_metadata.st_ino or
                not stat.S_ISDIR(opened_parent.st_mode)):
            raise RecoveryMaterializationError(
                "materialization parent identity drift")
        try:
            os.stat(
                root.name,
                dir_fd=parent_descriptor,
                follow_symlinks=False,
            )
        except FileNotFoundError:
            pass
        else:
            raise RecoveryMaterializationError(
                "materialization root must be a new empty path")
        os.mkdir(staging, mode=0o700, dir_fd=parent_descriptor)
        staging_exists = True
        root_descriptor = os.open(
            staging,
            os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC |
            getattr(os, "O_NOFOLLOW", 0),
            dir_fd=parent_descriptor,
        )
        os.fchmod(root_descriptor, 0o700)
        created = os.fstat(root_descriptor)
        if (not stat.S_ISDIR(created.st_mode) or
                created.st_uid != os.geteuid() or
                stat.S_IMODE(created.st_mode) != 0o700):
            raise RecoveryMaterializationError(
                "materialization root is not private")
        staging_metadata = os.stat(
            staging,
            dir_fd=parent_descriptor,
            follow_symlinks=False,
        )
        if (_root_identity(staging_metadata) != _root_identity(created) or
                not stat.S_ISDIR(staging_metadata.st_mode)):
            raise RecoveryMaterializationError(
                "materialization staging root identity drift")
        created_identity = _root_identity(created)
        os.fsync(root_descriptor)
        _rename_directory_noreplace(
            parent_descriptor,
            staging,
            root.name,
        )
        staging_exists = False
        published = True
        published_metadata = os.stat(
            root.name,
            dir_fd=parent_descriptor,
            follow_symlinks=False,
        )
        if (_root_identity(published_metadata) != created_identity or
                _root_identity(os.fstat(root_descriptor)) !=
                created_identity):
            raise RecoveryMaterializationError(
                "materialization root identity drift during publication")
        os.fsync(parent_descriptor)
    except (OSError, RecoveryMaterializationError) as error:
        raise RecoveryMaterializationError(
            "failed to create private materialization root") from error
    finally:
        if root_descriptor >= 0:
            os.close(root_descriptor)
        if staging_exists:
            try:
                candidate = os.stat(
                    staging,
                    dir_fd=parent_descriptor,
                    follow_symlinks=False,
                )
                if (created_identity is not None and
                        _root_identity(candidate) == created_identity):
                    os.rmdir(staging, dir_fd=parent_descriptor)
                    os.fsync(parent_descriptor)
            except OSError:
                pass
        os.close(parent_descriptor)
    if not published or created_identity is None:
        raise RecoveryMaterializationError(
            "failed to publish private materialization root")
    metadata = os.lstat(root)
    if (not stat.S_ISDIR(metadata.st_mode) or
            metadata.st_uid != os.geteuid() or
            stat.S_IMODE(metadata.st_mode) != 0o700 or
            _root_identity(metadata) != created_identity):
        raise RecoveryMaterializationError(
            "materialization root is not private")
    return root, created_identity


def _metadata_identity(value: os.stat_result) -> tuple[int, ...]:
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


def _scan_mirror_directory(
    descriptor: int,
    relative_directory: str,
    records: list[dict[str, Any]],
) -> None:
    with os.scandir(descriptor) as iterator:
        entries = sorted(iterator, key=lambda item: item.name)
    for entry in entries:
        relative = (
            entry.name if not relative_directory else
            f"{relative_directory}/{entry.name}")
        before = os.stat(
            entry.name, dir_fd=descriptor, follow_symlinks=False)
        mode = stat.S_IMODE(before.st_mode)
        if (not stat.S_ISREG(before.st_mode) and
                not stat.S_ISDIR(before.st_mode)):
            raise RecoveryMaterializationError(
                "materialized Git mirror contains a special file")
        if before.st_uid != os.geteuid() or mode & 0o022:
            raise RecoveryMaterializationError(
                "materialized Git mirror metadata is unsafe")
        if stat.S_ISREG(before.st_mode):
            if before.st_nlink != 1:
                raise RecoveryMaterializationError(
                    "materialized Git mirror contains a hardlink")
            child = os.open(
                entry.name,
                os.O_RDONLY | os.O_CLOEXEC |
                getattr(os, "O_NOFOLLOW", 0),
                dir_fd=descriptor,
            )
            try:
                if _metadata_identity(os.fstat(child)) != \
                        _metadata_identity(before):
                    raise RecoveryMaterializationError(
                        "materialized Git mirror file identity drift")
            finally:
                os.close(child)
            after = os.stat(
                entry.name, dir_fd=descriptor, follow_symlinks=False)
            if _metadata_identity(after) != _metadata_identity(before):
                raise RecoveryMaterializationError(
                    "materialized Git mirror file identity drift")
            records.append({
                "ctime_ns": before.st_ctime_ns,
                "device": before.st_dev,
                "gid": before.st_gid,
                "inode": before.st_ino,
                "kind": "file",
                "mode": f"{mode:04o}",
                "mtime_ns": before.st_mtime_ns,
                "nlink": before.st_nlink,
                "path": relative,
                "size": before.st_size,
                "uid": before.st_uid,
            })
            continue
        child = os.open(
            entry.name,
            os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC |
            getattr(os, "O_NOFOLLOW", 0),
            dir_fd=descriptor,
        )
        try:
            if _metadata_identity(os.fstat(child)) != \
                    _metadata_identity(before):
                raise RecoveryMaterializationError(
                    "materialized Git mirror directory identity drift")
            records.append({
                "ctime_ns": before.st_ctime_ns,
                "device": before.st_dev,
                "gid": before.st_gid,
                "inode": before.st_ino,
                "kind": "directory",
                "mode": f"{mode:04o}",
                "mtime_ns": before.st_mtime_ns,
                "nlink": before.st_nlink,
                "path": relative,
                "size": before.st_size,
                "uid": before.st_uid,
            })
            _scan_mirror_directory(child, relative, records)
        finally:
            os.close(child)
        after = os.stat(
            entry.name, dir_fd=descriptor, follow_symlinks=False)
        if _metadata_identity(after) != _metadata_identity(before):
            raise RecoveryMaterializationError(
                "materialized Git mirror directory identity drift")


def _harden_new_mirror_directory(descriptor: int) -> None:
    with os.scandir(descriptor) as iterator:
        entries = list(iterator)
    for entry in entries:
        before = os.stat(
            entry.name, dir_fd=descriptor, follow_symlinks=False)
        if before.st_uid != os.geteuid():
            raise RecoveryMaterializationError(
                "new Git mirror has a foreign owner")
        if stat.S_ISREG(before.st_mode):
            if before.st_nlink != 1:
                raise RecoveryMaterializationError(
                    "new Git mirror contains a hardlink")
            child = os.open(
                entry.name,
                os.O_RDONLY | os.O_CLOEXEC |
                getattr(os, "O_NOFOLLOW", 0),
                dir_fd=descriptor,
            )
            try:
                os.fchmod(child, stat.S_IMODE(before.st_mode) & ~0o022)
            finally:
                os.close(child)
            continue
        if not stat.S_ISDIR(before.st_mode):
            raise RecoveryMaterializationError(
                "new Git mirror contains a symlink or special file")
        child = os.open(
            entry.name,
            os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC |
            getattr(os, "O_NOFOLLOW", 0),
            dir_fd=descriptor,
        )
        try:
            os.fchmod(child, stat.S_IMODE(before.st_mode) & ~0o022)
            _harden_new_mirror_directory(child)
        finally:
            os.close(child)


def _harden_new_mirror(repository: Path) -> None:
    descriptor = os.open(
        repository,
        os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC |
        getattr(os, "O_NOFOLLOW", 0),
    )
    try:
        _harden_new_mirror_directory(descriptor)
    finally:
        os.close(descriptor)


def _scan_materialized_mirror(repository: Path) -> dict[str, Any]:
    before = os.lstat(repository)
    if (not stat.S_ISDIR(before.st_mode) or
            before.st_uid != os.geteuid() or
            stat.S_IMODE(before.st_mode) != 0o700):
        raise RecoveryMaterializationError(
            "materialized repository is not private")
    descriptor = os.open(
        repository,
        os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC |
        getattr(os, "O_NOFOLLOW", 0),
    )
    records: list[dict[str, Any]] = []
    try:
        if _metadata_identity(os.fstat(descriptor)) != \
                _metadata_identity(before):
            raise RecoveryMaterializationError(
                "materialized Git mirror root identity drift")
        _scan_mirror_directory(descriptor, "", records)
        opened = os.fstat(descriptor)
    finally:
        os.close(descriptor)
    after = os.lstat(repository)
    if (_metadata_identity(opened) != _metadata_identity(before) or
            _metadata_identity(after) != _metadata_identity(before)):
        raise RecoveryMaterializationError(
            "materialized Git mirror root identity drift")
    records.sort(key=lambda record: record["path"])
    return {
        "entry_count": len(records),
        "metadata_sha256": hashlib.sha256(
            closure.canonical_json(records)).hexdigest(),
        "root_device": before.st_dev,
        "root_inode": before.st_ino,
    }


def _restored_refs(repository: Path) -> list[dict[str, str]]:
    output = _run_git(
        ["-C", str(repository), "show-ref"],
        label="materialized Git ref query",
        text=True,
    )
    assert isinstance(output, str)
    refs: list[dict[str, str]] = []
    seen: set[str] = set()
    for line in output.splitlines():
        fields = line.split(" ", 1)
        if (len(fields) != 2 or
                closure.HEX40.fullmatch(fields[0]) is None or
                not fields[1].startswith("refs/") or
                fields[1] in seen or
                any(character.isspace() for character in fields[1])):
            raise RecoveryMaterializationError(
                "materialized Git ref set is invalid")
        refs.append({"object": fields[0], "name": fields[1]})
        seen.add(fields[1])
    refs.sort(key=lambda record: record["name"])
    return refs


def _verify_materialized_repository(
    repository: Path,
    ref_manifest: dict[str, Any],
) -> dict[str, Any]:
    mirror_before = _scan_materialized_mirror(repository)
    for name in ("alternates", "http-alternates"):
        if os.path.lexists(repository / "objects" / "info" / name):
            raise RecoveryMaterializationError(
                "materialized repository unexpectedly uses object alternates")
    fsck = _run_git(
        ["-C", str(repository), "fsck", "--full", "--strict",
         "--no-reflogs", "--unreachable"],
        label="materialized strict Git fsck",
    )
    assert isinstance(fsck, bytes)
    if fsck:
        raise RecoveryMaterializationError(
            "materialized strict Git fsck found unreachable objects")
    refs = _restored_refs(repository)
    head = _run_git(
        ["-C", str(repository), "rev-parse", "HEAD"],
        label="materialized Git HEAD query",
        text=True,
    )
    symbolic_head = _run_git(
        ["-C", str(repository), "symbolic-ref", "-q", "HEAD"],
        label="materialized Git symbolic HEAD query",
        text=True,
    )
    assert isinstance(head, str)
    assert isinstance(symbolic_head, str)
    expected_ref = next(
        (record for record in ref_manifest["refs"]
         if record["name"] == closure.ROUND38_REF),
        None,
    )
    if (not isinstance(expected_ref, dict) or
            expected_ref.get("object") != ref_manifest["head"]["object"] or
            refs != ref_manifest["refs"] or
            symbolic_head != expected_ref["name"] or
            head != ref_manifest["head"]["object"]):
        raise RecoveryMaterializationError(
            "materialized Git ref/HEAD closure differs from manifest")
    mirror_after = _scan_materialized_mirror(repository)
    if mirror_after != mirror_before:
        raise RecoveryMaterializationError(
            "materialized Git mirror changed during verification")
    return {
        "fsck_full_strict": True,
        "fsck_output_sha256": hashlib.sha256(fsck).hexdigest(),
        "head": head,
        "head_symbolic_ref": symbolic_head,
        "mirror_entry_count": mirror_after["entry_count"],
        "mirror_metadata_sha256": mirror_after["metadata_sha256"],
        "mirror_root_device": mirror_after["root_device"],
        "mirror_root_inode": mirror_after["root_inode"],
        "ref_count": len(refs),
        "ref_set_sha256": hashlib.sha256(
            closure.canonical_json(refs)).hexdigest(),
    }


def _clone_and_verify_repository(
    bundle: Path,
    root: Path,
    ref_manifest: dict[str, Any],
) -> tuple[Path, dict[str, Any]]:
    repository = root / "repository.git"
    try:
        clone = subprocess.run(
            [
                "git", "clone", "--mirror", "--no-hardlinks", "-q",
                str(bundle), str(repository),
            ],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            env=GIT_ENVIRONMENT,
            timeout=300,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        raise RecoveryMaterializationError(
            "rescue bundle materialization failed") from error
    if clone.returncode != 0:
        raise RecoveryMaterializationError(
            "rescue bundle materialization failed")
    os.chmod(repository, 0o700, follow_symlinks=False)
    _harden_new_mirror(repository)
    return repository, _verify_materialized_repository(
        repository, ref_manifest)


def _bound_source_file(
    repository: Path,
    revision: str,
    relative: str,
    expected_sha256: str,
    label: str,
) -> dict[str, Any]:
    relative = common.normalized_relative_path(relative, f"{label} path")
    listing = _run_git(
        ["-C", str(repository), "ls-tree", "-z", revision, "--", relative],
        label=f"{label} tree binding",
    )
    assert isinstance(listing, bytes)
    try:
        metadata, raw_path = listing.rstrip(b"\0").split(b"\t", 1)
        mode, kind, object_id = metadata.decode("ascii").split(" ")
        tree_path = raw_path.decode("utf-8", errors="strict")
    except (ValueError, UnicodeDecodeError) as error:
        raise RecoveryMaterializationError(
            f"{label} tree binding is invalid") from error
    if (mode not in {"100644", "100755"} or kind != "blob" or
            closure.HEX40.fullmatch(object_id) is None or
            tree_path != relative):
        raise RecoveryMaterializationError(
            f"{label} tree binding is invalid")
    payload = _run_git(
        ["-C", str(repository), "cat-file", "blob", object_id],
        label=f"{label} blob binding",
    )
    assert isinstance(payload, bytes)
    digest = hashlib.sha256(payload).hexdigest()
    if digest != expected_sha256:
        raise RecoveryMaterializationError(
            f"{label} differs from its product source blob")
    return {
        "git_blob_object": object_id,
        "mode": "0755" if mode == "100755" else "0644",
        "path": relative,
        "sha256": digest,
        "size": len(payload),
    }


def _snapshot_running_source(
    path: Path,
    label: str,
) -> common.StableRead:
    before = os.lstat(path)
    mode = stat.S_IMODE(before.st_mode)
    if (not stat.S_ISREG(before.st_mode) or before.st_nlink != 1 or
            before.st_uid != os.geteuid() or
            mode not in {0o600, 0o644, 0o664, 0o755, 0o775} or
            before.st_size > 4 * 1024 * 1024):
        raise RecoveryMaterializationError(
            f"{label} running source metadata is unsafe")
    descriptor = os.open(
        path,
        os.O_RDONLY | os.O_CLOEXEC | getattr(os, "O_NOFOLLOW", 0),
    )
    digest = hashlib.sha256()
    size = 0
    try:
        opened = os.fstat(descriptor)
        if _metadata_identity(opened) != _metadata_identity(before):
            raise RecoveryMaterializationError(
                f"{label} running source identity drift")
        while True:
            chunk = os.read(descriptor, 1024 * 1024)
            if not chunk:
                break
            size += len(chunk)
            if size > 4 * 1024 * 1024:
                raise RecoveryMaterializationError(
                    f"{label} running source is oversized")
            digest.update(chunk)
    finally:
        os.close(descriptor)
    after = os.lstat(path)
    if (_metadata_identity(after) != _metadata_identity(before) or
            size != before.st_size):
        raise RecoveryMaterializationError(
            f"{label} running source changed while reading")
    return common.StableRead(
        identity=_metadata_identity(after),
        size=size,
        sha256=digest.hexdigest(),
        mode=f"{mode:04o}",
        data=None,
    )


def _capture_running_sources(
) -> dict[str, tuple[str, Path, common.StableRead]]:
    definitions = {
        "delivery_closure": (
            DELIVERY_CLOSURE_SOURCE, Path(common.__file__)),
        "engineering_closure": (
            ENGINEERING_CLOSURE_SOURCE, Path(closure.__file__)),
        "materialization_verifier": (
            TOOL_SOURCE, Path(__file__)),
    }
    captured: dict[str, tuple[str, Path, common.StableRead]] = {}
    for role, (relative, loaded) in definitions.items():
        expected = (SCRIPT_DIRECTORY.parent / relative).resolve(strict=True)
        actual = loaded.resolve(strict=True)
        if actual != expected:
            raise RecoveryMaterializationError(
                f"{role} was loaded from an unexpected source path")
        captured[role] = (
            relative,
            actual,
            _snapshot_running_source(actual, role),
        )
    return captured


def _bind_running_sources(
    repository: Path,
    revision: str,
    sources: dict[str, tuple[str, Path, common.StableRead]],
) -> dict[str, dict[str, Any]]:
    return {
        role: _bound_source_file(
            repository, revision, relative, snapshot.sha256, role)
        for role, (relative, _path, snapshot) in sorted(sources.items())
    }


def _assert_running_sources_stable(
    sources: dict[str, tuple[str, Path, common.StableRead]],
) -> None:
    for role, (_relative, path, before) in sources.items():
        after = _snapshot_running_source(path, role)
        if (after.identity != before.identity or
                after.size != before.size or
                after.sha256 != before.sha256 or
                after.mode != before.mode):
            raise RecoveryMaterializationError(
                f"{role} changed during recovery materialization")


def _environment() -> dict[str, Any]:
    git_version = _run_git(
        ["--version"], label="Git version query", text=True)
    assert isinstance(git_version, str)
    system = os.uname()
    return {
        "effective_gid": os.getegid(),
        "effective_uid": os.geteuid(),
        "git_version": git_version,
        "kernel_machine": system.machine,
        "kernel_release": system.release,
        "kernel_sysname": system.sysname,
        "python_implementation": sys.implementation.name,
        "python_version": ".".join(str(part) for part in sys.version_info[:3]),
    }


def _assert_inputs_stable(
    bindings: dict[str, tuple[Path, common.StableRead]],
) -> None:
    limits = {
        "rescue-bundle": closure.MAX_ARTIFACT_BYTES,
        "rescue-ref-manifest": MAX_MANIFEST_BYTES,
        "rescue-delta-manifest": MAX_MANIFEST_BYTES,
        "rescue-delta-payload": 256 * 1024 * 1024,
    }
    for role, (path, before) in bindings.items():
        after = _snapshot(
            path, label=role, limit=limits[role], capture=False)
        if (after.identity != before.identity or
                after.size != before.size or
                after.sha256 != before.sha256 or
                after.mode != before.mode):
            raise RecoveryMaterializationError(
                f"{role} changed during recovery materialization")


def _root_identity(value: os.stat_result) -> dict[str, int | str]:
    return {
        "device": value.st_dev,
        "gid": value.st_gid,
        "inode": value.st_ino,
        "mode": f"{stat.S_IMODE(value.st_mode):04o}",
        "uid": value.st_uid,
    }


def _assert_private_root_identity(
    root: Path,
    expected_identity: dict[str, int | str],
) -> None:
    before = os.lstat(root)
    if (not stat.S_ISDIR(before.st_mode) or
            before.st_uid != os.geteuid() or
            stat.S_IMODE(before.st_mode) != 0o700 or
            _root_identity(before) != expected_identity):
        raise RecoveryMaterializationError(
            "recovery materialization root identity drift")
    descriptor = os.open(
        root,
        os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC |
        getattr(os, "O_NOFOLLOW", 0),
    )
    try:
        opened = os.fstat(descriptor)
        if (_root_identity(opened) != expected_identity or
                not stat.S_ISDIR(opened.st_mode)):
            raise RecoveryMaterializationError(
                "recovery materialization root identity drift")
    finally:
        os.close(descriptor)
    after = os.lstat(root)
    if _root_identity(after) != expected_identity:
        raise RecoveryMaterializationError(
            "recovery materialization root identity drift")


def _verify_root_layout(
    root: Path,
    expected_entries: set[str],
    expected_root_identity: dict[str, int | str] | None = None,
) -> dict[str, Any]:
    before = os.lstat(root)
    if (not stat.S_ISDIR(before.st_mode) or
            before.st_uid != os.geteuid() or
            stat.S_IMODE(before.st_mode) != 0o700):
        raise RecoveryMaterializationError(
            "recovery materialization root is not private")
    if (expected_root_identity is not None and
            _root_identity(before) != expected_root_identity):
        raise RecoveryMaterializationError(
            "recovery materialization root identity drift")
    descriptor = os.open(
        root,
        os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC |
        getattr(os, "O_NOFOLLOW", 0),
    )
    try:
        opened = os.fstat(descriptor)
        if _metadata_identity(opened) != _metadata_identity(before):
            raise RecoveryMaterializationError(
                "recovery materialization root identity drift")
        if (expected_root_identity is not None and
                _root_identity(opened) != expected_root_identity):
            raise RecoveryMaterializationError(
                "recovery materialization root identity drift")
        with os.scandir(descriptor) as iterator:
            entries = list(iterator)
        names = {entry.name for entry in entries}
        entry_identities: dict[str, dict[str, int | str]] = {}
        for entry in entries:
            metadata = os.stat(
                entry.name, dir_fd=descriptor, follow_symlinks=False)
            if (not stat.S_ISDIR(metadata.st_mode) or
                    metadata.st_uid != os.geteuid() or
                    stat.S_IMODE(metadata.st_mode) != 0o700):
                raise RecoveryMaterializationError(
                    "recovery materialization top-level closure is invalid")
            entry_identities[entry.name] = _root_identity(metadata)
        if len(entries) != len(names) or names != expected_entries:
            raise RecoveryMaterializationError(
                "recovery materialization top-level closure is invalid")
    finally:
        os.close(descriptor)
    after = os.lstat(root)
    if _metadata_identity(after) != _metadata_identity(before):
        raise RecoveryMaterializationError(
            "recovery materialization root identity drift")
    if (expected_root_identity is not None and
            _root_identity(after) != expected_root_identity):
        raise RecoveryMaterializationError(
            "recovery materialization root identity drift")
    return {
        "entries": entry_identities,
        "root": _root_identity(after),
    }


def _sync_published_directory(descriptor: int) -> None:
    os.fsync(descriptor)


def _atomic_receipt(
    root: Path,
    receipt_name: str,
    document: dict[str, Any],
    expected_layout: dict[str, Any],
    prepublish_verifier: Callable[[], None],
) -> None:
    payload = closure.canonical_json(document) + b"\n"
    if len(payload) > MAX_RECEIPT_BYTES:
        raise RecoveryMaterializationError("recovery receipt is oversized")
    parent_descriptor = os.open(
        root,
        os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC |
        getattr(os, "O_NOFOLLOW", 0),
    )
    temporary = f".{receipt_name}.tmp-{os.getpid()}-{uuid.uuid4().hex}"
    descriptor = -1
    linked = False
    temporary_exists = False
    try:
        root_identity = _root_identity(os.fstat(parent_descriptor))
        with os.scandir(parent_descriptor) as iterator:
            entries = list(iterator)
        entry_identities = {
            entry.name: _root_identity(os.stat(
                entry.name, dir_fd=parent_descriptor,
                follow_symlinks=False))
            for entry in entries
        }
        if (root_identity != expected_layout["root"] or
                entry_identities != expected_layout["entries"]):
            raise RecoveryMaterializationError(
                "recovery materialization root changed before receipt")
        descriptor = os.open(
            temporary,
            os.O_RDWR | os.O_CREAT | os.O_EXCL | os.O_CLOEXEC |
            getattr(os, "O_NOFOLLOW", 0),
            0o600,
            dir_fd=parent_descriptor,
        )
        temporary_exists = True
        closure._write_all(descriptor, payload)
        os.fchmod(descriptor, 0o600)
        os.fsync(descriptor)
        os.lseek(descriptor, 0, os.SEEK_SET)
        restored = bytearray()
        while len(restored) < len(payload):
            chunk = os.read(descriptor, len(payload) - len(restored))
            if not chunk:
                break
            restored.extend(chunk)
        metadata = os.fstat(descriptor)
        if (bytes(restored) != payload or
                not stat.S_ISREG(metadata.st_mode) or
                metadata.st_uid != os.geteuid() or metadata.st_nlink != 1 or
                stat.S_IMODE(metadata.st_mode) != 0o600 or
                metadata.st_size != len(payload)):
            raise RecoveryMaterializationError(
                "temporary recovery receipt verification failed")
        prepublish_verifier()
        os.lseek(descriptor, 0, os.SEEK_SET)
        confirmed = bytearray()
        while len(confirmed) < len(payload):
            chunk = os.read(descriptor, len(payload) - len(confirmed))
            if not chunk:
                break
            confirmed.extend(chunk)
        confirmed_metadata = os.fstat(descriptor)
        if (bytes(confirmed) != payload or
                _metadata_identity(confirmed_metadata) !=
                _metadata_identity(metadata)):
            raise RecoveryMaterializationError(
                "temporary recovery receipt changed before publication")
        _assert_private_root_identity(root, expected_layout["root"])
        current_root = _root_identity(os.fstat(parent_descriptor))
        with os.scandir(parent_descriptor) as iterator:
            current_entries = list(iterator)
        expected_names = set(expected_layout["entries"]) | {temporary}
        if (current_root != expected_layout["root"] or
                {entry.name for entry in current_entries} != expected_names):
            raise RecoveryMaterializationError(
                "recovery materialization root changed before publication")
        for entry in current_entries:
            current = os.stat(
                entry.name,
                dir_fd=parent_descriptor,
                follow_symlinks=False,
            )
            if entry.name == temporary:
                if (_metadata_identity(current) !=
                        _metadata_identity(confirmed_metadata) or
                        not stat.S_ISREG(current.st_mode) or
                        current.st_nlink != 1 or
                        current.st_size != len(payload)):
                    raise RecoveryMaterializationError(
                        "temporary recovery receipt changed before publication")
                continue
            if _root_identity(current) != \
                    expected_layout["entries"][entry.name]:
                raise RecoveryMaterializationError(
                    "recovery materialization child changed "
                    "before publication")
        os.link(
            temporary,
            receipt_name,
            src_dir_fd=parent_descriptor,
            dst_dir_fd=parent_descriptor,
            follow_symlinks=False,
        )
        linked = True
        os.unlink(temporary, dir_fd=parent_descriptor)
        temporary_exists = False
        os.lseek(descriptor, 0, os.SEEK_SET)
        published = bytearray()
        while len(published) < len(payload):
            chunk = os.read(descriptor, len(payload) - len(published))
            if not chunk:
                break
            published.extend(chunk)
        published_metadata = os.fstat(descriptor)
        linked_metadata = os.stat(
            receipt_name,
            dir_fd=parent_descriptor,
            follow_symlinks=False,
        )
        if (bytes(published) != payload or
                _metadata_identity(linked_metadata) !=
                _metadata_identity(published_metadata) or
                not stat.S_ISREG(published_metadata.st_mode) or
                published_metadata.st_uid != os.geteuid() or
                published_metadata.st_nlink != 1 or
                stat.S_IMODE(published_metadata.st_mode) != 0o600 or
                published_metadata.st_size != len(payload)):
            raise RecoveryMaterializationError(
                "published recovery receipt verification failed")
        _sync_published_directory(parent_descriptor)
        _assert_private_root_identity(root, expected_layout["root"])
    except (OSError, RecoveryMaterializationError) as error:
        if linked:
            try:
                os.unlink(receipt_name, dir_fd=parent_descriptor)
                linked = False
                _sync_published_directory(parent_descriptor)
            except OSError:
                pass
        raise RecoveryMaterializationError(
            "failed atomic recovery receipt creation") from error
    finally:
        if descriptor >= 0:
            os.close(descriptor)
        if temporary_exists:
            try:
                os.unlink(temporary, dir_fd=parent_descriptor)
            except FileNotFoundError:
                pass
        os.close(parent_descriptor)


def verify_materialization(
    *,
    rescue_bundle: Path,
    ref_manifest: Path,
    delta_manifest: Path,
    delta_payload: Path,
    materialization_root: Path,
    receipt_name: str = DEFAULT_RECEIPT_NAME,
    expected_sha256: dict[str, str],
) -> dict[str, Any]:
    if (RECEIPT_NAME.fullmatch(receipt_name) is None or
            receipt_name in {"repository.git", "untracked"}):
        raise RecoveryMaterializationError(
            "recovery receipt name is invalid")
    _reject_git_environment_redirection()
    running_sources = _capture_running_sources()
    bindings, ref_document, delta_document = _input_bindings(
        rescue_bundle, ref_manifest, delta_manifest, delta_payload,
        expected_sha256)
    bundle_path = bindings["rescue-bundle"][0]
    payload_path = bindings["rescue-delta-payload"][0]
    try:
        lineage = closure._verify_rescue_bundle(
            bundle_path,
            ref_document,
            ref_document.get("product_git_head"),
            ref_document.get("release_version"),
            ref_document.get("baseline"),
        )
        delta_summary = closure._verify_delta_against_bundle(
            bundle_path, ref_document, delta_document, payload_path)
        _records, inventory = closure._verify_delta_manifest(delta_document)
    except closure.EngineeringClosureError as error:
        raise RecoveryMaterializationError(str(error)) from error

    root, created_root_identity = _create_private_root(
        materialization_root)
    repository, repository_summary = _clone_and_verify_repository(
        bundle_path, root, ref_document)
    _assert_private_root_identity(root, created_root_identity)
    running_bindings = _bind_running_sources(
        repository, ref_document["product_git_head"], running_sources)
    generator_binding = _bound_source_file(
        repository, ref_document["product_git_head"],
        delta_document["runner_source_path"],
        delta_document["runner_sha256"], "recovery evidence generator",
    )
    try:
        persisted_delta_summary = closure._verify_delta_against_bundle(
            bundle_path,
            ref_document,
            delta_document,
            payload_path,
            materialization_root=root / "untracked",
        )
    except closure.EngineeringClosureError as error:
        raise RecoveryMaterializationError(str(error)) from error
    if persisted_delta_summary != delta_summary:
        raise RecoveryMaterializationError(
            "recovery delta verification changed during materialization")
    _assert_private_root_identity(root, created_root_identity)
    try:
        recovery_first = closure.verify_materialized_recovery(
            root / "untracked", inventory)
    except closure.EngineeringClosureError as error:
        raise RecoveryMaterializationError(str(error)) from error
    if (recovery_first["tree_sha256"] !=
            persisted_delta_summary["materialized_tree_sha256"] or
            recovery_first["inventory_sha256"] !=
            persisted_delta_summary["inventory_sha256"] or
            recovery_first["file_count"] !=
            persisted_delta_summary["inventory_file_count"]):
        raise RecoveryMaterializationError(
            "materialized recovery tree differs from delta closure")
    _assert_private_root_identity(root, created_root_identity)

    environment = _environment()
    _assert_inputs_stable(bindings)
    _assert_running_sources_stable(running_sources)
    repository_after = _verify_materialized_repository(
        repository, ref_document)
    if repository_after != repository_summary:
        raise RecoveryMaterializationError(
            "materialized repository changed during recovery verification")
    try:
        recovery_after = closure.verify_materialized_recovery(
            root / "untracked", inventory)
    except closure.EngineeringClosureError as error:
        raise RecoveryMaterializationError(str(error)) from error
    if recovery_after != recovery_first:
        raise RecoveryMaterializationError(
            "materialized recovery tree changed before receipt")
    root_layout = _verify_root_layout(
        root,
        {"repository.git", "untracked"},
        created_root_identity,
    )
    root_identity = root_layout["root"]

    artifacts = {
        role: {
            "path": str(path),
            "sha256": snapshot.sha256,
            "size": snapshot.size,
        }
        for role, (path, snapshot) in sorted(bindings.items())
    }
    document = {
        "schema": SCHEMA,
        "version": VERSION,
        "status": "same-host-recovery-materialized-and-verified",
        "generated_at": datetime.now(timezone.utc).replace(
            microsecond=0).isoformat().replace("+00:00", "Z"),
        "passed": True,
        "scope": "same-host-offline-recovery-only",
        "source": {
            "baseline": ref_document["baseline"],
            "product_git_head": lineage["product_git_head"],
            "release_git_head": lineage["release_git_head"],
            "release_version": ref_document["release_version"],
        },
        "artifacts": artifacts,
        "independent_expected_sha256": {
            role: expected_sha256[role] for role in sorted(expected_sha256)
        },
        "tools": {
            "evidence_generator": generator_binding,
            **running_bindings,
        },
        "materialization": {
            "root": str(root),
            "root_device": root_identity["device"],
            "root_gid": root_identity["gid"],
            "root_inode": root_identity["inode"],
            "root_mode": root_identity["mode"],
            "root_uid": root_identity["uid"],
            "repository": repository_summary,
            "untracked": {
                **persisted_delta_summary,
                "verified_directory_count": recovery_after[
                    "directory_count"],
                "verified_tree_sha256": recovery_after["tree_sha256"],
            },
        },
        "environment": environment,
        "safety_boundaries": {
            "broker_connection_performed": False,
            "git_clean_performed": False,
            "git_gc_performed": False,
            "git_prune_performed": False,
            "live_authorized": False,
            "order_placement_performed": False,
            "paper_authorized": False,
            "source_files_deleted": False,
        },
        "external_certification": {
            "immutable_worm_legal_hold_signed": False,
            "off_host_restore_certified": False,
            "release_authorized": False,
        },
    }
    def verify_immediately_before_publication() -> None:
        _assert_private_root_identity(root, created_root_identity)
        _assert_inputs_stable(bindings)
        _assert_running_sources_stable(running_sources)
        repository_final = _verify_materialized_repository(
            repository, ref_document)
        if repository_final != repository_summary:
            raise RecoveryMaterializationError(
                "materialized repository changed before receipt publication")
        try:
            recovery_final = closure.verify_materialized_recovery(
                root / "untracked", inventory)
        except closure.EngineeringClosureError as error:
            raise RecoveryMaterializationError(str(error)) from error
        if recovery_final != recovery_after:
            raise RecoveryMaterializationError(
                "materialized recovery tree changed before "
                "receipt publication")
        _assert_private_root_identity(root, created_root_identity)

    _atomic_receipt(
        root,
        receipt_name,
        document,
        root_layout,
        verify_immediately_before_publication,
    )
    return document


def _parse_arguments(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Materialize a new private Round38 same-host recovery root. "
            "This verifier never cleans, prunes, deletes, or mutates inputs."
        ))
    parser.add_argument(
        "--verify", action="store_true",
        help="Required explicit opt-in to perform materialization")
    parser.add_argument("--rescue-bundle", type=Path, required=True)
    parser.add_argument("--ref-manifest", type=Path, required=True)
    parser.add_argument("--delta-manifest", type=Path, required=True)
    parser.add_argument("--delta-payload", type=Path, required=True)
    parser.add_argument("--materialization-root", type=Path, required=True)
    parser.add_argument(
        "--receipt-name", default=DEFAULT_RECEIPT_NAME)
    parser.add_argument(
        "--expected-rescue-bundle-sha256", required=True)
    parser.add_argument(
        "--expected-ref-manifest-sha256", required=True)
    parser.add_argument(
        "--expected-delta-manifest-sha256", required=True)
    parser.add_argument(
        "--expected-delta-payload-sha256", required=True)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    arguments = _parse_arguments(argv)
    if arguments.verify is not True:
        raise RecoveryMaterializationError(
            "explicit --verify is required; no materialization was performed")
    expected = {
        "rescue-bundle": arguments.expected_rescue_bundle_sha256,
        "rescue-ref-manifest": arguments.expected_ref_manifest_sha256,
        "rescue-delta-manifest": arguments.expected_delta_manifest_sha256,
        "rescue-delta-payload": arguments.expected_delta_payload_sha256,
    }
    result = verify_materialization(
        rescue_bundle=arguments.rescue_bundle,
        ref_manifest=arguments.ref_manifest,
        delta_manifest=arguments.delta_manifest,
        delta_payload=arguments.delta_payload,
        materialization_root=arguments.materialization_root,
        receipt_name=arguments.receipt_name,
        expected_sha256=expected,
    )
    print(
        "PASS: hepta.round38-recovery-materialization.v1 "
        f"release={result['source']['release_git_head']} "
        f"refs={result['materialization']['repository']['ref_count']} "
        f"untracked={result['materialization']['untracked']['inventory_file_count']}"
    )
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except RecoveryMaterializationError as error:
        print(f"ERROR: {error}", file=sys.stderr)
        raise SystemExit(2)
