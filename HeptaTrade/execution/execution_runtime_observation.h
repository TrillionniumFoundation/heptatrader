#pragma once

#include "execution_authority.h"
#include "../oms_capacity_observation.h"
#include <array>
#include <limits>

// Versioned, bounded reason bins. Unknown/custom strings never become labels.
// These are local coordinator outcomes, not all profile/preview/risk decisions.
inline const std::array<const char*, 41>& ExecutionReasonNames() noexcept
{
    static const std::array<const char*, 41> names{{
        "NONE",
        "EXCEPTION",
        "OTHER",
        "DUPLICATE_TOOL_CALL",
        "IDEMPOTENCY_KEY_CONFLICT",
        "INVALID_AGENT_CONTEXT",
        "INVALID_ORDER",
        "REQUEST_HASH_FAILED",
        "SESSION_OWNER_FENCED",
        "SESSION_RECOVERY_ONLY",
        "TOOL_CALL_EXPIRED",
        "DECISION_LEASE_REQUIRED",
        "DECISION_LEASE_INVALID",
        "MUTATION_BLOCKED",
        "OMS_NEW_ENTRY_CAPACITY_EXHAUSTED",
        "OMS_NEW_ENTRY_CAPACITY_UNKNOWN",
        "OMS_INTENT_WRITE_FAILED",
        "OMS_PLACE_SEND_ATTEMPT_WRITE_FAILED",
        "OMS_PLACE_RECEIPT_WRITE_FAILED",
        "OMS_CANCEL_SEND_ATTEMPT_WRITE_FAILED",
        "OMS_CANCEL_PENDING_RECEIPT_WRITE_FAILED",
        "OMS_FLATTEN_SEND_ATTEMPT_WRITE_FAILED",
        "OMS_FLATTEN_RECEIPT_WRITE_FAILED",
        "IB_PLACE_REJECT",
        "IB_PLACE_OUTCOME_UNCERTAIN",
        "IB_CANCEL_REJECT",
        "IB_CANCEL_OUTCOME_UNCERTAIN",
        "IB_CANCEL_DEFERRED_UNTIL_BROKER_ACK",
        "IB_FLATTEN_REJECT",
        "IB_FLATTEN_OUTCOME_UNCERTAIN",
        "AUTHORITATIVE_FLATTEN_PLAN_INVALID",
        "POSITION_ALREADY_FLAT",
        "IB_PAPER_KILL_SWITCH_ENGAGED",
        "IB_PAPER_KILL_SWITCH_STATE_UNCERTAIN",
        "IB_POST_FILL_RISK_REFRESH_PENDING",
        "IB_PAPER_PLACE_QUOTE_CHANGED_BEFORE_SEND",
        "IB_PAPER_FLATTEN_QUOTE_CHANGED_BEFORE_SEND",
        "RECOVERY_RECONCILE_REQUIRED",
        "AUTHORITATIVE_ORDER_PROJECTION_FAILED",
        "AUTHORITATIVE_CANCEL_PROJECTION_FAILED",
        "AUTHORITATIVE_FLATTEN_PROJECTION_FAILED"}};
    return names;
}
inline std::size_t ExecutionReasonIndex(const std::string& reason) noexcept
{
    if (reason.empty()) return 0;
    const auto& names = ExecutionReasonNames();
    for (std::size_t index = 3; index < names.size(); ++index)
        if (reason == names[index]) return index;
    return 2; // OTHER; even a diagnostic spelling NONE/EXCEPTION is not an observation.
}

// Fixed-cardinality, process-instance observations. Protected by the existing
// coordinator mutex; neither a persistence ledger nor a trading authority.
struct ExecutionOperationObservation
{
    // accepted, rejected, duplicate, uncertain, exception (in that order).
    std::array<std::uint64_t, 5> results{};
    std::array<std::uint64_t, 41> reasonCounts{};
    OmsLatencySummary latency; // Existing lock-held work scope, preserved.
    bool timingPresent = false;
    OmsLatencySummary lockWait;
    OmsLatencySummary totalLatency; // Call entry through result/exception, before unlock.
    bool saturated = false;
    static std::size_t ResultIndex(ExecutionCommandStatus status) noexcept
    {
        switch (status)
        {
        case ExecutionCommandStatus::Accepted: return 0;
        case ExecutionCommandStatus::Rejected: return 1;
        case ExecutionCommandStatus::Duplicate: return 2;
        case ExecutionCommandStatus::Uncertain: return 3;
        default: return 4;
        }
    }
    void ObserveBins(std::size_t result, std::size_t reason) noexcept
    {
        if (result >= results.size() || reason >= reasonCounts.size())
        { saturated = true; return; }
        if (results[result] == std::numeric_limits<std::uint64_t>::max()) saturated = true;
        else ++results[result];
        if (reasonCounts[reason] == std::numeric_limits<std::uint64_t>::max()) saturated = true;
        else ++reasonCounts[reason];
    }
    void Observe(std::size_t result) noexcept
    { ObserveBins(result, result == 4 ? 1 : 0); }
    void Observe(ExecutionCommandStatus status) noexcept
    { Observe(ResultIndex(status)); }
    void Observe(const ExecutionCommandResult& result) noexcept
    { ObserveBins(ResultIndex(result.status), ExecutionReasonIndex(result.reasonCode)); }

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
        : m_target(target), m_total(target.totalLatency, entered),
          m_heldStart(acquired)
    {
        target.timingPresent = true;
        OmsScopedLatencySample wait(target.lockWait, entered);
        wait.Finish(acquired);
    }
    ~ExecutionOperationTiming() noexcept { Finish(); }

    void PauseHeld(Clock::time_point now = Clock::now()) noexcept
    {
        if (!m_heldActive || m_finished) return;
        const auto raw = std::chrono::duration_cast<std::chrono::nanoseconds>(
            now - m_heldStart).count();
        const std::uint64_t elapsed =
            raw > 0 ? static_cast<std::uint64_t>(raw) : 0U;
        const std::uint64_t maximum =
            std::numeric_limits<std::uint64_t>::max();
        if (elapsed > maximum - m_heldNs)
            m_heldNs = maximum;
        else
            m_heldNs += elapsed;
        m_heldActive = false;
    }

    void ResumeHeld(Clock::time_point now = Clock::now()) noexcept
    {
        if (m_heldActive || m_finished) return;
        m_heldStart = now;
        m_heldActive = true;
    }

    void Finish(Clock::time_point now = Clock::now()) noexcept
    {
        if (m_finished) return;
        PauseHeld(now);
        m_target.latency.Observe(m_heldNs);
        m_total.Finish(now);
        m_finished = true;
    }

private:
    ExecutionOperationObservation& m_target;
    OmsScopedLatencySample m_total;
    Clock::time_point m_heldStart;
    std::uint64_t m_heldNs = 0;
    bool m_heldActive = true;
    bool m_finished = false;
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
    out << "],\"reason_schema_version\":1,\"reason_counts\":[";
    for (std::size_t op = 0; op < execution.operations.size(); ++op)
    {
        if (op) out << ',';
        out << '[';
        const auto& counts = execution.operations[op].reasonCounts;
        for (std::size_t i = 0; i < counts.size(); ++i)
        { if (i) out << ','; out << counts[i]; }
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
