#include "native_tool_client.h"
#include "native_tool_discovery_contract.h"

#include "../tools/trading_tool_wire_contract.h"

#include "../tool_host/unix_tool_client.h"

#include <cerrno>
#include <fcntl.h>
#include <sys/stat.h>
#include <unistd.h>

namespace
{
bool SameFile(const struct stat& left, const struct stat& right)
{
    return left.st_dev == right.st_dev && left.st_ino == right.st_ino &&
        left.st_mode == right.st_mode && left.st_uid == right.st_uid &&
        left.st_gid == right.st_gid && left.st_nlink == 1 && right.st_nlink == 1 &&
        left.st_size == right.st_size &&
        left.st_mtim.tv_sec == right.st_mtim.tv_sec &&
        left.st_mtim.tv_nsec == right.st_mtim.tv_nsec &&
        left.st_ctim.tv_sec == right.st_ctim.tv_sec &&
        left.st_ctim.tv_nsec == right.st_ctim.tv_nsec;
}

bool IsDiscoveryOperation(const std::string& toolName)
{
    return toolName == "system.tools.list" ||
        toolName == "system.tools.describe";
}

std::string DiscoveryToolCallId(const std::string& parentToolCallId)
{
    const std::string base = parentToolCallId.empty()
        ? "native-sdk"
        : parentToolCallId.substr(0, 108);
    return base + "-catalog";
}

// Session tokens are Agent-boundary text, not arbitrary byte blobs.  Keep
// this check in the native token-file entry point as well as in the typed
// protocol encoder so an embedded caller cannot observe a different contract
// merely by loading a token directly.  The grammar mirrors
// TypedToolProtocol's field validator: strict UTF-8 scalar sequences and no
// C0/C1 controls or DEL.
bool IsSafeTokenText(const std::string& value)
{
    if (value.find('\0') != std::string::npos) return false;
    std::size_t offset = 0;
    while (offset < value.size())
    {
        const unsigned char first =
            static_cast<unsigned char>(value[offset]);
        if (first <= 0x7fu)
        {
            if (first < 0x20u || first == 0x7fu) return false;
            ++offset;
            continue;
        }
        std::size_t continuationCount = 0;
        if (first >= 0xc2u && first <= 0xdfu) continuationCount = 1;
        else if (first >= 0xe0u && first <= 0xefu) continuationCount = 2;
        else if (first >= 0xf0u && first <= 0xf4u) continuationCount = 3;
        else return false;
        if (value.size() - offset <= continuationCount) return false;
        const unsigned char second =
            static_cast<unsigned char>(value[offset + 1]);
        if ((first == 0xe0u && second < 0xa0u) ||
            (first == 0xedu && second >= 0xa0u) ||
            (first == 0xf0u && second < 0x90u) ||
            (first == 0xf4u && second > 0x8fu))
            return false;
        std::uint32_t codepoint = first &
            (continuationCount == 1u ? 0x1fu :
             continuationCount == 2u ? 0x0fu : 0x07u);
        for (std::size_t i = 1; i <= continuationCount; ++i)
        {
            const unsigned char continuation =
                static_cast<unsigned char>(value[offset + i]);
            if (continuation < 0x80u || continuation > 0xbfu) return false;
            codepoint = (codepoint << 6) | (continuation & 0x3fu);
        }
        if ((continuationCount == 1u && codepoint < 0x80u) ||
            (continuationCount == 2u && codepoint < 0x800u) ||
            (continuationCount == 3u && codepoint < 0x10000u) ||
            (codepoint >= 0xd800u && codepoint <= 0xdfffu) ||
            codepoint > 0x10ffffu ||
            (codepoint >= 0x7fu && codepoint <= 0x9fu))
            return false;
        offset += continuationCount + 1u;
    }
    return true;
}
}

NativeToolClient::NativeToolClient(const NativeToolClientConfig& config)
    : m_config(config)
{
}

