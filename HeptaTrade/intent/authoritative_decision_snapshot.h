#pragma once

#include "target_position_intent.h"

#include <cstdint>
#include <string>

struct AuthoritativeDecisionSnapshotPayloads
{
    std::string healthBefore;
    std::string healthAfter;
    std::string quote;
    std::string account;
    std::string positions;
    std::string orders;
    std::string riskLimits;
};

class AuthoritativeDecisionSnapshotCodec
{
public:
    static bool Build(
        const std::string& agentId,
        const std::string& sessionId,
        const std::string& account,
        const std::string& executionDomain,
        const std::string& instrument,
        std::int64_t collectionStartedAtMs,
        std::int64_t collectionCompletedAtMs,
        std::uint64_t collectionWatermark,
        const AuthoritativeDecisionSnapshotPayloads& payloads,
        TargetPositionDecisionSnapshot& snapshot,
        std::string& outputJson,
        std::string& reasonCode,
        std::string& detail);
};
