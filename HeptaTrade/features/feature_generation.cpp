#include "feature_generation.h"

#include <iomanip>
#include <limits>
#include <openssl/evp.h>
#include <sstream>
#include <type_traits>
#include <utility>

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
    : m_size(0), m_maximumKeys(maximumKeys), m_marketAuthority()
{
}

ShardedFeatureStore::ShardedFeatureStore(
    MarketDataConsumerBinding&& marketAuthority,
    std::size_t maximumKeys)
    : m_size(0),
      m_maximumKeys(maximumKeys),
      m_marketAuthority(std::move(marketAuthority))
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

void ShardedFeatureStore::SetAuthorityValidatedHookForTesting(
    const std::function<void()>& hook)
{
    std::lock_guard<std::mutex> lock(m_authorityHookMutex);
    m_authorityValidatedHook = hook;
}

std::string ShardedFeatureStore::AuthorityFailure(
    const std::string& marketReason)
{
    if (marketReason == "MARKET_RECEIPT_INVALID")
        return "FEATURE_INPUT_RECEIPT_INVALID";
    if (marketReason == "MARKET_SEQUENCE_GAP")
        return "FEATURE_INPUT_SEQUENCE_GAP";
    if (marketReason == "MARKET_SNAPSHOT_STALE")
        return "FEATURE_INPUT_STALE";
    if (marketReason == "MARKET_AUTHORITY_CLOCK_REGRESSION")
        return "FEATURE_INPUT_CLOCK_REGRESSION";
    if (marketReason == "MARKET_RECEIPT_SUPERSEDED")
        return "FEATURE_INPUT_SUPERSEDED";
    if (marketReason == "MARKET_AUTHORITY_BINDING_INVALID")
        return "FEATURE_MARKET_AUTHORITY_REQUIRED";
    if (marketReason == "MARKET_AUTHORITY_DESTROYED")
        return "FEATURE_INPUT_ISSUER_DESTROYED";
    if (marketReason == "MARKET_RECEIPT_AUDIENCE_MISMATCH")
        return "FEATURE_INPUT_AUDIENCE_MISMATCH";
    if (marketReason == "MARKET_RECEIPT_EPOCH_MISMATCH" ||
        marketReason == "MARKET_AUTHORITY_EPOCH_MISMATCH")
        return "FEATURE_INPUT_AUTHORITY_FENCED";
    if (marketReason == "MARKET_RECEIPT_ISSUER_MISMATCH" ||
        marketReason == "MARKET_AUTHORITY_ISSUER_MISMATCH")
        return "FEATURE_INPUT_ISSUER_MISMATCH";
    if (marketReason == "MARKET_AUTHORITY_CLOCK_INVALID" ||
        marketReason == "MARKET_AUTHORITY_CLOCK_FAILED")
        return "FEATURE_INPUT_CLOCK_INVALID";
    return "FEATURE_INPUT_AUTHORITY_INVALID";
}

FeatureWriteResult ShardedFeatureStore::Compute(
    const MarketDataSnapshot&,
    std::uint64_t,
    const std::string&)
{
    FeatureWriteResult result;
    result.reasonCode = "FEATURE_INPUT_RECEIPT_REQUIRED";
    return result;
}

FeatureWriteResult ShardedFeatureStore::Compute(
    const MarketDataSnapshotReceipt&,
    std::uint64_t,
    const std::string&)
{
    FeatureWriteResult result;
    result.reasonCode = "FEATURE_CALLER_TIME_FORBIDDEN";
    return result;
}

