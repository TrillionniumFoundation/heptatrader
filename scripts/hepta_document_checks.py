#!/usr/bin/env python3
"""Shared document and ModuleManifest validation helpers."""
from __future__ import annotations

from collections import defaultdict, deque
import json
from pathlib import Path
import re
import subprocess
import sys
from typing import Any
from urllib.parse import unquote

try:
    from jsonschema import Draft202012Validator
    from jsonschema.exceptions import SchemaError
except ImportError:  # pragma: no cover - exercised as a clear CLI diagnostic
    Draft202012Validator = None  # type: ignore[assignment]
    SchemaError = Exception  # type: ignore[assignment]

from hepta_module_boundaries import (
    ACTIVE_LIFECYCLES,
    SOURCE_OWNERSHIP_REL,
    active_source_files,
    canonical_relative_path,
    load_json,
    load_modules,
    load_source_ownership,
    parse_source_rules,
    selector_from_manifest_claim,
    selector_from_object,
    selector_matches,
)

ROOT = Path(__file__).resolve().parents[1]
DOCS = ROOT / "docs"
DOCUMENT_REGISTRY = DOCS / "document-registry-v2.json"
MODULE_SCHEMA = DOCS / "modules/module-manifest-schema-v2.json"
META = ("Status:", "Applies to:", "Verification:", "Authority:")
LINK_RE = re.compile(r"\[[^\]]*\]\(([^)]+)\)")
SHA_RE = re.compile(r"(?<![0-9a-f])[0-9a-f]{40}(?![0-9a-f])")
FORBIDDEN_DOC_DIRS = frozenset({"legacy", "proposals"})
FORBIDDEN_FILENAMES = frozenset({
    "PLAN.md", "EXACT-HEAD-CI.md", "EXACT-HEAD-FINAL.md",
    "EXACT-HEAD-RESULTS.md", "REMOTE-CLOSURE-AUDIT.json",
    "module-ownership-v1.json",
})
LEGACY_DOC_SUFFIXES = frozenset({
    ".md", ".txt", ".pdf", ".png", ".jpg", ".jpeg", ".webp"
})
LEGACY_BUILD_NAMES = frozenset({"CMakeLists.txt"})
LEGACY_BUILD_ENDINGS = (
    ".sln", ".vcxproj", ".vcxproj.filters", ".cmake",
)


def load(path: Path, errors: list[str]) -> Any:
    try:
        return load_json(path)
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        try:
            relative = path.relative_to(ROOT)
        except ValueError:
            relative = path
        errors.append(f"{relative}: invalid JSON: {exc}")
        return None


def indexed(
    items: Any, key: str, label: str, errors: list[str]
) -> dict[str, dict[str, Any]]:
    if not isinstance(items, list):
        errors.append(f"{label}: expected array")
        return {}
    result: dict[str, dict[str, Any]] = {}
    for position, item in enumerate(items):
        if not isinstance(item, dict):
            errors.append(f"{label}[{position}]: expected object")
            continue
        value = item.get(key)
        if not isinstance(value, str) or not value:
            errors.append(f"{label}[{position}]: missing string {key}")
            continue
        if value in result:
            errors.append(f"{label}: duplicate {key}: {value}")
            continue
        result[value] = item
    return result


def acyclic(
    nodes: set[str], edges: dict[str, set[str]], label: str, errors: list[str]
) -> None:
    indegree = {node: 0 for node in nodes}
    reverse: dict[str, set[str]] = defaultdict(set)
    for source, targets in edges.items():
        for target in targets:
            if source in nodes and target in nodes:
                indegree[source] += 1
                reverse[target].add(source)
    queue = deque(sorted(node for node, degree in indegree.items() if degree == 0))
    seen = 0
    while queue:
        node = queue.popleft()
        seen += 1
        for dependent in sorted(reverse.get(node, ())):
            indegree[dependent] -= 1
            if indegree[dependent] == 0:
                queue.append(dependent)
    if seen != len(nodes):
        cyclic = sorted(node for node, degree in indegree.items() if degree > 0)
        errors.append(f"{label}: dependency cycle: {', '.join(cyclic)}")


