#include "../HeptaTrade/agent/decision_lease_manager.h"

#include <atomic>
#include <cassert>
#include <chrono>
#include <cstddef>
#include <iostream>
#include <string>
#include <thread>
#include <vector>

namespace {

DecisionLeaseKey Key(const std::string& instrument = "EUR.USD",
                     const std::string& account = "DU123",
                     const std::string& domain = "IB-PAPER")
{
    DecisionLeaseKey key;
    key.executionDomain = domain;
    key.account = account;
    key.instrument = instrument;
    return key;
}

DecisionLeaseOwner Owner(const std::string& agent, const std::string& session)
{
    DecisionLeaseOwner owner;
    owner.agentId = agent;
    owner.sessionId = session;
    return owner;
}

void TestConcurrentContendersHaveOneWinner()
{
    DecisionLeaseManager::TimePoint now;
    DecisionLeaseManager manager([&]() { return now; });
    const DecisionLeaseKey key = Key();
    const std::size_t count = 16;
    std::vector<DecisionLeaseResult> results(count);
    std::vector<std::thread> threads;
    std::atomic<std::size_t> ready(0);
    std::atomic<bool> start(false);
    for (std::size_t i = 0; i < count; ++i)
    {
        threads.push_back(std::thread([&, i]() {
            ++ready;
            while (!start.load()) std::this_thread::yield();
            results[i] = manager.Acquire(
                key,
                Owner("agent-" + std::to_string(i), "session-" + std::to_string(i)),
                std::chrono::seconds(10));
        }));
    }
    while (ready.load() != count) std::this_thread::yield();
    start.store(true);
    for (std::size_t i = 0; i < threads.size(); ++i) threads[i].join();

    std::size_t winners = 0;
    std::size_t winnerIndex = 0;
    for (std::size_t i = 0; i < results.size(); ++i)
    {
        if (results[i].status == DecisionLeaseStatus::Acquired)
        {
            ++winners;
            winnerIndex = i;
        }
        else
        {
            assert(results[i].status == DecisionLeaseStatus::Busy);
            assert(!results[i].Succeeded());
        }
    }
    assert(winners == 1);
    assert(manager.Validate(
        key,
        Owner("agent-" + std::to_string(winnerIndex), "session-" + std::to_string(winnerIndex)),
        results[winnerIndex].credential).status == DecisionLeaseStatus::Valid);
}

void TestRenewExtendsWithoutChangingFence()
{
    DecisionLeaseManager::TimePoint now;
    DecisionLeaseManager manager([&]() { return now; });
    const DecisionLeaseKey key = Key();
    const DecisionLeaseOwner owner = Owner("agent-a", "session-a");
    const DecisionLeaseResult acquired = manager.Acquire(key, owner, std::chrono::milliseconds(100));
    assert(acquired.status == DecisionLeaseStatus::Acquired);

    now += std::chrono::milliseconds(60);
    const DecisionLeaseResult renewed =
        manager.Renew(key, owner, acquired.credential, std::chrono::milliseconds(100));
    assert(renewed.status == DecisionLeaseStatus::Renewed);
    assert(renewed.credential == acquired.credential);

    now += std::chrono::milliseconds(60);
    assert(manager.Validate(key, owner, acquired.credential).status == DecisionLeaseStatus::Valid);

    DecisionLeaseCredential wrong = acquired.credential;
    ++wrong.fencingToken;
    assert(manager.Renew(key, owner, wrong, std::chrono::milliseconds(100)).status ==
           DecisionLeaseStatus::StaleFence);
}

void TestExpiredTakeoverPermanentlyRejectsOldFence()
{
    DecisionLeaseManager::TimePoint now;
    DecisionLeaseManager manager([&]() { return now; });
    const DecisionLeaseKey key = Key();
    const DecisionLeaseOwner oldOwner = Owner("agent-old", "session-old");
    const DecisionLeaseOwner newOwner = Owner("agent-new", "session-new");
    const DecisionLeaseResult oldLease =
        manager.Acquire(key, oldOwner, std::chrono::milliseconds(50));
    assert(oldLease.status == DecisionLeaseStatus::Acquired);

    now += std::chrono::milliseconds(51);
    assert(manager.Validate(key, oldOwner, oldLease.credential).status == DecisionLeaseStatus::Expired);
    const DecisionLeaseResult newLease =
        manager.Acquire(key, newOwner, std::chrono::milliseconds(50));
    assert(newLease.status == DecisionLeaseStatus::Acquired);
    assert(newLease.credential.generation == oldLease.credential.generation + 1);
    assert(newLease.credential.fencingToken > oldLease.credential.fencingToken);

    assert(manager.Validate(key, newOwner, oldLease.credential).status ==
           DecisionLeaseStatus::StaleFence);
    assert(manager.Validate(key, oldOwner, oldLease.credential).status ==
           DecisionLeaseStatus::OwnerMismatch);
    assert(manager.Release(key, oldOwner, oldLease.credential).status ==
           DecisionLeaseStatus::OwnerMismatch);
    assert(manager.Validate(key, newOwner, newLease.credential).status == DecisionLeaseStatus::Valid);
}

void TestReleaseRequiresOwnerAndFenceAndAdvancesNextGrant()
{
    DecisionLeaseManager::TimePoint now;
    DecisionLeaseManager manager([&]() { return now; });
    const DecisionLeaseKey key = Key();
    const DecisionLeaseOwner owner = Owner("agent-a", "session-a");
    const DecisionLeaseResult first = manager.Acquire(key, owner, std::chrono::seconds(1));
    assert(first.Succeeded());
    assert(manager.Release(key, Owner("agent-b", "session-b"), first.credential).status ==
           DecisionLeaseStatus::OwnerMismatch);
    assert(manager.Release(key, owner, first.credential).status == DecisionLeaseStatus::Released);
    assert(manager.Validate(key, owner, first.credential).status == DecisionLeaseStatus::NotFound);

    const DecisionLeaseResult second = manager.Acquire(key, owner, std::chrono::seconds(1));
    assert(second.status == DecisionLeaseStatus::Acquired);
    assert(second.credential.generation == first.credential.generation + 1);
    assert(second.credential.fencingToken > first.credential.fencingToken);
    assert(manager.Validate(key, owner, first.credential).status == DecisionLeaseStatus::StaleFence);
}

void TestKeysAreIsolatedByAllThreeComponents()
{
    DecisionLeaseManager::TimePoint now;
    DecisionLeaseManager manager([&]() { return now; });
    const DecisionLeaseOwner ownerA = Owner("agent-a", "session-a");
    const DecisionLeaseOwner ownerB = Owner("agent-b", "session-b");

    const DecisionLeaseResult base = manager.Acquire(Key(), ownerA, std::chrono::seconds(1));
    const DecisionLeaseResult otherInstrument =
        manager.Acquire(Key("GBP.USD"), ownerB, std::chrono::seconds(1));
    const DecisionLeaseResult otherAccount =
        manager.Acquire(Key("EUR.USD", "DU456"), ownerB, std::chrono::seconds(1));
    const DecisionLeaseResult otherDomain =
        manager.Acquire(Key("EUR.USD", "DU123", "IB-LIVE"), ownerB, std::chrono::seconds(1));
    assert(base.status == DecisionLeaseStatus::Acquired);
    assert(otherInstrument.status == DecisionLeaseStatus::Acquired);
    assert(otherAccount.status == DecisionLeaseStatus::Acquired);
    assert(otherDomain.status == DecisionLeaseStatus::Acquired);
    assert(manager.Acquire(Key(), ownerB, std::chrono::seconds(1)).status == DecisionLeaseStatus::Busy);
}

void TestInvalidInputsAndClockRollbackFailClosed()
{
    DecisionLeaseManager::TimePoint now;
    DecisionLeaseManager manager([&]() { return now; }, std::chrono::seconds(1));
    DecisionLeaseKey invalid = Key();
    invalid.executionDomain.clear();
    assert(manager.Acquire(invalid, Owner("agent", "session"), std::chrono::milliseconds(10)).status ==
           DecisionLeaseStatus::InvalidArgument);
    assert(manager.Acquire(Key(), Owner("agent", "session"), std::chrono::milliseconds(0)).status ==
           DecisionLeaseStatus::InvalidArgument);
    assert(manager.Acquire(Key(), Owner("agent", "session"), std::chrono::seconds(2)).status ==
           DecisionLeaseStatus::InvalidArgument);

    const DecisionLeaseResult acquired =
        manager.Acquire(Key(), Owner("agent", "session"), std::chrono::milliseconds(100));
    assert(acquired.Succeeded());
    now -= std::chrono::milliseconds(1);
    assert(manager.Validate(Key(), Owner("agent", "session"), acquired.credential).status ==
           DecisionLeaseStatus::ClockFailure);
}

void TestOwnerFenceRevokesEveryInstrument()
{
    DecisionLeaseManager::TimePoint now;
    DecisionLeaseManager manager([&]() { return now; });
    const DecisionLeaseOwner owner = Owner("agent", "session");
    const DecisionLeaseResult eur =
        manager.Acquire(Key("EUR.USD"), owner, std::chrono::seconds(1));
    const DecisionLeaseResult gbp =
        manager.Acquire(Key("GBP.USD"), owner, std::chrono::seconds(1));
    assert(eur.Succeeded());
    assert(gbp.Succeeded());
    assert(manager.FenceOwner(owner) == 2);
    assert(manager.Validate(Key("EUR.USD"), owner, eur.credential).status ==
           DecisionLeaseStatus::NotFound);
    assert(manager.Validate(Key("GBP.USD"), owner, gbp.credential).status ==
           DecisionLeaseStatus::NotFound);
    assert(manager.FenceOwner(owner) == 0);
}

} // namespace

int main()
{
    TestConcurrentContendersHaveOneWinner();
    TestRenewExtendsWithoutChangingFence();
    TestExpiredTakeoverPermanentlyRejectsOldFence();
    TestReleaseRequiresOwnerAndFenceAndAdvancesNextGrant();
    TestKeysAreIsolatedByAllThreeComponents();
    TestInvalidInputsAndClockRollbackFailClosed();
    TestOwnerFenceRevokesEveryInstrument();
    std::cout << "decision_lease_manager_tests: PASS" << std::endl;
    return 0;
}
