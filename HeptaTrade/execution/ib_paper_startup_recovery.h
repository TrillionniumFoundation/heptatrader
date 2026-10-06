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

// A verified cash discrepancy is a trading fence, not a transport failure.
// Retain diagnostic IPC only after the SAME live epoch has finished positions,
// active orders and terminal downloads. Never mark the cash snapshot complete,
// rebase an account, checkpoint the mismatch, or promote the process later.
inline bool CanKeepFxCashMismatchReadOnly(
    bool userScope, bool connected, bool eventStreamAuthoritative,
    std::uint64_t epoch, const IBAuthoritativeRecoveryAuditSnapshot& recovery)
{
    const auto& risk = recovery.risk;
    const auto& active = recovery.active;
    const auto& terminal = recovery.terminal;
    return userScope && connected && eventStreamAuthoritative && epoch != 0 &&
        risk.reasonCode == "IB_FX_CASH_ATTESTED_BALANCE_MISMATCH" &&
        !risk.accountComplete && !risk.fxCashComplete &&
        risk.positionsComplete && risk.accountGeneration != 0 &&
        risk.positionsGeneration != 0 && risk.connectionEpoch == epoch &&
        active.complete && active.generation != 0 && active.connectionEpoch == epoch &&
        terminal.complete && terminal.generation != 0 && terminal.connectionEpoch == epoch;
}
