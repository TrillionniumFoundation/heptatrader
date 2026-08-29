#include "trading_tool_host.h"
#include "../state/ib_contract_identity.h"
#include <chrono>
#include <cmath>
namespace
{
std::uint64_t LifecycleEpochNowMs()
{
    return static_cast<std::uint64_t>(
        std::chrono::duration_cast<std::chrono::milliseconds>(
            std::chrono::system_clock::now().time_since_epoch()).count());
}
bool IsKnownSessionEnvironment(const std::string& environment)
{
    return environment == "WATCH" || environment == "PAPER" ||
        environment == "LIVE_REDUCE_ONLY" ||
        environment == "LIVE_CAPPED";
}

bool ValidateSessionShape(
    const TradingToolHostSessionBinding& binding,
    std::uint64_t nowMs,
    std::string& reason)
{
    if (binding.token.size() < 24)
    {
        reason = "SESSION_TOKEN_TOO_SHORT";
        return false;
    }
    if (binding.session.executionContext.agentId.empty() ||
        binding.session.executionContext.sessionId.empty() ||
        binding.session.executionContext.account.empty() ||
        binding.executionDomain.empty())
    {
        reason = "INVALID_SESSION_IDENTITY";
        return false;
    }
    if (!IsKnownSessionEnvironment(binding.session.environment))
    {
        reason = "INVALID_SESSION_ENVIRONMENT";
        return false;
    }
    if (binding.expiresAtMs <= nowMs)
    {
        reason = "SESSION_ALREADY_EXPIRED";
        return false;
    }
    if (binding.leaseGeneration == 0)
    {
        reason = "SESSION_LEASE_GENERATION_REQUIRED";
        return false;
    }
    return true;
}

TradingToolSessionContractRegistration BuildCatalogRegistration(
    const TradingToolHostSessionBinding& binding)
{
    TradingToolSessionContractRegistration registration;
    registration.token = binding.token;
    registration.agentId = binding.session.executionContext.agentId;
    registration.sessionId = binding.session.executionContext.sessionId;
    registration.expiresAtMs = binding.expiresAtMs;
    for (std::unordered_map<std::string, IBContractLite>::const_iterator
             contract = binding.instrumentContracts.begin();
         contract != binding.instrumentContracts.end(); ++contract)
        registration.contracts[contract->first] = contract->second;
    return registration;
}

bool MatchesExpectedCurrent(
    const TradingToolHostSessionBinding& current,
    const TradingToolHostSessionBinding& expected)
{
    return current.token == expected.token &&
        current.peerUid == expected.peerUid &&
        current.session.environment == expected.session.environment &&
        current.leaseGeneration == expected.leaseGeneration &&
        current.session.executionContext.agentId ==
            expected.session.executionContext.agentId &&
        current.session.executionContext.sessionId ==
            expected.session.executionContext.sessionId;
}
}
bool TradingToolHost::ValidateSessionTradePolicy(
    const TradingToolHostSessionBinding& binding,
    std::string& reason)
{
    const bool canPlace =
        binding.session.capabilities.find("trade.place") !=
        binding.session.capabilities.end();
    const bool canFlatten =
        binding.session.capabilities.find("trade.flatten") !=
        binding.session.capabilities.end();
    const bool canInstrumentMutation = canPlace || canFlatten;
    const bool canTrade = canInstrumentMutation ||
        binding.session.capabilities.find("trade.cancel") !=
            binding.session.capabilities.end();
    if (canTrade && binding.session.environment == "WATCH")
    {
        reason = "WATCH_SESSION_CANNOT_TRADE";
        return false;
    }
    if (canPlace && binding.session.environment == "LIVE_REDUCE_ONLY")
    {
        reason = "REDUCE_ONLY_PLACE_FORBIDDEN";
        return false;
    }
    if (canInstrumentMutation &&
        (binding.allowedInstruments.empty() ||
         binding.maxOrderQuantity <= 0.0 ||
         !std::isfinite(binding.maxOrderQuantity) ||
         binding.maxTradeCallsPerMinute == 0))
    {
        reason = "TRADE_SESSION_LIMITS_REQUIRED";
        return false;
    }
    if (canTrade &&
        (binding.executionDomain.empty() ||
         binding.maxTradeCallsPerMinute == 0))
    {
        reason = "TRADE_SESSION_CONFIG_REQUIRED";
        return false;
    }
    if (canInstrumentMutation &&
        (binding.decisionLeaseTtlMs < 5000 ||
         binding.decisionLeaseTtlMs > 60000))
    {
        reason = "DECISION_LEASE_CONFIG_REQUIRED";
        return false;
    }
    if (!canInstrumentMutation) return true;
    for (std::unordered_set<std::string>::const_iterator instrument =
             binding.allowedInstruments.begin();
         instrument != binding.allowedInstruments.end(); ++instrument)
    {
        const std::unordered_map<std::string, IBContractLite>::const_iterator
            contract = binding.instrumentContracts.find(*instrument);
        if (contract == binding.instrumentContracts.end() ||
            contract->second.symbol.empty() ||
            contract->second.secType.empty() ||
            contract->second.exchange.empty())
        {
            reason = "SERVER_CONTRACT_BINDING_REQUIRED";
            return false;
        }
        if (BuildIBAuthoritativeInstrumentIdentity(
                contract->second, *instrument) != *instrument)
        {
            reason = "SERVER_CONTRACT_IDENTITY_MISMATCH";
            return false;
        }
    }
    return true;
}

