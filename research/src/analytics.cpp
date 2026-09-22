#include "hepta/research/analytics.h"
#include "hepta/research/market_data.h"
#include <algorithm>
#include <cmath>
#include <limits>
#include <stdexcept>
#include <utility>

namespace hepta { namespace research {
namespace {
using LedgerAccumulator = detail::ResearchAccumulator;
void Require(bool value, const char* reason) {
    if (!value) throw std::invalid_argument(reason);
}
bool ValidPrice(double value, ResearchPriceDomain domain) {
    return std::isfinite(value) &&
        (domain == ResearchPriceDomain::SignedFinite ||
         (domain == ResearchPriceDomain::Positive && value > 0));
}
double Finite(long double value) {
    if (!std::isfinite(value) || std::fabs(value) > std::numeric_limits<double>::max())
        throw std::overflow_error("RESEARCH_NUMERIC_OVERFLOW");
    return static_cast<double>(value);
}
#if LDBL_MANT_DIG < 64 || defined(HEPTA_RESEARCH_TEST_PORTABLE_ACCUMULATOR)
double Finite(const detail::ResearchWide& value) {
    const detail::ResearchWide limit(std::numeric_limits<double>::max());
    if (!value.IsFinite() || value > limit || value < -limit)
        throw std::overflow_error("RESEARCH_NUMERIC_OVERFLOW");
    return static_cast<double>(value);
}
#endif
Metric Optional(long double value) {
    Metric m;
    if (std::isfinite(value) && std::fabs(value) <= std::numeric_limits<double>::max()) {
        m.defined = true; m.value = static_cast<double>(value);
    }
    return m;
}
void CheckSettlementPrecision(LedgerAccumulator entry, double price, ResearchPriceDomain domain) {
    // Reject a destructive finite rebase rather than rounding distinct nearby
    // representable entry prices into the same value. This is a representation
    // check, not a hard-coded market price-band or a broker risk decision.
    const double rounded = Finite(entry);
    const double neighbors[] = {rounded, std::nextafter(rounded, 0.0),
        std::nextafter(rounded, rounded < 0 ? -std::numeric_limits<double>::infinity() :
                       std::numeric_limits<double>::infinity())};
    for (double sample : neighbors) {
        if (!std::isfinite(sample) ||
            (domain == ResearchPriceDomain::Positive && sample <= 0) ||
            (domain == ResearchPriceDomain::SignedFinite && rounded == 0 && sample != 0)) continue;
        const LedgerAccumulator delta = static_cast<LedgerAccumulator>(price) - sample;
        Require(static_cast<double>(static_cast<LedgerAccumulator>(price) - delta) == sample,
                "RESEARCH_SETTLEMENT_PRECISION_LOSS");
    }
}
bool SameSettlement(const ResearchSettlement& a, const ResearchSettlement& b) {
    return a.settlementId == b.settlementId && a.instrument == b.instrument &&
           a.timestampUs == b.timestampUs && a.price == b.price;
}
bool SameFill(const ResearchFill& a, const ResearchFill& b) {
    return a.fillId == b.fillId && a.orderId == b.orderId && a.instrument == b.instrument &&
           a.timestampUs == b.timestampUs && a.side == b.side && a.quantity == b.quantity &&
           a.price == b.price && a.fee == b.fee;
}
}
Performance EvaluateEquity(const std::vector<EquityPoint>& points,
                           double periodsPerYear, double riskFree) {
    Require(std::isfinite(periodsPerYear) && periodsPerYear > 0 &&
            std::isfinite(riskFree) && riskFree > -1, "RESEARCH_SAMPLING_OR_RATE_INVALID");
    Performance result;
    if (points.empty()) return result;
    for (std::size_t i = 0; i < points.size(); ++i) {
        const auto& p = points[i];
        Require(p.timestampUs >= 0 && std::isfinite(p.equity) && p.equity > 0 &&
                std::isfinite(p.externalFlow), "RESEARCH_EQUITY_INVALID");
        if (i) Require(p.timestampUs > points[i - 1].timestampUs, "RESEARCH_EQUITY_OUT_OF_ORDER");
    }
    Require(points.front().externalFlow == 0, "RESEARCH_INITIAL_FLOW_AMBIGUOUS");
    if (points.size() == 1) return result;
    result.returnCount = points.size() - 1;
    const long double periodRiskFree = std::expm1(std::log1p(static_cast<long double>(riskFree)) / periodsPerYear);
    long double mean = 0, m2 = 0, downside = 0, logNav = 0, logHigh = 0;
    for (std::size_t i = 1; i < points.size(); ++i) {
        const long double preFlow = static_cast<long double>(points[i].equity) - points[i].externalFlow;
        Require(preFlow > 0, "RESEARCH_NONPOSITIVE_PREFLOW_CAPITAL");
        const long double ratio = preFlow / points[i - 1].equity;
        const long double ret = ratio - 1;
        Finite(ret);
        const long double delta = ret - mean;
        mean += delta / i;
        m2 += delta * (ret - mean);
        const long double below = std::min(0.0L, ret - periodRiskFree);
        downside += below * below;
        logNav += std::log(ratio);
        logHigh = std::max(logHigh, logNav);
        result.maxDrawdown = std::max(result.maxDrawdown, Finite(-std::expm1(logNav - logHigh)));
    }
    result.totalReturn = Finite(std::expm1(logNav));
    result.annualizedReturn = Optional(std::expm1(logNav * periodsPerYear / result.returnCount));
    if (result.returnCount > 1) {
        const long double vol = std::sqrt(std::max(0.0L, m2) / (result.returnCount - 1) * periodsPerYear);
        result.annualizedVolatility = Optional(vol);
        if (vol > 0) result.sharpe = Optional((mean - periodRiskFree) * periodsPerYear / vol);
    }
    const long double down = std::sqrt(downside / result.returnCount * periodsPerYear);
    if (down > 0) result.sortino = Optional((mean - periodRiskFree) * periodsPerYear / down);
    if (result.maxDrawdown > 0 && result.annualizedReturn.defined)
        result.calmar = Optional(result.annualizedReturn.value / result.maxDrawdown);
    return result;
}
ResearchLedger::ResearchLedger(std::string instrument, double initial, double multiplier,
                               std::size_t maxFillIds, CostBasis costBasis, ResearchPriceDomain priceDomain)
    : instrument_(std::move(instrument)), initialEquity_(initial), multiplier_(multiplier), maxEventIds_(maxFillIds), costBasis_(costBasis), priceDomain_(priceDomain) {
    Tick validation; validation.instrument = instrument_; validation.sequence = 1; validation.price = 1;
    ValidateTick(validation);
    Require(std::isfinite(initial) && initial > 0 && std::isfinite(multiplier) && multiplier > 0 &&
            (priceDomain == ResearchPriceDomain::Positive || priceDomain == ResearchPriceDomain::SignedFinite) &&
            maxFillIds > 0 && (costBasis == CostBasis::WeightedAverage || costBasis == CostBasis::Fifo),
            "RESEARCH_LEDGER_CONFIG_INVALID");
}
bool ResearchLedger::Apply(const ResearchFill& fill) {
    Require(!fill.fillId.empty() && fill.fillId.size() <= 128 && !fill.orderId.empty() &&
            fill.orderId.size() <= 128 && fill.instrument == instrument_ &&
            fill.timestampUs >= 0 && (fill.side == 1 || fill.side == -1) &&
            fill.quantity > 0 && fill.quantity <= 1000000000000LL &&
            ValidPrice(fill.price, priceDomain_) && std::isfinite(fill.fee) && fill.fee >= 0,
            "RESEARCH_FILL_INVALID");
    const auto found = fills_.find(fill.fillId);
    if (found != fills_.end()) {
        Require(SameFill(found->second, fill), "RESEARCH_FILL_ID_CONFLICT");
        return false;
    }
    Require(fill.timestampUs >= lastTimestampUs_, "RESEARCH_FILL_OUT_OF_ORDER");
    Require(settlements_.size() < maxEventIds_ &&
            fills_.size() < maxEventIds_ - settlements_.size(), "RESEARCH_FILL_ID_CAPACITY");
    const std::int64_t signedFill = fill.side * fill.quantity;
    const std::int64_t nextQuantity = quantity_ + signedFill; // Both are bounded to 1e12.
    Require(nextQuantity >= -1000000000000LL && nextQuantity <= 1000000000000LL,
            "RESEARCH_POSITION_CAPACITY");
    const std::int64_t oldAbs = quantity_ < 0 ? -quantity_ : quantity_;
    const bool sameDirection = quantity_ == 0 || (quantity_ > 0) == (signedFill > 0);
    LedgerAccumulator average = average_, realized = realized_, fees = fees_ + fill.fee;
    std::deque<Lot> nextLots;
    if (costBasis_ == CostBasis::Fifo) {
        nextLots = lots_; // Allocation and numeric failure leave live lots intact.
        std::int64_t remaining = fill.quantity;
        if (!sameDirection) {
            while (remaining > 0 && !nextLots.empty()) {
                Lot& lot = nextLots.front();
                const auto closed = std::min(remaining, lot.quantity);
                realized += (static_cast<LedgerAccumulator>(fill.price) - lot.price) *
                            (quantity_ > 0 ? 1 : -1) * closed * multiplier_;
                remaining -= closed; lot.quantity -= closed;
                if (lot.quantity == 0) nextLots.pop_front();
            }
        }
        if (remaining > 0) {
            if (!nextLots.empty() && nextLots.back().price == fill.price)
                nextLots.back().quantity += remaining;
            else nextLots.push_back(Lot{remaining, fill.price});
        }
        std::int64_t count = 0;
        average = 0;
        for (const auto& lot : nextLots) {
            const auto total = count + lot.quantity; // Bounded by position cap.
            average = count == 0 ? lot.price :
                average + (lot.price - average) * (static_cast<LedgerAccumulator>(lot.quantity) / total);
            count = total;
        }
    } else if (sameDirection) {
        // Interpolate within the two finite prices instead of
        // forming price*quantity sums. Identical fills preserve their exact
        // cost, even at DBL_MAX; true realized-P&L/fee overflow still rejects.
        const LedgerAccumulator weight = static_cast<LedgerAccumulator>(fill.quantity) /
                                   (oldAbs + fill.quantity);
        average = oldAbs == 0 ? static_cast<LedgerAccumulator>(fill.price) :
            average_ + (static_cast<LedgerAccumulator>(fill.price) - average_) * weight;
    } else {
        const auto closeQuantity = std::min(oldAbs, fill.quantity);
        realized += (static_cast<LedgerAccumulator>(fill.price) - average_) *
                    (quantity_ > 0 ? 1 : -1) * closeQuantity * multiplier_;
        if (nextQuantity == 0) average = 0;
        else if ((nextQuantity > 0) != (quantity_ > 0)) average = fill.price;
    }
    Finite(average); Finite(realized); Finite(fees);
    Finite(static_cast<LedgerAccumulator>(initialEquity_) + realized - fees);
    fills_.emplace(fill.fillId, fill);
    if (costBasis_ == CostBasis::Fifo) lots_.swap(nextLots);
    quantity_ = nextQuantity; average_ = average; realized_ = realized; fees_ = fees;
    lastTimestampUs_ = fill.timestampUs;
    return true;
}
bool ResearchLedger::Settle(const ResearchSettlement& settlement) {
    Require(!settlement.settlementId.empty() && settlement.settlementId.size() <= 128 &&
            settlement.instrument == instrument_ && settlement.timestampUs >= 0 &&
            ValidPrice(settlement.price, priceDomain_),
            "RESEARCH_SETTLEMENT_INVALID");
    const auto found = settlements_.find(settlement.settlementId);
    if (found != settlements_.end()) {
        Require(SameSettlement(found->second, settlement), "RESEARCH_SETTLEMENT_ID_CONFLICT");
        return false;
    }
    Require(settlement.timestampUs >= lastTimestampUs_, "RESEARCH_SETTLEMENT_OUT_OF_ORDER");
    Require(settlements_.size() < maxEventIds_ &&
            fills_.size() < maxEventIds_ - settlements_.size(), "RESEARCH_SETTLEMENT_ID_CAPACITY");
    // Stage both accounting and allocations before publishing the receipt. In
    // FIFO, realize each old lot, then combine equal rebased lots; never use the
    // rounded public average to compute settlement P&L.
    LedgerAccumulator variation = 0;
    std::deque<Lot> nextLots;
    if (costBasis_ == CostBasis::Fifo) {
        for (const auto& lot : lots_)
            variation += (static_cast<LedgerAccumulator>(settlement.price) - lot.price) *
                         lot.quantity * (quantity_ > 0 ? 1 : -1) * multiplier_;
        if (quantity_ != 0)
            nextLots.push_back(Lot{quantity_ > 0 ? quantity_ : -quantity_, settlement.price});
    } else {
        variation = (static_cast<LedgerAccumulator>(settlement.price) - average_) * quantity_ * multiplier_;
    }
    const LedgerAccumulator realized = realized_ + variation;
    Finite(variation); Finite(realized);
    if (quantity_ != 0) {
        if (costBasis_ == CostBasis::Fifo) {
            for (const auto& lot : lots_) CheckSettlementPrecision(lot.price, settlement.price, priceDomain_);
        } else CheckSettlementPrecision(average_, settlement.price, priceDomain_);
    }
    Require(static_cast<double>(realized - variation) == static_cast<double>(realized_),
            "RESEARCH_SETTLEMENT_PRECISION_LOSS");
    Finite(static_cast<LedgerAccumulator>(initialEquity_) + realized - fees_);
    settlements_.emplace(settlement.settlementId, settlement);
    if (costBasis_ == CostBasis::Fifo) lots_.swap(nextLots);
    realized_ = realized;
    average_ = quantity_ == 0 ? 0 : settlement.price;
    lastTimestampUs_ = settlement.timestampUs;
    return true;
}
ResearchAccountState ResearchLedger::State() const {
    ResearchAccountState out;
    out.quantity = quantity_; out.averageEntry = Finite(average_);
    out.realizedGross = Finite(realized_); out.fees = Finite(fees_);
    return out;
}
ResearchAccount ResearchLedger::Mark(double mark) const {
    Require(ValidPrice(mark, priceDomain_), "RESEARCH_MARK_INVALID");
    ResearchAccount out;
    out.quantity = quantity_; out.averageEntry = Finite(average_);
    out.realizedGross = Finite(realized_); out.fees = Finite(fees_);
    LedgerAccumulator unrealized = 0;
    if (costBasis_ == CostBasis::Fifo) {
        // Mark each compressed lot, preserving exact zero P&L for equal prices.
        for (const auto& lot : lots_)
            unrealized += (static_cast<LedgerAccumulator>(mark) - lot.price) * lot.quantity *
                          (quantity_ > 0 ? 1 : -1) * multiplier_;
    } else unrealized = (static_cast<LedgerAccumulator>(mark) - average_) * quantity_ * multiplier_;
    out.unrealized = Finite(unrealized);
    out.equity = Finite(static_cast<LedgerAccumulator>(initialEquity_) + realized_ + unrealized - fees_);
    return out;
}
ResearchPortfolio::ResearchPortfolio(double initial, std::string currency,
        const std::vector<ResearchInstrument>& instruments, std::size_t maxEventIds)
    : initialEquity_(initial), currency_(std::move(currency)), maxEventIds_(maxEventIds) {
    Require(std::isfinite(initial) && initial > 0 && maxEventIds > 0 &&
            !instruments.empty() && instruments.size() <= 1024 &&
            currency_.size() == 3, "RESEARCH_PORTFOLIO_CONFIG_INVALID");
    for (char c : currency_)
        Require(c >= 'A' && c <= 'Z', "RESEARCH_PORTFOLIO_CURRENCY_INVALID");
    for (const auto& spec : instruments) {
        Require(spec.currency == currency_, "RESEARCH_PORTFOLIO_FX_UNSUPPORTED");
        Require(positions_.emplace(spec.instrument, Position(spec, initial, maxEventIds)).second,
                "RESEARCH_PORTFOLIO_DUPLICATE_INSTRUMENT");
    }
}
bool ResearchPortfolio::Apply(const ResearchFill& fill) {
    const auto duplicate = fills_.find(fill.fillId);
    if (duplicate != fills_.end()) {
        Require(SameFill(duplicate->second, fill), "RESEARCH_FILL_ID_CONFLICT");
        return false;
    }
    auto position = positions_.find(fill.instrument);
    Require(position != positions_.end(), "RESEARCH_PORTFOLIO_INSTRUMENT_UNKNOWN");
    Require(fill.timestampUs >= clockUs_, "RESEARCH_PORTFOLIO_CLOCK_REVERSED");
    Require(eventCount_ < maxEventIds_, "RESEARCH_PORTFOLIO_EVENT_CAPACITY");
    const auto receipt = fills_.emplace(fill.fillId, fill);
    try {
        // Ledger::Apply validates/stages before committing. A failed update
        // must not consume the portfolio receipt or invalidate the old mark.
        position->second.ledger.Apply(fill);
    } catch (...) {
        fills_.erase(receipt.first);
        throw;
    }
    position->second.markCurrent = false;
    clockUs_ = fill.timestampUs; ++eventCount_;
    return true;
}
bool ResearchPortfolio::Settle(const ResearchSettlement& settlement) {
    const auto duplicate = settlements_.find(settlement.settlementId);
    if (duplicate != settlements_.end()) {
        Require(SameSettlement(duplicate->second, settlement), "RESEARCH_SETTLEMENT_ID_CONFLICT");
        return false;
    }
    auto position = positions_.find(settlement.instrument);
    Require(position != positions_.end(), "RESEARCH_PORTFOLIO_INSTRUMENT_UNKNOWN");
    Require(settlement.timestampUs >= clockUs_, "RESEARCH_PORTFOLIO_CLOCK_REVERSED");
    Require(eventCount_ < maxEventIds_, "RESEARCH_PORTFOLIO_EVENT_CAPACITY");
    const auto receipt = settlements_.emplace(settlement.settlementId, settlement);
    try {
        position->second.ledger.Settle(settlement);
    } catch (...) {
        settlements_.erase(receipt.first);
        throw;
    }
    // A rebased cost does not supply a new market price, revive an invalidated
    // mark or extend its age. Snapshot still requires the ordinary fresh tick.
    clockUs_ = settlement.timestampUs; ++eventCount_;
    return true;
}
bool ResearchPortfolio::ApplyCashFlow(const ResearchCashFlow& flow) {
    Require(!flow.flowId.empty() && flow.flowId.size() <= 128 && flow.timestampUs >= 0 &&
            std::isfinite(flow.amount) && flow.amount != 0, "RESEARCH_CASH_FLOW_INVALID");
    const auto duplicate = cashFlows_.find(flow.flowId);
    if (duplicate != cashFlows_.end()) {
        const auto& old = duplicate->second;
        Require(old.timestampUs == flow.timestampUs && old.amount == flow.amount,
                "RESEARCH_CASH_FLOW_ID_CONFLICT");
        return false;
    }
    Require(flow.timestampUs >= clockUs_, "RESEARCH_PORTFOLIO_CLOCK_REVERSED");
    Require(eventCount_ < maxEventIds_, "RESEARCH_PORTFOLIO_EVENT_CAPACITY");
    const long double nextFlows = flows_ + flow.amount;
    Finite(nextFlows); Finite(static_cast<long double>(initialEquity_) + nextFlows);
    cashFlows_.emplace(flow.flowId, flow);
    flows_ = nextFlows; clockUs_ = flow.timestampUs; ++eventCount_;
    return true;
}
bool ResearchPortfolio::Observe(const Tick& tick) {
    auto found = positions_.find(tick.instrument);
    Require(found != positions_.end(), "RESEARCH_PORTFOLIO_INSTRUMENT_UNKNOWN");
    auto& position = found->second;
    Require(ValidPrice(tick.price, position.ledger.PriceDomain()), "RESEARCH_MARK_INVALID");
    // Reuse the canonical identity/time/volume/sequence validator. The research
    // domain is fixed by the instrument spec, never by a caller-owned tick flag.
    Tick structural = tick; structural.price = 1; ValidateTick(structural);
    if (position.hasTick && tick.sequence == position.lastTick.sequence) {
        const auto& old = position.lastTick;
        Require(old.timestampUs == tick.timestampUs && old.price == tick.price &&
                old.volume == tick.volume, "RESEARCH_SEQUENCE_CONFLICT");
        return false; // In particular, cannot revalidate a pre-fill observation.
    }
    Require(tick.timestampUs >= clockUs_ &&
            (!position.hasTick || tick.sequence > position.lastTick.sequence),
            "RESEARCH_PORTFOLIO_TICK_OUT_OF_ORDER");
    Tick staged = tick;
    using std::swap;
    swap(position.lastTick, staged);
    position.hasTick = position.markCurrent = true;
    clockUs_ = tick.timestampUs;
    return true;
}
ResearchPortfolioSnapshot ResearchPortfolio::Snapshot(std::int64_t asOf,
                                                       std::int64_t maxAge) const {
    return Value(asOf, maxAge, true).snapshot;
}
ResearchPortfolioValuation ResearchPortfolio::Valuation(std::int64_t asOf,
                                                        std::int64_t maxAge) const {
    return Value(asOf, maxAge, false);
}
ResearchPortfolioValuation ResearchPortfolio::Value(std::int64_t asOf,
        std::int64_t maxAge, bool strict) const {
    Require(asOf >= clockUs_ && maxAge >= 0, "RESEARCH_PORTFOLIO_SNAPSHOT_TIME_INVALID");
    ResearchPortfolioValuation result;
    auto& out = result.snapshot;
    out.currency = currency_; out.timestampUs = asOf; out.initialEquity = initialEquity_;
    out.externalFlows = Finite(flows_);
    long double realized = 0, unrealized = 0, fees = 0, basis = 0, gross = 0;
    for (const auto& item : positions_) {
        const auto& position = item.second;
        const bool open = position.ledger.Quantity() != 0;
        const bool missing = open && (!position.hasTick || !position.markCurrent);
        const bool stale = open && !missing && asOf - position.lastTick.timestampUs > maxAge;
        if (strict) {
            Require(!missing, "RESEARCH_PORTFOLIO_MARK_MISSING");
            Require(!stale, "RESEARCH_PORTFOLIO_MARK_STALE");
        }
        if (!position.hasTick) result.unobservedMarks.push_back(item.first);
        if (missing) result.missingMarks.push_back(item.first);
        if (stale) result.staleMarks.push_back(item.first);
        const auto state = position.ledger.State();
        ResearchPositionSnapshot value;
        value.quantity = state.quantity; value.averageEntry = state.averageEntry;
        value.realizedGross = state.realizedGross; value.fees = state.fees;
        if (!missing && !stale) {
            const auto account = position.ledger.Mark(open ? position.lastTick.price : 1.0);
            value.unrealized = account.unrealized; unrealized += account.unrealized;
        } else result.complete = false;
        if (position.hasTick && (open || !strict)) {
            value.markPrice = position.lastTick.price; value.markTimestampUs = position.lastTick.timestampUs;
        }
        out.positions.emplace(item.first, value);
        realized += state.realizedGross; fees += state.fees;
        // Partial reporting is opt-in: do not add new overflow conditions to
        // the existing strict Snapshot/SDK contract.
        if (!strict) {
            basis += static_cast<long double>(state.quantity) * state.averageEntry * position.multiplier;
            if (open && !missing && !stale)
                gross += std::fabs(static_cast<long double>(state.quantity) * position.lastTick.price * position.multiplier);
        }
    }
    out.realizedGross = Finite(realized); out.fees = Finite(fees);
    if (result.complete) {
        out.unrealized = Finite(unrealized);
        out.equity = Finite(static_cast<long double>(initialEquity_) + flows_ + realized + unrealized - fees);
        if (!strict) result.grossNotional = Finite(gross);
    }
    if (!strict) result.cash = Finite(static_cast<long double>(initialEquity_) + flows_ + realized - fees - basis);
    return result;
}
}} // namespace hepta::research
