#pragma once

#include "../execution/trading_contract.h"

#include <string>
#include <vector>
#include <cstdint>
#include <cstddef>
#include <atomic>
#include <limits>
#include <memory>
#include <mutex>
#include <queue>

struct IBConnectParams {
    std::string host = "127.0.0.1";
    int port = 7497;
    int clientId = 101;
    // The dedicated PAPER client is scoped to exactly one configured DU
    // account.  Production uses this identity for reqAccountUpdates so CASH
    // FX balances cannot be aggregated across accounts.
    std::string account;
    bool readOnly = false;
};

enum class IBEventType {
    None = 0,
    Connected,
    ConnectionClosed,
    NextValidId,
    TickPrice,
    OrderStatus,
    Error,
    AccountValue,
    PositionSnapshotItem,
    // Source-compatibility alias for older consumers. New code should use
    // PositionSnapshotItem so it cannot be confused with PortfolioUpdate.
    Position = PositionSnapshotItem,
    PositionEnd,
    // Same-request reqPositionsMulti callback observed after its initial
    // positionMultiEnd. Consumers compare it with the committed snapshot and
    // invalidate on change; it never mutates a completed generation in place.
    PositionMonitorUpdate,
    AccountSummaryEnd,
    PortfolioUpdate,
    OpenOrder,
    OpenOrderEnd,
    // Broker-owned terminal evidence. CompletedOrder carries the original
    // Order.orderRef so it can be joined to the service-owned correlation;
    // ExecutionDetails carries the broker order id but never invents a
    // correlation on its own.
    CompletedOrder,
    CompletedOrdersEnd,
    ExecutionDetails,
    ExecutionDetailsEnd,
    EventQueueOverflow
};

struct IBEvent {
    IBEventType type = IBEventType::None;
    long long id = 0;
    // Request identity for callbacks whose broker protocol supplies one
    // (notably execDetails/execDetailsEnd). It must not be overloaded with the
    // broker order id in `id`.
    long long requestId = 0;
    std::uint64_t connectionEpoch = 0;
    std::string account;
    std::string key;
    std::string value;
    // Broker callback diagnostics. These fields deliberately remain separate
    // from key/value so consumers cannot accidentally discard whyHeld or the
    // advanced reject payload while interpreting the callback identity.
    std::string whyHeld;
    std::string advancedOrderRejectJson;
    double number = 0.0;
    double number2 = 0.0;
    double number3 = 0.0;
    double marketCapPrice = 0.0;
    IBContractLite contract;
    IBOrderLite order;
    std::uint64_t overflowGeneration = 0;
    std::uint64_t droppedEventCount = 0;
};

// Auxiliary transport shutdown observations. These fields are never an
// economic post-cutoff witness: IB does not expose a causal/atomic boundary
// joining EReader state with account-wide orders, executions and positions.
struct IBTerminalTransportDrainWitness {
    bool ingressHalted = false;
    bool readerStopped = false;
    bool rawMessageQueueDrained = false;
    bool callbackEventQueueDrained = false;
    bool eventQueueOverflowed = false;
    std::uint64_t rawMessagesDrained = 0;
    std::uint64_t callbacksInFlight = 0;
};

struct IBBrokerConnectionIdentity {
    std::uint64_t connectionEpoch = 0;
    std::string canonical;
};

// Test-only access peer used by the authoritative marker regression tests to
// hold the tiny callback lock while a control-thread reset is in flight.  The
// peer has no production methods or state; keeping the friendship explicit is
// preferable to exposing a debug lock API on the adapter surface.
class IBCashFarmAdmissionMarkerTestAccess;

// A bounded event queue whose data loss can never be silent. When capacity is
// exceeded, TryDequeueEvent() returns an EventQueueOverflow notification before
// any remaining data event. Consumers must invalidate derived state and perform
// a complete broker resynchronization before enabling mutations again.
class IBAuthoritativeEventQueue {
public:
    explicit IBAuthoritativeEventQueue(std::size_t maxEvents);

