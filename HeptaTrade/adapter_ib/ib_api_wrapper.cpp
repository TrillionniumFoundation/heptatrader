#include "ib_api_wrapper.h"

#include <algorithm>
#include <atomic>
#include <thread>
#include <chrono>
#include <memory>
#include <queue>
#include <deque>
#include <mutex>
#include <fstream>
#include <cstdlib>
#include <cstring>
#include <cctype>
#include <cmath>
#include <sstream>
#include <iomanip>
#include <utility>
#include <unordered_map>
#include <limits>
#include <cerrno>
#include <sys/socket.h>
#include <sys/stat.h>
#include <unistd.h>

IBAuthoritativeEventQueue::IBAuthoritativeEventQueue(std::size_t maxEvents)
    : m_maxEvents(maxEvents == 0 ? 1 : maxEvents) {
}

void IBAuthoritativeEventQueue::Push(IBEvent event) {
    std::lock_guard<std::mutex> lock(m_mutex);
    // Callback publication can be delayed across a reconnect.  Keep the
    // overflow witness bound to the newest epoch observed by the queue; a
    // late older event must never roll that identity backwards.
    if (event.connectionEpoch > m_latestConnectionEpoch)
        m_latestConnectionEpoch = event.connectionEpoch;
    if (m_events.size() >= m_maxEvents) {
        m_events.pop();
        ++m_droppedEventCount;
        ++m_overflowGeneration;
    }
    m_events.push(std::move(event));
}

bool IBAuthoritativeEventQueue::TryPush(
    IBEvent event, bool& overflowed) {
    overflowed = false;
    std::unique_lock<std::mutex> lock(m_mutex, std::try_to_lock);
    if (!lock.owns_lock()) return false;
    if (event.connectionEpoch > m_latestConnectionEpoch)
        m_latestConnectionEpoch = event.connectionEpoch;
    if (m_events.size() >= m_maxEvents) {
        m_events.pop();
        ++m_droppedEventCount;
        ++m_overflowGeneration;
        overflowed = true;
    }
    m_events.push(std::move(event));
    return true;
}

void IBAuthoritativeEventQueue::RecordDroppedEvent(
    std::uint64_t connectionEpoch, std::uint64_t count) {
    if (count == 0) return;
    std::lock_guard<std::mutex> lock(m_mutex);
    if (connectionEpoch > m_latestConnectionEpoch)
        m_latestConnectionEpoch = connectionEpoch;
    m_droppedEventCount += count;
    ++m_overflowGeneration;
}

bool IBAuthoritativeEventQueue::TryDequeueEvent(IBEvent& outEvent) {
    std::lock_guard<std::mutex> lock(m_mutex);
    if (m_reportedOverflowGeneration < m_overflowGeneration) {
        outEvent = IBEvent{};
        outEvent.type = IBEventType::EventQueueOverflow;
        outEvent.connectionEpoch = m_latestConnectionEpoch;
        outEvent.key = "EVENT_QUEUE_OVERFLOW";
        outEvent.value = "AUTHORITATIVE_STATE_INVALID_REQUIRES_RESYNC";
        outEvent.overflowGeneration = m_overflowGeneration;
        outEvent.droppedEventCount = m_droppedEventCount;
        const std::uint64_t maxId = static_cast<std::uint64_t>(std::numeric_limits<long long>::max());
        outEvent.id = static_cast<long long>(m_overflowGeneration > maxId ? maxId : m_overflowGeneration);
        outEvent.number = static_cast<double>(m_droppedEventCount);
        m_reportedOverflowGeneration = m_overflowGeneration;
        return true;
    }
    if (m_events.empty()) return false;
    outEvent = std::move(m_events.front());
    m_events.pop();
    return true;
}

std::size_t IBAuthoritativeEventQueue::Size() const {
    std::lock_guard<std::mutex> lock(m_mutex);
    return m_events.size();
}

std::uint64_t IBAuthoritativeEventQueue::OverflowGeneration() const {
    std::lock_guard<std::mutex> lock(m_mutex);
    return m_overflowGeneration;
}

std::uint64_t IBAuthoritativeEventQueue::DroppedEventCount() const {
    std::lock_guard<std::mutex> lock(m_mutex);
    return m_droppedEventCount;
}

IBPositionsRequestFence::IBPositionsRequestFence(int firstRequestId)
    : m_nextRequestId(firstRequestId) {
}

bool IBPositionsRequestFence::Begin(
    int& requestId,
    int& supersededRequestId) {
    requestId = 0;
    supersededRequestId = 0;
    if (m_nextRequestId <= 0 ||
        m_nextRequestId == std::numeric_limits<int>::max()) {
        return false;
    }
    supersededRequestId = m_activeRequestId;
    m_activeRequestId = m_nextRequestId++;
    requestId = m_activeRequestId;
    return true;
}

bool IBPositionsRequestFence::IsCurrent(int requestId) const {
    return requestId > 0 && requestId == m_activeRequestId;
}

bool IBPositionsRequestFence::Complete(int requestId) {
    if (!IsCurrent(requestId)) return false;
    m_activeRequestId = 0;
    return true;
}

int IBPositionsRequestFence::ActiveRequestId() const {
    return m_activeRequestId;
}

IBCashFarmAdmissionMarker::IBCashFarmAdmissionMarker()
    : m_resetGeneration(0),
      m_callbackFaultSerial(0),
      m_epoch(0),
      m_warningSequence(0),
      m_readySequence(0),
      m_pending(false),
      m_unsafe(false) {
}

void IBCashFarmAdmissionMarker::LockFromControl() const {
    while (m_lock.test_and_set(std::memory_order_acquire))
        std::this_thread::yield();
}

bool IBCashFarmAdmissionMarker::TryLockFromCallback() const {
    if (!m_lock.test_and_set(std::memory_order_acquire)) return true;
    // The EReader path must never wait for a control-thread reset or another
    // callback.  Treat contention as an ordering uncertainty and keep the
    // marker pending until the next explicit epoch reset.
    MarkContentionPending();
    return false;
}

void IBCashFarmAdmissionMarker::Unlock() const {
    m_lock.clear(std::memory_order_release);
}

void IBCashFarmAdmissionMarker::MarkContentionPending() const {
    // Publish a fault serial before the sticky bits.  Reset() snapshots this
    // serial before waiting on the callback lock and rechecks it before
    // clearing the new marker, which closes the reset/failed-TryLock race.
    (void)m_callbackFaultSerial.fetch_add(1, std::memory_order_acq_rel);
    m_unsafe.store(true, std::memory_order_release);
    m_pending.store(true, std::memory_order_release);
}

void IBCashFarmAdmissionMarker::Reset(std::uint64_t connectionEpoch) {
    // Snapshot the callback witness before announcing the odd reset
    // generation.  If the snapshot were taken after that announcement, a
    // callback could observe the odd value, record contention, and then be
    // mistakenly treated as part of the reset baseline; the subsequent
    // serial comparison would erase that same-generation witness.
    const std::uint64_t faultSerialAtStart =
        m_callbackFaultSerial.load(std::memory_order_acquire);
    // Announce an odd reset generation before taking the lock.  IsPending()
    // rejects during this interval, while a callback that cannot acquire the
    // lock records a fault included in the serial recheck below.
    (void)m_resetGeneration.fetch_add(1, std::memory_order_acq_rel);
    LockFromControl();
    // Publish a conservative state while the fields are being re-bound.  A
    // concurrent IsPending() can therefore only reject, never admit, during
    // this short control transition.
    m_pending.store(true, std::memory_order_release);
    m_epoch.store(connectionEpoch, std::memory_order_release);
    m_warningSequence.store(0, std::memory_order_release);
    m_readySequence.store(0, std::memory_order_release);
    m_unsafe.store(false, std::memory_order_release);
    // A callback may have failed its try-only lock while Reset() was waiting
    // (or while the fields above were being rebound).  Do not let the reset
    // erase that same-generation witness.
    const bool callbackFaultDuringReset =
        m_callbackFaultSerial.load(std::memory_order_acquire) !=
        faultSerialAtStart;
    if (callbackFaultDuringReset)
        m_unsafe.store(true, std::memory_order_release);
    m_pending.store(callbackFaultDuringReset, std::memory_order_release);
    Unlock();
    // Leave the generation odd until after the lock is released.  A callback
    // racing the final unlock can still set the sticky fault bits; publishing
    // the even generation last prevents IsPending() from observing a
    // transiently admissible reset state.
    (void)m_resetGeneration.fetch_add(1, std::memory_order_release);
}

void IBCashFarmAdmissionMarker::Observe(
    std::uint64_t connectionEpoch,
    std::uint64_t callbackSequence,
    bool warning) {
    // Never inspect or mutate marker state while the control thread is
    // rebinding an epoch.  The callback-side fault serial makes this witness
    // visible to the in-flight Reset(), and the sticky bits keep it closed
    // afterwards until the next explicit reset.
    if (m_resetGeneration.load(std::memory_order_acquire) & 1ULL) {
        MarkContentionPending();
        return;
    }
    // The tiny callback lock provides the linearization point while Reset()
    // rebinds the epoch: a callback that acquires it before the control reset
    // is ordered before the reset, one that cannot acquire it records a
    // sticky fault, and one arriving after unlock observes the fully rebound
    // state.  No callback-side path waits.
    const std::uint64_t observedEpoch =
        m_epoch.load(std::memory_order_acquire);
    if (connectionEpoch < observedEpoch) {
        // A late callback from an older transport epoch cannot affect the
        // current farm lease (and, in particular, cannot clear it).
        return;
    }
    if (connectionEpoch != observedEpoch) {
        // A future/mismatched epoch indicates a broken handoff.  Keep the
        // sender closed until the owner performs a fresh Reset().
        MarkUnsafe();
        return;
    }
    if (callbackSequence == 0) {
        MarkUnsafe();
        return;
    }
    if (!TryLockFromCallback()) return;
    const std::uint64_t lockedEpoch =
        m_epoch.load(std::memory_order_acquire);
    if (connectionEpoch < lockedEpoch) {
        Unlock();
        return;
    }
    if (connectionEpoch != lockedEpoch) {
        MarkUnsafe();
        Unlock();
        return;
    }
    if (warning) {
        const std::uint64_t prior =
            m_warningSequence.load(std::memory_order_relaxed);
        if (callbackSequence > prior)
            m_warningSequence.store(
                callbackSequence, std::memory_order_release);
    } else {
        const std::uint64_t prior =
            m_readySequence.load(std::memory_order_relaxed);
        if (callbackSequence > prior)
            m_readySequence.store(
                callbackSequence, std::memory_order_release);
    }
    const std::uint64_t warningSequence =
        m_warningSequence.load(std::memory_order_relaxed);
    const std::uint64_t readySequence =
        m_readySequence.load(std::memory_order_relaxed);
    // The state is a derived comparison, not a last-writer-wins bool.  Thus a
    // 2104 with a lower sequence (or an older epoch) cannot erase a newer
    // 2119, even when callback execution order is reversed.
    if (m_unsafe.load(std::memory_order_relaxed))
        m_pending.store(true, std::memory_order_release);
    else
        m_pending.store(warningSequence > readySequence,
                        std::memory_order_release);
    Unlock();
}

