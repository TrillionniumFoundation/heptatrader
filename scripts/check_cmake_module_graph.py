#!/usr/bin/env python3
"""Bind the configured CMake target/source/dependency graph to ModuleManifest V3."""
from __future__ import annotations

import argparse
from collections import defaultdict
import json
from pathlib import Path
import sys
from typing import Any

from hepta_module_boundaries import (
    ACTIVE_LIFECYCLES,
    SOURCE_OWNERSHIP_REL,
    canonical_relative_path,
    dependency_allowed,
    load_json,
    load_modules,
    load_source_ownership,
    parse_source_rules,
    resolve_physical_owner,
)

ROOT = Path(__file__).resolve().parents[1]
QUERY = Path(".cmake/api/v1/query/codemodel-v2")


def prepare(build_dir: Path) -> int:
    query = build_dir.resolve() / QUERY
    query.parent.mkdir(parents=True, exist_ok=True)
    query.write_text("", encoding="utf-8")
    print(f"[CMAKE-MODULE-GRAPH] query prepared: {query}")
    return 0


def _load(path: Path, errors: list[str]) -> Any:
    try:
        return load_json(path)
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        errors.append(f"{path}: invalid JSON: {exc}")
        return None


def _latest_reply(build_dir: Path, errors: list[str]) -> tuple[Path, dict[str, Any]] | None:
    reply_dir = build_dir / ".cmake/api/v1/reply"
    indexes = sorted(reply_dir.glob("index-*.json"), key=lambda p: p.stat().st_mtime_ns)
    if not indexes:
        errors.append(f"CMake File API reply is missing under {reply_dir}")
        return None
    index_path = indexes[-1]
    index = _load(index_path, errors)
    if not isinstance(index, dict):
        return None
    reply = index.get("reply")
    if not isinstance(reply, dict):
        errors.append(f"{index_path}: missing reply object")
        return None
    entry = reply.get("codemodel-v2")
    if not isinstance(entry, dict) or not isinstance(entry.get("jsonFile"), str):
        errors.append(f"{index_path}: codemodel-v2 reply is missing")
        return None
    codemodel_path = reply_dir / entry["jsonFile"]
    codemodel = _load(codemodel_path, errors)
    if not isinstance(codemodel, dict):
        return None
    return reply_dir, codemodel


def _source_path(root: Path, target: dict[str, Any], raw: str) -> Path | None:
    candidate = Path(raw)
    if candidate.is_absolute():
        return candidate.resolve(strict=False)
    source_base_raw = target.get("paths", {}).get("source") if isinstance(target.get("paths"), dict) else None
    candidates = [root / raw]
    if isinstance(source_base_raw, str):
        source_base = Path(source_base_raw)
        if not source_base.is_absolute():
            source_base = root / source_base
        candidates.append(source_base / raw)
    for value in candidates:
        resolved = value.resolve(strict=False)
        if resolved.exists():
            return resolved
    return candidates[0].resolve(strict=False)


def _target_owners(modules: dict[str, dict[str, Any]], errors: list[str]) -> dict[str, str]:
    owners: dict[str, list[str]] = defaultdict(list)
    for module_id, manifest in modules.items():
        if manifest.get("lifecycle") not in ACTIVE_LIFECYCLES:
            continue
        targets = manifest.get("build_targets", [])
        if not isinstance(targets, list):
            continue
        for target in targets:
            if isinstance(target, str):
                owners[target].append(module_id)
    result: dict[str, str] = {}
    for target, values in sorted(owners.items()):
        unique = sorted(set(values))
        if len(unique) != 1:
            errors.append(
                f"target {target}: expected one current owner, found {', '.join(unique)}"
            )
        else:
            result[target] = unique[0]
    return result


