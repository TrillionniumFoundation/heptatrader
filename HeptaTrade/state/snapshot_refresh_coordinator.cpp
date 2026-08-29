#include "snapshot_refresh_coordinator.h"

#include <limits>

SnapshotRefreshCoordinator::SnapshotRefreshCoordinator()
{
}

bool SnapshotRefreshCoordinator::ValidKind(SnapshotRefreshKind kind)
{
    return static_cast<std::size_t>(kind) <
           static_cast<std::size_t>(SnapshotRefreshKind::Count);
}

std::size_t SnapshotRefreshCoordinator::Index(SnapshotRefreshKind kind)
{
    return static_cast<std::size_t>(kind);
}

bool SnapshotRefreshCoordinator::AdvanceGeneration(State& state)
{
    if (state.generation == std::numeric_limits<std::uint64_t>::max())
    {
        state.inFlight = false;
        state.pending = false;
        return false;
    }
    ++state.generation;
    return true;
}

std::uint64_t SnapshotRefreshCoordinator::Deadline(std::uint64_t observedAtMs,
                                                   std::uint64_t timeoutMs)
{
    if (observedAtMs == 0 || timeoutMs == 0) return 0;
    if (observedAtMs > std::numeric_limits<std::uint64_t>::max() - timeoutMs)
        return std::numeric_limits<std::uint64_t>::max();
    return observedAtMs + timeoutMs;
}

SnapshotRefreshRequestResult SnapshotRefreshCoordinator::Request(SnapshotRefreshKind kind,
                                                                 std::uint64_t observedAtMs,
                                                                 std::uint64_t timeoutMs)
{
    SnapshotRefreshRequestResult result;
    if (!ValidKind(kind)) return result;

    std::lock_guard<std::mutex> lock(m_mutex);
    State& state = m_states[Index(kind)];
    if (state.inFlight)
    {
        state.pending = true;
        result.coalesced = true;
        result.generation = state.generation;
        return result;
    }

    if (!AdvanceGeneration(state)) return result;
    state.inFlight = true;
    state.pending = false;
    state.timeoutMs = timeoutMs;
    state.deadlineAtMs = Deadline(observedAtMs, timeoutMs);
    result.dispatch = true;
    result.generation = state.generation;
    return result;
}

SnapshotRefreshCompletionResult SnapshotRefreshCoordinator::Complete(
    SnapshotRefreshKind kind,
    std::uint64_t generation,
    std::uint64_t observedAtMs)
{
    SnapshotRefreshCompletionResult result;
    if (!ValidKind(kind) || generation == 0) return result;

    std::lock_guard<std::mutex> lock(m_mutex);
    State& state = m_states[Index(kind)];
    if (!state.inFlight || state.generation != generation) return result;

    result.accepted = true;
    result.completedGeneration = generation;
    if (!state.pending)
    {
        state.inFlight = false;
        state.deadlineAtMs = 0;
        return result;
    }

    state.pending = false;
    if (!AdvanceGeneration(state)) return result;
    state.inFlight = true;
    state.deadlineAtMs = Deadline(observedAtMs, state.timeoutMs);
    result.dispatchNext = true;
    result.nextGeneration = state.generation;
    return result;
}

SnapshotRefreshExpirationResult SnapshotRefreshCoordinator::Expire(
    SnapshotRefreshKind kind,
    std::uint64_t observedAtMs)
{
    SnapshotRefreshExpirationResult result;
    if (!ValidKind(kind) || observedAtMs == 0) return result;

    std::lock_guard<std::mutex> lock(m_mutex);
    State& state = m_states[Index(kind)];
    if (!state.inFlight || state.deadlineAtMs == 0 || observedAtMs < state.deadlineAtMs)
        return result;
    result.expired = true;
    result.hadPending = state.pending;
    result.generation = state.generation;
    state.inFlight = false;
    state.pending = false;
    state.deadlineAtMs = 0;
    return result;
}

bool SnapshotRefreshCoordinator::Abort(SnapshotRefreshKind kind,
                                       std::uint64_t generation)
{
    if (!ValidKind(kind) || generation == 0) return false;

    std::lock_guard<std::mutex> lock(m_mutex);
    State& state = m_states[Index(kind)];
    if (!state.inFlight || state.generation != generation) return false;
    state.inFlight = false;
    state.pending = false;
    state.deadlineAtMs = 0;
    return true;
}

bool SnapshotRefreshCoordinator::IsCurrent(SnapshotRefreshKind kind,
                                           std::uint64_t generation) const
{
    if (!ValidKind(kind) || generation == 0) return false;
    std::lock_guard<std::mutex> lock(m_mutex);
    const State& state = m_states[Index(kind)];
    return state.inFlight && state.generation == generation;
}

bool SnapshotRefreshCoordinator::IsInFlight(SnapshotRefreshKind kind) const
{
    if (!ValidKind(kind)) return false;
    std::lock_guard<std::mutex> lock(m_mutex);
    return m_states[Index(kind)].inFlight;
}
