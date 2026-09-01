#pragma once

#include "../allocation/global_allocator.h"
#include "../portfolio/portfolio_compiler.h"

#include <cstdint>
#include <string>

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
        const AllocationPlan& plan,
        const std::string& authoritativeSnapshotDigest,
        std::uint64_t nowMs,
        const AuthoritativePortfolioInput& authoritative,
        const PortfolioCapitalPolicy& policy);
};
