#include "hepta/research/market_data.hpp"

#include <algorithm>
#include <cstdio>
#include <limits>
#include <stdexcept>
#include <utility>

namespace hepta { namespace research {
namespace {
bool Leap(int y) { return y % 4 == 0 && (y % 100 != 0 || y % 400 == 0); }
int MonthDays(int y, int m) {
    static const int lengths[] = {31,28,31,30,31,30,31,31,30,31,30,31};
    return lengths[m-1] + (m == 2 && Leap(y) ? 1 : 0);
}
int DaysBeforeYear(int y) {
    const int n = y - 1;
    return 365*n + n/4 - n/100 + n/400;
}
bool SameTick(const Tick& a, const Tick& b) {
    return a.instrument == b.instrument && a.tradingDay == b.tradingDay &&
        a.timestampUs == b.timestampUs && a.priceTicks == b.priceTicks &&
        a.sequence == b.sequence && a.cumulativeVolume == b.cumulativeVolume;
}
void ValidateInstrument(const std::string& value) {
    if (value.empty() || value.size() > 128) throw std::invalid_argument("instrument length");
    for (unsigned char c : value)
        if (!((c >= 'a' && c <= 'z') || (c >= 'A' && c <= 'Z') ||
              (c >= '0' && c <= '9') || c == '.' || c == '_' || c == '-' || c == ':'))
            throw std::invalid_argument("instrument character");
}
}
CivilDay::CivilDay(int y, int m, int d) : year(y), month(m), day(d) {
    if (y < 1600 || y > 9999 || m < 1 || m > 12 || d < 1 || d > MonthDays(y,m))
        throw std::invalid_argument("invalid Gregorian date (1600..9999)");
}
CivilDay CivilDay::Parse(const std::string& text) {
    if (text.size() != 8) throw std::invalid_argument("date must be YYYYMMDD");
    int n = 0;
    for (char c : text) {
        if (c < '0' || c > '9') throw std::invalid_argument("date digit");
        n = 10*n + c-'0';
    }
    return CivilDay(n/10000, (n/100)%100, n%100);
}
int CivilDay::Serial() const {
    // Public fields may have been modified by a caller: validate at use too.
    const CivilDay checked(year,month,day);
    int result = DaysBeforeYear(checked.year) - DaysBeforeYear(1970) + checked.day-1;
    for (int m=1; m<checked.month; ++m) result += MonthDays(checked.year,m);
    return result;
}
CivilDay CivilDay::FromSerial(int value) {
    if (value < CivilDay(1600,1,1).Serial() || value > CivilDay(9999,12,31).Serial())
        throw std::out_of_range("date serial");
    int lo=1600, hi=10000;
    while (lo+1 < hi) {
        int mid=lo+(hi-lo)/2;
        if (DaysBeforeYear(mid)-DaysBeforeYear(1970) <= value) lo=mid; else hi=mid;
    }
    int rest=value-(DaysBeforeYear(lo)-DaysBeforeYear(1970)), month=1;
    while (rest >= MonthDays(lo,month)) rest -= MonthDays(lo,month++);
    return CivilDay(lo,month,rest+1);
}
int CivilDay::Weekday() const { const int n=(Serial()+4)%7; return n < 0 ? n+7 : n; }
std::string CivilDay::String() const {
    CivilDay checked(year,month,day);
    char out[9]; std::snprintf(out,sizeof(out),"%04d%02d%02d",checked.year,checked.month,checked.day);
    return out;
}
BarBuilder::BarBuilder(std::string instrument, std::vector<Session> sessions,
                       std::int64_t periodUs, FirstVolume firstVolume)
    : instrument_(std::move(instrument)), sessions_(std::move(sessions)),
      period_(periodUs), firstVolume_(firstVolume) {
    ValidateInstrument(instrument_);
    if (period_ < 0 || sessions_.empty()) throw std::invalid_argument("period/sessions");
    for (std::size_t i=0; i<sessions_.size(); ++i) {
        const auto& s=sessions_[i]; CivilDay::Parse(s.tradingDay);
        if (s.beginUs < 0 || s.endUs <= s.beginUs) throw std::invalid_argument("session interval");
        if (i && (sessions_[i-1].endUs > s.beginUs || sessions_[i-1].tradingDay > s.tradingDay))
            throw std::invalid_argument("sessions overlap/order/trading-day regression");
    }
}
PushResult BarBuilder::Push(const Tick& tick, Bar& closed) {
    if (finished_) throw std::logic_error("stream already finished");
    if (tick.instrument != instrument_ || tick.timestampUs < 0 || tick.sequence == 0)
        throw std::invalid_argument("tick identity");
    CivilDay::Parse(tick.tradingDay);
    if (hasTick_) {
        if (SameTick(tick,lastTick_)) return PushResult::Duplicate;
        if (tick.timestampUs < lastTick_.timestampUs || tick.tradingDay < lastTick_.tradingDay ||
            (tick.tradingDay == lastTick_.tradingDay && tick.sequence <= lastTick_.sequence))
            throw std::invalid_argument("out-of-order/conflicting tick");
    }
    auto it=std::lower_bound(sessions_.begin(),sessions_.end(),tick.timestampUs,
        [](const Session& s, std::int64_t t){return s.endUs <= t;});
    if (it == sessions_.end() || tick.timestampUs < it->beginUs || tick.tradingDay != it->tradingDay)
        throw std::invalid_argument("tick outside supplied trading session");
    std::int64_t begin=it->beginUs, end=it->endUs;
    if (period_) {
        begin += ((tick.timestampUs-begin)/period_)*period_;
        end = begin + std::min(period_,end-begin); // no overflow at INT64_MAX
    } else {
        auto first=it, last=it;
        while (first!=sessions_.begin() && (first-1)->tradingDay==tick.tradingDay) --first;
        while (last+1!=sessions_.end() && (last+1)->tradingDay==tick.tradingDay) ++last;
        begin=first->beginUs; end=last->endUs;
    }
    std::uint64_t delta=0;
    if (hasTick_ && lastTick_.tradingDay == tick.tradingDay) {
        if (tick.cumulativeVolume < lastTick_.cumulativeVolume)
            throw std::invalid_argument("intraday cumulative volume reset requires new explicit stream");
        delta=tick.cumulativeVolume-lastTick_.cumulativeVolume;
    } else if (firstVolume_ == FirstVolume::IncludeCumulative) delta=tick.cumulativeVolume;
    const bool boundary=hasBar_ && (current_.beginUs!=begin || current_.tradingDay!=tick.tradingDay);
    Bar next;
    if (!hasBar_ || boundary) {
        next.instrument=instrument_; next.tradingDay=tick.tradingDay;
        next.beginUs=begin; next.endUs=end;
        next.open=next.high=next.low=next.close=tick.priceTicks;
    } else next=current_;
    if (delta > std::numeric_limits<std::uint64_t>::max()-next.volume ||
        next.ticks == std::numeric_limits<std::uint64_t>::max())
        throw std::overflow_error("bar counter");
    next.high=std::max(next.high,tick.priceTicks); next.low=std::min(next.low,tick.priceTicks);
    next.close=tick.priceTicks; next.volume+=delta; ++next.ticks;
    Tick accepted=tick; // allocate before committing state
    if (boundary) { closed=current_; closed.complete=true; }
    current_=std::move(next); lastTick_=std::move(accepted); hasTick_=hasBar_=true;
    return boundary ? PushResult::ClosedBar : PushResult::Buffered;
}
bool BarBuilder::Finish(Bar& last) {
    if (finished_) return false;
    if (hasBar_) { last=current_; last.complete=false; }
    finished_=true;
    return hasBar_;
}
std::size_t Highest(const std::vector<Bar>& bars,std::size_t first,std::size_t last,bool earliest) {
    if (first>last || last>=bars.size()) throw std::out_of_range("inclusive bar range");
    std::size_t best=first;
    for (std::size_t i=first; i<=last; ++i)
        if (bars[i].high>bars[best].high || (!earliest && bars[i].high==bars[best].high)) best=i;
    return best;
}
std::size_t Lowest(const std::vector<Bar>& bars,std::size_t first,std::size_t last,bool earliest) {
    if (first>last || last>=bars.size()) throw std::out_of_range("inclusive bar range");
    std::size_t best=first;
    for (std::size_t i=first; i<=last; ++i)
        if (bars[i].low<bars[best].low || (!earliest && bars[i].low==bars[best].low)) best=i;
    return best;
}
}}