    void Push(IBEvent event);
    // Non-blocking producer path used at the SDK callback boundary.  A
    // false return means the queue mutex was busy; callers must retain an
    // explicit loss/fault witness and publish it from a consumer/control
    // thread rather than waiting in EReader.
    bool TryPush(IBEvent event, bool& overflowed);
    // Record a producer-side drop after the callback has left the boundary.
    // This is deliberately separate from Push so a callback that could not
    // acquire the queue mutex never has to block merely to report loss.
    void RecordDroppedEvent(std::uint64_t connectionEpoch,
                            std::uint64_t count = 1);
    bool TryDequeueEvent(IBEvent& outEvent);

    std::size_t Size() const;
    std::uint64_t OverflowGeneration() const;
    std::uint64_t DroppedEventCount() const;

private:
    const std::size_t m_maxEvents;
    mutable std::mutex m_mutex;
    std::queue<IBEvent> m_events;
    std::uint64_t m_overflowGeneration = 0;
    std::uint64_t m_reportedOverflowGeneration = 0;
    std::uint64_t m_droppedEventCount = 0;
    std::uint64_t m_latestConnectionEpoch = 0;
};

// reqPositions() has no request identity, so a delayed positionEnd from an
// older download can incorrectly complete a newer authoritative generation.
// The dedicated PAPER wrapper uses reqPositionsMulti and this monotonic fence
// to accept callbacks from exactly the currently active request.  It is kept
// independent of the IB SDK so the stale-callback boundary is unit testable in
// non-IB builds.
class IBPositionsRequestFence {
public:
    explicit IBPositionsRequestFence(int firstRequestId = 12001);

    // Starts a new generation. `supersededRequestId` is non-zero when the
    // caller must cancel a previous broker subscription first.
    bool Begin(int& requestId, int& supersededRequestId);
    bool IsCurrent(int requestId) const;
    bool Complete(int requestId);
    int ActiveRequestId() const;

private:
    int m_nextRequestId;
    int m_activeRequestId = 0;
};

// Monotonic CASH-farm readiness marker used at the callback boundary.  IB may
// deliver a 2104 acknowledgement after a newer 2119 warning (or a late
// callback from an older connection epoch).  The marker therefore compares
// both the connection epoch and a callback sequence, rather than letting a
// plain ready bool erase a newer warning.  Callback observations take a
// single try-only atomic lock; contention is converted to a sticky pending
// witness instead of making EReader wait.  Reset() is a control-thread
// operation and may briefly wait for that tiny critical section.
class IBCashFarmAdmissionMarker {
public:
    IBCashFarmAdmissionMarker();

    // Starts a fresh marker epoch.  Until the reset is complete IsPending()
    // remains conservative; a callback racing the reset can only leave the
    // marker pending/unsafe.
    void Reset(std::uint64_t connectionEpoch);
    // Observe callbacks in the wrapper's total callback order.  A warning or
    // ready with an older epoch is ignored; a future epoch is unsafe and
    // remains pending until the next Reset().
    void ObserveWarning(std::uint64_t connectionEpoch,
                        std::uint64_t callbackSequence);
    void ObserveReady(std::uint64_t connectionEpoch,
                      std::uint64_t callbackSequence);
    // Returns true on any epoch mismatch, contention, malformed sequence, or
    // an un-cleared warning.  This fail-closed read is lock-free.
    bool IsPending(std::uint64_t connectionEpoch) const;
    // Returns true only after a positive CASH-farm 2104 has been observed in
    // this exact connection epoch and no later 2119/unsafe witness exists.
    // Unlike IsPending(), a freshly reset marker is not considered ready.
    bool IsReady(std::uint64_t connectionEpoch) const;
    void MarkUnsafe();

private:
    friend class IBCashFarmAdmissionMarkerTestAccess;

    bool TryLockFromCallback() const;
    void LockFromControl() const;
    void Unlock() const;
    void MarkContentionPending() const;
    void Observe(std::uint64_t connectionEpoch,
                 std::uint64_t callbackSequence,
                 bool warning);