void IBCashFarmAdmissionMarker::ObserveWarning(
    std::uint64_t connectionEpoch,
    std::uint64_t callbackSequence) {
    Observe(connectionEpoch, callbackSequence, true);
}

void IBCashFarmAdmissionMarker::ObserveReady(
    std::uint64_t connectionEpoch,
    std::uint64_t callbackSequence) {
    Observe(connectionEpoch, callbackSequence, false);
}

bool IBCashFarmAdmissionMarker::IsPending(
    std::uint64_t connectionEpoch) const {
    if (m_resetGeneration.load(std::memory_order_acquire) & 1ULL)
        return true;
    if (m_unsafe.load(std::memory_order_acquire) ||
        m_pending.load(std::memory_order_acquire)) return true;
    return m_epoch.load(std::memory_order_acquire) != connectionEpoch;
}

bool IBCashFarmAdmissionMarker::IsReady(
    std::uint64_t connectionEpoch) const {
    // IsPending() intentionally answers the older warning/ready ordering
    // question and treats a fresh reset as quiescent.  A request admission
    // needs the stronger positive witness: a non-zero ready sequence from
    // this epoch, with no warning or callback fault after it.
    if (connectionEpoch == 0 ||
        m_resetGeneration.load(std::memory_order_acquire) & 1ULL)
        return false;
    if (m_unsafe.load(std::memory_order_acquire) ||
        m_pending.load(std::memory_order_acquire) ||
        m_epoch.load(std::memory_order_acquire) != connectionEpoch)
        return false;
    const std::uint64_t ready =
        m_readySequence.load(std::memory_order_acquire);
    const std::uint64_t warning =
        m_warningSequence.load(std::memory_order_acquire);
    return ready != 0 && ready >= warning;
}

void IBCashFarmAdmissionMarker::MarkUnsafe() {
    MarkContentionPending();
}

IBMarketDataAdmissionState::IBMarketDataAdmissionState()
    : m_state(Encode(0, Idle, false)) {
}

std::uint64_t IBMarketDataAdmissionState::Encode(
    std::uint64_t generation, Phase phase, bool fault) {
    return (generation << kPhaseBits) |
        (fault ? kFaultBit : 0ULL) |
        (static_cast<std::uint64_t>(phase) & kPhaseValueMask);
}

IBMarketDataAdmissionState::Phase
IBMarketDataAdmissionState::DecodePhase(std::uint64_t state) {
    return static_cast<Phase>(state & kPhaseValueMask);
}

std::uint64_t IBMarketDataAdmissionState::DecodeGeneration(
    std::uint64_t state) {
    return state >> kPhaseBits;
}

bool IBMarketDataAdmissionState::DecodeFault(std::uint64_t state) {
    return (state & kFaultBit) != 0;
}

bool IBMarketDataAdmissionState::Begin(std::uint64_t& generation) {
    generation = 0;
    std::uint64_t observed = m_state.load(std::memory_order_acquire);
    for (;;) {
        const Phase phase = DecodePhase(observed);
        // A new admission may only start from a clean idle state.  In
        // particular, do not recycle a faulted/blocked generation: doing so
        // would let a callback observed just before Begin be crossed by a new
        // SDK request.  The caller must obtain a fresh wrapper/connection
        // epoch (or an explicit higher-level rearm) after such a witness.
        if (phase != Idle || DecodeFault(observed)) return false;
        const std::uint64_t previousGeneration =
            DecodeGeneration(observed);
        if (previousGeneration >= kGenerationMax) return false;
        const std::uint64_t nextGeneration = previousGeneration + 1;
        const std::uint64_t desired = Encode(nextGeneration, Open, false);
        if (m_state.compare_exchange_weak(
                observed, desired, std::memory_order_acq_rel,
                std::memory_order_acquire)) {
            generation = nextGeneration;
            return true;
        }
    }
}

bool IBMarketDataAdmissionState::TryReserve(std::uint64_t generation) {
    if (generation == 0) return false;
    const std::uint64_t expected = Encode(generation, Open, false);
    std::uint64_t compare = expected;
    return m_state.compare_exchange_strong(
        compare, Encode(generation, Reserved, false),
        std::memory_order_acq_rel, std::memory_order_acquire);
}

IBMarketDataAdmissionState::CallbackDisposition
IBMarketDataAdmissionState::ObserveBlockingCallback(bool faultWhileIdle) {
    std::uint64_t observed = m_state.load(std::memory_order_acquire);
    for (;;) {
        const Phase phase = DecodePhase(observed);
        const std::uint64_t generation = DecodeGeneration(observed);
        if (phase == Idle) {
            if (!faultWhileIdle) {
                // A recoverable farm-warning callback is allowed to be
                // observed while no formal admission is open.  Validate the
                // exact Idle word with a no-op CAS so a concurrent Begin()
                // cannot slip between this read and the decision; on CAS
                // failure the loop re-evaluates the new Open/Reserved phase
                // and blocks that admission instead.
                if (m_state.compare_exchange_weak(
                        observed, observed, std::memory_order_acq_rel,
                        std::memory_order_acquire))
                    return CallbackIgnored;
                continue;
            }
            // Do not ignore a blocking callback that wins the tiny boundary
            // race immediately before Begin().  Keep the generation idle but
            // faulted; Begin() will reject the next admission rather than
            // clearing this witness and issuing a request.
            const std::uint64_t desired = Encode(generation, Idle, true);
            if (m_state.compare_exchange_weak(
                    observed, desired, std::memory_order_acq_rel,
                    std::memory_order_acquire))
                return CallbackBeforeReservation;
            continue;
        }
        if (phase == Open) {
            const std::uint64_t desired = Encode(generation, Blocked, true);
            if (m_state.compare_exchange_weak(
                    observed, desired, std::memory_order_acq_rel,
                    std::memory_order_acquire))
                return CallbackBeforeReservation;
            continue;
        }
        if (phase == Reserved) {
            const std::uint64_t desired =
                Encode(generation, ReservedBad, true);
            if (m_state.compare_exchange_weak(
                    observed, desired, std::memory_order_acq_rel,
                    std::memory_order_acquire))
                return CallbackAfterReservation;
            continue;
        }
        if (phase == Blocked) {
            // Keep the fault bit sticky even if a corrupted/legacy state ever
            // reaches BLOCKED without it.
            if (DecodeFault(observed)) return CallbackBeforeReservation;
            const std::uint64_t desired = Encode(generation, Blocked, true);
            if (m_state.compare_exchange_weak(
                    observed, desired, std::memory_order_acq_rel,
                    std::memory_order_acquire))
                return CallbackBeforeReservation;
            continue;
        }
        if (phase == ReservedBad) {
            if (DecodeFault(observed)) return CallbackAfterReservation;
            const std::uint64_t desired =
                Encode(generation, ReservedBad, true);
            if (m_state.compare_exchange_weak(
                    observed, desired, std::memory_order_acq_rel,
                    std::memory_order_acquire))
                return CallbackAfterReservation;
            continue;
        }
        // Unknown phase is fail-closed and cannot be made admissible.  Fold
        // it into an idle-but-faulted witness so Complete/Begin can never
        // normalize corruption back to a clean send gate.
        (void)MarkFault();
        return CallbackBeforeReservation;
    }
}

bool IBMarketDataAdmissionState::MarkFault() {
    std::uint64_t observed = m_state.load(std::memory_order_acquire);
    for (;;) {
        const Phase phase = DecodePhase(observed);
        const std::uint64_t generation = DecodeGeneration(observed);
        Phase target = phase;
        switch (phase) {
        case Idle:
            target = Idle;
            break;
        case Open:
            target = Blocked;
            break;
        case Reserved:
            target = ReservedBad;
            break;
        case Blocked:
        case ReservedBad:
            target = phase;
            break;
        default:
            // Unknown state is already unsafe.  A canonical idle+fault value
            // is the only representable terminal form; never clear it.
            target = Idle;
            break;
        }
        const std::uint64_t desired = Encode(generation, target, true);
        if (desired == observed) return true;
        if (m_state.compare_exchange_weak(
                observed, desired, std::memory_order_acq_rel,
                std::memory_order_acquire)) return true;
    }
}

bool IBMarketDataAdmissionState::EndSend(
    std::uint64_t generation, bool keepAdmissionOpen) {
    if (generation == 0) return false;
    std::uint64_t observed = m_state.load(std::memory_order_acquire);
    for (;;) {
        if (DecodeGeneration(observed) != generation) return false;
        const Phase phase = DecodePhase(observed);
        const bool fault = DecodeFault(observed);
        Phase target = phase;
        if (phase == Reserved)
            target = keepAdmissionOpen ? Open : Idle;
        else if (phase == ReservedBad)
            target = Blocked;
        else
            return false;
        const std::uint64_t desired = Encode(generation, target, fault);
        if (m_state.compare_exchange_weak(
                observed, desired, std::memory_order_acq_rel,
                std::memory_order_acquire)) return true;
    }
}

bool IBMarketDataAdmissionState::Complete() {
    std::uint64_t observed = m_state.load(std::memory_order_acquire);
    for (;;) {
        const Phase phase = DecodePhase(observed);
        if (phase == Idle) return true;
        if (phase == Reserved || phase == ReservedBad) return false;
        if (phase != Open && phase != Blocked) {
            (void)MarkFault();
            return false;
        }
        const std::uint64_t desired = Encode(
            DecodeGeneration(observed), Idle, DecodeFault(observed));
        if (m_state.compare_exchange_weak(
                observed, desired, std::memory_order_acq_rel,
                std::memory_order_acquire)) return true;
    }
}

bool IBMarketDataAdmissionState::IsFailed() const {
    const std::uint64_t observed = m_state.load(std::memory_order_acquire);
    const Phase phase = DecodePhase(observed);
    return DecodeFault(observed) || phase == Blocked ||
        phase == ReservedBad ||
        (phase != Idle && phase != Open && phase != Reserved);
}

bool IBMarketDataAdmissionState::IsDeferred() const {
    const Phase phase = DecodePhase(
        m_state.load(std::memory_order_acquire));
    return phase != Idle;
}

void IBMarketDataAdmissionState::Snapshot(
    Phase& phase, std::uint64_t& generation, bool& fault) const {
    const std::uint64_t observed = m_state.load(std::memory_order_acquire);
    phase = DecodePhase(observed);
    generation = DecodeGeneration(observed);
    fault = DecodeFault(observed);
}

IBMarketDataAdmissionState::Phase
IBMarketDataAdmissionState::GetPhase() const {
    return DecodePhase(m_state.load(std::memory_order_acquire));
}

