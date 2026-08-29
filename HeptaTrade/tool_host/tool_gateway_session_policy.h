#pragma once

#include "agent_os_runtime_config.h"
#include "execution_gateway_runtime_config.h"
#include "session_supervisor_protocol.h"
#include "trading_tool_host.h"

#include <cstdint>
#include <map>
#include <string>
#include <unordered_map>

class ToolGatewaySessionPolicy
{
public:
    static bool FromEnvironment(const ExecutionGatewayRuntimeConfig& execution,
                                const AgentOsRuntimeConfig& agentOs,
                                ToolGatewaySessionPolicy& policy,
                                std::string& reason);
    static bool FromValues(const std::map<std::string, std::string>& values,
                           const ExecutionGatewayRuntimeConfig& execution,
                           const AgentOsRuntimeConfig& agentOs,
                           ToolGatewaySessionPolicy& policy,
                           std::string& reason);

    bool Resolve(const SessionSupervisorRequest& request,
                 TradingToolHostSessionBinding& binding,
                 std::string& reason) const;
    bool Authorize(const std::string& issuer,
                   const TradingToolHostSessionBinding& binding,
                   std::string& reason) const;

    bool PaperEnabled() const;
    const std::string& Account() const;
    const std::string& Venue() const;
    const std::string& ExecutionDomain() const;

private:
    std::string m_agentId;
    std::string m_account;
    std::string m_venue;
    std::string m_executionDomain;
    std::uint32_t m_agentUid = 0;
    std::uint64_t m_maxSessionTtlMs = 0;
    std::uint32_t m_decisionLeaseTtlMs = 5000;
    bool m_paperEnabled = false;
    double m_maxOrderQuantity = 0.0;
    std::uint32_t m_maxTradeCallsPerMinute = 0;
    std::unordered_map<std::string, IBContractLite> m_contracts;
};
