#include "ib_gateway_adapter.h"

#include <algorithm>
#include <cerrno>
#include <chrono>
#include <climits>
#include <cmath>
#include <cstdlib>
#include <limits>

namespace {
std::string EscapeJson(const std::string& value) {
    std::string escaped;
    escaped.reserve(value.size() + 8);
    for (char ch : value) {
        switch (ch) {
        case '\\': escaped += "\\\\"; break;
        case '"': escaped += "\\\""; break;
        case '\n': escaped += "\\n"; break;
        case '\r': escaped += "\\r"; break;
        case '\t': escaped += "\\t"; break;
        default:
            escaped += static_cast<unsigned char>(ch) < 0x20 ? ' ' : ch;
            break;
        }
    }
    return escaped;
}

bool IsIbConnectionControlError(int errorCode) {
    return errorCode == 1100 || errorCode == 1101 ||
        errorCode == 1102 || errorCode == 2110;
}

bool IsIbFinalStatus(const std::string& status) {
    return status == "Filled" || status == "Cancelled" ||
        status == "ApiCancelled" || status == "Inactive" ||
        status == "Rejected";
}

bool HasEconomicFillEvidence(const IBEvent& event) {
    return std::isfinite(event.number2) && event.number2 > 0.0 &&
        std::isfinite(event.number) && event.number > 0.0;
}

bool IsHistoricalSyntheticExecutionStatus(const IBEvent& event) {
    return event.type == IBEventType::OrderStatus &&
        event.value == "execDetails" && event.requestId >= 0;
}

bool ParseBrokerErrorCode(const std::string& value, int& code) {
    if (value.empty()) return false;
    char* end = nullptr;
    errno = 0;
    const long parsed = std::strtol(value.c_str(), &end, 10);
    if (errno == ERANGE || end == value.c_str() || end == nullptr ||
        *end != '\0' || parsed < INT_MIN || parsed > INT_MAX)
        return false;
    code = static_cast<int>(parsed);
    return true;
}
}  // namespace

void HeptaIBGatewayAdapter::InvalidateCorrelationSnapshot(const std::string& reason) {
    m_correlationRefreshPending = false;
    m_correlationRefreshConflict = false;
    m_pendingCorrelationOrderIds.clear();
    m_pendingActiveOrderIds.clear();
    m_correlationSnapshot.connectionEpoch = m_connectionEpoch;
    m_correlationSnapshot.generation = m_correlationGeneration;
    m_correlationSnapshot.complete = false;
    m_correlationSnapshot.reasonCode = reason;
    m_correlationSnapshot.activeOrderIdsByCorrelation.clear();
    m_correlationSnapshot.activeOrderIds.clear();
}

IBAuthoritativeCorrelationSnapshot HeptaIBGatewayAdapter::GetAuthoritativeCorrelationSnapshot() const {
    std::lock_guard<std::recursive_mutex> lk(m_apiMutex);
    return m_correlationSnapshot;
}

bool HeptaIBGatewayAdapter::MergeIncrementalActiveOrder(
    long orderId, const std::string& correlationId) {
    if (!m_correlationSnapshot.complete) return true;
    if (orderId < 0) {
        InvalidateCorrelationSnapshot("IB_ACTIVE_ORDER_ID_INVALID");
        return false;
    }
    if (!correlationId.empty()) {
        const std::map<std::string, long>::const_iterator byCorrelation =
            m_correlationSnapshot.activeOrderIdsByCorrelation.find(
                correlationId);
        if (byCorrelation !=
                m_correlationSnapshot.activeOrderIdsByCorrelation.end() &&
            byCorrelation->second != orderId) {
            InvalidateCorrelationSnapshot(
                "IB_CORRELATION_INCREMENTAL_CONFLICT");
            return false;
        }
        for (std::map<std::string, long>::const_iterator it =
                 m_correlationSnapshot.activeOrderIdsByCorrelation.begin();
             it != m_correlationSnapshot.activeOrderIdsByCorrelation.end();
             ++it) {
            if (it->second == orderId && it->first != correlationId) {
                InvalidateCorrelationSnapshot(
                    "IB_CORRELATION_INCREMENTAL_CONFLICT");
                return false;
            }
        }
        m_correlationSnapshot.activeOrderIdsByCorrelation[correlationId] =
            orderId;
    }
    m_correlationSnapshot.activeOrderIds.insert(orderId);
    return true;
}

void HeptaIBGatewayAdapter::InvalidateTerminalCorrelationSnapshot(
    const std::string& reason) {
    m_completedOrdersRefreshPending = false;
    m_executionsRefreshPending = false;
    m_terminalCorrelationRefreshConflict = false;
    m_pendingTerminalOrderIds.clear();
    m_pendingTerminalStatuses.clear();
    m_pendingTerminalCorrelationsByOrderId.clear();
    m_pendingExecutionOrderIds.clear();
    m_terminalCorrelationSnapshot.connectionEpoch = m_connectionEpoch;
    m_terminalCorrelationSnapshot.generation =
        m_terminalCorrelationGeneration;
    m_terminalCorrelationSnapshot.complete = false;
    m_terminalCorrelationSnapshot.exposureGeneration = 0;
    m_terminalCorrelationSnapshot.reasonCode = reason;
    m_terminalCorrelationSnapshot.terminalOrderIdsByCorrelation.clear();
    m_terminalCorrelationSnapshot.terminalStatusesByCorrelation.clear();
    m_terminalCorrelationSnapshot.executionOrderIds.clear();
}