std::uint64_t IBMarketDataAdmissionState::Generation() const {
    return DecodeGeneration(m_state.load(std::memory_order_acquire));
}

#ifdef HEPTA_ENABLE_IBAPI
#include "EWrapper.h"
#include "EClientSocket.h"
#include "EReaderOSSignal.h"
#include "EReader.h"
#include "Contract.h"
#include "Order.h"
#include "Decimal.h"
#include "Execution.h"
#include "OrderCancel.h"
#include "OrderState.h"

class EWrapperDefault : public EWrapper {
public:
#define EWRAPPER_VIRTUAL_IMPL override {}
#include "EWrapper_prototypes.h"
};

static IBEvent MakeIBEvent(IBEventType type, long long id, std::string key, std::string value, double number, double number2 = 0.0, double number3 = 0.0) {
    IBEvent e;
    e.type = type;
    e.id = id;
    e.key = std::move(key);
    e.value = std::move(value);
    e.number = number;
    e.number2 = number2;
    e.number3 = number3;
    return e;
}

static void PopulateContract(Contract& ct, const IBContractLite& c) {
    ct.symbol = c.symbol;
    ct.secType = c.secType.empty() ? "FUT" : c.secType;
    ct.exchange = c.exchange.empty() ? "SMART" : c.exchange;
    ct.primaryExchange = c.primaryExchange;
    ct.currency = c.currency.empty() ? "USD" : c.currency;
    ct.lastTradeDateOrContractMonth = c.lastTradeDateOrContractMonth;
    ct.localSymbol = c.localSymbol;
    ct.tradingClass = c.tradingClass;
    if (!c.right.empty()) {
        std::string right = c.right;
        for (char& ch : right) ch = static_cast<char>(std::toupper(static_cast<unsigned char>(ch)));
        if (right == "CALL") right = "C";
        if (right == "PUT") right = "P";
        ct.right = right;
    }
    if (c.strike > 0.0 && std::isfinite(c.strike)) {
        ct.strike = c.strike;
    }
    ct.multiplier = c.multiplier;
}

static IBContractLite BuildContractLite(const Contract& c) {
    IBContractLite out;
    out.symbol = c.symbol;
    out.secType = c.secType;
    out.exchange = c.exchange;
    out.primaryExchange = c.primaryExchange;
    out.currency = c.currency;
    out.lastTradeDateOrContractMonth = c.lastTradeDateOrContractMonth;
    out.right = c.right;
    out.strike = c.strike;
    out.multiplier = c.multiplier;
    out.tradingClass = c.tradingClass;
    out.localSymbol = c.localSymbol;
    return out;
}

static IBOrderLite BuildOrderLite(const Order& o) {
    IBOrderLite out;
    out.action = o.action;
    out.orderType = o.orderType;
    if (o.totalQuantity != UNSET_DECIMAL) {
        out.totalQuantity = DecimalFunctions::decimalToDouble(o.totalQuantity);
    }
    if (o.lmtPrice != UNSET_DOUBLE && std::isfinite(o.lmtPrice)) {
        out.lmtPrice = o.lmtPrice;
    }
    if (o.auxPrice != UNSET_DOUBLE && std::isfinite(o.auxPrice)) {
        out.auxPrice = o.auxPrice;
    }
    out.outsideRth = o.outsideRth;
    out.orderRef = o.orderRef;
    return out;
}

// A small, allocation-free scope guard for the producer-side market-data
// reservation.  The SDK call is intentionally outside the admission mutex,
// so every exit (including an exception from the SDK) must release the
// reservation without relying on a hand-maintained list of return paths.
template <typename F>
class IBApiScopeExit final {
public:
    explicit IBApiScopeExit(F fn)
        : m_fn(std::move(fn)) {}

    IBApiScopeExit(const IBApiScopeExit&) = delete;
    IBApiScopeExit& operator=(const IBApiScopeExit&) = delete;

    IBApiScopeExit(IBApiScopeExit&& other)
        : m_fn(std::move(other.m_fn)), m_active(other.m_active) {
        other.m_active = false;
    }

    ~IBApiScopeExit() noexcept {
        if (!m_active) return;
        // Cleanup is best effort during stack unwinding.  End... only takes a
        // short mutex and cannot make the SDK call re-enter, but a broken
        // mutex implementation must never replace the original SDK error
        // with a second exception from this destructor.
        try {
            m_fn();
        }
        catch (...) {
        }
    }

    void Release() {
        if (!m_active) return;
        m_fn();
        m_active = false;
    }

private:
    F m_fn;
    bool m_active = true;
};

template <typename F>
IBApiScopeExit<F> MakeIBApiScopeExit(F fn) {
    return IBApiScopeExit<F>(std::move(fn));
}

class IBApiWrapperReal final : public IIBApiWrapper, public EWrapperDefault {
public:
    static constexpr int kInitialAccountRefreshReqId = 9001;
    static constexpr const char* kAccountSummaryTags = "NetLiquidation,AvailableFunds,MaintMarginReq,RealizedPnL,UnrealizedPnL,TotalCashValue,SettledCash,AccruedCash,BuyingPower,ExcessLiquidity,InitMarginReq,FullInitMarginReq,FullMaintMarginReq,LookAheadInitMarginReq,LookAheadMaintMarginReq,LookAheadAvailableFunds,GrossPositionValue,Cushion,Leverage";
    int ResolveMarketDataType() const {
        const char* p = std::getenv("HEPTA_IB_MARKET_DATA_TYPE");
        if (p == nullptr || *p == '\0') return 1; // realtime
        int t = atoi(p);
        if (t < 1 || t > 4) return 1;
        return t;
    }
    static int ResolveSignalTimeoutMs() {
        const char* p = std::getenv("HEPTA_IB_POLLONCE_TIMEOUT_MS");
        if (p == nullptr || *p == '\0') return 100;
        int v = atoi(p);
        if (v < 10) v = 10;
        if (v > 2000) v = 2000;
        return v;
    }
    static std::size_t ResolveMaxEventQueue() {
        const char* p = std::getenv("HEPTA_IB_EVENT_QUEUE_MAX");
        if (p == nullptr || *p == '\0') return 20000;
        long v = std::strtol(p, nullptr, 10);
        if (v < 1000) v = 1000;
        if (v > 500000) v = 500000;
        return static_cast<std::size_t>(v);
    }
    bool IsTraceEnabled() const {
        const char* p = std::getenv("HEPTA_IB_TRACE");
        return p != nullptr && (strcmp(p, "1") == 0 || strcmp(p, "true") == 0 || strcmp(p, "TRUE") == 0);
    }

    const char* GetTracePath() const {
        const char* p = std::getenv("HEPTA_IB_TRACE_FILE");
        return (p != nullptr && *p != '\0') ? p : "ib_connect_trace.log";
    }

    void Trace(const std::string& s) {
        if (!IsTraceEnabled()) return;
        std::ofstream f(GetTracePath(), std::ios::app);
        if (f.is_open()) {
            f << s << std::endl;
        }
    }
    IBApiWrapperReal()
        : m_events(ResolveMaxEventQueue()),
          m_signal(ResolveSignalTimeoutMs()),
          m_client(this, &m_signal) {}
    ~IBApiWrapperReal() override { Disconnect(); }

    void SetConnectionEpoch(std::uint64_t connectionEpoch) override {
        m_connectionEpoch.store(
            connectionEpoch, std::memory_order_release);
        m_eventIngressFarmMarker.Reset(connectionEpoch);
    }

    void SetEventIngressFence(
        const std::shared_ptr<std::recursive_mutex>& fence) override {
        m_eventIngressFence = fence;
    }

    void BeginEventIngressAdmission() override {
        // A wrapper instance is replaced on every broker connection epoch.
        // Begin() also increments the admission generation, so a late
        // EndSend from an older transaction cannot settle a fresh one.
        std::uint64_t generation = 0;
        if (!m_eventIngressAdmissionState.Begin(generation)) {
            // A previous reserved send is an impossible re-entry from the
            // runtime, but fail closed if a caller violates that contract.
            (void)m_eventIngressAdmissionState.MarkFault();
            m_eventIngressAdmissionGeneration.store(
                m_eventIngressAdmissionState.Generation(),
                std::memory_order_release);
            m_eventIngressAdmissionActive.store(true,
                std::memory_order_release);
            m_eventIngressFenceHeld.store(true,
                std::memory_order_release);
            return;
        }
        m_eventIngressAdmissionGeneration.store(
            generation, std::memory_order_release);
        m_eventIngressSendActive.store(false,
            std::memory_order_release);
        m_eventIngressAdmissionActive.store(true,
            std::memory_order_release);
        m_eventIngressFenceHeld.store(true,
            std::memory_order_release);
        // Begin only succeeds for a clean Idle state, so any failure witness
        // is already represented by the packed state word.
    }

    void EndEventIngressAdmission() override {
        // This is the first half of a close.  Keep fenceHeld=true while the
        // caller releases its external fence; callbacks that enter during
        // that handoff remain deferred and are flushed by Complete...().
        FlushDeferredIngress();
        m_eventIngressAdmissionActive.store(false,
            std::memory_order_release);
        // Catch callbacks that observed the old active bit while the first
        // drain was in progress.  The external fence is still held here.
        FlushDeferredIngress();
    }

    void FlushEventIngressAdmission() override {
        FlushDeferredIngress();
    }

    void CompleteEventIngressAdmission() override {
        // Called after the caller has released EventIngressFence().  The
        // fenceHeld bit is the producer-side barrier for that release gap;
        // clear it only after one final flush while holding the same mutex.
        FlushDeferredIngress();
        // Be defensive if a caller skipped End... after a failed Begin.  The
        // packed state, rather than this hint, decides whether completion is
        // actually safe.
        m_eventIngressAdmissionActive.store(false,
            std::memory_order_release);
        m_eventIngressFenceHeld.store(false,
            std::memory_order_release);
        if (!m_eventIngressSendActive.load(std::memory_order_acquire) &&
            !m_eventIngressAdmissionState.Complete())
            (void)m_eventIngressAdmissionState.MarkFault();
        // A callback may have entered during the fence release handoff.  Once
        // the state is idle, such a callback publishes directly; callbacks
        // that still hold a deferred snapshot are drained here.
        FlushDeferredIngress();
    }

    bool EventIngressAdmissionFailed() const override {
        return m_eventIngressAdmissionState.IsFailed();
    }

    std::uint64_t GetConnectionEpoch() const override {
        return m_connectionEpoch.load();
    }

