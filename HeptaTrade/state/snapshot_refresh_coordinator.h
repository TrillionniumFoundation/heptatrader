#pragma once

#include <cstddef>
#include <cstdint>
#include <mutex>

enum class SnapshotRefreshKind
{
    AccountSummary = 0,
    Positions,
    OpenOrders,
    Count
};

struct SnapshotRefreshRequestResult
{
    bool dispatch = false;
    bool coalesced = false;
    std::uint64_t generation = 0;
};

struct SnapshotRefreshCompletionResult
{
    bool accepted = false;
    bool dispatchNext = false;
    std::uint64_t completedGeneration = 0;
    std::uint64_t nextGeneration = 0;
};

struct SnapshotRefreshExpirationResult
{
    bool expired = false;
    bool hadPending = false;
    std::uint64_t generation = 0;
};

// Serializes broker snapshot requests whose callbacks do not carry a request
// identifier (notably IB position/open-order refreshes). Repeated requests are
// coalesced into at most one follow-up refresh, so callback generations can
// never overlap in-process.
class SnapshotRefreshCoordinator
{
public:
    SnapshotRefreshCoordinator();

    SnapshotRefreshRequestResult Request(SnapshotRefreshKind kind,
                                         std::uint64_t observedAtMs = 0,
                                         std::uint64_t timeoutMs = 0);
    SnapshotRefreshCompletionResult Complete(SnapshotRefreshKind kind,
                                              std::uint64_t generation,
                                              std::uint64_t observedAtMs = 0);
    SnapshotRefreshExpirationResult Expire(SnapshotRefreshKind kind,
                                           std::uint64_t observedAtMs);
    bool Abort(SnapshotRefreshKind kind, std::uint64_t generation);
    bool IsCurrent(SnapshotRefreshKind kind, std::uint64_t generation) const;
    bool IsInFlight(SnapshotRefreshKind kind) const;

private:
    struct State
    {
        bool inFlight = false;
        bool pending = false;
        std::uint64_t generation = 0;
        std::uint64_t timeoutMs = 0;
        std::uint64_t deadlineAtMs = 0;
    };

    static bool ValidKind(SnapshotRefreshKind kind);
    static std::size_t Index(SnapshotRefreshKind kind);
    static bool AdvanceGeneration(State& state);
    static std::uint64_t Deadline(std::uint64_t observedAtMs, std::uint64_t timeoutMs);

private:
    mutable std::mutex m_mutex;
    State m_states[static_cast<std::size_t>(SnapshotRefreshKind::Count)];
};
