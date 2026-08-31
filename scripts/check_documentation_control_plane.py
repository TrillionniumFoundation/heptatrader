#!/usr/bin/env python3
"""Validate the canonical Hepta Documentation Control Plane V2."""
from __future__ import annotations
from collections import defaultdict, deque
import json
from pathlib import Path
import re
import subprocess
import sys
from urllib.parse import unquote
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
DOCS = ROOT / "docs"
REGISTRY = DOCS / "document-registry-v2.json"
META = ("Status:", "Applies to:", "Verification:", "Authority:")
LINK_RE = re.compile(r"\[[^\]]*\]\(([^)]+)\)")
SHA_RE = re.compile(r"(?<![0-9a-f])[0-9a-f]{40}(?![0-9a-f])")
FORBIDDEN_DOC_DIRS = {"legacy", "proposals"}
FORBIDDEN_FILENAMES = {
    "PLAN.md", "EXACT-HEAD-CI.md", "EXACT-HEAD-FINAL.md",
    "EXACT-HEAD-RESULTS.md", "REMOTE-CLOSURE-AUDIT.json",
    "module-ownership-v1.json",
}
LEGACY_DOC_SUFFIXES = {".md", ".txt", ".pdf", ".png", ".jpg", ".jpeg", ".webp"}


def load(path: Path, errors: list[str]) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        errors.append(f"{path.relative_to(ROOT)}: invalid JSON: {exc}")
        return None


def indexed(items: Any, key: str, label: str, errors: list[str]) -> dict[str, dict[str, Any]]:
    if not isinstance(items, list):
        errors.append(f"{label}: expected array")
        return {}
    result: dict[str, dict[str, Any]] = {}
    for pos, item in enumerate(items):
        if not isinstance(item, dict):
            errors.append(f"{label}[{pos}]: expected object")
            continue
        value = item.get(key)
        if not isinstance(value, str) or not value:
            errors.append(f"{label}[{pos}]: missing {key}")
            continue
        if value in result:
            errors.append(f"{label}: duplicate {key}: {value}")
            continue
        result[value] = item
    return result


def acyclic(nodes: set[str], edges: dict[str, set[str]], label: str, errors: list[str]) -> None:
    indegree = {node: 0 for node in nodes}
    reverse: dict[str, set[str]] = defaultdict(set)
    for source, targets in edges.items():
        for target in targets:
            if source in nodes and target in nodes:
                indegree[source] += 1
                reverse[target].add(source)
    queue = deque(sorted(n for n, degree in indegree.items() if degree == 0))
    seen = 0
    while queue:
        node = queue.popleft(); seen += 1
        for dependent in sorted(reverse.get(node, ())):
            indegree[dependent] -= 1
            if indegree[dependent] == 0:
                queue.append(dependent)
    if seen != len(nodes):
        errors.append(f"{label}: dependency cycle: {', '.join(sorted(n for n,d in indegree.items() if d))}")


def check_markdown(path: Path, doc_class: str, errors: list[str]) -> None:
    try:
        text = path.read_text(encoding="utf-8-sig")
    except (OSError, UnicodeError) as exc:
        errors.append(f"{path.relative_to(ROOT)}: unreadable: {exc}")
        return
    lines = text.splitlines()[:12]
    for field in META:
        if not any(line.startswith(field) and line[len(field):].strip() for line in lines):
            errors.append(f"{path.relative_to(ROOT)}: missing metadata {field}")
    if "Status: current compatibility alias" in text or "Authority: none." in text:
        errors.append(f"{path.relative_to(ROOT)}: compatibility alias is forbidden")
    if doc_class == "normative" and SHA_RE.search(text):
        errors.append(f"{path.relative_to(ROOT)}: normative document hard-codes commit SHA")
    for raw in LINK_RE.findall(text):
        target = raw.strip().split(" ", 1)[0].strip("<>")
        if not target or target.startswith(("#", "http://", "https://", "mailto:")):
            continue
        target = unquote(target.split("#", 1)[0])
        resolved = (path.parent / target).resolve()
        try:
            resolved.relative_to(ROOT)
        except ValueError:
            errors.append(f"{path.relative_to(ROOT)}: link escapes repository: {raw}")
            continue
        if not resolved.exists():
            errors.append(f"{path.relative_to(ROOT)}: missing local link: {raw}")


def source_exists(prefix: str) -> bool:
    path = ROOT / prefix
    if path.exists():
        return True
    parent = path.parent
    if not parent.exists():
        return False
    return any(parent.glob(path.name + "*"))