void HeptaIBGatewayAdapter::FinalizeTerminalCorrelationSnapshot() {
    if (m_completedOrdersRefreshPending || m_executionsRefreshPending) return;
    m_terminalCorrelationSnapshot.connectionEpoch = m_connectionEpoch;
    m_terminalCorrelationSnapshot.generation =
        m_terminalCorrelationGeneration;
    if (m_terminalCorrelationRefreshConflict || !m_eventStreamAuthoritative) {
        m_terminalCorrelationSnapshot.complete = false;
        if (m_terminalCorrelationSnapshot.reasonCode.empty()) {
            m_terminalCorrelationSnapshot.reasonCode =
                "IB_TERMINAL_CORRELATION_REFRESH_NOT_AUTHORITATIVE";
        }
        m_terminalCorrelationSnapshot.terminalOrderIdsByCorrelation.clear();
        m_terminalCorrelationSnapshot.terminalStatusesByCorrelation.clear();
        m_terminalCorrelationSnapshot.executionOrderIds.clear();
        return;
    }
    for (std::map<std::string, std::string>::iterator status =
             m_pendingTerminalStatuses.begin();
         status != m_pendingTerminalStatuses.end();) {
        const std::map<std::string, long>::const_iterator order =
            m_pendingTerminalOrderIds.find(status->first);
        if (status->second == "Filled" &&
            (order == m_pendingTerminalOrderIds.end() ||
             m_pendingExecutionOrderIds.find(order->second) ==
                 m_pendingExecutionOrderIds.end())) {
            if (order != m_pendingTerminalOrderIds.end()) {
                m_pendingTerminalCorrelationsByOrderId.erase(order->second);
                m_pendingTerminalOrderIds.erase(order);
            }
            status = m_pendingTerminalStatuses.erase(status);
            continue;
        }
        ++status;
    }
    m_terminalCorrelationSnapshot.complete = true;
    m_terminalCorrelationSnapshot.exposureGeneration =
        m_exposureGeneration;
    m_terminalCorrelationSnapshot.reasonCode.clear();
    m_terminalCorrelationSnapshot.terminalOrderIdsByCorrelation =
        m_pendingTerminalOrderIds;
    m_terminalCorrelationSnapshot.terminalStatusesByCorrelation =
        m_pendingTerminalStatuses;
    m_terminalCorrelationSnapshot.executionOrderIds =
        m_pendingExecutionOrderIds;
    m_pendingTerminalOrderIds.clear();
    m_pendingTerminalStatuses.clear();
    m_pendingTerminalCorrelationsByOrderId.clear();
    m_pendingExecutionOrderIds.clear();
}

IBAuthoritativeTerminalCorrelationSnapshot
HeptaIBGatewayAdapter::GetAuthoritativeTerminalCorrelationSnapshot() const {
    std::lock_guard<std::recursive_mutex> lk(m_apiMutex);
    return m_terminalCorrelationSnapshot;
}

void HeptaIBGatewayAdapter::InvalidateRiskSnapshot(const std::string& reason) {
    m_coherentRiskRefreshPending = false;
    m_coherentRiskRefreshForRecoveryAudit = false;
    m_accountRefreshPending = false;
    m_positionsRefreshPending = false;
    m_accountRefreshObserved = false;
    m_accountReadyObserved = false;
    m_accountReady = false;
    m_fxCashRefreshConflict = false;
    m_positionsRefreshConflict = false;
    m_pendingPositionQuantities.clear(); m_pendingPositionContracts.clear();
    m_authoritativePositionQuantities.clear(); m_authoritativePositionContracts.clear();
    m_pendingFxCashBalances.clear();
    m_authoritativeFxCashBalances.clear();
    m_authoritativeFxPositionQuantities.clear();
    m_authoritativeFxCashExposures.clear();
    m_riskSnapshot.connectionEpoch = m_connectionEpoch;
    m_riskSnapshot.generation = m_riskGeneration;
    m_riskSnapshot.complete = false;
    m_riskSnapshot.coherentRefreshComplete = false;
    m_riskSnapshot.accountComplete = false;
    m_riskSnapshot.positionsComplete = false;
    m_riskSnapshot.fxCashComplete = m_fxInstrumentByBaseCurrency.empty();
    m_riskSnapshot.fxCashGeneration = 0;
    m_riskSnapshot.riskAbsorbedExposureGeneration = 0;
    m_riskSnapshot.grossAbsolutePosition = 0.0;
    m_riskSnapshot.reasonCode = reason;
    InvalidateRecoveryAuditBarrier(reason);
}

void HeptaIBGatewayAdapter::InvalidateRecoveryAuditBarrier(
    const std::string& reason) {
    m_recoveryAuditBarrierComplete = false;
    m_recoveryAuditBarrierConnectionEpoch = 0;
    m_recoveryAuditBarrierMutationGeneration = 0;
    m_recoveryAuditBarrierReason = reason.empty() ?
        "IB_RECOVERY_AUDIT_BARRIER_NOT_COMPLETE" : reason;
}

void HeptaIBGatewayAdapter::RefreshGrossAbsolutePosition() {
    double gross = 0.0;
    for (const auto& entry : m_authoritativePositionQuantities) {
        gross += std::fabs(entry.second);
    }
    for (const auto& entry : m_authoritativeFxPositionQuantities) {
        gross += std::fabs(entry.second);
    }
    m_riskSnapshot.grossAbsolutePosition = gross;
}

void HeptaIBGatewayAdapter::RefreshAuthoritativeFxCashPositions() {
    m_authoritativeFxPositionQuantities.clear();
    m_authoritativeFxCashExposures.clear();
    for (std::map<std::string, std::string>::const_iterator it =
             m_fxInstrumentByBaseCurrency.begin();
         it != m_fxInstrumentByBaseCurrency.end(); ++it) {
        const std::map<std::string, double>::const_iterator balance =
            m_authoritativeFxCashBalances.find(it->first);
        if (balance == m_authoritativeFxCashBalances.end() ||
            !std::isfinite(balance->second)) continue;
        const std::map<std::string, double>::const_iterator baseline =
            m_cfg.authoritativeCashFxBaselines.find(it->second);
        const std::map<std::string, InstrumentRef>::const_iterator contract =
            m_cfg.authoritativeCashFxContracts.find(it->second);
        if (baseline == m_cfg.authoritativeCashFxBaselines.end() ||
            contract == m_cfg.authoritativeCashFxContracts.end() ||
            !std::isfinite(baseline->second)) continue;
        const double owned = balance->second - baseline->second;
        if (!std::isfinite(owned)) continue;
        IBAuthoritativeFxCashExposure exposure;
        exposure.instrument = it->second;
        exposure.baseCurrency = it->first;
        exposure.quoteCurrency = contract->second.currency;
        exposure.baselineCashBalance = baseline->second;
        exposure.currentCashBalance = balance->second;
        exposure.campaignOwnedQuantity = owned;
        m_authoritativeFxCashExposures[it->second] = exposure;
        if (std::fabs(owned) > 1e-6)
            m_authoritativeFxPositionQuantities[it->second] = owned;
    }
    RefreshGrossAbsolutePosition();
}

IBAuthoritativeRiskSnapshot HeptaIBGatewayAdapter::GetAuthoritativeRiskSnapshot() const {
    std::lock_guard<std::recursive_mutex> lk(m_apiMutex);
    return m_riskSnapshot;
}

