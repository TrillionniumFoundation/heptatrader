#include "../HeptaTrade/execution/ib_paper_execution_runtime_composition.h"
#include "../HeptaTrade/execution/ib_paper_execution_runtime_internal.h"
#include "../HeptaTrade/execution/execution_event_feed.h"
#include "../HeptaTrade/execution/execution_service_protocol.h"
#include "../HeptaTrade/execution/unix_execution_service.h"

#include <algorithm>
#include <cassert>
#include <atomic>
#include <chrono>
#include <cctype>
#include <cerrno>
#include <cstring>
#include <cstdlib>
#include <deque>
#include <fcntl.h>
#include <fstream>
#include <iomanip>
#include <iostream>
#include <map>
#include <memory>
#include <mutex>
#include <stdexcept>
#include <sstream>
#include <string>
#include <thread>
#include <vector>
#include <sys/socket.h>
#include <sys/stat.h>
#include <sys/un.h>
#include <unistd.h>
#include <openssl/evp.h>

namespace
{
class MutableKillSwitch final : public IbPaperKillSwitchReader
{
public:
    std::atomic<IbPaperKillSwitchState> state{IbPaperKillSwitchState::Disarmed};

    IbPaperKillSwitchObservation Observe() const override
    {
        IbPaperKillSwitchObservation result;
        result.state = state.load();
        if (result.state == IbPaperKillSwitchState::Engaged)
            result.reasonCode = "IB_PAPER_KILL_SWITCH_ENGAGED";
        else if (result.state == IbPaperKillSwitchState::Uncertain)
            result.reasonCode = "IB_PAPER_KILL_SWITCH_STATE_UNCERTAIN";
        return result;
    }
};

struct FakeBrokerState
{
    long nextOrderId = 100;
    int sends = 0;
    double positionQuantity = 0.0;
    std::string positionKey = "EUR.USD";
    InstrumentRef positionContract;
    std::atomic<int> snapshotRequests{0};
    std::atomic<int> conflictingAccountSnapshots{0};
    std::atomic<double> staleCashBalance{0.0};
    std::atomic<int> marketDataRequests{0};
    std::atomic<int> marketDataCancels{0};
    std::atomic<int> marketDataCancelFailureRequestId{0};
    std::atomic<int> marketDataRequestId{0};
    // Real Gateway may expose a generic market-data farm 2104 before the CASH
    // farm status appears. These switches model that ordering explicitly.
    std::atomic<bool> emitTransportFarmOnConnect{false};
    std::atomic<bool> suppressCashFarmOnConnect{false};
    // Inject a blocking callback immediately before the serialized formal
    // request check to exercise the check-to-send admission boundary.
    std::atomic<bool> injectMarketDataErrorBeforeRequest{false};
    // Deliver a blocking callback a few polls after reqMktData returns. This
    // exercises the bounded post-dispatch settle before quote publication.
    std::atomic<bool> injectMarketDataErrorAfterRequest{false};
    std::atomic<int> marketDataErrorAfterRequestPolls{0};
    // Model the SDK throwing after the request may already have reached IB.
    // The admission path must retain the attempted id for cancellation.
    std::atomic<bool> throwMarketDataAfterSideEffect{false};
    // Model the IB socket disappearing during reqMktData without delivering a
    // connectionClosed callback.  The production wrapper must synthesize a
    // blocking witness after its post-request connection recheck.
    std::atomic<bool> disconnectDuringMarketDataRequest{false};
    // The runtime must never send market data before this positive current
    // epoch witness has been drained.
    std::atomic<bool> cashFarm2104Dequeued{false};
    std::atomic<std::uint64_t> cashFarm2104WitnessEpoch{0};
    std::atomic<bool> marketDataRequestBefore2104{false};
    std::atomic<bool> uppercaseCashFarmDescription{false};
    std::atomic<int> marketDataErrorCodeOnRequest{0};
    // Optional blocking control callback injected immediately before the
    // formal ReqMktData side effect. Zero retains the historical 10197 case.
    std::atomic<int> marketDataErrorCodeBeforeRequest{0};
    std::atomic<int> quoteBarriersDequeued{0};
    std::atomic<int> injectedQuoteTicksPolled{0};
    std::atomic<bool> economicFillDequeued{false};
    std::map<long, IBOrderLite> openOrders;
    std::deque<IBEvent> completedOrderEvents;
    std::deque<IBEvent> executionDetailEvents;
    std::mutex injectedMutex;
    std::deque<IBEvent> injectedEvents;
    std::atomic<bool> emitConnectionClosed{false};
    std::atomic<int> emitControlErrorCode{0};
    std::atomic<int> startupControlErrorOnConnect{0};
    std::atomic<int> reconnectControlErrorOnConnect{0};
    // Positive cash-farm readiness is explicit in the fixture.  A
    // non-negative warning delay enables the delayed 2119/2104 regression
    // scenario; the default zero ready delay emits 2104 on Connect().
    std::atomic<int> cashFarmWarningDelayMs{-1};
    std::atomic<int> cashFarmReadyDelayMs{0};
    std::atomic<bool> upstreamReady{true};
    std::atomic<int> refreshRequestsWhileUpstreamUnavailable{0};
    std::atomic<int> reconnectAttempts{0};
    std::atomic<int> reconnectFailuresRemaining{0};
    std::atomic<bool> suppressCompletedOrdersEnd{false};
    std::atomic<bool> suppressExecutionDetailsEnd{false};
    std::atomic<std::uint64_t> callbackEpoch{1};
    std::atomic<bool> emitEventQueueOverflow{false};
    std::atomic<bool> blockPoll{false};
    std::atomic<bool> pollEntered{false};
    std::atomic<bool> releasePoll{false};
    std::atomic<int> lastPollTimeoutMs{-1};
    std::atomic<std::uint64_t> pollCount{0};
    std::atomic<int> controlErrorsInjected{0};
    std::atomic<int> controlErrorsDequeued{0};
    std::atomic<bool> throwAfterPlaceSideEffect{false};
    std::atomic<bool> suppressPlaceStatus{false};
    // Models the broker transport remaining alive while the connector is no
    // longer authorized/visible to authoritative health.
    std::atomic<bool> connectorVisible{true};
    std::atomic<int> disconnectErrorCode{0};
    std::atomic<long> disconnectFillOrderId{-1};
    std::atomic<int> disconnectCalls{0};
};

IBEvent Event(IBEventType type, long id = 0)
{
    IBEvent event;
    event.type = type;
    event.id = id;
    // A zero epoch means "stamp with the wrapper's active epoch". Tests that
    // need stale-callback coverage set an explicit older epoch.
    event.connectionEpoch = 0;
    event.account = "DU123456";
    return event;
}

class FakeIbWrapper final : public IIBApiWrapper
{
public:
    explicit FakeIbWrapper(const std::shared_ptr<FakeBrokerState>& state)
        : m_state(state)
    {
        IBEvent next = Event(IBEventType::NextValidId, state->nextOrderId);
        m_events.push_back(next);
    }
    bool Connect(const IBConnectParams&) override {
        const int connectAttempt = ++m_state->reconnectAttempts;
        if (m_state->reconnectFailuresRemaining.fetch_sub(1) > 0) {
            m_connected = false;
            return false;
        }
        if (m_state->reconnectFailuresRemaining.load() < 0)
            m_state->reconnectFailuresRemaining.store(0);
        m_connected = true;
        m_connectedAt = std::chrono::steady_clock::now();
        m_cashFarmWarningQueued = false;
        m_cashFarmReadyQueued = false;
        m_state->cashFarm2104Dequeued.store(false);
        m_state->cashFarm2104WitnessEpoch.store(0);
        if (m_state->emitTransportFarmOnConnect.load())
            QueueTransportFarmEvent(2104);
        if (!m_state->suppressCashFarmOnConnect.load() &&
            m_state->cashFarmReadyDelayMs.load() <= 0) {
            QueueCashFarmEvent(2104);
            m_cashFarmReadyQueued = true;
        }
        {
            const int controlError = connectAttempt == 1 ?
                m_state->startupControlErrorOnConnect.exchange(0) :
                m_state->reconnectControlErrorOnConnect.exchange(0);
            if (controlError != 0) {
                if (controlError == 2110 || controlError == 1100)
                    m_state->upstreamReady.store(false);
                IBEvent error = Event(IBEventType::Error);
                error.key = std::to_string(controlError);
                PushEvent(std::move(error));
            }
        }
        return true;
    }
    void SetConnectionEpoch(std::uint64_t value) override {
        m_epoch = value;
        m_state->callbackEpoch.store(value);
    }
    void SetEventIngressFence(
        const std::shared_ptr<std::recursive_mutex>& fence) override {
        m_eventIngressFence = fence;
    }
    void BeginEventIngressAdmission() override {
        std::lock_guard<std::mutex> lock(m_eventIngressAdmissionMutex);
        m_eventIngressAdmissionActive = true;
        m_eventIngressFenceHeld = true;
        m_eventIngressAdmissionFailed = false;
    }
    void EndEventIngressAdmission() override {
        std::lock_guard<std::mutex> lock(m_eventIngressAdmissionMutex);
        while (!m_deferredEvents.empty()) {
            m_events.push_back(std::move(m_deferredEvents.front()));
            m_deferredEvents.pop_front();
        }
        m_eventIngressAdmissionActive = false;
    }
    void FlushEventIngressAdmission() override {
        std::lock_guard<std::mutex> lock(m_eventIngressAdmissionMutex);
        while (!m_deferredEvents.empty()) {
            m_events.push_back(std::move(m_deferredEvents.front()));
            m_deferredEvents.pop_front();
        }
    }
    void CompleteEventIngressAdmission() override {
        std::lock_guard<std::mutex> lock(m_eventIngressAdmissionMutex);
        while (!m_deferredEvents.empty()) {
            m_events.push_back(std::move(m_deferredEvents.front()));
            m_deferredEvents.pop_front();
        }
        m_eventIngressFenceHeld = false;
    }
    bool EventIngressAdmissionFailed() const override {
        std::lock_guard<std::mutex> lock(m_eventIngressAdmissionMutex);
        return m_eventIngressAdmissionFailed;
    }
    void InjectEventForTest(IBEvent event) { PushEvent(std::move(event)); }
    std::uint64_t GetConnectionEpoch() const override { return m_epoch; }
    void Disconnect() override {
        ++m_state->disconnectCalls;
        const int errorCode = m_state->disconnectErrorCode.exchange(0);
        if (errorCode != 0) {
            IBEvent error = Event(IBEventType::Error);
            error.key = std::to_string(errorCode);
            error.value = "disconnect-boundary-event";
            PushEvent(std::move(error));
        }
        const long fillOrderId =
            m_state->disconnectFillOrderId.exchange(-1);
        if (fillOrderId >= 0) {
            IBEvent filled = Event(IBEventType::OrderStatus, fillOrderId);
            filled.key = "Filled";
            filled.number = 1.10010;
            filled.number2 = 1.0;
            filled.number3 = 0.0;
            PushEvent(std::move(filled));
        }
        m_connected = false;
    }
    bool IsConnected() const override {
        return m_connected && m_state->connectorVisible.load();
    }
    const char* GetStatusString() const override { return "FAKE_IB_PAPER"; }
    bool GetBrokerConnectionIdentity(
        IBBrokerConnectionIdentity& identity,
        std::string& reason) const override {
        identity = IBBrokerConnectionIdentity();
        if (!IsConnected() || m_epoch == 0) {
            reason = "IB_BROKER_SOCKET_IDENTITY_NOT_CONNECTED";
            return false;
        }
        identity.connectionEpoch = m_epoch;
        identity.canonical = "FAKE_IBSOCK1\\nconnection_epoch=" +
            std::to_string(m_epoch) + "\\nendpoint=fixture\\n";
        reason.clear();
        return true;
    }
    bool ReqAccountSummary() override {
        RecordRefreshRequest(true);
        IBEvent value = Event(IBEventType::AccountValue);
        value.key = "NetLiquidation:USD";
        value.value = "1000000";
        PushEvent(std::move(value));
        IBEvent ready = Event(IBEventType::AccountValue);
        ready.key = "AccountReady:";
        ready.value = "true";
        PushEvent(std::move(ready));
        IBEvent cash = Event(IBEventType::AccountValue);
        cash.key = "CashBalance:EUR";
        if (m_state->conflictingAccountSnapshots.fetch_sub(1) > 0) {
            IBEvent stale = cash;
            stale.value = std::to_string(
                m_state->staleCashBalance.load());
            PushEvent(std::move(stale));
        } else if (m_state->conflictingAccountSnapshots.load() < 0) {
            m_state->conflictingAccountSnapshots.store(0);
        }
        cash.value = std::to_string(m_state->positionQuantity);
        PushEvent(std::move(cash));
        PushEvent(Event(IBEventType::AccountSummaryEnd));
        return true;
    }
    bool ReqPositions() override {
        RecordRefreshRequest(true);
        PushEvent(Event(IBEventType::PositionEnd));
        return true;
    }
    bool ReqOpenOrders() override {
        RecordRefreshRequest(true);
        for (std::map<long, IBOrderLite>::const_iterator it = m_state->openOrders.begin();
             it != m_state->openOrders.end(); ++it) {
            IBEvent open = Event(IBEventType::OpenOrder, it->first);
            open.order = it->second;
            PushEvent(std::move(open));
        }
        PushEvent(Event(IBEventType::OpenOrderEnd));
        return true;
    }
    bool ReqAllOpenOrders() override { return ReqOpenOrders(); }
    bool ReqCompletedOrders() override {
        RecordRefreshRequest(true);
        while (!m_state->completedOrderEvents.empty()) {
            PushEvent(m_state->completedOrderEvents.front());
            m_state->completedOrderEvents.pop_front();
        }
        if (!m_state->suppressCompletedOrdersEnd.load())
            PushEvent(Event(IBEventType::CompletedOrdersEnd));
        return true;
    }
    bool ReqExecutions(int requestId) override {
        RecordRefreshRequest(true);
        while (!m_state->executionDetailEvents.empty()) {
            IBEvent event = m_state->executionDetailEvents.front();
            m_state->executionDetailEvents.pop_front();
            event.requestId = requestId;
            PushEvent(std::move(event));
        }
        if (!m_state->suppressExecutionDetailsEnd.load()) {
            IBEvent end = Event(IBEventType::ExecutionDetailsEnd);
            end.requestId = requestId;
            PushEvent(std::move(end));
        }
        return true;
    }
    bool ReqMktData(int requestId, const IBContractLite& contract) override {
        // The fixture mirrors the production wrapper: CASH requests are
        // rejected until a positive current-epoch CASH-farm 2104 has been
        // dequeued.  There is no readiness exception to this invariant.
        if (contract.secType == "CASH" &&
            m_state->cashFarm2104WitnessEpoch.load() != m_epoch) {
            m_state->marketDataRequestBefore2104.store(true);
            return false;
        }
        if (m_state->injectMarketDataErrorBeforeRequest.exchange(false)) {
            int errorCode = m_state->marketDataErrorCodeBeforeRequest.exchange(0);
            if (errorCode == 0) errorCode = 10197;
            IBEvent error = Event(IBEventType::Error, requestId);
            error.key = std::to_string(errorCode);
            error.value = errorCode == 1101 ?
                "connectivity restored; market data lost" :
                "simulated competing live session";
            PushEvent(std::move(error));
        }
        bool admission = false;
        {
            std::lock_guard<std::mutex> admissionLock(
                m_eventIngressAdmissionMutex);
            admission = m_eventIngressAdmissionActive ||
                m_eventIngressFenceHeld;
            if (admission && m_eventIngressAdmissionFailed) return false;
            if (admission) m_eventIngressSendActive = true;
        }
        if (admission) {
            std::lock_guard<std::mutex> admissionLock(
                m_eventIngressAdmissionMutex);
            if (m_eventIngressAdmissionFailed) {
                m_eventIngressSendActive = false;
                return false;
            }
        }
        RecordRefreshRequest(false);
        if (!m_state->cashFarm2104Dequeued.load())
            m_state->marketDataRequestBefore2104.store(true);
        m_state->marketDataRequests.fetch_add(1);
        m_state->marketDataRequestId.store(requestId);
        if (m_state->injectMarketDataErrorAfterRequest.exchange(false))
            m_state->marketDataErrorAfterRequestPolls.store(8);
        if (m_state->throwMarketDataAfterSideEffect.exchange(false)) {
            if (admission) EndMarketDataAdmissionSend();
            throw std::runtime_error(
                "fake broker threw after market-data side effect");
        }
        if (m_state->disconnectDuringMarketDataRequest.exchange(false)) {
            // No broker callback is emitted here: this is the silent-close
            // path that the real wrapper detects only after the SDK returns.
            m_connected = false;
            IBEvent closed = Event(IBEventType::ConnectionClosed);
            closed.value = "IB_MARKET_DATA_REQUEST_CONNECTION_LOST";
            PushEvent(std::move(closed));
            if (admission) EndMarketDataAdmissionSend();
            return true;
        }
        const int errorCode =
            m_state->marketDataErrorCodeOnRequest.load();
        if (errorCode != 0) {
            IBEvent error = Event(IBEventType::Error, requestId);
            error.key = std::to_string(errorCode);
            error.value = "simulated market data error";
            PushEvent(std::move(error));
            if (admission) EndMarketDataAdmissionSend();
            return true;
        }
        IBEvent bid = Event(IBEventType::TickPrice, requestId);
        bid.key = "1";
        bid.number = 1.1000;
        PushEvent(std::move(bid));
        IBEvent ask = Event(IBEventType::TickPrice, requestId);
        ask.key = "2";
        ask.number = 1.1002;
        PushEvent(std::move(ask));
        if (admission) EndMarketDataAdmissionSend();
        return true;
    }
    bool CancelMktData(int requestId) override {
        ++m_state->marketDataCancels;
        if (m_state->marketDataCancelFailureRequestId.load() == requestId)
            return false;
        return true;
    }
    bool PlaceOrder(long orderId, const IBContractLite&, const IBOrderLite& order) override {
        ++m_state->sends;
        m_state->openOrders[orderId] = order;
        if (orderId >= m_state->nextOrderId) m_state->nextOrderId = orderId + 1;
        IBEvent submitted = Event(IBEventType::OrderStatus, orderId);
        submitted.key = "Submitted";
        if (!m_state->suppressPlaceStatus.load())
            PushEvent(std::move(submitted));
        if (m_state->throwAfterPlaceSideEffect.exchange(false))
            throw std::runtime_error(
                "fake broker threw after recording place side effect");
        return true;
    }
    bool CancelOrder(long orderId) override {
        m_state->openOrders.erase(orderId);
        IBEvent cancelled = Event(IBEventType::OrderStatus, orderId);
        cancelled.key = "Cancelled";
        PushEvent(std::move(cancelled));
        return true;
    }
    bool PollOnce(int timeoutMs) override {
        ++m_state->pollCount;
        m_state->lastPollTimeoutMs.store(timeoutMs);
        if (m_state->blockPoll.load()) {
            m_state->pollEntered.store(true);
            while (!m_state->releasePoll.load())
                std::this_thread::sleep_for(std::chrono::milliseconds(1));
        }
        MaybeQueueCashFarmEvents();
        if (m_state->marketDataErrorAfterRequestPolls.load() > 0 &&
            m_state->marketDataErrorAfterRequestPolls.fetch_sub(1) == 1) {
            IBEvent error = Event(IBEventType::Error);
            error.key = "10197";
            error.value = "delayed competing live session";
            PushEvent(std::move(error));
        }
        if (m_state->emitConnectionClosed.exchange(false))
            PushEvent(Event(IBEventType::ConnectionClosed));
        const int controlError = m_state->emitControlErrorCode.exchange(0);
        if (controlError != 0) {
            ++m_state->controlErrorsInjected;
            IBEvent error = Event(IBEventType::Error);
            error.key = std::to_string(controlError);
            PushEvent(std::move(error));
        }
        if (m_state->emitEventQueueOverflow.exchange(false)) {
            IBEvent overflow = Event(IBEventType::EventQueueOverflow);
            overflow.overflowGeneration = 1;
            overflow.droppedEventCount = 1;
            PushEvent(std::move(overflow));
        }
        {
            std::lock_guard<std::mutex> lock(m_state->injectedMutex);
            while (!m_state->injectedEvents.empty())
            {
                if (m_state->injectedEvents.front().type ==
                    IBEventType::TickPrice)
                    ++m_state->injectedQuoteTicksPolled;
                PushEvent(m_state->injectedEvents.front());
                m_state->injectedEvents.pop_front();
            }
        }
        return m_connected;
    }
    bool TryDequeueEvent(IBEvent& event) override {
        std::lock_guard<std::mutex> lock(m_eventIngressAdmissionMutex);
        if (m_events.empty()) return false;
        event = m_events.front();
        m_events.pop_front();
        if (event.connectionEpoch == 0) event.connectionEpoch = m_epoch;
        if (event.type == IBEventType::Error &&
            event.connectionEpoch == m_epoch && event.key == "2104" &&
            IsCashFarmDescription(event.value))
        {
            m_state->cashFarm2104Dequeued.store(true);
            m_state->cashFarm2104WitnessEpoch.store(m_epoch);
        }
        if (event.type == IBEventType::Error &&
            event.connectionEpoch == m_epoch) {
            ++m_state->controlErrorsDequeued;
            if (event.key == "2110" || event.key == "1100")
                m_state->upstreamReady.store(false);
            else if (event.key == "1101" || event.key == "1102")
                m_state->upstreamReady.store(true);
        }
        if (event.type == IBEventType::OrderStatus &&
            event.key == "Filled" && event.number > 0.0 &&
            event.number2 > 0.0)
            m_state->economicFillDequeued.store(true);
        if (event.key == "__hepta_test_quote_barrier__")
            ++m_state->quoteBarriersDequeued;
        return true;
    }
    long GetLastValidOrderId() const override { return m_state->nextOrderId; }
private:
    static bool IsCashFarmDescription(const std::string& description)
    {
        std::string normalized = description;
        std::transform(normalized.begin(), normalized.end(),
                       normalized.begin(), [](unsigned char value) {
            return static_cast<char>(std::tolower(value));
        });
        return normalized.find("cashfarm") != std::string::npos;
    }

    void QueueCashFarmEvent(int code)
    {
        IBEvent error = Event(IBEventType::Error);
        error.key = std::to_string(code);
        error.value = m_state->uppercaseCashFarmDescription.load() ?
            "CASHFARM" : "cashfarm";
        error.connectionEpoch = m_epoch;
        PushEvent(std::move(error));
    }

    void QueueTransportFarmEvent(int code)
    {
        IBEvent error = Event(IBEventType::Error);
        error.key = std::to_string(code);
        error.value = "hfarm";
        error.connectionEpoch = m_epoch;
        PushEvent(std::move(error));
    }

    void MaybeQueueCashFarmEvents()
    {
        if (!m_connected || m_state->suppressCashFarmOnConnect.load()) return;
        const long elapsedMs = std::chrono::duration_cast<
            std::chrono::milliseconds>(std::chrono::steady_clock::now() -
                                       m_connectedAt).count();
        const int warningDelayMs = m_state->cashFarmWarningDelayMs.load();
        if (!m_cashFarmWarningQueued && warningDelayMs >= 0 &&
            elapsedMs >= warningDelayMs)
        {
            QueueCashFarmEvent(2119);
            m_cashFarmWarningQueued = true;
        }
        const int readyDelayMs = m_state->cashFarmReadyDelayMs.load();
        if (!m_cashFarmReadyQueued && readyDelayMs >= 0 &&
            elapsedMs >= readyDelayMs)
        {
            QueueCashFarmEvent(2104);
            m_cashFarmReadyQueued = true;
        }
    }

    void RecordRefreshRequest(bool snapshot)
    {
        if (snapshot) ++m_state->snapshotRequests;
        if (!m_state->upstreamReady.load())
            ++m_state->refreshRequestsWhileUpstreamUnavailable;
    }

    void PushEvent(IBEvent event)
    {
        std::lock_guard<std::mutex> lock(m_eventIngressAdmissionMutex);
        const std::uint64_t eventEpoch = event.connectionEpoch == 0 ?
            m_epoch : event.connectionEpoch;
        const bool staleEpoch = eventEpoch != 0 && eventEpoch < m_epoch;
        if (IsAdmissionBlockingEvent(event) && !staleEpoch)
            m_eventIngressAdmissionFailed = true;
        if (m_eventIngressAdmissionActive || m_eventIngressFenceHeld ||
            m_eventIngressSendActive)
            m_deferredEvents.push_back(std::move(event));
        else
            m_events.push_back(std::move(event));
    }
    void EndMarketDataAdmissionSend()
    {
        std::lock_guard<std::mutex> lock(m_eventIngressAdmissionMutex);
        m_eventIngressSendActive = false;
        if (!m_eventIngressAdmissionActive && !m_eventIngressFenceHeld) {
            while (!m_deferredEvents.empty()) {
                m_events.push_back(std::move(m_deferredEvents.front()));
                m_deferredEvents.pop_front();
            }
        }
    }
    static bool IsAdmissionBlockingEvent(const IBEvent& event)
    {
        if (event.type == IBEventType::ConnectionClosed ||
            event.type == IBEventType::EventQueueOverflow)
            return true;
        if (event.type != IBEventType::Error || event.key.empty())
            return event.type == IBEventType::Error;
        char* end = nullptr;
        errno = 0;
        const long code = std::strtol(event.key.c_str(), &end, 10);
        if (errno != 0 || end == event.key.c_str() ||
            end == nullptr || *end != '\0')
            return true;
        switch (code) {
        case 2104:
        case 2106:
        case 2107:
        case 2108:
        case 2109:
        case 2158:
        case 1102: // connectivity restored; data maintained
            return false;
        case 1101: // connectivity restored; data lost
            return true;
        default:
            return true;
        }
    }
    std::shared_ptr<FakeBrokerState> m_state;
    std::shared_ptr<std::recursive_mutex> m_eventIngressFence;
    mutable std::mutex m_eventIngressAdmissionMutex;
    bool m_eventIngressAdmissionActive = false;
    bool m_eventIngressFenceHeld = false;
    bool m_eventIngressAdmissionFailed = false;
    bool m_eventIngressSendActive = false;
    std::deque<IBEvent> m_deferredEvents;
    // Match the production wrapper: construction alone is never connection
    // evidence. Startup must call Connect(), which also keeps the fake's
    // connect-attempt and per-attempt control-error injection deterministic.
    bool m_connected = false;
    std::uint64_t m_epoch = 1;
    std::deque<IBEvent> m_events;
    std::chrono::steady_clock::time_point m_connectedAt;
    bool m_cashFarmWarningQueued = false;
    bool m_cashFarmReadyQueued = false;
};

void InjectQuoteTick(const std::shared_ptr<FakeBrokerState>& broker,
                     const char* field,
                     double value)
{
    IBEvent event = Event(
        IBEventType::TickPrice,
        broker->marketDataRequestId.load());
    event.key = field;
    event.number = value;
    event.connectionEpoch = broker->callbackEpoch.load();
    std::lock_guard<std::mutex> lock(broker->injectedMutex);
    broker->injectedEvents.push_back(event);
}

void InjectQuoteBarrier(const std::shared_ptr<FakeBrokerState>& broker)
{
    IBEvent event = Event(IBEventType::AccountValue);
    event.key = "__hepta_test_quote_barrier__";
    event.connectionEpoch = broker->callbackEpoch.load();
    std::lock_guard<std::mutex> lock(broker->injectedMutex);
    broker->injectedEvents.push_back(event);
}

void WaitForQuoteBarrier(const std::shared_ptr<FakeBrokerState>& broker)
{
    const int before = broker->quoteBarriersDequeued.load();
    InjectQuoteBarrier(broker);
    for (int attempt = 0; attempt < 1000 &&
         broker->quoteBarriersDequeued.load() == before; ++attempt)
        std::this_thread::sleep_for(std::chrono::milliseconds(1));
    assert(broker->quoteBarriersDequeued.load() == before + 1);
}

void WaitForAppliedQuoteTicks(
    const std::shared_ptr<std::atomic<int> >& applied,
    int expected)
{
    for (int attempt = 0; attempt < 1000 &&
         applied->load() < expected; ++attempt)
        std::this_thread::sleep_for(std::chrono::milliseconds(1));
    assert(applied->load() >= expected);
}

std::string TempDirectory(const char* pattern)
{
    char buffer[128];
    std::strncpy(buffer, pattern, sizeof(buffer));
    buffer[sizeof(buffer) - 1] = '\0';
    char* created = ::mkdtemp(buffer);
    assert(created != nullptr);
    assert(::chmod(created, 0700) == 0);
    return created;
}

std::string SocketPath(const char* stem)
{
    return std::string("/tmp/") + stem + "-" + std::to_string(::getpid()) + ".sock";
}

int ActivatedSocket(const std::string& path)
{
    ::unlink(path.c_str());
    const int fd = ::socket(AF_UNIX, SOCK_STREAM | SOCK_CLOEXEC, 0);
    assert(fd >= 0);
    struct sockaddr_un address;
    std::memset(&address, 0, sizeof(address));
    address.sun_family = AF_UNIX;
    assert(path.size() < sizeof(address.sun_path));
    std::memcpy(address.sun_path, path.c_str(), path.size() + 1);
    assert(::bind(fd, reinterpret_cast<struct sockaddr*>(&address), sizeof(address)) == 0);
    assert(::chmod(path.c_str(), 0600) == 0);
    assert(::listen(fd, 8) == 0);
    return fd;
}

void WriteFile(const std::string& path, const std::string& value)
{
    std::ofstream output(path.c_str(), std::ios::out | std::ios::trunc);
    assert(output.is_open());
    output << value;
    output.close();
    assert(::chmod(path.c_str(), 0400) == 0);
}

std::string TestSha256(const std::string& value)
{
    unsigned char digest[EVP_MAX_MD_SIZE];
    unsigned int length = 0;
    EVP_MD_CTX* context = EVP_MD_CTX_new();
    assert(context != nullptr);
    assert(EVP_DigestInit_ex(context, EVP_sha256(), nullptr) == 1);
    assert(EVP_DigestUpdate(context, value.data(), value.size()) == 1);
    assert(EVP_DigestFinal_ex(context, digest, &length) == 1);
    EVP_MD_CTX_free(context);
    std::ostringstream output;
    output << "sha256:" << std::hex << std::setfill('0');
    for (unsigned int i = 0; i < length; ++i)
        output << std::setw(2) << static_cast<unsigned int>(digest[i]);
    return output.str();
}

std::string HfxRecord(
    const std::string& account,
    const std::string& instrument,
    const std::string& currency,
    const std::string& baseline,
    const std::string& observed,
    const std::string& delta,
    const std::string& observedAtMs)
{
    const std::string signedFields = account + "|" + instrument + "|" +
        currency + "|" + baseline + "|" + observed + "|" + delta +
        "|" + observedAtMs;
    return signedFields + "|" + TestSha256(signedFields);
}

std::string HfxRestartRecord(
    const std::string& account,
    const std::string& instrument,
    const std::string& currency,
    const std::string& baseline,
    const std::string& observed,
    const std::string& delta,
    const std::string& observedAtMs,
    const std::string& baselineProof)
{
    const std::string signedFields = account + "|" + instrument + "|" +
        currency + "|" + baseline + "|" + observed + "|" + delta +
        "|" + observedAtMs + "|" + baselineProof;
    return signedFields + "|" + TestSha256(signedFields);
}

std::string FxCashRestartCheckpointPath(const std::string& state)
{
    return state + "/ib-fx-cash-restart-attestation";
}

void WritePrivateStateFile(const std::string& path,
                           const std::string& contents)
{
    WriteFile(path, contents);
    assert(::chmod(path.c_str(), 0600) == 0);
}

std::string ReadTestFile(const std::string& path)
{
    std::ifstream input(path.c_str(), std::ios::in | std::ios::binary);
    assert(input.good());
    std::ostringstream contents;
    contents << input.rdbuf();
    assert(input.good() || input.eof());
    return contents.str();
}

IbPaperExecutionRuntimeConfig Config(int executionFd, int eventFd,
    const std::string& state, const std::string& credentials)
{
    IbPaperExecutionRuntimeConfig config;
    config.mode = IbPaperExecutionRuntimeMode::Paper;
    config.listenFd = executionFd;
    config.eventListenFd = eventFd;
    config.allowedGatewayUids.insert(static_cast<std::uint32_t>(::geteuid()));
    config.gatewayContextBinding.agentId = "agent";
    config.gatewayContextBinding.account = "DU123456";
    config.gatewayContextBinding.venue = "IB";
    config.gatewayContextBinding.executionDomain = "PAPER";
    config.stateDirectory = state;
    config.journalPath = state + "/oms-journal.jsonl";
    config.controlDirectory = "/run/hepta/ib-paper-control";
    config.fenceCredentialPath = credentials + "/hepta-execution-fence";
    config.fxCashBaselineCredentialPath =
        credentials + "/hepta-fx-cash-baseline";
    config.authorizationCredentialPath = credentials + "/hepta-ib-paper-authorization";
    config.ioTimeoutMs = 1000;
    config.readinessTimeoutMs = 1000;
    config.reconnectTimeoutMs = 1000;
    config.profile.enabled = true;
    config.profile.account = "DU123456";
    config.profile.host = "127.0.0.1";
    config.profile.port = 7497;
    config.profile.clientId = 701;
    config.profile.stateDirectory = state;
    config.profile.authorizationCredentialPath = config.authorizationCredentialPath;
    config.profile.controlDirectory = config.controlDirectory;
    config.profile.maxOrderQuantity = 1000.0;
    config.profile.maxOrderNotional = 250000.0;
    config.profile.maxOrdersPerMinute = 10;
    config.profile.maxActiveOrders = 10;
    config.profile.maxGrossPosition = 5000.0;
    IBContractLite eurUsd;
    eurUsd.symbol = "EUR";
    eurUsd.secType = "CASH";
    eurUsd.exchange = "IDEALPRO";
    eurUsd.currency = "USD";
    config.quoteContracts["EUR.USD"] = eurUsd;
    IbPaperFxCashBaseline baseline;
    baseline.account = config.profile.account;
    baseline.instrument = "EUR.USD";
    baseline.currency = "EUR";
    baseline.baselineCashBalance = 0.0;
    baseline.observedCashBalance = 0.0;
    baseline.campaignExecutionDelta = 0.0;
    baseline.observedAtMs = 1;
    baseline.proof = "sha256:" + std::string(64, '0');
    config.fxCashBaselines["EUR.USD"] = baseline;
    config.primaryQuoteInstrument = "EUR.USD";
    config.quoteMaxAgeMs = 5000;
    return config;
}

IbPaperExecutionRuntimeConfig ExternalLimitConfig(
    int executionFd, int eventFd,
    const std::string& state, const std::string& credentials)
{
    IbPaperExecutionRuntimeConfig config =
        Config(executionFd, eventFd, state, credentials);
    config.profile.orderMode = IbPaperOrderMode::ExternalLimitDay;
    config.profile.externalQuoteMaxAgeMs = 5000;
    config.profile.maxOrderQuantity = 1.0;
    config.profile.maxOrderNotional = 5000.0;
    config.profile.maxActiveOrders = 1;
    config.profile.maxGrossPosition = 1.0;
    config.quoteMaxAgeMs = 5000;
    return config;
}

void TestFxCashBaselineCredentialProductionParserAndStartupBinding()
{
    const std::string validRecord = HfxRecord(
        "DU123456", "EUR.USD", "EUR", "0", "0", "0", "1");
    const std::string validCredential = "HFX1\n" + validRecord + "\n";
    enum class SourceKind { Missing, Regular, Symlink };
    struct CredentialCase
    {
        const char* name;
        SourceKind source;
        std::string contents;
        mode_t mode;
        bool starts;
        const char* reason;
        double brokerCashBalance;
        bool reachesBroker;
    };
    const std::string badProof =
        "HFX1\nDU123456|EUR.USD|EUR|0|0|0|1|sha256:" +
        std::string(64, '0') + "\n";
    const std::string duplicate =
        "HFX1\n" + validRecord + "\n" + validRecord + "\n";
    const CredentialCase cases[] = {
        {"positive", SourceKind::Regular, validCredential, 0400, true, "",
         0.0, true},
        {"missing", SourceKind::Missing, std::string(), 0, false,
         "IB_FX_CASH_BASELINE_MISSING", 0.0, false},
        {"symlink", SourceKind::Symlink, validCredential, 0400, false,
         "IB_FX_CASH_BASELINE_UNSAFE", 0.0, false},
        {"mode", SourceKind::Regular, validCredential, 0600, false,
         "IB_FX_CASH_BASELINE_UNSAFE", 0.0, false},
        {"proof", SourceKind::Regular, badProof, 0400, false,
         "IB_FX_CASH_BASELINE_PROOF_MISMATCH", 0.0, false},
        {"account", SourceKind::Regular,
         "HFX1\n" + HfxRecord(
             "DU999999", "EUR.USD", "EUR", "0", "0", "0", "1") +
             "\n",
         0400, false, "IB_FX_CASH_BASELINE_INVALID", 0.0, false},
        {"instrument", SourceKind::Regular,
         "HFX1\n" + HfxRecord(
             "DU123456", "GBP.USD", "EUR", "0", "0", "0", "1") +
             "\n",
         0400, false, "IB_FX_CASH_BASELINE_INVALID", 0.0, false},
        {"currency", SourceKind::Regular,
         "HFX1\n" + HfxRecord(
             "DU123456", "EUR.USD", "GBP", "0", "0", "0", "1") +
             "\n",
         0400, false, "IB_FX_CASH_BASELINE_INVALID", 0.0, false},
        {"duplicate", SourceKind::Regular, duplicate, 0400, false,
         "IB_FX_CASH_BASELINE_AMBIGUOUS", 0.0, false},
        {"arithmetic", SourceKind::Regular,
         "HFX1\n" + HfxRecord(
             "DU123456", "EUR.USD", "EUR", "0", "1", "0", "1") +
             "\n",
         0400, false, "IB_FX_CASH_BASELINE_INVALID", 0.0, false},
        // The credential's observed balance is a one-shot startup binding.
        // A broker balance that drifted before process start must not be
        // silently attributed to this campaign's baseline delta.
        {"prestart-drift", SourceKind::Regular, validCredential, 0400, false,
         "IB_FX_CASH_ATTESTED_BALANCE_MISMATCH", 1.0, true},
    };

    for (std::size_t i = 0; i < sizeof(cases) / sizeof(cases[0]); ++i)
    {
        const std::string state = TempDirectory(
            "/tmp/hepta-hfx-parser-state-XXXXXX");
        const std::string credentials = TempDirectory(
            "/tmp/hepta-hfx-parser-cred-XXXXXX");
        WriteFile(credentials + "/hepta-execution-fence",
            "HFC1\nfencing_token=77\ngeneration=9\n");
        IbPaperExecutionRuntimeConfig bootstrap =
            Config(-1, -1, state, credentials);
        std::string authorization;
        std::string authorizationReason;
        assert(bootstrap.profile.BuildAuthorizationCredential(
            authorization, authorizationReason));
        WriteFile(credentials + "/hepta-ib-paper-authorization",
            authorization + "\n");

        const std::string baselinePath =
            credentials + "/hepta-fx-cash-baseline";
        const std::string symlinkTarget = credentials + "/baseline-target";
        if (cases[i].source == SourceKind::Regular)
        {
            WriteFile(baselinePath, cases[i].contents);
            assert(::chmod(baselinePath.c_str(), cases[i].mode) == 0);
        }
        else if (cases[i].source == SourceKind::Symlink)
        {
            WriteFile(symlinkTarget, cases[i].contents);
            assert(::symlink(symlinkTarget.c_str(), baselinePath.c_str()) == 0);
        }

        const std::string stem =
            std::string("hepta-hfx-parser-") + cases[i].name;
        const std::string socketPath = SocketPath(stem.c_str());
        const std::string eventPath = SocketPath((stem + "-events").c_str());
        const std::shared_ptr<FakeBrokerState> broker(new FakeBrokerState());
        broker->positionQuantity = cases[i].brokerCashBalance;
        const std::shared_ptr<MutableKillSwitch> killSwitch(
            new MutableKillSwitch());
        {
            IbPaperExecutionRuntimeConfig config = Config(
                ActivatedSocket(socketPath), ActivatedSocket(eventPath),
                state, credentials);
            // Exercise the production credential parser rather than the
            // injected-record test seam.
            config.fxCashBaselines.clear();
            IbPaperExecutionRuntimeComposition runtime(
                config,
                std::unique_ptr<IIBApiWrapper>(new FakeIbWrapper(broker)),
                IbPaperExecutionRuntimeTestHooks(), killSwitch);
            std::string reason;
            const bool started = runtime.Start(reason);
            if (started != cases[i].starts ||
                (!started && reason != cases[i].reason))
                std::cerr << "HFX parser case=" << cases[i].name
                          << " started=" << started
                          << " reason=" << reason << std::endl;
            assert(started == cases[i].starts);
            if (started)
                assert(reason.empty());
            else
                assert(reason == cases[i].reason);
            assert((broker->snapshotRequests.load() > 0) ==
                   cases[i].reachesBroker);
            assert(broker->sends == 0);
            runtime.Stop();
        }

        ::unlink(socketPath.c_str());
        ::unlink(eventPath.c_str());
        ::unlink((state + "/oms-journal.jsonl").c_str());
        ::unlink((state + "/ib-paper-runtime.lock").c_str());
        ::unlink((state + "/ib-observability.jsonl").c_str());
        ::unlink(FxCashRestartCheckpointPath(state).c_str());
        ::unlink(baselinePath.c_str());
        ::unlink(symlinkTarget.c_str());
        ::unlink((credentials + "/hepta-execution-fence").c_str());
        ::unlink((credentials +
                  "/hepta-ib-paper-authorization").c_str());
        assert(::rmdir(credentials.c_str()) == 0);
        assert(::rmdir(state.c_str()) == 0);
    }
}

void TestFxCashRestartCheckpointRejectsInvalidTamperedAndStaleState()
{
    const std::string baselineRecord = HfxRecord(
        "DU123456", "EUR.USD", "EUR", "0", "0", "0", "100");
    const std::string baselineProof = baselineRecord.substr(
        baselineRecord.rfind('|') + 1);
    const std::string validCheckpoint = "HFXR1\n" + HfxRestartRecord(
        "DU123456", "EUR.USD", "EUR", "0", "0", "0", "101",
        baselineProof) + "\n";
    std::string tamperedCheckpoint = validCheckpoint;
    assert(tamperedCheckpoint.size() > 2);
    tamperedCheckpoint[tamperedCheckpoint.size() - 2] =
        tamperedCheckpoint[tamperedCheckpoint.size() - 2] == '0' ? '1' : '0';
    const std::string staleCheckpoint = "HFXR1\n" + HfxRestartRecord(
        "DU123456", "EUR.USD", "EUR", "0", "0", "0", "99",
        baselineProof) + "\n";
    struct CheckpointCase
    {
        const char* name;
        std::string contents;
        const char* reason;
    };
    const CheckpointCase cases[] = {
        {"invalid", "HFXR0\n", "IB_FX_CASH_RESTART_CHECKPOINT_INVALID"},
        {"tampered", tamperedCheckpoint,
         "IB_FX_CASH_RESTART_CHECKPOINT_PROOF_MISMATCH"},
        {"stale", staleCheckpoint,
         "IB_FX_CASH_RESTART_CHECKPOINT_STALE"},
    };

    for (std::size_t i = 0; i < sizeof(cases) / sizeof(cases[0]); ++i)
    {
        const std::string state = TempDirectory(
            "/tmp/hepta-hfx-restart-checkpoint-state-XXXXXX");
        const std::string credentials = TempDirectory(
            "/tmp/hepta-hfx-restart-checkpoint-cred-XXXXXX");
        WriteFile(credentials + "/hepta-execution-fence",
            "HFC1\nfencing_token=77\ngeneration=9\n");
        IbPaperExecutionRuntimeConfig bootstrap =
            Config(-1, -1, state, credentials);
        std::string authorization;
        std::string reason;
        assert(bootstrap.profile.BuildAuthorizationCredential(
            authorization, reason));
        WriteFile(credentials + "/hepta-ib-paper-authorization",
            authorization + "\n");
        WriteFile(credentials + "/hepta-fx-cash-baseline",
            "HFX1\n" + baselineRecord + "\n");
        WritePrivateStateFile(FxCashRestartCheckpointPath(state),
            cases[i].contents);

        const std::string stem =
            std::string("hepta-hfx-restart-checkpoint-") + cases[i].name;
        const std::string socketPath = SocketPath(stem.c_str());
        const std::string eventPath =
            SocketPath((stem + "-events").c_str());
        const std::shared_ptr<FakeBrokerState> broker(new FakeBrokerState());
        const std::shared_ptr<MutableKillSwitch> killSwitch(
            new MutableKillSwitch());
        IbPaperExecutionRuntimeConfig config = Config(
            ActivatedSocket(socketPath), ActivatedSocket(eventPath),
            state, credentials);
        config.fxCashBaselines.clear();
        IbPaperExecutionRuntimeComposition runtime(
            config,
            std::unique_ptr<IIBApiWrapper>(new FakeIbWrapper(broker)),
            IbPaperExecutionRuntimeTestHooks(), killSwitch);
        assert(!runtime.Start(reason));
        assert(reason == cases[i].reason);
        assert(broker->snapshotRequests.load() == 0);
        assert(broker->sends == 0);
        runtime.Stop();

        ::unlink(socketPath.c_str());
        ::unlink(eventPath.c_str());
        ::unlink((state + "/oms-journal.jsonl").c_str());
        ::unlink((state + "/ib-paper-runtime.lock").c_str());
        ::unlink(FxCashRestartCheckpointPath(state).c_str());
        ::unlink((credentials + "/hepta-execution-fence").c_str());
        ::unlink((credentials +
                  "/hepta-ib-paper-authorization").c_str());
        ::unlink((credentials + "/hepta-fx-cash-baseline").c_str());
        assert(::rmdir(credentials.c_str()) == 0);
        assert(::rmdir(state.c_str()) == 0);
    }
}

IbPlaceOrderCommand Place(const std::string& id)
{
    IbPlaceOrderCommand command;
    command.context.agentId = "agent";
    command.context.sessionId = "session";
    command.context.toolCallId = id;
    command.context.strategy = "offline-fake-ib";
    command.context.account = "DU123456";
    command.context.venue = "IB";
    command.context.executionDomain = "PAPER";
    command.context.decisionLeaseFencingToken = 77;
    command.context.decisionLeaseGeneration = 9;
    command.contract.symbol = "EUR";
    command.contract.secType = "CASH";
    command.contract.exchange = "IDEALPRO";
    command.contract.currency = "USD";
    command.instrument = "EUR.USD";
    command.order.action = "BUY";
    command.order.orderType = "MKT";
    command.order.totalQuantity = 100.0;
    command.order.lmtPrice = 0.0;
    command.timeInForce = "DAY";
    command.referencePrice = 1.1;
    command.expiresAtMs = OmsJournal::NowEpochMs() + 60000;
    return command;
}

std::string PreviewField(const ExecutionCommandResult& preview,
                         const std::string& name)
{
    const std::string marker = "\"" + name + "\":\"";
    const std::size_t begin = preview.detail.find(marker);
    assert(begin != std::string::npos);
    const std::size_t value = begin + marker.size();
    const std::size_t end = preview.detail.find('"', value);
    assert(end != std::string::npos);
    return preview.detail.substr(value, end - value);
}

IbPlaceOrderCommand Previewed(
    UnixExecutionServiceClient& client,
    const IbPlaceOrderCommand& input)
{
    IbPlaceOrderCommand command = input;
    command.previewPermit.clear();
    const ExecutionCommandResult preview = client.PreviewOrder(command);
    assert(preview.status == ExecutionCommandStatus::Accepted);
    command.previewPermit = PreviewField(preview, "preview_permit");
    command.context.toolCallId = PreviewField(preview, "command_id");
    return command;
}

FlattenPositionCommand Flatten(const std::string& id)
{
    const IbPlaceOrderCommand place = Place(id);
    FlattenPositionCommand command;
    command.context = place.context;
    command.contract = place.contract;
    command.instrument = place.instrument;
    return command;
}

FlattenPositionCommand PreviewedFlatten(
    UnixExecutionServiceClient& client,
    const FlattenPositionCommand& input)
{
    FlattenPositionCommand command = input;
    command.previewPermit.clear();
    const ExecutionCommandResult preview =
        client.PreviewFlattenPosition(command);
    assert(preview.status == ExecutionCommandStatus::Accepted);
    command.previewPermit = PreviewField(preview, "preview_permit");
    command.context.toolCallId = PreviewField(preview, "command_id");
    return command;
}

void WriteExternalRuntimeCredentials(
    const IbPaperExecutionRuntimeConfig& config,
    const std::string& credentials)
{
    WriteFile(credentials + "/hepta-execution-fence",
        "HFC1\nfencing_token=77\ngeneration=9\n");
    std::string authorization;
    std::string reason;
    assert(config.profile.BuildAuthorizationCredential(
        authorization, reason));
    assert(authorization.compare(0, 16, "PAPER-V4:sha256:") == 0);
    WriteFile(credentials + "/hepta-ib-paper-authorization",
        authorization + "\n");
}

void CleanupExternalRuntimeFixture(
    const std::string& state, const std::string& credentials,
    const std::string& socketPath, const std::string& eventPath)
{
    ::unlink(socketPath.c_str());
    ::unlink(eventPath.c_str());
    ::unlink((state + "/oms-journal.jsonl").c_str());
    ::unlink((state + "/ib-paper-runtime.lock").c_str());
    ::unlink((state + "/ib-observability.jsonl").c_str());
    ::unlink(FxCashRestartCheckpointPath(state).c_str());
    ::unlink((credentials + "/hepta-execution-fence").c_str());
    ::unlink((credentials +
              "/hepta-ib-paper-authorization").c_str());
    assert(::rmdir(credentials.c_str()) == 0);
    assert(::rmdir(state.c_str()) == 0);
}

void TestExternalLimitDayRuntimeFinalSend()
{
    {
        const std::string state = TempDirectory(
            "/tmp/hepta-ib-external-entry-state-XXXXXX");
        const std::string credentials = TempDirectory(
            "/tmp/hepta-ib-external-entry-cred-XXXXXX");
        const std::string socketPath =
            SocketPath("hepta-ib-external-entry");
        const std::string eventPath =
            SocketPath("hepta-ib-external-entry-events");
        IbPaperExecutionRuntimeConfig config = ExternalLimitConfig(
            ActivatedSocket(socketPath), ActivatedSocket(eventPath),
            state, credentials);
        WriteExternalRuntimeCredentials(config, credentials);
        const std::shared_ptr<FakeBrokerState> broker(
            new FakeBrokerState());
        const std::shared_ptr<MutableKillSwitch> killSwitch(
            new MutableKillSwitch());
        const std::shared_ptr<std::atomic<bool> > driftNext(
            new std::atomic<bool>(false));
        const std::shared_ptr<std::atomic<int> > appliedQuoteTicks(
            new std::atomic<int>(0));
        IbPaperExecutionRuntimeTestHooks hooks;
        hooks.onStage = [broker, driftNext, appliedQuoteTicks](
                            const char* stage) {
            if (std::strcmp(
                    stage, "authoritative_quote_tick_applied") == 0)
            {
                ++(*appliedQuoteTicks);
                return;
            }
            if (std::strcmp(stage, "before_venue_send") != 0 ||
                !driftNext->exchange(false))
                return;
            const int expected = appliedQuoteTicks->load() + 1;
            InjectQuoteTick(broker, "2", 1.1003);
            WaitForQuoteBarrier(broker);
            WaitForAppliedQuoteTicks(appliedQuoteTicks, expected);
        };
        IbPaperExecutionRuntimeComposition runtime(
            config,
            std::unique_ptr<IIBApiWrapper>(new FakeIbWrapper(broker)),
            hooks, killSwitch);
        std::string reason;
        assert(runtime.Start(reason));
        UnixExecutionServiceClient client(socketPath, 3000);

        ExecutionReadCommand healthRead;
        healthRead.context = Place("external-health-one").context;
        healthRead.query = "system.get_health";
        const ExecutionCommandResult connectedHealth =
            client.ReadAuthoritativeState(healthRead);
        assert(connectedHealth.status ==
            ExecutionCommandStatus::Accepted);
        assert(connectedHealth.detail ==
            "{\"source\":\"IB\",\"authoritative\":true,"
            "\"paper_order_mode\":\"EXTERNAL_P1_CANARY_LMT_DAY\","
            "\"authorized_connector_count\":1}");

        IbPlaceOrderCommand market = Place("external-entry-mkt-reject");
        market.order.totalQuantity = 1.0;
        const ExecutionCommandResult marketRejected =
            client.PreviewOrder(market);
        assert(marketRejected.status ==
            ExecutionCommandStatus::Rejected);
        assert(marketRejected.reasonCode ==
            "IB_PAPER_EXTERNAL_LIMIT_ORDERS_ONLY");

        IbPlaceOrderCommand hiddenField =
            Place("external-entry-hidden-field");
        hiddenField.order.totalQuantity = 1.0;
        hiddenField.order.orderType = "LMT";
        hiddenField.order.lmtPrice = 1.1002;
        hiddenField.referencePrice = 1.1002;
        hiddenField.order.auxPrice = 1.0;
        assert(client.PreviewOrder(hiddenField).reasonCode ==
            "IB_PAPER_EXTERNAL_ORDER_FIELDS_INVALID");
        hiddenField.order.auxPrice = 0.0;
        hiddenField.order.outsideRth = true;
        assert(client.PreviewOrder(hiddenField).reasonCode ==
            "IB_PAPER_EXTERNAL_ORDER_FIELDS_INVALID");

        IbPlaceOrderCommand wrongLimit =
            Place("external-entry-wrong-limit");
        wrongLimit.order.totalQuantity = 1.0;
        wrongLimit.order.orderType = "LMT";
        wrongLimit.order.lmtPrice = 1.1001;
        wrongLimit.referencePrice = 1.1001;
        assert(client.PreviewOrder(wrongLimit).reasonCode ==
            "IB_PAPER_EXTERNAL_LIMIT_PRICE_MISMATCH");

        IbPlaceOrderCommand drift = Place("external-entry-quote-drift");
        drift.order.totalQuantity = 1.0;
        drift.order.orderType = "LMT";
        drift.order.lmtPrice = 1.1002;
        drift.referencePrice = 1.1002;
        ExecutionCommandResult driftPreview = client.PreviewOrder(drift);
        assert(driftPreview.status == ExecutionCommandStatus::Accepted);
        assert(driftPreview.detail.find("\"order_type\":\"LMT\"") !=
            std::string::npos);
        assert(driftPreview.detail.find("\"quote_bid\":1.1") !=
            std::string::npos);
        assert(driftPreview.detail.find("\"quote_ask\":1.1002") !=
            std::string::npos);
        drift.previewPermit = PreviewField(
            driftPreview, "preview_permit");
        drift.context.toolCallId = PreviewField(
            driftPreview, "command_id");
        driftNext->store(true);
        const ExecutionCommandResult driftRejected =
            client.PlaceIbOrder(drift);
        assert(driftRejected.status ==
            ExecutionCommandStatus::Rejected);
        assert(driftRejected.reasonCode ==
            "IB_PAPER_PLACE_QUOTE_CHANGED_BEFORE_SEND");
        assert(broker->sends == 0);
        const ExecutionCommandResult entryPermitReplay =
            client.PlaceIbOrder(drift);
        assert(entryPermitReplay.status ==
            ExecutionCommandStatus::Rejected);
        assert(entryPermitReplay.reasonCode ==
            "EXECUTION_PREVIEW_PERMIT_UNKNOWN_OR_CONSUMED");
        assert(broker->sends == 0);

        const int resetApplied = appliedQuoteTicks->load() + 1;
        InjectQuoteTick(broker, "2", 1.1002);
        WaitForQuoteBarrier(broker);
        WaitForAppliedQuoteTicks(appliedQuoteTicks, resetApplied);
        IbPlaceOrderCommand exact = Place("external-entry-exact");
        exact.order.totalQuantity = 1.0;
        exact.order.orderType = "LMT";
        exact.order.lmtPrice = 1.1002;
        exact.referencePrice = 1.1002;
        exact = Previewed(client, exact);
        const ExecutionCommandResult accepted =
            client.PlaceIbOrder(exact);
        assert(accepted.status == ExecutionCommandStatus::Accepted);
        assert(broker->sends == 1);
        assert(broker->openOrders.at(accepted.orderId).orderType == "LMT");
        assert(broker->openOrders.at(accepted.orderId).lmtPrice == 1.1002);
        assert(broker->openOrders.at(accepted.orderId).totalQuantity == 1.0);
        broker->connectorVisible.store(false);
        healthRead.context.toolCallId = "external-health-zero";
        const ExecutionCommandResult disconnectedHealth =
            client.ReadAuthoritativeState(healthRead);
        assert(disconnectedHealth.status ==
            ExecutionCommandStatus::Accepted);
        assert(disconnectedHealth.detail ==
            "{\"source\":\"IB\",\"authoritative\":true,"
            "\"paper_order_mode\":\"EXTERNAL_P1_CANARY_LMT_DAY\","
            "\"authorized_connector_count\":0}");
        runtime.Stop();
        CleanupExternalRuntimeFixture(
            state, credentials, socketPath, eventPath);
    }

    {
        const std::string state = TempDirectory(
            "/tmp/hepta-ib-external-flatten-state-XXXXXX");
        const std::string credentials = TempDirectory(
            "/tmp/hepta-ib-external-flatten-cred-XXXXXX");
        const std::string socketPath =
            SocketPath("hepta-ib-external-flatten");
        const std::string eventPath =
            SocketPath("hepta-ib-external-flatten-events");
        IbPaperExecutionRuntimeConfig config = ExternalLimitConfig(
            ActivatedSocket(socketPath), ActivatedSocket(eventPath),
            state, credentials);
        config.fxCashBaselines["EUR.USD"].observedCashBalance = 0.75;
        config.fxCashBaselines["EUR.USD"].campaignExecutionDelta = 0.75;
        WriteExternalRuntimeCredentials(config, credentials);
        const std::shared_ptr<FakeBrokerState> broker(
            new FakeBrokerState());
        broker->positionQuantity = 0.75;
        const std::shared_ptr<MutableKillSwitch> killSwitch(
            new MutableKillSwitch());
        const std::shared_ptr<std::atomic<bool> > driftNext(
            new std::atomic<bool>(false));
        const std::shared_ptr<std::atomic<int> > appliedQuoteTicks(
            new std::atomic<int>(0));
        IbPaperExecutionRuntimeTestHooks hooks;
        hooks.onStage = [broker, driftNext, appliedQuoteTicks](
                            const char* stage) {
            if (std::strcmp(
                    stage, "authoritative_quote_tick_applied") == 0)
            {
                ++(*appliedQuoteTicks);
                return;
            }
            if (std::strcmp(stage,
                    "before_flatten_venue_send") != 0 ||
                !driftNext->exchange(false))
                return;
            const int expected = appliedQuoteTicks->load() + 1;
            InjectQuoteTick(broker, "1", 1.0999);
            WaitForQuoteBarrier(broker);
            WaitForAppliedQuoteTicks(appliedQuoteTicks, expected);
        };
        IbPaperExecutionRuntimeComposition runtime(
            config,
            std::unique_ptr<IIBApiWrapper>(new FakeIbWrapper(broker)),
            hooks, killSwitch);
        std::string reason;
        assert(runtime.Start(reason));
        UnixExecutionServiceClient client(socketPath, 3000);

        FlattenPositionCommand drift =
            Flatten("external-flatten-quote-drift");
        ExecutionCommandResult driftPreview =
            client.PreviewFlattenPosition(drift);
        assert(driftPreview.status == ExecutionCommandStatus::Accepted);
        assert(driftPreview.detail.find("\"order_type\":\"LMT\"") !=
            std::string::npos);
        assert(driftPreview.detail.find("\"tif\":\"DAY\"") !=
            std::string::npos);
        assert(driftPreview.detail.find("\"limit_price\":1.1") !=
            std::string::npos);
        assert(driftPreview.detail.find("\"quote_bid\":1.1") !=
            std::string::npos);
        assert(driftPreview.detail.find("\"quote_ask\":1.1002") !=
            std::string::npos);
        assert(driftPreview.detail.find("\"atomic\":true") !=
            std::string::npos);
        drift.previewPermit = PreviewField(
            driftPreview, "preview_permit");
        drift.context.toolCallId = PreviewField(
            driftPreview, "command_id");
        driftNext->store(true);
        const ExecutionCommandResult driftRejected =
            client.FlattenPosition(drift);
        assert(driftRejected.status == ExecutionCommandStatus::Rejected);
        assert(driftRejected.reasonCode ==
            "IB_PAPER_FLATTEN_QUOTE_CHANGED_BEFORE_SEND");
        assert(broker->sends == 0);
        const ExecutionCommandResult flattenPermitReplay =
            client.FlattenPosition(drift);
        assert(flattenPermitReplay.status ==
            ExecutionCommandStatus::Duplicate);
        assert(flattenPermitReplay.reasonCode ==
            "DUPLICATE_TOOL_CALL");
        assert(broker->sends == 0);

        const int resetApplied = appliedQuoteTicks->load() + 1;
        InjectQuoteTick(broker, "1", 1.1000);
        WaitForQuoteBarrier(broker);
        WaitForAppliedQuoteTicks(appliedQuoteTicks, resetApplied);
        FlattenPositionCommand exact = PreviewedFlatten(
            client, Flatten("external-flatten-exact"));
        const ExecutionCommandResult accepted =
            client.FlattenPosition(exact);
        assert(accepted.status == ExecutionCommandStatus::Accepted);
        assert(broker->sends == 1);
        const IBOrderLite& sent = broker->openOrders.at(accepted.orderId);
        assert(sent.action == "SELL");
        assert(sent.orderType == "LMT");
        assert(sent.totalQuantity == 0.75);
        assert(sent.lmtPrice == 1.1000);
        const ExecutionCommandResult acceptedPermitReplay =
            client.FlattenPosition(exact);
        assert(acceptedPermitReplay.status ==
            ExecutionCommandStatus::Duplicate);
        assert(acceptedPermitReplay.reasonCode ==
            "DUPLICATE_TOOL_CALL");
        assert(broker->sends == 1);
        runtime.Stop();
        CleanupExternalRuntimeFixture(
            state, credentials, socketPath, eventPath);
    }
}

void AssertFatalState(
    IbPaperExecutionRuntimeComposition& runtime,
    UnixExecutionServiceClient& cachedMutationClient,
    UnixExecutionEventFeedClient& eventClient,
    const ExecutionServiceIdentity& serviceIdentity,
    const std::string& mutationSocket,
    const std::shared_ptr<FakeBrokerState>& broker,
    const std::string& expectedFatalReason,
    const std::string& commandId)
{
    std::string fatalReason;
    for (int attempt = 0; attempt < 100 &&
         !runtime.HasFatalRuntimeError(&fatalReason); ++attempt)
        std::this_thread::sleep_for(std::chrono::milliseconds(10));
    assert(runtime.HasFatalRuntimeError(&fatalReason));
    assert(fatalReason == expectedFatalReason);
    assert(!runtime.IsRunning());

    std::string blockedReason;
    assert(runtime.IsMutationBlocked(&blockedReason));
    assert(blockedReason == expectedFatalReason);

    const int sendsBefore = broker->sends;
    const ExecutionCommandResult blocked =
        cachedMutationClient.PlaceIbOrder(Place(commandId));
    assert(blocked.status == ExecutionCommandStatus::Rejected);
    assert(blocked.reasonCode == "EXECUTION_SERVICE_NOT_READY");
    assert(broker->sends == sendsBefore);

    UnixExecutionServiceClient freshMutationClient(mutationSocket, 1000);
    ExecutionServiceIdentity ignoredIdentity;
    std::string identityReason;
    assert(!freshMutationClient.GetServiceIdentity(ignoredIdentity, identityReason));
    assert(identityReason == "EXECUTION_SERVICE_NOT_READY");

    const ExecutionEventReadResult identityResult =
        eventClient.GetServiceIdentity();
    assert(identityResult.status == ExecutionEventReadStatus::ServiceNotReady);
    assert(identityResult.reasonCode == "EXECUTION_EVENT_SERVICE_NOT_READY");

    ExecutionEventFeedRequest waitRequest;
    waitRequest.executionDomain = "PAPER";
    waitRequest.agentId = "agent";
    waitRequest.sessionId = "session";
    waitRequest.expectedServiceIdentity = serviceIdentity;
    const ExecutionEventReadResult waitResult = eventClient.Wait(waitRequest);
    assert(waitResult.status == ExecutionEventReadStatus::ServiceNotReady);
    assert(waitResult.reasonCode == "EXECUTION_EVENT_SERVICE_NOT_READY");
    assert(broker->sends == sendsBefore);
}

void TestBrokerThrowAfterSideEffectReconcilesByCorrelationOnly()
{
    const std::string state = TempDirectory(
        "/tmp/hepta-ib-runtime-throw-state-XXXXXX");
    const std::string credentials = TempDirectory(
        "/tmp/hepta-ib-runtime-throw-cred-XXXXXX");
    WriteFile(credentials + "/hepta-execution-fence",
        "HFC1\nfencing_token=77\ngeneration=9\n");
    const IbPaperExecutionRuntimeConfig credentialConfig =
        Config(-1, -1, state, credentials);
    std::string authorization;
    std::string reason;
    assert(credentialConfig.profile.BuildAuthorizationCredential(
        authorization, reason));
    WriteFile(credentials + "/hepta-ib-paper-authorization",
        authorization + "\n");
    const std::string socketPath =
        SocketPath("hepta-ib-throw-after-side-effect");
    const std::string eventPath =
        SocketPath("hepta-ib-throw-after-side-effect-events");
    const std::shared_ptr<FakeBrokerState> broker(
        new FakeBrokerState());
    const std::shared_ptr<MutableKillSwitch> killSwitch(
        new MutableKillSwitch());
    IbPlaceOrderCommand persisted;
    {
        IbPaperExecutionRuntimeComposition runtime(
            Config(ActivatedSocket(socketPath),
                   ActivatedSocket(eventPath), state, credentials),
            std::unique_ptr<IIBApiWrapper>(
                new FakeIbWrapper(broker)),
            IbPaperExecutionRuntimeTestHooks(), killSwitch);
        assert(runtime.Start(reason));
        WaitForQuoteBarrier(broker);
        UnixExecutionServiceClient client(socketPath, 1000);
        persisted = Previewed(
            client, Place("paper-place-throw-after-side-effect"));
        broker->throwAfterPlaceSideEffect.store(true);
        const ExecutionCommandResult uncertain =
            client.PlaceIbOrder(persisted);
        assert(uncertain.status ==
               ExecutionCommandStatus::Uncertain);
        assert(uncertain.reasonCode ==
               "IB_PLACE_OUTCOME_UNCERTAIN");
        assert(broker->sends == 1);
        assert(broker->openOrders.size() == 1);
        assert(!broker->openOrders.begin()->second.orderRef.empty());

        const ExecutionCommandResult blocked =
            client.PreviewOrder(
                Place("paper-place-blocked-before-reconcile"));
        assert(blocked.status ==
               ExecutionCommandStatus::Rejected);
        assert(blocked.reasonCode ==
               "RECOVERY_RECONCILE_REQUIRED");
        assert(broker->sends == 1);
        runtime.Stop();
    }

    ::unlink(socketPath.c_str());
    ::unlink(eventPath.c_str());
    {
        IbPaperExecutionRuntimeComposition restarted(
            Config(ActivatedSocket(socketPath),
                   ActivatedSocket(eventPath), state, credentials),
            std::unique_ptr<IIBApiWrapper>(
                new FakeIbWrapper(broker)),
            IbPaperExecutionRuntimeTestHooks(), killSwitch);
        assert(restarted.Start(reason));
        UnixExecutionServiceClient client(socketPath, 1000);
        const ExecutionCommandResult duplicate =
            client.PlaceIbOrder(persisted);
        assert(duplicate.status ==
               ExecutionCommandStatus::Duplicate);
        assert(duplicate.orderId ==
               broker->openOrders.begin()->first);
        assert(broker->sends == 1);
        const ExecutionCommandResult unblocked =
            client.PreviewOrder(
                Place("paper-place-unblocked-after-reconcile"));
        assert(unblocked.status ==
               ExecutionCommandStatus::Accepted);
        assert(broker->sends == 1);
        restarted.Stop();
    }

    ::unlink(socketPath.c_str());
    ::unlink(eventPath.c_str());
    ::unlink((state + "/oms-journal.jsonl").c_str());
    ::unlink((state + "/ib-paper-runtime.lock").c_str());
    ::unlink((state + "/ib-observability.jsonl").c_str());
    ::unlink(FxCashRestartCheckpointPath(state).c_str());
    ::unlink((credentials + "/hepta-execution-fence").c_str());
    ::unlink((credentials +
              "/hepta-ib-paper-authorization").c_str());
    assert(::rmdir(credentials.c_str()) == 0);
    assert(::rmdir(state.c_str()) == 0);
}

void TestOrdersListPreservesGlobalAndProjectsExactSessionOwner()
{
    const std::string state = TempDirectory(
        "/tmp/hepta-ib-owner-orders-state-XXXXXX");
    const std::string credentials = TempDirectory(
        "/tmp/hepta-ib-owner-orders-cred-XXXXXX");
    WriteFile(credentials + "/hepta-execution-fence",
        "HFC1\nfencing_token=77\ngeneration=9\n");
    const IbPaperExecutionRuntimeConfig base =
        Config(-1, -1, state, credentials);
    std::string authorization;
    std::string reason;
    assert(base.profile.BuildAuthorizationCredential(authorization, reason));
    WriteFile(credentials + "/hepta-ib-paper-authorization",
        authorization + "\n");
    const std::string socketPath = SocketPath("hepta-ib-owner-orders");
    const std::string eventPath =
        SocketPath("hepta-ib-owner-orders-events");
    const std::shared_ptr<FakeBrokerState> broker(new FakeBrokerState());
    const std::shared_ptr<MutableKillSwitch> killSwitch(
        new MutableKillSwitch());
    const int ownerOrdersIpcTimeoutMs = 30000;
    IbPaperExecutionRuntimeConfig ownerConfig = Config(
        ActivatedSocket(socketPath), ActivatedSocket(eventPath),
        state, credentials);
    ownerConfig.ioTimeoutMs = ownerOrdersIpcTimeoutMs;
    // This fixture exercises owner projection, not the five-second quote-age
    // boundary. Keep its freshly injected quote valid across loaded-host
    // durable journal syncs; dedicated 250ms tests below prove fail-closed
    // quote expiry and final-send drift.
    ownerConfig.quoteMaxAgeMs = 30000;
    const std::shared_ptr<std::atomic<int> > appliedQuoteTicks(
        new std::atomic<int>(0));
    IbPaperExecutionRuntimeTestHooks ownerHooks;
    ownerHooks.onStage = [appliedQuoteTicks](const char* stage) {
        if (std::strcmp(
                stage, "authoritative_quote_tick_applied") == 0)
            ++(*appliedQuoteTicks);
    };
    IbPaperExecutionRuntimeComposition runtime(
        ownerConfig,
        std::unique_ptr<IIBApiWrapper>(new FakeIbWrapper(broker)),
        ownerHooks, killSwitch);
    assert(runtime.Start(reason));
    UnixExecutionServiceClient client(
        socketPath, ownerOrdersIpcTimeoutMs);
    const auto refreshQuote = [&broker, appliedQuoteTicks]() {
        const int appliedBefore = appliedQuoteTicks->load();
        InjectQuoteTick(broker, "1", 1.1000);
        InjectQuoteTick(broker, "2", 1.1002);
        WaitForQuoteBarrier(broker);
        WaitForAppliedQuoteTicks(appliedQuoteTicks, appliedBefore + 2);
    };

    refreshQuote();
    const IbPlaceOrderCommand first = Previewed(
        client, Place("owner-orders-first"));
    const ExecutionCommandResult firstPlaced = client.PlaceIbOrder(first);
    assert(firstPlaced.status == ExecutionCommandStatus::Accepted);
    ExecutionControlCommand fence;
    fence.context = first.context;
    fence.context.toolCallId = "owner-orders-fence-first";
    const ExecutionControlResult fenced = client.FenceSessionOwner(fence);
    assert(fenced.status == ExecutionCommandStatus::Accepted);
    assert(fenced.affectedCount == 1);

    IbPlaceOrderCommand secondInput = Place("owner-orders-second");
    secondInput.context.sessionId = "session-two";
    secondInput.order.totalQuantity = 101.0;
    refreshQuote();
    const IbPlaceOrderCommand second = Previewed(client, secondInput);
    const ExecutionCommandResult secondPlaced = client.PlaceIbOrder(second);
    if (secondPlaced.status != ExecutionCommandStatus::Accepted)
        std::cerr << "second owner place rejected: "
                  << secondPlaced.reasonCode << " detail="
                  << secondPlaced.detail << '\n';
    assert(secondPlaced.status == ExecutionCommandStatus::Accepted);
    assert(firstPlaced.orderId != secondPlaced.orderId);
    const long lower = std::min(firstPlaced.orderId, secondPlaced.orderId);
    const long upper = std::max(firstPlaced.orderId, secondPlaced.orderId);
    const std::string global = "\"active_order_ids\":[" +
        std::to_string(lower) + "," + std::to_string(upper) + "]";
    const auto readFor = [&client](const AgentExecutionContext& context,
                                   const char* id) {
        ExecutionReadCommand read;
        read.context = context;
        read.context.toolCallId = id;
        read.query = "orders.list";
        return client.ReadAuthoritativeState(read);
    };
    const ExecutionCommandResult firstRead =
        readFor(first.context, "owner-orders-read-first");
    const ExecutionCommandResult secondRead =
        readFor(second.context, "owner-orders-read-second");
    assert(firstRead.status == ExecutionCommandStatus::Accepted);
    assert(secondRead.status == ExecutionCommandStatus::Accepted);
    assert(firstRead.detail.find(global) != std::string::npos);
    assert(secondRead.detail.find(global) != std::string::npos);
    assert(firstRead.detail.find("\"owned_active_order_ids\":[" +
        std::to_string(firstPlaced.orderId) + "]") != std::string::npos);
    assert(secondRead.detail.find("\"owned_active_order_ids\":[" +
        std::to_string(secondPlaced.orderId) + "]") != std::string::npos);
    for (const std::string* detail :
         {&firstRead.detail, &secondRead.detail})
    {
        assert(detail->find("\"authoritative\":true") != std::string::npos);
        assert(detail->find("\"global_active_orders_complete\":true") !=
            std::string::npos);
        assert(detail->find("\"owner_projection_complete\":true") !=
            std::string::npos);
        assert(detail->find("\"active_orders_connection_epoch\":") !=
            std::string::npos);
        assert(detail->find("\"active_orders_connection_epoch\":0") ==
            std::string::npos);
        assert(detail->find("\"active_orders_generation\":") !=
            std::string::npos);
    }

    const long foreignOrderId = 9000;
    IBOrderLite foreign = broker->openOrders.begin()->second;
    foreign.orderRef.clear();
    broker->openOrders[foreignOrderId] = foreign;
    const IBAuthoritativeCorrelationSnapshot before =
        runtime.Adapter().GetAuthoritativeCorrelationSnapshot();
    assert(runtime.Adapter().ReqAuthoritativeOpenOrders());
    IBAuthoritativeCorrelationSnapshot refreshed;
    for (int attempt = 0; attempt < 1000; ++attempt)
    {
        refreshed = runtime.Adapter().GetAuthoritativeCorrelationSnapshot();
        if (refreshed.complete && refreshed.generation > before.generation &&
            refreshed.activeOrderIds.count(foreignOrderId) == 1)
            break;
        std::this_thread::sleep_for(std::chrono::milliseconds(1));
    }
    assert(refreshed.complete);
    assert(refreshed.activeOrderIds.count(foreignOrderId) == 1);
    const std::string globalWithForeign = "\"active_order_ids\":[" +
        std::to_string(lower) + "," + std::to_string(upper) + "," +
        std::to_string(foreignOrderId) + "]";
    const ExecutionCommandResult incomplete =
        readFor(second.context, "owner-orders-read-unmapped");
    assert(incomplete.status == ExecutionCommandStatus::Accepted);
    assert(incomplete.detail.find(globalWithForeign) != std::string::npos);
    assert(incomplete.detail.find("\"owned_active_order_ids\":[" +
        std::to_string(secondPlaced.orderId) + "]") != std::string::npos);
    assert(incomplete.detail.find("\"unmapped_active_order_ids\":[9000]") !=
        std::string::npos);
    assert(incomplete.detail.find("\"authoritative\":false") !=
        std::string::npos);
    assert(incomplete.detail.find("\"global_active_orders_complete\":true") !=
        std::string::npos);
    assert(incomplete.detail.find("\"owner_projection_complete\":false") !=
        std::string::npos);
    assert(incomplete.detail.find(
        "\"reason_code\":\"EXECUTION_ORDER_OWNER_PROJECTION_INCOMPLETE\"") !=
        std::string::npos);
    runtime.Stop();

    ::unlink(socketPath.c_str());
    ::unlink(eventPath.c_str());
    ::unlink((state + "/oms-journal.jsonl").c_str());
    ::unlink((state + "/ib-paper-runtime.lock").c_str());
    ::unlink((state + "/ib-observability.jsonl").c_str());
    ::unlink(FxCashRestartCheckpointPath(state).c_str());
    ::unlink((credentials + "/hepta-execution-fence").c_str());
    ::unlink((credentials + "/hepta-ib-paper-authorization").c_str());
    assert(::rmdir(credentials.c_str()) == 0);
    assert(::rmdir(state.c_str()) == 0);
}

void TestBrokerEvidencePublishesPersistsAndIsOwnerScoped()
{
    const std::string state = TempDirectory(
        "/tmp/hepta-ib-runtime-evidence-state-XXXXXX");
    const std::string credentials = TempDirectory(
        "/tmp/hepta-ib-runtime-evidence-cred-XXXXXX");
    WriteFile(credentials + "/hepta-execution-fence",
        "HFC1\nfencing_token=77\ngeneration=9\n");
    const IbPaperExecutionRuntimeConfig base =
        Config(-1, -1, state, credentials);
    std::string authorization;
    std::string reason;
    assert(base.profile.BuildAuthorizationCredential(
        authorization, reason));
    WriteFile(credentials + "/hepta-ib-paper-authorization",
        authorization + "\n");
    const std::string baselineRecord = HfxRecord(
        "DU123456", "EUR.USD", "EUR", "0", "0", "0", "1");
    WriteFile(credentials + "/hepta-fx-cash-baseline",
        "HFX1\n" + baselineRecord + "\n");
    const std::string socketPath =
        SocketPath("hepta-ib-runtime-evidence");
    const std::string eventPath =
        SocketPath("hepta-ib-runtime-evidence-events");
    const std::shared_ptr<FakeBrokerState> broker(
        new FakeBrokerState());
    const std::shared_ptr<MutableKillSwitch> killSwitch(
        new MutableKillSwitch());
    long orderId = -1;

    {
        IbPaperExecutionRuntimeConfig config = Config(
            ActivatedSocket(socketPath), ActivatedSocket(eventPath),
            state, credentials);
        config.fxCashBaselines.clear();
        IbPaperExecutionRuntimeComposition runtime(
            config,
            std::unique_ptr<IIBApiWrapper>(new FakeIbWrapper(broker)),
            IbPaperExecutionRuntimeTestHooks(), killSwitch);
        assert(runtime.Start(reason));
        WaitForQuoteBarrier(broker);
        UnixExecutionServiceClient client(socketPath, 1000);
        UnixExecutionEventFeedClient events(eventPath, 1000);
        ExecutionServiceIdentity identity;
        assert(client.GetServiceIdentity(identity, reason));
        const IbPlaceOrderCommand command = Previewed(
            client, Place("paper-broker-evidence"));
        const ExecutionCommandResult accepted = client.PlaceIbOrder(command);
        assert(accepted.status == ExecutionCommandStatus::Accepted);
        orderId = accepted.orderId;

        ExecutionEventFeedRequest wait;
        wait.executionDomain = command.context.executionDomain;
        wait.agentId = command.context.agentId;
        wait.sessionId = command.context.sessionId;
        wait.expectedServiceIdentity = identity;
        wait.timeoutMs = 1000;
        std::uint64_t after = 0;
        bool submittedSeen = false;
        for (int attempt = 0; attempt < 8 && !submittedSeen; ++attempt)
        {
            wait.afterSequence = after;
            const ExecutionEventReadResult observed = events.Wait(wait);
            assert(observed.status == ExecutionEventReadStatus::Event);
            after = observed.event.sequence;
            submittedSeen = observed.event.type == "order.status" &&
                observed.event.orderId == orderId &&
                observed.event.status == "Submitted";
        }
        assert(submittedSeen);

        IBEvent held = Event(IBEventType::OrderStatus, orderId);
        held.key = "PreSubmitted";
        held.number = 0.0;
        held.number2 = 0.0;
        held.number3 = 100.0;
        held.whyHeld = "offline paper hold";
        held.marketCapPrice = 1.25;
        {
            std::lock_guard<std::mutex> lock(broker->injectedMutex);
            broker->injectedEvents.push_back(held);
        }
        wait.afterSequence = after;
        const ExecutionEventReadResult heldEvent = events.Wait(wait);
        assert(heldEvent.status == ExecutionEventReadStatus::Event);
        assert(heldEvent.event.type == "order.status");
        assert(heldEvent.event.status == "PreSubmitted");
        assert(heldEvent.event.filledQuantity == 0.0);
        assert(heldEvent.event.remainingQuantity == 100.0);
        assert(heldEvent.event.averageFillPrice == 0.0);
        assert(heldEvent.event.reasonCode == "IB_ORDER_HELD");
        after = heldEvent.event.sequence;

        const int snapshotsBeforeFill = broker->snapshotRequests.load();
        broker->positionQuantity = 40.0;
        IBEvent partial = Event(IBEventType::ExecutionDetails, orderId);
        partial.requestId = -1;
        partial.key = "execution-partial-40";
        partial.value = "BOT";
        partial.number = 1.10010;
        partial.number2 = 40.0;
        partial.number3 = 60.0;
        partial.contract.symbol = "EUR";
        partial.contract.secType = "CASH";
        partial.contract.exchange = "IDEALPRO";
        partial.contract.currency = "USD";
        IBEvent partialStatus = Event(IBEventType::OrderStatus, orderId);
        partialStatus.key = "Submitted";
        partialStatus.number = partial.number;
        partialStatus.number2 = partial.number2;
        partialStatus.number3 = partial.number3;
        IBEvent exactDuplicate = partial;
        exactDuplicate.key = "execution-partial-40-duplicate";
        IBEvent lowerCumulative = partial;
        lowerCumulative.key = "execution-stale-20";
        lowerCumulative.number2 = 20.0;
        lowerCumulative.number3 = 80.0;
        IBEvent cashChanged = Event(IBEventType::AccountValue);
        cashChanged.key = "CashBalance:EUR";
        cashChanged.value = "40";
        {
            std::lock_guard<std::mutex> lock(broker->injectedMutex);
            // IB can publish the changed CashBalance before the economic fill.
            // The fill refresh must use the baseline captured under the final
            // broker-send lock rather than the now-invalid current snapshot.
            broker->injectedEvents.push_back(cashChanged);
            broker->injectedEvents.push_back(partialStatus);
            broker->injectedEvents.push_back(partial);
            broker->injectedEvents.push_back(exactDuplicate);
            broker->injectedEvents.push_back(lowerCumulative);
        }
        wait.afterSequence = after;
        const ExecutionEventReadResult partialStatusEvent = events.Wait(wait);
        assert(partialStatusEvent.status == ExecutionEventReadStatus::Event);
        assert(partialStatusEvent.event.type == "order.status");
        assert(partialStatusEvent.event.status == "Submitted");
        assert(partialStatusEvent.event.filledQuantity == 40.0);
        assert(partialStatusEvent.event.remainingQuantity == 60.0);
        after = partialStatusEvent.event.sequence;
        const double expectedCumulative[] = {40.0, 40.0, 20.0};
        for (std::size_t index = 0; index < 3; ++index)
        {
            wait.afterSequence = after;
            const ExecutionEventReadResult observed = events.Wait(wait);
            assert(observed.status == ExecutionEventReadStatus::Event);
            assert(observed.event.type == "order.fill");
            assert(observed.event.orderId == orderId);
            assert(observed.event.filledQuantity ==
                expectedCumulative[index]);
            after = observed.event.sequence;
        }
        ExecutionOrderOwner retainedOwner;
        assert(runtime.Coordinator().GetOrderOwner(orderId, retainedOwner));
        std::string partialBlockReason;
        assert(runtime.IsMutationBlocked(&partialBlockReason));
        assert(partialBlockReason ==
            "IB_POST_FILL_RISK_REFRESH_PENDING");
        const int snapshotsAfterPartial = broker->snapshotRequests.load();
        assert(snapshotsAfterPartial == snapshotsBeforeFill + 2);
        std::this_thread::sleep_for(std::chrono::milliseconds(3200));
        assert(broker->snapshotRequests.load() == snapshotsAfterPartial);
        assert(!runtime.HasFatalRuntimeError(nullptr));

        // Broker terminal status can precede the final execDetails callback.
        // It must retain the owner, advance the cumulative target exactly
        // once, and reserve a post-completion two-leg refresh.
        broker->staleCashBalance.store(40.0);
        broker->positionQuantity = 100.0;
        broker->conflictingAccountSnapshots.store(1);
        IBEvent filledStatus = Event(IBEventType::OrderStatus, orderId);
        filledStatus.key = "Filled";
        filledStatus.number = 1.10015;
        filledStatus.number2 = 100.0;
        filledStatus.number3 = 0.0;
        IBEvent execution = partial;
        execution.key = "execution-44";
        execution.number = 1.10015;
        execution.number2 = 100.0;
        execution.number3 = 0.0;
        {
            std::lock_guard<std::mutex> lock(broker->injectedMutex);
            broker->injectedEvents.push_back(filledStatus);
            broker->injectedEvents.push_back(execution);
        }
        wait.afterSequence = after;
        const ExecutionEventReadResult statusFirst = events.Wait(wait);
        assert(statusFirst.status == ExecutionEventReadStatus::Event);
        assert(statusFirst.event.type == "order.status");
        assert(statusFirst.event.status == "Filled");
        assert(statusFirst.event.filledQuantity == 100.0);
        after = statusFirst.event.sequence;
        assert(runtime.Coordinator().GetOrderOwner(orderId, retainedOwner));
        wait.afterSequence = after;
        const ExecutionEventReadResult fill = events.Wait(wait);
        assert(fill.status == ExecutionEventReadStatus::Event);
        assert(fill.event.type == "order.fill");
        assert(fill.event.orderId == orderId);
        assert(fill.event.status == "ExecutionDetails");
        assert(fill.event.filledQuantity == 100.0);
        assert(fill.event.remainingQuantity == 0.0);
        assert(fill.event.averageFillPrice == 1.10015);
        after = fill.event.sequence;

        ExecutionReadCommand ordersRead;
        ordersRead.context = command.context;
        ordersRead.context.toolCallId = "paper-read-recent-orders";
        ordersRead.query = "orders.list";
        const ExecutionCommandResult recent =
            client.ReadAuthoritativeState(ordersRead);
        assert(recent.status == ExecutionCommandStatus::Accepted);
        assert(recent.detail.find("\"recent_orders\":[{") !=
            std::string::npos);
        assert(recent.detail.find("\"order_id\":" +
            std::to_string(orderId)) != std::string::npos);
        assert(recent.detail.find("\"economic_fill\":true") !=
            std::string::npos);
        assert(recent.detail.find("\"filled_quantity\":100") !=
            std::string::npos);
        // This order had several cumulative execDetails ids (partial fill,
        // duplicate replay, and final fill). A one-row order projection cannot
        // bind that aggregate to one stable execution id and must fail closed.
        assert(recent.detail.find("\"broker_execution_id\":\"\"") !=
            std::string::npos);
        assert(recent.detail.find(
            "\"broker_execution_ambiguous\":true") !=
            std::string::npos);
        assert(recent.detail.find("\"account\":\"DU123456\"") !=
            std::string::npos);
        assert(recent.detail.find("\"execution_domain\":\"PAPER\"") !=
            std::string::npos);

        ExecutionReadCommand positionsRead;
        positionsRead.context = command.context;
        positionsRead.context.toolCallId =
            "paper-post-fill-risk-reconciled";
        positionsRead.query = "portfolio.list_positions";
        const ExecutionCommandResult pendingPositions =
            client.ReadAuthoritativeState(positionsRead);
        assert(pendingPositions.status == ExecutionCommandStatus::Accepted);
        assert(pendingPositions.detail.find("\"authoritative\":false") !=
            std::string::npos);
        assert(pendingPositions.detail.find(
            "\"reason_code\":\"IB_POST_FILL_RISK_REFRESH_PENDING\"") !=
            std::string::npos);
        std::string postFillBlockReason;
        assert(runtime.IsMutationBlocked(&postFillBlockReason));
        assert(postFillBlockReason == "IB_POST_FILL_RISK_REFRESH_PENDING");
        bool postFillReconciled = false;
        for (int attempt = 0; attempt < 100 && !postFillReconciled; ++attempt)
        {
            const ExecutionCommandResult positions =
                client.ReadAuthoritativeState(positionsRead);
            assert(positions.status == ExecutionCommandStatus::Accepted);
            postFillReconciled =
                positions.detail.find("\"authoritative\":true") !=
                    std::string::npos &&
                positions.detail.find("\"quantity\":100") !=
                    std::string::npos &&
                positions.detail.find("\"reason_code\":\"\"") !=
                    std::string::npos;
            if (!postFillReconciled)
                std::this_thread::sleep_for(std::chrono::milliseconds(50));
        }
        assert(postFillReconciled);
        assert(broker->snapshotRequests.load() >= snapshotsBeforeFill + 6);
        assert(!runtime.HasFatalRuntimeError(nullptr));
        assert(!runtime.Coordinator().GetOrderOwner(orderId, retainedOwner));
        assert(!runtime.Adapter().HasPendingPostFillRiskReconciliation());
        assert(runtime.Adapter().GetAuthoritativeCorrelationSnapshot()
            .activeOrderIds.count(orderId) == 0);

        ExecutionEventFeedRequest otherOwner = wait;
        otherOwner.sessionId = "different-session";
        otherOwner.afterSequence = 0;
        otherOwner.timeoutMs = 0;
        const ExecutionEventReadResult isolated = events.Wait(otherOwner);
        assert(isolated.status == ExecutionEventReadStatus::Timeout);

        IBEvent error = Event(IBEventType::Error, orderId);
        error.key = "399";
        error.value = "offline broker warning";
        error.advancedOrderRejectJson =
            "{\"errorCode\":\"OFFLINE_TEST\"}";
        {
            std::lock_guard<std::mutex> lock(broker->injectedMutex);
            broker->injectedEvents.push_back(error);
        }
        const int errorBarrierBefore =
            broker->quoteBarriersDequeued.load();
        InjectQuoteBarrier(broker);
        for (int attempt = 0; attempt < 1000 &&
             broker->quoteBarriersDequeued.load() == errorBarrierBefore;
             ++attempt)
            std::this_thread::sleep_for(std::chrono::milliseconds(1));
        assert(broker->quoteBarriersDequeued.load() ==
            errorBarrierBefore + 1);
        // Error callback ids are multi-namespace. Non-201/202 diagnostics are
        // durable but must not be projected onto an order owner by id alone.
        assert(runtime.EventHub().Pending(
            command.context.executionDomain, command.context.agentId,
            command.context.sessionId, after) == 0);

        IBEvent completed = Event(IBEventType::CompletedOrder, orderId);
        completed.key = "Cancelled";
        completed.number = 100.0;
        {
            std::lock_guard<std::mutex> lock(broker->injectedMutex);
            broker->injectedEvents.push_back(completed);
        }
        const int completedBarrierBefore =
            broker->quoteBarriersDequeued.load();
        InjectQuoteBarrier(broker);
        for (int attempt = 0; attempt < 1000 &&
             broker->quoteBarriersDequeued.load() == completedBarrierBefore;
             ++attempt)
            std::this_thread::sleep_for(std::chrono::milliseconds(1));
        assert(broker->quoteBarriersDequeued.load() ==
            completedBarrierBefore + 1);
        assert(runtime.EventHub().Pending(
            command.context.executionDomain, command.context.agentId,
            command.context.sessionId, after) == 0);
        ExecutionControlCommand ownerAudit;
        ownerAudit.context = command.context;
        ownerAudit.context.toolCallId =
            "paper-owner-audit-after-terminal-correlation";
        const ExecutionControlResult audited =
            client.RecoveryAuditOwner(ownerAudit);
        // Recovery audit is an ingress-fenced operation.  The full fresh-epoch
        // path is covered independently below; an unfenced legacy caller must
        // fail before it can schedule broker IO.
        assert(audited.status == ExecutionCommandStatus::Rejected);
        assert(audited.reasonCode == "RECOVERY_INGRESS_FENCE_REQUIRED");
        runtime.Stop();
    }

    // The immutable HFX1 credential still attests zero.  The execution-owned
    // checkpoint, written only after the coherent post-fill refresh, must let
    // a fresh process attest the exact non-flat broker cash without moving the
    // campaign baseline used to calculate position.
    struct stat checkpointMetadata;
    assert(::lstat(FxCashRestartCheckpointPath(state).c_str(),
                   &checkpointMetadata) == 0);
    assert(S_ISREG(checkpointMetadata.st_mode));
    assert((checkpointMetadata.st_mode & 07777) == 0600);
    assert(checkpointMetadata.st_uid == ::geteuid());
    {
        IbPaperExecutionRuntimeConfig restartedConfig = Config(
            ActivatedSocket(socketPath), ActivatedSocket(eventPath),
            state, credentials);
        restartedConfig.fxCashBaselines.clear();
        IbPaperExecutionRuntimeComposition restartedRuntime(
            restartedConfig,
            std::unique_ptr<IIBApiWrapper>(new FakeIbWrapper(broker)),
            IbPaperExecutionRuntimeTestHooks(), killSwitch);
        assert(restartedRuntime.Start(reason));
        UnixExecutionServiceClient restartedClient(socketPath, 1000);
        ExecutionReadCommand positions;
        positions.context = Place("paper-restart-checkpoint-position").context;
        positions.query = "portfolio.list_positions";
        const ExecutionCommandResult restored =
            restartedClient.ReadAuthoritativeState(positions);
        assert(restored.status == ExecutionCommandStatus::Accepted);
        assert(restored.detail.find("\"quantity\":100") !=
            std::string::npos);
        restartedRuntime.Stop();
    }

    {
        OmsJournal evidence;
        assert(evidence.Init(state + "/oms-journal.jsonl"));
        bool heldPersisted = false;
        bool executionPersisted = false;
        bool errorPersisted = false;
        bool completedPersisted = false;
        bool executionEndPersisted = false;
        bool completedEndPersisted = false;
        assert(evidence.Replay([&](const OmsJournalEvent& event) {
            if (event.eventType == "broker_order_status" &&
                event.orderId == orderId && event.status == "PreSubmitted")
            {
                heldPersisted = event.qty == 0.0 &&
                    event.brokerRemainingQuantity == 100.0 &&
                    event.brokerWhyHeld == "offline paper hold" &&
                    event.brokerMarketCapPrice == 1.25;
            }
            else if (event.eventType == "broker_execution" &&
                     event.orderId == orderId)
            {
                executionPersisted = event.brokerRequestId == -1 &&
                    event.brokerExecutionId == "execution-44" &&
                    event.qty == 100.0 &&
                    event.brokerRemainingQuantity == 0.0 &&
                    event.price == 1.10015;
            }
            else if (event.eventType == "broker_error" &&
                     event.orderId == orderId)
            {
                errorPersisted = event.brokerErrorCode == 399 &&
                    event.source == "ib-api-callback" &&
                    event.brokerMessage == "offline broker warning" &&
                    event.brokerAdvancedOrderRejectJson ==
                        "{\"errorCode\":\"OFFLINE_TEST\"}";
            }
            else if (event.eventType == "broker_completed_order" &&
                     event.orderId == orderId)
                completedPersisted = event.status == "Cancelled";
            else if (event.eventType == "broker_execution_details_end")
                executionEndPersisted = true;
            else if (event.eventType == "broker_completed_orders_end")
                completedEndPersisted = true;
        }) >= 0);
        assert(heldPersisted);
        assert(executionPersisted);
        assert(errorPersisted);
        assert(completedPersisted);
        assert(executionEndPersisted);
        assert(completedEndPersisted);
    }

    ::unlink(socketPath.c_str());
    ::unlink(eventPath.c_str());
    ::unlink((state + "/oms-journal.jsonl").c_str());
    ::unlink((state + "/ib-paper-runtime.lock").c_str());
    ::unlink((state + "/ib-observability.jsonl").c_str());
    ::unlink(FxCashRestartCheckpointPath(state).c_str());
    ::unlink((credentials + "/hepta-execution-fence").c_str());
    ::unlink((credentials +
              "/hepta-ib-paper-authorization").c_str());
    ::unlink((credentials + "/hepta-fx-cash-baseline").c_str());
    assert(::rmdir(credentials.c_str()) == 0);
    assert(::rmdir(state.c_str()) == 0);
}

void TestRestartRefreshRecoversOwnerAndOrderIdReuseResetsEvidence()
{
    const std::string state = TempDirectory(
        "/tmp/hepta-ib-runtime-restart-evidence-state-XXXXXX");
    const std::string credentials = TempDirectory(
        "/tmp/hepta-ib-runtime-restart-evidence-cred-XXXXXX");
    WriteFile(credentials + "/hepta-execution-fence",
        "HFC1\nfencing_token=77\ngeneration=9\n");
    const IbPaperExecutionRuntimeConfig base =
        Config(-1, -1, state, credentials);
    std::string authorization;
    std::string reason;
    assert(base.profile.BuildAuthorizationCredential(
        authorization, reason));
    WriteFile(credentials + "/hepta-ib-paper-authorization",
        authorization + "\n");
    const std::string socketPath =
        SocketPath("hepta-ib-runtime-restart-evidence");
    const std::string eventPath =
        SocketPath("hepta-ib-runtime-restart-evidence-events");
    const std::shared_ptr<FakeBrokerState> broker(
        new FakeBrokerState());
    const std::shared_ptr<MutableKillSwitch> killSwitch(
        new MutableKillSwitch());
    broker->suppressPlaceStatus.store(true);
    long reusedOrderId = -1;

    {
        IbPaperExecutionRuntimeComposition runtime(
            Config(ActivatedSocket(socketPath), ActivatedSocket(eventPath),
                   state, credentials),
            std::unique_ptr<IIBApiWrapper>(new FakeIbWrapper(broker)),
            IbPaperExecutionRuntimeTestHooks(), killSwitch);
        assert(runtime.Start(reason));
        UnixExecutionServiceClient client(socketPath, 1000);
        const IbPlaceOrderCommand command = Previewed(
            client, Place("paper-restart-owner-original"));
        const ExecutionCommandResult accepted = client.PlaceIbOrder(command);
        if (accepted.status != ExecutionCommandStatus::Accepted)
            std::cerr << "restart evidence place rejected: reason="
                      << accepted.reasonCode << " detail="
                      << accepted.detail << '\n';
        assert(accepted.status == ExecutionCommandStatus::Accepted);
        reusedOrderId = accepted.orderId;
        runtime.Stop();
    }
    ::unlink(socketPath.c_str());
    ::unlink(eventPath.c_str());

    broker->openOrders.clear();
    IBEvent recoveredExecution = Event(
        IBEventType::ExecutionDetails, reusedOrderId);
    recoveredExecution.key = "restart-execution";
    recoveredExecution.value = "BOT";
    recoveredExecution.number = 1.10025;
    recoveredExecution.number2 = 100.0;
    recoveredExecution.number3 = 0.0;
    recoveredExecution.contract.symbol = "EUR";
    recoveredExecution.contract.secType = "CASH";
    recoveredExecution.contract.exchange = "IDEALPRO";
    recoveredExecution.contract.currency = "USD";
    broker->executionDetailEvents.push_back(recoveredExecution);
    // A historical execDetails replay has no broker total quantity.  The
    // production wrapper must not manufacture the contradictory status
    // `PartiallyFilled, remaining=0`; the economic execution plus the complete
    // active-order boundary is sufficient to recover the prior owner.
    std::string recoveredServiceEpoch;
    {
        IbPaperExecutionRuntimeComposition restarted(
            Config(ActivatedSocket(socketPath), ActivatedSocket(eventPath),
                   state, credentials),
            std::unique_ptr<IIBApiWrapper>(new FakeIbWrapper(broker)),
            IbPaperExecutionRuntimeTestHooks(), killSwitch);
        assert(restarted.Start(reason));
        UnixExecutionServiceClient client(socketPath, 1000);
        UnixExecutionEventFeedClient events(eventPath, 1000);
        ExecutionServiceIdentity identity;
        assert(client.GetServiceIdentity(identity, reason));
        recoveredServiceEpoch = identity.serviceEpoch;
        ExecutionEventFeedRequest wait;
        wait.executionDomain = "PAPER";
        wait.agentId = "agent";
        wait.sessionId = "session";
        wait.expectedServiceIdentity = identity;
        wait.timeoutMs = 1000;
        const ExecutionEventReadResult recovered = events.Wait(wait);
        assert(recovered.status == ExecutionEventReadStatus::Event);
        assert(recovered.event.type == "order.fill");
        assert(recovered.event.orderId == reusedOrderId);
        assert(recovered.event.filledQuantity == 100.0);
        assert(recovered.event.averageFillPrice == 1.10025);

        ExecutionReadCommand orders;
        orders.context = Place("restart-owner-read").context;
        orders.query = "orders.list";
        ExecutionCommandResult recent;
        for (int attempt = 0; attempt < 50; ++attempt)
        {
            recent = client.ReadAuthoritativeState(orders);
            if (recent.status == ExecutionCommandStatus::Accepted &&
                recent.detail.find("\"status\":\"Filled\"") !=
                    std::string::npos)
                break;
            std::this_thread::sleep_for(std::chrono::milliseconds(10));
        }
        assert(recent.status == ExecutionCommandStatus::Accepted);
        assert(recent.detail.find("\"order_id\":" +
            std::to_string(reusedOrderId)) != std::string::npos);
        assert(recent.detail.find("\"economic_fill\":true") !=
            std::string::npos);
        assert(recent.detail.find("\"status\":\"Filled\"") !=
            std::string::npos);
        assert(recent.detail.find("\"terminal\":true") !=
            std::string::npos);
        assert(recent.detail.find("\"remaining_quantity\":0") !=
            std::string::npos);
        assert(recent.detail.find(
            "\"broker_execution_id\":\"restart-execution\"") !=
            std::string::npos);
        assert(recent.detail.find(
            "\"broker_execution_ambiguous\":false") !=
            std::string::npos);
        assert(recent.detail.find(
            "\"broker_execution_quantity\":100") !=
            std::string::npos);
        assert(recent.detail.find("\"evidence_service_epoch\":\"" +
            identity.serviceEpoch + "\"") != std::string::npos);
        restarted.Stop();
    }
    ::unlink(socketPath.c_str());
    ::unlink(eventPath.c_str());

    broker->nextOrderId = reusedOrderId;
    broker->suppressPlaceStatus.store(true);
    {
        IbPaperExecutionRuntimeComposition reused(
            Config(ActivatedSocket(socketPath), ActivatedSocket(eventPath),
                   state, credentials),
            std::unique_ptr<IIBApiWrapper>(new FakeIbWrapper(broker)),
            IbPaperExecutionRuntimeTestHooks(), killSwitch);
        assert(reused.Start(reason));
        UnixExecutionServiceClient client(socketPath, 1000);
        UnixExecutionEventFeedClient events(eventPath, 1000);
        ExecutionServiceIdentity identity;
        assert(client.GetServiceIdentity(identity, reason));
        assert(identity.serviceEpoch != recoveredServiceEpoch);
        const IbPlaceOrderCommand command = Previewed(
            client, Place("paper-reused-order-id"));
        const ExecutionCommandResult accepted = client.PlaceIbOrder(command);
        assert(accepted.status == ExecutionCommandStatus::Accepted);
        assert(accepted.orderId == reusedOrderId);

        // No broker callback exists yet. Local durable acceptance must already
        // have cleared the reused id's prior economic/terminal evidence.
        ExecutionReadCommand beforeCallback;
        beforeCallback.context = command.context;
        beforeCallback.context.toolCallId = "reused-before-callback-read";
        beforeCallback.query = "orders.list";
        const ExecutionCommandResult reset =
            client.ReadAuthoritativeState(beforeCallback);
        assert(reset.status == ExecutionCommandStatus::Accepted);
        assert(reset.detail.find("\"status\":\"Accepted\"") !=
            std::string::npos);
        assert(reset.detail.find("\"economic_fill\":false") !=
            std::string::npos);
        assert(reset.detail.find("\"terminal\":false") !=
            std::string::npos);
        assert(reset.detail.find("\"filled_quantity\":0") !=
            std::string::npos);
        assert(reset.detail.find("\"remaining_quantity\":100") !=
            std::string::npos);
        assert(reset.detail.find("\"evidence_service_epoch\":\"" +
            identity.serviceEpoch + "\"") != std::string::npos);

        IBEvent submittedCallback = Event(
            IBEventType::OrderStatus, reusedOrderId);
        submittedCallback.key = "Submitted";
        submittedCallback.number3 = 100.0;
        {
            std::lock_guard<std::mutex> lock(broker->injectedMutex);
            broker->injectedEvents.push_back(submittedCallback);
        }

        ExecutionEventFeedRequest wait;
        wait.executionDomain = command.context.executionDomain;
        wait.agentId = command.context.agentId;
        wait.sessionId = command.context.sessionId;
        wait.expectedServiceIdentity = identity;
        wait.timeoutMs = 1000;
        std::uint64_t after = 0;
        bool submitted = false;
        for (int attempt = 0; attempt < 8 && !submitted; ++attempt)
        {
            wait.afterSequence = after;
            const ExecutionEventReadResult observed = events.Wait(wait);
            assert(observed.status == ExecutionEventReadStatus::Event);
            after = observed.event.sequence;
            submitted = observed.event.type == "order.status" &&
                observed.event.orderId == reusedOrderId &&
                observed.event.status == "Submitted";
        }
        assert(submitted);

        ExecutionReadCommand orders;
        orders.context = command.context;
        orders.context.toolCallId = "reused-order-read";
        orders.query = "orders.list";
        const ExecutionCommandResult recent =
            client.ReadAuthoritativeState(orders);
        assert(recent.status == ExecutionCommandStatus::Accepted);
        assert(recent.detail.find("\"order_id\":" +
            std::to_string(reusedOrderId)) != std::string::npos);
        assert(recent.detail.find("\"status\":\"Submitted\"") !=
            std::string::npos);
        assert(recent.detail.find("\"economic_fill\":false") !=
            std::string::npos);
        assert(recent.detail.find("\"terminal\":false") !=
            std::string::npos);
        assert(recent.detail.find("\"filled_quantity\":0") !=
            std::string::npos);
        assert(recent.detail.find("\"evidence_service_epoch\":\"" +
            identity.serviceEpoch + "\"") != std::string::npos);
        reused.Stop();
    }

    ::unlink(socketPath.c_str());
    ::unlink(eventPath.c_str());
    ::unlink((state + "/oms-journal.jsonl").c_str());
    ::unlink((state + "/ib-paper-runtime.lock").c_str());
    ::unlink((state + "/ib-observability.jsonl").c_str());
    ::unlink(FxCashRestartCheckpointPath(state).c_str());
    ::unlink((credentials + "/hepta-execution-fence").c_str());
    ::unlink((credentials +
              "/hepta-ib-paper-authorization").c_str());
    assert(::rmdir(credentials.c_str()) == 0);
    assert(::rmdir(state.c_str()) == 0);
}

void TestBrokerCallbackIdentityMismatchFailsClosed()
{
    const auto runCase = [](const char* stem, IBEventType callbackType,
                            const std::string& account,
                            const std::string& side,
                            const std::string& expectedReason) {
        const std::string state = TempDirectory(
            "/tmp/hepta-ib-runtime-identity-state-XXXXXX");
        const std::string credentials = TempDirectory(
            "/tmp/hepta-ib-runtime-identity-cred-XXXXXX");
        WriteFile(credentials + "/hepta-execution-fence",
            "HFC1\nfencing_token=77\ngeneration=9\n");
        const IbPaperExecutionRuntimeConfig base =
            Config(-1, -1, state, credentials);
        std::string authorization;
        std::string reason;
        assert(base.profile.BuildAuthorizationCredential(
            authorization, reason));
        WriteFile(credentials + "/hepta-ib-paper-authorization",
            authorization + "\n");
        const std::string socketPath = SocketPath(stem);
        const std::string eventPath = SocketPath(
            (std::string(stem) + "-events").c_str());
        const std::shared_ptr<FakeBrokerState> broker(
            new FakeBrokerState());
        const std::shared_ptr<MutableKillSwitch> killSwitch(
            new MutableKillSwitch());
        long orderId = -1;
        {
            IbPaperExecutionRuntimeComposition runtime(
                Config(ActivatedSocket(socketPath), ActivatedSocket(eventPath),
                       state, credentials),
                std::unique_ptr<IIBApiWrapper>(new FakeIbWrapper(broker)),
                IbPaperExecutionRuntimeTestHooks(), killSwitch);
            assert(runtime.Start(reason));
            UnixExecutionServiceClient client(socketPath, 1000);
            UnixExecutionEventFeedClient events(eventPath, 1000);
            ExecutionServiceIdentity identity;
            assert(client.GetServiceIdentity(identity, reason));
            const IbPlaceOrderCommand command = Previewed(
                client, Place(std::string(stem) + "-place"));
            const ExecutionCommandResult accepted =
                client.PlaceIbOrder(command);
            assert(accepted.status == ExecutionCommandStatus::Accepted);
            orderId = accepted.orderId;

            ExecutionEventFeedRequest wait;
            wait.executionDomain = command.context.executionDomain;
            wait.agentId = command.context.agentId;
            wait.sessionId = command.context.sessionId;
            wait.expectedServiceIdentity = identity;
            wait.timeoutMs = 1000;
            std::uint64_t after = 0;
            bool submitted = false;
            for (int attempt = 0; attempt < 8 && !submitted; ++attempt)
            {
                wait.afterSequence = after;
                const ExecutionEventReadResult observed = events.Wait(wait);
                assert(observed.status == ExecutionEventReadStatus::Event);
                after = observed.event.sequence;
                submitted = observed.event.type == "order.status" &&
                    observed.event.orderId == orderId &&
                    observed.event.status == "Submitted";
            }
            assert(submitted);

            IBEvent callback = Event(callbackType, orderId);
            callback.account = account;
            if (callbackType == IBEventType::ExecutionDetails)
            {
                callback.key = "identity-mismatch-execution";
                callback.value = side;
                callback.number = 1.1003;
                callback.number2 = 100.0;
            }
            else
            {
                callback.key = "Cancelled";
                callback.number = 100.0;
            }
            {
                std::lock_guard<std::mutex> lock(broker->injectedMutex);
                broker->injectedEvents.push_back(callback);
            }
            std::string fatal;
            for (int attempt = 0; attempt < 100 &&
                 !runtime.HasFatalRuntimeError(&fatal); ++attempt)
                std::this_thread::sleep_for(std::chrono::milliseconds(10));
            assert(runtime.HasFatalRuntimeError(&fatal));
            assert(fatal == expectedReason);
            assert(runtime.EventHub().Pending(
                command.context.executionDomain, command.context.agentId,
                command.context.sessionId, after) == 0);
            runtime.Stop();
        }

        {
            OmsJournal journal;
            assert(journal.Init(state + "/oms-journal.jsonl"));
            bool unownedDiagnostic = false;
            assert(journal.Replay([&](const OmsJournalEvent& event) {
                if (event.orderId == orderId &&
                    ((callbackType == IBEventType::ExecutionDetails &&
                      event.eventType == "broker_execution") ||
                     (callbackType == IBEventType::CompletedOrder &&
                      event.eventType == "broker_completed_order")))
                    unownedDiagnostic =
                        event.source == "ib-api-callback";
            }) >= 0);
            assert(unownedDiagnostic);
        }
        ::unlink(socketPath.c_str());
        ::unlink(eventPath.c_str());
        ::unlink((state + "/oms-journal.jsonl").c_str());
        ::unlink((state + "/ib-paper-runtime.lock").c_str());
        ::unlink((state + "/ib-observability.jsonl").c_str());
        ::unlink(FxCashRestartCheckpointPath(state).c_str());
        ::unlink((credentials + "/hepta-execution-fence").c_str());
        ::unlink((credentials +
                  "/hepta-ib-paper-authorization").c_str());
        assert(::rmdir(credentials.c_str()) == 0);
        assert(::rmdir(state.c_str()) == 0);
    };

    runCase("hepta-ib-execution-account-mismatch",
        IBEventType::ExecutionDetails, "DU999999", "BOT",
        "IB_BROKER_CALLBACK_ACCOUNT_MISMATCH");
    runCase("hepta-ib-execution-side-mismatch",
        IBEventType::ExecutionDetails, "DU123456", "SLD",
        "IB_EXECUTION_SIDE_MISMATCH");
    runCase("hepta-ib-completed-account-mismatch",
        IBEventType::CompletedOrder, "DU999999", std::string(),
        "IB_BROKER_CALLBACK_ACCOUNT_MISMATCH");
}

void TestBrokerReconnectRejectsLocalInFlightOrder()
{
    const std::string state = TempDirectory(
        "/tmp/hepta-ib-reconnect-owner-state-XXXXXX");
    const std::string credentials = TempDirectory(
        "/tmp/hepta-ib-reconnect-owner-cred-XXXXXX");
    WriteFile(credentials + "/hepta-execution-fence",
        "HFC1\nfencing_token=77\ngeneration=9\n");
    const IbPaperExecutionRuntimeConfig base =
        Config(-1, -1, state, credentials);
    std::string authorization;
    std::string reason;
    assert(base.profile.BuildAuthorizationCredential(
        authorization, reason));
    WriteFile(credentials + "/hepta-ib-paper-authorization",
        authorization + "\n");
    const std::string socketPath = SocketPath(
        "hepta-ib-reconnect-owner");
    const std::string eventPath = SocketPath(
        "hepta-ib-reconnect-owner-events");
    const std::shared_ptr<FakeBrokerState> broker(
        new FakeBrokerState());
    const std::shared_ptr<MutableKillSwitch> killSwitch(
        new MutableKillSwitch());
    IbPaperExecutionRuntimeTestHooks hooks;
    hooks.reconnectApiFactory = [broker]() {
        return std::unique_ptr<IIBApiWrapper>(new FakeIbWrapper(broker));
    };
    IbPaperExecutionRuntimeComposition runtime(
        Config(ActivatedSocket(socketPath), ActivatedSocket(eventPath),
               state, credentials),
        std::unique_ptr<IIBApiWrapper>(new FakeIbWrapper(broker)),
        hooks, killSwitch);
    assert(runtime.Start(reason));
    UnixExecutionServiceClient client(socketPath, 1000);
    const IbPlaceOrderCommand command = Previewed(
        client, Place("paper-reconnect-local-owner"));
    const ExecutionCommandResult accepted = client.PlaceIbOrder(command);
    assert(accepted.status == ExecutionCommandStatus::Accepted);
    ExecutionOrderOwner owner;
    assert(runtime.Coordinator().GetOrderOwner(accepted.orderId, owner));
    const int connectsBeforeClose = broker->reconnectAttempts.load();
    broker->emitConnectionClosed.store(true);
    for (int attempt = 0; attempt < 3000 &&
         !runtime.HasFatalRuntimeError(&reason); ++attempt)
        std::this_thread::sleep_for(std::chrono::milliseconds(1));
    assert(runtime.HasFatalRuntimeError(&reason));
    assert(reason == "IB_PAPER_BROKER_RECONNECT_LOCAL_ORDERS_UNSAFE");
    assert(broker->reconnectAttempts.load() == connectsBeforeClose);
    assert(runtime.Coordinator().GetOrderOwner(accepted.orderId, owner));
    runtime.Stop();

    ::unlink(socketPath.c_str());
    ::unlink(eventPath.c_str());
    ::unlink((state + "/oms-journal.jsonl").c_str());
    ::unlink((state + "/ib-paper-runtime.lock").c_str());
    ::unlink((state + "/ib-observability.jsonl").c_str());
    ::unlink(FxCashRestartCheckpointPath(state).c_str());
    ::unlink((credentials + "/hepta-execution-fence").c_str());
    ::unlink((credentials +
              "/hepta-ib-paper-authorization").c_str());
    assert(::rmdir(credentials.c_str()) == 0);
    assert(::rmdir(state.c_str()) == 0);
}

// Diagnostic seam test: a broker callback can be queued by the transport's
// disconnect boundary after the runtime has already decided that reconnect is
// safe.  The callback is economically relevant even when it has no local
// owner, so it must not disappear when the adapter replaces its wrapper.
void TestReconnectLateEconomicFillAtDisconnect(bool cancelFailure = false)
{
    const std::string state = TempDirectory(
        "/tmp/hepta-ib-reconnect-late-fill-state-XXXXXX");
    const std::string credentials = TempDirectory(
        "/tmp/hepta-ib-reconnect-late-fill-cred-XXXXXX");
    WriteFile(credentials + "/hepta-execution-fence",
        "HFC1\nfencing_token=77\ngeneration=9\n");
    IbPaperExecutionRuntimeConfig config =
        Config(-1, -1, state, credentials);
    config.reconnectTimeoutMs = 2000;
    std::string authorization;
    std::string reason;
    assert(config.profile.BuildAuthorizationCredential(
        authorization, reason));
    WriteFile(credentials + "/hepta-ib-paper-authorization",
        authorization + "\n");
    const std::string socketPath = SocketPath(cancelFailure ?
        "hepta-ib-reconnect-late-fill-cancel" :
        "hepta-ib-reconnect-late-fill");
    const std::string eventPath = SocketPath(
        cancelFailure ? "hepta-ib-reconnect-late-fill-cancel-events" :
        "hepta-ib-reconnect-late-fill-events");
    const std::shared_ptr<FakeBrokerState> broker(
        new FakeBrokerState());
    if (cancelFailure)
        broker->marketDataCancelFailureRequestId.store(1000001);
    const std::shared_ptr<MutableKillSwitch> killSwitch(
        new MutableKillSwitch());
    const std::shared_ptr<std::atomic<bool> > reconnectComplete(
        new std::atomic<bool>(false));
    IbPaperExecutionRuntimeTestHooks hooks;
    hooks.reconnectApiFactory = [broker]() {
        return std::unique_ptr<IIBApiWrapper>(new FakeIbWrapper(broker));
    };
    hooks.onStage = [reconnectComplete](const char* stage) {
        if (std::strcmp(stage, "broker_reconnect_complete") == 0)
            reconnectComplete->store(true);
    };
    config.listenFd = ActivatedSocket(socketPath);
    config.eventListenFd = ActivatedSocket(eventPath);
    IbPaperExecutionRuntimeComposition runtime(
        config,
        std::unique_ptr<IIBApiWrapper>(new FakeIbWrapper(broker)),
        hooks, killSwitch);
    assert(runtime.Start(reason));
    const int connectsBefore = broker->reconnectAttempts.load();
    const long lateOrderId = 987654;
    broker->disconnectFillOrderId.store(lateOrderId);
    broker->emitConnectionClosed.store(true);
    for (int attempt = 0; attempt < 3000 &&
         !runtime.HasFatalRuntimeError(&reason); ++attempt)
        std::this_thread::sleep_for(std::chrono::milliseconds(1));
    assert(runtime.HasFatalRuntimeError(&reason));
    assert(reason ==
        "IB_PAPER_BROKER_DISCONNECTED_DURING_POST_FILL_RECONCILIATION");
    assert(!reconnectComplete->load());
    assert(broker->reconnectAttempts.load() == connectsBefore);
    assert(broker->economicFillDequeued.load());
    if (cancelFailure) assert(broker->marketDataCancels.load() == 1);
    assert(runtime.Adapter().HasPendingLivePostFillRiskReconciliation());
    OmsJournal evidence;
    assert(evidence.Init(state + "/oms-journal.jsonl"));
    bool latePersisted = false;
    assert(evidence.Replay([&](const OmsJournalEvent& event) {
        if (event.eventType == "broker_order_status" &&
            event.orderId == lateOrderId && event.status == "Filled")
            latePersisted = event.source == "ib-api-callback" &&
                event.qty == 1.0 && event.price == 1.10010;
    }) >= 0);
    assert(latePersisted);
    runtime.Stop();
    ::unlink(socketPath.c_str());
    ::unlink(eventPath.c_str());
    ::unlink((state + "/oms-journal.jsonl").c_str());
    ::unlink((state + "/ib-paper-runtime.lock").c_str());
    ::unlink((state + "/ib-observability.jsonl").c_str());
    ::unlink(FxCashRestartCheckpointPath(state).c_str());
    ::unlink((credentials + "/hepta-execution-fence").c_str());
    ::unlink((credentials + "/hepta-ib-paper-authorization").c_str());
    assert(::rmdir(credentials.c_str()) == 0);
    assert(::rmdir(state.c_str()) == 0);
}

// A transport-control callback and an economic callback may be dequeued in
// the same adapter batch.  The control event is routed first and schedules a
// reconnect boundary, but the already-dequeued tail still owns durable fill
// evidence and must be persisted before that batch is discarded.
void TestReconnectBoundaryPendingTailEconomicFill()
{
    const std::string state = TempDirectory(
        "/tmp/hepta-ib-reconnect-tail-fill-state-XXXXXX");
    const std::string credentials = TempDirectory(
        "/tmp/hepta-ib-reconnect-tail-fill-cred-XXXXXX");
    WriteFile(credentials + "/hepta-execution-fence",
        "HFC1\nfencing_token=77\ngeneration=9\n");
    IbPaperExecutionRuntimeConfig config =
        Config(-1, -1, state, credentials);
    config.reconnectTimeoutMs = 2000;
    std::string authorization;
    std::string reason;
    assert(config.profile.BuildAuthorizationCredential(
        authorization, reason));
    WriteFile(credentials + "/hepta-ib-paper-authorization",
        authorization + "\n");
    const std::string socketPath = SocketPath(
        "hepta-ib-reconnect-tail-fill");
    const std::string eventPath = SocketPath(
        "hepta-ib-reconnect-tail-fill-events");
    const std::shared_ptr<FakeBrokerState> broker(new FakeBrokerState());
    const std::shared_ptr<MutableKillSwitch> killSwitch(
        new MutableKillSwitch());
    IbPaperExecutionRuntimeTestHooks hooks;
    hooks.reconnectApiFactory = [broker]() {
        return std::unique_ptr<IIBApiWrapper>(new FakeIbWrapper(broker));
    };
    config.listenFd = ActivatedSocket(socketPath);
    config.eventListenFd = ActivatedSocket(eventPath);
    IbPaperExecutionRuntimeComposition runtime(
        config,
        std::unique_ptr<IIBApiWrapper>(new FakeIbWrapper(broker)),
        hooks, killSwitch);
    assert(runtime.Start(reason));
    const int connectsBefore = broker->reconnectAttempts.load();
    const long lateOrderId = 987655;
    IBEvent closed = Event(IBEventType::ConnectionClosed);
    IBEvent filled = Event(IBEventType::OrderStatus, lateOrderId);
    filled.key = "Filled";
    filled.number = 1.10010;
    filled.number2 = 1.0;
    filled.number3 = 0.0;
    // Hold the fixture queue lock while publishing both callbacks so one
    // PollOnce() necessarily exposes them as one ordered batch.
    {
        std::lock_guard<std::mutex> lock(broker->injectedMutex);
        broker->injectedEvents.push_back(closed);
        broker->injectedEvents.push_back(filled);
    }
    for (int attempt = 0; attempt < 5000 &&
         !runtime.HasFatalRuntimeError(&reason); ++attempt)
        std::this_thread::sleep_for(std::chrono::milliseconds(1));
    assert(runtime.HasFatalRuntimeError(&reason));
    assert(reason ==
        "IB_PAPER_BROKER_DISCONNECTED_DURING_POST_FILL_RECONCILIATION");
    assert(broker->reconnectAttempts.load() == connectsBefore);
    assert(broker->economicFillDequeued.load());
    assert(runtime.Adapter().HasPendingLivePostFillRiskReconciliation());
    OmsJournal evidence;
    assert(evidence.Init(state + "/oms-journal.jsonl"));
    bool latePersisted = false;
    assert(evidence.Replay([&](const OmsJournalEvent& event) {
        if (event.eventType == "broker_order_status" &&
            event.orderId == lateOrderId && event.status == "Filled")
            latePersisted = event.source == "ib-api-callback" &&
                event.qty == 1.0 && event.price == 1.10010;
    }) >= 0);
    assert(latePersisted);
    runtime.Stop();
    ::unlink(socketPath.c_str());
    ::unlink(eventPath.c_str());
    ::unlink((state + "/oms-journal.jsonl").c_str());
    ::unlink((state + "/ib-paper-runtime.lock").c_str());
    ::unlink((state + "/ib-observability.jsonl").c_str());
    ::unlink(FxCashRestartCheckpointPath(state).c_str());
    ::unlink((credentials + "/hepta-execution-fence").c_str());
    ::unlink((credentials + "/hepta-ib-paper-authorization").c_str());
    assert(::rmdir(credentials.c_str()) == 0);
    assert(::rmdir(state.c_str()) == 0);
}

// A transport-control callback may be followed by a session-wide 10197 in the
// same dequeued batch.  The tail must retain its broker-error evidence and
// still close the runtime; otherwise reconnect could be retried after a
// competing LIVE session witness was already observed.
void TestReconnectBoundaryPendingTailMarketData10197()
{
    const std::string state = TempDirectory(
        "/tmp/hepta-ib-reconnect-tail-10197-state-XXXXXX");
    const std::string credentials = TempDirectory(
        "/tmp/hepta-ib-reconnect-tail-10197-cred-XXXXXX");
    WriteFile(credentials + "/hepta-execution-fence",
        "HFC1\nfencing_token=77\ngeneration=9\n");
    IbPaperExecutionRuntimeConfig config =
        Config(-1, -1, state, credentials);
    config.reconnectTimeoutMs = 2000;
    std::string authorization;
    std::string reason;
    assert(config.profile.BuildAuthorizationCredential(
        authorization, reason));
    WriteFile(credentials + "/hepta-ib-paper-authorization",
        authorization + "\n");
    const std::string socketPath = SocketPath(
        "hepta-ib-reconnect-tail-10197");
    const std::string eventPath = SocketPath(
        "hepta-ib-reconnect-tail-10197-events");
    const std::shared_ptr<FakeBrokerState> broker(new FakeBrokerState());
    const std::shared_ptr<MutableKillSwitch> killSwitch(
        new MutableKillSwitch());
    IbPaperExecutionRuntimeTestHooks hooks;
    hooks.reconnectApiFactory = [broker]() {
        return std::unique_ptr<IIBApiWrapper>(new FakeIbWrapper(broker));
    };
    config.listenFd = ActivatedSocket(socketPath);
    config.eventListenFd = ActivatedSocket(eventPath);
    IbPaperExecutionRuntimeComposition runtime(
        config,
        std::unique_ptr<IIBApiWrapper>(new FakeIbWrapper(broker)),
        hooks, killSwitch);
    assert(runtime.Start(reason));
    const int connectsBefore = broker->reconnectAttempts.load();

    IBEvent closed = Event(IBEventType::ConnectionClosed);
    IBEvent error = Event(IBEventType::Error, -1);
    error.key = "10197";
    error.value = "simulated competing live session";
    {
        std::lock_guard<std::mutex> lock(broker->injectedMutex);
        broker->injectedEvents.push_back(closed);
        broker->injectedEvents.push_back(error);
    }
    for (int attempt = 0; attempt < 5000 &&
         !runtime.HasFatalRuntimeError(&reason); ++attempt)
        std::this_thread::sleep_for(std::chrono::milliseconds(1));
    assert(runtime.HasFatalRuntimeError(&reason));
    assert(reason ==
        "IB_PAPER_REALTIME_MARKET_DATA_UNAVAILABLE_10197");
    assert(broker->reconnectAttempts.load() == connectsBefore);

    OmsJournal evidence;
    assert(evidence.Init(state + "/oms-journal.jsonl"));
    bool persisted = false;
    assert(evidence.Replay([&](const OmsJournalEvent& event) {
        if (event.eventType == "broker_error" && event.orderId == -1)
            persisted = event.brokerErrorCode == 10197 &&
                event.riskCode == "IB_ERROR_10197";
    }) >= 0);
    assert(persisted);
    runtime.Stop();

    ::unlink(socketPath.c_str());
    ::unlink(eventPath.c_str());
    ::unlink((state + "/oms-journal.jsonl").c_str());
    ::unlink((state + "/ib-paper-runtime.lock").c_str());
    ::unlink((state + "/ib-observability.jsonl").c_str());
    ::unlink(FxCashRestartCheckpointPath(state).c_str());
    ::unlink((credentials + "/hepta-execution-fence").c_str());
    ::unlink((credentials + "/hepta-ib-paper-authorization").c_str());
    assert(::rmdir(credentials.c_str()) == 0);
    assert(::rmdir(state.c_str()) == 0);
}

// A 10197 arriving while startup is tearing down used to be treated as
// ordinary disconnect noise and dropped when there was no live fill.  The
// boundary drain must retain it and return the exact fail-closed reason.
void TestDisconnectBoundaryMarketData10197()
{
    const std::string state = TempDirectory(
        "/tmp/hepta-ib-disconnect-boundary-10197-state-XXXXXX");
    const std::string credentials = TempDirectory(
        "/tmp/hepta-ib-disconnect-boundary-10197-cred-XXXXXX");
    WriteFile(credentials + "/hepta-execution-fence",
        "HFC1\nfencing_token=77\ngeneration=9\n");
    IbPaperExecutionRuntimeConfig config =
        Config(-1, -1, state, credentials);
    config.readinessTimeoutMs = 500;
    config.reconnectTimeoutMs = 1000;
    std::string authorization;
    std::string reason;
    assert(config.profile.BuildAuthorizationCredential(
        authorization, reason));
    WriteFile(credentials + "/hepta-ib-paper-authorization",
        authorization + "\n");
    const std::string socketPath = SocketPath(
        "hepta-ib-disconnect-boundary-10197");
    const std::string eventPath = SocketPath(
        "hepta-ib-disconnect-boundary-10197-events");
    const std::shared_ptr<FakeBrokerState> broker(new FakeBrokerState());
    broker->emitConnectionClosed.store(true);
    broker->disconnectErrorCode.store(10197);
    config.listenFd = ActivatedSocket(socketPath);
    config.eventListenFd = ActivatedSocket(eventPath);
    IbPaperExecutionRuntimeComposition runtime(
        config,
        std::unique_ptr<IIBApiWrapper>(new FakeIbWrapper(broker)),
        IbPaperExecutionRuntimeTestHooks(),
        std::shared_ptr<MutableKillSwitch>(new MutableKillSwitch()));

    assert(!runtime.Start(reason));
    assert(reason ==
        "IB_PAPER_REALTIME_MARKET_DATA_UNAVAILABLE_10197");
    assert(broker->marketDataRequests.load() == 0);
    assert(broker->marketDataCancels.load() == 0);
    assert(!runtime.Adapter().IsConnected());
    OmsJournal evidence;
    assert(evidence.Init(state + "/oms-journal.jsonl"));
    bool persisted = false;
    assert(evidence.Replay([&](const OmsJournalEvent& event) {
        if (event.eventType == "broker_error" && event.orderId == 0)
            persisted = event.brokerErrorCode == 10197 &&
                event.riskCode == "IB_ERROR_10197";
    }) >= 0);
    assert(persisted);
    runtime.Stop();

    ::unlink(socketPath.c_str());
    ::unlink(eventPath.c_str());
    ::unlink((state + "/oms-journal.jsonl").c_str());
    ::unlink((state + "/ib-paper-runtime.lock").c_str());
    ::unlink((state + "/ib-observability.jsonl").c_str());
    ::unlink(FxCashRestartCheckpointPath(state).c_str());
    ::unlink((credentials + "/hepta-execution-fence").c_str());
    ::unlink((credentials + "/hepta-ib-paper-authorization").c_str());
    assert(::rmdir(credentials.c_str()) == 0);
    assert(::rmdir(state.c_str()) == 0);
}

void TestBrokerControlErrorsForceReconnect(int onlyControlError = 0)
{
    const int controlErrors[] = {504, 509, 1100, 1101, 1102, 1300, 2110};
    for (std::size_t index = 0;
         index < sizeof(controlErrors) / sizeof(controlErrors[0]); ++index)
    {
        if (onlyControlError != 0 &&
            controlErrors[index] != onlyControlError)
            continue;
        const std::string state = TempDirectory(
            "/tmp/hepta-ib-control-reconnect-state-XXXXXX");
        const std::string credentials = TempDirectory(
            "/tmp/hepta-ib-control-reconnect-cred-XXXXXX");
        WriteFile(credentials + "/hepta-execution-fence",
            "HFC1\nfencing_token=77\ngeneration=9\n");
        const IbPaperExecutionRuntimeConfig base =
            Config(-1, -1, state, credentials);
        std::string authorization;
        std::string reason;
        assert(base.profile.BuildAuthorizationCredential(
            authorization, reason));
        WriteFile(credentials + "/hepta-ib-paper-authorization",
            authorization + "\n");
        const std::string stem = "hepta-ib-control-reconnect-" +
            std::to_string(controlErrors[index]);
        const std::string socketPath = SocketPath(stem.c_str());
        const std::string eventPath = SocketPath(
            (stem + "-events").c_str());
        const std::shared_ptr<FakeBrokerState> broker(
            new FakeBrokerState());
        const std::shared_ptr<MutableKillSwitch> killSwitch(
            new MutableKillSwitch());
        const std::shared_ptr<std::atomic<bool> > reconnectComplete(
            new std::atomic<bool>(false));
        const std::shared_ptr<std::atomic<bool> > upstreamUnavailable(
            new std::atomic<bool>(false));
        const std::shared_ptr<std::atomic<bool> > reconnectScheduled(
            new std::atomic<bool>(false));
        const std::shared_ptr<std::atomic<bool> > reconnectAttempted(
            new std::atomic<bool>(false));
        IbPaperExecutionRuntimeTestHooks hooks;
        hooks.reconnectApiFactory = [broker]() {
            return std::unique_ptr<IIBApiWrapper>(new FakeIbWrapper(broker));
        };
        hooks.onStage = [reconnectComplete, upstreamUnavailable,
                         reconnectScheduled, reconnectAttempted](
                            const char* stage) {
            if (std::strcmp(stage, "broker_reconnect_complete") == 0)
                reconnectComplete->store(true);
            else if (std::strcmp(stage,
                         "broker_reconnect_upstream_unavailable") == 0)
                upstreamUnavailable->store(true);
            else if (std::strcmp(stage, "broker_reconnect_scheduled") == 0)
                reconnectScheduled->store(true);
            else if (std::strcmp(stage,
                         "before_broker_reconnect_attempt") == 0)
                reconnectAttempted->store(true);
        };
        IbPaperExecutionRuntimeComposition runtime(
            Config(ActivatedSocket(socketPath), ActivatedSocket(eventPath),
                   state, credentials),
            std::unique_ptr<IIBApiWrapper>(new FakeIbWrapper(broker)),
            hooks, killSwitch);
        const bool started = runtime.Start(reason);
        if (!started)
            std::cerr << "control reconnect startup rejected: control="
                      << controlErrors[index] << " reason=" << reason << '\n';
        assert(started);
        const int connectsBefore = broker->reconnectAttempts.load();
        const int snapshotsBefore = broker->snapshotRequests.load();
        if (controlErrors[index] == 1100 || controlErrors[index] == 2110)
            broker->reconnectControlErrorOnConnect.store(2110);
        broker->emitControlErrorCode.store(controlErrors[index]);
        if (controlErrors[index] == 1100 || controlErrors[index] == 2110) {
            for (int attempt = 0; attempt < 30000 &&
                 !upstreamUnavailable->load(); ++attempt)
                std::this_thread::sleep_for(std::chrono::milliseconds(1));
            if (!upstreamUnavailable->load()) {
                std::string fatal;
                const bool hasFatal = runtime.HasFatalRuntimeError(&fatal);
                std::cerr << "upstream-unavailable stage missing: control="
                          << controlErrors[index]
                          << " reconnect_attempts="
                          << broker->reconnectAttempts.load()
                          << " pending_control="
                          << broker->emitControlErrorCode.load()
                          << " poll_count=" << broker->pollCount.load()
                          << " controls_injected="
                          << broker->controlErrorsInjected.load()
                          << " controls_dequeued="
                          << broker->controlErrorsDequeued.load()
                          << " scheduled=" << reconnectScheduled->load()
                          << " attempted=" << reconnectAttempted->load()
                          << " fatal=" << hasFatal
                          << " reason=" << fatal << '\n';
            }
            assert(upstreamUnavailable->load());
            assert(runtime.IsMutationBlocked(&reason));
            assert(reason == "IB_PAPER_BROKER_RECONNECT_PENDING");
            assert(broker->refreshRequestsWhileUpstreamUnavailable.load() == 0);
            assert(broker->snapshotRequests.load() == snapshotsBefore);
            broker->emitControlErrorCode.store(1102);
        }
        for (int attempt = 0; attempt < 5000 &&
             !reconnectComplete->load(); ++attempt)
            std::this_thread::sleep_for(std::chrono::milliseconds(1));
        assert(reconnectComplete->load());
        assert(!runtime.HasFatalRuntimeError(&reason));
        assert(broker->reconnectAttempts.load() == connectsBefore + 1);
        assert(broker->snapshotRequests.load() > snapshotsBefore);
        assert(broker->refreshRequestsWhileUpstreamUnavailable.load() == 0);
        runtime.Stop();

        ::unlink(socketPath.c_str());
        ::unlink(eventPath.c_str());
        ::unlink((state + "/oms-journal.jsonl").c_str());
        ::unlink((state + "/ib-paper-runtime.lock").c_str());
        ::unlink((state + "/ib-observability.jsonl").c_str());
        ::unlink(FxCashRestartCheckpointPath(state).c_str());
        ::unlink((credentials + "/hepta-execution-fence").c_str());
        ::unlink((credentials +
                  "/hepta-ib-paper-authorization").c_str());
        assert(::rmdir(credentials.c_str()) == 0);
        assert(::rmdir(state.c_str()) == 0);
    }
}

void TestReconnectCoalescesTransportControlsBeforeRestore()
{
    const std::string state = TempDirectory(
        "/tmp/hepta-ib-reconnect-control-batch-state-XXXXXX");
    const std::string credentials = TempDirectory(
        "/tmp/hepta-ib-reconnect-control-batch-cred-XXXXXX");
    WriteFile(credentials + "/hepta-execution-fence",
        "HFC1\nfencing_token=77\ngeneration=9\n");
    IbPaperExecutionRuntimeConfig config =
        Config(-1, -1, state, credentials);
    config.reconnectTimeoutMs = 2000;
    std::string authorization;
    std::string reason;
    assert(config.profile.BuildAuthorizationCredential(
        authorization, reason));
    WriteFile(credentials + "/hepta-ib-paper-authorization",
        authorization + "\n");
    const std::string socketPath = SocketPath(
        "hepta-ib-reconnect-control-batch");
    const std::string eventPath = SocketPath(
        "hepta-ib-reconnect-control-batch-events");
    const std::shared_ptr<FakeBrokerState> broker(new FakeBrokerState());
    const std::shared_ptr<MutableKillSwitch> killSwitch(
        new MutableKillSwitch());
    const std::shared_ptr<std::atomic<bool> > batchInjected(
        new std::atomic<bool>(false));
    const std::shared_ptr<std::atomic<bool> > reconnectComplete(
        new std::atomic<bool>(false));
    const std::shared_ptr<std::atomic<bool> > upstreamRestored(
        new std::atomic<bool>(false));
    IbPaperExecutionRuntimeTestHooks hooks;
    hooks.reconnectApiFactory = [broker]() {
        return std::unique_ptr<IIBApiWrapper>(new FakeIbWrapper(broker));
    };
    hooks.onStage = [broker, batchInjected, reconnectComplete,
                     upstreamRestored](const char* stage) {
        if (std::strcmp(stage, "before_broker_reconnect_attempt") == 0 &&
            !batchInjected->exchange(true))
        {
            // The reconnect wrapper will already enqueue 2110 from Connect().
            // Put a duplicate transport notification and its restore callback
            // behind it in the same PollOnce batch.  The restore must survive
            // the duplicate 509; otherwise DrainAdapterEvents clears the tail
            // and the reconnect waits until its bounded timeout.
            IBEvent duplicateTransport = Event(IBEventType::Error);
            duplicateTransport.key = "509";
            IBEvent restored = Event(IBEventType::Error);
            restored.key = "1102";
            std::lock_guard<std::mutex> lock(broker->injectedMutex);
            broker->injectedEvents.push_back(duplicateTransport);
            broker->injectedEvents.push_back(restored);
        }
        else if (std::strcmp(stage, "broker_reconnect_upstream_restored") == 0)
            upstreamRestored->store(true);
        else if (std::strcmp(stage, "broker_reconnect_complete") == 0)
            reconnectComplete->store(true);
    };
    config.listenFd = ActivatedSocket(socketPath);
    config.eventListenFd = ActivatedSocket(eventPath);
    IbPaperExecutionRuntimeComposition runtime(
        config, std::unique_ptr<IIBApiWrapper>(new FakeIbWrapper(broker)),
        hooks, killSwitch);
    assert(runtime.Start(reason));
    const int connectsBefore = broker->reconnectAttempts.load();
    broker->reconnectControlErrorOnConnect.store(2110);
    broker->emitControlErrorCode.store(509);
    for (int attempt = 0; attempt < 5000 &&
         !reconnectComplete->load() &&
         !runtime.HasFatalRuntimeError(&reason); ++attempt)
        std::this_thread::sleep_for(std::chrono::milliseconds(1));
    assert(reconnectComplete->load());
    assert(upstreamRestored->load());
    assert(!runtime.HasFatalRuntimeError(&reason));
    assert(broker->reconnectAttempts.load() == connectsBefore + 1);
    // Initial 509, then 2110/509/1102 on the reconnect wrapper.  In
    // particular, 1102 must be dequeued rather than discarded as tail data.
    assert(broker->controlErrorsDequeued.load() >= 4);
    assert(broker->refreshRequestsWhileUpstreamUnavailable.load() == 0);
    runtime.Stop();

    ::unlink(socketPath.c_str());
    ::unlink(eventPath.c_str());
    ::unlink((state + "/oms-journal.jsonl").c_str());
    ::unlink((state + "/ib-paper-runtime.lock").c_str());
    ::unlink((state + "/ib-observability.jsonl").c_str());
    ::unlink(FxCashRestartCheckpointPath(state).c_str());
    ::unlink((credentials + "/hepta-execution-fence").c_str());
    ::unlink((credentials + "/hepta-ib-paper-authorization").c_str());
    assert(::rmdir(credentials.c_str()) == 0);
    assert(::rmdir(state.c_str()) == 0);
}

void TestBrokerReconnectUpstreamUnavailableTimesOutClosed()
{
    const std::string state = TempDirectory(
        "/tmp/hepta-ib-upstream-timeout-state-XXXXXX");
    const std::string credentials = TempDirectory(
        "/tmp/hepta-ib-upstream-timeout-cred-XXXXXX");
    WriteFile(credentials + "/hepta-execution-fence",
        "HFC1\nfencing_token=77\ngeneration=9\n");
    IbPaperExecutionRuntimeConfig config =
        Config(-1, -1, state, credentials);
    // Keep startup readiness independent from the reconnect timeout under
    // test; 100ms made this fixture scheduler-sensitive on a busy host.
    config.readinessTimeoutMs = 1000;
    std::string authorization;
    std::string reason;
    assert(config.profile.BuildAuthorizationCredential(
        authorization, reason));
    WriteFile(credentials + "/hepta-ib-paper-authorization",
        authorization + "\n");
    const std::string socketPath = SocketPath(
        "hepta-ib-upstream-timeout");
    const std::string eventPath = SocketPath(
        "hepta-ib-upstream-timeout-events");
    const std::shared_ptr<FakeBrokerState> broker(new FakeBrokerState());
    const std::shared_ptr<MutableKillSwitch> killSwitch(
        new MutableKillSwitch());
    const std::shared_ptr<std::atomic<bool> > upstreamUnavailable(
        new std::atomic<bool>(false));
    IbPaperExecutionRuntimeTestHooks hooks;
    hooks.reconnectApiFactory = [broker]() {
        return std::unique_ptr<IIBApiWrapper>(new FakeIbWrapper(broker));
    };
    hooks.onStage = [upstreamUnavailable](const char* stage) {
        if (std::strcmp(stage,
                "broker_reconnect_upstream_unavailable") == 0)
            upstreamUnavailable->store(true);
    };
    config.listenFd = ActivatedSocket(socketPath);
    config.eventListenFd = ActivatedSocket(eventPath);
    IbPaperExecutionRuntimeComposition runtime(
        config, std::unique_ptr<IIBApiWrapper>(new FakeIbWrapper(broker)),
        hooks, killSwitch);
    assert(runtime.Start(reason));
    const int snapshotsBefore = broker->snapshotRequests.load();
    broker->reconnectControlErrorOnConnect.store(2110);
    broker->emitControlErrorCode.store(509);
    for (int attempt = 0; attempt < 5000 &&
         !runtime.HasFatalRuntimeError(&reason); ++attempt)
        std::this_thread::sleep_for(std::chrono::milliseconds(1));
    assert(upstreamUnavailable->load());
    assert(runtime.HasFatalRuntimeError(&reason));
    assert(reason == "IB_PAPER_BROKER_RECONNECT_UPSTREAM_TIMEOUT");
    assert(runtime.IsMutationBlocked(&reason));
    assert(broker->snapshotRequests.load() == snapshotsBefore);
    assert(broker->refreshRequestsWhileUpstreamUnavailable.load() == 0);
    assert(broker->sends == 0);
    runtime.Stop();

    ::unlink(socketPath.c_str());
    ::unlink(eventPath.c_str());
    ::unlink((state + "/oms-journal.jsonl").c_str());
    ::unlink((state + "/ib-paper-runtime.lock").c_str());
    ::unlink((state + "/ib-observability.jsonl").c_str());
    ::unlink(FxCashRestartCheckpointPath(state).c_str());
    ::unlink((credentials + "/hepta-execution-fence").c_str());
    ::unlink((credentials +
              "/hepta-ib-paper-authorization").c_str());
    assert(::rmdir(credentials.c_str()) == 0);
    assert(::rmdir(state.c_str()) == 0);
}

// Reconnect uses the same CASH-farm gate as startup.  A 2104 followed by a
// same-batch 2119 on the new connection epoch must consume the reconnect
// budget without issuing a second formal quote request.
void TestReconnectCashFarmReverseOrderBlocksQuote()
{
    const std::string state = TempDirectory(
        "/tmp/hepta-ib-reconnect-cash-farm-reverse-state-XXXXXX");
    const std::string credentials = TempDirectory(
        "/tmp/hepta-ib-reconnect-cash-farm-reverse-cred-XXXXXX");
    WriteFile(credentials + "/hepta-execution-fence",
        "HFC1\nfencing_token=77\ngeneration=9\n");
    IbPaperExecutionRuntimeConfig config =
        Config(-1, -1, state, credentials);
    config.readinessTimeoutMs = 1000;
    config.reconnectTimeoutMs = 1500;
    std::string authorization;
    std::string reason;
    assert(config.profile.BuildAuthorizationCredential(
        authorization, reason));
    WriteFile(credentials + "/hepta-ib-paper-authorization",
        authorization + "\n");
    const std::string socketPath = SocketPath(
        "hepta-ib-reconnect-cash-farm-reverse");
    const std::string eventPath = SocketPath(
        "hepta-ib-reconnect-cash-farm-reverse-events");
    const std::shared_ptr<FakeBrokerState> broker(new FakeBrokerState());
    const std::shared_ptr<std::atomic<bool> > injected(
        new std::atomic<bool>(false));
    const std::shared_ptr<std::atomic<bool> > waiting(
        new std::atomic<bool>(false));
    const std::shared_ptr<std::atomic<FakeIbWrapper*> > reconnectWrapper(
        new std::atomic<FakeIbWrapper*>(nullptr));
    IbPaperExecutionRuntimeTestHooks hooks;
    hooks.reconnectApiFactory = [broker, reconnectWrapper]() {
        FakeIbWrapper* wrapper = new FakeIbWrapper(broker);
        reconnectWrapper->store(wrapper);
        return std::unique_ptr<IIBApiWrapper>(wrapper);
    };
    hooks.onStage = [broker, injected, waiting, reconnectWrapper](
                        const char* stage) {
        // Connect() increments reconnectAttempts; count 2 is the first
        // reconnect wrapper, while count 1 belongs to initial startup.
        if (std::strcmp(stage, "after_adapter_poll_before_drain") == 0 &&
            broker->reconnectAttempts.load() >= 2 &&
            !injected->exchange(true))
        {
            IBEvent warning = Event(IBEventType::Error);
            warning.key = "2119";
            warning.value = "cashfarm";
            warning.connectionEpoch = broker->callbackEpoch.load();
            FakeIbWrapper* wrapper = reconnectWrapper->load();
            if (wrapper != nullptr)
                wrapper->InjectEventForTest(std::move(warning));
        }
        else if (std::strcmp(stage,
                     "broker_startup_market_data_farm_waiting") == 0)
            waiting->store(true);
    };
    config.listenFd = ActivatedSocket(socketPath);
    config.eventListenFd = ActivatedSocket(eventPath);
    IbPaperExecutionRuntimeComposition runtime(
        config, std::unique_ptr<IIBApiWrapper>(new FakeIbWrapper(broker)),
        hooks, std::shared_ptr<MutableKillSwitch>(new MutableKillSwitch()));
    assert(runtime.Start(reason));
    const int requestsBefore = broker->marketDataRequests.load();
    assert(requestsBefore == 1);
    broker->emitControlErrorCode.store(509);
    std::string fatalReason;
    for (int attempt = 0; attempt < 5000 &&
         !runtime.HasFatalRuntimeError(&fatalReason); ++attempt)
        std::this_thread::sleep_for(std::chrono::milliseconds(1));
    assert(runtime.HasFatalRuntimeError(&fatalReason));
    assert(fatalReason ==
        "IB_PAPER_BROKER_RECONNECT_MARKET_DATA_FARM_TIMEOUT");
    assert(waiting->load());
    assert(broker->reconnectAttempts.load() == 2);
    assert(broker->marketDataRequests.load() == requestsBefore);
    assert(!broker->marketDataRequestBefore2104.load());
    runtime.Stop();
    CleanupExternalRuntimeFixture(
        state, credentials, socketPath, eventPath);
}

void TestStartupRetriesTransientGatewayPortUnavailability()
{
    const std::string state = TempDirectory(
        "/tmp/hepta-ib-startup-connect-retry-state-XXXXXX");
    const std::string credentials = TempDirectory(
        "/tmp/hepta-ib-startup-connect-retry-cred-XXXXXX");
    WriteFile(credentials + "/hepta-execution-fence",
        "HFC1\nfencing_token=77\ngeneration=9\n");
    IbPaperExecutionRuntimeConfig config =
        Config(-1, -1, state, credentials);
    // This case deliberately spends three bounded backoff intervals in the
    // startup connect loop (100/200/300 ms). Keep the readiness budget
    // independent and comfortably above scheduler/event-drain jitter after
    // the fourth connect succeeds; the production default remains unchanged.
    config.readinessTimeoutMs = 1000;
    config.reconnectTimeoutMs = 2000;
    std::string authorization;
    std::string reason;
    assert(config.profile.BuildAuthorizationCredential(
        authorization, reason));
    WriteFile(credentials + "/hepta-ib-paper-authorization",
        authorization + "\n");
    const std::string socketPath = SocketPath(
        "hepta-ib-startup-connect-retry");
    const std::string eventPath = SocketPath(
        "hepta-ib-startup-connect-retry-events");
    const std::shared_ptr<FakeBrokerState> broker(new FakeBrokerState());
    broker->reconnectFailuresRemaining.store(3);
    const std::shared_ptr<MutableKillSwitch> killSwitch(
        new MutableKillSwitch());
    IbPaperExecutionRuntimeTestHooks hooks;
    hooks.reconnectApiFactory = [broker]() {
        return std::unique_ptr<IIBApiWrapper>(new FakeIbWrapper(broker));
    };
    config.listenFd = ActivatedSocket(socketPath);
    config.eventListenFd = ActivatedSocket(eventPath);
    IbPaperExecutionRuntimeComposition runtime(
        config, std::unique_ptr<IIBApiWrapper>(new FakeIbWrapper(broker)),
        hooks, killSwitch);
    assert(runtime.Start(reason));
    assert(broker->reconnectAttempts.load() == 4);
    assert(broker->sends == 0);
    runtime.Stop();

    ::unlink(socketPath.c_str());
    ::unlink(eventPath.c_str());
    ::unlink((state + "/oms-journal.jsonl").c_str());
    ::unlink((state + "/ib-paper-runtime.lock").c_str());
    ::unlink((state + "/ib-observability.jsonl").c_str());
    ::unlink(FxCashRestartCheckpointPath(state).c_str());
    ::unlink((credentials + "/hepta-execution-fence").c_str());
    ::unlink((credentials +
              "/hepta-ib-paper-authorization").c_str());
    assert(::rmdir(credentials.c_str()) == 0);
    assert(::rmdir(state.c_str()) == 0);
}

// A service stop can arrive while the broker startup state machine is waiting
// for the CASH-farm 2104 witness.  The owner cancellation probe must abort
// that wait promptly, close the adapter, and never enter formal quote
// admission.  This is the regression for the process-level SIGTERM queueing
// gap: the production daemon wires the same probe to sigtimedwait.
void TestStartupCancellationProbeAbortsReadinessWait()
{
    const std::string state = TempDirectory(
        "/tmp/hepta-ib-startup-cancel-state-XXXXXX");
    const std::string credentials = TempDirectory(
        "/tmp/hepta-ib-startup-cancel-cred-XXXXXX");
    const std::string socketPath = SocketPath("hepta-ib-startup-cancel");
    const std::string eventPath = SocketPath(
        "hepta-ib-startup-cancel-events");
    IbPaperExecutionRuntimeConfig config =
        Config(ActivatedSocket(socketPath), ActivatedSocket(eventPath),
               state, credentials);
    config.readinessTimeoutMs = 5000;
    config.reconnectTimeoutMs = 5000;
    // This is the legacy LocalMarketDay fixture (PAPER-V3), whereas the
    // external-limit helper intentionally requires the newer PAPER-V4
    // credential.  Build the credential directly so the cancellation test
    // does not alter either profile contract.
    WriteFile(credentials + "/hepta-execution-fence",
        "HFC1\nfencing_token=77\ngeneration=9\n");
    std::string authorization;
    std::string authorizationReason;
    assert(config.profile.BuildAuthorizationCredential(
        authorization, authorizationReason));
    WriteFile(credentials + "/hepta-ib-paper-authorization",
        authorization + "\n");
    const std::shared_ptr<FakeBrokerState> broker(new FakeBrokerState());
    // Hold the startup gate open: no positive CASH-farm witness means no
    // formal ReqMktData request is legal.
    broker->suppressCashFarmOnConnect.store(true);
    const std::shared_ptr<MutableKillSwitch> killSwitch(
        new MutableKillSwitch());
    IbPaperExecutionRuntimeComposition runtime(
        config,
        std::unique_ptr<IIBApiWrapper>(new FakeIbWrapper(broker)),
        IbPaperExecutionRuntimeTestHooks(), killSwitch);
    const std::shared_ptr<std::atomic<int> > probes(
        new std::atomic<int>(0));
    runtime.SetStartupCancellationProbe([probes]() {
        // The first few probes occur at the outer startup boundaries; wait
        // until the adapter has connected and the upstream wait is active so
        // this specifically exercises cancellation during readiness polling.
        return probes->fetch_add(1) >= 7;
    });
    const std::chrono::steady_clock::time_point startedAt =
        std::chrono::steady_clock::now();
    std::string reason;
    assert(!runtime.Start(reason));
    const long elapsedMs = std::chrono::duration_cast<std::chrono::milliseconds>(
        std::chrono::steady_clock::now() - startedAt).count();
    assert(reason == "IB_PAPER_STARTUP_CANCELLED");
    assert(probes->load() >= 8);
    assert(broker->reconnectAttempts.load() == 1);
    assert(broker->disconnectCalls.load() >= 1);
    assert(broker->marketDataRequests.load() == 0);
    assert(!runtime.Adapter().IsConnected());
    // A cancellation must be materially below the five-second readiness
    // deadline; leave generous scheduler headroom for a loaded CI host.
    assert(elapsedMs < 2000);
    runtime.Stop();
    CleanupExternalRuntimeFixture(
        state, credentials, socketPath, eventPath);
}

void TestStartupMarketData10197FailsClosedWithExplicitReason()
{
    const std::string state = TempDirectory(
        "/tmp/hepta-ib-startup-market-data-10197-state-XXXXXX");
    const std::string credentials = TempDirectory(
        "/tmp/hepta-ib-startup-market-data-10197-cred-XXXXXX");
    WriteFile(credentials + "/hepta-execution-fence",
        "HFC1\nfencing_token=77\ngeneration=9\n");
    IbPaperExecutionRuntimeConfig config =
        Config(-1, -1, state, credentials);
    config.readinessTimeoutMs = 500;
    config.reconnectTimeoutMs = 1000;
    std::string authorization;
    std::string reason;
    assert(config.profile.BuildAuthorizationCredential(
        authorization, reason));
    WriteFile(credentials + "/hepta-ib-paper-authorization",
        authorization + "\n");
    const std::string socketPath = SocketPath(
        "hepta-ib-startup-market-data-10197");
    const std::string eventPath = SocketPath(
        "hepta-ib-startup-market-data-10197-events");
    const std::shared_ptr<FakeBrokerState> broker(new FakeBrokerState());
    broker->marketDataErrorCodeOnRequest.store(10197);
    const std::shared_ptr<MutableKillSwitch> killSwitch(
        new MutableKillSwitch());
    config.listenFd = ActivatedSocket(socketPath);
    config.eventListenFd = ActivatedSocket(eventPath);
    IbPaperExecutionRuntimeComposition runtime(
        config, std::unique_ptr<IIBApiWrapper>(new FakeIbWrapper(broker)),
        IbPaperExecutionRuntimeTestHooks(), killSwitch);

    assert(!runtime.Start(reason));
    assert(reason == "IB_PAPER_REALTIME_MARKET_DATA_UNAVAILABLE_10197");
    std::string fatalReason;
    assert(runtime.HasFatalRuntimeError(&fatalReason));
    assert(fatalReason == reason);
    assert(runtime.IsMutationBlocked(&fatalReason));
    assert(fatalReason == reason);
    assert(broker->marketDataRequests.load() == 1);
    assert(broker->marketDataRequestId.load() == 1000001);
    assert(!broker->marketDataRequestBefore2104.load());
    assert(broker->marketDataCancels.load() == 1);
    assert(!runtime.Adapter().IsConnected());
    assert(broker->sends == 0);
    runtime.Stop();
    assert(broker->marketDataCancels.load() == 1);

    bool persisted = false;
    OmsJournal evidence;
    assert(evidence.Init(state + "/oms-journal.jsonl"));
    assert(evidence.Replay([&](const OmsJournalEvent& event) {
        if (event.eventType == "broker_error" &&
            event.orderId == 1000001)
        {
            persisted = event.brokerErrorCode == 10197 &&
                event.riskCode == "IB_ERROR_10197";
        }
    }) >= 0);
    assert(persisted);

    ::unlink(socketPath.c_str());
    ::unlink(eventPath.c_str());
    ::unlink((state + "/oms-journal.jsonl").c_str());
    ::unlink((state + "/ib-paper-runtime.lock").c_str());
    ::unlink((state + "/ib-observability.jsonl").c_str());
    ::unlink(FxCashRestartCheckpointPath(state).c_str());
    ::unlink((credentials + "/hepta-execution-fence").c_str());
    ::unlink((credentials + "/hepta-ib-paper-authorization").c_str());
    assert(::rmdir(credentials.c_str()) == 0);
    assert(::rmdir(state.c_str()) == 0);
}

// Real Gateway ordering regression: a generic market-data farm witness may
// arrive on connect while the CASH farm is still uninitialized.  No startup
// pre-readiness request is permitted; every CASH request must wait for a positive
// CASH 2104 in the same epoch, so this scenario fails closed with zero sends.
void TestStartupCashFarmGateRejectsMissing2104()
{
    const std::string state = TempDirectory(
        "/tmp/hepta-ib-startup-cash-gate-state-XXXXXX");
    const std::string credentials = TempDirectory(
        "/tmp/hepta-ib-startup-cash-gate-cred-XXXXXX");
    WriteFile(credentials + "/hepta-execution-fence",
        "HFC1\nfencing_token=77\ngeneration=9\n");
    IbPaperExecutionRuntimeConfig config =
        Config(-1, -1, state, credentials);
    config.readinessTimeoutMs = 1500;
    config.reconnectTimeoutMs = 3000;
    std::string authorization;
    std::string reason;
    assert(config.profile.BuildAuthorizationCredential(
        authorization, reason));
    WriteFile(credentials + "/hepta-ib-paper-authorization",
        authorization + "\n");
    const std::string socketPath = SocketPath(
        "hepta-ib-startup-cash-gate");
    const std::string eventPath = SocketPath(
        "hepta-ib-startup-cash-gate-events");
    const std::shared_ptr<FakeBrokerState> broker(new FakeBrokerState());
    broker->emitTransportFarmOnConnect.store(true);
    broker->suppressCashFarmOnConnect.store(true);
    config.listenFd = ActivatedSocket(socketPath);
    config.eventListenFd = ActivatedSocket(eventPath);
    IbPaperExecutionRuntimeComposition runtime(
        config, std::unique_ptr<IIBApiWrapper>(new FakeIbWrapper(broker)),
        IbPaperExecutionRuntimeTestHooks(),
        std::shared_ptr<MutableKillSwitch>(new MutableKillSwitch()));

    assert(!runtime.Start(reason));
    assert(reason == "IB_PAPER_STARTUP_MARKET_DATA_FARM_TIMEOUT");
    assert(broker->marketDataRequests.load() == 0);
    assert(!broker->marketDataRequestBefore2104.load());
    assert(broker->marketDataCancels.load() == 0);
    assert(broker->sends == 0);
    runtime.Stop();
    CleanupExternalRuntimeFixture(
        state, credentials, socketPath, eventPath);
}

// A current-epoch 10197 must stop startup before any formal quote request or
// order can be attempted; there is no alternate request path that could hide it.
void TestStartupCashFarmGate10197FailsClosed()
{
    const std::string state = TempDirectory(
        "/tmp/hepta-ib-startup-cash-gate-10197-state-XXXXXX");
    const std::string credentials = TempDirectory(
        "/tmp/hepta-ib-startup-cash-gate-10197-cred-XXXXXX");
    WriteFile(credentials + "/hepta-execution-fence",
        "HFC1\nfencing_token=77\ngeneration=9\n");
    IbPaperExecutionRuntimeConfig config =
        Config(-1, -1, state, credentials);
    config.readinessTimeoutMs = 700;
    config.reconnectTimeoutMs = 1500;
    std::string authorization;
    std::string reason;
    assert(config.profile.BuildAuthorizationCredential(
        authorization, reason));
    WriteFile(credentials + "/hepta-ib-paper-authorization",
        authorization + "\n");
    const std::string socketPath = SocketPath(
        "hepta-ib-startup-cash-gate-10197");
    const std::string eventPath = SocketPath(
        "hepta-ib-startup-cash-gate-10197-events");
    const std::shared_ptr<FakeBrokerState> broker(new FakeBrokerState());
    broker->emitTransportFarmOnConnect.store(true);
    broker->suppressCashFarmOnConnect.store(true);
    broker->startupControlErrorOnConnect.store(10197);
    config.listenFd = ActivatedSocket(socketPath);
    config.eventListenFd = ActivatedSocket(eventPath);
    IbPaperExecutionRuntimeComposition runtime(
        config, std::unique_ptr<IIBApiWrapper>(new FakeIbWrapper(broker)),
        IbPaperExecutionRuntimeTestHooks(),
        std::shared_ptr<MutableKillSwitch>(new MutableKillSwitch()));

    assert(!runtime.Start(reason));
    assert(reason == "IB_PAPER_REALTIME_MARKET_DATA_UNAVAILABLE_10197");
    assert(broker->marketDataRequests.load() == 0);
    assert(broker->sends == 0);
    std::string fatalReason;
    assert(runtime.HasFatalRuntimeError(&fatalReason));
    assert(fatalReason == reason);
    runtime.Stop();
    CleanupExternalRuntimeFixture(
        state, credentials, socketPath, eventPath);
}

// A socket and generic-farm callback are not enough to authorize a CASH
// request.  No request is attempted while the CASH 2104 witness is absent.
void TestStartupCashFarmGateRequires2104()
{
    const std::string state = TempDirectory(
        "/tmp/hepta-ib-startup-cash-gate-next-id-state-XXXXXX");
    const std::string credentials = TempDirectory(
        "/tmp/hepta-ib-startup-cash-gate-next-id-cred-XXXXXX");
    WriteFile(credentials + "/hepta-execution-fence",
        "HFC1\nfencing_token=77\ngeneration=9\n");
    IbPaperExecutionRuntimeConfig config =
        Config(-1, -1, state, credentials);
    config.readinessTimeoutMs = 700;
    config.reconnectTimeoutMs = 1500;
    std::string authorization;
    std::string reason;
    assert(config.profile.BuildAuthorizationCredential(
        authorization, reason));
    WriteFile(credentials + "/hepta-ib-paper-authorization",
        authorization + "\n");
    const std::string socketPath = SocketPath(
        "hepta-ib-startup-cash-gate-next-id");
    const std::string eventPath = SocketPath(
        "hepta-ib-startup-cash-gate-next-id-events");
    const std::shared_ptr<FakeBrokerState> broker(new FakeBrokerState());
    broker->nextOrderId = 0;
    broker->emitTransportFarmOnConnect.store(true);
    broker->suppressCashFarmOnConnect.store(true);
    config.listenFd = ActivatedSocket(socketPath);
    config.eventListenFd = ActivatedSocket(eventPath);
    IbPaperExecutionRuntimeComposition runtime(
        config, std::unique_ptr<IIBApiWrapper>(new FakeIbWrapper(broker)),
        IbPaperExecutionRuntimeTestHooks(),
        std::shared_ptr<MutableKillSwitch>(new MutableKillSwitch()));

    assert(!runtime.Start(reason));
    assert(reason == "IB_PAPER_STARTUP_MARKET_DATA_FARM_TIMEOUT");
    assert(broker->marketDataRequests.load() == 0);
    assert(broker->marketDataCancels.load() == 0);
    assert(broker->sends == 0);
    runtime.Stop();
    CleanupExternalRuntimeFixture(
        state, credentials, socketPath, eventPath);
}

// A later 2103 invalidates a previously observed generic 2104 transport
// witness.  No CASH request is permitted while the farm is reported broken.
void TestStartupCashFarmGateBlocksAfterFarmLoss()
{
    const std::string state = TempDirectory(
        "/tmp/hepta-ib-startup-cash-gate-2103-state-XXXXXX");
    const std::string credentials = TempDirectory(
        "/tmp/hepta-ib-startup-cash-gate-2103-cred-XXXXXX");
    WriteFile(credentials + "/hepta-execution-fence",
        "HFC1\nfencing_token=77\ngeneration=9\n");
    IbPaperExecutionRuntimeConfig config =
        Config(-1, -1, state, credentials);
    config.readinessTimeoutMs = 700;
    config.reconnectTimeoutMs = 1500;
    std::string authorization;
    std::string reason;
    assert(config.profile.BuildAuthorizationCredential(
        authorization, reason));
    WriteFile(credentials + "/hepta-ib-paper-authorization",
        authorization + "\n");
    const std::string socketPath = SocketPath(
        "hepta-ib-startup-cash-gate-2103");
    const std::string eventPath = SocketPath(
        "hepta-ib-startup-cash-gate-2103-events");
    const std::shared_ptr<FakeBrokerState> broker(new FakeBrokerState());
    broker->emitTransportFarmOnConnect.store(true);
    broker->suppressCashFarmOnConnect.store(true);
    broker->emitControlErrorCode.store(2103);
    config.listenFd = ActivatedSocket(socketPath);
    config.eventListenFd = ActivatedSocket(eventPath);
    IbPaperExecutionRuntimeComposition runtime(
        config, std::unique_ptr<IIBApiWrapper>(new FakeIbWrapper(broker)),
        IbPaperExecutionRuntimeTestHooks(),
        std::shared_ptr<MutableKillSwitch>(new MutableKillSwitch()));

    assert(!runtime.Start(reason));
    assert(reason == "IB_PAPER_STARTUP_MARKET_DATA_FARM_TIMEOUT");
    assert(broker->marketDataRequests.load() == 0);
    assert(broker->marketDataCancels.load() == 0);
    assert(broker->sends == 0);
    runtime.Stop();
    CleanupExternalRuntimeFixture(
        state, credentials, socketPath, eventPath);
}

// A generic market-data 2104 must not outrank an unresolved upstream 1100.
// Until the session is restored, all CASH requests remain unopened.
void TestStartupCashFarmGateWaitsForUpstreamRestore()
{
    const std::string state = TempDirectory(
        "/tmp/hepta-ib-startup-cash-gate-upstream-state-XXXXXX");
    const std::string credentials = TempDirectory(
        "/tmp/hepta-ib-startup-cash-gate-upstream-cred-XXXXXX");
    WriteFile(credentials + "/hepta-execution-fence",
        "HFC1\nfencing_token=77\ngeneration=9\n");
    IbPaperExecutionRuntimeConfig config =
        Config(-1, -1, state, credentials);
    config.readinessTimeoutMs = 700;
    config.reconnectTimeoutMs = 1500;
    std::string authorization;
    std::string reason;
    assert(config.profile.BuildAuthorizationCredential(
        authorization, reason));
    WriteFile(credentials + "/hepta-ib-paper-authorization",
        authorization + "\n");
    const std::string socketPath = SocketPath(
        "hepta-ib-startup-cash-gate-upstream");
    const std::string eventPath = SocketPath(
        "hepta-ib-startup-cash-gate-upstream-events");
    const std::shared_ptr<FakeBrokerState> broker(new FakeBrokerState());
    broker->emitTransportFarmOnConnect.store(true);
    broker->suppressCashFarmOnConnect.store(true);
    broker->startupControlErrorOnConnect.store(1100);
    config.listenFd = ActivatedSocket(socketPath);
    config.eventListenFd = ActivatedSocket(eventPath);
    IbPaperExecutionRuntimeComposition runtime(
        config, std::unique_ptr<IIBApiWrapper>(new FakeIbWrapper(broker)),
        IbPaperExecutionRuntimeTestHooks(),
        std::shared_ptr<MutableKillSwitch>(new MutableKillSwitch()));

    assert(!runtime.Start(reason));
    assert(reason == "IB_PAPER_STARTUP_UPSTREAM_TIMEOUT");
    assert(broker->marketDataRequests.load() == 0);
    assert(broker->marketDataCancels.load() == 0);
    assert(broker->sends == 0);
    runtime.Stop();
    CleanupExternalRuntimeFixture(
        state, credentials, socketPath, eventPath);
}

// A control callback that is already queued after the positive 2104 gate but
// before quote admission begins must prevent publication of the old plan.
void TestQuoteAdmissionRejectsPendingControlBeforeBegin()
{
    const std::string state = TempDirectory(
        "/tmp/hepta-ib-quote-admission-pending-state-XXXXXX");
    const std::string credentials = TempDirectory(
        "/tmp/hepta-ib-quote-admission-pending-cred-XXXXXX");
    WriteFile(credentials + "/hepta-execution-fence",
        "HFC1\nfencing_token=77\ngeneration=9\n");
    IbPaperExecutionRuntimeConfig config =
        Config(-1, -1, state, credentials);
    std::string authorization;
    std::string reason;
    assert(config.profile.BuildAuthorizationCredential(
        authorization, reason));
    WriteFile(credentials + "/hepta-ib-paper-authorization",
        authorization + "\n");
    const std::string socketPath =
        SocketPath("hepta-ib-quote-admission-pending");
    const std::string eventPath =
        SocketPath("hepta-ib-quote-admission-pending-events");
    const std::shared_ptr<FakeBrokerState> broker(new FakeBrokerState());
    FakeIbWrapper* wrapper = new FakeIbWrapper(broker);
    const std::shared_ptr<std::atomic<bool> > injected(
        new std::atomic<bool>(false));
    IbPaperExecutionRuntimeTestHooks hooks;
    hooks.onStage = [wrapper, injected](const char* stage) {
        if (std::strcmp(stage, "before_quote_admission_begin") != 0 ||
            injected->exchange(true)) return;
        IBEvent error = Event(IBEventType::Error);
        error.key = "10197";
        error.value = "simulated competing live session";
        wrapper->InjectEventForTest(std::move(error));
    };
    config.listenFd = ActivatedSocket(socketPath);
    config.eventListenFd = ActivatedSocket(eventPath);
    IbPaperExecutionRuntimeComposition runtime(
        config, std::unique_ptr<IIBApiWrapper>(wrapper), hooks,
        std::shared_ptr<MutableKillSwitch>(new MutableKillSwitch()));
    assert(!runtime.Start(reason));
    assert(reason ==
        "IB_PAPER_REALTIME_MARKET_DATA_UNAVAILABLE_10197");
    assert(broker->marketDataRequests.load() == 0);
    assert(broker->marketDataCancels.load() == 0);
    assert(broker->sends == 0);
    runtime.Stop();
    CleanupExternalRuntimeFixture(
        state, credentials, socketPath, eventPath);
}

// A blocking callback arriving at the check-to-send boundary must be observed
// by the wrapper's short admission reservation, without blocking EReader.
void TestQuoteAdmissionSerializesFormalRequestCheck()
{
    const std::string state = TempDirectory(
        "/tmp/hepta-ib-quote-admission-serialize-state-XXXXXX");
    const std::string credentials = TempDirectory(
        "/tmp/hepta-ib-quote-admission-serialize-cred-XXXXXX");
    WriteFile(credentials + "/hepta-execution-fence",
        "HFC1\nfencing_token=77\ngeneration=9\n");
    IbPaperExecutionRuntimeConfig config =
        Config(-1, -1, state, credentials);
    std::string authorization;
    std::string reason;
    assert(config.profile.BuildAuthorizationCredential(
        authorization, reason));
    WriteFile(credentials + "/hepta-ib-paper-authorization",
        authorization + "\n");
    const std::string socketPath =
        SocketPath("hepta-ib-quote-admission-serialize");
    const std::string eventPath =
        SocketPath("hepta-ib-quote-admission-serialize-events");
    const std::shared_ptr<FakeBrokerState> broker(new FakeBrokerState());
    broker->injectMarketDataErrorBeforeRequest.store(true);
    config.listenFd = ActivatedSocket(socketPath);
    config.eventListenFd = ActivatedSocket(eventPath);
    IbPaperExecutionRuntimeComposition runtime(
        config, std::unique_ptr<IIBApiWrapper>(new FakeIbWrapper(broker)),
        IbPaperExecutionRuntimeTestHooks(),
        std::shared_ptr<MutableKillSwitch>(new MutableKillSwitch()));
    assert(!runtime.Start(reason));
    assert(reason ==
        "IB_PAPER_REALTIME_MARKET_DATA_UNAVAILABLE_10197");
    assert(broker->marketDataRequests.load() == 0);
    assert(broker->marketDataCancels.load() == 0);
    assert(broker->sends == 0);
    runtime.Stop();
    CleanupExternalRuntimeFixture(
        state, credentials, socketPath, eventPath);
}

// IB error 1101 means connectivity was restored but market data was lost.
// It is not a harmless control acknowledgement: a quote admission that sees
// it must fail closed and require a fresh recovery boundary.  Keep this
// regression next to the 10197 admission test so the fake broker exercises
// the same callback-to-send edge as the production wrapper classifier.
void TestQuoteAdmissionDataLoss1101BlocksFormalRequest()
{
    const std::string state = TempDirectory(
        "/tmp/hepta-ib-quote-admission-1101-state-XXXXXX");
    const std::string credentials = TempDirectory(
        "/tmp/hepta-ib-quote-admission-1101-cred-XXXXXX");
    WriteFile(credentials + "/hepta-execution-fence",
        "HFC1\nfencing_token=77\ngeneration=9\n");
    IbPaperExecutionRuntimeConfig config =
        Config(-1, -1, state, credentials);
    std::string authorization;
    std::string reason;
    assert(config.profile.BuildAuthorizationCredential(
        authorization, reason));
    WriteFile(credentials + "/hepta-ib-paper-authorization",
        authorization + "\n");
    const std::string socketPath =
        SocketPath("hepta-ib-quote-admission-1101");
    const std::string eventPath =
        SocketPath("hepta-ib-quote-admission-1101-events");
    const std::shared_ptr<FakeBrokerState> broker(new FakeBrokerState());
    broker->injectMarketDataErrorBeforeRequest.store(true);
    broker->marketDataErrorCodeBeforeRequest.store(1101);
    config.listenFd = ActivatedSocket(socketPath);
    config.eventListenFd = ActivatedSocket(eventPath);
    IbPaperExecutionRuntimeComposition runtime(
        config, std::unique_ptr<IIBApiWrapper>(new FakeIbWrapper(broker)),
        IbPaperExecutionRuntimeTestHooks(),
        std::shared_ptr<MutableKillSwitch>(new MutableKillSwitch()));

    assert(!runtime.Start(reason));
    // The data-loss witness is classified as blocking before the fake broker
    // increments its formal request counter.  Runtime may choose a generic
    // admission/recovery reason, but it must never publish a quote leg.
    assert(!reason.empty());
    assert(broker->marketDataRequests.load() == 0);
    assert(broker->marketDataCancels.load() == 0);
    assert(broker->sends == 0);
    runtime.Stop();
    CleanupExternalRuntimeFixture(
        state, credentials, socketPath, eventPath);
}

// Conversely, IB error 1102 says connectivity was restored with the data
// maintained.  It remains a recoverable control notice and must not poison a
// formal quote admission on an otherwise healthy epoch.
void TestQuoteAdmissionDataMaintained1102RemainsRecoverable()
{
    const std::string state = TempDirectory(
        "/tmp/hepta-ib-quote-admission-1102-state-XXXXXX");
    const std::string credentials = TempDirectory(
        "/tmp/hepta-ib-quote-admission-1102-cred-XXXXXX");
    WriteFile(credentials + "/hepta-execution-fence",
        "HFC1\nfencing_token=77\ngeneration=9\n");
    IbPaperExecutionRuntimeConfig config =
        Config(-1, -1, state, credentials);
    std::string authorization;
    std::string reason;
    assert(config.profile.BuildAuthorizationCredential(
        authorization, reason));
    WriteFile(credentials + "/hepta-ib-paper-authorization",
        authorization + "\n");
    const std::string socketPath =
        SocketPath("hepta-ib-quote-admission-1102");
    const std::string eventPath =
        SocketPath("hepta-ib-quote-admission-1102-events");
    const std::shared_ptr<FakeBrokerState> broker(new FakeBrokerState());
    broker->injectMarketDataErrorBeforeRequest.store(true);
    broker->marketDataErrorCodeBeforeRequest.store(1102);
    config.listenFd = ActivatedSocket(socketPath);
    config.eventListenFd = ActivatedSocket(eventPath);
    IbPaperExecutionRuntimeComposition runtime(
        config, std::unique_ptr<IIBApiWrapper>(new FakeIbWrapper(broker)),
        IbPaperExecutionRuntimeTestHooks(),
        std::shared_ptr<MutableKillSwitch>(new MutableKillSwitch()));

    assert(runtime.Start(reason));
    assert(reason.empty());
    assert(broker->marketDataRequests.load() == 1);
    assert(broker->sends == 0);
    runtime.Stop();
    CleanupExternalRuntimeFixture(
        state, credentials, socketPath, eventPath);
}

// A transport can disappear during the synchronous reqMktData call without
// producing a connectionClosed callback on the EReader path.  The wrapper's
// post-request connection check must turn that silent transition into a
// blocking witness and unwind the quote admission transaction.
void TestQuoteAdmissionSilentDisconnectAfterRequestFailsClosed()
{
    const std::string state = TempDirectory(
        "/tmp/hepta-ib-quote-admission-silent-close-state-XXXXXX");
    const std::string credentials = TempDirectory(
        "/tmp/hepta-ib-quote-admission-silent-close-cred-XXXXXX");
    WriteFile(credentials + "/hepta-execution-fence",
        "HFC1\nfencing_token=77\ngeneration=9\n");
    IbPaperExecutionRuntimeConfig config =
        Config(-1, -1, state, credentials);
    config.readinessTimeoutMs = 500;
    config.reconnectTimeoutMs = 1000;
    std::string authorization;
    std::string reason;
    assert(config.profile.BuildAuthorizationCredential(
        authorization, reason));
    WriteFile(credentials + "/hepta-ib-paper-authorization",
        authorization + "\n");
    const std::string socketPath = SocketPath(
        "hepta-ib-quote-admission-silent-close");
    const std::string eventPath = SocketPath(
        "hepta-ib-quote-admission-silent-close-events");
    const std::shared_ptr<FakeBrokerState> broker(new FakeBrokerState());
    broker->disconnectDuringMarketDataRequest.store(true);
    config.listenFd = ActivatedSocket(socketPath);
    config.eventListenFd = ActivatedSocket(eventPath);
    IbPaperExecutionRuntimeComposition runtime(
        config, std::unique_ptr<IIBApiWrapper>(new FakeIbWrapper(broker)),
        IbPaperExecutionRuntimeTestHooks(),
        std::shared_ptr<MutableKillSwitch>(new MutableKillSwitch()));

    assert(!runtime.Start(reason));
    assert(reason == "IB_PAPER_STARTUP_BROKER_CONNECTION_CLOSED");
    std::string fatalReason;
    assert(runtime.HasFatalRuntimeError(&fatalReason));
    assert(fatalReason == reason);
    assert(runtime.IsMutationBlocked(&fatalReason));
    assert(fatalReason == reason);
    // The request may have reached IB, so it remains in the accepted-id
    // cleanup set even though publication is rejected.
    assert(broker->marketDataRequests.load() == 1);
    assert(broker->marketDataRequestId.load() == 1000001);
    // Once the close witness has been drained the adapter refuses to issue a
    // cancellation over the dead socket; this is the safe outcome for an
    // uncertain request and must not be mistaken for a cleanup leak.
    assert(broker->marketDataCancels.load() == 0);
    assert(!runtime.Adapter().IsConnected());
    assert(broker->sends == 0);
    runtime.Stop();
    assert(broker->marketDataCancels.load() == 0);

    ::unlink(socketPath.c_str());
    ::unlink(eventPath.c_str());
    ::unlink((state + "/oms-journal.jsonl").c_str());
    ::unlink((state + "/ib-paper-runtime.lock").c_str());
    ::unlink((state + "/ib-observability.jsonl").c_str());
    ::unlink(FxCashRestartCheckpointPath(state).c_str());
    ::unlink((credentials + "/hepta-execution-fence").c_str());
    ::unlink((credentials + "/hepta-ib-paper-authorization").c_str());
    assert(::rmdir(credentials.c_str()) == 0);
    assert(::rmdir(state.c_str()) == 0);
}

void TestQuoteAdmissionDelayed10197FailsBeforePublication()
{
    const std::string state = TempDirectory(
        "/tmp/hepta-ib-quote-admission-delayed-state-XXXXXX");
    const std::string credentials = TempDirectory(
        "/tmp/hepta-ib-quote-admission-delayed-cred-XXXXXX");
    WriteFile(credentials + "/hepta-execution-fence",
        "HFC1\nfencing_token=77\ngeneration=9\n");
    IbPaperExecutionRuntimeConfig config =
        Config(-1, -1, state, credentials);
    config.readinessTimeoutMs = 1000;
    std::string authorization;
    std::string reason;
    assert(config.profile.BuildAuthorizationCredential(
        authorization, reason));
    WriteFile(credentials + "/hepta-ib-paper-authorization",
        authorization + "\n");
    const std::string socketPath = SocketPath(
        "hepta-ib-quote-admission-delayed");
    const std::string eventPath = SocketPath(
        "hepta-ib-quote-admission-delayed-events");
    const std::shared_ptr<FakeBrokerState> broker(new FakeBrokerState());
    broker->injectMarketDataErrorAfterRequest.store(true);
    config.listenFd = ActivatedSocket(socketPath);
    config.eventListenFd = ActivatedSocket(eventPath);
    IbPaperExecutionRuntimeComposition runtime(
        config, std::unique_ptr<IIBApiWrapper>(new FakeIbWrapper(broker)),
        IbPaperExecutionRuntimeTestHooks(),
        std::shared_ptr<MutableKillSwitch>(new MutableKillSwitch()));
    assert(!runtime.Start(reason));
    assert(reason ==
        "IB_PAPER_REALTIME_MARKET_DATA_UNAVAILABLE_10197");
    assert(broker->marketDataRequests.load() == 1);
    assert(broker->marketDataCancels.load() == 1);
    assert(broker->sends == 0);
    runtime.Stop();
    assert(broker->marketDataCancels.load() == 1);
    CleanupExternalRuntimeFixture(
        state, credentials, socketPath, eventPath);
}

void TestQuoteAdmissionExceptionCancelsUncertainRequest()
{
    const std::string state = TempDirectory(
        "/tmp/hepta-ib-quote-admission-exception-state-XXXXXX");
    const std::string credentials = TempDirectory(
        "/tmp/hepta-ib-quote-admission-exception-cred-XXXXXX");
    WriteFile(credentials + "/hepta-execution-fence",
        "HFC1\nfencing_token=77\ngeneration=9\n");
    IbPaperExecutionRuntimeConfig config =
        Config(-1, -1, state, credentials);
    config.readinessTimeoutMs = 1000;
    std::string authorization;
    std::string reason;
    assert(config.profile.BuildAuthorizationCredential(
        authorization, reason));
    WriteFile(credentials + "/hepta-ib-paper-authorization",
        authorization + "\n");
    const std::string socketPath = SocketPath(
        "hepta-ib-quote-admission-exception");
    const std::string eventPath = SocketPath(
        "hepta-ib-quote-admission-exception-events");
    const std::shared_ptr<FakeBrokerState> broker(new FakeBrokerState());
    broker->throwMarketDataAfterSideEffect.store(true);
    config.listenFd = ActivatedSocket(socketPath);
    config.eventListenFd = ActivatedSocket(eventPath);
    IbPaperExecutionRuntimeComposition runtime(
        config, std::unique_ptr<IIBApiWrapper>(new FakeIbWrapper(broker)),
        IbPaperExecutionRuntimeTestHooks(),
        std::shared_ptr<MutableKillSwitch>(new MutableKillSwitch()));
    assert(!runtime.Start(reason));
    assert(reason == "IB_PAPER_QUOTE_ADMISSION_EXCEPTION");
    std::string fatalReason;
    assert(runtime.HasFatalRuntimeError(&fatalReason));
    assert(fatalReason == reason);
    assert(broker->marketDataRequests.load() == 1);
    assert(broker->marketDataCancels.load() == 1);
    assert(broker->sends == 0);
    runtime.Stop();
    assert(broker->marketDataCancels.load() == 1);
    CleanupExternalRuntimeFixture(
        state, credentials, socketPath, eventPath);
}

void TestCurrentEpochMarketData10197IgnoresCallbackIdBeforeDispatch()
{
    // Exercise the classifier at the exact admission point where BeginCycle
    // has published an active quote leg but RecordDispatchResult has not yet
    // acknowledged reqMktData().  Both IB's global sentinels (-1 and 0) and a
    // mismatched positive id must still be treated as the same current-epoch
    // 10197 witness.
    const std::shared_ptr<FakeBrokerState> broker(new FakeBrokerState());
    std::unique_ptr<IIBApiWrapper> wrapper(new FakeIbWrapper(broker));
    HeptaIBGatewayAdapter adapter(std::move(wrapper));
    HeptaIBConfig adapterConfig;
    adapterConfig.account = "DU123456";
    IBContractLite contract;
    contract.symbol = "EUR";
    contract.secType = "CASH";
    contract.exchange = "IDEALPRO";
    contract.currency = "USD";
    adapterConfig.authoritativeCashFxContracts["EUR.USD"] = contract;
    adapterConfig.authoritativeCashFxBaselines["EUR.USD"] = 0.0;
    adapterConfig.authoritativeCashFxStartupObservedBalances["EUR.USD"] =
        0.0;
    assert(adapter.Init(adapterConfig));
    assert(adapter.Connect());

    AuthoritativeTradingSnapshotStore snapshots;
    IBAuthoritativeQuoteSubscriptionSet subscriptions(snapshots, 1000001);
    std::map<std::string, IBContractLite> contracts;
    contracts["EUR.USD"] = contract;
    std::string reason;
    assert(subscriptions.Configure(contracts, "EUR.USD", reason));
    const IBAuthoritativeQuoteSubscriptionPlan plan =
        subscriptions.BeginCycle(adapter.GetConnectionEpoch(), 1, 1);
    assert(plan.accepted);
    const IBAuthoritativeQuoteSubscriptionHealth health =
        subscriptions.GetHealth();
    assert(health.contracts.find("EUR.USD") != health.contracts.end());
    assert(health.contracts.find("EUR.USD")->second.active);
    assert(!health.contracts.find("EUR.USD")->second.dispatchAccepted);

    IBEvent error = Event(IBEventType::Error, -1);
    error.key = "10197";
    error.connectionEpoch = adapter.GetConnectionEpoch();
    assert(ib_paper_execution_runtime_internal::IsMarketData10197(
        error, &subscriptions, &adapter));
    error.id = 0;
    assert(ib_paper_execution_runtime_internal::IsMarketData10197(
        error, &subscriptions, &adapter));
    error.id = 987654;
    assert(ib_paper_execution_runtime_internal::IsMarketData10197(
        error, &subscriptions, &adapter));

    adapter.Disconnect();
}

void TestStartupCurrentEpochMarketData10197WithoutRequestIdFailsClosed()
{
    const std::string state = TempDirectory(
        "/tmp/hepta-ib-startup-global-market-data-10197-state-XXXXXX");
    const std::string credentials = TempDirectory(
        "/tmp/hepta-ib-startup-global-market-data-10197-cred-XXXXXX");
    WriteFile(credentials + "/hepta-execution-fence",
        "HFC1\nfencing_token=77\ngeneration=9\n");
    IbPaperExecutionRuntimeConfig config =
        Config(-1, -1, state, credentials);
    config.readinessTimeoutMs = 500;
    config.reconnectTimeoutMs = 1000;
    std::string authorization;
    std::string reason;
    assert(config.profile.BuildAuthorizationCredential(
        authorization, reason));
    WriteFile(credentials + "/hepta-ib-paper-authorization",
        authorization + "\n");
    const std::string socketPath = SocketPath(
        "hepta-ib-startup-global-market-data-10197");
    const std::string eventPath = SocketPath(
        "hepta-ib-startup-global-market-data-10197-events");
    const std::shared_ptr<FakeBrokerState> broker(new FakeBrokerState());
    // Connect-time broker errors have the IB global callback id (0), before
    // any authoritative quote cycle or dispatch acknowledgement exists.
    broker->startupControlErrorOnConnect.store(10197);
    config.listenFd = ActivatedSocket(socketPath);
    config.eventListenFd = ActivatedSocket(eventPath);
    IbPaperExecutionRuntimeComposition runtime(
        config, std::unique_ptr<IIBApiWrapper>(new FakeIbWrapper(broker)),
        IbPaperExecutionRuntimeTestHooks(),
        std::shared_ptr<MutableKillSwitch>(new MutableKillSwitch()));

    assert(!runtime.Start(reason));
    assert(reason == "IB_PAPER_REALTIME_MARKET_DATA_UNAVAILABLE_10197");
    std::string fatalReason;
    assert(runtime.HasFatalRuntimeError(&fatalReason));
    assert(fatalReason == reason);
    assert(runtime.IsMutationBlocked(&fatalReason));
    assert(fatalReason == reason);
    // The global callback must stop startup before the first formal request;
    // no request-id correlation or delayed-data fallback is allowed.
    assert(broker->marketDataRequests.load() == 0);
    assert(!broker->marketDataRequestBefore2104.load());
    assert(broker->marketDataCancels.load() == 0);
    assert(!runtime.Adapter().IsConnected());
    runtime.Stop();

    bool persisted = false;
    OmsJournal evidence;
    assert(evidence.Init(state + "/oms-journal.jsonl"));
    assert(evidence.Replay([&](const OmsJournalEvent& event) {
        if (event.eventType == "broker_error" && event.orderId == 0)
        {
            persisted = event.brokerErrorCode == 10197 &&
                event.riskCode == "IB_ERROR_10197";
        }
    }) >= 0);
    assert(persisted);

    ::unlink(socketPath.c_str());
    ::unlink(eventPath.c_str());
    ::unlink((state + "/oms-journal.jsonl").c_str());
    ::unlink((state + "/ib-paper-runtime.lock").c_str());
    ::unlink((state + "/ib-observability.jsonl").c_str());
    ::unlink(FxCashRestartCheckpointPath(state).c_str());
    ::unlink((credentials + "/hepta-execution-fence").c_str());
    ::unlink((credentials + "/hepta-ib-paper-authorization").c_str());
    assert(::rmdir(credentials.c_str()) == 0);
    assert(::rmdir(state.c_str()) == 0);
}

void TestStartupCashFarmPositiveGateRejectsLate2119Race()
{
    const std::string state = TempDirectory(
        "/tmp/hepta-ib-startup-cash-farm-gate-state-XXXXXX");
    const std::string credentials = TempDirectory(
        "/tmp/hepta-ib-startup-cash-farm-gate-cred-XXXXXX");
    WriteFile(credentials + "/hepta-execution-fence",
        "HFC1\nfencing_token=77\ngeneration=9\n");
    IbPaperExecutionRuntimeConfig config =
        Config(-1, -1, state, credentials);
    // Leave enough startup budget for the deliberately delayed 2119/2104
    // pair while keeping this regression test bounded.
    config.readinessTimeoutMs = 1500;
    config.reconnectTimeoutMs = 3000;
    std::string authorization;
    std::string reason;
    assert(config.profile.BuildAuthorizationCredential(
        authorization, reason));
    WriteFile(credentials + "/hepta-ib-paper-authorization",
        authorization + "\n");
    const std::string socketPath = SocketPath(
        "hepta-ib-startup-cash-farm-gate");
    const std::string eventPath = SocketPath(
        "hepta-ib-startup-cash-farm-gate-events");
    const std::shared_ptr<FakeBrokerState> broker(new FakeBrokerState());
    // Deliver 2119 after the old timer warm-up point and 2104 shortly
    // afterwards. No market-data request may be sent in that interval.
    broker->cashFarmWarningDelayMs.store(450);
    broker->cashFarmReadyDelayMs.store(800);
    broker->uppercaseCashFarmDescription.store(true);
    const std::shared_ptr<MutableKillSwitch> killSwitch(
        new MutableKillSwitch());
    const std::shared_ptr<std::atomic<bool> > farmWaiting(
        new std::atomic<bool>(false));
    const std::shared_ptr<std::atomic<bool> > farmRestored(
        new std::atomic<bool>(false));
    IbPaperExecutionRuntimeTestHooks hooks;
    hooks.onStage = [farmWaiting, farmRestored](const char* stage) {
        if (std::strcmp(stage,
                "broker_startup_market_data_farm_waiting") == 0)
            farmWaiting->store(true);
        else if (std::strcmp(stage,
                     "broker_startup_market_data_farm_restored") == 0)
            farmRestored->store(true);
    };
    config.listenFd = ActivatedSocket(socketPath);
    config.eventListenFd = ActivatedSocket(eventPath);
    IbPaperExecutionRuntimeComposition runtime(
        config, std::unique_ptr<IIBApiWrapper>(new FakeIbWrapper(broker)),
        hooks, killSwitch);
    bool started = false;
    std::string startReason;
    std::atomic<bool> startDone(false);
    std::thread starter([&runtime, &started, &startReason, &startDone]() {
        started = runtime.Start(startReason);
        startDone.store(true);
    });
    for (int attempt = 0; attempt < 2500 && !farmWaiting->load(); ++attempt)
        std::this_thread::sleep_for(std::chrono::milliseconds(1));
    assert(farmWaiting->load());
    // 2119 was observed, but 2104 has not arrived. The positive gate must
    // keep the first ReqMktData closed.
    assert(broker->marketDataRequests.load() == 0);
    for (int attempt = 0; attempt < 2500 && !farmRestored->load(); ++attempt)
        std::this_thread::sleep_for(std::chrono::milliseconds(1));
    assert(farmRestored->load());
    for (int attempt = 0; attempt < 2500 && !startDone.load(); ++attempt)
        std::this_thread::sleep_for(std::chrono::milliseconds(1));
    starter.join();
    assert(started);
    assert(startReason.empty());
    assert(broker->marketDataRequests.load() == 1);
    assert(broker->marketDataRequestId.load() == 1000001);
    assert(!broker->marketDataRequestBefore2104.load());
    // A farm warning from the prior transport epoch must be discarded before
    // runtime admission logic sees it.
    IBEvent staleWarning = Event(IBEventType::Error);
    staleWarning.key = "2119";
    staleWarning.value = "CASHFARM";
    staleWarning.connectionEpoch = 1;
    {
        std::lock_guard<std::mutex> lock(broker->injectedMutex);
        broker->injectedEvents.push_back(staleWarning);
    }
    WaitForQuoteBarrier(broker);
    std::string staleReason;
    assert(!runtime.HasFatalRuntimeError(&staleReason));
    assert(broker->sends == 0);
    runtime.Stop();

    ::unlink(socketPath.c_str());
    ::unlink(eventPath.c_str());
    ::unlink((state + "/oms-journal.jsonl").c_str());
    ::unlink((state + "/ib-paper-runtime.lock").c_str());
    ::unlink((state + "/ib-observability.jsonl").c_str());
    ::unlink(FxCashRestartCheckpointPath(state).c_str());
    ::unlink((credentials + "/hepta-execution-fence").c_str());
    ::unlink((credentials + "/hepta-ib-paper-authorization").c_str());
    assert(::rmdir(credentials.c_str()) == 0);
    assert(::rmdir(state.c_str()) == 0);
}

// A same-epoch 2104 is only a positive witness.  The first formal quote
// request must wait through the bounded CASH-farm stability window; a 2119
// observed during that window keeps admission closed and sends zero requests.
// The fake emits callbacks from its PollOnce clock (not a test-thread race),
// with 2119 deliberately inside the named stability interval.
void TestStartupCashFarmReverseOrderBlocksQuote()
{
    const std::string state = TempDirectory(
        "/tmp/hepta-ib-startup-cash-farm-reverse-state-XXXXXX");
    const std::string credentials = TempDirectory(
        "/tmp/hepta-ib-startup-cash-farm-reverse-cred-XXXXXX");
    WriteFile(credentials + "/hepta-execution-fence",
        "HFC1\nfencing_token=77\ngeneration=9\n");
    IbPaperExecutionRuntimeConfig config =
        Config(-1, -1, state, credentials);
    config.readinessTimeoutMs = 1000;
    config.reconnectTimeoutMs = 1500;
    std::string authorization;
    std::string reason;
    assert(config.profile.BuildAuthorizationCredential(
        authorization, reason));
    WriteFile(credentials + "/hepta-ib-paper-authorization",
        authorization + "\n");
    const std::string socketPath = SocketPath(
        "hepta-ib-startup-cash-farm-reverse");
    const std::string eventPath = SocketPath(
        "hepta-ib-startup-cash-farm-reverse-events");
    const std::shared_ptr<FakeBrokerState> broker(new FakeBrokerState());
    const int stabilityMs =
        ib_paper_execution_runtime_internal::
            kMarketDataAdmissionStabilityWindowMs;
    // 2104 is emitted first; 2119 follows 100ms later, strictly inside the
    // 250ms quiet lease.  This is deterministic relative to FakeIbWrapper's
    // elapsed PollOnce clock and does not depend on a callback race.
    broker->cashFarmReadyDelayMs.store(100);
    broker->cashFarmWarningDelayMs.store(100 + stabilityMs / 2);
    const std::shared_ptr<std::atomic<bool> > farmWaiting(
        new std::atomic<bool>(false));
    const std::shared_ptr<std::atomic<bool> > farmRestored(
        new std::atomic<bool>(false));
    IbPaperExecutionRuntimeTestHooks hooks;
    hooks.onStage = [farmWaiting, farmRestored](const char* stage) {
        if (std::strcmp(stage,
                "broker_startup_market_data_farm_waiting") == 0)
            farmWaiting->store(true);
        else if (std::strcmp(stage,
                     "broker_startup_market_data_farm_restored") == 0)
            farmRestored->store(true);
    };
    config.listenFd = ActivatedSocket(socketPath);
    config.eventListenFd = ActivatedSocket(eventPath);
    IbPaperExecutionRuntimeComposition runtime(
        config, std::unique_ptr<IIBApiWrapper>(new FakeIbWrapper(broker)),
        hooks, std::shared_ptr<MutableKillSwitch>(new MutableKillSwitch()));
    assert(!runtime.Start(reason));
    assert(reason == "IB_PAPER_STARTUP_MARKET_DATA_FARM_TIMEOUT");
    assert(farmRestored->load());
    assert(farmWaiting->load());
    assert(broker->marketDataRequests.load() == 0);
    assert(!broker->marketDataRequestBefore2104.load());
    assert(broker->marketDataCancels.load() == 0);
    runtime.Stop();
    CleanupExternalRuntimeFixture(
        state, credentials, socketPath, eventPath);
}

// The reverse order must also be safe when both callbacks are delivered in
// one adapter batch.  Injecting at the poll-to-drain boundary makes the event
// ordering deterministic: the queued 2104 is followed by the same-epoch 2119
// before the runtime evaluates the gate.
void TestStartupCashFarmSameBatchReverseOrderBlocksQuote()
{
    const std::string state = TempDirectory(
        "/tmp/hepta-ib-startup-cash-farm-same-batch-state-XXXXXX");
    const std::string credentials = TempDirectory(
        "/tmp/hepta-ib-startup-cash-farm-same-batch-cred-XXXXXX");
    WriteFile(credentials + "/hepta-execution-fence",
        "HFC1\nfencing_token=77\ngeneration=9\n");
    IbPaperExecutionRuntimeConfig config =
        Config(-1, -1, state, credentials);
    config.readinessTimeoutMs = 1000;
    config.reconnectTimeoutMs = 1500;
    std::string authorization;
    std::string reason;
    assert(config.profile.BuildAuthorizationCredential(
        authorization, reason));
    WriteFile(credentials + "/hepta-ib-paper-authorization",
        authorization + "\n");
    const std::string socketPath = SocketPath(
        "hepta-ib-startup-cash-farm-same-batch");
    const std::string eventPath = SocketPath(
        "hepta-ib-startup-cash-farm-same-batch-events");
    const std::shared_ptr<FakeBrokerState> broker(new FakeBrokerState());
    const std::shared_ptr<std::atomic<bool> > injected(
        new std::atomic<bool>(false));
    const std::shared_ptr<std::atomic<bool> > farmWaiting(
        new std::atomic<bool>(false));
    const std::shared_ptr<std::atomic<bool> > farmRestored(
        new std::atomic<bool>(false));
    FakeIbWrapper* wrapper = new FakeIbWrapper(broker);
    IbPaperExecutionRuntimeTestHooks hooks;
    hooks.onStage = [wrapper, broker, injected, farmWaiting, farmRestored](
                        const char* stage) {
        if (std::strcmp(stage, "after_adapter_poll_before_drain") == 0 &&
            !injected->exchange(true))
        {
            IBEvent warning = Event(IBEventType::Error);
            warning.key = "2119";
            warning.value = "cashfarm";
            warning.connectionEpoch = broker->callbackEpoch.load();
            wrapper->InjectEventForTest(std::move(warning));
        }
        else if (std::strcmp(stage,
                     "broker_startup_market_data_farm_waiting") == 0)
            farmWaiting->store(true);
        else if (std::strcmp(stage,
                     "broker_startup_market_data_farm_restored") == 0)
            farmRestored->store(true);
    };
    config.listenFd = ActivatedSocket(socketPath);
    config.eventListenFd = ActivatedSocket(eventPath);
    IbPaperExecutionRuntimeComposition runtime(
        config, std::unique_ptr<IIBApiWrapper>(wrapper),
        hooks, std::shared_ptr<MutableKillSwitch>(new MutableKillSwitch()));
    assert(!runtime.Start(reason));
    assert(reason == "IB_PAPER_STARTUP_MARKET_DATA_FARM_TIMEOUT");
    assert(farmRestored->load());
    assert(farmWaiting->load());
    assert(broker->marketDataRequests.load() == 0);
    assert(!broker->marketDataRequestBefore2104.load());
    assert(broker->marketDataCancels.load() == 0);
    runtime.Stop();
    CleanupExternalRuntimeFixture(
        state, credentials, socketPath, eventPath);
}

void TestStartupMissingCashFarmNeverRequestsMarketData()
{
    const std::string state = TempDirectory(
        "/tmp/hepta-ib-startup-no-cash-farm-state-XXXXXX");
    const std::string credentials = TempDirectory(
        "/tmp/hepta-ib-startup-no-cash-farm-cred-XXXXXX");
    WriteFile(credentials + "/hepta-execution-fence",
        "HFC1\nfencing_token=77\ngeneration=9\n");
    IbPaperExecutionRuntimeConfig config =
        Config(-1, -1, state, credentials);
    config.readinessTimeoutMs = 250;
    config.reconnectTimeoutMs = 1000;
    std::string authorization;
    std::string reason;
    assert(config.profile.BuildAuthorizationCredential(
        authorization, reason));
    WriteFile(credentials + "/hepta-ib-paper-authorization",
        authorization + "\n");
    const std::string socketPath = SocketPath(
        "hepta-ib-startup-no-cash-farm");
    const std::string eventPath = SocketPath(
        "hepta-ib-startup-no-cash-farm-events");
    const std::shared_ptr<FakeBrokerState> broker(new FakeBrokerState());
    broker->cashFarmWarningDelayMs.store(-1);
    broker->cashFarmReadyDelayMs.store(100000);
    config.listenFd = ActivatedSocket(socketPath);
    config.eventListenFd = ActivatedSocket(eventPath);
    IbPaperExecutionRuntimeComposition runtime(
        config, std::unique_ptr<IIBApiWrapper>(new FakeIbWrapper(broker)),
        IbPaperExecutionRuntimeTestHooks(),
        std::shared_ptr<MutableKillSwitch>(new MutableKillSwitch()));

    assert(!runtime.Start(reason));
    assert(reason == "IB_PAPER_STARTUP_MARKET_DATA_FARM_TIMEOUT");
    assert(broker->marketDataRequests.load() == 0);
    assert(broker->marketDataCancels.load() == 0);
    assert(!broker->marketDataRequestBefore2104.load());
    assert(broker->sends == 0);
    runtime.Stop();

    ::unlink(socketPath.c_str());
    ::unlink(eventPath.c_str());
    ::unlink((state + "/oms-journal.jsonl").c_str());
    ::unlink((state + "/ib-paper-runtime.lock").c_str());
    ::unlink((state + "/ib-observability.jsonl").c_str());
    ::unlink(FxCashRestartCheckpointPath(state).c_str());
    ::unlink((credentials + "/hepta-execution-fence").c_str());
    ::unlink((credentials + "/hepta-ib-paper-authorization").c_str());
    assert(::rmdir(credentials.c_str()) == 0);
    assert(::rmdir(state.c_str()) == 0);
}

void TestStartupLateCashFarmWarningFailsClosed()
{
    const std::string state = TempDirectory(
        "/tmp/hepta-ib-startup-late-cash-warning-state-XXXXXX");
    const std::string credentials = TempDirectory(
        "/tmp/hepta-ib-startup-late-cash-warning-cred-XXXXXX");
    WriteFile(credentials + "/hepta-execution-fence",
        "HFC1\nfencing_token=77\ngeneration=9\n");
    IbPaperExecutionRuntimeConfig config =
        Config(-1, -1, state, credentials);
    config.readinessTimeoutMs = 1000;
    config.reconnectTimeoutMs = 1500;
    std::string authorization;
    std::string reason;
    assert(config.profile.BuildAuthorizationCredential(
        authorization, reason));
    WriteFile(credentials + "/hepta-ib-paper-authorization",
        authorization + "\n");
    const std::string socketPath = SocketPath(
        "hepta-ib-startup-late-cash-warning");
    const std::string eventPath = SocketPath(
        "hepta-ib-startup-late-cash-warning-events");
    const std::shared_ptr<FakeBrokerState> broker(new FakeBrokerState());
    const std::shared_ptr<std::atomic<bool> > injected(
        new std::atomic<bool>(false));
    IbPaperExecutionRuntimeTestHooks hooks;
    hooks.onStage = [broker, injected](const char* stage) {
        if (std::strcmp(stage, "broker_startup_recovery_risk_dispatched") != 0 ||
            injected->exchange(true))
            return;
        IBEvent warning = Event(IBEventType::Error);
        warning.key = "2119";
        warning.value = "CASHFARM";
        warning.connectionEpoch = broker->callbackEpoch.load();
        std::lock_guard<std::mutex> lock(broker->injectedMutex);
        broker->injectedEvents.push_back(warning);
    };
    config.listenFd = ActivatedSocket(socketPath);
    config.eventListenFd = ActivatedSocket(eventPath);
    IbPaperExecutionRuntimeComposition runtime(
        config, std::unique_ptr<IIBApiWrapper>(new FakeIbWrapper(broker)),
        hooks, std::shared_ptr<MutableKillSwitch>(new MutableKillSwitch()));

    assert(!runtime.Start(reason));
    assert(reason == "IB_PAPER_MARKET_DATA_FARM_LOST_DURING_REFRESH");
    assert(broker->marketDataRequests.load() == 1);
    assert(broker->marketDataRequestId.load() == 1000001);
    assert(!broker->marketDataRequestBefore2104.load());
    assert(broker->marketDataCancels.load() == 1);
    assert(!runtime.Adapter().IsConnected());
    assert(broker->sends == 0);
    std::string fatalReason;
    assert(runtime.HasFatalRuntimeError(&fatalReason));
    assert(fatalReason == reason);
    runtime.Stop();

    ::unlink(socketPath.c_str());
    ::unlink(eventPath.c_str());
    ::unlink((state + "/oms-journal.jsonl").c_str());
    ::unlink((state + "/ib-paper-runtime.lock").c_str());
    ::unlink((state + "/ib-observability.jsonl").c_str());
    ::unlink(FxCashRestartCheckpointPath(state).c_str());
    ::unlink((credentials + "/hepta-execution-fence").c_str());
    ::unlink((credentials + "/hepta-ib-paper-authorization").c_str());
    assert(::rmdir(credentials.c_str()) == 0);
    assert(::rmdir(state.c_str()) == 0);
}

// A connectivity-restored/data-lost (1101) callback that arrives after the
// formal quote cycle exists must invalidate startup refresh.  Treating it as
// the same benign acknowledgement as 1102 would let the snapshot barrier
// publish Ready against a subscription IB has already discarded.
void TestStartupLateMarketDataLoss1101FailsClosed()
{
    const std::string state = TempDirectory(
        "/tmp/hepta-ib-startup-late-1101-state-XXXXXX");
    const std::string credentials = TempDirectory(
        "/tmp/hepta-ib-startup-late-1101-cred-XXXXXX");
    WriteFile(credentials + "/hepta-execution-fence",
        "HFC1\nfencing_token=77\ngeneration=9\n");
    IbPaperExecutionRuntimeConfig config =
        Config(-1, -1, state, credentials);
    config.readinessTimeoutMs = 1000;
    config.reconnectTimeoutMs = 1500;
    std::string authorization;
    std::string reason;
    assert(config.profile.BuildAuthorizationCredential(
        authorization, reason));
    WriteFile(credentials + "/hepta-ib-paper-authorization",
        authorization + "\n");
    const std::string socketPath =
        SocketPath("hepta-ib-startup-late-1101");
    const std::string eventPath = SocketPath(
        "hepta-ib-startup-late-1101-events");
    const std::shared_ptr<FakeBrokerState> broker(new FakeBrokerState());
    const std::shared_ptr<std::atomic<bool> > injected(
        new std::atomic<bool>(false));
    IbPaperExecutionRuntimeTestHooks hooks;
    hooks.onStage = [broker, injected](const char* stage) {
        if (std::strcmp(stage, "after_adapter_poll_before_drain") != 0 ||
            broker->marketDataRequests.load() != 1 ||
            broker->snapshotRequests.load() == 0 ||
            injected->exchange(true))
            return;
        IBEvent loss = Event(IBEventType::Error, -1);
        loss.key = "1101";
        loss.value = "connectivity restored; market data lost";
        loss.connectionEpoch = broker->callbackEpoch.load();
        std::lock_guard<std::mutex> lock(broker->injectedMutex);
        broker->injectedEvents.push_back(loss);
    };
    config.listenFd = ActivatedSocket(socketPath);
    config.eventListenFd = ActivatedSocket(eventPath);
    IbPaperExecutionRuntimeComposition runtime(
        config, std::unique_ptr<IIBApiWrapper>(new FakeIbWrapper(broker)),
        hooks, std::shared_ptr<MutableKillSwitch>(new MutableKillSwitch()));

    assert(!runtime.Start(reason));
    assert(reason ==
        "IB_PAPER_STARTUP_MARKET_DATA_LOST_DURING_REFRESH");
    assert(injected->load());
    assert(broker->marketDataRequests.load() == 1);
    assert(broker->marketDataCancels.load() == 1);
    assert(!broker->marketDataRequestBefore2104.load());
    assert(!runtime.Adapter().IsConnected());
    assert(broker->sends == 0);
    std::string fatalReason;
    assert(runtime.HasFatalRuntimeError(&fatalReason));
    assert(fatalReason == reason);

    OmsJournal evidence;
    assert(evidence.Init(state + "/oms-journal.jsonl"));
    bool persisted = false;
    assert(evidence.Replay([&](const OmsJournalEvent& event) {
        if (event.eventType == "broker_error" && event.orderId == -1)
            persisted = event.brokerErrorCode == 1101 &&
                event.riskCode == "IB_ERROR_1101";
    }) >= 0);
    assert(persisted);
    runtime.Stop();
    CleanupExternalRuntimeFixture(
        state, credentials, socketPath, eventPath);
}

void TestStartupMarketDataCancelFailureFailsClosed()
{
    const std::string state = TempDirectory(
        "/tmp/hepta-ib-startup-market-data-cancel-state-XXXXXX");
    const std::string credentials = TempDirectory(
        "/tmp/hepta-ib-startup-market-data-cancel-cred-XXXXXX");
    WriteFile(credentials + "/hepta-execution-fence",
        "HFC1\nfencing_token=77\ngeneration=9\n");
    IbPaperExecutionRuntimeConfig config =
        Config(-1, -1, state, credentials);
    // Keep the test bounded by the readiness deadline.  The formal quote is
    // already accepted, but the terminal-correlation end marker is withheld
    // so startup must execute its quote cleanup path.
    config.readinessTimeoutMs = 250;
    config.reconnectTimeoutMs = 1000;
    std::string authorization;
    std::string reason;
    assert(config.profile.BuildAuthorizationCredential(
        authorization, reason));
    WriteFile(credentials + "/hepta-ib-paper-authorization",
        authorization + "\n");
    const std::string socketPath = SocketPath(
        "hepta-ib-startup-market-data-cancel");
    const std::string eventPath = SocketPath(
        "hepta-ib-startup-market-data-cancel-events");
    const std::shared_ptr<FakeBrokerState> broker(new FakeBrokerState());
    broker->suppressCompletedOrdersEnd.store(true);
    broker->marketDataCancelFailureRequestId.store(1000001);
    config.listenFd = ActivatedSocket(socketPath);
    config.eventListenFd = ActivatedSocket(eventPath);
    IbPaperExecutionRuntimeComposition runtime(
        config, std::unique_ptr<IIBApiWrapper>(new FakeIbWrapper(broker)),
        IbPaperExecutionRuntimeTestHooks(),
        std::shared_ptr<MutableKillSwitch>(new MutableKillSwitch()));

    assert(!runtime.Start(reason));
    assert(reason == "IB_PAPER_MARKET_DATA_CANCEL_FAILED");
    assert(broker->marketDataRequests.load() == 1);
    assert(broker->marketDataRequestId.load() == 1000001);
    assert(!broker->marketDataRequestBefore2104.load());
    assert(broker->marketDataCancels.load() == 1);
    assert(!runtime.Adapter().IsConnected());
    assert(broker->sends == 0);
    runtime.Stop();

    ::unlink(socketPath.c_str());
    ::unlink(eventPath.c_str());
    ::unlink((state + "/oms-journal.jsonl").c_str());
    ::unlink((state + "/ib-paper-runtime.lock").c_str());
    ::unlink((state + "/ib-observability.jsonl").c_str());
    ::unlink(FxCashRestartCheckpointPath(state).c_str());
    ::unlink((credentials + "/hepta-execution-fence").c_str());
    ::unlink((credentials + "/hepta-ib-paper-authorization").c_str());
    assert(::rmdir(credentials.c_str()) == 0);
    assert(::rmdir(state.c_str()) == 0);
}

void TestSteadyStateMarketData10197FailsClosedWithExplicitReason()
{
    const std::string state = TempDirectory(
        "/tmp/hepta-ib-steady-market-data-10197-state-XXXXXX");
    const std::string credentials = TempDirectory(
        "/tmp/hepta-ib-steady-market-data-10197-cred-XXXXXX");
    WriteFile(credentials + "/hepta-execution-fence",
        "HFC1\nfencing_token=77\ngeneration=9\n");
    IbPaperExecutionRuntimeConfig config =
        Config(-1, -1, state, credentials);
    std::string authorization;
    std::string reason;
    assert(config.profile.BuildAuthorizationCredential(
        authorization, reason));
    WriteFile(credentials + "/hepta-ib-paper-authorization",
        authorization + "\n");
    const std::string socketPath = SocketPath(
        "hepta-ib-steady-market-data-10197");
    const std::string eventPath = SocketPath(
        "hepta-ib-steady-market-data-10197-events");
    const std::shared_ptr<FakeBrokerState> broker(new FakeBrokerState());
    const std::shared_ptr<MutableKillSwitch> killSwitch(
        new MutableKillSwitch());
    config.listenFd = ActivatedSocket(socketPath);
    config.eventListenFd = ActivatedSocket(eventPath);
    IbPaperExecutionRuntimeComposition runtime(
        config, std::unique_ptr<IIBApiWrapper>(new FakeIbWrapper(broker)),
        IbPaperExecutionRuntimeTestHooks(), killSwitch);
    assert(runtime.Start(reason));
    const int requestId = broker->marketDataRequestId.load();
    assert(requestId > 0);

    // IB may report the session-wide competing-live-session error with the
    // sentinel callback id -1 rather than the ticker id returned by
    // reqMktData().  Detection must rely on the current epoch/active cycle,
    // not this optional correlation.
    IBEvent error = Event(IBEventType::Error, -1);
    error.key = "10197";
    error.value = "simulated competing live session";
    {
        std::lock_guard<std::mutex> lock(broker->injectedMutex);
        broker->injectedEvents.push_back(error);
    }
    std::string fatalReason;
    for (int attempt = 0; attempt < 2000 &&
         !runtime.HasFatalRuntimeError(&fatalReason); ++attempt)
        std::this_thread::sleep_for(std::chrono::milliseconds(1));
    assert(runtime.HasFatalRuntimeError(&fatalReason));
    assert(fatalReason ==
        "IB_PAPER_REALTIME_MARKET_DATA_UNAVAILABLE_10197");
    assert(runtime.IsMutationBlocked(&fatalReason));
    assert(fatalReason ==
        "IB_PAPER_REALTIME_MARKET_DATA_UNAVAILABLE_10197");
    assert(broker->sends == 0);
    runtime.Stop();
    assert(broker->marketDataCancels.load() == 1);

    bool persisted = false;
    OmsJournal evidence;
    assert(evidence.Init(state + "/oms-journal.jsonl"));
    assert(evidence.Replay([&](const OmsJournalEvent& event) {
        if (event.eventType == "broker_error" && event.orderId == -1)
        {
            persisted = event.brokerErrorCode == 10197 &&
                event.riskCode == "IB_ERROR_10197";
        }
    }) >= 0);
    assert(persisted);

    ::unlink(socketPath.c_str());
    ::unlink(eventPath.c_str());
    ::unlink((state + "/oms-journal.jsonl").c_str());
    ::unlink((state + "/ib-paper-runtime.lock").c_str());
    ::unlink((state + "/ib-observability.jsonl").c_str());
    ::unlink(FxCashRestartCheckpointPath(state).c_str());
    ::unlink((credentials + "/hepta-execution-fence").c_str());
    ::unlink((credentials + "/hepta-ib-paper-authorization").c_str());
    assert(::rmdir(credentials.c_str()) == 0);
    assert(::rmdir(state.c_str()) == 0);
}

void TestStartupMissingCompletedOrdersEndFailsClosed()
{
    const std::string state = TempDirectory(
        "/tmp/hepta-ib-startup-terminal-boundary-state-XXXXXX");
    const std::string credentials = TempDirectory(
        "/tmp/hepta-ib-startup-terminal-boundary-cred-XXXXXX");
    WriteFile(credentials + "/hepta-execution-fence",
        "HFC1\nfencing_token=77\ngeneration=9\n");
    IbPaperExecutionRuntimeConfig config =
        Config(-1, -1, state, credentials);
    config.readinessTimeoutMs = 1000;
    config.reconnectTimeoutMs = 1000;
    std::string authorization;
    std::string reason;
    assert(config.profile.BuildAuthorizationCredential(
        authorization, reason));
    WriteFile(credentials + "/hepta-ib-paper-authorization",
        authorization + "\n");
    const std::string socketPath = SocketPath(
        "hepta-ib-startup-terminal-boundary");
    const std::string eventPath = SocketPath(
        "hepta-ib-startup-terminal-boundary-events");
    const std::shared_ptr<FakeBrokerState> broker(new FakeBrokerState());
    broker->suppressCompletedOrdersEnd.store(true);
    const std::shared_ptr<MutableKillSwitch> killSwitch(
        new MutableKillSwitch());
    config.listenFd = ActivatedSocket(socketPath);
    config.eventListenFd = ActivatedSocket(eventPath);
    IbPaperExecutionRuntimeComposition runtime(
        config, std::unique_ptr<IIBApiWrapper>(new FakeIbWrapper(broker)),
        IbPaperExecutionRuntimeTestHooks(), killSwitch);
    assert(!runtime.Start(reason));
    assert(reason == "IB_PAPER_AUTHORITATIVE_SNAPSHOTS_TIMEOUT");
    assert(broker->sends == 0);
    runtime.Stop();

    ::unlink(socketPath.c_str());
    ::unlink(eventPath.c_str());
    ::unlink((state + "/oms-journal.jsonl").c_str());
    ::unlink((state + "/ib-paper-runtime.lock").c_str());
    ::unlink((state + "/ib-observability.jsonl").c_str());
    ::unlink(FxCashRestartCheckpointPath(state).c_str());
    ::unlink((credentials + "/hepta-execution-fence").c_str());
    ::unlink((credentials +
              "/hepta-ib-paper-authorization").c_str());
    assert(::rmdir(credentials.c_str()) == 0);
    assert(::rmdir(state.c_str()) == 0);
}

void TestStartupDisconnectBoundaryLateEconomicFill()
{
    const char* const cases[] = {"closed", "timeout"};
    for (std::size_t index = 0; index < 2; ++index)
    {
        const std::string state = TempDirectory(
            "/tmp/hepta-ib-startup-late-fill-state-XXXXXX");
        const std::string credentials = TempDirectory(
            "/tmp/hepta-ib-startup-late-fill-cred-XXXXXX");
        WriteFile(credentials + "/hepta-execution-fence",
            "HFC1\nfencing_token=77\ngeneration=9\n");
        IbPaperExecutionRuntimeConfig config =
            Config(-1, -1, state, credentials);
        config.reconnectTimeoutMs = 1000;
        config.readinessTimeoutMs = 250;
        std::string authorization;
        std::string reason;
        assert(config.profile.BuildAuthorizationCredential(
            authorization, reason));
        WriteFile(credentials + "/hepta-ib-paper-authorization",
            authorization + "\n");
        const std::string stem = std::string("hepta-ib-startup-late-") +
            cases[index];
        const std::string socketPath = SocketPath(stem.c_str());
        const std::string eventPath = SocketPath(
            (stem + "-events").c_str());
        const std::shared_ptr<FakeBrokerState> broker(new FakeBrokerState());
        const long lateOrderId = 991000 + static_cast<long>(index);
        broker->disconnectFillOrderId.store(lateOrderId);
        if (index == 0)
            broker->emitConnectionClosed.store(true);
        else
            broker->cashFarmReadyDelayMs.store(10000);
        const std::shared_ptr<MutableKillSwitch> killSwitch(
            new MutableKillSwitch());
        config.listenFd = ActivatedSocket(socketPath);
        config.eventListenFd = ActivatedSocket(eventPath);
        IbPaperExecutionRuntimeComposition runtime(
            config, std::unique_ptr<IIBApiWrapper>(new FakeIbWrapper(broker)),
            IbPaperExecutionRuntimeTestHooks(), killSwitch);
        assert(!runtime.Start(reason));
        assert(reason ==
            "IB_PAPER_BROKER_DISCONNECTED_DURING_POST_FILL_RECONCILIATION");
        assert(broker->economicFillDequeued.load());
        assert(!runtime.Adapter().IsConnected());
        assert(runtime.Adapter().HasPendingLivePostFillRiskReconciliation());
        OmsJournal evidence;
        assert(evidence.Init(state + "/oms-journal.jsonl"));
        bool persisted = false;
        assert(evidence.Replay([&](const OmsJournalEvent& event) {
            if (event.eventType == "broker_order_status" &&
                event.orderId == lateOrderId && event.status == "Filled")
                persisted = event.source == "ib-api-callback";
        }) >= 0);
        assert(persisted);
        runtime.Stop();
        ::unlink(socketPath.c_str());
        ::unlink(eventPath.c_str());
        ::unlink((state + "/oms-journal.jsonl").c_str());
        ::unlink((state + "/ib-paper-runtime.lock").c_str());
        ::unlink((state + "/ib-observability.jsonl").c_str());
        ::unlink(FxCashRestartCheckpointPath(state).c_str());
        ::unlink((credentials + "/hepta-execution-fence").c_str());
        ::unlink((credentials + "/hepta-ib-paper-authorization").c_str());
        assert(::rmdir(credentials.c_str()) == 0);
        assert(::rmdir(state.c_str()) == 0);
    }
}

void TestStopDisconnectBoundaryLateEconomicFill()
{
    const std::string state = TempDirectory(
        "/tmp/hepta-ib-stop-late-fill-state-XXXXXX");
    const std::string credentials = TempDirectory(
        "/tmp/hepta-ib-stop-late-fill-cred-XXXXXX");
    WriteFile(credentials + "/hepta-execution-fence",
        "HFC1\nfencing_token=77\ngeneration=9\n");
    IbPaperExecutionRuntimeConfig config =
        Config(-1, -1, state, credentials);
    std::string authorization;
    std::string reason;
    assert(config.profile.BuildAuthorizationCredential(
        authorization, reason));
    WriteFile(credentials + "/hepta-ib-paper-authorization",
        authorization + "\n");
    const std::string socketPath = SocketPath("hepta-ib-stop-late-fill");
    const std::string eventPath = SocketPath(
        "hepta-ib-stop-late-fill-events");
    const std::shared_ptr<FakeBrokerState> broker(new FakeBrokerState());
    const long lateOrderId = 992000;
    broker->disconnectFillOrderId.store(lateOrderId);
    const std::shared_ptr<MutableKillSwitch> killSwitch(
        new MutableKillSwitch());
    config.listenFd = ActivatedSocket(socketPath);
    config.eventListenFd = ActivatedSocket(eventPath);
    IbPaperExecutionRuntimeComposition runtime(
        config, std::unique_ptr<IIBApiWrapper>(new FakeIbWrapper(broker)),
        IbPaperExecutionRuntimeTestHooks(), killSwitch);
    assert(runtime.Start(reason));
    runtime.Stop();
    assert(broker->economicFillDequeued.load());
    assert(runtime.Adapter().HasPendingLivePostFillRiskReconciliation());
    OmsJournal evidence;
    assert(evidence.Init(state + "/oms-journal.jsonl"));
    bool persisted = false;
    assert(evidence.Replay([&](const OmsJournalEvent& event) {
        if (event.eventType == "broker_order_status" &&
            event.orderId == lateOrderId && event.status == "Filled")
            persisted = event.source == "ib-api-callback";
    }) >= 0);
    assert(persisted);
    ::unlink(socketPath.c_str());
    ::unlink(eventPath.c_str());
    ::unlink((state + "/oms-journal.jsonl").c_str());
    ::unlink((state + "/ib-paper-runtime.lock").c_str());
    ::unlink((state + "/ib-observability.jsonl").c_str());
    ::unlink(FxCashRestartCheckpointPath(state).c_str());
    ::unlink((credentials + "/hepta-execution-fence").c_str());
    ::unlink((credentials + "/hepta-ib-paper-authorization").c_str());
    assert(::rmdir(credentials.c_str()) == 0);
    assert(::rmdir(state.c_str()) == 0);
}

void TestFailReconnectDisconnectBoundaryLateEconomicFill()
{
    const std::string state = TempDirectory(
        "/tmp/hepta-ib-fail-reconnect-late-state-XXXXXX");
    const std::string credentials = TempDirectory(
        "/tmp/hepta-ib-fail-reconnect-late-cred-XXXXXX");
    WriteFile(credentials + "/hepta-execution-fence",
        "HFC1\nfencing_token=77\ngeneration=9\n");
    IbPaperExecutionRuntimeConfig config =
        Config(-1, -1, state, credentials);
    config.reconnectTimeoutMs = 2000;
    std::string authorization;
    std::string reason;
    assert(config.profile.BuildAuthorizationCredential(
        authorization, reason));
    WriteFile(credentials + "/hepta-ib-paper-authorization",
        authorization + "\n");
    const std::string socketPath =
        SocketPath("hepta-ib-fail-reconnect-late");
    const std::string eventPath = SocketPath(
        "hepta-ib-fail-reconnect-late-events");
    const std::shared_ptr<FakeBrokerState> broker(new FakeBrokerState());
    const long lateOrderId = 993000;
    const std::shared_ptr<std::atomic<bool> > refreshDispatched(
        new std::atomic<bool>(false));
    const std::shared_ptr<std::atomic<bool> > reconnectComplete(
        new std::atomic<bool>(false));
    IbPaperExecutionRuntimeTestHooks hooks;
    hooks.reconnectApiFactory = [broker]() {
        return std::unique_ptr<IIBApiWrapper>(new FakeIbWrapper(broker));
    };
    hooks.onStage = [broker, refreshDispatched, reconnectComplete,
                     lateOrderId](
                        const char* stage) {
        if (std::strcmp(stage, "before_broker_reconnect_attempt") == 0) {
            broker->marketDataErrorCodeOnRequest.store(10197);
        }
        else if (std::strcmp(stage,
                     "before_quote_market_data_dispatch") == 0 &&
                 broker->reconnectAttempts.load() >= 2) {
            // The reconnect wrapper is now installed.  Arm the fill at the
            // final quote-send boundary so the subsequent failed reconnect
            // cleanup must drain it from this same wrapper/epoch.
            broker->disconnectFillOrderId.store(lateOrderId);
        }
        else if (std::strcmp(stage,
                     "broker_reconnect_refresh_dispatched") == 0) {
            refreshDispatched->store(true);
        }
        else if (std::strcmp(stage, "broker_reconnect_complete") == 0)
            reconnectComplete->store(true);
    };
    config.listenFd = ActivatedSocket(socketPath);
    config.eventListenFd = ActivatedSocket(eventPath);
    IbPaperExecutionRuntimeComposition runtime(
        config, std::unique_ptr<IIBApiWrapper>(new FakeIbWrapper(broker)),
        hooks, std::shared_ptr<MutableKillSwitch>(new MutableKillSwitch()));
    assert(runtime.Start(reason));
    const int connectsBefore = broker->reconnectAttempts.load();
    const int marketDataBefore = broker->marketDataRequests.load();
    broker->emitControlErrorCode.store(509);
    for (int attempt = 0; attempt < 5000 &&
         !runtime.HasFatalRuntimeError(&reason); ++attempt)
        std::this_thread::sleep_for(std::chrono::milliseconds(1));
    assert(!refreshDispatched->load());
    assert(runtime.HasFatalRuntimeError(&reason));
    assert(reason == "IB_PAPER_REALTIME_MARKET_DATA_UNAVAILABLE_10197");
    assert(!reconnectComplete->load());
    assert(broker->reconnectAttempts.load() == connectsBefore + 1);
    assert(broker->marketDataRequests.load() == marketDataBefore + 1);
    // The fatal flag is published while DrainAdapterEvents is still unwinding
    // the reconnect worker.  Wait for that worker to finish the disconnect
    // boundary before asserting the callback was drained; otherwise a slower
    // sanitizer build can observe the flag before FailBrokerReconnect runs.
    for (int attempt = 0; attempt < 5000 &&
         !broker->economicFillDequeued.load(); ++attempt)
        std::this_thread::sleep_for(std::chrono::milliseconds(1));
    assert(broker->economicFillDequeued.load());
    assert(runtime.Adapter().HasPendingLivePostFillRiskReconciliation());
    OmsJournal evidence;
    assert(evidence.Init(state + "/oms-journal.jsonl"));
    bool persisted = false;
    assert(evidence.Replay([&](const OmsJournalEvent& event) {
        if (event.eventType == "broker_order_status" &&
            event.orderId == lateOrderId && event.status == "Filled")
            persisted = event.source == "ib-api-callback";
    }) >= 0);
    assert(persisted);
    runtime.Stop();
    ::unlink(socketPath.c_str());
    ::unlink(eventPath.c_str());
    ::unlink((state + "/oms-journal.jsonl").c_str());
    ::unlink((state + "/ib-paper-runtime.lock").c_str());
    ::unlink((state + "/ib-observability.jsonl").c_str());
    ::unlink(FxCashRestartCheckpointPath(state).c_str());
    ::unlink((credentials + "/hepta-execution-fence").c_str());
    ::unlink((credentials + "/hepta-ib-paper-authorization").c_str());
    assert(::rmdir(credentials.c_str()) == 0);
    assert(::rmdir(state.c_str()) == 0);
}

void TestStartupUpstreamUnavailableWaitsForRestore()
{
    const std::string state = TempDirectory(
        "/tmp/hepta-ib-startup-upstream-state-XXXXXX");
    const std::string credentials = TempDirectory(
        "/tmp/hepta-ib-startup-upstream-cred-XXXXXX");
    WriteFile(credentials + "/hepta-execution-fence",
        "HFC1\nfencing_token=77\ngeneration=9\n");
    IbPaperExecutionRuntimeConfig config =
        Config(-1, -1, state, credentials);
    config.readinessTimeoutMs = 1000;
    config.reconnectTimeoutMs = 1500;
    std::string authorization;
    std::string reason;
    assert(config.profile.BuildAuthorizationCredential(
        authorization, reason));
    WriteFile(credentials + "/hepta-ib-paper-authorization",
        authorization + "\n");
    const std::string socketPath = SocketPath(
        "hepta-ib-startup-upstream");
    const std::string eventPath = SocketPath(
        "hepta-ib-startup-upstream-events");
    const std::shared_ptr<FakeBrokerState> broker(new FakeBrokerState());
    broker->startupControlErrorOnConnect.store(1100);
    const std::shared_ptr<MutableKillSwitch> killSwitch(
        new MutableKillSwitch());
    const std::shared_ptr<std::atomic<bool> > upstreamUnavailable(
        new std::atomic<bool>(false));
    const std::shared_ptr<std::atomic<bool> > upstreamRestored(
        new std::atomic<bool>(false));
    IbPaperExecutionRuntimeTestHooks hooks;
    hooks.onStage = [upstreamUnavailable, upstreamRestored](
                        const char* stage) {
        if (std::strcmp(stage,
                "broker_startup_upstream_unavailable") == 0)
            upstreamUnavailable->store(true);
        else if (std::strcmp(stage,
                     "broker_startup_upstream_restored") == 0)
            upstreamRestored->store(true);
    };
    config.listenFd = ActivatedSocket(socketPath);
    config.eventListenFd = ActivatedSocket(eventPath);
    IbPaperExecutionRuntimeComposition runtime(
        config, std::unique_ptr<IIBApiWrapper>(new FakeIbWrapper(broker)),
        hooks, killSwitch);
    std::thread restore([broker, upstreamUnavailable]() {
        for (int attempt = 0; attempt < 1000 &&
             !upstreamUnavailable->load(); ++attempt)
            std::this_thread::sleep_for(std::chrono::milliseconds(1));
        assert(upstreamUnavailable->load());
        // Exercise both upstream-loss codes and repeat 1100: if connection
        // control callbacks leaked into the default fuse, the two 1100s would
        // exceed its score threshold and leave the adapter tripped.
        IBEvent secondLoss = Event(IBEventType::Error);
        secondLoss.key = "2110";
        IBEvent repeatedLoss = Event(IBEventType::Error);
        repeatedLoss.key = "1100";
        {
            std::lock_guard<std::mutex> lock(broker->injectedMutex);
            broker->injectedEvents.push_back(secondLoss);
            broker->injectedEvents.push_back(repeatedLoss);
        }
        std::this_thread::sleep_for(std::chrono::milliseconds(250));
        broker->emitControlErrorCode.store(1102);
    });
    assert(runtime.Start(reason));
    restore.join();
    assert(upstreamUnavailable->load());
    assert(upstreamRestored->load());
    assert(broker->snapshotRequests.load() > 0);
    assert(broker->refreshRequestsWhileUpstreamUnavailable.load() == 0);
    assert(!runtime.Adapter().IsCircuitBreakerTripped());
    assert(broker->sends == 0);
    runtime.Stop();

    ::unlink(socketPath.c_str());
    ::unlink(eventPath.c_str());
    ::unlink((state + "/oms-journal.jsonl").c_str());
    ::unlink((state + "/ib-paper-runtime.lock").c_str());
    ::unlink((state + "/ib-observability.jsonl").c_str());
    ::unlink(FxCashRestartCheckpointPath(state).c_str());
    ::unlink((credentials + "/hepta-execution-fence").c_str());
    ::unlink((credentials +
              "/hepta-ib-paper-authorization").c_str());
    assert(::rmdir(credentials.c_str()) == 0);
    assert(::rmdir(state.c_str()) == 0);
}

void TestBrokerReconnectAllowsDelayedUpstreamRestore()
{
    const std::string state = TempDirectory(
        "/tmp/hepta-ib-delayed-upstream-state-XXXXXX");
    const std::string credentials = TempDirectory(
        "/tmp/hepta-ib-delayed-upstream-cred-XXXXXX");
    WriteFile(credentials + "/hepta-execution-fence",
        "HFC1\nfencing_token=77\ngeneration=9\n");
    IbPaperExecutionRuntimeConfig config =
        Config(-1, -1, state, credentials);
    config.readinessTimeoutMs = 1000;
    // The prior derived timeout was max(1000, readiness * 3). Waiting 1200ms
    // proves the independent reconnect budget is actually honored.
    config.reconnectTimeoutMs = 2000;
    std::string authorization;
    std::string reason;
    assert(config.profile.BuildAuthorizationCredential(
        authorization, reason));
    WriteFile(credentials + "/hepta-ib-paper-authorization",
        authorization + "\n");
    const std::string socketPath = SocketPath(
        "hepta-ib-delayed-upstream");
    const std::string eventPath = SocketPath(
        "hepta-ib-delayed-upstream-events");
    const std::shared_ptr<FakeBrokerState> broker(new FakeBrokerState());
    const std::shared_ptr<MutableKillSwitch> killSwitch(
        new MutableKillSwitch());
    const std::shared_ptr<std::atomic<bool> > upstreamUnavailable(
        new std::atomic<bool>(false));
    const std::shared_ptr<std::atomic<bool> > reconnectComplete(
        new std::atomic<bool>(false));
    IbPaperExecutionRuntimeTestHooks hooks;
    hooks.reconnectApiFactory = [broker]() {
        return std::unique_ptr<IIBApiWrapper>(new FakeIbWrapper(broker));
    };
    hooks.onStage = [upstreamUnavailable, reconnectComplete](
                        const char* stage) {
        if (std::strcmp(stage,
                "broker_reconnect_upstream_unavailable") == 0)
            upstreamUnavailable->store(true);
        else if (std::strcmp(stage, "broker_reconnect_complete") == 0)
            reconnectComplete->store(true);
    };
    config.listenFd = ActivatedSocket(socketPath);
    config.eventListenFd = ActivatedSocket(eventPath);
    IbPaperExecutionRuntimeComposition runtime(
        config, std::unique_ptr<IIBApiWrapper>(new FakeIbWrapper(broker)),
        hooks, killSwitch);
    assert(runtime.Start(reason));
    const int snapshotsBefore = broker->snapshotRequests.load();
    broker->reconnectControlErrorOnConnect.store(2110);
    broker->emitControlErrorCode.store(509);
    for (int attempt = 0; attempt < 2000 &&
         !upstreamUnavailable->load(); ++attempt)
        std::this_thread::sleep_for(std::chrono::milliseconds(1));
    assert(upstreamUnavailable->load());
    assert(runtime.IsMutationBlocked(&reason));
    assert(reason == "IB_PAPER_BROKER_RECONNECT_PENDING");
    assert(broker->snapshotRequests.load() == snapshotsBefore);
    std::this_thread::sleep_for(std::chrono::milliseconds(1200));
    assert(!runtime.HasFatalRuntimeError(&reason));
    assert(runtime.IsMutationBlocked(&reason));
    broker->emitControlErrorCode.store(1102);
    for (int attempt = 0; attempt < 3000 &&
         !reconnectComplete->load(); ++attempt)
        std::this_thread::sleep_for(std::chrono::milliseconds(1));
    assert(reconnectComplete->load());
    assert(!runtime.HasFatalRuntimeError(&reason));
    assert(broker->snapshotRequests.load() > snapshotsBefore);
    assert(broker->refreshRequestsWhileUpstreamUnavailable.load() == 0);
    assert(!runtime.Adapter().IsCircuitBreakerTripped());
    assert(broker->sends == 0);
    runtime.Stop();

    ::unlink(socketPath.c_str());
    ::unlink(eventPath.c_str());
    ::unlink((state + "/oms-journal.jsonl").c_str());
    ::unlink((state + "/ib-paper-runtime.lock").c_str());
    ::unlink((state + "/ib-observability.jsonl").c_str());
    ::unlink(FxCashRestartCheckpointPath(state).c_str());
    ::unlink((credentials + "/hepta-execution-fence").c_str());
    ::unlink((credentials +
              "/hepta-ib-paper-authorization").c_str());
    assert(::rmdir(credentials.c_str()) == 0);
    assert(::rmdir(state.c_str()) == 0);
}

// Once reconnect has dispatched a fresh quote/snapshot cycle, 1101 means the
// broker discarded that market-data cycle.  It must fail the reconnect rather
// than letting the old quote state cross the Ready transition.
void TestReconnectLateMarketDataLoss1101FailsClosed()
{
    const std::string state = TempDirectory(
        "/tmp/hepta-ib-reconnect-late-1101-state-XXXXXX");
    const std::string credentials = TempDirectory(
        "/tmp/hepta-ib-reconnect-late-1101-cred-XXXXXX");
    WriteFile(credentials + "/hepta-execution-fence",
        "HFC1\nfencing_token=77\ngeneration=9\n");
    IbPaperExecutionRuntimeConfig config =
        Config(-1, -1, state, credentials);
    config.readinessTimeoutMs = 1000;
    config.reconnectTimeoutMs = 1500;
    std::string authorization;
    std::string reason;
    assert(config.profile.BuildAuthorizationCredential(
        authorization, reason));
    WriteFile(credentials + "/hepta-ib-paper-authorization",
        authorization + "\n");
    const std::string socketPath =
        SocketPath("hepta-ib-reconnect-late-1101");
    const std::string eventPath = SocketPath(
        "hepta-ib-reconnect-late-1101-events");
    const std::shared_ptr<FakeBrokerState> broker(new FakeBrokerState());
    const std::shared_ptr<std::atomic<bool> > injected(
        new std::atomic<bool>(false));
    const std::shared_ptr<std::atomic<bool> > reconnectComplete(
        new std::atomic<bool>(false));
    IbPaperExecutionRuntimeTestHooks hooks;
    hooks.reconnectApiFactory = [broker]() {
        return std::unique_ptr<IIBApiWrapper>(new FakeIbWrapper(broker));
    };
    hooks.onStage = [broker, injected, reconnectComplete](const char* stage) {
        if (std::strcmp(stage, "broker_reconnect_refresh_dispatched") == 0 &&
            !injected->exchange(true))
        {
            IBEvent loss = Event(IBEventType::Error, -1);
            loss.key = "1101";
            loss.value = "connectivity restored; market data lost";
            loss.connectionEpoch = broker->callbackEpoch.load();
            std::lock_guard<std::mutex> lock(broker->injectedMutex);
            broker->injectedEvents.push_back(loss);
        }
        else if (std::strcmp(stage, "broker_reconnect_complete") == 0)
            reconnectComplete->store(true);
    };
    config.listenFd = ActivatedSocket(socketPath);
    config.eventListenFd = ActivatedSocket(eventPath);
    IbPaperExecutionRuntimeComposition runtime(
        config, std::unique_ptr<IIBApiWrapper>(new FakeIbWrapper(broker)),
        hooks, std::shared_ptr<MutableKillSwitch>(new MutableKillSwitch()));
    assert(runtime.Start(reason));
    const int requestsBefore = broker->marketDataRequests.load();
    assert(requestsBefore == 1);
    broker->emitControlErrorCode.store(509);
    std::string fatalReason;
    for (int attempt = 0; attempt < 5000 &&
         !runtime.HasFatalRuntimeError(&fatalReason); ++attempt)
        std::this_thread::sleep_for(std::chrono::milliseconds(1));
    assert(injected->load());
    assert(runtime.HasFatalRuntimeError(&fatalReason));
    assert(fatalReason ==
        "IB_PAPER_BROKER_RECONNECT_MARKET_DATA_LOST_DURING_REFRESH");
    assert(!reconnectComplete->load());
    assert(broker->reconnectAttempts.load() == 2);
    assert(broker->marketDataRequests.load() == requestsBefore + 1);
    assert(broker->marketDataCancels.load() >= 2);
    assert(broker->sends == 0);
    runtime.Stop();

    OmsJournal evidence;
    assert(evidence.Init(state + "/oms-journal.jsonl"));
    bool persisted = false;
    assert(evidence.Replay([&](const OmsJournalEvent& event) {
        if (event.eventType == "broker_error" && event.orderId == -1)
            persisted = event.brokerErrorCode == 1101 &&
                event.riskCode == "IB_ERROR_1101";
    }) >= 0);
    assert(persisted);
    CleanupExternalRuntimeFixture(
        state, credentials, socketPath, eventPath);
}

void TestRecoveryAuditRequiresFreshEpochAndDefersLateTerminalFill()
{
    const std::string state = TempDirectory(
        "/tmp/hepta-ib-recovery-audit-state-XXXXXX");
    const std::string credentials = TempDirectory(
        "/tmp/hepta-ib-recovery-audit-cred-XXXXXX");
    WriteFile(credentials + "/hepta-execution-fence",
        "HFC1\nfencing_token=77\ngeneration=9\n");
    IbPaperExecutionRuntimeConfig config =
        Config(-1, -1, state, credentials);
    config.reconnectTimeoutMs = 3000;
    config.fxCashBaselines["EUR.USD"].observedCashBalance = 100.0;
    config.fxCashBaselines["EUR.USD"].campaignExecutionDelta = 100.0;
    std::string authorization;
    std::string reason;
    assert(config.profile.BuildAuthorizationCredential(
        authorization, reason));
    WriteFile(credentials + "/hepta-ib-paper-authorization",
        authorization + "\n");

    const std::string socketPath = SocketPath(
        "hepta-ib-recovery-audit");
    const std::string eventPath = SocketPath(
        "hepta-ib-recovery-audit-events");
    config.listenFd = ActivatedSocket(socketPath);
    config.eventListenFd = ActivatedSocket(eventPath);
    const std::shared_ptr<FakeBrokerState> broker(new FakeBrokerState());
    broker->positionQuantity = 100.0;
    const std::shared_ptr<MutableKillSwitch> killSwitch(
        new MutableKillSwitch());
    const std::shared_ptr<std::atomic<long> > lateFillOrderId(
        new std::atomic<long>(-1));
    const std::shared_ptr<std::atomic<bool> > lateFillInjected(
        new std::atomic<bool>(false));
    const std::shared_ptr<std::atomic<bool> > postFillDeferred(
        new std::atomic<bool>(false));
    const std::shared_ptr<std::atomic<bool> > releasePostFillDeferred(
        new std::atomic<bool>(false));
    const std::shared_ptr<std::atomic<bool> > reconnectComplete(
        new std::atomic<bool>(false));
    const std::shared_ptr<std::atomic<bool> > riskAfterBoundaries(
        new std::atomic<bool>(false));
    const std::shared_ptr<std::atomic<int> > stageSequence(
        new std::atomic<int>(0));
    const std::shared_ptr<std::atomic<int> > refreshStage(
        new std::atomic<int>(0));
    const std::shared_ptr<std::atomic<int> > riskStage(
        new std::atomic<int>(0));
    const std::shared_ptr<std::atomic<int> > completeStage(
        new std::atomic<int>(0));
    std::atomic<IbPaperExecutionRuntimeComposition*> runtimePointer(
        nullptr);

    IbPaperExecutionRuntimeTestHooks hooks;
    hooks.reconnectApiFactory = [broker]() {
        return std::unique_ptr<IIBApiWrapper>(new FakeIbWrapper(broker));
    };
    hooks.onStage = [broker, lateFillOrderId, lateFillInjected,
                     postFillDeferred, releasePostFillDeferred,
                     reconnectComplete, riskAfterBoundaries, stageSequence,
                     refreshStage, riskStage, completeStage,
                     &runtimePointer](const char* stage) {
        if (std::strcmp(stage,
                "before_recovery_audit_reconnect_drain") == 0 &&
            !lateFillInjected->exchange(true))
        {
            const long orderId = lateFillOrderId->load();
            assert(orderId >= 0);
            broker->positionQuantity = 0.0;
            IBEvent filled = Event(IBEventType::OrderStatus, orderId);
            filled.key = "Filled";
            filled.number = 1.10010;
            filled.number2 = 100.0;
            filled.number3 = 0.0;
            std::lock_guard<std::mutex> lock(broker->injectedMutex);
            broker->injectedEvents.push_back(filled);
        }
        else if (std::strcmp(stage,
                     "recovery_audit_reconnect_deferred_post_fill") == 0)
        {
            postFillDeferred->store(true);
            while (!releasePostFillDeferred->load())
                std::this_thread::sleep_for(
                    std::chrono::milliseconds(1));
        }
        else if (std::strcmp(stage,
                     "broker_reconnect_refresh_dispatched") == 0)
        {
            refreshStage->store(stageSequence->fetch_add(1) + 1);
        }
        else if (std::strcmp(stage,
                     "broker_reconnect_recovery_risk_dispatched") == 0)
        {
            riskStage->store(stageSequence->fetch_add(1) + 1);
            IbPaperExecutionRuntimeComposition* runtime =
                runtimePointer.load();
            assert(runtime != nullptr);
            const IBAuthoritativeRecoveryAuditSnapshot snapshot =
                runtime->Adapter().GetAuthoritativeRecoveryAuditSnapshot();
            riskAfterBoundaries->store(
                snapshot.active.complete && snapshot.terminal.complete &&
                snapshot.active.connectionEpoch != 0 &&
                snapshot.active.connectionEpoch ==
                    snapshot.terminal.connectionEpoch);
        }
        else if (std::strcmp(stage, "broker_reconnect_complete") == 0)
        {
            completeStage->store(stageSequence->fetch_add(1) + 1);
            reconnectComplete->store(true);
        }
    };

    IbPaperExecutionRuntimeComposition runtime(
        config,
        std::unique_ptr<IIBApiWrapper>(new FakeIbWrapper(broker)),
        hooks, killSwitch);
    runtimePointer.store(&runtime);
    assert(runtime.Start(reason));
    WaitForQuoteBarrier(broker);
    UnixExecutionServiceClient client(socketPath, 1000);

    IbPlaceOrderCommand sell = Place("paper-recovery-audit-late-fill");
    sell.order.action = "SELL";
    const IbPlaceOrderCommand command = Previewed(client, sell);
    const ExecutionCommandResult accepted = client.PlaceIbOrder(command);
    assert(accepted.status == ExecutionCommandStatus::Accepted);
    lateFillOrderId->store(accepted.orderId);

    // Model a broker view that has already dropped the order from the
    // account-wide active set while its terminal fill callback is still in
    // flight.  The local owner remains available to reconcile that callback.
    broker->openOrders.erase(accepted.orderId);
    const std::uint64_t priorActiveGeneration =
        runtime.Adapter().GetAuthoritativeCorrelationSnapshot().generation;
    assert(runtime.Adapter().ReqAuthoritativeOpenOrders());
    bool activeFlat = false;
    for (int attempt = 0; attempt < 2000 && !activeFlat; ++attempt)
    {
        const IBAuthoritativeCorrelationSnapshot active =
            runtime.Adapter().GetAuthoritativeCorrelationSnapshot();
        activeFlat = active.complete &&
            active.generation > priorActiveGeneration &&
            active.activeOrderIds.empty();
        if (!activeFlat)
            std::this_thread::sleep_for(std::chrono::milliseconds(1));
    }
    assert(activeFlat);

    ExecutionControlCommand audit;
    audit.context = command.context;
    audit.context.toolCallId = "paper-recovery-audit-first";
    audit.recoveryIngressFence = 1;
    const ExecutionControlResult first = client.RecoveryAuditOwner(audit);
    assert(first.status == ExecutionCommandStatus::Rejected);
    assert(first.reasonCode ==
        "IB_RECOVERY_AUDIT_NEW_CONNECTION_EPOCH_REQUIRED");
    assert(first.brokerConnectionEpoch != 0);
    assert(!first.brokerRecoveryAuditBarrierComplete);
    assert(first.brokerRecoveryAuditNewConnectionEpochRequired);

    for (int attempt = 0; attempt < 3000 &&
         !postFillDeferred->load(); ++attempt)
        std::this_thread::sleep_for(std::chrono::milliseconds(1));
    assert(postFillDeferred->load());
    assert(lateFillInjected->load());
    assert(runtime.IsMutationBlocked(&reason));
    assert(reason == "IB_PAPER_BROKER_RECONNECT_PENDING");
    const ExecutionCommandResult closedGate =
        client.PreviewOrder(Place("paper-recovery-audit-gate-closed"));
    assert(closedGate.status == ExecutionCommandStatus::Rejected);
    assert(closedGate.reasonCode == "EXECUTION_SERVICE_NOT_READY");
    releasePostFillDeferred->store(true);

    for (int attempt = 0; attempt < 6000 &&
         !reconnectComplete->load(); ++attempt)
        std::this_thread::sleep_for(std::chrono::milliseconds(1));
    assert(reconnectComplete->load());
    assert(!runtime.HasFatalRuntimeError(&reason));
    assert(riskAfterBoundaries->load());
    assert(refreshStage->load() > 0);
    assert(riskStage->load() > refreshStage->load());
    assert(completeStage->load() > riskStage->load());
    assert(broker->reconnectAttempts.load() == 2);

    audit.context.toolCallId = "paper-recovery-audit-second";
    const ExecutionControlResult second = client.RecoveryAuditOwner(audit);
    assert(second.status == ExecutionCommandStatus::Accepted);
    assert(second.reasonCode == "RECOVERY_OWNER_ZERO_CONFIRMED");
    assert(second.ownerAuditAuthoritative);
    assert(second.ownerAuditComplete);
    assert(second.brokerConnectionEpoch > first.brokerConnectionEpoch);
    assert(second.brokerRecoveryAuditBarrierComplete);
    assert(!second.brokerRecoveryAuditNewConnectionEpochRequired);
    assert(!second.brokerPostFillRiskReconciliationPending);
    assert(second.brokerGlobalActiveOrderCount == 0);
    assert(second.ownerActiveOrderCount == 0);
    assert(second.ownerUncertainCommandCount == 0);
    assert(second.brokerPositionQuantity == "0");
    assert(second.brokerGrossAbsolutePosition == "0");
    assert(second.brokerTerminalExposureGeneration <=
        second.brokerRiskAbsorbedExposureGeneration);
    assert(second.brokerRiskAbsorbedExposureGeneration ==
        second.brokerExposureGeneration);

    ExecutionControlCommand terminal = audit;
    terminal.context.toolCallId = "paper-terminalize-first";
    terminal.targetCommandId = "paper-terminal-finalization-1";
    terminal.terminalPreliminaryReceiptSha256 =
        "sha256:" + std::string(64, 'a');
    const ExecutionControlResult terminalRejected =
        client.TerminalizeRecoveryOwner(terminal);
    assert(terminalRejected.status == ExecutionCommandStatus::Rejected);
    assert(terminalRejected.reasonCode ==
        "POST_CUTOFF_SIGNED_WITNESS_REQUIRED");
    assert(runtime.Adapter().IsTerminalTransportHalted());
    assert(!runtime.Adapter().IsTerminalTransportDrainVerified());
    assert(!runtime.Adapter().IsConnected());
    assert(runtime.IsMutationBlocked(&reason));
    assert(reason == "IB_PAPER_TERMINAL_HALTED");
    const std::string terminalizing = ReadTestFile(
        state + "/ib-paper-terminal-halt.v1");
    assert(terminalizing.find("state=TERMINALIZING\n") !=
        std::string::npos);
    assert(terminalizing.find("state=TERMINAL_HALTED\n") ==
        std::string::npos);

    terminal.context.toolCallId = "paper-terminalize-replay";
    const ExecutionControlResult replayRejected =
        client.TerminalizeRecoveryOwner(terminal);
    assert(replayRejected.status == ExecutionCommandStatus::Rejected);
    assert(replayRejected.reasonCode ==
        "POST_CUTOFF_SIGNED_WITNESS_REQUIRED");
    const ExecutionCommandResult terminalGate =
        client.PreviewOrder(Place("paper-terminal-gate-closed"));
    assert(terminalGate.status == ExecutionCommandStatus::Rejected);
    assert(terminalGate.reasonCode == "EXECUTION_SERVICE_NOT_READY");
    const int connectCountBeforeRestart = broker->reconnectAttempts.load();
    runtime.Stop();

    const std::string restartedSocketPath = SocketPath(
        "hepta-ib-terminal-restart");
    const std::string restartedEventPath = SocketPath(
        "hepta-ib-terminal-restart-events");
    IbPaperExecutionRuntimeConfig restartedConfig = config;
    restartedConfig.listenFd = ActivatedSocket(restartedSocketPath);
    restartedConfig.eventListenFd = ActivatedSocket(restartedEventPath);
    IbPaperExecutionRuntimeComposition restarted(
        restartedConfig,
        std::unique_ptr<IIBApiWrapper>(new FakeIbWrapper(broker)),
        IbPaperExecutionRuntimeTestHooks(), killSwitch);
    assert(restarted.Start(reason));
    assert(restarted.IsRunning());
    assert(broker->reconnectAttempts.load() == connectCountBeforeRestart);
    UnixExecutionServiceClient restartedClient(restartedSocketPath, 1000);
    terminal.context.toolCallId = "paper-terminalize-restart-replay";
    const ExecutionControlResult restartReplay =
        restartedClient.TerminalizeRecoveryOwner(terminal);
    assert(restartReplay.status == ExecutionCommandStatus::Rejected);
    assert(restartReplay.reasonCode ==
        "POST_CUTOFF_SIGNED_WITNESS_REQUIRED");
    assert(!restartReplay.terminalBrokerCallbackQueueDrained);
    terminal.terminalPreliminaryReceiptSha256 =
        "sha256:" + std::string(64, 'b');
    terminal.context.toolCallId = "paper-terminalize-binding-mismatch";
    const ExecutionControlResult mismatch =
        restartedClient.TerminalizeRecoveryOwner(terminal);
    assert(mismatch.status == ExecutionCommandStatus::Rejected);
    assert(mismatch.reasonCode ==
        "IB_PAPER_TERMINAL_LATCH_BINDING_MISMATCH");
    restarted.Stop();

    ::unlink(socketPath.c_str());
    ::unlink(eventPath.c_str());
    ::unlink(restartedSocketPath.c_str());
    ::unlink(restartedEventPath.c_str());
    ::unlink((state + "/oms-journal.jsonl").c_str());
    ::unlink((state + "/ib-paper-runtime.lock").c_str());
    ::unlink((state + "/ib-observability.jsonl").c_str());
    ::unlink(FxCashRestartCheckpointPath(state).c_str());
    ::unlink((state + "/ib-paper-terminal-halt.v1").c_str());
    ::unlink((state + "/ib-paper-terminal-mutation-manifest.v1").c_str());
    ::unlink((credentials + "/hepta-execution-fence").c_str());
    ::unlink((credentials +
              "/hepta-ib-paper-authorization").c_str());
    assert(::rmdir(credentials.c_str()) == 0);
    assert(::rmdir(state.c_str()) == 0);
}

ExecutionControlResult EstablishFlatRecoveryBarrier(
    UnixExecutionServiceClient& client,
    const AgentExecutionContext& owner)
{
    ExecutionControlResult result;
    for (int attempt = 0; attempt < 400; ++attempt)
    {
        ExecutionControlCommand audit;
        audit.context = owner;
        audit.context.toolCallId = "terminal-recovery-audit-" +
            std::to_string(attempt);
        audit.recoveryIngressFence = 7;
        result = client.RecoveryAuditOwner(audit);
        if (result.status == ExecutionCommandStatus::Accepted) break;
        assert(result.reasonCode ==
                   "IB_RECOVERY_AUDIT_NEW_CONNECTION_EPOCH_REQUIRED" ||
               result.reasonCode == "EXECUTION_SERVICE_NOT_READY" ||
               result.reasonCode == "connect failed" ||
               result.reasonCode == "read failed");
        std::this_thread::sleep_for(std::chrono::milliseconds(10));
    }
    assert(result.status == ExecutionCommandStatus::Accepted);
    assert(result.brokerRecoveryAuditBarrierComplete);
    assert(result.brokerGlobalActiveOrderCount == 0);
    assert(result.brokerPositionQuantity == "0");
    assert(result.brokerGrossAbsolutePosition == "0");
    return result;
}

void TestTerminalProtocolRejectsNonCanonicalSha256()
{
    ExecutionServiceRequest request;
    request.operation =
        ExecutionServiceOperation::TerminalizeRecoveryOwner;
    request.expectedServiceEpoch = "service-epoch";
    request.expectedServiceFencingGeneration = 9;
    request.control.context.agentId = "agent";
    request.control.context.sessionId = "session";
    request.control.context.toolCallId = "terminal-protocol";
    request.control.context.account = "DU123456";
    request.control.context.venue = "IB";
    request.control.context.executionDomain = "PAPER";
    request.control.targetCommandId = "finalization";
    request.control.recoveryIngressFence = 7;
    request.control.terminalPreliminaryReceiptSha256 =
        "sha256:" + std::string(64, 'A');
    std::string body;
    std::string reason;
    assert(!ExecutionServiceProtocol::EncodeRequest(
        request, body, reason));
    assert(reason == "EXECUTION_PROTOCOL_INVALID_TERMINAL_BINDING");

    request.control.terminalPreliminaryReceiptSha256 =
        "sha256:" + std::string(64, 'a');
    assert(ExecutionServiceProtocol::EncodeRequest(
        request, body, reason));
    ExecutionServiceRequest decoded;
    assert(ExecutionServiceProtocol::DecodeRequest(
        body, decoded, reason));
    const std::size_t hashOffset = body.find("sha256:");
    assert(hashOffset != std::string::npos);
    body[hashOffset + 7] = 'A';
    assert(!ExecutionServiceProtocol::DecodeRequest(
        body, decoded, reason));
    assert(reason == "EXECUTION_PROTOCOL_INVALID_CONTROL");

    ExecutionControlResult response;
    response.status = ExecutionCommandStatus::Accepted;
    response.commandId = "terminal-protocol";
    response.targetCommandId = "finalization";
    response.mutationBlocked = true;
    response.serviceEpoch = "current-epoch";
    response.serviceFencingGeneration = 9;
    response.terminalizationServiceEpoch = "terminal-epoch";
    response.terminalizationServiceFencingGeneration = 9;
    response.terminalizationGeneration = 1;
    response.terminalLatchSha256 =
        "sha256:" + std::string(64, 'A');
    response.terminalMutationGateClosed = true;
    response.terminalBrokerTransportConnected = false;
    response.terminalBrokerEventIngressHalted = true;
    response.terminalBrokerCallbackQueueDrained = true;
    response.terminalBrokerCallbacksInFlight = 0;
    response.terminalBrokerReconnectPermitted = false;
    response.terminalLatchDurable = true;
    response.terminalRuntimeLatchLoaded = true;
    response.terminalRuntimeVerified = true;
    assert(!ExecutionServiceProtocol::EncodeControlResponse(
        response, body, reason));
    assert(reason == "EXECUTION_PROTOCOL_INVALID_TERMINAL_WITNESS");
}

void TestTerminalizationRejectsLateFillAndRestartCannotReconnect()
{
    const char* const cases[] = {"pre_disconnect", "during_disconnect"};
    for (std::size_t index = 0;
         index < sizeof(cases) / sizeof(cases[0]); ++index)
    {
        const std::string state = TempDirectory(
            "/tmp/hepta-ib-terminal-late-state-XXXXXX");
        const std::string credentials = TempDirectory(
            "/tmp/hepta-ib-terminal-late-cred-XXXXXX");
        WriteFile(credentials + "/hepta-execution-fence",
            "HFC1\nfencing_token=77\ngeneration=9\n");
        IbPaperExecutionRuntimeConfig config =
            Config(-1, -1, state, credentials);
        config.reconnectTimeoutMs = 3000;
        std::string authorization;
        std::string reason;
        assert(config.profile.BuildAuthorizationCredential(
            authorization, reason));
        WriteFile(credentials + "/hepta-ib-paper-authorization",
            authorization + "\n");

        const std::string stem = std::string("hepta-terminal-late-") +
            cases[index];
        const std::string socketPath = SocketPath(stem.c_str());
        const std::string eventPath = SocketPath(
            (stem + "-events").c_str());
        config.listenFd = ActivatedSocket(socketPath);
        config.eventListenFd = ActivatedSocket(eventPath);
        const std::shared_ptr<FakeBrokerState> broker(
            new FakeBrokerState());
        const std::shared_ptr<MutableKillSwitch> killSwitch(
            new MutableKillSwitch());
        IbPaperExecutionRuntimeTestHooks hooks;
        hooks.reconnectApiFactory = [broker]() {
            return std::unique_ptr<IIBApiWrapper>(
                new FakeIbWrapper(broker));
        };
        IbPaperExecutionRuntimeComposition runtime(
            config,
            std::unique_ptr<IIBApiWrapper>(new FakeIbWrapper(broker)),
            hooks, killSwitch);
        assert(runtime.Start(reason));
        UnixExecutionServiceClient client(socketPath, 1000);
        AgentExecutionContext owner;
        owner.agentId = "agent";
        owner.sessionId = "terminal-late-session";
        owner.toolCallId = "terminal-owner";
        owner.strategy = "terminal-test";
        owner.account = "DU123456";
        owner.venue = "IB";
        owner.executionDomain = "PAPER";
        EstablishFlatRecoveryBarrier(client, owner);

        const long lateOrderId = 700 + static_cast<long>(index);
        if (index == 0)
        {
            IBEvent filled = Event(
                IBEventType::OrderStatus, lateOrderId);
            filled.key = "Filled";
            filled.number = 1.10010;
            filled.number2 = 1.0;
            filled.number3 = 0.0;
            std::lock_guard<std::mutex> lock(broker->injectedMutex);
            broker->injectedEvents.push_back(filled);
        }
        else
            broker->disconnectFillOrderId.store(lateOrderId);

        ExecutionControlCommand terminal;
        terminal.context = owner;
        terminal.context.toolCallId = "terminalize-late-fill";
        terminal.targetCommandId = "terminal-late-finalization";
        terminal.recoveryIngressFence = 7;
        terminal.terminalPreliminaryReceiptSha256 =
            "sha256:" + std::string(64, 'c');
        const ExecutionControlResult rejected =
            client.TerminalizeRecoveryOwner(terminal);
        assert(rejected.status == ExecutionCommandStatus::Rejected);
        assert(rejected.reasonCode ==
            "POST_CUTOFF_SIGNED_WITNESS_REQUIRED");
        assert(!runtime.Adapter().IsConnected());
        assert(runtime.Adapter().IsTerminalTransportHalted());
        assert(!runtime.Adapter().IsTerminalTransportDrainVerified());
        const std::string intent = ReadTestFile(
            state + "/ib-paper-terminal-halt.v1");
        assert(intent.find("state=TERMINALIZING\n") !=
            std::string::npos);
        assert(intent.find("state=TERMINAL_HALTED\n") ==
            std::string::npos);
        assert(runtime.IsMutationBlocked(&reason));
        assert(reason == "IB_PAPER_TERMINAL_HALTED");

        terminal.context.toolCallId = "terminalize-late-fill-replay";
        const ExecutionControlResult incomplete =
            client.TerminalizeRecoveryOwner(terminal);
        assert(incomplete.status == ExecutionCommandStatus::Rejected);
        assert(incomplete.reasonCode ==
            "POST_CUTOFF_SIGNED_WITNESS_REQUIRED");
        const int connectCountBeforeRestart =
            broker->reconnectAttempts.load();
        runtime.Stop();

        const std::string restartSocket = SocketPath(
            (stem + "-restart").c_str());
        const std::string restartEvent = SocketPath(
            (stem + "-restart-events").c_str());
        IbPaperExecutionRuntimeConfig restartConfig = config;
        restartConfig.listenFd = ActivatedSocket(restartSocket);
        restartConfig.eventListenFd = ActivatedSocket(restartEvent);
        IbPaperExecutionRuntimeComposition restarted(
            restartConfig,
            std::unique_ptr<IIBApiWrapper>(new FakeIbWrapper(broker)),
            hooks, killSwitch);
        assert(restarted.Start(reason));
        assert(restarted.IsRunning());
        assert(broker->reconnectAttempts.load() ==
            connectCountBeforeRestart);
        UnixExecutionServiceClient restartClient(restartSocket, 1000);
        terminal.context.toolCallId =
            "terminalize-late-fill-restart-replay";
        const ExecutionControlResult restartIncomplete =
            restartClient.TerminalizeRecoveryOwner(terminal);
        assert(restartIncomplete.status ==
            ExecutionCommandStatus::Rejected);
        assert(restartIncomplete.reasonCode ==
            "POST_CUTOFF_SIGNED_WITNESS_REQUIRED");
        assert(!restartIncomplete.terminalBrokerCallbackQueueDrained);
        restarted.Stop();

        ::unlink(socketPath.c_str());
        ::unlink(eventPath.c_str());
        ::unlink(restartSocket.c_str());
        ::unlink(restartEvent.c_str());
        ::unlink((state + "/oms-journal.jsonl").c_str());
        ::unlink((state + "/ib-paper-runtime.lock").c_str());
        ::unlink((state + "/ib-observability.jsonl").c_str());
        ::unlink(FxCashRestartCheckpointPath(state).c_str());
        ::unlink((state + "/ib-paper-terminal-halt.v1").c_str());
        ::unlink((state + "/ib-paper-terminal-mutation-manifest.v1").c_str());
        ::unlink((credentials + "/hepta-execution-fence").c_str());
        ::unlink((credentials +
                  "/hepta-ib-paper-authorization").c_str());
        assert(::rmdir(credentials.c_str()) == 0);
        assert(::rmdir(state.c_str()) == 0);
    }
}
}

int main(int argc, char** argv)
{
    if (argc == 2 && std::string(argv[1]) == "--owner-orders-only") {
        TestOrdersListPreservesGlobalAndProjectsExactSessionOwner();
        return 0;
    }
    if (argc == 2 && std::string(argv[1]) == "--restart-evidence-only") {
        TestRestartRefreshRecoversOwnerAndOrderIdReuseResetsEvidence();
        return 0;
    }
    if (argc == 2 &&
        std::string(argv[1]) == "--missing-terminal-boundary-only") {
        TestStartupMissingCompletedOrdersEndFailsClosed();
        return 0;
    }
    if (argc == 2 && std::string(argv[1]) == "--control-reconnect-only") {
        TestBrokerControlErrorsForceReconnect();
        return 0;
    }
    if (argc == 2 &&
        std::string(argv[1]) == "--reconnect-late-fill-only") {
        TestReconnectLateEconomicFillAtDisconnect();
        TestReconnectLateEconomicFillAtDisconnect(true);
        return 0;
    }
    if (argc == 2 &&
        std::string(argv[1]) == "--reconnect-boundary-tail-fill-only") {
        TestReconnectBoundaryPendingTailEconomicFill();
        return 0;
    }
    if (argc == 2 &&
        std::string(argv[1]) == "--reconnect-boundary-tail-10197-only") {
        TestReconnectBoundaryPendingTailMarketData10197();
        return 0;
    }
    if (argc == 2 &&
        std::string(argv[1]) == "--reconnect-control-batch-only") {
        TestReconnectCoalescesTransportControlsBeforeRestore();
        return 0;
    }
    if (argc == 2 &&
        std::string(argv[1]) == "--control-reconnect-1100-only") {
        TestBrokerControlErrorsForceReconnect(1100);
        return 0;
    }
    if (argc == 2 && std::string(argv[1]) == "--startup-boundaries-only") {
        TestStartupRetriesTransientGatewayPortUnavailability();
        TestStartupMissingCompletedOrdersEndFailsClosed();
        return 0;
    }
    if (argc == 2 &&
        std::string(argv[1]) == "--startup-cancellation-only") {
        TestStartupCancellationProbeAbortsReadinessWait();
        return 0;
    }
    if (argc == 2 &&
        std::string(argv[1]) == "--startup-disconnect-late-fill-only") {
        TestStartupDisconnectBoundaryLateEconomicFill();
        return 0;
    }
    if (argc == 2 &&
        std::string(argv[1]) == "--disconnect-boundary-10197-only") {
        TestDisconnectBoundaryMarketData10197();
        return 0;
    }
    if (argc == 2 &&
        std::string(argv[1]) == "--stop-disconnect-late-fill-only") {
        TestStopDisconnectBoundaryLateEconomicFill();
        return 0;
    }
    if (argc == 2 &&
        std::string(argv[1]) == "--fail-reconnect-late-fill-only") {
        TestFailReconnectDisconnectBoundaryLateEconomicFill();
        return 0;
    }
    if (argc == 2 &&
        std::string(argv[1]) == "--startup-market-data-10197-only") {
        TestCurrentEpochMarketData10197IgnoresCallbackIdBeforeDispatch();
        TestStartupMarketData10197FailsClosedWithExplicitReason();
        TestStartupCurrentEpochMarketData10197WithoutRequestIdFailsClosed();
        TestStartupCashFarmGate10197FailsClosed();
        return 0;
    }
    if (argc == 2 &&
        std::string(argv[1]) == "--startup-market-data-gate-ordering-only") {
        TestStartupCashFarmGateRejectsMissing2104();
        TestStartupCashFarmGate10197FailsClosed();
        TestStartupCashFarmGateRequires2104();
        TestStartupCashFarmGateBlocksAfterFarmLoss();
        TestStartupCashFarmGateWaitsForUpstreamRestore();
        return 0;
    }
    if (argc == 2 &&
        std::string(argv[1]) == "--startup-market-data-farm-gate-only") {
        TestStartupCashFarmPositiveGateRejectsLate2119Race();
        return 0;
    }
    if (argc == 2 &&
        std::string(argv[1]) == "--startup-market-data-farm-reverse-only") {
        TestStartupCashFarmReverseOrderBlocksQuote();
        TestStartupCashFarmSameBatchReverseOrderBlocksQuote();
        return 0;
    }
    if (argc == 2 &&
        std::string(argv[1]) == "--reconnect-market-data-farm-reverse-only") {
        TestReconnectCashFarmReverseOrderBlocksQuote();
        return 0;
    }
    if (argc == 2 &&
        std::string(argv[1]) == "--startup-market-data-no-farm-only") {
        TestStartupMissingCashFarmNeverRequestsMarketData();
        return 0;
    }
    if (argc == 2 &&
        std::string(argv[1]) == "--startup-market-data-late-warning-only") {
        TestStartupLateCashFarmWarningFailsClosed();
        return 0;
    }
    if (argc == 2 &&
        std::string(argv[1]) == "--startup-market-data-late-1101-only") {
        TestStartupLateMarketDataLoss1101FailsClosed();
        return 0;
    }
    if (argc == 2 &&
        std::string(argv[1]) == "--startup-market-data-cancel-failure-only") {
        TestStartupMarketDataCancelFailureFailsClosed();
        return 0;
    }
    if (argc == 2 &&
        std::string(argv[1]) == "--quote-admission-race-only") {
        TestQuoteAdmissionRejectsPendingControlBeforeBegin();
        TestQuoteAdmissionSerializesFormalRequestCheck();
        return 0;
    }
    if (argc == 2 &&
        std::string(argv[1]) == "--quote-admission-1101-only") {
        TestQuoteAdmissionDataLoss1101BlocksFormalRequest();
        return 0;
    }
    if (argc == 2 &&
        std::string(argv[1]) == "--quote-admission-1102-only") {
        TestQuoteAdmissionDataMaintained1102RemainsRecoverable();
        return 0;
    }
    if (argc == 2 &&
        std::string(argv[1]) == "--reconnect-market-data-late-1101-only") {
        TestReconnectLateMarketDataLoss1101FailsClosed();
        return 0;
    }
    if (argc == 2 &&
        std::string(argv[1]) == "--quote-admission-silent-close-only") {
        TestQuoteAdmissionSilentDisconnectAfterRequestFailsClosed();
        return 0;
    }
    if (argc == 2 &&
        std::string(argv[1]) == "--quote-admission-delayed-10197-only") {
        TestQuoteAdmissionDelayed10197FailsBeforePublication();
        return 0;
    }
    if (argc == 2 &&
        std::string(argv[1]) == "--quote-admission-exception-only") {
        TestQuoteAdmissionExceptionCancelsUncertainRequest();
        return 0;
    }
    if (argc == 2 &&
        std::string(argv[1]) == "--steady-market-data-10197-only") {
        TestSteadyStateMarketData10197FailsClosedWithExplicitReason();
        return 0;
    }
    if (argc == 2 && std::string(argv[1]) == "--external-limit-only") {
        TestExternalLimitDayRuntimeFinalSend();
        return 0;
    }
    if (argc == 2 && std::string(argv[1]) == "--recovery-audit-only") {
        TestRecoveryAuditRequiresFreshEpochAndDefersLateTerminalFill();
        return 0;
    }
    if (argc == 2 && std::string(argv[1]) == "--terminalization-only") {
        TestTerminalProtocolRejectsNonCanonicalSha256();
        TestRecoveryAuditRequiresFreshEpochAndDefersLateTerminalFill();
        TestTerminalizationRejectsLateFillAndRestartCannotReconnect();
        return 0;
    }
    TestFxCashBaselineCredentialProductionParserAndStartupBinding();
    TestFxCashRestartCheckpointRejectsInvalidTamperedAndStaleState();
    TestBrokerEvidencePublishesPersistsAndIsOwnerScoped();
    TestRestartRefreshRecoversOwnerAndOrderIdReuseResetsEvidence();
    TestBrokerCallbackIdentityMismatchFailsClosed();
    TestBrokerReconnectRejectsLocalInFlightOrder();
    TestReconnectLateEconomicFillAtDisconnect();
    TestReconnectLateEconomicFillAtDisconnect(true);
    TestReconnectBoundaryPendingTailEconomicFill();
    TestReconnectBoundaryPendingTailMarketData10197();
    TestBrokerControlErrorsForceReconnect();
    TestReconnectCoalescesTransportControlsBeforeRestore();
    TestBrokerReconnectUpstreamUnavailableTimesOutClosed();
    TestReconnectCashFarmReverseOrderBlocksQuote();
    TestStartupRetriesTransientGatewayPortUnavailability();
    TestStartupCancellationProbeAbortsReadinessWait();
    TestCurrentEpochMarketData10197IgnoresCallbackIdBeforeDispatch();
    TestStartupMarketData10197FailsClosedWithExplicitReason();
    TestStartupCurrentEpochMarketData10197WithoutRequestIdFailsClosed();
    TestStartupCashFarmGateRejectsMissing2104();
    TestStartupCashFarmGate10197FailsClosed();
    TestStartupCashFarmGateRequires2104();
    TestStartupCashFarmGateBlocksAfterFarmLoss();
    TestStartupCashFarmGateWaitsForUpstreamRestore();
    TestStartupCashFarmPositiveGateRejectsLate2119Race();
    TestStartupCashFarmReverseOrderBlocksQuote();
    TestStartupCashFarmSameBatchReverseOrderBlocksQuote();
    TestQuoteAdmissionRejectsPendingControlBeforeBegin();
    TestQuoteAdmissionSerializesFormalRequestCheck();
    TestQuoteAdmissionDataLoss1101BlocksFormalRequest();
    TestQuoteAdmissionDataMaintained1102RemainsRecoverable();
    TestQuoteAdmissionSilentDisconnectAfterRequestFailsClosed();
    TestQuoteAdmissionDelayed10197FailsBeforePublication();
    TestQuoteAdmissionExceptionCancelsUncertainRequest();
    TestStartupMissingCashFarmNeverRequestsMarketData();
    TestStartupLateCashFarmWarningFailsClosed();
    TestStartupLateMarketDataLoss1101FailsClosed();
    TestStartupMarketDataCancelFailureFailsClosed();
    TestStartupDisconnectBoundaryLateEconomicFill();
    TestDisconnectBoundaryMarketData10197();
    TestStopDisconnectBoundaryLateEconomicFill();
    TestFailReconnectDisconnectBoundaryLateEconomicFill();
    TestSteadyStateMarketData10197FailsClosedWithExplicitReason();
    TestStartupMissingCompletedOrdersEndFailsClosed();
    TestStartupUpstreamUnavailableWaitsForRestore();
    TestBrokerReconnectAllowsDelayedUpstreamRestore();
    TestReconnectLateMarketDataLoss1101FailsClosed();
    TestRecoveryAuditRequiresFreshEpochAndDefersLateTerminalFill();
    TestTerminalProtocolRejectsNonCanonicalSha256();
    TestTerminalizationRejectsLateFillAndRestartCannotReconnect();
    TestBrokerThrowAfterSideEffectReconcilesByCorrelationOnly();
    TestOrdersListPreservesGlobalAndProjectsExactSessionOwner();
    TestExternalLimitDayRuntimeFinalSend();
    const std::string state = TempDirectory("/tmp/hepta-ib-runtime-state-XXXXXX");
    const std::string credentials = TempDirectory("/tmp/hepta-ib-runtime-cred-XXXXXX");
    WriteFile(credentials + "/hepta-execution-fence",
        "HFC1\nfencing_token=77\ngeneration=9\n");
    std::string authorization;
    std::string authorizationReason;
    const IbPaperExecutionRuntimeConfig authorizationConfig =
        Config(-1, -1, state, credentials);
    assert(authorizationConfig.profile.BuildAuthorizationCredential(
        authorization, authorizationReason));
    WriteFile(credentials + "/hepta-ib-paper-authorization",
        authorization + "\n");
    const std::string socketPath = SocketPath("hepta-ib-execution");
    const std::string eventPath = SocketPath("hepta-ib-events");
    std::shared_ptr<FakeBrokerState> broker(new FakeBrokerState());
    const std::shared_ptr<MutableKillSwitch> killSwitch(new MutableKillSwitch());
    const IbPlaceOrderCommand originalCommand = Place("paper-place-1");
    IbPlaceOrderCommand persistedCommand;

    const std::string fenceCredential =
        credentials + "/hepta-execution-fence";
    const auto assertInvalidFenceNumber = [&](const std::string& contents,
                                               const char* stem) {
        assert(::chmod(fenceCredential.c_str(), 0600) == 0);
        WriteFile(fenceCredential, contents);
        const std::string rejectedSocket = SocketPath(stem);
        const std::string rejectedEvent = SocketPath(
            (std::string(stem) + "-events").c_str());
        const std::shared_ptr<FakeBrokerState> untouchedBroker(
            new FakeBrokerState());
        {
            IbPaperExecutionRuntimeComposition rejected(
                Config(ActivatedSocket(rejectedSocket),
                       ActivatedSocket(rejectedEvent), state, credentials),
                std::unique_ptr<IIBApiWrapper>(
                    new FakeIbWrapper(untouchedBroker)),
                IbPaperExecutionRuntimeTestHooks(), killSwitch);
            std::string reason;
            assert(!rejected.Start(reason));
            assert(reason == "IB_PAPER_FENCE_CREDENTIAL_INVALID");
            assert(untouchedBroker->snapshotRequests.load() == 0);
            assert(untouchedBroker->sends == 0);
        }
        ::unlink(rejectedSocket.c_str());
        ::unlink(rejectedEvent.c_str());
    };
    assertInvalidFenceNumber(
        "HFC1\nfencing_token=+77\ngeneration=9\n", "hepta-ib-fence-plus");
    assertInvalidFenceNumber(
        "HFC1\nfencing_token= 77\ngeneration=9\n", "hepta-ib-fence-space");
    std::string embeddedNull = "HFC1\nfencing_token=77";
    embeddedNull.push_back('\0');
    embeddedNull.append("suffix\ngeneration=9\n");
    assertInvalidFenceNumber(embeddedNull, "hepta-ib-fence-nul");
    assert(::chmod(fenceCredential.c_str(), 0600) == 0);
    WriteFile(fenceCredential,
        "HFC1\nfencing_token=77\ngeneration=9\n");

    {
        const std::string rejectedSocket = SocketPath("hepta-ib-control-rejected");
        const std::string rejectedEvent = SocketPath("hepta-ib-control-events-rejected");
        const std::shared_ptr<FakeBrokerState> untouchedBroker(new FakeBrokerState());
        IbPaperExecutionRuntimeTestHooks hooks;
        hooks.openKillSwitch = [](const std::string&,
                                  std::shared_ptr<IbPaperKillSwitchReader>&,
                                  std::string& reason) {
            reason = "IB_PAPER_KILL_SWITCH_CONTROL_UNSAFE";
            return false;
        };
        IbPaperExecutionRuntimeComposition rejected(
            Config(ActivatedSocket(rejectedSocket), ActivatedSocket(rejectedEvent),
                   state, credentials),
            std::unique_ptr<IIBApiWrapper>(new FakeIbWrapper(untouchedBroker)),
            hooks);
        std::string reason;
        assert(!rejected.Start(reason));
        assert(reason == "IB_PAPER_KILL_SWITCH_CONTROL_UNSAFE");
        assert(untouchedBroker->snapshotRequests.load() == 0);
        assert(untouchedBroker->sends == 0);
        ::unlink(rejectedSocket.c_str());
        ::unlink(rejectedEvent.c_str());
    }

    {
        const std::string finalGateSocket = SocketPath("hepta-ib-final-send-gate");
        const std::string finalGateEvent = SocketPath("hepta-ib-final-send-events");
        const std::shared_ptr<FakeBrokerState> finalGateBroker(new FakeBrokerState());
        const std::shared_ptr<MutableKillSwitch> finalGateSwitch(new MutableKillSwitch());
        const std::shared_ptr<std::atomic<bool> > beforeVenueSend(
            new std::atomic<bool>(false));
        const std::shared_ptr<std::vector<std::string> > startupStages(
            new std::vector<std::string>());
        IbPaperExecutionRuntimeTestHooks hooks;
        hooks.onStage = [
            beforeVenueSend, finalGateBroker, startupStages](const char* stage) {
            if (std::strcmp(stage, "startup_contract_validated") == 0 ||
                std::strcmp(stage, "execution_foundation_ready") == 0 ||
                std::strcmp(stage, "coordinator_ready") == 0 ||
                std::strcmp(stage, "policy_authority_ready") == 0)
                startupStages->push_back(stage);
            if (std::strcmp(stage, "before_venue_send") == 0) {
                finalGateBroker->blockPoll.store(true);
                for (int attempt = 0; attempt < 1000 &&
                     !finalGateBroker->pollEntered.load(); ++attempt)
                    std::this_thread::sleep_for(std::chrono::milliseconds(1));
                assert(finalGateBroker->pollEntered.load());
                beforeVenueSend->store(true);
            }
        };
        IbPaperExecutionRuntimeComposition finalGateRuntime(
            Config(ActivatedSocket(finalGateSocket), ActivatedSocket(finalGateEvent),
                   state, credentials),
            std::unique_ptr<IIBApiWrapper>(new FakeIbWrapper(finalGateBroker)),
            hooks, finalGateSwitch);
        std::string reason;
        assert(finalGateRuntime.Start(reason));
        const std::vector<std::string> expectedStartupStages = {
            "startup_contract_validated",
            "execution_foundation_ready",
            "coordinator_ready",
            "policy_authority_ready",
        };
        assert(*startupStages == expectedStartupStages);
        UnixExecutionServiceClient client(finalGateSocket, 1000);
        const IbPlaceOrderCommand finalGateCommand =
            Previewed(client, Place("paper-place-final-send-gate"));
        ExecutionCommandResult blocked;
        std::thread placeThread([&client, &blocked, &finalGateCommand]() {
            blocked = client.PlaceIbOrder(finalGateCommand);
        });
        for (int attempt = 0; attempt < 1000 &&
             !beforeVenueSend->load(); ++attempt)
            std::this_thread::sleep_for(std::chrono::milliseconds(1));
        assert(beforeVenueSend->load());
        finalGateSwitch->state = IbPaperKillSwitchState::Engaged;
        finalGateBroker->releasePoll.store(true);
        placeThread.join();
        assert(blocked.status == ExecutionCommandStatus::Rejected);
        assert(blocked.reasonCode == "IB_PAPER_KILL_SWITCH_ENGAGED");
        assert(finalGateBroker->sends == 0);
        finalGateRuntime.Stop();
        ::unlink(finalGateSocket.c_str());
        ::unlink(finalGateEvent.c_str());
    }

    {
        const std::string mismatchSocket =
            SocketPath("hepta-ib-place-contract-mismatch");
        const std::string mismatchEvent =
            SocketPath("hepta-ib-place-contract-mismatch-events");
        const std::string mismatchState = TempDirectory(
            "/tmp/hepta-ib-contract-mismatch-state-XXXXXX");
        const std::string mismatchCredentials = TempDirectory(
            "/tmp/hepta-ib-contract-mismatch-cred-XXXXXX");
        WriteFile(mismatchCredentials + "/hepta-execution-fence",
            "HFC1\nfencing_token=77\ngeneration=9\n");
        std::string mismatchAuthorization;
        std::string mismatchAuthorizationReason;
        const IbPaperExecutionRuntimeConfig mismatchAuthorizationConfig =
            Config(-1, -1, mismatchState, mismatchCredentials);
        assert(mismatchAuthorizationConfig.profile.BuildAuthorizationCredential(
            mismatchAuthorization, mismatchAuthorizationReason));
        WriteFile(mismatchCredentials + "/hepta-ib-paper-authorization",
            mismatchAuthorization + "\n");
        const std::shared_ptr<FakeBrokerState> mismatchBroker(
            new FakeBrokerState());
        const std::shared_ptr<std::atomic<int> > appliedQuoteTicks(
            new std::atomic<int>(0));
        IbPaperExecutionRuntimeTestHooks hooks;
        hooks.onStage = [appliedQuoteTicks](const char* stage) {
            if (std::strcmp(
                    stage, "authoritative_quote_tick_applied") == 0)
                ++(*appliedQuoteTicks);
        };
        IbPaperExecutionRuntimeComposition mismatchRuntime(
            Config(ActivatedSocket(mismatchSocket),
                   ActivatedSocket(mismatchEvent), mismatchState,
                   mismatchCredentials),
            std::unique_ptr<IIBApiWrapper>(
                new FakeIbWrapper(mismatchBroker)),
            hooks, killSwitch);
        std::string reason;
        assert(mismatchRuntime.Start(reason));
        const int appliedBefore = appliedQuoteTicks->load();
        InjectQuoteTick(mismatchBroker, "1", 1.1000);
        InjectQuoteTick(mismatchBroker, "2", 1.1002);
        WaitForQuoteBarrier(mismatchBroker);
        WaitForAppliedQuoteTicks(appliedQuoteTicks, appliedBefore + 2);
        UnixExecutionServiceClient client(mismatchSocket, 5000);

        IBContractLite mismatches[4];
        for (std::size_t i = 0; i < 4; ++i)
            mismatches[i] = Place("contract-template").contract;
        // A fresh EUR.USD quote must not authorize a GBP/USD venue order.
        mismatches[0].symbol = "GBP";
        // Even a matching symbol/currency/security type is not enough: all
        // allowlist fields remain part of the exact venue contract identity.
        mismatches[1].exchange = "SMART";
        // The quoted CASH identity cannot be rebound to another asset class.
        mismatches[2].secType = "STK";
        // A less prominent identity field is equally exact-bound.
        mismatches[3].primaryExchange = "ARCA";
        const char* ids[] = {
            "paper-place-gbp-contract-mismatch",
            "paper-place-exchange-contract-mismatch",
            "paper-place-security-type-contract-mismatch",
            "paper-place-primary-exchange-contract-mismatch",
        };
        IbPlaceOrderCommand durableRejected;
        for (std::size_t i = 0; i < 4; ++i)
        {
            IbPlaceOrderCommand input = Place(ids[i]);
            input.contract = mismatches[i];
            IbPlaceOrderCommand command = input;
            const ExecutionCommandResult preview =
                client.PreviewOrder(command);
            if (preview.status != ExecutionCommandStatus::Accepted)
                std::cerr << "contract mismatch preview case=" << i
                          << " reason=" << preview.reasonCode
                          << " detail=" << preview.detail << std::endl;
            assert(preview.status == ExecutionCommandStatus::Accepted);
            command.previewPermit = PreviewField(preview, "preview_permit");
            command.context.toolCallId =
                PreviewField(preview, "command_id");
            const ExecutionCommandResult rejected =
                client.PlaceIbOrder(command);
            assert(rejected.status == ExecutionCommandStatus::Rejected);
            assert(rejected.reasonCode ==
                "IB_PAPER_PLACE_CONTRACT_MISMATCH");
            assert(rejected.detail ==
                "IB_PAPER_PLACE_CONTRACT_MISMATCH");
            assert(mismatchBroker->sends == 0);
            if (i == 0) durableRejected = command;
        }

        ExecutionCommandResult durableDuplicate;
        assert(mismatchRuntime.Coordinator().PrecheckPlaceIbOrder(
            durableRejected, durableDuplicate));
        assert(durableDuplicate.status == ExecutionCommandStatus::Duplicate);
        assert(durableDuplicate.reasonCode == "DUPLICATE_TOOL_CALL");
        assert(durableDuplicate.detail == "previous_status=rejected");
        // Unix place permits are one-shot and are checked before policy and
        // coordinator dispatch. Therefore an old permit must fail closed,
        // while the coordinator's durable projection above proves idempotency.
        const ExecutionCommandResult consumedPermitReplay =
            client.PlaceIbOrder(durableRejected);
        assert(consumedPermitReplay.status ==
            ExecutionCommandStatus::Rejected);
        assert(consumedPermitReplay.reasonCode ==
            "EXECUTION_PREVIEW_PERMIT_UNKNOWN_OR_CONSUMED");
        assert(mismatchBroker->sends == 0);
        mismatchRuntime.Stop();

        const std::shared_ptr<FakeBrokerState> restartedBroker(
            new FakeBrokerState());
        {
            IbPaperExecutionRuntimeComposition restartedRuntime(
                Config(ActivatedSocket(mismatchSocket),
                       ActivatedSocket(mismatchEvent), mismatchState,
                       mismatchCredentials),
                std::unique_ptr<IIBApiWrapper>(
                    new FakeIbWrapper(restartedBroker)),
                IbPaperExecutionRuntimeTestHooks(), killSwitch);
            assert(restartedRuntime.Start(reason));
            ExecutionCommandResult restartedDuplicate;
            assert(restartedRuntime.Coordinator().PrecheckPlaceIbOrder(
                durableRejected, restartedDuplicate));
            assert(restartedDuplicate.status ==
                ExecutionCommandStatus::Duplicate);
            assert(restartedDuplicate.reasonCode == "DUPLICATE_TOOL_CALL");
            assert(restartedDuplicate.detail == "previous_status=rejected");
            UnixExecutionServiceClient restartedClient(mismatchSocket, 5000);
            const ExecutionCommandResult restartedPermitReplay =
                restartedClient.PlaceIbOrder(durableRejected);
            assert(restartedPermitReplay.status ==
                ExecutionCommandStatus::Rejected);
            assert(restartedPermitReplay.reasonCode ==
                "EXECUTION_PREVIEW_PERMIT_UNKNOWN_OR_CONSUMED");
            assert(restartedBroker->sends == 0);
            restartedRuntime.Stop();
        }

        OmsJournal journal;
        const IbPaperExecutionRuntimeConfig journalConfig =
            Config(-1, -1, mismatchState, mismatchCredentials);
        assert(journal.Init(journalConfig.journalPath));
        int intents = 0;
        int sendAttempts = 0;
        int rejects = 0;
        assert(journal.Replay([
            &durableRejected, &intents, &sendAttempts, &rejects](
                const OmsJournalEvent& event) {
            if (event.reqId != durableRejected.context.toolCallId) return;
            if (event.eventType == "order_intent")
                ++intents;
            else if (event.eventType == "place_send_attempt")
                ++sendAttempts;
            else if (event.eventType == "reject")
            {
                ++rejects;
                assert(event.riskCode ==
                    "IB_PAPER_PLACE_CONTRACT_MISMATCH");
                assert(event.reason ==
                    "IB_PAPER_PLACE_CONTRACT_MISMATCH");
            }
        }) >= 0);
        assert(intents == 1);
        assert(sendAttempts == 1);
        assert(rejects == 1);
        ::unlink(mismatchSocket.c_str());
        ::unlink(mismatchEvent.c_str());
        ::unlink((mismatchState + "/oms-journal.jsonl").c_str());
        ::unlink((mismatchState + "/ib-paper-runtime.lock").c_str());
        ::unlink((mismatchState + "/ib-observability.jsonl").c_str());
        ::unlink(FxCashRestartCheckpointPath(mismatchState).c_str());
        ::unlink((mismatchCredentials +
                  "/hepta-execution-fence").c_str());
        ::unlink((mismatchCredentials +
                  "/hepta-ib-paper-authorization").c_str());
        assert(::rmdir(mismatchCredentials.c_str()) == 0);
        assert(::rmdir(mismatchState.c_str()) == 0);
    }

    {
        const std::string pendingGateSocket =
            SocketPath("hepta-ib-post-fill-final-send-gate");
        const std::string pendingGateEvent =
            SocketPath("hepta-ib-post-fill-final-send-events");
        const std::shared_ptr<FakeBrokerState> pendingGateBroker(
            new FakeBrokerState());
        const std::shared_ptr<MutableKillSwitch> pendingGateSwitch(
            new MutableKillSwitch());
        const std::shared_ptr<std::atomic<bool> > injectPostFill(
            new std::atomic<bool>(false));
        const std::shared_ptr<std::atomic<long> > ownedOrderId(
            new std::atomic<long>(-1));
        IbPaperExecutionRuntimeTestHooks hooks;
        hooks.onStage = [pendingGateBroker, injectPostFill, ownedOrderId](
                            const char* stage) {
            if (std::strcmp(stage, "before_venue_send") != 0 ||
                !injectPostFill->exchange(false))
                return;
            pendingGateBroker->positionQuantity = 100.0;
            pendingGateBroker->economicFillDequeued.store(false);
            IBEvent filled = Event(
                IBEventType::OrderStatus, ownedOrderId->load());
            filled.key = "Filled";
            filled.number = 1.10015;
            filled.number2 = 100.0;
            filled.number3 = 0.0;
            {
                std::lock_guard<std::mutex> lock(
                    pendingGateBroker->injectedMutex);
                pendingGateBroker->injectedEvents.push_back(filled);
            }
            // The runtime is intentionally blocked on the coordinator owner
            // lock held by this place dispatch. The adapter must nevertheless
            // observe the economic fill and close its final broker-send gate.
            for (int attempt = 0; attempt < 2000 &&
                 !pendingGateBroker->economicFillDequeued.load(); ++attempt)
                std::this_thread::sleep_for(
                    std::chrono::milliseconds(1));
            assert(pendingGateBroker->economicFillDequeued.load());
        };
        IbPaperExecutionRuntimeComposition pendingGateRuntime(
            Config(ActivatedSocket(pendingGateSocket),
                   ActivatedSocket(pendingGateEvent), state, credentials),
            std::unique_ptr<IIBApiWrapper>(
                new FakeIbWrapper(pendingGateBroker)),
            hooks, pendingGateSwitch);
        std::string reason;
        assert(pendingGateRuntime.Start(reason));
        UnixExecutionServiceClient client(pendingGateSocket, 3000);
        const IbPlaceOrderCommand first = Previewed(
            client, Place("paper-post-fill-gate-owner"));
        const ExecutionCommandResult firstPlaced =
            client.PlaceIbOrder(first);
        assert(firstPlaced.status == ExecutionCommandStatus::Accepted);
        ownedOrderId->store(firstPlaced.orderId);
        assert(pendingGateBroker->sends == 1);

        // Freeze a preview against the pre-fill generation, then deliver the
        // fill after policy dispatch but before the adapter send lock check.
        IbPlaceOrderCommand stalePreviewInput =
            Place("paper-post-fill-gate-stale-preview");
        stalePreviewInput.order.action = "SELL";
        const IbPlaceOrderCommand stalePreview = Previewed(
            client, stalePreviewInput);
        injectPostFill->store(true);
        const ExecutionCommandResult blocked =
            client.PlaceIbOrder(stalePreview);
        assert(blocked.status == ExecutionCommandStatus::Rejected);
        assert(blocked.reasonCode ==
            "IB_POST_FILL_RISK_REFRESH_PENDING");
        assert(pendingGateBroker->sends == 1);

        bool reconciled = false;
        for (int attempt = 0; attempt < 100 && !reconciled; ++attempt)
        {
            std::string blockedReason;
            reconciled = !pendingGateRuntime.IsMutationBlocked(
                &blockedReason);
            if (!reconciled)
                std::this_thread::sleep_for(
                    std::chrono::milliseconds(50));
        }
        assert(reconciled);
        assert(!pendingGateRuntime.HasFatalRuntimeError(nullptr));
        assert(!pendingGateRuntime.Adapter()
            .HasPendingPostFillRiskReconciliation());
        pendingGateRuntime.Stop();
        // The next scenario intentionally starts from a zero-balance fixture;
        // do not let this block's committed non-flat restart point leak into it.
        assert(::unlink(FxCashRestartCheckpointPath(state).c_str()) == 0);
        ::unlink(pendingGateSocket.c_str());
        ::unlink(pendingGateEvent.c_str());
    }

    {
        const std::string quoteExpirySocket =
            SocketPath("hepta-ib-place-quote-expiry");
        const std::string quoteExpiryEvent =
            SocketPath("hepta-ib-place-quote-expiry-events");
        const std::shared_ptr<FakeBrokerState> quoteExpiryBroker(
            new FakeBrokerState());
        const std::shared_ptr<std::atomic<bool> > expiryDelayApplied(
            new std::atomic<bool>(false));
        const std::shared_ptr<std::atomic<int> > appliedQuoteTicks(
            new std::atomic<int>(0));
        IbPaperExecutionRuntimeTestHooks hooks;
        hooks.onStage = [expiryDelayApplied, appliedQuoteTicks](
                            const char* stage) {
            if (std::strcmp(
                    stage, "authoritative_quote_tick_applied") == 0)
            {
                ++(*appliedQuoteTicks);
                return;
            }
            if (std::strcmp(stage, "before_venue_send") != 0)
                return;
            std::this_thread::sleep_for(
                std::chrono::milliseconds(400));
            expiryDelayApplied->store(true);
        };
        IbPaperExecutionRuntimeConfig quoteExpiryConfig =
            Config(ActivatedSocket(quoteExpirySocket),
                   ActivatedSocket(quoteExpiryEvent), state, credentials);
        quoteExpiryConfig.quoteMaxAgeMs = 250;
        quoteExpiryConfig.ioTimeoutMs = 5000;
        IbPaperExecutionRuntimeComposition quoteExpiryRuntime(
            quoteExpiryConfig,
            std::unique_ptr<IIBApiWrapper>(
                new FakeIbWrapper(quoteExpiryBroker)),
            hooks, killSwitch);
        std::string reason;
        assert(quoteExpiryRuntime.Start(reason));
        const int appliedBefore = appliedQuoteTicks->load();
        InjectQuoteTick(quoteExpiryBroker, "1", 1.1000);
        InjectQuoteTick(quoteExpiryBroker, "2", 1.1002);
        WaitForQuoteBarrier(quoteExpiryBroker);
        WaitForAppliedQuoteTicks(appliedQuoteTicks, appliedBefore + 2);
        UnixExecutionServiceClient client(quoteExpirySocket, 5000);
        const IbPlaceOrderCommand command = Previewed(
            client, Place("paper-place-quote-expiry-before-send"));
        const ExecutionCommandResult rejected =
            client.PlaceIbOrder(command);
        assert(expiryDelayApplied->load());
        assert(rejected.status == ExecutionCommandStatus::Rejected);
        assert(rejected.reasonCode ==
            "IB_PAPER_PLACE_QUOTE_CHANGED_BEFORE_SEND");
        assert(rejected.detail ==
            "IB_PAPER_PLACE_QUOTE_CHANGED_BEFORE_SEND");
        assert(quoteExpiryBroker->sends == 0);
        quoteExpiryRuntime.Stop();
        ::unlink(quoteExpirySocket.c_str());
        ::unlink(quoteExpiryEvent.c_str());
    }

    {
        const std::string quoteDriftSocket =
            SocketPath("hepta-ib-place-quote-drift");
        const std::string quoteDriftEvent =
            SocketPath("hepta-ib-place-quote-drift-events");
        const std::shared_ptr<FakeBrokerState> quoteDriftBroker(
            new FakeBrokerState());
        const std::shared_ptr<std::atomic<bool> > blockPolledTick(
            new std::atomic<bool>(false));
        const std::shared_ptr<std::atomic<bool> > polledTickBlocked(
            new std::atomic<bool>(false));
        const std::shared_ptr<std::atomic<bool> > releasePolledTick(
            new std::atomic<bool>(false));
        const std::shared_ptr<std::atomic<int> > targetPolledTickCount(
            new std::atomic<int>(0));
        const std::shared_ptr<std::atomic<int> > appliedQuoteTicks(
            new std::atomic<int>(0));
        IbPaperExecutionRuntimeTestHooks hooks;
        hooks.onStage = [quoteDriftBroker, blockPolledTick,
                         polledTickBlocked, releasePolledTick,
                         targetPolledTickCount,
                         appliedQuoteTicks](
                            const char* stage) {
            if (std::strcmp(
                    stage, "authoritative_quote_tick_applied") == 0)
            {
                ++(*appliedQuoteTicks);
                return;
            }
            if (std::strcmp(stage,
                    "after_adapter_poll_before_drain") == 0)
            {
                if (!blockPolledTick->load() ||
                    quoteDriftBroker->injectedQuoteTicksPolled.load() <
                        targetPolledTickCount->load()) return;
                if (!blockPolledTick->exchange(false)) return;
                polledTickBlocked->store(true);
                for (int attempt = 0; attempt < 5000 &&
                     !releasePolledTick->load(); ++attempt)
                    std::this_thread::sleep_for(
                        std::chrono::milliseconds(1));
                assert(releasePolledTick->load());
                return;
            }
            if (std::strcmp(stage, "before_venue_send") != 0) return;
            targetPolledTickCount->store(
                quoteDriftBroker->injectedQuoteTicksPolled.load() + 1);
            blockPolledTick->store(true);
            InjectQuoteTick(quoteDriftBroker, "1", 1.0998);
            for (int attempt = 0; attempt < 2000 &&
                 !polledTickBlocked->load(); ++attempt)
                std::this_thread::sleep_for(
                    std::chrono::milliseconds(1));
            assert(polledTickBlocked->load());
        };
        IbPaperExecutionRuntimeConfig quoteDriftConfig =
            Config(ActivatedSocket(quoteDriftSocket),
                   ActivatedSocket(quoteDriftEvent), state, credentials);
        quoteDriftConfig.ioTimeoutMs = 5000;
        IbPaperExecutionRuntimeComposition quoteDriftRuntime(
            quoteDriftConfig,
            std::unique_ptr<IIBApiWrapper>(
                new FakeIbWrapper(quoteDriftBroker)),
            hooks, killSwitch);
        std::string reason;
        assert(quoteDriftRuntime.Start(reason));
        const int appliedBefore = appliedQuoteTicks->load();
        InjectQuoteTick(quoteDriftBroker, "1", 1.1000);
        InjectQuoteTick(quoteDriftBroker, "2", 1.1002);
        WaitForQuoteBarrier(quoteDriftBroker);
        WaitForAppliedQuoteTicks(appliedQuoteTicks, appliedBefore + 2);
        UnixExecutionServiceClient client(quoteDriftSocket, 5000);
        const IbPlaceOrderCommand command = Previewed(
            client, Place("paper-place-quote-drift-before-send"));
        ExecutionCommandResult rejected;
        const std::shared_ptr<std::atomic<bool> > placeCompleted(
            new std::atomic<bool>(false));
        std::thread placeThread([&client, &command, &rejected, placeCompleted]() {
            rejected = client.PlaceIbOrder(command);
            placeCompleted->store(true);
        });
        for (int attempt = 0; attempt < 2000 &&
             !polledTickBlocked->load(); ++attempt)
            std::this_thread::sleep_for(std::chrono::milliseconds(1));
        assert(polledTickBlocked->load());
        std::this_thread::sleep_for(std::chrono::milliseconds(20));
        assert(!placeCompleted->load());
        releasePolledTick->store(true);
        placeThread.join();
        assert(rejected.status == ExecutionCommandStatus::Rejected);
        assert(rejected.reasonCode ==
            "IB_PAPER_PLACE_QUOTE_CHANGED_BEFORE_SEND");
        assert(rejected.detail ==
            "IB_PAPER_PLACE_QUOTE_CHANGED_BEFORE_SEND");
        assert(quoteDriftBroker->sends == 0);
        quoteDriftRuntime.Stop();
        ::unlink(quoteDriftSocket.c_str());
        ::unlink(quoteDriftEvent.c_str());
    }

    {
        const std::string staleSocket = SocketPath("hepta-ib-stale-quote");
        const std::string staleEvent = SocketPath("hepta-ib-stale-quote-events");
        const std::shared_ptr<FakeBrokerState> staleBroker(
            new FakeBrokerState());
        IbPaperExecutionRuntimeConfig staleConfig =
            Config(ActivatedSocket(staleSocket), ActivatedSocket(staleEvent),
                   state, credentials);
        staleConfig.quoteMaxAgeMs = 250;
        IbPaperExecutionRuntimeComposition staleRuntime(
            staleConfig,
            std::unique_ptr<IIBApiWrapper>(new FakeIbWrapper(staleBroker)),
            IbPaperExecutionRuntimeTestHooks(), killSwitch);
        std::string reason;
        assert(staleRuntime.Start(reason));
        // Startup now includes a bounded upstream-control observation window;
        // refresh both sides before testing the deliberately short 250ms age.
        InjectQuoteTick(staleBroker, "1", 1.1000);
        InjectQuoteTick(staleBroker, "2", 1.1002);
        WaitForQuoteBarrier(staleBroker);
        UnixExecutionServiceClient client(staleSocket, 1000);
        ExecutionReadCommand quoteRead;
        quoteRead.context = originalCommand.context;
        quoteRead.context.toolCallId = "paper-read-fresh-quote";
        quoteRead.query = "market.get_quote";
        quoteRead.instrument = "EUR.USD";
        const ExecutionCommandResult fresh =
            client.ReadAuthoritativeState(quoteRead);
        assert(fresh.status == ExecutionCommandStatus::Accepted);
        std::this_thread::sleep_for(std::chrono::milliseconds(150));
        InjectQuoteTick(staleBroker, "1", 1.1001);
        std::this_thread::sleep_for(std::chrono::milliseconds(150));
        const ExecutionCommandResult oneSidedPreview =
            client.PreviewOrder(Place("paper-one-sided-preview"));
        assert(oneSidedPreview.status == ExecutionCommandStatus::Accepted);
        assert(staleBroker->sends == 0);

        InjectQuoteTick(staleBroker, "1", 1.1000);
        InjectQuoteTick(staleBroker, "2", 1.1002);
        std::this_thread::sleep_for(std::chrono::milliseconds(50));
        const IbPlaceOrderCommand lastOnlyFinal =
            Previewed(client, Place("paper-last-only-final"));

        std::this_thread::sleep_for(std::chrono::milliseconds(150));
        InjectQuoteTick(staleBroker, "4", 1.1001);
        std::this_thread::sleep_for(std::chrono::milliseconds(150));
        const ExecutionCommandResult lastOnlyPreview =
            client.PreviewOrder(Place("paper-last-only-preview"));
        assert(lastOnlyPreview.status == ExecutionCommandStatus::Rejected);
        if (lastOnlyPreview.reasonCode != "AUTHORITATIVE_QUOTE_STALE")
            std::cerr << "last-only preview reason=" << lastOnlyPreview.reasonCode
                      << " detail=" << lastOnlyPreview.detail << '\n';
        assert(lastOnlyPreview.reasonCode == "AUTHORITATIVE_QUOTE_STALE");
        const ExecutionCommandResult lastOnlyPlace =
            client.PlaceIbOrder(lastOnlyFinal);
        assert(lastOnlyPlace.status == ExecutionCommandStatus::Rejected);
        assert(lastOnlyPlace.reasonCode == "AUTHORITATIVE_QUOTE_STALE");
        assert(staleBroker->sends == 0);

        quoteRead.context.toolCallId = "paper-read-stale-quote";
        const ExecutionCommandResult stale =
            client.ReadAuthoritativeState(quoteRead);
        assert(stale.status == ExecutionCommandStatus::Rejected);
        assert(stale.reasonCode == "AUTHORITATIVE_QUOTE_STALE");
        assert(staleBroker->marketDataRequests.load() == 1);
        staleRuntime.Stop();
        assert(staleBroker->marketDataCancels.load() == 1);
        assert(::unlink(FxCashRestartCheckpointPath(state).c_str()) == 0);
        ::unlink(staleSocket.c_str());
        ::unlink(staleEvent.c_str());
    }

    {
        const std::string quoteDriftSocket =
            SocketPath("hepta-ib-flatten-quote-drift");
        const std::string quoteDriftEvent =
            SocketPath("hepta-ib-flatten-quote-drift-events");
        const std::shared_ptr<FakeBrokerState> quoteDriftBroker(
            new FakeBrokerState());
        quoteDriftBroker->positionQuantity = 100.0;
        quoteDriftBroker->positionKey = "CONID:12087792";
        quoteDriftBroker->positionContract.symbol = "EUR";
        quoteDriftBroker->positionContract.secType = "CASH";
        quoteDriftBroker->positionContract.exchange = "IDEALPRO";
        quoteDriftBroker->positionContract.currency = "USD";
        const std::shared_ptr<std::atomic<bool> > pendingTickBlocked(
            new std::atomic<bool>(false));
        const std::shared_ptr<std::atomic<bool> > releasePendingTick(
            new std::atomic<bool>(false));
        const std::shared_ptr<std::atomic<bool> > blockNextTick(
            new std::atomic<bool>(false));
        const std::shared_ptr<std::atomic<int> > appliedQuoteTicks(
            new std::atomic<int>(0));
        IbPaperExecutionRuntimeTestHooks hooks;
        hooks.onStage = [quoteDriftBroker, pendingTickBlocked,
                         releasePendingTick, blockNextTick,
                         appliedQuoteTicks](const char* stage) {
            if (std::strcmp(stage,
                    "authoritative_quote_tick_applied") == 0)
            { ++(*appliedQuoteTicks); return; }
            if (std::strcmp(stage,
                    "before_authoritative_quote_tick_apply") == 0)
            {
                if (!blockNextTick->exchange(false)) return;
                pendingTickBlocked->store(true);
                for (int attempt = 0; attempt < 5000 &&
                     !releasePendingTick->load(); ++attempt)
                    std::this_thread::sleep_for(
                        std::chrono::milliseconds(1));
                assert(releasePendingTick->load());
                return;
            }
            if (std::strcmp(stage,
                    "before_flatten_venue_send") != 0) return;
            blockNextTick->store(true);
            InjectQuoteTick(quoteDriftBroker, "1", 1.0998);
            for (int attempt = 0; attempt < 2000 &&
                 !pendingTickBlocked->load(); ++attempt)
                std::this_thread::sleep_for(
                    std::chrono::milliseconds(1));
            assert(pendingTickBlocked->load());
        };
        IbPaperExecutionRuntimeConfig quoteDriftConfig =
            Config(ActivatedSocket(quoteDriftSocket),
                   ActivatedSocket(quoteDriftEvent), state, credentials);
        quoteDriftConfig.fxCashBaselines["EUR.USD"].observedCashBalance =
            100.0;
        quoteDriftConfig.fxCashBaselines["EUR.USD"].campaignExecutionDelta =
            100.0;
        IbPaperExecutionRuntimeComposition quoteDriftRuntime(
            quoteDriftConfig,
            std::unique_ptr<IIBApiWrapper>(
                new FakeIbWrapper(quoteDriftBroker)),
            hooks, killSwitch);
        std::string reason;
        assert(quoteDriftRuntime.Start(reason));
        const int appliedBefore = appliedQuoteTicks->load();
        UnixExecutionServiceClient client(quoteDriftSocket, 2000);
        const FlattenPositionCommand command = PreviewedFlatten(
            client, Flatten("paper-flatten-quote-drift"));
        const ExecutionCommandResult rejected =
            client.FlattenPosition(command);
        releasePendingTick->store(true);
        assert(pendingTickBlocked->load());
        assert(rejected.status == ExecutionCommandStatus::Rejected);
        assert(rejected.reasonCode ==
            "IB_PAPER_FLATTEN_QUOTE_CHANGED_BEFORE_SEND");
        assert(rejected.detail ==
            "IB_PAPER_FLATTEN_QUOTE_CHANGED_BEFORE_SEND");
        assert(quoteDriftBroker->sends == 0);
        WaitForAppliedQuoteTicks(appliedQuoteTicks, appliedBefore + 1);
        const ExecutionCommandResult duplicate =
            client.FlattenPosition(command);
        assert(duplicate.status == ExecutionCommandStatus::Duplicate);
        assert(duplicate.reasonCode == "DUPLICATE_TOOL_CALL");
        assert(quoteDriftBroker->sends == 0);
        quoteDriftRuntime.Stop();

        const std::shared_ptr<FakeBrokerState> restartedBroker(
            new FakeBrokerState());
        restartedBroker->positionQuantity = 100.0;
        restartedBroker->positionKey = quoteDriftBroker->positionKey;
        restartedBroker->positionContract =
            quoteDriftBroker->positionContract;
        {
            IbPaperExecutionRuntimeConfig restartedConfig =
                Config(ActivatedSocket(quoteDriftSocket),
                       ActivatedSocket(quoteDriftEvent),
                       state, credentials);
            restartedConfig.fxCashBaselines["EUR.USD"].observedCashBalance =
                100.0;
            restartedConfig.fxCashBaselines["EUR.USD"].campaignExecutionDelta =
                100.0;
            IbPaperExecutionRuntimeComposition restartedRuntime(
                restartedConfig,
                std::unique_ptr<IIBApiWrapper>(
                    new FakeIbWrapper(restartedBroker)),
                IbPaperExecutionRuntimeTestHooks(), killSwitch);
            assert(restartedRuntime.Start(reason));
            UnixExecutionServiceClient restartedClient(
                quoteDriftSocket, 2000);
            const ExecutionCommandResult restartedDuplicate =
                restartedClient.FlattenPosition(command);
            assert(restartedDuplicate.status ==
                ExecutionCommandStatus::Duplicate);
            assert(restartedDuplicate.reasonCode ==
                "DUPLICATE_TOOL_CALL");
            assert(restartedDuplicate.detail ==
                "previous_status=rejected");
            assert(restartedBroker->sends == 0);
            restartedRuntime.Stop();
        }
        assert(::unlink(FxCashRestartCheckpointPath(state).c_str()) == 0);

        OmsJournal journal;
        assert(journal.Init(quoteDriftConfig.journalPath));
        int intents = 0;
        int sendAttempts = 0;
        int rejects = 0;
        assert(journal.Replay([
            &command, &intents, &sendAttempts, &rejects](
                const OmsJournalEvent& event) {
            if (event.reqId != command.context.toolCallId)
                return;
            if (event.eventType == "flatten_intent")
                ++intents;
            else if (event.eventType == "flatten_send_attempt")
                ++sendAttempts;
            else if (event.eventType == "flatten_reject")
            {
                ++rejects;
                assert(event.riskCode ==
                    "IB_PAPER_FLATTEN_QUOTE_CHANGED_BEFORE_SEND");
                assert(event.reason ==
                    "IB_PAPER_FLATTEN_QUOTE_CHANGED_BEFORE_SEND");
            }
        }) >= 0);
        assert(intents == 1);
        assert(sendAttempts == 1);
        assert(rejects == 1);
        ::unlink(quoteDriftSocket.c_str());
        ::unlink(quoteDriftEvent.c_str());
    }

    {
        IbPaperExecutionRuntimeComposition runtime(
            Config(ActivatedSocket(socketPath), ActivatedSocket(eventPath), state, credentials),
            std::unique_ptr<IIBApiWrapper>(new FakeIbWrapper(broker)),
            IbPaperExecutionRuntimeTestHooks(), killSwitch);
        std::string reason;
        assert(runtime.Start(reason));
        assert(runtime.IsRunning());
        WaitForQuoteBarrier(broker);
        for (int attempt = 0; attempt < 1000 &&
             broker->lastPollTimeoutMs.load() != 0; ++attempt)
            std::this_thread::sleep_for(std::chrono::milliseconds(1));
        assert(broker->lastPollTimeoutMs.load() == 0);
        UnixExecutionServiceClient client(socketPath, 1000);
        UnixExecutionEventFeedClient eventClient(eventPath, 1000);
        ExecutionServiceIdentity mutationIdentity;
        std::string identityReason;
        assert(client.GetServiceIdentity(mutationIdentity, identityReason));
        const ExecutionEventReadResult eventIdentity =
            eventClient.GetServiceIdentity();
        assert(eventIdentity.status == ExecutionEventReadStatus::ServiceIdentity);
        assert(eventIdentity.serviceIdentity.serviceEpoch ==
            mutationIdentity.serviceEpoch);
        assert(eventIdentity.serviceIdentity.serviceFencingGeneration ==
            mutationIdentity.serviceFencingGeneration);
        assert(mutationIdentity.serviceEpoch == runtime.EventHub().StreamEpoch());
        assert(mutationIdentity.serviceFencingGeneration == 9);
        ExecutionReadCommand quoteRead;
        quoteRead.context = originalCommand.context;
        quoteRead.context.toolCallId = "paper-read-authoritative-quote";
        quoteRead.query = "market.get_quote";
        quoteRead.instrument = "EUR.USD";
        const ExecutionCommandResult quoted =
            client.ReadAuthoritativeState(quoteRead);
        assert(quoted.status == ExecutionCommandStatus::Accepted);
        assert(quoted.detail.find("\"source\":\"IB\"") != std::string::npos);
        assert(quoted.detail.find("\"authoritative\":true") != std::string::npos);
        assert(quoted.detail.find("\"subscription_state\":\"active\"") !=
            std::string::npos);
        assert(quoted.detail.find("\"stale\":false") != std::string::npos);
        quoteRead.context.toolCallId = "paper-read-unknown-quote";
        quoteRead.instrument = "GBP.USD";
        const ExecutionCommandResult unavailable =
            client.ReadAuthoritativeState(quoteRead);
        assert(unavailable.status == ExecutionCommandStatus::Rejected);
        assert(unavailable.reasonCode == "AUTHORITATIVE_QUOTE_UNAVAILABLE");
        assert(broker->marketDataRequests.load() == 1);
        persistedCommand = Previewed(client, originalCommand);
        const ExecutionCommandResult accepted =
            client.PlaceIbOrder(persistedCommand);
        assert(accepted.status == ExecutionCommandStatus::Accepted);
        assert(broker->sends == 1);

        IBEvent zeroQuantityFilled = Event(
            IBEventType::OrderStatus, accepted.orderId);
        zeroQuantityFilled.key = "Filled";
        zeroQuantityFilled.number = 0.0;
        zeroQuantityFilled.number2 = 0.0;
        zeroQuantityFilled.number3 = 0.0;
        {
            std::lock_guard<std::mutex> lock(broker->injectedMutex);
            broker->injectedEvents.push_back(zeroQuantityFilled);
        }
        const int terminalBarrierBefore =
            broker->quoteBarriersDequeued.load();
        InjectQuoteBarrier(broker);
        for (int attempt = 0; attempt < 1000 &&
             broker->quoteBarriersDequeued.load() == terminalBarrierBefore;
             ++attempt)
            std::this_thread::sleep_for(std::chrono::milliseconds(1));
        assert(broker->quoteBarriersDequeued.load() ==
               terminalBarrierBefore + 1);
        CancelOrderCommand cancelAfterZeroFill;
        cancelAfterZeroFill.context = originalCommand.context;
        cancelAfterZeroFill.context.toolCallId =
            "paper-cancel-after-zero-filled-status";
        cancelAfterZeroFill.orderId = accepted.orderId;
        cancelAfterZeroFill.instrument = originalCommand.instrument;
        cancelAfterZeroFill.side = originalCommand.order.action;
        const ExecutionCommandResult cancelledAfterZeroFill =
            client.CancelOrder(cancelAfterZeroFill);
        if (cancelledAfterZeroFill.status != ExecutionCommandStatus::Accepted)
            std::cerr << "cancel-after-zero-fill reason="
                      << cancelledAfterZeroFill.reasonCode << " detail="
                      << cancelledAfterZeroFill.detail << '\n';
        assert(cancelledAfterZeroFill.status ==
               ExecutionCommandStatus::Accepted);

        killSwitch->state = IbPaperKillSwitchState::Engaged;
        const ExecutionCommandResult duplicate =
            client.PlaceIbOrder(persistedCommand);
        assert(duplicate.status == ExecutionCommandStatus::Duplicate);
        IbPlaceOrderCommand changed = persistedCommand;
        changed.order.totalQuantity = 101.0;
        const ExecutionCommandResult conflict = client.PlaceIbOrder(changed);
        assert(conflict.reasonCode == "EXECUTION_PREVIEW_PERMIT_UNKNOWN_OR_CONSUMED");
        const ExecutionCommandResult killed = client.PreviewOrder(Place("paper-place-killed"));
        assert(killed.reasonCode == "IB_PAPER_KILL_SWITCH_ENGAGED");
        killSwitch->state = IbPaperKillSwitchState::Disarmed;

        const std::string secondSocket = SocketPath("hepta-ib-execution-second");
        const std::string secondEvent = SocketPath("hepta-ib-events-second");
        IbPaperExecutionRuntimeComposition competing(
            Config(ActivatedSocket(secondSocket), ActivatedSocket(secondEvent), state, credentials),
            std::unique_ptr<IIBApiWrapper>(new FakeIbWrapper(broker)),
            IbPaperExecutionRuntimeTestHooks(), killSwitch);
        assert(!competing.Start(reason));
        assert(reason == "IB_PAPER_STATE_LOCK_UNAVAILABLE");
        runtime.Stop();
        ::unlink(secondSocket.c_str());
        ::unlink(secondEvent.c_str());
    }

    ::unlink(socketPath.c_str());
    ::unlink(eventPath.c_str());
    {
        const std::shared_ptr<std::atomic<bool> > reconnectEntered(
            new std::atomic<bool>(false));
        const std::shared_ptr<std::atomic<bool> > releaseReconnect(
            new std::atomic<bool>(false));
        const std::shared_ptr<std::atomic<bool> > reconnectComplete(
            new std::atomic<bool>(false));
        IbPaperExecutionRuntimeTestHooks hooks;
        hooks.reconnectApiFactory = [broker]() {
            return std::unique_ptr<IIBApiWrapper>(new FakeIbWrapper(broker));
        };
        hooks.onStage = [reconnectEntered, releaseReconnect,
                         reconnectComplete](const char* stage) {
            if (std::strcmp(stage, "before_broker_reconnect_attempt") == 0) {
                reconnectEntered->store(true);
                while (!releaseReconnect->load())
                    std::this_thread::sleep_for(std::chrono::milliseconds(1));
            } else if (std::strcmp(stage, "broker_reconnect_complete") == 0) {
                reconnectComplete->store(true);
            }
        };
        IbPaperExecutionRuntimeComposition restarted(
            Config(ActivatedSocket(socketPath), ActivatedSocket(eventPath), state, credentials),
            std::unique_ptr<IIBApiWrapper>(new FakeIbWrapper(broker)),
            hooks, killSwitch);
        std::string reason;
        assert(restarted.Start(reason));
        const int connectAttemptsBeforeReconnect =
            broker->reconnectAttempts.load();
        UnixExecutionServiceClient client(socketPath, 1000);
        UnixExecutionEventFeedClient eventClient(eventPath, 1000);
        ExecutionServiceIdentity serviceIdentityBefore;
        std::string identityReason;
        assert(client.GetServiceIdentity(serviceIdentityBefore, identityReason));
        const int marketDataRequestsBefore = broker->marketDataRequests.load();
        IBEvent staleAfterClose = Event(
            IBEventType::TickPrice, broker->marketDataRequestId.load());
        staleAfterClose.key = "1";
        staleAfterClose.number = 9.0;
        {
            std::lock_guard<std::mutex> lock(broker->injectedMutex);
            broker->injectedEvents.push_back(staleAfterClose);
        }
        broker->emitConnectionClosed.store(true);
        for (int attempt = 0; attempt < 2000 &&
             !reconnectEntered->load(); ++attempt)
            std::this_thread::sleep_for(std::chrono::milliseconds(1));
        assert(reconnectEntered->load());
        assert(restarted.IsMutationBlocked(&reason));
        assert(reason == "IB_PAPER_BROKER_RECONNECT_PENDING");
        releaseReconnect->store(true);
        for (int attempt = 0; attempt < 5000 &&
             !reconnectComplete->load(); ++attempt)
            std::this_thread::sleep_for(std::chrono::milliseconds(1));
        if (!reconnectComplete->load()) {
            std::string reconnectFailure;
            restarted.HasFatalRuntimeError(&reconnectFailure);
            std::cerr << "reconnect did not complete: fatal="
                      << reconnectFailure << " attempts="
                      << broker->reconnectAttempts.load() << '\n';
        }
        assert(reconnectComplete->load());
        assert(!restarted.HasFatalRuntimeError(&reason));
        assert(restarted.IsRunning());
        assert(broker->reconnectAttempts.load() ==
            connectAttemptsBeforeReconnect + 1);
        // A reconnect opens one fresh formal quote subscription in the new
        // connection epoch, after the positive 2104 gate.
        assert(broker->marketDataRequests.load() ==
            marketDataRequestsBefore + 1);
        ExecutionServiceIdentity serviceIdentityAfter;
        assert(client.GetServiceIdentity(serviceIdentityAfter, identityReason));
        assert(serviceIdentityAfter.serviceEpoch ==
            serviceIdentityBefore.serviceEpoch);
        assert(serviceIdentityAfter.serviceFencingGeneration ==
            serviceIdentityBefore.serviceFencingGeneration);
        const ExecutionEventReadResult eventIdentity =
            eventClient.GetServiceIdentity();
        assert(eventIdentity.status == ExecutionEventReadStatus::ServiceIdentity);
        assert(eventIdentity.serviceIdentity.serviceEpoch ==
            serviceIdentityBefore.serviceEpoch);
        ExecutionReadCommand quoteRead;
        quoteRead.context = originalCommand.context;
        quoteRead.context.toolCallId = "paper-read-after-reconnect";
        quoteRead.query = "market.get_quote";
        quoteRead.instrument = "EUR.USD";
        const ExecutionCommandResult quote =
            client.ReadAuthoritativeState(quoteRead);
        assert(quote.status == ExecutionCommandStatus::Accepted);
        assert(quote.detail.find("\"stale\":false") != std::string::npos);
        assert(quote.detail.find("\"bid\":9") == std::string::npos);
        const int sendsBefore = broker->sends;
        const ExecutionCommandResult duplicate =
            client.PlaceIbOrder(persistedCommand);
        assert(duplicate.status == ExecutionCommandStatus::Duplicate);
        assert(broker->sends == sendsBefore);
        restarted.Stop();
    }

    ::unlink(socketPath.c_str());
    ::unlink(eventPath.c_str());
    {
        const std::shared_ptr<FakeBrokerState> failedBroker(
            new FakeBrokerState());
        IbPaperExecutionRuntimeTestHooks hooks;
        hooks.reconnectApiFactory = [failedBroker]() {
            return std::unique_ptr<IIBApiWrapper>(
                new FakeIbWrapper(failedBroker));
        };
        IbPaperExecutionRuntimeComposition restarted(
            Config(ActivatedSocket(socketPath), ActivatedSocket(eventPath),
                   state, credentials),
            std::unique_ptr<IIBApiWrapper>(new FakeIbWrapper(failedBroker)),
            hooks, killSwitch);
        std::string reason;
        assert(restarted.Start(reason));
        const int connectAttemptsBeforeReconnect =
            failedBroker->reconnectAttempts.load();
        failedBroker->reconnectFailuresRemaining.store(10000);
        failedBroker->emitConnectionClosed.store(true);
        for (int attempt = 0; attempt < 5000 &&
             !restarted.HasFatalRuntimeError(&reason); ++attempt)
            std::this_thread::sleep_for(std::chrono::milliseconds(1));
        assert(restarted.HasFatalRuntimeError(&reason));
        assert(reason == "IB_PAPER_BROKER_RECONNECT_EXHAUSTED");
        // Transport retry is bounded by the total deadline, not by a fixed
        // three-attempt cap.
        assert(failedBroker->reconnectAttempts.load() >=
            connectAttemptsBeforeReconnect + 4);
        assert(failedBroker->sends == 0);
        restarted.Stop();
    }

    ::unlink(socketPath.c_str());
    ::unlink(eventPath.c_str());
    {
        IbPaperExecutionRuntimeComposition restarted(
            Config(ActivatedSocket(socketPath), ActivatedSocket(eventPath), state, credentials),
            std::unique_ptr<IIBApiWrapper>(new FakeIbWrapper(broker)),
            IbPaperExecutionRuntimeTestHooks(), killSwitch);
        std::string reason;
        assert(restarted.Start(reason));
        UnixExecutionServiceClient client(socketPath, 1000);
        UnixExecutionEventFeedClient eventClient(eventPath, 1000);
        ExecutionServiceIdentity serviceIdentity;
        std::string identityReason;
        assert(client.GetServiceIdentity(serviceIdentity, identityReason));
        const int sendsBefore = broker->sends;
        broker->emitEventQueueOverflow.store(true);
        AssertFatalState(restarted, client, eventClient, serviceIdentity,
            socketPath, broker, "IB_PAPER_EVENT_STREAM_OVERFLOW",
            "paper-place-after-overflow-fatal");
        assert(broker->sends == sendsBefore);
        restarted.Stop();
    }

    ::unlink(socketPath.c_str());
    ::unlink(eventPath.c_str());
    ::unlink((state + "/oms-journal.jsonl").c_str());
    ::unlink((state + "/ib-paper-runtime.lock").c_str());
    ::unlink((state + "/ib-observability.jsonl").c_str());
    ::unlink(FxCashRestartCheckpointPath(state).c_str());
    ::unlink((credentials + "/hepta-execution-fence").c_str());
    ::unlink((credentials + "/hepta-ib-paper-authorization").c_str());
    assert(::rmdir(credentials.c_str()) == 0);
    assert(::rmdir(state.c_str()) == 0);
    return 0;
}
