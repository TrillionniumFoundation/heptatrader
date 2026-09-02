#!/usr/bin/env python3
"""Verify that PR and merge-group context projections are byte-equivalent."""
from __future__ import annotations

import json
from pathlib import Path
import sys
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
REGISTRY_REL = Path(".github/required-check-contexts-v1.json")
PROJECTIONS = (
    "required_pull_request_contexts",
    "required_merge_group_contexts",
)


def _strict_object_pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate JSON key: {key}")
        result[key] = value
    return result


def validate(root: Path = ROOT) -> list[str]:
    root = Path(root).resolve()
    errors: list[str] = []
    try:
        document = json.loads(
            (root / REGISTRY_REL).read_text(encoding="utf-8-sig"),
            object_pairs_hook=_strict_object_pairs,
        )
    except (OSError, UnicodeError, ValueError, json.JSONDecodeError) as exc:
        return [f"{REGISTRY_REL}: invalid: {exc}"]
    if not isinstance(document, dict):
        return [f"{REGISTRY_REL}: expected object"]
    if document.get("schema") != "heptatrader.required-check-contexts.v1":
        errors.append(f"{REGISTRY_REL}: schema mismatch")
    canonical = document.get("required_branch_contexts")
    if (
        not isinstance(canonical, list)
        or not canonical
        or any(not isinstance(item, str) or not item for item in canonical)
        or len(canonical) != len(set(canonical))
    ):
        errors.append(
            f"{REGISTRY_REL}: required_branch_contexts must be a unique "
            "non-empty string array"
        )
        return errors
    for field in PROJECTIONS:
        if document.get(field) != canonical:
            errors.append(
                f"{REGISTRY_REL}: {field} must exactly equal "
                "required_branch_contexts"
            )
    return errors


def main() -> int:
    errors = validate()
    for error in errors:
        print(f"[REQUIRED-CONTEXT-PROJECTIONS] {error}", file=sys.stderr)
    if errors:
        return 1
    print("[REQUIRED-CONTEXT-PROJECTIONS] PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
