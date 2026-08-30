#include "ib_gateway_adapter.h"
#include <algorithm>
#include <chrono>
#include <cmath>
#include <cctype>
#include <cstring>
#include <ctime>
#include <exception>
#include <limits>
namespace {
// The low-level adapter does not receive the runtime's millisecond quote
// generation, so retain only a short epoch-local reference window.  The
// higher PAPER guard applies its exact configured quote TTL as a second fence.
const std::time_t kReferencePriceMaxAgeSec = 5;

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
std::string NormalizeIbOptionRight(std::string right) {
    std::transform(
        right.begin(), right.end(), right.begin(), [](unsigned char ch) {
            return ch >= static_cast<unsigned char>('a') &&
                    ch <= static_cast<unsigned char>('z') ?
                static_cast<char>(ch - static_cast<unsigned char>('a') +
                                  static_cast<unsigned char>('A')) :
                static_cast<char>(ch);
        });
    if (right == "CALL") return "C";
    if (right == "PUT") return "P";
    return right;
}
std::string OrderDetailJson(const std::string& detail) {
    return detail.empty() ? std::string() :
        "\"detail\":\"" + EscapeJson(detail) + "\"";
}
bool SameCashFxContract(const IBContractLite& l, const IBContractLite& r) {
    return l.symbol == r.symbol && l.secType == r.secType &&
        l.exchange == r.exchange && l.primaryExchange == r.primaryExchange &&
        l.currency == r.currency && l.lastTradeDateOrContractMonth ==
            r.lastTradeDateOrContractMonth && l.right == r.right &&
        std::memcmp(&l.strike, &r.strike, sizeof(l.strike)) == 0 &&
        l.multiplier == r.multiplier && l.tradingClass == r.tradingClass &&
        l.localSymbol == r.localSymbol;
}
}  // namespace
void HeptaIBGatewayAdapter::SetPrePlaceOrderSendCheck(
    const std::function<bool(
        const IBFinalOrderSendContext*, const IBContractLite&,
        const IBOrderLite&, std::string*)>& check) {
    std::lock_guard<std::recursive_mutex> lk(m_apiMutex);
    m_prePlaceOrderSendCheck = check;
}
bool HeptaIBGatewayAdapter::PlaceOrder(const IBContractLite& c, const IBOrderLite& o,
                                      long* outOrderId) {
    std::lock_guard<std::recursive_mutex> lk(m_apiMutex);
    if (!o.orderRef.empty()) {
        m_lastRejectReason = "IB_ORDER_REF_RESERVED";
        return false;
    }
    return PlaceOrderInternal(c, o, outOrderId);
}
bool HeptaIBGatewayAdapter::PlaceOrderCorrelated(
    const IBContractLite& c, const IBOrderLite& o,
    const std::string& venueCorrelationId, long* outOrderId,
    const IBFinalOrderSendContext* context) {
    std::lock_guard<std::recursive_mutex> lk(m_apiMutex);
    if (!o.orderRef.empty()) {
        m_lastRejectReason = "IB_ORDER_REF_RESERVED";
        return false;
    }
    IBOrderLite correlatedOrder = o;
    std::string reason;
    if (!EncodeVenueOrderRef(
            venueCorrelationId, correlatedOrder.orderRef, reason)) {
        m_lastRejectReason = reason;
        return false;
    }
    long orderId = -1;
    if (!PlaceOrderInternal(
        c, correlatedOrder, &orderId, context))
        return false;
    // A previously complete snapshot remains conservative between broker
    // refreshes: locally accepted correlated sends are immediately visible to
    // max-active-order and owner reconciliation. Cancel does not remove them;
    // only a terminal broker status may do that.
    MergeIncrementalActiveOrder(orderId, venueCorrelationId);
    if (outOrderId) *outOrderId = orderId;
    return true;
}
bool HeptaIBGatewayAdapter::RejectOrder(
    const IBContractLite& contract,
    const std::chrono::steady_clock::time_point& startedAt,
    const std::string& reason, const std::string& extraJson) {
    m_lastRejectReason = reason;
    std::string fields =
        "\"symbol\":\"" + EscapeJson(contract.symbol) +
        "\",\"reason\":\"" + EscapeJson(reason) + "\"";
    if (!extraJson.empty()) fields += "," + extraJson;
    EmitLatency(
        "order", "risk_gate",
        std::chrono::duration_cast<std::chrono::milliseconds>(
            std::chrono::steady_clock::now() - startedAt).count(),
        false, fields);
    return false;
}
bool HeptaIBGatewayAdapter::ValidateOrderRequest(
    const IBContractLite& contract, const IBOrderLite& order,
    std::string& reason, std::string& detail) const {
    if (contract.symbol.empty() || contract.secType.empty() ||
        contract.exchange.empty() || contract.currency.empty()) {
        reason = "RISK_CONTRACT_INVALID";
        detail = "missing contract fields";
        return false;
    }
    if (contract.secType == "OPT") {
        const std::string right = NormalizeIbOptionRight(contract.right);
        if (contract.lastTradeDateOrContractMonth.empty() ||
            (right != "C" && right != "P") ||
            !(contract.strike > 0.0) || !std::isfinite(contract.strike)) {
            reason = "RISK_CONTRACT_INVALID";
            detail = "missing option expiry/right/strike";
            return false;
        }
    }
    if (!(order.totalQuantity > 0.0) ||
        !std::isfinite(order.totalQuantity)) {
        reason = "RISK_QTY_INVALID";
        detail = "qty must be finite and > 0";
        return false;
    }
    if (order.orderType == "LMT" &&
        (!(order.lmtPrice > 0.0) || !std::isfinite(order.lmtPrice))) {
        reason = "RISK_PRICE_INVALID";
        detail = "limit price must be finite and > 0";
        return false;
    }
    return true;
}
void HeptaIBGatewayAdapter::ResetDailyRiskStateIfNeeded() {
    if (IsSameTradingDay()) return;
    const std::time_t now = std::time(nullptr);
    std::tm nowTm{};
#ifdef _WIN32
    localtime_s(&nowTm, &now);
#else
    localtime_r(&now, &nowTm);
#endif
    m_dayOfYear = nowTm.tm_yday;
    m_todayOrderCount = 0;
    m_consecutiveErrorCount = 0;
    m_errorFuseScore = 0;
    m_errorCodeCounts.clear();
    m_circuitBreakerTripped = false;
    m_circuitBreakerTripTs = 0;
}
bool HeptaIBGatewayAdapter::CircuitBreakerAllowsOrder(std::time_t nowTs) {
    if (!m_circuitBreakerTripped) return true;
    const bool cooldownElapsed =
        m_cfg.risk.circuitBreakerCooldownSec > 0 &&
        m_circuitBreakerTripTs > 0 &&
        nowTs - m_circuitBreakerTripTs >=
            m_cfg.risk.circuitBreakerCooldownSec;
    if (!cooldownElapsed) return false;
    m_circuitBreakerTripped = false;
    m_consecutiveErrorCount = 0;
    m_errorFuseScore = 0;
    m_errorCodeCounts.clear();
    m_circuitBreakerTripTs = 0;
    EmitObsEvent(
        "risk.circuit_breaker_recovered",
        "\"reason\":\"cooldown_elapsed\"");
    return true;
}

