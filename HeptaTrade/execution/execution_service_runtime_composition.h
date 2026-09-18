#pragma once

#include "execution_runtime_observation.h"

#include "execution_authority.h"
#include "execution_service_runtime_config.h"
#include "../events/execution_event_hub.h"
#include "../oms_generation_store.h"
#include "../oms_journal.h"
#include "../simulator/deterministic_execution_venue.h"

#include <atomic>
#include <condition_variable>
#include <cstdint>
#include <functional>
#include <limits>
#include <memory>
#include <mutex>
#include <string>
#include <thread>

class ExecutionCoordinator;
class ExecutionDecisionLeaseAuthority;
class UnixExecutionServiceServer;
class UnixExecutionEventFeedServer;

// The coordinator deliberately restores only bounded hot state from a sealed
// OMS generation. Simulator risk restoration has a different requirement: it
// needs terminal fills, the full admitted-order count and the order-id high
// water mark. Keep that distinction local to the simulator composition instead
// of teaching the legacy OmsJournal reader to accept the V2 sentinel.
class SimulatorStateRecoveryJournal final : public OmsJournal
{
public:
    int Replay(const std::function<void(const OmsJournalEvent&)>& onEvent) const
    {
        OmsGenerationStore generations(GetPath());
        if (generations.HasStore())
        {
            std::uint64_t records = 0;
            std::string reason;
            if (generations.ReplayCompleteHistory(
                    256U * 1024U, onEvent, records, reason))
            {
                if (records > static_cast<std::uint64_t>(
                        std::numeric_limits<int>::max()))
                    return -1;
                return static_cast<int>(records);
            }
            // A V1 checkpoint does not rotate the journal: its ordinary JSONL
            // file is still complete, so retain the exact legacy replay path.
            // Any V2 lineage error remains fail closed and must not silently
            // fall back to reading only the active tail.
            if (reason != "OMS_GENERATION_COMPLETE_REPLAY_REQUIRES_V2")
                return -1;
        }
        return OmsJournal::Replay(onEvent);
    }
};

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
    // Local process observability only; not an Agent command or admission gate.
    const std::string& ServiceEpoch() const { return m_serviceIdentity.serviceEpoch; }
    OmsJournalHealthSnapshot JournalHealth() const { return m_journal.GetHealthSnapshot(); }
    ExecutionRuntimeObservation CoordinatorObservation() const;

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
    OmsLatencySummary m_simulatorStateRecoveryLatency;
    OmsLatencySummary m_startupReadyLatency;
    SimulatorStateRecoveryJournal m_journal;
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
