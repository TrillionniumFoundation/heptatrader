#include "pre_trade_risk_engine.h"

#include <cmath>

namespace {

bool IsFinite(double value)
{
    return std::isfinite(value);
}

bool IsSupportedAction(const std::string& action)
{
    return action == "BUY" || action == "SELL";
}

bool IsSupportedOrderType(const std::string& orderType)
{
    return orderType == "LMT" || orderType == "MKT";
}

PreTradeRiskDecision Allow()
{
    PreTradeRiskDecision decision;
    decision.allow = true;
    decision.reasonCode = "RISK_OK";
    return decision;
}

PreTradeRiskDecision Reject(const char* code, const std::string& detail)
{
    PreTradeRiskDecision decision;
    decision.allow = false;
    decision.reasonCode = code ? code : "RISK_REJECTED";
    decision.detail = detail;
    return decision;
}

} // namespace

PreTradeRiskDecision PreTradeRiskEngine::Evaluate(
    const PreTradeRiskConfig& cfg,
    const PreTradeRiskContext& ctx)
{
    if (!IsFinite(cfg.maxOrderQuantity) || cfg.maxOrderQuantity <= 0.0 ||
        cfg.maxDailyOrders < 1 ||
        !IsFinite(cfg.maxPriceDeviationBps) ||
        cfg.maxPriceDeviationBps < 0.0)
    {
        return Reject("RISK_CONFIG_INVALID",
                      "risk limits must be finite and strictly bounded");
    }

    if (cfg.globalKillSwitch)
    {
        return Reject("RISK_GLOBAL_KILL_SWITCH_ON",
                      "global kill switch enabled");
    }

    if (!cfg.enableOrderSubmission)
    {
        return Reject("RISK_ORDER_SUBMISSION_DISABLED",
                      "order submission gate is closed");
    }

    if (!IsSupportedAction(ctx.action))
    {
        return Reject("RISK_ACTION_INVALID", "unsupported order action");
    }

    if (!IsSupportedOrderType(ctx.orderType))
    {
        return Reject("RISK_ORDER_TYPE_INVALID", "unsupported order type");
    }

    if (!IsFinite(ctx.totalQuantity) || ctx.totalQuantity <= 0.0 ||
        ctx.totalQuantity > cfg.maxOrderQuantity)
    {
        return Reject("RISK_QTY_OUT_OF_RANGE",
                      "quantity must be finite, positive, and within the configured maximum");
    }

    if (ctx.todayOrderCount < 0)
    {
        return Reject("RISK_CONTEXT_INVALID",
                      "daily order count cannot be negative");
    }

    if (ctx.todayOrderCount >= cfg.maxDailyOrders)
    {
        return Reject("RISK_DAILY_ORDER_LIMIT", "daily order limit reached");
    }

    if (!ctx.accountWhitelisted)
    {
        return Reject("RISK_ACCOUNT_NOT_WHITELISTED",
                      "account is not in whitelist");
    }

    if (!ctx.paperAccount)
    {
        if (!cfg.allowLiveTrading)
        {
            return Reject("RISK_LIVE_NOT_AUTHORIZED",
                          "live trading is not explicitly authorized");
        }
        if (cfg.liveKillSwitch)
        {
            return Reject("RISK_LIVE_KILL_SWITCH_ON",
                          "live kill switch is ON");
        }
    }

    if (!IsFinite(ctx.limitPrice) || !IsFinite(ctx.referencePrice))
    {
        return Reject("RISK_PRICE_NOT_FINITE",
                      "price inputs must be finite");
    }

    if (ctx.positionKnown && !IsFinite(ctx.netPosition))
    {
        return Reject("RISK_POSITION_NOT_FINITE",
                      "known position must be finite");
    }

    if (cfg.flattenOnly)
    {
        if (!ctx.positionKnown)
        {
            return Reject("RISK_FLATTEN_ONLY_POSITION_UNKNOWN",
                          "flatten-only mode requires a known position");
        }
        if (!IsFlatteningOrder(ctx))
        {
            return Reject("RISK_FLATTEN_ONLY_BLOCK",
                          "order must reduce exposure without crossing through zero");
        }
    }

    if (ctx.orderType == "LMT")
    {
        if (ctx.limitPrice <= 0.0)
        {
            return Reject("RISK_LIMIT_PRICE_INVALID",
                          "limit orders require a finite positive limit price");
        }

        if (cfg.maxPriceDeviationBps > 0.0)
        {
            if (ctx.referencePrice <= 0.0)
            {
                return Reject("RISK_REFERENCE_PRICE_INVALID",
                              "price-collar enforcement requires a finite positive reference price");
            }

            const double relativeDeviation =
                std::abs(ctx.limitPrice / ctx.referencePrice - 1.0);
            const double deviationBps = relativeDeviation * 10000.0;
            if (!IsFinite(relativeDeviation) || !IsFinite(deviationBps))
            {
                return Reject("RISK_PRICE_DEVIATION_INVALID",
                              "price deviation calculation overflowed");
            }
            if (deviationBps > cfg.maxPriceDeviationBps)
            {
                return Reject("RISK_PRICE_DEVIATION_TOO_LARGE",
                              "limit price deviation exceeds maxPriceDeviationBps");
            }
        }
    }

    return Allow();
}

bool PreTradeRiskEngine::IsFlatteningOrder(const PreTradeRiskContext& ctx)
{
    if (!ctx.positionKnown || !IsFinite(ctx.netPosition) ||
        !IsFinite(ctx.totalQuantity) || ctx.totalQuantity <= 0.0)
    {
        return false;
    }

    if (ctx.netPosition > 0.0)
    {
        return ctx.action == "SELL" && ctx.totalQuantity <= ctx.netPosition;
    }
    if (ctx.netPosition < 0.0)
    {
        return ctx.action == "BUY" && ctx.totalQuantity <= -ctx.netPosition;
    }
    return false;
}
