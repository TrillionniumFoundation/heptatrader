#include "../HeptaTrade/features/feature_generation.h"

#include <cassert>
#include <string>

namespace
{
HeptaFixedDecimal Fixed(const char* value)
{
    HeptaFixedDecimal out;
    std::string reason;
    assert(HeptaFixedDecimal::ParseCanonical(value, out, reason));
    return out;
}

MarketDataEvent Event(std::uint64_t epoch, std::uint64_t sequence)
{
    MarketDataEvent event;
    event.eventId = "feature-event-" + std::to_string(epoch) + "-" +
        std::to_string(sequence);
    event.producer = "feature-feed";
    event.venue = "SIM";
    event.instrument = "EUR.USD";
    event.sourceDigest = std::string("sha256:") + std::string(64, 'b');
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

MarketDataSnapshotReceipt Receipt(
    ShardedMarketDataStore& market,
    const MarketDataEvent& event,
    std::uint64_t issuedAtMs = 1200)
{
    assert(market.Apply(event).accepted);
    MarketDataSnapshotReceipt receipt;
    std::string reason;
    assert(market.GetRiskReady(
        {event.venue, event.instrument}, issuedAtMs, receipt, reason));
    assert(receipt.IsValid());
    return receipt;
}

void TestDeterministicGeneration()
{
    ShardedMarketDataStore market;
    ShardedFeatureStore features;
    MarketDataSnapshotReceipt first = Receipt(market, Event(1, 1));
    FeatureWriteResult result = features.Compute(first, 1200);
    assert(result.accepted && !result.duplicate);
    assert(result.featureGeneration == 1);

    FeatureSnapshot output;
    assert(features.Get({"SIM", "EUR.USD"}, "mid-spread-v1", output));
    assert(output.mid == Fixed("1.15"));
    assert(output.spread == Fixed("0.1"));
    assert(output.inputDigest == first.Snapshot().digest);
    assert(output.digest == result.digest);

    FeatureWriteResult duplicate = features.Compute(first, 1200);
    assert(duplicate.accepted && duplicate.duplicate);
    assert(duplicate.featureGeneration == 1);
    assert(duplicate.digest == result.digest);

    MarketDataSnapshotReceipt second = Receipt(market, Event(1, 2));
    FeatureWriteResult next = features.Compute(second, 1200);
    assert(next.accepted && !next.duplicate);
    assert(next.featureGeneration == 2);
    assert(next.digest != result.digest);
}

void TestAuthorityAndInputFailures()
{
    ShardedFeatureStore features;
    MarketDataSnapshotReceipt missing;
    assert(features.Compute(missing, 1200).reasonCode ==
           "FEATURE_INPUT_RECEIPT_INVALID");

    MarketDataSnapshot rawMissing;
    assert(features.Compute(rawMissing, 1200).reasonCode ==
           "FEATURE_INPUT_RECEIPT_REQUIRED");

    ShardedMarketDataStore market;
    MarketDataSnapshotReceipt current = Receipt(market, Event(1, 1));
    assert(features.Compute(current, 6000).reasonCode ==
           "FEATURE_INPUT_STALE");
    assert(features.Compute(current, 1000).reasonCode ==
           "FEATURE_INPUT_CLOCK_REGRESSION");
    assert(features.Compute(current, 1200, "unknown").reasonCode ==
           "FEATURE_SET_UNSUPPORTED");

    MarketDataWriteResult gapWrite = market.Apply(Event(1, 3));
    assert(gapWrite.accepted && gapWrite.sequenceGap);
    MarketDataSnapshotReceipt gapReceipt;
    std::string reason;
    assert(!market.GetRiskReady(
        {"SIM", "EUR.USD"}, 1200, gapReceipt, reason));
    assert(reason == "MARKET_SEQUENCE_GAP");
    assert(!gapReceipt.IsValid());

    // A raw diagnostic copy can be edited and can even remain structurally
    // valid because derived store state is not an authority envelope. Feature
    // must therefore reject the raw value regardless of its visible fields.
    MarketDataSnapshot forged;
    assert(market.Get({"SIM", "EUR.USD"}, forged));
    assert(forged.sequenceGap);
    forged.sequenceGap = false;
    forged.generation += 100;
    assert(ShardedMarketDataStore::ValidateSnapshot(forged, reason));
    assert(features.Compute(forged, 1200).reasonCode ==
           "FEATURE_INPUT_RECEIPT_REQUIRED");

    ShardedMarketDataStore oddMarket;
    MarketDataEvent oddEvent = Event(1, 1);
    oddEvent.ask = Fixed("1.100001");
    oddEvent.last = Fixed("1.100001");
    MarketDataSnapshotReceipt odd = Receipt(oddMarket, oddEvent);
    assert(features.Compute(odd, 1200).reasonCode ==
           "FEATURE_NUMERIC_SCALE_MISMATCH");
}

void TestRegressionAndCapacity()
{
    ShardedMarketDataStore market;
    ShardedFeatureStore features(1);
    MarketDataSnapshotReceipt first = Receipt(market, Event(1, 1));
    assert(features.Compute(first, 1200).accepted);
    MarketDataSnapshotReceipt second = Receipt(market, Event(1, 2));
    assert(features.Compute(second, 1200).accepted);
    assert(features.Compute(first, 1200).reasonCode ==
           "FEATURE_INPUT_REGRESSION");

    MarketDataEvent another = Event(1, 1);
    another.instrument = "USD.JPY";
    another.eventId = "feature-event-usdjpy";
    MarketDataSnapshotReceipt other = Receipt(market, another);
    assert(features.Compute(other, 1200).reasonCode ==
           "FEATURE_CAPACITY_EXHAUSTED");
}

void TestRiskReady()
{
    ShardedMarketDataStore market;
    ShardedFeatureStore features;
    MarketDataSnapshotReceipt input = Receipt(market, Event(1, 1));
    assert(features.Compute(input, 1200).accepted);
    FeatureSnapshot output;
    std::string reason;
    assert(features.GetRiskReady(
        {"SIM", "EUR.USD"}, "mid-spread-v1", 1200, output, reason));
    assert(!features.GetRiskReady(
        {"SIM", "EUR.USD"}, "mid-spread-v1", 6000, output, reason));
    assert(reason == "FEATURE_SNAPSHOT_STALE");
    assert(!features.GetRiskReady(
        {"SIM", "GBP.USD"}, "mid-spread-v1", 1200, output, reason));
    assert(reason == "FEATURE_SNAPSHOT_MISSING");
}
}

int main()
{
    TestDeterministicGeneration();
    TestAuthorityAndInputFailures();
    TestRegressionAndCapacity();
    TestRiskReady();
    return 0;
}
