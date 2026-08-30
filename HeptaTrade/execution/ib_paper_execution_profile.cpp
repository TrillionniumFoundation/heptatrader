#include "ib_paper_execution_profile.h"
#include "../observability/runtime_telemetry.h"
#include "../risk/deterministic_risk_policy.h"

#include <cerrno>
#include <cmath>
#include <cstring>
#include <cstdlib>
#include <fcntl.h>
#include <iomanip>
#include <limits>
#include <locale>
#include <openssl/evp.h>
#include <set>
#include <sstream>
#include <sys/stat.h>
#include <unistd.h>

namespace
{
// Bounded to the IDEALPRO minimum ticket used by this PAPER-only profile.
const double kMaximumOrderQuantity = 25000.0;
const double kMaximumOrderNotional = 250000.0;
const std::size_t kMaximumOrdersPerMinute = 30;
const std::size_t kMaximumActiveOrders = 50;
const double kMaximumGrossPosition = 100000.0;
const int kMaximumClientId = 65535;
const char* const kExternalLimitDayKey =
    "HEPTA_EXECUTION_EXTERNAL_P1_CANARY_LMT_DAY";
const char* const kExternalMaxOrderNotionalKey =
    "HEPTA_EXECUTION_MAX_ORDER_NOTIONAL";
const char* const kQuoteMaxAgeKey =
    "HEPTA_IB_PAPER_QUOTE_MAX_AGE_MS";

bool CanonicalUnsignedInteger(const std::string& value)
{
    if (value.empty() || (value.size() > 1 && value[0] == '0')) return false;
    for (std::string::const_iterator it = value.begin(); it != value.end(); ++it)
        if (*it < '0' || *it > '9') return false;
    return true;
}

std::string EscapeJson(const std::string& value)
{
    std::string escaped;
    escaped.reserve(value.size());
    for (std::string::const_iterator it = value.begin();
         it != value.end(); ++it)
    {
        const unsigned char byte = static_cast<unsigned char>(*it);
        if (byte == '"' || byte == '\\') escaped.push_back('\\');
        // Subscription identifiers are authority metadata. Keep the outer
        // preview envelope valid even if an adapter supplies an odd value.
        escaped.push_back(byte < 0x20u ? '?' : static_cast<char>(byte));
    }
    return escaped;
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

bool SafeAbsolutePath(const std::string& path)
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

bool SameOrChildPath(const std::string& path, const std::string& parent)
{
    return path == parent ||
        (path.size() > parent.size() &&
         path.compare(0, parent.size(), parent) == 0 &&
         path[parent.size()] == '/');
}

bool ParseUnsigned(const std::string& value, std::uint64_t maximum, std::uint64_t& out)
{
    if (!CanonicalUnsignedInteger(value)) return false;
    char* end = nullptr;
    errno = 0;
    const unsigned long long parsed = std::strtoull(value.c_str(), &end, 10);
    if (errno != 0 || end == value.c_str() || *end != '\0' || parsed > maximum)
        return false;
    out = static_cast<std::uint64_t>(parsed);
    return true;
}

bool ParsePositiveDouble(const std::string& value, double& out)
{
    if (!CanonicalFloating(value)) return false;
    std::istringstream input(value);
    input.imbue(std::locale::classic());
    input >> std::noskipws;
    double parsed = 0.0;
    input >> parsed;
    if (!input || !input.eof() || !std::isfinite(parsed) || parsed <= 0.0)
        return false;
    out = parsed;
    return true;
}

std::string CanonicalRecoveryDecimal(double value)
{
    if (!std::isfinite(value)) return std::string();
    if (value == 0.0) return "0";
    std::ostringstream output;
    output.imbue(std::locale::classic());
    output << std::fixed << std::setprecision(17) << value;
    std::string canonical = output.str();
    const std::size_t dot = canonical.find('.');
    if (dot != std::string::npos)
    {
        while (!canonical.empty() && canonical.back() == '0')
            canonical.pop_back();
        if (!canonical.empty() && canonical.back() == '.')
            canonical.pop_back();
    }
    return canonical == "-0" ? std::string("0") : canonical;
}

bool CanonicalSha256(const std::string& value)
{
    if (value.size() != 71 || value.compare(0, 7, "sha256:") != 0)
        return false;
    for (std::size_t i = 7; i < value.size(); ++i)
        if (!((value[i] >= '0' && value[i] <= '9') ||
              (value[i] >= 'a' && value[i] <= 'f')))
            return false;
    return true;
}

void AppendCanonicalField(std::string& out, const char* name,
                          const std::string& value)
{
    out.append(name);
    out.push_back('=');
    out.append(std::to_string(value.size()));
    out.push_back(':');
    out.append(value);
    out.push_back(';');
}

std::string CanonicalDouble(double value)
{
    static_assert(sizeof(double) == sizeof(std::uint64_t),
                  "unsupported double representation");
    std::uint64_t bits = 0;
    std::memcpy(&bits, &value, sizeof(bits));
    std::ostringstream out;
    out.imbue(std::locale::classic());
    out << std::hex << std::setw(16) << std::setfill('0') << bits;
    return out.str();
}

std::string Sha256Hex(const std::string& value)
{
    unsigned char digest[EVP_MAX_MD_SIZE];
    unsigned int length = 0;
    EVP_MD_CTX* context = EVP_MD_CTX_new();
    if (context == nullptr) return std::string();
    const bool ok = EVP_DigestInit_ex(context, EVP_sha256(), nullptr) == 1 &&
        EVP_DigestUpdate(context, value.data(), value.size()) == 1 &&
        EVP_DigestFinal_ex(context, digest, &length) == 1;
    EVP_MD_CTX_free(context);
    if (!ok || length != 32) return std::string();
    std::ostringstream out;
    out.imbue(std::locale::classic());
    out << std::hex << std::setfill('0');
    for (unsigned int i = 0; i < length; ++i)
        out << std::setw(2) << static_cast<unsigned int>(digest[i]);
    return out.str();
}

bool StrictPaperAccount(const std::string& account)
{
    if (account.size() < 3 || account.size() > 18 ||
        account.compare(0, 2, "DU") != 0)
        return false;
    bool hasDigit = false;
    for (std::size_t i = 2; i < account.size(); ++i)
    {
        const unsigned char character =
            static_cast<unsigned char>(account[i]);
        if (character >= static_cast<unsigned char>('0') &&
            character <= static_cast<unsigned char>('9'))
            hasDigit = true;
        else if (character < 'A' || character > 'Z')
            return false;
    }
    return hasDigit;
}

bool CanonicalDomainName(const std::string& value)
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

bool CanonicalControlDirectory(const std::string& value)
{
    const std::string base =
        IbPaperExecutionProfileConfig::ControlDirectoryPath();
    if (value == base) return true;
    const std::string prefix = base + "-";
    return value.size() > prefix.size() &&
        value.compare(0, prefix.size(), prefix) == 0 &&
        CanonicalDomainName(value.substr(prefix.size()));
}

bool PaperExecutionDomain(const std::string& value)
{
    return value == "PAPER" ||
        (value.size() > 6 && value.compare(0, 6, "PAPER:") == 0 &&
         CanonicalDomainName(value.substr(6)));
}

bool ReadPrivateCredential(const std::string& path, std::string& value,
                           std::string& reason)
{
    const int fd = ::open(path.c_str(), O_RDONLY | O_CLOEXEC | O_NOFOLLOW);
    if (fd < 0)
    {
        reason = "IB_PAPER_AUTHORIZATION_CREDENTIAL_OPEN_FAILED";
        return false;
    }
    struct stat metadata;
    if (::fstat(fd, &metadata) != 0)
    {
        ::close(fd);
        reason = "IB_PAPER_AUTHORIZATION_CREDENTIAL_UNSAFE";
        return false;
    }
    const mode_t credentialMode = metadata.st_mode & 07777;
    const bool privateSourceMode = credentialMode == 0400;
    const bool systemdCredentialMode =
        credentialMode == 0440 && metadata.st_uid == 0 && metadata.st_gid == 0;
    if (!S_ISREG(metadata.st_mode) ||
        metadata.st_size <= 0 || metadata.st_size > 256 ||
        (!privateSourceMode && !systemdCredentialMode) ||
        metadata.st_nlink != 1 ||
        (metadata.st_uid != 0 && metadata.st_uid != ::geteuid()))
    {
        ::close(fd);
        reason = "IB_PAPER_AUTHORIZATION_CREDENTIAL_UNSAFE";
        return false;
    }
    value.assign(static_cast<std::size_t>(metadata.st_size), '\0');
    std::size_t offset = 0;
    while (offset < value.size())
    {
        const ssize_t count = ::read(fd, &value[offset], value.size() - offset);
        if (count < 0 && errno == EINTR) continue;
        if (count <= 0)
        {
            ::close(fd);
            reason = "IB_PAPER_AUTHORIZATION_CREDENTIAL_READ_FAILED";
            return false;
        }
        offset += static_cast<std::size_t>(count);
    }
    ::close(fd);
    while (!value.empty() && (value[value.size() - 1] == '\n' ||
                              value[value.size() - 1] == '\r'))
        value.resize(value.size() - 1);
    return true;
}
std::string MapCommonRiskReason(const std::string& code)
{
    if (code == "RISK_ORDER_QUANTITY_LIMIT" ||
        code == "RISK_ORDER_QUANTITY_INVALID")
        return "IB_PAPER_MAX_ORDER_QUANTITY_EXCEEDED";
    if (code == "RISK_ORDER_NOTIONAL_LIMIT" ||
        code == "RISK_VALUATION_PRICE_INVALID")
        return "IB_PAPER_MAX_ORDER_NOTIONAL_EXCEEDED";
    if (code == "RISK_ORDER_RATE_LIMIT")
        return "IB_PAPER_ORDER_RATE_EXCEEDED";
    if (code == "RISK_ACTIVE_ORDER_LIMIT")
        return "IB_PAPER_MAX_ACTIVE_ORDERS_EXCEEDED";
    if (code == "RISK_GROSS_POSITION_LIMIT" ||
        code == "RISK_POSITION_SNAPSHOT_INVALID")
        return "IB_PAPER_MAX_GROSS_POSITION_EXCEEDED";
    return code;
}
}

const char* IbPaperExecutionProfileConfig::AuthorizationCredentialName()
{
    return "hepta-ib-paper-authorization";
}

const char* IbPaperExecutionProfileConfig::ControlDirectoryPath()
{
    return "/run/hepta/ib-paper-control";
}

const char* IbPaperExecutionProfileConfig::AllowedSecurityTypes()
{
    return "CASH,STK";
}

const char* IbPaperExecutionProfileConfig::AllowedOrderTypes() const
{
    return UsesExternalLimitDay() ? "LMT" : "MKT";
}

const char* IbPaperExecutionProfileConfig::OrderModeName(
    IbPaperOrderMode mode)
{
    switch (mode)
    {
    case IbPaperOrderMode::LocalMarketDay:
        return "LOCAL_MKT_DAY";
    case IbPaperOrderMode::ExternalLimitDay:
        return "EXTERNAL_P1_CANARY_LMT_DAY";
    }
    return "UNKNOWN";
}

bool IbPaperExecutionProfileConfig::UsesExternalLimitDay() const
{
    return orderMode == IbPaperOrderMode::ExternalLimitDay;
}

bool IbPaperExecutionProfileConfig::FromEnvironment(
    IbPaperExecutionProfileConfig& config, std::string& reason)
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
        "HEPTA_IB_PAPER_QUOTE_MAX_AGE_MS",
        "HEPTA_IB_PAPER_CONTROL_DIRECTORY", "STATE_DIRECTORY",
        "CREDENTIALS_DIRECTORY"
    };
    std::map<std::string, std::string> values;
    for (std::size_t i = 0; i < sizeof(keys) / sizeof(keys[0]); ++i)
    {
        const char* value = std::getenv(keys[i]);
        if (value != nullptr) values[keys[i]] = value;
    }
    return FromValues(values, config, reason);
}

