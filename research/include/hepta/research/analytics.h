#pragma once
#include <cstddef>
#include <cstdint>
#include <map>
#include <string>
#include <vector>

namespace hepta { namespace research {

struct Metric {
    bool defined = false;
    double value = 0;
};
struct EquityPoint {
    std::int64_t timestampUs = 0;
    double equity = 0;
    double externalFlow = 0; // End-of-period deposit (+) or withdrawal (-).
};
struct Performance {
    std::size_t returnCount = 0;
    double totalReturn = 0;
    double maxDrawdown = 0;
    Metric annualizedReturn, annualizedVolatility, sharpe, sortino, calmar;
};
// Observations must be equally spaced at the caller-declared sampling rate.
// No hard-coded 16:00 cutoff, inferred holiday calendar, or automatic deposit.
// Nonpositive capital is rejected. Undefined ratios stay explicitly undefined.
Performance EvaluateEquity(const std::vector<EquityPoint>& points,
                           double periodsPerYear, double annualRiskFreeRate = 0);

struct ResearchFill {
    std::string fillId, orderId, instrument;
    std::int64_t timestampUs = 0;
    int side = 0; // +1 buy, -1 sell.
    std::int64_t quantity = 0;
    double price = 0, fee = 0;
};
struct ResearchAccount {
    std::int64_t quantity = 0;
    double averageEntry = 0, realizedGross = 0, unrealized = 0, fees = 0, equity = 0;
};

// Single-instrument, futures-style research P&L. No margin engine, broker
// credentials, execution-authority interface, persistence or live reconciliation.
class ResearchLedger {
public:
    ResearchLedger(std::string instrument, double initialEquity,
                   double multiplier, std::size_t maxFillIds = 100000);
    bool Apply(const ResearchFill& fill); // false for an exact duplicate.
    ResearchAccount Mark(double markPrice) const;
private:
    std::string instrument_;
    double initialEquity_, multiplier_;
    std::size_t maxFillIds_;
    std::int64_t quantity_ = 0, lastTimestampUs_ = 0;
    long double average_ = 0, realized_ = 0, fees_ = 0;
    std::map<std::string, ResearchFill> fills_;
};

}} // namespace hepta::research
