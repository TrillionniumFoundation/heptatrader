#include "ib_paper_execution_runtime_internal.h"

#include <algorithm>
#include <chrono>
#include <iostream>
#include <thread>
#include <utility>

using namespace ib_paper_execution_runtime_internal;
bool IbPaperExecutionRuntimeComposition::InitializeAdapterAndConnect(
    std::string& reason,
    std::chrono::steady_clock::time_point deadline)
{
    if (AbortStartupIfCancelled(reason)) return false;
    if (m_injectedApi)
        m_adapter.reset(new HeptaIBGatewayAdapter(
            std::move(m_initialApi), m_testHooks.reconnectApiFactory));
    else
        m_adapter.reset(new HeptaIBGatewayAdapter());
    m_adapter->SetPrePlaceOrderSendCheck(
        [this](const IBFinalOrderSendContext* context,
               const IBContractLite& contract, const IBOrderLite& order,
               std::string* detail) {
        return AuthorizeFinalBrokerSend(context, contract, order, detail);
    });
    HeptaIBConfig adapterConfig;
    adapterConfig.host = m_config.profile.host;
    adapterConfig.port = m_config.profile.port;
    adapterConfig.clientId = m_config.profile.clientId;
    adapterConfig.account = m_config.profile.account;
    adapterConfig.readOnly = false;
    adapterConfig.observabilityLogPath =
        m_config.stateDirectory + "/ib-observability.jsonl";
    for (std::map<std::string, InstrumentRef>::const_iterator it =
             m_config.quoteContracts.begin();
         it != m_config.quoteContracts.end(); ++it) {
        if (it->second.secType != "CASH") continue;
        const std::map<std::string, IbPaperFxCashBaseline>::const_iterator
            baseline = m_config.fxCashBaselines.find(it->first);
        if (baseline == m_config.fxCashBaselines.end()) {
            reason = "IB_FX_CASH_BASELINE_MISSING";
            return false;
        }
        adapterConfig.authoritativeCashFxContracts[it->first] = it->second;
        adapterConfig.authoritativeCashFxBaselines[it->first] =
            baseline->second.baselineCashBalance;
        adapterConfig.authoritativeCashFxStartupObservedBalances[it->first] =
            baseline->second.observedCashBalance;
    }
    adapterConfig.risk.enableOrderSubmission = true;
    adapterConfig.risk.maxOrderQuantity = m_config.profile.maxOrderQuantity;
    adapterConfig.risk.maxDailyOrders = 100000;
    adapterConfig.risk.maxPriceDeviationBps = 0.0;
    adapterConfig.risk.allowLiveTrading = false;
    adapterConfig.risk.liveKillSwitch = true;

    m_startupBrokerPhase.store(true);
    m_startupWaitingForUpstream.store(true);
    m_startupUpstreamUnavailable.store(false);
    m_startupUpstreamRestored.store(false);
    m_startupMarketDataFarmWaiting.store(false);
    m_startupMarketDataFarmRestored.store(false);
    m_startupMarketDataFarmEpoch.store(0);
    if (!m_adapter->Init(adapterConfig)) {
        m_startupBrokerPhase.store(false);
        m_startupWaitingForUpstream.store(false);
        reason = "IB_PAPER_ADAPTER_INIT_FAILED";
        return false;
    }
    int attempt = 0;
    while (std::chrono::steady_clock::now() < deadline &&
           !m_adapter->IsConnected()) {
        if (AbortStartupIfCancelled(reason)) return false;
        ++attempt;
        if (m_adapter->Connect()) break;
        if (AbortStartupIfCancelled(reason)) return false;
        const int backoffMs = 100 + 100 * std::min(attempt, 19);
        const std::chrono::steady_clock::duration remaining =
            deadline - std::chrono::steady_clock::now();
        if (remaining <= std::chrono::steady_clock::duration::zero()) break;
        std::this_thread::sleep_for(std::min(
            std::chrono::milliseconds(backoffMs),
            std::chrono::duration_cast<std::chrono::milliseconds>(remaining)));
    }
    // A successful socket connect is not market-data readiness. The startup
    // wait observes only a positive CASH-farm 2104 in this adapter epoch;
    // no market-data request is sent from this connection path.
    if (AbortStartupIfCancelled(reason)) return false;
    if (m_adapter->IsConnected()) return true;
    m_startupBrokerPhase.store(false);
    m_startupWaitingForUpstream.store(false);
    reason = "IB_PAPER_ADAPTER_CONNECT_FAILED";
    return false;
}