std::map<std::string, double>
HeptaIBGatewayAdapter::GetAuthoritativePositionQuantities() const {
    std::lock_guard<std::recursive_mutex> lk(m_apiMutex);
    std::map<std::string, double> combined =
        m_authoritativePositionQuantities;
    for (std::map<std::string, double>::const_iterator it =
             m_authoritativeFxPositionQuantities.begin();
         it != m_authoritativeFxPositionQuantities.end(); ++it)
        combined[it->first] = it->second;
    return combined;
}

std::map<std::string, double>
HeptaIBGatewayAdapter::GetAuthoritativeFxCashPositionQuantities() const {
    std::lock_guard<std::recursive_mutex> lk(m_apiMutex);
    return m_authoritativeFxPositionQuantities;
}

std::map<std::string, IBAuthoritativeFxCashExposure>
HeptaIBGatewayAdapter::GetAuthoritativeFxCashExposures() const {
    std::lock_guard<std::recursive_mutex> lk(m_apiMutex);
    return m_authoritativeFxCashExposures;
}

bool HeptaIBGatewayAdapter::IsConnected() const {
    std::lock_guard<std::recursive_mutex> lk(m_apiMutex);
    return m_api ? m_api->IsConnected() : false;
}

std::uint64_t HeptaIBGatewayAdapter::GetConnectionEpoch() const {
    std::lock_guard<std::recursive_mutex> lk(m_apiMutex);
    return m_connectionEpoch;
}

bool HeptaIBGatewayAdapter::GetBrokerConnectionIdentity(
    IBBrokerConnectionIdentity& identity, std::string& reason) const {
    std::lock_guard<std::recursive_mutex> lock(m_apiMutex);
    if (!m_api || !m_connected || !m_api->IsConnected()) {
        identity = IBBrokerConnectionIdentity();
        reason = "IB_BROKER_SOCKET_IDENTITY_NOT_CONNECTED";
        return false;
    }
    if (!m_api->GetBrokerConnectionIdentity(identity, reason) ||
        identity.connectionEpoch == 0 || identity.canonical.empty() ||
        identity.connectionEpoch != m_connectionEpoch) {
        identity = IBBrokerConnectionIdentity();
        if (reason.empty()) reason = "IB_BROKER_SOCKET_IDENTITY_MISMATCH";
        return false;
    }
    reason.clear();
    return true;
}

bool HeptaIBGatewayAdapter::PollOnce(int timeoutMs) {
    std::lock_guard<std::recursive_mutex> lk(m_apiMutex);
    if (!m_api) return false;
    return m_api->PollOnce(timeoutMs);
}

bool HeptaIBGatewayAdapter::DequeueCurrentEpochEvent(IBEvent& event) {
    do {
        if (!m_api->TryDequeueEvent(event)) return false;
        if (event.connectionEpoch != 0 && event.connectionEpoch != m_connectionEpoch) {
            EmitObsEvent("event.stale_connection_epoch",
                "\"eventEpoch\":" + std::to_string(event.connectionEpoch)
                    + ",\"activeEpoch\":" + std::to_string(m_connectionEpoch));
            continue;
        }
        return true;
    } while (true);
}

bool HeptaIBGatewayAdapter::CorrelateOpenOrderEvent(const IBEvent& event) {
    bool acceptedBrokerOpenOrder = false;
    // An account-wide OpenOrder callback is broker acknowledgement after a
    // process restart. Restore cancelability from that authoritative evidence
    // rather than relying on process-local submit history.
    if (event.type == IBEventType::OpenOrder &&
        !m_cfg.account.empty() && event.account == m_cfg.account) {
        if (event.id >= 0) {
            acceptedBrokerOpenOrder =
                m_orderLifecycle.RecordBrokerOpenOrder(
                static_cast<long>(event.id), event.value);
            if (acceptedBrokerOpenOrder)
                DispatchPendingCancelIfAcknowledged(
                    static_cast<long>(event.id), event.value,
                    event.value != "Filled", true);
        }
        if (acceptedBrokerOpenOrder &&
            !m_correlationRefreshPending && m_correlationSnapshot.complete) {
            std::string correlationId;
            std::string decodeReason;
            if (DecodeVenueOrderRef(
                    event.order.orderRef, correlationId, decodeReason)) {
                MergeIncrementalActiveOrder(
                    static_cast<long>(event.id), correlationId);
            } else if (event.order.orderRef.compare(0, 2, "H1") == 0) {
                InvalidateCorrelationSnapshot(decodeReason);
            } else {
                MergeIncrementalActiveOrder(
                    static_cast<long>(event.id), std::string());
            }
        }
    }
    return acceptedBrokerOpenOrder;
}

void HeptaIBGatewayAdapter::RetireTerminalActiveOrder(
    long orderId, bool eraseRiskBaseline) {
    if (m_postFillReconciliationOrderIds.count(orderId) != 0 ||
        !m_correlationSnapshot.complete)
        return;
    if (eraseRiskBaseline) m_orderRiskBaselines.erase(orderId);
    m_correlationSnapshot.activeOrderIds.erase(orderId);
    for (std::map<std::string, long>::iterator it =
             m_correlationSnapshot.activeOrderIdsByCorrelation.begin();
         it != m_correlationSnapshot.activeOrderIdsByCorrelation.end();) {
        if (it->second == orderId)
            it = m_correlationSnapshot.activeOrderIdsByCorrelation.erase(it);
        else
            ++it;
    }
}

void HeptaIBGatewayAdapter::ApplyOrderStatusTransition(
    const IBEvent& event) {
    const long orderId = static_cast<long>(event.id);
    const std::chrono::steady_clock::time_point now =
        std::chrono::steady_clock::now();
    const bool economicFill = HasEconomicFillEvidence(event);
    const bool economicTerminal = event.key != "Filled" || economicFill;
    m_orderLifecycle.RecordBrokerStatus(
        orderId, event.key, economicTerminal);
    DispatchPendingCancelIfAcknowledged(
        orderId, event.key, economicTerminal);
    if (IsIbFinalStatus(event.key) && economicTerminal)
        RetireTerminalActiveOrder(orderId, true);

    std::unordered_map<
        long, std::chrono::steady_clock::time_point>::iterator submitted =
            m_orderSubmitTs.find(orderId);
    if (submitted != m_orderSubmitTs.end()) {
        const long elapsed =
            std::chrono::duration_cast<std::chrono::milliseconds>(
                now - submitted->second).count();
        EmitLatency(
            "order", "submit_to_first_status", elapsed, true,
            "\"orderId\":" + std::to_string(orderId) +
            ",\"status\":\"" + EscapeJson(event.key) + "\"");
        m_orderSubmitTs.erase(submitted);
    }

    std::unordered_map<
        long, std::chrono::steady_clock::time_point>::iterator cancelled =
            m_cancelSubmitTs.find(orderId);
    const bool cancelDone = event.key == "Cancelled" ||
        event.key == "ApiCancelled" || event.key == "Inactive";
    if (cancelled != m_cancelSubmitTs.end() && cancelDone) {
        const long elapsed =
            std::chrono::duration_cast<std::chrono::milliseconds>(
                now - cancelled->second).count();
        EmitLatency(
            "cancel", "submit_to_final_cancel", elapsed, true,
            "\"orderId\":" + std::to_string(orderId) +
            ",\"status\":\"" + EscapeJson(event.key) + "\"");
        m_cancelSubmitTs.erase(cancelled);
    }
}

