#pragma once

#include "../adapter_ib/ib_gateway_adapter.h"

#include <cmath>
#include <cstdint>
#include <sstream>
#include <string>

// Fixed-cardinality process observation. It is read-only, carries no credentials,
// command/order identifiers or authorization effect, and is sampled from the
// adapter's lock-bound recovery-audit snapshot rather than by joining unrelated
// getters in userspace.
struct IbRuntimeObservationSnapshot
{
    std::uint64_t observedAtMs = 0;
    std::uint64_t monotonicMs = 0;
    std::string serviceEpoch;
    bool connected = false;
    bool eventStreamAuthoritative = false;
    std::uint64_t eventOverflowGeneration = 0;

    std::uint64_t connectionEpoch = 0;
    std::uint64_t activeGeneration = 0;
    bool activeComplete = false;
    std::uint64_t activeOrders = 0;
    std::uint64_t activeCorrelations = 0;
    std::string activeReason;

    std::uint64_t terminalGeneration = 0;
    bool terminalComplete = false;
    std::uint64_t terminalOrders = 0;
    std::uint64_t terminalExecutions = 0;
    std::uint64_t terminalExposureGeneration = 0;
    std::string terminalReason;

    std::uint64_t riskGeneration = 0;
    std::uint64_t accountGeneration = 0;
    std::uint64_t positionsGeneration = 0;
    std::uint64_t fxCashGeneration = 0;
    bool riskComplete = false;
    bool coherentRiskComplete = false;
    bool accountComplete = false;
    bool positionsComplete = false;
    bool fxCashComplete = false;
    std::uint64_t riskAbsorbedExposureGeneration = 0;
    double grossAbsolutePosition = 0.0;
    std::string riskReason;

    std::uint64_t positions = 0;
    bool postFillRiskReconciliationPending = false;
    std::uint64_t exposureGeneration = 0;
    bool recoveryBarrierComplete = false;
    bool newConnectionEpochRequired = false;
    std::string recoveryReason;

    bool terminalTransportHalted = false;
    bool terminalTransportDrainVerified = false;
    std::uint64_t terminalCallbacksInFlight = 0;
    OmsLatencySummary callbackQueueLag;
    std::uint64_t callbackConflictCount = 0;
    bool callbackConflictMetricsSaturated = false;
    bool quoteAgeMetricsPresent = false;
    bool primaryQuoteAgeValid = false;
    std::uint64_t primaryQuoteAgeMs = 0;
    bool snapshotAgeMetricsPresent = false;
    bool authoritativeSnapshotAgeValid = false;
    std::uint64_t authoritativeSnapshotAgeMs = 0;
    bool brokerReconciliationDurationMetricsPresent = false;
    OmsLatencySummary brokerReconciliationDuration;
};
struct IbRuntimeObservationSupplement
{
    bool quoteAgeMetricsPresent = false;
    bool primaryQuoteAgeValid = false;
    std::uint64_t primaryQuoteAgeMs = 0;
    bool snapshotAgeMetricsPresent = false;
    bool authoritativeSnapshotAgeValid = false;
    std::uint64_t authoritativeSnapshotAgeMs = 0;
    bool brokerReconciliationDurationMetricsPresent = false;
    OmsLatencySummary brokerReconciliationDuration;
};


