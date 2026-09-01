#pragma once

#include "../numeric/fixed_decimal.h"

#include <array>
#include <atomic>
#include <cstddef>
#include <cstdint>
#include <functional>
#include <map>
#include <memory>
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

struct MarketDataAuthorityState;
class MarketDataSnapshotReceipt;
class ShardedFeatureStore;
class ShardedMarketDataStore;

// Move-only audience binding issued by one exact market-data authority. A
// binding is consumed by one Feature store and cannot be copied into another
// consumer. It carries no cross-process authority and is invalidated when the
// issuer is destroyed or its lifecycle epoch is fenced.
class MarketDataConsumerBinding final
{
public:
    MarketDataConsumerBinding() noexcept;
    MarketDataConsumerBinding(MarketDataConsumerBinding&& other) noexcept;
    MarketDataConsumerBinding& operator=(
        MarketDataConsumerBinding&& other) noexcept;

    MarketDataConsumerBinding(const MarketDataConsumerBinding&) = delete;
    MarketDataConsumerBinding& operator=(
        const MarketDataConsumerBinding&) = delete;

    bool IsValid() const noexcept;
    const std::string& Audience() const noexcept { return m_audience; }

private:
    MarketDataConsumerBinding(
        const std::shared_ptr<MarketDataAuthorityState>& authority,
        std::uint64_t issuerId,
        std::uint64_t lifecycleEpoch,
        std::uint64_t consumerId,
        const std::string& audience);

    bool Resolve(const MarketDataSnapshotReceipt& receipt,
                 MarketDataSnapshot& out,
                 std::string& reason) const;
    bool WithCurrentReceipt(
        const MarketDataSnapshotReceipt& receipt,
        const std::function<void(const MarketDataSnapshot&)>& consumer,
        std::string& reason) const;
    bool ResolveLineage(const MarketDataKey& key,
                        std::uint64_t producerEpoch,
                        std::uint64_t sequence,
                        std::uint64_t generation,
                        const std::string& digest,
                        MarketDataSnapshot& out,
                        std::string& reason) const;
    void Invalidate() noexcept;

    std::weak_ptr<MarketDataAuthorityState> m_authority;
    std::uint64_t m_issuerId = 0;
    std::uint64_t m_lifecycleEpoch = 0;
    std::uint64_t m_consumerId = 0;
    std::string m_audience;
    bool m_valid = false;

    friend class ShardedMarketDataStore;
    friend class ShardedFeatureStore;
};

// Move-only same-process capability issued only after the exact market-data
// store has validated structural integrity, ordering continuity and freshness
// using its own trusted clock. The receipt is audience-, issuer-, lifecycle-
// and lineage-bound. It must be revalidated against current store state at use.
class MarketDataSnapshotReceipt final
{
public:
    MarketDataSnapshotReceipt() noexcept;
    MarketDataSnapshotReceipt(MarketDataSnapshotReceipt&& other) noexcept;
    MarketDataSnapshotReceipt& operator=(
        MarketDataSnapshotReceipt&& other) noexcept;

    MarketDataSnapshotReceipt(const MarketDataSnapshotReceipt&) = delete;
    MarketDataSnapshotReceipt& operator=(
        const MarketDataSnapshotReceipt&) = delete;

    bool IsValid() const noexcept;

    // Returns a diagnostic copy. Mutating the returned value cannot mutate the
    // receipt or its private issuer/audience/lineage identity.
    MarketDataSnapshot Snapshot() const { return m_snapshot; }

    std::uint64_t IssuedAtMs() const noexcept { return m_issuedAtMs; }
    std::uint64_t Nonce() const noexcept { return m_nonce; }

private:
    MarketDataSnapshotReceipt(
        const std::shared_ptr<MarketDataAuthorityState>& authority,
        const MarketDataSnapshot& snapshot,
        std::uint64_t issuerId,
        std::uint64_t lifecycleEpoch,
        std::uint64_t consumerId,
        const std::string& audience,
        std::uint64_t issuedAtMs,
        std::uint64_t nonce);
    void Invalidate() noexcept;

    std::weak_ptr<MarketDataAuthorityState> m_authority;
    MarketDataSnapshot m_snapshot;
    std::uint64_t m_issuerId = 0;
    std::uint64_t m_lifecycleEpoch = 0;
    std::uint64_t m_consumerId = 0;
    std::string m_audience;
    MarketDataKey m_key;
    std::uint64_t m_producerEpoch = 0;
    std::uint64_t m_sequence = 0;
    std::uint64_t m_generation = 0;
    std::string m_digest;
    std::uint64_t m_issuedAtMs = 0;
    std::uint64_t m_nonce = 0;
    bool m_valid = false;

