#include "../HeptaTrade/allocation/global_allocator.h"

#include <cassert>
#include <string>
#include <vector>

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
        proposals, expected, 1500);
    assert(result.accepted);
    return result.proposalSet;
}

GlobalAllocationPolicy Policy(std::uint64_t combinations)
{
    GlobalAllocationPolicy policy;
    policy.maximumGrossTarget = 10000000;
    policy.maximumInstruments = 4;
    policy.maximumExactCombinations = combinations;
    policy.instrumentAbsoluteLimits["EUR.USD"] = 10000000;
    return policy;
}

void TestExactOptimalEvidence()
{
    GlobalAllocationResult result = GlobalAllocator::Allocate(
        Set(), Policy(100), 1, 1500, 1800);
    assert(result.accepted);
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
        Set(), Policy(100), 1, 1500, 1800);
    assert(repeated.accepted);
    assert(repeated.plan.planDigest == result.plan.planDigest);
}

void TestBoundedHeuristicIsTruthful()
{
    GlobalAllocationResult result = GlobalAllocator::Allocate(
        Set(), Policy(2), 1, 1500, 1800);
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
    assert(GlobalAllocator::Allocate(Set(), invalid, 1, 1500, 1800).reasonCode ==
           "ALLOCATION_POLICY_INVALID");

    GlobalAllocationPolicy constrained = Policy(100);
    constrained.instrumentAbsoluteLimits["EUR.USD"] = 3000000;
    GlobalAllocationResult result = GlobalAllocator::Allocate(
        Set(), constrained, 1, 1500, 1800);
    assert(result.accepted);
    assert(result.plan.targets.empty());
    assert(result.plan.solver.objective == 0);
    assert(result.plan.rejectedProposals.size() == 2);

    ProposalSet tampered = Set();
    tampered.proposals[0].proposalId = "tampered";
    assert(GlobalAllocator::Allocate(tampered, Policy(100), 1, 1500, 1800)
               .reasonCode == "ALLOCATION_PROPOSAL_SET_INVALID");
    assert(GlobalAllocator::Allocate(Set(), Policy(100), 0, 1500, 1800)
               .reasonCode == "ALLOCATION_TIME_ENVELOPE_INVALID");
}
}

int main()
{
    TestExactOptimalEvidence();
    TestBoundedHeuristicIsTruthful();
    TestConstraintAndPolicyFailures();
    return 0;
}
