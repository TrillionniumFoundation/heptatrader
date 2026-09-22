#pragma once
// Refactored research boundary informed by HeptaDLL's candle/series interfaces.
// Original interfaces: Copyright (c) Wu Chang Sheng. All rights reserved.
// Consult your license regarding permissions and restrictions.
// No broker SDK types, credentials, mutable trading state, or background threads.
#include <cstddef>
#include <cstdint>
#include <deque>
#include <istream>
#include <string>
#include <vector>

namespace hepta { namespace research {

struct Tick {
    std::string instrument;
    int tradingDay = 0; // YYYYMMDD supplied by the dataset, not inferred from UTC.
    std::int64_t timestampUs = 0;
    double price = 0;
    std::uint64_t cumulativeVolume = 0;
};

struct Session {
    int tradingDay = 0;
    std::int64_t beginUs = 0;
    std::int64_t endUs = 0; // half-open [begin,end); absolute, timezone-resolved input.
};

class SessionCalendar {
public:
    explicit SessionCalendar(std::vector<Session> sessions);
    const Session& At(std::int64_t timestampUs, int tradingDay) const;
    Session Day(int tradingDay) const;
private:
    std::vector<Session> sessions_;
};

// Field-wise replacement for the legacy candle value. No binary-layout promise.
struct Bar {
    std::string instrument;
    int tradingDay = 0;
    std::int64_t beginUs = 0, endUs = 0, highTimeUs = 0, lowTimeUs = 0;
    double open = 0, high = 0, low = 0, close = 0;
    std::uint64_t volume = 0;
    bool complete = false;
};

enum class TickResult { Applied, Duplicate };
enum class FirstVolume { BaselineOnly, FromTradingDayStart };

// Single instrument and caller-owned. periodUs==0 means a session-calendar day.
// Validation failures leave the stream unchanged; exact consecutive duplicates
// are ignored. No gap filling and no implicit holiday/trading-hours table.
class BarSeries {
public:
    BarSeries(std::string instrument, SessionCalendar calendar,
              std::int64_t periodUs, std::size_t capacity = 4096,
              FirstVolume firstVolume = FirstVolume::BaselineOnly);
    TickResult Push(const Tick& tick);
    void Finish(std::int64_t watermarkUs); // irreversible; incomplete tail marked
    const std::deque<Bar>& Closed() const { return closed_; }
    bool Current(Bar& out) const;
private:
    std::string instrument_;
    SessionCalendar calendar_;
    std::int64_t periodUs_;
    std::size_t capacity_;
    FirstVolume firstVolume_;
    Tick last_;
    Bar current_;
    bool seen_ = false, finished_ = false;
    std::deque<Bar> closed_;
    void AppendClosed(const Bar& bar);
};

// Normalized, deliberately NOT a raw legacy binary/struct-memory reader.
// Exact header: instrument,trading_day,timestamp_us,price,cumulative_volume
// Classic locale, bounded 4096-byte rows; malformed input throws with row number.
class TickCsvReader {
public:
    explicit TickCsvReader(std::istream& input);
    bool Next(Tick& tick);
private:
    std::istream& input_;
    std::size_t row_ = 0;
    bool Line(std::string& line);
};

// Old reverse-index convention: 0 is the newest bar, endpoints are inclusive.
// Equal extrema select newest unless oldestTie is explicitly requested.
std::size_t ExtremeIndex(const std::deque<Bar>& bars, std::size_t begin,
                         std::size_t end, bool highest, bool oldestTie = false);
double MeanClose(const std::deque<Bar>& bars, std::size_t begin, std::size_t end);
void ValidateTick(const Tick& tick);
void ValidateBar(const Bar& bar);

}} // namespace hepta::research
