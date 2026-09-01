#include "marketdata_authority_internal.h"

#include <chrono>
#include <limits>
#include <memory>
#include <mutex>

using hepta_marketdata_internal::BoundedPrintable;
using hepta_marketdata_internal::CanonicalDigest;

namespace
{
std::atomic<std::uint64_t> g_nextIssuerId(1);

std::uint64_t SystemNowMs()
{
    using namespace std::chrono;
    return static_cast<std::uint64_t>(duration_cast<milliseconds>(
        system_clock::now().time_since_epoch()).count());
}

std::uint64_t NextIssuerId()
{
    std::uint64_t current = g_nextIssuerId.load();
    for (;;)
    {
        if (current == 0 ||
            current == std::numeric_limits<std::uint64_t>::max())
            return 0;
        if (g_nextIssuerId.compare_exchange_weak(current, current + 1u))
            return current;
    }
}
}

ShardedMarketDataStore::ShardedMarketDataStore(std::size_t maximumKeys)
    : ShardedMarketDataStore(maximumKeys, Clock(SystemNowMs))
{
}

ShardedMarketDataStore::ShardedMarketDataStore(
    std::size_t maximumKeys,
    const Clock& clock)
    : m_size(0),
      m_maximumKeys(maximumKeys),
      m_authority(new MarketDataAuthorityState())
{
    m_authority->clock = clock;
    m_authority->store = this;
    m_authority->issuerId = NextIssuerId();
    if (!m_authority->clock || m_authority->issuerId == 0)
        m_authority->alive = false;
}

ShardedMarketDataStore::~ShardedMarketDataStore()
{
    if (!m_authority) return;
    std::lock_guard<std::mutex> lock(m_authority->mutex);
    m_authority->alive = false;
    m_authority->store = nullptr;
    if (m_authority->lifecycleEpoch !=
        std::numeric_limits<std::uint64_t>::max())
        ++m_authority->lifecycleEpoch;
}


MarketDataConsumerBinding ShardedMarketDataStore::BindConsumer(
    const std::string& audience)
{
    if (!BoundedPrintable(audience, 128u) || !m_authority)
        return MarketDataConsumerBinding();
    std::lock_guard<std::mutex> lock(m_authority->mutex);
    if (!m_authority->alive || m_authority->store != this ||
        m_authority->issuerId == 0 || m_authority->lifecycleEpoch == 0 ||
        m_authority->nextConsumerId == 0 ||
        m_authority->nextConsumerId ==
            std::numeric_limits<std::uint64_t>::max())
        return MarketDataConsumerBinding();
    const std::uint64_t consumerId = m_authority->nextConsumerId++;
    return MarketDataConsumerBinding(
        m_authority, m_authority->issuerId,
        m_authority->lifecycleEpoch, consumerId, audience);
}

bool ShardedMarketDataStore::GetRiskReady(
    const MarketDataConsumerBinding& consumer,
    const MarketDataKey& key,
    MarketDataSnapshotReceipt& out,
    std::string& reason) const
{
    out = MarketDataSnapshotReceipt();
    if (!m_authority)
    {
        reason = "MARKET_AUTHORITY_DESTROYED";
        return false;
    }
    std::lock_guard<std::mutex> lock(m_authority->mutex);
    if (!ValidateBindingLocked(m_authority, consumer, reason)) return false;
    std::uint64_t nowMs = 0;
    if (!ObserveTrustedNowLocked(m_authority, nowMs, reason)) return false;
    MarketDataSnapshot snapshot;
    if (!CurrentSnapshotLocked(key, nowMs, snapshot, reason)) return false;
    if (m_authority->nextReceiptNonce == 0 ||
        m_authority->nextReceiptNonce ==
            std::numeric_limits<std::uint64_t>::max())
    {
        reason = "MARKET_RECEIPT_NONCE_EXHAUSTED";
        return false;
    }
    const std::uint64_t nonce = m_authority->nextReceiptNonce++;
    out = MarketDataSnapshotReceipt(
        m_authority, snapshot, m_authority->issuerId,
        m_authority->lifecycleEpoch, consumer.m_consumerId,
        consumer.m_audience, nowMs, nonce);
    reason.clear();
    return true;
}

