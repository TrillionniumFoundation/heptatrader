#include "../HeptaTrade/marketdata/sharded_market_data.h"

#include <cassert>
#include <string>
#include <thread>
#include <vector>

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

void TestFreshnessAndValidation()
{
    ShardedMarketDataStore store;
    MarketDataEvent event = Event(1, 1);
    assert(store.Apply(event).accepted);
    MarketDataSnapshot snapshot;
    std::string reason;
    assert(!store.GetRiskReady({"SIM", "EUR.USD"}, 1000, snapshot, reason));
    assert(reason == "MARKET_CLOCK_REGRESSION");
    assert(!store.GetRiskReady({"SIM", "EUR.USD"}, 6000, snapshot, reason));
    assert(reason == "MARKET_SNAPSHOT_STALE");

    event = Event(1, 2);
    event.sourceDigest = "bad";
    assert(store.Apply(event).reasonCode == "MARKET_SOURCE_DIGEST_INVALID");
    event = Event(1, 2);
    event.capturedAtMs = event.observedAtMs - 1;
    assert(store.Apply(event).reasonCode == "MARKET_TIME_ENVELOPE_INVALID");
    event = Event(1, 2);
    event.ask = Fixed("1.0");
    assert(store.Apply(event).reasonCode == "MARKET_QUOTE_INVALID");
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
    TestFreshnessAndValidation();
    TestCapacityAndVector();
    TestIndependentShardProgress();
    return 0;
}
