#pragma once
#include "market_data.h"
#include <map>
#include <string>
#include <vector>

namespace hepta { namespace research {
// Offline futures-style P&L only. Not a broker, margin engine or canonical simulator.
struct ReplayConfig {
    std::string instrument;
    double tickSize = 0, multiplier = 0, feePerUnit = 0, initialEquity = 0;
    std::int64_t slippageTicks = 0;
    std::size_t maximumOrders = 10000;
    InitialVolume initialVolume = InitialVolume::Baseline;
};
enum class ReplayTif { Day, ImmediateOrCancel, FillOrKill };
enum class ReplayStatus { Pending, PartiallyFilled, Filled, Cancelled, Expired };
struct ReplayOrder {
    std::string id;
    int side = 0; // +1 buy, -1 sell.
    std::int64_t quantity = 0;
    double limitPrice = 0;
    ReplayTif tif = ReplayTif::Day;
};
struct ReplayOrderState {
    ReplayOrder order;
    ReplayStatus status = ReplayStatus::Pending;
    std::int64_t remaining = 0;
    std::string tradingDay;
    std::uint64_t ordinal = 0;
};
struct ReplayFill {
    std::string orderId;
    std::int64_t timestampMs = 0, quantity = 0;
    int side = 0;
    double price = 0, fee = 0;
};
struct ReplayAccount {
    std::int64_t position = 0;
    double averagePrice = 0, realizedPnl = 0, unrealizedPnl = 0;
    double fees = 0, equity = 0, peakEquity = 0, maximumDrawdown = 0;
};
class OfflineReplay {
public:
    OfflineReplay(ReplayConfig config, SessionCalendar calendar);
    // Submission follows an observed tick; first eligibility is the next distinct tick.
    ReplayOrderState Submit(const ReplayOrder& order);
    ReplayOrderState Cancel(const std::string& id);
    ReplayOrderState Status(const std::string& id) const;
    std::vector<ReplayFill> Push(const Tick& tick);
    void Settle(double markPrice); // Explicit mark-to-market; preserves equity.
    ReplayAccount Account() const { return account_; }
private:
    ReplayConfig config_;
    SessionCalendar calendar_;
    TickCursor cursor_;
    bool initialized_ = false;
    std::string day_;
    std::uint64_t ordinal_ = 0;
    ReplayAccount account_;
    std::map<std::string, ReplayOrderState> orders_;
    std::int64_t PriceTicks(double price) const;
    void ApplyFill(ReplayAccount& account, const ReplayFill& fill) const;
    void Mark(ReplayAccount& account, double price) const;
};
} }
