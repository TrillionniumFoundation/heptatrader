#pragma once

#include "trading_tool_host.h"

#include <functional>
#include <string>

class UnixSessionSupervisorServer;

class TradingToolSessionControlPlane
{
public:
    typedef std::function<bool(const std::string&, const TradingToolHostSessionBinding&, std::string&)>
        Authorizer;

    TradingToolSessionControlPlane(TradingToolHost& host, const Authorizer& authorizer);

    bool Provision(const std::string& issuer,
                   const TradingToolHostSessionBinding& binding,
                   std::string& reason);
    bool Revoke(const std::string& issuer,
                const std::string& token,
                std::uint64_t expectedGeneration,
                std::string& reason);
    bool RevokeCurrentIfOwner(const std::string& issuer,
                              const std::string& token,
                              const std::string& expectedAgentId,
                              const std::string& expectedSessionId,
                              const std::string& revokeReason,
                              std::string& reason);
    bool RevokeExpired(const std::string& issuer,
                       const std::string& token,
                       std::uint64_t expectedGeneration,
                       std::string& reason);
    bool FenceRestored(const std::string& issuer,
                       const TradingToolHostSessionBinding& binding,
                       const std::string& revokeReason,
                       std::string& reason);
    bool EnterRecoveryOnlyAndQuery(
        const std::string& issuer,
        const std::string& token,
        std::uint64_t expectedGeneration,
        const std::string& targetCommandId,
        SessionSupervisorLeaseStore& leaseStore,
        SessionSupervisorLeaseRecord& durableRecord,
        ExecutionControlResult& result,
        std::string& reason,
        ExecutionControlResult* ownerAudit = nullptr,
        std::uint64_t recoveryExpiresAtMs = 0,
        const std::string& durableCurrentToken = std::string())
    {
        TradingToolHostSessionBinding identity;
        identity.token = token;
        if (issuer.empty() || !m_authorizer ||
            !m_authorizer(issuer, identity, reason))
        {
            if (reason.empty()) reason = "SESSION_CONTROL_PLANE_DENIED";
            return false;
        }
        return m_host.EnterRecoveryOnlyAndQuery(
            token, expectedGeneration, targetCommandId, leaseStore,
            durableRecord, result, reason, nullptr, nullptr,
            ownerAudit, recoveryExpiresAtMs, durableCurrentToken);
    }
    bool FinalizeRecoveryOnlyOwner(
        const std::string& issuer,
        const std::string& token,
        std::uint64_t expectedGeneration,
        const SessionSupervisorLeaseRecord& durableRecord,
        ExecutionControlResult& ownerAudit,
        std::string& reason);
    bool FenceRecoveryOnlyOwner(
        const std::string& issuer,
        const std::string& token,
        std::uint64_t expectedGeneration,
        const SessionSupervisorLeaseRecord& durableRecord,
        std::string& reason);
    bool AuditFinalizedRecoveryOwner(
        const std::string& issuer,
        const SessionSupervisorLeaseRecord& durableRecord,
        ExecutionControlResult& ownerAudit,
        std::string& reason);
	bool TerminalizeFinalizedRecoveryOwner(
		const std::string& issuer,
		const SessionSupervisorLeaseRecord& durableRecord,
		const std::string& preliminaryReceiptSha256,
		ExecutionControlResult& terminalResult,
		std::string& reason);
    bool PurgeFinalizedRecoveryOwner(
        const std::string& issuer,
        const SessionSupervisorLeaseRecord& durableRecord,
        std::string& reason);
    bool RestorePaperFinalizationTombstone(
        const std::string& issuer,
        const TradingToolHostSessionBinding& binding,
        const SessionSupervisorLeaseRecord& durableRecord,
        std::string& reason);
    bool RenewPaperAfterAudit(
        const std::string& issuer,
        const std::string& token,
        const std::string& replacementToken,
        std::uint64_t expectedGeneration,
        std::uint64_t expiresAtMs,
        std::uint64_t& newGeneration,
        ExecutionControlResult& ownerAudit,
        std::string& reason);
    bool Renew(const std::string& issuer,
               const std::string& token,
               std::uint64_t expectedGeneration,
               std::uint64_t expiresAtMs,
               std::uint64_t& newGeneration,
               std::string& reason);
    bool Rotate(const std::string& issuer,
                const std::string& token,
                const std::string& replacementToken,
                std::uint64_t expectedGeneration,
                std::uint64_t expiresAtMs,
                std::uint64_t& newGeneration,
                std::string& reason);
    std::size_t ReapExpired(std::uint64_t nowMs);

private:
    friend class UnixSessionSupervisorServer;

    bool BeginWatchTransaction(
        const std::string& issuer,
        const std::vector<TradingToolHostSessionBinding>& expectedBindings,
        std::string& watchTransactionId,
        std::string& reason);
    bool ProvisionForWatchTransaction(
        const std::string& issuer,
        const std::string& watchTransactionId,
        const TradingToolHostSessionBinding& binding,
        std::string& reason);
    bool RotateForWatchTransaction(
        const std::string& issuer,
        const std::string& watchTransactionId,
        const TradingToolHostSessionBinding& expectedCurrent,
        const std::string& token,
        const std::string& replacementToken,
        std::uint64_t expectedGeneration,
        std::uint64_t expiresAtMs,
        std::uint64_t& newGeneration,
        std::string& reason);
    bool RevokeExactWatchTransaction(
        const std::string& issuer,
        const std::string& watchTransactionId,
        const std::vector<TradingToolHostSessionBinding>& expectedBindings,
        const std::string& revokeReason,
        bool& allLocalAbsent,
        std::string& reason);
    bool ReleaseWatchTransaction(const std::string& issuer,
                                 const std::string& watchTransactionId,
                                 const std::vector<TradingToolHostSessionBinding>&
                                     expectedBindings,
                                 std::string& reason);
    bool RevokeWithReason(const std::string& issuer,
                          const std::string& token,
                          std::uint64_t expectedGeneration,
                          const std::string& revokeReason,
                          std::string& reason);

    TradingToolHost& m_host;
    Authorizer m_authorizer;
};
