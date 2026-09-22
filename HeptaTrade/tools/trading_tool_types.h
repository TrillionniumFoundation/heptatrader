#pragma once

// Transport values only. No session binding, lease manager, registry or
// Execution authority is exposed through the native-client header closure.
// Keep these existing wire types unchanged when moving their declarations.
#include "../execution/trading_contract.h"

#include <cstddef>
#include <cstdint>
#include <string>

enum class TradingToolEffect
{
    Read = 0,
    Trade
};

enum class TradingToolCallStatus
{
    Ok = 0,
    PermissionDenied,
    InvalidTool,
    Rejected,
    Duplicate,
    Uncertain,
    Error
};

// Shared by the privileged registry, Unix result codec, and installed native
// client SDK.  Keep the transport ceiling in this installed dependency rather
// than making SDK consumers depend on a private host-only header.
struct TradingToolWireLimits
{
    static std::size_t MaximumResultEnvelopeBytes()
    {
        return 1024u * 1024u;
    }
};

struct TradingToolDescriptor
{
    std::string name;
    std::string description;
    std::string requiredCapability;
    TradingToolEffect effect = TradingToolEffect::Read;
    int timeoutMs = 1000;
    std::string inputSchema;
    std::string resultSchema;
};

struct TradingToolCall
{
    std::string name;
    std::string targetToolName;
    std::string targetCommandId;
    std::string instrument;
    long orderId = -1;
    InstrumentRef ibContract;
    OrderIntent ibOrder;
    // The current supported venue profiles fix TIF to DAY. Keep the Agent-visible
    // value explicit so a model cannot request one policy while another is
    // silently sent to the venue.
    std::string timeInForce;
    double referencePrice = 0.0;
    long long expiresAtMs = 0;
    std::string previewPermit;
    int waitTimeoutMs = 0;
    std::uint64_t afterEventSequence = 0;
};

struct TradingToolResult
{
    TradingToolCallStatus status = TradingToolCallStatus::Error;
    std::string toolName;
    std::string reasonCode;
    std::string detail;
    std::string payloadJson;
    long orderId = -1;
};
