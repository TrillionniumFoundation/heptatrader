#!/usr/bin/env python3
"""Integrate the M4 proposal, allocation and Execution-shadow runtime."""

from __future__ import annotations

import json
from pathlib import Path
import subprocess

ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github/workflows/gap-closure-m4.yml"
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


def patch_safety_edges() -> None:
    path = ROOT / "HeptaTrade/allocation/global_allocator.cpp"
    text = path.read_text(encoding="utf-8")
    text = replace_once(
        text,
        'proposalSet.proposals[i].moduleId);\n        AppendField(key, "candidate", choices[i] < 0 ? "~" :',
        'proposalSet.proposals[i].moduleId);\n        AppendField(key, "candidate", choices[i] < 0 ? "!" :',
        "zero-utility reject tie break",
    )
    path.write_text(text, encoding="utf-8")

    path = ROOT / "HeptaTrade/execution/allocation_plan_revalidator.cpp"
    text = path.read_text(encoding="utf-8")
    marker = '''AllocationPlanRevalidationResult Reject(const char* code)
{
    AllocationPlanRevalidationResult result;
    result.reasonCode = code;
    return result;
}

'''
    checked = marker + '''bool CheckedSubtract(
    DecisionMicrounits left,
    DecisionMicrounits right,
    DecisionMicrounits& out)
{
    if ((right < 0 && left >
            std::numeric_limits<DecisionMicrounits>::max() + right) ||
        (right > 0 && left <
            std::numeric_limits<DecisionMicrounits>::min() + right))
        return false;
    out = left - right;
    return true;
}

'''
    text = replace_once(text, marker, checked, "checked solver gap")
    text = replace_once(
        text,
        '#include <limits>\n#include <map>\n',
        '#include <limits>\n#include <map>\n',
        "limits include",
    ) if '#include <limits>\n' in text else text.replace(
        '#include "../numeric/fixed_decimal.h"\n\n',
        '#include "../numeric/fixed_decimal.h"\n\n#include <limits>\n',
        1,
    )
    old = '''    if (solver.digest.empty() ||
        GlobalAllocator::SolverDigest(solver) != solver.digest ||
        solver.primalBound != solver.objective ||
        solver.upperBound < solver.objective ||
        solver.absoluteGap != solver.upperBound - solver.objective)
        return false;
'''
    new = '''    DecisionMicrounits expectedGap = 0;
    if (solver.digest.empty() ||
        GlobalAllocator::SolverDigest(solver) != solver.digest ||
        solver.primalBound != solver.objective ||
        solver.upperBound < solver.objective ||
        !CheckedSubtract(solver.upperBound, solver.objective, expectedGap) ||
        solver.absoluteGap != expectedGap)
        return false;
'''
    text = replace_once(text, old, new, "solver gap validation")
    path.write_text(text, encoding="utf-8")


