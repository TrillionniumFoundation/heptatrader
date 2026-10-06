#pragma once
#include "snowball_option_feed.h"
#include "../oms_journal.h"
#include <cstddef>
#include <string>
#include <vector>

namespace hepta { namespace options {
// Offline, caller-supplied filter; not a qualified contract or permission.
struct OmsOptionAuditScope {
    std::string account, agentId, sessionId;
    int brokerClientId = -1;
    std::int64_t conId = 0;
    CanonicalOptionIdentity identity;
};
struct OmsOptionAuditResult {
    std::size_t records = 0, sendAttempts = 0, commands = 0;
    std::size_t executions = 0, duplicateExecutions = 0, joinedFees = 0;
    std::size_t unmatchedFees = 0, legacyExecutions = 0, ignoredCompletedOrders = 0;
    bool executionCashFlowComplete = false, feesComplete = false;
    double boughtContracts = 0, soldContracts = 0, executionCashFlowUsd = 0, feesUsd = 0;
    std::vector<std::string> issues;
    std::string Json() const;
};
// Recomputes a report from an already copied native journal. No trading-state
// mutation, EvidenceLedger authority, SDK/socket, file writing or recovery API.
OmsOptionAuditResult AuditOptionOms(const OmsOptionAuditScope&,
                                   const std::vector<OmsJournalEvent>&);
// Reads an owner-private, bounded regular file with O_RDONLY|O_NOFOLLOW. Never
// calls OmsJournal::Init (which is a writer). Requires a complete immutable file.
bool ReadPrivateOmsAuditInput(const std::string& path,
    std::vector<OmsJournalEvent>& records, std::string& sha256, std::string& reason);
} }
