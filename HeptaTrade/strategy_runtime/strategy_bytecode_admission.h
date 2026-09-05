#pragma once

#include <cstdint>
#include <memory>
#include <mutex>
#include <utility>

// Reservations, not measured RSS/CPU: all runners in one supervision domain
// must share this object. The ordinary runner constructor uses Default().
struct StrategyBytecodeAdmissionLimits
{
    std::uint32_t maximumInvocations = 8;
    std::uint64_t maximumReservedAddressSpaceBytes = 512ULL << 20;
    std::uint64_t maximumReservedSteps = 8000000;
};

struct StrategyBytecodeAdmissionSnapshot
{
    bool configured = false;
    std::uint32_t activeInvocations = 0;
    std::uint64_t reservedAddressSpaceBytes = 0;
    std::uint64_t reservedSteps = 0;
};

class StrategyBytecodeAdmission
{
    struct State
    {
        explicit State(const StrategyBytecodeAdmissionLimits& value) : limits(value)
        {
            usage.configured = limits.maximumInvocations > 0 && limits.maximumInvocations <= 64 &&
                limits.maximumReservedAddressSpaceBytes >= (1ULL << 20) &&
                limits.maximumReservedAddressSpaceBytes <= (64ULL << 30) &&
                limits.maximumReservedSteps > 0 && limits.maximumReservedSteps <= 64000000;
        }
        const StrategyBytecodeAdmissionLimits limits;
        std::mutex mutex;
        StrategyBytecodeAdmissionSnapshot usage;
    };

public:
    class Reservation
    {
    public:
        Reservation() noexcept = default;
        Reservation(const Reservation&) = delete;
        Reservation& operator=(const Reservation&) = delete;
        Reservation(Reservation&& other) noexcept
            : m_state(std::move(other.m_state)), m_bytes(other.m_bytes),
              m_steps(other.m_steps), m_reason(other.m_reason) {}
        Reservation& operator=(Reservation&& other) noexcept
        {
            if (this != &other)
            {
                Reset();
                m_state = std::move(other.m_state);
                m_bytes = other.m_bytes;
                m_steps = other.m_steps;
                m_reason = other.m_reason;
            }
            return *this;
        }
        ~Reservation() { Reset(); }
        bool IsValid() const noexcept { return static_cast<bool>(m_state); }
        const char* ReasonCode() const noexcept { return m_reason; }
        void Reset() noexcept
        {
            // Keep the state and its mutex alive even if the public admission
            // object was destroyed while this reservation was outstanding.
            auto state = std::move(m_state);
            if (!state) return;
            std::lock_guard<std::mutex> lock(state->mutex);
            --state->usage.activeInvocations;
            state->usage.reservedAddressSpaceBytes -= m_bytes;
            state->usage.reservedSteps -= m_steps;
        }
    private:
        explicit Reservation(const char* reason) noexcept : m_reason(reason) {}
        Reservation(std::shared_ptr<State> state, std::uint64_t bytes, std::uint64_t steps) noexcept
            : m_state(std::move(state)), m_bytes(bytes), m_steps(steps),
              m_reason("STRATEGY_VM_ADMITTED") {}
        std::shared_ptr<State> m_state;
        std::uint64_t m_bytes = 0, m_steps = 0;
        const char* m_reason = "STRATEGY_VM_NOT_ADMITTED";
        friend class StrategyBytecodeAdmission;
    };

    explicit StrategyBytecodeAdmission(const StrategyBytecodeAdmissionLimits& limits = {})
        : m_state(std::make_shared<State>(limits)) {}
    StrategyBytecodeAdmission(const StrategyBytecodeAdmission&) = delete;
    StrategyBytecodeAdmission& operator=(const StrategyBytecodeAdmission&) = delete;
    static const char* Version() noexcept { return "hepta.strategy-bytecode-admission.v1"; }

    static std::shared_ptr<StrategyBytecodeAdmission> Default()
    {
        // Shared by ordinary runner instances in this linked runtime image.
        // Separate processes or independently loaded runtime copies are not
        // coordinated by a C++ static; cross-process quotas require supervision.
        static const auto admission = std::make_shared<StrategyBytecodeAdmission>();
        return admission;
    }

    Reservation TryAcquire(std::uint64_t addressSpaceBytes, std::uint64_t steps)
    {
        if (addressSpaceBytes < (1ULL << 20) || addressSpaceBytes > (1ULL << 30) ||
            steps == 0 || steps > 1000000)
            return Reservation("STRATEGY_VM_RESERVATION_INVALID");
        std::lock_guard<std::mutex> lock(m_state->mutex);
        auto& used = m_state->usage;
        const auto& limit = m_state->limits;
        if (!used.configured) return Reservation("STRATEGY_VM_ADMISSION_CONFIG_INVALID");
        if (used.activeInvocations >= limit.maximumInvocations)
            return Reservation("STRATEGY_VM_SHARED_CAPACITY_EXHAUSTED");
        // Subtraction comparisons cannot wrap even for rejected large requests.
        if (addressSpaceBytes > limit.maximumReservedAddressSpaceBytes - used.reservedAddressSpaceBytes)
            return Reservation("STRATEGY_VM_SHARED_MEMORY_EXHAUSTED");
        if (steps > limit.maximumReservedSteps - used.reservedSteps)
            return Reservation("STRATEGY_VM_SHARED_FUEL_EXHAUSTED");
        // Copying shared_ptr and moving Reservation do not allocate. Prepare
        // the return value before atomically publishing all three counters.
        Reservation result(m_state, addressSpaceBytes, steps);
        ++used.activeInvocations;
        used.reservedAddressSpaceBytes += addressSpaceBytes;
        used.reservedSteps += steps;
        return result;
    }

    StrategyBytecodeAdmissionSnapshot Snapshot() const
    {
        std::lock_guard<std::mutex> lock(m_state->mutex);
        return m_state->usage;
    }

private:
    const std::shared_ptr<State> m_state;
};
