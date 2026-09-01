#include "marketdata_authority_internal.h"

#include <algorithm>
#include <iomanip>
#include <limits>
#include <openssl/evp.h>
#include <sstream>
#include <utility>

using hepta_marketdata_internal::BoundedPrintable;
using hepta_marketdata_internal::CanonicalDigest;

namespace
{
void AppendField(std::string& out, const char* name, const std::string& value)
{
    out.append(name);
    out.push_back('=');
    out.append(std::to_string(value.size()));
    out.push_back(':');
    out.append(value);
    out.push_back(';');
}

void AppendRaw(std::string& out, const char* name, HeptaFixedDecimal value)
{
    AppendField(out, name, std::to_string(value.Raw()));
}

std::string Sha256(const std::string& value)
{
    unsigned char digest[EVP_MAX_MD_SIZE];
    unsigned int length = 0;
    EVP_MD_CTX* context = EVP_MD_CTX_new();
    if (context == nullptr) return std::string();
    const bool ok = EVP_DigestInit_ex(context, EVP_sha256(), nullptr) == 1 &&
        EVP_DigestUpdate(context, value.data(), value.size()) == 1 &&
        EVP_DigestFinal_ex(context, digest, &length) == 1;
    EVP_MD_CTX_free(context);
    if (!ok) return std::string();
    std::ostringstream out;
    out << "sha256:" << std::hex << std::setfill('0');
    for (unsigned int i = 0; i < length; ++i)
        out << std::setw(2) << static_cast<unsigned int>(digest[i]);
    return out.str();
}

std::string VectorDigest(const std::vector<MarketDataSnapshot>& snapshots)
{
    std::string canonical;
    AppendField(canonical, "schema", "hepta.market-snapshot-vector.v1");
    for (std::size_t i = 0; i < snapshots.size(); ++i)
    {
        AppendField(canonical, "venue", snapshots[i].key.venue);
        AppendField(canonical, "instrument", snapshots[i].key.instrument);
        AppendField(canonical, "epoch",
                    std::to_string(snapshots[i].producerEpoch));
        AppendField(canonical, "sequence",
                    std::to_string(snapshots[i].sequence));
        AppendField(canonical, "generation",
                    std::to_string(snapshots[i].generation));
        AppendField(canonical, "digest", snapshots[i].digest);
    }
    return Sha256(canonical);
}
}

std::size_t ShardedMarketDataStore::ShardFor(const MarketDataKey& key) noexcept
{
    std::uint64_t hash = 1469598103934665603ULL;
    const std::string values[2] = {key.venue, key.instrument};
    for (int part = 0; part < 2; ++part)
    {
        for (std::size_t i = 0; i < values[part].size(); ++i)
        {
            hash ^= static_cast<unsigned char>(values[part][i]);
            hash *= 1099511628211ULL;
        }
        hash ^= 0xffu;
        hash *= 1099511628211ULL;
    }
    return static_cast<std::size_t>(hash % kShardCount);
}

bool ShardedMarketDataStore::ValidateEvent(
    const MarketDataEvent& event,
    std::string& reason)
{
    if (!BoundedPrintable(event.eventId, 128u) ||
        !BoundedPrintable(event.producer, 64u) ||
        !BoundedPrintable(event.venue, 32u) ||
        !BoundedPrintable(event.instrument, 128u))
    {
        reason = "MARKET_IDENTITY_INVALID";
        return false;
    }
    if (!CanonicalDigest(event.sourceDigest))
    {
        reason = "MARKET_SOURCE_DIGEST_INVALID";
        return false;
    }
    if (event.producerEpoch == 0 || event.sequence == 0)
    {
        reason = "MARKET_ORDERING_IDENTITY_INVALID";
        return false;
    }
    if (event.observedAtMs == 0 ||
        event.capturedAtMs < event.observedAtMs ||
        event.freshUntilMs < event.capturedAtMs)
    {
        reason = "MARKET_TIME_ENVELOPE_INVALID";
        return false;
    }
    if (!event.bid.IsValid() || !event.ask.IsValid() ||
        !event.last.IsValid() || !event.bidSize.IsValid() ||
        !event.askSize.IsValid() || event.ask < event.bid ||
        event.bidSize.Raw() < 0 || event.askSize.Raw() < 0)
    {
        reason = "MARKET_QUOTE_INVALID";
        return false;
    }
    reason.clear();
    return true;
}

