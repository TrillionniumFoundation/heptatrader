#include "pre_trade_risk_engine.h"

#include <cmath>
#include <limits>

namespace {
PreTradeRiskDecision Allow(double orderNotional = 0.0,
                           double worstCaseGrossNotional = 0.0) {
    PreTradeRiskDecision d;
    d.allow = true;
    d.reasonCode = "RISK_OK";
    d.orderNotional = orderNotional;
    d.worstCaseGrossNotional = worstCaseGrossNotional;
    return d;
}

PreTradeRiskDecision Reject(const char* code, const std::string& detail,
                            double orderNotional = 0.0,
                            double worstCaseGrossNotional = 0.0) {
    PreTradeRiskDecision d;
    d.allow = false;
    d.reasonCode = code ? code : "RISK_REJECTED";
    d.detail = detail;
    d.orderNotional = orderNotional;
    d.worstCaseGrossNotional = worstCaseGrossNotional;
    return d;
}

bool FiniteNonNegative(double value) {
    return std::isfinite(value) && value >= 0.0;
}

bool LimitEnabled(double value) {
    return std::isfinite(value) && value > 0.0;
}

bool ExceedsInclusiveLimit(double value, double limit) {
    return value > limit;
}
}

PreTradeRiskDecision PreTradeRiskEngine::Evaluate(
    const PreTradeRiskConfig& cfg, const PreTradeRiskContext& ctx) {
    if (cfg.globalKillSwitch) {
        return Reject("RISK_GLOBAL_KILL_SWITCH_ON", "global kill switch enabled");
    }

    if (!cfg.enableOrderSubmission) {
        return Reject("RISK_ORDER_SUBMISSION_DISABLED", "order submission gate is closed");
    }

    if (!std::isfinite(ctx.totalQuantity) || ctx.totalQuantity <= 0.0 ||
        !std::isfinite(cfg.maxOrderQuantity) || cfg.maxOrderQuantity <= 0.0 ||
        ctx.totalQuantity > cfg.maxOrderQuantity) {
        return Reject("RISK_QTY_OUT_OF_RANGE", "qty invalid or exceeds maxOrderQuantity");
    }

    if (ctx.todayOrderCount < 0 || cfg.maxDailyOrders <= 0 ||
        ctx.todayOrderCount >= cfg.maxDailyOrders) {
        return Reject("RISK_DAILY_ORDER_LIMIT", "daily order limit reached or invalid");
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

    if (!ctx.snapshotComplete) {
        return Reject("RISK_SNAPSHOT_INCOMPLETE", "authoritative risk snapshot is incomplete");
    }
    if (cfg.maxSnapshotAgeMs < 0) {
        return Reject("RISK_SNAPSHOT_POLICY_INVALID", "maxSnapshotAgeMs must be non-negative");
    }
    if (cfg.maxSnapshotAgeMs > 0) {
        if (ctx.nowMs <= 0 || ctx.snapshotObservedAtMs <= 0 ||
            ctx.nowMs < ctx.snapshotObservedAtMs ||
            ctx.nowMs - ctx.snapshotObservedAtMs > cfg.maxSnapshotAgeMs) {
            return Reject("RISK_SNAPSHOT_STALE", "authoritative risk snapshot is missing or stale");
        }
    }

    bool reducingExposure = false;
    if (cfg.flattenOnly) {
        if (!ctx.positionKnown || !std::isfinite(ctx.netPosition)) {
            return Reject("RISK_FLATTEN_ONLY_POSITION_UNKNOWN", "flatten-only mode requires known finite position");
        }
        reducingExposure = IsFlatteningOrder(ctx);
        if (!reducingExposure) {
            return Reject("RISK_FLATTEN_ONLY_BLOCK", "order is not reducing current exposure");
        }
    }

    if (!std::isfinite(ctx.limitPrice) || !std::isfinite(ctx.referencePrice) ||
        ctx.limitPrice < 0.0 || ctx.referencePrice < 0.0) {
        return Reject("RISK_PRICE_INVALID", "price inputs must be finite and non-negative");
    }

    if (cfg.maxPriceDeviationBps < 0.0 || !std::isfinite(cfg.maxPriceDeviationBps)) {
        return Reject("RISK_PRICE_POLICY_INVALID", "maxPriceDeviationBps must be finite and non-negative");
    }
    if (cfg.maxPriceDeviationBps > 0.0 && ctx.orderType == "LMT") {
        if (ctx.limitPrice <= 0.0 || ctx.referencePrice <= 0.0) {
            return Reject("RISK_REFERENCE_PRICE_REQUIRED", "LMT deviation check requires positive prices");
        }
        const double devBps = std::abs(ctx.limitPrice - ctx.referencePrice) /
            ctx.referencePrice * 10000.0;
        if (!std::isfinite(devBps) || devBps > cfg.maxPriceDeviationBps) {
            return Reject("RISK_PRICE_DEVIATION_TOO_LARGE", "limit price deviation exceeds maxPriceDeviationBps");
        }
    }

    if (!FiniteNonNegative(ctx.baseCurrencyOrderNotional) ||
        !FiniteNonNegative(ctx.currentGrossNotional) ||
        !FiniteNonNegative(ctx.pendingBuyNotional) ||
        !FiniteNonNegative(ctx.pendingSellNotional) ||
        !std::isfinite(ctx.realizedPnl) ||
        !std::isfinite(ctx.unrealizedPnl) ||
        !FiniteNonNegative(ctx.peakEquity) ||
        !FiniteNonNegative(ctx.currentEquity)) {
        return Reject("RISK_NUMERIC_CONTEXT_INVALID", "notional, PnL, or equity context is invalid");
    }

    for (double limit : {
            cfg.maxOrderNotional,
            cfg.maxWorstCaseGrossNotional,
            cfg.maxDailyLoss,
            cfg.maxDrawdown}) {
        if (!std::isfinite(limit) || limit < 0.0) {
            return Reject("RISK_LIMIT_POLICY_INVALID", "optional risk limits must be finite and non-negative");
        }
    }

    double orderNotional = ctx.baseCurrencyOrderNotional;
    if (orderNotional == 0.0 &&
        (LimitEnabled(cfg.maxOrderNotional) ||
         LimitEnabled(cfg.maxWorstCaseGrossNotional))) {
        const double price = ctx.referencePrice > 0.0 ?
            ctx.referencePrice : ctx.limitPrice;
        if (!std::isfinite(price) || price <= 0.0) {
            return Reject("RISK_ORDER_NOTIONAL_UNAVAILABLE", "base-currency order notional requires a positive authoritative price");
        }
        orderNotional = ctx.totalQuantity * price;
        if (!std::isfinite(orderNotional) || orderNotional <= 0.0) {
            return Reject("RISK_ORDER_NOTIONAL_UNAVAILABLE", "calculated order notional is invalid");
        }
    }

    const double worstCaseGrossNotional = ctx.currentGrossNotional +
        ctx.pendingBuyNotional + ctx.pendingSellNotional + orderNotional;
    if (!std::isfinite(worstCaseGrossNotional)) {
        return Reject("RISK_WORST_CASE_GROSS_INVALID", "worst-case gross notional overflowed");
    }

    // A verified flatten-only order is an exit path. Portfolio loss/gross
    // breaches may force that mode and therefore must not block exposure
    // reduction. Per-order quantity and price validity still apply above.
    if (!reducingExposure) {
        if (LimitEnabled(cfg.maxOrderNotional) &&
            ExceedsInclusiveLimit(orderNotional, cfg.maxOrderNotional)) {
            return Reject("RISK_ORDER_NOTIONAL_LIMIT", "order notional exceeds maxOrderNotional",
                          orderNotional, worstCaseGrossNotional);
        }
        if (LimitEnabled(cfg.maxWorstCaseGrossNotional) &&
            ExceedsInclusiveLimit(worstCaseGrossNotional,
                                  cfg.maxWorstCaseGrossNotional)) {
            return Reject("RISK_WORST_CASE_GROSS_LIMIT", "current plus pending plus candidate exposure exceeds maxWorstCaseGrossNotional",
                          orderNotional, worstCaseGrossNotional);
        }

        const double totalPnl = ctx.realizedPnl + ctx.unrealizedPnl;
        const double dailyLoss = totalPnl < 0.0 ? -totalPnl : 0.0;
        if (!std::isfinite(dailyLoss)) {
            return Reject("RISK_DAILY_LOSS_INVALID", "daily loss calculation is invalid",
                          orderNotional, worstCaseGrossNotional);
        }
        if (LimitEnabled(cfg.maxDailyLoss) &&
            ExceedsInclusiveLimit(dailyLoss, cfg.maxDailyLoss)) {
            return Reject("RISK_DAILY_LOSS_LIMIT", "daily loss exceeds maxDailyLoss",
                          orderNotional, worstCaseGrossNotional);
        }

        const double drawdown = ctx.peakEquity > ctx.currentEquity ?
            ctx.peakEquity - ctx.currentEquity : 0.0;
        if (!std::isfinite(drawdown)) {
            return Reject("RISK_DRAWDOWN_INVALID", "drawdown calculation is invalid",
                          orderNotional, worstCaseGrossNotional);
        }
        if (LimitEnabled(cfg.maxDrawdown) &&
            ExceedsInclusiveLimit(drawdown, cfg.maxDrawdown)) {
            return Reject("RISK_DRAWDOWN_LIMIT", "drawdown exceeds maxDrawdown",
                          orderNotional, worstCaseGrossNotional);
        }
    }

    return Allow(orderNotional, worstCaseGrossNotional);
}

bool PreTradeRiskEngine::IsFlatteningOrder(const PreTradeRiskContext& ctx) {
    if (!std::isfinite(ctx.totalQuantity) || !std::isfinite(ctx.netPosition)) {
        return false;
    }
    double signedQty = 0.0;
    if (ctx.action == "BUY") signedQty = ctx.totalQuantity;
    else if (ctx.action == "SELL") signedQty = -ctx.totalQuantity;
    else return false;

    const double currentAbs = std::abs(ctx.netPosition);
    const double afterAbs = std::abs(ctx.netPosition + signedQty);
    return afterAbs < currentAbs;
}
