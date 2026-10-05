#pragma once

#include <cmath>
#include <map>
#include <string>
#include <vector>

// This consumes the cumulative quantity / cumulative average price recorded
// by the canonical IB wrapper. They are NOT incremental shares / fill price.
// Identity (account, owner, contract, correlation, client) is bound by caller.
class IbCumulativeExecutionAggregate {
public:
    struct Point {
        double cumulativeQuantity;
        double cumulativeAveragePrice;
        Point(double q = 0, double p = 0) : cumulativeQuantity(q), cumulativeAveragePrice(p) {}
    };
    bool Observe(const std::string& id, double cumulativeQuantity, double averagePrice) {
        if (conflicted_) return false;
        if (id.empty() || id.size() > 256 || !std::isfinite(cumulativeQuantity) ||
            !std::isfinite(averagePrice) || cumulativeQuantity <= 0 || averagePrice <= 0) {
            return Conflict("IB_EXECUTION_AGGREGATE_INVALID");
        }
        const auto same = points_.find(id);
        if (same != points_.end()) {
            if (same->second.cumulativeQuantity != cumulativeQuantity ||
                same->second.cumulativeAveragePrice != averagePrice)
                return Conflict("IB_EXECUTION_ID_REPLAY_CONFLICT");
            return true; // Identical history download, including a new epoch.
        }
        if (points_.size() >= 512) return Conflict("IB_EXECUTION_AGGREGATE_CAPACITY");
        const std::string family = CorrectionFamily(id);
        for (const auto& item : points_) {
            if (!family.empty() && CorrectionFamily(item.first) == family)
                return Conflict("IB_EXECUTION_CORRECTION_RECONCILIATION_REQUIRED");
            if (item.second.cumulativeQuantity == cumulativeQuantity &&
                item.second.cumulativeAveragePrice != averagePrice)
                return Conflict("IB_EXECUTION_CUMULATIVE_PRICE_CONFLICT");
        }
        points_.emplace(id, Point(cumulativeQuantity, averagePrice));
        if (cumulativeQuantity > quantity_ ||
            (cumulativeQuantity == quantity_ && (lastId_.empty() || id > lastId_))) {
            quantity_ = cumulativeQuantity;
            averagePrice_ = averagePrice;
            lastId_ = id;
        }
        return true;
    }
    bool Valid() const { return !conflicted_ && !points_.empty(); }
    bool Conflicted() const { return conflicted_; }
    double Quantity() const { return Valid() ? quantity_ : 0; }
    double AveragePrice() const { return Valid() ? averagePrice_ : 0; }
    const std::string& LastExecutionId() const { return lastId_; }
    const std::string& Reason() const { return reason_; }
    const std::map<std::string, Point>& Points() const { return points_; }
private:
    static std::string CorrectionFamily(const std::string& id) {
        // IB corrections retain the execution id except the final period suffix.
        // Treat unfamiliar formats as independent IDs, not guessed corrections.
        const auto dot = id.rfind('.');
        if (dot == std::string::npos || dot == 0 || dot + 1 == id.size()) return {};
        for (auto i = dot + 1; i < id.size(); ++i)
            if (id[i] < '0' || id[i] > '9') return {};
        return id.substr(0, dot);
    }
    bool Conflict(const char* reason) { conflicted_ = true; reason_ = reason; return false; }
    std::map<std::string, Point> points_;
    bool conflicted_ = false;
    double quantity_ = 0;
    double averagePrice_ = 0;
    std::string lastId_, reason_;
};
