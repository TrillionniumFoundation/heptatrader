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

MarketDataSnapshot Snapshot(
    ShardedMarketDataStore& market,
    const MarketDataEvent& event)
{
    assert(market.Apply(event).accepted);
    MarketDataSnapshot snapshot;
    assert(market.Get({event.venue, event.instrument}, snapshot));
    return snapshot;
}

void TestDeterministicGeneration()
{
    ShardedMarketDataStore market;
    ShardedFeatureStore features;
    MarketDataSnapshot first = Snapshot(market, Event(1, 1));
    FeatureWriteResult result = features.Compute(first, 1200);
    assert(result.accepted && !result.duplicate);
    assert(result.featureGeneration == 1);

    FeatureSnapshot output;
    assert(features.Get({"SIM", "EUR.USD"}, "mid-spread-v1", output));
    assert(output.mid == Fixed("1.15"));
    assert(output.spread == Fixed("0.1"));
    assert(output.inputDigest == first.digest);
    assert(output.digest == result.digest);

    FeatureWriteResult duplicate = features.Compute(first, 1200);
    assert(duplicate.accepted && duplicate.duplicate);
    assert(duplicate.featureGeneration == 1);
    assert(duplicate.digest == result.digest);

    MarketDataSnapshot second = Snapshot(market, Event(1, 2));
    FeatureWriteResult next = features.Compute(second, 1200);
    assert(next.accepted && !next.duplicate);
    assert(next.featureGeneration == 2);
    assert(next.digest != result.digest);
}

void TestInputFailures()
{
    ShardedFeatureStore features;
    MarketDataSnapshot missing;
    assert(features.Compute(missing, 1200).reasonCode ==
           "FEATURE_INPUT_INCOMPLETE");

    ShardedMarketDataStore market;
    MarketDataSnapshot stale = Snapshot(market, Event(1, 1));
    assert(features.Compute(stale, 6000).reasonCode == "FEATURE_INPUT_STALE");
    assert(features.Compute(stale, 1000).reasonCode ==
           "FEATURE_INPUT_CLOCK_REGRESSION");

    MarketDataEvent gapEvent = Event(1, 3);
    MarketDataSnapshot gap = Snapshot(market, gapEvent);
    assert(gap.sequenceGap);
    assert(features.Compute(gap, 1200).reasonCode ==
           "FEATURE_INPUT_SEQUENCE_GAP");

    MarketDataSnapshot odd = stale;
    odd.ask = Fixed("1.100001");
    assert(features.Compute(odd, 1200).reasonCode ==
           "FEATURE_INPUT_INVALID");
    MarketDataSnapshot forged = stale;
    forged.digest = std::string("sha256:") + std::string(64, 'f');
    assert(features.Compute(forged, 1200).reasonCode ==
           "FEATURE_INPUT_INVALID");
    assert(features.Compute(stale, 1200, "unknown").reasonCode ==
           "FEATURE_SET_UNSUPPORTED");
}

void TestRegressionAndCapacity()
{
    ShardedMarketDataStore market;
    ShardedFeatureStore features(1);
    MarketDataSnapshot first = Snapshot(market, Event(1, 1));
    assert(features.Compute(first, 1200).accepted);
    MarketDataSnapshot second = Snapshot(market, Event(1, 2));
    assert(features.Compute(second, 1200).accepted);
    assert(features.Compute(first, 1200).reasonCode ==
           "FEATURE_INPUT_REGRESSION");

    MarketDataEvent another = Event(1, 1);
    another.instrument = "USD.JPY";
    another.eventId = "feature-event-usdjpy";
    MarketDataSnapshot other = Snapshot(market, another);
    assert(features.Compute(other, 1200).reasonCode ==
           "FEATURE_CAPACITY_EXHAUSTED");
}

void TestRiskReady()
{
    ShardedMarketDataStore market;
    ShardedFeatureStore features;
    MarketDataSnapshot input = Snapshot(market, Event(1, 1));
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
    TestInputFailures();
    TestRegressionAndCapacity();
    TestRiskReady();
    return 0;
}
