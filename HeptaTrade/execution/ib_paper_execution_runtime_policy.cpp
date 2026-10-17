#include "ib_paper_execution_runtime_internal.h"
#include "ib_paper_account_broker_evidence.h"
#include <cstdint>
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
    callbacks.placement = VenuePlacement::Immediate([this](
        const IbPlaceOrderCommand& command,
        const std::string& correlation) {
        NotifyTestStage("before_venue_send");
        VenuePlaceResult outcome;
        {
            std::lock_guard<std::recursive_mutex> lock(m_authoritativeQuoteSendMutex);
            const AuthoritativePlaceQuoteBinding& quote = command.authoritativeQuoteBinding;
            IBFinalOrderSendContext context;
            context.timeInForce = command.timeInForce;
            context.authoritativeQuoteBound = quote.valid;
            context.instrument = quote.instrument;
            context.quoteSubscriptionId = quote.subscriptionId;
            context.quoteBid = quote.bid; context.quoteAsk = quote.ask;
            context.quoteObservedAtMs = quote.observedAtMs;
            context.quoteStaleAfterMs = quote.staleAfterMs;
            outcome = m_adapter->PlaceOrderWithResult(
                command.contract, command.order, correlation, &context);
        }
        if (outcome.disposition == VenuePlaceDisposition::Submitted) NotifyTestStage("after_venue_send");
        return outcome;
    });
    callbacks.cancelOrder = [this](long orderId) {
        if (m_startupRecoveryReadOnly.load())
            return VenueCancelResult::RejectedBeforeSend(StartupRecoveryReadOnlyReason());
        NotifyTestStage("before_cancel_venue_send");
        const VenueCancelResult outcome = m_adapter->CancelOrder(orderId);
        if (outcome.disposition == VenueCancelDisposition::Submitted)
            NotifyTestStage("after_cancel_venue_send");
        return outcome;
    };
    callbacks.flattenOrder =
        [this](const AuthoritativeFlattenPlan& plan, const std::string& correlation) {
            NotifyTestStage("before_flatten_venue_send");
            VenueFlattenResult outcome;
            {
                std::lock_guard<std::recursive_mutex> lock(m_authoritativeQuoteSendMutex);
                outcome = m_adapter->PlaceReduceOnlyOrderCorrelated(
                    plan.contract, plan.order, plan.instrument,
                    plan.expectedPositionQuantity,
                    plan.positionConnectionEpoch, plan.positionGeneration,
                    plan.quoteSubscriptionId, plan.quoteObservedAtMs,
                    plan.quoteStaleAfterMs, correlation, plan.quoteBid, plan.quoteAsk, plan.timeInForce);
            }
            if (outcome.disposition == VenueFlattenDisposition::Submitted)
                NotifyTestStage("after_flatten_venue_send");
            return outcome;
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
        if (m_startupRecoveryReadOnly.load()) {
            if (detail) *detail = StartupRecoveryReadOnlyReason();
            return false;
        }
        return m_adapter->CanCancelOrder(orderId, detail);
    };
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
bool IbPaperExecutionRuntimeComposition::RecoveryAuditEconomicPreflight(
    const IBAuthoritativeRecoveryAuditSnapshot& snapshot)
{
    bool safe = snapshot.risk.complete &&
        snapshot.risk.coherentRefreshComplete &&
        snapshot.risk.accountComplete && snapshot.risk.positionsComplete &&
        snapshot.risk.fxCashComplete &&
        snapshot.risk.grossAbsolutePosition == 0.0 &&
        snapshot.active.complete && snapshot.active.activeOrderIds.empty() &&
        snapshot.terminal.complete &&
        !snapshot.postFillRiskReconciliationPending &&
        snapshot.terminalExposureGeneration <=
            snapshot.riskAbsorbedExposureGeneration &&
        snapshot.riskAbsorbedExposureGeneration == snapshot.exposureGeneration;
    for (const auto& position : snapshot.positionQuantities)
        if (position.first.empty() || position.second != 0.0)
            safe = false;
    return safe;
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
    // BeginRecoveryAuditBarrier can require a new epoch before the policy
    // rejects a global owner audit for NONFLAT position. Never turn that
    // *rejected* probe into a background Broker disconnect/reconnect. The
    // complete, current, all-flat economic state must be proven BEFORE any
    // reconnect scheduling, not after the RPC has returned Rejected.
    if (snapshot.newConnectionEpochRequired &&
        RecoveryAuditEconomicPreflight(snapshot))
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
    result.complete = !m_startupRecoveryReadOnly.load() &&
        risk.accountComplete && risk.positionsComplete &&
        risk.fxCashComplete && correlations.complete &&
        !PostFillRiskRefreshPending();
    result.activeOrderCount = correlations.activeOrderIds.size();
    result.grossAbsolutePosition = risk.grossAbsolutePosition;
    if (m_config.profile.UsesUserCrypto())
    {
        result.instrumentPositions = m_adapter->GetAuthoritativePositionQuantities();
        result.instrumentPositionsComplete = result.complete;
        for (const auto& contract : m_config.quoteContracts)
        {
            double quantity = 0; std::string reason;
            if (!m_adapter->ResolveAuthoritativePositionQuantity(contract.first, contract.second, quantity, reason))
                result.instrumentPositionsComplete = false;
            else result.instrumentPositions[contract.first] = quantity;
        }
    }
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
    result.complete = !m_startupRecoveryReadOnly.load() &&
        risk.accountComplete && risk.positionsComplete &&
        risk.fxCashComplete && risk.connectionEpoch != 0 &&
        risk.positionsGeneration != 0 && !postFillPending;
    result.reasonCode = m_startupRecoveryReadOnly.load() ?
        StartupRecoveryReadOnlyReason() : (postFillPending ?
        "IB_POST_FILL_RISK_REFRESH_PENDING" : risk.reasonCode);
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
    output.precision(17);
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
    std::size_t totalRecent = 0, returnedRecent = 0;
    const std::string recent = RecentBrokerOrdersJson(context, &totalRecent, &returnedRecent);
    output << ",\"recent_orders\":" << recent
           << ",\"recent_orders_total\":" << totalRecent
           << ",\"recent_orders_returned\":" << returnedRecent
           << ",\"recent_orders_truncated\":" << (returnedRecent < totalRecent ? "true" : "false") << '}';
    return output.str();
}
ExecutionCommandResult IbPaperExecutionRuntimeComposition::PolicyAuthoritativeRead(const ExecutionReadCommand& command) const
{
    ExecutionCommandResult result;
    result.commandId = command.context.toolCallId;
    result.status = ExecutionCommandStatus::Accepted;
    if (command.query == "system.get_health")
    {
        // The post-fence state machine may advance a fresh Broker risk read
        // ONLY if the authenticated custodian already started it through the
        // original journal-durable account-fence control operation.
        const IBAccountPostFenceReadObservation postFence =
            m_config.profile.userScope && m_adapter ?
                m_adapter->PollAccountPostFenceRead() :
                IBAccountPostFenceReadObservation();
        const IBAuthoritativeRecoveryAuditSnapshot broker =
            m_config.profile.userScope && m_adapter ?
                m_adapter->GetAuthoritativeRecoveryAuditSnapshot() :
                IBAuthoritativeRecoveryAuditSnapshot();
        // For USER PAPER, the visible connector state is read under the
        // SAME original adapter lock as all Broker risk/order evidence.
        // External qualification retains its established response path.
        const std::uint32_t authorizedConnectorCount =
            m_config.profile.userScope ?
                (broker.transportConnected ? 1U : 0U) :
                (m_adapter && m_adapter->IsConnected() ? 1U : 0U);
        std::ostringstream health;
        health << "{\"source\":\"IB\",\"authoritative\":true,"
               << "\"paper_order_mode\":\""
               << IbPaperExecutionProfileConfig::OrderModeName(
                      m_config.profile.orderMode)
               << "\",\"authorized_connector_count\":"
               << authorizedConnectorCount;
        // Keep external-qualification health bytes unchanged. User PAPER adds
        // the exact authenticated owner's readiness, never a mere socket probe.
        if (m_config.profile.userScope)
        {
            // Read owner/account fence and every local venue in-flight flag
            // together under ONE original coordinator mutex. Previously
            // separate reads could advertise a false idle mixed generation.
            const ExecutionAccountLocalDrainObservation local = m_coordinator ?
                m_coordinator->ObserveAccountLocalDrain(command.context) :
                ExecutionAccountLocalDrainObservation();
            const bool fenced = !local.present || local.sessionOwnerFenced;
            const bool recoveryOnly = !local.present || local.sessionOwnerRecoveryOnly;
            const bool accountEntryFenced = !local.present || local.accountEntryFenced;
            std::string reason = local.mutationBlockReason;
            const bool startupReadOnly = m_startupRecoveryReadOnly.load();
            const bool blocked = !local.present || local.mutationBlocked || startupReadOnly;
            const bool riskDispatchInFlight =
                !local.present || local.riskMutationDispatchInFlight;
            std::string guardReason;
            const bool nativeNewRiskGuardBlocked = !m_killSwitch ||
                m_killSwitch->BlocksRiskIncrease(guardReason);
            // These three Broker views AND transport/stream provenance must
            // be sampled under one ORIGINAL adapter mutex. Independent risk,
            // active and terminal reads can mix incompatible generations
            // while the EReader applies callbacks.
            const auto& risk = broker.risk;
            const auto& orders = broker.active;
            const auto accountBroker = InspectIbPaperAccountBrokerEvidence(
                broker, m_config.profile.clientId);
            const bool snapshotsReady =
                broker.transportConnected && broker.eventStreamAuthoritative &&
                risk.complete && risk.coherentRefreshComplete &&
                risk.accountComplete && risk.positionsComplete && risk.fxCashComplete &&
                orders.complete && risk.connectionEpoch != 0 &&
                risk.connectionEpoch == orders.connectionEpoch &&
                !broker.postFillRiskReconciliationPending &&
                !PostFillRiskRefreshPending();
            if (startupReadOnly) reason = StartupRecoveryReadOnlyReason();
            else if (!local.present) reason = "NATIVE_ACCOUNT_LOCAL_OBSERVATION_UNAVAILABLE";
            else if (fenced) reason = "SESSION_OWNER_FENCED";
            else if (recoveryOnly) reason = "SESSION_RECOVERY_ONLY";
            else if (accountEntryFenced) reason = "ACCOUNT_NEW_ENTRY_FENCED";
            else if (blocked && reason.empty()) reason = "EXECUTION_MUTATION_BLOCKED";
            else if (nativeNewRiskGuardBlocked)
                reason = guardReason.empty() ? "IB_PAPER_KILL_SWITCH_STATE_UNCERTAIN" : guardReason;
            else if (riskDispatchInFlight) reason = "RISK_MUTATION_DISPATCH_IN_FLIGHT";
            else if (authorizedConnectorCount != 1 ||
                     !broker.transportConnected) reason = "IB_CONNECTION_UNAVAILABLE";
            else if (!snapshotsReady) reason = "AUTHORITATIVE_STATE_INCOMPLETE";
            health << ",\"startup_recovery_read_only\":" << (startupReadOnly ? "true" : "false")
                   << ",\"terminal_download_complete\":"
                   << (broker.terminal.complete ? "true" : "false")
                   << ",\"account_broker_sample_scope\":\"ORIGINAL_ADAPTER_MUTEX_NOT_POST_FENCE\""
                   << ",\"account_broker_sample_connected\":"
                   << (broker.transportConnected ? "true" : "false")
                   << ",\"account_broker_sample_event_stream_authoritative\":"
                   << (broker.eventStreamAuthoritative ? "true" : "false")
                   << ",\"account_broker_sample_coherent\":"
                   << (accountBroker.snapshotCoherent ? "true" : "false")
                   << ",\"account_broker_zero_observed\":"
                   << (accountBroker.brokerZeroObserved ? "true" : "false")
                   << ",\"account_broker_global_active_order_count\":"
                   << (accountBroker.snapshotCoherent ?
                       std::to_string(accountBroker.globalActiveOrders) : "null")
                   << ",\"account_broker_foreign_active_order_count\":"
                   << (accountBroker.snapshotCoherent ?
                       std::to_string(accountBroker.foreignActiveOrders) : "null")
                   << ",\"account_broker_reason\":\""
                   << accountBroker.reasonCode << "\""
                   << ",\"account_broker_post_fence_refresh_proven\":false"
                   << ",\"account_post_fence_read_started\":"
                   << (postFence.started ? "true" : "false")
                   << ",\"account_post_fence_active_end_received\":"
                   << (postFence.activeReadComplete ? "true" : "false")
                   << ",\"account_post_fence_risk_end_received\":"
                   << (postFence.riskReadComplete ? "true" : "false")
                   << ",\"account_post_fence_active_then_risk_observed\":"
                   << (postFence.activeThenRiskObserved ? "true" : "false")
                   << ",\"account_post_fence_zero_observed\":"
                   << (postFence.brokerZeroObserved ? "true" : "false")
                   << ",\"account_post_fence_stop_permitted\":false"
                   << ",\"account_post_fence_terminal_refreshed\":false"
                   << ",\"account_post_fence_connection_epoch\":"
                   << postFence.connectionEpoch
                   << ",\"account_post_fence_active_generation\":"
                   << postFence.activeGeneration
                   << ",\"account_post_fence_risk_generation\":"
                   << postFence.riskGeneration
                   << ",\"account_post_fence_global_active_count\":"
                   << (postFence.activeThenRiskObserved ?
                       std::to_string(postFence.globalActiveOrders) : "null")
                   << ",\"account_post_fence_reason\":\""
                   << EscapeJson(postFence.reasonCode) << "\""
                   << ",\"owner_fenced\":" << (fenced ? "true" : "false")
                   << ",\"owner_recovery_only\":" << (recoveryOnly ? "true" : "false")
                   << ",\"account_new_entry_fenced\":" << (accountEntryFenced ? "true" : "false")
                   << ",\"account_local_drain_scope\":\"COORDINATOR_HOT_ONLY\""
                   << ",\"account_local_drain_observation_present\":"
                   << (local.present ? "true" : "false")
                   << ",\"account_local_owned_orders\":" << local.ownedOrders
                   << ",\"account_local_uncertain_commands\":" << local.uncertainCommands
                   << ",\"account_local_unattributed_owned_orders\":"
                   << local.unattributedOwnedOrders
                   << ",\"account_local_unattributed_uncertain_commands\":"
                   << local.unattributedUncertainCommands
                   << ",\"account_local_venue_dispatches_in_flight\":"
                   << local.venueDispatchesInFlight
                   << ",\"account_local_hot_projection_quiescent\":"
                   << (local.localHotProjectionQuiescent ? "true" : "false")
                   << ",\"maintenance_stop_permitted\":false"
                   << ",\"mutation_blocked\":" << (blocked ? "true" : "false")
                   << ",\"risk_mutation_dispatch_in_flight\":"
                   << (riskDispatchInFlight ? "true" : "false")
                   << ",\"native_new_risk_guard_blocked\":"
                   << (nativeNewRiskGuardBlocked ? "true" : "false")
                   << ",\"mutation_ready\":"
                   << (!fenced && !recoveryOnly && !accountEntryFenced && !blocked &&
                       !riskDispatchInFlight &&
                       !nativeNewRiskGuardBlocked && snapshotsReady &&
                       authorizedConnectorCount == 1 ? "true" : "false")
                   << ",\"active_lease_continuation_supported\":" << (m_config.profile.UsesUserCrypto() ? "true" : "false")
                   << ",\"mutation_reason\":\"" << EscapeJson(reason)
                   << "\",\"preview_required\":true";
        }
        health << '}';
        result.detail = health.str();
        return result;
    }
    const IBAuthoritativeRiskSnapshot risk =
        m_adapter->GetAuthoritativeRiskSnapshot();
    const IBAuthoritativeCorrelationSnapshot orders =
        m_adapter->GetAuthoritativeCorrelationSnapshot();
    const bool postFillPending = PostFillRiskRefreshPending();
    const std::string riskReason = m_startupRecoveryReadOnly.load() ?
        StartupRecoveryReadOnlyReason() : (postFillPending ?
        "IB_POST_FILL_RISK_REFRESH_PENDING" : risk.reasonCode);
    std::ostringstream output;
    output.precision(17);
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
        if ((!health.complete && !health.independentQuotes) || !subscription->second.active ||
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
        output << ']';
        if (m_config.profile.UsesUserCrypto() && m_startupFxCashMismatchReadOnly.load() &&
            command.context.account == m_config.profile.account &&
            command.context.executionDomain == "PAPER") {
            const auto physical=m_adapter->GetPhysicalCryptoPositionObservation();
            output << ",\"settlement_observation\":{\"source\":\"IB_REQ_POSITIONS\","
                   << "\"authoritative\":" << (physical.complete ? "true" : "false")
                   << ",\"scope\":\"PHYSICAL_CRYPTO_ONLY\",\"mutation_authorized\":false"
                   << ",\"account\":\"" << EscapeJson(physical.account) << "\""
                   << ",\"execution_service_epoch\":\"" << EscapeJson(m_serviceIdentity.serviceEpoch) << "\""
                   << ",\"connection_epoch\":" << physical.connectionEpoch
                   << ",\"position_generation\":" << physical.positionGeneration
                   << ",\"active_orders_generation\":" << physical.activeGeneration
                   << ",\"terminal_generation\":" << physical.terminalGeneration
                   << ",\"sampled_at_ms\":" << NowEpochMs()
                   << ",\"positions\":[";
            std::size_t physicalCount=0;
            for (const auto& position : physical.positions) {
                if (physicalCount++) output << ',';
                output << "{\"instrument\":\"" << EscapeJson(position.first)
                       << "\",\"quantity\":" << position.second << '}';
            }
            output << "]}";
        }
        output << '}';
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
               << ",\"mixed_asset_units\":" << (m_config.profile.UsesUserCrypto() ? "true" : "false")
               << ",\"crypto_quantity_limit\":" << (m_config.profile.UsesUserCrypto() ? IbPaperExecutionProfileConfig::CryptoQuantityLimit() : 0.0)
               << ",\"crypto_notional_limit_usd\":" << (m_config.profile.UsesUserCrypto() ? IbPaperExecutionProfileConfig::CryptoNotionalLimit() : 0.0)
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
    policyCallbacks.leaseAuditSnapshot = [this]() {
        std::lock_guard<std::recursive_mutex> lock(m_authoritativeQuoteSendMutex);
        auto snapshot = m_adapter->GetAuthoritativeRecoveryAuditSnapshot();
        snapshot.postFillRiskReconciliationPending = snapshot.postFillRiskReconciliationPending || PostFillRiskRefreshPending();
        return snapshot;
    };
    policyCallbacks.recoveryAuditSnapshot = [this]() {
        return PolicyRecoveryAuditSnapshot();
    };
    policyCallbacks.beginAccountPostFenceBrokerRead =
        [this](const AgentExecutionContext& context, std::string& reason) {
        if (!m_config.profile.userScope ||
            !m_config.profile.UsesUserCrypto() ||
            context.account != m_config.profile.account ||
            context.agentId != m_config.profile.userCryptoAgentId ||
            context.sessionId != m_config.profile.userCryptoSessionId ||
            context.executionDomain != "PAPER" || context.venue != "IB" ||
            !m_adapter || !m_coordinator) {
            reason = "IB_ACCOUNT_POST_FENCE_OWNER_UNAUTHORIZED";
            return false;
        }
        const auto local = m_coordinator->ObserveAccountLocalDrain(context);
        if (!local.present || !local.localHotProjectionQuiescent ||
            m_reconnectPending.load() || m_startupRecoveryReadOnly.load() ||
            PostFillRiskRefreshPending()) {
            reason = "IB_ACCOUNT_POST_FENCE_LOCAL_DRAIN_INCOMPLETE";
            return false;
        }
        std::string guardReason;
        if (!m_killSwitch || !m_killSwitch->BlocksRiskIncrease(guardReason)) {
            reason = "IB_ACCOUNT_POST_FENCE_INDEPENDENT_RISK_HOLD_REQUIRED";
            return false;
        }
        // Never substitute an old snapshot for the new request. The fresh
        // global order read itself is sent only AFTER original OMS fsync.
        if (!m_adapter->BeginAccountPostFenceRead(reason)) return false;
        // A Cancel/Flatten may have entered the venue during the gap between
        // the first original coordinator observation and the IB read issue.
        // Recheck; if in doubt keep the durable account fence and revoke the
        // observation. No Broker order, cancel or request retry is made.
        const auto after = m_coordinator->ObserveAccountLocalDrain(context);
        if (!after.present || !after.localHotProjectionQuiescent) {
            reason = "IB_ACCOUNT_POST_FENCE_LOCAL_DRAIN_CHANGED";
            m_adapter->InvalidateAccountPostFenceRead(reason);
            return false;
        }
        return true;
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
    const bool reconciled = !m_startupRecoveryReadOnly.load() &&
        m_policyAuthority->ReconcileAuthoritativeState(affected, recovery);
    if (!reconciled && !m_startupRecoveryReadOnly.load() && recovery != "RECOVERY_RECONCILE_REQUIRED")
    {
        reason = recovery;
        CloseUnconsumedListenFds();
        return false;
    }
    std::string rebuildReason;
    if (!RebuildRecentBrokerOrders(rebuildReason))
    {
        reason = rebuildReason;
        CloseUnconsumedListenFds();
        return false;
    }
    // A replayed uncertain command is a mutation fence, not a reason to make
    // the read/recovery service unavailable. New risk remains blocked; owned
    // cancels can use the coordinator recovery reserve to drain active risk.
    recovery.clear();
    NotifyTestStage("policy_authority_ready");
    return true;
}
