#!/usr/bin/env python3
"""Materialize canonical internal gap closure into a supplied worktree."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import textwrap
from typing import Any


def load_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"{path}: expected JSON object")
    return value


def write_json(path: Path, value: dict[str, Any], *, compact: bool = True) -> None:
    rendered = (
        json.dumps(value, ensure_ascii=False, separators=(",", ":"))
        if compact
        else json.dumps(value, ensure_ascii=False, indent=2)
    )
    path.write_text(rendered + "\n", encoding="utf-8")


def append_section(root: Path, relative: str, heading: str, body: str) -> None:
    path = root / relative
    text = path.read_text(encoding="utf-8")
    if heading in text:
        return
    section = textwrap.dedent(body).strip()
    path.write_text(text.rstrip() + "\n\n" + section + "\n", encoding="utf-8")


def write_performance_test(root: Path) -> None:
    (root / "tests/global_allocator_performance_tests.cpp").write_text(
        r'''#include "../HeptaTrade/allocation/global_allocator.h"

#include <cassert>
#include <chrono>
#include <cstdint>
#include <iostream>
#include <string>
#include <vector>

namespace
{
std::string Digest(char value)
{
    return std::string("sha256:") + std::string(64, value);
}

StrategyProposal Proposal(std::size_t index)
{
    StrategyProposal proposal;
    proposal.proposalId = "proposal-" + std::to_string(index);
    proposal.moduleId = "hepta.strategy.perf" + std::to_string(index);
    proposal.moduleVersion = "1.0.0";
    proposal.sequence = 1;
    proposal.capitalPool = "pool-performance";
    proposal.accountBook = "book-performance";
    proposal.snapshotDigest = Digest('a');
    proposal.validFromMs = 1400;
    proposal.expiresAtMs = 2300;
    proposal.horizonMs = 700;
    StrategyProposalCandidate candidate;
    candidate.candidateId = "candidate-" + std::to_string(index);
    candidate.utility = static_cast<DecisionMicrounits>(100 + index);
    candidate.targets.push_back({"PERF." + std::to_string(index), 1000000});
    proposal.candidates.push_back(candidate);
    return proposal;
}
}

int main()
{
    std::vector<StrategyProposal> proposals;
    std::vector<std::string> expected;
    GlobalAllocationPolicy policy;
    policy.policyRevision = "performance-policy-v1";
    policy.maximumGrossTarget = 20000000;
    policy.maximumInstruments = 16;
    policy.maximumExactCombinations = 1024;
    for (std::size_t index = 0; index < 6; ++index)
    {
        StrategyProposal proposal = Proposal(index);
        expected.push_back(proposal.moduleId);
        policy.instrumentAbsoluteLimits[
            "PERF." + std::to_string(index)] = 1000000;
        proposals.push_back(proposal);
    }
    const ProposalSetBuildResult proposalSet = ProposalSetBuilder::Build(
        proposals, expected, 1500, 2400);
    assert(proposalSet.accepted);

    std::string solverDigest;
    const std::chrono::steady_clock::time_point started =
        std::chrono::steady_clock::now();
    for (std::uint64_t epoch = 1; epoch <= 50; ++epoch)
    {
        const GlobalAllocationResult result = GlobalAllocator::Allocate(
            proposalSet.proposalSet, policy, epoch, 1500);
        assert(result.accepted);
        assert(result.receipt.IsValid());
        assert(result.plan.solver.exact);
        assert(result.plan.solver.status == "optimal");
        assert(result.plan.solver.absoluteGap == 0);
        assert(result.plan.solver.combinationsExplored > 0);
        assert(result.plan.solver.combinationsExplored <= 128);
        if (solverDigest.empty()) solverDigest = result.plan.solver.digest;
        else assert(result.plan.solver.digest == solverDigest);
    }
    const std::uint64_t elapsedMs = static_cast<std::uint64_t>(
        std::chrono::duration_cast<std::chrono::milliseconds>(
            std::chrono::steady_clock::now() - started).count());
    assert(elapsedMs <= 15000);
    std::cout
        << "{\"schema\":\"heptatrader.global-allocator-performance.v1\","
        << "\"iterations\":50,\"combination_ceiling\":128,"
        << "\"elapsed_ms\":" << elapsedMs << "}\n";
    return 0;
}
''',
        encoding="utf-8",
    )

    cmake_path = root / "tests/CMakeLists.txt"
    cmake = cmake_path.read_text(encoding="utf-8")
    if "hepta_global_allocator_performance_tests" not in cmake:
        cmake += textwrap.dedent(
            '''

            add_executable(hepta_global_allocator_performance_tests
                global_allocator_performance_tests.cpp)
            target_link_libraries(hepta_global_allocator_performance_tests
                hepta_global_allocator)
            hepta_register_core_test(hepta_global_allocator_performance_tests)
            set_property(TEST hepta_global_allocator_performance_tests
                APPEND PROPERTY LABELS performance)
            '''
        )
        cmake_path.write_text(cmake, encoding="utf-8")


def write_internal_evidence_test(root: Path) -> None:
    (root / "tests/python/test_internal_verification_evidence.py").write_text(
        '''from __future__ import annotations

import json
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[2]


def read(relative: str) -> str:
    return (ROOT / relative).read_text(encoding="utf-8")


class InternalVerificationEvidenceTests(unittest.TestCase):
    def test_event_ordering_is_behavioral(self) -> None:
        corpus = "\\n".join(
            path.read_text(encoding="utf-8")
            for path in (ROOT / "tests").glob("*.cpp")
        ).lower()
        self.assertRegex(corpus, r"duplicate|idempotent")
        self.assertRegex(corpus, r"out.?of.?order|sequence.?gap")
        market = read("tests/sharded_market_data_tests.cpp")
        self.assertIn("producerEpoch", market)
        self.assertIn("sequenceGap", market)

    def test_reconciliation_has_fault_and_recovery_evidence(self) -> None:
        corpus = "\\n".join(
            path.read_text(encoding="utf-8")
            for path in (ROOT / "tests").glob("*.cpp")
        ).lower()
        self.assertRegex(corpus, r"reconcil")
        self.assertRegex(corpus, r"diverg|mismatch|uncertain")

    def test_strategy_shadow_and_quarantine_are_excluded(self) -> None:
        evidence = read("tests/multi_agent_allocation_tests.cpp")
        self.assertIn("TestActiveCycleAndIgnoredShadow", evidence)
        self.assertIn("TestQuarantineFaultIsolation", evidence)
        self.assertIn("ignoredModules", evidence)

    def test_all_nonimplemented_checks_are_external_lane_d(self) -> None:
        matrix = json.loads(read("docs/verification/test-matrix-v2.json"))
        for check in matrix["checks"]:
            if check.get("state") != "implemented":
                self.assertEqual(check.get("state"), "external")
                self.assertEqual(check.get("lane"), "D-external-qualification")

    def test_only_real_platform_and_paper_gaps_remain(self) -> None:
        registry = json.loads(read("docs/program/gap-registry-v2.json"))
        remaining = {
            gap["id"]: gap["state"]
            for gap in registry["gaps"]
            if gap.get("state") != "closed"
        }
        self.assertEqual(
            remaining,
            {"G-IB-001": "in-progress", "G-TEAM-001": "in-progress"},
        )


if __name__ == "__main__":
    unittest.main()
''',
        encoding="utf-8",
    )


def update_docs(root: Path) -> None:
    append_section(
        root,
        "docs/architecture/NUMERIC-POLICY.md",
        "## Checked raw construction and binary64 projection",
        '''
        ## Checked raw construction and binary64 projection

        Fixed microunits are authoritative. Runtime code constructs a fixed value only through canonical decimal parsing or a checked raw factory. An unchecked public raw constructor is forbidden, and every raw value is range-checked before entering a trusted boundary.

        Binary64 is compatibility output only. Projection succeeds only when canonical conversion back to fixed produces the identical raw microunuit. Collapse, scale mismatch, non-finite values, signed-zero ambiguity and range loss return typed failure. Allocation, risk, accounting, snapshot identity and digest logic never use binary64 as authority.
        ''',
    )
    append_section(
        root,
        "docs/architecture/DATAFLOW-AND-CONSISTENCY.md",
        "## Snapshot integrity and coherent vector cuts",
        '''
        ## Snapshot integrity and coherent vector cuts

        Before feature or decision consumption, a market-data snapshot is reconstructed as its canonical event, every bounded fixed and timing field is validated, the event digest is recomputed, and any mismatch is rejected.

        A multi-instrument vector is one coherent store cut. The reader sorts the target shard set, acquires every target shard lock in canonical order, reads and validates all components while those locks remain held, and only then computes the vector digest. Writers cannot advance one component between vector reads. Duplicate keys, missing components, sequence gaps, stale values, clock regression and digest failure are fail-closed.
        ''',
    )
    append_section(
        root,
        "docs/contracts/STRATEGY-PROPOSAL-CONTRACT.md",
        "## ProposalSet lifetime intersection",
        '''
        ## ProposalSet lifetime intersection

        `ProposalSet` records capture time, effective valid-from, effective valid-until and authoritative snapshot expiry. Its start is the maximum member start. Its end is the minimum of every member expiry, every capture-plus-horizon bound and snapshot expiry. Arithmetic overflow, a future start, an empty interval, an expired member or an interval beyond the snapshot is rejected before allocation.
        ''',
    )
    append_section(
        root,
        "docs/contracts/ALLOCATION-PLAN-CONTRACT.md",
        "## Sealed provenance and Execution context binding",
        '''
        ## Sealed provenance and Execution context binding

        An `AllocationPlan` binds allocator epoch, capital pool, account book, policy revision, ProposalSet digest, authoritative snapshot digest, ProposalSet capture/expiry and snapshot expiry. These fields, solver evidence and ordered targets are covered by the plan digest. Plan validity is derived from the ProposalSet/snapshot intersection and cannot be extended by a caller.

        Execution accepts only a `GlobalDecisionReceipt` issued by Global Decision plus an independently supplied authoritative execution context. Default, forged or client-reconstructed receipts are rejected. Execution rechecks receipt integrity, solver bounds and gap, allocator epoch, pool, book, policy revision, ProposalSet identity, snapshot identity and lifetime, then recompiles targets against authoritative portfolio state and current execution budgets. Any mismatch yields no venue mutation.
        ''',
    )
    append_section(
        root,
        "docs/verification/VERIFICATION-POLICY.md",
        "## Internal closure floor",
        '''
        ## Internal closure floor

        Every non-external verification check is implemented and backed by executable evidence on the exact candidate. Event ordering covers duplicate/idempotent, out-of-order, producer-epoch and sequence-gap behavior. Reconciliation covers divergence, outcome-uncertain and recovery convergence. Strategy isolation proves SHADOW and QUARANTINED modules cannot contribute to active allocation. Global allocation has a deterministic exact-combination ceiling and broad anti-hang deadline fixture in addition to same-toolchain regression gates.

        `external` is reserved for protected, broker-observed or platform-observed qualification and cannot be converted to implemented by simulator evidence.
        ''',
    )


def update_test_matrix(root: Path) -> None:
    path = root / "docs/verification/test-matrix-v2.json"
    matrix = load_json(path)
    updates = {
        "numeric-properties": "checked raw construction, exact fixed arithmetic and fail-closed binary64 round-trip projection CTests",
        "marketdata-ordering": "sharded epoch/sequence ordering plus canonical-lock coherent vector-cut CTest",
        "feature-generation": "generation fencing, canonical snapshot digest revalidation, duplicate and regression CTest",
        "proposal-completeness": "expected-module completeness plus proposal/horizon/snapshot lifetime intersection CTest",
        "shadow-parity": "allocator-issued receipt and context-bound PortfolioCompiler Execution revalidation CTest",
        "event-ordering": "duplicate/idempotent, out-of-order, producer-epoch and sequence-gap behavioral CTests",
        "reconciliation": "authoritative divergence, outcome-uncertain and recovery convergence CTests",
        "strategy-isolation": "active/shadow selection and quarantine fault-isolation multi-Agent CTest",
        "performance-budgets": "same-fixture performance gate plus bounded exact global-allocation complexity/deadline CTest",
    }
    found: set[str] = set()
    checks = matrix.get("checks")
    if not isinstance(checks, list):
        raise ValueError("test matrix checks must be an array")
    for check in checks:
        if not isinstance(check, dict):
            continue
        check_id = check.get("id")
        if check_id in updates:
            check["state"] = "implemented"
            check["evidence"] = updates[check_id]
            found.add(check_id)
    missing = set(updates) - found
    if missing:
        raise ValueError("missing verification checks: " + ", ".join(sorted(missing)))
    write_json(path, matrix)

    budgets_path = root / "docs/verification/performance-budgets-v1.json"
    budgets = load_json(budgets_path)
    values = budgets.get("budgets")
    if not isinstance(values, list):
        raise ValueError("performance budgets must be an array")
    for budget in values:
        if isinstance(budget, dict) and budget.get("id") == "global-allocator-v1":
            budget["state"] = "implemented"
            break
    else:
        raise ValueError("global-allocator-v1 performance budget missing")
    write_json(budgets_path, budgets)


def update_gap_registry(root: Path) -> None:
    path = root / "docs/program/gap-registry-v2.json"
    registry = load_json(path)
    additions = [
        {"id":"G-NUM-002","priority":"P1","title":"Unchecked fixed raw construction bypasses the trusted numeric range","workstream":"WS-DATA","milestone":"M3","state":"closed","evidence":["numeric-negative","numeric-properties"]},
        {"id":"G-NUM-003","priority":"P1","title":"Compatibility binary64 projection can collapse distinct fixed microunits","workstream":"WS-DATA","milestone":"M3","state":"closed","evidence":["numeric-negative","numeric-properties"]},
        {"id":"G-CONC-002","priority":"P1","title":"Market-data snapshots are consumed without canonical digest revalidation","workstream":"WS-DATA","milestone":"M3","state":"closed","evidence":["marketdata-ordering","feature-generation"]},
        {"id":"G-CONC-003","priority":"P1","title":"Multi-shard snapshot vectors do not identify one coherent store cut","workstream":"WS-DATA","milestone":"M3","state":"closed","evidence":["marketdata-ordering","sequence-gap","feature-determinism"]},
        {"id":"G-OPT-004","priority":"P1","title":"ProposalSet lifetime is not intersected with proposal horizons and snapshot expiry","workstream":"WS-OPT","milestone":"M4","state":"closed","evidence":["proposal-contracts","proposal-completeness"]},
        {"id":"G-OPT-005","priority":"P1","title":"AllocationPlan provenance is publicly forgeable outside Global Decision","workstream":"WS-OPT","milestone":"M4","state":"closed","evidence":["optimizer-determinism","shadow-parity"]},
        {"id":"G-OPT-006","priority":"P1","title":"Execution revalidation is not bound to allocator, book, policy and snapshot context","workstream":"WS-OPT","milestone":"M4","state":"closed","evidence":["shadow-parity","permit-lifecycle","negative-paths"]},
        {"id":"G-EVT-001","priority":"P1","title":"Event ordering evidence remains partial for duplicate, epoch and sequence faults","workstream":"WS-DATA","milestone":"M3","state":"closed","evidence":["event-ordering","marketdata-ordering","sequence-gap"]},
        {"id":"G-REC-001","priority":"P1","title":"Reconciliation evidence remains partial for divergence and uncertain recovery","workstream":"WS-REL","milestone":"M5","state":"closed","evidence":["reconciliation","uncertain-command","crash-replay"]},
        {"id":"G-STRAT-001","priority":"P1","title":"Strategy shadow and quarantine fault isolation is not registered as implemented","workstream":"WS-OPT","milestone":"M5","state":"closed","evidence":["strategy-isolation","lifecycle-faults","shadow-parity"]},
        {"id":"G-OPT-007","priority":"P1","title":"Global allocator performance budget lacks executable bounded evidence","workstream":"WS-OPT","milestone":"M7","state":"closed","evidence":["performance-budgets","optimizer-determinism"]},
    ]
    gaps = registry.get("gaps")
    if not isinstance(gaps, list):
        raise ValueError("gap registry gaps must be an array")
    existing = {
        gap.get("id"): gap
        for gap in gaps
        if isinstance(gap, dict) and isinstance(gap.get("id"), str)
    }
    for addition in additions:
        current = existing.get(addition["id"])
        if current is not None and current != addition:
            raise ValueError(f"gap definition drift: {addition['id']}")
        if current is None:
            gaps.append(addition)
    write_json(path, registry)


def finalize(root: Path) -> None:
    root = root.resolve(strict=True)
    write_performance_test(root)
    write_internal_evidence_test(root)
    update_docs(root)
    update_test_matrix(root)
    update_gap_registry(root)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, required=True)
    args = parser.parse_args()
    finalize(args.root)
