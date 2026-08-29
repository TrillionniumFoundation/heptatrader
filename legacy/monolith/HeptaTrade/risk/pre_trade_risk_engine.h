#pragma once

#include <string>
#include <ctime>

struct PreTradeRiskConfig {
    bool enableOrderSubmission = false;
    bool globalKillSwitch = false;
    bool flattenOnly = false;

    double maxOrderQuantity = 1.0;
    int maxDailyOrders = 1;
    double maxPriceDeviationBps = 30.0;

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

    // adapter extension points (for CTP etc.)
    std::string adapterTag;
};

struct PreTradeRiskDecision {
    bool allow = false;
    std::string reasonCode; // unified RISK_XXX
    std::string detail;
};

class PreTradeRiskEngine {
public:
    static PreTradeRiskDecision Evaluate(const PreTradeRiskConfig& cfg, const PreTradeRiskContext& ctx);

private:
    static bool IsFlatteningOrder(const PreTradeRiskContext& ctx);
};
