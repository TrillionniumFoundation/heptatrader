#include "agent_os_runtime_config.h"

#include <cstdint>
#include <cstdlib>
#include <limits>

namespace
{
std::string ReadString(const std::map<std::string, std::string>& values, const char* key)
{
    const std::map<std::string, std::string>::const_iterator it = values.find(key);
    return it == values.end() ? std::string() : it->second;
}

bool ParseUnsigned(const std::string& value, std::uint64_t maximum,
                   std::uint64_t& parsed)
{
    if (value.empty()) return false;
    std::uint64_t result = 0;
    for (std::size_t i = 0; i < value.size(); ++i)
    {
        const unsigned char character = static_cast<unsigned char>(value[i]);
        if (character < static_cast<unsigned char>('0') ||
            character > static_cast<unsigned char>('9')) return false;
        const std::uint64_t digit = static_cast<std::uint64_t>(character - '0');
        if (digit > maximum) return false;
        if (result > (maximum - digit) / 10) return false;
        result = result * 10 + digit;
    }
    parsed = result;
    return true;
}

bool ParseSignedInt(const std::string& value, int minimum, int maximum, int& parsed)
{
    if (value.empty()) return false;
    std::size_t offset = 0;
    bool negative = false;
    if (value[0] == '-')
    {
        negative = true;
        offset = 1;
    }
    if (offset == value.size()) return false;
    std::uint64_t magnitude = 0;
    const std::uint64_t limit = negative ?
        static_cast<std::uint64_t>(-(static_cast<std::int64_t>(minimum) + 1)) + 1 :
        static_cast<std::uint64_t>(maximum);
    for (; offset < value.size(); ++offset)
    {
        const unsigned char character = static_cast<unsigned char>(value[offset]);
        if (character < static_cast<unsigned char>('0') ||
            character > static_cast<unsigned char>('9')) return false;
        const std::uint64_t digit = static_cast<std::uint64_t>(character - '0');
        if (digit > limit) return false;
        if (magnitude > (limit - digit) / 10) return false;
        magnitude = magnitude * 10 + digit;
    }
    const std::int64_t signedValue = negative ?
        -static_cast<std::int64_t>(magnitude) : static_cast<std::int64_t>(magnitude);
    if (signedValue < minimum || signedValue > maximum) return false;
    parsed = static_cast<int>(signedValue);
    return true;
}

bool ReadInt(const std::map<std::string, std::string>& values, const char* key,
             int fallback, int minimum, int maximum, int& out)
{
    const std::map<std::string, std::string>::const_iterator it = values.find(key);
    if (it == values.end())
    {
        out = fallback;
        return true;
    }
    return ParseSignedInt(it->second, minimum, maximum, out);
}

bool ReadUnsigned(const std::map<std::string, std::string>& values, const char* key,
                  std::uint64_t fallback, std::uint64_t minimum,
                  std::uint64_t maximum, std::uint64_t& out)
{
    const std::map<std::string, std::string>::const_iterator it = values.find(key);
    if (it == values.end())
    {
        out = fallback;
        return true;
    }
    std::uint64_t parsed = 0;
    if (!ParseUnsigned(it->second, maximum, parsed) || parsed < minimum) return false;
    out = parsed;
    return true;
}

bool ReadBoundedSize(const std::map<std::string, std::string>& values,
                     const char* key, int fallback, int minimum, int maximum,
                     std::size_t& out)
{
    int parsed = 0;
    if (!ReadInt(values, key, fallback, minimum, maximum, parsed)) return false;
    out = static_cast<std::size_t>(parsed);
    return true;
}

void MarkInvalid(AgentOsRuntimeConfig& config, const char* key)
{
    if (config.valid)
    {
        config.valid = false;
        config.invalidReason = std::string("AGENT_OS_RUNTIME_CONFIG_INVALID:") + key;
    }
}
}

bool AgentOsRuntimeConfig::ToolServerEnabled() const
{
    return !toolSocket.empty() || toolListenFd >= 0;
}

bool AgentOsRuntimeConfig::SupervisorEnabled() const
{
    return !supervisorSocket.empty() || supervisorListenFd >= 0;
}