def _resolved_links(path: Path, errors: list[str]) -> set[Path]:
    try:
        text = path.read_text(encoding="utf-8-sig")
    except (OSError, UnicodeError) as exc:
        errors.append(f"{path.relative_to(ROOT)}: unreadable: {exc}")
        return set()
    result: set[Path] = set()
    for raw in LINK_RE.findall(text):
        target = raw.strip().split(" ", 1)[0].strip("<>")
        if not target or target.startswith(("#", "http://", "https://", "mailto:")):
            continue
        target = unquote(target.split("#", 1)[0])
        resolved = (path.parent / target).resolve(strict=False)
        try:
            resolved.relative_to(ROOT.resolve())
        except ValueError:
            errors.append(f"{path.relative_to(ROOT)}: link escapes repository: {raw}")
            continue
        if not resolved.exists():
            errors.append(f"{path.relative_to(ROOT)}: missing local link: {raw}")
            continue
        result.add(resolved)
    return result


def check_markdown(path: Path, document_class: str, errors: list[str]) -> None:
    try:
        text = path.read_text(encoding="utf-8-sig")
    except (OSError, UnicodeError) as exc:
        errors.append(f"{path.relative_to(ROOT)}: unreadable: {exc}")
        return
    lines = text.splitlines()[:14]
    for field in META:
        if not any(
            line.startswith(field) and line[len(field):].strip() for line in lines
        ):
            errors.append(f"{path.relative_to(ROOT)}: missing metadata {field}")
    if "Status: current compatibility alias" in text or "Authority: none." in text:
        errors.append(f"{path.relative_to(ROOT)}: compatibility alias is forbidden")
    if document_class == "normative" and SHA_RE.search(text):
        errors.append(f"{path.relative_to(ROOT)}: normative document hard-codes commit SHA")
    _resolved_links(path, errors)


def validate_module_manifest(
    manifest: Any,
    schema: Any,
    label: str,
    errors: list[str],
) -> None:
    """Apply the checked-in Draft 2020-12 schema to one module manifest."""
    if Draft202012Validator is None:
        errors.append("python jsonschema package is required for ModuleManifest validation")
        return
    if not isinstance(schema, dict):
        errors.append("ModuleManifest schema must be an object")
        return
    try:
        Draft202012Validator.check_schema(schema)
    except SchemaError as exc:
        errors.append(f"ModuleManifest schema is invalid: {exc.message}")
        return
    validator = Draft202012Validator(schema)
    for failure in sorted(validator.iter_errors(manifest), key=lambda item: list(item.path)):
        location = ".".join(str(part) for part in failure.absolute_path) or "<root>"
        errors.append(f"{label}: schema violation at {location}: {failure.message}")


def _validate_document_registry(errors: list[str]) -> dict[str, Any]:
    document = load(DOCUMENT_REGISTRY, errors)
    if not isinstance(document, dict):
        return {}
    if document.get("schema") != "heptatrader.document-registry.v2":
        errors.append("document registry schema mismatch")
    documents = indexed(document.get("documents"), "path", "document registry", errors)
    actual = {
        path.relative_to(DOCS).as_posix()
        for path in DOCS.rglob("*") if path.is_file()
    }
    for relative in sorted(set(documents) - actual):
        errors.append(f"registered document missing: docs/{relative}")
    for relative in sorted(actual - set(documents)):
        errors.append(f"unregistered document: docs/{relative}")
    root_files = {path.name for path in DOCS.iterdir() if path.is_file()}
    if root_files != {"README.md", "document-registry-v2.json"}:
        errors.append(
            "docs root may contain only README.md and document-registry-v2.json"
        )
    for directory in FORBIDDEN_DOC_DIRS:
        if (DOCS / directory).exists():
            errors.append(f"forbidden historical directory: docs/{directory}")
    for path in DOCS.rglob("*"):
        if path.is_file() and path.name in FORBIDDEN_FILENAMES:
            errors.append(f"forbidden historical/status file: {path.relative_to(ROOT)}")
    for relative, entry in documents.items():
        document_class = entry.get("class")
        if document_class not in {"normative", "generated-view", "machine-registry"}:
            errors.append(f"docs/{relative}: invalid document class {document_class}")
            continue
        path = DOCS / relative
        if path.suffix.lower() == ".md":
            check_markdown(path, str(document_class), errors)
    return document


