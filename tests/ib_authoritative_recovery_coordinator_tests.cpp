#include "state/ib_authoritative_recovery_coordinator.h"

#include <cassert>
#include <cstdint>
#include <vector>

namespace {

struct FakeDriver
{
    int snapshotCalls[3] = {0, 0, 0};
    int snapshotFailuresRemaining[3] = {0, 0, 0};
    int quoteCalls = 0;
    int quoteFailuresRemaining = 0;
    std::uint64_t lastQuoteGeneration = 0;
    std::vector<std::uint64_t> abortedQuotes;

    static int Index(SnapshotRefreshKind kind)
    {
        if (kind == SnapshotRefreshKind::Positions) return 1;
        if (kind == SnapshotRefreshKind::OpenOrders) return 2;
        return 0;
    }

    bool DispatchSnapshot(SnapshotRefreshKind kind)
    {
        const int index = Index(kind);
        ++snapshotCalls[index];
        if (snapshotFailuresRemaining[index] <= 0) return true;
        --snapshotFailuresRemaining[index];
        return false;
    }

    bool DispatchQuotes(std::uint64_t, std::uint64_t generation, std::uint64_t)
    {
        ++quoteCalls;
        lastQuoteGeneration = generation;
        if (quoteFailuresRemaining <= 0) return true;
        --quoteFailuresRemaining;
        return false;
    }
};

IBAuthoritativeRecoveryCoordinator MakeCoordinator(FakeDriver& driver,
                                                   std::uint64_t snapshotTimeoutMs = 100,
                                                   std::uint64_t quoteTimeoutMs = 50)
{
    IBAuthoritativeRecoveryPolicy policy;
    policy.snapshotTimeoutMs = snapshotTimeoutMs;
    policy.quoteTimeoutMs = quoteTimeoutMs;
    policy.maxAttempts = 3;
    policy.initialBackoffMs = 10;
    policy.maxBackoffMs = 40;
    IBAuthoritativeRecoveryCallbacks callbacks;
    callbacks.beginSnapshot = [](SnapshotRefreshKind, std::uint64_t) {};
    callbacks.abortSnapshot = [](SnapshotRefreshKind, std::uint64_t) {};
    callbacks.dispatchSnapshot = [&driver](SnapshotRefreshKind kind) {
        return driver.DispatchSnapshot(kind);
    };
    callbacks.dispatchQuotes = [&driver](std::uint64_t epoch,
                                         std::uint64_t generation,
                                         std::uint64_t observedAtMs) {
        return driver.DispatchQuotes(epoch, generation, observedAtMs);
    };
    callbacks.abortQuotes = [&driver](std::uint64_t generation) {
        driver.abortedQuotes.push_back(generation);
    };
    return IBAuthoritativeRecoveryCoordinator(policy, callbacks);
}

void CompleteAllSnapshots(IBAuthoritativeRecoveryCoordinator& coordinator,
                          std::uint64_t observedAtMs)
{
    const SnapshotRefreshKind kinds[] = {
        SnapshotRefreshKind::AccountSummary,
        SnapshotRefreshKind::Positions,
        SnapshotRefreshKind::OpenOrders
    };
    for (std::size_t i = 0; i < sizeof(kinds) / sizeof(kinds[0]); ++i)
    {
        const std::uint64_t generation = coordinator.CurrentSnapshotGeneration(kinds[i]);
        assert(generation != 0);
        assert(coordinator.CompleteSnapshot(kinds[i], generation, true, observedAtMs).accepted);
    }
}

}

