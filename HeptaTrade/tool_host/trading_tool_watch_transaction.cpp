#include "trading_tool_host.h"

bool TradingToolHost::WatchTransactionAllowsLocked(
    const std::string* watchTransactionId,
    const std::string& ownerKey,
    const std::string& token) const
{
    if (watchTransactionId == nullptr || watchTransactionId->empty())
        return false;
    const std::unordered_map<std::string,
        WatchTransactionReservation>::const_iterator transaction =
        m_watchTransactions.find(*watchTransactionId);
    if (transaction == m_watchTransactions.end() ||
        transaction->second.ownerKey != ownerKey ||
        transaction->second.tokens.find(token) ==
            transaction->second.tokens.end())
        return false;
    const std::unordered_map<std::string, std::string>::const_iterator owner =
        m_watchOwnerTransactions.find(ownerKey);
    const std::unordered_map<std::string, std::string>::const_iterator
        reservedToken = m_watchTokenTransactions.find(token);
    return owner != m_watchOwnerTransactions.end() &&
        owner->second == *watchTransactionId &&
        reservedToken != m_watchTokenTransactions.end() &&
        reservedToken->second == *watchTransactionId;
}

bool TradingToolHost::WatchTransactionPendingLocked(
    const TradingToolHostSessionBinding& binding) const
{
    return m_watchOwnerTransactions.find(SessionOwnerKey(binding)) !=
            m_watchOwnerTransactions.end() ||
        m_watchTokenTransactions.find(binding.token) !=
            m_watchTokenTransactions.end();
}

bool TradingToolHost::WatchBindingScopeEqual(
    const TradingToolHostSessionBinding& left,
    const TradingToolHostSessionBinding& right)
{
    return left.token == right.token &&
        left.peerUid == right.peerUid &&
        left.leaseGeneration == right.leaseGeneration &&
        left.session.environment == right.session.environment &&
        left.session.executionContext.agentId ==
            right.session.executionContext.agentId &&
        left.session.executionContext.sessionId ==
            right.session.executionContext.sessionId;
}

bool TradingToolHost::ValidateWatchTransactionBindings(
    const std::vector<TradingToolHostSessionBinding>& expectedBindings,
    std::string& ownerKey,
    std::unordered_set<std::string>& tokens,
    std::string& reason) const
{
    ownerKey.clear();
    tokens.clear();
    if (expectedBindings.empty() || expectedBindings.size() > 2)
    {
        reason = "WATCH_TRANSACTION_BINDINGS_INVALID";
        return false;
    }
    ownerKey = SessionOwnerKey(expectedBindings[0]);
    for (std::size_t i = 0; i < expectedBindings.size(); ++i)
    {
        const TradingToolHostSessionBinding& binding = expectedBindings[i];
        if (binding.token.size() < 24 ||
            binding.leaseGeneration == 0 ||
            binding.session.executionContext.agentId.empty() ||
            binding.session.executionContext.sessionId.empty() ||
            binding.session.environment != "WATCH" ||
            SessionOwnerKey(binding) != ownerKey)
        {
            reason = "WATCH_TRANSACTION_BINDINGS_INVALID";
            return false;
        }
        tokens.insert(binding.token);
    }
    return true;
}

bool TradingToolHost::ValidateWatchReservationScopeLocked(
    const WatchTransactionReservation& reservation,
    const std::vector<TradingToolHostSessionBinding>& expectedBindings,
    std::unordered_set<std::string>& expectedTokens,
    std::string& reason) const
{
    std::string ownerKey;
    if (!ValidateWatchTransactionBindings(
            expectedBindings, ownerKey, expectedTokens, reason))
        return false;
    if (ownerKey != reservation.ownerKey ||
        expectedTokens != reservation.tokens ||
        expectedBindings.size() != reservation.expectedBindings.size())
    {
        reason = "WATCH_TRANSACTION_EXPECTATION_INCOMPLETE";
        return false;
    }
    std::vector<bool> matched(reservation.expectedBindings.size(), false);
    for (std::size_t i = 0; i < expectedBindings.size(); ++i)
    {
        bool exactScope = false;
        for (std::size_t j = 0; j < reservation.expectedBindings.size(); ++j)
        {
            if (!matched[j] && WatchBindingScopeEqual(
                    expectedBindings[i],
                    reservation.expectedBindings[j]))
            {
                matched[j] = true;
                exactScope = true;
                break;
            }
        }
        if (!exactScope)
        {
            reason = "WATCH_TRANSACTION_EXPECTATION_INCOMPLETE";
            return false;
        }
    }
    return true;
}

