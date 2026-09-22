#include "signal.h"
#include <algorithm>
#include <cmath>
#include <limits>
#include <stdexcept>
#include <utility>

namespace hepta { namespace research {
void ValidateIntent(const BoundedIntent& i, std::int64_t now) {
    Tick identity; identity.instrument = i.instrument; identity.tradingDay = 20000101;
    identity.timestampUs = i.observedAtUs; identity.price = i.limitPrice; ValidateTick(identity);
    if (now <= 0 || i.observedAtUs / 1000 > now || i.observedAtUs % 1000 != 0 ||
        i.expiresAtMs <= now || i.expiresAtMs - now > 60000 ||
        (i.action != "BUY" && i.action != "SELL") || !std::isfinite(i.quantity) ||
        i.quantity <= 0 || i.quantity > 1000000000 ||
        now - i.observedAtUs / 1000 > 60000)
        throw std::invalid_argument("invalid, expired or stale bounded strategy intent");
}
BreakoutSignal::BreakoutSignal(std::string instrument, std::size_t lookback,
    double quantity, std::int64_t maxAge, std::int64_t ttl)
    : instrument_(std::move(instrument)), lookback_(lookback), quantity_(quantity), maxAgeMs_(maxAge), ttlMs_(ttl) {
    if (instrument_.empty() || !lookback || lookback > 100000 || !std::isfinite(quantity) || quantity <= 0 ||
        quantity > 1000000000 || maxAge < 0 || maxAge > 60000 || ttl <= 0 || ttl > 60000)
        throw std::invalid_argument("invalid breakout-signal configuration");
}
bool BreakoutSignal::OnClosedBar(const Bar& b, std::int64_t now, BoundedIntent& output) {
    ValidateBar(b);
    if (!b.complete || b.instrument != instrument_ || now <= 0 || b.endUs / 1000 > now ||
        b.endUs % 1000 != 0 || now-b.endUs/1000 > maxAgeMs_ || now > std::numeric_limits<std::int64_t>::max()-ttlMs_)
        throw std::invalid_argument("incomplete, future or stale signal bar");
    if (!window_.empty()) {
        const Bar& last = window_.back();
        if (b.beginUs == last.beginUs && b.endUs == last.endUs && b.tradingDay == last.tradingDay &&
            b.open == last.open && b.high == last.high && b.low == last.low && b.close == last.close &&
            b.volume == last.volume && b.highTimeUs == last.highTimeUs && b.lowTimeUs == last.lowTimeUs) return false;
        if (b.beginUs < last.endUs || b.tradingDay < last.tradingDay)
            throw std::invalid_argument("conflicting or out-of-order signal bar");
    }
    BoundedIntent proposal;
    if (window_.size() == lookback_) {
        double high = window_.front().high, low = window_.front().low;
        for (const Bar& item : window_) { high = std::max(high, item.high); low = std::min(low, item.low); }
        if (b.close > high) proposal.action = "BUY";
        else if (b.close < low) proposal.action = "SELL";
        if (!proposal.action.empty()) {
            proposal.instrument = instrument_; proposal.quantity = quantity_; proposal.limitPrice = b.close;
            proposal.observedAtUs = b.endUs; proposal.expiresAtMs = now + ttlMs_; ValidateIntent(proposal, now);
        }
    }
    window_.push_back(b); if (window_.size() > lookback_) window_.pop_front();
    if (proposal.action.empty()) return false;
    output = proposal; return true;
}
}} // namespace
