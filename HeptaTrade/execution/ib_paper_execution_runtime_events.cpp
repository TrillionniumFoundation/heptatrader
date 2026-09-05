#include "ib_paper_execution_runtime_internal.h"

#include <algorithm>
#include <chrono>
#include <cctype>
#include <cmath>
#include <cstring>
#include <limits>
#include <map>
using namespace ib_paper_execution_runtime_internal;

bool IbPaperExecutionRuntimeComposition::AllowsBoundRiskIncreasingPlace(
    const IBFinalOrderSendContext* context,
    const IBContractLite& contract, const IBOrderLite& order,
    std::string& reason) const
{
    std::lock_guard<std::recursive_mutex> lock(m_authoritativeQuoteSendMutex);
    if (m_pendingAuthoritativeQuoteEvents != 0)
    { reason = "IB_PAPER_PLACE_QUOTE_CHANGED_BEFORE_SEND"; return false; }
    if (!AllowsRiskIncrease(reason)) return false;
    if (context == nullptr || !context->authoritativeQuoteBound)
    { reason = "IB_PAPER_PLACE_QUOTE_BINDING_REQUIRED"; return false; }
    const std::map<std::string, InstrumentRef>::const_iterator expected =
        m_config.quoteContracts.find(context->instrument);
    if (expected == m_config.quoteContracts.end() ||
        contract.symbol != expected->second.symbol ||
        contract.secType != expected->second.secType ||
        contract.exchange != expected->second.exchange ||
        contract.primaryExchange != expected->second.primaryExchange ||
        contract.currency != expected->second.currency ||
        contract.lastTradeDateOrContractMonth !=
            expected->second.lastTradeDateOrContractMonth ||
        contract.right != expected->second.right ||
        std::memcmp(&contract.strike, &expected->second.strike,
                    sizeof(contract.strike)) != 0 ||
        contract.multiplier != expected->second.multiplier ||
        contract.tradingClass != expected->second.tradingClass ||
        contract.localSymbol != expected->second.localSymbol)
    { reason = "IB_PAPER_PLACE_CONTRACT_MISMATCH"; return false; }
    const MarketQuoteSnapshot quote = AuthoritativeQuote(context->instrument);
    const bool quoteUnchanged = quote.IsFresh(NowEpochMs()) &&
        context->instrument == quote.instrument &&
        context->quoteSubscriptionId == quote.subscriptionId &&
        context->quoteObservedAtMs == quote.observedAtMs &&
        context->quoteStaleAfterMs == quote.staleAfterMs &&
        context->quoteBid == quote.bid && context->quoteAsk == quote.ask;
    const bool externalLimitDay =
        m_config.profile.UsesExternalLimitDay();
    const bool allowedOrder = externalLimitDay ?
        (order.orderType == "LMT" &&
         order.auxPrice == 0.0 && !order.outsideRth &&
         std::isfinite(order.lmtPrice) && order.lmtPrice > 0.0 &&
         ((order.action == "BUY" && order.lmtPrice == quote.ask) ||
          (order.action == "SELL" && order.lmtPrice == quote.bid))) :
        (order.orderType == "MKT" && order.lmtPrice == 0.0);
    const bool allowed = quoteUnchanged && allowedOrder;
    if (!allowed) reason = "IB_PAPER_PLACE_QUOTE_CHANGED_BEFORE_SEND";
    return allowed;
}

