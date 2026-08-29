#pragma once

#include "../agent/decision_lease_manager.h"
#include "../tools/trading_tool_registry.h"
#include "trading_tool_session_catalog.h"

#include <cstdint>
#include <functional>
#include <mutex>
#include <string>
#include <unordered_map>
#include <unordered_set>
#include <vector>

struct TradingToolHostSessionBinding
{
    std::string token;
    std::uint32_t peerUid = 0;
    TradingToolSession session;
    std::uint64_t expiresAtMs = 0;
    std::uint64_t leaseGeneration = 1;
    bool enabled = true;
    // Durable root-custodian admission fence.  Reads and owned risk reduction
    // remain available, but no new entry or entry preview may cross the host.
    bool recoveryOnly = false;
    std::unordered_set<std::string> allowedInstruments;
    std::unordered_map<std::string, IBContractLite> instrumentContracts;
    double maxOrderQuantity = 0.0;
    std::uint32_t maxTradeCallsPerMinute = 0;
    std::string executionDomain;
    std::uint32_t decisionLeaseTtlMs = 5000;
};

struct TradingToolHostRequest
{
    std::string sessionToken;
    std::string toolCallId;
    unsigned int protocolMinVersion = 1;
    unsigned int protocolMaxVersion = 1;
    std::string expectedSchemaHash;
    std::uint64_t queueDeadlineAtMs = 0;
    std::string cancelToolCallId;
    TradingToolCall call;
};

typedef std::function<bool(const TradingToolSession&, const TradingToolCall&, std::string&)>
    TradingToolMutationReadiness;
typedef std::function<bool(const TradingToolHostSessionBinding&,
                           const std::string&,
                           std::string&)>
    TradingToolSessionRevokedObserver;
typedef void (*TradingToolRecoveryFenceCommittedHook)(void*);

class TradingToolSessionControlPlane;
class SessionSupervisorLeaseStore;
struct SessionSupervisorLeaseRecord;

// Security boundary between an Agent process and the typed tool registry.
// Account, environment, Agent identity and capabilities come exclusively from
// a server-side session binding; request data can never grant privileges.
class TradingToolHost
{
public:
    explicit TradingToolHost(TradingToolRegistry& registry);
    TradingToolHost(TradingToolRegistry& registry,
                    DecisionLeaseManager& decisionLeases,
                    const TradingToolMutationReadiness& mutationReadiness = TradingToolMutationReadiness());

    bool RegisterSession(const TradingToolHostSessionBinding& binding, std::string& reason);
    bool GetSession(const std::string& token, TradingToolHostSessionBinding& binding) const;
    bool UpdateSessionLease(const std::string& currentToken,
                            const std::string& replacementToken,
                            std::uint64_t expectedGeneration,
                            std::uint64_t expiresAtMs,
                            std::uint64_t& newGeneration,
                            std::string& reason);
    void RevokeSession(const std::string& token);
    bool RevokeSession(const std::string& token,
                       std::uint64_t expectedGeneration,
                       std::string& reason);
    bool RevokeSession(const std::string& token,
                       std::uint64_t expectedGeneration,
                       const std::string& revokeReason,
                       std::string& reason);
    bool RevokeCurrentSessionIfOwner(const std::string& token,
                                     const std::string& expectedAgentId,
                                     const std::string& expectedSessionId,
                                     const std::string& revokeReason,
                                     std::string& reason);
    bool FenceRestoredSession(const TradingToolHostSessionBinding& binding,
                              const std::string& revokeReason,
                              std::string& reason);
    bool EnterRecoveryOnlyAndQuery(
        const std::string& token,
        std::uint64_t expectedGeneration,
        const std::string& targetCommandId,
        SessionSupervisorLeaseStore& leaseStore,
        SessionSupervisorLeaseRecord& durableRecord,
        ExecutionControlResult& result,
        std::string& reason,
        TradingToolRecoveryFenceCommittedHook committedHook = nullptr,
        void* committedHookContext = nullptr,
        ExecutionControlResult* ownerAudit = nullptr,
        std::uint64_t recoveryExpiresAtMs = 0,
        const std::string& durableCurrentToken = std::string());
    bool FinalizeRecoveryOnlyOwner(
        const std::string& token,
        std::uint64_t expectedGeneration,
        const SessionSupervisorLeaseRecord& durableRecord,
        ExecutionControlResult& ownerAudit,
        std::string& reason);
    // PAPER finalization is deliberately split into three independently
    // retryable phases.  The HSL7 state machine, rather than local absence,
    // is the proof that each phase is allowed to run.
    bool FenceRecoveryOnlyOwner(
        const std::string& token,
        std::uint64_t expectedGeneration,
        const SessionSupervisorLeaseRecord& durableRecord,
        std::string& reason);
    bool AuditFinalizedRecoveryOwner(
        const SessionSupervisorLeaseRecord& durableRecord,
        ExecutionControlResult& ownerAudit,
        std::string& reason);
	bool TerminalizeFinalizedRecoveryOwner(
		const SessionSupervisorLeaseRecord& durableRecord,
		const std::string& preliminaryReceiptSha256,
		ExecutionControlResult& terminalResult,
		std::string& reason);
    bool PurgeFinalizedRecoveryOwner(
        const SessionSupervisorLeaseRecord& durableRecord,
        std::string& reason);
    bool RestorePaperFinalizationTombstone(
        const TradingToolHostSessionBinding& binding,
        const SessionSupervisorLeaseRecord& durableRecord,
        std::string& reason);
    bool UpdatePaperSessionLeaseAfterAudit(
        const std::string& currentToken,
        const std::string& replacementToken,
        std::uint64_t expectedGeneration,
        std::uint64_t expiresAtMs,
        std::uint64_t& newGeneration,
        ExecutionControlResult& ownerAudit,
        std::string& reason);
    std::size_t ReapExpiredSessions(std::uint64_t nowMs);
    TradingToolResult Invoke(std::uint32_t peerUid, const TradingToolHostRequest& request);
    TradingToolResult AuthorizeControlRequest(
        std::uint32_t peerUid,
        const TradingToolHostRequest& request,
        TradingToolHostSessionBinding& binding);
    bool IsMutationTool(const std::string& toolName) const;
    bool ValidateSchemaHash(const std::string& toolName,
                            const std::string& expectedSchemaHash,
                            std::string& actualSchemaHash) const;
    std::size_t SessionCount() const;
    std::vector<TradingToolHostSessionBinding> ListSessions() const;
    TradingToolSessionContractCatalogSnapshot GetContractCatalogSnapshot() const;
    void SetContractCatalogObserver(const TradingToolSessionContractCatalog::Observer& observer);
    void SetSessionRevokedObserver(const TradingToolSessionRevokedObserver& observer);
    void SetRecoveryControlAuthority(ExecutionControlAuthority* authority)
    {
        std::lock_guard<std::mutex> lock(m_mutex);
        m_recoveryControlAuthority = authority;
    }

private:
    friend class TradingToolSessionControlPlane;

