#include "ib_crypto_settlement_observation.h"
#include <sstream>
#include "ib_admission_event_policy.h"
#include "ib_gateway_adapter.h"
#include "ib_venue_correlation.h"
#include "ib_order_parse_rejection.h"
#include "../state/ib_contract_identity.h"

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
bool IsIbVisibleProtectiveCandidate(const IBEvent& event) {
    const std::string& type=event.order.orderType;
    return type=="STP" || type=="STP LMT" ||
        type=="TRAIL" || type=="TRAIL LIMIT" ||
        !event.brokerOcaGroup.empty() || event.brokerParentOrderId!=0;
}

// Original IB SDK openOrder projects Lite + native Order from one callback.
// A contradiction within that single Broker observation is not a complete
// account-wide protective-order census. Never synthesize trading authority.
bool NativeAndLiteProtectiveShapeConsistent(const IBEvent& event) {
    if (!event.nativeOrder.present ||
        !IsIbVisibleProtectiveCandidate(event)) return true;
    const auto& n=event.nativeOrder;
    const bool stopType=event.order.orderType=="STP" ||
        event.order.orderType=="STP LMT" ||
        event.order.orderType=="TRAIL" ||
        event.order.orderType=="TRAIL LIMIT";
    return event.id==n.orderId &&
        event.brokerPermId==n.permanentId &&
        event.brokerParentOrderId==n.parentId &&
        event.brokerOcaGroup==n.ocaGroup &&
        event.brokerTransmit==n.transmit &&
        event.brokerClientId==n.clientId &&
        event.account==n.account &&
        event.order.action==n.action &&
        event.order.orderType==n.orderType &&
        event.order.orderRef==n.orderRef &&
        std::isfinite(n.totalQuantity) &&
        event.order.totalQuantity==n.totalQuantity &&
        (!stopType || (std::isfinite(n.stopAuxPrice) &&
                      event.order.auxPrice==n.stopAuxPrice));
}

IBVisibleProtectiveOrderCandidate MakeIbVisibleProtectiveCandidate(
    const IBEvent& event)
{
    IBVisibleProtectiveOrderCandidate candidate;
    candidate.orderType=event.order.orderType;
    candidate.action=event.order.action;
    candidate.ocaGroup=event.brokerOcaGroup;
    candidate.parentOrderId=event.brokerParentOrderId;
    candidate.transmit=event.brokerTransmit;
    candidate.stopTrigger=event.order.auxPrice;
    candidate.totalQuantity=event.order.totalQuantity;
    candidate.brokerClientId=event.brokerClientId;
    candidate.orderId=static_cast<long>(event.id);
    return candidate;
}

bool SameIbVisibleProtectiveCandidate(
    const IBVisibleProtectiveOrderCandidate& a,
    const IBVisibleProtectiveOrderCandidate& b)
{
    return a.orderType==b.orderType && a.action==b.action &&
        a.ocaGroup==b.ocaGroup && a.parentOrderId==b.parentOrderId &&
        a.transmit==b.transmit && a.stopTrigger==b.stopTrigger &&
        a.totalQuantity==b.totalQuantity &&
        a.brokerClientId==b.brokerClientId && a.orderId==b.orderId;
}

}  // namespace

void HeptaIBGatewayAdapter::InvalidateCorrelationSnapshot(const std::string& reason) {
    m_correlationRefreshPending = false;
    m_correlationRefreshConflict = false;
    m_pendingCorrelationOrderIds.clear();
    m_pendingActiveOrderIds.clear();
    m_pendingActiveOrderClientIds.clear();
    m_pendingActivePermIdOwners.clear();
    m_pendingUnboundManualPermIds.clear();
    m_pendingProtectiveCandidatesByPermId.clear();
    m_pendingProtectiveMetadataIncomplete = false;
    m_pendingMissingPermanentId = false;
    m_correlationSnapshot.connectionEpoch = m_connectionEpoch;
    m_correlationSnapshot.generation = m_correlationGeneration;
    m_correlationSnapshot.complete = false;
    m_correlationSnapshot.reasonCode = reason;
    m_correlationSnapshot.activeOrderIdsByCorrelation.clear();
    m_correlationSnapshot.activeOrderIds.clear();
    m_correlationSnapshot.activeOrderClientIdsByOrderId.clear();
    m_correlationSnapshot.activeOrderByPermanentId.clear();
    m_correlationSnapshot.unboundManualPermanentIds.clear();
    m_correlationSnapshot.visibleProtectiveCandidatesByPermId.clear();
    m_correlationSnapshot.protectiveMetadataIncomplete = false;
    m_correlationSnapshot.activeOrderMissingPermanentId = false;
}

IBAuthoritativeCorrelationSnapshot HeptaIBGatewayAdapter::GetAuthoritativeCorrelationSnapshot() const {
    std::lock_guard<std::recursive_mutex> lk(m_apiMutex);
    return m_correlationSnapshot;
}

std::uint64_t CallbackMonotonicNs() {
    const auto raw = std::chrono::duration_cast<std::chrono::nanoseconds>(
        std::chrono::steady_clock::now().time_since_epoch()).count();
    return raw > 0 ? static_cast<std::uint64_t>(raw) : 1U;
}

