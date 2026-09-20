#include "replay.h"
#include <algorithm>
#include <cmath>
#include <limits>
#include <stdexcept>
#include <utility>

namespace hepta { namespace research {
namespace {
void Require(bool ok, const char* reason) { if (!ok) throw std::invalid_argument(reason); }
double Finite(long double n) {
    if (!std::isfinite(n) || std::fabs(n) > std::numeric_limits<double>::max())
        throw std::overflow_error("RESEARCH_ACCOUNT_OVERFLOW");
    return static_cast<double>(n);
}
bool Active(ReplayStatus s) { return s == ReplayStatus::Pending || s == ReplayStatus::PartiallyFilled; }
bool Same(const ReplayOrder& a,const ReplayOrder& b) {
    return a.id==b.id && a.side==b.side && a.quantity==b.quantity && a.limitPrice==b.limitPrice && a.tif==b.tif;
}
}
OfflineReplay::OfflineReplay(ReplayConfig config, SessionCalendar calendar)
    : config_(std::move(config)), calendar_(std::move(calendar)), cursor_(config_.instrument, config_.initialVolume) {
    Require(std::isfinite(config_.tickSize) && config_.tickSize > 0 &&
        std::isfinite(config_.multiplier) && config_.multiplier > 0 &&
        std::isfinite(config_.feePerUnit) && config_.feePerUnit >= 0 &&
        std::isfinite(config_.initialEquity) && config_.initialEquity > 0 &&
        config_.slippageTicks >= 0 && config_.slippageTicks <= 1000000 &&
        config_.maximumOrders > 0 && config_.maximumOrders <= 1000000, "RESEARCH_REPLAY_CONFIG");
    account_.equity=account_.peakEquity=config_.initialEquity;
}
std::int64_t OfflineReplay::PriceTicks(double p) const {
    Require(std::isfinite(p) && p > 0, "RESEARCH_REPLAY_PRICE");
    const long double q=static_cast<long double>(p)/config_.tickSize;
    Require(std::isfinite(q) && q >= 1 && q <= 1099511627776.0L, "RESEARCH_PRICE_GRID_RANGE");
    const long double nearest=std::round(q);
    Require(std::fabs(q-nearest) <= 8*std::numeric_limits<double>::epsilon()*std::max(1.0L,q),
            "RESEARCH_PRICE_OFF_TICK");
    return static_cast<std::int64_t>(nearest);
}
ReplayOrderState OfflineReplay::Submit(const ReplayOrder& order) {
    Require(initialized_, "RESEARCH_ORDER_REQUIRES_OBSERVATION");
    Require(ValidInstrument(order.id) && (order.side==1 || order.side==-1) &&
        order.quantity>0 && order.quantity<=1000000000 &&
        (order.tif==ReplayTif::Day || order.tif==ReplayTif::ImmediateOrCancel || order.tif==ReplayTif::FillOrKill),
        "RESEARCH_ORDER_INVALID");
    PriceTicks(order.limitPrice);
    auto existing=orders_.find(order.id);
    if (existing!=orders_.end()) {
        Require(Same(existing->second.order,order), "RESEARCH_ORDER_ID_CONFLICT");
        return existing->second;
    }
    Require(orders_.size()<config_.maximumOrders, "RESEARCH_ORDER_CAPACITY");
    ReplayOrderState state; state.order=order; state.remaining=order.quantity; state.tradingDay=day_;
    state.ordinal=ordinal_+1;
    orders_.emplace(order.id,state); ++ordinal_; return state;
}
ReplayOrderState OfflineReplay::Cancel(const std::string& id) {
    auto it=orders_.find(id); Require(it!=orders_.end(), "RESEARCH_ORDER_UNKNOWN");
    if (Active(it->second.status)) it->second.status=ReplayStatus::Cancelled;
    return it->second;
}
ReplayOrderState OfflineReplay::Status(const std::string& id) const {
    auto it=orders_.find(id); Require(it!=orders_.end(), "RESEARCH_ORDER_UNKNOWN"); return it->second;
}
void OfflineReplay::ApplyFill(ReplayAccount& a,const ReplayFill& f) const {
    const std::int64_t delta=f.side*f.quantity, previous=a.position;
    // Prevent loss of exact integer inventory in numeric projection and overflow.
    Require(std::abs(previous)<=1000000000000LL && std::abs(previous+delta)<=1000000000000LL,
            "RESEARCH_POSITION_CAPACITY");
    const auto closing=(previous!=0 && (previous>0)!=(delta>0)) ? std::min(std::abs(previous),f.quantity) : 0;
    const long double realized=static_cast<long double>(closing)*(f.price-a.averagePrice)*
        (previous>0?1:-1)*config_.multiplier;
    a.realizedPnl=Finite(static_cast<long double>(a.realizedPnl)+realized-f.fee);
    a.fees=Finite(static_cast<long double>(a.fees)+f.fee);
    a.position=previous+delta;
    if (previous==0 || (previous>0)==(delta>0)) {
        a.averagePrice=Finite((static_cast<long double>(std::abs(previous))*a.averagePrice+
            static_cast<long double>(f.quantity)*f.price)/std::abs(a.position));
    } else if (a.position==0) a.averagePrice=0;
    else if ((previous>0)!=(a.position>0)) a.averagePrice=f.price;
}
void OfflineReplay::Mark(ReplayAccount& a,double price) const {
    a.unrealizedPnl=Finite(static_cast<long double>(a.position)*(price-a.averagePrice)*config_.multiplier);
    a.equity=Finite(static_cast<long double>(config_.initialEquity)+a.realizedPnl+a.unrealizedPnl);
    a.peakEquity=std::max(a.peakEquity,a.equity);
    a.maximumDrawdown=std::max(a.maximumDrawdown,Finite(static_cast<long double>(a.peakEquity)-a.equity));
}
std::vector<ReplayFill> OfflineReplay::Push(const Tick& tick) {
    const auto observation=cursor_.Validate(tick,calendar_);
    if (observation.duplicate) return {};
    const std::int64_t market=PriceTicks(tick.price);
    // All logical/numeric validation commits atomically. Memory is explicitly bounded.
    auto next=orders_; ReplayAccount account=account_;
    std::vector<ReplayOrderState*> eligible;
    for (auto& item:next) if (Active(item.second.status)) eligible.push_back(&item.second);
    std::sort(eligible.begin(),eligible.end(),[](const ReplayOrderState* a,const ReplayOrderState* b) {
        return a->ordinal<b->ordinal;
    });
    std::uint64_t liquidity=observation.volumeDelta;
    std::vector<ReplayFill> fills;
    for (auto* state:eligible) {
        if (state->tradingDay!=observation.tradingDay) { state->status=ReplayStatus::Expired; continue; }
        const auto priceTicks=market+state->order.side*config_.slippageTicks;
        const auto limit=PriceTicks(state->order.limitPrice);
        const bool marketable=priceTicks>0 && (state->order.side==1 ? priceTicks<=limit : priceTicks>=limit);
        std::int64_t amount=marketable ? static_cast<std::int64_t>(std::min(liquidity,static_cast<std::uint64_t>(state->remaining))) : 0;
        if (state->order.tif==ReplayTif::FillOrKill && amount<state->remaining) amount=0;
        if (amount) {
            ReplayFill f; f.orderId=state->order.id; f.timestampMs=tick.timestampMs;
            f.quantity=amount; f.side=state->order.side;
            f.price=Finite(static_cast<long double>(priceTicks)*config_.tickSize);
            f.fee=Finite(static_cast<long double>(amount)*config_.feePerUnit);
            ApplyFill(account,f); fills.push_back(f); liquidity-=static_cast<std::uint64_t>(amount); state->remaining-=amount;
            state->status=state->remaining==0 ? ReplayStatus::Filled : ReplayStatus::PartiallyFilled;
        }
        if (state->remaining>0 && state->order.tif!=ReplayTif::Day) state->status=ReplayStatus::Expired;
    }
    Mark(account,tick.price);
    cursor_.Commit(tick,observation); orders_.swap(next); account_=account;
    day_=observation.tradingDay; initialized_=true; return fills;
}
void OfflineReplay::Settle(double price) {
    Require(initialized_, "RESEARCH_SETTLEMENT_REQUIRES_OBSERVATION"); PriceTicks(price);
    ReplayAccount next=account_; Mark(next,price);
    next.realizedPnl=Finite(static_cast<long double>(next.realizedPnl)+next.unrealizedPnl);
    next.unrealizedPnl=0; next.averagePrice=next.position==0?0:price;
    account_=next;
}
} }
