#include "market.h"
#include <algorithm>
#include <cmath>
#include <limits>
#include <locale>
#include <sstream>
#include <stdexcept>
#include <utility>

namespace hepta { namespace research {
namespace {
bool ValidDay(int day) {
    const int y = day / 10000, m = day / 100 % 100, d = day % 100;
    const int lengths[] = {31,28,31,30,31,30,31,31,30,31,30,31};
    if (y < 1900 || y > 9999 || m < 1 || m > 12) return false;
    const bool leap = y % 4 == 0 && (y % 100 != 0 || y % 400 == 0);
    return d > 0 && d <= lengths[m - 1] + (m == 2 && leap ? 1 : 0);
}
bool Identifier(const std::string& s) {
    if (s.empty() || s.size() > 128) return false;
    for (unsigned char c : s)
        if (!((c >= 'a' && c <= 'z') || (c >= 'A' && c <= 'Z') ||
              (c >= '0' && c <= '9') || c == '.' || c == '_' || c == '-' || c == ':')) return false;
    return true;
}
bool Equal(const Tick& a, const Tick& b) {
    return a.instrument == b.instrument && a.tradingDay == b.tradingDay &&
        a.timestampUs == b.timestampUs && a.price == b.price &&
        a.cumulativeVolume == b.cumulativeVolume;
}
std::uint64_t Unsigned(const std::string& s) {
    if (s.empty()) throw std::invalid_argument("empty integer");
    std::uint64_t v = 0;
    for (char c : s) {
        if (c < '0' || c > '9' || v > (std::numeric_limits<std::uint64_t>::max() - (c-'0')) / 10)
            throw std::invalid_argument("invalid or overflowing integer");
        v = v * 10 + (c - '0');
    }
    return v;
}
void Range(const std::deque<Bar>& bars, std::size_t begin, std::size_t end) {
    if (begin > end || end >= bars.size()) throw std::invalid_argument("invalid inclusive bar range");
    for (std::size_t i = begin; i <= end; ++i) ValidateBar(bars[bars.size()-1-i]);
}
}

void ValidateTick(const Tick& t) {
    if (!Identifier(t.instrument) || !ValidDay(t.tradingDay) || t.timestampUs <= 0 ||
        !std::isfinite(t.price) || t.price <= 0)
        throw std::invalid_argument("invalid normalized tick");
}
void ValidateBar(const Bar& b) {
    if (!Identifier(b.instrument) || !ValidDay(b.tradingDay) || b.beginUs <= 0 ||
        b.endUs <= b.beginUs || !std::isfinite(b.open) || !std::isfinite(b.high) ||
        !std::isfinite(b.low) || !std::isfinite(b.close) || b.low <= 0 ||
        b.high < b.low || b.open < b.low || b.open > b.high ||
        b.close < b.low || b.close > b.high)
        throw std::invalid_argument("invalid normalized bar");
}
SessionCalendar::SessionCalendar(std::vector<Session> sessions) : sessions_(std::move(sessions)) {
    if (sessions_.empty()) throw std::invalid_argument("explicit sessions required");
    for (std::size_t i = 0; i < sessions_.size(); ++i) {
        const Session& s = sessions_[i];
        if (!ValidDay(s.tradingDay) || s.beginUs <= 0 || s.endUs <= s.beginUs ||
            (i && (s.beginUs < sessions_[i-1].endUs || s.tradingDay < sessions_[i-1].tradingDay)))
            throw std::invalid_argument("invalid, overlapping, unsorted or backward-day sessions");
    }
}
const Session& SessionCalendar::At(std::int64_t timestamp, int day) const {
    auto it = std::upper_bound(sessions_.begin(), sessions_.end(), timestamp,
        [](std::int64_t t, const Session& s) { return t < s.beginUs; });
    if (it == sessions_.begin()) throw std::invalid_argument("tick outside sessions");
    --it;
    if (timestamp >= it->endUs || it->tradingDay != day)
        throw std::invalid_argument("tick outside session or trading-day mismatch");
    return *it;
}
Session SessionCalendar::Day(int day) const {
    Session out;
    for (const Session& s : sessions_) if (s.tradingDay == day) {
        if (!out.beginUs) { out.tradingDay = day; out.beginUs = s.beginUs; }
        out.endUs = s.endUs;
    }
    if (!out.beginUs) throw std::invalid_argument("unknown trading day");
    return out;
}
BarSeries::BarSeries(std::string instrument, SessionCalendar calendar,
    std::int64_t period, std::size_t capacity, FirstVolume volume)
    : instrument_(std::move(instrument)), calendar_(std::move(calendar)), periodUs_(period),
      capacity_(capacity), firstVolume_(volume) {
    if (!Identifier(instrument_) || period < 0 || !capacity || capacity > 1000000 ||
        (volume != FirstVolume::BaselineOnly && volume != FirstVolume::FromTradingDayStart))
        throw std::invalid_argument("invalid bar-series configuration");
}
void BarSeries::AppendClosed(const Bar& b) {
    closed_.push_back(b);
    if (closed_.size() > capacity_) closed_.pop_front();
}
TickResult BarSeries::Push(const Tick& t) {
    if (finished_) throw std::logic_error("bar stream already finished");
    ValidateTick(t);
    if (t.instrument != instrument_) throw std::invalid_argument("mixed-instrument stream");
    const Session& session = calendar_.At(t.timestampUs, t.tradingDay);
    if (seen_ && Equal(last_, t)) return TickResult::Duplicate;
    if (seen_ && (t.timestampUs < last_.timestampUs || t.tradingDay < last_.tradingDay ||
        (t.tradingDay == last_.tradingDay && t.cumulativeVolume < last_.cumulativeVolume)))
        throw std::invalid_argument("out-of-order tick or intraday volume reset");
    const Session bounds = periodUs_ == 0 ? calendar_.Day(t.tradingDay) : session;
    const std::int64_t begin = periodUs_ == 0 ? bounds.beginUs :
        bounds.beginUs + ((t.timestampUs - bounds.beginUs) / periodUs_) * periodUs_;
    const std::int64_t end = periodUs_ == 0 ? bounds.endUs :
        begin + std::min(periodUs_, bounds.endUs - begin); // no signed overflow
    const std::uint64_t delta = seen_ && t.tradingDay == last_.tradingDay ?
        t.cumulativeVolume - last_.cumulativeVolume :
        (firstVolume_ == FirstVolume::FromTradingDayStart ? t.cumulativeVolume : 0);
    const bool next = !seen_ || begin != current_.beginUs || t.tradingDay != current_.tradingDay;
    Bar candidate = current_;
    if (next) {
        candidate = Bar(); candidate.instrument = instrument_; candidate.tradingDay = t.tradingDay;
        candidate.beginUs = begin; candidate.endUs = end;
        candidate.highTimeUs = candidate.lowTimeUs = t.timestampUs;
        candidate.open = candidate.high = candidate.low = candidate.close = t.price;
    }
    if (candidate.volume > std::numeric_limits<std::uint64_t>::max() - delta)
        throw std::overflow_error("bar volume overflow");
    candidate.volume += delta;
    if (t.price > candidate.high) { candidate.high = t.price; candidate.highTimeUs = t.timestampUs; }
    if (t.price < candidate.low) { candidate.low = t.price; candidate.lowTimeUs = t.timestampUs; }
    candidate.close = t.price;
    if (seen_ && next) { Bar done = current_; done.complete = true; AppendClosed(done); }
    current_ = std::move(candidate); last_ = t; seen_ = true;
    return TickResult::Applied;
}
void BarSeries::Finish(std::int64_t watermark) {
    if (finished_) throw std::logic_error("bar stream already finished");
    if (watermark < 0 || (seen_ && watermark < last_.timestampUs))
        throw std::invalid_argument("watermark before last tick");
    if (seen_) { Bar done = current_; done.complete = watermark >= done.endUs; AppendClosed(done); }
    finished_ = true;
}
bool BarSeries::Current(Bar& out) const {
    if (!seen_ || finished_) return false;
    out = current_; return true;
}
TickCsvReader::TickCsvReader(std::istream& input) : input_(input) {
    std::string header;
    if (!Line(header) || header != "instrument,trading_day,timestamp_us,price,cumulative_volume")
        throw std::invalid_argument("CSV row 1: normalized header required");
}
bool TickCsvReader::Line(std::string& line) {
    line.clear(); char c;
    while (input_.get(c)) {
        if (c == '\n') break;
        if (line.size() == 4096) throw std::invalid_argument("CSV row exceeds 4096 bytes");
        line.push_back(c);
    }
    if (input_.bad() || (input_.fail() && !input_.eof())) throw std::runtime_error("CSV read failure");
    if (line.empty() && input_.eof()) return false;
    ++row_;
    if (!line.empty() && line.back() == '\r') line.pop_back();
    return true;
}
bool TickCsvReader::Next(Tick& tick) {
    std::string line;
    if (!Line(line)) return false;
    try {
        std::vector<std::string> fields;
        std::size_t begin = 0;
        for (;;) {
            const std::size_t end = line.find(',', begin);
            fields.push_back(line.substr(begin, end == std::string::npos ? end : end-begin));
            if (end == std::string::npos) break;
            begin = end + 1;
        }
        if (fields.size() != 5) throw std::invalid_argument("expected five columns");
        Tick candidate; candidate.instrument = fields[0];
        const std::uint64_t day = Unsigned(fields[1]), stamp = Unsigned(fields[2]);
        if (day > 99991231 || stamp > static_cast<std::uint64_t>(std::numeric_limits<std::int64_t>::max()))
            throw std::invalid_argument("day/timestamp overflow");
        candidate.tradingDay = static_cast<int>(day); candidate.timestampUs = static_cast<std::int64_t>(stamp);
        std::istringstream price(fields[3]); price.imbue(std::locale::classic()); price >> std::noskipws;
        if (!(price >> candidate.price) || price.peek() != std::char_traits<char>::eof())
            throw std::invalid_argument("invalid price");
        candidate.cumulativeVolume = Unsigned(fields[4]); ValidateTick(candidate);
        tick = std::move(candidate); return true;
    } catch (const std::invalid_argument& e) {
        throw std::invalid_argument("CSV row " + std::to_string(row_) + ": " + e.what());
    }
}
std::size_t ExtremeIndex(const std::deque<Bar>& bars, std::size_t begin,
    std::size_t end, bool highest, bool oldestTie) {
    Range(bars, begin, end);
    std::size_t selected = begin;
    for (std::size_t i = begin + 1; i <= end; ++i) {
        const Bar& a = bars[bars.size()-1-i]; const Bar& b = bars[bars.size()-1-selected];
        const double av = highest ? a.high : a.low, bv = highest ? b.high : b.low;
        if ((highest ? av > bv : av < bv) || (oldestTie && av == bv)) selected = i;
    }
    return selected;
}
double MeanClose(const std::deque<Bar>& bars, std::size_t begin, std::size_t end) {
    Range(bars, begin, end); long double sum = 0;
    for (std::size_t i = begin; i <= end; ++i) sum += bars[bars.size()-1-i].close;
    return static_cast<double>(sum / (end-begin+1));
}
}} // namespace
