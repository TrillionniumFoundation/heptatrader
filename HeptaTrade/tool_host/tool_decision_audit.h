#pragma once

#include "session_supervisor_audit_journal.h"
#include "trading_tool_host.h"

#include <cstdint>
#include <string>

// Records critical Gateway decisions durably and samples repeated successful
// routine reads. Session tokens and preview permits never enter the journal.
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
    static std::string RequestFingerprint(const TradingToolHostRequest& request,
                                         bool observationGroup = false);
    bool Append(bool peerCredentialAvailable, std::uint32_t peerUid,
                const TradingToolHostRequest* request,
                const TradingToolHostSessionBinding* binding,
                bool mutation,
                const std::string& phase,
                const std::string& outcome,
                const std::string& reasonCode,
                std::string& reason,
                const TradingToolResult* response = nullptr) const;

    SessionSupervisorAuditJournal* m_journal;
    bool m_allowMissingForTests;
};