bool IbPaperExecutionRuntimeComposition::BeginPostFillRiskRefresh(
    long orderId, const ExecutionOrderOwner& owner,
    const IBEvent& execution)
{
    // reqExecutions() is a restart/reconciliation read. Only the live
    // execDetails stream (request id -1) can begin a new post-fill generation.
    if (execution.requestId >= 0) return true;
    const std::string side = NormalizeExecutionSide(execution.value);
    if (orderId < 0 || side.empty() ||
        !std::isfinite(execution.number2) || execution.number2 <= 0.0 ||
        !std::isfinite(execution.number3) || execution.number3 < 0.0)
        return false;
    const double signedFill = side == "BUY" ?
        execution.number2 : -execution.number2;
    if (m_postFillRiskRefreshPending.load())
    {
        if (orderId != m_postFillOrderId ||
            owner.instrument != m_postFillInstrument ||
            side != m_postFillSide)
            return false;
        const double priorCumulative = std::fabs(
            m_postFillExpectedPosition - m_postFillBaselinePosition);
        // IB may repeat or reorder cumulative execution callbacks. Never
        // regress the trusted cumulative quantity and never apply an exact
        // duplicate twice. A same-cumulative remaining=0 callback is still a
        // valid transition from partial to complete.
        if (execution.number2 + 1e-6 < priorCumulative)
            return true;
        const bool cumulativeAdvanced =
            execution.number2 > priorCumulative + 1e-6;
        const bool executionComplete = execution.number3 <= 1e-6;
        const bool completionAdvanced =
            executionComplete && !m_postFillExecutionComplete;
        if (!cumulativeAdvanced && !completionAdvanced)
            return true;
        if (m_postFillExecutionComplete && cumulativeAdvanced &&
            !executionComplete)
            return false;
        if (cumulativeAdvanced)
            m_postFillExpectedPosition =
                m_postFillBaselinePosition + signedFill;
        m_postFillExecutionComplete =
            m_postFillExecutionComplete || executionComplete;
        m_postFillStableSince = std::chrono::steady_clock::time_point();
        if (completionAdvanced)
        {
            // Partial fills must not consume every bounded refresh before the
            // final cumulative execution arrives. Reserve one complete
            // account+positions attempt without extending the hard deadline.
            m_postFillRiskRefreshAttempts =
                std::min(m_postFillRiskRefreshAttempts, 2);
            m_postFillNextRetryAt = std::chrono::steady_clock::now();
        }
        return true;
    }
    // Duplicate live execution evidence for the already-settled order must
    // not apply the cumulative quantity a second time.
    if (orderId == m_postFillOrderId) return true;

    IBOrderRiskBaseline baseline;
    if (!m_adapter->GetOrderRiskBaseline(orderId, baseline) ||
        baseline.instrument != owner.instrument || baseline.side != side ||
        !std::isfinite(baseline.positionQuantity) ||
        baseline.connectionEpoch == 0 ||
        baseline.positionGeneration == 0 || baseline.fxCashGeneration == 0)
        return false;
    const std::map<std::string, InstrumentRef>::const_iterator contract =
        m_config.quoteContracts.find(owner.instrument);
    if (contract == m_config.quoteContracts.end())
        return false;

    m_postFillOrderId = orderId;
    m_postFillInstrument = owner.instrument;
    m_postFillSide = side;
    m_postFillBaselinePosition = baseline.positionQuantity;
    m_postFillExpectedPosition = baseline.positionQuantity + signedFill;
    m_postFillExecutionComplete = execution.number3 <= 1e-6;
    m_postFillTerminalObserved = false;
    m_postFillBaselinePositionGeneration = baseline.positionGeneration;
    m_postFillBaselineFxCashGeneration = baseline.fxCashGeneration;
    m_postFillRiskRefreshAttempts = 0;
    const std::chrono::steady_clock::time_point now =
        std::chrono::steady_clock::now();
    m_postFillNextRetryAt = now;
    m_postFillDeadline = now + std::chrono::seconds(8);
    m_postFillStableSince = std::chrono::steady_clock::time_point();
    m_postFillRiskRefreshPending.store(true);
    if (m_adapter->ReqRiskRefresh()) {
        m_postFillRiskRefreshAttempts = 1;
        m_postFillNextRetryAt = now + std::chrono::milliseconds(1500);
    }
    return true;
}

bool IbPaperExecutionRuntimeComposition::PostFillRiskRefreshPending() const
{
    return m_postFillRiskRefreshPending.load() ||
        (m_adapter &&
         m_adapter->HasPendingPostFillRiskReconciliation());
}

void IbPaperExecutionRuntimeComposition::DrivePostFillRiskRefresh()
{
    if (!m_postFillRiskRefreshPending.load() ||
        m_fatalRuntimeError.load() || !m_adapter)
        return;
    const std::chrono::steady_clock::time_point now =
        std::chrono::steady_clock::now();
    const IBAuthoritativeRiskSnapshot risk =
        m_adapter->GetAuthoritativeRiskSnapshot();
    double position = 0.0;
    std::string reason;
    const std::map<std::string, InstrumentRef>::const_iterator contract =
        m_config.quoteContracts.find(m_postFillInstrument);
    const bool coherent = m_postFillExecutionComplete &&
        risk.accountComplete && risk.positionsComplete &&
        risk.fxCashComplete &&
        risk.positionsGeneration > m_postFillBaselinePositionGeneration &&
        risk.fxCashGeneration > m_postFillBaselineFxCashGeneration &&
        contract != m_config.quoteContracts.end() &&
        m_adapter->ResolveAuthoritativePositionQuantity(
            m_postFillInstrument, contract->second, position, reason) &&
        std::fabs(position - m_postFillExpectedPosition) <= 1e-6;
    if (coherent)
    {
        if (m_postFillStableSince ==
                std::chrono::steady_clock::time_point())
            m_postFillStableSince = now;
        if (now - m_postFillStableSince >=
                std::chrono::milliseconds(250))
        {
            // Commit the exact broker cash observation before releasing either
            // mutation gate. A crash after a fill but before this point leaves
            // the previous checkpoint in place, so restart fails closed rather
            // than attributing uncheckpointed cash drift to the campaign.
            std::string checkpointReason;
            if (!PersistFxCashRestartCheckpoint(checkpointReason))
            {
                MarkFatalRuntimeError(checkpointReason.empty() ?
                    "IB_FX_CASH_RESTART_CHECKPOINT_WRITE_FAILED" :
                    checkpointReason);
                return;
            }
            // Release the adapter's final-send gate while the runtime pending
            // flag still blocks every concurrent command. Owner retirement
            // follows only for an observed broker terminal.
            if (!m_adapter->AcknowledgePostFillRiskReconciled(
                    m_postFillOrderId))
            {
                MarkFatalRuntimeError(
                    "IB_POST_FILL_RISK_ACKNOWLEDGEMENT_FAILED");
                return;
            }
            if (m_postFillTerminalObserved && m_coordinator)
                m_coordinator->RecordOrderTerminal(m_postFillOrderId);
            m_postFillTerminalObserved = false;
            m_postFillRiskRefreshPending.store(false);
        }
        return;
    }
    m_postFillStableSince = std::chrono::steady_clock::time_point();
    if (now >= m_postFillDeadline)
    {
        MarkFatalRuntimeError("IB_POST_FILL_RISK_RECONCILIATION_FAILED");
        return;
    }
    if (!m_postFillExecutionComplete)
    {
        // The first partial callback starts the hard deadline and one coherent
        // snapshot, but retries are reserved for the final cumulative fill.
        m_postFillNextRetryAt = m_postFillDeadline;
        return;
    }
    if (now < m_postFillNextRetryAt) return;
    if (m_postFillRiskRefreshAttempts >= 3)
    {
        // The final bounded attempt still owns the remaining hard-deadline
        // window; do not declare failure merely because its broker end
        // callbacks take longer than the normal retry cadence.
        m_postFillNextRetryAt = m_postFillDeadline;
        return;
    }
    if (m_adapter->ReqRiskRefresh())
    {
        ++m_postFillRiskRefreshAttempts;
        m_postFillNextRetryAt = now + std::chrono::milliseconds(1500);
    }
    else
    {
        // A prior generation can still be delivering its exact end boundary.
        // Do not supersede one leg or extend the hard deadline.
        m_postFillNextRetryAt = now + std::chrono::milliseconds(100);
    }
}