inline IbRuntimeObservationSnapshot CaptureIbRuntimeObservation(
    HeptaIBGatewayAdapter& adapter,
    std::uint64_t observedAtMs,
    std::uint64_t monotonicMs,
    const std::string& serviceEpoch,
    const IbRuntimeObservationSupplement& supplement =
        IbRuntimeObservationSupplement())
{
    const IBAuthoritativeRecoveryAuditSnapshot audit =
        adapter.GetAuthoritativeRecoveryAuditSnapshot();
    IbRuntimeObservationSnapshot out;
    out.observedAtMs = observedAtMs;
    out.monotonicMs = monotonicMs;
    out.serviceEpoch = serviceEpoch;
    out.connected = adapter.IsConnected();
    out.eventStreamAuthoritative = adapter.IsEventStreamAuthoritative();
    out.eventOverflowGeneration = adapter.GetLastEventOverflowGeneration();

    out.connectionEpoch = audit.risk.connectionEpoch;
    out.activeGeneration = audit.active.generation;
    out.activeComplete = audit.active.complete;
    out.activeOrders = static_cast<std::uint64_t>(audit.active.activeOrderIds.size());
    out.activeCorrelations = static_cast<std::uint64_t>(
        audit.active.activeOrderIdsByCorrelation.size());
    out.activeReason = audit.active.reasonCode;

    out.terminalGeneration = audit.terminal.generation;
    out.terminalComplete = audit.terminal.complete;
    out.terminalOrders = static_cast<std::uint64_t>(
        audit.terminal.terminalOrderIdsByCorrelation.size());
    out.terminalExecutions = static_cast<std::uint64_t>(
        audit.terminal.executionOrderIds.size());
    out.terminalExposureGeneration = audit.terminal.exposureGeneration;
    out.terminalReason = audit.terminal.reasonCode;

    out.riskGeneration = audit.risk.generation;
    out.accountGeneration = audit.risk.accountGeneration;
    out.positionsGeneration = audit.risk.positionsGeneration;
    out.fxCashGeneration = audit.risk.fxCashGeneration;
    out.riskComplete = audit.risk.complete;
    out.coherentRiskComplete = audit.risk.coherentRefreshComplete;
    out.accountComplete = audit.risk.accountComplete;
    out.positionsComplete = audit.risk.positionsComplete;
    out.fxCashComplete = audit.risk.fxCashComplete;
    out.riskAbsorbedExposureGeneration =
        audit.risk.riskAbsorbedExposureGeneration;
    out.grossAbsolutePosition = audit.risk.grossAbsolutePosition;
    out.riskReason = audit.risk.reasonCode;

    out.positions = static_cast<std::uint64_t>(audit.positionQuantities.size());
    out.postFillRiskReconciliationPending =
        audit.postFillRiskReconciliationPending;
    out.exposureGeneration = audit.exposureGeneration;
    out.recoveryBarrierComplete = audit.barrierComplete;
    out.newConnectionEpochRequired = audit.newConnectionEpochRequired;
    out.recoveryReason = audit.reasonCode;

    out.terminalTransportHalted = adapter.IsTerminalTransportHalted();
    out.terminalTransportDrainVerified =
        adapter.IsTerminalTransportDrainVerified();
    out.terminalCallbacksInFlight = adapter.TerminalCallbacksInFlight();
    out.callbackQueueLag = audit.callbackQueueLag;
    out.callbackConflictCount = audit.callbackConflictCount;
    out.callbackConflictMetricsSaturated =
        audit.callbackConflictMetricsSaturated;
    out.quoteAgeMetricsPresent = supplement.quoteAgeMetricsPresent;
    out.primaryQuoteAgeValid = supplement.primaryQuoteAgeValid;
    out.primaryQuoteAgeMs = supplement.primaryQuoteAgeMs;
    out.snapshotAgeMetricsPresent = supplement.snapshotAgeMetricsPresent;
    out.authoritativeSnapshotAgeValid =
        supplement.authoritativeSnapshotAgeValid;
    out.authoritativeSnapshotAgeMs =
        supplement.authoritativeSnapshotAgeMs;
    out.brokerReconciliationDurationMetricsPresent =
        supplement.brokerReconciliationDurationMetricsPresent;
    out.brokerReconciliationDuration =
        supplement.brokerReconciliationDuration;
    return out;
}

inline std::string EscapeIbRuntimeObservationJson(const std::string& value)
{
    std::string out;
    out.reserve(value.size() + 8U);
    static const char digits[] = "0123456789abcdef";
    for (std::string::const_iterator it = value.begin(); it != value.end(); ++it)
    {
        const unsigned char ch = static_cast<unsigned char>(*it);
        switch (ch)
        {
        case '\\': out += "\\\\"; break;
        case '"': out += "\\\""; break;
        case '\n': out += "\\n"; break;
        case '\r': out += "\\r"; break;
        case '\t': out += "\\t"; break;
        default:
            if (ch < 0x20)
            {
                out += "\\u00";
                out += digits[(ch >> 4) & 0x0f];
                out += digits[ch & 0x0f];
            }
            else out.push_back(static_cast<char>(ch));
        }
    }
    return out;
}

