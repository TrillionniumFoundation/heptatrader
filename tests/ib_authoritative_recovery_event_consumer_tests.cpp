#include "../HeptaTrade/state/ib_authoritative_recovery_event_consumer.h"

#include <cassert>
#include <iostream>

int main()
{
    AuthoritativeTradingSnapshotStore store;
    IBAuthoritativeAccountPositionConsumer accountPositions(store, "DU123");
    IBAuthoritativeOpenOrderConsumer openOrders(store, "DU123");
    IBAuthoritativeQuoteSubscriptionSet quotes(store);
    IBAuthoritativeRecoveryCallbacks callbacks;
    callbacks.beginSnapshot = [&](SnapshotRefreshKind kind, std::uint64_t generation) {
        if (kind == SnapshotRefreshKind::AccountSummary) accountPositions.BeginAccount(generation);
        else if (kind == SnapshotRefreshKind::Positions) accountPositions.BeginPositions(generation);
        else if (kind == SnapshotRefreshKind::OpenOrders) openOrders.BeginRefresh(generation);
    };
    callbacks.abortSnapshot = [&](SnapshotRefreshKind kind, std::uint64_t generation) {
        if (kind == SnapshotRefreshKind::AccountSummary) accountPositions.AbortAccount(generation);
        else if (kind == SnapshotRefreshKind::Positions) accountPositions.AbortPositions(generation);
        else if (kind == SnapshotRefreshKind::OpenOrders) openOrders.AbortRefresh(generation);
    };
    callbacks.dispatchSnapshot = [](SnapshotRefreshKind) { return true; };
    callbacks.dispatchQuotes = [](std::uint64_t, std::uint64_t, std::uint64_t) { return true; };
    callbacks.abortQuotes = [](std::uint64_t) {};
    IBAuthoritativeRecoveryPolicy policy;
    IBAuthoritativeRecoveryCoordinator recovery(policy, callbacks);
    IBAuthoritativeRecoveryEventConsumer events(recovery, accountPositions, openOrders, quotes);
    assert(recovery.StartFullRecovery(7, 1000, "test").accepted);

    IBEvent netLiquidation;
    netLiquidation.type = IBEventType::AccountValue;
    netLiquidation.account = "DU123";
    netLiquidation.key = "NetLiquidation:USD";
    netLiquidation.value = "100000";
    assert(accountPositions.ConsumeAccountValue(netLiquidation) ==
        IBAuthoritativeSnapshotConsumeStatus::Applied);

    IBEvent end;
    end.type = IBEventType::AccountSummaryEnd;
    const IBAuthoritativeRecoveryEventCompletion completed =
        events.ConsumeCompletion(end, 1100);
    assert(completed.handled);
    assert(completed.hadActiveGeneration);
    assert(completed.snapshotAccepted);
    assert(completed.recovery.accepted);
    assert(completed.account.account.account == "DU123");
    assert(completed.account.metrics.at("NetLiquidation") == 100000.0);

    const IBAuthoritativeRecoveryEventCompletion stale =
        events.ConsumeCompletion(end, 1200);
    assert(stale.handled);
    assert(!stale.hadActiveGeneration);
    assert(stale.reasonCode == "RECOVERY_COMPLETION_WITHOUT_GENERATION");

    IBEvent unrelated;
    unrelated.type = IBEventType::TickPrice;
    assert(!events.ConsumeCompletion(unrelated, 1300).handled);

    IBEvent overflow;
    overflow.type = IBEventType::EventQueueOverflow;
    overflow.overflowGeneration = 9;
    const IBAuthoritativeRecoveryControlAction overflowAction =
        IBAuthoritativeRecoveryEventConsumer::ClassifyControlEvent(overflow);
    assert(overflowAction.overflow);
    assert(overflowAction.overflowGeneration == 9);
    assert(overflowAction.recoveryReason == "event_queue_overflow");

    IBEvent reset;
    reset.type = IBEventType::Error;
    reset.key = "1300";
    const IBAuthoritativeRecoveryControlAction resetAction =
        IBAuthoritativeRecoveryEventConsumer::ClassifyControlEvent(reset);
    assert(resetAction.forceDisconnect);
    assert(resetAction.recoveryReason == "ib_error_1300_socket_reset");
    std::cout << "ib_authoritative_recovery_event_consumer_tests: PASS" << std::endl;
    return 0;
}