void HeptaIBGatewayAdapter::ApplyExecutionDetailsTransition(
    const IBEvent& event) {
    (void)event;
}

bool HeptaIBGatewayAdapter::ObserveEconomicFill(const IBEvent& event) {
    if ((event.type != IBEventType::OrderStatus &&
         event.type != IBEventType::ExecutionDetails) ||
        event.id < 0 || !HasEconomicFillEvidence(event) ||
        (event.type == IBEventType::ExecutionDetails &&
         !m_cfg.account.empty() && event.account != m_cfg.account))
        return false;
    const long orderId = static_cast<long>(event.id);
    const std::map<long, double>::const_iterator prior =
        m_observedEconomicFillQuantityByOrderId.find(orderId);
    if (prior != m_observedEconomicFillQuantityByOrderId.end() &&
        event.number2 <= prior->second)
        return false;
    if (m_exposureGeneration ==
        std::numeric_limits<std::uint64_t>::max()) {
        m_eventStreamAuthoritative = false;
        m_lastRejectReason = "IB_EXPOSURE_GENERATION_EXHAUSTED";
        InvalidateCorrelationSnapshot(
            "IB_EXPOSURE_GENERATION_EXHAUSTED");
        InvalidateTerminalCorrelationSnapshot(
            "IB_EXPOSURE_GENERATION_EXHAUSTED");
        InvalidateRiskSnapshot("IB_EXPOSURE_GENERATION_EXHAUSTED");
        return false;
    }
    ++m_exposureGeneration;
    m_observedEconomicFillQuantityByOrderId[orderId] = event.number2;
    const bool historical =
        (event.type == IBEventType::ExecutionDetails &&
         event.requestId >= 0) ||
        IsHistoricalSyntheticExecutionStatus(event);
    if (!historical) {
        m_postFillReconciliationOrderIds.insert(orderId);
        m_postFillExposureGenerationByOrderId[orderId] =
            m_exposureGeneration;
    }
    InvalidateRecoveryAuditBarrier(
        historical ? "IB_RECOVERY_AUDIT_HISTORICAL_FILL_OBSERVED" :
            "IB_RECOVERY_AUDIT_LIVE_FILL_OBSERVED");
    return true;
}

void HeptaIBGatewayAdapter::ApplyBrokerErrorTransition(
    const IBEvent& event) {
    int errorCode = 0;
    if (!ParseBrokerErrorCode(event.key, errorCode)) return;
    if (!IsIbConnectionControlError(errorCode))
        NotifyErrorEvent(errorCode);
    if (event.id < 0 || (errorCode != 201 && errorCode != 202))
        return;
    const long orderId = static_cast<long>(event.id);
    const std::string status =
        errorCode == 201 ? "Rejected" : "Cancelled";
    m_orderLifecycle.RecordBrokerStatus(orderId, status, true);
    // Error 201/202 is terminal broker evidence too.  Clear any pre-ACK
    // cancel intent before retiring the order so a same-epoch order-id reuse
    // cannot replay the stale cancellation.
    DispatchPendingCancelIfAcknowledged(orderId, status, true);
    RetireTerminalActiveOrder(orderId, false);
}

void HeptaIBGatewayAdapter::ApplyEventQueueOverflow(
    const IBEvent& event) {
    m_eventStreamAuthoritative = false;
    if (event.overflowGeneration > m_lastEventOverflowGeneration)
        m_lastEventOverflowGeneration = event.overflowGeneration;
    m_lastRejectReason = "RISK_IB_EVENT_STREAM_NOT_AUTHORITATIVE";
    InvalidateCorrelationSnapshot("IB_CORRELATION_EVENT_STREAM_OVERFLOW");
    InvalidateTerminalCorrelationSnapshot(
        "IB_TERMINAL_CORRELATION_EVENT_STREAM_OVERFLOW");
    InvalidateRiskSnapshot("IB_RISK_EVENT_STREAM_OVERFLOW");
    EmitObsEvent(
        "event_queue.overflow_fail_closed",
        "\"overflow_generation\":" +
            std::to_string(event.overflowGeneration) +
        ",\"dropped_event_count\":" +
            std::to_string(event.droppedEventCount) +
        ",\"requires_full_resync\":true");
}

