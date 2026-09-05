#pragma once

#include "../proposal/proposal_set.h"

#include <cstddef>
#include <cstdint>
#include <map>
#include <string>
#include <vector>

struct AllocationTarget { std::string instrument; DecisionMicrounits targetPosition = 0; };

struct GlobalAllocationPolicy
{
    std::string policyRevision;
    DecisionMicrounits maximumGrossTarget = 0;
    std::size_t maximumInstruments = 0;
    std::uint64_t maximumExactCombinations = 0;
    std::map<std::string, DecisionMicrounits> instrumentAbsoluteLimits;
};

struct AllocationSolverResult
{
    std::string status;
    DecisionMicrounits objective = 0;
    DecisionMicrounits primalBound = 0;
    DecisionMicrounits upperBound = 0;
    DecisionMicrounits absoluteGap = 0;
    std::uint64_t combinationsExplored = 0;
    bool exact = false;
    std::string digest;
};

struct AllocationPlan
{
    std::string planId;
    std::uint64_t allocatorEpoch = 0;
    std::string capitalPool;
    std::string accountBook;
    std::string policyRevision;
    std::string proposalSetDigest;
    std::string snapshotDigest;
    std::uint64_t proposalCapturedAtMs = 0;
    std::uint64_t proposalValidUntilMs = 0;
    std::uint64_t snapshotValidUntilMs = 0;
    AllocationSolverResult solver;
    std::vector<AllocationTarget> targets;
    std::vector<std::string> acceptedCandidates;
    std::vector<std::string> rejectedProposals;
    std::uint64_t createdAtMs = 0;
    std::uint64_t validUntilMs = 0;
    std::string planDigest;
};

class GlobalAllocator;

class GlobalDecisionReceipt
{
public:
    GlobalDecisionReceipt() noexcept : m_valid(false) {}
    bool IsValid() const noexcept { return m_valid; }
    const AllocationPlan& Plan() const noexcept { return m_plan; }
private:
    explicit GlobalDecisionReceipt(const AllocationPlan& plan)
        : m_plan(plan), m_valid(true) {}
    AllocationPlan m_plan;
    bool m_valid;
    friend class GlobalAllocator;
};

struct GlobalAllocationResult
{
    bool accepted = false;
    std::string reasonCode;
    AllocationPlan plan;
    GlobalDecisionReceipt receipt;
};

class GlobalAllocator
{
public:
    static const char* Version();
    static GlobalAllocationResult Allocate(
        const ProposalSet& proposalSet,
        const GlobalAllocationPolicy& policy,
        std::uint64_t allocatorEpoch,
        std::uint64_t createdAtMs);
    static std::string SolverDigest(const AllocationSolverResult& solver);
    static std::string PlanDigest(const AllocationPlan& plan);
};
