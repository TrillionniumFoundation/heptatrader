#pragma once
// A production SDK type translation probe for Issue #331. NO placeOrder,
// cancelOrder, socket, queue, timer, session or trading authority is exposed.
// Compiled only by a native-IB-enabled core test; never installed with Gateway.
#include "../execution/ib_paper_execution_bracket_research.h"
#include "../execution/ib_bracket_h1_oms_reservation.h"
#include "Contract.h"
#include "Order.h"
#include "Decimal.h"
#include <cstdint>
#include <limits>
#include <cmath>
#include <utility>

namespace hepta_execution_bracket_native_research {

struct PreparedNativePair final {
    Contract contract;
    Order parent;
    Order stop;
    bool brokerSendEnabled = false;
    bool driverStopAuthorized = false;
    bool ordinaryExitWithoutOcaAllowed = false;
};

// Accept authoritative flat snapshot + frozen broker rule, not an untrusted
// serialized OrderIntent. The prototype itself performs original side/size/
// order-id/quote/owner checks before constructing SDK types.
// This helper only translates a plan already checked by the original
// Broker/Owner/quote policy; the public wrappers below enforce that policy.
inline PreparedNativePair NativeFromValidatedShadowPlan(
    const hepta_execution_bracket_research::BracketShadowPlan& p,
    const std::string& ocaGroup)
{
    using namespace hepta_execution_bracket_research;
    PreparedNativePair wire;
    wire.contract.conId = p.contractId;
    wire.contract.symbol = "EUR";
    wire.contract.secType = "CASH";
    wire.contract.currency = "USD";
    wire.contract.exchange = "IDEALPRO";

    wire.parent.orderId = p.parent.orderId;
    wire.parent.clientId = p.clientId;
    wire.parent.action = p.parent.intent.action;
    wire.parent.orderType = p.parent.intent.orderType;
    wire.parent.totalQuantity =
        DecimalFunctions::doubleToDecimal(p.parent.intent.totalQuantity);
    wire.parent.tif = p.parent.tif;
    wire.parent.parentId = 0;
    wire.parent.transmit = false;
    wire.parent.account = p.account;
    wire.parent.orderRef = p.parent.intent.orderRef;
    wire.parent.auxPrice = 0.0;
    wire.parent.lmtPrice = 0.0;
    wire.parent.outsideRth = false;
    wire.parent.ocaGroup.clear();
    wire.parent.ocaType = 0;

    wire.stop.orderId = p.stop.orderId;
    wire.stop.clientId = p.clientId;
    wire.stop.action = p.stop.intent.action;
    wire.stop.orderType = "STP";
    wire.stop.totalQuantity =
        DecimalFunctions::doubleToDecimal(p.stop.intent.totalQuantity);
    wire.stop.tif = p.stop.tif;
    wire.stop.parentId = p.parent.orderId;
    wire.stop.transmit = true;
    wire.stop.account = p.account;
    wire.stop.orderRef = p.stop.intent.orderRef;
    wire.stop.auxPrice = p.stop.intent.auxPrice;
    wire.stop.lmtPrice = 0.0;
    wire.stop.triggerMethod = 0; // IB default, real forex stop handling NOT verified
    wire.stop.outsideRth = false;
    // Future OCA design candidate: original protective child and ordinary
    // exit share a broker OCA group with block. Not PAPER qualified yet.
    wire.stop.ocaGroup = ocaGroup;
    wire.stop.ocaType = 1; // cancel-with-block. NO exit authorization.

    if (wire.parent.totalQuantity == UNSET_DECIMAL ||
        wire.stop.totalQuantity == UNSET_DECIMAL ||
        DecimalFunctions::decimalToDouble(wire.parent.totalQuantity) !=
            p.parent.intent.totalQuantity ||
        DecimalFunctions::decimalToDouble(wire.stop.totalQuantity) !=
            p.stop.intent.totalQuantity ||
        wire.parent.orderId <= 0 || wire.stop.parentId != wire.parent.orderId ||
        wire.stop.orderId <= wire.parent.orderId ||
        !std::isfinite(wire.stop.auxPrice) || wire.stop.auxPrice <= 0.0 ||
        wire.stop.account != wire.parent.account ||
        wire.stop.ocaGroup.empty() || wire.stop.ocaGroup.size() > 45)
        throw Refusal("IB_NATIVE_BRACKET_DECIMAL_OR_PARENT_ID_MISMATCH");
    return wire;
}


inline PreparedNativePair BuildNeverSentNativePair(
    const hepta_execution_bracket_research::PreEntrySnapshot& observation,
    std::uint64_t nowMs)
{
    const auto plan=hepta_execution_bracket_research::ProposeUnsentBracket(
        observation,nowMs);
    return NativeFromValidatedShadowPlan(
        plan,"HEPTA-FX-OCA-"+plan.originalCommandKey);
}

// Translate a Coordinator-durable H1 identity into actual native SDK
// Contract/Order objects. No API placeOrder or approved Broker ID lease.
inline PreparedNativePair BuildNeverSentNativeReservedH1Pair(
    const hepta_execution_bracket_research::PreEntrySnapshot& observation,
    const hepta_bracket_h1::Reservation& reservation,
    std::uint64_t nowMs)
{
    using hepta_execution_bracket_research::Refusal;
    const auto& f=reservation.frozen;
    const auto& ids=reservation.ids;
    std::string reason;
    if (!reservation.replayComplete || reservation.uncertain ||
        reservation.sendAuthorized || reservation.driverStopAllowed ||
        !hepta_bracket_h1::VerifyFrozenRefs(f,ids,reason) ||
        observation.account!=f.account ||
        observation.owner!=f.owner ||
        observation.originalCommandKey!=f.originalCommandKey ||
        observation.executionDomain!=f.executionDomain ||
        observation.contractId!=f.contractId ||
        observation.clientId!=f.clientId ||
        observation.reservedParentOrderId!=f.parentOrderId ||
        observation.reservedChildOrderId!=f.childOrderId ||
        observation.connectionEpoch!=f.connectionEpoch ||
        observation.originalPositionGeneration!=f.positionGeneration ||
        observation.observedAtMs!=f.intentObservedAtMs ||
        observation.action!=f.side ||
        observation.quantity!=f.quantity ||
        (f.side=="BUY" ? observation.ask : observation.bid)!=f.referencePrice)
        throw Refusal("IB_NATIVE_H1_ORIGINAL_RESERVATION_MISMATCH");
    // Never invoke the legacy HEPTA-* ref producer as a prerequisite for
    // native H1. It rejects valid long original commands before H1 encoding.
    // Both modes use exactly the same Broker/Owner/quote/stop-price policy.
    const auto plan=hepta_execution_bracket_research::ProposeUnsentBracketWithRefs(
        observation,nowMs,ids.parentRef,ids.childRef);
    if(plan.stop.intent.auxPrice!=f.stopPrice)
        throw Refusal("IB_NATIVE_H1_FROZEN_STOP_PRICE_MISMATCH");
    PreparedNativePair result=NativeFromValidatedShadowPlan(plan,ids.ocaGroup);
    result.parent.orderRef=ids.parentRef;
    result.stop.orderRef=ids.childRef;
    result.stop.ocaGroup=ids.ocaGroup;
    if(result.parent.orderRef.size()!=45 ||
       result.stop.orderRef.size()!=45 ||
       result.stop.ocaGroup.size()!=45 ||
       result.parent.orderRef==result.stop.orderRef ||
       result.parent.orderRef==result.stop.ocaGroup ||
       result.stop.orderRef==result.stop.ocaGroup ||
       result.brokerSendEnabled || result.driverStopAuthorized ||
       result.ordinaryExitWithoutOcaAllowed)
        throw Refusal("IB_NATIVE_H1_BROKER_TRANSPORT_REFUSED");
    return result;
}

// This is a research-only OCA sibling that cannot enter the broker transport.
// A MARKET close without matching broker OCA can reverse a closed FX position.
struct BrokerOcaStopObservation final {
    std::string account;
    std::string owner;
    std::string originalCommandKey;
    std::string originalStopOrderRef;
    std::string brokerOcaGroup;
    std::string executionDomain;
    long contractId = 0;
    long stopOrderId = 0;
    long stopPermanentId = 0;
    long originalParentPermanentId = 0;
    long reservedExitOrderId = 0;
    int brokerClientId = -1;
    std::uint64_t connectionEpoch = 0;
    std::uint64_t positionConnectionEpoch = 0;
    std::uint64_t activeOrdersConnectionEpoch = 0;
    std::uint64_t observedAtMs = 0;
    std::uint64_t validUntilMs = 0;
    bool originalEntryAndStopAttested = false;
    bool sameBrokerOcaGroupAttested = false;
    bool noNewEntriesAcknowledged = false;
    bool workingStopActive = false;
    bool brokerOrderSetComplete = false;
    bool noUnknownOriginalCommand = false;
    std::uint64_t activeOrderCount = 0;
    double brokerPosition = 0.0;
    double stopTotalQuantity = 0.0;
    double stopRemainingQuantity = 0.0;
};

struct UnsentNativeOcaExit final {
    Contract contract;
    Order exit;
    bool sendAuthority = false;
    bool mayStopDriver = false;
    bool automaticTradingEnabled = false;
};

inline UnsentNativeOcaExit ProposeUnsentOcaExitWithRefs(
    const hepta_execution_bracket_research::BracketShadowPlan& group,
    const BrokerOcaStopObservation& state, std::uint64_t nowMs,
    const std::string& expectedOcaGroup, const std::string& expectedExitRef)
{
    using hepta_execution_bracket_research::Refusal;
    if (state.executionDomain != "PAPER" ||
        state.account != group.account || state.owner != group.owner ||
        state.originalCommandKey != group.originalCommandKey ||
        state.originalStopOrderRef != group.stop.intent.orderRef ||
        state.contractId != group.contractId || state.contractId != 12087792 ||
        state.brokerOcaGroup != expectedOcaGroup ||
        state.brokerClientId != group.clientId)
        throw Refusal("OCA_EXACT_ORIGINAL_ORDER_SCOPE_MISMATCH");
    if (!state.originalEntryAndStopAttested ||
        !state.sameBrokerOcaGroupAttested ||
        !state.noNewEntriesAcknowledged ||
        !state.workingStopActive || !state.brokerOrderSetComplete ||
        !state.noUnknownOriginalCommand || state.activeOrderCount != 1)
        throw Refusal("OCA_ORIGINAL_STOP_OR_FENCE_UNPROVEN");
    if (state.connectionEpoch < group.connectionEpoch ||
        state.connectionEpoch == 0 ||
        state.positionConnectionEpoch != state.connectionEpoch ||
        state.activeOrdersConnectionEpoch != state.connectionEpoch ||
        state.observedAtMs == 0 || state.observedAtMs > nowMs ||
        state.validUntilMs < nowMs ||
        state.validUntilMs < state.observedAtMs ||
        state.validUntilMs - state.observedAtMs > 3000)
        throw Refusal("OCA_BROKER_EPOCH_OR_FRESHNESS_MISMATCH");
    if (state.stopOrderId != group.stop.orderId ||
        state.stopOrderId < 1 ||
        state.stopPermanentId < 1 ||
        state.originalParentPermanentId < 1 ||
        state.stopPermanentId == state.originalParentPermanentId ||
        state.reservedExitOrderId <= state.stopOrderId ||
        // A single OCA exit only consumes one signed TWS native ID.
        // There is no need to reserve two extra integers; the last valid
        // int32 ID is legal as an UNSENT candidate. The actual exit still
        // requires an Owner-owned durable ID and live Broker working stop.
        static_cast<std::int64_t>(state.reservedExitOrderId) >
            static_cast<std::int64_t>(std::numeric_limits<std::int32_t>::max()))
        throw Refusal("OCA_STOP_PERM_ID_OR_EXIT_RESERVATION_MISMATCH");
    const double qty = group.parent.intent.totalQuantity;
    const double signedPosition =
        group.parent.intent.action == "BUY" ? qty : -qty;
    if (!std::isfinite(qty) || qty <= 0.0 ||
        !std::isfinite(state.brokerPosition) ||
        state.brokerPosition != signedPosition ||
        state.stopTotalQuantity != qty ||
        state.stopRemainingQuantity != qty)
        throw Refusal("OCA_PARTIAL_OR_REVERSED_EXPOSURE");

    UnsentNativeOcaExit plan;
    plan.contract.conId = group.contractId;
    plan.contract.symbol = "EUR";
    plan.contract.currency = "USD";
    plan.contract.secType = "CASH";
    plan.contract.exchange = "IDEALPRO";
    plan.exit.orderId = state.reservedExitOrderId;
    plan.exit.clientId = state.brokerClientId;
    plan.exit.account = group.account;
    plan.exit.orderType = "MKT";
    plan.exit.action = group.stop.intent.action;
    plan.exit.totalQuantity = DecimalFunctions::doubleToDecimal(qty);
    if (plan.exit.totalQuantity == UNSET_DECIMAL ||
        DecimalFunctions::decimalToDouble(plan.exit.totalQuantity) != qty)
        throw Refusal("OCA_NATIVE_QUANTITY_ENCODE_FAILED");
    plan.exit.lmtPrice = 0.0;
    plan.exit.auxPrice = 0.0;
    plan.exit.tif = "DAY";
    plan.exit.parentId = 0; // sibling OCA, not a second attached child
    plan.exit.ocaGroup = state.brokerOcaGroup;
    plan.exit.ocaType = 1;   // IB overfill-protecting cancel-with-block
    plan.exit.orderRef = expectedExitRef;
    // The OCA sibling is UNSENT. Never silently truncate an IB orderRef.
    if (plan.exit.orderRef.size() > 45)
        throw Refusal("OCA_SHADOW_ORDER_REF_EXCEEDS_IB_LIMIT");
    plan.exit.transmit = true;
    return plan;
}

// Preserve the historical research-only legacy wrapper unchanged in meaning.
inline UnsentNativeOcaExit ProposeUnsentOcaExit(
    const hepta_execution_bracket_research::BracketShadowPlan& group,
    const BrokerOcaStopObservation& state, std::uint64_t nowMs)
{
    return ProposeUnsentOcaExitWithRefs(group,state,nowMs,
        "HEPTA-FX-OCA-"+group.originalCommandKey,
        "HEPTA-OCA-CLOSE-"+group.originalCommandKey);
}

// Translate the SAME protected H1 stop into a distinct H1 ordinary-exit
// sibling within its original OCA group. This is a pure native SDK proposal:
// it has NO broker sender, order ID allocator, maintenance or trade authority.
// Caller-supplied broker state booleans are synthetic here, not live proof.
inline UnsentNativeOcaExit ProposeUnsentH1OcaExit(
    const hepta_bracket_h1::Reservation& reserved,
    const BrokerOcaStopObservation& state, std::uint64_t nowMs)
{
    using hepta_execution_bracket_research::Refusal;
    const auto& f=reserved.frozen;
    const auto& ids=reserved.ids;
    std::string reason;
    if(!reserved.replayComplete || reserved.uncertain ||
       reserved.sendAuthorized || reserved.driverStopAllowed ||
       !hepta_bracket_h1::VerifyFrozenRefs(f,ids,reason))
        throw Refusal("H1_OCA_ORIGINAL_RESERVATION_UNVERIFIED");
    // Keep the original native child permanent-ID and this same Broker
    // connection's openOrder re-attestation bound to the OCA candidate.
    // A caller-supplied working=true flag is not sufficient evidence.
    if(reserved.latestNativeStopPermanentId<=0 ||
       reserved.latestNativeStopPermanentId!=state.stopPermanentId ||
       reserved.latestNativeOpenOrderEpoch==0 ||
       reserved.latestNativeOpenOrderEpoch!=state.connectionEpoch)
        throw Refusal("H1_OCA_NATIVE_PERM_ID_OR_REATTESTATION_MISMATCH");
    hepta_execution_bracket_research::BracketShadowPlan group;
    group.account=f.account;
    group.owner=f.owner;
    group.originalCommandKey=f.originalCommandKey;
    group.contractId=f.contractId;
    group.clientId=f.clientId;
    group.connectionEpoch=f.connectionEpoch;
    group.parent.intent.action=f.side;
    group.parent.intent.totalQuantity=f.quantity;
    group.stop.orderId=f.childOrderId;
    group.stop.intent.orderRef=ids.childRef;
    group.stop.intent.action=f.side=="BUY"?"SELL":"BUY";
    group.stop.intent.totalQuantity=f.quantity;
    group.stop.intent.auxPrice=f.stopPrice;

    // The ordinary exit has a DIFFERENT 45-byte H1 role from parent/stop/OCA.
    // This H1 exit identity is stable across a reconnected Broker epoch
    // when the SAME original stop permanent ID and reserved exit ID are
    // re-attested. A different exit or stop identity produces a different
    // ref; no implicit order placement is possible.
    std::string identity;
    hepta_bracket_h1::Field(identity,"hepta-bracket-h1-oca-exit/v1");
    hepta_bracket_h1::Field(identity,ids.groupCorrelation);
    hepta_bracket_h1::Field(identity,std::to_string(state.reservedExitOrderId));
    hepta_bracket_h1::Field(identity,std::to_string(state.stopPermanentId));
    const std::string correlation=hepta_bracket_h1::HashCanonical(identity);
    std::string exitRef;
    if(!IbVenueCorrelationCodec::EncodeOrderRef(correlation,exitRef,reason) ||
       exitRef.size()!=45 || exitRef==ids.parentRef ||
       exitRef==ids.childRef || exitRef==ids.ocaGroup)
        throw Refusal("H1_OCA_DISTINCT_EXIT_CORRELATION_INVALID");
    const auto proposal=ProposeUnsentOcaExitWithRefs(
        group,state,nowMs,ids.ocaGroup,exitRef);
    if(proposal.sendAuthority || proposal.mayStopDriver ||
       proposal.automaticTradingEnabled ||
       proposal.exit.orderRef!=exitRef ||
       proposal.exit.ocaGroup!=ids.ocaGroup ||
       proposal.exit.ocaType!=1)
        throw Refusal("H1_OCA_NO_BROKER_SEND_AUTHORITY");
    return proposal;
}

} // namespace hepta_execution_bracket_native_research
