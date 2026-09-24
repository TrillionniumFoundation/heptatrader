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
    SessionSupervisorLeaseRecord& durableRecord, ExecutionControlStatusResult& result,
    std::string& reason,
    TradingToolRecoveryFenceCommittedHook committedHook,
    void* committedHookContext,
    ExecutionOwnerAuditResult* ownerAudit,
    std::uint64_t recoveryExpiresAtMs,
    const std::string& durableCurrentToken,
    std::chrono::steady_clock::time_point deadline)
{
    result = ExecutionControlStatusResult();
    if (!ValidRequest(token, expectedGeneration, targetCommandId,
            ownerAudit != nullptr))
        return RecoveryFailure(reason, "SESSION_RECOVERY_QUERY_INVALID");
    // Wait for earlier synchronous dispatch and exclude every queued entry.
    std::unique_lock<std::mutex> dispatchLock(m_mutationDispatchMutex);
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
    // Local and durable recovery-only are now committed. Remote observation
    // cannot re-enable entry and need not serialize unrelated owner dispatch.
    dispatchLock.unlock();
    const auto expired = [&] { return std::chrono::steady_clock::now() >= deadline; };
    if (expired()) return RecoveryFailure(reason, "SUPERVISOR_WORK_BUDGET_EXHAUSTED");
    if (ownerAudit != nullptr)
    {
        auto command = AuditCommand(binding, expectedGeneration);
        command.localDeadline = deadline;
        *ownerAudit = authority->RecoveryAuditOwner(command);
    }
    if (!targetCommandId.empty() && !expired())
    {
        auto command = QueryCommand(binding, targetCommandId, expectedGeneration);
        command.localDeadline = deadline;
        result = authority->QueryCommandStatus(command);
    }
    // A late response must not be adopted after any local owner/generation
    // change, or after its caller's total work budget. No response grants a lease.
    TradingToolHostSessionBinding current;
    const bool currentBinding = GetSession(token, current) && current.enabled &&
        current.recoveryOnly && BindingMatchesRecord(current, durableRecord);
    if (!currentBinding || expired())
    {
        result = ExecutionControlStatusResult();
        if (ownerAudit != nullptr) *ownerAudit = ExecutionOwnerAuditResult();
        return RecoveryFailure(reason, currentBinding ?
            "SUPERVISOR_WORK_BUDGET_EXHAUSTED" : "SESSION_RECOVERY_FENCE_STATE_CHANGED");
    }
    reason.clear();
    return true;
}
