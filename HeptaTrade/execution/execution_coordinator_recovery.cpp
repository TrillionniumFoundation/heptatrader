#include "execution_coordinator.h"

#include <cerrno>
#include <cstdlib>

bool ExecutionCoordinator::ApplyRecoveredOwnershipEventLocked(
    const OmsJournalEvent& event, const std::string& agentId)
{
    if (event.eventType == "session_owner_fenced")
        m_fencedSessionOwners.insert(OwnerKey(agentId, event.traceId));
    else if (event.eventType == "session_owner_recovery_only")
    {
        char* end = nullptr;
        errno = 0;
        const unsigned long long fence = std::strtoull(event.status.c_str(), &end, 10);
        if (errno != 0 || end == event.status.c_str() || *end != '\0' ||
            fence == 0)
        {
            BlockMutationsLocked("OMS_RECOVERY_ONLY_FENCE_INVALID");
            return true;
        }
        m_recoveryOnlySessionOwners[OwnerKey(agentId, event.traceId)] =
            static_cast<std::uint64_t>(fence);
    }
    else if (event.eventType == "session_owner_fence_release")
        m_fencedSessionOwners.erase(OwnerKey(agentId, event.traceId));
    else if (event.eventType == "order_owner_reconciled_terminal")
        m_orderOwners.erase(event.orderId);
    else if (event.eventType == "paper_terminal_fence")
        return ApplyRecoveredPaperTerminalFenceLocked(event, agentId);
    else
        return false;
    return true;
}

bool ExecutionCoordinator::EnterRecoveryOnlyOwner(
    const std::string& agentId,
    const std::string& sessionId,
    std::uint64_t ingressFence,
    std::string& reason)
{
    if (agentId.empty() || sessionId.empty() || ingressFence == 0)
    {
        reason = "SESSION_RECOVERY_INGRESS_FENCE_INVALID";
        return false;
    }
    std::lock_guard<std::mutex> lock(m_mutex);
    const std::string ownerKey = OwnerKey(agentId, sessionId);
    const std::unordered_map<std::string, std::uint64_t>::const_iterator current =
        m_recoveryOnlySessionOwners.find(ownerKey);
    if (current != m_recoveryOnlySessionOwners.end() &&
        current->second > ingressFence)
    {
        reason = "SESSION_RECOVERY_INGRESS_FENCE_STALE";
        return false;
    }
    if (current == m_recoveryOnlySessionOwners.end() ||
        current->second != ingressFence)
    {
        AgentExecutionContext context;
        context.agentId = agentId;
        context.sessionId = sessionId;
        context.toolCallId = "session-owner-recovery-only-" + std::to_string(ingressFence);
        if (!AppendOrBlockLocked(BuildEvent(context,
                "session_owner_recovery_only", -1, "", "", 0.0, 0.0,
                std::to_string(ingressFence),
                "root custodian blocked new entry",
                "SESSION_RECOVERY_ONLY"),
            "OMS_SESSION_RECOVERY_FENCE_JOURNAL_FAILED"))
        {
            reason = "OMS_SESSION_RECOVERY_FENCE_JOURNAL_FAILED";
            return false;
        }
    }
    m_recoveryOnlySessionOwners[ownerKey] = ingressFence;
    reason.clear();
    return true;
}

bool ExecutionCoordinator::IsSessionOwnerRecoveryOnly(
    const std::string& agentId,
    const std::string& sessionId) const
{
    std::lock_guard<std::mutex> lock(m_mutex);
    return m_recoveryOnlySessionOwners.find(OwnerKey(agentId, sessionId)) !=
        m_recoveryOnlySessionOwners.end();
}

bool ExecutionCoordinator::EnterRecoveryOnlyForControl(
    const ExecutionControlCommand& command,
    ExecutionControlResult& result)
{
    if (command.recoveryIngressFence == 0) return true;
    std::string reason;
    if (EnterRecoveryOnlyOwner(command.context.agentId,
            command.context.sessionId, command.recoveryIngressFence, reason))
        return true;
    result.status = ExecutionCommandStatus::Rejected;
    result.reasonCode = reason.empty() ?
        "EXECUTION_RECOVERY_FENCE_FAILED" : reason;
    return false;
}