void HeptaIBGatewayAdapter::PruneOrderAttemptTimes() {
    const std::chrono::steady_clock::time_point now =
        std::chrono::steady_clock::now();
    const std::chrono::steady_clock::duration window =
        std::chrono::seconds(60);
    while (!m_orderAttemptTimes.empty() &&
           now - m_orderAttemptTimes.front() >= window)
        m_orderAttemptTimes.pop_front();
}

void HeptaIBGatewayAdapter::PopulateDeterministicRiskContext(
    const IBContractLite& contract, const IBOrderLite& order) {
    DeterministicRiskContext& risk = m_riskCtxScratch;
    risk = DeterministicRiskContext{};
    risk.action = order.action;
    risk.orderType = order.orderType;
    risk.quantity = order.totalQuantity;
    risk.submittedPrice = order.lmtPrice;
    risk.referencePrice = m_lastReferencePrice;
    risk.valuationPrice = order.orderType == "LMT" ?
        order.lmtPrice : m_lastReferencePrice;
    PruneOrderAttemptTimes();
    risk.ordersInLastMinute = m_orderAttemptTimes.size();
    risk.activeOrderCount = m_correlationSnapshot.activeOrderIds.size();
    risk.grossAbsolutePosition = m_riskSnapshot.grossAbsolutePosition;

    // Only consume generation-bound authoritative maps here.  The callback
    // convenience map (`m_symbolNetPosition`) survives reconnects and may
    // contain stale values, so using it could falsely prove a reduction in a
    // fresh broker epoch.  Broker position keys are normally CONID/CONTRACT
    // identities rather than the display symbol; use the adapter's canonical
    // resolver so options/futures cannot be collapsed by symbol.  An absent
    // key in a complete snapshot is the authoritative zero position.
    double current = 0.0;
    bool knownPosition = false;
    std::string positionReason;
    if (contract.secType == "CASH" &&
        !m_cfg.authoritativeCashFxContracts.empty()) {
        // Campaign-owned CASH exposure is keyed by the configured
        // instrument, not by the broker's base-currency callback key.
        for (std::map<std::string, InstrumentRef>::const_iterator it =
                 m_cfg.authoritativeCashFxContracts.begin();
             it != m_cfg.authoritativeCashFxContracts.end(); ++it) {
            if (!SameCashFxContract(it->second, contract)) continue;
            const bool fxReady = m_riskSnapshot.accountComplete &&
                m_riskSnapshot.fxCashComplete &&
                m_riskSnapshot.connectionEpoch == m_connectionEpoch &&
                m_riskSnapshot.fxCashGeneration != 0;
            if (fxReady)
                knownPosition = ResolveAuthoritativePositionQuantity(
                    it->first, it->second, current, positionReason);
            break;
        }
    } else {
        const bool positionsReady = m_riskSnapshot.positionsComplete &&
            m_riskSnapshot.connectionEpoch == m_connectionEpoch &&
            m_riskSnapshot.positionsGeneration != 0;
        if (positionsReady)
            knownPosition = ResolveAuthoritativePositionQuantity(
                // Do not use the display symbol as an exact map key: a
                // symbol collision across option/future series must be
                // resolved by the full authoritative contract identity.
                std::string(), contract, current, positionReason);
    }
    const double signedQuantity = order.action == "BUY" ?
        order.totalQuantity : (order.action == "SELL" ?
            -order.totalQuantity : 0.0);
    risk.netPosition = current;
    risk.projectedNetPosition = current + signedQuantity;

    const bool validGross = std::isfinite(risk.grossAbsolutePosition) &&
        risk.grossAbsolutePosition >= 0.0;
    if (knownPosition && validGross && std::isfinite(signedQuantity)) {
        risk.projectedGrossAbsolutePosition =
            risk.grossAbsolutePosition - std::fabs(current) +
            std::fabs(current + signedQuantity);
        risk.exposureReducing =
            std::fabs(current + signedQuantity) < std::fabs(current);
    } else {
        // Unknown position data is never treated as a free reduction.  Use a
        // conservative increase projection; the complete-snapshot gate below
        // rejects the order unless the authoritative state is available.
        risk.projectedGrossAbsolutePosition =
            validGross ? risk.grossAbsolutePosition + order.totalQuantity :
                std::numeric_limits<double>::quiet_NaN();
        risk.exposureReducing = false;
    }

    const std::time_t now = std::time(nullptr);
    const std::time_t referenceAge = now - m_lastReferencePriceTs;
    const bool referencePresent = std::isfinite(m_lastReferencePrice) &&
        m_lastReferencePrice > 0.0 && m_lastReferencePriceTs > 0 &&
        referenceAge >= 0 && referenceAge <= kReferencePriceMaxAgeSec;
    risk.quoteFresh = !m_cachedRiskLimits.requireFreshQuote || referencePresent;
    risk.portfolioSnapshotComplete =
        !m_cachedRiskLimits.requireCompleteSnapshot ||
        (knownPosition && m_eventStreamAuthoritative && m_riskSnapshot.complete &&
        m_riskSnapshot.connectionEpoch == m_connectionEpoch &&
        m_riskSnapshot.accountGeneration != 0 &&
        m_riskSnapshot.positionsGeneration != 0 &&
        m_correlationSnapshot.complete &&
        m_correlationSnapshot.connectionEpoch == m_connectionEpoch);
}
bool HeptaIBGatewayAdapter::RunFinalOrderSendCheck(
    const IBFinalOrderSendContext* context,
    const IBContractLite& contract, const IBOrderLite& order,
    std::string& reason) {
    if (!m_prePlaceOrderSendCheck) return true;
    bool allowed = false;
    try {
        allowed = m_prePlaceOrderSendCheck(
            context, contract, order, &reason);
    } catch (const std::exception& error) {
        reason = error.what();
    } catch (...) {
        reason = "final pre-send check threw";
    }
    if (!allowed && reason.empty())
        reason = "IB_PAPER_KILL_SWITCH_STATE_UNCERTAIN";
    return allowed;
}
bool HeptaIBGatewayAdapter::ResolveCashFxInstrumentForOrder(
    const IBFinalOrderSendContext* context,
    const IBContractLite& contract, std::string& instrument,
    std::string& reason) const {
    instrument = context ? context->instrument : std::string();
    if (!instrument.empty()) {
        const auto configured =
            m_cfg.authoritativeCashFxContracts.find(instrument);
        if (configured == m_cfg.authoritativeCashFxContracts.end() ||
            !SameCashFxContract(configured->second, contract)) {
            reason = "IB_PAPER_PLACE_CONTRACT_MISMATCH";
            return false;
        }
        return true;
    }
    for (std::map<std::string, InstrumentRef>::const_iterator it =
             m_cfg.authoritativeCashFxContracts.begin();
         it != m_cfg.authoritativeCashFxContracts.end(); ++it) {
        if (!SameCashFxContract(it->second, contract)) continue;
        if (!instrument.empty()) {
            reason = "IB_POST_FILL_BASELINE_AMBIGUOUS";
            return false;
        }
        instrument = it->first;
    }
    return true;
}