bool IbPaperExecutionRuntimeComposition::CleanupStartupSnapshotFailure(
    std::string& reason, const std::string& primaryReason)
{
    std::string cleanupReason;
    StopQuoteSubscriptions(&cleanupReason);
    std::string boundaryReason;
    DisconnectAndDrainBoundaryEvents(boundaryReason);
    if (!boundaryReason.empty()) cleanupReason = boundaryReason;
    reason = !cleanupReason.empty() ? cleanupReason : primaryReason;
    return false;
}

bool IbPaperExecutionRuntimeComposition::DispatchStartupRecoveryRisk(
    const IBAuthoritativeRecoveryAuditSnapshot& recovery,
    bool& dispatched, std::string& reason)
{
    const IBAuthoritativeCorrelationSnapshot& active = recovery.active;
    const IBAuthoritativeTerminalCorrelationSnapshot& terminal = recovery.terminal;
    if (dispatched || !active.complete || !terminal.complete ||
        active.connectionEpoch != m_adapter->GetConnectionEpoch() ||
        terminal.connectionEpoch != active.connectionEpoch)
        return true;
    if (!m_adapter->ReqRecoveryAuditRiskRefresh()) {
        reason = "IB_PAPER_RECOVERY_RISK_REFRESH_REJECTED";
        return false;
    }
    dispatched = true;
    NotifyTestStage("broker_startup_recovery_risk_dispatched");
    return true;
}

bool IbPaperExecutionRuntimeComposition::StartupSnapshotBarrierReady(
    const IBAuthoritativeRecoveryAuditSnapshot& recovery,
    bool recoveryRiskDispatched) const
{
    const IBAuthoritativeRiskSnapshot& risk = recovery.risk;
    const IBAuthoritativeCorrelationSnapshot& active = recovery.active;
    const IBAuthoritativeTerminalCorrelationSnapshot& terminal = recovery.terminal;
    const std::uint64_t epoch = m_adapter->GetConnectionEpoch();
    bool quotesComplete = false;
    {
        std::lock_guard<std::recursive_mutex> quoteLock(
            m_authoritativeQuoteSendMutex);
        quotesComplete = m_quoteSubscriptions &&
            m_quoteSubscriptions->IsComplete();
    }
    return risk.accountComplete && risk.positionsComplete && risk.fxCashComplete &&
        active.complete && terminal.complete && quotesComplete &&
        risk.connectionEpoch == epoch &&
        active.connectionEpoch == epoch && terminal.connectionEpoch == epoch &&
        risk.accountGeneration != 0 && risk.positionsGeneration != 0 &&
        active.generation != 0 && terminal.generation != 0 &&
        recoveryRiskDispatched && recovery.barrierComplete &&
        !recovery.postFillRiskReconciliationPending &&
        recovery.terminalExposureGeneration <= recovery.riskAbsorbedExposureGeneration &&
        recovery.riskAbsorbedExposureGeneration == recovery.exposureGeneration;
}