def patch_cmake() -> None:
    path = ROOT / "HeptaTrade/CMakeLists.txt"
    text = path.read_text(encoding="utf-8")
    marker = '''add_library(hepta_risk_core STATIC
    risk/deterministic_risk_policy.cpp)
hepta_runtime_target(hepta_risk_core)
target_link_libraries(hepta_risk_core PUBLIC hepta_observability_core)
'''
    additions = marker + '''

add_library(hepta_strategy_runtime STATIC
    strategy_runtime/strategy_proposal.cpp)
hepta_runtime_target(hepta_strategy_runtime)
target_link_libraries(hepta_strategy_runtime PUBLIC
    hepta_numeric_core
    OpenSSL::Crypto)

add_library(hepta_proposal_aggregator STATIC
    proposal/proposal_set.cpp)
hepta_runtime_target(hepta_proposal_aggregator)
target_link_libraries(hepta_proposal_aggregator PUBLIC
    hepta_strategy_runtime
    OpenSSL::Crypto)

add_library(hepta_global_allocator STATIC
    allocation/global_allocator.cpp)
hepta_runtime_target(hepta_global_allocator)
target_link_libraries(hepta_global_allocator PUBLIC
    hepta_proposal_aggregator
    hepta_numeric_core
    OpenSSL::Crypto)

add_library(hepta_allocation_revalidator STATIC
    execution/allocation_plan_revalidator.cpp)
hepta_runtime_target(hepta_allocation_revalidator)
target_link_libraries(hepta_allocation_revalidator PUBLIC
    hepta_global_allocator
    hepta_portfolio_core
    hepta_numeric_core)
'''
    path.write_text(
        replace_once(text, marker, additions, "M4 targets"),
        encoding="utf-8",
    )

    path = ROOT / "tests/CMakeLists.txt"
    text = path.read_text(encoding="utf-8")
    marker = '''add_executable(hepta_feature_generation_tests
    feature_generation_tests.cpp)
target_link_libraries(hepta_feature_generation_tests
    hepta_feature_runtime)
hepta_register_core_test(hepta_feature_generation_tests)
'''
    additions = marker + '''

add_executable(hepta_strategy_proposal_tests
    strategy_proposal_tests.cpp)
target_link_libraries(hepta_strategy_proposal_tests
    hepta_proposal_aggregator)
hepta_register_core_test(hepta_strategy_proposal_tests)

add_executable(hepta_global_allocator_tests
    global_allocator_tests.cpp)
target_link_libraries(hepta_global_allocator_tests
    hepta_global_allocator)
hepta_register_core_test(hepta_global_allocator_tests)

add_executable(hepta_allocation_plan_revalidator_tests
    allocation_plan_revalidator_tests.cpp)
target_link_libraries(hepta_allocation_plan_revalidator_tests
    hepta_allocation_revalidator)
hepta_register_core_test(hepta_allocation_plan_revalidator_tests)
'''
    path.write_text(
        replace_once(text, marker, additions, "M4 tests"),
        encoding="utf-8",
    )


def patch_ownership() -> None:
    path = ROOT / "docs/modules/source-ownership-registry-v1.json"
    value = json.loads(path.read_text(encoding="utf-8"))
    additions = [
        {
            "id": "strategy-runtime",
            "selector": {"kind": "directory", "path": "HeptaTrade/strategy_runtime/"},
            "physical_owner": "hepta.strategy.runtime",
            "priority": 200,
        },
        {
            "id": "proposal-runtime",
            "selector": {"kind": "directory", "path": "HeptaTrade/proposal/"},
            "physical_owner": "hepta.global.decision",
            "priority": 200,
        },
        {
            "id": "allocation-runtime",
            "selector": {"kind": "directory", "path": "HeptaTrade/allocation/"},
            "physical_owner": "hepta.global.decision",
            "priority": 200,
        },
        {
            "id": "allocation-revalidator",
            "selector": {"kind": "prefix", "path": "HeptaTrade/execution/allocation_plan_revalidator"},
            "physical_owner": "hepta.execution.runtime",
            "priority": 350,
        },
    ]
    ids = {item["id"] for item in value["physical_ownership_rules"]}
    for item in additions:
        if item["id"] not in ids:
            value["physical_ownership_rules"].append(item)
    write_json(path, value, compact=True)


def patch_manifests() -> None:
    path = ROOT / "docs/modules/manifests/hepta-strategy-runtime.json"
    value = json.loads(path.read_text(encoding="utf-8"))
    value["lifecycle"] = "current"
    write_json(path, value)

    path = ROOT / "docs/modules/manifests/hepta-global-decision.json"
    value = json.loads(path.read_text(encoding="utf-8"))
    value["lifecycle"] = "current"
    dependencies = value.setdefault("allowed_dependencies", [])
    for dependency in ("hepta.strategy.runtime", "hepta.numeric.core"):
        if dependency not in dependencies:
            dependencies.append(dependency)
    dependencies.sort()
    write_json(path, value)

    path = ROOT / "docs/modules/manifests/hepta-execution-runtime.json"
    value = json.loads(path.read_text(encoding="utf-8"))
    targets = value.setdefault("build_targets", [])
    if "hepta_allocation_revalidator" not in targets:
        targets.append("hepta_allocation_revalidator")
    dependencies = value.setdefault("allowed_dependencies", [])
    for dependency in (
        "hepta.global.decision",
        "hepta.portfolio.compiler",
        "hepta.numeric.core",
    ):
        if dependency not in dependencies:
            dependencies.append(dependency)
    dependencies.sort()
    verification = value.setdefault("verification", [])
    if "shadow-parity" not in verification:
        verification.append("shadow-parity")
    write_json(path, value)


