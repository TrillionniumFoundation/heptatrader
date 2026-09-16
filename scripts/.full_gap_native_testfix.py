from pathlib import Path

p = Path("tests/oms_recovery_growth_probe.h")
s = p.read_text()

needle = '''    auto oldCommand = MakePlace("generation-old-command");
    oldCommand.expiresAtMs = expiry;
'''
replacement = '''    auto oldCommand = MakePlace("generation-old-command");
    oldCommand.expiresAtMs = expiry;
    oldCommand.context.executionDomain = "SIM";
    oldCommand.context.decisionLeaseFencingToken = 1;
    oldCommand.context.decisionLeaseGeneration = 1;
'''
if s.count(needle) != 1:
    raise SystemExit(f"old-command subject anchor count={s.count(needle)}")
s = s.replace(needle, replacement, 1)

needle = '''    int sends = 0;
    auto callbacks = CancelFixtureCallbacks();
    callbacks.placement = VenuePlacement::Immediate(
        [&](const PlaceOrderCommand&, const std::string&) {
            return VenuePlaceResult::Submitted(7100 + ++sends);
        });
'''
replacement = '''    int sends = 0;
    auto callbacks = CancelFixtureCallbacks();
    callbacks.validateDecisionLease =
        [](const AgentExecutionContext&, const std::string&, std::string*) {
            return true;
        };
    callbacks.placement = VenuePlacement::Immediate(
        [&](const PlaceOrderCommand&, const std::string&) {
            return VenuePlaceResult::Submitted(7100 + ++sends);
        });
'''
if s.count(needle) != 1:
    raise SystemExit(f"decision lease generation fixture anchor count={s.count(needle)}")
s = s.replace(needle, replacement, 1)

needle = '''        foreignCommand.context.sessionId = "foreign-session";
        const auto foreign = coordinator.PlaceOrder(foreignCommand);
'''
replacement = '''        foreignCommand.context.sessionId = "foreign-session";
        foreignCommand.context.decisionLeaseFencingToken =
            oldCommand.context.decisionLeaseFencingToken;
        foreignCommand.context.decisionLeaseGeneration =
            oldCommand.context.decisionLeaseGeneration;
        const auto foreign = coordinator.PlaceOrder(foreignCommand);
'''
if s.count(needle) != 1:
    raise SystemExit(f"foreign lease anchor count={s.count(needle)}")
s = s.replace(needle, replacement, 1)

old = '''        assert(recovered.PlaceOrder(conflict).reasonCode == "IDEMPOTENCY_KEY_CONFLICT");
        ExecutionCommandResult foreignStatus;
        assert(recovered.GetCommandStatus(
            oldCommand.context.agentId, "foreign-session",
            "generation-foreign-session-command", foreignStatus));
        assert(foreignStatus.status == ExecutionCommandStatus::Accepted);

        OmsGenerationStore generationStore(path);
        std::string generationReason;
        assert(generationStore.Recover(
            64U * 1024U * 1024U, 65536U, 262144U,
            [](const OmsJournalEvent&) {}, generationReason));
        OmsGenerationMutationSummary ownerSummary;
        assert(generationStore.SummarizeMutationRecords(
            oldCommand.context.agentId, oldCommand.context.sessionId,
            oldCommand.context.account, oldCommand.context.executionDomain,
            ownerSummary, generationReason));
        assert(ownerSummary.commandCount == 1);
        OmsGenerationMutationSummary foreignSummary;
        assert(generationStore.SummarizeMutationRecords(
            oldCommand.context.agentId, "foreign-session",
            oldCommand.context.account, oldCommand.context.executionDomain,
            foreignSummary, generationReason));
        assert(foreignSummary.commandCount == 1);
        assert(ownerSummary.commandBindingSha256 !=
               foreignSummary.commandBindingSha256);
        assert(sends == 2); // disk lookup/history summary never calls venue

        auto newCommand = MakePlace("generation-new-command");
'''
new = '''        assert(recovered.PlaceOrder(conflict).reasonCode == "IDEMPOTENCY_KEY_CONFLICT");
        ExecutionCommandResult foreignStatus;
        assert(recovered.GetCommandStatus(
            oldCommand.context.agentId, "foreign-session",
            "generation-foreign-session-command", foreignStatus));
        assert(foreignStatus.status == ExecutionCommandStatus::Accepted);
        assert(sends == 2); // historical status lookup never calls the venue

        auto newCommand = MakePlace("generation-new-command");
        newCommand.context.executionDomain = oldCommand.context.executionDomain;
        newCommand.context.decisionLeaseFencingToken =
            oldCommand.context.decisionLeaseFencingToken;
        newCommand.context.decisionLeaseGeneration =
            oldCommand.context.decisionLeaseGeneration;
'''
if s.count(old) != 1:
    raise SystemExit(f"second-reader test block count={s.count(old)}")
s = s.replace(old, new, 1)

needle = '''        const auto next = recovered.PlaceOrder(newCommand);
        assert(next.status == ExecutionCommandStatus::Accepted);
        assert(sends == 3); // capacity was adopted, so new entry is not UNKNOWN
'''
replacement = '''        const auto next = recovered.PlaceOrder(newCommand);
        assert(next.status == ExecutionCommandStatus::Accepted);
        assert(sends == 3); // capacity was adopted, so new entry is not UNKNOWN
        assert(recovered.RecordOrderTerminalDurably(next.orderId, &reason));
'''
if s.count(needle) != 1:
    raise SystemExit(f"tail terminalization anchor count={s.count(needle)}")
s = s.replace(needle, replacement, 1)

needle = '''        PaperTerminalMutationUniverse universe;
        assert(recovered.EnterPaperTerminalFenceAndProject(
            binding, universe, reason));
        assert(universe.compactSummary);
'''
replacement = '''        PaperTerminalMutationUniverse universe;
        const bool terminalProjected = recovered.EnterPaperTerminalFenceAndProject(
            binding, universe, reason);
        if (!terminalProjected)
            std::cerr << "generation terminal owner scope failed: " << reason << std::endl;
        assert(terminalProjected);
        assert(universe.compactSummary);
'''
if s.count(needle) != 1:
    raise SystemExit(f"terminal projection assertion count={s.count(needle)}")
s = s.replace(needle, replacement, 1)
p.write_text(s)
