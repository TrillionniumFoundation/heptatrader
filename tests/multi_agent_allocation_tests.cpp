#include "../HeptaTrade/simulator/multi_agent_allocation.h"

#include <cassert>
#include <string>
#include <vector>

namespace
{
std::string Digest(char value)
{
    return std::string("sha256:") + std::string(64, value);
}

ModuleArtifactIdentity Identity(const std::string& module, char digest)
{
    ModuleArtifactIdentity identity;
    identity.moduleId = module;
    identity.version = "1.0.0";
    identity.artifactDigest = Digest(digest);
    identity.configDigest = Digest('c');
    identity.modelDigest = Digest('m');
    return identity;
}

ModuleHealthEvidence Health(std::uint64_t observedAtMs)
{
    ModuleHealthEvidence health;
    health.healthy = true;
    health.observedAtMs = observedAtMs;
    health.evidenceDigest = Digest('h');
    return health;
}

ModuleLifecycleSnapshot Activate(ModuleLifecycleRegistry& registry,
                                 const std::string& module,
                                 char digest)
{
    ModuleLifecycleResult registered = registry.Register(
        Identity(module, digest), 1000);
    assert(registered.accepted);
    ModuleLifecycleResult warming = registry.Transition(
        module, registered.snapshot.generation,
        ModuleLifecycleState::Warming, ModuleHealthEvidence(), 1100);
    assert(warming.accepted);
    ModuleLifecycleResult shadow = registry.Transition(
        module, warming.snapshot.generation,
        ModuleLifecycleState::Shadow, Health(1150), 1200);
    assert(shadow.accepted);
    ModuleLifecycleResult active = registry.Transition(
        module, shadow.snapshot.generation,
        ModuleLifecycleState::Active, Health(1250), 1300);
    assert(active.accepted);
    return active.snapshot;
}

StrategyProposal Proposal(const std::string& module,
                          const std::string& proposalId,
                          DecisionMicrounits utility,
                          DecisionMicrounits target)
{
    StrategyProposal proposal;
    proposal.proposalId = proposalId;
    proposal.moduleId = module;
    proposal.moduleVersion = "1.0.0";
    proposal.sequence = 1;
    proposal.capitalPool = "pool-a";
    proposal.accountBook = "book-a";
    proposal.snapshotDigest = Digest('s');
    proposal.validFromMs = 1400;
    proposal.expiresAtMs = 2000;
    proposal.horizonMs = 500;
    StrategyProposalCandidate candidate;
    candidate.candidateId = "candidate-a";
    candidate.utility = utility;
    candidate.targets.push_back({"EUR.USD", target});
    proposal.candidates.push_back(candidate);
    return proposal;
}

GlobalAllocationPolicy AllocationPolicy()
{
    GlobalAllocationPolicy policy;
    policy.maximumGrossTarget = 10000000;
    policy.maximumInstruments = 4;
    policy.maximumExactCombinations = 100;
    policy.instrumentAbsoluteLimits["EUR.USD"] = 10000000;
    return policy;
}

AuthoritativePortfolioInput Authoritative()
{
    AuthoritativePortfolioInput input;
    input.complete = true;
    input.generation = 7;
    input.currentPositions["EUR.USD"] = 1000000;
    return input;
}

PortfolioCapitalPolicy ExecutionPolicy()
{
    PortfolioCapitalPolicy policy;
    policy.maximumGrossTarget = 10000000;
    policy.maximumStrategies = 1;
    policy.maximumInstruments = 4;
    StrategyCapitalBudget budget;
    budget.strategyId = "global-allocation";
    budget.maximumGrossTarget = 10000000;
    policy.strategyBudgets[budget.strategyId] = budget;
    return policy;
}

void TestActiveCycleAndIgnoredShadow()
{
    ModuleLifecycleRegistry lifecycle;
    Activate(lifecycle, "hepta.strategy.alpha", 'a');
    Activate(lifecycle, "hepta.strategy.beta", 'b');
    ModuleLifecycleResult gamma = lifecycle.Register(
        Identity("hepta.strategy.gamma", 'g'), 1000);
    assert(gamma.accepted);
    gamma = lifecycle.Transition(
        gamma.snapshot.identity.moduleId, gamma.snapshot.generation,
        ModuleLifecycleState::Warming, ModuleHealthEvidence(), 1100);
    gamma = lifecycle.Transition(
        gamma.snapshot.identity.moduleId, gamma.snapshot.generation,
        ModuleLifecycleState::Shadow, Health(1150), 1200);
    assert(gamma.accepted);

    std::vector<StrategyProposal> proposals;
    proposals.push_back(Proposal(
        "hepta.strategy.beta", "proposal-beta", 15, 4000000));
    proposals.push_back(Proposal(
        "hepta.strategy.alpha", "proposal-alpha", 20, 4000000));
    proposals.push_back(Proposal(
        "hepta.strategy.gamma", "proposal-gamma", 100, 10000000));
    MultiAgentSimulationResult cycle =
        MultiAgentAllocationSimulator::RunCycle(
            lifecycle, proposals, AllocationPolicy(), 1, 1500, 1800,
            Digest('s'), Authoritative(), ExecutionPolicy());
    assert(cycle.accepted);
    assert(cycle.reasonCode == "SIMULATOR_MULTI_AGENT_CYCLE_ACCEPTED");
    assert(cycle.ignoredModules.size() == 1);
    assert(cycle.ignoredModules[0] == "hepta.strategy.gamma");
    assert(cycle.plan.solver.status == "optimal");
    assert(cycle.plan.targets.size() == 1);
    assert(cycle.plan.targets[0].targetPosition == 8000000);
    assert(cycle.revalidation.compiled.deltas[0].delta == 7000000);
}

void TestQuarantineFaultIsolation()
{
    ModuleLifecycleRegistry lifecycle;
    Activate(lifecycle, "hepta.strategy.alpha", 'a');
    ModuleLifecycleSnapshot beta = Activate(
        lifecycle, "hepta.strategy.beta", 'b');
    assert(lifecycle.Quarantine(
        beta.identity.moduleId, beta.generation,
        "MODULE_RUNTIME_FAULT", 1400).accepted);

    std::vector<StrategyProposal> proposals;
    proposals.push_back(Proposal(
        "hepta.strategy.alpha", "proposal-alpha", 20, 4000000));
    proposals.push_back(Proposal(
        "hepta.strategy.beta", "proposal-beta", 100, 10000000));
    MultiAgentSimulationResult cycle =
        MultiAgentAllocationSimulator::RunCycle(
            lifecycle, proposals, AllocationPolicy(), 2, 1500, 1800,
            Digest('s'), Authoritative(), ExecutionPolicy());
    assert(cycle.accepted);
    assert(cycle.plan.targets[0].targetPosition == 4000000);
    assert(cycle.ignoredModules.size() == 1);
    assert(cycle.ignoredModules[0] == "hepta.strategy.beta");
}

void TestMissingActiveProposalAndSnapshotFailure()
{
    ModuleLifecycleRegistry lifecycle;
    Activate(lifecycle, "hepta.strategy.alpha", 'a');
    Activate(lifecycle, "hepta.strategy.beta", 'b');
    std::vector<StrategyProposal> proposals;
    proposals.push_back(Proposal(
        "hepta.strategy.alpha", "proposal-alpha", 20, 4000000));
    MultiAgentSimulationResult missing =
        MultiAgentAllocationSimulator::RunCycle(
            lifecycle, proposals, AllocationPolicy(), 1, 1500, 1800,
            Digest('s'), Authoritative(), ExecutionPolicy());
    assert(!missing.accepted);
    assert(missing.reasonCode == "PROPOSAL_SET_INCOMPLETE");

    ModuleLifecycleRegistry one;
    Activate(one, "hepta.strategy.alpha", 'a');
    MultiAgentSimulationResult mismatch =
        MultiAgentAllocationSimulator::RunCycle(
            one, proposals, AllocationPolicy(), 1, 1500, 1800,
            Digest('x'), Authoritative(), ExecutionPolicy());
    assert(!mismatch.accepted);
    assert(mismatch.reasonCode == "ALLOCATION_PLAN_SNAPSHOT_MISMATCH");
}

void TestNoActiveModules()
{
    ModuleLifecycleRegistry lifecycle;
    std::vector<StrategyProposal> proposals;
    MultiAgentSimulationResult result =
        MultiAgentAllocationSimulator::RunCycle(
            lifecycle, proposals, AllocationPolicy(), 1, 1500, 1800,
            Digest('s'), Authoritative(), ExecutionPolicy());
    assert(result.reasonCode == "SIMULATOR_NO_ACTIVE_STRATEGIES");
}
}

int main()
{
    TestActiveCycleAndIgnoredShadow();
    TestQuarantineFaultIsolation();
    TestMissingActiveProposalAndSnapshotFailure();
    TestNoActiveModules();
    return 0;
}