def validate() -> list[str]:
    errors: list[str] = []
    if not DOCS.is_dir():
        return ["docs/: missing"]
    doc = load(REGISTRY, errors)
    if not isinstance(doc, dict):
        return errors
    if doc.get("schema") != "heptatrader.document-registry.v2":
        errors.append("document registry schema mismatch")
    documents = indexed(doc.get("documents"), "path", "document registry", errors)
    actual = {p.relative_to(DOCS).as_posix() for p in DOCS.rglob("*") if p.is_file()}
    for path in sorted(set(documents) - actual):
        errors.append(f"registered document missing: docs/{path}")
    for path in sorted(actual - set(documents)):
        errors.append(f"unregistered document: docs/{path}")
    if {p.name for p in DOCS.iterdir() if p.is_file()} != {"README.md", "document-registry-v2.json"}:
        errors.append("docs root may contain only README.md and document-registry-v2.json")
    for directory in FORBIDDEN_DOC_DIRS:
        if (DOCS / directory).exists():
            errors.append(f"forbidden historical directory: docs/{directory}")
    for path in DOCS.rglob("*"):
        if path.is_file() and path.name in FORBIDDEN_FILENAMES:
            errors.append(f"forbidden historical/status file: {path.relative_to(ROOT)}")
    for rel, entry in documents.items():
        cls = entry.get("class")
        if cls not in {"normative", "generated-view", "machine-registry"}:
            errors.append(f"docs/{rel}: invalid/legacy class {cls}")
        path = DOCS / rel
        if path.suffix.lower() == ".md":
            check_markdown(path, str(cls), errors)
    legacy = ROOT / "legacy"
    if legacy.exists():
        marker = legacy / "QUARANTINE.json"
        marker_doc = load(marker, errors) if marker.is_file() else None
        if not isinstance(marker_doc, dict) or marker_doc.get("active_dependency") is not False:
            errors.append("legacy/QUARANTINE.json missing or invalid")
        for path in legacy.rglob("*"):
            if path.is_file() and path.suffix.lower() in LEGACY_DOC_SUFFIXES:
                errors.append(f"historical documentation/media remains: {path.relative_to(ROOT)}")

    generator = ROOT / "scripts/generate_documentation_views.py"
    completed = subprocess.run([sys.executable, str(generator), "--check"], cwd=ROOT,
                               text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                               check=False)
    if completed.returncode:
        errors.append("generated views drift: " + completed.stdout.strip().replace("\n", " | "))

    contract_doc = load(DOCS / "contracts/contract-registry-v2.json", errors)
    module_doc = load(DOCS / "modules/module-registry-v2.json", errors)
    module_entries: list[dict[str, Any]] = []
    if isinstance(module_doc, dict):
        for manifest_path in module_doc.get("manifest_paths", []):
            if not isinstance(manifest_path, str):
                errors.append("module registry contains invalid manifest path")
                continue
            manifest = load(DOCS / manifest_path, errors)
            if isinstance(manifest, dict):
                module_entries.append(manifest)
    capability_doc = load(DOCS / "product/capability-registry-v2.json", errors)
    test_doc = load(DOCS / "verification/test-matrix-v2.json", errors)
    milestone_doc = load(DOCS / "program/milestone-registry-v1.json", errors)
    workstream_doc = load(DOCS / "program/workstream-registry-v1.json", errors)
    gap_doc = load(DOCS / "program/gap-registry-v2.json", errors)
    metric_doc = load(DOCS / "verification/metric-registry-v1.json", errors)
    reason_doc = load(DOCS / "verification/reason-code-registry-v1.json", errors)
    source_budget = load(DOCS / "modules/source-size-budget-v1.json", errors)

    contracts = indexed(contract_doc.get("contracts") if isinstance(contract_doc,dict) else None,
                        "id", "contract registry", errors)
    modules = indexed(module_entries, "id", "module registry", errors)
    checks = indexed(test_doc.get("checks") if isinstance(test_doc,dict) else None,
                     "id", "test matrix", errors)
    milestones = indexed(milestone_doc.get("milestones") if isinstance(milestone_doc,dict) else None,
                         "id", "milestone registry", errors)
    workstreams = indexed(workstream_doc.get("workstreams") if isinstance(workstream_doc,dict) else None,
                          "id", "workstream registry", errors)
    gaps = indexed(gap_doc.get("gaps") if isinstance(gap_doc,dict) else None,
                   "id", "gap registry", errors)

    if isinstance(contract_doc, dict) and contract_doc.get("schema") != "heptatrader.contract-registry.v2":
        errors.append("contract registry schema mismatch")
    if isinstance(module_doc, dict) and module_doc.get("schema") != "heptatrader.module-registry.v2":
        errors.append("module registry schema mismatch")
    if isinstance(test_doc, dict) and test_doc.get("schema") != "heptatrader.test-matrix.v2":
        errors.append("test matrix schema mismatch")

    cmake_text = "\n".join(p.read_text(encoding="utf-8-sig") for p in
                            (ROOT / "CMakeLists.txt", ROOT / "HeptaTrade/CMakeLists.txt") if p.is_file())
    actual_targets = set(re.findall(r"add_(?:library|executable)\s*\(\s*([A-Za-z0-9_.+-]+)", cmake_text))
    target_owners: dict[str, list[dict[str, Any]]] = defaultdict(list)
    graph: dict[str, set[str]] = defaultdict(set)
    required_module_fields = ("version","lifecycle","kind","trust_domain","authority",
                              "ownership_mode","source_roots","build_targets","provides","consumes",
                              "allowed_dependencies","forbidden_dependencies","state","concurrency",
                              "backpressure","failure","resource_budget","owners","verification")
    for mid, module in modules.items():
        for field in required_module_fields:
            if field not in module:
                errors.append(f"module {mid}: missing {field}")
        owners = module.get("owners")
        if not isinstance(owners, dict) or not all(owners.get(k) for k in ("dri","backup","reviewers")):
            errors.append(f"module {mid}: incomplete owners")
        if module.get("ownership_mode") == "shared-migration":
            gap = module.get("migration_gap")
            if gap not in gaps:
                errors.append(f"module {mid}: shared migration lacks valid gap")
        elif module.get("ownership_mode") != "exclusive":
            errors.append(f"module {mid}: invalid ownership mode")
        for dep in module.get("allowed_dependencies", []):
            if not isinstance(dep, str):
                errors.append(f"module {mid}: invalid dependency")
            elif "*" not in dep:
                if dep not in modules:
                    errors.append(f"module {mid}: unknown dependency {dep}")
                else:
                    graph[mid].add(dep)
        for relation in ("provides", "consumes"):
            values = module.get(relation, [])
            if not isinstance(values, list):
                errors.append(f"module {mid}: {relation} must be an array")
                continue
            if len(values) != len(set(values)):
                errors.append(f"module {mid}: duplicate {relation} contract")
            for cid in values:
                if cid not in contracts:
                    errors.append(f"module {mid}: unknown contract {cid}")
        for check in module.get("verification", []):
            if check not in checks:
                errors.append(f"module {mid}: unknown verification {check}")
        lifecycle = module.get("lifecycle")
        for source in module.get("source_roots", []):
            if lifecycle in {"current","experimental","unsupported"} and not source_exists(source):
                errors.append(f"module {mid}: current source root missing {source}")
        for target in module.get("build_targets", []):
            target_owners[target].append(module)
            if lifecycle in {"current","experimental"} and target not in actual_targets:
                errors.append(f"module {mid}: current target not found in CMake: {target}")
    acyclic(set(modules), graph, "module registry", errors)
    for target, owners in target_owners.items():
        if len(owners) <= 1:
            continue
        gaps_for_target = {o.get("migration_gap") for o in owners}
        if any(o.get("ownership_mode") != "shared-migration" for o in owners) or len(gaps_for_target) != 1:
            errors.append(f"build target ownership conflict: {target}")

    for cid, contract in contracts.items():
        docpath = contract.get("document")
        if not isinstance(docpath, str) or not (DOCS / docpath).is_file():
            errors.append(f"contract {cid}: canonical document missing")
        schema_path = contract.get("schema_path")
        if schema_path is not None:
            if not isinstance(schema_path, str) or not (ROOT / schema_path).is_file():
                errors.append(f"contract {cid}: schema missing {schema_path}")
            elif (ROOT / schema_path).suffix.lower() == ".json":
                load(ROOT / schema_path, errors)
        for relation in ("providers", "consumers"):
            values = contract.get(relation, [])
            if not isinstance(values, list):
                errors.append(f"contract {cid}: {relation} must be an array")
                continue
            if len(values) != len(set(values)):
                errors.append(f"contract {cid}: duplicate {relation}")
            for mid in values:
                if mid not in modules:
                    errors.append(f"contract {cid}: unknown module {mid}")
        for mid in contract.get("providers", []):
            if mid in modules and cid not in modules[mid].get("provides", []):
                errors.append(f"contract {cid}: provider {mid} does not declare provides")
        for mid in contract.get("consumers", []):
            if mid in modules and cid not in modules[mid].get("consumes", []):
                errors.append(f"contract {cid}: consumer {mid} does not declare consumes")

    capabilities = indexed(capability_doc.get("capabilities") if isinstance(capability_doc,dict) else None,
                           "id", "capability registry", errors)
    for capid, capability in capabilities.items():
        if capability.get("milestone") not in milestones:
            errors.append(f"capability {capid}: invalid milestone")
        for relation, index_map, label in (("modules", modules, "module"), ("contracts", contracts, "contract")):
            values = capability.get(relation, [])
            if not isinstance(values, list):
                errors.append(f"capability {capid}: {relation} must be an array")
                continue
            if len(values) != len(set(values)):
                errors.append(f"capability {capid}: duplicate {relation}")
            for value in values:
                if value not in index_map:
                    errors.append(f"capability {capid}: unknown {label} {value}")
        verification = capability.get("verification", [])
        if not verification:
            errors.append(f"capability {capid}: no verification")
        if isinstance(verification, list) and len(verification) != len(set(verification)):
            errors.append(f"capability {capid}: duplicate verification")
        for check in verification:
            if check not in checks:
                errors.append(f"capability {capid}: unknown verification {check}")
        if capability.get("declared_state") in {"implemented","qualified"} and not any(
                checks[c].get("state") == "implemented" for c in verification if c in checks):
            errors.append(f"capability {capid}: implemented claim lacks implemented check")
        if capability.get("integration", {}).get("live") not in {"forbidden", "not-applicable"} and capid != "hepta.venue.live":
            errors.append(f"capability {capid}: LIVE must remain forbidden or not-applicable")

    milestone_edges: dict[str,set[str]] = defaultdict(set)
    allowed_milestone_states = {"planned","in-progress","blocked","closed"}
    for mid, milestone in milestones.items():
        if milestone.get("state") not in allowed_milestone_states:
            errors.append(f"milestone {mid}: invalid state")
        for dep in milestone.get("depends_on", []):
            if dep not in milestones:
                errors.append(f"milestone {mid}: unknown dependency {dep}")
            else:
                milestone_edges[mid].add(dep)
    acyclic(set(milestones), milestone_edges, "milestone registry", errors)
    for wid, workstream in workstreams.items():
        if not workstream.get("owner"):
            errors.append(f"workstream {wid}: owner missing")
        for mid in workstream.get("milestones", []):
            if mid not in milestones:
                errors.append(f"workstream {wid}: unknown milestone {mid}")
    allowed_gap_states = set(gap_doc.get("allowed_states", [])) if isinstance(gap_doc,dict) else set()
    for gid, gap in gaps.items():
        if gap.get("state") not in allowed_gap_states:
            errors.append(f"gap {gid}: invalid state")
        if gap.get("workstream") not in workstreams or gap.get("milestone") not in milestones:
            errors.append(f"gap {gid}: invalid workstream/milestone")
        if not gap.get("evidence"):
            errors.append(f"gap {gid}: evidence missing")
        for check in gap.get("evidence", []):
            if check not in checks:
                errors.append(f"gap {gid}: unknown evidence {check}")

    if isinstance(metric_doc, dict):
        if metric_doc.get("schema") != "heptatrader.metric-registry.v1":
            errors.append("metric registry schema mismatch")
        forbidden = set(metric_doc.get("policy", {}).get("forbidden_labels", []))
        for metric in metric_doc.get("metrics", []):
            if metric.get("owner") not in modules:
                errors.append(f"metric {metric.get('name')}: unknown owner")
            bad = forbidden.intersection(metric.get("labels", []))
            if bad:
                errors.append(f"metric {metric.get('name')}: forbidden labels {sorted(bad)}")
    if isinstance(reason_doc, dict):
        if reason_doc.get("schema") != "heptatrader.reason-code-registry.v1":
            errors.append("reason-code registry schema mismatch")
        seen_codes: set[str] = set()
        for family in reason_doc.get("families", []):
            if family.get("owner") not in modules:
                errors.append(f"reason family {family.get('prefix')}: unknown owner")
            for code in family.get("codes", []):
                if code in seen_codes:
                    errors.append(f"duplicate reason code: {code}")
                seen_codes.add(code)
    if isinstance(source_budget, dict) and source_budget.get("schema") != "heptatrader.source-size-budget.v1":
        errors.append("source size budget schema mismatch")
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
