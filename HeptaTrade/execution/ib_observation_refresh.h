#pragma once

// This is the actual reconnect request selection used by the native runtime.
// Account/quote observation never asks historical order ownership to become
// complete. Trading retains its original open/terminal then risk barrier.
template <typename Adapter>
bool DispatchIbReconnectReadEvidence(Adapter& adapter, bool observationOnly)
{
    if (observationOnly) return adapter.ReqAccountSummary();
    return adapter.ReqAuthoritativeOpenOrders() && adapter.ReqTerminalCorrelations();
}
