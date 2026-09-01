#pragma once

#include "../strategy_runtime/strategy_proposal.h"

#include <cstdint>
#include <string>
#include <vector>

struct ProposalSet
{
    std::string capitalPool;
    std::string accountBook;
    std::string snapshotDigest;
    std::uint64_t capturedAtMs = 0;
    std::vector<StrategyProposal> proposals;
    std::string digest;
};

struct ProposalSetBuildResult
{
    bool accepted = false;
    std::string reasonCode;
    ProposalSet proposalSet;
};

class ProposalSetBuilder
{
public:
    static const char* Version();
    static ProposalSetBuildResult Build(
        const std::vector<StrategyProposal>& proposals,
        const std::vector<std::string>& expectedModules,
        std::uint64_t nowMs);
    static std::string Digest(const ProposalSet& proposalSet);
};
