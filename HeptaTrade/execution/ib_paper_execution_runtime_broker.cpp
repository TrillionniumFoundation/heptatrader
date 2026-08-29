#include "ib_paper_execution_runtime_internal.h"

#include <algorithm>
#include <cctype>
#include <chrono>
#include <cmath>
#include <limits>
#include <map>
#include <thread>

using namespace ib_paper_execution_runtime_internal;

IbPaperExecutionRuntimeComposition::AdapterControlAction
IbPaperExecutionRuntimeComposition::HandleStartupBrokerControl(
    const IBEvent& event, int controlErrorCode)
{
    // Broker connectivity/data controls are meaningful only in the adapter
    // epoch that emitted them.  A delayed callback from a prior socket must
    // be routed for durable audit, but must not reopen (or close) the current
    // startup gate.
    if (!IsCurrentBrokerEpoch(m_adapter.get(), event.connectionEpoch))
        return AdapterControlAction::Route;
    if (controlErrorCode == 2110 || controlErrorCode == 1100)
    {
        if (m_startupWaitingForUpstream.load())
        {
            m_startupUpstreamUnavailable.store(true);
            m_startupUpstreamRestored.store(false);
            NotifyTestStage("broker_startup_upstream_unavailable");
        }
        else
            MarkFatalRuntimeError("IB_PAPER_STARTUP_UPSTREAM_LOST_DURING_REFRESH");
        return AdapterControlAction::Consumed;
    }
    if (controlErrorCode == 1101)
    {
        // 1101 explicitly says market data was lost.  Invalidate even a
        // pre-admission 2104 witness (there may be no quote cycle yet); the
        // next formal request must wait for a fresh 2104 in this epoch.
        if (RequiresCashMarketDataFarm(m_config.quoteContracts) &&
            IsCurrentBrokerEpoch(m_adapter.get(), event.connectionEpoch))
        {
            m_startupMarketDataFarmWaiting.store(true);
            m_startupMarketDataFarmRestored.store(false);
            m_startupMarketDataFarmEpoch.store(0);
        }
        bool quoteActive = false;
        if (!m_startupWaitingForUpstream.load() &&
            IsCurrentBrokerEpoch(m_adapter.get(), event.connectionEpoch))
        {
            std::lock_guard<std::recursive_mutex> quoteLock(
                m_authoritativeQuoteSendMutex);
            quoteActive = HasActiveAuthoritativeQuoteCycle(
                m_quoteSubscriptions.get());
        }
        if (quoteActive)
        {
            NotifyTestStage(
                "broker_startup_market_data_lost_during_refresh");
            PersistAndMarkFatalBrokerControl(event,
                "IB_PAPER_STARTUP_MARKET_DATA_LOST_DURING_REFRESH");
        }
        else if (m_startupWaitingForUpstream.load())
        {
            m_startupUpstreamUnavailable.store(false);
            m_startupUpstreamRestored.store(true);
            NotifyTestStage("broker_startup_upstream_restored");
        }
        return AdapterControlAction::Consumed;
    }
    if (controlErrorCode == 1102)
    {
        if (m_startupWaitingForUpstream.load())
        {
            m_startupUpstreamUnavailable.store(false);
            m_startupUpstreamRestored.store(true);
            NotifyTestStage("broker_startup_upstream_restored");
        }
        return AdapterControlAction::Consumed;
    }
    if (controlErrorCode == 504 || controlErrorCode == 509 ||
        controlErrorCode == 1300)
    {
        MarkFatalRuntimeError("IB_PAPER_STARTUP_BROKER_CONNECTION_UNAVAILABLE");
        return AdapterControlAction::Consumed;
    }
    return AdapterControlAction::Route;
}

