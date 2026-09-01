#!/usr/bin/env python3
"""Finalize M4/M5 registries with an acyclic simulation orchestration module."""

from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SIMULATION_MODULE = "hepta.simulation.runtime"
SIMULATION_MANIFEST = "modules/manifests/hepta-simulation-runtime.json"


def read_json(relative: str):
    return json.loads((ROOT / relative).read_text(encoding="utf-8"))


def write_json(relative: str, value, *, compact: bool = False) -> None:
    path = ROOT / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    if compact:
        rendered = json.dumps(value, ensure_ascii=False, separators=(",", ":"))
    else:
        rendered = json.dumps(value, ensure_ascii=False, indent=2)
    path.write_text(rendered + "\n", encoding="utf-8")


def patch_fixture_digests() -> None:
    for relative in (
        "tests/module_lifecycle_tests.cpp",
        "tests/multi_agent_allocation_tests.cpp",
    ):
        path = ROOT / relative
        text = path.read_text(encoding="utf-8")
        text = text.replace("Digest('m')", "Digest('d')")
        text = text.replace("Digest('h')", "Digest('e')")
        text = text.replace("char evidence = 'h'", "char evidence = 'e'")
        path.write_text(text, encoding="utf-8")


def patch_source_ownership() -> None:
    relative = "docs/modules/source-ownership-registry-v1.json"
    registry = read_json(relative)
    found = False
    for rule in registry["physical_ownership_rules"]:
        if rule.get("id") != "multi-agent-simulator":
            continue
        rule["selector"] = {
            "kind": "prefix",
            "path": "HeptaTrade/simulator/multi_agent_allocation",
        }
        rule["physical_owner"] = SIMULATION_MODULE
        rule["priority"] = 350
        found = True
    if not found:
        registry["physical_ownership_rules"].append({
            "id": "multi-agent-simulator",
            "selector": {
                "kind": "prefix",
                "path": "HeptaTrade/simulator/multi_agent_allocation",
            },
            "physical_owner": SIMULATION_MODULE,
            "priority": 350,
        })
    write_json(relative, registry, compact=True)


def patch_module_manifests() -> list[str]:
    venue_relative = "docs/modules/manifests/hepta-venue-simulator.json"
    venue = read_json(venue_relative)
    # The venue module owns only the deterministic execution venue.  The
    # multi-Agent orchestration target consumes Execution revalidation and
    # therefore must not be claimed by the venue module that Execution itself
    # consumes.
    venue["source_roots"] = [
        "HeptaTrade/simulator/deterministic_execution_venue"
    ]
    venue["build_targets"] = [
        target for target in venue.get("build_targets", [])
        if target != "hepta_multi_agent_simulator"
    ]
    orchestration_dependencies = {
        "hepta.execution.runtime",
        "hepta.global.decision",
        "hepta.management.control",
        "hepta.numeric.core",
        "hepta.portfolio.compiler",
        "hepta.strategy.runtime",
    }
    venue["allowed_dependencies"] = sorted(
        dependency
        for dependency in venue.get("allowed_dependencies", [])
        if dependency not in orchestration_dependencies
    )
    venue["verification"] = [
        check for check in venue.get("verification", [])
        if check not in {"lifecycle-faults", "rollout-rollback"}
    ]
    write_json(venue_relative, venue)

    consumed_contracts = [
        "hepta.allocation-plan.v1",
        "hepta.authoritative-snapshot.v2",
        "hepta.global-optimization.v1",
        "hepta.module-lifecycle.v1",
        "hepta.numeric.fixed-v1",
        "hepta.solver-result.v1",
        "hepta.strategy-proposal.v1",
        "portfolio.net-target.v1",
        "proposal-set.v1",
    ]
    simulation_manifest = {
        "schema": "heptatrader.module-manifest.v2",
        "id": SIMULATION_MODULE,
        "version": "1.0.0",
        "lifecycle": "current",
        "kind": "simulation-orchestrator",
        "trust_domain": "simulation-control",
        "authority": (
            "simulation-only multi-agent orchestration without broker "
            "mutation authority"
        ),
        "ownership_mode": "exclusive",
        "source_roots": [
            "HeptaTrade/simulator/multi_agent_allocation"
        ],
        "build_targets": ["hepta_multi_agent_simulator"],
        "provides": [],
        "consumes": consumed_contracts,
        "allowed_dependencies": sorted([
            "hepta.execution.runtime",
            "hepta.global.decision",
            "hepta.management.control",
            "hepta.numeric.core",
            "hepta.portfolio.compiler",
            "hepta.strategy.runtime",
        ]),
        "forbidden_dependencies": [
            "broker.credentials",
            "hepta.gateway.runtime",
            "hepta.venue.*",
        ],
        "state": {
            "model": "ephemeral-cycle",
            "persistence": "none",
            "writer": "single-owner",
        },
        "concurrency": {
            "model": "capital-pool-cycle",
            "shard_key": "capital-pool",
            "blocking_io": "forbidden",
            "cross_module_lock": "forbidden",
        },
        "backpressure": {
            "class": "bounded-proposal-set",
            "overflow": "typed-failure",
        },
        "failure": {
            "risk_increase": "no-plan",
            "safe_exit": "never-weaken",
        },
        "resource_budget": "multi-agent-simulator-v1",
        "owners": {
            "dri": "@hepta/simulator",
            "backup": "@hepta/global-allocation",
            "reviewers": [
                "@hepta/architecture",
                "@hepta/execution-safety",
            ],
        },
        "verification": [
            "proposal-completeness",
            "optimizer-determinism",
            "constraint-properties",
            "shadow-parity",
            "lifecycle-faults",
            "rollout-rollback",
        ],
    }
    write_json("docs/" + SIMULATION_MANIFEST, simulation_manifest)
    return consumed_contracts


