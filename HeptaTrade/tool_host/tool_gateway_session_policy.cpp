#include "tool_gateway_session_policy.h"

#include <algorithm>
#include <cerrno>
#include <chrono>
#include <cmath>
#include <cstdlib>
#include <limits>
#include <locale>
#include <set>
#include <sstream>
#include <vector>

namespace
{
bool CanonicalUnsignedInteger(const std::string& value)
{
    if (value.empty() || (value.size() > 1 && value[0] == '0')) return false;
    for (std::string::const_iterator it = value.begin(); it != value.end(); ++it)
        if (*it < '0' || *it > '9') return false;
    return true;
}

bool CanonicalFloating(const std::string& value)
{
    if (value.empty()) return false;
    std::size_t offset = value[0] == '-' ? 1u : 0u;
    if (offset == value.size()) return false;
    if (value[offset] == '0')
    {
        ++offset;
        if (offset < value.size() && value[offset] >= '0' &&
            value[offset] <= '9') return false;
    }
    else
    {
        if (value[offset] < '1' || value[offset] > '9') return false;
        while (offset < value.size() && value[offset] >= '0' &&
               value[offset] <= '9') ++offset;
    }
    if (offset < value.size() && value[offset] == '.')
    {
        ++offset;
        const std::size_t fractionStart = offset;
        while (offset < value.size() && value[offset] >= '0' &&
               value[offset] <= '9') ++offset;
        if (offset == fractionStart) return false;
    }
    if (offset < value.size() &&
        (value[offset] == 'e' || value[offset] == 'E'))
    {
        ++offset;
        if (offset < value.size() &&
            (value[offset] == '+' || value[offset] == '-')) ++offset;
        const std::size_t exponentStart = offset;
        while (offset < value.size() && value[offset] >= '0' &&
               value[offset] <= '9') ++offset;
        if (offset == exponentStart) return false;
    }
    return offset == value.size();
}

std::string Read(const std::map<std::string, std::string>& values, const char* key)
{
    const std::map<std::string, std::string>::const_iterator found = values.find(key);
    return found == values.end() ? std::string() : found->second;
}

bool CanonicalText(const std::string& value, std::size_t maximum)
{
    if (value.empty() || value.size() > maximum) return false;
    for (std::string::const_iterator it = value.begin(); it != value.end(); ++it)
    {
        const unsigned char byte = static_cast<unsigned char>(*it);
        if (byte < 0x21 || byte > 0x7e || byte == '|' || byte == ';') return false;
    }
    return true;
}

bool CanonicalInstrument(const std::string& value)
{
    if (value.empty() || value.size() > 128) return false;
    bool previousSeparator = false;
    bool sawAlphaNumeric = false;
    for (std::string::const_iterator it = value.begin(); it != value.end(); ++it)
    {
        const unsigned char byte = static_cast<unsigned char>(*it);
        const bool alphaNumeric =
            (byte >= 'a' && byte <= 'z') || (byte >= 'A' && byte <= 'Z') ||
            (byte >= '0' && byte <= '9');
        if (alphaNumeric)
        {
            sawAlphaNumeric = true;
            previousSeparator = false;
            continue;
        }
        const bool separator = byte == '.' || byte == '-' || byte == '_' ||
            byte == '/' || byte == ':';
        if (!separator || previousSeparator || it == value.begin() ||
            it + 1 == value.end())
            return false;
        previousSeparator = true;
    }
    return sawAlphaNumeric && !previousSeparator;
}

bool CanonicalDomainSuffix(const std::string& value)
{
    if (value.empty() || value.size() > 32 ||
        value[0] < 'a' || value[0] > 'z')
        return false;
    for (std::size_t i = 1; i < value.size(); ++i)
    {
        const unsigned char byte = static_cast<unsigned char>(value[i]);
        const bool lower = byte >= static_cast<unsigned char>('a') &&
            byte <= static_cast<unsigned char>('z');
        const bool digit = byte >= static_cast<unsigned char>('0') &&
            byte <= static_cast<unsigned char>('9');
        if (!lower && !digit && byte != '-')
            return false;
    }
    return true;
}

bool PaperDomain(const std::string& value)
{
    static const std::string prefix = "PAPER:";
    return value == "PAPER" ||
        (value.size() > prefix.size() &&
         value.compare(0, prefix.size(), prefix) == 0 &&
         CanonicalDomainSuffix(value.substr(prefix.size())));
}

bool PaperDomainMatchesAgent(const std::string& executionDomain,
                             const std::string& agentId)
{
    static const std::string prefix = "PAPER:";
    if (executionDomain == "PAPER") return true;
    return executionDomain.size() > prefix.size() &&
        executionDomain.compare(0, prefix.size(), prefix) == 0 &&
        executionDomain.substr(prefix.size()) == agentId;
}

bool ParseUnsigned(const std::string& value, std::uint64_t minimum,
                   std::uint64_t maximum, std::uint64_t& parsed)
{
    if (!CanonicalUnsignedInteger(value)) return false;
    char* end = nullptr;
    errno = 0;
    const unsigned long long number = std::strtoull(value.c_str(), &end, 10);
    if (errno != 0 || end == value.c_str() || *end != '\0' ||
        number < minimum || number > maximum)
        return false;
    parsed = static_cast<std::uint64_t>(number);
    return true;
}

bool ParsePositiveDouble(const std::string& value, double& parsed)
{
    if (!CanonicalFloating(value)) return false;
    std::istringstream input(value);
    input.imbue(std::locale::classic());
    input >> std::noskipws >> parsed;
    return input && input.eof() && std::isfinite(parsed) && parsed > 0.0;
}

std::vector<std::string> Split(const std::string& value, char delimiter)
{
    std::vector<std::string> parts;
    std::size_t begin = 0;
    for (;;)
    {
        const std::size_t end = value.find(delimiter, begin);
        parts.push_back(value.substr(begin, end == std::string::npos ?
            std::string::npos : end - begin));
        if (end == std::string::npos) break;
        begin = end + 1;
    }
    return parts;
}

bool SameContract(const IBContractLite& left, const IBContractLite& right)
{
    return left.symbol == right.symbol && left.currency == right.currency &&
        left.secType == right.secType && left.exchange == right.exchange &&
        left.primaryExchange == right.primaryExchange &&
        left.lastTradeDateOrContractMonth == right.lastTradeDateOrContractMonth &&
        left.right == right.right && left.strike == right.strike &&
        left.multiplier == right.multiplier &&
        left.tradingClass == right.tradingClass &&
        left.localSymbol == right.localSymbol;
}

std::uint64_t NowMs()
{
    return static_cast<std::uint64_t>(std::chrono::duration_cast<std::chrono::milliseconds>(
        std::chrono::system_clock::now().time_since_epoch()).count());
}

std::set<std::string> ExpectedCapabilities(bool paper)
{
    std::set<std::string> capabilities;
    capabilities.insert("system.read");
    capabilities.insert("events.read");
    capabilities.insert("market.read");
    capabilities.insert("account.read");
    capabilities.insert("portfolio.read");
    capabilities.insert("orders.read");
    capabilities.insert("risk.read");
    if (paper)
    {
        capabilities.insert("intent.preview");
        capabilities.insert("intent.apply");
        capabilities.insert("trade.cancel");
        capabilities.insert("trade.flatten");
    }
    return capabilities;
}

bool SameCapabilities(const std::unordered_set<std::string>& actual, bool paper)
{
    const std::set<std::string> expected = ExpectedCapabilities(paper);
    return actual.size() == expected.size() &&
        std::all_of(expected.begin(), expected.end(), [&](const std::string& capability) {
            return actual.find(capability) != actual.end();
        });
}
}

