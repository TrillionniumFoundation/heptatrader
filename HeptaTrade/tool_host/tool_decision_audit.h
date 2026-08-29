#pragma once

#include "session_supervisor_audit_journal.h"
#include "trading_tool_host.h"

#include <cstdint>
#include <string>

// Converts Gateway decisions into a secret-free, durable audit record.  The
// session token and preview permit are never accepted by this interface.
class ToolDecisionAudit
{
public:
    ToolDecisionAudit();

    void SetJournal(SessionSupervisorAuditJournal* journal);
    void AllowMissingForTests();
    bool Ready() const;
    bool AppendIntent(bool peerCredentialAvailable, std::uint32_t peerUid,
                      const TradingToolHostRequest& request,
                      const TradingToolHostSessionBinding* binding,
                      bool mutation,
                      std::string& reason) const;
    void AppendOutcome(bool peerCredentialAvailable, std::uint32_t peerUid,
                       const TradingToolHostRequest* request,
                       const TradingToolHostSessionBinding* binding,
                       bool mutation,
                       TradingToolResult& result) const;

private:
    static std::string RequestFingerprint(const TradingToolHostRequest& request);
    bool Append(bool peerCredentialAvailable, std::uint32_t peerUid,
                const TradingToolHostRequest* request,
                const TradingToolHostSessionBinding* binding,
                const std::string& phase,
                const std::string& outcome,
                const std::string& reasonCode,
                std::string& reason) const;

    SessionSupervisorAuditJournal* m_journal;
    bool m_allowMissingForTests;
};
