// Included by the canonical coordinator test executable. No alternate test
// composition: callbacks, journal and public status APIs are the real ones.
void TestPreIntentRefusalsDoNotRetainCommandIdentities()
{
    const std::string path = TempJournalPath();
    OmsJournal journal;
    assert(journal.Init(path));
    int sends = 0;
    bool leaseAllowed = false;
    ExecutionCoordinatorCallbacks callbacks;
    callbacks.placement = VenuePlacement::Immediate(
        [&](const PlaceOrderCommand&, const std::string&) {
            return VenuePlaceResult::Submitted(400 + ++sends);
        });
    callbacks.cancelOrder = [](long) { return VenueCancelResult::Submitted(); };
    callbacks.validateDecisionLease = [&](const AgentExecutionContext&,
        const std::string&, std::string* reason) {
        if (reason) *reason = "fixture lease not granted";
        return leaseAllowed;
    };
    ExecutionCoordinator coordinator(journal, callbacks);
    const auto accepted = MakePlace("retained-accepted");
    assert(coordinator.PlaceOrder(accepted).status == ExecutionCommandStatus::Accepted);
    const auto baseline = journal.GetHealthSnapshot();
    for (int i = 0; i < 4096; ++i)
    {
        auto invalid = MakePlace("invalid-place-" + std::to_string(i));
        invalid.order.totalQuantity = 0;
        assert(coordinator.PlaceOrder(invalid).reasonCode == "INVALID_ORDER");
        ExecutionCommandResult status;
        assert(!coordinator.GetCommandStatus(invalid.context.agentId,
            invalid.context.sessionId, invalid.context.toolCallId, status));
        CancelOrderCommand cancel;
        cancel.context = MakePlace("invalid-cancel-" + std::to_string(i)).context;
        cancel.orderId = -1;
        assert(coordinator.CancelOrder(cancel).reasonCode == "INVALID_CANCEL");
        assert(!coordinator.GetCommandStatus(cancel.context.agentId,
            cancel.context.sessionId, cancel.context.toolCallId, status));
    }
    const auto observed = coordinator.RuntimeObservation();
    assert(observed.present && observed.retainedCommands == 1);
    assert(observed.orderOwners == 1 && observed.retainedSendAttempts == 1);
    assert(observed.operations[0].results[0] == 1 && observed.operations[0].results[1] == 4096);
    assert(observed.operations[0].latency.samples == 4097);
    assert(observed.operations[1].results[1] == 4096 && observed.operations[1].latency.samples == 4096);
    assert(!observed.mutationBlocked);
    const auto after = journal.GetHealthSnapshot();
    assert(after.currentRecords == baseline.currentRecords);
    assert(after.currentBytes == baseline.currentBytes && sends == 1);
    assert(coordinator.PlaceOrder(accepted).status == ExecutionCommandStatus::Duplicate);
    auto conflict = accepted;
    conflict.order.totalQuantity += 1;
    assert(coordinator.PlaceOrder(conflict).reasonCode == "IDEMPOTENCY_KEY_CONFLICT");

    // No intent, no send: the same normalized request may be re-evaluated
    // after the actual authority changes. This is not a possibly-sent retry.
    auto awaitingLease = MakePlace("awaiting-lease");
    awaitingLease.context.executionDomain = "PAPER";
    awaitingLease.context.decisionLeaseGeneration = 1;
    awaitingLease.context.decisionLeaseFencingToken = 1;
    assert(coordinator.PlaceOrder(awaitingLease).reasonCode == "DECISION_LEASE_INVALID");
    ExecutionCommandResult status;
    assert(!coordinator.GetCommandStatus("agent-a", "session-1", "awaiting-lease", status));
    leaseAllowed = true;
    assert(coordinator.PlaceOrder(awaitingLease).status == ExecutionCommandStatus::Accepted);
    assert(coordinator.PlaceOrder(awaitingLease).status == ExecutionCommandStatus::Duplicate);
    assert(sends == 2);

    // Post-intent rejections remain durable, queryable and never re-sent.
    ExecutionCoordinatorCallbacks rejectedCallbacks;
    rejectedCallbacks.placement = VenuePlacement::Immediate(
        [&](const PlaceOrderCommand&, const std::string&) {
            ++sends;
            return VenuePlaceResult::Rejected("fixture venue rejection");
        });
    ExecutionCoordinator rejected(journal, rejectedCallbacks);
    const auto denied = MakePlace("retained-venue-reject");
    assert(rejected.PlaceOrder(denied).status == ExecutionCommandStatus::Rejected);
    assert(rejected.GetCommandStatus("agent-a", "session-1", denied.context.toolCallId, status));
    assert(status.status == ExecutionCommandStatus::Rejected);
    assert(rejected.PlaceOrder(denied).status == ExecutionCommandStatus::Duplicate);
    ExecutionCoordinator replay(journal, callbacks);
    std::string reason;
    assert(replay.RecoverFromJournal(reason));
    const auto recoveredMetrics = replay.RuntimeObservation();
    assert(recoveredMetrics.recoveryLatency.samples == 1 && recoveredMetrics.retainedCommands == 3);
    assert(recoveredMetrics.operations[0].latency.samples == 0); // process counters are not replayed
    assert(replay.PlaceOrder(accepted).status == ExecutionCommandStatus::Duplicate);
    assert(replay.PlaceOrder(denied).status == ExecutionCommandStatus::Duplicate);
    assert(sends == 3);
    std::remove(path.c_str());
}