std::string ShardedMarketDataStore::EventDigest(const MarketDataEvent& event)
{
    std::string canonical;
    AppendField(canonical, "schema", "hepta.market-event.v1");
    AppendField(canonical, "event_id", event.eventId);
    AppendField(canonical, "producer", event.producer);
    AppendField(canonical, "venue", event.venue);
    AppendField(canonical, "instrument", event.instrument);
    AppendField(canonical, "source_digest", event.sourceDigest);
    AppendField(canonical, "producer_epoch",
                std::to_string(event.producerEpoch));
    AppendField(canonical, "sequence", std::to_string(event.sequence));
    AppendField(canonical, "observed_at_ms",
                std::to_string(event.observedAtMs));
    AppendField(canonical, "captured_at_ms",
                std::to_string(event.capturedAtMs));
    AppendField(canonical, "fresh_until_ms",
                std::to_string(event.freshUntilMs));
    AppendRaw(canonical, "bid", event.bid);
    AppendRaw(canonical, "ask", event.ask);
    AppendRaw(canonical, "last", event.last);
    AppendRaw(canonical, "bid_size", event.bidSize);
    AppendRaw(canonical, "ask_size", event.askSize);
    return Sha256(canonical);
}

bool ShardedMarketDataStore::ValidateSnapshot(
    const MarketDataSnapshot& snapshot, std::string& reason)
{
    if (!snapshot.found || snapshot.generation == 0 ||
        !CanonicalDigest(snapshot.digest))
    {
        reason = "MARKET_SNAPSHOT_INCOMPLETE";
        return false;
    }
    MarketDataEvent event;
    event.eventId = snapshot.eventId;
    event.producer = snapshot.producer;
    event.venue = snapshot.key.venue;
    event.instrument = snapshot.key.instrument;
    event.sourceDigest = snapshot.sourceDigest;
    event.producerEpoch = snapshot.producerEpoch;
    event.sequence = snapshot.sequence;
    event.observedAtMs = snapshot.observedAtMs;
    event.capturedAtMs = snapshot.capturedAtMs;
    event.freshUntilMs = snapshot.freshUntilMs;
    event.bid = snapshot.bid;
    event.ask = snapshot.ask;
    event.last = snapshot.last;
    event.bidSize = snapshot.bidSize;
    event.askSize = snapshot.askSize;
    if (!ValidateEvent(event, reason)) return false;
    const std::string expected = EventDigest(event);
    if (expected.empty() || expected != snapshot.digest)
    {
        reason = "MARKET_SNAPSHOT_DIGEST_MISMATCH";
        return false;
    }
    reason.clear();
    return true;
}

void ShardedMarketDataStore::SetReadVectorLocksAcquiredHookForTesting(
    const std::function<void()>& hook)
{
    std::lock_guard<std::mutex> lock(m_vectorHookMutex);
    m_vectorLocksAcquiredHook = hook;
}

bool ShardedMarketDataStore::ReserveKey()
{
    std::size_t current = m_size.load();
    for (;;)
    {
        if (current >= m_maximumKeys) return false;
        if (m_size.compare_exchange_weak(current, current + 1u)) return true;
    }
}

