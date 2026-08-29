#include "unix_tool_server.h"

#include "typed_tool_protocol.h"

#include <cerrno>
#include <chrono>
#include <algorithm>
#include <cstring>
#include <exception>
#include <poll.h>
#include <sys/socket.h>
#include <sys/stat.h>
#include <sys/un.h>
#include <unistd.h>

UnixToolServer::UnixToolServer(TradingToolHost& host)
    : m_host(host), m_stop(true), m_listenFd(-1), m_unlinkOnStop(false),
      m_maxRequestBytes(65536), m_ioTimeoutMs(3000),
      m_maxPendingConnections(32), m_maxConcurrentPerOwner(1), m_maxPendingPerOwner(8),
      m_maxQueueWaitMs(5000), m_pendingCount(0), m_queueBackpressureRejections(0),
      m_ownerBackpressureRejections(0), m_deadlineRejections(0), m_cancelledRequests(0),
      m_activeRequests(0)
{
}

UnixToolServer::~UnixToolServer()
{
    Stop();
}

bool UnixToolServer::Start(const std::string& socketPath, std::string& reason,
                           std::size_t maxRequestBytes, int ioTimeoutMs,
                           std::size_t workerCount, std::size_t maxPendingConnections,
                           std::size_t maxConcurrentPerOwner,
                           std::size_t maxPendingPerOwner,
                           std::size_t ingressWorkerCount,
                           std::uint64_t maxQueueWaitMs)
{
    if (!m_stop.load()) { reason = "server already running"; return false; }
    if (socketPath.empty() || socketPath.size() >= sizeof(sockaddr_un::sun_path))
    { reason = "invalid socket path"; return false; }
    struct stat existing;
    if (::lstat(socketPath.c_str(), &existing) == 0)
    {
        if (!S_ISSOCK(existing.st_mode)) { reason = "socket path exists and is not a socket"; return false; }
        reason = "socket path already exists; use activated fd or owner cleanup";
        return false;
    }
    const int fd = ::socket(AF_UNIX, SOCK_STREAM | SOCK_CLOEXEC, 0);
    if (fd < 0) { reason = std::strerror(errno); return false; }
    sockaddr_un address;
    std::memset(&address, 0, sizeof(address));
    address.sun_family = AF_UNIX;
    std::strncpy(address.sun_path, socketPath.c_str(), sizeof(address.sun_path) - 1);
    if (::bind(fd, reinterpret_cast<sockaddr*>(&address), sizeof(address)) != 0)
    { reason = std::strerror(errno); ::close(fd); return false; }
    if (!m_socketPathIdentity.Prepare(fd, socketPath, 32, reason)) return false;
    const UnixSocketPathIdentity pathIdentity = m_socketPathIdentity;
    if (!Activate(fd, socketPath, true, reason, maxRequestBytes, ioTimeoutMs,
            workerCount, maxPendingConnections, maxConcurrentPerOwner,
            maxPendingPerOwner, ingressWorkerCount, maxQueueWaitMs))
    {
        const int failedFd = m_listenFd.exchange(-1);
        if (failedFd >= 0) ::close(failedFd);
        pathIdentity.UnlinkIfUnchanged(socketPath);
        return false;
    }
    return true;
}

bool UnixToolServer::StartFromFd(int listenFd, std::string& reason,
    std::size_t maxRequestBytes, int ioTimeoutMs, std::size_t workerCount,
    std::size_t maxPendingConnections, std::size_t maxConcurrentPerOwner,
    std::size_t maxPendingPerOwner, std::size_t ingressWorkerCount,
    std::uint64_t maxQueueWaitMs)
{
    if (!m_stop.load()) { reason = "server already running"; return false; }
    if (listenFd < 0) { reason = "invalid activated socket fd"; return false; }
    int acceptConnections = 0;
    socklen_t optionLength = sizeof(acceptConnections);
    if (::getsockopt(listenFd, SOL_SOCKET, SO_ACCEPTCONN,
            &acceptConnections, &optionLength) != 0 || acceptConnections != 1)
    { reason = "activated fd is not a listening socket"; return false; }
    return Activate(listenFd, std::string(), false, reason, maxRequestBytes,
        ioTimeoutMs, workerCount, maxPendingConnections, maxConcurrentPerOwner,
        maxPendingPerOwner, ingressWorkerCount, maxQueueWaitMs);
}

