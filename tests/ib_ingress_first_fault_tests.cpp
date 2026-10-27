#include "adapter_ib/ib_api_wrapper.h"
#include "execution/ib_paper_execution_runtime_composition.h"
#include <atomic>
#include <cstdlib>
#include <cstring>
#include <iostream>
#include <new>
#include <stdexcept>
#include <thread>

namespace {
thread_local bool denyAllocation = false;
std::atomic<unsigned> deniedAllocations{0};
std::atomic<bool> clockEntered{false}, releaseClock{false};
void require(bool value, const char* reason) {
    if (!value) throw std::runtime_error(reason);
}
std::uint64_t fixedClock() { return 123456789; }
std::uint64_t pausedClock() {
    // Private fixture only: production's diagnostic clock never blocks.
    clockEntered.store(true, std::memory_order_release);
    while (!releaseClock.load(std::memory_order_acquire)) std::this_thread::yield();
    return fixedClock();
}
struct NoAllocation {
    NoAllocation() { denyAllocation = true; }
    ~NoAllocation() { denyAllocation = false; }
};
bool same(const IBIngressFirstFault& a, const IBIngressFirstFault& b) {
    return a.pending == b.pending && a.kind == b.kind &&
        a.monotonicNs == b.monotonicNs && a.connectionEpoch == b.connectionEpoch &&
        a.admissionGeneration == b.admissionGeneration;
}
void pendingIsNotASecondWritersRecord() {
    clockEntered.store(false); releaseClock.store(false);
    IBMarketDataAdmissionState state(pausedClock);
    state.SetDiagnosticConnectionEpoch(7);
    std::uint64_t generation = 0;
    require(state.Begin(generation) && generation == 1, "open original generation");
    std::thread writer([&] {
        NoAllocation guard;
        state.MarkFault(IBIngressFaultKind::AuthoritativeQueueBusy);
    });
    while (!clockEntered.load(std::memory_order_acquire)) std::this_thread::yield();
    require(state.IsFailed(), "original fault CAS precedes diagnostic publication");
    const auto pending = state.GetFirstFault();
    require(pending.pending && pending.kind == IBIngressFaultKind::None &&
        pending.monotonicNs == 0 && pending.connectionEpoch == 0 &&
        pending.admissionGeneration == 0, "pending reader never reads unpublished POD");
    state.MarkFault(IBIngressFaultKind::SdkCallException);
    // An already-faulted Idle no-op CAS must not publish a second record.
    require(state.Complete(), "close faulted original generation");
    state.ObserveBlockingCallback();
    state.SetDiagnosticConnectionEpoch(9);
    require(state.GetFirstFault().pending, "later reason/context cannot steal publication");
    releaseClock.store(true, std::memory_order_release); writer.join();
    const auto first = state.GetFirstFault();
    require(!first.pending && first.kind == IBIngressFaultKind::AuthoritativeQueueBusy &&
        first.monotonicNs == fixedClock() && first.connectionEpoch == 7 &&
        first.admissionGeneration == generation, "CAS winner publishes exact immutable original record");
    std::uint64_t deniedGeneration = 42;
    require(!state.Begin(deniedGeneration) && deniedGeneration == 0 &&
        !state.TryReserve(generation) && state.IsFailed(), "diagnostics never rearm authority");
    require(same(first, state.GetFirstFault()), "Begin/Complete/context never overwrite original record");
}
void recoverableAndReservedCallbacks() {
    for (bool reserved : {false, true}) {
        IBMarketDataAdmissionState state(fixedClock);
        state.SetDiagnosticConnectionEpoch(11);
        require(state.ObserveBlockingCallback(false, IBIngressFaultKind::FarmWarningDuringAdmission)
            == IBMarketDataAdmissionState::CallbackIgnored, "idle farm notice remains recoverable");
        require(!state.IsFailed() && state.GetFirstFault().kind == IBIngressFaultKind::None &&
            !state.GetFirstFault().pending, "recoverable notice creates no diagnostic fault");
        std::uint64_t generation = 0;
        require(state.Begin(generation), "open admission");
        if (reserved) require(state.TryReserve(generation), "reserve original send");
        const auto disposition = state.ObserveBlockingCallback(false,
            IBIngressFaultKind::FarmWarningDuringAdmission);
        require(disposition == (reserved ? IBMarketDataAdmissionState::CallbackAfterReservation :
            IBMarketDataAdmissionState::CallbackBeforeReservation), "original callback disposition unchanged");
        const auto first = state.GetFirstFault();
        require(first.kind == IBIngressFaultKind::FarmWarningDuringAdmission &&
            first.connectionEpoch == 11 && first.admissionGeneration == generation,
            "callback fault captures original generation");
        if (reserved) require(state.EndSend(generation, false), "settle original reserved send");
        require(state.Complete() && state.IsFailed() && same(first, state.GetFirstFault()),
            "settlement retains fault and first cause");
    }
    IBMarketDataAdmissionState idle(fixedClock);
    idle.ObserveBlockingCallback();
    const auto first = idle.GetFirstFault();
    idle.ObserveBlockingCallback(); idle.MarkFault();
    require(first.kind == IBIngressFaultKind::BlockingCallback &&
        first.admissionGeneration == 0 && same(first, idle.GetFirstFault()),
        "faulted Idle repeated successful no-op CAS cannot overwrite");
}
void categoriesAndAllocation() {
    for (const auto kind : {IBIngressFaultKind::BlockingCallback,
            IBIngressFaultKind::FarmWarningDuringAdmission,
            IBIngressFaultKind::AuthoritativeQueueBusy,
            IBIngressFaultKind::AuthoritativeQueueOverflow,
            IBIngressFaultKind::CallbackPublishException,
            IBIngressFaultKind::DeferredQueueBusy,
            IBIngressFaultKind::DeferredQueueOverflow,
            IBIngressFaultKind::DeferredPublishException,
            IBIngressFaultKind::SdkCallException,
            IBIngressFaultKind::AdmissionLifecycle,
            IBIngressFaultKind::DisconnectDuringAdmission}) {
        IBMarketDataAdmissionState state;
        state.SetDiagnosticConnectionEpoch(19);
        IBIngressFirstFault value;
        {
            NoAllocation guard;
            state.MarkFault(kind); value = state.GetFirstFault();
            state.MarkFault(IBIngressFaultKind::AdmissionLifecycle);
            state.Complete(); state.ObserveBlockingCallback();
        }
        require(value.kind == kind && value.monotonicNs > 0 && value.connectionEpoch == 19 &&
            !value.pending && same(value, state.GetFirstFault()), "every fixed kind remains immutable");
        require(std::strlen(IBIngressFaultKindName(kind)) > 0, "fixed nonallocating kind name");
    }
    IBMarketDataAdmissionState fresh(fixedClock);
    fresh.SetDiagnosticConnectionEpoch(20);
    require(!fresh.IsFailed() && !fresh.GetFirstFault().pending &&
        fresh.GetFirstFault().kind == IBIngressFaultKind::None, "only a fresh state starts clean");
}
void concurrentSnapshots() {
    for (unsigned round = 0; round != 128; ++round) {
        IBMarketDataAdmissionState state(fixedClock);
        state.SetDiagnosticConnectionEpoch(23);
        std::uint64_t generation = 0; require(state.Begin(generation), "concurrent generation");
        std::atomic<bool> go{false};
        auto write = [&](IBIngressFaultKind kind) {
            while (!go.load(std::memory_order_acquire)) std::this_thread::yield();
            NoAllocation guard; state.MarkFault(kind);
        };
        std::thread a(write, IBIngressFaultKind::AuthoritativeQueueBusy);
        std::thread b(write, IBIngressFaultKind::SdkCallException);
        go.store(true, std::memory_order_release);
        for (unsigned i = 0; i != 64; ++i) {
            const auto value = state.GetFirstFault();
            if (value.kind != IBIngressFaultKind::None)
                require(!value.pending && value.connectionEpoch == 23 &&
                    value.admissionGeneration == generation && value.monotonicNs == fixedClock(),
                    "acquire reads one complete record");
        }
        a.join(); b.join();
        const auto first = state.GetFirstFault();
        require((first.kind == IBIngressFaultKind::AuthoritativeQueueBusy ||
            first.kind == IBIngressFaultKind::SdkCallException) && !first.pending,
            "exactly one real fault CAS winner records its kind");
        state.SetDiagnosticConnectionEpoch(24); state.MarkFault();
        require(same(first, state.GetFirstFault()), "later context does not relabel published record");
    }
}
}
void* operator new(std::size_t size) {
    if (denyAllocation) { deniedAllocations.fetch_add(1); throw std::bad_alloc(); }
    if (void* p = std::malloc(size ? size : 1)) return p;
    throw std::bad_alloc();
}
void* operator new[](std::size_t size) { return ::operator new(size); }
void operator delete(void* p) noexcept { std::free(p); }
void operator delete[](void* p) noexcept { std::free(p); }