    bool Connect(const IBConnectParams& p) override {
        if (m_terminalIngressHalted.load()) {
            m_status = "IB_TERMINAL_INGRESS_HALTED";
            return false;
        }
        // A wrapper object is connection-epoch scoped.  Refuse to resurrect
        // one whose admission state still carries a fault/open reservation;
        // the adapter's reconnect factory supplies a fresh state word for the
        // next epoch instead of risking stale callbacks reopening the gate.
        if (m_eventIngressAdmissionState.IsDeferred() ||
            m_eventIngressAdmissionState.IsFailed()) {
            m_status = "IB_CONNECT_ADMISSION_STATE_NOT_CLEAN";
            return false;
        }
        m_eventIngressFarmMarker.Reset(
            m_connectionEpoch.load(std::memory_order_acquire));
        m_params = p;
        m_gotNextValidId = false;
        m_lastValidOrderId = -1;
        m_status = "IB_CONNECTING";
        Trace("IB_CONNECTING host=" + m_params.host + " port=" + std::to_string(m_params.port) + " clientId=" + std::to_string(m_params.clientId));
        try {
            m_client.asyncEConnect(false);
            // NOTE: 4th arg is extraAuth (not readOnly). Keep false unless you explicitly use extraAuth flow.
            if (!m_client.eConnect(m_params.host.c_str(), m_params.port, m_params.clientId, false)) {
                m_status = "IB_CONNECT_FAILED";
                m_connected = false;
                Trace("IB_CONNECT_FAILED");
                return false;
            }

            m_status = "IB_SOCKET_CONNECTED";
            Trace("IB_SOCKET_CONNECTED");
            m_reader.reset(new EReader(&m_client, &m_signal));
            m_status = "IB_READER_CREATED";
            m_reader->start();
            m_status = "IB_READER_STARTED";

            m_connected = true;
            m_status = "IB_CONNECTED_WAITING_NEXTVALIDID";
            m_marketDataType = ResolveMarketDataType();
            Trace("IB_CONNECTED_WAITING_NEXTVALIDID");

            auto t0 = std::chrono::steady_clock::now();
            auto lastReqIds = t0 - std::chrono::seconds(10);
            while (std::chrono::steady_clock::now() - t0 < std::chrono::seconds(20)) {
                PollOnce(500);
                auto now = std::chrono::steady_clock::now();
                if (now - lastReqIds >= std::chrono::seconds(3)) {
                    m_client.reqIds(1);
                    lastReqIds = now;
                }
                if (m_gotNextValidId.load()) {
                    m_status = "IB_CONNECTED";
                    m_client.reqMarketDataType(m_marketDataType);
                    Trace("IB_CONNECTED marketDataType=" + std::to_string(m_marketDataType));
                    return true;
                }
            }
            m_connected = false;
            m_status = "IB_CONNECT_TIMEOUT_NO_NEXTVALIDID";
            Trace("IB_CONNECT_TIMEOUT_NO_NEXTVALIDID");
            m_client.eDisconnect();
            if (m_reader) {
                m_reader->stop();
                m_reader.reset();
            }
            m_gotNextValidId = false;
            m_lastValidOrderId = -1;
            return false;
        }
        catch (const std::exception& ex) {
            m_status = std::string("IB_CONNECT_EXCEPTION:") + ex.what();
            m_connected = false;
            m_client.eDisconnect();
            if (m_reader) {
                m_reader->stop();
                m_reader.reset();
            }
            m_gotNextValidId = false;
            m_lastValidOrderId = -1;
            Trace(m_status);
            return false;
        }
    }

    void Disconnect() override {
        // EClientSocket::eDisconnect() joins an internal API thread and is not
        // safe to invoke a second time after that thread has been consumed.
        // RuntimeComposition::Stop() disconnects explicitly, and the wrapper
        // destructor calls Disconnect() again, so make that lifecycle boundary
        // strictly idempotent before touching the IB client.
        const bool admissionInFlight =
            m_eventIngressAdmissionActive.load(std::memory_order_acquire) ||
            m_eventIngressFenceHeld.load(std::memory_order_acquire) ||
            m_eventIngressSendActive.load(std::memory_order_acquire) ||
            m_eventIngressAdmissionState.IsDeferred();
        if (admissionInFlight)
            (void)m_eventIngressAdmissionState.MarkFault();
        // Surface callbacks already deferred by the admission boundary before
        // the SDK reader is stopped.  This is outside the EReader callback
        // path, so the short queue lock is safe here and no evidence is lost
        // merely because disconnect raced a quote request.
        FlushDeferredIngress();
        const bool wasConnected = m_connected.exchange(false);
        if (!wasConnected && !m_reader) {
            m_eventIngressAdmissionActive.store(false,
                std::memory_order_release);
            m_eventIngressFenceHeld.store(false,
                std::memory_order_release);
            if (!m_eventIngressSendActive.load(std::memory_order_acquire)) {
                if (!m_eventIngressAdmissionState.Complete())
                    (void)m_eventIngressAdmissionState.MarkFault();
                FlushDeferredIngress();
            }
            m_eventIngressFarmMarker.Reset(
                m_connectionEpoch.load(std::memory_order_acquire));
            m_status = "IB_DISCONNECTED";
            return;
        }
        if (wasConnected && m_accountSummarySubscribed) {
            m_client.cancelAccountSummary(m_activeAccountSummaryReqId);
            m_accountSummarySubscribed = false;
        }
        if (wasConnected && m_accountUpdatesSubscribed &&
            !m_params.account.empty()) {
            m_client.cancelAccountUpdatesMulti(m_activeAccountUpdatesReqId);
            m_accountUpdatesSubscribed = false;
            m_accountUpdatesInitialDownloadPending = false;
        }
        if (wasConnected && m_positionsSubscribed &&
            m_positionsRequestFence.ActiveRequestId() > 0) {
            m_client.cancelPositionsMulti(
                m_positionsRequestFence.ActiveRequestId());
            m_positionsRequestFence.Complete(
                m_positionsRequestFence.ActiveRequestId());
            m_positionsSubscribed = false;
            m_positionsInitialDownloadPending = false;
        }
        m_client.eDisconnect();
        if (m_reader) {
            m_reader->stop();
            m_reader.reset();
        }
        m_gotNextValidId = false;
        m_lastValidOrderId = -1;
        m_orderTotalQty.clear();
        m_eventIngressAdmissionActive.store(false,
            std::memory_order_release);
        m_eventIngressFenceHeld.store(false,
            std::memory_order_release);
        if (!m_eventIngressSendActive.load(std::memory_order_acquire)) {
            if (!m_eventIngressAdmissionState.Complete())
                (void)m_eventIngressAdmissionState.MarkFault();
        }
        m_eventIngressFarmMarker.Reset(
            m_connectionEpoch.load(std::memory_order_acquire));
        FlushDeferredIngress();
        m_status = "IB_DISCONNECTED";
    }

    bool IsConnected() const override { return m_connected; }
    const char* GetStatusString() const override { return m_status.c_str(); }

    bool GetBrokerConnectionIdentity(
        IBBrokerConnectionIdentity& identity,
        std::string& reason) const override {
        identity = IBBrokerConnectionIdentity();
        if (!m_connected || m_terminalIngressHalted.load()) {
            reason = "IB_BROKER_SOCKET_IDENTITY_NOT_CONNECTED";
            return false;
        }
        const int socketFd = m_client.fd();
        struct stat metadata;
        struct sockaddr_storage localAddress;
        struct sockaddr_storage peerAddress;
        std::memset(&localAddress, 0, sizeof(localAddress));
        std::memset(&peerAddress, 0, sizeof(peerAddress));
        socklen_t localLength = sizeof(localAddress);
        socklen_t peerLength = sizeof(peerAddress);
        int socketType = 0;
        socklen_t socketTypeLength = sizeof(socketType);
        const std::uint64_t epoch = m_connectionEpoch.load();
        if (socketFd < 0 || epoch == 0 ||
            ::fstat(socketFd, &metadata) != 0 ||
            !S_ISSOCK(metadata.st_mode) ||
            ::getsockopt(socketFd, SOL_SOCKET, SO_TYPE,
                &socketType, &socketTypeLength) != 0 ||
            socketTypeLength != sizeof(socketType) || socketType <= 0 ||
            ::getsockname(socketFd,
                reinterpret_cast<struct sockaddr*>(&localAddress),
                &localLength) != 0 ||
            ::getpeername(socketFd,
                reinterpret_cast<struct sockaddr*>(&peerAddress),
                &peerLength) != 0 || localLength == 0 ||
            localLength > sizeof(localAddress) || peerLength == 0 ||
            peerLength > sizeof(peerAddress)) {
            reason = "IB_BROKER_SOCKET_IDENTITY_UNAVAILABLE";
            return false;
        }
        static const char digits[] = "0123456789abcdef";
        const auto appendHex = [](std::ostringstream& out,
                                  const void* data, std::size_t size) {
            static const char hex[] = "0123456789abcdef";
            const unsigned char* bytes =
                static_cast<const unsigned char*>(data);
            for (std::size_t i = 0; i < size; ++i)
                out << hex[bytes[i] >> 4] << hex[bytes[i] & 15];
        };
        (void)digits;
        std::ostringstream canonical;
        canonical << "IBSOCK1\nconnection_epoch=" << epoch
            << "\nst_dev=" << static_cast<unsigned long long>(metadata.st_dev)
            << "\nst_ino=" << static_cast<unsigned long long>(metadata.st_ino)
            << "\nsocket_type=" << socketType
            << "\nlocal_length=" << localLength << "\nlocal_hex=";
        appendHex(canonical, &localAddress, localLength);
        canonical << "\npeer_length=" << peerLength << "\npeer_hex=";
        appendHex(canonical, &peerAddress, peerLength);
        canonical << '\n';
        identity.connectionEpoch = epoch;
        identity.canonical = canonical.str();
        reason.clear();
        return true;
    }

    bool ReqAccountSummary() override {
        if (!m_connected || m_params.account.empty()) return false;
        if (m_accountSummarySubscribed) {
            m_client.cancelAccountSummary(m_activeAccountSummaryReqId);
            m_accountSummarySubscribed = false;
        }
        if (m_accountUpdatesSubscribed) {
            m_client.cancelAccountUpdatesMulti(m_activeAccountUpdatesReqId);
            m_accountUpdatesSubscribed = false;
            m_accountUpdatesInitialDownloadPending = false;
        }
        m_accountSummaryEndObserved = false;
        m_accountDownloadEndObserved = false;
        if (m_nextAccountRefreshReqId <= 0 ||
            m_nextAccountRefreshReqId >
                std::numeric_limits<int>::max() - 2) return false;
        m_activeAccountSummaryReqId = m_nextAccountRefreshReqId++;
        m_activeAccountUpdatesReqId = m_nextAccountRefreshReqId++;
        m_client.reqAccountSummary(
            m_activeAccountSummaryReqId, "All", kAccountSummaryTags);
        m_accountSummarySubscribed = true;
        // reqPositions does not represent IB spot-FX CASH inventory.  The
        // account-specific multi-update stream supplies currency-unit
        // CashBalance values and accountUpdateMultiEnd supplies an initial
        // generation boundary. The runtime explicitly re-requests a full
        // download after a fill because IB does not promise sub-minute
        // account-value subscription updates.
        m_client.reqAccountUpdatesMulti(
            m_activeAccountUpdatesReqId, m_params.account, "", true);
        m_accountUpdatesSubscribed = true;
        m_accountUpdatesInitialDownloadPending = true;
        Trace("account_refresh.request summary_req=" +
            std::to_string(m_activeAccountSummaryReqId) +
            " multi_req=" +
            std::to_string(m_activeAccountUpdatesReqId));
        return true;
    }

