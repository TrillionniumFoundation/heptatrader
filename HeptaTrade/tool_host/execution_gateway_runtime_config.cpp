#include "execution_gateway_runtime_config.h"

#include <algorithm>
#include <cerrno>
#include <cstdlib>
#include <limits>

namespace
{
std::string Read(const std::map<std::string, std::string>& values, const char* key)
{
    const std::map<std::string, std::string>::const_iterator it = values.find(key);
    return it == values.end() ? std::string() : it->second;
}

bool ParseUnsigned(const std::string& value, std::uint32_t& out)
{
    if (value.empty() || value[0] == '-') return false;
    char* end = nullptr;
    errno = 0;
    const unsigned long parsed = std::strtoul(value.c_str(), &end, 10);
    if (errno != 0 || end == value.c_str() || *end != '\0' ||
        parsed > std::numeric_limits<std::uint32_t>::max()) return false;
    out = static_cast<std::uint32_t>(parsed);
    return true;
}

bool StrictInt(const std::string& value, int fallback, int minimum, int maximum, int& out)
{
    if (value.empty())
    {
        out = fallback;
        return true;
    }
    char* end = nullptr;
    errno = 0;
    const long parsed = std::strtol(value.c_str(), &end, 10);
    if (errno != 0 || end == value.c_str() || *end != '\0' ||
        parsed < minimum || parsed > maximum) return false;
    out = static_cast<int>(parsed);
    return true;
}

bool StrictBool(const std::string& value, bool fallback, bool& out)
{
    if (value.empty())
    {
        out = fallback;
        return true;
    }
    if (value == "1" || value == "true" || value == "TRUE")
    {
        out = true;
        return true;
    }
    if (value == "0" || value == "false" || value == "FALSE")
    {
        out = false;
        return true;
    }
    return false;
}

bool AbsoluteUnixPath(const std::string& path)
{
    return path.size() > 1 && path[0] == '/' && path.size() < 108;
}
}

bool ExecutionGatewayRuntimeConfig::Enabled() const
{
    return mode == ExecutionGatewayMode::Simulator || mode == ExecutionGatewayMode::Paper;
}

const char* ExecutionGatewayRuntimeConfig::ModeName() const
{
    if (mode == ExecutionGatewayMode::Simulator) return "SIMULATOR";
    if (mode == ExecutionGatewayMode::Paper) return "PAPER";
    return "LOCAL";
}

bool ExecutionGatewayRuntimeConfig::Validate(std::string& reason) const
{
    if (mode == ExecutionGatewayMode::Invalid)
    {
        reason = "EXECUTION_GATEWAY_MODE_UNSUPPORTED";
        return false;
    }
    if (!flagsValid)
    {
        reason = "EXECUTION_GATEWAY_FLAG_INVALID";
        return false;
    }
    if (externalP1CanaryLimitDay && mode != ExecutionGatewayMode::Paper)
    {
        reason = "EXECUTION_GATEWAY_EXTERNAL_P1_CANARY_REQUIRES_PAPER";
        return false;
    }
    if (!Enabled())
    {
        if (mutationToolsEnabled)
        {
            reason = "EXECUTION_GATEWAY_REMOTE_REQUIRED_FOR_MUTATION_TOOLS";
            return false;
        }
        if (!executionSocket.empty() || !eventSocket.empty() || executionServiceUidConfigured)
        {
            reason = "EXECUTION_GATEWAY_DISABLED_WITH_REMOTE_CONFIGURATION";
            return false;
        }
        reason.clear();
        return true;
    }
    if (mode != ExecutionGatewayMode::Simulator && mode != ExecutionGatewayMode::Paper)
    {
        reason = "EXECUTION_GATEWAY_MODE_UNSUPPORTED";
        return false;
    }
    if (!AbsoluteUnixPath(executionSocket) || !AbsoluteUnixPath(eventSocket))
    {
        reason = "EXECUTION_GATEWAY_SOCKET_PATH_INVALID";
        return false;
    }
    if (executionSocket == eventSocket)
    {
        reason = "EXECUTION_GATEWAY_SOCKETS_MUST_BE_DISTINCT";
        return false;
    }
    if (!executionServiceUidConfigured)
    {
        reason = "EXECUTION_GATEWAY_SERVICE_UID_REQUIRED";
        return false;
    }
    // An Agent operation may perform multiple authenticated Unix exchanges
    // (mutation identity, event identity, then the command). Keep each
    // exchange tightly bounded so discovery timeouts remain truthful.
    if (!limitsValid || ioTimeoutMs < 100 || ioTimeoutMs > 2500 ||
        maxResponseBytes < 1024 || maxResponseBytes > 1048576)
    {
        reason = "EXECUTION_GATEWAY_LIMIT_INVALID";
        return false;
    }
    reason.clear();
    return true;
}

