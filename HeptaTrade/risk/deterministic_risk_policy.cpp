#include "deterministic_risk_policy.h"
#include "../numeric/fixed_decimal.h"
#include "../observability/runtime_telemetry.h"

#include <cmath>
#include <cstdint>

#if !defined(__SIZEOF_INT128__)
#error "DeterministicRiskPolicy requires exact signed 128-bit intermediate arithmetic"
#endif

namespace
{
using RiskRaw = std::int64_t;
using RiskWide = __int128_t;

constexpr RiskRaw kScale = HeptaFixedDecimal::kScale;
constexpr RiskRaw kMaximumRaw = HeptaFixedDecimal::kMaximumRaw;

struct FixedRiskLimits
{
    RiskRaw maxOrderQuantity = 0;
    RiskRaw maxOrderNotional = 0;
    RiskRaw maxGrossPosition = 0;
    RiskRaw maxPriceDeviationBps = 0;
    RiskRaw maxNetPosition = 0;
    RiskRaw maxStrategyGrossPosition = 0;
    RiskRaw maxDailyLoss = 0;
    RiskRaw maxDrawdown = 0;
};

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

// Binary64 is accepted only as a compatibility ingress. This conversion is
// intentionally byte-for-byte equivalent to the canonical fixed-point
// boundary: the value must map to one exact microunit and the canonical double
// projection of that raw value must equal the original input.
bool ExactRaw(double value, RiskRaw& out)
{
    out = 0;
    if (!std::isfinite(value)) return false;
    if (value == 0.0 && std::signbit(value)) return false;

    const long double scaled =
        static_cast<long double>(value) * static_cast<long double>(kScale);
    if (scaled < -static_cast<long double>(kMaximumRaw) ||
        scaled > static_cast<long double>(kMaximumRaw))
        return false;

    const long double nearest = std::round(scaled);
    if ((nearest == 0.0L && value != 0.0) ||
        std::fabs(scaled - nearest) > 0.000001L)
        return false;

    const RiskRaw raw = static_cast<RiskRaw>(nearest);
    const double canonical =
        static_cast<double>(raw) / static_cast<double>(kScale);
    if (canonical != value) return false;

    out = raw == 0 ? 0 : raw;
    return true;
}

bool PositiveRaw(double value, RiskRaw& out)
{
    return ExactRaw(value, out) && out > 0;
}

bool NonNegativeRaw(double value, RiskRaw& out)
{
    return ExactRaw(value, out) && out >= 0;
}

bool CheckedAdd(RiskRaw left, RiskRaw right, RiskRaw& out)
{
    if ((right > 0 && left > kMaximumRaw - right) ||
        (right < 0 && left < -kMaximumRaw - right))
        return false;
    out = left + right;
    return true;
}

RiskRaw Absolute(RiskRaw value)
{
    // Canonical raw values are bounded far above INT64_MIN, so negation is
    // well-defined for every value admitted by ExactRaw.
    return value < 0 ? -value : value;
}

bool ConvertLimits(
    const DeterministicRiskLimits& limits,
    FixedRiskLimits& fixed)
{
    return PositiveRaw(limits.maxOrderQuantity, fixed.maxOrderQuantity) &&
        PositiveRaw(limits.maxOrderNotional, fixed.maxOrderNotional) &&
        limits.maxOrdersPerMinute != 0 &&
        limits.maxActiveOrders != 0 &&
        PositiveRaw(limits.maxGrossPosition, fixed.maxGrossPosition) &&
        NonNegativeRaw(
            limits.maxPriceDeviationBps, fixed.maxPriceDeviationBps) &&
        NonNegativeRaw(limits.maxNetPosition, fixed.maxNetPosition) &&
        NonNegativeRaw(
            limits.maxStrategyGrossPosition,
            fixed.maxStrategyGrossPosition) &&
        NonNegativeRaw(limits.maxDailyLoss, fixed.maxDailyLoss) &&
        NonNegativeRaw(limits.maxDrawdown, fixed.maxDrawdown);
}

bool NotionalWithinLimit(
    RiskRaw quantity,
    RiskRaw price,
    RiskRaw maximumNotional)
{
    const RiskWide notional =
        static_cast<RiskWide>(quantity) * static_cast<RiskWide>(price);
    const RiskWide limit =
        static_cast<RiskWide>(maximumNotional) *
        static_cast<RiskWide>(kScale);
    return notional <= limit;
}

bool SignedPositionReduction(
    const std::string& action,
    RiskRaw quantity,
    RiskRaw netPosition,
    RiskRaw projectedNetPosition)
{
    const RiskRaw signedQuantity = action == "BUY" ? quantity : -quantity;
    RiskRaw expectedProjected = 0;
    if (!CheckedAdd(netPosition, signedQuantity, expectedProjected) ||
        projectedNetPosition != expectedProjected)
        return false;

    if (netPosition > 0)
        return action == "SELL" && projectedNetPosition >= 0 &&
            projectedNetPosition < netPosition;
    if (netPosition < 0)
        return action == "BUY" && projectedNetPosition <= 0 &&
            projectedNetPosition > netPosition;
    return false;
}

bool StrictGrossReduction(
    const DeterministicRiskContext& context,
    RiskRaw quantity,
    RiskRaw grossAbsolutePosition,
    RiskRaw projectedGrossAbsolutePosition,
    RiskRaw netPosition,
    RiskRaw projectedNetPosition)
{
    if (!context.exposureReducing ||
        !SignedPositionReduction(
            context.action,
            quantity,
            netPosition,
            projectedNetPosition) ||
        projectedGrossAbsolutePosition >= grossAbsolutePosition)
        return false;

    // A normalized single-instrument strict reduction removes exactly the
    // admitted quantity from gross exposure. No epsilon or portfolio-scaled
    // tolerance is permitted at this authority boundary.
    RiskRaw reconstructedGross = 0;
    return CheckedAdd(
               projectedGrossAbsolutePosition,
               quantity,
               reconstructedGross) &&
        reconstructedGross == grossAbsolutePosition;
}

bool ReducesAbsolute(RiskRaw current, RiskRaw projected)
{
    return Absolute(projected) < Absolute(current);
}

bool PriceDeviationWithinLimit(
    RiskRaw submittedPrice,
    RiskRaw referencePrice,
    RiskRaw maximumDeviationBps)
{
    const RiskRaw difference =
        submittedPrice >= referencePrice
            ? submittedPrice - referencePrice
            : referencePrice - submittedPrice;
    const RiskWide left =
        static_cast<RiskWide>(difference) *
        static_cast<RiskWide>(10000) *
        static_cast<RiskWide>(kScale);
    const RiskWide right =
        static_cast<RiskWide>(referencePrice) *
        static_cast<RiskWide>(maximumDeviationBps);
    return left <= right;
}
}

