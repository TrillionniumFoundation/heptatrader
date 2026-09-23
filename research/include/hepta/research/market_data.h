#pragma once

#include <cstddef>
#include <cstdint>
#include <deque>
#include <functional>
#include <map>
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
    // End this stream without claiming completeness. Exports a populated tail
    // once with complete=false. Empty/repeated Finish leaves output unchanged.
    // No timestamp/price/volume is invented; Push/AdvanceWatermark then reject.
    bool Finish(Bar& incompleteTail);
    bool Finished() const { return finished_; }
private:
    std::string instrument_;
    std::int64_t periodUs_;
    SessionSchedule schedule_;
    std::int64_t watermarkUs_ = 0;
    bool hasLast_ = false, hasBar_ = false, finished_ = false;
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

// Shared CSV stream contract: caller-enabled eofbit/failbit/badbit exception
// masks are supported without changing them or clearing the stream state.
// A clean EOF can terminate the final unterminated row. badbit, non-EOF failbit
// and backing-source exceptions are errors, never salvaged final rows. Cursor
// error/poisoning rules below remain in force across every CSV input profile.
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

// Explicit historical positional CSV profiles from the retained HeptaDLL data
// helpers. This is an OFFLINE schema adapter, not a vendor SDK, depth book or
// an exchange event-identity decoder. It publishes the SAME incremental Tick.
enum class LegacyTickCsvLayout { Hepta32, Immsg34, Immsg35, Zs58 };
struct LegacyTickClock {
    std::string actionDay; // Actual Gregorian civil date, never inferred from TradingDay.
    int utcOffsetMinutes = 0; // Explicit local-minus-UTC offset for this exact row.
};
// Arguments: one-based DATA row, instrument, TradingDay, source ActionDay (empty
// except in Immsg35). Must supply independently verified civil date/offset. The
// source ActionDay, when present, cannot be overridden. No host timezone/DST,
// holiday lookup or trading-day-to-civil-day inference occurs in this library.
using LegacyTickClockResolver = std::function<LegacyTickClock(
    std::size_t, const std::string&, const std::string&, const std::string&)>;
struct LegacyTickRecord {
    Tick tick;
    std::string tradingDay, actionDay;
    int utcOffsetMinutes = 0;
    std::int64_t cumulativeVolume = 0;
    double turnover = 0, openInterest = 0;
    // Retained, bounded, unqualified source columns. They remain available even
    // when callers do not opt into a typed adapter below.
    std::vector<std::string> sourceFields;
};
// Explicit OFFLINE promotion of the reviewed Bid1/Ask1 columns from one legacy
// record. These are observed source fields, not a reconstructed queue, trade tape
// or execution quote. Invalid/empty/crossed legacy depth fails when this adapter
// is requested; ordinary LegacyTickCsvReader use retains its previous behavior.
struct LegacyTopOfBookObservation {
    std::string instrument;
    std::int64_t timestampUs = 0;
    std::uint64_t sequence = 0;
    double bestBidPrice = 0, bestAskPrice = 0;
    std::int64_t bestBidVolume = 0, bestAskVolume = 0;
};
LegacyTopOfBookObservation DecodeLegacyTopOfBook(
    const LegacyTickRecord& record, LegacyTickCsvLayout layout);
// One serialized, possibly mixed-instrument stream. Callers explicitly supply
// 1..64 instrument/session bindings, layout, clock resolver and first-volume
// policy. Headerless by default; hasHeader validates selected positional names.
// Global UTC time and each instrument's trading day/counter must not reverse.
// Source row numbers become sequence IDs, NOT exchange identities: repeated
// rows are preserved; equal cumulative counters contribute zero extra volume.
// No history-sized storage: one decoder per allowed instrument, a bounded row,
// and owned schedule/resolver values. Input and callback captures must outlive
// their use. Thread-affine; no concurrent input mutation or resolver re-entry.
// EOF/error leaves output/count unchanged; ANY Next failure permanently poisons
// this reader. A later failure invalidates a run's previously emitted prefix.
// Resolver side effects cannot be rolled back. No automatic retry/skip/reorder.
class LegacyTickCsvReader {
public:
    LegacyTickCsvReader(std::istream& input, LegacyTickCsvLayout layout,
        std::map<std::string, SessionSchedule> schedules,
        LegacyTickClockResolver clockResolver, bool countFirstObservation,
        bool hasHeader = false, std::size_t maxRows = 1000000);
    ~LegacyTickCsvReader();
    LegacyTickCsvReader(const LegacyTickCsvReader&) = delete;
    LegacyTickCsvReader& operator=(const LegacyTickCsvReader&) = delete;
    bool Next(LegacyTickRecord& output);
    std::size_t RowsRead() const;
private:
    struct Impl;
    std::unique_ptr<Impl> impl_;
};