IbPaperExecutionRuntimeComposition::AdapterControlAction
IbPaperExecutionRuntimeComposition::HandleReconnectBrokerControl(
    const IBEvent& event, int controlErrorCode)
{
    if (!IsCurrentBrokerEpoch(m_adapter.get(), event.connectionEpoch))
        return AdapterControlAction::Route; // stale epoch: no state mutation
    if (controlErrorCode == 2110 || controlErrorCode == 1100)
    {
        m_reconnectUpstreamUnavailable.store(true);
        m_reconnectUpstreamRestored.store(false);
        NotifyTestStage("broker_reconnect_upstream_unavailable");
        if (m_reconnectRefreshDispatched.load())
            MarkFatalRuntimeError("IB_PAPER_BROKER_UPSTREAM_LOST_DURING_REFRESH");
        return AdapterControlAction::Consumed;
    }
    if (controlErrorCode == 1101)
    {
        // 1101 invalidates the farm witness; only a fresh CASH 2104 reopens it.
        if (RequiresCashMarketDataFarm(m_config.quoteContracts) &&
            IsCurrentBrokerEpoch(m_adapter.get(), event.connectionEpoch))
        {
            m_startupMarketDataFarmWaiting.store(true);
            m_startupMarketDataFarmRestored.store(false);
            m_startupMarketDataFarmEpoch.store(0);
        }
        bool quoteActive = false;
        if (IsCurrentBrokerEpoch(m_adapter.get(), event.connectionEpoch))
        {
            std::lock_guard<std::recursive_mutex> quoteLock(
                m_authoritativeQuoteSendMutex);
            quoteActive = HasActiveAuthoritativeQuoteCycle(
                m_quoteSubscriptions.get());
        }
        if (quoteActive)
        {
            // A reconnect refresh is not complete merely because transport
            // connectivity returned.  1101 says the already-dispatched
            // market-data cycle was lost; fail closed and force the normal
            // reconnect cleanup instead of allowing a stale quote set to
            // cross the ready transition.
            m_reconnectUpstreamUnavailable.store(false);
            m_reconnectUpstreamRestored.store(false);
            NotifyTestStage(
                "broker_reconnect_market_data_lost_during_refresh");
            PersistAndMarkFatalBrokerControl(event,
                "IB_PAPER_BROKER_RECONNECT_MARKET_DATA_LOST_DURING_REFRESH");
        }
        else
        {
            m_reconnectUpstreamUnavailable.store(false);
            m_reconnectUpstreamRestored.store(true);
            NotifyTestStage("broker_reconnect_upstream_restored");
        }
        return AdapterControlAction::Consumed;
    }
    if (controlErrorCode == 1102)
    {
        m_reconnectUpstreamUnavailable.store(false);
        m_reconnectUpstreamRestored.store(true);
        NotifyTestStage("broker_reconnect_upstream_restored");
        // 1102 explicitly says that market data was maintained, so it does
        // not invalidate the active refresh or block reconnect readiness.
        return AdapterControlAction::Consumed;
    }
    if (controlErrorCode == 504 || controlErrorCode == 509 ||
        controlErrorCode == 1300)
        return AdapterControlAction::Consumed;
    return AdapterControlAction::Route;
}

