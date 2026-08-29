#pragma once

#include "execution_authority.h"
#include "execution_service_runtime_config.h"
#include "../events/execution_event_hub.h"
#include "../oms_journal.h"
#include "../simulator/deterministic_execution_venue.h"

#include <atomic>
#include <condition_variable>
#include <cstdint>
#include <memory>
#include <mutex>
#include <string>
#include <thread>

class ExecutionCoordinator;
class ExecutionDecisionLeaseAuthority;
class UnixExecutionServiceServer;
class UnixExecutionEventFeedServer;

class ExecutionServiceRuntimeComposition
{
public:
    explicit ExecutionServiceRuntimeComposition(const ExecutionServiceRuntimeConfig& config);
    ~ExecutionServiceRuntimeComposition();

    bool Start(std::string& reason);
    void Stop();
    bool IsRunning() const;
    bool IsMutationBlocked(std::string* reason = nullptr) const;
    const std::string& RecoveryReason() const;

    ExecutionCoordinator& Coordinator();
    DeterministicExecutionVenue& Venue();
    ExecutionEventHub& EventHub();

private:
    class SimulatorPolicyAuthority;

    bool PreparePrivateState(std::string& reason);
    bool LoadFenceCredential(std::string& reason);
    bool RestoreSimulatorState(std::string& reason);
    bool StartSimulatorQuoteFeed(std::string& reason);
    void StopSimulatorQuoteFeed();
    void RefreshSimulatorQuotes();
    void SimulatorQuoteFeedLoop();
    void CloseUnconsumedListenFd();

    ExecutionServiceRuntimeConfig m_config;
    int m_ownedListenFd;
    int m_ownedEventListenFd;
    int m_stateLockFd;
    std::uint64_t m_fencingToken;
    std::uint64_t m_fencingGeneration;
    ExecutionServiceIdentity m_serviceIdentity;
    std::shared_ptr<ExecutionServiceLifecycleGate> m_lifecycleGate;
    bool m_startAttempted;
    bool m_started;
    std::string m_recoveryReason;
    OmsJournal m_journal;
    DeterministicExecutionVenue m_venue;
    std::unique_ptr<ExecutionEventHub> m_eventHub;
    std::shared_ptr<ExecutionDecisionLeaseAuthority> m_decisionLeases;
    std::unique_ptr<ExecutionCoordinator> m_coordinator;
    std::unique_ptr<SimulatorPolicyAuthority> m_policyAuthority;
    std::unique_ptr<UnixExecutionServiceServer> m_server;
    std::unique_ptr<UnixExecutionEventFeedServer> m_eventServer;
    std::mutex m_quoteFeedMutex;
    std::condition_variable m_quoteFeedChanged;
    std::thread m_quoteFeedThread;
    std::atomic<bool> m_quoteFeedRunning;
    bool m_quoteFeedStop;
};