void HeptaIBGatewayAdapter::ApplyEventStateTransition(
    const IBEvent& event) {
    if (m_recoveryAuditBarrierComplete &&
        (event.type == IBEventType::OpenOrder ||
         event.type == IBEventType::OrderStatus ||
         event.type == IBEventType::ExecutionDetails ||
         event.type == IBEventType::CompletedOrder ||
         (event.type == IBEventType::Error && event.id >= 0)))
        InvalidateRecoveryAuditBarrier(
            "IB_RECOVERY_AUDIT_LATE_BROKER_EVENT");
    ObserveEconomicFill(event);
    switch (event.type) {
    case IBEventType::Connected:
        m_connected = true;
        EmitObsEvent(
            "connect.connected_event",
            "\"id\":" + std::to_string(event.id));
        break;
    case IBEventType::ConnectionClosed:
        m_connected = false;
        // Do not carry a pre-ack cancel intent over a broken broker
        // connection.  The order id can be reassigned in the next epoch;
        // recovery must rebuild any cancel from authoritative evidence.
        m_pendingCancelOrderIds.clear();
        m_orderLifecycle.InvalidateConnectionEpoch();
        InvalidateCorrelationSnapshot(
            "IB_CORRELATION_CONNECTION_CLOSED");
        InvalidateTerminalCorrelationSnapshot(
            "IB_TERMINAL_CORRELATION_CONNECTION_CLOSED");
        InvalidateRiskSnapshot("IB_RISK_CONNECTION_CLOSED");
        EmitObsEvent(
            "connect.closed",
            "\"status\":\"" + EscapeJson(event.value) + "\"");
        break;
    case IBEventType::NextValidId:
        m_connected = true;
        EmitObsEvent(
            "connect.next_valid_id",
            "\"nextValidId\":" + std::to_string(event.id));
        break;
    case IBEventType::OrderStatus:
        ApplyOrderStatusTransition(event);
        break;
    case IBEventType::ExecutionDetails:
        ApplyExecutionDetailsTransition(event);
        break;
    case IBEventType::Error:
        ApplyBrokerErrorTransition(event);
        break;
    case IBEventType::EventQueueOverflow:
        ApplyEventQueueOverflow(event);
        break;
    default:
        break;
    }
}
bool HeptaIBGatewayAdapter::TryDequeueEvent(IBEvent& outEvent) {
    std::lock_guard<std::recursive_mutex> lk(m_apiMutex);
    if (!m_api || !DequeueCurrentEpochEvent(outEvent)) return false;
    const bool acceptedBrokerOpenOrder = CorrelateOpenOrderEvent(outEvent);

    if (outEvent.type == IBEventType::OrderStatus) {
        EmitObsEvent("callback.order_status",
            "\"order_id\":" + std::to_string(outEvent.id) +
            ",\"status\":\"" + EscapeJson(outEvent.key) + "\"" +
            ",\"filled_quantity\":" + std::to_string(outEvent.number2) +
            ",\"remaining_quantity\":" + std::to_string(outEvent.number3) +
            ",\"average_fill_price\":" + std::to_string(outEvent.number) +
            ",\"why_held\":\"" + EscapeJson(outEvent.whyHeld) + "\"" +
            ",\"market_cap_price\":" +
                std::to_string(outEvent.marketCapPrice));
    } else if (outEvent.type == IBEventType::Error) {
        EmitObsEvent("callback.error",
            "\"callback_id\":" + std::to_string(outEvent.id) +
            ",\"error_code\":\"" + EscapeJson(outEvent.key) + "\"" +
            ",\"message\":\"" + EscapeJson(outEvent.value) + "\"" +
            ",\"advanced_order_reject_json\":\"" +
                EscapeJson(outEvent.advancedOrderRejectJson) + "\"");
    } else if (outEvent.type == IBEventType::ExecutionDetails) {
        EmitObsEvent("callback.execution_details",
            "\"request_id\":" + std::to_string(outEvent.requestId) +
            ",\"order_id\":" + std::to_string(outEvent.id) +
            ",\"execution_id\":\"" + EscapeJson(outEvent.key) + "\"" +
            ",\"side\":\"" + EscapeJson(outEvent.value) + "\"" +
            ",\"cumulative_quantity\":" + std::to_string(outEvent.number2) +
            ",\"remaining_quantity\":" + std::to_string(outEvent.number3) +
            ",\"average_fill_price\":" + std::to_string(outEvent.number));
    } else if (outEvent.type == IBEventType::CompletedOrder) {
        EmitObsEvent("callback.completed_order",
            "\"order_id\":" + std::to_string(outEvent.id) +
            ",\"status\":\"" + EscapeJson(outEvent.key) + "\"" +
            ",\"quantity\":" + std::to_string(outEvent.number) +
            ",\"limit_price\":" + std::to_string(outEvent.number2));
    } else if (outEvent.type == IBEventType::CompletedOrdersEnd) {
        EmitObsEvent("callback.completed_orders_end");
    } else if (outEvent.type == IBEventType::ExecutionDetailsEnd) {
        EmitObsEvent("callback.execution_details_end",
            "\"request_id\":" + std::to_string(outEvent.requestId));
    }

    ApplyEventStateTransition(outEvent);
    ApplyRiskSnapshotEvent(outEvent);
    ApplyActiveCorrelationEvent(outEvent, acceptedBrokerOpenOrder);
    ApplyTerminalCorrelationEvent(outEvent);
    PublishPositionEvent(outEvent);
    return true;
}

bool HeptaIBGatewayAdapter::ConsumeFxCashAccountValue(
    const IBEvent& event, bool initialSnapshot) {
    if (event.type != IBEventType::AccountValue ||
        event.account != m_cfg.account || m_fxInstrumentByBaseCurrency.empty())
        return false;
    if (event.key.compare(0, 13, "AccountReady:") == 0) {
        std::string ready = event.value;
        std::transform(ready.begin(), ready.end(), ready.begin(),
            [](unsigned char ch) {
                return static_cast<char>(std::tolower(ch));
            });
        if (ready != "true" && ready != "false") {
            if (initialSnapshot) m_fxCashRefreshConflict = true;
            else InvalidateRiskSnapshot("IB_ACCOUNT_READY_VALUE_INVALID");
            return true;
        }
        if (initialSnapshot) {
            if (m_accountReadyObserved && m_accountReady !=
                    (ready == "true"))
                m_fxCashRefreshConflict = true;
            m_accountReadyObserved = true;
            m_accountReady = ready == "true";
        } else if (ready == "false") {
            InvalidateRiskSnapshot("IB_ACCOUNT_NOT_READY");
        }
        return true;
    }
    static const std::string prefix("CashBalance:");
    if (event.key.compare(0, prefix.size(), prefix) != 0) return false;
    const std::string currency = event.key.substr(prefix.size());
    if (m_fxInstrumentByBaseCurrency.find(currency) ==
        m_fxInstrumentByBaseCurrency.end()) return true;
    char* end = nullptr;
    errno = 0;
    const double parsed = std::strtod(event.value.c_str(), &end);
    if (errno == ERANGE || end == event.value.c_str() || end == nullptr ||
        *end != '\0' || !std::isfinite(parsed)) {
        if (initialSnapshot) m_fxCashRefreshConflict = true;
        else InvalidateRiskSnapshot("IB_FX_CASH_BALANCE_INVALID");
        return true;
    }
    if (initialSnapshot) {
        const std::map<std::string, double>::const_iterator existing =
            m_pendingFxCashBalances.find(currency);
        if (existing != m_pendingFxCashBalances.end() &&
            existing->second != parsed)
            m_fxCashRefreshConflict = true;
        else
            m_pendingFxCashBalances[currency] = parsed;
        return true;
    }
    // Subscription pushes outside a declared request/end generation are not a
    // snapshot boundary and can be delayed by IB. Never let them directly
    // mutate the authoritative campaign exposure; however, a changed value is
    // proof that the committed snapshot is stale, so invalidate fail-closed.
    const std::map<std::string, double>::const_iterator committed =
        m_authoritativeFxCashBalances.find(currency);
    if (committed == m_authoritativeFxCashBalances.end() ||
        committed->second != parsed)
        InvalidateRiskSnapshot(
            "IB_FX_CASH_BALANCE_OUT_OF_GENERATION_CHANGE");
    return true;
}

