#include "ib_paper_execution_runtime_internal.h"
#include <map>
#include <mutex>
#include <vector>
using namespace ib_paper_execution_runtime_internal;
class IbPaperExecutionRuntimeComposition::QuoteAdmissionTransaction
{
public:
    explicit QuoteAdmissionTransaction(HeptaIBGatewayAdapter& adapter)
        : m_adapter(adapter)
    {
        m_adapter.BeginEventIngressAdmission();
        try
        {
            m_lock = std::unique_lock<std::recursive_mutex>(
                adapter.EventIngressFence());
        }
        catch (...)
        {
            try { m_adapter.EndEventIngressAdmission(); }
            catch (...) {}
            try { m_adapter.CompleteEventIngressAdmission(); }
            catch (...) {}
            throw;
        }
    }
    ~QuoteAdmissionTransaction() { Close(); }
    void Close()
    {
        if (!m_open) return;
        m_adapter.EndEventIngressAdmission();
        if (m_lock.owns_lock()) m_lock.unlock();
        m_adapter.CompleteEventIngressAdmission();
        m_open = false;
    }

private:
    HeptaIBGatewayAdapter& m_adapter;
    std::unique_lock<std::recursive_mutex> m_lock;
    bool m_open = true;
};
bool IbPaperExecutionRuntimeComposition::PrepareQuoteSubscriptionPlan(
    std::unique_ptr<IBAuthoritativeQuoteSubscriptionSet>& subscriptions,
    IBAuthoritativeQuoteSubscriptionPlan& plan,
    std::string& reason, std::uint64_t expectedConnectionEpoch)
{
    std::lock_guard<std::recursive_mutex> quoteLock(
        m_authoritativeQuoteSendMutex);
    if (m_quoteSubscriptions)
    {
        reason = "IB_PAPER_QUOTE_SUBSCRIPTIONS_ALREADY_ACTIVE";
        return false;
    }
    if (!m_adapter->IsConnected() ||
        (expectedConnectionEpoch != 0 &&
         m_adapter->GetConnectionEpoch() != expectedConnectionEpoch) ||
        (!m_startupBrokerPhase.load() && !m_reconnectPending.load()))
    {
        reason = "IB_PAPER_BROKER_CONNECTION_CHANGED_BEFORE_QUOTES";
        return false;
    }
    if (RequiresCashMarketDataFarm(m_config.quoteContracts) &&
        (!m_startupMarketDataFarmRestored.load() ||
         m_startupMarketDataFarmWaiting.load() ||
         m_startupMarketDataFarmEpoch.load() == 0 ||
         m_startupMarketDataFarmEpoch.load() !=
             (expectedConnectionEpoch != 0 ? expectedConnectionEpoch :
              m_adapter->GetConnectionEpoch())))
    {
        reason = "IB_PAPER_MARKET_DATA_FARM_NOT_READY";
        return false;
    }
    subscriptions.reset(new IBAuthoritativeQuoteSubscriptionSet(
        m_authoritativeSnapshots, 1000001));
    if (!subscriptions->Configure(
            m_config.quoteContracts, m_config.primaryQuoteInstrument, reason))
    {
        if (reason.empty()) reason = "IB_PAPER_QUOTE_CONFIGURATION_INVALID";
        return false;
    }
    const std::uint64_t observedAtMs = NowEpochMs();
    plan = subscriptions->BeginCycle(
        m_adapter->GetConnectionEpoch(), 1, observedAtMs);
    if (!plan.accepted)
    {
        reason = plan.reasonCode.empty() ?
            "IB_PAPER_QUOTE_CYCLE_START_FAILED" : plan.reasonCode;
        return false;
    }
    return true;
}
void IbPaperExecutionRuntimeComposition::AbortUnpublishedQuoteCycle(
    std::unique_ptr<IBAuthoritativeQuoteSubscriptionSet>& subscriptions,
    std::uint64_t generation)
{
    if (!subscriptions) return;
    subscriptions->AbortCycle(generation);
    m_authoritativeSnapshots.InvalidateQuotes(
        NowEpochMs(), "ib.quote_subscriptions_admission_aborted");
    subscriptions.reset();
}

