#pragma once

#include <cstdint>
#include <string>

struct PreTradeRiskConfig {
    bool enableOrderSubmission = false;
    bool globalKillSwitch = false;
    bool flattenOnly = false;

    double maxOrderQuantity = 1.0;
    int maxDailyOrders = 1;
    double maxPriceDeviationBps = 30.0;

    // Base-currency limits. A zero value disables the corresponding optional
    // limit. Portfolio limits require an explicit, fresh authoritative
    // snapshot; default numeric zeroes never mean "observed zero".
    double maxOrderNotional = 0.0;
    double maxWorstCaseGrossNotional = 0.0;
    double maxDailyLoss = 0.0;
    double maxDrawdown = 0.0;
    std::int64_t maxSnapshotAgeMs = 0;

    bool allowLiveTrading = false;
    bool liveKillSwitch = true;
};

struct PreTradeRiskSnapshotIdentity {
    bool present = false;
    bool complete = false;
    std::uint64_t connectionEpoch = 0;
    std::uint64_t generation = 0;
    std::int64_t observedAtMs = 0;
    std::int64_t evaluatedAtMs = 0;
};

struct PreTradeRiskExposureSnapshot {
    bool present = false;
    std::uint64_t generation = 0;
    double currentGrossNotional = 0.0;
    double pendingBuyNotional = 0.0;
    double pendingSellNotional = 0.0;
};

struct PreTradeRiskPnlSnapshot {
    bool present = false;
    std::uint64_t generation = 0;
    double realizedPnl = 0.0;
    double unrealizedPnl = 0.0;
};

struct PreTradeRiskEquitySnapshot {
    bool present = false;
    std::uint64_t generation = 0;
    double peakEquity = 0.0;
    double currentEquity = 0.0;
};

struct PreTradeRiskAuthoritativeSnapshot {
    PreTradeRiskSnapshotIdentity identity;
    PreTradeRiskExposureSnapshot exposure;
    PreTradeRiskPnlSnapshot pnl;
    PreTradeRiskEquitySnapshot equity;
};

struct PreTradeRiskContext {
    std::string venue;      // IB / CTP / ...
    std::string account;
    std::string symbol;
    std::string action;     // BUY / SELL
    std::string orderType;  // LMT / MKT

    double totalQuantity = 0.0;
    double limitPrice = 0.0;
    double referencePrice = 0.0;

    int todayOrderCount = 0;
    bool accountWhitelisted = false;
    bool paperAccount = true;

    bool positionKnown = false;
    double netPosition = 0.0;

    // Explicit presence prevents an omitted conversion from masquerading as
    // a real zero. When absent, enabled notional limits may derive quantity *
    // authoritative price only for instruments whose caller defines that as
    // the base-currency notional contract.
    bool baseCurrencyOrderNotionalPresent = false;
    double baseCurrencyOrderNotional = 0.0;

    PreTradeRiskAuthoritativeSnapshot authoritativeSnapshot;

    // Compatibility-only fields retained for legacy callers. They are never
    // accepted as evidence for an enabled portfolio limit. New code must use
    // authoritativeSnapshot and its per-section presence/generation fields.
    bool snapshotComplete = false;
    std::int64_t snapshotObservedAtMs = 0;
    std::int64_t nowMs = 0;
    double currentGrossNotional = 0.0;
    double pendingBuyNotional = 0.0;
    double pendingSellNotional = 0.0;
    double realizedPnl = 0.0;
    double unrealizedPnl = 0.0;
    double peakEquity = 0.0;
    double currentEquity = 0.0;

    // adapter extension points (for CTP etc.)
    std::string adapterTag;
};

struct PreTradeRiskDecision {
    bool allow = false;
    std::string reasonCode; // unified RISK_XXX
    std::string detail;
    double orderNotional = 0.0;
    double worstCaseGrossNotional = 0.0;
    std::uint64_t snapshotConnectionEpoch = 0;
    std::uint64_t snapshotGeneration = 0;
};

class PreTradeRiskEngine {
public:
    static PreTradeRiskDecision Evaluate(const PreTradeRiskConfig& cfg,
                                         const PreTradeRiskContext& ctx);

private:
    static bool IsFlatteningOrder(const PreTradeRiskContext& ctx);
};
