#pragma once

#include "snapshot_refresh_coordinator.h"

#include <cstddef>
#include <cstdint>
#include <functional>
#include <string>
#include <vector>

enum class IBAuthoritativeRecoveryDomain
{
    AccountSummary = 0,
    Positions,
    OpenOrders,
    Quotes,
    Count
};

const char* IBAuthoritativeRecoveryDomainName(IBAuthoritativeRecoveryDomain domain);

struct IBAuthoritativeRecoveryPolicy
{
    std::uint64_t snapshotTimeoutMs = 15000;
    std::uint64_t quoteTimeoutMs = 30000;
    std::uint32_t maxAttempts = 3;
    std::uint64_t initialBackoffMs = 250;
    std::uint64_t maxBackoffMs = 5000;
};

struct IBAuthoritativeRecoveryCallbacks
{
    std::function<void(SnapshotRefreshKind, std::uint64_t)> beginSnapshot;
    std::function<void(SnapshotRefreshKind, std::uint64_t)> abortSnapshot;
    std::function<bool(SnapshotRefreshKind)> dispatchSnapshot;
    std::function<bool(std::uint64_t, std::uint64_t, std::uint64_t)> dispatchQuotes;
    std::function<void(std::uint64_t)> abortQuotes;
};

struct IBAuthoritativeRecoveryDomainSnapshot
{
    bool required = false;
    bool complete = false;
    bool inFlight = false;
    bool retryScheduled = false;
    bool exhausted = false;
    std::uint64_t activeGeneration = 0;
    std::uint64_t requiredGeneration = 0;
    std::uint64_t nextRetryAtMs = 0;
    std::uint64_t totalDispatchAttempts = 0;
    std::uint32_t consecutiveFailures = 0;
    std::string lastFailure;
};

struct IBAuthoritativeRecoverySnapshot
{
    bool pending = false;
    std::uint64_t connectionEpoch = 0;
    std::uint64_t recoveryGeneration = 0;
    std::string reason;
    IBAuthoritativeRecoveryDomainSnapshot domains[
        static_cast<std::size_t>(IBAuthoritativeRecoveryDomain::Count)];
};

struct IBAuthoritativeRecoveryStartResult
{
    bool accepted = false;
    bool allDispatched = false;
    bool exhausted = false;
    std::uint64_t recoveryGeneration = 0;
};

struct IBAuthoritativeRecoveryCompletionResult
{
    bool accepted = false;
    bool dispatchedNext = false;
    bool retryScheduled = false;
    bool exhausted = false;
    IBAuthoritativeRecoveryDomain domain = IBAuthoritativeRecoveryDomain::AccountSummary;
    std::uint64_t generation = 0;
};

struct IBAuthoritativeRecoveryPollResult
{
    bool retryAttempted = false;
    bool quoteExpired = false;
    bool unsafeSnapshotTimeout = false;
    bool exhausted = false;
    std::vector<IBAuthoritativeRecoveryDomain> affectedDomains;
};

class IBAuthoritativeRecoveryCoordinator
{
public:
    IBAuthoritativeRecoveryCoordinator(const IBAuthoritativeRecoveryPolicy& policy,
                                       const IBAuthoritativeRecoveryCallbacks& callbacks);

    IBAuthoritativeRecoveryStartResult StartFullRecovery(std::uint64_t connectionEpoch,
                                                         std::uint64_t observedAtMs,
                                                         const std::string& reason);
    SnapshotRefreshRequestResult RequestSnapshot(SnapshotRefreshKind kind,
                                                 std::uint64_t observedAtMs,
                                                 const std::string& reason);
    IBAuthoritativeRecoveryCompletionResult CompleteSnapshot(
        SnapshotRefreshKind kind,
        std::uint64_t generation,
        bool snapshotAccepted,
        std::uint64_t observedAtMs,
        const std::string& failureReason = "");
    IBAuthoritativeRecoveryCompletionResult CompleteQuotes(
        std::uint64_t generation,
        bool quotesAccepted,
        std::uint64_t observedAtMs,
        const std::string& failureReason = "");
    IBAuthoritativeRecoveryPollResult Poll(std::uint64_t observedAtMs);
    void AbortAll(const std::string& reason);

    bool ReadyToRestore() const;
    bool MarkRestored();
    bool IsSnapshotInFlight(SnapshotRefreshKind kind) const;
    std::uint64_t CurrentSnapshotGeneration(SnapshotRefreshKind kind) const;
    IBAuthoritativeRecoverySnapshot GetSnapshot() const;

private:
    struct DomainState
    {
        bool required = false;
        bool complete = false;
        bool inFlight = false;
        bool retryScheduled = false;
        bool exhausted = false;
        std::uint64_t activeGeneration = 0;
        std::uint64_t requiredGeneration = 0;
        std::uint64_t deadlineAtMs = 0;
        std::uint64_t nextRetryAtMs = 0;
        std::uint64_t totalDispatchAttempts = 0;
        std::uint32_t consecutiveFailures = 0;
        std::string lastFailure;
    };

    static bool ValidSnapshotKind(SnapshotRefreshKind kind);
    static IBAuthoritativeRecoveryDomain DomainForSnapshot(SnapshotRefreshKind kind);
    static std::size_t Index(IBAuthoritativeRecoveryDomain domain);
    static std::uint64_t Deadline(std::uint64_t observedAtMs, std::uint64_t timeoutMs);
    std::uint64_t BackoffMs(std::uint32_t failureCount) const;

    SnapshotRefreshRequestResult RequestSnapshotInternal(SnapshotRefreshKind kind,
                                                         std::uint64_t observedAtMs,
                                                         bool requiredForRecovery,
                                                         const std::string& reason);
    bool DispatchSnapshotGeneration(SnapshotRefreshKind kind,
                                    std::uint64_t generation,
                                    std::uint64_t observedAtMs,
                                    const std::string& reason);
    bool DispatchQuoteGeneration(std::uint64_t observedAtMs,
                                 const std::string& reason);
    void ScheduleRetry(IBAuthoritativeRecoveryDomain domain,
                       std::uint64_t observedAtMs,
                       const std::string& reason);
    void ClearFailureState(DomainState& state);
    bool AnyExhausted() const;
    void AbortSnapshotDomain(SnapshotRefreshKind kind, DomainState& state);

private:
    IBAuthoritativeRecoveryPolicy m_policy;
    IBAuthoritativeRecoveryCallbacks m_callbacks;
    SnapshotRefreshCoordinator m_snapshotRefreshes;
    DomainState m_domains[static_cast<std::size_t>(IBAuthoritativeRecoveryDomain::Count)];
    bool m_pending = false;
    std::uint64_t m_connectionEpoch = 0;
    std::uint64_t m_recoveryGeneration = 0;
    std::uint64_t m_quoteGeneration = 0;
    std::string m_reason;
};