bool TradingToolHost::RegisterSession(
    const TradingToolHostSessionBinding& binding,
    std::string& reason)
{
    return RegisterSessionImpl(binding, nullptr, reason);
}

bool TradingToolHost::RegisterSessionImpl(
    const TradingToolHostSessionBinding& binding,
    const std::string* watchTransactionId,
    std::string& reason)
{
    const std::uint64_t nowMs = LifecycleEpochNowMs();
    if (!ValidateSessionShape(binding, nowMs, reason) ||
        !ValidateSessionTradePolicy(binding, reason))
        return false;
    std::lock_guard<std::mutex> dispatchLock(m_mutationDispatchMutex);
    std::lock_guard<std::mutex> lock(m_mutex);
    const std::string ownerKey = SessionOwnerKey(binding);
    if (m_watchTokenTransactions.find(binding.token) !=
            m_watchTokenTransactions.end() &&
        !WatchTransactionAllowsLocked(
            watchTransactionId, ownerKey, binding.token))
    {
        reason = "SESSION_TOKEN_FENCE_PENDING";
        return false;
    }
    if (m_watchOwnerTransactions.find(ownerKey) !=
            m_watchOwnerTransactions.end() &&
        !WatchTransactionAllowsLocked(
            watchTransactionId, ownerKey, binding.token))
    {
        reason = "SESSION_OWNER_FENCE_PENDING";
        return false;
    }
    if (watchTransactionId != nullptr)
    {
        const auto transaction =
            m_watchTransactions.find(*watchTransactionId);
        std::vector<TradingToolHostSessionBinding> exactBinding(1, binding);
        std::unordered_set<std::string> exactTokens;
        if (transaction == m_watchTransactions.end() ||
            !ValidateWatchReservationScopeLocked(
            transaction->second, exactBinding, exactTokens, reason))
        {
            reason = "WATCH_TRANSACTION_RESERVATION_MISMATCH";
            return false;
        }
    }
    if (m_pendingOwnerFences.find(ownerKey) !=
        m_pendingOwnerFences.end())
    {
        reason = "SESSION_OWNER_FENCE_PENDING";
        return false;
    }
    if (m_sessions.find(binding.token) != m_sessions.end())
    {
        reason = "SESSION_TOKEN_EXISTS";
        return false;
    }
    const TradingToolSessionContractRegistration registration =
        BuildCatalogRegistration(binding);
    if (!m_contractCatalog.Register(registration, reason)) return false;
    m_sessions[binding.token] = binding;
    m_rateWindowStartMs[binding.token] = nowMs;
    m_tradeCallsInWindow[binding.token] = 0;
    m_riskReductionWindowStartMs[binding.token] = nowMs;
    m_riskReductionCallsInWindow[binding.token] = 0;
    m_flattenWindowStartMs[binding.token] = nowMs;
    m_flattenCallsInWindow[binding.token] = 0;
    reason.clear();
    return true;
}
bool TradingToolHost::GetSession(
    const std::string& token,
    TradingToolHostSessionBinding& binding) const
{
    std::lock_guard<std::mutex> lock(m_mutex);
    const std::unordered_map<std::string,
        TradingToolHostSessionBinding>::const_iterator found =
        m_sessions.find(token);
    if (found == m_sessions.end()) return false;
    binding = found->second;
    return true;
}

bool TradingToolHost::UpdateSessionLease(
    const std::string& currentToken,
    const std::string& replacementToken,
    std::uint64_t expectedGeneration,
    std::uint64_t expiresAtMs,
    std::uint64_t& newGeneration,
    std::string& reason)
{
    return UpdateSessionLeaseImpl(
        currentToken, replacementToken, expectedGeneration,
        expiresAtMs, newGeneration, nullptr, nullptr, reason);
}

