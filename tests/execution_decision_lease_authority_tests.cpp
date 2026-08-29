#include "../HeptaTrade/execution/execution_decision_lease_authority.h"

#include <cassert>
#include <chrono>
#include <iostream>
#include <string>

namespace
{
AgentExecutionContext Context(const std::string& agentId,
                              const std::string& sessionId)
{
    AgentExecutionContext context;
    context.agentId = agentId;
    context.sessionId = sessionId;
    context.toolCallId = "tool-call";
    context.account = "DU123456";
    context.venue = "IB";
    context.executionDomain = "PAPER";
    context.decisionLeaseFencingToken = 777;
    context.decisionLeaseGeneration = 99;
    return context;
}

void TestServiceOwnsAndRenewsCredentialsPerInstrument()
{
    DecisionLeaseManager::TimePoint now;
    ExecutionDecisionLeaseAuthority authority(
        [&]() { return now; }, std::chrono::milliseconds(5000));
    std::string reason;

    AgentExecutionContext eur = Context("agent", "session");
    assert(authority.Authorize(eur, "EUR.USD", reason));
    assert(reason.empty());
    assert(eur.decisionLeaseFencingToken != 0);
    assert(eur.decisionLeaseFencingToken != 777);
    assert(eur.decisionLeaseGeneration != 99);
    const std::uint64_t eurToken = eur.decisionLeaseFencingToken;
    const std::uint64_t eurGeneration = eur.decisionLeaseGeneration;
    assert(authority.Validate(eur, "EUR.USD", nullptr));

    AgentExecutionContext renewed = Context("agent", "session");
    renewed.decisionLeaseFencingToken = 9000;
    renewed.decisionLeaseGeneration = 9000;
    assert(authority.Authorize(renewed, "EUR.USD", reason));
    assert(renewed.decisionLeaseFencingToken == eurToken);
    assert(renewed.decisionLeaseGeneration == eurGeneration);

    AgentExecutionContext gbp = Context("agent", "session");
    assert(authority.Authorize(gbp, "GBP.USD", reason));
    assert(gbp.decisionLeaseFencingToken > eurToken);
    assert(gbp.decisionLeaseGeneration == 1);
    assert(authority.Validate(gbp, "GBP.USD", nullptr));
    assert(!authority.Validate(gbp, "EUR.USD", nullptr));
}

void TestContentionExpiryAndOwnerFenceFailClosed()
{
    DecisionLeaseManager::TimePoint now;
    ExecutionDecisionLeaseAuthority authority(
        [&]() { return now; }, std::chrono::milliseconds(100));
    std::string reason;

    AgentExecutionContext first = Context("agent-a", "session-a");
    assert(authority.Authorize(first, "EUR.USD", reason));
    AgentExecutionContext contender = Context("agent-b", "session-b");
    assert(!authority.Authorize(contender, "EUR.USD", reason));
    assert(reason == "EXECUTION_DECISION_LEASE_BUSY");
    assert(contender.decisionLeaseFencingToken == 0);
    assert(contender.decisionLeaseGeneration == 0);

    assert(authority.FenceOwner("agent-a", "session-a") == 1);
    assert(!authority.Validate(first, "EUR.USD", nullptr));
    assert(authority.Authorize(contender, "EUR.USD", reason));
    assert(contender.decisionLeaseFencingToken >
           first.decisionLeaseFencingToken);
    assert(contender.decisionLeaseGeneration >
           first.decisionLeaseGeneration);

    now += std::chrono::milliseconds(101);
    AgentExecutionContext afterExpiry = Context("agent-c", "session-c");
    assert(authority.Authorize(afterExpiry, "EUR.USD", reason));
    assert(afterExpiry.decisionLeaseFencingToken >
           contender.decisionLeaseFencingToken);
    assert(!authority.Validate(contender, "EUR.USD", nullptr));
}

void TestInvalidContextCannotRetainUpstreamCredential()
{
    ExecutionDecisionLeaseAuthority authority;
    AgentExecutionContext invalid = Context("agent", "session");
    invalid.executionDomain.clear();
    std::string reason;
    assert(!authority.Authorize(invalid, "EUR.USD", reason));
    assert(reason == "EXECUTION_DECISION_LEASE_AUTHORIZATION_FAILED");
    assert(invalid.decisionLeaseFencingToken == 0);
    assert(invalid.decisionLeaseGeneration == 0);
}
}

int main()
{
    TestServiceOwnsAndRenewsCredentialsPerInstrument();
    TestContentionExpiryAndOwnerFenceFailClosed();
    TestInvalidContextCannotRetainUpstreamCredential();
    std::cout << "execution_decision_lease_authority_tests: PASS" << std::endl;
    return 0;
}