inline std::string SerializeIbRuntimeObservation(
    const IbRuntimeObservationSnapshot& value)
{
    std::ostringstream out;
    out.precision(17);
    const double gross = std::isfinite(value.grossAbsolutePosition) ?
        value.grossAbsolutePosition : 0.0;
    out << "{\"schema\":\"heptatrader.ib-runtime-observation.v1\""
        << ",\"authorization_effect\":\"NONE\""
        << ",\"paper_authorized\":false,\"live_authorized\":false"
        << ",\"observed_at_ms\":" << value.observedAtMs
        << ",\"monotonic_ms\":" << value.monotonicMs
        << ",\"service_epoch\":\"" << EscapeIbRuntimeObservationJson(value.serviceEpoch) << "\""
        << ",\"connected\":" << (value.connected ? "true" : "false")
        << ",\"event_stream_authoritative\":" << (value.eventStreamAuthoritative ? "true" : "false")
        << ",\"event_overflow_generation\":" << value.eventOverflowGeneration
        << ",\"connection_epoch\":" << value.connectionEpoch
        << ",\"active_generation\":" << value.activeGeneration
        << ",\"active_complete\":" << (value.activeComplete ? "true" : "false")
        << ",\"active_orders\":" << value.activeOrders
        << ",\"active_correlations\":" << value.activeCorrelations
        << ",\"active_reason\":\"" << EscapeIbRuntimeObservationJson(value.activeReason) << "\""
        << ",\"terminal_generation\":" << value.terminalGeneration
        << ",\"terminal_complete\":" << (value.terminalComplete ? "true" : "false")
        << ",\"terminal_orders\":" << value.terminalOrders
        << ",\"terminal_executions\":" << value.terminalExecutions
        << ",\"terminal_exposure_generation\":" << value.terminalExposureGeneration
        << ",\"terminal_reason\":\"" << EscapeIbRuntimeObservationJson(value.terminalReason) << "\""
        << ",\"risk_generation\":" << value.riskGeneration
        << ",\"account_generation\":" << value.accountGeneration
        << ",\"positions_generation\":" << value.positionsGeneration
        << ",\"fx_cash_generation\":" << value.fxCashGeneration
        << ",\"risk_complete\":" << (value.riskComplete ? "true" : "false")
        << ",\"coherent_risk_complete\":" << (value.coherentRiskComplete ? "true" : "false")
        << ",\"account_complete\":" << (value.accountComplete ? "true" : "false")
        << ",\"positions_complete\":" << (value.positionsComplete ? "true" : "false")
        << ",\"fx_cash_complete\":" << (value.fxCashComplete ? "true" : "false")
        << ",\"risk_absorbed_exposure_generation\":" << value.riskAbsorbedExposureGeneration
        << ",\"gross_absolute_position\":" << gross
        << ",\"risk_reason\":\"" << EscapeIbRuntimeObservationJson(value.riskReason) << "\""
        << ",\"positions\":" << value.positions
        << ",\"post_fill_risk_reconciliation_pending\":"
        << (value.postFillRiskReconciliationPending ? "true" : "false")
        << ",\"exposure_generation\":" << value.exposureGeneration
        << ",\"recovery_barrier_complete\":" << (value.recoveryBarrierComplete ? "true" : "false")
        << ",\"new_connection_epoch_required\":" << (value.newConnectionEpochRequired ? "true" : "false")
        << ",\"recovery_reason\":\"" << EscapeIbRuntimeObservationJson(value.recoveryReason) << "\""
        << ",\"terminal_transport_halted\":" << (value.terminalTransportHalted ? "true" : "false")
        << ",\"terminal_transport_drain_verified\":"
        << (value.terminalTransportDrainVerified ? "true" : "false")
        << ",\"terminal_callbacks_in_flight\":" << value.terminalCallbacksInFlight
        << ",\"callback_lag_metrics_present\":true"
        << ",\"callback_queue_lag\":";
    WriteOmsLatencyJson(out, value.callbackQueueLag);
    out << ",\"callback_conflict_metrics_present\":true"
        << ",\"callback_conflicts_total\":" << value.callbackConflictCount
        << ",\"callback_conflict_metrics_saturated\":"
        << (value.callbackConflictMetricsSaturated ? "true" : "false")
        << ",\"quote_age_metrics_present\":"
        << (value.quoteAgeMetricsPresent ? "true" : "false")
        << ",\"primary_quote_age_valid\":"
        << (value.primaryQuoteAgeValid ? "true" : "false")
        << ",\"primary_quote_age_ms\":" << value.primaryQuoteAgeMs
        << ",\"snapshot_age_metrics_present\":"
        << (value.snapshotAgeMetricsPresent ? "true" : "false")
        << ",\"authoritative_snapshot_age_valid\":"
        << (value.authoritativeSnapshotAgeValid ? "true" : "false")
        << ",\"authoritative_snapshot_age_ms\":"
        << value.authoritativeSnapshotAgeMs
        << ",\"broker_reconciliation_duration_metrics_present\":"
        << (value.brokerReconciliationDurationMetricsPresent ? "true" : "false")
        << ",\"broker_reconciliation_duration\":";
    WriteOmsLatencyJson(out, value.brokerReconciliationDuration);
    out
        // Network policy is host-owned and stays absent until an actual
        // nftables readback producer supplies it.
        << ",\"network_policy_metrics_present\":false"
        << "}";
    return out.str();
}

inline std::string IbRuntimeObservation(
    HeptaIBGatewayAdapter& adapter,
    std::uint64_t observedAtMs,
    std::uint64_t monotonicMs,
    const std::string& serviceEpoch,
    const IbRuntimeObservationSupplement& supplement =
        IbRuntimeObservationSupplement())
{
    return SerializeIbRuntimeObservation(CaptureIbRuntimeObservation(
        adapter, observedAtMs, monotonicMs, serviceEpoch, supplement));
}