def _validate_repository_entrypoints(document_registry: dict[str, Any], errors: list[str]) -> None:
    entries = indexed(
        document_registry.get("repository_entrypoints"),
        "path", "repository entrypoints", errors,
    )
    actual: set[str] = set()
    for path in ROOT.rglob("*.md"):
        relative = path.relative_to(ROOT)
        if ".git" in relative.parts or "build" in relative.parts:
            continue
        if relative.parts and relative.parts[0] in {"docs", "legacy"}:
            continue
        actual.add(relative.as_posix())
    for relative in sorted(set(entries) - actual):
        errors.append(f"registered repository entrypoint missing: {relative}")
    for relative in sorted(actual - set(entries)):
        errors.append(f"unregistered Markdown outside docs/: {relative}")
    for relative, entry in entries.items():
        path = ROOT / relative
        try:
            text = path.read_text(encoding="utf-8-sig")
        except (OSError, UnicodeError) as exc:
            errors.append(f"{relative}: unreadable entrypoint: {exc}")
            continue
        if "Authority: entrypoint only" not in "\n".join(text.splitlines()[:14]):
            errors.append(f"{relative}: repository entrypoint must declare entrypoint-only authority")
        canonical = entry.get("canonical_target")
        try:
            canonical = canonical_relative_path(ROOT, canonical, allow_trailing_slash=False)
        except ValueError as exc:
            errors.append(f"{relative}: invalid canonical target: {exc}")
            continue
        target = (ROOT / canonical).resolve(strict=False)
        if not target.is_file() or not canonical.startswith("docs/"):
            errors.append(f"{relative}: canonical target is not an active docs file: {canonical}")
            continue
        links = _resolved_links(path, errors)
        if target not in links:
            errors.append(f"{relative}: does not link to canonical target {canonical}")


def _validate_legacy(errors: list[str]) -> None:
    legacy = ROOT / "legacy"
    if not legacy.exists():
        return
    marker = load(legacy / "QUARANTINE.json", errors)
    if not isinstance(marker, dict):
        errors.append("legacy/QUARANTINE.json missing or invalid")
        return
    expected = {
        "active_dependency": False,
        "build_entry": False,
        "install_entry": False,
        "documentation_allowed": False,
        "history_location": "git",
    }
    for key, value in expected.items():
        if marker.get(key) != value:
            errors.append(f"legacy/QUARANTINE.json: {key} must be {value!r}")
    for path in legacy.rglob("*"):
        if not path.is_file() or path.name == "QUARANTINE.json":
            continue
        relative = path.relative_to(ROOT)
        lower_name = path.name.lower()
        if path.suffix.lower() in LEGACY_DOC_SUFFIXES:
            errors.append(f"historical documentation/media remains: {relative}")
        if path.name in LEGACY_BUILD_NAMES or lower_name.endswith(LEGACY_BUILD_ENDINGS):
            errors.append(f"historical build entry remains: {relative}")


def _validate_generated_views(errors: list[str]) -> None:
    generator = ROOT / "scripts/generate_documentation_views.py"
    completed = subprocess.run(
        [sys.executable, str(generator), "--check"],
        cwd=ROOT,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        check=False,
    )
    if completed.returncode:
        diagnostic = completed.stdout.strip().replace("\n", " | ")
        errors.append(f"generated views drift: {diagnostic}")


def _current_cmake_targets() -> set[str]:
    targets: set[str] = set()
    for path in ROOT.rglob("CMakeLists.txt"):
        relative = path.relative_to(ROOT)
        if relative.parts and relative.parts[0] in {"legacy", "build"}:
            continue
        text = path.read_text(encoding="utf-8-sig")
        targets.update(re.findall(
            r"add_(?:library|executable)\s*\(\s*([A-Za-z0-9_.+-]+)", text
        ))
    return targets


