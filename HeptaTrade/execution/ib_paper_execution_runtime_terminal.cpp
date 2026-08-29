#include "ib_paper_execution_runtime_internal.h"

#include <chrono>
#include <thread>
#include <vector>

using namespace ib_paper_execution_runtime_internal;

bool IbPaperExecutionRuntimeComposition::PublishTerminalControlReady(
    std::string& reason)
{
    std::lock_guard<std::mutex> lifecycleLock(m_lifecycleStateMutex);
    m_polling.store(false);
    m_lifecycleGate->ready.store(false);
    m_lifecycleGate->terminalControlOnly.store(true);
    m_started = true;
    reason.clear();
    return true;
}

void IbPaperExecutionRuntimeComposition::BindTerminalPolicyCallbacks(
    IbPaperExecutionPolicyCallbacks& callbacks)
{
    callbacks.beginTerminalRecoveryAudit = [this](
        const ExecutionControlCommand& command,
        IBAuthoritativeRecoveryAuditSnapshot& snapshot,
        ExecutionControlResult& terminal,
        std::string& callbackReason) {
            return BeginTerminalRecoveryAudit(
                command, snapshot, terminal, callbackReason);
        };
    callbacks.commitTerminalRecoveryAudit = [this](
        const ExecutionControlCommand& command,
        const ExecutionControlResult& audit,
        ExecutionControlResult& terminal,
        std::string& callbackReason) {
            return CommitTerminalRecoveryAudit(
                command, audit, terminal, callbackReason);
        };
}

bool IbPaperExecutionRuntimeComposition::BuildTerminalControlAuthority(
    std::string& reason)
{
    if (!m_terminalLatchPresent || !m_coordinator || !m_lifecycleGate)
    {
        reason = "IB_PAPER_TERMINAL_CONTROL_STATE_REQUIRED";
        return false;
    }
    IbPaperExecutionPolicyCallbacks callbacks;
    BindTerminalPolicyCallbacks(callbacks);
    callbacks.nowMs = []() {
        return static_cast<std::int64_t>(OmsJournal::NowEpochMs());
    };
    m_policyAuthority.reset(new IbPaperExecutionPolicyAuthority(
        *m_coordinator, m_config.profile, callbacks, m_killSwitch));
    reason.clear();
    return true;
}

