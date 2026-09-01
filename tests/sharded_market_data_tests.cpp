#include "../HeptaTrade/marketdata/sharded_market_data.h"

#include <atomic>
#include <cassert>
#include <chrono>
#include <condition_variable>
#include <memory>
#include <mutex>
#include <string>
#include <thread>
#include <type_traits>
#include <utility>
#include <vector>

static_assert(
    !std::is_constructible<MarketDataSnapshotReceipt,
                           MarketDataSnapshot>::value,
    "raw market-data snapshots must not construct authority receipts");
static_assert(
    !std::is_copy_constructible<MarketDataSnapshotReceipt>::value,
    "authority receipts must be move-only");
static_assert(
    !std::is_copy_assignable<MarketDataSnapshotReceipt>::value,
    "authority receipts must not be copied");
static_assert(
    !std::is_copy_constructible<MarketDataConsumerBinding>::value,
    "consumer bindings must be move-only");

namespace
{
HeptaFixedDecimal Fixed(const char* value)
{
    HeptaFixedDecimal out;
    std::string reason;
    assert(HeptaFixedDecimal::ParseCanonical(value, out, reason));
    return out;
}

std::string Digest(char value)
{
    return std::string("sha256:") + std::string(64, value);
}

MarketDataEvent Event(std::uint64_t epoch, std::uint64_t sequence)
{
    MarketDataEvent event;
    event.eventId = "event-" + std::to_string(epoch) + "-" +
        std::to_string(sequence);
    event.producer = "feed-a";
    event.venue = "SIM";
    event.instrument = "EUR.USD";
    event.sourceDigest = Digest('a');
    event.producerEpoch = epoch;
    event.sequence = sequence;
    event.observedAtMs = 1000 + sequence;
    event.capturedAtMs = 1100 + sequence;
    event.freshUntilMs = 5000 + sequence;
    event.bid = Fixed("1.1");
    event.ask = Fixed("1.2");
    event.last = Fixed("1.15");
    event.bidSize = Fixed("10");
    event.askSize = Fixed("12");
    return event;
}

void TestOrderingAndEpochs()
{
    ShardedMarketDataStore store;
    MarketDataWriteResult first = store.Apply(Event(1, 1));
    assert(first.accepted && !first.duplicate && !first.sequenceGap);
    assert(first.generation == 1);

    MarketDataWriteResult duplicate = store.Apply(Event(1, 1));
    assert(duplicate.accepted && duplicate.duplicate);
    assert(duplicate.generation == 1);

    MarketDataEvent conflict = Event(1, 1);
    conflict.ask = Fixed("1.3");
    assert(store.Apply(conflict).reasonCode == "MARKET_SEQUENCE_CONFLICT");

    MarketDataEvent writer = Event(1, 2);
    writer.producer = "feed-b";
    assert(store.Apply(writer).reasonCode == "MARKET_WRITER_CONFLICT");

    MarketDataWriteResult gap = store.Apply(Event(1, 3));
    assert(gap.accepted && gap.sequenceGap && gap.generation == 2);
    MarketDataSnapshot snapshot;
    std::string reason;
    assert(!store.GetRiskReady({"SIM", "EUR.USD"}, 1200, snapshot, reason));
    assert(reason == "MARKET_SEQUENCE_GAP");

    assert(store.Apply(Event(1, 2)).reasonCode == "MARKET_SEQUENCE_STALE");
    MarketDataEvent badEpochStart = Event(2, 2);
    assert(store.Apply(badEpochStart).reasonCode ==
           "MARKET_EPOCH_START_SEQUENCE_INVALID");

    MarketDataEvent newEpoch = Event(2, 1);
    newEpoch.producer = "feed-b";
    MarketDataWriteResult reset = store.Apply(newEpoch);
    assert(reset.accepted && !reset.sequenceGap && reset.generation == 3);
    assert(store.GetRiskReady({"SIM", "EUR.USD"}, 1200, snapshot, reason));
    assert(snapshot.producer == "feed-b");
    assert(snapshot.producerEpoch == 2 && snapshot.sequence == 1);
    assert(store.Apply(Event(1, 4)).reasonCode == "MARKET_EPOCH_STALE");
}

void TestReceiptAuthority()
{
    std::shared_ptr<std::atomic<std::uint64_t> > now(
        new std::atomic<std::uint64_t>(1200));
    ShardedMarketDataStore store(
        4096, [clock = now]() { return clock->load(); });
    MarketDataConsumerBinding consumer = store.BindConsumer("feature-test");
    assert(consumer.IsValid());

    MarketDataSnapshotReceipt receipt;
    assert(!receipt.IsValid());
    assert(store.Apply(Event(1, 1)).accepted);
    std::string reason;
    assert(store.GetRiskReady(
        consumer, {"SIM", "EUR.USD"}, receipt, reason));
    assert(receipt.IsValid());
    assert(receipt.Snapshot().generation == 1);
    assert(!receipt.Snapshot().sequenceGap);
    assert(receipt.IssuedAtMs() == 1200);
    assert(receipt.Nonce() != 0);

    MarketDataSnapshot diagnostic = receipt.Snapshot();
    diagnostic.generation += 100;
    diagnostic.sequenceGap = true;
    assert(receipt.Snapshot().generation == 1);
    assert(!receipt.Snapshot().sequenceGap);

    MarketDataSnapshotReceipt moved(std::move(receipt));
    assert(!receipt.IsValid());
    assert(moved.IsValid());

    // Caller-selected time cannot issue a risk-ready capability.
    MarketDataSnapshotReceipt callerTimed;
    assert(!store.GetRiskReady(
        {"SIM", "EUR.USD"}, 1200, callerTimed, reason));
    assert(reason == "MARKET_AUTHORITY_CONSUMER_BINDING_REQUIRED");
    assert(!callerTimed.IsValid());

    MarketDataWriteResult gap = store.Apply(Event(1, 3));
    assert(gap.accepted && gap.sequenceGap);
    MarketDataSnapshotReceipt blocked;
    assert(!store.GetRiskReady(
        consumer, {"SIM", "EUR.USD"}, blocked, reason));
    assert(reason == "MARKET_SEQUENCE_GAP");
    assert(!blocked.IsValid());

    MarketDataSnapshot rawGap;
    assert(store.Get({"SIM", "EUR.USD"}, rawGap));
    assert(rawGap.sequenceGap);
    rawGap.sequenceGap = false;
    rawGap.generation += 100;
    assert(ShardedMarketDataStore::ValidateSnapshot(rawGap, reason));

    MarketDataEvent reset = Event(2, 1);
    reset.producer = "feed-b";
    assert(store.Apply(reset).accepted);
    assert(store.GetRiskReady(
        consumer, {"SIM", "EUR.USD"}, blocked, reason));
    assert(blocked.IsValid());
    assert(blocked.Snapshot().producerEpoch == 2);
    assert(blocked.Snapshot().generation == 3);

    assert(store.FenceAuthority(reason));
    assert(!consumer.IsValid());
    assert(!blocked.IsValid());
    MarketDataSnapshotReceipt fenced;
    assert(!store.GetRiskReady(
        consumer, {"SIM", "EUR.USD"}, fenced, reason));
    assert(reason == "MARKET_AUTHORITY_EPOCH_MISMATCH");

    MarketDataConsumerBinding next = store.BindConsumer("feature-next");
    assert(next.IsValid());
    assert(store.GetRiskReady(
        next, {"SIM", "EUR.USD"}, fenced, reason));
    assert(fenced.IsValid());
}

void TestFreshnessAndValidation()
{
    ShardedMarketDataStore diagnosticStore;
    MarketDataEvent event = Event(1, 1);
    assert(diagnosticStore.Apply(event).accepted);
    MarketDataSnapshot snapshot;
    std::string reason;
    assert(!diagnosticStore.GetRiskReady(
        {"SIM", "EUR.USD"}, 1000, snapshot, reason));
    assert(reason == "MARKET_CLOCK_REGRESSION");
    assert(!diagnosticStore.GetRiskReady(
        {"SIM", "EUR.USD"}, 6000, snapshot, reason));
    assert(reason == "MARKET_SNAPSHOT_STALE");

    std::shared_ptr<std::atomic<std::uint64_t> > now(
        new std::atomic<std::uint64_t>(1000));
    ShardedMarketDataStore authority(
        4096, [clock = now]() { return clock->load(); });
    assert(authority.Apply(Event(1, 1)).accepted);
    MarketDataConsumerBinding consumer = authority.BindConsumer("feature-time");
    MarketDataSnapshotReceipt receipt;
    assert(!authority.GetRiskReady(
        consumer, {"SIM", "EUR.USD"}, receipt, reason));
    assert(!receipt.IsValid());
    assert(reason == "MARKET_AUTHORITY_CLOCK_REGRESSION");

    now->store(1200);
    assert(authority.GetRiskReady(
        consumer, {"SIM", "EUR.USD"}, receipt, reason));
    assert(receipt.IsValid());
    now->store(6000);
    MarketDataSnapshotReceipt stale;
    assert(!authority.GetRiskReady(
        consumer, {"SIM", "EUR.USD"}, stale, reason));
    assert(!stale.IsValid());
    assert(reason == "MARKET_SNAPSHOT_STALE");

    // Once trusted time advances it may not be rolled back by a caller/test
    // clock without closing the authority gate.
    now->store(5999);
    assert(!authority.GetRiskReady(
        consumer, {"SIM", "EUR.USD"}, stale, reason));
    assert(reason == "MARKET_AUTHORITY_CLOCK_REGRESSION");

    event = Event(1, 2);
    event.sourceDigest = "bad";
    assert(diagnosticStore.Apply(event).reasonCode ==
           "MARKET_SOURCE_DIGEST_INVALID");
    event = Event(1, 2);
    event.capturedAtMs = event.observedAtMs - 1;
    assert(diagnosticStore.Apply(event).reasonCode ==
           "MARKET_TIME_ENVELOPE_INVALID");
    event = Event(1, 2);
    event.ask = Fixed("1.0");
    assert(diagnosticStore.Apply(event).reasonCode ==
           "MARKET_QUOTE_INVALID");
}

void TestCapacityAndVector()
{
    ShardedMarketDataStore store(2);
    MarketDataEvent first = Event(1, 1);
    assert(store.Apply(first).accepted);
    MarketDataEvent second = Event(1, 1);
    second.instrument = "USD.JPY";
    second.eventId = "event-usdjpy";
    assert(store.Apply(second).accepted);
    MarketDataEvent third = Event(1, 1);
    third.instrument = "GBP.USD";
    third.eventId = "event-gbpusd";
    assert(store.Apply(third).reasonCode == "MARKET_CAPACITY_EXHAUSTED");

    MarketDataSnapshotVector vectorA;
    MarketDataSnapshotVector vectorB;
    std::string reason;
    std::vector<MarketDataKey> keysA;
    keysA.push_back({"SIM", "USD.JPY"});
    keysA.push_back({"SIM", "EUR.USD"});
    assert(store.ReadVector(keysA, 1200, vectorA, reason));
    std::vector<MarketDataKey> keysB;
    keysB.push_back({"SIM", "EUR.USD"});
    keysB.push_back({"SIM", "USD.JPY"});
    assert(store.ReadVector(keysB, 1200, vectorB, reason));
    assert(vectorA.digest == vectorB.digest);
    assert(vectorA.components[0].key.instrument == "EUR.USD");

    keysB.push_back({"SIM", "USD.JPY"});
    assert(!store.ReadVector(keysB, 1200, vectorB, reason));
    assert(reason == "MARKET_VECTOR_DUPLICATE_KEY");
}

void TestVectorIsOneCoherentCut()
{
    ShardedMarketDataStore store(8);
    MarketDataEvent first = Event(1, 1);
    MarketDataEvent second = Event(1, 1);
    second.instrument = "VECTOR.B";
    while (ShardedMarketDataStore::ShardFor(
               {second.venue, second.instrument}) ==
           ShardedMarketDataStore::ShardFor(
               {first.venue, first.instrument}))
        second.instrument.push_back('X');
    second.eventId = "vector-second";
    assert(store.Apply(first).accepted);
    assert(store.Apply(second).accepted);

    std::mutex gateMutex;
    std::condition_variable gate;
    bool locked = false;
    bool release = false;
    store.SetReadVectorLocksAcquiredHookForTesting([&]() {
        std::unique_lock<std::mutex> lock(gateMutex);
        locked = true;
        gate.notify_all();
        gate.wait(lock, [&]() { return release; });
    });

    MarketDataSnapshotVector vector;
    std::string reason;
    std::thread reader([&]() {
        assert(store.ReadVector(
            {{first.venue, first.instrument},
             {second.venue, second.instrument}},
            1200, vector, reason));
    });
    {
        std::unique_lock<std::mutex> lock(gateMutex);
        gate.wait(lock, [&]() { return locked; });
    }

    std::atomic<bool> writerFinished(false);
    std::thread writer([&]() {
        MarketDataEvent update = first;
        update.eventId = "event-1-2";
        update.sequence = 2;
        update.observedAtMs = 1002;
        update.capturedAtMs = 1102;
        update.freshUntilMs = 5002;
        assert(store.Apply(update).accepted);
        writerFinished.store(true);
    });
    std::this_thread::sleep_for(std::chrono::milliseconds(20));
    assert(!writerFinished.load());
    {
        std::lock_guard<std::mutex> lock(gateMutex);
        release = true;
    }
    gate.notify_all();
    reader.join();
    writer.join();
    store.SetReadVectorLocksAcquiredHookForTesting(std::function<void()>());

    assert(vector.components.size() == 2);
    for (std::size_t i = 0; i < vector.components.size(); ++i)
        assert(vector.components[i].sequence == 1);

    MarketDataSnapshotVector after;
    assert(store.ReadVector(
        {{first.venue, first.instrument},
         {second.venue, second.instrument}},
        1200, after, reason));
    bool sawUpdated = false;
    for (std::size_t i = 0; i < after.components.size(); ++i)
        if (after.components[i].key.instrument == first.instrument)
            sawUpdated = after.components[i].sequence == 2;
    assert(sawUpdated);
}

void TestIndependentShardProgress()
{
    ShardedMarketDataStore store(64);
    std::vector<std::thread> workers;
    for (int index = 0; index < 32; ++index)
    {
        workers.push_back(std::thread([&store, index]() {
            MarketDataEvent event = Event(1, 1);
            event.instrument = "SYM." + std::to_string(index);
            event.eventId = "event-" + std::to_string(index);
            assert(store.Apply(event).accepted);
        }));
    }
    for (std::size_t i = 0; i < workers.size(); ++i) workers[i].join();
    assert(store.Size() == 32);
}
}

int main()
{
    TestOrderingAndEpochs();
    TestReceiptAuthority();
    TestFreshnessAndValidation();
    TestCapacityAndVector();
    TestVectorIsOneCoherentCut();
    TestIndependentShardProgress();
    return 0;
}
