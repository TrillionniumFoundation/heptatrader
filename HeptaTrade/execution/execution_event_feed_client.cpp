#include "execution_event_feed_client.h"

#include "execution_event_feed_transport.h"

#include <cerrno>
#include <chrono>
#include <cstring>
#include <poll.h>
#include <sys/socket.h>
#include <sys/un.h>
#include <unistd.h>

using namespace HeptaExecutionEventFeedTransport;

namespace
{
bool AllowedServerPeerCredential(
    std::uint32_t peerUid,
    int peerPid,
    const std::set<std::uint32_t>& allowedServerUids)
{
    return allowedServerUids.find(peerUid) != allowedServerUids.end() ||
        (peerUid == 0 && peerPid == 1);
}

ExecutionEventReadResult TransportFailure(const std::string& reason)
{
    ExecutionEventReadResult result;
    result.status = ExecutionEventReadStatus::Timeout;
    result.reasonCode = reason;
    return result;
}
} // namespace

UnixExecutionEventFeedClient::UnixExecutionEventFeedClient(
    const std::string& socketPath,
    int ioTimeoutMs,
    std::size_t maxResponseBytes,
    const std::set<std::uint32_t>& allowedServerUids)
    : m_socketPath(socketPath), m_ioTimeoutMs(ioTimeoutMs),
      m_maxResponseBytes(maxResponseBytes), m_allowedServerUids(allowedServerUids)
{
    if (m_allowedServerUids.empty())
        m_allowedServerUids.insert(static_cast<std::uint32_t>(::geteuid()));
}

ExecutionEventReadResult UnixExecutionEventFeedClient::GetServiceIdentity() const
{
    ExecutionEventFeedRequest request;
    request.operation = ExecutionEventFeedOperation::GetServiceIdentity;
    const ExecutionEventReadResult result = Call(request);
    if (ValidIdentity(result.serviceIdentity) &&
        result.status != ExecutionEventReadStatus::ServiceIdentity &&
        result.status != ExecutionEventReadStatus::ServiceNotReady &&
        result.status != ExecutionEventReadStatus::ServiceStopping)
        return TransportFailure("EXECUTION_EVENT_IDENTITY_RESPONSE_INVALID");
    return result;
}

ExecutionEventReadResult UnixExecutionEventFeedClient::Wait(
    const ExecutionEventFeedRequest& request) const
{
    if (request.operation != ExecutionEventFeedOperation::Wait)
        return TransportFailure("EXECUTION_EVENT_WAIT_OPERATION_REQUIRED");
    const ExecutionEventReadResult result = Call(request);
    if (!ValidIdentity(result.serviceIdentity)) return result;
    if (result.status == ExecutionEventReadStatus::ServiceIdentityMismatch)
    {
        if (SameIdentity(result.serviceIdentity, request.expectedServiceIdentity))
            return TransportFailure("EXECUTION_EVENT_FALSE_IDENTITY_MISMATCH");
        return result;
    }
    if (!SameIdentity(result.serviceIdentity, request.expectedServiceIdentity))
        return TransportFailure("EXECUTION_EVENT_SERVICE_IDENTITY_CHANGED");
    if (result.status == ExecutionEventReadStatus::ServiceIdentity)
        return TransportFailure("EXECUTION_EVENT_UNEXPECTED_IDENTITY_RESPONSE");
    if (result.status == ExecutionEventReadStatus::Event &&
        (result.event.executionDomain != request.executionDomain ||
         result.event.agentId != request.agentId ||
         result.event.sessionId != request.sessionId ||
         result.event.sequence <= request.afterSequence))
        return TransportFailure("EXECUTION_EVENT_OWNER_OR_SEQUENCE_MISMATCH");
    if (result.status == ExecutionEventReadStatus::Gap &&
        request.afterSequence >= result.droppedThroughSequence)
        return TransportFailure("EXECUTION_EVENT_GAP_CURSOR_INVALID");
    return result;
}

ExecutionEventReadResult UnixExecutionEventFeedClient::Call(
    const ExecutionEventFeedRequest& request) const
{
    std::string requestBody;
    std::string reason;
    if (!ExecutionEventFeedProtocol::EncodeRequest(request, requestBody, reason))
        return TransportFailure(reason);
    const int waitTimeout = request.operation == ExecutionEventFeedOperation::Wait ?
        request.timeoutMs : 0;
    const Deadline deadline = std::chrono::steady_clock::now() +
        std::chrono::milliseconds(m_ioTimeoutMs + waitTimeout);
    const int fd = ::socket(AF_UNIX, SOCK_STREAM | SOCK_CLOEXEC | SOCK_NONBLOCK, 0);
    if (fd < 0) return TransportFailure("EXECUTION_EVENT_SOCKET_CREATE_FAILED");
    struct sockaddr_un address;
    std::memset(&address, 0, sizeof(address));
    if (m_socketPath.empty() || m_socketPath.size() >= sizeof(address.sun_path))
    {
        ::close(fd);
        return TransportFailure("EXECUTION_EVENT_SOCKET_PATH_INVALID");
    }
    address.sun_family = AF_UNIX;
    std::memcpy(address.sun_path, m_socketPath.c_str(), m_socketPath.size() + 1);
    int rc = ::connect(fd, reinterpret_cast<struct sockaddr*>(&address), sizeof(address));
    if (rc != 0 && errno == EINPROGRESS)
    {
        if (!WaitFd(fd, POLLOUT, deadline))
        {
            ::close(fd);
            return TransportFailure("EXECUTION_EVENT_CONNECT_TIMEOUT");
        }
        int socketError = 0;
        socklen_t socketErrorLength = sizeof(socketError);
        if (::getsockopt(fd, SOL_SOCKET, SO_ERROR, &socketError, &socketErrorLength) != 0 ||
            socketError != 0)
        {
            ::close(fd);
            return TransportFailure("EXECUTION_EVENT_CONNECT_FAILED");
        }
    }
    else if (rc != 0)
    {
        ::close(fd);
        return TransportFailure("EXECUTION_EVENT_CONNECT_FAILED");
    }
    struct ucred credential;
    socklen_t credentialLength = sizeof(credential);
    if (::getsockopt(fd, SOL_SOCKET, SO_PEERCRED, &credential, &credentialLength) != 0 ||
        credentialLength != sizeof(credential) ||
        !AllowedServerPeerCredential(
            static_cast<std::uint32_t>(credential.uid),
            credential.pid, m_allowedServerUids))
    {
        ::close(fd);
        return TransportFailure("EXECUTION_EVENT_PEER_UID_REJECTED");
    }
    if (!WriteFrame(fd, requestBody, deadline))
    {
        ::close(fd);
        return TransportFailure("EXECUTION_EVENT_REQUEST_WRITE_FAILED");
    }
    std::string responseBody;
    if (!ReadFrame(fd, m_maxResponseBytes, deadline, responseBody))
    {
        ::close(fd);
        return TransportFailure("EXECUTION_EVENT_RESPONSE_READ_FAILED");
    }
    ::close(fd);
    ExecutionEventReadResult result;
    if (!ExecutionEventFeedProtocol::DecodeResponse(responseBody, result, reason))
        return TransportFailure(reason);
    return result;
}
