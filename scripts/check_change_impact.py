#!/usr/bin/env python3
"""Derive a deterministic module/review impact set for an exact PR candidate.

The checker treats the module manifests and physical ownership registry as the
source of truth.  It never uses filename heuristics to *reduce* verification:
unknown, shared, contract, build or governance changes expand to every active
module.  This makes the generated evidence suitable for a merge-candidate gate
without turning selective testing into a way to skip downstream owners.
"""

from __future__ import annotations

import argparse
from collections import deque
import hashlib
import json
from pathlib import Path, PurePosixPath
import subprocess
import sys
from typing import Any, Iterable

from hepta_module_boundaries import (
    ACTIVE_LIFECYCLES,
    active_source_files,
    load_json,
    load_modules,
    load_source_ownership,
    parse_source_rules,
    resolve_physical_owner,
)

ROOT = Path(__file__).resolve().parents[1]
TEST_MATRIX_REL = "docs/verification/test-matrix-v2.json"
GLOBAL_PREFIXES = (
    ".github/",
    "adapters/",
    "cmake/",
    "docs/architecture/",
    "docs/contracts/",
    "docs/development/",
    "docs/governance/",
    "docs/product/",
    "docs/program/",
    "docs/research/",
    "docs/verification/",
    "packaging/",
    "research/",
    "schemas/",
    "scripts/",
    "systemd/",
    "tests/",
)
GLOBAL_FILES = {
    "CMakeLists.txt",
    "README.md",
    "docs/document-registry-v2.json",
    "docs/modules/module-manifest-schema-v2.json",
    "docs/modules/module-registry-v2.json",
    "docs/modules/source-ownership-registry-v1.json",
}


def _git(*arguments: str, root: Path = ROOT) -> str:
    completed = subprocess.run(
        ["git", *arguments],
        cwd=root,
        check=False,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
    )
    if completed.returncode != 0:
        detail = completed.stderr.strip() or completed.stdout.strip()
        raise ValueError(f"git {' '.join(arguments)} failed: {detail}")
    return completed.stdout.strip()


def _canonical_path(raw: str) -> str:
    if not raw or "\x00" in raw or "\\" in raw:
        raise ValueError(f"invalid changed path: {raw!r}")
    path = PurePosixPath(raw)
    if path.is_absolute() or any(part in {"", ".", ".."} for part in path.parts):
        raise ValueError(f"non-canonical changed path: {raw!r}")
    return path.as_posix()


def changed_paths(base: str, head: str, root: Path = ROOT) -> list[str]:
    output = _git(
        "diff", "--name-only", "--diff-filter=ACMRD", f"{base}...{head}",
        root=root,
    )
    paths = sorted({_canonical_path(line) for line in output.splitlines() if line})
    if not paths:
        raise ValueError("merge candidate contains no changed paths")
    return paths


def verify_merge_candidate(
    base: str,
    head: str,
    merge_candidate: str,
    root: Path = ROOT,
) -> None:
    resolved = _git("rev-parse", merge_candidate, root=root)
    if resolved != merge_candidate:
        raise ValueError(
            f"merge candidate ref drift: expected {merge_candidate}, got {resolved}"
        )
    parents = _git("rev-list", "--parents", "-n", "1", merge_candidate, root=root).split()
    if len(parents) != 3:
        raise ValueError("exact pull-request merge candidate must have two parents")
    if parents[1] != base or parents[2] != head:
        raise ValueError(
            "merge candidate parent mismatch: "
            f"parents={parents[1:]} expected={[base, head]}"
        )


def build_reverse_dependencies(
    modules: dict[str, dict[str, Any]],
) -> dict[str, set[str]]:
    active = {
        module_id
        for module_id, manifest in modules.items()
        if manifest.get("lifecycle") in ACTIVE_LIFECYCLES
    }
    reverse = {module_id: set() for module_id in active}
    for consumer in active:
        allowed = modules[consumer].get("allowed_dependencies", [])
        if not isinstance(allowed, list):
            continue
        for pattern in allowed:
            if not isinstance(pattern, str):
                continue
            dependencies: Iterable[str]
            if pattern.endswith(".*"):
                prefix = pattern[:-1]
                dependencies = (
                    module_id for module_id in active
                    if module_id.startswith(prefix)
                )
            elif pattern in active:
                dependencies = (pattern,)
            else:
                dependencies = ()
            for dependency in dependencies:
                reverse[dependency].add(consumer)
    return reverse


