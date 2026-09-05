#include "../HeptaTrade/features/feature_generation.h"

#include <atomic>
#include <cassert>
#include <thread>
#include <mutex>
#include <condition_variable>
#include <chrono>
#include <cstdint>
#include <memory>
#include <string>
#include <type_traits>
#include <utility>

static_assert(
    !std::is_copy_constructible<MarketDataSnapshotReceipt>::value,
    "market-data receipts must be move-only");
static_assert(
    !std::is_copy_assignable<MarketDataSnapshotReceipt>::value,
    "market-data receipts must not be copy-assigned");
static_assert(
    std::is_move_constructible<MarketDataSnapshotReceipt>::value,
    "market-data receipts must remain transferable by move");
static_assert(
    !std::is_copy_constructible<MarketDataConsumerBinding>::value,
    "consumer bindings must be move-only");
static_assert(
    !std::is_copy_assignable<MarketDataConsumerBinding>::value,
    "consumer bindings must not be copy-assigned");

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
    event.producer = epoch == 1 ? "feature-feed" : "feature-feed-next";
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

struct Fixture
{
    explicit Fixture(std::size_t featureCapacity = 4096,
                     const std::string& audience = "feature-primary")
        : now(new std::atomic<std::uint64_t>(1200)),
          market(4096, [clock = now]() { return clock->load(); }),
          features(market.BindConsumer(audience), featureCapacity)
    {
        assert(features.MarketAuthority().IsValid());
    }

    std::shared_ptr<std::atomic<std::uint64_t> > now;
    ShardedMarketDataStore market;
    ShardedFeatureStore features;
};

MarketDataSnapshotReceipt Receipt(
    ShardedMarketDataStore& market,
    const MarketDataConsumerBinding& consumer,
    const MarketDataEvent& event)
{
    assert(market.Apply(event).accepted);
    MarketDataSnapshotReceipt receipt;
    std::string reason;
    assert(market.GetRiskReady(
        consumer, {event.venue, event.instrument}, receipt, reason));
    assert(reason.empty());
    assert(receipt.IsValid());
    assert(receipt.IssuedAtMs() != 0);
    assert(receipt.Nonce() != 0);
    return receipt;
}

void TestDeterministicGeneration()
{
    Fixture fixture;
    MarketDataSnapshotReceipt first = Receipt(
        fixture.market, fixture.features.MarketAuthority(), Event(1, 1));
    FeatureWriteResult result = fixture.features.Compute(first);
    assert(result.accepted && !result.duplicate);
    assert(result.featureGeneration == 1);

    FeatureSnapshot output;
    assert(fixture.features.Get(
        {"SIM", "EUR.USD"}, "mid-spread-v1", output));
    assert(output.mid == Fixed("1.15"));
    assert(output.spread == Fixed("0.1"));
    assert(output.inputDigest == first.Snapshot().digest);
    assert(output.digest == result.digest);

    FeatureWriteResult duplicate = fixture.features.Compute(first);
    assert(duplicate.accepted && duplicate.duplicate);
    assert(duplicate.featureGeneration == 1);
    assert(duplicate.digest == result.digest);

    MarketDataSnapshotReceipt second = Receipt(
        fixture.market, fixture.features.MarketAuthority(), Event(1, 2));
    FeatureWriteResult next = fixture.features.Compute(second);
    assert(next.accepted && !next.duplicate);
    assert(next.featureGeneration == 2);
    assert(next.digest != result.digest);
}

