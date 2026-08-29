#include "ib_paper_execution_profile.h"
#include "ib_paper_flatten_plan_binding.h"

#include <algorithm>
#include <cmath>
#include <iomanip>
#include <sstream>

namespace
{
bool ValidAuthoritativeContract(const InstrumentRef& contract)
{
    return !contract.symbol.empty() &&
        !contract.secType.empty() &&
        !contract.exchange.empty() &&
        !contract.currency.empty();
}

bool ValidateAuthoritativePositionSnapshot(
    const FlattenPositionCommand& command,
    const IbPaperAuthoritativePositionSnapshot& position,
    std::string& reason)
{
    if (!position.complete || position.connectionEpoch == 0 ||
        position.generation == 0 || !std::isfinite(position.quantity))
    {
        reason = position.reasonCode.empty() ?
            "IB_PAPER_POSITION_SNAPSHOT_INCOMPLETE" :
            position.reasonCode;
        return false;
    }
    if (!command.hasAuthoritativePreviewSnapshot)
        return true;

    if (!std::isfinite(command.previewPositionQuantity) ||
        command.previewPositionConnectionEpoch == 0 ||
        command.previewPositionGeneration == 0 ||
        position.connectionEpoch != command.previewPositionConnectionEpoch ||
        position.generation != command.previewPositionGeneration ||
        position.quantity != command.previewPositionQuantity)
    {
        reason = "IB_PAPER_FLATTEN_PREVIEW_SNAPSHOT_CHANGED";
        return false;
    }
    return true;
}

bool ResolveAuthoritativeFlattenInputs(
    const FlattenPositionCommand& command,
    std::int64_t nowMs,
    const IbPaperExecutionPolicyCallbacks& callbacks,
    InstrumentRef& authoritativeContract,
    IbPaperAuthoritativePositionSnapshot& position,
    std::string& reason)
{
    if (!callbacks.authoritativePosition || !callbacks.riskSnapshot ||
        !callbacks.authoritativeQuote || !callbacks.authoritativeContract)
    {
        reason = "IB_PAPER_FLATTEN_CALLBACKS_REQUIRED";
        return false;
    }
    if (command.instrument.empty() || nowMs < 0)
    {
        reason = "IB_PAPER_FLATTEN_INSTRUMENT_INVALID";
        return false;
    }
    if (!callbacks.authoritativeContract(
            command.instrument, authoritativeContract))
    {
        reason = "IB_PAPER_FLATTEN_INSTRUMENT_NOT_ALLOWLISTED";
        return false;
    }
    if (!ValidAuthoritativeContract(authoritativeContract))
    {
        reason = "IB_PAPER_FLATTEN_AUTHORITY_CONTRACT_INVALID";
        return false;
    }
    if (!SameIbPaperFlattenContract(
            command.contract, authoritativeContract))
    {
        reason = "IB_PAPER_FLATTEN_CONTRACT_MISMATCH";
        return false;
    }
    position = callbacks.authoritativePosition(command.instrument);
    return ValidateAuthoritativePositionSnapshot(command, position, reason);
}

bool ConfigureExternalLimitDayFlattenPlan(
    const IbPaperExecutionProfileConfig& config,
    const IbPaperAuthoritativePositionSnapshot& position,
    AuthoritativeFlattenPlan& plan,
    std::string& reason)
{
    plan.profileOrderMode =
        IbPaperExecutionProfileConfig::OrderModeName(config.orderMode);
    plan.order.orderType = "LMT";
    plan.timeInForce = "DAY";
    if (std::fabs(position.quantity) <= 1.0)
        return true;
    reason = "IB_PAPER_EXTERNAL_FLATTEN_POSITION_LIMIT_EXCEEDED";
    return false;
}

void BindAuthoritativeFlattenQuote(
    const MarketQuoteSnapshot& quote,
    bool externalLimitDay,
    AuthoritativeFlattenPlan& plan)
{
    plan.quoteSubscriptionId = quote.subscriptionId;
    plan.quoteObservedAtMs = quote.observedAtMs;
    plan.quoteStaleAfterMs = quote.staleAfterMs;
    if (externalLimitDay)
    {
        plan.quoteBid = quote.bid;
        plan.quoteAsk = quote.ask;
    }
}

void PopulateNonzeroFlattenOrder(
    const IbPaperAuthoritativePositionSnapshot& position,
    const MarketQuoteSnapshot& quote,
    bool externalLimitDay,
    double maxOrderQuantity,
    AuthoritativeFlattenPlan& plan)
{
    plan.order.action = position.quantity > 0.0 ? "SELL" : "BUY";
    if (externalLimitDay)
    {
        // External finalization is one atomic exact-position reduction. It
        // never chunks or crosses the freshly observed executable boundary.
        plan.order.totalQuantity = std::fabs(position.quantity);
        plan.order.lmtPrice = plan.order.action == "SELL" ?
            quote.bid : quote.ask;
        plan.referencePrice = plan.order.lmtPrice;
        return;
    }
    plan.order.orderType = "MKT";
    // A recovered exposure can exceed the normal per-order cap (for example
    // after an earlier position-model defect). Reduce it in bounded chunks;
    // every dispatch remains bound to the exact full pre-trade position and a
    // fresh generation, so it can never cross zero or increase exposure.
    plan.order.totalQuantity = std::min(
        std::fabs(position.quantity), maxOrderQuantity);
    plan.order.lmtPrice = 0.0;
    plan.timeInForce = "DAY";
    // MKT has no executable limit. Bind the conservative authoritative ask as
    // the pre-trade notional reference while preserving the exact quote
    // snapshot through the preview/dispatch fence.
    plan.referencePrice = quote.ask;
}
}

