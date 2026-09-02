#!/usr/bin/env python3
"""Validate the canonical, alias-free Hepta Documentation Control Plane V2."""
from __future__ import annotations

import sys

from check_module_implementation_evidence import (
    validate as validate_module_implementation_evidence,
)
from hepta_document_checks import (
    DOCS, _validate_document_registry, _validate_generated_views,
    _validate_legacy, _validate_repository_entrypoints,
    validate_module_manifest,
)
from hepta_registry_checks import _validate_registries


def validate() -> list[str]:
    errors: list[str] = []
    if not DOCS.is_dir():
        return ["docs/: missing"]
    document_registry = _validate_document_registry(errors)
    _validate_repository_entrypoints(document_registry, errors)
    _validate_legacy(errors)
    _validate_generated_views(errors)
    _validate_registries(errors)
    errors.extend(validate_module_implementation_evidence())
    return errors


def main() -> int:
    errors = validate()
    for error in errors:
        print(f"[DOCUMENTATION-CONTROL-PLANE] {error}", file=sys.stderr)
    if errors:
        return 1
    print("[DOCUMENTATION-CONTROL-PLANE] PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
