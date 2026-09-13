#pragma once

#include <algorithm>
#include <array>
#include <chrono>
#include <cstdint>
#include <limits>
#include <ostream>

// Protected by the journal's existing mutex. Counters are process-instance
// observations, not persistence or proof that a particular write succeeded.
struct OmsLatencySummary
{
    std::uint64_t samples = 0;
    std::uint64_t totalNs = 0;
    std::uint64_t maximumNs = 0;
    std::uint64_t lastNs = 0;
    bool saturated = false;
    // Non-cumulative fixed buckets, nanoseconds. Last bucket is +Inf.
    std::array<std::uint64_t, 11> buckets{};
    static std::uint64_t BucketUpper(std::size_t i) noexcept
    {
        static const std::uint64_t bounds[] = {1000, 10000, 100000, 1000000,
            5000000, 10000000, 50000000, 100000000, 1000000000, 10000000000ULL};
        return i < 10 ? bounds[i] : std::numeric_limits<std::uint64_t>::max();
    }

    void Observe(std::uint64_t ns) noexcept
    {
        const auto maximum = std::numeric_limits<std::uint64_t>::max();
        if (samples == maximum) saturated = true;
        else ++samples;
        if (ns > maximum - totalNs) { totalNs = maximum; saturated = true; }
        else totalNs += ns;
        std::size_t bucket = 0;
        while (bucket < 10 && ns > BucketUpper(bucket)) ++bucket;
        if (buckets[bucket] == maximum) saturated = true;
        else ++buckets[bucket];
        maximumNs = std::max(maximumNs, ns);
        lastNs = ns;
    }
};

// Construct AFTER acquiring the journal mutex; destruction/Finish must run
// while it is still held. A replay explicitly finishes before releasing the
// lock and invoking arbitrary/reentrant recovery callbacks.
class OmsScopedLatencySample
{
public:
    using Clock = std::chrono::steady_clock;
    explicit OmsScopedLatencySample(OmsLatencySummary& target,
                                   Clock::time_point start = Clock::now()) noexcept
        : m_target(target), m_start(start) {}
    ~OmsScopedLatencySample() noexcept { Finish(); }
    OmsScopedLatencySample(const OmsScopedLatencySample&) = delete;
    OmsScopedLatencySample& operator=(const OmsScopedLatencySample&) = delete;

    void Finish(Clock::time_point now = Clock::now()) noexcept
    {
        if (m_finished) return;
        m_finished = true;
        // No elapsed duration is inferred from the epoch/wall clock.
        const auto ns = std::chrono::duration_cast<std::chrono::nanoseconds>(now - m_start).count();
        m_target.Observe(ns > 0 ? static_cast<std::uint64_t>(ns) : 0);
    }
private:
    OmsLatencySummary& m_target;
    Clock::time_point m_start;
    bool m_finished = false;
};

inline void WriteOmsLatencyJson(std::ostream& out, const OmsLatencySummary& value)
{
    out << "{\"samples\":" << value.samples << ",\"total_ns\":" << value.totalNs
        << ",\"max_ns\":" << value.maximumNs << ",\"last_ns\":" << value.lastNs
        << ",\"saturated\":" << (value.saturated ? "true" : "false") << ",\"bucket_counts\":[";
    for (std::size_t i = 0; i < value.buckets.size(); ++i)
    {
        if (i) out << ',';
        out << value.buckets[i];
    }
    out << "],\"bucket_upper_ns\":[";
    for (std::size_t i = 0; i < value.buckets.size(); ++i)
    {
        if (i) out << ',';
        if (i < 10) out << OmsLatencySummary::BucketUpper(i);
        else out << "null";
    }
    out << "]}";
}