bool AgentOsRuntimeConfig::Validate(std::string& reason) const
{
    if (!valid)
    {
        reason = invalidReason.empty() ?
            "AGENT_OS_RUNTIME_CONFIG_INVALID" : invalidReason;
        return false;
    }
    reason.clear();
    return true;
}

AgentOsRuntimeConfig AgentOsRuntimeConfig::FromEnvironment(int currentPid, std::uint32_t currentUid)
{
    static const char* keys[] = {
        "HEPTA_TOOL_SOCKET",
        "HEPTA_TOOL_LISTEN_FD",
        "HEPTA_TOOL_SERVER_WORKERS",
        "HEPTA_TOOL_SERVER_MAX_PENDING",
        "HEPTA_TOOL_SERVER_MAX_CONCURRENT_PER_OWNER",
        "HEPTA_TOOL_SERVER_MAX_PENDING_PER_OWNER",
        "HEPTA_TOOL_SERVER_INGRESS_WORKERS",
        "HEPTA_TOOL_SUPERVISOR_SOCKET",
        "HEPTA_TOOL_SUPERVISOR_LISTEN_FD",
        "HEPTA_TOOL_SUPERVISOR_LEASE_STORE",
        "HEPTA_TOOL_SUPERVISOR_LEASE_KEY_FILE",
        "HEPTA_TOOL_SUPERVISOR_AUDIT_JOURNAL",
        "HEPTA_TOOL_SUPERVISOR_UID",
        "HEPTA_TOOL_AGENT_UID",
        "HEPTA_TOOL_SUPERVISOR_MAX_TTL_SEC",
        "LISTEN_PID",
        "LISTEN_FDS",
        "LISTEN_FDNAMES",
        "CREDENTIALS_DIRECTORY"
    };
    std::map<std::string, std::string> values;
    for (std::size_t i = 0; i < sizeof(keys) / sizeof(keys[0]); ++i)
    {
        const char* value = std::getenv(keys[i]);
        if (value != nullptr) values[keys[i]] = value;
    }
    return FromValues(values, currentPid, currentUid);
}

