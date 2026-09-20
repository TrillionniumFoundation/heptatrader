#pragma once

#include <cstddef>
#include <cstdint>
#include <deque>
#include <iosfwd>
#include <string>
#include <vector>

namespace hepta { namespace research {

// Research values, never authoritative execution/quote/position objects.
// Timestamps are UTC microseconds. Sessions and trading-day labels are supplied
// explicitly; this library does not infer current exchange holidays or DST.
struct Tick {
    std::string instrument;
    std::int64_t timestampUs = 0;
    std::uint64_t sequence = 0;
    double price = 0;
    std::int64_t volume = 0; // Incremental quantity, not a cumulative counter.
};
struct SessionWindow {
    std::int64_t openUs = 0;
    std::int64_t closeUs = 0; // Half-open interval [open, close).
    std::string tradingDay;  // Gregorian YYYYMMDD, including night sessions.
};
void ValidateTick(const Tick& tick);
void ValidateTradingDay(const std::string& day);

class SessionSchedule {
public:
    explicit SessionSchedule(std::vector<SessionWindow> windows);
    const SessionWindow& At(std::int64_t timestampUs) const;
    SessionWindow Day(const std::string& tradingDay) const;
private:
    std::vector<SessionWindow> windows_;
};

struct Bar {
    std::string instrument;
    std::string tradingDay;
    std::int64_t beginUs = 0;
    std::int64_t endUs = 0;
    double open = 0, high = 0, low = 0, close = 0;
    std::int64_t volume = 0;
    std::uint64_t tickCount = 0;
    bool complete = false;
};
void ValidateBar(const Bar& bar);

enum class TickOutcome { Updated, ClosedPrevious, Duplicate };

// Thread-affine state. Callers serialize access. periodUs==0 means one bar per
// supplied trading day; positive periods are anchored to each session open.
// No synthetic empty bars or cross-break buckets. Invalid inputs do not mutate.
class BarBuilder {
public:
    BarBuilder(std::string instrument, std::int64_t periodUs,
               SessionSchedule schedule);
    TickOutcome Push(const Tick& tick, Bar& closed);
    // Watermarks are promises: ticks older than a watermark are rejected.
    bool AdvanceWatermark(std::int64_t watermarkUs, Bar& closed);
    bool Current(Bar& partial) const;
private:
    std::string instrument_;
    std::int64_t periodUs_;
    SessionSchedule schedule_;
    std::int64_t watermarkUs_ = 0;
    bool hasLast_ = false, hasBar_ = false;
    Tick last_;
    Bar bar_;
};

// Explicit conversion boundary for exchange cumulative-volume streams. A day
// rollover resets the counter; a decrease within a day is an error, not a reset.
// By default the first observation establishes a zero-volume baseline. Counting
// the first counter requires an explicit guarantee of complete day-start capture.
class CumulativeVolumeDecoder {
public:
    explicit CumulativeVolumeDecoder(std::string instrument, bool countFirstObservation = false);
    Tick Decode(const Tick& cumulativeTick, const std::string& tradingDay);
private:
    std::string instrument_, day_;
    bool initialized_ = false;
    bool countFirstObservation_ = false;
    Tick lastInput_, lastOutput_;
};

// Strict portable CSV, not a decoder for ABI-dependent legacy binary dumps.
// Header: instrument,timestamp_us,sequence,price,volume
std::vector<Tick> ReadTicksCsv(std::istream& input, std::size_t maxRows = 1000000);
void WriteTicksCsv(std::ostream& output, const std::vector<Tick>& ticks);
SessionSchedule ReadSessionsCsv(std::istream& input, std::size_t maxRows = 100000);

class BarSeries {
public:
    explicit BarSeries(std::size_t capacity);
    void Append(const Bar& bar);
    void Replace(const Bar& bar); // Exact existing interval only.
    void EraseBefore(std::int64_t beginUs);
    void EraseAfter(std::int64_t beginUs);
    std::size_t Size() const { return bars_.size(); }
    const Bar& At(std::size_t index) const;
    // Inclusive endpoints, as in the legacy series API.
    std::size_t Highest(std::size_t begin, std::size_t end,
                        bool latestOnTie = true) const;
    std::size_t Lowest(std::size_t begin, std::size_t end,
                       bool latestOnTie = true) const;
    double MeanClose(std::size_t count) const;
private:
    std::size_t capacity_;
    std::deque<Bar> bars_;
};
Bar MergeBars(const std::vector<Bar>& bars);

}} // namespace hepta::research