bool ShardedMarketDataStore::GetRiskReady(
    const MarketDataKey&,
    std::uint64_t,
    MarketDataSnapshotReceipt& out,
    std::string& reason) const
{
    out = MarketDataSnapshotReceipt();
    reason = "MARKET_AUTHORITY_CONSUMER_BINDING_REQUIRED";
    return false;
}

bool ShardedMarketDataStore::FenceAuthority(std::string& reason) noexcept
{
    try
    {
        if (!m_authority)
        {
            reason = "MARKET_AUTHORITY_DESTROYED";
            return false;
        }
        std::lock_guard<std::mutex> lock(m_authority->mutex);
        if (!m_authority->alive || m_authority->store != this)
        {
            reason = "MARKET_AUTHORITY_DESTROYED";
            return false;
        }
        if (m_authority->lifecycleEpoch ==
            std::numeric_limits<std::uint64_t>::max())
        {
            m_authority->alive = false;
            reason = "MARKET_AUTHORITY_EPOCH_EXHAUSTED";
            return false;
        }
        ++m_authority->lifecycleEpoch;
        reason.clear();
        return true;
    }
    catch (...)
    {
        reason = "MARKET_AUTHORITY_FENCE_FAILED";
        return false;
    }
}

bool ShardedMarketDataStore::ValidateBindingLocked(
    const std::shared_ptr<MarketDataAuthorityState>& authority,
    const MarketDataConsumerBinding& consumer,
    std::string& reason) const
{
    if (!authority || !authority->alive || authority->store != this)
    {
        reason = "MARKET_AUTHORITY_DESTROYED";
        return false;
    }
    const std::shared_ptr<MarketDataAuthorityState> bound =
        consumer.m_authority.lock();
    if (!consumer.m_valid || !bound || bound.get() != authority.get() ||
        consumer.m_issuerId != authority->issuerId)
    {
        reason = "MARKET_AUTHORITY_ISSUER_MISMATCH";
        return false;
    }
    if (consumer.m_lifecycleEpoch != authority->lifecycleEpoch)
    {
        reason = "MARKET_AUTHORITY_EPOCH_MISMATCH";
        return false;
    }
    if (consumer.m_consumerId == 0 ||
        !BoundedPrintable(consumer.m_audience, 128u))
    {
        reason = "MARKET_AUTHORITY_AUDIENCE_INVALID";
        return false;
    }
    reason.clear();
    return true;
}

bool ShardedMarketDataStore::ObserveTrustedNowLocked(
    const std::shared_ptr<MarketDataAuthorityState>& authority,
    std::uint64_t& nowMs,
    std::string& reason) const
{
    nowMs = 0;
    if (!authority || !authority->clock)
    {
        reason = "MARKET_AUTHORITY_CLOCK_INVALID";
        return false;
    }
    try
    {
        nowMs = authority->clock();
    }
    catch (...)
    {
        reason = "MARKET_AUTHORITY_CLOCK_FAILED";
        return false;
    }
    if (nowMs == 0)
    {
        reason = "MARKET_AUTHORITY_CLOCK_INVALID";
        return false;
    }
    if (authority->lastTrustedNowMs != 0 &&
        nowMs < authority->lastTrustedNowMs)
    {
        reason = "MARKET_AUTHORITY_CLOCK_REGRESSION";
        return false;
    }
    authority->lastTrustedNowMs = nowMs;
    reason.clear();
    return true;
}

bool ShardedMarketDataStore::CurrentSnapshotLocked(
    const MarketDataKey& key,
    std::uint64_t nowMs,
    MarketDataSnapshot& out,
    std::string& reason) const
{
    out = MarketDataSnapshot();
    const Shard& shard = m_shards[ShardFor(key)];
    std::lock_guard<std::mutex> lock(shard.mutex);
    const std::map<MarketDataKey, Entry>::const_iterator found =
        shard.entries.find(key);
    if (found == shard.entries.end())
    {
        reason = "MARKET_SNAPSHOT_MISSING";
        return false;
    }
    out = Snapshot(found->second);
    if (!ValidateSnapshot(out, reason)) return false;
    if (out.sequenceGap)
    {
        reason = "MARKET_SEQUENCE_GAP";
        return false;
    }
    if (nowMs < out.capturedAtMs)
    {
        reason = "MARKET_AUTHORITY_CLOCK_REGRESSION";
        return false;
    }
    if (nowMs > out.freshUntilMs)
    {
        reason = "MARKET_SNAPSHOT_STALE";
        return false;
    }
    reason.clear();
    return true;
}