bool IbPaperExecutionPolicyAuthority::BuildAuthoritativeFlattenPlan(
    const FlattenPositionCommand& command,
    std::int64_t nowMs,
    AuthoritativeFlattenPlan& plan,
    std::string& reason)
{
    plan = AuthoritativeFlattenPlan();
    InstrumentRef authoritativeContract;
    IbPaperAuthoritativePositionSnapshot position;
    if (!ResolveAuthoritativeFlattenInputs(
            command, nowMs, m_callbacks,
            authoritativeContract, position, reason))
        return false;
    plan.contract = authoritativeContract;
    plan.instrument = command.instrument;
    plan.expectedPositionQuantity = position.quantity;
    plan.positionConnectionEpoch = position.connectionEpoch;
    plan.positionGeneration = position.generation;
    const bool externalLimitDay = m_config.UsesExternalLimitDay();
    if (externalLimitDay &&
        !ConfigureExternalLimitDayFlattenPlan(
            m_config, position, plan, reason))
        return false;
    if (position.quantity == 0.0 && !externalLimitDay)
    {
        if (!IbPaperFlattenPreviewPlanMatches(
                command, plan, reason))
            return false;
        reason.clear();
        return true;
    }

    PlaceOrderCommand quoteRequest;
    quoteRequest.instrument = command.instrument;
    MarketQuoteSnapshot quote;
    if (!ValidateFreshQuote(
            quoteRequest, nowMs, quote, reason))
        return false;
    BindAuthoritativeFlattenQuote(quote, externalLimitDay, plan);
    if (position.quantity != 0.0)
        PopulateNonzeroFlattenOrder(
            position, quote, externalLimitDay,
            m_maxOrderQuantity, plan);
    if (!IbPaperFlattenPreviewPlanMatches(
            command, plan, reason))
        return false;
    reason.clear();
    return true;
}

