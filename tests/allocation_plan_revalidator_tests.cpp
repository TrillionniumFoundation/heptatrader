#include "../HeptaTrade/execution/allocation_plan_revalidator.h"

#include <cassert>
#include <string>
#include <vector>

namespace
{
std::string Digest(char value)
{
    return std::string("sha256:") + std::string(64, value);
}

AllocationPlan Plan()
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
    std::vector<StrategyProposal> proposals(1, proposal);
    std::vector<std::string> expected(1, "hepta.strategy.alpha");
    ProposalSetBuildResult set = ProposalSetBuilder::Build(
        proposals, expected, 1500);
    assert(set.accepted);
    GlobalAllocationPolicy allocation;
    allocation.maximumGrossTarget = 10000000;
    allocation.maximumInstruments = 4;
    allocation.maximumExactCombinations = 100;
    allocation.instrumentAbsoluteLimits["EUR.USD"] = 10000000;
    GlobalAllocationResult result = GlobalAllocator::Allocate(
        set.proposalSet, allocation, 1, 1500, 1800);
    assert(result.accepted);
    return result.plan;
}

AuthoritativePortfolioInput Authoritative()
{
    AuthoritativePortfolioInput input;
    input.complete = true;
    input.generation = 7;
    input.currentPositions["EUR.USD"] = 2000000;
    return input;
}

PortfolioCapitalPolicy ExecutionPolicy(DecisionMicrounits gross)
{
    PortfolioCapitalPolicy policy;
    policy.maximumGrossTarget = gross;
    policy.maximumStrategies = 1;
    policy.maximumInstruments = 4;
    StrategyCapitalBudget budget;
    budget.strategyId = "global-allocation";
    budget.maximumGrossTarget = gross;
    policy.strategyBudgets[budget.strategyId] = budget;
    return policy;
}

void TestShadowRevalidation()
{
    AllocationPlan plan = Plan();
    AllocationPlanRevalidationResult result =
        AllocationPlanRevalidator::ValidateShadow(
            plan, Digest('a'), 1600, Authoritative(),
            ExecutionPolicy(10000000));
    assert(result.accepted);
    assert(result.reasonCode == "ALLOCATION_PLAN_REVALIDATED_SHADOW");
    assert(result.compiled.accepted);
    assert(result.compiled.deltas.size() == 1);
    assert(result.compiled.deltas[0].delta == 6000000);
}

void TestPlanIntegrityAndFreshness()
{
    AllocationPlan plan = Plan();
    plan.targets[0].targetPosition = 9000000;
    assert(AllocationPlanRevalidator::ValidateShadow(
        plan, Digest('a'), 1600, Authoritative(),
        ExecutionPolicy(10000000)).reasonCode ==
        "ALLOCATION_PLAN_INTEGRITY_INVALID");

    plan = Plan();
    assert(AllocationPlanRevalidator::ValidateShadow(
        plan, Digest('a'), 1900, Authoritative(),
        ExecutionPolicy(10000000)).reasonCode ==
        "ALLOCATION_PLAN_NOT_CURRENT");
    assert(AllocationPlanRevalidator::ValidateShadow(
        plan, Digest('b'), 1600, Authoritative(),
        ExecutionPolicy(10000000)).reasonCode ==
        "ALLOCATION_PLAN_SNAPSHOT_MISMATCH");

    plan = Plan();
    plan.solver.status = "optimal";
    plan.solver.exact = false;
    plan.solver.digest = GlobalAllocator::SolverDigest(plan.solver);
    plan.planDigest = GlobalAllocator::PlanDigest(plan);
    assert(AllocationPlanRevalidator::ValidateShadow(
        plan, Digest('a'), 1600, Authoritative(),
        ExecutionPolicy(10000000)).reasonCode ==
        "ALLOCATION_SOLVER_EVIDENCE_INVALID");
}

void TestExecutionBudgetAndSnapshotRejection()
{
    AllocationPlan plan = Plan();
    AllocationPlanRevalidationResult budget =
        AllocationPlanRevalidator::ValidateShadow(
            plan, Digest('a'), 1600, Authoritative(),
            ExecutionPolicy(5000000));
    assert(!budget.accepted);
    assert(budget.reasonCode ==
           "ALLOCATION_EXECUTION_REVALIDATION_REJECTED");
    assert(budget.compiled.reasonCode ==
           "PORTFOLIO_STRATEGY_BUDGET_EXCEEDED");

    AuthoritativePortfolioInput incomplete = Authoritative();
    incomplete.complete = false;
    assert(AllocationPlanRevalidator::ValidateShadow(
        plan, Digest('a'), 1600, incomplete,
        ExecutionPolicy(10000000)).reasonCode ==
        "ALLOCATION_AUTHORITATIVE_SNAPSHOT_INCOMPLETE");
}
}

int main()
{
    TestShadowRevalidation();
    TestPlanIntegrityAndFreshness();
    TestExecutionBudgetAndSnapshotRejection();
    return 0;
}