bool IbPaperExecutionRuntimeComposition::StopQuoteSubscriptions(
    std::string* reason)
{
    // Do not take the admission mutex here.  This function is also called
    // from RoutePendingAdapterEvents(), while m_adapterEventDrainMutex is
    // held.  StartQuoteSubscriptions takes the admission mutex before its
    // final drain, so waiting for admission here would create the cycle
    // event-drain -> admission -> quote versus admission -> event-drain.
    // The quote-send mutex is the ownership boundary: a concurrent stop can
    // safely invalidate the published set and the dispatch pass will observe
    // stateRejected and fail closed.
    std::lock_guard<std::recursive_mutex> lock(m_authoritativeQuoteSendMutex);
    if (reason) reason->clear();
    if (!m_quoteSubscriptions) return true;
    const IBAuthoritativeQuoteSubscriptionHealth health =
        m_quoteSubscriptions->GetHealth();
    bool cancellationFailed = false;
    // A disconnected transport owns no cancellable request.  In that case
    // teardown itself is the cleanup boundary; do not turn the original
    // connection/fatal reason into a spurious cancel failure.
    if (m_adapter && m_adapter->IsConnected())
    {
        for (std::map<std::string, IBAuthoritativeQuoteContractHealth>::const_iterator it =
                 health.contracts.begin(); it != health.contracts.end(); ++it)
        {
            if (it->second.active && it->second.dispatchAccepted &&
                it->second.requestId > 0)
            {
                if (!m_adapter->CancelMktData(it->second.requestId))
                    cancellationFailed = true;
            }
        }
    }
    m_quoteSubscriptions->AbortCycle(health.generation);
    m_authoritativeSnapshots.InvalidateQuotes(
        NowEpochMs(), "ib.quote_subscriptions_stopped");
    m_quoteSubscriptions.reset();
    if (cancellationFailed)
    {
        if (reason) *reason = "IB_PAPER_MARKET_DATA_CANCEL_FAILED";
        return false;
    }
    return true;
}

bool IbPaperExecutionRuntimeComposition::WaitForStartupUpstream(
    std::string& reason,
    std::chrono::steady_clock::time_point deadline)
{
    if (AbortStartupIfCancelled(reason)) return false;
    const bool requiresCashMarketDataFarm =
        RequiresCashMarketDataFarm(m_config.quoteContracts);
    const int fastPathDelayMs = std::max(
        100, std::min(3000, m_config.reconnectTimeoutMs / 20));
    const std::chrono::steady_clock::time_point fastPathReadyAt =
        std::chrono::steady_clock::now() +
        std::chrono::milliseconds(fastPathDelayMs);
    const int marketDataWarmupDelayMs = std::max(
        100, std::min(3000, m_config.readinessTimeoutMs / 4));
    const std::chrono::steady_clock::time_point marketDataReadyAt =
        fastPathReadyAt + std::chrono::milliseconds(marketDataWarmupDelayMs);
    // A positive 2104 callback is a readiness witness, not an atomic lease:
    // the same farm can report 2119 immediately afterwards in this epoch.
    // Require a short, bounded quiet interval before formal ReqMktData.  The
    // admission fence still closes the final callback/send hand-off race.
    const std::chrono::milliseconds cashFarmStabilityWindow(
        kMarketDataAdmissionStabilityWindowMs);
    std::chrono::steady_clock::time_point cashFarmStableSince;
    while (std::chrono::steady_clock::now() < deadline) {
        if (AbortStartupIfCancelled(reason)) return false;
        DrainAdapterEvents(10);
        if (HasFatalRuntimeError(&reason) || !m_adapter->IsConnected()) {
            m_startupBrokerPhase.store(false);
            m_startupWaitingForUpstream.store(false);
            if (!HasFatalRuntimeError(&reason))
                reason = "IB_PAPER_STARTUP_BROKER_CONNECTION_CLOSED";
            std::string boundaryReason;
            if (!DisconnectAndDrainBoundaryEvents(boundaryReason) &&
                !boundaryReason.empty()) reason = boundaryReason;
            return false;
        }
        const std::chrono::steady_clock::time_point now =
            std::chrono::steady_clock::now();
        const bool upstreamReady = m_startupUpstreamRestored.load() ||
            (!m_startupUpstreamUnavailable.load() && now >= fastPathReadyAt);
        // CASH market data has no pre-readiness request path.  The first and
        // every subsequent ReqMktData must be ordered after a positive CASH-farm
        // 2104 from this connection epoch.  Keep the bounded quiet interval
        // so a same-epoch 2119 that follows 2104 invalidates the lease before
        // formal quote admission.
        const bool cashFarmReady =
            m_startupMarketDataFarmRestored.load() &&
            !m_startupMarketDataFarmWaiting.load() &&
            m_startupMarketDataFarmEpoch.load() != 0 &&
            m_startupMarketDataFarmEpoch.load() ==
                m_adapter->GetConnectionEpoch();
        if (requiresCashMarketDataFarm) {
            if (!cashFarmReady)
                cashFarmStableSince =
                    std::chrono::steady_clock::time_point();
            else if (cashFarmStableSince ==
                     std::chrono::steady_clock::time_point())
                cashFarmStableSince = now;
        }
        const bool marketDataReady = requiresCashMarketDataFarm ?
            (cashFarmReady &&
             cashFarmStableSince != std::chrono::steady_clock::time_point() &&
             now - cashFarmStableSince >= cashFarmStabilityWindow) :
            (!m_startupMarketDataFarmWaiting.load() && now >= marketDataReadyAt);
        if (upstreamReady && marketDataReady) {
            m_startupWaitingForUpstream.store(false);
            NotifyTestStage("broker_startup_upstream_ready");
            return true;
        }
        std::this_thread::sleep_for(std::chrono::milliseconds(1));
    }
    m_startupBrokerPhase.store(false);
    m_startupWaitingForUpstream.store(false);
    if (m_startupUpstreamUnavailable.load() &&
             !m_startupUpstreamRestored.load())
        reason = "IB_PAPER_STARTUP_UPSTREAM_TIMEOUT";
    else if (requiresCashMarketDataFarm &&
             (m_startupMarketDataFarmWaiting.load() ||
              !m_startupMarketDataFarmRestored.load()))
        reason = "IB_PAPER_STARTUP_MARKET_DATA_FARM_TIMEOUT";
    else
        reason = "IB_PAPER_STARTUP_UPSTREAM_TIMEOUT";
    std::string boundaryReason;
    if (!DisconnectAndDrainBoundaryEvents(boundaryReason) &&
        !boundaryReason.empty()) reason = boundaryReason;
    return false;
}

