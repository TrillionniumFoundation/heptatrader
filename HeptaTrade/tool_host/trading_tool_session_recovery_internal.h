#pragma once

#include "session_supervisor_lease_store.h"
#include "trading_tool_host.h"

#if defined(__GNUC__) || defined(__clang__)
#define HEPTA_RECOVERY_INLINE inline __attribute__((always_inline))
#else
#define HEPTA_RECOVERY_INLINE inline
#endif

namespace HeptaTradingToolRecoveryInternal
{
HEPTA_RECOVERY_INLINE bool RecoveryFailure(
    std::string& reason, const char* code)
{
    reason = code;
    return false;
}

HEPTA_RECOVERY_INLINE bool ValidRequest(const std::string& token,
                         std::uint64_t generation,
                         const std::string& commandId,
                         bool ownerAuditRequested)
{
    return !token.empty() && generation != 0 &&
        (ownerAuditRequested || !commandId.empty()) &&
        commandId.size() <= 128;
}

HEPTA_RECOVERY_INLINE bool BindingMatchesRecord(
    const TradingToolHostSessionBinding& binding,
    const SessionSupervisorLeaseRecord& record)
{
    return binding.token == record.token &&
        binding.leaseGeneration == record.leaseGeneration &&
        binding.session.executionContext.agentId == record.agentId &&
        binding.session.executionContext.sessionId == record.sessionId &&
        binding.session.executionContext.account == record.ownerAccount &&
        binding.executionDomain == record.ownerExecutionDomain &&
        record.templateId == "paper";
}

HEPTA_RECOVERY_INLINE bool CommitRecoveryOnlyLease(
    SessionSupervisorLeaseStore& store,
    const std::string& durableCurrentToken,
    const std::string& commandId,
    std::uint64_t recoveryExpiresAtMs,
    SessionSupervisorLeaseRecord& record,
    std::string& reason)
{
    SessionSupervisorLeaseRecord recovery = record;
    recovery.recoveryOnly = true;
	if (!commandId.empty() && recovery.recoveryCommandId.empty())
		recovery.recoveryCommandId = commandId;
    if (recoveryExpiresAtMs > recovery.expiresAtMs)
        recovery.expiresAtMs = recoveryExpiresAtMs;
    // Always revalidate and durably replace the exact token record.  An
    // in-memory/detached copy that already says recovery-only is not proof
    // that the encrypted supervisor store contains the recovery fence.
    if (!store.Replace(durableCurrentToken, recovery, reason)) return false;
    record = recovery;
    return true;
}

HEPTA_RECOVERY_INLINE bool MarkRecoveryOnly(
    std::mutex& mutex,
    std::unordered_map<std::string, TradingToolHostSessionBinding>& sessions,
    const std::string& token,
    std::uint64_t generation,
    std::uint64_t recoveryExpiresAtMs,
    std::string& reason)
{
    std::lock_guard<std::mutex> lock(mutex);
    const std::unordered_map<std::string,
        TradingToolHostSessionBinding>::iterator session = sessions.find(token);
    if (session == sessions.end() ||
        session->second.leaseGeneration != generation ||
        !session->second.enabled)
    {
        reason = "SESSION_RECOVERY_FENCE_STATE_CHANGED";
        return false;
    }
    session->second.recoveryOnly = true;
    if (recoveryExpiresAtMs > session->second.expiresAtMs)
        session->second.expiresAtMs = recoveryExpiresAtMs;
    return true;
}

inline ExecutionControlCommand QueryCommand(
    const TradingToolHostSessionBinding& binding,
    const std::string& commandId,
    std::uint64_t generation)
{
    ExecutionControlCommand command;
    command.context = binding.session.executionContext;
    command.context.executionDomain = binding.executionDomain;
    command.context.toolCallId = commandId;
    command.targetCommandId = commandId;
    command.recoveryIngressFence = generation;
    return command;
}

inline ExecutionControlCommand AuditCommand(
    const TradingToolHostSessionBinding& binding,
    std::uint64_t generation)
{
    ExecutionControlCommand command;
    command.context = binding.session.executionContext;
    command.context.executionDomain = binding.executionDomain;
    command.context.toolCallId = "recovery-owner-audit-" +
        std::to_string(generation);
    command.recoveryIngressFence = generation;
    return command;
}

#undef HEPTA_RECOVERY_INLINE
}
