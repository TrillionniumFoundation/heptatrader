#!/usr/bin/env python3
"""Build a versioned local engineering closure that remains fail-closed."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import io
import json
import os
from pathlib import Path, PurePosixPath
import re
import stat
import subprocess
import sys
import tempfile
import tarfile
from typing import Any


SCRIPT_DIRECTORY = Path(__file__).resolve(strict=True).parent
REPOSITORY = SCRIPT_DIRECTORY.parent
sys.path.insert(0, str(SCRIPT_DIRECTORY))

import build_heptatrader_delivery_closure as common  # noqa: E402
import build_heptatrader_verification_evidence as verification  # noqa: E402
import verify_hepta_execution_native_vm_bundle as native_verifier  # noqa: E402
import verify_heptatrader_agent_os_source_bundle as agent_verifier  # noqa: E402
import verify_heptatrader_clean_source_bundle as source_verifier  # noqa: E402
import verify_heptatrader_runtime_package as runtime_verifier  # noqa: E402


SCHEMA = "heptatrader.engineering-closure.v2"
MAP_SCHEMA = "heptatrader.engineering-artifact-map.v2"
PROJECT_ID = "heptatrader-agent-os"
PASSED_SCOPE = "local-offline-engineering-only"
STATUS = "local-engineering-pass-pending-external"
MAX_MAP_BYTES = 4 * 1024 * 1024
MAX_ARTIFACT_BYTES = 16 * 1024 * 1024 * 1024
ROUND38_REF = "refs/heads/round38-consolidation"
OOS_REF = "refs/tags/ai-native-os-v0.4.0-oos-v6.1"
OOS_REF_OBJECT = "e0294ba7bf68129ad14dd87b693ea74f6891583d"
OOS_TREE_OBJECT = "996d3b283cdc17cc1ed224fd044145801ab9abd7"
ROUND38_UNTRACKED_INVENTORY_SHA256 = (
    "b6b7cd3a9ce7ccfb4f99a8f79eb1d0919e02635b49de6e7c82598f20a8bad069"
)
RECOVERY_RUNNER_SOURCE = "scripts/build_heptatrader_recovery_evidence.py"
HEX40 = re.compile(r"^[0-9a-f]{40}$")
RELEASE = re.compile(r"^[0-9A-Za-z][0-9A-Za-z.+-]{0,126}$")
RECOVERY_MAX_FILE_BYTES = 64 * 1024 * 1024
RECOVERY_MAX_TOTAL_BYTES = 64 * 1024 * 1024
RECOVERY_MODES = frozenset({
    "0600", "0644", "0664", "0700", "0755", "0775",
})
RECOVERY_BASELINE_MODES = frozenset({"0644", "0755"})
REQUIRED_ROLES = (
    "agent-os-source-bundle",
    "agent-os-source-manifest",
    "agent-os-source-policy",
    "coverage-report",
    "engineering-artifact-map",
    "legacy-wrapper-inventory-report",
    "native-vm-report",
    "native-vm-rootfs",
    "rescue-bundle",
    "rescue-delta-manifest",
    "rescue-delta-payload",
    "rescue-ref-manifest",
    "runner-identity-report",
    "runtime-package",
    "runtime-package-manifest",
    "sanitizer-report",
    "source-baseline-manifest",
    "strict-source-bundle",
    "strict-source-manifest",
    "test-matrix-report",
    "workspace-layout-report",
)
JSON_ROLES = frozenset(
    role for role in REQUIRED_ROLES
    if role not in {
        "agent-os-source-bundle",
        "rescue-delta-payload",
        "native-vm-rootfs",
        "rescue-bundle",
        "strict-source-bundle",
        "runtime-package",
    })
SAFETY_BOUNDARIES = {
    "broker_connection_performed": False,
    "live_authorized": False,
    "object_store_ingestion_receipt_certified": False,
    "order_placement_performed": False,
    "paper_authorized": False,
    "real_systemd_certified": False,
    "source_files_deleted": False,
    "source_removal_authorized": False,
}
EXTERNAL_BLOCKERS = (
    "private-off-host-remote-and-branch-protection",
    "three-distinct-rootful-watch-vms",
    "immutable-worm-legal-hold-signed-receipt",
    "capped-paper-authorization-and-real-broker-validation",
)
PRODUCTION_PASSED = False
RELEASE_AUTHORIZED = False
GIT_ENVIRONMENT = {
    "GIT_CONFIG_GLOBAL": "/dev/null",
    "GIT_CONFIG_NOSYSTEM": "1",
    "GIT_CONFIG_SYSTEM": "/dev/null",
    "GIT_OPTIONAL_LOCKS": "0",
    "GIT_TERMINAL_PROMPT": "0",
    "HOME": "/nonexistent",
    "LANG": "C",
    "LC_ALL": "C",
    "PATH": "/usr/bin:/bin",
}


class EngineeringClosureError(RuntimeError):
    """The engineering closure is incomplete, unsafe, or inconsistent."""


def _is_hex40(value: Any) -> bool:
    return isinstance(value, str) and HEX40.fullmatch(value) is not None


def _is_hex64(value: Any) -> bool:
    return isinstance(value, str) and common.HEX64.fullmatch(value) is not None


def canonical_json(value: Any) -> bytes:
    try:
        return json.dumps(
            value, ensure_ascii=True, sort_keys=True,
            separators=(",", ":"), allow_nan=False).encode("ascii")
    except (TypeError, ValueError) as error:
        raise EngineeringClosureError(
            "engineering closure is not canonical JSON data") from error


def _relative(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value or "\0" in value or "\\" in value:
        raise EngineeringClosureError(f"{label} is not a relative path")
    path = PurePosixPath(value)
    if (path.is_absolute() or path.as_posix() != value or
            any(part in {"", ".", ".."} for part in path.parts)):
        raise EngineeringClosureError(f"{label} is not canonical")
    return value


def _path_ancestors(relative: str) -> tuple[str, ...]:
    parts = PurePosixPath(relative).parts
    return tuple(
        PurePosixPath(*parts[:index]).as_posix()
        for index in range(1, len(parts)))


def _require_file_prefix_free(paths: set[str], label: str) -> None:
    if any(
            ancestor in paths
            for relative in paths
            for ancestor in _path_ancestors(relative)):
        raise EngineeringClosureError(f"{label} has a file-prefix collision")


def _require_disjoint_file_trees(
    first: set[str], second: set[str], label: str,
) -> None:
    if (first & second or
            any(ancestor in second
                for relative in first
                for ancestor in _path_ancestors(relative)) or
            any(ancestor in first
                for relative in second
                for ancestor in _path_ancestors(relative))):
        raise EngineeringClosureError(f"{label} has a path collision")


def _stable_binding(
    root: Path, relative: str, role: str, *, capture: bool,
) -> tuple[dict[str, Any], bytes | None]:
    relative = _relative(relative, f"{role} path")
    path = root.joinpath(*PurePosixPath(relative).parts)
    try:
        snapshot = common.stable_read(
            path,
            limit=MAX_ARTIFACT_BYTES,
            capture=capture,
            require_trusted_parent=True,
        )
    except common.DeliveryClosureError as error:
        raise EngineeringClosureError(
            f"{role} failed stable read: {error}") from error
    return ({
        "role": role,
        "path": relative,
        "sha256": snapshot.sha256,
        "size": snapshot.size,
        "mode": snapshot.mode,
    }, snapshot.data)


def _strict_document(data: bytes, label: str) -> dict[str, Any]:
    try:
        value = common.strict_json(data, label)
    except common.DeliveryClosureError as error:
        raise EngineeringClosureError(str(error)) from error
    if not isinstance(value, dict):
        raise EngineeringClosureError(f"{label} root is not an object")
    return value


def _report_input_binding(
    artifact_root: Path, record: Any, label: str,
) -> None:
    expected = {"name", "path", "sha256", "size", "mode"}
    if not isinstance(record, dict) or set(record) != expected:
        raise EngineeringClosureError(
            f"{label} transitive input fields are invalid")
    binding, _ = _stable_binding(
        artifact_root, record["path"], f"{label}:{record['name']}",
        capture=False)
    for field in ("sha256", "size", "mode"):
        if record[field] != binding[field]:
            raise EngineeringClosureError(
                f"{label} transitive input {field} drift")


def _verification_report(
    artifact_root: Path, value: dict[str, Any], kind: str,
) -> None:
    if set(value) != {
            "schema", "version", "kind", "generated_at", "passed",
            "cases", "inputs", "boundary"}:
        raise EngineeringClosureError(
            f"{kind} verification report fields are invalid")
    if (value["schema"] != verification.SCHEMA or value["version"] != 2 or
            value["kind"] != kind or value["passed"] is not True or
            value["boundary"] != verification.BOUNDARY or
            not isinstance(value["cases"], list) or not value["cases"] or
            not isinstance(value["inputs"], list) or not value["inputs"]):
        raise EngineeringClosureError(
            f"{kind} verification report did not pass")
    inputs_by_name: dict[str, dict[str, Any]] = {}
    for record in value["inputs"]:
        _report_input_binding(artifact_root, record, kind)
        name = record["name"]
        if not isinstance(name, str) or name in inputs_by_name:
            raise EngineeringClosureError(
                f"{kind} verification inputs are duplicated")
        inputs_by_name[name] = record
    try:
        if kind in {"matrix", "sanitizer"}:
            expected_labels = (
                verification.MATRIX_LABELS
                if kind == "matrix" else verification.SANITIZER_LABELS)
            cases = value["cases"]
            if (not all(isinstance(case, dict) for case in cases) or
                    {case.get("name") for case in cases} != expected_labels):
                raise EngineeringClosureError(
                    f"{kind} verification case closure is invalid")
            values = []
            for case in cases:
                name = case["name"]
                sidecar_name = f"{name}.sidecar"
                record = inputs_by_name.get(sidecar_name)
                if (record is None or
                        not isinstance(case.get("expected"), int) or
                        isinstance(case.get("expected"), bool)):
                    raise EngineeringClosureError(
                        f"{kind} verification case is invalid")
                values.append(
                    f"{name}={case['expected']}={record['path']}")
            rebuilt = verification.build_ctest(
                kind, artifact_root, values, value["generated_at"])
        elif kind == "coverage":
            if (len(value["cases"]) != 1 or
                    not isinstance(value["cases"][0], dict) or
                    value["cases"][0].get("name") != "line-rate" or
                    "coverage.sidecar" not in inputs_by_name):
                raise EngineeringClosureError(
                    "coverage verification case closure is invalid")
            rebuilt = verification.build_coverage(
                artifact_root,
                inputs_by_name["coverage.sidecar"]["path"],
                value["cases"][0].get("expected"),
                value["generated_at"])
        else:
            caches = []
            sources = []
            for name in sorted(verification.RUNNER_LABELS):
                cache = inputs_by_name.get(f"{name}.cmake-cache")
                if cache is None:
                    raise EngineeringClosureError(
                        "runner cache input closure is invalid")
                caches.append(f"{name}={cache['path']}")
            for name in sorted(
                    verification.SOURCE_ATTESTATION_LABELS):
                source = inputs_by_name.get(f"{name}.source-manifest")
                if source is None:
                    raise EngineeringClosureError(
                        "runner source input closure is invalid")
                sources.append(f"{name}={source['path']}")
            rebuilt = verification.build_runner(
                artifact_root, caches, value["generated_at"], sources)
    except verification.EvidenceError as error:
        raise EngineeringClosureError(
            f"{kind} verification evidence is invalid: {error}") from error
    if rebuilt != value:
        raise EngineeringClosureError(
            f"{kind} verification report is not reproducible")


def _bundle_heads(path: Path) -> tuple[list[dict[str, str]], dict[str, str]]:
    listed = subprocess.run(
        ["git", "bundle", "list-heads", str(path)],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        env=GIT_ENVIRONMENT,
        text=True,
        encoding="ascii",
        errors="strict",
        check=False)
    if listed.returncode != 0:
        raise EngineeringClosureError("rescue Git bundle heads are unavailable")
    refs: list[dict[str, str]] = []
    head: dict[str, str] | None = None
    for line in listed.stdout.splitlines():
        fields = line.split(" ", 1)
        if len(fields) != 2 or HEX40.fullmatch(fields[0]) is None:
            raise EngineeringClosureError("rescue Git bundle head is invalid")
        record = {"object": fields[0], "name": fields[1]}
        if fields[1] == "HEAD":
            if head is not None:
                raise EngineeringClosureError("rescue bundle has duplicate HEAD")
            head = record
        elif (not fields[1].startswith("refs/") or
              any(character.isspace() for character in fields[1])):
            raise EngineeringClosureError("rescue Git ref is invalid")
        else:
            refs.append(record)
    refs.sort(key=lambda record: record["name"])
    if head is None or not refs:
        raise EngineeringClosureError("rescue Git ref closure is incomplete")
    return refs, head


def _round_baseline_path(release_version: str) -> str:
    if (not isinstance(release_version, str) or
            RELEASE.fullmatch(release_version) is None or
            not release_version.endswith("-round38")):
        raise EngineeringClosureError(
            "Round38 release version is invalid")
    return (
        f"release-manifests/heptatrader-agent-os-v{release_version}/"
        "manifest.json")


def _verify_runner_source_manifests(
    runner_inputs: dict[str, dict[str, Any]],
    agent_binding: dict[str, Any],
    strict_binding: dict[str, Any],
) -> None:
    identity = lambda record: (
        record.get("sha256"), record.get("size"), record.get("mode"))
    for label in verification.NO_GIT_LABELS:
        if identity(runner_inputs.get(
                f"{label}.source-manifest", {})) != identity(agent_binding):
            raise EngineeringClosureError(
                "no-Git runner source manifest differs from Agent bundle")
    for label in verification.STRICT_SOURCE_LABELS:
        if identity(runner_inputs.get(
                f"{label}.source-manifest", {})) != identity(strict_binding):
            raise EngineeringClosureError(
                "sanitizer runner source manifest differs from strict bundle")


def _git_capture(
    repository: Path,
    arguments: list[str],
    label: str,
) -> bytes:
    try:
        result = subprocess.run(
            ["git", "-C", str(repository), *arguments],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            env=GIT_ENVIRONMENT,
            timeout=60,
            check=False)
    except (OSError, subprocess.TimeoutExpired) as error:
        raise EngineeringClosureError(f"{label} failed") from error
    if result.returncode != 0:
        raise EngineeringClosureError(f"{label} failed")
    return result.stdout


def _verify_release_commit(
    repository: Path,
    product_git_head: str,
    release_git_head: str,
    release_version: str,
    baseline: dict[str, Any],
) -> dict[str, Any]:
    baseline_path = _round_baseline_path(release_version)
    if (not _is_hex40(product_git_head) or
            not _is_hex40(release_git_head) or
            product_git_head == release_git_head or
            not isinstance(baseline, dict) or
            set(baseline) != {"path", "sha256", "size", "mode"} or
            baseline.get("path") != baseline_path or
            not _is_hex64(baseline.get("sha256")) or
            type(baseline.get("size")) is not int or
            baseline["size"] <= 0 or baseline.get("mode") != "0644"):
        raise EngineeringClosureError(
            "Round38 dual-head lineage identity is invalid")
    parents = _git_capture(
        repository, ["show", "-s", "--format=%P", release_git_head],
        "Round38 release parent query")
    try:
        parent_text = parents.decode("ascii", errors="strict").strip()
    except UnicodeDecodeError as error:
        raise EngineeringClosureError(
            "Round38 release parent identity is invalid") from error
    if parent_text.split() != [product_git_head]:
        raise EngineeringClosureError(
            "Round38 release commit is not the single child of product HEAD")
    changed = _git_capture(
        repository,
        ["diff", "--name-status", "--no-renames", "-z",
         product_git_head, release_git_head, "--"],
        "Round38 release tree query")
    if changed.split(b"\0") != [
            b"A", baseline_path.encode("utf-8"), b""]:
        raise EngineeringClosureError(
            "Round38 release commit changed paths other than its baseline")
    listing = _git_capture(
        repository,
        ["ls-tree", "-z", release_git_head, "--", baseline_path],
        "Round38 baseline tree query")
    try:
        metadata, raw_path = listing.rstrip(b"\0").split(b"\t", 1)
        mode, kind, object_id = metadata.decode("ascii").split(" ")
        tree_path = raw_path.decode("utf-8", errors="strict")
    except (ValueError, UnicodeDecodeError) as error:
        raise EngineeringClosureError(
            "Round38 baseline tree record is invalid") from error
    if (mode != "100644" or kind != "blob" or
            HEX40.fullmatch(object_id) is None or tree_path != baseline_path):
        raise EngineeringClosureError(
            "Round38 baseline tree record is invalid")
    payload = _git_capture(
        repository, ["cat-file", "blob", object_id],
        "Round38 baseline blob query")
    if (hashlib.sha256(payload).hexdigest() != baseline["sha256"] or
            len(payload) != baseline["size"]):
        raise EngineeringClosureError(
            "Round38 baseline release blob identity drift")
    document = _strict_document(payload, "Round38 release baseline")
    if (document.get("schema") != "hepta.versioned-source-baseline.v1" or
            document.get("git_head") != product_git_head or
            document.get("version") != release_version):
        raise EngineeringClosureError(
            "Round38 release baseline does not bind product HEAD")
    return {
        "product_git_head": product_git_head,
        "release_git_head": release_git_head,
        "baseline_path": baseline_path,
    }


def _verify_rescue_bundle(
    path: Path,
    ref_manifest: dict[str, Any],
    expected_product_git_head: str,
    expected_release: str,
    expected_baseline: dict[str, Any],
) -> dict[str, Any]:
    refs, head = _bundle_heads(path)
    bundle_snapshot = common.stable_read(
        path, limit=MAX_ARTIFACT_BYTES, capture=False,
        require_trusted_parent=True)
    ref_by_name = {record["name"]: record for record in refs}
    ref_set_sha256 = hashlib.sha256(canonical_json({
        "head": head,
        "refs": refs,
    })).hexdigest()
    if (set(ref_manifest) != {
            "schema", "version", "product_git_head", "release_git_head",
            "release_version", "baseline", "bundle_sha256", "bundle_size",
            "ref_count", "ref_set_sha256", "refs", "head"} or
            ref_manifest["schema"] != "hepta.git-rescue-ref-manifest.v2" or
            ref_manifest["version"] != 2 or
            ref_manifest["product_git_head"] != expected_product_git_head or
            ref_manifest["release_version"] != expected_release or
            ref_manifest["baseline"] != expected_baseline or
            not _is_hex40(expected_product_git_head) or
            not _is_hex40(ref_manifest["release_git_head"]) or
            ref_manifest["bundle_sha256"] != bundle_snapshot.sha256 or
            ref_manifest["bundle_size"] != bundle_snapshot.size or
            ref_manifest["ref_count"] != len(refs) or
            ref_manifest["ref_set_sha256"] != ref_set_sha256 or
            ref_manifest["refs"] != refs or
            ref_manifest["head"] != head or
            head["object"] != ref_manifest["release_git_head"] or
            ref_by_name.get(ROUND38_REF, {}).get("object") !=
            ref_manifest["release_git_head"] or
            OOS_REF not in ref_by_name or
            len(ref_by_name) != len(refs)):
        raise EngineeringClosureError("rescue Git ref manifest drift")
    release_git_head = ref_manifest["release_git_head"]
    with tempfile.TemporaryDirectory(
            prefix="hepta-engineering-bundle-") as temporary:
        bare = Path(temporary) / "verify.git"
        restored = subprocess.run(
            ["git", "clone", "--mirror", "-q", str(path), str(bare)],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            env=GIT_ENVIRONMENT,
            check=False)
        fsck = subprocess.run(
            ["git", "-C", str(bare), "fsck", "--full", "--strict"],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            env=GIT_ENVIRONMENT,
            check=False)
        shown = subprocess.run(
            ["git", "-C", str(bare), "show-ref"],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            env=GIT_ENVIRONMENT,
            text=True,
            encoding="ascii",
            errors="strict",
            check=False)
        restored_refs = sorted(
            ({"object": line.split(" ", 1)[0],
              "name": line.split(" ", 1)[1]}
             for line in shown.stdout.splitlines() if " " in line),
            key=lambda record: record["name"])
        restored_head = subprocess.run(
            ["git", "-C", str(bare), "rev-parse", "HEAD"],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            env=GIT_ENVIRONMENT,
            text=True,
            encoding="ascii",
            errors="strict",
            check=False)
        lineage = (
            _verify_release_commit(
                bare, expected_product_git_head, release_git_head,
                expected_release, expected_baseline)
            if (restored.returncode == 0 and fsck.returncode == 0)
            else None)
    if (restored.returncode != 0 or fsck.returncode != 0 or
            shown.returncode != 0 or restored_head.returncode != 0 or
            restored_refs != refs or restored_head.stdout.strip() !=
            head["object"] or lineage is None):
        raise EngineeringClosureError(
            "rescue Git bundle restore/fsck/ref comparison failed")
    return lineage


def _recovery_current(value: Any, label: str) -> dict[str, Any]:
    if (not isinstance(value, dict) or
            set(value) != {"sha256", "size", "mode"} or
            not _is_hex64(value["sha256"]) or
            type(value["size"]) is not int or
            not 0 <= value["size"] <= RECOVERY_MAX_FILE_BYTES or
            value["mode"] not in RECOVERY_MODES):
        raise EngineeringClosureError(f"{label} is invalid")
    return dict(value)


def _verify_delta_inventory(
    value: dict[str, Any],
    records: list[dict[str, Any]],
    overrides: list[dict[str, str]],
) -> list[dict[str, Any]]:
    inventory: list[dict[str, Any]] = []
    seen: set[str] = set()
    for raw in value["untracked_inventory"]:
        if (not isinstance(raw, dict) or
                set(raw) != {
                    "path", "relation", "current",
                    "baseline_blob_object"}):
            raise EngineeringClosureError(
                "rescue untracked inventory row is invalid")
        relative = _relative(
            raw["path"], "rescue untracked inventory path")
        relation = raw["relation"]
        baseline_object = raw["baseline_blob_object"]
        if (relative in seen or
                relation not in {"exact", "modified", "new"} or
                (relation == "new" and baseline_object is not None) or
                (relation != "new" and not _is_hex40(baseline_object))):
            raise EngineeringClosureError(
                "rescue untracked inventory row is invalid")
        inventory.append({
            "path": relative,
            "relation": relation,
            "current": _recovery_current(
                raw["current"], "rescue untracked inventory current"),
            "baseline_blob_object": baseline_object,
        })
        seen.add(relative)

    by_path = {record["path"]: record for record in records}
    exact_paths: set[str] = set()
    for record in inventory:
        relative = record["path"]
        relation = record["relation"]
        delta = by_path.get(relative)
        if relation == "exact":
            if delta is not None:
                raise EngineeringClosureError(
                    "rescue exact inventory overlaps delta payload")
            exact_paths.add(relative)
            continue
        classification = (
            "modified-after-oos-v6.1"
            if relation == "modified" else "new-after-oos-v6.1")
        if (delta is None or
                delta["classification"] != classification or
                delta["current"] != record["current"] or
                (relation == "modified" and
                 delta["baseline"]["blob_object"] !=
                 record["baseline_blob_object"])):
            raise EngineeringClosureError(
                "rescue untracked inventory does not bind delta payload")

    override_by_path = {
        record["path"]: record["mode"] for record in overrides
    }
    current_by_path = {
        record["path"]: record["current"]["mode"] for record in inventory
    }
    _require_file_prefix_free(seen, "rescue untracked inventory")
    if (len(inventory) != value["untracked_file_count"] or
            len(inventory) != 1120 or
            [record["path"] for record in inventory] !=
            sorted(record["path"] for record in inventory) or
            len(exact_paths) != value["exact_match_excluded_count"] or
            len(exact_paths) != 1084 or
            set(by_path) != seen - exact_paths or
            not set(override_by_path).issubset(exact_paths) or
            any(current_by_path[path] != mode
                for path, mode in override_by_path.items()) or
            sum(record["relation"] == "modified"
                for record in inventory) != 21 or
            sum(record["relation"] == "new"
                for record in inventory) != 15 or
            sum(record["current"]["size"]
                for record in inventory) > RECOVERY_MAX_TOTAL_BYTES or
            value["untracked_inventory_sha256"] !=
            hashlib.sha256(canonical_json(inventory)).hexdigest() or
            value["untracked_inventory_sha256"] !=
            ROUND38_UNTRACKED_INVENTORY_SHA256):
        raise EngineeringClosureError(
            "rescue untracked inventory closure is invalid")
    return inventory


def _verify_delta_manifest(
    value: dict[str, Any],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    if (set(value) != {
            "schema", "version", "scope", "runner_source_path",
            "runner_sha256", "oos_ref", "oos_ref_object", "oos_tree",
            "untracked_file_count", "exact_match_excluded_count",
            "exact_mode_override_count", "exact_mode_overrides_sha256",
            "exact_mode_overrides",
            "untracked_inventory_sha256", "untracked_inventory", "file_count",
            "classification_counts", "files_sha256", "files"} or
            value["schema"] != "hepta.oos-worktree-delta.v3" or
            value["version"] != 3 or
            value["scope"] !=
            "untracked-files-differing-from-oos-tag" or
            value["runner_source_path"] != RECOVERY_RUNNER_SOURCE or
            not _is_hex64(value["runner_sha256"]) or
            value["oos_ref"] != OOS_REF or
            value["oos_ref_object"] != OOS_REF_OBJECT or
            value["oos_tree"] != OOS_TREE_OBJECT or
            value["untracked_file_count"] != 1120 or
            value["exact_match_excluded_count"] != 1084 or
            type(value["exact_mode_override_count"]) is not int or
            not 0 <= value["exact_mode_override_count"] <= 1084 or
            not _is_hex64(value["exact_mode_overrides_sha256"]) or
            not isinstance(value["exact_mode_overrides"], list) or
            not _is_hex64(value["untracked_inventory_sha256"]) or
            not isinstance(value["untracked_inventory"], list) or
            value["classification_counts"] != {
                "modified-after-oos-v6.1": 21,
                "new-after-oos-v6.1": 15,
            } or not isinstance(value["files"], list)):
        raise EngineeringClosureError("rescue delta manifest is incomplete")
    seen: set[str] = set()
    records: list[dict[str, Any]] = []
    for raw in value["files"]:
        if (not isinstance(raw, dict) or
                set(raw) != {
                    "classification", "path", "current", "baseline"}):
            raise EngineeringClosureError("rescue delta row is invalid")
        classification = raw["classification"]
        relative = _relative(raw["path"], "rescue delta path")
        baseline = raw["baseline"]
        if (classification not in {
                "modified-after-oos-v6.1", "new-after-oos-v6.1"} or
                relative in seen):
            raise EngineeringClosureError("rescue delta row is invalid")
        current = _recovery_current(
            raw["current"], "rescue delta current")
        if classification == "new-after-oos-v6.1":
            if baseline is not None:
                raise EngineeringClosureError(
                    "new rescue delta unexpectedly has a baseline")
        elif (not isinstance(baseline, dict) or
                set(baseline) != {
                    "blob_object", "sha256", "size", "mode"} or
                not _is_hex40(baseline["blob_object"]) or
                not _is_hex64(baseline["sha256"]) or
                type(baseline["size"]) is not int or
                not 0 <= baseline["size"] <= RECOVERY_MAX_FILE_BYTES or
                baseline["mode"] not in RECOVERY_BASELINE_MODES):
            raise EngineeringClosureError(
                "modified rescue delta baseline is invalid")
        records.append({
            "classification": classification,
            "path": relative,
            "current": dict(current),
            "baseline": dict(baseline) if baseline is not None else None,
        })
        seen.add(relative)
    override_seen: set[str] = set()
    overrides: list[dict[str, str]] = []
    for raw in value["exact_mode_overrides"]:
        if (not isinstance(raw, dict) or
                set(raw) != {"path", "mode"} or
                raw["mode"] not in RECOVERY_MODES):
            raise EngineeringClosureError(
                "rescue exact mode override is invalid")
        relative = _relative(raw["path"], "rescue mode override path")
        if relative in seen or relative in override_seen:
            raise EngineeringClosureError(
                "rescue exact mode override is duplicated")
        overrides.append({"path": relative, "mode": raw["mode"]})
        override_seen.add(relative)
    if (value["file_count"] != 36 or len(records) != 36 or
            sum(record["classification"] == "modified-after-oos-v6.1"
                for record in records) != 21 or
            sum(record["classification"] == "new-after-oos-v6.1"
                for record in records) != 15 or
            sum(record["current"]["size"] for record in records) >
            RECOVERY_MAX_TOTAL_BYTES or
            sum(
                record["baseline"]["size"]
                for record in records
                if record["baseline"] is not None) >
            RECOVERY_MAX_TOTAL_BYTES or
            [record["path"] for record in records] !=
            sorted(record["path"] for record in records) or
            len(overrides) != value["exact_mode_override_count"] or
            [record["path"] for record in overrides] !=
            sorted(record["path"] for record in overrides) or
            value["exact_mode_overrides_sha256"] !=
            hashlib.sha256(canonical_json(overrides)).hexdigest() or
            value["files_sha256"] !=
            hashlib.sha256(canonical_json(records)).hexdigest()):
        raise EngineeringClosureError(
            "rescue delta manifest count/order closure is invalid")
    inventory = _verify_delta_inventory(value, records, overrides)
    return records, inventory


def _verify_delta_payload(
    path: Path, records: list[dict[str, Any]],
) -> dict[str, tuple[bytes, str]]:
    snapshot = common.stable_read(
        path, limit=256 * 1024 * 1024, capture=True,
        require_trusted_parent=True)
    assert snapshot.data is not None
    expected = {record["path"]: record for record in records}
    seen: set[str] = set()
    captured: dict[str, tuple[bytes, str]] = {}
    try:
        archive = tarfile.open(fileobj=io.BytesIO(snapshot.data), mode="r:")
    except tarfile.TarError as error:
        raise EngineeringClosureError(
            "rescue delta payload is not a plain tar") from error
    with archive:
        for member in archive.getmembers():
            relative = common.normalized_relative_path(
                member.name, "rescue delta member")
            if (not member.isfile() or member.linkname or member.pax_headers or
                    member.uid != 0 or member.gid != 0 or member.mtime != 0 or
                    member.mode not in {
                        0o600, 0o644, 0o664, 0o700, 0o755, 0o775} or
                    relative in seen or
                    relative not in expected):
                raise EngineeringClosureError(
                    "rescue delta payload metadata is invalid")
            extracted = archive.extractfile(member)
            if extracted is None:
                raise EngineeringClosureError(
                    "rescue delta payload member is unreadable")
            data = extracted.read()
            current = expected[relative]["current"]
            if (len(data) != current["size"] or
                    hashlib.sha256(data).hexdigest() != current["sha256"] or
                    f"{member.mode:04o}" != current["mode"]):
                raise EngineeringClosureError(
                    "rescue delta payload content drift")
            seen.add(relative)
            captured[relative] = (data, f"{member.mode:04o}")
    if seen != set(expected):
        raise EngineeringClosureError(
            "rescue delta payload file closure is incomplete")
    return captured


def _read_git_blobs(
    repository: Path, expected_sizes: dict[str, int],
) -> dict[str, bytes]:
    ordered = sorted(expected_sizes)
    if (not ordered or
            any(not _is_hex40(object_id) for object_id in ordered) or
            any(type(size) is not int or
                not 0 <= size <= RECOVERY_MAX_FILE_BYTES
                for size in expected_sizes.values()) or
            sum(expected_sizes.values()) > RECOVERY_MAX_TOTAL_BYTES):
        raise EngineeringClosureError(
            "rescue baseline blob size closure is invalid")
    batch_input = "".join(
        f"{object_id}\n" for object_id in ordered).encode("ascii")
    checked = subprocess.run(
        ["git", "-C", str(repository), "cat-file",
         "--batch-check=%(objectname) %(objecttype) %(objectsize)"],
        input=batch_input,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env=GIT_ENVIRONMENT,
        check=False)
    if (checked.returncode != 0 or checked.stderr or
            len(checked.stdout.splitlines()) != len(ordered)):
        raise EngineeringClosureError(
            "rescue baseline blob size preflight failed")
    for expected_object, raw in zip(ordered, checked.stdout.splitlines()):
        try:
            object_id, kind, raw_size = raw.decode("ascii").split(" ")
            size = int(raw_size)
        except (UnicodeDecodeError, ValueError) as error:
            raise EngineeringClosureError(
                "rescue baseline blob size preflight is invalid") from error
        if (object_id != expected_object or kind != "blob" or
                size != expected_sizes[expected_object]):
            raise EngineeringClosureError(
                "rescue baseline blob size preflight drift")
    run = subprocess.run(
        ["git", "-C", str(repository), "cat-file", "--batch"],
        input=batch_input,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env=GIT_ENVIRONMENT,
        check=False)
    maximum_output = (
        sum(expected_sizes.values()) + len(ordered) * 128)
    if (run.returncode != 0 or run.stderr or
            len(run.stdout) > maximum_output):
        raise EngineeringClosureError(
            "rescue baseline blob batch is unavailable")
    offset = 0
    blobs: dict[str, bytes] = {}
    for expected_object in ordered:
        line_end = run.stdout.find(b"\n", offset)
        if line_end < 0:
            raise EngineeringClosureError(
                "rescue baseline blob batch is truncated")
        try:
            object_id, kind, raw_size = run.stdout[
                offset:line_end].decode("ascii").split(" ")
            size = int(raw_size)
        except (UnicodeDecodeError, ValueError) as error:
            raise EngineeringClosureError(
                "rescue baseline blob batch header is invalid") from error
        content_start = line_end + 1
        content_end = content_start + size
        if (object_id != expected_object or kind != "blob" or
                size != expected_sizes[expected_object] or
                content_end >= len(run.stdout) or
                run.stdout[content_end:content_end + 1] != b"\n"):
            raise EngineeringClosureError(
                "rescue baseline blob batch record is invalid")
        blobs[object_id] = run.stdout[content_start:content_end]
        offset = content_end + 1
    if offset != len(run.stdout):
        raise EngineeringClosureError(
            "rescue baseline blob batch has trailing data")
    return blobs


def _git_tree_leaf_paths(
    repository: Path, revision: str, label: str,
) -> set[str]:
    if not _is_hex40(revision):
        raise EngineeringClosureError(f"{label} identity is invalid")
    listing = subprocess.run(
        ["git", "-C", str(repository), "ls-tree", "-rz", "--full-tree",
         revision],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env=GIT_ENVIRONMENT,
        check=False)
    if listing.returncode != 0 or listing.stderr:
        raise EngineeringClosureError(f"{label} is unavailable")
    paths: set[str] = set()
    for raw in listing.stdout.split(b"\0"):
        if not raw:
            continue
        try:
            metadata, raw_path = raw.split(b"\t", 1)
            _mode, kind, object_id = metadata.decode("ascii").split(" ")
            relative = raw_path.decode("utf-8", errors="strict")
        except (ValueError, UnicodeDecodeError) as error:
            raise EngineeringClosureError(
                f"{label} record is invalid") from error
        relative = _relative(relative, f"{label} path")
        if (kind not in {"blob", "commit"} or
                not _is_hex40(object_id) or relative in paths):
            raise EngineeringClosureError(f"{label} record is invalid")
        paths.add(relative)
    _require_file_prefix_free(paths, label)
    return paths


def _write_all(file_descriptor: int, data: bytes) -> None:
    remaining = memoryview(data)
    while remaining:
        written = os.write(file_descriptor, remaining)
        if written <= 0:
            raise EngineeringClosureError(
                "recovery materialization write made no progress")
        remaining = remaining[written:]


def _recovery_identity(value: os.stat_result) -> tuple[int, ...]:
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


def _expected_recovery_tree(
    inventory: list[dict[str, Any]],
) -> tuple[dict[str, dict[str, Any]], set[str]]:
    expected: dict[str, dict[str, Any]] = {}
    directories = {""}
    for record in inventory:
        if not isinstance(record, dict):
            raise EngineeringClosureError(
                "recovery materialization inventory is invalid")
        relative = _relative(
            record.get("path"), "recovery materialization inventory path")
        current = _recovery_current(
            record.get("current"),
            "recovery materialization inventory current")
        if relative in expected:
            raise EngineeringClosureError(
                "recovery materialization inventory is duplicated")
        expected[relative] = current
        parts = PurePosixPath(relative).parts
        for index in range(1, len(parts)):
            directories.add(PurePosixPath(*parts[:index]).as_posix())
    if len(expected) != 1120:
        raise EngineeringClosureError(
            "recovery materialization inventory is incomplete")
    _require_file_prefix_free(
        set(expected), "recovery materialization inventory")
    return expected, directories


def _read_verified_recovery_file(
    directory_descriptor: int,
    name: str,
    before: os.stat_result,
    current: dict[str, Any],
) -> str:
    if (not stat.S_ISREG(before.st_mode) or
            before.st_uid != os.geteuid() or before.st_nlink != 1 or
            stat.S_IMODE(before.st_mode) != int(current["mode"], 8) or
            before.st_size != current["size"]):
        raise EngineeringClosureError(
            "recovery materialization file metadata drift")
    descriptor = os.open(
        name,
        os.O_RDONLY | os.O_CLOEXEC | getattr(os, "O_NOFOLLOW", 0),
        dir_fd=directory_descriptor,
    )
    digest = hashlib.sha256()
    size = 0
    try:
        opened = os.fstat(descriptor)
        if _recovery_identity(opened) != _recovery_identity(before):
            raise EngineeringClosureError(
                "recovery materialization file identity drift")
        while True:
            chunk = os.read(descriptor, 1024 * 1024)
            if not chunk:
                break
            size += len(chunk)
            if size > RECOVERY_MAX_FILE_BYTES:
                raise EngineeringClosureError(
                    "recovery materialization file is oversized")
            digest.update(chunk)
    finally:
        os.close(descriptor)
    after = os.stat(
        name, dir_fd=directory_descriptor, follow_symlinks=False)
    if (_recovery_identity(after) != _recovery_identity(before) or
            size != current["size"] or
            digest.hexdigest() != current["sha256"]):
        raise EngineeringClosureError(
            "recovery materialization content drift")
    return digest.hexdigest()


def _scan_verified_recovery_directory(
    descriptor: int,
    relative_directory: str,
    expected_files: dict[str, dict[str, Any]],
    expected_directories: set[str],
    seen_files: set[str],
    seen_directories: set[str],
    file_records: list[dict[str, Any]],
) -> None:
    with os.scandir(descriptor) as iterator:
        entries = sorted(iterator, key=lambda item: item.name)
    for entry in entries:
        relative = (
            entry.name if not relative_directory else
            f"{relative_directory}/{entry.name}")
        before = os.stat(
            entry.name, dir_fd=descriptor, follow_symlinks=False)
        if stat.S_ISDIR(before.st_mode):
            if (relative not in expected_directories or
                    before.st_uid != os.geteuid() or
                    stat.S_IMODE(before.st_mode) != 0o700):
                raise EngineeringClosureError(
                    "recovery materialization directory drift")
            child = os.open(
                entry.name,
                os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC |
                getattr(os, "O_NOFOLLOW", 0),
                dir_fd=descriptor,
            )
            try:
                if _recovery_identity(os.fstat(child)) != \
                        _recovery_identity(before):
                    raise EngineeringClosureError(
                        "recovery materialization directory identity drift")
                seen_directories.add(relative)
                _scan_verified_recovery_directory(
                    child, relative, expected_files, expected_directories,
                    seen_files, seen_directories, file_records)
            finally:
                os.close(child)
            after = os.stat(
                entry.name, dir_fd=descriptor, follow_symlinks=False)
            if _recovery_identity(after) != _recovery_identity(before):
                raise EngineeringClosureError(
                    "recovery materialization directory identity drift")
            continue
        current = expected_files.get(relative)
        if current is None:
            raise EngineeringClosureError(
                "recovery materialization file set drift")
        digest = _read_verified_recovery_file(
            descriptor, entry.name, before, current)
        seen_files.add(relative)
        file_records.append({
            "mode": current["mode"],
            "path": relative,
            "sha256": digest,
            "size": current["size"],
        })


def verify_materialized_recovery(
    root: Path,
    inventory: list[dict[str, Any]],
) -> dict[str, Any]:
    """Read-only exact verification of one private recovery file tree."""
    expected_files, expected_directories = _expected_recovery_tree(inventory)
    root = Path(os.path.abspath(os.fspath(root)))
    before = os.lstat(root)
    if (not stat.S_ISDIR(before.st_mode) or
            before.st_uid != os.geteuid() or
            stat.S_IMODE(before.st_mode) != 0o700):
        raise EngineeringClosureError(
            "recovery materialization root is unsafe")
    descriptor = os.open(
        root,
        os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC |
        getattr(os, "O_NOFOLLOW", 0),
    )
    seen_files: set[str] = set()
    seen_directories = {""}
    file_records: list[dict[str, Any]] = []
    try:
        if _recovery_identity(os.fstat(descriptor)) != \
                _recovery_identity(before):
            raise EngineeringClosureError(
                "recovery materialization root identity drift")
        _scan_verified_recovery_directory(
            descriptor, "", expected_files, expected_directories,
            seen_files, seen_directories, file_records)
        opened = os.fstat(descriptor)
    finally:
        os.close(descriptor)
    after = os.lstat(root)
    if (_recovery_identity(after) != _recovery_identity(before) or
            _recovery_identity(opened) != _recovery_identity(before)):
        raise EngineeringClosureError(
            "recovery materialization root identity drift")
    if (seen_files != set(expected_files) or
            seen_directories != expected_directories):
        raise EngineeringClosureError(
            "recovery materialization exact tree is incomplete")
    directory_records = [
        {"mode": "0700", "path": relative}
        for relative in sorted(expected_directories)
    ]
    tree = {
        "directories": directory_records,
        "files": sorted(file_records, key=lambda record: record["path"]),
    }
    return {
        "directory_count": len(directory_records),
        "file_count": len(file_records),
        "inventory_sha256": hashlib.sha256(
            canonical_json(inventory)).hexdigest(),
        "materialized_bytes": sum(
            record["size"] for record in file_records),
        "tree_sha256": hashlib.sha256(canonical_json(tree)).hexdigest(),
    }


def _materialize_recovery(
    root: Path,
    inventory: list[dict[str, Any]],
    contents: dict[str, bytes],
) -> dict[str, Any]:
    root.mkdir(mode=0o700)
    os.chmod(root, 0o700, follow_symlinks=False)
    expected = {record["path"]: record for record in inventory}
    if set(contents) != set(expected):
        raise EngineeringClosureError(
            "recovery materialization content closure is incomplete")
    for relative in sorted(expected):
        record = expected[relative]
        path = root.joinpath(*PurePosixPath(relative).parts)
        parent = root
        for part in PurePosixPath(relative).parts[:-1]:
            parent /= part
            try:
                parent.mkdir(mode=0o700)
            except FileExistsError:
                pass
            opened = os.lstat(parent)
            if (not stat.S_ISDIR(opened.st_mode) or
                    opened.st_nlink < 1):
                raise EngineeringClosureError(
                    "recovery materialization parent is unsafe")
            os.chmod(parent, 0o700, follow_symlinks=False)
            opened = os.lstat(parent)
            if (not stat.S_ISDIR(opened.st_mode) or
                    stat.S_IMODE(opened.st_mode) != 0o700):
                raise EngineeringClosureError(
                    "recovery materialization parent mode is unsafe")
        descriptor = os.open(
            path,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_CLOEXEC |
            os.O_NOFOLLOW,
            int(record["current"]["mode"], 8))
        try:
            _write_all(descriptor, contents[relative])
            os.fchmod(descriptor, int(record["current"]["mode"], 8))
            os.fsync(descriptor)
        finally:
            os.close(descriptor)

    return verify_materialized_recovery(root, inventory)


def _verify_delta_against_bundle(
    bundle: Path,
    ref_manifest: dict[str, Any],
    delta: dict[str, Any],
    payload: Path,
    *,
    materialization_root: Path | None = None,
) -> dict[str, Any]:
    records, inventory = _verify_delta_manifest(delta)
    captured = _verify_delta_payload(payload, records)
    overrides = {
        record["path"]: record["mode"]
        for record in delta["exact_mode_overrides"]
    }
    ref_by_name = {
        record["name"]: record["object"]
        for record in ref_manifest["refs"]
    }
    if ref_by_name.get(OOS_REF) != delta["oos_ref_object"]:
        raise EngineeringClosureError("rescue OOS ref identity drift")
    with tempfile.TemporaryDirectory(
            prefix="hepta-engineering-delta-") as temporary:
        bare = Path(temporary) / "verify.git"
        restored = subprocess.run(
            ["git", "clone", "--mirror", "-q", str(bundle), str(bare)],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            env=GIT_ENVIRONMENT,
            check=False)
        if restored.returncode != 0:
            raise EngineeringClosureError(
                "rescue delta bundle restore failed")
        inventory_paths = {record["path"] for record in inventory}
        release_paths = _git_tree_leaf_paths(
            bare, ref_manifest.get("release_git_head"),
            "rescue release tree")
        _require_disjoint_file_trees(
            inventory_paths, release_paths,
            "rescue inventory and release tree")
        tree_run = subprocess.run(
            ["git", "-C", str(bare), "rev-parse", f"{OOS_REF}^{{tree}}"],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            env=GIT_ENVIRONMENT,
            text=True,
            encoding="ascii",
            errors="strict",
            check=False)
        listing = subprocess.run(
            ["git", "-C", str(bare), "ls-tree", "-rz", "--full-tree",
             delta["oos_tree"]],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            env=GIT_ENVIRONMENT,
            check=False)
        if (tree_run.returncode != 0 or listing.returncode != 0 or
                tree_run.stdout.strip() != delta["oos_tree"]):
            raise EngineeringClosureError(
                "rescue OOS tree identity drift")
        wanted_paths = inventory_paths
        tree: dict[str, tuple[str, str, str]] = {}
        oos_paths: set[str] = set()
        for raw in listing.stdout.split(b"\0"):
            if not raw:
                continue
            try:
                metadata, raw_path = raw.split(b"\t", 1)
                mode, kind, object_id = metadata.decode("ascii").split(" ")
                relative = raw_path.decode("utf-8", errors="strict")
            except (ValueError, UnicodeDecodeError) as error:
                raise EngineeringClosureError(
                    "rescue OOS tree record is invalid") from error
            relative = _relative(relative, "rescue OOS tree path")
            if (relative in oos_paths or kind not in {"blob", "commit"} or
                    not _is_hex40(object_id)):
                raise EngineeringClosureError(
                    "rescue OOS tree record is invalid")
            oos_paths.add(relative)
            if relative not in wanted_paths:
                continue
            if relative in tree:
                raise EngineeringClosureError(
                    "rescue OOS tree paths are duplicated")
            tree[relative] = (
                mode, kind, object_id)
        _require_file_prefix_free(oos_paths, "rescue OOS tree")
        _require_disjoint_file_trees(
            {
                record["path"] for record in inventory
                if record["relation"] == "new"
            },
            oos_paths,
            "rescue new inventory and OOS tree")
        baseline_paths = {
            record["path"] for record in inventory
            if record["relation"] != "new"
        }
        if set(tree) != baseline_paths or any(
                kind != "blob" or mode not in {"100644", "100755"}
                for mode, kind, _object_id in tree.values()):
            raise EngineeringClosureError(
                "rescue inventory baseline tree closure is incomplete")
        delta_by_path = {record["path"]: record for record in records}
        expected_blob_sizes: dict[str, int] = {}
        for inventory_record in inventory:
            if inventory_record["relation"] == "new":
                continue
            relative = inventory_record["path"]
            object_id = tree[relative][2]
            expected_size = (
                inventory_record["current"]["size"]
                if inventory_record["relation"] == "exact"
                else delta_by_path[relative]["baseline"]["size"])
            previous_size = expected_blob_sizes.setdefault(
                object_id, expected_size)
            if previous_size != expected_size:
                raise EngineeringClosureError(
                    "rescue baseline blob size bindings conflict")
        blobs = _read_git_blobs(bare, expected_blob_sizes)
        expected_overrides: dict[str, str] = {}
        materialized_contents: dict[str, bytes] = {}
        for inventory_record in inventory:
            relative = inventory_record["path"]
            relation = inventory_record["relation"]
            if relation == "new":
                if relative in tree:
                    raise EngineeringClosureError(
                        "rescue delta new classification is forged")
                materialized_contents[relative] = captured[relative][0]
                continue
            baseline_tree = tree[relative]
            baseline_mode = (
                "0755" if baseline_tree[0] == "100755" else "0644")
            baseline_data = blobs[baseline_tree[2]]
            if (inventory_record["baseline_blob_object"] !=
                    baseline_tree[2]):
                raise EngineeringClosureError(
                    "rescue inventory baseline object is forged")
            if relation == "exact":
                expected_current = {
                    "sha256": hashlib.sha256(baseline_data).hexdigest(),
                    "size": len(baseline_data),
                    "mode": inventory_record["current"]["mode"],
                }
                if inventory_record["current"] != expected_current:
                    raise EngineeringClosureError(
                        "rescue exact inventory content is forged")
                materialized_contents[relative] = baseline_data
                if inventory_record["current"]["mode"] != baseline_mode:
                    expected_overrides[relative] = (
                        inventory_record["current"]["mode"])
                continue
            delta_record = delta_by_path[relative]
            expected_baseline = {
                "blob_object": baseline_tree[2],
                "sha256": hashlib.sha256(baseline_data).hexdigest(),
                "size": len(baseline_data),
                "mode": baseline_mode,
            }
            current_data, _current_mode = captured[relative]
            if (delta_record["baseline"] != expected_baseline or
                    current_data == baseline_data):
                raise EngineeringClosureError(
                    "rescue delta modified classification is forged")
            materialized_contents[relative] = current_data
        if any(
                recovery_mode == (
                    "0755" if tree[relative][0] == "100755" else "0644")
                for relative, recovery_mode in overrides.items()):
            raise EngineeringClosureError(
                "rescue mode override is redundant")
        if overrides != expected_overrides:
            raise EngineeringClosureError(
                "rescue exact mode override closure is incomplete")
        materialized = _materialize_recovery(
            (Path(temporary) / "materialized"
             if materialization_root is None else materialization_root),
            inventory, materialized_contents)
        if (materialized["file_count"] != len(inventory) or
                materialized["inventory_sha256"] !=
                delta["untracked_inventory_sha256"]):
            raise EngineeringClosureError(
                "recovery materialization verification drift")
    return {
        "delta_file_count": len(records),
        "exact_file_count": sum(
            record["relation"] == "exact" for record in inventory),
        "exact_mode_override_count": len(overrides),
        "inventory_file_count": len(inventory),
        "inventory_sha256": delta["untracked_inventory_sha256"],
        "materialized_bytes": materialized["materialized_bytes"],
        "materialized_directory_count": materialized["directory_count"],
        "materialized_tree_sha256": materialized["tree_sha256"],
    }


def _semantic_verify(
    artifact_root: Path,
    paths: dict[str, Path],
    documents: dict[str, dict[str, Any]],
    expected_product_git_head: str,
    expected_release: str,
) -> dict[str, Any]:
    source_result = source_verifier.verify_bundle(
        paths["strict-source-bundle"], paths["strict-source-manifest"])
    agent_result = agent_verifier.verify(
        paths["agent-os-source-bundle"],
        paths["agent-os-source-manifest"],
        paths["strict-source-bundle"],
        paths["strict-source-manifest"],
        paths["agent-os-source-policy"],
    )
    runtime_result = runtime_verifier.verify_package(
        paths["runtime-package"], paths["runtime-package-manifest"])
    native_arguments = argparse.Namespace(
        bundle_report=paths["native-vm-report"],
        archive=paths["native-vm-rootfs"],
        clean_source_bundle=paths["strict-source-bundle"],
        clean_source_manifest=paths["strict-source-manifest"],
    )
    native_result = native_verifier.verify(native_arguments)

    strict_manifest = documents["strict-source-manifest"]
    baseline = documents["source-baseline-manifest"]
    agent_manifest = documents["agent-os-source-manifest"]
    runtime_manifest = documents["runtime-package-manifest"]
    if (source_result.get("git_head") != expected_product_git_head or
            source_result.get("version") != expected_release or
            strict_manifest.get("git_head") != expected_product_git_head or
            strict_manifest.get("version") != expected_release):
        raise EngineeringClosureError("strict source lineage is inconsistent")
    if (baseline.get("schema") != "hepta.versioned-source-baseline.v1" or
            baseline.get("git_head") != expected_product_git_head or
            baseline.get("version") != expected_release or
            baseline.get("clean_checkout_certified") is not True or
            baseline.get("source_baseline_frozen") is not True or
            baseline.get("release_authorized") is not False or
            baseline.get("paper_authorized") is not False or
            baseline.get("live_authorized") is not False):
        raise EngineeringClosureError("source baseline manifest is invalid")
    baseline_relative = _round_baseline_path(expected_release)
    baseline_record = next(
        (record for record in strict_manifest.get("files", [])
         if isinstance(record, dict) and
         record.get("path") == baseline_relative),
        None)
    baseline_binding, _ = _stable_binding(
        artifact_root,
        paths["source-baseline-manifest"].relative_to(
            artifact_root).as_posix(),
        "source-baseline-cross-lineage",
        capture=False)
    if (not isinstance(baseline_record, dict) or
            baseline_record.get("sha256") != baseline_binding["sha256"] or
            baseline_record.get("size") != baseline_binding["size"] or
            baseline_record.get("mode") != "0644"):
        raise EngineeringClosureError(
            "external and bundled source baselines differ")
    release_baseline = {
        "path": baseline_relative,
        "sha256": baseline_binding["sha256"],
        "size": baseline_binding["size"],
        "mode": "0644",
    }
    parent = agent_manifest.get("parent_strict_source")
    if (agent_manifest.get("schema") != "hepta.agent-os-source-bundle.v1" or
            agent_manifest.get("release_version") != expected_release or
            not isinstance(parent, dict) or
            parent.get("git_head") != expected_product_git_head or
            parent.get("bundle_sha256") != source_result["bundle_sha256"] or
            parent.get("manifest_sha256") !=
            source_result["manifest_sha256"] or
            parent.get("files_sha256") != source_result["files_sha256"]):
        raise EngineeringClosureError("Agent source lineage is inconsistent")
    runtime_source = runtime_result.get("source_ref")
    if (runtime_result.get("release_version") != expected_release or
            runtime_manifest.get("release_version") != expected_release or
            not isinstance(runtime_source, dict) or
            runtime_source.get("git_head") != expected_product_git_head or
            runtime_source.get("bundle_sha256") !=
            "sha256:" + source_result["bundle_sha256"] or
            runtime_source.get("manifest_sha256") !=
            "sha256:" + source_result["manifest_sha256"] or
            runtime_source.get("files_sha256") !=
            "sha256:" + source_result["files_sha256"]):
        raise EngineeringClosureError("runtime source lineage is inconsistent")

    for role, kind in (
            ("test-matrix-report", "matrix"),
            ("sanitizer-report", "sanitizer"),
            ("coverage-report", "coverage"),
            ("runner-identity-report", "runner")):
        _verification_report(artifact_root, documents[role], kind)
    report_by_kind = {
        documents[role]["kind"]: documents[role]
        for role in (
            "test-matrix-report", "sanitizer-report",
            "coverage-report", "runner-identity-report")
    }
    runner_inputs = {
        record["name"]: record
        for record in report_by_kind["runner"]["inputs"]}
    for kind, labels in (
            ("matrix", verification.MATRIX_LABELS),
            ("sanitizer", verification.SANITIZER_LABELS)):
        lane_inputs = {
            record["name"]: record
            for record in report_by_kind[kind]["inputs"]}
        for label in labels:
            name = f"{label}.cmake-cache"
            if lane_inputs.get(name) != runner_inputs.get(name):
                raise EngineeringClosureError(
                    f"{label} CTest and runner cache identities differ")
            if label in verification.SOURCE_ATTESTATION_LABELS:
                source_name = f"{label}.source-manifest"
                if lane_inputs.get(source_name) != runner_inputs.get(
                        source_name):
                    raise EngineeringClosureError(
                        f"{label} CTest and runner source identities differ")
    coverage_inputs = {
        record["name"]: record
        for record in report_by_kind["coverage"]["inputs"]}
    if (coverage_inputs.get("coverage.cmake-cache") !=
            runner_inputs.get("coverage.cmake-cache")):
        raise EngineeringClosureError(
            "coverage execution and runner cache identities differ")
    strict_binding, _ = _stable_binding(
        artifact_root,
        paths["strict-source-manifest"].relative_to(
            artifact_root).as_posix(),
        "strict-source-verification-cross-lineage",
        capture=False)
    agent_binding, _ = _stable_binding(
        artifact_root,
        paths["agent-os-source-manifest"].relative_to(
            artifact_root).as_posix(),
        "agent-source-verification-cross-lineage",
        capture=False)
    identity = lambda record: (
        record.get("sha256"), record.get("size"), record.get("mode"))
    if identity(coverage_inputs.get(
            "coverage.strict-source-manifest", {})) != identity(
            strict_binding):
        raise EngineeringClosureError(
            "coverage strict source manifest identity differs")
    _verify_runner_source_manifests(
        runner_inputs, agent_binding, strict_binding)
    strict_files = {
        record.get("path"): record
        for record in strict_manifest.get("files", [])
        if isinstance(record, dict)
    }
    coverage_policy_record = strict_files.get(
        "policies/heptatrader-code-quality-v1.json")
    if (not isinstance(coverage_policy_record, dict) or
            coverage_inputs.get("coverage.policy", {}).get("sha256") !=
            coverage_policy_record.get("sha256") or
            coverage_inputs.get("coverage.policy", {}).get("size") !=
            coverage_policy_record.get("size")):
        raise EngineeringClosureError(
            "coverage policy is not bound to strict source")
    for kind in ("matrix", "sanitizer"):
        for case in report_by_kind[kind]["cases"]:
            runner = case.get("runner_source")
            helper = case.get("helper_source")
            strict_record = (
                strict_files.get(verification.CTEST_RUNNER_SOURCE))
            strict_helper = strict_files.get(
                verification.VERIFICATION_HELPER_SOURCE)
            if (not isinstance(runner, dict) or
                    not isinstance(strict_record, dict) or
                    runner.get("path") != verification.CTEST_RUNNER_SOURCE or
                    runner.get("sha256") != strict_record.get("sha256") or
                    runner.get("size") != strict_record.get("size") or
                    not isinstance(helper, dict) or
                    not isinstance(strict_helper, dict) or
                    helper.get("path") !=
                    verification.VERIFICATION_HELPER_SOURCE or
                    helper.get("sha256") != strict_helper.get("sha256") or
                    helper.get("size") != strict_helper.get("size")):
                raise EngineeringClosureError(
                    "CTest runner/helper is not bound to strict source")
    coverage_runner = report_by_kind["coverage"]["cases"][0].get(
        "runner_source")
    coverage_helper = report_by_kind["coverage"]["cases"][0].get(
        "helper_source")
    strict_coverage_runner = strict_files.get(
        verification.COVERAGE_RUNNER_SOURCE)
    strict_verification_helper = strict_files.get(
        verification.VERIFICATION_HELPER_SOURCE)
    if (not isinstance(coverage_runner, dict) or
            not isinstance(strict_coverage_runner, dict) or
            coverage_runner.get("path") !=
            verification.COVERAGE_RUNNER_SOURCE or
            coverage_runner.get("sha256") !=
            strict_coverage_runner.get("sha256") or
            coverage_runner.get("size") !=
            strict_coverage_runner.get("size") or
            not isinstance(coverage_helper, dict) or
            not isinstance(strict_verification_helper, dict) or
            coverage_helper.get("path") !=
            verification.VERIFICATION_HELPER_SOURCE or
            coverage_helper.get("sha256") !=
            strict_verification_helper.get("sha256") or
            coverage_helper.get("size") !=
            strict_verification_helper.get("size")):
        raise EngineeringClosureError(
            "coverage runner/helper is not bound to strict source")
    recovery_runner = strict_files.get(RECOVERY_RUNNER_SOURCE)
    delta_manifest = documents["rescue-delta-manifest"]
    if (not isinstance(recovery_runner, dict) or
            delta_manifest.get("runner_source_path") !=
            RECOVERY_RUNNER_SOURCE or
            delta_manifest.get("runner_sha256") !=
            recovery_runner.get("sha256")):
        raise EngineeringClosureError(
            "recovery runner is not bound to strict source")
    for case in report_by_kind["runner"]["cases"]:
        if case["name"] in verification.SOURCE_ATTESTATION_LABELS:
            if case["source"].get("git_head") != expected_product_git_head:
                raise EngineeringClosureError(
                    "runner source lineage is inconsistent")
    layout = documents["workspace-layout-report"]
    if (layout.get("schema") != "hepta.workspace-layout-audit.v2" or
            layout.get("passed") is not True or
            layout.get("scan_complete") is not True or
            not isinstance(layout.get("externalization_complete"), bool)):
        raise EngineeringClosureError("workspace layout report did not pass")
    wrappers = documents["legacy-wrapper-inventory-report"]
    if (wrappers.get("schema") !=
            "hepta.legacy-wrapper-retirement-inventory.v2" or
            wrappers.get("passed") is not True or
            wrappers.get("scan_complete") is not True or
            wrappers.get("deletion_authorized") is not False):
        raise EngineeringClosureError("wrapper inventory boundary is invalid")
    release_lineage = _verify_rescue_bundle(
        paths["rescue-bundle"], documents["rescue-ref-manifest"],
        expected_product_git_head, expected_release, release_baseline)
    _verify_delta_against_bundle(
        paths["rescue-bundle"], documents["rescue-ref-manifest"],
        documents["rescue-delta-manifest"],
        paths["rescue-delta-payload"])
    return {
        "strict_source_files": source_result["file_count"],
        "agent_os_source_files": agent_result["file_count"],
        "runtime_files": runtime_result["file_count"],
        "native_vm_variant": native_result["variant"],
        "product_git_head": expected_product_git_head,
        "release_git_head": release_lineage["release_git_head"],
        "baseline_path": release_lineage["baseline_path"],
        "release_version": expected_release,
    }


def load_artifact_map(path: Path) -> tuple[dict[str, Any], bytes]:
    try:
        snapshot = common.stable_read(
            path, limit=MAX_MAP_BYTES, capture=True,
            require_trusted_parent=True)
    except common.DeliveryClosureError as error:
        raise EngineeringClosureError(
            f"artifact map failed stable read: {error}") from error
    assert snapshot.data is not None
    value = _strict_document(snapshot.data, "engineering artifact map")
    if set(value) != {
            "schema", "version", "round", "release_version",
            "git_head", "artifacts"}:
        raise EngineeringClosureError("artifact map fields are invalid")
    if (value["schema"] != MAP_SCHEMA or value["version"] != 2 or
            type(value["round"]) is not int or value["round"] <= 0 or
            not isinstance(value["release_version"], str) or
            RELEASE.fullmatch(value["release_version"]) is None or
            not value["release_version"].endswith(
                f"-round{value['round']}") or
            not isinstance(value["git_head"], str) or
            HEX40.fullmatch(value["git_head"]) is None or
            not isinstance(value["artifacts"], list)):
        raise EngineeringClosureError("artifact map identity is invalid")
    roles: set[str] = set()
    normalized = []
    for record in value["artifacts"]:
        if not isinstance(record, dict) or set(record) != {"role", "path"}:
            raise EngineeringClosureError("artifact map record is invalid")
        role = record["role"]
        if role not in REQUIRED_ROLES or role in roles:
            raise EngineeringClosureError(
                "artifact map role closure is invalid")
        roles.add(role)
        normalized.append({
            "role": role,
            "path": _relative(record["path"], f"{role} path"),
        })
    if roles != set(REQUIRED_ROLES):
        raise EngineeringClosureError(
            "artifact map does not contain the required role closure")
    if normalized != sorted(normalized, key=lambda item: item["role"]):
        raise EngineeringClosureError("artifact map records are not sorted")
    return value, snapshot.data


def build(
    artifact_root: Path,
    artifact_map_path: Path,
    generated_at: str,
) -> dict[str, Any]:
    try:
        root = verification._protected_root(artifact_root)
    except verification.EvidenceError as error:
        raise EngineeringClosureError(
            "engineering artifact root is unsafe") from error
    root_metadata = root.lstat()
    if (not stat.S_ISDIR(root_metadata.st_mode) or
            stat.S_IMODE(root_metadata.st_mode) & 0o022 or
            stat.S_IMODE(root_metadata.st_mode) & 0o7000):
        raise EngineeringClosureError(
            "artifact root is not a protected directory")
    artifact_map, map_bytes = load_artifact_map(artifact_map_path)
    artifacts: list[dict[str, Any]] = []
    documents: dict[str, dict[str, Any]] = {}
    paths: dict[str, Path] = {}
    for record in artifact_map["artifacts"]:
        role = record["role"]
        binding, data = _stable_binding(
            root, record["path"], role, capture=role in JSON_ROLES)
        artifacts.append(binding)
        paths[role] = root.joinpath(
            *PurePosixPath(record["path"]).parts)
        if role in JSON_ROLES:
            assert data is not None
            documents[role] = _strict_document(data, role)
    semantic = _semantic_verify(
        root, paths, documents,
        artifact_map["git_head"], artifact_map["release_version"])
    if (not isinstance(semantic, dict) or
            semantic.get("product_git_head") != artifact_map["git_head"] or
            not _is_hex40(semantic.get("release_git_head")) or
            semantic["release_git_head"] == artifact_map["git_head"] or
            semantic.get("baseline_path") !=
            _round_baseline_path(artifact_map["release_version"])):
        raise EngineeringClosureError(
            "engineering dual-head semantic lineage is invalid")
    map_binding = next(
        item for item in artifacts
        if item["role"] == "engineering-artifact-map")
    if (map_binding["sha256"] != hashlib.sha256(map_bytes).hexdigest() or
            paths["engineering-artifact-map"].resolve(strict=True) !=
            artifact_map_path.resolve(strict=True)):
        raise EngineeringClosureError(
            "engineering artifact map is not self-bound")
    try:
        generated_at = common._normalize_generated_at(generated_at)
    except common.DeliveryClosureError as error:
        raise EngineeringClosureError(
            "generated_at is not normalized UTC RFC3339") from error
    internal_open_items: list[str] = []
    layout = documents["workspace-layout-report"]
    if layout.get("externalization_complete") is not True:
        internal_open_items.append("workspace-storage-externalization")
    wrappers = documents["legacy-wrapper-inventory-report"]
    if wrappers.get("migration_complete") is not True:
        internal_open_items.append("legacy-wrapper-migration")
    return {
        "schema": SCHEMA,
        "version": 2,
        "project_id": PROJECT_ID,
        "round": artifact_map["round"],
        "release_version": artifact_map["release_version"],
        "generated_at": generated_at,
        "status": STATUS,
        "passed": True,
        "passed_scope": PASSED_SCOPE,
        "production_passed": PRODUCTION_PASSED,
        "release_authorized": RELEASE_AUTHORIZED,
        "source": {
            "product_git_head": artifact_map["git_head"],
            "release_git_head": semantic["release_git_head"],
            "artifact_map_sha256": hashlib.sha256(map_bytes).hexdigest(),
        },
        "artifact_roles": list(REQUIRED_ROLES),
        "artifacts": artifacts,
        "semantic_summary": semantic,
        "safety_boundaries": dict(SAFETY_BOUNDARIES),
        "internal_open_items": internal_open_items,
        "external_blockers": list(EXTERNAL_BLOCKERS),
    }


def write_private(path: Path, value: dict[str, Any]) -> None:
    try:
        common.write_private_json(
            path,
            value,
            max_bytes=MAX_MAP_BYTES,
        )
    except common.DeliveryClosureError as error:
        raise EngineeringClosureError(
            f"failed to publish engineering closure: {error}") from error


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--artifact-root", type=Path, required=True)
    parser.add_argument("--artifact-map", type=Path, required=True)
    parser.add_argument("--generated-at")
    parser.add_argument("--output", type=Path, required=True)
    arguments = parser.parse_args()
    generated_at = arguments.generated_at or datetime.now(
        timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
    closure = build(
        arguments.artifact_root, arguments.artifact_map, generated_at)
    write_private(arguments.output, closure)
    print(
        f"PASS: {SCHEMA} round={closure['round']} "
        f"status={closure['status']}")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except EngineeringClosureError as error:
        print(f"FAIL: {error}", file=sys.stderr)
        raise SystemExit(1)
