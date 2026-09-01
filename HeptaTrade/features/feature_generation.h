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

    explicit ShardedFeatureStore(std::size_t maximumKeys = 4096);

    // The authoritative path accepts only a same-process capability issued by
    // ShardedMarketDataStore after structural, continuity and freshness checks.
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
    bool ReserveKey();

private:
    std::array<Shard, kShardCount> m_shards;
    std::atomic<std::size_t> m_size;
    const std::size_t m_maximumKeys;
};