bool ShardedMarketDataStore::ResolveReceiptLocked(
    const std::shared_ptr<MarketDataAuthorityState>& authority,
    const MarketDataConsumerBinding& consumer,
    const MarketDataSnapshotReceipt& receipt,
    MarketDataSnapshot& out,
    std::string& reason) const
{
    out = MarketDataSnapshot();
    if (!ValidateBindingLocked(authority, consumer, reason)) return false;
    if (!receipt.m_valid)
    {
        reason = "MARKET_RECEIPT_INVALID";
        return false;
    }
    const std::shared_ptr<MarketDataAuthorityState> receiptAuthority =
        receipt.m_authority.lock();
    if (!receiptAuthority)
    {
        reason = "MARKET_AUTHORITY_DESTROYED";
        return false;
    }
    if (receiptAuthority.get() != authority.get() ||
        receipt.m_issuerId != authority->issuerId)
    {
        reason = "MARKET_RECEIPT_ISSUER_MISMATCH";
        return false;
    }
    if (receipt.m_lifecycleEpoch != authority->lifecycleEpoch)
    {
        reason = "MARKET_RECEIPT_EPOCH_MISMATCH";
        return false;
    }
    if (receipt.m_consumerId != consumer.m_consumerId ||
        receipt.m_audience != consumer.m_audience)
    {
        reason = "MARKET_RECEIPT_AUDIENCE_MISMATCH";
        return false;
    }
    if (receipt.m_nonce == 0 || receipt.m_issuedAtMs == 0 ||
        !(receipt.m_key == receipt.m_snapshot.key) ||
        receipt.m_producerEpoch != receipt.m_snapshot.producerEpoch ||
        receipt.m_sequence != receipt.m_snapshot.sequence ||
        receipt.m_generation != receipt.m_snapshot.generation ||
        receipt.m_digest != receipt.m_snapshot.digest)
    {
        reason = "MARKET_RECEIPT_LINEAGE_INVALID";
        return false;
    }
    std::string snapshotReason;
    if (!ValidateSnapshot(receipt.m_snapshot, snapshotReason))
    {
        reason = "MARKET_RECEIPT_SNAPSHOT_INVALID";
        return false;
    }
    std::uint64_t nowMs = 0;
    if (!ObserveTrustedNowLocked(authority, nowMs, reason)) return false;
    if (nowMs < receipt.m_issuedAtMs)
    {
        reason = "MARKET_AUTHORITY_CLOCK_REGRESSION";
        return false;
    }
    MarketDataSnapshot current;
    if (!CurrentSnapshotLocked(receipt.m_key, nowMs, current, reason))
        return false;
    if (current.producerEpoch != receipt.m_producerEpoch ||
        current.sequence != receipt.m_sequence ||
        current.generation != receipt.m_generation ||
        current.digest != receipt.m_digest)
    {
        reason = "MARKET_RECEIPT_SUPERSEDED";
        return false;
    }
    out = current;
    reason.clear();
    return true;
}

bool ShardedMarketDataStore::ResolveLineageLocked(
    const std::shared_ptr<MarketDataAuthorityState>& authority,
    const MarketDataConsumerBinding& consumer,
    const MarketDataKey& key,
    std::uint64_t producerEpoch,
    std::uint64_t sequence,
    std::uint64_t generation,
    const std::string& digest,
    MarketDataSnapshot& out,
    std::string& reason) const
{
    out = MarketDataSnapshot();
    if (!ValidateBindingLocked(authority, consumer, reason)) return false;
    if (producerEpoch == 0 || sequence == 0 || generation == 0 ||
        !CanonicalDigest(digest))
    {
        reason = "MARKET_AUTHORITY_LINEAGE_INVALID";
        return false;
    }
    std::uint64_t nowMs = 0;
    if (!ObserveTrustedNowLocked(authority, nowMs, reason)) return false;
    MarketDataSnapshot current;
    if (!CurrentSnapshotLocked(key, nowMs, current, reason)) return false;
    if (current.producerEpoch != producerEpoch ||
        current.sequence != sequence ||
        current.generation != generation || current.digest != digest)
    {
        reason = "MARKET_RECEIPT_SUPERSEDED";
        return false;
    }
    out = current;
    reason.clear();
    return true;
}