ExecutionGatewayRuntimeConfig ExecutionGatewayRuntimeConfig::FromEnvironment()
{
    static const char* keys[] = {
        "HEPTA_EXECUTION_REMOTE_MODE", "HEPTA_EXECUTION_SOCKET",
        "HEPTA_EXECUTION_EVENT_SOCKET", "HEPTA_EXECUTION_SERVICE_UID",
        "HEPTA_EXECUTION_IO_TIMEOUT_MS", "HEPTA_EXECUTION_MAX_RESPONSE_BYTES",
        "HEPTA_TOOL_ALLOW_TRADE",
        "HEPTA_EXECUTION_EXTERNAL_P1_CANARY_LMT_DAY"
    };
    std::map<std::string, std::string> values;
    for (std::size_t i = 0; i < sizeof(keys) / sizeof(keys[0]); ++i)
    {
        const char* value = std::getenv(keys[i]);
        if (value != nullptr) values[keys[i]] = value;
    }
    return FromValues(values);
}

ExecutionGatewayRuntimeConfig ExecutionGatewayRuntimeConfig::FromValues(
    const std::map<std::string, std::string>& values)
{
    ExecutionGatewayRuntimeConfig config;
    const std::string mode = Read(values, "HEPTA_EXECUTION_REMOTE_MODE");
    if (mode == "SIMULATOR") config.mode = ExecutionGatewayMode::Simulator;
    else if (mode == "PAPER") config.mode = ExecutionGatewayMode::Paper;
    else if (mode.empty() || mode == "DISABLED") config.mode = ExecutionGatewayMode::Disabled;
    else config.mode = ExecutionGatewayMode::Invalid;
    config.executionSocket = Read(values, "HEPTA_EXECUTION_SOCKET");
    config.eventSocket = Read(values, "HEPTA_EXECUTION_EVENT_SOCKET");
    const std::string uid = Read(values, "HEPTA_EXECUTION_SERVICE_UID");
    config.executionServiceUidConfigured = ParseUnsigned(uid, config.executionServiceUid);
    int responseBytes = 32768;
    config.limitsValid = StrictInt(Read(values, "HEPTA_EXECUTION_IO_TIMEOUT_MS"),
        1000, 100, 2500, config.ioTimeoutMs) &&
        StrictInt(Read(values, "HEPTA_EXECUTION_MAX_RESPONSE_BYTES"),
            32768, 1024, 1048576, responseBytes);
    config.flagsValid = StrictBool(Read(values, "HEPTA_TOOL_ALLOW_TRADE"),
        false, config.mutationToolsEnabled);
    const std::map<std::string, std::string>::const_iterator external =
        values.find("HEPTA_EXECUTION_EXTERNAL_P1_CANARY_LMT_DAY");
    if (external != values.end())
    {
        config.externalP1CanaryLimitDay = external->second == "1";
        config.flagsValid = config.flagsValid &&
            config.externalP1CanaryLimitDay;
    }
    config.maxResponseBytes = static_cast<std::size_t>(responseBytes);
    return config;
}
