#include "unix_execution_service_server.h"
#include "execution_service_protocol.h"
#include <algorithm>
#include <arpa/inet.h>
#include <cerrno>
#include <cstring>
#include <fcntl.h>
#include <poll.h>
#include <stdexcept>
#include <sys/eventfd.h>
#include <sys/socket.h>
#include <unistd.h>

namespace {
thread_local const UnixExecutionServiceServer* schedulerOwner = nullptr;
const std::size_t kMaxConnections = 64;
const std::size_t kMaxQueuedPerLane = 24;

bool ControlLane(ExecutionServiceOperation operation)
{
    // Fence release shares the fence lane: an earlier queued release must
    // never overtake a later completed fence. These are local snapshot/control
    // calls, not Broker I/O.
    return operation == ExecutionServiceOperation::GetServiceIdentity ||
        operation == ExecutionServiceOperation::QueryCommandStatus ||
        operation == ExecutionServiceOperation::RecoveryQueryCommandStatus ||
        operation == ExecutionServiceOperation::FenceSessionOwner ||
        operation == ExecutionServiceOperation::ReleaseSessionOwnerFence;
}

bool ExitLane(ExecutionServiceOperation operation)
{
    // Guarded exit operations may perform venue I/O, so they must not share
    // the short control lane. Give them their own FIFO instead of placing them
    // behind risk-increasing placement work.
    return operation == ExecutionServiceOperation::CancelIbOrder ||
        operation == ExecutionServiceOperation::FlattenPosition;
}
}

struct UnixExecutionServiceServer::ClientJob
{
    enum State { Reading, Queued, Executing, Writing, Closed } state = Reading;
    int fd = -1;
    std::chrono::steady_clock::time_point deadline;
    char header[4] = {};
    std::size_t headerBytes = 0;
    std::size_t bodyBytes = 0;
    std::string body;
    ExecutionServiceRequest request;
    std::string response;
    std::size_t responseBytes = 0;
};

bool UnixExecutionServiceServer::IsSchedulerThread() const
{
    return schedulerOwner == this;
}

void UnixExecutionServiceServer::WakeScheduler()
{
    if (m_wakeFd < 0) return;
    const std::uint64_t one = 1;
    const ssize_t ignored = ::write(m_wakeFd, &one, sizeof(one));
    (void)ignored; // EAGAIN means a wake is already pending.
}

void UnixExecutionServiceServer::RequestSchedulerStop()
{
    if (m_lifecycleGate) m_lifecycleGate->ready.store(false);
    {
        // Publish the predicate under the same mutex used by wait(). Atomic
        // alone would allow notify to fall between predicate-check and sleep.
        std::lock_guard<std::mutex> lock(m_schedulerMutex);
        m_stop.store(true);
    }
    m_schedulerChanged.notify_all();
    WakeScheduler();
}

void UnixExecutionServiceServer::StartScheduler()
{
    const int flags = ::fcntl(m_listenFd.load(), F_GETFL);
    if (flags < 0 || ::fcntl(m_listenFd.load(), F_SETFL, flags | O_NONBLOCK) < 0)
        throw std::runtime_error("cannot make Execution listener nonblocking");
    m_wakeFd = ::eventfd(0, EFD_CLOEXEC | EFD_NONBLOCK);
    if (m_wakeFd < 0) throw std::runtime_error("cannot create Execution reactor wakeup");
    try
    {
        m_controlThread = std::thread(
            &UnixExecutionServiceServer::AuthorityLoop, this, true, false);
        m_exitThread = std::thread(
            &UnixExecutionServiceServer::AuthorityLoop, this, false, true);
        m_commandThread = std::thread(
            &UnixExecutionServiceServer::AuthorityLoop, this, false, false);
        m_acceptThread =
            std::thread(&UnixExecutionServiceServer::AcceptLoop, this);
    }
    catch (...)
    {
        RequestSchedulerStop();
        JoinScheduler();
        ::close(m_wakeFd);
        m_wakeFd = -1;
        throw;
    }
}

void UnixExecutionServiceServer::JoinScheduler()
{
    if (m_acceptThread.joinable()) m_acceptThread.join();
    if (m_commandThread.joinable()) m_commandThread.join();
    if (m_exitThread.joinable()) m_exitThread.join();
    if (m_controlThread.joinable()) m_controlThread.join();
    std::lock_guard<std::mutex> lock(m_schedulerMutex);
    for (const auto& client : m_clients) CloseClient(client);
    m_clients.clear();
    m_controlQueue.clear();
    m_exitQueue.clear();
    m_commandQueue.clear();
}

// Client state and descriptor ownership below are guarded by schedulerMutex.
// Closing and reusing a descriptor is serialized with client state changes.
// Stop joins every lane before destroying authority or descriptor state.
void UnixExecutionServiceServer::CloseClient(const std::shared_ptr<ClientJob>& client)
{
    if (client->fd >= 0) ::close(client->fd);
    client->fd = -1;
    client->state = ClientJob::Closed;
}

