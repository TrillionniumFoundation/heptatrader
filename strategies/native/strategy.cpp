#include "strategy.h"
#include <cmath>
#include <stdexcept>
#include <utility>

namespace hepta { namespace research {
CtaSignal::CtaSignal(std::string id,std::size_t history,std::int64_t maximum,bool longOnly,Decision decision)
    : strategyId_(std::move(id)), history_(history), maximum_(maximum), longOnly_(longOnly), decision_(std::move(decision)) {
    if (!ValidInstrument(strategyId_) || maximum<=0 || maximum>1000000000 || !decision_)
        throw std::invalid_argument("RESEARCH_STRATEGY_CONFIG");
}
TargetExposure CtaSignal::OnClosedBar(const Bar& bar) {
    // A rejected decision does not consume the bar. Stateful user callbacks must
    // manage their own rollback; only this adapter's history is transactional.
    BarWindow candidate=history_; candidate.Push(bar);
    const auto units=decision_(candidate);
    if (units>maximum_ || units < -maximum_ || (longOnly_ && units<0))
        throw std::invalid_argument("RESEARCH_TARGET_OUT_OF_BOUNDS");
    TargetExposure result; result.strategyId=strategyId_; result.instrument=bar.instrument;
    result.observationEndMs=bar.endMs; result.desiredUnits=units;
    history_=std::move(candidate); return result;
}
CtaSignal::Decision MovingAverageDecision(std::size_t fast,std::size_t slow,std::int64_t units,bool longOnly) {
    if (fast==0 || fast>=slow || slow>1000000 || units<=0 || units>1000000000)
        throw std::invalid_argument("RESEARCH_MA_CONFIG");
    return [fast,slow,units,longOnly](const BarWindow& h) -> std::int64_t {
        if (h.Size()<slow) return 0;
        const auto a=h.MeanClose(fast), b=h.MeanClose(slow);
        return a>b ? units : (a<b && !longOnly ? -units : 0);
    };
}
bool ValidateProposal(const BoundedOrderProposal& p,const ProposalLimits& l,std::int64_t now,std::string& reason) {
    reason.clear();
    if (l.maximumQuantity<=0 || l.maximumQuantity>1000000000 || l.maximumLifetimeMs<=0 ||
        !std::isfinite(l.maximumPriceTimesQuantity) || l.maximumPriceTimesQuantity<=0) reason="RESEARCH_PROPOSAL_LIMITS";
    else if (!ValidInstrument(p.proposalId) || !ValidInstrument(p.instrument) || (p.side!=1 && p.side!=-1) ||
        p.quantity<=0 || p.quantity>l.maximumQuantity || !std::isfinite(p.limitPrice) || p.limitPrice<=0)
        reason="RESEARCH_PROPOSAL_INVALID";
    else if (static_cast<long double>(p.quantity)*p.limitPrice>l.maximumPriceTimesQuantity)
        reason="RESEARCH_PROPOSAL_SIZE_LIMIT";
    else if (now<=0 || p.expiresAtMs<=now || p.expiresAtMs-now>l.maximumLifetimeMs)
        reason="RESEARCH_PROPOSAL_EXPIRED_OR_HORIZON";
    return reason.empty();
}
} }
