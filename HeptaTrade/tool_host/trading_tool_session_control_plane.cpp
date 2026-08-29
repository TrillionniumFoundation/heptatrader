#include "trading_tool_session_control_plane.h"
#include "session_supervisor_lease_store.h"

TradingToolSessionControlPlane::TradingToolSessionControlPlane(
    TradingToolHost& host,
    const Authorizer& authorizer)
    : m_host(host), m_authorizer(authorizer)
{
}

bool TradingToolSessionControlPlane::Provision(
    const std::string& issuer,
    const TradingToolHostSessionBinding& binding,
    std::string& reason)
{
    if (issuer.empty() || !m_authorizer)
    {
        reason = "SESSION_CONTROL_PLANE_UNAUTHORIZED";
        return false;
    }
    if (!m_authorizer(issuer, binding, reason))
    {
        if (reason.empty()) reason = "SESSION_CONTROL_PLANE_DENIED";
        return false;
    }
    return m_host.RegisterSession(binding, reason);
}

bool TradingToolSessionControlPlane::Revoke(
    const std::string& issuer,
    const std::string& token,
    std::uint64_t expectedGeneration,
    std::string& reason)
{
    return RevokeWithReason(
        issuer, token, expectedGeneration, "session_revoked", reason);
}

bool TradingToolSessionControlPlane::RevokeCurrentIfOwner(
    const std::string& issuer,
    const std::string& token,
    const std::string& expectedAgentId,
    const std::string& expectedSessionId,
    const std::string& revokeReason,
    std::string& reason)
{
    TradingToolHostSessionBinding identity;
    identity.token = token;
    if (issuer.empty() || !m_authorizer ||
        !m_authorizer(issuer, identity, reason))
    {
        if (reason.empty()) reason = "SESSION_CONTROL_PLANE_DENIED";
        return false;
    }
    return m_host.RevokeCurrentSessionIfOwner(
        token, expectedAgentId, expectedSessionId, revokeReason, reason);
}

bool TradingToolSessionControlPlane::BeginWatchTransaction(
    const std::string& issuer,
    const std::vector<TradingToolHostSessionBinding>& expectedBindings,
    std::string& watchTransactionId,
    std::string& reason)
{
    watchTransactionId.clear();
    if (issuer.empty() || !m_authorizer || expectedBindings.empty())
    {
        reason = "SESSION_CONTROL_PLANE_UNAUTHORIZED";
        return false;
    }
    for (std::size_t i = 0; i < expectedBindings.size(); ++i)
    {
        TradingToolHostSessionBinding identity;
        identity.token = expectedBindings[i].token;
        if (!m_authorizer(issuer, identity, reason))
        {
            if (reason.empty()) reason = "SESSION_CONTROL_PLANE_DENIED";
            return false;
        }
    }
    return m_host.BeginWatchTransaction(
        expectedBindings, watchTransactionId, reason);
}

bool TradingToolSessionControlPlane::ProvisionForWatchTransaction(
    const std::string& issuer,
    const std::string& watchTransactionId,
    const TradingToolHostSessionBinding& binding,
    std::string& reason)
{
    if (issuer.empty() || !m_authorizer ||
        !m_authorizer(issuer, binding, reason))
    {
        if (reason.empty()) reason = "SESSION_CONTROL_PLANE_DENIED";
        return false;
    }
    return m_host.RegisterSessionForWatchTransaction(
        watchTransactionId, binding, reason);
}

bool TradingToolSessionControlPlane::RotateForWatchTransaction(
    const std::string& issuer,
    const std::string& watchTransactionId,
    const TradingToolHostSessionBinding& expectedCurrent,
    const std::string& token,
    const std::string& replacementToken,
    std::uint64_t expectedGeneration,
    std::uint64_t expiresAtMs,
    std::uint64_t& newGeneration,
    std::string& reason)
{
    TradingToolHostSessionBinding existing;
    if (issuer.empty() || !m_authorizer || !m_host.GetSession(token, existing))
    {
        reason = issuer.empty() || !m_authorizer ?
            "SESSION_CONTROL_PLANE_UNAUTHORIZED" : "SESSION_NOT_FOUND";
        return false;
    }
    TradingToolHostSessionBinding replacement = existing;
    replacement.token = replacementToken;
    replacement.expiresAtMs = expiresAtMs;
    if (!m_authorizer(issuer, replacement, reason))
    {
        if (reason.empty()) reason = "SESSION_CONTROL_PLANE_DENIED";
        return false;
    }
    return m_host.UpdateSessionLeaseForWatchTransaction(
        watchTransactionId, expectedCurrent, token, replacementToken,
        expectedGeneration, expiresAtMs, newGeneration, reason);
}