    friend class ShardedMarketDataStore;
    friend class MarketDataConsumerBinding;
};

struct MarketDataSnapshotVector
{
    std::vector<MarketDataSnapshot> components;
    std::string digest;
};

class ShardedMarketDataStore
{
public:
    using Clock = std::function<std::uint64_t()>;

    static const std::size_t kShardCount = 64;

    explicit ShardedMarketDataStore(std::size_t maximumKeys = 4096);
    ShardedMarketDataStore(std::size_t maximumKeys, const Clock& clock);
    ~ShardedMarketDataStore();

    ShardedMarketDataStore(const ShardedMarketDataStore&) = delete;
    ShardedMarketDataStore& operator=(const ShardedMarketDataStore&) = delete;
    ShardedMarketDataStore(ShardedMarketDataStore&&) = delete;
    ShardedMarketDataStore& operator=(ShardedMarketDataStore&&) = delete;

    MarketDataWriteResult Apply(const MarketDataEvent& event);
    bool Get(const MarketDataKey& key, MarketDataSnapshot& out) const;

    // Diagnostic compatibility path. Caller time is accepted only for a raw
    // diagnostic snapshot and grants no authority.
    bool GetRiskReady(const MarketDataKey& key,
                      std::uint64_t nowMs,
                      MarketDataSnapshot& out,
                      std::string& reason) const;

    // Issue one move-only consumer binding for the exact store/lifecycle.
    MarketDataConsumerBinding BindConsumer(const std::string& audience);

    // Authoritative path. The store obtains time from its trusted clock and
    // targets the receipt to one exact consumer binding.
    bool GetRiskReady(const MarketDataConsumerBinding& consumer,
                      const MarketDataKey& key,
                      MarketDataSnapshotReceipt& out,
                      std::string& reason) const;

    // Source-compatible caller-time receipt path. It always fails closed.
    bool GetRiskReady(const MarketDataKey& key,
                      std::uint64_t nowMs,
                      MarketDataSnapshotReceipt& out,
                      std::string& reason) const;

    // Fail-closed lifecycle fence. Existing bindings and receipts become
    // invalid; new bindings may be issued for the new epoch.
    bool FenceAuthority(std::string& reason) noexcept;

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

    bool ValidateBindingLocked(
        const std::shared_ptr<MarketDataAuthorityState>& authority,
        const MarketDataConsumerBinding& consumer,
        std::string& reason) const;
    bool ObserveTrustedNowLocked(
        const std::shared_ptr<MarketDataAuthorityState>& authority,
        std::uint64_t& nowMs,
        std::string& reason) const;
    bool ValidateCurrentEntryLocked(const Entry& entry,
                                    std::uint64_t nowMs,
                                    MarketDataSnapshot& out,
                                    std::string& reason) const;
    bool CurrentSnapshotLocked(
        const std::shared_ptr<MarketDataAuthorityState>& authority,
        const MarketDataKey& key,
        std::uint64_t& nowMs,
        MarketDataSnapshot& out,
        std::string& reason) const;
    bool ResolveReceiptLocked(
        const std::shared_ptr<MarketDataAuthorityState>& authority,
        const MarketDataConsumerBinding& consumer,
        const MarketDataSnapshotReceipt& receipt,
        MarketDataSnapshot& out,
        std::string& reason) const;
    bool UseReceiptLocked(
        const std::shared_ptr<MarketDataAuthorityState>& authority,
        const MarketDataConsumerBinding& consumer,
        const MarketDataSnapshotReceipt& receipt,
        const std::function<void(const MarketDataSnapshot&)>& use,
        std::string& reason) const;
    bool ResolveLineageLocked(
        const std::shared_ptr<MarketDataAuthorityState>& authority,
        const MarketDataConsumerBinding& consumer,
        const MarketDataKey& key,
        std::uint64_t producerEpoch,
        std::uint64_t sequence,
        std::uint64_t generation,
        const std::string& digest,
        MarketDataSnapshot& out,
        std::string& reason) const;

private:
    std::array<Shard, kShardCount> m_shards;
    std::atomic<std::size_t> m_size;
    const std::size_t m_maximumKeys;
    mutable std::mutex m_vectorHookMutex;
    std::function<void()> m_vectorLocksAcquiredHook;
    std::shared_ptr<MarketDataAuthorityState> m_authority;

    friend class MarketDataConsumerBinding;
};
