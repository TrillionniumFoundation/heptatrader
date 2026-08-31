#!/usr/bin/env python3
"""Validate the Hepta Documentation Control Plane V2.

The checker intentionally uses only Python's standard library. It validates
that the active docs tree contains one registered V2 graph, that compatibility
aliases contain no independent authority, and that the principal machine
registries are internally consistent.
"""

from __future__ import annotations

from collections import defaultdict, deque
import json
from pathlib import Path
import re
import sys
from urllib.parse import unquote
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
DOCS = ROOT / "docs"
REGISTRY_PATH = DOCS / "document-registry-v2.json"
METADATA_FIELDS = ("Status:", "Applies to:", "Verification:")
FORBIDDEN_DOC_DIRECTORIES = ("legacy", "proposals")
FORBIDDEN_STATUS_FILES = (
    "EXACT-HEAD-CI.md",
    "EXACT-HEAD-FINAL.md",
    "EXACT-HEAD-RESULTS.md",
    "REMOTE-CLOSURE-AUDIT.json",
)
HEX_SHA_RE = re.compile(r"(?<![0-9a-f])[0-9a-f]{40}(?![0-9a-f])")
LINK_RE = re.compile(r"\[[^\]]*\]\(([^)]+)\)")


def load_json(path: Path, errors: list[str]) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        errors.append(f"{path.relative_to(ROOT)}: invalid JSON: {exc}")
        return None


def relative_files(root: Path) -> set[str]:
    return {
        path.relative_to(root).as_posix()
        for path in root.rglob("*")
        if path.is_file()
    }


def check_metadata(path: Path, errors: list[str]) -> None:
    try:
        lines = path.read_text(encoding="utf-8-sig").splitlines()[:12]
    except (OSError, UnicodeError) as exc:
        errors.append(f"{path.relative_to(ROOT)}: unreadable Markdown: {exc}")
        return
    missing = [
        field for field in METADATA_FIELDS
        if not any(line.startswith(field) and line[len(field):].strip()
                   for line in lines)
    ]
    if missing:
        errors.append(
            f"{path.relative_to(ROOT)}: missing metadata: {', '.join(missing)}"
        )


def check_links(path: Path, errors: list[str]) -> None:
    try:
        text = path.read_text(encoding="utf-8-sig")
    except (OSError, UnicodeError):
        return
    for raw in LINK_RE.findall(text):
        target = raw.strip().split(" ", 1)[0].strip("<>")
        if not target or target.startswith(("#", "http://", "https://", "mailto:")):
            continue
        target = unquote(target.split("#", 1)[0])
        resolved = (path.parent / target).resolve()
        try:
            resolved.relative_to(ROOT)
        except ValueError:
            errors.append(
                f"{path.relative_to(ROOT)}: link escapes repository: {raw}"
            )
            continue
        if not resolved.exists():
            errors.append(
                f"{path.relative_to(ROOT)}: missing local link target: {raw}"
            )


def unique_entries(
    entries: Any, key: str, label: str, errors: list[str]
) -> tuple[dict[str, dict[str, Any]], list[dict[str, Any]]]:
    if not isinstance(entries, list):
        errors.append(f"{label}: entries must be an array")
        return {}, []
    indexed: dict[str, dict[str, Any]] = {}
    valid: list[dict[str, Any]] = []
    for index, entry in enumerate(entries):
        if not isinstance(entry, dict):
            errors.append(f"{label}[{index}]: entry must be an object")
            continue
        value = entry.get(key)
        if not isinstance(value, str) or not value:
            errors.append(f"{label}[{index}]: missing string {key}")
            continue
        if value in indexed:
            errors.append(f"{label}: duplicate {key}: {value}")
            continue
        indexed[value] = entry
        valid.append(entry)
    return indexed, valid


def check_acyclic(
    nodes: set[str], edges: dict[str, set[str]], label: str, errors: list[str]
) -> None:
    indegree = {node: 0 for node in nodes}
    reverse: dict[str, set[str]] = defaultdict(set)
    for source, targets in edges.items():
        for target in targets:
            if source not in nodes or target not in nodes:
                continue
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