bool TradingToolSessionControlPlane::RevokeExactWatchTransaction(
    const std::string& issuer,
    const std::string& watchTransactionId,
    const std::vector<TradingToolHostSessionBinding>& expectedBindings,
    const std::string& revokeReason,
    bool& allLocalAbsent,
    std::string& reason)
{
    allLocalAbsent = false;
    if (issuer.empty() || !m_authorizer || expectedBindings.empty())
    {
        reason = "SESSION_CONTROL_PLANE_UNAUTHORIZED";
        return false;
    }
    for (std::size_t i = 0; i < expectedBindings.size(); ++i)
    {
        TradingToolHostSessionBinding identity;
        identity.token = expectedBindings[i].token;
        if (!m_authorizer(issuer, identity, reason))
        {
            if (reason.empty()) reason = "SESSION_CONTROL_PLANE_DENIED";
            return false;
        }
    }
    return m_host.RevokeExactWatchTransaction(
        watchTransactionId, expectedBindings, revokeReason,
        allLocalAbsent, reason);
}

bool TradingToolSessionControlPlane::ReleaseWatchTransaction(
    const std::string& issuer,
    const std::string& watchTransactionId,
    const std::vector<TradingToolHostSessionBinding>& expectedBindings,
    std::string& reason)
{
    if (issuer.empty() || !m_authorizer || expectedBindings.empty())
    {
        reason = "SESSION_CONTROL_PLANE_UNAUTHORIZED";
        return false;
    }
    for (std::size_t i = 0; i < expectedBindings.size(); ++i)
    {
        TradingToolHostSessionBinding identity;
        identity.token = expectedBindings[i].token;
        if (!m_authorizer(issuer, identity, reason))
        {
            if (reason.empty()) reason = "SESSION_CONTROL_PLANE_DENIED";
            return false;
        }
    }
    return m_host.ReleaseWatchTransaction(
        watchTransactionId, expectedBindings, reason);
}

bool TradingToolSessionControlPlane::RevokeExpired(
    const std::string& issuer,
    const std::string& token,
    std::uint64_t expectedGeneration,
    std::string& reason)
{
    return RevokeWithReason(
        issuer, token, expectedGeneration, "session_expired", reason);
}

bool TradingToolSessionControlPlane::RevokeWithReason(
    const std::string& issuer,
    const std::string& token,
    std::uint64_t expectedGeneration,
    const std::string& revokeReason,
    std::string& reason)
{
    TradingToolHostSessionBinding identity;
    identity.token = token;
    if (issuer.empty() || !m_authorizer || !m_authorizer(issuer, identity, reason))
    {
        if (reason.empty()) reason = "SESSION_CONTROL_PLANE_DENIED";
        return false;
    }
    return m_host.RevokeSession(
        token, expectedGeneration, revokeReason, reason);
}

bool TradingToolSessionControlPlane::FenceRestored(
    const std::string& issuer,
    const TradingToolHostSessionBinding& binding,
    const std::string& revokeReason,
    std::string& reason)
{
    // Recovery is a revoke, not a new provisioning decision. The reviewed
    // resolver already reconstructed the stored scope; authorize the durable
    // token/issuer exactly as the normal revoke path so an expired timestamp
    // cannot turn recovery into an impossible new-provision authorization.
    TradingToolHostSessionBinding identity;
    identity.token = binding.token;
    if (issuer.empty() || !m_authorizer ||
        !m_authorizer(issuer, identity, reason))
    {
        if (reason.empty()) reason = "SESSION_CONTROL_PLANE_DENIED";
        return false;
    }
    return m_host.FenceRestoredSession(binding, revokeReason, reason);
}

bool TradingToolSessionControlPlane::Renew(
    const std::string& issuer,
    const std::string& token,
    std::uint64_t expectedGeneration,
    std::uint64_t expiresAtMs,
    std::uint64_t& newGeneration,
    std::string& reason)
{
    return Rotate(issuer, token, token, expectedGeneration, expiresAtMs, newGeneration, reason);
}

bool TradingToolSessionControlPlane::FinalizeRecoveryOnlyOwner(
    const std::string& issuer,
    const std::string& token,
    std::uint64_t expectedGeneration,
    const SessionSupervisorLeaseRecord& durableRecord,
    ExecutionControlResult& ownerAudit,
    std::string& reason)
{
    TradingToolHostSessionBinding identity;
    identity.token = token;
    if (issuer.empty() || !m_authorizer ||
        !m_authorizer(issuer, identity, reason))
    {
        if (reason.empty()) reason = "SESSION_CONTROL_PLANE_DENIED";
        return false;
    }
    return m_host.FinalizeRecoveryOnlyOwner(
        token, expectedGeneration, durableRecord, ownerAudit, reason);
}

bool TradingToolSessionControlPlane::FenceRecoveryOnlyOwner(
    const std::string& issuer,
    const std::string& token,
    std::uint64_t expectedGeneration,
    const SessionSupervisorLeaseRecord& durableRecord,
    std::string& reason)
{
    TradingToolHostSessionBinding identity;
    identity.token = token;
    if (issuer.empty() || !m_authorizer ||
        !m_authorizer(issuer, identity, reason))
    {
        if (reason.empty()) reason = "SESSION_CONTROL_PLANE_DENIED";
        return false;
    }
    return m_host.FenceRecoveryOnlyOwner(
        token, expectedGeneration, durableRecord, reason);
}