class NoBrokerApi final : public IIBApiWrapper {
public:
    mutable IBMarketDataAdmissionState state;
    std::thread publication;
    mutable bool faultWhenRead = false;
    std::uint64_t epoch = 31;
    bool allowFixtureConnect = false;
    explicit NoBrokerApi(bool pause, bool allowConnect = false)
        : state(pause ? pausedClock : fixedClock), allowFixtureConnect(allowConnect) {
        state.SetDiagnosticConnectionEpoch(epoch);
    }
    void SetConnectionEpoch(std::uint64_t value) override {
        epoch = value; state.SetDiagnosticConnectionEpoch(value);
    }
    std::uint64_t GetConnectionEpoch() const override { return epoch; }
    ~NoBrokerApi() override { if (publication.joinable()) publication.join(); }
    bool EventIngressAdmissionFailed() const override { return state.IsFailed(); }
    IBIngressFirstFault GetIngressFirstFault() const override {
        // Deterministic callback between adapter's failed and record reads.
        if (faultWhenRead) {
            faultWhenRead = false;
            state.MarkFault(IBIngressFaultKind::BlockingCallback);
        }
        return state.GetFirstFault();
    }
    void Disconnect() override {
        releaseClock.store(true, std::memory_order_release);
        if (publication.joinable()) publication.join();
        state.MarkFault(IBIngressFaultKind::DisconnectDuringAdmission);
    }
    bool Connect(const IBConnectParams&) override {
        if (!allowFixtureConnect) throw std::runtime_error("BROKER_FORBIDDEN");
        return true; // Explicit in-memory reconnect lifecycle fixture only.
    }
    bool IsConnected() const override { return false; }
    const char* GetStatusString() const override { return "NO_BROKER_FIXTURE"; }
    bool ReqAccountSummary() override { throw std::runtime_error("ACCOUNT_FORBIDDEN"); }
    bool ReqPositions() override { throw std::runtime_error("POSITIONS_FORBIDDEN"); }
    bool ReqMktData(int, const IBContractLite&) override { throw std::runtime_error("MARKET_DATA_FORBIDDEN"); }
    bool CancelMktData(int) override { throw std::runtime_error("MARKET_DATA_FORBIDDEN"); }
    bool PlaceOrder(long, const IBContractLite&, const IBOrderLite&) override { throw std::runtime_error("ORDER_FORBIDDEN"); }
    bool CancelOrder(long) override { throw std::runtime_error("ORDER_FORBIDDEN"); }
    bool PollOnce(int) override { throw std::runtime_error("BROKER_POLL_FORBIDDEN"); }
    bool TryDequeueEvent(IBEvent&) override { return false; }
    long GetLastValidOrderId() const override { return -1; }
};
// Reuse the existing socket-free runtime fixture friendship; no new production
// access or test hook is introduced to the runtime shutdown path.
struct IbQuoteRecoveryFixture {
    static void observationCannotDiscardAConcurrentFault() {
        auto api = std::unique_ptr<NoBrokerApi>(new NoBrokerApi(false));
        api->faultWhenRead = true;
        HeptaIBGatewayAdapter adapter(std::move(api));
        bool failed = false; std::uint64_t epoch = 0;
        const auto observed = adapter.GetIngressFirstFault(&epoch, &failed);
        require(failed && epoch == 31 && observed.kind == IBIngressFaultKind::BlockingCallback,
            "record observed before Stop is retained even when initial failed sample was false");
    }
    static void shutdownRetainsOnlyPreexistingFault(bool preexisting) {
        clockEntered.store(false); releaseClock.store(false);
        IbPaperExecutionRuntimeConfig config;
        IbPaperExecutionRuntimeComposition runtime(config);
        auto api = std::unique_ptr<NoBrokerApi>(new NoBrokerApi(preexisting));
        auto* original = api.get();
        runtime.m_adapter.reset(new HeptaIBGatewayAdapter(std::move(api)));
        if (preexisting) {
            original->publication = std::thread([&] {
                NoAllocation guard;
                original->state.MarkFault(IBIngressFaultKind::AuthoritativeQueueBusy);
            });
            while (!clockEntered.load(std::memory_order_acquire)) std::this_thread::yield();
        }
        bool failedBeforeStop = false; std::uint64_t beforeEpoch = 0;
        const auto before = runtime.Adapter().GetIngressFirstFault(&beforeEpoch, &failedBeforeStop);
        require(failedBeforeStop == preexisting && beforeEpoch == 31,
            "shutdown cannot claim its own future fault");
        require(before.pending == preexisting, "fixture starts with exact original pending state");
        runtime.Stop();
        require(runtime.Adapter().EventIngressAdmissionFailed(), "fixture shutdown produces a fault");
        if (failedBeforeStop) {
            const auto fault = runtime.Adapter().GetIngressFirstFault();
            require(fault.kind == IBIngressFaultKind::AuthoritativeQueueBusy && !fault.pending &&
                fault.connectionEpoch == 31, "real Stop joins publication and retains original wrapper record");
        }
    }
    static void shutdownCannotRelabelReplacedWrapper(bool initiallyPending) {
        clockEntered.store(false); releaseClock.store(false);
        IbPaperExecutionRuntimeConfig config;
        IbPaperExecutionRuntimeComposition runtime(config);
        auto api = std::unique_ptr<NoBrokerApi>(new NoBrokerApi(initiallyPending, true));
        auto* original = api.get();
        const auto factory = []() -> std::unique_ptr<IIBApiWrapper> {
            return std::unique_ptr<IIBApiWrapper>(new NoBrokerApi(false, true));
        };
        runtime.m_adapter.reset(new HeptaIBGatewayAdapter(std::move(api), factory));
        require(runtime.Adapter().Connect(), "first fake connection establishes original wrapper");
        if (initiallyPending) {
            original->publication = std::thread([&] {
                NoAllocation guard;
                original->state.MarkFault(IBIngressFaultKind::AuthoritativeQueueBusy);
            });
            while (!clockEntered.load(std::memory_order_acquire)) std::this_thread::yield();
        } else original->state.MarkFault(IBIngressFaultKind::AuthoritativeQueueBusy);
        bool failedBeforeStop = false; std::uint64_t beforeEpoch = 0;
        auto chosen = runtime.Adapter().GetIngressFirstFault(&beforeEpoch, &failedBeforeStop);
        require(failedBeforeStop && chosen.pending == initiallyPending, "capture original before Stop");
        // Reproduce an already-entered reconnect finishing in Stop's join.
        runtime.m_pollThread = std::thread([&] {
            releaseClock.store(true, std::memory_order_release);
            require(runtime.Adapter().Connect(), "fake poll reconnect replaces wrapper");
        });
        runtime.Stop();
        std::uint64_t afterEpoch = 0;
        const auto after = runtime.Adapter().GetIngressFirstFault(&afterEpoch);
        require(afterEpoch > beforeEpoch && after.kind == IBIngressFaultKind::DisconnectDuringAdmission,
            "real adapter factory creates a new wrapper/epoch during Stop");
        chosen = CompleteIngressFirstFaultObservation(chosen, beforeEpoch, after, afterEpoch);
        if (initiallyPending)
            require(chosen.pending && chosen.kind == IBIngressFaultKind::None,
                "unknown original remains pending instead of acquiring new wrapper's fault");
        else require(!chosen.pending && chosen.kind == IBIngressFaultKind::AuthoritativeQueueBusy &&
                chosen.connectionEpoch == beforeEpoch,
                "already-published original is retained across wrapper replacement");
    }
};
int main() {
    try {
        pendingIsNotASecondWritersRecord(); recoverableAndReservedCallbacks();
        categoriesAndAllocation(); concurrentSnapshots();
        IbQuoteRecoveryFixture::observationCannotDiscardAConcurrentFault();
        IbQuoteRecoveryFixture::shutdownRetainsOnlyPreexistingFault(true);
        IbQuoteRecoveryFixture::shutdownRetainsOnlyPreexistingFault(false);
        IbQuoteRecoveryFixture::shutdownCannotRelabelReplacedWrapper(true);
        IbQuoteRecoveryFixture::shutdownCannotRelabelReplacedWrapper(false);
        require(deniedAllocations.load() == 0, "fault path performed no allocation");
        std::cout << "PASS ingress first-fault CAS/publication/lifecycle/no-allocation tests\n";
        return 0;
    } catch (const std::exception& e) {
        std::cerr << e.what() << '\n'; return 1;
    }
}
