#include "unix_execution_service_server.h"
#include "unix_execution_service_internal.h"
#include "execution_decision_lease_authority.h"
#include "execution_service_protocol.h"
#include <arpa/inet.h>
#include <cerrno>
#include <chrono>
#include <cstring>
#include <exception>
#include <fcntl.h>
#include <iomanip>
#include <locale>
#include <poll.h>
#include <sstream>
#include <sys/file.h>
#include <sys/socket.h>
#include <sys/random.h>
#include <sys/stat.h>
#include <sys/un.h>
#include <unistd.h>
namespace HeptaExecutionServiceInternal
{
bool GenerateServiceEpoch(std::string& epoch)
{
    unsigned char bytes[16];
    std::size_t offset = 0;
    while (offset < sizeof(bytes))
    {
        const ssize_t count = ::getrandom(bytes + offset, sizeof(bytes) - offset, 0);
        if (count < 0 && errno == EINTR) continue;
        if (count <= 0) return false;
        offset += static_cast<std::size_t>(count);
    }
    static const char hex[] = "0123456789abcdef";
    epoch = "hexec-v6-";
    for (std::size_t i = 0; i < sizeof(bytes); ++i)
    {
        epoch.push_back(hex[bytes[i] >> 4]);
        epoch.push_back(hex[bytes[i] & 0x0f]);
    }
    return true;
}
long long EpochNowMs()
{
    return static_cast<long long>(
        std::chrono::duration_cast<std::chrono::milliseconds>(
            std::chrono::system_clock::now().time_since_epoch()).count());
}
void AppendFingerprintField(std::string& out, const std::string& value)
{
    out.append(std::to_string(value.size()));
    out.push_back(':');
    out.append(value);
    out.push_back('\n');
}
template <typename T>
std::string FingerprintNumber(T value)
{
    // Fingerprints are protocol identity, not presentation.  Normalize
    // signed zero and pin the classic locale so a process-wide locale cannot
    // make equivalent commands hash differently (or emit comma decimals).
    if (value == 0) return "0";
    std::ostringstream output;
    output.imbue(std::locale::classic());
    output << std::setprecision(17) << value;
    return output.str();
}
std::string PreviewFingerprint(const PlaceOrderCommand& command)
{
    std::string value;
    AppendFingerprintField(value, command.context.agentId);
    AppendFingerprintField(value, command.context.sessionId);
    AppendFingerprintField(value, command.context.strategy);
    AppendFingerprintField(value, command.context.account);
    AppendFingerprintField(value, command.context.venue);
    AppendFingerprintField(value, command.context.executionDomain);
    AppendFingerprintField(value, command.context.allowCancelAny ? "1" : "0");
    AppendFingerprintField(value, command.instrument);
    AppendFingerprintField(value, command.contract.symbol);
    AppendFingerprintField(value, command.contract.secType);
    AppendFingerprintField(value, command.contract.exchange);
    AppendFingerprintField(value, command.contract.primaryExchange);
    AppendFingerprintField(value, command.contract.currency);
    AppendFingerprintField(value, command.contract.lastTradeDateOrContractMonth);
    AppendFingerprintField(value, command.contract.right);
    AppendFingerprintField(value, FingerprintNumber(command.contract.strike));
    AppendFingerprintField(value, command.contract.multiplier);
    AppendFingerprintField(value, command.contract.tradingClass);
    AppendFingerprintField(value, command.contract.localSymbol);
    AppendFingerprintField(value, command.order.action);
    AppendFingerprintField(value, command.order.orderType);
    AppendFingerprintField(value, FingerprintNumber(command.order.totalQuantity));
    AppendFingerprintField(value, FingerprintNumber(command.order.lmtPrice));
    AppendFingerprintField(value, FingerprintNumber(command.order.auxPrice));
    AppendFingerprintField(value, command.order.outsideRth ? "1" : "0");
    AppendFingerprintField(value, command.order.orderRef);
    AppendFingerprintField(value, command.timeInForce);
    AppendFingerprintField(value, FingerprintNumber(command.referencePrice));
    AppendFingerprintField(value, std::to_string(command.expiresAtMs));
    return value;
}
std::string PreviewOwnerKey(const PlaceOrderCommand& command)
{
    std::string value;
    AppendFingerprintField(value, command.context.agentId);
    AppendFingerprintField(value, command.context.sessionId);
    return value;
}

std::string PlaceDispatchKey(const PlaceOrderCommand& command)
{
    std::string value("place\n");
    AppendFingerprintField(value, command.context.agentId);
    AppendFingerprintField(value, command.context.sessionId);
    AppendFingerprintField(value, command.context.toolCallId);
    return value;
}

bool ShouldRetainPreviewDispatch(const ExecutionCommandResult& result)
{
    // The permit has already been atomically consumed before the authority is
    // called.  Even a Rejected/Error response can therefore follow a journal
    // write or venue-side preflight that is not visible to this layer.  Keep a
    // replay witness for every authority response; only validation/lease
    // failures that occur before the claim leave the permit retryable.
    (void)result;
    return true;
}

ExecutionCommandResult PreviewDispatchInFlightResult(
    const std::string& commandId)
{
    ExecutionCommandResult result;
    result.status = ExecutionCommandStatus::Uncertain;
    result.commandId = commandId;
    result.reasonCode = "EXECUTION_COMMAND_IN_FLIGHT";
    result.detail =
        "the bound preview mutation is still being dispatched";
    return result;
}

ExecutionCommandResult ReplayPreviewDispatchResult(
    const ExecutionCommandResult& stored)
{
    ExecutionCommandResult result = stored;
    if (result.status == ExecutionCommandStatus::Accepted)
    {
        result.status = ExecutionCommandStatus::Duplicate;
        result.reasonCode = "DUPLICATE_TOOL_CALL";
        result.detail = "previous_status=accepted";
    }
    return result;
}

ExecutionCommandResult PreviewDispatchConflictResult(
    const std::string& commandId)
{
    ExecutionCommandResult result;
    result.status = ExecutionCommandStatus::Rejected;
    result.commandId = commandId;
    result.reasonCode = "IDEMPOTENCY_KEY_CONFLICT";
    result.detail =
        "tool_call_id was already used for a different preview mutation payload";
    return result;
}

const std::size_t kMaxPreviewPermits = 128;
const std::size_t kMaxPreviewPermitsPerOwner = 8;
const std::size_t kMaxPreviewDispatchRecords = 2048;
const std::chrono::hours kPreviewDispatchReplayTtl(24);
bool GeneratePreviewPermit(std::string& permit)
{
    unsigned char bytes[32];
    std::size_t offset = 0;
    while (offset < sizeof(bytes))
    {
        const ssize_t count = ::getrandom(bytes + offset, sizeof(bytes) - offset, 0);
        if (count < 0 && errno == EINTR) continue;
        if (count <= 0) return false;
        offset += static_cast<std::size_t>(count);
    }
    static const char hex[] = "0123456789abcdef";
    permit = "sha256:";
    for (std::size_t i = 0; i < sizeof(bytes); ++i)
    {
        permit.push_back(hex[bytes[i] >> 4]);
        permit.push_back(hex[bytes[i] & 0x0f]);
    }
    return true;
}
bool GeneratePreviewMutationCommandId(std::string& commandId)
{
    unsigned char bytes[16];
    std::size_t offset = 0;
    while (offset < sizeof(bytes))
    {
        const ssize_t count =
            ::getrandom(bytes + offset, sizeof(bytes) - offset, 0);
        if (count < 0 && errno == EINTR) continue;
        if (count <= 0) return false;
        offset += static_cast<std::size_t>(count);
    }
    static const char hex[] = "0123456789abcdef";
    commandId = "hexec-command-";
    for (std::size_t i = 0; i < sizeof(bytes); ++i)
    {
        commandId.push_back(hex[bytes[i] >> 4]);
        commandId.push_back(hex[bytes[i] & 0x0f]);
    }
    return true;
}
bool ExistingSocketIsStale(const std::string& socketPath,
                           const struct stat& original,
                           std::string& reason)
{
    if (!S_ISSOCK(original.st_mode))
    {
        reason = "EXECUTION_SOCKET_PATH_NOT_SOCKET";
        return false;
    }
    if (original.st_uid != ::geteuid())
    {
        reason = "EXECUTION_SOCKET_PATH_NOT_OWNED";
        return false;
    }
    struct sockaddr_un address;
    if (!BuildAddress(socketPath, address, reason)) return false;
    const int probe = ::socket(AF_UNIX, SOCK_STREAM | SOCK_CLOEXEC | SOCK_NONBLOCK, 0);
    if (probe < 0)
    {
        reason = "EXECUTION_SOCKET_PROBE_FAILED";
        return false;
    }
    const int rc = ::connect(probe, reinterpret_cast<struct sockaddr*>(&address), sizeof(address));
    const int connectError = rc == 0 ? 0 : errno;
    ::close(probe);
    if (rc == 0 || connectError == EINPROGRESS || connectError == EAGAIN ||
        connectError == EALREADY || connectError == EISCONN)
    {
        reason = "EXECUTION_SOCKET_ALREADY_ACTIVE";
        return false;
    }
    if (connectError != ECONNREFUSED)
    {
        reason = std::string("EXECUTION_SOCKET_PROBE_REJECTED:") + std::strerror(connectError);
        return false;
    }
    struct stat current;
    if (::lstat(socketPath.c_str(), &current) != 0 ||
        current.st_dev != original.st_dev || current.st_ino != original.st_ino ||
        !S_ISSOCK(current.st_mode) || current.st_uid != original.st_uid)
    {
        reason = "EXECUTION_SOCKET_PATH_CHANGED_DURING_PROBE";
        return false;
    }
    reason.clear();
    return true;
}
int LockSocketPath(const std::string& socketPath, std::string& reason)
{
    const std::string lockPath = socketPath + ".lock";
    const int lockFd = ::open(lockPath.c_str(), O_RDWR | O_CREAT | O_CLOEXEC | O_NOFOLLOW, 0600);
    if (lockFd < 0)
    {
        reason = "EXECUTION_SOCKET_LOCK_OPEN_FAILED";
        return -1;
    }
    struct stat metadata;
    if (::fstat(lockFd, &metadata) != 0 || !S_ISREG(metadata.st_mode) ||
        metadata.st_uid != ::geteuid() || metadata.st_nlink != 1 ||
        (metadata.st_mode & 0077) != 0 || ::flock(lockFd, LOCK_EX | LOCK_NB) != 0)
    {
        ::close(lockFd);
        reason = "EXECUTION_SOCKET_LOCK_UNAVAILABLE";
        return -1;
    }
    reason.clear();
    return lockFd;
}
void UnlockAndClose(int fd)
{
    if (fd < 0) return;
    ::flock(fd, LOCK_UN);
    ::close(fd);
}
void UnlinkSocketIfIdentityMatches(const std::string& socketPath,
                                   std::uint64_t device,
                                   std::uint64_t inode)
{
    struct stat current;
    if (::lstat(socketPath.c_str(), &current) == 0 && S_ISSOCK(current.st_mode) &&
        static_cast<std::uint64_t>(current.st_dev) == device &&
        static_cast<std::uint64_t>(current.st_ino) == inode)
        ::unlink(socketPath.c_str());
}
bool ValidateActivatedSocket(int listenFd, std::string& reason)
{
    if (listenFd < 0 || ::fcntl(listenFd, F_GETFD) < 0)
    {
        reason = "EXECUTION_ACTIVATED_FD_INVALID";
        return false;
    }
    int socketType = 0;
    socklen_t socketTypeLength = sizeof(socketType);
    int accepting = 0;
    socklen_t acceptingLength = sizeof(accepting);
    struct sockaddr_un address;
    socklen_t addressLength = sizeof(address);
    std::memset(&address, 0, sizeof(address));
    if (::getsockopt(listenFd, SOL_SOCKET, SO_TYPE, &socketType, &socketTypeLength) != 0 ||
        socketType != SOCK_STREAM ||
        ::getsockopt(listenFd, SOL_SOCKET, SO_ACCEPTCONN, &accepting, &acceptingLength) != 0 ||
        accepting != 1 ||
        ::getsockname(listenFd, reinterpret_cast<struct sockaddr*>(&address), &addressLength) != 0 ||
        address.sun_family != AF_UNIX)
    {
        reason = "EXECUTION_ACTIVATED_FD_NOT_LISTENING_UNIX_STREAM";
        return false;
    }
    const int flags = ::fcntl(listenFd, F_GETFD);
    if (flags < 0 || ::fcntl(listenFd, F_SETFD, flags | FD_CLOEXEC) != 0)
    {
        reason = "EXECUTION_ACTIVATED_FD_CLOEXEC_FAILED";
        return false;
    }
    reason.clear();
    return true;
}
bool IsControlOperation(ExecutionServiceOperation operation)
{
    return operation != ExecutionServiceOperation::PlaceIbOrder &&
        operation != ExecutionServiceOperation::CancelIbOrder &&
        operation != ExecutionServiceOperation::FlattenPosition &&
        operation != ExecutionServiceOperation::ReadAuthoritativeState &&
        operation != ExecutionServiceOperation::PreviewOrder &&
        operation != ExecutionServiceOperation::PreviewFlattenPosition &&
        operation != ExecutionServiceOperation::GetServiceIdentity;
}
const AgentExecutionContext* RequestContext(const ExecutionServiceRequest& request)
{
    if (IsControlOperation(request.operation)) return &request.control.context;
    switch (request.operation)
    {
    case ExecutionServiceOperation::PlaceIbOrder:
    case ExecutionServiceOperation::PreviewOrder:
        return &request.place.context;
    case ExecutionServiceOperation::CancelIbOrder:
        return &request.cancel.context;
    case ExecutionServiceOperation::FlattenPosition:
    case ExecutionServiceOperation::PreviewFlattenPosition:
        return &request.flatten.context;
    case ExecutionServiceOperation::ReadAuthoritativeState:
        return &request.read.context;
    case ExecutionServiceOperation::GetServiceIdentity:
        return nullptr;
    default: return nullptr;
    }
}

namespace
{
bool PreviewJsonWhitespace(unsigned char value)
{
    return value == static_cast<unsigned char>(' ') ||
        value == static_cast<unsigned char>('\t') ||
        value == static_cast<unsigned char>('\r') ||
        value == static_cast<unsigned char>('\n');
}

bool PreviewJsonHex(unsigned char value)
{
    return (value >= static_cast<unsigned char>('0') &&
            value <= static_cast<unsigned char>('9')) ||
        (value >= static_cast<unsigned char>('a') &&
         value <= static_cast<unsigned char>('f')) ||
        (value >= static_cast<unsigned char>('A') &&
         value <= static_cast<unsigned char>('F'));
}

class PreviewJsonValidator
{
public:
    explicit PreviewJsonValidator(const std::string& input)
        : m_input(input), m_offset(0), m_nodes(0)
    {
    }