bool ToolGatewaySessionPolicy::FromEnvironment(
    const ExecutionGatewayRuntimeConfig& execution,
    const AgentOsRuntimeConfig& agentOs,
    ToolGatewaySessionPolicy& policy,
    std::string& reason)
{
    static const char* keys[] = {
        "HEPTA_TOOL_ACCOUNT",
        "HEPTA_TOOL_AGENT_ID",
        "HEPTA_EXECUTION_DOMAIN_ID",
        "HEPTA_TOOL_SESSION_TEMPLATES",
        "HEPTA_TOOL_CONTRACT_BINDINGS",
        "HEPTA_TOOL_MAX_ORDER_QTY",
        "HEPTA_TOOL_MAX_TRADE_CALLS_PER_MIN",
        "HEPTA_TOOL_DECISION_LEASE_TTL_MS"
    };
    std::map<std::string, std::string> values;
    for (std::size_t i = 0; i < sizeof(keys) / sizeof(keys[0]); ++i)
    {
        const char* value = std::getenv(keys[i]);
        if (value != nullptr) values[keys[i]] = value;
    }
    return FromValues(values, execution, agentOs, policy, reason);
}

bool ToolGatewaySessionPolicy::FromValues(
    const std::map<std::string, std::string>& values,
    const ExecutionGatewayRuntimeConfig& execution,
    const AgentOsRuntimeConfig& agentOs,
    ToolGatewaySessionPolicy& policy,
    std::string& reason)
{
    policy = ToolGatewaySessionPolicy();
    if (!agentOs.Validate(reason)) return false;
    if (!execution.Enabled())
    {
        reason = "TOOL_GATEWAY_REMOTE_EXECUTION_REQUIRED";
        return false;
    }
    if (!agentOs.ToolServerEnabled() || !agentOs.SupervisorEnabled() ||
        agentOs.toolSocket == agentOs.supervisorSocket)
    {
        reason = "TOOL_GATEWAY_OS_SOCKET_CONFIGURATION_INVALID";
        return false;
    }

    policy.m_agentId = Read(values, "HEPTA_TOOL_AGENT_ID");
    if (!CanonicalText(policy.m_agentId, 32))
    {
        reason = "TOOL_GATEWAY_AGENT_ID_INVALID";
        return false;
    }
    policy.m_account = Read(values, "HEPTA_TOOL_ACCOUNT");
    if (!CanonicalText(policy.m_account, 64))
    {
        reason = "TOOL_GATEWAY_ACCOUNT_INVALID";
        return false;
    }
    policy.m_agentUid = agentOs.agentUid;
    policy.m_maxSessionTtlMs = agentOs.supervisorMaxTtlMs;
    policy.m_venue = execution.mode == ExecutionGatewayMode::Paper ? "IB" : "SIMULATOR";
    policy.m_executionDomain = Read(values, "HEPTA_EXECUTION_DOMAIN_ID");
    if (policy.m_executionDomain.empty())
        policy.m_executionDomain = execution.mode == ExecutionGatewayMode::Paper ?
            "PAPER" : "SIM:" + policy.m_account;
    if ((execution.mode == ExecutionGatewayMode::Paper &&
         (!PaperDomain(policy.m_executionDomain) ||
          !PaperDomainMatchesAgent(policy.m_executionDomain,
                                   policy.m_agentId))) ||
        (execution.mode == ExecutionGatewayMode::Simulator &&
         (policy.m_executionDomain.size() <= 4 ||
          policy.m_executionDomain.compare(0, 4, "SIM:") != 0)) ||
        !CanonicalText(policy.m_executionDomain, 128))
    {
        reason = "TOOL_GATEWAY_EXECUTION_DOMAIN_INVALID";
        return false;
    }

    const std::string templates = Read(values, "HEPTA_TOOL_SESSION_TEMPLATES").empty() ?
        "watch" : Read(values, "HEPTA_TOOL_SESSION_TEMPLATES");
    if (templates != "watch" && templates != "watch,paper")
    {
        reason = "TOOL_GATEWAY_SESSION_TEMPLATES_INVALID";
        return false;
    }
    policy.m_paperEnabled = templates == "watch,paper";
    if (policy.m_paperEnabled != execution.mutationToolsEnabled)
    {
        reason = "TOOL_GATEWAY_PAPER_TEMPLATE_FLAG_MISMATCH";
        return false;
    }

    const std::string leaseTtl = Read(values, "HEPTA_TOOL_DECISION_LEASE_TTL_MS");
    std::uint64_t parsed = 5000;
    if ((!leaseTtl.empty() && !ParseUnsigned(leaseTtl, 5000, 60000, parsed)) ||
        parsed > std::numeric_limits<std::uint32_t>::max())
    {
        reason = "TOOL_GATEWAY_DECISION_LEASE_TTL_INVALID";
        return false;
    }
    policy.m_decisionLeaseTtlMs = static_cast<std::uint32_t>(parsed);

    if (policy.m_paperEnabled)
    {
        if (!ParsePositiveDouble(Read(values, "HEPTA_TOOL_MAX_ORDER_QTY"),
                                 policy.m_maxOrderQuantity) ||
            policy.m_maxOrderQuantity > 25000.0 ||
            !ParseUnsigned(Read(values, "HEPTA_TOOL_MAX_TRADE_CALLS_PER_MIN"),
                           1, 100000, parsed))
        {
            reason = "TOOL_GATEWAY_TRADE_LIMITS_INVALID";
            return false;
        }
        policy.m_maxTradeCallsPerMinute = static_cast<std::uint32_t>(parsed);

    }
    const std::string bindings = Read(values, "HEPTA_TOOL_CONTRACT_BINDINGS");
    if (policy.m_paperEnabled && bindings.empty())
    {
        reason = "TOOL_GATEWAY_CONTRACT_BINDINGS_REQUIRED";
        return false;
    }
    if (!bindings.empty())
    {
        const std::vector<std::string> records = Split(bindings, ';');
        for (std::size_t i = 0; i < records.size(); ++i)
        {
            const std::vector<std::string> fields = Split(records[i], '|');
            if (fields.size() != 5 || !CanonicalInstrument(fields[0]) ||
                !CanonicalText(fields[1], 64) || !CanonicalText(fields[2], 16) ||
                !CanonicalText(fields[3], 32) || !CanonicalText(fields[4], 16) ||
                fields[2] != "CASH" || policy.m_contracts.find(fields[0]) != policy.m_contracts.end())
            {
                reason = "TOOL_GATEWAY_CONTRACT_BINDING_INVALID";
                return false;
            }
            IBContractLite contract;
            contract.symbol = fields[1];
            contract.secType = fields[2];
            contract.exchange = fields[3];
            contract.currency = fields[4];
            policy.m_contracts[fields[0]] = contract;
        }
    }
    reason.clear();
    return true;
}

