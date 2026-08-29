#include "../HeptaTrade/tool_host/tool_gateway_session_policy.h"

#include <cassert>
#include <iostream>
#include <map>
#include <string>
#include <unistd.h>

namespace
{
AgentOsRuntimeConfig AgentConfig()
{
    AgentOsRuntimeConfig config;
    config.toolSocket = "/tmp/hepta-tool-policy.sock";
    config.supervisorSocket = "/tmp/hepta-supervisor-policy.sock";
    config.agentUid = static_cast<std::uint32_t>(::geteuid());
    config.supervisorMaxTtlMs = 3600000;
    return config;
}

ExecutionGatewayRuntimeConfig ExecutionConfig(bool mutation)
{
    ExecutionGatewayRuntimeConfig config;
    config.mode = ExecutionGatewayMode::Simulator;
    config.executionSocket = "/tmp/hepta-execution-policy.sock";
    config.eventSocket = "/tmp/hepta-events-policy.sock";
    config.executionServiceUid = static_cast<std::uint32_t>(::geteuid());
    config.executionServiceUidConfigured = true;
    config.mutationToolsEnabled = mutation;
    return config;
}

ExecutionGatewayRuntimeConfig PaperExecutionConfig(bool mutation)
{
    ExecutionGatewayRuntimeConfig config = ExecutionConfig(mutation);
    config.mode = ExecutionGatewayMode::Paper;
    return config;
}

SessionSupervisorRequest Request(const std::string& templateId)
{
    SessionSupervisorRequest request;
    request.operation = SessionSupervisorOperation::Provision;
    request.templateId = templateId;
    request.token = std::string(32, 'T');
    request.agentId = "codex-agent";
    request.sessionId = "session-1";
    request.peerUid = static_cast<std::uint32_t>(::geteuid());
    request.ttlMs = 120000;
    return request;
}
}