def patch_test_matrix_and_capability() -> None:
    path = ROOT / "docs/verification/test-matrix-v2.json"
    value = json.loads(path.read_text(encoding="utf-8"))
    evidence = {
        "proposal-contracts": "StrategyProposal seal, canonical digest and malformed input CTest",
        "proposal-completeness": "expected-module proposal-set completeness CTest",
        "optimizer-determinism": "exact enumeration, deterministic tie-break and digest CTest",
        "constraint-properties": "gross/instrument feasibility and bounded upper-gap CTest",
        "shadow-parity": "AllocationPlan to PortfolioCompiler Execution revalidation CTest",
    }
    found = set()
    for check in value["checks"]:
        if check["id"] in evidence:
            check["state"] = "implemented"
            check["evidence"] = evidence[check["id"]]
            found.add(check["id"])
    if found != set(evidence):
        raise SystemExit(f"missing checks: {sorted(set(evidence)-found)}")
    write_json(path, value, compact=True)

    path = ROOT / "docs/product/capability-registry-v2.json"
    value = json.loads(path.read_text(encoding="utf-8"))
    matched = False
    for capability in value["capabilities"]:
        if capability["id"] != "hepta.global.multi-agent-allocation":
            continue
        matched = True
        capability["declared_state"] = "experimental"
        capability["design"] = "approved"
        capability["implementation"] = "implemented-core"
        capability["build"] = "default"
        capability["integration"] = {
            "simulator": "active-shadow",
            "paper": "absent",
            "live": "forbidden",
        }
        capability["release"] = "core"
    if not matched:
        raise SystemExit("global allocation capability missing")
    write_json(path, value)