bool IbPaperExecutionProfileConfig::Validate(std::string& reason) const
{
    if (!enabled)
    {
        if (orderMode != IbPaperOrderMode::LocalMarketDay ||
            externalQuoteMaxAgeMs != 0)
        {
            reason = "IB_PAPER_EXTERNAL_ORDER_MODE_REQUIRES_PAPER";
            return false;
        }
        reason.clear();
        return true;
    }
    if (!StrictPaperAccount(account))
    {
        reason = "IB_PAPER_ACCOUNT_REQUIRED";
        return false;
    }
    if (host != "127.0.0.1" && host != "::1")
    {
        reason = "IB_PAPER_LOOPBACK_HOST_REQUIRED";
        return false;
    }
    if (port != 7497 && port != 4002)
    {
        reason = "IB_PAPER_PORT_INVALID";
        return false;
    }
    if (clientId <= 0 || clientId > kMaximumClientId)
    {
        reason = "IB_PAPER_CLIENT_ID_INVALID";
        return false;
    }
    if (!SafeAbsolutePath(stateDirectory))
    {
        reason = "IB_PAPER_STATE_DIRECTORY_INVALID";
        return false;
    }
    if (!CanonicalControlDirectory(controlDirectory) ||
        !SafeAbsolutePath(controlDirectory) ||
        SameOrChildPath(controlDirectory, stateDirectory) ||
        SameOrChildPath(stateDirectory, controlDirectory))
    {
        reason = "IB_PAPER_CONTROL_DIRECTORY_INVALID";
        return false;
    }
    const std::size_t slash = authorizationCredentialPath.find_last_of('/');
    if (slash == std::string::npos ||
        authorizationCredentialPath.substr(slash + 1) != AuthorizationCredentialName() ||
        !SafeAbsolutePath(authorizationCredentialPath.substr(0, slash)))
    {
        reason = "IB_PAPER_AUTHORIZATION_CREDENTIAL_PATH_INVALID";
        return false;
    }
    if (!std::isfinite(maxOrderQuantity) || maxOrderQuantity <= 0.0 ||
        maxOrderQuantity > kMaximumOrderQuantity ||
        !std::isfinite(maxOrderNotional) || maxOrderNotional <= 0.0 ||
        maxOrderNotional > kMaximumOrderNotional ||
        maxOrdersPerMinute == 0 ||
        maxOrdersPerMinute > kMaximumOrdersPerMinute ||
        maxActiveOrders == 0 || maxActiveOrders > kMaximumActiveOrders ||
        !std::isfinite(maxGrossPosition) || maxGrossPosition <= 0.0 ||
        maxGrossPosition > kMaximumGrossPosition)
    {
        reason = "IB_PAPER_HARD_LIMITS_INVALID";
        return false;
    }
    switch (orderMode)
    {
    case IbPaperOrderMode::LocalMarketDay:
        if (externalQuoteMaxAgeMs != 0)
        {
            reason = "IB_PAPER_EXTERNAL_ORDER_MODE_CONFIGURATION_INVALID";
            return false;
        }
        break;
    case IbPaperOrderMode::ExternalLimitDay:
        if (maxOrderQuantity > 1.0 || maxOrderNotional > 5000.0 ||
            maxActiveOrders > 1 || maxGrossPosition > 1.0 ||
            externalQuoteMaxAgeMs < 100 ||
            externalQuoteMaxAgeMs > 5000)
        {
            reason = "IB_PAPER_EXTERNAL_ORDER_MODE_LIMITS_INVALID";
            return false;
        }
        break;
    default:
        reason = "IB_PAPER_ORDER_MODE_INVALID";
        return false;
    }
    reason.clear();
    return true;
}

bool IbPaperExecutionProfileConfig::VerifyAuthorizationCredential(
    std::string& reason) const
{
    if (!enabled)
    {
        reason = "IB_PAPER_PROFILE_DISABLED";
        return false;
    }
    std::string value;
    if (!ReadPrivateCredential(authorizationCredentialPath, value, reason))
        return false;
    std::string expected;
    if (!BuildAuthorizationCredential(expected, reason)) return false;
    if (value != expected)
    {
        reason = "IB_PAPER_AUTHORIZATION_CREDENTIAL_MISMATCH";
        return false;
    }
    reason.clear();
    return true;
}