void TradingToolHost::MoveActiveDecisionLeasesLocked(
    const std::string& currentToken,
    const std::string& replacementToken)
{
    if (replacementToken == currentToken) return;
    std::vector<std::pair<std::string, ActiveDecisionLease> > rotated;
    for (std::unordered_map<std::string, ActiveDecisionLease>::iterator lease =
             m_activeDecisionLeases.begin();
         lease != m_activeDecisionLeases.end();)
    {
        if (lease->second.sessionToken != currentToken)
        {
            ++lease;
            continue;
        }
        ActiveDecisionLease replacement = lease->second;
        replacement.sessionToken = replacementToken;
        rotated.push_back(std::make_pair(
            SessionLeaseKey(replacementToken, replacement.key.instrument),
            replacement));
        lease = m_activeDecisionLeases.erase(lease);
    }
    for (std::size_t i = 0; i < rotated.size(); ++i)
        m_activeDecisionLeases[rotated[i].first] = rotated[i].second;
}

bool TradingToolHost::UpdateSessionLeaseImpl(
    const std::string& currentToken,
    const std::string& replacementToken,
    std::uint64_t expectedGeneration,
    std::uint64_t expiresAtMs,
    std::uint64_t& newGeneration,
    const std::string* watchTransactionId,
    const TradingToolHostSessionBinding* expectedCurrent,
    std::string& reason,
    bool dispatchLocked)
{
    const std::uint64_t nowMs = LifecycleEpochNowMs();
    if (currentToken.empty() || replacementToken.size() < 24 ||
        expiresAtMs <= nowMs || expectedGeneration == 0 ||
        expectedGeneration == UINT64_MAX)
    {
        reason = "INVALID_SESSION_LEASE_UPDATE";
        return false;
    }
    std::unique_lock<std::mutex> dispatchLock(
        m_mutationDispatchMutex, std::defer_lock);
    if (!dispatchLocked) dispatchLock.lock();
    std::lock_guard<std::mutex> lock(m_mutex);
    const std::unordered_map<std::string,
        TradingToolHostSessionBinding>::iterator current =
        m_sessions.find(currentToken);
    if (current == m_sessions.end())
    {
        reason = "SESSION_NOT_FOUND";
        return false;
    }
    if (expectedCurrent != nullptr &&
        !MatchesExpectedCurrent(current->second, *expectedCurrent))
    {
        current->second.enabled = false;
        m_pendingOwnerFences.insert(SessionOwnerKey(current->second));
        reason = "WATCH_TRANSACTION_CURRENT_BINDING_MISMATCH";
        return false;
    }
    if (current->second.leaseGeneration != expectedGeneration)
    {
        reason = "SESSION_LEASE_GENERATION_MISMATCH";
        return false;
    }
    const std::string ownerKey = SessionOwnerKey(current->second);
    if (!current->second.enabled ||
        m_pendingOwnerFences.find(ownerKey) != m_pendingOwnerFences.end())
    {
        reason = "SESSION_OWNER_FENCE_PENDING";
        return false;
    }
    const bool currentAllowed = WatchTransactionAllowsLocked(
        watchTransactionId, ownerKey, currentToken);
    const bool replacementAllowed = WatchTransactionAllowsLocked(
        watchTransactionId, ownerKey, replacementToken);
    if (m_watchTokenTransactions.find(replacementToken) !=
            m_watchTokenTransactions.end() &&
        !replacementAllowed)
    {
        reason = "SESSION_TOKEN_FENCE_PENDING";
        return false;
    }
    if ((m_watchTokenTransactions.find(currentToken) !=
            m_watchTokenTransactions.end() ||
         m_watchOwnerTransactions.find(ownerKey) !=
            m_watchOwnerTransactions.end()) &&
        (!currentAllowed || !replacementAllowed))
    {
        reason = "SESSION_OWNER_FENCE_PENDING";
        return false;
    }
    if (watchTransactionId != nullptr &&
        (!currentAllowed || !replacementAllowed))
    {
        reason = "WATCH_TRANSACTION_RESERVATION_MISMATCH";
        return false;
    }
    if (replacementToken != currentToken &&
        m_sessions.find(replacementToken) != m_sessions.end())
    {
        reason = "SESSION_TOKEN_EXISTS";
        return false;
    }

    TradingToolHostSessionBinding replacement = current->second;
    replacement.token = replacementToken;
    replacement.expiresAtMs = expiresAtMs;
    replacement.leaseGeneration = expectedGeneration + 1;
    const TradingToolSessionContractRegistration registration =
        BuildCatalogRegistration(replacement);
    if (!m_contractCatalog.Replace(currentToken, registration, reason))
        return false;

    m_sessions.erase(current);
    m_sessions[replacementToken] = replacement;
    MoveSessionRateBudgetsLocked(currentToken, replacementToken);
    MoveActiveDecisionLeasesLocked(currentToken, replacementToken);
    newGeneration = replacement.leaseGeneration;
    reason.clear();
    return true;
}

