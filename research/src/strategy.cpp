#include "hepta/research/strategy.h"
#include <stdexcept>
namespace hepta { namespace research {
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
}} // namespace hepta::research
