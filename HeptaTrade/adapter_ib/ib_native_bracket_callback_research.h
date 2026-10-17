#pragma once
// Direct IB C++ SDK callback evidence from EWrapper.openOrder /
// completedOrder. Never order transport or a broker stop authorization.
// Capturing as additive IBEvent witness prevents parentId, OCA and permId
// from silently disappearing through the existing single-order projection.
#include "ib_api_wrapper.h"
#include "../execution/ib_paper_execution_bracket_research.h"
#include "Contract.h"
#include "Order.h"
#include "Decimal.h"
#include <cmath>
#include <limits>

namespace hepta_ib_bracket_callback_research {

inline IBBrokerNativeOrderWitness CaptureNativeBrokerOrder(
    const Contract& c, const Order& o, long callbackOrderId)
{
    IBBrokerNativeOrderWitness w;
    w.present = true; // only for actual order-bearing SDK callbacks
    w.contractId = c.conId;
    w.orderId = callbackOrderId;
    w.permanentId = o.permId;
    w.parentId = o.parentId;
    w.clientId = static_cast<int>(o.clientId);
    w.transmit = o.transmit;
    w.account = o.account;
    w.action = o.action;
    w.orderType = o.orderType;
    w.timeInForce = o.tif;
    w.orderRef = o.orderRef;
    w.ocaGroup = o.ocaGroup;
    w.ocaType = o.ocaType;
    w.triggerMethod = o.triggerMethod;
    w.stopAuxPrice = o.auxPrice == UNSET_DOUBLE ?
        std::numeric_limits<double>::quiet_NaN() : o.auxPrice;
    w.totalQuantity = o.totalQuantity == UNSET_DECIMAL ?
        std::numeric_limits<double>::quiet_NaN() :
        DecimalFunctions::decimalToDouble(o.totalQuantity);
    return w;
}

// The pure matcher verifies that an exact broker openOrder callback represents
// a working original protective child. It is NOT sufficient to authorize
// maintenance: account-wide position and active orders, parent actual fill,
// OCA cancel/replace, fresh original command and stop trigger remain independent
// requirements of Issue #331. Synthetic orderStatus without SDK Order fields,
// completedOrder records and disconnected-epoch copies must never qualify.
inline bool ExactWorkingStopOpenOrder(
    const IBEvent& event,
    const hepta_execution_bracket_research::BracketShadowPlan& group,
    int expectedClientId, std::uint64_t expectedBrokerEpoch)
{
    const IBBrokerNativeOrderWitness& w = event.nativeOrder;
    if (event.type != IBEventType::OpenOrder ||
        event.value != "Submitted" ||
        !w.present ||
        expectedClientId < 1 || expectedBrokerEpoch == 0 ||
        event.connectionEpoch != expectedBrokerEpoch ||
        event.queueIngressMonotonicNs == 0 ||
        event.account != group.account ||
        event.brokerClientId != expectedClientId ||
        event.id != group.stop.orderId ||
        event.contract.symbol != "EUR" ||
        event.contract.secType != "CASH" ||
        event.contract.currency != "USD" ||
        event.contract.exchange != "IDEALPRO" ||
        event.order.orderType != "STP" ||
        event.order.action != group.stop.intent.action ||
        event.order.orderRef != group.stop.intent.orderRef ||
        event.order.totalQuantity != group.stop.intent.totalQuantity ||
        event.order.auxPrice != group.stop.intent.auxPrice)
        return false;
    return w.contractId == group.contractId &&
        w.contractId == 12087792 &&
        w.orderId == group.stop.orderId &&
        w.parentId == group.parent.orderId &&
        w.permanentId > 0 &&
        w.clientId == expectedClientId &&
        w.transmit &&
        w.account == group.account &&
        w.action == group.stop.intent.action &&
        w.orderType == "STP" &&
        w.timeInForce == "GTC" &&
        w.orderRef == group.stop.intent.orderRef &&
        w.ocaGroup == "HEPTA-FX-OCA-" + group.originalCommandKey &&
        w.ocaType == 1 &&
        w.triggerMethod == 0 &&
        std::isfinite(w.stopAuxPrice) &&
        w.stopAuxPrice == group.stop.intent.auxPrice &&
        std::isfinite(w.totalQuantity) &&
        w.totalQuantity == group.stop.intent.totalQuantity;
}

} // namespace hepta_ib_bracket_callback_research
