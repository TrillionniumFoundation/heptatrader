#pragma once

#include "execution_event_feed_contract.h"
#include "execution_gateway_context_binding.h"

#include <atomic>
#include <condition_variable>
#include <cstddef>
#include <cstdint>
#include <deque>
#include <memory>
#include <mutex>
#include <set>
#include <string>
#include <thread>
#include <vector>

// Privileged event-feed server owned by the Execution Service process.
class UnixExecutionEventFeedServer
{
public:
    UnixExecutionEventFeedServer(
        ExecutionEventFeedSource& source,
        const ExecutionServiceIdentity& serviceIdentity,
        const std::shared_ptr<ExecutionServiceLifecycleGate>& lifecycleGate);
    ~UnixExecutionEventFeedServer();

    // Takes ownership of a listening AF_UNIX/SOCK_STREAM descriptor. The
    // activated socket pathname remains owned by the service manager.
    bool StartFromFd(int listenFd,
                     const std::set<std::uint32_t>& allowedPeerUids,
                     std::string& reason,
                     std::size_t maxRequestBytes = 8192,
                     int ioTimeoutMs = 1000,
                     std::size_t workerCount = 4,
                     std::size_t maxPendingClients = 32);
    bool StartFromFd(int listenFd,
                     const std::set<std::uint32_t>& allowedPeerUids,
                     const ExecutionGatewayContextBinding& gatewayContextBinding,
                     std::string& reason,
                     std::size_t maxRequestBytes = 8192,
                     int ioTimeoutMs = 1000,
                     std::size_t workerCount = 4,
                     std::size_t maxPendingClients = 32);
    void Stop();
    bool IsRunning() const;

private:
    void AcceptLoop();
    void WorkerLoop();
    void HandleClient(int clientFd);
    bool StartFromFdInternal(
        int listenFd,
        const std::set<std::uint32_t>& allowedPeerUids,
        const ExecutionGatewayContextBinding* gatewayContextBinding,
        std::string& reason,
        std::size_t maxRequestBytes,
        int ioTimeoutMs,
        std::size_t workerCount,
        std::size_t maxPendingClients);

    ExecutionEventFeedSource& m_source;
    const ExecutionServiceIdentity m_serviceIdentity;
    std::shared_ptr<ExecutionServiceLifecycleGate> m_lifecycleGate;
    std::atomic<bool> m_stop;
    std::atomic<int> m_listenFd;
    std::set<std::uint32_t> m_allowedPeerUids;
    ExecutionGatewayContextBinding m_gatewayContextBinding;
    bool m_enforceGatewayContextBinding;
    std::size_t m_maxRequestBytes;
    int m_ioTimeoutMs;
    std::size_t m_maxPendingClients;
    std::thread m_acceptThread;
    std::vector<std::thread> m_workers;
    std::deque<int> m_pendingClients;
    mutable std::mutex m_mutex;
    // Linearizes the final response admission/write with Stop(). A response
    // that wins this lock is completed before Stop takes effect; every later
    // response observes stopping/not-ready and cannot return an Event.
    mutable std::mutex m_responseMutex;
    std::condition_variable m_pendingChanged;
};
