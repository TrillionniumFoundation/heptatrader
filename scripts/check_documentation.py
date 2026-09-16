#!/usr/bin/env python3
"""Validate canonical HeptaTrader documentation without a root README."""
from __future__ import annotations

import argparse
from pathlib import Path
import sys

import check_documentation_core as _core

ROOT = Path(__file__).resolve().parents[1]
CANONICAL_FILES = (
    Path("docs/index.md"),
    Path("docs/DOCUMENTATION-POLICY.md"),
)
REQUIRED_WORKFLOWS = _core.REQUIRED_WORKFLOWS

# Keep the mature validator as the implementation and change only the repository
# entry-point contract.  The documentation policy intentionally removed the root
# README, so link traversal must start from docs/index.md rather than requiring
# an obsolete duplicate homepage.
_core.CANONICAL_FILES = CANONICAL_FILES

for _name in dir(_core):
    if _name not in {"validate", "main", "CANONICAL_FILES"}:
        globals()[_name] = getattr(_core, _name)


def validate(root: Path | str = ROOT) -> list[str]:
    root = Path(root).resolve()
    errors: list[str] = []
    index = _core._read_text(root / "docs/index.md", "docs/index.md", errors)
    if not index.strip():
        errors.append("docs/index.md: canonical project entry point is missing")
    for workflow in REQUIRED_WORKFLOWS:
        _core._existing_path(root, workflow.as_posix(), workflow.as_posix(), errors)
    modules = _core._validate_catalog(root, errors)
    _core._validate_capabilities(root, modules, errors)
    _core._validate_links(root, _core._markdown_files(root, modules), errors)
    errors.extend(_core.validate_generated_index(root, modules))
    return errors


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, default=ROOT)
    parser.add_argument("--write-index", action="store_true",
                        help="regenerate only the catalog-owned navigation table")
    parser.add_argument("--write-module-metadata", action="store_true",
                        help="regenerate module Implementation/Tests headers from the catalog")
    args = parser.parse_args(argv)
    if args.write_index or args.write_module_metadata:
        try:
            if args.write_module_metadata:
                _core.write_module_metadata(args.root.resolve())
            if args.write_index:
                _core.write_index(args.root.resolve())
        except (OSError, ValueError) as error:
            parser.error(str(error))
    errors = validate(args.root)
    for error in errors:
        print(f"[DOCUMENTATION] {error}", file=sys.stderr)
    if errors:
        return 1
    print("[DOCUMENTATION] PASS structure/links/capability facts; design depth requires review")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