MarketDataWriteResult ShardedMarketDataStore::Apply(
    const MarketDataEvent& event)
{
    MarketDataWriteResult result;
    std::string reason;
    if (!ValidateEvent(event, reason))
    {
        result.reasonCode = reason;
        return result;
    }
    const std::string digest = EventDigest(event);
    if (digest.empty())
    {
        result.reasonCode = "MARKET_DIGEST_FAILED";
        return result;
    }
    const MarketDataKey key = {event.venue, event.instrument};
    Shard& shard = m_shards[ShardFor(key)];
    std::lock_guard<std::mutex> lock(shard.mutex);
    std::map<MarketDataKey, Entry>::iterator found = shard.entries.find(key);
    if (found == shard.entries.end())
    {
        if (!ReserveKey())
        {
            result.reasonCode = "MARKET_CAPACITY_EXHAUSTED";
            return result;
        }
        Entry entry;
        entry.event = event;
        entry.generation = 1;
        entry.sequenceGap = event.sequence != 1;
        entry.digest = digest;
        try
        {
            found = shard.entries.insert(std::make_pair(key, entry)).first;
        }
        catch (...)
        {
            --m_size;
            result.reasonCode = "MARKET_STORAGE_FAILED";
            return result;
        }
        result.accepted = true;
        result.sequenceGap = entry.sequenceGap;
        result.generation = entry.generation;
        result.digest = entry.digest;
        result.reasonCode = entry.sequenceGap
            ? "MARKET_SEQUENCE_GAP_RECORDED" : "MARKET_ACCEPTED";
        return result;
    }

    Entry& current = found->second;
    if (event.producerEpoch < current.event.producerEpoch)
    {
        result.reasonCode = "MARKET_EPOCH_STALE";
        return result;
    }
    bool sequenceGap = false;
    if (event.producerEpoch == current.event.producerEpoch)
    {
        if (event.producer != current.event.producer)
        {
            result.reasonCode = "MARKET_WRITER_CONFLICT";
            return result;
        }
        if (event.sequence < current.event.sequence)
        {
            result.reasonCode = "MARKET_SEQUENCE_STALE";
            return result;
        }
        if (event.sequence == current.event.sequence)
        {
            result.generation = current.generation;
            result.sequenceGap = current.sequenceGap;
            result.digest = current.digest;
            if (digest == current.digest)
            {
                result.accepted = true;
                result.duplicate = true;
                result.reasonCode = "MARKET_DUPLICATE";
            }
            else
                result.reasonCode = "MARKET_SEQUENCE_CONFLICT";
            return result;
        }
        sequenceGap = current.sequenceGap ||
            event.sequence != current.event.sequence + 1u;
    }
    else
    {
        if (event.sequence != 1)
        {
            result.reasonCode = "MARKET_EPOCH_START_SEQUENCE_INVALID";
            return result;
        }
        sequenceGap = false;
    }
    if (current.generation == std::numeric_limits<std::uint64_t>::max())
    {
        result.reasonCode = "MARKET_GENERATION_EXHAUSTED";
        return result;
    }
    current.event = event;
    ++current.generation;
    current.sequenceGap = sequenceGap;
    current.digest = digest;
    result.accepted = true;
    result.sequenceGap = sequenceGap;
    result.generation = current.generation;
    result.digest = digest;
    result.reasonCode = sequenceGap
        ? "MARKET_SEQUENCE_GAP_RECORDED" : "MARKET_ACCEPTED";
    return result;
}

MarketDataSnapshot ShardedMarketDataStore::Snapshot(const Entry& entry)
{
    MarketDataSnapshot out;
    out.found = true;
    out.sequenceGap = entry.sequenceGap;
    out.key.venue = entry.event.venue;
    out.key.instrument = entry.event.instrument;
    out.eventId = entry.event.eventId;
    out.producer = entry.event.producer;
    out.sourceDigest = entry.event.sourceDigest;
    out.producerEpoch = entry.event.producerEpoch;
    out.sequence = entry.event.sequence;
    out.generation = entry.generation;
    out.observedAtMs = entry.event.observedAtMs;
    out.capturedAtMs = entry.event.capturedAtMs;
    out.freshUntilMs = entry.event.freshUntilMs;
    out.bid = entry.event.bid;
    out.ask = entry.event.ask;
    out.last = entry.event.last;
    out.bidSize = entry.event.bidSize;
    out.askSize = entry.event.askSize;
    out.digest = entry.digest;
    return out;
}

bool ShardedMarketDataStore::Get(
    const MarketDataKey& key,
    MarketDataSnapshot& out) const
{
    out = MarketDataSnapshot();
    const Shard& shard = m_shards[ShardFor(key)];
    std::lock_guard<std::mutex> lock(shard.mutex);
    const std::map<MarketDataKey, Entry>::const_iterator found =
        shard.entries.find(key);
    if (found == shard.entries.end()) return false;
    out = Snapshot(found->second);
    return true;
}