def patch_schemas() -> None:
    maximum = 9_000_000_000_000_000
    digest = {"type": "string", "pattern": "^sha256:[0-9a-f]{64}$"}
    canonical_id = {
        "type": "string",
        "minLength": 1,
        "maxLength": 128,
        "pattern": "^[A-Za-z0-9._:-]+$",
    }
    target = {
        "type": "object",
        "additionalProperties": False,
        "required": ["instrument", "target_position_raw"],
        "properties": {
            "instrument": canonical_id,
            "target_position_raw": {
                "type": "integer", "minimum": -maximum, "maximum": maximum
            },
        },
    }
    candidate = {
        "type": "object",
        "additionalProperties": False,
        "required": ["candidate_id", "utility_raw", "targets"],
        "properties": {
            "candidate_id": canonical_id,
            "utility_raw": {
                "type": "integer", "minimum": -maximum, "maximum": maximum
            },
            "targets": {
                "type": "array", "minItems": 1, "maxItems": 256,
                "items": target,
            },
        },
    }
    strategy = {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "title": "StrategyProposalV1",
        "type": "object",
        "additionalProperties": False,
        "required": [
            "schema", "proposal_id", "module_id", "module_version", "sequence",
            "capital_pool", "account_book", "snapshot_digest", "valid_from_ms",
            "expires_at_ms", "horizon_ms", "candidates",
            "numeric_policy_version", "proposal_digest",
        ],
        "properties": {
            "schema": {"const": "hepta.strategy-proposal.v1"},
            "proposal_id": canonical_id,
            "module_id": {
                "type": "string", "minLength": 7, "maxLength": 128,
                "pattern": "^hepta\\.[A-Za-z0-9._:-]+$",
            },
            "module_version": canonical_id,
            "sequence": {"type": "integer", "minimum": 1},
            "capital_pool": canonical_id,
            "account_book": canonical_id,
            "snapshot_digest": digest,
            "valid_from_ms": {"type": "integer", "minimum": 1},
            "expires_at_ms": {"type": "integer", "minimum": 1},
            "horizon_ms": {"type": "integer", "minimum": 1},
            "candidates": {
                "type": "array", "minItems": 1, "maxItems": 256,
                "items": candidate,
            },
            "numeric_policy_version": {"const": "hepta.numeric.fixed-v1"},
            "proposal_digest": digest,
        },
    }
    write_json(ROOT / "schemas/strategy-proposal-v1.json", strategy)

    solver = {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "title": "SolverResultV1",
        "type": "object",
        "additionalProperties": False,
        "required": [
            "schema", "status", "objective_raw", "primal_bound_raw",
            "upper_bound_raw", "absolute_gap_raw", "combinations_explored",
            "exact", "digest",
        ],
        "properties": {
            "schema": {"const": "hepta.solver-result.v1"},
            "status": {"enum": ["optimal", "feasible_not_proven"]},
            "objective_raw": {"type": "integer"},
            "primal_bound_raw": {"type": "integer"},
            "upper_bound_raw": {"type": "integer"},
            "absolute_gap_raw": {"type": "integer", "minimum": 0},
            "combinations_explored": {"type": "integer", "minimum": 0},
            "exact": {"type": "boolean"},
            "digest": digest,
        },
        "allOf": [
            {
                "if": {"properties": {"exact": {"const": True}}},
                "then": {
                    "properties": {
                        "status": {"const": "optimal"},
                        "absolute_gap_raw": {"const": 0},
                    }
                },
            },
            {
                "if": {"properties": {"exact": {"const": False}}},
                "then": {
                    "properties": {"status": {"const": "feasible_not_proven"}}
                },
            },
        ],
    }
    write_json(ROOT / "schemas/solver-result-v1.json", solver)

    allocation_target = {
        "type": "object",
        "additionalProperties": False,
        "required": ["instrument", "target_position_raw"],
        "properties": {
            "instrument": canonical_id,
            "target_position_raw": {
                "type": "integer", "minimum": -maximum, "maximum": maximum
            },
        },
    }
    allocation = {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "title": "AllocationPlanV1",
        "type": "object",
        "additionalProperties": False,
        "required": [
            "schema", "plan_id", "allocator_epoch", "capital_pool",
            "account_book", "proposal_set_digest", "snapshot_digest", "solver",
            "targets", "accepted_candidates", "rejected_proposals", "created_at_ms",
            "valid_until_ms", "numeric_policy_version", "plan_digest",
        ],
        "properties": {
            "schema": {"const": "hepta.allocation-plan.v1"},
            "plan_id": canonical_id,
            "allocator_epoch": {"type": "integer", "minimum": 1},
            "capital_pool": canonical_id,
            "account_book": canonical_id,
            "proposal_set_digest": digest,
            "snapshot_digest": digest,
            "solver": solver,
            "targets": {
                "type": "array", "maxItems": 4096, "items": allocation_target
            },
            "accepted_candidates": {
                "type": "array", "maxItems": 256, "items": canonical_id
            },
            "rejected_proposals": {
                "type": "array", "maxItems": 256, "items": canonical_id
            },
            "created_at_ms": {"type": "integer", "minimum": 1},
            "valid_until_ms": {"type": "integer", "minimum": 1},
            "numeric_policy_version": {"const": "hepta.numeric.fixed-v1"},
            "plan_digest": digest,
        },
    }
    write_json(ROOT / "schemas/allocation-plan-v1.json", allocation)


