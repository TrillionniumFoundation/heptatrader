#include "trading_tool_session_recovery_internal.h"

using namespace HeptaTradingToolRecoveryInternal;

bool TradingToolHost::PrepareRecoveryOnlyBinding(
    const std::string& token, std::uint64_t expectedGeneration,
    TradingToolHostSessionBinding& binding,
    ExecutionControlAuthority*& authority, std::string& reason)
{
    std::lock_guard<std::mutex> lock(m_mutex);
    const std::unordered_map<std::string,
        TradingToolHostSessionBinding>::iterator session =
        m_sessions.find(token);
    if (session == m_sessions.end())
        return RecoveryFailure(reason, "SESSION_NOT_FOUND");
    if (session->second.leaseGeneration != expectedGeneration)
        return RecoveryFailure(reason, "SESSION_LEASE_GENERATION_MISMATCH");
    const std::string ownerKey = SessionOwnerKey(session->second);
    if (!session->second.enabled || m_pendingOwnerFences.find(ownerKey) !=
            m_pendingOwnerFences.end() ||
            WatchTransactionPendingLocked(session->second))
        return RecoveryFailure(reason, "SESSION_OWNER_FENCE_PENDING");
    binding = session->second;
    binding.recoveryOnly = true;
    authority = m_recoveryControlAuthority;
    return true;
}

bool TradingToolHost::EnterRecoveryOnlyAndQuery(
    const std::string& token, std::uint64_t expectedGeneration,
    const std::string& targetCommandId, SessionSupervisorLeaseStore& leaseStore,
    SessionSupervisorLeaseRecord& durableRecord, ExecutionControlResult& result,
    std::string& reason,
    TradingToolRecoveryFenceCommittedHook committedHook,
    void* committedHookContext,
    ExecutionControlResult* ownerAudit,
    std::uint64_t recoveryExpiresAtMs,
    const std::string& durableCurrentToken)
{
    result = ExecutionControlResult();
    if (!ValidRequest(token, expectedGeneration, targetCommandId,
            ownerAudit != nullptr))
        return RecoveryFailure(reason, "SESSION_RECOVERY_QUERY_INVALID");
    // Wait for earlier synchronous dispatch and exclude every queued entry.
    std::lock_guard<std::mutex> dispatchLock(m_mutationDispatchMutex);
    TradingToolHostSessionBinding binding;
    ExecutionControlAuthority* authority = nullptr;
    if (!PrepareRecoveryOnlyBinding(
            token, expectedGeneration, binding, authority, reason))
        return false;
    if (!BindingMatchesRecord(binding, durableRecord))
        return RecoveryFailure(reason, "SESSION_RECOVERY_FENCE_BINDING_MISMATCH");
    const std::string storeToken = durableCurrentToken.empty() ?
        token : durableCurrentToken;
    if (!CommitRecoveryOnlyLease(leaseStore, storeToken, targetCommandId,
            recoveryExpiresAtMs, durableRecord, reason))
        return false;
    if (committedHook != nullptr) committedHook(committedHookContext);
    if (!MarkRecoveryOnly(m_mutex, m_sessions, token, expectedGeneration,
            recoveryExpiresAtMs, reason))
        return false;
    // A recovery request must fail closed even when the remote control
    // authority is temporarily unavailable.  Persist and apply the local
    // recovery-only fence first; never leave an entry-enabled bearer beside
    // a durable recovery-only lease.
    if (authority == nullptr)
        return RecoveryFailure(reason, "SESSION_RECOVERY_QUERY_UNAVAILABLE");
    if (ownerAudit != nullptr)
        *ownerAudit = authority->RecoveryAuditOwner(
            AuditCommand(binding, expectedGeneration));
    if (!targetCommandId.empty())
        result = authority->QueryCommandStatus(QueryCommand(
            binding, targetCommandId, expectedGeneration));
    reason.clear();
    return true;
}
