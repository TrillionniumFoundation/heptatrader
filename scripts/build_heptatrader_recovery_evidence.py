#!/usr/bin/env python3
"""Build a final-HEAD rescue ref set and independently derived OOS delta."""

from __future__ import annotations

import argparse
import hashlib
import io
import os
from pathlib import Path
import stat
import subprocess
import sys
import tarfile
from typing import Any


SCRIPT_DIRECTORY = Path(__file__).resolve(strict=True).parent
sys.path.insert(0, str(SCRIPT_DIRECTORY))

import build_heptatrader_delivery_closure as common  # noqa: E402
import build_heptatrader_engineering_closure as closure  # noqa: E402
import build_heptatrader_verification_evidence as evidence  # noqa: E402


RECOVERY_MODES = frozenset({0o600, 0o644, 0o664, 0o700, 0o755, 0o775})


def _worktree_bytes(path: Path) -> tuple[bytes, str, str]:
    if path.resolve(strict=True) != Path(os.path.abspath(path)):
        raise closure.EngineeringClosureError(
            f"delta source contains a symlink ancestor: {path}")
    before = path.lstat()
    if (stat.S_ISLNK(before.st_mode) or not stat.S_ISREG(before.st_mode) or
            before.st_nlink != 1 or before.st_size > 64 * 1024 * 1024):
        raise closure.EngineeringClosureError(
            f"unsafe delta source: {path}")
    descriptor = os.open(
        path, os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) |
        getattr(os, "O_NOFOLLOW", 0))
    try:
        opened = os.fstat(descriptor)
        data = bytearray()
        while True:
            chunk = os.read(descriptor, 1024 * 1024)
            if not chunk:
                break
            data.extend(chunk)
            if len(data) > 64 * 1024 * 1024:
                raise closure.EngineeringClosureError(
                    f"delta source exceeds limit: {path}")
        after = os.fstat(descriptor)
    finally:
        os.close(descriptor)
    identity = lambda value: (
        value.st_dev, value.st_ino, value.st_mode, value.st_nlink,
        value.st_uid, value.st_gid, value.st_size, value.st_mtime_ns,
        value.st_ctime_ns)
    if (identity(before) != identity(opened) or
            identity(opened) != identity(after) or len(data) != before.st_size):
        raise closure.EngineeringClosureError(
            f"delta source changed while reading: {path}")
    recovery_mode_value = stat.S_IMODE(before.st_mode)
    if recovery_mode_value not in RECOVERY_MODES:
        raise closure.EngineeringClosureError(
            f"unsupported recovery mode {recovery_mode_value:04o}: {path}")
    git_mode = "0755" if recovery_mode_value & 0o111 else "0644"
    recovery_mode = f"{recovery_mode_value:04o}"
    return bytes(data), git_mode, recovery_mode


def _git(
    repository: Path,
    arguments: list[str],
    *,
    text: bool = False,
) -> bytes | str:
    run = subprocess.run(
        ["git", "-C", str(repository), *arguments],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        check=False)
    if run.returncode != 0:
        raise closure.EngineeringClosureError(
            f"Git recovery query failed: {' '.join(arguments)}")
    if text:
        try:
            return run.stdout.decode("utf-8", errors="strict")
        except UnicodeDecodeError as error:
            raise closure.EngineeringClosureError(
                "Git recovery query was not UTF-8") from error
    return run.stdout


def _live_refs(repository: Path) -> list[dict[str, str]]:
    text = _git(
        repository,
        ["for-each-ref", "--format=%(objectname) %(refname)"],
        text=True)
    assert isinstance(text, str)
    refs: list[dict[str, str]] = []
    for line in text.splitlines():
        fields = line.split(" ", 1)
        if (len(fields) != 2 or
                closure.HEX40.fullmatch(fields[0]) is None or
                not fields[1].startswith("refs/")):
            raise closure.EngineeringClosureError(
                "live Git ref identity is invalid")
        refs.append({"object": fields[0], "name": fields[1]})
    refs.sort(key=lambda record: record["name"])
    if len({record["name"] for record in refs}) != len(refs):
        raise closure.EngineeringClosureError("live Git refs are duplicated")
    return refs