    bool ReqPositions() override {
        if (!m_connected || m_params.account.empty()) return false;
        int requestId = 0;
        int supersededRequestId = 0;
        if (!m_positionsRequestFence.Begin(
                requestId, supersededRequestId)) return false;
        if (supersededRequestId > 0) {
            m_client.cancelPositionsMulti(supersededRequestId);
        }
        m_client.reqPositionsMulti(requestId, m_params.account, "");
        m_positionsSubscribed = true;
        m_positionsInitialDownloadPending = true;
        return true;
    }

    bool ReqOpenOrders() override {
        if (!m_connected) return false;
        m_client.reqOpenOrders();
        return true;
    }

    bool ReqAllOpenOrders() override {
        if (!m_connected) return false;
        m_client.reqAllOpenOrders();
        return true;
    }

    bool ReqCompletedOrders() override {
        if (!m_connected) return false;
        // false is required for a restarted dedicated service to recover
        // broker-owned orders submitted by its prior API client instance.
        m_client.reqCompletedOrders(false);
        return true;
    }

    bool ReqExecutions(int requestId) override {
        if (!m_connected || requestId <= 0) return false;
        ExecutionFilter filter;
        m_client.reqExecutions(requestId, filter);
        return true;
    }

    bool ReqMktData(int reqId, const IBContractLite& c) override {
        return ReqMktDataInternal(reqId, c);
    }

    bool ReqMktDataInternal(int reqId, const IBContractLite& c) {
        // The final state check and Open -> Reserved CAS are the only
        // admission linearization point.  No admission/queue mutex is held
        // while entering the SDK: IB may synchronously invoke EWrapper (or
        // disconnect and join its reader) from reqMktData itself.
        bool admission = false;
        bool implicitAdmission = false;
        std::uint64_t admissionGeneration = 0;
        bool cleanupNeeded = false;
        bool implicitAdmissionCompleted = false;
        // Construct all potentially allocating SDK argument objects before
        // the reservation CAS.  Once the sender wins, the only operation
        // between the linearization point and IB is the single reqMktData
        // call (plus the atomic bookkeeping needed for its cleanup).
        const TagValueListSPtr mktDataOptions(new TagValueList());
        auto cleanup = MakeIBApiScopeExit(
            [this, &cleanupNeeded, &implicitAdmission,
             &admissionGeneration, &implicitAdmissionCompleted]() {
                if (cleanupNeeded) {
                    EndMarketDataAdmissionSend(admissionGeneration);
                    cleanupNeeded = false;
                }
                if (implicitAdmission && !implicitAdmissionCompleted) {
                    // Legacy/unfenced callers still get a complete local
                    // admission lifecycle.  Without this, an early return
                    // after reqMarketDataType() would leave OPEN forever and
                    // poison every later request on this wrapper.
                    if (!m_eventIngressAdmissionState.Complete())
                        (void)m_eventIngressAdmissionState.MarkFault();
                    FlushDeferredIngress();
                    implicitAdmissionCompleted = true;
                }
            });
        bool connectionLossReported = false;
        auto reportConnectionLoss = [this, &connectionLossReported]() {
            if (connectionLossReported) return;
            connectionLossReported = true;
            // A disconnect callback can be lost in the same SDK call that
            // observes it (some IB builds close the socket without first
            // delivering connectionClosed()).  Preserve an explicit
            // current-epoch witness so the runtime fails closed instead of
            // treating an uncertain request as an accepted quote leg.
            PushEvent(MakeIBEvent(
                IBEventType::ConnectionClosed, 0, "",
                "IB_MARKET_DATA_REQUEST_CONNECTION_LOST", 0.0));
        };
        if (!m_connected.load(std::memory_order_acquire) ||
            !m_client.isSocketOK() || m_params.readOnly)
            return false;
        const bool cashContract = c.secType == "CASH";
        const std::uint64_t requestEpoch =
            m_connectionEpoch.load(std::memory_order_acquire);
        // A CASH request is never a readiness signal.  The broker wrapper
        // itself requires a positive 2104 observed in this connection epoch;
        // generic transport readiness, nextValidId, or callback ordering
        // cannot authorize the SDK call.
        if (cashContract && !m_eventIngressFarmMarker.IsReady(requestEpoch))
            return false;
        // Read the packed phase first.  It is published by Begin before the
        // lifecycle hint flags, so a concurrent Begin/Complete cannot make a
        // request bypass an already-open (or still-closing) transaction.
        IBMarketDataAdmissionState::Phase phase;
        bool stateFault = false;
        m_eventIngressAdmissionState.Snapshot(
            phase, admissionGeneration, stateFault);
        if (stateFault) return false;
        admission = phase != IBMarketDataAdmissionState::Idle;
        if (admission) {
            if (phase != IBMarketDataAdmissionState::Open ||
                admissionGeneration == 0)
                return false;
        } else {
            // All production quote sends should be explicitly fenced, but
            // retain a safe path for legacy callers: open an implicit
            // generation before the first control callback.  This closes the
            // Idle->callback->send race instead of allowing an un-fenced SDK
            // request to cross a blocking witness.
            if (!m_eventIngressAdmissionState.Begin(admissionGeneration))
                return false;
            admission = true;
            implicitAdmission = true;
            m_eventIngressAdmissionGeneration.store(
                admissionGeneration, std::memory_order_release);
        }
        try {
            // This control call is deliberately before the final reservation;
            // any synchronous blocking callback it produces therefore wins
            // Open -> Blocked and prevents the real quote request.
            m_client.reqMarketDataType(m_marketDataType);
            if (!m_connected.load(std::memory_order_acquire) ||
                !m_client.isSocketOK() ||
                m_eventIngressFarmMarker.IsPending(
                    requestEpoch) ||
                (cashContract &&
                 !m_eventIngressFarmMarker.IsReady(requestEpoch))) {
                if (!m_connected.load(std::memory_order_acquire) ||
                    !m_client.isSocketOK())
                    reportConnectionLoss();
                cleanup.Release();
                return false;
            }
            Contract ct;
            PopulateContract(ct, c);
            if (admission) {
                IBMarketDataAdmissionState::Phase beforeReservePhase;
                std::uint64_t beforeReserveGeneration = 0;
                bool beforeReserveFault = false;
                m_eventIngressAdmissionState.Snapshot(
                    beforeReservePhase, beforeReserveGeneration,
                    beforeReserveFault);
                if (!m_connected.load(std::memory_order_acquire) ||
                    !m_client.isSocketOK() || admissionGeneration == 0 ||
                    beforeReserveFault ||
                    beforeReservePhase != IBMarketDataAdmissionState::Open ||
                    beforeReserveGeneration != admissionGeneration ||
                    m_eventIngressFarmMarker.IsPending(
                        requestEpoch) ||
                    (cashContract &&
                     !m_eventIngressFarmMarker.IsReady(requestEpoch))) {
                    if (!m_connected.load(std::memory_order_acquire) ||
                        !m_client.isSocketOK())
                        reportConnectionLoss();
                    cleanup.Release();
                    return false;
                }
                if (!m_eventIngressAdmissionState.TryReserve(
                        admissionGeneration)) {
                    // A blocking callback won Open -> Blocked first (or the
                    // transaction was already closed).  Never issue the SDK
                    // request after that failed reservation.
                    cleanup.Release();
                    return false;
                }
                m_eventIngressSendGeneration.store(
                    admissionGeneration, std::memory_order_release);
                m_eventIngressSendActive.store(true,
                    std::memory_order_release);
                cleanupNeeded = true;
            }
            m_client.reqMktData(reqId, ct, "", false, false,
                                mktDataOptions);
            // A synchronous SDK return does not prove that the socket stayed
            // alive.  Keep the return value true because the request may have
            // reached IB and therefore must remain in the accepted-id cleanup
            // set; the explicit witness below makes Finish... fail closed.
            if (!m_connected.load(std::memory_order_acquire) ||
                !m_client.isSocketOK())
                reportConnectionLoss();
        }
        catch (...) {
            // An SDK exception does not prove that no bytes/request reached
            // IB (and reqMarketDataType itself may have synchronously
            // delivered a fatal callback).  Preserve a sticky fault before
            // cleanup so this generation can never be silently reopened.
            (void)m_eventIngressAdmissionState.MarkFault();
            if (!m_connected.load(std::memory_order_acquire) ||
                !m_client.isSocketOK())
                reportConnectionLoss();
            throw;
        }
        cleanup.Release();
        return true;
    }

    bool CancelMktData(int reqId) override {
        if (!m_connected || m_params.readOnly) return false;
        m_client.cancelMktData(reqId);
        return true;
    }



    std::string BuildOrderTrace(long localOrderId, const Contract& ct, const Order& od) {
        std::ostringstream oss;
        oss << std::fixed << std::setprecision(8)
            << "placeOrder id=" << localOrderId
            << " symbol=" << ct.symbol
            << " secType=" << ct.secType
            << " exch=" << ct.exchange
            << " primaryExch=" << ct.primaryExchange
            << " ccy=" << ct.currency
            << " expiry=" << ct.lastTradeDateOrContractMonth
            << " right=" << ct.right
            << " strike=" << ct.strike
            << " mult=" << ct.multiplier
            << " tradingClass=" << ct.tradingClass
            << " localSymbol=" << ct.localSymbol
            << " action=" << od.action
            << " type=" << od.orderType
            << " tif=" << od.tif
            << " qty=" << DecimalFunctions::decimalToDouble(od.totalQuantity)
            << " lmt=" << od.lmtPrice
            << " outsideRth=" << (od.outsideRth ? 1 : 0);
        return oss.str();
    }

