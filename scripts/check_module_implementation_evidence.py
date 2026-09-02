#!/usr/bin/env python3
"""Validate module implementation evidence and fail-closed external gates."""
from __future__ import annotations

import json
from pathlib import Path
import sys
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
REGISTRY_PATH = ROOT / "docs/modules/module-registry-v2.json"
GAP_REGISTRY_PATH = ROOT / "docs/program/gap-registry-v2.json"

_ALLOWED_ENFORCEMENT = {
    "documentation-ceiling",
    "test-checked",
    "negative-test-only",
}
_REQUIRED_BUDGET_FIELDS = {
    "max_threads",
    "max_queue_items",
    "max_queue_bytes",
    "max_memory_mib",
    "max_file_descriptors",
    "deadline_ms",
    "telemetry_series_max",
    "restart_burst_max",
}


def _read_json(path: Path, errors: list[str]) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        errors.append(f"{path.relative_to(ROOT)}: cannot load JSON: {exc}")
        return {}
    if not isinstance(value, dict):
        errors.append(f"{path.relative_to(ROOT)}: top-level value must be an object")
        return {}
    return value


def _safe_repo_path(raw: object, field: str, errors: list[str]) -> Path | None:
    if not isinstance(raw, str) or not raw.strip():
        errors.append(f"{field}: expected non-empty repository-relative path")
        return None
    path = Path(raw)
    if path.is_absolute() or ".." in path.parts:
        errors.append(f"{field}: unsafe path {raw!r}")
        return None
    resolved = (ROOT / path).resolve()
    try:
        resolved.relative_to(ROOT.resolve())
    except ValueError:
        errors.append(f"{field}: path escapes repository root: {raw!r}")
        return None
    if not resolved.exists():
        errors.append(f"{field}: evidence path does not exist: {raw}")
        return None
    return resolved


def _non_empty_strings(value: object, field: str, errors: list[str]) -> list[str]:
    if not isinstance(value, list) or not value:
        errors.append(f"{field}: expected a non-empty array")
        return []
    result: list[str] = []
    for index, item in enumerate(value):
        if not isinstance(item, str) or not item.strip():
            errors.append(f"{field}[{index}]: expected non-empty string")
            continue
        result.append(item)
    if len(set(result)) != len(result):
        errors.append(f"{field}: duplicate values are forbidden")
    return result


