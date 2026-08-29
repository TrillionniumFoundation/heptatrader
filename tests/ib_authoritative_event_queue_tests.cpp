#include "adapter_ib/ib_api_wrapper.h"
#include "adapter_ib/ib_gateway_adapter.h"
#include "adapter_ib/ib_venue_correlation.h"

#include <cassert>
#include <atomic>
#include <chrono>
#include <cmath>
#include <cstdio>
#include <cstdlib>
#include <deque>
#include <functional>
#include <iostream>
#include <memory>
#include <string>
#include <thread>

// The production marker deliberately exposes no debug lock API.  This peer
// is used only by the deterministic regression below to hold its tiny
// callback lock while Reset() is waiting, reproducing the exact lost-witness
// interleaving without relying on scheduler luck.
class IBCashFarmAdmissionMarkerTestAccess {
public:
    static void Lock(const IBCashFarmAdmissionMarker& marker) {
        marker.LockFromControl();
    }

    static void Unlock(const IBCashFarmAdmissionMarker& marker) {
        marker.Unlock();
    }

    static bool TryLockFromCallback(
            const IBCashFarmAdmissionMarker& marker) {
        return marker.TryLockFromCallback();
    }

    static std::uint64_t ResetGeneration(
            const IBCashFarmAdmissionMarker& marker) {
        return marker.m_resetGeneration.load(std::memory_order_acquire);
    }
};

namespace {

IBEvent MakeEvent(IBEventType type, long long id, const std::string& account);

void TestPositionsMultiRequestFenceRejectsDelayedOldEnd() {
    IBPositionsRequestFence fence(12001);
    int firstRequestId = 0;
    int supersededRequestId = 0;
    assert(fence.Begin(firstRequestId, supersededRequestId));
    assert(firstRequestId == 12001);
    assert(supersededRequestId == 0);
    assert(fence.IsCurrent(firstRequestId));

    int secondRequestId = 0;
    assert(fence.Begin(secondRequestId, supersededRequestId));
    assert(secondRequestId == 12002);
    assert(supersededRequestId == firstRequestId);

    // Models a delayed positionMultiEnd(firstRequestId) arriving after the
    // post-fill refresh has started. It must neither be accepted nor close the
    // second generation.
    assert(!fence.IsCurrent(firstRequestId));
    assert(!fence.Complete(firstRequestId));
    assert(fence.IsCurrent(secondRequestId));
    assert(fence.ActiveRequestId() == secondRequestId);

    assert(fence.Complete(secondRequestId));
    assert(fence.ActiveRequestId() == 0);
}

void TestMarketDataAdmissionStateKeepsFaultsAndGenerationsAtomic() {
    IBMarketDataAdmissionState state;
    std::uint64_t generation = 0;
    assert(state.Begin(generation));
    assert(generation == 1);
    IBMarketDataAdmissionState::Phase phase;
    std::uint64_t observedGeneration = 0;
    bool fault = false;
    state.Snapshot(phase, observedGeneration, fault);
    assert(phase == IBMarketDataAdmissionState::Open);
    assert(observedGeneration == generation);
    assert(!fault);

    // A callback that wins before reservation blocks the send atomically.
    IBMarketDataAdmissionState before;
    assert(before.ObserveBlockingCallback() ==
        IBMarketDataAdmissionState::CallbackBeforeReservation);
    assert(before.IsFailed());
    std::uint64_t rejectedGeneration = 0;
    assert(!before.Begin(rejectedGeneration));

    // A recoverable pre-admission farm warning may be observed while Idle,
    // but the same callback must block a generation that has opened before it
    // completes its no-op Idle validation.
    IBMarketDataAdmissionState farmWarning;
    assert(farmWarning.ObserveBlockingCallback(false) ==
        IBMarketDataAdmissionState::CallbackIgnored);
    std::uint64_t farmGeneration = 0;
    assert(farmWarning.Begin(farmGeneration));
    assert(farmWarning.ObserveBlockingCallback(false) ==
        IBMarketDataAdmissionState::CallbackBeforeReservation);
    assert(farmWarning.IsFailed());
    assert(farmWarning.Complete());
    assert(!farmWarning.Begin(rejectedGeneration));

    // A callback that wins after reservation makes exactly that generation
    // bad; EndSend cannot reopen it, and Complete retains the fault.
    assert(state.TryReserve(generation));
    assert(state.ObserveBlockingCallback() ==
        IBMarketDataAdmissionState::CallbackAfterReservation);
    assert(state.IsFailed());
    assert(state.EndSend(generation, true));
    assert(state.GetPhase() == IBMarketDataAdmissionState::Blocked);
    assert(state.Complete());
    assert(state.GetPhase() == IBMarketDataAdmissionState::Idle);
    assert(state.IsFailed());
    assert(!state.Begin(rejectedGeneration));

    // A stale cleanup token cannot settle a current generation.
    IBMarketDataAdmissionState stale;
    std::uint64_t current = 0;
    assert(stale.Begin(current));
    assert(!stale.EndSend(current + 1, false));
    assert(stale.GetPhase() == IBMarketDataAdmissionState::Open);
    assert(stale.TryReserve(current));
    assert(stale.EndSend(current, false));
    assert(stale.Complete());
}

void TestCashFarmMarkerKeepsEpochAndSequenceOrdering() {
    IBCashFarmAdmissionMarker marker;
    marker.Reset(7);
    // A fresh connection is not request-ready merely because no warning has
    // arrived; a positive same-epoch CASH 2104 is required first.
    assert(!marker.IsReady(7));

    // A warning opens the pending interval. A ready callback from an older
    // epoch, or one ordered before that warning, cannot close it.
    marker.ObserveWarning(7, 20);
    assert(marker.IsPending(7));
    marker.ObserveReady(6, 99);
    assert(marker.IsPending(7));
    marker.ObserveReady(7, 19);
    assert(marker.IsPending(7));

    // Only a same-epoch ready at or after the warning sequence closes it.
    marker.ObserveReady(7, 20);
    assert(!marker.IsPending(7));
    assert(marker.IsReady(7));
    marker.ObserveWarning(7, 40);
    marker.ObserveReady(7, 39);
    assert(marker.IsPending(7));
    assert(!marker.IsReady(7));
    marker.ObserveReady(7, 41);
    assert(!marker.IsPending(7));
    assert(marker.IsReady(7));

    // Sequence order, rather than callback execution order, is authoritative:
    // a later ready observed first still closes an older warning.
    marker.Reset(8);
    assert(!marker.IsReady(8));
    marker.ObserveReady(8, 12);
    marker.ObserveWarning(8, 11);
    assert(!marker.IsPending(8));
    assert(marker.IsReady(8));
    marker.ObserveWarning(8, 15);
    assert(marker.IsPending(8));
    assert(!marker.IsReady(8));
    marker.ObserveReady(8, 14);
    assert(marker.IsPending(8));
    marker.ObserveReady(8, 16);
    assert(!marker.IsPending(8));
    assert(marker.IsReady(8));

    // A future/mismatched epoch is unsafe until an explicit reset; an old
    // ready callback must never clear the new epoch's pending warning.
    marker.ObserveWarning(9, 22);
    assert(marker.IsPending(8));
    assert(!marker.IsReady(8));
    marker.Reset(9);
    marker.ObserveWarning(9, 30);
    marker.ObserveReady(8, 1000);
    assert(marker.IsPending(9));
    assert(!marker.IsReady(9));
    marker.ObserveReady(9, 31);
    assert(!marker.IsPending(9));
    assert(marker.IsReady(9));
    marker.MarkUnsafe();
    assert(!marker.IsReady(9));
}

void TestCashFarmMarkerResetRetainsConcurrentCallbackContention() {
    // Repeat the forced interleaving so this remains a stable regression
    // rather than a scheduler-dependent one-shot check.  The control reset
    // snapshots its fault serial before publishing the odd generation, so a
    // callback that observes that generation cannot be mistaken for baseline
    // state and erased by the reset.
    constexpr int kAttempts = 32;
    for (int attempt = 0; attempt < kAttempts; ++attempt) {
        IBCashFarmAdmissionMarker marker;
        marker.Reset(1);

        // Hold the marker lock so the control reset has to wait.  Once Reset
        // has announced its odd generation, model the callback's try-only
        // lock miss.
        IBCashFarmAdmissionMarkerTestAccess::Lock(marker);
        std::atomic<bool> resetReturned(false);
        std::thread resetter([&]() {
            marker.Reset(2);
            resetReturned.store(true, std::memory_order_release);
        });
        while ((IBCashFarmAdmissionMarkerTestAccess::ResetGeneration(marker) &
                1ULL) == 0ULL) {
            std::this_thread::yield();
        }
        assert(!IBCashFarmAdmissionMarkerTestAccess::TryLockFromCallback(
            marker));
        IBCashFarmAdmissionMarkerTestAccess::Unlock(marker);
        resetter.join();
        assert(resetReturned.load(std::memory_order_acquire));
        assert(marker.IsPending(2));
    }

    // The witness is sticky for the whole new epoch; only a subsequent
    // explicit reset is allowed to clear it.
    IBCashFarmAdmissionMarker marker;
    marker.Reset(1);
    IBCashFarmAdmissionMarkerTestAccess::Lock(marker);
    std::atomic<bool> resetReturned(false);
    std::thread resetter([&]() {
        marker.Reset(2);
        resetReturned.store(true, std::memory_order_release);
    });
    while ((IBCashFarmAdmissionMarkerTestAccess::ResetGeneration(marker) &
            1ULL) == 0ULL) {
        std::this_thread::yield();
    }
    assert(!IBCashFarmAdmissionMarkerTestAccess::TryLockFromCallback(marker));
    IBCashFarmAdmissionMarkerTestAccess::Unlock(marker);
    resetter.join();
    assert(resetReturned.load(std::memory_order_acquire));
    assert(marker.IsPending(2));
    marker.ObserveReady(2, 100);
    assert(marker.IsPending(2));
    marker.Reset(3);
    assert(!marker.IsPending(3));
}

void TestEventQueueTryPushAndExternalDropAreExplicit() {
    IBAuthoritativeEventQueue queue(2);
    IBEvent first = MakeEvent(IBEventType::TickPrice, 1, "");
    first.connectionEpoch = 9;
    bool overflowed = false;
    assert(queue.TryPush(first, overflowed));
    assert(!overflowed);
    assert(queue.TryPush(MakeEvent(IBEventType::TickPrice, 2, ""),
                         overflowed));
    assert(!overflowed);
    assert(queue.TryPush(MakeEvent(IBEventType::TickPrice, 3, ""),
                         overflowed));
    assert(overflowed);
    IBEvent out;
    assert(queue.TryDequeueEvent(out));
    assert(out.type == IBEventType::EventQueueOverflow);
    assert(out.droppedEventCount == 1);
    queue.RecordDroppedEvent(9, 2);
    assert(queue.TryDequeueEvent(out));
    assert(out.type == IBEventType::EventQueueOverflow);
    assert(out.droppedEventCount == 3);
    assert(out.overflowGeneration == 2);

    // A delayed callback from an older transport epoch must not roll the
    // overflow witness back to that stale epoch.
    IBAuthoritativeEventQueue epochs(1);
    IBEvent current = MakeEvent(IBEventType::TickPrice, 10, "");
    current.connectionEpoch = 9;
    epochs.Push(current);
    IBEvent stale = MakeEvent(IBEventType::TickPrice, 11, "");
    stale.connectionEpoch = 3;
    epochs.Push(stale);
    assert(epochs.TryDequeueEvent(out));
    assert(out.type == IBEventType::EventQueueOverflow);
    assert(out.connectionEpoch == 9);
}

IBEvent MakeEvent(IBEventType type, long long id, const std::string& account) {
    IBEvent event;
    event.type = type;
    event.id = id;
    event.account = account;
    return event;
}

class LegacyMockWrapper final : public IIBApiWrapper {
public:
    bool Connect(const IBConnectParams&) override { return false; }
    void Disconnect() override {}
    bool IsConnected() const override { return false; }
    const char* GetStatusString() const override { return "LEGACY_MOCK"; }
    bool ReqAccountSummary() override { return false; }
    bool ReqPositions() override { return false; }
    bool ReqMktData(int, const IBContractLite&) override { return false; }
    bool CancelMktData(int) override { return false; }
    bool PlaceOrder(long, const IBContractLite&, const IBOrderLite&) override { return false; }
    bool CancelOrder(long) override { return false; }
    bool PollOnce(int) override { return false; }
    bool TryDequeueEvent(IBEvent&) override { return false; }
    long GetLastValidOrderId() const override { return -1; }
};

class FakeAuthorityWrapper final : public IIBApiWrapper {
public:
    void SetConnectionEpoch(std::uint64_t value) override { connectionEpoch = value; }
    std::uint64_t GetConnectionEpoch() const override { return connectionEpoch; }
    bool Connect(const IBConnectParams&) override { return connected; }
    void Disconnect() override { connected = false; }
    bool IsConnected() const override { return connected; }
    const char* GetStatusString() const override { return "FAKE_CONNECTED"; }
    bool ReqAccountSummary() override {
        ++accountSummaryRequests;
        return connected && acceptAccountSummaryRequests;
    }
    bool ReqPositions() override {
        ++positionRequests;
        return connected && acceptPositionRequests;
    }
    bool ReqOpenOrders() override {
        ++openOrderRequests;
        return connected;
    }
    bool ReqAllOpenOrders() override {
        ++allOpenOrderRequests;
        return connected;
    }
    bool ReqCompletedOrders() override {
        ++completedOrderRequests;
        return connected && acceptTerminalRequests;
    }
    bool ReqExecutions(int requestId) override {
        ++executionRequests;
        lastExecutionRequestId = requestId;
        return connected && acceptTerminalRequests;
    }
    bool ReqMktData(int, const IBContractLite&) override { return connected; }
    bool CancelMktData(int) override { return connected; }
    bool PlaceOrder(long orderId, const IBContractLite& contract,
                    const IBOrderLite& order) override {
        ++placeOrderRequests;
        lastOrderId = orderId;
        lastContract = contract;
        lastOrder = order;
        return connected;
    }
    bool CancelOrder(long orderId) override {
        ++cancelOrderRequests;
        lastCancelledOrderId = orderId;
        return connected;
    }
    bool PollOnce(int) override { return connected; }
    bool TryDequeueEvent(IBEvent& event) override {
        if (events.empty()) return false;
        event = events.front();
        events.pop_front();
        return true;
    }
    long GetLastValidOrderId() const override { return 100; }

