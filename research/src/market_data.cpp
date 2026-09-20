#include "hepta/research/market_data.h"

#include <algorithm>
#include <cmath>
#include <iomanip>
#include <istream>
#include <limits>
#include <locale>
#include <ostream>
#include <sstream>
#include <stdexcept>
#include <utility>

namespace hepta { namespace research {
namespace {
void Require(bool condition, const char* reason) {
    if (!condition) throw std::invalid_argument(reason);
}
bool InstrumentValid(const std::string& s) {
    if (s.empty() || s.size() > 64) return false;
    for (unsigned char c : s) {
        if (!((c >= 'A' && c <= 'Z') || (c >= 'a' && c <= 'z') ||
              (c >= '0' && c <= '9') || c == '.' || c == '_' ||
              c == '-' || c == ':' || c == '/')) return false;
    }
    return true;
}
bool SameTick(const Tick& a, const Tick& b) {
    return a.instrument == b.instrument && a.timestampUs == b.timestampUs &&
           a.sequence == b.sequence && a.price == b.price && a.volume == b.volume;
}
std::int64_t AddVolume(std::int64_t a, std::int64_t b) {
    if (b > std::numeric_limits<std::int64_t>::max() - a)
        throw std::overflow_error("RESEARCH_VOLUME_OVERFLOW");
    return a + b;
}
std::uint64_t AddCount(std::uint64_t a, std::uint64_t b) {
    if (b > std::numeric_limits<std::uint64_t>::max() - a)
        throw std::overflow_error("RESEARCH_COUNT_OVERFLOW");
    return a + b;
}
std::uint64_t Unsigned(const std::string& s) {
    Require(!s.empty(), "RESEARCH_CSV_INTEGER_EMPTY");
    std::uint64_t value = 0;
    for (unsigned char c : s) {
        Require(c >= '0' && c <= '9', "RESEARCH_CSV_INTEGER_INVALID");
        const unsigned digit = c - '0';
        Require(value <= (std::numeric_limits<std::uint64_t>::max() - digit) / 10,
                "RESEARCH_CSV_INTEGER_OVERFLOW");
        value = value * 10 + digit;
    }
    return value;
}
std::int64_t SignedNonnegative(const std::string& s) {
    const auto value = Unsigned(s);
    Require(value <= static_cast<std::uint64_t>(std::numeric_limits<std::int64_t>::max()),
            "RESEARCH_CSV_INTEGER_OVERFLOW");
    return static_cast<std::int64_t>(value);
}
bool ReadBoundedLine(std::istream& input, std::string& line) {
    line.clear();
    char c;
    while (input.get(c)) {
        if (c == '\n') return true;
        Require(line.size() < 4096, "RESEARCH_CSV_LINE_TOO_LONG");
        line.push_back(c);
    }
    if (input.bad() || (input.fail() && !input.eof()))
        throw std::runtime_error("RESEARCH_CSV_READ_FAILED");
    return !line.empty();
}
void StripCR(std::string& line) {
    if (!line.empty() && line.back() == '\r') line.pop_back();
}
}

void ValidateTradingDay(const std::string& day) {
    Require(day.size() == 8, "RESEARCH_TRADING_DAY_INVALID");
    for (char c : day) Require(c >= '0' && c <= '9', "RESEARCH_TRADING_DAY_INVALID");
    const int year = std::stoi(day.substr(0, 4));
    const int month = std::stoi(day.substr(4, 2));
    const int date = std::stoi(day.substr(6, 2));
    Require(year >= 1 && month >= 1 && month <= 12, "RESEARCH_TRADING_DAY_INVALID");
    const int days[] = {31,28,31,30,31,30,31,31,30,31,30,31};
    const bool leap = year % 4 == 0 && (year % 100 != 0 || year % 400 == 0);
    Require(date >= 1 && date <= days[month - 1] + (month == 2 && leap),
            "RESEARCH_TRADING_DAY_INVALID");
}
void ValidateTick(const Tick& tick) {
    Require(InstrumentValid(tick.instrument), "RESEARCH_INSTRUMENT_INVALID");
    Require(tick.timestampUs >= 0 && tick.sequence > 0, "RESEARCH_TICK_ID_INVALID");
    Require(std::isfinite(tick.price) && tick.price > 0, "RESEARCH_PRICE_INVALID");
    Require(tick.volume >= 0, "RESEARCH_VOLUME_INVALID");
}
void ValidateBar(const Bar& bar) {
    Require(InstrumentValid(bar.instrument), "RESEARCH_INSTRUMENT_INVALID");
    ValidateTradingDay(bar.tradingDay);
    Require(bar.beginUs >= 0 && bar.endUs > bar.beginUs, "RESEARCH_BAR_INTERVAL_INVALID");
    Require(std::isfinite(bar.open) && std::isfinite(bar.high) &&
            std::isfinite(bar.low) && std::isfinite(bar.close) && bar.low > 0 &&
            bar.low <= bar.open && bar.low <= bar.close &&
            bar.high >= bar.open && bar.high >= bar.close,
            "RESEARCH_BAR_OHLC_INVALID");
    Require(bar.volume >= 0 && bar.tickCount > 0, "RESEARCH_BAR_COUNT_INVALID");
}
SessionSchedule::SessionSchedule(std::vector<SessionWindow> windows)
    : windows_(std::move(windows)) {
    Require(!windows_.empty(), "RESEARCH_SESSIONS_EMPTY");
    for (std::size_t i = 0; i < windows_.size(); ++i) {
        const auto& w = windows_[i];
        ValidateTradingDay(w.tradingDay);
        Require(w.openUs >= 0 && w.closeUs > w.openUs, "RESEARCH_SESSION_INTERVAL_INVALID");
        if (i) {
            Require(w.openUs >= windows_[i - 1].closeUs, "RESEARCH_SESSIONS_OVERLAP_OR_UNSORTED");
            Require(w.tradingDay >= windows_[i - 1].tradingDay, "RESEARCH_TRADING_DAY_REVERSED");
        }
    }
}
const SessionWindow& SessionSchedule::At(std::int64_t time) const {
    auto it = std::upper_bound(windows_.begin(), windows_.end(), time,
        [](std::int64_t t, const SessionWindow& w) { return t < w.openUs; });
    Require(it != windows_.begin(), "RESEARCH_OUTSIDE_SESSION");
    --it;
    Require(time < it->closeUs, "RESEARCH_OUTSIDE_SESSION");
    return *it;
}
SessionWindow SessionSchedule::Day(const std::string& day) const {
    ValidateTradingDay(day);
    auto first = std::lower_bound(windows_.begin(), windows_.end(), day,
        [](const SessionWindow& w, const std::string& d) { return w.tradingDay < d; });
    Require(first != windows_.end() && first->tradingDay == day, "RESEARCH_TRADING_DAY_UNKNOWN");
    auto last = first;
    while (last + 1 != windows_.end() && (last + 1)->tradingDay == day) ++last;
    SessionWindow out = *first;
    out.closeUs = last->closeUs;
    return out;
}
BarBuilder::BarBuilder(std::string instrument, std::int64_t periodUs, SessionSchedule schedule)
    : instrument_(std::move(instrument)), periodUs_(periodUs), schedule_(std::move(schedule)) {
    Require(InstrumentValid(instrument_), "RESEARCH_INSTRUMENT_INVALID");
    Require(periodUs >= 0, "RESEARCH_PERIOD_INVALID");
}
TickOutcome BarBuilder::Push(const Tick& tick, Bar& closed) {
    ValidateTick(tick);
    Require(tick.instrument == instrument_, "RESEARCH_INSTRUMENT_MISMATCH");
    if (hasLast_ && tick.sequence == last_.sequence) {
        Require(SameTick(tick, last_), "RESEARCH_SEQUENCE_CONFLICT");
        return TickOutcome::Duplicate;
    }
    Require(tick.timestampUs >= watermarkUs_, "RESEARCH_TICK_BEFORE_WATERMARK");
    if (hasLast_) Require(tick.sequence > last_.sequence && tick.timestampUs >= last_.timestampUs,
                          "RESEARCH_TICK_OUT_OF_ORDER");
    const SessionWindow& session = schedule_.At(tick.timestampUs);
    SessionWindow bucket = periodUs_ == 0 ? schedule_.Day(session.tradingDay) : session;
    if (periodUs_ > 0) {
        const auto offset = tick.timestampUs - session.openUs;
        bucket.openUs = session.openUs + (offset / periodUs_) * periodUs_;
        // Subtraction-first bound avoids signed addition overflow.
        bucket.closeUs = bucket.openUs + std::min(periodUs_, session.closeUs - bucket.openUs);
    }
    const bool same = hasBar_ && bar_.beginUs == bucket.openUs && bar_.endUs == bucket.closeUs;
    Bar next = same ? bar_ : Bar();
    if (!same) {
        next.instrument = instrument_;
        next.tradingDay = session.tradingDay;
        next.beginUs = bucket.openUs; next.endUs = bucket.closeUs;
        next.open = next.high = next.low = next.close = tick.price;
    }
    next.high = std::max(next.high, tick.price);
    next.low = std::min(next.low, tick.price);
    next.close = tick.price;
    next.volume = AddVolume(next.volume, tick.volume);
    next.tickCount = AddCount(next.tickCount, 1);
    const bool didClose = hasBar_ && !same;
    if (didClose) { closed = bar_; closed.complete = true; }
    bar_ = std::move(next); hasBar_ = true; last_ = tick; hasLast_ = true;
    return didClose ? TickOutcome::ClosedPrevious : TickOutcome::Updated;
}
bool BarBuilder::AdvanceWatermark(std::int64_t watermark, Bar& closed) {
    Require(watermark >= watermarkUs_ && (!hasLast_ || watermark >= last_.timestampUs),
            "RESEARCH_WATERMARK_REVERSED");
    watermarkUs_ = watermark;
    if (!hasBar_ || bar_.endUs > watermark) return false;
    closed = bar_; closed.complete = true; hasBar_ = false;
    return true;
}
bool BarBuilder::Current(Bar& out) const {
    if (!hasBar_) return false;
    out = bar_;
    return true;
}
CumulativeVolumeDecoder::CumulativeVolumeDecoder(std::string instrument, bool countFirst)
    : instrument_(std::move(instrument)), countFirstObservation_(countFirst) {
    Require(InstrumentValid(instrument_), "RESEARCH_INSTRUMENT_INVALID");
}
Tick CumulativeVolumeDecoder::Decode(const Tick& tick, const std::string& day) {
    ValidateTick(tick); ValidateTradingDay(day);
    Require(tick.instrument == instrument_, "RESEARCH_INSTRUMENT_MISMATCH");
    if (initialized_ && tick.sequence == lastInput_.sequence) {
        Require(day == day_ && SameTick(tick, lastInput_), "RESEARCH_SEQUENCE_CONFLICT");
        return lastOutput_;
    }
    if (initialized_) {
        Require(tick.sequence > lastInput_.sequence && tick.timestampUs >= lastInput_.timestampUs &&
                day >= day_, "RESEARCH_CUMULATIVE_OUT_OF_ORDER");
        if (day == day_) Require(tick.volume >= lastInput_.volume, "RESEARCH_CUMULATIVE_DECREASE");
    }
    Tick result = tick;
    result.volume = initialized_ && day == day_ ? tick.volume - lastInput_.volume : (countFirstObservation_ ? tick.volume : 0);
    lastInput_ = tick; lastOutput_ = result; day_ = day; initialized_ = true;
    return result;
}
std::vector<Tick> ReadTicksCsv(std::istream& input, std::size_t maxRows) {
    Require(maxRows > 0, "RESEARCH_CSV_ROW_LIMIT_INVALID");
    std::string line;
    Require(ReadBoundedLine(input, line), "RESEARCH_CSV_HEADER_MISSING");
    StripCR(line);
    Require(line == "instrument,timestamp_us,sequence,price,volume", "RESEARCH_CSV_HEADER_INVALID");
    std::vector<Tick> ticks;
    while (ReadBoundedLine(input, line)) {
        StripCR(line);
        Require(ticks.size() < maxRows, "RESEARCH_CSV_ROW_LIMIT");
        std::vector<std::string> cells;
        std::size_t begin = 0;
        for (;;) {
            const auto end = line.find(',', begin);
            cells.push_back(line.substr(begin, end == std::string::npos ? end : end - begin));
            if (end == std::string::npos) break;
            begin = end + 1;
        }
        Require(cells.size() == 5, "RESEARCH_CSV_FIELD_COUNT");
        Tick tick;
        tick.instrument = cells[0]; tick.timestampUs = SignedNonnegative(cells[1]);
        tick.sequence = Unsigned(cells[2]); tick.volume = SignedNonnegative(cells[4]);
        std::istringstream number(cells[3]); number.imbue(std::locale::classic());
        number >> std::noskipws >> tick.price;
        Require(!number.fail() && number.peek() == std::char_traits<char>::eof(), "RESEARCH_CSV_PRICE_INVALID");
        ValidateTick(tick); ticks.push_back(tick);
    }
    return ticks;
}
void WriteTicksCsv(std::ostream& output, const std::vector<Tick>& ticks) {
    for (const auto& tick : ticks) ValidateTick(tick);
    std::ostringstream encoded; encoded.imbue(std::locale::classic());
    encoded << "instrument,timestamp_us,sequence,price,volume\n" << std::setprecision(17);
    for (const auto& tick : ticks)
        encoded << tick.instrument << ',' << tick.timestampUs << ',' << tick.sequence << ','
                << tick.price << ',' << tick.volume << '\n';
    output << encoded.str();
    if (!output) throw std::runtime_error("RESEARCH_CSV_WRITE_FAILED");
}
SessionSchedule ReadSessionsCsv(std::istream& input, std::size_t maxRows) {
    Require(maxRows > 0, "RESEARCH_CSV_ROW_LIMIT_INVALID");
    std::string line;
    Require(ReadBoundedLine(input, line), "RESEARCH_CSV_HEADER_MISSING");
    StripCR(line);
    Require(line == "open_us,close_us,trading_day", "RESEARCH_SESSION_CSV_HEADER_INVALID");
    std::vector<SessionWindow> windows;
    while (ReadBoundedLine(input, line)) {
        StripCR(line);
        Require(windows.size() < maxRows, "RESEARCH_CSV_ROW_LIMIT");
        const auto first = line.find(',');
        const auto second = first == std::string::npos ? first : line.find(',', first + 1);
        Require(first != std::string::npos && second != std::string::npos &&
                line.find(',', second + 1) == std::string::npos, "RESEARCH_CSV_FIELD_COUNT");
        SessionWindow w;
        w.openUs = SignedNonnegative(line.substr(0, first));
        w.closeUs = SignedNonnegative(line.substr(first + 1, second - first - 1));
        w.tradingDay = line.substr(second + 1); windows.push_back(w);
    }
    return SessionSchedule(std::move(windows));
}
BarSeries::BarSeries(std::size_t capacity) : capacity_(capacity) {
    Require(capacity > 0, "RESEARCH_SERIES_CAPACITY_INVALID");
}
void BarSeries::Append(const Bar& bar) {
    ValidateBar(bar); Require(bar.complete, "RESEARCH_PARTIAL_BAR");
    if (!bars_.empty()) {
        Require(bar.instrument == bars_.back().instrument, "RESEARCH_INSTRUMENT_MISMATCH");
        Require(bar.beginUs >= bars_.back().endUs && bar.tradingDay >= bars_.back().tradingDay,
                "RESEARCH_BAR_OUT_OF_ORDER");
    }
    bars_.push_back(bar);
    if (bars_.size() > capacity_) bars_.pop_front();
}
void BarSeries::Replace(const Bar& bar) {
    ValidateBar(bar); Require(bar.complete, "RESEARCH_PARTIAL_BAR");
    for (auto& old : bars_) if (old.beginUs == bar.beginUs) {
        Require(old.instrument == bar.instrument && old.tradingDay == bar.tradingDay && old.endUs == bar.endUs,
                "RESEARCH_REPLACEMENT_ID_CONFLICT");
        old = bar; return;
    }
    throw std::invalid_argument("RESEARCH_REPLACEMENT_NOT_FOUND");
}
void BarSeries::EraseBefore(std::int64_t begin) {
    while (!bars_.empty() && bars_.front().beginUs < begin) bars_.pop_front();
}
void BarSeries::EraseAfter(std::int64_t begin) {
    while (!bars_.empty() && bars_.back().beginUs > begin) bars_.pop_back();
}
const Bar& BarSeries::At(std::size_t index) const { return bars_.at(index); }
std::size_t BarSeries::Highest(std::size_t begin, std::size_t end, bool latest) const {
    Require(begin <= end && end < bars_.size(), "RESEARCH_SERIES_RANGE_INVALID");
    auto best = begin;
    for (auto i = begin + 1; i <= end; ++i)
        if (bars_[i].high > bars_[best].high || (latest && bars_[i].high == bars_[best].high)) best = i;
    return best;
}
std::size_t BarSeries::Lowest(std::size_t begin, std::size_t end, bool latest) const {
    Require(begin <= end && end < bars_.size(), "RESEARCH_SERIES_RANGE_INVALID");
    auto best = begin;
    for (auto i = begin + 1; i <= end; ++i)
        if (bars_[i].low < bars_[best].low || (latest && bars_[i].low == bars_[best].low)) best = i;
    return best;
}
double BarSeries::MeanClose(std::size_t count) const {
    Require(count > 0 && count <= bars_.size(), "RESEARCH_SERIES_RANGE_INVALID");
    long double average = 0;
    for (std::size_t i = bars_.size() - count; i < bars_.size(); ++i)
        average += static_cast<long double>(bars_[i].close) / count;
    Require(std::isfinite(average) && average <= std::numeric_limits<double>::max(), "RESEARCH_MEAN_OVERFLOW");
    return static_cast<double>(average);
}
Bar MergeBars(const std::vector<Bar>& bars) {
    Require(!bars.empty(), "RESEARCH_BARS_EMPTY");
    Bar result = bars.front(); ValidateBar(result);
    Require(result.complete, "RESEARCH_PARTIAL_BAR");
    for (std::size_t i = 1; i < bars.size(); ++i) {
        const auto& b = bars[i]; ValidateBar(b); Require(b.complete, "RESEARCH_PARTIAL_BAR");
        Require(b.instrument == result.instrument && b.tradingDay == result.tradingDay && b.beginUs >= result.endUs,
                "RESEARCH_BAR_MERGE_ID_OR_ORDER");
        result.endUs = b.endUs; result.high = std::max(result.high, b.high);
        result.low = std::min(result.low, b.low); result.close = b.close;
        result.volume = AddVolume(result.volume, b.volume);
        result.tickCount = AddCount(result.tickCount, b.tickCount);
    }
    return result;
}
}} // namespace hepta::research
