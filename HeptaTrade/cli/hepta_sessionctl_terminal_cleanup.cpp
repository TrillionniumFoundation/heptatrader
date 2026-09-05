#include "hepta_sessionctl_terminal_cleanup.h"

#include "../tool_host/session_supervisor_lease_store.h"

#include <cerrno>
#include <cstdlib>
#include <iostream>
#include <locale>
#include <limits>
#include <map>
#include <unistd.h>

namespace
{
const char* kOperation = "terminal-cleanup-hsl5-paper";

bool ParseUnsigned(const std::string& value, std::uint64_t maximum,
                   std::uint64_t& parsed)
{
    if (value.empty() || (value.size() > 1 && value[0] == '0')) return false;
    for (std::string::const_iterator it = value.begin(); it != value.end(); ++it)
        if (*it < '0' || *it > '9') return false;
    char* end = nullptr;
    errno = 0;
    const unsigned long long number = std::strtoull(value.c_str(), &end, 10);
    if (errno != 0 || end == value.c_str() || *end != '\0' ||
        number > maximum)
        return false;
    parsed = static_cast<std::uint64_t>(number);
    return true;
}

bool SafeIdentity(const std::string& value)
{
    if (value.empty() || value.size() > 128) return false;
    for (std::string::const_iterator it = value.begin(); it != value.end(); ++it)
    {
        const unsigned char byte = static_cast<unsigned char>(*it);
        if (byte < 0x21 || byte > 0x7e) return false;
    }
    return true;
}

bool CanonicalSha256(const std::string& value)
{
    if (value.size() != 71 || value.compare(0, 7, "sha256:") != 0)
        return false;
    for (std::size_t i = 7; i < value.size(); ++i)
        if (std::string("0123456789abcdef").find(value[i]) ==
            std::string::npos)
            return false;
    return true;
}

bool Require(const std::map<std::string, std::string>& options,
             const char* name, std::string& value, std::string& reason)
{
    const std::map<std::string, std::string>::const_iterator found =
        options.find(name);
    if (found == options.end() || found->second.empty())
    {
        reason = std::string("MISSING_OPTION:") + name;
        return false;
    }
    value = found->second;
    return true;
}

std::string JsonEscape(const std::string& value)
{
    std::string escaped;
    for (std::string::const_iterator it = value.begin(); it != value.end(); ++it)
    {
        if (*it == '\\' || *it == '"') escaped.push_back('\\');
        escaped.push_back(*it);
    }
    return escaped;
}
}

bool HeptaSessionCtlTerminalCleanup::IsCommand(int argc, char** argv)
{
    return argc >= 2 && argv != nullptr && argv[1] != nullptr &&
        std::string(argv[1]) == kOperation;
}

bool HeptaSessionCtlTerminalCleanup::Parse(
    int argc, char** argv, HeptaSessionCtlTerminalCleanupCommand& command,
    std::string& reason)
{
    command = HeptaSessionCtlTerminalCleanupCommand();
    if (!IsCommand(argc, argv))
    {
        reason = "INVALID_COMMAND";
        return false;
    }
    std::map<std::string, std::string> options;
    int index = 2;
    while (index < argc)
    {
        const std::string option = argv[index++];
        if (option.compare(0, 2, "--") != 0 || index >= argc ||
            options.find(option) != options.end())
        {
            reason = "INVALID_TERMINAL_CLEANUP_OPTION:" + option;
            return false;
        }
        options[option] = argv[index++];
    }
    if (options.size() != 15 ||
        !Require(options, "--store", command.storePath, reason) ||
        !Require(options, "--key-file", command.keyPath, reason) ||
        !Require(options, "--backup", command.backupPath, reason) ||
        !Require(options, "--lock-file", command.cleanupLockPath, reason) ||
        !Require(options, "--expected-issuer", command.expectedIssuer, reason) ||
        !Require(options, "--expected-agent-id", command.expectedAgentId, reason) ||
        !Require(options, "--expected-pre-store-sha256",
                 command.expectedPreStoreSha256, reason) ||
        !Require(options, "--expected-key-file-sha256",
                 command.expectedKeyFileSha256, reason))
    {
        if (reason.empty()) reason = "INVALID_TERMINAL_CLEANUP_OPTIONS";
        return false;
    }
    std::string peerUid;
    std::string sourceUid;
    std::string sourceGid;
    std::string sourceMode;
    std::string keyUid;
    std::string keyGid;
    std::string keyMode;
    std::uint64_t parsedPeerUid = 0;
    std::uint64_t parsedSourceUid = 0;
    std::uint64_t parsedSourceGid = 0;
    std::uint64_t parsedKeyUid = 0;
    std::uint64_t parsedKeyGid = 0;
    if (!Require(options, "--expected-peer-uid", peerUid, reason) ||
        !Require(options, "--expected-source-uid", sourceUid, reason) ||
        !Require(options, "--expected-source-gid", sourceGid, reason) ||
        !Require(options, "--expected-source-mode", sourceMode, reason) ||
        !Require(options, "--expected-key-uid", keyUid, reason) ||
        !Require(options, "--expected-key-gid", keyGid, reason) ||
        !Require(options, "--expected-key-mode", keyMode, reason) ||
        !ParseUnsigned(peerUid, std::numeric_limits<std::uint32_t>::max(),
                       parsedPeerUid) ||
        !ParseUnsigned(sourceUid, std::numeric_limits<std::uint32_t>::max(),
                       parsedSourceUid) ||
        !ParseUnsigned(sourceGid, std::numeric_limits<std::uint32_t>::max(),
                       parsedSourceGid) ||
        !ParseUnsigned(keyUid, std::numeric_limits<std::uint32_t>::max(),
                       parsedKeyUid) ||
        !ParseUnsigned(keyGid, std::numeric_limits<std::uint32_t>::max(),
                       parsedKeyGid) ||
        sourceMode != "0600" ||
        keyMode != "0400" ||
        command.storePath.empty() || command.storePath[0] != '/' ||
        command.keyPath.empty() || command.keyPath[0] != '/' ||
        command.backupPath.empty() || command.backupPath[0] != '/' ||
        command.cleanupLockPath.empty() || command.cleanupLockPath[0] != '/' ||
        !SafeIdentity(command.expectedIssuer) ||
        !SafeIdentity(command.expectedAgentId) ||
        !CanonicalSha256(command.expectedPreStoreSha256) ||
        !CanonicalSha256(command.expectedKeyFileSha256))
    {
        if (reason.empty()) reason = "INVALID_TERMINAL_CLEANUP_OPTIONS";
        return false;
    }
    command.expectedPeerUid = static_cast<std::uint32_t>(parsedPeerUid);
    command.expectedSourceUid = static_cast<std::uint32_t>(parsedSourceUid);
    command.expectedSourceGid = static_cast<std::uint32_t>(parsedSourceGid);
    command.expectedSourceMode = 0600;
    command.expectedKeyUid = static_cast<std::uint32_t>(parsedKeyUid);
    command.expectedKeyGid = static_cast<std::uint32_t>(parsedKeyGid);
    command.expectedKeyMode = 0400;
    reason.clear();
    return true;
}