def _exception_map(
    root: Path, registry: dict[str, Any], errors: list[str]
) -> dict[tuple[str, str], dict[str, Any]]:
    result: dict[tuple[str, str], dict[str, Any]] = {}
    for index, item in enumerate(registry.get("compilation_exceptions", [])):
        label = f"compilation exception[{index}]"
        if not isinstance(item, dict):
            errors.append(f"{label}: expected object")
            continue
        target = item.get("target")
        source = item.get("source")
        if not isinstance(target, str) or not target:
            errors.append(f"{label}: invalid target")
            continue
        try:
            source = canonical_relative_path(root, source, allow_trailing_slash=False)
        except ValueError as exc:
            errors.append(f"{label}: {exc}")
            continue
        key = (target, source)
        if key in result:
            errors.append(f"duplicate compilation exception: {target} -> {source}")
            continue
        result[key] = item
    return result


def _validate_exception(
    item: dict[str, Any],
    *, target: str,
    source: str,
    target_owner: str,
    source_owner: str,
    gaps: dict[str, dict[str, Any]],
    errors: list[str],
) -> None:
    if item.get("target_owner") != target_owner:
        errors.append(
            f"{target} -> {source}: exception target owner mismatch "
            f"({item.get('target_owner')} != {target_owner})"
        )
    declared_source_owner = item.get("source_owner")
    if declared_source_owner not in {None, source_owner}:
        errors.append(
            f"{target} -> {source}: exception source owner mismatch "
            f"({declared_source_owner} != {source_owner})"
        )
    gap_id = item.get("gap")
    milestone = item.get("milestone")
    gap = gaps.get(gap_id) if isinstance(gap_id, str) else None
    if gap is None:
        errors.append(f"{target} -> {source}: unknown migration gap {gap_id}")
    else:
        if gap.get("state") == "closed":
            errors.append(f"{target} -> {source}: exception remains after closed gap {gap_id}")
        if gap.get("milestone") != milestone:
            errors.append(f"{target} -> {source}: exception milestone mismatch")


