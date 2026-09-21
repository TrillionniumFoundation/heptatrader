#pragma once

#include "../tools/trading_tool_types.h"

#include <cstdint>
#include <string>

// Client-originated request data, not a session or an execution authority.
// Authentication, ownership and permits remain verified by the service.
struct TradingToolHostRequest
{
    std::string sessionToken;
    std::string toolCallId;
    unsigned int protocolMinVersion = 1;
    unsigned int protocolMaxVersion = 1;
    std::string expectedSchemaHash;
    std::uint64_t queueDeadlineAtMs = 0;
    std::string cancelToolCallId;
    TradingToolCall call;
};