def reverse_closure(
    direct: Iterable[str],
    reverse: dict[str, set[str]],
) -> set[str]:
    impacted = set(direct)
    queue: deque[str] = deque(sorted(impacted))
    while queue:
        dependency = queue.popleft()
        for consumer in sorted(reverse.get(dependency, ())):
            if consumer not in impacted:
                impacted.add(consumer)
                queue.append(consumer)
    return impacted


def _manifest_path_map(modules: dict[str, dict[str, Any]]) -> dict[str, str]:
    result: dict[str, str] = {}
    for module_id, manifest in modules.items():
        path = manifest.get("__manifest_path")
        if isinstance(path, str):
            result[path] = module_id
    return result


def _is_governed_source(path: str, source_extensions: set[str]) -> bool:
    return path.startswith("HeptaTrade/") and PurePosixPath(path).suffix.lower() in source_extensions


def derive_direct_impact(
    paths: Iterable[str],
    modules: dict[str, dict[str, Any]],
    ownership: dict[str, Any],
    root: Path = ROOT,
) -> tuple[set[str], bool, list[str]]:
    active = {
        module_id
        for module_id, manifest in modules.items()
        if manifest.get("lifecycle") in ACTIVE_LIFECYCLES
    }
    errors: list[str] = []
    rules = parse_source_rules(root, ownership, errors)
    if errors:
        raise ValueError("; ".join(errors))
    extensions = {
        value.lower()
        for value in ownership.get(
            "source_extensions", [".c", ".cc", ".cpp", ".h", ".hpp"]
        )
        if isinstance(value, str)
    }
    manifest_paths = _manifest_path_map(modules)
    direct: set[str] = set()
    global_impact = False
    globally_classified: list[str] = []

    for raw in paths:
        path = _canonical_path(raw)
        if path in manifest_paths:
            module_id = manifest_paths[path]
            if module_id in active:
                direct.add(module_id)
            continue
        if _is_governed_source(path, extensions):
            owner, ambiguous = resolve_physical_owner(path, rules)
            if owner is None:
                labels = ",".join(rule.rule_id for rule in ambiguous) or "none"
                raise ValueError(
                    f"changed governed source has no unique owner: {path} ({labels})"
                )
            if owner not in active:
                raise ValueError(
                    f"changed governed source owner is not active: {path} -> {owner}"
                )
            direct.add(owner)
            continue
        if path in GLOBAL_FILES or path.endswith("/CMakeLists.txt") or any(
            path.startswith(prefix) for prefix in GLOBAL_PREFIXES
        ):
            global_impact = True
            globally_classified.append(path)
            continue
        # Unknown repository surfaces are conservatively global.  They remain
        # visible in evidence so a future classifier can narrow them only with
        # an explicit reviewed rule.
        global_impact = True
        globally_classified.append(path)

    if global_impact:
        direct.update(active)
    if not direct:
        raise ValueError("changed paths produced an empty active-module impact set")
    return direct, global_impact, sorted(globally_classified)


def validate_verification_coverage(
    impacted: Iterable[str],
    modules: dict[str, dict[str, Any]],
    root: Path = ROOT,
) -> list[str]:
    matrix = load_json(root / TEST_MATRIX_REL)
    checks = matrix.get("checks") if isinstance(matrix, dict) else None
    if not isinstance(checks, list):
        raise ValueError("test matrix checks must be an array")
    check_states = {
        item.get("id"): item.get("state")
        for item in checks
        if isinstance(item, dict) and isinstance(item.get("id"), str)
    }
    selected: set[str] = set()
    for module_id in sorted(set(impacted)):
        verification = modules[module_id].get("verification")
        if not isinstance(verification, list) or not verification:
            raise ValueError(f"impacted module has no verification contract: {module_id}")
        for check_id in verification:
            if not isinstance(check_id, str) or check_id not in check_states:
                raise ValueError(
                    f"impacted module references unknown verification: {module_id}:{check_id}"
                )
            if check_states[check_id] == "planned":
                raise ValueError(
                    f"impacted active module still depends on planned verification: "
                    f"{module_id}:{check_id}"
                )
            selected.add(check_id)
    return sorted(selected)


