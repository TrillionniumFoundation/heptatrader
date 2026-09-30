#include "ib_paper_fx_cash_restart_catchup.h"

#include <algorithm>
#include <cmath>
#include <cstdint>
#include <map>
#include <set>

namespace {
struct SubmittedIdentity
{
    std::string sessionId;
    std::string executionDomain;
    std::string instrument;
    std::string side;
    std::string correlationId;
    double quantity = 0.0;
    std::uint64_t sentAtMs = 0;
    std::string serviceEpoch;
    std::uint64_t connectionEpoch = 0;
    bool accepted = false;
};

std::string NormalizeSide(const std::string& side)
{
    if (side == "BOT") return "BUY";
    if (side == "SLD") return "SELL";
    return side;
}

bool BasicBaselineValid(
    const std::map<std::string, IbPaperFxCashBaseline>& baselines,
    const IbPaperFxCashRestartCatchUpContext& context)
{
    if (baselines.empty() || context.toolSource.empty() ||
        context.brokerSource.empty() || context.account.empty() ||
        context.executionDomain.empty() ||
        !std::isfinite(context.maxGrossPosition) ||
        context.maxGrossPosition <= 0.0)
        return false;
    for (const auto& item : baselines)
    {
        const IbPaperFxCashBaseline& baseline = item.second;
        if (item.first.empty() || baseline.instrument != item.first ||
            baseline.account != context.account || baseline.currency.empty() ||
            !std::isfinite(baseline.baselineCashBalance) ||
            !std::isfinite(baseline.observedCashBalance) ||
            !std::isfinite(baseline.campaignExecutionDelta) ||
            baseline.observedAtMs == 0 ||
            !std::isfinite(baseline.baselineCashBalance +
                baseline.campaignExecutionDelta) ||
            std::fabs((baseline.baselineCashBalance +
                baseline.campaignExecutionDelta) -
                baseline.observedCashBalance) > 1e-6)
            return false;
    }
    return true;
}
} // namespace

