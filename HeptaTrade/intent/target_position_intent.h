#pragma once

#include <cstdint>
#include <string>

struct TargetPositionDecisionSnapshot
{
    std::string agentId;
    std::string sessionId;
    std::string account;
    std::string executionDomain;
    std::string executionServiceEpoch;
    std::uint64_t fencingGeneration = 0;
    std::uint64_t collectionWatermark = 0;
    std::uint64_t eventWatermark = 0;
    std::uint64_t snapshotWatermark = 0;
    std::string instrument;
    std::int64_t collectionStartedAtMs = 0;
    std::int64_t collectionCompletedAtMs = 0;
    std::int64_t quoteObservedAtMs = 0;
    double bid = 0.0;
    double ask = 0.0;
    double currentPosition = 0.0;
};

struct TargetPositionIntentRequest
{
    double targetPosition = 0.0;
    double maxSlippageBps = 0.0;
    std::int64_t expiresAtMs = 0;
};

struct TargetPositionIntentPolicy
{
    std::string version = "target-position-intent-v1";
    double maxOrderQuantity = 1.0;
    double maxAbsoluteTargetPosition = 1.0;
    double maxSlippageBps = 25.0;
    std::int64_t maxIntentLifetimeMs = 60000;
};

struct TargetPositionExecutionPlan
{
    bool noOp = false;
    std::string side;
    std::string orderType;
    std::string timeInForce;
    double quantity = 0.0;
    double referencePrice = 0.0;
    double limitPrice = 0.0;
    std::string previewPermit;
};

class TargetPositionIntentContract
{
public:
    static bool BuildPlan(
        const TargetPositionDecisionSnapshot& snapshot,
        const TargetPositionIntentRequest& request,
        const TargetPositionIntentPolicy& policy,
        std::int64_t nowMs,
        TargetPositionExecutionPlan& plan,
        std::string& reasonCode,
        std::string& detail);

    static std::string PermitDigest(
        const TargetPositionDecisionSnapshot& snapshot,
        const TargetPositionIntentRequest& request,
        const TargetPositionIntentPolicy& policy,
        const TargetPositionExecutionPlan& plan);

    static bool PermitMatches(
        const std::string& permit,
        const TargetPositionDecisionSnapshot& snapshot,
        const TargetPositionIntentRequest& request,
        const TargetPositionIntentPolicy& policy,
        const TargetPositionExecutionPlan& plan);
};
