#include "../HeptaTrade/tools/trading_tool_registry.h"
#include "latency_fixture_common.h"

#include <chrono>
#include <cstdint>
#include <string>
#include <vector>

namespace
{
constexpr int kWarmupIterations = 100;
constexpr int kSampleCount = 2000;

std::int64_t EpochMilliseconds()
{
    return std::chrono::duration_cast<std::chrono::milliseconds>(
        std::chrono::system_clock::now().time_since_epoch()).count();
}

class NoMutationAuthority final : public ExecutionAuthority
{
public:
    ExecutionCommandResult PlaceOrder(const PlaceOrderCommand& command) override
    {
        ExecutionCommandResult result;
        result.status = ExecutionCommandStatus::Rejected;
        result.commandId = command.context.toolCallId;
        result.reasonCode = "PERFORMANCE_FIXTURE_NO_MUTATION";
        return result;
    }

    ExecutionCommandResult CancelOrder(const CancelOrderCommand& command) override
    {
        ExecutionCommandResult result;
        result.status = ExecutionCommandStatus::Rejected;
        result.commandId = command.context.toolCallId;
        result.reasonCode = "PERFORMANCE_FIXTURE_NO_MUTATION";
        return result;
    }
};

bool InvokeSnapshot(TradingToolRegistry& registry,
                    const TradingToolSession& session,
                    const TradingToolCall& call)
{
    const TradingToolResult result = registry.Invoke(session, call);
    return result.status == TradingToolCallStatus::Ok &&
        result.reasonCode.empty() &&
        result.payloadJson.find("\"schema\":\"hepta.decision-snapshot.v1\"") !=
            std::string::npos;
}
}

int main()
{
    NoMutationAuthority execution;
    TradingToolReadCallbacks callbacks;
    callbacks.systemGetHealth = [](
        const TradingToolSession&, const TradingToolCall&,
        std::string& payload, std::string& reason) {
        payload =
            "{\"gateway_ready\":true,\"remote_execution_ready\":true,"
            "\"execution_service_epoch\":\"performance-epoch-1\","
            "\"execution_service_fencing_generation\":7,"
            "\"event_watermark\":42,\"state_generation\":11}";
        reason.clear();
        return true;
    };
    callbacks.marketGetQuote = [](
        const TradingToolSession&, const TradingToolCall& call,
        std::string& payload, std::string& reason) {
        payload = "{\"authoritative\":true,\"instrument\":\"" +
            call.instrument + "\",\"observed_at_ms\":" +
            std::to_string(EpochMilliseconds()) +
            ",\"stale\":false,\"bid\":1.1001,\"ask\":1.1002}";
        reason.clear();
        return true;
    };
    callbacks.accountGetSummary = [](
        const TradingToolSession&, const TradingToolCall&,
        std::string& payload, std::string& reason) {
        payload = "{\"authoritative\":true,\"account_complete\":true}";
        reason.clear();
        return true;
    };
    callbacks.portfolioListPositions = [](
        const TradingToolSession&, const TradingToolCall&,
        std::string& payload, std::string& reason) {
        payload =
            "{\"authoritative\":true,\"positions\":[{"
            "\"instrument\":\"EUR.USD\",\"quantity\":1000}]}";
        reason.clear();
        return true;
    };
    callbacks.ordersList = [](
        const TradingToolSession&, const TradingToolCall&,
        std::string& payload, std::string& reason) {
        payload =
            "{\"authoritative\":true,\"active_order_ids\":[]}";
        reason.clear();
        return true;
    };
    callbacks.riskGetLimits = [](
        const TradingToolSession&, const TradingToolCall&,
        std::string& payload, std::string& reason) {
        payload =
            "{\"authoritative\":true,\"gross_absolute_position\":1000}";
        reason.clear();
        return true;
    };

    TradingToolRegistry registry(execution, callbacks);
    TradingToolSession session;
    session.executionContext.agentId = "performance-agent";
    session.executionContext.sessionId = "performance-session";
    session.executionContext.account = "PERF-ACCOUNT";
    session.executionContext.executionDomain = "SIM-PERFORMANCE";
    session.environment = "WATCH";
    session.capabilities.insert("system.read");
    session.visibleInstruments.insert("EUR.USD");
    TradingToolCall call;
    call.name = "decision.get_snapshot";
    call.instrument = "EUR.USD";

    for (int i = 0; i < kWarmupIterations; ++i)
        if (!InvokeSnapshot(registry, session, call)) return 2;

    std::vector<long long> samples;
    samples.reserve(kSampleCount);
    for (int i = 0; i < kSampleCount; ++i)
    {
        const auto start = std::chrono::steady_clock::now();
        const bool ok = InvokeSnapshot(registry, session, call);
        const auto end = std::chrono::steady_clock::now();
        if (!ok) return 2;
        samples.push_back(
            std::chrono::duration_cast<std::chrono::microseconds>(end - start).count());
    }
    return HeptaLatencyFixture::ReportAndCheck(
        "decision-snapshot-capture-v1",
        "seven-read authoritative decision snapshot, canonical validation, digest and generation publication",
        kWarmupIterations,
        samples);
}
