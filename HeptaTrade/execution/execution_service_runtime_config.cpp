#include "execution_service_runtime_config.h"
#include "../risk/deterministic_risk_policy.h"

#include <cerrno>
#include <cmath>
#include <cstdlib>
#include <limits>
#include <locale>
#include <sstream>

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

std::string ReadString(const std::map<std::string, std::string>& values, const char* key)
{
    const std::map<std::string, std::string>::const_iterator found = values.find(key);
    return found == values.end() ? std::string() : found->second;
}

bool ParseUnsigned(const std::string& value, std::uint64_t maximum, std::uint64_t& parsed)
{
    if (!CanonicalUnsignedInteger(value)) return false;
    char* end = nullptr;
    errno = 0;
    const unsigned long long number = std::strtoull(value.c_str(), &end, 10);
    if (errno != 0 || end == value.c_str() || *end != '\0' || number > maximum) return false;
    parsed = static_cast<std::uint64_t>(number);
    return true;
}

bool ParseBool01(const std::string& value, bool& parsed)
{
    if (value == "0") { parsed = false; return true; }
    if (value == "1") { parsed = true; return true; }
    return false;
}

bool ParsePositiveDouble(const std::string& value, double& parsed)
{
    if (!CanonicalFloating(value)) return false;
    std::istringstream input(value);
    input.imbue(std::locale::classic());
    input >> std::noskipws;
    double number = 0.0;
    input >> number;
    if (!input || !input.eof() || !std::isfinite(number) || number <= 0.0)
        return false;
    parsed = number;
    return true;
}

bool CanonicalAgentId(const std::string& value)
{
    if (value.empty() || value.size() > 32 ||
        value[0] < 'a' || value[0] > 'z')
        return false;
    for (std::size_t i = 1; i < value.size(); ++i)
    {
        const unsigned char character =
            static_cast<unsigned char>(value[i]);
        const bool lower = character >= static_cast<unsigned char>('a') &&
            character <= static_cast<unsigned char>('z');
        const bool digit = character >= static_cast<unsigned char>('0') &&
            character <= static_cast<unsigned char>('9');
        if (!lower && !digit && character != '-')
            return false;
    }
    return true;
}

bool SafePrivateDirectoryPath(const std::string& path)
{
    if (path.size() < 2 || path[0] != '/' || path.find(':') != std::string::npos) return false;
    std::size_t offset = 1;
    while (offset <= path.size())
    {
        const std::size_t slash = path.find('/', offset);
        const std::string component = path.substr(
            offset, slash == std::string::npos ? slash : slash - offset);
        if (component == "." || component == "..") return false;
        if (slash == std::string::npos) break;
        offset = slash + 1;
    }
    return true;
}

std::string WithoutTrailingSlash(std::string path)
{
    while (path.size() > 1 && path[path.size() - 1] == '/') path.resize(path.size() - 1);
    return path;
}

bool MapActivatedFds(const std::string& names,
                     std::uint64_t count,
                     int& executionFd,
                     int& eventFd)
{
    executionFd = -1;
    eventFd = -1;
    std::size_t offset = 0;
    std::uint64_t index = 0;
    while (offset <= names.size())
    {
        const std::size_t colon = names.find(':', offset);
        const std::string name = names.substr(offset,
            colon == std::string::npos ? colon : colon - offset);
        if (name == ExecutionServiceRuntimeConfig::ActivatedSocketName() && executionFd < 0)
            executionFd = 3 + static_cast<int>(index);
        else if (name == ExecutionServiceRuntimeConfig::EventActivatedSocketName() && eventFd < 0)
            eventFd = 3 + static_cast<int>(index);
        else
            return false;
        ++index;
        if (colon == std::string::npos) break;
        offset = colon + 1;
    }
    return index == count && count == 2 && executionFd >= 3 && eventFd >= 3 &&
        executionFd != eventFd;
}
}

bool ExecutionServiceRuntimeConfig::Enabled() const
{
    return mode != ExecutionServiceRuntimeMode::Disabled;
}

const char* ExecutionServiceRuntimeConfig::ModeName(ExecutionServiceRuntimeMode value)
{
    switch (value)
    {
    case ExecutionServiceRuntimeMode::Disabled: return "DISABLED";
    case ExecutionServiceRuntimeMode::Simulator: return "SIMULATOR";
    }
    return "UNKNOWN";
}

