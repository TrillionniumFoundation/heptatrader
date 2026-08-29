#include "ib_authoritative_recovery_coordinator.h"

#include <algorithm>
#include <limits>

const char* IBAuthoritativeRecoveryDomainName(IBAuthoritativeRecoveryDomain domain)
{
    switch (domain)
    {
    case IBAuthoritativeRecoveryDomain::AccountSummary: return "account";
    case IBAuthoritativeRecoveryDomain::Positions: return "positions";
    case IBAuthoritativeRecoveryDomain::OpenOrders: return "open_orders";
    case IBAuthoritativeRecoveryDomain::Quotes: return "quotes";
    case IBAuthoritativeRecoveryDomain::Count: break;
    }
    return "unknown";
}

IBAuthoritativeRecoveryCoordinator::IBAuthoritativeRecoveryCoordinator(
    const IBAuthoritativeRecoveryPolicy& policy,
    const IBAuthoritativeRecoveryCallbacks& callbacks)
    : m_policy(policy),
      m_callbacks(callbacks)
{
    if (m_policy.maxAttempts == 0) m_policy.maxAttempts = 1;
    if (m_policy.maxBackoffMs < m_policy.initialBackoffMs)
        m_policy.maxBackoffMs = m_policy.initialBackoffMs;
}

IBAuthoritativeRecoveryStartResult
IBAuthoritativeRecoveryCoordinator::StartFullRecovery(
    std::uint64_t connectionEpoch,
    std::uint64_t observedAtMs,
    const std::string& reason)
{
    IBAuthoritativeRecoveryStartResult result;
    if (connectionEpoch == 0 || observedAtMs == 0 || reason.empty() ||
        m_recoveryGeneration == std::numeric_limits<std::uint64_t>::max())
        return result;

    if (m_connectionEpoch != 0 && m_connectionEpoch != connectionEpoch)
        AbortAll("connection_epoch_changed");

    ++m_recoveryGeneration;
    m_connectionEpoch = connectionEpoch;
    m_pending = true;
    m_reason = reason;
    for (std::size_t i = 0;
         i < static_cast<std::size_t>(IBAuthoritativeRecoveryDomain::Count); ++i)
    {
        DomainState& state = m_domains[i];
        state.required = true;
        state.complete = false;
        state.retryScheduled = false;
        state.exhausted = false;
        state.nextRetryAtMs = 0;
        state.consecutiveFailures = 0;
        state.lastFailure.clear();
    }

    RequestSnapshotInternal(SnapshotRefreshKind::AccountSummary, observedAtMs, true, reason);
    RequestSnapshotInternal(SnapshotRefreshKind::Positions, observedAtMs, true, reason);
    RequestSnapshotInternal(SnapshotRefreshKind::OpenOrders, observedAtMs, true, reason);

    DomainState& quotes = m_domains[Index(IBAuthoritativeRecoveryDomain::Quotes)];
    if (quotes.inFlight)
    {
        if (m_callbacks.abortQuotes) m_callbacks.abortQuotes(quotes.activeGeneration);
        quotes.inFlight = false;
        quotes.activeGeneration = 0;
        quotes.deadlineAtMs = 0;
    }
    DispatchQuoteGeneration(observedAtMs, reason);

    result.accepted = true;
    result.recoveryGeneration = m_recoveryGeneration;
    result.exhausted = AnyExhausted();
    result.allDispatched = true;
    for (std::size_t i = 0;
         i < static_cast<std::size_t>(IBAuthoritativeRecoveryDomain::Count); ++i)
        result.allDispatched = result.allDispatched && m_domains[i].inFlight;
    return result;
}

SnapshotRefreshRequestResult IBAuthoritativeRecoveryCoordinator::RequestSnapshot(
    SnapshotRefreshKind kind,
    std::uint64_t observedAtMs,
    const std::string& reason)
{
    if (!ValidSnapshotKind(kind) || observedAtMs == 0 || reason.empty())
        return SnapshotRefreshRequestResult();
    const IBAuthoritativeRecoveryDomain domain = DomainForSnapshot(kind);
    const DomainState& state = m_domains[Index(domain)];
    return RequestSnapshotInternal(kind, observedAtMs,
        m_pending && state.required && !state.complete, reason);
}

