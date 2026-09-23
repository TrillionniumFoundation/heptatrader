#pragma once
#include "analytics.h"
#include "market_data.h"
#include <cstddef>
#include <map>
#include <string>
#include <vector>

namespace hepta { namespace research {

// Optional, immutable last-trade fill policy. tickSize==0 preserves the original
// ungridded/no-slippage model. A positive grid uses the explicitly named bounded
// binary64-nearest convention (8 epsilon units, |index| <= 2^40), as in #108.
// It is not a decimal/ABI equivalence claim. Slippage is adverse and limit-aware.
struct ReplayExecutionPolicy {
    double tickSize = 0;
    std::int64_t slippageTicks = 0;
    ResearchPriceDomain priceDomain = ResearchPriceDomain::Positive;
};

// Explicit integer-index input for distinct OFFLINE models. Conversion is bounded
// and checked; this is not the legacy signed-64/Decimal domain at all magnitudes.
class ResearchPriceGrid {
public:
    ResearchPriceGrid(double tickSize, ResearchPriceDomain domain);
    double Price(std::int64_t ticks) const;
    std::int64_t Index(double price) const;
private:
    double tickSize_;
    ResearchPriceDomain domain_;
};

// OFFLINE inference from two consecutive cumulative volume/turnover observations
// and the PREVIOUS observed top of book. The result is inferred, never an
// observed trade tape or authoritative exchange event. This intentionally
// supports only a one-tick previous spread, where bid/ask quantities are
// algebraically unique. Wider spreads and inconsistent evidence fail closed.
struct CumulativeTradeObservation {
    std::string instrument;
    std::int64_t timestampUs = 0;
    std::uint64_t sequence = 0;
    std::int64_t cumulativeVolume = 0;
    double cumulativeTurnover = 0;
    double lastPrice = 0;
    double bestBidPrice = 0;
    double bestAskPrice = 0;
};
struct InferredTradeLevel {
    std::int64_t priceTicks = 0;
    double price = 0;
    std::int64_t quantity = 0;
};
struct CumulativeTradeInferenceResult {
    bool inferred = false;
    std::uint64_t fromSequence = 0, toSequence = 0;
    std::int64_t timestampUs = 0;
    std::int64_t deltaVolume = 0;
    double deltaTurnover = 0;
    std::int64_t inferredBuyVolume = 0, inferredSellVolume = 0;
    std::vector<InferredTradeLevel> levels;
};
class CumulativeTopOfBookTradeInference {
public:
    CumulativeTopOfBookTradeInference(std::string instrument, double tickSize,
        double multiplier, ResearchPriceDomain domain = ResearchPriceDomain::Positive,
        std::size_t maxObservations = 100000);
    // The first observation establishes a baseline and returns false. Exact
    // retries also return false. In both cases output is unchanged.
    bool Observe(const CumulativeTradeObservation& observation,
                 CumulativeTradeInferenceResult& output);
    std::size_t Observations() const { return receipts_.size(); }
    std::int64_t ClockUs() const { return clockUs_; }
private:
    std::string instrument_;
    ResearchPriceGrid grid_;
    double tickSize_, multiplier_;
    std::size_t maxObservations_;
    bool initialized_ = false;
    std::uint64_t lastSequence_ = 0;
    std::int64_t clockUs_ = 0;
    CumulativeTradeObservation last_;
    std::map<std::uint64_t, CumulativeTradeObservation> receipts_;
};

enum class ReplayTimeInForce { Day, ImmediateOrCancel, FillOrKill };
struct ReplayOrder {
    std::string orderId, instrument, tradingDay;
    std::int64_t submittedAtUs = 0, expiresAtUs = 0;
    int side = 0;
    std::int64_t quantity = 0;
    double limitPrice = 0; // Required; market orders are deliberately unsupported.
    ReplayTimeInForce timeInForce = ReplayTimeInForce::Day;
};
enum class ReplayEventKind { Fill, Cancelled, Expired };
struct ReplayEvent {
    ReplayEventKind kind = ReplayEventKind::Cancelled;
    std::string orderId;
    std::int64_t remaining = 0;
    ResearchFill fill;
};
// Offline last-trade liquidity model, NOT an exchange queue-position model.
// A signal never fills on its own timestamp. Available incremental volume is
// shared across orders in submission order. No invented liquidity or fills.
// All new input shares a monotonic clock, including submissions. Exact retries
// do not advance that clock or revive terminal orders. Thread-affine.
class ReplayMatcher {
public:
    ReplayMatcher(std::string instrument, SessionSchedule schedule,
                  double feePerUnit = 0, std::size_t maxOrderIds = 100000);
    ReplayMatcher(std::string instrument, SessionSchedule schedule,
                  double feePerUnit, std::size_t maxOrderIds, ReplayExecutionPolicy policy);
    bool Submit(const ReplayOrder& order); // Exact duplicate is idempotent.
    std::vector<ReplayEvent> OnTick(const Tick& tick);
    std::vector<ReplayEvent> Cancel(const std::string& orderId);
    // Expire without requiring a new market tick (including session breaks).
    // Older input is forbidden afterward. No synthetic ticks or liquidity.
    std::vector<ReplayEvent> AdvanceWatermark(std::int64_t timestampUs);
    // End a run: expire due orders and cancel every other remainder, without
    // closing positions at an invented price. Repeating the same end is a no-op.
    // Different end times or new submissions/ticks after finalization fail.
    std::vector<ReplayEvent> Finish(std::int64_t timestampUs);
    bool Finished() const { return finished_; }
    std::int64_t ClockUs() const { return clockUs_; }
    std::size_t ActiveOrders() const { return pending_.size(); }
private:
    struct Pending {
        ReplayOrder order;
        std::int64_t remaining = 0, effectiveExpiryUs = 0;
        std::uint64_t fills = 0;
    };
    std::vector<ReplayEvent> Advance(std::int64_t timestampUs, bool finish);
    std::string instrument_;
    SessionSchedule schedule_;
    double feePerUnit_;
    ReplayExecutionPolicy policy_;
    std::size_t maxOrderIds_;
    bool hasTick_ = false, finished_ = false;
    std::int64_t clockUs_ = 0;
    Tick last_;
    std::vector<Pending> pending_;
    std::map<std::string, ReplayOrder> identities_;
};

// Distinct explicit-order-flow model. No tick/depth snapshot is converted to
// fictional external liquidity. It shares ResearchPortfolio/ResearchLedger with
// every other research consumer and has no broker/OMS/transport dependency.
enum class FlowActor { External, Research };
enum class FlowTimeInForce { Gtc, Day, Ioc, Fok };
enum class FlowEventKind { Add, Cancel, SessionEnd, Mark, BasisRebase };
struct FlowInstrument {
    ResearchInstrument account;
    double tickSize = 0, feePerUnit = 0, feeRate = 0;
    std::int64_t lot = 1;
};
struct FlowEvent {
    FlowEventKind kind = FlowEventKind::Add;
    std::uint64_t sequence = 0;
    std::int64_t timestampUs = 0;
    std::string instrument, orderId;
    FlowActor actor = FlowActor::External;
    FlowTimeInForce timeInForce = FlowTimeInForce::Gtc;
    int side = 0;
    std::int64_t quantity = 0, priceTicks = 0;
    bool hasLimit = true;
};
struct FlowOrderState {
    FlowEvent submitted;
    std::int64_t remaining = 0, filled = 0, cancelled = 0;
};
class OrderFlowReplay {
public:
    OrderFlowReplay(double initialEquity, std::string currency,
                    const std::vector<FlowInstrument>& instruments,
                    std::size_t maxEvents = 100000);
    // Global increasing sequence/nondecreasing time. An exact historical retry
    // is a no-op; a reused sequence with different bytes/fields is rejected.
    // Logical, numeric and allocation failure preserve the whole prior state.
    std::vector<ResearchFill> Consume(const FlowEvent& event);
    FlowOrderState Order(const std::string& id) const;
    ResearchPortfolioSnapshot Snapshot(std::int64_t asOfUs,
                                       std::int64_t maxMarkAgeUs) const;
    std::size_t ActiveOrders() const;
    std::int64_t ClockUs() const { return clockUs_; }
private:
    std::vector<ResearchFill> Process(const FlowEvent& event);
    std::vector<ResearchFill> Match(const FlowEvent& event);
    std::map<std::string, FlowInstrument> specs_;
    ResearchPortfolio account_;
    std::map<std::string, FlowOrderState> orders_;
    std::map<std::uint64_t, FlowEvent> receipts_;
    std::size_t maxEvents_;
    std::uint64_t lastSequence_ = 0;
    std::int64_t clockUs_ = 0;
};

// Explicit next-distinct-open hypothetical model, not last-trade liquidity.
// A target is a caller-supplied research observation of a completed bar. A fresh
// open strictly AFTER that observation may fill the close-first target delta.
// No inferred open, future close, implicit margin/funding or tick-volume claim.
struct NextBarTarget {
    std::string targetId;
    Bar sourceBar;
    std::int64_t observedAtUs = 0, targetQuantity = 0;
};
enum class NextOpenTiming { StrictlyLater, AfterClosePhase };
// Opt-in historical normalized-bar model. AfterClosePhase permits an OPEN
// delivered after its completed target at the SAME timestamp; it is an idealized
// offline convention, never a claim of zero-latency live execution.
struct NextBarPolicy {
    NextOpenTiming timing = NextOpenTiming::StrictlyLater;
    std::int64_t slippageTicks = 0;
    std::map<std::string, std::int64_t> instrumentSlippageTicks;
};
class NextBarReplay {
public:
    NextBarReplay(double initialEquity, std::string currency,
                  const std::vector<FlowInstrument>& instruments,
                  std::int64_t slippageTicks = 0,
                  std::size_t maxEvents = 100000);
    NextBarReplay(double initialEquity, std::string currency,
                  const std::vector<FlowInstrument>& instruments,
                  NextBarPolicy policy, std::size_t maxEvents = 100000);
    bool SetTarget(const NextBarTarget& target);
    // Open ticks are explicit input evidence, not derived from a future bar.
    // Volume is retained for identity but NOT used as a liquidity assertion.
    // Exact retry never revalidates a stale mark or submits the target twice.
    std::vector<ResearchFill> ObserveOpen(const Tick& open);
    // Explicit completed-close mark: refreshes valuation only, never consumes a
    // pending target. Shares account clock/sequence validation and event budget.
    bool ObserveMark(const Tick& mark);
    ResearchPortfolioValuation Valuation(std::int64_t asOfUs,
                                         std::int64_t maxMarkAgeUs) const;
    std::map<std::string, std::int64_t> PendingTargets() const;
    ResearchPortfolioSnapshot Snapshot(std::int64_t asOfUs,
                                       std::int64_t maxMarkAgeUs) const;
private:
    std::map<std::string, FlowInstrument> specs_;
    ResearchPortfolio account_;
    std::map<std::string, NextBarTarget> targetIds_, pending_;
    std::map<std::string, Tick> opens_;
    std::map<std::string, std::int64_t> quantities_;
    NextBarPolicy policy_;
    std::int64_t clockUs_ = 0;
    std::size_t maxEvents_, eventCount_ = 0;
};

}} // namespace hepta::research