bool IbPaperExecutionRuntimeComposition::WaitForAuthoritativeSnapshots(
    std::string& reason,
    std::chrono::steady_clock::time_point deadline)
{
    if (AbortStartupIfCancelled(reason)) return false;
    const std::uint64_t startupConnectionEpoch = m_adapter ?
        m_adapter->GetConnectionEpoch() : 0;
    try {
        if (!StartQuoteSubscriptions(reason, startupConnectionEpoch))
            return CleanupStartupSnapshotFailure(reason, reason);
    }
    catch (...) {
        reason = "IB_PAPER_QUOTE_ADMISSION_EXCEPTION";
        return CleanupStartupSnapshotFailure(reason, reason);
    }
    if (!m_adapter->ReqAuthoritativeOpenOrders() ||
        !m_adapter->ReqTerminalCorrelations())
        return CleanupStartupSnapshotFailure(
            reason, "IB_PAPER_AUTHORITATIVE_REFRESH_REQUEST_FAILED");
    const std::chrono::steady_clock::time_point readinessDeadline = std::min(
        deadline, std::chrono::steady_clock::now() +
            std::chrono::milliseconds(m_config.readinessTimeoutMs));
    bool recoveryRiskDispatched = false;
    while (std::chrono::steady_clock::now() < readinessDeadline) {
        if (StartupCancellationRequested(reason)) {
            const std::string cancellationReason = reason;
            CleanupStartupSnapshotFailure(reason, cancellationReason);
            return false;
        }
        DrainAdapterEvents(10);
        if (HasFatalRuntimeError(&reason)) {
            const std::string fatalReason = reason;
            return CleanupStartupSnapshotFailure(reason, fatalReason);
        }
        if (RequiresCashMarketDataFarm(m_config.quoteContracts) &&
            m_startupMarketDataFarmWaiting.load())
            return CleanupStartupSnapshotFailure(
                reason, "IB_PAPER_STARTUP_MARKET_DATA_FARM_LOST_DURING_REFRESH");
        const IBAuthoritativeRecoveryAuditSnapshot recovery =
            m_adapter->GetAuthoritativeRecoveryAuditSnapshot();
        if (recovery.risk.reasonCode ==
            "IB_FX_CASH_ATTESTED_BALANCE_MISMATCH")
            return CleanupStartupSnapshotFailure(
                reason, recovery.risk.reasonCode);
        if (!DispatchStartupRecoveryRisk(
                recovery, recoveryRiskDispatched, reason))
            return CleanupStartupSnapshotFailure(reason, reason);
        if (StartupSnapshotBarrierReady(recovery, recoveryRiskDispatched))
            return true;
        std::this_thread::sleep_for(std::chrono::milliseconds(1));
    }
    const IBAuthoritativeRiskSnapshot risk =
        m_adapter->GetAuthoritativeRiskSnapshot();
    const IBAuthoritativeCorrelationSnapshot active =
        m_adapter->GetAuthoritativeCorrelationSnapshot();
    const IBAuthoritativeTerminalCorrelationSnapshot terminal =
        m_adapter->GetAuthoritativeTerminalCorrelationSnapshot();
    bool quotesComplete = false;
    {
        std::lock_guard<std::recursive_mutex> quoteLock(
            m_authoritativeQuoteSendMutex);
        quotesComplete = m_quoteSubscriptions &&
            m_quoteSubscriptions->IsComplete();
    }
    CleanupStartupSnapshotFailure(
        reason, "IB_PAPER_AUTHORITATIVE_SNAPSHOTS_TIMEOUT");
    std::clog << "IB PAPER readiness timeout"
              << " account=" << risk.accountComplete
              << " positions=" << risk.positionsComplete
              << " fx_cash=" << risk.fxCashComplete
              << " active_orders=" << active.complete
              << " terminal=" << terminal.complete
              << " quotes=" << quotesComplete
              << " risk_reason=" << risk.reasonCode
              << " active_reason=" << active.reasonCode
              << " terminal_reason=" << terminal.reasonCode << std::endl;
    return false;
}
bool IbPaperExecutionRuntimeComposition::StartAdapterAndBuildSnapshots(std::string& reason)
{
    if (AbortStartupIfCancelled(reason)) return false;
    // One startup budget spans connect, upstream/farm warm-up, and the
    // authoritative snapshot barrier; splitting functions must not multiply it.
    const std::chrono::steady_clock::time_point deadline =
        std::chrono::steady_clock::now() +
        std::chrono::milliseconds(m_config.reconnectTimeoutMs);
    if (!InitializeAdapterAndConnect(reason, deadline)) return false;
    if (!WaitForStartupUpstream(reason, deadline)) return false;
    if (!WaitForAuthoritativeSnapshots(reason, deadline)) {
        m_startupBrokerPhase.store(false);
        return false;
    }
    m_startupBrokerPhase.store(false);
    return true;
}