bool UnixToolServer::Activate(int fd, const std::string& socketPath, bool unlinkOnStop,
    std::string& reason, std::size_t maxRequestBytes, int ioTimeoutMs,
    std::size_t workerCount, std::size_t maxPendingConnections,
    std::size_t maxConcurrentPerOwner, std::size_t maxPendingPerOwner,
    std::size_t ingressWorkerCount, std::uint64_t maxQueueWaitMs)
{
    m_listenFd.store(fd); m_socketPath = socketPath; m_unlinkOnStop = unlinkOnStop;
    if (!unlinkOnStop) m_socketPathIdentity.Reset();
    if (maxRequestBytes < 128 || maxRequestBytes > 1024 * 1024 || ioTimeoutMs <= 0 ||
        ioTimeoutMs > 30000 || workerCount == 0 || workerCount > 64 ||
        maxPendingConnections == 0 || maxPendingConnections > 1024 ||
        maxConcurrentPerOwner == 0 || maxConcurrentPerOwner > workerCount ||
        maxPendingPerOwner == 0 || maxPendingPerOwner > maxPendingConnections ||
        ingressWorkerCount == 0 || ingressWorkerCount > 16 ||
        maxQueueWaitMs == 0 || maxQueueWaitMs > 60000 || !m_decisionAudit.Ready())
    {
        reason = "invalid server limits"; ::close(m_listenFd.exchange(-1)); return false;
    }
    m_maxRequestBytes = maxRequestBytes; m_ioTimeoutMs = ioTimeoutMs;
    m_maxPendingConnections = maxPendingConnections; m_maxConcurrentPerOwner = maxConcurrentPerOwner;
    m_maxPendingPerOwner = maxPendingPerOwner;
    m_maxQueueWaitMs = maxQueueWaitMs; m_pendingCount = 0;
    m_queueBackpressureRejections.store(0);
    m_ownerBackpressureRejections.store(0);
    m_activeRequests.store(0);
    m_drainedListenerIdentityValid = false;
    m_stop.store(false);
    try
    {
        for (std::size_t i = 0; i < ingressWorkerCount; ++i)
            m_ingressWorkers.push_back(std::thread(&UnixToolServer::IngressLoop, this));
        for (std::size_t i = 0; i < workerCount; ++i)
            m_executionWorkers.push_back(std::thread(&UnixToolServer::ExecutionLoop, this));
        m_acceptThread = std::thread(&UnixToolServer::AcceptLoop, this);
    }
    catch (const std::exception& ex)
    {
        reason = ex.what();
        Stop();
        return false;
    }
    reason.clear();
    return true;
}