// Historical completed-bar inputs. Defaults: Futures11/Futures13 are start-
// labelled one-minute rows; Stock7 is an end-labelled three-minute row. The
// explicit-period overload changes only duration, not the label convention,
// numeric epoch, columns or evidence requirements. The caller must
// select the profile; column counts never select it implicitly. Futures numeric
// timestamps use the reviewed Hepta civil FILETIME-microsecond epoch (1601),
// NOT Unix time. Zero StartTime means use the required civil text; a nonzero
// value must agree with that text to the displayed second. No host TZ is used.
enum class LegacyBarCsvLayout { Futures11, Futures13, Stock7 };
struct LegacyBarEvidence {
    int utcOffsetMinutes = 0; // Local-minus-UTC offset for this exact source row.
    std::uint64_t tickCount = 0; // Not recorded in these files: independently supplied.
    std::int64_t observedAtUs = 0; // Actual research availability, at/after bar end.
    bool complete = false; // Explicit caller evidence; EOF does not complete a bar.
};
// Arguments: one-based data row, bound instrument, civil date from source text.
// No default tick count, availability, completion or exchange calendar is invented.
using LegacyBarEvidenceResolver = std::function<LegacyBarEvidence(
    std::size_t, const std::string&, const std::string&)>;
struct LegacyBarRecord {
    Bar bar;
    std::string sourceCivilDay;
    std::int64_t observedAtUs = 0;
    int utcOffsetMinutes = 0;
    double turnover = 0; // Per-bar turnover, not cumulative total.
    bool hasCumulativeTotals = false, hasOpenInterest = false;
    std::int64_t cumulativeVolume = 0;
    double cumulativeTurnover = 0, openInterest = 0;
    bool hasHighTime = false, hasLowTime = false;
    std::int64_t highTimeUs = 0, lowTimeUs = 0;
    std::vector<std::string> sourceFields; // Bounded, retained source columns.
};
// OFFLINE, single bound instrument. Futures LastVolume/LastTurnOver are the bar
// amounts; Volume/TurnOver remain cumulative metadata. Stock7 amounts are per
// bar. Missing OHLC is rejected, never copied from a preceding row. Each bar must
// lie in ONE supplied session; its trading-day label comes from that session.
// Evidence controls completion/count/availability; delivery time, bar intervals,
// trading day and same-day futures totals cannot reverse. Gaps are allowed.
// expectedHeader empty means headerless; otherwise the caller supplies the exact
// header line for the selected positional profile (no automatic header guessing).
// Construction validates configuration before consuming that optional header.
// One bounded row plus one prior record: no seek/history-sized state. The input
// and resolver captures must outlive their use; serialize calls, never re-enter
// from the resolver. EOF/error preserves output and RowsRead; ANY Next error
// permanently fails the cursor. Callback side effects cannot be rolled back.
class LegacyBarCsvReader {
public:
    LegacyBarCsvReader(std::istream& input, LegacyBarCsvLayout layout,
        std::string instrument, SessionSchedule schedule,
        LegacyBarEvidenceResolver evidenceResolver,
        std::string expectedHeader = "", std::size_t maxRows = 1000000);
    // Explicit positive duration in UTC microseconds, fixed for this cursor.
    // No duration inference from filenames, adjacent rows, volume or EOF.
    // This interprets already formed bars; it does NOT aggregate/resample them.
    // Start/end labels retain the selected profile's convention. Each interval
    // must still fit one supplied session and satisfy independently supplied
    // completion/availability evidence. Zero/negative durations are rejected
    // before consuming input; checked interval arithmetic rejects overflow.
    // The original overload and its default-duration ABI remain available.
    LegacyBarCsvReader(std::istream& input, LegacyBarCsvLayout layout,
        std::string instrument, SessionSchedule schedule,
        LegacyBarEvidenceResolver evidenceResolver, std::int64_t periodUs,
        std::string expectedHeader = "", std::size_t maxRows = 1000000);
    ~LegacyBarCsvReader();
    LegacyBarCsvReader(const LegacyBarCsvReader&) = delete;
    LegacyBarCsvReader& operator=(const LegacyBarCsvReader&) = delete;
    bool Next(LegacyBarRecord& output);
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