void UnixExecutionServiceServer::SetResponse(
    const std::shared_ptr<ClientJob>& client, const std::string& body)
{
    if (client->fd < 0) return;
    if (body.empty() || body.size() > 1048576)
    {
        CloseClient(client);
        return;
    }
    const std::uint32_t length = htonl(static_cast<std::uint32_t>(body.size()));
    client->response.assign(reinterpret_cast<const char*>(&length), sizeof(length));
    client->response.append(body);
    // Authority execution is not an I/O phase. Once it completes, give the
    // response its own bounded nonblocking write window instead of reusing the
    // accept/read or queue deadline that may already have elapsed.
    client->deadline = std::chrono::steady_clock::now() +
        std::chrono::milliseconds(m_ioTimeoutMs);
    client->state = ClientJob::Writing;
}

void UnixExecutionServiceServer::ReceiveClient(const std::shared_ptr<ClientJob>& client)
{
    while (client->state == ClientJob::Reading)
    {
        char* destination;
        std::size_t remaining;
        if (client->headerBytes < sizeof(client->header))
        {
            destination = client->header + client->headerBytes;
            remaining = sizeof(client->header) - client->headerBytes;
        }
        else
        {
            destination = &client->body[client->bodyBytes];
            remaining = client->body.size() - client->bodyBytes;
        }
        const ssize_t count = ::recv(client->fd, destination, remaining, MSG_DONTWAIT);
        if (count < 0 && errno == EINTR) continue;
        if (count < 0 && (errno == EAGAIN || errno == EWOULDBLOCK)) return;
        if (count <= 0) { CloseClient(client); return; }
        if (client->headerBytes < sizeof(client->header))
        {
            client->headerBytes += static_cast<std::size_t>(count);
            if (client->headerBytes != sizeof(client->header)) continue;
            std::uint32_t length;
            std::memcpy(&length, client->header, sizeof(length));
            length = ntohl(length);
            if (length == 0 || length > m_maxRequestBytes) { CloseClient(client); return; }
            client->body.resize(length);
            continue;
        }
        client->bodyBytes += static_cast<std::size_t>(count);
        if (client->bodyBytes != client->body.size()) continue;
        std::string reason;
        if (!ExecutionServiceProtocol::DecodeRequest(client->body, client->request, reason))
        {
            ExecutionCommandResult result;
            result.reasonCode = reason;
            result.detail = "Execution IPC request rejected before authority dispatch";
            std::string response;
            if (ExecutionServiceProtocol::EncodeResponse(result, response, reason))
                SetResponse(client, response);
            else CloseClient(client);
            return;
        }
        std::deque<std::shared_ptr<ClientJob>>* queue =
            &m_commandQueue;
        if (ControlLane(client->request.operation))
            queue = &m_controlQueue;
        else if (ExitLane(client->request.operation))
            queue = &m_exitQueue;
        if (queue->size() >= kMaxQueuedPerLane)
        {
            // No authority has been called. Transport failure is conservative
            // for all caller operations and cannot fabricate a mutation result.
            CloseClient(client);
            return;
        }
        // Completing the input frame starts a distinct bounded queue phase.
        // A slow sender cannot consume this budget because the read phase had
        // its own deadline from accept time.
        client->deadline = std::chrono::steady_clock::now() +
            std::chrono::milliseconds(m_ioTimeoutMs);
        client->state = ClientJob::Queued;
        queue->push_back(client);
        m_schedulerChanged.notify_all();
    }
}

void UnixExecutionServiceServer::WriteClient(const std::shared_ptr<ClientJob>& client)
{
    while (client->responseBytes < client->response.size())
    {
        const ssize_t count = ::send(client->fd,
            client->response.data() + client->responseBytes,
            client->response.size() - client->responseBytes, MSG_DONTWAIT | MSG_NOSIGNAL);
        if (count < 0 && errno == EINTR) continue;
        if (count < 0 && (errno == EAGAIN || errno == EWOULDBLOCK)) return;
        if (count <= 0) { CloseClient(client); return; }
        client->responseBytes += static_cast<std::size_t>(count);
    }
    CloseClient(client);
}

