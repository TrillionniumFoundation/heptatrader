#pragma once
#include "../adapter_ib/ib_gateway_adapter.h"

// Availability fallback, never terminal evidence or permission to trade.
// Keep the exact live connection when a complete, correlated active-order
// download exists but the broker omits completedOrdersEnd. A timeout must not
// fabricate an empty completed-order set or discard subsequent original fills.
inline bool CanKeepPendingOrderReadOnly(
    bool userScope, bool connected, bool eventStreamAuthoritative,
    std::uint64_t epoch, const IBAuthoritativeCorrelationSnapshot& active,
    const IBAuthoritativeTerminalCorrelationSnapshot& terminal)
{
    if (!userScope || !connected || !eventStreamAuthoritative || epoch == 0 ||
        !active.complete || active.connectionEpoch != epoch || active.generation == 0 ||
        active.activeOrderIds.empty() ||
        active.activeOrderIdsByCorrelation.size() != active.activeOrderIds.size() ||
        terminal.complete || terminal.connectionEpoch != epoch || terminal.generation == 0 ||
        terminal.reasonCode != "IB_TERMINAL_CORRELATION_REFRESH_PENDING")
        return false;
    std::set<long> bound;
    for (const auto& item : active.activeOrderIdsByCorrelation)
        if (item.first.empty() || item.second < 0 || !active.activeOrderIds.count(item.second) ||
            !bound.insert(item.second).second) return false;
    return bound == active.activeOrderIds;
}
