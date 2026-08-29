#include "ib_paper_execution_runtime_internal.h"

#include <chrono>
#include <thread>

using namespace ib_paper_execution_runtime_internal;

bool IbPaperExecutionRuntimeComposition::CheckQuoteAdmissionPreflight(
    std::string& reason, std::uint64_t expectedConnectionEpoch)
{
    reason.clear();
    if (!m_adapter->IsConnected() ||
        (expectedConnectionEpoch != 0 &&
         m_adapter->GetConnectionEpoch() != expectedConnectionEpoch))
    {
        reason = "IB_PAPER_BROKER_CONNECTION_CHANGED_BEFORE_QUOTES";
        return false;
    }
    if (!RequiresCashMarketDataFarm(m_config.quoteContracts)) return true;
    DrainAdapterEvents(0);
    if (HasFatalRuntimeError(&reason)) return false;
    if (!m_adapter->IsEventStreamAuthoritative())
    {
        reason = "IB_PAPER_EVENT_STREAM_OVERFLOW";
        return false;
    }
    if (HasPendingAdapterEvents())
    {
        reason = "IB_PAPER_RUNTIME_EVENT_DRAIN_BUSY";
        MarkFatalRuntimeError(reason);
        return false;
    }
    if (!m_startupMarketDataFarmRestored.load() ||
        m_startupMarketDataFarmWaiting.load() ||
        m_startupMarketDataFarmEpoch.load() == 0 ||
        m_startupMarketDataFarmEpoch.load() !=
            (expectedConnectionEpoch != 0 ? expectedConnectionEpoch :
             m_adapter->GetConnectionEpoch()))
        reason = "IB_PAPER_MARKET_DATA_FARM_NOT_READY";
    return reason.empty();
}

bool IbPaperExecutionRuntimeComposition::SettleQuoteAdmission(
    std::string& reason, std::uint64_t expectedConnectionEpoch)
{
    const std::chrono::steady_clock::time_point deadline =
        std::chrono::steady_clock::now() + std::chrono::milliseconds(
            kMarketDataAdmissionStabilityWindowMs);
    for (;;) {
        DrainAdapterEvents(0);
        if (HasFatalRuntimeError(&reason)) return false;
        if (m_adapter->EventIngressAdmissionFailed()) {
            DrainAdapterEvents(0);
            if (HasFatalRuntimeError(&reason)) return false;
            reason = "IB_PAPER_QUOTE_ADMISSION_CALLBACK_UNSAFE";
            return false;
        }
        if (!m_adapter->IsConnected() ||
            (expectedConnectionEpoch != 0 &&
             m_adapter->GetConnectionEpoch() != expectedConnectionEpoch)) {
            reason = "IB_PAPER_BROKER_CONNECTION_CHANGED_BEFORE_QUOTES";
            return false;
        }
        if (std::chrono::steady_clock::now() >= deadline) break;
        std::this_thread::sleep_for(std::chrono::milliseconds(5));
    }
    return true;
}

bool IbPaperExecutionRuntimeComposition::DisconnectAndDrainBoundaryEvents(
    std::string& reason)
{
    // Disconnect clears the adapter post-fill set; capture it before crossing.
    bool liveFill = m_postFillRiskRefreshPending.load() ||
        (m_adapter && m_adapter->HasPendingLivePostFillRiskReconciliation());
    if (m_adapter) m_adapter->Disconnect();
    bool overflow = false;
    bool marketData10197 = false;
    IBEvent event;
    while (m_adapter && m_adapter->TryDequeueEvent(event))
    {
        if (event.type == IBEventType::EventQueueOverflow) overflow = true;
        if (IsCurrentEpochMarketData10197(event, m_adapter.get()))
            marketData10197 = true;
        if (m_adapter->HasPendingLivePostFillRiskReconciliation())
            liveFill = true;
        // Preserve 10197 as session-wide realtime-data evidence and fail closed.
        if ((!liveFill && !overflow && !marketData10197) ||
            !IsPersistedBrokerCallback(event.type))
            continue;
        if (!PersistBrokerCallback(event, nullptr))
        {
            reason = "OMS_BROKER_EVENT_WRITE_FAILED";
            return false;
        }
    }
    if (overflow)
    {
        reason = "IB_PAPER_EVENT_STREAM_OVERFLOW";
        return false;
    }
    if (marketData10197)
    {
        reason = "IB_PAPER_REALTIME_MARKET_DATA_UNAVAILABLE_10197";
        return false;
    }
    if (liveFill)
    {
        reason = "IB_PAPER_BROKER_DISCONNECTED_DURING_POST_FILL_RECONCILIATION";
        return false;
    }
    return true;
}

IbPaperExecutionRuntimeComposition::AdapterControlAction
IbPaperExecutionRuntimeComposition::HandleMarketDataFarmControl(
    int controlErrorCode, std::uint64_t eventEpoch)
{
    if (controlErrorCode == 2119)
    {
        const bool farmWasRestored = m_startupMarketDataFarmRestored.load();
        m_startupMarketDataFarmWaiting.store(true);
        // A warning invalidates the lease; the next 2104 must republish it.
        m_startupMarketDataFarmRestored.store(false);
        m_startupMarketDataFarmEpoch.store(0);
        NotifyTestStage("broker_startup_market_data_farm_waiting");
        if (farmWasRestored)
        {
            bool quoteActive = false;
            {
                std::lock_guard<std::recursive_mutex> quoteLock(
                    m_authoritativeQuoteSendMutex);
                quoteActive = m_quoteSubscriptions != nullptr;
            }
            if (quoteActive)
                MarkFatalRuntimeError(
                    "IB_PAPER_MARKET_DATA_FARM_LOST_DURING_REFRESH");
        }
        return AdapterControlAction::Consumed;
    }
    if (controlErrorCode == 2104)
    {
        m_startupMarketDataFarmWaiting.store(false);
        m_startupMarketDataFarmRestored.store(true);
        m_startupMarketDataFarmEpoch.store(eventEpoch);
        NotifyTestStage("broker_startup_market_data_farm_restored");
        return AdapterControlAction::Consumed;
    }
    return AdapterControlAction::Route;
}
