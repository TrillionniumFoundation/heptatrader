#include "ib_paper_execution_runtime_config.h"

#include <cerrno>
#include <cstdlib>
#include <limits>
#include <sys/stat.h>
#include <unistd.h>
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

std::string ReadString(const std::map<std::string, std::string>& values,
                       const char* key)
{
    const std::map<std::string, std::string>::const_iterator found = values.find(key);
    return found == values.end() ? std::string() : found->second;
}

bool ParseUnsigned(const std::string& value, std::uint64_t maximum,
                   std::uint64_t& parsed)
{
    if (!CanonicalUnsignedInteger(value)) return false;
    char* end = nullptr;
    errno = 0;
    const unsigned long long number = std::strtoull(value.c_str(), &end, 10);
    if (errno != 0 || end == value.c_str() || *end != '\0' || number > maximum)
        return false;
    parsed = static_cast<std::uint64_t>(number);
    return true;
}

bool CanonicalText(const std::string& value, std::size_t maximum)
{
    if (value.empty() || value.size() > maximum) return false;
    for (std::size_t i = 0; i < value.size(); ++i)
    {
        const unsigned char character = static_cast<unsigned char>(value[i]);
        const bool alpha =
            (character >= static_cast<unsigned char>('a') &&
             character <= static_cast<unsigned char>('z')) ||
            (character >= static_cast<unsigned char>('A') &&
             character <= static_cast<unsigned char>('Z'));
        const bool digit = character >= static_cast<unsigned char>('0') &&
            character <= static_cast<unsigned char>('9');
        if (!(alpha || digit || character == '.' || character == '_' ||
              character == '-'))
            return false;
    }
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

bool CanonicalPaperDomain(const std::string& value)
{
    static const std::string prefix = "PAPER:";
    if (value == "PAPER") return true;
    return value.size() > prefix.size() &&
        value.compare(0, prefix.size(), prefix) == 0 &&
        CanonicalAgentId(value.substr(prefix.size()));
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

bool ExpectedControlDirectory(const std::string& executionDomain,
                              std::string& expected)
{
    expected = IbPaperExecutionProfileConfig::ControlDirectoryPath();
    if (executionDomain == "PAPER") return true;
    if (!CanonicalPaperDomain(executionDomain)) return false;
    expected.push_back('-');
    expected.append(executionDomain.substr(6));
    return true;
}

std::vector<std::string> Split(const std::string& value, char delimiter)
{
    std::vector<std::string> parts;
    std::size_t offset = 0;
    while (offset <= value.size())
    {
        const std::size_t found = value.find(delimiter, offset);
        parts.push_back(value.substr(
            offset, found == std::string::npos ? found : found - offset));
        if (found == std::string::npos) break;
        offset = found + 1;
    }
    return parts;
}

bool ParseQuoteContracts(const std::string& value,
                         std::map<std::string, InstrumentRef>& contracts)
{
    contracts.clear();
    if (value.empty()) return false;
    const std::vector<std::string> records = Split(value, ';');
    if (records.empty() || records.size() > 64) return false;
    for (std::size_t i = 0; i < records.size(); ++i)
    {
        const std::vector<std::string> fields = Split(records[i], '|');
        if (fields.size() != 5 || !CanonicalText(fields[0], 128) ||
            !CanonicalText(fields[1], 64) || fields[2] != "CASH" ||
            !CanonicalText(fields[3], 32) || !CanonicalText(fields[4], 16) ||
            fields[0] != fields[1] + "." + fields[4] ||
            contracts.find(fields[0]) != contracts.end())
            return false;
        InstrumentRef contract;
        contract.symbol = fields[1];
        contract.secType = fields[2];
        contract.exchange = fields[3];
        contract.currency = fields[4];
        contracts[fields[0]] = contract;
    }
    return true;
}

bool SafeAbsoluteDirectoryPath(const std::string& path)
{
    if (path.size() < 2 || path[0] != '/' || path.find(':') != std::string::npos)
        return false;
    std::size_t offset = 1;
    while (offset <= path.size())
    {
        const std::size_t slash = path.find('/', offset);
        const std::string component = path.substr(
            offset, slash == std::string::npos ? slash : slash - offset);
        if (component.empty() || component == "." || component == "..") return false;
        if (slash == std::string::npos) break;
        offset = slash + 1;
    }
    return true;
}

std::string ParentDirectory(const std::string& path)
{
    const std::size_t slash = path.find_last_of('/');
    if (slash == std::string::npos || slash == 0) return std::string();
    return path.substr(0, slash);
}

bool SameOrChildPath(const std::string& candidate, const std::string& parent)
{
    return candidate == parent ||
        (candidate.size() > parent.size() &&
         candidate.compare(0, parent.size(), parent) == 0 &&
         candidate[parent.size()] == '/');
}

bool MapActivatedFds(const std::string& names, std::uint64_t count,
                     int& executionFd, int& eventFd)
{
    executionFd = -1;
    eventFd = -1;
    std::size_t offset = 0;
    std::uint64_t index = 0;
    while (offset <= names.size())
    {
        const std::size_t colon = names.find(':', offset);
        const std::string name = names.substr(
            offset, colon == std::string::npos ? colon : colon - offset);
        if (name == IbPaperExecutionRuntimeConfig::ActivatedSocketName() &&
            executionFd < 0)
            executionFd = 3 + static_cast<int>(index);
        else if (name == IbPaperExecutionRuntimeConfig::EventActivatedSocketName() &&
                 eventFd < 0)
            eventFd = 3 + static_cast<int>(index);
        else
            return false;
        ++index;
        if (colon == std::string::npos) break;
        offset = colon + 1;
    }
    return count == 2 && index == count && executionFd >= 3 && eventFd >= 3 &&
        executionFd != eventFd;
}

bool HasDisabledResidualConfiguration(
    const std::map<std::string, std::string>& values)
{
    static const char* keys[] = {
        "HEPTA_IB_PAPER_ACCOUNT", "HEPTA_IB_PAPER_HOST",
        "HEPTA_IB_PAPER_PORT", "HEPTA_IB_PAPER_CLIENT_ID",
        "HEPTA_IB_PAPER_MAX_ORDER_QTY",
        "HEPTA_IB_PAPER_MAX_ORDER_NOTIONAL",
        "HEPTA_IB_PAPER_MAX_ORDERS_PER_MINUTE",
        "HEPTA_IB_PAPER_MAX_ACTIVE_ORDERS",
        "HEPTA_IB_PAPER_MAX_GROSS_POSITION",
        "HEPTA_EXECUTION_EXTERNAL_P1_CANARY_LMT_DAY",
        "HEPTA_EXECUTION_MAX_ORDER_NOTIONAL",
        "HEPTA_IB_PAPER_QUOTE_CONTRACTS",
        "HEPTA_IB_PAPER_PRIMARY_QUOTE_INSTRUMENT",
        "HEPTA_IB_PAPER_QUOTE_MAX_AGE_MS",
        "HEPTA_IB_PAPER_CONTROL_DIRECTORY",
        "HEPTA_IB_EXECUTION_GATEWAY_UID",
        "HEPTA_IB_EXECUTION_GATEWAY_AGENT_ID",
        "HEPTA_IB_EXECUTION_DOMAIN_ID",
        "HEPTA_IB_EXECUTION_MAX_REQUEST_BYTES",
        "HEPTA_IB_EXECUTION_IO_TIMEOUT_MS",
        "HEPTA_IB_EXECUTION_READINESS_TIMEOUT_MS",
        "HEPTA_IB_EXECUTION_RECONNECT_TIMEOUT_MS",
        "LISTEN_PID", "LISTEN_FDS", "LISTEN_FDNAMES",
        "STATE_DIRECTORY", "CREDENTIALS_DIRECTORY"
    };
    for (std::size_t i = 0; i < sizeof(keys) / sizeof(keys[0]); ++i)
    {
        if (!ReadString(values, keys[i]).empty()) return true;
    }
    return false;
}
}

bool IbPaperExecutionRuntimeConfig::Enabled() const
{
    return mode != IbPaperExecutionRuntimeMode::Disabled;
}

const char* IbPaperExecutionRuntimeConfig::ModeName(
    IbPaperExecutionRuntimeMode value)
{
    switch (value)
    {
    case IbPaperExecutionRuntimeMode::Disabled: return "DISABLED";
    case IbPaperExecutionRuntimeMode::Paper: return "PAPER";
    }
    return "UNKNOWN";
}

const char* IbPaperExecutionRuntimeConfig::ActivatedSocketName()
{
    return "execution";
}

const char* IbPaperExecutionRuntimeConfig::EventActivatedSocketName()
{
    return "events";
}

const char* IbPaperExecutionRuntimeConfig::FenceCredentialName()
{
    return "hepta-execution-fence";
}

const char* IbPaperExecutionRuntimeConfig::FxCashBaselineCredentialName()
{
    return "hepta-fx-cash-baseline";
}

bool IbPaperExecutionRuntimeConfig::Validate(std::string& reason) const
{
    if (!Enabled())
    {
        if (mode != IbPaperExecutionRuntimeMode::Disabled || profile.enabled ||
            !profile.account.empty() || !profile.host.empty() || profile.port != 0 ||
            profile.clientId != 0 || !profile.stateDirectory.empty() ||
            !profile.authorizationCredentialPath.empty() ||
            !profile.controlDirectory.empty() || profile.maxOrderQuantity != 0.0 ||
            profile.maxOrderNotional != 0.0 || profile.maxOrdersPerMinute != 0 ||
            profile.maxActiveOrders != 0 || profile.maxGrossPosition != 0.0 ||
            listenFd != -1 || eventListenFd != -1 || !allowedGatewayUids.empty() ||
            gatewayContextBinding.Complete() ||
            !stateDirectory.empty() || !journalPath.empty() ||
            !controlDirectory.empty() || !fenceCredentialPath.empty() ||
            !fxCashBaselineCredentialPath.empty() ||
            !authorizationCredentialPath.empty() || !quoteContracts.empty() ||
            !fxCashBaselines.empty() ||
            !primaryQuoteInstrument.empty() || quoteMaxAgeMs != 5000 ||
            maxRequestBytes != 32768 ||
            ioTimeoutMs != 3000 || readinessTimeoutMs != 10000 ||
            reconnectTimeoutMs != 180000)
        {
            reason = "IB_PAPER_RUNTIME_DISABLED_CONFIGURATION_PRESENT";
            return false;
        }
        reason.clear();
        return true;
    }
    if (mode != IbPaperExecutionRuntimeMode::Paper || !profile.enabled)
    {
        reason = "IB_PAPER_RUNTIME_MODE_UNSUPPORTED";
        return false;
    }
    if (listenFd < 0 || eventListenFd < 0 || listenFd == eventListenFd)
    {
        reason = "IB_PAPER_ACTIVATED_SOCKETS_REQUIRED";
        return false;
    }
    if (allowedGatewayUids.size() != 1)
    {
        reason = "IB_PAPER_GATEWAY_UID_REQUIRED";
        return false;
    }
    if (*allowedGatewayUids.begin() == 0)
    {
        reason = "IB_PAPER_GATEWAY_UID_NOT_ISOLATED";
        return false;
    }
    if (!CanonicalAgentId(gatewayContextBinding.agentId) ||
        gatewayContextBinding.account != profile.account ||
        gatewayContextBinding.venue != "IB" ||
        !CanonicalPaperDomain(gatewayContextBinding.executionDomain) ||
        !PaperDomainMatchesAgent(gatewayContextBinding.executionDomain,
                                 gatewayContextBinding.agentId))
    {
        reason = "IB_PAPER_GATEWAY_CONTEXT_BINDING_INVALID";
        return false;
    }
    if (quoteContracts.empty() || quoteContracts.size() > 64 ||
        primaryQuoteInstrument.empty() ||
        quoteContracts.find(primaryQuoteInstrument) == quoteContracts.end())
    {
        reason = "IB_PAPER_QUOTE_CONTRACTS_REQUIRED";
        return false;
    }
    for (std::map<std::string, InstrumentRef>::const_iterator it =
             quoteContracts.begin(); it != quoteContracts.end(); ++it)
    {
        const InstrumentRef& contract = it->second;
        if (!CanonicalText(it->first, 128) || !CanonicalText(contract.symbol, 64) ||
            contract.secType != "CASH" || !CanonicalText(contract.exchange, 32) ||
            !CanonicalText(contract.currency, 16) ||
            it->first != contract.symbol + "." + contract.currency ||
            !contract.primaryExchange.empty() ||
            !contract.lastTradeDateOrContractMonth.empty() ||
            !contract.right.empty() || contract.strike != 0.0 ||
            !contract.multiplier.empty() || !contract.tradingClass.empty() ||
            !contract.localSymbol.empty())
        {
            reason = "IB_PAPER_QUOTE_CONTRACT_INVALID";
            return false;
        }
    }
    if (quoteMaxAgeMs < 100 || quoteMaxAgeMs > 60000)
    {
        reason = "IB_PAPER_QUOTE_MAX_AGE_INVALID";
        return false;
    }
    if (profile.UsesExternalLimitDay() &&
        (quoteMaxAgeMs > 5000 ||
         quoteMaxAgeMs != profile.externalQuoteMaxAgeMs))
    {
        reason = "IB_PAPER_EXTERNAL_ORDER_MODE_LIMITS_INVALID";
        return false;
    }
    std::string profileReason;
    if (!profile.Validate(profileReason))
    {
        reason = profileReason;
        return false;
    }
    std::string expectedControlDirectory;
    if (!ExpectedControlDirectory(
            gatewayContextBinding.executionDomain,
            expectedControlDirectory) ||
        profile.controlDirectory != expectedControlDirectory)
    {
        reason =
            "IB_PAPER_CONTROL_DIRECTORY_DOMAIN_MISMATCH";
        return false;
    }
    if (stateDirectory != profile.stateDirectory ||
        controlDirectory != profile.controlDirectory ||
        authorizationCredentialPath != profile.authorizationCredentialPath ||
        !SafeAbsoluteDirectoryPath(stateDirectory) ||
        journalPath != stateDirectory + "/oms-journal.jsonl")
    {
        reason = "IB_PAPER_RUNTIME_STATE_PATHS_INVALID";
        return false;
    }
    struct stat directory;
    if (::lstat(stateDirectory.c_str(), &directory) != 0 ||
        !S_ISDIR(directory.st_mode) || directory.st_uid != ::geteuid() ||
        (directory.st_mode & 0777) != 0700)
    {
        reason = "IB_PAPER_RUNTIME_STATE_DIRECTORY_UNSAFE";
        return false;
    }
    const std::string credentialDirectory = ParentDirectory(fenceCredentialPath);
    struct stat credentialDirectoryMetadata;
    if (!SafeAbsoluteDirectoryPath(credentialDirectory) ||
        fenceCredentialPath != credentialDirectory + "/" + FenceCredentialName() ||
        fxCashBaselineCredentialPath != credentialDirectory + "/" +
            FxCashBaselineCredentialName() ||
        authorizationCredentialPath != credentialDirectory + "/" +
            IbPaperExecutionProfileConfig::AuthorizationCredentialName() ||
        SameOrChildPath(credentialDirectory, stateDirectory) ||
        SameOrChildPath(stateDirectory, credentialDirectory) ||
        SameOrChildPath(credentialDirectory, controlDirectory) ||
        SameOrChildPath(controlDirectory, credentialDirectory) ||
        ::lstat(credentialDirectory.c_str(), &credentialDirectoryMetadata) != 0 ||
        !S_ISDIR(credentialDirectoryMetadata.st_mode) ||
        (credentialDirectoryMetadata.st_uid != 0 &&
         credentialDirectoryMetadata.st_uid != ::geteuid()) ||
        (credentialDirectoryMetadata.st_mode & 0022) != 0)
    {
        reason = "IB_PAPER_RUNTIME_CREDENTIAL_PATHS_INVALID";
        return false;
    }
    if (maxRequestBytes < 1024 || maxRequestBytes > 32768)
    {
        reason = "IB_PAPER_MAX_REQUEST_BYTES_INVALID";
        return false;
    }
    if (ioTimeoutMs < 1 || ioTimeoutMs > 30000)
    {
        reason = "IB_PAPER_IO_TIMEOUT_INVALID";
        return false;
    }
    if (readinessTimeoutMs < 100 || readinessTimeoutMs > 30000)
    {
        reason = "IB_PAPER_READINESS_TIMEOUT_INVALID";
        return false;
    }
    if (reconnectTimeoutMs < 1000 || reconnectTimeoutMs > 300000 ||
        reconnectTimeoutMs < readinessTimeoutMs)
    {
        reason = "IB_PAPER_RECONNECT_TIMEOUT_INVALID";
        return false;
    }
    reason.clear();
    return true;
}

bool IbPaperExecutionRuntimeConfig::ValidateProductionIdentity(
    std::uint32_t effectiveServiceUid, std::string& reason) const
{
    if (!Enabled())
    {
        reason.clear();
        return true;
    }
    if (effectiveServiceUid == 0)
    {
        reason = "IB_PAPER_SERVICE_UID_NOT_ISOLATED";
        return false;
    }
    if (allowedGatewayUids.size() != 1 ||
        *allowedGatewayUids.begin() == effectiveServiceUid)
    {
        reason = "IB_PAPER_GATEWAY_UID_NOT_ISOLATED";
        return false;
    }
    reason.clear();
    return true;
}

bool IbPaperExecutionRuntimeConfig::FromEnvironment(
    int currentPid, IbPaperExecutionRuntimeConfig& config, std::string& reason)
{
    static const char* keys[] = {
        "HEPTA_IB_EXECUTION_MODE", "HEPTA_IB_PAPER_ACCOUNT",
        "HEPTA_IB_PAPER_HOST", "HEPTA_IB_PAPER_PORT",
        "HEPTA_IB_PAPER_CLIENT_ID", "HEPTA_IB_PAPER_MAX_ORDER_QTY",
        "HEPTA_IB_PAPER_MAX_ORDER_NOTIONAL",
        "HEPTA_IB_PAPER_MAX_ORDERS_PER_MINUTE",
        "HEPTA_IB_PAPER_MAX_ACTIVE_ORDERS",
        "HEPTA_IB_PAPER_MAX_GROSS_POSITION",
        "HEPTA_EXECUTION_EXTERNAL_P1_CANARY_LMT_DAY",
        "HEPTA_EXECUTION_MAX_ORDER_NOTIONAL",
        "HEPTA_IB_PAPER_QUOTE_CONTRACTS",
        "HEPTA_IB_PAPER_PRIMARY_QUOTE_INSTRUMENT",
        "HEPTA_IB_PAPER_QUOTE_MAX_AGE_MS",
        "HEPTA_IB_PAPER_CONTROL_DIRECTORY",
        "HEPTA_IB_EXECUTION_GATEWAY_UID",
        "HEPTA_IB_EXECUTION_GATEWAY_AGENT_ID",
        "HEPTA_IB_EXECUTION_DOMAIN_ID",
        "HEPTA_IB_EXECUTION_MAX_REQUEST_BYTES",
        "HEPTA_IB_EXECUTION_IO_TIMEOUT_MS",
        "HEPTA_IB_EXECUTION_READINESS_TIMEOUT_MS",
        "HEPTA_IB_EXECUTION_RECONNECT_TIMEOUT_MS",
        "LISTEN_PID", "LISTEN_FDS", "LISTEN_FDNAMES",
        "STATE_DIRECTORY", "CREDENTIALS_DIRECTORY"
    };
    std::map<std::string, std::string> values;
    for (std::size_t i = 0; i < sizeof(keys) / sizeof(keys[0]); ++i)
    {
        const char* value = std::getenv(keys[i]);
        if (value != nullptr) values[keys[i]] = value;
    }
    return FromValues(values, currentPid, config, reason);
}

bool IbPaperExecutionRuntimeConfig::FromValues(
    const std::map<std::string, std::string>& values, int currentPid,
    IbPaperExecutionRuntimeConfig& config, std::string& reason)
{
    config = IbPaperExecutionRuntimeConfig();
    const std::string mode = ReadString(values, "HEPTA_IB_EXECUTION_MODE");
    if (mode.empty() || mode == "DISABLED")
    {
        if (HasDisabledResidualConfiguration(values))
        {
            reason = "IB_PAPER_RUNTIME_DISABLED_CONFIGURATION_PRESENT";
            return false;
        }
        return config.Validate(reason);
    }
    if (mode != "PAPER")
    {
        reason = "IB_PAPER_RUNTIME_MODE_UNSUPPORTED";
        return false;
    }
    config.mode = IbPaperExecutionRuntimeMode::Paper;

    if (!IbPaperExecutionProfileConfig::FromValues(values, config.profile, reason))
        return false;

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
        reason = "IB_PAPER_SYSTEMD_SOCKET_ACTIVATION_INVALID";
        return false;
    }

    std::uint64_t gatewayUid = 0;
    if (!ParseUnsigned(ReadString(values, "HEPTA_IB_EXECUTION_GATEWAY_UID"),
        std::numeric_limits<std::uint32_t>::max(), gatewayUid))
    {
        reason = "IB_PAPER_GATEWAY_UID_INVALID";
        return false;
    }
    config.allowedGatewayUids.insert(static_cast<std::uint32_t>(gatewayUid));
    config.gatewayContextBinding.agentId =
        ReadString(values, "HEPTA_IB_EXECUTION_GATEWAY_AGENT_ID");
    config.gatewayContextBinding.account = config.profile.account;
    config.gatewayContextBinding.venue = "IB";
    config.gatewayContextBinding.executionDomain =
        ReadString(values, "HEPTA_IB_EXECUTION_DOMAIN_ID");
    if (config.gatewayContextBinding.executionDomain.empty())
        config.gatewayContextBinding.executionDomain = "PAPER";

    config.stateDirectory = config.profile.stateDirectory;
    config.journalPath = config.stateDirectory + "/oms-journal.jsonl";
    config.controlDirectory = config.profile.controlDirectory;
    config.authorizationCredentialPath = config.profile.authorizationCredentialPath;
    const std::string credentialDirectory =
        ParentDirectory(config.authorizationCredentialPath);
    config.fenceCredentialPath = credentialDirectory + "/" + FenceCredentialName();
    config.fxCashBaselineCredentialPath = credentialDirectory + "/" +
        FxCashBaselineCredentialName();

    if (!ParseQuoteContracts(
            ReadString(values, "HEPTA_IB_PAPER_QUOTE_CONTRACTS"),
            config.quoteContracts))
    {
        reason = "IB_PAPER_QUOTE_CONTRACTS_INVALID";
        return false;
    }
    config.primaryQuoteInstrument =
        ReadString(values, "HEPTA_IB_PAPER_PRIMARY_QUOTE_INSTRUMENT");
    if (!CanonicalText(config.primaryQuoteInstrument, 128))
    {
        reason = "IB_PAPER_PRIMARY_QUOTE_INSTRUMENT_INVALID";
        return false;
    }
    const std::string quoteMaxAge =
        ReadString(values, "HEPTA_IB_PAPER_QUOTE_MAX_AGE_MS");
    {
        if (quoteMaxAge.empty())
        {
            reason = "IB_PAPER_QUOTE_MAX_AGE_INVALID";
            return false;
        }
        std::uint64_t parsed = 0;
        if (!ParseUnsigned(quoteMaxAge, 60000, parsed) || parsed < 100)
        {
            reason = "IB_PAPER_QUOTE_MAX_AGE_INVALID";
            return false;
        }
        config.quoteMaxAgeMs = parsed;
    }

    const std::string maxRequest =
        ReadString(values, "HEPTA_IB_EXECUTION_MAX_REQUEST_BYTES");
    if (!maxRequest.empty())
    {
        std::uint64_t parsed = 0;
        if (!ParseUnsigned(maxRequest, 32768, parsed) || parsed < 1024)
        {
            reason = "IB_PAPER_MAX_REQUEST_BYTES_INVALID";
            return false;
        }
        config.maxRequestBytes = static_cast<std::size_t>(parsed);
    }
    const std::string ioTimeout =
        ReadString(values, "HEPTA_IB_EXECUTION_IO_TIMEOUT_MS");
    if (!ioTimeout.empty())
    {
        std::uint64_t parsed = 0;
        if (!ParseUnsigned(ioTimeout, 30000, parsed) || parsed < 1)
        {
            reason = "IB_PAPER_IO_TIMEOUT_INVALID";
            return false;
        }
        config.ioTimeoutMs = static_cast<int>(parsed);
    }
    const std::string readinessTimeout =
        ReadString(values, "HEPTA_IB_EXECUTION_READINESS_TIMEOUT_MS");
    if (!readinessTimeout.empty())
    {
        std::uint64_t parsed = 0;
        if (!ParseUnsigned(readinessTimeout, 30000, parsed) || parsed < 100)
        {
            reason = "IB_PAPER_READINESS_TIMEOUT_INVALID";
            return false;
        }
        config.readinessTimeoutMs = static_cast<int>(parsed);
    }
    const std::string reconnectTimeout =
        ReadString(values, "HEPTA_IB_EXECUTION_RECONNECT_TIMEOUT_MS");
    if (!reconnectTimeout.empty())
    {
        std::uint64_t parsed = 0;
        if (!ParseUnsigned(reconnectTimeout, 300000, parsed) || parsed < 1000)
        {
            reason = "IB_PAPER_RECONNECT_TIMEOUT_INVALID";
            return false;
        }
        config.reconnectTimeoutMs = static_cast<int>(parsed);
    }
    return config.Validate(reason);
}
