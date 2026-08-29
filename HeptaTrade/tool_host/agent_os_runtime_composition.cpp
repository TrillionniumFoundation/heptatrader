#include "agent_os_runtime_composition.h"

#include <map>

namespace
{
bool ValidToolServerConfig(const AgentOsRuntimeConfig& config, std::string& reason)
{
    if (!config.Validate(reason)) return false;
    if (config.ToolServerEnabled()) return true;
    reason = "TOOL_SERVER_SOCKET_NOT_CONFIGURED";
    return false;
}

bool ValidSupervisorConfig(const AgentOsRuntimeConfig& config, std::string& reason)
{
    if (!config.Validate(reason)) return false;
    if (config.SupervisorEnabled()) return true;
    reason = "SUPERVISOR_SOCKET_NOT_CONFIGURED";
    return false;
}
}
AgentOsRuntimeComposition::AgentOsRuntimeComposition(
    TradingToolHost& host,
    const AgentOsRuntimeConfig& config,
    const TradingToolSessionControlPlane::Authorizer& authorizer)
    : m_host(host),
      m_config(config),
      m_controlPlane(host, authorizer),
      m_toolServer(host),
      m_auditJournalInitialized(false),
      m_supervisor(m_controlPlane)
{
}

AgentOsRuntimeComposition::~AgentOsRuntimeComposition()
{
    Stop();
}

bool AgentOsRuntimeComposition::StartToolServer(std::string& reason)
{
    if (!ValidToolServerConfig(m_config, reason)) return false;
    if (!EnsureAuditJournal(reason)) return false;
    if (m_config.toolListenFd >= 0)
        return m_toolServer.StartFromFd(m_config.toolListenFd, reason, 65536, 3000,
            m_config.toolExecutionWorkers,
            m_config.toolMaxPending,
            m_config.toolMaxConcurrentPerOwner,
            m_config.toolMaxPendingPerOwner,
            m_config.toolIngressWorkers);
    return m_toolServer.Start(m_config.toolSocket, reason, 65536, 3000,
        m_config.toolExecutionWorkers,
        m_config.toolMaxPending,
        m_config.toolMaxConcurrentPerOwner,
        m_config.toolMaxPendingPerOwner,
        m_config.toolIngressWorkers);
}

bool AgentOsRuntimeComposition::StartToolServer(
    const std::string& issuer,
    const TradingToolHostSessionBinding& binding,
    std::string& reason)
{
    if (!ValidToolServerConfig(m_config, reason)) return false;
    if (!m_controlPlane.Provision(issuer, binding, reason)) return false;
    if (StartToolServer(reason))
    {
        return true;
    }

    const std::string startReason = reason;
    std::string revokeReason;
    m_controlPlane.Revoke(issuer, binding.token, binding.leaseGeneration, revokeReason);
    reason = startReason;
    return false;
}

bool AgentOsRuntimeComposition::StartSupervisor(
    const UnixSessionSupervisorServer::BindingResolver& resolver,
    std::string& reason)
{
    if (!ValidSupervisorConfig(m_config, reason)) return false;
    if (!EnsureAuditJournal(reason)) return false;
    if (!m_config.supervisorLeaseStorePath.empty())
    {
        if (m_config.supervisorLeaseCleanupLockPath.empty())
        { reason = "SUPERVISOR_LEASE_CLEANUP_LOCK_REQUIRED"; return false; }
        if (!m_leaseStore.Init(m_config.supervisorLeaseStorePath,
                m_config.supervisorLeaseKeyPath,
                m_config.supervisorLeaseCleanupLockPath,
                m_config.supervisorLeaseCleanupLockUid,
                m_config.supervisorLeaseCleanupLockGid, reason))
        {
            reason = "SUPERVISOR_LEASE_STORE_INIT_FAILED:" + reason;
            return false;
        }
        m_supervisor.SetLeaseStore(&m_leaseStore);
    }
    if (m_config.supervisorListenFd < 0 &&
        m_config.supervisorSocket == m_config.toolSocket)
    {
        reason = "SUPERVISOR_SOCKET_MUST_BE_SEPARATE";
        return false;
    }

    std::map<std::uint32_t, std::string> authorizedIssuers;
    authorizedIssuers[m_config.supervisorUid] = "hepta.os.bootstrap";
    if (m_config.supervisorListenFd >= 0)
    {
        return m_supervisor.StartFromFd(m_config.supervisorListenFd, authorizedIssuers,
            resolver, reason, 16384, 3000, m_config.supervisorMaxTtlMs);
    }
    return m_supervisor.Start(m_config.supervisorSocket, authorizedIssuers,
        resolver, reason, 16384, 3000, m_config.supervisorMaxTtlMs);
}

bool AgentOsRuntimeComposition::EnsureAuditJournal(std::string& reason)
{
    if (m_config.supervisorAuditJournalPath.empty())
    {
        if (!m_config.allowMissingAuditForTests)
        {
            reason = "TOOL_DECISION_AUDIT_JOURNAL_REQUIRED";
            return false;
        }
        m_toolServer.AllowMissingDecisionAuditForTests();
        reason.clear();
        return true;
    }
    if (!m_auditJournalInitialized)
    {
        if (!m_auditJournal.Init(m_config.supervisorAuditJournalPath, reason))
        {
            reason = "SUPERVISOR_AUDIT_INIT_FAILED:" + reason;
            return false;
        }
        m_auditJournalInitialized = true;
        m_toolServer.SetDecisionAuditJournal(&m_auditJournal);
        m_supervisor.SetAuditJournal(&m_auditJournal);
    }
    reason.clear();
    return true;
}

void AgentOsRuntimeComposition::Stop()
{
    m_supervisor.Stop();
    m_toolServer.Stop();
}

TradingToolSessionControlPlane& AgentOsRuntimeComposition::ControlPlane()
{
    return m_controlPlane;
}

UnixToolServer& AgentOsRuntimeComposition::ToolServer()
{
    return m_toolServer;
}

UnixSessionSupervisorServer& AgentOsRuntimeComposition::Supervisor()
{
    return m_supervisor;
}