    bool connected = true;
    int openOrderRequests = 0;
    int allOpenOrderRequests = 0;
    int completedOrderRequests = 0;
    int executionRequests = 0;
    int accountSummaryRequests = 0;
    int positionRequests = 0;
    int placeOrderRequests = 0;
    int cancelOrderRequests = 0;
    bool acceptAccountSummaryRequests = true;
    bool acceptPositionRequests = true;
    bool acceptTerminalRequests = true;
    int lastExecutionRequestId = 0;
    long lastCancelledOrderId = -1;
    long lastOrderId = -1;
    IBContractLite lastContract;
    IBOrderLite lastOrder;
    std::uint64_t connectionEpoch = 0;
    std::deque<IBEvent> events;
};

void TestDistinctAuthorityEventsPreserveAccountAndPayload() {
    IBAuthoritativeEventQueue queue(8);

    IBEvent portfolio = MakeEvent(IBEventType::PortfolioUpdate, 1, "DU111");
    portfolio.key = "EUR.USD";
    portfolio.number = 2.0;
    portfolio.contract.symbol = "EUR";
    portfolio.contract.currency = "USD";
    queue.Push(portfolio);

    IBEvent snapshot = MakeEvent(IBEventType::PositionSnapshotItem, 2, "DU222");
    snapshot.key = "USD.JPY";
    snapshot.number = -3.0;
    queue.Push(snapshot);

    IBEvent openOrder = MakeEvent(IBEventType::OpenOrder, 77, "DU333");
    openOrder.contract.symbol = "SPY";
    openOrder.order.action = "BUY";
    openOrder.order.totalQuantity = 5.0;
    openOrder.value = "Submitted";
    queue.Push(openOrder);

    IBEvent out;
    assert(queue.TryDequeueEvent(out));
    assert(out.type == IBEventType::PortfolioUpdate);
    assert(out.account == "DU111");
    assert(out.contract.symbol == "EUR");

    assert(queue.TryDequeueEvent(out));
    assert(out.type == IBEventType::PositionSnapshotItem);
    assert(out.account == "DU222");

    assert(queue.TryDequeueEvent(out));
    assert(out.type == IBEventType::OpenOrder);
    assert(out.account == "DU333");
    assert(out.id == 77);
    assert(out.order.action == "BUY");
    assert(out.order.totalQuantity == 5.0);
    assert(!queue.TryDequeueEvent(out));
}

void TestOverflowIsReportedBeforeRemainingEvents() {
    IBAuthoritativeEventQueue queue(2);
    IBEvent first = MakeEvent(IBEventType::TickPrice, 1, "");
    first.connectionEpoch = 7;
    queue.Push(first);
    queue.Push(MakeEvent(IBEventType::TickPrice, 2, ""));
    queue.Push(MakeEvent(IBEventType::TickPrice, 3, ""));

    IBEvent out;
    assert(queue.TryDequeueEvent(out));
    assert(out.type == IBEventType::EventQueueOverflow);
    assert(out.connectionEpoch == 7);
    assert(out.value == "AUTHORITATIVE_STATE_INVALID_REQUIRES_RESYNC");
    assert(out.overflowGeneration == 1);
    assert(out.droppedEventCount == 1);

    assert(queue.TryDequeueEvent(out));
    assert(out.id == 2);
    assert(queue.TryDequeueEvent(out));
    assert(out.id == 3);
    assert(!queue.TryDequeueEvent(out));

    queue.Push(MakeEvent(IBEventType::TickPrice, 4, ""));
    queue.Push(MakeEvent(IBEventType::TickPrice, 5, ""));
    queue.Push(MakeEvent(IBEventType::TickPrice, 6, ""));
    assert(queue.TryDequeueEvent(out));
    assert(out.type == IBEventType::EventQueueOverflow);
    assert(out.overflowGeneration == 2);
    assert(out.droppedEventCount == 2);
    assert(queue.OverflowGeneration() == 2);
    assert(queue.DroppedEventCount() == 2);
}

void TestAdapterDropsStaleConnectionEpochEvents() {
    std::unique_ptr<FakeAuthorityWrapper> fake(new FakeAuthorityWrapper());
    FakeAuthorityWrapper* fakeRaw = fake.get();
    fake->connectionEpoch = 2;
    IBEvent stale = MakeEvent(IBEventType::ConnectionClosed, 0, "");
    stale.connectionEpoch = 1;
    fake->events.push_back(stale);
    IBEvent current = MakeEvent(IBEventType::NextValidId, 100, "");
    current.connectionEpoch = 2;
    fake->events.push_back(current);

    HeptaIBGatewayAdapter adapter(std::move(fake));
    IBEvent out;
    assert(adapter.TryDequeueEvent(out));
    assert(out.type == IBEventType::NextValidId);
    assert(out.connectionEpoch == 2);
    assert(fakeRaw->events.empty());
    assert(adapter.GetConnectionEpoch() == 2);
}

void TestLegacyMocksAndAdapterFailClosedLatch() {
    LegacyMockWrapper legacy;
    assert(!legacy.ReqOpenOrders());
    assert(!legacy.ReqAllOpenOrders());
    assert(!legacy.ReqCompletedOrders());
    assert(!legacy.ReqExecutions(1));

    std::unique_ptr<FakeAuthorityWrapper> fake(new FakeAuthorityWrapper());
    FakeAuthorityWrapper* fakeRaw = fake.get();
    IBEvent overflow = MakeEvent(IBEventType::EventQueueOverflow, 9, "");
    overflow.overflowGeneration = 9;
    overflow.droppedEventCount = 23;
    overflow.value = "AUTHORITATIVE_STATE_INVALID_REQUIRES_RESYNC";
    fake->events.push_back(overflow);

    HeptaIBGatewayAdapter adapter(std::move(fake));
    assert(adapter.ReqOpenOrders());
    assert(fakeRaw->openOrderRequests == 1);

    IBEvent out;
    assert(adapter.TryDequeueEvent(out));
    assert(!adapter.IsEventStreamAuthoritative());
    assert(adapter.GetLastEventOverflowGeneration() == 9);

    std::string reasonCode;
    std::string detail;
    assert(!adapter.RunPreflightChecksDetailed(reasonCode, detail));
    assert(reasonCode == "RISK_IB_EVENT_STREAM_NOT_AUTHORITATIVE");
    assert(!adapter.MarkAuthoritativeResyncComplete(8));
    assert(adapter.MarkAuthoritativeResyncComplete(9));
    assert(adapter.IsEventStreamAuthoritative());

    IBEvent newerOverflow = MakeEvent(IBEventType::EventQueueOverflow, 10, "");
    newerOverflow.overflowGeneration = 10;
    newerOverflow.droppedEventCount = 24;
    newerOverflow.value = "AUTHORITATIVE_STATE_INVALID_REQUIRES_RESYNC";
    fakeRaw->events.push_back(newerOverflow);
    assert(adapter.TryDequeueEvent(out));
    assert(!adapter.IsEventStreamAuthoritative());
    assert(!adapter.MarkAuthoritativeResyncComplete(9));
    assert(adapter.MarkAuthoritativeResyncComplete(10));
    assert(adapter.IsEventStreamAuthoritative());
}

std::string Correlation(char digit) {
    return std::string("hepta-v1-sha256:") + std::string(64, digit);
}

void DrainOne(HeptaIBGatewayAdapter& adapter, IBEventType expectedType) {
    IBEvent out;
    assert(adapter.TryDequeueEvent(out));
    assert(out.type == expectedType);
}

void TestCorrelationCodecIsCanonicalAndReversible() {
    std::string orderRef;
    std::string reason;
    assert(IbVenueCorrelationCodec::EncodeOrderRef(
        Correlation('a'), orderRef, reason));
    assert(orderRef.size() == 45);
    assert(orderRef == "H1qqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqo");

    std::string decoded;
    assert(IbVenueCorrelationCodec::DecodeOrderRef(orderRef, decoded, reason));
    assert(decoded == Correlation('a'));
    assert(!IbVenueCorrelationCodec::EncodeOrderRef(
        std::string("hepta-v1-sha256:") + std::string(64, 'A'), orderRef, reason));
    assert(reason == "IB_CORRELATION_FORMAT_INVALID");

    std::string malformed = "H1" + std::string(43, 'A');
    malformed[44] = 'B';
    assert(!IbVenueCorrelationCodec::DecodeOrderRef(malformed, decoded, reason));
    assert(reason == "IB_ORDER_REF_CORRELATION_INVALID");
}

HeptaIBConfig TestTradingConfig() {
    HeptaIBConfig cfg;
    cfg.account = "DU123";
    cfg.readOnly = false;
    cfg.risk.enableOrderSubmission = true;
    cfg.risk.maxOrderQuantity = 2000.0;
    cfg.risk.maxDailyOrders = 10;
    cfg.risk.maxPriceDeviationBps = 1000.0;
    cfg.risk.requireNextValidId = true;
    cfg.risk.allowLiveTrading = false;
    cfg.risk.liveKillSwitch = true;
    return cfg;
}

HeptaIBConfig TestFxCashTradingConfig(double baselineCashBalance) {
    HeptaIBConfig cfg = TestTradingConfig();
    InstrumentRef contract;
    contract.symbol = "EUR";
    contract.secType = "CASH";
    contract.exchange = "IDEALPRO";
    contract.currency = "USD";
    cfg.authoritativeCashFxContracts["EUR.USD"] = contract;
    cfg.authoritativeCashFxBaselines["EUR.USD"] = baselineCashBalance;
    cfg.authoritativeCashFxStartupObservedBalances["EUR.USD"] =
        baselineCashBalance;
    return cfg;
}

void PushConnected(
    FakeAuthorityWrapper* fake,
    HeptaIBGatewayAdapter& adapter);

void CompleteFxCashRiskRefresh(
    FakeAuthorityWrapper* fake,
    HeptaIBGatewayAdapter& adapter,
    const std::string& cashBalance,
    bool includeCashBalance = true,
    const std::string& conflictingCashBalance = std::string()) {
    assert(adapter.ReqAccountSummary());
    assert(adapter.ReqPositions());
    IBEvent net = MakeEvent(IBEventType::AccountValue, 9001, "DU123");
    net.key = "NetLiquidation:USD";
    net.value = "1000000";
    fake->events.push_back(net);
    IBEvent ready = MakeEvent(IBEventType::AccountValue, 9002, "DU123");
    ready.key = "AccountReady:BASE";
    ready.value = "true";
    fake->events.push_back(ready);
    if (includeCashBalance) {
        IBEvent cash = MakeEvent(
            IBEventType::AccountValue, 9002, "DU123");
        cash.key = "CashBalance:EUR";
        cash.value = cashBalance;
        fake->events.push_back(cash);
        if (!conflictingCashBalance.empty()) {
            cash.value = conflictingCashBalance;
            fake->events.push_back(cash);
        }
    }
    fake->events.push_back(MakeEvent(
        IBEventType::AccountSummaryEnd, 9001, "DU123"));
    fake->events.push_back(MakeEvent(
        IBEventType::PositionEnd, 12001, "DU123"));
    DrainOne(adapter, IBEventType::AccountValue);
    DrainOne(adapter, IBEventType::AccountValue);
    if (includeCashBalance) {
        DrainOne(adapter, IBEventType::AccountValue);
        if (!conflictingCashBalance.empty())
            DrainOne(adapter, IBEventType::AccountValue);
    }
    DrainOne(adapter, IBEventType::AccountSummaryEnd);
    DrainOne(adapter, IBEventType::PositionEnd);
}

void TestFxCashBaselineDeltaIsAuthoritativeCampaignPosition() {
    constexpr double baseline = -1271411.16;
    std::unique_ptr<FakeAuthorityWrapper> fake(new FakeAuthorityWrapper());
    FakeAuthorityWrapper* fakeRaw = fake.get();
    fakeRaw->connectionEpoch = 1;
    HeptaIBGatewayAdapter adapter(std::move(fake));
    assert(adapter.Init(TestFxCashTradingConfig(baseline)));
    PushConnected(fakeRaw, adapter);

    CompleteFxCashRiskRefresh(fakeRaw, adapter, "-1271411.16");
    IBAuthoritativeRiskSnapshot snapshot =
        adapter.GetAuthoritativeRiskSnapshot();
    assert(snapshot.accountComplete);
    assert(snapshot.positionsComplete);
    assert(snapshot.fxCashComplete);
    assert(snapshot.grossAbsolutePosition == 0.0);
    assert(adapter.GetAuthoritativePositionQuantities().empty());
    const std::map<std::string, IBAuthoritativeFxCashExposure> flatExposure =
        adapter.GetAuthoritativeFxCashExposures();
    assert(flatExposure.size() == 1);
    assert(std::fabs(flatExposure.at("EUR.USD").campaignOwnedQuantity) < 1e-6);
    const std::uint64_t flatPositionGeneration = snapshot.positionsGeneration;
    const std::uint64_t flatFxGeneration = snapshot.fxCashGeneration;

    CompleteFxCashRiskRefresh(fakeRaw, adapter, "-1496411.16");
    snapshot = adapter.GetAuthoritativeRiskSnapshot();
    assert(snapshot.accountComplete);
    assert(snapshot.positionsComplete);
    assert(snapshot.fxCashComplete);
    assert(snapshot.positionsGeneration > flatPositionGeneration);
    assert(snapshot.fxCashGeneration > flatFxGeneration);
    assert(std::fabs(snapshot.grossAbsolutePosition - 225000.0) < 1e-6);
    const std::map<std::string, double> positions =
        adapter.GetAuthoritativePositionQuantities();
    assert(positions.size() == 1);
    assert(std::fabs(positions.at("EUR.USD") + 225000.0) < 1e-6);

    // An asynchronous balance change is proof that the committed generation
    // is stale, not permission to mutate the campaign position in place.
    IBEvent delayed = MakeEvent(
        IBEventType::AccountValue, 9002, "DU123");
    delayed.key = "CashBalance:EUR";
    delayed.value = "-1471411.16";
    fakeRaw->events.push_back(delayed);
    DrainOne(adapter, IBEventType::AccountValue);
    snapshot = adapter.GetAuthoritativeRiskSnapshot();
    assert(!snapshot.accountComplete);
    assert(!snapshot.positionsComplete);
    assert(!snapshot.fxCashComplete);
    assert(snapshot.reasonCode ==
           "IB_FX_CASH_BALANCE_OUT_OF_GENERATION_CHANGE");

    std::unique_ptr<FakeAuthorityWrapper> monitorFake(
        new FakeAuthorityWrapper());
    FakeAuthorityWrapper* monitorRaw = monitorFake.get();
    monitorRaw->connectionEpoch = 1;
    HeptaIBGatewayAdapter monitorAdapter(std::move(monitorFake));
    assert(monitorAdapter.Init(TestFxCashTradingConfig(baseline)));
    PushConnected(monitorRaw, monitorAdapter);
    CompleteFxCashRiskRefresh(
        monitorRaw, monitorAdapter, "-1271411.16");
    IBEvent unchangedMonitor = MakeEvent(
        IBEventType::PositionMonitorUpdate, 12001, "DU123");
    unchangedMonitor.key = "SPY";
    unchangedMonitor.number = 0.0;
    unchangedMonitor.contract.symbol = "SPY";
    unchangedMonitor.contract.secType = "STK";
    unchangedMonitor.contract.currency = "USD";
    monitorRaw->events.push_back(unchangedMonitor);
    DrainOne(monitorAdapter, IBEventType::PositionMonitorUpdate);
    assert(monitorAdapter.GetAuthoritativeRiskSnapshot().positionsComplete);
    unchangedMonitor.number = 1.0;
    monitorRaw->events.push_back(unchangedMonitor);
    DrainOne(monitorAdapter, IBEventType::PositionMonitorUpdate);
    snapshot = monitorAdapter.GetAuthoritativeRiskSnapshot();
    assert(!snapshot.accountComplete);
    assert(!snapshot.positionsComplete);
    assert(snapshot.reasonCode ==
           "IB_POSITION_OUT_OF_GENERATION_CHANGE");

    std::unique_ptr<FakeAuthorityWrapper> conflictFake(
        new FakeAuthorityWrapper());
    FakeAuthorityWrapper* conflictRaw = conflictFake.get();
    conflictRaw->connectionEpoch = 1;
    HeptaIBGatewayAdapter conflictAdapter(std::move(conflictFake));
    assert(conflictAdapter.Init(TestFxCashTradingConfig(baseline)));
    PushConnected(conflictRaw, conflictAdapter);
    CompleteFxCashRiskRefresh(
        conflictRaw, conflictAdapter, "-1271411.16", true,
        "-1271410.16");
    snapshot = conflictAdapter.GetAuthoritativeRiskSnapshot();
    assert(!snapshot.accountComplete);
    assert(!snapshot.fxCashComplete);
    assert(snapshot.reasonCode == "IB_FX_CASH_BALANCE_INVALID");

    std::unique_ptr<FakeAuthorityWrapper> missingFake(
        new FakeAuthorityWrapper());
    FakeAuthorityWrapper* missingRaw = missingFake.get();
    missingRaw->connectionEpoch = 1;
    HeptaIBGatewayAdapter missingAdapter(std::move(missingFake));
    assert(missingAdapter.Init(TestFxCashTradingConfig(baseline)));
    PushConnected(missingRaw, missingAdapter);
    CompleteFxCashRiskRefresh(
        missingRaw, missingAdapter, "", false);
    snapshot = missingAdapter.GetAuthoritativeRiskSnapshot();
    assert(!snapshot.accountComplete);
    assert(!snapshot.fxCashComplete);
    assert(snapshot.reasonCode == "IB_FX_CASH_BALANCE_MISSING");
}

void TestBoundCashFxContractMustMatchConfiguredContract() {
    std::unique_ptr<FakeAuthorityWrapper> fake(new FakeAuthorityWrapper());
    FakeAuthorityWrapper* fakeRaw = fake.get();
    fakeRaw->connectionEpoch = 1;
    HeptaIBConfig config = TestFxCashTradingConfig(0.0);
    const IBContractLite expected =
        config.authoritativeCashFxContracts.at("EUR.USD");
    HeptaIBGatewayAdapter adapter(std::move(fake));
    assert(adapter.Init(config));
    PushConnected(fakeRaw, adapter);
    adapter.UpdateReferencePrice(1.1);
    CompleteFxCashRiskRefresh(fakeRaw, adapter, "0");

    IBOrderLite order;
    order.action = "BUY";
    order.orderType = "LMT";
    order.totalQuantity = 1.0;
    order.lmtPrice = 1.1;
    IBFinalOrderSendContext context;
    context.authoritativeQuoteBound = true;
    context.instrument = "EUR.USD";
    context.quoteSubscriptionId = "IB:1:1:1001";
    context.quoteBid = 1.0999;
    context.quoteAsk = 1.1001;
    context.quoteObservedAtMs = 100000;
    context.quoteStaleAfterMs = 105000;
    int finalChecks = 0;
    adapter.SetPrePlaceOrderSendCheck(
        [&](const IBFinalOrderSendContext*, const IBContractLite&,
            const IBOrderLite&, std::string*) {
            ++finalChecks;
            return true;
        });
    long orderId = -1;
    const auto expectMismatch = [&](const IBContractLite& actual, char seed) {
        assert(!adapter.PlaceOrderCorrelated(
            actual, order, Correlation(seed), &orderId, &context));
        assert(adapter.GetLastRejectReason() ==
               "IB_PAPER_PLACE_CONTRACT_MISMATCH");
        assert(fakeRaw->placeOrderRequests == 0);
        assert(finalChecks == 0);
    };

    IBContractLite wrongPair = expected;
    wrongPair.symbol = "GBP";
    expectMismatch(wrongPair, '1');
    IBContractLite wrongExchange = expected;
    wrongExchange.exchange = "SMART";
    expectMismatch(wrongExchange, '2');
    IBContractLite wrongPrimaryExchange = expected;
    wrongPrimaryExchange.primaryExchange = "ARCA";
    expectMismatch(wrongPrimaryExchange, '3');
    IBContractLite wrongSecurityType = expected;
    wrongSecurityType.secType = "STK";
    expectMismatch(wrongSecurityType, '4');
    IBContractLite wrongCurrency = expected;
    wrongCurrency.currency = "CHF";
    expectMismatch(wrongCurrency, '5');
    IBContractLite wrongExpiry = expected;
    wrongExpiry.lastTradeDateOrContractMonth = "20991231";
    expectMismatch(wrongExpiry, '6');
    IBContractLite wrongRight = expected;
    wrongRight.right = "C";
    expectMismatch(wrongRight, '7');
    IBContractLite wrongStrike = expected;
    wrongStrike.strike = -0.0;
    expectMismatch(wrongStrike, '8');
    IBContractLite wrongMultiplier = expected;
    wrongMultiplier.multiplier = "1";
    expectMismatch(wrongMultiplier, '9');
    IBContractLite wrongTradingClass = expected;
    wrongTradingClass.tradingClass = "EUR.USD";
    expectMismatch(wrongTradingClass, 'a');
    IBContractLite wrongLocalSymbol = expected;
    wrongLocalSymbol.localSymbol = "EUR.USD";
    expectMismatch(wrongLocalSymbol, 'b');

    assert(adapter.PlaceOrderCorrelated(
        expected, order, Correlation('c'), &orderId, &context));
    assert(fakeRaw->placeOrderRequests == 1);
    assert(finalChecks == 1);
}

void PushConnected(FakeAuthorityWrapper* fake, HeptaIBGatewayAdapter& adapter) {
    IBEvent connected = MakeEvent(IBEventType::NextValidId, 100, "DU123");
    connected.connectionEpoch = fake->connectionEpoch;
    fake->events.push_back(connected);
    DrainOne(adapter, IBEventType::NextValidId);
}

void CompleteCoherentRiskRefresh(
    FakeAuthorityWrapper* fake, HeptaIBGatewayAdapter& adapter,
    double positionQuantity = 0.0,
    bool recoveryBarrier = false) {
    assert(recoveryBarrier ? adapter.ReqRecoveryAuditRiskRefresh() :
        adapter.ReqRiskRefresh());
    IBEvent account = MakeEvent(
        IBEventType::AccountValue, 9001, "DU123");
    account.key = "NetLiquidation:USD";
    account.value = "100000";
    fake->events.push_back(account);
    fake->events.push_back(MakeEvent(
        IBEventType::AccountSummaryEnd, 9001, "DU123"));
    if (positionQuantity != 0.0) {
        IBEvent position = MakeEvent(
            IBEventType::PositionSnapshotItem, 0, "DU123");
        position.key = "EUR.USD";
        position.number = positionQuantity;
        fake->events.push_back(position);
    }
    fake->events.push_back(MakeEvent(
        IBEventType::PositionEnd, 12001, "DU123"));
    DrainOne(adapter, IBEventType::AccountValue);
    DrainOne(adapter, IBEventType::AccountSummaryEnd);
    if (positionQuantity != 0.0)
        DrainOne(adapter, IBEventType::PositionSnapshotItem);
    DrainOne(adapter, IBEventType::PositionEnd);
    const IBAuthoritativeRiskSnapshot risk =
        adapter.GetAuthoritativeRiskSnapshot();
    assert(risk.coherentRefreshComplete);
}

void TestCorrelatedSendAndAuthoritativeReadback() {
    std::unique_ptr<FakeAuthorityWrapper> fake(new FakeAuthorityWrapper());
    FakeAuthorityWrapper* fakeRaw = fake.get();
    HeptaIBGatewayAdapter adapter(std::move(fake));
    assert(adapter.Init(TestTradingConfig()));
    PushConnected(fakeRaw, adapter);
    adapter.UpdateReferencePrice(1.1);

    IBContractLite contract;
    contract.symbol = "EUR";
    contract.secType = "CASH";
    contract.exchange = "IDEALPRO";
    contract.currency = "USD";
    IBOrderLite order;
    order.action = "BUY";
    order.orderType = "LMT";
    order.totalQuantity = 1000.0;
    order.lmtPrice = 1.1;

    const std::string correlation = Correlation('b');
    long orderId = -1;
    assert(adapter.PlaceOrderCorrelated(contract, order, correlation, &orderId));
    assert(fakeRaw->placeOrderRequests == 1);
    assert(orderId == fakeRaw->lastOrderId);
    assert(fakeRaw->lastOrder.orderRef.size() == 45);
    std::string decoded;
    std::string reason;
    assert(IbVenueCorrelationCodec::DecodeOrderRef(
        fakeRaw->lastOrder.orderRef, decoded, reason));
    assert(decoded == correlation);

    IBOrderLite forged = order;
    forged.orderRef = fakeRaw->lastOrder.orderRef;
    assert(!adapter.PlaceOrder(contract, forged, nullptr));
    assert(adapter.GetLastRejectReason() == "IB_ORDER_REF_RESERVED");
    assert(fakeRaw->placeOrderRequests == 1);

    assert(adapter.ReqOpenOrders());
    IBEvent openOrder = MakeEvent(IBEventType::OpenOrder, orderId, "DU123");
    openOrder.order = fakeRaw->lastOrder;
    fakeRaw->events.push_back(openOrder);
    fakeRaw->events.push_back(MakeEvent(IBEventType::OpenOrderEnd, 0, "DU123"));
    DrainOne(adapter, IBEventType::OpenOrder);
    DrainOne(adapter, IBEventType::OpenOrderEnd);

    IBAuthoritativeCorrelationSnapshot snapshot =
        adapter.GetAuthoritativeCorrelationSnapshot();
    assert(snapshot.complete);
    assert(snapshot.reasonCode.empty());
    assert(snapshot.activeOrderIdsByCorrelation.size() == 1);
    assert(snapshot.activeOrderIdsByCorrelation.at(correlation) == orderId);

    IBEvent filled = MakeEvent(IBEventType::OrderStatus, orderId, "DU123");
    filled.key = "Filled";
    filled.number = 1.1;
    filled.number2 = order.totalQuantity;
    fakeRaw->events.push_back(filled);
    DrainOne(adapter, IBEventType::OrderStatus);
    snapshot = adapter.GetAuthoritativeCorrelationSnapshot();
    assert(snapshot.complete);
    assert(adapter.HasPendingPostFillRiskReconciliation());
    assert(snapshot.activeOrderIdsByCorrelation.size() == 1);
    assert(snapshot.activeOrderIdsByCorrelation.at(correlation) == orderId);
    assert(!adapter.AcknowledgePostFillRiskReconciled(orderId));
    CompleteCoherentRiskRefresh(fakeRaw, adapter);
    assert(adapter.AcknowledgePostFillRiskReconciled(orderId));
    assert(!adapter.HasPendingPostFillRiskReconciliation());
    snapshot = adapter.GetAuthoritativeCorrelationSnapshot();
    assert(snapshot.activeOrderIdsByCorrelation.empty());
}

void TestCorrelationSnapshotFailClosedAndRestartReconstruction() {
    std::string orderRef;
    std::string reason;
    const std::string correlation = Correlation('c');
    assert(IbVenueCorrelationCodec::EncodeOrderRef(correlation, orderRef, reason));

    std::unique_ptr<FakeAuthorityWrapper> fake(new FakeAuthorityWrapper());
    FakeAuthorityWrapper* fakeRaw = fake.get();
    HeptaIBGatewayAdapter adapter(std::move(fake));
    assert(adapter.Init(TestTradingConfig()));
    PushConnected(fakeRaw, adapter);

    assert(adapter.ReqOpenOrders());
    IBEvent first = MakeEvent(IBEventType::OpenOrder, 701, "DU123");
    first.order.orderRef = orderRef;
    IBEvent duplicate = first;
    duplicate.id = 702;
    fakeRaw->events.push_back(first);
    fakeRaw->events.push_back(duplicate);
    fakeRaw->events.push_back(MakeEvent(IBEventType::OpenOrderEnd, 0, "DU123"));
    DrainOne(adapter, IBEventType::OpenOrder);
    DrainOne(adapter, IBEventType::OpenOrder);
    DrainOne(adapter, IBEventType::OpenOrderEnd);
    IBAuthoritativeCorrelationSnapshot snapshot =
        adapter.GetAuthoritativeCorrelationSnapshot();
    assert(!snapshot.complete);
    assert(snapshot.reasonCode == "IB_CORRELATION_DUPLICATE_CONFLICT");
    assert(snapshot.activeOrderIdsByCorrelation.empty());

    assert(adapter.ReqOpenOrders());
    IBEvent malformed = MakeEvent(IBEventType::OpenOrder, 703, "DU123");
    malformed.order.orderRef = "H1" + std::string(43, 'A');
    malformed.order.orderRef[44] = 'B';
    fakeRaw->events.push_back(malformed);
    fakeRaw->events.push_back(MakeEvent(IBEventType::OpenOrderEnd, 0, "DU123"));
    DrainOne(adapter, IBEventType::OpenOrder);
    DrainOne(adapter, IBEventType::OpenOrderEnd);
    snapshot = adapter.GetAuthoritativeCorrelationSnapshot();
    assert(!snapshot.complete);
    assert(snapshot.reasonCode == "IB_ORDER_REF_CORRELATION_INVALID");

    std::unique_ptr<FakeAuthorityWrapper> restartedFake(new FakeAuthorityWrapper());
    FakeAuthorityWrapper* restartedRaw = restartedFake.get();
    HeptaIBGatewayAdapter restarted(std::move(restartedFake));
    assert(restarted.Init(TestTradingConfig()));
    PushConnected(restartedRaw, restarted);
    assert(restarted.ReqOpenOrders());
    IBEvent recovered = MakeEvent(IBEventType::OpenOrder, 701, "DU123");
    recovered.order.orderRef = orderRef;
    IBEvent manual = MakeEvent(IBEventType::OpenOrder, 800, "DU123");
    manual.order.orderRef = "manual-order";
    restartedRaw->events.push_back(recovered);
    restartedRaw->events.push_back(manual);
    restartedRaw->events.push_back(MakeEvent(IBEventType::OpenOrderEnd, 0, "DU123"));
    DrainOne(restarted, IBEventType::OpenOrder);
    DrainOne(restarted, IBEventType::OpenOrder);
    DrainOne(restarted, IBEventType::OpenOrderEnd);
    snapshot = restarted.GetAuthoritativeCorrelationSnapshot();
    assert(snapshot.complete);
    assert(snapshot.activeOrderIdsByCorrelation.size() == 1);
    assert(snapshot.activeOrderIdsByCorrelation.at(correlation) == 701);

    IBEvent overflow = MakeEvent(IBEventType::EventQueueOverflow, 0, "");
    overflow.overflowGeneration = 1;
    restartedRaw->events.push_back(overflow);
    DrainOne(restarted, IBEventType::EventQueueOverflow);
    snapshot = restarted.GetAuthoritativeCorrelationSnapshot();
    assert(!snapshot.complete);
    assert(snapshot.reasonCode == "IB_CORRELATION_EVENT_STREAM_OVERFLOW");
    assert(snapshot.activeOrderIdsByCorrelation.empty());
}

void TestAccountWideOpenOrderRestoresRestartCancelAuthority() {
    std::unique_ptr<FakeAuthorityWrapper> fake(new FakeAuthorityWrapper());
    FakeAuthorityWrapper* fakeRaw = fake.get();
    HeptaIBGatewayAdapter adapter(std::move(fake));
    assert(adapter.Init(TestTradingConfig()));
    PushConnected(fakeRaw, adapter);

    assert(adapter.ReqAuthoritativeOpenOrders());
    assert(fakeRaw->allOpenOrderRequests == 1);
    assert(fakeRaw->openOrderRequests == 0);
    IBEvent active = MakeEvent(IBEventType::OpenOrder, 901, "DU123");
    active.value = "Submitted";
    active.order.orderRef = "manual-order";
    fakeRaw->events.push_back(active);
    fakeRaw->events.push_back(MakeEvent(
        IBEventType::OpenOrderEnd, 0, ""));
    DrainOne(adapter, IBEventType::OpenOrder);
    DrainOne(adapter, IBEventType::OpenOrderEnd);

    std::string reason;
    assert(adapter.CanCancelOrder(901, &reason));
    assert(adapter.CancelOrder(901));
    assert(fakeRaw->cancelOrderRequests == 1);
    assert(fakeRaw->lastCancelledOrderId == 901);

    // An order from a different account must not grant cancellation authority.
    IBEvent foreign = MakeEvent(IBEventType::OpenOrder, 902, "DU999");
    foreign.value = "Submitted";
    fakeRaw->events.push_back(foreign);
    DrainOne(adapter, IBEventType::OpenOrder);
    assert(!adapter.CanCancelOrder(902, &reason));
    assert(reason == "NO_BROKER_SUBMIT");
}

void TestDisconnectInvalidatesOldCancelAuthority() {
    std::unique_ptr<FakeAuthorityWrapper> fake(new FakeAuthorityWrapper());
    FakeAuthorityWrapper* fakeRaw = fake.get();
    HeptaIBGatewayAdapter adapter(std::move(fake));
    assert(adapter.Init(TestTradingConfig()));
    PushConnected(fakeRaw, adapter);

    IBEvent active = MakeEvent(IBEventType::OpenOrder, 903, "DU123");
    active.value = "Submitted";
    fakeRaw->events.push_back(active);
    DrainOne(adapter, IBEventType::OpenOrder);
    assert(adapter.CanCancelOrder(903));

    IBEvent closed = MakeEvent(IBEventType::ConnectionClosed, 0, "");
    closed.connectionEpoch = fakeRaw->connectionEpoch;
    fakeRaw->events.push_back(closed);
    DrainOne(adapter, IBEventType::ConnectionClosed);
    std::string reason;
    assert(!adapter.CanCancelOrder(903, &reason));
    assert(reason == "NO_BROKER_SUBMIT");

    // Late evidence from the invalidated epoch cannot reopen cancel authority.
    fakeRaw->events.push_back(active);
    DrainOne(adapter, IBEventType::OpenOrder);
    assert(!adapter.CanCancelOrder(903, &reason));
    assert(reason == "NO_BROKER_SUBMIT");
}

void TestSameEpochReinitializeInvalidatesOldCancelAuthority() {
    std::unique_ptr<FakeAuthorityWrapper> fake(new FakeAuthorityWrapper());
    FakeAuthorityWrapper* fakeRaw = fake.get();
    HeptaIBGatewayAdapter adapter(std::move(fake));
    assert(adapter.Init(TestTradingConfig()));
    PushConnected(fakeRaw, adapter);

    IBEvent active = MakeEvent(IBEventType::OpenOrder, 904, "DU123");
    active.value = "Submitted";
    active.connectionEpoch = fakeRaw->connectionEpoch;
    fakeRaw->events.push_back(active);
    DrainOne(adapter, IBEventType::OpenOrder);
    assert(adapter.CanCancelOrder(904));
    const std::uint64_t connectionEpoch = adapter.GetConnectionEpoch();

    assert(adapter.Init(TestTradingConfig()));
    assert(adapter.GetConnectionEpoch() == connectionEpoch);
    std::string reason;
    assert(!adapter.CanCancelOrder(904, &reason));
    assert(reason == "NO_BROKER_SUBMIT");
    assert(!adapter.CancelOrder(904));
    assert(fakeRaw->cancelOrderRequests == 0);
}

void TestDeferredCancelDoesNotCrossReconnectEpoch() {
    std::unique_ptr<FakeAuthorityWrapper> first(new FakeAuthorityWrapper());
    FakeAuthorityWrapper* firstRaw = first.get();
    std::shared_ptr<std::unique_ptr<FakeAuthorityWrapper> > replacement(
        new std::unique_ptr<FakeAuthorityWrapper>(
            new FakeAuthorityWrapper()));
    FakeAuthorityWrapper* replacementRaw = replacement->get();
    std::function<std::unique_ptr<IIBApiWrapper>()> reconnectFactory =
        [replacement]() mutable {
            return std::move(*replacement);
        };

    HeptaIBGatewayAdapter adapter(std::move(first), reconnectFactory);
    assert(adapter.Init(TestTradingConfig()));
    assert(adapter.Connect());
    adapter.UpdateReferencePrice(1.1);

    IBContractLite contract;
    contract.symbol = "EUR";
    contract.secType = "CASH";
    contract.exchange = "IDEALPRO";
    contract.currency = "USD";
    IBOrderLite order;
    order.action = "BUY";
    order.orderType = "LMT";
    order.totalQuantity = 1000.0;
    order.lmtPrice = 1.1;
    long orderId = -1;
    assert(adapter.PlaceOrder(contract, order, &orderId));

    // The local send is known, but no asynchronous broker acknowledgement has
    // arrived yet.  Cancellation is queued and must not touch the API.
    assert(adapter.CancelOrder(orderId));
    assert(adapter.GetLastRejectReason() ==
           "IB_CANCEL_DEFERRED_UNTIL_BROKER_ACK");
    assert(firstRaw->cancelOrderRequests == 0);

    // A disconnect fences the old lifecycle generation.  Reconnecting with a
    // fresh wrapper and seeing the same numeric order id must not replay the
    // old pre-ack intent against a potentially reused venue order.
    adapter.Disconnect();
    assert(adapter.Connect());
    IBEvent reusedOrderAck = MakeEvent(
        IBEventType::OrderStatus, orderId, "DU123");
    reusedOrderAck.connectionEpoch = adapter.GetConnectionEpoch();
    reusedOrderAck.key = "Submitted";
    replacementRaw->events.push_back(reusedOrderAck);
    DrainOne(adapter, IBEventType::OrderStatus);
    assert(replacementRaw->cancelOrderRequests == 0);
}

void TestDeferredCancelHandlesNonEconomicAndPartialAcknowledgements() {
    std::unique_ptr<FakeAuthorityWrapper> fake(new FakeAuthorityWrapper());
    FakeAuthorityWrapper* fakeRaw = fake.get();
    HeptaIBGatewayAdapter adapter(std::move(fake));
    HeptaIBConfig config = TestTradingConfig();
    config.risk.duplicateOrderWindowSec = 0;
    assert(adapter.Init(config));
    PushConnected(fakeRaw, adapter);
    adapter.UpdateReferencePrice(1.1);

    IBContractLite contract;
    contract.symbol = "EUR";
    contract.secType = "CASH";
    contract.exchange = "IDEALPRO";
    contract.currency = "USD";
    IBOrderLite order;
    order.action = "BUY";
    order.orderType = "LMT";
    order.totalQuantity = 1000.0;
    order.lmtPrice = 1.1;

    long zeroFilledId = -1;
    assert(adapter.PlaceOrder(contract, order, &zeroFilledId));
    assert(adapter.CancelOrder(zeroFilledId));
    assert(fakeRaw->cancelOrderRequests == 0);

    // Filled without positive quantity/price is an acknowledgement but not
    // economic terminal evidence.  The queued cancel must still be sent.
    IBEvent zeroFilled = MakeEvent(
        IBEventType::OrderStatus, zeroFilledId, "DU123");
    zeroFilled.key = "Filled";
    zeroFilled.number = 0.0;
    zeroFilled.number2 = 0.0;
    fakeRaw->events.push_back(zeroFilled);
    DrainOne(adapter, IBEventType::OrderStatus);
    assert(fakeRaw->cancelOrderRequests == 1);

    long partialId = -1;
    order.totalQuantity = 1001.0;
    assert(adapter.PlaceOrder(contract, order, &partialId));
    assert(adapter.CancelOrder(partialId));
    IBEvent partial = MakeEvent(
        IBEventType::OrderStatus, partialId, "DU123");
    partial.key = "PartiallyFilled";
    partial.number = 1.1;
    partial.number2 = 10.0;
    partial.number3 = 991.0;
    fakeRaw->events.push_back(partial);
    DrainOne(adapter, IBEventType::OrderStatus);
    assert(fakeRaw->cancelOrderRequests == 2);

    long pendingCancelId = -1;
    order.totalQuantity = 1002.0;
    assert(adapter.PlaceOrder(contract, order, &pendingCancelId));
    assert(adapter.CancelOrder(pendingCancelId));
    IBEvent pendingCancel = MakeEvent(
        IBEventType::OrderStatus, pendingCancelId, "DU123");
    pendingCancel.key = "PendingCancel";
    pendingCancel.number3 = order.totalQuantity;
    fakeRaw->events.push_back(pendingCancel);
    DrainOne(adapter, IBEventType::OrderStatus);
    // PendingCancel is already a broker-side cancellation acknowledgement;
    // do not issue a duplicate API request.
    assert(fakeRaw->cancelOrderRequests == 2);
}

void TestDeferredCancelDispatchesOnEmptyOpenOrderAcknowledgement() {
    std::unique_ptr<FakeAuthorityWrapper> fake(new FakeAuthorityWrapper());
    FakeAuthorityWrapper* fakeRaw = fake.get();
    HeptaIBGatewayAdapter adapter(std::move(fake));
    assert(adapter.Init(TestTradingConfig()));
    PushConnected(fakeRaw, adapter);
    adapter.UpdateReferencePrice(1.1);

    IBContractLite contract;
    contract.symbol = "EUR";
    contract.secType = "CASH";
    contract.exchange = "IDEALPRO";
    contract.currency = "USD";
    IBOrderLite order;
    order.action = "BUY";
    order.orderType = "LMT";
    order.totalQuantity = 1000.0;
    order.lmtPrice = 1.1;
    long orderId = -1;
    assert(adapter.PlaceOrder(contract, order, &orderId));
    assert(adapter.CancelOrder(orderId));
    assert(fakeRaw->cancelOrderRequests == 0);

    // OpenOrder itself is authoritative existence evidence.  Some broker
    // snapshots may omit OrderState.status; that must still drain the
    // deferred cancel rather than leave it queued forever.
    IBEvent open = MakeEvent(IBEventType::OpenOrder, orderId, "DU123");
    open.order = order;
    open.value.clear();
    fakeRaw->events.push_back(open);
    DrainOne(adapter, IBEventType::OpenOrder);
    assert(fakeRaw->cancelOrderRequests == 1);

    // A later status callback must not replay the same cancel request.
    IBEvent submitted = MakeEvent(IBEventType::OrderStatus, orderId, "DU123");
    submitted.key = "Submitted";
    fakeRaw->events.push_back(submitted);
    DrainOne(adapter, IBEventType::OrderStatus);
    assert(fakeRaw->cancelOrderRequests == 1);
}

void ExerciseDeferredCancelClearedByBrokerError(const std::string& errorCode) {
    std::unique_ptr<FakeAuthorityWrapper> fake(new FakeAuthorityWrapper());
    FakeAuthorityWrapper* fakeRaw = fake.get();
    HeptaIBGatewayAdapter adapter(std::move(fake));
    assert(adapter.Init(TestTradingConfig()));
    PushConnected(fakeRaw, adapter);
    adapter.UpdateReferencePrice(1.1);

    IBContractLite contract;
    contract.symbol = "EUR";
    contract.secType = "CASH";
    contract.exchange = "IDEALPRO";
    contract.currency = "USD";
    IBOrderLite order;
    order.action = "BUY";
    order.orderType = "LMT";
    order.totalQuantity = 1000.0;
    order.lmtPrice = 1.1;
    long orderId = -1;
    assert(adapter.PlaceOrder(contract, order, &orderId));
    assert(adapter.CancelOrder(orderId));
    assert(fakeRaw->cancelOrderRequests == 0);

    // IB 201 (rejected) and 202 (cancelled) are terminal evidence even when
    // they arrive before Submitted.  They must consume the deferred intent.
    IBEvent terminalError = MakeEvent(
        IBEventType::Error, orderId, "DU123");
    terminalError.connectionEpoch = fakeRaw->connectionEpoch;
    terminalError.key = errorCode;
    terminalError.value = "terminal order error";
    fakeRaw->events.push_back(terminalError);
    DrainOne(adapter, IBEventType::Error);

    // Reusing the numeric order id in this still-live epoch must not cause a
    // stale pre-ACK cancel to be sent to the new order.
    IBEvent reused = MakeEvent(
        IBEventType::OrderStatus, orderId, "DU123");
    reused.connectionEpoch = fakeRaw->connectionEpoch;
    reused.key = "Submitted";
    fakeRaw->events.push_back(reused);
    DrainOne(adapter, IBEventType::OrderStatus);
    assert(fakeRaw->cancelOrderRequests == 0);
}

void TestDeferredCancelClearedByBrokerErrorTerminal() {
    ExerciseDeferredCancelClearedByBrokerError("201");
    ExerciseDeferredCancelClearedByBrokerError("202");
}

void TestCompleteActiveViewTracksSequentialMutationsAndCallbacks() {
    std::unique_ptr<FakeAuthorityWrapper> fake(new FakeAuthorityWrapper());
    FakeAuthorityWrapper* fakeRaw = fake.get();
    HeptaIBGatewayAdapter adapter(std::move(fake));
    assert(adapter.Init(TestTradingConfig()));
    PushConnected(fakeRaw, adapter);
    adapter.UpdateReferencePrice(1.1);

    assert(adapter.ReqAuthoritativeOpenOrders());
    fakeRaw->events.push_back(MakeEvent(
        IBEventType::OpenOrderEnd, 0, ""));
    DrainOne(adapter, IBEventType::OpenOrderEnd);
    IBAuthoritativeCorrelationSnapshot snapshot =
        adapter.GetAuthoritativeCorrelationSnapshot();
    assert(snapshot.complete);
    assert(snapshot.activeOrderIds.empty());

    IBContractLite contract;
    contract.symbol = "EUR";
    contract.secType = "CASH";
    contract.exchange = "IDEALPRO";
    contract.currency = "USD";
    IBOrderLite first;
    first.action = "BUY";
    first.orderType = "LMT";
    first.totalQuantity = 1000.0;
    first.lmtPrice = 1.1;
    long firstId = -1;
    const std::string firstCorrelation = Correlation('7');
    assert(adapter.PlaceOrderCorrelated(
        contract, first, firstCorrelation, &firstId));
    snapshot = adapter.GetAuthoritativeCorrelationSnapshot();
    assert(snapshot.complete);
    assert(snapshot.activeOrderIds.size() == 1);
    assert(snapshot.activeOrderIdsByCorrelation.at(firstCorrelation) == firstId);

    IBOrderLite second = first;
    second.totalQuantity = 999.0;
    long secondId = -1;
    const std::string secondCorrelation = Correlation('8');
    assert(adapter.PlaceOrderCorrelated(
        contract, second, secondCorrelation, &secondId));
    assert(secondId != firstId);
    snapshot = adapter.GetAuthoritativeCorrelationSnapshot();
    assert(snapshot.complete);
    assert(snapshot.activeOrderIds.size() == 2);
    assert(snapshot.activeOrderIdsByCorrelation.size() == 2);

    // Cancellation may race the asynchronous Submitted callback.  The
    // adapter must defer the broker cancel until acknowledgement instead of
    // returning NO_BROKER_ACK and losing the operator's cancel intent.
    const int cancelsBeforeDeferred = fakeRaw->cancelOrderRequests;
    assert(adapter.CancelOrder(firstId));
    assert(fakeRaw->cancelOrderRequests == cancelsBeforeDeferred);

    // Cancel submission must retain the order until terminal broker evidence.
    IBEvent submitted = MakeEvent(
        IBEventType::OrderStatus, firstId, "DU123");
    submitted.key = "Submitted";
    fakeRaw->events.push_back(submitted);
    DrainOne(adapter, IBEventType::OrderStatus);
    assert(fakeRaw->cancelOrderRequests == cancelsBeforeDeferred + 1);
    snapshot = adapter.GetAuthoritativeCorrelationSnapshot();
    assert(snapshot.activeOrderIds.count(firstId) == 1);

    IBEvent filled = submitted;
    filled.key = "Filled";
    filled.number = 1.1;
    filled.number2 = first.totalQuantity;
    fakeRaw->events.push_back(filled);
    DrainOne(adapter, IBEventType::OrderStatus);
    snapshot = adapter.GetAuthoritativeCorrelationSnapshot();
    assert(snapshot.complete);
    assert(adapter.HasPendingPostFillRiskReconciliation());
    assert(snapshot.activeOrderIds.count(firstId) == 1);
    assert(!adapter.AcknowledgePostFillRiskReconciled(firstId));
    CompleteCoherentRiskRefresh(fakeRaw, adapter);
    assert(adapter.AcknowledgePostFillRiskReconciled(firstId));
    snapshot = adapter.GetAuthoritativeCorrelationSnapshot();
    assert(snapshot.activeOrderIds.count(firstId) == 0);
    assert(snapshot.activeOrderIds.count(secondId) == 1);
    assert(snapshot.activeOrderIdsByCorrelation.count(firstCorrelation) == 0);

    // Non-refresh broker callbacks increment the already-complete view,
    // including manual orders for maxActiveOrders accounting.
    IBEvent manual = MakeEvent(IBEventType::OpenOrder, 950, "DU123");
    manual.value = "Submitted";
    manual.order.orderRef = "manual-order";
    fakeRaw->events.push_back(manual);
    DrainOne(adapter, IBEventType::OpenOrder);
    snapshot = adapter.GetAuthoritativeCorrelationSnapshot();
    assert(snapshot.activeOrderIds.count(950) == 1);
    assert(snapshot.activeOrderIdsByCorrelation.size() == 1);

    IBEvent malformed = MakeEvent(IBEventType::OpenOrder, 949, "DU123");
    malformed.value = "Submitted";
    malformed.order.orderRef = "H1" + std::string(43, 'A');
    malformed.order.orderRef[44] = 'B';
    fakeRaw->events.push_back(malformed);
    DrainOne(adapter, IBEventType::OpenOrder);
    snapshot = adapter.GetAuthoritativeCorrelationSnapshot();
    assert(!snapshot.complete);
    assert(snapshot.reasonCode == "IB_ORDER_REF_CORRELATION_INVALID");

    assert(adapter.ReqAuthoritativeOpenOrders());
    fakeRaw->events.push_back(MakeEvent(
        IBEventType::OpenOrderEnd, 0, ""));
    DrainOne(adapter, IBEventType::OpenOrderEnd);
    snapshot = adapter.GetAuthoritativeCorrelationSnapshot();
    assert(snapshot.complete);

    std::string thirdRef;
    std::string reason;
    const std::string thirdCorrelation = Correlation('9');
    assert(IbVenueCorrelationCodec::EncodeOrderRef(
        thirdCorrelation, thirdRef, reason));
    IBEvent correlated = MakeEvent(IBEventType::OpenOrder, 951, "DU123");
    correlated.value = "Submitted";
    correlated.order.orderRef = thirdRef;
    fakeRaw->events.push_back(correlated);
    DrainOne(adapter, IBEventType::OpenOrder);
    snapshot = adapter.GetAuthoritativeCorrelationSnapshot();
    assert(snapshot.activeOrderIds.count(951) == 1);
    assert(snapshot.activeOrderIdsByCorrelation.at(thirdCorrelation) == 951);

    IBEvent conflict = correlated;
    conflict.id = 952;
    fakeRaw->events.push_back(conflict);
    DrainOne(adapter, IBEventType::OpenOrder);
    snapshot = adapter.GetAuthoritativeCorrelationSnapshot();
    assert(!snapshot.complete);
    assert(snapshot.reasonCode == "IB_CORRELATION_INCREMENTAL_CONFLICT");
    assert(snapshot.activeOrderIds.empty());
}

void TestTerminalCorrelationSnapshotIsPositiveOnlyAndFailClosed() {
    std::string orderRef;
    std::string codecReason;
    const std::string correlation = Correlation('d');
    assert(IbVenueCorrelationCodec::EncodeOrderRef(
        correlation, orderRef, codecReason));

    std::unique_ptr<FakeAuthorityWrapper> fake(new FakeAuthorityWrapper());
    FakeAuthorityWrapper* fakeRaw = fake.get();
    HeptaIBGatewayAdapter adapter(std::move(fake));
    assert(adapter.Init(TestTradingConfig()));
    PushConnected(fakeRaw, adapter);

    assert(adapter.ReqTerminalCorrelations());
    assert(fakeRaw->completedOrderRequests == 1);
    assert(fakeRaw->executionRequests == 1);
    assert(fakeRaw->lastExecutionRequestId == 1);

    IBEvent terminal = MakeEvent(
        IBEventType::CompletedOrder, 701, "DU123");
    terminal.key = "Filled";
    terminal.order.orderRef = orderRef;
    IBEvent foreign = terminal;
    foreign.id = 999;
    foreign.account = "DU999";
    IBEvent manual = MakeEvent(
        IBEventType::CompletedOrder, 702, "DU123");
    manual.key = "Cancelled";
    manual.order.orderRef = "manual-order";
    IBEvent execution = MakeEvent(
        IBEventType::ExecutionDetails, 701, "DU123");
    execution.requestId = 1;
    execution.number = 1.1001;
    execution.number2 = 100.0;
    IBEvent wrongExecution = execution;
    wrongExecution.id = 999;
    wrongExecution.requestId = 99;
    IBEvent zeroEconomicExecution = execution;
    zeroEconomicExecution.id = 703;
    zeroEconomicExecution.number = 0.0;
    zeroEconomicExecution.number2 = 0.0;
    IBEvent ambiguousZeroOrderExecution = execution;
    ambiguousZeroOrderExecution.id = 0;
    IBEvent wrongExecutionEnd = MakeEvent(
        IBEventType::ExecutionDetailsEnd, 99, "");
    wrongExecutionEnd.requestId = 99;
    IBEvent executionEnd = MakeEvent(
        IBEventType::ExecutionDetailsEnd, 1, "");
    executionEnd.requestId = 1;
    fakeRaw->events.push_back(foreign);
    fakeRaw->events.push_back(manual);
    fakeRaw->events.push_back(terminal);
    fakeRaw->events.push_back(MakeEvent(
        IBEventType::CompletedOrdersEnd, 0, ""));
    fakeRaw->events.push_back(wrongExecution);
    fakeRaw->events.push_back(wrongExecutionEnd);
    fakeRaw->events.push_back(zeroEconomicExecution);
    fakeRaw->events.push_back(ambiguousZeroOrderExecution);
    fakeRaw->events.push_back(execution);
    fakeRaw->events.push_back(executionEnd);

    DrainOne(adapter, IBEventType::CompletedOrder);
    DrainOne(adapter, IBEventType::CompletedOrder);
    DrainOne(adapter, IBEventType::CompletedOrder);
    DrainOne(adapter, IBEventType::CompletedOrdersEnd);
    IBAuthoritativeTerminalCorrelationSnapshot snapshot =
        adapter.GetAuthoritativeTerminalCorrelationSnapshot();
    assert(!snapshot.complete);
    assert(snapshot.reasonCode == "IB_TERMINAL_CORRELATION_REFRESH_PENDING");
    DrainOne(adapter, IBEventType::ExecutionDetails);
    DrainOne(adapter, IBEventType::ExecutionDetailsEnd);
    snapshot = adapter.GetAuthoritativeTerminalCorrelationSnapshot();
    assert(!snapshot.complete);
    DrainOne(adapter, IBEventType::ExecutionDetails);
    snapshot = adapter.GetAuthoritativeTerminalCorrelationSnapshot();
    assert(!snapshot.complete);
    DrainOne(adapter, IBEventType::ExecutionDetails);
    snapshot = adapter.GetAuthoritativeTerminalCorrelationSnapshot();
    assert(!snapshot.complete);
    DrainOne(adapter, IBEventType::ExecutionDetails);
    DrainOne(adapter, IBEventType::ExecutionDetailsEnd);

    snapshot = adapter.GetAuthoritativeTerminalCorrelationSnapshot();
    assert(snapshot.complete);
    assert(snapshot.reasonCode.empty());
    assert(snapshot.connectionEpoch == adapter.GetConnectionEpoch());
    assert(snapshot.generation == 1);
    assert(snapshot.terminalOrderIdsByCorrelation.size() == 1);
    assert(snapshot.terminalOrderIdsByCorrelation.at(correlation) == 701);
    assert(snapshot.terminalStatusesByCorrelation.at(correlation) == "Filled");
    assert(snapshot.executionOrderIds.count(701) == 1);
    assert(snapshot.executionOrderIds.count(0) == 0);
    assert(snapshot.executionOrderIds.count(999) == 0);
    assert(snapshot.executionOrderIds.count(703) == 0);
    // No negative set is exposed: an absent correlation remains unknown.
    assert(snapshot.terminalOrderIdsByCorrelation.count(Correlation('e')) == 0);

    // completedOrdersEnd carries no reqId. Retrying in this connection epoch
    // would let a delayed prior End complete a new generation, so reconnect is
    // mandatory even after a successful query.
    assert(!adapter.ReqTerminalCorrelations());
    assert(adapter.GetLastRejectReason() ==
        "IB_TERMINAL_CORRELATION_RECONNECT_REQUIRED");
    fakeRaw->events.push_back(MakeEvent(
        IBEventType::CompletedOrdersEnd, 0, ""));
    DrainOne(adapter, IBEventType::CompletedOrdersEnd);
    snapshot = adapter.GetAuthoritativeTerminalCorrelationSnapshot();
    assert(snapshot.complete);
    assert(snapshot.generation == 1);
    assert(snapshot.terminalOrderIdsByCorrelation.at(correlation) == 701);

    IBEvent overflow = MakeEvent(IBEventType::EventQueueOverflow, 0, "");
    overflow.overflowGeneration = 1;
    fakeRaw->events.push_back(overflow);
    DrainOne(adapter, IBEventType::EventQueueOverflow);
    snapshot = adapter.GetAuthoritativeTerminalCorrelationSnapshot();
    assert(!snapshot.complete);
    assert(snapshot.reasonCode ==
        "IB_TERMINAL_CORRELATION_EVENT_STREAM_OVERFLOW");
}

void TestTerminalCorrelationInvalidEvidenceFailsClosed() {
    std::string orderRef;
    std::string codecReason;
    assert(IbVenueCorrelationCodec::EncodeOrderRef(
        Correlation('f'), orderRef, codecReason));

    {
        std::unique_ptr<FakeAuthorityWrapper> fake(new FakeAuthorityWrapper());
        FakeAuthorityWrapper* raw = fake.get();
        HeptaIBGatewayAdapter adapter(std::move(fake));
        assert(adapter.Init(TestTradingConfig()));
        PushConnected(raw, adapter);
        assert(adapter.ReqTerminalCorrelations());
        IBEvent nonFinal = MakeEvent(
            IBEventType::CompletedOrder, 801, "DU123");
        nonFinal.key = "Submitted";
        nonFinal.order.orderRef = orderRef;
        IBEvent executionEnd = MakeEvent(
            IBEventType::ExecutionDetailsEnd, 1, "");
        executionEnd.requestId = 1;
        raw->events.push_back(nonFinal);
        raw->events.push_back(MakeEvent(
            IBEventType::CompletedOrdersEnd, 0, ""));
        raw->events.push_back(executionEnd);
        DrainOne(adapter, IBEventType::CompletedOrder);
        DrainOne(adapter, IBEventType::CompletedOrdersEnd);
        DrainOne(adapter, IBEventType::ExecutionDetailsEnd);
        const IBAuthoritativeTerminalCorrelationSnapshot snapshot =
            adapter.GetAuthoritativeTerminalCorrelationSnapshot();
        assert(!snapshot.complete);
        assert(snapshot.reasonCode == "IB_TERMINAL_ORDER_STATUS_NOT_FINAL");
        assert(snapshot.terminalOrderIdsByCorrelation.empty());
    }

    {
        std::unique_ptr<FakeAuthorityWrapper> fake(new FakeAuthorityWrapper());
        FakeAuthorityWrapper* raw = fake.get();
        HeptaIBGatewayAdapter adapter(std::move(fake));
        assert(adapter.Init(TestTradingConfig()));
        PushConnected(raw, adapter);
        assert(adapter.ReqTerminalCorrelations());
        IBEvent malformed = MakeEvent(
            IBEventType::CompletedOrder, 802, "DU123");
        malformed.key = "Filled";
        malformed.order.orderRef = "H1" + std::string(43, 'A');
        malformed.order.orderRef[44] = 'B';
        IBEvent executionEnd = MakeEvent(
            IBEventType::ExecutionDetailsEnd, 1, "");
        executionEnd.requestId = 1;
        raw->events.push_back(malformed);
        raw->events.push_back(MakeEvent(
            IBEventType::CompletedOrdersEnd, 0, ""));
        raw->events.push_back(executionEnd);
        DrainOne(adapter, IBEventType::CompletedOrder);
        DrainOne(adapter, IBEventType::CompletedOrdersEnd);
        DrainOne(adapter, IBEventType::ExecutionDetailsEnd);
        const IBAuthoritativeTerminalCorrelationSnapshot snapshot =
            adapter.GetAuthoritativeTerminalCorrelationSnapshot();
        assert(!snapshot.complete);
        assert(snapshot.reasonCode == "IB_ORDER_REF_CORRELATION_INVALID");
    }

    // A failed request also consumes the epoch. Late completedOrdersEnd cannot
    // be made safe by starting another request without reconnecting.
    std::unique_ptr<FakeAuthorityWrapper> rejecting(new FakeAuthorityWrapper());
    FakeAuthorityWrapper* rejectingRaw = rejecting.get();
    rejectingRaw->acceptTerminalRequests = false;
    HeptaIBGatewayAdapter rejectingAdapter(std::move(rejecting));
    assert(rejectingAdapter.Init(TestTradingConfig()));
    PushConnected(rejectingRaw, rejectingAdapter);
    assert(!rejectingAdapter.ReqTerminalCorrelations());
    rejectingRaw->events.push_back(MakeEvent(
        IBEventType::CompletedOrdersEnd, 0, ""));
    DrainOne(rejectingAdapter, IBEventType::CompletedOrdersEnd);
    IBAuthoritativeTerminalCorrelationSnapshot rejected =
        rejectingAdapter.GetAuthoritativeTerminalCorrelationSnapshot();
    assert(!rejected.complete);
    assert(rejected.reasonCode ==
        "IB_TERMINAL_CORRELATION_REFRESH_REJECTED");
    assert(!rejectingAdapter.ReqTerminalCorrelations());
    assert(rejectingAdapter.GetLastRejectReason() ==
        "IB_TERMINAL_CORRELATION_RECONNECT_REQUIRED");
}

void TestCompletedOrderZeroIdIsAnUnavailableSentinel() {
    std::string firstRef;
    std::string secondRef;
    std::string reason;
    const std::string first = Correlation('1');
    const std::string second = Correlation('2');
    assert(IbVenueCorrelationCodec::EncodeOrderRef(first, firstRef, reason));
    assert(IbVenueCorrelationCodec::EncodeOrderRef(second, secondRef, reason));

    std::unique_ptr<FakeAuthorityWrapper> fake(new FakeAuthorityWrapper());
    FakeAuthorityWrapper* raw = fake.get();
    HeptaIBGatewayAdapter adapter(std::move(fake));
    assert(adapter.Init(TestTradingConfig()));
    PushConnected(raw, adapter);
    assert(adapter.ReqTerminalCorrelations());

    IBEvent firstTerminal = MakeEvent(
        IBEventType::CompletedOrder, 0, "DU123");
    firstTerminal.key = "Filled";
    firstTerminal.order.orderRef = firstRef;
    IBEvent secondTerminal = firstTerminal;
    secondTerminal.order.orderRef = secondRef;
    IBEvent executionEnd = MakeEvent(
        IBEventType::ExecutionDetailsEnd, 0, "");
    executionEnd.requestId = 1;
    raw->events.push_back(firstTerminal);
    raw->events.push_back(secondTerminal);
    raw->events.push_back(MakeEvent(
        IBEventType::CompletedOrdersEnd, 0, ""));
    raw->events.push_back(executionEnd);
    DrainOne(adapter, IBEventType::CompletedOrder);
    DrainOne(adapter, IBEventType::CompletedOrder);
    DrainOne(adapter, IBEventType::CompletedOrdersEnd);
    DrainOne(adapter, IBEventType::ExecutionDetailsEnd);

    const IBAuthoritativeTerminalCorrelationSnapshot snapshot =
        adapter.GetAuthoritativeTerminalCorrelationSnapshot();
    assert(snapshot.complete);
    // A broker string status of Filled without any execDetails is not
    // economic terminal evidence, even when completedOrders reports it.
    assert(snapshot.terminalOrderIdsByCorrelation.count(first) == 0);
    assert(snapshot.terminalOrderIdsByCorrelation.count(second) == 0);
    assert(snapshot.terminalStatusesByCorrelation.count(first) == 0);
    assert(snapshot.terminalStatusesByCorrelation.count(second) == 0);
}

void TestPositionIdentityCollisionFailsClosed() {
    std::unique_ptr<FakeAuthorityWrapper> fake(new FakeAuthorityWrapper());
    FakeAuthorityWrapper* fakeRaw = fake.get();
    HeptaIBGatewayAdapter adapter(std::move(fake));
    assert(adapter.Init(TestTradingConfig()));
    PushConnected(fakeRaw, adapter);
    assert(adapter.ReqPositions());
    IBEvent first = MakeEvent(
        IBEventType::PositionSnapshotItem, 0, "DU123");
    first.key = "CONTRACT:COLLISION";
    first.number = 2.0;
    IBEvent second = first;
    second.number = -2.0;
    fakeRaw->events.push_back(first);
    fakeRaw->events.push_back(second);
    fakeRaw->events.push_back(MakeEvent(IBEventType::PositionEnd, 0, ""));
    DrainOne(adapter, IBEventType::PositionSnapshotItem);
    DrainOne(adapter, IBEventType::PositionSnapshotItem);
    DrainOne(adapter, IBEventType::PositionEnd);
    const IBAuthoritativeRiskSnapshot snapshot =
        adapter.GetAuthoritativeRiskSnapshot();
    assert(!snapshot.positionsComplete);
    assert(snapshot.grossAbsolutePosition == 0.0);
    assert(snapshot.reasonCode == "IB_POSITION_IDENTITY_CONFLICT");
}

void TestAuthoritativeRiskSnapshotCompletesAndTracksGrossPosition() {
    std::unique_ptr<FakeAuthorityWrapper> fake(new FakeAuthorityWrapper());
    FakeAuthorityWrapper* fakeRaw = fake.get();
    HeptaIBGatewayAdapter adapter(std::move(fake));
    assert(adapter.Init(TestTradingConfig()));
    PushConnected(fakeRaw, adapter);

    assert(adapter.ReqAccountSummary());
    assert(adapter.ReqPositions());
    assert(fakeRaw->accountSummaryRequests == 1);
    assert(fakeRaw->positionRequests == 1);

    IBAuthoritativeRiskSnapshot snapshot = adapter.GetAuthoritativeRiskSnapshot();
    assert(!snapshot.accountComplete);
    assert(!snapshot.positionsComplete);
    assert(snapshot.accountGeneration != 0);
    assert(snapshot.positionsGeneration > snapshot.accountGeneration);
    assert(snapshot.generation == snapshot.positionsGeneration);

    IBEvent account = MakeEvent(IBEventType::AccountValue, 9001, "DU123");
    account.key = "NetLiquidation:USD";
    account.value = "100000";
    fakeRaw->events.push_back(account);
    fakeRaw->events.push_back(MakeEvent(
        IBEventType::AccountSummaryEnd, 9001, ""));

    IBEvent eur = MakeEvent(IBEventType::PositionSnapshotItem, 0, "DU123");
    eur.key = "EUR.USD";
    eur.number = -3.5;
    IBEvent spy = MakeEvent(IBEventType::PositionSnapshotItem, 0, "DU123");
    spy.key = "SPY";
    spy.number = 2.0;
    fakeRaw->events.push_back(eur);
    fakeRaw->events.push_back(spy);
    fakeRaw->events.push_back(MakeEvent(IBEventType::PositionEnd, 0, ""));

    DrainOne(adapter, IBEventType::AccountValue);
    DrainOne(adapter, IBEventType::AccountSummaryEnd);
    DrainOne(adapter, IBEventType::PositionSnapshotItem);
    DrainOne(adapter, IBEventType::PositionSnapshotItem);
    DrainOne(adapter, IBEventType::PositionEnd);

    snapshot = adapter.GetAuthoritativeRiskSnapshot();
    assert(snapshot.accountComplete);
    assert(snapshot.positionsComplete);
    assert(snapshot.reasonCode.empty());
    assert(snapshot.grossAbsolutePosition == 5.5);
    assert(snapshot.connectionEpoch == adapter.GetConnectionEpoch());

    assert(adapter.ReqOpenOrders());
    fakeRaw->events.push_back(MakeEvent(IBEventType::OpenOrderEnd, 0, ""));
    DrainOne(adapter, IBEventType::OpenOrderEnd);
    const IBAuthoritativeCorrelationSnapshot correlation =
        adapter.GetAuthoritativeCorrelationSnapshot();
    assert(correlation.complete);
    assert(snapshot.accountComplete && snapshot.positionsComplete);

    IBEvent update = MakeEvent(IBEventType::PortfolioUpdate, 0, "DU123");
    update.key = "EUR.USD";
    update.number = -1.0;
    fakeRaw->events.push_back(update);
    DrainOne(adapter, IBEventType::PortfolioUpdate);
    snapshot = adapter.GetAuthoritativeRiskSnapshot();
    assert(snapshot.positionsComplete);
    assert(snapshot.grossAbsolutePosition == 3.0);
}

void TestReduceOnlyFlattenRevalidatesUnderAdapterSendLock() {
    std::unique_ptr<FakeAuthorityWrapper> fake(new FakeAuthorityWrapper());
    FakeAuthorityWrapper* fakeRaw = fake.get();
    fakeRaw->connectionEpoch = 1;
    HeptaIBGatewayAdapter adapter(std::move(fake));
    assert(adapter.Init(TestTradingConfig()));
    PushConnected(fakeRaw, adapter);
    adapter.UpdateReferencePrice(1.1);

    assert(adapter.ReqAccountSummary());
    assert(adapter.ReqPositions());
    IBEvent account = MakeEvent(
        IBEventType::AccountValue, 9001, "DU123");
    account.key = "NetLiquidation:USD";
    account.value = "100000";
    fakeRaw->events.push_back(account);
    fakeRaw->events.push_back(MakeEvent(
        IBEventType::AccountSummaryEnd, 9001, ""));
    IBEvent position = MakeEvent(
        IBEventType::PositionSnapshotItem, 0, "DU123");
    position.key = "EUR.USD";
    position.number = 100.0;
    fakeRaw->events.push_back(position);
    fakeRaw->events.push_back(
        MakeEvent(IBEventType::PositionEnd, 0, ""));
    DrainOne(adapter, IBEventType::AccountValue);
    DrainOne(adapter, IBEventType::AccountSummaryEnd);
    DrainOne(adapter, IBEventType::PositionSnapshotItem);
    DrainOne(adapter, IBEventType::PositionEnd);

    assert(adapter.ReqAuthoritativeOpenOrders());
    fakeRaw->events.push_back(
        MakeEvent(IBEventType::OpenOrderEnd, 0, ""));
    DrainOne(adapter, IBEventType::OpenOrderEnd);
    const IBAuthoritativeRiskSnapshot initial =
        adapter.GetAuthoritativeRiskSnapshot();
    assert(initial.positionsComplete);

    IBContractLite contract;
    contract.symbol = "EUR";
    contract.secType = "CASH";
    contract.exchange = "IDEALPRO";
    contract.currency = "USD";
    IBOrderLite close;
    close.action = "SELL";
    close.orderType = "LMT";
    close.totalQuantity = 100.0;
    close.lmtPrice = 1.1;
    long orderId = -1;
    const bool firstFlatten = adapter.PlaceReduceOnlyOrderCorrelated(
        contract, close, "EUR.USD", 100.0,
        initial.connectionEpoch, initial.positionsGeneration,
        "IB:1:1:1001", 100000, 105000,
        Correlation('e'), &orderId);
    if (!firstFlatten)
        std::cerr << "initial reduce-only flatten rejected: "
                  << adapter.GetLastRejectReason() << std::endl;
    assert(firstFlatten);
    assert(fakeRaw->placeOrderRequests == 1);

    long ignored = -1;
    assert(!adapter.PlaceReduceOnlyOrderCorrelated(
        contract, close, "EUR.USD", 100.0,
        initial.connectionEpoch, initial.positionsGeneration,
        "IB:1:1:1001", 100000, 105000,
        Correlation('f'), &ignored));
    assert(adapter.GetLastRejectReason() ==
           "IB_FLATTEN_ACTIVE_ORDER_SNAPSHOT_UNSAFE");
    assert(fakeRaw->placeOrderRequests == 1);

    IBEvent filled = MakeEvent(
        IBEventType::OrderStatus, orderId, "DU123");
    filled.key = "Filled";
    filled.number = 1.1;
    filled.number2 = close.totalQuantity;
    fakeRaw->events.push_back(filled);
    DrainOne(adapter, IBEventType::OrderStatus);
    assert(adapter.HasPendingPostFillRiskReconciliation());
    assert(!adapter.AcknowledgePostFillRiskReconciled(orderId));
    CompleteCoherentRiskRefresh(fakeRaw, adapter);
    assert(adapter.AcknowledgePostFillRiskReconciled(orderId));
    const IBAuthoritativeRiskSnapshot reconciled =
        adapter.GetAuthoritativeRiskSnapshot();
    assert(reconciled.coherentRefreshComplete);
    IBEvent changed = MakeEvent(
        IBEventType::PortfolioUpdate, 0, "DU123");
    changed.key = "EUR.USD";
    changed.number = 80.0;
    fakeRaw->events.push_back(changed);
    DrainOne(adapter, IBEventType::PortfolioUpdate);

    assert(!adapter.PlaceReduceOnlyOrderCorrelated(
        contract, close, "EUR.USD", 100.0,
        reconciled.connectionEpoch, reconciled.positionsGeneration,
        "IB:1:1:1001", 100000, 105000,
        Correlation('1'), &ignored));
    assert(adapter.GetLastRejectReason() ==
           "IB_FLATTEN_POSITION_CHANGED_BEFORE_SEND");
    // Bounded partial flatten is valid; only an overshoot is rejected.
    close.totalQuantity = 81.0;
    assert(!adapter.PlaceReduceOnlyOrderCorrelated(
        contract, close, "EUR.USD", 80.0,
        reconciled.connectionEpoch, reconciled.positionsGeneration,
        "IB:1:1:1001", 100000, 105000,
        Correlation('2'), &ignored));
    assert(adapter.GetLastRejectReason() ==
           "IB_FLATTEN_NOT_EXACT_REDUCE_ONLY");

    int killSwitchState = 2;
    adapter.SetPrePlaceOrderSendCheck(
        [&](const IBFinalOrderSendContext* context,
            const IBContractLite&, const IBOrderLite&,
            std::string* reason) {
            if (killSwitchState == 2)
            {
                if (reason != nullptr)
                    *reason =
                        "IB_PAPER_KILL_SWITCH_STATE_UNCERTAIN";
                return false;
            }
            if (killSwitchState == 1 &&
                (context == nullptr || !context->exactReduceOnly))
            {
                if (reason != nullptr)
                    *reason = "IB_PAPER_KILL_SWITCH_ENGAGED";
                return false;
            }
            if (context == nullptr)
            {
                if (reason != nullptr)
                    *reason =
                        "IB_PAPER_PLACE_QUOTE_BINDING_REQUIRED";
                return false;
            }
            assert(context != nullptr);
            assert(context->instrument == "EUR.USD");
            assert(context->quoteSubscriptionId ==
                   "IB:1:1:1001");
            assert(context->quoteObservedAtMs == 100000);
            assert(context->quoteStaleAfterMs == 105000);
            if (!context->exactReduceOnly)
            {
                assert(context->authoritativeQuoteBound);
                assert(context->quoteBid == 1.1000);
                assert(context->quoteAsk == 1.1002);
            }
            return true;
        });
    close.totalQuantity = 80.0;
    assert(!adapter.PlaceReduceOnlyOrderCorrelated(
        contract, close, "EUR.USD", 80.0,
        reconciled.connectionEpoch, reconciled.positionsGeneration,
        "IB:1:1:1001", 100000, 105000,
        Correlation('3'), &ignored));
    assert(adapter.GetLastRejectReason() ==
           "IB_PAPER_KILL_SWITCH_STATE_UNCERTAIN");
    assert(fakeRaw->placeOrderRequests == 1);

    killSwitchState = 1;
    IBOrderLite ordinary = close;
    ordinary.action = "BUY";
    ordinary.totalQuantity = 1.0;
    ordinary.lmtPrice = 1.101;
    assert(!adapter.PlaceOrder(contract, ordinary, &ignored));
    assert(adapter.GetLastRejectReason() ==
           "IB_PAPER_KILL_SWITCH_ENGAGED");
    assert(fakeRaw->placeOrderRequests == 1);
    assert(adapter.PlaceReduceOnlyOrderCorrelated(
        contract, close, "EUR.USD", 80.0,
        reconciled.connectionEpoch, reconciled.positionsGeneration,
        "IB:1:1:1001", 100000, 105000,
        Correlation('4'), &ignored));
    assert(fakeRaw->placeOrderRequests == 2);

    killSwitchState = 0;
    ordinary.orderType = "MKT";
    ordinary.lmtPrice = 0.0;
    assert(!adapter.PlaceOrderCorrelated(
        contract, ordinary, Correlation('5'), &ignored));
    assert(adapter.GetLastRejectReason() ==
           "IB_PAPER_PLACE_QUOTE_BINDING_REQUIRED");
    assert(fakeRaw->placeOrderRequests == 2);
    IBFinalOrderSendContext ordinaryContext;
    ordinaryContext.authoritativeQuoteBound = true;
    ordinaryContext.instrument = "EUR.USD";
    ordinaryContext.quoteSubscriptionId = "IB:1:1:1001";
    ordinaryContext.quoteBid = 1.1000;
    ordinaryContext.quoteAsk = 1.1002;
    ordinaryContext.quoteObservedAtMs = 100000;
    ordinaryContext.quoteStaleAfterMs = 105000;
    assert(!adapter.PlaceOrderCorrelated(
        contract, ordinary, Correlation('6'),
        &ignored, &ordinaryContext));
    assert(adapter.GetLastRejectReason() ==
           "IB_PAPER_PLACE_CONTRACT_MISMATCH");
    assert(fakeRaw->placeOrderRequests == 2);
}

void TestAtomicProveFlatAndSubToleranceOverCloseFailClosed() {
    std::unique_ptr<FakeAuthorityWrapper> fake(new FakeAuthorityWrapper());
    FakeAuthorityWrapper* fakeRaw = fake.get();
    fakeRaw->connectionEpoch = 1;
    HeptaIBGatewayAdapter adapter(std::move(fake));
    assert(adapter.Init(TestTradingConfig()));
    PushConnected(fakeRaw, adapter);
    adapter.UpdateReferencePrice(1.1);

    assert(adapter.ReqAccountSummary());
    assert(adapter.ReqPositions());
    IBEvent account = MakeEvent(
        IBEventType::AccountValue, 9001, "DU123");
    account.key = "NetLiquidation:USD";
    account.value = "100000";
    fakeRaw->events.push_back(account);
    fakeRaw->events.push_back(MakeEvent(
        IBEventType::AccountSummaryEnd, 9001, ""));
    IBEvent flat = MakeEvent(
        IBEventType::PositionSnapshotItem, 0, "DU123");
    flat.key = "EUR.USD";
    flat.number = 0.0;
    fakeRaw->events.push_back(flat);
    fakeRaw->events.push_back(
        MakeEvent(IBEventType::PositionEnd, 0, ""));
    DrainOne(adapter, IBEventType::AccountValue);
    DrainOne(adapter, IBEventType::AccountSummaryEnd);
    DrainOne(adapter, IBEventType::PositionSnapshotItem);
    DrainOne(adapter, IBEventType::PositionEnd);
    assert(adapter.ReqAuthoritativeOpenOrders());
    fakeRaw->events.push_back(
        MakeEvent(IBEventType::OpenOrderEnd, 0, ""));
    DrainOne(adapter, IBEventType::OpenOrderEnd);

    int finalGuard = 0;
    adapter.SetPrePlaceOrderSendCheck(
        [&](const IBFinalOrderSendContext* context,
            const IBContractLite&, const IBOrderLite&,
            std::string* reason) {
            if (context != nullptr && context->proveFlatOnly)
            {
                assert(context->exactReduceOnly);
                assert(context->instrument == "EUR.USD");
                if (finalGuard != 0)
                {
                    if (reason != nullptr)
                        *reason =
                            "IB_PAPER_KILL_SWITCH_STATE_UNCERTAIN";
                    return false;
                }
            }
            return true;
        });
    IBEvent tiny = MakeEvent(
        IBEventType::PortfolioUpdate, 0, "DU123");
    tiny.key = "EUR.USD";
    tiny.number = 1e-14;
    const IBAuthoritativeRiskSnapshot initial =
        adapter.GetAuthoritativeRiskSnapshot();
    std::string reason;
    std::atomic<bool> commitEntered(false);
    std::atomic<bool> releaseCommit(false);
    std::atomic<bool> authorityUpdateApplied(false);
    bool commitAttempted = false;
    bool committed = false;
    std::thread proveThread([&]() {
        committed = adapter.ProveAndCommitFlatNoop(
            "EUR.USD", initial.connectionEpoch,
            initial.positionsGeneration,
            [&]() {
                commitEntered.store(true);
                while (!releaseCommit.load())
                    std::this_thread::yield();
                return true;
            },
            &commitAttempted, &reason);
    });
    while (!commitEntered.load())
        std::this_thread::yield();
    fakeRaw->events.push_back(tiny);
    std::thread updateThread([&]() {
        DrainOne(adapter, IBEventType::PortfolioUpdate);
        authorityUpdateApplied.store(true);
    });
    std::this_thread::sleep_for(
        std::chrono::milliseconds(20));
    assert(!authorityUpdateApplied.load());
    assert(fakeRaw->placeOrderRequests == 0);
    // A kill-state transition requested after durable commit begins is after
    // the no-op's audit linearization point.
    finalGuard = 1;
    releaseCommit.store(true);
    proveThread.join();
    updateThread.join();
    assert(committed);
    assert(commitAttempted);
    assert(authorityUpdateApplied.load());
    assert(reason.empty());

    commitAttempted = false;
    assert(!adapter.ProveAndCommitFlatNoop(
        "EUR.USD", initial.connectionEpoch,
        initial.positionsGeneration, []() { return true; },
        &commitAttempted, &reason));
    assert(!commitAttempted);
    assert(reason ==
           "IB_FLATTEN_POSITION_CHANGED_BEFORE_NOOP");

    IBContractLite contract;
    contract.symbol = "EUR";
    contract.secType = "CASH";
    contract.exchange = "IDEALPRO";
    contract.currency = "USD";
    IBOrderLite overClose;
    overClose.action = "SELL";
    overClose.orderType = "LMT";
    overClose.totalQuantity = 1e-13;
    overClose.lmtPrice = 1.1;
    long orderId = -1;
    assert(!adapter.PlaceReduceOnlyOrderCorrelated(
        contract, overClose, "EUR.USD", 1e-13,
        initial.connectionEpoch, initial.positionsGeneration,
        "IB:1:1:1001", 100000, 105000,
        Correlation('5'), &orderId));
    assert(adapter.GetLastRejectReason() ==
           "IB_FLATTEN_POSITION_CHANGED_BEFORE_SEND");
    assert(!adapter.PlaceReduceOnlyOrderCorrelated(
        contract, overClose, "EUR.USD", 1e-14,
        initial.connectionEpoch, initial.positionsGeneration,
        "IB:1:1:1001", 100000, 105000,
        Correlation('6'), &orderId));
    assert(adapter.GetLastRejectReason() ==
           "IB_FLATTEN_NOT_EXACT_REDUCE_ONLY");
    assert(fakeRaw->placeOrderRequests == 0);

    tiny.number = 0.0;
    fakeRaw->events.push_back(tiny);
    DrainOne(adapter, IBEventType::PortfolioUpdate);
    IBOrderLite ordinary;
    ordinary.action = "BUY";
    ordinary.orderType = "LMT";
    ordinary.totalQuantity = 1.0;
    ordinary.lmtPrice = 1.1;
    assert(adapter.PlaceOrderCorrelated(
        contract, ordinary, Correlation('7'), &orderId));
    commitAttempted = false;
    assert(!adapter.ProveAndCommitFlatNoop(
        "EUR.USD", initial.connectionEpoch,
        initial.positionsGeneration, []() { return true; },
        &commitAttempted, &reason));
    assert(!commitAttempted);
    assert(reason ==
           "IB_FLATTEN_ACTIVE_ORDER_SNAPSHOT_UNSAFE");
    assert(fakeRaw->placeOrderRequests == 1);

    IBEvent filled = MakeEvent(
        IBEventType::OrderStatus, orderId, "DU123");
    filled.key = "Filled";
    filled.number = 1.1;
    filled.number2 = ordinary.totalQuantity;
    fakeRaw->events.push_back(filled);
    DrainOne(adapter, IBEventType::OrderStatus);
    assert(adapter.HasPendingPostFillRiskReconciliation());
    assert(!adapter.AcknowledgePostFillRiskReconciled(orderId));
    CompleteCoherentRiskRefresh(fakeRaw, adapter);
    assert(adapter.AcknowledgePostFillRiskReconciled(orderId));
    const IBAuthoritativeRiskSnapshot postFill =
        adapter.GetAuthoritativeRiskSnapshot();
    assert(postFill.coherentRefreshComplete);
    commitAttempted = false;
    assert(!adapter.ProveAndCommitFlatNoop(
        "EUR.USD", postFill.connectionEpoch,
        postFill.positionsGeneration, []() { return true; },
        &commitAttempted, &reason));
    assert(!commitAttempted);
    assert(reason ==
           "IB_PAPER_KILL_SWITCH_STATE_UNCERTAIN");
}

void TestAuthoritativeRiskSnapshotFailsClosedOnStaleDisconnectAndOverflow() {
    std::unique_ptr<FakeAuthorityWrapper> fake(new FakeAuthorityWrapper());
    FakeAuthorityWrapper* fakeRaw = fake.get();
    HeptaIBGatewayAdapter adapter(std::move(fake));
    assert(adapter.Init(TestTradingConfig()));
    PushConnected(fakeRaw, adapter);

    assert(adapter.ReqAccountSummary());
    IBEvent staleEnd = MakeEvent(IBEventType::AccountSummaryEnd, 9001, "");
    staleEnd.connectionEpoch = 99;
    fakeRaw->events.push_back(staleEnd);
    IBEvent ignored;
    assert(!adapter.TryDequeueEvent(ignored));
    IBAuthoritativeRiskSnapshot snapshot = adapter.GetAuthoritativeRiskSnapshot();
    assert(!snapshot.accountComplete);
    assert(snapshot.reasonCode == "IB_ACCOUNT_SUMMARY_REFRESH_PENDING");

    IBEvent closed = MakeEvent(IBEventType::ConnectionClosed, 0, "");
    fakeRaw->events.push_back(closed);
    DrainOne(adapter, IBEventType::ConnectionClosed);
    snapshot = adapter.GetAuthoritativeRiskSnapshot();
    assert(!snapshot.accountComplete);
    assert(!snapshot.positionsComplete);
    assert(snapshot.reasonCode == "IB_RISK_CONNECTION_CLOSED");

    std::unique_ptr<FakeAuthorityWrapper> overflowFake(new FakeAuthorityWrapper());
    FakeAuthorityWrapper* overflowRaw = overflowFake.get();
    HeptaIBGatewayAdapter overflowAdapter(std::move(overflowFake));
    assert(overflowAdapter.Init(TestTradingConfig()));
    PushConnected(overflowRaw, overflowAdapter);
    assert(overflowAdapter.ReqPositions());
    IBEvent position = MakeEvent(
        IBEventType::PositionSnapshotItem, 0, "DU123");
    position.key = "SPY";
    position.number = 4.0;
    overflowRaw->events.push_back(position);
    DrainOne(overflowAdapter, IBEventType::PositionSnapshotItem);
    IBEvent overflow = MakeEvent(IBEventType::EventQueueOverflow, 0, "");
    overflow.overflowGeneration = 1;
    overflowRaw->events.push_back(overflow);
    DrainOne(overflowAdapter, IBEventType::EventQueueOverflow);
    snapshot = overflowAdapter.GetAuthoritativeRiskSnapshot();
    assert(!snapshot.accountComplete);
    assert(!snapshot.positionsComplete);
    assert(snapshot.grossAbsolutePosition == 0.0);
    assert(snapshot.reasonCode == "IB_RISK_EVENT_STREAM_OVERFLOW");

    overflowRaw->events.push_back(MakeEvent(IBEventType::PositionEnd, 0, ""));
    DrainOne(overflowAdapter, IBEventType::PositionEnd);
    snapshot = overflowAdapter.GetAuthoritativeRiskSnapshot();
    assert(!snapshot.positionsComplete);
    assert(snapshot.reasonCode == "IB_RISK_EVENT_STREAM_OVERFLOW");
}

void TestRecoveryAuditCompositeAbsorbsHistoricalAndRejectsLiveLateFill() {
    std::unique_ptr<FakeAuthorityWrapper> fake(new FakeAuthorityWrapper());
    FakeAuthorityWrapper* raw = fake.get();
    HeptaIBGatewayAdapter adapter(std::move(fake));
    assert(adapter.Init(TestTradingConfig()));
    assert(adapter.Connect());
    PushConnected(raw, adapter);

    // This flat risk view predates the historical terminal query and therefore
    // cannot absorb an execution discovered by that query.
    CompleteCoherentRiskRefresh(raw, adapter);
    assert(adapter.ReqAuthoritativeOpenOrders());
    raw->events.push_back(MakeEvent(
        IBEventType::OpenOrderEnd, 0, "DU123"));
    DrainOne(adapter, IBEventType::OpenOrderEnd);
    assert(adapter.ReqTerminalCorrelations());

    std::string orderRef;
    std::string reason;
    assert(IbVenueCorrelationCodec::EncodeOrderRef(
        Correlation('4'), orderRef, reason));
    IBEvent completed = MakeEvent(
        IBEventType::CompletedOrder, 404, "DU123");
    completed.key = "Filled";
    completed.order.orderRef = orderRef;
    IBEvent historical = MakeEvent(
        IBEventType::ExecutionDetails, 404, "DU123");
    historical.requestId = 1;
    historical.key = "historical-execution-404";
    historical.value = "BUY";
    historical.number = 1.1;
    historical.number2 = 1.0;
    historical.number3 = 0.0;
    IBEvent executionEnd = MakeEvent(
        IBEventType::ExecutionDetailsEnd, 0, "DU123");
    executionEnd.requestId = 1;
    raw->events.push_back(completed);
    raw->events.push_back(MakeEvent(
        IBEventType::CompletedOrdersEnd, 0, "DU123"));
    raw->events.push_back(historical);
    raw->events.push_back(executionEnd);
    DrainOne(adapter, IBEventType::CompletedOrder);
    DrainOne(adapter, IBEventType::CompletedOrdersEnd);
    DrainOne(adapter, IBEventType::ExecutionDetails);
    DrainOne(adapter, IBEventType::ExecutionDetailsEnd);

    IBAuthoritativeRecoveryAuditSnapshot snapshot =
        adapter.GetAuthoritativeRecoveryAuditSnapshot();
    assert(snapshot.active.complete);
    assert(snapshot.terminal.complete);
    assert(snapshot.exposureGeneration == 1);
    assert(snapshot.terminalExposureGeneration == 1);
    assert(snapshot.riskAbsorbedExposureGeneration == 0);
    assert(snapshot.postFillRiskReconciliationPending);
    assert(!snapshot.barrierComplete);

    CompleteCoherentRiskRefresh(raw, adapter, 1.0, true);
    snapshot = adapter.GetAuthoritativeRecoveryAuditSnapshot();
    assert(snapshot.barrierComplete);
    assert(snapshot.exposureGeneration == 1);
    assert(snapshot.terminalExposureGeneration == 1);
    assert(snapshot.riskAbsorbedExposureGeneration == 1);
    assert(!snapshot.postFillRiskReconciliationPending);
    assert(snapshot.positionQuantities.at("EUR.USD") == 1.0);
    assert(snapshot.risk.grossAbsolutePosition == 1.0);

    // A live economic callback after the completed barrier invalidates it
    // before any later recovery audit can publish the stale flat evidence.
    IBEvent lateFill = MakeEvent(
        IBEventType::OrderStatus, 405, "DU123");
    lateFill.key = "Filled";
    lateFill.number = 1.1;
    lateFill.number2 = 1.0;
    lateFill.number3 = 0.0;
    raw->events.push_back(lateFill);
    DrainOne(adapter, IBEventType::OrderStatus);
    snapshot = adapter.GetAuthoritativeRecoveryAuditSnapshot();
    assert(!snapshot.barrierComplete);
    assert(snapshot.exposureGeneration == 2);
    assert(snapshot.riskAbsorbedExposureGeneration == 1);
    assert(snapshot.postFillRiskReconciliationPending);
    assert(!adapter.AcknowledgePostFillRiskReconciled(405));

    IBAuthoritativeRecoveryAuditSnapshot begin =
        adapter.BeginRecoveryAuditBarrier();
    assert(!begin.barrierComplete);
    assert(!begin.newConnectionEpochRequired);
    assert(begin.reasonCode ==
        "IB_RECOVERY_AUDIT_PRECONDITIONS_NOT_FLAT");
    CompleteCoherentRiskRefresh(raw, adapter, 1.0);
    assert(adapter.AcknowledgePostFillRiskReconciled(405));
    begin = adapter.BeginRecoveryAuditBarrier();
    assert(begin.newConnectionEpochRequired);
    assert(begin.reasonCode ==
        "IB_RECOVERY_AUDIT_NEW_CONNECTION_EPOCH_REQUIRED");
}

} // namespace

