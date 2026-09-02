#!/usr/bin/env python3
"""Validate local systemd Documentation= references against canonical docs."""
from __future__ import annotations

import json
from pathlib import Path
import shlex
import sys
from typing import Any
from urllib.parse import unquote


ROOT = Path(__file__).resolve().parents[1]
DOCUMENT_REGISTRY = Path("docs/document-registry-v2.json")
UNIT_PATTERNS = ("*.service", "*.service.in")
INSTALL_DOC_PREFIXES = (
    "@HEPTA_RUNTIME_DOC_DIR@/",
    "/usr/share/doc/heptatrader/",
    "/usr/local/share/doc/heptatrader/",
    "/usr/share/doc/HeptaTrader/",
    "/usr/local/share/doc/HeptaTrader/",
)


def _strict_object_pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate JSON key: {key}")
        result[key] = value
    return result


def _registered_documents(root: Path, errors: list[str]) -> set[str]:
    registry_path = root / DOCUMENT_REGISTRY
    try:
        registry = json.loads(
            registry_path.read_text(encoding="utf-8-sig"),
            object_pairs_hook=_strict_object_pairs,
        )
    except (OSError, UnicodeError, ValueError, json.JSONDecodeError) as exc:
        errors.append(f"{DOCUMENT_REGISTRY.as_posix()}: invalid: {exc}")
        return set()

    if not isinstance(registry, dict):
        errors.append(f"{DOCUMENT_REGISTRY.as_posix()}: expected object")
        return set()
    documents = registry.get("documents")
    if not isinstance(documents, list):
        errors.append(f"{DOCUMENT_REGISTRY.as_posix()}: documents must be an array")
        return set()

    registered: set[str] = set()
    for position, item in enumerate(documents):
        if not isinstance(item, dict):
            errors.append(
                f"{DOCUMENT_REGISTRY.as_posix()}: documents[{position}] must be an object"
            )
            continue
        relative = item.get("path")
        if not isinstance(relative, str) or not relative:
            errors.append(
                f"{DOCUMENT_REGISTRY.as_posix()}: documents[{position}] has no path"
            )
            continue
        candidate = Path(relative)
        if candidate.is_absolute() or ".." in candidate.parts:
            errors.append(
                f"{DOCUMENT_REGISTRY.as_posix()}: unsafe document path: {relative}"
            )
            continue
        canonical = candidate.as_posix()
        if canonical in registered:
            errors.append(
                f"{DOCUMENT_REGISTRY.as_posix()}: duplicate document path: {canonical}"
            )
            continue
        if not (root / "docs" / candidate).is_file():
            errors.append(f"registered document is missing: docs/{canonical}")
            continue
        registered.add(canonical)
    return registered


def _canonical_document_path(location: str) -> str | None:
    decoded = unquote(location).replace("\\", "/")
    for prefix in INSTALL_DOC_PREFIXES:
        if decoded.startswith(prefix):
            relative = decoded[len(prefix):].lstrip("/")
            candidate = Path(relative)
            if not relative or candidate.is_absolute() or ".." in candidate.parts:
                return None
            return candidate.as_posix()
    return None


def validate(root: Path = ROOT) -> list[str]:
    root = Path(root).resolve()
    errors: list[str] = []
    registered = _registered_documents(root, errors)
    systemd_root = root / "systemd"
    if not systemd_root.is_dir():
        return errors

    units: set[Path] = set()
    for pattern in UNIT_PATTERNS:
        units.update(path for path in systemd_root.glob(pattern) if path.is_file())

    for path in sorted(units):
        try:
            lines = path.read_text(encoding="utf-8-sig").splitlines()
        except (OSError, UnicodeError) as exc:
            errors.append(f"{path.relative_to(root)}: unreadable: {exc}")
            continue

        for line_number, line in enumerate(lines, start=1):
            stripped = line.strip()
            if not stripped.startswith("Documentation="):
                continue
            raw_value = stripped.split("=", 1)[1]
            try:
                references = shlex.split(raw_value, comments=False, posix=True)
            except ValueError as exc:
                errors.append(
                    f"{path.relative_to(root)}:{line_number}: invalid Documentation= value: {exc}"
                )
                continue
            for reference in references:
                if not reference.startswith("file:"):
                    continue
                location = reference[len("file:"):]
                relative = _canonical_document_path(location)
                if relative is None:
                    errors.append(
                        f"{path.relative_to(root)}:{line_number}: local Documentation path "
                        f"is outside canonical install roots: {location}"
                    )
                    continue
                if relative not in registered:
                    errors.append(
                        f"{path.relative_to(root)}:{line_number}: local Documentation path "
                        f"is not a registered canonical document: docs/{relative}"
                    )
    return errors


def main() -> int:
    errors = validate()
    for error in errors:
        print(f"[SYSTEMD-DOCUMENTATION] {error}", file=sys.stderr)
    if errors:
        return 1
    print("[SYSTEMD-DOCUMENTATION] PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