IbPaperExecutionRuntimeComposition::AdapterControlAction
IbPaperExecutionRuntimeComposition::HandleAdapterControlEvent(
    const IBEvent& event)
{
    int controlErrorCode = 0;
    const bool brokerControlError = event.type == IBEventType::Error &&
        ParseBrokerErrorCode(event.key, controlErrorCode);
    std::string farmDescription = event.value;
    std::transform(farmDescription.begin(), farmDescription.end(),
                   farmDescription.begin(), [](unsigned char value) {
        return value >= static_cast<unsigned char>('A') &&
                value <= static_cast<unsigned char>('Z') ?
            static_cast<char>(value - static_cast<unsigned char>('A') +
                              static_cast<unsigned char>('a')) :
            static_cast<char>(value);
    });
    const bool cashMarketDataFarm =
        farmDescription.find("cashfarm") != std::string::npos;
    const bool currentBrokerEpoch =
        IsCurrentBrokerEpoch(m_adapter.get(), event.connectionEpoch);
    if (brokerControlError && currentBrokerEpoch &&
        (m_startupBrokerPhase.load() || m_reconnectPending.load())) {
        // A generic 2104 is diagnostic only.  It never authorizes a CASH
        // request; only the scoped CASH-farm 2104 path below establishes the
        // epoch-bound readiness witness.  A 2103 revokes that witness until a
        // fresh scoped 2104 arrives.
        if (controlErrorCode == 2103) {
            // Do not leave a prior CASH 2104 lease valid after the broker has
            // explicitly declared a market-data farm broken.  This state is
            // also needed by test/adapter implementations whose wrapper does
            // not carry the production marker's blocking bit.
            if (RequiresCashMarketDataFarm(m_config.quoteContracts)) {
                m_startupMarketDataFarmRestored.store(false);
                m_startupMarketDataFarmWaiting.store(true);
                m_startupMarketDataFarmEpoch.store(0);
            }
            bool quoteActive = false;
            {
                std::lock_guard<std::recursive_mutex> quoteLock(
                    m_authoritativeQuoteSendMutex);
                quoteActive = HasActiveAuthoritativeQuoteCycle(
                    m_quoteSubscriptions.get());
            }
            if (quoteActive)
                MarkFatalRuntimeError(
                    "IB_PAPER_MARKET_DATA_FARM_LOST_DURING_REFRESH");
        }
    }
    const bool marketDataFarmControl =
        controlErrorCode == 10197 ||
        (cashMarketDataFarm &&
         (controlErrorCode == 2104 || controlErrorCode == 2119));
    // 10197 is a terminal real-time-data witness, not a readiness/control
    // acknowledgement.  Keep it on the persistence path even during
    // startup/reconnect; DrainAdapterEvents records the callback first and
    // then publishes the exact fatal reason.  Returning Consumed here would
    // silently bypass that evidence (especially for global id=-1/0 errors).
    if (brokerControlError && controlErrorCode == 10197)
        return AdapterControlAction::Route;
    if (brokerControlError && currentBrokerEpoch &&
        (m_startupBrokerPhase.load() || m_reconnectPending.load()) &&
        marketDataFarmControl)
        return HandleMarketDataFarmControl(controlErrorCode,
                                           event.connectionEpoch);
    if (brokerControlError && m_startupBrokerPhase.load())
    {
        const AdapterControlAction action =
            HandleStartupBrokerControl(event, controlErrorCode);
        if (action != AdapterControlAction::Route) return action;
    }
    if (brokerControlError && m_reconnectPending.load())
    {
        const AdapterControlAction action =
            HandleReconnectBrokerControl(event, controlErrorCode);
        if (action != AdapterControlAction::Route) return action;
    }
    const bool brokerControlReconnect = brokerControlError && currentBrokerEpoch &&
        (controlErrorCode == 504 || controlErrorCode == 509 ||
         controlErrorCode == 1100 || controlErrorCode == 1101 ||
         controlErrorCode == 1102 || controlErrorCode == 1300 ||
         controlErrorCode == 2110 ||
         (controlErrorCode == 2119 && cashMarketDataFarm &&
          currentBrokerEpoch));
    if (event.type == IBEventType::ConnectionClosed && !currentBrokerEpoch)
        return AdapterControlAction::Route;
    if (event.type == IBEventType::ConnectionClosed &&
        m_startupBrokerPhase.load())
    {
        MarkFatalRuntimeError("IB_PAPER_STARTUP_BROKER_CONNECTION_CLOSED");
        return AdapterControlAction::Consumed;
    }
    if (event.type != IBEventType::ConnectionClosed &&
        !brokerControlReconnect)
        return AdapterControlAction::Route;
    std::string reconnectReason;
    if (!BeginBrokerReconnect(reconnectReason))
        MarkFatalRuntimeError(reconnectReason.empty() ?
            "IB_PAPER_BROKER_RECONNECT_UNSAFE" : reconnectReason);
    return AdapterControlAction::ReconnectBoundary;
}

void IbPaperExecutionRuntimeComposition::PersistAndMarkFatalBrokerControl(
    const IBEvent& event, const std::string& reason)
{
    if (!PersistBrokerCallback(event, nullptr))
    {
        MarkFatalRuntimeError("OMS_BROKER_EVENT_WRITE_FAILED");
        return;
    }
    MarkFatalRuntimeError(reason);
}