void HeptaIBGatewayAdapter::ApplyAccountValueRiskEvent(
    const IBEvent& event) {
    if (event.account != m_cfg.account) return;
    if (m_accountRefreshPending) {
        if (!ConsumeFxCashAccountValue(event, true))
            m_accountRefreshObserved = true;
        return;
    }
    ConsumeFxCashAccountValue(event, false);
}

bool HeptaIBGatewayAdapter::HasCompletePendingFxCashSnapshot() const {
    if (m_fxInstrumentByBaseCurrency.empty()) return true;
    if (m_fxCashRefreshConflict ||
        (m_accountReadyObserved && !m_accountReady))
        return false;
    for (std::map<std::string, std::string>::const_iterator it =
             m_fxInstrumentByBaseCurrency.begin();
         it != m_fxInstrumentByBaseCurrency.end(); ++it) {
        if (m_pendingFxCashBalances.find(it->first) ==
            m_pendingFxCashBalances.end())
            return false;
    }
    return true;
}

bool HeptaIBGatewayAdapter::InitialFxCashAttestationMatches() const {
    if (!m_fxCashInitialAttestationPending) return true;
    for (std::map<std::string, InstrumentRef>::const_iterator it =
             m_cfg.authoritativeCashFxContracts.begin();
         it != m_cfg.authoritativeCashFxContracts.end(); ++it) {
        const std::map<std::string, double>::const_iterator expected =
            m_cfg.authoritativeCashFxStartupObservedBalances.find(it->first);
        const std::map<std::string, double>::const_iterator observed =
            m_pendingFxCashBalances.find(it->second.symbol);
        if (expected ==
                m_cfg.authoritativeCashFxStartupObservedBalances.end() ||
            observed == m_pendingFxCashBalances.end() ||
            std::fabs(expected->second - observed->second) > 1e-6)
            return false;
    }
    return true;
}

void HeptaIBGatewayAdapter::CommitAccountRiskRefresh() {
    m_riskSnapshot.accountComplete = true;
    m_riskSnapshot.fxCashComplete = true;
    m_riskSnapshot.fxCashGeneration = m_riskSnapshot.accountGeneration;
    m_authoritativeFxCashBalances = m_pendingFxCashBalances;
    RefreshAuthoritativeFxCashPositions();
    m_fxCashInitialAttestationPending = false;
    m_riskSnapshot.reasonCode = m_riskSnapshot.positionsComplete ?
        std::string() : "IB_POSITIONS_NOT_COMPLETE";
}

void HeptaIBGatewayAdapter::RejectAccountRiskRefresh(
    bool initialAttestationMatches) {
    m_riskSnapshot.accountComplete = false;
    m_riskSnapshot.fxCashComplete = m_fxInstrumentByBaseCurrency.empty();
    m_authoritativeFxCashBalances.clear();
    m_authoritativeFxPositionQuantities.clear();
    m_authoritativeFxCashExposures.clear();
    RefreshGrossAbsolutePosition();
    if (!m_eventStreamAuthoritative)
        m_riskSnapshot.reasonCode = "IB_RISK_REFRESH_NOT_AUTHORITATIVE";
    else if (!m_accountRefreshObserved)
        m_riskSnapshot.reasonCode = "IB_ACCOUNT_SUMMARY_EMPTY";
    else if (m_fxCashRefreshConflict)
        m_riskSnapshot.reasonCode = "IB_FX_CASH_BALANCE_INVALID";
    else if (m_accountReadyObserved && !m_accountReady)
        m_riskSnapshot.reasonCode = "IB_ACCOUNT_NOT_READY";
    else if (!initialAttestationMatches)
        m_riskSnapshot.reasonCode =
            "IB_FX_CASH_ATTESTED_BALANCE_MISMATCH";
    else
        m_riskSnapshot.reasonCode = "IB_FX_CASH_BALANCE_MISSING";
}

void HeptaIBGatewayAdapter::CompleteAccountRiskRefresh() {
    m_accountRefreshPending = false;
    m_riskSnapshot.connectionEpoch = m_connectionEpoch;
    const bool fxComplete = HasCompletePendingFxCashSnapshot();
    const bool initialAttestationMatches =
        !fxComplete || InitialFxCashAttestationMatches();
    if (m_eventStreamAuthoritative && m_accountRefreshObserved &&
        fxComplete && initialAttestationMatches)
        CommitAccountRiskRefresh();
    else
        RejectAccountRiskRefresh(initialAttestationMatches);

    m_pendingFxCashBalances.clear();
    m_accountReadyObserved = false;
    m_accountReady = false;
    m_fxCashRefreshConflict = false;
    EmitObsEvent(
        "risk.account_refresh_complete",
        "\"account_generation\":" +
            std::to_string(m_riskSnapshot.accountGeneration) +
        ",\"account_complete\":" +
            (m_riskSnapshot.accountComplete ? "true" : "false") +
        ",\"fx_cash_complete\":" +
            (m_riskSnapshot.fxCashComplete ? "true" : "false") +
        ",\"reason_code\":\"" +
            EscapeJson(m_riskSnapshot.reasonCode) + "\"");
    CompleteCoherentRiskRefreshIfReady();
}

void HeptaIBGatewayAdapter::ApplyPositionSnapshotItem(
    const IBEvent& event) {
    if (m_cfg.account.empty() || event.account != m_cfg.account) return;
    if (!std::isfinite(event.number)) {
        m_positionsRefreshConflict = true;
        m_riskSnapshot.reasonCode = "IB_POSITION_VALUE_INVALID";
        return;
    }
    const std::pair<std::map<std::string, double>::iterator, bool> inserted =
        m_pendingPositionQuantities.insert(
            std::make_pair(event.key, event.number));
    const bool contractInserted = m_pendingPositionContracts.insert(
        std::make_pair(event.key, event.contract)).second;
    if (!inserted.second || !contractInserted) {
        m_positionsRefreshConflict = true;
        m_riskSnapshot.reasonCode = "IB_POSITION_IDENTITY_CONFLICT";
    }
}