bool IbPaperExecutionRuntimeComposition::BeginTerminalRecoveryAudit(
    const ExecutionControlCommand& command,
    IBAuthoritativeRecoveryAuditSnapshot& snapshot,
    ExecutionControlResult& terminalState,
    std::string& reason)
{
    std::unique_lock<std::mutex> terminalLock(m_terminalizationMutex);
    snapshot = IBAuthoritativeRecoveryAuditSnapshot();
    terminalState = ExecutionControlResult();

    if (m_terminalLatchPresent)
    {
        if (!LoadPaperTerminalLatch(reason)) return false;
        const bool exact =
            m_terminalFinalizationId == command.targetCommandId &&
            m_terminalPreliminaryReceiptSha256 ==
                command.terminalPreliminaryReceiptSha256 &&
            m_terminalOwnerAgentId == command.context.agentId &&
            m_terminalOwnerSessionId == command.context.sessionId &&
            m_terminalOwnerAccount == command.context.account &&
            m_terminalOwnerExecutionDomain ==
                command.context.executionDomain &&
            m_terminalRecoveryIngressFence == command.recoveryIngressFence;
        if (!exact)
        {
            reason = "IB_PAPER_TERMINAL_LATCH_BINDING_MISMATCH";
            return false;
        }
        if (m_terminalLatchHalted)
        {
            std::string coordinatorReason;
            const bool coordinatorHalted = m_coordinator &&
                m_coordinator->IsMutationBlocked(&coordinatorReason) &&
                coordinatorReason == "IB_PAPER_TERMINAL_HALTED";
            const bool lifecycleHalted = m_lifecycleGate &&
                !m_lifecycleGate->ready.load() &&
                m_lifecycleGate->terminalControlOnly.load() &&
                !m_polling.load();
            const bool transportHalted = !m_adapter ||
                (m_adapter->IsTerminalTransportHalted() &&
                 !m_adapter->IsConnected());
            if (!coordinatorHalted || !lifecycleHalted || !transportHalted ||
                !m_terminalResult.terminalRuntimeLatchLoaded ||
                !m_terminalResult.terminalRuntimeVerified ||
                !m_terminalResult.terminalLatchDurable ||
                !m_terminalResult.terminalMutationGateClosed ||
                m_terminalResult.terminalBrokerTransportConnected ||
                !m_terminalResult.terminalBrokerEventIngressHalted ||
                !m_terminalResult.terminalBrokerCallbackQueueDrained ||
                m_terminalResult.terminalBrokerCallbacksInFlight != 0 ||
                m_terminalResult.terminalBrokerReconnectPermitted)
            {
                reason = "IB_PAPER_TERMINAL_RUNTIME_WITNESS_INVALID";
                return false;
            }
            terminalState = m_terminalResult;
            terminalState.terminalReplay = true;
            terminalState.reasonCode = "PAPER_EXECUTION_TERMINAL_HALTED";
            reason.clear();
            return true;
        }
    }
    else if (!m_adapter || !m_lifecycleGate || !m_coordinator ||
             !m_lifecycleGate->ready.load() ||
             m_lifecycleGate->terminalControlOnly.load())
    {
        reason = "IB_PAPER_TERMINALIZATION_RUNTIME_UNREADY";
        return false;
    }
    if (!PersistPaperTerminalizingLatch(command, reason)) return false;
    terminalState = m_terminalResult;
    // A restarted PREPARING/TERMINALIZING process deliberately constructs no
    // broker adapter.  It may finish the durable fence+HPM1 projection but it
    // can only hand control to the independent post-cutoff witness chain.
    if (!m_adapter)
    {
        terminalState.terminalReplay = true;
        reason = "POST_CUTOFF_SIGNED_WITNESS_REQUIRED";
        return false;
    }

    {
        std::lock_guard<std::mutex> lifecycleLock(m_lifecycleStateMutex);
        m_lifecycleGate->ready.store(false);
        m_lifecycleGate->terminalControlOnly.store(true);
        m_polling.store(false);
    }
    if (m_pollThread.joinable()) m_pollThread.join();
    std::string quoteCleanupReason;
    const bool quoteCleanupOk = StopQuoteSubscriptions(&quoteCleanupReason);

    std::vector<IBEvent> events;
    IBAuthoritativeRecoveryAuditSnapshot frozen;
    {
        std::lock_guard<std::mutex> eventDrainLock(m_adapterEventDrainMutex);
        events.swap(m_pendingAdapterEvents);
        std::vector<IBEvent> boundaryEvents;
        if (!m_adapter->HaltTransportForTerminalAudit(
                boundaryEvents, frozen, reason))
        {
            terminalState = m_terminalResult;
            terminalState.terminalBrokerTransportConnected =
                m_adapter->IsConnected();
            terminalState.terminalBrokerEventIngressHalted =
                m_adapter->IsTerminalTransportHalted();
            terminalState.terminalBrokerCallbackQueueDrained = false;
            terminalState.terminalBrokerCallbacksInFlight =
                m_adapter->TerminalCallbacksInFlight();
            terminalState.terminalReplay = false;
            return false;
        }
        events.insert(events.end(), boundaryEvents.begin(),
            boundaryEvents.end());

        for (std::vector<IBEvent>::const_iterator event = events.begin();
             event != events.end(); ++event)
        {
            if (event->type == IBEventType::ConnectionClosed)
                continue;
            if (event->type == IBEventType::EventQueueOverflow)
            {
                reason = "IB_PAPER_TERMINAL_EVENT_STREAM_OVERFLOW";
                return false;
            }
            if (HandleNonPersistentAdapterEvent(*event))
            {
                if (m_fatalRuntimeError.load())
                {
                    reason = "IB_PAPER_TERMINAL_EVENT_ROUTE_FAILED";
                    return false;
                }
                continue;
            }
            AdapterRouteAction routed = AdapterRouteAction::Busy;
            const std::chrono::steady_clock::time_point deadline =
                std::chrono::steady_clock::now() +
                std::chrono::seconds(2);
            while (routed == AdapterRouteAction::Busy &&
                   std::chrono::steady_clock::now() < deadline)
            {
                routed = RoutePersistedAdapterEvent(*event);
                if (routed == AdapterRouteAction::Busy)
                    std::this_thread::sleep_for(
                        std::chrono::milliseconds(1));
            }
            if (routed != AdapterRouteAction::Processed ||
                m_fatalRuntimeError.load())
            {
                reason = "IB_PAPER_TERMINAL_EVENT_ROUTE_FAILED";
                return false;
            }
        }
        {
            std::lock_guard<std::recursive_mutex> quoteLock(
                m_authoritativeQuoteSendMutex);
            m_pendingAuthoritativeQuoteEvents.store(0);
        }
        m_pendingAdapterEvents.clear();
        // Routing can retire coordinator ownership or acknowledge an already
        // absorbed terminal fill. Sample the adapter once more after every
        // callback has been durably routed; transport ingress is now
        // irreversible and no producer can invalidate this composite view.
        frozen = m_adapter->GetAuthoritativeRecoveryAuditSnapshot();
    }
    if (!quoteCleanupOk) {
        reason = quoteCleanupReason.empty() ?
            "IB_PAPER_MARKET_DATA_CANCEL_FAILED" : quoteCleanupReason;
        return false;
    }
    return CompleteTerminalTransportAudit(
        frozen, snapshot, terminalState, reason);
}

