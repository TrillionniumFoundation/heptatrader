#include "intent/authoritative_decision_snapshot.h"
#include "intent/bounded_json.h"

#include <cassert>
#include <cstdint>
#include <limits>
#include <locale>
#include <string>

namespace
{
std::string Health(const char* epoch, unsigned long long fence,
                   unsigned long long eventWatermark,
                   unsigned long long stateGeneration = 0)
{
    std::string result =
        std::string("{\"gateway_ready\":true,\"remote_execution_ready\":true,") +
        "\"execution_service_epoch\":\"" + epoch +
        "\",\"execution_service_fencing_generation\":" +
        std::to_string(fence) + ",\"event_watermark\":" +
        std::to_string(eventWatermark);
    if (stateGeneration != 0)
        result += ",\"state_generation\":" +
            std::to_string(stateGeneration);
    return result + "}";
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

void ExpectInvalidPositions(const std::string& positionsJson)
{
    AuthoritativeDecisionSnapshotPayloads payloads = Payloads();
    payloads.positions = positionsJson;
    TargetPositionDecisionSnapshot snapshot;
    std::string output;
    std::string code;
    assert(!Build(payloads, snapshot, output, code));
    assert(code == "DECISION_SNAPSHOT_POSITION_INVALID");
    assert(output.empty());
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
    assert(output.find("\"owner_scope\":{\"agent_id\":\"agent-a\"") !=
           std::string::npos);
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

void TestRejectsGatewayNotReadyHealth()
{
    AuthoritativeDecisionSnapshotPayloads payloads = Payloads();
    payloads.healthBefore =
        "{\"gateway_ready\":false,\"remote_execution_ready\":true,"
        "\"execution_service_epoch\":\"epoch-a\","
        "\"execution_service_fencing_generation\":4,"
        "\"event_watermark\":12}";
    TargetPositionDecisionSnapshot snapshot;
    std::string output;
    std::string code;
    assert(!Build(payloads, snapshot, output, code));
    assert(code == "DECISION_SNAPSHOT_HEALTH_INVALID");

    payloads = Payloads();
    payloads.healthAfter =
        "{\"remote_execution_ready\":true,"
        "\"execution_service_epoch\":\"epoch-a\","
        "\"execution_service_fencing_generation\":4,"
        "\"event_watermark\":12}";
    // Omitting the required field must fail closed just like an explicit
    // gateway_ready=false response.
    assert(!Build(payloads, snapshot, output, code));
    assert(code == "DECISION_SNAPSHOT_HEALTH_INVALID");
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

    // A quote captured before the collection window cannot prove the
    // authority state observed by this snapshot, even when it is otherwise
    // fresh and non-stale.  Enforce the documented monotonic timestamp
    // window at the snapshot boundary.
    payloads = Payloads();
    payloads.quote =
        "{\"source\":\"SIMULATOR\",\"authoritative\":true,"
        "\"instrument\":\"EUR.USD\",\"observed_at_ms\":999,"
        "\"stale\":false,\"bid\":1.1,\"ask\":1.1002}";
    assert(!Build(payloads, snapshot, output, code));
    assert(code == "DECISION_SNAPSHOT_QUOTE_INVALID");

    // A quote timestamp from after collection completion cannot be attested
    // by this snapshot and must not be accepted as a future observation.
    payloads = Payloads();
    payloads.quote =
        "{\"source\":\"SIMULATOR\",\"authoritative\":true,"
        "\"instrument\":\"EUR.USD\",\"observed_at_ms\":1011,"
        "\"stale\":false,\"bid\":1.1,\"ask\":1.1002}";
    assert(!Build(payloads, snapshot, output, code));
    assert(code == "DECISION_SNAPSHOT_QUOTE_INVALID");
}

void TestRejectsDuplicatePosition()
{
    ExpectInvalidPositions(
        "{\"source\":\"SIMULATOR\",\"authoritative\":true,"
        "\"positions\":[{\"instrument\":\"EUR.USD\",\"quantity\":1},"
        "{\"instrument\":\"EUR.USD\",\"quantity\":2}]}");
}

void TestRejectsMalformedPositionCollection()
{
    // Only a present top-level array is allowed to assert an authoritative
    // zero. A missing or wrong-shaped collection is unknown state, not flat.
    ExpectInvalidPositions(
        "{\"source\":\"SIMULATOR\",\"authoritative\":true}");
    ExpectInvalidPositions(
        "{\"source\":\"SIMULATOR\",\"authoritative\":true,"
        "\"positions\":{}}");
    ExpectInvalidPositions(
        "{\"source\":\"SIMULATOR\",\"authoritative\":true,"
        "\"positions\":[null]}");

    // Every record is authoritative input. An irrelevant malformed entry may
    // not be ignored while a valid target record is promoted into a permit.
    ExpectInvalidPositions(
        "{\"source\":\"SIMULATOR\",\"authoritative\":true,"
        "\"positions\":[{\"instrument\":\"USD.JPY\"},"
        "{\"instrument\":\"EUR.USD\",\"quantity\":2.5}]}");
    ExpectInvalidPositions(
        "{\"source\":\"SIMULATOR\",\"authoritative\":true,"
        "\"positions\":[{\"instrument\":\"EUR.USD\","
        "\"quantity\":\"2.5\"}]}");
    ExpectInvalidPositions(
        "{\"source\":\"SIMULATOR\",\"authoritative\":true,"
        "\"positions\":[{\"instrument\":\"EUR.USD\",\"quantity\":-0.0}]}");

    // The authoritative list is aggregate-by-instrument, so duplicate
    // unrelated records are also inconsistent with the collection contract.
    ExpectInvalidPositions(
        "{\"source\":\"SIMULATOR\",\"authoritative\":true,"
        "\"positions\":[{\"instrument\":\"USD.JPY\",\"quantity\":1},"
        "{\"instrument\":\"USD.JPY\",\"quantity\":2}]}");
}

void TestRejectsOwnerMetadataMismatch()
{
    AuthoritativeDecisionSnapshotPayloads payloads = Payloads();
    payloads.orders =
        "{\"source\":\"SIMULATOR\",\"authoritative\":true,"
        "\"owner_scope\":{\"agent_id\":\"other-agent\","
        "\"session_id\":\"session-a\",\"account\":\"SIM\","
        "\"execution_domain\":\"SIM:default\"},\"active_order_ids\":[]}";
    TargetPositionDecisionSnapshot snapshot;
    std::string output;
    std::string code;
    assert(!Build(payloads, snapshot, output, code));
    assert(code == "DECISION_SNAPSHOT_OWNER_MISMATCH");
}

void TestRejectsStateGenerationDrift()
{
    AuthoritativeDecisionSnapshotPayloads payloads = Payloads();
    payloads.healthBefore = Health("epoch-a", 4, 12, 21);
    payloads.healthAfter = Health("epoch-a", 4, 12, 22);
    TargetPositionDecisionSnapshot snapshot;
    std::string output;
    std::string code;
    assert(!Build(payloads, snapshot, output, code));
    assert(code == "DECISION_SNAPSHOT_STATE_GENERATION_CHANGED");
}

void TestUnsignedKeepsLargeIntegerPrecision()
{
    BoundedJsonValue value;
    std::string reason;
    assert(ParseBoundedJson(
        "{\"n\":18446744073709551615,\"safe\":9007199254740993}",
        value, reason));
    std::uint64_t parsed = 0;
    const BoundedJsonValue* field = value.Find("n");
    assert(field != nullptr && field->Unsigned(parsed));
    assert(parsed == std::numeric_limits<std::uint64_t>::max());
    field = value.Find("safe");
    assert(field != nullptr && field->Unsigned(parsed));
    assert(parsed == 9007199254740993ULL);

    assert(ParseBoundedJson(
        "{\"n\":18446744073709551616}", value, reason));
    field = value.Find("n");
    assert(field != nullptr && !field->Unsigned(parsed));
    assert(ParseBoundedJson("{\"n\":1.5}", value, reason));
    field = value.Find("n");
    assert(field != nullptr && !field->Unsigned(parsed));
    assert(ParseBoundedJson("{\"n\":1e5}", value, reason));
    field = value.Find("n");
    assert(field != nullptr && field->Unsigned(parsed) && parsed == 100000ULL);
    assert(ParseBoundedJson("{\"n\":1e19}", value, reason));
    field = value.Find("n");
    assert(field != nullptr && field->Unsigned(parsed) &&
           parsed == 10000000000000000000ULL);
    assert(ParseBoundedJson("{\"n\":1e20}", value, reason));
    field = value.Find("n");
    assert(field != nullptr && !field->Unsigned(parsed));
    assert(ParseBoundedJson("{\"n\":-0}", value, reason));
    field = value.Find("n");
    assert(field != nullptr && !field->Unsigned(parsed));
}

void TestBoundedJsonRejectsInvalidUtf8AndPreservesUnicodeEscapes()
{
    BoundedJsonValue value;
    std::string reason;
    const std::string valid = "{\"text\":\"\xE2\x82\xAC\xF0\x9F\x98\x80\"}";
    assert(ParseBoundedJson(valid, value, reason));
    std::string text;
    const BoundedJsonValue* field = value.Find("text");
    assert(field != nullptr && field->String(text));
    assert(text == "\xE2\x82\xAC\xF0\x9F\x98\x80");

    assert(ParseBoundedJson("{\"text\":\"\\u20ac\\ud83d\\ude00\"}",
                            value, reason));
    field = value.Find("text");
    assert(field != nullptr && field->String(text));
    assert(text == "\xE2\x82\xAC\xF0\x9F\x98\x80");

    const std::string invalid[] = {
        "{\"text\":\"\xC0\x80\"}", // overlong NUL
        "{\"text\":\"\xE0\x80\x80\"}", // overlong 3-byte form
        "{\"text\":\"\xED\xA0\x80\"}", // UTF-8 encoded surrogate
        "{\"text\":\"\xF4\x90\x80\x80\"}", // above U+10FFFF
        "{\"text\":\"\xE2\x82\"}", // truncated sequence
    };
    for (std::size_t i = 0; i < sizeof(invalid) / sizeof(invalid[0]); ++i)
    {
        assert(!ParseBoundedJson(invalid[i], value, reason));
        assert(reason == "JSON_UTF8_INVALID");
    }
    assert(!ParseBoundedJson("{\"text\":\"\\ud800\"}", value, reason));
    assert(reason == "JSON_UNICODE_SURROGATE_INVALID");
    assert(!ParseBoundedJson("{\"text\":\"\\udc00\"}", value, reason));
    assert(reason == "JSON_UNICODE_SURROGATE_INVALID");
    // Control characters must be rejected after JSON decoding as well as in
    // raw input; otherwise escaped DEL/C1 bytes can reach snapshot payloads.
    assert(!ParseBoundedJson("{\"text\":\"\\u007f\"}", value, reason));
    assert(reason == "JSON_CONTROL_CHARACTER");
    assert(!ParseBoundedJson("{\"text\":\"\\u0085\"}", value, reason));
    assert(reason == "JSON_CONTROL_CHARACTER");
    const std::string rawC1 =
        std::string("{\"text\":\"") + "\xC2\x85" + "\"}";
    assert(!ParseBoundedJson(rawC1, value, reason));
    assert(reason == "JSON_CONTROL_CHARACTER");
}

class CommaDecimalPoint : public std::numpunct<char>
{
protected:
    char do_decimal_point() const override { return ','; }
};

void TestBoundedJsonNumbersIgnoreProcessLocale()
{
    const std::locale previous = std::locale::global(
        std::locale(std::locale::classic(), new CommaDecimalPoint()));
    BoundedJsonValue value;
    std::string reason;
    assert(ParseBoundedJson("{\"n\":1.25}", value, reason));
    double parsed = 0.0;
    const BoundedJsonValue* field = value.Find("n");
    assert(field != nullptr && field->Number(parsed));
    assert(parsed == 1.25);
    std::locale::global(previous);
}

void TestBoundedJsonEnforcesRawStringLimit()
{
    const std::string oversized(1024u * 1024u + 1u, 'a');
    const std::string json = std::string("{\"text\":\"") + oversized + "\"}";
    BoundedJsonValue value;
    std::string reason;
    assert(!ParseBoundedJson(json, value, reason, json.size() + 1u));
    assert(reason == "JSON_STRING_LIMIT");
}

void TestSnapshotIdentityEncodingIsBoundedAndLossless()
{
    TargetPositionDecisionSnapshot snapshot;
    std::string output;
    std::string code;
    AuthoritativeDecisionSnapshotPayloads payloads = Payloads();
    assert(Build(payloads, snapshot, output, code));

    // Quotes and backslashes are escaped without changing the bound identity.
    std::string detail;
    assert(AuthoritativeDecisionSnapshotCodec::Build(
        "agent\\\"x", "session\\\\x", "SIM", "SIM:default", "EUR.USD",
        1000, 1010, 7, payloads, snapshot, output, code, detail));
    BoundedJsonValue compound;
    assert(ParseBoundedJson(output, compound, detail));
    const BoundedJsonValue* scope = compound.Find("owner_scope");
    std::string actual;
    assert(scope != nullptr && scope->Find("agent_id") != nullptr &&
           scope->Find("agent_id")->String(actual) && actual == "agent\\\"x");

    // Caller-supplied identity bytes are validated before interpolation into
    // JSON; controls and malformed UTF-8 must fail closed.
    payloads = Payloads();
    std::string invalidControl("agent");
    invalidControl.push_back('\x01');
    assert(!AuthoritativeDecisionSnapshotCodec::Build(
        invalidControl, "session-a", "SIM", "SIM:default", "EUR.USD",
        1000, 1010, 7, payloads, snapshot, output, code, detail));
    assert(code == "DECISION_SNAPSHOT_REQUEST_INVALID");
    std::string invalidUtf8("agent\xC0", 6);
    assert(!AuthoritativeDecisionSnapshotCodec::Build(
        invalidUtf8, "session-a", "SIM", "SIM:default", "EUR.USD",
        1000, 1010, 7, payloads, snapshot, output, code, detail));
    assert(code == "DECISION_SNAPSHOT_REQUEST_INVALID");
}
}

int main()
{
    TestBuildsCompoundSnapshot();
    TestMissingInstrumentMeansFlat();
    TestRejectsIdentityAndEventDrift();
    TestRejectsGatewayNotReadyHealth();
    TestRejectsIncompleteAndStaleComponents();
    TestRejectsDuplicatePosition();
    TestRejectsMalformedPositionCollection();
    TestRejectsOwnerMetadataMismatch();
    TestRejectsStateGenerationDrift();
    TestUnsignedKeepsLargeIntegerPrecision();
    TestBoundedJsonRejectsInvalidUtf8AndPreservesUnicodeEscapes();
    TestBoundedJsonNumbersIgnoreProcessLocale();
    TestBoundedJsonEnforcesRawStringLimit();
    TestSnapshotIdentityEncodingIsBoundedAndLossless();
    return 0;
}
