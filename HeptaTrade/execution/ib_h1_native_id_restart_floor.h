#pragma once
// Issue #331 — read-only OMS high-watermark for real Execution native IDs.
// This is not a broker ID lease. An authoritative NEXT_VALID_ID must be
// STRICTLY greater than this recovered floor before any future IB send.
// Unknown or malformed logs fail closed; never treat a missing callback as
// proof that reserved IDs are free.
#include "../oms_journal.h"
#include <algorithm>
#include <climits>
#include <cstdint>
#include <string>
namespace hepta_h1_restart_floor {
struct Result {
    long highestObservedOrderId = 0;
    bool complete = false;
    bool containsPotentialSend = false;
    bool hasOrphanReservation = false;
    std::string reason;
};
inline Result Inspect(const OmsJournal& journal) {
    Result out;
    bool parent = false, child = false;
    int parentCount=0,childCount=0;
    const int records = journal.Replay([&](const OmsJournalEvent& event) {
        if (event.eventType!="bracket_parent_reserved" &&
            event.eventType!="bracket_child_reserved" &&
            event.eventType!="bracket_parent_send_attempt" &&
            event.eventType!="bracket_child_send_attempt") return;
        if (event.orderId<=0 || event.orderId>=INT_MAX-1 ||
            event.reqId.empty() || event.account.empty() ||
            event.requestHash.empty() || event.brokerClientId < -1) {
            out.reason="H1_OMS_NATIVE_ID_OR_PROVENANCE_INVALID";return;
        }
        out.highestObservedOrderId=std::max(out.highestObservedOrderId,event.orderId);
        if(event.eventType=="bracket_parent_reserved") {parent=true;++parentCount;}
        if(event.eventType=="bracket_child_reserved"){child=true;++childCount;}
        if(event.eventType=="bracket_parent_send_attempt" ||
           event.eventType=="bracket_child_send_attempt")out.containsPotentialSend=true;
    });
    if (records<0 || !out.reason.empty()) {
        if(out.reason.empty())out.reason="H1_OMS_REPLAY_UNAVAILABLE";
        return out;
    }
    if (parent != child || parentCount != childCount)out.hasOrphanReservation=true;
    // A reservation (even a full pair) is NOT disposable merely because
    // broker never echoed it. Do not lower the high-watermark on reconnect.
    if(out.hasOrphanReservation) {
        out.reason="H1_OMS_PAIR_PREFIX_ORPHANED";
        return out;
    }
    out.complete=true;
    return out;
}
inline bool AuthorizeResearchIdFloor(const Result& historical,
                                    long brokerNextValidId,
                                    std::string& reason) {
    if(!historical.complete || historical.hasOrphanReservation ||
       !historical.reason.empty()) {
        reason="H1_NATIVE_ID_RECOVERY_UNCERTAIN";return false;
    }
    if(brokerNextValidId<=0 || brokerNextValidId>=INT_MAX-2 ||
       brokerNextValidId<=historical.highestObservedOrderId) {
        reason="H1_BROKER_NEXT_VALID_ID_NOT_BEYOND_OMS_FLOOR";return false;
    }
    reason.clear();return true;
}
}