IBAuthoritativeRecoveryCompletionResult
IBAuthoritativeRecoveryCoordinator::CompleteSnapshot(
    SnapshotRefreshKind kind,
    std::uint64_t generation,
    bool snapshotAccepted,
    std::uint64_t observedAtMs,
    const std::string& failureReason)
{
    IBAuthoritativeRecoveryCompletionResult result;
    if (!ValidSnapshotKind(kind) || generation == 0 || observedAtMs == 0) return result;
    const IBAuthoritativeRecoveryDomain domain = DomainForSnapshot(kind);
    result.domain = domain;
    result.generation = generation;
    DomainState& state = m_domains[Index(domain)];
    if (!state.inFlight || state.activeGeneration != generation) return result;

    const SnapshotRefreshCompletionResult completion =
        m_snapshotRefreshes.Complete(kind, generation, observedAtMs);
    if (!completion.accepted) return result;
    result.accepted = true;
    state.inFlight = false;
    state.activeGeneration = 0;
    if (snapshotAccepted)
    {
        ClearFailureState(state);
        if (m_pending && state.required && state.requiredGeneration != 0 &&
            generation >= state.requiredGeneration)
            state.complete = true;
    }
    else
    {
        state.complete = false;
    }

    if (completion.dispatchNext)
    {
        if (!snapshotAccepted)
        {
            ScheduleRetry(domain, observedAtMs,
                failureReason.empty() ? "SNAPSHOT_COMPLETION_REJECTED" : failureReason);
            if (state.exhausted)
            {
                m_snapshotRefreshes.Abort(kind, completion.nextGeneration);
                result.exhausted = true;
                return result;
            }
            state.retryScheduled = false;
            state.nextRetryAtMs = 0;
        }
        result.dispatchedNext = DispatchSnapshotGeneration(
            kind, completion.nextGeneration, observedAtMs,
            failureReason.empty() ? "coalesced_follow_up" : failureReason);
    }
    else if (!snapshotAccepted)
    {
        ScheduleRetry(domain, observedAtMs,
            failureReason.empty() ? "SNAPSHOT_COMPLETION_REJECTED" : failureReason);
    }
    result.retryScheduled = state.retryScheduled;
    result.exhausted = state.exhausted;
    return result;
}

IBAuthoritativeRecoveryCompletionResult
IBAuthoritativeRecoveryCoordinator::CompleteQuotes(
    std::uint64_t generation,
    bool quotesAccepted,
    std::uint64_t observedAtMs,
    const std::string& failureReason)
{
    IBAuthoritativeRecoveryCompletionResult result;
    result.domain = IBAuthoritativeRecoveryDomain::Quotes;
    result.generation = generation;
    if (generation == 0 || observedAtMs == 0) return result;
    DomainState& state = m_domains[Index(IBAuthoritativeRecoveryDomain::Quotes)];
    if (!state.inFlight || state.activeGeneration != generation) return result;

    result.accepted = true;
    state.inFlight = false;
    state.deadlineAtMs = 0;
    if (quotesAccepted)
    {
        ClearFailureState(state);
        if (m_pending && state.required && state.requiredGeneration != 0 &&
            generation >= state.requiredGeneration)
            state.complete = true;
    }
    else
    {
        state.complete = false;
        ScheduleRetry(IBAuthoritativeRecoveryDomain::Quotes, observedAtMs,
            failureReason.empty() ? "QUOTE_COMPLETION_REJECTED" : failureReason);
    }
    result.retryScheduled = state.retryScheduled;
    result.exhausted = state.exhausted;
    return result;
}