FeatureWriteResult ShardedFeatureStore::Compute(
    const MarketDataSnapshotReceipt& receipt,
    const std::string& featureSetId)
{
    FeatureWriteResult result;
    if (featureSetId != "mid-spread-v1")
    {
        result.reasonCode = "FEATURE_SET_UNSUPPORTED";
        return result;
    }

    std::string marketReason;
    const bool sourceCurrent = m_marketAuthority.WithCurrentReceipt(
        receipt,
        [this, &result, &featureSetId](const MarketDataSnapshot& input) {
            if (!input.found || input.digest.empty() ||
                input.producerEpoch == 0 || input.sequence == 0 ||
                input.generation == 0)
            {
                result.reasonCode = "FEATURE_INPUT_INCOMPLETE";
                return;
            }
            std::string inputReason;
            if (!ShardedMarketDataStore::ValidateSnapshot(input, inputReason))
            {
                result.reasonCode = "FEATURE_INPUT_INVALID";
                return;
            }

            std::function<void()> hook;
            {
                std::lock_guard<std::mutex> hookLock(m_authorityHookMutex);
                hook = m_authorityValidatedHook;
            }
            if (hook) hook();

            HeptaFixedDecimal bidAskSum;
            HeptaFixedDecimal spread;
            if (!HeptaFixedDecimal::CheckedAdd(
                    input.bid, input.ask, bidAskSum) ||
                !HeptaFixedDecimal::CheckedSubtract(
                    input.ask, input.bid, spread))
            {
                result.reasonCode = "FEATURE_NUMERIC_OVERFLOW";
                return;
            }
            if ((bidAskSum.Raw() % 2) != 0)
            {
                result.reasonCode = "FEATURE_NUMERIC_SCALE_MISMATCH";
                return;
            }

            FeatureKey key;
            key.market = input.key;
            key.featureSetId = featureSetId;
            Shard& shard = m_shards[ShardFor(key)];
            std::lock_guard<std::mutex> featureLock(shard.mutex);
            std::map<FeatureKey, FeatureSnapshot>::iterator found =
                shard.entries.find(key);
            const bool isNew = found == shard.entries.end();
            if (!isNew)
            {
                const FeatureSnapshot& current = found->second;
                if (input.producerEpoch < current.inputEpoch ||
                    (input.producerEpoch == current.inputEpoch &&
                     input.sequence < current.inputSequence) ||
                    (input.producerEpoch == current.inputEpoch &&
                     input.generation < current.inputGeneration))
                {
                    result.reasonCode = "FEATURE_INPUT_REGRESSION";
                    return;
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
                    return;
                }
                if (current.featureGeneration ==
                    std::numeric_limits<std::uint64_t>::max())
                {
                    result.reasonCode = "FEATURE_GENERATION_EXHAUSTED";
                    return;
                }
            }

            FeatureSnapshot snapshot;
            snapshot.found = true;
            snapshot.key = input.key;
            snapshot.featureSetId = featureSetId;
            snapshot.inputDigest = input.digest;
            snapshot.inputEpoch = input.producerEpoch;
            snapshot.inputSequence = input.sequence;
            snapshot.inputGeneration = input.generation;
            snapshot.featureGeneration = isNew
                ? 1u : found->second.featureGeneration + 1u;
            snapshot.observedAtMs = input.observedAtMs;
            snapshot.freshUntilMs = input.freshUntilMs;
            std::string numericReason;
            if (!HeptaFixedDecimal::FromRawExact(
                    bidAskSum.Raw() / 2, snapshot.mid, numericReason))
            {
                result.reasonCode = "FEATURE_NUMERIC_OVERFLOW";
                return;
            }
            snapshot.spread = spread;
            snapshot.digest = SnapshotDigest(snapshot);
            if (snapshot.digest.empty())
            {
                result.reasonCode = "FEATURE_DIGEST_FAILED";
                return;
            }
            const std::string committedDigest = snapshot.digest;
            const std::uint64_t committedGeneration =
                snapshot.featureGeneration;

            if (isNew)
            {
                if (!ReserveKey())
                {
                    result.reasonCode = "FEATURE_CAPACITY_EXHAUSTED";
                    return;
                }
                try
                {
                    const std::pair<
                        std::map<FeatureKey, FeatureSnapshot>::iterator, bool>
                        inserted = shard.entries.emplace(
                            std::move(key), std::move(snapshot));
                    if (!inserted.second)
                    {
                        --m_size;
                        result.reasonCode = "FEATURE_INPUT_CONFLICT";
                        return;
                    }
                }
                catch (...)
                {
                    --m_size;
                    result.reasonCode = "FEATURE_STORAGE_FAILED";
                    return;
                }
            }
            else
            {
                static_assert(
                    std::is_nothrow_move_assignable<FeatureSnapshot>::value,
                    "FeatureSnapshot replacement must preserve strong state");
                found->second = std::move(snapshot);
            }

            result.accepted = true;
            result.featureGeneration = committedGeneration;
            result.reasonCode = "FEATURE_ACCEPTED";
            result.digest = committedDigest;
        },
        marketReason);
    if (!sourceCurrent)
        result.reasonCode = AuthorityFailure(marketReason);
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
    FeatureSnapshot& out,
    std::string& reason) const
{
    out = FeatureSnapshot();
    if (!Get(market, featureSetId, out))
    {
        reason = "FEATURE_SNAPSHOT_MISSING";
        return false;
    }
    if (!out.found || out.featureGeneration == 0 ||
        out.inputEpoch == 0 || out.inputSequence == 0 ||
        out.inputGeneration == 0 || out.digest.empty() ||
        SnapshotDigest(out) != out.digest)
    {
        out = FeatureSnapshot();
        reason = "FEATURE_SNAPSHOT_INVALID";
        return false;
    }
    MarketDataSnapshot current;
    std::string marketReason;
    if (!m_marketAuthority.ResolveLineage(
            out.key, out.inputEpoch, out.inputSequence,
            out.inputGeneration, out.inputDigest, current, marketReason))
    {
        out = FeatureSnapshot();
        reason = AuthorityFailure(marketReason);
        return false;
    }
    reason.clear();
    return true;
}

bool ShardedFeatureStore::GetRiskReady(
    const MarketDataKey&,
    const std::string&,
    std::uint64_t,
    FeatureSnapshot& out,
    std::string& reason) const
{
    out = FeatureSnapshot();
    reason = "FEATURE_CALLER_TIME_FORBIDDEN";
    return false;
}
