#pragma once
// Exact native EWrapper::openOrder echo matching, strictly diagnostic.
// A matching Submitted echo does NOT prove a working stop, current physical
// exposure, OCA cancellation semantics or Driver-stop permission.
#include "ib_api_wrapper.h"
#include "../execution/ib_bracket_h1_identity.h"
#include <cmath>
namespace hepta_ib_h1_native_stop_echo {
inline bool MatchesOriginalSdkStop(const IBEvent& event,
    const hepta_bracket_h1::FrozenBracket& original,
    const hepta_bracket_h1::BoundRefs& refs,
    std::uint64_t exactBrokerEpoch)
{
    const auto& native=event.nativeOrder;
    const auto expectedSide=original.side=="BUY" ? "SELL" : "BUY";
    return event.type==IBEventType::OpenOrder &&
        event.value=="Submitted" &&
        event.id>0 && event.id==original.childOrderId &&
        event.connectionEpoch==exactBrokerEpoch &&
        event.queueIngressMonotonicNs!=0 &&
        event.account==original.account &&
        event.brokerClientId==original.clientId &&
        event.contract.symbol=="EUR" &&
        event.contract.secType=="CASH" &&
        event.contract.currency=="USD" &&
        event.contract.exchange=="IDEALPRO" &&
        event.order.action==expectedSide &&
        event.order.orderType=="STP" &&
        event.order.orderRef==refs.childRef &&
        event.order.totalQuantity==original.quantity &&
        event.order.auxPrice==original.stopPrice &&
        native.present && native.contractId==12087792 &&
        native.contractId==original.contractId &&
        native.orderId==original.childOrderId &&
        native.parentId==original.parentOrderId &&
        native.permanentId>0 &&
        native.clientId==original.clientId &&
        native.account==original.account &&
        native.action==expectedSide &&
        native.orderType=="STP" &&
        native.timeInForce=="GTC" &&
        native.orderRef==refs.childRef &&
        native.ocaGroup==refs.ocaGroup &&
        native.ocaType==1 &&
        native.transmit &&
        native.triggerMethod==0 &&
        std::isfinite(native.stopAuxPrice) &&
        native.stopAuxPrice==original.stopPrice &&
        std::isfinite(native.totalQuantity) &&
        native.totalQuantity==original.quantity;
}
} // namespace hepta_ib_h1_native_stop_echo