def validate_document_registry(errors: list[str]) -> dict[str, dict[str, Any]]:
    registry = load_json(REGISTRY_PATH, errors)
    if not isinstance(registry, dict):
        return {}
    if registry.get("schema") != "heptatrader.document-registry.v2":
        errors.append("docs/document-registry-v2.json: schema mismatch")
    indexed, entries = unique_entries(
        registry.get("documents"), "path", "document registry", errors
    )
    actual = relative_files(DOCS)
    registered = set(indexed)
    missing = sorted(registered - actual)
    extra = sorted(actual - registered)
    for path in missing:
        errors.append(f"document registry points to missing file: docs/{path}")
    for path in extra:
        errors.append(f"unregistered active document: docs/{path}")

    for forbidden in FORBIDDEN_DOC_DIRECTORIES:
        if (DOCS / forbidden).exists():
            errors.append(f"historical documentation directory remains: docs/{forbidden}")
    for name in FORBIDDEN_STATUS_FILES:
        for path in DOCS.rglob(name):
            errors.append(f"manual dynamic status file remains: {path.relative_to(ROOT)}")

    allowed_classes = {"normative", "generated-view", "machine-registry", "alias"}
    for entry in entries:
        path_value = entry["path"]
        doc_class = entry.get("class")
        if doc_class not in allowed_classes:
            errors.append(f"docs/{path_value}: invalid document class: {doc_class}")
            continue
        path = DOCS / path_value
        if path.suffix.lower() != ".md":
            continue
        check_metadata(path, errors)
        check_links(path, errors)
        text = path.read_text(encoding="utf-8-sig")
        if doc_class == "alias":
            if "Status: current compatibility alias" not in text:
                errors.append(f"docs/{path_value}: alias status is not canonical")
            if "Authority: none." not in text:
                errors.append(f"docs/{path_value}: alias contains no authority marker")
            if len(LINK_RE.findall(text)) != 1:
                errors.append(f"docs/{path_value}: alias must contain exactly one target link")
        else:
            if "Status: current compatibility alias" in text:
                errors.append(f"docs/{path_value}: non-alias uses alias status")
            if HEX_SHA_RE.search(text):
                errors.append(
                    f"docs/{path_value}: normative/generated document hard-codes a commit SHA"
                )
    return indexed


