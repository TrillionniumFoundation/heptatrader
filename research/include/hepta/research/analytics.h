#pragma once
#include <cstddef>
#include <cstdint>
#include <cfloat>
#include <cmath>
#include <type_traits>
#include <deque>
#include "hepta/research/market_data.h"
#include <map>
#include <string>
#include <vector>

namespace hepta { namespace research {

namespace detail {
// Internal ledger arithmetic for ABIs where long double has only 53 bits.
// A normalized binary64 pair is rounded to a 64-bit significand after each
// operation. This retains the existing extended-significand settlement checks
// instead of relaxing them or claiming arbitrary precision. The exponent range
// remains binary64. This is storage/arithmetic in the SAME ResearchLedger, not
// another accounting model; callers must rebuild against the installed header.
class ResearchWide {
public:
    ResearchWide() : high_(0), low_(0) {}
    template<class T, typename std::enable_if<std::is_arithmetic<T>::value, int>::type = 0>
    ResearchWide(T value) : high_(static_cast<double>(value)), low_(0) {
        if (std::isfinite(high_))
            low_ = static_cast<double>(static_cast<long double>(value) -
                                       static_cast<long double>(high_));
        Normalize();
    }
    explicit operator double() const { return high_ + low_; }
    bool IsFinite() const { return std::isfinite(high_) && std::isfinite(low_); }
    friend ResearchWide operator-(ResearchWide value) {
        value.high_ = -value.high_; value.low_ = -value.low_; return value;
    }
    friend ResearchWide operator+(const ResearchWide& a, const ResearchWide& b) {
        const double high = a.high_ + b.high_;
        if (!std::isfinite(high)) return ResearchWide(high);
        const double v = high - a.high_;
        const double error = (a.high_ - (high - v)) + (b.high_ - v);
        return Parts(high, (error + a.low_) + b.low_);
    }
    friend ResearchWide operator-(const ResearchWide& a, const ResearchWide& b) { return a + -b; }
    friend ResearchWide operator*(const ResearchWide& a, const ResearchWide& b) {
        const double high = a.high_ * b.high_;
        if (!std::isfinite(high)) return ResearchWide(high);
        const double error = std::fma(a.high_, b.high_, -high);
        return Parts(high, ((error + a.high_ * b.low_) + a.low_ * b.high_) + a.low_ * b.low_);
    }
    friend ResearchWide operator/(const ResearchWide& a, const ResearchWide& b) {
        const double first = a.high_ / b.high_;
        if (!std::isfinite(first)) return ResearchWide(first);
        // Keep the product residual unrounded until the quotient correction.
        // Rounding b*first to the 64-bit storage lattice before subtracting
        // discards precisely the bits needed by this correction.
        const double product = b.high_ * first;
        const double productError = std::fma(b.high_, first, -product) + b.low_ * first;
        const double remainder = a.high_ - product;
        const double v = remainder - a.high_;
        const double remainderError = (a.high_ - (remainder - v)) + (-product - v);
        const double second = (remainder + ((remainderError + a.low_) - productError)) / b.high_;
        return Parts(first, second);
    }
    ResearchWide& operator+=(const ResearchWide& other) { return *this = *this + other; }
    ResearchWide& operator-=(const ResearchWide& other) { return *this = *this - other; }
    friend bool operator==(const ResearchWide& a, const ResearchWide& b) {
        return a.high_ == b.high_ && a.low_ == b.low_;
    }
    friend bool operator!=(const ResearchWide& a, const ResearchWide& b) { return !(a == b); }
    friend bool operator<(const ResearchWide& a, const ResearchWide& b) {
        return a.high_ < b.high_ || (a.high_ == b.high_ && a.low_ < b.low_);
    }
    friend bool operator>(const ResearchWide& a, const ResearchWide& b) { return b < a; }
    friend bool operator<=(const ResearchWide& a, const ResearchWide& b) { return a < b || a == b; }
    friend bool operator>=(const ResearchWide& a, const ResearchWide& b) { return b <= a; }
private:
    static ResearchWide Parts(double high, double low) {
        ResearchWide result; result.high_ = high; result.low_ = low; result.Normalize(); return result;
    }
    void Normalize() {
        if (!std::isfinite(high_)) { low_ = 0; return; }
        const double sum = high_ + low_;
        if (!std::isfinite(sum)) { high_ = sum; low_ = 0; return; }
        const double v = sum - high_;
        double tail = (high_ - (sum - v)) + (low_ - v);
        high_ = sum;
        if (high_ != 0 && tail != 0) {
            int exponent = 0;
            const double fractionHigh = std::frexp(high_, &exponent);
            if (std::fabs(fractionHigh) == 0.5 && ((high_ > 0 && tail < 0) || (high_ < 0 && tail > 0)))
                --exponent; // exact value lies just below a power-of-two binade
            const double quantum = std::ldexp(1.0, exponent - 64);
            if (quantum != 0) {
                // Nearest-even on the low lattice: the high binary64 limb is
                // an even multiple of this quantum. No epsilon price repair.
                const double scaled = std::fabs(tail / quantum);
                double integral = std::floor(scaled);
                const double fraction = scaled - integral;
                if (fraction > 0.5 || (fraction == 0.5 && std::fmod(integral, 2.0) != 0)) ++integral;
                tail = std::copysign(integral * quantum, tail);
            }
        }
        const double rounded = high_ + tail;
        low_ = tail - (rounded - high_);
        high_ = rounded;
    }
    double high_, low_;
};
// The override is only for a separately rebuilt diagnostic/test configuration;
// do not mix headers and objects from different accumulator configurations.
#if LDBL_MANT_DIG < 64 || defined(HEPTA_RESEARCH_TEST_PORTABLE_ACCUMULATOR)
using ResearchAccumulator = ResearchWide;
#else
using ResearchAccumulator = long double;
#endif
} // namespace detail

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
// Explicit OFFLINE variation settlement. The supplied price is an accounting
// input, not a market observation, fill, cash deposit or broker instruction.
struct ResearchSettlement {
    std::string settlementId, instrument;
    std::int64_t timestampUs = 0;
    double price = 0;
};
// Accounting basis only: no quote, unrealized P&L or equity is implied.
struct ResearchAccountState {
    std::int64_t quantity = 0;
    double averageEntry = 0, realizedGross = 0, fees = 0;
};
struct ResearchAccount {
    std::int64_t quantity = 0;
    double averageEntry = 0, realizedGross = 0, unrealized = 0, fees = 0, equity = 0;
};

// Cost allocation is explicit: it changes realized/unrealized attribution,
// not total marked P&L. FIFO is a research cost convention, NOT a venue's
// close-today/close-yesterday instruction or tax accounting policy.
enum class CostBasis { WeightedAverage, Fifo };
// Explicit OFFLINE accounting price domain. Positive is the original default.
// SignedFinite accepts finite zero/negative research fills, marks and settlement
// inputs. It grants no production quote/venue permission and does not reinterpret
// the positive-only market-data CSV or BarBuilder contracts.
enum class ResearchPriceDomain { Positive, SignedFinite };

// Single-instrument, futures-style research P&L. No margin engine, broker
// credentials, execution-authority interface, persistence or live reconciliation.
class ResearchLedger {
public:
    ResearchLedger(std::string instrument, double initialEquity,
                   double multiplier, std::size_t maxFillIds = 100000,
                   CostBasis costBasis = CostBasis::WeightedAverage,
                   ResearchPriceDomain priceDomain = ResearchPriceDomain::Positive);
    bool Apply(const ResearchFill& fill); // false for an exact duplicate.
    // Realize marked P&L and reset the remaining basis without changing quantity,
    // fees or marked equity (subject to checked floating-point arithmetic).
    // Destructive precision loss is rejected; exact settlement retries are no-ops.
    // The historical maxFillIds argument bounds combined fill/settlement IDs;
    // the two ID namespaces are distinct. Failed events do not consume an ID.
    bool Settle(const ResearchSettlement& settlement);
    ResearchAccountState State() const;
    ResearchAccount Mark(double markPrice) const;
    std::int64_t Quantity() const { return quantity_; }
    CostBasis Basis() const { return costBasis_; }
    ResearchPriceDomain PriceDomain() const { return priceDomain_; }
private:
    std::string instrument_;
    double initialEquity_, multiplier_;
    std::size_t maxEventIds_;
    CostBasis costBasis_;
    ResearchPriceDomain priceDomain_;
    struct Lot { std::int64_t quantity; detail::ResearchAccumulator price; };
    // Quantity-compressed lots, not one allocation per contract. FIFO updates
    // stage the active lots before committing; cost is O(active lots), not O(q).
    std::deque<Lot> lots_;
    std::int64_t quantity_ = 0, lastTimestampUs_ = 0;
    detail::ResearchAccumulator average_ = 0, realized_ = 0, fees_ = 0;
    std::map<std::string, ResearchFill> fills_;
    std::map<std::string, ResearchSettlement> settlements_;
};

struct ResearchInstrument {
    std::string instrument, currency;
    double multiplier = 0;
    CostBasis costBasis = CostBasis::WeightedAverage;
    ResearchPriceDomain priceDomain = ResearchPriceDomain::Positive;
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

// Explicit partial valuation for OFFLINE consumers. When complete is false,
// snapshot.equity/unrealized and grossNotional are UNDEFINED (stored as zero),
// not zero-valued assets. Serialize them as null, never as a risk approval.
// Cash is the model's shared cash/basis identity, not a broker cash balance.
struct ResearchPortfolioValuation {
    ResearchPortfolioSnapshot snapshot;
    bool complete = true;
    double cash = 0, grossNotional = 0;
    std::vector<std::string> missingMarks, staleMarks;
    // Includes flat instruments with no observed quote; zero is a valid signed price.
    std::vector<std::string> unobservedMarks;
};

// Thread-affine OFFLINE portfolio. A fixed same-currency universe prevents
// implicit FX conversions. One monotonic delivery clock spans fills, flows,
// settlements and ticks (equal timestamps retain caller delivery order). Fill identities
// are portfolio-global; flow identities have their own namespace. Exact retries
// do not advance time, consume capacity or revalidate a stale mark.
//
// Every position-changing fill invalidates that instrument's cached valuation.
// An OPEN position requires a subsequent new-sequence tick and an explicit
// caller-supplied maximum age; the fill price is not substituted for a quote.
// Snapshot is read-only, not a watermark or a historical/bitemporal query.
// Zero/negative equity is reported, never treated as permission to trade.
// Explicit variation settlement only; no margin, FX, venue clearing calendar,
// persistence or execution authority. Settlement never refreshes a quote.
class ResearchPortfolio {
public:
    ResearchPortfolio(double initialEquity, std::string currency,
                      const std::vector<ResearchInstrument>& instruments,
                      std::size_t maxEventIds = 100000);
    bool Apply(const ResearchFill& fill);
    // Settlement IDs are portfolio-global and separate from fill/flow IDs, but
    // share their finite event budget. Existing mark age/validity is unchanged.
    bool Settle(const ResearchSettlement& settlement);
    bool ApplyCashFlow(const ResearchCashFlow& flow);
    bool Observe(const Tick& tick);
    ResearchPortfolioSnapshot Snapshot(std::int64_t asOfUs,
                                       std::int64_t maxMarkAgeUs) const;
    // Read-only partial alternative; strict Snapshot keeps its original errors.
    ResearchPortfolioValuation Valuation(std::int64_t asOfUs,
                                         std::int64_t maxMarkAgeUs) const;
private:
    ResearchPortfolioValuation Value(std::int64_t asOfUs, std::int64_t maxMarkAgeUs,
                                    bool strict) const;
    struct Position {
        Position(const ResearchInstrument& spec, double initial, std::size_t capacity)
            : ledger(spec.instrument, initial, spec.multiplier, capacity, spec.costBasis, spec.priceDomain), multiplier(spec.multiplier) {}
        ResearchLedger ledger;
        double multiplier;
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
    std::map<std::string, ResearchSettlement> settlements_;
    std::map<std::string, ResearchCashFlow> cashFlows_;
};

}} // namespace hepta::research
