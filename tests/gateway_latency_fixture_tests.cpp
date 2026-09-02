#include "../HeptaTrade/tools/trading_tool_registry.h"
#include "latency_fixture_common.h"

#include <chrono>
#include <string>
#include <vector>

namespace
{
constexpr int kWarmupIterations = 1000;
constexpr int kSampleCount = 10000;

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

bool InvokeHealth(TradingToolRegistry& registry,
                  const TradingToolSession& session,
                  const TradingToolCall& call)
{
    const TradingToolResult result = registry.Invoke(session, call);
    return result.status == TradingToolCallStatus::Ok &&
        result.reasonCode.empty() &&
        result.payloadJson == "{\"gateway_ready\":true}";
}
}

int main()
{
    NoMutationAuthority execution;
    TradingToolReadCallbacks callbacks;
    callbacks.systemGetHealth = [](
        const TradingToolSession&, const TradingToolCall&,
        std::string& payload, std::string& reason) {
        payload = "{\"gateway_ready\":true}";
        reason.clear();
        return true;
    };
    TradingToolRegistry registry(execution, callbacks);
    TradingToolSession session;
    session.environment = "WATCH";
    session.capabilities.insert("system.read");
    TradingToolCall call;
    call.name = "system.get_health";

    for (int i = 0; i < kWarmupIterations; ++i)
        if (!InvokeHealth(registry, session, call)) return 2;

    std::vector<long long> samples;
    samples.reserve(kSampleCount);
    for (int i = 0; i < kSampleCount; ++i)
    {
        const auto start = std::chrono::steady_clock::now();
        const bool ok = InvokeHealth(registry, session, call);
        const auto end = std::chrono::steady_clock::now();
        if (!ok) return 2;
        samples.push_back(
            std::chrono::duration_cast<std::chrono::microseconds>(end - start).count());
    }
    return HeptaLatencyFixture::ReportAndCheck(
        "gateway-validated-read-dispatch-v1",
        "capability/environment/schema validation, read callback and bounded JSON validation",
        kWarmupIterations,
        samples);
}
