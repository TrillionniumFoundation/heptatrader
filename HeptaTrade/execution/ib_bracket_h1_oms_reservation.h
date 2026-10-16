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
    // Strictly previous *observation* receipts; neither grants sending.
    std::uint64_t latestNativeOpenOrderEpoch=0;
    long latestNativeStopPermanentId=0;
};
inline OmsJournalEvent Intent(const FrozenBracket& f,const BoundRefs& b,bool child) {
    OmsJournalEvent e;
    e.eventType=child?"bracket_child_reserved":"bracket_parent_reserved";
    e.tsMs=static_cast<long long>(f.intentObservedAtMs);
    e.orderId=child?f.childOrderId:f.parentOrderId;
    e.reqId=f.originalCommandKey;
    e.eventId=(child?"stop:":"parent:") + b.groupCorrelation;
    e.account=f.account;
    e.traceId=f.sessionId;
    e.strategy=f.owner;
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
    e.eventType=child?"bracket_child_send_attempt":"bracket_parent_send_attempt";
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
        // The legacy OMS has BOTH req_id and client_req_id. A duplicate
        // command under the other alias is not a foreign, ignorable record.
        // In particular, never hide a possible native send by changing only
        // its req_id while retaining the original client_req_id.
        const bool originalByEitherAlias =
            e.reqId==f.originalCommandKey ||
            e.clientReqId==f.originalCommandKey;
        if(!originalByEitherAlias) {
            // Unrelated original commands are ignorable only when they do
            // not borrow this pair's immutable H1 venue identities.
            if(e.requestHash==b.groupCorrelation ||
               e.requestHash==b.parentCorrelation ||
               e.requestHash==b.childCorrelation ||
               e.requestHash==b.ocaCorrelation ||
               e.venueCorrelationId==b.groupCorrelation ||
               e.venueCorrelationId==parent.venueCorrelationId ||
               e.venueCorrelationId==child.venueCorrelationId ||
               e.venueCorrelationId==b.ocaCorrelation) {
                out.uncertain=true;
                reason="H1_FOREIGN_COMMAND_REUSES_FROZEN_IDENTITY";
                return false;
            }
            if(e.account==f.account && e.executionDomain=="PAPER" &&
               e.venue=="IB" &&
               (e.orderId==parent.orderId ||
                e.orderId==child.orderId)) {
                out.uncertain=true;
                reason="H1_FOREIGN_COMMAND_REUSES_NATIVE_ORDER_ID";
                return false;
            }
            continue;
        }
        if(e.reqId!=f.originalCommandKey ||
           (!e.clientReqId.empty() && e.clientReqId!=f.originalCommandKey)) {
            out.uncertain=true;
            reason="H1_ORIGINAL_COMMAND_REQUEST_ALIAS_MISMATCH";
            return false;
        }
        if((e.eventType=="bracket_parent_send_attempt" ||
            e.eventType=="bracket_child_send_attempt") &&
           e.requestHash==b.groupCorrelation &&
           e.account==f.account &&
           e.status=="POSSIBLY_SENT") {
            out.uncertain=true;
            reason="H1_BROKER_SEND_UNCERTAIN_REQUIRES_ORIGINAL_BROKER_RECOVERY";
            return false;
        }
        if((e.eventType!="bracket_parent_reserved" &&
            e.eventType!="bracket_child_reserved") ||
           e.requestHash!=b.groupCorrelation ||
           e.account!=f.account || e.traceId!=f.sessionId ||
           e.strategy!=f.owner || e.executionDomain!="PAPER") {
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
        bool isParent=e.eventType=="bracket_parent_reserved" &&
            e.tsMs==parent.tsMs && e.source==parent.source && e.venue==parent.venue && e.instrument==parent.instrument && e.orderId==parent.orderId &&
            e.venueCorrelationId==parent.venueCorrelationId &&
            e.eventId==parent.eventId && e.side==parent.side &&
            e.qty==parent.qty && e.price==parent.price;
        bool isChild=e.eventType=="bracket_child_reserved" &&
            e.tsMs==child.tsMs && e.source==child.source && e.venue==child.venue && e.instrument==child.instrument && e.orderId==child.orderId &&
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
// A real Broker stop OpenOrder can arrive AFTER two durable POSSIBLY_SENT
// markers. These markers stay UNKNOWN for resend/restart decisions; however,
// the exact native callback may become a candidate witness if BOTH markers
// and the original pair belong to one immutable H1 group in original order.
// No sender, maintenance permit, or independent broker protection is inferred.
inline bool ConfirmReplayForOriginalNativeEcho(
    const std::vector<OmsJournalEvent>& records, const FrozenBracket& frozen,
    Reservation& out, std::string& reason)
{
    out=Reservation{};
    BoundRefs refs;
    if(!BuildFrozenRefs(frozen,refs,reason))return false;
    std::vector<OmsJournalEvent> beforeNative;
    beforeNative.reserve(records.size());
    unsigned int stage=0;
    std::uint64_t lastObservedEpoch=0;
    long stopPermanentId=0;
    for(const auto& e:records) {
        const bool original=e.reqId==frozen.originalCommandKey ||
                            e.clientReqId==frozen.originalCommandKey;
        if(!original) {
            // ConfirmReplay will independently refuse a foreign command
            // borrowing either native ID or any frozen H1 role correlation.
            beforeNative.push_back(e);
            continue;
        }
        if(e.eventType=="broker_open_order_bracket" && stage==4) {
            // A prior native OpenOrder candidate may already have been
            // fsynced during an earlier connection. It is NOT a placement
            // attempt, but it must be fully source/role/epoch attested before
            // a NEW connection can correlate the original working stop.
            const auto& b=e.brokerBracket;
            const std::string expectedEventId=refs.childRef+"-"+
                std::to_string(e.brokerConnectionEpoch)+"-"+
                std::to_string(b.permanentOrderId);
            if(e.reqId!=frozen.originalCommandKey ||
               (!e.clientReqId.empty() &&
                e.clientReqId!=frozen.originalCommandKey) ||
               e.tsMs!=static_cast<long long>(frozen.intentObservedAtMs) ||
               e.account!=frozen.account || e.traceId!=frozen.sessionId ||
               e.strategy!=frozen.owner ||
               e.source!="agent:"+frozen.owner ||
               e.executionDomain!="PAPER" || e.venue!="IB" ||
               e.instrument!="EUR.USD" || e.orderId!=frozen.childOrderId ||
               e.side!=(frozen.side=="BUY"?"SELL":"BUY") ||
               e.qty!=frozen.quantity || e.price!=frozen.stopPrice ||
               e.requestHash!=refs.groupCorrelation ||
               e.venueCorrelationId!=refs.childCorrelation ||
               e.eventId!=expectedEventId ||
               e.brokerCallbackType!="openOrder" ||
               e.status!="Submitted" || !e.reason.empty() ||
               !e.brokerExecutionId.empty() ||
               e.brokerClientId!=frozen.clientId ||
               e.brokerConnectionEpoch<frozen.connectionEpoch ||
               e.brokerConnectionEpoch<=lastObservedEpoch ||
               b.version!=2 || !b.ValidShape() ||
               b.contractId!=frozen.contractId ||
               b.parentOrderId!=frozen.parentOrderId ||
               b.permanentOrderId<=0 ||
               (stopPermanentId>0 &&
                b.permanentOrderId!=stopPermanentId) ||
               b.orderRef!=refs.childRef || b.ocaGroup!=refs.ocaGroup ||
               b.transmit!=1 || b.ocaType!=1 || b.triggerMethod!=0 ||
               b.timeInForce!="GTC" || b.quantity!=frozen.quantity ||
               b.stopAuxPrice!=frozen.stopPrice) {
                out.uncertain=true;
                reason="H1_PRIOR_NATIVE_OPEN_ORDER_IDENTITY_UNVERIFIED";
                return false;
            }
            stopPermanentId=b.permanentOrderId;
            lastObservedEpoch=e.brokerConnectionEpoch;
            continue; // Keep original OMS immutable; validate only this view.
        }
        if(e.eventType=="bracket_parent_reserved" && stage==0)stage=1;
        else if(e.eventType=="bracket_child_reserved" && stage==1)stage=2;
        else if(e.eventType=="bracket_parent_send_attempt" && stage==2)stage=3;
        else if(e.eventType=="bracket_child_send_attempt" && stage==3)stage=4;
        else {
            out.uncertain=true;
            reason="H1_NATIVE_ECHO_ORIGINAL_SEND_SEQUENCE_UNKNOWN";
            return false;
        }
        if(e.eventType=="bracket_parent_send_attempt" ||
           e.eventType=="bracket_child_send_attempt") {
            const bool child=e.eventType=="bracket_child_send_attempt";
            const OmsJournalEvent expected=Intent(frozen,refs,child);
            if(e.reqId!=frozen.originalCommandKey ||
               (!e.clientReqId.empty() &&
                e.clientReqId!=frozen.originalCommandKey) ||
               e.tsMs!=expected.tsMs || e.orderId!=expected.orderId ||
               e.eventId!=(child?"possible-child:":"possible-parent:")+
                          refs.groupCorrelation ||
               e.account!=expected.account || e.traceId!=expected.traceId ||
               e.strategy!=expected.strategy || e.source!=expected.source ||
               e.executionDomain!=expected.executionDomain ||
               e.venue!=expected.venue || e.instrument!=expected.instrument ||
               e.side!=expected.side || e.qty!=expected.qty ||
               e.price!=expected.price || e.status!="POSSIBLY_SENT" ||
               !e.reason.empty() || e.requestHash!=expected.requestHash ||
               e.venueCorrelationId!=expected.venueCorrelationId ||
               e.brokerClientId!=-1 || e.brokerConnectionEpoch!=0 ||
               !e.brokerCallbackType.empty() ||
               !e.brokerExecutionId.empty() || e.brokerBracket.version!=0) {
                out.uncertain=true;
                reason="H1_NATIVE_ECHO_DURABLE_SEND_IDENTITY_MISMATCH";
                return false;
            }
            // Do not drop send attempts from the immutable OMS! Only the
            // candidate's local original-reservation view omits them.
            continue;
        }
        beforeNative.push_back(e);
    }
    if(stage!=4) {
        out.uncertain=true;
        reason="H1_NATIVE_ECHO_PARENT_CHILD_SEND_RECEIPTS_INCOMPLETE";
        return false;
    }
    Reservation original;
    if(!ConfirmReplay(beforeNative,frozen,original,reason)) {
        out.uncertain=true;
        return false;
    }
    out=original;
    out.latestNativeOpenOrderEpoch=lastObservedEpoch;
    out.latestNativeStopPermanentId=stopPermanentId;
    // A positive native callback below may create ONLY an observation
    // candidate. Broker send disposition and ordinary exit remain unknown.
    out.uncertain=true;
    out.sendAuthorized=false;
    out.driverStopAllowed=false;
    reason.clear();
    return true;
}

} // namespace hepta_bracket_h1
