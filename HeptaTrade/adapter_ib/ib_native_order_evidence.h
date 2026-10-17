#pragma once
// Read-only evidence from the ORIGINAL TWS SDK EWrapper order-bearing callback.
// Never a trading permit, send path, protective-stop guarantee or policy input.
#include "ib_api_wrapper.h"
#include "Contract.h"
#include "Order.h"
#include "Decimal.h"
#include <limits>

namespace hepta_ib_native_order_evidence {
inline IBBrokerNativeOrderWitness Capture(
    const Contract& contract, const Order& order, long callbackOrderId)
{
    IBBrokerNativeOrderWitness evidence;
    evidence.present=true;
    evidence.contractId=contract.conId;
    evidence.orderId=callbackOrderId;
    evidence.permanentId=order.permId;
    evidence.parentId=order.parentId;
    evidence.clientId=static_cast<int>(order.clientId);
    evidence.transmit=order.transmit;
    evidence.account=order.account;
    evidence.action=order.action;
    evidence.orderType=order.orderType;
    evidence.timeInForce=order.tif;
    evidence.orderRef=order.orderRef;
    evidence.ocaGroup=order.ocaGroup;
    evidence.ocaType=order.ocaType;
    evidence.triggerMethod=order.triggerMethod;
    evidence.stopAuxPrice=order.auxPrice==UNSET_DOUBLE ?
        std::numeric_limits<double>::quiet_NaN() : order.auxPrice;
    evidence.totalQuantity=order.totalQuantity==UNSET_DECIMAL ?
        std::numeric_limits<double>::quiet_NaN() :
        DecimalFunctions::decimalToDouble(order.totalQuantity);
    return evidence;
}
} // namespace hepta_ib_native_order_evidence
