#include "../HeptaTrade/oms_journal.h"
#include "../HeptaTrade/execution/ib_paper_native_bracket_projection.h"
#include <cstdio>
#include <cstdlib>
#include <iostream>
#include <stdexcept>
#include <string>
#include <vector>
#include <unistd.h>

namespace {
void Require(bool okay,const char* reason) {
    if (!okay) throw std::runtime_error(reason);
}
OmsJournalEvent Bracket() {
    OmsJournalEvent evt;
    evt.eventType="broker_open_order_bracket";
    evt.brokerCallbackType="openOrder";
    evt.tsMs=1792080000000LL;
    evt.orderId=4502;
    evt.reqId="original-001";
    evt.clientReqId=evt.reqId;
    evt.eventId="callback-test-entry-001-79-888871";
    evt.account="DU-SYNTHETIC";
    evt.venue="IB";
    evt.executionDomain="PAPER";
    evt.source="agent:execution-test";
    evt.brokerConnectionEpoch=79;
    evt.brokerClientId=1772;
    evt.status="Submitted";
    evt.side="SELL";
    evt.qty=89200;
    evt.price=1.11830;
    auto& b=evt.brokerBracket;
    b.version=1;
    b.contractId=12087792;
    b.parentOrderId=4501;
    b.permanentOrderId=888871;
    b.transmit=1;
    b.ocaType=1;
    b.triggerMethod=0;
    b.orderRef="HEPTA-STOP-callback-test-entry-001";
    b.ocaGroup="HEPTA-FX-OCA-callback-test-entry-001";
    b.timeInForce="GTC";
    b.quantity=89200;
    b.stopAuxPrice=1.11830;
    return evt;
}
void RefuseTamper(std::string raw, const std::string& old,
                  const std::string& replacement, const char* reason) {
    const auto at=raw.find(old);
    Require(at!=std::string::npos,"mutation fixture field not found");
    raw.replace(at,old.size(),replacement);
    OmsJournalEvent event;
    Require(!OmsJournal::ParseJsonLine(raw,event),reason);
}
IBEvent OriginalSdkWorkingStop() {
    IBEvent e;
    e.type=IBEventType::OpenOrder;
    e.id=4502;
    e.account="DU-SYNTHETIC";
    e.brokerClientId=1772;
    e.connectionEpoch=79;
    e.queueIngressMonotonicNs=987000;
    e.value="Submitted";
    e.contract.symbol="EUR";
    e.contract.currency="USD";
    e.contract.secType="CASH";
    e.contract.exchange="IDEALPRO";
    e.order.action="SELL";
    e.order.orderType="STP";
    e.order.orderRef="HEPTA-STOP-callback-test-entry-001";
    e.order.totalQuantity=89200;
    e.order.auxPrice=1.11830;
    auto& n=e.nativeOrder;
    n.present=true;
    n.contractId=12087792;
    n.orderId=4502;
    n.permanentId=888871;
    n.parentId=4501;
    n.clientId=1772;
    n.transmit=true;
    n.account=e.account;
    n.action=e.order.action;
    n.orderType=e.order.orderType;
    n.timeInForce="GTC";
    n.orderRef=e.order.orderRef;
    n.ocaGroup="HEPTA-FX-OCA-callback-test-entry-001";
    n.ocaType=1;
    n.triggerMethod=0;
    n.totalQuantity=e.order.totalQuantity;
    n.stopAuxPrice=e.order.auxPrice;
    return e;
}
void ActualSharedProducerRejectsSpoofedEvents() {
    using namespace hepta_native_bracket_projection;
    OmsJournalEvent seed=Bracket();
    const auto event=OriginalSdkWorkingStop();
    auto makeRecord=[&]() {
        auto r=seed;
        r.eventType.clear();r.brokerCallbackType.clear();
        r.eventId.clear();r.brokerBracket=IbBracketCallbackEvidence{};
        r.qty=0;r.price=0;
        return r;
    };
    auto rec=makeRecord();
    std::string why;
    Require(ProjectOriginalWorkingStop(event,rec,why)==
                Result::WorkingWitnessCandidate,
            "real runtime callback projector refused exact SDK echo");
    Require(rec.eventType=="broker_open_order_bracket" &&
            rec.brokerBracket.ValidShape() &&
            rec.brokerBracket.parentOrderId==4501 &&
            rec.brokerBracket.permanentOrderId==888871 &&
            rec.eventId=="HEPTA-STOP-callback-test-entry-001-79-888871",
            "runtime projector dropped original native fields");
    auto fake=event;
    fake.nativeOrder.present=false;
    rec=makeRecord();
    Require(ProjectOriginalWorkingStop(fake,rec,why)==Result::Rejected,
            "orderStatus partial evidence masqueraded as working child");
    fake=event;fake.value="PreSubmitted";
    rec=makeRecord();
    Require(ProjectOriginalWorkingStop(fake,rec,why)==Result::Ignored,
            "held TWS order classified as working");
    fake=event;fake.nativeOrder.ocaGroup="different";
    rec=makeRecord();
    Require(ProjectOriginalWorkingStop(fake,rec,why)==Result::Rejected,
            "foreign OCA admitted into owned OMS callback");
    fake=event;fake.nativeOrder.parentId++;
    rec=makeRecord();
    // Shape alone cannot verify WHICH prior original parent command exists;
    // the exact broker owner/group matcher must separately check the parent.
    Require(ProjectOriginalWorkingStop(fake,rec,why)==
                Result::WorkingWitnessCandidate,
            "positive parent ID shape unexpectedly rejected");
    fake=event;fake.nativeOrder.permanentId=0;
    rec=makeRecord();
    Require(ProjectOriginalWorkingStop(fake,rec,why)==Result::Rejected,
            "missing permanent ID emitted as positive OMS witness");
    fake=event;fake.nativeOrder.stopAuxPrice=1.11831;
    rec=makeRecord();
    Require(ProjectOriginalWorkingStop(fake,rec,why)==Result::Rejected,
            "wrong 3188 tick accepted");
    fake=event;fake.account="DU-OTHER";
    rec=makeRecord();
    Require(ProjectOriginalWorkingStop(fake,rec,why)==Result::Rejected,
            "other account callback recorded on protected original owner");
}
void NativeRoundTripAndLegacyCompatibility() {
    const char* env=std::getenv("TMPDIR");
    const std::string base=env ? env : "/tmp";
    const std::string name=base+"/hepta-bracket-native-journal-XXXXXX";
    std::vector<char> temp(name.begin(),name.end());
    temp.push_back('\0');
    char* directory=::mkdtemp(temp.data());
    Require(directory!=nullptr,"scratch ledger unavailable");
    const std::string folder(directory);
    const std::string path=folder+"/oms.jsonl";
    std::string raw;
    {
        OmsJournal journal;
        Require(journal.Init(path),"journal init failed");
        Require(journal.Append(Bracket()),"valid SDK bracket callback not durable");
        Require(journal.GetHealthSnapshot().durableSyncWrites>=1,
            "broker protective callback not durably synced");
        auto unsafe=Bracket();unsafe.brokerBracket.permanentOrderId=0;
        Require(!journal.Append(unsafe),"unknown Permanent ID persisted as protected");
        unsafe=Bracket();unsafe.status="PreSubmitted";
        Require(!journal.Append(unsafe),"pre-submitted stop persisted as working");
        Require(journal.GetHealthSnapshot().currentRecords==1,
            "failed callback wrote a partial or fake event");
        int count=0;
        Require(journal.Replay([&](const OmsJournalEvent& event){
            ++count; raw=event.rawLine;
            Require(event.eventType=="broker_open_order_bracket" &&
                    event.brokerBracket.ValidShape() &&
                    event.brokerBracket.parentOrderId==4501 &&
                    event.brokerBracket.permanentOrderId==888871 &&
                    event.brokerBracket.ocaType==1 &&
                    event.brokerBracket.orderRef==Bracket().brokerBracket.orderRef &&
                    event.brokerBracket.stopAuxPrice==1.11830,
                    "original native bracket callback drift");
        })==1 && count==1,"bracket broker callback recovered incorrectly");
    }
    {
        OmsJournal reopened;
        Require(reopened.Init(path),"restart reopen failed");
        int seen=0;
        Require(reopened.Replay([&](const OmsJournalEvent& record){
            ++seen;Require(record.brokerBracket.ValidShape(),
                "restart lost order-group provenance");
        })==1 && seen==1,"restart did not retain exact callback");
    }
    // Each corruption is a different real broker order's authority. An
    // unknown or partially recorded group must not parse as a valid stop.
    RefuseTamper(raw,"\"broker_bracket_parent_order_id\":4501",
                 "\"broker_bracket_parent_order_id\":0",
                 "parent lost during replay");
    RefuseTamper(raw,"\"broker_bracket_permanent_order_id\":888871",
                 "\"broker_bracket_permanent_order_id\":0",
                 "Permanent ID missing");
    RefuseTamper(raw,"\"broker_bracket_stop_aux_price\":1.1183",
                 "\"broker_bracket_stop_aux_price\":1.11831",
                 "off-market-rule stop accepted");
    RefuseTamper(raw,"\"broker_bracket_tif\":\"GTC\"",
                 "\"broker_bracket_tif\":\"DAY\"",
                 "wrong TIF accepted");
    RefuseTamper(raw,"\"broker_bracket_order_ref\":\"HEPTA-STOP-callback-test-entry-001\"",
                 "\"broker_bracket_order_ref\":\"HEPTA-STOP-alien\"",
                 "foreign original orderRef accepted");
    RefuseTamper(raw,"\"broker_bracket_oca_type\":1",
                 "\"broker_bracket_oca_type\":0",
                 "unblocked ordinary exit OCA accepted");
    RefuseTamper(raw,"\"broker_bracket_order_ref\":\"HEPTA-STOP-callback-test-entry-001\"",
                 "\"broker_bracket_order_ref\":\"HEPTA-STOP-callback-test-entry-001-verylong-exceeding-limit\"",
                 "longer-than-45-byte IB orderRef accepted");
    RefuseTamper(raw,"\"broker_bracket_order_ref\":\"HEPTA-STOP-callback-test-entry-001\"",
                 "\"broker_bracket_order_ref\":\"HEPTA-STOP-invalid:entry\"",
                 "noncanonical IB orderRef accepted");
    RefuseTamper(raw,"\"event\":\"broker_open_order_bracket\"",
                 "\"event\":\"broker_completed_order\"",
                 "native group replayed as a completed status");
    RefuseTamper(raw,"\"status\":\"Submitted\"",
                 "\"status\":\"PreSubmitted\"",
                 "untransmitted TWS order treated as protection");
    RefuseTamper(raw,"\"broker_bracket_version\":1",
                 "\"broker_bracket_version\":null",
                 "malformed version accepted");
    RefuseTamper(raw,"\"broker_bracket_quantity\":89200",
                 "\"broker_bracket_quantity\":89200,\"broker_bracket_extraneous\":1",
                 "unknown broker-native attribute accepted");
    {
        OmsJournalEvent legacy;
        Require(OmsJournal::ParseJsonLine(
                "{\"schema_version\":4,\"event\":\"place_sent\",\"order_id\":12}",
                legacy) && legacy.brokerBracket.version==0,
            "legacy OMS event incorrectly requires native bracket fields");
    }
    Require(::unlink(path.c_str())==0 && ::rmdir(folder.c_str())==0,
            "private OMS scratch cleanup failed");
}
} // namespace

int main() {
    try {
        NativeRoundTripAndLegacyCompatibility();
        ActualSharedProducerRejectsSpoofedEvents();
        std::cout<<"ib_native_bracket_oms_v4_roundtrip=PASS\n";
        return 0;
    } catch (const std::exception& e) {
        std::cerr<<"FAILED: "<<e.what()<<'\n';
        return 1;
    }
}
