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
    virtual bool OnCompletedBar(const Bar& bar, Forecast& forecast) = 0;
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
