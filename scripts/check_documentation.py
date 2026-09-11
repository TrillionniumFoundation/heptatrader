#!/usr/bin/env python3
"""Validate canonical HeptaTrader documentation and substantive module depth."""
from __future__ import annotations

import argparse
from pathlib import Path
import re
import sys
from typing import Any

import check_documentation_core as _core

ROOT = Path(__file__).resolve().parents[1]
CANONICAL_FILES = (
    Path("README.md"),
    Path("docs/index.md"),
    Path("docs/DOCUMENTATION-POLICY.md"),
)
REQUIRED_WORKFLOWS = _core.REQUIRED_WORKFLOWS

# Documentation is useful only when it explains behavior, not merely when a
# file exists. These topic groups intentionally accept module-specific wording
# while requiring maintained modules to cover the engineering questions that
# matter during development, recovery and incident review. Match complete
# concepts and their ordinary grammatical forms rather than brittle literal
# headings, so the gate measures content instead of enforcing one template.
TOPIC_PATTERNS: dict[str, re.Pattern[str]] = {
    "purpose": re.compile(
        r"\b(responsibilit(?:y|ies)|scope|purpose|role|current capability)\b",
        re.I,
    ),
    "contracts": re.compile(
        r"\b(contracts?|public interfaces?|protocols?|composition|install contract|data contract)\b",
        re.I,
    ),
    "state": re.compile(
        r"\b(states?|persistence|persistent|persisted|journal(?:s|ed)?|snapshots?|recovery|recovering|lifecycle|migration)\b",
        re.I,
    ),
    "concurrency": re.compile(
        r"\b(concurrency|concurrent|atomic(?:ity)?|locks?|races?|serialized|serialization)\b",
        re.I,
    ),
    "failure": re.compile(
        r"\b(failures?|fail[- ]closed|rejections?|rejected|rejects|uncertain(?:ty)?|poison(?:ed)?|invalidat(?:e|ed|es|ion))\b",
        re.I,
    ),
    "security": re.compile(
        r"\b(security|authority|identit(?:y|ies)|credentials?|permissions?|trust boundar(?:y|ies)|network boundar(?:y|ies)|kill switch|fenc(?:e|ed|ing))\b",
        re.I,
    ),
    "observability": re.compile(
        r"\b(observability|metrics?|telemetry|alerts?|logs?|reason codes?)\b",
        re.I,
    ),
    "testing": re.compile(
        r"(?:^|\n)\s*Tests:\s*|\b(test expectations|testing|tests?|regressions?|fault injection)\b",
        re.I | re.M,
    ),
    "operations": re.compile(
        r"\b(startup|shutdown|deploy(?:ment|ed|ing)?|upgrades?|rollback|operators?|runbooks?|qualification)\b",
        re.I,
    ),
    "limitations": re.compile(
        r"\b(known limitations|limitations?|unavailable|experimental|not implemented|prerequisites?)\b",
        re.I,
    ),
}

MAINTAINED_STATUSES = {"CURRENT", "QUALIFICATION_REQUIRED"}
NONCURRENT_STATUSES = {"EXPERIMENTAL", "LEGACY", "PROPOSAL", "UNAVAILABLE"}


def _markdown_files(
    root: Path,
    modules: dict[str, dict[str, Any]],
) -> list[Path]:
    files = [root / relative for relative in CANONICAL_FILES]
    for module in modules.values():
        document = _core._canonical_relative(
            module.get("document"),
            "module.document",
            [],
        )
        if document is not None:
            files.append(root / document)
    development_index = root / "docs/DEVELOPMENT-DOCUMENTATION-INDEX.md"
    if development_index.is_file() and not development_index.is_symlink():
        files.append(development_index)
    files.extend(sorted((root / "docs/operations").glob("*.md")))
    files.extend(sorted((root / "docs/technical").glob("*.md")))
    return files


def _topic_coverage(text: str) -> set[str]:
    return {
        name
        for name, pattern in TOPIC_PATTERNS.items()
        if pattern.search(text) is not None
    }


def _validate_documentation_depth(
    root: Path,
    modules: dict[str, dict[str, Any]],
    errors: list[str],
) -> None:
    for module_id, module in modules.items():
        document = _core._canonical_relative(
            module.get("document"),
            f"{module_id}.document",
            errors,
        )
        if document is None:
            continue
        text = _core._read_text(root / document, document.as_posix(), errors)
        if not text:
            continue
        status = module.get("status")
        topics = _topic_coverage(text)

        if status in MAINTAINED_STATUSES:
            minimum_bytes = 1200
            minimum_topics = 7
            hard_required = {"purpose", "failure", "observability", "testing"}
        elif status in NONCURRENT_STATUSES:
            minimum_bytes = 700
            minimum_topics = 5
            hard_required = {"purpose", "failure", "testing"}
        else:
            continue

        if len(text.encode("utf-8")) < minimum_bytes:
            errors.append(
                f"{document}: technical document is too shallow for {status}; "
                f"need at least {minimum_bytes} UTF-8 bytes"
            )
        missing_hard = sorted(hard_required - topics)
        if missing_hard:
            errors.append(
                f"{document}: missing required engineering topics: "
                + ", ".join(missing_hard)
            )
        if len(topics) < minimum_topics:
            errors.append(
                f"{document}: covers only {len(topics)} engineering topic groups "
                f"({', '.join(sorted(topics))}); need at least {minimum_topics}"
            )

        # A maintained document must describe behavior in prose, not just list
        # implementation/test paths and headings. Requiring multiple paragraphs
        # catches catalog-shaped stubs that satisfy metadata tokens only.
        paragraphs = [
            part.strip()
            for part in re.split(r"\n\s*\n", text)
            if part.strip() and not part.lstrip().startswith("#")
        ]
        prose = [part for part in paragraphs if len(part.split()) >= 12]
        if status in MAINTAINED_STATUSES and len(prose) < 4:
            errors.append(
                f"{document}: maintained module needs at least four substantive prose paragraphs"
            )


def validate(root: Path | str = ROOT) -> list[str]:
    root = Path(root).resolve()
    errors: list[str] = []
    for workflow in REQUIRED_WORKFLOWS:
        _core._existing_path(
            root,
            workflow.as_posix(),
            workflow.as_posix(),
            errors,
        )
    modules = _core._validate_catalog(root, errors)
    _core._validate_capabilities(root, modules, errors)
    _validate_documentation_depth(root, modules, errors)
    _core._validate_links(root, _markdown_files(root, modules), errors)
    return errors


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, default=ROOT)
    args = parser.parse_args(argv)
    errors = validate(args.root)
    for error in errors:
        print(f"[DOCUMENTATION] {error}", file=sys.stderr)
    if errors:
        return 1
    print("[DOCUMENTATION] PASS")
    return 0


def __getattr__(name: str) -> Any:
    return getattr(_core, name)


if __name__ == "__main__":
    raise SystemExit(main())
