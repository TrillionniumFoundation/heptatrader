#include "hepta/research/replay.h"
#include <algorithm>
#include <cmath>
#include <limits>
#include <stdexcept>
#include <utility>

namespace hepta { namespace research {
namespace {
void Require(bool value, const char* why) { if (!value) throw std::invalid_argument(why); }
bool SameOrder(const ReplayOrder& a, const ReplayOrder& b) {
    return a.orderId == b.orderId && a.instrument == b.instrument && a.tradingDay == b.tradingDay &&
        a.submittedAtUs == b.submittedAtUs && a.expiresAtUs == b.expiresAtUs && a.side == b.side &&
        a.quantity == b.quantity && a.limitPrice == b.limitPrice && a.timeInForce == b.timeInForce;
}
}
ReplayMatcher::ReplayMatcher(std::string instrument, SessionSchedule schedule, double fee, std::size_t capacity)
    : instrument_(std::move(instrument)), schedule_(std::move(schedule)), feePerUnit_(fee), maxOrderIds_(capacity) {
    Tick v; v.instrument = instrument_; v.sequence = 1; v.price = 1; ValidateTick(v);
    Require(std::isfinite(fee) && fee >= 0 && capacity > 0, "RESEARCH_REPLAY_CONFIG_INVALID");
}
bool ReplayMatcher::Submit(const ReplayOrder& order) {
    Require(!order.orderId.empty() && order.orderId.size() <= 96 && order.instrument == instrument_ &&
            (order.side == 1 || order.side == -1) && order.quantity > 0 && order.quantity <= 1000000000000LL &&
            std::isfinite(order.limitPrice) && order.limitPrice > 0 && order.submittedAtUs >= 0 &&
            order.expiresAtUs > order.submittedAtUs, "RESEARCH_REPLAY_ORDER_INVALID");
    Require(order.timeInForce == ReplayTimeInForce::Day || order.timeInForce == ReplayTimeInForce::ImmediateOrCancel ||
            order.timeInForce == ReplayTimeInForce::FillOrKill, "RESEARCH_REPLAY_TIF_INVALID");
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
    ValidateTick(tick); Require(tick.instrument == instrument_, "RESEARCH_INSTRUMENT_MISMATCH");
    if (hasTick_ && tick.sequence == last_.sequence) {
        Require(tick.timestampUs == last_.timestampUs && tick.price == last_.price && tick.volume == last_.volume,
                "RESEARCH_SEQUENCE_CONFLICT");
        return {};
    }
    Require(tick.timestampUs >= clockUs_ && (!hasTick_ || tick.sequence > last_.sequence),
            "RESEARCH_REPLAY_TICK_OUT_OF_ORDER");
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
        const bool crosses = o.side == 1 ? tick.price <= o.limitPrice : tick.price >= o.limitPrice;
        const bool enough = o.timeInForce != ReplayTimeInForce::FillOrKill || available >= p.remaining;
        const auto filled = crosses && enough ? std::min(p.remaining, available) : 0;
        if (filled > 0) {
            Require(p.fills < std::numeric_limits<std::uint64_t>::max(), "RESEARCH_FILL_SEQUENCE_OVERFLOW");
            ReplayEvent e; e.kind = ReplayEventKind::Fill; e.orderId = o.orderId;
            e.fill.fillId = o.orderId + ":" + std::to_string(++p.fills);
            e.fill.orderId = o.orderId; e.fill.instrument = instrument_; e.fill.timestampUs = tick.timestampUs;
            e.fill.side = o.side; e.fill.quantity = filled; e.fill.price = tick.price;
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
}} // namespace hepta::research
