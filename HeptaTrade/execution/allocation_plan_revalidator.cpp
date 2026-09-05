#include "allocation_plan_revalidator.h"
#include "../numeric/fixed_decimal.h"
#include <limits>
#include <map>
#include <set>

namespace
{
AllocationPlanRevalidationResult Reject(const char* code)
{ AllocationPlanRevalidationResult result; result.reasonCode = code; return result; }

bool CheckedSubtract(DecisionMicrounits left, DecisionMicrounits right,
                     DecisionMicrounits& out)
{
    if ((right < 0 && left > std::numeric_limits<DecisionMicrounits>::max() + right) ||
        (right > 0 && left < std::numeric_limits<DecisionMicrounits>::min() + right))
        return false;
    out = left - right; return true;
}

bool SolverEvidenceValid(const AllocationSolverResult& solver)
{
    DecisionMicrounits expectedGap = 0;
    if (solver.digest.empty() || GlobalAllocator::SolverDigest(solver) != solver.digest ||
        solver.primalBound != solver.objective || solver.upperBound < solver.objective ||
        !CheckedSubtract(solver.upperBound, solver.objective, expectedGap) ||
        solver.absoluteGap != expectedGap) return false;
    if (solver.exact)
        return solver.status == "optimal" && solver.absoluteGap == 0 &&
            solver.upperBound == solver.objective;
    return solver.status == "feasible_not_proven";
}
}

const char* AllocationPlanRevalidator::Version()
{ return "allocation-plan-revalidator-v2"; }

AllocationPlanRevalidationResult AllocationPlanRevalidator::ValidateShadow(
    const GlobalDecisionReceipt& receipt,
    const AllocationExecutionContext& context,
    std::uint64_t nowMs,
    const AuthoritativePortfolioInput& authoritative,
    const PortfolioCapitalPolicy& policy)
{
    if (!receipt.IsValid()) return Reject("ALLOCATION_PLAN_PROVENANCE_INVALID");
    const AllocationPlan& plan = receipt.Plan();
    if (plan.planId.empty() || plan.allocatorEpoch == 0 || plan.planDigest.empty() ||
        GlobalAllocator::PlanDigest(plan) != plan.planDigest)
        return Reject("ALLOCATION_PLAN_INTEGRITY_INVALID");
    if (!SolverEvidenceValid(plan.solver))
        return Reject("ALLOCATION_SOLVER_EVIDENCE_INVALID");
    if (context.allocatorEpoch != plan.allocatorEpoch ||
        context.capitalPool != plan.capitalPool ||
        context.accountBook != plan.accountBook ||
        context.policyRevision != plan.policyRevision ||
        context.proposalSetDigest != plan.proposalSetDigest)
        return Reject("ALLOCATION_EXECUTION_CONTEXT_MISMATCH");
    if (context.authoritativeSnapshotDigest.empty() ||
        context.authoritativeSnapshotDigest != plan.snapshotDigest)
        return Reject("ALLOCATION_PLAN_SNAPSHOT_MISMATCH");
    if (plan.createdAtMs == 0 || plan.validUntilMs <= plan.createdAtMs ||
        plan.validUntilMs != plan.proposalValidUntilMs ||
        plan.validUntilMs > plan.snapshotValidUntilMs ||
        context.authoritativeSnapshotValidUntilMs < plan.validUntilMs ||
        nowMs < plan.createdAtMs || nowMs >= plan.validUntilMs ||
        nowMs >= context.authoritativeSnapshotValidUntilMs)
        return Reject("ALLOCATION_PLAN_NOT_CURRENT");
    if (!authoritative.complete || authoritative.generation == 0)
        return Reject("ALLOCATION_AUTHORITATIVE_SNAPSHOT_INCOMPLETE");

    std::set<std::string> instruments;
    std::map<std::string, DecisionMicrounits> expected;
    std::vector<StrategyTargetIntent> intents;
    for (std::size_t i = 0; i < plan.targets.size(); ++i)
    {
        const AllocationTarget& target = plan.targets[i];
        if (target.instrument.empty() || !instruments.insert(target.instrument).second ||
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
        intents.push_back(intent); expected[target.instrument] = target.targetPosition;
    }
    const PortfolioCompileResult compiled = PortfolioCompiler::Compile(intents, authoritative, policy);
    if (!compiled.accepted)
    {
        AllocationPlanRevalidationResult rejected = Reject("ALLOCATION_EXECUTION_REVALIDATION_REJECTED");
        rejected.compiled = compiled; return rejected;
    }
    if (compiled.netTargets != expected)
    {
        AllocationPlanRevalidationResult rejected = Reject("ALLOCATION_EXECUTION_TARGET_MISMATCH");
        rejected.compiled = compiled; return rejected;
    }
    AllocationPlanRevalidationResult result;
    result.accepted = true; result.reasonCode = "ALLOCATION_PLAN_REVALIDATED_SHADOW";
    result.compiled = compiled; return result;
}
