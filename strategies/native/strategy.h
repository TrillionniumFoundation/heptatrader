#pragma once
#include "market_data.h"
#include <functional>
#include <string>

namespace hepta { namespace research {
struct TargetExposure {
    std::string strategyId, instrument;
    std::int64_t observationEndMs = 0;
    std::int64_t desiredUnits = 0;
};
// Pure CTA composition: immutable closed bars in, target exposure out.
// Neither target nor local research position is an authoritative order quantity.
class CtaSignal {
public:
    typedef std::function<std::int64_t(const BarWindow&)> Decision;
    CtaSignal(std::string strategyId, std::size_t history, std::int64_t maximumAbsoluteUnits,
              bool longOnly, Decision decision);
    TargetExposure OnClosedBar(const Bar& bar);
private:
    std::string strategyId_;
    BarWindow history_;
    std::int64_t maximum_;
    bool longOnly_;
    Decision decision_;
};
// Explicit example, not a claim to reproduce a historical trading strategy.
CtaSignal::Decision MovingAverageDecision(std::size_t fast, std::size_t slow,
                                         std::int64_t units, bool longOnly);
struct BoundedOrderProposal {
    std::string proposalId, instrument;
    int side = 0;
    std::int64_t quantity = 0;
    double limitPrice = 0;
    std::int64_t expiresAtMs = 0;
};
struct ProposalLimits {
    std::int64_t maximumQuantity = 0, maximumLifetimeMs = 0;
    double maximumPriceTimesQuantity = 0;
};
// Client-side preflight only; never risk approval. Caller supplies the wall clock.
bool ValidateProposal(const BoundedOrderProposal& proposal, const ProposalLimits& limits,
                      std::int64_t nowMs, std::string& reason);
} }
