#pragma once

#include <cstddef>
#include <string>

// Venue-independent, deterministic limits shared by Simulator and broker
// profiles.  A venue may add stricter order-shape and transport rules, but it
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
};

struct DeterministicRiskContext
{
    std::string action;      // BUY / SELL
    std::string orderType;   // MKT / LMT

    double quantity = 0.0;
    // Conservative price used for notional valuation.
    double valuationPrice = 0.0;
    // Submitted limit price and authoritative reference price.  For MKT the
    // submitted price may be zero and the deviation check is skipped.
    double submittedPrice = 0.0;
    double referencePrice = 0.0;

    std::size_t ordersInLastMinute = 0;
    std::size_t activeOrderCount = 0;
    double grossAbsolutePosition = 0.0;
    double projectedGrossAbsolutePosition = 0.0;
    bool exposureReducing = false;
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
    static bool ValidateLimits(
        const DeterministicRiskLimits& limits,
        std::string& reason);

    static DeterministicRiskDecision Evaluate(
        const DeterministicRiskLimits& limits,
        const DeterministicRiskContext& context);
};
