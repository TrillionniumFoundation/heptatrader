#include "execution_event_feed_server.h"

#include "execution_event_feed_transport.h"

#include <algorithm>
#include <cerrno>
#include <chrono>
#include <cmath>
#include <cstring>
#include <exception>
#include <fcntl.h>
#include <poll.h>
#include <sys/socket.h>
#include <sys/un.h>
#include <unistd.h>

using namespace HeptaExecutionEventFeedTransport;

namespace
{
bool ValidateActivatedSocket(int fd, std::string& reason)
{
    int socketType = 0;
    int accepting = 0;
    socklen_t socketTypeLength = sizeof(socketType);
    socklen_t acceptingLength = sizeof(accepting);
    struct sockaddr_un address;
    socklen_t addressLength = sizeof(address);
    std::memset(&address, 0, sizeof(address));
    if (fd < 0 ||
        ::getsockopt(fd, SOL_SOCKET, SO_TYPE, &socketType, &socketTypeLength) != 0 ||
        socketType != SOCK_STREAM ||
        ::getsockopt(fd, SOL_SOCKET, SO_ACCEPTCONN, &accepting, &acceptingLength) != 0 ||
        accepting != 1 ||
        ::getsockname(fd, reinterpret_cast<struct sockaddr*>(&address), &addressLength) != 0 ||
        address.sun_family != AF_UNIX)
    {
        reason = "EXECUTION_EVENT_ACTIVATED_FD_INVALID";
        return false;
    }
    const int flags = ::fcntl(fd, F_GETFD);
    if (flags < 0 || ::fcntl(fd, F_SETFD, flags | FD_CLOEXEC) != 0)
    {
        reason = "EXECUTION_EVENT_ACTIVATED_FD_CLOEXEC_FAILED";
        return false;
    }
    return true;
}

ExecutionEventReadResult ServiceStatus(const ExecutionServiceIdentity& identity,
                                       ExecutionEventReadStatus status,
                                       const std::string& reason,
                                       std::uint64_t latestSequence = 0)
{
    ExecutionEventReadResult result;
    result.status = status;
    result.serviceIdentity = identity;
    result.streamEpoch = identity.serviceEpoch;
    result.latestSequence = latestSequence;
    result.reasonCode = reason;
    return result;
}

bool ContainsAsciiInsensitive(const std::string& value,
                              const char* needle)
{
    if (needle == nullptr || *needle == '\0') return false;
    const std::size_t needleLength = std::strlen(needle);
    if (needleLength > value.size()) return false;
    for (std::size_t offset = 0;
         offset + needleLength <= value.size(); ++offset)
    {
        bool match = true;
        for (std::size_t i = 0; i < needleLength; ++i)
        {
            char left = value[offset + i];
            char right = needle[i];
            if (left >= 'A' && left <= 'Z') left =
                static_cast<char>(left - 'A' + 'a');
            if (right >= 'A' && right <= 'Z') right =
                static_cast<char>(right - 'A' + 'a');
            if (left != right)
            {
                match = false;
                break;
            }
        }
        if (match) return true;
    }
    return false;
}

// Event fields are mostly broker/status identifiers, so preserve ordinary
// structured values such as ``OWNER_QUEUE_BACKPRESSURE:count=1``.  Treat
// prose, control bytes, path-like values, and credential/error markers as
// exception-derived diagnostics that must not cross the Agent event socket.
//
// Control detection is UTF-8 aware.  A byte in 0x80..0x9f is a C1 control only
// when it occurs as a standalone byte; it is also a valid continuation byte
// in many non-ASCII UTF-8 code points and must not be rejected on that basis.
bool ContainsForbiddenControl(const std::string& value)
{
    std::size_t offset = 0;
    while (offset < value.size())
    {
        const unsigned char first =
            static_cast<unsigned char>(value[offset]);
        if (first < 0x20u || first == 0x7fu ||
            (first >= 0x80u && first <= 0x9fu)) return true;
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
            return true; // malformed UTF-8 is not safe wire text
        if (value.size() - offset <= continuationCount) return true;
        const unsigned char second =
            static_cast<unsigned char>(value[offset + 1]);
        if ((first == 0xe0u && second < 0xa0u) ||
            (first == 0xedu && second >= 0xa0u) ||
            (first == 0xf0u && second < 0x90u) ||
            (first == 0xf4u && second > 0x8fu)) return true;
        std::uint32_t codepoint = first &
            (continuationCount == 1 ? 0x1fu :
             continuationCount == 2 ? 0x0fu : 0x07u);
        for (std::size_t i = 1; i <= continuationCount; ++i)
        {
            const unsigned char continuation =
                static_cast<unsigned char>(value[offset + i]);
            if (continuation < 0x80u || continuation > 0xbfu) return true;
            codepoint = (codepoint << 6) | (continuation & 0x3fu);
        }
        if (codepoint < 0x20u || codepoint == 0x7fu ||
            (codepoint >= 0x80u && codepoint <= 0x9fu)) return true;
        offset += continuationCount + 1u;
    }
    return false;
}

bool IsCanonicalEventCode(const std::string& value)
{
    if (value.empty() || value.size() > 256 ||
        value[0] < 'A' || value[0] > 'Z')
        return false;
    for (std::size_t i = 0; i < value.size(); ++i)
    {
        const unsigned char c = static_cast<unsigned char>(value[i]);
        if (!((c >= 'A' && c <= 'Z') || (c >= '0' && c <= '9') ||
              c == '_' || c == ':' || c == '=' || c == '-' || c == '.'))
            return false;
    }
    return true;
}

bool LooksLikeExceptionText(const std::string& value,
                            bool pathLikeField)
{
    if (ContainsForbiddenControl(value)) return true;
    // Uppercase structured values are protocol status/type codes.  Preserve
    // suffixes such as `_FAILED`, `_TOKEN`, and `_EXCEPTION`; an actual
    // exception string carries prose/path syntax and fails the code grammar.
    if (IsCanonicalEventCode(value)) return false;
    static const char* const markers[] = {
        "exception", "what()", "credential", "secret", "password",
        "bearer", "authorization", "token", "private key", "api_key",
        "apikey", "errno", "stack trace", "threw", "could not",
        "not found", "failed"
    };
    for (std::size_t i = 0; i < sizeof(markers) / sizeof(markers[0]); ++i)
        if (ContainsAsciiInsensitive(value, markers[i])) return true;
    // A slash is valid in some instrument/venue identifiers (for example
    // ``EUR/USD``), so only classify unambiguously path-like forms for those
    // fields.  Leading separators, private/system path prefixes and URLs
    // remain blocked regardless of the caller's field classification.
    if (value.find("/private/") != std::string::npos ||
        value.find("\\private\\") != std::string::npos ||
        value.find("://") != std::string::npos ||
        (!value.empty() && (value[0] == '/' || value[0] == '\\')) ||
        (pathLikeField &&
         (value.find('/') != std::string::npos ||
          value.find('\\') != std::string::npos)))
        return true;
    return false;
}

bool StableEventReasonCode(const std::string& value)
{
    if (value.empty() || value.size() > 256 ||
        ContainsForbiddenControl(value))
        return false;
    const bool startsUpper = value[0] >= 'A' && value[0] <= 'Z';
    // Production reason codes are uppercase machine identifiers.  The only
    // lowercase form retained for compatibility is a structured health
    // witness such as `generation=7`/`state:ready`; free-form lowercase prose
    // (including credential-like `secret-token`) is not a reason code.
    if (!startsUpper && value.find('=') == std::string::npos &&
        value.find(':') == std::string::npos)
        return false;
    // Structured, non-prose reason codes (for example
    // OWNER_QUEUE_BACKPRESSURE:count=1) remain compatible with the existing
    // event hub contract; reject punctuation/prose that could make a malformed
    // source response look like a free-form diagnostic. Lower-case fields in
    // existing health reasons (for example generation=7) remain supported.
    for (std::size_t i = 0; i < value.size(); ++i)
    {
        const unsigned char c = static_cast<unsigned char>(value[i]);
        if (!((c >= 'A' && c <= 'Z') || (c >= 'a' && c <= 'z') ||
              (c >= '0' && c <= '9') || c == '_' || c == ':' ||
              c == '=' || c == '-' || c == '.'))
            return false;
    }
    if (!startsUpper)
    {
        static const char* const sensitive[] = {
            "exception", "credential", "secret", "password", "bearer",
            "authorization", "token", "private key", "api_key", "apikey",
            "errno", "stack trace", "threw", "failed"
        };
        for (std::size_t i = 0; i < sizeof(sensitive) / sizeof(sensitive[0]); ++i)
            if (ContainsAsciiInsensitive(value, sensitive[i])) return false;
    }
    return true;
}

bool HasEventPayload(const ExecutionEvent& event)
{
    return !event.streamEpoch.empty() || event.sequence != 0 ||
        !event.upstreamServiceEpoch.empty() ||
        event.upstreamServiceFencingGeneration != 0 ||
        !event.upstreamStreamEpoch.empty() || event.upstreamSequence != 0 ||
        event.timestampMs != 0 || !event.executionDomain.empty() ||
        !event.agentId.empty() || !event.sessionId.empty() ||
        !event.type.empty() || !event.venue.empty() || event.orderId != -1 ||
        !event.instrument.empty() || !event.side.empty() ||
        !event.status.empty() || !event.reasonCode.empty() ||
        event.filledQuantity != 0.0 || event.remainingQuantity != 0.0 ||
        event.averageFillPrice != 0.0;
}

bool RequiredEventTextIsBounded(const std::string& value)
{
    return !value.empty() && value.size() <= 256 &&
        !ContainsForbiddenControl(value);
}

ExecutionEventReadResult SourceExceptionResult(
    const ExecutionServiceIdentity& identity)
{
    return ServiceStatus(identity, ExecutionEventReadStatus::InvalidOwner,
        "EXECUTION_EVENT_SOURCE_EXCEPTION");
}

void RejectEventResponseForWire(ExecutionEventReadResult& result,
                                const ExecutionServiceIdentity& authorityIdentity,
                                const char* reasonCode)
{
    // Keep the replacement response protocol-valid even when the source
    // supplied malformed stream/watermark metadata alongside the diagnostic.
    // The server has already bound serviceIdentity to its own incarnation.
    result.status = ExecutionEventReadStatus::InvalidOwner;
    result.serviceIdentity = authorityIdentity;
    result.streamEpoch = authorityIdentity.serviceEpoch;
    result.droppedThroughSequence = 0;
    result.latestSequence = 0;
    result.reasonCode = reasonCode == nullptr ?
        "EXECUTION_EVENT_CALLBACK_EXCEPTION" : reasonCode;
    result.event = ExecutionEvent();
}

void SanitizeEventResponseForWire(
    ExecutionEventReadResult& result,
    const ExecutionServiceIdentity& authorityIdentity,
    const ExecutionEventFeedRequest* request)
{
    // The service identity is authoritative at this boundary.  Normalize the
    // source copy before inspecting any untrusted event fields so a malformed
    // stream epoch cannot make EncodeResponse silently drop the client.
    const std::string sourceStreamEpoch = result.streamEpoch;
    // Epochs are opaque service identifiers. Do not classify words such as
    // "secret" or path separators in an identifier as exception text; the
    // authoritative identity/equality check below is the security boundary.
    const bool sourceStreamEpochUnsafe = ContainsForbiddenControl(sourceStreamEpoch);
    const bool sourceStreamEpochMismatch =
        sourceStreamEpoch != authorityIdentity.serviceEpoch;
    result.serviceIdentity = authorityIdentity;
    result.streamEpoch = authorityIdentity.serviceEpoch;
    if (result.status != ExecutionEventReadStatus::Event)
    {
        // Lifecycle/gap/rejection responses carry no event payload by
        // contract. Clearing it here also prevents a faulty source from
        // smuggling callback diagnostics through a non-Event response.
        const bool hadEventPayload = HasEventPayload(result.event);
        result.event = ExecutionEvent();
        if (sourceStreamEpochUnsafe || sourceStreamEpochMismatch || hadEventPayload)
        {
            RejectEventResponseForWire(result,
                authorityIdentity,
                "EXECUTION_EVENT_SOURCE_IDENTITY_INVALID");
            return;
        }
        if (result.droppedThroughSequence > result.latestSequence)
        {
            RejectEventResponseForWire(result,
                authorityIdentity,
                "EXECUTION_EVENT_SOURCE_RESPONSE_INVALID");
            return;
        }
        switch (result.status)
        {
        case ExecutionEventReadStatus::Timeout:
            if (result.droppedThroughSequence != 0)
            {
                RejectEventResponseForWire(result, authorityIdentity,
                    "EXECUTION_EVENT_SOURCE_RESPONSE_INVALID");
                return;
            }
            result.reasonCode = "EXECUTION_EVENT_TIMEOUT";
            break;
        case ExecutionEventReadStatus::Gap:
            if (result.droppedThroughSequence == 0)
            {
                RejectEventResponseForWire(result, authorityIdentity,
                    "EXECUTION_EVENT_SOURCE_RESPONSE_INVALID");
                return;
            }
            result.reasonCode = "EXECUTION_EVENT_GAP";
            break;
        case ExecutionEventReadStatus::InvalidOwner:
            // Invalid-owner responses are intentionally generic. Preserve a
            // stable structured reason emitted by the server, but never echo
            // arbitrary source prose or malformed punctuation.
            result.reasonCode = StableEventReasonCode(result.reasonCode) ?
                result.reasonCode : "EXECUTION_EVENT_SOURCE_EXCEPTION";
            result.droppedThroughSequence = 0;
            result.latestSequence = 0;
            break;
        case ExecutionEventReadStatus::ServiceIdentity:
            if (result.droppedThroughSequence != 0)
            {
                RejectEventResponseForWire(result, authorityIdentity,
                    "EXECUTION_EVENT_SOURCE_RESPONSE_INVALID");
                return;
            }
            result.reasonCode = "EXECUTION_EVENT_SERVICE_IDENTITY";
            break;
        case ExecutionEventReadStatus::ServiceIdentityMismatch:
            if (result.droppedThroughSequence != 0 ||
                result.latestSequence != 0)
            {
                RejectEventResponseForWire(result, authorityIdentity,
                    "EXECUTION_EVENT_SOURCE_RESPONSE_INVALID");
                return;
            }
            result.reasonCode = "EXECUTION_EVENT_SERVICE_IDENTITY_MISMATCH";
            break;
        case ExecutionEventReadStatus::ServiceNotReady:
            if (result.droppedThroughSequence != 0 ||
                result.latestSequence != 0)
            {
                RejectEventResponseForWire(result, authorityIdentity,
                    "EXECUTION_EVENT_SOURCE_RESPONSE_INVALID");
                return;
            }
            result.reasonCode = "EXECUTION_EVENT_SERVICE_NOT_READY";
            break;
        case ExecutionEventReadStatus::ServiceStopping:
            if (result.droppedThroughSequence != 0 ||
                result.latestSequence != 0)
            {
                RejectEventResponseForWire(result, authorityIdentity,
                    "EXECUTION_EVENT_SOURCE_RESPONSE_INVALID");
                return;
            }
            result.reasonCode = "EXECUTION_EVENT_SERVICE_STOPPING";
            break;
        case ExecutionEventReadStatus::Event:
        case ExecutionEventReadStatus::EpochChanged:
        default:
            RejectEventResponseForWire(result, authorityIdentity,
                "EXECUTION_EVENT_SOURCE_RESPONSE_INVALID");
            return;
        }
        return;
    }

    // Event responses must have an empty top-level reason. Keep this strict
    // even if a source accidentally copied callback prose into it.
    result.reasonCode.clear();

    // Ownership/stream identity is authoritative metadata. A source may
    // return arbitrary opaque owner identifiers, including words
    // that happen to resemble credentials. Bind them to the decoded request
    // instead of applying substring heuristics: only an exact owner match may
    // cross this socket. Request components have already passed the protocol's
    // UTF-8/control/size validation.
    if (request == nullptr || request->operation != ExecutionEventFeedOperation::Wait ||
        sourceStreamEpochUnsafe || sourceStreamEpochMismatch ||
        result.event.streamEpoch != result.streamEpoch ||
        result.event.executionDomain != request->executionDomain ||
        result.event.agentId != request->agentId ||
        result.event.sessionId != request->sessionId ||
        !RequiredEventTextIsBounded(result.event.type) ||
        !RequiredEventTextIsBounded(result.event.venue))
    {
        RejectEventResponseForWire(result,
            authorityIdentity,
            "EXECUTION_EVENT_SOURCE_IDENTITY_INVALID");
        return;
    }

    if (result.droppedThroughSequence > result.latestSequence ||
        result.event.sequence == 0 ||
        result.event.sequence <= result.droppedThroughSequence ||
        result.event.sequence > result.latestSequence ||
        result.event.timestampMs == 0 || result.event.orderId < -1 ||
        !std::isfinite(result.event.filledQuantity) ||
        !std::isfinite(result.event.remainingQuantity) ||
        !std::isfinite(result.event.averageFillPrice))
    {
        RejectEventResponseForWire(result,
            authorityIdentity,
            "EXECUTION_EVENT_SOURCE_RESPONSE_INVALID");
        return;
    }

    if (!result.event.reasonCode.empty() &&
        !StableEventReasonCode(result.event.reasonCode))
        result.event.reasonCode = "EXECUTION_EVENT_CALLBACK_EXCEPTION";
    if (LooksLikeExceptionText(result.event.status, true))
        result.event.status = "Error";
    if (LooksLikeExceptionText(result.event.type, true))
        result.event.type = "event.error";
    if (LooksLikeExceptionText(result.event.side, true))
        result.event.side.clear();
    // Instrument and venue names can legitimately contain slash/colon
    // separators, so only redact them when they carry explicit sensitive or
    // exception markers.
    if (LooksLikeExceptionText(result.event.instrument, false))
        result.event.instrument.clear();
    if (LooksLikeExceptionText(result.event.venue, false))
        result.event.venue = "UNKNOWN";
}
} // namespace