def canonical_evidence(value: dict[str, Any]) -> dict[str, Any]:
    payload = json.dumps(value, sort_keys=True, separators=(",", ":"))
    result = dict(value)
    result["evidence_digest"] = "sha256:" + hashlib.sha256(
        payload.encode("utf-8")
    ).hexdigest()
    return result


def analyze(
    base: str,
    head: str,
    merge_candidate: str | None,
    root: Path = ROOT,
) -> dict[str, Any]:
    if merge_candidate:
        verify_merge_candidate(base, head, merge_candidate, root)
    errors: list[str] = []
    modules, _ = load_modules(root, errors)
    ownership = load_source_ownership(root, errors)
    if errors:
        raise ValueError("; ".join(errors))
    paths = changed_paths(base, head, root)
    direct, global_impact, global_paths = derive_direct_impact(
        paths, modules, ownership, root
    )
    impacted = reverse_closure(direct, build_reverse_dependencies(modules))
    verification = validate_verification_coverage(impacted, modules, root)
    return canonical_evidence({
        "schema": "heptatrader.change-impact.v1",
        "base_sha": base,
        "head_sha": head,
        "merge_candidate_sha": merge_candidate or "",
        "changed_paths": paths,
        "global_impact": global_impact,
        "global_paths": global_paths,
        "direct_modules": sorted(direct),
        "impacted_modules": sorted(impacted),
        "verification_ids": verification,
    })


def self_test() -> None:
    reverse = {
        "hepta.a": {"hepta.b"},
        "hepta.b": {"hepta.c"},
        "hepta.c": set(),
    }
    assert reverse_closure({"hepta.a"}, reverse) == {
        "hepta.a", "hepta.b", "hepta.c"
    }
    assert reverse_closure({"hepta.c"}, reverse) == {"hepta.c"}
    evidence = canonical_evidence({
        "schema": "heptatrader.change-impact.v1",
        "direct_modules": ["hepta.a"],
    })
    assert evidence["evidence_digest"].startswith("sha256:")
    assert len(evidence["evidence_digest"]) == 71
    assert _canonical_path("HeptaTrade/execution/example.cpp") == (
        "HeptaTrade/execution/example.cpp"
    )
    for invalid in ("../escape", "/absolute", "a\\b", ""):
        try:
            _canonical_path(invalid)
        except ValueError:
            pass
        else:
            raise AssertionError(f"unsafe path accepted: {invalid!r}")
    print("[CHANGE-IMPACT] SELF-TEST PASS")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base")
    parser.add_argument("--head")
    parser.add_argument("--merge-candidate")
    parser.add_argument("--output", type=Path)
    parser.add_argument("--self-test", action="store_true")
    arguments = parser.parse_args()
    if arguments.self_test:
        self_test()
        return 0
    if not arguments.base or not arguments.head:
        parser.error("--base and --head are required unless --self-test is used")
    try:
        evidence = analyze(
            arguments.base,
            arguments.head,
            arguments.merge_candidate,
        )
    except (OSError, UnicodeError, ValueError, json.JSONDecodeError) as error:
        print(f"[CHANGE-IMPACT] {error}", file=sys.stderr)
        return 1
    rendered = json.dumps(evidence, sort_keys=True, separators=(",", ":")) + "\n"
    if arguments.output:
        arguments.output.parent.mkdir(parents=True, exist_ok=True)
        arguments.output.write_text(rendered, encoding="utf-8")
    sys.stdout.write(rendered)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