bool TradingToolHost::BeginWatchTransaction(
    const std::vector<TradingToolHostSessionBinding>& expectedBindings,
    std::string& watchTransactionId,
    std::string& reason)
{
    watchTransactionId.clear();
    std::string ownerKey;
    std::unordered_set<std::string> tokens;
    if (!ValidateWatchTransactionBindings(
            expectedBindings, ownerKey, tokens, reason))
        return false;

    std::lock_guard<std::mutex> dispatchLock(m_mutationDispatchMutex);
    std::lock_guard<std::mutex> lock(m_mutex);
    std::string existingTransaction;
    const std::unordered_map<std::string, std::string>::const_iterator
        existingOwner = m_watchOwnerTransactions.find(ownerKey);
    if (existingOwner != m_watchOwnerTransactions.end())
        existingTransaction = existingOwner->second;
    for (std::unordered_set<std::string>::const_iterator token =
             tokens.begin(); token != tokens.end(); ++token)
    {
        const std::unordered_map<std::string, std::string>::const_iterator
            existingToken = m_watchTokenTransactions.find(*token);
        if (existingToken == m_watchTokenTransactions.end()) continue;
        if (!existingTransaction.empty() &&
            existingTransaction != existingToken->second)
        {
            reason = "WATCH_TRANSACTION_RESERVATION_CONFLICT";
            return false;
        }
        existingTransaction = existingToken->second;
    }
    if (!existingTransaction.empty())
    {
        const std::unordered_map<std::string,
            WatchTransactionReservation>::const_iterator existing =
            m_watchTransactions.find(existingTransaction);
        std::unordered_set<std::string> expectedTokens;
        if (existing == m_watchTransactions.end() ||
            !ValidateWatchReservationScopeLocked(
                existing->second, expectedBindings,
                expectedTokens, reason))
        {
            reason = "WATCH_TRANSACTION_RESERVATION_CONFLICT";
            return false;
        }
        watchTransactionId = existingTransaction;
        reason.clear();
        return true;
    }
    if (m_nextWatchTransactionId == 0)
    {
        reason = "WATCH_TRANSACTION_ID_EXHAUSTED";
        return false;
    }
    watchTransactionId =
        "watch-transaction-" + std::to_string(m_nextWatchTransactionId++);
    WatchTransactionReservation reservation;
    reservation.ownerKey = ownerKey;
    reservation.tokens = tokens;
    reservation.expectedBindings = expectedBindings;
    m_watchTransactions[watchTransactionId] = reservation;
    m_watchOwnerTransactions[ownerKey] = watchTransactionId;
    for (std::unordered_set<std::string>::const_iterator token =
             tokens.begin(); token != tokens.end(); ++token)
        m_watchTokenTransactions[*token] = watchTransactionId;
    reason.clear();
    return true;
}

bool TradingToolHost::RegisterSessionForWatchTransaction(
    const std::string& watchTransactionId,
    const TradingToolHostSessionBinding& binding,
    std::string& reason)
{
    return RegisterSessionImpl(binding, &watchTransactionId, reason);
}

bool TradingToolHost::UpdateSessionLeaseForWatchTransaction(
    const std::string& watchTransactionId,
    const TradingToolHostSessionBinding& expectedCurrent,
    const std::string& currentToken,
    const std::string& replacementToken,
    std::uint64_t expectedGeneration,
    std::uint64_t expiresAtMs,
    std::uint64_t& newGeneration,
    std::string& reason)
{
    return UpdateSessionLeaseImpl(
        currentToken, replacementToken, expectedGeneration,
        expiresAtMs, newGeneration, &watchTransactionId,
        &expectedCurrent, reason);
}

bool TradingToolHost::CollectWatchRevokeTargetsLocked(
    const WatchTransactionReservation& reservation,
    const std::vector<TradingToolHostSessionBinding>& expectedBindings,
    const std::unordered_set<std::string>& expectedTokens,
    std::vector<std::pair<std::string, std::uint64_t> >& revokeTargets,
    std::string& reason)
{
    for (std::unordered_map<std::string,
             TradingToolHostSessionBinding>::iterator session =
             m_sessions.begin();
         session != m_sessions.end(); ++session)
    {
        if (SessionOwnerKey(session->second) == reservation.ownerKey &&
            expectedTokens.find(session->second.token) ==
                expectedTokens.end())
        {
            session->second.enabled = false;
            m_pendingOwnerFences.insert(SessionOwnerKey(session->second));
            reason = "SESSION_OWNER_IDENTITY_MISMATCH";
            return false;
        }
    }
    for (std::unordered_set<std::string>::const_iterator token =
             expectedTokens.begin();
         token != expectedTokens.end(); ++token)
    {
        const std::unordered_map<std::string,
            TradingToolHostSessionBinding>::iterator session =
            m_sessions.find(*token);
        if (session == m_sessions.end()) continue;
        bool exact = false;
        bool ownerMatch = false;
        bool peerMatch = false;
        bool environmentMatch = false;
        for (std::size_t i = 0; i < expectedBindings.size(); ++i)
        {
            if (expectedBindings[i].token != *token) continue;
            const bool thisOwner =
                session->second.session.executionContext.agentId ==
                    expectedBindings[i].session.executionContext.agentId &&
                session->second.session.executionContext.sessionId ==
                    expectedBindings[i].session.executionContext.sessionId;
            const bool thisPeer =
                session->second.peerUid == expectedBindings[i].peerUid;
            const bool thisEnvironment =
                session->second.session.environment ==
                    expectedBindings[i].session.environment;
            ownerMatch = ownerMatch || thisOwner;
            peerMatch = peerMatch || (thisOwner && thisPeer);
            environmentMatch = environmentMatch ||
                (thisOwner && thisPeer && thisEnvironment);
            if (thisOwner && thisPeer && thisEnvironment &&
                session->second.leaseGeneration ==
                    expectedBindings[i].leaseGeneration)
            {
                exact = true;
                break;
            }
        }
        if (!exact)
        {
            session->second.enabled = false;
            m_pendingOwnerFences.insert(SessionOwnerKey(session->second));
            reason = !ownerMatch ? "SESSION_OWNER_IDENTITY_MISMATCH" :
                (!peerMatch ? "SESSION_PEER_UID_MISMATCH" :
                    (!environmentMatch ?
                        "SESSION_ENVIRONMENT_MISMATCH" :
                        "SESSION_LEASE_GENERATION_MISMATCH"));
            return false;
        }
        revokeTargets.push_back(std::make_pair(
            session->second.token, session->second.leaseGeneration));
    }
    return true;
}

