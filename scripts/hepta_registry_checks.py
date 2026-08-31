#!/usr/bin/env python3
"""Cross-registry validation for the Hepta Documentation Control Plane."""
from __future__ import annotations

from collections import defaultdict

from hepta_document_checks import (
    DOCS, MODULE_SCHEMA, ROOT, _current_cmake_targets, acyclic, indexed,
    load, validate_module_manifest,
)
from hepta_module_boundaries import (
    ACTIVE_LIFECYCLES, active_source_files, canonical_relative_path,
    load_modules, load_source_ownership, parse_source_rules,
    selector_from_manifest_claim, selector_from_object, selector_matches,
)

def _validate_registries(errors: list[str]) -> None:
    contract_doc = load(DOCS / "contracts/contract-registry-v2.json", errors)
    capability_doc = load(DOCS / "product/capability-registry-v2.json", errors)
    test_doc = load(DOCS / "verification/test-matrix-v2.json", errors)
    milestone_doc = load(DOCS / "program/milestone-registry-v1.json", errors)
    workstream_doc = load(DOCS / "program/workstream-registry-v1.json", errors)
    gap_doc = load(DOCS / "program/gap-registry-v2.json", errors)
    metric_doc = load(DOCS / "verification/metric-registry-v1.json", errors)
    reason_doc = load(DOCS / "verification/reason-code-registry-v1.json", errors)
    source_budget = load(DOCS / "modules/source-size-budget-v1.json", errors)
    module_schema = load(MODULE_SCHEMA, errors)
    modules, module_registry = load_modules(ROOT, errors)
    source_ownership = load_source_ownership(ROOT, errors)

    contracts = indexed(
        contract_doc.get("contracts") if isinstance(contract_doc, dict) else None,
        "id", "contract registry", errors,
    )
    checks = indexed(
        test_doc.get("checks") if isinstance(test_doc, dict) else None,
        "id", "test matrix", errors,
    )
    milestones = indexed(
        milestone_doc.get("milestones") if isinstance(milestone_doc, dict) else None,
        "id", "milestone registry", errors,
    )
    workstreams = indexed(
        workstream_doc.get("workstreams") if isinstance(workstream_doc, dict) else None,
        "id", "workstream registry", errors,
    )
    gaps = indexed(
        gap_doc.get("gaps") if isinstance(gap_doc, dict) else None,
        "id", "gap registry", errors,
    )

    expected_schemas = (
        (contract_doc, "heptatrader.contract-registry.v2", "contract registry"),
        (capability_doc, "heptatrader.capability-registry.v2", "capability registry"),
        (test_doc, "heptatrader.test-matrix.v2", "test matrix"),
        (milestone_doc, "heptatrader.milestone-registry.v1", "milestone registry"),
        (workstream_doc, "heptatrader.workstream-registry.v1", "workstream registry"),
        (gap_doc, "heptatrader.gap-registry.v2", "gap registry"),
        (metric_doc, "heptatrader.metric-registry.v1", "metric registry"),
        (reason_doc, "heptatrader.reason-code-registry.v1", "reason-code registry"),
        (source_budget, "heptatrader.source-size-budget.v1", "source-size budget"),
        (module_registry, "heptatrader.module-registry.v2", "module registry"),
        (source_ownership, "heptatrader.source-ownership-registry.v1", "source ownership registry"),
    )
    for document, expected, label in expected_schemas:
        if isinstance(document, dict) and document.get("schema") != expected:
            errors.append(f"{label} schema mismatch")

    # Apply the formal module schema before semantic cross-reference checks.
    for module_id, manifest in modules.items():
        raw = dict(manifest)
        manifest_path = raw.pop("__manifest_path", f"module {module_id}")
        validate_module_manifest(raw, module_schema, manifest_path, errors)

    module_edges: dict[str, set[str]] = defaultdict(set)
    target_owners: dict[str, list[str]] = defaultdict(list)
    actual_targets = _current_cmake_targets()
    optional_targets = {
        value for value in source_ownership.get("optional_targets", [])
        if isinstance(value, str)
    } if isinstance(source_ownership, dict) else set()

    for module_id, manifest in modules.items():
        ownership_mode = manifest.get("ownership_mode")
        gap_id = manifest.get("migration_gap")
        if ownership_mode == "shared-migration":
            gap = gaps.get(gap_id) if isinstance(gap_id, str) else None
            if gap is None:
                errors.append(f"module {module_id}: shared migration has unknown gap {gap_id}")
            elif gap.get("state") == "closed":
                errors.append(f"module {module_id}: shared migration remains after closed gap {gap_id}")
        for raw in manifest.get("source_roots", []):
            if not isinstance(raw, str):
                continue
            try:
                selector = selector_from_manifest_claim(ROOT, raw)
            except ValueError as exc:
                errors.append(f"module {module_id}: invalid source root {raw!r}: {exc}")
                continue
            if manifest.get("lifecycle") in ACTIVE_LIFECYCLES:
                base = ROOT / selector.path.rstrip("/")
                if selector.kind == "directory":
                    exists = base.is_dir()
                elif selector.kind == "file":
                    exists = base.is_file()
                else:
                    parent = base.parent
                    exists = parent.is_dir() and any(
                        child.name.startswith(base.name) for child in parent.iterdir()
                    )
                if not exists:
                    errors.append(f"module {module_id}: active source root is empty/missing: {raw}")
        for dependency in manifest.get("allowed_dependencies", []):
            if not isinstance(dependency, str):
                continue
            if dependency.endswith(".*"):
                continue
            if dependency not in modules:
                errors.append(f"module {module_id}: unknown dependency {dependency}")
            else:
                module_edges[module_id].add(dependency)
        for relation in ("provides", "consumes"):
            for contract_id in manifest.get(relation, []):
                if contract_id not in contracts:
                    errors.append(f"module {module_id}: unknown contract {contract_id}")
        for check_id in manifest.get("verification", []):
            if check_id not in checks:
                errors.append(f"module {module_id}: unknown verification {check_id}")
        for target in manifest.get("build_targets", []):
            if isinstance(target, str):
                target_owners[target].append(module_id)
                if (
                    manifest.get("lifecycle") in {"current", "experimental"}
                    and target not in actual_targets
                    and target not in optional_targets
                ):
                    errors.append(f"module {module_id}: current CMake target missing: {target}")
    acyclic(set(modules), module_edges, "module registry", errors)
    for target, owners in sorted(target_owners.items()):
        current = [
            owner for owner in owners
            if modules[owner].get("lifecycle") in {"current", "experimental"}
        ]
        if len(set(current)) > 1:
            errors.append(
                f"CMake target has multiple current owners: {target}: {', '.join(sorted(current))}"
            )

    for contract_id, contract in contracts.items():
        document = contract.get("document")
        if not isinstance(document, str) or not (DOCS / document).is_file():
            errors.append(f"contract {contract_id}: canonical document missing: {document}")
        schema_path = contract.get("schema_path")
        if schema_path is not None:
            try:
                canonical = canonical_relative_path(ROOT, schema_path, allow_trailing_slash=False)
            except ValueError as exc:
                errors.append(f"contract {contract_id}: invalid schema path: {exc}")
            else:
                schema_file = ROOT / canonical
                if not schema_file.is_file():
                    errors.append(f"contract {contract_id}: schema missing: {canonical}")
                elif schema_file.suffix.lower() == ".json":
                    load(schema_file, errors)
        for relation, inverse in (("providers", "provides"), ("consumers", "consumes")):
            values = contract.get(relation, [])
            if not isinstance(values, list):
                errors.append(f"contract {contract_id}: {relation} must be an array")
                continue
            if len(values) != len(set(values)):
                errors.append(f"contract {contract_id}: duplicate {relation}")
            for module_id in values:
                if module_id not in modules:
                    errors.append(f"contract {contract_id}: unknown {relation[:-1]} {module_id}")
                elif contract_id not in modules[module_id].get(inverse, []):
                    errors.append(
                        f"contract {contract_id}: {module_id} does not declare {inverse}"
                    )

    capabilities = indexed(
        capability_doc.get("capabilities") if isinstance(capability_doc, dict) else None,
        "id", "capability registry", errors,
    )
    for capability_id, capability in capabilities.items():
        if capability.get("milestone") not in milestones:
            errors.append(f"capability {capability_id}: invalid milestone")
        for relation, mapping in (("modules", modules), ("contracts", contracts), ("verification", checks)):
            values = capability.get(relation, [])
            if not isinstance(values, list):
                errors.append(f"capability {capability_id}: {relation} must be an array")
                continue
            if len(values) != len(set(values)):
                errors.append(f"capability {capability_id}: duplicate {relation}")
            for value in values:
                if value not in mapping:
                    errors.append(f"capability {capability_id}: unknown {relation[:-1]} {value}")

    milestone_edges: dict[str, set[str]] = defaultdict(set)
    for milestone_id, milestone in milestones.items():
        for dependency in milestone.get("depends_on", []):
            if dependency not in milestones:
                errors.append(f"milestone {milestone_id}: unknown dependency {dependency}")
            else:
                milestone_edges[milestone_id].add(dependency)
    acyclic(set(milestones), milestone_edges, "milestone registry", errors)

    for workstream_id, workstream in workstreams.items():
        for milestone in workstream.get("milestones", []):
            if milestone not in milestones:
                errors.append(f"workstream {workstream_id}: unknown milestone {milestone}")

    allowed_gap_states = set(gap_doc.get("allowed_states", [])) if isinstance(gap_doc, dict) else set()
    for gap_id, gap in gaps.items():
        if gap.get("state") not in allowed_gap_states:
            errors.append(f"gap {gap_id}: invalid state")
        if gap.get("workstream") not in workstreams:
            errors.append(f"gap {gap_id}: unknown workstream")
        if gap.get("milestone") not in milestones:
            errors.append(f"gap {gap_id}: unknown milestone")
        evidence = gap.get("evidence")
        if not isinstance(evidence, list) or not evidence:
            errors.append(f"gap {gap_id}: evidence requirements missing")
        else:
            for check_id in evidence:
                if check_id not in checks:
                    errors.append(f"gap {gap_id}: unknown evidence check {check_id}")

    # Source ownership rules and all exceptions are themselves bounded by
    # current modules, open migration gaps and exact repository paths.
    if isinstance(source_ownership, dict):
        active = [path.relative_to(ROOT).as_posix() for path in active_source_files(ROOT, source_ownership)]
        rules = parse_source_rules(ROOT, source_ownership, errors)
        for rule in rules:
            if rule.physical_owner not in modules:
                errors.append(f"source rule {rule.rule_id}: unknown owner {rule.physical_owner}")
            if not any(selector_matches(relative, rule.selector) for relative in active):
                errors.append(f"source rule {rule.rule_id}: selector matches no active source")
        overlap_ids: set[str] = set()
        for position, item in enumerate(source_ownership.get("source_overlap_exceptions", [])):
            label = f"source overlap exception[{position}]"
            if not isinstance(item, dict):
                errors.append(f"{label}: expected object")
                continue
            exception_id = item.get("id")
            if not isinstance(exception_id, str) or not exception_id:
                errors.append(f"{label}: missing id")
            elif exception_id in overlap_ids:
                errors.append(f"duplicate source overlap exception: {exception_id}")
            else:
                overlap_ids.add(exception_id)
            participants = item.get("participants")
            if not isinstance(participants, list) or len(set(participants)) < 2:
                errors.append(f"{label}: participants must contain at least two unique modules")
                participants = []
            gap_id = item.get("gap")
            physical_owner = item.get("physical_owner")
            for module_id in participants:
                module = modules.get(module_id)
                if module is None:
                    errors.append(f"{label}: unknown participant {module_id}")
                elif module.get("ownership_mode") != "shared-migration":
                    errors.append(f"{label}: participant {module_id} is not shared-migration")
                elif module.get("migration_gap") != gap_id:
                    errors.append(f"{label}: participant {module_id} uses a different gap")
            if physical_owner not in participants:
                errors.append(f"{label}: physical_owner must be a participant")
            gap = gaps.get(gap_id) if isinstance(gap_id, str) else None
            if gap is None or gap.get("state") == "closed":
                errors.append(f"{label}: gap must exist and remain open")
            elif gap.get("milestone") != item.get("milestone"):
                errors.append(f"{label}: milestone must equal the gap milestone")
            if item.get("new_participants_forbidden") is not True:
                errors.append(f"{label}: new participants must be forbidden")
            scopes = item.get("scopes")
            if not isinstance(scopes, list) or not scopes:
                errors.append(f"{label}: scopes missing")
            else:
                for raw_selector in scopes:
                    try:
                        selector_from_object(ROOT, raw_selector)
                    except ValueError as exc:
                        errors.append(f"{label}: invalid scope: {exc}")
            if not isinstance(item.get("exit"), str) or not item["exit"].strip():
                errors.append(f"{label}: exit condition missing")
        compilation_pairs: set[tuple[str, str]] = set()
        for position, item in enumerate(source_ownership.get("compilation_exceptions", [])):
            label = f"compilation exception[{position}]"
            if not isinstance(item, dict):
                errors.append(f"{label}: expected object")
                continue
            target = item.get("target")
            source = item.get("source")
            if not isinstance(target, str) or not target:
                errors.append(f"{label}: invalid target")
                continue
            try:
                source = canonical_relative_path(ROOT, source, allow_trailing_slash=False)
            except ValueError as exc:
                errors.append(f"{label}: invalid source: {exc}")
                continue
            pair = (target, source)
            if pair in compilation_pairs:
                errors.append(f"duplicate compilation exception: {target} -> {source}")
            compilation_pairs.add(pair)
            if not (ROOT / source).is_file():
                errors.append(f"{label}: source is missing: {source}")
            target_owner = item.get("target_owner")
            if target_owner != "hepta.tests" and target_owner not in modules:
                errors.append(f"{label}: unknown target owner {target_owner}")
            source_owner = item.get("source_owner")
            if source_owner is not None and source_owner not in modules:
                errors.append(f"{label}: unknown source owner {source_owner}")
            gap_id = item.get("gap")
            gap = gaps.get(gap_id) if isinstance(gap_id, str) else None
            if gap is None or gap.get("state") == "closed":
                errors.append(f"{label}: gap must exist and remain open")
            elif gap.get("milestone") != item.get("milestone"):
                errors.append(f"{label}: milestone must equal the gap milestone")
            if item.get("profile") not in {"core", "ib-paper"}:
                errors.append(f"{label}: invalid profile")
        for target in optional_targets:
            if target not in actual_targets:
                errors.append(f"optional target is not defined in CMake source: {target}")