bool IbPaperExecutionProfileConfig::BuildAuthorizationCredential(
    std::string& value, std::string& reason) const
{
    if (!enabled)
    {
        reason = "IB_PAPER_PROFILE_DISABLED";
        return false;
    }
    if (!Validate(reason)) return false;
    std::string canonical;
    AppendCanonicalField(canonical, "profile_version",
                         UsesExternalLimitDay() ? "4" : "3");
    AppendCanonicalField(canonical, "account", account);
    AppendCanonicalField(canonical, "host", host);
    AppendCanonicalField(canonical, "port", std::to_string(port));
    AppendCanonicalField(canonical, "client_id", std::to_string(clientId));
    AppendCanonicalField(canonical, "control_directory", controlDirectory);
    AppendCanonicalField(canonical, "allowed_security_types", AllowedSecurityTypes());
    AppendCanonicalField(canonical, "allowed_order_types", AllowedOrderTypes());
    AppendCanonicalField(canonical, "max_order_quantity",
                         CanonicalDouble(maxOrderQuantity));
    AppendCanonicalField(canonical, "max_order_notional",
                         CanonicalDouble(maxOrderNotional));
    AppendCanonicalField(canonical, "max_orders_per_minute",
                         std::to_string(maxOrdersPerMinute));
    AppendCanonicalField(canonical, "max_active_orders",
                         std::to_string(maxActiveOrders));
    AppendCanonicalField(canonical, "max_gross_position",
                         CanonicalDouble(maxGrossPosition));
    if (UsesExternalLimitDay())
    {
        AppendCanonicalField(canonical, "paper_order_mode",
                             OrderModeName(orderMode));
        AppendCanonicalField(canonical, "quote_max_age_ms",
                             std::to_string(externalQuoteMaxAgeMs));
    }
    const std::string digest = Sha256Hex(canonical);
    if (digest.empty())
    {
        reason = "IB_PAPER_AUTHORIZATION_PROFILE_HASH_FAILED";
        return false;
    }
    value = std::string(UsesExternalLimitDay() ?
        "PAPER-V4:sha256:" : "PAPER-V3:sha256:") + digest;
    reason.clear();
    return true;
}

bool IbPaperExecutionProfileConfig::FromValues(
    const std::map<std::string, std::string>& values,
    IbPaperExecutionProfileConfig& config, std::string& reason)
{
    config = IbPaperExecutionProfileConfig();
    const std::string mode = Read(values, "HEPTA_IB_EXECUTION_MODE");
    const std::map<std::string, std::string>::const_iterator externalMode =
        values.find(kExternalLimitDayKey);
    const std::map<std::string, std::string>::const_iterator externalNotional =
        values.find(kExternalMaxOrderNotionalKey);
    if (mode.empty() || mode == "DISABLED")
    {
        if (externalMode != values.end() || externalNotional != values.end())
        {
            reason = "IB_PAPER_EXTERNAL_ORDER_MODE_REQUIRES_PAPER";
            return false;
        }
        reason.clear();
        return true;
    }
    if (mode != "PAPER")
    {
        reason = "IB_PAPER_MODE_UNSUPPORTED";
        return false;
    }
    config.enabled = true;
    if (externalMode != values.end())
    {
        if (externalMode->second != "1" ||
            externalNotional == values.end() ||
            externalNotional->second != "5000")
        {
            reason = "IB_PAPER_EXTERNAL_ORDER_MODE_CONFIGURATION_INVALID";
            return false;
        }
        config.orderMode = IbPaperOrderMode::ExternalLimitDay;
        std::uint64_t quoteMaxAge = 0;
        if (!ParseUnsigned(Read(values, kQuoteMaxAgeKey), 5000,
                quoteMaxAge) || quoteMaxAge < 100)
        {
            reason = "IB_PAPER_EXTERNAL_ORDER_MODE_LIMITS_INVALID";
            return false;
        }
        config.externalQuoteMaxAgeMs = quoteMaxAge;
    }
    else if (externalNotional != values.end())
    {
        reason = "IB_PAPER_EXTERNAL_ORDER_MODE_CONFIGURATION_INVALID";
        return false;
    }
    config.account = Read(values, "HEPTA_IB_PAPER_ACCOUNT");
    config.host = Read(values, "HEPTA_IB_PAPER_HOST");
    config.stateDirectory = Read(values, "STATE_DIRECTORY");
    config.controlDirectory = Read(values, "HEPTA_IB_PAPER_CONTROL_DIRECTORY");
    const std::string credentialsDirectory = Read(values, "CREDENTIALS_DIRECTORY");
    config.authorizationCredentialPath = credentialsDirectory + "/" +
        AuthorizationCredentialName();

    std::uint64_t unsignedValue = 0;
    if (!ParseUnsigned(Read(values, "HEPTA_IB_PAPER_PORT"), 65535, unsignedValue) ||
        unsignedValue == 0)
    {
        reason = "IB_PAPER_PORT_INVALID";
        return false;
    }
    config.port = static_cast<int>(unsignedValue);
    if (!ParseUnsigned(Read(values, "HEPTA_IB_PAPER_CLIENT_ID"),
            static_cast<std::uint64_t>(kMaximumClientId), unsignedValue) ||
        unsignedValue == 0)
    {
        reason = "IB_PAPER_CLIENT_ID_INVALID";
        return false;
    }
    config.clientId = static_cast<int>(unsignedValue);
    if (!ParsePositiveDouble(Read(values, "HEPTA_IB_PAPER_MAX_ORDER_QTY"),
            config.maxOrderQuantity) ||
        !ParsePositiveDouble(Read(values, "HEPTA_IB_PAPER_MAX_ORDER_NOTIONAL"),
            config.maxOrderNotional) ||
        !ParseUnsigned(Read(values, "HEPTA_IB_PAPER_MAX_ORDERS_PER_MINUTE"),
            kMaximumOrdersPerMinute, unsignedValue) || unsignedValue == 0)
    {
        reason = "IB_PAPER_HARD_LIMITS_INVALID";
        return false;
    }
    config.maxOrdersPerMinute = static_cast<std::size_t>(unsignedValue);
    if (!ParseUnsigned(Read(values, "HEPTA_IB_PAPER_MAX_ACTIVE_ORDERS"),
            kMaximumActiveOrders, unsignedValue) || unsignedValue == 0)
    {
        reason = "IB_PAPER_HARD_LIMITS_INVALID";
        return false;
    }
    config.maxActiveOrders = static_cast<std::size_t>(unsignedValue);
    if (!ParsePositiveDouble(Read(values, "HEPTA_IB_PAPER_MAX_GROSS_POSITION"),
            config.maxGrossPosition))
    {
        reason = "IB_PAPER_HARD_LIMITS_INVALID";
        return false;
    }
    return config.Validate(reason);
}

IbPaperExecutionGuard::IbPaperExecutionGuard(
    const IbPaperExecutionProfileConfig& config,
    const std::shared_ptr<IbPaperKillSwitchReader>& killSwitch)
    : m_config(config), m_killSwitch(killSwitch) {}

void IbPaperExecutionGuard::PruneRateWindow(std::int64_t nowMs)
{
    const std::int64_t cutoff = nowMs - 60000;
    while (!m_acceptedPlaceTimesMs.empty() &&
           m_acceptedPlaceTimesMs.front() <= cutoff)
        m_acceptedPlaceTimesMs.pop_front();
}

bool IbPaperExecutionGuard::AllowPlace(
    const IbPlaceOrderCommand& command,
    const IbPaperAuthoritativeRiskSnapshot& snapshot,
    std::int64_t nowMs, std::string& reason)
{
    if (m_config.UsesExternalLimitDay())
    {
        reason = "IB_PAPER_AUTHORITATIVE_PRICE_REQUIRED";
        return false;
    }
    return AllowPlaceAtAuthoritativePrice(
        command, snapshot, command.referencePrice, nowMs, reason);
}