void UnixToolServer::Stop()
{
    if (m_stop.exchange(true)) return;
    if (m_acceptThread.joinable()) m_acceptThread.join();
    const int listenFd = m_listenFd.exchange(-1);
    const bool listenerIdentityValid = m_socketPathIdentity.ListenerWitness(listenFd) ||
        m_drainedListenerIdentityValid;
    if (listenFd >= 0)
    {
        ::close(listenFd);
    }
    std::vector<PendingRequest> stoppedRequests;
    {
        std::lock_guard<std::mutex> lock(m_queueMutex);
        for (std::deque<int>::const_iterator it = m_pendingClients.begin(); it != m_pendingClients.end(); ++it)
        {
            ::shutdown(*it, SHUT_RDWR);
            ::close(*it);
            --m_pendingCount;
        }
        for (std::map<std::string, std::deque<PendingRequest> >::const_iterator owner =
                 m_ownerQueues.begin(); owner != m_ownerQueues.end(); ++owner)
        {
            for (std::deque<PendingRequest>::const_iterator request = owner->second.begin();
                 request != owner->second.end(); ++request)
            {
                stoppedRequests.push_back(*request);
                --m_pendingCount;
            }
        }
        m_pendingClients.clear();
        m_ownerQueues.clear();
        m_readyOwners.clear();
    }
    m_queueReady.notify_all();
    for (std::vector<PendingRequest>::iterator request =
             stoppedRequests.begin(); request != stoppedRequests.end(); ++request)
    {
        TradingToolResult result;
        result.status = request->mutation ?
            TradingToolCallStatus::Uncertain : TradingToolCallStatus::Rejected;
        result.toolName = request->request.call.name;
        result.reasonCode = request->mutation ?
            "SERVER_STOPPED_AFTER_DURABLE_INTENT" :
            "SERVER_STOPPED_BEFORE_DISPATCH";
        result.detail = request->mutation ?
            "request was not dispatched; reconcile the durable intent before retrying" :
            "request was stopped before dispatch";
        m_decisionAudit.AppendOutcome(true, request->peerUid,
            &request->request, &request->binding, request->mutation, result);
        ReplyAndClose(request->clientFd, result);
    }
    for (std::size_t i = 0; i < m_ingressWorkers.size(); ++i)
        if (m_ingressWorkers[i].joinable()) m_ingressWorkers[i].join();
    for (std::size_t i = 0; i < m_executionWorkers.size(); ++i)
        if (m_executionWorkers[i].joinable()) m_executionWorkers[i].join();
    m_ingressWorkers.clear();
    m_executionWorkers.clear();
    m_activeByOwner.clear();
    {
        std::lock_guard<std::mutex> lock(m_queueMutex);
        m_pendingCount = 0;
    }
    if (m_unlinkOnStop && !m_socketPath.empty() && listenerIdentityValid)
        m_socketPathIdentity.UnlinkIfUnchanged(m_socketPath);
    m_socketPath.clear();
    m_unlinkOnStop = false;
    m_socketPathIdentity.Reset();
    m_drainedListenerIdentityValid = false;
}

bool UnixToolServer::Drain(std::uint64_t timeoutMs)
{
    if (m_stop.load()) return true;
    if (timeoutMs == 0 || timeoutMs > 60000) return false;
    const int listenFd = m_listenFd.exchange(-1);
    m_drainedListenerIdentityValid = m_socketPathIdentity.ListenerWitness(listenFd);
    if (m_acceptThread.joinable()) m_acceptThread.join();
    if (listenFd >= 0) ::close(listenFd);
    bool drained = false;
    {
        std::unique_lock<std::mutex> lock(m_queueMutex);
        drained = m_queueReady.wait_for(lock, std::chrono::milliseconds(timeoutMs), [this]() {
            return m_pendingCount == 0 && m_activeRequests.load() == 0;
        });
    }
    Stop();
    return drained;
}

bool UnixToolServer::IsRunning() const
{
    return !m_stop.load();
}

UnixToolServerHealth UnixToolServer::GetHealth() const
{
    UnixToolServerHealth health;
    {
        std::lock_guard<std::mutex> lock(m_queueMutex);
        health.pendingConnections = m_pendingCount;
        health.readyOwners = m_readyOwners.size();
    }
    health.activeRequests = m_activeRequests.load();
    health.queueBackpressureRejections = m_queueBackpressureRejections.load();
    health.ownerBackpressureRejections = m_ownerBackpressureRejections.load();
    health.deadlineRejections = m_deadlineRejections.load();
    health.cancelledRequests = m_cancelledRequests.load();
    return health;
}

void UnixToolServer::SetBackpressureObserver(const BackpressureObserver& observer)
{
    std::lock_guard<std::mutex> lock(m_queueMutex);
    m_backpressureObserver = observer;
}

void UnixToolServer::SetDecisionAuditJournal(SessionSupervisorAuditJournal* journal)
{
    m_decisionAudit.SetJournal(journal);
}

void UnixToolServer::AllowMissingDecisionAuditForTests()
{
    m_decisionAudit.AllowMissingForTests();
}
bool UnixToolServer::IsAccepting() const
{
    return !m_stop.load() && m_listenFd.load() >= 0;
}

