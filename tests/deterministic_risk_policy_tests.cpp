#include "../HeptaTrade/risk/deterministic_risk_policy.h"

#include <cassert>
#include <limits>
#include <string>

namespace
{
DeterministicRiskLimits Limits()
{
    DeterministicRiskLimits limits;
    limits.maxOrderQuantity = 10.0;
    limits.maxOrderNotional = 1000.0;
    limits.maxOrdersPerMinute = 3;
    limits.maxActiveOrders = 2;
    limits.maxGrossPosition = 20.0;
    limits.maxPriceDeviationBps = 25.0;
    return limits;
}

DeterministicRiskContext Context()
{
    DeterministicRiskContext context;
    context.action = "BUY";
    context.orderType = "LMT";
    context.quantity = 2.0;
    context.valuationPrice = 100.0;
    context.submittedPrice = 100.1;
    context.referencePrice = 100.0;
    context.grossAbsolutePosition = 5.0;
    context.projectedGrossAbsolutePosition = 7.0;
    return context;
}

DeterministicRiskContext StrictReduction()
{
    DeterministicRiskContext context = Context();
    context.action = "SELL";
    context.quantity = 2.0;
    context.grossAbsolutePosition = 5.0;
    context.projectedGrossAbsolutePosition = 3.0;
    context.exposureReducing = true;
    return context;
}

void ExpectReject(
    const DeterministicRiskLimits& limits,
    const DeterministicRiskContext& context,
    const char* code)
{
    const DeterministicRiskDecision decision =
        DeterministicRiskPolicy::Evaluate(limits, context);
    assert(!decision.allow);
    assert(decision.reasonCode == code);
}

void TestAllowsBoundedOrder()
{
    const DeterministicRiskDecision decision =
        DeterministicRiskPolicy::Evaluate(Limits(), Context());
    assert(decision.allow);
    assert(decision.reasonCode == "RISK_OK");
    assert(std::string(DeterministicRiskPolicy::Version()) ==
           "deterministic-risk-v2");
}

void TestKillAndSubmissionGates()
{
    DeterministicRiskLimits limits = Limits();
    limits.globalKillSwitch = true;
    ExpectReject(limits, Context(), "RISK_GLOBAL_KILL_SWITCH_ON");
    limits.globalKillSwitch = false;
    limits.orderSubmissionEnabled = false;
    ExpectReject(limits, Context(), "RISK_ORDER_SUBMISSION_DISABLED");

    limits.globalKillSwitch = true;
    assert(DeterministicRiskPolicy::Evaluate(limits, StrictReduction()).allow);
}

void TestQuantityAndNotionalLimits()
{
    DeterministicRiskContext context = Context();
    context.quantity = 11.0;
    ExpectReject(Limits(), context, "RISK_ORDER_QUANTITY_LIMIT");
    context = Context();
    context.valuationPrice = 600.0;
    ExpectReject(Limits(), context, "RISK_ORDER_NOTIONAL_LIMIT");
}

void TestRateAndActiveOrderLimits()
{
    DeterministicRiskContext context = Context();
    context.ordersInLastMinute = 3;
    ExpectReject(Limits(), context, "RISK_ORDER_RATE_LIMIT");
    context = Context();
    context.activeOrderCount = 2;
    ExpectReject(Limits(), context, "RISK_ACTIVE_ORDER_LIMIT");

    context = StrictReduction();
    context.ordersInLastMinute = 3;
    context.activeOrderCount = 2;
    assert(DeterministicRiskPolicy::Evaluate(Limits(), context).allow);
}

void TestProjectedGrossLimitAndReductionEscape()
{
    DeterministicRiskContext context = Context();
    context.grossAbsolutePosition = 19.0;
    context.projectedGrossAbsolutePosition = 21.0;
    ExpectReject(Limits(), context, "RISK_GROSS_POSITION_LIMIT");

    context = StrictReduction();
    context.quantity = 3.0;
    context.grossAbsolutePosition = 25.0;
    context.projectedGrossAbsolutePosition = 22.0;
    const DeterministicRiskDecision reduction =
        DeterministicRiskPolicy::Evaluate(Limits(), context);
    assert(reduction.allow);
}

void TestReduceOnlyCannotCrossZero()
{
    DeterministicRiskContext context = StrictReduction();
    // Current exposure can be +10 and a SELL 15 would project gross from 10 to
    // 5 while crossing into a new -5 position. The gross reduction is only 5,
    // not the submitted quantity 15, so the independent proof rejects it.
    context.quantity = 15.0;
    context.grossAbsolutePosition = 10.0;
    context.projectedGrossAbsolutePosition = 5.0;
    ExpectReject(Limits(), context, "RISK_ORDER_QUANTITY_LIMIT");

    DeterministicRiskLimits wider = Limits();
    wider.maxOrderQuantity = 20.0;
    wider.maxOrderNotional = 5000.0;
    ExpectReject(wider, context, "RISK_REDUCE_ONLY_CROSS_ZERO");
}

void TestFlattenOnly()
{
    DeterministicRiskLimits limits = Limits();
    limits.flattenOnly = true;
    ExpectReject(limits, Context(), "RISK_FLATTEN_ONLY");
    assert(DeterministicRiskPolicy::Evaluate(limits, StrictReduction()).allow);
}

void TestFreshnessAndSnapshotGates()
{
    DeterministicRiskContext context = Context();
    context.quoteFresh = false;
    ExpectReject(Limits(), context, "RISK_QUOTE_STALE");
    context = Context();
    context.portfolioSnapshotComplete = false;
    ExpectReject(Limits(), context, "RISK_SNAPSHOT_INCOMPLETE");
}

void TestOptionalPortfolioBudgets()
{
    DeterministicRiskLimits limits = Limits();
    limits.maxNetPosition = 5.0;
    limits.maxStrategyGrossPosition = 6.0;
    limits.maxDailyLoss = 100.0;
    limits.maxDrawdown = 50.0;

    DeterministicRiskContext context = Context();
    context.projectedNetPosition = 6.0;
    ExpectReject(limits, context, "RISK_NET_POSITION_LIMIT");

    context = Context();
    context.strategyGrossPosition = 5.0;
    context.projectedStrategyGrossPosition = 7.0;
    ExpectReject(limits, context, "RISK_STRATEGY_GROSS_LIMIT");

    context = Context();
    context.dailyPnl = -100.0;
    ExpectReject(limits, context, "RISK_DAILY_LOSS_LIMIT");

    context = Context();
    context.drawdown = 50.0;
    ExpectReject(limits, context, "RISK_DRAWDOWN_LIMIT");
}

void TestPriceDeviation()
{
    DeterministicRiskContext context = Context();
    context.submittedPrice = 101.0;
    ExpectReject(Limits(), context, "RISK_PRICE_DEVIATION_LIMIT");
}

void TestInvalidNumbersFailClosed()
{
    DeterministicRiskContext context = Context();
    context.projectedGrossAbsolutePosition = -1.0;
    ExpectReject(Limits(), context, "RISK_POSITION_SNAPSHOT_INVALID");

    context = Context();
    context.quantity = std::numeric_limits<double>::quiet_NaN();
    ExpectReject(Limits(), context, "RISK_ORDER_QUANTITY_INVALID");

    context = Context();
    context.dailyPnl = std::numeric_limits<double>::infinity();
    ExpectReject(Limits(), context, "RISK_POSITION_SNAPSHOT_INVALID");

    DeterministicRiskLimits limits = Limits();
    limits.maxNetPosition = -1.0;
    std::string reason;
    assert(!DeterministicRiskPolicy::ValidateLimits(limits, reason));
    assert(reason == "RISK_LIMITS_INVALID");
}
}

int main()
{
    TestAllowsBoundedOrder();
    TestKillAndSubmissionGates();
    TestQuantityAndNotionalLimits();
    TestRateAndActiveOrderLimits();
    TestProjectedGrossLimitAndReductionEscape();
    TestReduceOnlyCannotCrossZero();
    TestFlattenOnly();
    TestFreshnessAndSnapshotGates();
    TestOptionalPortfolioBudgets();
    TestPriceDeviation();
    TestInvalidNumbersFailClosed();
    return 0;
}
