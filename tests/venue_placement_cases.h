#include "../HeptaTrade/execution/new_entry_capacity.h"
#include <cstdlib>
#include <limits>

void TestNewEntryCapacityBoundaries()
{
    NewEntryCapacity capacity{true, false, 100, 100, 79, 79, 0, 0, 0};
    assert(NewEntryCapacityReason(capacity) == nullptr);
    capacity.pendingBytes = 1;
    assert(std::string(NewEntryCapacityReason(capacity)) == "OMS_NEW_ENTRY_CAPACITY_EXHAUSTED");
    capacity.pendingBytes = 0;
    capacity.queuedRecords = 1;
    assert(NewEntryCapacityReason(capacity));
    capacity.queuedRecords = 0;
    capacity.bufferedRecords = 1;
    assert(NewEntryCapacityReason(capacity));
    capacity.bufferedRecords = 0;
    capacity.known = false;
    assert(std::string(NewEntryCapacityReason(capacity)) == "OMS_NEW_ENTRY_CAPACITY_UNKNOWN");
    capacity.known = true;
    capacity.writePoisoned = true;
    assert(NewEntryCapacityReason(capacity));
    capacity.writePoisoned = false;
    capacity.maxBytes = 0;
    assert(NewEntryCapacityReason(capacity));
    const auto maximum = std::numeric_limits<std::uint64_t>::max();
    capacity = {true, false, maximum, maximum, maximum / 2, maximum / 2, 0, 0, 0};
    assert(!NewEntryCapacityReason(capacity));
    capacity.pendingBytes = maximum;
    assert(NewEntryCapacityReason(capacity));
    capacity.pendingBytes = 0;
    capacity.queuedRecords = maximum;
    assert(NewEntryCapacityReason(capacity));
    for (std::uint64_t limit = 1; limit < 200; ++limit)
        for (std::uint64_t written = 0; written < 220; ++written)
            for (std::uint64_t pending = 0; pending < 5; ++pending)
            {
                capacity = {true, false, limit, 1000, written, 0, pending, 0, 0};
                assert(bool(NewEntryCapacityReason(capacity)) ==
                    (written + pending >= limit - limit / 5));
            }
}

void TestCapacityPausePreservesExitsAndReplay()
{
    struct RecordBudget {
        bool present;
        std::string value;
        RecordBudget() : present(std::getenv("HEPTA_OMS_REPLAY_MAX_RECORDS") != nullptr),
            value(present ? std::getenv("HEPTA_OMS_REPLAY_MAX_RECORDS") : "")
        { assert(::setenv("HEPTA_OMS_REPLAY_MAX_RECORDS", "40", 1) == 0); }
        ~RecordBudget() {
            if (present) ::setenv("HEPTA_OMS_REPLAY_MAX_RECORDS", value.c_str(), 1);
            else ::unsetenv("HEPTA_OMS_REPLAY_MAX_RECORDS");
        }
    } budget;
    const std::string path = TempJournalPath();
    const auto original = MakePlace("capacity-original");
    int placeCalls = 0, cancelCalls = 0, flattenCalls = 0;
    ExecutionCoordinatorCallbacks callbacks;
    callbacks.placement = VenuePlacement::Immediate(
        [&](const PlaceOrderCommand&, const std::string&) {
            ++placeCalls;
            return VenuePlaceResult::Submitted(42);
        });
    callbacks.cancelIbOrder = [&](long id) {
        assert(id == 42); ++cancelCalls; return true;
    };
    callbacks.validateDecisionLease = [](const AgentExecutionContext&,
        const std::string&, std::string*) { return true; };
    callbacks.onIbOrderPlaced = [](const IbPlaceOrderCommand&, long,
        std::string*) { return true; };
    callbacks.placeIbReduceOnlyOrderCorrelated =
        [&](const AuthoritativeFlattenPlan& plan, const std::string&, long* id) {
            assert(plan.order.action == "SELL" && plan.order.totalQuantity == 100.0);
            ++flattenCalls; *id = 43; return true;
        };
    {
        OmsJournal journal;
        assert(journal.Init(path));
        ExecutionCoordinator coordinator(journal, callbacks);
        assert(coordinator.PlaceOrder(original).status == ExecutionCommandStatus::Accepted);
        for (;;)
        {
            const auto health = journal.GetHealthSnapshot();
            if (health.currentRecords + health.queueDepth + health.bufferedDepth >= 32) break;
            OmsJournalEvent event;
            event.eventType = "capacity_fixture";
            event.tsMs = OmsJournal::NowEpochMs();
            assert(journal.Append(event));
        }
        assert(journal.Replay([](const OmsJournalEvent&) {}) == 32);
        const auto before = journal.GetHealthSnapshot();
        for (int i = 0; i < 100; ++i)
        {
            const auto denied = MakePlace("capacity-denied-" + std::to_string(i));
            const auto result = coordinator.PlaceOrder(denied);
            assert(result.status == ExecutionCommandStatus::Rejected);
            assert(result.reasonCode == "OMS_NEW_ENTRY_CAPACITY_EXHAUSTED");
            ExecutionCommandResult status;
            assert(!coordinator.GetCommandStatus("agent-a", "session-1",
                denied.context.toolCallId, status));
        }
        const auto after = journal.GetHealthSnapshot();
        assert(before.currentBytes == after.currentBytes && before.currentRecords == after.currentRecords);
        assert(before.pendingBytes == after.pendingBytes && before.enqueuedTotal == after.enqueuedTotal);
        assert(!coordinator.IsMutationBlocked());
        assert(coordinator.PlaceOrder(original).status == ExecutionCommandStatus::Duplicate);
        auto conflict = original;
        conflict.order.totalQuantity += 1;
        assert(coordinator.PlaceOrder(conflict).reasonCode == "IDEMPOTENCY_KEY_CONFLICT");
        IbCancelOrderCommand cancel;
        cancel.context = original.context;
        cancel.context.toolCallId = "capacity-cancel";
        cancel.orderId = 42;
        cancel.instrument = "EUR.USD";
        cancel.side = "BUY";
        assert(coordinator.CancelOrder(cancel).status == ExecutionCommandStatus::Accepted);
        const auto flatten = MakeFlatten("capacity-flatten");
        assert(coordinator.ExecuteAuthoritativeFlatten(flatten, MakeFlattenPlan(flatten)).status ==
            ExecutionCommandStatus::Accepted);
        assert(placeCalls == 1 && cancelCalls == 1 && flattenCalls == 1);
        assert(journal.Replay([](const OmsJournalEvent&) {}) == 38);
    }
    {
        OmsJournal journal;
        assert(journal.Init(path));
        assert(!journal.GetHealthSnapshot().capacityKnown);
        ExecutionCoordinator recovered(journal, callbacks);
        assert(recovered.PlaceOrder(MakePlace("before-capacity-replay")).reasonCode ==
            "OMS_NEW_ENTRY_CAPACITY_UNKNOWN");
        std::string reason;
        assert(recovered.RecoverFromJournal(reason));
        assert(recovered.PlaceOrder(original).status == ExecutionCommandStatus::Duplicate);
        assert(recovered.PlaceOrder(MakePlace("after-capacity-replay")).reasonCode ==
            "OMS_NEW_ENTRY_CAPACITY_EXHAUSTED");
        assert(!recovered.IsMutationBlocked());
        assert(placeCalls == 1 && cancelCalls == 1 && flattenCalls == 1);
        assert(journal.Replay([](const OmsJournalEvent&) {}) == 38);
    }
    std::remove(path.c_str());
}