void UnixToolServer::AcceptLoop()
{
    const int listenFd = m_listenFd.load();
    while (IsAccepting())
    {
        pollfd ready = {listenFd, POLLIN, 0};
        const int pollResult = ::poll(&ready, 1, 100);
        if (pollResult < 0) { if (errno == EINTR) continue; break; }
        if (pollResult == 0) continue;
        if ((ready.revents & (POLLERR | POLLHUP | POLLNVAL)) != 0) break;
        if ((ready.revents & POLLIN) == 0) continue;
        const int clientFd = ::accept4(listenFd, nullptr, nullptr, SOCK_CLOEXEC);
        if (clientFd < 0)
        {
            if (errno == EINTR) continue;
            if (m_stop.load() || errno == EBADF || errno == EINVAL) break;
            continue;
        }
        bool accepted = false;
        {
            std::lock_guard<std::mutex> lock(m_queueMutex);
            if (m_pendingCount < m_maxPendingConnections)
            {
                m_pendingClients.push_back(clientFd);
                ++m_pendingCount;
                accepted = true;
            }
        }
        if (accepted) m_queueReady.notify_all();
        else
        {
            ++m_queueBackpressureRejections;
            struct ucred credentials;
            socklen_t credentialsLength = sizeof(credentials);
            const bool havePeer =
                ::getsockopt(clientFd, SOL_SOCKET, SO_PEERCRED,
                    &credentials, &credentialsLength) == 0 &&
                credentialsLength == sizeof(credentials);
            TradingToolResult result;
            result.status = TradingToolCallStatus::Rejected;
            result.reasonCode = "GLOBAL_QUEUE_BACKPRESSURE";
            m_decisionAudit.AppendOutcome(
                havePeer,
                havePeer ? static_cast<std::uint32_t>(credentials.uid) : 0,
                nullptr, nullptr, false, result);
            ::shutdown(clientFd, SHUT_RDWR);
            ::close(clientFd);
        }
    }
}

void UnixToolServer::IngressLoop()
{
    while (true)
    {
        int clientFd = -1;
        {
            std::unique_lock<std::mutex> lock(m_queueMutex);
            m_queueReady.wait(lock, [this]() { return m_stop.load() || !m_pendingClients.empty(); });
            if (m_pendingClients.empty())
            {
                if (m_stop.load()) return;
                continue;
            }
            clientFd = m_pendingClients.front();
            m_pendingClients.pop_front();
        }
        DecodeAndQueue(clientFd);
    }
}

void UnixToolServer::ExecutionLoop()
{
    while (true)
    {
        PendingRequest pending;
        bool found = false;
        {
            std::unique_lock<std::mutex> lock(m_queueMutex);
            m_queueReady.wait(lock, [this]() {
                return m_stop.load() || !m_readyOwners.empty();
            });
            if (m_stop.load() && m_readyOwners.empty()) return;
            const std::size_t owners = m_readyOwners.size();
            for (std::size_t i = 0; i < owners; ++i)
            {
                const std::string owner = m_readyOwners.front();
                m_readyOwners.pop_front();
                std::deque<PendingRequest>& queue = m_ownerQueues[owner];
                if (m_activeByOwner[owner] >= m_maxConcurrentPerOwner)
                {
                    m_readyOwners.push_back(owner);
                    continue;
                }
                pending = queue.front();
                queue.pop_front();
                ++m_activeByOwner[owner];
                --m_pendingCount;
                if (!queue.empty()) m_readyOwners.push_back(owner);
                else m_ownerQueues.erase(owner);
                found = true;
                break;
            }
            if (!found)
            {
                m_queueReady.wait_for(lock, std::chrono::milliseconds(10));
                continue;
            }
        }
        Execute(pending);
    }
}