bool IbPaperExecutionRuntimeComposition::HandleNonPersistentAdapterEvent(
    const IBEvent& event)
{
    if (event.type == IBEventType::EventQueueOverflow)
    {
        MarkFatalRuntimeError("IB_PAPER_EVENT_STREAM_OVERFLOW");
        return true;
    }
    if (event.type != IBEventType::TickPrice)
        return !IsPersistedBrokerCallback(event.type);
    IBAuthoritativeQuoteConsumeResult consumed;
    IBAuthoritativeQuoteSnapshot primaryQuote;
    {
        std::lock_guard<std::recursive_mutex> quoteLock(
            m_authoritativeQuoteSendMutex);
        if (!m_quoteSubscriptions) return true;
        consumed = m_quoteSubscriptions->ConsumeTick(event, NowEpochMs());
        if (consumed.status == IBAuthoritativeQuoteConsumeStatus::Applied &&
            consumed.primary)
            primaryQuote = m_quoteSubscriptions->GetPrimaryQuote();
    }
    if (consumed.status == IBAuthoritativeQuoteConsumeStatus::Rejected)
    {
        MarkFatalRuntimeError(consumed.reasonCode.empty() ?
            "IB_PAPER_AUTHORITATIVE_QUOTE_WRITE_REJECTED" :
            consumed.reasonCode);
        return true;
    }
    if (consumed.status == IBAuthoritativeQuoteConsumeStatus::Applied &&
        consumed.primary)
    {
        if (HasPositiveTradableQuote(primaryQuote))
            m_adapter->UpdateReferencePrice(
                (primaryQuote.bid + primaryQuote.ask) * 0.5);
        NotifyTestStage("authoritative_quote_tick_applied");
    }
    return true;
}

bool IbPaperExecutionRuntimeComposition::ValidateBrokerCallbackIdentity(
    const IBEvent& event, const ExecutionOrderOwner& owner,
    std::string& reason) const
{
    if (event.type != IBEventType::ExecutionDetails &&
        event.type != IBEventType::CompletedOrder)
        return true;
    if (event.account.empty() || owner.account.empty() ||
        event.account != owner.account ||
        owner.account != m_config.profile.account)
    {
        reason = "IB_BROKER_CALLBACK_ACCOUNT_MISMATCH";
        return false;
    }
    if (event.type != IBEventType::ExecutionDetails) return true;
    if (NormalizeExecutionSide(event.value) != owner.side)
    {
        reason = "IB_EXECUTION_SIDE_MISMATCH";
        return false;
    }
    const std::map<std::string, InstrumentRef>::const_iterator expected =
        m_config.quoteContracts.find(owner.instrument);
    if (expected == m_config.quoteContracts.end() ||
        event.contract.symbol != expected->second.symbol ||
        event.contract.secType != expected->second.secType ||
        event.contract.currency != expected->second.currency)
    {
        reason = "IB_EXECUTION_CONTRACT_MISMATCH";
        return false;
    }
    return true;
}

bool IbPaperExecutionRuntimeComposition::BeginEventPostFillRiskRefresh(
    const IBEvent& event, const ExecutionOrderOwner& owner, long orderId)
{
    if (event.type == IBEventType::OrderStatus &&
        HasPositiveEconomicFillEvidence(event) &&
        !IsHistoricalSyntheticExecutionStatus(event))
    {
        const std::map<std::string, InstrumentRef>::const_iterator expected =
            m_config.quoteContracts.find(owner.instrument);
        if (expected == m_config.quoteContracts.end() ||
            expected->second.secType != "CASH")
        {
            MarkFatalRuntimeError(
                "IB_POST_FILL_RISK_REFRESH_CONTRACT_UNSUPPORTED");
            return false;
        }
        IBEvent statusEvidence = event;
        statusEvidence.requestId = -1;
        statusEvidence.value = owner.side;
        if (!BeginPostFillRiskRefresh(orderId, owner, statusEvidence))
        {
            MarkFatalRuntimeError(
                "IB_POST_FILL_RISK_REFRESH_START_FAILED");
            return false;
        }
    }
    if (event.type == IBEventType::ExecutionDetails &&
        event.contract.secType == "CASH" &&
        std::isfinite(event.number2) && event.number2 > 0.0 &&
        std::isfinite(event.number) && event.number > 0.0 &&
        !BeginPostFillRiskRefresh(orderId, owner, event))
    {
        MarkFatalRuntimeError("IB_POST_FILL_RISK_REFRESH_START_FAILED");
        return false;
    }
    return true;
}

bool IbPaperExecutionRuntimeComposition::IsTerminalBrokerEvent(
    const IBEvent& event)
{
    if (event.type == IBEventType::OrderStatus)
        return IsEconomicallyTerminalOrderStatus(event);
    if (event.type == IBEventType::CompletedOrder)
        return event.key == "Cancelled" || event.key == "ApiCancelled" ||
            event.key == "Inactive" || event.key == "Rejected";
    if (event.type != IBEventType::Error) return false;
    int errorCode = 0;
    return ParseBrokerErrorCode(event.key, errorCode) &&
        (errorCode == 201 || errorCode == 202);
}