bool ShardedMarketDataStore::GetRiskReady(
    const MarketDataKey& key,
    std::uint64_t nowMs,
    MarketDataSnapshot& out,
    std::string& reason) const
{
    if (!Get(key, out))
    {
        reason = "MARKET_SNAPSHOT_MISSING";
        return false;
    }
    if (!ValidateSnapshot(out, reason)) return false;
    if (out.sequenceGap)
    {
        reason = "MARKET_SEQUENCE_GAP";
        return false;
    }
    if (nowMs < out.capturedAtMs)
    {
        reason = "MARKET_CLOCK_REGRESSION";
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


bool ShardedMarketDataStore::ReadVector(
    const std::vector<MarketDataKey>& keys,
    std::uint64_t nowMs,
    MarketDataSnapshotVector& out,
    std::string& reason) const
{
    out = MarketDataSnapshotVector();
    if (keys.empty() || keys.size() > 256u)
    {
        reason = "MARKET_VECTOR_SIZE_INVALID";
        return false;
    }
    std::vector<MarketDataKey> ordered = keys;
    std::sort(ordered.begin(), ordered.end());
    for (std::size_t i = 1; i < ordered.size(); ++i)
    {
        if (ordered[i] == ordered[i - 1])
        {
            reason = "MARKET_VECTOR_DUPLICATE_KEY";
            return false;
        }
    }

    std::vector<std::size_t> shardIds;
    shardIds.reserve(ordered.size());
    for (std::size_t i = 0; i < ordered.size(); ++i)
        shardIds.push_back(ShardFor(ordered[i]));
    std::sort(shardIds.begin(), shardIds.end());
    shardIds.erase(std::unique(shardIds.begin(), shardIds.end()),
                   shardIds.end());

    // Lock the complete target shard set in canonical order. No writer can
    // advance one component while the vector is being assembled, so the
    // resulting digest identifies one coherent store cut.
    std::vector<std::unique_lock<std::mutex> > locks;
    locks.reserve(shardIds.size());
    for (std::size_t i = 0; i < shardIds.size(); ++i)
        locks.push_back(std::unique_lock<std::mutex>(
            m_shards[shardIds[i]].mutex));

    std::function<void()> hook;
    {
        std::lock_guard<std::mutex> hookLock(m_vectorHookMutex);
        hook = m_vectorLocksAcquiredHook;
    }
    if (hook) hook();

    out.components.reserve(ordered.size());
    for (std::size_t i = 0; i < ordered.size(); ++i)
    {
        const Shard& shard = m_shards[ShardFor(ordered[i])];
        const std::map<MarketDataKey, Entry>::const_iterator found =
            shard.entries.find(ordered[i]);
        if (found == shard.entries.end())
        {
            out = MarketDataSnapshotVector();
            reason = "MARKET_SNAPSHOT_MISSING";
            return false;
        }
        MarketDataSnapshot snapshot = Snapshot(found->second);
        if (!ValidateSnapshot(snapshot, reason))
        {
            out = MarketDataSnapshotVector();
            return false;
        }
        if (snapshot.sequenceGap)
        {
            out = MarketDataSnapshotVector();
            reason = "MARKET_SEQUENCE_GAP";
            return false;
        }
        if (nowMs < snapshot.capturedAtMs)
        {
            out = MarketDataSnapshotVector();
            reason = "MARKET_CLOCK_REGRESSION";
            return false;
        }
        if (nowMs > snapshot.freshUntilMs)
        {
            out = MarketDataSnapshotVector();
            reason = "MARKET_SNAPSHOT_STALE";
            return false;
        }
        out.components.push_back(snapshot);
    }
    out.digest = VectorDigest(out.components);
    if (out.digest.empty())
    {
        out = MarketDataSnapshotVector();
        reason = "MARKET_VECTOR_DIGEST_FAILED";
        return false;
    }
    reason.clear();
    return true;
}