bool TradingToolSessionControlPlane::AuditFinalizedRecoveryOwner(
    const std::string& issuer,
    const SessionSupervisorLeaseRecord& durableRecord,
    ExecutionControlResult& ownerAudit,
    std::string& reason)
{
    TradingToolHostSessionBinding identity;
    identity.token = durableRecord.token;
    if (issuer.empty() || !m_authorizer ||
        !m_authorizer(issuer, identity, reason))
    {
        if (reason.empty()) reason = "SESSION_CONTROL_PLANE_DENIED";
        return false;
    }
    return m_host.AuditFinalizedRecoveryOwner(
        durableRecord, ownerAudit, reason);
}

bool TradingToolSessionControlPlane::TerminalizeFinalizedRecoveryOwner(
    const std::string& issuer,
    const SessionSupervisorLeaseRecord& durableRecord,
    const std::string& preliminaryReceiptSha256,
    ExecutionControlResult& terminalResult,
    std::string& reason)
{
    TradingToolHostSessionBinding identity;
    identity.token = durableRecord.token;
    if (issuer.empty() || !m_authorizer ||
        !m_authorizer(issuer, identity, reason))
    {
        if (reason.empty()) reason = "SESSION_CONTROL_PLANE_DENIED";
        return false;
    }
    return m_host.TerminalizeFinalizedRecoveryOwner(
        durableRecord, preliminaryReceiptSha256,
        terminalResult, reason);
}

bool TradingToolSessionControlPlane::PurgeFinalizedRecoveryOwner(
    const std::string& issuer,
    const SessionSupervisorLeaseRecord& durableRecord,
    std::string& reason)
{
    TradingToolHostSessionBinding identity;
    identity.token = durableRecord.token;
    if (issuer.empty() || !m_authorizer ||
        !m_authorizer(issuer, identity, reason))
    {
        if (reason.empty()) reason = "SESSION_CONTROL_PLANE_DENIED";
        return false;
    }
    return m_host.PurgeFinalizedRecoveryOwner(durableRecord, reason);
}

bool TradingToolSessionControlPlane::RestorePaperFinalizationTombstone(
    const std::string& issuer,
    const TradingToolHostSessionBinding& binding,
    const SessionSupervisorLeaseRecord& durableRecord,
    std::string& reason)
{
    if (issuer.empty() || !m_authorizer ||
        !m_authorizer(issuer, binding, reason))
    {
        if (reason.empty()) reason = "SESSION_CONTROL_PLANE_DENIED";
        return false;
    }
    return m_host.RestorePaperFinalizationTombstone(
        binding, durableRecord, reason);
}

bool TradingToolSessionControlPlane::RenewPaperAfterAudit(
    const std::string& issuer,
    const std::string& token,
    const std::string& replacementToken,
    std::uint64_t expectedGeneration,
    std::uint64_t expiresAtMs,
    std::uint64_t& newGeneration,
    ExecutionControlResult& ownerAudit,
    std::string& reason)
{
    TradingToolHostSessionBinding existing;
    if (issuer.empty() || !m_authorizer ||
        !m_host.GetSession(token, existing))
    {
        reason = issuer.empty() || !m_authorizer ?
            "SESSION_CONTROL_PLANE_UNAUTHORIZED" : "SESSION_NOT_FOUND";
        return false;
    }
    TradingToolHostSessionBinding replacement = existing;
    replacement.token = replacementToken;
    replacement.expiresAtMs = expiresAtMs;
    if (!m_authorizer(issuer, replacement, reason))
    {
        if (reason.empty()) reason = "SESSION_CONTROL_PLANE_DENIED";
        return false;
    }
    return m_host.UpdatePaperSessionLeaseAfterAudit(
        token, replacementToken, expectedGeneration, expiresAtMs,
        newGeneration, ownerAudit, reason);
}

bool TradingToolSessionControlPlane::Rotate(
    const std::string& issuer,
    const std::string& token,
    const std::string& replacementToken,
    std::uint64_t expectedGeneration,
    std::uint64_t expiresAtMs,
    std::uint64_t& newGeneration,
    std::string& reason)
{
    TradingToolHostSessionBinding existing;
    if (issuer.empty() || !m_authorizer || !m_host.GetSession(token, existing))
    {
        reason = issuer.empty() || !m_authorizer ?
            "SESSION_CONTROL_PLANE_UNAUTHORIZED" : "SESSION_NOT_FOUND";
        return false;
    }
    TradingToolHostSessionBinding replacement = existing;
    replacement.token = replacementToken;
    replacement.expiresAtMs = expiresAtMs;
    if (!m_authorizer(issuer, replacement, reason))
    {
        if (reason.empty()) reason = "SESSION_CONTROL_PLANE_DENIED";
        return false;
    }
    return m_host.UpdateSessionLease(token, replacementToken, expectedGeneration,
        expiresAtMs, newGeneration, reason);
}

std::size_t TradingToolSessionControlPlane::ReapExpired(std::uint64_t nowMs)
{
    return m_host.ReapExpiredSessions(nowMs);
}