bool IbPaperExecutionRuntimeComposition::PrepareQuoteAdmissionPass(
    QuoteAdmissionTransaction& transaction,
    std::unique_ptr<IBAuthoritativeQuoteSubscriptionSet>& subscriptions,
    const IBAuthoritativeQuoteSubscriptionPlan& plan,
    std::string& reason, std::uint64_t expectedConnectionEpoch)
{
    (void)transaction;
    CollectAdapterIngressEvents(-1);
    // Never publish while a callback queued across preflight/admission remains.
    if (HasFatalRuntimeError(&reason)) return false;
    const bool pending = HasPendingAdapterEvents();
    const bool callbackFailure = m_adapter->EventIngressAdmissionFailed();
    if (!m_adapter->IsEventStreamAuthoritative())
    {
        reason = "IB_PAPER_EVENT_STREAM_OVERFLOW";
        return false;
    }
    if (pending || callbackFailure)
    {
        reason = "IB_PAPER_QUOTE_ADMISSION_CALLBACK_UNSAFE";
        return false;
    }
    const std::uint64_t currentEpoch = m_adapter->GetConnectionEpoch();
    if (!m_adapter->IsConnected() ||
        plan.connectionEpoch == 0 || currentEpoch != plan.connectionEpoch ||
        (expectedConnectionEpoch != 0 && currentEpoch != expectedConnectionEpoch) ||
        (!m_startupBrokerPhase.load() && !m_reconnectPending.load()))
    {
        reason = "IB_PAPER_BROKER_CONNECTION_CHANGED_BEFORE_QUOTES";
        return false;
    }
    if (RequiresCashMarketDataFarm(m_config.quoteContracts) &&
        (!m_startupMarketDataFarmRestored.load() ||
         m_startupMarketDataFarmWaiting.load() ||
         m_startupMarketDataFarmEpoch.load() == 0 ||
         m_startupMarketDataFarmEpoch.load() !=
             (expectedConnectionEpoch != 0 ? expectedConnectionEpoch :
              m_adapter->GetConnectionEpoch())))
    {
        reason = "IB_PAPER_MARKET_DATA_FARM_NOT_READY";
        return false;
    }
    // Recheck immediately before publishing; ReqMktData repeats this gate.
    if (m_adapter->EventIngressAdmissionFailed())
    {
        reason = "IB_PAPER_QUOTE_ADMISSION_CALLBACK_UNSAFE";
        return false;
    }
    std::lock_guard<std::recursive_mutex> quoteLock(
        m_authoritativeQuoteSendMutex);
    if (m_quoteSubscriptions)
    {
        reason = "IB_PAPER_QUOTE_SUBSCRIPTIONS_ALREADY_ACTIVE";
        return false;
    }
    m_quoteSubscriptions = std::move(subscriptions);
    return true;
}

bool IbPaperExecutionRuntimeComposition::DispatchQuoteAdmissionPlan(
    QuoteAdmissionTransaction& transaction,
    const IBAuthoritativeQuoteSubscriptionPlan& plan,
    std::vector<int>& acceptedRequestIds, std::string& reason)
{
    (void)transaction;
    NotifyTestStage("before_quote_market_data_dispatch");
    acceptedRequestIds.reserve(plan.subscriptions.size());
    for (std::size_t i = 0; i < plan.subscriptions.size(); ++i)
    {
        const IBAuthoritativeQuoteSubscription& subscription =
            plan.subscriptions[i];
        if (m_adapter->EventIngressAdmissionFailed())
        {
            reason = "IB_PAPER_QUOTE_ADMISSION_CALLBACK_UNSAFE";
            return false;
        }
        acceptedRequestIds.push_back(subscription.requestId);
        bool dispatched = false;
        try { dispatched = m_adapter->ReqMktData(
            subscription.requestId, subscription.contract); }
        catch (...) {
            reason = "IB_PAPER_QUOTE_ADMISSION_EXCEPTION";
            MarkFatalRuntimeError(reason);
            return false;
        }
        if (!dispatched) acceptedRequestIds.pop_back();
        bool stateAccepted = false;
        {
            std::lock_guard<std::recursive_mutex> quoteLock(
                m_authoritativeQuoteSendMutex);
            stateAccepted = m_quoteSubscriptions &&
                m_quoteSubscriptions->RecordDispatchResult(
                    plan.generation, subscription.requestId, dispatched);
        }
        if (!stateAccepted)
        {
            reason = "IB_PAPER_QUOTE_DISPATCH_STATE_REJECTED";
            return false;
        }
        if (!dispatched)
        {
            reason = "IB_PAPER_QUOTE_SUBSCRIPTION_REJECTED";
            return false;
        }
        if (m_adapter->EventIngressAdmissionFailed())
        {
            reason = "IB_PAPER_QUOTE_ADMISSION_CALLBACK_UNSAFE";
            return false;
        }
    }
    return true;
}