bool IbPaperExecutionGuard::AllowPlaceAtAuthoritativePrice(
    const IbPlaceOrderCommand& command,
    const IbPaperAuthoritativeRiskSnapshot& snapshot,
    double authoritativePrice,
    std::int64_t nowMs, std::string& reason)
{
    std::lock_guard<std::mutex> lock(m_mutex);
    if (!m_config.enabled)
    {
        reason = "IB_PAPER_PROFILE_DISABLED";
        return false;
    }
    if (command.context.account != m_config.account ||
        command.context.venue != "IB" ||
        !PaperExecutionDomain(command.context.executionDomain))
    {
        reason = "IB_PAPER_EXECUTION_CONTEXT_MISMATCH";
        return false;
    }
    if (command.contract.secType != "STK" && command.contract.secType != "CASH")
    {
        reason = "IB_PAPER_SECURITY_TYPE_NOT_ALLOWED";
        return false;
    }
    if (!m_killSwitch)
    {
        reason = "IB_PAPER_KILL_SWITCH_STATE_UNCERTAIN";
        return false;
    }
    if (m_killSwitch->BlocksRiskIncrease(reason))
    {
        RuntimeRecordKillSwitch("blocked");
        return false;
    }
    RuntimeRecordKillSwitch("open");
    if (!snapshot.complete)
    {
        reason = "IB_PAPER_AUTHORITATIVE_RISK_SNAPSHOT_REQUIRED";
        return false;
    }
    const double quantity = std::fabs(command.order.totalQuantity);
    if (command.timeInForce != "DAY" ||
        (command.order.action != "BUY" && command.order.action != "SELL"))
    {
        reason = "IB_PAPER_ORDER_INTENT_INVALID";
        return false;
    }
    if (m_config.UsesExternalLimitDay())
    {
        if (!(command.order.totalQuantity > 0.0) ||
            command.order.auxPrice != 0.0 || command.order.outsideRth ||
            !command.order.orderRef.empty())
        {
            reason = "IB_PAPER_EXTERNAL_ORDER_FIELDS_INVALID";
            return false;
        }
        if (command.order.orderType != "LMT")
        {
            reason = "IB_PAPER_EXTERNAL_LIMIT_ORDERS_ONLY";
            return false;
        }
        if (!std::isfinite(command.order.lmtPrice) ||
            command.order.lmtPrice <= 0.0)
        {
            reason = "IB_PAPER_EXTERNAL_LIMIT_PRICE_REQUIRED";
            return false;
        }
    }
    else
    {
        if (command.order.orderType != "MKT")
        {
            reason = "IB_PAPER_MARKET_ORDERS_ONLY";
            return false;
        }
        if (command.order.lmtPrice != 0.0)
        {
            reason = "IB_PAPER_MARKET_ORDER_LIMIT_PRICE_FORBIDDEN";
            return false;
        }
    }
    if (!std::isfinite(command.referencePrice) || command.referencePrice <= 0.0)
    {
        reason = "IB_PAPER_REFERENCE_PRICE_REQUIRED";
        return false;
    }
    if (!std::isfinite(authoritativePrice) || authoritativePrice <= 0.0)
    {
        reason = m_config.UsesExternalLimitDay() ?
            "IB_PAPER_AUTHORITATIVE_PRICE_REQUIRED" :
            "IB_PAPER_MAX_ORDER_NOTIONAL_EXCEEDED";
        return false;
    }
    if (m_config.UsesExternalLimitDay() &&
        (command.order.lmtPrice != authoritativePrice ||
         command.referencePrice != authoritativePrice))
    {
        reason = "IB_PAPER_EXTERNAL_LIMIT_PRICE_MISMATCH";
        return false;
    }

    PruneRateWindow(nowMs);
    DeterministicRiskLimits limits;
    limits.maxOrderQuantity = m_config.maxOrderQuantity;
    limits.maxOrderNotional = m_config.maxOrderNotional;
    limits.maxOrdersPerMinute = m_config.maxOrdersPerMinute;
    limits.maxActiveOrders = m_config.maxActiveOrders;
    limits.maxGrossPosition = m_config.maxGrossPosition;
    limits.maxPriceDeviationBps = 0.0;

    DeterministicRiskContext context;
    context.action = command.order.action;
    context.orderType = command.order.orderType;
    context.quantity = quantity;
    context.valuationPrice = authoritativePrice;
    context.submittedPrice = command.order.lmtPrice;
    context.referencePrice = authoritativePrice;
    context.ordersInLastMinute = m_acceptedPlaceTimesMs.size();
    context.activeOrderCount = snapshot.activeOrderCount;
    context.grossAbsolutePosition = snapshot.grossAbsolutePosition;
    context.projectedGrossAbsolutePosition =
        snapshot.grossAbsolutePosition + quantity;
    context.exposureReducing = false;
    context.quoteFresh = true;
    context.portfolioSnapshotComplete = snapshot.complete;
    const DeterministicRiskDecision decision =
        DeterministicRiskPolicy::Evaluate(limits, context);
    if (!decision.allow)
    {
        reason = MapCommonRiskReason(decision.reasonCode);
        return false;
    }
    reason.clear();
    return true;
}

bool IbPaperExecutionGuard::AllowCancel(
    const IbCancelOrderCommand& command, std::string& reason) const
{
    if (!m_config.enabled || command.context.account != m_config.account ||
        command.context.venue != "IB" ||
        !PaperExecutionDomain(command.context.executionDomain) ||
        command.orderId < 0)
    {
        reason = "IB_PAPER_CANCEL_CONTEXT_MISMATCH";
        return false;
    }
    reason.clear();
    return true;
}

void IbPaperExecutionGuard::ReplaceSendAttemptTimes(
    const std::vector<std::int64_t>& times, std::int64_t nowMs)
{
    std::lock_guard<std::mutex> lock(m_mutex);
    m_acceptedPlaceTimesMs.assign(times.begin(), times.end());
    PruneRateWindow(nowMs);
}

IbPaperExecutionPolicyAuthority::IbPaperExecutionPolicyAuthority(
    ExecutionCoordinator& coordinator,
    const IbPaperExecutionProfileConfig& config,
    const IbPaperExecutionPolicyCallbacks& callbacks,
    const std::shared_ptr<IbPaperKillSwitchReader>& killSwitch)
    : m_coordinator(coordinator), m_config(config),
      m_guard(config, killSwitch), m_callbacks(callbacks),
      m_account(config.account),
      m_maxOrderQuantity(config.maxOrderQuantity)
{
    if (m_callbacks.nowMs) RefreshRateBudget(m_callbacks.nowMs());
}

void IbPaperExecutionPolicyAuthority::RefreshRateBudget(
    std::int64_t nowMs, const std::string& executionDomain)
{
    std::vector<std::int64_t> attempts;
    m_coordinator.GetPlaceSendAttemptTimes(
        m_account, executionDomain, nowMs - 60000, attempts);
    m_guard.ReplaceSendAttemptTimes(attempts, nowMs);
}

bool IbPaperExecutionPolicyAuthority::ValidContext(
    const AgentExecutionContext& context) const
{
    return !context.agentId.empty() && !context.sessionId.empty() &&
        !context.toolCallId.empty() && context.venue == "IB" &&
        PaperExecutionDomain(context.executionDomain) &&
        !context.allowCancelAny;
}

ExecutionCommandResult IbPaperExecutionPolicyAuthority::Reject(
    const AgentExecutionContext& context, long orderId,
    const std::string& reason) const
{
    ExecutionCommandResult result;
    result.status = ExecutionCommandStatus::Rejected;
    result.commandId = context.toolCallId;
    result.orderId = orderId;
    result.reasonCode = reason;
    return result;
}

ExecutionCommandResult IbPaperExecutionPolicyAuthority::PlaceOrder(
    const PlaceOrderCommand& command)
{
    std::lock_guard<std::mutex> lock(m_mutex);
    if (!ValidContext(command.context))
        return Reject(command.context, -1, "IB_PAPER_MUTATION_CONTEXT_INVALID");
    ExecutionCommandResult prechecked;
    if (m_coordinator.PrecheckPlaceIbOrder(command, prechecked))
        return prechecked;
    if (!m_callbacks.riskSnapshot || !m_callbacks.nowMs ||
        !m_callbacks.authoritativeQuote)
        return Reject(command.context, -1, "IB_PAPER_POLICY_CALLBACKS_REQUIRED");
    const std::int64_t now = m_callbacks.nowMs();
    if (command.expiresAtMs <= 0 || now >= command.expiresAtMs)
        return Reject(command.context, -1, "TOOL_CALL_EXPIRED");
    if (command.instrument.empty() || command.contract.symbol.empty() ||
        (command.order.action != "BUY" &&
         command.order.action != "SELL") ||
        command.timeInForce != "DAY")
        return Reject(command.context, -1, "IB_PAPER_ORDER_INTENT_INVALID");
    MarketQuoteSnapshot quote;
    std::string reason;
    if (!ValidateFreshQuote(command, now, quote, reason))
        return Reject(command.context, -1, reason);
    RefreshRateBudget(now, command.context.executionDomain);
    const double authoritativePrice =
        command.order.action == "SELL" ? quote.bid : quote.ask;
    if (!m_guard.AllowPlaceAtAuthoritativePrice(
            command, m_callbacks.riskSnapshot(), authoritativePrice, now,
            reason))
        return Reject(command.context, -1, reason);
    PlaceOrderCommand authorized = command;
    // This is service-owned state, not an Agent assertion. Preserve the exact
    // quote that approved risk while the coordinator performs its durable
    // intent and send-attempt writes; the adapter revalidates it under the
    // final broker-send lock.
    authorized.authoritativeQuoteBinding.valid = true;
    authorized.authoritativeQuoteBinding.instrument = quote.instrument;
    authorized.authoritativeQuoteBinding.subscriptionId =
        quote.subscriptionId;
    authorized.authoritativeQuoteBinding.bid = quote.bid;
    authorized.authoritativeQuoteBinding.ask = quote.ask;
    authorized.authoritativeQuoteBinding.observedAtMs = quote.observedAtMs;
    authorized.authoritativeQuoteBinding.staleAfterMs = quote.staleAfterMs;
    const ExecutionCommandResult result =
        m_coordinator.PlaceOrder(authorized);
    RefreshRateBudget(now, command.context.executionDomain);
    return result;
}

