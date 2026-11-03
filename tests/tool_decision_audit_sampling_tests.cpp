#include "tool_host/tool_decision_audit.h"

#include <cassert>
#include <cstdio>
#include <cstdlib>
#include <iostream>
#include <string>
#include <unistd.h>

// Uses the real Gateway record builder and journal. No Execution service,
// credential, broker connection or order dispatch exists in this fixture.
int main()
{
    char temporary[] = "/tmp/hepta-audit-sampling-XXXXXX";
    const int fd = ::mkstemp(temporary); assert(fd >= 0); ::close(fd);
    SessionSupervisorAuditJournal journal;
    std::string reason;
    assert(journal.Init(temporary, reason));
    ToolDecisionAudit audit; audit.SetJournal(&journal);
    TradingToolHostSessionBinding binding;
    binding.peerUid = static_cast<std::uint32_t>(::geteuid());
    binding.session.executionContext.agentId = "fixture-agent";
    binding.session.executionContext.sessionId = "fixture-session";
    binding.executionDomain = "PAPER";
    binding.session.environment = "PAPER";
    TradingToolHostRequest request;
    request.call.name = "market.get_quote";
    request.call.instrument = "EUR.USD";
    request.expectedSchemaHash = "fixture-schema";
    unsigned serial = 0;
    auto append = [&](bool peer = true, bool mutation = false,
                      TradingToolCallStatus status = TradingToolCallStatus::Ok,
                      const std::string& payload = "{}") {
        request.toolCallId = "fixture-" + std::to_string(++serial);
        request.queueDeadlineAtMs = serial;
        TradingToolResult result; result.status = status; result.payloadJson = payload;
        audit.AppendOutcome(peer, binding.peerUid, &request, &binding, mutation, result);
        assert(result.status == status);
    };
    auto count = [&](std::uint64_t expected) {
        std::uint64_t actual = 0;
        assert(SessionSupervisorAuditJournal::Verify(temporary, actual, reason));
        assert(actual == expected);
    };
    append(); append(); count(1);
    assert(journal.CapacitySnapshot().observationsCoalesced == 1);
    request.call.instrument = "BTC.USD"; append(); count(2);
    request.call.afterEventSequence = 123; append(); count(3);
    request.expectedSchemaHash = "different-schema"; append(); count(4);
    binding.session.executionContext.sessionId = "different-session"; append(); count(5);
    append(true, false, TradingToolCallStatus::Ok, "{\"position\":1}"); count(6);
    append(true, false, TradingToolCallStatus::Ok, "{\"position\":1}"); count(6);
    append(); count(7); // A -> B -> A is a transition, not an old cached sample
    // An unauthenticated or disabled owner never gets the sampling privilege.
    append(false); append(false); count(9);
    binding.enabled = false; append(); append(); count(11);
    binding.enabled = true;
    append(true, false, TradingToolCallStatus::PermissionDenied);
    append(true, false, TradingToolCallStatus::PermissionDenied); count(13);
    append(); count(14); // a denied outcome invalidates the earlier success window
    request.call.name = "execution.get_command_status";
    append(); append(); count(16);
    request.call.name = "trade.place_order";
    append(true, true); append(true, true); count(18);
    request.call.name = "market.get_quote";
    append(); count(19); // critical audit cleared the earlier sample window
    append(); count(19);
    request.call.name = "system.tools.list"; append(); append(); count(20);
    assert(journal.CapacitySnapshot().observationsCoalesced == 4);
    std::remove(temporary);
    std::cout << "Gateway observation scope, owner, denied, unknown and mutation durability: PASS\n";
}
