#include "execution_event_feed_server.h"

#include "execution_event_feed_transport.h"

#include <algorithm>
#include <cerrno>
#include <chrono>
#include <cstring>
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
                                       const std::string& reason)
{
    ExecutionEventReadResult result;
    result.status = status;
    result.serviceIdentity = identity;
    result.streamEpoch = identity.serviceEpoch;
    result.reasonCode = reason;
    return result;
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
    if (!m_stop.load() || allowedPeerUids.empty() || maxRequestBytes < 1024 ||
        maxRequestBytes > 32768 || ioTimeoutMs < 1 || workerCount < 1 ||
        workerCount > 32 || maxPendingClients < workerCount || maxPendingClients > 1024 ||
        !ValidIdentity(m_serviceIdentity) || !m_lifecycleGate ||
        m_source.StreamEpoch() != m_serviceIdentity.serviceEpoch ||
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
            result = ServiceStatus(m_serviceIdentity,
                ExecutionEventReadStatus::ServiceIdentity,
                "EXECUTION_EVENT_SERVICE_IDENTITY");
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
            result = m_source.ReadNext(request.executionDomain, request.agentId,
                request.sessionId, request.expectedServiceIdentity.serviceEpoch,
                request.afterSequence, slice);
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
    std::string responseBody;
    if (!ExecutionEventFeedProtocol::EncodeResponse(result, responseBody, reason)) return;
    const Deadline writeDeadline = std::chrono::steady_clock::now() +
        std::chrono::milliseconds(m_ioTimeoutMs);
    WriteFrame(clientFd, responseBody, writeDeadline);
}
