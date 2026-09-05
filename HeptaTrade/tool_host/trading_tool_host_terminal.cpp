#include "trading_tool_host.h"
#include "session_supervisor_lease_store.h"

bool TradingToolHost::TerminalizeFinalizedRecoveryOwner(
    const SessionSupervisorLeaseRecord& durableRecord,
    const std::string& preliminaryReceiptSha256,
    ExecutionControlResult& terminalResult,
    std::string& reason)
{
    terminalResult = ExecutionControlResult();
    std::lock_guard<std::mutex> dispatchLock(m_mutationDispatchMutex);
    ExecutionControlAuthority* authority = nullptr;
    {
        std::lock_guard<std::mutex> lock(m_mutex);
        authority = m_recoveryControlAuthority;
        const std::unordered_map<std::string,
            TradingToolHostSessionBinding>::const_iterator found =
                m_sessions.find(durableRecord.token);
        if (found != m_sessions.end())
        {
            const AgentExecutionContext& context =
                found->second.session.executionContext;
            if (found->second.enabled || !found->second.recoveryOnly ||
                found->second.session.environment != "PAPER" ||
                found->second.leaseGeneration != durableRecord.leaseGeneration ||
                context.agentId != durableRecord.agentId ||
                context.sessionId != durableRecord.sessionId ||
                context.account != durableRecord.ownerAccount ||
                found->second.executionDomain !=
                    durableRecord.ownerExecutionDomain)
            {
                reason =
                    "SESSION_TERMINALIZATION_TOMBSTONE_BINDING_MISMATCH";
                return false;
            }
        }
    }
    if (authority == nullptr || durableRecord.templateId != "paper" ||
        !durableRecord.recoveryOnly ||
        !durableRecord.paperFinalizationRequired ||
        durableRecord.fencePending || durableRecord.fenceComplete ||
        durableRecord.paperFinalizationState !=
            SessionSupervisorPaperFinalizationState::AuditSealed ||
        durableRecord.leaseGeneration == 0 ||
        durableRecord.agentId.empty() || durableRecord.sessionId.empty() ||
        durableRecord.ownerAccount.empty() ||
        durableRecord.ownerExecutionDomain.empty() ||
        durableRecord.finalizationId.empty() ||
        preliminaryReceiptSha256.empty() ||
        durableRecord.finalizationReceiptSha256 != preliminaryReceiptSha256)
    {
        reason = authority == nullptr ?
            "SESSION_RECOVERY_QUERY_UNAVAILABLE" :
            "SESSION_TERMINALIZATION_BINDING_MISMATCH";
        return false;
    }
    ExecutionControlCommand command;
    command.context.agentId = durableRecord.agentId;
    command.context.sessionId = durableRecord.sessionId;
    command.context.account = durableRecord.ownerAccount;
    command.context.executionDomain = durableRecord.ownerExecutionDomain;
    command.context.toolCallId = "paper-terminalize-ack-" +
        durableRecord.finalizationId + "-" +
        std::to_string(durableRecord.leaseGeneration);
    command.targetCommandId = durableRecord.finalizationId;
    command.recoveryIngressFence = durableRecord.leaseGeneration;
    command.terminalPreliminaryReceiptSha256 = preliminaryReceiptSha256;
    terminalResult = authority->TerminalizeRecoveryOwner(command);
    if (terminalResult.ownerAccount != durableRecord.ownerAccount ||
        terminalResult.ownerExecutionDomain !=
            durableRecord.ownerExecutionDomain ||
        terminalResult.targetCommandId != durableRecord.finalizationId)
    {
        reason = "SESSION_TERMINALIZATION_RESULT_BINDING_MISMATCH";
        return false;
    }
    if (terminalResult.status != ExecutionCommandStatus::Accepted)
    {
        reason = terminalResult.reasonCode.empty() ?
            "SESSION_TERMINALIZATION_INCOMPLETE" :
            terminalResult.reasonCode;
        return false;
    }
    reason.clear();
    return true;
}

bool TradingToolHost::PurgeFinalizedRecoveryOwner(
    const SessionSupervisorLeaseRecord& durableRecord,
    std::string& reason)
{
    std::lock_guard<std::mutex> dispatchLock(m_mutationDispatchMutex);
    if (durableRecord.templateId != "paper" ||
        !durableRecord.recoveryOnly ||
        !durableRecord.paperFinalizationRequired ||
        durableRecord.paperFinalizationState !=
            SessionSupervisorPaperFinalizationState::AuditSealed ||
        durableRecord.leaseGeneration == 0 ||
        durableRecord.agentId.empty() || durableRecord.sessionId.empty())
    {
        reason = "SESSION_RECOVERY_FINALIZE_BINDING_MISMATCH";
        return false;
    }
    {
        std::lock_guard<std::mutex> lock(m_mutex);
        const std::unordered_map<std::string,
            TradingToolHostSessionBinding>::const_iterator found =
                m_sessions.find(durableRecord.token);
        if (found != m_sessions.end())
        {
            const AgentExecutionContext& context =
                found->second.session.executionContext;
            if (found->second.leaseGeneration !=
                    durableRecord.leaseGeneration ||
                context.agentId != durableRecord.agentId ||
                context.sessionId != durableRecord.sessionId ||
                context.account != durableRecord.ownerAccount ||
                found->second.executionDomain !=
                    durableRecord.ownerExecutionDomain)
            {
                reason =
                    "SESSION_RECOVERY_FINALIZE_LOCAL_BINDING_MISMATCH";
                return false;
            }
        }
    }
    DecisionLeaseOwner owner;
    owner.agentId = durableRecord.agentId;
    owner.sessionId = durableRecord.sessionId;
    m_decisionLeases.FenceOwner(owner);
    // Purge is the terminal lifecycle boundary.  Invalidate any registry
    // target permits/replay witnesses even when the local tombstone is
    // already absent and only durable agent/session identity remains.
    m_registry.RevokeTargetPermitsForIdentity(
        durableRecord.agentId, durableRecord.sessionId);
    if (!m_contractCatalog.RevokeIfPresent(durableRecord.token))
    {
        reason = "SESSION_CONTRACT_CATALOG_REVOKE_FAILED";
        return false;
    }
    {
        std::lock_guard<std::mutex> lock(m_mutex);
        if (m_sessions.find(durableRecord.token) != m_sessions.end())
            EraseSessionLocked(durableRecord.token);
        TradingToolHostSessionBinding ownerBinding;
        ownerBinding.session.executionContext.agentId = durableRecord.agentId;
        ownerBinding.session.executionContext.sessionId = durableRecord.sessionId;
        m_pendingOwnerFences.erase(SessionOwnerKey(ownerBinding));
    }
    reason.clear();
    return true;
}
