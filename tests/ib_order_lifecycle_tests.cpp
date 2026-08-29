#include "adapter_ib/ib_order_lifecycle.h"

#include <cassert>
#include <string>
#include <vector>

namespace
{
void ExpectBlocked(
    const IbOrderLifecycleTracker& tracker,
    long orderId,
    const std::string& expectedReason)
{
    std::string reason;
    assert(!tracker.CanCancel(orderId, &reason));
    assert(reason == expectedReason);
}

void TestLocalSendRequiresBrokerAcknowledgement()
{
    IbOrderLifecycleTracker tracker;
    tracker.ActivateConnectionEpoch(1);
    ExpectBlocked(tracker, 41, "NO_BROKER_SUBMIT");

    tracker.BeginLocalOrderGeneration(41);
    ExpectBlocked(tracker, 41, "NO_BROKER_ACK");

    tracker.RecordBrokerStatus(41, "Submitted");
    std::string unchanged("sentinel");
    assert(tracker.CanCancel(41, &unchanged));
    assert(unchanged == "sentinel");
}

void TestEveryTerminalStatusSuppressesCancel()
{
    const char* const statuses[] = {
        "Filled", "Cancelled", "ApiCancelled", "Inactive", "Rejected"
    };
    for (const char* status : statuses)
    {
        IbOrderLifecycleTracker tracker;
        tracker.ActivateConnectionEpoch(2);
        tracker.BeginLocalOrderGeneration(42);
        tracker.RecordBrokerStatus(42, "Submitted");
        tracker.RecordBrokerStatus(
            42, status, std::string(status) == "Filled");
        ExpectBlocked(tracker, 42, "ALREADY_FINAL");
    }
}

void TestAuthoritativeOpenOrderRestoresCancelAuthority()
{
    IbOrderLifecycleTracker tracker;
    tracker.ActivateConnectionEpoch(3);
    tracker.RecordBrokerOpenOrder(43, "PreSubmitted");
    assert(tracker.CanCancel(43));

    tracker.RecordBrokerStatus(43, "Filled", true);
    ExpectBlocked(tracker, 43, "ALREADY_FINAL");
}

void TestUnknownStatusDoesNotInventBrokerAck()
{
    IbOrderLifecycleTracker tracker;
    tracker.ActivateConnectionEpoch(4);
    tracker.BeginLocalOrderGeneration(44);
    tracker.RecordBrokerStatus(44, "UnknownFutureStatus");
    ExpectBlocked(tracker, 44, "NO_BROKER_ACK");

    tracker.Forget(44);
    ExpectBlocked(tracker, 44, "NO_BROKER_SUBMIT");
    tracker.RecordBrokerOpenOrder(44, "");
    assert(tracker.CanCancel(44));
    tracker.InvalidateConnectionEpoch();
    ExpectBlocked(tracker, 44, "NO_BROKER_SUBMIT");
}

void TestTerminalStateIsStickyAcrossLateOpenOrder()
{
    IbOrderLifecycleTracker tracker;
    tracker.ActivateConnectionEpoch(5);
    tracker.BeginLocalOrderGeneration(45);
    tracker.RecordBrokerStatus(45, "Submitted");
    tracker.RecordBrokerStatus(45, "Filled", true);
    ExpectBlocked(tracker, 45, "ALREADY_FINAL");

    assert(!tracker.RecordBrokerOpenOrder(45, "Submitted"));
    ExpectBlocked(tracker, 45, "ALREADY_FINAL");
}

void TestReusedOrderIdStartsFreshLocalGeneration()
{
    IbOrderLifecycleTracker tracker;
    tracker.ActivateConnectionEpoch(6);
    tracker.BeginLocalOrderGeneration(46);
    tracker.RecordBrokerStatus(46, "Submitted");
    assert(tracker.CanCancel(46));

    tracker.BeginLocalOrderGeneration(46);
    ExpectBlocked(tracker, 46, "NO_BROKER_ACK");
    tracker.RecordBrokerOpenOrder(46, "Submitted");
    assert(tracker.CanCancel(46));
}

void TestConnectionEpochInvalidatesOldBrokerEvidence()
{
    IbOrderLifecycleTracker tracker;
    tracker.ActivateConnectionEpoch(7);
    assert(tracker.RecordBrokerOpenOrder(47, "Submitted"));
    assert(tracker.CanCancel(47));

    tracker.InvalidateConnectionEpoch();
    ExpectBlocked(tracker, 47, "NO_BROKER_SUBMIT");
    assert(!tracker.RecordBrokerOpenOrder(47, "Submitted"));
    ExpectBlocked(tracker, 47, "NO_BROKER_SUBMIT");

    tracker.ActivateConnectionEpoch(8);
    ExpectBlocked(tracker, 47, "NO_BROKER_SUBMIT");
    assert(tracker.RecordBrokerOpenOrder(47, "Submitted"));
    assert(tracker.CanCancel(47));
}

void TestTerminalOpenOrderStatusIsNotCancellable()
{
    IbOrderLifecycleTracker tracker;
    tracker.ActivateConnectionEpoch(12);
    tracker.BeginLocalOrderGeneration(50);
    assert(tracker.RecordBrokerOpenOrder(50, "ApiCancelled"));
    ExpectBlocked(tracker, 50, "ALREADY_FINAL");
}

void TestFilledRequiresEconomicEvidenceBeforeFinalizing()
{
    IbOrderLifecycleTracker tracker;
    tracker.ActivateConnectionEpoch(9);
    tracker.BeginLocalOrderGeneration(48);
    tracker.RecordBrokerStatus(48, "Submitted");

    tracker.RecordBrokerStatus(48, "Filled", false);
    assert(tracker.CanCancel(48));

    tracker.RecordBrokerStatus(48, "Filled", true);
    ExpectBlocked(tracker, 48, "ALREADY_FINAL");
}

void TestPartialAndPendingCancelStatusesAcknowledgeBrokerOrder()
{
    IbOrderLifecycleTracker tracker;
    tracker.ActivateConnectionEpoch(10);
    tracker.BeginLocalOrderGeneration(49);

    // Both statuses prove that IB knows the order exists.  They must not be
    // misclassified as NO_BROKER_ACK while a cancel intent is being drained.
    tracker.RecordBrokerStatus(49, "PartiallyFilled");
    assert(tracker.CanCancel(49));
    tracker.RecordBrokerStatus(49, "PendingCancel");
    assert(tracker.CanCancel(49));
}
} // namespace

int main()
{
    TestLocalSendRequiresBrokerAcknowledgement();
    TestEveryTerminalStatusSuppressesCancel();
    TestAuthoritativeOpenOrderRestoresCancelAuthority();
    TestUnknownStatusDoesNotInventBrokerAck();
    TestTerminalStateIsStickyAcrossLateOpenOrder();
    TestReusedOrderIdStartsFreshLocalGeneration();
    TestConnectionEpochInvalidatesOldBrokerEvidence();
    TestTerminalOpenOrderStatusIsNotCancellable();
    TestFilledRequiresEconomicEvidenceBeforeFinalizing();
    TestPartialAndPendingCancelStatusesAcknowledgeBrokerOrder();
    return 0;
}
