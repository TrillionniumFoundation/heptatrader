#!/usr/bin/env python3
"""Shared, fail-closed helpers for Hepta module/source/build governance."""
from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path, PurePosixPath
from typing import Any, Iterable

MODULE_REGISTRY_REL = "docs/modules/module-registry-v2.json"
SOURCE_OWNERSHIP_REL = "docs/modules/source-ownership-registry-v1.json"
GAP_REGISTRY_REL = "docs/program/gap-registry-v2.json"
ACTIVE_LIFECYCLES = frozenset({"current", "experimental", "unsupported"})


@dataclass(frozen=True)
class Selector:
    kind: str
    path: str


@dataclass(frozen=True)
class SourceRule:
    rule_id: str
    selector: Selector
    physical_owner: str
    priority: int


def load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8-sig"))


def canonical_relative_path(root: Path, raw: str, *, allow_trailing_slash: bool = True) -> str:
    """Return a repository-relative POSIX path or raise ValueError.

    The syntax check happens before resolving the path so path aliases such as
    ``a/../b`` are rejected rather than silently normalized.  Resolution then
    rejects symlink escapes from the repository root.
    """
    if not isinstance(raw, str) or not raw or "\x00" in raw:
        raise ValueError("path must be a non-empty string without NUL")
    if "\\" in raw:
        raise ValueError("path must use POSIX separators")
    had_trailing = raw.endswith("/")
    posix = PurePosixPath(raw)
    if posix.is_absolute():
        raise ValueError("absolute path is forbidden")
    if any(part in {"", ".", ".."} for part in posix.parts):
        raise ValueError("path aliases and parent traversal are forbidden")
    normalized = posix.as_posix()
    resolved_root = root.resolve()
    resolved = (resolved_root / normalized).resolve(strict=False)
    try:
        resolved.relative_to(resolved_root)
    except ValueError as exc:
        raise ValueError("path escapes repository") from exc
    if had_trailing and allow_trailing_slash:
        normalized += "/"
    return normalized


def selector_from_object(root: Path, value: Any) -> Selector:
    if not isinstance(value, dict) or set(value) != {"kind", "path"}:
        raise ValueError("selector must contain exactly kind and path")
    kind = value.get("kind")
    if kind not in {"directory", "file", "prefix"}:
        raise ValueError("selector kind must be directory, file or prefix")
    path = canonical_relative_path(root, value.get("path"), allow_trailing_slash=True)
    if kind == "directory":
        path = path.rstrip("/") + "/"
    elif path.endswith("/"):
        raise ValueError(f"{kind} selector cannot end with slash")
    return Selector(kind=kind, path=path)


def selector_from_manifest_claim(root: Path, raw: str) -> Selector:
    path = canonical_relative_path(root, raw, allow_trailing_slash=True)
    if path.endswith("/"):
        return Selector("directory", path)
    candidate = root / path
    if candidate.is_file():
        return Selector("file", path)
    # A non-existent exact file for a planned module is indistinguishable from
    # a filename prefix.  Current/experimental manifests are checked for a
    # non-empty match set, so treating it as a prefix remains fail-closed.
    return Selector("prefix", path)


def selector_matches(relative: str, selector: Selector) -> bool:
    relative = PurePosixPath(relative).as_posix()
    if selector.kind == "file":
        return relative == selector.path
    if selector.kind == "directory":
        base = selector.path.rstrip("/")
        return relative == base or relative.startswith(base + "/")
    # Prefixes are filename prefixes within one exact parent directory.  This
    # prevents ``foo`` from claiming ``foobar/child.cpp`` in another subtree.
    prefix = PurePosixPath(selector.path)
    candidate = PurePosixPath(relative)
    return candidate.parent == prefix.parent and candidate.name.startswith(prefix.name)


def selector_specificity(selector: Selector) -> tuple[int, int]:
    rank = {"directory": 1, "prefix": 2, "file": 3}[selector.kind]
    return rank, len(selector.path)


def load_modules(root: Path, errors: list[str] | None = None) -> tuple[dict[str, dict[str, Any]], dict[str, Any]]:
    errors = errors if errors is not None else []
    registry_path = root / MODULE_REGISTRY_REL
    try:
        registry = load_json(registry_path)
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        errors.append(f"{MODULE_REGISTRY_REL}: invalid JSON: {exc}")
        return {}, {}
    if not isinstance(registry, dict):
        errors.append(f"{MODULE_REGISTRY_REL}: root must be an object")
        return {}, {}
    manifests: dict[str, dict[str, Any]] = {}
    paths = registry.get("manifest_paths")
    if not isinstance(paths, list):
        errors.append("module registry manifest_paths must be an array")
        return manifests, registry
    for index, relative in enumerate(paths):
        if not isinstance(relative, str):
            errors.append(f"module registry manifest_paths[{index}] must be a string")
            continue
        try:
            canonical = canonical_relative_path(root, "docs/" + relative, allow_trailing_slash=False)
        except ValueError as exc:
            errors.append(f"module registry manifest path {relative!r}: {exc}")
            continue
        path = root / canonical
        try:
            manifest = load_json(path)
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            errors.append(f"{canonical}: invalid JSON: {exc}")
            continue
        if not isinstance(manifest, dict):
            errors.append(f"{canonical}: root must be an object")
            continue
        module_id = manifest.get("id")
        if not isinstance(module_id, str) or not module_id:
            errors.append(f"{canonical}: missing module id")
            continue
        if module_id in manifests:
            errors.append(f"duplicate module id: {module_id}")
            continue
        manifest["__manifest_path"] = canonical
        manifests[module_id] = manifest
    return manifests, registry


