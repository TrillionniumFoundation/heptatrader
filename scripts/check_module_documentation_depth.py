#!/usr/bin/env python3
"""Validate that catalogued module documents contain implementation-grade detail.

The existing documentation control plane validates identity, paths, statuses and
links.  This checker validates the complementary question: can a maintainer find
the contracts, state model, failure behavior, security boundary, observability
and verification guidance that the module status claims?

It intentionally checks concepts rather than exact heading spellings.  A module
may use domain-appropriate names such as "Fixed profile" or "Data contract".
"""
from __future__ import annotations

import json
from pathlib import Path
import re
import sys
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
MAINTAINED = {"CURRENT", "QUALIFICATION_REQUIRED"}
HEADING_RE = re.compile(r"^##\s+(.+?)\s*$", re.MULTILINE)
WORD_RE = re.compile(r"[A-Za-z0-9_./:+-]+|[\u3400-\u9fff]")

COMMON_CONCEPTS: dict[str, tuple[str, ...]] = {
    "scope/responsibility": (
        r"responsibilit", r"scope", r"current capability", r"separation of claims",
    ),
    "contract/interface": (
        r"contract", r"public", r"composition", r"profile", r"schema",
        r"protocol", r"implemented components", r"intended responsibilit",
    ),
    "failure behavior": (
        r"failure", r"fail[- ]closed", r"recovery", r"rollback",
    ),
    "verification": (
        r"test", r"verification", r"qualification", r"observability",
    ),
}

MAINTAINED_CONCEPTS: dict[str, tuple[str, ...]] = {
    "state/persistence": (
        r"state", r"persistence", r"durability", r"storage", r"recovery",
        r"configuration", r"authorization",
    ),
    "security/trust boundary": (
        r"security", r"identity", r"trust", r"boundary", r"kill switch",
        r"network", r"authorization order", r"external controls",
    ),
    "operations/observability": (
        r"observability", r"operation", r"incident", r"upgrade", r"qualification",
    ),
}


def _load_json(path: Path) -> Any:
    def unique(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in pairs:
            if key in result:
                raise ValueError(f"duplicate key {key!r}")
            result[key] = value
        return result

    return json.loads(path.read_text(encoding="utf-8"), object_pairs_hook=unique)


def _matches_any(headings: list[str], patterns: tuple[str, ...]) -> bool:
    return any(re.search(pattern, heading, re.IGNORECASE) for pattern in patterns
               for heading in headings)


def _normalized_reference(value: str) -> str:
    return value.rstrip("/")


def validate(root: Path | str = ROOT) -> list[str]:
    root = Path(root).resolve()
    errors: list[str] = []
    try:
        catalog = _load_json(root / "docs/module-catalog.json")
    except (OSError, UnicodeError, json.JSONDecodeError, ValueError) as error:
        return [f"cannot load module catalog: {error}"]

    modules = catalog.get("modules") if isinstance(catalog, dict) else None
    if not isinstance(modules, list):
        return ["module catalog has no modules array"]

    for item in modules:
        if not isinstance(item, dict):
            errors.append("module catalog contains a non-object entry")
            continue
        module_id = item.get("id", "<unknown>")
        status = item.get("status")
        document = item.get("document")
        if not isinstance(document, str):
            errors.append(f"{module_id}: missing document path")
            continue
        path = root / document
        try:
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeError) as error:
            errors.append(f"{module_id}: cannot read {document}: {error}")
            continue

        headings = [match.strip() for match in HEADING_RE.findall(text)]
        minimum_headings = 6 if status in MAINTAINED else 4
        if len(headings) < minimum_headings:
            errors.append(
                f"{module_id}: {document} has {len(headings)} technical sections; "
                f"at least {minimum_headings} are required for status {status}"
            )

        words = WORD_RE.findall(text)
        minimum_words = 320 if status in MAINTAINED else 220
        if len(words) < minimum_words:
            errors.append(
                f"{module_id}: {document} is too shallow for status {status} "
                f"({len(words)} tokens; minimum {minimum_words})"
            )

        concepts = dict(COMMON_CONCEPTS)
        if status in MAINTAINED:
            concepts.update(MAINTAINED_CONCEPTS)
        for label, patterns in concepts.items():
            if not _matches_any(headings, patterns):
                errors.append(
                    f"{module_id}: {document} lacks a technical section covering {label}"
                )

        for field in ("implementation", "tests"):
            values = item.get(field)
            if not isinstance(values, list):
                continue
            for value in values:
                if not isinstance(value, str):
                    continue
                normalized = _normalized_reference(value)
                if normalized not in text and (normalized + "/") not in text:
                    errors.append(
                        f"{module_id}: {document} does not reference catalogued "
                        f"{field} path {value}"
                    )

        if status in MAINTAINED and not re.search(
            r"\b(Known limitations|Limitations|Current authorization state|"
            r"Qualification|Promotion boundary)\b", text, re.IGNORECASE
        ):
            errors.append(
                f"{module_id}: maintained documentation must state limitations or "
                "an explicit qualification/authorization boundary"
            )

    return errors


def main() -> int:
    errors = validate()
    for error in errors:
        print(f"[DOCUMENTATION-DEPTH] {error}", file=sys.stderr)
    if errors:
        return 1
    print("[DOCUMENTATION-DEPTH] PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