    bool PlaceOrder(long localOrderId, const IBContractLite& c, const IBOrderLite& o) override {
        if (!m_connected || m_params.readOnly) return false;

        auto rejectLocal = [&](const std::string& reason, int code) -> bool {
            m_lastError = reason;
            Trace("placeOrder blocked id=" + std::to_string(localOrderId) + " reason=" + reason);
            PushEvent(MakeIBEvent(IBEventType::Error, static_cast<long long>(localOrderId), std::to_string(code), reason, 0.0));
            return false;
        };

        Contract ct;
        PopulateContract(ct, c);

        Order od;
        od.action = o.action;
        od.orderType = o.orderType.empty() ? "MKT" : o.orderType;
        const double qty = o.totalQuantity;
        if (!(std::isfinite(qty)) || qty <= 0.0) {
            return rejectLocal("IB_LOCAL_REJECT_QTY_INVALID", 32101);
        }
        od.totalQuantity = DecimalFunctions::doubleToDecimal(qty);
        if (od.totalQuantity == UNSET_DECIMAL) {
            return rejectLocal("IB_LOCAL_REJECT_QTY_DECIMAL_ENCODE", 32104);
        }
        if (od.action != "BUY" && od.action != "SELL") {
            return rejectLocal("IB_LOCAL_REJECT_ACTION_INVALID", 32102);
        }
        if (od.orderType == "LMT") {
            od.lmtPrice = o.lmtPrice;
            if (!(std::isfinite(od.lmtPrice)) || od.lmtPrice <= 0.0) {
                return rejectLocal("IB_LOCAL_REJECT_LMT_INVALID", 32103);
            }
        }

        if (o.outsideRth) {
            od.outsideRth = true;
        }
        od.tif = "DAY";
        od.orderRef = o.orderRef;

        Trace(BuildOrderTrace(localOrderId, ct, od));
        m_orderTotalQty[localOrderId] = qty;
        m_client.placeOrder(localOrderId, ct, od);
        return true;
    }

    bool CancelOrder(long localOrderId) override {
        if (!m_connected || m_params.readOnly) return false;
        OrderCancel oc;
        m_client.cancelOrder(localOrderId, oc);
        return true;
    }

    bool PollOnce(int timeoutMs) override {
        if (m_terminalIngressHalted.load() ||
            !m_connected || !m_reader) return false;
        m_reader->processMsgs();
        if (timeoutMs > 0) {
            m_signal.waitForSignal();
            m_reader->processMsgs();
        }
        return true;
    }

    bool HaltAndDrainTerminalTransport(
        std::vector<IBEvent>& drainedEvents,
        IBTerminalTransportDrainWitness& witness,
        std::string& reason) override {
        drainedEvents.clear();
        witness = IBTerminalTransportDrainWitness();
        if (m_terminalIngressHalted.exchange(true)) {
            reason = "IB_TERMINAL_INGRESS_ALREADY_HALTED";
            return false;
        }
        witness.ingressHalted = true;
        try {
            // Closing ingress is still required, but neither this join nor
            // any decoded callback queue can prove that the broker had no
            // economically relevant event beyond the local cutoff.
            Disconnect();
            witness.readerStopped = true;
        }
        catch (...) {
            reason = "POST_CUTOFF_SIGNED_WITNESS_REQUIRED";
            return false;
        }
        m_status = "IB_TERMINAL_SIGNED_WITNESS_REQUIRED";
        reason = "POST_CUTOFF_SIGNED_WITNESS_REQUIRED";
        return false;
    }

    bool TryDequeueEvent(IBEvent& outEvent) override {
        PublishIngressDropNotice();
        return m_events.TryDequeueEvent(outEvent);
    }

    long GetLastValidOrderId() const override {
        return m_lastValidOrderId;
    }

    // ---- EWrapper events ----
    void nextValidId(OrderId oid) override {
        m_gotNextValidId = true;
        m_lastValidOrderId = static_cast<long>(oid);
        Trace("nextValidId=" + std::to_string((long long)oid));
        PushEvent(MakeIBEvent(IBEventType::NextValidId, static_cast<long long>(oid), "", "", 0.0));
    }
    void error(int id, int code, const std::string& errorString,
               const std::string& advancedOrderRejectJson) override {
        m_lastError = errorString;
        Trace("error id=" + std::to_string(id) + " code=" + std::to_string(code) + " msg=" + errorString);
        if (code == 10197 && m_connected && m_marketDataType == 1) {
            Trace("market data warning 10197 observed under forced realtime mode (no fallback)");
        }
        IBEvent event = MakeIBEvent(
            IBEventType::Error, static_cast<long long>(id),
            std::to_string(code), errorString, 0.0);
        event.advancedOrderRejectJson = advancedOrderRejectJson;
        PushEvent(std::move(event));
    }
    void connectionClosed() override {
        m_connected = false;
        m_accountSummarySubscribed = false;
        m_accountUpdatesSubscribed = false;
        m_accountUpdatesInitialDownloadPending = false;
        m_positionsSubscribed = false;
        m_positionsInitialDownloadPending = false;
        if (m_positionsRequestFence.ActiveRequestId() > 0)
            m_positionsRequestFence.Complete(
                m_positionsRequestFence.ActiveRequestId());
        m_status = "IB_CONNECTION_CLOSED";
        Trace("connectionClosed");
        PushEvent(MakeIBEvent(IBEventType::ConnectionClosed, 0, "", m_status, 0.0));
    }

    // Minimal no-op overrides for compatibility
    void tickPrice(TickerId reqId, TickType field, double price, const TickAttrib&) override {
        PushEvent(MakeIBEvent(IBEventType::TickPrice, static_cast<long long>(reqId), std::to_string((int)field), "", price));
    }
    void tickSize(TickerId, TickType, Decimal) override {}
    void tickOptionComputation(TickerId, TickType, int, double, double, double, double, double, double, double, double) override {}
    void tickGeneric(TickerId, TickType, double) override {}
    void tickString(TickerId, TickType, const std::string&) override {}
    void tickEFP(TickerId, TickType, double, const std::string&, double, int, const std::string&, double, double) override {}
    void orderStatus(OrderId oid, const std::string& status, Decimal filled,
                     Decimal remaining, double avgFillPrice, int, int, double,
                     int, const std::string& whyHeld,
                     double mktCapPrice) override {
        const double filledQuantity = DecimalFunctions::decimalToDouble(filled);
        const bool economicFill = status == "Filled" && filledQuantity > 0.0 &&
            std::isfinite(avgFillPrice) && avgFillPrice > 0.0;
        if (economicFill || status == "Cancelled" ||
            status == "ApiCancelled" || status == "Inactive" ||
            status == "Rejected") {
            m_orderTotalQty.erase(static_cast<long>(oid));
        }
        IBEvent event = MakeIBEvent(IBEventType::OrderStatus,
            static_cast<long long>(oid),
            status,
            "",
            avgFillPrice,
            filledQuantity,
            DecimalFunctions::decimalToDouble(remaining));
        event.whyHeld = whyHeld;
        event.marketCapPrice = mktCapPrice;
        PushEvent(std::move(event));
    }
    void execDetails(int requestId, const Contract& contract, const Execution& execution) override {
        const long orderId = static_cast<long>(execution.orderId);
        const auto itQty = m_orderTotalQty.find(orderId);
        const double totalQty = (itQty != m_orderTotalQty.end()) ? itQty->second : 0.0;
        const double cumQty = DecimalFunctions::decimalToDouble(execution.cumQty);
        const bool totalQuantityKnown = totalQty > 0.0;
        const bool looksFilled = totalQuantityKnown && cumQty > 0.0 &&
            (cumQty + 1e-9) >= totalQty;
        const std::string synthStatus = looksFilled ? "Filled" :
            (totalQuantityKnown ? "PartiallyFilled" : "NotSynthesized");
        const double fillPx = (execution.avgPrice > 0.0 ? execution.avgPrice : execution.price);
        Trace("execDetails orderId=" + std::to_string((long long)execution.orderId)
            + " cumQty=" + std::to_string(cumQty)
            + " totalQty=" + std::to_string(totalQty)
            + " avgPrice=" + std::to_string(fillPx)
            + " synthStatus=" + synthStatus);
        if (looksFilled) {
            m_orderTotalQty.erase(orderId);
        }
        const double remainingQty = (totalQty > 0.0) ? std::max(0.0, totalQty - cumQty) : 0.0;
        IBEvent executionEvent = MakeIBEvent(
            IBEventType::ExecutionDetails,
            static_cast<long long>(execution.orderId),
            execution.execId,
            execution.side,
            fillPx,
            cumQty,
            remainingQty);
        executionEvent.account = execution.acctNumber;
        executionEvent.requestId = requestId;
        executionEvent.contract = BuildContractLite(contract);
        PushEvent(std::move(executionEvent));
        // execDetails does not carry the order's total quantity.  After a
        // process restart m_orderTotalQty has no entry for historical fills,
        // so emitting `PartiallyFilled, remaining=0` would manufacture a
        // contradictory broker status.  Preserve the economic execution and
        // let the complete active-order snapshot reconcile ownership; only
        // synthesize a status when this process actually knows the total.
        if (totalQuantityKnown) {
            IBEvent syntheticStatus = MakeIBEvent(
                IBEventType::OrderStatus,
                static_cast<long long>(execution.orderId),
                synthStatus,
                "execDetails",
                fillPx,
                cumQty,
                remainingQty);
            // Preserve the execution-query provenance on the synthetic
            // status. A positive historical reqExecutions() replay is durable
            // evidence, but it is not a new fill in this process and must
            // never re-arm the live post-fill mutation gate during startup.
            syntheticStatus.requestId = requestId;
            PushEvent(std::move(syntheticStatus));
        }
    }
    void execDetailsEnd(int requestId) override {
        IBEvent event = MakeIBEvent(
            IBEventType::ExecutionDetailsEnd,
            static_cast<long long>(requestId),
            "EXECUTION_DETAILS_END", "END", 0.0);
        event.requestId = requestId;
        PushEvent(std::move(event));
    }
    void openOrder(OrderId orderId, const Contract& contract, const Order& order, const OrderState& state) override {
        IBEvent event = MakeIBEvent(
            IBEventType::OpenOrder,
            static_cast<long long>(orderId),
            BuildPositionKey(contract),
            state.status,
            0.0);
        event.account = order.account;
        event.contract = BuildContractLite(contract);
        event.order = BuildOrderLite(order);
        event.number = event.order.totalQuantity;
        event.number2 = event.order.lmtPrice;
        PushEvent(std::move(event));
    }
    void openOrderEnd() override {
        PushEvent(MakeIBEvent(IBEventType::OpenOrderEnd, 0, "OPEN_ORDER_END", "END", 0.0));
    }
    void completedOrder(const Contract& contract, const Order& order,
                        const OrderState& state) override {
        IBEvent event = MakeIBEvent(
            IBEventType::CompletedOrder,
            static_cast<long long>(order.orderId),
            state.status,
            "COMPLETED_ORDER",
            0.0);
        event.account = order.account;
        event.contract = BuildContractLite(contract);
        event.order = BuildOrderLite(order);
        event.number = event.order.totalQuantity;
        event.number2 = event.order.lmtPrice;
        PushEvent(std::move(event));
    }
    void completedOrdersEnd() override {
        PushEvent(MakeIBEvent(
            IBEventType::CompletedOrdersEnd, 0,
            "COMPLETED_ORDERS_END", "END", 0.0));
    }
    void winError(const std::string&, int) override {}
    void updateAccountValue(const std::string& key, const std::string& val, const std::string& ccy, const std::string& account) override {
        IBEvent event = MakeIBEvent(IBEventType::AccountValue, 0, key + ":" + ccy, val, 0.0);
        event.account = account;
        PushEvent(std::move(event));
    }
    void accountUpdateMulti(int reqId, const std::string& account,
                            const std::string&, const std::string& key,
                            const std::string& value,
                            const std::string& currency) override {
        if (reqId != m_activeAccountUpdatesReqId ||
            account != m_params.account) return;
        IBEvent event = MakeIBEvent(
            IBEventType::AccountValue, static_cast<long long>(reqId),
            key + ":" + currency, value, 0.0);
        event.account = account;
        PushEvent(std::move(event));
        if (key == "CashBalance" || key == "AccountReady")
            Trace("account_refresh.value req=" + std::to_string(reqId) +
                " key=" + key + " currency=" + currency);
    }
    void accountUpdateMultiEnd(int reqId) override {
        Trace("account_refresh.multi_end req=" + std::to_string(reqId) +
            " active=" + std::to_string(m_activeAccountUpdatesReqId) +
            " pending=" +
            (m_accountUpdatesInitialDownloadPending ? "true" : "false"));
        if (reqId != m_activeAccountUpdatesReqId ||
            !m_accountUpdatesSubscribed ||
            !m_accountUpdatesInitialDownloadPending) return;
        // Keep this request subscribed after its initial download. Later
        // CashBalance/AccountReady callbacks are monitor signals that make the
        // committed generation stale if their values change.
        m_accountUpdatesInitialDownloadPending = false;
        m_accountDownloadEndObserved = true;
        PublishCombinedAccountSnapshotEnd();
    }
    void accountSummary(int reqId, const std::string& account, const std::string& tag, const std::string& value, const std::string& currency) override {
        if (reqId != m_activeAccountSummaryReqId) return;
        IBEvent event = MakeIBEvent(IBEventType::AccountValue, static_cast<long long>(reqId), tag + ":" + currency, value, 0.0);
        event.account = account;
        PushEvent(std::move(event));
    }
    void accountSummaryEnd(int reqId) override {
        Trace("account_refresh.summary_end req=" + std::to_string(reqId) +
            " active=" + std::to_string(m_activeAccountSummaryReqId));
        if (reqId != m_activeAccountSummaryReqId) return;
        m_accountSummaryEndObserved = true;
        PublishCombinedAccountSnapshotEnd();
    }
    static std::string BuildPositionKey(const Contract& c) {
        if (c.conId > 0) return std::string("CONID:") + std::to_string(c.conId);
        // conId should normally be present. Retain a deterministic complete
        // identity fallback rather than collapsing option/future series by
        // symbol and undercounting gross position.
        std::ostringstream key;
        key << "CONTRACT:" << c.secType << '|' << c.symbol << '|'
            << c.currency << '|' << c.exchange << '|'
            << c.primaryExchange << '|' << c.lastTradeDateOrContractMonth
            << '|' << c.right << '|' << std::setprecision(17) << c.strike
            << '|' << c.multiplier << '|' << c.tradingClass << '|'
            << c.localSymbol;
        return key.str();
    }
    void updatePortfolio(const Contract& c, Decimal pos, double marketPrice, double, double averageCost, double, double, const std::string& account) override {
        if (pos == UNSET_DECIMAL) return;
        IBEvent event = MakeIBEvent(
            IBEventType::PortfolioUpdate,
            0,
            BuildPositionKey(c),
            "",
            DecimalFunctions::decimalToDouble(pos),
            averageCost,
            marketPrice);
        event.account = account;
        event.contract = BuildContractLite(c);
        PushEvent(std::move(event));
    }
    // The dedicated service never consumes the unscoped reqPositions stream.
    // Only request-identified, account-scoped positionMulti callbacks below
    // are allowed to form an authoritative generation.
    void position(const std::string&, const Contract&, Decimal, double) override {}
    void positionEnd() override {}
    void positionMulti(
        int reqId,
        const std::string& account,
        const std::string& modelCode,
        const Contract& c,
        Decimal pos,
        double averageCost) override {
        if (!m_positionsSubscribed ||
            !m_positionsRequestFence.IsCurrent(reqId) ||
            account != m_params.account ||
            !modelCode.empty() ||
            pos == UNSET_DECIMAL || c.secType == "CASH") return;
        IBEvent event = MakeIBEvent(
            m_positionsInitialDownloadPending ?
                IBEventType::PositionSnapshotItem :
                IBEventType::PositionMonitorUpdate,
            static_cast<long long>(reqId),
            BuildPositionKey(c),
            "",
            DecimalFunctions::decimalToDouble(pos),
            averageCost);
        event.requestId = reqId;
        event.account = account;
        event.contract = BuildContractLite(c);
        PushEvent(std::move(event));
    }
    void positionMultiEnd(int reqId) override {
        if (!m_positionsSubscribed ||
            !m_positionsInitialDownloadPending ||
            !m_positionsRequestFence.IsCurrent(reqId)) return;
        // Keep the current subscription active. A later explicit refresh
        // cancels it and begins a unique request generation.
        m_positionsInitialDownloadPending = false;
        IBEvent event = MakeIBEvent(
            IBEventType::PositionEnd,
            static_cast<long long>(reqId), "", "", 0.0);
        event.requestId = reqId;
        event.account = m_params.account;
        PushEvent(std::move(event));
    }
    void updateAccountTime(const std::string&) override {}
    void accountDownloadEnd(const std::string&) override {}

private:
    void PublishCombinedAccountSnapshotEnd() {
        if (!m_accountSummaryEndObserved ||
            !m_accountDownloadEndObserved) return;
        Trace("account_refresh.combined_end summary_req=" +
            std::to_string(m_activeAccountSummaryReqId) +
            " multi_req=" +
            std::to_string(m_activeAccountUpdatesReqId));
        if (m_accountSummarySubscribed) {
            m_client.cancelAccountSummary(m_activeAccountSummaryReqId);
            m_accountSummarySubscribed = false;
        }
        m_accountSummaryEndObserved = false;
        m_accountDownloadEndObserved = false;
        IBEvent event = MakeIBEvent(
            IBEventType::AccountSummaryEnd,
            static_cast<long long>(m_activeAccountSummaryReqId),
            "ACCOUNT_AND_CASH_SNAPSHOT_END", "END", 0.0);
        event.account = m_params.account;
        PushEvent(std::move(event));
    }

