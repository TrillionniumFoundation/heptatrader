#include "execution_coordinator.h"

#include <cerrno>
#include <cstdlib>

bool ExecutionCoordinator::ApplyRecoveredOwnershipEventLocked(
    const OmsJournalEvent& event, const std::string& agentId)
{
    if (event.eventType == "account_new_entry_fenced")
    {
        if (agentId.empty() || event.account.empty() ||
            event.reqId.empty() || event.traceId.empty() ||
            event.status != "fenced" ||
            event.riskCode != "ACCOUNT_NEW_ENTRY_FENCED" ||
            event.orderId != -1)
            BlockMutationsLocked("OMS_ACCOUNT_NEW_ENTRY_FENCE_RECOVERY_INVALID");
        else
            m_accountNewEntryFences.insert(event.account);
    }
    else if (event.eventType == "session_owner_fenced")
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
        const std::string key = OwnerKey(agentId, event.traceId);
        const auto resumed = m_resumedRecoveryIngress.find(key);
        if (resumed != m_resumedRecoveryIngress.end() && resumed->second >= fence)
        {
            BlockMutationsLocked("OMS_RECOVERY_INGRESS_REPLAYED_AFTER_RESUME");
            return true;
        }
        m_recoveryOnlySessionOwners[key] = static_cast<std::uint64_t>(fence);
    }
    else if (event.eventType == "session_owner_fence_release")
    {
        const std::string key = OwnerKey(agentId, event.traceId);
        const std::string prefix = "released-user-recovery:";
        if (event.status.compare(0, prefix.size(), prefix) == 0)
        {
            const std::string value = event.status.substr(prefix.size());
            char* end = nullptr; errno = 0;
            const unsigned long long fence = std::strtoull(value.c_str(), &end, 10);
            const auto recovery = m_recoveryOnlySessionOwners.find(key);
            if (errno != 0 || value.empty() || *end != '\0' || fence == 0 ||
                value != std::to_string(fence) || recovery == m_recoveryOnlySessionOwners.end() ||
                recovery->second != fence || m_paperTerminalFencePresent)
            {
                BlockMutationsLocked("OMS_USER_RECOVERY_RELEASE_INVALID");
                return true;
            }
            m_resumedRecoveryIngress[key] = fence;
            m_recoveryOnlySessionOwners.erase(key);
        }
        else if (event.status != "released")
        {
            BlockMutationsLocked("OMS_SESSION_RELEASE_STATE_INVALID");
            return true;
        }
        m_fencedSessionOwners.erase(key);
    }
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
    const auto resumed = m_resumedRecoveryIngress.find(ownerKey);
    if (resumed != m_resumedRecoveryIngress.end() && resumed->second >= ingressFence)
    {
        reason = "SESSION_RECOVERY_INGRESS_FENCE_STALE";
        return false;
    }
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

std::uint64_t ExecutionCoordinator::SessionOwnerRecoveryIngressFence(
    const std::string& agentId, const std::string& sessionId) const
{
    std::lock_guard<std::mutex> lock(m_mutex);
    const auto found = m_recoveryOnlySessionOwners.find(OwnerKey(agentId, sessionId));
    return found == m_recoveryOnlySessionOwners.end() ? 0 : found->second;
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
    ExecutionControlStatusResult& result)
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
