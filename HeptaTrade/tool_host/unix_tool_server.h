#pragma once

#include "tool_decision_audit.h"
#include "trading_tool_host.h"
#include "unix_socket_path_identity.h"

#include <atomic>
#include <cstddef>
#include <condition_variable>
#include <deque>
#include <functional>
#include <map>
#include <mutex>
#include <string>
#include <thread>
#include <unordered_map>
#include <vector>

struct UnixToolServerHealth
{
    std::size_t pendingConnections = 0;
    std::size_t activeRequests = 0;
    std::size_t readyOwners = 0;
    std::uint64_t queueBackpressureRejections = 0;
    std::uint64_t ownerBackpressureRejections = 0;
    std::uint64_t deadlineRejections = 0;
    std::uint64_t cancelledRequests = 0;
};

class UnixToolServer
{
public:
    typedef std::function<void(const TradingToolHostSessionBinding&, const std::string&)>
        BackpressureObserver;

    explicit UnixToolServer(TradingToolHost& host);
    ~UnixToolServer();

    bool Start(const std::string& socketPath, std::string& reason,
               std::size_t maxRequestBytes = 65536, int ioTimeoutMs = 3000,
               std::size_t workerCount = 4, std::size_t maxPendingConnections = 32,
               std::size_t maxConcurrentPerOwner = 1,
               std::size_t maxPendingPerOwner = 8,
               std::size_t ingressWorkerCount = 2,
               std::uint64_t maxQueueWaitMs = 5000);
    bool StartFromFd(int listenFd, std::string& reason,
               std::size_t maxRequestBytes = 65536, int ioTimeoutMs = 3000,
               std::size_t workerCount = 4, std::size_t maxPendingConnections = 32,
               std::size_t maxConcurrentPerOwner = 1,
               std::size_t maxPendingPerOwner = 8,
               std::size_t ingressWorkerCount = 2,
               std::uint64_t maxQueueWaitMs = 5000);
    void Stop();
    bool Drain(std::uint64_t timeoutMs);
    bool IsRunning() const;
    UnixToolServerHealth GetHealth() const;
    void SetBackpressureObserver(const BackpressureObserver& observer);
    void SetDecisionAuditJournal(SessionSupervisorAuditJournal* journal);
    void AllowMissingDecisionAuditForTests();

private:
    struct PendingRequest
    {
        int clientFd = -1;
        std::uint32_t peerUid = 0;
        std::string owner;
        TradingToolHostSessionBinding binding;
        TradingToolHostRequest request;
        bool mutation = false;
        std::uint64_t deadlineAtMs = 0;
    };

    void AcceptLoop();
    bool IsAccepting() const;
    void IngressLoop();
    void ExecutionLoop();
    void DecodeAndQueue(int clientFd);
    bool DecodeIngress(int clientFd,
                       std::uint32_t& peerUid,
                       TradingToolHostRequest& request,
                       TradingToolResult& rejection,
                       bool& peerCredentialAvailable,
                       bool& decodedRequest);
    bool HandleCancellation(PendingRequest& pending, bool hasBinding);
    void QueueRequest(PendingRequest pending,
                      bool hasBinding,
                      bool peerMatches);
    void Execute(PendingRequest pending);
    void ReplyAndClose(int clientFd, const TradingToolResult& result);
    bool Activate(int listenFd, const std::string& socketPath, bool unlinkOnStop,
                  std::string& reason, std::size_t maxRequestBytes, int ioTimeoutMs,
                  std::size_t workerCount, std::size_t maxPendingConnections,
                  std::size_t maxConcurrentPerOwner, std::size_t maxPendingPerOwner,
                  std::size_t ingressWorkerCount, std::uint64_t maxQueueWaitMs);

private:
    TradingToolHost& m_host;
    ToolDecisionAudit m_decisionAudit;
    std::atomic<bool> m_stop;
    std::atomic<int> m_listenFd;
    std::string m_socketPath;
    bool m_unlinkOnStop;
    UnixSocketPathIdentity m_socketPathIdentity;
    // Drain() may close the listener before Stop() runs.  Preserve the fact
    // that the descriptor was a valid socket so owned pathname cleanup still
    // has an identity witness without touching an activated listener.
    std::atomic<bool> m_drainedListenerIdentityValid{false};
    std::size_t m_maxRequestBytes;
    int m_ioTimeoutMs;
    std::size_t m_maxPendingConnections;
    std::size_t m_maxConcurrentPerOwner;
    std::size_t m_maxPendingPerOwner;
    std::uint64_t m_maxQueueWaitMs;
    std::size_t m_pendingCount;
    std::atomic<std::uint64_t> m_queueBackpressureRejections;
    std::atomic<std::uint64_t> m_ownerBackpressureRejections;
    std::atomic<std::uint64_t> m_deadlineRejections;
    std::atomic<std::uint64_t> m_cancelledRequests;
    std::atomic<std::size_t> m_activeRequests;
    std::thread m_acceptThread;
    mutable std::mutex m_queueMutex;
    std::condition_variable m_queueReady;
    std::deque<int> m_pendingClients;
    std::map<std::string, std::deque<PendingRequest> > m_ownerQueues;
    std::deque<std::string> m_readyOwners;
    std::vector<std::thread> m_ingressWorkers;
    std::vector<std::thread> m_executionWorkers;
    std::unordered_map<std::string, std::size_t> m_activeByOwner;
    BackpressureObserver m_backpressureObserver;
};