bool HeptaIBGatewayAdapter::MergeIncrementalActiveOrder(
    long orderId, const std::string& correlationId, int brokerClientId,
    std::int64_t brokerPermId) {
    if (!m_correlationSnapshot.complete) return true;
    if (orderId < 0 || brokerClientId < 0) {
        InvalidateCorrelationSnapshot("IB_ACTIVE_ORDER_ID_OR_CLIENT_INVALID");
        return false;
    }
    if (orderId == 0 &&
        (brokerClientId != 0 || brokerPermId <= 0 ||
         !correlationId.empty())) {
        InvalidateCorrelationSnapshot("IB_UNBOUND_MANUAL_PERMID_REQUIRED");
        return false;
    }
    if (brokerPermId > 0) {
        const auto identity = std::make_pair(brokerClientId, orderId);
        const auto found =
            m_correlationSnapshot.activeOrderByPermanentId.find(brokerPermId);
        if (found != m_correlationSnapshot.activeOrderByPermanentId.end() &&
            found->second != identity) {
            InvalidateCorrelationSnapshot("IB_ACTIVE_ORDER_PERMID_COLLISION");
            return false;
        }
        m_correlationSnapshot.activeOrderByPermanentId[brokerPermId] = identity;
        if (orderId == 0)
            m_correlationSnapshot.unboundManualPermanentIds.insert(brokerPermId);
    } else {
        m_correlationSnapshot.activeOrderMissingPermanentId = true;
    }
    const auto known = m_correlationSnapshot.activeOrderClientIdsByOrderId.find(orderId);
    if (known != m_correlationSnapshot.activeOrderClientIdsByOrderId.end() &&
        known->second != brokerClientId) {
        InvalidateCorrelationSnapshot("IB_ACTIVE_ORDER_CLIENT_ID_COLLISION");
        return false;
    }
    // A foreign client can contribute to Broker-global active risk, never
    // to the original service owner's correlation or cancellation authority.
    if (brokerClientId != m_cfg.clientId && !correlationId.empty()) {
        InvalidateCorrelationSnapshot("IB_FOREIGN_CLIENT_CORRELATION_INVALID");
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
    m_correlationSnapshot.activeOrderClientIdsByOrderId[orderId] = brokerClientId;
    return true;
}

void HeptaIBGatewayAdapter::ObserveIncrementalProtectiveMetadata(
    const IBEvent& event)
{
    if (!m_correlationSnapshot.complete ||
        event.type!=IBEventType::OpenOrder ||
        event.account!=m_cfg.account || event.id<0 ||
        event.id>LONG_MAX || event.brokerClientId<0)
        return;
    if (!NativeAndLiteProtectiveShapeConsistent(event)) {
        InvalidateCorrelationSnapshot(
            "IB_PROTECTIVE_NATIVE_LITE_INCREMENTAL_CONFLICT");
        return;
    }
    if (event.brokerPermId<=0) {
        if (IsIbVisibleProtectiveCandidate(event))
            m_correlationSnapshot.protectiveMetadataIncomplete=true;
        return;
    }
    auto& visible=m_correlationSnapshot.visibleProtectiveCandidatesByPermId;
    const auto previous=visible.find(event.brokerPermId);
    if (!IsIbVisibleProtectiveCandidate(event)) {
        // An existing stop/OCA becoming an ordinary order must NOT leave an
        // outdated protective candidate in a completed active projection.
        // The new all-client download is required to re-establish its shape.
        if (previous!=visible.end())
            InvalidateCorrelationSnapshot(
                "IB_PROTECTIVE_INCREMENTAL_REMOVED_REQUIRES_REFRESH");
        return;
    }
    const auto candidate=MakeIbVisibleProtectiveCandidate(event);
    if (previous!=visible.end() &&
        !SameIbVisibleProtectiveCandidate(previous->second,candidate)) {
        InvalidateCorrelationSnapshot(
            "IB_PROTECTIVE_INCREMENTAL_CONFLICT_REQUIRES_REFRESH");
        return;
    }
    visible[event.brokerPermId]=candidate;
}

void HeptaIBGatewayAdapter::InvalidateTerminalCorrelationSnapshot(
    const std::string& reason) {
    m_liveTerminalBindings.clear();
    m_completedOrdersRefreshPending = false;
    m_executionsRefreshPending = false;
    m_terminalCorrelationRefreshConflict = false;
    m_pendingTerminalOrderIds.clear();
    m_pendingTerminalStatuses.clear();
    m_pendingExecutionCorrelations.clear();
    m_pendingExecutionIdsByCorrelation.clear();
    m_terminalCorrelationSnapshot.connectionEpoch = m_connectionEpoch;
    m_terminalCorrelationSnapshot.generation =
        m_terminalCorrelationGeneration;
    m_terminalCorrelationSnapshot.complete = false;
    m_terminalCorrelationSnapshot.exposureGeneration = 0;
    m_terminalCorrelationSnapshot.reasonCode = reason;
    m_terminalCorrelationSnapshot.terminalOrderIdsByCorrelation.clear();
    m_terminalCorrelationSnapshot.terminalStatusesByCorrelation.clear();
    m_terminalCorrelationSnapshot.executionCorrelations.clear();
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
        m_terminalCorrelationSnapshot.executionCorrelations.clear();
        m_terminalCorrelationSnapshot.executionOrderIds.clear();
        return;
    }
    for (const auto& execution : m_pendingExecutionIdsByCorrelation) {
        auto order=m_pendingTerminalOrderIds.find(execution.first);
        if (order!=m_pendingTerminalOrderIds.end() && order->second>0 && order->second!=execution.second) {
            InvalidateTerminalCorrelationSnapshot("IB_TERMINAL_EXECUTION_ORDER_ID_CONFLICT");
            InvalidateRiskSnapshot("IB_TERMINAL_EXECUTION_ORDER_ID_CONFLICT");
            return;
        }
        // IB completedOrder() may omit its connection-scoped numeric order id
        // (reported as 0) while retaining our durable orderRef correlation.
        // The paired reqExecutions() refresh is account/client scoped and, after
        // its End barrier, carries the exact positive numeric id for that same
        // correlation. Promote only this unknown-id case; a positive mismatch
        // above remains fatal and a correlation mismatch cannot reach this row.
        if (order!=m_pendingTerminalOrderIds.end() && order->second==0 &&
            execution.second>0)
            order->second=execution.second;
    }
    for (std::map<std::string, std::string>::iterator status =
             m_pendingTerminalStatuses.begin();
         status != m_pendingTerminalStatuses.end();) {
        const std::map<std::string, long>::const_iterator order =
            m_pendingTerminalOrderIds.find(status->first);
        if (status->second == "Filled" &&
            (order == m_pendingTerminalOrderIds.end() ||
             m_pendingExecutionCorrelations.find(status->first) ==
                 m_pendingExecutionCorrelations.end())) {
            if (order != m_pendingTerminalOrderIds.end()) {
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
    m_terminalCorrelationSnapshot.executionCorrelations =
        m_pendingExecutionCorrelations;
    m_terminalCorrelationSnapshot.executionOrderIds.clear();
    for (const std::string& correlation : m_pendingExecutionCorrelations) {
        const auto order = m_pendingTerminalOrderIds.find(correlation);
        if (order != m_pendingTerminalOrderIds.end() && order->second > 0)
            m_terminalCorrelationSnapshot.executionOrderIds.insert(order->second);
    }
    m_pendingTerminalOrderIds.clear();
    m_pendingTerminalStatuses.clear();
    m_pendingExecutionCorrelations.clear();
    m_pendingExecutionIdsByCorrelation.clear();
    // A live callback may race the initial End markers. Preserve its proof
    // without ever promoting an incomplete bootstrap snapshot.
    std::vector<long> liveOrderIds;
    for (const auto& item : m_liveTerminalBindings) liveOrderIds.push_back(item.first);
    for (long orderId : liveOrderIds) PublishLiveTerminalEvidence(orderId);
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
    m_rejectedFxCashObservationBalances.clear();
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

IBPhysicalCryptoPositionObservation
HeptaIBGatewayAdapter::GetPhysicalCryptoPositionObservation() const {
    std::lock_guard<std::recursive_mutex> lk(m_apiMutex);
    IBPhysicalCryptoPositionObservation result;
    const auto snapshot = BuildRecoveryAuditSnapshotLocked();
    if (m_cfg.authoritativeCryptoContracts.empty() ||
        !CanObservePhysicalCryptoPositions(IsConnected(), IsEventStreamAuthoritative(),
            m_connectionEpoch, snapshot)) return result;
    for (const auto& configured : m_cfg.authoritativeCryptoContracts) {
        if (configured.second.secType != "CRYPTO") return IBPhysicalCryptoPositionObservation();
        double quantity=0.0; std::string reason;
        if (!ResolveAuthoritativePositionQuantity(configured.first, configured.second, quantity, reason) ||
            !std::isfinite(quantity)) return IBPhysicalCryptoPositionObservation();
        result.positions[configured.first]=quantity;
    }
    result.complete=true;
    result.account=m_cfg.account;
    result.connectionEpoch=m_connectionEpoch;
    result.positionGeneration=snapshot.risk.positionsGeneration;
    result.activeGeneration=snapshot.active.generation;
    result.terminalGeneration=snapshot.terminal.generation;
    return result;
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
    for (const auto& configured : m_cfg.authoritativeCryptoContracts)
    {
        double quantity = 0.0; std::string reason;
        if (!ResolveAuthoritativePositionQuantity(configured.first, configured.second, quantity, reason)) continue;
        for (const auto& observed : m_authoritativePositionContracts)
            if (observed.second.symbol == configured.second.symbol &&
                observed.second.secType == configured.second.secType &&
                observed.second.currency == configured.second.currency &&
                (observed.second.exchange.empty() || observed.second.exchange == configured.second.exchange))
                combined.erase(observed.first);
        if (quantity != 0.0) combined[configured.first] = quantity;
    }
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

void HeptaIBGatewayAdapter::RecordCallbackConflict() noexcept {
    if (m_callbackConflictCount == std::numeric_limits<std::uint64_t>::max())
        m_callbackConflictMetricsSaturated = true;
    else
        ++m_callbackConflictCount;
}

void HeptaIBGatewayAdapter::MarkCallbackConflict(bool& flag) noexcept {
    if (!flag) RecordCallbackConflict();
    flag = true;
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
        if (event.queueIngressMonotonicNs != 0) {
            const std::uint64_t now = CallbackMonotonicNs();
            if (now >= event.queueIngressMonotonicNs)
                m_callbackQueueLag.Observe(now - event.queueIngressMonotonicNs);
            else
                m_callbackQueueLag.saturated = true;
        }
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
    // Any active openOrder whose Broker account is unknown may belong to
    // our PAPER account. Skipping it would turn a partially sourced order
    // inventory into a false global zero, especially for manual TWS orders.
    if (event.type == IBEventType::OpenOrder &&
        !m_cfg.account.empty() && event.account.empty() &&
        !m_correlationRefreshPending && m_correlationSnapshot.complete)
        InvalidateCorrelationSnapshot("IB_ACTIVE_ORDER_ACCOUNT_UNKNOWN");
    // An account-wide OpenOrder callback is broker acknowledgement after a
    // process restart. Restore cancelability from that authoritative evidence
    // rather than relying on process-local submit history.
    if (event.type == IBEventType::OpenOrder &&
        !m_cfg.account.empty() && event.account == m_cfg.account) {
        if (event.id >= 0 && event.id <= LONG_MAX) {
            // reqAllOpenOrders echoes orders from OTHER client IDs. Those
            // count toward account-global active risk, but MUST NOT seed
            // the original client-scoped cancel/terminal lifecycle.
            acceptedBrokerOpenOrder =
                event.brokerClientId == m_cfg.clientId && event.id > 0 ?
                m_orderLifecycle.RecordBrokerOpenOrder(
                    static_cast<long>(event.id), event.value) :
                event.brokerClientId >= 0;
            // Rebuild the callback identity from a broker-echoed open order,
            // not the naked numeric id. Previously only a new local send made
            // this binding, so every post-restart orderStatus was quarantined
            // as unscoped and an original PendingCancel could never finish.
            if (acceptedBrokerOpenOrder && event.connectionEpoch == m_connectionEpoch &&
                event.brokerClientId == m_cfg.clientId && event.id <= LONG_MAX &&
                (event.order.action == "BUY" || event.order.action == "SELL") &&
                !event.contract.symbol.empty() && !event.contract.secType.empty() &&
                !event.contract.currency.empty() && !event.contract.exchange.empty() &&
                std::isfinite(event.order.totalQuantity) &&
                std::isfinite(event.order.cashQuantity) &&
                (event.order.cashQuantity > 0.0 ? event.order.totalQuantity == 0.0 :
                    event.order.cashQuantity == 0.0 && event.order.totalQuantity > 0.0)) {
                std::string correlation, decodeReason;
                if (DecodeVenueOrderRef(event.order.orderRef, correlation, decodeReason)) {
                    const long id = static_cast<long>(event.id);
                    const auto existing = m_liveTerminalBindings.find(id);
                    if (existing == m_liveTerminalBindings.end()) {
                        LiveTerminalBinding binding;
                        binding.connectionEpoch = m_connectionEpoch;
                        binding.correlationId = correlation;
                        binding.contract = event.contract;
                        binding.side = event.order.action;
                        binding.quantity = event.order.totalQuantity;
                        binding.cashQuantity = event.order.cashQuantity;
                        m_liveTerminalBindings.emplace(id, std::move(binding));
                    } else if (existing->second.connectionEpoch != m_connectionEpoch ||
                        existing->second.correlationId != correlation ||
                        existing->second.side != event.order.action ||
                        existing->second.quantity != event.order.totalQuantity ||
                        existing->second.cashQuantity != event.order.cashQuantity ||
                        !MatchesSubmittedIBContract(existing->second.contract, event.contract)) {
                        MarkCallbackConflict(m_correlationRefreshConflict);
                        InvalidateCorrelationSnapshot("IB_RECOVERED_ORDER_BINDING_CONFLICT");
                        InvalidateTerminalCorrelationSnapshot("IB_RECOVERED_ORDER_BINDING_CONFLICT");
                    }
                }
            }
            if (acceptedBrokerOpenOrder &&
                event.brokerClientId == m_cfg.clientId && event.id > 0)
                DispatchPendingCancelIfAcknowledged(
                    static_cast<long>(event.id), event.value,
                    event.value != "Filled", true);
        }
        if (event.brokerClientId < 0 && !m_correlationRefreshPending &&
            m_correlationSnapshot.complete)
            InvalidateCorrelationSnapshot("IB_ACTIVE_ORDER_CLIENT_ID_UNKNOWN");
        if (acceptedBrokerOpenOrder &&
            !m_correlationRefreshPending && m_correlationSnapshot.complete) {
            bool merged = false;
            if (event.brokerClientId != m_cfg.clientId) {
                merged = MergeIncrementalActiveOrder(
                    static_cast<long>(event.id), std::string(),
                    event.brokerClientId, event.brokerPermId);
            } else {
                std::string correlationId;
                std::string decodeReason;
                if (DecodeVenueOrderRef(
                        event.order.orderRef, correlationId, decodeReason)) {
                    merged = MergeIncrementalActiveOrder(
                        static_cast<long>(event.id), correlationId,
                        event.brokerClientId, event.brokerPermId);
                } else if (event.order.orderRef.compare(0, 2, "H1") == 0) {
                    InvalidateCorrelationSnapshot(decodeReason);
                } else {
                    merged = MergeIncrementalActiveOrder(
                        static_cast<long>(event.id), std::string(),
                        event.brokerClientId, event.brokerPermId);
                }
            }
            // The same callback that adds an account-wide active identity
            // must also reconcile Broker stop/OCA/parent CANDIDATE metadata.
            // Otherwise health can show a new active order but zero
            // protective candidates, or retain an obsolete candidate.
            if (merged && m_correlationSnapshot.complete)
                ObserveIncrementalProtectiveMetadata(event);
        }
    }
    return acceptedBrokerOpenOrder;
}

void HeptaIBGatewayAdapter::RetireTerminalActiveOrder(
    long orderId, bool eraseRiskBaseline) {
    if (m_postFillReconciliationOrderIds.count(orderId) != 0 ||
        !m_correlationSnapshot.complete)
        return;
    const auto identity = m_correlationSnapshot.activeOrderClientIdsByOrderId.find(orderId);
    if (identity != m_correlationSnapshot.activeOrderClientIdsByOrderId.end() &&
        identity->second != m_cfg.clientId)
        return; // A local numeric terminal may NOT erase a foreign order.
    if (eraseRiskBaseline) m_orderRiskBaselines.erase(orderId);
    m_correlationSnapshot.activeOrderIds.erase(orderId);
    m_correlationSnapshot.activeOrderClientIdsByOrderId.erase(orderId);
    for (auto it = m_correlationSnapshot.activeOrderByPermanentId.begin();
         it != m_correlationSnapshot.activeOrderByPermanentId.end();) {
        if (it->second.first == m_cfg.clientId &&
            it->second.second == orderId) {
            m_correlationSnapshot.visibleProtectiveCandidatesByPermId.erase(
                it->first);
            it = m_correlationSnapshot.activeOrderByPermanentId.erase(it);
        } else {
            ++it;
        }
    }
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
    // Native orderStatus includes the Broker client ID. Foreign terminal
    // statuses NEVER grant our owner cancel/terminal authority, but if a
    // known foreign active order has ended, keeping its old protective
    // candidate as a current complete census is equally misleading.
    if (event.brokerClientId >= 0 && event.brokerClientId != m_cfg.clientId) {
        if (m_correlationSnapshot.complete && IsIbFinalStatus(event.key) &&
            event.id >= 0 && event.id <= LONG_MAX &&
            (event.account.empty() || event.account == m_cfg.account)) {
            const auto known=m_correlationSnapshot.activeOrderClientIdsByOrderId.find(
                static_cast<long>(event.id));
            if (known!=m_correlationSnapshot.activeOrderClientIdsByOrderId.end() &&
                known->second==event.brokerClientId)
                InvalidateCorrelationSnapshot(
                    "IB_FOREIGN_TERMINAL_REQUIRES_GLOBAL_REFRESH");
        }
        return;
    }
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

    // Economic-fill identity is never the naked numeric IB order id. Prefer the
    // durable service correlation when this is one of our sends. Native
    // orderStatus does not echo orderRef, so the live send binding supplies the
    // same correlation under the adapter lock. Historical execution callbacks
    // carry the broker-echoed orderRef and execution id.
    std::string correlationId;
    // An execution query can return OLD orderRef data on the NEW connection.
    // Its broker echo takes precedence over any current numeric-id live binding.
    if (event.type == IBEventType::ExecutionDetails)
    {
        std::string decodeReason;
        if (!DecodeVenueOrderRef(event.order.orderRef, correlationId, decodeReason) &&
            event.order.orderRef.compare(0, 2, "H1") == 0)
        {
            InvalidateRecoveryAuditBarrier("IB_RECOVERY_AUDIT_EXECUTION_CORRELATION_INVALID");
            InvalidateRiskSnapshot("IB_RISK_EXECUTION_CORRELATION_INVALID");
            RecordCallbackConflict(); return false;
        }
    }
    else
    {
        const auto live = m_liveTerminalBindings.find(orderId);
        if (live != m_liveTerminalBindings.end() && IsConsistentBoundOrderStatus(event))
            correlationId = live->second.correlationId;
    }

    std::string fillIdentity;
    if (!correlationId.empty())
        fillIdentity = "corr|" + std::to_string(event.brokerClientId) + "|" + event.account + "|" + correlationId;
    else if (event.type == IBEventType::ExecutionDetails)
    {
        if (event.brokerClientId < 0 || event.key.empty())
        {
            InvalidateRecoveryAuditBarrier(
                "IB_RECOVERY_AUDIT_EXECUTION_IDENTITY_INCOMPLETE");
            InvalidateRiskSnapshot("IB_RISK_EXECUTION_IDENTITY_INCOMPLETE");
            RecordCallbackConflict();
            return false;
        }
        // Account-wide/manual execution evidence still invalidates risk and
        // must be deduplicated, but cannot be promoted to a service order.
        fillIdentity = "external-exec|" + std::to_string(event.brokerClientId) +
            "|" + event.key + "|" + event.order.orderRef;
    }
    else
    {
        if (event.connectionEpoch == 0 || event.brokerClientId < 0)
            return false;
        fillIdentity = "legacy-status|" + std::to_string(event.connectionEpoch) +
            "|" + std::to_string(event.brokerClientId) + "|" +
            std::to_string(orderId);
    }

    const auto prior = m_observedEconomicFillQuantityByIdentity.find(fillIdentity);
    if (prior != m_observedEconomicFillQuantityByIdentity.end() &&
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
    m_observedEconomicFillQuantityByIdentity[fillIdentity] = event.number2;
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
    if (IsIbGlobalUpgradeNotice(event) && event.connectionEpoch==m_connectionEpoch) {
        EmitObsEvent("risk.client_upgrade_notice", "\"error_code\":2172");
        return; // event remains in the queue and is persisted by Execution.
    }
    if (IsIbUnscopedSecDefFarmDisconnect(event) &&
        event.connectionEpoch == m_connectionEpoch) {
        // The raw 2157 callback is still sent to Execution/OMS. Do not
        // inflate the order-error counter or mark a valid cash quote unsafe
        // merely because the *security definition* farm is unavailable.
        EmitObsEvent("broker.secdef_farm_unavailable_unscoped",
            "\"error_code\":2157");
        return;
    }
    if (IsIbCorroboratedCashMarketDataRequestError(event) &&
        event.connectionEpoch == m_connectionEpoch &&
        event.brokerClientId == m_cfg.clientId) {
        EmitObsEvent("market_data.local_request_failure",
            "\"request_id\":" + std::to_string(event.requestId) +
            ",\"error_code\":" + std::to_string(errorCode));
        return; // Preserve the raw callback; no order or account effect.
    }
    // A repeat-cancel diagnostic is not an economic failure or terminal proof.
    // Suppress ONLY a current-epoch, locally corroborated pending-cancel 10148;
    // do not globally whitelist this code or infer Filled from its description.
    if (errorCode == 10148 && event.connectionEpoch == m_connectionEpoch &&
        event.id >= 0 && event.id <= std::numeric_limits<long>::max() &&
        (event.brokerClientId < 0 || event.brokerClientId == m_cfg.clientId) &&
        (event.account.empty() || event.account == m_cfg.account) &&
        (event.value.find("PendingCancel") != std::string::npos ||
         event.value.find("PreCancelled") != std::string::npos)) {
        std::string suppression;
        if (!m_orderLifecycle.CanCancel(static_cast<long>(event.id), &suppression) &&
            suppression == "CANCEL_ALREADY_PENDING") {
            EmitObsEvent("risk.pending_cancel_diagnostic",
                "\"error_code\":10148,\"order_id\":" + std::to_string(event.id));
            return;
        }
    }
    if (!IsIbConnectionControlError(errorCode))
        NotifyErrorEvent(errorCode);
    const bool cashSizeParseRejected =
        IsIbOrderSizeParseRejection320(event) &&
        event.connectionEpoch == m_connectionEpoch && event.id <= LONG_MAX &&
        m_liveTerminalBindings.find(static_cast<long>(event.id)) !=
            m_liveTerminalBindings.end() &&
        m_liveTerminalBindings.at(static_cast<long>(event.id)).connectionEpoch ==
            m_connectionEpoch;
    if (event.id < 0 || event.id > LONG_MAX ||
        (errorCode != 201 && errorCode != 202 && !cashSizeParseRejected))
        return;
    const long orderId = static_cast<long>(event.id);
    // Error 201/202 carries a numeric order id, which is scoped to an IB API
    // client. Another client's same-number rejection/cancel must NOT create
    // this owner's terminal acknowledgement or drain an active order.
    // Unscoped SDK errors are retained as diagnostics but never elevated
    // to account-wide permanent-identity proof.
    const auto activeOwner =
        m_correlationSnapshot.activeOrderClientIdsByOrderId.find(orderId);
    const bool foreignReported =
        event.brokerClientId >= 0 && event.brokerClientId != m_cfg.clientId;
    const bool mappedForeign =
        m_correlationSnapshot.complete &&
        activeOwner != m_correlationSnapshot.activeOrderClientIdsByOrderId.end() &&
        activeOwner->second != m_cfg.clientId;
    const bool accountMismatch =
        !event.account.empty() && event.account != m_cfg.account;
    if (foreignReported || accountMismatch ||
        (event.brokerClientId < 0 && mappedForeign)) {
        // Positive foreign or unscoped same-number Broker terminal errors
        // cannot safely remove original or foreign protective evidence.
        // Invalidate a currently COMPLETE observed foreign order, requiring
        // an original new all-client download before believing its status.
        if (mappedForeign && !accountMismatch &&
            (event.brokerClientId < 0 ||
             event.brokerClientId == activeOwner->second))
            InvalidateCorrelationSnapshot(
                "IB_FOREIGN_TERMINAL_ERROR_REQUIRES_GLOBAL_REFRESH");
        EmitObsEvent("broker.foreign_order_error_not_local_terminal",
            "\"orderId\":" + std::to_string(orderId) +
            ",\"errorCode\":" + std::to_string(errorCode));
        return;
    }
    const std::string status =
        errorCode == 202 ? "Cancelled" : "Rejected";
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
    // A numeric IB order id is only unique within its broker client. Do not
    // let another client's terminal status remove a locally submitted active
    // order before the stricter live terminal-evidence path can reject it.
    if (event.type == IBEventType::OrderStatus &&
        m_liveTerminalBindings.count(static_cast<long>(event.id)) != 0 &&
        !IsConsistentBoundOrderStatus(event))
        return;
    if (m_recoveryAuditBarrierComplete &&
        !IsExactDuplicateNativeTerminalStatus(event) &&
        (event.type == IBEventType::OpenOrder ||
         event.type == IBEventType::OrderStatus ||
         event.type == IBEventType::ExecutionDetails ||
         event.type == IBEventType::CompletedOrder ||
         (event.type == IBEventType::Error && event.id >= 0 &&
          !(IsIbCorroboratedCashMarketDataRequestError(event) &&
            event.connectionEpoch == m_connectionEpoch &&
            event.brokerClientId == m_cfg.clientId))))
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

bool HeptaIBGatewayAdapter::QuarantineContradictoryOrderStatusIdentity(
    const IBEvent& event)
{
    if (event.type != IBEventType::OrderStatus ||
        !m_correlationSnapshot.complete ||
        event.connectionEpoch != m_connectionEpoch ||
        event.id < 0 || event.id > LONG_MAX ||
        event.brokerClientId < 0 || event.value == "execDetails")
        return false;
    // A provided OTHER-account identity cannot be used to retire or
    // invalidate a current-account order. An absent account is not proof
    // of other-account scope: IB orderStatus often omits the account.
    if (!event.account.empty() && event.account != m_cfg.account)
        return false;
    const long scopedId = static_cast<long>(event.id);
    const bool activeStatus =
        event.key=="Submitted" || event.key=="PreSubmitted" ||
        event.key=="PendingSubmit" || event.key=="ApiPending" ||
        event.key=="PendingCancel";
    if (event.brokerPermId <= 0) {
        // An unbound manual status with client0/order0 carries NO
        // authoritative account-wide identity. It could represent a NEW
        // manual order even when another manual order shares numeric zero.
        if (activeStatus && event.brokerClientId == 0 && scopedId == 0) {
            InvalidateCorrelationSnapshot(
                "IB_UNBOUND_MANUAL_STATUS_PERMID_UNKNOWN");
            return false;
        }
        if (event.key=="PendingCancel") {
            // A pending cancellation of a visible protective candidate
            // cannot remain a completed protective census merely because
            // the status callback lacks the positive host permId.
            for (const auto& candidate :
                 m_correlationSnapshot.visibleProtectiveCandidatesByPermId) {
                if (candidate.second.brokerClientId == event.brokerClientId &&
                    candidate.second.orderId == scopedId) {
                    InvalidateCorrelationSnapshot(
                        "IB_PROTECTIVE_PENDING_CANCEL_PERMID_UNKNOWN");
                    return false;
                }
            }
        }
        return false;
    }
    const auto scoped = std::make_pair(event.brokerClientId, scopedId);
    const auto& owners = m_correlationSnapshot.activeOrderByPermanentId;
    const auto matchingPerm = owners.find(event.brokerPermId);
    bool contradictory = matchingPerm != owners.end() &&
        matchingPerm->second != scoped;

    // A bound numeric (clientId, orderId) cannot suddenly acquire ANOTHER
    // Host-wide permId. Manual unbound (client0,orderId0) deliberately uses
    // many distinct positive permIds and is handled by the lookup above.
    if (!contradictory && scopedId != 0 &&
        m_correlationSnapshot.activeOrderClientIdsByOrderId.count(scopedId) &&
        m_correlationSnapshot.activeOrderClientIdsByOrderId.at(scopedId) ==
            event.brokerClientId) {
        for (const auto& owner : owners) {
            if (owner.second == scoped &&
                owner.first != event.brokerPermId) {
                contradictory = true;
                break;
            }
        }
    }
    if (contradictory) {
        // Drop the untrusted callback before any original native lifecycle,
        // bound-terminal inference, or downstream order event consumer sees
        // it. A positive ECONOMIC difference additionally poisons risk.
        InvalidateCorrelationSnapshot("IB_ORDER_STATUS_PERMID_IDENTITY_CONFLICT");
        InvalidateRecoveryAuditBarrier(
            "IB_RECOVERY_AUDIT_ORDER_STATUS_PERMID_IDENTITY_CONFLICT");
        if (HasEconomicFillEvidence(event))
            InvalidateRiskSnapshot(
                "IB_RISK_ORDER_STATUS_PERMID_IDENTITY_CONFLICT");
        RecordCallbackConflict();
        EmitObsEvent("callback.order_status_permid_conflict",
            "\"clientId\":" + std::to_string(event.brokerClientId) +
            ",\"orderId\":" + std::to_string(scopedId));
        return true;
    }

    // An orderStatus for an unknown positive permId may be the FIRST
    // notification of a new foreign/manual order. Its numeric orderId can
    // collide with an already known order (all manual orders use 0). Never
    // leave the old complete all-client snapshot unchanged.
    if (activeStatus && matchingPerm==owners.end()) {
        InvalidateCorrelationSnapshot(
            "IB_ORDER_STATUS_UNSEEN_ACTIVE_PERMID_REQUIRES_REFRESH");
        return false; // status still reaches original callback consumers
    }
    if (event.key=="PendingCancel" &&
        m_correlationSnapshot.visibleProtectiveCandidatesByPermId.count(
            event.brokerPermId) != 0)
        InvalidateCorrelationSnapshot(
            "IB_PROTECTIVE_PENDING_CANCEL_REQUIRES_GLOBAL_REFRESH");
    return false;
}

bool HeptaIBGatewayAdapter::TryDequeueEvent(IBEvent& outEvent) {
    std::lock_guard<std::recursive_mutex> lk(m_apiMutex);
    if (!m_api) return false;
    for (;;) {
        if (!DequeueCurrentEpochEvent(outEvent)) return false;
        if (QuarantineContradictoryOrderStatusIdentity(outEvent))
            continue;
        if ((outEvent.type != IBEventType::OrderStatus &&
             outEvent.type != IBEventType::ExecutionDetails) ||
            m_liveTerminalBindings.count(static_cast<long>(outEvent.id)) == 0)
            break;
        // Historical execution-query responses retain their existing explicit
        // request/end path. They cannot supplement the live terminal view.
        if ((outEvent.type == IBEventType::ExecutionDetails ||
             IsHistoricalSyntheticExecutionStatus(outEvent)) && outEvent.requestId >= 0)
            break;
        const bool consistent = outEvent.type == IBEventType::OrderStatus ?
            IsConsistentBoundOrderStatus(outEvent) :
            IsConsistentBoundLiveExecution(outEvent);
        if (consistent) break;
        // Quarantine before both adapter projections and downstream runtime
        // consumers can mistake another client's same numeric id for ours.
        // A rejected event is still a late broker observation: it must not
        // leave a previously completed recovery barrier authoritative. An
        // economic callback for this account can indicate other-client
        // activity, so also require a fresh risk snapshot without attributing
        // that fill to this service's order.
        InvalidateRecoveryAuditBarrier("IB_RECOVERY_AUDIT_UNTRUSTED_BOUND_ORDER_EVENT");
        if ((outEvent.account.empty() || outEvent.account == m_cfg.account) &&
            HasEconomicFillEvidence(outEvent))
            InvalidateRiskSnapshot("IB_RISK_UNTRUSTED_BOUND_ORDER_EVENT");
        RecordCallbackConflict();
        EmitObsEvent("callback.bound_order_identity_rejected",
            "\"order_id\":" + std::to_string(outEvent.id));
    }
    if (outEvent.type == IBEventType::OrderStatus && IsConsistentBoundOrderStatus(outEvent))
    {
        const auto& binding = m_liveTerminalBindings.at(static_cast<long>(outEvent.id));
        std::string reason;
        if (!IbVenueCorrelationCodec::EncodeOrderRef(binding.correlationId, outEvent.order.orderRef, reason))
        {
            InvalidateRiskSnapshot("IB_LIVE_STATUS_BINDING_INVALID");
            return false;
        }
        outEvent.account = m_cfg.account;
    }
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
            if (initialSnapshot) MarkCallbackConflict(m_fxCashRefreshConflict);
            else InvalidateRiskSnapshot("IB_ACCOUNT_READY_VALUE_INVALID");
            return true;
        }
        if (initialSnapshot) {
            if (m_accountReadyObserved && m_accountReady !=
                    (ready == "true"))
                MarkCallbackConflict(m_fxCashRefreshConflict);
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
        if (initialSnapshot) MarkCallbackConflict(m_fxCashRefreshConflict);
        else InvalidateRiskSnapshot("IB_FX_CASH_BALANCE_INVALID");
        return true;
    }
    if (initialSnapshot) {
        const std::map<std::string, double>::const_iterator existing =
            m_pendingFxCashBalances.find(currency);
        if (existing != m_pendingFxCashBalances.end() &&
            existing->second != parsed)
            MarkCallbackConflict(m_fxCashRefreshConflict);
        else
            m_pendingFxCashBalances[currency] = parsed;
        return true;
    }
    // Subscription pushes outside a declared request/end generation are not a
    // snapshot boundary and can be delayed by IB. Never let them directly
    // mutate the authoritative campaign exposure; however, a changed value is
    // proof that the committed snapshot is stale, so invalidate fail-closed.
    // A rejected startup cash snapshot remains rejected. Repeating precisely
    // the same observed balance carries no new information and must not erase
    // an independently completed physical crypto positions download. A changed
    // balance (including a return to the expected checkpoint) still invalidates
    // all risk/settlement observations below. No cash value is promoted here.
    if (!m_riskSnapshot.accountComplete && !m_riskSnapshot.fxCashComplete &&
        m_riskSnapshot.reasonCode == "IB_FX_CASH_ATTESTED_BALANCE_MISMATCH") {
        const auto rejected = m_rejectedFxCashObservationBalances.find(currency);
        if (rejected != m_rejectedFxCashObservationBalances.end() &&
            rejected->second == parsed) return true;
    }
    // After a committed cash generation has already been invalidated, the
    // authoritative cash map is empty until a NEW account request/end pair
    // completes. Subsequent numeric subscription callbacks have no generation
    // identity and cannot reauthorize anything. In particular they must NOT
    // cancel an in-progress independent positions refresh by re-entering
    // InvalidateRiskSnapshot() on every monitor tick. Preserve the current
    // denial and await a new coherent account+positions generation.
    //
    // Startup-attestation mismatch is different: a changed monitor balance
    // must still invalidate any independently observed physical crypto
    // position, as tested by the existing original rejection regression.
    if (!m_riskSnapshot.accountComplete &&
        !m_riskSnapshot.fxCashComplete &&
        m_authoritativeFxCashBalances.empty() &&
        m_riskSnapshot.reasonCode !=
            "IB_FX_CASH_ATTESTED_BALANCE_MISMATCH")
        return true;
    const std::map<std::string, double>::const_iterator committed =
        m_authoritativeFxCashBalances.find(currency);
    if (committed == m_authoritativeFxCashBalances.end() ||
        committed->second != parsed) {
        // A subscription update is a freshness signal, never an account/position
        // download boundary. Record the provenance of the invalidation without
        // persisting currency balances or silently reopening broker authority.
        // Only the first transition in a risk generation needs a diagnostic.
        // Repeated subscription pushes after risk is already denied must not
        // flood a bounded observability journal or look like new generations.
        if (m_riskSnapshot.accountComplete ||
            m_riskSnapshot.fxCashComplete ||
            m_riskSnapshot.reasonCode !=
                "IB_FX_CASH_BALANCE_OUT_OF_GENERATION_CHANGE")
            EmitObsEvent(
            "risk.fx_cash_monitor_invalidated",
            "\"subscription_request_id\":" + std::to_string(event.id) +
            ",\"connection_epoch\":" + std::to_string(m_connectionEpoch) +
            ",\"account_generation\":" +
                std::to_string(m_riskSnapshot.accountGeneration) +
            ",\"positions_generation\":" +
                std::to_string(m_riskSnapshot.positionsGeneration) +
            ",\"fx_cash_generation\":" +
                std::to_string(m_riskSnapshot.fxCashGeneration) +
            ",\"previous_cash_checkpoint_present\":" +
                (committed == m_authoritativeFxCashBalances.end() ?
                    "false" : "true") +
            ",\"account_complete_before\":" +
                (m_riskSnapshot.accountComplete ? "true" : "false") +
            ",\"fx_cash_complete_before\":" +
                (m_riskSnapshot.fxCashComplete ? "true" : "false"));
        InvalidateRiskSnapshot(
            "IB_FX_CASH_BALANCE_OUT_OF_GENERATION_CHANGE");
    }
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
    m_rejectedFxCashObservationBalances.clear();
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
    m_rejectedFxCashObservationBalances.clear();
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
    else if (!initialAttestationMatches) {
        m_riskSnapshot.reasonCode = "IB_FX_CASH_ATTESTED_BALANCE_MISMATCH";
        m_rejectedFxCashObservationBalances = m_pendingFxCashBalances;
        for (const auto& item : m_cfg.authoritativeCashFxContracts) {
            const auto expected = m_cfg.authoritativeCashFxStartupObservedBalances.find(item.first);
            const auto observed = m_pendingFxCashBalances.find(item.second.symbol);
            if (expected == m_cfg.authoritativeCashFxStartupObservedBalances.end() ||
                observed == m_pendingFxCashBalances.end()) continue;
            std::ostringstream diagnostic;
            diagnostic.precision(17);
            diagnostic << "\"instrument\":\"" << EscapeJson(item.first)
                       << "\",\"currency\":\"" << EscapeJson(item.second.symbol)
                       << "\",\"expected_balance\":" << expected->second
                       << ",\"observed_balance\":" << observed->second
                       << ",\"difference\":" << (observed->second - expected->second)
                       << ",\"connection_epoch\":" << m_connectionEpoch
                       << ",\"account_generation\":" << m_riskSnapshot.accountGeneration;
            EmitObsEvent("risk.fx_cash_attestation_mismatch", diagnostic.str());
        }
    }
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
        MarkCallbackConflict(m_positionsRefreshConflict);
        m_riskSnapshot.reasonCode = "IB_POSITION_VALUE_INVALID";
        return;
    }
    const std::pair<std::map<std::string, double>::iterator, bool> inserted =
        m_pendingPositionQuantities.insert(
            std::make_pair(event.key, event.number));
    const bool contractInserted = m_pendingPositionContracts.insert(
        std::make_pair(event.key, event.contract)).second;
    if (!inserted.second || !contractInserted) {
        MarkCallbackConflict(m_positionsRefreshConflict);
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
        (m_recoveryAuditMinimumConnectionEpoch == 0 ||
         m_connectionEpoch >= m_recoveryAuditMinimumConnectionEpoch) &&
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
        if (m_cfg.account.empty()) return;
        if (outEvent.account.empty()) {
            MarkCallbackConflict(m_correlationRefreshConflict);
            m_correlationSnapshot.reasonCode =
                "IB_ACTIVE_ORDER_ACCOUNT_UNKNOWN";
            return;
        }
        // Another positively identified IB account is out of scope.
        if (outEvent.account != m_cfg.account) return;
        if (outEvent.id < 0 || outEvent.id > LONG_MAX) {
            MarkCallbackConflict(m_correlationRefreshConflict);
            m_correlationSnapshot.reasonCode = "IB_ACTIVE_ORDER_ID_INVALID";
            return;
        }
        if (outEvent.brokerClientId < 0) {
            MarkCallbackConflict(m_correlationRefreshConflict);
            m_correlationSnapshot.reasonCode = "IB_ACTIVE_ORDER_CLIENT_ID_UNKNOWN";
            return;
        }
        if (!acceptedBrokerOpenOrder) return;
        if (!NativeAndLiteProtectiveShapeConsistent(outEvent)) {
            MarkCallbackConflict(m_correlationRefreshConflict);
            m_correlationSnapshot.reasonCode=
                "IB_PROTECTIVE_NATIVE_LITE_SHAPE_CONFLICT";
            return;
        }
        const long orderId = static_cast<long>(outEvent.id);
        if (orderId == 0 && (outEvent.brokerClientId != 0 ||
                             outEvent.brokerPermId <= 0)) {
            MarkCallbackConflict(m_correlationRefreshConflict);
            m_correlationSnapshot.reasonCode =
                "IB_UNBOUND_MANUAL_PERMID_REQUIRED";
            return;
        }
        if (outEvent.brokerPermId > 0) {
            const auto nativeIdentity =
                std::make_pair(outEvent.brokerClientId, orderId);
            const auto perm = m_pendingActivePermIdOwners.emplace(
                outEvent.brokerPermId, nativeIdentity);
            if (!perm.second && perm.first->second != nativeIdentity) {
                MarkCallbackConflict(m_correlationRefreshConflict);
                m_correlationSnapshot.reasonCode =
                    "IB_ACTIVE_ORDER_PERMID_COLLISION";
                return;
            }
            if (orderId == 0)
                m_pendingUnboundManualPermIds.insert(outEvent.brokerPermId);
        } else {
            m_pendingMissingPermanentId = true;
        }
        // Observe Broker-native stop/OCA/parent metadata on the ORIGINAL
        // account-wide callback. Never infer protective survival from this
        // label alone, and never bind/change orders or create cancel authority.
        if (IsIbVisibleProtectiveCandidate(outEvent)) {
            if (outEvent.brokerPermId <= 0) {
                m_pendingProtectiveMetadataIncomplete = true;
            } else {
                const IBVisibleProtectiveOrderCandidate candidate =
                    MakeIbVisibleProtectiveCandidate(outEvent);
                const auto existing =
                    m_pendingProtectiveCandidatesByPermId.emplace(
                        outEvent.brokerPermId, candidate);
                if (!existing.second &&
                    !SameIbVisibleProtectiveCandidate(
                        existing.first->second, candidate)) {
                    MarkCallbackConflict(m_correlationRefreshConflict);
                    m_correlationSnapshot.reasonCode =
                        "IB_PROTECTIVE_PERMID_METADATA_CONFLICT";
                    return;
                }
            }
        }
        const auto identity = m_pendingActiveOrderClientIds.emplace(
            orderId, outEvent.brokerClientId);
        if (!identity.second && identity.first->second != outEvent.brokerClientId) {
            MarkCallbackConflict(m_correlationRefreshConflict);
            m_correlationSnapshot.reasonCode = "IB_ACTIVE_ORDER_CLIENT_ID_COLLISION";
            return;
        }
        m_pendingActiveOrderIds.insert(orderId);
        // Foreign H1 orderRefs cannot create an original-owner correlation.
        if (outEvent.brokerClientId != m_cfg.clientId) return;
        std::string correlationId;
        std::string decodeReason;
        if (DecodeVenueOrderRef(
                outEvent.order.orderRef, correlationId, decodeReason)) {
            const long orderId = static_cast<long>(outEvent.id);
            const auto inserted = m_pendingCorrelationOrderIds.insert(
                std::make_pair(correlationId, orderId));
            if (!inserted.second && inserted.first->second != orderId) {
                MarkCallbackConflict(m_correlationRefreshConflict);
                m_correlationSnapshot.reasonCode = "IB_CORRELATION_DUPLICATE_CONFLICT";
            }
        } else if (outEvent.order.orderRef.compare(0, 2, "H1") == 0) {
            MarkCallbackConflict(m_correlationRefreshConflict);
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
            m_correlationSnapshot.activeOrderClientIdsByOrderId =
                m_pendingActiveOrderClientIds;
            m_correlationSnapshot.activeOrderByPermanentId =
                m_pendingActivePermIdOwners;
            m_correlationSnapshot.unboundManualPermanentIds =
                m_pendingUnboundManualPermIds;
            m_correlationSnapshot.activeOrderMissingPermanentId =
                m_pendingMissingPermanentId;
            m_correlationSnapshot.visibleProtectiveCandidatesByPermId =
                m_pendingProtectiveCandidatesByPermId;
            m_correlationSnapshot.protectiveMetadataIncomplete =
                m_pendingProtectiveMetadataIncomplete;
        } else {
            m_correlationSnapshot.complete = false;
            if (m_correlationSnapshot.reasonCode.empty()) {
                m_correlationSnapshot.reasonCode =
                    "IB_CORRELATION_REFRESH_NOT_AUTHORITATIVE";
            }
            m_correlationSnapshot.activeOrderIdsByCorrelation.clear();
            m_correlationSnapshot.activeOrderIds.clear();
            m_correlationSnapshot.activeOrderClientIdsByOrderId.clear();
    m_correlationSnapshot.activeOrderByPermanentId.clear();
    m_correlationSnapshot.unboundManualPermanentIds.clear();
    m_correlationSnapshot.visibleProtectiveCandidatesByPermId.clear();
    m_correlationSnapshot.protectiveMetadataIncomplete = false;
    m_correlationSnapshot.activeOrderMissingPermanentId = false;
        }
        m_pendingCorrelationOrderIds.clear();
        m_pendingActiveOrderIds.clear();
        m_pendingActiveOrderClientIds.clear();
    m_pendingActivePermIdOwners.clear();
    m_pendingUnboundManualPermIds.clear();
    m_pendingProtectiveCandidatesByPermId.clear();
    m_pendingProtectiveMetadataIncomplete = false;
    m_pendingMissingPermanentId = false;
    }
}

bool HeptaIBGatewayAdapter::IsExactDuplicateNativeTerminalStatus(
    const IBEvent& event) const {
    // IB explicitly permits repeated native orderStatus callbacks. Only the
    // exact, already-published terminal evidence for our current send binding
    // is a no-op. Never use naked order ids, quantity tolerances or this path
    // to establish/reopen a recovery barrier. Economic extensions remain
    // conservative until they have their own complete identity comparison.
    if (!m_eventStreamAuthoritative || !event.value.empty() ||
        !event.economics.Absent() || !event.advancedOrderRejectJson.empty() ||
        !IsConsistentBoundOrderStatus(event) || !IsIbFinalStatus(event.key))
        return false;
    const auto& binding=m_liveTerminalBindings.at(static_cast<long>(event.id));
    if (!binding.hasNativeTerminalStatus ||
        !m_terminalCorrelationSnapshot.complete ||
        m_terminalCorrelationSnapshot.connectionEpoch != m_connectionEpoch)
        return false;
    const auto order=m_terminalCorrelationSnapshot.terminalOrderIdsByCorrelation.find(binding.correlationId);
    const auto status=m_terminalCorrelationSnapshot.terminalStatusesByCorrelation.find(binding.correlationId);
    if (order==m_terminalCorrelationSnapshot.terminalOrderIdsByCorrelation.end() ||
        status==m_terminalCorrelationSnapshot.terminalStatusesByCorrelation.end() ||
        order->second!=event.id || status->second!=event.key ||
        binding.executionQuantity!=event.number2)
        return false;
    const auto& original=binding.nativeTerminalStatus;
    return original.requestId==event.requestId && original.key==event.key &&
        original.number==event.number && original.number2==event.number2 &&
        original.number3==event.number3 && original.whyHeld==event.whyHeld &&
        original.marketCapPrice==event.marketCapPrice &&
        original.remainingQuantityKnown==event.remainingQuantityKnown;
}

bool HeptaIBGatewayAdapter::IsConsistentBoundOrderStatus(const IBEvent& event) const {
    const auto found = m_liveTerminalBindings.find(static_cast<long>(event.id));
    if (found == m_liveTerminalBindings.end()) return false;
    const LiveTerminalBinding& binding = found->second;
    // A wrapper-derived status may drive the economic lifecycle only after
    // its exact cumulative fill was validated from the broker's execution.
    // It remains synthetic and never supplies a native terminal ACK below.
    const bool supportedStatusSource = event.value.empty() ||
        (event.value == "execDetails" && event.requestId == -1 &&
         (event.key == "Filled" || event.key == "PartiallyFilled") &&
         binding.cashQuantity == 0.0 &&
         binding.executionQuantity > 0.0 &&
         event.number2 == binding.executionQuantity &&
         event.number == binding.executionAveragePrice &&
         event.number3 == binding.quantity - binding.executionQuantity);
    return event.type == IBEventType::OrderStatus && event.id > 0 &&
        event.id <= LONG_MAX && event.connectionEpoch != 0 &&
        event.connectionEpoch == m_connectionEpoch &&
        binding.connectionEpoch == m_connectionEpoch &&
        event.brokerClientId >= 0 && event.brokerClientId == m_cfg.clientId &&
        (event.account.empty() || event.account == m_cfg.account) &&
        supportedStatusSource &&
        std::isfinite(binding.quantity) && (binding.cashQuantity > 0.0 || binding.quantity > 0.0) &&
        std::isfinite(event.number2) && event.number2 >= 0.0 &&
        std::isfinite(event.number3) && event.number3 >= 0.0 &&
        std::isfinite(event.number2 + event.number3) &&
        (binding.cashQuantity > 0.0 ||
         (event.number2 <= binding.quantity && event.number3 <= binding.quantity &&
          event.number2 + event.number3 <= binding.quantity)) &&
        (event.number2 == 0.0 || HasEconomicFillEvidence(event)) &&
        (event.key != "Filled" ||
         (event.number3 == 0.0 && (binding.cashQuantity > 0.0 ?
          event.number2 > 0.0 : event.number2 == binding.quantity)));
}

bool HeptaIBGatewayAdapter::IsConsistentBoundLiveExecution(const IBEvent& event) const {
    const auto found = m_liveTerminalBindings.find(static_cast<long>(event.id));
    if (found == m_liveTerminalBindings.end()) return false;
    const LiveTerminalBinding& binding = found->second;
    std::string correlation, reason;
    const std::string side = event.value == "BOT" ? "BUY" :
        (event.value == "SLD" ? "SELL" : event.value);
    const std::string expectedInstrument =
        BuildIBAuthoritativeInstrumentIdentity(binding.contract, "");
    return event.type == IBEventType::ExecutionDetails &&
        event.requestId == -1 && event.id > 0 && event.id <= LONG_MAX &&
        event.connectionEpoch != 0 && event.connectionEpoch == m_connectionEpoch &&
        binding.connectionEpoch == m_connectionEpoch &&
        event.brokerClientId >= 0 && event.brokerClientId == m_cfg.clientId &&
        !event.key.empty() && !m_cfg.account.empty() && event.account == m_cfg.account &&
        side == binding.side && HasEconomicFillEvidence(event) &&
        std::isfinite(binding.quantity) && (binding.cashQuantity > 0.0 || binding.quantity > 0.0) &&
        (binding.cashQuantity > 0.0 || event.number2 <= binding.quantity) &&
        MatchesSubmittedIBContract(binding.contract, event.contract) &&
        !expectedInstrument.empty() &&
        DecodeVenueOrderRef(event.order.orderRef, correlation, reason) &&
        correlation == binding.correlationId;
}

void HeptaIBGatewayAdapter::ApplyLiveTerminalEvidence(const IBEvent& event) {
    const bool cashSizeParseRejected = IsIbOrderSizeParseRejection320(event);
    if ((event.type != IBEventType::ExecutionDetails &&
         event.type != IBEventType::OrderStatus && !cashSizeParseRejected) ||
        event.connectionEpoch == 0 || event.connectionEpoch != m_connectionEpoch ||
        (!cashSizeParseRejected &&
         (event.brokerClientId < 0 || event.brokerClientId != m_cfg.clientId)) ||
        event.id <= 0 || event.id > LONG_MAX || !m_eventStreamAuthoritative)
        return;
    const long orderId = static_cast<long>(event.id);
    const auto found = m_liveTerminalBindings.find(orderId);
    if (found == m_liveTerminalBindings.end()) return;
    LiveTerminalBinding& binding = found->second;
    if (binding.connectionEpoch != m_connectionEpoch ||
        !std::isfinite(binding.quantity) || (binding.cashQuantity == 0.0 && binding.quantity <= 0.0) ||
        !std::isfinite(event.number2) || event.number2 < 0.0 ||
        (binding.cashQuantity == 0.0 && event.number2 > binding.quantity))
        return;
    if (cashSizeParseRejected) {
        // The exact current-order binding supplies the identity omitted by the
        // generic IB error callback. This error occurs before venue acceptance,
        // so there is no economic fill to synthesize.
        binding.terminalStatus = "Rejected";
        binding.terminalFilledQuantity = 0.0;
    } else if (event.type == IBEventType::ExecutionDetails) {
        if (!IsConsistentBoundLiveExecution(event)) return;
        // Cumulative execution quantity is monotonic. Duplicate callbacks do
        // not create a new terminal generation or reopen a completed proof.
        if (event.number2 >= binding.executionQuantity) {
            binding.executionQuantity = event.number2;
            binding.executionAveragePrice = event.number;
        }
    } else {
        if (event.value == "execDetails" ||
            !IsConsistentBoundOrderStatus(event) || !IsIbFinalStatus(event.key))
            return;
        binding.hasNativeTerminalStatus = event.value.empty() &&
            event.economics.Absent() && event.advancedOrderRejectJson.empty() &&
            std::isfinite(event.number) && std::isfinite(event.marketCapPrice);
        if (binding.hasNativeTerminalStatus) binding.nativeTerminalStatus = event;
        binding.terminalStatus = event.key;
        binding.terminalFilledQuantity = event.number2;
    }
    PublishLiveTerminalEvidence(orderId);
}

void HeptaIBGatewayAdapter::PublishLiveTerminalEvidence(long orderId) {
    const auto found = m_liveTerminalBindings.find(orderId);
    if (found == m_liveTerminalBindings.end() ||
        !m_terminalCorrelationSnapshot.complete ||
        m_completedOrdersRefreshPending || m_executionsRefreshPending ||
        !m_eventStreamAuthoritative || m_connectionEpoch == 0 ||
        m_terminalCorrelationSnapshot.connectionEpoch != m_connectionEpoch ||
        found->second.connectionEpoch != m_connectionEpoch)
        return;
    LiveTerminalBinding& binding = found->second;
    const bool executed = binding.executionQuantity > 0.0;
    const bool terminal = !binding.terminalStatus.empty() &&
        binding.terminalFilledQuantity == binding.executionQuantity &&
        (binding.terminalStatus != "Filled" ||
         (binding.cashQuantity > 0.0 ? binding.executionQuantity > 0.0 :
          binding.executionQuantity == binding.quantity));
    if (!executed && !terminal) return;
    const auto existing = m_terminalCorrelationSnapshot.
        terminalOrderIdsByCorrelation.find(binding.correlationId);
    if (terminal) {
        bool conflict = existing != m_terminalCorrelationSnapshot.
            terminalOrderIdsByCorrelation.end() && existing->second != orderId;
        if (conflict) {
            InvalidateTerminalCorrelationSnapshot("IB_LIVE_TERMINAL_CORRELATION_CONFLICT");
            return;
        }
    }
    const auto priorStatus = m_terminalCorrelationSnapshot.
        terminalStatusesByCorrelation.find(binding.correlationId);
    const bool changed = (executed &&
        (m_terminalCorrelationSnapshot.executionCorrelations.count(binding.correlationId) == 0 ||
         binding.executionQuantity > binding.publishedExecutionQuantity)) ||
        (terminal && (existing == m_terminalCorrelationSnapshot.terminalOrderIdsByCorrelation.end() ||
         priorStatus == m_terminalCorrelationSnapshot.terminalStatusesByCorrelation.end() ||
         priorStatus->second != binding.terminalStatus));
    if (!changed) return;
    if (m_terminalCorrelationGeneration >=
        static_cast<std::uint64_t>(std::numeric_limits<int>::max())) {
        InvalidateTerminalCorrelationSnapshot("IB_TERMINAL_CORRELATION_GENERATION_EXHAUSTED");
        return;
    }
    ++m_terminalCorrelationGeneration;
    m_terminalCorrelationSnapshot.generation = m_terminalCorrelationGeneration;
    m_terminalCorrelationSnapshot.exposureGeneration = m_exposureGeneration;
    if (executed) {
        m_terminalCorrelationSnapshot.executionCorrelations.insert(binding.correlationId);
        m_terminalCorrelationSnapshot.executionOrderIds.insert(orderId);
        binding.publishedExecutionQuantity = binding.executionQuantity;
    }
    if (terminal) {
        m_terminalCorrelationSnapshot.terminalOrderIdsByCorrelation[binding.correlationId] = orderId;
        m_terminalCorrelationSnapshot.terminalStatusesByCorrelation[binding.correlationId] = binding.terminalStatus;
    }
}

void HeptaIBGatewayAdapter::ApplyTerminalCorrelationEvent(const IBEvent& outEvent) {
    ApplyLiveTerminalEvidence(outEvent);
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
                MarkCallbackConflict(m_terminalCorrelationRefreshConflict);
                m_terminalCorrelationSnapshot.reasonCode = orderId < 0 ?
                    "IB_TERMINAL_ORDER_ID_INVALID" :
                    "IB_TERMINAL_ORDER_STATUS_NOT_FINAL";
            } else {
                const auto byCorrelation =
                    m_pendingTerminalOrderIds.insert({correlationId, orderId});
                if (!byCorrelation.second &&
                    byCorrelation.first->second != orderId) {
                    MarkCallbackConflict(m_terminalCorrelationRefreshConflict);
                    m_terminalCorrelationSnapshot.reasonCode =
                        "IB_TERMINAL_CORRELATION_DUPLICATE_CONFLICT";
                } else {
                    // Numeric IB order ids are connection/client scoped and may
                    // repeat after restart. Correlation is the durable identity.
                    m_pendingTerminalStatuses[correlationId] = outEvent.key;
                }
            }
        } else if (outEvent.order.orderRef.compare(0, 2, "H1") == 0) {
            MarkCallbackConflict(m_terminalCorrelationRefreshConflict);
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
            outEvent.id > 0 && HasEconomicFillEvidence(outEvent)) {
            std::string correlationId;
            std::string decodeReason;
            if (DecodeVenueOrderRef(outEvent.order.orderRef, correlationId, decodeReason)) {
                if (outEvent.brokerClientId != m_cfg.clientId || outEvent.key.empty()) {
                    MarkCallbackConflict(m_terminalCorrelationRefreshConflict);
                    m_terminalCorrelationSnapshot.reasonCode =
                        outEvent.brokerClientId != m_cfg.clientId ?
                        "IB_TERMINAL_EXECUTION_CLIENT_MISMATCH" :
                        "IB_TERMINAL_EXECUTION_ID_REQUIRED";
                } else {
                    const auto id=m_pendingExecutionIdsByCorrelation.emplace(correlationId, static_cast<long>(outEvent.id));
                    if (!id.second && id.first->second!=outEvent.id) {
                        MarkCallbackConflict(m_terminalCorrelationRefreshConflict);
                        m_terminalCorrelationSnapshot.reasonCode="IB_TERMINAL_EXECUTION_ORDER_ID_CONFLICT";
                    } else m_pendingExecutionCorrelations.insert(correlationId);
                }
            } else if (outEvent.order.orderRef.compare(0, 2, "H1") == 0) {
                MarkCallbackConflict(m_terminalCorrelationRefreshConflict);
                m_terminalCorrelationSnapshot.reasonCode = decodeReason;
            }
        }
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