const char* ExecutionServiceRuntimeConfig::ActivatedSocketName()
{
    return "execution";
}

const char* ExecutionServiceRuntimeConfig::EventActivatedSocketName()
{
    return "events";
}

const char* ExecutionServiceRuntimeConfig::FenceCredentialName()
{
    return "hepta-execution-fence";
}

bool ExecutionServiceRuntimeConfig::Validate(std::string& reason) const
{
    if (!Enabled())
    {
        reason.clear();
        return true;
    }
    if (mode != ExecutionServiceRuntimeMode::Simulator)
    {
        reason = "EXECUTION_RUNTIME_MODE_UNSUPPORTED";
        return false;
    }
    if (listenFd < 0 || eventListenFd < 0 || listenFd == eventListenFd)
    {
        reason = "EXECUTION_ACTIVATED_SOCKETS_REQUIRED";
        return false;
    }
    if (allowedGatewayUids.size() != 1)
    {
        reason = "EXECUTION_GATEWAY_UID_REQUIRED";
        return false;
    }
    if (*allowedGatewayUids.begin() == 0)
    {
        reason = "EXECUTION_GATEWAY_UID_NOT_ISOLATED";
        return false;
    }
    if (!CanonicalAgentId(gatewayContextBinding.agentId) ||
        gatewayContextBinding.account != "SIM" ||
        gatewayContextBinding.venue != "SIMULATOR" ||
        gatewayContextBinding.executionDomain !=
            "SIM:" + gatewayContextBinding.agentId)
    {
        reason = "EXECUTION_GATEWAY_CONTEXT_BINDING_INVALID";
        return false;
    }
    if (!SafePrivateDirectoryPath(stateDirectory) ||
        journalPath != WithoutTrailingSlash(stateDirectory) + "/oms-journal.jsonl")
    {
        reason = "EXECUTION_STATE_DIRECTORY_INVALID";
        return false;
    }
    const std::size_t credentialSlash = fenceCredentialPath.find_last_of('/');
    if (credentialSlash == std::string::npos ||
        fenceCredentialPath.substr(credentialSlash + 1) != FenceCredentialName() ||
        !SafePrivateDirectoryPath(fenceCredentialPath.substr(0, credentialSlash)))
    {
        reason = "EXECUTION_FENCE_CREDENTIAL_PATH_INVALID";
        return false;
    }
    if (maxRequestBytes < 1024 || maxRequestBytes > 32768)
    {
        reason = "EXECUTION_MAX_REQUEST_BYTES_INVALID";
        return false;
    }
    if (ioTimeoutMs < 1 || ioTimeoutMs > 30000)
    {
        reason = "EXECUTION_IO_TIMEOUT_INVALID";
        return false;
    }
    if (simulatorQuoteTtlMs < 10 || simulatorQuoteTtlMs > 600000)
    {
        reason = "EXECUTION_SIMULATOR_QUOTE_TTL_INVALID";
        return false;
    }
    if (simulatorQuoteRefreshIntervalMs < 1 ||
        simulatorQuoteRefreshIntervalMs >
            simulatorQuoteTtlMs / 2)
    {
        reason = "EXECUTION_SIMULATOR_QUOTE_REFRESH_INTERVAL_INVALID";
        return false;
    }
    DeterministicRiskLimits riskLimits;
    riskLimits.orderSubmissionEnabled = simulatorOrderSubmissionEnabled;
    riskLimits.globalKillSwitch = simulatorGlobalKillSwitch;
    riskLimits.flattenOnly = simulatorFlattenOnly;
    riskLimits.maxOrderQuantity = simulatorMaxOrderQuantity;
    riskLimits.maxOrderNotional = simulatorMaxOrderNotional;
    riskLimits.maxOrdersPerMinute = simulatorMaxOrdersPerMinute;
    riskLimits.maxActiveOrders = simulatorMaxActiveOrders;
    riskLimits.maxGrossPosition = simulatorMaxGrossPosition;
    riskLimits.maxPriceDeviationBps = simulatorMaxPriceDeviationBps;
    if (!DeterministicRiskPolicy::ValidateLimits(riskLimits, reason))
        return false;
    reason.clear();
    return true;
}

