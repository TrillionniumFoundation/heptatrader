#include "../HeptaTrade/execution/execution_coordinator.h"
#include "../HeptaTrade/oms_recover.h"

#include <cassert>
#include <cmath>
#include <cstdio>
#include <fstream>
#include <iostream>
#include <set>
#include <stdexcept>
#include <string>
#include <unistd.h>

namespace {

std::string TempJournalPath()
{
    char path[] = "/tmp/hepta-execution-coordinator-XXXXXX";
    const int fd = mkstemp(path);
    assert(fd >= 0);
    close(fd);
    return std::string(path);
}

IbPlaceOrderCommand MakePlace(const std::string& callId, const std::string& agentId = "agent-a")
{
    IbPlaceOrderCommand command;
    command.context.agentId = agentId;
    command.context.sessionId = "session-1";
    command.context.toolCallId = callId;
    command.context.strategy = "test";
    command.context.account = "DU123";
    command.contract.symbol = "EUR";
    command.contract.secType = "CASH";
    command.contract.currency = "USD";
    command.contract.exchange = "IDEALPRO";
    command.instrument = "EUR.USD";
    command.order.action = "BUY";
    command.order.orderType = "LMT";
    command.order.totalQuantity = 1000.0;
    command.order.lmtPrice = 1.1;
    command.expiresAtMs = OmsJournal::NowEpochMs() + 60000;
    return command;
}

FlattenPositionCommand MakeFlatten(
    const std::string& callId, double position = 100.0)
{
    FlattenPositionCommand command;
    command.context = MakePlace(callId).context;
    command.context.toolCallId = callId;
    command.context.venue = "IB";
    command.context.executionDomain = "PAPER";
    command.context.decisionLeaseFencingToken = 17;
    command.context.decisionLeaseGeneration = 3;
    command.contract = MakePlace(callId).contract;
    command.instrument = "EUR.USD";
    command.hasAuthoritativePreviewSnapshot = true;
    command.previewPositionQuantity = position;
    command.previewPositionConnectionEpoch = 21;
    command.previewPositionGeneration = 22;
    command.authoritativePreviewPlanBinding =
        "execution-owned-flatten-plan-binding";
    return command;
}

AuthoritativeFlattenPlan MakeFlattenPlan(
    const FlattenPositionCommand& command, double position = 100.0)
{
    AuthoritativeFlattenPlan plan;
    plan.contract = command.contract;
    plan.instrument = command.instrument;
    plan.expectedPositionQuantity = position;
    plan.positionConnectionEpoch =
        command.previewPositionConnectionEpoch;
    plan.positionGeneration = command.previewPositionGeneration;
    if (position != 0.0)
    {
        plan.order.action = position > 0.0 ? "SELL" : "BUY";
        plan.order.orderType = "MKT";
        plan.order.totalQuantity = std::fabs(position);
        plan.order.lmtPrice = 0.0;
        plan.timeInForce = "DAY";
        plan.referencePrice = 1.10;
        plan.quoteSubscriptionId = "IB:21:4:1001";
        plan.quoteObservedAtMs = 100000;
        plan.quoteStaleAfterMs = 105000;
    }
    return plan;
}

void TestJournalBeforeSendAndDuplicate()
{
    const std::string path = TempJournalPath();
    OmsJournal journal;
    assert(journal.Init(path));

    int placeCalls = 0;
    bool sawIntentBeforeSend = false;
    bool sawAttemptBeforeSend = false;
    ExecutionCoordinatorCallbacks callbacks;
    callbacks.placeIbOrder = [&](const IBContractLite&, const IBOrderLite&, long* outOrderId) {
        ++placeCalls;
        int intents = 0;
        int attempts = 0;
        journal.Replay([&](const OmsJournalEvent& event) {
            if (event.eventType == "order_intent" && event.reqId == "call-1" &&
                event.eventId != event.reqId) ++intents;
            if (event.eventType == "place_send_attempt" &&
                event.reqId == "call-1") ++attempts;
        });
        sawIntentBeforeSend = intents == 1;
        sawAttemptBeforeSend = attempts == 1;
        *outOrderId = 42;
        return true;
    };
    callbacks.validateDecisionLease = [](const AgentExecutionContext&, const std::string&, std::string*) {
        return true;
    };

    ExecutionCoordinator coordinator(journal, callbacks);
    IbPlaceOrderCommand command = MakePlace("call-1");
    command.context.executionDomain = "IB-PAPER:EUR.USD";
    command.context.decisionLeaseFencingToken = 7;
    command.context.decisionLeaseGeneration = 3;
    const ExecutionCommandResult first = coordinator.PlaceIbOrder(command);
    assert(first.status == ExecutionCommandStatus::Accepted);
    assert(first.orderId == 42);
    assert(sawIntentBeforeSend);
    assert(sawAttemptBeforeSend);

    const ExecutionCommandResult duplicate = coordinator.PlaceIbOrder(command);
    assert(duplicate.status == ExecutionCommandStatus::Duplicate);
    assert(duplicate.orderId == 42);
    assert(placeCalls == 1);

    std::set<std::string> lifecycleEventIds;
    std::string requestHash;
    int lifecycleEvents = 0;
    journal.Replay([&](const OmsJournalEvent& event) {
        if (event.reqId != "call-1") return;
        ++lifecycleEvents;
        assert(event.schemaVersion == 4);
        assert(event.executionDomain == "IB-PAPER:EUR.USD");
        assert(event.requestHash.rfind("sha256:", 0) == 0);
        if (requestHash.empty()) requestHash = event.requestHash;
        assert(event.requestHash == requestHash);
        lifecycleEventIds.insert(event.eventId);
    });
    assert(lifecycleEvents == 3);
    assert(lifecycleEventIds.size() == 3);

    const OmsRecoverResult recovered = OmsRecover::Replay(journal);
    assert(recovered.dedupSkipped == 0);
    assert(recovered.eventCounts.at("order_intent") == 1);
    assert(recovered.eventCounts.at("place_send_attempt") == 1);
    assert(recovered.eventCounts.at("place_sent") == 1);
    assert(recovered.orders.at(42).placeSent);

    std::remove(path.c_str());
}

void AppendUncertainIntent(OmsJournal& journal, const std::string& callId,
                           const std::string& correlationId)
{
    OmsJournalEvent event;
    event.eventType = "order_intent";
    event.tsMs = OmsJournal::NowEpochMs();
    event.reqId = callId;
    event.clientReqId = callId;
    event.traceId = "session-1";
    event.eventId = callId + ":order_intent:intent_recorded:-1";
    event.source = "agent.tool:agent-a";
    event.strategy = "test";
    event.account = "DU123";
    event.venue = "IB";
    event.executionDomain = "IB-PAPER:EUR.USD";
    event.instrument = "EUR.USD";
    event.side = "BUY";
    event.qty = 1000.0;
    event.price = 1.1;
    event.status = "intent_recorded";
    event.requestHash = "sha256:test-" + callId;
    event.venueCorrelationId = correlationId;
    assert(journal.Append(event));
}

void TestDurableAuthoritativeCorrelationResolution()
{
    const std::string path = TempJournalPath();
    OmsJournal journal;
    assert(journal.Init(path));
    AppendUncertainIntent(journal, "uncertain-present", "hepta-v1-present");
    AppendUncertainIntent(journal, "uncertain-missing", "hepta-v1-missing");

    ExecutionCoordinator coordinator(journal, ExecutionCoordinatorCallbacks());
    std::string reason;
    assert(!coordinator.RecoverFromJournal(reason));
    assert(reason == "RECOVERY_RECONCILE_REQUIRED");
    std::size_t resolved = 0;
    assert(!coordinator.ResolveUncertainPlaceCommands(
        std::map<std::string, long>(), false, resolved, reason));
    assert(reason == "AUTHORITATIVE_CORRELATION_SNAPSHOT_INCOMPLETE");
    assert(resolved == 0);

    std::map<std::string, long> authoritative;
    authoritative["hepta-v1-present"] = 808;
    assert(coordinator.ResolveUncertainPlaceCommands(
        authoritative, true, resolved, reason, false));
    assert(resolved == 1);
    assert(coordinator.IsMutationBlocked());
    ExecutionCommandResult status;
    assert(coordinator.GetCommandStatus("agent-a", "session-1", "uncertain-missing", status));
    assert(status.status == ExecutionCommandStatus::Uncertain);
    assert(status.reasonCode == "RECOVERY_RECONCILE_REQUIRED");

    assert(coordinator.ResolveUncertainPlaceCommands(authoritative, true, resolved, reason));
    assert(resolved == 1);
    assert(!coordinator.IsMutationBlocked());
    assert(coordinator.GetCommandStatus("agent-a", "session-1", "uncertain-present", status));
    assert(status.status == ExecutionCommandStatus::Accepted);
    assert(status.orderId == 808);
    assert(coordinator.GetCommandStatus("agent-a", "session-1", "uncertain-missing", status));
    assert(status.status == ExecutionCommandStatus::Rejected);
    assert(status.reasonCode == "AUTHORITATIVE_CORRELATION_NOT_FOUND");

    ExecutionCoordinator replay(journal, ExecutionCoordinatorCallbacks());
    assert(replay.RecoverFromJournal(reason));
    assert(!replay.IsMutationBlocked());
    assert(replay.GetCommandStatus("agent-a", "session-1", "uncertain-present", status));
    assert(status.status == ExecutionCommandStatus::Accepted && status.orderId == 808);
    ExecutionOrderOwner owner;
    assert(replay.GetOrderOwner(808, owner));
    assert(owner.executionDomain == "IB-PAPER:EUR.USD");
    assert(replay.GetCommandStatus("agent-a", "session-1", "uncertain-missing", status));
    assert(status.status == ExecutionCommandStatus::Rejected);

    int resolutions = 0;
    journal.Replay([&](const OmsJournalEvent& event) {
        if (event.eventType == "execution_command_resolved")
        {
            ++resolutions;
            assert(event.schemaVersion == 4);
            assert(!event.venueCorrelationId.empty());
        }
    });
    assert(resolutions == 2);
    std::remove(path.c_str());
}

void AppendUncertainCancel(OmsJournal& journal, const std::string& callId,
                           long orderId)
{
    OmsJournalEvent event;
    event.eventType = "cancel";
    event.tsMs = OmsJournal::NowEpochMs();
    event.orderId = orderId;
    event.reqId = callId;
    event.clientReqId = callId;
    event.traceId = "session-1";
    event.eventId = callId + ":cancel:intent_recorded:" +
        std::to_string(orderId);
    event.source = "agent.tool:agent-a";
    event.strategy = "test";
    event.account = "DU123";
    event.venue = "IB";
    event.executionDomain = "PAPER";
    event.instrument = "EUR.USD";
    event.side = "BUY";
    event.status = "intent_recorded";
    event.requestHash = "sha256:cancel-" + callId;
    assert(journal.Append(event));
    event.eventType = "cancel_send_attempt";
    event.eventId = callId + ":cancel_send_attempt:attempt_recorded:" +
        std::to_string(orderId);
    event.status = "attempt_recorded";
    assert(journal.Append(event));
}

void AppendDeferredCancel(OmsJournal& journal, const std::string& callId,
                          long orderId)
{
    AppendUncertainCancel(journal, callId, orderId);

    // This is the durable receipt emitted when the adapter accepted a local
    // cancel before IB delivered Submitted/OpenOrder.  On replay it must stay
    // uncertain until a complete authoritative terminal snapshot resolves it.
    OmsJournalEvent event;
    event.eventType = "cancel";
    event.tsMs = OmsJournal::NowEpochMs();
    event.orderId = orderId;
    event.reqId = callId;
    event.clientReqId = callId;
    event.traceId = "session-1";
    event.eventId = callId + ":cancel:cancel_pending:" +
        std::to_string(orderId);
    event.source = "agent.tool:agent-a";
    event.strategy = "test";
    event.account = "DU123";
    event.venue = "IB";
    event.executionDomain = "PAPER";
    event.instrument = "EUR.USD";
    event.side = "BUY";
    event.status = "cancel_pending";
    event.reason = "waiting for broker order acknowledgement";
    event.riskCode = "IB_CANCEL_DEFERRED_UNTIL_BROKER_ACK";
    event.requestHash = "sha256:cancel-" + callId;
    assert(journal.Append(event));
}

void TestDeferredCancelRecoveryResolution()
{
    const std::string path = TempJournalPath();
    OmsJournal journal;
    assert(journal.Init(path));
    AppendDeferredCancel(journal, "cancel-deferred", 504);

    ExecutionCoordinator coordinator(journal, ExecutionCoordinatorCallbacks());
    std::string reason;
    assert(!coordinator.RecoverFromJournal(reason));
    assert(reason == "RECOVERY_RECONCILE_REQUIRED");

    ExecutionCommandResult status;
    assert(coordinator.GetCommandStatus(
        "agent-a", "session-1", "cancel-deferred", status));
    assert(status.status == ExecutionCommandStatus::Uncertain);
    assert(status.reasonCode == "IB_CANCEL_DEFERRED_UNTIL_BROKER_ACK");

    std::map<long, std::string> terminal;
    terminal[504] = "Cancelled";
    std::size_t resolved = 0;
    std::string resolveReason;
    assert(coordinator.ResolveUncertainCancelCommands(
        std::set<long>(), true, terminal, std::set<long>(), true,
        resolved, resolveReason));
    assert(resolved == 1);
    assert(!coordinator.IsMutationBlocked());
    assert(coordinator.GetCommandStatus(
        "agent-a", "session-1", "cancel-deferred", status));
    assert(status.status == ExecutionCommandStatus::Accepted);
    assert(status.reasonCode == "AUTHORITATIVE_CANCEL_TERMINAL_CONFIRMED");

    // The resolution receipt is durable: a fresh coordinator must replay the
    // same accepted outcome without attempting another broker mutation.
    ExecutionCoordinator replay(journal, ExecutionCoordinatorCallbacks());
    assert(replay.RecoverFromJournal(reason));
    assert(replay.GetCommandStatus(
        "agent-a", "session-1", "cancel-deferred", status));
    assert(status.status == ExecutionCommandStatus::Accepted);
    assert(status.reasonCode == "AUTHORITATIVE_CANCEL_TERMINAL_CONFIRMED");
    std::remove(path.c_str());
}

void TestDurableAuthoritativeCancelResolution()
{
    const std::string path = TempJournalPath();
    OmsJournal journal;
    assert(journal.Init(path));
    AppendUncertainCancel(journal, "cancel-active", 501);
    AppendUncertainCancel(journal, "cancel-cancelled", 502);
    AppendUncertainCancel(journal, "cancel-filled", 503);

    ExecutionCoordinator coordinator(journal, ExecutionCoordinatorCallbacks());
    std::string reason;
    assert(!coordinator.RecoverFromJournal(reason));
    assert(reason == "RECOVERY_RECONCILE_REQUIRED");
    std::size_t resolved = 0;
    assert(!coordinator.ResolveUncertainCancelCommands(
        std::set<long>(), false, std::map<long, std::string>(),
        std::set<long>(), true, resolved, reason));
    assert(reason == "AUTHORITATIVE_ACTIVE_ORDER_SNAPSHOT_INCOMPLETE");
    assert(!coordinator.ResolveUncertainCancelCommands(
        std::set<long>(), true, std::map<long, std::string>(),
        std::set<long>(), false, resolved, reason));
    assert(reason == "AUTHORITATIVE_TERMINAL_ORDER_SNAPSHOT_INCOMPLETE");

    std::set<long> active;
    active.insert(501);
    std::map<long, std::string> terminal;
    terminal[502] = "Cancelled";
    terminal[503] = "Filled";
    std::set<long> executions;
    executions.insert(501);
    executions.insert(503);
    assert(coordinator.ResolveUncertainCancelCommands(
        active, true, terminal, executions, true, resolved, reason));
    assert(resolved == 2);
    assert(coordinator.IsMutationBlocked());

    ExecutionCommandResult status;
    assert(coordinator.GetCommandStatus(
        "agent-a", "session-1", "cancel-active", status));
    assert(status.status == ExecutionCommandStatus::Uncertain);
    assert(coordinator.GetCommandStatus(
        "agent-a", "session-1", "cancel-cancelled", status));
    assert(status.status == ExecutionCommandStatus::Accepted);
    assert(status.reasonCode == "AUTHORITATIVE_CANCEL_TERMINAL_CONFIRMED");
    assert(coordinator.GetCommandStatus(
        "agent-a", "session-1", "cancel-filled", status));
    assert(status.status == ExecutionCommandStatus::Rejected);
    assert(status.reasonCode == "AUTHORITATIVE_CANCEL_TARGET_FILLED");

    terminal.clear();
    executions.clear();
    executions.insert(501);
    assert(coordinator.ResolveUncertainCancelCommands(
        std::set<long>(), true, terminal, executions, true,
        resolved, reason));
    assert(resolved == 1);
    assert(!coordinator.IsMutationBlocked());

    ExecutionCoordinator replay(journal, ExecutionCoordinatorCallbacks());
    assert(replay.RecoverFromJournal(reason));
    assert(replay.GetCommandStatus(
        "agent-a", "session-1", "cancel-active", status));
    assert(status.status == ExecutionCommandStatus::Rejected);
    assert(status.reasonCode == "AUTHORITATIVE_CANCEL_TARGET_FILLED");
    assert(replay.GetCommandStatus(
        "agent-a", "session-1", "cancel-filled", status));
    assert(status.status == ExecutionCommandStatus::Rejected);
    int resolutions = 0;
    assert(journal.Replay([&](const OmsJournalEvent& item) {
        if (item.eventType == "cancel_command_resolved") ++resolutions;
    }) >= 0);
    assert(resolutions == 3);
    std::remove(path.c_str());

    const std::string zeroPath = TempJournalPath();
    OmsJournal zeroJournal;
    assert(zeroJournal.Init(zeroPath));
    AppendUncertainCancel(
        zeroJournal, "cancel-zero-ambiguous-evidence", 0);
    ExecutionCoordinator zeroCoordinator(
        zeroJournal, ExecutionCoordinatorCallbacks());
    assert(!zeroCoordinator.RecoverFromJournal(reason));
    assert(reason == "RECOVERY_RECONCILE_REQUIRED");
    executions.clear();
    executions.insert(0);
    terminal.clear();
    terminal[0] = "Cancelled";
    assert(zeroCoordinator.ResolveUncertainCancelCommands(
        std::set<long>(), true, terminal, executions, true,
        resolved, reason));
    assert(resolved == 0);
    assert(zeroCoordinator.IsMutationBlocked());
    assert(zeroCoordinator.GetCommandStatus(
        "agent-a", "session-1", "cancel-zero-ambiguous-evidence",
        status));
    assert(status.status == ExecutionCommandStatus::Uncertain);
    assert(status.reasonCode == "RECOVERY_RECONCILE_REQUIRED");
    std::remove(zeroPath.c_str());
}

void TestJournalFailurePreventsBrokerSend()
{
    OmsJournal journal;
    int placeCalls = 0;
    ExecutionCoordinatorCallbacks callbacks;
    callbacks.placeIbOrder = [&](const IBContractLite&, const IBOrderLite&, long*) {
        ++placeCalls;
        return true;
    };
    ExecutionCoordinator coordinator(journal, callbacks);
    const ExecutionCommandResult result = coordinator.PlaceIbOrder(MakePlace("call-no-journal"));
    assert(result.status == ExecutionCommandStatus::Rejected);
    assert(result.reasonCode == "OMS_INTENT_WRITE_FAILED");
    assert(placeCalls == 0);
    assert(coordinator.IsMutationBlocked());
    assert(!coordinator.ResolveProjectionBlockAfterAuthoritativeResync());
    assert(coordinator.IsMutationBlocked());
}

void TestVenueCorrelationBindsCommandIdentity()
{
    const std::string path = TempJournalPath();
    OmsJournal journal;
    assert(journal.Init(path));
    std::vector<std::string> correlations;
    ExecutionCoordinatorCallbacks callbacks;
    callbacks.placeIbOrderCorrelated = [&](const IBContractLite&, const IBOrderLite&,
                                            const std::string& correlation, long* orderId) {
        correlations.push_back(correlation);
        *orderId = 600 + static_cast<long>(correlations.size());
        return true;
    };
    ExecutionCoordinator coordinator(journal, callbacks);
    IbPlaceOrderCommand first = MakePlace("correlation-a");
    IbPlaceOrderCommand second = first;
    second.context.toolCallId = "correlation-b";
    assert(coordinator.PlaceIbOrder(first).status == ExecutionCommandStatus::Accepted);
    assert(coordinator.PlaceIbOrder(second).status == ExecutionCommandStatus::Accepted);
    assert(correlations.size() == 2);
    assert(correlations[0].rfind("hepta-v1-sha256:", 0) == 0);
    assert(correlations[0] != correlations[1]);
    int correlatedEvents = 0;
    journal.Replay([&](const OmsJournalEvent& event) {
        if (event.reqId == "correlation-a" || event.reqId == "correlation-b")
        {
            ++correlatedEvents;
            assert(!event.venueCorrelationId.empty());
        }
    });
    assert(correlatedEvents == 6);
    std::remove(path.c_str());
}

enum PlaceOutcomeMode
{
    PlaceThrowsAfterSideEffect,
    PlaceReturnsFalseWithoutReason,
    PlaceRejectReasonReaderThrows,
    PlaceReturnsTrueWithoutOrderId
};

void ExerciseUncertainPlaceOutcome(
    const std::string& callId,
    PlaceOutcomeMode mode,
    const std::string& expectedDetail)
{
    const std::string path = TempJournalPath();
    OmsJournal journal;
    assert(journal.Init(path));
    int venueCalls = 0;
    int rejectReasonReads = 0;
    ExecutionCoordinatorCallbacks callbacks;
    callbacks.placeIbOrderCorrelated =
        [&](const IBContractLite&, const IBOrderLite&,
            const std::string&, long* orderId) -> bool {
            ++venueCalls;
            if (mode == PlaceThrowsAfterSideEffect)
            {
                *orderId = 630;
                throw std::runtime_error(
                    "simulated place exception after venue side effect");
            }
            if (mode == PlaceReturnsTrueWithoutOrderId)
                return true;
            return false;
        };
    callbacks.lastIbRejectReason = [&]() {
        ++rejectReasonReads;
        if (mode == PlaceRejectReasonReaderThrows)
            throw std::runtime_error(
                "simulated place rejection reader exception");
        if (mode == PlaceReturnsFalseWithoutReason)
            return std::string();
        return std::string("misleading adapter rejection");
    };
    const IbPlaceOrderCommand command = MakePlace(callId);
    ExecutionCoordinator coordinator(journal, callbacks);
    const ExecutionCommandResult first =
        coordinator.PlaceIbOrder(command);
    assert(first.status == ExecutionCommandStatus::Uncertain);
    assert(first.reasonCode == "IB_PLACE_OUTCOME_UNCERTAIN");
    assert(first.detail.find(expectedDetail) != std::string::npos);
    assert(venueCalls == 1);
    assert(rejectReasonReads ==
           (mode == PlaceReturnsFalseWithoutReason ||
            mode == PlaceRejectReasonReaderThrows ? 1 : 0));
    assert(coordinator.IsMutationBlocked());
    const ExecutionCommandResult retry =
        coordinator.PlaceIbOrder(command);
    assert(retry.status == ExecutionCommandStatus::Uncertain);
    assert(retry.reasonCode == "IB_PLACE_OUTCOME_UNCERTAIN");
    assert(venueCalls == 1);

    int intents = 0;
    int attempts = 0;
    int uncertainOutcomes = 0;
    std::string correlation;
    assert(journal.Replay([&](const OmsJournalEvent& event) {
        if (event.reqId != callId) return;
        if (event.eventType == "order_intent") ++intents;
        if (event.eventType == "place_send_attempt") ++attempts;
        if (event.eventType == "place_outcome_uncertain")
        {
            ++uncertainOutcomes;
            assert(event.status == "uncertain");
            assert(event.riskCode ==
                   "IB_PLACE_OUTCOME_UNCERTAIN");
            assert(event.reason.find(expectedDetail) !=
                   std::string::npos);
            assert(event.requestHash.rfind("sha256:", 0) == 0);
            correlation = event.venueCorrelationId;
        }
    }) >= 0);
    assert(intents == 1);
    assert(attempts == 1);
    assert(uncertainOutcomes == 1);
    assert(!correlation.empty());

    ExecutionCoordinator recovered(journal, callbacks);
    std::string reason;
    assert(!recovered.RecoverFromJournal(reason));
    assert(reason == "RECOVERY_RECONCILE_REQUIRED");
    ExecutionCommandResult status;
    assert(recovered.GetCommandStatus(
        "agent-a", "session-1", callId, status));
    assert(status.status == ExecutionCommandStatus::Uncertain);
    assert(status.reasonCode == "IB_PLACE_OUTCOME_UNCERTAIN");
    assert(recovered.PlaceIbOrder(command).status ==
           ExecutionCommandStatus::Uncertain);
    assert(venueCalls == 1);
    std::vector<std::int64_t> rateAttempts;
    recovered.GetPlaceSendAttemptTimes(
        "DU123", "", 0, rateAttempts);
    assert(rateAttempts.size() == 1);

    std::map<std::string, long> correlations;
    const bool sideEffectWasKnown =
        mode == PlaceThrowsAfterSideEffect;
    if (sideEffectWasKnown) correlations[correlation] = 630;
    std::size_t resolved = 0;
    assert(recovered.ResolveUncertainPlaceCommands(
        correlations, true, resolved, reason));
    assert(resolved == 1);
    assert(!recovered.IsMutationBlocked());
    assert(recovered.GetCommandStatus(
        "agent-a", "session-1", callId, status));
    assert(status.status == (sideEffectWasKnown ?
        ExecutionCommandStatus::Accepted :
        ExecutionCommandStatus::Rejected));
    assert(status.orderId == (sideEffectWasKnown ? 630 : -1));

    ExecutionCoordinator replay(journal, callbacks);
    assert(replay.RecoverFromJournal(reason));
    assert(replay.GetCommandStatus(
        "agent-a", "session-1", callId, status));
    assert(status.status == (sideEffectWasKnown ?
        ExecutionCommandStatus::Accepted :
        ExecutionCommandStatus::Rejected));
    const ExecutionCommandResult duplicate =
        replay.PlaceIbOrder(command);
    assert(duplicate.status == ExecutionCommandStatus::Duplicate);
    assert(duplicate.orderId ==
           (sideEffectWasKnown ? 630 : -1));
    assert(venueCalls == 1);
    replay.GetPlaceSendAttemptTimes(
        "DU123", "", 0, rateAttempts);
    assert(rateAttempts.size() == 1);
    std::remove(path.c_str());
}

void TestPlaceCallbackUncertaintyAndReliableReject()
{
    ExerciseUncertainPlaceOutcome(
        "place-throw-after-side-effect",
        PlaceThrowsAfterSideEffect, "after venue side effect");
    ExerciseUncertainPlaceOutcome(
        "place-false-empty-reason",
        PlaceReturnsFalseWithoutReason, "without a reliable rejection");
    ExerciseUncertainPlaceOutcome(
        "place-reject-reader-throws",
        PlaceRejectReasonReaderThrows, "rejection reader exception");
    ExerciseUncertainPlaceOutcome(
        "place-true-invalid-order-id",
        PlaceReturnsTrueWithoutOrderId, "without an order id");

    const std::string path = TempJournalPath();
    OmsJournal journal;
    assert(journal.Init(path));
    int venueCalls = 0;
    int reasonReads = 0;
    ExecutionCoordinatorCallbacks callbacks;
    callbacks.placeIbOrderCorrelated =
        [&](const IBContractLite&, const IBOrderLite&,
            const std::string&, long*) {
            ++venueCalls;
            return false;
        };
    callbacks.lastIbRejectReason = [&]() {
        ++reasonReads;
        return std::string("explicit reliable adapter rejection");
    };
    const IbPlaceOrderCommand command =
        MakePlace("place-explicit-reject");
    ExecutionCoordinator coordinator(journal, callbacks);
    const ExecutionCommandResult rejected =
        coordinator.PlaceIbOrder(command);
    assert(rejected.status == ExecutionCommandStatus::Rejected);
    assert(rejected.reasonCode == "IB_PLACE_REJECT");
    assert(rejected.detail == "explicit reliable adapter rejection");
    assert(!coordinator.IsMutationBlocked());
    assert(venueCalls == 1);
    assert(reasonReads == 1);
    assert(coordinator.PlaceIbOrder(command).status ==
           ExecutionCommandStatus::Duplicate);
    assert(venueCalls == 1);
    int rejects = 0;
    int uncertainOutcomes = 0;
    assert(journal.Replay([&](const OmsJournalEvent& event) {
        if (event.reqId != command.context.toolCallId) return;
        if (event.eventType == "reject") ++rejects;
        if (event.eventType == "place_outcome_uncertain")
            ++uncertainOutcomes;
    }) >= 0);
    assert(rejects == 1);
    assert(uncertainOutcomes == 0);
    ExecutionCoordinator replay(journal, callbacks);
    std::string reason;
    assert(replay.RecoverFromJournal(reason));
    assert(replay.PlaceIbOrder(command).status ==
           ExecutionCommandStatus::Duplicate);
    assert(venueCalls == 1);
    std::remove(path.c_str());
}

void TestIdempotencyIsScopedPerAgentSession()
{
    const std::string path = TempJournalPath();
    OmsJournal journal;
    assert(journal.Init(path));
    int placeCalls = 0;
    ExecutionCoordinatorCallbacks callbacks;
    callbacks.placeIbOrder = [&](const IBContractLite&, const IBOrderLite&, long* outOrderId) {
        *outOrderId = 500 + (++placeCalls);
        return true;
    };
    ExecutionCoordinator coordinator(journal, callbacks);

    IbPlaceOrderCommand first = MakePlace("same-call-id", "agent-a");
    IbPlaceOrderCommand second = MakePlace("same-call-id", "agent-b");
    second.context.sessionId = "session-2";
    const ExecutionCommandResult firstResult = coordinator.PlaceIbOrder(first);
    const ExecutionCommandResult secondResult = coordinator.PlaceIbOrder(second);
    assert(firstResult.status == ExecutionCommandStatus::Accepted);
    assert(secondResult.status == ExecutionCommandStatus::Accepted);
    assert(firstResult.orderId != secondResult.orderId);
    assert(placeCalls == 2);
    assert(coordinator.PlaceIbOrder(first).status == ExecutionCommandStatus::Duplicate);
    assert(placeCalls == 2);
    std::remove(path.c_str());
}

void TestIdempotencyPayloadAndOperationConflict()
{
    const std::string path = TempJournalPath();
    OmsJournal journal;
    assert(journal.Init(path));
    int placeCalls = 0;
    int cancelCalls = 0;
    ExecutionCoordinatorCallbacks callbacks;
    callbacks.placeIbOrder = [&](const IBContractLite&, const IBOrderLite&, long* outOrderId) {
        ++placeCalls;
        *outOrderId = 610;
        return true;
    };
    callbacks.cancelIbOrder = [&](long) {
        ++cancelCalls;
        return true;
    };
    callbacks.canCancelIbOrder = [](long, std::string*) { return true; };

    ExecutionCoordinator coordinator(journal, callbacks);
    const IbPlaceOrderCommand original = MakePlace("conflict-key");
    assert(coordinator.PlaceIbOrder(original).status == ExecutionCommandStatus::Accepted);

    IbPlaceOrderCommand changedPayload = original;
    changedPayload.order.totalQuantity = 2000.0;
    const ExecutionCommandResult payloadConflict = coordinator.PlaceIbOrder(changedPayload);
    assert(payloadConflict.status == ExecutionCommandStatus::Rejected);
    assert(payloadConflict.reasonCode == "IDEMPOTENCY_KEY_CONFLICT");
    assert(payloadConflict.orderId == 610);
    assert(placeCalls == 1);

    IbCancelOrderCommand changedOperation;
    changedOperation.context = original.context;
    changedOperation.orderId = 610;
    const ExecutionCommandResult operationConflict = coordinator.CancelIbOrder(changedOperation);
    assert(operationConflict.status == ExecutionCommandStatus::Rejected);
    assert(operationConflict.reasonCode == "IDEMPOTENCY_KEY_CONFLICT");
    assert(cancelCalls == 0);

    const ExecutionCommandResult exactRetry = coordinator.PlaceIbOrder(original);
    assert(exactRetry.status == ExecutionCommandStatus::Duplicate);
    assert(exactRetry.orderId == 610);
    assert(placeCalls == 1);
    std::remove(path.c_str());
}

void TestIdempotencyConflictSurvivesReplayAndLegacyHashIsCompatible()
{
    const std::string path = TempJournalPath();
    IbPlaceOrderCommand original = MakePlace("replay-conflict");
    {
        OmsJournal journal;
        assert(journal.Init(path));
        int placeCalls = 0;
        ExecutionCoordinatorCallbacks callbacks;
        callbacks.placeIbOrder = [&](const IBContractLite&, const IBOrderLite&, long* outOrderId) {
            ++placeCalls;
            *outOrderId = 620;
            return true;
        };
        ExecutionCoordinator coordinator(journal, callbacks);
        assert(coordinator.PlaceIbOrder(original).status == ExecutionCommandStatus::Accepted);
        assert(placeCalls == 1);
    }

    {
        OmsJournal journal;
        assert(journal.Init(path));
        int placeCalls = 0;
        ExecutionCoordinatorCallbacks callbacks;
        callbacks.placeIbOrder = [&](const IBContractLite&, const IBOrderLite&, long*) {
            ++placeCalls;
            return true;
        };
        ExecutionCoordinator recovered(journal, callbacks);
        std::string reason;
        assert(recovered.RecoverFromJournal(reason));
        const ExecutionCommandResult exactRetry = recovered.PlaceIbOrder(original);
        assert(exactRetry.status == ExecutionCommandStatus::Duplicate);
        assert(exactRetry.orderId == 620);

        IbPlaceOrderCommand changed = original;
        changed.order.lmtPrice = 1.2;
        const ExecutionCommandResult conflict = recovered.PlaceIbOrder(changed);
        assert(conflict.status == ExecutionCommandStatus::Rejected);
        assert(conflict.reasonCode == "IDEMPOTENCY_KEY_CONFLICT");
        assert(placeCalls == 0);
    }
    std::remove(path.c_str());

    const std::string legacyPath = TempJournalPath();
    OmsJournal legacyJournal;
    assert(legacyJournal.Init(legacyPath));
    OmsJournalEvent legacyIntent;
    legacyIntent.schemaVersion = 2;
    legacyIntent.eventType = "order_intent";
    legacyIntent.tsMs = OmsJournal::NowEpochMs();
    legacyIntent.reqId = "legacy-key";
    legacyIntent.clientReqId = legacyIntent.reqId;
    legacyIntent.traceId = "session-1";
    legacyIntent.eventId = "legacy-key";
    legacyIntent.source = "agent.tool:agent-a";
    legacyIntent.instrument = "EUR.USD";
    legacyIntent.side = "BUY";
    assert(legacyJournal.Append(legacyIntent));
    OmsJournalEvent legacySent = legacyIntent;
    legacySent.eventType = "place_sent";
    legacySent.orderId = 630;
    legacySent.status = "submitted";
    assert(legacyJournal.Append(legacySent));

    int legacyDispatches = 0;
    ExecutionCoordinatorCallbacks legacyCallbacks;
    legacyCallbacks.placeIbOrder = [&](const IBContractLite&, const IBOrderLite&, long*) {
        ++legacyDispatches;
        return true;
    };
    ExecutionCoordinator legacyRecovered(legacyJournal, legacyCallbacks);
    std::string legacyReason;
    assert(legacyRecovered.RecoverFromJournal(legacyReason));
    IbPlaceOrderCommand legacyRetry = MakePlace("legacy-key");
    legacyRetry.order.totalQuantity = 9999.0;
    const ExecutionCommandResult legacyDuplicate = legacyRecovered.PlaceIbOrder(legacyRetry);
    assert(legacyDuplicate.status == ExecutionCommandStatus::Duplicate);
    assert(legacyDuplicate.orderId == 630);
    assert(legacyDispatches == 0);
    std::remove(legacyPath.c_str());
}

void TestOwnershipAndCancel()
{
    const std::string path = TempJournalPath();
    OmsJournal journal;
    assert(journal.Init(path));

    int cancelCalls = 0;
    int placedProjectionCalls = 0;
    int cancelProjectionCalls = 0;
    ExecutionCoordinatorCallbacks callbacks;
    callbacks.placeIbOrder = [](const IBContractLite&, const IBOrderLite&, long* outOrderId) {
        *outOrderId = 99;
        return true;
    };
    callbacks.canCancelIbOrder = [](long, std::string*) { return true; };
    callbacks.cancelIbOrder = [&](long orderId) {
        ++cancelCalls;
        return orderId == 99;
    };
    callbacks.onIbOrderPlaced = [&](const IbPlaceOrderCommand& command, long orderId, std::string*) {
        ++placedProjectionCalls;
        assert(command.instrument == "EUR.USD");
        assert(orderId == 99);
        return true;
    };
    callbacks.onIbCancelSent = [&](const IbCancelOrderCommand& command, std::string*) {
        ++cancelProjectionCalls;
        assert(command.orderId == 99);
        return true;
    };

    ExecutionCoordinator coordinator(journal, callbacks);
    assert(coordinator.PlaceIbOrder(MakePlace("place-owner")).status == ExecutionCommandStatus::Accepted);
    assert(placedProjectionCalls == 1);

    IbCancelOrderCommand wrong;
    wrong.context.agentId = "agent-b";
    wrong.context.sessionId = "session-2";
    wrong.context.toolCallId = "cancel-wrong";
    wrong.orderId = 99;
    assert(coordinator.CancelIbOrder(wrong).reasonCode == "ORDER_OWNER_MISMATCH");
    assert(cancelCalls == 0);

    IbCancelOrderCommand own;
    own.context.agentId = "agent-a";
    own.context.sessionId = "session-1";
    own.context.toolCallId = "cancel-own";
    own.context.account = "DU123";
    own.orderId = 99;
    assert(coordinator.CancelIbOrder(own).status == ExecutionCommandStatus::Accepted);
    assert(cancelCalls == 1);
    assert(cancelProjectionCalls == 1);

    IbCancelOrderCommand changedCancel = own;
    changedCancel.orderId = 100;
    const ExecutionCommandResult cancelConflict = coordinator.CancelIbOrder(changedCancel);
    assert(cancelConflict.status == ExecutionCommandStatus::Rejected);
    assert(cancelConflict.reasonCode == "IDEMPOTENCY_KEY_CONFLICT");
    assert(cancelCalls == 1);
    const ExecutionCommandResult cancelDuplicate = coordinator.CancelIbOrder(own);
    assert(cancelDuplicate.status == ExecutionCommandStatus::Duplicate);
    assert(cancelDuplicate.orderId == 99);
    assert(cancelCalls == 1);

    std::remove(path.c_str());
}

void TestRecoveryBlocksUncertainIntent()
{
    const std::string path = TempJournalPath();
    OmsJournal journal;
    assert(journal.Init(path));

    OmsJournalEvent intent;
    intent.eventType = "order_intent";
    intent.tsMs = OmsJournal::NowEpochMs();
    intent.eventId = "orphan-intent";
    intent.reqId = intent.eventId;
    intent.traceId = "session-recover";
    intent.source = "agent.tool:agent-recover";
    intent.venue = "IB";
    intent.instrument = "EUR.USD";
    intent.side = "BUY";
    assert(journal.Append(intent));

    int placeCalls = 0;
    ExecutionCoordinatorCallbacks callbacks;
    callbacks.placeIbOrder = [&](const IBContractLite&, const IBOrderLite&, long*) {
        ++placeCalls;
        return true;
    };
    ExecutionCoordinator coordinator(journal, callbacks);
    std::string reason;
    assert(!coordinator.RecoverFromJournal(reason));
    assert(reason == "RECOVERY_RECONCILE_REQUIRED");
    assert(coordinator.IsMutationBlocked());

    IbPlaceOrderCommand retry = MakePlace("orphan-intent", "agent-recover");
    retry.context.sessionId = "session-recover";
    const ExecutionCommandResult uncertain = coordinator.PlaceIbOrder(retry);
    assert(uncertain.status == ExecutionCommandStatus::Uncertain);
    assert(uncertain.commandId == "orphan-intent");
    assert(uncertain.reasonCode == "RECOVERY_RECONCILE_REQUIRED");
    assert(uncertain.orderId == -1);
    assert(placeCalls == 0);

    std::remove(path.c_str());
}

void TestProjectionFailureBlocksFurtherMutations()
{
    const std::string path = TempJournalPath();
    OmsJournal journal;
    assert(journal.Init(path));

    int placeCalls = 0;
    ExecutionCoordinatorCallbacks callbacks;
    callbacks.placeIbOrder = [&](const IBContractLite&, const IBOrderLite&, long* outOrderId) {
        ++placeCalls;
        *outOrderId = 701;
        return true;
    };
    callbacks.onIbOrderPlaced = [](const IbPlaceOrderCommand&, long, std::string* reason) {
        if (reason != nullptr) *reason = "snapshot write rejected";
        return false;
    };

    ExecutionCoordinator coordinator(journal, callbacks);
    const IbPlaceOrderCommand projectionCommand = MakePlace("projection-failure");
    const ExecutionCommandResult first = coordinator.PlaceIbOrder(projectionCommand);
    assert(first.status == ExecutionCommandStatus::Uncertain);
    assert(first.orderId == 701);
    assert(first.reasonCode == "AUTHORITATIVE_ORDER_PROJECTION_FAILED");
    assert(coordinator.IsMutationBlocked());
    assert(placeCalls == 1);

    int sentReceipts = 0;
    int projectionFailures = 0;
    journal.Replay([&](const OmsJournalEvent& event) {
        if (event.eventType == "place_sent" && event.orderId == 701) ++sentReceipts;
        if (event.eventType == "execution_projection_failed" && event.orderId == 701)
        {
            ++projectionFailures;
            assert(event.schemaVersion == 4);
            assert(event.reqId == "projection-failure");
            assert(event.eventId != event.reqId);
            assert(event.riskCode == "AUTHORITATIVE_ORDER_PROJECTION_FAILED");
            assert(event.requestHash.rfind("sha256:", 0) == 0);
        }
    });
    assert(sentReceipts == 1);
    assert(projectionFailures == 1);

    ExecutionCoordinator recovered(journal, callbacks);
    std::string recoveryReason;
    assert(!recovered.RecoverFromJournal(recoveryReason));
    assert(recoveryReason == "AUTHORITATIVE_ORDER_PROJECTION_FAILED");
    assert(recovered.IsMutationBlocked());
    const ExecutionCommandResult recoveredRetry = recovered.PlaceIbOrder(projectionCommand);
    assert(recoveredRetry.status == ExecutionCommandStatus::Uncertain);
    assert(recoveredRetry.reasonCode == "AUTHORITATIVE_ORDER_PROJECTION_FAILED");
    assert(recoveredRetry.orderId == 701);
    assert(placeCalls == 1);

    IbPlaceOrderCommand changedRetry = projectionCommand;
    changedRetry.order.totalQuantity = 2000.0;
    const ExecutionCommandResult recoveredConflict = recovered.PlaceIbOrder(changedRetry);
    assert(recoveredConflict.status == ExecutionCommandStatus::Rejected);
    assert(recoveredConflict.reasonCode == "IDEMPOTENCY_KEY_CONFLICT");
    assert(placeCalls == 1);

    const ExecutionCommandResult second = coordinator.PlaceIbOrder(MakePlace("blocked-after-projection"));
    assert(second.status == ExecutionCommandStatus::Rejected);
    assert(second.reasonCode == "MUTATION_BLOCKED");
    assert(placeCalls == 1);

    assert(coordinator.ResolveProjectionBlockAfterAuthoritativeResync());
    assert(!coordinator.IsMutationBlocked());

    int projectionResolutions = 0;
    journal.Replay([&](const OmsJournalEvent& event) {
        if (event.eventType == "execution_projection_resolved") ++projectionResolutions;
    });
    assert(projectionResolutions == 1);
    ExecutionCoordinator resolvedReplay(journal, callbacks);
    assert(resolvedReplay.RecoverFromJournal(recoveryReason));
    assert(!resolvedReplay.IsMutationBlocked());
    const ExecutionCommandResult resolvedRetry = resolvedReplay.PlaceIbOrder(projectionCommand);
    assert(resolvedRetry.status == ExecutionCommandStatus::Duplicate);
    assert(resolvedRetry.orderId == 701);
    assert(placeCalls == 1);

    std::remove(path.c_str());
}

void TestCancelProjectionFailureIsUncertain()
{
    const std::string path = TempJournalPath();
    OmsJournal journal;
    assert(journal.Init(path));

    ExecutionCoordinatorCallbacks callbacks;
    callbacks.placeIbOrder = [](const IBContractLite&, const IBOrderLite&, long* outOrderId) {
        *outOrderId = 801;
        return true;
    };
    callbacks.onIbOrderPlaced = [](const IbPlaceOrderCommand&, long, std::string*) { return true; };
    callbacks.canCancelIbOrder = [](long, std::string*) { return true; };
    callbacks.cancelIbOrder = [](long orderId) { return orderId == 801; };
    callbacks.onIbCancelSent = [](const IbCancelOrderCommand&, std::string* reason) {
        if (reason != nullptr) *reason = "cancel snapshot write rejected";
        return false;
    };

    ExecutionCoordinator coordinator(journal, callbacks);
    assert(coordinator.PlaceIbOrder(MakePlace("cancel-projection-place")).status ==
           ExecutionCommandStatus::Accepted);

    IbCancelOrderCommand cancel;
    cancel.context.agentId = "agent-a";
    cancel.context.sessionId = "session-1";
    cancel.context.toolCallId = "cancel-projection-failure";
    cancel.context.account = "DU123";
    cancel.orderId = 801;
    const ExecutionCommandResult result = coordinator.CancelIbOrder(cancel);
    assert(result.status == ExecutionCommandStatus::Uncertain);
    assert(result.reasonCode == "AUTHORITATIVE_CANCEL_PROJECTION_FAILED");
    assert(coordinator.IsMutationBlocked());

    int cancelReceipts = 0;
    int projectionFailures = 0;
    journal.Replay([&](const OmsJournalEvent& event) {
        if (event.eventType == "cancel" && event.status == "cancel_sent" && event.orderId == 801)
            ++cancelReceipts;
        if (event.eventType == "execution_projection_failed" && event.orderId == 801)
        {
            ++projectionFailures;
            assert(event.status == "cancel_projection_failed");
            assert(event.riskCode == "AUTHORITATIVE_CANCEL_PROJECTION_FAILED");
            assert(!event.requestHash.empty());
        }
    });
    assert(cancelReceipts == 1);
    assert(projectionFailures == 1);

    ExecutionCoordinator recovered(journal, callbacks);
    std::string recoveryReason;
    assert(!recovered.RecoverFromJournal(recoveryReason));
    assert(recoveryReason == "AUTHORITATIVE_CANCEL_PROJECTION_FAILED");
    assert(recovered.IsMutationBlocked());
    const ExecutionCommandResult recoveredRetry = recovered.CancelIbOrder(cancel);
    assert(recoveredRetry.status == ExecutionCommandStatus::Uncertain);
    assert(recoveredRetry.reasonCode == "AUTHORITATIVE_CANCEL_PROJECTION_FAILED");
    assert(recoveredRetry.orderId == 801);

    std::remove(path.c_str());
}

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
    callbacks.placeIbReduceOnlyOrderCorrelated =
        [&](const AuthoritativeFlattenPlan&, const std::string&,
            long* orderId) {
            ++venueSends;
            *orderId = 880;
            return true;
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
    callbacks.placeIbReduceOnlyOrderCorrelated =
        [&](const AuthoritativeFlattenPlan&, const std::string&,
            long*) {
            ++venueSends;
            return true;
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
    callbacks.placeIbReduceOnlyOrderCorrelated =
        [&](const AuthoritativeFlattenPlan&, const std::string&,
            long*) {
            ++venueCalls;
            return false;
        };
    callbacks.lastIbRejectReason =
        [&]() { return venueReason; };
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
    callbacks.placeIbReduceOnlyOrderCorrelated =
        [&](const AuthoritativeFlattenPlan&, const std::string&,
            long* orderId) {
            ++venueSends;
            *orderId = 882;
            return true;
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
    callbacks.placeIbReduceOnlyOrderCorrelated =
        [&](const AuthoritativeFlattenPlan&, const std::string&,
            long* orderId) -> bool {
            ++venueSideEffects;
            *orderId = 883;
            throw std::runtime_error(
                "simulated exception after venue side effect");
        };
    callbacks.lastIbRejectReason =
        []() { return std::string("misleading rejection"); };
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

void TestRevokedSessionOwnerIsFenced()
{
    const std::string path = TempJournalPath();
    OmsJournal journal;
    assert(journal.Init(path));
    int placeCalls = 0;
    ExecutionCoordinatorCallbacks callbacks;
    callbacks.placeIbOrder = [&](const IBContractLite&, const IBOrderLite&, long* orderId) {
        ++placeCalls;
        *orderId = 901;
        return true;
    };
    callbacks.validateDecisionLease = [](const AgentExecutionContext&, const std::string&, std::string*) {
        return true;
    };
    ExecutionCoordinator coordinator(journal, callbacks);
	IbPlaceOrderCommand owned = MakePlace("owned-before-fence");
	owned.context.executionDomain = "IB-PAPER:EUR.USD";
	owned.context.decisionLeaseFencingToken = 19;
	owned.context.decisionLeaseGeneration = 4;
	const ExecutionCommandResult accepted = coordinator.PlaceIbOrder(owned);
	assert(accepted.status == ExecutionCommandStatus::Accepted);
    assert(coordinator.FenceSessionOwner("agent-a", "session-1") == 1);
    assert(coordinator.IsSessionOwnerFenced("agent-a", "session-1"));
    const ExecutionCommandResult rejected = coordinator.PlaceIbOrder(MakePlace("fenced-place"));
    assert(rejected.status == ExecutionCommandStatus::Rejected);
    assert(rejected.reasonCode == "SESSION_OWNER_FENCED");
	assert(placeCalls == 1);
	std::string reason;
	assert(!coordinator.AuditAndReleaseSessionOwnerFence("agent-a", "session-1", false, reason));
	assert(reason == "AUTHORITATIVE_OPEN_ORDERS_INCOMPLETE");
	assert(!coordinator.AuditAndReleaseSessionOwnerFence("agent-a", "session-1", true, reason));
	assert(reason == "FENCED_OWNER_ACTIVE_ORDERS_REMAIN");
	coordinator.RecordOrderTerminal(901);
	assert(coordinator.AuditAndReleaseSessionOwnerFence("agent-a", "session-1", true, reason));
	assert(!coordinator.IsSessionOwnerFenced("agent-a", "session-1"));

	ExecutionCoordinator recovered(journal, callbacks);
	assert(recovered.RecoverFromJournal(reason));
	assert(!recovered.IsSessionOwnerFenced("agent-a", "session-1"));
    ExecutionOrderOwner recoveredOwner;
	assert(recovered.GetOrderOwner(901, recoveredOwner));
	assert(recoveredOwner.executionDomain == "IB-PAPER:EUR.USD");
	std::size_t removedOwners = 0;
	assert(!recovered.ReconcileOrderOwners(std::set<long>(), false, removedOwners, reason));
	assert(reason == "AUTHORITATIVE_OPEN_ORDERS_INCOMPLETE");
	assert(recovered.ReconcileOrderOwners(std::set<long>(), true, removedOwners, reason));
	assert(removedOwners == 1);
	assert(!recovered.GetOrderOwner(901, recoveredOwner));
	ExecutionCoordinator reconciledReplay(journal, callbacks);
	assert(reconciledReplay.RecoverFromJournal(reason));
	assert(!reconciledReplay.GetOrderOwner(901, recoveredOwner));
    std::remove(path.c_str());
}

void TestRecoveryOnlySessionOwnerIsDurableAndAllowsOwnedCancel()
{
    const std::string path = TempJournalPath();
    OmsJournal journal;
    assert(journal.Init(path));
    int placeCalls = 0;
    int cancelCalls = 0;
    ExecutionCoordinatorCallbacks callbacks;
    callbacks.placeIbOrder = [&](const IBContractLite&,
        const IBOrderLite&, long* orderId) {
        ++placeCalls;
        *orderId = 902;
        return true;
    };
    callbacks.cancelIbOrder = [&](long orderId) {
        assert(orderId == 902);
        ++cancelCalls;
        return true;
    };
    callbacks.validateDecisionLease = [](const AgentExecutionContext&,
        const std::string&, std::string*) { return true; };

    ExecutionCoordinator coordinator(journal, callbacks);
    IbPlaceOrderCommand owned = MakePlace("owned-before-recovery-only");
    owned.context.executionDomain = "IB-PAPER:EUR.USD";
    owned.context.decisionLeaseFencingToken = 23;
    owned.context.decisionLeaseGeneration = 5;
    const ExecutionCommandResult accepted = coordinator.PlaceIbOrder(owned);
    assert(accepted.status == ExecutionCommandStatus::Accepted);
    assert(accepted.orderId == 902);

    std::string reason;
    assert(coordinator.EnterRecoveryOnlyOwner(
        "agent-a", "session-1", 7, reason));
    assert(coordinator.IsSessionOwnerRecoveryOnly(
        "agent-a", "session-1"));
    assert(!coordinator.IsSessionOwnerFenced("agent-a", "session-1"));
    const ExecutionCommandResult blocked = coordinator.PlaceIbOrder(
        MakePlace("entry-after-recovery-only"));
    assert(blocked.status == ExecutionCommandStatus::Rejected);
    assert(blocked.reasonCode == "SESSION_RECOVERY_ONLY");
    assert(placeCalls == 1);

    IbCancelOrderCommand cancel;
    cancel.context = owned.context;
    cancel.context.toolCallId = "owned-cancel-after-recovery-only";
    cancel.orderId = 902;
    cancel.instrument = "EUR.USD";
    cancel.side = "BUY";
    const ExecutionCommandResult cancelled =
        coordinator.CancelIbOrder(cancel);
    assert(cancelled.status == ExecutionCommandStatus::Accepted);
    assert(cancelCalls == 1);
    assert(coordinator.EnterRecoveryOnlyOwner(
        "agent-a", "session-1", 8, reason));
    assert(!coordinator.EnterRecoveryOnlyOwner(
        "agent-a", "session-1", 7, reason));
    assert(reason == "SESSION_RECOVERY_INGRESS_FENCE_STALE");

    ExecutionCoordinator recovered(journal, callbacks);
    assert(recovered.RecoverFromJournal(reason));
    assert(recovered.IsSessionOwnerRecoveryOnly(
        "agent-a", "session-1"));
    assert(!recovered.IsSessionOwnerFenced("agent-a", "session-1"));
    const ExecutionCommandResult restartBlocked = recovered.PlaceIbOrder(
        MakePlace("entry-after-recovery-only-restart"));
    assert(restartBlocked.status == ExecutionCommandStatus::Rejected);
    assert(restartBlocked.reasonCode == "SESSION_RECOVERY_ONLY");
    ExecutionCommandResult oldStatus;
    assert(recovered.GetCommandStatus(
        "agent-a", "session-1", "owned-before-recovery-only",
        oldStatus));
    assert(oldStatus.status == ExecutionCommandStatus::Accepted);
    assert(oldStatus.orderId == 902);
    std::remove(path.c_str());
}

} // namespace

int main()
{
    TestJournalBeforeSendAndDuplicate();
    TestJournalFailurePreventsBrokerSend();
    TestVenueCorrelationBindsCommandIdentity();
    TestPlaceCallbackUncertaintyAndReliableReject();
    TestIdempotencyIsScopedPerAgentSession();
    TestIdempotencyPayloadAndOperationConflict();
    TestIdempotencyConflictSurvivesReplayAndLegacyHashIsCompatible();
    TestOwnershipAndCancel();
    TestRecoveryBlocksUncertainIntent();
    TestProjectionFailureBlocksFurtherMutations();
    TestCancelProjectionFailureIsUncertain();
    TestFlattenLifecycleRecoveryAndRateProjection();
    TestFlattenVenueRejectCodeAllowlist();
    TestFlattenOrphanIntentRequiresDurableReconciliation();
    TestFlattenProjectionFailureBlocksAndRecovers();
    TestFlattenCallbackExceptionIsDurablyUncertain();
    TestFlattenRejectsSubToleranceOverCloseAndStaleNoop();
    TestFlattenNoopJournalFailureIsUncertain();
    TestRevokedSessionOwnerIsFenced();
    TestRecoveryOnlySessionOwnerIsDurableAndAllowsOwnedCancel();
    TestDurableAuthoritativeCorrelationResolution();
    TestDeferredCancelRecoveryResolution();
    TestDurableAuthoritativeCancelResolution();
    std::cout << "execution_coordinator_tests: PASS" << std::endl;
    return 0;
}