void TestCompatibilityAndInputFailures()
{
    ShardedFeatureStore unbound;
    MarketDataSnapshotReceipt missing;
    assert(unbound.Compute(missing).reasonCode ==
           "FEATURE_INPUT_RECEIPT_INVALID");

    MarketDataSnapshot rawMissing;
    assert(unbound.Compute(rawMissing, 1200).reasonCode ==
           "FEATURE_INPUT_RECEIPT_REQUIRED");

    Fixture fixture;
    MarketDataSnapshotReceipt current = Receipt(
        fixture.market, fixture.features.MarketAuthority(), Event(1, 1));
    assert(fixture.features.Compute(current, 1200).reasonCode ==
           "FEATURE_CALLER_TIME_FORBIDDEN");
    assert(fixture.features.Compute(current, "unknown").reasonCode ==
           "FEATURE_SET_UNSUPPORTED");

    MarketDataSnapshot forged = current.Snapshot();
    forged.generation += 100;
    assert(fixture.features.Compute(forged, 1200).reasonCode ==
           "FEATURE_INPUT_RECEIPT_REQUIRED");

    Fixture oddFixture;
    MarketDataEvent oddEvent = Event(1, 1);
    oddEvent.ask = Fixed("1.100001");
    oddEvent.last = Fixed("1.100001");
    MarketDataSnapshotReceipt odd = Receipt(
        oddFixture.market, oddFixture.features.MarketAuthority(), oddEvent);
    assert(oddFixture.features.Compute(odd).reasonCode ==
           "FEATURE_NUMERIC_SCALE_MISMATCH");
}

void TestMoveOnlyReceiptAndAudienceScope()
{
    Fixture firstFixture(4096, "feature-a");
    MarketDataSnapshotReceipt original = Receipt(
        firstFixture.market, firstFixture.features.MarketAuthority(),
        Event(1, 1));
    MarketDataSnapshotReceipt moved(std::move(original));
    assert(!original.IsValid());
    assert(moved.IsValid());
    assert(firstFixture.features.Compute(original).reasonCode ==
           "FEATURE_INPUT_RECEIPT_INVALID");
    assert(firstFixture.features.Compute(moved).accepted);

    MarketDataConsumerBinding secondBinding =
        firstFixture.market.BindConsumer("feature-b");
    ShardedFeatureStore secondFeature(std::move(secondBinding));
    assert(secondFeature.Compute(moved).reasonCode ==
           "FEATURE_INPUT_AUDIENCE_MISMATCH");

    MarketDataSnapshotReceipt forSecond = Receipt(
        firstFixture.market, secondFeature.MarketAuthority(), Event(1, 2));
    assert(firstFixture.features.Compute(forSecond).reasonCode ==
           "FEATURE_INPUT_AUDIENCE_MISMATCH");
    assert(secondFeature.Compute(forSecond).accepted);
}

void TestCrossStoreAndReconstructedStoreRejection()
{
    Fixture canonical(4096, "canonical-feature");
    Fixture attacker(4096, "attacker-feature");

    MarketDataEvent attackerEvent = Event(1, 1);
    attackerEvent.bid = Fixed("9.0");
    attackerEvent.ask = Fixed("9.2");
    attackerEvent.last = Fixed("9.1");
    MarketDataSnapshotReceipt attackerReceipt = Receipt(
        attacker.market, attacker.features.MarketAuthority(), attackerEvent);
    assert(canonical.features.Compute(attackerReceipt).reasonCode ==
           "FEATURE_INPUT_ISSUER_MISMATCH");

    MarketDataSnapshotReceipt canonicalReceipt = Receipt(
        canonical.market, canonical.features.MarketAuthority(), Event(1, 1));
    assert(attacker.features.Compute(canonicalReceipt).reasonCode ==
           "FEATURE_INPUT_ISSUER_MISMATCH");

    // A newly reconstructed store has a different process-local issuer even
    // when its visible state and audience text are identical.
    std::shared_ptr<std::atomic<std::uint64_t> > now(
        new std::atomic<std::uint64_t>(1200));
    ShardedMarketDataStore reconstructed(
        4096, [clock = now]() { return clock->load(); });
    ShardedFeatureStore reconstructedFeature(
        reconstructed.BindConsumer("canonical-feature"));
    MarketDataSnapshotReceipt reconstructedReceipt = Receipt(
        reconstructed, reconstructedFeature.MarketAuthority(), Event(1, 1));
    assert(canonical.features.Compute(reconstructedReceipt).reasonCode ==
           "FEATURE_INPUT_ISSUER_MISMATCH");
}

