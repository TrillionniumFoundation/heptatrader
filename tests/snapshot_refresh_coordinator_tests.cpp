#include "state/snapshot_refresh_coordinator.h"

#include <cassert>
#include <thread>
#include <vector>

int main()
{
    SnapshotRefreshCoordinator coordinator;

    const SnapshotRefreshRequestResult first =
        coordinator.Request(SnapshotRefreshKind::Positions);
    assert(first.dispatch);
    assert(!first.coalesced);
    assert(first.generation == 1);
    assert(coordinator.IsCurrent(SnapshotRefreshKind::Positions, 1));

    const SnapshotRefreshRequestResult duplicate =
        coordinator.Request(SnapshotRefreshKind::Positions);
    assert(!duplicate.dispatch);
    assert(duplicate.coalesced);
    assert(duplicate.generation == 1);

    const SnapshotRefreshCompletionResult firstComplete =
        coordinator.Complete(SnapshotRefreshKind::Positions, 1);
    assert(firstComplete.accepted);
    assert(firstComplete.dispatchNext);
    assert(firstComplete.completedGeneration == 1);
    assert(firstComplete.nextGeneration == 2);
    assert(!coordinator.IsCurrent(SnapshotRefreshKind::Positions, 1));
    assert(coordinator.IsCurrent(SnapshotRefreshKind::Positions, 2));

    const SnapshotRefreshCompletionResult staleComplete =
        coordinator.Complete(SnapshotRefreshKind::Positions, 1);
    assert(!staleComplete.accepted);

    const SnapshotRefreshCompletionResult secondComplete =
        coordinator.Complete(SnapshotRefreshKind::Positions, 2);
    assert(secondComplete.accepted);
    assert(!secondComplete.dispatchNext);
    assert(!coordinator.IsInFlight(SnapshotRefreshKind::Positions));

    const SnapshotRefreshRequestResult account =
        coordinator.Request(SnapshotRefreshKind::AccountSummary);
    const SnapshotRefreshRequestResult orders =
        coordinator.Request(SnapshotRefreshKind::OpenOrders);
    assert(account.dispatch && account.generation == 1);
    assert(orders.dispatch && orders.generation == 1);
    assert(coordinator.Abort(SnapshotRefreshKind::AccountSummary, 1));
    assert(!coordinator.Abort(SnapshotRefreshKind::AccountSummary, 1));
    assert(coordinator.IsCurrent(SnapshotRefreshKind::OpenOrders, 1));

    SnapshotRefreshCoordinator concurrent;
    std::vector<std::thread> threads;
    std::vector<SnapshotRefreshRequestResult> results(16);
    for (std::size_t index = 0; index < results.size(); ++index)
    {
        threads.push_back(std::thread([&concurrent, &results, index]() {
            results[index] = concurrent.Request(SnapshotRefreshKind::Positions);
        }));
    }
    for (std::size_t index = 0; index < threads.size(); ++index)
    {
        threads[index].join();
    }

    std::size_t dispatchCount = 0;
    std::size_t coalescedCount = 0;
    for (std::size_t index = 0; index < results.size(); ++index)
    {
        if (results[index].dispatch) ++dispatchCount;
        if (results[index].coalesced) ++coalescedCount;
        assert(results[index].generation == 1);
    }
    assert(dispatchCount == 1);
    assert(coalescedCount == results.size() - 1);

    const SnapshotRefreshCompletionResult concurrentComplete =
        concurrent.Complete(SnapshotRefreshKind::Positions, 1);
    assert(concurrentComplete.accepted);
    assert(concurrentComplete.dispatchNext);
    assert(concurrentComplete.nextGeneration == 2);

    SnapshotRefreshCoordinator abortedPending;
    const SnapshotRefreshRequestResult abortedFirst =
        abortedPending.Request(SnapshotRefreshKind::OpenOrders);
    const SnapshotRefreshRequestResult abortedFollowup =
        abortedPending.Request(SnapshotRefreshKind::OpenOrders);
    assert(abortedFirst.dispatch && abortedFirst.generation == 1);
    assert(abortedFollowup.coalesced && abortedFollowup.generation == 1);
    assert(abortedPending.Abort(SnapshotRefreshKind::OpenOrders, 1));
    const SnapshotRefreshRequestResult afterAbort =
        abortedPending.Request(SnapshotRefreshKind::OpenOrders);
    assert(afterAbort.dispatch);
    assert(!afterAbort.coalesced);
    assert(afterAbort.generation == 2);

    SnapshotRefreshCoordinator deadlines;
    const SnapshotRefreshRequestResult timed = deadlines.Request(
        SnapshotRefreshKind::AccountSummary, 1000, 500);
    assert(timed.dispatch && timed.generation == 1);
    assert(!deadlines.Expire(SnapshotRefreshKind::AccountSummary, 1499).expired);
    const SnapshotRefreshRequestResult timedPending = deadlines.Request(
        SnapshotRefreshKind::AccountSummary, 1200, 500);
    assert(timedPending.coalesced);
    const SnapshotRefreshExpirationResult expired = deadlines.Expire(
        SnapshotRefreshKind::AccountSummary, 1500);
    assert(expired.expired);
    assert(expired.hadPending);
    assert(expired.generation == 1);
    assert(!deadlines.Complete(SnapshotRefreshKind::AccountSummary, 1, 1501).accepted);
    const SnapshotRefreshRequestResult retry = deadlines.Request(
        SnapshotRefreshKind::AccountSummary, 1501, 500);
    assert(retry.dispatch && retry.generation == 2);
    const SnapshotRefreshRequestResult retryPending = deadlines.Request(
        SnapshotRefreshKind::AccountSummary, 1502, 500);
    assert(retryPending.coalesced);
    const SnapshotRefreshCompletionResult retryComplete = deadlines.Complete(
        SnapshotRefreshKind::AccountSummary, 2, 1600);
    assert(retryComplete.accepted && retryComplete.dispatchNext);
    assert(retryComplete.nextGeneration == 3);
    assert(!deadlines.Expire(SnapshotRefreshKind::AccountSummary, 2099).expired);
    assert(deadlines.Expire(SnapshotRefreshKind::AccountSummary, 2100).expired);

    return 0;
}