    mutable std::atomic_flag m_lock = ATOMIC_FLAG_INIT;
    // Odd values mean a control-thread Reset is in progress.  Callback-side
    // faults observed during that interval must survive the reset's state
    // rebind; the even value is the quiescent generation.
    mutable std::atomic<std::uint64_t> m_resetGeneration;
    // Every callback-side unsafe/contention witness increments this serial.
    // Reset snapshots it before taking the lock and compares it again before
    // publishing the new quiescent state, so it cannot overwrite a witness
    // from the same reset generation.
    mutable std::atomic<std::uint64_t> m_callbackFaultSerial;
    std::atomic<std::uint64_t> m_epoch;
    std::atomic<std::uint64_t> m_warningSequence;
    std::atomic<std::uint64_t> m_readySequence;
    mutable std::atomic<bool> m_pending;
    mutable std::atomic<bool> m_unsafe;
};

// Sender/callback linearization state for a formal ReqMktData admission.
//
// The state word carries both a monotonically increasing admission generation
// and its phase.  A blocking callback CASes OPEN -> BLOCKED before a sender
// can reserve, or RESERVED -> RESERVED_BAD after the sender has won.  The
// latter is deliberately sticky until the transaction closes: the request
// may have reached IB, so callers must clean it up and fail closed.  Callbacks
// only perform atomics before touching the deferred queue; they never wait on
// the sender or the SDK/EReader join path.
class IBMarketDataAdmissionState {
public:
    enum Phase {
        Idle = 0,
        Open = 1,
        Blocked = 2,
        Reserved = 3,
        ReservedBad = 4
    };

    enum CallbackDisposition {
        CallbackIgnored = 0,
        CallbackBeforeReservation = 1,
        CallbackAfterReservation = 2
    };

    IBMarketDataAdmissionState();

    // Begins a fresh generation. Returns false unless the previous state is a
    // clean Idle state and the generation counter is not exhausted.  A
    // pending fault is deliberately not clearable by Begin; callers need a
    // fresh connection epoch or an explicit higher-level rearm.
    bool Begin(std::uint64_t& generation);
    // The successful CAS is the unique sender linearization point.
    bool TryReserve(std::uint64_t generation);
    // Applies a blocking callback without taking a mutex.  By default an
    // event observed while Idle is retained as a pending fault so it cannot
    // fall through the Begin boundary between the callback's observation and
    // the next send.  A recoverable pre-admission control notice may pass
    // false: its Idle observation is validated with a no-op CAS, and a
    // concurrent Begin/Reserve is still re-evaluated and blocked.
    CallbackDisposition ObserveBlockingCallback(bool faultWhileIdle = true);
    // Records a non-callback ingress failure (for example queue contention or
    // overflow) in the same packed state word used by callback faults.
    bool MarkFault();
    // Ends one SDK send. A ReservedBad send can only settle to Blocked.
    bool EndSend(std::uint64_t generation, bool keepAdmissionOpen);
    // Completes the two-phase admission close (after the external fence is
    // released), retaining the fault bit for diagnostics until the next Begin.
    bool Complete();
    bool IsFailed() const;
    bool IsDeferred() const;
    // Atomically samples phase, generation, and fault from one state word.
    void Snapshot(Phase& phase, std::uint64_t& generation,
                  bool& fault) const;
    Phase GetPhase() const;
    std::uint64_t Generation() const;

private:
    static const unsigned kPhaseBits = 4;
    static const std::uint64_t kPhaseValueMask = 0x7ULL;
    static const std::uint64_t kFaultBit =
        static_cast<std::uint64_t>(1) << 3;
    static const std::uint64_t kGenerationMax =
        std::numeric_limits<std::uint64_t>::max() >> kPhaseBits;

    static std::uint64_t Encode(
        std::uint64_t generation, Phase phase, bool fault);
    static Phase DecodePhase(std::uint64_t state);
    static std::uint64_t DecodeGeneration(std::uint64_t state);
    static bool DecodeFault(std::uint64_t state);

    std::atomic<std::uint64_t> m_state;
};

class IIBApiWrapper {
public:
    virtual ~IIBApiWrapper() = default;