bool UnixToolServer::DecodeIngress(
    int clientFd,
    std::uint32_t& peerUid,
    TradingToolHostRequest& request,
    TradingToolResult& rejection,
    bool& peerCredentialAvailable,
    bool& decodedRequest)
{
    struct ucred credentials;
    socklen_t credentialsLength = sizeof(credentials);
    std::string body;
    std::string reason;
    peerCredentialAvailable = false;
    decodedRequest = false;
    if (::getsockopt(clientFd, SOL_SOCKET, SO_PEERCRED, &credentials, &credentialsLength) != 0)
    {
        rejection.status = TradingToolCallStatus::Rejected;
        rejection.reasonCode = "PEER_CREDENTIAL_UNAVAILABLE";
    }
    else
    {
        peerCredentialAvailable = true;
        peerUid = static_cast<std::uint32_t>(credentials.uid);
    }
    if (rejection.reasonCode.empty() &&
        !TypedToolProtocol::ReadFrame(clientFd, m_maxRequestBytes, m_ioTimeoutMs, body, reason))
    {
        rejection.status = TradingToolCallStatus::Rejected;
        rejection.reasonCode = "INVALID_FRAME";
        rejection.detail = reason;
    }
    else if (rejection.reasonCode.empty() &&
        !TypedToolProtocol::DecodeRequest(body, request, reason))
    {
        rejection.status = TradingToolCallStatus::Rejected;
        rejection.reasonCode = "INVALID_TYPED_REQUEST";
        rejection.detail = reason;
    }
    else if (rejection.reasonCode.empty())
    {
        decodedRequest = true;
    }
    return rejection.reasonCode.empty();
}