def validate(build_dir: Path) -> list[str]:
    errors: list[str] = []
    build_dir = build_dir.resolve()
    reply = _latest_reply(build_dir, errors)
    if reply is None:
        return errors
    reply_dir, codemodel = reply

    modules, _ = load_modules(ROOT, errors)
    ownership = load_source_ownership(ROOT, errors)
    if not modules or not ownership:
        return errors
    physical_rules = parse_source_rules(ROOT, ownership, errors)
    target_owners = _target_owners(modules, errors)
    exceptions = _exception_map(ROOT, ownership, errors)
    optional_targets = {
        value for value in ownership.get("optional_targets", []) if isinstance(value, str)
    }

    try:
        gap_doc = load_json(ROOT / "docs/program/gap-registry-v2.json")
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        errors.append(f"gap registry invalid: {exc}")
        gap_doc = {}
    gaps = {
        item["id"]: item for item in gap_doc.get("gaps", [])
        if isinstance(item, dict) and isinstance(item.get("id"), str)
    } if isinstance(gap_doc, dict) else {}

    configurations = codemodel.get("configurations")
    if not isinstance(configurations, list) or not configurations:
        errors.append("CMake codemodel has no configurations")
        return errors
    # Single-config generators expose one configuration.  For a multi-config
    # build, checking every configuration is safe because ownership is invariant.
    seen_declared_targets: set[str] = set()
    used_exceptions: set[tuple[str, str]] = set()

    for configuration in configurations:
        if not isinstance(configuration, dict):
            continue
        target_entries = configuration.get("targets")
        if not isinstance(target_entries, list):
            continue
        id_to_name = {
            entry.get("id"): entry.get("name")
            for entry in target_entries
            if isinstance(entry, dict)
            and isinstance(entry.get("id"), str)
            and isinstance(entry.get("name"), str)
        }
        loaded_targets: dict[str, dict[str, Any]] = {}
        for entry in target_entries:
            if not isinstance(entry, dict):
                continue
            json_file = entry.get("jsonFile")
            name = entry.get("name")
            if not isinstance(json_file, str) or not isinstance(name, str):
                continue
            target_doc = _load(reply_dir / json_file, errors)
            if isinstance(target_doc, dict):
                loaded_targets[name] = target_doc

        for name, target_doc in loaded_targets.items():
            owner = target_owners.get(name)
            is_test = name.startswith("hepta_") and name.endswith("_tests")
            if owner is not None:
                seen_declared_targets.add(name)
            sources = target_doc.get("sources")
            if not isinstance(sources, list):
                sources = []
            for source_entry in sources:
                if not isinstance(source_entry, dict) or source_entry.get("isGenerated") is True:
                    continue
                raw = source_entry.get("path")
                if not isinstance(raw, str):
                    continue
                path = _source_path(ROOT, target_doc, raw)
                if path is None:
                    continue
                try:
                    relative = path.relative_to(ROOT.resolve()).as_posix()
                except ValueError:
                    # Toolchain, generated and optional external SDK sources are
                    # outside this repository and have their own supply-chain gate.
                    continue
                if not relative.startswith("HeptaTrade/") or path.suffix.lower() not in {
                    ".c", ".cc", ".cpp", ".h", ".hpp"
                }:
                    continue
                source_owner, conflicts = resolve_physical_owner(relative, physical_rules)
                if source_owner is None:
                    if conflicts:
                        errors.append(
                            f"{name}: {relative}: physical ownership conflict "
                            f"{', '.join(rule.rule_id for rule in conflicts)}"
                        )
                    else:
                        errors.append(f"{name}: {relative}: no physical owner")
                    continue
                target_owner = "hepta.tests" if is_test else owner
                if target_owner is None:
                    errors.append(
                        f"configured target {name} compiles active source {relative} "
                        "but has no ModuleManifest owner"
                    )
                    continue
                if target_owner == source_owner:
                    continue
                key = (name, relative)
                exception = exceptions.get(key)
                if exception is None:
                    errors.append(
                        f"unregistered cross-module compilation: {name} "
                        f"({target_owner}) -> {relative} ({source_owner})"
                    )
                    continue
                _validate_exception(
                    exception,
                    target=name,
                    source=relative,
                    target_owner=target_owner,
                    source_owner=source_owner,
                    gaps=gaps,
                    errors=errors,
                )
                used_exceptions.add(key)

            if owner is None:
                continue
            allowed = target_doc.get("dependencies")
            if not isinstance(allowed, list):
                allowed = []
            module_allowed = modules[owner].get("allowed_dependencies", [])
            for dependency in allowed:
                if not isinstance(dependency, dict):
                    continue
                dep_name = id_to_name.get(dependency.get("id"))
                dep_owner = target_owners.get(dep_name) if isinstance(dep_name, str) else None
                if dep_owner is None or dep_owner == owner:
                    continue
                if not dependency_allowed(dep_owner, module_allowed):
                    errors.append(
                        f"configured dependency is undeclared: {name} ({owner}) -> "
                        f"{dep_name} ({dep_owner})"
                    )

    for target in sorted(set(target_owners) - seen_declared_targets - optional_targets):
        errors.append(f"declared current CMake target is absent from configured graph: {target}")

    configured_names = seen_declared_targets | {
        key[0] for key in used_exceptions
    }
    for key, item in sorted(exceptions.items()):
        if item.get("profile") != "core":
            continue
        # Test direct-compilation exceptions are also validated statically by
        # check_module_discipline; here they must be observed in the configured graph.
        if key not in used_exceptions:
            errors.append(
                f"stale or unobserved core compilation exception: {key[0]} -> {key[1]}"
            )
    return errors


def main() -> int:
    parser = argparse.ArgumentParser()
    action = parser.add_mutually_exclusive_group(required=True)
    action.add_argument("--prepare", action="store_true")
    action.add_argument("--check", action="store_true")
    parser.add_argument("--build-dir", type=Path, required=True)
    args = parser.parse_args()
    if args.prepare:
        return prepare(args.build_dir)
    errors = validate(args.build_dir)
    for error in errors:
        print(f"[CMAKE-MODULE-GRAPH] {error}", file=sys.stderr)
    if errors:
        return 1
    print("[CMAKE-MODULE-GRAPH] PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