AgentOsRuntimeConfig AgentOsRuntimeConfig::FromValues(
    const std::map<std::string, std::string>& values,
    int currentPid,
    std::uint32_t currentUid)
{
    AgentOsRuntimeConfig config;
    // String socket/path variables use an explicit empty value as the
    // documented disabled/unset sentinel (the legacy unit files intentionally
    // export empty socket paths).  Numeric variables are handled by the
    // presence-aware readers below, where an explicit empty value is invalid
    // instead of silently selecting a fallback.
    config.toolSocket = ReadString(values, "HEPTA_TOOL_SOCKET");
    if (!ReadInt(values, "HEPTA_TOOL_LISTEN_FD", -1, -1,
                 std::numeric_limits<int>::max(), config.toolListenFd))
        MarkInvalid(config, "HEPTA_TOOL_LISTEN_FD");
    if (!ReadBoundedSize(values, "HEPTA_TOOL_SERVER_WORKERS", 4, 1, 64,
                         config.toolExecutionWorkers))
        MarkInvalid(config, "HEPTA_TOOL_SERVER_WORKERS");
    if (!ReadBoundedSize(values, "HEPTA_TOOL_SERVER_MAX_PENDING", 32, 1, 1024,
                         config.toolMaxPending))
        MarkInvalid(config, "HEPTA_TOOL_SERVER_MAX_PENDING");
    if (!ReadBoundedSize(values, "HEPTA_TOOL_SERVER_MAX_CONCURRENT_PER_OWNER", 1,
                         1, 64, config.toolMaxConcurrentPerOwner))
        MarkInvalid(config, "HEPTA_TOOL_SERVER_MAX_CONCURRENT_PER_OWNER");
    if (!ReadBoundedSize(values, "HEPTA_TOOL_SERVER_MAX_PENDING_PER_OWNER", 8,
                         1, 1024, config.toolMaxPendingPerOwner))
        MarkInvalid(config, "HEPTA_TOOL_SERVER_MAX_PENDING_PER_OWNER");
    if (!ReadBoundedSize(values, "HEPTA_TOOL_SERVER_INGRESS_WORKERS", 2, 1, 16,
                         config.toolIngressWorkers))
        MarkInvalid(config, "HEPTA_TOOL_SERVER_INGRESS_WORKERS");

    config.supervisorSocket = ReadString(values, "HEPTA_TOOL_SUPERVISOR_SOCKET");
    if (!ReadInt(values, "HEPTA_TOOL_SUPERVISOR_LISTEN_FD", -1, -1,
                 std::numeric_limits<int>::max(), config.supervisorListenFd))
        MarkInvalid(config, "HEPTA_TOOL_SUPERVISOR_LISTEN_FD");
    int listenPid = -1;
    int descriptorCount = 0;
    if (!ReadInt(values, "LISTEN_PID", -1, -1, std::numeric_limits<int>::max(), listenPid))
        MarkInvalid(config, "LISTEN_PID");
    // systemd socket activation needs only a small, bounded descriptor set
    // here (the gateway consumes at most the two named sockets).  Reject an
    // unbounded/malformed environment value before iterating over names.
    if (!ReadInt(values, "LISTEN_FDS", 0, 0, 64, descriptorCount))
        MarkInvalid(config, "LISTEN_FDS");
    if (listenPid == currentPid)
    {
        const std::string names = ReadString(values, "LISTEN_FDNAMES");
        std::size_t begin = 0;
        for (int index = 0; index < descriptorCount && begin <= names.size(); ++index)
        {
            const std::size_t end = names.find(':', begin);
            const std::string name = names.substr(begin,
                end == std::string::npos ? std::string::npos : end - begin);
            if (name == "hepta-tool") config.toolListenFd = 3 + index;
            if (name == "hepta-supervisor") config.supervisorListenFd = 3 + index;
            if (end == std::string::npos) break;
            begin = end + 1;
        }
        if (descriptorCount == 1 && names.empty() && config.supervisorListenFd < 0)
            config.supervisorListenFd = 3;
    }
    config.supervisorLeaseStorePath = ReadString(values, "HEPTA_TOOL_SUPERVISOR_LEASE_STORE");
    config.supervisorLeaseKeyPath = ReadString(values, "HEPTA_TOOL_SUPERVISOR_LEASE_KEY_FILE");
    // All gateway instances share one passive, root-provisioned interlock.
    // This deliberately serializes terminal store cleanup against every
    // supervisor instead of placing a replaceable lock in a gateway-owned
    // StateDirectory.
    config.supervisorLeaseCleanupLockPath =
        "/run/hepta-agent/session-lease-terminal-cleanup.lock";
    if (config.supervisorLeaseKeyPath.empty())
    {
        const std::string credentialsDirectory = ReadString(values, "CREDENTIALS_DIRECTORY");
        if (!credentialsDirectory.empty())
            config.supervisorLeaseKeyPath = credentialsDirectory + "/hepta-supervisor-lease-key";
    }
    config.supervisorAuditJournalPath = ReadString(values, "HEPTA_TOOL_SUPERVISOR_AUDIT_JOURNAL");
    std::uint64_t uid = 0;
    if (!ReadUnsigned(values, "HEPTA_TOOL_SUPERVISOR_UID", currentUid, 0,
                      std::numeric_limits<std::uint32_t>::max(), uid))
        MarkInvalid(config, "HEPTA_TOOL_SUPERVISOR_UID");
    else
        config.supervisorUid = static_cast<std::uint32_t>(uid);
    if (!ReadUnsigned(values, "HEPTA_TOOL_AGENT_UID", currentUid, 0,
                      std::numeric_limits<std::uint32_t>::max(), uid))
        MarkInvalid(config, "HEPTA_TOOL_AGENT_UID");
    else
        config.agentUid = static_cast<std::uint32_t>(uid);
    std::uint64_t ttlSeconds = 0;
    if (!ReadUnsigned(values, "HEPTA_TOOL_SUPERVISOR_MAX_TTL_SEC", 86400, 60,
                      std::numeric_limits<std::uint64_t>::max() / 1000, ttlSeconds))
        MarkInvalid(config, "HEPTA_TOOL_SUPERVISOR_MAX_TTL_SEC");
    else
        config.supervisorMaxTtlMs = ttlSeconds * 1000;
    return config;
}