void IbPaperExecutionRuntimeComposition::RecordTerminalBrokerEvent(
    long orderId)
{
    if (!m_coordinator) return;
    if (m_postFillRiskRefreshPending.load() && orderId == m_postFillOrderId)
    {
        m_postFillTerminalObserved = true;
        if (!m_postFillExecutionComplete)
        {
            m_postFillExecutionComplete = true;
            m_postFillRiskRefreshAttempts =
                std::min(m_postFillRiskRefreshAttempts, 2);
            m_postFillNextRetryAt = std::chrono::steady_clock::now();
        }
        return;
    }
    if (m_adapter && orderId == m_postFillOrderId)
        m_adapter->AcknowledgePostFillRiskReconciled(orderId);
    m_coordinator->RecordOrderTerminal(orderId);
}

IbPaperExecutionRuntimeComposition::AdapterRouteAction
IbPaperExecutionRuntimeComposition::RoutePersistedAdapterEvent(
    const IBEvent& event)
{
    const long orderId = static_cast<long>(event.id);
    bool ownerEligible = event.type != IBEventType::Error;
    if (event.type == IBEventType::Error)
    {
        int errorCode = 0;
        ownerEligible = ParseBrokerErrorCode(event.key, errorCode) &&
            (errorCode == 201 || errorCode == 202);
    }
    ExecutionOrderOwner owner;
    ExecutionOrderOwnerLookupStatus ownerLookup =
        ExecutionOrderOwnerLookupStatus::Missing;
    if (ownerEligible && m_coordinator && orderId >= 0)
        ownerLookup = m_coordinator->TryGetOrderOwner(orderId, owner);
    if (ownerLookup == ExecutionOrderOwnerLookupStatus::Busy)
        return AdapterRouteAction::Busy;
    const bool hasOwner =
        ownerLookup == ExecutionOrderOwnerLookupStatus::Found;
    std::string identityFailure;
    const bool validIdentity = !hasOwner ||
        ValidateBrokerCallbackIdentity(event, owner, identityFailure);
    const bool routeOwner = hasOwner && validIdentity;
    if (!PersistBrokerCallback(event, routeOwner ? &owner : nullptr))
    {
        MarkFatalRuntimeError("OMS_BROKER_EVENT_WRITE_FAILED");
        return AdapterRouteAction::Fatal;
    }
    if (!validIdentity)
    {
        MarkFatalRuntimeError(identityFailure);
        return AdapterRouteAction::Fatal;
    }
    if (routeOwner)
    {
        PublishBrokerCallback(event, owner);
        if (!BeginEventPostFillRiskRefresh(event, owner, orderId))
            return AdapterRouteAction::Fatal;
    }
    if (IsTerminalBrokerEvent(event)) RecordTerminalBrokerEvent(orderId);
    return AdapterRouteAction::Processed;
}


bool IbPaperExecutionRuntimeComposition::PersistBrokerCallback(
    const IBEvent& event, const ExecutionOrderOwner* owner)
{
    OmsJournalEvent record;
    record.tsMs = OmsJournal::NowEpochMs();
    record.orderId = static_cast<long>(event.id);
    record.venue = "IB";
    record.source = owner ? std::string("agent:") + owner->agentId :
        "ib-api-callback";
    record.traceId = owner ? owner->sessionId : std::string();
    record.strategy = owner ? owner->strategy : std::string();
    record.executionDomain = owner ? owner->executionDomain : "PAPER";
    record.instrument = owner ? owner->instrument : std::string();
    record.side = owner ? owner->side : std::string();
    record.account = !event.account.empty() ? event.account :
        (owner ? owner->account : std::string());
    if (record.instrument.empty() &&
        event.type == IBEventType::ExecutionDetails) {
        for (std::map<std::string, InstrumentRef>::const_iterator it =
                 m_config.quoteContracts.begin();
             it != m_config.quoteContracts.end(); ++it) {
            if (event.contract.symbol == it->second.symbol &&
                event.contract.secType == it->second.secType &&
                event.contract.currency == it->second.currency) {
                if (!record.instrument.empty()) {
                    record.instrument.clear();
                    break;
                }
                record.instrument = it->first;
            }
        }
    }
    record.brokerRequestId = event.requestId;
    record.brokerServiceEpoch = m_serviceIdentity.serviceEpoch;
    record.brokerConnectionEpoch = event.connectionEpoch;
    record.brokerWhyHeld = event.whyHeld;
    record.brokerMarketCapPrice = event.marketCapPrice;
    record.brokerAdvancedOrderRejectJson =
        event.advancedOrderRejectJson;

    if (event.type == IBEventType::OrderStatus)
    {
        record.eventType = "broker_order_status";
        record.brokerCallbackType = "orderStatus";
        record.status = event.key;
        record.qty = event.number2;
        record.brokerRemainingQuantity = event.number3;
        record.price = event.number;
        record.reason = event.whyHeld;
        if (event.key == "Filled" &&
            !IsEconomicallyTerminalOrderStatus(event))
            record.riskCode = "IB_FILLED_ECONOMIC_EVIDENCE_REQUIRED";
        else if (!event.whyHeld.empty())
            record.riskCode = "IB_ORDER_HELD";
        else if (event.key == "Cancelled" || event.key == "ApiCancelled" ||
                 event.key == "Inactive" || event.key == "Rejected")
            record.riskCode = StatusReasonCode(event.key);
    }
    else if (event.type == IBEventType::Error)
    {
        record.eventType = "broker_error";
        record.brokerCallbackType = "error";
        record.status = "Error";
        record.reason = event.value;
        record.brokerMessage = event.value;
        int errorCode = 0;
        if (ParseBrokerErrorCode(event.key, errorCode))
        {
            record.brokerErrorCode = errorCode;
            record.riskCode = "IB_ERROR_" + std::to_string(errorCode);
            if (errorCode == 201) record.status = "Rejected";
            else if (errorCode == 202) record.status = "Cancelled";
        }
    }
    else if (event.type == IBEventType::ExecutionDetails)
    {
        record.eventType = "broker_execution";
        record.brokerCallbackType = "execDetails";
        record.status = "ExecutionDetails";
        record.brokerExecutionId = event.key;
        if (!event.value.empty()) record.side = event.value;
        record.qty = event.number2;
        record.brokerRemainingQuantity = event.number3;
        record.price = event.number;
    }
    else if (event.type == IBEventType::CompletedOrder)
    {
        record.eventType = "broker_completed_order";
        record.brokerCallbackType = "completedOrder";
        record.status = event.key;
        record.qty = event.number;
        record.price = event.number2;
        if (event.key == "Cancelled" || event.key == "ApiCancelled" ||
            event.key == "Inactive" || event.key == "Rejected")
            record.riskCode = StatusReasonCode(event.key);
        else if (event.key == "Filled")
            record.riskCode = "IB_FILLED_ECONOMIC_EVIDENCE_REQUIRED";
    }
    else if (event.type == IBEventType::CompletedOrdersEnd)
    {
        record.eventType = "broker_completed_orders_end";
        record.brokerCallbackType = "completedOrdersEnd";
        record.status = "End";
        record.orderId = -1;
    }
    else if (event.type == IBEventType::ExecutionDetailsEnd)
    {
        record.eventType = "broker_execution_details_end";
        record.brokerCallbackType = "execDetailsEnd";
        record.status = "End";
        record.orderId = -1;
    }
    else return true;

    if (!m_journal.Append(record)) return false;
    ProjectRecentBrokerOrder(record);
    return true;
}

