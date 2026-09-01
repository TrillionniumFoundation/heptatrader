#pragma once

#include <cstdint>
#include <string>
#include <vector>

using DecisionMicrounits = std::int64_t;

struct StrategyCandidateTarget
{
    std::string instrument;
    DecisionMicrounits targetPosition = 0;
};

struct StrategyProposalCandidate
{
    std::string candidateId;
    DecisionMicrounits utility = 0;
    std::vector<StrategyCandidateTarget> targets;
};

struct StrategyProposal
{
    std::string proposalId;
    std::string moduleId;
    std::string moduleVersion;
    std::uint64_t sequence = 0;
    std::string capitalPool;
    std::string accountBook;
    std::string snapshotDigest;
    std::uint64_t validFromMs = 0;
    std::uint64_t expiresAtMs = 0;
    std::uint64_t horizonMs = 0;
    std::vector<StrategyProposalCandidate> candidates;
    std::string proposalDigest;
};

struct StrategyProposalSealResult
{
    bool accepted = false;
    std::string reasonCode;
    StrategyProposal proposal;
};

class StrategyProposalContract
{
public:
    static const char* Version();
    static StrategyProposalSealResult ValidateAndSeal(
        const StrategyProposal& proposal,
        std::uint64_t nowMs);
    static std::string Digest(const StrategyProposal& proposal);
};