bool ApplyIbPaperFxCashRestartCatchUp(
    const std::vector<OmsJournalEvent>& events,
    const IbPaperFxCashRestartCatchUpContext& context,
    std::map<std::string, IbPaperFxCashBaseline>& baselines,
    std::string& reason)
{
    reason.clear();
    if (!BasicBaselineValid(baselines, context))
    {
        reason = "IB_FX_CASH_RESTART_CATCHUP_BASELINE_INVALID";
        return false;
    }

    std::uint64_t checkpointAtMs = 0;
    for (const auto& item : baselines)
        checkpointAtMs = std::max(checkpointAtMs, item.second.observedAtMs);

    std::map<long, SubmittedIdentity> submitted;
    std::vector<const OmsJournalEvent*> acceptances;
    std::vector<const OmsJournalEvent*> executions;
    for (const OmsJournalEvent& event : events)
    {
        if (event.tsMs <= 0 ||
            static_cast<std::uint64_t>(event.tsMs) <= checkpointAtMs)
            continue;
        if (event.eventType == "place_sent" || event.eventType == "flatten_sent")
        {
            if (event.source != context.toolSource || event.orderId < 0 ||
                event.traceId.empty() || event.account != context.account ||
                event.executionDomain != context.executionDomain ||
                event.strategy != "agent-native" || event.instrument.empty() ||
                event.venueCorrelationId.empty() || !std::isfinite(event.qty) ||
                event.qty <= 0.0)
            {
                reason = "IB_FX_CASH_RESTART_CATCHUP_SUBMISSION_INVALID";
                return false;
            }
            SubmittedIdentity identity;
            identity.sessionId = event.traceId;
            identity.executionDomain = event.executionDomain;
            identity.instrument = event.instrument;
            identity.side = event.side;
            identity.correlationId = event.venueCorrelationId;
            identity.quantity = event.qty;
            identity.sentAtMs = static_cast<std::uint64_t>(event.tsMs);
            const auto existing = submitted.find(event.orderId);
            if (existing != submitted.end() &&
                (existing->second.correlationId != identity.correlationId ||
                 existing->second.sentAtMs != identity.sentAtMs))
            {
                reason = "IB_FX_CASH_RESTART_CATCHUP_SUBMISSION_INVALID";
                return false;
            }
            submitted[event.orderId] = identity;
        }
        else if (event.eventType == "broker_order_accepted" &&
                 event.source == context.brokerSource)
            acceptances.push_back(&event);
        else if (event.eventType == "broker_execution" &&
                 event.source == context.brokerSource)
            executions.push_back(&event);
    }

    for (const OmsJournalEvent* pointer : acceptances)
    {
        const OmsJournalEvent& event = *pointer;
        const auto found = submitted.find(event.orderId);
        if (found == submitted.end()) continue;
        const std::uint64_t acceptedAtMs =
            static_cast<std::uint64_t>(event.tsMs);
        if (event.traceId != found->second.sessionId ||
            event.account != context.account ||
            event.strategy != "agent-native" ||
            event.executionDomain != found->second.executionDomain ||
            event.instrument != found->second.instrument ||
            event.side != found->second.side || !std::isfinite(event.qty) ||
            event.qty != found->second.quantity ||
            event.brokerServiceEpoch.empty() ||
            event.brokerConnectionEpoch == 0 ||
            acceptedAtMs > found->second.sentAtMs ||
            found->second.sentAtMs - acceptedAtMs > 5000)
        {
            reason = "IB_FX_CASH_RESTART_CATCHUP_ACCEPTANCE_INVALID";
            return false;
        }
        if (found->second.accepted &&
            (found->second.serviceEpoch != event.brokerServiceEpoch ||
             found->second.connectionEpoch != event.brokerConnectionEpoch))
        {
            reason = "IB_FX_CASH_RESTART_CATCHUP_ACCEPTANCE_INVALID";
            return false;
        }
        found->second.serviceEpoch = event.brokerServiceEpoch;
        found->second.connectionEpoch = event.brokerConnectionEpoch;
        found->second.accepted = true;
    }

    std::map<std::string, IbPaperFxCashBaseline> advanced = baselines;
    std::set<std::string> executionIds;
    std::set<long> completedOrders;
    for (const OmsJournalEvent* pointer : executions)
    {
        const OmsJournalEvent& event = *pointer;
        const auto submit = submitted.find(event.orderId);
        if (submit == submitted.end() || !submit->second.accepted ||
            completedOrders.find(event.orderId) != completedOrders.end())
            continue;
        if (event.brokerServiceEpoch != submit->second.serviceEpoch ||
            event.brokerConnectionEpoch != submit->second.connectionEpoch ||
            event.traceId != submit->second.sessionId ||
            event.account != context.account ||
            event.strategy != "agent-native" ||
            event.executionDomain != submit->second.executionDomain ||
            event.instrument != submit->second.instrument ||
            event.brokerExecutionId.empty() || !std::isfinite(event.qty) ||
            event.qty <= 0.0 || event.qty > submit->second.quantity ||
            !std::isfinite(event.price) || event.price <= 0.0)
            continue;
        if (!event.venueCorrelationId.empty() &&
            event.venueCorrelationId != submit->second.correlationId)
        {
            reason = "IB_FX_CASH_RESTART_CATCHUP_EXECUTION_INVALID";
            return false;
        }
        if (!executionIds.insert(event.brokerExecutionId).second) continue;
        const std::string side = NormalizeSide(event.side);
        const std::string expectedSide = NormalizeSide(submit->second.side);
        if (side != expectedSide || (side != "BUY" && side != "SELL"))
        {
            reason = "IB_FX_CASH_RESTART_CATCHUP_EXECUTION_INVALID";
            return false;
        }
        if (event.qty < submit->second.quantity) continue;
        auto baseline = advanced.find(event.instrument);
        if (baseline == advanced.end())
        {
            reason = "IB_FX_CASH_RESTART_CATCHUP_EXECUTION_INVALID";
            return false;
        }
        const double signedQuantity = side == "BUY" ? event.qty : -event.qty;
        const double nextDelta =
            baseline->second.campaignExecutionDelta + signedQuantity;
        if (!std::isfinite(nextDelta) ||
            std::fabs(nextDelta) > context.maxGrossPosition + 1e-6 ||
            !std::isfinite(baseline->second.baselineCashBalance + nextDelta))
        {
            reason = "IB_FX_CASH_RESTART_CATCHUP_EXECUTION_INVALID";
            return false;
        }
        baseline->second.campaignExecutionDelta = nextDelta;
        baseline->second.observedCashBalance =
            baseline->second.baselineCashBalance + nextDelta;
        baseline->second.observedAtMs = std::max(
            baseline->second.observedAtMs,
            static_cast<std::uint64_t>(event.tsMs));
        completedOrders.insert(event.orderId);
    }

    for (const auto& item : submitted)
        if (item.second.accepted &&
            completedOrders.find(item.first) == completedOrders.end())
        {
            reason = "IB_FX_CASH_RESTART_CATCHUP_EXECUTION_INVALID";
            return false;
        }

    baselines.swap(advanced);
    reason.clear();
    return true;
}