void IbPaperExecutionRuntimeComposition::PublishBrokerCallback(
    const IBEvent& event, const ExecutionOrderOwner& owner)
{
    if (!m_eventHub) return;
    ExecutionEvent output;
    output.executionDomain = owner.executionDomain;
    output.agentId = owner.agentId;
    output.sessionId = owner.sessionId;
    output.venue = "IB";
    output.orderId = static_cast<long>(event.id);
    output.instrument = owner.instrument;
    output.side = owner.side;
    if (event.type == IBEventType::OrderStatus)
    {
        output.type = "order.status";
        output.status = event.key;
        output.averageFillPrice = event.number;
        output.filledQuantity = event.number2;
        output.remainingQuantity = event.number3;
        if (event.key == "Filled" &&
            !IsEconomicallyTerminalOrderStatus(event))
            output.reasonCode = "IB_FILLED_ECONOMIC_EVIDENCE_REQUIRED";
        else if (!event.whyHeld.empty())
            output.reasonCode = "IB_ORDER_HELD";
        else if (event.key == "Cancelled" || event.key == "ApiCancelled" ||
                 event.key == "Inactive" || event.key == "Rejected")
            output.reasonCode = StatusReasonCode(event.key);
    }
    else if (event.type == IBEventType::ExecutionDetails)
    {
        output.type = "order.fill";
        output.status = "ExecutionDetails";
        if (!event.value.empty()) output.side = event.value;
        output.averageFillPrice = event.number;
        output.filledQuantity = event.number2;
        output.remainingQuantity = event.number3;
    }
    else if (event.type == IBEventType::CompletedOrder)
    {
        output.type = "order.completed";
        output.status = event.key;
        if (event.key == "Cancelled" || event.key == "ApiCancelled" ||
            event.key == "Inactive" || event.key == "Rejected")
            output.reasonCode = StatusReasonCode(event.key);
        else if (event.key == "Filled")
            output.reasonCode = "IB_FILLED_ECONOMIC_EVIDENCE_REQUIRED";
    }
    else if (event.type == IBEventType::Error)
    {
        int errorCode = 0;
        if (!ParseBrokerErrorCode(event.key, errorCode)) return;
        output.reasonCode = "IB_ERROR_" + std::to_string(errorCode);
        if (errorCode == 201)
        {
            output.type = "order.reject";
            output.status = "Rejected";
        }
        else if (errorCode == 202)
        {
            output.type = "order.status";
            output.status = "Cancelled";
        }
        else
        {
            output.type = "order.error";
            output.status = "Error";
        }
    }
    else return;
    m_eventHub->Publish(output);
}