void TestCurrentStateGapAndGenerationFences()
{
    Fixture fixture;
    MarketDataSnapshotReceipt first = Receipt(
        fixture.market, fixture.features.MarketAuthority(), Event(1, 1));

    assert(fixture.market.Apply(Event(1, 2)).accepted);
    assert(fixture.features.Compute(first).reasonCode ==
           "FEATURE_INPUT_SUPERSEDED");

    MarketDataSnapshotReceipt second;
    std::string reason;
    assert(fixture.market.GetRiskReady(
        fixture.features.MarketAuthority(), {"SIM", "EUR.USD"},
        second, reason));
    MarketDataWriteResult gap = fixture.market.Apply(Event(1, 4));
    assert(gap.accepted && gap.sequenceGap);
    assert(fixture.features.Compute(second).reasonCode ==
           "FEATURE_INPUT_SEQUENCE_GAP");
}

void TestTrustedClockExpiryAndRollback()
{
    Fixture expiry;
    MarketDataSnapshotReceipt receipt = Receipt(
        expiry.market, expiry.features.MarketAuthority(), Event(1, 1));
    expiry.now->store(6000);
    assert(expiry.features.Compute(receipt).reasonCode ==
           "FEATURE_INPUT_STALE");
    assert(expiry.features.Compute(receipt, 1200).reasonCode ==
           "FEATURE_CALLER_TIME_FORBIDDEN");

    Fixture rollback;
    MarketDataSnapshotReceipt rollbackReceipt = Receipt(
        rollback.market, rollback.features.MarketAuthority(), Event(1, 1));
    rollback.now->store(1199);
    assert(rollback.features.Compute(rollbackReceipt).reasonCode ==
           "FEATURE_INPUT_CLOCK_REGRESSION");

    // A rollback faults the complete authority epoch. Restoring the numeric
    // clock value cannot silently reopen it; an explicit lifecycle fence and a
    // fresh audience binding are required.
    rollback.now->store(1200);
    assert(rollback.features.Compute(rollbackReceipt).reasonCode ==
           "FEATURE_INPUT_CLOCK_REGRESSION");
    std::string reason;
    assert(rollback.market.FenceAuthority(reason));
    ShardedFeatureStore recovered(
        rollback.market.BindConsumer("feature-clock-recovered"));
    MarketDataSnapshotReceipt recoveredReceipt;
    assert(rollback.market.GetRiskReady(
        recovered.MarketAuthority(), {"SIM", "EUR.USD"},
        recoveredReceipt, reason));
    assert(recovered.Compute(recoveredReceipt).accepted);
}

void TestFeatureCommitLinearizesAgainstSourceAdvance()
{
    Fixture fixture;
    MarketDataSnapshotReceipt receipt = Receipt(
        fixture.market, fixture.features.MarketAuthority(), Event(1, 1));

    std::mutex gateMutex;
    std::condition_variable gate;
    bool validated = false;
    bool releaseCommit = false;
    fixture.features.SetAuthorityValidatedHookForTesting([&]() {
        std::unique_lock<std::mutex> lock(gateMutex);
        validated = true;
        gate.notify_all();
        gate.wait(lock, [&]() { return releaseCommit; });
    });

    FeatureWriteResult featureResult;
    std::thread compute([&]() {
        featureResult = fixture.features.Compute(receipt);
    });
    {
        std::unique_lock<std::mutex> lock(gateMutex);
        gate.wait(lock, [&]() { return validated; });
    }

    std::atomic<bool> writerFinished(false);
    MarketDataWriteResult writeResult;
    std::thread writer([&]() {
        writeResult = fixture.market.Apply(Event(1, 2));
        writerFinished.store(true);
    });
    std::this_thread::sleep_for(std::chrono::milliseconds(20));
    assert(!writerFinished.load());

    {
        std::lock_guard<std::mutex> lock(gateMutex);
        releaseCommit = true;
    }
    gate.notify_all();
    compute.join();
    writer.join();
    fixture.features.SetAuthorityValidatedHookForTesting(
        std::function<void()>());

    assert(featureResult.accepted);
    assert(writeResult.accepted);
    FeatureSnapshot output;
    std::string reason;
    assert(!fixture.features.GetRiskReady(
        {"SIM", "EUR.USD"}, "mid-spread-v1", output, reason));
    assert(reason == "FEATURE_INPUT_SUPERSEDED");
}

