#pragma once
// Issue #331: research-only, read-only H1 native-ID high-watermark from the
// EXISTING OMS. This cannot grant Broker send or maintenance authority.
// Every original command must have exactly one matching Parent/Stop pair.
// A possibly sent order remains UNKNOWN until independently reconciled with
// complete original Broker order+execution evidence.
#include "../oms_journal.h"
#include <algorithm>
#include <climits>
#include <cstdint>
#include <map>
#include <string>
namespace hepta_h1_restart_floor {
struct Result {
    long highestObservedOrderId = 0;
    bool complete = false;
    bool containsPotentialSend = false;
    bool hasOrphanReservation = false;
    std::string reason;
};
struct Group {
    bool parentFound = false;
    bool childFound = false;
    OmsJournalEvent parent;
    OmsJournalEvent child;
};
inline bool SameGroup(const OmsJournalEvent& a, const OmsJournalEvent& b) {
    return a.reqId == b.reqId && a.account == b.account &&
           a.requestHash == b.requestHash && a.traceId == b.traceId &&
           a.strategy == b.strategy && a.source == b.source &&
           a.executionDomain == b.executionDomain &&
           a.instrument == b.instrument && a.qty == b.qty;
}
inline bool SameLeg(const OmsJournalEvent& actual,
                    const OmsJournalEvent& reserved) {
    return SameGroup(actual,reserved) &&
           actual.orderId == reserved.orderId &&
           actual.venueCorrelationId == reserved.venueCorrelationId &&
           actual.side == reserved.side && actual.price == reserved.price;
}
inline Result Inspect(const OmsJournal& journal) {
    Result out;
    std::map<std::string, Group> groups;
    std::map<long, std::string> ownersByNativeId;
    const int count = journal.Replay([&](const OmsJournalEvent& e) {
        if (!out.reason.empty()) return;
        const bool p = e.eventType == "bracket_parent_reserved";
        const bool c = e.eventType == "bracket_child_reserved";
        const bool pa = e.eventType == "bracket_parent_send_attempt";
        const bool ca = e.eventType == "bracket_child_send_attempt";
        if (!p && !c && !pa && !ca) return;
        if (e.orderId <= 0 || e.orderId >= INT_MAX-2 ||
            e.reqId.empty() || e.clientReqId != e.reqId ||
            e.account.compare(0,2,"DU") != 0 ||
            e.requestHash.empty() || e.executionDomain != "PAPER" ||
            e.instrument != "EUR.USD" || e.venue != "IB" ||
            e.source != "SHADOW_NO_BROKER_EGRESS" ||
            e.brokerClientId < -1) {
            out.reason = "H1_OMS_NATIVE_ID_OR_PROVENANCE_INVALID";
            return;
        }
        out.highestObservedOrderId = std::max(out.highestObservedOrderId,e.orderId);
        Group& g = groups[e.reqId];
        if (p || c) {
            // A numeric native ID cannot belong to two original commands or
            // two legs, even if the Broker later reuses its own order-id seed.
            if (!ownersByNativeId.emplace(e.orderId,e.reqId).second) {
                out.reason="H1_OMS_DUPLICATE_NATIVE_ORDER_ID";
                return;
            }
            if (p) {
                if (g.parentFound || (g.childFound && !SameGroup(e,g.child))) {
                    out.reason="H1_OMS_PARENT_ORIGINAL_IDENTITY_CHANGED";
                    return;
                }
                g.parent=e;g.parentFound=true;
            } else {
                if (g.childFound || !g.parentFound ||
                    !SameGroup(e,g.parent) ||
                    e.orderId != g.parent.orderId+1 ||
                    e.side == g.parent.side ||
                    e.venueCorrelationId == g.parent.venueCorrelationId) {
                    out.reason="H1_OMS_CHILD_ORIGINAL_IDENTITY_CHANGED";
                    return;
                }
                g.child=e;g.childFound=true;
            }
        } else {
            out.containsPotentialSend=true;
            if (!g.parentFound || !g.childFound ||
                e.status != "POSSIBLY_SENT" ||
                !SameLeg(e,pa?g.parent:g.child)) {
                out.reason="H1_OMS_UNBOUND_OR_CHANGED_SEND_ATTEMPT";
                return;
            }
        }
    });
    if (count < 0 || !out.reason.empty()) {
        if (out.reason.empty()) out.reason="H1_OMS_REPLAY_UNAVAILABLE";
        return out;
    }
    for (const auto& item : groups) {
        if (!item.second.parentFound || !item.second.childFound) {
            out.hasOrphanReservation=true;
            out.reason="H1_OMS_PAIR_PREFIX_ORPHANED";
            return out;
        }
    }
    out.complete=true;
    return out;
}
inline bool AuthorizeResearchIdFloor(const Result& history,
                                    long brokerNextValidId,
                                    std::string& reason) {
    if (!history.complete || history.hasOrphanReservation ||
        !history.reason.empty()) {
        reason="H1_NATIVE_ID_RECOVERY_UNCERTAIN";
        return false;
    }
    if (history.containsPotentialSend) {
        reason="H1_NATIVE_BROKER_SEND_UNKNOWN_REQUIRES_RECONCILIATION";
        return false;
    }
    if (brokerNextValidId <= 0 || brokerNextValidId >= INT_MAX-2 ||
        brokerNextValidId <= history.highestObservedOrderId) {
        reason="H1_BROKER_NEXT_VALID_ID_NOT_BEYOND_OMS_FLOOR";
        return false;
    }
    reason.clear();
    return true;
}
} // namespace hepta_h1_restart_floor
