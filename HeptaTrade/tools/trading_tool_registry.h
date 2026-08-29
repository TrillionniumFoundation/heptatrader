#pragma once

#include "../execution/execution_authority.h"

#include <functional>
#include <cstddef>
#include <cstdint>
#include <string>
#include <unordered_map>
#include <unordered_set>
#include <vector>

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

struct TradingToolSession
{
    AgentExecutionContext executionContext;
    std::string environment; // Bound by the OS: WATCH, PAPER, LIVE_REDUCE_ONLY, LIVE_CAPPED.
    std::unordered_set<std::string> capabilities;
    // Server-derived visibility scope. The client cannot populate or widen it.
    std::unordered_set<std::string> visibleInstruments;
    std::unordered_map<std::string, InstrumentRef> boundInstrumentContracts;
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

struct TradingToolReadCallbacks
{
    // The callback must return a bounded JSON object generated from live C++ state.
    typedef std::function<bool(const TradingToolSession&, const TradingToolCall&, std::string&, std::string&)> Handler;
    Handler marketGetQuote;
    Handler accountGetSummary;
    Handler portfolioListPositions;
    Handler ordersList;
    Handler executionGetCommandStatus;
    Handler riskGetLimits;
    Handler riskPreviewOrder;
    Handler riskPreviewFlatten;
    Handler eventsWait;
    Handler systemGetHealth;
};

struct TradingToolTradeCallbacks
{
    std::function<ExecutionCommandResult(
        const TradingToolSession&, const TradingToolCall&)>
        flattenPosition;
};

class TradingToolRegistry
{
public:
    TradingToolRegistry(ExecutionAuthority& execution,
                        const TradingToolReadCallbacks& readCallbacks = TradingToolReadCallbacks(),
                        const TradingToolTradeCallbacks& tradeCallbacks = TradingToolTradeCallbacks());

    std::vector<TradingToolDescriptor> ListTools(const TradingToolSession& session) const;
    bool GetDescriptor(const std::string& name, TradingToolDescriptor& out) const;
    static std::string DescriptorSchemaHash(const TradingToolDescriptor& descriptor);
    static unsigned int DiscoverySchemaVersion();
    std::string CatalogSchemaHash(const TradingToolSession& session) const;
    TradingToolResult Invoke(const TradingToolSession& session, const TradingToolCall& call);

    static const char* StatusName(TradingToolCallStatus status);
    static bool ValidateCallSemantics(const TradingToolCall& call,
                                      std::string& reasonCode,
                                      std::string& detail);

private:
    typedef TradingToolReadCallbacks::Handler ReadHandler;

    void RegisterDefaults();
    void RegisterReadTool(const std::string& name,
                          const std::string& description,
                          const std::string& capability,
                          int timeoutMs,
                          const std::string& inputSchema,
                          const ReadHandler& handler);
    bool HasCapability(const TradingToolSession& session, const std::string& capability) const;
    static bool EnvironmentAllows(const TradingToolSession& session,
                                  const TradingToolDescriptor& descriptor,
                                  std::string& reasonCode);
    bool HasRequiredCapabilities(const TradingToolSession& session,
                                 const TradingToolDescriptor& descriptor,
                                 std::string& missingCapability) const;
    TradingToolResult InvokeRead(const TradingToolSession& session,
                                 const TradingToolDescriptor& descriptor,
                                 const TradingToolCall& call) const;
    TradingToolResult InvokeWatchSnapshot(const TradingToolSession& session,
                                          const TradingToolCall& call) const;
    TradingToolResult InvokeDiscovery(const TradingToolSession& session,
                                      const TradingToolCall& call) const;
    static TradingToolResult FromExecution(const std::string& toolName, const ExecutionCommandResult& execution);

private:
    ExecutionAuthority& m_execution;
    TradingToolReadCallbacks m_readCallbacks;
    TradingToolTradeCallbacks m_tradeCallbacks;
    std::unordered_map<std::string, TradingToolDescriptor> m_descriptors;
    std::unordered_map<std::string, ReadHandler> m_readHandlers;
};