int main() {
#ifdef _WIN32
    _putenv_s("HEPTA_IB_OBS_LOG", "ib_authoritative_event_queue_tests.log");
#else
    setenv("HEPTA_IB_OBS_LOG", "/tmp/hepta_ib_authoritative_event_queue_tests.log", 1);
#endif

    TestPositionsMultiRequestFenceRejectsDelayedOldEnd();
    TestMarketDataAdmissionStateKeepsFaultsAndGenerationsAtomic();
    TestCashFarmMarkerKeepsEpochAndSequenceOrdering();
    TestCashFarmMarkerResetRetainsConcurrentCallbackContention();
    TestEventQueueTryPushAndExternalDropAreExplicit();
    TestFxCashBaselineDeltaIsAuthoritativeCampaignPosition();
    TestBoundCashFxContractMustMatchConfiguredContract();
    TestDistinctAuthorityEventsPreserveAccountAndPayload();
    TestOverflowIsReportedBeforeRemainingEvents();
    TestAdapterDropsStaleConnectionEpochEvents();
    TestLegacyMocksAndAdapterFailClosedLatch();
    TestCorrelationCodecIsCanonicalAndReversible();
    TestCorrelatedSendAndAuthoritativeReadback();
    TestCorrelationSnapshotFailClosedAndRestartReconstruction();
    TestAccountWideOpenOrderRestoresRestartCancelAuthority();
    TestDisconnectInvalidatesOldCancelAuthority();
    TestSameEpochReinitializeInvalidatesOldCancelAuthority();
    TestDeferredCancelDoesNotCrossReconnectEpoch();
    TestDeferredCancelHandlesNonEconomicAndPartialAcknowledgements();
    TestDeferredCancelDispatchesOnEmptyOpenOrderAcknowledgement();
    TestDeferredCancelClearedByBrokerErrorTerminal();
    TestCompleteActiveViewTracksSequentialMutationsAndCallbacks();
    TestTerminalCorrelationSnapshotIsPositiveOnlyAndFailClosed();
    TestTerminalCorrelationInvalidEvidenceFailsClosed();
    TestCompletedOrderZeroIdIsAnUnavailableSentinel();
    TestPositionIdentityCollisionFailsClosed();
    TestAuthoritativeRiskSnapshotCompletesAndTracksGrossPosition();
    TestReduceOnlyFlattenRevalidatesUnderAdapterSendLock();
    TestAtomicProveFlatAndSubToleranceOverCloseFailClosed();
    TestRecoveryAuditCompositeAbsorbsHistoricalAndRejectsLiveLateFill();
    TestAuthoritativeRiskSnapshotFailsClosedOnStaleDisconnectAndOverflow();

    std::cout << "ib_authoritative_fault_matrix_evidence:"
              << " stale_connection_epoch_drop=verified"
              << " queue_overflow_resync_latch=verified"
              << " positions_multi_stale_end_fence=verified"
              << " market_data_admission_state=verified"
              << " cash_farm_marker_epoch_sequence=verified"
              << " event_queue_try_push_drop=verified"
              << " active_duplicate_conflict=verified"
              << " active_incremental_conflict=verified"
              << " terminal_invalid_evidence=verified"
              << " terminal_overflow_fail_closed=verified"
              << " risk_snapshot_fail_closed=verified"
              << " contract_binding_fail_closed=verified"
              << " reduce_only_send_revalidation=verified"
              << std::endl;

#ifdef _WIN32
    std::remove("ib_authoritative_event_queue_tests.log");
#else
    std::remove("/tmp/hepta_ib_authoritative_event_queue_tests.log");
#endif
    return 0;
}