void HeptaIBGatewayAdapter::ApplyPositionMonitorUpdate(
    const IBEvent& event) {
    if (event.account != m_cfg.account ||
        !m_riskSnapshot.positionsComplete)
        return;
    if (!std::isfinite(event.number)) {
        InvalidateRiskSnapshot("IB_POSITION_MONITOR_VALUE_INVALID");
        return;
    }
    const std::map<std::string, double>::const_iterator committed =
        m_authoritativePositionQuantities.find(event.key);
    const bool changed =
        committed == m_authoritativePositionQuantities.end() ?
            event.number != 0.0 : committed->second != event.number;
    const std::map<std::string, InstrumentRef>::const_iterator contract =
        m_authoritativePositionContracts.find(event.key);
    const bool contractChanged =
        committed != m_authoritativePositionQuantities.end() &&
        (contract == m_authoritativePositionContracts.end() ||
         contract->second.symbol != event.contract.symbol ||
         contract->second.secType != event.contract.secType ||
         contract->second.currency != event.contract.currency);
    if (changed || contractChanged)
        InvalidateRiskSnapshot("IB_POSITION_OUT_OF_GENERATION_CHANGE");
}

void HeptaIBGatewayAdapter::CompletePositionRiskRefresh() {
    m_positionsRefreshPending = false;
    m_riskSnapshot.connectionEpoch = m_connectionEpoch;
    if (m_eventStreamAuthoritative && !m_positionsRefreshConflict) {
        m_authoritativePositionQuantities = m_pendingPositionQuantities;
        m_authoritativePositionContracts = m_pendingPositionContracts;
        m_riskSnapshot.positionsComplete = true;
        RefreshGrossAbsolutePosition();
        m_riskSnapshot.reasonCode =
            m_riskSnapshot.accountComplete && m_riskSnapshot.fxCashComplete ?
                std::string() :
                (m_riskSnapshot.reasonCode.empty() ?
                    "IB_ACCOUNT_SUMMARY_NOT_COMPLETE" :
                    m_riskSnapshot.reasonCode);
    } else {
        m_authoritativePositionQuantities.clear();
        m_authoritativePositionContracts.clear();
        m_riskSnapshot.positionsComplete = false;
        m_riskSnapshot.grossAbsolutePosition = 0.0;
        if (m_riskSnapshot.reasonCode.empty())
            m_riskSnapshot.reasonCode =
                "IB_RISK_REFRESH_NOT_AUTHORITATIVE";
    }
    m_pendingPositionQuantities.clear();
    m_pendingPositionContracts.clear();
    m_positionsRefreshConflict = false;
    CompleteCoherentRiskRefreshIfReady();
}

void HeptaIBGatewayAdapter::CompleteCoherentRiskRefreshIfReady() {
    if (!m_coherentRiskRefreshPending || m_accountRefreshPending ||
        m_positionsRefreshPending)
        return;
    const bool exactGeneration =
        m_coherentRiskRefreshConnectionEpoch == m_connectionEpoch &&
        m_riskSnapshot.connectionEpoch == m_connectionEpoch &&
        m_riskSnapshot.accountGeneration ==
            m_coherentRiskRefreshAccountGeneration &&
        m_riskSnapshot.positionsGeneration ==
            m_coherentRiskRefreshPositionGeneration;
    const bool complete = exactGeneration && m_eventStreamAuthoritative &&
        m_riskSnapshot.accountComplete &&
        m_riskSnapshot.positionsComplete &&
        m_riskSnapshot.fxCashComplete &&
        m_riskSnapshot.accountGeneration != 0 &&
        m_riskSnapshot.positionsGeneration != 0 &&
        m_riskSnapshot.fxCashGeneration != 0;
    m_riskSnapshot.complete = complete;
    m_riskSnapshot.coherentRefreshComplete = complete;
    if (complete) {
        m_riskSnapshot.riskAbsorbedExposureGeneration =
            m_coherentRiskRefreshExposureGeneration;
        if (m_exposureGeneration !=
                m_coherentRiskRefreshExposureGeneration)
            m_riskSnapshot.reasonCode =
                "IB_RISK_EXPOSURE_ADVANCED_DURING_REFRESH";
    }
    const bool recoveryBarrierComplete = complete &&
        m_coherentRiskRefreshForRecoveryAudit &&
        m_correlationSnapshot.complete &&
        m_terminalCorrelationSnapshot.complete &&
        m_correlationSnapshot.connectionEpoch == m_connectionEpoch &&
        m_terminalCorrelationSnapshot.connectionEpoch == m_connectionEpoch &&
        m_correlationSnapshot.generation ==
            m_coherentRiskRefreshActiveGeneration &&
        m_terminalCorrelationSnapshot.generation ==
            m_coherentRiskRefreshTerminalGeneration &&
        m_terminalCorrelationSnapshot.exposureGeneration <=
            m_coherentRiskRefreshExposureGeneration &&
        m_exposureGeneration == m_coherentRiskRefreshExposureGeneration &&
        m_brokerMutationGeneration ==
            m_coherentRiskRefreshMutationGeneration &&
        m_postFillReconciliationOrderIds.empty();
    if (recoveryBarrierComplete) {
        m_recoveryAuditBarrierComplete = true;
        m_recoveryAuditBarrierConnectionEpoch = m_connectionEpoch;
        m_recoveryAuditBarrierMutationGeneration =
            m_brokerMutationGeneration;
        m_recoveryAuditBarrierReason.clear();
    } else if (m_coherentRiskRefreshForRecoveryAudit) {
        InvalidateRecoveryAuditBarrier(complete ?
            "IB_RECOVERY_AUDIT_BARRIER_DRIFT" :
            "IB_RECOVERY_AUDIT_RISK_REFRESH_INCOMPLETE");
    }
    m_coherentRiskRefreshPending = false;
    m_coherentRiskRefreshForRecoveryAudit = false;
}

