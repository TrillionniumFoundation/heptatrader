#!/usr/bin/env python3
"""Audit the five-layer HeptaTrader workspace boundary."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import stat
import subprocess
import sys
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from hepta_ops import agent_os_source


POLICY_SCHEMA = "hepta.workspace-layout-policy.v1"
REPORT_SCHEMA = "hepta.workspace-layout-audit.v2"
SOURCE_POLICY = Path("policies/heptatrader-agent-os-source-v2.json")
ROOT_WRAPPER = re.compile(
    r"^(?:build|check|install|launch|provision|run|status|stop|verify)_"
    r"[A-Za-z0-9_.-]+\.sh$")
NESTED_CACHE_PATHS = (
    "HeptaTrade/x64",
    "HeptaTrade/HeptaTrader/x64",
    "scripts/__pycache__",
    "bin",
    ".pytest_cache",
    "Tools/Log",
)


class WorkspaceLayoutError(RuntimeError):
    pass


def _reject_constant(value: str) -> None:
    raise ValueError(f"non-finite JSON value is forbidden: {value}")


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate JSON key: {key}")
        result[key] = value
    return result


def _safe_json(path: Path) -> tuple[dict[str, Any], bytes]:
    before = path.lstat()
    if (stat.S_ISLNK(before.st_mode) or not stat.S_ISREG(before.st_mode) or
            before.st_nlink != 1 or before.st_mode & 0o022):
        raise WorkspaceLayoutError("policy must be a protected regular file")
    payload = path.read_bytes()
    after = path.lstat()
    if (
        before.st_dev, before.st_ino, before.st_mode, before.st_nlink,
        before.st_uid, before.st_gid, before.st_size, before.st_mtime_ns,
        before.st_ctime_ns,
    ) != (
        after.st_dev, after.st_ino, after.st_mode, after.st_nlink,
        after.st_uid, after.st_gid, after.st_size, after.st_mtime_ns,
        after.st_ctime_ns,
    ):
        raise WorkspaceLayoutError("policy changed while reading")
    try:
        document = json.loads(
            payload.decode("utf-8", errors="strict"),
            object_pairs_hook=_unique_object,
            parse_constant=_reject_constant)
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as error:
        raise WorkspaceLayoutError("policy is not strict UTF-8 JSON") from error
    if not isinstance(document, dict):
        raise WorkspaceLayoutError("policy root must be an object")
    return document, payload


def _git_paths(root: Path, *arguments: str) -> list[str]:
    result = subprocess.run(
        ["git", *arguments, "-z"],
        cwd=root,
        capture_output=True)
    if result.returncode != 0:
        if arguments == ("ls-files",):
            return _source_bundle_paths(root)
        if arguments == (
                "ls-files", "--others", "--exclude-standard"):
            _source_bundle_paths(root)
            return []
        raise WorkspaceLayoutError("Git workspace inventory failed")
    paths = [
        value.decode("utf-8", errors="surrogateescape")
        for value in result.stdout.split(b"\0") if value
    ]
    for path in paths:
        normalized = PurePosixPath(path)
        if (normalized.is_absolute() or normalized.as_posix() != path or
                any(part in {"", ".", ".."} for part in normalized.parts)):
            raise WorkspaceLayoutError(f"git returned an unsafe path: {path}")
    return sorted(paths)


def _source_bundle_paths(root: Path) -> list[str]:
    strict = root / ".hepta/source-bundle-manifest.json"
    agent = root / ".hepta/agent-os-source-manifest.json"
    markers = [path for path in (strict, agent) if path.exists()]
    if len(markers) != 1:
        raise WorkspaceLayoutError(
            "neither Git nor one source-bundle inventory is available")
    document, _ = _safe_json(markers[0])
    if document.get("schema") not in {
            "hepta.clean-source-bundle.v2",
            "hepta.agent-os-source-bundle.v1"}:
        raise WorkspaceLayoutError("source-bundle inventory schema is invalid")
    records = document.get("files")
    if not isinstance(records, list):
        raise WorkspaceLayoutError("source-bundle inventory is invalid")
    paths: list[str] = []
    for record in records:
        if not isinstance(record, dict) or not isinstance(
                record.get("path"), str):
            raise WorkspaceLayoutError("source-bundle file record is invalid")
        path = record["path"]
        normalized = PurePosixPath(path)
        if (normalized.is_absolute() or normalized.as_posix() != path or
                any(part in {"", ".", ".."} for part in normalized.parts)):
            raise WorkspaceLayoutError(
                f"source bundle returned an unsafe path: {path}")
        paths.append(path)
    if len(paths) != len(set(paths)):
        raise WorkspaceLayoutError("source-bundle paths are duplicated")
    return sorted(paths)


def _top(path: str) -> str:
    return PurePosixPath(path).parts[0]


def _layer(path: str, source_policy: agent_os_source.SourcePolicy) -> str:
    top = _top(path)
    if top in {"runtime-logs", "evidence-indexes", "evidence-requests"}:
        return "external-evidence-store"
    if (top.startswith("build") or top in {".pytest_cache"} or
            path.startswith("third_party/ctp/6.7.7/include/")):
        return "external-vendor-build-cache"
    if (top in {
            "HeptaSimulator", "HeptaStrategy", "Interface", "Tools",
            "_backups"} or path.startswith("compat/unsafe-direct-broker/") or
            ("/" not in path and ROOT_WRAPPER.fullmatch(path))):
        return "legacy-compat"
    if source_policy.selects(path):
        return "agent-os-product"
    if top in {
            ".github", "compat", "docs", "hepta_ops", "ops", "policies",
            "scripts", "tests", "third_party"}:
        return "ops-evidence-tooling"
    return "legacy-compat"


def _tree_bytes(path: Path, errors: list[str] | None = None) -> int:
    if errors is None:
        errors = []
    if not path.exists() or path.is_symlink():
        if path.is_symlink():
            errors.append(f"symlink tree skipped: {path}")
        return 0
    total = 0
    def onerror(error: OSError) -> None:
        errors.append(f"tree scan failed: {error.filename or path}")
    for directory, names, files in os.walk(
            path, followlinks=False, onerror=onerror):
        for name in names:
            candidate = Path(directory) / name
            if candidate.is_symlink():
                errors.append(f"symlink entry skipped: {candidate}")
        names[:] = [
            name for name in names
            if not (Path(directory) / name).is_symlink()
        ]
        for name in files:
            candidate = Path(directory) / name
            try:
                metadata = candidate.lstat()
            except OSError:
                errors.append(f"file stat failed: {candidate}")
                continue
            if stat.S_ISLNK(metadata.st_mode):
                errors.append(f"symlink file skipped: {candidate}")
            elif stat.S_ISREG(metadata.st_mode):
                total += metadata.st_size
            else:
                errors.append(f"non-regular cache entry skipped: {candidate}")
    return total


def audit(
    root: Path,
    policy_path: Path,
    source_policy_path: Path | None = None,
) -> dict[str, Any]:
    policy, policy_bytes = _safe_json(policy_path)
    if source_policy_path is None:
        source_policy_path = root / SOURCE_POLICY
    source_policy = agent_os_source.load_policy(source_policy_path)
    if set(policy) != {
            "schema", "version", "layers", "budgets", "retirement"}:
        raise WorkspaceLayoutError("policy fields do not exactly match schema")
    if policy["schema"] != POLICY_SCHEMA or policy["version"] != 1:
        raise WorkspaceLayoutError("unsupported workspace layout policy")
    expected_layers = {
        "agent-os-product", "legacy-compat", "ops-evidence-tooling",
        "external-evidence-store", "external-vendor-build-cache",
    }
    if set(policy["layers"]) != expected_layers:
        raise WorkspaceLayoutError("policy layer closure is invalid")
    budgets = policy["budgets"]
    expected_budgets = {
        "tracked_root_legacy_wrappers",
        "untracked_root_legacy_wrappers",
        "generated_compatibility_wrappers",
        "runtime_logs_warning_bytes",
        "build_cache_warning_bytes",
        "nested_cache_warning_bytes",
    }
    if (not isinstance(budgets, dict) or set(budgets) != expected_budgets or
            any(not isinstance(value, int) or value < 0
                for value in budgets.values())):
        raise WorkspaceLayoutError("policy budgets are invalid")

    tracked = _git_paths(root, "ls-files")
    untracked = _git_paths(root, "ls-files", "--others", "--exclude-standard")
    counts = {
        layer: {"tracked": 0, "untracked": 0}
        for layer in sorted(expected_layers)
    }
    for state, paths in (("tracked", tracked), ("untracked", untracked)):
        for path in paths:
            counts[_layer(path, source_policy)][state] += 1

    tracked_root = [
        path for path in tracked
        if "/" not in path and ROOT_WRAPPER.fullmatch(path)
    ]
    untracked_root = [
        path for path in untracked
        if "/" not in path and ROOT_WRAPPER.fullmatch(path)
    ]
    generated = [
        path for path in tracked
        if path.startswith("compat/hepta-ops-generated/") and
        path.endswith(".sh")
    ]
    tracked_volatile = [
        path for path in tracked
        if _layer(path, source_policy) in {
            "external-evidence-store", "external-vendor-build-cache"}
    ]
    violations: list[str] = []
    if tracked_volatile:
        violations.append("volatile files entered Git")
    if len(tracked_root) > budgets["tracked_root_legacy_wrappers"]:
        violations.append("tracked root legacy wrapper budget grew")
    if len(untracked_root) > budgets["untracked_root_legacy_wrappers"]:
        violations.append("untracked root legacy wrapper budget grew")
    if len(generated) > budgets["generated_compatibility_wrappers"]:
        violations.append("generated compatibility wrapper budget grew")

    scan_errors: list[str] = []
    runtime_bytes = _tree_bytes(root / "runtime-logs", scan_errors)
    build_bytes = sum(
        _tree_bytes(path, scan_errors)
        for path in root.iterdir()
        if path.is_dir() and not path.is_symlink() and
        path.name.startswith("build"))
    nested_cache_breakdown = {
        relative: _tree_bytes(root / relative, scan_errors)
        for relative in NESTED_CACHE_PATHS
    }
    nested_cache_bytes = sum(nested_cache_breakdown.values())
    warnings: list[str] = []
    if runtime_bytes > budgets["runtime_logs_warning_bytes"]:
        warnings.append("runtime logs exceed externalization budget")
    if build_bytes > budgets["build_cache_warning_bytes"]:
        warnings.append("build trees exceed externalization budget")
    if nested_cache_bytes > budgets["nested_cache_warning_bytes"]:
        warnings.append("nested caches exceed externalization budget")
    scan_events = sorted(set(scan_errors))
    scan_skips = [
        value for value in scan_events
        if value.startswith(("symlink ", "non-regular "))]
    scan_errors = [
        value for value in scan_events
        if value not in scan_skips]
    scan_complete = not scan_errors
    externalization_complete = scan_complete and not warnings

    return {
        "schema": REPORT_SCHEMA,
        "version": 1,
        "passed": not violations and externalization_complete,
        "scan_complete": scan_complete,
        "externalization_complete": externalization_complete,
        "layers": counts,
        "wrapper_inventory": {
            "tracked_root_legacy": len(tracked_root),
            "untracked_root_legacy": len(untracked_root),
            "generated_compatibility": len(generated),
        },
        "volatile_storage": {
            "runtime_logs_bytes": runtime_bytes,
            "build_cache_bytes": build_bytes,
            "nested_cache_bytes": nested_cache_bytes,
            "nested_cache_breakdown": nested_cache_breakdown,
        },
        "violations": violations,
        "warnings": warnings,
        "scan_errors": scan_errors,
        "scan_skips": scan_skips,
        "policy": {
            "path": policy_path.relative_to(root).as_posix(),
            "sha256": hashlib.sha256(policy_bytes).hexdigest(),
        },
        "agent_os_source_policy": {
            "path": source_policy_path.relative_to(root).as_posix(),
            "sha256": source_policy.sha256,
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--root", type=Path,
        default=Path(__file__).resolve().parents[1])
    parser.add_argument(
        "--policy", type=Path,
        default=Path("policies/heptatrader-workspace-layout-v1.json"))
    parser.add_argument(
        "--agent-os-source-policy", type=Path,
        default=SOURCE_POLICY)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--strict", action="store_true")
    arguments = parser.parse_args()
    root = arguments.root.resolve(strict=True)
    policy = arguments.policy
    if not policy.is_absolute():
        policy = root / policy
    source_policy = arguments.agent_os_source_policy
    if not source_policy.is_absolute():
        source_policy = root / source_policy
    report = audit(
        root,
        policy.resolve(strict=True),
        source_policy.resolve(strict=True))
    payload = json.dumps(
        report, ensure_ascii=True, indent=2, sort_keys=True) + "\n"
    if arguments.output is None:
        print(payload, end="")
    else:
        output = arguments.output
        if not output.is_absolute():
            output = root / output
        output.parent.mkdir(parents=True, exist_ok=True)
        descriptor = os.open(
            output, os.O_WRONLY | os.O_CREAT | os.O_EXCL |
            getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0),
            0o600)
        try:
            view = memoryview(payload.encode("utf-8"))
            while view:
                written = os.write(descriptor, view)
                if written <= 0:
                    raise WorkspaceLayoutError(
                        "workspace report write made no progress")
                view = view[written:]
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
        directory_descriptor = os.open(
            output.parent,
            os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) |
            getattr(os, "O_CLOEXEC", 0))
        try:
            os.fsync(directory_descriptor)
        finally:
            os.close(directory_descriptor)
    if arguments.strict and not report["passed"]:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