bool IbPaperExecutionRuntimeComposition::FinishQuoteAdmission(
    QuoteAdmissionTransaction& transaction,
    std::string& reason, std::uint64_t expectedConnectionEpoch)
{
    transaction.Close();
    if (!SettleQuoteAdmission(reason, expectedConnectionEpoch)) return false;
    if (!m_adapter->IsEventStreamAuthoritative())
    {
        reason = "IB_PAPER_EVENT_STREAM_OVERFLOW";
        return false;
    }
    if (!m_adapter->IsConnected() ||
        (expectedConnectionEpoch != 0 &&
         m_adapter->GetConnectionEpoch() != expectedConnectionEpoch))
    {
        reason = "IB_PAPER_BROKER_CONNECTION_CHANGED_BEFORE_QUOTES";
        return false;
    }
    if (RequiresCashMarketDataFarm(m_config.quoteContracts) &&
        (!m_startupMarketDataFarmRestored.load() ||
         m_startupMarketDataFarmWaiting.load() ||
         m_startupMarketDataFarmEpoch.load() == 0 ||
         m_startupMarketDataFarmEpoch.load() !=
             (expectedConnectionEpoch != 0 ? expectedConnectionEpoch :
              m_adapter->GetConnectionEpoch())))
    {
        reason = "IB_PAPER_MARKET_DATA_FARM_NOT_READY";
        return false;
    }
    reason.clear();
    return true;
}

