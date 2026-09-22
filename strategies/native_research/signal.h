#pragma once
#include "market.h"
#include <cstddef>
#include <cstdint>
#include <deque>
#include <string>

namespace hepta { namespace research {
// A proposal, never a claim of position, risk approval, or broker acceptance.
struct BoundedIntent {
    std::string instrument;
    std::string action;
    double quantity = 0, limitPrice = 0;
    std::int64_t observedAtUs = 0, expiresAtMs = 0;
};

// Example migration consumer of legacy-style completed-candle callbacks.
// This is NOT a claim to reproduce every old CTA strategy or its performance.
// The reference window excludes the candidate bar; identical callbacks do not
// generate another signal. Uses caller-provided clock, no hidden thread/timer.
class BreakoutSignal {
public:
    BreakoutSignal(std::string instrument, std::size_t lookback, double quantity,
                   std::int64_t maxAgeMs, std::int64_t intentTtlMs);
    bool OnClosedBar(const Bar& bar, std::int64_t nowMs, BoundedIntent& intent);
private:
    std::string instrument_;
    std::size_t lookback_;
    double quantity_;
    std::int64_t maxAgeMs_, ttlMs_;
    std::deque<Bar> window_;
};
void ValidateIntent(const BoundedIntent& intent, std::int64_t nowMs);
}} // namespace
