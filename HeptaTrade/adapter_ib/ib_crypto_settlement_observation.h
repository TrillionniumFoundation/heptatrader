#pragma once
#include "ib_gateway_adapter.h"

// A complete physical reqPositions observation may prove a historical crypto
// fill while FX cash is fenced. It NEVER proves account risk or permits a send.
inline bool CanObservePhysicalCryptoPositions(bool connected, bool eventStream,
    std::uint64_t epoch, const IBAuthoritativeRecoveryAuditSnapshot& s) {
    return connected && eventStream && epoch != 0 &&
        s.risk.positionsComplete && s.risk.positionsGeneration != 0 &&
        s.risk.connectionEpoch == epoch &&
        s.active.complete && s.active.generation != 0 &&
        s.active.connectionEpoch == epoch && s.active.activeOrderIds.empty() &&
        s.active.activeOrderIdsByCorrelation.empty() &&
        s.terminal.complete && s.terminal.generation != 0 &&
        s.terminal.connectionEpoch == epoch &&
        s.riskAbsorbedExposureGeneration == s.exposureGeneration &&
        s.terminalExposureGeneration <= s.riskAbsorbedExposureGeneration &&
        !s.newConnectionEpochRequired && s.callbackConflictCount == 0 &&
        !s.callbackConflictMetricsSaturated;
}