bool NativeToolClient::ReadSessionToken(const std::string& path,
                                        std::string& token,
                                        std::string& reason)
{
    token.clear();
    if (path.empty())
    {
        reason = "TOKEN_FILE_REQUIRED";
        return false;
    }
    struct stat before;
    if (::lstat(path.c_str(), &before) != 0 || !S_ISREG(before.st_mode) ||
        S_ISLNK(before.st_mode) || (before.st_uid != 0 && before.st_uid != ::geteuid()) ||
        before.st_nlink != 1 || (before.st_mode & 07777) != 0600 ||
        before.st_size < 1 || before.st_size > 514)
    {
        reason = "TOKEN_FILE_UNSAFE";
        return false;
    }
    const int fd = ::open(path.c_str(), O_RDONLY | O_CLOEXEC | O_NOFOLLOW);
    if (fd < 0)
    {
        reason = "TOKEN_FILE_OPEN_FAILED";
        return false;
    }
    struct stat opened;
    struct stat after;
    struct stat pathAfter;
    char buffer[515];
    const bool openedSafe = ::fstat(fd, &opened) == 0 &&
        S_ISREG(opened.st_mode) &&
        (opened.st_uid == 0 || opened.st_uid == ::geteuid()) &&
        opened.st_nlink == 1 && (opened.st_mode & 07777) == 0600 &&
        opened.st_size >= 1 && opened.st_size <= 514 &&
        SameFile(before, opened);
    std::size_t total = 0;
    while (openedSafe && total < static_cast<std::size_t>(opened.st_size))
    {
        const ssize_t count = ::read(
            fd, buffer + total, static_cast<std::size_t>(opened.st_size) - total);
        if (count < 0 && errno == EINTR) continue;
        if (count <= 0) break;
        total += static_cast<std::size_t>(count);
    }
    char extra = '\0';
    const ssize_t extraCount =
        openedSafe && total == static_cast<std::size_t>(opened.st_size)
        ? ::read(fd, &extra, 1) : -1;
    const bool stable = openedSafe &&
        total == static_cast<std::size_t>(opened.st_size) && extraCount == 0 &&
        ::fstat(fd, &after) == 0 && ::lstat(path.c_str(), &pathAfter) == 0 &&
        SameFile(opened, after) && SameFile(after, pathAfter);
    const int closeResult = ::close(fd);
    if (!stable || closeResult != 0)
    {
        reason = "TOKEN_FILE_CHANGED_OR_UNREADABLE";
        return false;
    }
    token.assign(buffer, total);
    while (!token.empty() && (token[token.size() - 1] == '\n' || token[token.size() - 1] == '\r'))
        token.erase(token.size() - 1);
    if (token.empty() || token.size() > 512 || !IsSafeTokenText(token))
    {
        token.clear();
        reason = "TOKEN_FILE_INVALID";
        return false;
    }
    reason.clear();
    return true;
}

bool NativeToolClient::Call(TradingToolHostRequest request,
                            NativeToolClientResult& result,
                            std::string& reason) const
{
    result = NativeToolClientResult();
    if (m_config.timeoutMs < 1 || m_config.timeoutMs > 120000 ||
        m_config.maxResponseBytes < 1 ||
        m_config.maxResponseBytes >
            TradingToolWireLimits::MaximumResultEnvelopeBytes())
    {
        reason = "NATIVE_CLIENT_CONFIG_INVALID";
        return false;
    }
    // Discovery is itself an RPC.  Validate the caller's idempotency key
    // before deriving a discovery child id or touching the socket, otherwise
    // malformed direct-client requests could return an unrelated transport
    // error (and potentially amplify a retry) instead of the stable protocol
    // rejection used by TypedToolProtocol::EncodeRequest.
    if (!TradingToolWireContract::IsCanonicalCommandId(request.toolCallId))
    {
        reason = "INVALID_TOOL_CALL_ID";
        return false;
    }
    if (!TradingToolWireContract::IsCanonicalToolName(request.call.name))
    {
        reason = "INVALID_TOOL_NAME";
        return false;
    }
    if (request.call.name != "system.tools.list")
    {
        if (!EnsureDiscoveryCatalog(request.toolCallId, reason))
            return false;
    }
    if (!IsDiscoveryOperation(request.call.name))
    {
        std::string discoveredSchemaHash;
        {
            std::lock_guard<std::mutex> lock(m_discoveryMutex);
            const std::map<std::string, std::string>::const_iterator descriptor =
                m_discoveryCatalog.descriptorSchemaHashes.find(
                    request.call.name);
            if (descriptor == m_discoveryCatalog.descriptorSchemaHashes.end())
            {
                reason = "DISCOVERY_TOOL_NOT_ADVERTISED";
                return false;
            }
            discoveredSchemaHash = descriptor->second;
        }
        if (!request.expectedSchemaHash.empty() &&
            request.expectedSchemaHash != discoveredSchemaHash)
        {
            reason = "DISCOVERY_REQUEST_SCHEMA_HASH_MISMATCH";
            return false;
        }
        request.expectedSchemaHash = discoveredSchemaHash;
    }
    return CallOnce(request, result, reason);
}

