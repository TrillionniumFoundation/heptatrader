#include "intent/authoritative_decision_snapshot.h"

#include <cassert>
#include <string>

namespace
{
std::string Health(const char* epoch, unsigned long long fence,
                   unsigned long long eventWatermark)
{
    return std::string("{\"gateway_ready\":true,\"remote_execution_ready\":true,") +
        "\"execution_service_epoch\":\"" + epoch +
        "\",\"execution_service_fencing_generation\":" +
        std::to_string(fence) + ",\"event_watermark\":" +
        std::to_string(eventWatermark) + "}";
}

AuthoritativeDecisionSnapshotPayloads Payloads()
{
    AuthoritativeDecisionSnapshotPayloads payloads;
    payloads.healthBefore = Health("epoch-a", 4, 12);
    payloads.healthAfter = Health("epoch-a", 4, 12);
    payloads.quote =
        "{\"source\":\"SIMULATOR\",\"authoritative\":true,"
        "\"instrument\":\"EUR.USD\",\"observed_at_ms\":1005,"
        "\"stale_after_ms\":2000,\"stale\":false,"
        "\"bid\":1.1,\"ask\":1.1002}";
    payloads.account =
        "{\"source\":\"SIMULATOR\",\"authoritative\":true,"
        "\"account_complete\":true}";
    payloads.positions =
        "{\"source\":\"SIMULATOR\",\"authoritative\":true,"
        "\"positions\":[{\"instrument\":\"EUR.USD\",\"quantity\":2.5}]}";
    payloads.orders =
        "{\"source\":\"SIMULATOR\",\"authoritative\":true,"
        "\"active_order_ids\":[]}";
    payloads.riskLimits =
        "{\"source\":\"SIMULATOR\",\"authoritative\":true,"
        "\"max_order_quantity\":10}";
    return payloads;
}

bool Build(const AuthoritativeDecisionSnapshotPayloads& payloads,
           TargetPositionDecisionSnapshot& snapshot,
           std::string& output,
           std::string& code)
{
    std::string detail;
    return AuthoritativeDecisionSnapshotCodec::Build(
        "agent-a", "session-a", "SIM", "SIM:default", "EUR.USD",
        1000, 1010, 7, payloads, snapshot, output, code, detail);
}

void TestBuildsCompoundSnapshot()
{
    TargetPositionDecisionSnapshot snapshot;
    std::string output;
    std::string code;
    assert(Build(Payloads(), snapshot, output, code));
    assert(code.empty());
    assert(snapshot.executionServiceEpoch == "epoch-a");
    assert(snapshot.fencingGeneration == 4);
    assert(snapshot.eventWatermark == 12);
    assert(snapshot.collectionWatermark == 7);
    assert(snapshot.currentPosition == 2.5);
    assert(snapshot.bid == 1.1);
    assert(snapshot.ask == 1.1002);
    assert(output.find("\"schema\":\"hepta.decision-snapshot.v1\"") !=
           std::string::npos);
    assert(output.find("\"authoritative\":true") != std::string::npos);
}

void TestMissingInstrumentMeansFlat()
{
    AuthoritativeDecisionSnapshotPayloads payloads = Payloads();
    payloads.positions =
        "{\"source\":\"SIMULATOR\",\"authoritative\":true,"
        "\"positions\":[]}";
    TargetPositionDecisionSnapshot snapshot;
    std::string output;
    std::string code;
    assert(Build(payloads, snapshot, output, code));
    assert(snapshot.currentPosition == 0.0);
}

void TestRejectsIdentityAndEventDrift()
{
    AuthoritativeDecisionSnapshotPayloads payloads = Payloads();
    payloads.healthAfter = Health("epoch-b", 4, 12);
    TargetPositionDecisionSnapshot snapshot;
    std::string output;
    std::string code;
    assert(!Build(payloads, snapshot, output, code));
    assert(code == "DECISION_SNAPSHOT_EXECUTION_IDENTITY_CHANGED");

    payloads = Payloads();
    payloads.healthAfter = Health("epoch-a", 4, 13);
    assert(!Build(payloads, snapshot, output, code));
    assert(code == "DECISION_SNAPSHOT_EVENT_WATERMARK_CHANGED");
}

void TestRejectsIncompleteAndStaleComponents()
{
    AuthoritativeDecisionSnapshotPayloads payloads = Payloads();
    payloads.account =
        "{\"source\":\"SIMULATOR\",\"authoritative\":false}";
    TargetPositionDecisionSnapshot snapshot;
    std::string output;
    std::string code;
    assert(!Build(payloads, snapshot, output, code));
    assert(code == "DECISION_SNAPSHOT_COMPONENT_INCOMPLETE");

    payloads = Payloads();
    payloads.quote =
        "{\"source\":\"SIMULATOR\",\"authoritative\":true,"
        "\"instrument\":\"EUR.USD\",\"observed_at_ms\":1005,"
        "\"stale\":true,\"bid\":1.1,\"ask\":1.1002}";
    assert(!Build(payloads, snapshot, output, code));
    assert(code == "DECISION_SNAPSHOT_QUOTE_INVALID");
}

void TestRejectsDuplicatePosition()
{
    AuthoritativeDecisionSnapshotPayloads payloads = Payloads();
    payloads.positions =
        "{\"source\":\"SIMULATOR\",\"authoritative\":true,"
        "\"positions\":[{\"instrument\":\"EUR.USD\",\"quantity\":1},"
        "{\"instrument\":\"EUR.USD\",\"quantity\":2}]}";
    TargetPositionDecisionSnapshot snapshot;
    std::string output;
    std::string code;
    assert(!Build(payloads, snapshot, output, code));
    assert(code == "DECISION_SNAPSHOT_POSITION_INVALID");
}
}

int main()
{
    TestBuildsCompoundSnapshot();
    TestMissingInstrumentMeansFlat();
    TestRejectsIdentityAndEventDrift();
    TestRejectsIncompleteAndStaleComponents();
    TestRejectsDuplicatePosition();
    return 0;
}