bool TradingToolHost::RevokeExactWatchTransaction(
    const std::string& watchTransactionId,
    const std::vector<TradingToolHostSessionBinding>& expectedBindings,
    const std::string& revokeReason,
    bool& allLocalAbsent,
    std::string& reason)
{
    allLocalAbsent = false;
    std::string ownerKey;
    std::unordered_set<std::string> expectedTokens;
    if (watchTransactionId.empty() ||
        !ValidateWatchTransactionBindings(
            expectedBindings, ownerKey, expectedTokens, reason))
        return false;

    std::lock_guard<std::mutex> dispatchLock(m_mutationDispatchMutex);
    std::vector<std::pair<std::string, std::uint64_t> > revokeTargets;
    {
        std::lock_guard<std::mutex> lock(m_mutex);
        const std::unordered_map<std::string,
            WatchTransactionReservation>::const_iterator transaction =
            m_watchTransactions.find(watchTransactionId);
        if (transaction == m_watchTransactions.end())
        {
            reason = "WATCH_TRANSACTION_RESERVATION_NOT_FOUND";
            return false;
        }
        if (!ValidateWatchReservationScopeLocked(
                transaction->second, expectedBindings,
                expectedTokens, reason))
            return false;
        if (!CollectWatchRevokeTargetsLocked(
                transaction->second, expectedBindings,
                expectedTokens, revokeTargets, reason))
            return false;
    }
    if (revokeTargets.empty())
    {
        allLocalAbsent = true;
        reason = "SESSION_NOT_FOUND";
        return false;
    }
    for (std::size_t i = 0; i < revokeTargets.size(); ++i)
    {
        if (!RevokeSessionUnderDispatchLock(
                revokeTargets[i].first, revokeTargets[i].second,
                nullptr, nullptr, &watchTransactionId,
                revokeReason, reason))
            return false;
    }
    reason.clear();
    return true;
}

bool TradingToolHost::ReleaseWatchTransaction(
    const std::string& watchTransactionId,
    const std::vector<TradingToolHostSessionBinding>& expectedBindings,
    std::string& reason)
{
    std::string ownerKey;
    std::unordered_set<std::string> expectedTokens;
    if (watchTransactionId.empty() ||
        !ValidateWatchTransactionBindings(
            expectedBindings, ownerKey, expectedTokens, reason))
    {
        reason = "WATCH_TRANSACTION_RESERVATION_INVALID";
        return false;
    }
    std::lock_guard<std::mutex> dispatchLock(m_mutationDispatchMutex);
    std::lock_guard<std::mutex> lock(m_mutex);
    const std::unordered_map<std::string,
        WatchTransactionReservation>::iterator transaction =
        m_watchTransactions.find(watchTransactionId);
    if (transaction == m_watchTransactions.end() ||
        !ValidateWatchReservationScopeLocked(
            transaction->second, expectedBindings,
            expectedTokens, reason))
    {
        reason = transaction == m_watchTransactions.end() ?
            "WATCH_TRANSACTION_RESERVATION_NOT_FOUND" :
            "WATCH_TRANSACTION_RESERVATION_MISMATCH";
        return false;
    }
    const std::unordered_map<std::string, std::string>::iterator owner =
        m_watchOwnerTransactions.find(transaction->second.ownerKey);
    if (owner != m_watchOwnerTransactions.end() &&
        owner->second == watchTransactionId)
        m_watchOwnerTransactions.erase(owner);
    for (std::unordered_set<std::string>::const_iterator token =
             transaction->second.tokens.begin();
         token != transaction->second.tokens.end(); ++token)
    {
        const std::unordered_map<std::string, std::string>::iterator
            reserved = m_watchTokenTransactions.find(*token);
        if (reserved != m_watchTokenTransactions.end() &&
            reserved->second == watchTransactionId)
            m_watchTokenTransactions.erase(reserved);
    }
    m_watchTransactions.erase(transaction);
    reason.clear();
    return true;
}