void IbPaperExecutionRuntimeComposition::ProjectRecentBrokerOrder(
    const OmsJournalEvent& event)
{
    const bool acceptedSend = event.eventType == "broker_order_accepted";
    const bool reconciledTerminal =
        event.eventType == "order_owner_reconciled_terminal";
    const bool projectable = acceptedSend || reconciledTerminal ||
        event.eventType == "broker_order_status" ||
        event.eventType == "broker_error" ||
        event.eventType == "broker_execution" ||
        event.eventType == "broker_completed_order";
    if (event.orderId < 0 || !projectable) return;
    const std::string brokerAgentPrefix("agent:");
    const std::string toolAgentPrefix("agent.tool:");
    std::string agentId;
    if (event.source.compare(
            0, brokerAgentPrefix.size(), brokerAgentPrefix) == 0)
        agentId = event.source.substr(brokerAgentPrefix.size());
    else if (event.source.compare(
                 0, toolAgentPrefix.size(), toolAgentPrefix) == 0)
        agentId = event.source.substr(toolAgentPrefix.size());
    else
        return;
    if (agentId.empty() || event.traceId.empty()) return;

    std::lock_guard<std::mutex> lock(m_recentBrokerOrdersMutex);
    if (acceptedSend)
    {
        ProjectAcceptedRecentBrokerOrder(event, agentId);
        return;
    }
    RecentBrokerOrder& order = m_recentBrokerOrders[event.orderId];
    ApplyRecentBrokerOrderIdentity(order, event, agentId);
    ApplyRecentBrokerExecutionIdentity(order, event);
    const bool economic = HasPositiveEconomicEvidence(event);
    ApplyRecentBrokerEconomicEvidence(order, event, economic);
    ApplyRecentBrokerTerminalEvidence(
        order, event, reconciledTerminal, economic);
    TrimRecentBrokerOrders();
}
bool IbPaperExecutionRuntimeComposition::ProjectAcceptedBrokerOrder(
    const IbPlaceOrderCommand& command, long orderId,
    std::string& reason)
{
    if (orderId < 0)
    {
        reason = "IB_BROKER_ACCEPTED_ORDER_ID_INVALID";
        return false;
    }
    OmsJournalEvent accepted;
    accepted.eventType = "broker_order_accepted";
    accepted.tsMs = OmsJournal::NowEpochMs();
    accepted.orderId = orderId;
    accepted.venue = "IB";
    accepted.source = std::string("agent:") + command.context.agentId;
    accepted.traceId = command.context.sessionId;
    accepted.strategy = command.context.strategy;
    accepted.account = command.context.account;
    accepted.executionDomain = command.context.executionDomain;
    accepted.instrument = command.instrument;
    accepted.side = command.order.action;
    accepted.qty = command.order.totalQuantity;
    accepted.status = "Accepted";
    accepted.brokerCallbackType = "localBrokerAcceptance";
    accepted.brokerServiceEpoch = m_serviceIdentity.serviceEpoch;
    accepted.brokerConnectionEpoch = m_adapter ?
        m_adapter->GetConnectionEpoch() : 0;
    accepted.brokerRemainingQuantity = command.order.totalQuantity;
    if (!m_journal.Append(accepted))
    {
        reason = "OMS_BROKER_ACCEPTANCE_WRITE_FAILED";
        return false;
    }
    ProjectRecentBrokerOrder(accepted);
    reason.clear();
    return true;
}
bool IbPaperExecutionRuntimeComposition::RebuildRecentBrokerOrders(
    std::string& reason)
{
    {
        std::lock_guard<std::mutex> lock(m_recentBrokerOrdersMutex);
        m_recentBrokerOrders.clear();
    }
    const int replayed = m_journal.Replay(
        [this](const OmsJournalEvent& event) {
            ProjectRecentBrokerOrder(event);
        });
    if (replayed < 0)
    {
        reason = "IB_PAPER_BROKER_EVIDENCE_REPLAY_FAILED";
        return false;
    }
    reason.clear();
    return true;
}
bool IbPaperExecutionRuntimeComposition::CompleteTerminalTransportAudit(
    IBAuthoritativeRecoveryAuditSnapshot& frozen,
    IBAuthoritativeRecoveryAuditSnapshot& snapshot,
    ExecutionControlResult& terminalState,
    std::string& reason)
{
    frozen.postFillRiskReconciliationPending =
        frozen.postFillRiskReconciliationPending ||
        m_postFillRiskRefreshPending.load() ||
        m_adapter->HasPendingPostFillRiskReconciliation();
    snapshot = frozen;
    terminalState = m_terminalResult;
    terminalState.terminalBrokerTransportConnected = m_adapter->IsConnected();
    terminalState.terminalBrokerEventIngressHalted =
        m_adapter->IsTerminalTransportHalted();
    terminalState.terminalBrokerCallbackQueueDrained =
        m_adapter->IsTerminalTransportHalted();
    terminalState.terminalBrokerCallbacksInFlight = 0;
    terminalState.terminalRuntimeLatchLoaded = true;
    terminalState.terminalRuntimeVerified = false;
    terminalState.terminalReplay = false;
    if (terminalState.terminalBrokerTransportConnected ||
        !terminalState.terminalBrokerEventIngressHalted ||
        !terminalState.terminalBrokerCallbackQueueDrained)
    {
        reason = "IB_PAPER_TERMINAL_TRANSPORT_BOUNDARY_INVALID";
        return false;
    }
    NotifyTestStage("paper_terminal_transport_halted");
    reason.clear();
    return true;
}