int main()
{
    FakeDriver driver;
    IBAuthoritativeRecoveryCoordinator coordinator = MakeCoordinator(driver);
    IBAuthoritativeRecoveryStartResult started =
        coordinator.StartFullRecovery(11, 1000, "startup");
    assert(started.accepted && started.allDispatched && !started.exhausted);
    const std::uint64_t firstAccount = coordinator.CurrentSnapshotGeneration(
        SnapshotRefreshKind::AccountSummary);
    const std::uint64_t firstQuote = driver.lastQuoteGeneration;
    started = coordinator.StartFullRecovery(11, 1001, "overflow");
    assert(started.accepted);
    assert(driver.abortedQuotes.size() == 1 && driver.abortedQuotes[0] == firstQuote);
    assert(driver.lastQuoteGeneration > firstQuote);

    IBAuthoritativeRecoveryCompletionResult firstCompletion =
        coordinator.CompleteSnapshot(SnapshotRefreshKind::AccountSummary,
            firstAccount, true, 1002);
    assert(firstCompletion.accepted && firstCompletion.dispatchedNext);
    const std::uint64_t secondAccount = coordinator.CurrentSnapshotGeneration(
        SnapshotRefreshKind::AccountSummary);
    assert(secondAccount > firstAccount);
    assert(coordinator.CompleteSnapshot(SnapshotRefreshKind::AccountSummary,
        secondAccount, true, 1003).accepted);
    const SnapshotRefreshKind remaining[] = {
        SnapshotRefreshKind::Positions,
        SnapshotRefreshKind::OpenOrders
    };
    for (std::size_t i = 0; i < sizeof(remaining) / sizeof(remaining[0]); ++i)
    {
        const std::uint64_t first = coordinator.CurrentSnapshotGeneration(remaining[i]);
        const IBAuthoritativeRecoveryCompletionResult completion =
            coordinator.CompleteSnapshot(remaining[i], first, true, 1004 + i);
        assert(completion.accepted && completion.dispatchedNext);
        const std::uint64_t second = coordinator.CurrentSnapshotGeneration(remaining[i]);
        assert(coordinator.CompleteSnapshot(remaining[i], second, true, 1006 + i).accepted);
    }
    assert(coordinator.CompleteQuotes(driver.lastQuoteGeneration, true, 1010).accepted);
    assert(coordinator.ReadyToRestore());
    assert(coordinator.MarkRestored());
    assert(!coordinator.GetSnapshot().pending);
    coordinator.AbortAll("disconnect");
    assert(driver.abortedQuotes.size() == 2);

    FakeDriver retryDriver;
    retryDriver.snapshotFailuresRemaining[0] = 2;
    IBAuthoritativeRecoveryCoordinator retry = MakeCoordinator(retryDriver);
    assert(retry.StartFullRecovery(12, 2000, "startup").accepted);
    assert(retryDriver.snapshotCalls[0] == 1);
    IBAuthoritativeRecoverySnapshot retrySnapshot = retry.GetSnapshot();
    const IBAuthoritativeRecoveryDomainSnapshot& retryAccount = retrySnapshot.domains[
        static_cast<std::size_t>(IBAuthoritativeRecoveryDomain::AccountSummary)];
    assert(retryAccount.retryScheduled);
    assert(retryAccount.nextRetryAtMs == 2010);
    assert(retryAccount.totalDispatchAttempts == 1);
    assert(retryAccount.lastFailure == "SNAPSHOT_DISPATCH_FAILED:startup");
    assert(!retry.Poll(2009).retryAttempted);
    assert(retry.Poll(2010).retryAttempted);
    assert(retryDriver.snapshotCalls[0] == 2);
    assert(retry.Poll(2030).retryAttempted);
    assert(retryDriver.snapshotCalls[0] == 3);
    CompleteAllSnapshots(retry, 2040);
    assert(retry.CompleteQuotes(retryDriver.lastQuoteGeneration, true, 2041).accepted);
    assert(retry.ReadyToRestore());

    FakeDriver exhaustedDriver;
    exhaustedDriver.snapshotFailuresRemaining[0] = 10;
    IBAuthoritativeRecoveryCoordinator exhausted = MakeCoordinator(exhaustedDriver);
    exhausted.StartFullRecovery(13, 3000, "startup");
    exhausted.Poll(3010);
    const IBAuthoritativeRecoveryPollResult exhaustedPoll = exhausted.Poll(3030);
    assert(exhaustedPoll.exhausted);
    const IBAuthoritativeRecoverySnapshot exhaustedSnapshot = exhausted.GetSnapshot();
    assert(exhaustedSnapshot.domains[
        static_cast<std::size_t>(IBAuthoritativeRecoveryDomain::AccountSummary)].exhausted);
    assert(exhaustedSnapshot.domains[
        static_cast<std::size_t>(IBAuthoritativeRecoveryDomain::AccountSummary)].totalDispatchAttempts == 3);

    FakeDriver timeoutDriver;
    IBAuthoritativeRecoveryCoordinator timeout = MakeCoordinator(timeoutDriver);
    timeout.StartFullRecovery(14, 4000, "startup");
    const IBAuthoritativeRecoveryPollResult timeoutPoll = timeout.Poll(4100);
    assert(timeoutPoll.unsafeSnapshotTimeout);

    FakeDriver quoteTimeoutDriver;
    IBAuthoritativeRecoveryCoordinator quoteTimeout = MakeCoordinator(quoteTimeoutDriver);
    quoteTimeout.StartFullRecovery(15, 5000, "startup");
    CompleteAllSnapshots(quoteTimeout, 5010);
    const IBAuthoritativeRecoveryPollResult quoteExpired = quoteTimeout.Poll(5050);
    assert(quoteExpired.quoteExpired);
    assert(!quoteExpired.exhausted);
    assert(quoteTimeout.Poll(5060).retryAttempted);
    assert(quoteTimeoutDriver.quoteCalls == 2);
    assert(quoteTimeout.CompleteQuotes(
        quoteTimeoutDriver.lastQuoteGeneration, true, 5061).accepted);
    assert(quoteTimeout.ReadyToRestore());

    FakeDriver rejectionDriver;
    IBAuthoritativeRecoveryCoordinator rejection = MakeCoordinator(rejectionDriver);
    rejection.StartFullRecovery(16, 6000, "startup");
    for (int failure = 0; failure < 3; ++failure)
    {
        const std::uint64_t generation = rejection.CurrentSnapshotGeneration(
            SnapshotRefreshKind::AccountSummary);
        assert(generation != 0);
        rejection.RequestSnapshot(SnapshotRefreshKind::AccountSummary,
            6001 + failure * 2, "coalesced_probe");
        const IBAuthoritativeRecoveryCompletionResult rejected =
            rejection.CompleteSnapshot(SnapshotRefreshKind::AccountSummary,
                generation, false, 6002 + failure * 2, "INVALID_ACCOUNT_SNAPSHOT");
        assert(rejected.accepted);
        if (failure < 2) assert(rejected.dispatchedNext);
        else assert(rejected.exhausted);
    }
    assert(rejection.GetSnapshot().domains[
        static_cast<std::size_t>(IBAuthoritativeRecoveryDomain::AccountSummary)].exhausted);
    return 0;
}