bool ToolGatewaySessionPolicy::Resolve(
    const SessionSupervisorRequest& request,
    TradingToolHostSessionBinding& binding,
    std::string& reason) const
{
    if (request.operation != SessionSupervisorOperation::Provision ||
        request.peerUid != m_agentUid ||
        request.agentId != m_agentId)
    {
        reason = request.peerUid != m_agentUid ?
            "SUPERVISOR_AGENT_UID_NOT_ALLOWLISTED" :
            "SUPERVISOR_AGENT_ID_NOT_BOUND";
        return false;
    }
    if (request.ttlMs < 60000 || request.ttlMs > m_maxSessionTtlMs)
    {
        reason = "SUPERVISOR_TTL_OUT_OF_RANGE";
        return false;
    }
    const bool paper = request.templateId == "paper";
    if (request.templateId != "watch" && !paper)
    {
        reason = "SUPERVISOR_TEMPLATE_NOT_ALLOWLISTED";
        return false;
    }
    if (paper && !m_paperEnabled)
    {
        reason = "SUPERVISOR_PAPER_TEMPLATE_DISABLED";
        return false;
    }

    binding = TradingToolHostSessionBinding();
    binding.token = request.token;
    binding.peerUid = request.peerUid;
    binding.session.executionContext.agentId = request.agentId;
    binding.session.executionContext.sessionId = request.sessionId;
    binding.session.executionContext.account = m_account;
    binding.session.executionContext.venue = m_venue;
    binding.session.executionContext.strategy = "agent-native";
    binding.session.environment = paper ? "PAPER" : "WATCH";
    const std::set<std::string> capabilities = ExpectedCapabilities(paper);
    binding.session.capabilities.insert(capabilities.begin(), capabilities.end());
    binding.expiresAtMs = NowMs() + request.ttlMs;
    binding.executionDomain = m_executionDomain;
    binding.decisionLeaseTtlMs = m_decisionLeaseTtlMs;
    for (std::unordered_map<std::string, IBContractLite>::const_iterator it =
         m_contracts.begin(); it != m_contracts.end(); ++it)
    {
        binding.allowedInstruments.insert(it->first);
        binding.instrumentContracts[it->first] = it->second;
    }
    if (paper)
    {
        binding.maxOrderQuantity = m_maxOrderQuantity;
        binding.maxTradeCallsPerMinute = m_maxTradeCallsPerMinute;
    }
    reason.clear();
    return true;
}

