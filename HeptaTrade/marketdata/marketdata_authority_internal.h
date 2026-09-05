#pragma once

#include "sharded_market_data.h"

#include <atomic>
#include <cstddef>
#include <cstdint>
#include <functional>
#include <memory>
#include <mutex>
#include <string>

struct MarketDataAuthorityState
{
    mutable std::mutex mutex;
    ShardedMarketDataStore::Clock clock;
    const ShardedMarketDataStore* store = nullptr;
    std::uint64_t issuerId = 0;
    std::uint64_t lifecycleEpoch = 1;
    std::uint64_t lastTrustedNowMs = 0;
    std::uint64_t nextConsumerId = 1;
    std::uint64_t nextReceiptNonce = 1;
    bool clockFaulted = false;
    bool alive = true;
};

namespace hepta_marketdata_internal
{
inline bool BoundedPrintable(const std::string& value, std::size_t maximum)
{
    if (value.empty() || value.size() > maximum) return false;
    for (std::size_t i = 0; i < value.size(); ++i)
    {
        const unsigned char c = static_cast<unsigned char>(value[i]);
        if (c < 0x21 || c > 0x7e) return false;
    }
    return true;
}

inline bool CanonicalDigest(const std::string& value)
{
    if (value.size() != 71u || value.compare(0, 7, "sha256:") != 0)
        return false;
    for (std::size_t i = 7; i < value.size(); ++i)
    {
        const char c = value[i];
        if (!((c >= '0' && c <= '9') || (c >= 'a' && c <= 'f')))
            return false;
    }
    return true;
}
}