int HeptaSessionCtlTerminalCleanup::Run(int argc, char** argv)
{
    HeptaSessionCtlTerminalCleanupCommand command;
    std::string reason;
    if (!Parse(argc, argv, command, reason))
    {
        std::cerr << reason << '\n' << Usage() << '\n';
        return 2;
    }
    if (::geteuid() != 0)
    {
        std::cerr << "TERMINAL_CLEANUP_ROOT_REQUIRED\n";
        return 2;
    }

    SessionSupervisorLegacyPaperCleanupRequest request;
    request.expectedIssuer = command.expectedIssuer;
    request.expectedAgentId = command.expectedAgentId;
    request.expectedPeerUid = command.expectedPeerUid;
    request.expectedPreStoreSha256 =
        command.expectedPreStoreSha256.substr(7);
    request.backupPath = command.backupPath;
    request.cleanupLockPath = command.cleanupLockPath;
    request.expectedLockUid = 0;
    request.expectedLockGid = 0;
    request.expectedSourceUid = command.expectedSourceUid;
    request.expectedSourceGid = command.expectedSourceGid;
    request.expectedSourceMode = command.expectedSourceMode;
    request.expectedKeyUid = command.expectedKeyUid;
    request.expectedKeyGid = command.expectedKeyGid;
    request.expectedKeyMode = command.expectedKeyMode;
    request.expectedKeySha256 = command.expectedKeyFileSha256.substr(7);
    SessionSupervisorLegacyPaperCleanupResult result;
    SessionSupervisorLeaseStore store;
    // The cleanup command writes a JSON response directly to stdout; avoid
    // locale-dependent integer grouping in that wire-facing output.
    std::cout.imbue(std::locale::classic());
    if (!store.MigrateHsl5PaperForTerminalCleanup(
            command.storePath, command.keyPath, request, result, reason))
    {
        std::cout << "{\"accepted\":false,\"reason_code\":\""
                  << JsonEscape(reason) << "\",\"retired_records\":0,"
                  << "\"pre_store_sha256\":\"\","
                  << "\"post_store_sha256\":\"\","
                  << "\"backup_store_sha256\":\"\","
                  << "\"already_migrated\":false}\n";
        return 4;
    }
    std::cout << "{\"accepted\":true,\"reason_code\":\"OK\","
              << "\"retired_records\":" << result.retiredRecords
              << ",\"pre_store_sha256\":\"sha256:"
              << result.preStoreSha256
              << "\",\"post_store_sha256\":\"sha256:"
              << result.postStoreSha256
              << "\",\"backup_store_sha256\":\"sha256:"
              << result.preStoreSha256
              << "\",\"already_migrated\":"
              << (result.alreadyMigrated ? "true" : "false") << "}\n";
    return 0;
}

const char* HeptaSessionCtlTerminalCleanup::Usage()
{
    return "usage: hepta-sessionctl terminal-cleanup-hsl5-paper "
           "--store ABSOLUTE_PATH --key-file ABSOLUTE_PATH "
           "--backup ABSOLUTE_PATH --lock-file ABSOLUTE_PATH "
           "--expected-issuer ISSUER --expected-agent-id AGENT "
           "--expected-peer-uid UID --expected-source-uid UID "
           "--expected-source-gid GID --expected-source-mode 0600 "
           "--expected-key-uid UID --expected-key-gid GID "
           "--expected-key-mode 0400 "
           "--expected-key-file-sha256 SHA256 "
           "--expected-pre-store-sha256 SHA256";
}