IBAuthoritativeRecoveryPollResult IBAuthoritativeRecoveryCoordinator::Poll(
    std::uint64_t observedAtMs)
{
    IBAuthoritativeRecoveryPollResult result;
    if (observedAtMs == 0) return result;

    const SnapshotRefreshKind kinds[] = {
        SnapshotRefreshKind::AccountSummary,
        SnapshotRefreshKind::Positions,
        SnapshotRefreshKind::OpenOrders
    };
    for (std::size_t i = 0; i < sizeof(kinds) / sizeof(kinds[0]); ++i)
    {
        const SnapshotRefreshExpirationResult expired =
            m_snapshotRefreshes.Expire(kinds[i], observedAtMs);
        if (!expired.expired) continue;
        const IBAuthoritativeRecoveryDomain domain = DomainForSnapshot(kinds[i]);
        DomainState& state = m_domains[Index(domain)];
        if (m_callbacks.abortSnapshot) m_callbacks.abortSnapshot(kinds[i], expired.generation);
        state.inFlight = false;
        state.activeGeneration = 0;
        state.complete = false;
        state.lastFailure = "SNAPSHOT_REFRESH_TIMEOUT";
        result.unsafeSnapshotTimeout = true;
        result.affectedDomains.push_back(domain);
    }
    if (result.unsafeSnapshotTimeout) return result;

    DomainState& quotes = m_domains[Index(IBAuthoritativeRecoveryDomain::Quotes)];
    if (quotes.inFlight && quotes.deadlineAtMs != 0 && observedAtMs >= quotes.deadlineAtMs)
    {
        if (m_callbacks.abortQuotes) m_callbacks.abortQuotes(quotes.activeGeneration);
        quotes.inFlight = false;
        quotes.activeGeneration = 0;
        quotes.deadlineAtMs = 0;
        quotes.complete = false;
        ScheduleRetry(IBAuthoritativeRecoveryDomain::Quotes, observedAtMs,
            "QUOTE_RECOVERY_TIMEOUT");
        result.quoteExpired = true;
        result.affectedDomains.push_back(IBAuthoritativeRecoveryDomain::Quotes);
    }

    for (std::size_t i = 0;
         i < static_cast<std::size_t>(IBAuthoritativeRecoveryDomain::Count); ++i)
    {
        DomainState& state = m_domains[i];
        if (!state.retryScheduled || state.exhausted || state.inFlight ||
            observedAtMs < state.nextRetryAtMs) continue;
        state.retryScheduled = false;
        state.nextRetryAtMs = 0;
        const IBAuthoritativeRecoveryDomain domain =
            static_cast<IBAuthoritativeRecoveryDomain>(i);
        result.retryAttempted = true;
        result.affectedDomains.push_back(domain);
        if (domain == IBAuthoritativeRecoveryDomain::Quotes)
            DispatchQuoteGeneration(observedAtMs, "scheduled_retry");
        else
        {
            const SnapshotRefreshKind kind = domain == IBAuthoritativeRecoveryDomain::AccountSummary
                ? SnapshotRefreshKind::AccountSummary
                : (domain == IBAuthoritativeRecoveryDomain::Positions
                    ? SnapshotRefreshKind::Positions
                    : SnapshotRefreshKind::OpenOrders);
            RequestSnapshotInternal(kind, observedAtMs,
                m_pending && state.required && !state.complete, "scheduled_retry");
        }
    }
    result.exhausted = AnyExhausted();
    return result;
}

void IBAuthoritativeRecoveryCoordinator::AbortAll(const std::string& reason)
{
    const SnapshotRefreshKind kinds[] = {
        SnapshotRefreshKind::AccountSummary,
        SnapshotRefreshKind::Positions,
        SnapshotRefreshKind::OpenOrders
    };
    for (std::size_t i = 0; i < sizeof(kinds) / sizeof(kinds[0]); ++i)
    {
        DomainState& state = m_domains[Index(DomainForSnapshot(kinds[i]))];
        AbortSnapshotDomain(kinds[i], state);
    }
    DomainState& quotes = m_domains[Index(IBAuthoritativeRecoveryDomain::Quotes)];
    if (quotes.activeGeneration != 0 && m_callbacks.abortQuotes)
        m_callbacks.abortQuotes(quotes.activeGeneration);
    quotes = DomainState();
    m_pending = false;
    m_connectionEpoch = 0;
    m_reason = reason;
}

bool IBAuthoritativeRecoveryCoordinator::ReadyToRestore() const
{
    if (!m_pending || AnyExhausted()) return false;
    for (std::size_t i = 0;
         i < static_cast<std::size_t>(IBAuthoritativeRecoveryDomain::Count); ++i)
    {
        if (!m_domains[i].required || !m_domains[i].complete) return false;
    }
    return true;
}

bool IBAuthoritativeRecoveryCoordinator::MarkRestored()
{
    if (!ReadyToRestore()) return false;
    m_pending = false;
    return true;
}

bool IBAuthoritativeRecoveryCoordinator::IsSnapshotInFlight(SnapshotRefreshKind kind) const
{
    if (!ValidSnapshotKind(kind)) return false;
    return m_domains[Index(DomainForSnapshot(kind))].inFlight;
}

std::uint64_t IBAuthoritativeRecoveryCoordinator::CurrentSnapshotGeneration(
    SnapshotRefreshKind kind) const
{
    if (!ValidSnapshotKind(kind)) return 0;
    return m_domains[Index(DomainForSnapshot(kind))].activeGeneration;
}

