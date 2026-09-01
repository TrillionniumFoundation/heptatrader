#pragma once

#include "../allocation/global_allocator.h"
#include "../execution/allocation_plan_revalidator.h"
#include "../management/module_lifecycle.h"

#include <cstdint>
#include <string>
#include <vector>

struct MultiAgentSimulationResult
{
    bool accepted = false;
    std::string reasonCode;
    ProposalSet proposalSet;
    AllocationPlan plan;
    AllocationPlanRevalidationResult revalidation;
    std::vector<std::string> ignoredModules;
};

class MultiAgentAllocationSimulator
{
public:
    static const char* Version();
    static MultiAgentSimulationResult RunCycle(
        const ModuleLifecycleRegistry& lifecycle,
        const std::vector<StrategyProposal>& proposals,
        const GlobalAllocationPolicy& allocationPolicy,
        std::uint64_t allocatorEpoch,
        std::uint64_t nowMs,
        std::uint64_t planValidUntilMs,
        const std::string& authoritativeSnapshotDigest,
        const AuthoritativePortfolioInput& authoritative,
        const PortfolioCapitalPolicy& executionPolicy);
};
