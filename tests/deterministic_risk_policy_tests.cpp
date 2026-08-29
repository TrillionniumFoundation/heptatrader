#include "../HeptaTrade/risk/deterministic_risk_policy.h"

#include <cassert>
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
}

void TestKillAndSubmissionGates()
{
    DeterministicRiskLimits limits = Limits();
    limits.globalKillSwitch = true;
    ExpectReject(limits, Context(), "RISK_GLOBAL_KILL_SWITCH_ON");
    limits.globalKillSwitch = false;
    limits.orderSubmissionEnabled = false;
    ExpectReject(limits, Context(), "RISK_ORDER_SUBMISSION_DISABLED");
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
}

void TestProjectedGrossLimitAndReductionEscape()
{
    DeterministicRiskContext context = Context();
    context.grossAbsolutePosition = 19.0;
    context.projectedGrossAbsolutePosition = 21.0;
    ExpectReject(Limits(), context, "RISK_GROSS_POSITION_LIMIT");

    context.grossAbsolutePosition = 25.0;
    context.projectedGrossAbsolutePosition = 22.0;
    context.exposureReducing = true;
    const DeterministicRiskDecision reduction =
        DeterministicRiskPolicy::Evaluate(Limits(), context);
    assert(reduction.allow);
}

void TestFlattenOnly()
{
    DeterministicRiskLimits limits = Limits();
    limits.flattenOnly = true;
    ExpectReject(limits, Context(), "RISK_FLATTEN_ONLY");
    DeterministicRiskContext reduction = Context();
    reduction.exposureReducing = true;
    reduction.projectedGrossAbsolutePosition = 3.0;
    assert(DeterministicRiskPolicy::Evaluate(limits, reduction).allow);
}

void TestPriceDeviation()
{
    DeterministicRiskContext context = Context();
    context.submittedPrice = 101.0;
    ExpectReject(Limits(), context, "RISK_PRICE_DEVIATION_LIMIT");
}

void TestInvalidSnapshotsFailClosed()
{
    DeterministicRiskContext context = Context();
    context.projectedGrossAbsolutePosition = -1.0;
    ExpectReject(Limits(), context, "RISK_POSITION_SNAPSHOT_INVALID");
}
}

int main()
{
    TestAllowsBoundedOrder();
    TestKillAndSubmissionGates();
    TestQuantityAndNotionalLimits();
    TestRateAndActiveOrderLimits();
    TestProjectedGrossLimitAndReductionEscape();
    TestFlattenOnly();
    TestPriceDeviation();
    TestInvalidSnapshotsFailClosed();
    return 0;
}
