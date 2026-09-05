#include "../HeptaTrade/execution/execution_service_protocol.h"

#include <cassert>
#include <cstdint>
#include <string>

namespace
{
std::uint64_t Next(std::uint64_t& state)
{
    state ^= state << 13;
    state ^= state >> 7;
    state ^= state << 17;
    return state;
}

unsigned int ReadU16(const std::string& body, std::size_t offset)
{
    assert(offset + 2 <= body.size());
    return (static_cast<unsigned char>(body[offset]) << 8) |
        static_cast<unsigned char>(body[offset + 1]);
}

std::size_t ReadU32(const std::string& body, std::size_t offset)
{
    assert(offset + 4 <= body.size());
    return (static_cast<std::size_t>(static_cast<unsigned char>(body[offset])) << 24) |
        (static_cast<std::size_t>(static_cast<unsigned char>(body[offset + 1])) << 16) |
        (static_cast<std::size_t>(static_cast<unsigned char>(body[offset + 2])) << 8) |
        static_cast<unsigned char>(body[offset + 3]);
}

void AppendU16(std::string& body, unsigned int value)
{
    body.push_back(static_cast<char>((value >> 8) & 0xff));
    body.push_back(static_cast<char>(value & 0xff));
}

void AppendU32(std::string& body, std::size_t value)
{
    body.push_back(static_cast<char>((value >> 24) & 0xff));
    body.push_back(static_cast<char>((value >> 16) & 0xff));
    body.push_back(static_cast<char>((value >> 8) & 0xff));
    body.push_back(static_cast<char>(value & 0xff));
}

std::string ReplaceField(const std::string& body, unsigned int tag,
                         const std::string& replacement)
{
    assert(body.size() >= 8);
    std::string result = body.substr(0, 8);
    std::size_t offset = 8;
    bool replaced = false;
    while (offset < body.size())
    {
        assert(offset + 6 <= body.size());
        const unsigned int fieldTag = ReadU16(body, offset);
        const std::size_t length = ReadU32(body, offset + 2);
        const std::size_t valueOffset = offset + 6;
        assert(valueOffset + length <= body.size());
        const std::string value = fieldTag == tag ? replacement :
            body.substr(valueOffset, length);
        AppendU16(result, fieldTag);
        AppendU32(result, value.size());
        result.append(value);
        replaced = replaced || fieldTag == tag;
        offset = valueOffset + length;
    }
    assert(replaced);
    return result;
}

void TestStrictNumericLexicalBoundaries()
{
    // Field tags are part of the versioned HEX1 contract: expiry=12,
    // reference price=13, quantity=27, service fencing generation=35.
    ExecutionServiceRequest request;
    request.operation = ExecutionServiceOperation::PlaceIbOrder;
    request.expectedServiceEpoch = "service-epoch";
    request.expectedServiceFencingGeneration = 7;
    request.place.context.agentId = "agent";
    request.place.context.sessionId = "session";
    request.place.context.toolCallId = "call";
    request.place.context.strategy = "strategy";
    request.place.context.account = "SIM";
    request.place.context.venue = "SIMULATOR";
    request.place.context.executionDomain = "SIM:agent";
    request.place.instrument = "EUR.USD";
    request.place.expiresAtMs = 1700000000000LL;
    request.place.referencePrice = 1.25;
    request.place.contract.symbol = "EUR";
    request.place.contract.secType = "CASH";
    request.place.contract.exchange = "IDEALPRO";
    request.place.contract.currency = "USD";
    request.place.order.action = "BUY";
    request.place.order.orderType = "MKT";
    request.place.order.totalQuantity = 1.0;
    request.place.timeInForce = "DAY";

    std::string body;
    std::string reason;
    assert(ExecutionServiceProtocol::EncodeRequest(request, body, reason));
    ExecutionServiceRequest decoded;
    assert(ExecutionServiceProtocol::DecodeRequest(body, decoded, reason));

    const std::string malformed[] = {
        " 1700000000000", // leading whitespace
        "+1700000000000", // leading plus
        "01700000000000", // integer leading zero
    };
    for (const std::string& value : malformed)
        assert(!ExecutionServiceProtocol::DecodeRequest(
            ReplaceField(body, 12, value), decoded, reason));
    assert(!ExecutionServiceProtocol::DecodeRequest(
        ReplaceField(body, 35, "07"), decoded, reason));
    assert(!ExecutionServiceProtocol::DecodeRequest(
        ReplaceField(body, 35, "-0"), decoded, reason));
    assert(!ExecutionServiceProtocol::DecodeRequest(
        ReplaceField(body, 13, "NaN"), decoded, reason));
    assert(!ExecutionServiceProtocol::DecodeRequest(
        ReplaceField(body, 13, "0x1p0"), decoded, reason));
    assert(!ExecutionServiceProtocol::DecodeRequest(
        ReplaceField(body, 13, "1e999"), decoded, reason));

    // Decimal scientific notation remains a valid finite spelling.
    assert(ExecutionServiceProtocol::DecodeRequest(
        ReplaceField(body, 13, "1e+3"), decoded, reason));
    assert(decoded.place.referencePrice == 1000.0);

    // Expiry is an absolute exclusive deadline.  Validate it in the codec as
    // well as in the higher-level dispatch path so direct callers cannot
    // bypass the TTL gate with zero/negative values.
    request.place.expiresAtMs = 0;
    assert(!ExecutionServiceProtocol::EncodeRequest(request, body, reason));
    request.place.expiresAtMs = -1;
    assert(!ExecutionServiceProtocol::EncodeRequest(request, body, reason));
    request.place.expiresAtMs = 1700000000000LL;
    assert(ExecutionServiceProtocol::EncodeRequest(request, body, reason));
    assert(!ExecutionServiceProtocol::DecodeRequest(
        ReplaceField(body, 12, "0"), decoded, reason));
    assert(!ExecutionServiceProtocol::DecodeRequest(
        ReplaceField(body, 12, "-1"), decoded, reason));

    // Authority-controlled result detail must remain bounded printable UTF-8
    // at the binary IPC boundary; escaped controls are rejected after decode
    // by the same contract (the binary field itself carries decoded bytes).
    ExecutionCommandResult response;
    response.status = ExecutionCommandStatus::Rejected;
    response.commandId = "response-command";
    response.reasonCode = "EXECUTION_REJECTED";
    response.serviceEpoch = "service-epoch";
    response.serviceFencingGeneration = 1;
    response.detail = std::string("bad") +
        std::string(1, static_cast<char>(0x7f)) + "detail";
    assert(!ExecutionServiceProtocol::EncodeResponse(response, body, reason));
    response.detail = std::string(32769, 'x');
    assert(!ExecutionServiceProtocol::EncodeResponse(response, body, reason));
}
}

int main()
{
    TestStrictNumericLexicalBoundaries();
    std::uint64_t state = 0x6a09e667f3bcc909ULL;
    for (std::size_t sample = 0; sample < 20000; ++sample)
    {
        const std::size_t length = static_cast<std::size_t>(Next(state) % 1024);
        std::string body(length, '\0');
        for (std::size_t i = 0; i < length; ++i)
  body[i] = static_cast<char>(Next(state) & 0xff);
        std::string reason;
        ExecutionServiceRequest request;
        ExecutionServiceProtocol::DecodeRequest(body, request, reason);
        ExecutionCommandResult response;
        ExecutionServiceProtocol::DecodeResponse(body, response, reason);
        ExecutionControlResult control;
        ExecutionServiceProtocol::DecodeControlResponse(body, control, reason);
    }
    return 0;
}
