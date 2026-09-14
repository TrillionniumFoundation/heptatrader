#pragma once

#include "execution_authority.h"
#include "../oms_capacity_observation.h"
#include <array>
#include <limits>

// Fixed-cardinality, process-instance observations. Protected by the existing
// coordinator mutex; neither a persistence ledger nor a trading authority.
struct ExecutionOperationObservation
{
    // accepted, rejected, duplicate, uncertain, exception (in that order).
    std::array<std::uint64_t, 5> results{};
    OmsLatencySummary latency; // Existing lock-held work scope, preserved.
    bool timingPresent = false;
    OmsLatencySummary lockWait;
    OmsLatencySummary totalLatency; // Call entry through result/exception, before unlock.
    bool saturated = false;
    void Observe(std::size_t result) noexcept
    {
        if (results[result] == std::numeric_limits<std::uint64_t>::max()) saturated = true;
        else ++results[result];
    }
    void Observe(ExecutionCommandStatus status) noexcept
    {
        switch (status)
        {
        case ExecutionCommandStatus::Accepted: Observe(0U); break;
        case ExecutionCommandStatus::Rejected: Observe(1U); break;
        case ExecutionCommandStatus::Duplicate: Observe(2U); break;
        case ExecutionCommandStatus::Uncertain: Observe(3U); break;
        default: Observe(4U); break;
        }
    }
};

// Construct and finish while holding the same coordinator mutex. The start
// timestamp is captured before acquiring it, without touching shared counters.
// Explicit time points make boundaries testable without scheduler sleeps.
class ExecutionOperationTiming
{
public:
    using Clock = OmsScopedLatencySample::Clock;
    ExecutionOperationTiming(ExecutionOperationObservation& target,
                             Clock::time_point entered,
                             Clock::time_point acquired) noexcept
        : m_held(target.latency, acquired), m_total(target.totalLatency, entered)
    {
        target.timingPresent = true;
        OmsScopedLatencySample wait(target.lockWait, entered);
        wait.Finish(acquired);
    }
    ~ExecutionOperationTiming() noexcept { Finish(); }
    void Finish(Clock::time_point now = Clock::now()) noexcept
    {
        m_held.Finish(now);
        m_total.Finish(now);
    }
private:
    OmsScopedLatencySample m_held;
    OmsScopedLatencySample m_total;
};

struct ExecutionRuntimeObservation
{
    bool present = false;
    // place, cancel, authoritative flatten. No arbitrary reason/owner labels.
    std::array<ExecutionOperationObservation, 3> operations{};
    OmsLatencySummary recoveryLatency;
    std::uint64_t retainedCommands = 0;
    std::uint64_t orderOwners = 0;
    std::uint64_t fencedOwners = 0;
    std::uint64_t recoveryOnlyOwners = 0;
    std::uint64_t retainedSendAttempts = 0;
    bool mutationBlocked = false;
};

// Additive extension to the existing OMS log/export path. Snapshots are read
// separately, so this does NOT claim a transactionally joint journal/state view.
// No IDs, request payloads, account names, tokens or credentials are serialized.
inline std::string ExecutionCapacityObservation(
    const OmsJournalHealthSnapshot& journal, const ExecutionRuntimeObservation& execution,
    long long observedAtMs, const std::string& epoch, std::uint64_t monotonicMs)
{
    std::string base = OmsCapacityObservation(journal, observedAtMs, epoch, monotonicMs);
    if (!execution.present) return base; // absence must never become healthy zero
    base.pop_back();
    std::ostringstream out;
    out.imbue(std::locale::classic());
    out << base << ",\"execution_metrics\":{\"results\":[";
    bool saturated = false;
    for (std::size_t op = 0; op < execution.operations.size(); ++op)
    {
        if (op) out << ',';
        out << '[';
        const auto& value = execution.operations[op];
        saturated = saturated || value.saturated;
        for (std::size_t i = 0; i < value.results.size(); ++i)
        {
            if (i) out << ',';
            out << value.results[i];
        }
        out << ']';
    }
    out << "],\"metrics_saturated\":" << (saturated ? "true" : "false")
        << ",\"retained_commands\":" << execution.retainedCommands
        << ",\"order_owners\":" << execution.orderOwners
        << ",\"fenced_owners\":" << execution.fencedOwners
        << ",\"recovery_only_owners\":" << execution.recoveryOnlyOwners
        << ",\"retained_send_attempts\":" << execution.retainedSendAttempts
        << ",\"mutation_blocked\":" << (execution.mutationBlocked ? "true" : "false");
    const char* names[] = {"place_latency", "cancel_latency", "flatten_latency"};
    for (std::size_t i = 0; i < execution.operations.size(); ++i)
    {
        out << ",\"" << names[i] << "\":";
        WriteOmsLatencyJson(out, execution.operations[i].latency);
        if (execution.operations[i].timingPresent)
        {
            out << ",\"" << names[i] << "_lock_wait\":";
            WriteOmsLatencyJson(out, execution.operations[i].lockWait);
            out << ",\"" << names[i] << "_total\":";
            WriteOmsLatencyJson(out, execution.operations[i].totalLatency);
        }
    }
    out << ",\"recovery_latency\":";
    WriteOmsLatencyJson(out, execution.recoveryLatency);
    out << "}}";
    return out.str();
}