    void PushAuthoritativeEvent(IBEvent e) {
        const std::uint64_t before = m_events.OverflowGeneration();
        m_events.Push(std::move(e));
        const std::uint64_t after = m_events.OverflowGeneration();
        if (after != before)
            (void)m_eventIngressAdmissionState.MarkFault();
        if (after != before && ((after % 1000ULL) == 1ULL)) {
            Trace("event_queue_overflow generation=" + std::to_string(static_cast<unsigned long long>(after))
                + " dropped=" + std::to_string(static_cast<unsigned long long>(m_events.DroppedEventCount())));
        }
    }

    // Callback-side publication must never wait for the authoritative queue
    // consumer. If the queue mutex is busy, retain an atomic loss witness and
    // publish the overflow marker from TryDequeueEvent/FlushDeferredIngress.
    void PushCallbackEvent(IBEvent e) {
        const std::uint64_t epoch = e.connectionEpoch;
        try {
            bool overflowed = false;
            if (!m_events.TryPush(std::move(e), overflowed)) {
                (void)m_eventIngressAdmissionState.MarkFault();
                m_ingressDropEpoch.store(epoch, std::memory_order_release);
                m_ingressDropCount.fetch_add(1, std::memory_order_acq_rel);
                return;
            }
            if (overflowed)
                (void)m_eventIngressAdmissionState.MarkFault();
        }
        catch (...) {
            // EWrapper callbacks must not let an allocation failure unwind
            // through the SDK reader.  Convert it to the same explicit loss
            // witness used by a busy queue and keep the sender fail-closed.
            (void)m_eventIngressAdmissionState.MarkFault();
            m_ingressDropEpoch.store(epoch, std::memory_order_release);
            m_ingressDropCount.fetch_add(1, std::memory_order_acq_rel);
        }
    }

    void PublishIngressDropNotice() {
        const std::uint64_t dropped =
            m_ingressDropCount.exchange(0, std::memory_order_acq_rel);
        if (dropped == 0) return;
        const std::uint64_t epoch =
            m_ingressDropEpoch.load(std::memory_order_acquire);
        m_events.RecordDroppedEvent(epoch, dropped);
    }

    void PushEvent(IBEvent e) {
        const std::uint64_t currentEpoch =
            m_connectionEpoch.load(std::memory_order_acquire);
        // Internal EWrapper callbacks arrive with epoch 0 and are stamped
        // here.  Preserve an explicit non-zero epoch so a delayed callback
        // cannot be mistaken for evidence from the current transport.
        if (e.connectionEpoch == 0) e.connectionEpoch = currentEpoch;
        // A callback explicitly tied to an older epoch is retained for the
        // adapter's stale-event filter, but must not poison the admission
        // state of this wrapper.  Future/mismatched epochs remain blocking so
        // an impossible handoff fails closed.
        const bool staleEpoch = e.connectionEpoch != 0 &&
            e.connectionEpoch < currentEpoch;
        const std::uint64_t previousSequence =
            m_eventIngressFarmCallbackSequence.fetch_add(
                1, std::memory_order_acq_rel);
        const std::uint64_t callbackSequence = previousSequence + 1;
        if (callbackSequence == 0)
            m_eventIngressFarmMarker.MarkUnsafe();
        // A CASH-farm 2119 is recoverable while startup is idle, but it must
        // still participate in the same callback/sender linearization once a
        // quote admission is open.  The marker compares both epoch and this
        // callback sequence, so a stale/older 2104 cannot erase a newer 2119.
        const bool cashFarmWarning = IsCashFarmWarningEvent(e);
        if (cashFarmWarning)
            m_eventIngressFarmMarker.ObserveWarning(
                e.connectionEpoch, callbackSequence);
        else if (IsCashFarmReadyEvent(e))
            m_eventIngressFarmMarker.ObserveReady(
                e.connectionEpoch, callbackSequence);
        const bool blocking = IsAdmissionBlockingEvent(e);
        if (blocking && !staleEpoch) {
            // A callback never waits for the sender.  The state CAS is the
            // callback side of the same linearization point as ReqMktData's
            // Open -> Reserved transition.  If the sender already reserved,
            // the callback makes that reservation permanently bad so the
            // runtime must clean it up and fail closed.
            const IBMarketDataAdmissionState::CallbackDisposition disposition =
                m_eventIngressAdmissionState.ObserveBlockingCallback();
            (void)disposition;
        } else if (cashFarmWarning && !staleEpoch) {
            const IBMarketDataAdmissionState::CallbackDisposition disposition =
                m_eventIngressAdmissionState.ObserveBlockingCallback(false);
            (void)disposition;
        }
        const bool defer =
            m_eventIngressAdmissionActive.load(std::memory_order_acquire) ||
            m_eventIngressFenceHeld.load(std::memory_order_acquire) ||
            m_eventIngressSendActive.load(std::memory_order_acquire) ||
            m_eventIngressAdmissionState.IsDeferred();
        if (defer) {
            // This mutex protects only the callback queue.  Never wait for it
            // on the EReader path: a concurrent flush may be doing durable
            // queue publication while an SDK disconnect/join is in flight.
            std::unique_lock<std::mutex> lock(
                m_eventIngressAdmissionMutex, std::try_to_lock);
            if (!lock.owns_lock()) {
                // We cannot safely order this callback against the closing
                // transaction.  Preserve the event in the authoritative
                // queue, and make the packed state a blocking witness so no
                // quote leg can be published from this admission.
                (void)m_eventIngressAdmissionState.MarkFault();
                PushCallbackEvent(std::move(e));
                return;
            }
            // Recheck after taking the queue lock: End/Complete may have
            // changed the phase while this callback was entering.
            const bool stillDeferred =
                m_eventIngressAdmissionActive.load(std::memory_order_acquire) ||
                m_eventIngressFenceHeld.load(std::memory_order_acquire) ||
                m_eventIngressSendActive.load(std::memory_order_acquire) ||
                m_eventIngressAdmissionState.IsDeferred();
            if (stillDeferred) {
                static const std::size_t kMaxDeferredIngressEvents = 20000;
                if (m_deferredIngressEvents.size() >=
                    kMaxDeferredIngressEvents) {
                    m_deferredIngressEvents.pop_front();
                    ++m_deferredIngressDropped;
                    // Loss during an admission transaction is itself a
                    // blocking witness; do not wait for the synthetic
                    // overflow callback at close before suppressing another
                    // quote leg.
                    (void)m_eventIngressAdmissionState.MarkFault();
                }
                try {
                    m_deferredIngressEvents.push_back(std::move(e));
                }
                catch (...) {
                    // Keep the callback boundary noexcept. The dropped event
                    // is surfaced as an explicit overflow marker at close.
                    ++m_deferredIngressDropped;
                    (void)m_eventIngressAdmissionState.MarkFault();
                }
                return;
            }
            lock.unlock();
            PushCallbackEvent(std::move(e));
            return;
        }
        PushCallbackEvent(std::move(e));
    }

