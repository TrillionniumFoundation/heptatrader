#pragma once
// Issue #331: non-dispatching, experimental IB PAPER stop-order contract.
// Deliberately NO socket, transport, command sender or maintenance permission.
// Must not be installed/activated as live protection without independent
// Execution-owner fencing, durable OMS integration and PAPER broker validation.
#include "trading_contract.h"
#include <algorithm>
#include <cmath>
#include <cctype>
#include <cstdint>
#include <stdexcept>
#include <string>
#include <utility>

namespace hepta_execution_stop_research {
struct Refusal final : std::runtime_error {
    explicit Refusal(const char* why) : std::runtime_error(why) {}
};

struct BrokerExposure final {
    std::string account;
    std::string owner;
    std::string originalEntryExecutionId;
    std::string executionDomain;
    long exactBrokerContractId = 0;
    std::uint64_t connectionEpoch = 0;
    std::uint64_t positionConnectionEpoch = 0;
    std::uint64_t activeOrdersConnectionEpoch = 0;
    std::uint64_t positionGeneration = 0;
    std::uint64_t activeOrdersGeneration = 0;
    std::uint64_t observedAtMs = 0;
    std::uint64_t validUntilMs = 0;
    bool accountAndPositionAttested = false;
    bool activeOrdersComplete = false;
    bool originalEntryBrokerFillAttested = false;
    bool noNewEntryFenceAcknowledged = false;
    bool unresolvedOriginalCommand = true;
    std::uint64_t activeOrderCount = 0;
    double brokerQuantity = 0.0;
    double originalDriverQuantity = 0.0;
    double brokerAverageEntry = 0.0;
    double brokerBid = 0.0;
    double brokerAsk = 0.0;
    double minTick = 0.0;
};

struct StopProposal final {
    InstrumentRef contract;
    OrderIntent order;
    std::string account;
    std::string owner;
    std::string originalEntryExecutionId;
    long exactBrokerContractId = 0;
    std::string timeInForce;
    std::uint64_t connectionEpoch = 0;
    std::uint64_t positionGeneration = 0;
    std::uint64_t activeOrdersGeneration = 0;
    // This proposal is NOT a broker-side reduce-only contract. CASH STOP can
    // open a new reverse position if the original is manually closed.
    bool brokerNativeReduceOnlyProven = false;
    bool sendAuthority = false;
    bool maintenanceAuthorized = false;
};

// Requires all authoritative facts at the SAME broker epoch, plus an already
// acknowledged owner entry fence; cannot race an unfenced strategy position.
// Quote freshness and stop non-immediate trigger are explicit.
inline StopProposal ProposeUnsentFxStop(
    const BrokerExposure& s, std::uint64_t nowMs)
{
    if (s.executionDomain != "PAPER" || s.account.size() < 3 ||
        s.account.compare(0, 2, "DU") != 0 || s.owner.empty() ||
        s.originalEntryExecutionId.empty() ||
        s.originalEntryExecutionId.size() > 96 ||
        !std::all_of(s.originalEntryExecutionId.begin(),
                     s.originalEntryExecutionId.end(), [](unsigned char c) {
            return std::isalnum(c) || c == '-' || c == '_' || c == '.';
        }) ||
        s.exactBrokerContractId != 12087792)
        throw Refusal("PAPER_EXACT_ACCOUNT_OWNER_REQUIRED");
    if (!s.accountAndPositionAttested || !s.activeOrdersComplete ||
        !s.originalEntryBrokerFillAttested || !s.noNewEntryFenceAcknowledged ||
        s.unresolvedOriginalCommand || s.activeOrderCount != 0)
        throw Refusal("BROKER_FENCE_POSITION_ORDERS_NOT_ATTESTED");
    if (s.connectionEpoch == 0 ||
        s.positionConnectionEpoch != s.connectionEpoch ||
        s.activeOrdersConnectionEpoch != s.connectionEpoch ||
        s.positionGeneration == 0 ||
        s.activeOrdersGeneration == 0 ||
        s.observedAtMs == 0 || s.observedAtMs > nowMs ||
        s.validUntilMs < nowMs || s.validUntilMs < s.observedAtMs ||
        s.validUntilMs - s.observedAtMs > 3000)
        throw Refusal("BROKER_GENERATION_OR_FRESHNESS_UNKNOWN");
    auto positive = [](double n) { return std::isfinite(n) && n > 0.0; };
    if (!std::isfinite(s.brokerQuantity) ||
        s.brokerQuantity == 0.0 ||
        s.brokerQuantity != s.originalDriverQuantity ||
        !positive(s.brokerAverageEntry) || !positive(s.minTick) ||
        s.minTick > 0.001 || !positive(s.brokerBid) ||
        !positive(s.brokerAsk) || s.brokerAsk < s.brokerBid)
        throw Refusal("EXACT_FX_FILL_QUOTE_GRID_REQUIRED");
    // Exact PAPER conId 12087792 / market rule 3188 on 2026-10-15.
    // Refuse changed ticks until new live broker contractDetails and rule
    // evidence is independently requalified (research-only candidate).
    if (s.minTick != 0.00005)
        throw Refusal("FX_CONTRACT_MARKET_RULE_CHANGED");
    const double quantity = std::fabs(s.brokerQuantity);
    if (quantity > 100000.0 || std::floor(quantity) != quantity)
        throw Refusal("FX_QUANTITY_OUTSIDE_ORIGINAL_GRID");
    const bool shortPosition = s.brokerQuantity < 0.0;
    // For a long, upward rounding triggers earlier; for a short, downward
    // rounding triggers earlier. Grid must be verified with IB contractDetails
    // before treating these as executable native venue values.
    const double desired = s.brokerAverageEntry *
                           (shortPosition ? 1.002 : 0.998);
    const double scaled = desired / s.minTick;
    if (!std::isfinite(scaled) || scaled > 1.e12)
        throw Refusal("UNREPRESENTABLE_FX_STOP");
    const double stop = (shortPosition ?
        std::floor(scaled + 1.e-9) :
        std::ceil(scaled - 1.e-9)) * s.minTick;
    if (!positive(stop) ||
        (shortPosition && stop <= s.brokerAsk + s.minTick) ||
        (!shortPosition && stop >= s.brokerBid - s.minTick))
        throw Refusal("RISK_THRESHOLD_ALREADY_NEAR_OR_CROSSED");
    StopProposal result;
    result.contract.symbol = "EUR";
    result.contract.currency = "USD";
    result.contract.secType = "CASH";
    result.contract.exchange = "IDEALPRO";
    result.account = s.account;
    result.owner = s.owner;
    result.originalEntryExecutionId = s.originalEntryExecutionId;
    result.exactBrokerContractId = s.exactBrokerContractId;
    result.order.orderRef = "HEPTA-PROTECT-" + s.originalEntryExecutionId;
    result.connectionEpoch = s.connectionEpoch;
    result.positionGeneration = s.positionGeneration;
    result.activeOrdersGeneration = s.activeOrdersGeneration;
    result.timeInForce = "GTC";  // must be confirmed by IB PAPER API.
    result.order.action = shortPosition ? "BUY" : "SELL";
    result.order.orderType = "STP";
    result.order.totalQuantity = quantity;
    result.order.auxPrice = stop;
    result.order.lmtPrice = 0.0;
    return result;
}

// Broker order acceptance is NOT active risk protection until an independent
// broker snapshot binds a real permanent ID, client ID, exact quantity/order,
// account, order type, stop trigger and connection epoch.
struct BrokerStopWitness final {
    std::string account;
    std::string owner;
    std::string instrument;
    std::string status;
    std::string action;
    std::string orderType;
    std::string timeInForce;
    std::string orderRef;
    long exactBrokerContractId = 0;
    long orderId = 0;
    long permanentId = 0;
    int brokerClientId = -1;
    std::uint64_t connectionEpoch = 0;
    std::uint64_t positionGeneration = 0;
    std::uint64_t activeOrdersGeneration = 0;
    bool globalActiveOrdersComplete = false;
    bool quoteAndTriggerMethodAttested = false;
    double quantity = 0.0;
    double auxPrice = 0.0;
    double filledQuantity = 0.0;
    double remainingQuantity = 0.0;
};

inline bool MatchesBrokerWorkingStop(
    const StopProposal& plan, const BrokerStopWitness& receipt,
    int expectedBrokerClientId)
{
    return expectedBrokerClientId >= 0 && receipt.brokerClientId == expectedBrokerClientId &&
        receipt.orderId > 0 && receipt.permanentId > 0 &&
        receipt.account == plan.account && receipt.owner == plan.owner &&
        receipt.instrument == "EUR.USD" && receipt.status == "Submitted" &&
        receipt.orderRef == plan.order.orderRef &&
        receipt.exactBrokerContractId == plan.exactBrokerContractId &&
        receipt.action == plan.order.action && receipt.orderType == "STP" &&
        receipt.timeInForce == plan.timeInForce &&
        receipt.quantity == plan.order.totalQuantity &&
        receipt.filledQuantity == 0.0 &&
        receipt.remainingQuantity == plan.order.totalQuantity &&
        receipt.auxPrice == plan.order.auxPrice &&
        receipt.connectionEpoch == plan.connectionEpoch &&
        receipt.positionGeneration == plan.positionGeneration &&
        receipt.activeOrdersGeneration >= plan.activeOrdersGeneration &&
        receipt.globalActiveOrdersComplete && receipt.quoteAndTriggerMethodAttested;
}

enum class StopEvidencePhase {
    NONE, INTENT_DURABLE, SEND_UNCERTAIN, BROKER_WORKING,
    RECONNECT_UNCONFIRMED, CANCELLED_OR_PARTIAL
};

// This class never sends orders. An UNKNOWN disposition cannot mint an order
// identity or invoke a second submission. Broker reconnect strips even a prior
// WORKING label until the actual broker reattests its permanent order identity.
class ExactStopReceiptSequence final {
public:
    explicit ExactStopReceiptSequence(StopProposal candidate) : m_plan(std::move(candidate)) {}
    StopEvidencePhase Phase() const { return m_phase; }
    const std::string& CommandKey() const { return m_commandKey; }
    bool RecordPrepared(std::string key) {
        if (key.empty() || m_phase != StopEvidencePhase::NONE) return false;
        m_commandKey = std::move(key);
        m_phase = StopEvidencePhase::INTENT_DURABLE;
        return true;
    }
    bool RecordPossiblySent(const std::string& key) {
        if (key != m_commandKey || m_phase != StopEvidencePhase::INTENT_DURABLE)
            return false;
        m_phase = StopEvidencePhase::SEND_UNCERTAIN;
        return true;
    }
    bool ObserveWorking(const std::string& key,
                        const BrokerStopWitness& witness, int brokerClientId) {
        if (key != m_commandKey ||
            m_phase != StopEvidencePhase::SEND_UNCERTAIN)
            return false;
        if (!MatchesBrokerWorkingStop(m_plan, witness, brokerClientId))
            return false;
        if (m_permanentId != 0 && m_permanentId != witness.permanentId)
            return false;
        m_permanentId = witness.permanentId;
        m_phase = StopEvidencePhase::BROKER_WORKING;
        return true;
    }
    // A replay of an old working receipt after reconnect is never enough.
    // Only a new complete broker epoch and the SAME permanent ID may rebind
    // the existing stop. No new order is transmitted by this method.
    bool ObserveReconnectedWorking(const std::string& key,
        const BrokerExposure& renewed,
        const BrokerStopWitness& broker,
        int expectedBrokerClientId, std::uint64_t nowMs) {
        if (m_phase != StopEvidencePhase::RECONNECT_UNCONFIRMED ||
            key != m_commandKey || m_permanentId <= 0 ||
            renewed.account != m_plan.account ||
            renewed.owner != m_plan.owner ||
            renewed.originalEntryExecutionId != m_plan.originalEntryExecutionId ||
            renewed.exactBrokerContractId != m_plan.exactBrokerContractId ||
            renewed.executionDomain != "PAPER" ||
            renewed.connectionEpoch <= m_plan.connectionEpoch ||
            renewed.positionConnectionEpoch != renewed.connectionEpoch ||
            renewed.activeOrdersConnectionEpoch != renewed.connectionEpoch ||
            renewed.positionGeneration == 0 ||
            renewed.activeOrdersGeneration == 0 ||
            renewed.observedAtMs == 0 || renewed.observedAtMs > nowMs ||
            renewed.validUntilMs < nowMs ||
            renewed.validUntilMs < renewed.observedAtMs ||
            renewed.validUntilMs - renewed.observedAtMs > 3000 ||
            !renewed.accountAndPositionAttested ||
            !renewed.activeOrdersComplete ||
            !renewed.noNewEntryFenceAcknowledged ||
            renewed.unresolvedOriginalCommand ||
            renewed.activeOrderCount != 1 ||
            !std::isfinite(renewed.brokerQuantity) ||
            renewed.brokerQuantity !=
                (m_plan.order.action == "BUY" ?
                    -m_plan.order.totalQuantity :
                    m_plan.order.totalQuantity))
            return false;
        StopProposal next = m_plan;
        next.connectionEpoch = renewed.connectionEpoch;
        next.positionGeneration = renewed.positionGeneration;
        next.activeOrdersGeneration = renewed.activeOrdersGeneration;
        if (broker.permanentId != m_permanentId ||
            !MatchesBrokerWorkingStop(next, broker, expectedBrokerClientId))
            return false;
        m_plan = std::move(next);
        m_phase = StopEvidencePhase::BROKER_WORKING;
        return true;
    }
    void OnBrokerReconnect() {
        if (m_phase != StopEvidencePhase::NONE &&
            m_phase != StopEvidencePhase::CANCELLED_OR_PARTIAL)
            m_phase = StopEvidencePhase::RECONNECT_UNCONFIRMED;
    }
    void OnCancelOrPartialFill() { m_phase = StopEvidencePhase::CANCELLED_OR_PARTIAL; }
    bool MaySubmitAgain() const { return false; }
    bool MayStopDriver() const { return false; }
private:
    StopProposal m_plan;
    StopEvidencePhase m_phase = StopEvidencePhase::NONE;
    std::string m_commandKey;
    long m_permanentId = 0;
};
}  // namespace hepta_execution_stop_research