def patch_docs() -> None:
    (ROOT / "docs/contracts/STRATEGY-PROPOSAL-CONTRACT.md").write_text(
        "# StrategyProposal V1\n\n"
        "Status: current normative\n"
        "Applies to: strategy modules and Global Decision intake\n"
        "Verification: schema, canonicalization, completeness and digest CTests\n"
        "Authority: strategy-output contract\n\n"
        "策略只能输出有界 `StrategyProposal`，不能持有 broker credential 或 mutation authority。"
        "Proposal 绑定 module/version、单调 sequence、capital pool/account book、"
        "authoritative snapshot digest、validity window 与固定点 numeric policy。\n\n"
        "每个 proposal 含 1–256 个互斥 candidate；candidate 具有稳定 ID、fixed-point utility 与"
        "按 instrument 排序的 target vector。重复 module/candidate/instrument、过期 proposal、"
        "snapshot 不一致、digest 不匹配和越界数值全部 fail closed。Seal 操作规范化顺序并生成"
        "SHA-256；相同语义输入产生相同 digest。机器 schema 为 `schemas/strategy-proposal-v1.json`。\n",
        encoding="utf-8",
    )
    (ROOT / "docs/contracts/GLOBAL-OPTIMIZATION-CONTRACT.md").write_text(
        "# Global Optimization Contract V1\n\n"
        "Status: current normative\n"
        "Applies to: proposal aggregation and Global Decision solver\n"
        "Verification: exact enumeration, bounded fallback, constraint and digest CTests\n"
        "Authority: global-allocation semantics\n\n"
        "输入必须是 expected module set 的完整、同 book、同 snapshot `ProposalSet`。"
        "有限组合数不超过 policy cap 时，solver 枚举每个 module 的 reject/candidate 选择并给出"
        "可复验 `optimal`：upper bound = objective、gap = 0。超出 cap 时只返回"
        "`feasible_not_proven`，同时记录 primal lower bound、独立松弛 upper bound 和 absolute gap；"
        "任何 heuristic 结果都不得标记 optimal。\n\n"
        "约束使用 fixed-point 整数检查 instrument absolute limit、portfolio gross 和 active instrument count；"
        "tie-break 按规范化 module/candidate key，零收益默认 reject。SolverResult 与 plan 都绑定 SHA-256。"
        "机器 schema 为 `schemas/solver-result-v1.json`。\n",
        encoding="utf-8",
    )
    (ROOT / "docs/contracts/ALLOCATION-PLAN-CONTRACT.md").write_text(
        "# AllocationPlan V1\n\n"
        "Status: current normative\n"
        "Applies to: Global Decision output and Execution shadow intake\n"
        "Verification: plan integrity, expiry, snapshot binding and Execution revalidation CTests\n"
        "Authority: global-decision output contract\n\n"
        "`AllocationPlan` 是 immutable、bounded、可重放的目标计划，不是 broker command，也不授予 mutation。"
        "Plan 绑定 allocator epoch、proposal-set digest、snapshot digest、SolverResult、fixed-point targets、"
        "created/valid-until 和 plan digest。\n\n"
        "Execution 依次验证 plan/solver digest、exact/heuristic 状态与 bound/gap 一致性、时间窗口、"
        "authoritative snapshot digest、target 排序/范围，再把 targets 交给现有 `PortfolioCompiler` 重算"
        "strategy/global capital budget 和 authoritative generation delta。失败保持 typed reject；当前集成为"
        "shadow revalidation，尚不直接发送 broker mutation。机器 schema 为 `schemas/allocation-plan-v1.json`。\n",
        encoding="utf-8",
    )


def main() -> None:
    patch_safety_edges()
    patch_cmake()
    patch_ownership()
    patch_manifests()
    patch_test_matrix_and_capability()
    patch_schemas()
    patch_docs()
    subprocess.run(
        ["python3", "scripts/generate_documentation_views.py", "--write"],
        cwd=ROOT,
        check=True,
    )
    WORKFLOW.unlink()
    SELF.unlink()


if __name__ == "__main__":
    main()
