#include "../HeptaTrade/allocation/global_allocator.h"

#include <algorithm>
#include <cstdlib>
#include <iostream>
#include <limits>
#include <map>
#include <string>
#include <vector>
#include <chrono>
#include <cstdint>

namespace
{
std::uint64_t checkedAssertions = 0;

void Require(bool condition, const char* expression, int line)
{
    ++checkedAssertions;
    if (condition) return;
    std::cerr << "requirement failed at line " << line << ": " << expression << '\n';
    std::abort();
}
#define REQUIRE(expression) Require(static_cast<bool>(expression), #expression, __LINE__)
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
    REQUIRE(result.accepted);
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
    REQUIRE(result.accepted);
    REQUIRE(result.receipt.IsValid());
    REQUIRE(result.receipt.Plan().planDigest == result.plan.planDigest);
    REQUIRE(result.plan.validUntilMs == 1800);
    REQUIRE(result.reasonCode == "ALLOCATION_OPTIMAL");
    REQUIRE(result.plan.solver.status == "optimal");
    REQUIRE(result.plan.solver.exact);
    REQUIRE(result.plan.solver.objective == 25);
    REQUIRE(result.plan.solver.primalBound == 25);
    REQUIRE(result.plan.solver.upperBound == 25);
    REQUIRE(result.plan.solver.absoluteGap == 0);
    REQUIRE(result.plan.targets.size() == 1);
    REQUIRE(result.plan.targets[0].targetPosition == 8000000);
    REQUIRE(result.plan.acceptedCandidates.size() == 2);
    REQUIRE(result.plan.rejectedProposals.empty());
    REQUIRE(GlobalAllocator::SolverDigest(result.plan.solver) ==
           result.plan.solver.digest);
    REQUIRE(GlobalAllocator::PlanDigest(result.plan) == result.plan.planDigest);

    GlobalAllocationResult repeated = GlobalAllocator::Allocate(
        Set(), Policy(100), 1, 1500);
    REQUIRE(repeated.accepted);
    REQUIRE(repeated.plan.planDigest == result.plan.planDigest);
}

void TestBoundedHeuristicIsTruthful()
{
    GlobalAllocationResult result = GlobalAllocator::Allocate(
        Set(), Policy(2), 1, 1500);
    REQUIRE(result.accepted);
    REQUIRE(result.reasonCode == "ALLOCATION_FEASIBLE_NOT_PROVEN");
    REQUIRE(result.plan.solver.status == "feasible_not_proven");
    REQUIRE(!result.plan.solver.exact);
    REQUIRE(result.plan.solver.objective == 20);
    REQUIRE(result.plan.solver.upperBound == 35);
    REQUIRE(result.plan.solver.absoluteGap == 15);
    REQUIRE(result.plan.targets.size() == 1);
    REQUIRE(result.plan.targets[0].targetPosition == 8000000);
    REQUIRE(result.plan.acceptedCandidates.size() == 1);
    REQUIRE(result.plan.rejectedProposals.size() == 1);
}