bool HeptaIBGatewayAdapter::BuildOrderRiskBaseline(
    const IBFinalOrderSendContext* context,
    const IBContractLite& contract, const IBOrderLite& order,
    IBOrderRiskBaseline& baseline, bool& hasBaseline,
    std::string& reason, std::string& detail) {
    hasBaseline = false;
    std::string instrument;
    const bool contextBound = context && !context->instrument.empty();
    // Generic exact-reduce adapters may omit PAPER FX mapping; ordinary sends
    // and mapped production reduce-only sends must bind the complete contract.
    const bool bindingRequiresContract = contextBound &&
        (!context->exactReduceOnly ||
         !m_cfg.authoritativeCashFxContracts.empty());
    if (bindingRequiresContract &&
        !ResolveCashFxInstrumentForOrder(
            context, contract, instrument, reason))
        return false;
    if (contract.secType != "CASH" ||
        m_cfg.authoritativeCashFxContracts.empty())
        return true;

    if (!bindingRequiresContract && !ResolveCashFxInstrumentForOrder(
            context, contract, instrument, reason))
        return false;
    double position = 0.0;
    const std::map<std::string, InstrumentRef>::const_iterator configured =
        m_cfg.authoritativeCashFxContracts.find(instrument);
    if (instrument.empty() ||
        configured == m_cfg.authoritativeCashFxContracts.end() ||
        !m_riskSnapshot.accountComplete ||
        !m_riskSnapshot.positionsComplete ||
        !m_riskSnapshot.fxCashComplete ||
        m_riskSnapshot.connectionEpoch == 0 ||
        m_riskSnapshot.positionsGeneration == 0 ||
        m_riskSnapshot.fxCashGeneration == 0 ||
        !ResolveAuthoritativePositionQuantity(
            instrument, configured->second, position, detail)) {
        reason = "IB_POST_FILL_BASELINE_UNAVAILABLE";
        return false;
    }
    baseline.instrument = instrument;
    baseline.side = order.action;
    baseline.positionQuantity = position;
    baseline.connectionEpoch = m_riskSnapshot.connectionEpoch;
    baseline.positionGeneration = m_riskSnapshot.positionsGeneration;
    baseline.fxCashGeneration = m_riskSnapshot.fxCashGeneration;
    hasBaseline = true;
    return true;
}

