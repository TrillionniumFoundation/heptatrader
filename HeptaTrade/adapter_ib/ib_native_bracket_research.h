#pragma once
// A production SDK type translation probe for Issue #331. NO placeOrder,
// cancelOrder, socket, queue, timer, session or trading authority is exposed.
// Compiled only by a native-IB-enabled core test; never installed with Gateway.
#include "../execution/ib_paper_execution_bracket_research.h"
#include "Contract.h"
#include "Order.h"
#include "Decimal.h"
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
inline PreparedNativePair BuildNeverSentNativePair(
    const hepta_execution_bracket_research::PreEntrySnapshot& observation,
    std::uint64_t nowMs)
{
    using namespace hepta_execution_bracket_research;
    const BracketShadowPlan p = ProposeUnsentBracket(observation, nowMs);
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
    wire.stop.ocaGroup = "HEPTA-FX-OCA-" + p.originalCommandKey;
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
        wire.stop.account != wire.parent.account)
        throw Refusal("IB_NATIVE_BRACKET_DECIMAL_OR_PARENT_ID_MISMATCH");
    return wire;
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

inline UnsentNativeOcaExit ProposeUnsentOcaExit(
    const hepta_execution_bracket_research::BracketShadowPlan& group,
    const BrokerOcaStopObservation& state, std::uint64_t nowMs)
{
    using hepta_execution_bracket_research::Refusal;
    if (state.executionDomain != "PAPER" ||
        state.account != group.account || state.owner != group.owner ||
        state.originalCommandKey != group.originalCommandKey ||
        state.originalStopOrderRef != group.stop.intent.orderRef ||
        state.contractId != group.contractId || state.contractId != 12087792 ||
        state.brokerOcaGroup !=
            "HEPTA-FX-OCA-" + group.originalCommandKey ||
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
        state.reservedExitOrderId >= 2147483646)
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
    plan.exit.orderRef = "HEPTA-OCA-CLOSE-" + group.originalCommandKey;
    plan.exit.transmit = true;
    return plan;
}

} // namespace hepta_execution_bracket_native_research
