#include "marketdata_authority_internal.h"

#include <memory>
#include <mutex>
#include <utility>

using hepta_marketdata_internal::BoundedPrintable;

MarketDataConsumerBinding::MarketDataConsumerBinding() noexcept = default;

MarketDataConsumerBinding::MarketDataConsumerBinding(
    const std::shared_ptr<MarketDataAuthorityState>& authority,
    std::uint64_t issuerId,
    std::uint64_t lifecycleEpoch,
    std::uint64_t consumerId,
    const std::string& audience)
    : m_authority(authority),
      m_issuerId(issuerId),
      m_lifecycleEpoch(lifecycleEpoch),
      m_consumerId(consumerId),
      m_audience(audience),
      m_valid(true)
{
}

MarketDataConsumerBinding::MarketDataConsumerBinding(
    MarketDataConsumerBinding&& other) noexcept
    : m_authority(std::move(other.m_authority)),
      m_issuerId(other.m_issuerId),
      m_lifecycleEpoch(other.m_lifecycleEpoch),
      m_consumerId(other.m_consumerId),
      m_audience(std::move(other.m_audience)),
      m_valid(other.m_valid)
{
    other.Invalidate();
}

MarketDataConsumerBinding& MarketDataConsumerBinding::operator=(
    MarketDataConsumerBinding&& other) noexcept
{
    if (this != &other)
    {
        m_authority = std::move(other.m_authority);
        m_issuerId = other.m_issuerId;
        m_lifecycleEpoch = other.m_lifecycleEpoch;
        m_consumerId = other.m_consumerId;
        m_audience = std::move(other.m_audience);
        m_valid = other.m_valid;
        other.Invalidate();
    }
    return *this;
}

void MarketDataConsumerBinding::Invalidate() noexcept
{
    m_authority.reset();
    m_issuerId = 0;
    m_lifecycleEpoch = 0;
    m_consumerId = 0;
    m_audience.clear();
    m_valid = false;
}

bool MarketDataConsumerBinding::IsValid() const noexcept
{
    if (!m_valid || m_issuerId == 0 || m_lifecycleEpoch == 0 ||
        m_consumerId == 0 || m_audience.empty())
        return false;
    const std::shared_ptr<MarketDataAuthorityState> authority =
        m_authority.lock();
    if (!authority) return false;
    std::lock_guard<std::mutex> lock(authority->mutex);
    return authority->alive && authority->store != nullptr &&
        authority->issuerId == m_issuerId &&
        authority->lifecycleEpoch == m_lifecycleEpoch;
}

bool MarketDataConsumerBinding::Resolve(
    const MarketDataSnapshotReceipt& receipt,
    MarketDataSnapshot& out,
    std::string& reason) const
{
    out = MarketDataSnapshot();
    if (!receipt.m_valid)
    {
        reason = "MARKET_RECEIPT_INVALID";
        return false;
    }
    if (!m_valid || m_issuerId == 0 || m_lifecycleEpoch == 0 ||
        m_consumerId == 0 || m_audience.empty())
    {
        reason = "MARKET_AUTHORITY_BINDING_INVALID";
        return false;
    }
    const std::shared_ptr<MarketDataAuthorityState> authority =
        m_authority.lock();
    if (!authority)
    {
        reason = "MARKET_AUTHORITY_DESTROYED";
        return false;
    }
    std::lock_guard<std::mutex> lock(authority->mutex);
    if (!authority->alive || authority->store == nullptr)
    {
        reason = "MARKET_AUTHORITY_DESTROYED";
        return false;
    }
    return authority->store->ResolveReceiptLocked(
        authority, *this, receipt, out, reason);
}

bool MarketDataConsumerBinding::ResolveLineage(
    const MarketDataKey& key,
    std::uint64_t producerEpoch,
    std::uint64_t sequence,
    std::uint64_t generation,
    const std::string& digest,
    MarketDataSnapshot& out,
    std::string& reason) const
{
    out = MarketDataSnapshot();
    if (!m_valid || m_issuerId == 0 || m_lifecycleEpoch == 0 ||
        m_consumerId == 0 || m_audience.empty())
    {
        reason = "MARKET_AUTHORITY_BINDING_INVALID";
        return false;
    }
    const std::shared_ptr<MarketDataAuthorityState> authority =
        m_authority.lock();
    if (!authority)
    {
        reason = "MARKET_AUTHORITY_DESTROYED";
        return false;
    }
    std::lock_guard<std::mutex> lock(authority->mutex);
    if (!authority->alive || authority->store == nullptr)
    {
        reason = "MARKET_AUTHORITY_DESTROYED";
        return false;
    }
    return authority->store->ResolveLineageLocked(
        authority, *this, key, producerEpoch, sequence, generation, digest,
        out, reason);
}