void TestConstraintAndPolicyFailures()
{
    GlobalAllocationPolicy invalid = Policy(100);
    invalid.maximumGrossTarget = 0;
    REQUIRE(GlobalAllocator::Allocate(Set(), invalid, 1, 1500).reasonCode ==
           "ALLOCATION_POLICY_INVALID");

    GlobalAllocationPolicy constrained = Policy(100);
    constrained.instrumentAbsoluteLimits["EUR.USD"] = 3000000;
    GlobalAllocationResult result = GlobalAllocator::Allocate(
        Set(), constrained, 1, 1500);
    REQUIRE(result.accepted);
    // +4m with utility 10, offset by -1m with utility -5, is feasible.
    // The former empty-plan expectation encoded invalid prefix pruning.
    REQUIRE(result.plan.targets.size() == 1);
    REQUIRE(result.plan.targets[0].targetPosition == 3000000);
    REQUIRE(result.plan.solver.objective == 5);
    REQUIRE(result.plan.rejectedProposals.empty());

    ProposalSet tampered = Set();
    tampered.proposals[0].proposalId = "tampered";
    REQUIRE(GlobalAllocator::Allocate(tampered, Policy(100), 1, 1500)
               .reasonCode == "ALLOCATION_PROPOSAL_SET_INVALID");
    REQUIRE(GlobalAllocator::Allocate(Set(), Policy(100), 0, 1500)
               .reasonCode == "ALLOCATION_TIME_ENVELOPE_INVALID");
    ProposalSet expired = Set();
    expired.validUntilMs = 1500;
    expired.digest = ProposalSetBuilder::Digest(expired);
    REQUIRE(GlobalAllocator::Allocate(expired, Policy(100), 1, 1500)
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
    REQUIRE(proposalSet.accepted);

    std::string solverDigest;
    const std::chrono::steady_clock::time_point started =
        std::chrono::steady_clock::now();
    for (std::uint64_t epoch = 1; epoch <= 20; ++epoch)
    {
        const GlobalAllocationResult result = GlobalAllocator::Allocate(
            proposalSet.proposalSet, policy, epoch, 1500);
        REQUIRE(result.accepted);
        REQUIRE(result.receipt.IsValid());
        REQUIRE(result.plan.solver.exact);
        REQUIRE(result.plan.solver.status == "optimal");
        REQUIRE(result.plan.solver.absoluteGap == 0);
        REQUIRE(result.plan.solver.combinationsExplored > 0);
        REQUIRE(result.plan.solver.combinationsExplored <= 1000000);
        if (solverDigest.empty()) solverDigest = result.plan.solver.digest;
        else REQUIRE(result.plan.solver.digest == solverDigest);
    }
    const std::uint64_t elapsedMs = static_cast<std::uint64_t>(
        std::chrono::duration_cast<std::chrono::milliseconds>(
            std::chrono::steady_clock::now() - started).count());
    REQUIRE(elapsedMs <= 30000);
}

// Independent complete-assignment oracle: no production projection, feasibility
// or digest helper is used to establish the expected optimum. Fixture sums
// fit int64: seeded values are small; the large case has three 9e15 targets.
struct OracleResult
{
    DecisionMicrounits objective = 0;
    std::map<std::string, DecisionMicrounits> targets;
    std::vector<std::string> accepted;
    std::vector<std::string> rejected;
    std::vector<std::string> tieKey;
    std::uint64_t combinations = 0;
};

bool OracleFeasible(const std::map<std::string, DecisionMicrounits>& targets,
                    const GlobalAllocationPolicy& policy)
{
    DecisionMicrounits gross = 0;
    std::size_t active = 0;
    for (const auto& target : targets)
    {
        const auto limit = policy.instrumentAbsoluteLimits.find(target.first);
        if (limit == policy.instrumentAbsoluteLimits.end()) return false;
        const auto magnitude = target.second < 0 ? -target.second : target.second;
        if (magnitude > limit->second) return false;
        gross += magnitude;
        active += magnitude != 0 ? 1u : 0u;
    }
    return gross <= policy.maximumGrossTarget && active <= policy.maximumInstruments;
}

OracleResult Enumerate(const ProposalSet& set, const GlobalAllocationPolicy& policy)
{
    OracleResult best;
    std::uint64_t count = 1;
    for (const auto& p : set.proposals) count *= p.candidates.size() + 1u;
    best.combinations = count;
    bool initialized = false;
    for (std::uint64_t combination = 0; combination < count; ++combination)
    {
        std::uint64_t remaining = combination;
        OracleResult candidate;
        for (const auto& p : set.proposals)
        {
            const std::size_t digit = remaining % (p.candidates.size() + 1u);
            remaining /= p.candidates.size() + 1u;
            if (digit == 0)
            {
                candidate.rejected.push_back(p.proposalId);
                candidate.tieKey.push_back("");
                continue;
            }
            const auto& choice = p.candidates[digit - 1u];
            candidate.tieKey.push_back(choice.candidateId);
            candidate.accepted.push_back(p.proposalId + ":" + choice.candidateId);
            candidate.objective += choice.utility;
            for (const auto& target : choice.targets)
                candidate.targets[target.instrument] += target.targetPosition;
        }
        if (!OracleFeasible(candidate.targets, policy)) continue;
        if (!initialized || candidate.objective > best.objective ||
            (candidate.objective == best.objective && candidate.tieKey < best.tieKey))
        {
            best = candidate;
            best.combinations = count;
            initialized = true;
        }
    }
    REQUIRE(initialized); // all-skip is always feasible under a valid policy
    for (auto it = best.targets.begin(); it != best.targets.end(); )
        if (it->second == 0) it = best.targets.erase(it); else ++it;
    return best;
}

ProposalSet BuildSet(const std::vector<StrategyProposal>& proposals)
{
    std::vector<std::string> expected;
    for (const auto& p : proposals) expected.push_back(p.moduleId);
    const auto built = ProposalSetBuilder::Build(proposals, expected, 1500, 1800);
    REQUIRE(built.accepted);
    return built.proposalSet;
}

StrategyProposal Single(const std::string& module, DecisionMicrounits utility,
                        const std::vector<StrategyCandidateTarget>& targets)
{
    auto p = Proposal(module, module + ".proposal", 0, 0, 0, 0);
    p.candidates.resize(1);
    p.candidates[0].utility = utility;
    p.candidates[0].targets = targets;
    return p;
}

void CompareOracle(const ProposalSet& set, GlobalAllocationPolicy policy)
{
    const auto expected = Enumerate(set, policy);
    const auto exact = GlobalAllocator::Allocate(set, policy, 7, 1500);
    REQUIRE(exact.accepted && exact.receipt.IsValid());
    REQUIRE(exact.plan.solver.exact);
    REQUIRE(exact.plan.solver.status == "optimal");
    REQUIRE(exact.plan.solver.objective == expected.objective);
    REQUIRE(exact.plan.solver.primalBound == expected.objective);
    REQUIRE(exact.plan.solver.upperBound == expected.objective);
    REQUIRE(exact.plan.solver.absoluteGap == 0);
    REQUIRE(exact.plan.solver.combinationsExplored > 0);
    REQUIRE(exact.plan.solver.combinationsExplored <= expected.combinations);
    REQUIRE(exact.plan.acceptedCandidates == expected.accepted);
    REQUIRE(exact.plan.rejectedProposals == expected.rejected);
    std::map<std::string, DecisionMicrounits> actual;
    for (const auto& target : exact.plan.targets)
        REQUIRE(actual.emplace(target.instrument, target.targetPosition).second);
    REQUIRE(actual == expected.targets);
    REQUIRE(GlobalAllocator::PlanDigest(exact.plan) == exact.plan.planDigest);

    policy.maximumExactCombinations = 1;
    const auto fallback = GlobalAllocator::Allocate(set, policy, 7, 1500);
    REQUIRE(fallback.accepted && fallback.receipt.IsValid());
    REQUIRE(!fallback.plan.solver.exact);
    REQUIRE(fallback.plan.solver.status == "feasible_not_proven");
    REQUIRE(fallback.plan.solver.objective <= expected.objective);
    REQUIRE(fallback.plan.solver.upperBound >= expected.objective);
    REQUIRE(fallback.plan.solver.primalBound == fallback.plan.solver.objective);
    REQUIRE(fallback.plan.solver.absoluteGap ==
            fallback.plan.solver.upperBound - fallback.plan.solver.objective);
    actual.clear();
    for (const auto& target : fallback.plan.targets)
        REQUIRE(actual.emplace(target.instrument, target.targetPosition).second);
    REQUIRE(OracleFeasible(actual, policy));
}

void TestOffsettingInstrumentLimitAndNegativeUtility()
{
    for (const int sign : {-1, 1})
    {
        auto policy = Policy(100);
        policy.instrumentAbsoluteLimits["EUR.USD"] = 3;
        const auto set = BuildSet({
            Single("hepta.a", 10, {{"EUR.USD", sign * 4}}),
            Single("hepta.b", -5, {{"EUR.USD", sign * -1}})});
        CompareOracle(set, policy);
        const auto result = GlobalAllocator::Allocate(set, policy, 1, 1500);
        REQUIRE(result.plan.solver.objective == 5);
        REQUIRE(result.plan.targets[0].targetPosition == sign * 3);
        REQUIRE(result.plan.acceptedCandidates.size() == 2);
    }
}

void TestOffsettingGrossAndInstrumentCountLimits()
{
    auto policy = Policy(100);
    policy.instrumentAbsoluteLimits["OTHER"] = 10;
    policy.maximumGrossTarget = 4;
    const auto set = BuildSet({
        Single("hepta.a", 10, {{"EUR.USD", 4}, {"OTHER", 4}}),
        Single("hepta.b", 1, {{"OTHER", -4}})});
    CompareOracle(set, policy);
    REQUIRE(GlobalAllocator::Allocate(set, policy, 1, 1500).plan.solver.objective == 11);
    policy.maximumGrossTarget = 10;
    policy.maximumInstruments = 1;
    CompareOracle(set, policy);
    REQUIRE(GlobalAllocator::Allocate(set, policy, 1, 1500).plan.solver.objective == 11);
}

void TestLargeIntermediateTargetsRemainRepresentable()
{
    const DecisionMicrounits maximum = 9000000000000000LL;
    auto policy = Policy(100);
    policy.maximumGrossTarget = maximum;
    policy.instrumentAbsoluteLimits["EUR.USD"] = maximum;
    const auto set = BuildSet({
        Single("hepta.a", 10, {{"EUR.USD", maximum}}),
        Single("hepta.b", 20, {{"EUR.USD", maximum}}),
        Single("hepta.c", -1, {{"EUR.USD", -maximum}})});
    CompareOracle(set, policy);
    const auto result = GlobalAllocator::Allocate(set, policy, 1, 1500);
    REQUIRE(result.plan.solver.objective == 29);
    REQUIRE(result.plan.targets[0].targetPosition == maximum);
}

void TestNestedMutationCannotReuseMemberDigests()
{
    for (int mutation = 0; mutation < 9; ++mutation)
        for (bool rehashOuter : {false, true})
        {
            auto set = Set();
            auto& member = set.proposals[0];
            switch (mutation)
            {
            case 0: member.candidates[0].utility = 999; break;
            case 1: member.candidates[0].targets[0].targetPosition = 1; break;
            case 2: member.candidates[0].candidateId = "changed"; break;
            case 3: member.candidates[0].targets[0].instrument = "OTHER"; break;
            case 4: member.candidates.clear(); break;
            case 5: member.horizonMs = 1; break;
            case 6: member.capitalPool = "other-pool"; break;
            case 7: member.proposalDigest.clear(); break;
            case 8: member.candidates[0].targets.push_back(member.candidates[0].targets[0]); break;
            }
            if (rehashOuter) set.digest = ProposalSetBuilder::Digest(set);
            const auto result = GlobalAllocator::Allocate(set, Policy(100), 1, 1500);
            REQUIRE(!result.accepted && !result.receipt.IsValid());
            REQUIRE(result.reasonCode == "ALLOCATION_PROPOSAL_SET_INVALID");
        }
}

void TestSelfConsistentDigestsDoNotReplaceSemanticValidation()
{
    for (int mutation = 0; mutation < 10; ++mutation)
    {
        auto set = Set();
        auto& member = set.proposals[0];
        switch (mutation)
        {
        case 0: member.candidates[0].utility = std::numeric_limits<DecisionMicrounits>::min(); break;
        case 1: member.candidates[0].targets[0].targetPosition = 9000000000000001LL; break;
        case 2: member.candidates[1].candidateId = member.candidates[0].candidateId; break;
        case 3: member.candidates[0].targets.push_back(member.candidates[0].targets[0]); break;
        case 4: member.accountBook = "another-book"; break;
        case 5: member.snapshotDigest = Digest('b'); break;
        case 6: member.horizonMs = 0; break;
        case 7: member.sequence = 0; break;
        case 8: member.candidates[0].targets.clear(); break;
        case 9: member.candidates.resize(257, member.candidates[0]); break;
        }
        member.proposalDigest = StrategyProposalContract::Digest(member);
        set.digest = ProposalSetBuilder::Digest(set);
        const auto result = GlobalAllocator::Allocate(set, Policy(100), 1, 1500);
        REQUIRE(!result.accepted && !result.receipt.IsValid());
        REQUIRE(result.reasonCode == "ALLOCATION_PROPOSAL_SET_INVALID");
    }
    for (int mutation = 0; mutation < 5; ++mutation)
    {
        auto set = Set();
        switch (mutation)
        {
        case 0: set.capitalPool = "header-only-pool"; break;
        case 1: set.accountBook = "header-only-book"; break;
        case 2: set.snapshotDigest = Digest('c'); break;
        case 3: set.validFromMs = 1400; break;
        case 4: set.proposals.resize(257, set.proposals[0]); break;
        }
        set.digest = ProposalSetBuilder::Digest(set);
        const auto result = GlobalAllocator::Allocate(set, Policy(100), 1, 1500);
        REQUIRE(!result.accepted && !result.receipt.IsValid());
        REQUIRE(result.reasonCode == "ALLOCATION_PROPOSAL_SET_INVALID");
    }
}

void TestCanonicalRebuildDoesNotRenewHorizonsOrDependOnCandidateOrder()
{
    auto set = Set();
    const auto first = GlobalAllocator::Allocate(set, Policy(100), 1, 1500);
    std::reverse(set.proposals[0].candidates.begin(), set.proposals[0].candidates.end());
    // Its member digest already denotes this same canonical body.
    const auto reordered = GlobalAllocator::Allocate(set, Policy(100), 1, 1500);
    REQUIRE(reordered.accepted);
    REQUIRE(reordered.plan.planDigest == first.plan.planDigest);
    const auto later = GlobalAllocator::Allocate(set, Policy(100), 1, 1799);
    REQUIRE(later.accepted && later.plan.validUntilMs == 1800);
    REQUIRE(GlobalAllocator::Allocate(set, Policy(100), 1, 1800).reasonCode ==
            "ALLOCATION_TIME_ENVELOPE_INVALID");
    auto shortSet = Set();
    shortSet.proposals[0].horizonMs = 100;
    shortSet.proposals[0].proposalDigest.clear();
    shortSet = BuildSet(shortSet.proposals);
    REQUIRE(shortSet.validUntilMs == 1600);
    shortSet.validUntilMs = 1700;
    shortSet.digest = ProposalSetBuilder::Digest(shortSet);
    REQUIRE(!GlobalAllocator::Allocate(shortSet, Policy(100), 1, 1550).accepted);
}

void TestExactBudgetBoundaryUnknownInstrumentsAndDeterministicTies()
{
    const auto set = Set();
    auto policy = Policy(9);
    const auto at = GlobalAllocator::Allocate(set, policy, 1, 1500);
    REQUIRE(at.accepted && at.plan.solver.exact);
    REQUIRE(at.plan.solver.combinationsExplored == 9);
    policy.maximumExactCombinations = 8;
    REQUIRE(!GlobalAllocator::Allocate(set, policy, 1, 1500).plan.solver.exact);
    const auto unsupported = BuildSet({Single("hepta.a", 10, {{"UNKNOWN", 0}})});
    CompareOracle(unsupported, Policy(100));
    REQUIRE(GlobalAllocator::Allocate(unsupported, Policy(100), 1, 1500)
                .plan.acceptedCandidates.empty());
    auto tied = Proposal("hepta.a", "tie", 10, 1, 10, -1);
    const auto canonical = BuildSet({tied});
    CompareOracle(canonical, Policy(100));
    std::reverse(tied.candidates.begin(), tied.candidates.end());
    REQUIRE(GlobalAllocator::Allocate(BuildSet({tied}), Policy(100), 1, 1500)
                .plan.planDigest ==
            GlobalAllocator::Allocate(canonical, Policy(100), 1, 1500).plan.planDigest);
}

void TestZeroUtilityRejectsUnlessNeededAndTiesUseLexicalIds()
{
    auto zero = Single("hepta.a", 0, {{"EUR.USD", 1}});
    zero.candidates[0].candidateId = "long-candidate-id";
    const auto empty = GlobalAllocator::Allocate(BuildSet({zero}), Policy(100), 1, 1500);
    REQUIRE(empty.accepted && empty.plan.solver.objective == 0);
    REQUIRE(empty.plan.acceptedCandidates.empty() && empty.plan.targets.empty());
    CompareOracle(BuildSet({zero}), Policy(100));

    auto tied = Proposal("hepta.a", "tie", 10, 1, 10, 2);
    tied.candidates[0].candidateId = "aa";
    tied.candidates[1].candidateId = "z";
    const auto lexical = GlobalAllocator::Allocate(BuildSet({tied}), Policy(100), 1, 1500);
    REQUIRE(lexical.plan.acceptedCandidates.size() == 1);
    REQUIRE(lexical.plan.acceptedCandidates[0] == "tie:aa");
    REQUIRE(lexical.plan.targets[0].targetPosition == 1);
    CompareOracle(BuildSet({tied}), Policy(100));

    auto policy = Policy(100);
    policy.instrumentAbsoluteLimits["EUR.USD"] = 3;
    const auto hedge = BuildSet({Single("hepta.a", 10, {{"EUR.USD", 4}}),
                                Single("hepta.b", 0, {{"EUR.USD", -1}})});
    const auto beneficial = GlobalAllocator::Allocate(hedge, policy, 1, 1500);
    REQUIRE(beneficial.plan.solver.objective == 10);
    REQUIRE(beneficial.plan.acceptedCandidates.size() == 2);
    CompareOracle(hedge, policy);
}

void TestSeededCompleteAssignmentOracle()
{
    std::uint32_t state = 0x20260905u;
    const auto next = [&state](std::uint32_t n) {
        state = state * 1664525u + 1013904223u;
        return state % n;
    };
    std::uint64_t assignments = 0;
    for (unsigned int sample = 0; sample < 512u; ++sample)
    {
        auto policy = Policy(1000000);
        policy.instrumentAbsoluteLimits.clear();
        const std::size_t instrumentCount = 1u + next(3);
        for (std::size_t k = 0; k < instrumentCount; ++k)
            policy.instrumentAbsoluteLimits["X" + std::to_string(k)] = 1u + next(12);
        policy.maximumGrossTarget = 1u + next(24);
        policy.maximumInstruments = 1u + next(static_cast<std::uint32_t>(instrumentCount));
        std::vector<StrategyProposal> proposals;
        const std::size_t moduleCount = 1u + next(5);
        for (std::size_t i = 0; i < moduleCount; ++i)
        {
            auto p = Single("hepta.s" + std::to_string(i), 0, {{"X0", 0}});
            p.candidates.clear();
            const std::size_t candidateCount = 1u + next(3);
            for (std::size_t j = 0; j < candidateCount; ++j)
            {
                StrategyProposalCandidate c;
                c.candidateId = "c" + std::to_string(j);
                c.utility = static_cast<DecisionMicrounits>(next(26)) - 5;
                for (std::size_t k = 0; k < instrumentCount; ++k)
                    c.targets.push_back({"X" + std::to_string(k),
                        static_cast<DecisionMicrounits>(next(25)) - 12});
                p.candidates.push_back(c);
            }
            proposals.push_back(p);
        }
        const auto set = BuildSet(proposals);
        CompareOracle(set, policy);
        assignments += Enumerate(set, policy).combinations;
        std::reverse(proposals.begin(), proposals.end());
        for (auto& p : proposals)
        {
            std::reverse(p.candidates.begin(), p.candidates.end());
            for (auto& c : p.candidates) std::reverse(c.targets.begin(), c.targets.end());
        }
        REQUIRE(GlobalAllocator::Allocate(BuildSet(proposals), policy, 1, 1500)
                    .plan.planDigest ==
                GlobalAllocator::Allocate(set, policy, 1, 1500).plan.planDigest);
    }
    std::cout << "allocator oracle: 512 seeded fixtures, " << assignments
              << " complete assignments; exact/fallback/permutation checks passed\n";
}

}

int main()
{
    TestExactOptimalEvidence();
    TestBoundedHeuristicIsTruthful();
    TestConstraintAndPolicyFailures();
    TestBoundedPerformanceEnvelope();
    TestOffsettingInstrumentLimitAndNegativeUtility();
    TestOffsettingGrossAndInstrumentCountLimits();
    TestLargeIntermediateTargetsRemainRepresentable();
    TestNestedMutationCannotReuseMemberDigests();
    TestSelfConsistentDigestsDoNotReplaceSemanticValidation();
    TestCanonicalRebuildDoesNotRenewHorizonsOrDependOnCandidateOrder();
    TestExactBudgetBoundaryUnknownInstrumentsAndDeterministicTies();
    TestZeroUtilityRejectsUnlessNeededAndTiesUseLexicalIds();
    TestSeededCompleteAssignmentOracle();
    std::cout << "total assertions: " << checkedAssertions << "\n";
    return 0;
}