UnixExecutionEventFeedServer::UnixExecutionEventFeedServer(
    ExecutionEventFeedSource& source,
    const ExecutionServiceIdentity& serviceIdentity,
    const std::shared_ptr<ExecutionServiceLifecycleGate>& lifecycleGate)
    : m_source(source), m_serviceIdentity(serviceIdentity),
      m_lifecycleGate(lifecycleGate), m_stop(true), m_listenFd(-1),
      m_enforceGatewayContextBinding(false), m_maxRequestBytes(8192),
      m_ioTimeoutMs(1000), m_maxPendingClients(32)
{
}

UnixExecutionEventFeedServer::~UnixExecutionEventFeedServer()
{
    Stop();
}

bool UnixExecutionEventFeedServer::StartFromFd(
    int listenFd, const std::set<std::uint32_t>& allowedPeerUids,
    std::string& reason, std::size_t maxRequestBytes, int ioTimeoutMs,
    std::size_t workerCount, std::size_t maxPendingClients)
{
    return StartFromFdInternal(listenFd, allowedPeerUids, nullptr, reason,
        maxRequestBytes, ioTimeoutMs, workerCount, maxPendingClients);
}
bool UnixExecutionEventFeedServer::StartFromFd(
    int listenFd, const std::set<std::uint32_t>& allowedPeerUids,
    const ExecutionGatewayContextBinding& gatewayContextBinding,
    std::string& reason, std::size_t maxRequestBytes, int ioTimeoutMs,
    std::size_t workerCount, std::size_t maxPendingClients)
{
    return StartFromFdInternal(listenFd, allowedPeerUids,
        &gatewayContextBinding, reason, maxRequestBytes, ioTimeoutMs,
        workerCount, maxPendingClients);
}
bool UnixExecutionEventFeedServer::StartFromFdInternal(
    int listenFd, const std::set<std::uint32_t>& allowedPeerUids,
    const ExecutionGatewayContextBinding* gatewayContextBinding,
    std::string& reason, std::size_t maxRequestBytes, int ioTimeoutMs,
    std::size_t workerCount,
    std::size_t maxPendingClients)
{
    std::unique_lock<std::mutex> lock(m_mutex);
    bool sourceEpochMatches = false;
    try
    {
        sourceEpochMatches =
            m_source.StreamEpoch() == m_serviceIdentity.serviceEpoch;
    }
    catch (...)
    {
        // A source implementation is in the privileged process.  Do not let
        // an adapter exception escape StartFromFd (or become a process-wide
        // terminate); expose only the stable boundary code to the caller.
        reason = "EXECUTION_EVENT_SOURCE_EXCEPTION";
    }
    if (!m_stop.load() || allowedPeerUids.empty() || maxRequestBytes < 1024 ||
        maxRequestBytes > 32768 || ioTimeoutMs < 1 || workerCount < 1 ||
        workerCount > 32 || maxPendingClients < workerCount || maxPendingClients > 1024 ||
        !ValidIdentity(m_serviceIdentity) || !m_lifecycleGate ||
        !sourceEpochMatches ||
        (gatewayContextBinding != nullptr &&
         !gatewayContextBinding->Complete()) ||
        !ValidateActivatedSocket(listenFd, reason))
    {
        if (listenFd >= 0) ::close(listenFd);
        if (m_lifecycleGate) m_lifecycleGate->ready.store(false);
        if (reason.empty()) reason = "EXECUTION_EVENT_SERVER_INVALID_CONFIG";
        return false;
    }
    m_allowedPeerUids = allowedPeerUids;
    m_gatewayContextBinding = gatewayContextBinding == nullptr ?
        ExecutionGatewayContextBinding() : *gatewayContextBinding;
    m_enforceGatewayContextBinding = gatewayContextBinding != nullptr;
    m_maxRequestBytes = maxRequestBytes;
    m_ioTimeoutMs = ioTimeoutMs;
    m_maxPendingClients = maxPendingClients;
    m_stop.store(false);
    m_listenFd.store(listenFd);
    lock.unlock();
    try
    {
        for (std::size_t i = 0; i < workerCount; ++i)
            m_workers.push_back(std::thread(&UnixExecutionEventFeedServer::WorkerLoop, this));
        m_acceptThread = std::thread(&UnixExecutionEventFeedServer::AcceptLoop, this);
    }
    catch (...)
    {
        m_lifecycleGate->ready.store(false);
        m_stop.store(true);
        const int owned = m_listenFd.exchange(-1);
        if (owned >= 0) ::close(owned);
        m_pendingChanged.notify_all();
        for (std::size_t i = 0; i < m_workers.size(); ++i)
            if (m_workers[i].joinable()) m_workers[i].join();
        m_workers.clear();
        reason = "EXECUTION_EVENT_THREAD_START_FAILED";
        return false;
    }
    reason.clear();
    return true;
}