// Runs inside the existing real coordinator suite in both sanitizer lanes.
void TestVenuePlacementConstructionAndResultContract()
{
    TestNewEntryCapacityBoundaries();
    TestCapacityPausePreservesExitsAndReplay();
    int rejected = 0;
    try { VenuePlacement::Immediate(VenuePlacement::Submit()); }
    catch (const std::invalid_argument&) { ++rejected; }
    auto submit = [](const PlaceOrderCommand&, const std::string&) {
        return VenuePlaceResult::Reserved(5);
    };
    auto activate = [](long) { return VenueActivationResult::Activated(); };
    try { VenuePlacement::Reserving(submit, VenuePlacement::Activate()); }
    catch (const std::invalid_argument&) { ++rejected; }
    try { VenuePlacement::Reserving(VenuePlacement::Submit(), activate); }
    catch (const std::invalid_argument&) { ++rejected; }
    OmsJournal unused;
    ExecutionCoordinatorCallbacks invalid;
    invalid.placement = VenuePlacement::Reserving(submit, activate);
    try { ExecutionCoordinator coordinator(unused, invalid); }
    catch (const std::invalid_argument&) { ++rejected; }
    assert(rejected == 4);
    assert(unused.GetPath().empty());

    // Invalid/ambiguous outcomes retain intent/correlation and NEVER activate
    // or retry. Test real journal recovery, not merely enum conversion.
    for (int mode = 0; mode < 5; ++mode)
    {
        const std::string path = TempJournalPath();
        OmsJournal journal;
        assert(journal.Init(path));
        int calls = 0, activations = 0;
        ExecutionCoordinatorCallbacks callbacks;
        auto send = [&](const PlaceOrderCommand&, const std::string& correlation) {
            ++calls;
            assert(!correlation.empty());
            if (mode == 0) return VenuePlaceResult::Reserved(900);
            if (mode == 1) return VenuePlaceResult::Submitted(901);
            if (mode == 2) return VenuePlaceResult::Submitted(-1);
            if (mode == 3) return VenuePlaceResult::Rejected("");
            VenuePlaceResult invalidResult;
            invalidResult.disposition = static_cast<VenuePlaceDisposition>(777);
            return invalidResult;
        };
        callbacks.onIbOrderPlaced = [](const PlaceOrderCommand&, long, std::string*) { return true; };
        callbacks.placement = mode == 1
            ? VenuePlacement::Reserving(send, [&](long) { ++activations; return VenueActivationResult::Activated(); })
            : VenuePlacement::Immediate(send);
        ExecutionCoordinator coordinator(journal, callbacks);
        const auto command = MakePlace("malformed-venue-result");
        assert(coordinator.PlaceOrder(command).status == ExecutionCommandStatus::Uncertain);
        assert(coordinator.IsMutationBlocked());
        assert(coordinator.PlaceOrder(command).status == ExecutionCommandStatus::Uncertain);
        ExecutionCoordinator recovered(journal, callbacks);
        std::string reason;
        assert(!recovered.RecoverFromJournal(reason));
        assert(recovered.PlaceOrder(command).status == ExecutionCommandStatus::Uncertain);
        assert(calls == 1 && activations == 0);
        std::remove(path.c_str());
    }
}
