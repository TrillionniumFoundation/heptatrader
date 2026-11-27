#pragma once

#include <chrono>
#include <cstdint>
#include <string>

// A bounded first-pair wait, never market-data-send or trading authority.
// The runtime arms this only AFTER the original native admission transaction
// has completed for one CASH contract in an explicit user observation profile.
// All access is serialized by the runtime's authoritative-quote send mutex.
class IbObservationInitialFarmWarmup
{
public:
    using Clock = std::chrono::steady_clock;
    void Reset() { *this = IbObservationInitialFarmWarmup(); }
    void Arm(std::uint64_t epoch, Clock::time_point deadline,
             std::uint64_t observedAtMs)
    {
        Reset();
        m_armed = epoch != 0 && observedAtMs != 0;
        m_epoch = epoch;
        m_deadline = deadline;
        m_wallHighWater = observedAtMs;
    }
    bool Armed() const { return m_armed; }
    bool Failed() const { return m_failed; }
    bool WaitingForSpecificFarm() const { return m_pendingFarms != 0; }
    bool HadConnectingWarning() const { return m_hadWarning; }

    bool ObserveConnecting(const std::string& farm, std::uint64_t epoch,
                           Clock::time_point now, std::uint64_t observedAtMs)
    {
        const unsigned bit = FarmBit(farm);
        if (!Current(epoch, now, observedAtMs) || bit == 0 || ++m_warnings > 4)
        {
            if (m_armed) m_failed = true;
            return false;
        }
        m_hadWarning = true;
        m_pendingFarms |= bit;
        m_restoredAtMs = 0;
        m_stableSince = Clock::time_point();
        return true;
    }

    // A later hfarm 2104 cannot discharge a cashfarm 2119. If both farms
    // reported connecting, each requires its own positive same-epoch callback.
    void ObserveReady(const std::string& farm, std::uint64_t epoch,
                      Clock::time_point now, std::uint64_t observedAtMs)
    {
        if (!m_armed || !m_hadWarning) return;
        if (!Current(epoch, now, observedAtMs)) return;
        const unsigned bit = FarmBit(farm);
        if ((m_pendingFarms & bit) == 0) return;
        m_pendingFarms &= ~bit;
        if (m_pendingFarms == 0)
        {
            m_restoredAtMs = observedAtMs;
            m_stableSince = now;
        }
    }

    bool CanFinish(std::uint64_t epoch, Clock::time_point now,
                   std::uint64_t observedAtMs, std::uint64_t bidAtMs,
                   std::uint64_t askAtMs, std::uint64_t maxAgeMs,
                   int stabilityWindowMs)
    {
        if (!m_armed) return true; // Other runtime profiles retain their gate.
        if (!Current(epoch, now, observedAtMs)) return false;
        if (!m_hadWarning) return true;
        return m_pendingFarms == 0 && m_restoredAtMs != 0 &&
            m_stableSince != Clock::time_point() && stabilityWindowMs >= 0 &&
            now - m_stableSince >= std::chrono::milliseconds(stabilityWindowMs) &&
            bidAtMs > m_restoredAtMs && askAtMs > m_restoredAtMs &&
            bidAtMs <= observedAtMs && askAtMs <= observedAtMs && maxAgeMs != 0 &&
            observedAtMs - bidAtMs < maxAgeMs && observedAtMs - askAtMs < maxAgeMs;
    }
    void Complete() { m_armed = false; }

private:
    static unsigned FarmBit(const std::string& farm)
    {
        return farm == "cashfarm" ? 1u : farm == "hfarm" ? 2u : 0u;
    }
    bool Current(std::uint64_t epoch, Clock::time_point now,
                 std::uint64_t observedAtMs)
    {
        if (!m_armed || m_failed) return false;
        if (epoch == 0 || epoch != m_epoch || now >= m_deadline ||
            observedAtMs == 0 || observedAtMs < m_wallHighWater)
        {
            m_failed = true;
            return false;
        }
        m_wallHighWater = observedAtMs;
        return true;
    }
    bool m_armed = false;
    bool m_failed = false;
    bool m_hadWarning = false;
    unsigned m_pendingFarms = 0;
    unsigned m_warnings = 0;
    std::uint64_t m_epoch = 0;
    std::uint64_t m_wallHighWater = 0;
    std::uint64_t m_restoredAtMs = 0;
    Clock::time_point m_deadline;
    Clock::time_point m_stableSince;
};