bool IbPaperExecutionRuntimeComposition::BeginBrokerReconnect(
    std::string& reason, bool recoveryAudit)
{
    (void)recoveryAudit;
    if (m_reconnectPending.load())
    {
        reason.clear();
        return true;
    }
    if (!m_adapter || !m_lifecycleGate || !m_policyAuthority ||
        !m_coordinator)
    {
        reason = "IB_PAPER_BROKER_RECONNECT_RUNTIME_UNREADY";
        return false;
    }
    {
        // Close the mutation gate before cancelling subscriptions or attempting
        // any broker IO. The service identity remains stable, but every RPC
        // other than identity probing observes EXECUTION_SERVICE_NOT_READY.
        std::lock_guard<std::mutex> lifecycleLock(m_lifecycleStateMutex);
        m_lifecycleGate->ready.store(false);
    }
    const bool livePostFillPending =
        m_postFillRiskRefreshPending.load() ||
        (m_adapter &&
         m_adapter->HasPendingLivePostFillRiskReconciliation());
    // Disconnect itself invalidates coherent risk snapshots.  That broad
    // watermark mismatch is repaired by reconnect and must not masquerade as
    // an in-flight post-fill operation.  Only a real live fill owned by the
    // runtime/adapter blocks the disconnect boundary.
    if (livePostFillPending)
        return FailBrokerReconnect(reason,
            "IB_PAPER_BROKER_DISCONNECTED_DURING_POST_FILL_RECONCILIATION",
            false);
    // This coordinator fence closes the narrow race for a request that passed
    // the IPC lifecycle check immediately before the transport-close callback.
    // It waits for any in-flight coordinator operation; a completed send leaves
    // an owner and therefore fails closed, while a later request observes the
    // transient mutation block and cannot reach broker IO.
    if (!m_coordinator->BeginBrokerReconnectFence(reason))
        return FailBrokerReconnect(reason, reason, false);
    if (!StopQuoteSubscriptions(&reason)) {
        DisconnectAndDrainBoundaryEvents(reason);
        m_reconnectTransportConnected.store(false);
        return false;
    }
    m_reconnectTransportConnected.store(false);
    if (!DisconnectAndDrainBoundaryEvents(reason))
        return FailBrokerReconnect(reason, reason, false);
    m_reconnectUpstreamUnavailable.store(false);
    m_reconnectUpstreamRestored.store(false);
    // Reuse the startup market-data admission state for this mutually
    // exclusive reconnect phase; it is reset before the new API epoch.
    m_startupMarketDataFarmWaiting.store(false);
    m_startupMarketDataFarmRestored.store(false);
    m_startupMarketDataFarmEpoch.store(0);
    m_reconnectRefreshDispatched.store(false);
    m_reconnectRiskRefreshDispatched.store(false);
    m_reconnectConnectionEpoch.store(0);
    m_reconnectAttempt = 0;
    const std::chrono::steady_clock::time_point now =
        std::chrono::steady_clock::now();
    m_reconnectDeadline = now +
        std::chrono::milliseconds(m_config.reconnectTimeoutMs);
    m_reconnectNextAttemptAt = now;
    m_reconnectPending.store(true);
    NotifyTestStage("broker_reconnect_scheduled");
    reason.clear();
    return true;
}

