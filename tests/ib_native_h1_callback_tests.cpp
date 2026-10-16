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
    wrong=event;wrong.nativeOrder.contractId=f.contractId+1;
    rejectEvent(wrong,"mismatched native Contract.conId accepted");
    wrong=event;wrong.nativeOrder.clientId=f.clientId+1;
    rejectEvent(wrong,"mismatched native Broker client accepted");
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
    // A stopped/inactive original H1 bracket must NOT be a no-op state
    // that a future caller could misread as continuing WORKING protection.
    auto mustRejectOriginalStopState=[&](IBEvent changed,const char* label){
        auto sink=originalRecord;
        std::string refused;
        Require(ProjectFrozenH1WorkingStop(changed,f,original,sink,refused)==
                    Result::Rejected &&
                refused=="IB_H1_ORIGINAL_STOP_NOT_WORKING_OPEN_ORDER" &&
                sink.brokerBracket.version==0 && sink.eventType.empty(),label);
    };
    wrong=event;wrong.value="PreSubmitted";
    mustRejectOriginalStopState(wrong,"PreSubmitted H1 child treated as ignorable");
    wrong=event;wrong.value="Inactive";
    mustRejectOriginalStopState(wrong,"Inactive H1 stop treated as ignorable");
    wrong=event;wrong.value="Cancelled";
    mustRejectOriginalStopState(wrong,"Cancelled H1 stop treated as ignorable");
    wrong=event;wrong.value="Filled";
    mustRejectOriginalStopState(wrong,"Filled H1 stop treated as still working");
    wrong=event;wrong.type=IBEventType::OrderStatus;
    mustRejectOriginalStopState(wrong,"H1 status-only event treated as working");
    auto independent=event;
    independent.order.orderRef=refs.parentRef;
    auto ignoredRecord=originalRecord;std::string ignoredReason;
    Require(ProjectFrozenH1WorkingStop(independent,f,original,
                    ignoredRecord,ignoredReason)==Result::Ignored &&
            ignoredRecord.eventType.empty() &&
            ignoredRecord.brokerBracket.version==0,
            "unrelated H1 parent falsely treated as a stop failure");
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
static void SubmittedH1EchoAfterDurablePotentialSends() {
    using namespace hepta_native_bracket_projection;
    const auto f=Frozen();std::string why;
    BoundRefs refs;Require(BuildFrozenRefs(f,refs,why),"derive original role IDs");
    std::string mask="/tmp/hepta-h1-positive-send-XXXXXX";
    std::vector<char> tmp(mask.begin(),mask.end());tmp.push_back('\0');
    int fd=::mkstemp(tmp.data());Require(fd>=0,"create native send scratch");
    ::close(fd);Require(::unlink(tmp.data())==0,"unlink scratch before OMS init");
    OmsJournal journal;Require(journal.Init(tmp.data()),"native send OMS init");
    Reservation r;Require(ReservePair(journal,f,r,why),"native send reserve pair");
    Require(MarkPotentialSend(journal,r,false,why),"durable parent attempt missing");
    Require(MarkPotentialSend(journal,r,true,why),"durable child attempt missing");
    std::vector<OmsJournalEvent> rows;
    Require(journal.Replay([&](const OmsJournalEvent& e){rows.push_back(e);})==4,
            "original durable send records lost before Broker callback");
    auto record=OriginalCallbackRecord(f);
    const auto event=ExactNativeH1OpenOrder(f,refs);
    Require(ProjectFrozenH1WorkingStop(event,f,rows,record,why)==
                Result::WorkingWitnessCandidate,
            "durable original parent/child sends plus exact native stop echo cannot reconcile");
    Require(!r.sendAuthorized && !r.driverStopAllowed,
            "reconciled native callback minted order or stop authority");
    Reservation generic;
    Require(!ConfirmReplay(rows,f,generic,why)&&generic.uncertain,
            "generic OMS replay was improperly allowed to resend after Broker attempts");
    auto rejectLedger=[&](std::vector<OmsJournalEvent> evidence,
                          const char* label){
        auto dest=OriginalCallbackRecord(f);
        std::string rejected;
        Require(ProjectFrozenH1WorkingStop(event,f,evidence,dest,rejected)==
                    Result::Rejected && !rejected.empty() &&
                    dest.eventType.empty() && dest.brokerBracket.version==0,
                label);
    };
    auto incomplete=rows;incomplete.pop_back();
    rejectLedger(incomplete,"one durable send marker was treated as confirmed");
    auto duplicate=rows;duplicate.push_back(rows.back());
    rejectLedger(duplicate,"duplicate child send attempt accepted");
    auto reversed=rows;std::swap(reversed[2],reversed[3]);
    rejectLedger(reversed,"child marker preceding parent accepted");
    auto changed=rows;changed[3].status="Submitted";
    rejectLedger(changed,"pre-send marker forged Broker Submitted status");
    changed=rows;changed[3].venueCorrelationId=refs.parentCorrelation;
    rejectLedger(changed,"parent correlation substituted for child marker");
    changed=rows;changed[3].orderId=f.parentOrderId;
    rejectLedger(changed,"parent order ID substituted for child marker");
    changed=rows;changed[2].brokerCallbackType="openOrder";
    rejectLedger(changed,"synthetic send attempt masqueraded as native callback");
    changed=rows;changed[2].clientReqId="foreign-original";
    rejectLedger(changed,"foreign client_req_id alias on parent marker accepted");
    changed=rows;auto foreign=rows[3];foreign.reqId="foreign-original";
    foreign.clientReqId="foreign-original";changed.push_back(foreign);
    rejectLedger(changed,"foreign command borrowed original stop H1 correlation");
    changed=rows;auto unrelated=rows[2];
    unrelated.reqId="unrelated-original";
    unrelated.clientReqId="unrelated-original";
    unrelated.requestHash=HashCanonical("other-H1-group");
    unrelated.venueCorrelationId=HashCanonical("other-H1-parent-role");
    unrelated.orderId=f.childOrderId+200;
    unrelated.eventId="unrelated-send";
    changed.push_back(unrelated);
    auto disjoint=OriginalCallbackRecord(f);
    Require(ProjectFrozenH1WorkingStop(event,f,changed,disjoint,why)==
                Result::WorkingWitnessCandidate,
            "unrelated historical send improperly blocks exact original native echo");
    Require(::unlink(tmp.data())==0,"clean test OMS");
}
static void NativeEchoAfterProcessRestartCannotMintAnotherSend() {
    using namespace hepta_native_bracket_projection;
    const auto f=Frozen();std::string why;
    BoundRefs refs;Require(BuildFrozenRefs(f,refs,why),"restart H1 identities absent");
    std::string pattern="/tmp/hepta-h1-reopen-XXXXXX";
    std::vector<char> tmp(pattern.begin(),pattern.end());tmp.push_back('\0');
    const int fd=::mkstemp(tmp.data());Require(fd>=0,"create restart ledger");
    ::close(fd);Require(::unlink(tmp.data())==0,"prepare restart ledger");
    {
        OmsJournal first;
        Require(first.Init(tmp.data()),"initial restart OMS open");
        Reservation original;
        Require(ReservePair(first,f,original,why),"restart pair not fsynced");
        Require(MarkPotentialSend(first,original,false,why),"restart parent marker not durable");
        Require(MarkPotentialSend(first,original,true,why),"restart child marker not durable");
        Require(!original.sendAuthorized&&!original.driverStopAllowed,
                "durable send intents already minted risk authority");
    }
    {
        OmsJournal reopened;
        Require(reopened.Init(tmp.data()),"restart OMS re-open refused");
        std::vector<OmsJournalEvent> rows;
        Require(reopened.Replay([&](const OmsJournalEvent& e){
                    rows.push_back(e);})==4,
                "restart discarded original parent/child send markers");
        Reservation generic;
        Require(!ConfirmReplay(rows,f,generic,why)&&generic.uncertain,
                "restart incorrectly made uncertain Broker sends retryable");
        const auto epoch=f.connectionEpoch+1;
        auto broker=ExactNativeH1OpenOrder(f,refs);
        broker.connectionEpoch=epoch;
        broker.queueIngressMonotonicNs+=1000;
        auto receipt=OriginalCallbackRecord(f);
        receipt.brokerConnectionEpoch=epoch;
        Require(ProjectFrozenH1WorkingStop(broker,f,rows,receipt,why)==
                    Result::WorkingWitnessCandidate &&
                receipt.brokerBracket.version==2 &&
                receipt.brokerBracket.permanentOrderId==broker.nativeOrder.permanentId,
                "restart cannot associate same original H1 with fresh Broker epoch");
        Require(reopened.AppendDurableCritical(receipt),
                "recovered candidate callback not durably recorded");
        std::vector<OmsJournalEvent> later;
        Require(reopened.Replay([&](const OmsJournalEvent& e){
                    later.push_back(e);})==5 &&
                later.back().brokerBracket.version==2 &&
                later.back().brokerBracket.permanentOrderId==broker.nativeOrder.permanentId,
                "new-epoch Broker permanent ID lost in durable OMS replay");
        Require(!generic.sendAuthorized&&!generic.driverStopAllowed,
                "recovered H1 observation silently permitted Broker resend or driver stop");
    }
    Require(::unlink(tmp.data())==0,"restart test ledger cleanup");
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
    SubmittedH1EchoAfterDurablePotentialSends();
    NativeEchoAfterProcessRestartCannotMintAnotherSend();
    std::cout<<"ib_native_h1_callback=PASS\n";return 0;
  } catch(const std::exception& e){
    std::cerr<<"FAIL "<<e.what()<<'\n';return 1;
  }
}
