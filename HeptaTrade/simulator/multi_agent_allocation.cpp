#include "multi_agent_allocation.h"

#include <algorithm>
#include <set>

namespace
{
MultiAgentSimulationResult Reject(const char* code)
{
    MultiAgentSimulationResult result;
    result.reasonCode = code;
    return result;
}
}

const char* MultiAgentAllocationSimulator::Version()
{
    return "hepta.multi-agent-simulator.v1";
}

MultiAgentSimulationResult MultiAgentAllocationSimulator::RunCycle(
    const ModuleLifecycleRegistry& lifecycle,
    const std::vector<StrategyProposal>& proposals,
    const GlobalAllocationPolicy& allocationPolicy,
    std::uint64_t allocatorEpoch,
    std::uint64_t nowMs,
    std::uint64_t planValidUntilMs,
    const std::string& authoritativeSnapshotDigest,
    const AuthoritativePortfolioInput& authoritative,
    const PortfolioCapitalPolicy& executionPolicy)
{
    const std::vector<ModuleLifecycleSnapshot> active = lifecycle.ListActive();
    if (active.empty()) return Reject("SIMULATOR_NO_ACTIVE_STRATEGIES");
    std::vector<std::string> expected;
    std::set<std::string> activeIds;
    for (std::size_t i = 0; i < active.size(); ++i)
    {
        expected.push_back(active[i].identity.moduleId);
        activeIds.insert(active[i].identity.moduleId);
    }
    std::sort(expected.begin(), expected.end());

    std::vector<StrategyProposal> selected;
    MultiAgentSimulationResult result;
    for (std::size_t i = 0; i < proposals.size(); ++i)
    {
        if (activeIds.find(proposals[i].moduleId) == activeIds.end())
            result.ignoredModules.push_back(proposals[i].moduleId);
        else
            selected.push_back(proposals[i]);
    }
    std::sort(result.ignoredModules.begin(), result.ignoredModules.end());
    result.ignoredModules.erase(
        std::unique(result.ignoredModules.begin(), result.ignoredModules.end()),
        result.ignoredModules.end());

    const ProposalSetBuildResult proposalSet = ProposalSetBuilder::Build(
        selected, expected, nowMs);
    if (!proposalSet.accepted)
    {
        result.reasonCode = proposalSet.reasonCode;
        result.proposalSet = proposalSet.proposalSet;
        return result;
    }
    result.proposalSet = proposalSet.proposalSet;
    const GlobalAllocationResult allocation = GlobalAllocator::Allocate(
        result.proposalSet, allocationPolicy, allocatorEpoch,
        nowMs, planValidUntilMs);
    if (!allocation.accepted)
    {
        result.reasonCode = allocation.reasonCode;
        result.plan = allocation.plan;
        return result;
    }
    result.plan = allocation.plan;
    result.revalidation = AllocationPlanRevalidator::ValidateShadow(
        result.plan, authoritativeSnapshotDigest, nowMs,
        authoritative, executionPolicy);
    if (!result.revalidation.accepted)
    {
        result.reasonCode = result.revalidation.reasonCode;
        return result;
    }
    result.accepted = true;
    result.reasonCode = "SIMULATOR_MULTI_AGENT_CYCLE_ACCEPTED";
    return result;
}