bool IbPaperExecutionRuntimeComposition::CommitTerminalRecoveryAudit(
    const ExecutionControlCommand& command,
    const ExecutionControlResult& audit,
    ExecutionControlResult& terminalState,
    std::string& reason)
{
    std::lock_guard<std::mutex> terminalLock(m_terminalizationMutex);
    if (!m_terminalLatchPresent || m_terminalLatchHalted || !m_adapter ||
        !m_adapter->IsTerminalTransportHalted() ||
        m_adapter->IsConnected() || !m_lifecycleGate ||
        m_lifecycleGate->ready.load() ||
        !m_lifecycleGate->terminalControlOnly.load() || m_polling.load())
    {
        reason = "IB_PAPER_TERMINAL_COMMIT_BOUNDARY_INVALID";
        return false;
    }
    {
        std::lock_guard<std::mutex> eventDrainLock(m_adapterEventDrainMutex);
        if (!m_pendingAdapterEvents.empty())
        {
            reason = "IB_PAPER_TERMINAL_CALLBACK_QUEUE_NOT_DRAINED";
            return false;
        }
    }
    if (!PersistPaperTerminalHaltedLatch(
            command, audit, terminalState, reason))
        return false;
    const std::string expectedLatchSha = terminalState.terminalLatchSha256;
    if (!LoadPaperTerminalLatch(reason) || !m_terminalLatchHalted ||
        m_terminalResult.terminalLatchSha256 != expectedLatchSha ||
        !m_terminalResult.terminalRuntimeVerified)
    {
        if (reason.empty()) reason = "IB_PAPER_TERMINAL_LATCH_VERIFY_FAILED";
        return false;
    }
    terminalState = m_terminalResult;
    terminalState.terminalReplay = false;
    NotifyTestStage("paper_terminal_latch_committed");
    reason.clear();
    return true;
}
// RecoveryAuditOwner never reconnects on its RPC thread. The broker poll
// thread closes the lifecycle gate, drains one final callback boundary, and
// completes any live post-fill refresh before starting the fresh-epoch audit.
bool IbPaperExecutionRuntimeComposition::DriveRecoveryAuditReconnect()
{
    {
        std::lock_guard<std::mutex> lifecycleLock(m_lifecycleStateMutex);
        m_lifecycleGate->ready.store(false);
    }
    NotifyTestStage("before_recovery_audit_reconnect_drain");
    const bool drained = DrainAdapterEvents(0);
    if (m_fatalRuntimeError.load()) return false;
    if (m_reconnectPending.load()) return true;
    const bool livePostFillPending =
        m_postFillRiskRefreshPending.load() ||
        (m_adapter &&
         m_adapter->HasPendingLivePostFillRiskReconciliation());
    if (livePostFillPending)
    {
        NotifyTestStage("recovery_audit_reconnect_deferred_post_fill");
        DrivePostFillRiskRefresh();
        if (!drained && !m_fatalRuntimeError.load())
            std::this_thread::sleep_for(std::chrono::milliseconds(10));
        return !m_fatalRuntimeError.load();
    }
    std::string reconnectReason;
    if (!BeginBrokerReconnect(reconnectReason, true))
    {
        // The adapter may publish a fill between the outer observation and
        // BeginBrokerReconnect's own closed-gate check. Retry that seam via
        // the same post-fill state machine instead of making it fatal.
        if (reconnectReason ==
            "IB_PAPER_BROKER_DISCONNECTED_DURING_POST_FILL_RECONCILIATION")
        {
            NotifyTestStage("recovery_audit_reconnect_deferred_post_fill");
            DrivePostFillRiskRefresh();
            return !m_fatalRuntimeError.load();
        }
        MarkFatalRuntimeError(reconnectReason.empty() ?
            "IB_RECOVERY_AUDIT_RECONNECT_FAILED" : reconnectReason);
        return false;
    }
    m_recoveryAuditReconnectRequested.store(false);
    return true;
}