def _tree(
    repository: Path,
    tree_object: str,
    wanted_paths: set[str],
) -> dict[str, tuple[str, str, str]]:
    output = _git(
        repository,
        ["ls-tree", "-rz", "--full-tree", tree_object])
    assert isinstance(output, bytes)
    records: dict[str, tuple[str, str, str]] = {}
    for raw in output.split(b"\0"):
        if not raw:
            continue
        try:
            metadata, raw_path = raw.split(b"\t", 1)
            mode, kind, object_id = metadata.decode("ascii").split(" ")
            relative = raw_path.decode("utf-8", errors="strict")
        except (ValueError, UnicodeDecodeError) as error:
            raise closure.EngineeringClosureError(
                "OOS tree record is invalid") from error
        if relative not in wanted_paths:
            continue
        relative = common.normalized_relative_path(relative, "OOS tree path")
        if relative in records:
            raise closure.EngineeringClosureError(
                "OOS tree paths are duplicated")
        records[relative] = (mode, kind, object_id)
    return records


def _git_blob_oid(data: bytes) -> str:
    return hashlib.sha1(
        f"blob {len(data)}\0".encode("ascii") + data).hexdigest()


def _snapshot_delta(
    repository: Path,
    oos_ref: str,
) -> tuple[dict[str, Any], dict[str, bytes]]:
    oos_object_raw = _git(
        repository, ["rev-parse", "--verify", oos_ref], text=True)
    oos_tree_raw = _git(
        repository, ["rev-parse", "--verify", f"{oos_ref}^{{tree}}"],
        text=True)
    assert isinstance(oos_object_raw, str)
    assert isinstance(oos_tree_raw, str)
    oos_object = oos_object_raw.strip()
    oos_tree = oos_tree_raw.strip()
    if (closure.HEX40.fullmatch(oos_object) is None or
            closure.HEX40.fullmatch(oos_tree) is None):
        raise closure.EngineeringClosureError("OOS ref/tree is invalid")
    untracked_raw = _git(
        repository,
        ["ls-files", "--others", "--exclude-standard", "-z"])
    assert isinstance(untracked_raw, bytes)
    raw_paths = [item for item in untracked_raw.split(b"\0") if item]
    paths: list[str] = []
    for raw in raw_paths:
        try:
            relative = raw.decode("utf-8", errors="strict")
        except UnicodeDecodeError as error:
            raise closure.EngineeringClosureError(
                "untracked path is not UTF-8") from error
        paths.append(common.normalized_relative_path(
            relative, "untracked recovery path"))
    if paths != sorted(paths) or len(paths) != len(set(paths)):
        raise closure.EngineeringClosureError(
            "untracked recovery inventory is not canonical")
    baseline_tree = _tree(repository, oos_tree, set(paths))
    inventory: list[dict[str, Any]] = []
    delta: list[dict[str, Any]] = []
    exact_mode_overrides: list[dict[str, str]] = []
    payload: dict[str, bytes] = {}
    exact_count = 0
    for relative in paths:
        data, git_mode, recovery_mode = _worktree_bytes(
            repository.joinpath(*Path(relative).parts))
        current = {
            "sha256": hashlib.sha256(data).hexdigest(),
            "size": len(data),
            "mode": recovery_mode,
        }
        baseline = baseline_tree.get(relative)
        baseline_blob = None
        relation: str
        if baseline is None:
            relation = "new"
        elif (baseline[1] == "blob" and
              baseline[0] in {"100644", "100755"}):
            baseline_mode = (
                "0755" if baseline[0] == "100755" else "0644")
            if _git_blob_oid(data) == baseline[2]:
                relation = "exact"
                exact_count += 1
                if recovery_mode != baseline_mode:
                    exact_mode_overrides.append({
                        "path": relative,
                        "mode": recovery_mode,
                    })
            else:
                relation = "modified"
                blob = _git(
                    repository, ["cat-file", "blob", baseline[2]])
                assert isinstance(blob, bytes)
                baseline_blob = {
                    "blob_object": baseline[2],
                    "sha256": hashlib.sha256(blob).hexdigest(),
                    "size": len(blob),
                    "mode": baseline_mode,
                }
        else:
            raise closure.EngineeringClosureError(
                f"unsupported OOS baseline type: {relative}")
        inventory.append({
            "path": relative,
            "relation": relation,
            "current": current,
            "baseline_blob_object": (
                baseline[2] if baseline is not None else None),
        })
        if relation == "exact":
            continue
        classification = (
            "modified-after-oos-v6.1"
            if relation == "modified" else "new-after-oos-v6.1")
        delta.append({
            "classification": classification,
            "path": relative,
            "current": current,
            "baseline": baseline_blob,
        })
        payload[relative] = data
    counts = {
        "modified-after-oos-v6.1": sum(
            record["classification"] == "modified-after-oos-v6.1"
            for record in delta),
        "new-after-oos-v6.1": sum(
            record["classification"] == "new-after-oos-v6.1"
            for record in delta),
    }
    runner_snapshot = common.stable_read(
        Path(__file__).resolve(strict=True),
        limit=evidence.MAX_INPUT_BYTES, capture=False,
        require_trusted_parent=False)
    document = {
        "schema": "hepta.oos-worktree-delta.v3",
        "version": 3,
        "scope": "untracked-files-differing-from-oos-tag",
        "runner_source_path": closure.RECOVERY_RUNNER_SOURCE,
        "runner_sha256": runner_snapshot.sha256,
        "oos_ref": oos_ref,
        "oos_ref_object": oos_object,
        "oos_tree": oos_tree,
        "untracked_file_count": len(paths),
        "exact_match_excluded_count": exact_count,
        "exact_mode_override_count": len(exact_mode_overrides),
        "exact_mode_overrides_sha256": hashlib.sha256(
            closure.canonical_json(exact_mode_overrides)).hexdigest(),
        "exact_mode_overrides": exact_mode_overrides,
        "untracked_inventory_sha256": hashlib.sha256(
            closure.canonical_json(inventory)).hexdigest(),
        "untracked_inventory": inventory,
        "file_count": len(delta),
        "classification_counts": counts,
        "files_sha256": hashlib.sha256(
            closure.canonical_json(delta)).hexdigest(),
        "files": delta,
    }
    closure._verify_delta_manifest(document)
    return document, payload


