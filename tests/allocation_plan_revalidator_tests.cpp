#include "../HeptaTrade/execution/allocation_plan_revalidator.h"
#include <cassert>
#include <string>
#include <type_traits>
#include <vector>

namespace
{
std::string Digest(char value) { return std::string("sha256:") + std::string(64, value); }

GlobalAllocationResult Allocation()
{
    StrategyProposal proposal;
    proposal.proposalId = "proposal-alpha";
    proposal.moduleId = "hepta.strategy.alpha";
    proposal.moduleVersion = "1.0.0";
    proposal.sequence = 1;
    proposal.capitalPool = "pool-a";
    proposal.accountBook = "book-a";
    proposal.snapshotDigest = Digest('a');
    proposal.validFromMs = 1000;
    proposal.expiresAtMs = 2000;
    proposal.horizonMs = 500;
    StrategyProposalCandidate candidate;
    candidate.candidateId = "candidate-a";
    candidate.utility = 10;
    candidate.targets.push_back({"EUR.USD", 8000000});
    proposal.candidates.push_back(candidate);
    ProposalSetBuildResult set = ProposalSetBuilder::Build(
        std::vector<StrategyProposal>(1, proposal),
        std::vector<std::string>(1, "hepta.strategy.alpha"), 1500, 1800);
    assert(set.accepted);
    GlobalAllocationPolicy allocation;
    allocation.policyRevision = "policy-v1";
    allocation.maximumGrossTarget = 10000000;
    allocation.maximumInstruments = 4;
    allocation.maximumExactCombinations = 100;
    allocation.instrumentAbsoluteLimits["EUR.USD"] = 10000000;
    GlobalAllocationResult result = GlobalAllocator::Allocate(set.proposalSet, allocation, 1, 1500);
    assert(result.accepted && result.receipt.IsValid());
    return result;
}

AuthoritativePortfolioInput Authoritative()
{
    AuthoritativePortfolioInput input; input.complete = true; input.generation = 7;
    input.currentPositions["EUR.USD"] = 2000000; return input;
}

PortfolioCapitalPolicy ExecutionPolicy(DecisionMicrounits gross)
{
    PortfolioCapitalPolicy policy; policy.maximumGrossTarget = gross;
    policy.maximumStrategies = 1; policy.maximumInstruments = 4;
    StrategyCapitalBudget budget; budget.strategyId = "global-allocation";
    budget.maximumGrossTarget = gross; policy.strategyBudgets[budget.strategyId] = budget;
    return policy;
}

AllocationExecutionContext Context(const GlobalAllocationResult& allocation)
{
    AllocationExecutionContext context;
    context.allocatorEpoch = allocation.plan.allocatorEpoch;
    context.capitalPool = allocation.plan.capitalPool;
    context.accountBook = allocation.plan.accountBook;
    context.policyRevision = allocation.plan.policyRevision;
    context.proposalSetDigest = allocation.plan.proposalSetDigest;
    context.authoritativeSnapshotDigest = allocation.plan.snapshotDigest;
    context.authoritativeSnapshotValidUntilMs = allocation.plan.snapshotValidUntilMs;
    return context;
}

void TestSealedShadowRevalidation()
{
    static_assert(!std::is_constructible<GlobalDecisionReceipt, AllocationPlan>::value,
                  "Execution receipt must not be publicly forgeable");
    GlobalAllocationResult allocation = Allocation();
    AllocationPlanRevalidationResult result = AllocationPlanRevalidator::ValidateShadow(
        allocation.receipt, Context(allocation), 1600, Authoritative(), ExecutionPolicy(10000000));
    assert(result.accepted && result.compiled.deltas[0].delta == 6000000);

    GlobalDecisionReceipt forged;
    assert(AllocationPlanRevalidator::ValidateShadow(
        forged, Context(allocation), 1600, Authoritative(), ExecutionPolicy(10000000)).reasonCode ==
        "ALLOCATION_PLAN_PROVENANCE_INVALID");
}

void TestContextAndLifetimeBinding()
{
    GlobalAllocationResult allocation = Allocation();
    AllocationExecutionContext context = Context(allocation);
    context.allocatorEpoch++;
    assert(AllocationPlanRevalidator::ValidateShadow(
        allocation.receipt, context, 1600, Authoritative(), ExecutionPolicy(10000000)).reasonCode ==
        "ALLOCATION_EXECUTION_CONTEXT_MISMATCH");
    context = Context(allocation); context.policyRevision = "policy-v2";
    assert(AllocationPlanRevalidator::ValidateShadow(
        allocation.receipt, context, 1600, Authoritative(), ExecutionPolicy(10000000)).reasonCode ==
        "ALLOCATION_EXECUTION_CONTEXT_MISMATCH");
    context = Context(allocation); context.accountBook = "other-book";
    assert(AllocationPlanRevalidator::ValidateShadow(
        allocation.receipt, context, 1600, Authoritative(), ExecutionPolicy(10000000)).reasonCode ==
        "ALLOCATION_EXECUTION_CONTEXT_MISMATCH");
    context = Context(allocation); context.authoritativeSnapshotDigest = Digest('b');
    assert(AllocationPlanRevalidator::ValidateShadow(
        allocation.receipt, context, 1600, Authoritative(), ExecutionPolicy(10000000)).reasonCode ==
        "ALLOCATION_PLAN_SNAPSHOT_MISMATCH");
    context = Context(allocation); context.authoritativeSnapshotValidUntilMs = 1700;
    assert(AllocationPlanRevalidator::ValidateShadow(
        allocation.receipt, context, 1600, Authoritative(), ExecutionPolicy(10000000)).reasonCode ==
        "ALLOCATION_PLAN_NOT_CURRENT");
    assert(AllocationPlanRevalidator::ValidateShadow(
        allocation.receipt, Context(allocation), 1800, Authoritative(), ExecutionPolicy(10000000)).reasonCode ==
        "ALLOCATION_PLAN_NOT_CURRENT");
}

void TestExecutionBudgetAndSnapshotRejection()
{
    GlobalAllocationResult allocation = Allocation();
    AllocationPlanRevalidationResult budget = AllocationPlanRevalidator::ValidateShadow(
        allocation.receipt, Context(allocation), 1600, Authoritative(), ExecutionPolicy(5000000));
    assert(!budget.accepted && budget.compiled.reasonCode == "PORTFOLIO_STRATEGY_BUDGET_EXCEEDED");
    AuthoritativePortfolioInput incomplete = Authoritative(); incomplete.complete = false;
    assert(AllocationPlanRevalidator::ValidateShadow(
        allocation.receipt, Context(allocation), 1600, incomplete, ExecutionPolicy(10000000)).reasonCode ==
        "ALLOCATION_AUTHORITATIVE_SNAPSHOT_INCOMPLETE");
}
}

int main()
{
    TestSealedShadowRevalidation();
    TestContextAndLifetimeBinding();
    TestExecutionBudgetAndSnapshotRejection();
    return 0;
}
