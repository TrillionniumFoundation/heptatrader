#pragma once

#include "../proposal/proposal_set.h"

#include <cstddef>
#include <cstdint>
#include <map>
#include <string>
#include <vector>

struct AllocationTarget
{
    std::string instrument;
    DecisionMicrounits targetPosition = 0;
};

struct GlobalAllocationPolicy
{
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
    std::string proposalSetDigest;
    std::string snapshotDigest;
    AllocationSolverResult solver;
    std::vector<AllocationTarget> targets;
    std::vector<std::string> acceptedCandidates;
    std::vector<std::string> rejectedProposals;
    std::uint64_t createdAtMs = 0;
    std::uint64_t validUntilMs = 0;
    std::string planDigest;
};

struct GlobalAllocationResult
{
    bool accepted = false;
    std::string reasonCode;
    AllocationPlan plan;
};

class GlobalAllocator
{
public:
    static const char* Version();
    static GlobalAllocationResult Allocate(
        const ProposalSet& proposalSet,
        const GlobalAllocationPolicy& policy,
        std::uint64_t allocatorEpoch,
        std::uint64_t createdAtMs,
        std::uint64_t validUntilMs);
    static std::string SolverDigest(const AllocationSolverResult& solver);
    static std::string PlanDigest(const AllocationPlan& plan);
};
