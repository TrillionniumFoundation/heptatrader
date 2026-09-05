#!/usr/bin/env python3
"""Generate and validate the README projection of module implementation truth."""
from __future__ import annotations

import argparse
from collections import Counter
import json
from pathlib import Path
import sys
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
README = ROOT / "docs/README.md"
REGISTRY = ROOT / "docs/modules/module-registry-v2.json"
START = "<!-- module-implementation-evidence:start -->"
END = "<!-- module-implementation-evidence:end -->"


def _load_registry(path: Path = REGISTRY) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError("module registry root must be an object")
    return value


def _index(registry: dict[str, Any]) -> list[dict[str, Any]]:
    paths = registry.get("manifest_paths")
    evidence = registry.get("implementation_evidence")
    if not isinstance(paths, list) or not paths:
        raise ValueError("manifest_paths must be a non-empty array")
    if not isinstance(evidence, list):
        raise ValueError("implementation_evidence must be an array")

    manifest_ids: set[str] = set()
    for relative in paths:
        if not isinstance(relative, str) or not relative:
            raise ValueError("manifest_paths entries must be non-empty strings")
        manifest = json.loads((ROOT / "docs" / relative).read_text(encoding="utf-8"))
        module_id = manifest.get("id")
        if not isinstance(module_id, str) or not module_id:
            raise ValueError(f"{relative}: module id missing")
        if module_id in manifest_ids:
            raise ValueError(f"duplicate module manifest: {module_id}")
        manifest_ids.add(module_id)

    by_id: dict[str, dict[str, Any]] = {}
    for position, entry in enumerate(evidence):
        if not isinstance(entry, dict):
            raise ValueError(f"implementation_evidence[{position}] must be an object")
        module_id = entry.get("module_id")
        if not isinstance(module_id, str) or not module_id:
            raise ValueError(f"implementation_evidence[{position}].module_id missing")
        if module_id in by_id:
            raise ValueError(f"duplicate implementation evidence: {module_id}")
        state = entry.get("state")
        if not isinstance(state, str) or not state:
            raise ValueError(f"{module_id}: evidence state missing")
        gates = entry.get("external_gates")
        if not isinstance(gates, list) or any(
            not isinstance(gate, str) or not gate for gate in gates
        ):
            raise ValueError(f"{module_id}: external_gates must be strings")
        for field in (
            "implemented_scope",
            "source_evidence",
            "test_evidence",
        ):
            values = entry.get(field)
            if not isinstance(values, list) or not values or any(
                not isinstance(item, str) or not item for item in values
            ):
                raise ValueError(f"{module_id}: {field} must be non-empty strings")
        excluded = entry.get("excluded_scope")
        if not isinstance(excluded, list) or any(
            not isinstance(item, str) or not item for item in excluded
        ):
            raise ValueError(f"{module_id}: excluded_scope must be strings")
        if state != "implemented" and not excluded:
            raise ValueError(f"{module_id}: bounded state requires excluded_scope")
        by_id[module_id] = entry

    if set(by_id) != manifest_ids:
        missing = sorted(manifest_ids - set(by_id))
        extra = sorted(set(by_id) - manifest_ids)
        raise ValueError(
            f"implementation evidence/module mismatch missing={missing} extra={extra}"
        )
    return [by_id[module_id] for module_id in sorted(by_id)]


def render(registry: dict[str, Any] | None = None) -> str:
    entries = _index(registry or _load_registry())
    counts = Counter(entry["state"] for entry in entries)
    count_text = ", ".join(
        f"`{state}`={counts[state]}" for state in sorted(counts)
    )
    lines = [
        START,
        "## Current Module Implementation Evidence",
        "",
        "The generated module technical guides define authority, contracts and target "
        "engineering semantics. They are **not proof that every described target "
        "capability is fully implemented or deployment-qualified**. Current repository "
        "truth is the evidence state below; exact `implemented_scope`, "
        "`excluded_scope`, source paths and direct tests are authoritative in "
        "[`modules/module-registry-v2.json`](modules/module-registry-v2.json).",
        "",
        f"Registered modules: **{len(entries)}**. Evidence distribution: {count_text}.",
        "",
        "| Module | Current evidence state | External qualification gates |",
        "|---|---|---|",
    ]
    for entry in entries:
        gates = entry["external_gates"]
        rendered_gates = ", ".join(f"`{gate}`" for gate in gates) or "—"
        lines.append(
            f"| `{entry['module_id']}` | `{entry['state']}` | {rendered_gates} |"
        )
    lines += [
        "",
        "`implemented` means complete only for the explicitly registered repository "
        "scope. `bounded-implementation`, `contract-only`, `unsupported`, and "
        "`external-qualification-required` retain every exclusion in the registry. "
        "No generated guide, green hosted test, directory, or build target may "
        "silently raise this ceiling.",
        END,
    ]
    return "\n".join(lines)


def validate(
    readme_path: Path = README,
    registry_path: Path = REGISTRY,
) -> list[str]:
    errors: list[str] = []
    try:
        text = readme_path.read_text(encoding="utf-8")
        expected = render(_load_registry(registry_path))
    except (OSError, UnicodeError, json.JSONDecodeError, ValueError) as exc:
        return [f"module implementation projection cannot be evaluated: {exc}"]
    if text.count(START) != 1 or text.count(END) != 1:
        return ["docs/README.md: module implementation evidence markers must occur once"]
    start = text.index(START)
    end = text.index(END, start) + len(END)
    actual = text[start:end]
    if actual != expected:
        errors.append(
            "docs/README.md: module implementation evidence projection drift; "
            "run python3 scripts/generate_module_implementation_projection.py --write"
        )
    return errors


def write(
    readme_path: Path = README,
    registry_path: Path = REGISTRY,
) -> None:
    text = readme_path.read_text(encoding="utf-8")
    expected = render(_load_registry(registry_path))
    if text.count(START) != 1 or text.count(END) != 1:
        raise ValueError("README projection markers must occur exactly once")
    start = text.index(START)
    end = text.index(END, start) + len(END)
    readme_path.write_text(text[:start] + expected + text[end:], encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--write", action="store_true")
    args = parser.parse_args(argv)
    if args.write:
        try:
            write()
        except (OSError, UnicodeError, json.JSONDecodeError, ValueError) as exc:
            print(f"[MODULE-IMPLEMENTATION-PROJECTION] {exc}", file=sys.stderr)
            return 1
    errors = validate()
    for error in errors:
        print(f"[MODULE-IMPLEMENTATION-PROJECTION] {error}", file=sys.stderr)
    if errors:
        return 1
    print("[MODULE-IMPLEMENTATION-PROJECTION] PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