void UnixToolServer::DecodeAndQueue(int clientFd)
{
    TradingToolResult rejection;
    TradingToolHostRequest request;
    std::string reason;
    std::uint32_t peerUid = 0;
    bool peerCredentialAvailable = false;
    bool decodedRequest = false;
    DecodeIngress(clientFd, peerUid, request, rejection,
                  peerCredentialAvailable, decodedRequest);
    if (m_stop.load())
    {
        {
            std::lock_guard<std::mutex> lock(m_queueMutex);
            --m_pendingCount;
        }
        rejection.status = TradingToolCallStatus::Rejected;
        rejection.toolName = decodedRequest ? request.call.name : std::string();
        rejection.reasonCode = "SERVER_STOPPED_DURING_INGRESS";
        rejection.detail = "server stopped before a durable mutation intent";
        m_decisionAudit.AppendOutcome(peerCredentialAvailable,
            peerUid,
            decodedRequest ? &request : nullptr, nullptr, false, rejection);
        ReplyAndClose(clientFd, rejection);
        m_queueReady.notify_all();
        return;
    }
    if (!rejection.reasonCode.empty())
    {
        m_decisionAudit.AppendOutcome(peerCredentialAvailable,
            peerUid,
            decodedRequest ? &request : nullptr, nullptr, false, rejection);
        {
            std::lock_guard<std::mutex> lock(m_queueMutex);
            --m_pendingCount;
        }
        ReplyAndClose(clientFd, rejection);
        return;
    }

    PendingRequest pending;
    pending.clientFd = clientFd;
    pending.peerUid = peerUid;
    pending.request = request;
    pending.mutation = m_host.IsMutationTool(request.call.name);
    const bool hasBinding = m_host.GetSession(request.sessionToken, pending.binding);
    const bool peerMatches = hasBinding && pending.binding.peerUid == pending.peerUid;
    pending.owner = peerMatches ?
        pending.binding.session.executionContext.agentId + "\n" +
            pending.binding.session.executionContext.sessionId :
        "peer\n" + std::to_string(pending.peerUid);
    if (!m_decisionAudit.AppendIntent(true, pending.peerUid, request,
            hasBinding ? &pending.binding : nullptr, pending.mutation, reason))
    {
        {
            std::lock_guard<std::mutex> lock(m_queueMutex);
            --m_pendingCount;
        }
        rejection.status = TradingToolCallStatus::Rejected;
        rejection.toolName = request.call.name;
        rejection.reasonCode = "DECISION_AUDIT_WRITE_FAILED";
        rejection.detail = "Tool decision audit is unavailable";
        ReplyAndClose(clientFd, rejection);
        return;
    }

    if (request.call.name == "system.cancel_request")
    {
        TradingToolHostSessionBinding authorizedBinding;
        TradingToolResult authorization = m_host.AuthorizeControlRequest(
            pending.peerUid, request, authorizedBinding);
        if (authorization.status != TradingToolCallStatus::Ok)
        {
            {
                std::lock_guard<std::mutex> lock(m_queueMutex);
                --m_pendingCount;
            }
            m_decisionAudit.AppendOutcome(true, pending.peerUid, &request,
                hasBinding ? &pending.binding : nullptr,
                pending.mutation, authorization);
            ReplyAndClose(clientFd, authorization);
            return;
        }
        pending.binding = authorizedBinding;
        pending.owner =
            authorizedBinding.session.executionContext.agentId + "\n" +
            authorizedBinding.session.executionContext.sessionId;
    }

    const std::uint64_t nowMs = static_cast<std::uint64_t>(
        std::chrono::duration_cast<std::chrono::milliseconds>(
            std::chrono::system_clock::now().time_since_epoch()).count());
    const std::uint64_t serverDeadline = nowMs + m_maxQueueWaitMs;
    pending.deadlineAtMs = request.queueDeadlineAtMs == 0 ? serverDeadline :
        std::min(serverDeadline, request.queueDeadlineAtMs);
    if (pending.deadlineAtMs <= nowMs)
    {
        {
            std::lock_guard<std::mutex> lock(m_queueMutex);
            --m_pendingCount;
        }
        ++m_deadlineRejections;
        TradingToolResult result;
        result.status = TradingToolCallStatus::Rejected;
        result.toolName = request.call.name;
        result.reasonCode = "QUEUE_DEADLINE_EXCEEDED";
        m_decisionAudit.AppendOutcome(true, pending.peerUid, &request,
            hasBinding ? &pending.binding : nullptr, pending.mutation, result);
        ReplyAndClose(clientFd, result);
        return;
    }

    if (request.call.name == "system.cancel_request")
    {
        PendingRequest cancelled;
        bool found = false;
        {
            std::lock_guard<std::mutex> lock(m_queueMutex);
            std::map<std::string, std::deque<PendingRequest> >::iterator owner =
                m_ownerQueues.find(pending.owner);
            if (owner != m_ownerQueues.end())
            {
                for (std::deque<PendingRequest>::iterator it = owner->second.begin();
                     it != owner->second.end(); ++it)
                {
                    if (it->request.toolCallId != request.cancelToolCallId) continue;
                    cancelled = *it;
                    owner->second.erase(it);
                    --m_pendingCount;
                    found = true;
                    if (owner->second.empty())
                    {
                        m_ownerQueues.erase(owner);
                        m_readyOwners.erase(std::remove(m_readyOwners.begin(), m_readyOwners.end(),
                            pending.owner), m_readyOwners.end());
                    }
                    break;
                }
            }
            --m_pendingCount;
        }
        TradingToolResult cancelResult;
        cancelResult.toolName = request.call.name;
        cancelResult.status = found ? TradingToolCallStatus::Ok : TradingToolCallStatus::Rejected;
        cancelResult.reasonCode = found ? "REQUEST_CANCELLED" : "REQUEST_NOT_PENDING";
        if (found)
        {
            ++m_cancelledRequests;
            TradingToolResult targetResult;
            targetResult.status = TradingToolCallStatus::Rejected;
            targetResult.toolName = cancelled.request.call.name;
            targetResult.reasonCode = "REQUEST_CANCELLED";
            m_decisionAudit.AppendOutcome(true, cancelled.peerUid,
                &cancelled.request, &cancelled.binding,
                cancelled.mutation, targetResult);
            ReplyAndClose(cancelled.clientFd, targetResult);
        }
        m_decisionAudit.AppendOutcome(true, pending.peerUid, &request,
            hasBinding ? &pending.binding : nullptr,
            pending.mutation, cancelResult);
        ReplyAndClose(clientFd, cancelResult);
        return;
    }

    QueueRequest(pending, hasBinding, peerMatches);
}

