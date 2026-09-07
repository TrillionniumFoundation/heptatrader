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
    // limit, but all supplied context values must still be finite/non-negative.
    double maxOrderNotional = 0.0;
    double maxWorstCaseGrossNotional = 0.0;
    double maxDailyLoss = 0.0;
    double maxDrawdown = 0.0;
    std::int64_t maxSnapshotAgeMs = 0;

    bool allowLiveTrading = false;
    bool liveKillSwitch = true;
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

    // Authoritative snapshot identity/freshness. The default complete value
    // preserves the legacy caller contract when freshness enforcement is off.
    bool snapshotComplete = true;
    std::int64_t snapshotObservedAtMs = 0;
    std::int64_t nowMs = 0;

    // All notional and PnL values below use one declared account base currency.
    // baseCurrencyOrderNotional may be supplied by a contract-aware caller; if
    // zero, the engine derives quantity * reference/limit price when required.
    double baseCurrencyOrderNotional = 0.0;
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
};

class PreTradeRiskEngine {
public:
    static PreTradeRiskDecision Evaluate(const PreTradeRiskConfig& cfg,
                                         const PreTradeRiskContext& ctx);

private:
    static bool IsFlatteningOrder(const PreTradeRiskContext& ctx);
};