bool IbPaperExecutionPolicyAuthority::ValidateFreshQuote(
    const PlaceOrderCommand& command,
    std::int64_t nowMs,
    MarketQuoteSnapshot& quote,
    std::string& reason) const
{
    if (!m_callbacks.authoritativeQuote || command.instrument.empty() || nowMs < 0)
    {
        reason = "AUTHORITATIVE_QUOTE_UNAVAILABLE";
        return false;
    }
    quote = m_callbacks.authoritativeQuote(command.instrument);
    if (quote.instrument != command.instrument ||
        quote.state == MarketSubscriptionState::Unavailable)
    {
        reason = "AUTHORITATIVE_QUOTE_UNAVAILABLE";
        return false;
    }
    if (!quote.IsFresh(static_cast<std::uint64_t>(nowMs)))
    {
        reason = "AUTHORITATIVE_QUOTE_STALE";
        return false;
    }
    if (m_config.UsesExternalLimitDay() &&
        (quote.staleAfterMs < quote.observedAtMs ||
         quote.staleAfterMs - quote.observedAtMs >
            m_config.externalQuoteMaxAgeMs))
    {
        reason = "AUTHORITATIVE_QUOTE_MAX_AGE_EXCEEDED";
        return false;
    }
    if (!std::isfinite(quote.bid) || !std::isfinite(quote.ask) ||
        quote.bid <= 0.0 || quote.ask < quote.bid)
    {
        reason = "AUTHORITATIVE_QUOTE_INVALID";
        return false;
    }
    reason.clear();
    return true;
}

ExecutionCommandResult IbPaperExecutionPolicyAuthority::PreviewOrder(
    const PlaceOrderCommand& command)
{
    std::lock_guard<std::mutex> lock(m_mutex);
    if (!ValidContext(command.context))
        return Reject(command.context, -1, "IB_PAPER_CONTEXT_REQUIRED");
    if (!m_callbacks.riskSnapshot || !m_callbacks.nowMs ||
        !m_callbacks.authoritativeQuote)
        return Reject(command.context, -1, "IB_PAPER_POLICY_CALLBACKS_REQUIRED");
    const std::int64_t now = m_callbacks.nowMs();
    if (command.expiresAtMs <= 0 || now >= command.expiresAtMs)
        return Reject(command.context, -1, "TOOL_CALL_EXPIRED");
    if (command.instrument.empty() || command.contract.symbol.empty() ||
        (command.order.action != "BUY" &&
         command.order.action != "SELL") ||
        command.timeInForce != "DAY")
        return Reject(command.context, -1, "IB_PAPER_ORDER_INTENT_INVALID");
    std::string blockedReason;
    if (m_coordinator.IsMutationBlocked(&blockedReason))
        return Reject(command.context, -1,
            blockedReason.empty() ? "MUTATION_BLOCKED" : blockedReason);
    if (m_coordinator.IsSessionOwnerFenced(
            command.context.agentId, command.context.sessionId))
        return Reject(command.context, -1, "SESSION_OWNER_FENCED");
    if (m_coordinator.IsSessionOwnerRecoveryOnly(
            command.context.agentId, command.context.sessionId))
        return Reject(command.context, -1, "SESSION_RECOVERY_ONLY");
    MarketQuoteSnapshot quote;
    std::string reason;
    if (!ValidateFreshQuote(command, now, quote, reason))
        return Reject(command.context, -1, reason);
    RefreshRateBudget(now, command.context.executionDomain);
    const double authoritativePrice =
        command.order.action == "SELL" ? quote.bid : quote.ask;
    if (!m_guard.AllowPlaceAtAuthoritativePrice(
            command, m_callbacks.riskSnapshot(), authoritativePrice, now,
            reason))
        return Reject(command.context, -1, reason);
    ExecutionCommandResult result;
    result.status = ExecutionCommandStatus::Accepted;
    result.commandId = command.context.toolCallId;
    std::ostringstream output;
    output.imbue(std::locale::classic());
    output << "{\"source\":\"IB\",\"authoritative\":true,"
           << "\"subscription_id\":\""
           << EscapeJson(quote.subscriptionId) << "\","
           << "\"observed_at_ms\":" << quote.observedAtMs << ','
           << "\"stale_after_ms\":" << quote.staleAfterMs << ','
           << "\"stale\":false";
    if (m_config.UsesExternalLimitDay())
    {
        output << ",\"order_type\":\"LMT\""
               << ",\"tif\":\"DAY\""
               << ",\"limit_price\":" << std::setprecision(17)
               << command.order.lmtPrice
               << ",\"reference_price\":"
               << command.referencePrice
               << ",\"quote_bid\":" << quote.bid
               << ",\"quote_ask\":" << quote.ask;
    }
    output << ",\"risk_approved\":true}";
    result.detail = output.str();
    return result;
}

bool IbPaperExecutionPolicyAuthority::IsDurablePlaceReplay(
    const PlaceOrderCommand& command) const
{
    return m_coordinator.IsDurablePlaceReplay(command);
}

ExecutionCommandResult IbPaperExecutionPolicyAuthority::CancelOrder(
    const CancelOrderCommand& command)
{
    std::lock_guard<std::mutex> lock(m_mutex);
    if (!ValidContext(command.context))
        return Reject(command.context, command.orderId,
                      "IB_PAPER_MUTATION_CONTEXT_INVALID");
    std::string reason;
    if (!m_guard.AllowCancel(command, reason))
        return Reject(command.context, command.orderId, reason);
    return m_coordinator.CancelOrder(command);
}

ExecutionControlResult IbPaperExecutionPolicyAuthority::BeginControl(
    const ExecutionControlCommand& command) const
{
    ExecutionControlResult result;
    result.commandId = command.context.toolCallId;
    if (!ValidContext(command.context))
    {
        result.status = ExecutionCommandStatus::Rejected;
        result.reasonCode = "IB_PAPER_CONTROL_CONTEXT_INVALID";
        return result;
    }
    result.status = ExecutionCommandStatus::Accepted;
    return result;
}

ExecutionControlResult IbPaperExecutionPolicyAuthority::QueryCommandStatus(
    const ExecutionControlCommand& command)
{
    std::lock_guard<std::mutex> lock(m_mutex);
    ExecutionControlResult result = BeginControl(command);
    if (result.status == ExecutionCommandStatus::Rejected) return result;
    result.targetCommandId = command.targetCommandId;
    if (command.recoveryIngressFence != 0)
    {
        std::string reason;
        if (!m_coordinator.EnterRecoveryOnlyOwner(
                command.context.agentId, command.context.sessionId,
                command.recoveryIngressFence, reason))
        {
            result.status = ExecutionCommandStatus::Rejected;
            result.reasonCode = reason.empty() ?
                "EXECUTION_RECOVERY_FENCE_FAILED" : reason;
            return result;
        }
    }
    ExecutionCommandResult target;
    if (!m_coordinator.GetCommandStatus(command.context.agentId,
            command.context.sessionId, command.targetCommandId, target))
    {
        result.status = ExecutionCommandStatus::Rejected;
        result.reasonCode = "EXECUTION_COMMAND_NOT_FOUND";
        return result;
    }
    result.status = ExecutionCommandStatus::Accepted;
    result.targetStatus = target.status;
    result.orderId = target.orderId;
    result.reasonCode = target.reasonCode;
    result.detail = target.detail;
    result.mutationBlocked = m_coordinator.IsMutationBlocked(nullptr);
    return result;
}