int main()
{
    std::string reason;
    ToolGatewaySessionPolicy watch;
    std::map<std::string, std::string> values;
    values["HEPTA_TOOL_AGENT_ID"] = "codex-agent";
    values["HEPTA_TOOL_ACCOUNT"] = "SIM";
    assert(ToolGatewaySessionPolicy::FromValues(
        values, ExecutionConfig(false), AgentConfig(), watch, reason));
    assert(!watch.PaperEnabled());
    assert(watch.Venue() == "SIMULATOR");
    assert(watch.ExecutionDomain() == "SIM:SIM");
    TradingToolHostSessionBinding binding;
    assert(watch.Resolve(Request("watch"), binding, reason));
    assert(binding.session.environment == "WATCH");
    assert(binding.session.capabilities.count("system.read") == 1);
    assert(binding.session.capabilities.count("events.read") == 1);
    assert(binding.session.capabilities.count("trade.place") == 0);
    assert(watch.Authorize("hepta.os.bootstrap", binding, reason));
    SessionSupervisorRequest wrongAgent = Request("watch");
    wrongAgent.agentId = "openclaw-agent";
    assert(!watch.Resolve(wrongAgent, binding, reason));
    assert(reason == "SUPERVISOR_AGENT_ID_NOT_BOUND");
    assert(!watch.Resolve(Request("paper"), binding, reason));
    assert(reason == "SUPERVISOR_PAPER_TEMPLATE_DISABLED");

    ToolGatewaySessionPolicy paper;
    values["HEPTA_TOOL_SESSION_TEMPLATES"] = "watch,paper";
    values["HEPTA_TOOL_MAX_ORDER_QTY"] = "25000";
    values["HEPTA_TOOL_MAX_TRADE_CALLS_PER_MIN"] = "2";
    values["HEPTA_TOOL_DECISION_LEASE_TTL_MS"] = "7000";
    values["HEPTA_TOOL_CONTRACT_BINDINGS"] =
        "EUR.USD|EUR|CASH|IDEALPRO|USD;GBP.USD|GBP|CASH|IDEALPRO|USD";
    assert(ToolGatewaySessionPolicy::FromValues(
        values, ExecutionConfig(true), AgentConfig(), paper, reason));
    assert(paper.PaperEnabled());
    assert(paper.Resolve(Request("paper"), binding, reason));
    assert(binding.session.capabilities.count("trade.place") == 1);
    assert(binding.session.capabilities.count("trade.cancel") == 1);
    assert(binding.session.capabilities.count("risk.preview") == 1);
    assert(binding.allowedInstruments.size() == 2);
    assert(binding.instrumentContracts.at("EUR.USD").symbol == "EUR");
    assert(binding.maxOrderQuantity == 25000.0);
    assert(binding.maxTradeCallsPerMinute == 2);
    assert(binding.decisionLeaseTtlMs == 7000);
    assert(paper.Authorize("hepta.os.bootstrap", binding, reason));

    values["HEPTA_TOOL_MAX_ORDER_QTY"] = "25000.01";
    assert(!ToolGatewaySessionPolicy::FromValues(
        values, ExecutionConfig(true), AgentConfig(), paper, reason));
    assert(reason == "TOOL_GATEWAY_TRADE_LIMITS_INVALID");
    values["HEPTA_TOOL_MAX_ORDER_QTY"] = "25000";

    ToolGatewaySessionPolicy domainPaper;
    values["HEPTA_EXECUTION_DOMAIN_ID"] = "PAPER:codex-agent";
    assert(ToolGatewaySessionPolicy::FromValues(
        values, PaperExecutionConfig(true), AgentConfig(), domainPaper, reason));
    assert(domainPaper.ExecutionDomain() == "PAPER:codex-agent");
    assert(domainPaper.Resolve(Request("paper"), binding, reason));
    binding.executionDomain = "PAPER:openclaw-b";
    assert(!domainPaper.Authorize("hepta.os.bootstrap", binding, reason));
    assert(reason == "TOOL_GATEWAY_SESSION_POLICY_REJECTED");
    values["HEPTA_EXECUTION_DOMAIN_ID"] = "PAPER:codex-a";
    assert(!ToolGatewaySessionPolicy::FromValues(
        values, PaperExecutionConfig(true), AgentConfig(), domainPaper, reason));
    assert(reason == "TOOL_GATEWAY_EXECUTION_DOMAIN_INVALID");
    values["HEPTA_EXECUTION_DOMAIN_ID"] = "PAPER:";
    assert(!ToolGatewaySessionPolicy::FromValues(
        values, PaperExecutionConfig(true), AgentConfig(), domainPaper, reason));
    assert(reason == "TOOL_GATEWAY_EXECUTION_DOMAIN_INVALID");
    values["HEPTA_EXECUTION_DOMAIN_ID"] = "PAPER:Codex-a";
    assert(!ToolGatewaySessionPolicy::FromValues(
        values, PaperExecutionConfig(true), AgentConfig(), domainPaper, reason));
    values["HEPTA_EXECUTION_DOMAIN_ID"] =
        "PAPER:abcdefghijklmnopqrstuvwxyzabcdefg";
    assert(!ToolGatewaySessionPolicy::FromValues(
        values, PaperExecutionConfig(true), AgentConfig(), domainPaper, reason));
    values.erase("HEPTA_EXECUTION_DOMAIN_ID");

    assert(paper.Resolve(Request("paper"), binding, reason));
    binding.maxOrderQuantity = 2000.0;
    assert(!paper.Authorize("hepta.os.bootstrap", binding, reason));
    assert(reason == "TOOL_GATEWAY_PAPER_SCOPE_INVALID");

    values["HEPTA_TOOL_CONTRACT_BINDINGS"] =
        "EUR.USD|EUR|STK|SMART|USD";
    assert(!ToolGatewaySessionPolicy::FromValues(
        values, ExecutionConfig(true), AgentConfig(), paper, reason));
    assert(reason == "TOOL_GATEWAY_CONTRACT_BINDING_INVALID");

    values["HEPTA_TOOL_CONTRACT_BINDINGS"] =
        "EUR.USD|EUR|CASH|IDEALPRO|USD";
    assert(!ToolGatewaySessionPolicy::FromValues(
        values, ExecutionConfig(false), AgentConfig(), paper, reason));
    assert(reason == "TOOL_GATEWAY_PAPER_TEMPLATE_FLAG_MISMATCH");

    std::cout << "tool_gateway_session_policy_tests: PASS\n";
    return 0;
}
