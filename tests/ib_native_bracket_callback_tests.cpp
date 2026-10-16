#include "../HeptaTrade/adapter_ib/ib_native_bracket_research.h"
#include "../HeptaTrade/adapter_ib/ib_native_bracket_callback_research.h"
#include <iostream>
#include <stdexcept>
#include <string>

using namespace hepta_execution_bracket_research;
using namespace hepta_execution_bracket_native_research;
using namespace hepta_ib_bracket_callback_research;

namespace {
void Require(bool yes, const char* why) {
    if (!yes) throw std::runtime_error(why);
}
PreEntrySnapshot Entry() {
    PreEntrySnapshot s;
    s.account="DU-SYNTHETIC";s.owner="fenced-owner-1";
    s.originalCommandKey="callback-test-entry-001";
    s.executionDomain="PAPER";
    s.contractId=12087792;s.clientId=1772;
    s.reservedParentOrderId=4501;s.reservedChildOrderId=4502;
    s.connectionEpoch=79;s.positionConnectionEpoch=79;
    s.openOrdersConnectionEpoch=79;s.originalPositionGeneration=101;
    s.observedAtMs=100000;s.validUntilMs=101000;
    s.exactAccountOwnerAndContract=true;
    s.brokerIdsReservedWithinExactClient=true;
    s.coordinatorExclusiveEntryOwner=true;
    s.riskSnapshotComplete=true;s.openOrdersComplete=true;
    s.positionFlat=true;s.noUnresolvedOrders=true;
    s.quantity=89200;
    s.bid=1.12045;s.ask=1.12050;
    s.minTick=0.00005;s.action="BUY";
    return s;
}
IBEvent BrokerOpenStop(
    const PreparedNativePair& pair, const BracketShadowPlan& group)
{
    Order stop=pair.stop;
    stop.permId=888871;
    IBEvent event;
    event.type=IBEventType::OpenOrder;
    event.id=stop.orderId;
    event.value="Submitted";
    event.account=stop.account;
    event.brokerClientId=stop.clientId;
    event.connectionEpoch=group.connectionEpoch;
    event.queueIngressMonotonicNs=555000;
    event.contract.symbol=pair.contract.symbol;
    event.contract.currency=pair.contract.currency;
    event.contract.exchange=pair.contract.exchange;
    event.contract.secType=pair.contract.secType;
    event.order.action=stop.action;
    event.order.orderType=stop.orderType;
    event.order.totalQuantity=DecimalFunctions::decimalToDouble(stop.totalQuantity);
    event.order.orderRef=stop.orderRef;
    event.order.auxPrice=stop.auxPrice;
    event.nativeOrder=CaptureNativeBrokerOrder(
        pair.contract,stop,stop.orderId);
    return event;
}
void RealSdkEchoMatching() {
    const PreEntrySnapshot source=Entry();
    const auto p=ProposeUnsentBracket(source,100500);
    const auto pair=BuildNeverSentNativePair(source,100500);
    auto callback=BrokerOpenStop(pair,p);
    Require(ExactWorkingStopOpenOrder(callback,p,source.clientId,79),
            "correct native stop broker echo refused");
    Require(callback.nativeOrder.permanentId==888871 &&
            callback.nativeOrder.parentId==p.parent.orderId &&
            callback.nativeOrder.ocaType==1 &&
            callback.nativeOrder.ocaGroup==
                "HEPTA-FX-OCA-"+p.originalCommandKey &&
            callback.nativeOrder.transmit,
            "native callback lost permanentId/parent/OCA/transmit");
    // An openOrder from unrelated MARKET parent is not a protection signal.
    Order parent=pair.parent;
    parent.permId=888872;
    auto parentEcho=CaptureNativeBrokerOrder(pair.contract,parent,parent.orderId);
    Require(parentEcho.present && !parentEcho.transmit &&
            parentEcho.parentId==0 && parentEcho.ocaType==0,
            "parent's untransmitted native evidence was lost");
    callback.nativeOrder=parentEcho;
    Require(!ExactWorkingStopOpenOrder(callback,p,source.clientId,79),
            "untransmitted native parent falsely passed protective matcher");
}
void RejectForgedAndIncompleteEcho() {
    const auto source=Entry();
    const auto p=ProposeUnsentBracket(source,100500);
    const auto pair=BuildNeverSentNativePair(source,100500);
    auto base=BrokerOpenStop(pair,p);
    auto e=base;e.type=IBEventType::OrderStatus;e.nativeOrder.present=false;
    Require(!ExactWorkingStopOpenOrder(e,p,source.clientId,79),
            "numeric orderStatus promoted to working protection");
    e=base;e.type=IBEventType::CompletedOrder;
    Require(!ExactWorkingStopOpenOrder(e,p,source.clientId,79),
            "completed order promoted to active protection");
    e=base;e.value="PreSubmitted";
    Require(!ExactWorkingStopOpenOrder(e,p,source.clientId,79),
            "preSubmitted stop trusted as active");
    e=base;e.nativeOrder.permanentId=0;
    Require(!ExactWorkingStopOpenOrder(e,p,source.clientId,79),
            "missing permanent ID accepted");
    e=base;e.nativeOrder.parentId++;
    Require(!ExactWorkingStopOpenOrder(e,p,source.clientId,79),
            "wrong parent ID accepted");
    e=base;e.nativeOrder.ocaGroup="different";
    Require(!ExactWorkingStopOpenOrder(e,p,source.clientId,79),
            "different broker OCA group accepted");
    e=base;e.nativeOrder.ocaType=0;
    Require(!ExactWorkingStopOpenOrder(e,p,source.clientId,79),
            "unblocked OCA accepted");
    e=base;e.nativeOrder.transmit=false;
    Require(!ExactWorkingStopOpenOrder(e,p,source.clientId,79),
            "held child accepted");
    e=base;e.nativeOrder.timeInForce="DAY";
    Require(!ExactWorkingStopOpenOrder(e,p,source.clientId,79),
            "wrong native stop TIF accepted");
    e=base;e.nativeOrder.stopAuxPrice+=.00005;
    Require(!ExactWorkingStopOpenOrder(e,p,source.clientId,79),
            "changed callback trigger accepted");
    e=base;e.nativeOrder.totalQuantity--;
    Require(!ExactWorkingStopOpenOrder(e,p,source.clientId,79),
            "different native qty accepted");
    e=base;e.nativeOrder.contractId++;
    Require(!ExactWorkingStopOpenOrder(e,p,source.clientId,79),
            "different contract ID accepted");
    e=base;e.nativeOrder.account="DU-OTHER";
    Require(!ExactWorkingStopOpenOrder(e,p,source.clientId,79),
            "other broker account accepted");
    e=base;e.nativeOrder.clientId++;
    Require(!ExactWorkingStopOpenOrder(e,p,source.clientId,79),
            "foreign owner client accepted");
    e=base;e.connectionEpoch=80;
    Require(!ExactWorkingStopOpenOrder(e,p,source.clientId,79),
            "stale broker connection accepted");
    e=base;e.queueIngressMonotonicNs=0;
    Require(!ExactWorkingStopOpenOrder(e,p,source.clientId,79),
            "callback without ingress provenance accepted");
    e=base;e.order.orderRef.clear();
    Require(!ExactWorkingStopOpenOrder(e,p,source.clientId,79),
            "lost original venue reference accepted");
    e=base;e.contract.symbol="BTC";
    Require(!ExactWorkingStopOpenOrder(e,p,source.clientId,79),
            "wrong instrument accepted");
}
} // namespace

int main() {
    try {
        RealSdkEchoMatching();
        RejectForgedAndIncompleteEcho();
        std::cout << "ib_native_bracket_callback_evidence=PASS\n";
        return 0;
    } catch(const std::exception& e) {
        std::cerr << "FAIL: " << e.what() << '\n';
        return 1;
    }
}