    bool Parse()
    {
        if (m_input.empty() || m_input.size() > 32768u) return false;
        SkipWhitespace();
        if (!ParseValue(0)) return false;
        SkipWhitespace();
        return m_offset == m_input.size();
    }

private:
    void SkipWhitespace()
    {
        while (m_offset < m_input.size() &&
               PreviewJsonWhitespace(static_cast<unsigned char>(
                   m_input[m_offset])))
            ++m_offset;
    }

    bool Consume(char expected)
    {
        if (m_offset >= m_input.size() || m_input[m_offset] != expected)
            return false;
        ++m_offset;
        return true;
    }

    bool ParseString(std::string* key)
    {
        if (!Consume('"')) return false;
        if (key != nullptr) key->clear();
        while (m_offset < m_input.size())
        {
            const unsigned char value =
                static_cast<unsigned char>(m_input[m_offset++]);
            if (value == '"') return true;
            if (value < 0x20u || value == 0x7fu ||
                (value >= 0x80u && value <= 0x9fu))
                return false;
            if (value == '\\')
            {
                if (m_offset >= m_input.size()) return false;
                const unsigned char escaped =
                    static_cast<unsigned char>(m_input[m_offset++]);
                if (escaped == '"' || escaped == '\\' || escaped == '/')
                {
                    if (key != nullptr)
                        key->push_back(static_cast<char>(escaped));
                    continue;
                }
                if (escaped == 'b' || escaped == 'f' || escaped == 'n' ||
                    escaped == 'r' || escaped == 't')
                    return false;
                if (escaped != 'u' || m_input.size() - m_offset < 4u)
                    return false;
                unsigned int codepoint = 0;
                for (std::size_t i = 0; i < 4u; ++i)
                {
                    const unsigned char digit =
                        static_cast<unsigned char>(m_input[m_offset++]);
                    if (!PreviewJsonHex(digit)) return false;
                    codepoint <<= 4u;
                    codepoint += digit <= '9' ? digit - '0' :
                        (digit >= 'a' && digit <= 'f' ?
                            digit - 'a' + 10u : digit - 'A' + 10u);
                }
                if (codepoint < 0x20u || codepoint == 0x7fu ||
                    (codepoint >= 0x80u && codepoint <= 0x9fu) ||
                    (codepoint >= 0xd800u && codepoint <= 0xdfffu))
                    return false;
                // Object-key uniqueness is semantic, not lexical: `"a"`
                // and `"\\u0061"` must collide.  Decode the safe Unicode
                // escape into the same UTF-8 bytes used by a raw key.
                if (key != nullptr)
                {
                    if (codepoint <= 0x7fu)
                        key->push_back(static_cast<char>(codepoint));
                    else if (codepoint <= 0x7ffu)
                    {
                        key->push_back(static_cast<char>(0xc0u |
                            (codepoint >> 6u)));
                        key->push_back(static_cast<char>(0x80u |
                            (codepoint & 0x3fu)));
                    }
                    else
                    {
                        key->push_back(static_cast<char>(0xe0u |
                            (codepoint >> 12u)));
                        key->push_back(static_cast<char>(0x80u |
                            ((codepoint >> 6u) & 0x3fu)));
                        key->push_back(static_cast<char>(0x80u |
                            (codepoint & 0x3fu)));
                    }
                }
                continue;
            }
            if (value < 0x80u)
            {
                if (key != nullptr) key->push_back(static_cast<char>(value));
                continue;
            }
            std::size_t continuationCount = 0;
            if (value >= 0xc2u && value <= 0xdfu) continuationCount = 1;
            else if (value >= 0xe0u && value <= 0xefu) continuationCount = 2;
            else if (value >= 0xf0u && value <= 0xf4u) continuationCount = 3;
            else return false;
            if (m_input.size() - m_offset < continuationCount) return false;
            const unsigned char second =
                static_cast<unsigned char>(m_input[m_offset]);
            if ((value == 0xe0u && second < 0xa0u) ||
                (value == 0xedu && second >= 0xa0u) ||
                (value == 0xf0u && second < 0x90u) ||
                (value == 0xf4u && second > 0x8fu)) return false;
            for (std::size_t i = 0; i < continuationCount; ++i)
            {
                const unsigned char continuation =
                    static_cast<unsigned char>(m_input[m_offset++]);
                if (continuation < 0x80u || continuation > 0xbfu)
                    return false;
            }
        }
        return false;
    }

    bool ParseNumber()
    {
        const std::size_t start = m_offset;
        if (m_offset < m_input.size() && m_input[m_offset] == '-') ++m_offset;
        if (m_offset >= m_input.size()) return false;
        if (m_input[m_offset] == '0')
        {
            ++m_offset;
            if (m_offset < m_input.size() &&
                m_input[m_offset] >= '0' && m_input[m_offset] <= '9')
                return false;
        }
        else
        {
            if (m_input[m_offset] < '1' || m_input[m_offset] > '9')
                return false;
            while (m_offset < m_input.size() &&
                   m_input[m_offset] >= '0' && m_input[m_offset] <= '9')
                ++m_offset;
        }
        if (m_offset < m_input.size() && m_input[m_offset] == '.')
        {
            ++m_offset;
            const std::size_t fraction = m_offset;
            while (m_offset < m_input.size() &&
                   m_input[m_offset] >= '0' && m_input[m_offset] <= '9')
                ++m_offset;
            if (fraction == m_offset) return false;
        }
        if (m_offset < m_input.size() &&
            (m_input[m_offset] == 'e' || m_input[m_offset] == 'E'))
        {
            ++m_offset;
            if (m_offset < m_input.size() &&
                (m_input[m_offset] == '+' || m_input[m_offset] == '-'))
                ++m_offset;
            const std::size_t exponent = m_offset;
            while (m_offset < m_input.size() &&
                   m_input[m_offset] >= '0' && m_input[m_offset] <= '9')
                ++m_offset;
            if (exponent == m_offset) return false;
        }
        const std::string token = m_input.substr(start, m_offset - start);
        std::istringstream input(token);
        input.imbue(std::locale::classic());
        double parsed = 0.0;
        input >> std::noskipws >> parsed;
        return !input.fail() && input.eof() && std::isfinite(parsed);
    }

    bool ParseArray(std::size_t depth)
    {
        if (!Consume('[')) return false;
        SkipWhitespace();
        if (Consume(']')) return true;
        for (;;)
        {
            if (!ParseValue(depth + 1u)) return false;
            SkipWhitespace();
            if (Consume(']')) return true;
            if (!Consume(',')) return false;
            SkipWhitespace();
        }
    }

