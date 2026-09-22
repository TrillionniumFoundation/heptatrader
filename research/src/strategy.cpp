#include "hepta/research/strategy.h"
#include <stdexcept>
namespace hepta { namespace research {
bool BarStrategy::ObserveCompletedBar(const Bar& bar, std::int64_t observedAtUs,
                                      Forecast& forecast) {
    ValidateBar(bar);
    if (!bar.complete) throw std::invalid_argument("RESEARCH_PARTIAL_BAR");
    if (observedAtUs < bar.endUs)
        throw std::invalid_argument("RESEARCH_STRATEGY_OBSERVATION_BEFORE_CLOSE");
    Forecast next;
    if (!OnCompletedBar(bar, next)) return false;
    if (next.instrument != bar.instrument || next.direction < -1 || next.direction > 1)
        throw std::invalid_argument("RESEARCH_STRATEGY_FORECAST_INVALID");
    next.observedAtUs = observedAtUs;
    // All validation/allocation precedes publication to the caller. The input
    // callback remains the existing computation, not an execution permission.
    forecast.instrument.swap(next.instrument);
    forecast.observedAtUs = next.observedAtUs;
    forecast.direction = next.direction;
    return true;
}
MovingAverageForecast::MovingAverageForecast(std::size_t fast, std::size_t slow)
    : fast_(fast), slow_(slow), history_(slow) {
    if (fast == 0 || fast >= slow || slow > 1000000)
        throw std::invalid_argument("RESEARCH_STRATEGY_WINDOW_INVALID");
}
bool MovingAverageForecast::OnCompletedBar(const Bar& bar, Forecast& forecast) {
    history_.Append(bar);
    if (history_.Size() < slow_) return false;
    const double fast = history_.MeanClose(fast_);
    const double slow = history_.MeanClose(slow_);
    const int direction = fast > slow ? 1 : (fast < slow ? -1 : 0);
    if (direction == lastDirection_) return false;
    forecast.instrument = bar.instrument;
    forecast.observedAtUs = bar.endUs;
    forecast.direction = direction;
    lastDirection_ = direction;
    return true;
}
IntegerGridMovingAverage::IntegerGridMovingAverage(std::size_t fast, std::size_t slow)
    : fast_(fast), slow_(slow) {
    if (fast == 0 || fast >= slow || slow > 100000)
        throw std::invalid_argument("RESEARCH_STRATEGY_WINDOW_INVALID");
}
int IntegerGridMovingAverage::ObserveClose(std::int64_t ticks) {
    if (ticks < -(1LL << 40) || ticks > (1LL << 40))
        throw std::invalid_argument("RESEARCH_STRATEGY_GRID_BOUND");
    // At most 100000 values of magnitude 2^40: each sum fits in signed 64 bits.
    // Publish allocation before changing sums, retaining failed-input atomicity.
    values_.push_back(ticks); fastSum_ += ticks; slowSum_ += ticks;
    if (values_.size() > fast_) fastSum_ -= values_[values_.size() - fast_ - 1];
    if (values_.size() > slow_) { slowSum_ -= values_.front(); values_.pop_front(); }
    if (values_.size() < slow_) return 0;
    // Compare rational means without binary64 rounding or overflowing products.
    auto fq = fastSum_ / static_cast<std::int64_t>(fast_), fr = fastSum_ % static_cast<std::int64_t>(fast_);
    auto sq = slowSum_ / static_cast<std::int64_t>(slow_), sr = slowSum_ % static_cast<std::int64_t>(slow_);
    if (fr < 0) { --fq; fr += static_cast<std::int64_t>(fast_); }
    if (sr < 0) { --sq; sr += static_cast<std::int64_t>(slow_); }
    if (fq != sq) return fq > sq ? 1 : -1;
    const auto left = fr * static_cast<std::int64_t>(slow_), right = sr * static_cast<std::int64_t>(fast_);
    return left > right ? 1 : (left < right ? -1 : 0);
}
}} // namespace hepta::research
