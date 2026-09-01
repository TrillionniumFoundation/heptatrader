#include "allocation_plan_revalidator.h"

#include "../numeric/fixed_decimal.h"

#include <limits>
#include <map>
#include <set>

namespace
{
AllocationPlanRevalidationResult Reject(const char* code)
{
    AllocationPlanRevalidationResult result;
    result.reasonCode = code;
    return result;
}

bool SolverEvidenceValid(const AllocationSolverResult& solver)
{
    if (solver.digest.empty() ||
        GlobalAllocator::SolverDigest(solver) != solver.digest ||
        solver.primalBound != solver.objective ||
        solver.upperBound < solver.objective ||
        solver.absoluteGap != solver.upperBound - solver.objective)
        return false;
    if (solver.exact)
        return solver.status == "optimal" && solver.absoluteGap == 0 &&
            solver.upperBound == solver.objective;
    return solver.status == "feasible_not_proven";
}
}

const char* AllocationPlanRevalidator::Version()
{
    return "allocation-plan-revalidator-v1";
}

AllocationPlanRevalidationResult AllocationPlanRevalidator::ValidateShadow(
    const AllocationPlan& plan,
    const std::string& authoritativeSnapshotDigest,
    std::uint64_t nowMs,
    const AuthoritativePortfolioInput& authoritative,
    const PortfolioCapitalPolicy& policy)
{
    if (plan.planId.empty() || plan.allocatorEpoch == 0 ||
        plan.planDigest.empty() ||
        GlobalAllocator::PlanDigest(plan) != plan.planDigest)
        return Reject("ALLOCATION_PLAN_INTEGRITY_INVALID");
    if (!SolverEvidenceValid(plan.solver))
        return Reject("ALLOCATION_SOLVER_EVIDENCE_INVALID");
    if (plan.createdAtMs == 0 || plan.validUntilMs <= plan.createdAtMs ||
        nowMs < plan.createdAtMs || nowMs > plan.validUntilMs)
        return Reject("ALLOCATION_PLAN_NOT_CURRENT");
    if (authoritativeSnapshotDigest.empty() ||
        authoritativeSnapshotDigest != plan.snapshotDigest)
        return Reject("ALLOCATION_PLAN_SNAPSHOT_MISMATCH");
    if (!authoritative.complete || authoritative.generation == 0)
        return Reject("ALLOCATION_AUTHORITATIVE_SNAPSHOT_INCOMPLETE");

    std::set<std::string> instruments;
    std::map<std::string, DecisionMicrounits> expected;
    std::vector<StrategyTargetIntent> intents;
    for (std::size_t i = 0; i < plan.targets.size(); ++i)
    {
        const AllocationTarget& target = plan.targets[i];
        if (target.instrument.empty() ||
            !instruments.insert(target.instrument).second ||
            target.targetPosition < -HeptaFixedDecimal::kMaximumRaw ||
            target.targetPosition > HeptaFixedDecimal::kMaximumRaw)
            return Reject("ALLOCATION_PLAN_TARGET_INVALID");
        if (i > 0 && plan.targets[i - 1].instrument >= target.instrument)
            return Reject("ALLOCATION_PLAN_TARGET_ORDER_INVALID");
        StrategyTargetIntent intent;
        intent.strategyId = "global-allocation";
        intent.instrument = target.instrument;
        intent.targetPosition = target.targetPosition;
        intent.snapshotGeneration = authoritative.generation;
        intents.push_back(intent);
        expected[target.instrument] = target.targetPosition;
    }

    const PortfolioCompileResult compiled =
        PortfolioCompiler::Compile(intents, authoritative, policy);
    if (!compiled.accepted)
    {
        AllocationPlanRevalidationResult rejected =
            Reject("ALLOCATION_EXECUTION_REVALIDATION_REJECTED");
        rejected.compiled = compiled;
        return rejected;
    }
    if (compiled.netTargets != expected)
    {
        AllocationPlanRevalidationResult rejected =
            Reject("ALLOCATION_EXECUTION_TARGET_MISMATCH");
        rejected.compiled = compiled;
        return rejected;
    }
    AllocationPlanRevalidationResult result;
    result.accepted = true;
    result.reasonCode = "ALLOCATION_PLAN_REVALIDATED_SHADOW";
    result.compiled = compiled;
    return result;
}