void TestBlockedRefusalFloodDoesNotEraseUncertainIdentity()
{
    const std::string path = TempJournalPath();
    OmsJournal journal;
    assert(journal.Init(path));
    int sends = 0;
    ExecutionCoordinatorCallbacks callbacks;
    callbacks.placement = VenuePlacement::Immediate(
        [&](const PlaceOrderCommand&, const std::string&) {
            ++sends;
            return VenuePlaceResult::Uncertain("response lost after possible send");
        });
    callbacks.cancelOrder = [](long) { return VenueCancelResult::Submitted(); };
    ExecutionCoordinator coordinator(journal, callbacks);
    const auto uncertain = MakePlace("retained-uncertain");
    assert(coordinator.PlaceOrder(uncertain).status == ExecutionCommandStatus::Uncertain);
    const auto baseline = journal.GetHealthSnapshot();
    for (int i = 0; i < 4096; ++i)
    {
        auto other = MakePlace("blocked-refusal-" + std::to_string(i));
        assert(coordinator.PlaceOrder(other).reasonCode == "MUTATION_BLOCKED");
        ExecutionCommandResult status;
        assert(!coordinator.GetCommandStatus("agent-a", "session-1", other.context.toolCallId, status));
    }
    assert(journal.GetHealthSnapshot().currentBytes == baseline.currentBytes);
    assert(coordinator.PlaceOrder(uncertain).status == ExecutionCommandStatus::Uncertain);
    ExecutionCoordinator recovered(journal, callbacks);
    std::string reason;
    assert(!recovered.RecoverFromJournal(reason));
    const auto observation = recovered.RuntimeObservation();
    assert(observation.recoveryLatency.samples == 1 && observation.mutationBlocked);
    assert(observation.retainedCommands == 1);
    assert(recovered.PlaceOrder(uncertain).status == ExecutionCommandStatus::Uncertain);
    assert(sends == 1);
    std::remove(path.c_str());
}

void TestCoordinatorMeasurementsPreserveExceptionsAndFlattenRejection()
{
    const std::string path = TempJournalPath();
    OmsJournal journal;
    assert(journal.Init(path));
    ExecutionCoordinatorCallbacks callbacks;
    callbacks.placement = VenuePlacement::Immediate(
        [](const PlaceOrderCommand&, const std::string&) { assert(false); return VenuePlaceResult(); });
    callbacks.validateDecisionLease = [](const AgentExecutionContext&, const std::string&, std::string*) -> bool {
        throw std::runtime_error("fixture pre-intent lease exception");
    };
    ExecutionCoordinator coordinator(journal, callbacks);
    auto command = MakePlace("measured-lease-exception");
    command.context.executionDomain = "PAPER";
    command.context.decisionLeaseGeneration = command.context.decisionLeaseFencingToken = 1;
    bool caught = false;
    try { coordinator.PlaceOrder(command); } catch (const std::runtime_error&) { caught = true; }
    assert(caught);
    const auto observation = coordinator.RuntimeObservation();
    assert(observation.operations[0].results[4] == 1 && observation.operations[0].latency.samples == 1);
    assert(observation.retainedCommands == 0 && journal.GetHealthSnapshot().currentRecords == 0);
    auto flatten = MakeFlatten("measured-invalid-flatten");
    flatten.context.agentId.clear();
    assert(coordinator.ExecuteAuthoritativeFlatten(flatten, MakeFlattenPlan(flatten)).status == ExecutionCommandStatus::Rejected);
    assert(coordinator.RuntimeObservation().operations[2].results[1] == 1);
    assert(coordinator.RuntimeObservation().operations[2].latency.samples == 1);
    std::remove(path.c_str());
}