bool ToolGatewaySessionPolicy::Authorize(
    const std::string& issuer,
    const TradingToolHostSessionBinding& binding,
    std::string& reason) const
{
    if (issuer != "hepta.os.bootstrap")
    {
        reason = "TOOL_GATEWAY_SESSION_ISSUER_REJECTED";
        return false;
    }
    // Revoke authorization carries only the opaque token. Peer UID
    // authentication already selected the issuer; the host then performs the
    // exact token/generation lookup before mutation.
    if (binding.session.executionContext.account.empty())
    {
        if (binding.token.empty())
        {
            reason = "TOOL_GATEWAY_REVOKE_TOKEN_REQUIRED";
            return false;
        }
        reason.clear();
        return true;
    }
    const bool paper = binding.session.environment == "PAPER";
    if (binding.peerUid != m_agentUid ||
        binding.session.executionContext.agentId != m_agentId ||
        binding.session.executionContext.account != m_account ||
        binding.session.executionContext.venue != m_venue ||
        binding.executionDomain != m_executionDomain ||
        (binding.session.environment != "WATCH" && !paper) ||
        (paper && !m_paperEnabled) || !SameCapabilities(binding.session.capabilities, paper) ||
        binding.expiresAtMs <= NowMs() || binding.decisionLeaseTtlMs != m_decisionLeaseTtlMs)
    {
        reason = "TOOL_GATEWAY_SESSION_POLICY_REJECTED";
        return false;
    }
    if (!paper)
    {
        if (binding.allowedInstruments.size() != m_contracts.size() ||
            binding.instrumentContracts.size() != m_contracts.size() ||
            binding.maxOrderQuantity != 0.0 || binding.maxTradeCallsPerMinute != 0)
        {
            reason = "TOOL_GATEWAY_WATCH_SCOPE_INVALID";
            return false;
        }
    }
    else if (binding.allowedInstruments.size() != m_contracts.size() ||
        binding.instrumentContracts.size() != m_contracts.size() ||
        binding.maxOrderQuantity != m_maxOrderQuantity ||
        binding.maxTradeCallsPerMinute != m_maxTradeCallsPerMinute)
    {
        reason = "TOOL_GATEWAY_PAPER_SCOPE_INVALID";
        return false;
    }
    for (std::unordered_map<std::string, IBContractLite>::const_iterator it =
         m_contracts.begin(); it != m_contracts.end(); ++it)
    {
        const std::unordered_map<std::string, IBContractLite>::const_iterator bound =
            binding.instrumentContracts.find(it->first);
        if (binding.allowedInstruments.find(it->first) == binding.allowedInstruments.end() ||
            bound == binding.instrumentContracts.end() || !SameContract(bound->second, it->second))
        {
            reason = paper ? "TOOL_GATEWAY_PAPER_SCOPE_INVALID" :
                "TOOL_GATEWAY_WATCH_SCOPE_INVALID";
            return false;
        }
    }
    reason.clear();
    return true;
}

bool ToolGatewaySessionPolicy::PaperEnabled() const
{
    return m_paperEnabled;
}

const std::string& ToolGatewaySessionPolicy::Account() const
{
    return m_account;
}

const std::string& ToolGatewaySessionPolicy::Venue() const
{
    return m_venue;
}

const std::string& ToolGatewaySessionPolicy::ExecutionDomain() const
{
    return m_executionDomain;
}
