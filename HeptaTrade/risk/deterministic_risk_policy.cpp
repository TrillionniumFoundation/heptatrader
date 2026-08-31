#include "deterministic_risk_policy.h"
#include "../observability/runtime_telemetry.h"

#include <algorithm>
#include <cmath>

namespace
{
DeterministicRiskDecision Allow()
{
    DeterministicRiskDecision decision;
    decision.allow = true;
    decision.reasonCode = "RISK_OK";
    RuntimeRecordRiskDecision(true, decision.reasonCode);
    return decision;
}

DeterministicRiskDecision Reject(const char* code, const char* detail)
{
    DeterministicRiskDecision decision;
    decision.allow = false;
    decision.reasonCode = code;
    decision.detail = detail;
    RuntimeRecordRiskDecision(false, code);
    return decision;
}

bool PositiveFinite(double value)
{
    return std::isfinite(value) && value > 0.0;
}

bool OptionalLimit(double value)
{
    return std::isfinite(value) && value >= 0.0;
}

bool NearlyEqual(double left, double right)
{
    const double scale = std::max(1.0, std::max(std::fabs(left), std::fabs(right)));
    return std::fabs(left - right) <= scale * 1e-10;
}

bool SignedPositionReduction(const DeterministicRiskContext& context)
{
    const double signedQuantity = context.action == "BUY" ?
        context.quantity : -context.quantity;
    const double expectedProjected = context.netPosition + signedQuantity;
    const double localScale = std::max(
        1.0, std::max(std::fabs(context.netPosition),
            std::max(std::fabs(context.projectedNetPosition),
                     std::fabs(context.quantity))));
    const double localTolerance = localScale * 1e-12;
    if (std::fabs(context.projectedNetPosition - expectedProjected) >
        localTolerance)
        return false;

    // The signed position of the affected instrument, rather than portfolio
    // gross, is the authority for crossing-zero semantics.  Portfolio-scale
    // floating tolerance must never turn +x -> -y (or -x -> +y) into a safe
    // reduction merely because an unrelated book has very large exposure.
    if (context.netPosition > 0.0)
        return context.action == "SELL" &&
            context.projectedNetPosition >= 0.0 &&
            context.projectedNetPosition < context.netPosition;
    if (context.netPosition < 0.0)
        return context.action == "BUY" &&
            context.projectedNetPosition <= 0.0 &&
            context.projectedNetPosition > context.netPosition;
    return false;
}

bool StrictGrossReduction(const DeterministicRiskContext& context)
{
    if (!context.exposureReducing ||
        !SignedPositionReduction(context) ||
        !(context.projectedGrossAbsolutePosition < context.grossAbsolutePosition))
        return false;

    // One normalized order changes one instrument. A true reduce-only order
    // removes exactly its quantity from gross absolute exposure. The signed
    // position proof above independently forbids crossing zero; this gross
    // equality remains a portfolio-consistency check only.
    return NearlyEqual(
        context.projectedGrossAbsolutePosition + context.quantity,
        context.grossAbsolutePosition);
}

bool ReducesAbsolute(double current, double projected)
{
    return std::isfinite(current) && std::isfinite(projected) &&
        std::fabs(projected) < std::fabs(current);
}
}

const char* DeterministicRiskPolicy::Version()
{
    return "deterministic-risk-v2";
}

bool DeterministicRiskPolicy::ValidateLimits(
    const DeterministicRiskLimits& limits,
    std::string& reason)
{
    if (!PositiveFinite(limits.maxOrderQuantity) ||
        !PositiveFinite(limits.maxOrderNotional) ||
        limits.maxOrdersPerMinute == 0 ||
        limits.maxActiveOrders == 0 ||
        !PositiveFinite(limits.maxGrossPosition) ||
        !std::isfinite(limits.maxPriceDeviationBps) ||
        limits.maxPriceDeviationBps < 0.0 ||
        !OptionalLimit(limits.maxNetPosition) ||
        !OptionalLimit(limits.maxStrategyGrossPosition) ||
        !OptionalLimit(limits.maxDailyLoss) ||
        !OptionalLimit(limits.maxDrawdown))
    {
        reason = "RISK_LIMITS_INVALID";
        return false;
    }
    reason.clear();
    return true;
}

