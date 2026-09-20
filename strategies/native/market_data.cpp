#include "market_data.h"
#include <algorithm>
#include <cmath>
#include <iomanip>
#include <limits>
#include <locale>
#include <sstream>
#include <stdexcept>
#include <utility>

namespace hepta { namespace research {
namespace {
void Require(bool ok, const char* reason) { if (!ok) throw std::invalid_argument(reason); }
std::uint64_t Add(std::uint64_t a, std::uint64_t b) {
    if (b > std::numeric_limits<std::uint64_t>::max() - a)
        throw std::overflow_error("RESEARCH_VOLUME_OVERFLOW");
    return a + b;
}
std::uint64_t Unsigned(const std::string& s) {
    Require(!s.empty(), "RESEARCH_CSV_INTEGER");
    std::uint64_t n = 0;
    for (char c : s) {
        Require(c >= '0' && c <= '9', "RESEARCH_CSV_INTEGER");
        const unsigned d = static_cast<unsigned>(c - '0');
        Require(n <= (std::numeric_limits<std::uint64_t>::max() - d) / 10,
                "RESEARCH_CSV_INTEGER_OVERFLOW");
        n = n * 10 + d;
    }
    return n;
}
bool ValidDay(const std::string& s) {
    if (s.size() != 8 || s.find_first_not_of("0123456789") != std::string::npos) return false;
    const int y = std::stoi(s.substr(0,4)), m = std::stoi(s.substr(4,2)), d = std::stoi(s.substr(6,2));
    if (y < 1970 || m < 1 || m > 12 || d < 1) return false;
    const int days[] = {31,28,31,30,31,30,31,31,30,31,30,31};
    const bool leap = y % 4 == 0 && (y % 100 != 0 || y % 400 == 0);
    return d <= days[m-1] + (m == 2 && leap ? 1 : 0);
}
void ValidateTick(const Tick& t) {
    Require(ValidInstrument(t.instrument) && t.timestampMs > 0 && t.sequence > 0 &&
            std::isfinite(t.price) && t.price > 0, "RESEARCH_TICK_INVALID");
}
}
bool ValidInstrument(const std::string& s) {
    if (s.empty() || s.size() > 128) return false;
    for (unsigned char c : s)
        if (!((c >= 'a' && c <= 'z') || (c >= 'A' && c <= 'Z') ||
              (c >= '0' && c <= '9') || c == '.' || c == ':' || c == '_' || c == '-' || c == '/')) return false;
    return true;
}
SessionCalendar::SessionCalendar(std::vector<SessionWindow> windows) : windows_(std::move(windows)) {
    Require(!windows_.empty() && windows_.size() <= 100000, "RESEARCH_CALENDAR_SIZE");
    for (std::size_t i=0; i<windows_.size(); ++i) {
        const auto& w = windows_[i];
        Require(ValidDay(w.tradingDay) && w.beginMs > 0 && w.endMs > w.beginMs, "RESEARCH_SESSION_INVALID");
        if (i) Require(w.beginMs >= windows_[i-1].endMs && w.tradingDay >= windows_[i-1].tradingDay,
                       "RESEARCH_SESSION_ORDER_OR_OVERLAP");
    }
}
const SessionWindow& SessionCalendar::At(std::int64_t t) const {
    auto it = std::upper_bound(windows_.begin(), windows_.end(), t,
        [](std::int64_t v, const SessionWindow& w) { return v < w.beginMs; });
    Require(it != windows_.begin(), "RESEARCH_OUTSIDE_SESSION");
    --it;
    Require(t < it->endMs, "RESEARCH_OUTSIDE_SESSION");
    return *it;
}
SessionWindow SessionCalendar::TradingDay(const std::string& label) const {
    auto first = std::lower_bound(windows_.begin(), windows_.end(), label,
        [](const SessionWindow& w, const std::string& s) { return w.tradingDay < s; });
    Require(first != windows_.end() && first->tradingDay == label, "RESEARCH_UNKNOWN_TRADING_DAY");
    auto last = std::upper_bound(first, windows_.end(), label,
        [](const std::string& s, const SessionWindow& w) { return s < w.tradingDay; });
    SessionWindow result = *first;
    result.endMs = (last-1)->endMs;
    return result;
}
TickCursor::TickCursor(std::string instrument, InitialVolume mode)
    : instrument_(std::move(instrument)), initialVolume_(mode) {
    Require(ValidInstrument(instrument_), "RESEARCH_INSTRUMENT_INVALID");
    Require(mode == InitialVolume::Baseline || mode == InitialVolume::IncludeCumulative,
            "RESEARCH_VOLUME_MODE_INVALID");
}
TickObservation TickCursor::Validate(const Tick& t, const SessionCalendar& calendar) const {
    ValidateTick(t);
    Require(t.instrument == instrument_, "RESEARCH_INSTRUMENT_MISMATCH");
    TickObservation o;
    o.tradingDay = calendar.At(t.timestampMs).tradingDay;
    if (initialized_) {
        if (t.timestampMs == last_.timestampMs && t.sequence == last_.sequence &&
            t.cumulativeVolume == last_.cumulativeVolume && t.price == last_.price) {
            o.duplicate = true;
            return o;
        }
        Require(t.timestampMs >= last_.timestampMs, "RESEARCH_OUT_OF_ORDER_TIME");
        if (o.tradingDay == tradingDay_) {
            Require(t.sequence > last_.sequence, "RESEARCH_SEQUENCE_CONFLICT");
            Require(t.cumulativeVolume >= last_.cumulativeVolume, "RESEARCH_VOLUME_RESET_WITHIN_DAY");
            o.volumeDelta = t.cumulativeVolume - last_.cumulativeVolume;
            return o;
        }
        Require(o.tradingDay > tradingDay_, "RESEARCH_OUT_OF_ORDER_DAY");
    }
    o.volumeDelta = initialVolume_ == InitialVolume::IncludeCumulative ? t.cumulativeVolume : 0;
    return o;
}
void TickCursor::Commit(const Tick& t, const TickObservation& o) {
    last_ = t; tradingDay_ = o.tradingDay; initialized_ = true;
}
BarBuilder::BarBuilder(std::string instrument, SessionCalendar calendar, std::int64_t interval,
                       InitialVolume mode)
    : calendar_(std::move(calendar)), cursor_(std::move(instrument), mode), intervalMs_(interval) {
    Require(interval >= 0, "RESEARCH_BAR_INTERVAL");
}
TickDisposition BarBuilder::Push(const Tick& t, std::vector<Bar>& completed) {
    Require(!finished_, "RESEARCH_STREAM_FINISHED");
    Require(t.timestampMs >= watermark_, "RESEARCH_BEHIND_WATERMARK");
    const TickObservation o = cursor_.Validate(t, calendar_);
    if (o.duplicate) return TickDisposition::Duplicate;
    const SessionWindow& w = calendar_.At(t.timestampMs);
    SessionWindow bucket = intervalMs_ == 0 ? calendar_.TradingDay(w.tradingDay) : w;
    if (intervalMs_ != 0) {
        bucket.beginMs += ((t.timestampMs - w.beginMs) / intervalMs_) * intervalMs_;
        bucket.endMs = bucket.beginMs + std::min(intervalMs_, w.endMs - bucket.beginMs);
    }
    Bar next;
    const bool same = active_ && current_.beginMs == bucket.beginMs && current_.endMs == bucket.endMs;
    if (same) {
        next = current_;
        next.high = std::max(next.high, t.price); next.low = std::min(next.low, t.price);
        next.close = t.price; next.volume = Add(next.volume, o.volumeDelta);
        next.observations = Add(next.observations, 1);
    } else {
        next.instrument = t.instrument; next.tradingDay = o.tradingDay;
        next.beginMs = bucket.beginMs; next.endMs = bucket.endMs;
        next.open = next.high = next.low = next.close = t.price;
        next.volume = o.volumeDelta; next.observations = 1;
        if (active_) { Bar closed = current_; closed.complete = true; completed.push_back(closed); }
    }
    current_ = std::move(next); active_ = true;
    cursor_.Commit(t, o);
    return TickDisposition::Applied;
}
void BarBuilder::AdvanceWatermark(std::int64_t t, std::vector<Bar>& completed) {
    Require(!finished_ && t > 0 && t >= watermark_, "RESEARCH_WATERMARK_INVALID");
    if (active_ && t >= current_.endMs) {
        Bar closed = current_; closed.complete = true; completed.push_back(closed); active_ = false;
    }
    watermark_ = t;
}
void BarBuilder::Finish(std::vector<Bar>& tail) {
    if (finished_) return;
    if (active_) tail.push_back(current_);
    active_ = false; finished_ = true;
}
void ValidateBar(const Bar& b) {
    Require(ValidInstrument(b.instrument) && ValidDay(b.tradingDay) && b.beginMs > 0 &&
            b.endMs > b.beginMs && b.observations > 0 && std::isfinite(b.open) &&
            std::isfinite(b.high) && std::isfinite(b.low) && std::isfinite(b.close) && b.low > 0 &&
            b.low <= std::min(b.open,b.close) && b.high >= std::max(b.open,b.close), "RESEARCH_BAR_INVALID");
}
Bar MergeBars(const std::vector<Bar>& bars) {
    Require(!bars.empty(), "RESEARCH_EMPTY_BARS");
    Bar merged = bars.front(); ValidateBar(merged);
    for (std::size_t i=1; i<bars.size(); ++i) {
        const Bar& b = bars[i]; ValidateBar(b);
        Require(b.instrument == merged.instrument && b.tradingDay == merged.tradingDay &&
                b.beginMs >= merged.endMs, "RESEARCH_BAR_MERGE_CONFLICT");
        merged.endMs = b.endMs; merged.close = b.close;
        merged.high = std::max(merged.high,b.high); merged.low = std::min(merged.low,b.low);
        merged.volume = Add(merged.volume,b.volume); merged.observations = Add(merged.observations,b.observations);
        merged.complete = merged.complete && b.complete;
    }
    return merged;
}
BarWindow::BarWindow(std::size_t capacity) : capacity_(capacity) {
    Require(capacity > 0 && capacity <= 1000000, "RESEARCH_BAR_WINDOW_CAPACITY");
}
void BarWindow::Push(const Bar& b) {
    ValidateBar(b); Require(b.complete, "RESEARCH_INCOMPLETE_BAR");
    if (!bars_.empty()) Require(b.instrument == bars_.back().instrument &&
        b.beginMs >= bars_.back().endMs && b.tradingDay >= bars_.back().tradingDay, "RESEARCH_BAR_WINDOW_ORDER");
    bars_.push_back(b); if (bars_.size() > capacity_) bars_.pop_front();
}
const Bar& BarWindow::Recent(std::size_t offset) const {
    if (offset >= bars_.size()) throw std::out_of_range("RESEARCH_BAR_WINDOW_RANGE");
    return bars_[bars_.size()-1-offset];
}
double BarWindow::MeanClose(std::size_t count) const {
    Require(count > 0 && count <= bars_.size(), "RESEARCH_BAR_WINDOW_RANGE");
    long double mean = 0;
    for (std::size_t i=0; i<count; ++i) mean += (static_cast<long double>(Recent(i).close)-mean)/(i+1);
    return static_cast<double>(mean);
}
std::size_t BarWindow::Highest(std::size_t count, bool newest) const {
    Require(count > 0 && count <= bars_.size(), "RESEARCH_BAR_WINDOW_RANGE");
    std::size_t best = 0;
    for (std::size_t i=1;i<count;++i)
        if (Recent(i).high > Recent(best).high || (!newest && Recent(i).high == Recent(best).high)) best=i;
    return best;
}
std::size_t BarWindow::Lowest(std::size_t count, bool newest) const {
    Require(count > 0 && count <= bars_.size(), "RESEARCH_BAR_WINDOW_RANGE");
    std::size_t best = 0;
    for (std::size_t i=1;i<count;++i)
        if (Recent(i).low < Recent(best).low || (!newest && Recent(i).low == Recent(best).low)) best=i;
    return best;
}
const char* TickCsvHeader() { return "instrument,timestamp_ms,sequence,price,cumulative_volume"; }
Tick ParseTickCsv(const std::string& row) {
    Require(!row.empty() && row.size() <= 512, "RESEARCH_CSV_ROW_SIZE");
    std::string s = row;
    if (s.back() == '\r') s.pop_back();
    Require(s.find_first_of("\r\n\t\" ") == std::string::npos, "RESEARCH_CSV_CHARACTERS");
    std::vector<std::string> fields; std::size_t start=0;
    for (;;) {
        const auto end = s.find(',',start); fields.push_back(s.substr(start,end-start));
        if (end == std::string::npos) break;
        start=end+1;
    }
    Require(fields.size()==5, "RESEARCH_CSV_FIELDS");
    Tick t; t.instrument=fields[0];
    const auto stamp=Unsigned(fields[1]);
    Require(stamp<=static_cast<std::uint64_t>(std::numeric_limits<std::int64_t>::max()), "RESEARCH_CSV_TIMESTAMP_OVERFLOW");
    t.timestampMs=static_cast<std::int64_t>(stamp); t.sequence=Unsigned(fields[2]);
    std::istringstream price(fields[3]); price.imbue(std::locale::classic()); price >> std::noskipws >> t.price;
    Require(!price.fail() && price.peek()==std::char_traits<char>::eof(), "RESEARCH_CSV_PRICE");
    t.cumulativeVolume=Unsigned(fields[4]); ValidateTick(t); return t;
}
std::string FormatTickCsv(const Tick& t) {
    ValidateTick(t); std::ostringstream out; out.imbue(std::locale::classic());
    out << t.instrument << ',' << t.timestampMs << ',' << t.sequence << ','
        << std::setprecision(std::numeric_limits<double>::max_digits10) << t.price << ',' << t.cumulativeVolume;
    return out.str();
}
} }
