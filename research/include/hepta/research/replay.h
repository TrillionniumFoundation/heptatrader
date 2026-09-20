#pragma once
#include "analytics.h"
#include "market_data.h"
#include <cstddef>
#include <map>
#include <string>
#include <vector>

namespace hepta { namespace research {
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
class ReplayMatcher {
public:
    ReplayMatcher(std::string instrument, SessionSchedule schedule,
                  double feePerUnit = 0, std::size_t maxOrderIds = 100000);
    bool Submit(const ReplayOrder& order); // Exact duplicate is idempotent.
    std::vector<ReplayEvent> OnTick(const Tick& tick);
    std::vector<ReplayEvent> Cancel(const std::string& orderId);
    std::size_t ActiveOrders() const { return pending_.size(); }
private:
    struct Pending { ReplayOrder order; std::int64_t remaining = 0; std::uint64_t fills = 0; };
    std::string instrument_;
    SessionSchedule schedule_;
    double feePerUnit_;
    std::size_t maxOrderIds_;
    bool hasTick_ = false;
    Tick last_;
    std::vector<Pending> pending_;
    std::map<std::string, ReplayOrder> identities_;
};
}} // namespace hepta::research
