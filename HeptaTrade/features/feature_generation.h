#pragma once

#include "../marketdata/sharded_market_data.h"

#include <array>
#include <atomic>
#include <cstddef>
#include <cstdint>
#include <map>
#include <mutex>
#include <string>

struct FeatureSnapshot
{
    bool found = false;
    MarketDataKey key;
    std::string featureSetId;
    std::string inputDigest;
    std::uint64_t inputEpoch = 0;
    std::uint64_t inputSequence = 0;
    std::uint64_t inputGeneration = 0;
    std::uint64_t featureGeneration = 0;
    std::uint64_t observedAtMs = 0;
    std::uint64_t freshUntilMs = 0;
    HeptaFixedDecimal mid;
    HeptaFixedDecimal spread;
    std::string digest;
};

struct FeatureWriteResult
{
    bool accepted = false;
    bool duplicate = false;
    std::uint64_t featureGeneration = 0;
    std::string reasonCode;
    std::string digest;
};

class ShardedFeatureStore
{
public:
    static const std::size_t kShardCount = 64;

    // Compatibility constructor. It creates no authority and therefore every
    // risk-sensitive operation fails closed until callers migrate to an exact
    // MarketDataConsumerBinding.
    explicit ShardedFeatureStore(std::size_t maximumKeys = 4096);

    // Canonical constructor. The move-only binding is consumed by this exact
    // Feature store and defines the only accepted Market Data issuer/audience.
    ShardedFeatureStore(MarketDataConsumerBinding&& marketAuthority,
                        std::size_t maximumKeys = 4096);

    const MarketDataConsumerBinding& MarketAuthority() const noexcept
    {
        return m_marketAuthority;
    }

    // Authoritative path. The bound issuer revalidates the receipt against its
    // current entry and trusted clock before Feature state can be written.
    FeatureWriteResult Compute(const MarketDataSnapshotReceipt& input,
                               const std::string& featureSetId =
                                   "mid-spread-v1");

    // Caller-time compatibility path. Caller-provided time is not authority
    // and this overload always fails closed.
    FeatureWriteResult Compute(const MarketDataSnapshotReceipt& input,
                               std::uint64_t nowMs,
                               const std::string& featureSetId =
                                   "mid-spread-v1");

    // Source-compatible fail-closed boundary for legacy callers. A mutable raw
    // snapshot is diagnostic data, not proof of Market Data authority.
    FeatureWriteResult Compute(const MarketDataSnapshot& input,
                               std::uint64_t nowMs,
                               const std::string& featureSetId =
                                   "mid-spread-v1");

    bool Get(const MarketDataKey& key,
             const std::string& featureSetId,
             FeatureSnapshot& out) const;

    // Canonical risk-ready read. Source lineage and freshness are revalidated
    // through the same exact Market Data authority before returning a feature.
    bool GetRiskReady(const MarketDataKey& key,
                      const std::string& featureSetId,
                      FeatureSnapshot& out,
                      std::string& reason) const;

    // Caller-time compatibility path. It always fails closed.
    bool GetRiskReady(const MarketDataKey& key,
                      const std::string& featureSetId,
                      std::uint64_t nowMs,
                      FeatureSnapshot& out,
                      std::string& reason) const;

    std::size_t Size() const noexcept { return m_size.load(); }

    static std::string SnapshotDigest(const FeatureSnapshot& snapshot);

private:
    struct FeatureKey
    {
        MarketDataKey market;
        std::string featureSetId;
        bool operator<(const FeatureKey& other) const
        {
            if (market < other.market) return true;
            if (other.market < market) return false;
            return featureSetId < other.featureSetId;
        }
    };

    struct Shard
    {
        mutable std::mutex mutex;
        std::map<FeatureKey, FeatureSnapshot> entries;
    };

    static std::size_t ShardFor(const FeatureKey& key) noexcept;
    static std::string AuthorityFailure(const std::string& marketReason);
    bool ReserveKey();

private:
    std::array<Shard, kShardCount> m_shards;
    std::atomic<std::size_t> m_size;
    const std::size_t m_maximumKeys;
    MarketDataConsumerBinding m_marketAuthority;
};
