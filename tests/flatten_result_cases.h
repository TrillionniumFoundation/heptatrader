#pragma once

// Included in the existing real coordinator suite. No additional test lane.
void TestTypedFlattenResultPreservesDurableUncertainty()
{
    for (int fault = 0; fault != 9; ++fault)
    {
        const auto path = TempJournalPath();
        int calls = 0;
        ExecutionCoordinatorCallbacks callbacks;
        callbacks.validateDecisionLease = [](const AgentExecutionContext&,
            const std::string&, std::string*) { return true; };
        callbacks.flattenOrder = [&](const AuthoritativeFlattenPlan&,
                                      const std::string&) -> VenueFlattenResult {
            ++calls;
            if (fault == 0) throw std::runtime_error("possible external effect");
            if (fault == 1) throw 42;
            if (fault == 2) return VenueFlattenResult();
            if (fault == 3) return VenueFlattenResult::Submitted(-1);
            if (fault == 4) return VenueFlattenResult::RejectedBeforeSend(
                VenueFlattenRejection::Generic, "");
            if (fault == 5) return VenueFlattenResult::RejectedBeforeSend(
                static_cast<VenueFlattenRejection>(999), "not a known classification");
            if (fault == 6) {
                auto value = VenueFlattenResult::RejectedBeforeSend(
                    VenueFlattenRejection::Generic, "contradictory assigned order");
                value.orderId = 992; return value;
            }
            if (fault == 7) {
                auto value = VenueFlattenResult();
                value.disposition = static_cast<VenueFlattenDisposition>(999);
                return value;
            }
            return VenueFlattenResult::Uncertain("after possible send", 992);
        };
        const auto command = MakeFlatten("typed-flatten-uncertain");
        const auto plan = MakeFlattenPlan(command);
        {
            OmsJournal journal; assert(journal.Init(path));
            ExecutionCoordinator coordinator(journal, callbacks);
            const auto result = coordinator.ExecuteAuthoritativeFlatten(command, plan);
            assert(result.status == ExecutionCommandStatus::Uncertain);
            assert(result.reasonCode == "IB_FLATTEN_OUTCOME_UNCERTAIN");
            assert(coordinator.IsMutationBlocked() && calls == 1);
            const auto metrics = coordinator.RuntimeObservation();
            assert(metrics.operations[2].reasonCounts[
                ExecutionReasonIndex("IB_FLATTEN_OUTCOME_UNCERTAIN")] == 1);
            assert(coordinator.ExecuteAuthoritativeFlatten(command, plan).status == ExecutionCommandStatus::Uncertain);
            auto conflict = command; conflict.instrument = "GBP.USD";
            assert(coordinator.ExecuteAuthoritativeFlatten(conflict, MakeFlattenPlan(conflict)).reasonCode == "IDEMPOTENCY_KEY_CONFLICT");
            int attempts = 0, uncertain = 0, rejects = 0;
            assert(journal.Replay([&](const OmsJournalEvent& event) {
                if (event.reqId != command.context.toolCallId) return;
                if (event.eventType == "flatten_send_attempt") ++attempts;
                if (event.eventType == "flatten_outcome_uncertain") ++uncertain;
                if (event.eventType == "flatten_reject") ++rejects;
            }) > 0);
            assert(attempts == 1 && uncertain == 1 && rejects == 0 && calls == 1);
        }
        {
            OmsJournal journal; assert(journal.Init(path));
            ExecutionCoordinator recovered(journal, callbacks); std::string reason;
            assert(!recovered.RecoverFromJournal(reason));
            assert(reason == "RECOVERY_RECONCILE_REQUIRED");
            const auto replay = recovered.ExecuteAuthoritativeFlatten(command, plan);
            assert(replay.status == ExecutionCommandStatus::Uncertain && calls == 1);
            auto conflict = command; conflict.instrument = "GBP.USD";
            assert(recovered.ExecuteAuthoritativeFlatten(conflict, MakeFlattenPlan(conflict)).reasonCode == "IDEMPOTENCY_KEY_CONFLICT");
        }
        std::remove(path.c_str());
    }
}

void TestTypedFlattenReasonIsIndependentOfDiagnosticText()
{
    const auto path = TempJournalPath();
    const auto command = MakeFlatten("typed-flatten-rejected");
    const auto plan = MakeFlattenPlan(command);
    int calls = 0;
    ExecutionCoordinatorCallbacks callbacks;
    callbacks.validateDecisionLease = [](const AgentExecutionContext&,
        const std::string&, std::string*) { return true; };
    callbacks.flattenOrder = [&](const AuthoritativeFlattenPlan&, const std::string&) {
        ++calls;
        auto result = VenueFlattenResult::RejectedBeforeSend(
            VenueFlattenRejection::QuoteChangedBeforeSend, "initial message");
        // This looks like another canonical code but is only diagnostic text.
        result.detail = "IB_PAPER_KILL_SWITCH_ENGAGED";
        return result;
    };
    {
        OmsJournal journal; assert(journal.Init(path));
        ExecutionCoordinator coordinator(journal, callbacks);
        const auto result = coordinator.ExecuteAuthoritativeFlatten(command, plan);
        assert(result.status == ExecutionCommandStatus::Rejected);
        assert(result.reasonCode == "IB_PAPER_FLATTEN_QUOTE_CHANGED_BEFORE_SEND");
        assert(result.detail == "IB_PAPER_KILL_SWITCH_ENGAGED");
        assert(!coordinator.IsMutationBlocked());
        assert(coordinator.ExecuteAuthoritativeFlatten(command, plan).status == ExecutionCommandStatus::Duplicate);
        assert(calls == 1);
    }
    {
        OmsJournal journal; assert(journal.Init(path));
        ExecutionCoordinator recovered(journal, callbacks); std::string reason;
        assert(recovered.RecoverFromJournal(reason));
        ExecutionCommandResult result;
        assert(recovered.GetCommandStatus(command.context.agentId, command.context.sessionId,
            command.context.toolCallId, result));
        assert(result.status == ExecutionCommandStatus::Rejected);
        assert(result.reasonCode == "IB_PAPER_FLATTEN_QUOTE_CHANGED_BEFORE_SEND");
        assert(result.detail == "IB_PAPER_KILL_SWITCH_ENGAGED");
        assert(recovered.ExecuteAuthoritativeFlatten(command, plan).status == ExecutionCommandStatus::Duplicate);
        assert(calls == 1);
    }
    std::remove(path.c_str());
}