bool TradingToolHost::RevokeSessionUnderDispatchLock(
    const std::string& token,
    std::uint64_t expectedGeneration,
    const std::string* expectedAgentId,
    const std::string* expectedSessionId,
    const std::string* watchTransactionId,
    const std::string& revokeReason,
    std::string& reason)
{
    TradingToolHostSessionBinding revoked;
    TradingToolSessionRevokedObserver observer;
    std::string ownerKey;
    {
        std::lock_guard<std::mutex> lock(m_mutex);
        const std::unordered_map<std::string,
            TradingToolHostSessionBinding>::iterator session =
            m_sessions.find(token);
        if (session == m_sessions.end())
        {
            reason = "SESSION_NOT_FOUND";
            return false;
        }
        const std::string sessionOwnerKey =
            SessionOwnerKey(session->second);
        if (WatchTransactionPendingLocked(session->second) &&
            !WatchTransactionAllowsLocked(
                watchTransactionId, sessionOwnerKey, token))
        {
            reason = "SESSION_OWNER_FENCE_PENDING";
            return false;
        }
        if (expectedAgentId != nullptr &&
            (session->second.session.executionContext.agentId !=
                 *expectedAgentId ||
             session->second.session.executionContext.sessionId !=
                 *expectedSessionId))
        {
            reason = "SESSION_OWNER_IDENTITY_MISMATCH";
            return false;
        }
        if (expectedAgentId == nullptr &&
            (expectedGeneration == 0 ||
             session->second.leaseGeneration != expectedGeneration))
        {
            reason = "SESSION_LEASE_GENERATION_MISMATCH";
            return false;
        }
        session->second.enabled = false;
        revoked = session->second;
        ownerKey = SessionOwnerKey(revoked);
        m_pendingOwnerFences.insert(ownerKey);
        observer = m_sessionRevokedObserver;
    }

    std::string fenceReason;
    if (observer && !observer(revoked, revokeReason, fenceReason))
    {
        reason = fenceReason.empty() ?
            "SESSION_REMOTE_FENCE_PENDING" : fenceReason;
        return false;
    }
    {
        std::lock_guard<std::mutex> lock(m_mutex);
        EraseSessionLocked(token);
        m_pendingOwnerFences.erase(ownerKey);
    }
    m_contractCatalog.Revoke(token);
    reason.clear();
    return true;
}

bool TradingToolHost::RevokeSessionWithReason(
    const std::string& token,
    const std::string& revokeReason,
    std::string& reason)
{
    std::uint64_t generation = 0;
    {
        std::lock_guard<std::mutex> lock(m_mutex);
        const std::unordered_map<std::string,
            TradingToolHostSessionBinding>::const_iterator session =
            m_sessions.find(token);
        if (session == m_sessions.end())
        {
            reason = "SESSION_NOT_FOUND";
            return false;
        }
        generation = session->second.leaseGeneration;
    }
    return RevokeSession(token, generation, revokeReason, reason);
}

bool TradingToolHost::FenceRestoredSession(
    const TradingToolHostSessionBinding& binding,
    const std::string& revokeReason,
    std::string& reason)
{
    std::lock_guard<std::mutex> dispatchLock(m_mutationDispatchMutex);
    if (binding.session.executionContext.agentId.empty() ||
        binding.session.executionContext.sessionId.empty())
    {
        reason = "INVALID_SESSION_IDENTITY";
        return false;
    }
    TradingToolHostSessionBinding pending = binding;
    pending.enabled = false;
    const std::string ownerKey = SessionOwnerKey(pending);
    TradingToolSessionRevokedObserver observer;
    {
        std::lock_guard<std::mutex> lock(m_mutex);
        m_pendingOwnerFences.insert(ownerKey);
        observer = m_sessionRevokedObserver;
    }
    std::string fenceReason;
    if (observer && !observer(pending, revokeReason, fenceReason))
    {
        reason = fenceReason.empty() ?
            "SESSION_REMOTE_FENCE_PENDING" : fenceReason;
        return false;
    }
    {
        std::lock_guard<std::mutex> lock(m_mutex);
        m_pendingOwnerFences.erase(ownerKey);
    }
    reason.clear();
    return true;
}

std::size_t TradingToolHost::ReapExpiredSessions(std::uint64_t nowMs)
{
    std::vector<std::string> expired;
    {
        std::lock_guard<std::mutex> lock(m_mutex);
        for (std::unordered_map<std::string,
                 TradingToolHostSessionBinding>::const_iterator session =
                 m_sessions.begin();
             session != m_sessions.end(); ++session)
            if (session->second.expiresAtMs <= nowMs)
                expired.push_back(session->first);
    }
    std::size_t reaped = 0;
    for (std::size_t i = 0; i < expired.size(); ++i)
    {
        std::string reason;
        if (RevokeSessionWithReason(
                expired[i], "session_expired", reason))
            ++reaped;
    }
    return reaped;
}