ExecutionControlResult IbPaperExecutionPolicyAuthority::FenceSessionOwner(
    const ExecutionControlCommand& command)
{
    std::lock_guard<std::mutex> lock(m_mutex);
    ExecutionControlResult result = BeginControl(command);
    if (result.status == ExecutionCommandStatus::Rejected) return result;
    result.affectedCount = m_coordinator.FenceSessionOwner(
        command.context.agentId, command.context.sessionId);
    std::string blockReason;
    result.mutationBlocked = m_coordinator.IsMutationBlocked(&blockReason);
    if (result.mutationBlocked &&
        blockReason == "OMS_SESSION_FENCE_JOURNAL_FAILED")
    {
        result.status = ExecutionCommandStatus::Uncertain;
        result.reasonCode = blockReason;
    }
    return result;
}

ExecutionControlResult IbPaperExecutionPolicyAuthority::ReleaseSessionOwnerFence(
    const ExecutionControlCommand& command)
{
    std::lock_guard<std::mutex> lock(m_mutex);
    ExecutionControlResult result = BeginControl(command);
    if (result.status == ExecutionCommandStatus::Rejected) return result;
    if (!m_callbacks.correlationSnapshot)
    {
        result.status = ExecutionCommandStatus::Rejected;
        result.reasonCode = "IB_PAPER_CORRELATION_SNAPSHOT_CALLBACK_REQUIRED";
        return result;
    }
    const IBAuthoritativeCorrelationSnapshot snapshot = m_callbacks.correlationSnapshot();
    if (!snapshot.complete)
    {
        result.status = ExecutionCommandStatus::Rejected;
        result.reasonCode = snapshot.reasonCode.empty() ?
            "IB_PAPER_CORRELATION_SNAPSHOT_INCOMPLETE" : snapshot.reasonCode;
        return result;
    }
    std::size_t removed = 0;
    std::string reason;
    if (!m_coordinator.ReconcileOrderOwners(snapshot.activeOrderIds, true, removed, reason) ||
        !m_coordinator.AuditAndReleaseSessionOwnerFence(
            command.context.agentId, command.context.sessionId, true, reason))
    {
        result.status = ExecutionCommandStatus::Rejected;
        result.reasonCode = reason;
        result.affectedCount = removed;
        result.mutationBlocked = m_coordinator.IsMutationBlocked(nullptr);
        return result;
    }
    result.affectedCount = removed;
    result.mutationBlocked = m_coordinator.IsMutationBlocked(nullptr);
    return result;
}

ExecutionControlResult IbPaperExecutionPolicyAuthority::ReconcileAuthoritativeState(
    const ExecutionControlCommand& command)
{
    ExecutionControlResult result = BeginControl(command);
    if (result.status == ExecutionCommandStatus::Rejected) return result;
    std::size_t affected = 0;
    std::string reason;
    if (!ReconcileAuthoritativeState(affected, reason))
    {
        result.status = ExecutionCommandStatus::Rejected;
        result.reasonCode = reason;
        result.mutationBlocked = true;
        return result;
    }
    result.affectedCount = affected;
    result.mutationBlocked = false;
    return result;
}

ExecutionControlResult IbPaperExecutionPolicyAuthority::RecoveryAuditOwner(
    const ExecutionControlCommand& command)
{
    std::lock_guard<std::mutex> lock(m_mutex);
    ExecutionControlResult result = BeginControl(command);
    result.ownerAccount = command.context.account;
    result.ownerExecutionDomain = command.context.executionDomain;
    if (result.status == ExecutionCommandStatus::Rejected) return result;
    if (command.recoveryIngressFence == 0)
    {
        result.status = ExecutionCommandStatus::Rejected;
        result.reasonCode = "RECOVERY_INGRESS_FENCE_REQUIRED";
        return result;
    }
    if (command.recoveryIngressFence != 0)
    {
        std::string fenceReason;
        if (!m_coordinator.EnterRecoveryOnlyOwner(
                command.context.agentId, command.context.sessionId,
                command.recoveryIngressFence, fenceReason))
        {
            result.status = ExecutionCommandStatus::Rejected;
            result.reasonCode = fenceReason;
            return result;
        }
    }
    if (command.context.account != m_account ||
        !m_callbacks.recoveryAuditSnapshot)
    {
        result.status = ExecutionCommandStatus::Rejected;
        result.reasonCode = command.context.account != m_account ?
            "RECOVERY_OWNER_AUDIT_ACCOUNT_MISMATCH" :
            "RECOVERY_OWNER_AUDIT_CALLBACKS_REQUIRED";
        return result;
    }
    const IBAuthoritativeRecoveryAuditSnapshot recovery =
        m_callbacks.recoveryAuditSnapshot();
    return AuditRecoveryOwner(command, recovery);
}

