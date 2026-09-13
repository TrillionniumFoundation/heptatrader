#pragma once

#include "../oms_latency_observation.h"
#include "../tools/trading_tool_registry.h"
#include <array>
#include <cstdint>
#include <limits>
#include <locale>
#include <sstream>
#include <string>

// Fixed-cardinality observations, protected by UnixToolServer's telemetry
// mutex. This is local process telemetry, never execution or session authority.
// Reuse the bounded histogram primitive; no OMS writer is linked into Gateway.
struct GatewayActivity
{
    std::uint64_t responses = 0, written = 0, writeFailures = 0;
    std::array<std::uint64_t, 8> statuses{};
    bool saturated = false;
    OmsLatencySummary ingress, queueWait, dispatch, reply;
    static const char* StatusName(std::size_t i)
    {
        static const char* names[] = {"ok", "permission_denied", "invalid_tool",
            "rejected", "duplicate", "uncertain", "error", "unknown"};
        return names[i < 8 ? i : 7];
    }
    static std::size_t StatusIndex(TradingToolCallStatus status)
    {
        switch (status) {
        case TradingToolCallStatus::Ok: return 0;
        case TradingToolCallStatus::PermissionDenied: return 1;
        case TradingToolCallStatus::InvalidTool: return 2;
        case TradingToolCallStatus::Rejected: return 3;
        case TradingToolCallStatus::Duplicate: return 4;
        case TradingToolCallStatus::Uncertain: return 5;
        case TradingToolCallStatus::Error: return 6;
        }
        return 7;
    }
    void Increment(std::uint64_t& n)
    {
        if (n == std::numeric_limits<std::uint64_t>::max()) saturated = true;
        else ++n;
    }
    void RecordReply(TradingToolCallStatus status, bool success, std::uint64_t ns)
    {
        Increment(responses); Increment(success ? written : writeFailures);
        Increment(statuses[StatusIndex(status)]); reply.Observe(ns);
    }
};

inline std::uint64_t GatewayElapsedNs(std::chrono::steady_clock::time_point start)
{
    const auto n = std::chrono::duration_cast<std::chrono::nanoseconds>(
        std::chrono::steady_clock::now() - start).count();
    return n > 0 ? static_cast<std::uint64_t>(n) : 0;
}

inline void WriteGatewayActivity(std::ostream& out, const GatewayActivity& a)
{
    out << "\"response_attempts\":" << a.responses
        << ",\"response_writes\":" << a.written
        << ",\"response_write_failures\":" << a.writeFailures
        << ",\"saturated\":" << (a.saturated ? "true" : "false")
        << ",\"result_counts\":{";
    for (std::size_t i = 0; i < a.statuses.size(); ++i) {
        if (i) out << ',';
        out << '\"' << GatewayActivity::StatusName(i) << "\":" << a.statuses[i];
    }
    out << "},\"ingress_latency\":"; WriteOmsLatencyJson(out, a.ingress);
    out << ",\"queue_latency\":"; WriteOmsLatencyJson(out, a.queueWait);
    out << ",\"dispatch_latency\":"; WriteOmsLatencyJson(out, a.dispatch);
    out << ",\"reply_latency\":"; WriteOmsLatencyJson(out, a.reply);
}