bool IbPaperExecutionRuntimeComposition::RunQuoteAdmissionTransaction(
    std::unique_ptr<IBAuthoritativeQuoteSubscriptionSet>& subscriptions,
    const IBAuthoritativeQuoteSubscriptionPlan& plan,
    std::string& reason, std::uint64_t expectedConnectionEpoch)
{
    const std::uint64_t generation = plan.generation;
    for (int pass = 0; pass < 4; ++pass)
    {
        CollectAdapterIngressEvents(0);
        if (HasFatalRuntimeError(&reason))
        {
            DrainAdapterEvents(0);
            AbortUnpublishedQuoteCycle(subscriptions, generation);
            return false;
        }
        NotifyTestStage("before_quote_admission_begin");
        QuoteAdmissionTransaction transaction(*m_adapter);
        const bool passReady = PrepareQuoteAdmissionPass(
            transaction, subscriptions, plan, reason, expectedConnectionEpoch);
        bool published = false;
        if (passReady)
        {
            std::lock_guard<std::recursive_mutex> quoteLock(
                m_authoritativeQuoteSendMutex);
            published = m_quoteSubscriptions != nullptr;
        }
        if (!passReady && !published)
        {
            transaction.Close();
            DrainAdapterEvents(0);
            std::string fatalReason;
            if (HasFatalRuntimeError(&fatalReason) && !fatalReason.empty()) reason = fatalReason;
            AbortUnpublishedQuoteCycle(subscriptions, generation);
            return false;
        }
        if (!published)
        {
            transaction.Close();
            DrainAdapterEvents(0);
            if (HasFatalRuntimeError(&reason))
            {
                AbortUnpublishedQuoteCycle(subscriptions, generation);
                return false;
            }
            if (pass == 3)
            {
                reason = "IB_PAPER_RUNTIME_EVENT_DRAIN_BUSY";
                MarkFatalRuntimeError(reason);
                AbortUnpublishedQuoteCycle(subscriptions, generation);
                return false;
            }
            continue;
        }
        std::vector<int> acceptedRequestIds;
        if (!DispatchQuoteAdmissionPlan(
                transaction, plan, acceptedRequestIds, reason))
        {
            transaction.Close();
            DrainAdapterEvents(0);
            bool cancelFailed = false;
            for (std::size_t i = 0; i < acceptedRequestIds.size(); ++i)
                if (!m_adapter->CancelMktData(acceptedRequestIds[i]))
                    cancelFailed = true;
            {
                std::lock_guard<std::recursive_mutex> quoteLock(
                    m_authoritativeQuoteSendMutex);
                if (m_quoteSubscriptions)
                {
                    m_quoteSubscriptions->AbortCycle(generation);
                    m_authoritativeSnapshots.InvalidateQuotes(
                        NowEpochMs(), "ib.quote_subscriptions_start_failed");
                    m_quoteSubscriptions.reset();
                }
            }
            std::string fatalReason;
            if (HasFatalRuntimeError(&fatalReason) && !fatalReason.empty())
                reason = fatalReason;
            else if (cancelFailed)
                reason = "IB_PAPER_MARKET_DATA_CANCEL_FAILED";
            return false;
        }
        if (!FinishQuoteAdmission(transaction, reason,
                                  expectedConnectionEpoch))
        {
            std::string cleanupReason;
            if (!StopQuoteSubscriptions(&cleanupReason) && reason.empty())
                reason = cleanupReason;
            return false;
        }
        return true;
    }
    reason = "IB_PAPER_MARKET_DATA_FARM_NOT_READY";
    AbortUnpublishedQuoteCycle(subscriptions, generation);
    return false;
}

bool IbPaperExecutionRuntimeComposition::StartQuoteSubscriptions(
    std::string& reason, std::uint64_t expectedConnectionEpoch)
{
    if (!m_adapter || m_config.quoteContracts.empty() ||
        m_config.primaryQuoteInstrument.empty())
    {
        reason = "IB_PAPER_QUOTE_CONTRACTS_REQUIRED";
        return false;
    }
    std::lock_guard<std::recursive_mutex> admissionLock(m_quoteAdmissionMutex);
    if (!CheckQuoteAdmissionPreflight(reason, expectedConnectionEpoch))
        return false;
    std::unique_ptr<IBAuthoritativeQuoteSubscriptionSet> subscriptions;
    IBAuthoritativeQuoteSubscriptionPlan plan;
    if (!PrepareQuoteSubscriptionPlan(
            subscriptions, plan, reason, expectedConnectionEpoch))
        return false;
    bool cancellationFailed = false;
    for (std::size_t i = 0; i < plan.cancelRequestIds.size(); ++i)
        if (!m_adapter->CancelMktData(plan.cancelRequestIds[i]))
            cancellationFailed = true;
    if (cancellationFailed)
    {
        subscriptions->AbortCycle(plan.generation);
        m_authoritativeSnapshots.InvalidateQuotes(
            NowEpochMs(), "ib.quote_subscriptions_start_cancel_failed");
        reason = "IB_PAPER_MARKET_DATA_CANCEL_FAILED";
        return false;
    }
    return RunQuoteAdmissionTransaction(
        subscriptions, plan, reason, expectedConnectionEpoch);
}

