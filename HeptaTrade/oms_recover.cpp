#include "oms_recover.h"

#include <sstream>

std::string OmsRecover::BuildEventDedupKey(const OmsJournalEvent& e)
{
    if (!e.eventId.empty()) return std::string("eid:") + e.eventId;

    std::ostringstream oss;
    oss << "raw:" << e.eventType << "|" << e.tsMs << "|" << e.orderId << "|"
        << (e.reqId.empty() ? e.clientReqId : e.reqId) << "|"
        << e.status << "|" << e.reason << "|" << e.source;
    return oss.str();
}

void OmsRecover::ApplyEvent(const OmsJournalEvent& e, OmsRecoverResult& out)
{
    out.eventCounts[e.eventType]++;

    const std::string reqId = e.reqId.empty() ? e.clientReqId : e.reqId;
    if (!reqId.empty() && !e.status.empty())
    {
        out.reqIdToStatus[reqId] = e.status;
    }

    if (e.orderId <= 0) return;

    OmsRecoveredOrder& o = out.orders[e.orderId];
    o.orderId = e.orderId;
    if (!reqId.empty()) o.reqId = reqId;
    if (!e.traceId.empty()) o.traceId = e.traceId;
    if (!e.instrument.empty()) o.instrument = e.instrument;
    if (!e.side.empty()) o.side = e.side;
    if (e.qty > 0.0) o.qty = e.qty;
    if (e.price > 0.0) o.price = e.price;
    if (e.tsMs > o.lastTsMs) o.lastTsMs = e.tsMs;

    if (e.eventType == "order_intent")
    {
        if (o.qty <= 0.0 && e.qty > 0.0) o.qty = e.qty;
        if (o.price <= 0.0 && e.price > 0.0) o.price = e.price;
    }
    else if (e.eventType == "place_sent")
    {
        o.placeSent = true;
        if (!e.status.empty()) o.status = e.status;
    }
    else if (e.eventType == "status")
    {
        if (!e.status.empty()) o.status = e.status;
    }
    else if (e.eventType == "cancel")
    {
        o.cancelSent = true;
        if (!e.status.empty()) o.status = e.status;
    }
    else if (e.eventType == "reject" || e.eventType == "risk_blocked")
    {
        o.rejected = true;
        if (!e.reason.empty()) o.rejectReason = e.reason;
        if (!e.status.empty()) o.status = e.status;
    }
}

OmsRecoverResult OmsRecover::Replay(const OmsJournal& journal)
{
    OmsRecoverResult out;
    std::unordered_set<std::string> dedup;

    out.totalRead = journal.Replay([&](const OmsJournalEvent& e) {
        const std::string key = BuildEventDedupKey(e);
        if (dedup.find(key) != dedup.end())
        {
            out.dedupSkipped++;
            return;
        }
        dedup.insert(key);
        ApplyEvent(e, out);
    });

    return out;
}
