#pragma once
// A changed IB CashBalance push invalidates the risk generation. Re-acquire
// a broker-declared account+positions risk generation under the SAME
// independently complete order/terminal generation, never from the push.
#include "adapter_ib/ib_gateway_adapter.h"
#include <cstdint>

inline bool CanRequestCashRecoveryReaudit(
    const IBAuthoritativeRecoveryAuditSnapshot& snapshot,
    std::uint64_t connectionEpoch, bool postFillPending,
    std::uint64_t lastAttemptedEpoch, std::uint64_t lastAttemptedRiskGeneration)
{
    return connectionEpoch != 0 &&
        snapshot.reasonCode == "IB_FX_CASH_BALANCE_OUT_OF_GENERATION_CHANGE" &&
        !snapshot.barrierComplete && !postFillPending &&
        snapshot.risk.connectionEpoch == connectionEpoch &&
        snapshot.risk.generation != 0 &&
        snapshot.active.complete && snapshot.active.generation != 0 &&
        snapshot.active.connectionEpoch == connectionEpoch &&
        snapshot.active.activeOrderIds.empty() &&
        snapshot.terminal.complete && snapshot.terminal.generation != 0 &&
        snapshot.terminal.connectionEpoch == connectionEpoch &&
        snapshot.terminalExposureGeneration <= snapshot.exposureGeneration &&
        !(lastAttemptedEpoch == connectionEpoch &&
          lastAttemptedRiskGeneration == snapshot.risk.generation);
}
