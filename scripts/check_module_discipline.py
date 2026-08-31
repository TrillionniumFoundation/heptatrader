#!/usr/bin/env python3
"""Validate Hepta physical source ownership, module claims and migration debt."""
from __future__ import annotations

import json
from pathlib import Path
import re
import sys
from typing import Any

from hepta_module_boundaries import (
    ACTIVE_LIFECYCLES,
    SOURCE_OWNERSHIP_REL,
    active_source_files,
    canonical_relative_path,
    load_json,
    load_modules,
    load_source_ownership,
    manifest_claims_for_source,
    matching_overlap_exception,
    parse_source_rules,
    resolve_physical_owner,
    selector_from_object,
    selector_matches,
)

ROOT = Path(__file__).resolve().parents[1]
BUDGET_REL = "docs/modules/source-size-budget-v1.json"
GAPS_REL = "docs/program/gap-registry-v2.json"


def _load_object(relative: str, errors: list[str]) -> dict[str, Any] | None:
    try:
        value = load_json(ROOT / relative)
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        errors.append(f"{relative}: invalid JSON: {exc}")
        return None
    if not isinstance(value, dict):
        errors.append(f"{relative}: root must be an object")
        return None
    return value


def _gap_map(errors: list[str]) -> dict[str, dict[str, Any]]:
    document = _load_object(GAPS_REL, errors)
    if document is None:
        return {}
    result: dict[str, dict[str, Any]] = {}
    for item in document.get("gaps", []):
        if isinstance(item, dict) and isinstance(item.get("id"), str):
            result[item["id"]] = item
    return result


def _validate_overlap_exception(
    relative: str,
    owners: set[str],
    physical_owner: str,
    registry: dict[str, Any],
    gaps: dict[str, dict[str, Any]],
    errors: list[str],
) -> None:
    exception = matching_overlap_exception(ROOT, relative, owners, registry, errors)
    if exception is None:
        errors.append(
            f"{relative}: manifest ownership overlap is not explicitly registered: "
            f"{', '.join(sorted(owners))}"
        )
        return
    if exception.get("physical_owner") != physical_owner:
        errors.append(
            f"{relative}: overlap physical owner {exception.get('physical_owner')} "
            f"does not match resolved owner {physical_owner}"
        )
    gap_id = exception.get("gap")
    milestone = exception.get("milestone")
    gap = gaps.get(gap_id) if isinstance(gap_id, str) else None
    if gap is None:
        errors.append(f"{relative}: overlap exception has unknown gap {gap_id}")
        return
    if gap.get("state") == "closed":
        errors.append(f"{relative}: overlap exception remains after closed gap {gap_id}")
    if gap.get("milestone") != milestone:
        errors.append(
            f"{relative}: overlap milestone {milestone} does not match {gap_id}"
        )
    if exception.get("new_participants_forbidden") is not True:
        errors.append(f"{relative}: overlap must forbid new participants")
    if not isinstance(exception.get("exit"), str) or not exception["exit"].strip():
        errors.append(f"{relative}: overlap exception lacks an exit condition")


def _cmake_direct_production_pairs() -> set[tuple[str, str]]:
    """Return explicit test-target -> production-source pairs from CMake files."""
    result: set[tuple[str, str]] = set()
    for cmake_path in sorted((ROOT / "tests").rglob("CMakeLists.txt")):
        text = cmake_path.read_text(encoding="utf-8-sig")
        text = "\n".join(line.split("#", 1)[0] for line in text.splitlines())
        for match in re.finditer(
            r"add_executable\s*\(\s*([^\s()]+)(.*?)\)", text,
            flags=re.IGNORECASE | re.DOTALL,
        ):
            target = match.group(1).strip('"')
            body = match.group(2)
            for token in re.findall(r"[^\s()]+", body):
                token = token.strip('"')
                if "HeptaTrade/" not in token.replace("\\", "/"):
                    continue
                candidate = (cmake_path.parent / token).resolve(strict=False)
                try:
                    relative = candidate.relative_to(ROOT.resolve()).as_posix()
                except ValueError:
                    continue
                if relative.startswith("HeptaTrade/") and Path(relative).suffix in {
                    ".c", ".cc", ".cpp", ".h", ".hpp"
                }:
                    result.add((target, relative))
    return result


def _registered_test_pairs(registry: dict[str, Any], errors: list[str]) -> set[tuple[str, str]]:
    result: set[tuple[str, str]] = set()
    for index, item in enumerate(registry.get("compilation_exceptions", [])):
        if not isinstance(item, dict) or item.get("target_owner") != "hepta.tests":
            continue
        if item.get("profile") != "core":
            continue
        target = item.get("target")
        source = item.get("source")
        if not isinstance(target, str) or not isinstance(source, str):
            errors.append(f"compilation exception[{index}]: invalid test target/source")
            continue
        try:
            source = canonical_relative_path(ROOT, source, allow_trailing_slash=False)
        except ValueError as exc:
            errors.append(f"compilation exception[{index}]: {exc}")
            continue
        pair = (target, source)
        if pair in result:
            errors.append(f"duplicate test compilation exception: {target} -> {source}")
        result.add(pair)
    return result


