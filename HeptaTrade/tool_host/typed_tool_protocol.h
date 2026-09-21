#pragma once

#include "trading_tool_request.h"

#include <cstddef>
#include <cstdint>
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

// A decoded record of an approved service preview, NOT a locally issued permit
// or evidence of current validity. Keep it bound to the original proposal and
// credential; the Execution Service alone validates it when a mutation arrives.
struct TypedPreviewAuthorization
{
    std::string toolName;
    std::string commandId;
    std::string previewPermit;
    std::int64_t permitExpiresAtMs = 0;
    std::string serviceEpoch;
    std::uint64_t serviceFencingGeneration = 0;
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

    // Parse the full envelope and the exact approved preview payload using the
    // same bounded JSON parser. expectedTool must be risk.preview_order or
    // risk.preview_flatten. Rejection/uncertainty/mismatch/malformed data clears
    // authorization and returns false. No side effect, time refresh or retry.
    // Input strings may borrow previous output fields; output-to-output aliases
    // are not supported. No private authority or vendor types are exposed.
    static bool DecodePreviewAuthorization(const std::string& json,
                                            const std::string& expectedTool,
                                            TypedPreviewAuthorization& authorization,
                                            std::string& reason);

    static bool ReadFrame(int fd, std::size_t maxBodyBytes, int timeoutMs,
                          std::string& body, std::string& reason);
    static bool WriteFrame(int fd, const std::string& body, int timeoutMs, std::string& reason);
};