    void EndMarketDataAdmissionSend(std::uint64_t generation) {
        // The SDK call has returned; settle only the generation that reserved
        // it.  A late cleanup from an older request can never reopen a newer
        // admission generation.
        const std::uint64_t activeGeneration =
            m_eventIngressSendGeneration.load(std::memory_order_acquire);
        if (activeGeneration != generation) {
            (void)m_eventIngressAdmissionState.MarkFault();
            // If there is no newer send, clear a stale hint so a malformed
            // cleanup cannot leave queue handoff permanently wedged.  Never
            // clear a different non-zero generation: its owner still has to
            // settle its own SDK call.
            if (activeGeneration == 0)
                m_eventIngressSendActive.store(false,
                    std::memory_order_release);
            return;
        }
        const bool keepOpen =
            m_eventIngressAdmissionActive.load(std::memory_order_acquire) ||
            m_eventIngressFenceHeld.load(std::memory_order_acquire);
        if (!m_eventIngressAdmissionState.EndSend(generation, keepOpen))
            (void)m_eventIngressAdmissionState.MarkFault();
        m_eventIngressSendActive.store(false, std::memory_order_release);
        m_eventIngressSendGeneration.store(0, std::memory_order_release);
        if (!m_eventIngressAdmissionActive.load(std::memory_order_acquire) &&
            !m_eventIngressFenceHeld.load(std::memory_order_acquire)) {
            if (!m_eventIngressAdmissionState.Complete())
                (void)m_eventIngressAdmissionState.MarkFault();
            FlushDeferredIngress();
        }
    }

    static bool IsAdmissionBlockingEvent(const IBEvent& event) {
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
            return true; // unknown broker error: keep the send gate closed
        switch (code) {
        case 2104: // market-data farm connection OK
        case 2106: // HMDS farm connection OK
        case 2107: // HMDS farm inactive (not needed for live quotes)
        case 2108:
        case 2109: // outside-RTH notice
        case 2119: // CASH-farm status is handled by the runtime gate; it is
                   // promoted to a blocking witness only while admission is
                   // formally open (see PushEvent above).
        case 2158: // sec-def farm connection OK
        case 1102: // connectivity restored; data maintained
            return false;
        case 1101: // connectivity restored; data lost: force a fresh
                    // admission/recovery boundary before another quote send
            return true;
        default:
            // Unknown and all other broker errors keep the admission closed;
            // routing will preserve the exact callback/reason.
            return true;
        }
    }

    static bool IsCashFarmWarningEvent(const IBEvent& event) {
        return event.type == IBEventType::Error && event.key == "2119";
    }

    static bool IsCashFarmReadyEvent(const IBEvent& event) {
        if (event.type != IBEventType::Error || event.key != "2104")
            return false;
        std::string description = event.value;
        std::transform(description.begin(), description.end(),
                       description.begin(), [](unsigned char value) {
            return static_cast<char>(std::tolower(value));
        });
        return description.find("cashfarm") != std::string::npos;
    }

    void FlushDeferredIngress() {
        // Swap under the short queue mutex, then publish outside it.  This is
        // the important EReader/join boundary: callbacks never wait behind a
        // potentially large authoritative publication loop.
        PublishIngressDropNotice();
        std::deque<IBEvent> pending;
        std::uint64_t dropped = 0;
        {
            std::lock_guard<std::mutex> lock(m_eventIngressAdmissionMutex);
            pending.swap(m_deferredIngressEvents);
            dropped = m_deferredIngressDropped;
            m_deferredIngressDropped = 0;
        }
        if (dropped != 0) {
            IBEvent overflow;
            overflow.type = IBEventType::EventQueueOverflow;
            overflow.connectionEpoch = m_connectionEpoch.load();
            overflow.key = "EVENT_QUEUE_OVERFLOW";
            overflow.value =
                "AUTHORITATIVE_STATE_INVALID_REQUIRES_RESYNC";
            overflow.number = static_cast<double>(dropped);
            overflow.droppedEventCount = dropped;
            overflow.overflowGeneration =
                m_deferredIngressOverflowGeneration.fetch_add(
                    1, std::memory_order_acq_rel) + 1;
            PushAuthoritativeEvent(std::move(overflow));
        }
        for (std::deque<IBEvent>::const_iterator it = pending.begin();
             it != pending.end(); ++it)
            PushAuthoritativeEvent(*it);
        // A callback may have lost the queue mutex while the batch was being
        // published.  Surface that loss promptly on the next control flush;
        // TryDequeueEvent also performs this publication for ordinary drains.
        PublishIngressDropNotice();
    }

    IBConnectParams m_params;
    std::atomic<bool> m_connected{ false };
    std::atomic<bool> m_gotNextValidId{ false };
    std::atomic<std::uint64_t> m_connectionEpoch{ 0 };
    long m_lastValidOrderId = -1;
    int m_marketDataType = 1;
    std::string m_status = "IB_INIT";
    std::string m_lastError;

    IBAuthoritativeEventQueue m_events;

    EReaderOSSignal m_signal;
    EClientSocket m_client;
    std::unique_ptr<EReader> m_reader;
    std::shared_ptr<std::recursive_mutex> m_eventIngressFence;
    mutable std::mutex m_eventIngressAdmissionMutex;
    // The atomics below are lifecycle hints used for queue handoff.  The
    // admission state word is the sole authority for send/fault ordering.
    std::atomic<bool> m_eventIngressSendActive{ false };
    std::atomic<bool> m_eventIngressAdmissionActive{ false };
    std::atomic<bool> m_eventIngressFenceHeld{ false };
    std::atomic<std::uint64_t> m_eventIngressAdmissionGeneration{ 0 };
    std::atomic<std::uint64_t> m_eventIngressSendGeneration{ 0 };
    IBMarketDataAdmissionState m_eventIngressAdmissionState;
    std::deque<IBEvent> m_deferredIngressEvents;
    std::uint64_t m_deferredIngressDropped = 0;
    std::atomic<std::uint64_t> m_deferredIngressOverflowGeneration{ 0 };
    std::atomic<std::uint64_t> m_ingressDropCount{ 0 };
    std::atomic<std::uint64_t> m_ingressDropEpoch{ 0 };
    std::atomic<std::uint64_t> m_eventIngressFarmCallbackSequence{ 0 };
    IBCashFarmAdmissionMarker m_eventIngressFarmMarker;
    std::atomic<bool> m_terminalIngressHalted{ false };
    bool m_accountSummarySubscribed = false;
    bool m_accountUpdatesSubscribed = false;
    bool m_accountUpdatesInitialDownloadPending = false;
    bool m_accountSummaryEndObserved = false;
    bool m_accountDownloadEndObserved = false;
    int m_nextAccountRefreshReqId = kInitialAccountRefreshReqId;
    int m_activeAccountSummaryReqId = 0;
    int m_activeAccountUpdatesReqId = 0;
    bool m_positionsSubscribed = false;
    bool m_positionsInitialDownloadPending = false;
    IBPositionsRequestFence m_positionsRequestFence;
    std::unordered_map<long, double> m_orderTotalQty;
};

#else

class IBApiWrapperStub : public IIBApiWrapper {
public:
    void SetConnectionEpoch(std::uint64_t connectionEpoch) override { m_connectionEpoch = connectionEpoch; }
    std::uint64_t GetConnectionEpoch() const override { return m_connectionEpoch; }
    bool Connect(const IBConnectParams&) override { m_connected = false; return false; }
    void Disconnect() override { m_connected = false; }
    bool IsConnected() const override { return m_connected; }
    const char* GetStatusString() const override { return m_connected ? "IB_CONNECTED" : "IB_STUB_NOT_LINKED"; }

    bool ReqAccountSummary() override { return false; }
    bool ReqPositions() override { return false; }
    bool ReqOpenOrders() override { return false; }
    bool ReqAllOpenOrders() override { return false; }
    bool ReqCompletedOrders() override { return false; }
    bool ReqExecutions(int) override { return false; }
    bool ReqMktData(int, const IBContractLite&) override { return false; }
    bool CancelMktData(int) override { return false; }

    bool PlaceOrder(long, const IBContractLite&, const IBOrderLite&) override { return false; }
    bool CancelOrder(long) override { return false; }

    bool PollOnce(int) override { return false; }
    bool TryDequeueEvent(IBEvent&) override { return false; }
    long GetLastValidOrderId() const override { return -1; }

private:
    bool m_connected = false;
    std::uint64_t m_connectionEpoch = 0;
};

#endif

IIBApiWrapper* CreateIBApiWrapper() {
#ifdef HEPTA_ENABLE_IBAPI
    return new IBApiWrapperReal();
#else
    return new IBApiWrapperStub();
#endif
}