bool IbPaperExecutionRuntimeComposition::CollectAdapterIngressEvents(
    int pollTimeoutMs)
{
    std::lock_guard<std::mutex> eventDrainLock(m_adapterEventDrainMutex);
    bool drained = false;
    bool overflow = false;
    {
        // This phase only moves callbacks into the pending batch. It never
        // routes control events, so it is safe under the ingress fence.
        std::lock_guard<std::recursive_mutex> quoteSendLock(
            m_authoritativeQuoteSendMutex);
        if (m_adapter && pollTimeoutMs >= 0)
        {
            m_adapter->PollOnce(pollTimeoutMs);
            NotifyTestStage("after_adapter_poll_before_drain");
        }
        IBEvent event;
        while (m_adapter && m_adapter->TryDequeueEvent(event))
        {
            drained = true;
            if (event.type == IBEventType::TickPrice)
                ++m_pendingAuthoritativeQuoteEvents;
            if (m_pendingAdapterEvents.size() >= kMaxPendingAdapterEvents)
            {
                if (event.type == IBEventType::TickPrice &&
                    m_pendingAuthoritativeQuoteEvents > 0)
                    --m_pendingAuthoritativeQuoteEvents;
                overflow = true;
                break;
            }
            m_pendingAdapterEvents.push_back(std::move(event));
        }
    }
    if (overflow)
        MarkFatalRuntimeError("IB_PAPER_RUNTIME_EVENT_BACKLOG_OVERFLOW");
    return drained;
}

bool IbPaperExecutionRuntimeComposition::HasPendingAdapterEvents() const
{
    std::lock_guard<std::mutex> eventDrainLock(m_adapterEventDrainMutex);
    return !m_pendingAdapterEvents.empty();
}

bool IbPaperExecutionRuntimeComposition::RoutePendingAdapterEvents(
    std::vector<IBEvent>& pendingEvents, std::uint64_t& pendingQuoteEvents,
    bool& drained)
{
    for (std::vector<IBEvent>::const_iterator pending = pendingEvents.begin();
         pending != pendingEvents.end(); ++pending)
    {
        bool marketData10197 = false;
        {
            std::lock_guard<std::recursive_mutex> quoteSendLock(
                m_authoritativeQuoteSendMutex);
            marketData10197 = IsMarketData10197(
                *pending, m_quoteSubscriptions.get(), m_adapter.get());
        }
        if (!marketData10197 && IsCurrentEpochMarketData10197(
                *pending, m_adapter.get()) &&
            (m_startupBrokerPhase.load() || m_reconnectPending.load()))
            marketData10197 = true;
        const AdapterControlAction control =
            HandleAdapterControlEvent(*pending);
        if (control == AdapterControlAction::Consumed) continue;
        if (control == AdapterControlAction::ReconnectBoundary)
        {
            std::vector<IBEvent>::const_iterator tail = pending;
            ++tail;
            for (; tail != pendingEvents.cend(); ++tail)
            {
                if (tail->type == IBEventType::TickPrice) continue;
                if (tail->type == IBEventType::EventQueueOverflow)
                {
                    MarkFatalRuntimeError("IB_PAPER_EVENT_STREAM_OVERFLOW");
                    continue;
                }
                if (!IsPersistedBrokerCallback(tail->type)) continue;
                // A reconnect/control callback can precede a session-wide
                // 10197 in the same dequeued batch.  The reconnect boundary
                // path must preserve that witness and close the runtime just
                // like the ordinary route below; otherwise the tail is merely
                // journaled and a competing LIVE session could be retried.
                const bool tailMarketData10197 =
                    IsCurrentEpochMarketData10197(*tail, m_adapter.get());
                const AdapterRouteAction tailRoute =
                    RoutePersistedAdapterEvent(*tail);
                if (tailRoute == AdapterRouteAction::Busy)
                {
                    if (!PersistBrokerCallback(*tail, nullptr))
                        MarkFatalRuntimeError("OMS_BROKER_EVENT_WRITE_FAILED");
                    else
                        MarkFatalRuntimeError(
                            "IB_PAPER_BROKER_EVENT_ROUTE_BUSY_AT_DISCONNECT");
                }
                else if (tailRoute == AdapterRouteAction::Processed &&
                         tailMarketData10197)
                    MarkFatalRuntimeError(
                        "IB_PAPER_REALTIME_MARKET_DATA_UNAVAILABLE_10197");
            }
            std::lock_guard<std::recursive_mutex> quoteSendLock(
                m_authoritativeQuoteSendMutex);
            if (pendingQuoteEvents <= m_pendingAuthoritativeQuoteEvents)
                m_pendingAuthoritativeQuoteEvents -= pendingQuoteEvents;
            else
                m_pendingAuthoritativeQuoteEvents = 0;
            m_pendingAdapterEvents.clear();
            return true;
        }
        if (pending->type == IBEventType::TickPrice)
        {
            NotifyTestStage("before_authoritative_quote_tick_apply");
            std::lock_guard<std::recursive_mutex> quoteSendLock(
                m_authoritativeQuoteSendMutex);
            if (pendingQuoteEvents == 0 ||
                m_pendingAuthoritativeQuoteEvents == 0)
            {
                pendingQuoteEvents = 0;
                m_pendingAuthoritativeQuoteEvents = 0;
                MarkFatalRuntimeError(
                    "IB_PAPER_QUOTE_SEND_FENCE_STATE_INVALID");
                return true;
            }
            HandleNonPersistentAdapterEvent(*pending);
            --pendingQuoteEvents;
            --m_pendingAuthoritativeQuoteEvents;
            continue;
        }
        if (HandleNonPersistentAdapterEvent(*pending)) continue;
        const AdapterRouteAction routed = RoutePersistedAdapterEvent(*pending);
        if (routed == AdapterRouteAction::Busy)
        {
            m_pendingAdapterEvents.assign(pending, pendingEvents.cend());
            return drained;
        }
        if (routed == AdapterRouteAction::Fatal)
        {
            std::lock_guard<std::recursive_mutex> quoteSendLock(
                m_authoritativeQuoteSendMutex);
            if (pendingQuoteEvents <= m_pendingAuthoritativeQuoteEvents)
                m_pendingAuthoritativeQuoteEvents -= pendingQuoteEvents;
            else
                m_pendingAuthoritativeQuoteEvents = 0;
            return drained;
        }
        if (marketData10197)
        {
            MarkFatalRuntimeError(
                "IB_PAPER_REALTIME_MARKET_DATA_UNAVAILABLE_10197");
            return drained;
        }
    }
    return drained;
}

