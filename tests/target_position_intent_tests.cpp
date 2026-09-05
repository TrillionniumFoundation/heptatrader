#include "intent/target_position_intent.h"

#include <cassert>
#include <cmath>
#include <limits>
#include <string>

namespace
{
TargetPositionDecisionSnapshot Snapshot()
{
    TargetPositionDecisionSnapshot snapshot;
    snapshot.agentId = "agent-a";
    snapshot.sessionId = "session-a";
    snapshot.account = "SIM";
    snapshot.executionDomain = "SIM:default";
    snapshot.executionServiceEpoch = "epoch-a";
    snapshot.fencingGeneration = 3;
    snapshot.collectionWatermark = 9;
    snapshot.eventWatermark = 8;
    snapshot.snapshotWatermark = 9;
    snapshot.instrument = "EUR.USD";
    snapshot.collectionStartedAtMs = 1000;
    snapshot.collectionCompletedAtMs = 1010;
    snapshot.quoteObservedAtMs = 1005;
    snapshot.bid = 1.1000;
    snapshot.ask = 1.1002;
    snapshot.currentPosition = 2.0;
    return snapshot;
}

TargetPositionIntentPolicy Policy()
{
    TargetPositionIntentPolicy policy;
    policy.maxOrderQuantity = 20.0;
    policy.maxAbsoluteTargetPosition = 20.0;
    policy.maxSlippageBps = 10.0;
    policy.maxIntentLifetimeMs = 60000;
    return policy;
}

TargetPositionIntentRequest Request(double target)
{
    TargetPositionIntentRequest request;
    request.targetPosition = target;
    request.maxSlippageBps = 2.0;
    request.expiresAtMs = 50000;
    return request;
}

TargetPositionExecutionPlan Build(
    const TargetPositionDecisionSnapshot& snapshot,
    const TargetPositionIntentRequest& request,
    const TargetPositionIntentPolicy& policy,
    std::string& code)
{
    TargetPositionExecutionPlan plan;
    std::string detail;
    assert(TargetPositionIntentContract::BuildPlan(
        snapshot, request, policy, 10000, plan, code, detail));
    assert(detail.empty());
    assert(plan.previewPermit.size() == 71);
    assert(plan.previewPermit.compare(0, 7, "sha256:") == 0);
    return plan;
}

void TestBuyAndSellPlans()
{
    std::string code;
    TargetPositionExecutionPlan buy = Build(Snapshot(), Request(5.0), Policy(), code);
    assert(code == "INTENT_PLAN_READY");
    assert(!buy.noOp);
    assert(buy.side == "BUY");
    assert(buy.orderType == "LMT");
    assert(buy.timeInForce == "DAY");
    assert(buy.quantity == 3.0);
    assert(buy.referencePrice == Snapshot().ask);
    assert(buy.limitPrice > buy.referencePrice);

    TargetPositionExecutionPlan sell = Build(Snapshot(), Request(-5.0), Policy(), code);
    assert(sell.side == "SELL");
    assert(sell.quantity == 7.0);
    assert(sell.referencePrice == Snapshot().bid);
    assert(sell.limitPrice < sell.referencePrice);
}

void TestNoOp()
{
    std::string code;
    const TargetPositionExecutionPlan plan =
        Build(Snapshot(), Request(2.0), Policy(), code);
    assert(plan.noOp);
    assert(code == "INTENT_NO_CHANGE");
    assert(plan.quantity == 0.0);
}

void TestSignedZeroHasOnePermitIdentity()
{
    std::string code;
    TargetPositionDecisionSnapshot snapshot = Snapshot();
    snapshot.currentPosition = 0.0;
    TargetPositionIntentRequest positive = Request(0.0);
    TargetPositionIntentRequest negative = Request(-0.0);
    const TargetPositionIntentPolicy policy = Policy();
    const TargetPositionExecutionPlan positivePlan =
        Build(snapshot, positive, policy, code);
    const TargetPositionExecutionPlan negativePlan =
        Build(snapshot, negative, policy, code);
    // Signed zero is one canonical wire value; a direct in-process caller
    // must not receive a distinct permit for the same no-op intent.
    assert(positivePlan.previewPermit == negativePlan.previewPermit);
}

void TestPermitBindsSnapshotAndIntent()
{
    std::string code;
    const TargetPositionDecisionSnapshot snapshot = Snapshot();
    const TargetPositionIntentRequest request = Request(5.0);
    const TargetPositionIntentPolicy policy = Policy();
    const TargetPositionExecutionPlan plan = Build(snapshot, request, policy, code);
    assert(TargetPositionIntentContract::PermitMatches(
        plan.previewPermit, snapshot, request, policy, plan));

    TargetPositionDecisionSnapshot changed = snapshot;
    ++changed.fencingGeneration;
    assert(!TargetPositionIntentContract::PermitMatches(
        plan.previewPermit, changed, request, policy, plan));
    changed = snapshot;
    changed.currentPosition = 1.0;
    assert(!TargetPositionIntentContract::PermitMatches(
        plan.previewPermit, changed, request, policy, plan));
    changed = snapshot;
    ++changed.snapshotWatermark;
    assert(!TargetPositionIntentContract::PermitMatches(
        plan.previewPermit, changed, request, policy, plan));

    TargetPositionIntentRequest changedRequest = request;
    changedRequest.targetPosition = 6.0;
    assert(!TargetPositionIntentContract::PermitMatches(
        plan.previewPermit, snapshot, changedRequest, policy, plan));
}

void TestLimitsAndInvalidState()
{
    TargetPositionExecutionPlan plan;
    std::string code;
    std::string detail;
    TargetPositionIntentRequest request = Request(30.0);
    assert(!TargetPositionIntentContract::BuildPlan(
        Snapshot(), request, Policy(), 10000, plan, code, detail));
    assert(code == "INTENT_TARGET_LIMIT");

    request = Request(5.0);
    request.maxSlippageBps = 11.0;
    assert(!TargetPositionIntentContract::BuildPlan(
        Snapshot(), request, Policy(), 10000, plan, code, detail));
    assert(code == "INTENT_SLIPPAGE_LIMIT");

    request = Request(5.0);
    request.expiresAtMs = 70001;
    assert(!TargetPositionIntentContract::BuildPlan(
        Snapshot(), request, Policy(), 10000, plan, code, detail));
    assert(code == "INTENT_EXPIRY_INVALID");

    TargetPositionDecisionSnapshot invalid = Snapshot();
    invalid.ask = std::numeric_limits<double>::quiet_NaN();
    assert(!TargetPositionIntentContract::BuildPlan(
        invalid, Request(5.0), Policy(), 10000, plan, code, detail));
    assert(code == "INTENT_MARKET_STATE_INVALID");

    invalid = Snapshot();
    // Event-feed and collection watermarks are independent domains.  A
    // freshly started feed has an authoritative zero cursor until its first
    // event; that must not block a safe target preview.
    invalid.eventWatermark = 0;
    assert(TargetPositionIntentContract::BuildPlan(
        invalid, Request(5.0), Policy(), 10000, plan, code, detail));
    assert(code == "INTENT_PLAN_READY");

    invalid = Snapshot();
    invalid.collectionWatermark = invalid.snapshotWatermark + 1;
    assert(!TargetPositionIntentContract::BuildPlan(
        invalid, Request(5.0), Policy(), 10000, plan, code, detail));
    assert(code == "INTENT_SNAPSHOT_INCONSISTENT");
}

void TestDerivedQuantityLimitAndCrossZeroPlan()
{
    TargetPositionIntentPolicy policy = Policy();
    policy.maxOrderQuantity = 5.0;
    TargetPositionExecutionPlan plan;
    std::string code;
    std::string detail;
    assert(!TargetPositionIntentContract::BuildPlan(
        Snapshot(), Request(-5.0), policy, 10000, plan, code, detail));
    assert(code == "INTENT_ORDER_QUANTITY_LIMIT");

    // Intent derivation faithfully represents a requested target flip. The
    // deterministic portfolio/risk authority decides whether that flip is
    // allowed; a reduce-only path can never use this as a crossing bypass.
    policy.maxOrderQuantity = 20.0;
    assert(TargetPositionIntentContract::BuildPlan(
        Snapshot(), Request(-5.0), policy, 10000, plan, code, detail));
    assert(plan.side == "SELL");
    assert(plan.quantity == 7.0);
}
}

int main()
{
    TestBuyAndSellPlans();
    TestNoOp();
    TestSignedZeroHasOnePermitIdentity();
    TestPermitBindsSnapshotAndIntent();
    TestLimitsAndInvalidState();
    TestDerivedQuantityLimitAndCrossZeroPlan();
    return 0;
}
