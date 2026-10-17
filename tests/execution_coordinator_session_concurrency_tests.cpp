#include "execution_coordinator_test_support.h"

namespace hepta_execution_coordinator_test {

void TestRevokedSessionOwnerIsFenced()
{
    const std::string path = TempJournalPath();
    OmsJournal journal;
    assert(journal.Init(path));
    int placeCalls = 0;
    ExecutionCoordinatorCallbacks callbacks;
    callbacks.placement = VenuePlacement::Immediate([&](const PlaceOrderCommand&, const std::string& ) -> VenuePlaceResult {
        long venueOrderId = -1;
        ++placeCalls;
        venueOrderId = 901;
        return VenuePlaceResult::Submitted(venueOrderId);
    });
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
    callbacks.placement = VenuePlacement::Immediate([&](const PlaceOrderCommand&, const std::string& ) -> VenuePlaceResult {
        long venueOrderId = -1;
        ++placeCalls;
        venueOrderId = 902;
        return VenuePlaceResult::Submitted(venueOrderId);
    });
    callbacks.cancelOrder = [&](long orderId) {
        assert(orderId == 902);
        ++cancelCalls;
        return VenueCancelResult::Submitted();
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

void TestTwoPhaseActivationDurabilityAndRecovery()
{
    for (int outcome = 0; outcome < 6; ++outcome)
    {
        const std::string path = TempJournalPath();
        OmsJournal journal;
        assert(journal.Init(path));
        int reservations = 0;
        int activations = 0;
        bool projected = false;
        ExecutionCoordinatorCallbacks callbacks;
        VenuePlacement::Submit reserve = [&](const PlaceOrderCommand&, const std::string&) -> VenuePlaceResult {
        long venueOrderId = -1;
            ++reservations;
            venueOrderId = 1701;
            return VenuePlaceResult::Reserved(venueOrderId);
        };
        callbacks.onIbOrderPlaced = [&](const IbPlaceOrderCommand&, long, std::string*) {
            projected = true;
            // Fail before activation, once at projection and once at receipt IO.
            if (outcome == 4) assert(::unlink(path.c_str()) == 0);
            return outcome != 3;
        };
        callbacks.placement = VenuePlacement::Reserving(reserve, [&](long id) -> VenueActivationResult {
            std::string reason;
            ++activations;
            assert(id == 1701 && projected);
            int receipts = 0;
            std::vector<std::string> preActivationEvents;
            assert(journal.Replay([&](const OmsJournalEvent& event) {
                preActivationEvents.push_back(event.eventType);
                if (event.eventType == "place_sent" && event.orderId == id) ++receipts;
            }) == 3);
            assert(receipts == 1);
            assert(preActivationEvents == std::vector<std::string>(
                {"order_intent", "place_send_attempt", "place_sent"}));
            // The pair has one barrier; the reservation receipt has its own
            // later barrier. Activation must not cross either boundary early.
            const auto durable = journal.GetHealthSnapshot();
            assert(durable.criticalSyncWrites == 3);
            assert(durable.durableSyncWrites == 2);
            // The durable prefix is exactly what a process crash before or
            // during activation leaves behind. It must never replay accepted.
            ExecutionCoordinator interrupted(journal, callbacks);
            std::string recoveryReason;
            assert(!interrupted.RecoverFromJournal(recoveryReason));
            assert(interrupted.IsMutationBlocked());
            ExecutionCommandResult interruptedStatus;
            assert(interrupted.GetCommandStatus("agent-a", "session-1", "two-phase", interruptedStatus));
            assert(interruptedStatus.status == ExecutionCommandStatus::Uncertain);
            if (outcome == 2) throw std::runtime_error("activation threw");
            if (outcome == 1) reason = "activation rejected";
            if (outcome == 5) assert(::unlink(path.c_str()) == 0);
            return outcome == 0 || outcome == 5 ? VenueActivationResult::Activated() : VenueActivationResult::Uncertain(reason);
        });
        ExecutionCoordinator coordinator(journal, callbacks);
        const IbPlaceOrderCommand command = MakePlace("two-phase");
        const ExecutionCommandResult result = coordinator.PlaceIbOrder(command);
        assert(reservations == 1);
        assert(activations == (outcome == 3 || outcome == 4 ? 0 : 1));
        if (outcome == 0)
        {
            assert(result.status == ExecutionCommandStatus::Accepted);
            assert(coordinator.PlaceIbOrder(command).status == ExecutionCommandStatus::Duplicate);
            assert(reservations == 1 && activations == 1);
            ExecutionCoordinator recovered(journal, callbacks);
            std::string reason;
            assert(recovered.RecoverFromJournal(reason));
            ExecutionOrderOwner owner;
            assert(recovered.GetOrderOwner(1701, owner));
            assert(recovered.PlaceIbOrder(command).status == ExecutionCommandStatus::Duplicate);
            assert(recovered.RecordOrderTerminalDurably(1701, &reason));
            assert(!recovered.GetOrderOwner(1701, owner));
            assert(recovered.RecordOrderTerminalDurably(1701, &reason));
            int terminalEvents = 0;
            assert(journal.Replay([&](const OmsJournalEvent& event) {
                if (event.eventType == "order_owner_reconciled_terminal") ++terminalEvents;
            }) >= 0);
            assert(terminalEvents == 1);
            ExecutionCoordinator terminalReplay(journal, callbacks);
            assert(terminalReplay.RecoverFromJournal(reason));
            assert(!terminalReplay.GetOrderOwner(1701, owner));
        }
        else
        {
            assert(result.status == ExecutionCommandStatus::Uncertain);
            assert(coordinator.IsMutationBlocked());
            assert(coordinator.PlaceIbOrder(command).status == ExecutionCommandStatus::Uncertain);
            assert(reservations == 1);
        }
        if (outcome == 1 || outcome == 2)
        {
            std::vector<std::string> events;
            assert(journal.Replay([&](const OmsJournalEvent& event) {
                events.push_back(event.eventType);
            }) == 4);
            assert(events[2] == "place_sent");
            assert(events[3] == "place_outcome_uncertain");
            ExecutionCoordinator recovered(journal, callbacks);
            std::string reason;
            assert(!recovered.RecoverFromJournal(reason));
            assert(recovered.IsMutationBlocked());
            const ExecutionCommandResult retry = recovered.PlaceIbOrder(command);
            assert(retry.status == ExecutionCommandStatus::Uncertain);
            assert(retry.reasonCode == "IB_PLACE_OUTCOME_UNCERTAIN");
            assert(reservations == 1 && activations == 1);
        }
        std::remove(path.c_str());
    }
}


void TestSlowVenueDispatchDoesNotHoldCoordinatorLock()
{
    const std::string path = TempJournalPath();
    OmsJournal journal;
    assert(journal.Init(path));

    std::mutex gateMutex;
    std::condition_variable gateChanged;
    bool slowEntered = false;
    bool releaseSlow = false;
    int placeCalls = 0;
    int cancelCalls = 0;
    ExecutionCoordinatorCallbacks callbacks;
    callbacks.placement = VenuePlacement::Immediate(
        [&](const PlaceOrderCommand& command, const std::string&) {
            ++placeCalls;
            if (command.context.toolCallId != "slow-venue")
                return VenuePlaceResult::Submitted(42);
            {
                std::lock_guard<std::mutex> gate(gateMutex);
                slowEntered = true;
            }
            gateChanged.notify_all();
            std::unique_lock<std::mutex> gate(gateMutex);
            gateChanged.wait(gate, [&]() { return releaseSlow; });
            return VenuePlaceResult::Submitted(43);
        });
    callbacks.cancelOrder = [&](long orderId) {
        assert(orderId == 42);
        ++cancelCalls;
        return VenueCancelResult::Submitted();
    };

    ExecutionCoordinator coordinator(journal, callbacks);
    const auto seed = MakePlace("slow-venue-seed");
    assert(coordinator.PlaceOrder(seed).status == ExecutionCommandStatus::Accepted);

    std::promise<ExecutionCommandResult> slowPromise;
    std::future<ExecutionCommandResult> slowFuture = slowPromise.get_future();
    std::thread slow([&]() {
        slowPromise.set_value(coordinator.PlaceOrder(MakePlace("slow-venue")));
    });
    {
        std::unique_lock<std::mutex> gate(gateMutex);
        assert(gateChanged.wait_for(
            gate, std::chrono::seconds(2), [&]() { return slowEntered; }));
    }

    auto statusFuture = std::async(std::launch::async, [&]() {
        ExecutionCommandResult status;
        const bool found = coordinator.GetCommandStatus(
            "agent-a", "session-1", "slow-venue", status);
        return std::make_pair(found, status);
    });
    CancelOrderCommand cancel;
    cancel.context = seed.context;
    cancel.context.toolCallId = "slow-venue-cancel";
    cancel.orderId = 42;
    cancel.instrument = seed.instrument;
    auto cancelFuture = std::async(std::launch::async, [&]() {
        return coordinator.CancelOrder(cancel);
    });
    auto parallelRiskFuture = std::async(std::launch::async, [&]() {
        return coordinator.PlaceOrder(
            MakePlace("slow-venue-parallel-risk"));
    });

    assert(statusFuture.wait_for(std::chrono::seconds(2)) ==
        std::future_status::ready);
    assert(cancelFuture.wait_for(std::chrono::seconds(2)) ==
        std::future_status::ready);
    assert(parallelRiskFuture.wait_for(std::chrono::seconds(2)) ==
        std::future_status::ready);
    const auto status = statusFuture.get();
    assert(status.first);
    assert(status.second.status == ExecutionCommandStatus::Uncertain);
    assert(status.second.reasonCode == "BROKER_RESULT_PENDING");
    assert(cancelFuture.get().status == ExecutionCommandStatus::Accepted);
    const auto parallelRisk = parallelRiskFuture.get();
    assert(parallelRisk.status == ExecutionCommandStatus::Rejected);
    assert(parallelRisk.reasonCode == "MUTATION_BLOCKED");
    assert(cancelCalls == 1);

    {
        std::lock_guard<std::mutex> gate(gateMutex);
        releaseSlow = true;
    }
    gateChanged.notify_all();
    assert(slowFuture.get().status == ExecutionCommandStatus::Accepted);
    slow.join();
    assert(placeCalls == 2);
    std::remove(path.c_str());
}


void TestCancelEligibilityPreflightDoesNotHoldCoordinatorLock()
{
    const std::string path = TempJournalPath();
    OmsJournal journal;
    assert(journal.Init(path));

    std::mutex gateMutex;
    std::condition_variable gateChanged;
    bool preflightEntered = false;
    bool releasePreflight = false;
    int cancelCalls = 0;
    ExecutionCoordinatorCallbacks callbacks;
    callbacks.placement = VenuePlacement::Immediate(
        [](const PlaceOrderCommand&, const std::string&) {
            return VenuePlaceResult::Submitted(701);
        });
    callbacks.canCancelIbOrder =
        [&](long orderId, std::string* reason) {
            assert(orderId == 701);
            {
                std::lock_guard<std::mutex> gate(gateMutex);
                preflightEntered = true;
            }
            gateChanged.notify_all();
            std::unique_lock<std::mutex> gate(gateMutex);
            gateChanged.wait(gate, [&]() { return releasePreflight; });
            if (reason) reason->clear();
            return true;
        };
    callbacks.cancelOrder = [&](long orderId) {
        assert(orderId == 701);
        ++cancelCalls;
        return VenueCancelResult::Submitted();
    };

    ExecutionCoordinator coordinator(journal, callbacks);
    const auto seed = MakePlace("cancel-preflight-seed");
    assert(coordinator.PlaceOrder(seed).status ==
        ExecutionCommandStatus::Accepted);

    CancelOrderCommand cancel;
    cancel.context = seed.context;
    cancel.context.toolCallId = "cancel-preflight";
    cancel.orderId = 701;
    cancel.instrument = seed.instrument;
    auto cancelFuture = std::async(std::launch::async, [&]() {
        return coordinator.CancelOrder(cancel);
    });
    {
        std::unique_lock<std::mutex> gate(gateMutex);
        assert(gateChanged.wait_for(
            gate, std::chrono::seconds(2),
            [&]() { return preflightEntered; }));
    }

    auto statusFuture = std::async(std::launch::async, [&]() {
        ExecutionCommandResult status;
        const bool found = coordinator.GetCommandStatus(
            "agent-a", "session-1", "cancel-preflight-seed", status);
        return std::make_pair(found, status);
    });
    assert(statusFuture.wait_for(std::chrono::seconds(2)) ==
        std::future_status::ready);
    const auto status = statusFuture.get();
    assert(status.first);
    assert(status.second.status == ExecutionCommandStatus::Accepted);

    CancelOrderCommand competing = cancel;
    competing.context.toolCallId = "cancel-preflight-competing";
    auto competingFuture = std::async(std::launch::async, [&]() {
        return coordinator.CancelOrder(competing);
    });
    assert(competingFuture.wait_for(std::chrono::seconds(2)) ==
        std::future_status::ready);
    const auto competingResult = competingFuture.get();
    assert(competingResult.status == ExecutionCommandStatus::Rejected);
    assert(competingResult.reasonCode == "CANCEL_PREFLIGHT_IN_FLIGHT");
    ExecutionCommandResult competingStatus;
    assert(!coordinator.GetCommandStatus(
        "agent-a", "session-1", "cancel-preflight-competing",
        competingStatus));
    assert(cancelCalls == 0);

    // Authority may change while the coordinator lock is released. Revalidate
    // before persisting cancel intent or invoking the venue.
    assert(coordinator.FenceSessionOwner("agent-a", "session-1") == 1);
    {
        std::lock_guard<std::mutex> gate(gateMutex);
        releasePreflight = true;
    }
    gateChanged.notify_all();

    const auto result = cancelFuture.get();
    assert(result.status == ExecutionCommandStatus::Rejected);
    assert(result.reasonCode == "SESSION_OWNER_FENCED");
    assert(cancelCalls == 0);
    ExecutionCommandResult absent;
    assert(!coordinator.GetCommandStatus(
        "agent-a", "session-1", "cancel-preflight", absent));
    std::remove(path.c_str());
}


void TestReconnectFenceRefusesVenueDispatchInFlight()
{
    const std::string path = TempJournalPath();
    OmsJournal journal;
    assert(journal.Init(path));

    std::mutex gateMutex;
    std::condition_variable gateChanged;
    bool entered = false;
    bool release = false;
    ExecutionCoordinatorCallbacks callbacks;
    callbacks.placement = VenuePlacement::Immediate(
        [&](const PlaceOrderCommand&, const std::string&) {
            {
                std::lock_guard<std::mutex> gate(gateMutex);
                entered = true;
            }
            gateChanged.notify_all();
            std::unique_lock<std::mutex> gate(gateMutex);
            gateChanged.wait(gate, [&]() { return release; });
            return VenuePlaceResult::Submitted(501);
        });

    ExecutionCoordinator coordinator(journal, callbacks);
    std::promise<ExecutionCommandResult> placePromise;
    std::future<ExecutionCommandResult> placeFuture = placePromise.get_future();
    std::thread place([&]() {
        placePromise.set_value(
            coordinator.PlaceOrder(MakePlace("reconnect-dispatch-race")));
    });
    {
        std::unique_lock<std::mutex> gate(gateMutex);
        assert(gateChanged.wait_for(
            gate, std::chrono::seconds(2), [&]() { return entered; }));
    }

    std::string reason;
    assert(!coordinator.BeginBrokerReconnectFence(reason));
    assert(reason == "IB_PAPER_BROKER_RECONNECT_VENUE_DISPATCH_IN_FLIGHT");
    assert(!coordinator.IsMutationBlocked());

    {
        std::lock_guard<std::mutex> gate(gateMutex);
        release = true;
    }
    gateChanged.notify_all();
    assert(placeFuture.get().status == ExecutionCommandStatus::Accepted);
    place.join();

    assert(!coordinator.BeginBrokerReconnectFence(reason));
    assert(reason == "IB_PAPER_BROKER_RECONNECT_LOCAL_ORDERS_UNSAFE");
    std::remove(path.c_str());
}


void TestSessionFenceTracksRiskDispatchOutsideCoordinatorLock()
{
    const std::string path = TempJournalPath();
    OmsJournal journal;
    assert(journal.Init(path));

    std::mutex gateMutex;
    std::condition_variable gateChanged;
    bool entered = false;
    bool release = false;
    ExecutionCoordinatorCallbacks callbacks;
    callbacks.placement = VenuePlacement::Immediate(
        [&](const PlaceOrderCommand&, const std::string&) {
            {
                std::lock_guard<std::mutex> gate(gateMutex);
                entered = true;
            }
            gateChanged.notify_all();
            std::unique_lock<std::mutex> gate(gateMutex);
            gateChanged.wait(gate, [&]() { return release; });
            return VenuePlaceResult::Submitted(903);
        });

    ExecutionCoordinator coordinator(journal, callbacks);
    const auto command = MakePlace("fence-dispatch-race");
    auto placeFuture = std::async(std::launch::async, [&]() {
        return coordinator.PlaceOrder(command);
    });
    {
        std::unique_lock<std::mutex> gate(gateMutex);
        assert(gateChanged.wait_for(
            gate, std::chrono::seconds(2), [&]() { return entered; }));
    }

    // The durable send boundary has been crossed but the order owner cannot be
    // projected until the provider returns. Fencing must not report a false
    // zero or allow the fence to be released through that gap.
    assert(coordinator.RuntimeObservation().riskMutationDispatchInFlight);
    assert(coordinator.FenceSessionOwner("agent-a", "session-1") == 1);
    assert(coordinator.RuntimeObservation().riskMutationDispatchInFlight);
    assert(coordinator.IsSessionOwnerFenced("agent-a", "session-1"));
    std::string reason;
    assert(!coordinator.AuditAndReleaseSessionOwnerFence(
        "agent-a", "session-1", true, reason));
    assert(reason == "FENCED_OWNER_VENUE_DISPATCH_IN_FLIGHT");

    ExecutionCommandResult pending;
    assert(coordinator.GetCommandStatus(
        "agent-a", "session-1", "fence-dispatch-race", pending));
    assert(pending.status == ExecutionCommandStatus::Uncertain);

    {
        std::lock_guard<std::mutex> gate(gateMutex);
        release = true;
    }
    gateChanged.notify_all();
    const ExecutionCommandResult placed = placeFuture.get();
    assert(placed.status == ExecutionCommandStatus::Accepted);
    assert(placed.orderId == 903);
    assert(!coordinator.RuntimeObservation().riskMutationDispatchInFlight);

    ExecutionOrderOwner owner;
    assert(coordinator.GetOrderOwner(903, owner));
    assert(owner.agentId == "agent-a" && owner.sessionId == "session-1");
    assert(!coordinator.AuditAndReleaseSessionOwnerFence(
        "agent-a", "session-1", true, reason));
    assert(reason == "FENCED_OWNER_ACTIVE_ORDERS_REMAIN");
    assert(coordinator.RecordOrderTerminalDurably(903, &reason));
    assert(coordinator.AuditAndReleaseSessionOwnerFence(
        "agent-a", "session-1", true, reason));
    assert(reason.empty());
    assert(!coordinator.IsSessionOwnerFenced("agent-a", "session-1"));
    std::remove(path.c_str());
}

void TestSessionFenceTracksFlattenDispatchOutsideCoordinatorLock()
{
    const std::string path = TempJournalPath();
    OmsJournal journal;
    assert(journal.Init(path));

    std::mutex gateMutex;
    std::condition_variable gateChanged;
    bool entered = false;
    bool release = false;
    ExecutionCoordinatorCallbacks callbacks;
    callbacks.validateDecisionLease =
        [](const AgentExecutionContext&, const std::string&,
           std::string*) { return true; };
    callbacks.flattenOrder =
        [&](const AuthoritativeFlattenPlan&, const std::string&) {
            {
                std::lock_guard<std::mutex> gate(gateMutex);
                entered = true;
            }
            gateChanged.notify_all();
            std::unique_lock<std::mutex> gate(gateMutex);
            gateChanged.wait(gate, [&]() { return release; });
            return VenueFlattenResult::Submitted(904);
        };
    callbacks.onIbOrderPlaced =
        [](const IbPlaceOrderCommand&, long, std::string*) {
            return true;
        };

    ExecutionCoordinator coordinator(journal, callbacks);
    const FlattenPositionCommand command =
        MakeFlatten("fence-flatten-dispatch-race");
    const AuthoritativeFlattenPlan plan = MakeFlattenPlan(command);
    assert(!coordinator.RuntimeObservation().riskMutationDispatchInFlight);
    auto flattenFuture = std::async(std::launch::async, [&]() {
        return coordinator.ExecuteAuthoritativeFlatten(command, plan);
    });
    {
        std::unique_lock<std::mutex> gate(gateMutex);
        assert(gateChanged.wait_for(
            gate, std::chrono::seconds(2), [&]() { return entered; }));
    }

    assert(coordinator.RuntimeObservation().riskMutationDispatchInFlight);
    assert(coordinator.FenceSessionOwner("agent-a", "session-1") == 1);
    assert(coordinator.IsSessionOwnerFenced("agent-a", "session-1"));
    assert(coordinator.RuntimeObservation().riskMutationDispatchInFlight);
    std::string reason;
    assert(!coordinator.AuditAndReleaseSessionOwnerFence(
        "agent-a", "session-1", true, reason));
    assert(reason == "FENCED_OWNER_VENUE_DISPATCH_IN_FLIGHT");

    ExecutionCommandResult pending;
    assert(coordinator.GetCommandStatus(
        "agent-a", "session-1", "fence-flatten-dispatch-race", pending));
    assert(pending.status == ExecutionCommandStatus::Uncertain);

    {
        std::lock_guard<std::mutex> gate(gateMutex);
        release = true;
    }
    gateChanged.notify_all();
    const ExecutionCommandResult flattened = flattenFuture.get();
    assert(flattened.status == ExecutionCommandStatus::Accepted);
    assert(flattened.orderId == 904);
    assert(!coordinator.RuntimeObservation().riskMutationDispatchInFlight);

    ExecutionOrderOwner owner;
    assert(coordinator.GetOrderOwner(904, owner));
    assert(owner.agentId == "agent-a" && owner.sessionId == "session-1");
    assert(!coordinator.AuditAndReleaseSessionOwnerFence(
        "agent-a", "session-1", true, reason));
    assert(reason == "FENCED_OWNER_ACTIVE_ORDERS_REMAIN");
    assert(coordinator.RecordOrderTerminalDurably(904, &reason));
    assert(coordinator.AuditAndReleaseSessionOwnerFence(
        "agent-a", "session-1", true, reason));
    assert(reason.empty());
    std::remove(path.c_str());
}


void TestAccountNewEntryFencePersistsAcrossSessions()
{
    const std::string path=TempJournalPath();
    OmsJournal journal;
    assert(journal.Init(path));
    int sends=0;
    ExecutionCoordinatorCallbacks callbacks;
    callbacks.placement=VenuePlacement::Immediate(
        [&](const PlaceOrderCommand&,const std::string&) {
            ++sends;return VenuePlaceResult::Submitted(800 + sends);
        });
    ExecutionCoordinator coordinator(journal,callbacks);
    AgentExecutionContext custodian=MakePlace("maint-account-1").context;
    custodian.account="DU123";custodian.venue="IB";
    custodian.executionDomain="PAPER";
    std::size_t outstanding=777;
    std::string reason;
    const auto before = coordinator.ObserveAccountLocalDrain(custodian);
    assert(before.present && !before.accountEntryFenced);
    assert(!before.localHotProjectionQuiescent);
    assert(!coordinator.ObserveAccountLocalDrain(AgentExecutionContext()).present);
    assert(coordinator.EnterAccountNewEntryFence(
        custodian,outstanding,reason));
    assert(outstanding==0);
    assert(reason=="ACCOUNT_NEW_ENTRY_FENCE_INSTALLED_NOT_STOP_PERMIT");
    const auto first = coordinator.ObserveAccountLocalDrain(custodian);
    assert(first.present && first.accountEntryFenced);
    assert(first.ownedOrders==0 && first.uncertainCommands==0);
    assert(first.unattributedOwnedOrders==0 && first.unattributedUncertainCommands==0);
    assert(first.venueDispatchesInFlight==0);
    assert(first.localHotProjectionQuiescent);
    assert(coordinator.IsAccountNewEntryFenced("DU123"));
    assert(!coordinator.IsAccountNewEntryFenced("DU_FOREIGN"));
    auto crypto=MakePlace("crypto-new-after-maint");
    crypto.context.agentId="crypto-owner";
    crypto.context.sessionId="crypto-session";
    crypto.context.executionDomain="PAPER";
    const auto rejected=coordinator.PlaceOrder(crypto);
    assert(rejected.status==ExecutionCommandStatus::Rejected);
    assert(rejected.reasonCode=="ACCOUNT_NEW_ENTRY_FENCED");
    auto fx=MakePlace("fx-new-after-maint");
    fx.context.agentId="fx-other-owner";
    fx.context.sessionId="fx-session";
    fx.context.executionDomain="IB-PAPER:EUR.USD";
    assert(coordinator.PlaceOrder(fx).reasonCode=="ACCOUNT_NEW_ENTRY_FENCED");
    assert(sends==0);
    auto foreign=MakePlace("foreign-account");
    foreign.context.account="DU_OTHER";
    assert(coordinator.PlaceOrder(foreign).status==ExecutionCommandStatus::Accepted);
    assert(sends==1);
    // An order on a DIFFERENT account is not our local account risk.
    assert(coordinator.ObserveAccountLocalDrain(custodian).localHotProjectionQuiescent);
    custodian.toolCallId="maint-account-repeat";
    assert(coordinator.EnterAccountNewEntryFence(custodian,outstanding,reason));
    assert(outstanding==0); // The foreign account is not scope of this lock.
    // An unattributed original owner is not the same as a proven other
    // Broker account. It must remain an account-maintenance blocker.
    auto unknown=MakePlace("missing-account-original");
    unknown.context.account.clear();
    const auto unattributed=coordinator.PlaceOrder(unknown);
    assert(unattributed.status==ExecutionCommandStatus::Accepted);
    assert(sends==2);
    const auto unknownState=coordinator.ObserveAccountLocalDrain(custodian);
    assert(unknownState.unattributedOwnedOrders==1);
    assert(!unknownState.localHotProjectionQuiescent);
    custodian.toolCallId="maint-account-unknown-repeat";
    assert(coordinator.EnterAccountNewEntryFence(custodian,outstanding,reason));
    assert(outstanding>=1);
    assert(reason=="ACCOUNT_NEW_ENTRY_FENCE_INSTALLED_RISK_OUTSTANDING");
    assert(coordinator.RecordOrderTerminalDurably(unattributed.orderId,&reason));
    const auto unknownCleared=coordinator.ObserveAccountLocalDrain(custodian);
    assert(unknownCleared.unattributedOwnedOrders==0);
    assert(unknownCleared.localHotProjectionQuiescent);
    ExecutionCoordinator recovered(journal,callbacks);
    assert(recovered.RecoverFromJournal(reason));
    assert(recovered.IsAccountNewEntryFenced("DU123"));
    const auto replayed = recovered.ObserveAccountLocalDrain(custodian);
    assert(replayed.present && replayed.accountEntryFenced);
    assert(replayed.ownedOrders==0 && replayed.localHotProjectionQuiescent);
    assert(!recovered.IsAccountNewEntryFenced("DU_OTHER"));
    auto later=MakePlace("post-restart-btc");
    later.context.agentId="third-agent";
    later.context.sessionId="new-session";
    assert(recovered.PlaceOrder(later).reasonCode=="ACCOUNT_NEW_ENTRY_FENCED");
    assert(sends==2);
    ::unlink(path.c_str());
}

void TestAccountNewEntryFenceReportsInFlightAndDoesNotResend()
{
    const std::string path=TempJournalPath();
    OmsJournal journal;
    assert(journal.Init(path));
    std::mutex gate;
    std::condition_variable changed;
    bool entered=false,release=false;
    int sends=0;
    ExecutionCoordinatorCallbacks callbacks;
    callbacks.placement=VenuePlacement::Immediate(
        [&](const PlaceOrderCommand&, const std::string&) {
            {
                std::lock_guard<std::mutex> lock(gate);
                entered=true;++sends;
            }
            changed.notify_all();
            std::unique_lock<std::mutex> lock(gate);
            changed.wait(lock,[&]{return release;});
            return VenuePlaceResult::Submitted(980);
        });
    ExecutionCoordinator coordinator(journal,callbacks);
    auto old=MakePlace("original-risk-already-dispatched");
    auto running=std::async(std::launch::async,[&] {
        return coordinator.PlaceOrder(old);
    });
    {
        std::unique_lock<std::mutex> lock(gate);
        assert(changed.wait_for(lock,std::chrono::seconds(2),
                                [&]{return entered;}));
    }
    assert(coordinator.RuntimeObservation().riskMutationDispatchInFlight);
    AgentExecutionContext control=old.context;
    control.toolCallId="account-fence-during-send";
    std::size_t outstanding=0;
    std::string reason;
    assert(coordinator.EnterAccountNewEntryFence(control,outstanding,reason));
    assert(outstanding>=1);
    assert(reason=="ACCOUNT_NEW_ENTRY_FENCE_INSTALLED_RISK_OUTSTANDING");
    const auto sending = coordinator.ObserveAccountLocalDrain(control);
    assert(sending.present && sending.accountEntryFenced);
    assert(sending.riskMutationDispatchInFlight);
    assert(sending.venueDispatchesInFlight>=1);
    assert(sending.uncertainCommands>=1);
    assert(!sending.localHotProjectionQuiescent);
    auto competing=MakePlace("different-session-must-reject");
    competing.context.agentId="other";
    competing.context.sessionId="session2";
    assert(coordinator.PlaceOrder(competing).reasonCode=="ACCOUNT_NEW_ENTRY_FENCED");
    assert(sends==1);
    {
        std::lock_guard<std::mutex> lock(gate);
        release=true;
    }
    changed.notify_all();
    assert(running.get().status==ExecutionCommandStatus::Accepted);
    assert(!coordinator.RuntimeObservation().riskMutationDispatchInFlight);
    const auto brokerAcknowledged = coordinator.ObserveAccountLocalDrain(control);
    assert(!brokerAcknowledged.riskMutationDispatchInFlight);
    assert(brokerAcknowledged.venueDispatchesInFlight==0);
    assert(brokerAcknowledged.ownedOrders>=1);
    assert(!brokerAcknowledged.localHotProjectionQuiescent);
    assert(sends==1);
    assert(coordinator.IsAccountNewEntryFenced("DU123"));
    assert(coordinator.RecordOrderTerminalDurably(980,&reason));
    const auto localDrained = coordinator.ObserveAccountLocalDrain(control);
    assert(localDrained.accountEntryFenced && localDrained.localHotProjectionQuiescent);
    assert(localDrained.ownedOrders==0 && localDrained.uncertainCommands==0);
    assert(coordinator.PlaceOrder(
        MakePlace("new-after-original-return")).reasonCode=="ACCOUNT_NEW_ENTRY_FENCED");
    ExecutionCoordinator recovered(journal,callbacks);
    assert(recovered.RecoverFromJournal(reason));
    assert(recovered.IsAccountNewEntryFenced("DU123"));
    assert(sends==1);
    ::unlink(path.c_str());
}


void TestAccountNewEntryFenceCountsCancelVenueDispatch()
{
    const std::string path=TempJournalPath();
    OmsJournal journal;
    assert(journal.Init(path));
    std::mutex gate;
    std::condition_variable changed;
    bool entered=false,release=false;
    unsigned cancels=0;
    ExecutionCoordinatorCallbacks callbacks;
    callbacks.placement=VenuePlacement::Immediate(
        [](const PlaceOrderCommand&, const std::string&) {
            return VenuePlaceResult::Submitted(991);
        });
    callbacks.canCancelIbOrder=[](long id,std::string* reason) {
        if (reason) reason->clear();
        return id==991;
    };
    callbacks.cancelOrder=[&](long id) {
        assert(id==991);
        {
            std::lock_guard<std::mutex> lock(gate);
            entered=true;++cancels;
        }
        changed.notify_all();
        std::unique_lock<std::mutex> lock(gate);
        changed.wait(lock,[&]{return release;});
        return VenueCancelResult::Submitted();
    };
    ExecutionCoordinator coordinator(journal,callbacks);
    const auto place=MakePlace("cancel-boundary-seed");
    const auto original=coordinator.PlaceOrder(place);
    assert(original.status==ExecutionCommandStatus::Accepted);
    assert(original.orderId==991);
    CancelOrderCommand cancel;
    cancel.context=place.context;
    cancel.context.toolCallId="cancel-in-flight-before-account-fence";
    cancel.orderId=991;
    cancel.instrument=place.instrument;
    auto running=std::async(std::launch::async,[&] {
        return coordinator.CancelOrder(cancel);
    });
    {
        std::unique_lock<std::mutex> lock(gate);
        assert(changed.wait_for(lock,std::chrono::seconds(2),
                                [&]{return entered;}));
    }
    AgentExecutionContext control=place.context;
    control.toolCallId="account-cancel-drain-test";
    std::size_t outstanding=0;
    std::string reason;
    assert(coordinator.EnterAccountNewEntryFence(control,outstanding,reason));
    assert(outstanding>=2); // Original active order AND venue CANCEL send.
    assert(reason=="ACCOUNT_NEW_ENTRY_FENCE_INSTALLED_RISK_OUTSTANDING");
    const auto during=coordinator.ObserveAccountLocalDrain(control);
    assert(during.present && during.accountEntryFenced);
    assert(!during.riskMutationDispatchInFlight); // A CANCEL is not a new entry.
    assert(during.venueDispatchesInFlight>=1);
    assert(during.ownedOrders==1);
    assert(!during.localHotProjectionQuiescent);
    {
        std::lock_guard<std::mutex> lock(gate);
        release=true;
    }
    changed.notify_all();
    const auto cancelled=running.get();
    assert(cancelled.status==ExecutionCommandStatus::Accepted);
    assert(cancels==1);
    const auto callbackReturned=coordinator.ObserveAccountLocalDrain(control);
    assert(callbackReturned.venueDispatchesInFlight==0);
    assert(callbackReturned.ownedOrders==1);
    assert(!callbackReturned.localHotProjectionQuiescent);
    // A callback return is NOT Broker terminal evidence. Only the native
    // durable terminal record clears the exact original owner projection.
    assert(coordinator.RecordOrderTerminalDurably(991,&reason));
    const auto terminal=coordinator.ObserveAccountLocalDrain(control);
    assert(terminal.accountEntryFenced && terminal.localHotProjectionQuiescent);
    assert(terminal.ownedOrders==0 && terminal.uncertainCommands==0);
    ::unlink(path.c_str());
}


void TestAccountNewEntryFenceJournalFailureFailsClosed()
{
    const std::string path=TempJournalPath();
    OmsJournal journal;
    assert(journal.Init(path));
    struct stat original{};
    assert(::stat(path.c_str(),&original)==0);
    CoordinatorSyncFault::device=original.st_dev;
    CoordinatorSyncFault::inode=original.st_ino;
    int sends=0;
    ExecutionCoordinatorCallbacks callbacks;
    callbacks.placement=VenuePlacement::Immediate(
        [&](const PlaceOrderCommand&,const std::string&) {
            ++sends;return VenuePlaceResult::Submitted(999);
        });
    ExecutionCoordinator coordinator(journal,callbacks);
    AgentExecutionContext context=MakePlace("account-fence-fsync-fault").context;
    std::size_t pending=0;
    std::string reason;
    CoordinatorSyncFault::armed.store(true);
    const bool accepted=coordinator.EnterAccountNewEntryFence(
        context,pending,reason);
    CoordinatorSyncFault::armed.store(false);
    assert(!accepted);
    assert(reason=="OMS_ACCOUNT_NEW_ENTRY_FENCE_WRITE_FAILED");
    assert(coordinator.IsAccountNewEntryFenced("DU123"));
    assert(coordinator.IsMutationBlocked());
    const auto failed = coordinator.ObserveAccountLocalDrain(context);
    assert(failed.present && failed.accountEntryFenced && failed.mutationBlocked);
    assert(!failed.localHotProjectionQuiescent);
    assert(failed.mutationBlockReason=="OMS_ACCOUNT_NEW_ENTRY_FENCE_WRITE_FAILED");
    auto command=MakePlace("different-owner-after-fence-write-failure");
    command.context.agentId="different-owner";
    command.context.sessionId="different-session";
    const auto refused=coordinator.PlaceOrder(command);
    assert(refused.status==ExecutionCommandStatus::Rejected);
    assert(sends==0);
    assert(journal.GetHealthSnapshot().writePoisoned);
    ::unlink(path.c_str());
}

void TestCompactTerminalUniverseExceedsLegacyEnumerationLimit()
{
    const std::string empty =
        "sha256:e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855";
    PaperTerminalMutationRecord tail;
    tail.agentId = "agent-a";
    tail.sessionId = "session-1";
    tail.toolCallId = "tail-command";
    tail.operation = "place";
    tail.venueCorrelationId = "venue-tail";

    PaperTerminalMutationUniverse universe;
    std::string reason;
    assert(BuildPaperTerminalPartitionedUniverse(
        5000, empty, 5000, empty, {tail}, universe, reason));
    assert(reason.empty());
    assert(universe.compactSummary);
    assert(universe.commands.empty());
    assert(universe.correlations.empty());
    assert(universe.commandCount == 5001);
    assert(universe.correlationCount == 5001);

    PaperTerminalFenceBinding binding;
    binding.owner.agentId = "agent-a";
    binding.owner.sessionId = "session-1";
    binding.owner.account = "DU123";
    binding.owner.executionDomain = "PAPER";
    binding.finalizationId = "finalize-large";
    binding.preliminaryReceiptSha256 = empty;
    binding.recoveryIngressFence = 1;
    binding.serviceEpoch = "service-1";
    binding.serviceFencingGeneration = 1;
    binding.serviceProcessId = 1;
    binding.serviceProcessStartTicks = 1;
    binding.brokerConnectionEpoch = 1;
    binding.brokerSocketIdentitySha256 = empty;

    PaperTerminalMutationManifest manifest;
    assert(BuildPaperTerminalMutationManifest(
        binding, universe, manifest, reason));
    assert(reason.empty());
    assert(manifest.universe.compactSummary);
    assert(manifest.universe.commandCount == 5001);
    assert(manifest.contents.size() < 4096);
}

} // namespace hepta_execution_coordinator_test
