#pragma once

#include "execution_authority.h"
#include "execution_gateway_context_binding.h"

#include <atomic>
#include <chrono>
#include <cstddef>
#include <cstdint>
#include <condition_variable>
#include <deque>
#include <vector>
#include <memory>
#include <mutex>
#include <set>
#include <string>
#include <thread>
#include <unordered_map>

bool GenerateExecutionServiceIdentity(
    std::uint64_t serviceFencingGeneration,
    ExecutionServiceIdentity& identity,
    std::string& reason);

class ExecutionDecisionLeaseAuthority;
struct ExecutionServiceRequest;

// Privileged service implementation.  Agent-facing targets must depend on
// unix_execution_service_client.h instead of this header.
class UnixExecutionServiceServer
{
public:
    explicit UnixExecutionServiceServer(
        ExecutionAuthority& authority,
        ExecutionControlAuthority* controlAuthority = nullptr,
        const std::shared_ptr<ExecutionDecisionLeaseAuthority>& decisionLeases =
            std::shared_ptr<ExecutionDecisionLeaseAuthority>());
    ~UnixExecutionServiceServer();

    bool Start(const std::string& socketPath,
               const std::set<std::uint32_t>& allowedPeerUids,
               std::string& reason,
               std::size_t maxRequestBytes = 32768,
               int ioTimeoutMs = 3000);
    // Takes ownership of an already-listening AF_UNIX/SOCK_STREAM descriptor,
    // such as fd 3 supplied by systemd socket activation. The descriptor is
    // closed on validation/start failure or exactly once by Stop()/destruction;
    // its pathname is never unlinked by this process.
    bool StartFromFd(int listenFd,
                     const std::set<std::uint32_t>& allowedPeerUids,
                     std::string& reason,
                     std::size_t maxRequestBytes = 32768,
                     int ioTimeoutMs = 3000);
    bool StartFromFd(int listenFd,
                     const std::set<std::uint32_t>& allowedPeerUids,
                     const ExecutionServiceIdentity& identity,
                     std::string& reason,
                     std::size_t maxRequestBytes = 32768,
                     int ioTimeoutMs = 3000);
    bool StartFromFd(
        int listenFd,
        const std::set<std::uint32_t>& allowedPeerUids,
        const ExecutionServiceIdentity& identity,
        const std::shared_ptr<ExecutionServiceLifecycleGate>& lifecycleGate,
        std::string& reason,
        std::size_t maxRequestBytes = 32768,
        int ioTimeoutMs = 3000);
    // Production runtime entry point. In addition to SO_PEERCRED, every
    // non-discovery request must match the single reviewed trust-domain
    // Agent/account/venue/execution-domain binding.
    bool StartFromFd(
        int listenFd,
        const std::set<std::uint32_t>& allowedPeerUids,
        const ExecutionGatewayContextBinding& gatewayContextBinding,
        const ExecutionServiceIdentity& identity,
        const std::shared_ptr<ExecutionServiceLifecycleGate>& lifecycleGate,
        std::string& reason,
        std::size_t maxRequestBytes = 32768,
        int ioTimeoutMs = 3000);
    void Stop();
    bool IsRunning() const;
    std::string ServiceEpoch() const;
    ExecutionServiceIdentity ServiceIdentity() const;

private:
    struct PreviewPermitRecord
    {
        std::string fingerprint;
        std::string ownerKey;
        std::string mutationCommandId;
        long long expiresAtMs = 0;
        std::chrono::steady_clock::time_point steadyExpiresAt;
        bool flattenSnapshot = false;
        double flattenPositionQuantity = 0.0;
        std::uint64_t flattenConnectionEpoch = 0;
        std::uint64_t flattenPositionGeneration = 0;
        std::string flattenPlanBinding;
    };