const char* DeterministicRiskPolicy::Version()
{
    return "deterministic-risk-v3";
}

const char* DeterministicRiskPolicy::NumericPolicy()
{
    return "hepta.numeric.fixed-v1";
}

bool DeterministicRiskPolicy::ValidateLimits(
    const DeterministicRiskLimits& limits,
    std::string& reason)
{
    FixedRiskLimits fixed;
    if (!ConvertLimits(limits, fixed))
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

    FixedRiskLimits fixedLimits;
    if (!ConvertLimits(limits, fixedLimits))
        return Reject(
            "RISK_LIMITS_INVALID",
            "risk limits are not exact hepta.numeric.fixed-v1 values");

    if (context.action != "BUY" && context.action != "SELL")
        return Reject("RISK_ORDER_SIDE_INVALID", "side must be BUY or SELL");
    if (context.orderType != "MKT" && context.orderType != "LMT")
        return Reject(
            "RISK_ORDER_TYPE_INVALID",
            "order type must be MKT or LMT");

    RiskRaw quantity = 0;
    if (!PositiveRaw(context.quantity, quantity))
        return Reject(
            "RISK_ORDER_QUANTITY_INVALID",
            "quantity must be positive and exactly representable in microunits");
    if (quantity > fixedLimits.maxOrderQuantity)
        return Reject(
            "RISK_ORDER_QUANTITY_LIMIT",
            "quantity exceeds maxOrderQuantity");

    RiskRaw valuationPrice = 0;
    if (!PositiveRaw(context.valuationPrice, valuationPrice))
        return Reject(
            "RISK_VALUATION_PRICE_INVALID",
            "authoritative valuation price is unavailable or not exact");
    if (!NotionalWithinLimit(
            quantity,
            valuationPrice,
            fixedLimits.maxOrderNotional))
        return Reject(
            "RISK_ORDER_NOTIONAL_LIMIT",
            "order notional exceeds maxOrderNotional");

    RiskRaw grossAbsolutePosition = 0;
    RiskRaw projectedGrossAbsolutePosition = 0;
    RiskRaw netPosition = 0;
    RiskRaw projectedNetPosition = 0;
    RiskRaw strategyGrossPosition = 0;
    RiskRaw projectedStrategyGrossPosition = 0;
    RiskRaw dailyPnl = 0;
    RiskRaw drawdown = 0;
    if (!NonNegativeRaw(
            context.grossAbsolutePosition,
            grossAbsolutePosition) ||
        !NonNegativeRaw(
            context.projectedGrossAbsolutePosition,
            projectedGrossAbsolutePosition) ||
        !ExactRaw(context.netPosition, netPosition) ||
        !ExactRaw(context.projectedNetPosition, projectedNetPosition) ||
        !NonNegativeRaw(
            context.strategyGrossPosition,
            strategyGrossPosition) ||
        !NonNegativeRaw(
            context.projectedStrategyGrossPosition,
            projectedStrategyGrossPosition) ||
        !ExactRaw(context.dailyPnl, dailyPnl) ||
        !NonNegativeRaw(context.drawdown, drawdown))
        return Reject(
            "RISK_POSITION_SNAPSHOT_INVALID",
            "portfolio state is not exact hepta.numeric.fixed-v1 data");

    if (limits.requireCompleteSnapshot && !context.portfolioSnapshotComplete)
        return Reject(
            "RISK_SNAPSHOT_INCOMPLETE",
            "authoritative portfolio snapshot is incomplete");
    if (limits.requireFreshQuote && !context.quoteFresh)
        return Reject(
            "RISK_QUOTE_STALE",
            "authoritative quote is stale or unavailable");

    const bool strictReduction = StrictGrossReduction(
        context,
        quantity,
        grossAbsolutePosition,
        projectedGrossAbsolutePosition,
        netPosition,
        projectedNetPosition);
    if (context.exposureReducing && !strictReduction)
        return Reject(
            "RISK_REDUCE_ONLY_CROSS_ZERO",
            "claimed reduction is not an exact same-side position reduction");

    // A proven strict reduction remains available when entry is disabled or a
    // kill switch is engaged. The proof cannot cross zero and all order-shape,
    // valuation and quantity checks above still apply.
    if (limits.globalKillSwitch && !strictReduction)
        return Reject(
            "RISK_GLOBAL_KILL_SWITCH_ON",
            "global kill switch is engaged");
    if (!limits.orderSubmissionEnabled && !strictReduction)
        return Reject(
            "RISK_ORDER_SUBMISSION_DISABLED",
            "order submission gate is closed");
    if (limits.flattenOnly && !strictReduction)
        return Reject(
            "RISK_FLATTEN_ONLY",
            "flatten-only mode requires a proven strict reduction");

    // Entry-rate and active-order budgets apply to risk-increasing orders.
    // Strict safe-exit orders remain possible while those budgets are full.
    if (context.ordersInLastMinute >= limits.maxOrdersPerMinute &&
        !strictReduction)
        return Reject(
            "RISK_ORDER_RATE_LIMIT",
            "rolling order-attempt rate is exhausted");
    if (context.activeOrderCount >= limits.maxActiveOrders &&
        !strictReduction)
        return Reject(
            "RISK_ACTIVE_ORDER_LIMIT",
            "active order count reached maxActiveOrders");

    if (projectedGrossAbsolutePosition > fixedLimits.maxGrossPosition &&
        !strictReduction)
        return Reject(
            "RISK_GROSS_POSITION_LIMIT",
            "projected gross position exceeds maxGrossPosition");

    if (fixedLimits.maxNetPosition > 0 &&
        Absolute(projectedNetPosition) > fixedLimits.maxNetPosition &&
        !(strictReduction &&
          ReducesAbsolute(netPosition, projectedNetPosition)))
        return Reject(
            "RISK_NET_POSITION_LIMIT",
            "projected net position exceeds maxNetPosition");

    if (fixedLimits.maxStrategyGrossPosition > 0 &&
        projectedStrategyGrossPosition >
            fixedLimits.maxStrategyGrossPosition &&
        !(strictReduction &&
          projectedStrategyGrossPosition < strategyGrossPosition))
        return Reject(
            "RISK_STRATEGY_GROSS_LIMIT",
            "projected strategy gross exceeds its budget");

    if (fixedLimits.maxDailyLoss > 0 &&
        dailyPnl <= -fixedLimits.maxDailyLoss &&
        !strictReduction)
        return Reject(
            "RISK_DAILY_LOSS_LIMIT",
            "daily loss budget is exhausted");

    if (fixedLimits.maxDrawdown > 0 &&
        drawdown >= fixedLimits.maxDrawdown &&
        !strictReduction)
        return Reject(
            "RISK_DRAWDOWN_LIMIT",
            "drawdown budget is exhausted");

    if (context.orderType == "LMT")
    {
        RiskRaw submittedPrice = 0;
        RiskRaw referencePrice = 0;
        if (!PositiveRaw(context.submittedPrice, submittedPrice) ||
            !PositiveRaw(context.referencePrice, referencePrice))
            return Reject(
                "RISK_LIMIT_PRICE_INVALID",
                "limit and reference prices must be exact positive microunits");
        if (fixedLimits.maxPriceDeviationBps > 0 &&
            !PriceDeviationWithinLimit(
                submittedPrice,
                referencePrice,
                fixedLimits.maxPriceDeviationBps))
            return Reject(
                "RISK_PRICE_DEVIATION_LIMIT",
                "limit price deviation exceeds policy");
    }
    return Allow();
}
