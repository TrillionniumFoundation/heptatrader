#!/usr/bin/env python3
"""Fail closed when a repository-executable gap is not closed.

External qualification gates are derived from the canonical module registry.
They may remain open because their evidence must come from an independent
platform or broker environment; no repository fixture may synthesize closure.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_GAP_REGISTRY = ROOT / "docs/program/gap-registry-v2.json"
DEFAULT_MODULE_REGISTRY = ROOT / "docs/modules/module-registry-v2.json"


def _load(path: Path, label: str, errors: list[str]) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        errors.append(f"{label}: cannot load {path}: {exc}")
        return {}
    if not isinstance(value, dict):
        errors.append(f"{label}: root must be an object")
        return {}
    return value


def validate(
    gap_registry_path: Path = DEFAULT_GAP_REGISTRY,
    module_registry_path: Path = DEFAULT_MODULE_REGISTRY,
) -> list[str]:
    """Return deterministic validation errors for the exact repository tree."""
    errors: list[str] = []
    gap_doc = _load(gap_registry_path, "gap registry", errors)
    module_doc = _load(module_registry_path, "module registry", errors)
    if errors:
        return errors

    if gap_doc.get("schema") != "heptatrader.gap-registry.v2":
        errors.append("gap registry: schema mismatch")
    if module_doc.get("schema") != "heptatrader.module-registry.v2":
        errors.append("module registry: schema mismatch")

    allowed_states = gap_doc.get("allowed_states")
    if not isinstance(allowed_states, list) or not allowed_states:
        errors.append("gap registry: allowed_states must be a non-empty array")
        allowed_state_set: set[str] = set()
    else:
        allowed_state_set = {
            value for value in allowed_states if isinstance(value, str)
        }
        if len(allowed_state_set) != len(allowed_states):
            errors.append("gap registry: allowed_states must be unique strings")
    if "closed" not in allowed_state_set:
        errors.append("gap registry: closed state is required")

    policy = module_doc.get("implementation_evidence_policy")
    if not isinstance(policy, dict):
        errors.append("module registry: implementation_evidence_policy missing")
        policy = {}
    raw_external = policy.get("external_gate_ids")
    if not isinstance(raw_external, list):
        errors.append("module registry: external_gate_ids must be an array")
        external_gate_ids: set[str] = set()
    else:
        external_gate_ids = {
            value for value in raw_external if isinstance(value, str) and value
        }
        if len(external_gate_ids) != len(raw_external):
            errors.append(
                "module registry: external_gate_ids must be unique non-empty strings"
            )
    if policy.get("external_gates_fail_closed") is not True:
        errors.append("module registry: external_gates_fail_closed must be true")

    gaps = gap_doc.get("gaps")
    if not isinstance(gaps, list):
        errors.append("gap registry: gaps must be an array")
        return errors

    by_id: dict[str, dict[str, Any]] = {}
    for index, item in enumerate(gaps):
        label = f"gap registry: gaps[{index}]"
        if not isinstance(item, dict):
            errors.append(f"{label} must be an object")
            continue
        gap_id = item.get("id")
        if not isinstance(gap_id, str) or not gap_id:
            errors.append(f"{label} id missing")
            continue
        if gap_id in by_id:
            errors.append(f"gap registry: duplicate gap id {gap_id}")
            continue
        by_id[gap_id] = item
        state = item.get("state")
        if state not in allowed_state_set:
            errors.append(f"gap {gap_id}: invalid state {state!r}")
            continue
        if state != "closed" and gap_id not in external_gate_ids:
            errors.append(
                f"gap {gap_id}: repository-executable gap must be closed; "
                "only registered external qualification gates may remain open"
            )

    for gate_id in sorted(external_gate_ids):
        gate = by_id.get(gate_id)
        if gate is None:
            errors.append(
                f"module registry: external gate is absent from gap registry: {gate_id}"
            )
            continue
        state = gate.get("state")
        if state == "closed":
            errors.append(
                f"external gate {gate_id}: closed state requires a separate "
                "machine-verified qualification receipt; narrative closure is forbidden"
            )

    return errors


def summary(
    gap_registry_path: Path = DEFAULT_GAP_REGISTRY,
    module_registry_path: Path = DEFAULT_MODULE_REGISTRY,
) -> dict[str, Any]:
    gap_doc = json.loads(gap_registry_path.read_text(encoding="utf-8"))
    module_doc = json.loads(module_registry_path.read_text(encoding="utf-8"))
    policy = module_doc["implementation_evidence_policy"]
    external = set(policy["external_gate_ids"])
    gaps = gap_doc["gaps"]
    return {
        "schema": "heptatrader.gap-closure-summary.v1",
        "repository_executable_open": sorted(
            item["id"]
            for item in gaps
            if item["state"] != "closed" and item["id"] not in external
        ),
        "external_open": sorted(
            item["id"]
            for item in gaps
            if item["state"] != "closed" and item["id"] in external
        ),
        "closed": sorted(item["id"] for item in gaps if item["state"] == "closed"),
        "external_evidence_synthesized": False,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--gap-registry", type=Path, default=DEFAULT_GAP_REGISTRY)
    parser.add_argument(
        "--module-registry", type=Path, default=DEFAULT_MODULE_REGISTRY
    )
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)

    errors = validate(args.gap_registry, args.module_registry)
    for error in errors:
        print(f"[GAP-CLOSURE] {error}", file=sys.stderr)
    if errors:
        return 1
    result = summary(args.gap_registry, args.module_registry)
    if args.json:
        print(json.dumps(result, sort_keys=True, separators=(",", ":")))
    else:
        print(
            "[GAP-CLOSURE] PASS "
            f"repository_open={len(result['repository_executable_open'])} "
            f"external_open={','.join(result['external_open']) or 'none'}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