bool IbPaperExecutionRuntimeComposition::ReconnectAuthoritativeStateReady(
    std::string& reason) const
{
    std::lock_guard<std::recursive_mutex> lock(
        m_authoritativeQuoteSendMutex);
    if (!m_adapter || !m_quoteSubscriptions)
    {
        reason = "IB_PAPER_BROKER_RECONNECT_SNAPSHOTS_UNREADY";
        return false;
    }
    const std::uint64_t connectionEpoch = m_adapter->GetConnectionEpoch();
    const IBAuthoritativeRecoveryAuditSnapshot recovery =
        m_adapter->GetAuthoritativeRecoveryAuditSnapshot();
    const IBAuthoritativeRiskSnapshot& risk = recovery.risk;
    const IBAuthoritativeCorrelationSnapshot& active = recovery.active;
    const IBAuthoritativeTerminalCorrelationSnapshot& terminal =
        recovery.terminal;
    const IBAuthoritativeQuoteSubscriptionHealth quotes =
        m_quoteSubscriptions->GetHealth();
    const bool complete = connectionEpoch != 0 &&
        risk.connectionEpoch == connectionEpoch &&
        risk.accountComplete && risk.positionsComplete && risk.fxCashComplete &&
        risk.accountGeneration != 0 && risk.positionsGeneration != 0 &&
        risk.fxCashGeneration != 0 &&
        active.connectionEpoch == connectionEpoch && active.complete &&
        active.generation != 0 &&
        terminal.connectionEpoch == connectionEpoch && terminal.complete &&
        terminal.generation != 0 &&
        quotes.connectionEpoch == connectionEpoch && quotes.complete &&
        quotes.generation != 0 && recovery.barrierComplete &&
        !recovery.postFillRiskReconciliationPending &&
        recovery.terminalExposureGeneration <=
            recovery.riskAbsorbedExposureGeneration &&
        recovery.riskAbsorbedExposureGeneration ==
            recovery.exposureGeneration;
    if (!complete)
    {
        reason = !risk.reasonCode.empty() ? risk.reasonCode :
            (!active.reasonCode.empty() ? active.reasonCode :
             (!terminal.reasonCode.empty() ? terminal.reasonCode :
              "IB_PAPER_BROKER_RECONNECT_SNAPSHOTS_UNREADY"));
        return false;
    }
    // Disconnect invalidates the adapter's per-order pre-fill risk baselines.
    // Until those baselines can be reconstructed from exact broker/OMS
    // evidence, an in-flight order cannot safely cross a reconnect boundary:
    // a later fill would have no trustworthy starting position.  Escalate to
    // operator recovery instead of reopening mutation authority.
    if (!active.activeOrderIds.empty())
    {
        reason = "IB_PAPER_BROKER_RECONNECT_ACTIVE_ORDERS_UNSAFE";
        return false;
    }
    reason.clear();
    return true;
}

bool IbPaperExecutionRuntimeComposition::DriveBrokerReconnect(
    std::string& reason)
{
    if (!m_reconnectPending.load())
    {
        reason.clear();
        return true;
    }
    const std::chrono::steady_clock::time_point now =
        std::chrono::steady_clock::now();
    if (now >= m_reconnectDeadline)
    {
        reason = "IB_PAPER_BROKER_RECONNECT_EXHAUSTED";
        return false;
    }
    if (now < m_reconnectNextAttemptAt)
    {
        reason.clear();
        return true;
    }

    ++m_reconnectAttempt;
    NotifyTestStage("before_broker_reconnect_attempt");
    std::map<std::string, double> observedBalances;
    for (std::map<std::string, IbPaperFxCashBaseline>::const_iterator it =
             m_config.fxCashBaselines.begin();
         it != m_config.fxCashBaselines.end(); ++it)
        observedBalances[it->first] = it->second.observedCashBalance;
    if (!m_adapter->PrepareReconnectCashAttestation(
            observedBalances, reason))
        return false;
    if (!m_adapter->Connect())
    {
        const int backoffMs = 100 +
            100 * std::min(m_reconnectAttempt, 19);
        m_reconnectNextAttemptAt = std::min(
            m_reconnectDeadline,
            std::chrono::steady_clock::now() +
                std::chrono::milliseconds(backoffMs));
        reason.clear();
        return true;
    }
    return DriveBrokerReconnectConnected(reason);
}

