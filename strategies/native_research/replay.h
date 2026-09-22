#pragma once
#include "market.h"
#include <map>
#include <string>
#include <vector>

namespace hepta { namespace research {

enum class Side { Buy, Sell };
enum class TimeInForce { Resting, FAK, FOK };
enum class ReplayStatus { Working, Filled, Cancelled, Expired };
struct ReplayOrder {
    std::string id, instrument;
    Side side = Side::Buy;
    TimeInForce tif = TimeInForce::Resting;
    std::uint64_t quantity = 0;
    double limit = 0;
    std::int64_t submittedUs = 0, expiresUs = 0;
};
struct ResearchFill {
    std::string id, orderId, instrument;
    Side side = Side::Buy;
    std::uint64_t quantity = 0;
    double price = 0;
    std::int64_t timestampUs = 0;
};
struct ReplayOrderState {
    ReplayOrder order;
    std::uint64_t filled = 0;
    ReplayStatus status = ReplayStatus::Working;
};

// Offline last-trade crossing model, NOT a venue adapter. Shared tick-volume
// budget; deterministic insertion priority; first tick establishes baseline.
// A submission can only fill on a strictly later timestamp (no same-tick fill).
// Queue rank, impact, margin, auction and live/exchange fills are NOT modelled.
class ReplayMatcher {
public:
    explicit ReplayMatcher(std::string instrument, std::size_t maxOrders = 100000);
    bool Submit(const ReplayOrder& order); // false=identical duplicate; conflict throws
    bool Cancel(const std::string& id);
    std::vector<ResearchFill> OnTick(const Tick& tick);
    const ReplayOrderState& State(const std::string& id) const;
private:
    std::string instrument_;
    std::size_t maxOrders_;
    std::vector<ReplayOrderState> orders_;
    std::map<std::string, std::size_t> index_;
    Tick last_;
    bool seen_ = false;
    std::uint64_t sequence_ = 0;
};

// One-instrument futures-style cash/PnL research ledger. Explicit funding only;
// no automatic cash injection, no market data/account authority, no OMS writes.
class ReplayLedger {
public:
    ReplayLedger(std::string instrument, double initialCash, double multiplier,
                 double feePerUnit, std::size_t maxFills = 1000000);
    bool Apply(const ResearchFill& fill); // idempotent by fill id; conflicts throw
    double Equity(double mark) const;
    double Cash() const { return cash_; }
    double Position() const { return position_; }
    double AverageEntry() const { return average_; }
    double RealizedPnl() const { return realized_; }
    double Fees() const { return fees_; }
private:
    std::string instrument_;
    double cash_, multiplier_, feePerUnit_, position_ = 0, average_ = 0, realized_ = 0, fees_ = 0;
    std::size_t maxFills_;
    std::map<std::string, ResearchFill> fills_;
};

// Unitized equity/drawdown portion refactored from heptaNetValueEvaluation.
// Copyright (c) Wu Chang Sheng. All rights reserved.
// Consult your license regarding permissions and restrictions.
// Annualization and expected return are explicit caller inputs. No guessed
// 16:00 session end, weekend calendar, or divide-by-zero ratio outputs.
class NetAssetTracker {
public:
    explicit NetAssetTracker(double initialEquity);
    void Funding(double amount);
    void Observe(double equity);
    double NetAsset() const { return nav_; }
    double MaxDrawdown() const { return maxDrawdown_; }
private:
    double equity_, units_, nav_ = 1, high_ = 1, maxDrawdown_ = 0;
};

}} // namespace