void UnixExecutionEventFeedServer::Stop()
{
    {
        std::lock_guard<std::mutex> responseLock(m_responseMutex);
        if (m_lifecycleGate) m_lifecycleGate->ready.store(false);
        m_stop.store(true);
    }
    m_pendingChanged.notify_all();
    if (m_acceptThread.joinable()) m_acceptThread.join();
    const int listenFd = m_listenFd.exchange(-1);
    if (listenFd >= 0) ::close(listenFd);
    for (std::size_t i = 0; i < m_workers.size(); ++i)
        if (m_workers[i].joinable()) m_workers[i].join();
    m_workers.clear();
    std::lock_guard<std::mutex> lock(m_mutex);
    while (!m_pendingClients.empty())
    {
        ::close(m_pendingClients.front());
        m_pendingClients.pop_front();
    }
    m_gatewayContextBinding = ExecutionGatewayContextBinding();
    m_enforceGatewayContextBinding = false;
}
bool UnixExecutionEventFeedServer::IsRunning() const
{
    return !m_stop.load() && m_listenFd.load() >= 0 && m_lifecycleGate &&
        (m_lifecycleGate->ready.load() || m_lifecycleGate->terminalControlOnly.load());
}
void UnixExecutionEventFeedServer::AcceptLoop()
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
        std::lock_guard<std::mutex> lock(m_mutex);
        if (m_pendingClients.size() >= m_maxPendingClients) ::close(clientFd);
        else
        {
            m_pendingClients.push_back(clientFd);
            m_pendingChanged.notify_one();
        }
    }
}

