#pragma once

#include "../allocation/global_allocator.h"
#include "../portfolio/portfolio_compiler.h"

#include <cstdint>
#include <string>

struct AllocationExecutionContext
{
    std::uint64_t allocatorEpoch = 0;
    std::string capitalPool;
    std::string accountBook;
    std::string policyRevision;
    std::string proposalSetDigest;
    std::string authoritativeSnapshotDigest;
    std::uint64_t authoritativeSnapshotValidUntilMs = 0;
};

struct AllocationPlanRevalidationResult
{
    bool accepted = false;
    std::string reasonCode;
    PortfolioCompileResult compiled;
};

class AllocationPlanRevalidator
{
public:
    static const char* Version();
    static AllocationPlanRevalidationResult ValidateShadow(
        const GlobalDecisionReceipt& receipt,
        const AllocationExecutionContext& context,
        std::uint64_t nowMs,
        const AuthoritativePortfolioInput& authoritative,
        const PortfolioCapitalPolicy& policy);
};