bool NativeToolClient::EnsureDiscoveryCatalog(
    const std::string& parentToolCallId,
    std::string& reason) const
{
    {
        std::lock_guard<std::mutex> lock(m_discoveryMutex);
        if (!m_discoveryCatalog.schemaHash.empty() &&
            !m_discoveryCatalog.descriptorSchemaHashes.empty())
        {
            reason.clear();
            return true;
        }
    }
    TradingToolHostRequest discovery;
    discovery.toolCallId = DiscoveryToolCallId(parentToolCallId);
    discovery.call.name = "system.tools.list";
    NativeToolClientResult result;
    if (!CallOnce(discovery, result, reason))
        return false;
    if (result.envelope.status != "ok")
    {
        reason = "DISCOVERY_LIST_REJECTED";
        return false;
    }
    std::lock_guard<std::mutex> lock(m_discoveryMutex);
    if (m_discoveryCatalog.schemaHash.empty() ||
        m_discoveryCatalog.descriptorSchemaHashes.empty())
    {
        reason = "DISCOVERY_CATALOG_EMPTY";
        return false;
    }
    reason.clear();
    return true;
}

bool NativeToolClient::CallOnce(TradingToolHostRequest request,
                                NativeToolClientResult& result,
                                std::string& reason) const
{
    result = NativeToolClientResult();
    if (!m_config.tokenFile.empty())
    {
        if (!ReadSessionToken(m_config.tokenFile, request.sessionToken, reason)) return false;
    }
    else
    {
        request.sessionToken = m_config.sessionToken;
        if (request.sessionToken.empty() || request.sessionToken.size() > 512 ||
            !IsSafeTokenText(request.sessionToken))
        {
            reason = "SESSION_TOKEN_INVALID";
            return false;
        }
    }
    if (!UnixToolClient::Call(m_config.socketPath, request, result.responseJson, reason,
            m_config.timeoutMs, m_config.maxResponseBytes))
        return false;
    if (!TypedToolProtocol::DecodeResultEnvelope(
            result.responseJson, result.envelope, reason))
    {
        result = NativeToolClientResult();
        return false;
    }
    if (result.envelope.toolName != request.call.name)
    {
        result = NativeToolClientResult();
        reason = "RESULT_TOOL_MISMATCH";
        return false;
    }
    if (result.envelope.status == "ok" &&
        IsDiscoveryOperation(request.call.name))
    {
        NativeToolDiscoveryContract::CatalogSnapshot observedCatalog;
        {
            std::lock_guard<std::mutex> lock(m_discoveryMutex);
            if (!NativeToolDiscoveryContract::Validate(
                    request.call.name, result.envelope.payloadJson,
                    request.call.targetToolName, m_discoveryCatalog,
                    observedCatalog, reason))
            {
                result = NativeToolClientResult();
                return false;
            }
            if (request.call.name == "system.tools.list")
                m_discoveryCatalog = observedCatalog;
        }
    }
    reason.clear();
    return true;
}
