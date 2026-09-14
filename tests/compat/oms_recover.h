#pragma once

#include "oms_journal.h"

#include <string>
#include <unordered_map>
#include <unordered_set>

struct OmsRecoveredOrder {
    long orderId = -1;
    std::string reqId;
    std::string traceId;
    std::string instrument;
    std::string side;
    double qty = 0.0;
    double price = 0.0;
    std::string status;
    bool placeSent = false;
    bool cancelSent = false;
    bool rejected = false;
    std::string rejectReason;
    long long lastTsMs = 0;
};

struct OmsRecoverResult {
    int totalRead = 0;
    int dedupSkipped = 0;
    std::unordered_map<std::string, int> eventCounts;
    std::unordered_map<long, OmsRecoveredOrder> orders;
    std::unordered_map<std::string, std::string> reqIdToStatus;
};

class OmsRecover {
public:
    static OmsRecoverResult Replay(const OmsJournal& journal);

private:
    static std::string BuildEventDedupKey(const OmsJournalEvent& e);
    static void ApplyEvent(const OmsJournalEvent& e, OmsRecoverResult& out);
};
