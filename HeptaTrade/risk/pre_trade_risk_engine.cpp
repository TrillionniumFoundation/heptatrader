#include "pre_trade_risk_engine.h"

#include <cmath>
#include <cstdlib>

namespace {
PreTradeRiskDecision Allow() {
    PreTradeRiskDecision d;
    d.allow = true;
    d.reasonCode = "RISK_OK";
    d.detail = "";
    return d;
}

PreTradeRiskDecision Reject(const char* code, const std::string& detail) {
    PreTradeRiskDecision d;
    d.allow = false;
    d.reasonCode = code ? code : "RISK_REJECTED";
    d.detail = detail;
    return d;
}
}

PreTradeRiskDecision PreTradeRiskEngine::Evaluate(const PreTradeRiskConfig& cfg, const PreTradeRiskContext& ctx) {
    if (cfg.globalKillSwitch) {
        return Reject("RISK_GLOBAL_KILL_SWITCH_ON", "global kill switch enabled");
    }

    if (!cfg.enableOrderSubmission) {
        return Reject("RISK_ORDER_SUBMISSION_DISABLED", "order submission gate is closed");
    }

    if (ctx.totalQuantity <= 0.0 || ctx.totalQuantity > cfg.maxOrderQuantity) {
        return Reject("RISK_QTY_OUT_OF_RANGE", "qty invalid or exceeds maxOrderQuantity");
    }

    if (ctx.todayOrderCount >= cfg.maxDailyOrders) {
        return Reject("RISK_DAILY_ORDER_LIMIT", "daily order limit reached");
    }

    if (!ctx.accountWhitelisted) {
        return Reject("RISK_ACCOUNT_NOT_WHITELISTED", "account is not in whitelist");
    }

    if (!ctx.paperAccount) {
        if (!cfg.allowLiveTrading) {
            return Reject("RISK_LIVE_NOT_AUTHORIZED", "live trading is not explicitly authorized");
        }
        if (cfg.liveKillSwitch) {
            return Reject("RISK_LIVE_KILL_SWITCH_ON", "live kill switch is ON");
        }
    }

    if (cfg.flattenOnly) {
        if (!ctx.positionKnown) {
            return Reject("RISK_FLATTEN_ONLY_POSITION_UNKNOWN", "flatten-only mode requires known position");
        }
        if (!IsFlatteningOrder(ctx)) {
            return Reject("RISK_FLATTEN_ONLY_BLOCK", "order is not reducing current exposure");
        }
    }

    if (cfg.maxPriceDeviationBps > 0.0 && ctx.orderType == "LMT" && ctx.limitPrice > 0.0 && ctx.referencePrice > 0.0) {
        const double devBps = std::abs(ctx.limitPrice - ctx.referencePrice) / ctx.referencePrice * 10000.0;
        if (devBps > cfg.maxPriceDeviationBps) {
            return Reject("RISK_PRICE_DEVIATION_TOO_LARGE", "limit price deviation exceeds maxPriceDeviationBps");
        }
    }

    return Allow();
}

bool PreTradeRiskEngine::IsFlatteningOrder(const PreTradeRiskContext& ctx) {
    double signedQty = 0.0;
    if (ctx.action == "BUY") signedQty = ctx.totalQuantity;
    else if (ctx.action == "SELL") signedQty = -ctx.totalQuantity;
    else return false;

    const double currentAbs = std::abs(ctx.netPosition);
    const double afterAbs = std::abs(ctx.netPosition + signedQty);
    return afterAbs < currentAbs;
}
