#pragma once
#include "native_tool_client.h"
#include "../../strategies/native_research/signal.h"
#include <cstdint>
#include <string>

struct StrategyCallResult {
    NativeToolClientResult native;
    std::string commandId;
    std::uint64_t elapsedUs = 0;
    bool transportComplete = false;
};

// Unprivileged forwarding client. All paths use the maintained NativeToolClient
// and Tool Gateway; it owns no broker SDK, position, approval, journal or retry
// authority. true means a decoded transport response, NOT accepted/filled.
class StrategyIntentClient {
public:
    explicit StrategyIntentClient(const NativeToolClientConfig& config);
    bool Preview(const hepta::research::BoundedIntent& intent,
                 const std::string& requestId, std::int64_t nowMs,
                 StrategyCallResult& result, std::string& reason) const;
    // commandId and previewPermit must be the matching Execution-issued pair.
    bool Submit(const hepta::research::BoundedIntent& intent,
                const std::string& commandId, const std::string& previewPermit,
                std::int64_t nowMs, StrategyCallResult& result, std::string& reason) const;
    bool Query(const std::string& commandId, const std::string& queryRequestId,
               StrategyCallResult& result, std::string& reason) const;
    static bool BuildRequest(const hepta::research::BoundedIntent& intent,
                             const std::string& requestId, const std::string& previewPermit,
                             bool submit, std::int64_t nowMs,
                             TradingToolHostRequest& request, std::string& reason);
private:
    NativeToolClient client_;
    bool Call(TradingToolHostRequest request, const std::string& commandId,
              StrategyCallResult& result, std::string& reason) const;
};