ExecutionControlResult IbPaperExecutionPolicyAuthority::AuditRecoveryOwner(
    const ExecutionControlCommand& command,
    const IBAuthoritativeRecoveryAuditSnapshot& recovery)
{
    ExecutionControlResult result = BeginControl(command);
    result.ownerAccount = command.context.account;
    result.ownerExecutionDomain = command.context.executionDomain;
    if (result.status == ExecutionCommandStatus::Rejected) return result;
    const IBAuthoritativeCorrelationSnapshot& active = recovery.active;
    const IBAuthoritativeTerminalCorrelationSnapshot& terminal =
        recovery.terminal;
    const IBAuthoritativeRiskSnapshot& risk = recovery.risk;
    result.brokerConnectionEpoch = active.connectionEpoch;
    result.brokerActiveGeneration = active.generation;
    result.brokerTerminalGeneration = terminal.generation;
    result.brokerRiskGeneration = risk.generation;
    result.brokerAccountGeneration = risk.accountGeneration;
    result.brokerPositionGeneration = risk.positionsGeneration;
    result.brokerFxCashGeneration = risk.fxCashGeneration;
    result.brokerExposureGeneration = recovery.exposureGeneration;
    result.brokerTerminalExposureGeneration =
        recovery.terminalExposureGeneration;
    result.brokerRiskAbsorbedExposureGeneration =
        recovery.riskAbsorbedExposureGeneration;
    result.brokerGlobalActiveOrderCount = active.activeOrderIds.size();
    result.brokerPostFillRiskReconciliationPending =
        recovery.postFillRiskReconciliationPending;
    result.brokerRecoveryAuditBarrierComplete = recovery.barrierComplete;
    result.brokerRecoveryAuditNewConnectionEpochRequired =
        recovery.newConnectionEpochRequired;
    double positionQuantity = 0.0;
    double calculatedGross = 0.0;
    bool positionValuesValid = true;
    for (std::map<std::string, double>::const_iterator position =
             recovery.positionQuantities.begin();
         position != recovery.positionQuantities.end(); ++position)
    {
        if (position->first.empty() || !std::isfinite(position->second))
        {
            positionValuesValid = false;
            break;
        }
        positionQuantity += position->second;
        calculatedGross += std::fabs(position->second);
        if (!std::isfinite(positionQuantity) ||
            !std::isfinite(calculatedGross))
        {
            positionValuesValid = false;
            break;
        }
    }
    result.brokerPositionQuantity =
        CanonicalRecoveryDecimal(positionQuantity);
    result.brokerGrossAbsolutePosition =
        CanonicalRecoveryDecimal(risk.grossAbsolutePosition);
    if (recovery.newConnectionEpochRequired || !recovery.barrierComplete)
    {
        result.status = ExecutionCommandStatus::Rejected;
        result.reasonCode = recovery.reasonCode.empty() ?
            (recovery.newConnectionEpochRequired ?
                "IB_RECOVERY_AUDIT_NEW_CONNECTION_EPOCH_REQUIRED" :
                "RECOVERY_OWNER_BROKER_BARRIER_INCOMPLETE") :
            recovery.reasonCode;
        return result;
    }
    if (!active.complete || !terminal.complete ||
        active.connectionEpoch == 0 || active.generation == 0 ||
        terminal.generation == 0)
    {
        result.status = ExecutionCommandStatus::Rejected;
        result.reasonCode = !active.complete ?
            (active.reasonCode.empty() ?
                "RECOVERY_OWNER_ACTIVE_SNAPSHOT_INCOMPLETE" :
                active.reasonCode) :
            (!terminal.complete ?
                (terminal.reasonCode.empty() ?
                    "RECOVERY_OWNER_TERMINAL_SNAPSHOT_INCOMPLETE" :
                    terminal.reasonCode) :
                "RECOVERY_OWNER_SNAPSHOT_GENERATION_INVALID");
        return result;
    }
    if (terminal.connectionEpoch != active.connectionEpoch)
    {
        result.status = ExecutionCommandStatus::Rejected;
        result.reasonCode = "RECOVERY_OWNER_SNAPSHOT_EPOCH_DRIFT";
        return result;
    }
    if (!risk.complete || !risk.coherentRefreshComplete ||
        !risk.accountComplete || !risk.positionsComplete ||
        !risk.fxCashComplete || risk.connectionEpoch == 0 ||
        risk.generation == 0 || risk.accountGeneration == 0 ||
        risk.positionsGeneration == 0 || risk.fxCashGeneration == 0)
    {
        result.status = ExecutionCommandStatus::Rejected;
        result.reasonCode = "RECOVERY_OWNER_RISK_SNAPSHOT_INCOMPLETE";
        return result;
    }
    if (risk.connectionEpoch != active.connectionEpoch)
    {
        result.status = ExecutionCommandStatus::Rejected;
        result.reasonCode = "RECOVERY_OWNER_SNAPSHOT_EPOCH_DRIFT";
        return result;
    }
    if (recovery.postFillRiskReconciliationPending)
    {
        result.status = ExecutionCommandStatus::Rejected;
        result.reasonCode =
            "RECOVERY_OWNER_POST_FILL_RECONCILIATION_PENDING";
        return result;
    }
    if (terminal.exposureGeneration !=
            recovery.terminalExposureGeneration ||
        risk.riskAbsorbedExposureGeneration !=
            recovery.riskAbsorbedExposureGeneration ||
        recovery.terminalExposureGeneration >
            recovery.riskAbsorbedExposureGeneration ||
        recovery.riskAbsorbedExposureGeneration !=
            recovery.exposureGeneration)
    {
        result.status = ExecutionCommandStatus::Rejected;
        result.reasonCode = "RECOVERY_OWNER_EXPOSURE_NOT_ABSORBED";
        return result;
    }
    if (!positionValuesValid ||
        result.brokerPositionQuantity.empty() ||
        result.brokerGrossAbsolutePosition.empty() ||
        !std::isfinite(risk.grossAbsolutePosition) ||
        risk.grossAbsolutePosition < 0.0 ||
        calculatedGross != risk.grossAbsolutePosition)
    {
        result.status = ExecutionCommandStatus::Rejected;
        result.reasonCode = "RECOVERY_OWNER_POSITION_SNAPSHOT_INVALID";
        return result;
    }
    if (positionQuantity != 0.0 || risk.grossAbsolutePosition != 0.0)
    {
        result.status = ExecutionCommandStatus::Rejected;
        result.reasonCode = "RECOVERY_OWNER_POSITION_NOT_FLAT";
        return result;
    }
    if (terminal.terminalStatusesByCorrelation.size() !=
        terminal.terminalOrderIdsByCorrelation.size())
    {
        result.status = ExecutionCommandStatus::Rejected;
        result.reasonCode = "RECOVERY_OWNER_TERMINAL_CORRELATION_INVALID";
        return result;
    }
    std::set<long> correlatedActiveIds;
    std::map<std::string, long> correlations =
        active.activeOrderIdsByCorrelation;
    for (std::map<std::string, long>::const_iterator item =
             active.activeOrderIdsByCorrelation.begin();
         item != active.activeOrderIdsByCorrelation.end(); ++item)
    {
        if (item->first.empty() || item->second < 0 ||
            active.activeOrderIds.find(item->second) ==
                active.activeOrderIds.end() ||
            !correlatedActiveIds.insert(item->second).second)
        {
            result.status = ExecutionCommandStatus::Rejected;
            result.reasonCode = "RECOVERY_OWNER_ACTIVE_CORRELATION_INVALID";
            return result;
        }
    }
    std::map<long, std::string> terminalStatuses;
    for (std::map<std::string, long>::const_iterator item =
             terminal.terminalOrderIdsByCorrelation.begin();
         item != terminal.terminalOrderIdsByCorrelation.end(); ++item)
    {
        const std::map<std::string, std::string>::const_iterator status =
            terminal.terminalStatusesByCorrelation.find(item->first);
        const std::map<std::string, long>::const_iterator duplicate =
            correlations.find(item->first);
        if (item->first.empty() || item->second < 0 ||
            status == terminal.terminalStatusesByCorrelation.end() ||
            (status->second != "Filled" &&
             status->second != "Cancelled" &&
             status->second != "ApiCancelled" &&
             status->second != "Inactive" &&
             status->second != "Rejected") ||
            (duplicate != correlations.end() &&
             duplicate->second != item->second) ||
            !correlations.insert(*item).second)
        {
            result.status = ExecutionCommandStatus::Rejected;
            result.reasonCode = "RECOVERY_OWNER_TERMINAL_CORRELATION_INVALID";
            return result;
        }
        if (active.activeOrderIds.find(item->second) !=
            active.activeOrderIds.end())
        {
            result.status = ExecutionCommandStatus::Rejected;
            result.reasonCode =
                "RECOVERY_OWNER_ACTIVE_TERMINAL_ORDER_CONFLICT";
            return result;
        }
        // IB can report zero for more than one completed order. Preserve
        // each H1 correlation as positive place evidence, but never expose
        // the non-unique zero ID as evidence for resolving a cancel.
        if (item->second == 0) continue;
        if (!terminalStatuses.insert(
                std::make_pair(item->second, status->second)).second)
        {
            result.status = ExecutionCommandStatus::Rejected;
            result.reasonCode = "RECOVERY_OWNER_TERMINAL_CORRELATION_INVALID";
            return result;
        }
    }
    std::size_t resolvedPlaces = 0;
    std::size_t resolvedCancels = 0;
    std::size_t removedOwners = 0;
    std::string reason;
    if (!m_coordinator.ResolveUncertainPlaceCommands(
            correlations, true, resolvedPlaces, reason, false) ||
        !m_coordinator.ResolveUncertainCancelCommands(
            active.activeOrderIds, true, terminalStatuses,
            terminal.executionOrderIds, true, resolvedCancels, reason) ||
        !m_coordinator.ReconcileOrderOwners(
            active.activeOrderIds, true, removedOwners, reason) ||
        !m_coordinator.AuditRecoveryOwner(
            active.activeOrderIds, true, command.context,
            result.ownerActiveOrderCount,
            result.ownerUncertainCommandCount, reason))
    {
        result.status = ExecutionCommandStatus::Rejected;
        result.reasonCode = reason.empty() ?
            "RECOVERY_OWNER_AUDIT_FAILED" : reason;
        return result;
    }
    result.ownerAuditAuthoritative = true;
    result.ownerAuditComplete = true;
    result.affectedCount = result.ownerActiveOrderCount;
    result.mutationBlocked = m_coordinator.IsMutationBlocked(nullptr);
    if (!active.activeOrderIds.empty() ||
        result.ownerActiveOrderCount != 0)
    {
        result.status = ExecutionCommandStatus::Rejected;
        result.reasonCode = "RECOVERY_OWNER_GLOBAL_ACTIVE_ORDERS";
        return result;
    }
    if (result.ownerUncertainCommandCount != 0)
    {
        result.status = ExecutionCommandStatus::Rejected;
        result.reasonCode = "RECOVERY_OWNER_UNCERTAIN_COMMANDS_REMAIN";
        return result;
    }
    result.status = ExecutionCommandStatus::Accepted;
    result.reasonCode = "RECOVERY_OWNER_ZERO_CONFIRMED";
    return result;
}

