#include "ib_paper_execution_profile.h"

#include <algorithm>
#include <cmath>

namespace
{
bool IsPaperExecutionDomain(const std::string& executionDomain)
{
    return executionDomain == "PAPER" ||
        (executionDomain.size() > 6 &&
         executionDomain.compare(0, 6, "PAPER:") == 0);
}

bool FlattenContextMatches(
    const FlattenPositionCommand& command,
    const IbPaperExecutionProfileConfig& config,
    std::int64_t nowMs)
{
    return config.enabled &&
        command.context.account == config.account &&
        command.context.venue == "IB" &&
        IsPaperExecutionDomain(command.context.executionDomain) &&
        !command.context.allowCancelAny && nowMs >= 0;
}

bool ValidExternalFlattenEnvelope(
    const AuthoritativeFlattenPlan& plan,
    const IbPaperExecutionProfileConfig& config,
    std::int64_t nowMs)
{
    return plan.profileOrderMode ==
            IbPaperExecutionProfileConfig::OrderModeName(
                IbPaperOrderMode::ExternalLimitDay) &&
        plan.timeInForce == "DAY" &&
        plan.order.orderType == "LMT" &&
        plan.order.auxPrice == 0.0 &&
        !plan.order.outsideRth &&
        plan.order.orderRef.empty() &&
        !plan.quoteSubscriptionId.empty() &&
        plan.quoteObservedAtMs > 0 &&
        plan.quoteObservedAtMs <= static_cast<std::uint64_t>(nowMs) &&
        plan.quoteStaleAfterMs >= static_cast<std::uint64_t>(nowMs) &&
        plan.quoteStaleAfterMs >= plan.quoteObservedAtMs &&
        plan.quoteStaleAfterMs - plan.quoteObservedAtMs <=
            config.externalQuoteMaxAgeMs &&
        std::isfinite(plan.quoteBid) && plan.quoteBid > 0.0 &&
        std::isfinite(plan.quoteAsk) &&
        plan.quoteAsk >= plan.quoteBid;
}

bool ExactReduceOnlyQuantity(
    const AuthoritativeFlattenPlan& plan,
    bool externalLimitDay)
{
    const double position = plan.expectedPositionQuantity;
    const double quantity = plan.order.totalQuantity;
    const bool opposite =
        (position > 0.0 && plan.order.action == "SELL") ||
        (position < 0.0 && plan.order.action == "BUY");
    return opposite && std::isfinite(quantity) && quantity > 0.0 &&
        quantity <= std::fabs(position) &&
        (!externalLimitDay || quantity == std::fabs(position));
}

bool ValidExternalFlattenOrder(
    const AuthoritativeFlattenPlan& plan,
    bool validExternalEnvelope)
{
    return validExternalEnvelope &&
        std::isfinite(plan.order.lmtPrice) &&
        plan.order.lmtPrice > 0.0 &&
        std::isfinite(plan.referencePrice) &&
        plan.referencePrice == plan.order.lmtPrice &&
        ((plan.order.action == "SELL" &&
          plan.order.lmtPrice == plan.quoteBid) ||
         (plan.order.action == "BUY" &&
          plan.order.lmtPrice == plan.quoteAsk));
}

bool ValidLocalFlattenOrder(const AuthoritativeFlattenPlan& plan)
{
    return plan.profileOrderMode.empty() &&
        plan.timeInForce == "DAY" &&
        plan.order.orderType == "MKT" &&
        plan.order.lmtPrice == 0.0 &&
        std::isfinite(plan.referencePrice) &&
        plan.referencePrice > 0.0;
}
}

bool IbPaperExecutionGuard::AllowFlatten(
    const FlattenPositionCommand& command,
    const AuthoritativeFlattenPlan& plan,
    const IbPaperAuthoritativeRiskSnapshot& risk,
    std::int64_t nowMs,
    std::string& reason)
{
    std::lock_guard<std::mutex> lock(m_mutex);
    if (!FlattenContextMatches(command, m_config, nowMs))
    {
        reason = "IB_PAPER_FLATTEN_CONTEXT_MISMATCH";
        return false;
    }
    if (!std::isfinite(plan.expectedPositionQuantity))
    {
        reason = "IB_PAPER_POSITION_SNAPSHOT_INVALID";
        return false;
    }
    const bool externalLimitDay = m_config.UsesExternalLimitDay();
    if (externalLimitDay &&
        std::fabs(plan.expectedPositionQuantity) > 1.0)
    {
        reason = "IB_PAPER_EXTERNAL_FLATTEN_POSITION_LIMIT_EXCEEDED";
        return false;
    }
    if (!risk.complete)
    {
        reason = "IB_PAPER_AUTHORITATIVE_RISK_SNAPSHOT_INCOMPLETE";
        return false;
    }
    if (risk.activeOrderCount != 0)
    {
        reason = "IB_PAPER_FLATTEN_ACTIVE_ORDERS_PRESENT";
        return false;
    }
    if (!m_killSwitch)
    {
        reason = "IB_PAPER_KILL_SWITCH_READER_REQUIRED";
        return false;
    }
    const IbPaperKillSwitchObservation observation =
        m_killSwitch->Observe();
    if (observation.state == IbPaperKillSwitchState::Uncertain)
    {
        reason = observation.reasonCode.empty() ?
            "IB_PAPER_KILL_SWITCH_STATE_UNCERTAIN" :
            observation.reasonCode;
        return false;
    }
    const bool validExternalEnvelope = externalLimitDay &&
        ValidExternalFlattenEnvelope(plan, m_config, nowMs);
    if (plan.expectedPositionQuantity == 0.0)
    {
        if (externalLimitDay &&
            (!validExternalEnvelope || !plan.order.action.empty() ||
             plan.order.totalQuantity != 0.0 ||
             plan.order.lmtPrice != 0.0 ||
             plan.referencePrice != 0.0))
        {
            reason = "IB_PAPER_FLATTEN_ORDER_INVALID";
            return false;
        }
        reason.clear();
        return true;
    }
    const double quantity = plan.order.totalQuantity;
    if (!ExactReduceOnlyQuantity(plan, externalLimitDay))
    {
        reason = "IB_PAPER_FLATTEN_NOT_EXACT_REDUCE_ONLY";
        return false;
    }
    const bool validExternalOrder = externalLimitDay &&
        ValidExternalFlattenOrder(plan, validExternalEnvelope);
    const bool validLocalOrder = !externalLimitDay &&
        ValidLocalFlattenOrder(plan);
    if (!validExternalOrder && !validLocalOrder)
    {
        reason = "IB_PAPER_FLATTEN_ORDER_INVALID";
        return false;
    }
    if (quantity > m_config.maxOrderQuantity)
    {
        reason = "IB_PAPER_MAX_ORDER_QUANTITY_EXCEEDED";
        return false;
    }
    if (plan.referencePrice >
        m_config.maxOrderNotional / quantity)
    {
        reason = "IB_PAPER_MAX_ORDER_NOTIONAL_EXCEEDED";
        return false;
    }
    // Entry send attempts are deliberately not a blocker for exact
    // authoritative reduce-only liquidation.  The gateway applies a separate
    // bounded emergency-reduction budget, while this guard still enforces the
    // PAPER account, fresh authoritative position, zero active orders,
    // opposite side, MKT type and no-overshoot constraints above.
    reason.clear();
    return true;
}
