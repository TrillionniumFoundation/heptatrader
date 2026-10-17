#pragma once

#include "../adapter_ib/ib_gateway_adapter.h"

#include <cmath>
#include <cstddef>
#include <cstdint>

// Read-only diagnostic from ONE original adapter-lock snapshot. Never a
// Broker maintenance STOP permit: IB open-order/position/terminal downloads
// are not an atomic cross-client point-in-time transaction, and no
// post-account-fence refresh generation is bound by this predicate.
struct IbPaperAccountBrokerEvidence
{
    bool snapshotCoherent = false;
    bool brokerZeroObserved = false;
    std::size_t globalActiveOrders = 0;
    std::size_t foreignActiveOrders = 0;
    const char* reasonCode = "IB_ACCOUNT_BROKER_EVIDENCE_UNAVAILABLE";
};

inline IbPaperAccountBrokerEvidence InspectIbPaperAccountBrokerEvidence(
    const IBAuthoritativeRecoveryAuditSnapshot& snapshot, int ownClientId)
{
    IbPaperAccountBrokerEvidence result;
    const IBAuthoritativeCorrelationSnapshot& active = snapshot.active;
    const IBAuthoritativeTerminalCorrelationSnapshot& terminal =
        snapshot.terminal;
    const IBAuthoritativeRiskSnapshot& risk = snapshot.risk;
    result.globalActiveOrders = active.activeOrderIds.size();
    if (!snapshot.transportConnected) {
        result.reasonCode = "IB_ACCOUNT_BROKER_NOT_CONNECTED"; return result;
    }
    if (!snapshot.eventStreamAuthoritative) {
        result.reasonCode = "IB_ACCOUNT_BROKER_EVENT_STREAM_UNTRUSTED"; return result;
    }
    if (!active.complete || !terminal.complete ||
        !risk.complete || !risk.coherentRefreshComplete ||
        !risk.accountComplete || !risk.positionsComplete ||
        !risk.fxCashComplete ||
        active.generation == 0 || terminal.generation == 0 ||
        risk.generation == 0) {
        result.reasonCode = "IB_ACCOUNT_BROKER_SNAPSHOT_INCOMPLETE"; return result;
    }
    if (active.connectionEpoch == 0 ||
        active.connectionEpoch != terminal.connectionEpoch ||
        active.connectionEpoch != risk.connectionEpoch) {
        result.reasonCode = "IB_ACCOUNT_BROKER_CONNECTION_EPOCH_MISMATCH";
        return result;
    }
    // reqAllOpenOrders() contains OTHER API client IDs; the numeric order ID
    // is never a global owner key. PR #391 invalidates collisions. Verify
    // its native identity projection here as part of every scoped read.
    if (ownClientId < 0 ||
        active.activeOrderIds.size() != active.activeOrderClientIdsByOrderId.size()) {
        result.reasonCode = "IB_ACCOUNT_BROKER_CLIENT_IDENTITY_INCOMPLETE";
        return result;
    }
    for (const auto& id : active.activeOrderIds) {
        const auto client = active.activeOrderClientIdsByOrderId.find(id);
        if (id <= 0 || client == active.activeOrderClientIdsByOrderId.end() ||
            client->second < 0) {
            result.reasonCode = "IB_ACCOUNT_BROKER_CLIENT_IDENTITY_INCOMPLETE";
            return result;
        }
        if (client->second != ownClientId) ++result.foreignActiveOrders;
    }
    for (const auto& correlation : active.activeOrderIdsByCorrelation) {
        const auto client = active.activeOrderClientIdsByOrderId.find(
            correlation.second);
        if (correlation.first.empty() ||
            client == active.activeOrderClientIdsByOrderId.end() ||
            client->second != ownClientId) {
            result.reasonCode = "IB_ACCOUNT_BROKER_CORRELATION_SCOPE_INVALID";
            return result;
        }
    }
    if (!snapshot.barrierComplete || snapshot.newConnectionEpochRequired) {
        result.reasonCode = "IB_ACCOUNT_BROKER_RECOVERY_BARRIER_INCOMPLETE";
        return result;
    }
    if (snapshot.postFillRiskReconciliationPending) {
        result.reasonCode = "IB_ACCOUNT_BROKER_POSTFILL_RECONCILIATION_PENDING";
        return result;
    }
    // A terminal read from before a subsequent fill must not be joined to
    // the newer account+positions view to manufacture a settled flat state.
    if (snapshot.riskAbsorbedExposureGeneration != snapshot.exposureGeneration ||
        snapshot.terminalExposureGeneration != snapshot.exposureGeneration ||
        risk.riskAbsorbedExposureGeneration != snapshot.exposureGeneration ||
        terminal.exposureGeneration != snapshot.exposureGeneration) {
        result.reasonCode = "IB_ACCOUNT_BROKER_EXPOSURE_GENERATION_MISMATCH";
        return result;
    }
    if (!std::isfinite(risk.grossAbsolutePosition) ||
        risk.grossAbsolutePosition < 0.0) {
        result.reasonCode = "IB_ACCOUNT_BROKER_POSITION_INVALID"; return result;
    }
    // This read-only maintenance diagnostic may never round away a real
    // nonzero residual as "zero"; quote/trading tolerances are NOT stop proof.
    bool flat = risk.grossAbsolutePosition == 0.0;
    for (const auto& position : snapshot.positionQuantities) {
        if (position.first.empty() || !std::isfinite(position.second)) {
            result.reasonCode = "IB_ACCOUNT_BROKER_POSITION_INVALID";
            return result;
        }
        if (position.second != 0.0) flat = false;
    }
    result.snapshotCoherent = true;
    if (result.globalActiveOrders != 0) {
        result.reasonCode = "IB_ACCOUNT_BROKER_ACTIVE_ORDERS_REMAIN";
        return result;
    }
    if (!flat) {
        result.reasonCode = "IB_ACCOUNT_BROKER_POSITION_REMAINS";
        return result;
    }
    result.brokerZeroObserved = true;
    result.reasonCode = "IB_ACCOUNT_BROKER_ZERO_OBSERVED_NOT_STOP_PERMIT";
    return result;
}
