#pragma once
// Pure, explicitly owner-scoped projection used by the actual Execution
// Broker callback logger AND isolated OMS native stop tests.
// No order dispatch, maintenance authorization or implicit broker acceptance.
#include "../adapter_ib/ib_api_wrapper.h"
#include "../oms_journal.h"
#include <string>

namespace hepta_native_bracket_projection {

enum class Result { Ignored, Rejected, WorkingWitnessCandidate };

inline Result ProjectOriginalWorkingStop(
    const IBEvent& event, OmsJournalEvent& record, std::string& reason)
{
    const std::string& ref=event.order.orderRef;
    if (event.type != IBEventType::OpenOrder ||
        ref.compare(0,11,"HEPTA-STOP-") != 0)
        return Result::Ignored;
    if (event.value != "Submitted")
        return Result::Ignored;
    const auto& w=event.nativeOrder;
    if (!w.present || event.queueIngressMonotonicNs == 0 ||
        !event.connectionEpoch ||
        event.connectionEpoch != record.brokerConnectionEpoch ||
        w.orderRef != ref || w.account != record.account ||
        w.orderId != record.orderId ||
        w.clientId != record.brokerClientId ||
        event.account != record.account ||
        event.brokerClientId != record.brokerClientId ||
        record.brokerClientId <= 0 ||
        record.source.compare(0,6,"agent:") != 0 ||
        w.action != event.order.action ||
        w.orderType != "STP" ||
        w.stopAuxPrice != event.order.auxPrice ||
        w.totalQuantity != event.order.totalQuantity ||
        w.timeInForce != "GTC" || !w.transmit ||
        event.contract.symbol != "EUR" ||
        event.contract.currency != "USD" ||
        event.contract.secType != "CASH" ||
        event.contract.exchange != "IDEALPRO")
    {
        reason="IB_BRACKET_NATIVE_OPEN_ORDER_ECHO_UNVERIFIED";
        return Result::Rejected;
    }
    auto& b=record.brokerBracket;
    b.version=1;
    b.contractId=w.contractId;
    b.parentOrderId=w.parentId;
    b.permanentOrderId=w.permanentId;
    b.transmit=w.transmit ? 1 : 0;
    b.ocaType=w.ocaType;
    b.triggerMethod=w.triggerMethod;
    b.orderRef=w.orderRef;
    b.ocaGroup=w.ocaGroup;
    b.timeInForce=w.timeInForce;
    b.quantity=w.totalQuantity;
    b.stopAuxPrice=w.stopAuxPrice;
    if (!b.ValidShape())
    {
        reason="IB_BRACKET_NATIVE_OPEN_ORDER_SHAPE_INVALID";
        return Result::Rejected;
    }
    record.eventType="broker_open_order_bracket";
    record.brokerCallbackType="openOrder";
    record.status="Submitted";
    record.side=w.action;
    record.qty=b.quantity;
    record.price=b.stopAuxPrice;
    record.eventId=ref+"-"+std::to_string(record.brokerConnectionEpoch)+
        "-"+std::to_string(b.permanentOrderId);
    reason.clear();
    return Result::WorkingWitnessCandidate;
}

} // namespace hepta_native_bracket_projection
