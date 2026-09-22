#include "replay.h"
#include <algorithm>
#include <cmath>
#include <limits>
#include <stdexcept>
#include <utility>

namespace hepta { namespace research {
namespace {
bool SameOrder(const ReplayOrder& a, const ReplayOrder& b) {
    return a.id == b.id && a.instrument == b.instrument && a.side == b.side && a.tif == b.tif &&
        a.quantity == b.quantity && a.limit == b.limit && a.submittedUs == b.submittedUs && a.expiresUs == b.expiresUs;
}
bool SameFill(const ResearchFill& a, const ResearchFill& b) {
    return a.id == b.id && a.orderId == b.orderId && a.instrument == b.instrument && a.side == b.side &&
        a.quantity == b.quantity && a.price == b.price && a.timestampUs == b.timestampUs;
}
bool ValidSide(Side s) { return s == Side::Buy || s == Side::Sell; }
void ValidateId(const std::string& id) {
    if (id.empty() || id.size() > 128) throw std::invalid_argument("invalid research identity");
    for (unsigned char c : id) if (c <= 32 || c >= 127) throw std::invalid_argument("invalid research identity");
}
}
ReplayMatcher::ReplayMatcher(std::string instrument, std::size_t maxOrders)
    : instrument_(std::move(instrument)), maxOrders_(maxOrders) {
    ValidateId(instrument_);
    if (!maxOrders || maxOrders > 1000000) throw std::invalid_argument("invalid replay order capacity");
}
bool ReplayMatcher::Submit(const ReplayOrder& o) {
    ValidateId(o.id);
    if (o.instrument != instrument_ || !ValidSide(o.side) || !o.quantity || o.quantity > 1000000000 ||
        !std::isfinite(o.limit) || o.limit <= 0 || o.submittedUs <= 0 || o.expiresUs <= o.submittedUs ||
        (o.tif != TimeInForce::Resting && o.tif != TimeInForce::FAK && o.tif != TimeInForce::FOK))
        throw std::invalid_argument("invalid replay order");
    auto found = index_.find(o.id);
    if (found != index_.end()) {
        if (!SameOrder(o, orders_[found->second].order)) throw std::invalid_argument("replay order identity conflict");
        return false;
    }
    if (seen_ && o.submittedUs < last_.timestampUs) throw std::invalid_argument("backdated replay order");
    if (orders_.size() == maxOrders_) throw std::length_error("replay order capacity exhausted");
    ReplayOrderState state; state.order = o;
    orders_.push_back(state);
    try { index_.emplace(o.id, orders_.size()-1); } catch (...) { orders_.pop_back(); throw; }
    return true;
}
const ReplayOrderState& ReplayMatcher::State(const std::string& id) const {
    auto it = index_.find(id);
    if (it == index_.end()) throw std::invalid_argument("unknown research order");
    return orders_[it->second];
}
bool ReplayMatcher::Cancel(const std::string& id) {
    auto it = index_.find(id);
    if (it == index_.end()) throw std::invalid_argument("unknown research order");
    ReplayOrderState& state = orders_[it->second];
    if (state.status != ReplayStatus::Working) return false;
    state.status = ReplayStatus::Cancelled; return true;
}
std::vector<ResearchFill> ReplayMatcher::OnTick(const Tick& tick) {
    ValidateTick(tick);
    if (tick.instrument != instrument_ || (seen_ &&
        (tick.timestampUs < last_.timestampUs || tick.tradingDay < last_.tradingDay ||
         (tick.tradingDay == last_.tradingDay && tick.cumulativeVolume < last_.cumulativeVolume))))
        throw std::invalid_argument("invalid replay chronology or instrument");
    if (seen_ && tick.timestampUs == last_.timestampUs && tick.price == last_.price &&
        tick.tradingDay == last_.tradingDay && tick.cumulativeVolume == last_.cumulativeVolume) return {};
    if (sequence_ == std::numeric_limits<std::uint64_t>::max()) throw std::overflow_error("replay sequence exhausted");
    std::uint64_t volume = seen_ && tick.tradingDay == last_.tradingDay ?
        tick.cumulativeVolume - last_.cumulativeVolume : 0;
    // Construct the whole tick transition before publishing any order changes.
    std::vector<ReplayOrderState> candidate = orders_;
    std::vector<ResearchFill> fills;
    for (ReplayOrderState& s : candidate) {
        if (s.status != ReplayStatus::Working) continue;
        const ReplayOrder& o = s.order;
        if (tick.timestampUs >= o.expiresUs) { s.status = ReplayStatus::Expired; continue; }
        if (tick.timestampUs <= o.submittedUs) continue;
        const bool crossed = o.side == Side::Buy ? tick.price <= o.limit : tick.price >= o.limit;
        const std::uint64_t left = o.quantity - s.filled;
        const std::uint64_t amount = !crossed || (o.tif == TimeInForce::FOK && volume < left) ? 0 : std::min(volume, left);
        if (amount) {
            ResearchFill f; f.id = o.id + ":tick:" + std::to_string(sequence_+1); f.orderId = o.id;
            f.instrument = instrument_; f.side = o.side; f.quantity = amount; f.price = tick.price;
            f.timestampUs = tick.timestampUs; fills.push_back(f);
            s.filled += amount; volume -= amount;
        }
        if (s.filled == o.quantity) s.status = ReplayStatus::Filled;
        else if (o.tif != TimeInForce::Resting) s.status = ReplayStatus::Cancelled;
    }
    orders_.swap(candidate); last_ = tick; seen_ = true; ++sequence_;
    return fills;
}
ReplayLedger::ReplayLedger(std::string instrument, double cash, double multiplier,
    double fee, std::size_t maxFills) : instrument_(std::move(instrument)), cash_(cash),
    multiplier_(multiplier), feePerUnit_(fee), maxFills_(maxFills) {
    ValidateId(instrument_);
    if (!std::isfinite(cash) || cash < 0 || !std::isfinite(multiplier) || multiplier <= 0 ||
        !std::isfinite(fee) || fee < 0 || !maxFills || maxFills > 10000000)
        throw std::invalid_argument("invalid research ledger configuration");
}
bool ReplayLedger::Apply(const ResearchFill& f) {
    if (f.id.empty() || f.id.size() > 180 || f.orderId.empty() || f.instrument != instrument_ || !ValidSide(f.side) ||
        !f.quantity || f.quantity > 1000000000 || !std::isfinite(f.price) || f.price <= 0 || f.timestampUs <= 0)
        throw std::invalid_argument("invalid research fill");
    auto it = fills_.find(f.id);
    if (it != fills_.end()) {
        if (!SameFill(it->second, f)) throw std::invalid_argument("research fill identity conflict");
        return false;
    }
    if (fills_.size() == maxFills_) throw std::length_error("research fill capacity exhausted");
    const double q = static_cast<double>(f.quantity) * (f.side == Side::Buy ? 1 : -1);
    const double nextPosition = position_ + q;
    if (std::abs(nextPosition) > 1000000000000.0) throw std::overflow_error("research position capacity exhausted");
    const double closing = position_ * q < 0 ? std::min(std::abs(position_), std::abs(q)) : 0;
    const double realized = closing * (f.price-average_) * (position_ > 0 ? 1 : -1) * multiplier_;
    const double fee = std::abs(q) * feePerUnit_;
    double average = average_;
    if (position_ == 0 || position_ * q > 0)
        average = (std::abs(position_)*average_ + std::abs(q)*f.price) / std::abs(nextPosition);
    else if (nextPosition == 0) average = 0;
    else if (position_ * nextPosition < 0) average = f.price;
    const double cash = cash_ + realized - fee;
    if (!std::isfinite(cash) || !std::isfinite(average) || !std::isfinite(realized_+realized) || !std::isfinite(fees_+fee))
        throw std::overflow_error("nonfinite research ledger transition");
    fills_.emplace(f.id, f);
    cash_ = cash; position_ = nextPosition; average_ = average; realized_ += realized; fees_ += fee;
    return true;
}
double ReplayLedger::Equity(double mark) const {
    if (!std::isfinite(mark) || mark <= 0) throw std::invalid_argument("invalid research mark");
    const double equity = cash_ + position_ * (mark-average_) * multiplier_;
    if (!std::isfinite(equity)) throw std::overflow_error("nonfinite research equity");
    return equity;
}
NetAssetTracker::NetAssetTracker(double initial) : equity_(initial), units_(initial) {
    if (!std::isfinite(initial) || initial <= 0) throw std::invalid_argument("positive initial equity required");
}
void NetAssetTracker::Funding(double amount) {
    if (!std::isfinite(amount) || nav_ <= 0 || !std::isfinite(equity_+amount) || equity_+amount <= 0 ||
        !std::isfinite(units_+amount/nav_) || units_+amount/nav_ <= 0)
        throw std::invalid_argument("invalid funding or zero-NAV fund");
    units_ += amount / nav_; equity_ += amount;
}
void NetAssetTracker::Observe(double equity) {
    if (!std::isfinite(equity) || equity < 0 || !std::isfinite(equity/units_))
        throw std::invalid_argument("invalid marked research equity");
    equity_ = equity; nav_ = equity / units_; high_ = std::max(high_, nav_);
    maxDrawdown_ = std::max(maxDrawdown_, (high_-nav_) / high_);
}
}} // namespace
