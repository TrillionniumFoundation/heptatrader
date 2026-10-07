#pragma once
#include "ib_gateway_adapter.h"

// A complete physical reqPositions observation may prove a historical crypto
// fill while FX cash is fenced. It NEVER proves account risk or permits a send.
//
// Cash-attestation failure intentionally prevents the coherent account-risk
// refresh from advancing riskAbsorbedExposureGeneration. Requiring that field
// here would therefore make the documented read-only settlement path
// unreachable after an actual crypto fill. For settlement evidence, require
// instead that authoritative terminal evidence has caught up exactly to the
// current exposure generation, while positions/active/terminal snapshots are
// complete in the same Broker epoch and no callback conflict is present.
// riskAbsorbedExposureGeneration may lag, but may never be ahead of exposure;
// it remains unchanged and all mutation/recovery barriers stay fail-closed.
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
        s.terminalExposureGeneration == s.exposureGeneration &&
        s.riskAbsorbedExposureGeneration <= s.exposureGeneration &&
        !s.newConnectionEpochRequired && s.callbackConflictCount == 0 &&
        !s.callbackConflictMetricsSaturated;
}