bool IbPaperExecutionRuntimeComposition::FailBrokerReconnect(
    std::string& reason, const std::string& primaryReason, bool disconnect)
{
    // Every terminal reconnect path owns the same cleanup boundary.  Leaving
    // a formal ReqMktData subscription attached while AdapterLoop publishes a
    // fatal state would make the broker request outlive the epoch that proved
    // its snapshots, and would also leave stale quote state available to a
    // later recovery attempt.
    std::string cleanupReason;
    const bool cleanupOk = StopQuoteSubscriptions(&cleanupReason);
    std::string boundaryReason;
    if (disconnect && m_adapter) {
        DisconnectAndDrainBoundaryEvents(boundaryReason);
    }
    if (disconnect) m_reconnectTransportConnected.store(false);
    m_reconnectRefreshDispatched.store(false);
    m_reconnectRiskRefreshDispatched.store(false);
    if (!boundaryReason.empty())
        reason = boundaryReason;
    else if (!cleanupOk && !cleanupReason.empty())
        reason = cleanupReason;
    else
        reason = primaryReason;
    return false;
}

bool IbPaperExecutionRuntimeComposition::DriveBrokerReconnectConnected(
    std::string& reason)
{
    m_reconnectTransportConnected.store(true);
    m_reconnectUpstreamUnavailable.store(false);
    m_reconnectUpstreamRestored.store(false);
    m_startupMarketDataFarmWaiting.store(false);
    m_startupMarketDataFarmRestored.store(false);
    m_startupMarketDataFarmEpoch.store(0);
    m_reconnectRefreshDispatched.store(false);
    m_reconnectRiskRefreshDispatched.store(false);
    m_reconnectConnectionEpoch.store(m_adapter->GetConnectionEpoch());
    if (m_reconnectConnectionEpoch.load() == 0)
        return FailBrokerReconnect(
            reason, "IB_PAPER_BROKER_RECONNECT_EPOCH_INVALID");
    const int fastPathDelayMs = std::max(
        100, std::min(3000, m_config.reconnectTimeoutMs / 20));
    m_reconnectFastPathReadyAt = std::chrono::steady_clock::now() +
        std::chrono::milliseconds(fastPathDelayMs);
    const int marketDataWarmupDelayMs = std::max(
        100, std::min(3000, m_config.readinessTimeoutMs / 4));
    const std::chrono::steady_clock::time_point marketDataWarmupReadyAt =
        m_reconnectFastPathReadyAt +
        std::chrono::milliseconds(marketDataWarmupDelayMs);
    // Keep the reconnect gate symmetric with startup.  A 2104 callback is a
    // positive witness, but it is not an atomic lease; require a bounded quiet
    // interval so a same-epoch 2119 is observed before formal ReqMktData.
    const std::chrono::milliseconds cashFarmStabilityWindow(
        kMarketDataAdmissionStabilityWindowMs);
    std::chrono::steady_clock::time_point cashFarmStableSince;
    const bool requiresCashMarketDataFarm =
        RequiresCashMarketDataFarm(m_config.quoteContracts);
    std::chrono::steady_clock::time_point snapshotDeadline =
        m_reconnectDeadline;
    while (m_polling.load() &&
           std::chrono::steady_clock::now() < m_reconnectDeadline)
    {
        DrainAdapterEvents(10);
        if (m_fatalRuntimeError.load())
        {
            std::string fatalReason;
            HasFatalRuntimeError(&fatalReason);
            return FailBrokerReconnect(reason, fatalReason.empty() ?
                "IB_PAPER_BROKER_RECONNECT_CALLBACK_FATAL" : fatalReason);
        }
        if (requiresCashMarketDataFarm &&
            m_startupMarketDataFarmWaiting.load() &&
            m_reconnectRefreshDispatched.load())
        {
            return FailBrokerReconnect(reason,
                "IB_PAPER_BROKER_RECONNECT_MARKET_DATA_FARM_LOST_DURING_REFRESH");
        }
        if (m_reconnectPending.load() && !m_adapter->IsConnected())
            return FailBrokerReconnect(
                reason, "IB_PAPER_BROKER_RECONNECT_CLOSED_DURING_REFRESH");
        if (m_adapter->GetConnectionEpoch() !=
                m_reconnectConnectionEpoch.load())
            return FailBrokerReconnect(
                reason, "IB_PAPER_BROKER_RECONNECT_EPOCH_CHANGED");
        const std::chrono::steady_clock::time_point observedNow =
            std::chrono::steady_clock::now();
        const bool upstreamReady = m_reconnectUpstreamRestored.load() ||
            (!m_reconnectUpstreamUnavailable.load() &&
             observedNow >= m_reconnectFastPathReadyAt);
        bool marketDataFarmReady = false;
        if (!AdvanceReconnectMarketDataGate(
                requiresCashMarketDataFarm, upstreamReady, observedNow,
                marketDataWarmupReadyAt, cashFarmStabilityWindow,
                cashFarmStableSince, reason,
                marketDataFarmReady))
            return FailBrokerReconnect(reason, reason.empty() ?
                "IB_PAPER_BROKER_RECONNECT_MARKET_DATA_FARM_TIMEOUT" : reason);
        bool retryScheduled = false;
        if (!DispatchReconnectRefreshIfReady(
                upstreamReady, marketDataFarmReady, observedNow,
                snapshotDeadline, reason, retryScheduled))
            return false;
        if (retryScheduled) return true;
        if (!m_reconnectRefreshDispatched.load())
        {
            std::this_thread::sleep_for(std::chrono::milliseconds(1));
            continue;
        }
        if (m_reconnectUpstreamUnavailable.load())
            return FailBrokerReconnect(
                reason, "IB_PAPER_BROKER_UPSTREAM_LOST_DURING_REFRESH");
        if (!m_reconnectRiskRefreshDispatched.load() &&
            !RequestReconnectRiskRefresh(reason))
            return FailBrokerReconnect(reason, reason.empty() ?
                "IB_PAPER_BROKER_RECONNECT_RISK_REFRESH_REJECTED" : reason);
        if (!m_reconnectRiskRefreshDispatched.load())
        {
            std::this_thread::sleep_for(std::chrono::milliseconds(1));
            continue;
        }
        if (ReconnectAuthoritativeStateReady(reason))
        {
            if (!PersistFxCashRestartCheckpoint(reason))
                return FailBrokerReconnect(reason, reason.empty() ?
                    "IB_PAPER_BROKER_RECONNECT_CHECKPOINT_FAILED" : reason);
            std::size_t affected = 0;
            if (!m_policyAuthority->ReconcileAuthoritativeState(
                    affected, reason) ||
                !RebuildRecentBrokerOrders(reason) ||
                !m_coordinator->EndBrokerReconnectFence(reason) ||
                m_coordinator->IsMutationBlocked(&reason))
            {
                if (reason.empty())
                    reason = "IB_PAPER_BROKER_RECONNECT_RECONCILIATION_FAILED";
                return FailBrokerReconnect(reason, reason);
            }
            {
                std::lock_guard<std::mutex> lifecycleLock(
                    m_lifecycleStateMutex);
                m_reconnectPending.store(false);
                m_lifecycleGate->ready.store(true);
            }
            NotifyTestStage("broker_reconnect_complete");
            reason.clear();
            return true;
        }
        if (observedNow >= snapshotDeadline) break;
        std::this_thread::sleep_for(std::chrono::milliseconds(1));
    }
    if (!m_reconnectRefreshDispatched.load())
    {
        if (m_reconnectUpstreamUnavailable.load() &&
            !m_reconnectUpstreamRestored.load())
            reason = "IB_PAPER_BROKER_RECONNECT_UPSTREAM_TIMEOUT";
        else if (requiresCashMarketDataFarm &&
                 (m_startupMarketDataFarmWaiting.load() ||
                  !m_startupMarketDataFarmRestored.load()))
            reason = "IB_PAPER_BROKER_RECONNECT_MARKET_DATA_FARM_TIMEOUT";
        else
            reason = "IB_PAPER_BROKER_RECONNECT_UPSTREAM_TIMEOUT";
        return FailBrokerReconnect(reason, reason);
    }
    const std::string timeoutReason = reason;
    // An accepted terminal-correlation request cannot safely be retried in
    // the same epoch. A timed-out refresh is therefore terminal.
    return FailBrokerReconnect(reason, timeoutReason.empty() ?
        "IB_PAPER_BROKER_RECONNECT_SNAPSHOTS_TIMEOUT" : timeoutReason);
}

