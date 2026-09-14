#pragma once

namespace {
CancelOrderCommand CancelFor(const PlaceOrderCommand& place, const std::string& id)
{
    CancelOrderCommand cancel;
    cancel.context = place.context;
    cancel.context.toolCallId = id;
    cancel.orderId = 1901;
    cancel.instrument = place.instrument;
    return cancel;
}
ExecutionCoordinatorCallbacks CancelFixtureCallbacks()
{
    ExecutionCoordinatorCallbacks callbacks;
    callbacks.placement = VenuePlacement::Immediate(
        [](const PlaceOrderCommand&, const std::string&) {
            return VenuePlaceResult::Submitted(1901);
        });
    return callbacks;
}
void TestCancelUncertaintySurvivesReplayAndRequiresTerminalProof()
{
    // Each case invokes the real coordinator/journal, not a result-code mock.
    for (int fault = 0; fault < 6; ++fault)
    {
        const std::string path = TempJournalPath();
        const auto place = MakePlace("cancel-fault-place");
        const auto cancel = CancelFor(place, "cancel-fault");
        int sends = 0;
        auto callbacks = CancelFixtureCallbacks();
        callbacks.cancelOrder = [&](long id) -> VenueCancelResult {
            assert(id == 1901);
            ++sends;
            if (fault == 0) throw std::runtime_error("post-send exception");
            if (fault == 1) throw std::bad_alloc();
            if (fault == 2) throw 42;
            if (fault == 3) return VenueCancelResult::RejectedBeforeSend("");
            if (fault == 4) {
                VenueCancelResult invalid;
                invalid.disposition = static_cast<VenueCancelDisposition>(99);
                return invalid;
            }
            return VenueCancelResult::Uncertain("lost reply after send");
        };
        {
            OmsJournal journal;
            assert(journal.Init(path));
            ExecutionCoordinator coordinator(journal, callbacks);
            assert(coordinator.PlaceOrder(place).status == ExecutionCommandStatus::Accepted);
            const auto result = coordinator.CancelOrder(cancel);
            assert(sends == 1 && result.status == ExecutionCommandStatus::Uncertain);
            assert(coordinator.IsMutationBlocked());
            assert(coordinator.CancelOrder(cancel).status == ExecutionCommandStatus::Uncertain);
            assert(coordinator.PlaceOrder(MakePlace("new-risk")).reasonCode == "MUTATION_BLOCKED");
            auto changed = cancel; ++changed.orderId;
            assert(coordinator.CancelOrder(changed).reasonCode == "IDEMPOTENCY_KEY_CONFLICT");
            assert(sends == 1);
            int attempts = 0, uncertain = 0, rejects = 0;
            assert(journal.Replay([&](const OmsJournalEvent& event) {
                if (event.reqId != cancel.context.toolCallId) return;
                if (event.eventType == "cancel_send_attempt") ++attempts;
                if (event.eventType == "cancel" && event.status == "cancel_pending") ++uncertain;
                if (event.eventType == "reject") ++rejects;
            }) > 0);
            assert(attempts == 1 && uncertain == 1 && rejects == 0);
        }
        {
            OmsJournal journal;
            assert(journal.Init(path));
            ExecutionCoordinator recovered(journal, callbacks);
            std::string reason;
            assert(!recovered.RecoverFromJournal(reason));
            assert(recovered.CancelOrder(cancel).status == ExecutionCommandStatus::Uncertain);
            assert(sends == 1);
            std::size_t resolved = 0;
            assert(!recovered.ResolveUncertainCancelCommands({}, false, {}, {}, true, resolved, reason));
            assert(recovered.ResolveUncertainCancelCommands({1901}, true, {}, {}, true, resolved, reason));
            assert(resolved == 0); // active or absent is not terminal proof
            assert(recovered.ResolveUncertainCancelCommands({}, true, {}, {}, true, resolved, reason));
            assert(resolved == 0);
            const bool targetFilled = fault % 2 == 1;
            assert(recovered.ResolveUncertainCancelCommands({}, true,
                {{1901, targetFilled ? "Filled" : "Cancelled"}},
                targetFilled ? std::set<long>{1901} : std::set<long>{},
                true, resolved, reason));
            assert(resolved == 1 && !recovered.IsMutationBlocked());
            ExecutionCommandResult status;
            assert(recovered.GetCommandStatus("agent-a", "session-1", "cancel-fault", status));
            assert(status.status == (targetFilled ? ExecutionCommandStatus::Rejected :
                                                   ExecutionCommandStatus::Accepted));
            ExecutionCoordinator replay(journal, callbacks);
            assert(replay.RecoverFromJournal(reason));
            assert(replay.CancelOrder(cancel).status == ExecutionCommandStatus::Duplicate);
            assert(sends == 1);
        }
        std::remove(path.c_str());
    }
}
void TestCancelPreSendRefusalIsDistinctFromDeferred()
{
    for (int deferred = 0; deferred < 2; ++deferred)
    {
        const auto path = TempJournalPath();
        OmsJournal journal;
        assert(journal.Init(path));
        auto callbacks = CancelFixtureCallbacks();
        int calls = 0;
        callbacks.cancelOrder = [&](long) {
            ++calls;
            return deferred ? VenueCancelResult::Deferred() :
                VenueCancelResult::RejectedBeforeSend("KNOWN_PRE_SEND_REFUSAL");
        };
        ExecutionCoordinator coordinator(journal, callbacks);
        const auto place = MakePlace("cancel-presend-place");
        assert(coordinator.PlaceOrder(place).status == ExecutionCommandStatus::Accepted);
        const auto cancel = CancelFor(place, "cancel-presend");
        const auto result = coordinator.CancelOrder(cancel);
        assert(result.status == (deferred ? ExecutionCommandStatus::Uncertain : ExecutionCommandStatus::Rejected));
        assert(result.reasonCode == (deferred ? "IB_CANCEL_DEFERRED_UNTIL_BROKER_ACK" : "IB_CANCEL_REJECT"));
        assert(coordinator.CancelOrder(cancel).status == (deferred ? ExecutionCommandStatus::Uncertain : ExecutionCommandStatus::Duplicate));
        assert(calls == 1);
        ExecutionCoordinator replay(journal, callbacks);
        std::string reason;
        assert(replay.RecoverFromJournal(reason) == !deferred);
        assert(replay.CancelOrder(cancel).status == (deferred ? ExecutionCommandStatus::Uncertain : ExecutionCommandStatus::Duplicate));
        assert(calls == 1);
        std::remove(path.c_str());
    }
}
void TestCancelUncertainReceiptFailurePreservesAttempt()
{
    const auto path = TempJournalPath();
    const auto displaced = path + ".displaced";
    OmsJournal journal;
    assert(journal.Init(path));
    auto callbacks = CancelFixtureCallbacks();
    int sends = 0;
    callbacks.cancelOrder = [&](long) -> VenueCancelResult {
        ++sends;
        assert(std::rename(path.c_str(), displaced.c_str()) == 0);
        throw std::runtime_error("post-send path loss");
    };
    ExecutionCoordinator coordinator(journal, callbacks);
    const auto place = MakePlace("cancel-path-place");
    assert(coordinator.PlaceOrder(place).status == ExecutionCommandStatus::Accepted);
    const auto cancel = CancelFor(place, "cancel-path");
    const auto result = coordinator.CancelOrder(cancel);
    assert(result.status == ExecutionCommandStatus::Uncertain);
    assert(result.reasonCode == "OMS_CANCEL_UNCERTAIN_WRITE_FAILED");
    assert(coordinator.IsMutationBlocked());
    assert(coordinator.CancelOrder(cancel).status == ExecutionCommandStatus::Uncertain);
    assert(sends == 1);
    // Restart from the retained inode, with no fabricated uncertainty receipt.
    OmsJournal saved;
    assert(saved.Init(displaced));
    ExecutionCoordinator replay(saved, callbacks);
    std::string reason;
    assert(!replay.RecoverFromJournal(reason));
    assert(replay.CancelOrder(cancel).status == ExecutionCommandStatus::Uncertain);
    assert(sends == 1);
    std::remove(displaced.c_str());
}
} // namespace