bool HeptaIBGatewayAdapter::SubmitValidatedOrder(
    long orderId, const IBContractLite& contract, const IBOrderLite& order,
    std::time_t nowTs, const IBOrderRiskBaseline* baseline,
    long* outOrderId,
    const std::chrono::steady_clock::time_point& startedAt) {
    if (!BeginBrokerMutation("IB_RECOVERY_AUDIT_PLACE_MUTATION"))
        return false;
    // Count every broker send attempt (including an API rejection) in the
    // rolling common-policy budget.  This is appended immediately before the
    // sole mutation call, after all gates have passed.
    PruneOrderAttemptTimes();
    m_orderAttemptTimes.push_back(std::chrono::steady_clock::now());
    const bool accepted = m_api->PlaceOrder(orderId, contract, order);
    if (accepted) {
        ++m_todayOrderCount;
        RememberLastOrder(contract, order, nowTs);
        if (outOrderId) *outOrderId = orderId;
        m_orderSubmitTs[orderId] = std::chrono::steady_clock::now();
        m_orderLifecycle.BeginLocalOrderGeneration(orderId);
        if (baseline) m_orderRiskBaselines[orderId] = *baseline;
        m_lastRejectReason.clear();
    } else {
        m_lastRejectReason = "IB_API_PLACE_REJECTED";
        m_orderLifecycle.Forget(orderId);
    }
    EmitLatency(
        "order", "api_place",
        std::chrono::duration_cast<std::chrono::milliseconds>(
            std::chrono::steady_clock::now() - startedAt).count(),
        accepted,
        "\"orderId\":" + std::to_string(orderId) +
        ",\"symbol\":\"" + EscapeJson(contract.symbol) +
        "\",\"qty\":" + std::to_string(order.totalQuantity) +
        ",\"type\":\"" + EscapeJson(order.orderType) + "\"");
    return accepted;
}