bool IbPaperExecutionRuntimeComposition::RequestReconnectRiskRefresh(
    std::string& reason)
{
    const IBAuthoritativeRecoveryAuditSnapshot broker =
        m_adapter->GetAuthoritativeRecoveryAuditSnapshot();
    if (!broker.active.complete || !broker.terminal.complete ||
        broker.active.connectionEpoch != m_reconnectConnectionEpoch.load() ||
        broker.terminal.connectionEpoch != m_reconnectConnectionEpoch.load())
        return true;
    if (!m_adapter->ReqRecoveryAuditRiskRefresh())
    {
        reason = "IB_PAPER_BROKER_RECONNECT_RISK_REFRESH_REJECTED";
        return false;
    }
    m_reconnectRiskRefreshDispatched.store(true);
    NotifyTestStage("broker_reconnect_recovery_risk_dispatched");
    return true;
}

void IbPaperExecutionRuntimeComposition::MarkFatalRuntimeError(
    const std::string& reason)
{
    // Serialize fatal publication with the final Ready transition.  A polling
    // thread that detects a fatal event while Start() is publishing readiness
    // waits for that publication, then irreversibly closes the shared gate.
    std::lock_guard<std::mutex> lifecycleLock(m_lifecycleStateMutex);
    {
        std::lock_guard<std::mutex> lock(m_fatalMutex);
        if (m_fatalReason.empty()) m_fatalReason = reason;
    }
    m_fatalRuntimeError.store(true);
    if (m_lifecycleGate) m_lifecycleGate->ready.store(false);
    m_polling.store(false);
}