    // A bounded nonblocking framing reactor feeds two serialized lanes.
    // Only owner/status controls may overlap an ordinary authority call.
    struct ClientJob;
    void StartScheduler();
    void RequestSchedulerStop();
    void JoinScheduler();
    bool IsSchedulerThread() const;
    void WakeScheduler();
    void AcceptLoop();
    void AuthorityLoop(bool controlLane);
    void ReceiveClient(const std::shared_ptr<ClientJob>& client);
    void WriteClient(const std::shared_ptr<ClientJob>& client);
    void CloseClient(const std::shared_ptr<ClientJob>& client);
    void SetResponse(const std::shared_ptr<ClientJob>& client,
                     const std::string& body);
    std::string HandleRequest(const ExecutionServiceRequest& request,
        const std::chrono::steady_clock::time_point& deadline);
    bool ApplyPreDispatchGate(const ExecutionServiceRequest& request,
                              ExecutionCommandResult& result,
                              ExecutionControlResult& controlResult,
                              bool& controlResponse);
    ExecutionCommandResult DispatchPlaceOrder(
        const IbPlaceOrderCommand& command);
    ExecutionCommandResult DispatchPreviewOrder(
        const IbPlaceOrderCommand& command);
    ExecutionControlResult DispatchControl(
        const ExecutionServiceRequest& request);
    void DispatchRequest(const ExecutionServiceRequest& request,
                         ExecutionCommandResult& result,
                         ExecutionControlResult& controlResult,
                         bool& controlResponse);
    void ValidateAndBindResponse(const ExecutionServiceRequest& request,
                                 ExecutionCommandResult& result,
                                 ExecutionControlResult& controlResult,
                                 bool controlResponse) const;
    bool StartFromFdInternal(
        int listenFd,
        const std::set<std::uint32_t>& allowedPeerUids,
        const ExecutionGatewayContextBinding* gatewayContextBinding,
        const ExecutionServiceIdentity& identity,
        const std::shared_ptr<ExecutionServiceLifecycleGate>& lifecycleGate,
        std::string& reason,
        std::size_t maxRequestBytes,
        int ioTimeoutMs);
    bool IssuePreviewPermit(const PlaceOrderCommand& command,
                            std::string& permit,
                            std::string& mutationCommandId,
                            long long& expiresAtMs,
                            std::string& reason);
    bool ConsumePreviewPermit(const PlaceOrderCommand& command,
                              std::string& reason);
    bool IssueFlattenPreviewPermit(
        const FlattenPositionCommand& command,
        const ExecutionCommandResult& preview,
        std::string& permit,
        std::string& mutationCommandId,
        long long& expiresAtMs,
        std::string& reason);
    bool ConsumeFlattenPreviewPermit(
        FlattenPositionCommand& command,
        std::string& reason);
    ExecutionCommandResult DispatchFlattenPosition(
        const FlattenPositionCommand& command);
    ExecutionCommandResult DispatchFlattenPreview(
        const FlattenPositionCommand& command);
    void RevokePreviewPermitsForOwner(const std::string& agentId,
                                      const std::string& sessionId);

    ExecutionAuthority& m_authority;
    ExecutionControlAuthority* m_controlAuthority;
    ExecutionReadAuthority* m_readAuthority;
    std::shared_ptr<ExecutionDecisionLeaseAuthority> m_decisionLeases;
    std::atomic<bool> m_stop;
    std::atomic<int> m_listenFd;
    std::string m_socketPath;
    std::uint64_t m_socketDevice;
    std::uint64_t m_socketInode;
    bool m_ownsSocketPath;
    int m_socketLockFd;
    std::set<std::uint32_t> m_allowedPeerUids;
    ExecutionGatewayContextBinding m_gatewayContextBinding;
    bool m_enforceGatewayContextBinding;
    std::size_t m_maxRequestBytes;
    int m_ioTimeoutMs;
    ExecutionServiceIdentity m_serviceIdentity;
    std::shared_ptr<ExecutionServiceLifecycleGate> m_lifecycleGate;
    std::thread m_acceptThread;
    std::thread m_controlThread;
    std::thread m_commandThread;
    int m_wakeFd = -1;
    mutable std::mutex m_lifecycleMutex;
    std::condition_variable m_lifecycleChanged;
    bool m_stopping = false;
    std::mutex m_schedulerMutex;
    std::condition_variable m_schedulerChanged;
    std::vector<std::shared_ptr<ClientJob>> m_clients;
    std::deque<std::shared_ptr<ClientJob>> m_controlQueue;
    std::deque<std::shared_ptr<ClientJob>> m_commandQueue;
    mutable std::mutex m_previewMutex;
    std::unordered_map<std::string, PreviewPermitRecord> m_previewPermits;
};