void UnixToolServer::QueueRequest(
    PendingRequest pending,
    bool hasBinding,
    bool peerMatches)
{
    const TradingToolHostRequest& request = pending.request;
    const int clientFd = pending.clientFd;
    BackpressureObserver observer;
    bool accepted = false;
    bool stopped = false;
    {
        std::lock_guard<std::mutex> lock(m_queueMutex);
        if (m_stop.load())
        {
            --m_pendingCount;
            stopped = true;
        }
        else
        {
            std::deque<PendingRequest>& queue = m_ownerQueues[pending.owner];
            if (queue.size() < m_maxPendingPerOwner)
            {
                const bool wasEmpty = queue.empty();
                queue.push_back(pending);
                if (wasEmpty) m_readyOwners.push_back(pending.owner);
                accepted = true;
            }
            else
            {
                --m_pendingCount;
                observer = m_backpressureObserver;
            }
        }
    }
    if (accepted) m_queueReady.notify_all();
    else if (stopped)
    {
        TradingToolResult result;
        result.status = pending.mutation ?
            TradingToolCallStatus::Uncertain : TradingToolCallStatus::Rejected;
        result.reasonCode = pending.mutation ?
            "SERVER_STOPPED_AFTER_DURABLE_INTENT" :
            "SERVER_STOPPED_BEFORE_DISPATCH";
        result.detail = pending.mutation ?
            "request was not dispatched; reconcile the durable intent before retrying" :
            "request was stopped before dispatch";
        m_decisionAudit.AppendOutcome(true, pending.peerUid, &request,
            hasBinding ? &pending.binding : nullptr, pending.mutation, result);
        ReplyAndClose(clientFd, result);
        m_queueReady.notify_all();
    }
    else
    {
        ++m_ownerBackpressureRejections;
        TradingToolResult result;
        result.status = TradingToolCallStatus::Rejected;
        result.reasonCode = "OWNER_QUEUE_BACKPRESSURE";
        result.detail = "owner pending request limit reached";
        if (observer && peerMatches) observer(pending.binding, result.reasonCode);
        m_decisionAudit.AppendOutcome(true, pending.peerUid, &request,
            hasBinding ? &pending.binding : nullptr, pending.mutation, result);
        ReplyAndClose(clientFd, result);
    }
}

void UnixToolServer::Execute(PendingRequest pending)
{
    ++m_activeRequests;
    const std::uint64_t nowMs = static_cast<std::uint64_t>(
        std::chrono::duration_cast<std::chrono::milliseconds>(
            std::chrono::system_clock::now().time_since_epoch()).count());
    TradingToolResult result;
    if (pending.deadlineAtMs <= nowMs)
    {
        ++m_deadlineRejections;
        result.status = TradingToolCallStatus::Rejected;
        result.toolName = pending.request.call.name;
        result.reasonCode = "QUEUE_DEADLINE_EXCEEDED";
    }
    else result = m_host.Invoke(pending.peerUid, pending.request);
    --m_activeRequests;
    m_decisionAudit.AppendOutcome(true, pending.peerUid, &pending.request,
        &pending.binding, pending.mutation, result);
    ReplyAndClose(pending.clientFd, result);
    {
        std::lock_guard<std::mutex> lock(m_queueMutex);
        const std::unordered_map<std::string, std::size_t>::iterator active =
            m_activeByOwner.find(pending.owner);
        if (active != m_activeByOwner.end() && --active->second == 0)
            m_activeByOwner.erase(active);
    }
    m_queueReady.notify_all();
}

void UnixToolServer::ReplyAndClose(int clientFd, const TradingToolResult& result)
{
    std::string reason;
    TypedToolProtocol::WriteFrame(clientFd,
        TypedToolProtocol::EncodeResultJson(result), m_ioTimeoutMs, reason);
    ::shutdown(clientFd, SHUT_RDWR);
    ::close(clientFd);
}