    bool ParseObject(std::size_t depth)
    {
        if (!Consume('{')) return false;
        std::set<std::string> keys;
        SkipWhitespace();
        if (Consume('}')) return true;
        for (;;)
        {
            std::string key;
            if (!ParseString(&key) || !keys.insert(key).second)
                return false;
            SkipWhitespace();
            if (!Consume(':')) return false;
            SkipWhitespace();
            if (!ParseValue(depth + 1u)) return false;
            SkipWhitespace();
            if (Consume('}')) return true;
            if (!Consume(',')) return false;
            SkipWhitespace();
        }
    }

    bool ParseValue(std::size_t depth)
    {
        if (depth > 64u || ++m_nodes > 100000u || m_offset >= m_input.size())
            return false;
        const char value = m_input[m_offset];
        if (value == '{') return ParseObject(depth);
        if (value == '[') return ParseArray(depth);
        if (value == '"') return ParseString(nullptr);
        if (value == 't' && m_input.compare(m_offset, 4, "true") == 0)
        {
            m_offset += 4;
            return true;
        }
        if (value == 'f' && m_input.compare(m_offset, 5, "false") == 0)
        {
            m_offset += 5;
            return true;
        }
        if (value == 'n' && m_input.compare(m_offset, 4, "null") == 0)
        {
            m_offset += 4;
            return true;
        }
        return ParseNumber();
    }

    const std::string& m_input;
    std::size_t m_offset;
    std::size_t m_nodes;
};
}

bool ValidPreviewJson(const std::string& value)
{
    return PreviewJsonValidator(value).Parse();
}
} // namespace HeptaExecutionServiceInternal
using namespace HeptaExecutionServiceInternal;

namespace
{
// Result fields are populated by several independent authority/adapter
// implementations.  Those implementations intentionally retain rich local
// diagnostics (and may persist them in their journals), but the Unix service
// is the last trust boundary before an Agent/MCP peer receives a response.
// Keep reason codes machine-stable and replace all failure details at this
// boundary.  In particular, this prevents an exception's what() text (which
// commonly contains filesystem paths, account identifiers, or credentials)
// from escaping even when a nested authority callback forgot to classify it.
bool StableExecutionReasonCode(const std::string& value)
{
    if (value.empty() || value.size() > 128 ||
        value[0] < 'A' || value[0] > 'Z')
        return false;
    for (std::string::const_iterator it = value.begin(); it != value.end(); ++it)
    {
        const char c = *it;
        if (!((c >= 'A' && c <= 'Z') ||
              (c >= '0' && c <= '9') || c == '_'))
            return false;
    }
    return true;
}

// Authority details are still untrusted even when the authority reports an
// Accepted or Duplicate status.  Preview/read operations intentionally carry
// bounded JSON in this field, so keep ordinary finite prose/JSON intact while
// rejecting controls, malformed UTF-8, path/credential markers and exception
// diagnostics before the final response codec.
bool LooksLikeExecutionPath(const std::string& value,
                            const std::string& lower)
{
    const auto isSeparator = [](char c) {
        return c == '/' || c == '\\';
    };
    // URLs, absolute POSIX/UNC paths and Windows drive paths are unambiguous
    // implementation details.  Do not reject every slash: finite authority
    // prose may legitimately contain a ratio ("fill ratio 1/2") or a market
    // symbol ("EUR/USD").
    if (value.find("://") != std::string::npos ||
        (!value.empty() && isSeparator(value[0])) ||
        (value.size() >= 3 &&
         ((value[0] >= 'A' && value[0] <= 'Z') ||
          (value[0] >= 'a' && value[0] <= 'z')) &&
         value[1] == ':' && isSeparator(value[2])) ||
        lower.find("/private/") != std::string::npos ||
        lower.find("\\private\\") != std::string::npos)
        return true;

    static const char* const systemPrefixes[] = {
        "/tmp/", "/var/", "/etc/", "/home/", "/root/", "/usr/",
        "/opt/", "/run/", "/dev/", "/proc/", "/sys/", "\\windows\\",
        "\\temp\\"
    };
    for (std::size_t i = 0;
         i < sizeof(systemPrefixes) / sizeof(systemPrefixes[0]); ++i)
    {
        if (lower.find(systemPrefixes[i]) != std::string::npos) return true;
    }

    // Relative traversal and repeated separators are path syntax, not
    // ordinary detail prose.  A separator following a punctuation/space
    // boundary also catches forms such as "socket: /run/..." without
    // classifying an identifier containing one ordinary slash.
    for (std::size_t i = 0; i < value.size(); ++i)
    {
        if (!isSeparator(value[i])) continue;
        if (i + 1 < value.size() &&
            (isSeparator(value[i + 1]) || value[i + 1] == '.'))
            return true;
        if (i > 0 && value[i - 1] == '.') return true;
        if (i == 0 || value[i - 1] == ' ' || value[i - 1] == '\t' ||
            value[i - 1] == '=' || value[i - 1] == ':' ||
            value[i - 1] == '"' || value[i - 1] == '\'' ||
            value[i - 1] == '(' || value[i - 1] == '[')
            return true;
    }

    // Only treat explicit path/file/socket labels as path-bearing.  Matching
    // a bare "file" or "path" substring would incorrectly reject prose such
    // as "profile ready".
    static const char* const labels[] = {
        "path=", "path:", "file=", "file:", "filename=", "filename:",
        "directory=", "directory:", "socket=", "socket:"
    };
    for (std::size_t i = 0; i < sizeof(labels) / sizeof(labels[0]); ++i)
    {
        const std::string label(labels[i]);
        const std::size_t at = lower.find(label);
        if (at == std::string::npos) continue;
        const std::size_t valueStart = at + label.size();
        if (valueStart < value.size() && isSeparator(value[valueStart]))
            return true;
    }
    return false;
}

bool SafeExecutionDetail(const std::string& value)
{
    if (value.size() > 32768u || value.find('\0') != std::string::npos)
        return false;
    for (std::size_t offset = 0; offset < value.size();)
    {
        const unsigned char first =
            static_cast<unsigned char>(value[offset]);
        if (first < 0x20u || first == 0x7fu ||
            (first >= 0x80u && first <= 0x9fu))
            return false;
        if (first < 0x80u)
        {
            ++offset;
            continue;
        }
        std::size_t continuationCount = 0;
        if (first >= 0xc2u && first <= 0xdfu)
            continuationCount = 1;
        else if (first >= 0xe0u && first <= 0xefu)
            continuationCount = 2;
        else if (first >= 0xf0u && first <= 0xf4u)
            continuationCount = 3;
        else
            return false;
        if (value.size() - offset <= continuationCount) return false;
        const unsigned char second =
            static_cast<unsigned char>(value[offset + 1]);
        if ((first == 0xe0u && second < 0xa0u) ||
            (first == 0xedu && second >= 0xa0u) ||
            (first == 0xf0u && second < 0x90u) ||
            (first == 0xf4u && second > 0x8fu))
            return false;
        std::uint32_t codepoint = first &
            (continuationCount == 1 ? 0x1fu :
             continuationCount == 2 ? 0x0fu : 0x07u);
        for (std::size_t i = 1; i <= continuationCount; ++i)
        {
            const unsigned char continuation =
                static_cast<unsigned char>(value[offset + i]);
            if (continuation < 0x80u || continuation > 0xbfu)
                return false;
            codepoint = (codepoint << 6) | (continuation & 0x3fu);
        }
        if (codepoint < 0x20u || codepoint == 0x7fu ||
            (codepoint >= 0x80u && codepoint <= 0x9fu))
            return false;
        offset += continuationCount + 1u;
    }
    std::string lower;
    lower.reserve(value.size());
    for (std::string::const_iterator it = value.begin(); it != value.end(); ++it)
    {
        const char c = *it;
        lower.push_back(c >= 'A' && c <= 'Z' ?
            static_cast<char>(c - 'A' + 'a') : c);
    }
    if (LooksLikeExecutionPath(value, lower)) return false;
    static const char* const markers[] = {
        "exception", "what()", "credential", "secret", "password",
        "bearer", "authorization", "token", "private key", "api_key",
        "apikey", "errno", "stack trace", "threw", "could not",
        "not found", "failed"
    };
    for (std::size_t i = 0; i < sizeof(markers) / sizeof(markers[0]); ++i)
        if (lower.find(markers[i]) != std::string::npos)
            return false;
    return true;
}

bool IsReadOnlyExecutionOperation(ExecutionServiceOperation operation)
{
    return operation == ExecutionServiceOperation::ReadAuthoritativeState ||
        operation == ExecutionServiceOperation::PreviewOrder ||
        operation == ExecutionServiceOperation::PreviewFlattenPosition;
}

void SanitizeExecutionResultForWire(
    const ExecutionServiceRequest& request,
    ExecutionCommandResult& result)
{
    const int rawStatus = static_cast<int>(result.status);
    if (rawStatus < static_cast<int>(ExecutionCommandStatus::Accepted) ||
        rawStatus > static_cast<int>(ExecutionCommandStatus::Uncertain))
    {
        result.status = ExecutionCommandStatus::Rejected;
        result.reasonCode = "EXECUTION_AUTHORITY_RESPONSE_INVALID";
        result.detail = "execution authority response was invalid";
        return;
    }
    const bool rejected = result.status == ExecutionCommandStatus::Rejected;
    const bool uncertain = result.status == ExecutionCommandStatus::Uncertain;
    const bool duplicate = result.status == ExecutionCommandStatus::Duplicate;
    if ((rejected || uncertain || duplicate || !result.reasonCode.empty()) &&
        !StableExecutionReasonCode(result.reasonCode))
    {
        result.reasonCode = uncertain ? "EXECUTION_AUTHORITY_EXCEPTION" :
            (rejected ? "EXECUTION_REQUEST_REJECTED" :
                (result.status == ExecutionCommandStatus::Duplicate ?
                    "DUPLICATE_TOOL_CALL" :
                    "EXECUTION_AUTHORITY_RESPONSE_INVALID"));
    }
    else if (duplicate && result.reasonCode.empty())
        result.reasonCode = "DUPLICATE_TOOL_CALL";
    // Never forward authority-controlled prose for a failed operation.  The
    // reason code remains available for deterministic handling; local
    // authority/journal diagnostics are deliberately untouched.
    if (uncertain)
        result.detail = "execution authority outcome is uncertain";
    else if (rejected)
        result.detail = IsReadOnlyExecutionOperation(request.operation) ?
            "tool dispatch failed" : "execution request was rejected";
    else if (result.status == ExecutionCommandStatus::Duplicate)
    {
        if (!SafeExecutionDetail(result.detail))
            result.detail = "duplicate tool call";
    }
    else if (!SafeExecutionDetail(result.detail))
    {
        // Preview/read responses use `detail` as their bounded JSON payload.
        // A malformed or sensitive accepted payload cannot be repaired at the
        // wire boundary; turn it into a deterministic rejection instead of
        // returning an empty/partially interpreted preview.
        if (IsReadOnlyExecutionOperation(request.operation))
        {
            result.status = ExecutionCommandStatus::Rejected;
            result.reasonCode = request.operation ==
                    ExecutionServiceOperation::ReadAuthoritativeState ?
                "EXECUTION_READ_RESPONSE_INVALID" :
                "EXECUTION_PREVIEW_RESPONSE_INVALID";
            result.detail = "execution authority response was invalid";
        }
        else
            result.detail.clear();
    }
}

void SanitizeExecutionControlResultForWire(
    ExecutionControlResult& result)
{
    const int rawStatus = static_cast<int>(result.status);
    if (rawStatus < static_cast<int>(ExecutionCommandStatus::Accepted) ||
        rawStatus > static_cast<int>(ExecutionCommandStatus::Uncertain))
    {
        result.status = ExecutionCommandStatus::Rejected;
        result.reasonCode = "EXECUTION_AUTHORITY_RESPONSE_INVALID";
        result.detail = "execution authority response was invalid";
        return;
    }
    const bool rejected = result.status == ExecutionCommandStatus::Rejected;
    const bool uncertain = result.status == ExecutionCommandStatus::Uncertain;
    const bool duplicate = result.status == ExecutionCommandStatus::Duplicate;
    if ((rejected || uncertain || duplicate || !result.reasonCode.empty()) &&
        !StableExecutionReasonCode(result.reasonCode))
    {
        result.reasonCode = uncertain ? "EXECUTION_AUTHORITY_EXCEPTION" :
            (rejected ? "EXECUTION_CONTROL_REJECTED" :
                (result.status == ExecutionCommandStatus::Duplicate ?
                    "DUPLICATE_TOOL_CALL" :
                    "EXECUTION_AUTHORITY_RESPONSE_INVALID"));
    }
    else if (duplicate && result.reasonCode.empty())
        result.reasonCode = "DUPLICATE_TOOL_CALL";
    if (uncertain)
        result.detail = "execution authority outcome is uncertain";
    else if (rejected)
        result.detail = "execution control request was rejected";
    else if (result.status == ExecutionCommandStatus::Duplicate)
    {
        if (!SafeExecutionDetail(result.detail))
            result.detail = "duplicate tool call";
    }
    else if (!SafeExecutionDetail(result.detail))
        // An Accepted control response has no structured result payload. Keep
        // the status/reason while dropping only the unsafe diagnostic.
        result.detail.clear();
}

// Embedded compositions may provide one object implementing both the
// mutation and read-only facets without exposing the optional control-plane
// interface. Prefer an explicit control-plane read facet, then discover the
// read facet on the served authority so preview/replay checks remain active.
ExecutionReadAuthority* ResolveReadAuthority(
    ExecutionAuthority& authority,
    ExecutionControlAuthority* controlAuthority)
{
    if (controlAuthority != nullptr)
    {
        ExecutionReadAuthority* fromControl =
            dynamic_cast<ExecutionReadAuthority*>(controlAuthority);
        if (fromControl != nullptr) return fromControl;
    }
    return dynamic_cast<ExecutionReadAuthority*>(&authority);
}
}

