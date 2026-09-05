#include "ib_paper_execution_runtime_internal.h"
#include <cstdint>
#include <locale>
#include <map>
#include <set>
#include <sstream>
using namespace ib_paper_execution_runtime_internal;
void IbPaperExecutionRuntimeComposition::BuildCoordinator()
{
    ExecutionCoordinatorCallbacks callbacks;
    callbacks.preVenuePlaceCheck = [this](const IbPlaceOrderCommand&,
                                          std::string* detail) {
        std::string reason;
        const bool blocked = !AllowsRiskIncrease(reason);
        if (blocked && detail)
            *detail = reason.empty() ?
                "IB_PAPER_KILL_SWITCH_STATE_UNCERTAIN" : reason;
        return !blocked;
    };
    callbacks.preVenueFlattenCheck =
        [this](const FlattenPositionCommand&,
               const AuthoritativeFlattenPlan&, std::string* detail) {
            std::string reason;
            const bool blocked =
                !AllowsAuthoritativeFlatten(reason);
            if (blocked && detail)
                *detail = reason.empty() ?
                    "IB_PAPER_KILL_SWITCH_STATE_UNCERTAIN" : reason;
            return !blocked;
        };
    callbacks.placeIbOrderCommandCorrelated = [this](
        const IbPlaceOrderCommand& command,
        const std::string& correlation, long* orderId) {
        NotifyTestStage("before_venue_send");
        bool placed = false;
        {
            std::lock_guard<std::recursive_mutex> lock(m_authoritativeQuoteSendMutex);
            const AuthoritativePlaceQuoteBinding& quote = command.authoritativeQuoteBinding;
            IBFinalOrderSendContext context;
            context.authoritativeQuoteBound = quote.valid;
            context.instrument = quote.instrument;
            context.quoteSubscriptionId = quote.subscriptionId;
            context.quoteBid = quote.bid; context.quoteAsk = quote.ask;
            context.quoteObservedAtMs = quote.observedAtMs;
            context.quoteStaleAfterMs = quote.staleAfterMs;
            placed = m_adapter->PlaceOrderCorrelated(
                command.contract, command.order, correlation, orderId, &context);
        }
        if (placed) NotifyTestStage("after_venue_send");
        return placed;
    };
    callbacks.cancelIbOrder = [this](long orderId) {
        NotifyTestStage("before_cancel_venue_send");
        const bool cancelled = m_adapter->CancelOrder(orderId);
        if (cancelled) NotifyTestStage("after_cancel_venue_send");
        return cancelled;
    };
    callbacks.placeIbReduceOnlyOrderCorrelated =
        [this](const AuthoritativeFlattenPlan& plan,
               const std::string& correlation, long* orderId) {
            NotifyTestStage("before_flatten_venue_send");
            bool placed = false;
            {
            std::lock_guard<std::recursive_mutex> lock(m_authoritativeQuoteSendMutex);
                placed = m_adapter->PlaceReduceOnlyOrderCorrelated(
                    plan.contract, plan.order, plan.instrument,
                    plan.expectedPositionQuantity,
                    plan.positionConnectionEpoch,
                    plan.positionGeneration,
                    plan.quoteSubscriptionId,
                    plan.quoteObservedAtMs,
                    plan.quoteStaleAfterMs,
                    correlation, orderId,
                    plan.quoteBid, plan.quoteAsk);
            }
            if (placed) NotifyTestStage("after_flatten_venue_send");
            return placed;
        };
    callbacks.proveAndCommitIbFlatNoop =
        [this](const AuthoritativeFlattenPlan& plan,
               const std::function<bool()>& durableCommit,
               bool* commitAttempted,
               std::string* detail) {
            std::lock_guard<std::recursive_mutex> lock(m_authoritativeQuoteSendMutex);
            return m_adapter->ProveAndCommitFlatNoop(
                plan.instrument, plan.positionConnectionEpoch,
                plan.positionGeneration, durableCommit,
                commitAttempted, detail);
        };
    callbacks.canCancelIbOrder = [this](long orderId, std::string* detail) {
        return m_adapter->CanCancelOrder(orderId, detail);
    };
    callbacks.lastIbRejectReason = [this]() { return m_adapter->GetLastRejectReason(); };
    callbacks.validateDecisionLease = [this](const AgentExecutionContext& context,
        const std::string& instrument, std::string* detail) {
        return m_decisionLeases->Validate(context, instrument, detail);
    };
    callbacks.onIbOrderPlaced = [this](const IbPlaceOrderCommand& command,
        long orderId, std::string* reason) {
        // Record the local broker-API acceptance generation before publishing
        // it. A broker callback may race this hook; the generation-aware
        // projector preserves callback evidence from the same generation.
        std::string projectionReason;
        if (!ProjectAcceptedBrokerOrder(
                command, orderId, projectionReason))
        {
            if (reason) *reason = projectionReason;
            return false;
        }
        ExecutionEvent event;
        event.executionDomain = command.context.executionDomain;
        event.agentId = command.context.agentId;
        event.sessionId = command.context.sessionId;
        event.type = "order.accepted";
        event.venue = "IB";
        event.orderId = orderId;
        event.instrument = command.instrument;
        event.side = command.order.action;
        event.status = "Accepted";
        event.remainingQuantity = command.order.totalQuantity;
        return m_eventHub->Publish(event) != 0;
    };
    m_coordinator.reset(new ExecutionCoordinator(m_journal, callbacks));
    std::string recovery;
    if (!m_coordinator->RecoverFromJournal(recovery)) m_recoveryReason = recovery;
    NotifyTestStage("coordinator_ready");
}
IBAuthoritativeCorrelationSnapshot
IbPaperExecutionRuntimeComposition::PolicyCorrelationSnapshot() const
{
    // This callback is the active-order view. Terminal correlations are
    // supplied independently through terminalCorrelationSnapshot and are
    // merged by the policy authority only for uncertain-command resolution.
    // Mixing terminal ids into this map while leaving activeOrderIds pure
    // makes an otherwise flat owner audit internally contradictory.
    return m_adapter->GetAuthoritativeCorrelationSnapshot();
}
IBAuthoritativeRecoveryAuditSnapshot
IbPaperExecutionRuntimeComposition::PolicyRecoveryAuditSnapshot()
{
    std::lock_guard<std::recursive_mutex> lock(
        m_authoritativeQuoteSendMutex);
    IBAuthoritativeRecoveryAuditSnapshot snapshot =
        m_adapter->BeginRecoveryAuditBarrier();
    snapshot.postFillRiskReconciliationPending =
        snapshot.postFillRiskReconciliationPending ||
        m_postFillRiskRefreshPending.load();
    if (snapshot.newConnectionEpochRequired)
        m_recoveryAuditReconnectRequested.store(true);
    return snapshot;
}
IbPaperAuthoritativeRiskSnapshot
IbPaperExecutionRuntimeComposition::PolicyRiskSnapshot() const
{
    const IBAuthoritativeRiskSnapshot risk =
        m_adapter->GetAuthoritativeRiskSnapshot();
    const IBAuthoritativeCorrelationSnapshot correlations =
        m_adapter->GetAuthoritativeCorrelationSnapshot();
    IbPaperAuthoritativeRiskSnapshot result;
    result.complete = risk.accountComplete && risk.positionsComplete &&
        risk.fxCashComplete && correlations.complete &&
        !PostFillRiskRefreshPending();
    result.activeOrderCount = correlations.activeOrderIds.size();
    result.grossAbsolutePosition = risk.grossAbsolutePosition;
    return result;
}
IbPaperAuthoritativePositionSnapshot
IbPaperExecutionRuntimeComposition::PolicyPositionSnapshot(
    const std::string& instrument) const
{
    IbPaperAuthoritativePositionSnapshot result;
    const IBAuthoritativeRiskSnapshot risk =
        m_adapter->GetAuthoritativeRiskSnapshot();
    result.connectionEpoch = risk.connectionEpoch;
    result.generation = risk.positionsGeneration;
    const bool postFillPending = PostFillRiskRefreshPending();
    result.complete = risk.accountComplete && risk.positionsComplete &&
        risk.fxCashComplete && risk.connectionEpoch != 0 &&
        risk.positionsGeneration != 0 && !postFillPending;
    result.reasonCode = postFillPending ?
        "IB_POST_FILL_RISK_REFRESH_PENDING" : risk.reasonCode;
    if (!result.complete) return result;
    const std::map<std::string, InstrumentRef>::const_iterator contract =
        m_config.quoteContracts.find(instrument);
    if (contract == m_config.quoteContracts.end() ||
        !m_adapter->ResolveAuthoritativePositionQuantity(
            instrument, contract->second, result.quantity,
            result.reasonCode))
    {
        result.complete = false;
        if (result.reasonCode.empty())
            result.reasonCode = "IB_POSITION_CONTRACT_UNRESOLVED";
    }
    return result;
}
std::string IbPaperExecutionRuntimeComposition::OwnedOrdersJson(const AgentExecutionContext& context, const IBAuthoritativeCorrelationSnapshot& orders) const
{
    const ExecutionOwnedActiveOrderProjection projection = m_coordinator->ProjectOwnedActiveOrders(orders.activeOrderIds, context);
    const bool complete = orders.complete && projection.complete;
    const std::string reason = !orders.complete ? (orders.reasonCode.empty() ? "IB_ACTIVE_ORDER_SNAPSHOT_INCOMPLETE" : orders.reasonCode) : (!projection.complete ? "EXECUTION_ORDER_OWNER_PROJECTION_INCOMPLETE" : "");
    std::ostringstream output;
    output.imbue(std::locale::classic());
    output << "{\"source\":\"IB\",\"authoritative\":" << (complete ? "true" : "false") << ",\"active_orders_source\":\"IB_OPEN_ORDERS\",\"active_orders_connection_epoch\":" << orders.connectionEpoch << ",\"active_orders_generation\":" << orders.generation << ",\"global_active_orders_complete\":" << (orders.complete ? "true" : "false")
           << ",\"owner_projection_source\":\"EXECUTION_COORDINATOR_ORDER_OWNERS\",\"owner_projection_connection_epoch\":" << orders.connectionEpoch << ",\"owner_projection_generation\":" << orders.generation << ",\"owner_projection_complete\":" << (complete ? "true" : "false") << ",\"owned_active_order_ids_authoritative\":" << (complete ? "true" : "false")
           << ",\"owner_scope\":{\"agent_id\":\"" << EscapeJson(context.agentId) << "\",\"session_id\":\"" << EscapeJson(context.sessionId) << "\",\"execution_domain\":\"" << EscapeJson(context.executionDomain) << "\",\"account\":\"" << EscapeJson(context.account) << "\"},\"reason_code\":\"" << EscapeJson(reason) << "\"";
    const std::set<long>* lists[] = {&orders.activeOrderIds, &projection.ownedOrderIds, &projection.unmappedOrderIds};
    const char* names[] = {"active_order_ids", "owned_active_order_ids", "unmapped_active_order_ids"};
    for (std::size_t list = 0; list < 3; ++list)
    {
        output << ",\"" << names[list] << "\":[";
        std::size_t count = 0;
        for (std::set<long>::const_iterator it = lists[list]->begin(); it != lists[list]->end(); ++it, ++count)
        { if (count != 0) output << ','; output << *it; }
        output << ']';
    }
    output << ",\"recent_orders\":" << RecentBrokerOrdersJson(context) << '}';
    return output.str();
}
ExecutionCommandResult IbPaperExecutionRuntimeComposition::PolicyAuthoritativeRead(const ExecutionReadCommand& command) const
{
    ExecutionCommandResult result;
    result.commandId = command.context.toolCallId;
    result.status = ExecutionCommandStatus::Accepted;
    if (command.query == "system.get_health")
    {
        const std::uint32_t authorizedConnectorCount =
            m_adapter && m_adapter->IsConnected() ? 1U : 0U;
        std::ostringstream health;
        health.imbue(std::locale::classic());
        health << "{\"source\":\"IB\",\"authoritative\":true,"
               << "\"paper_order_mode\":\""
               << IbPaperExecutionProfileConfig::OrderModeName(
                      m_config.profile.orderMode)
               << "\",\"authorized_connector_count\":"
               << authorizedConnectorCount << '}';
        result.detail = health.str();
        return result;
    }
    const IBAuthoritativeRiskSnapshot risk =
        m_adapter->GetAuthoritativeRiskSnapshot();
    const IBAuthoritativeCorrelationSnapshot orders =
        m_adapter->GetAuthoritativeCorrelationSnapshot();
    const bool postFillPending = PostFillRiskRefreshPending();
    const std::string riskReason = postFillPending ?
        "IB_POST_FILL_RISK_REFRESH_PENDING" : risk.reasonCode;
    std::ostringstream output;
    output.imbue(std::locale::classic());
    if (command.query == "market.get_quote")
    {
        IBAuthoritativeQuoteSubscriptionHealth health;
        { std::lock_guard<std::recursive_mutex> quoteLock(m_authoritativeQuoteSendMutex);
          if (command.instrument.empty() || !m_quoteSubscriptions) {
              result.status = ExecutionCommandStatus::Rejected;
              result.reasonCode = "AUTHORITATIVE_QUOTE_UNAVAILABLE";
              return result;
          }
          health = m_quoteSubscriptions->GetHealth(); }
        const std::map<std::string,
            IBAuthoritativeQuoteContractHealth>::const_iterator subscription =
            health.contracts.find(command.instrument);
        if (subscription == health.contracts.end())
        {
            result.status = ExecutionCommandStatus::Rejected;
            result.reasonCode = "AUTHORITATIVE_QUOTE_UNAVAILABLE";
            return result;
        }
        if (!health.complete || !subscription->second.active ||
            !subscription->second.dispatchAccepted ||
            !HasPositiveTradableQuote(subscription->second.quote))
        {
            result.status = ExecutionCommandStatus::Rejected;
            result.reasonCode = "AUTHORITATIVE_QUOTE_UNREADY";
            return result;
        }
        const std::uint64_t now = NowEpochMs();
        std::uint64_t observedAtMs = 0;
        std::uint64_t staleAfterMs = 0;
        if (!FreshCompositeQuote(subscription->second.quote, now,
                m_config.quoteMaxAgeMs, observedAtMs, staleAfterMs))
        {
            result.status = ExecutionCommandStatus::Rejected;
            result.reasonCode = "AUTHORITATIVE_QUOTE_STALE";
            return result;
        }
        const AuthoritativeQuoteRecord quote =
            m_authoritativeSnapshots.GetQuote(
                command.instrument, now, m_config.quoteMaxAgeMs);
        if (quote.state.availability ==
            AuthoritativeSnapshotAvailability::Missing)
        {
            result.status = ExecutionCommandStatus::Rejected;
            result.reasonCode = "AUTHORITATIVE_QUOTE_UNREADY";
            return result;
        }
        if (quote.state.availability ==
            AuthoritativeSnapshotAvailability::Stale)
        {
            result.status = ExecutionCommandStatus::Rejected;
            result.reasonCode = "AUTHORITATIVE_QUOTE_STALE";
            return result;
        }
        output << "{\"source\":\"IB\",\"authoritative\":true,"
               << "\"instrument\":\""
               << EscapeJson(command.instrument) << "\","
               << "\"subscription_id\":\"IB:"
               << health.connectionEpoch << ':' << health.generation << ':'
               << subscription->second.requestId << "\","
               << "\"subscription_state\":\"active\","
               << "\"observed_at_ms\":" << observedAtMs << ','
               << "\"stale_after_ms\":" << staleAfterMs << ','
               << "\"stale\":false,\"bid\":"
               << subscription->second.quote.bid << ",\"ask\":"
               << subscription->second.quote.ask << "}";
    }
    else if (command.query == "account.get_summary")
    {
        const std::map<std::string, IBAuthoritativeFxCashExposure>
            exposures = m_adapter->GetAuthoritativeFxCashExposures();
        const bool complete = risk.accountComplete &&
            risk.fxCashComplete && !postFillPending;
        output << "{\"source\":\"IB\",\"authoritative\":"
               << (complete ? "true" : "false")
               << ",\"account_complete\":"
               << (risk.accountComplete ? "true" : "false")
               << ",\"fx_cash_complete\":"
               << (risk.fxCashComplete ? "true" : "false")
               << ",\"fx_cash_generation\":" << risk.fxCashGeneration
               << ",\"reason_code\":\"" << EscapeJson(riskReason)
               << "\",\"position_scope\":\"PAPER_BASELINE_DELTA\""
               << ",\"fx_cash_exposures\":[";
        std::size_t count = 0;
        for (std::map<std::string,
                 IBAuthoritativeFxCashExposure>::const_iterator it =
                 exposures.begin();
             it != exposures.end() && count < 64; ++it, ++count)
        {
            if (count != 0) output << ',';
            output << "{\"instrument\":\""
                   << EscapeJson(it->second.instrument)
                   << "\",\"base_currency\":\""
                   << EscapeJson(it->second.baseCurrency)
                   << "\",\"quote_currency\":\""
                   << EscapeJson(it->second.quoteCurrency)
                   << "\",\"current_cash_balance\":"
                   << it->second.currentCashBalance
                   << ",\"baseline_cash_balance\":"
                   << it->second.baselineCashBalance
                   << ",\"campaign_owned_quantity\":"
                   << it->second.campaignOwnedQuantity << "}";
        }
        output << "]}";
    }
    else if (command.query == "portfolio.list_positions")
    {
        const std::map<std::string, double> positions =
            m_adapter->GetAuthoritativePositionQuantities();
        const bool complete = risk.accountComplete &&
            risk.positionsComplete && risk.fxCashComplete &&
            !postFillPending;
        output << "{\"source\":\"IB\",\"authoritative\":"
               << (complete ? "true" : "false")
               << ",\"position_generation\":"
               << risk.positionsGeneration
               << ",\"fx_cash_generation\":" << risk.fxCashGeneration
               << ",\"reason_code\":\"" << EscapeJson(riskReason)
               << "\",\"position_scope\":\"PAPER_BASELINE_DELTA\""
               << ",\"positions\":[";
        std::size_t count = 0;
        for (std::map<std::string, double>::const_iterator it =
                 positions.begin();
             it != positions.end() && count < 64; ++it, ++count)
        {
            if (count != 0) output << ',';
            output << "{\"instrument\":\""
                   << EscapeJson(it->first) << "\",\"quantity\":"
                   << it->second << "}";
        }
        output << "]}";
    }
    else if (command.query == "orders.list")
        output << OwnedOrdersJson(command.context, orders);
    else if (command.query == "risk.get_limits")
    {
        output << "{\"source\":\"IB\",\"authoritative\":"
               << (risk.accountComplete && risk.positionsComplete &&
                   risk.fxCashComplete && !postFillPending ?
                   "true" : "false")
               << ",\"max_order_quantity\":"
               << m_config.profile.maxOrderQuantity
               << ",\"max_order_notional\":"
               << m_config.profile.maxOrderNotional
               << ",\"max_orders_per_minute\":"
               << m_config.profile.maxOrdersPerMinute
               << ",\"max_active_orders\":"
               << m_config.profile.maxActiveOrders
               << ",\"max_gross_position\":"
               << m_config.profile.maxGrossPosition
               << ",\"gross_absolute_position\":"
               << risk.grossAbsolutePosition
               << ",\"reason_code\":\"" << EscapeJson(riskReason)
               << "\",\"gross_scope\":\"PAPER_BASELINE_DELTA\"}";
    }
    else
    {
        result.status = ExecutionCommandStatus::Rejected;
        result.reasonCode = "AUTHORITATIVE_READ_QUERY_UNSUPPORTED";
        return result;
    }
    result.detail = output.str();
    return result;
}
bool IbPaperExecutionRuntimeComposition::BuildPolicyAuthority(
    std::string& reason)
{
    IbPaperExecutionPolicyCallbacks policyCallbacks;
    policyCallbacks.correlationSnapshot =
        [this]() { return PolicyCorrelationSnapshot(); };
    policyCallbacks.terminalCorrelationSnapshot = [this]() {
        return m_adapter->GetAuthoritativeTerminalCorrelationSnapshot();
    };
    policyCallbacks.recoveryAuditSnapshot = [this]() {
        return PolicyRecoveryAuditSnapshot();
    };
    BindTerminalPolicyCallbacks(policyCallbacks);
    policyCallbacks.riskSnapshot =
        [this]() { return PolicyRiskSnapshot(); };
    policyCallbacks.nowMs = []() {
        return static_cast<std::int64_t>(OmsJournal::NowEpochMs());
    };
    policyCallbacks.authoritativeContract =
        [this](const std::string& instrument, InstrumentRef& contract) {
            const std::map<std::string, InstrumentRef>::const_iterator found =
                m_config.quoteContracts.find(instrument);
            if (found == m_config.quoteContracts.end()) return false;
            contract = found->second;
            return true;
        };
    policyCallbacks.authoritativeQuote =
        [this](const std::string& instrument) {
            return AuthoritativeQuote(instrument);
        };
    policyCallbacks.authoritativePosition =
        [this](const std::string& instrument) {
            return PolicyPositionSnapshot(instrument);
        };
    policyCallbacks.authoritativeRead =
        [this](const ExecutionReadCommand& command) {
            return PolicyAuthoritativeRead(command);
        };
    m_policyAuthority.reset(new IbPaperExecutionPolicyAuthority(
        *m_coordinator, m_config.profile, policyCallbacks, m_killSwitch));
    std::size_t affected = 0;
    std::string recovery;
    if (!m_policyAuthority->ReconcileAuthoritativeState(affected, recovery) ||
        !RebuildRecentBrokerOrders(recovery))
    {
        reason = recovery;
        CloseUnconsumedListenFds();
        return false;
    }
    NotifyTestStage("policy_authority_ready");
    return true;
}
