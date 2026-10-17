#include "../HeptaTrade/adapter_ib/ib_h1_native_stop_remaining_research.h"
#include <functional>
#include <iostream>
#include <stdexcept>
#include <string>

using namespace hepta_bracket_h1;
using namespace hepta_ib_h1_native_stop_remaining;

namespace {
void Need(bool v,const char* what){if(!v)throw std::runtime_error(what);}
FrozenBracket Frozen() {
    FrozenBracket f;
    f.originalCommandKey="original-full-remain";
    f.account="DU-SYNTHETIC";f.owner="owner-1";
    f.sessionId="session-1";f.executionDomain="PAPER";
    f.side="BUY";f.contractId=12087792;f.clientId=1701;
    f.parentOrderId=4501;f.childOrderId=4502;
    f.connectionEpoch=85;f.positionGeneration=9;
    f.ownerFenceToken=19;f.intentObservedAtMs=1792128100000ULL;
    f.quantity=25000;f.referencePrice=1.1205;
    f.stopPrice=1.11825;f.minTick=0.00005;
    return f;
}
IBEvent Open(const FrozenBracket& f,const BoundRefs& ids) {
    IBEvent e;
    e.type=IBEventType::OpenOrder;e.value="Submitted";
    e.id=f.childOrderId;e.connectionEpoch=f.connectionEpoch;
    e.brokerClientId=f.clientId;e.account=f.account;
    e.queueIngressMonotonicNs=1000;
    e.contract.symbol="EUR";e.contract.secType="CASH";
    e.contract.currency="USD";e.contract.exchange="IDEALPRO";
    e.order.action="SELL";e.order.orderType="STP";
    e.order.totalQuantity=f.quantity;e.order.orderRef=ids.childRef;
    e.order.auxPrice=f.stopPrice;
    auto& n=e.nativeOrder;n.present=true;
    n.contractId=f.contractId;n.orderId=f.childOrderId;
    n.permanentId=990001;n.parentId=f.parentOrderId;
    n.clientId=f.clientId;n.transmit=true;n.account=f.account;
    n.action="SELL";n.orderType="STP";n.timeInForce="GTC";
    n.orderRef=ids.childRef;n.ocaGroup=ids.ocaGroup;n.ocaType=1;
    n.triggerMethod=0;n.stopAuxPrice=f.stopPrice;n.totalQuantity=f.quantity;
    return e;
}
IBEvent Status(const FrozenBracket& f) {
    IBEvent e;
    e.type=IBEventType::OrderStatus;
    e.id=f.childOrderId;e.key="Submitted";
    e.value.clear();e.requestId=0;
    e.connectionEpoch=f.connectionEpoch;e.brokerClientId=f.clientId;
    e.account=f.account;e.queueIngressMonotonicNs=1100;
    e.nativeOrder.permanentId=990001;
    e.remainingQuantityKnown=true;
    e.number=0.0;e.number2=0.0;e.number3=f.quantity;
    return e;
}
void CheckAll() {
    const auto f=Frozen();
    BoundRefs ids;std::string why;
    Need(BuildFrozenRefs(f,ids,why),"H1 original refs missing");
    const auto open=Open(f,ids);
    auto base=Status(f);
    auto match=[&](const IBEvent& candidate) {
        return MatchesUnfilledStopStatus(open,candidate,f,ids,f.connectionEpoch);
    };
    Need(match(base),"exact original native stop unfilled status rejected");
    auto negative=[&](const char* label,const std::function<void(IBEvent&)>& edit) {
        auto sample=base;edit(sample);
        Need(!match(sample),label);
    };
    negative("missing status",[](IBEvent& e){e.type=IBEventType::None;});
    negative("completed mislabeled",[](IBEvent& e){e.key="Filled";});
    negative("cancelled still considered protected",[](IBEvent& e){e.key="Cancelled";});
    negative("held stop treated submitted",[](IBEvent& e){e.key="PreSubmitted";});
    negative("foreign client",[](IBEvent& e){++e.brokerClientId;});
    negative("foreign account",[](IBEvent& e){e.account="DU-FOREIGN";});
    negative("foreign order id",[](IBEvent& e){++e.id;});
    negative("foreign permanent id",[](IBEvent& e){++e.nativeOrder.permanentId;});
    negative("missing permanent id",[](IBEvent& e){e.nativeOrder.permanentId=0;});
    negative("SDK native orderStatus cannot be order-bearing",
        [](IBEvent& e){e.nativeOrder.present=true;});
    negative("changed epoch",[](IBEvent& e){++e.connectionEpoch;});
    negative("stale before openOrder",[](IBEvent& e){e.queueIngressMonotonicNs=999;});
    negative("missing ingress provenance",[](IBEvent& e){e.queueIngressMonotonicNs=0;});
    negative("partially filled",[](IBEvent& e){e.number2=1;e.number3-=1;});
    negative("partial remaining",[](IBEvent& e){e.number3-=1;});
    negative("overfilled",[](IBEvent& e){e.number2=1;});
    negative("zero remaining",[](IBEvent& e){e.number3=0;});
    negative("missing remaining provenance",[](IBEvent& e){e.remainingQuantityKnown=false;});
    negative("historical execution synthesis",[](IBEvent& e){e.requestId=7;});
    negative("execDetails synthetic status",[](IBEvent& e){e.value="execDetails";});
    negative("invalid quantity",[](IBEvent& e){e.number3=-1;});
    negative("nan remaining",[](IBEvent& e){e.number3=std::numeric_limits<double>::quiet_NaN();});
    Need(!match(IBEvent{}),"empty status unexpectedly qualified");
}
}
int main() {
    try {CheckAll();std::cout<<"h1_sdk_stop_filled_remaining_status=PASS\n";return 0;}
    catch(const std::exception& e){std::cerr<<"FAILED "<<e.what()<<'\n';return 1;}
}