bool ExecutionServiceRuntimeConfig::FromEnvironment(
    int currentPid,
    ExecutionServiceRuntimeConfig& config,
    std::string& reason)
{
    static const char* keys[] = {
        "HEPTA_EXECUTION_SERVICE_MODE",
        "HEPTA_EXECUTION_GATEWAY_UID",
        "HEPTA_EXECUTION_GATEWAY_AGENT_ID",
        "HEPTA_EXECUTION_MAX_REQUEST_BYTES",
        "HEPTA_EXECUTION_IO_TIMEOUT_MS",
        "HEPTA_SIM_ORDER_SUBMISSION_ENABLED",
        "HEPTA_SIM_GLOBAL_KILL_SWITCH",
        "HEPTA_SIM_FLATTEN_ONLY",
        "HEPTA_SIM_MAX_ORDER_QTY",
        "HEPTA_SIM_MAX_ORDER_NOTIONAL",
        "HEPTA_SIM_MAX_ORDERS_PER_MINUTE",
        "HEPTA_SIM_MAX_ACTIVE_ORDERS",
        "HEPTA_SIM_MAX_GROSS_POSITION",
        "HEPTA_SIM_MAX_PRICE_DEVIATION_BPS",
        "LISTEN_PID",
        "LISTEN_FDS",
        "LISTEN_FDNAMES",
        "STATE_DIRECTORY",
        "CREDENTIALS_DIRECTORY"
    };
    std::map<std::string, std::string> values;
    for (std::size_t i = 0; i < sizeof(keys) / sizeof(keys[0]); ++i)
    {
        const char* value = std::getenv(keys[i]);
        if (value != nullptr) values[keys[i]] = value;
    }
    return FromValues(values, currentPid, config, reason);
}

