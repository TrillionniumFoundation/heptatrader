#pragma once

#include <cstddef>
#include <cstdint>
#include <deque>
#include <iosfwd>
#include <memory>
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
// Incremental reader: retains no tick history and needs no seekable stream.
// The input must outlive the reader; a cursor cannot be copied or shared between
// threads. The row quota bounds total work; the existing 4096-byte line limit
// bounds parser storage. Construction consumes only the header.
// Next returns false only at clean EOF, leaving output unchanged. A malformed
// row, quota breach or I/O error leaves output/RowsRead unchanged and poisons
// this cursor: clearing the underlying stream cannot silently skip bad input.
class TickCsvReader {
public:
    explicit TickCsvReader(std::istream& input, std::size_t maxRows = 1000000);
    TickCsvReader(const TickCsvReader&) = delete;
    TickCsvReader& operator=(const TickCsvReader&) = delete;
    bool Next(Tick& output);
    std::size_t RowsRead() const { return rowsRead_; }
private:
    std::istream& input_;
    std::size_t maxRows_, rowsRead_ = 0;
    bool finished_ = false, failed_ = false;
};

// Synchronous OFFLINE merge of 1..1024 single-instrument CSV streams using the
// existing TickCsvReader schema. Each nonempty source has a distinct instrument,
// nondecreasing timestamps and increasing sequences (exact adjacent retries are
// emitted unchanged). Inputs are borrowed exclusively and must outlive this
// noncopyable, nonmovable, thread-affine cursor; their vector need not outlive it.
// Construction reads only headers. Next orders by timestamp, then input index,
// preserving each source's row order. This deterministic simulation convention
// is NOT evidence of historical cross-feed arrival/availability or live ordering.
// Storage is O(sources), merge work O(log sources) per row. At most one unread
// head per source is prefetched; maxRows bounds GLOBAL published rows, including
// duplicates, with at most one source-head lookahead per input. No seek/sort-all.
// Clean EOF leaves output/count unchanged. Malformed/unsorted/conflicting rows,
// quota or I/O errors leave that call's output/count unchanged and permanently
// fail the cursor. Already emitted prefixes are not complete valid runs after
// a later failure. No input rewind, deduplication or synthetic tick is performed.
class MergedTickCsvReader {
public:
    explicit MergedTickCsvReader(const std::vector<std::istream*>& inputs,
                                 std::size_t maxRows = 1000000);
    ~MergedTickCsvReader();
    MergedTickCsvReader(const MergedTickCsvReader&) = delete;
    MergedTickCsvReader& operator=(const MergedTickCsvReader&) = delete;
    bool Next(Tick& output);
    std::size_t RowsRead() const;
private:
    struct Impl;
    std::unique_ptr<Impl> impl_;
};

// Eager compatibility helper, implemented using the same incremental decoder.
std::vector<Tick> ReadTicksCsv(std::istream& input, std::size_t maxRows = 1000000);
void WriteTicksCsv(std::ostream& output, const std::vector<Tick>& ticks);
SessionSchedule ReadSessionsCsv(std::istream& input, std::size_t maxRows = 100000);

// Completed single-instrument bars only. The header and all rows are checked;
// partial/overlapping/reversed rows are rejected rather than coerced to complete.
// Header: instrument,trading_day,begin_us,end_us,open,high,low,close,volume,tick_count,complete
// Incremental decoder for the SAME completed-bar schema. Retains only the last
// validated bar for cross-row instrument/day/interval checks, never a history.
// Construction reads only the header; the borrowed stream must outlive this
// noncopyable, thread-affine cursor. Row quota and 4096-byte line bound apply.
// Next returns false only at clean EOF. EOF/error leaves output unchanged;
// malformed rows, quota breaches and I/O failures permanently poison the cursor.
// Mutating a returned bar cannot change the cursor's private validation state.
class BarCsvReader {
public:
    explicit BarCsvReader(std::istream& input, std::size_t maxRows = 1000000);
    BarCsvReader(const BarCsvReader&) = delete;
    BarCsvReader& operator=(const BarCsvReader&) = delete;
    bool Next(Bar& output);
    std::size_t RowsRead() const { return rowsRead_; }
private:
    std::istream& input_;
    std::size_t maxRows_, rowsRead_ = 0;
    bool finished_ = false, failed_ = false;
    Bar previous_;
};
// Eager compatibility helper, sharing exactly the same incremental decoder.
std::vector<Bar> ReadBarsCsv(std::istream& input, std::size_t maxRows = 1000000);
void WriteBarsCsv(std::ostream& output, const std::vector<Bar>& bars);

enum class BarPrice { Open, High, Low, Close };
struct BarSearchResult {
    bool found = false;
    std::size_t index = 0; // Meaningful only when found; relative to retained data.
    std::int64_t beginUs = 0;
    double price = 0;
};
struct ConfirmedExtremum {
    std::size_t index = 0; // Invalidated by retention, erasure or replacement.
    std::int64_t beginUs = 0;
    std::int64_t confirmedAtUs = 0; // End of the LAST required right-hand bar.
    double price = 0;
};

class BarSeries {
public:
    explicit BarSeries(std::size_t capacity);
    void Append(const Bar& bar);
    void Replace(const Bar& bar); // Exact existing interval only.
    void EraseBefore(std::int64_t beginUs);
    void EraseAfter(std::int64_t beginUs);
    std::size_t Size() const { return bars_.size(); }
    const Bar& At(std::size_t index) const;
    const Bar& AtLatest(std::size_t offset = 0) const;
    // Inclusive endpoints, as in the legacy series API.
    std::size_t Highest(std::size_t begin, std::size_t end,
                        bool latestOnTie = true) const;
    std::size_t Lowest(std::size_t begin, std::size_t end,
                       bool latestOnTie = true) const;
    std::size_t Highest(std::size_t begin, std::size_t end, BarPrice field,
                        bool latestOnTie = true) const;
    std::size_t Lowest(std::size_t begin, std::size_t end, BarPrice field,
                       bool latestOnTie = true) const;
    // Search end -> begin, with a strict comparison; no unsigned -1 sentinel.
    BarSearchResult LatestHigher(double threshold, std::size_t begin,
                                 std::size_t end, BarPrice field = BarPrice::High) const;
    BarSearchResult LatestLower(double threshold, std::size_t begin,
                                std::size_t end, BarPrice field = BarPrice::Low) const;
    // Only [begin, observedEnd] is visible. A candidate needs radius complete
    // neighbours on EACH side inside that range. Equal neighbours are not strict
    // extrema. radius==0 returns every visible bar. Results are chronological.
    // Never treat beginUs as signal availability: use confirmedAtUs. Caller must
    // pass the last actually observed bar, not the end of an offline dataset.
    std::vector<ConfirmedExtremum> ConfirmedPeaks(
        std::size_t begin, std::size_t observedEnd, std::size_t radius,
        BarPrice field = BarPrice::High) const;
    std::vector<ConfirmedExtremum> ConfirmedTroughs(
        std::size_t begin, std::size_t observedEnd, std::size_t radius,
        BarPrice field = BarPrice::Low) const;
    // Counts retained bars only; bounded storage cannot certify a full-day count.
    std::size_t RetainedBarsInLatestTradingDay() const;
    double MeanClose(std::size_t count) const;
private:
    std::size_t capacity_;
    std::deque<Bar> bars_;
};
Bar MergeBars(const std::vector<Bar>& bars);

}} // namespace hepta::research
