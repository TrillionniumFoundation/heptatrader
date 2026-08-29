#pragma once

#include <cstddef>
#include <string>

// Venue-independent, deterministic limits shared by Simulator and broker
// profiles. A venue may add stricter order-shape and transport rules, but it
// must not silently weaken these controls.
struct DeterministicRiskLimits
{
    bool orderSubmissionEnabled = true;
    bool globalKillSwitch = false;
    bool flattenOnly = false;

    double maxOrderQuantity = 25000.0;
    double maxOrderNotional = 250000.0;
    std::size_t maxOrdersPerMinute = 30;
    std::size_t maxActiveOrders = 50;
    double maxGrossPosition = 100000.0;
    double maxPriceDeviationBps = 30.0;

    // Optional portfolio/strategy controls. Zero disables that specific cap.
    double maxNetPosition = 0.0;
    double maxStrategyGrossPosition = 0.0;
    double maxDailyLoss = 0.0;
    double maxDrawdown = 0.0;

    bool requireFreshQuote = true;
    bool requireCompleteSnapshot = true;
};

struct DeterministicRiskContext
{
    std::string action;      // BUY / SELL
    std::string orderType;   // MKT / LMT

    double quantity = 0.0;
    // Conservative price used for notional valuation.
    double valuationPrice = 0.0;
    // Submitted limit price and authoritative reference price. For MKT the
    // submitted price may be zero and the deviation check is skipped.
    double submittedPrice = 0.0;
    double referencePrice = 0.0;

    std::size_t ordersInLastMinute = 0;
    std::size_t activeOrderCount = 0;
    double grossAbsolutePosition = 0.0;
    double projectedGrossAbsolutePosition = 0.0;

    // This is a claim made by trusted portfolio/execution code. The policy
    // independently verifies it using quantity and gross projection so a
    // crossing-through-zero order cannot masquerade as reduce-only.
    bool exposureReducing = false;

    bool quoteFresh = true;
    bool portfolioSnapshotComplete = true;

    // Optional portfolio and strategy projections. They are evaluated when
    // the corresponding limit is non-zero.
    double netPosition = 0.0;
    double projectedNetPosition = 0.0;
    double strategyGrossPosition = 0.0;
    double projectedStrategyGrossPosition = 0.0;
    double dailyPnl = 0.0;
    double drawdown = 0.0;
};

struct DeterministicRiskDecision
{
    bool allow = false;
    std::string reasonCode;
    std::string detail;
};

class DeterministicRiskPolicy
{
public:
    static const char* Version();

    static bool ValidateLimits(
        const DeterministicRiskLimits& limits,
        std::string& reason);

    static DeterministicRiskDecision Evaluate(
        const DeterministicRiskLimits& limits,
        const DeterministicRiskContext& context);
};
