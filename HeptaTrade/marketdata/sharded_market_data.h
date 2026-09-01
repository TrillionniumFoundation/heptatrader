#pragma once

#include "../numeric/fixed_decimal.h"

#include <array>
#include <atomic>
#include <cstddef>
#include <cstdint>
#include <functional>
#include <map>
#include <mutex>
#include <string>
#include <vector>

struct MarketDataKey
{
    std::string venue;
    std::string instrument;

    bool operator<(const MarketDataKey& other) const
    {
        if (venue != other.venue) return venue < other.venue;
        return instrument < other.instrument;
    }
    bool operator==(const MarketDataKey& other) const
    {
        return venue == other.venue && instrument == other.instrument;
    }
};

struct MarketDataEvent
{
    std::string eventId;
    std::string producer;
    std::string venue;
    std::string instrument;
    std::string sourceDigest;
    std::uint64_t producerEpoch = 0;
    std::uint64_t sequence = 0;
    std::uint64_t observedAtMs = 0;
    std::uint64_t capturedAtMs = 0;
    std::uint64_t freshUntilMs = 0;
    HeptaFixedDecimal bid;
    HeptaFixedDecimal ask;
    HeptaFixedDecimal last;
    HeptaFixedDecimal bidSize;
    HeptaFixedDecimal askSize;
};

struct MarketDataWriteResult
{
    bool accepted = false;
    bool duplicate = false;
    bool sequenceGap = false;
    std::uint64_t generation = 0;
    std::string reasonCode;
    std::string digest;
};

// Diagnostic and replay value. This public aggregate is deliberately not an
// authority token: callers may inspect, copy, serialize, or mutate it, but
// risk-sensitive consumers must require MarketDataSnapshotReceipt instead.
struct MarketDataSnapshot
{
    bool found = false;
    bool sequenceGap = false;
    MarketDataKey key;
    std::string eventId;
    std::string producer;
    std::string sourceDigest;
    std::uint64_t producerEpoch = 0;
    std::uint64_t sequence = 0;
    std::uint64_t generation = 0;
    std::uint64_t observedAtMs = 0;
    std::uint64_t capturedAtMs = 0;
    std::uint64_t freshUntilMs = 0;
    HeptaFixedDecimal bid;
    HeptaFixedDecimal ask;
    HeptaFixedDecimal last;
    HeptaFixedDecimal bidSize;
    HeptaFixedDecimal askSize;
    std::string digest;
};

class ShardedMarketDataStore;

// Same-process typed capability issued only after the store has validated
// structural integrity, ordering continuity, freshness, and clock direction.
// It is not a cross-process authentication envelope.
class MarketDataSnapshotReceipt final
{
public:
    MarketDataSnapshotReceipt() noexcept : m_valid(false) {}

    bool IsValid() const noexcept { return m_valid; }
    const MarketDataSnapshot& Snapshot() const noexcept { return m_snapshot; }

private:
    explicit MarketDataSnapshotReceipt(const MarketDataSnapshot& snapshot)
        : m_snapshot(snapshot), m_valid(true) {}

    MarketDataSnapshot m_snapshot;
    bool m_valid;

    friend class ShardedMarketDataStore;
};

struct MarketDataSnapshotVector
{
    std::vector<MarketDataSnapshot> components;
    std::string digest;
};

class ShardedMarketDataStore
{
public:
    static const std::size_t kShardCount = 64;

    explicit ShardedMarketDataStore(std::size_t maximumKeys = 4096);

    MarketDataWriteResult Apply(const MarketDataEvent& event);
    bool Get(const MarketDataKey& key, MarketDataSnapshot& out) const;

    // Diagnostic compatibility path. This returns a mutable value and must
    // never be treated as proof that the store issued the value.
    bool GetRiskReady(const MarketDataKey& key,
                      std::uint64_t nowMs,
                      MarketDataSnapshot& out,
                      std::string& reason) const;

    // Authoritative same-process path for Feature/risk-sensitive consumers.
    bool GetRiskReady(const MarketDataKey& key,
                      std::uint64_t nowMs,
                      MarketDataSnapshotReceipt& out,
                      std::string& reason) const;

    bool ReadVector(const std::vector<MarketDataKey>& keys,
                    std::uint64_t nowMs,
                    MarketDataSnapshotVector& out,
                    std::string& reason) const;
    std::size_t Size() const noexcept { return m_size.load(); }
    std::size_t MaximumKeys() const noexcept { return m_maximumKeys; }

    static std::size_t ShardFor(const MarketDataKey& key) noexcept;
    static std::string EventDigest(const MarketDataEvent& event);
    static bool ValidateSnapshot(const MarketDataSnapshot& snapshot,
                                 std::string& reason);
    void SetReadVectorLocksAcquiredHookForTesting(
        const std::function<void()>& hook);

private:
    struct Entry
    {
        MarketDataEvent event;
        std::uint64_t generation = 0;
        bool sequenceGap = false;
        std::string digest;
    };

    struct Shard
    {
        mutable std::mutex mutex;
        std::map<MarketDataKey, Entry> entries;
    };

    static bool ValidateEvent(const MarketDataEvent& event,
                              std::string& reason);
    static MarketDataSnapshot Snapshot(const Entry& entry);
    bool ReserveKey();

private:
    std::array<Shard, kShardCount> m_shards;
    std::atomic<std::size_t> m_size;
    const std::size_t m_maximumKeys;
    mutable std::mutex m_vectorHookMutex;
    std::function<void()> m_vectorLocksAcquiredHook;
};
