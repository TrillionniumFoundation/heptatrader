#pragma once
#include <cstddef>
#include <cstdint>
#include <deque>
#include "hepta/research/market_data.h"
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

// Cost allocation is explicit: it changes realized/unrealized attribution,
// not total marked P&L. FIFO is a research cost convention, NOT a venue's
// close-today/close-yesterday instruction or tax accounting policy.
enum class CostBasis { WeightedAverage, Fifo };

// Single-instrument, futures-style research P&L. No margin engine, broker
// credentials, execution-authority interface, persistence or live reconciliation.
class ResearchLedger {
public:
    ResearchLedger(std::string instrument, double initialEquity,
                   double multiplier, std::size_t maxFillIds = 100000,
                   CostBasis costBasis = CostBasis::WeightedAverage);
    bool Apply(const ResearchFill& fill); // false for an exact duplicate.
    ResearchAccount Mark(double markPrice) const;
    std::int64_t Quantity() const { return quantity_; }
    CostBasis Basis() const { return costBasis_; }
private:
    std::string instrument_;
    double initialEquity_, multiplier_;
    std::size_t maxFillIds_;
    CostBasis costBasis_;
    struct Lot { std::int64_t quantity; long double price; };
    // Quantity-compressed lots, not one allocation per contract. FIFO updates
    // stage the active lots before committing; cost is O(active lots), not O(q).
    std::deque<Lot> lots_;
    std::int64_t quantity_ = 0, lastTimestampUs_ = 0;
    long double average_ = 0, realized_ = 0, fees_ = 0;
    std::map<std::string, ResearchFill> fills_;
};

struct ResearchInstrument {
    std::string instrument, currency;
    double multiplier = 0;
    CostBasis costBasis = CostBasis::WeightedAverage;
};
struct ResearchCashFlow {
    std::string flowId;
    std::int64_t timestampUs = 0;
    double amount = 0; // Explicit deposit (+) or withdrawal (-), never a fill.
};
struct ResearchPositionSnapshot {
    std::int64_t quantity = 0;
    double averageEntry = 0, realizedGross = 0, unrealized = 0, fees = 0;
    double markPrice = 0;
    std::int64_t markTimestampUs = 0;
};
struct ResearchPortfolioSnapshot {
    std::string currency;
    std::int64_t timestampUs = 0;
    double initialEquity = 0, externalFlows = 0;
    double realizedGross = 0, unrealized = 0, fees = 0, equity = 0;
    std::map<std::string, ResearchPositionSnapshot> positions;
};

// Thread-affine OFFLINE portfolio. A fixed same-currency universe prevents
// implicit FX conversions. One monotonic delivery clock spans fills, flows
// and ticks (equal timestamps retain caller delivery order). Fill identities
// are portfolio-global; flow identities have their own namespace. Exact retries
// do not advance time, consume capacity or revalidate a stale mark.
//
// Every position-changing fill invalidates that instrument's cached valuation.
// An OPEN position requires a subsequent new-sequence tick and an explicit
// caller-supplied maximum age; the fill price is not substituted for a quote.
// Snapshot is read-only, not a watermark or a historical/bitemporal query.
// Zero/negative equity is reported, never treated as permission to trade.
// No margin, FX, exchange settlement, persistence or execution authority.
class ResearchPortfolio {
public:
    ResearchPortfolio(double initialEquity, std::string currency,
                      const std::vector<ResearchInstrument>& instruments,
                      std::size_t maxEventIds = 100000);
    bool Apply(const ResearchFill& fill);
    bool ApplyCashFlow(const ResearchCashFlow& flow);
    bool Observe(const Tick& tick);
    ResearchPortfolioSnapshot Snapshot(std::int64_t asOfUs,
                                       std::int64_t maxMarkAgeUs) const;
private:
    struct Position {
        Position(const ResearchInstrument& spec, double initial, std::size_t capacity)
            : ledger(spec.instrument, initial, spec.multiplier, capacity, spec.costBasis) {}
        ResearchLedger ledger;
        Tick lastTick;
        bool hasTick = false, markCurrent = false;
    };
    double initialEquity_;
    std::string currency_;
    std::size_t maxEventIds_, eventCount_ = 0;
    std::int64_t clockUs_ = 0;
    long double flows_ = 0;
    std::map<std::string, Position> positions_;
    std::map<std::string, ResearchFill> fills_;
    std::map<std::string, ResearchCashFlow> cashFlows_;
};

}} // namespace hepta::research
