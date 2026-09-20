#pragma once

#include <cstdint>
#include <string>
#include <vector>

namespace hepta { namespace research {

// Calendar arithmetic only. This is NOT an exchange holiday calendar.
struct CivilDay {
    int year, month, day;
    CivilDay(int y, int m, int d);
    static CivilDay Parse(const std::string& yyyymmdd);
    static CivilDay FromSerial(int daysSince1970);
    int Serial() const;
    int Weekday() const; // Sunday = 0
    std::string String() const;
};

// Explicit UTC microsecond windows supplied by the data owner; [begin,end).
// Night sessions may share the NEXT trading day with subsequent day sessions.
struct Session {
    std::int64_t beginUs, endUs;
    std::string tradingDay;
};
struct Tick {
    std::string instrument, tradingDay;
    std::int64_t timestampUs, priceTicks;
    std::uint64_t sequence, cumulativeVolume;
};
struct Bar {
    std::string instrument, tradingDay;
    std::int64_t beginUs = 0, endUs = 0;
    std::int64_t open = 0, high = 0, low = 0, close = 0;
    std::uint64_t volume = 0, ticks = 0;
    bool complete = false;
};
enum class FirstVolume { BaselineOnly, IncludeCumulative };
enum class PushResult { Buffered, ClosedBar, Duplicate };

// Single-writer, single-instrument stream. Bounded state; no broker/OS state.
// periodUs == 0 means trading-day bars spanning explicit session breaks.
class BarBuilder {
public:
    BarBuilder(std::string instrument, std::vector<Session> sessions,
               std::int64_t periodUs, FirstVolume firstVolume);
    // A rejected tick leaves the builder and output unchanged.
    PushResult Push(const Tick& tick, Bar& closed);
    // Caller promise: no new tick earlier than timestampUs may arrive. It
    // closes a populated elapsed bar without inventing a tick or an empty bar.
    // The last exact tick retry stays idempotent. Rejects clock regression and
    // calls after Finish; rejection/no output leave closed unchanged.
    bool AdvanceWatermark(std::int64_t timestampUs, Bar& closed);
    // EOF is NOT evidence of an elapsed interval: the remaining bar is incomplete.
    bool Finish(Bar& last);
private:
    std::string instrument_;
    std::vector<Session> sessions_;
    std::int64_t period_;
    FirstVolume firstVolume_;
    bool hasTick_ = false, hasBar_ = false, finished_ = false;
    std::int64_t watermarkUs_ = -1;
    Tick lastTick_;
    Bar current_;
};

// Inclusive indices, like the legacy K-line query API. Ties select the
// latest occurrence unless earliestTie is explicitly requested.
std::size_t Highest(const std::vector<Bar>& bars, std::size_t first,
                    std::size_t last, bool earliestTie = false);
std::size_t Lowest(const std::vector<Bar>& bars, std::size_t first,
                   std::size_t last, bool earliestTie = false);

}} // namespace hepta::research
