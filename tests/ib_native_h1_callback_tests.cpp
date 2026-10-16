#include "../HeptaTrade/oms_journal.h"
#include "../HeptaTrade/execution/ib_bracket_h1_identity.h"
#include "../HeptaTrade/execution/ib_paper_native_bracket_projection.h"
#include <vector>
#include <unistd.h>
#include <cstdlib>
#include <iostream>
#include <stdexcept>
using namespace hepta_bracket_h1;
static void Require(bool yes,const char* reason){if(!yes)throw std::runtime_error(reason);}
static FrozenBracket Frozen(){
    FrozenBracket f;
    f.originalCommandKey="original-h1-callback-001";
    f.account="DU-SYNTHETIC";f.owner="original-owner";
    f.sessionId="session-h1-callback";f.executionDomain="PAPER";
    f.side="BUY";f.contractId=12087792;f.clientId=991;
    f.parentOrderId=4501;f.childOrderId=4502;
    f.connectionEpoch=79;f.positionGeneration=101;f.ownerFenceToken=22;
    f.intentObservedAtMs=100000;f.quantity=89200;
    f.referencePrice=1.12050;f.stopPrice=1.11825;f.minTick=0.00005;
    return f;
}
static OmsJournalEvent OriginalCallbackRecord(const FrozenBracket& f){
    OmsJournalEvent r;
    r.reqId=f.originalCommandKey;r.clientReqId=f.originalCommandKey;
    r.account=f.account;r.traceId=f.sessionId;r.strategy=f.owner;
    r.source="agent:"+f.owner;r.executionDomain="PAPER";
    r.venue="IB";r.instrument="EUR.USD";
    r.orderId=f.childOrderId;r.tsMs=static_cast<long long>(f.intentObservedAtMs);
    r.brokerClientId=f.clientId;r.brokerConnectionEpoch=f.connectionEpoch;
    return r;
}
static IBEvent ExactNativeH1OpenOrder(const FrozenBracket& f,
                                      const BoundRefs& refs){
    IBEvent event;
    event.type=IBEventType::OpenOrder;
    event.value="Submitted";event.id=f.childOrderId;
    event.account=f.account;event.brokerClientId=f.clientId;
    event.connectionEpoch=f.connectionEpoch;
    event.queueIngressMonotonicNs=123456;
    event.contract.symbol="EUR";event.contract.currency="USD";
    event.contract.secType="CASH";event.contract.exchange="IDEALPRO";
    event.order.action=f.side=="BUY"?"SELL":"BUY";
    event.order.orderType="STP";event.order.orderRef=refs.childRef;
    event.order.totalQuantity=f.quantity;event.order.auxPrice=f.stopPrice;
    auto& w=event.nativeOrder;
    w.present=true;w.contractId=f.contractId;
    w.orderId=f.childOrderId;w.parentId=f.parentOrderId;
    w.permanentId=888871;w.clientId=f.clientId;w.transmit=true;
    w.account=f.account;w.action=event.order.action;
    w.orderType="STP";w.timeInForce="GTC";
    w.orderRef=refs.childRef;w.ocaGroup=refs.ocaGroup;
    w.ocaType=1;w.triggerMethod=0;
    w.totalQuantity=f.quantity;w.stopAuxPrice=f.stopPrice;
    return event;
}
static void ExactOriginalH1CallbackAndDurableOms(){
    using namespace hepta_native_bracket_projection;
    const auto f=Frozen();std::string reason;
    BoundRefs refs;Require(BuildFrozenRefs(f,refs,reason),"build frozen H1");
    std::string pattern="/tmp/hepta-h1-openorder-XXXXXX";
    std::vector<char> name(pattern.begin(),pattern.end());name.push_back('\0');
    const int fd=::mkstemp(name.data());Require(fd>=0,"scratch file");
    ::close(fd);Require(::unlink(name.data())==0,"scratch file create");
    const std::string file(name.data());
    OmsJournal journal;Require(journal.Init(file),"exact OMS init failed");
    Reservation reserved;
    Require(ReservePair(journal,f,reserved,reason),"durable frozen H1 pair missing");
    std::vector<OmsJournalEvent> original;
    Require(journal.Replay([&](const OmsJournalEvent& e){original.push_back(e);})==2,
            "original H1 durable OMS proof missing");
    const auto event=ExactNativeH1OpenOrder(f,refs);
    const auto originalRecord=OriginalCallbackRecord(f);
    OmsJournalEvent accepted=originalRecord;
    Require(ProjectFrozenH1WorkingStop(event,f,original,accepted,reason)==
                Result::WorkingWitnessCandidate,
            "exact H1 original broker OpenOrder was not projected");
    Require(accepted.brokerBracket.version==2 &&
            accepted.brokerBracket.ValidShape() &&
            accepted.brokerBracket.orderRef==refs.childRef &&
            accepted.brokerBracket.ocaGroup==refs.ocaGroup &&
            accepted.requestHash==refs.groupCorrelation &&
            accepted.venueCorrelationId==refs.childCorrelation,
            "H1 role/parent/native callback identity was lost");
    Require(journal.AppendDurableCritical(accepted),"H1 original callback not durably stored");
    std::vector<OmsJournalEvent> reopened;
    Require(journal.Replay([&](const OmsJournalEvent& e){reopened.push_back(e);})==3,
            "H1 callback record did not roundtrip through native OMS");
    Require(reopened[2].brokerBracket.version==2 &&
            reopened[2].brokerBracket.orderRef==refs.childRef &&
            reopened[2].brokerBracket.ocaGroup==refs.ocaGroup &&
            reopened[2].brokerBracket.permanentOrderId==888871,
            "H1 original native callback lost its durable permanent ID");
    auto rejectEvent=[&](const IBEvent& test,const char* label){
        auto output=originalRecord;
        Require(ProjectFrozenH1WorkingStop(test,f,original,output,reason)!=
                    Result::WorkingWitnessCandidate &&
                    output.brokerBracket.version==0 && output.eventType.empty(),label);
    };
    auto wrong=event;wrong.nativeOrder.ocaGroup=refs.parentRef;
    rejectEvent(wrong,"different H1 OCA accepted");
    wrong=event;wrong.nativeOrder.permanentId=0;
    rejectEvent(wrong,"missing permanent ID accepted");
    wrong=event;wrong.nativeOrder.parentId++;
    rejectEvent(wrong,"different parent ID accepted");
    wrong=event;wrong.nativeOrder.transmit=false;
    rejectEvent(wrong,"held untransmitted child accepted");
    wrong=event;wrong.connectionEpoch++;
    rejectEvent(wrong,"different Broker callback epoch accepted");
    wrong=event;wrong.queueIngressMonotonicNs=0;
    rejectEvent(wrong,"no native queue ingress identity accepted");
    wrong=event;wrong.type=IBEventType::OrderStatus;
    rejectEvent(wrong,"numeric OrderStatus became Broker OpenOrder");
    wrong=event;wrong.value="PreSubmitted";
    rejectEvent(wrong,"PreSubmitted became active stop");
    auto output=originalRecord;output.traceId="foreign-session";
    Require(ProjectFrozenH1WorkingStop(event,f,original,output,reason)!=
                Result::WorkingWitnessCandidate && output.brokerBracket.version==0,
            "unowned source session got Broker stop witness");
    // An already bound callback record must not be silently overwritten
    // to launder a different group, child, callback or venue record.
    auto rejectRecord=[&](OmsJournalEvent forged,const char* label){
        auto before=forged;
        Require(ProjectFrozenH1WorkingStop(event,f,original,forged,reason)==
                    Result::Rejected &&
                    forged.eventType==before.eventType &&
                    forged.requestHash==before.requestHash &&
                    forged.venueCorrelationId==before.venueCorrelationId &&
                    forged.eventId==before.eventId &&
                    forged.brokerBracket.version==0,label);
    };
    auto wrongRecord=originalRecord;
    wrongRecord.requestHash=refs.parentCorrelation;
    rejectRecord(wrongRecord,"existing other-group hash silently overwritten");
    wrongRecord=originalRecord;
    wrongRecord.venueCorrelationId=refs.parentCorrelation;
    rejectRecord(wrongRecord,"existing parent correlation silently overwritten");
    wrongRecord=originalRecord;
    wrongRecord.eventId="old-unrelated-broker-event";
    rejectRecord(wrongRecord,"existing event ID silently overwritten");
    wrongRecord=originalRecord;
    wrongRecord.status="Filled";
    rejectRecord(wrongRecord,"prior terminal status silently overwritten");
    wrongRecord=originalRecord;
    wrongRecord.brokerCallbackType="completedOrder";
    rejectRecord(wrongRecord,"existing callback type silently overwritten");
    auto partial=original;partial.pop_back();output=originalRecord;
    Require(ProjectFrozenH1WorkingStop(event,f,partial,output,reason)==
                Result::Rejected && output.brokerBracket.version==0,
            "incomplete durable parent/child OMS accepted");
    auto possible=original;
    auto send=Intent(f,refs,true);
    send.eventType="bracket_child_send_attempt";send.status="POSSIBLY_SENT";
    possible.push_back(send);output=originalRecord;
    Require(ProjectFrozenH1WorkingStop(event,f,possible,output,reason)==
                Result::Rejected && output.brokerBracket.version==0,
            "unknown Broker send was converted to working stop");
    Require(::unlink(file.c_str())==0,"scratch journal cleanup failed");
}
int main(){
  try {
    const auto f=Frozen();BoundRefs refs;std::string why;
    Require(BuildFrozenRefs(f,refs,why),"exact frozen H1 role derivation refused");
    IbBracketCallbackEvidence evidence;
    evidence.version=2;evidence.contractId=f.contractId;
    evidence.parentOrderId=f.parentOrderId;evidence.permanentOrderId=888871;
    evidence.transmit=1;evidence.ocaType=1;evidence.triggerMethod=0;
    evidence.orderRef=refs.childRef;evidence.ocaGroup=refs.ocaGroup;
    evidence.timeInForce="GTC";evidence.quantity=f.quantity;
    evidence.stopAuxPrice=f.stopPrice;
    Require(evidence.ValidShape(),
      "native H1 protective child is rejected by original broker witness schema");
    auto malformed=evidence;malformed.orderRef.back()='B';
    Require(!malformed.ValidShape(),"noncanonical H1 trailing sextet accepted");
    malformed=evidence;malformed.ocaGroup=refs.parentRef;
    Require(malformed.ValidShape(),"H1 shape improperly claims exact OCA binding");
    malformed=evidence;malformed.version=1;
    Require(!malformed.ValidShape(),"H1 bypassed legacy v1 marker");
    ExactOriginalH1CallbackAndDurableOms();
    std::cout<<"ib_native_h1_callback=PASS\n";return 0;
  } catch(const std::exception& e){
    std::cerr<<"FAIL "<<e.what()<<'\n';return 1;
  }
}
