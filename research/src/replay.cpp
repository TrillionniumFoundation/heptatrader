#include "hepta/research/replay.h"
#include <algorithm>
#include <cmath>
#include <limits>
#include <stdexcept>
#include <utility>

namespace hepta { namespace research {
namespace {
void Require(bool value, const char* why) { if (!value) throw std::invalid_argument(why); }
bool ModelPrice(double price, ResearchPriceDomain domain) {
    return std::isfinite(price) && (domain == ResearchPriceDomain::SignedFinite ||
        (domain == ResearchPriceDomain::Positive && price > 0));
}
void ModelTick(const Tick& tick, ResearchPriceDomain domain) {
    Require(ModelPrice(tick.price, domain), "RESEARCH_MODEL_PRICE_INVALID");
    Tick structural = tick; structural.price = 1; ValidateTick(structural);
}
double ModelFinite(long double value) {
    Require(std::isfinite(value) && std::fabs(value) <= std::numeric_limits<double>::max(),
            "RESEARCH_MODEL_NUMERIC_OVERFLOW");
    return static_cast<double>(value);
}
bool SameOrder(const ReplayOrder& a, const ReplayOrder& b) {
    return a.orderId == b.orderId && a.instrument == b.instrument && a.tradingDay == b.tradingDay &&
        a.submittedAtUs == b.submittedAtUs && a.expiresAtUs == b.expiresAtUs && a.side == b.side &&
        a.quantity == b.quantity && a.limitPrice == b.limitPrice && a.timeInForce == b.timeInForce;
}
bool SameInferenceObservation(const CumulativeTradeObservation& a,
                              const CumulativeTradeObservation& b) {
    return a.instrument == b.instrument && a.timestampUs == b.timestampUs &&
        a.sequence == b.sequence && a.cumulativeVolume == b.cumulativeVolume &&
        a.cumulativeTurnover == b.cumulativeTurnover && a.lastPrice == b.lastPrice &&
        a.bestBidPrice == b.bestBidPrice && a.bestAskPrice == b.bestAskPrice;
}
long double InferenceTolerance(long double a, long double b) {
    const long double scale = std::max(1.0L, std::max(std::fabs(a), std::fabs(b)));
    // This is an inference-admission tolerance, not a rounding license. If
    // cumulative-double subtraction has lost more than one millionth of one
    // tick-notional unit, the evidence is too ambiguous for this model.
    return std::min(1.0e-6L,
        64.0L * std::numeric_limits<double>::epsilon() * scale);
}
}
ReplayMatcher::ReplayMatcher(std::string instrument, SessionSchedule schedule, double fee, std::size_t capacity)
    : ReplayMatcher(std::move(instrument), std::move(schedule), fee, capacity, ReplayExecutionPolicy()) {}
ReplayMatcher::ReplayMatcher(std::string instrument, SessionSchedule schedule, double fee,
                             std::size_t capacity, ReplayExecutionPolicy policy)
    : instrument_(std::move(instrument)), schedule_(std::move(schedule)), feePerUnit_(fee),
      policy_(policy), maxOrderIds_(capacity) {
    Require((policy.priceDomain == ResearchPriceDomain::Positive ||
             policy.priceDomain == ResearchPriceDomain::SignedFinite) &&
            std::isfinite(policy.tickSize) && policy.tickSize >= 0 &&
            policy.slippageTicks >= 0 && policy.slippageTicks <= 1000000 &&
            (policy.tickSize > 0 || policy.slippageTicks == 0), "RESEARCH_REPLAY_POLICY_INVALID");
    Tick v; v.instrument = instrument_; v.sequence = 1; v.price = 1; ValidateTick(v);
    Require(std::isfinite(fee) && fee >= 0 && capacity > 0, "RESEARCH_REPLAY_CONFIG_INVALID");
}
bool ReplayMatcher::Submit(const ReplayOrder& order) {
    Require(!order.orderId.empty() && order.orderId.size() <= 96 && order.instrument == instrument_ &&
            (order.side == 1 || order.side == -1) && order.quantity > 0 && order.quantity <= 1000000000000LL &&
            ModelPrice(order.limitPrice, policy_.priceDomain) && order.submittedAtUs >= 0 &&
            order.expiresAtUs > order.submittedAtUs, "RESEARCH_REPLAY_ORDER_INVALID");
    Require(order.timeInForce == ReplayTimeInForce::Day || order.timeInForce == ReplayTimeInForce::ImmediateOrCancel ||
            order.timeInForce == ReplayTimeInForce::FillOrKill, "RESEARCH_REPLAY_TIF_INVALID");
    if (policy_.tickSize > 0) ResearchPriceGrid(policy_.tickSize, policy_.priceDomain).Index(order.limitPrice);
    const auto found = identities_.find(order.orderId);
    if (found != identities_.end()) {
        Require(SameOrder(found->second, order), "RESEARCH_ORDER_ID_CONFLICT");
        return false;
    }
    Require(!finished_, "RESEARCH_REPLAY_FINISHED");
    Require(order.submittedAtUs >= clockUs_, "RESEARCH_ORDER_IN_PAST");
    Require(schedule_.At(order.submittedAtUs).tradingDay == order.tradingDay, "RESEARCH_ORDER_DAY_MISMATCH");
    Require(identities_.size() < maxOrderIds_, "RESEARCH_ORDER_ID_CAPACITY");
    auto next = pending_;
    Pending p; p.order = order; p.remaining = order.quantity;
    // Day orders expire at the supplied day's FINAL session close, not at a
    // midday break and not on the next day's first tick. Earlier TTL still wins.
    p.effectiveExpiryUs = std::min(order.expiresAtUs, schedule_.Day(order.tradingDay).closeUs);
    next.push_back(p);
    identities_.emplace(order.orderId, order); pending_.swap(next);
    clockUs_ = order.submittedAtUs;
    return true;
}
std::vector<ReplayEvent> ReplayMatcher::OnTick(const Tick& tick) {
    Require(!finished_, "RESEARCH_REPLAY_FINISHED");
    ModelTick(tick, policy_.priceDomain); Require(tick.instrument == instrument_, "RESEARCH_INSTRUMENT_MISMATCH");
    if (hasTick_ && tick.sequence == last_.sequence) {
        Require(tick.timestampUs == last_.timestampUs && tick.price == last_.price && tick.volume == last_.volume,
                "RESEARCH_SEQUENCE_CONFLICT");
        return {};
    }
    Require(tick.timestampUs >= clockUs_ && (!hasTick_ || tick.sequence > last_.sequence),
            "RESEARCH_REPLAY_TICK_OUT_OF_ORDER");
    std::int64_t marketIndex = 0;
    if (policy_.tickSize > 0) marketIndex = ResearchPriceGrid(policy_.tickSize, policy_.priceDomain).Index(tick.price);
    const auto& session = schedule_.At(tick.timestampUs);
    // Allocate/copy before committing any mutable state.
    Tick nextLast = tick;
    auto next = pending_;
    std::vector<ReplayEvent> events;
    auto available = tick.volume;
    for (auto& p : next) {
        const auto& o = p.order;
        if (tick.timestampUs >= p.effectiveExpiryUs || session.tradingDay > o.tradingDay) {
            ReplayEvent e; e.kind = ReplayEventKind::Expired; e.orderId = o.orderId; e.remaining = p.remaining;
            events.push_back(e); p.remaining = 0; continue;
        }
        // Strict causality, including bars/signals produced at the same timestamp.
        if (tick.timestampUs <= o.submittedAtUs || session.tradingDay < o.tradingDay) continue;
        double fillPrice = tick.price;
        bool crosses = o.side == 1 ? tick.price <= o.limitPrice : tick.price >= o.limitPrice;
        if (policy_.tickSize > 0) {
            const ResearchPriceGrid grid(policy_.tickSize, policy_.priceDomain);
            const auto slipped = marketIndex + o.side * policy_.slippageTicks;
            const auto limit = grid.Index(o.limitPrice);
            crosses = (policy_.priceDomain == ResearchPriceDomain::SignedFinite || slipped > 0) &&
                (o.side == 1 ? slipped <= limit : slipped >= limit);
            if (crosses) fillPrice = grid.Price(slipped);
        }
        const bool enough = o.timeInForce != ReplayTimeInForce::FillOrKill || available >= p.remaining;
        const auto filled = crosses && enough ? std::min(p.remaining, available) : 0;
        if (filled > 0) {
            Require(p.fills < std::numeric_limits<std::uint64_t>::max(), "RESEARCH_FILL_SEQUENCE_OVERFLOW");
            ReplayEvent e; e.kind = ReplayEventKind::Fill; e.orderId = o.orderId;
            e.fill.fillId = o.orderId + ":" + std::to_string(++p.fills);
            e.fill.orderId = o.orderId; e.fill.instrument = instrument_; e.fill.timestampUs = tick.timestampUs;
            e.fill.side = o.side; e.fill.quantity = filled; e.fill.price = fillPrice;
            e.fill.fee = feePerUnit_ * filled;
            Require(std::isfinite(e.fill.fee), "RESEARCH_REPLAY_FEE_OVERFLOW");
            p.remaining -= filled; available -= filled; e.remaining = p.remaining; events.push_back(e);
        }
        if (p.remaining > 0 && o.timeInForce != ReplayTimeInForce::Day) {
            ReplayEvent e; e.kind = ReplayEventKind::Cancelled; e.orderId = o.orderId; e.remaining = p.remaining;
            events.push_back(e); p.remaining = 0;
        }
    }
    next.erase(std::remove_if(next.begin(), next.end(), [](const Pending& p) { return p.remaining == 0; }), next.end());
    pending_.swap(next); last_ = std::move(nextLast); hasTick_ = true; clockUs_ = tick.timestampUs;
    return events;
}
std::vector<ReplayEvent> ReplayMatcher::Cancel(const std::string& id) {
    Require(identities_.count(id) != 0, "RESEARCH_ORDER_UNKNOWN");
    for (auto it = pending_.begin(); it != pending_.end(); ++it) if (it->order.orderId == id) {
        ReplayEvent e; e.kind = ReplayEventKind::Cancelled; e.orderId = id; e.remaining = it->remaining;
        std::vector<ReplayEvent> result(1, e); pending_.erase(it); return result;
    }
    return {};
}
std::vector<ReplayEvent> ReplayMatcher::Advance(std::int64_t time, bool finish) {
    Require(time >= clockUs_, "RESEARCH_REPLAY_CLOCK_REVERSED");
    if (finished_) {
        Require(finish && time == clockUs_, "RESEARCH_REPLAY_FINISHED");
        return {};
    }
    auto next = pending_;
    std::vector<ReplayEvent> events;
    for (auto& p : next) {
        const bool due = time >= p.effectiveExpiryUs;
        if (!due && !finish) continue;
        ReplayEvent event;
        event.kind = due ? ReplayEventKind::Expired : ReplayEventKind::Cancelled;
        event.orderId = p.order.orderId; event.remaining = p.remaining;
        events.push_back(event); p.remaining = 0;
    }
    next.erase(std::remove_if(next.begin(), next.end(), [](const Pending& p) { return p.remaining == 0; }), next.end());
    pending_.swap(next); clockUs_ = time; finished_ = finish;
    return events;
}
std::vector<ReplayEvent> ReplayMatcher::AdvanceWatermark(std::int64_t time) { return Advance(time, false); }
std::vector<ReplayEvent> ReplayMatcher::Finish(std::int64_t time) { return Advance(time, true); }


ResearchPriceGrid::ResearchPriceGrid(double size, ResearchPriceDomain domain)
    : tickSize_(size), domain_(domain) {
    Require(std::isfinite(size) && size > 0 &&
        (domain == ResearchPriceDomain::Positive || domain == ResearchPriceDomain::SignedFinite),
        "RESEARCH_GRID_CONFIG_INVALID");
}
double ResearchPriceGrid::Price(std::int64_t ticks) const {
    Require(ticks >= -1099511627776LL && ticks <= 1099511627776LL &&
        (domain_ == ResearchPriceDomain::SignedFinite || ticks > 0), "RESEARCH_GRID_INDEX_RANGE");
    const double value = ModelFinite(static_cast<long double>(ticks) * tickSize_);
    Require(ModelPrice(value, domain_) && (ticks == 0 || value != 0), "RESEARCH_GRID_PRICE_UNREPRESENTABLE");
    // The grid is injective at this index; never silently merge adjacent prices.
    Require(static_cast<double>(static_cast<long double>(ticks - 1) * tickSize_) != value &&
            static_cast<double>(static_cast<long double>(ticks + 1) * tickSize_) != value,
            "RESEARCH_GRID_PRICE_AMBIGUOUS");
    return value;
}
std::int64_t ResearchPriceGrid::Index(double price) const {
    Require(ModelPrice(price, domain_), "RESEARCH_GRID_PRICE_INVALID");
    const long double q = static_cast<long double>(price) / tickSize_;
    Require(std::isfinite(q) && std::fabs(q) <= 1099511627776.0L, "RESEARCH_GRID_INDEX_RANGE");
    const long double nearest = std::round(q);
    Require(std::fabs(q - nearest) <= 8 * std::numeric_limits<double>::epsilon() *
            std::max(1.0L, std::fabs(q)), "RESEARCH_GRID_OFF_TICK");
    const auto index = static_cast<std::int64_t>(nearest);
    Price(index); // Includes positive-domain and adjacent-representation checks.
    return index;
}

CumulativeTradeObservation LegacyCumulativeTradeObservation(
        const LegacyTickRecord& record, LegacyTickCsvLayout layout) {
    const auto top = DecodeLegacyTopOfBook(record, layout);
    CumulativeTradeObservation result;
    result.instrument = record.tick.instrument;
    result.timestampUs = record.tick.timestampUs;
    result.sequence = record.tick.sequence;
    result.cumulativeVolume = record.cumulativeVolume;
    result.cumulativeTurnover = record.turnover;
    result.lastPrice = record.tick.price;
    result.bestBidPrice = top.bestBidPrice;
    result.bestAskPrice = top.bestAskPrice;
    return result;
}

CumulativeTopOfBookTradeInference::CumulativeTopOfBookTradeInference(
        std::string instrument, double tickSize, double multiplier,
        ResearchPriceDomain domain, std::size_t maxObservations)
    : instrument_(std::move(instrument)), grid_(tickSize, domain),
      tickSize_(tickSize), multiplier_(multiplier), maxObservations_(maxObservations) {
    Require(domain == ResearchPriceDomain::Positive,
            "RESEARCH_TRADE_INFERENCE_SIGNED_DOMAIN_UNSUPPORTED");
    Tick identity; identity.instrument = instrument_; identity.sequence = 1;
    identity.price = grid_.Price(1); ValidateTick(identity);
    const long double unit = static_cast<long double>(tickSize_) * multiplier_;
    Require(std::isfinite(multiplier_) && multiplier_ > 0 &&
            std::isfinite(unit) && unit > 0 &&
            unit <= std::numeric_limits<double>::max() &&
            maxObservations_ > 0 && maxObservations_ <= 1000000,
            "RESEARCH_TRADE_INFERENCE_CONFIG_INVALID");
}
bool CumulativeTopOfBookTradeInference::Observe(
        const CumulativeTradeObservation& o, CumulativeTradeInferenceResult& output) {
    Require(o.instrument == instrument_ && o.sequence > 0 && o.timestampUs >= 0 &&
            o.cumulativeVolume >= 0 && std::isfinite(o.cumulativeTurnover) &&
            o.cumulativeTurnover >= 0 && std::isfinite(o.lastPrice) &&
            std::isfinite(o.bestBidPrice) && std::isfinite(o.bestAskPrice),
            "RESEARCH_TRADE_INFERENCE_OBSERVATION_INVALID");
    const auto bid = grid_.Index(o.bestBidPrice);
    const auto ask = grid_.Index(o.bestAskPrice);
    grid_.Index(o.lastPrice);
    Require(bid < ask, "RESEARCH_TRADE_INFERENCE_QUOTE_INVALID");

    const auto duplicate = receipts_.find(o.sequence);
    if (duplicate != receipts_.end()) {
        Require(SameInferenceObservation(duplicate->second, o),
                "RESEARCH_TRADE_INFERENCE_SEQUENCE_CONFLICT");
        return false;
    }
    Require(receipts_.size() < maxObservations_,
            "RESEARCH_TRADE_INFERENCE_CAPACITY");
    if (!initialized_) {
        CumulativeTradeObservation nextLast = o, receipt = o;
        receipts_.emplace(o.sequence, std::move(receipt));
        last_ = std::move(nextLast); lastSequence_ = o.sequence; clockUs_ = o.timestampUs;
        initialized_ = true;
        return false;
    }

    Require(o.sequence > lastSequence_ && o.timestampUs >= clockUs_,
            "RESEARCH_TRADE_INFERENCE_OUT_OF_ORDER");
    Require(o.cumulativeVolume >= last_.cumulativeVolume &&
            o.cumulativeTurnover >= last_.cumulativeTurnover,
            "RESEARCH_TRADE_INFERENCE_COUNTER_REVERSED");
    const auto deltaVolume = o.cumulativeVolume - last_.cumulativeVolume;
    Require(deltaVolume <= 1000000000000LL,
            "RESEARCH_TRADE_INFERENCE_VOLUME_BOUND");
    const long double deltaTurnover =
        static_cast<long double>(o.cumulativeTurnover) - last_.cumulativeTurnover;

    CumulativeTradeInferenceResult result;
    result.inferred = true;
    result.fromSequence = last_.sequence; result.toSequence = o.sequence;
    result.timestampUs = o.timestampUs; result.deltaVolume = deltaVolume;
    result.deltaTurnover = ModelFinite(deltaTurnover);

    if (deltaVolume == 0) {
        Require(o.cumulativeTurnover == last_.cumulativeTurnover,
                "RESEARCH_TRADE_INFERENCE_TURNOVER_WITHOUT_VOLUME");
    } else {
        const auto previousBid = grid_.Index(last_.bestBidPrice);
        const auto previousAsk = grid_.Index(last_.bestAskPrice);
        Require(previousAsk - previousBid == 1,
                "RESEARCH_TRADE_INFERENCE_AMBIGUOUS_SPREAD");
        const auto lastPrice = grid_.Index(o.lastPrice);
        Require(lastPrice == previousBid || lastPrice == previousAsk,
                "RESEARCH_TRADE_INFERENCE_LAST_PRICE_OUTSIDE_BOOK");

        const long double turnoverPerTick =
            static_cast<long double>(tickSize_) * multiplier_;
        const long double tickNotional = deltaTurnover / turnoverPerTick;
        const long double rounded = std::round(tickNotional);
        Require(std::isfinite(tickNotional) &&
                std::fabs(tickNotional - rounded) <= InferenceTolerance(tickNotional, rounded) &&
                rounded >= std::numeric_limits<std::int64_t>::min() &&
                rounded <= std::numeric_limits<std::int64_t>::max(),
                "RESEARCH_TRADE_INFERENCE_TURNOVER_OFF_GRID");

        const long double askQuantityRaw =
            rounded - static_cast<long double>(deltaVolume) * previousBid;
        const long double askRounded = std::round(askQuantityRaw);
        Require(std::fabs(askQuantityRaw - askRounded) <=
                    InferenceTolerance(askQuantityRaw, askRounded) &&
                askRounded >= 0 && askRounded <= deltaVolume,
                "RESEARCH_TRADE_INFERENCE_EVIDENCE_INCONSISTENT");
        const auto askQuantity = static_cast<std::int64_t>(askRounded);
        const auto bidQuantity = deltaVolume - askQuantity;
        Require((lastPrice != previousAsk || askQuantity > 0) &&
                (lastPrice != previousBid || bidQuantity > 0),
                "RESEARCH_TRADE_INFERENCE_LAST_PRICE_UNSUPPORTED");

        result.inferredSellVolume = bidQuantity;
        result.inferredBuyVolume = askQuantity;
        if (bidQuantity > 0) {
            InferredTradeLevel level;
            level.priceTicks = previousBid; level.price = grid_.Price(previousBid);
            level.quantity = bidQuantity; result.levels.push_back(level);
        }
        if (askQuantity > 0) {
            InferredTradeLevel level;
            level.priceTicks = previousAsk; level.price = grid_.Price(previousAsk);
            level.quantity = askQuantity; result.levels.push_back(level);
        }
    }

    CumulativeTradeObservation nextLast = o, receipt = o;
    receipts_.emplace(o.sequence, std::move(receipt));
    last_ = std::move(nextLast); lastSequence_ = o.sequence; clockUs_ = o.timestampUs;
    output = std::move(result);
    return true;
}
namespace {
bool ModelIdentity(const std::string& id) {
    if (id.empty() || id.size() > 96) return false;
    for (unsigned char c : id)
        if (!((c >= 'A' && c <= 'Z') || (c >= 'a' && c <= 'z') ||
              (c >= '0' && c <= '9') || c == '_' || c == '-' || c == '.' || c == ':')) return false;
    return true;
}
std::vector<ResearchInstrument> AccountSpecs(const std::vector<FlowInstrument>& specs) {
    std::vector<ResearchInstrument> result;
    Require(!specs.empty() && specs.size() <= 1024, "RESEARCH_MODEL_UNIVERSE_INVALID");
    for (const auto& spec : specs) {
        ResearchPriceGrid(spec.tickSize, spec.account.priceDomain);
        Require(spec.lot > 0 && spec.lot <= 1000000000000LL &&
            std::isfinite(spec.feePerUnit) && spec.feePerUnit >= 0 &&
            std::isfinite(spec.feeRate) && spec.feeRate >= 0 && spec.feeRate <= 1,
            "RESEARCH_MODEL_INSTRUMENT_INVALID");
        result.push_back(spec.account);
    }
    return result;
}
double ModelFee(const FlowInstrument& spec, double price, std::int64_t quantity) {
    return ModelFinite(static_cast<long double>(quantity) *
        (spec.feePerUnit + std::fabs(static_cast<long double>(price)) *
         spec.account.multiplier * spec.feeRate));
}
bool SameFlow(const FlowEvent& a, const FlowEvent& b) {
    return a.kind == b.kind && a.sequence == b.sequence && a.timestampUs == b.timestampUs &&
        a.instrument == b.instrument && a.orderId == b.orderId && a.actor == b.actor &&
        a.timeInForce == b.timeInForce && a.side == b.side && a.quantity == b.quantity &&
        a.priceTicks == b.priceTicks && a.hasLimit == b.hasLimit;
}
bool SameTick(const Tick& a, const Tick& b) {
    return a.instrument == b.instrument && a.timestampUs == b.timestampUs &&
        a.sequence == b.sequence && a.price == b.price && a.volume == b.volume;
}
bool SameTarget(const NextBarTarget& a, const NextBarTarget& b) {
    const auto& x = a.sourceBar; const auto& y = b.sourceBar;
    return a.targetId == b.targetId && a.observedAtUs == b.observedAtUs &&
        a.targetQuantity == b.targetQuantity && x.instrument == y.instrument &&
        x.tradingDay == y.tradingDay && x.beginUs == y.beginUs && x.endUs == y.endUs &&
        x.open == y.open && x.high == y.high && x.low == y.low && x.close == y.close &&
        x.volume == y.volume && x.tickCount == y.tickCount && x.complete == y.complete;
}
void ModelBar(const Bar& bar, ResearchPriceDomain domain) {
    Require(ModelPrice(bar.open, domain) && ModelPrice(bar.high, domain) &&
        ModelPrice(bar.low, domain) && ModelPrice(bar.close, domain) &&
        bar.low <= bar.open && bar.low <= bar.close && bar.high >= bar.open &&
        bar.high >= bar.close && bar.low <= bar.high, "RESEARCH_MODEL_BAR_PRICE_INVALID");
    // Reuse the Data SDK for all non-price invariants, including trading dates.
    Bar structural = bar;
    structural.open = structural.high = structural.low = structural.close = 1;
    ValidateBar(structural);
}
}
OrderFlowReplay::OrderFlowReplay(double initial, std::string currency,
        const std::vector<FlowInstrument>& specs, std::size_t maxEvents)
    : account_(initial, std::move(currency), AccountSpecs(specs), maxEvents), maxEvents_(maxEvents) {
    Require(maxEvents > 0 && maxEvents <= 1000000, "RESEARCH_MODEL_EVENT_CAPACITY");
    for (const auto& spec : specs) specs_.emplace(spec.account.instrument, spec);
}
std::vector<ResearchFill> OrderFlowReplay::Consume(const FlowEvent& event) {
    const auto found = receipts_.find(event.sequence);
    if (found != receipts_.end()) {
        Require(SameFlow(found->second, event), "RESEARCH_FLOW_SEQUENCE_CONFLICT");
        return {};
    }
    Require(event.sequence > lastSequence_ && event.timestampUs >= clockUs_,
            "RESEARCH_FLOW_OUT_OF_ORDER");
    Require(receipts_.size() < maxEvents_, "RESEARCH_MODEL_EVENT_CAPACITY");
    Require(specs_.count(event.instrument) != 0, "RESEARCH_MODEL_INSTRUMENT_UNKNOWN");
    OrderFlowReplay staged = *this;
    auto result = staged.Process(event);
    staged.receipts_.emplace(event.sequence, event);
    staged.lastSequence_ = event.sequence; staged.clockUs_ = event.timestampUs;
    *this = std::move(staged);
    return result;
}
std::vector<ResearchFill> OrderFlowReplay::Process(const FlowEvent& e) {
    const auto& spec = specs_.at(e.instrument);
    const ResearchPriceGrid grid(spec.tickSize, spec.account.priceDomain);
    Require(e.actor == FlowActor::External || e.actor == FlowActor::Research, "RESEARCH_FLOW_ACTOR_INVALID");
    if (e.kind == FlowEventKind::Add) {
        Require(ModelIdentity(e.orderId) && orders_.count(e.orderId) == 0 &&
            (e.side == 1 || e.side == -1) && e.quantity > 0 && e.quantity <= 1000000000000LL &&
            e.quantity % spec.lot == 0, "RESEARCH_FLOW_ORDER_INVALID");
        Require(e.timeInForce == FlowTimeInForce::Gtc || e.timeInForce == FlowTimeInForce::Day ||
            e.timeInForce == FlowTimeInForce::Ioc || e.timeInForce == FlowTimeInForce::Fok,
            "RESEARCH_FLOW_TIF_INVALID");
        if (e.hasLimit) grid.Price(e.priceTicks);
        else Require(e.priceTicks == 0 &&
            (e.timeInForce == FlowTimeInForce::Ioc || e.timeInForce == FlowTimeInForce::Fok),
            "RESEARCH_FLOW_UNPRICED_RESTING_ORDER");
        OrderFlowReplay trial = *this;
        auto result = trial.Match(e);
        if (e.timeInForce == FlowTimeInForce::Fok && trial.orders_.at(e.orderId).filled != e.quantity) {
            // Reject the entire walk: no maker quantity, fee or account changes.
            FlowOrderState failed; failed.submitted = e; failed.cancelled = e.quantity;
            orders_.emplace(e.orderId, std::move(failed));
            return {};
        }
        *this = std::move(trial);
        return result;
    }
    if (e.kind == FlowEventKind::Cancel) {
        Require(ModelIdentity(e.orderId) && e.side == 0 && e.priceTicks == 0 && e.hasLimit &&
            e.timeInForce == FlowTimeInForce::Gtc, "RESEARCH_FLOW_CANCEL_FIELDS_INVALID");
        auto found = orders_.find(e.orderId);
        Require(found != orders_.end() && found->second.submitted.instrument == e.instrument &&
            found->second.submitted.actor == e.actor, "RESEARCH_FLOW_CANCEL_IDENTITY_INVALID");
        auto& order = found->second;
        Require(e.quantity >= 0 && e.quantity <= order.remaining && e.quantity % spec.lot == 0,
            "RESEARCH_FLOW_CANCEL_QUANTITY_INVALID");
        const auto quantity = e.quantity == 0 ? order.remaining : e.quantity;
        order.remaining -= quantity; order.cancelled += quantity;
        return {};
    }
    Require(e.orderId.empty() && e.side == 0 && e.quantity == 0 && e.hasLimit &&
        e.actor == FlowActor::External && e.timeInForce == FlowTimeInForce::Gtc,
        "RESEARCH_FLOW_EVENT_FIELDS_INVALID");
    if (e.kind == FlowEventKind::SessionEnd) {
        Require(e.priceTicks == 0, "RESEARCH_FLOW_SESSION_FIELDS_INVALID");
        for (auto& entry : orders_) {
            auto& order = entry.second;
            if (order.submitted.instrument == e.instrument && order.submitted.timeInForce == FlowTimeInForce::Day) {
                order.cancelled += order.remaining; order.remaining = 0;
            }
        }
        return {};
    }
    Require(e.kind == FlowEventKind::Mark || e.kind == FlowEventKind::BasisRebase,
            "RESEARCH_FLOW_EVENT_KIND_INVALID");
    const double price = grid.Price(e.priceTicks);
    if (e.kind == FlowEventKind::BasisRebase) {
        ResearchSettlement settlement;
        settlement.settlementId = "flow-basis:" + std::to_string(e.sequence);
        settlement.instrument = e.instrument; settlement.timestampUs = e.timestampUs; settlement.price = price;
        account_.Settle(settlement);
    }
    Tick mark; mark.instrument = e.instrument; mark.timestampUs = e.timestampUs;
    mark.sequence = e.sequence; mark.price = price; account_.Observe(mark);
    return {};
}
std::vector<ResearchFill> OrderFlowReplay::Match(const FlowEvent& e) {
    FlowOrderState state; state.submitted = e; state.remaining = e.quantity;
    orders_.emplace(e.orderId, state);
    auto& incoming = orders_.at(e.orderId);
    std::vector<std::string> candidates;
    for (const auto& entry : orders_) {
        const auto& maker = entry.second;
        if (maker.remaining > 0 && maker.submitted.instrument == e.instrument &&
            maker.submitted.side == -e.side) candidates.push_back(entry.first);
    }
    std::sort(candidates.begin(), candidates.end(), [&](const std::string& a, const std::string& b) {
        const auto& x = orders_.at(a).submitted; const auto& y = orders_.at(b).submitted;
        if (x.priceTicks != y.priceTicks) return e.side == 1 ? x.priceTicks < y.priceTicks : x.priceTicks > y.priceTicks;
        return x.sequence < y.sequence;
    });
    const auto& spec = specs_.at(e.instrument);
    const ResearchPriceGrid grid(spec.tickSize, spec.account.priceDomain);
    std::vector<ResearchFill> result;
    // FOK admission is decided before any accounting, even if a hypothetical
    // partial walk would overflow a fee. Insufficient FOK is a cancellation,
    // not a partial trade or a numeric error in a trade that never happened.
    if (e.timeInForce == FlowTimeInForce::Fok) {
        auto needed = e.quantity;
        for (const auto& id : candidates) {
            const auto& maker = orders_.at(id);
            if (e.hasLimit && (e.side == 1 ? maker.submitted.priceTicks > e.priceTicks : maker.submitted.priceTicks < e.priceTicks)) break;
            if (e.actor == FlowActor::Research && maker.submitted.actor == FlowActor::Research) break;
            needed -= std::min(needed, maker.remaining);
            if (needed == 0) break;
        }
        if (needed != 0) {
            incoming.cancelled = incoming.remaining; incoming.remaining = 0;
            return result;
        }
    }
    for (const auto& id : candidates) {
        if (incoming.remaining == 0) break;
        auto& maker = orders_.at(id);
        if (e.hasLimit && (e.side == 1 ? maker.submitted.priceTicks > e.priceTicks : maker.submitted.priceTicks < e.priceTicks)) break;
        if (e.actor == FlowActor::Research && maker.submitted.actor == FlowActor::Research) {
            incoming.cancelled += incoming.remaining; incoming.remaining = 0; break;
        }
        const auto quantity = std::min(incoming.remaining, maker.remaining);
        if (e.actor == FlowActor::Research || maker.submitted.actor == FlowActor::Research) {
            const auto& research = e.actor == FlowActor::Research ? e : maker.submitted;
            ResearchFill fill; fill.fillId = "flow:" + std::to_string(e.sequence) + ":" + id;
            fill.orderId = research.orderId; fill.instrument = e.instrument; fill.timestampUs = e.timestampUs;
            fill.side = research.side; fill.quantity = quantity; fill.price = grid.Price(maker.submitted.priceTicks);
            fill.fee = ModelFee(spec, fill.price, quantity);
            account_.Apply(fill); result.push_back(fill);
        }
        incoming.remaining -= quantity; incoming.filled += quantity;
        maker.remaining -= quantity; maker.filled += quantity;
    }
    if (e.timeInForce == FlowTimeInForce::Ioc || e.timeInForce == FlowTimeInForce::Fok) {
        incoming.cancelled += incoming.remaining; incoming.remaining = 0;
    }
    return result;
}
FlowOrderState OrderFlowReplay::Order(const std::string& id) const {
    const auto found = orders_.find(id);
    Require(found != orders_.end(), "RESEARCH_FLOW_ORDER_UNKNOWN");
    return found->second;
}
std::size_t OrderFlowReplay::ActiveOrders() const {
    std::size_t count = 0;
    for (const auto& item : orders_) if (item.second.remaining > 0) ++count;
    return count;
}
ResearchPortfolioSnapshot OrderFlowReplay::Snapshot(std::int64_t asOf, std::int64_t maxAge) const {
    Require(asOf >= clockUs_, "RESEARCH_FLOW_SNAPSHOT_IN_PAST");
    return account_.Snapshot(asOf, maxAge);
}
namespace {
NextBarPolicy DefaultNextPolicy(std::int64_t slippage) {
    NextBarPolicy policy; policy.slippageTicks = slippage; return policy;
}
}
NextBarReplay::NextBarReplay(double initial, std::string currency,
        const std::vector<FlowInstrument>& specs, std::int64_t slippage, std::size_t maxEvents)
    : NextBarReplay(initial, std::move(currency), specs, DefaultNextPolicy(slippage), maxEvents) {}
NextBarReplay::NextBarReplay(double initial, std::string currency,
        const std::vector<FlowInstrument>& specs, NextBarPolicy policy, std::size_t maxEvents)
    : account_(initial, std::move(currency), AccountSpecs(specs), maxEvents),
      policy_(std::move(policy)), maxEvents_(maxEvents) {
    Require((policy_.timing == NextOpenTiming::StrictlyLater || policy_.timing == NextOpenTiming::AfterClosePhase) &&
            policy_.slippageTicks >= 0 && policy_.slippageTicks <= 1000000 &&
            maxEvents > 0 && maxEvents <= 1000000, "RESEARCH_NEXT_BAR_CONFIG_INVALID");
    for (const auto& spec : specs) {
        specs_.emplace(spec.account.instrument, spec); quantities_.emplace(spec.account.instrument, 0);
    }
    for (const auto& item : policy_.instrumentSlippageTicks)
        Require(specs_.count(item.first) && item.second >= 0 && item.second <= 1000000,
                "RESEARCH_NEXT_BAR_SLIPPAGE_INVALID");
}
bool NextBarReplay::SetTarget(const NextBarTarget& target) {
    Require(ModelIdentity(target.targetId), "RESEARCH_NEXT_BAR_TARGET_ID_INVALID");
    const auto duplicate = targetIds_.find(target.targetId);
    if (duplicate != targetIds_.end()) {
        Require(SameTarget(duplicate->second, target), "RESEARCH_NEXT_BAR_TARGET_ID_CONFLICT");
        return false;
    }
    const auto& bar = target.sourceBar;
    const auto found = specs_.find(bar.instrument);
    Require(found != specs_.end(), "RESEARCH_MODEL_INSTRUMENT_UNKNOWN");
    const auto& spec = found->second;
    ModelBar(bar, spec.account.priceDomain);
    Require(bar.complete && target.observedAtUs >= bar.endUs && target.observedAtUs >= clockUs_ &&
        target.targetQuantity >= -1000000000000LL && target.targetQuantity <= 1000000000000LL &&
        target.targetQuantity % spec.lot == 0, "RESEARCH_NEXT_BAR_TARGET_INVALID");
    const ResearchPriceGrid grid(spec.tickSize, spec.account.priceDomain);
    grid.Index(bar.open); grid.Index(bar.high); grid.Index(bar.low); grid.Index(bar.close);
    Require(eventCount_ < maxEvents_, "RESEARCH_MODEL_EVENT_CAPACITY");
    NextBarReplay staged = *this;
    staged.targetIds_.emplace(target.targetId, target); staged.pending_[bar.instrument] = target;
    staged.clockUs_ = target.observedAtUs; ++staged.eventCount_;
    *this = std::move(staged);
    return true;
}
std::vector<ResearchFill> NextBarReplay::ObserveOpen(const Tick& open) {
    const auto found = specs_.find(open.instrument);
    Require(found != specs_.end(), "RESEARCH_MODEL_INSTRUMENT_UNKNOWN");
    const auto& spec = found->second;
    ModelTick(open, spec.account.priceDomain);
    const auto previous = opens_.find(open.instrument);
    if (previous != opens_.end() && previous->second.sequence == open.sequence) {
        Require(SameTick(previous->second, open), "RESEARCH_SEQUENCE_CONFLICT");
        return {};
    }
    Require(open.timestampUs >= clockUs_ && (previous == opens_.end() ||
        (open.sequence > previous->second.sequence && open.timestampUs > previous->second.timestampUs)),
        "RESEARCH_NEXT_BAR_OPEN_OUT_OF_ORDER");
    Require(eventCount_ < maxEvents_, "RESEARCH_MODEL_EVENT_CAPACITY");
    const ResearchPriceGrid grid(spec.tickSize, spec.account.priceDomain);
    const auto market = grid.Index(open.price);
    NextBarReplay staged = *this;
    std::vector<ResearchFill> fills;
    auto pending = staged.pending_.find(open.instrument);
    if (pending != staged.pending_.end() && (open.timestampUs > pending->second.observedAtUs ||
        (policy_.timing == NextOpenTiming::AfterClosePhase && open.timestampUs == pending->second.observedAtUs))) {
        const auto& target = pending->second;
        const auto current = staged.quantities_.at(open.instrument);
        const auto desired = target.targetQuantity;
        std::vector<std::int64_t> deltas;
        if (current != 0 && desired != 0 && (current > 0) != (desired > 0)) {
            deltas.push_back(-current); deltas.push_back(desired);
        } else if (desired != current) deltas.push_back(desired - current);
        for (std::size_t i = 0; i < deltas.size(); ++i) {
            ResearchFill fill; fill.fillId = "next:" + target.targetId + ":" + std::to_string(i);
            fill.orderId = target.targetId; fill.instrument = open.instrument; fill.timestampUs = open.timestampUs;
            fill.side = deltas[i] > 0 ? 1 : -1; fill.quantity = deltas[i] > 0 ? deltas[i] : -deltas[i];
            const auto overrideSlip = policy_.instrumentSlippageTicks.find(open.instrument);
            const auto slip = overrideSlip == policy_.instrumentSlippageTicks.end() ?
                policy_.slippageTicks : overrideSlip->second;
            fill.price = grid.Price(market + fill.side * slip);
            fill.fee = ModelFee(spec, fill.price, fill.quantity);
            staged.account_.Apply(fill); fills.push_back(fill);
        }
        staged.quantities_[open.instrument] = desired; staged.pending_.erase(pending);
    }
    // This is the explicit observed OPEN, not a fabricated post-trade quote.
    // The canonical portfolio invalidates a filled position until Observe.
    // A duplicate portfolio quote may be a previously accepted MARK, not an
    // OPEN. It cannot become fresh execution evidence. Any staged fills, fees
    // or target changes must roll back when no new observation was accepted.
    Require(staged.account_.Observe(open), "RESEARCH_NEXT_BAR_QUOTE_KIND_CONFLICT");
    staged.opens_[open.instrument] = open;
    staged.clockUs_ = open.timestampUs; ++staged.eventCount_;
    *this = std::move(staged);
    return fills;
}
bool NextBarReplay::ObserveMark(const Tick& mark) {
    const auto found = specs_.find(mark.instrument);
    Require(found != specs_.end(), "RESEARCH_MODEL_INSTRUMENT_UNKNOWN");
    ModelTick(mark, found->second.account.priceDomain);
    ResearchPriceGrid(found->second.tickSize, found->second.account.priceDomain).Index(mark.price);
    const auto opening = opens_.find(mark.instrument);
    Require(opening == opens_.end() || opening->second.sequence != mark.sequence,
            "RESEARCH_NEXT_BAR_QUOTE_KIND_CONFLICT");
    NextBarReplay staged = *this;
    if (!staged.account_.Observe(mark)) return false;
    Require(mark.timestampUs >= clockUs_ && eventCount_ < maxEvents_, "RESEARCH_NEXT_BAR_MARK_INVALID");
    staged.clockUs_ = mark.timestampUs; ++staged.eventCount_;
    *this = std::move(staged); return true;
}
ResearchPortfolioValuation NextBarReplay::Valuation(std::int64_t asOf, std::int64_t maxAge) const {
    Require(asOf >= clockUs_, "RESEARCH_NEXT_BAR_SNAPSHOT_IN_PAST");
    return account_.Valuation(asOf, maxAge);
}
std::map<std::string, std::int64_t> NextBarReplay::PendingTargets() const {
    std::map<std::string, std::int64_t> values;
    for (const auto& item : pending_) values.emplace(item.first, item.second.targetQuantity);
    return values;
}
ResearchPortfolioSnapshot NextBarReplay::Snapshot(std::int64_t asOf, std::int64_t maxAge) const {
    Require(asOf >= clockUs_, "RESEARCH_NEXT_BAR_SNAPSHOT_IN_PAST");
    return account_.Snapshot(asOf, maxAge);
}
}} // namespace hepta::research
