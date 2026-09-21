#pragma once

#include "../execution/execution_authority.h"
#include "trading_tool_types.h"

#include <functional>
#include <cstddef>
#include <cstdint>
#include <string>
#include <unordered_map>
#include <unordered_set>
#include <vector>

struct TradingToolSession
{
    AgentExecutionContext executionContext;
    std::string environment; // Bound by the OS: WATCH, PAPER, LIVE_REDUCE_ONLY, LIVE_CAPPED.
    std::unordered_set<std::string> capabilities;
    // Server-derived visibility scope. The client cannot populate or widen it.
    std::unordered_set<std::string> visibleInstruments;
    std::unordered_map<std::string, InstrumentRef> boundInstrumentContracts;
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
