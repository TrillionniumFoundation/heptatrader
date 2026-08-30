#pragma once

#include <array>
#include <chrono>
#include <cstddef>
#include <cstdint>
#include <map>
#include <mutex>
#include <string>

struct RuntimeHistogramSnapshot
{
    std::uint64_t count = 0;
    std::uint64_t totalMicros = 0;
    std::uint64_t maxMicros = 0;
    std::array<std::uint64_t, 12> buckets{};
};

// Process-local, bounded telemetry for deterministic runtime transitions.
// Labels are allow-listed and sanitized before entering a series key. Account,
// credential, token, prompt, free-form detail and raw strategy text are never
// accepted by the typed recording helpers below.
class RuntimeTelemetry
{
public:
    static RuntimeTelemetry& Global();

    void IncrementKey(const std::string& metric,
            const std::string& boundedLabels = std::string());
    void SetGaugeKey(const std::string& metric,
           double value,
           const std::string& boundedLabels = std::string());
    void ObserveLatencyKey(const std::string& metric,
                 std::uint64_t micros,
                 const std::string& boundedLabels = std::string());

    std::string SnapshotJson() const;
    std::size_t SeriesCount() const;
    void ResetForTests();

    // Return a bounded, privacy-safe value for an untyped label.  Values that
    // look like credentials, account identifiers or opaque tokens are replaced
    // with the constant "redacted"; malformed values are represented by a
    // deterministic hash so callers cannot inject delimiters into a series
    // key.  The result is always safe to embed in a metric key.
    static std::string BoundedLabel(const std::string& value);

    // Apply the stricter allow-list associated with a label name.  Runtime
    // recording helpers use this entry point so a caller cannot smuggle an
    // account/token value through a value that happens to contain only safe
    // ASCII characters (for example, "DU123456").
    static std::string BoundedLabelFor(const std::string& labelName,
                                       const std::string& value);

private:
    RuntimeTelemetry() = default;
    static std::string Key(const std::string& metric,
                 const std::string& labels);

    static const std::size_t kMaximumSeries = 2048;
    mutable std::mutex m_mutex;
    std::map<std::string, std::uint64_t> m_counters;
    std::map<std::string, double> m_gauges;
    std::map<std::string, RuntimeHistogramSnapshot> m_histograms;
    std::uint64_t m_droppedSeries = 0;
};

class RuntimeLatencyScope
{
public:
    RuntimeLatencyScope(const std::string& metric,
              const std::string& labelName = std::string(),
              const std::string& labelValue = std::string());
    ~RuntimeLatencyScope();

    RuntimeLatencyScope(const RuntimeLatencyScope&) = delete;
    RuntimeLatencyScope& operator=(const RuntimeLatencyScope&) = delete;

private:
    std::string m_metric;
    std::string m_labels;
    std::chrono::steady_clock::time_point m_started;
};

void RuntimeRecordRiskDecision(bool allowed, const std::string& reasonCode);
void RuntimeRecordToolOutcome(const std::string& tool,
                    int status,
                    const std::string& reasonCode);
void RuntimeRecordJournalEvent(const std::string& eventType,
                     const std::string& status,
                     const std::string& reasonCode);
void RuntimeRecordJournalFailure(const std::string& reasonCode);
void RuntimeRecordReconcile(const std::string& operation,
                  const std::string& outcome,
                  const std::string& reasonCode);
void RuntimeRecordKillSwitch(const std::string& state);
