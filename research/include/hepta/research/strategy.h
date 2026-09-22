#pragma once
#include "market_data.h"
#include <cstddef>
#include <cstdint>
#include <string>

namespace hepta { namespace research {
struct Forecast {
    std::string instrument;
    std::int64_t observedAtUs = 0;
    int direction = 0; // -1, 0, +1 is a forecast, NOT an authoritative position.
};
class BarStrategy {
public:
    virtual ~BarStrategy() {}
    // Low-level calculation callback. Its bar-end timestamp is only an
    // idealized availability assumption; use ObserveCompletedBar for delivery.
    virtual bool OnCompletedBar(const Bar& bar, Forecast& forecast) = 0;
    // Explicit observation time in the same UTC-microsecond domain as the bar.
    // Reject an incomplete/future bar BEFORE entering the strategy callback.
    // Publish a bounded, instrument-matched forecast at the supplied observation
    // time, never at the earlier bar close. False/error leaves output unchanged.
    // Thread-affine: the caller owns the monotonic delivery clock (the replay
    // consumer uses ReplayMatcher). No wall clock or trading authority is added.
    // A user callback's internal state is not rolled back if it throws or emits
    // invalid output; this wrapper is not a transaction manager for user code.
    bool ObserveCompletedBar(const Bar& bar, std::int64_t observedAtUs,
                             Forecast& forecast);
};
// Executable example of migrating a strategy calculation, not a claim that all
// private historical strategies have been ported or that this is profitable.
class MovingAverageForecast : public BarStrategy {
public:
    MovingAverageForecast(std::size_t fast, std::size_t slow);
    bool OnCompletedBar(const Bar& bar, Forecast& forecast) override;
private:
    std::size_t fast_, slow_;
    BarSeries history_;
    int lastDirection_ = 0;
};
}} // namespace hepta::research