void UnixExecutionEventFeedServer::WorkerLoop()
{
    while (true)
    {
        int clientFd = -1;
        {
            std::unique_lock<std::mutex> lock(m_mutex);
            m_pendingChanged.wait(lock, [this]() {
                return m_stop.load() || !m_pendingClients.empty();
            });
            if (m_pendingClients.empty())
            {
                if (m_stop.load()) return;
                continue;
            }
            clientFd = m_pendingClients.front();
            m_pendingClients.pop_front();
        }
        HandleClient(clientFd);
        ::close(clientFd);
    }
}

void UnixExecutionEventFeedServer::HandleClient(int clientFd)
{
    struct ucred credential;
    socklen_t credentialLength = sizeof(credential);
    if (::getsockopt(clientFd, SOL_SOCKET, SO_PEERCRED, &credential, &credentialLength) != 0 ||
        credentialLength != sizeof(credential) ||
        m_allowedPeerUids.find(static_cast<std::uint32_t>(credential.uid)) ==
            m_allowedPeerUids.end())
        return;

    const Deadline readDeadline = std::chrono::steady_clock::now() +
        std::chrono::milliseconds(m_ioTimeoutMs);
    std::string requestBody;
    if (!ReadFrame(clientFd, m_maxRequestBytes, readDeadline, requestBody)) return;
    ExecutionEventFeedRequest request;
    std::string reason;
    ExecutionEventReadResult result;
    if (!ExecutionEventFeedProtocol::DecodeRequest(requestBody, request, reason))
    {
        result = ServiceStatus(m_serviceIdentity,
            ExecutionEventReadStatus::InvalidOwner, reason);
    }
    else if (request.operation == ExecutionEventFeedOperation::GetServiceIdentity)
    {
        if (m_stop.load())
            result = ServiceStatus(m_serviceIdentity,
                ExecutionEventReadStatus::ServiceStopping,
                "EXECUTION_EVENT_SERVICE_STOPPING");
        else if (!m_lifecycleGate || (!m_lifecycleGate->ready.load() && !m_lifecycleGate->terminalControlOnly.load()))
            result = ServiceStatus(m_serviceIdentity,
                ExecutionEventReadStatus::ServiceNotReady,
                "EXECUTION_EVENT_SERVICE_NOT_READY");
        else
        {
            try
            {
                result = ServiceStatus(m_serviceIdentity,
                    ExecutionEventReadStatus::ServiceIdentity,
                    "EXECUTION_EVENT_SERVICE_IDENTITY",
                    m_source.LatestSequence());
            }
            catch (...)
            {
                result = SourceExceptionResult(m_serviceIdentity);
            }
        }
    }
    else if (m_enforceGatewayContextBinding &&
             !m_gatewayContextBinding.MatchesEventOwner(
                 request.executionDomain, request.agentId))
    {
        result = ServiceStatus(m_serviceIdentity,
            ExecutionEventReadStatus::InvalidOwner,
            "EXECUTION_EVENT_GATEWAY_CONTEXT_BINDING_MISMATCH");
    }
    else if (!SameIdentity(request.expectedServiceIdentity, m_serviceIdentity))
    {
        result = ServiceStatus(m_serviceIdentity,
            ExecutionEventReadStatus::ServiceIdentityMismatch,
            "EXECUTION_EVENT_SERVICE_IDENTITY_MISMATCH");
    }
    else if (m_stop.load())
    {
        result = ServiceStatus(m_serviceIdentity,
            ExecutionEventReadStatus::ServiceStopping,
            "EXECUTION_EVENT_SERVICE_STOPPING");
    }
    else if (!m_lifecycleGate || !m_lifecycleGate->ready.load())
    {
        result = ServiceStatus(m_serviceIdentity,
            ExecutionEventReadStatus::ServiceNotReady,
            "EXECUTION_EVENT_SERVICE_NOT_READY");
    }
    else
    {
        const Deadline waitDeadline = std::chrono::steady_clock::now() +
            std::chrono::milliseconds(request.timeoutMs);
        do
        {
            if (m_stop.load() || !m_lifecycleGate->ready.load()) break;
            const int slice = request.timeoutMs == 0 ? 0 :
                std::min(100, RemainingMs(waitDeadline));
            try
            {
                result = m_source.ReadNext(request.executionDomain,
                    request.agentId, request.sessionId,
                    request.expectedServiceIdentity.serviceEpoch,
                    request.afterSequence, slice);
            }
            catch (...)
            {
                // Never put std::exception::what() (which commonly contains
                // paths, credentials, or SDK diagnostics) in a response.
                // SourceExceptionResult is already protocol-valid and keeps
                // the worker alive for subsequent requests.
                result = SourceExceptionResult(m_serviceIdentity);
                break;
            }
            if (m_stop.load() || !m_lifecycleGate->ready.load()) break;
            if (result.status != ExecutionEventReadStatus::Timeout ||
                request.timeoutMs == 0 || RemainingMs(waitDeadline) == 0) break;
        }
        while (true);
        if (m_stop.load())
            result = ServiceStatus(m_serviceIdentity,
                ExecutionEventReadStatus::ServiceStopping,
                "EXECUTION_EVENT_SERVICE_STOPPING");
        else if (!m_lifecycleGate->ready.load())
            result = ServiceStatus(m_serviceIdentity,
                ExecutionEventReadStatus::ServiceNotReady,
                "EXECUTION_EVENT_SERVICE_NOT_READY");
        else
        {
            result.serviceIdentity = m_serviceIdentity;
            if (result.streamEpoch != m_serviceIdentity.serviceEpoch ||
                result.status == ExecutionEventReadStatus::EpochChanged)
                result = ServiceStatus(m_serviceIdentity,
                    ExecutionEventReadStatus::InvalidOwner,
                    "EXECUTION_EVENT_SOURCE_IDENTITY_INVALID");
        }
    }
    std::lock_guard<std::mutex> responseLock(m_responseMutex);
    if (m_stop.load())
        result = ServiceStatus(m_serviceIdentity,
            ExecutionEventReadStatus::ServiceStopping,
            "EXECUTION_EVENT_SERVICE_STOPPING");
    else if (!m_lifecycleGate || (!m_lifecycleGate->ready.load() &&
             !(m_lifecycleGate->terminalControlOnly.load() && result.status == ExecutionEventReadStatus::ServiceIdentity)))
        result = ServiceStatus(m_serviceIdentity,
            ExecutionEventReadStatus::ServiceNotReady,
            "EXECUTION_EVENT_SERVICE_NOT_READY");
    const ExecutionEventFeedRequest* ownerRequest =
        request.operation == ExecutionEventFeedOperation::Wait ?
            &request : nullptr;
    SanitizeEventResponseForWire(result, m_serviceIdentity, ownerRequest);
    std::string responseBody;
    if (!ExecutionEventFeedProtocol::EncodeResponse(result, responseBody, reason)) return;
    const Deadline writeDeadline = std::chrono::steady_clock::now() +
        std::chrono::milliseconds(m_ioTimeoutMs);
    WriteFrame(clientFd, responseBody, writeDeadline);
}
