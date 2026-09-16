#!/usr/bin/env python3
from pathlib import Path


def replace_one(text: str, old: str, new: str, label: str) -> str:
    count = text.count(old)
    if count != 1:
        raise SystemExit(f"{label}: expected exactly one match, got {count}")
    return text.replace(old, new, 1)


path = Path("tests/oms_recovery_growth_probe.h")
text = path.read_text()
text = replace_one(text,
'''    const auto expiry = OmsJournal::NowEpochMs() + 86400000;
    auto oldCommand = MakePlace("generation-old-command");
    oldCommand.expiresAtMs = expiry;
    auto foreignCommand = MakePlace("generation-foreign-session");
    foreignCommand.context.sessionId += "-foreign";
    foreignCommand.expiresAtMs = expiry;
''',
'''    const auto expiry = OmsJournal::NowEpochMs() + 86400000;
    const auto bindPaperContext = [expiry](IbPlaceOrderCommand& command) {
        command.context.executionDomain = "IB-PAPER:EUR.USD";
        command.context.decisionLeaseFencingToken = 7;
        command.context.decisionLeaseGeneration = 3;
        command.expiresAtMs = expiry;
    };
    auto oldCommand = MakePlace("generation-old-command");
    bindPaperContext(oldCommand);
    auto foreignCommand = MakePlace("generation-foreign-session");
    foreignCommand.context.sessionId += "-foreign";
    bindPaperContext(foreignCommand);
''', "paper fixture context")
text = replace_one(text,
'''    callbacks.placement = VenuePlacement::Immediate(
        [&](const PlaceOrderCommand&, const std::string&) {
            return VenuePlaceResult::Submitted(7100 + ++sends);
        });
''',
'''    callbacks.placement = VenuePlacement::Immediate(
        [&](const PlaceOrderCommand&, const std::string&) {
            return VenuePlaceResult::Submitted(7100 + ++sends);
        });
    callbacks.validateDecisionLease = [](const AgentExecutionContext&,
        const std::string&, std::string*) { return true; };
''', "decision lease fixture")
for old, new, label in (
    ("coordinator.PlaceOrder(oldCommand)", "coordinator.PlaceIbOrder(oldCommand)", "initial paper place"),
    ("coordinator.PlaceOrder(foreignCommand)", "coordinator.PlaceIbOrder(foreignCommand)", "foreign paper place"),
    ("recovered.PlaceOrder(oldCommand)", "recovered.PlaceIbOrder(oldCommand)", "duplicate paper place"),
    ("recovered.PlaceOrder(conflict)", "recovered.PlaceIbOrder(conflict)", "conflict paper place"),
    ("recovered.PlaceOrder(newCommand)", "recovered.PlaceIbOrder(newCommand)", "new paper place"),
):
    text = replace_one(text, old, new, label)
text = replace_one(text,
'''        auto newCommand = MakePlace("generation-new-command");
        newCommand.expiresAtMs = expiry;
''',
'''        auto newCommand = MakePlace("generation-new-command");
        bindPaperContext(newCommand);
''', "new paper context")
path.write_text(text)
