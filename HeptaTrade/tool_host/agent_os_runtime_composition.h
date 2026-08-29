#pragma once

#include "agent_os_runtime_config.h"
#include "session_supervisor_audit_journal.h"
#include "session_supervisor_lease_store.h"
#include "trading_tool_session_control_plane.h"
#include "unix_session_supervisor_server.h"
#include "unix_tool_server.h"

#include <string>

class AgentOsRuntimeComposition
{
public:
    AgentOsRuntimeComposition(TradingToolHost& host,
                              const AgentOsRuntimeConfig& config,
                              const TradingToolSessionControlPlane::Authorizer& authorizer);
    ~AgentOsRuntimeComposition();

    bool StartToolServer(std::string& reason);
    bool StartToolServer(const std::string& issuer,
                         const TradingToolHostSessionBinding& binding,
                         std::string& reason);
    bool StartSupervisor(const UnixSessionSupervisorServer::BindingResolver& resolver,
                         std::string& reason);
    void Stop();

    TradingToolSessionControlPlane& ControlPlane();
    UnixToolServer& ToolServer();
    UnixSessionSupervisorServer& Supervisor();

private:
    bool EnsureAuditJournal(std::string& reason);

    TradingToolHost& m_host;
    AgentOsRuntimeConfig m_config;
    TradingToolSessionControlPlane m_controlPlane;
    UnixToolServer m_toolServer;
    SessionSupervisorLeaseStore m_leaseStore;
    SessionSupervisorAuditJournal m_auditJournal;
    bool m_auditJournalInitialized;
    UnixSessionSupervisorServer m_supervisor;
};