def validate(root: Path | None = None) -> list[str]:
    global ROOT, REGISTRY_PATH, GAP_REGISTRY_PATH
    if root is not None:
        ROOT = root.resolve()
        REGISTRY_PATH = ROOT / "docs/modules/module-registry-v2.json"
        GAP_REGISTRY_PATH = ROOT / "docs/program/gap-registry-v2.json"

    errors: list[str] = []
    registry = _read_json(REGISTRY_PATH, errors)
    gaps_document = _read_json(GAP_REGISTRY_PATH, errors)
    if not registry or not gaps_document:
        return errors

    policy = registry.get("implementation_evidence_policy")
    if not isinstance(policy, dict):
        return errors + ["module registry: implementation_evidence_policy missing"]

    if policy.get("schema") != "heptatrader.module-implementation-evidence.v1":
        errors.append("module registry: invalid implementation evidence schema")

    allowed_states = set(_non_empty_strings(
        policy.get("allowed_states"),
        "implementation_evidence_policy.allowed_states",
        errors,
    ))
    manifest_paths = _non_empty_strings(
        registry.get("manifest_paths"), "manifest_paths", errors
    )

    manifests: dict[str, dict[str, Any]] = {}
    for index, relative in enumerate(manifest_paths):
        manifest_path = ROOT / "docs" / relative
        manifest = _read_json(manifest_path, errors)
        module_id = manifest.get("id")
        if not isinstance(module_id, str) or not module_id:
            errors.append(f"manifest_paths[{index}]: manifest has no module id")
            continue
        if module_id in manifests:
            errors.append(f"manifest_paths[{index}]: duplicate module id {module_id}")
            continue
        manifests[module_id] = manifest

    raw_profiles = registry.get("resource_guardrail_profiles")
    if not isinstance(raw_profiles, dict) or not raw_profiles:
        errors.append("module registry: resource_guardrail_profiles must be a non-empty object")
        raw_profiles = {}
    profiles: dict[str, dict[str, Any]] = {}
    for profile_id, budget in raw_profiles.items():
        if not isinstance(profile_id, str) or not profile_id.strip():
            errors.append("resource_guardrail_profiles: profile id must be non-empty")
            continue
        if not isinstance(budget, dict):
            errors.append(f"resource_guardrail_profiles.{profile_id}: expected object")
            continue
        profiles[profile_id] = budget
        if budget.get("scope") != "repository-planning-ceiling-not-target-host-slo":
            errors.append(
                f"resource_guardrail_profiles.{profile_id}.scope: "
                "must remain explicitly non-SLO"
            )
        if budget.get("enforcement") not in _ALLOWED_ENFORCEMENT:
            errors.append(
                f"resource_guardrail_profiles.{profile_id}.enforcement: invalid value"
            )
        for field in sorted(_REQUIRED_BUDGET_FIELDS):
            value = budget.get(field)
            if not isinstance(value, int) or isinstance(value, bool) or value <= 0:
                errors.append(
                    f"resource_guardrail_profiles.{profile_id}.{field}: "
                    "expected positive integer planning ceiling"
                )

    raw_entries = registry.get("implementation_evidence")
    if not isinstance(raw_entries, list):
        return errors + ["module registry: implementation_evidence must be an array"]

    entries: dict[str, dict[str, Any]] = {}
    for index, raw in enumerate(raw_entries):
        field = f"implementation_evidence[{index}]"
        if not isinstance(raw, dict):
            errors.append(f"{field}: expected object")
            continue
        module_id = raw.get("module_id")
        if not isinstance(module_id, str) or not module_id:
            errors.append(f"{field}.module_id: expected non-empty string")
            continue
        if module_id in entries:
            errors.append(f"{field}.module_id: duplicate module {module_id}")
            continue
        entries[module_id] = raw

    manifest_ids = set(manifests)
    evidence_ids = set(entries)
    for missing in sorted(manifest_ids - evidence_ids):
        errors.append(f"implementation evidence missing module: {missing}")
    for extra in sorted(evidence_ids - manifest_ids):
        errors.append(f"implementation evidence references unknown module: {extra}")

    truth_floor = policy.get("truth_floor")
    if not isinstance(truth_floor, dict):
        errors.append("implementation_evidence_policy.truth_floor: expected object")
        truth_floor = {}

    external_gate_ids = set(_non_empty_strings(
        policy.get("external_gate_ids"),
        "implementation_evidence_policy.external_gate_ids",
        errors,
    ))
    gaps = {
        item.get("id"): item
        for item in gaps_document.get("gaps", [])
        if isinstance(item, dict) and isinstance(item.get("id"), str)
    }
    for gate_id in sorted(external_gate_ids):
        gap = gaps.get(gate_id)
        if gap is None:
            errors.append(f"external gate is absent from gap registry: {gate_id}")
        elif gap.get("state") == "closed":
            receipt = gap.get("qualification_receipt")
            if not isinstance(receipt, str) or not receipt.strip():
                errors.append(
                    f"external gate {gate_id} is closed without a separate "
                    "machine-readable external qualification receipt"
                )
            else:
                _safe_repo_path(
                    receipt,
                    f"gap-registry.{gate_id}.qualification_receipt",
                    errors,
                )

    for module_id in sorted(manifest_ids & evidence_ids):
        entry = entries[module_id]
        state = entry.get("state")
        if state not in allowed_states:
            errors.append(f"{module_id}.state: invalid state {state!r}")

        expected = truth_floor.get(module_id)
        if expected is not None and state != expected:
            errors.append(
                f"{module_id}.state: truth floor requires {expected!r}, got {state!r}"
            )

        implemented_scope = _non_empty_strings(
            entry.get("implemented_scope"),
            f"{module_id}.implemented_scope",
            errors,
        )
        excluded_scope = entry.get("excluded_scope")
        if not isinstance(excluded_scope, list):
            errors.append(f"{module_id}.excluded_scope: expected array")
            excluded_scope = []
        else:
            for index, item in enumerate(excluded_scope):
                if not isinstance(item, str) or not item.strip():
                    errors.append(
                        f"{module_id}.excluded_scope[{index}]: expected non-empty string"
                    )
            if len(set(excluded_scope)) != len(excluded_scope):
                errors.append(f"{module_id}.excluded_scope: duplicate values forbidden")

        if state != "implemented" and not excluded_scope:
            errors.append(
                f"{module_id}.excluded_scope: non-implemented state must identify limits"
            )
        if state == "implemented" and excluded_scope:
            errors.append(
                f"{module_id}.excluded_scope: implemented state cannot hide exclusions"
            )
        if not implemented_scope:
            errors.append(f"{module_id}: no implemented repository scope declared")

        for evidence_field in ("source_evidence", "test_evidence"):
            paths = _non_empty_strings(
                entry.get(evidence_field),
                f"{module_id}.{evidence_field}",
                errors,
            )
            for index, raw_path in enumerate(paths):
                resolved = _safe_repo_path(
                    raw_path, f"{module_id}.{evidence_field}[{index}]", errors
                )
                if resolved is None:
                    continue
                relative = resolved.relative_to(ROOT)
                if evidence_field == "test_evidence" and relative.parts[0] != "tests":
                    errors.append(
                        f"{module_id}.{evidence_field}[{index}]: "
                        "test evidence must live under tests/"
                    )
                if evidence_field == "source_evidence" and relative.parts[0] == "tests":
                    errors.append(
                        f"{module_id}.{evidence_field}[{index}]: "
                        "source evidence cannot live under tests/"
                    )

        gates = entry.get("external_gates")
        if not isinstance(gates, list):
            errors.append(f"{module_id}.external_gates: expected array")
            gates = []
        for index, gate in enumerate(gates):
            if gate not in external_gate_ids:
                errors.append(
                    f"{module_id}.external_gates[{index}]: unknown gate {gate!r}"
                )
        if state == "external-qualification-required" and not gates:
            errors.append(
                f"{module_id}: external-qualification-required needs an external gate"
            )

        profile_id = entry.get("resource_guardrail_profile")
        if not isinstance(profile_id, str) or profile_id not in profiles:
            errors.append(
                f"{module_id}.resource_guardrail_profile: unknown profile {profile_id!r}"
            )

        manifest_budget = manifests[module_id].get("resource_budget")
        if not isinstance(manifest_budget, str) or not manifest_budget.strip():
            errors.append(f"{module_id}: manifest resource_budget is unresolved")

    return errors


def main() -> int:
    errors = validate()
    for error in errors:
        print(f"[MODULE-IMPLEMENTATION-EVIDENCE] {error}", file=sys.stderr)
    if errors:
        return 1
    print("[MODULE-IMPLEMENTATION-EVIDENCE] PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
