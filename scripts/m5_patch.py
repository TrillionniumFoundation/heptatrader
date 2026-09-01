#!/usr/bin/env python3
"""Integrate module lifecycle authority and active multi-agent simulation."""

from __future__ import annotations

import json
from pathlib import Path
import subprocess

ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github/workflows/gap-closure-m5.yml"
SELF = Path(__file__)


def replace_once(text: str, old: str, new: str, label: str) -> str:
    count = text.count(old)
    if count != 1:
        raise SystemExit(f"{label}: expected one match, found {count}")
    return text.replace(old, new)


def write_json(path: Path, value: dict, compact: bool = False) -> None:
    rendered = (json.dumps(value, ensure_ascii=False, separators=(",", ":"))
                if compact else json.dumps(value, ensure_ascii=False, indent=2))
    path.write_text(rendered + "\n", encoding="utf-8")


def patch_cmake() -> None:
    path = ROOT / "HeptaTrade/CMakeLists.txt"
    text = path.read_text(encoding="utf-8")
    marker = '''add_library(hepta_allocation_revalidator STATIC
    execution/allocation_plan_revalidator.cpp)
hepta_runtime_target(hepta_allocation_revalidator)
target_link_libraries(hepta_allocation_revalidator PUBLIC
    hepta_global_allocator
    hepta_portfolio_core
    hepta_numeric_core)
'''
    additions = marker + '''

add_library(hepta_management_control STATIC
    management/module_lifecycle.cpp)
hepta_runtime_target(hepta_management_control)
target_link_libraries(hepta_management_control PUBLIC Threads::Threads)

add_library(hepta_multi_agent_simulator STATIC
    simulator/multi_agent_allocation.cpp)
hepta_runtime_target(hepta_multi_agent_simulator)
target_link_libraries(hepta_multi_agent_simulator PUBLIC
    hepta_management_control
    hepta_allocation_revalidator
    hepta_global_allocator
    hepta_proposal_aggregator
    hepta_strategy_runtime
    hepta_portfolio_core
    hepta_numeric_core)
'''
    path.write_text(
        replace_once(text, marker, additions, "M5 targets"),
        encoding="utf-8",
    )

    path = ROOT / "tests/CMakeLists.txt"
    text = path.read_text(encoding="utf-8")
    marker = '''add_executable(hepta_allocation_plan_revalidator_tests
    allocation_plan_revalidator_tests.cpp)
target_link_libraries(hepta_allocation_plan_revalidator_tests
    hepta_allocation_revalidator)
hepta_register_core_test(hepta_allocation_plan_revalidator_tests)
'''
    additions = marker + '''

add_executable(hepta_module_lifecycle_tests
    module_lifecycle_tests.cpp)
target_link_libraries(hepta_module_lifecycle_tests
    hepta_management_control)
hepta_register_core_test(hepta_module_lifecycle_tests)

add_executable(hepta_multi_agent_allocation_tests
    multi_agent_allocation_tests.cpp)
target_link_libraries(hepta_multi_agent_allocation_tests
    hepta_multi_agent_simulator)
hepta_register_core_test(hepta_multi_agent_allocation_tests)
'''
    path.write_text(
        replace_once(text, marker, additions, "M5 tests"),
        encoding="utf-8",
    )


def patch_ownership() -> None:
    path = ROOT / "docs/modules/source-ownership-registry-v1.json"
    value = json.loads(path.read_text(encoding="utf-8"))
    additions = [
        {
            "id": "management-control-runtime",
            "selector": {"kind": "directory", "path": "HeptaTrade/management/"},
            "physical_owner": "hepta.management.control",
            "priority": 200,
        },
        {
            "id": "multi-agent-simulator",
            "selector": {"kind": "prefix", "path": "HeptaTrade/simulator/multi_agent_allocation"},
            "physical_owner": "hepta.venue.simulator",
            "priority": 350,
        },
    ]
    ids = {item["id"] for item in value["physical_ownership_rules"]}
    for item in additions:
        if item["id"] not in ids:
            value["physical_ownership_rules"].append(item)
    write_json(path, value, compact=True)


def patch_manifests() -> None:
    path = ROOT / "docs/modules/manifests/hepta-management-control.json"
    value = json.loads(path.read_text(encoding="utf-8"))
    value["lifecycle"] = "current"
    value["build_targets"] = ["hepta_management_control"]
    value["verification"] = ["lifecycle-faults", "rollout-rollback"]
    write_json(path, value)

    path = ROOT / "docs/modules/manifests/hepta-venue-simulator.json"
    value = json.loads(path.read_text(encoding="utf-8"))
    targets = value.setdefault("build_targets", [])
    if "hepta_multi_agent_simulator" not in targets:
        targets.append("hepta_multi_agent_simulator")
    dependencies = value.setdefault("allowed_dependencies", [])
    for dependency in (
        "hepta.management.control",
        "hepta.global.decision",
        "hepta.strategy.runtime",
        "hepta.portfolio.compiler",
        "hepta.execution.runtime",
        "hepta.numeric.core",
    ):
        if dependency not in dependencies:
            dependencies.append(dependency)
    dependencies.sort()
    verification = value.setdefault("verification", [])
    for check in ("lifecycle-faults", "rollout-rollback"):
        if check not in verification:
            verification.append(check)
    write_json(path, value)