bool GenerateExecutionServiceIdentity(
    std::uint64_t serviceFencingGeneration,
    ExecutionServiceIdentity& identity,
    std::string& reason)
{
    identity = ExecutionServiceIdentity();
    if (serviceFencingGeneration == 0)
    {
        reason = "EXECUTION_SERVICE_FENCING_GENERATION_INVALID";
        return false;
    }
    if (!GenerateServiceEpoch(identity.serviceEpoch))
    {
        reason = "EXECUTION_SERVICE_EPOCH_GENERATION_FAILED";
        return false;
    }
    identity.serviceFencingGeneration = serviceFencingGeneration;
    reason.clear();
    return true;
}
UnixExecutionServiceServer::UnixExecutionServiceServer(
    ExecutionAuthority& authority,
    ExecutionControlAuthority* controlAuthority,
    const std::shared_ptr<ExecutionDecisionLeaseAuthority>& decisionLeases)
    : m_authority(authority), m_controlAuthority(controlAuthority),
      m_readAuthority(ResolveReadAuthority(authority, controlAuthority)),
      m_decisionLeases(decisionLeases ? decisionLeases :
          std::shared_ptr<ExecutionDecisionLeaseAuthority>(
              new ExecutionDecisionLeaseAuthority())), m_stop(true),
      m_listenFd(-1), m_socketDevice(0),
      m_socketInode(0), m_ownsSocketPath(false), m_socketLockFd(-1),
      m_enforceGatewayContextBinding(false), m_maxRequestBytes(32768),
      m_ioTimeoutMs(3000)
{
}
UnixExecutionServiceServer::~UnixExecutionServiceServer()
{
    Stop();
}
bool UnixExecutionServiceServer::IssuePreviewPermit(
    const PlaceOrderCommand& command,
    std::string& permit,
    std::string& mutationCommandId,
    long long& expiresAtMs,
    std::string& reason)
{
    const long long now = EpochNowMs();
    const std::chrono::steady_clock::time_point steadyNow =
        std::chrono::steady_clock::now();
    expiresAtMs = std::min(command.expiresAtMs, now + 5000);
    if (command.expiresAtMs <= 0 || expiresAtMs <= now)
    {
        reason = "EXECUTION_PREVIEW_EXPIRY_INVALID";
        return false;
    }
    if (!GeneratePreviewPermit(permit) ||
        !GeneratePreviewMutationCommandId(mutationCommandId))
    {
        reason = "EXECUTION_PREVIEW_PERMIT_GENERATION_FAILED";
        return false;
    }
    std::lock_guard<std::mutex> lock(m_previewMutex);
    for (std::unordered_map<std::string, PreviewPermitRecord>::iterator it =
             m_previewPermits.begin(); it != m_previewPermits.end();)
    {
        if (it->second.expiresAtMs <= now ||
            it->second.steadyExpiresAt <= steadyNow)
            it = m_previewPermits.erase(it);
        else ++it;
    }
    const std::string fingerprint = PreviewFingerprint(command);
    const std::string ownerKey = PreviewOwnerKey(command);
    std::string replacedPermit;
    std::size_t ownerCount = 0;
    for (std::unordered_map<std::string, PreviewPermitRecord>::iterator it =
             m_previewPermits.begin(); it != m_previewPermits.end();)
    {
        if (it->second.fingerprint == fingerprint)
        {
            // A newer preview of the exact command deterministically revokes
            // the prior credential instead of growing the store.  Defer that
            // erase until capacity checks pass so a failed replacement does
            // not strand the still-valid older credential.
            replacedPermit = it->first;
            ++it;
            continue;
        }
        if (it->second.ownerKey == ownerKey) ++ownerCount;
        ++it;
    }
    if (ownerCount >= kMaxPreviewPermitsPerOwner)
    {
        reason = "EXECUTION_PREVIEW_PERMIT_OWNER_CAPACITY_EXCEEDED";
        return false;
    }
    if (m_previewPermits.size() >= kMaxPreviewPermits &&
        replacedPermit.empty())
    {
        reason = "EXECUTION_PREVIEW_PERMIT_CAPACITY_EXCEEDED";
        return false;
    }
    PreviewPermitRecord record;
    record.fingerprint = fingerprint;
    record.ownerKey = ownerKey;
    record.mutationCommandId = mutationCommandId;
    record.expiresAtMs = expiresAtMs;
    record.steadyExpiresAt =
        steadyNow + std::chrono::milliseconds(expiresAtMs - now);
    if (!replacedPermit.empty()) m_previewPermits.erase(replacedPermit);
    m_previewPermits[permit] = record;
    reason.clear();
    return true;
}
bool UnixExecutionServiceServer::ConsumePreviewPermit(
    const PlaceOrderCommand& command,
    std::string& reason)
{
    // Keep permit lookup/validation separate from the mutation.  In
    // particular, a malformed payload, cross-command retry, or expired
    // credential must not turn a still-valid preview into an
    // UNKNOWN_OR_CONSUMED error for the legitimate retry.
    const long long now = EpochNowMs();
    const std::chrono::steady_clock::time_point steadyNow =
        std::chrono::steady_clock::now();
    std::lock_guard<std::mutex> lock(m_previewMutex);
    const std::unordered_map<std::string, PreviewPermitRecord>::iterator found =
        m_previewPermits.find(command.previewPermit);
    if (found == m_previewPermits.end())
    {
        reason = "EXECUTION_PREVIEW_PERMIT_UNKNOWN_OR_CONSUMED";
        return false;
    }
    const PreviewPermitRecord record = found->second;
    if (record.expiresAtMs <= now ||
        record.steadyExpiresAt <= steadyNow)
    {
        reason = "EXECUTION_PREVIEW_PERMIT_EXPIRED";
        return false;
    }
    if (record.fingerprint != PreviewFingerprint(command))
    {
        reason = "EXECUTION_PREVIEW_PERMIT_ORDER_MISMATCH";
        return false;
    }
    if (record.mutationCommandId != command.context.toolCallId)
    {
        reason = "EXECUTION_PREVIEW_PERMIT_COMMAND_ID_MISMATCH";
        return false;
    }
    // Consume only after every validation above succeeds.  The lock remains
    // held across the final erase, making this one-time transition atomic
    // with respect to concurrent retries.
    m_previewPermits.erase(found);
    reason.clear();
    return true;
}

