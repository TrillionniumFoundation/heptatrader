#include "../HeptaTrade/cli/heptactl_command.h"
#include "../HeptaTrade/cli/heptactl_exit_codes.h"
#include "../HeptaTrade/tool_host/typed_tool_protocol.h"

#include <cassert>
#include <cstdlib>
#include <iostream>
#include <string>
#include <vector>

namespace {

bool Parse(const std::vector<std::string>& arguments, HeptaCtlCommand& command, std::string& reason)
{
    std::vector<std::string> storage;
    storage.push_back("heptactl");
    storage.insert(storage.end(), arguments.begin(), arguments.end());
    std::vector<char*> argv;
    for (std::size_t i = 0; i < storage.size(); ++i) argv.push_back(&storage[i][0]);
    return HeptaCtlCommandParser::Parse(static_cast<int>(argv.size()), &argv[0], command, reason);
}

void TestDiscoveryCommands()
{
    setenv("HEPTA_TOOL_SESSION_TOKEN", "test-token", 1);
    HeptaCtlCommand command;
    std::string reason;
    const std::string schemaHash = "sha256:" + std::string(64, 'a');
    assert(Parse({"--socket", "/tmp/hepta.sock", "--call-id", "list-001",
                  "--protocol-min", "1", "--protocol-max", "1", "--schema-hash", schemaHash,
                  "tools", "list"}, command, reason));
    assert(command.request.call.name == "system.tools.list");
    assert(command.request.toolCallId == "list-001");
    assert(command.request.expectedSchemaHash == schemaHash);

    assert(Parse({"--socket", "/tmp/hepta.sock", "tools", "describe", "market.get_quote"}, command, reason));
    assert(command.request.call.name == "system.tools.describe");
    assert(command.request.call.targetToolName == "market.get_quote");

    std::string body;
    command.request.sessionToken = command.sessionToken;
    assert(TypedToolProtocol::EncodeRequest(command.request, body, reason));
    TradingToolHostRequest decoded;
    assert(TypedToolProtocol::DecodeRequest(body, decoded, reason));
    assert(decoded.call.targetToolName == "market.get_quote");
    assert(decoded.protocolMinVersion == 1 && decoded.protocolMaxVersion == 1);
}

void TestStableResultExitCodes()
{
    TradingToolResult result;
    result.toolName = "market.get_quote";
    result.status = TradingToolCallStatus::PermissionDenied;
    result.reasonCode = "CAPABILITY_REQUIRED";
    const std::string json = TypedToolProtocol::EncodeResultJson(result);
    TypedToolResultEnvelope envelope;
    std::string reason;
    assert(TypedToolProtocol::DecodeResultEnvelope(json, envelope, reason));
    assert(envelope.reasonCode == "CAPABILITY_REQUIRED");
    assert(HeptaCtlExitCodes::FromResult(envelope) == HeptaCtlPermissionDenied);

    result.status = TradingToolCallStatus::Rejected;
    result.reasonCode = "INVALID_TYPED_REQUEST";
    result.detail = "UNSUPPORTED_PROTOCOL_VERSION";
    assert(TypedToolProtocol::DecodeResultEnvelope(
        TypedToolProtocol::EncodeResultJson(result), envelope, reason));
    assert(HeptaCtlExitCodes::FromResult(envelope) == HeptaCtlTransportOrProtocol);

    result.status = TradingToolCallStatus::Uncertain;
    result.reasonCode = "VENUE_ACK_UNCERTAIN";
    result.detail.clear();
    assert(TypedToolProtocol::DecodeResultEnvelope(
        TypedToolProtocol::EncodeResultJson(result), envelope, reason));
    assert(HeptaCtlExitCodes::FromResult(envelope) == HeptaCtlUncertain);
    assert(!TypedToolProtocol::DecodeResultEnvelope("{}", envelope, reason));
    assert(reason == "INVALID_RESULT_ENVELOPE");
}

void AssertInvalidResultEnvelope(const std::string& json)
{
    TypedToolResultEnvelope envelope;
    std::string reason;
    assert(!TypedToolProtocol::DecodeResultEnvelope(json, envelope, reason));
    assert(reason == "INVALID_RESULT_ENVELOPE");
}

void TestStrictResultEnvelope()
{
    TypedToolResultEnvelope envelope;
    std::string reason;
    const std::string reordered =
        " { \"payload\" : {\"nested\":[true,false,null,{\"value\":\"ok\"}]},"
        "\"order_id\":7,\"detail\":\"safe\\ntext\",\"reason_code\":\"\","
        "\"tool\":\"market.get_quote\",\"status\":\"ok\" } ";
    assert(TypedToolProtocol::DecodeResultEnvelope(reordered, envelope, reason));
    assert(envelope.toolName == "market.get_quote");
    assert(envelope.orderId == 7);
    assert(envelope.payloadJson ==
        "{\"nested\":[true,false,null,{\"value\":\"ok\"}]}");

    AssertInvalidResultEnvelope(
        "{\"status\":\"ok\",\"tool\":\"market.get_quote\","
        "\"reason_code\":\"\",\"detail\":\"\",\"order_id\":-1,\"payload\":null}x");
    AssertInvalidResultEnvelope(
        "{\"status\":\"ok\",\"status\":\"error\",\"tool\":\"market.get_quote\","
        "\"reason_code\":\"\",\"detail\":\"\",\"order_id\":-1,\"payload\":null}");
    AssertInvalidResultEnvelope(
        "{\"wrapper\":{\"status\":\"ok\",\"tool\":\"market.get_quote\","
        "\"reason_code\":\"\",\"detail\":\"\"},\"order_id\":-1,\"payload\":null}");
    AssertInvalidResultEnvelope(
        "{\"status\":true,\"tool\":\"market.get_quote\",\"reason_code\":\"\","
        "\"detail\":\"\",\"order_id\":-1,\"payload\":null}");
    AssertInvalidResultEnvelope(
        "{\"status\":\"ok\",\"tool\":\"market.get_quote\",\"reason_code\":\"\","
        "\"detail\":\"\",\"order_id\":\"7\",\"payload\":null}");
    AssertInvalidResultEnvelope(
        "{\"status\":\"ok\",\"tool\":\"market.get_quote\",\"reason_code\":\"\","
        "\"detail\":\"\",\"order_id\":-1,\"payload\":[]}");
    AssertInvalidResultEnvelope(
        "{\"status\":\"ok\",\"tool\":\"market.get_quote\",\"reason_code\":\"\","
        "\"detail\":\"\",\"order_id\":-1,\"payload\":{\"value\":1,\"value\":2}}");
    AssertInvalidResultEnvelope(
        "{\"status\":\"ok\",\"tool\":\"market.get_quote\",\"reason_code\":\"\","
        "\"detail\":\"\",\"order_id\":-1}");
    AssertInvalidResultEnvelope(
        "{\"status\":\"ok\",\"tool\":\"market.get_quote\",\"reason_code\":\"\","
        "\"detail\":\"\\uD800\",\"order_id\":-1,\"payload\":null}");
    AssertInvalidResultEnvelope(
        "{\"status\":\"ok\",\"tool\":\"market.get_quote\",\"reason_code\":\"\","
        "\"detail\":\"\",\"order_id\":-1,\"payload\":null,\"extra\":false}");

    assert(HeptaCtlExitCodes::FromClientFailure(
        "INVALID_RESULT_ENVELOPE") == HeptaCtlInvalidResponse);
    assert(HeptaCtlExitCodes::FromClientFailure(
        "UNKNOWN_RESULT_STATUS") == HeptaCtlInvalidResponse);
    assert(HeptaCtlExitCodes::FromClientFailure(
        "RESULT_TOOL_MISMATCH") == HeptaCtlInvalidResponse);
    assert(HeptaCtlExitCodes::FromClientFailure(
        "SOCKET_CONNECT_FAILED") == HeptaCtlTransportOrProtocol);
}

void TestCallWaitCancelMappings()
{
    HeptaCtlCommand command;
    std::string reason;
    assert(Parse({"--socket", "/tmp/hepta.sock", "call", "market.get_quote", "instrument=EUR.USD"}, command, reason));
    assert(command.request.call.name == "market.get_quote");
    assert(command.request.call.instrument == "EUR.USD");

    assert(Parse({"--socket", "/tmp/hepta.sock", "call",
                  "execution.get_command_status",
                  "command_id=place-command-001"}, command, reason));
    assert(command.request.call.name == "execution.get_command_status");
    assert(command.request.call.targetCommandId == "place-command-001");
    command.request.sessionToken = command.sessionToken;
    std::string statusBody;
    assert(TypedToolProtocol::EncodeRequest(
        command.request, statusBody, reason));
    TradingToolHostRequest statusDecoded;
    assert(TypedToolProtocol::DecodeRequest(
        statusBody, statusDecoded, reason));
    assert(statusDecoded.call.targetCommandId == "place-command-001");

    assert(Parse({"--socket", "/tmp/hepta.sock", "wait", "after_sequence=7", "timeout_ms=250"}, command, reason));
    assert(command.request.call.name == "events.wait");
    assert(command.request.call.afterEventSequence == 7);
    assert(command.request.call.waitTimeoutMs == 250);

    assert(Parse({"--socket", "/tmp/hepta.sock", "cancel", "call-to-cancel"}, command, reason));
    assert(command.request.call.name == "system.cancel_request");
    assert(command.request.cancelToolCallId == "call-to-cancel");

    assert(Parse({"--socket", "/tmp/hepta.sock", "watch", "snapshot", "EUR.USD"},
                 command, reason));
    assert(command.watchSnapshot);
    assert(command.watchInstrument == "EUR.USD");
    TradingToolHostRequest composite;
    composite.sessionToken = command.sessionToken;
    composite.toolCallId = "watch-snapshot-001";
    composite.call.name = "watch.get_snapshot";
    composite.call.instrument = command.watchInstrument;
    std::string body;
    assert(TypedToolProtocol::EncodeRequest(composite, body, reason));
    TradingToolHostRequest decoded;
    assert(TypedToolProtocol::DecodeRequest(body, decoded, reason));
    assert(decoded.call.name == "watch.get_snapshot");
    assert(decoded.call.instrument == "EUR.USD");
    assert(!Parse({"--socket", "/tmp/hepta.sock", "watch", "snapshot", "GBP.USD"},
                  command, reason));
    assert(reason == "WATCH_INSTRUMENT_FORBIDDEN");
    assert(!Parse({"--socket", "/tmp/hepta.sock", "watch", "snapshot", "EUR.USD", "extra"},
                  command, reason));
    assert(reason == "UNEXPECTED_ARGUMENT");
}

}

int main()
{
    TestDiscoveryCommands();
    TestCallWaitCancelMappings();
    TestStableResultExitCodes();
    TestStrictResultEnvelope();
    std::cout << "heptactl_tests: PASS" << std::endl;
    return 0;
}
