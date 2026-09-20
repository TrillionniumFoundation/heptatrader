#pragma once

#include "market_data.hpp"
#include <algorithm>
#include <limits>
#include <stdexcept>
#include <utility>

namespace hepta { namespace research {

enum class PriceField { Open, High, Low, Close };
struct BarSearchResult {
    bool found;
    std::size_t index; // meaningful only when found == true
};
struct ConfirmedSwing {
    std::size_t index;
    std::int64_t confirmedAtUs; // end of the complete right comparison window
};

// Single-writer research history, not an authoritative market/position store.
// All indices are chronological and ranges inclusive. Returned references and
// indices may be invalidated by mutation; Revision() detects explicit changes.
// Queries never sort input, invent gap bars, or mark a partial bar complete.
class BarSeries {
public:
    explicit BarSeries(std::string instrument, std::size_t capacity = 100000)
        : instrument_(std::move(instrument)), capacity_(capacity) {
        if (instrument_.empty() || instrument_.size() > 128 || capacity_ == 0)
            throw std::invalid_argument("series identity/capacity");
        for (unsigned char c : instrument_)
            if (!((c >= 'a' && c <= 'z') || (c >= 'A' && c <= 'Z') ||
                  (c >= '0' && c <= '9') || c == '.' || c == '_' || c == '-' || c == ':'))
                throw std::invalid_argument("series instrument character");
    }
    const std::string& Instrument() const { return instrument_; }
    std::size_t Size() const { return bars_.size(); }
    std::uint64_t Revision() const { return revision_; }
    const Bar& At(std::size_t index) const { return bars_.at(index); }
    const Bar& FromLatest(std::size_t offset = 0) const {
        if (offset >= bars_.size()) throw std::out_of_range("reverse bar index");
        return bars_[bars_.size()-1-offset];
    }
    const std::vector<Bar>& Bars() const { return bars_; }

    void Append(const Bar& bar) {
        Validate(bar); CheckRevision();
        if (bars_.size() >= capacity_) throw std::length_error("series capacity");
        if (!bars_.empty()) {
            const Bar& previous = bars_.back();
            if (!previous.complete || bar.beginUs < previous.endUs ||
                bar.tradingDay < previous.tradingDay)
                throw std::invalid_argument("bar overlap/order/unfinished predecessor");
        }
        bars_.push_back(bar);
        ++revision_;
    }
    // Explicit correction, including finalizing the last partial bar. Identity,
    // interval and trading day cannot be changed by an OHLC correction.
    void Replace(std::size_t index, const Bar& replacement) {
        const Bar& old = bars_.at(index);
        Validate(replacement); CheckRevision();
        if (replacement.beginUs != old.beginUs || replacement.endUs != old.endUs ||
            replacement.tradingDay != old.tradingDay ||
            (index+1 < bars_.size() && !replacement.complete))
            throw std::invalid_argument("replacement changes interval/day/completeness");
        Bar accepted = replacement;
        std::swap(bars_[index], accepted);
        ++revision_;
    }
    // A bar starting exactly at the boundary is retained in both operations.
    void RemoveBefore(std::int64_t beginUs) { Trim(beginUs, true); }
    void RemoveAfter(std::int64_t beginUs) { Trim(beginUs, false); }

    std::size_t Highest(std::size_t first, std::size_t last,
                        PriceField field = PriceField::High, bool earliestTie = false) const {
        return Extreme(first, last, field, earliestTie, true);
    }
    std::size_t Lowest(std::size_t first, std::size_t last,
                       PriceField field = PriceField::Low, bool earliestTie = false) const {
        return Extreme(first, last, field, earliestTie, false);
    }
    BarSearchResult NextHigher(std::int64_t threshold, std::size_t first,
                               std::size_t last, PriceField field = PriceField::High) const {
        return Next(threshold, first, last, field, true);
    }
    BarSearchResult NextLower(std::int64_t threshold, std::size_t first,
                              std::size_t last, PriceField field = PriceField::Low) const {
        return Next(threshold, first, last, field, false);
    }
    // Strict extrema: plateaus are not peaks/troughs. The entire comparison
    // window must lie inside [first,last], be complete and end <= asOfUs.
    // This prevents a centered historical peak from becoming a same-bar signal.
    std::vector<ConfirmedSwing> Peaks(std::size_t first, std::size_t last,
        std::size_t radius, std::int64_t asOfUs, PriceField field = PriceField::High) const {
        return Swings(first, last, radius, asOfUs, field, true);
    }
    std::vector<ConfirmedSwing> Troughs(std::size_t first, std::size_t last,
        std::size_t radius, std::int64_t asOfUs, PriceField field = PriceField::Low) const {
        return Swings(first, last, radius, asOfUs, field, false);
    }
    std::size_t CountTradingDay(const std::string& day) const {
        CivilDay::Parse(day);
        return static_cast<std::size_t>(std::count_if(bars_.begin(), bars_.end(),
            [&day](const Bar& b) { return b.tradingDay == day; }));
    }
    std::size_t CountSinceTradingDay() const {
        return bars_.empty() ? 0 : CountTradingDay(bars_.back().tradingDay);
    }
    // Sum actual observations only. Without a supplied session calendar, a
    // gap prevents complete=true; no inference that the gap was a market break.
    Bar Aggregate(std::size_t first, std::size_t last) const {
        Range(first, last);
        Bar result = bars_[first];
        for (std::size_t i=first+1; i<=last; ++i) {
            const Bar& next = bars_[i];
            if (next.tradingDay != result.tradingDay)
                throw std::invalid_argument("aggregate crosses trading day");
            const auto maximum = std::numeric_limits<std::uint64_t>::max();
            if (next.volume > maximum-result.volume || next.ticks > maximum-result.ticks)
                throw std::overflow_error("aggregate counter");
            result.complete = result.complete && next.complete && next.beginUs == result.endUs;
            result.endUs = next.endUs;
            result.high = std::max(result.high, next.high);
            result.low = std::min(result.low, next.low);
            result.close = next.close;
            result.volume += next.volume; result.ticks += next.ticks;
        }
        return result;
    }
private:
    std::string instrument_;
    std::size_t capacity_;
    std::uint64_t revision_ = 0;
    std::vector<Bar> bars_;