IBAuthoritativeRecoverySnapshot IBAuthoritativeRecoveryCoordinator::GetSnapshot() const
{
    IBAuthoritativeRecoverySnapshot snapshot;
    snapshot.pending = m_pending;
    snapshot.connectionEpoch = m_connectionEpoch;
    snapshot.recoveryGeneration = m_recoveryGeneration;
    snapshot.reason = m_reason;
    for (std::size_t i = 0;
         i < static_cast<std::size_t>(IBAuthoritativeRecoveryDomain::Count); ++i)
    {
        const DomainState& state = m_domains[i];
        IBAuthoritativeRecoveryDomainSnapshot& target = snapshot.domains[i];
        target.required = state.required;
        target.complete = state.complete;
        target.inFlight = state.inFlight;
        target.retryScheduled = state.retryScheduled;
        target.exhausted = state.exhausted;
        target.activeGeneration = state.activeGeneration;
        target.requiredGeneration = state.requiredGeneration;
        target.nextRetryAtMs = state.nextRetryAtMs;
        target.totalDispatchAttempts = state.totalDispatchAttempts;
        target.consecutiveFailures = state.consecutiveFailures;
        target.lastFailure = state.lastFailure;
    }
    return snapshot;
}

bool IBAuthoritativeRecoveryCoordinator::ValidSnapshotKind(SnapshotRefreshKind kind)
{
    return kind == SnapshotRefreshKind::AccountSummary ||
        kind == SnapshotRefreshKind::Positions ||
        kind == SnapshotRefreshKind::OpenOrders;
}

IBAuthoritativeRecoveryDomain IBAuthoritativeRecoveryCoordinator::DomainForSnapshot(
    SnapshotRefreshKind kind)
{
    if (kind == SnapshotRefreshKind::Positions) return IBAuthoritativeRecoveryDomain::Positions;
    if (kind == SnapshotRefreshKind::OpenOrders) return IBAuthoritativeRecoveryDomain::OpenOrders;
    return IBAuthoritativeRecoveryDomain::AccountSummary;
}

std::size_t IBAuthoritativeRecoveryCoordinator::Index(
    IBAuthoritativeRecoveryDomain domain)
{
    return static_cast<std::size_t>(domain);
}

std::uint64_t IBAuthoritativeRecoveryCoordinator::Deadline(
    std::uint64_t observedAtMs,
    std::uint64_t timeoutMs)
{
    if (observedAtMs == 0 || timeoutMs == 0) return 0;
    if (observedAtMs > std::numeric_limits<std::uint64_t>::max() - timeoutMs)
        return std::numeric_limits<std::uint64_t>::max();
    return observedAtMs + timeoutMs;
}

std::uint64_t IBAuthoritativeRecoveryCoordinator::BackoffMs(
    std::uint32_t failureCount) const
{
    if (m_policy.initialBackoffMs == 0 || failureCount == 0) return 0;
    std::uint64_t backoff = m_policy.initialBackoffMs;
    for (std::uint32_t i = 1; i < failureCount && backoff < m_policy.maxBackoffMs; ++i)
    {
        if (backoff > std::numeric_limits<std::uint64_t>::max() / 2)
        {
            backoff = m_policy.maxBackoffMs;
            break;
        }
        backoff = std::min(m_policy.maxBackoffMs, backoff * 2);
    }
    return std::min(backoff, m_policy.maxBackoffMs);
}

SnapshotRefreshRequestResult
IBAuthoritativeRecoveryCoordinator::RequestSnapshotInternal(
    SnapshotRefreshKind kind,
    std::uint64_t observedAtMs,
    bool requiredForRecovery,
    const std::string& reason)
{
    SnapshotRefreshRequestResult request = m_snapshotRefreshes.Request(
        kind, observedAtMs, m_policy.snapshotTimeoutMs);
    DomainState& state = m_domains[Index(DomainForSnapshot(kind))];
    if (request.coalesced)
    {
        if (requiredForRecovery)
        {
            state.required = true;
            state.complete = false;
            if (request.generation == std::numeric_limits<std::uint64_t>::max())
                ScheduleRetry(DomainForSnapshot(kind), observedAtMs,
                    "SNAPSHOT_GENERATION_EXHAUSTED");
            else
                state.requiredGeneration = request.generation + 1;
        }
        return request;
    }
    if (!request.dispatch)
    {
        ScheduleRetry(DomainForSnapshot(kind), observedAtMs,
            reason.empty() ? "SNAPSHOT_REQUEST_REJECTED" :
                (std::string("SNAPSHOT_REQUEST_REJECTED:") + reason));
        return request;
    }
    if (requiredForRecovery)
    {
        state.required = true;
        state.complete = false;
        state.requiredGeneration = request.generation;
    }
    DispatchSnapshotGeneration(kind, request.generation, observedAtMs, reason);
    return request;
}