DeterministicRiskDecision DeterministicRiskPolicy::Evaluate(
    const DeterministicRiskLimits& limits,
    const DeterministicRiskContext& context)
{
    RuntimeLatencyScope riskLatency("hepta_risk_decision_latency_microseconds");

    std::string limitReason;
    if (!ValidateLimits(limits, limitReason))
        return Reject("RISK_LIMITS_INVALID", "risk limits are invalid");

    if (context.action != "BUY" && context.action != "SELL")
        return Reject("RISK_ORDER_SIDE_INVALID", "side must be BUY or SELL");
    if (context.orderType != "MKT" && context.orderType != "LMT")
        return Reject("RISK_ORDER_TYPE_INVALID",
                      "order type must be MKT or LMT");
    if (!PositiveFinite(context.quantity))
        return Reject("RISK_ORDER_QUANTITY_INVALID",
                      "quantity must be positive and finite");
    if (context.quantity > limits.maxOrderQuantity)
        return Reject("RISK_ORDER_QUANTITY_LIMIT",
                      "quantity exceeds maxOrderQuantity");
    if (!PositiveFinite(context.valuationPrice))
        return Reject("RISK_VALUATION_PRICE_INVALID",
                      "authoritative valuation price is unavailable");
    if (context.valuationPrice >
        limits.maxOrderNotional / context.quantity)
        return Reject("RISK_ORDER_NOTIONAL_LIMIT",
                      "order notional exceeds maxOrderNotional");

    if (!std::isfinite(context.grossAbsolutePosition) ||
        context.grossAbsolutePosition < 0.0 ||
        !std::isfinite(context.projectedGrossAbsolutePosition) ||
        context.projectedGrossAbsolutePosition < 0.0 ||
        !std::isfinite(context.netPosition) ||
        !std::isfinite(context.projectedNetPosition) ||
        !std::isfinite(context.strategyGrossPosition) ||
        context.strategyGrossPosition < 0.0 ||
        !std::isfinite(context.projectedStrategyGrossPosition) ||
        context.projectedStrategyGrossPosition < 0.0 ||
        !std::isfinite(context.dailyPnl) ||
        !std::isfinite(context.drawdown) || context.drawdown < 0.0)
        return Reject("RISK_POSITION_SNAPSHOT_INVALID",
                      "portfolio or projected position state is invalid");

    if (limits.requireCompleteSnapshot && !context.portfolioSnapshotComplete)
        return Reject("RISK_SNAPSHOT_INCOMPLETE",
                      "authoritative portfolio snapshot is incomplete");
    if (limits.requireFreshQuote && !context.quoteFresh)
        return Reject("RISK_QUOTE_STALE",
                      "authoritative quote is stale or unavailable");

    const bool strictReduction = StrictGrossReduction(context);
    if (context.exposureReducing && !strictReduction)
        return Reject("RISK_REDUCE_ONLY_CROSS_ZERO",
                      "claimed reduction is not an exact same-side position reduction");

    // A proven strict reduction remains available when entry is disabled or a
    // kill switch is engaged. The proof cannot cross zero and all order-shape,
    // valuation and quantity checks above still apply.
    if (limits.globalKillSwitch && !strictReduction)
        return Reject("RISK_GLOBAL_KILL_SWITCH_ON",
                      "global kill switch is engaged");
    if (!limits.orderSubmissionEnabled && !strictReduction)
        return Reject("RISK_ORDER_SUBMISSION_DISABLED",
                      "order submission gate is closed");
    if (limits.flattenOnly && !strictReduction)
        return Reject("RISK_FLATTEN_ONLY",
                      "flatten-only mode requires a proven strict reduction");

    // Entry-rate and active-order budgets apply to risk-increasing orders.
    // Strict safe-exit orders remain possible while those budgets are full.
    if (context.ordersInLastMinute >= limits.maxOrdersPerMinute &&
        !strictReduction)
        return Reject("RISK_ORDER_RATE_LIMIT",
                      "rolling order-attempt rate is exhausted");
    if (context.activeOrderCount >= limits.maxActiveOrders &&
        !strictReduction)
        return Reject("RISK_ACTIVE_ORDER_LIMIT",
                      "active order count reached maxActiveOrders");

    if (context.projectedGrossAbsolutePosition > limits.maxGrossPosition &&
        !strictReduction)
        return Reject("RISK_GROSS_POSITION_LIMIT",
                      "projected gross position exceeds maxGrossPosition");

    if (limits.maxNetPosition > 0.0 &&
        std::fabs(context.projectedNetPosition) > limits.maxNetPosition &&
        !(strictReduction &&
          ReducesAbsolute(context.netPosition, context.projectedNetPosition)))
        return Reject("RISK_NET_POSITION_LIMIT",
                      "projected net position exceeds maxNetPosition");

    if (limits.maxStrategyGrossPosition > 0.0 &&
        context.projectedStrategyGrossPosition >
            limits.maxStrategyGrossPosition &&
        !(strictReduction &&
          context.projectedStrategyGrossPosition < context.strategyGrossPosition))
        return Reject("RISK_STRATEGY_GROSS_LIMIT",
                      "projected strategy gross exceeds its budget");

    if (limits.maxDailyLoss > 0.0 &&
        context.dailyPnl <= -limits.maxDailyLoss && !strictReduction)
        return Reject("RISK_DAILY_LOSS_LIMIT",
                      "daily loss budget is exhausted");

    if (limits.maxDrawdown > 0.0 &&
        context.drawdown >= limits.maxDrawdown && !strictReduction)
        return Reject("RISK_DRAWDOWN_LIMIT",
                      "drawdown budget is exhausted");

    if (context.orderType == "LMT")
    {
        if (!PositiveFinite(context.submittedPrice) ||
            !PositiveFinite(context.referencePrice))
            return Reject("RISK_LIMIT_PRICE_INVALID",
                          "limit and authoritative reference prices are required");
        if (limits.maxPriceDeviationBps > 0.0)
        {
            const double deviation =
                std::fabs(context.submittedPrice - context.referencePrice) /
                context.referencePrice * 10000.0;
            if (!std::isfinite(deviation) ||
                deviation > limits.maxPriceDeviationBps)
                return Reject("RISK_PRICE_DEVIATION_LIMIT",
                              "limit price deviation exceeds policy");
        }
    }
    return Allow();
}
