#pragma once

#include "execution_authority.h"
#include "execution_gateway_context_binding.h"

#include <atomic>
#include <chrono>
#include <cstddef>
#include <cstdint>
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
    friend class PreviewPermitTestAccess;
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

    // A permit is removed from m_previewPermits at the single transition
    // point immediately before authority dispatch.  Keep a bounded, in-memory
    // witness for that transition while the authority call is running (and
    // for a short replay window after it completes).  This closes the race in
    // which a concurrent retry would otherwise observe only
    // UNKNOWN_OR_CONSUMED before the first call has produced its durable
    // Execution result.  The durable authority remains the source of truth
    // across process restart; this cache is deliberately bounded and never
    // stores a raw permit in completed records.
    struct PreviewDispatchRecord
    {
        std::string ownerKey;
        std::string fingerprint;
        std::string permit;
        bool flatten = false;
        bool complete = false;
        ExecutionCommandResult result;
        std::chrono::steady_clock::time_point steadyExpiresAt;
    };

    void AcceptLoop();
    void HandleClient(int clientFd);
    bool ReadAuthorizedRequest(int clientFd,
                               const std::chrono::steady_clock::time_point& deadline,
                               ExecutionServiceRequest& request,
                               std::string& reason);
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
    // Validate without mutating the permit store.  Dispatch uses this phase
    // before acquiring the decision lease so a caller that fails an
    // independent safety check can retry the exact preview command.
    bool ValidatePreviewPermit(const PlaceOrderCommand& command,
                               std::string& reason) const;
    bool ConsumePreviewPermit(const PlaceOrderCommand& command,
                              std::string& reason);
    bool IssueFlattenPreviewPermit(
        const FlattenPositionCommand& command,
        const ExecutionCommandResult& preview,
        std::string& permit,
        std::string& mutationCommandId,
        long long& expiresAtMs,
        std::string& reason);
    // See ValidatePreviewPermit().  Flatten validation also checks the
    // service-owned snapshot binding, but does not inject or consume it.
    bool ValidateFlattenPreviewPermit(
        const FlattenPositionCommand& command,
        std::string& reason) const;
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
    mutable std::mutex m_lifecycleMutex;
    mutable std::mutex m_previewMutex;
    std::unordered_map<std::string, PreviewPermitRecord> m_previewPermits;
    // Keyed by owner + operation + server-issued mutation command id.
    std::unordered_map<std::string, PreviewDispatchRecord>
        m_previewDispatches;
};