bool IbPaperExecutionRuntimeComposition::DrainAdapterEvents(int pollTimeoutMs)
{
    std::lock_guard<std::mutex> eventDrainLock(m_adapterEventDrainMutex);
    std::vector<IBEvent> pendingEvents;
    pendingEvents.swap(m_pendingAdapterEvents);
    std::uint64_t pendingQuoteEvents = 0;
    for (std::vector<IBEvent>::const_iterator pending = pendingEvents.begin();
         pending != pendingEvents.end(); ++pending)
        if (pending->type == IBEventType::TickPrice) ++pendingQuoteEvents;
    IBEvent dequeuedEvent;
    bool drained = false;
    bool overflow = false;
    {
        std::lock_guard<std::recursive_mutex> quoteSendLock(
            m_authoritativeQuoteSendMutex);
        if (m_adapter && pollTimeoutMs >= 0)
        {
            m_adapter->PollOnce(pollTimeoutMs);
            NotifyTestStage("after_adapter_poll_before_drain");
        }
        while (m_adapter && m_adapter->TryDequeueEvent(dequeuedEvent))
        {
            drained = true;
            if (dequeuedEvent.type == IBEventType::TickPrice)
            {
                ++m_pendingAuthoritativeQuoteEvents;
                ++pendingQuoteEvents;
            }
            if (pendingEvents.size() >= kMaxPendingAdapterEvents)
            {
                if (pendingQuoteEvents <= m_pendingAuthoritativeQuoteEvents)
                    m_pendingAuthoritativeQuoteEvents -= pendingQuoteEvents;
                else
                    m_pendingAuthoritativeQuoteEvents = 0;
                overflow = true;
                break;
            }
            pendingEvents.push_back(std::move(dequeuedEvent));
        }
    }
    if (overflow)
    {
        MarkFatalRuntimeError("IB_PAPER_RUNTIME_EVENT_BACKLOG_OVERFLOW");
        return true;
    }
    return RoutePendingAdapterEvents(pendingEvents, pendingQuoteEvents,
                                     drained);
}