    static void Field(PriceField field) {
        switch (field) {
        case PriceField::Open: case PriceField::High:
        case PriceField::Low: case PriceField::Close: return;
        }
        throw std::invalid_argument("price field");
    }
    static std::int64_t Price(const Bar& b, PriceField field) {
        switch (field) {
        case PriceField::Open: return b.open;
        case PriceField::High: return b.high;
        case PriceField::Low: return b.low;
        case PriceField::Close: return b.close;
        }
        throw std::invalid_argument("price field");
    }
    void Validate(const Bar& bar) const {
        CivilDay::Parse(bar.tradingDay);
        if (bar.instrument != instrument_ || bar.beginUs < 0 || bar.endUs <= bar.beginUs ||
            bar.low > std::min(bar.open, bar.close) || bar.high < std::max(bar.open, bar.close))
            throw std::invalid_argument("bar identity/interval/OHLC");
    }
    void CheckRevision() const {
        if (revision_ == std::numeric_limits<std::uint64_t>::max())
            throw std::overflow_error("series revision");
    }
    void Range(std::size_t first, std::size_t last) const {
        if (first > last || last >= bars_.size()) throw std::out_of_range("inclusive series range");
    }
    void Trim(std::int64_t boundary, bool before) {
        if (boundary < 0) throw std::invalid_argument("negative trim boundary");
        const auto cut = before
            ? std::lower_bound(bars_.begin(), bars_.end(), boundary,
                [](const Bar& b, std::int64_t t) { return b.beginUs < t; })
            : std::upper_bound(bars_.begin(), bars_.end(), boundary,
                [](std::int64_t t, const Bar& b) { return t < b.beginUs; });
        if ((before && cut == bars_.begin()) || (!before && cut == bars_.end())) return;
        CheckRevision();
        std::vector<Bar> retained(before ? cut : bars_.begin(), before ? bars_.end() : cut);
        bars_.swap(retained); ++revision_;
    }
    std::size_t Extreme(std::size_t first, std::size_t last, PriceField field,
                        bool earliest, bool higher) const {
        Range(first, last); Field(field);
        std::size_t best = first;
        for (std::size_t i=first+1; i<=last; ++i) {
            const auto value = Price(bars_[i], field), prior = Price(bars_[best], field);
            if ((higher ? value > prior : value < prior) || (!earliest && value == prior)) best=i;
        }
        return best;
    }
    BarSearchResult Next(std::int64_t threshold, std::size_t first, std::size_t last,
                         PriceField field, bool higher) const {
        Range(first, last); Field(field);
        for (std::size_t i=first; i<=last; ++i)
            if (higher ? Price(bars_[i],field) > threshold : Price(bars_[i],field) < threshold)
                return {true, i};
        return {false, 0};
    }
    std::vector<ConfirmedSwing> Swings(std::size_t first, std::size_t last,
        std::size_t radius, std::int64_t asOf, PriceField field, bool higher) const {
        Range(first, last); Field(field);
        if (asOf < 0) throw std::invalid_argument("negative query time");
        std::vector<ConfirmedSwing> result;
        if (radius > (last-first)/2) return result; // includes SIZE_MAX safely
        for (std::size_t i=first+radius; i<=last-radius; ++i) {
            const auto confirmed = bars_[i+radius].endUs;
            if (confirmed > asOf) break;
            bool qualifies = bars_[i].complete;
            const auto center = Price(bars_[i], field);
            for (std::size_t j=i-radius; qualifies && j<=i+radius; ++j) {
                if (!bars_[j].complete) qualifies=false;
                if (j!=i && (higher ? Price(bars_[j],field)>=center : Price(bars_[j],field)<=center))
                    qualifies=false;
            }
            if (qualifies) result.push_back({i, confirmed});
        }
        return result;
    }
};

}} // namespace hepta::research
