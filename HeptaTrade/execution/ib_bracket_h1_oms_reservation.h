#pragma once
// Owner-bound, non-dispatching research: reserve a H1 parent/stop pair in
// the EXISTING OmsJournal before broker mutation. No sender or risk grant.
// The durability pair can leave a prefix on failure; on replay, any incomplete
// group is permanently uncertain and MUST NOT be retransmitted.
#include "ib_bracket_h1_identity.h"
#include "../oms_journal.h"
#include <string>
#include <vector>
namespace hepta_bracket_h1 {
struct Reservation {
    FrozenBracket frozen;
    BoundRefs ids;
    bool replayComplete=false;
    bool uncertain=false;
    bool sendAuthorized=false;
    bool driverStopAllowed=false;
};
inline OmsJournalEvent Intent(const FrozenBracket& f,const BoundRefs& b,bool child) {
    OmsJournalEvent e;
    e.eventType="order_intent";
    e.tsMs=static_cast<long long>(f.intentObservedAtMs);
    e.orderId=child?f.childOrderId:f.parentOrderId;
    e.reqId=f.originalCommandKey;
    e.eventId=(child?"stop:":"parent:") + b.groupCorrelation;
    e.account=f.account;
    e.source="SHADOW_NO_BROKER_EGRESS";
    e.executionDomain=f.executionDomain;
    e.venue="IB";
    e.instrument="EUR.USD";
    e.side=child?(f.side=="BUY"?"SELL":"BUY"):f.side;
    e.qty=f.quantity;
    e.price=child?f.stopPrice:f.referencePrice;
    e.requestHash=b.groupCorrelation;
    e.venueCorrelationId=child?b.childCorrelation:b.parentCorrelation;
    return e;
}
inline bool ReservePair(OmsJournal& journal, const FrozenBracket& f,
                        Reservation& out,std::string& reason) {
    out=Reservation{};
    BoundRefs b;
    if(!BuildFrozenRefs(f,b,reason)) return false;
    // No external broker side effect happens before this durable boundary.
    const auto result=journal.AppendDurablePair(Intent(f,b,false),Intent(f,b,true));
    if(result!=OmsJournal::DurablePairResult::Committed){
        reason="H1_PAIR_DURABILITY_UNCERTAIN_NO_RETRY";
        out.uncertain=true;return false;
    }
    out.frozen=f;out.ids=b;out.replayComplete=true;
    // Durable intent != permission to place orders or stop Driver.
    out.sendAuthorized=false;out.driverStopAllowed=false;
    reason.clear();return true;
}
// The only effect here is a durable PRE-SEND marker. Actual sender remains
// absent. Once even a potential send is in the journal, recovery must keep
// UNKNOWN rather than creating another parent/child order.
inline bool MarkPotentialSend(OmsJournal& journal, const Reservation& r,
                              bool child, std::string& reason) {
    if(!r.replayComplete || r.uncertain || r.sendAuthorized ||
       r.driverStopAllowed) {
        reason="H1_PAIR_NO_SAFE_DURABLE_PRE_SEND_SCOPE";return false;
    }
    auto e=Intent(r.frozen,r.ids,child);
    e.eventType="place_send_attempt";
    e.eventId=(child?"possible-child:":"possible-parent:")+r.ids.groupCorrelation;
    e.status="POSSIBLY_SENT";
    // A successful ordinary Append can mean "queued" when critical fsync is
    // disabled by host configuration. The future Broker send needs the
    // unconditionally durable single-record barrier before ANY venue effect.
    if(!journal.AppendDurableCritical(e)){
        reason="H1_SEND_ATTEMPT_DURABILITY_UNKNOWN";return false;
    }
    reason.clear();return true;
}
inline bool ConfirmReplay(const std::vector<OmsJournalEvent>& records,
                         const FrozenBracket& f, Reservation& out,
                         std::string& reason) {
    out=Reservation{};
    BoundRefs b;
    if(!BuildFrozenRefs(f,b,reason))return false;
    const OmsJournalEvent parent=Intent(f,b,false),child=Intent(f,b,true);
    unsigned int matchedParent=0,matchedChild=0;
    for(const auto& e:records) {
        if(e.reqId!=f.originalCommandKey)continue;
        if(e.eventType=="place_send_attempt" &&
           e.requestHash==b.groupCorrelation &&
           e.account==f.account &&
           e.status=="POSSIBLY_SENT") {
            out.uncertain=true;
            reason="H1_BROKER_SEND_UNCERTAIN_REQUIRES_ORIGINAL_BROKER_RECOVERY";
            return false;
        }
        if(e.eventType!="order_intent" ||
           e.requestHash!=b.groupCorrelation ||
           e.account!=f.account || e.executionDomain!="PAPER") {
            out.uncertain=true;reason="H1_UNEXPECTED_ORIGINAL_COMMAND_EVENT";return false;
        }
        // Shadow order_intent cannot carry positive Broker callback or
        // Submitted/filled evidence. Only an independently matched broker
        // callback may ever establish working protection; no sender exists here.
        if(!e.status.empty() || !e.reason.empty() ||
           !e.brokerCallbackType.empty() || e.brokerClientId!=-1 ||
           e.brokerConnectionEpoch!=0 || !e.brokerExecutionId.empty() ||
           e.brokerBracket.version!=0) {
            out.uncertain=true;
            reason="H1_SHADOW_INTENT_CONTAINS_UNATTESTED_BROKER_EVIDENCE";
            return false;
        }
        bool isParent=e.tsMs==parent.tsMs && e.source==parent.source && e.venue==parent.venue && e.instrument==parent.instrument && e.orderId==parent.orderId &&
            e.venueCorrelationId==parent.venueCorrelationId &&
            e.eventId==parent.eventId && e.side==parent.side &&
            e.qty==parent.qty && e.price==parent.price;
        bool isChild=e.tsMs==child.tsMs && e.source==child.source && e.venue==child.venue && e.instrument==child.instrument && e.orderId==child.orderId &&
            e.venueCorrelationId==child.venueCorrelationId &&
            e.eventId==child.eventId && e.side==child.side &&
            e.qty==child.qty && e.price==child.price;
        if(!isParent&&!isChild) {
            out.uncertain=true;reason="H1_FROZEN_ORDER_BINDING_MISMATCH";return false;
        }
        if(isParent)++matchedParent;else ++matchedChild;
    }
    if(matchedParent!=1 || matchedChild!=1) {
        out.uncertain=true;reason="H1_ORDER_PAIR_MISSING_OR_DUPLICATE";return false;
    }
    out.frozen=f;out.ids=b;out.replayComplete=true;
    reason.clear();return true;
}
} // namespace hepta_bracket_h1