def _validate_source_size_budget(
    active_sources: list[Path], errors: list[str]
) -> None:
    budget = _load_object(BUDGET_REL, errors)
    if budget is None:
        return
    if budget.get("schema") != "heptatrader.source-size-budget.v1":
        errors.append("source-size budget schema mismatch")
    try:
        cpp_limit = int(budget.get("new_cpp_line_limit", 0))
        python_limit = int(budget.get("new_python_line_limit", 0))
    except (TypeError, ValueError):
        errors.append("source-size limits must be integers")
        return
    if cpp_limit <= 0 or python_limit <= 0:
        errors.append("source-size limits must be positive")
        return
    exceptions = budget.get("exceptions")
    if not isinstance(exceptions, dict):
        exceptions = {}

    for path in active_sources:
        if path.suffix.lower() not in {".c", ".cc", ".cpp"}:
            continue
        relative = path.relative_to(ROOT).as_posix()
        count = len(path.read_text(encoding="utf-8-sig").splitlines())
        exception = exceptions.get(relative)
        if count > cpp_limit and not isinstance(exception, dict):
            errors.append(f"large C++ source lacks migration budget: {relative}")
        if isinstance(exception, dict):
            try:
                baseline = int(exception.get("baseline_lines", 0))
            except (TypeError, ValueError):
                errors.append(f"invalid source-size exception: {relative}")
                continue
            if count > baseline:
                errors.append(f"large C++ source grew beyond baseline: {relative}")

    for base in (ROOT / "scripts", ROOT / "research"):
        if not base.is_dir():
            continue
        for path in sorted(base.rglob("*.py")):
            relative = path.relative_to(ROOT).as_posix()
            count = len(path.read_text(encoding="utf-8-sig").splitlines())
            exception = exceptions.get(relative)
            if count > python_limit and not isinstance(exception, dict):
                errors.append(f"large Python source lacks migration budget: {relative}")
            if isinstance(exception, dict):
                try:
                    baseline = int(exception.get("baseline_lines", 0))
                except (TypeError, ValueError):
                    errors.append(f"invalid source-size exception: {relative}")
                    continue
                if count > baseline:
                    errors.append(f"large Python source grew beyond baseline: {relative}")


def validate() -> list[str]:
    errors: list[str] = []
    modules, module_registry = load_modules(ROOT, errors)
    ownership = load_source_ownership(ROOT, errors)
    if not modules or not ownership:
        return errors
    if module_registry.get("schema") != "heptatrader.module-registry.v2":
        errors.append("module registry schema mismatch")
    if ownership.get("schema") != "heptatrader.source-ownership-registry.v1":
        errors.append("source ownership registry schema mismatch")

    gaps = _gap_map(errors)
    rules = parse_source_rules(ROOT, ownership, errors)
    active_sources = active_source_files(ROOT, ownership)
    if not active_sources:
        errors.append("source ownership registry matched no active sources")

    rule_hits: dict[str, int] = {rule.rule_id: 0 for rule in rules}
    for path in active_sources:
        relative = path.relative_to(ROOT).as_posix()
        physical_owner, winning = resolve_physical_owner(relative, rules)
        if physical_owner is None:
            if winning:
                errors.append(
                    f"{relative}: equal-priority physical ownership conflict: "
                    f"{', '.join(rule.rule_id for rule in winning)}"
                )
            else:
                errors.append(f"{relative}: no physical source owner")
            continue
        if physical_owner not in modules:
            errors.append(f"{relative}: unknown physical owner {physical_owner}")
        for rule in winning:
            rule_hits[rule.rule_id] += 1

        claims = manifest_claims_for_source(ROOT, relative, modules, errors)
        if not claims:
            errors.append(f"{relative}: no active ModuleManifest source claim")
        elif len(claims) == 1:
            only = next(iter(claims))
            if only != physical_owner:
                errors.append(
                    f"{relative}: sole manifest owner {only} differs from "
                    f"physical owner {physical_owner}"
                )
        else:
            _validate_overlap_exception(
                relative, claims, physical_owner, ownership, gaps, errors
            )

    for rule_id, hits in sorted(rule_hits.items()):
        if hits == 0:
            errors.append(f"physical source ownership rule is stale: {rule_id}")

    # Every current/experimental/unsupported C++ claim must resolve to a real
    # file set.  Planned modules may intentionally name future roots.
    active_relatives = [path.relative_to(ROOT).as_posix() for path in active_sources]
    for module_id, module in modules.items():
        if module.get("lifecycle") not in ACTIVE_LIFECYCLES:
            continue
        for raw in module.get("source_roots", []):
            if not isinstance(raw, str) or not raw.startswith("HeptaTrade/"):
                continue
            try:
                selector = (
                    selector_from_object(ROOT, {"kind": "directory", "path": raw})
                    if raw.endswith("/") else
                    selector_from_object(ROOT, {
                        "kind": "file" if (ROOT / raw).is_file() else "prefix",
                        "path": raw,
                    })
                )
            except ValueError as exc:
                errors.append(f"module {module_id}: invalid source root {raw!r}: {exc}")
                continue
            if not any(selector_matches(relative, selector) for relative in active_relatives):
                errors.append(f"module {module_id}: active source root matches no file: {raw}")

    actual_pairs = _cmake_direct_production_pairs()
    registered_pairs = _registered_test_pairs(ownership, errors)
    for target, source in sorted(actual_pairs - registered_pairs):
        errors.append(
            f"unregistered direct production-source test compilation: {target} -> {source}"
        )
    for target, source in sorted(registered_pairs - actual_pairs):
        errors.append(
            f"stale direct production-source test exception: {target} -> {source}"
        )

    _validate_source_size_budget(active_sources, errors)

    cli = ROOT / "HeptaTrade/cli"
    if cli.is_dir():
        for path in sorted(cli.glob("*.cpp")):
            text = path.read_text(encoding="utf-8-sig")
            if "int main(" in text and len(text.splitlines()) > 500:
                errors.append(f"CLI entry point is not thin: {path.relative_to(ROOT)}")
    return errors


def main() -> int:
    errors = validate()
    for error in errors:
        print(f"[MODULE-DISCIPLINE] {error}", file=sys.stderr)
    if errors:
        return 1
    print("[MODULE-DISCIPLINE] PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