ExecutionCommandResult
IbPaperExecutionPolicyAuthority::PreviewFlattenPosition(
    const FlattenPositionCommand& command)
{
    std::lock_guard<std::mutex> lock(m_mutex);
    if (!ValidContext(command.context))
        return Reject(command.context, -1,
            "IB_PAPER_CONTEXT_REQUIRED");
    std::string blockedReason;
    if (m_coordinator.IsMutationBlocked(&blockedReason))
        return Reject(command.context, -1,
            blockedReason.empty() ? "MUTATION_BLOCKED" :
                blockedReason);
    if (m_coordinator.IsSessionOwnerFenced(
            command.context.agentId, command.context.sessionId))
        return Reject(command.context, -1,
            "SESSION_OWNER_FENCED");
    if (!m_callbacks.nowMs)
        return Reject(command.context, -1,
            "IB_PAPER_FLATTEN_CALLBACKS_REQUIRED");
    const std::int64_t now = m_callbacks.nowMs();
    AuthoritativeFlattenPlan plan;
    std::string reason;
    if (!BuildAuthoritativeFlattenPlan(
            command, now, plan, reason))
        return Reject(command.context, -1, reason);
    RefreshRateBudget(now, command.context.executionDomain);
    if (!m_guard.AllowFlatten(
            command, plan, m_callbacks.riskSnapshot(), now, reason))
        return Reject(command.context, -1, reason);

    ExecutionCommandResult result;
    result.status = ExecutionCommandStatus::Accepted;
    result.commandId = command.context.toolCallId;
    result.hasAuthoritativeFlattenSnapshot = true;
    result.authoritativeFlattenPositionQuantity = plan.expectedPositionQuantity;
    result.authoritativeFlattenConnectionEpoch =
        plan.positionConnectionEpoch;
    result.authoritativeFlattenPositionGeneration =
        plan.positionGeneration;
    result.authoritativeFlattenPlanBinding =
        CanonicalIbPaperFlattenPlanBinding(plan);
    std::ostringstream output;
    output << "{\"source\":\"IB\",\"authoritative\":true,"
           << "\"position_connection_epoch\":"
           << plan.positionConnectionEpoch
           << ",\"position_generation\":"
           << plan.positionGeneration
           << ",\"position_quantity\":" << std::setprecision(17)
           << plan.expectedPositionQuantity
           << ",\"side\":\"" << plan.order.action
           << "\",\"quantity\":" << plan.order.totalQuantity;
    if (m_config.UsesExternalLimitDay())
    {
        output << ",\"order_type\":\"LMT\""
               << ",\"tif\":\"DAY\""
               << ",\"limit_price\":" << plan.order.lmtPrice
               << ",\"reference_price\":" << plan.referencePrice
               << ",\"quote_bid\":" << plan.quoteBid
               << ",\"quote_ask\":" << plan.quoteAsk;
    }
    else
    {
        output << ",\"order_type\":\"MKT\""
               << ",\"reference_price\":" << plan.referencePrice;
    }
    output
           << ",\"quote_subscription_id\":\""
           << plan.quoteSubscriptionId
           << "\",\"quote_observed_at_ms\":"
           << plan.quoteObservedAtMs
           << ",\"reduce_only\":true";
    if (m_config.UsesExternalLimitDay())
        output << ",\"atomic\":true";
    output << ",\"risk_approved\":true}";
    result.detail = output.str();
    return result;
}

ExecutionCommandResult IbPaperExecutionPolicyAuthority::FlattenPosition(
    const FlattenPositionCommand& command)
{
    std::lock_guard<std::mutex> lock(m_mutex);
    if (!ValidContext(command.context))
        return Reject(command.context, -1,
            "IB_PAPER_MUTATION_CONTEXT_INVALID");
    ExecutionCommandResult prechecked;
    if (m_coordinator.PrecheckFlattenPosition(
            command, prechecked))
        return prechecked;
    if (!m_callbacks.nowMs)
        return Reject(command.context, -1,
            "IB_PAPER_FLATTEN_CALLBACKS_REQUIRED");
    if (!command.hasAuthoritativePreviewSnapshot)
        return Reject(command.context, -1,
            "IB_PAPER_FLATTEN_PREVIEW_BINDING_REQUIRED");
    const std::int64_t now = m_callbacks.nowMs();
    AuthoritativeFlattenPlan plan;
    std::string reason;
    if (!BuildAuthoritativeFlattenPlan(
            command, now, plan, reason))
        return Reject(command.context, -1, reason);
    RefreshRateBudget(now, command.context.executionDomain);
    if (!m_guard.AllowFlatten(
            command, plan, m_callbacks.riskSnapshot(), now, reason))
        return Reject(command.context, -1, reason);
    const ExecutionCommandResult result =
        m_coordinator.ExecuteAuthoritativeFlatten(command, plan);
    RefreshRateBudget(now, command.context.executionDomain);
    return result;
}

bool IbPaperExecutionPolicyAuthority::IsDurableFlattenReplay(
    const FlattenPositionCommand& command) const
{
    return m_coordinator.IsDurableFlattenReplay(command);
}
