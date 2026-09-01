#include "feature_generation.h"

#include <iomanip>
#include <limits>
#include <openssl/evp.h>
#include <sstream>

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
}

ShardedFeatureStore::ShardedFeatureStore(std::size_t maximumKeys)
    : m_size(0), m_maximumKeys(maximumKeys)
{
}

std::size_t ShardedFeatureStore::ShardFor(const FeatureKey& key) noexcept
{
    std::uint64_t hash = 1469598103934665603ULL;
    const std::string values[3] = {
        key.market.venue, key.market.instrument, key.featureSetId};
    for (int part = 0; part < 3; ++part)
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

bool ShardedFeatureStore::ReserveKey()
{
    std::size_t current = m_size.load();
    for (;;)
    {
        if (current >= m_maximumKeys) return false;
        if (m_size.compare_exchange_weak(current, current + 1u)) return true;
    }
}

std::string ShardedFeatureStore::SnapshotDigest(
    const FeatureSnapshot& snapshot)
{
    std::string canonical;
    AppendField(canonical, "schema", "hepta.feature-snapshot.v1");
    AppendField(canonical, "venue", snapshot.key.venue);
    AppendField(canonical, "instrument", snapshot.key.instrument);
    AppendField(canonical, "feature_set", snapshot.featureSetId);
    AppendField(canonical, "input_digest", snapshot.inputDigest);
    AppendField(canonical, "input_epoch",
                std::to_string(snapshot.inputEpoch));
    AppendField(canonical, "input_sequence",
                std::to_string(snapshot.inputSequence));
    AppendField(canonical, "input_generation",
                std::to_string(snapshot.inputGeneration));
    AppendField(canonical, "feature_generation",
                std::to_string(snapshot.featureGeneration));
    AppendField(canonical, "observed_at_ms",
                std::to_string(snapshot.observedAtMs));
    AppendField(canonical, "fresh_until_ms",
                std::to_string(snapshot.freshUntilMs));
    AppendField(canonical, "mid_raw", std::to_string(snapshot.mid.Raw()));
    AppendField(canonical, "spread_raw",
                std::to_string(snapshot.spread.Raw()));
    return Sha256(canonical);
}

FeatureWriteResult ShardedFeatureStore::Compute(
    const MarketDataSnapshot& input,
    std::uint64_t nowMs,
    const std::string& featureSetId)
{
    FeatureWriteResult result;
    if (featureSetId != "mid-spread-v1")
    {
        result.reasonCode = "FEATURE_SET_UNSUPPORTED";
        return result;
    }
    if (!input.found || input.digest.empty() || input.producerEpoch == 0 ||
        input.sequence == 0 || input.generation == 0)
    {
        result.reasonCode = "FEATURE_INPUT_INCOMPLETE";
        return result;
    }
    if (input.sequenceGap)
    {
        result.reasonCode = "FEATURE_INPUT_SEQUENCE_GAP";
        return result;
    }
    if (nowMs < input.capturedAtMs)
    {
        result.reasonCode = "FEATURE_INPUT_CLOCK_REGRESSION";
        return result;
    }
    if (nowMs > input.freshUntilMs)
    {
        result.reasonCode = "FEATURE_INPUT_STALE";
        return result;
    }
    HeptaFixedDecimal bidAskSum;
    HeptaFixedDecimal spread;
    if (!HeptaFixedDecimal::CheckedAdd(input.bid, input.ask, bidAskSum) ||
        !HeptaFixedDecimal::CheckedSubtract(input.ask, input.bid, spread))
    {
        result.reasonCode = "FEATURE_NUMERIC_OVERFLOW";
        return result;
    }
    if ((bidAskSum.Raw() % 2) != 0)
    {
        result.reasonCode = "FEATURE_NUMERIC_SCALE_MISMATCH";
        return result;
    }

    FeatureKey key;
    key.market = input.key;
    key.featureSetId = featureSetId;
    Shard& shard = m_shards[ShardFor(key)];
    std::lock_guard<std::mutex> lock(shard.mutex);
    std::map<FeatureKey, FeatureSnapshot>::iterator found =
        shard.entries.find(key);
    if (found != shard.entries.end())
    {
        const FeatureSnapshot& current = found->second;
        if (input.producerEpoch < current.inputEpoch ||
            (input.producerEpoch == current.inputEpoch &&
             input.sequence < current.inputSequence) ||
            (input.producerEpoch == current.inputEpoch &&
             input.generation < current.inputGeneration))
        {
            result.reasonCode = "FEATURE_INPUT_REGRESSION";
            return result;
        }
        if (input.producerEpoch == current.inputEpoch &&
            input.sequence == current.inputSequence)
        {
            result.featureGeneration = current.featureGeneration;
            result.digest = current.digest;
            if (input.generation == current.inputGeneration &&
                input.digest == current.inputDigest)
            {
                result.accepted = true;
                result.duplicate = true;
                result.reasonCode = "FEATURE_DUPLICATE";
            }
            else
                result.reasonCode = "FEATURE_INPUT_CONFLICT";
            return result;
        }
        if (current.featureGeneration ==
            std::numeric_limits<std::uint64_t>::max())
        {
            result.reasonCode = "FEATURE_GENERATION_EXHAUSTED";
            return result;
        }
    }
    else if (!ReserveKey())
    {
        result.reasonCode = "FEATURE_CAPACITY_EXHAUSTED";
        return result;
    }

    FeatureSnapshot snapshot;
    snapshot.found = true;
    snapshot.key = input.key;
    snapshot.featureSetId = featureSetId;
    snapshot.inputDigest = input.digest;
    snapshot.inputEpoch = input.producerEpoch;
    snapshot.inputSequence = input.sequence;
    snapshot.inputGeneration = input.generation;
    snapshot.featureGeneration = found == shard.entries.end()
        ? 1u : found->second.featureGeneration + 1u;
    snapshot.observedAtMs = input.observedAtMs;
    snapshot.freshUntilMs = input.freshUntilMs;
    snapshot.mid = HeptaFixedDecimal(bidAskSum.Raw() / 2);
    snapshot.spread = spread;
    snapshot.digest = SnapshotDigest(snapshot);
    if (snapshot.digest.empty())
    {
        if (found == shard.entries.end()) --m_size;
        result.reasonCode = "FEATURE_DIGEST_FAILED";
        return result;
    }
    try
    {
        shard.entries[key] = snapshot;
    }
    catch (...)
    {
        if (found == shard.entries.end()) --m_size;
        result.reasonCode = "FEATURE_STORAGE_FAILED";
        return result;
    }
    result.accepted = true;
    result.featureGeneration = snapshot.featureGeneration;
    result.reasonCode = "FEATURE_ACCEPTED";
    result.digest = snapshot.digest;
    return result;
}

bool ShardedFeatureStore::Get(
    const MarketDataKey& market,
    const std::string& featureSetId,
    FeatureSnapshot& out) const
{
    out = FeatureSnapshot();
    FeatureKey key;
    key.market = market;
    key.featureSetId = featureSetId;
    const Shard& shard = m_shards[ShardFor(key)];
    std::lock_guard<std::mutex> lock(shard.mutex);
    const std::map<FeatureKey, FeatureSnapshot>::const_iterator found =
        shard.entries.find(key);
    if (found == shard.entries.end()) return false;
    out = found->second;
    return true;
}

bool ShardedFeatureStore::GetRiskReady(
    const MarketDataKey& market,
    const std::string& featureSetId,
    std::uint64_t nowMs,
    FeatureSnapshot& out,
    std::string& reason) const
{
    if (!Get(market, featureSetId, out))
    {
        reason = "FEATURE_SNAPSHOT_MISSING";
        return false;
    }
    if (nowMs < out.observedAtMs)
    {
        reason = "FEATURE_CLOCK_REGRESSION";
        return false;
    }
    if (nowMs > out.freshUntilMs)
    {
        reason = "FEATURE_SNAPSHOT_STALE";
        return false;
    }
    reason.clear();
    return true;
}
