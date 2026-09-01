#!/usr/bin/env python3
"""Run internal closure while extending the already-governed allocator test."""

from __future__ import annotations

import argparse
from pathlib import Path
import re
import sys

SCRIPTS = Path(__file__).resolve().parent
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import internal_closure_finalizer as base


def ensure_include(text: str, include: str) -> str:
    directive = f"#include <{include}>"
    if directive in text:
        return text
    last = text.rfind("#include <")
    if last < 0:
        raise ValueError("global allocator test has no system include anchor")
    end = text.find("\n", last)
    if end < 0:
        raise ValueError("global allocator test include block is malformed")
    return text[: end + 1] + directive + "\n" + text[end + 1 :]


def remove_standalone_target(root: Path) -> None:
    source = root / "tests/global_allocator_performance_tests.cpp"
    if source.exists():
        source.unlink()
    cmake_path = root / "tests/CMakeLists.txt"
    cmake = cmake_path.read_text(encoding="utf-8")
    cmake = re.sub(
        r"\n*add_executable\(hepta_global_allocator_performance_tests\s+"
        r"global_allocator_performance_tests\.cpp\)\s*"
        r"target_link_libraries\(hepta_global_allocator_performance_tests\s+"
        r"hepta_global_allocator\)\s*"
        r"hepta_register_core_test\(hepta_global_allocator_performance_tests\)\s*"
        r"set_property\(TEST hepta_global_allocator_performance_tests\s+"
        r"APPEND PROPERTY LABELS performance\)\s*",
        "\n",
        cmake,
        flags=re.MULTILINE,
    )
    cmake_path.write_text(cmake.rstrip() + "\n", encoding="utf-8")


def write_performance_test(root: Path) -> None:
    remove_standalone_target(root)
    path = root / "tests/global_allocator_tests.cpp"
    text = path.read_text(encoding="utf-8")
    marker = "void TestBoundedPerformanceEnvelope()"
    if marker in text:
        return
    for include in ("chrono", "cstdint", "string", "vector"):
        text = ensure_include(text, include)

    function = r'''

void TestBoundedPerformanceEnvelope()
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
        StrategyProposal proposal;
        proposal.proposalId = "performance-proposal-" + std::to_string(index);
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
        candidate.candidateId = "performance-candidate-" +
            std::to_string(index);
        candidate.utility = static_cast<DecisionMicrounits>(100 + index);
        candidate.targets.push_back({
            "PERF." + std::to_string(index), 1000000
        });
        proposal.candidates.push_back(candidate);
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
}
'''
    namespace_boundary = "\n}\n\nint main()"
    position = text.rfind(namespace_boundary)
    if position < 0:
        raise ValueError("global allocator test namespace boundary is missing")
    text = text[:position] + function + text[position:]

    main_return = "\n    return 0;\n}\n"
    position = text.rfind(main_return)
    if position < 0:
        raise ValueError("global allocator test main return is missing")
    text = (
        text[:position]
        + "\n    TestBoundedPerformanceEnvelope();"
        + text[position:]
    )
    path.write_text(text, encoding="utf-8")


def finalize(root: Path) -> None:
    base.write_performance_test = write_performance_test
    base.finalize(root)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, required=True)
    args = parser.parse_args()
    finalize(args.root)
