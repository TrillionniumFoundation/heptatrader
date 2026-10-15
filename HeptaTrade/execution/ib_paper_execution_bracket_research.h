#pragma once
// Shadow-only group contract. Deliberately NOT wired to IB API/Execution sender.
// Official IBKR bracket: untransmitted parent followed by transmitting STP
// child. This module never calls placeOrder, stop, cancel or flatten.
#include "ib_paper_execution_stop_research.h"
#include <cctype>
#include <cmath>
#include <cstdint>
#include <limits>
#include <string>
#include <utility>

namespace hepta_execution_bracket_research {
using hepta_execution_stop_research::Refusal;

struct PreEntrySnapshot final {
    std::string account;
    std::string owner;
    std::string originalCommandKey;
    std::string executionDomain;
    long contractId = 0;
    int clientId = -1;
    long reservedParentOrderId = 0;
    long reservedChildOrderId = 0;
    std::uint64_t connectionEpoch = 0;
    std::uint64_t positionConnectionEpoch = 0;
    std::uint64_t openOrdersConnectionEpoch = 0;
    std::uint64_t originalPositionGeneration = 0;
    std::uint64_t observedAtMs = 0;
    std::uint64_t validUntilMs = 0;
    bool exactAccountOwnerAndContract = false;
    bool brokerIdsReservedWithinExactClient = false;
    bool coordinatorExclusiveEntryOwner = false;
    bool riskSnapshotComplete = false;
    bool openOrdersComplete = false;
    bool positionFlat = false;
    bool noUnresolvedOrders = false;
    std::uint64_t brokerActiveOrderCount = 0;
    double brokerPosition = 0.0;
    double quantity = 0.0;
    double bid = 0.0;
    double ask = 0.0;
    double minTick = 0.0;
    std::string action;
};

struct BrokerWireShadow final {
    OrderIntent intent;
    long orderId = 0;
    long parentId = 0;
    bool transmit = false;
    std::string tif;
    // No guarantee parent+child are server-atomic across disconnect.
    bool sendAuthority = false;
};

struct BracketShadowPlan final {
    BrokerWireShadow parent;
    BrokerWireShadow stop;
    std::string account;
    std::string owner;
    std::string originalCommandKey;
    long contractId = 0;
    int clientId = -1;
    std::uint64_t connectionEpoch = 0;
    std::uint64_t originalPositionGeneration = 0;
    bool maintenanceAuthorized = false;
    bool actualBrokerProtectionProven = false;
    bool ordinaryExitCompatible = false;
};

inline BracketShadowPlan ProposeUnsentBracket(
    const PreEntrySnapshot& s, std::uint64_t nowMs)
{
    if (s.executionDomain != "PAPER" ||
        s.account.size() < 3 || s.account.compare(0, 2, "DU") != 0 ||
        s.owner.empty() || s.originalCommandKey.empty() ||
        s.originalCommandKey.size() > 80 ||
        !std::all_of(s.originalCommandKey.begin(), s.originalCommandKey.end(),
            [](unsigned char c) { return std::isalnum(c) || c=='-' || c=='_' || c=='.'; }) ||
        s.contractId != 12087792)
        throw Refusal("BRACKET_EXACT_PAPER_SCOPE_REQUIRED");
    if (!s.exactAccountOwnerAndContract || !s.brokerIdsReservedWithinExactClient ||
        !s.coordinatorExclusiveEntryOwner || !s.riskSnapshotComplete ||
        !s.openOrdersComplete || !s.positionFlat || !s.noUnresolvedOrders ||
        s.brokerActiveOrderCount != 0 || s.brokerPosition != 0.0)
        throw Refusal("BRACKET_OWNER_FLAT_FENCE_MISSING");
    if (s.connectionEpoch == 0 ||
        s.connectionEpoch != s.positionConnectionEpoch ||
        s.connectionEpoch != s.openOrdersConnectionEpoch ||
        s.originalPositionGeneration == 0 ||
        s.observedAtMs == 0 || s.observedAtMs > nowMs ||
        s.validUntilMs < nowMs || s.validUntilMs < s.observedAtMs ||
        s.validUntilMs - s.observedAtMs > 3000)
        throw Refusal("BRACKET_BROKER_EPOCH_OR_QUOTE_STALE");
    if (s.clientId < 1 || s.reservedParentOrderId < 1 ||
        s.reservedParentOrderId >= std::numeric_limits<std::int32_t>::max() - 1 ||
        s.reservedChildOrderId != s.reservedParentOrderId + 1)
        throw Refusal("BRACKET_BROKER_ORDER_RESERVATION_INVALID");
    if (!std::isfinite(s.quantity) || s.quantity < 1.0 ||
        s.quantity > 100000.0 || s.quantity != std::floor(s.quantity) ||
        !std::isfinite(s.bid) || !std::isfinite(s.ask) ||
        s.bid <= 0.0 || s.ask < s.bid ||
        !std::isfinite(s.minTick) || s.minTick <= 0.0 || s.minTick > 0.001 ||
        (s.action != "BUY" && s.action != "SELL"))
        throw Refusal("BRACKET_FX_QUANTITY_QUOTE_GRID_INVALID");
    // Broker ContractDetails conId=12087792 and MarketRule 3188 confirm
    // exactly 0.00005 tick on current PAPER IDEALPRO EUR/USD.
    if (s.minTick != 0.00005)
        throw Refusal("BRACKET_CONTRACT_MARKET_RULE_CHANGED");
    const bool buy = s.action == "BUY";
    const double mark = buy ? s.ask : s.bid;
    // Preserve the resident EUR/USD $100k reference-notional entry bound.
    // The actual Execution final send must re-evaluate with its own fresh
    // broker quote; this shadow proposal cannot authorize a real order.
    if (s.quantity * mark > 100000.0)
        throw Refusal("BRACKET_ORIGINAL_NOTIONAL_EXCEEDED");
    const double wantedStop = mark * (buy ? .998 : 1.002);
    const double scaled = wantedStop / s.minTick;
    if (!std::isfinite(scaled) || scaled <= 0.0 || scaled > 1.e12)
        throw Refusal("BRACKET_STOP_NONREPRESENTABLE");
    const double stop = (buy ? std::ceil(scaled - 1.e-9) :
                         std::floor(scaled + 1.e-9)) * s.minTick;
    if (!(stop > 0.0) ||
        (buy && stop >= s.bid - s.minTick) ||
        (!buy && stop <= s.ask + s.minTick))
        throw Refusal("BRACKET_STOP_ALREADY_NEAR_OR_CROSSED");

    BracketShadowPlan p;
    p.account = s.account;
    p.owner = s.owner;
    p.originalCommandKey = s.originalCommandKey;
    p.contractId = s.contractId;
    p.clientId = s.clientId;
    p.connectionEpoch = s.connectionEpoch;
    p.originalPositionGeneration = s.originalPositionGeneration;
    p.parent.orderId = s.reservedParentOrderId;
    p.parent.parentId = 0;
    p.parent.transmit = false;
    p.parent.tif = "DAY";
    p.parent.intent.action = s.action;
    p.parent.intent.orderType = "MKT";
    p.parent.intent.totalQuantity = s.quantity;
    p.parent.intent.orderRef = "HEPTA-PARENT-" + s.originalCommandKey;
    p.stop.orderId = s.reservedChildOrderId;
    p.stop.parentId = p.parent.orderId;
    p.stop.transmit = true;
    p.stop.tif = "GTC";
    p.stop.intent.action = buy ? "SELL" : "BUY";
    p.stop.intent.orderType = "STP";
    p.stop.intent.totalQuantity = s.quantity;
    p.stop.intent.auxPrice = stop;
    p.stop.intent.orderRef = "HEPTA-STOP-" + s.originalCommandKey;
    return p;
}

enum class BracketPhase {
    NO_INTENT, DURABLE_INTENT, PARENT_UNTRANSMITTED,
    CHILD_RELEASE_UNKNOWN, BROKER_PAIR_ATTESTED,
    RECONNECT_UNCONFIRMED, PROTECTION_BROKEN
};

// Both records are verified against the same original command & broker ids.
// This is NOT an actual network proof, and paired acceptance does not prove
// future stop triggering or race-free ordinary exit.
struct BrokerBracketWitness final {
    std::string account;
    std::string owner;
    std::string originalCommandKey;
    std::string parentRef;
    std::string stopRef;
    std::string parentStatus;
    std::string stopStatus;
    std::string parentAction;
    std::string stopAction;
    long parentOrderId = 0;
    long childOrderId = 0;
    long childParentId = 0;
    long parentPermId = 0;
    long stopPermId = 0;
    std::string originalParentExecutionId;
    bool originalParentExecutionProven = false;
    bool nativeChildLinkedBeforeParentFill = false;
    int clientId = -1;
    long contractId = 0;
    std::uint64_t connectionEpoch = 0;
    std::uint64_t activeOrderConnectionEpoch = 0;
    std::uint64_t positionConnectionEpoch = 0;
    bool activeOrderInventoryComplete = false;
    bool brokerSourceAttested = false;
    bool stopTriggerMethodAttested = false;
    double parentQuantity = 0.0;
    double stopQuantity = 0.0;
    double stopRemaining = 0.0;
    double positionQuantity = 0.0;
    double stopPrice = 0.0;
    std::string orderType;
    std::string timeInForce;
};

inline bool MatchesPair(
    const BracketShadowPlan& p, const BrokerBracketWitness& w,
    std::uint64_t expectedEpoch)
{
    return p.connectionEpoch <= expectedEpoch &&
        w.connectionEpoch == expectedEpoch &&
        w.positionConnectionEpoch == expectedEpoch &&
        w.activeOrderConnectionEpoch == expectedEpoch &&
        w.account == p.account && w.owner == p.owner &&
        w.originalCommandKey == p.originalCommandKey &&
        w.parentRef == p.parent.intent.orderRef &&
        w.stopRef == p.stop.intent.orderRef &&
        w.parentOrderId == p.parent.orderId &&
        w.childOrderId == p.stop.orderId &&
        w.childParentId == p.parent.orderId &&
        w.parentPermId > 0 && w.stopPermId > 0 &&
        w.parentPermId != w.stopPermId &&
        !w.originalParentExecutionId.empty() &&
        w.originalParentExecutionProven &&
        w.nativeChildLinkedBeforeParentFill &&
        w.clientId == p.clientId && w.contractId == p.contractId &&
        w.parentStatus == "Filled" && w.stopStatus == "Submitted" &&
        w.parentAction == p.parent.intent.action &&
        w.stopAction == p.stop.intent.action &&
        w.parentQuantity == p.parent.intent.totalQuantity &&
        w.stopQuantity == p.stop.intent.totalQuantity &&
        w.stopRemaining == p.stop.intent.totalQuantity &&
        w.positionQuantity == (p.parent.intent.action == "BUY" ?
            p.parent.intent.totalQuantity : -p.parent.intent.totalQuantity) &&
        w.stopPrice == p.stop.intent.auxPrice &&
        w.orderType == "STP" && w.timeInForce == "GTC" &&
        w.brokerSourceAttested && w.activeOrderInventoryComplete &&
        w.stopTriggerMethodAttested;
}

// A synthetic durable-intent/recovery contract. There is NO dispatch method.
// Once child release is unknown, do not re-use those order IDs to place a
// replacement. A later broker snapshot must prove the same exact order group.
class ShadowBracketSequence final {
public:
    explicit ShadowBracketSequence(BracketShadowPlan p) : m_plan(std::move(p)) {}
    bool DurablePrepare(const std::string& command, bool omsProof) {
        if (m_phase != BracketPhase::NO_INTENT || !omsProof ||
            command != m_plan.originalCommandKey)
            return false;
        m_phase = BracketPhase::DURABLE_INTENT;
        return true;
    }
    bool ParentHeld(const std::string& command, bool twsNotTransmitted) {
        if (command != m_plan.originalCommandKey || !twsNotTransmitted ||
            m_phase != BracketPhase::DURABLE_INTENT)
            return false;
        m_phase = BracketPhase::PARENT_UNTRANSMITTED;
        return true;
    }
    bool ChildReleaseMayHaveBeenSent(const std::string& command) {
        if (command != m_plan.originalCommandKey ||
            m_phase != BracketPhase::PARENT_UNTRANSMITTED)
            return false;
        m_phase = BracketPhase::CHILD_RELEASE_UNKNOWN;
        return true;
    }
    bool AttestPair(const BrokerBracketWitness& witness) {
        if (m_phase != BracketPhase::CHILD_RELEASE_UNKNOWN ||
            !MatchesPair(m_plan, witness, m_plan.connectionEpoch))
            return false;
        m_parentPermId = witness.parentPermId;
        m_stopPermId = witness.stopPermId;
        m_phase = BracketPhase::BROKER_PAIR_ATTESTED;
        return true;
    }
    void OnBrokerDisconnectOrExecutionRestart() {
        if (m_phase != BracketPhase::NO_INTENT &&
            m_phase != BracketPhase::PROTECTION_BROKEN)
            m_phase = BracketPhase::RECONNECT_UNCONFIRMED;
    }
    bool ReattestPair(const BrokerBracketWitness& witness) {
        if (m_phase != BracketPhase::RECONNECT_UNCONFIRMED ||
            m_stopPermId <= 0 || m_parentPermId <= 0 ||
            witness.parentPermId != m_parentPermId ||
            witness.stopPermId != m_stopPermId ||
            witness.connectionEpoch <= m_plan.connectionEpoch ||
            !MatchesPair(m_plan, witness, witness.connectionEpoch))
            return false;
        m_plan.connectionEpoch = witness.connectionEpoch;
        m_phase = BracketPhase::BROKER_PAIR_ATTESTED;
        return true;
    }
    void OnPartialFillOrManualCloseWithoutOca() {
        m_phase = BracketPhase::PROTECTION_BROKEN;
    }
    BracketPhase Phase() const { return m_phase; }
    // Even a simulated correctly-attested pair cannot prove a safe
    // simultaneous manual close, gap fill, or cross-service shutdown.
    bool MaySendAnotherOrder() const { return false; }
    bool MayOrdinaryExitWithoutOca() const { return false; }
    bool MayStopDriver() const { return false; }
    bool IsLiveProtectionQualified() const { return false; }
private:
    BracketShadowPlan m_plan;
    BracketPhase m_phase = BracketPhase::NO_INTENT;
    long m_parentPermId = 0;
    long m_stopPermId = 0;
};
} // namespace hepta_execution_bracket_research
