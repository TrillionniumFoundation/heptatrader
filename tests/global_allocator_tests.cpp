#include "../HeptaTrade/allocation/global_allocator.h"

#include <cassert>
#include <string>
#include <vector>
#include <chrono>
#include <cstdint>

namespace
{
std::string Digest(char value)
{
    return std::string("sha256:") + std::string(64, value);
}

StrategyProposal Proposal(const std::string& module,
                          const std::string& proposalId,
                          DecisionMicrounits firstUtility,
                          DecisionMicrounits firstTarget,
                          DecisionMicrounits secondUtility,
                          DecisionMicrounits secondTarget)
{
    StrategyProposal proposal;
    proposal.proposalId = proposalId;
    proposal.moduleId = module;
    proposal.moduleVersion = "1.0.0";
    proposal.sequence = 1;
    proposal.capitalPool = "pool-a";
    proposal.accountBook = "book-a";
    proposal.snapshotDigest = Digest('a');
    proposal.validFromMs = 1000;
    proposal.expiresAtMs = 2000;
    proposal.horizonMs = 500;
    StrategyProposalCandidate first;
    first.candidateId = "candidate-a";
    first.utility = firstUtility;
    first.targets.push_back({"EUR.USD", firstTarget});
    StrategyProposalCandidate second;
    second.candidateId = "candidate-b";
    second.utility = secondUtility;
    second.targets.push_back({"EUR.USD", secondTarget});
    proposal.candidates.push_back(first);
    proposal.candidates.push_back(second);
    return proposal;
}

ProposalSet Set()
{
    std::vector<StrategyProposal> proposals;
    proposals.push_back(Proposal(
        "hepta.strategy.alpha", "proposal-alpha", 10, 4000000, 20, 8000000));
    proposals.push_back(Proposal(
        "hepta.strategy.beta", "proposal-beta", 15, 4000000, -5, -1000000));
    std::vector<std::string> expected;
    expected.push_back("hepta.strategy.alpha");
    expected.push_back("hepta.strategy.beta");
    ProposalSetBuildResult result = ProposalSetBuilder::Build(
        proposals, expected, 1500, 1800);
    assert(result.accepted);
    return result.proposalSet;
}

GlobalAllocationPolicy Policy(std::uint64_t combinations)
{
    GlobalAllocationPolicy policy;
    policy.policyRevision = "policy-v1";
    policy.maximumGrossTarget = 10000000;
    policy.maximumInstruments = 4;
    policy.maximumExactCombinations = combinations;
    policy.instrumentAbsoluteLimits["EUR.USD"] = 10000000;
    return policy;
}

void TestExactOptimalEvidence()
{
    GlobalAllocationResult result = GlobalAllocator::Allocate(
        Set(), Policy(100), 1, 1500);
    assert(result.accepted);
    assert(result.receipt.IsValid());
    assert(result.receipt.Plan().planDigest == result.plan.planDigest);
    assert(result.plan.validUntilMs == 1800);
    assert(result.reasonCode == "ALLOCATION_OPTIMAL");
    assert(result.plan.solver.status == "optimal");
    assert(result.plan.solver.exact);
    assert(result.plan.solver.objective == 25);
    assert(result.plan.solver.primalBound == 25);
    assert(result.plan.solver.upperBound == 25);
    assert(result.plan.solver.absoluteGap == 0);
    assert(result.plan.targets.size() == 1);
    assert(result.plan.targets[0].targetPosition == 8000000);
    assert(result.plan.acceptedCandidates.size() == 2);
    assert(result.plan.rejectedProposals.empty());
    assert(GlobalAllocator::SolverDigest(result.plan.solver) ==
           result.plan.solver.digest);
    assert(GlobalAllocator::PlanDigest(result.plan) == result.plan.planDigest);

    GlobalAllocationResult repeated = GlobalAllocator::Allocate(
        Set(), Policy(100), 1, 1500);
    assert(repeated.accepted);
    assert(repeated.plan.planDigest == result.plan.planDigest);
}

void TestBoundedHeuristicIsTruthful()
{
    GlobalAllocationResult result = GlobalAllocator::Allocate(
        Set(), Policy(2), 1, 1500);
    assert(result.accepted);
    assert(result.reasonCode == "ALLOCATION_FEASIBLE_NOT_PROVEN");
    assert(result.plan.solver.status == "feasible_not_proven");
    assert(!result.plan.solver.exact);
    assert(result.plan.solver.objective == 20);
    assert(result.plan.solver.upperBound == 35);
    assert(result.plan.solver.absoluteGap == 15);
    assert(result.plan.targets.size() == 1);
    assert(result.plan.targets[0].targetPosition == 8000000);
    assert(result.plan.acceptedCandidates.size() == 1);
    assert(result.plan.rejectedProposals.size() == 1);
}

void TestConstraintAndPolicyFailures()
{
    GlobalAllocationPolicy invalid = Policy(100);
    invalid.maximumGrossTarget = 0;
    assert(GlobalAllocator::Allocate(Set(), invalid, 1, 1500).reasonCode ==
           "ALLOCATION_POLICY_INVALID");

    GlobalAllocationPolicy constrained = Policy(100);
    constrained.instrumentAbsoluteLimits["EUR.USD"] = 3000000;
    GlobalAllocationResult result = GlobalAllocator::Allocate(
        Set(), constrained, 1, 1500);
    assert(result.accepted);
    assert(result.plan.targets.empty());
    assert(result.plan.solver.objective == 0);
    assert(result.plan.rejectedProposals.size() == 2);

    ProposalSet tampered = Set();
    tampered.proposals[0].proposalId = "tampered";
    assert(GlobalAllocator::Allocate(tampered, Policy(100), 1, 1500)
               .reasonCode == "ALLOCATION_PROPOSAL_SET_INVALID");
    assert(GlobalAllocator::Allocate(Set(), Policy(100), 0, 1500)
               .reasonCode == "ALLOCATION_TIME_ENVELOPE_INVALID");
    ProposalSet expired = Set();
    expired.validUntilMs = 1500;
    expired.digest = ProposalSetBuilder::Digest(expired);
    assert(GlobalAllocator::Allocate(expired, Policy(100), 1, 1500)
               .reasonCode == "ALLOCATION_TIME_ENVELOPE_INVALID");
}

void TestBoundedPerformanceEnvelope()
{
    std::vector<StrategyProposal> proposals;
    std::vector<std::string> expected;
    GlobalAllocationPolicy policy;
    policy.policyRevision = "performance-policy-v1";
    policy.maximumGrossTarget = 20000000;
    policy.maximumInstruments = 16;
    policy.maximumExactCombinations = 1000000;
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
    for (std::uint64_t epoch = 1; epoch <= 20; ++epoch)
    {
        const GlobalAllocationResult result = GlobalAllocator::Allocate(
            proposalSet.proposalSet, policy, epoch, 1500);
        assert(result.accepted);
        assert(result.receipt.IsValid());
        assert(result.plan.solver.exact);
        assert(result.plan.solver.status == "optimal");
        assert(result.plan.solver.absoluteGap == 0);
        assert(result.plan.solver.combinationsExplored > 0);
        assert(result.plan.solver.combinationsExplored <= 1000000);
        if (solverDigest.empty()) solverDigest = result.plan.solver.digest;
        else assert(result.plan.solver.digest == solverDigest);
    }
    const std::uint64_t elapsedMs = static_cast<std::uint64_t>(
        std::chrono::duration_cast<std::chrono::milliseconds>(
            std::chrono::steady_clock::now() - started).count());
    assert(elapsedMs <= 30000);
}

}

int main()
{
    TestExactOptimalEvidence();
    TestBoundedHeuristicIsTruthful();
    TestConstraintAndPolicyFailures();
    TestBoundedPerformanceEnvelope();
    return 0;
}