bool ExecutionServiceRuntimeConfig::FromValues(
    const std::map<std::string, std::string>& values,
    int currentPid,
    ExecutionServiceRuntimeConfig& config,
    std::string& reason)
{
    config = ExecutionServiceRuntimeConfig();
    const std::string mode = ReadString(values, "HEPTA_EXECUTION_SERVICE_MODE");
    if (mode.empty() || mode == "DISABLED")
    {
        reason.clear();
        return true;
    }
    if (mode != "SIMULATOR")
    {
        reason = "EXECUTION_RUNTIME_MODE_UNSUPPORTED";
        return false;
    }
    config.mode = ExecutionServiceRuntimeMode::Simulator;

    std::uint64_t listenPid = 0;
    std::uint64_t listenFds = 0;
    if (currentPid <= 0 ||
        !ParseUnsigned(ReadString(values, "LISTEN_PID"),
            static_cast<std::uint64_t>(std::numeric_limits<int>::max()), listenPid) ||
        listenPid != static_cast<std::uint64_t>(currentPid) ||
        !ParseUnsigned(ReadString(values, "LISTEN_FDS"), 1024, listenFds) ||
        !MapActivatedFds(ReadString(values, "LISTEN_FDNAMES"), listenFds,
                         config.listenFd, config.eventListenFd))
    {
        reason = "EXECUTION_SYSTEMD_SOCKET_ACTIVATION_INVALID";
        return false;
    }
    std::uint64_t gatewayUid = 0;
    if (!ParseUnsigned(ReadString(values, "HEPTA_EXECUTION_GATEWAY_UID"),
        std::numeric_limits<std::uint32_t>::max(), gatewayUid))
    {
        reason = "EXECUTION_GATEWAY_UID_INVALID";
        return false;
    }
    config.allowedGatewayUids.insert(static_cast<std::uint32_t>(gatewayUid));
    config.gatewayContextBinding.agentId =
        ReadString(values, "HEPTA_EXECUTION_GATEWAY_AGENT_ID");
    config.gatewayContextBinding.account = "SIM";
    config.gatewayContextBinding.venue = "SIMULATOR";
    config.gatewayContextBinding.executionDomain =
        "SIM:" + config.gatewayContextBinding.agentId;

    config.stateDirectory = WithoutTrailingSlash(ReadString(values, "STATE_DIRECTORY"));
    const std::string credentialsDirectory = WithoutTrailingSlash(
        ReadString(values, "CREDENTIALS_DIRECTORY"));
    if (!SafePrivateDirectoryPath(config.stateDirectory) ||
        !SafePrivateDirectoryPath(credentialsDirectory))
    {
        reason = "EXECUTION_PRIVATE_DIRECTORY_INVALID";
        return false;
    }
    config.journalPath = config.stateDirectory + "/oms-journal.jsonl";
    config.fenceCredentialPath = credentialsDirectory + "/" + FenceCredentialName();

    const std::string maxRequest = ReadString(values, "HEPTA_EXECUTION_MAX_REQUEST_BYTES");
    if (!maxRequest.empty())
    {
        std::uint64_t parsed = 0;
        if (!ParseUnsigned(maxRequest, 32768, parsed) || parsed < 1024)
        {
            reason = "EXECUTION_MAX_REQUEST_BYTES_INVALID";
            return false;
        }
        config.maxRequestBytes = static_cast<std::size_t>(parsed);
    }
    const std::string ioTimeout = ReadString(values, "HEPTA_EXECUTION_IO_TIMEOUT_MS");
    if (!ioTimeout.empty())
    {
        std::uint64_t parsed = 0;
        if (!ParseUnsigned(ioTimeout, 30000, parsed) || parsed < 1)
        {
            reason = "EXECUTION_IO_TIMEOUT_INVALID";
            return false;
        }
        config.ioTimeoutMs = static_cast<int>(parsed);
    }
    const std::string orderEnabled = ReadString(values,
        "HEPTA_SIM_ORDER_SUBMISSION_ENABLED");
    const std::string killSwitch = ReadString(values,
        "HEPTA_SIM_GLOBAL_KILL_SWITCH");
    const std::string flattenOnly = ReadString(values,
        "HEPTA_SIM_FLATTEN_ONLY");
    if ((!orderEnabled.empty() && !ParseBool01(orderEnabled,
            config.simulatorOrderSubmissionEnabled)) ||
        (!killSwitch.empty() && !ParseBool01(killSwitch,
            config.simulatorGlobalKillSwitch)) ||
        (!flattenOnly.empty() && !ParseBool01(flattenOnly,
            config.simulatorFlattenOnly)))
    {
        reason = "EXECUTION_SIMULATOR_RISK_BOOLEAN_INVALID";
        return false;
    }
    const std::string maxQty = ReadString(values, "HEPTA_SIM_MAX_ORDER_QTY");
    const std::string maxNotional = ReadString(values,
        "HEPTA_SIM_MAX_ORDER_NOTIONAL");
    const std::string maxGross = ReadString(values,
        "HEPTA_SIM_MAX_GROSS_POSITION");
    const std::string maxDeviation = ReadString(values,
        "HEPTA_SIM_MAX_PRICE_DEVIATION_BPS");
    if ((!maxQty.empty() && !ParsePositiveDouble(maxQty,
            config.simulatorMaxOrderQuantity)) ||
        (!maxNotional.empty() && !ParsePositiveDouble(maxNotional,
            config.simulatorMaxOrderNotional)) ||
        (!maxGross.empty() && !ParsePositiveDouble(maxGross,
            config.simulatorMaxGrossPosition)) ||
        (!maxDeviation.empty() && !ParsePositiveDouble(maxDeviation,
            config.simulatorMaxPriceDeviationBps)))
    {
        reason = "EXECUTION_SIMULATOR_RISK_DECIMAL_INVALID";
        return false;
    }
    std::uint64_t riskInteger = 0;
    const std::string maxRate = ReadString(values,
        "HEPTA_SIM_MAX_ORDERS_PER_MINUTE");
    if (!maxRate.empty())
    {
        if (!ParseUnsigned(maxRate, 1000000, riskInteger) || riskInteger == 0)
        { reason = "EXECUTION_SIMULATOR_RISK_RATE_INVALID"; return false; }
        config.simulatorMaxOrdersPerMinute =
            static_cast<std::size_t>(riskInteger);
    }
    const std::string maxActive = ReadString(values,
        "HEPTA_SIM_MAX_ACTIVE_ORDERS");
    if (!maxActive.empty())
    {
        if (!ParseUnsigned(maxActive, 1000000, riskInteger) || riskInteger == 0)
        { reason = "EXECUTION_SIMULATOR_RISK_ACTIVE_INVALID"; return false; }
        config.simulatorMaxActiveOrders =
            static_cast<std::size_t>(riskInteger);
    }
    return config.Validate(reason);
}