void HeptaIBGatewayAdapter::ApplyRiskSnapshotEvent(const IBEvent& event) {
    switch (event.type) {
    case IBEventType::AccountValue:
        ApplyAccountValueRiskEvent(event);
        break;
    case IBEventType::AccountSummaryEnd:
        if (m_accountRefreshPending) CompleteAccountRiskRefresh();
        break;
    case IBEventType::PositionSnapshotItem:
        if (m_positionsRefreshPending) ApplyPositionSnapshotItem(event);
        break;
    case IBEventType::PositionMonitorUpdate:
        ApplyPositionMonitorUpdate(event);
        break;
    case IBEventType::PositionEnd:
        if (m_positionsRefreshPending) CompletePositionRiskRefresh();
        break;
    default:
        break;
    }
}
void HeptaIBGatewayAdapter::ApplyActiveCorrelationEvent(
    const IBEvent& outEvent,
    bool acceptedBrokerOpenOrder) {
    if (outEvent.type == IBEventType::OpenOrder && m_correlationRefreshPending) {
        if (m_cfg.account.empty() || outEvent.account != m_cfg.account) return;
        if (outEvent.id < 0) {
            m_correlationRefreshConflict = true;
            m_correlationSnapshot.reasonCode = "IB_ACTIVE_ORDER_ID_INVALID";
            return;
        }
        if (!acceptedBrokerOpenOrder) return;
        m_pendingActiveOrderIds.insert(static_cast<long>(outEvent.id));
        std::string correlationId;
        std::string decodeReason;
        if (DecodeVenueOrderRef(
                outEvent.order.orderRef, correlationId, decodeReason)) {
            const long orderId = static_cast<long>(outEvent.id);
            const auto inserted = m_pendingCorrelationOrderIds.insert(
                std::make_pair(correlationId, orderId));
            if (!inserted.second && inserted.first->second != orderId) {
                m_correlationRefreshConflict = true;
                m_correlationSnapshot.reasonCode = "IB_CORRELATION_DUPLICATE_CONFLICT";
            }
        } else if (outEvent.order.orderRef.compare(0, 2, "H1") == 0) {
            m_correlationRefreshConflict = true;
            m_correlationSnapshot.reasonCode = decodeReason;
        }
    } else if (outEvent.type == IBEventType::OpenOrderEnd && m_correlationRefreshPending) {
        m_correlationRefreshPending = false;
        m_correlationSnapshot.connectionEpoch = m_connectionEpoch;
        m_correlationSnapshot.generation = m_correlationGeneration;
        if (!m_correlationRefreshConflict && m_eventStreamAuthoritative) {
            m_correlationSnapshot.complete = true;
            m_correlationSnapshot.reasonCode.clear();
            m_correlationSnapshot.activeOrderIdsByCorrelation = m_pendingCorrelationOrderIds;
            m_correlationSnapshot.activeOrderIds = m_pendingActiveOrderIds;
        } else {
            m_correlationSnapshot.complete = false;
            if (m_correlationSnapshot.reasonCode.empty()) {
                m_correlationSnapshot.reasonCode =
                    "IB_CORRELATION_REFRESH_NOT_AUTHORITATIVE";
            }
            m_correlationSnapshot.activeOrderIdsByCorrelation.clear();
            m_correlationSnapshot.activeOrderIds.clear();
        }
        m_pendingCorrelationOrderIds.clear();
        m_pendingActiveOrderIds.clear();
    }
}

void HeptaIBGatewayAdapter::ApplyTerminalCorrelationEvent(const IBEvent& outEvent) {
    if (outEvent.type == IBEventType::CompletedOrder &&
               m_completedOrdersRefreshPending) {
        if (m_cfg.account.empty() || outEvent.account != m_cfg.account)
            return;
        std::string correlationId;
        std::string decodeReason;
        if (DecodeVenueOrderRef(
                outEvent.order.orderRef, correlationId, decodeReason)) {
            const long orderId = static_cast<long>(outEvent.id);
            if (orderId < 0 || !IsIbFinalStatus(outEvent.key)) {
                m_terminalCorrelationRefreshConflict = true;
                m_terminalCorrelationSnapshot.reasonCode = orderId < 0 ?
                    "IB_TERMINAL_ORDER_ID_INVALID" :
                    "IB_TERMINAL_ORDER_STATUS_NOT_FINAL";
            } else {
                const auto byCorrelation = m_pendingTerminalOrderIds.insert({correlationId, orderId});
                const std::pair<std::map<long, std::string>::iterator, bool>
                    byOrderId = orderId == 0 ?
                        std::make_pair(m_pendingTerminalCorrelationsByOrderId.end(), true) :
                        m_pendingTerminalCorrelationsByOrderId.insert({
                            orderId, correlationId});
                if ((!byCorrelation.second &&
                     byCorrelation.first->second != orderId) ||
                    (!byOrderId.second &&
                     byOrderId.first->second != correlationId)) {
                    m_terminalCorrelationRefreshConflict = true;
                    m_terminalCorrelationSnapshot.reasonCode =
                        "IB_TERMINAL_CORRELATION_DUPLICATE_CONFLICT";
                } else {
                    m_pendingTerminalStatuses[correlationId] = outEvent.key;
                }
            }
        } else if (outEvent.order.orderRef.compare(0, 2, "H1") == 0) {
            m_terminalCorrelationRefreshConflict = true;
            m_terminalCorrelationSnapshot.reasonCode = decodeReason;
        }
    } else if (outEvent.type == IBEventType::CompletedOrdersEnd &&
               m_completedOrdersRefreshPending) {
        m_completedOrdersRefreshPending = false;
        FinalizeTerminalCorrelationSnapshot();
    } else if (outEvent.type == IBEventType::ExecutionDetails &&
               m_executionsRefreshPending) {
        if (outEvent.requestId == m_terminalExecutionRequestId &&
            !m_cfg.account.empty() && outEvent.account == m_cfg.account &&
            outEvent.id > 0 && HasEconomicFillEvidence(outEvent))
            m_pendingExecutionOrderIds.insert(static_cast<long>(outEvent.id));
    } else if (outEvent.type == IBEventType::ExecutionDetailsEnd &&
               m_executionsRefreshPending &&
               outEvent.requestId == m_terminalExecutionRequestId) {
        m_executionsRefreshPending = false;
        FinalizeTerminalCorrelationSnapshot();
    }
}

void HeptaIBGatewayAdapter::PublishPositionEvent(const IBEvent& outEvent) {
    if (outEvent.type == IBEventType::PortfolioUpdate ||
               outEvent.type == IBEventType::PositionSnapshotItem) {
        m_symbolNetPosition[outEvent.key] = outEvent.number;
        if (outEvent.type == IBEventType::PortfolioUpdate &&
            m_riskSnapshot.positionsComplete && m_eventStreamAuthoritative &&
            std::isfinite(outEvent.number) &&
            !m_cfg.account.empty() && outEvent.account == m_cfg.account) {
            m_authoritativePositionQuantities[outEvent.key] = outEvent.number; m_authoritativePositionContracts[outEvent.key] = outEvent.contract;
            RefreshGrossAbsolutePosition();
        }
    }
}