def _payload(document: dict[str, Any], files: dict[str, bytes]) -> bytes:
    output = io.BytesIO()
    with tarfile.open(
            fileobj=output, mode="w",
            format=tarfile.USTAR_FORMAT) as archive:
        for record in document["files"]:
            data = files[record["path"]]
            info = tarfile.TarInfo(record["path"])
            info.size = len(data)
            info.mode = int(record["current"]["mode"], 8)
            info.uid = 0
            info.gid = 0
            info.uname = "root"
            info.gname = "root"
            info.mtime = 0
            archive.addfile(info, io.BytesIO(data))
    return output.getvalue()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repository", type=Path, required=True)
    parser.add_argument("--rescue-bundle", type=Path, required=True)
    parser.add_argument(
        "--release-git-head", "--expected-git-head",
        dest="release_git_head", required=True,
        help="Final Round38 release commit R")
    parser.add_argument("--product-git-head", required=True)
    parser.add_argument("--release-version", required=True)
    parser.add_argument("--artifact-root", type=Path, required=True)
    parser.add_argument("--oos-ref", default=closure.OOS_REF)
    parser.add_argument(
        "--ref-manifest-output", default="round38-rescue-ref-manifest.json")
    parser.add_argument(
        "--delta-manifest-output", default="round38-rescue-delta-manifest.json")
    parser.add_argument(
        "--delta-payload-output", default="round38-rescue-delta-payload.tar")
    arguments = parser.parse_args()
    if (closure.HEX40.fullmatch(arguments.release_git_head) is None or
            closure.HEX40.fullmatch(arguments.product_git_head) is None or
            arguments.release_git_head == arguments.product_git_head or
            closure.RELEASE.fullmatch(arguments.release_version) is None or
            not arguments.release_version.endswith("-round38") or
            arguments.oos_ref != closure.OOS_REF):
        raise closure.EngineeringClosureError(
            "recovery dual-head identity or OOS ref is invalid")
    repository = Path(os.path.abspath(arguments.repository))
    if repository.resolve(strict=True) != repository:
        raise closure.EngineeringClosureError(
            "recovery repository contains a symlink")
    root = evidence._protected_root(arguments.artifact_root)
    if root == repository or repository in root.parents:
        raise closure.EngineeringClosureError(
            "recovery artifact root must be outside the repository")
    outputs = {
        "refs": evidence._relative(arguments.ref_manifest_output),
        "delta": evidence._relative(arguments.delta_manifest_output),
        "payload": evidence._relative(arguments.delta_payload_output),
    }
    if len(set(outputs.values())) != len(outputs):
        raise closure.EngineeringClosureError(
            "recovery output paths are duplicated")
    for relative in outputs.values():
        if os.path.lexists(root.joinpath(*Path(relative).parts)):
            raise closure.EngineeringClosureError(
                f"recovery output already exists: {relative}")
    bundle = Path(os.path.abspath(arguments.rescue_bundle))
    if bundle.resolve(strict=True) != bundle:
        raise closure.EngineeringClosureError(
            "rescue bundle contains a symlink")
    baseline_path = closure._round_baseline_path(arguments.release_version)
    baseline_bytes, baseline_git_mode, baseline_recovery_mode = _worktree_bytes(
        repository.joinpath(*Path(baseline_path).parts))
    baseline = {
        "path": baseline_path,
        "sha256": hashlib.sha256(baseline_bytes).hexdigest(),
        "size": len(baseline_bytes),
        "mode": baseline_git_mode,
    }
    if (baseline_git_mode != "0644" or
            baseline_recovery_mode != "0644"):
        raise closure.EngineeringClosureError(
            "Round38 release baseline mode is invalid")
    closure._verify_release_commit(
        repository, arguments.product_git_head, arguments.release_git_head,
        arguments.release_version, baseline)
    symbolic = _git(repository, ["symbolic-ref", "-q", "HEAD"], text=True)
    head = _git(repository, ["rev-parse", "HEAD"], text=True)
    round38 = _git(
        repository, ["rev-parse", closure.ROUND38_REF], text=True)
    dirty = _git(
        repository,
        ["status", "--porcelain=v1", "--untracked-files=no"],
        text=True)
    assert all(isinstance(item, str)
               for item in (symbolic, head, round38, dirty))
    if (symbolic.strip() != closure.ROUND38_REF or
            head.strip() != arguments.release_git_head or
            round38.strip() != arguments.release_git_head or
            dirty.strip()):
        raise closure.EngineeringClosureError(
            "final Round38 repository identity is not closed")
    refs_before = _live_refs(repository)
    delta_document, payload_files = _snapshot_delta(
        repository, arguments.oos_ref)
    refs, bundle_head = closure._bundle_heads(bundle)
    if (refs != refs_before or
            bundle_head["object"] != arguments.release_git_head):
        raise closure.EngineeringClosureError(
            "rescue bundle does not bind the final repository ref set")
    bundle_snapshot = common.stable_read(
        bundle, limit=closure.MAX_ARTIFACT_BYTES, capture=False,
        require_trusted_parent=True)
    ref_manifest = {
        "schema": "hepta.git-rescue-ref-manifest.v2",
        "version": 2,
        "product_git_head": arguments.product_git_head,
        "release_git_head": arguments.release_git_head,
        "release_version": arguments.release_version,
        "baseline": baseline,
        "bundle_sha256": bundle_snapshot.sha256,
        "bundle_size": bundle_snapshot.size,
        "ref_count": len(refs),
        "ref_set_sha256": hashlib.sha256(closure.canonical_json({
            "head": bundle_head,
            "refs": refs,
        })).hexdigest(),
        "refs": refs,
        "head": bundle_head,
    }
    closure._verify_rescue_bundle(
        bundle, ref_manifest, arguments.product_git_head,
        arguments.release_version, baseline)
    payload = _payload(delta_document, payload_files)
    delta_after, _ = _snapshot_delta(repository, arguments.oos_ref)
    refs_after = _live_refs(repository)
    head_after = _git(repository, ["rev-parse", "HEAD"], text=True)
    dirty_after = _git(
        repository,
        ["status", "--porcelain=v1", "--untracked-files=no"],
        text=True)
    if (delta_after != delta_document or refs_after != refs_before or
            not isinstance(head_after, str) or
            head_after.strip() != arguments.release_git_head or
            not isinstance(dirty_after, str) or dirty_after.strip()):
        raise closure.EngineeringClosureError(
            "repository changed while recovery evidence was built")
    evidence._write_private(
        root, Path(outputs["refs"]),
        closure.canonical_json(ref_manifest) + b"\n")
    evidence._write_private(
        root, Path(outputs["delta"]),
        closure.canonical_json(delta_document) + b"\n")
    evidence._write_private(
        root, Path(outputs["payload"]), payload)
    print(
        "PASS: hepta.round38-recovery-evidence.v2 "
        f"refs={len(refs)} delta_files={delta_document['file_count']} "
        f"payload_sha256={hashlib.sha256(payload).hexdigest()}")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (
            closure.EngineeringClosureError, evidence.EvidenceError,
            common.DeliveryClosureError, OSError, ValueError) as error:
        print(f"FAIL: {error}", file=sys.stderr)
        raise SystemExit(1)