bool UnixExecutionServiceServer::ValidatePreviewPermit(
    const PlaceOrderCommand& command,
    std::string& reason) const
{
    const long long now = EpochNowMs();
    const std::chrono::steady_clock::time_point steadyNow =
        std::chrono::steady_clock::now();
    std::lock_guard<std::mutex> lock(m_previewMutex);
    const std::unordered_map<std::string, PreviewPermitRecord>::const_iterator found =
        m_previewPermits.find(command.previewPermit);
    if (found == m_previewPermits.end())
    {
        reason = "EXECUTION_PREVIEW_PERMIT_UNKNOWN_OR_CONSUMED";
        return false;
    }
    const PreviewPermitRecord& record = found->second;
    if (record.expiresAtMs <= now ||
        record.steadyExpiresAt <= steadyNow)
    {
        reason = "EXECUTION_PREVIEW_PERMIT_EXPIRED";
        return false;
    }
    if (record.fingerprint != PreviewFingerprint(command))
    {
        reason = "EXECUTION_PREVIEW_PERMIT_ORDER_MISMATCH";
        return false;
    }
    if (record.mutationCommandId != command.context.toolCallId)
    {
        reason = "EXECUTION_PREVIEW_PERMIT_COMMAND_ID_MISMATCH";
        return false;
    }
    reason.clear();
    return true;
}
void UnixExecutionServiceServer::RevokePreviewPermitsForOwner(
    const std::string& agentId,
    const std::string& sessionId)
{
    PlaceOrderCommand owner;
    owner.context.agentId = agentId;
    owner.context.sessionId = sessionId;
    const std::string ownerKey = PreviewOwnerKey(owner);
    std::lock_guard<std::mutex> lock(m_previewMutex);
    for (std::unordered_map<std::string, PreviewPermitRecord>::iterator it =
             m_previewPermits.begin(); it != m_previewPermits.end();)
    {
        if (it->second.ownerKey == ownerKey)
            it = m_previewPermits.erase(it);
        else
            ++it;
    }
    // A revoked owner must not receive a cached replay from a command that
    // was accepted before the fence.  An authority call already outside this
    // lock cannot be undone, but dropping its local witness prevents a later
    // retry from bypassing the new owner fence.
    for (std::unordered_map<std::string, PreviewDispatchRecord>::iterator it =
             m_previewDispatches.begin(); it != m_previewDispatches.end();)
    {
        if (it->second.ownerKey == ownerKey)
            it = m_previewDispatches.erase(it);
        else
            ++it;
    }
}
bool UnixExecutionServiceServer::Start(const std::string& socketPath,
                                       const std::set<std::uint32_t>& allowedPeerUids,
                                       std::string& reason, std::size_t maxRequestBytes,
                                       int ioTimeoutMs)
{
    std::lock_guard<std::mutex> lifecycleLock(m_lifecycleMutex);
    if (!m_stop.load() || allowedPeerUids.empty() || maxRequestBytes < 1024 ||
        maxRequestBytes > 1024u * 1024u || ioTimeoutMs < 1)
    {
        reason = "EXECUTION_SERVER_INVALID_CONFIG";
        return false;
    }
    ExecutionServiceIdentity identity;
    if (!GenerateExecutionServiceIdentity(1, identity, reason)) return false;
    std::shared_ptr<ExecutionServiceLifecycleGate> lifecycleGate(
        new ExecutionServiceLifecycleGate());
    lifecycleGate->ready.store(true);
    struct sockaddr_un address;
    if (!BuildAddress(socketPath, address, reason)) return false;
    const int socketLockFd = LockSocketPath(socketPath, reason);
    if (socketLockFd < 0) return false;
    const int listenFd = ::socket(AF_UNIX, SOCK_STREAM | SOCK_CLOEXEC, 0);
    if (listenFd < 0)
    {
        UnlockAndClose(socketLockFd);
        reason = "EXECUTION_SOCKET_CREATE_FAILED";
        return false;
    }
    struct stat existing;
    if (::lstat(socketPath.c_str(), &existing) == 0)
    {
        if (!ExistingSocketIsStale(socketPath, existing, reason))
        {
            ::close(listenFd);
            UnlockAndClose(socketLockFd);
            return false;
        }
        if (::unlink(socketPath.c_str()) != 0)
        {
            reason = "EXECUTION_STALE_SOCKET_UNLINK_FAILED";
            ::close(listenFd);
            UnlockAndClose(socketLockFd);
            return false;
        }
    }
    else if (errno != ENOENT)
    {
        reason = "EXECUTION_SOCKET_PATH_INSPECTION_FAILED";
        ::close(listenFd);
        UnlockAndClose(socketLockFd);
        return false;
    }
    if (::bind(listenFd, reinterpret_cast<struct sockaddr*>(&address), sizeof(address)) != 0)
    {
        reason = std::string("EXECUTION_SOCKET_START_FAILED:") + std::strerror(errno);
        ::close(listenFd);
        UnlockAndClose(socketLockFd);
        return false;
    }
    struct stat bound;
    if (::lstat(socketPath.c_str(), &bound) != 0 || !S_ISSOCK(bound.st_mode))
    {
        reason = "EXECUTION_BOUND_SOCKET_INSPECTION_FAILED";
        ::close(listenFd);
        UnlockAndClose(socketLockFd);
        return false;
    }
    const std::uint64_t socketDevice = static_cast<std::uint64_t>(bound.st_dev);
    const std::uint64_t socketInode = static_cast<std::uint64_t>(bound.st_ino);
    if (::chmod(socketPath.c_str(), 0600) != 0 || ::listen(listenFd, 16) != 0)
    {
        reason = std::string("EXECUTION_SOCKET_START_FAILED:") + std::strerror(errno);
        ::close(listenFd);
        UnlinkSocketIfIdentityMatches(socketPath, socketDevice, socketInode);
        UnlockAndClose(socketLockFd);
        return false;
    }
    m_socketPath = socketPath;
    m_socketDevice = socketDevice;
    m_socketInode = socketInode;
    m_ownsSocketPath = true;
    m_socketLockFd = socketLockFd;
    m_allowedPeerUids = allowedPeerUids;
    m_maxRequestBytes = maxRequestBytes;
    m_ioTimeoutMs = ioTimeoutMs;
    m_serviceIdentity = identity;
    m_lifecycleGate = lifecycleGate;
    m_listenFd.store(listenFd);
    m_stop.store(false);
    try
    {
        m_acceptThread = std::thread(&UnixExecutionServiceServer::AcceptLoop, this);
    }
    catch (const std::exception&)
    {
        m_stop.store(true);
        m_lifecycleGate->ready.store(false);
        m_listenFd.store(-1);
        ::close(listenFd);
        UnlinkSocketIfIdentityMatches(socketPath, m_socketDevice, m_socketInode);
        UnlockAndClose(m_socketLockFd);
        m_socketLockFd = -1;
        m_ownsSocketPath = false;
        // Startup failures are returned to the embedding process as a reason
        // string.  Keep implementation/OS exception text out of that API as
        // well; no local logger is attached to this service boundary.
        reason = "EXECUTION_ACCEPT_THREAD_START_FAILED";
        return false;
    }
    reason.clear();
    return true;
}
bool UnixExecutionServiceServer::StartFromFd(
    int listenFd,
    const std::set<std::uint32_t>& allowedPeerUids,
    std::string& reason,
    std::size_t maxRequestBytes,
    int ioTimeoutMs)
{
    ExecutionServiceIdentity identity;
    if (!GenerateExecutionServiceIdentity(1, identity, reason))
    {
        if (listenFd >= 0) ::close(listenFd);
        return false;
    }
    return StartFromFd(listenFd, allowedPeerUids, identity, reason,
        maxRequestBytes, ioTimeoutMs);
}
bool UnixExecutionServiceServer::StartFromFd(
    int listenFd,
    const std::set<std::uint32_t>& allowedPeerUids,
    const ExecutionServiceIdentity& identity,
    std::string& reason,
    std::size_t maxRequestBytes,
    int ioTimeoutMs)
{
    std::shared_ptr<ExecutionServiceLifecycleGate> lifecycleGate(
        new ExecutionServiceLifecycleGate());
    lifecycleGate->ready.store(true);
    return StartFromFd(listenFd, allowedPeerUids, identity, lifecycleGate,
        reason, maxRequestBytes, ioTimeoutMs);
}
bool UnixExecutionServiceServer::StartFromFd(
    int listenFd,
    const std::set<std::uint32_t>& allowedPeerUids,
    const ExecutionServiceIdentity& identity,
    const std::shared_ptr<ExecutionServiceLifecycleGate>& lifecycleGate,
    std::string& reason,
    std::size_t maxRequestBytes,
    int ioTimeoutMs)
{
    return StartFromFdInternal(listenFd, allowedPeerUids, nullptr, identity,
        lifecycleGate, reason, maxRequestBytes, ioTimeoutMs);
}
bool UnixExecutionServiceServer::StartFromFd(
    int listenFd,
    const std::set<std::uint32_t>& allowedPeerUids,
    const ExecutionGatewayContextBinding& gatewayContextBinding,
    const ExecutionServiceIdentity& identity,
    const std::shared_ptr<ExecutionServiceLifecycleGate>& lifecycleGate,
    std::string& reason,
    std::size_t maxRequestBytes,
    int ioTimeoutMs)
{
    return StartFromFdInternal(listenFd, allowedPeerUids,
        &gatewayContextBinding, identity, lifecycleGate, reason,
        maxRequestBytes, ioTimeoutMs);
}
bool UnixExecutionServiceServer::StartFromFdInternal(
    int listenFd,
    const std::set<std::uint32_t>& allowedPeerUids,
    const ExecutionGatewayContextBinding* gatewayContextBinding,
    const ExecutionServiceIdentity& identity,
    const std::shared_ptr<ExecutionServiceLifecycleGate>& lifecycleGate,
    std::string& reason,
    std::size_t maxRequestBytes,
    int ioTimeoutMs)
{
    std::lock_guard<std::mutex> lifecycleLock(m_lifecycleMutex);
    if (!m_stop.load() || allowedPeerUids.empty() || maxRequestBytes < 1024 ||
        maxRequestBytes > 1024u * 1024u ||
        ioTimeoutMs < 1 || !ValidIdentity(identity) || !lifecycleGate ||
        (gatewayContextBinding != nullptr &&
         !gatewayContextBinding->Complete()))
    {
        if (listenFd >= 0) ::close(listenFd);
        reason = "EXECUTION_SERVER_INVALID_CONFIG";
        return false;
    }
    if (!ValidateActivatedSocket(listenFd, reason))
    {
        if (listenFd >= 0) ::close(listenFd);
        return false;
    }
    m_socketPath.clear();
    m_socketDevice = 0;
    m_socketInode = 0;
    m_ownsSocketPath = false;
    m_socketLockFd = -1;
    m_allowedPeerUids = allowedPeerUids;
    m_gatewayContextBinding = gatewayContextBinding == nullptr ?
        ExecutionGatewayContextBinding() : *gatewayContextBinding;
    m_enforceGatewayContextBinding = gatewayContextBinding != nullptr;
    m_maxRequestBytes = maxRequestBytes;
    m_ioTimeoutMs = ioTimeoutMs;
    m_serviceIdentity = identity;
    m_lifecycleGate = lifecycleGate;
    m_listenFd.store(listenFd);
    m_stop.store(false);
    try
    {
        m_acceptThread = std::thread(&UnixExecutionServiceServer::AcceptLoop, this);
    }
    catch (const std::exception&)
    {
        m_stop.store(true);
        m_lifecycleGate->ready.store(false);
        m_listenFd.store(-1);
        ::close(listenFd);
        reason = "EXECUTION_ACCEPT_THREAD_START_FAILED";
        return false;
    }
    reason.clear();
    return true;
}
void UnixExecutionServiceServer::Stop()
{
    std::lock_guard<std::mutex> lifecycleLock(m_lifecycleMutex);
    if (m_lifecycleGate) m_lifecycleGate->ready.store(false);
    m_stop.store(true);
    if (m_acceptThread.joinable() &&
        m_acceptThread.get_id() == std::this_thread::get_id())
    {
        // A callback may request shutdown, but final join/close/path cleanup
        // must be performed later by the owning lifecycle thread.
        return;
    }
    if (m_acceptThread.joinable()) m_acceptThread.join();
    const int ownedListenFd = m_listenFd.exchange(-1);
    if (ownedListenFd >= 0) ::close(ownedListenFd);
    if (m_ownsSocketPath && !m_socketPath.empty())
        UnlinkSocketIfIdentityMatches(m_socketPath, m_socketDevice, m_socketInode);
    UnlockAndClose(m_socketLockFd);
    m_socketLockFd = -1;
    m_ownsSocketPath = false;
    m_socketDevice = 0;
    m_socketInode = 0;
    m_socketPath.clear();
    m_serviceIdentity = ExecutionServiceIdentity();
    m_gatewayContextBinding = ExecutionGatewayContextBinding();
    m_enforceGatewayContextBinding = false;
    m_lifecycleGate.reset();
    {
        std::lock_guard<std::mutex> previewLock(m_previewMutex);
        m_previewPermits.clear();
        m_previewDispatches.clear();
    }
}
bool UnixExecutionServiceServer::IsRunning() const
{
    return !m_stop.load() && m_listenFd.load() >= 0 && m_lifecycleGate &&
        (m_lifecycleGate->ready.load() ||
         m_lifecycleGate->terminalControlOnly.load());
}
std::string UnixExecutionServiceServer::ServiceEpoch() const
{
    std::lock_guard<std::mutex> lifecycleLock(m_lifecycleMutex);
    return m_serviceIdentity.serviceEpoch;
}
ExecutionServiceIdentity UnixExecutionServiceServer::ServiceIdentity() const
{
    std::lock_guard<std::mutex> lifecycleLock(m_lifecycleMutex);
    return m_serviceIdentity;
}
void UnixExecutionServiceServer::AcceptLoop()
{
    while (!m_stop.load())
    {
        const int listenFd = m_listenFd.load();
        if (listenFd < 0) break;
        struct pollfd pending;
        pending.fd = listenFd;
        pending.events = POLLIN;
        pending.revents = 0;
        const int pollResult = ::poll(&pending, 1, 100);
        if (pollResult < 0 && errno == EINTR) continue;
        if (pollResult <= 0) continue;
        if ((pending.revents & POLLIN) == 0)
        {
            if (m_stop.load()) break;
            continue;
        }
        const int clientFd = ::accept4(
            listenFd, nullptr, nullptr, SOCK_CLOEXEC | SOCK_NONBLOCK);
        if (clientFd < 0)
        {
            if (errno == EINTR) continue;
            if (m_stop.load() || errno == EBADF || errno == EINVAL) break;
            continue;
        }
        HandleClient(clientFd);
        ::close(clientFd);
    }
}
bool UnixExecutionServiceServer::ReadAuthorizedRequest(
    int clientFd,
    const std::chrono::steady_clock::time_point& deadline,
    ExecutionServiceRequest& request,
    std::string& reason)
{
    struct ucred credential;
    socklen_t credentialLength = sizeof(credential);
    if (::getsockopt(clientFd, SOL_SOCKET, SO_PEERCRED, &credential, &credentialLength) != 0 ||
        credentialLength != sizeof(credential) ||
        m_allowedPeerUids.find(static_cast<std::uint32_t>(credential.uid)) == m_allowedPeerUids.end())
        return false;
    std::string requestBody;
    if (!ReadFrame(clientFd, m_maxRequestBytes, deadline, requestBody))
        return false;
    return ExecutionServiceProtocol::DecodeRequest(requestBody, request, reason);
}
namespace
{
void RejectExecutionRequest(const ExecutionServiceRequest& request,
                            const std::string& reasonCode,
                            ExecutionCommandResult& result,
                            ExecutionControlResult& controlResult,
                            bool& controlResponse)
{
    controlResponse = IsControlOperation(request.operation);
    const AgentExecutionContext* context = RequestContext(request);
    const std::string commandId = context == nullptr ?
        std::string() : context->toolCallId;
    if (controlResponse)
    {
        controlResult.status = ExecutionCommandStatus::Rejected;
        controlResult.commandId = commandId;
        controlResult.targetCommandId = request.control.targetCommandId;
        controlResult.reasonCode = reasonCode;
    }
    else
    {
        result.status = ExecutionCommandStatus::Rejected;
        result.commandId = commandId;
        result.reasonCode = reasonCode;
    }
}
}
bool UnixExecutionServiceServer::ApplyPreDispatchGate(
    const ExecutionServiceRequest& request,
    ExecutionCommandResult& result,
    ExecutionControlResult& controlResult,
    bool& controlResponse)
{
    if (request.operation == ExecutionServiceOperation::GetServiceIdentity)
    {
        result.commandId = "__service_identity__";
        if (!m_lifecycleGate ||
            (!m_lifecycleGate->ready.load() &&
             !m_lifecycleGate->terminalControlOnly.load()))
        {
            result.status = ExecutionCommandStatus::Rejected;
            result.reasonCode = "EXECUTION_SERVICE_NOT_READY";
        }
        else
        {
            result.status = ExecutionCommandStatus::Accepted;
            result.reasonCode = "EXECUTION_SERVICE_IDENTITY";
        }
        return false;
    }
    if (m_enforceGatewayContextBinding &&
        (RequestContext(request) == nullptr ||
         !m_gatewayContextBinding.Matches(*RequestContext(request))))
    {
        RejectExecutionRequest(request,
            "EXECUTION_GATEWAY_CONTEXT_BINDING_MISMATCH",
            result, controlResult, controlResponse);
        return false;
    }
    if (request.expectedServiceEpoch != m_serviceIdentity.serviceEpoch ||
        request.expectedServiceFencingGeneration !=
            m_serviceIdentity.serviceFencingGeneration)
    {
        RejectExecutionRequest(request, "EXECUTION_SERVICE_EPOCH_MISMATCH",
            result, controlResult, controlResponse);
        return false;
    }
    if (!m_lifecycleGate ||
        (!m_lifecycleGate->ready.load() &&
         !(m_lifecycleGate->terminalControlOnly.load() &&
           request.operation ==
               ExecutionServiceOperation::TerminalizeRecoveryOwner)))
    {
        RejectExecutionRequest(request, "EXECUTION_SERVICE_NOT_READY",
            result, controlResult, controlResponse);
        return false;
    }
    return true;
}
ExecutionCommandResult UnixExecutionServiceServer::DispatchPlaceOrder(
    const IbPlaceOrderCommand& command)
{
    IbPlaceOrderCommand authorized = command;
    const std::string dispatchKey = PlaceDispatchKey(command);
    const std::string dispatchFingerprint = PreviewFingerprint(command);

    // Check the local transition witness before consulting the authority.
    // This is intentionally a short lock-only section: authority callbacks
    // must never run while m_previewMutex is held, otherwise a callback that
    // performs a status/read RPC could deadlock the service.
    {
        std::lock_guard<std::mutex> lock(m_previewMutex);
        const std::chrono::steady_clock::time_point steadyNow =
            std::chrono::steady_clock::now();
        for (std::unordered_map<std::string,
                 PreviewDispatchRecord>::iterator it =
                 m_previewDispatches.begin();
             it != m_previewDispatches.end();)
        {
            if (it->second.complete &&
                it->second.steadyExpiresAt <= steadyNow)
                it = m_previewDispatches.erase(it);
            else
                ++it;
        }
        const std::unordered_map<std::string,
            PreviewDispatchRecord>::const_iterator existing =
            m_previewDispatches.find(dispatchKey);
        if (existing != m_previewDispatches.end())
        {
            if (existing->second.fingerprint != dispatchFingerprint)
                return PreviewDispatchConflictResult(command.context.toolCallId);
            if (!existing->second.complete)
                return PreviewDispatchInFlightResult(command.context.toolCallId);
            return ReplayPreviewDispatchResult(existing->second.result);
        }
    }

    std::string permitReason;
    bool durablePlaceReplay = false;
    try
    {
        durablePlaceReplay = m_readAuthority != nullptr &&
            m_readAuthority->IsDurablePlaceReplay(authorized);
    }
    catch (...)
    {
        // A replay probe is advisory.  If the read facet is unavailable or
        // throws, continue through the normal permit gate; a valid permit can
        // still be dispatched and the authority will provide the durable
        // outcome.
        durablePlaceReplay = false;
    }
    if (durablePlaceReplay)
    {
        // A durable replay probe is advisory, but the dispatch itself still
        // needs a local claim.  Two callers can observe the same durable
        // journal record concurrently (especially when this method is used
        // directly by an embedded composition); claim the command before
        // invoking the authority so only one callback is made and the other
        // receives the same in-flight/replay semantics as a permit-backed
        // dispatch.
        {
            std::lock_guard<std::mutex> lock(m_previewMutex);
            const std::chrono::steady_clock::time_point steadyNow =
                std::chrono::steady_clock::now();
            for (std::unordered_map<std::string,
                     PreviewDispatchRecord>::iterator it =
                     m_previewDispatches.begin();
                 it != m_previewDispatches.end();)
            {
                if (it->second.complete &&
                    it->second.steadyExpiresAt <= steadyNow)
                    it = m_previewDispatches.erase(it);
                else
                    ++it;
            }
            const std::unordered_map<std::string,
                PreviewDispatchRecord>::const_iterator existing =
                m_previewDispatches.find(dispatchKey);
            if (existing != m_previewDispatches.end())
            {
                if (existing->second.fingerprint != dispatchFingerprint)
                    return PreviewDispatchConflictResult(
                        command.context.toolCallId);
                if (!existing->second.complete)
                    return PreviewDispatchInFlightResult(
                        command.context.toolCallId);
                return ReplayPreviewDispatchResult(existing->second.result);
            }
            if (m_previewDispatches.size() >= kMaxPreviewDispatchRecords)
            {
                // Completed witnesses are only a local replay optimization;
                // evict the one with the earliest expiry to keep a burst of
                // unique command ids from denying all later dispatches.  An
                // in-flight witness is never evicted, since dropping it would
                // allow a concurrent retry to issue a second mutation.
                std::unordered_map<std::string,
                    PreviewDispatchRecord>::iterator oldest =
                    m_previewDispatches.end();
                for (std::unordered_map<std::string,
                         PreviewDispatchRecord>::iterator it =
                         m_previewDispatches.begin();
                     it != m_previewDispatches.end(); ++it)
                {
                    if (!it->second.complete) continue;
                    if (oldest == m_previewDispatches.end() ||
                        it->second.steadyExpiresAt <
                            oldest->second.steadyExpiresAt)
                        oldest = it;
                }
                if (oldest != m_previewDispatches.end())
                    m_previewDispatches.erase(oldest);
                else
                {
                    ExecutionCommandResult capacity;
                    capacity.status = ExecutionCommandStatus::Rejected;
                    capacity.commandId = command.context.toolCallId;
                    capacity.reasonCode =
                        "EXECUTION_PREVIEW_DISPATCH_CAPACITY_EXHAUSTED";
                    capacity.detail =
                        "too many preview mutations are currently in flight or retained for replay";
                    return capacity;
                }
            }
            PreviewDispatchRecord dispatch;
            dispatch.ownerKey = PreviewOwnerKey(command);
            dispatch.fingerprint = dispatchFingerprint;
            dispatch.flatten = false;
            dispatch.complete = false;
            // Durable replay has no raw permit. Keep the marker until the
            // authority returns; completed entries receive the normal replay
            // TTL below.
            dispatch.steadyExpiresAt = steadyNow +
                kPreviewDispatchReplayTtl;
            m_previewDispatches[dispatchKey] = dispatch;
        }
        authorized.previewPermit.clear();
        ExecutionCommandResult replay;
        try
        {
            replay = m_authority.PlaceOrder(authorized);
        }
        catch (const std::exception&)
        {
            replay.status = ExecutionCommandStatus::Uncertain;
            replay.commandId = command.context.toolCallId;
            replay.reasonCode = "EXECUTION_AUTHORITY_EXCEPTION";
            // Exception text can contain venue paths, credentials or other
            // process-local details.  This result crosses the Unix IPC
            // boundary, so expose only the stable reconciliation guidance.
            replay.detail = "execution authority outcome is uncertain";
        }
        catch (...)
        {
            replay.status = ExecutionCommandStatus::Uncertain;
            replay.commandId = command.context.toolCallId;
            replay.reasonCode = "EXECUTION_AUTHORITY_EXCEPTION";
            replay.detail = "execution authority outcome is uncertain";
        }
        if (replay.commandId.empty())
            replay.commandId = command.context.toolCallId;
        // A durable replay result is safe to retain locally as well.  This
        // avoids a second authority call if another retry arrives before the
        // caller has observed the response.  Do not retain a raw permit.
        {
            std::lock_guard<std::mutex> lock(m_previewMutex);
            const std::chrono::steady_clock::time_point steadyNow =
                std::chrono::steady_clock::now();
            for (std::unordered_map<std::string,
                     PreviewDispatchRecord>::iterator it =
                     m_previewDispatches.begin();
                 it != m_previewDispatches.end();)
            {
                if (it->second.complete &&
                    it->second.steadyExpiresAt <= steadyNow)
                    it = m_previewDispatches.erase(it);
                else
                    ++it;
            }
            const std::unordered_map<std::string,
                PreviewDispatchRecord>::iterator claimed =
                m_previewDispatches.find(dispatchKey);
            if (claimed != m_previewDispatches.end() &&
                claimed->second.fingerprint == dispatchFingerprint)
            {
                claimed->second.complete = true;
                claimed->second.result = replay;
                claimed->second.permit.clear();
                claimed->second.steadyExpiresAt = steadyNow +
                    kPreviewDispatchReplayTtl;
            }
        }
        return replay;
    }
    ExecutionCommandResult result;
    // Validate first without consuming.  Lease acquisition is an independent
    // safety gate; if it is unavailable/busy the caller must be able to retry
    // the same server-issued permit once the gate recovers.
    if (!ValidatePreviewPermit(authorized, permitReason))
    {
        result.status = ExecutionCommandStatus::Rejected;
        result.commandId = command.context.toolCallId;
        result.reasonCode = permitReason;
        result.detail =
            "Execution Service rejected the missing, expired, replayed, or mismatched preview permit";
        return result;
    }
    const std::string instrument = authorized.instrument.empty() ?
        authorized.contract.symbol : authorized.instrument;
    std::string leaseReason;
    if (!m_decisionLeases ||
        !m_decisionLeases->Authorize(authorized.context, instrument, leaseReason))
    {
        result.status = ExecutionCommandStatus::Rejected;
        result.commandId = command.context.toolCallId;
        result.reasonCode = leaseReason.empty() ?
            "EXECUTION_DECISION_LEASE_AUTHORITY_REQUIRED" : leaseReason;
        result.detail = "Execution Service could not grant the mutation lease";
        return result;
    }

    // Claim the command identity before consuming the permit.  This closes
    // the small race between validation and ConsumePreviewPermit: an exact
    // concurrent retry now receives a typed in-flight result instead of
    // observing an opaque permit-unknown response.  Only bounded metadata is
    // retained while the authority call is outside the lock.
    {
        std::lock_guard<std::mutex> lock(m_previewMutex);
        const std::chrono::steady_clock::time_point steadyNow =
            std::chrono::steady_clock::now();
        for (std::unordered_map<std::string,
                 PreviewDispatchRecord>::iterator it =
                 m_previewDispatches.begin();
             it != m_previewDispatches.end();)
        {
            if (it->second.complete &&
                it->second.steadyExpiresAt <= steadyNow)
                it = m_previewDispatches.erase(it);
            else
                ++it;
        }
        const std::unordered_map<std::string,
            PreviewDispatchRecord>::const_iterator existing =
            m_previewDispatches.find(dispatchKey);
        if (existing != m_previewDispatches.end())
        {
            if (existing->second.fingerprint != dispatchFingerprint)
                return PreviewDispatchConflictResult(command.context.toolCallId);
            if (!existing->second.complete)
                return PreviewDispatchInFlightResult(command.context.toolCallId);
            return ReplayPreviewDispatchResult(existing->second.result);
        }
        if (m_previewDispatches.size() >= kMaxPreviewDispatchRecords)
        {
            std::unordered_map<std::string,
                PreviewDispatchRecord>::iterator oldest =
                m_previewDispatches.end();
            for (std::unordered_map<std::string,
                     PreviewDispatchRecord>::iterator it =
                     m_previewDispatches.begin();
                 it != m_previewDispatches.end(); ++it)
            {
                if (!it->second.complete) continue;
                if (oldest == m_previewDispatches.end() ||
                    it->second.steadyExpiresAt <
                        oldest->second.steadyExpiresAt)
                    oldest = it;
            }
            if (oldest != m_previewDispatches.end())
                m_previewDispatches.erase(oldest);
            else
            {
                result.status = ExecutionCommandStatus::Rejected;
                result.commandId = command.context.toolCallId;
                result.reasonCode =
                    "EXECUTION_PREVIEW_DISPATCH_CAPACITY_EXHAUSTED";
                result.detail =
                    "too many preview mutations are currently in flight or retained for replay";
                return result;
            }
        }
        PreviewDispatchRecord dispatch;
        dispatch.ownerKey = PreviewOwnerKey(command);
        dispatch.fingerprint = dispatchFingerprint;
        dispatch.permit = command.previewPermit;
        dispatch.flatten = false;
        dispatch.complete = false;
        // Keep an in-flight witness until the authority returns.  Its expiry
        // is only a cleanup hint; a completed uncertain result is retained by
        // the bounded replay TTL below.
        dispatch.steadyExpiresAt = steadyNow +
            std::chrono::milliseconds(1);
        m_previewDispatches[dispatchKey] = dispatch;
    }

    // Revalidate under the permit-store lock and consume only after the lease
    // gate succeeds.  A concurrent retry can win this race; in that case no
    // venue call is attempted with a stale credential.
    if (!ConsumePreviewPermit(authorized, permitReason))
    {
        std::lock_guard<std::mutex> lock(m_previewMutex);
        const std::unordered_map<std::string,
            PreviewDispatchRecord>::iterator claimed =
            m_previewDispatches.find(dispatchKey);
        if (claimed != m_previewDispatches.end() &&
            !claimed->second.complete &&
            claimed->second.fingerprint == dispatchFingerprint)
            m_previewDispatches.erase(claimed);
        result.status = ExecutionCommandStatus::Rejected;
        result.commandId = command.context.toolCallId;
        result.reasonCode = permitReason;
        result.detail =
            "Execution Service rejected the preview permit after lease validation";
        return result;
    }
    authorized.previewPermit.clear();
    try
    {
        result = m_authority.PlaceOrder(authorized);
    }
    catch (const std::exception&)
    {
        result.status = ExecutionCommandStatus::Uncertain;
        result.commandId = command.context.toolCallId;
        result.reasonCode = "EXECUTION_AUTHORITY_EXCEPTION";
        // Do not return raw authority exception text to an Agent/MCP peer.
        result.detail = "execution authority outcome is uncertain";
    }
    catch (...)
    {
        result.status = ExecutionCommandStatus::Uncertain;
        result.commandId = command.context.toolCallId;
        result.reasonCode = "EXECUTION_AUTHORITY_EXCEPTION";
        result.detail = "execution authority outcome is uncertain";
    }
    if (result.commandId.empty()) result.commandId = command.context.toolCallId;

    {
        std::lock_guard<std::mutex> lock(m_previewMutex);
        const std::unordered_map<std::string,
            PreviewDispatchRecord>::iterator claimed =
            m_previewDispatches.find(dispatchKey);
        if (claimed != m_previewDispatches.end() &&
            claimed->second.fingerprint == dispatchFingerprint)
        {
            // The permit was consumed before authority dispatch. Retain the
            // exact response even when it is Rejected/Error: replaying that
            // terminal result is safer than attempting a second venue call
            // after an outcome that may have crossed an opaque authority
            // boundary.
            if (ShouldRetainPreviewDispatch(result))
            {
                claimed->second.complete = true;
                claimed->second.result = result;
                claimed->second.permit.clear();
                claimed->second.steadyExpiresAt =
                    std::chrono::steady_clock::now() +
                    kPreviewDispatchReplayTtl;
            }
        }
    }
    return result;
}
ExecutionCommandResult UnixExecutionServiceServer::DispatchPreviewOrder(
    const IbPlaceOrderCommand& command)
{
    ExecutionCommandResult result;
    if (m_readAuthority == nullptr)
    {
        result.status = ExecutionCommandStatus::Rejected;
        result.commandId = command.context.toolCallId;
        result.reasonCode = "EXECUTION_PREVIEW_UNAVAILABLE";
        return result;
    }
    result = m_readAuthority->PreviewOrder(command);
    if (result.status != ExecutionCommandStatus::Accepted) return result;
    if (!result.detail.empty() && !ValidPreviewJson(result.detail))
    {
        result.status = ExecutionCommandStatus::Rejected;
        result.commandId = command.context.toolCallId;
        result.reasonCode = "EXECUTION_PREVIEW_RESPONSE_INVALID";
        result.detail = "execution authority response was invalid";
        return result;
    }
    std::string permit;
    std::string mutationCommandId;
    long long permitExpiry = 0;
    std::string permitReason;
    if (!IssuePreviewPermit(command, permit, mutationCommandId,
                            permitExpiry, permitReason))
    {
        result.status = ExecutionCommandStatus::Rejected;
        result.reasonCode = permitReason;
        result.detail.clear();
        return result;
    }
    const std::string authoritative = result.detail.empty() ? "null" : result.detail;
    std::ostringstream payload;
    payload.imbue(std::locale::classic());
    payload << "{\"approved\":true,\"preview_permit\":\"" << permit
            << "\",\"command_id\":\"" << mutationCommandId
            << "\",\"permit_expires_at_ms\":" << permitExpiry
            << ",\"single_use\":true,\"service_epoch\":\""
            << m_serviceIdentity.serviceEpoch
            << "\",\"service_fencing_generation\":"
            << m_serviceIdentity.serviceFencingGeneration
            << ",\"authoritative_preview\":" << authoritative << '}';
    result.detail = payload.str();
    return result;
}
ExecutionControlResult UnixExecutionServiceServer::DispatchControl(
    const ExecutionServiceRequest& request)
{
    ExecutionControlResult result;
    if (m_controlAuthority == nullptr)
    {
        result.status = ExecutionCommandStatus::Rejected;
        result.commandId = request.control.context.toolCallId;
        result.reasonCode = "EXECUTION_CONTROL_UNAVAILABLE";
        return result;
    }
    if (request.operation == ExecutionServiceOperation::QueryCommandStatus)
        return m_controlAuthority->QueryCommandStatus(request.control);
    if (request.operation ==
        ExecutionServiceOperation::RecoveryQueryCommandStatus)
    {
        RevokePreviewPermitsForOwner(request.control.context.agentId,
            request.control.context.sessionId);
        return m_controlAuthority->QueryCommandStatus(request.control);
    }
    if (request.operation == ExecutionServiceOperation::FenceSessionOwner)
    {
        RevokePreviewPermitsForOwner(request.control.context.agentId,
                                     request.control.context.sessionId);
        m_decisionLeases->FenceOwner(request.control.context.agentId,
                                     request.control.context.sessionId);
        return m_controlAuthority->FenceSessionOwner(request.control);
    }
    if (request.operation == ExecutionServiceOperation::ReleaseSessionOwnerFence)
        return m_controlAuthority->ReleaseSessionOwnerFence(request.control);
    if (request.operation == ExecutionServiceOperation::RecoveryAuditOwner)
    {
        RevokePreviewPermitsForOwner(request.control.context.agentId,
            request.control.context.sessionId);
        m_decisionLeases->FenceOwner(request.control.context.agentId,
            request.control.context.sessionId);
        return m_controlAuthority->RecoveryAuditOwner(request.control);
    }
    if (request.operation ==
        ExecutionServiceOperation::TerminalizeRecoveryOwner)
    {
        RevokePreviewPermitsForOwner(request.control.context.agentId,
            request.control.context.sessionId);
        m_decisionLeases->FenceOwner(request.control.context.agentId,
            request.control.context.sessionId);
        return m_controlAuthority->TerminalizeRecoveryOwner(
            request.control);
    }
    return m_controlAuthority->ReconcileAuthoritativeState(request.control);
}
void UnixExecutionServiceServer::DispatchRequest(
    const ExecutionServiceRequest& request,
    ExecutionCommandResult& result,
    ExecutionControlResult& controlResult,
    bool& controlResponse)
{
    // The accept loop services clients on its own thread.  An authority or
    // projection callback is untrusted process-local code and must never be
    // allowed to unwind through that loop (which would terminate the service
    // and turn an unknown mutation outcome into a fail-open restart).  The
    // mutation paths below already classify their venue callback failures;
    // this outer boundary covers direct Cancel/read/control dispatches and
    // any future operation added without an inner guard.
    try
    {
        if (request.operation == ExecutionServiceOperation::PlaceIbOrder)
            result = DispatchPlaceOrder(request.place);
        else if (request.operation == ExecutionServiceOperation::CancelIbOrder)
            result = m_authority.CancelOrder(request.cancel);
        else if (request.operation ==
                 ExecutionServiceOperation::FlattenPosition)
            result = DispatchFlattenPosition(request.flatten);
        else if (request.operation == ExecutionServiceOperation::ReadAuthoritativeState)
        {
            if (m_readAuthority == nullptr)
            {
                result.status = ExecutionCommandStatus::Rejected;
                result.commandId = request.read.context.toolCallId;
                result.reasonCode = "EXECUTION_READ_UNAVAILABLE";
            }
            else
                result = m_readAuthority->ReadAuthoritativeState(request.read);
        }
        else if (request.operation == ExecutionServiceOperation::PreviewOrder)
            result = DispatchPreviewOrder(request.place);
        else if (request.operation == ExecutionServiceOperation::PreviewFlattenPosition)
            result = DispatchFlattenPreview(request.flatten);
        else
        {
            controlResponse = true;
            controlResult = DispatchControl(request);
        }
    }
    catch (const std::exception&)
    {
        const AgentExecutionContext* context = RequestContext(request);
        const std::string commandId = context == nullptr ? std::string() :
            context->toolCallId;
        if (controlResponse)
        {
            controlResult = ExecutionControlResult();
            controlResult.status = ExecutionCommandStatus::Uncertain;
            controlResult.commandId = commandId;
            controlResult.targetCommandId = request.control.targetCommandId;
            controlResult.reasonCode = "EXECUTION_AUTHORITY_EXCEPTION";
            // Keep internal exception details out of the IPC response.  The
            // uncertain status forces command reconciliation before retry.
            controlResult.detail = "execution authority outcome is uncertain";
        }
        else
        {
            result = ExecutionCommandResult();
            const bool readOnly = request.operation ==
                    ExecutionServiceOperation::ReadAuthoritativeState ||
                request.operation == ExecutionServiceOperation::PreviewOrder ||
                request.operation ==
                    ExecutionServiceOperation::PreviewFlattenPosition;
            result.status = readOnly ?
                ExecutionCommandStatus::Rejected :
                ExecutionCommandStatus::Uncertain;
            result.commandId = commandId;
            result.reasonCode = "EXECUTION_AUTHORITY_EXCEPTION";
            // Keep internal exception details out of the IPC response.  Read
            // and preview failures remain rejected; mutations remain
            // uncertain.
            result.detail = readOnly ?
                "tool dispatch failed" :
                "execution authority outcome is uncertain";
        }
    }
    catch (...)
    {
        const AgentExecutionContext* context = RequestContext(request);
        const std::string commandId = context == nullptr ? std::string() :
            context->toolCallId;
        if (controlResponse)
        {
            controlResult = ExecutionControlResult();
            controlResult.status = ExecutionCommandStatus::Uncertain;
            controlResult.commandId = commandId;
            controlResult.targetCommandId = request.control.targetCommandId;
            controlResult.reasonCode = "EXECUTION_AUTHORITY_EXCEPTION";
            controlResult.detail = "execution authority outcome is uncertain";
        }
        else
        {
            result = ExecutionCommandResult();
            const bool readOnly = request.operation ==
                    ExecutionServiceOperation::ReadAuthoritativeState ||
                request.operation == ExecutionServiceOperation::PreviewOrder ||
                request.operation ==
                    ExecutionServiceOperation::PreviewFlattenPosition;
            result.status = readOnly ?
                ExecutionCommandStatus::Rejected :
                ExecutionCommandStatus::Uncertain;
            result.commandId = commandId;
            result.reasonCode = "EXECUTION_AUTHORITY_EXCEPTION";
            result.detail = readOnly ?
                "tool dispatch failed" :
                "execution authority outcome is uncertain";
        }
    }
}
void UnixExecutionServiceServer::ValidateAndBindResponse(
    const ExecutionServiceRequest& request,
    ExecutionCommandResult& result,
    ExecutionControlResult& controlResult,
    bool controlResponse) const
{
    const std::string expectedCommandId =
        request.operation == ExecutionServiceOperation::GetServiceIdentity ?
            "__service_identity__" : (controlResponse ?
        request.control.context.toolCallId :
        (RequestContext(request) == nullptr ? std::string() :
            RequestContext(request)->toolCallId));
    if ((!controlResponse && result.commandId != expectedCommandId) ||
        (controlResponse && controlResult.commandId != expectedCommandId))
    {
        if (controlResponse)
            controlResult = ControlTransportFailure(expectedCommandId,
                "EXECUTION_AUTHORITY_RESPONSE_COMMAND_ID_MISMATCH");
        else
            result = TransportFailure(expectedCommandId,
                "EXECUTION_AUTHORITY_RESPONSE_COMMAND_ID_MISMATCH");
    }
    // This is the final response admission point before Encode(Response).
    // Sanitize after command-id binding so both normal authority results and
    // transport-failure replacements receive the same wire policy.
    if (controlResponse)
        SanitizeExecutionControlResultForWire(controlResult);
    else
        SanitizeExecutionResultForWire(request, result);
    result.serviceEpoch = m_serviceIdentity.serviceEpoch;
    result.serviceFencingGeneration = m_serviceIdentity.serviceFencingGeneration;
    controlResult.serviceEpoch = m_serviceIdentity.serviceEpoch;
    controlResult.serviceFencingGeneration =
        m_serviceIdentity.serviceFencingGeneration;
}
void UnixExecutionServiceServer::HandleClient(int clientFd)
{
    const IoDeadline deadline = DeadlineAfter(m_ioTimeoutMs);
    ExecutionServiceRequest request;
    std::string reason;
    ExecutionCommandResult result;
    ExecutionControlResult controlResult;
    bool controlResponse = false;
    if (!ReadAuthorizedRequest(clientFd, deadline, request, reason))
    {
        if (reason.empty()) return;
        result.status = ExecutionCommandStatus::Rejected;
        result.reasonCode = reason;
        result.detail =
            "Execution IPC request was rejected before authority dispatch";
        // Responses carry the current execution identity even when ingress
        // decoding fails.  The response codec deliberately requires a
        // non-empty service epoch; populate it here so malformed requests
        // receive a typed rejection instead of a silent connection close.
        result.serviceEpoch = m_serviceIdentity.serviceEpoch;
        result.serviceFencingGeneration =
            m_serviceIdentity.serviceFencingGeneration;
    }
    else
    {
        if (ApplyPreDispatchGate(request, result, controlResult, controlResponse))
            DispatchRequest(request, result, controlResult, controlResponse);
        ValidateAndBindResponse(request, result, controlResult, controlResponse);
    }
    std::string responseBody;
    const bool encoded = controlResponse ?
        ExecutionServiceProtocol::EncodeControlResponse(controlResult, responseBody, reason) :
        ExecutionServiceProtocol::EncodeResponse(result, responseBody, reason);
    if (encoded)
        WriteFrame(clientFd, responseBody, deadline);
}
