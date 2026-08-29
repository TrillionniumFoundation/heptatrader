#pragma once

#include "trading_tool_host.h"

#include <cstddef>
#include <string>

struct TypedToolResultEnvelope
{
    std::string status;
    std::string toolName;
    std::string reasonCode;
    std::string detail;
    long orderId = -1;
    std::string payloadJson;
};

class TypedToolProtocol
{
public:
    static const char* ProtocolName();
    static unsigned int ProtocolVersion();
    static bool EncodeRequest(const TradingToolHostRequest& request, std::string& body, std::string& reason);
    static bool DecodeRequest(const std::string& body, TradingToolHostRequest& request, std::string& reason);
    static std::string EncodeResultJson(const TradingToolResult& result);
    static bool DecodeResultEnvelope(const std::string& json,
                                     TypedToolResultEnvelope& result,
                                     std::string& reason);

    static bool ReadFrame(int fd, std::size_t maxBodyBytes, int timeoutMs,
                          std::string& body, std::string& reason);
    static bool WriteFrame(int fd, const std::string& body, int timeoutMs, std::string& reason);
};