bool IBAuthoritativeRecoveryCoordinator::DispatchSnapshotGeneration(
    SnapshotRefreshKind kind,
    std::uint64_t generation,
    std::uint64_t observedAtMs,
    const std::string& reason)
{
    const IBAuthoritativeRecoveryDomain domain = DomainForSnapshot(kind);
    DomainState& state = m_domains[Index(domain)];
    ++state.totalDispatchAttempts;
    bool dispatched = false;
    try
    {
        if (m_callbacks.beginSnapshot) m_callbacks.beginSnapshot(kind, generation);
        dispatched = m_callbacks.dispatchSnapshot && m_callbacks.dispatchSnapshot(kind);
    }
    catch (...)
    {
        dispatched = false;
    }
    if (!dispatched)
    {
        m_snapshotRefreshes.Abort(kind, generation);
        if (m_callbacks.abortSnapshot) m_callbacks.abortSnapshot(kind, generation);
        state.inFlight = false;
        state.activeGeneration = 0;
        ScheduleRetry(domain, observedAtMs,
            reason.empty() ? "SNAPSHOT_DISPATCH_FAILED" :
                (std::string("SNAPSHOT_DISPATCH_FAILED:") + reason));
        return false;
    }
    state.inFlight = true;
    state.activeGeneration = generation;
    state.retryScheduled = false;
    state.nextRetryAtMs = 0;
    return true;
}

bool IBAuthoritativeRecoveryCoordinator::DispatchQuoteGeneration(
    std::uint64_t observedAtMs,
    const std::string& reason)
{
    DomainState& state = m_domains[Index(IBAuthoritativeRecoveryDomain::Quotes)];
    if (m_quoteGeneration == std::numeric_limits<std::uint64_t>::max())
    {
        ScheduleRetry(IBAuthoritativeRecoveryDomain::Quotes, observedAtMs,
            "QUOTE_GENERATION_EXHAUSTED");
        return false;
    }
    ++m_quoteGeneration;
    ++state.totalDispatchAttempts;
    const std::uint64_t generation = m_quoteGeneration;
    bool dispatched = false;
    try
    {
        dispatched = m_callbacks.dispatchQuotes &&
            m_callbacks.dispatchQuotes(m_connectionEpoch, generation, observedAtMs);
    }
    catch (...)
    {
        dispatched = false;
    }
    if (!dispatched)
    {
        if (m_callbacks.abortQuotes) m_callbacks.abortQuotes(generation);
        state.inFlight = false;
        state.activeGeneration = 0;
        state.deadlineAtMs = 0;
        ScheduleRetry(IBAuthoritativeRecoveryDomain::Quotes, observedAtMs,
            reason.empty() ? "QUOTE_DISPATCH_FAILED" :
                (std::string("QUOTE_DISPATCH_FAILED:") + reason));
        return false;
    }
    state.required = m_pending;
    state.complete = false;
    state.requiredGeneration = generation;
    state.inFlight = true;
    state.activeGeneration = generation;
    state.deadlineAtMs = Deadline(observedAtMs, m_policy.quoteTimeoutMs);
    state.retryScheduled = false;
    state.nextRetryAtMs = 0;
    return true;
}

void IBAuthoritativeRecoveryCoordinator::ScheduleRetry(
    IBAuthoritativeRecoveryDomain domain,
    std::uint64_t observedAtMs,
    const std::string& reason)
{
    DomainState& state = m_domains[Index(domain)];
    state.complete = false;
    state.lastFailure = reason;
    if (state.consecutiveFailures < std::numeric_limits<std::uint32_t>::max())
        ++state.consecutiveFailures;
    if (state.consecutiveFailures >= m_policy.maxAttempts)
    {
        state.exhausted = true;
        state.retryScheduled = false;
        state.nextRetryAtMs = 0;
        return;
    }
    const std::uint64_t backoff = BackoffMs(state.consecutiveFailures);
    state.retryScheduled = true;
    state.nextRetryAtMs = Deadline(observedAtMs, backoff);
}

void IBAuthoritativeRecoveryCoordinator::ClearFailureState(DomainState& state)
{
    state.retryScheduled = false;
    state.exhausted = false;
    state.nextRetryAtMs = 0;
    state.consecutiveFailures = 0;
    state.lastFailure.clear();
}

bool IBAuthoritativeRecoveryCoordinator::AnyExhausted() const
{
    for (std::size_t i = 0;
         i < static_cast<std::size_t>(IBAuthoritativeRecoveryDomain::Count); ++i)
    {
        if (m_domains[i].exhausted) return true;
    }
    return false;
}

void IBAuthoritativeRecoveryCoordinator::AbortSnapshotDomain(
    SnapshotRefreshKind kind,
    DomainState& state)
{
    if (state.inFlight)
    {
        m_snapshotRefreshes.Abort(kind, state.activeGeneration);
        if (m_callbacks.abortSnapshot)
            m_callbacks.abortSnapshot(kind, state.activeGeneration);
    }
    state = DomainState();
}