def validate_registries(errors: list[str]) -> None:
    contract_registry = load_json(
        DOCS / "contracts/contract-registry-v1.json", errors
    )
    module_registry = load_json(DOCS / "modules/module-registry-v2.json", errors)
    capability_registry = load_json(
        DOCS / "product/capability-registry-v2.json", errors
    )
    milestones = load_json(DOCS / "program/milestone-registry-v1.json", errors)
    workstreams = load_json(DOCS / "program/workstream-registry-v1.json", errors)
    gaps = load_json(DOCS / "program/gap-registry-v2.json", errors)
    tests = load_json(DOCS / "verification/test-matrix-v2.json", errors)
    faults = load_json(DOCS / "verification/fault-matrix-v1.json", errors)
    performance = load_json(
        DOCS / "verification/performance-budgets-v1.json", errors
    )
    qualification = load_json(
        DOCS / "verification/qualification-policy-v1.json", errors
    )

    if isinstance(contract_registry, dict):
        if contract_registry.get("schema") != "heptatrader.contract-registry.v1":
            errors.append("contract registry schema mismatch")
        contracts, contract_entries = unique_entries(
            contract_registry.get("contracts"), "id", "contract registry", errors
        )
        for entry in contract_entries:
            document = entry.get("document")
            if not isinstance(document, str) or not (DOCS / document).is_file():
                errors.append(
                    f"contract {entry['id']}: missing canonical document: {document}"
                )
            schema_path = entry.get("schema")
            if schema_path is not None:
                if not isinstance(schema_path, str):
                    errors.append(f"contract {entry['id']}: schema path is invalid")
                elif not (DOCS / schema_path).resolve().is_file():
                    errors.append(
                        f"contract {entry['id']}: missing schema: {schema_path}"
                    )
    else:
        contracts = {}

    if isinstance(module_registry, dict):
        if module_registry.get("schema") != "heptatrader.module-registry.v2":
            errors.append("module registry schema mismatch")
        modules, module_entries = unique_entries(
            module_registry.get("modules"), "id", "module registry", errors
        )
        edges: dict[str, set[str]] = defaultdict(set)
        for entry in module_entries:
            owners = entry.get("owners")
            if not isinstance(owners, dict):
                errors.append(f"module {entry['id']}: owners missing")
            else:
                for field in ("dri", "backup"):
                    if not isinstance(owners.get(field), str) or not owners[field]:
                        errors.append(f"module {entry['id']}: owner {field} missing")
                reviewers = owners.get("reviewers")
                if not isinstance(reviewers, list) or not reviewers:
                    errors.append(f"module {entry['id']}: reviewers missing")
            dependencies = entry.get("allowed_dependencies")
            if not isinstance(dependencies, list):
                errors.append(f"module {entry['id']}: dependencies must be an array")
                continue
            for dependency in dependencies:
                if not isinstance(dependency, str):
                    errors.append(f"module {entry['id']}: invalid dependency")
                elif "*" not in dependency:
                    if dependency not in modules:
                        errors.append(
                            f"module {entry['id']}: unknown dependency: {dependency}"
                        )
                    else:
                        edges[entry["id"]].add(dependency)
        check_acyclic(set(modules), edges, "module registry", errors)
    else:
        modules = {}

    if isinstance(capability_registry, dict):
        if capability_registry.get("schema") != "heptatrader.capability-registry.v2":
            errors.append("capability registry schema mismatch")
        _, capability_entries = unique_entries(
            capability_registry.get("capabilities"), "id",
            "capability registry", errors
        )
        for entry in capability_entries:
            for module_id in entry.get("modules", []):
                if module_id not in modules:
                    errors.append(
                        f"capability {entry['id']}: unknown module: {module_id}"
                    )
            for contract_id in entry.get("contracts", []):
                if contract_id not in contracts:
                    errors.append(
                        f"capability {entry['id']}: unknown contract: {contract_id}"
                    )

    if isinstance(milestones, dict):
        if milestones.get("schema") != "heptatrader.milestone-registry.v1":
            errors.append("milestone registry schema mismatch")
        milestone_map, milestone_entries = unique_entries(
            milestones.get("milestones"), "id", "milestone registry", errors
        )
        milestone_edges: dict[str, set[str]] = defaultdict(set)
        for entry in milestone_entries:
            for dependency in entry.get("depends_on", []):
                if dependency not in milestone_map:
                    errors.append(
                        f"milestone {entry['id']}: unknown dependency: {dependency}"
                    )
                else:
                    milestone_edges[entry["id"]].add(dependency)
        check_acyclic(
            set(milestone_map), milestone_edges, "milestone registry", errors
        )
    else:
        milestone_map = {}

    if isinstance(workstreams, dict):
        if workstreams.get("schema") != "heptatrader.workstream-registry.v1":
            errors.append("workstream registry schema mismatch")
        workstream_map, workstream_entries = unique_entries(
            workstreams.get("workstreams"), "id", "workstream registry", errors
        )
        for entry in workstream_entries:
            for milestone in entry.get("milestones", []):
                if milestone not in milestone_map:
                    errors.append(
                        f"workstream {entry['id']}: unknown milestone: {milestone}"
                    )
    else:
        workstream_map = {}

    if isinstance(gaps, dict):
        if gaps.get("schema") != "heptatrader.gap-registry.v2":
            errors.append("gap registry schema mismatch")
        allowed_states = set(gaps.get("allowed_states", []))
        _, gap_entries = unique_entries(
            gaps.get("gaps"), "id", "gap registry", errors
        )
        for entry in gap_entries:
            if entry.get("state") not in allowed_states:
                errors.append(f"gap {entry['id']}: invalid state")
            if entry.get("workstream") not in workstream_map:
                errors.append(f"gap {entry['id']}: unknown workstream")
            if entry.get("milestone") not in milestone_map:
                errors.append(f"gap {entry['id']}: unknown milestone")
            evidence = entry.get("evidence")
            if not isinstance(evidence, list) or not evidence:
                errors.append(f"gap {entry['id']}: evidence requirements missing")

    expected_schemas = {
        "test matrix": (tests, "heptatrader.test-matrix.v2", "lanes", "id"),
        "fault matrix": (faults, "heptatrader.fault-matrix.v1", "faults", "id"),
        "performance budgets": (
            performance, "heptatrader.performance-budgets.v1", "budgets", "id"
        ),
    }
    for label, (document, schema, list_key, id_key) in expected_schemas.items():
        if not isinstance(document, dict):
            continue
        if document.get("schema") != schema:
            errors.append(f"{label}: schema mismatch")
        unique_entries(document.get(list_key), id_key, label, errors)

    if isinstance(qualification, dict):
        if qualification.get("schema") != "heptatrader.qualification-policy.v1":
            errors.append("qualification policy schema mismatch")


def validate() -> list[str]:
    errors: list[str] = []
    if not DOCS.is_dir():
        return ["docs/: missing documentation root"]
    validate_document_registry(errors)
    validate_registries(errors)
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