def patch_registries(consumed_contracts: list[str]) -> None:
    relative = "docs/modules/module-registry-v2.json"
    registry = read_json(relative)
    if SIMULATION_MANIFEST not in registry["manifest_paths"]:
        registry["manifest_paths"].append(SIMULATION_MANIFEST)
    registry["manifest_paths"].sort()
    write_json(relative, registry)

    relative = "docs/document-registry-v2.json"
    registry = read_json(relative)
    if not any(
        item.get("path") == SIMULATION_MANIFEST
        for item in registry["documents"]
    ):
        registry["documents"].append({
            "path": SIMULATION_MANIFEST,
            "class": "machine-registry",
            "owner": "@hepta/platform",
        })
    registry["documents"].sort(key=lambda item: item["path"])
    write_json(relative, registry, compact=True)

    relative = "docs/contracts/contract-registry-v2.json"
    registry = read_json(relative)
    remaining = set(consumed_contracts)
    for contract in registry["contracts"]:
        if contract.get("id") not in remaining:
            continue
        consumers = contract.setdefault("consumers", [])
        if SIMULATION_MODULE not in consumers:
            consumers.append(SIMULATION_MODULE)
            consumers.sort()
        remaining.remove(contract["id"])
    if remaining:
        raise SystemExit(
            "simulation contracts missing from registry: "
            + ", ".join(sorted(remaining))
        )
    write_json(relative, registry)

    relative = "docs/product/capability-registry-v2.json"
    registry = read_json(relative)
    found_global = False
    found_lifecycle = False
    for capability in registry["capabilities"]:
        if capability.get("id") == "hepta.global.multi-agent-allocation":
            found_global = True
            modules = capability.setdefault("modules", [])
            if SIMULATION_MODULE not in modules:
                modules.append(SIMULATION_MODULE)
                modules.sort()
            verification = capability.setdefault("verification", [])
            for check in ("lifecycle-faults", "rollout-rollback"):
                if check not in verification:
                    verification.append(check)
            capability["declared_state"] = "implemented"
            capability["design"] = "approved"
            capability["implementation"] = "implemented-core"
            capability["build"] = "default"
            capability["integration"] = {
                "simulator": "active",
                "paper": "absent",
                "live": "forbidden",
            }
        elif capability.get("id") == "hepta.management.module-lifecycle":
            found_lifecycle = True
            capability["declared_state"] = "experimental"
            capability["design"] = "approved"
            capability["implementation"] = "implemented-core"
            capability["build"] = "default"
            capability["integration"] = {
                "simulator": "active",
                "paper": "absent",
                "live": "forbidden",
            }
            capability["release"] = "core"
    if not found_global or not found_lifecycle:
        raise SystemExit("M5 capability entries are missing")
    write_json(relative, registry)


def close_verified_program_gaps() -> None:
    relative = "docs/program/gap-registry-v2.json"
    registry = read_json(relative)
    expected = {"G-OPT-001", "G-OPT-002", "G-OPT-003", "G-LIFE-001"}
    seen: set[str] = set()
    for gap in registry["gaps"]:
        if gap.get("id") in expected:
            gap["state"] = "closed"
            seen.add(gap["id"])
    if seen != expected:
        raise SystemExit("closure gaps missing: " + ", ".join(sorted(expected - seen)))
    write_json(relative, registry, compact=True)

    relative = "docs/program/milestone-registry-v1.json"
    registry = read_json(relative)
    expected_milestones = {"M4", "M5"}
    seen_milestones: set[str] = set()
    for milestone in registry["milestones"]:
        if milestone.get("id") in expected_milestones:
            milestone["state"] = "closed"
            seen_milestones.add(milestone["id"])
    if seen_milestones != expected_milestones:
        raise SystemExit(
            "closure milestones missing: "
            + ", ".join(sorted(expected_milestones - seen_milestones))
        )
    write_json(relative, registry)


def main() -> None:
    patch_fixture_digests()
    patch_source_ownership()
    consumed_contracts = patch_module_manifests()
    patch_registries(consumed_contracts)
    close_verified_program_gaps()


if __name__ == "__main__":
    main()