ExecutionControlResult
IbPaperExecutionPolicyAuthority::TerminalizeRecoveryOwner(
    const ExecutionControlCommand& command)
{
    ExecutionControlResult result = BeginControl(command);
    result.targetCommandId = command.targetCommandId;
    result.ownerAccount = command.context.account;
    result.ownerExecutionDomain = command.context.executionDomain;
    if (result.status == ExecutionCommandStatus::Rejected) return result;
    if (command.recoveryIngressFence == 0 ||
        command.targetCommandId.empty() ||
        command.targetCommandId.size() > 128 ||
        !CanonicalSha256(command.terminalPreliminaryReceiptSha256))
    {
        result.status = ExecutionCommandStatus::Rejected;
        result.reasonCode = "PAPER_TERMINALIZATION_BINDING_INVALID";
        return result;
    }
    if (command.context.account != m_account ||
        !m_callbacks.beginTerminalRecoveryAudit ||
        !m_callbacks.commitTerminalRecoveryAudit)
    {
        result.status = ExecutionCommandStatus::Rejected;
        result.reasonCode = command.context.account != m_account ?
            "RECOVERY_OWNER_AUDIT_ACCOUNT_MISMATCH" :
            "PAPER_TERMINALIZATION_CALLBACKS_REQUIRED";
        return result;
    }
    {
        std::lock_guard<std::mutex> lock(m_mutex);
        std::string fenceReason;
        // The runtime terminal callback owns construction of the V2 fence
        // binding (service/broker epochs and socket identity).  The policy
        // layer cannot safely synthesize those values, so it only records
        // the recovery-only ingress fence here.  BeginTerminalRecoveryAudit
        // atomically creates and projects the bound terminal fence before
        // any broker terminalization work.
        if (!m_coordinator.EnterRecoveryOnlyOwner(
                command.context.agentId, command.context.sessionId,
                command.recoveryIngressFence, fenceReason))
        {
            result.status = ExecutionCommandStatus::Rejected;
            result.reasonCode = fenceReason.empty() ?
                "PAPER_TERMINALIZATION_FENCE_FAILED" : fenceReason;
            return result;
        }
    }
    IBAuthoritativeRecoveryAuditSnapshot frozen;
    ExecutionControlResult terminalState;
    std::string reason;
    if (!m_callbacks.beginTerminalRecoveryAudit(
            command, frozen, terminalState, reason))
    {
        terminalState.commandId = command.context.toolCallId;
        terminalState.targetCommandId = command.targetCommandId;
        terminalState.ownerAccount = command.context.account;
        terminalState.ownerExecutionDomain =
            command.context.executionDomain;
        terminalState.status = ExecutionCommandStatus::Rejected;
        terminalState.reasonCode = reason.empty() ?
            "PAPER_TERMINALIZATION_BOUNDARY_FAILED" : reason;
        return terminalState;
    }
    if (terminalState.terminalRuntimeVerified &&
        terminalState.terminalLatchDurable)
    {
        terminalState.commandId = command.context.toolCallId;
        terminalState.targetCommandId = command.targetCommandId;
        terminalState.terminalReplay = true;
        return terminalState;
    }
    {
        std::lock_guard<std::mutex> lock(m_mutex);
        result = AuditRecoveryOwner(command, frozen);
    }
    result.targetCommandId = command.targetCommandId;
    if (result.status != ExecutionCommandStatus::Accepted)
        return result;
    ExecutionControlResult committed;
    if (!m_callbacks.commitTerminalRecoveryAudit(
            command, result, committed, reason))
    {
        result.status = ExecutionCommandStatus::Rejected;
        result.reasonCode = reason.empty() ?
            "PAPER_TERMINALIZATION_COMMIT_FAILED" : reason;
        return result;
    }
    committed.commandId = command.context.toolCallId;
    committed.targetCommandId = command.targetCommandId;
    return committed;
}

ExecutionCommandResult IbPaperExecutionPolicyAuthority::ReadAuthoritativeState(
    const ExecutionReadCommand& command)
{
    if (!ValidContext(command.context))
        return Reject(command.context, -1, "IB_PAPER_CONTEXT_REQUIRED");
    if (!m_callbacks.authoritativeRead)
        return Reject(command.context, -1, "EXECUTION_READ_UNAVAILABLE");
    return m_callbacks.authoritativeRead(command);
}

bool IbPaperExecutionPolicyAuthority::ReconcileAuthoritativeState(
    std::size_t& affectedCount, std::string& reason)
{
    std::lock_guard<std::mutex> lock(m_mutex);
    affectedCount = 0;
    if (!m_callbacks.correlationSnapshot)
    {
        reason = "IB_PAPER_CORRELATION_SNAPSHOT_CALLBACK_REQUIRED";
        return false;
    }
    const IBAuthoritativeCorrelationSnapshot snapshot =
        m_callbacks.correlationSnapshot();
    if (!snapshot.complete)
    {
        reason = snapshot.reasonCode.empty() ?
            "IB_PAPER_CORRELATION_SNAPSHOT_INCOMPLETE" : snapshot.reasonCode;
        return false;
    }
    if (!m_callbacks.terminalCorrelationSnapshot)
    {
        reason = "IB_PAPER_TERMINAL_CORRELATION_SNAPSHOT_CALLBACK_REQUIRED";
        return false;
    }
    const IBAuthoritativeTerminalCorrelationSnapshot terminal =
        m_callbacks.terminalCorrelationSnapshot();
    if (!terminal.complete)
    {
        reason = terminal.reasonCode.empty() ?
            "IB_PAPER_TERMINAL_CORRELATION_SNAPSHOT_INCOMPLETE" :
            terminal.reasonCode;
        return false;
    }
    if (terminal.connectionEpoch != snapshot.connectionEpoch)
    {
        reason = "IB_PAPER_ACTIVE_TERMINAL_EPOCH_MISMATCH";
        return false;
    }
    if (snapshot.connectionEpoch == 0 || snapshot.generation == 0 ||
        terminal.generation == 0)
    {
        reason = "IB_PAPER_ACTIVE_TERMINAL_GENERATION_INVALID";
        return false;
    }
    std::map<std::string, long> correlations =
        snapshot.activeOrderIdsByCorrelation;
    std::set<long> correlatedActiveIds;
    for (std::map<std::string, long>::const_iterator item =
             snapshot.activeOrderIdsByCorrelation.begin();
         item != snapshot.activeOrderIdsByCorrelation.end(); ++item)
    {
        if (item->first.empty() || item->second < 0 ||
            snapshot.activeOrderIds.find(item->second) ==
                snapshot.activeOrderIds.end() ||
            !correlatedActiveIds.insert(item->second).second)
        {
            reason = "IB_PAPER_ACTIVE_CORRELATION_INVALID";
            return false;
        }
    }
    if (terminal.terminalStatusesByCorrelation.size() !=
        terminal.terminalOrderIdsByCorrelation.size())
    {
        reason = "IB_PAPER_TERMINAL_CORRELATION_INVALID";
        return false;
    }
    std::map<long, std::string> terminalStatusesByOrderId;
    for (std::map<std::string, long>::const_iterator item =
             terminal.terminalOrderIdsByCorrelation.begin();
         item != terminal.terminalOrderIdsByCorrelation.end(); ++item)
    {
        const std::map<std::string, std::string>::const_iterator status =
            terminal.terminalStatusesByCorrelation.find(item->first);
        if (item->first.empty() || item->second < 0 ||
            status == terminal.terminalStatusesByCorrelation.end() ||
            (status->second != "Filled" &&
             status->second != "Cancelled" &&
             status->second != "ApiCancelled" &&
             status->second != "Inactive" &&
             status->second != "Rejected") ||
            correlations.find(item->first) != correlations.end() ||
            !correlations.insert(*item).second)
        {
            reason = "IB_PAPER_TERMINAL_CORRELATION_INVALID";
            return false;
        }
        if (snapshot.activeOrderIds.find(item->second) !=
            snapshot.activeOrderIds.end())
        {
            reason = "IB_PAPER_ACTIVE_TERMINAL_ORDER_CONFLICT";
            return false;
        }
        // IB can report zero for multiple completed orders. The H1
        // correlation still proves an uncertain place reached the broker,
        // but zero is not a unique cancel target.
        if (item->second == 0) continue;
        const std::pair<std::map<long, std::string>::iterator, bool> inserted =
            terminalStatusesByOrderId.insert(
                std::make_pair(item->second, status->second));
        if (!inserted.second)
        {
            reason = "IB_PAPER_TERMINAL_ORDER_ID_CONFLICT";
            return false;
        }
    }
    std::size_t resolved = 0;
    if (!m_coordinator.ResolveUncertainPlaceCommands(
            correlations, true, resolved, reason, false))
        return false;
    std::size_t resolvedCancels = 0;
    if (!m_coordinator.ResolveUncertainCancelCommands(
            snapshot.activeOrderIds, true, terminalStatusesByOrderId,
            terminal.executionOrderIds, true, resolvedCancels, reason))
        return false;
    std::string recoveryBlock;
    if (m_coordinator.IsMutationBlocked(&recoveryBlock) &&
        recoveryBlock == "RECOVERY_RECONCILE_REQUIRED")
    {
        reason = recoveryBlock;
        return false;
    }
    std::size_t removed = 0;
    if (!m_coordinator.ReconcileOrderOwners(
            snapshot.activeOrderIds, true, removed, reason))
        return false;
    m_coordinator.ResolveProjectionBlockAfterAuthoritativeResync();
    affectedCount = resolved + resolvedCancels + removed;
    if (m_coordinator.IsMutationBlocked(&reason) &&
        reason != "IB_PAPER_BROKER_RECONNECT_PENDING") return false;
    reason.clear();
    return true;
}
