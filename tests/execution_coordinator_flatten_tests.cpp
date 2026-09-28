#include "execution_coordinator_test_support.h"

namespace hepta_execution_coordinator_test {

void TestFlattenLifecycleRecoveryAndRateProjection()
{
    const std::string path = TempJournalPath();
    OmsJournal journal;
    assert(journal.Init(path));
    int venueSends = 0;
    ExecutionCoordinatorCallbacks callbacks;
    callbacks.validateDecisionLease =
        [](const AgentExecutionContext&, const std::string&,
           std::string*) { return true; };
    callbacks.flattenOrder =
        [&](const AuthoritativeFlattenPlan&, const std::string&) {
            ++venueSends;
            return VenueFlattenResult::Submitted(880);
        };
    callbacks.proveAndCommitIbFlatNoop =
        [](const AuthoritativeFlattenPlan&,
           const std::function<bool()>& commit,
           bool* attempted, std::string*) {
            *attempted = true;
            return commit();
        };
    callbacks.onIbOrderPlaced =
        [](const IbPlaceOrderCommand&, long, std::string*) {
            return true;
        };
    ExecutionCoordinator coordinator(journal, callbacks);

    const FlattenPositionCommand sentCommand =
        MakeFlatten("flatten-recovery-sent");
    const AuthoritativeFlattenPlan sentPlan =
        MakeFlattenPlan(sentCommand);
    const ExecutionCommandResult sent =
        coordinator.ExecuteAuthoritativeFlatten(
            sentCommand, sentPlan);
    assert(sent.status == ExecutionCommandStatus::Accepted);
    assert(sent.orderId == 880);
    assert(venueSends == 1);

    const FlattenPositionCommand noopCommand =
        MakeFlatten("flatten-recovery-noop", 0.0);
    const AuthoritativeFlattenPlan noopPlan =
        MakeFlattenPlan(noopCommand, 0.0);
    const ExecutionCommandResult noop =
        coordinator.ExecuteAuthoritativeFlatten(
            noopCommand, noopPlan);
    assert(noop.status == ExecutionCommandStatus::Accepted);
    assert(noop.reasonCode == "POSITION_ALREADY_FLAT");
    assert(venueSends == 1);

    const FlattenPositionCommand rejectCommand =
        MakeFlatten("flatten-recovery-reject");
    AuthoritativeFlattenPlan rejectPlan =
        MakeFlattenPlan(rejectCommand);
    rejectPlan.order.action = "BUY";
    const ExecutionCommandResult rejected =
        coordinator.ExecuteAuthoritativeFlatten(
            rejectCommand, rejectPlan);
    assert(rejected.status == ExecutionCommandStatus::Rejected);
    assert(rejected.reasonCode ==
           "AUTHORITATIVE_FLATTEN_PLAN_INVALID");
    assert(venueSends == 1);

    int intents = 0;
    int attempts = 0;
    int receipts = 0;
    int noops = 0;
    int rejects = 0;
    assert(journal.Replay([&](const OmsJournalEvent& event) {
        if (event.eventType == "flatten_intent") ++intents;
        if (event.eventType == "flatten_send_attempt") ++attempts;
        if (event.eventType == "flatten_sent") ++receipts;
        if (event.eventType == "flatten_noop") ++noops;
        if (event.eventType == "flatten_reject") ++rejects;
    }) >= 0);
    assert(intents == 2);
    assert(attempts == 1);
    assert(receipts == 1);
    assert(noops == 1);
    assert(rejects == 1);

    ExecutionCoordinator recovered(journal, callbacks);
    std::string reason;
    assert(recovered.RecoverFromJournal(reason));
    ExecutionCommandResult status;
    assert(recovered.GetCommandStatus(
        "agent-a", "session-1", "flatten-recovery-sent", status));
    assert(status.status == ExecutionCommandStatus::Accepted);
    assert(status.orderId == 880);
    assert(recovered.GetCommandStatus(
        "agent-a", "session-1", "flatten-recovery-noop", status));
    assert(status.status == ExecutionCommandStatus::Accepted);
    assert(status.reasonCode == "POSITION_ALREADY_FLAT");
    assert(recovered.GetCommandStatus(
        "agent-a", "session-1", "flatten-recovery-reject", status));
    assert(status.status == ExecutionCommandStatus::Rejected);
    assert(status.reasonCode ==
           "AUTHORITATIVE_FLATTEN_PLAN_INVALID");
    ExecutionOrderOwner owner;
    assert(recovered.GetOrderOwner(880, owner));
    assert(owner.agentId == "agent-a");
    assert(owner.executionDomain == "PAPER");
    assert(owner.instrument == "EUR.USD");
    assert(owner.side == "SELL");
    std::vector<std::int64_t> rateAttempts;
    recovered.GetPlaceSendAttemptTimes(
        "DU123", "PAPER", 0, rateAttempts);
    assert(rateAttempts.size() == 1);
    assert(recovered.IsDurableFlattenReplay(sentCommand));
    assert(recovered.IsDurableFlattenReplay(noopCommand));
    assert(recovered.IsDurableFlattenReplay(rejectCommand));
    const ExecutionCommandResult duplicate =
        recovered.ExecuteAuthoritativeFlatten(
            sentCommand, sentPlan);
    assert(duplicate.status == ExecutionCommandStatus::Duplicate);
    assert(duplicate.orderId == 880);
    const ExecutionCommandResult rejectedDuplicate =
        recovered.ExecuteAuthoritativeFlatten(
            rejectCommand, rejectPlan);
    assert(rejectedDuplicate.status ==
        ExecutionCommandStatus::Duplicate);
    assert(rejectedDuplicate.orderId == -1);
    assert(venueSends == 1);
    std::remove(path.c_str());
}

void TestQualificationExternalFlattenIsExactAndAbsolutelyBounded()
{
    const std::string path = TempJournalPath();
    OmsJournal journal;
    assert(journal.Init(path));
    int venueSends = 0;
    ExecutionCoordinatorCallbacks callbacks;
    callbacks.validateDecisionLease =
        [](const AgentExecutionContext&, const std::string&,
           std::string*) { return true; };
    callbacks.flattenOrder =
        [&](const AuthoritativeFlattenPlan&, const std::string&) {
            ++venueSends;
            return VenueFlattenResult::Submitted(1880);
        };
    callbacks.onIbOrderPlaced =
        [](const IbPlaceOrderCommand&, long, std::string*) {
            return true;
        };
    ExecutionCoordinator coordinator(journal, callbacks);

    const double position = 250000.0;
    const FlattenPositionCommand command =
        MakeFlatten("qualification-external-flatten", position);
    AuthoritativeFlattenPlan plan = MakeFlattenPlan(command, position);
    plan.profileOrderMode = "EXTERNAL_QUALIFICATION_LMT_DAY";
    plan.order.orderType = "LMT";
    plan.order.lmtPrice = 1.10;
    plan.referencePrice = 1.10;
    plan.quoteBid = 1.10;
    plan.quoteAsk = 1.11;
    const ExecutionCommandResult accepted =
        coordinator.ExecuteAuthoritativeFlatten(command, plan);
    assert(accepted.status == ExecutionCommandStatus::Accepted);
    assert(accepted.orderId == 1880);
    assert(venueSends == 1);

    const double oversizedPosition = 1000001.0;
    const FlattenPositionCommand oversizedCommand =
        MakeFlatten("qualification-external-flatten-oversized",
                    oversizedPosition);
    AuthoritativeFlattenPlan oversizedPlan =
        MakeFlattenPlan(oversizedCommand, oversizedPosition);
    oversizedPlan.profileOrderMode =
        "EXTERNAL_QUALIFICATION_LMT_DAY";
    oversizedPlan.order.orderType = "LMT";
    oversizedPlan.order.lmtPrice = 1.10;
    oversizedPlan.referencePrice = 1.10;
    oversizedPlan.quoteBid = 1.10;
    oversizedPlan.quoteAsk = 1.11;
    const ExecutionCommandResult rejected =
        coordinator.ExecuteAuthoritativeFlatten(
            oversizedCommand, oversizedPlan);
    assert(rejected.status == ExecutionCommandStatus::Rejected);
    assert(rejected.reasonCode ==
        "AUTHORITATIVE_FLATTEN_PLAN_INVALID");
    assert(venueSends == 1);
    std::remove(path.c_str());
}

void TestFlattenRejectsSubToleranceOverCloseAndStaleNoop()
{
    const std::string path = TempJournalPath();
    OmsJournal journal;
    assert(journal.Init(path));
    int venueSends = 0;
    bool proveFlat = false;
    std::string proveReason =
        "IB_FLATTEN_POSITION_CHANGED_BEFORE_NOOP";
    ExecutionCoordinatorCallbacks callbacks;
    callbacks.validateDecisionLease =
        [](const AgentExecutionContext&, const std::string&,
           std::string*) { return true; };
    callbacks.flattenOrder =
        [&](const AuthoritativeFlattenPlan&, const std::string&) {
            ++venueSends;
            return VenueFlattenResult::Submitted(1881);
        };
    callbacks.proveAndCommitIbFlatNoop =
        [&](const AuthoritativeFlattenPlan&,
            const std::function<bool()>& commit,
            bool* attempted, std::string* reason) {
            if (!proveFlat && reason != nullptr)
                *reason = proveReason;
            if (!proveFlat) return false;
            *attempted = true;
            return commit();
        };
    ExecutionCoordinator coordinator(journal, callbacks);

    const FlattenPositionCommand tinyCommand =
        MakeFlatten("flatten-sub-tolerance-over-close", 1e-14);
    AuthoritativeFlattenPlan tinyPlan =
        MakeFlattenPlan(tinyCommand, 1e-14);
    tinyPlan.order.totalQuantity = 1e-13;
    const ExecutionCommandResult overClose =
        coordinator.ExecuteAuthoritativeFlatten(
            tinyCommand, tinyPlan);
    assert(overClose.status ==
           ExecutionCommandStatus::Rejected);
    assert(overClose.reasonCode ==
           "AUTHORITATIVE_FLATTEN_PLAN_INVALID");
    assert(venueSends == 0);

    const FlattenPositionCommand staleNoopCommand =
        MakeFlatten("flatten-stale-noop", 0.0);
    const ExecutionCommandResult staleNoop =
        coordinator.ExecuteAuthoritativeFlatten(
            staleNoopCommand,
            MakeFlattenPlan(staleNoopCommand, 0.0));
    assert(staleNoop.status ==
           ExecutionCommandStatus::Rejected);
    assert(staleNoop.reasonCode ==
           "IB_FLATTEN_POSITION_CHANGED_BEFORE_NOOP");

    proveReason =
        "IB_FLATTEN_ACTIVE_ORDER_SNAPSHOT_UNSAFE";
    const FlattenPositionCommand activeOrderCommand =
        MakeFlatten("flatten-active-order-noop", 0.0);
    const ExecutionCommandResult activeOrder =
        coordinator.ExecuteAuthoritativeFlatten(
            activeOrderCommand,
            MakeFlattenPlan(activeOrderCommand, 0.0));
    assert(activeOrder.status ==
           ExecutionCommandStatus::Rejected);
    assert(activeOrder.reasonCode ==
           "IB_FLATTEN_ACTIVE_ORDER_SNAPSHOT_UNSAFE");

    proveFlat = true;
    const FlattenPositionCommand provedCommand =
        MakeFlatten("flatten-atomic-proved-noop", 0.0);
    const ExecutionCommandResult proved =
        coordinator.ExecuteAuthoritativeFlatten(
            provedCommand,
            MakeFlattenPlan(provedCommand, 0.0));
    assert(proved.status ==
           ExecutionCommandStatus::Accepted);
    assert(proved.reasonCode == "POSITION_ALREADY_FLAT");
    assert(venueSends == 0);
    std::remove(path.c_str());
}

void TestFlattenNoopJournalFailureIsUncertain()
{
    const std::string path = TempJournalPath();
    const std::string moved = path + ".moved";
    OmsJournal journal;
    assert(journal.Init(path));
    ExecutionCoordinatorCallbacks callbacks;
    callbacks.validateDecisionLease =
        [](const AgentExecutionContext&, const std::string&,
           std::string*) { return true; };
    callbacks.proveAndCommitIbFlatNoop =
        [&](const AuthoritativeFlattenPlan&,
            const std::function<bool()>& commit,
            bool* attempted, std::string*) {
            assert(std::rename(
                path.c_str(), moved.c_str()) == 0);
            *attempted = true;
            return commit();
        };
    ExecutionCoordinator coordinator(journal, callbacks);
    const FlattenPositionCommand command =
        MakeFlatten("flatten-noop-journal-failure", 0.0);
    const ExecutionCommandResult result =
        coordinator.ExecuteAuthoritativeFlatten(
            command, MakeFlattenPlan(command, 0.0));
    assert(result.status ==
           ExecutionCommandStatus::Uncertain);
    assert(result.reasonCode ==
           "OMS_FLATTEN_NOOP_WRITE_FAILED");
    std::string blockedReason;
    assert(coordinator.IsMutationBlocked(&blockedReason));
    assert(blockedReason ==
           "OMS_FLATTEN_NOOP_WRITE_FAILED");
    std::remove(path.c_str());
    std::remove(moved.c_str());
}

void TestFlattenVenueRejectCodeAllowlist()
{
    const std::string path = TempJournalPath();
    OmsJournal journal;
    assert(journal.Init(path));
    int venueCalls = 0;
    std::string venueReason =
        "IB_PAPER_FLATTEN_QUOTE_CHANGED_BEFORE_SEND suffix";
    ExecutionCoordinatorCallbacks callbacks;
    callbacks.validateDecisionLease =
        [](const AgentExecutionContext&, const std::string&,
           std::string*) { return true; };
    callbacks.flattenOrder =
        [&](const AuthoritativeFlattenPlan&, const std::string&) {
            ++venueCalls;
            return VenueFlattenResult::RejectedBeforeSend(
                VenueFlattenRejectionFromReasonCode(venueReason), venueReason);
        };
    ExecutionCoordinator coordinator(journal, callbacks);

    const FlattenPositionCommand untrustedCommand =
        MakeFlatten("flatten-untrusted-venue-reject");
    const ExecutionCommandResult untrusted =
        coordinator.ExecuteAuthoritativeFlatten(
            untrustedCommand, MakeFlattenPlan(untrustedCommand));
    assert(untrusted.status == ExecutionCommandStatus::Rejected);
    assert(untrusted.reasonCode == "IB_FLATTEN_REJECT");
    assert(untrusted.detail == venueReason);

    venueReason =
        "IB_PAPER_FLATTEN_QUOTE_CHANGED_BEFORE_SEND";
    const FlattenPositionCommand canonicalCommand =
        MakeFlatten("flatten-canonical-venue-reject");
    const ExecutionCommandResult canonical =
        coordinator.ExecuteAuthoritativeFlatten(
            canonicalCommand, MakeFlattenPlan(canonicalCommand));
    assert(canonical.status == ExecutionCommandStatus::Rejected);
    assert(canonical.reasonCode == venueReason);
    assert(canonical.detail == venueReason);
    assert(venueCalls == 2);
    assert(coordinator.ExecuteAuthoritativeFlatten(
        untrustedCommand, MakeFlattenPlan(untrustedCommand)).status ==
        ExecutionCommandStatus::Duplicate);
    assert(coordinator.ExecuteAuthoritativeFlatten(
        canonicalCommand, MakeFlattenPlan(canonicalCommand)).status ==
        ExecutionCommandStatus::Duplicate);
    assert(venueCalls == 2);
    std::remove(path.c_str());
}

void AppendOrphanFlattenEvent(
    OmsJournal& journal, const char* eventType,
    const char* status)
{
    OmsJournalEvent event;
    event.eventType = eventType;
    event.tsMs = OmsJournal::NowEpochMs();
    event.eventId = std::string("flatten-orphan:") + eventType;
    event.reqId = "flatten-orphan";
    event.clientReqId = event.reqId;
    event.traceId = "session-1";
    event.source = "agent.tool:agent-a";
    event.strategy = "test";
    event.account = "DU123";
    event.venue = "IB";
    event.executionDomain = "PAPER";
    event.instrument = "EUR.USD";
    event.side = "SELL";
    event.qty = 100.0;
    event.price = 1.09;
    event.status = status;
    event.requestHash = "sha256:flatten-orphan-request";
    event.venueCorrelationId =
        "hepta-v1-sha256:flatten-orphan-correlation";
    assert(journal.Append(event));
}

void TestFlattenOrphanIntentRequiresDurableReconciliation()
{
    const std::string path = TempJournalPath();
    OmsJournal journal;
    assert(journal.Init(path));
    AppendOrphanFlattenEvent(
        journal, "flatten_intent", "intent_recorded");
    AppendOrphanFlattenEvent(
        journal, "flatten_send_attempt", "attempt_recorded");

    ExecutionCoordinator recovered(
        journal, ExecutionCoordinatorCallbacks());
    std::string reason;
    assert(!recovered.RecoverFromJournal(reason));
    assert(reason == "RECOVERY_RECONCILE_REQUIRED");
    assert(recovered.IsMutationBlocked());
    ExecutionCommandResult status;
    assert(recovered.GetCommandStatus(
        "agent-a", "session-1", "flatten-orphan", status));
    assert(status.status == ExecutionCommandStatus::Uncertain);
    assert(status.reasonCode == "RECOVERY_RECONCILE_REQUIRED");
    std::vector<std::int64_t> rateAttempts;
    recovered.GetPlaceSendAttemptTimes(
        "DU123", "PAPER", 0, rateAttempts);
    assert(rateAttempts.size() == 1);

    std::map<std::string, long> correlations;
    correlations[
        "hepta-v1-sha256:flatten-orphan-correlation"] = 881;
    std::size_t resolved = 0;
    assert(recovered.ResolveUncertainPlaceCommands(
        correlations, true, resolved, reason));
    assert(resolved == 1);
    assert(!recovered.IsMutationBlocked());
    assert(recovered.GetCommandStatus(
        "agent-a", "session-1", "flatten-orphan", status));
    assert(status.status == ExecutionCommandStatus::Accepted);
    assert(status.orderId == 881);
    ExecutionOrderOwner owner;
    assert(recovered.GetOrderOwner(881, owner));
    assert(owner.side == "SELL");

    ExecutionCoordinator replay(
        journal, ExecutionCoordinatorCallbacks());
    assert(replay.RecoverFromJournal(reason));
    assert(replay.GetCommandStatus(
        "agent-a", "session-1", "flatten-orphan", status));
    assert(status.status == ExecutionCommandStatus::Accepted);
    assert(status.orderId == 881);
    replay.GetPlaceSendAttemptTimes(
        "DU123", "PAPER", 0, rateAttempts);
    assert(rateAttempts.size() == 1);
    std::remove(path.c_str());
}

void TestFlattenProjectionFailureBlocksAndRecovers()
{
    const std::string path = TempJournalPath();
    OmsJournal journal;
    assert(journal.Init(path));
    int venueSends = 0;
    ExecutionCoordinatorCallbacks callbacks;
    callbacks.validateDecisionLease =
        [](const AgentExecutionContext&, const std::string&,
           std::string*) { return true; };
    callbacks.flattenOrder =
        [&](const AuthoritativeFlattenPlan&, const std::string&) {
            ++venueSends;
            return VenueFlattenResult::Submitted(882);
        };
    callbacks.onIbOrderPlaced =
        [](const IbPlaceOrderCommand&, long, std::string* reason) {
            if (reason != nullptr)
                *reason = "flatten projection write failed";
            return false;
        };
    const FlattenPositionCommand command =
        MakeFlatten("flatten-projection-failure");
    const AuthoritativeFlattenPlan plan =
        MakeFlattenPlan(command);
    ExecutionCoordinator coordinator(journal, callbacks);
    const ExecutionCommandResult first =
        coordinator.ExecuteAuthoritativeFlatten(command, plan);
    assert(first.status == ExecutionCommandStatus::Uncertain);
    assert(first.reasonCode ==
           "AUTHORITATIVE_FLATTEN_PROJECTION_FAILED");
    assert(first.orderId == 882);
    assert(coordinator.IsMutationBlocked());
    assert(venueSends == 1);

    ExecutionCoordinator recovered(journal, callbacks);
    std::string reason;
    assert(!recovered.RecoverFromJournal(reason));
    assert(reason ==
           "AUTHORITATIVE_FLATTEN_PROJECTION_FAILED");
    assert(recovered.IsMutationBlocked());
    ExecutionCommandResult status;
    assert(recovered.GetCommandStatus(
        "agent-a", "session-1",
        "flatten-projection-failure", status));
    assert(status.status == ExecutionCommandStatus::Uncertain);
    assert(status.orderId == 882);
    ExecutionOrderOwner recoveredOwner;
    assert(recovered.GetOrderOwner(882, recoveredOwner));
    assert(recoveredOwner.instrument == "EUR.USD");
    const ExecutionCommandResult uncertainRetry =
        recovered.ExecuteAuthoritativeFlatten(command, plan);
    assert(uncertainRetry.status ==
           ExecutionCommandStatus::Uncertain);
    assert(uncertainRetry.reasonCode ==
           "AUTHORITATIVE_FLATTEN_PROJECTION_FAILED");
    assert(venueSends == 1);
    assert(recovered.ResolveProjectionBlockAfterAuthoritativeResync());
    assert(!recovered.IsMutationBlocked());

    ExecutionCoordinator resolved(journal, callbacks);
    assert(resolved.RecoverFromJournal(reason));
    assert(!resolved.IsMutationBlocked());
    assert(resolved.GetCommandStatus(
        "agent-a", "session-1",
        "flatten-projection-failure", status));
    assert(status.status == ExecutionCommandStatus::Accepted);
    const ExecutionCommandResult duplicate =
        resolved.ExecuteAuthoritativeFlatten(command, plan);
    assert(duplicate.status == ExecutionCommandStatus::Duplicate);
    assert(duplicate.orderId == 882);
    assert(venueSends == 1);
    std::vector<std::int64_t> rateAttempts;
    resolved.GetPlaceSendAttemptTimes(
        "DU123", "PAPER", 0, rateAttempts);
    assert(rateAttempts.size() == 1);
    std::remove(path.c_str());
}

void TestFlattenCallbackExceptionIsDurablyUncertain()
{
    const std::string path = TempJournalPath();
    OmsJournal journal;
    assert(journal.Init(path));
    int venueSideEffects = 0;
    ExecutionCoordinatorCallbacks callbacks;
    callbacks.validateDecisionLease =
        [](const AgentExecutionContext&, const std::string&,
           std::string*) { return true; };
    callbacks.flattenOrder =
        [&](const AuthoritativeFlattenPlan&, const std::string&) {
            ++venueSideEffects;
            // The adapter catches after-send exceptions while the assigned ID
            // is still in scope; the coordinator receives that single result.
            return VenueFlattenResult::Uncertain(
                "simulated exception after venue side effect", 883);
        };
    const FlattenPositionCommand command =
        MakeFlatten("flatten-callback-uncertain");
    const AuthoritativeFlattenPlan plan = MakeFlattenPlan(command);
    ExecutionCoordinator coordinator(journal, callbacks);
    const ExecutionCommandResult first =
        coordinator.ExecuteAuthoritativeFlatten(command, plan);
    assert(first.status == ExecutionCommandStatus::Uncertain);
    assert(first.reasonCode == "IB_FLATTEN_OUTCOME_UNCERTAIN");
    assert(first.orderId == 883);
    assert(coordinator.IsMutationBlocked());
    assert(venueSideEffects == 1);
    const ExecutionCommandResult retry =
        coordinator.ExecuteAuthoritativeFlatten(command, plan);
    assert(retry.status == ExecutionCommandStatus::Uncertain);
    assert(retry.reasonCode == "IB_FLATTEN_OUTCOME_UNCERTAIN");
    assert(venueSideEffects == 1);

    int attempts = 0;
    int uncertainOutcomes = 0;
    std::string correlation;
    assert(journal.Replay([&](const OmsJournalEvent& event) {
        if (event.reqId != command.context.toolCallId) return;
        if (event.eventType == "flatten_send_attempt") ++attempts;
        if (event.eventType == "flatten_outcome_uncertain")
        {
            ++uncertainOutcomes;
            assert(event.status == "uncertain");
            assert(event.riskCode ==
                   "IB_FLATTEN_OUTCOME_UNCERTAIN");
            assert(event.orderId == 883);
            correlation = event.venueCorrelationId;
        }
    }) >= 0);
    assert(attempts == 1);
    assert(uncertainOutcomes == 1);
    assert(!correlation.empty());

    ExecutionCoordinator recovered(journal, callbacks);
    std::string reason;
    assert(!recovered.RecoverFromJournal(reason));
    assert(reason == "RECOVERY_RECONCILE_REQUIRED");
    ExecutionCommandResult status;
    assert(recovered.GetCommandStatus(
        "agent-a", "session-1",
        "flatten-callback-uncertain", status));
    assert(status.status == ExecutionCommandStatus::Uncertain);
    assert(status.reasonCode == "IB_FLATTEN_OUTCOME_UNCERTAIN");
    const ExecutionCommandResult recoveredRetry =
        recovered.ExecuteAuthoritativeFlatten(command, plan);
    assert(recoveredRetry.status ==
           ExecutionCommandStatus::Uncertain);
    assert(venueSideEffects == 1);
    std::vector<std::int64_t> rateAttempts;
    recovered.GetPlaceSendAttemptTimes(
        "DU123", "PAPER", 0, rateAttempts);
    assert(rateAttempts.size() == 1);

    std::map<std::string, long> correlations;
    correlations[correlation] = 883;
    std::size_t resolved = 0;
    assert(recovered.ResolveUncertainPlaceCommands(
        correlations, true, resolved, reason));
    assert(resolved == 1);
    assert(!recovered.IsMutationBlocked());
    ExecutionCoordinator replay(journal, callbacks);
    assert(replay.RecoverFromJournal(reason));
    assert(replay.GetCommandStatus(
        "agent-a", "session-1",
        "flatten-callback-uncertain", status));
    assert(status.status == ExecutionCommandStatus::Accepted);
    assert(status.orderId == 883);
    const ExecutionCommandResult duplicate =
        replay.ExecuteAuthoritativeFlatten(command, plan);
    assert(duplicate.status == ExecutionCommandStatus::Duplicate);
    assert(duplicate.orderId == 883);
    assert(venueSideEffects == 1);
    std::remove(path.c_str());
}

} // namespace hepta_execution_coordinator_test