void UnixExecutionServiceServer::AuthorityLoop(
    bool controlLane, bool exitLane)
{
    schedulerOwner = this;
    try
    {
        auto& queue = controlLane ? m_controlQueue :
            (exitLane ? m_exitQueue : m_commandQueue);
        while (!m_stop.load())
        {
            std::shared_ptr<ClientJob> client;
            {
                std::unique_lock<std::mutex> lock(m_schedulerMutex);
                m_schedulerChanged.wait(lock, [this, &queue] { return m_stop.load() || !queue.empty(); });
                if (m_stop.load()) break;
                client = queue.front();
                queue.pop_front();
                if (client->state == ClientJob::Closed) continue;
                if (std::chrono::steady_clock::now() >= client->deadline)
                {
                    CloseClient(client);
                    continue;
                }
                client->state = ClientJob::Executing;
            }
            // The authority owns durable effects. Once dispatched it must run
            // to a typed result; an ingress/queue timeout must never close the
            // executing reply channel or imply cancellation/retry. The caller's
            // independently bounded response wait may still expire and report
            // uncertainty, while this service completes exactly once.
            const std::string response = HandleRequest(client->request, client->deadline);
            {
                std::lock_guard<std::mutex> lock(m_schedulerMutex);
                if (!m_stop.load() && client->state != ClientJob::Closed)
                    SetResponse(client, response);
            }
            WakeScheduler();
        }
    }
    catch (...)
    {
        // A possibly-effectful exception cannot be represented as rejection.
        // Preserve the journal and fail the service closed for recovery.
        RequestSchedulerStop();
    }
    schedulerOwner = nullptr;
}

void UnixExecutionServiceServer::AcceptLoop()
{
    schedulerOwner = this;
    try
    {
        while (!m_stop.load())
        {
            std::vector<struct pollfd> descriptors;
            std::vector<std::shared_ptr<ClientJob>> observed;
            descriptors.push_back({m_listenFd.load(), POLLIN, 0});
            descriptors.push_back({m_wakeFd, POLLIN, 0});
            {
                std::lock_guard<std::mutex> lock(m_schedulerMutex);
                const auto now = std::chrono::steady_clock::now();
                for (const auto& client : m_clients)
                {
                    if ((client->state == ClientJob::Reading ||
                         client->state == ClientJob::Queued ||
                         client->state == ClientJob::Writing) &&
                        now >= client->deadline)
                        CloseClient(client);
                    if (client->state == ClientJob::Reading || client->state == ClientJob::Writing)
                    {
                        const short events = client->state == ClientJob::Reading ? POLLIN : POLLOUT;
                        descriptors.push_back({client->fd, events, 0});
                        observed.push_back(client);
                    }
                }
                m_clients.erase(std::remove_if(m_clients.begin(), m_clients.end(),
                    [](const std::shared_ptr<ClientJob>& job) { return job->state == ClientJob::Closed; }),
                    m_clients.end());
            }
            const int ready = ::poll(descriptors.data(), descriptors.size(), 20);
            if (ready < 0 && errno == EINTR) continue;
            if (ready < 0) throw std::runtime_error("Execution poll failed");
            if (m_stop.load()) break;
            if (descriptors[1].revents & POLLIN)
            {
                std::uint64_t value;
                const ssize_t ignored = ::read(m_wakeFd, &value, sizeof(value));
                (void)ignored;
            }
            std::lock_guard<std::mutex> lock(m_schedulerMutex);
            // Process existing frames before accepting a bounded batch. A slow
            // peer retains only its own frame slot, not an ingress worker.
            for (std::size_t i = 0; i < observed.size(); ++i)
            {
                const auto& client = observed[i];
                if (client->state == ClientJob::Closed) continue;
                const short events = descriptors[i + 2].revents;
                if (events & (POLLERR | POLLNVAL)) { CloseClient(client); continue; }
                if (client->state == ClientJob::Reading && (events & (POLLIN | POLLHUP)))
                    ReceiveClient(client);
                else if (client->state == ClientJob::Writing && (events & POLLOUT))
                    WriteClient(client);
                else if (events & POLLHUP) CloseClient(client);
            }
            if (!(descriptors[0].revents & POLLIN)) continue;
            for (unsigned int accepted = 0; accepted < 8; ++accepted)
            {
                const int fd = ::accept4(m_listenFd.load(), nullptr, nullptr, SOCK_CLOEXEC | SOCK_NONBLOCK);
                if (fd < 0) break;
                struct ucred credential;
                socklen_t length = sizeof(credential);
                if (m_clients.size() >= kMaxConnections ||
                    ::getsockopt(fd, SOL_SOCKET, SO_PEERCRED, &credential, &length) != 0 ||
                    length != sizeof(credential) ||
                    !m_allowedPeerUids.count(static_cast<std::uint32_t>(credential.uid)))
                {
                    ::close(fd);
                    continue;
                }
                std::shared_ptr<ClientJob> client;
                try { client.reset(new ClientJob()); }
                catch (...) { ::close(fd); throw; }
                client->fd = fd;
                client->deadline = std::chrono::steady_clock::now() + std::chrono::milliseconds(m_ioTimeoutMs);
                try { m_clients.push_back(client); }
                catch (...) { CloseClient(client); throw; }
            }
        }
    }
    catch (...) { RequestSchedulerStop(); }
    {
        std::lock_guard<std::mutex> lock(m_schedulerMutex);
        for (const auto& client : m_clients) CloseClient(client);
    }
    schedulerOwner = nullptr;
}
