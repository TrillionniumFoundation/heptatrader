#!/usr/bin/env python3
"""Validate canonical HeptaTrader documentation without a root README."""
from __future__ import annotations

import argparse
from pathlib import Path
import sys
from typing import Any

import check_documentation_core as _core

ROOT = Path(__file__).resolve().parents[1]
CANONICAL_FILES = (
    Path("docs/index.md"),
    Path("docs/DOCUMENTATION-POLICY.md"),
)
REQUIRED_WORKFLOWS = _core.REQUIRED_WORKFLOWS


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
    files.extend(sorted((root / "docs/operations").glob("*.md")))
    return files


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