MarketDataSnapshotReceipt::MarketDataSnapshotReceipt() noexcept = default;

bool MarketDataSnapshotReceipt::IsValid() const noexcept
{
    if (!m_valid || m_issuerId == 0 || m_lifecycleEpoch == 0 ||
        m_consumerId == 0 || m_audience.empty() || m_nonce == 0 ||
        m_issuedAtMs == 0)
        return false;
    const std::shared_ptr<MarketDataAuthorityState> authority =
        m_authority.lock();
    if (!authority) return false;
    std::lock_guard<std::mutex> lock(authority->mutex);
    return authority->alive && authority->store != nullptr &&
        authority->issuerId == m_issuerId &&
        authority->lifecycleEpoch == m_lifecycleEpoch;
}

MarketDataSnapshotReceipt::MarketDataSnapshotReceipt(
    const std::shared_ptr<MarketDataAuthorityState>& authority,
    const MarketDataSnapshot& snapshot,
    std::uint64_t issuerId,
    std::uint64_t lifecycleEpoch,
    std::uint64_t consumerId,
    const std::string& audience,
    std::uint64_t issuedAtMs,
    std::uint64_t nonce)
    : m_authority(authority),
      m_snapshot(snapshot),
      m_issuerId(issuerId),
      m_lifecycleEpoch(lifecycleEpoch),
      m_consumerId(consumerId),
      m_audience(audience),
      m_key(snapshot.key),
      m_producerEpoch(snapshot.producerEpoch),
      m_sequence(snapshot.sequence),
      m_generation(snapshot.generation),
      m_digest(snapshot.digest),
      m_issuedAtMs(issuedAtMs),
      m_nonce(nonce),
      m_valid(true)
{
}

MarketDataSnapshotReceipt::MarketDataSnapshotReceipt(
    MarketDataSnapshotReceipt&& other) noexcept
    : m_authority(std::move(other.m_authority)),
      m_snapshot(std::move(other.m_snapshot)),
      m_issuerId(other.m_issuerId),
      m_lifecycleEpoch(other.m_lifecycleEpoch),
      m_consumerId(other.m_consumerId),
      m_audience(std::move(other.m_audience)),
      m_key(std::move(other.m_key)),
      m_producerEpoch(other.m_producerEpoch),
      m_sequence(other.m_sequence),
      m_generation(other.m_generation),
      m_digest(std::move(other.m_digest)),
      m_issuedAtMs(other.m_issuedAtMs),
      m_nonce(other.m_nonce),
      m_valid(other.m_valid)
{
    other.Invalidate();
}

MarketDataSnapshotReceipt& MarketDataSnapshotReceipt::operator=(
    MarketDataSnapshotReceipt&& other) noexcept
{
    if (this != &other)
    {
        m_authority = std::move(other.m_authority);
        m_snapshot = std::move(other.m_snapshot);
        m_issuerId = other.m_issuerId;
        m_lifecycleEpoch = other.m_lifecycleEpoch;
        m_consumerId = other.m_consumerId;
        m_audience = std::move(other.m_audience);
        m_key = std::move(other.m_key);
        m_producerEpoch = other.m_producerEpoch;
        m_sequence = other.m_sequence;
        m_generation = other.m_generation;
        m_digest = std::move(other.m_digest);
        m_issuedAtMs = other.m_issuedAtMs;
        m_nonce = other.m_nonce;
        m_valid = other.m_valid;
        other.Invalidate();
    }
    return *this;
}

void MarketDataSnapshotReceipt::Invalidate() noexcept
{
    m_authority.reset();
    m_snapshot = MarketDataSnapshot();
    m_issuerId = 0;
    m_lifecycleEpoch = 0;
    m_consumerId = 0;
    m_audience.clear();
    m_key = MarketDataKey();
    m_producerEpoch = 0;
    m_sequence = 0;
    m_generation = 0;
    m_digest.clear();
    m_issuedAtMs = 0;
    m_nonce = 0;
    m_valid = false;
}