def load_source_ownership(root: Path, errors: list[str] | None = None) -> dict[str, Any]:
    errors = errors if errors is not None else []
    path = root / SOURCE_OWNERSHIP_REL
    try:
        value = load_json(path)
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        errors.append(f"{SOURCE_OWNERSHIP_REL}: invalid JSON: {exc}")
        return {}
    if not isinstance(value, dict):
        errors.append(f"{SOURCE_OWNERSHIP_REL}: root must be an object")
        return {}
    return value


def parse_source_rules(root: Path, registry: dict[str, Any], errors: list[str]) -> list[SourceRule]:
    rules: list[SourceRule] = []
    seen_ids: set[str] = set()
    for index, item in enumerate(registry.get("physical_ownership_rules", [])):
        label = f"source ownership rule[{index}]"
        if not isinstance(item, dict):
            errors.append(f"{label}: expected object")
            continue
        rule_id = item.get("id")
        owner = item.get("physical_owner")
        priority = item.get("priority")
        if not isinstance(rule_id, str) or not rule_id:
            errors.append(f"{label}: missing id")
            continue
        if rule_id in seen_ids:
            errors.append(f"duplicate source ownership rule: {rule_id}")
            continue
        seen_ids.add(rule_id)
        if not isinstance(owner, str) or not owner:
            errors.append(f"{label}: missing physical_owner")
            continue
        if not isinstance(priority, int) or isinstance(priority, bool) or priority < 0:
            errors.append(f"{label}: priority must be a non-negative integer")
            continue
        try:
            selector = selector_from_object(root, item.get("selector"))
        except ValueError as exc:
            errors.append(f"{label}: {exc}")
            continue
        rules.append(SourceRule(rule_id, selector, owner, priority))
    return rules


def resolve_physical_owner(relative: str, rules: Iterable[SourceRule]) -> tuple[str | None, list[SourceRule]]:
    matching = [rule for rule in rules if selector_matches(relative, rule.selector)]
    if not matching:
        return None, []
    best_key = max((rule.priority, *selector_specificity(rule.selector)) for rule in matching)
    best = [
        rule for rule in matching
        if (rule.priority, *selector_specificity(rule.selector)) == best_key
    ]
    owners = {rule.physical_owner for rule in best}
    if len(owners) != 1:
        return None, best
    return next(iter(owners)), best


def active_source_files(root: Path, registry: dict[str, Any]) -> list[Path]:
    extensions = registry.get("source_extensions", [".c", ".cc", ".cpp", ".h", ".hpp"])
    suffixes = {value.lower() for value in extensions if isinstance(value, str)}
    result: list[Path] = []
    for raw in registry.get("scope_roots", ["HeptaTrade/"]):
        if not isinstance(raw, str):
            continue
        try:
            relative = canonical_relative_path(root, raw, allow_trailing_slash=True)
        except ValueError:
            continue
        base = root / relative.rstrip("/")
        if not base.is_dir():
            continue
        result.extend(
            path for path in base.rglob("*")
            if path.is_file() and path.suffix.lower() in suffixes
        )
    return sorted(set(path.resolve() for path in result))


def manifest_claims_for_source(root: Path, relative: str, modules: dict[str, dict[str, Any]], errors: list[str] | None = None) -> set[str]:
    errors = errors if errors is not None else []
    owners: set[str] = set()
    for module_id, manifest in modules.items():
        if manifest.get("lifecycle") not in ACTIVE_LIFECYCLES:
            continue
        claims = manifest.get("source_roots", [])
        if not isinstance(claims, list):
            continue
        for raw in claims:
            if not isinstance(raw, str):
                continue
            try:
                selector = selector_from_manifest_claim(root, raw)
            except ValueError as exc:
                errors.append(f"module {module_id}: invalid source root {raw!r}: {exc}")
                continue
            if selector_matches(relative, selector):
                owners.add(module_id)
                break
    return owners


def matching_overlap_exception(root: Path, relative: str, participants: set[str], registry: dict[str, Any], errors: list[str] | None = None) -> dict[str, Any] | None:
    errors = errors if errors is not None else []
    matches: list[dict[str, Any]] = []
    for index, item in enumerate(registry.get("source_overlap_exceptions", [])):
        if not isinstance(item, dict):
            continue
        declared = item.get("participants")
        if not isinstance(declared, list) or set(declared) != participants:
            continue
        for raw_selector in item.get("scopes", []):
            try:
                selector = selector_from_object(root, raw_selector)
            except ValueError as exc:
                errors.append(f"source overlap exception[{index}]: {exc}")
                continue
            if selector_matches(relative, selector):
                matches.append(item)
                break
    if len(matches) == 1:
        return matches[0]
    if len(matches) > 1:
        errors.append(f"{relative}: multiple source-overlap exceptions match")
    return None


def dependency_allowed(dependency: str, allowed: Iterable[str]) -> bool:
    for pattern in allowed:
        if pattern == dependency:
            return True
        if pattern.endswith(".*") and dependency.startswith(pattern[:-1]):
            return True
    return False
