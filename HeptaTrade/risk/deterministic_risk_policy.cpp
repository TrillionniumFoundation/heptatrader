#include "deterministic_risk_policy.h"

#include <cmath>

namespace
{
DeterministicRiskDecision Allow()
{
    DeterministicRiskDecision decision;
    decision.allow = true;
    decision.reasonCode = "RISK_OK";
    return decision;
}

DeterministicRiskDecision Reject(const char* code, const char* detail)
{
    DeterministicRiskDecision decision;
    decision.allow = false;
    decision.reasonCode = code;
    decision.detail = detail;
    return decision;
}

bool PositiveFinite(double value)
{
    return std::isfinite(value) && value > 0.0;
}
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
        limits.maxPriceDeviationBps < 0.0)
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
    std::string limitReason;
    if (!ValidateLimits(limits, limitReason))
        return Reject("RISK_LIMITS_INVALID", "risk limits are invalid");
    if (limits.globalKillSwitch)
        return Reject("RISK_GLOBAL_KILL_SWITCH_ON",
                      "global kill switch is engaged");
    if (!limits.orderSubmissionEnabled)
        return Reject("RISK_ORDER_SUBMISSION_DISABLED",
                      "order submission gate is closed");
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
    if (context.ordersInLastMinute >= limits.maxOrdersPerMinute)
        return Reject("RISK_ORDER_RATE_LIMIT",
                      "rolling order-attempt rate is exhausted");
    if (context.activeOrderCount >= limits.maxActiveOrders)
        return Reject("RISK_ACTIVE_ORDER_LIMIT",
                      "active order count reached maxActiveOrders");
    if (!std::isfinite(context.grossAbsolutePosition) ||
        context.grossAbsolutePosition < 0.0 ||
        !std::isfinite(context.projectedGrossAbsolutePosition) ||
        context.projectedGrossAbsolutePosition < 0.0)
        return Reject("RISK_POSITION_SNAPSHOT_INVALID",
                      "gross or projected position is invalid");
    if (limits.flattenOnly && !context.exposureReducing)
        return Reject("RISK_FLATTEN_ONLY",
                      "flatten-only mode requires exposure reduction");
    // When an authoritative account is already above a limit, a proven
    // reduction remains available.  Any flat/increasing mutation must fit the
    // configured cap.
    if (context.projectedGrossAbsolutePosition > limits.maxGrossPosition &&
        (!context.exposureReducing ||
         context.projectedGrossAbsolutePosition >=
            context.grossAbsolutePosition))
        return Reject("RISK_GROSS_POSITION_LIMIT",
                      "projected gross position exceeds maxGrossPosition");
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
