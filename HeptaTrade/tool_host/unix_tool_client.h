#pragma once

#include "trading_tool_host.h"

#include <cstddef>
#include <string>

class UnixToolClient
{
public:
    static bool Call(const std::string& socketPath,
                     const TradingToolHostRequest& request,
                     std::string& responseJson,
                     std::string& reason,
                     int timeoutMs = 5000,
                     std::size_t maxResponseBytes = 1048576);
};