bool IbPaperExecutionRuntimeComposition::HasFatalRuntimeError(
    std::string* reason) const
{
    if (!m_fatalRuntimeError.load()) return false;
    if (reason)
    {
        std::lock_guard<std::mutex> lock(m_fatalMutex);
        *reason = m_fatalReason;
    }
    return true;
}

bool IbPaperExecutionRuntimeComposition::AuthorizeFinalBrokerSend(
    const IBFinalOrderSendContext* context,
    const IBContractLite& contract,
    const IBOrderLite& order,
    std::string* detail) const
{
    std::string reason;
    bool allowed = false;
    if (context != nullptr && context->proveFlatOnly)
    {
        allowed = context->exactReduceOnly &&
            AllowsAuthoritativeFlatten(reason);
    }
    else if (context != nullptr && context->exactReduceOnly)
    {
        allowed = AllowsAuthoritativeFlatten(reason, true);
        if (allowed)
        {
            const MarketQuoteSnapshot quote =
                AuthoritativeQuote(context->instrument);
            const bool quoteUnchanged = quote.IsFresh(NowEpochMs()) &&
                context->quoteSubscriptionId == quote.subscriptionId &&
                context->quoteObservedAtMs == quote.observedAtMs &&
                context->quoteStaleAfterMs == quote.staleAfterMs;
            const bool externalLimitDay =
                m_config.profile.UsesExternalLimitDay();
            const bool exactExternalQuote =
                context->quoteBid == quote.bid &&
                context->quoteAsk == quote.ask;
            const bool allowedOrder = externalLimitDay ?
                (exactExternalQuote && order.orderType == "LMT" &&
                 order.auxPrice == 0.0 && !order.outsideRth &&
                 std::isfinite(order.lmtPrice) && order.lmtPrice > 0.0 &&
                 ((order.action == "BUY" &&
                   order.lmtPrice == quote.ask) ||
                  (order.action == "SELL" &&
                   order.lmtPrice == quote.bid))) :
                (order.orderType == "MKT" && order.lmtPrice == 0.0);
            allowed = quoteUnchanged && allowedOrder;
            if (!allowed)
                reason = "IB_PAPER_FLATTEN_QUOTE_CHANGED_BEFORE_SEND";
        }
    }
    else
        allowed = AllowsBoundRiskIncreasingPlace(
            context, contract, order, reason);
    if (!allowed && detail)
    {
        *detail = reason.empty() ?
            "IB_PAPER_KILL_SWITCH_STATE_UNCERTAIN" : reason;
    }
    return allowed;
}
