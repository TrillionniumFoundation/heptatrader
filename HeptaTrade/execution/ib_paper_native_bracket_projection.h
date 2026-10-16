#pragma once
// Pure, explicitly owner-scoped projection used by the actual Execution
// Broker callback logger AND isolated OMS native stop tests.
// No order dispatch, maintenance authorization or implicit broker acceptance.
#include "../adapter_ib/ib_api_wrapper.h"
#include "../oms_journal.h"
#include "ib_bracket_h1_oms_reservation.h"
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

// Research-only H1 callback candidate: distinct from legacy HEPTA-STOP.
// Caller must supply the actual original OMS journal replay; a structurally
// plausible H1 OpenOrder alone never proves working stop or maintenance safety.
inline Result ProjectFrozenH1WorkingStop(
    const IBEvent& event,
    const hepta_bracket_h1::FrozenBracket& frozen,
    const std::vector<OmsJournalEvent>& originalOms,
    OmsJournalEvent& record, std::string& reason)
{
    hepta_bracket_h1::Reservation original;
    std::string replayReason;
    if(!hepta_bracket_h1::ConfirmReplay(originalOms,frozen,
                                       original,replayReason) &&
       !hepta_bracket_h1::ConfirmReplayForOriginalNativeEcho(
                                       originalOms,frozen,
                                       original,replayReason)) {
        reason="IB_H1_ORIGINAL_DURABLE_PAIR_UNVERIFIED";
        return Result::Rejected;
    }
    if(event.order.orderRef!=original.ids.childRef)
        return Result::Ignored; // not this frozen protection group
    // No repeated old-epoch callback may be relabelled as a new observation.
    // The permanent Broker order ID must survive every reconnect.
    if(original.latestNativeOpenOrderEpoch!=0 &&
       (event.connectionEpoch<=original.latestNativeOpenOrderEpoch ||
        event.nativeOrder.permanentId!=original.latestNativeStopPermanentId)) {
        reason="IB_H1_NATIVE_OPEN_ORDER_REATTEST_EPOCH_OR_PERMID_CHANGED";
        return Result::Rejected;
    }
    if(event.type!=IBEventType::OpenOrder || event.value!="Submitted") {
        // An event matching our exact H1 stop but not a live submitted
        // OpenOrder is NEGATIVE evidence. Do not silently treat an inactive,
        // held, completed or status-only update as continued protection.
        reason="IB_H1_ORIGINAL_STOP_NOT_WORKING_OPEN_ORDER";
        return Result::Rejected;
    }
    const auto& w=event.nativeOrder;
    const std::string action=frozen.side=="BUY"?"SELL":"BUY";
    if(!record.eventType.empty() || record.brokerBracket.version!=0 ||
       !record.eventId.empty() || !record.status.empty() ||
       !record.brokerCallbackType.empty() ||
       (!record.requestHash.empty() &&
        record.requestHash!=original.ids.groupCorrelation) ||
       (!record.venueCorrelationId.empty() &&
        record.venueCorrelationId!=original.ids.childCorrelation) ||
       record.reqId!=frozen.originalCommandKey ||
       (!record.clientReqId.empty() &&
        record.clientReqId!=frozen.originalCommandKey) ||
       record.traceId!=frozen.sessionId || record.strategy!=frozen.owner ||
       record.source!="agent:"+frozen.owner ||
       record.account!=frozen.account || record.instrument!="EUR.USD" ||
       record.executionDomain!="PAPER" || record.venue!="IB" ||
       record.orderId!=frozen.childOrderId ||
       record.brokerClientId!=frozen.clientId ||
       record.brokerConnectionEpoch==0 ||
       record.brokerConnectionEpoch!=event.connectionEpoch ||
       event.connectionEpoch<frozen.connectionEpoch ||
       event.queueIngressMonotonicNs==0 ||
       event.account!=frozen.account ||
       event.brokerClientId!=frozen.clientId ||
       event.id!=frozen.childOrderId ||
       event.contract.symbol!="EUR" ||
       event.contract.currency!="USD" ||
       event.contract.secType!="CASH" ||
       event.contract.exchange!="IDEALPRO" ||
       event.order.action!=action ||
       event.order.orderType!="STP" ||
       event.order.totalQuantity!=frozen.quantity ||
       event.order.auxPrice!=frozen.stopPrice ||
       !w.present || w.contractId!=frozen.contractId ||
       w.orderId!=frozen.childOrderId ||
       w.parentId!=frozen.parentOrderId || w.permanentId<=0 ||
       w.clientId!=frozen.clientId || !w.transmit ||
       w.account!=frozen.account || w.action!=action ||
       w.orderType!="STP" || w.timeInForce!="GTC" ||
       w.orderRef!=original.ids.childRef ||
       w.ocaGroup!=original.ids.ocaGroup || w.ocaType!=1 ||
       w.triggerMethod!=0 || w.stopAuxPrice!=frozen.stopPrice ||
       w.totalQuantity!=frozen.quantity) {
        reason="IB_H1_ORIGINAL_NATIVE_OPEN_ORDER_UNVERIFIED";
        return Result::Rejected;
    }
    OmsJournalEvent candidate=record;
    auto& b=candidate.brokerBracket;
    b.version=2; b.contractId=w.contractId;
    b.parentOrderId=w.parentId;b.permanentOrderId=w.permanentId;
    b.transmit=1;b.ocaType=w.ocaType;b.triggerMethod=w.triggerMethod;
    b.orderRef=w.orderRef;b.ocaGroup=w.ocaGroup;
    b.timeInForce=w.timeInForce;b.quantity=w.totalQuantity;
    b.stopAuxPrice=w.stopAuxPrice;
    if(!b.ValidShape()) {
        reason="IB_H1_ORIGINAL_NATIVE_SHAPE_INVALID";
        return Result::Rejected;
    }
    candidate.eventType="broker_open_order_bracket";
    candidate.brokerCallbackType="openOrder";
    candidate.status="Submitted";
    candidate.side=action;candidate.qty=b.quantity;candidate.price=b.stopAuxPrice;
    candidate.requestHash=original.ids.groupCorrelation;
    candidate.venueCorrelationId=original.ids.childCorrelation;
    candidate.eventId=original.ids.childRef+"-"+
        std::to_string(candidate.brokerConnectionEpoch)+"-"+
        std::to_string(b.permanentOrderId);
    record=std::move(candidate);
    reason.clear();
    // Candidate only: account-wide position, pending entries, parent fill,
    // original native order set and OCA-safe ordinary exit are NOT proven.
    return Result::WorkingWitnessCandidate;
}

} // namespace hepta_native_bracket_projection