void TestIssuerDestructionAndLifecycleFence()
{
    std::shared_ptr<std::atomic<std::uint64_t> > now(
        new std::atomic<std::uint64_t>(1200));
    std::unique_ptr<ShardedMarketDataStore> market(
        new ShardedMarketDataStore(
            4096, [clock = now]() { return clock->load(); }));
    ShardedFeatureStore feature(market->BindConsumer("feature-destroy"));
    MarketDataSnapshotReceipt receipt = Receipt(
        *market, feature.MarketAuthority(), Event(1, 1));
    market.reset();
    assert(!receipt.IsValid());
    assert(feature.Compute(receipt).reasonCode ==
           "FEATURE_INPUT_ISSUER_DESTROYED");

    Fixture fenced;
    MarketDataSnapshotReceipt old = Receipt(
        fenced.market, fenced.features.MarketAuthority(), Event(1, 1));
    std::string reason;
    assert(fenced.market.FenceAuthority(reason));
    assert(!fenced.features.MarketAuthority().IsValid());
    assert(!old.IsValid());
    assert(fenced.features.Compute(old).reasonCode ==
           "FEATURE_INPUT_AUTHORITY_FENCED");

    ShardedFeatureStore next(
        fenced.market.BindConsumer("feature-after-fence"));
    MarketDataSnapshotReceipt current;
    assert(fenced.market.GetRiskReady(
        next.MarketAuthority(), {"SIM", "EUR.USD"}, current, reason));
    assert(next.Compute(current).accepted);
}

void TestRegressionCapacityAndRiskReadyRevalidation()
{
    Fixture fixture(1);
    MarketDataSnapshotReceipt first = Receipt(
        fixture.market, fixture.features.MarketAuthority(), Event(1, 1));
    assert(fixture.features.Compute(first).accepted);

    FeatureSnapshot output;
    std::string reason;
    assert(fixture.features.GetRiskReady(
        {"SIM", "EUR.USD"}, "mid-spread-v1", output, reason));
    assert(fixture.features.GetRiskReady(
        {"SIM", "EUR.USD"}, "mid-spread-v1", 1200, output, reason) == false);
    assert(reason == "FEATURE_CALLER_TIME_FORBIDDEN");

    MarketDataSnapshotReceipt second = Receipt(
        fixture.market, fixture.features.MarketAuthority(), Event(1, 2));
    assert(!fixture.features.GetRiskReady(
        {"SIM", "EUR.USD"}, "mid-spread-v1", output, reason));
    assert(reason == "FEATURE_INPUT_SUPERSEDED");
    assert(fixture.features.Compute(second).accepted);

    // The older receipt cannot be accepted after the Feature lineage advances.
    assert(fixture.features.Compute(first).reasonCode ==
           "FEATURE_INPUT_SUPERSEDED");

    MarketDataEvent another = Event(1, 1);
    another.instrument = "USD.JPY";
    another.eventId = "feature-event-usdjpy";
    MarketDataSnapshotReceipt other = Receipt(
        fixture.market, fixture.features.MarketAuthority(), another);
    assert(fixture.features.Compute(other).reasonCode ==
           "FEATURE_CAPACITY_EXHAUSTED");

    fixture.now->store(6000);
    assert(!fixture.features.GetRiskReady(
        {"SIM", "EUR.USD"}, "mid-spread-v1", output, reason));
    assert(reason == "FEATURE_INPUT_STALE");
    assert(!fixture.features.GetRiskReady(
        {"SIM", "GBP.USD"}, "mid-spread-v1", output, reason));
    assert(reason == "FEATURE_SNAPSHOT_MISSING");
}
}

int main()
{
    TestDeterministicGeneration();
    TestCompatibilityAndInputFailures();
    TestMoveOnlyReceiptAndAudienceScope();
    TestCrossStoreAndReconstructedStoreRejection();
    TestCurrentStateGapAndGenerationFences();
    TestTrustedClockExpiryAndRollback();
    TestFeatureCommitLinearizesAgainstSourceAdvance();
    TestIssuerDestructionAndLifecycleFence();
    TestRegressionCapacityAndRiskReadyRevalidation();
    return 0;
}
