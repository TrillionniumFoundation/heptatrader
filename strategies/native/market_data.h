#pragma once

// Native research API. No venue SDK, credentials, authoritative state or IO.
// Semantic migration context and upstream attribution: docs/technical/heptadll-integration.md.
#include <cstddef>
#include <cstdint>
#include <deque>
#include <string>
#include <vector>

namespace hepta { namespace research {
struct SessionWindow {
    std::string tradingDay;
    std::int64_t beginMs = 0;
    std::int64_t endMs = 0; // Half-open [begin,end), UTC epoch milliseconds.
};
class SessionCalendar {
public:
    explicit SessionCalendar(std::vector<SessionWindow> windows);
    const SessionWindow& At(std::int64_t timestampMs) const;
    SessionWindow TradingDay(const std::string& label) const;
private:
    std::vector<SessionWindow> windows_;
};
struct Tick {
    std::string instrument;
    std::int64_t timestampMs = 0;
    std::uint64_t sequence = 0;
    double price = 0;
    std::uint64_t cumulativeVolume = 0;
};
struct Bar {
    std::string instrument;
    std::string tradingDay;
    std::int64_t beginMs = 0, endMs = 0;
    double open = 0, high = 0, low = 0, close = 0;
    std::uint64_t volume = 0, observations = 0;
    bool complete = false;
};
enum class InitialVolume { Baseline, IncludeCumulative };
enum class TickDisposition { Applied, Duplicate };
struct TickObservation {
    std::string tradingDay;
    std::uint64_t volumeDelta = 0;
    bool duplicate = false;
};
// Stream ordering and volume validation is shared by bars and offline replay.
// Validate is side-effect free; Commit is called only after the consumer succeeds.
class TickCursor {
public:
    TickCursor(std::string instrument, InitialVolume initialVolume);
    TickObservation Validate(const Tick& tick, const SessionCalendar& calendar) const;
    void Commit(const Tick& tick, const TickObservation& observation);
private:
    std::string instrument_, tradingDay_;
    InitialVolume initialVolume_;
    bool initialized_ = false;
    Tick last_;
};
class BarBuilder {
public:
    // intervalMs == 0 builds one bar per explicitly supplied trading-day label.
    BarBuilder(std::string instrument, SessionCalendar calendar,
               std::int64_t intervalMs, InitialVolume initialVolume);
    TickDisposition Push(const Tick& tick, std::vector<Bar>& completed);
    void AdvanceWatermark(std::int64_t timestampMs, std::vector<Bar>& completed);
    // End-of-input exports an incomplete tail, never an invented completed bar.
    void Finish(std::vector<Bar>& tail);
private:
    SessionCalendar calendar_;
    TickCursor cursor_;
    std::int64_t intervalMs_, watermark_ = 0;
    bool active_ = false, finished_ = false;
    Bar current_;
};
void ValidateBar(const Bar& bar);
Bar MergeBars(const std::vector<Bar>& bars);
class BarWindow {
public:
    explicit BarWindow(std::size_t capacity);
    void Push(const Bar& bar);
    // Offsets count backwards: zero is the most recent completed bar.
    const Bar& Recent(std::size_t offset = 0) const;
    double MeanClose(std::size_t count) const;
    std::size_t Highest(std::size_t count, bool newestTie = true) const;
    std::size_t Lowest(std::size_t count, bool newestTie = true) const;
    std::size_t Size() const { return bars_.size(); }
private:
    std::size_t capacity_;
    std::deque<Bar> bars_;
};
bool ValidInstrument(const std::string& instrument);
const char* TickCsvHeader();
Tick ParseTickCsv(const std::string& row); // Strict normalized v1; not a legacy binary decoder.
std::string FormatTickCsv(const Tick& tick);
} }