bool HeptaIBGatewayAdapter::PlaceOrderInternal(
    const IBContractLite& contract, const IBOrderLite& order,
    long* outOrderId, const IBFinalOrderSendContext* context) {
    const std::chrono::steady_clock::time_point startedAt =
        std::chrono::steady_clock::now();
    std::lock_guard<std::recursive_mutex> lock(m_apiMutex);
    if (!m_api || !m_connected) {
        m_lastRejectReason = "RISK_NOT_CONNECTED";
        EmitLatency(
            "order", "api_place",
            std::chrono::duration_cast<std::chrono::milliseconds>(
                std::chrono::steady_clock::now() - startedAt).count(),
            false,
            "\"symbol\":\"" + EscapeJson(contract.symbol) +
            "\",\"reason\":\"RISK_NOT_CONNECTED\"");
        return false;
    }

    std::string reason;
    std::string detail;
    if (!RunPreflightChecksDetailed(reason, detail))
        return RejectOrder(
            contract, startedAt,
            reason.empty() ? "RISK_PREFLIGHT_FAILED" : reason,
            OrderDetailJson(detail));
    if (!ValidateOrderRequest(contract, order, reason, detail))
        return RejectOrder(
            contract, startedAt, reason, OrderDetailJson(detail));

    ResetDailyRiskStateIfNeeded();
    const std::time_t nowTs = std::time(nullptr);
    if (!CircuitBreakerAllowsOrder(nowTs))
        return RejectOrder(
            contract, startedAt, "RISK_CIRCUIT_BREAKER_TRIPPED",
            std::string());
    if (IsDuplicateOrder(contract, order, nowTs))
        return RejectOrder(
            contract, startedAt, "RISK_DUPLICATE_ORDER", std::string());

    PopulateDeterministicRiskContext(contract, order);
    // `maxDailyOrders` is retained as the adapter-local calendar-day budget;
    // the common policy separately evaluates the rolling minute budget.
    // Proven strict reductions may still pass this entry budget, matching the
    // shared safe-exit rule.
    if (m_todayOrderCount >= m_cfg.risk.maxDailyOrders &&
        !m_riskCtxScratch.exposureReducing)
        return RejectOrder(
            contract, startedAt, "RISK_DAILY_ORDER_LIMIT", std::string());

    const DeterministicRiskDecision decision =
        DeterministicRiskPolicy::Evaluate(
            m_cachedRiskLimits, m_riskCtxScratch);
    if (!decision.allow)
        return RejectOrder(
            contract, startedAt, decision.reasonCode,
            OrderDetailJson(decision.detail));

    const long orderId = NextOrderId();
    IBOrderRiskBaseline baseline;
    bool hasBaseline = false;
    if (!BuildOrderRiskBaseline(
            context, contract, order, baseline, hasBaseline, reason, detail))
        return RejectOrder(
            contract, startedAt, reason, OrderDetailJson(detail));
    // This is intentionally the final operation before SubmitValidatedOrder,
    // whose first operation is the broker API PlaceOrder call. All adapter
    // preflight and authoritative baseline work is already complete.
    if (!RunFinalOrderSendCheck(context, contract, order, reason))
        return RejectOrder(
            contract, startedAt, reason,
            "\"detail\":\"final pre-send risk-increase check rejected "
            "broker send\"");
    return SubmitValidatedOrder(
        orderId, contract, order, nowTs,
        hasBaseline ? &baseline : nullptr, outOrderId, startedAt);
}