    struct WatchTransactionReservation
    {
        std::string ownerKey;
        std::unordered_set<std::string> tokens;
        std::vector<TradingToolHostSessionBinding> expectedBindings;
    };

    struct ActiveDecisionLease
    {
        std::string sessionToken;
        DecisionLeaseKey key;
        DecisionLeaseOwner owner;
        DecisionLeaseCredential credential;
    };

    struct MutationReplayRecord
    {
        std::string ownerKey;
        TradingToolSession session;
        TradingToolCall call;
        TradingToolResult result;
    };

    static TradingToolResult Reject(const std::string& toolName,
                                    TradingToolCallStatus status,
                                    const std::string& reasonCode,
                                    const std::string& detail);
    static std::string SessionLeaseKey(const std::string& token, const std::string& instrument);
    static std::string SessionOwnerKey(const TradingToolHostSessionBinding& binding);
    static std::string MutationReplayKey(
        const TradingToolHostSessionBinding& binding,
        const std::string& toolCallId);
    static bool ValidateSessionTradePolicy(
        const TradingToolHostSessionBinding& binding,
        std::string& reason);
    bool RegisterSessionImpl(const TradingToolHostSessionBinding& binding,
                             const std::string* watchTransactionId,
                             std::string& reason);
    bool UpdateSessionLeaseImpl(const std::string& currentToken,
                                const std::string& replacementToken,
                                std::uint64_t expectedGeneration,
                                std::uint64_t expiresAtMs,
                                std::uint64_t& newGeneration,
                                const std::string* watchTransactionId,
                                const TradingToolHostSessionBinding*
                                    expectedCurrent,
                                std::string& reason,
                                bool dispatchLocked = false);
    void MoveActiveDecisionLeasesLocked(
        const std::string& currentToken,
        const std::string& replacementToken);
    void MoveSessionRateBudgetsLocked(
        const std::string& currentToken,
        const std::string& replacementToken);
    bool BeginWatchTransaction(
        const std::vector<TradingToolHostSessionBinding>& expectedBindings,
        std::string& watchTransactionId,
        std::string& reason);
    bool RegisterSessionForWatchTransaction(
        const std::string& watchTransactionId,
        const TradingToolHostSessionBinding& binding,
        std::string& reason);
    bool UpdateSessionLeaseForWatchTransaction(
        const std::string& watchTransactionId,
        const TradingToolHostSessionBinding& expectedCurrent,
        const std::string& currentToken,
        const std::string& replacementToken,
        std::uint64_t expectedGeneration,
        std::uint64_t expiresAtMs,
        std::uint64_t& newGeneration,
        std::string& reason);
    bool RevokeExactWatchTransaction(
        const std::string& watchTransactionId,
        const std::vector<TradingToolHostSessionBinding>& expectedBindings,
        const std::string& revokeReason,
        bool& allLocalAbsent,
        std::string& reason);
    bool ReleaseWatchTransaction(
        const std::string& watchTransactionId,
        const std::vector<TradingToolHostSessionBinding>& expectedBindings,
                                 std::string& reason);
    bool WatchTransactionAllowsLocked(
        const std::string* watchTransactionId,
        const std::string& ownerKey,
        const std::string& token) const;
    bool WatchTransactionPendingLocked(
        const TradingToolHostSessionBinding& binding) const;
    static bool WatchBindingScopeEqual(
        const TradingToolHostSessionBinding& left,
        const TradingToolHostSessionBinding& right);
    bool ValidateWatchTransactionBindings(
        const std::vector<TradingToolHostSessionBinding>& expectedBindings,
        std::string& ownerKey,
        std::unordered_set<std::string>& tokens,
        std::string& reason) const;
    bool ValidateWatchReservationScopeLocked(
        const WatchTransactionReservation& reservation,
        const std::vector<TradingToolHostSessionBinding>& expectedBindings,
        std::unordered_set<std::string>& expectedTokens,
        std::string& reason) const;
    bool CollectWatchRevokeTargetsLocked(
        const WatchTransactionReservation& reservation,
        const std::vector<TradingToolHostSessionBinding>& expectedBindings,
        const std::unordered_set<std::string>& expectedTokens,
        std::vector<std::pair<std::string, std::uint64_t> >&
            revokeTargets,
        std::string& reason);
    bool RevokeSessionWithReason(const std::string& token,
                                 const std::string& revokeReason,
                                 std::string& reason);
    bool RevokeSessionUnderDispatchLock(const std::string& token,
                                        std::uint64_t expectedGeneration,
                                        const std::string* expectedAgentId,
                                        const std::string* expectedSessionId,
                                        const std::string* watchTransactionId,
                                        const std::string& revokeReason,
                                        std::string& reason);
    bool PrepareRecoveryOnlyBinding(
        const std::string& token,
        std::uint64_t expectedGeneration,
        TradingToolHostSessionBinding& binding,
        ExecutionControlAuthority*& authority,
        std::string& reason);
    void EraseSessionLocked(const std::string& token);
    TradingToolResult EnsureDecisionLease(const TradingToolHostSessionBinding& binding,
                                          const std::string& sessionToken,
                                          const TradingToolCall& call,
                                          DecisionLeaseCredential& credential);
    TradingToolResult AuthorizeCommon(
        std::uint32_t peerUid,
        const TradingToolHostRequest& request,
        TradingToolHostSessionBinding& binding,
        TradingToolDescriptor& descriptor);
    TradingToolResult PrepareReadCall(
        const TradingToolHostSessionBinding& binding,
        TradingToolCall& call) const;
    TradingToolResult PrepareMutationCall(
        const TradingToolHostSessionBinding& binding,
        TradingToolCall& call) const;
    TradingToolSession BuildDispatchSession(
        const TradingToolHostSessionBinding& binding,
        const TradingToolHostRequest& request,
        const DecisionLeaseCredential& credential) const;
    TradingToolResult DispatchRead(
        const TradingToolHostSessionBinding& binding,
        const TradingToolHostRequest& request,
        const TradingToolSession& session,
        const TradingToolCall& call);
    TradingToolResult DispatchMutation(
        const TradingToolHostSessionBinding& binding,
        const TradingToolHostRequest& request,
        const TradingToolSession& session,
        const TradingToolCall& call);

private:
    TradingToolRegistry& m_registry;
    // Linearizes every final dispatch gate with WATCH reservation,
    // session revoke/fence and lease rotation. A call holds this lock from its
    // last binding validation through the registry/authority call; control
    // operations take it before changing the binding. Therefore a dispatch is
    // wholly ordered before the control operation, or observes its
    // disabled/pending binding and never reaches the registered handler.
    mutable std::mutex m_mutationDispatchMutex;
    mutable std::mutex m_mutex;
    std::unordered_map<std::string, TradingToolHostSessionBinding> m_sessions;
    std::unordered_map<std::string, std::uint64_t> m_rateWindowStartMs;
    std::unordered_map<std::string, std::uint32_t> m_tradeCallsInWindow;
    std::unordered_map<std::string, std::uint64_t>
        m_riskReductionWindowStartMs;
    std::unordered_map<std::string, std::uint32_t>
        m_riskReductionCallsInWindow;
    std::unordered_map<std::string, std::uint64_t>
        m_flattenWindowStartMs;
    std::unordered_map<std::string, std::uint32_t>
        m_flattenCallsInWindow;
    std::unordered_map<std::string, MutationReplayRecord>
        m_mutationReplays;
    DecisionLeaseManager m_ownedDecisionLeases;
    DecisionLeaseManager& m_decisionLeases;
    TradingToolMutationReadiness m_mutationReadiness;
    std::unordered_map<std::string, ActiveDecisionLease> m_activeDecisionLeases;
    std::unordered_set<std::string> m_pendingOwnerFences;
    std::unordered_map<std::string, WatchTransactionReservation>
        m_watchTransactions;
    std::unordered_map<std::string, std::string> m_watchOwnerTransactions;
    std::unordered_map<std::string, std::string> m_watchTokenTransactions;
    std::uint64_t m_nextWatchTransactionId = 1;
    TradingToolSessionContractCatalog m_contractCatalog;
    TradingToolSessionRevokedObserver m_sessionRevokedObserver;
    ExecutionControlAuthority* m_recoveryControlAuthority = nullptr;
};
