#pragma once

#include <cmath>
#include <cstdint>
#include <map>
#include <string>

// IB's legacy execDetails projection stores cumQty and avgPrice, NOT shares
// and the individual execution price. A second execution ID is normal for a
// partial fill. Retain every ID for deduplication, choose the largest coherent
// cumulative observation, and never sum these quantities or invent a VWAP.
class IbCumulativeExecutionEvidence {
public:
    struct Point {
        double quantity = 0.0;
        double average = 0.0;
        std::uint64_t observedAtMs = 0;
    };
    static std::size_t Capacity() { return 128; }
    bool Observe(const std::string& id, double quantity, double average,
                 std::uint64_t observedAtMs, bool priceRevisionPending = false) {
        if (m_ambiguous) return false;
        if (priceRevisionPending) return Reject("IB_EXECUTION_PRICE_REVISION_PENDING");
        if (id.empty() || id.size() > 128 || quantity <= 0.0 || average <= 0.0 ||
            !std::isfinite(quantity) || !std::isfinite(average) ||
            !std::isfinite(quantity * average))
            return Reject("IB_EXECUTION_CUMULATIVE_INVALID");
        const auto existing = m_points.find(id);
        if (existing != m_points.end()) {
            if (existing->second.quantity != quantity || existing->second.average != average)
                return Reject("IB_EXECUTION_ID_REPLAY_CONFLICT");
            return true; // exact replay is inert, including its later receipt time
        }
        if (m_points.size() >= Capacity()) return Reject("IB_EXECUTION_EVIDENCE_CAPACITY");
        const std::string correctionFamily = CorrectionFamily(id);
        const long double notional = static_cast<long double>(quantity) * average;
        for (const auto& item : m_points) {
            // IB corrections differ after the last dot. They require explicit
            // correction handling, not treatment as another partial fill.
            if (!correctionFamily.empty() && correctionFamily == CorrectionFamily(item.first))
                return Reject("IB_EXECUTION_CORRECTION_RECONCILIATION_REQUIRED");
            const auto& before = item.second;
            if (quantity == before.quantity)
                return Reject("IB_EXECUTION_CUMULATIVE_ID_CONFLICT");
            const long double earlier = static_cast<long double>(before.quantity) * before.average;
            if ((quantity > before.quantity && notional <= earlier) ||
                (quantity < before.quantity && notional >= earlier))
                return Reject("IB_EXECUTION_CUMULATIVE_NOTIONAL_CONFLICT");
        }
        Point point; point.quantity=quantity; point.average=average; point.observedAtMs=observedAtMs;
        m_points.emplace(id, point);
        if (m_latestId.empty() || quantity > Latest().quantity) m_latestId=id;
        return true;
    }
    bool Reject(const std::string& reason) {
        m_ambiguous=true; m_reason=reason; return false;
    }
    bool Ambiguous() const { return m_ambiguous; }
    bool HasEvidence() const { return !m_latestId.empty() && !m_ambiguous; }
    const std::string& Reason() const { return m_reason; }
    const std::string& LatestId() const { return m_latestId; }
    const std::map<std::string, Point>& Points() const { return m_points; }
    const Point& Latest() const {
        const auto found=m_points.find(m_latestId);
        if (found!=m_points.end()) return found->second;
        static const Point empty; return empty;
    }
    bool MatchesTerminal(double quantity, double average) const {
        return HasEvidence() && std::isfinite(quantity) && std::isfinite(average) &&
            quantity == Latest().quantity && average == Latest().average;
    }
private:
    static std::string CorrectionFamily(const std::string& id) {
        const auto dot=id.rfind('.');
        return dot==std::string::npos ? std::string() : id.substr(0,dot);
    }
    std::map<std::string, Point> m_points;
    std::string m_latestId, m_reason;
    bool m_ambiguous=false;
};