    virtual bool Connect(const IBConnectParams& p) = 0;
    virtual void SetConnectionEpoch(std::uint64_t) {}
    // Runtime quote admission holds this fence across its final callback
    // drain and formal ReqMktData dispatch. The production wrapper uses its
    // admission state as the callback boundary; it must not block an EReader
    // shutdown join on this external mutex.
    virtual void SetEventIngressFence(
        const std::shared_ptr<std::recursive_mutex>&) {}
    // Quote admission is a short, externally fenced transaction.  Callback
    // producers must not block on the fence (the IB send path can synchronously
    // disconnect and join the EReader thread); instead they defer callbacks
    // until EndEventIngressAdmission() flushes them into the authoritative
    // queue.  The default no-op keeps legacy test wrappers source-compatible.
    virtual void BeginEventIngressAdmission() {}
    // Prepare-close while the caller still owns EventIngressFence().  The
    // implementation may keep producer admission marked as closing until
    // CompleteEventIngressAdmission() is called after fence release.
    virtual void EndEventIngressAdmission() {}
    // Flush callbacks deferred so far while keeping the admission fence
    // active.  This lets the caller perform a stable post-dispatch drain
    // before publishing quote readiness.
    virtual void FlushEventIngressAdmission() {}
    // Completes the admission handoff after the caller releases its external
    // fence.  The two-phase close keeps callbacks that started during the
    // release gap deferred until they have been flushed, avoiding a
    // final-drain race.  Legacy wrappers may keep the default no-op behavior.
    virtual void CompleteEventIngressAdmission() {}
    // A callback received during admission can make the broker witness unsafe
    // for any remaining request in the same plan.  Callers may stop dispatch
    // early; the deferred callback is still flushed by End...().
    virtual bool EventIngressAdmissionFailed() const { return false; }
    virtual std::uint64_t GetConnectionEpoch() const { return 0; }
    virtual void Disconnect() = 0;
    virtual bool IsConnected() const = 0;
    virtual const char* GetStatusString() const = 0;
    virtual bool GetBrokerConnectionIdentity(
        IBBrokerConnectionIdentity&, std::string& reason) const
    {
        reason = "IB_BROKER_SOCKET_IDENTITY_UNAVAILABLE";
        return false;
    }

    virtual bool ReqAccountSummary() = 0;
    virtual bool ReqPositions() = 0;
    // Default keeps existing test/mocked wrappers source-compatible while the
    // production implementation exposes broker open-order authority.
    virtual bool ReqOpenOrders() { return false; }
    // Account-wide authority for the dedicated execution service. Unlike
    // reqOpenOrders(), this includes orders submitted by other client IDs.
    virtual bool ReqAllOpenOrders() { return false; }
    // These two queries form the positive-only terminal authority. Their
    // default failure preserves source compatibility with legacy/fake wrappers
    // and forces callers to remain fail-closed.
    virtual bool ReqCompletedOrders() { return false; }
    virtual bool ReqExecutions(int) { return false; }
    virtual bool ReqMktData(int reqId, const IBContractLite& c) = 0;
    virtual bool CancelMktData(int reqId) = 0;

    virtual bool PlaceOrder(long localOrderId, const IBContractLite& c, const IBOrderLite& o) = 0;
    virtual bool CancelOrder(long localOrderId) = 0;

    virtual bool PollOnce(int timeoutMs) = 0;

    // Terminal-only transport operation. Generic Disconnect and an EReader
    // queue drain are deliberately not economic evidence. Until a signed
    // post-cutoff authority is supplied, every implementation fails closed.
    virtual bool HaltAndDrainTerminalTransport(
        std::vector<IBEvent>&,
        IBTerminalTransportDrainWitness&,
        std::string& reason)
    {
        reason = "POST_CUTOFF_SIGNED_WITNESS_REQUIRED";
        return false;
    }

    virtual bool TryDequeueEvent(IBEvent& outEvent) = 0;
    virtual long GetLastValidOrderId() const = 0;
};

// Factory: HEPTA_ENABLE_IBAPI selects the linked IB implementation; otherwise
// it returns the deterministic safe stub.
IIBApiWrapper* CreateIBApiWrapper();