def patch_test_matrix_and_capabilities() -> None:
    path = ROOT / "docs/verification/test-matrix-v2.json"
    value = json.loads(path.read_text(encoding="utf-8"))
    evidence = {
        "lifecycle-faults": "generation fence, stale health and quarantine isolation CTests",
        "rollout-rollback": "staged upgrade, shadow divergence and previous-active rollback CTests",
    }
    found = set()
    for check in value["checks"]:
        if check["id"] in evidence:
            check["state"] = "implemented"
            check["evidence"] = evidence[check["id"]]
            found.add(check["id"])
    if found != set(evidence):
        raise SystemExit(f"missing lifecycle checks: {sorted(set(evidence)-found)}")
    write_json(path, value, compact=True)

    path = ROOT / "docs/product/capability-registry-v2.json"
    value = json.loads(path.read_text(encoding="utf-8"))
    found = set()
    for capability in value["capabilities"]:
        if capability["id"] == "hepta.management.module-lifecycle":
            found.add(capability["id"])
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
        elif capability["id"] == "hepta.global.multi-agent-allocation":
            found.add(capability["id"])
            capability["declared_state"] = "implemented"
            capability["implementation"] = "implemented-core"
            capability["integration"] = {
                "simulator": "active",
                "paper": "absent",
                "live": "forbidden",
            }
    if len(found) != 2:
        raise SystemExit("M5 capability entries missing")
    write_json(path, value)


def patch_schema_and_docs() -> None:
    digest = {"type": "string", "pattern": "^sha256:[0-9a-f]{64}$"}
    identity = {
        "type": "object",
        "additionalProperties": False,
        "required": [
            "module_id", "version", "artifact_digest", "config_digest"
        ],
        "properties": {
            "module_id": {
                "type": "string", "pattern": "^hepta\\.[A-Za-z0-9._:-]+$",
                "maxLength": 128,
            },
            "version": {"type": "string", "minLength": 1, "maxLength": 64},
            "artifact_digest": digest,
            "config_digest": digest,
            "model_digest": {
                "anyOf": [digest, {"type": "string", "maxLength": 0}]
            },
        },
    }
    schema = {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "title": "ModuleLifecycleSnapshotV1",
        "type": "object",
        "additionalProperties": False,
        "required": [
            "schema", "identity", "state", "generation", "updated_at_ms",
            "reason_code",
        ],
        "properties": {
            "schema": {"const": "hepta.module-lifecycle.v1"},
            "identity": identity,
            "state": {"enum": [
                "registered", "warming", "shadow", "active",
                "quarantined", "draining", "stopped"
            ]},
            "generation": {"type": "integer", "minimum": 1},
            "updated_at_ms": {"type": "integer", "minimum": 1},
            "reason_code": {
                "type": "string", "minLength": 1, "maxLength": 96,
                "pattern": "^[A-Z0-9_]+$",
            },
            "health": {
                "type": "object",
                "additionalProperties": False,
                "required": ["healthy", "observed_at_ms", "evidence_digest"],
                "properties": {
                    "healthy": {"type": "boolean"},
                    "observed_at_ms": {"type": "integer", "minimum": 0},
                    "evidence_digest": {
                        "anyOf": [digest, {"type": "string", "maxLength": 0}]
                    },
                },
            },
        },
    }
    write_json(ROOT / "schemas/module-lifecycle-v1.json", schema)

    (ROOT / "docs/contracts/MODULE-LIFECYCLE-CONTRACT.md").write_text(
        "# Module Lifecycle Contract V1\n\n"
        "Status: current normative\n"
        "Applies to: Management Control and simulator strategy modules\n"
        "Verification: lifecycle-faults and rollout-rollback CTests\n"
        "Authority: generation-fenced lifecycle state\n\n"
        "每个模块版本绑定 module/version、artifact/config/model digest 与单调 generation。"
        "允许状态为 REGISTERED → WARMING → SHADOW → ACTIVE → DRAINING → STOPPED；"
        "任意非 stopped 状态可被 fail-closed QUARANTINED。所有 transition 使用 expected generation，"
        "旧 generation、时间回退、过期/不健康 evidence 和非法状态跳转均拒绝。\n\n"
        "ACTIVE 升级会保存 previous-active identity 并进入 WARMING；若 shadow diverges 或运行故障，"
        "Management 可 quarantine 新版本，再以新 generation 恢复经健康验证的 previous active。"
        "Management 不持有 broker credential，不参与 tick hot path，也不能绕过 Execution。"
        "机器 schema 为 `schemas/module-lifecycle-v1.json`。\n",
        encoding="utf-8",
    )


def main() -> None:
    patch_cmake()
    patch_ownership()
    patch_manifests()
    patch_test_matrix_and_capabilities()
    patch_schema_and_docs()
    subprocess.run(
        ["python3", "scripts/generate_documentation_views.py", "--write"],
        cwd=ROOT,
        check=True,
    )
    WORKFLOW.unlink()
    SELF.unlink()


if __name__ == "__main__":
    main()
