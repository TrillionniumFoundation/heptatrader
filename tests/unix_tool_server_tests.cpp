#include "../HeptaTrade/tool_host/typed_tool_protocol.h"
#include "../HeptaTrade/client/native_tool_client.h"
#include "../HeptaTrade/execution/execution_coordinator.h"
#include "../HeptaTrade/tool_host/unix_tool_client.h"
#include "../HeptaTrade/tool_host/unix_tool_server.h"

#include <cassert>
#include <arpa/inet.h>
#include <algorithm>
#include <atomic>
#include <chrono>
#include <cstdio>
#include <cstring>
#include <fcntl.h>
#include <fstream>
#include <iostream>
#include <mutex>
#include <string>
#include <thread>
#include <vector>
#include <sys/socket.h>
#include <sys/stat.h>
#include <sys/un.h>
#include <unistd.h>

namespace {

constexpr int kLocalSocketTimeoutMs = 5000;

std::string WithoutProtocolRange(const std::string& body)
{
    assert(body.size() >= 4);
    std::string result = body.substr(0, 4);
    std::size_t offset = 4;
    while (offset < body.size())
    {
        assert(offset + 6 <= body.size());
        const unsigned int id =
            (static_cast<unsigned char>(body[offset]) << 8) |
            static_cast<unsigned char>(body[offset + 1]);
        const std::uint32_t length =
            (static_cast<std::uint32_t>(static_cast<unsigned char>(body[offset + 2])) << 24) |
            (static_cast<std::uint32_t>(static_cast<unsigned char>(body[offset + 3])) << 16) |
            (static_cast<std::uint32_t>(static_cast<unsigned char>(body[offset + 4])) << 8) |
            static_cast<std::uint32_t>(static_cast<unsigned char>(body[offset + 5]));
        assert(offset + 6 + length <= body.size());
        if (id != 22 && id != 23) result.append(body, offset, 6 + length);
        offset += 6 + length;
    }
    return result;
}

std::string TempPath(const char* pattern)
{
    std::string value(pattern);
    std::vector<char> buffer(value.begin(), value.end());
    buffer.push_back('\0');
    const int fd = mkstemp(buffer.data());
    assert(fd >= 0);
    close(fd);
    unlink(buffer.data());
    return std::string(buffer.data());
}

std::string AuditPayloads(const std::string& path)
{
    std::ifstream input(path.c_str(), std::ios::binary);
    std::string decoded;
    std::string line;
    while (std::getline(input, line))
    {
        std::vector<std::string> fields;
        std::size_t begin = 0;
        while (begin <= line.size())
        {
            const std::size_t end = line.find('\t', begin);
            fields.push_back(line.substr(begin,
                end == std::string::npos ? std::string::npos : end - begin));
            if (end == std::string::npos) break;
            begin = end + 1;
        }
        if (fields.size() != 7 || fields[0] != "HJA2") continue;
        assert(fields[5].size() % 2 == 0);
        for (std::size_t i = 0; i < fields[5].size(); i += 2)
        {
            const int high = fields[5][i] <= '9' ?
                fields[5][i] - '0' : fields[5][i] - 'a' + 10;
            const int low = fields[5][i + 1] <= '9' ?
                fields[5][i + 1] - '0' : fields[5][i + 1] - 'a' + 10;
            decoded.push_back(static_cast<char>((high << 4) | low));
        }
        decoded.push_back('\n');
    }
    return decoded;
}

void BindSchemaHash(const TradingToolRegistry& registry,
                    TradingToolHostRequest& request)
{
    TradingToolDescriptor descriptor;
    assert(registry.GetDescriptor(request.call.name, descriptor));
    request.expectedSchemaHash =
        TradingToolRegistry::DescriptorSchemaHash(descriptor);
}

std::size_t CountOccurrences(const std::string& value,
                             const std::string& needle)
{
    std::size_t count = 0;
    std::size_t offset = 0;
    while ((offset = value.find(needle, offset)) != std::string::npos)
    {
        ++count;
        offset += needle.size();
    }
    return count;
}

std::string CallTool(const std::string& socketPath,
                     const TradingToolHostRequest& request,
                     int timeoutMs = 2000)
{
    std::string response;
    std::string reason;
    assert(UnixToolClient::Call(
        socketPath, request, response, reason, timeoutMs, 65536));
    return response;
}

void TestSocketRoundTripAndStrictProtocol()
{
    const std::string journalPath = TempPath("/tmp/hepta-tool-socket-journal-XXXXXX");
    const std::string decisionAuditPath =
        TempPath("/tmp/hepta-tool-decision-audit-XXXXXX");
    const std::string socketPath = TempPath("/tmp/hepta-tool-socket-XXXXXX");
    OmsJournal journal;
    assert(journal.Init(journalPath));
    ExecutionCoordinatorCallbacks executionCallbacks;
    ExecutionCoordinator execution(journal, executionCallbacks);
    TradingToolReadCallbacks reads;
    reads.marketGetQuote = [](const TradingToolSession& session, const TradingToolCall& call,
                              std::string& payload, std::string&) {
        if (session.executionContext.toolCallId.find("pressure-") == 0) usleep(150000);
        payload = std::string("{\"agent\":\"") + session.executionContext.agentId +
                  "\",\"instrument\":\"" + call.instrument + "\",\"bid\":1.1,\"ask\":1.2}";
        return true;
    };
    reads.systemGetHealth = [](const TradingToolSession&, const TradingToolCall&,
                               std::string& payload, std::string&) {
        payload = "{\"authoritative\":false,\"catalog\":{\"revision\":7},\"quotes\":{\"contracts\":[]}}";
        return true;
    };
    TradingToolRegistry registry(execution, reads);
    TradingToolHost host(registry);
    SessionSupervisorAuditJournal decisionAudit;
    std::string reason;
    assert(decisionAudit.Init(decisionAuditPath, reason));
    TradingToolHostSessionBinding binding;
    binding.token = "socket-session-token-000001";
    binding.peerUid = static_cast<std::uint32_t>(getuid());
    binding.session.executionContext.agentId = "socket-agent";
    binding.session.executionContext.sessionId = "socket-session";
    binding.session.executionContext.account = "DU123";
    binding.session.environment = "WATCH";
    binding.executionDomain = "IB-PAPER";
    binding.session.capabilities.insert("market.read");
    binding.session.capabilities.insert("system.read");
    binding.expiresAtMs = static_cast<std::uint64_t>(OmsJournal::NowEpochMs()) + 60000;
    assert(host.RegisterSession(binding, reason));

    UnixToolServer server(host);
    server.SetDecisionAuditJournal(&decisionAudit);
	std::atomic<int> ownerHealthEvents(0);
	server.SetBackpressureObserver(
		[&](const TradingToolHostSessionBinding& observed, const std::string& reasonCode) {
			assert(observed.session.executionContext.agentId == "socket-agent");
			assert(reasonCode == "OWNER_QUEUE_BACKPRESSURE");
			++ownerHealthEvents;
		});
    assert(server.Start(socketPath, reason, 4096, 1000, 4, 32, 1, 2, 2));

    const int client = socket(AF_UNIX, SOCK_STREAM | SOCK_CLOEXEC, 0);
    assert(client >= 0);
    sockaddr_un address;
    std::memset(&address, 0, sizeof(address));
    address.sun_family = AF_UNIX;
    std::strncpy(address.sun_path, socketPath.c_str(), sizeof(address.sun_path) - 1);
    assert(connect(client, reinterpret_cast<sockaddr*>(&address), sizeof(address)) == 0);

	auto invalidFrame = [&](std::uint32_t length, const std::string& payload,
		bool closeWrite, const std::string& expected) {
		const int faultClient = socket(AF_UNIX, SOCK_STREAM | SOCK_CLOEXEC, 0);
		assert(faultClient >= 0);
		assert(connect(faultClient, reinterpret_cast<sockaddr*>(&address), sizeof(address)) == 0);
		const std::uint32_t networkLength = htonl(length);
		assert(write(faultClient, &networkLength, sizeof(networkLength)) ==
			static_cast<ssize_t>(sizeof(networkLength)));
		if (!payload.empty())
			assert(write(faultClient, payload.data(), payload.size()) ==
				static_cast<ssize_t>(payload.size()));
		if (closeWrite) shutdown(faultClient, SHUT_WR);
		std::string faultResponse;
		std::string faultReason;
		assert(TypedToolProtocol::ReadFrame(faultClient, 4096, 2500,
			faultResponse, faultReason));
		assert(faultResponse.find(expected) != std::string::npos);
		close(faultClient);
	};
	invalidFrame(4097, std::string(), false, "FRAME_LENGTH_REJECTED");
	invalidFrame(8, "BAD", true, "FRAME_BODY_TIMEOUT");
	invalidFrame(4, "NOPE", false, "INVALID_TYPED_REQUEST");

    TradingToolHostRequest request;
    request.sessionToken = binding.token;
    request.toolCallId = "quote-call-1";
    request.call.name = "market.get_quote";
    request.call.instrument = "EUR.USD";
    BindSchemaHash(registry, request);
    std::string body;
    assert(TypedToolProtocol::EncodeRequest(request, body, reason));
    assert(TypedToolProtocol::WriteFrame(client, body, kLocalSocketTimeoutMs, reason));
    std::string response;
    assert(TypedToolProtocol::ReadFrame(
        client, 4096, kLocalSocketTimeoutMs, response, reason));
    assert(response.find("\"status\":\"ok\"") != std::string::npos);
    assert(response.find("\"agent\":\"socket-agent\"") != std::string::npos);
    close(client);
	{
		const int timeoutClient = socket(AF_UNIX, SOCK_STREAM | SOCK_CLOEXEC, 0);
		assert(timeoutClient >= 0);
		assert(connect(timeoutClient, reinterpret_cast<sockaddr*>(&address), sizeof(address)) == 0);
		const std::uint32_t timeoutLength = htonl(8);
		assert(write(timeoutClient, &timeoutLength, sizeof(timeoutLength)) ==
			static_cast<ssize_t>(sizeof(timeoutLength)));
		assert(write(timeoutClient, "BAD", 3) == 3);
		const std::chrono::steady_clock::time_point started = std::chrono::steady_clock::now();
		std::string timeoutResponse;
		std::string timeoutReason;
		assert(TypedToolProtocol::ReadFrame(timeoutClient, 4096, 2500,
			timeoutResponse, timeoutReason));
		const long elapsedMs = static_cast<long>(std::chrono::duration_cast<std::chrono::milliseconds>(
			std::chrono::steady_clock::now() - started).count());
		assert(elapsedMs >= 800 && elapsedMs < 2200);
		assert(timeoutResponse.find("FRAME_BODY_TIMEOUT") != std::string::npos);
		close(timeoutClient);
		for (int retry = 0; retry < 100 && server.GetHealth().pendingConnections != 0; ++retry)
			usleep(1000);
		assert(server.GetHealth().pendingConnections == 0);
	}

    const int healthClient = socket(AF_UNIX, SOCK_STREAM | SOCK_CLOEXEC, 0);
    assert(healthClient >= 0);
    assert(connect(healthClient, reinterpret_cast<sockaddr*>(&address), sizeof(address)) == 0);
    TradingToolHostRequest healthRequest;
    healthRequest.sessionToken = binding.token;
    healthRequest.toolCallId = "health-call-1";
    healthRequest.call.name = "system.get_health";
    assert(TypedToolProtocol::EncodeRequest(healthRequest, body, reason));
    assert(TypedToolProtocol::WriteFrame(
        healthClient, body, kLocalSocketTimeoutMs, reason));
    response.clear();
    assert(TypedToolProtocol::ReadFrame(
        healthClient, 4096, kLocalSocketTimeoutMs, response, reason));
    assert(response.find("SCHEMA_HASH_REQUIRED") != std::string::npos);
    close(healthClient);
    BindSchemaHash(registry, healthRequest);
    healthRequest.toolCallId = "health-call-2";
    response.clear();
    assert(UnixToolClient::Call(
        socketPath, healthRequest, response, reason, kLocalSocketTimeoutMs, 65536));
    assert(response.find("\"status\":\"ok\"") != std::string::npos);
    assert(response.find("\"catalog\":{\"revision\":7}") != std::string::npos);

    TradingToolHostRequest discoveryRequest;
    discoveryRequest.sessionToken = binding.token;
    discoveryRequest.toolCallId = "tools-list-1";
    discoveryRequest.call.name = "system.tools.list";
    response.clear();
    assert(UnixToolClient::Call(
        socketPath, discoveryRequest, response, reason, kLocalSocketTimeoutMs, 65536));
    assert(response.find("\"protocol\":\"hepta.agent-tools\"") != std::string::npos);
    assert(response.find("\"name\":\"market.get_quote\"") != std::string::npos);

    discoveryRequest.toolCallId = "tools-describe-1";
    discoveryRequest.call.name = "system.tools.describe";
    discoveryRequest.call.targetToolName = "market.get_quote";
    response.clear();
    assert(UnixToolClient::Call(
        socketPath, discoveryRequest, response, reason, kLocalSocketTimeoutMs, 65536));
    assert(response.find("\"required_capability\":\"market.read\"") != std::string::npos);

    TradingToolHostRequest discoveryMismatch = discoveryRequest;
    discoveryMismatch.toolCallId = "tools-describe-mismatch";
    discoveryMismatch.expectedSchemaHash =
        "sha256:" + std::string(64, '0');
    response.clear();
    assert(UnixToolClient::Call(
        socketPath, discoveryMismatch, response, reason, kLocalSocketTimeoutMs, 65536));
    assert(response.find("SCHEMA_HASH_MISMATCH") != std::string::npos);

    TradingToolHostRequest schemaMismatchRequest;
    schemaMismatchRequest.sessionToken = binding.token;
    schemaMismatchRequest.toolCallId = "schema-mismatch-1";
    schemaMismatchRequest.call.name = "market.get_quote";
    schemaMismatchRequest.call.instrument = "EUR.USD";
    schemaMismatchRequest.expectedSchemaHash = "sha256:" + std::string(64, '0');
    response.clear();
    assert(UnixToolClient::Call(
        socketPath, schemaMismatchRequest, response, reason, kLocalSocketTimeoutMs, 65536));
    assert(response.find("SCHEMA_HASH_MISMATCH") != std::string::npos);

    TradingToolHostRequest capabilityDenied;
    capabilityDenied.sessionToken = binding.token;
    capabilityDenied.toolCallId = "capability-denied-1";
    capabilityDenied.call.name = "account.get_summary";
    capabilityDenied.expectedSchemaHash =
        "sha256:" + std::string(64, '0');
    response.clear();
    assert(UnixToolClient::Call(socketPath, capabilityDenied,
        response, reason, kLocalSocketTimeoutMs, 65536));
    assert(response.find("CAPABILITY_REQUIRED") != std::string::npos);
    assert(response.find("SCHEMA_HASH_") == std::string::npos);

    TradingToolHostRequest tokenDenied;
    tokenDenied.sessionToken = "unknown-session-token-00001";
    tokenDenied.toolCallId = "token-denied-1";
    tokenDenied.call.name = "market.get_quote";
    tokenDenied.call.instrument = "EUR.USD";
    response.clear();
    assert(UnixToolClient::Call(socketPath, tokenDenied,
        response, reason, kLocalSocketTimeoutMs, 65536));
    assert(response.find("SESSION_NOT_FOUND") != std::string::npos);
    assert(response.find("SCHEMA_HASH_") == std::string::npos);
    TradingToolHostRequest unknownTool;
    unknownTool.sessionToken = binding.token;
    unknownTool.toolCallId = "unknown-tool-1";
    unknownTool.call.name = "vendor.unknown";
    response.clear();
    assert(UnixToolClient::Call(
        socketPath, unknownTool, response, reason, kLocalSocketTimeoutMs, 65536));
    assert(response.find("UNKNOWN_TOOL") != std::string::npos);
    assert(response.find("SCHEMA_HASH_") == std::string::npos);
    TradingToolDescriptor quoteDescriptor;
    assert(registry.GetDescriptor("market.get_quote", quoteDescriptor));
    schemaMismatchRequest.toolCallId = "schema-match-1";
    schemaMismatchRequest.expectedSchemaHash = TradingToolRegistry::DescriptorSchemaHash(quoteDescriptor);
    response.clear();
    assert(UnixToolClient::Call(
        socketPath, schemaMismatchRequest, response, reason, kLocalSocketTimeoutMs, 65536));
    assert(response.find("\"status\":\"ok\"") != std::string::npos);

	const int concurrentSessions = 12;
	std::vector<TradingToolHostSessionBinding> concurrentBindings;
	for (int i = 0; i < concurrentSessions; ++i)
	{
		TradingToolHostSessionBinding concurrent = binding;
		concurrent.token = "socket-concurrent-token-0000-" + std::to_string(i);
		concurrent.session.executionContext.agentId = "concurrent-agent-" + std::to_string(i);
		concurrent.session.executionContext.sessionId = "concurrent-session-" + std::to_string(i);
		assert(host.RegisterSession(concurrent, reason));
		concurrentBindings.push_back(concurrent);
	}
	std::atomic<int> successful(0);
	std::vector<std::thread> clients;
	for (int i = 0; i < concurrentSessions; ++i)
	{
		clients.push_back(std::thread([&, i]() {
			const int concurrentClient = socket(AF_UNIX, SOCK_STREAM | SOCK_CLOEXEC, 0);
			if (concurrentClient < 0 ||
				connect(concurrentClient, reinterpret_cast<const sockaddr*>(&address), sizeof(address)) != 0)
				return;
			TradingToolHostRequest concurrentRequest;
			concurrentRequest.sessionToken = concurrentBindings[i].token;
			concurrentRequest.toolCallId = "concurrent-call-" + std::to_string(i);
			concurrentRequest.call.name = "market.get_quote";
			concurrentRequest.call.instrument = "EUR.USD";
			BindSchemaHash(registry, concurrentRequest);
			std::string concurrentBody;
			std::string concurrentReason;
			std::string concurrentResponse;
			if (TypedToolProtocol::EncodeRequest(concurrentRequest, concurrentBody, concurrentReason) &&
				TypedToolProtocol::WriteFrame(
					concurrentClient, concurrentBody, kLocalSocketTimeoutMs, concurrentReason) &&
				TypedToolProtocol::ReadFrame(
					concurrentClient, 4096, kLocalSocketTimeoutMs,
					concurrentResponse, concurrentReason) &&
				concurrentResponse.find("\"agent\":\"" +
					concurrentBindings[i].session.executionContext.agentId + "\"") != std::string::npos)
			{
				++successful;
			}
			close(concurrentClient);
		}));
	}
	for (std::size_t i = 0; i < clients.size(); ++i) clients[i].join();
	assert(successful.load() == concurrentSessions);

	std::atomic<int> pressureOk(0);
	std::atomic<int> pressureRejected(0);
	clients.clear();
	for (int i = 0; i < 4; ++i)
	{
		clients.push_back(std::thread([&, i]() {
			const int pressureClient = socket(AF_UNIX, SOCK_STREAM | SOCK_CLOEXEC, 0);
			assert(pressureClient >= 0);
			assert(connect(pressureClient, reinterpret_cast<const sockaddr*>(&address), sizeof(address)) == 0);
			TradingToolHostRequest pressureRequest;
			pressureRequest.sessionToken = binding.token;
			pressureRequest.toolCallId = "pressure-" + std::to_string(i);
			pressureRequest.call.name = "market.get_quote";
			pressureRequest.call.instrument = "EUR.USD";
			BindSchemaHash(registry, pressureRequest);
			std::string pressureBody;
			std::string pressureReason;
			std::string pressureResponse;
			assert(TypedToolProtocol::EncodeRequest(pressureRequest, pressureBody, pressureReason));
			assert(TypedToolProtocol::WriteFrame(
				pressureClient, pressureBody, kLocalSocketTimeoutMs, pressureReason));
			assert(TypedToolProtocol::ReadFrame(
				pressureClient, 4096, kLocalSocketTimeoutMs,
				pressureResponse, pressureReason));
			if (pressureResponse.find("\"status\":\"ok\"") != std::string::npos) ++pressureOk;
			if (pressureResponse.find("OWNER_QUEUE_BACKPRESSURE") != std::string::npos) ++pressureRejected;
			close(pressureClient);
		}));
	}
	for (std::size_t i = 0; i < clients.size(); ++i) clients[i].join();
	assert(pressureOk.load() >= 1);
	assert(pressureRejected.load() >= 1);
	assert(ownerHealthEvents.load() == pressureRejected.load());
	const UnixToolServerHealth toolHealth = server.GetHealth();
	assert(toolHealth.ownerBackpressureRejections >=
		static_cast<std::uint64_t>(pressureRejected.load()));
    server.Stop();
    assert(access(socketPath.c_str(), F_OK) != 0);
    std::uint64_t decisionRecords = 0;
    assert(SessionSupervisorAuditJournal::Verify(
        decisionAuditPath, decisionRecords, reason));
    assert(decisionRecords > 10);
    const std::string audited = AuditPayloads(decisionAuditPath);
    assert(audited.find("reason_code=SCHEMA_HASH_REQUIRED") != std::string::npos);
    assert(audited.find("reason_code=SCHEMA_HASH_MISMATCH") != std::string::npos);
    assert(audited.find("reason_code=CAPABILITY_REQUIRED") != std::string::npos);
    assert(audited.find("reason_code=SESSION_NOT_FOUND") != std::string::npos);
    assert(audited.find("reason_code=UNKNOWN_TOOL") != std::string::npos);
    assert(audited.find(binding.token) == std::string::npos);

    std::string malformed = body;
    const std::string duplicateFields = body.substr(4);
    malformed.append(duplicateFields); // duplicate all fields
    TradingToolHostRequest decoded;
    assert(!TypedToolProtocol::DecodeRequest(malformed, decoded, reason));
    assert(reason == "SCHEMA_UNKNOWN_OR_DUPLICATE_FIELD");

    TradingToolHostRequest unknownCanonical;
    unknownCanonical.sessionToken = binding.token;
    unknownCanonical.toolCallId = "unknown-canonical";
    unknownCanonical.call.name = "vendor.unknown";
    assert(TypedToolProtocol::EncodeRequest(unknownCanonical, body, reason));
    const std::size_t unknownSeparator = body.find("vendor.unknown");
    assert(unknownSeparator != std::string::npos);
    for (const char invalidCharacter : {'\n', '\t', '\0'})
    {
        std::string invalidToolName = body;
        invalidToolName[unknownSeparator + 6] = invalidCharacter;
        assert(!TypedToolProtocol::DecodeRequest(
            invalidToolName, decoded, reason));
        assert(reason == "INVALID_TOOL_NAME");
    }

    TradingToolHostRequest describeCanonical;
    describeCanonical.sessionToken = binding.token;
    describeCanonical.toolCallId = "describe-canonical";
    describeCanonical.call.name = "system.tools.describe";
    describeCanonical.call.targetToolName = "market.get_quote";
    assert(TypedToolProtocol::EncodeRequest(
        describeCanonical, body, reason));
    const std::size_t targetSeparator = body.find("market.get_quote");
    assert(targetSeparator != std::string::npos);
    body[targetSeparator + 6] = '\n';
    assert(!TypedToolProtocol::DecodeRequest(body, decoded, reason));
    assert(reason == "INVALID_TARGET_TOOL_NAME");

    TradingToolHostRequest place;
    place.sessionToken = binding.token;
    place.toolCallId = "strict-place";
    place.call.name = "trade.place_order";
    place.call.instrument = "EUR.USD";
    place.call.ibOrder.action = "BUY";
    place.call.ibOrder.orderType = "LMT";
    place.call.ibOrder.totalQuantity = 100.0;
    place.call.ibOrder.lmtPrice = 1.1;
    place.call.timeInForce = "DAY";
    place.call.expiresAtMs = OmsJournal::NowEpochMs() + 60000;
    BindSchemaHash(registry, place);
    assert(TypedToolProtocol::EncodeRequest(place, body, reason));
    assert(TypedToolProtocol::DecodeRequest(body, decoded, reason));
    assert(decoded.call.timeInForce == "DAY");
    TradingToolHostRequest invalidCallId = place;
    invalidCallId.toolCallId = "short";
    assert(!TypedToolProtocol::EncodeRequest(invalidCallId, body, reason));
    assert(reason == "INVALID_TOOL_CALL_ID");
    invalidCallId.toolCallId = "invalid command id";
    assert(!TypedToolProtocol::EncodeRequest(invalidCallId, body, reason));
    assert(reason == "INVALID_TOOL_CALL_ID");
    invalidCallId.toolCallId = std::string("valid-") + "\xc3\xa9";
    assert(!TypedToolProtocol::EncodeRequest(invalidCallId, body, reason));
    assert(reason == "INVALID_TOOL_CALL_ID");
    assert(TypedToolProtocol::EncodeRequest(place, body, reason));
    const std::size_t callIdOffset = body.find(place.toolCallId);
    assert(callIdOffset != std::string::npos);
    std::string invalidCallIdBody = body;
    invalidCallIdBody.replace(
        callIdOffset, place.toolCallId.size(),
        std::string(place.toolCallId.size(), '!'));
    assert(!TypedToolProtocol::DecodeRequest(
        invalidCallIdBody, decoded, reason));
    assert(reason == "INVALID_TOOL_CALL_ID");
    const std::string legacyBody = WithoutProtocolRange(body);
    assert(TypedToolProtocol::DecodeRequest(legacyBody, decoded, reason));
    assert(decoded.protocolMinVersion == 1 && decoded.protocolMaxVersion == 1);

    TradingToolHostRequest invalid = place;
    invalid.call.timeInForce.clear();
    assert(!TypedToolProtocol::EncodeRequest(invalid, body, reason));
    assert(reason.find("INVALID_TIME_IN_FORCE:") == 0);

    invalid = place;
    invalid.protocolMinVersion = 2;
    invalid.protocolMaxVersion = 2;
    assert(!TypedToolProtocol::EncodeRequest(invalid, body, reason));
    assert(reason == "UNSUPPORTED_PROTOCOL_VERSION");

    TradingToolHostRequest cancel;
    cancel.sessionToken = binding.token;
    cancel.toolCallId = "strict-cancel";
    cancel.call.name = "trade.cancel_order";
    cancel.call.orderId = 77;
    BindSchemaHash(registry, cancel);
    assert(TypedToolProtocol::EncodeRequest(cancel, body, reason));
    assert(TypedToolProtocol::DecodeRequest(body, decoded, reason));
    assert(decoded.call.orderId == 77);
    assert(decoded.call.instrument.empty());
    cancel.call.instrument = "EUR.USD";
    assert(!TypedToolProtocol::EncodeRequest(cancel, body, reason));
    assert(reason.find("UNEXPECTED_TOOL_FIELD:") == 0);

    TradingToolHostRequest statusQuery;
    statusQuery.sessionToken = binding.token;
    statusQuery.toolCallId = "strict-command-status";
    statusQuery.call.name = "execution.get_command_status";
    statusQuery.call.targetCommandId = "place-command-001";
    assert(TypedToolProtocol::EncodeRequest(statusQuery, body, reason));
    assert(TypedToolProtocol::DecodeRequest(body, decoded, reason));
    assert(decoded.call.targetCommandId == "place-command-001");
    std::string invalidCommandBody = body;
    const std::size_t commandOffset =
        invalidCommandBody.find(statusQuery.call.targetCommandId);
    assert(commandOffset != std::string::npos);
    invalidCommandBody.replace(
        commandOffset, statusQuery.call.targetCommandId.size(),
        std::string(statusQuery.call.targetCommandId.size(), '!'));
    assert(!TypedToolProtocol::DecodeRequest(
        invalidCommandBody, decoded, reason));
    assert(reason.find("INVALID_COMMAND_ID:") == 0);
    statusQuery.call.targetCommandId.clear();
    assert(!TypedToolProtocol::EncodeRequest(statusQuery, body, reason));
    assert(reason.find("MISSING_REQUIRED_FIELD:") == 0);
    statusQuery.call.targetCommandId = "invalid command";
    assert(!TypedToolProtocol::EncodeRequest(statusQuery, body, reason));
    assert(reason.find("INVALID_COMMAND_ID:") == 0);
    statusQuery.call.targetCommandId = "place-command-001";
    statusQuery.call.orderId = 77;
    assert(!TypedToolProtocol::EncodeRequest(statusQuery, body, reason));
    assert(reason.find("UNEXPECTED_TOOL_FIELD:") == 0);

    TradingToolHostRequest invalidWait;
    invalidWait.sessionToken = binding.token;
    invalidWait.toolCallId = "invalid-wait";
    invalidWait.call.name = "events.wait";
    invalidWait.call.waitTimeoutMs = 30001;
    assert(!TypedToolProtocol::EncodeRequest(invalidWait, body, reason));
    assert(reason.find("INVALID_WAIT_TIMEOUT:") == 0);

    std::remove(journalPath.c_str());
    std::remove(decisionAuditPath.c_str());
}

void TestGlobalQueueBackpressureDecisionAudit()
{
    const std::string journalPath =
        TempPath("/tmp/hepta-global-pressure-journal-XXXXXX");
    const std::string auditPath =
        TempPath("/tmp/hepta-global-pressure-audit-XXXXXX");
    const std::string socketPath =
        TempPath("/tmp/hepta-global-pressure-socket-XXXXXX");
    OmsJournal journal;
    assert(journal.Init(journalPath));
    ExecutionCoordinatorCallbacks callbacks;
    ExecutionCoordinator execution(journal, callbacks);
    TradingToolRegistry registry(execution);
    TradingToolHost host(registry);
    SessionSupervisorAuditJournal audit;
    std::string reason;
    assert(audit.Init(auditPath, reason));
    UnixToolServer server(host);
    server.SetDecisionAuditJournal(&audit);
    assert(server.Start(socketPath, reason, 4096, 1000, 1, 1, 1, 1, 1));

    sockaddr_un address;
    std::memset(&address, 0, sizeof(address));
    address.sun_family = AF_UNIX;
    std::strncpy(address.sun_path, socketPath.c_str(),
        sizeof(address.sun_path) - 1);
    const int blockedClient =
        socket(AF_UNIX, SOCK_STREAM | SOCK_CLOEXEC, 0);
    assert(blockedClient >= 0);
    assert(connect(blockedClient,
        reinterpret_cast<sockaddr*>(&address), sizeof(address)) == 0);
    const std::uint32_t networkLength = htonl(16);
    assert(write(blockedClient, &networkLength, sizeof(networkLength)) ==
        static_cast<ssize_t>(sizeof(networkLength)));
    assert(write(blockedClient, "X", 1) == 1);
    for (int retry = 0;
         retry < 1000 && server.GetHealth().pendingConnections != 1;
         ++retry)
        usleep(1000);
    assert(server.GetHealth().pendingConnections == 1);

    const int rejectedClient =
        socket(AF_UNIX, SOCK_STREAM | SOCK_CLOEXEC, 0);
    assert(rejectedClient >= 0);
    assert(connect(rejectedClient,
        reinterpret_cast<sockaddr*>(&address), sizeof(address)) == 0);
    for (int retry = 0;
         retry < 1000 &&
             server.GetHealth().queueBackpressureRejections != 1;
         ++retry)
        usleep(1000);
    assert(server.GetHealth().queueBackpressureRejections == 1);
    close(rejectedClient);

    assert(shutdown(blockedClient, SHUT_WR) == 0);
    std::string response;
    assert(TypedToolProtocol::ReadFrame(
        blockedClient, 4096, 2000, response, reason));
    assert(response.find("INVALID_FRAME") != std::string::npos);
    close(blockedClient);
    server.Stop();

    std::uint64_t records = 0;
    assert(SessionSupervisorAuditJournal::Verify(auditPath, records, reason));
    const std::string audited = AuditPayloads(auditPath);
    assert(audited.find("reason_code=GLOBAL_QUEUE_BACKPRESSURE") !=
        std::string::npos);
    assert(audited.find("peer_uid=" + std::to_string(getuid())) !=
        std::string::npos);
    assert(audited.find("phase=outcome") != std::string::npos);
    std::remove(socketPath.c_str());
    std::remove(auditPath.c_str());
    std::remove(journalPath.c_str());
}

void TestWatchRejectionAndDescriptorEffectAudit()
{
    const std::string journalPath =
        TempPath("/tmp/hepta-watch-audit-journal-XXXXXX");
    const std::string auditPath =
        TempPath("/tmp/hepta-watch-decision-audit-XXXXXX");
    const std::string socketPath =
        TempPath("/tmp/hepta-watch-audit-socket-XXXXXX");
    OmsJournal journal;
    assert(journal.Init(journalPath));
    std::atomic<int> dispatches(0);
    ExecutionCoordinatorCallbacks callbacks;
    callbacks.cancelIbOrder = [&](long) {
        ++dispatches;
        return true;
    };
    ExecutionCoordinator execution(journal, callbacks);
    TradingToolRegistry registry(execution);
    TradingToolHost host(registry);
    TradingToolHostSessionBinding binding;
    binding.token = "watch-audit-session-token-0001";
    binding.peerUid = static_cast<std::uint32_t>(getuid());
    binding.session.executionContext.agentId = "watch-audit-agent";
    binding.session.executionContext.sessionId = "watch-audit-session";
    binding.session.executionContext.account = "DU123";
    binding.session.environment = "WATCH";
    binding.executionDomain = "IB-PAPER";
    binding.expiresAtMs =
        static_cast<std::uint64_t>(OmsJournal::NowEpochMs()) + 60000;
    std::string reason;
    assert(host.RegisterSession(binding, reason));
    SessionSupervisorAuditJournal audit;
    assert(audit.Init(auditPath, reason));
    UnixToolServer server(host);
    server.SetDecisionAuditJournal(&audit);
    assert(server.Start(socketPath, reason, 4096, 1000, 1, 8, 1, 4, 1));

    TradingToolHostRequest watchMutation;
    watchMutation.sessionToken = binding.token;
    watchMutation.toolCallId = "watch-mutation-denied";
    watchMutation.call.name = "trade.cancel_order";
    watchMutation.call.orderId = 42;
    BindSchemaHash(registry, watchMutation);
    const std::string watchResponse = CallTool(socketPath, watchMutation);
    assert(watchResponse.find("WATCH_SESSION_CANNOT_TRADE") !=
        std::string::npos);
    assert(dispatches.load() == 0);

    // A trade-looking string that has no registered mutation descriptor must
    // not create a durable mutation intent.
    TradingToolHostRequest unknownPrefix;
    unknownPrefix.sessionToken = binding.token;
    unknownPrefix.toolCallId = "trade-prefix-is-not-authority";
    unknownPrefix.call.name = "trade.not_registered";
    const std::string unknownResponse = CallTool(socketPath, unknownPrefix);
    assert(unknownResponse.find("UNKNOWN_TOOL") != std::string::npos);
    server.Stop();

    std::uint64_t records = 0;
    assert(SessionSupervisorAuditJournal::Verify(auditPath, records, reason));
    const std::string audited = AuditPayloads(auditPath);
    assert(audited.find("reason_code=WATCH_SESSION_CANNOT_TRADE") !=
        std::string::npos);
    assert(CountOccurrences(
        audited, "tool_call_id=watch-mutation-denied") == 2);
    assert(CountOccurrences(
        audited, "tool_call_id=trade-prefix-is-not-authority") == 1);
    std::remove(socketPath.c_str());
    std::remove(auditPath.c_str());
    std::remove(journalPath.c_str());
}

void TestWatchSnapshotAuditCardinalityAndBoundedDelivery()
{
    const std::string journalPath =
        TempPath("/tmp/hepta-watch-snapshot-journal-XXXXXX");
    const std::string auditPath =
        TempPath("/tmp/hepta-watch-snapshot-audit-XXXXXX");
    const std::string socketPath =
        TempPath("/tmp/hepta-watch-snapshot-socket-XXXXXX");
    OmsJournal journal;
    assert(journal.Init(journalPath));
    ExecutionCoordinator execution(journal, ExecutionCoordinatorCallbacks());
    std::string ordersPayloadOverride;
    std::atomic<int> childReads(0);
    TradingToolReadCallbacks reads;
    reads.accountGetSummary = [&](const TradingToolSession&,
        const TradingToolCall&, std::string& payload, std::string&) {
        ++childReads;
        payload = "{\"authoritative\":true}";
        return true;
    };
    reads.portfolioListPositions = [&](const TradingToolSession&,
        const TradingToolCall&, std::string& payload, std::string&) {
        ++childReads;
        payload = "{\"positions\":[]}";
        return true;
    };
    reads.ordersList = [&](const TradingToolSession&,
        const TradingToolCall&, std::string& payload, std::string&) {
        ++childReads;
        payload = ordersPayloadOverride.empty() ?
            "{\"orders\":[]}" : ordersPayloadOverride;
        return true;
    };
    reads.riskGetLimits = [&](const TradingToolSession&,
        const TradingToolCall&, std::string& payload, std::string&) {
        ++childReads;
        payload = "{\"gross_absolute_position\":0}";
        return true;
    };
    reads.marketGetQuote = [&](const TradingToolSession&,
        const TradingToolCall& call, std::string& payload, std::string&) {
        ++childReads;
        payload = "{\"instrument\":\"" + call.instrument +
            "\",\"bid\":1.1,\"ask\":1.2}";
        return true;
    };
    reads.systemGetHealth = [&](const TradingToolSession&,
        const TradingToolCall&, std::string& payload, std::string&) {
        ++childReads;
        payload = "{\"gateway_ready\":true}";
        return true;
    };
    TradingToolRegistry registry(execution, reads);
    TradingToolHost host(registry);
    TradingToolHostSessionBinding binding;
    binding.token = "watch-snapshot-session-token-0001";
    binding.peerUid = static_cast<std::uint32_t>(getuid());
    binding.session.executionContext.agentId = "watch-snapshot-agent";
    binding.session.executionContext.sessionId = "watch-snapshot-session";
    binding.session.executionContext.account = "DU123";
    binding.session.environment = "WATCH";
    binding.executionDomain = "IB-PAPER";
    binding.allowedInstruments.insert("EUR.USD");
    const char* const capabilities[] = {
        "system.read", "market.read", "account.read", "portfolio.read",
        "orders.read", "risk.read"
    };
    for (std::size_t i = 0;
         i < sizeof(capabilities) / sizeof(capabilities[0]); ++i)
        binding.session.capabilities.insert(capabilities[i]);
    binding.expiresAtMs =
        static_cast<std::uint64_t>(OmsJournal::NowEpochMs()) + 60000;
    std::string reason;
    assert(host.RegisterSession(binding, reason));
    SessionSupervisorAuditJournal audit;
    assert(audit.Init(auditPath, reason));
    UnixToolServer server(host);
    server.SetDecisionAuditJournal(&audit);
    assert(server.Start(socketPath, reason, 4096, 3000, 1, 8, 1, 4, 1));

    NativeToolClientConfig config;
    config.socketPath = socketPath;
    config.sessionToken = binding.token;
    config.timeoutMs = 5000;
    config.maxResponseBytes =
        TradingToolWireLimits::MaximumResultEnvelopeBytes();
    NativeToolClient client(config);
    TradingToolHostRequest catalog;
    catalog.toolCallId = "watch-snapshot-catalog";
    catalog.call.name = "system.tools.list";
    NativeToolClientResult delivered;
    assert(client.Call(catalog, delivered, reason));
    assert(delivered.envelope.status == "ok");

    TradingToolHostRequest snapshot;
    snapshot.toolCallId = "watch-snapshot-composite";
    snapshot.call.name = "watch.get_snapshot";
    snapshot.call.instrument = "EUR.USD";
    assert(client.Call(snapshot, delivered, reason));
    assert(delivered.envelope.status == "ok");
    assert(childReads.load() == 6);

    std::uint64_t records = 0;
    assert(SessionSupervisorAuditJournal::Verify(auditPath, records, reason));
    assert(records == 2);
    std::string audited = AuditPayloads(auditPath);
    assert(CountOccurrences(
        audited, "tool_call_id=watch-snapshot-catalog") == 1);
    assert(CountOccurrences(
        audited, "tool_call_id=watch-snapshot-composite") == 1);
    assert(audited.find("tool_name=system.tools.describe") ==
        std::string::npos);
    assert(audited.find("tool_name=account.get_summary") ==
        std::string::npos);

    ordersPayloadOverride = "{\"padding\":\"" +
        std::string(
            TradingToolWireLimits::MaximumResultEnvelopeBytes(), 'x') +
        "\"}";
    snapshot.toolCallId = "watch-snapshot-over-limit";
    delivered = NativeToolClientResult();
    assert(client.Call(snapshot, delivered, reason));
    assert(delivered.envelope.status == "error");
    assert(delivered.envelope.reasonCode ==
        "WATCH_SNAPSHOT_RESPONSE_TOO_LARGE");
    assert(delivered.envelope.payloadJson == "null");
    assert(delivered.responseJson.size() <=
        TradingToolWireLimits::MaximumResultEnvelopeBytes());
    server.Stop();

    records = 0;
    assert(SessionSupervisorAuditJournal::Verify(auditPath, records, reason));
    assert(records == 3);
    audited = AuditPayloads(auditPath);
    assert(CountOccurrences(
        audited, "tool_call_id=watch-snapshot-over-limit") == 1);
    assert(audited.find(
        "reason_code=WATCH_SNAPSHOT_RESPONSE_TOO_LARGE") !=
        std::string::npos);
    std::remove(socketPath.c_str());
    std::remove(auditPath.c_str());
    std::remove(journalPath.c_str());
}

void TestMutationOutcomeAuditFailureIsUncertain()
{
    const std::string journalPath =
        TempPath("/tmp/hepta-outcome-fail-journal-XXXXXX");
    const std::string auditPath =
        TempPath("/tmp/hepta-outcome-fail-audit-XXXXXX");
    const std::string displacedAuditPath =
        TempPath("/tmp/hepta-outcome-fail-displaced-XXXXXX");
    const std::string socketPath =
        TempPath("/tmp/hepta-outcome-fail-socket-XXXXXX");
    OmsJournal journal;
    assert(journal.Init(journalPath));
    std::atomic<int> dispatches(0);
    ExecutionCoordinatorCallbacks callbacks;
    callbacks.cancelIbOrder = [&](long) {
        ++dispatches;
        assert(rename(auditPath.c_str(), displacedAuditPath.c_str()) == 0);
        const int replacement = open(
            auditPath.c_str(), O_WRONLY | O_CREAT | O_CLOEXEC, 0600);
        assert(replacement >= 0);
        close(replacement);
        return true;
    };
    ExecutionCoordinator execution(journal, callbacks);
    TradingToolRegistry registry(execution);
    TradingToolHost host(registry);
    TradingToolHostSessionBinding binding;
    binding.token = "outcome-fail-session-token-001";
    binding.peerUid = static_cast<std::uint32_t>(getuid());
    binding.session.executionContext.agentId = "outcome-fail-agent";
    binding.session.executionContext.sessionId = "outcome-fail-session";
    binding.session.executionContext.account = "DU123";
    binding.session.executionContext.allowCancelAny = true;
    binding.session.environment = "PAPER";
    binding.session.capabilities.insert("trade.cancel");
    binding.executionDomain = "IB-PAPER";
    binding.maxTradeCallsPerMinute = 4;
    binding.expiresAtMs =
        static_cast<std::uint64_t>(OmsJournal::NowEpochMs()) + 60000;
    std::string reason;
    assert(host.RegisterSession(binding, reason));
    SessionSupervisorAuditJournal audit;
    assert(audit.Init(auditPath, reason));
    UnixToolServer server(host);
    server.SetDecisionAuditJournal(&audit);
    assert(server.Start(socketPath, reason, 4096, 1000, 1, 8, 1, 4, 1));

    TradingToolHostRequest request;
    request.sessionToken = binding.token;
    request.toolCallId = "outcome-audit-fails-after-dispatch";
    request.call.name = "trade.cancel_order";
    request.call.orderId = 4242;
    BindSchemaHash(registry, request);
    const std::string response = CallTool(socketPath, request);
    assert(dispatches.load() == 1);
    assert(response.find("\"status\":\"uncertain\"") != std::string::npos);
    assert(response.find("DECISION_AUDIT_OUTCOME_UNCERTAIN") !=
        std::string::npos);
    server.Stop();

    std::uint64_t records = 0;
    assert(SessionSupervisorAuditJournal::Verify(
        displacedAuditPath, records, reason));
    assert(records == 1);
    const std::string audited = AuditPayloads(displacedAuditPath);
    assert(audited.find("phase=intent") != std::string::npos);
    assert(audited.find(request.toolCallId) != std::string::npos);
    std::remove(socketPath.c_str());
    std::remove(auditPath.c_str());
    std::remove(displacedAuditPath.c_str());
    std::remove(journalPath.c_str());
}

void TestStopPreservesDurableMutationIntent()
{
    const std::string journalPath =
        TempPath("/tmp/hepta-stop-intent-journal-XXXXXX");
    const std::string auditPath =
        TempPath("/tmp/hepta-stop-intent-audit-XXXXXX");
    const std::string socketPath =
        TempPath("/tmp/hepta-stop-intent-socket-XXXXXX");
    OmsJournal journal;
    assert(journal.Init(journalPath));
    std::atomic<bool> readStarted(false);
    std::atomic<bool> releaseRead(false);
    std::atomic<int> mutationDispatches(0);
    TradingToolReadCallbacks reads;
    reads.marketGetQuote = [&](const TradingToolSession&,
                               const TradingToolCall&,
                               std::string& payload,
                               std::string&) {
        readStarted.store(true);
        while (!releaseRead.load()) usleep(1000);
        payload = "{}";
        return true;
    };
    ExecutionCoordinatorCallbacks callbacks;
    callbacks.cancelIbOrder = [&](long) {
        ++mutationDispatches;
        return true;
    };
    ExecutionCoordinator execution(journal, callbacks);
    TradingToolRegistry registry(execution, reads);
    TradingToolHost host(registry);
    TradingToolHostSessionBinding binding;
    binding.token = "stop-intent-session-token-0001";
    binding.peerUid = static_cast<std::uint32_t>(getuid());
    binding.session.executionContext.agentId = "stop-intent-agent";
    binding.session.executionContext.sessionId = "stop-intent-session";
    binding.session.executionContext.account = "DU123";
    binding.session.executionContext.allowCancelAny = true;
    binding.session.environment = "PAPER";
    binding.session.capabilities.insert("market.read");
    binding.session.capabilities.insert("trade.cancel");
    binding.executionDomain = "IB-PAPER";
    binding.maxTradeCallsPerMinute = 4;
    binding.expiresAtMs =
        static_cast<std::uint64_t>(OmsJournal::NowEpochMs()) + 60000;
    std::string reason;
    assert(host.RegisterSession(binding, reason));
    SessionSupervisorAuditJournal audit;
    assert(audit.Init(auditPath, reason));
    UnixToolServer server(host);
    server.SetDecisionAuditJournal(&audit);
    assert(server.Start(socketPath, reason, 4096, 2000, 1, 8, 1, 4, 2));

    TradingToolHostRequest blocker;
    blocker.sessionToken = binding.token;
    blocker.toolCallId = "stop-read-blocker";
    blocker.call.name = "market.get_quote";
    blocker.call.instrument = "EUR.USD";
    BindSchemaHash(registry, blocker);
    std::string blockerResponse;
    std::thread blockerClient([&]() {
        blockerResponse = CallTool(socketPath, blocker, 4000);
    });
    for (int retry = 0; retry < 2000 && !readStarted.load(); ++retry)
        usleep(1000);
    assert(readStarted.load());

    TradingToolHostRequest mutation;
    mutation.sessionToken = binding.token;
    mutation.toolCallId = "stop-mutation-after-intent";
    mutation.call.name = "trade.cancel_order";
    mutation.call.orderId = 5151;
    BindSchemaHash(registry, mutation);
    std::string mutationResponse;
    std::thread mutationClient([&]() {
        mutationResponse = CallTool(socketPath, mutation, 4000);
    });
    bool intentDurable = false;
    for (int retry = 0; retry < 2000; ++retry)
    {
        if (AuditPayloads(auditPath).find(
                "tool_call_id=stop-mutation-after-intent") !=
            std::string::npos)
        {
            intentDurable = true;
            break;
        }
        usleep(1000);
    }
    assert(intentDurable);
    assert(server.GetHealth().pendingConnections >= 1);

    std::thread stopper([&]() { server.Stop(); });
    mutationClient.join();
    assert(mutationDispatches.load() == 0);
    assert(mutationResponse.find("\"status\":\"uncertain\"") !=
        std::string::npos);
    assert(mutationResponse.find("SERVER_STOPPED_AFTER_DURABLE_INTENT") !=
        std::string::npos);
    releaseRead.store(true);
    blockerClient.join();
    stopper.join();
    assert(blockerResponse.find("\"status\":\"ok\"") != std::string::npos);

    std::uint64_t records = 0;
    assert(SessionSupervisorAuditJournal::Verify(auditPath, records, reason));
    const std::string audited = AuditPayloads(auditPath);
    assert(CountOccurrences(
        audited, "tool_call_id=stop-mutation-after-intent") == 2);
    assert(audited.find(
        "reason_code=SERVER_STOPPED_AFTER_DURABLE_INTENT") !=
        std::string::npos);
    std::remove(socketPath.c_str());
    std::remove(auditPath.c_str());
    std::remove(journalPath.c_str());
}

void TestOwnerRoundRobin()
{
    const std::string journalPath = TempPath("/tmp/hepta-tool-fair-journal-XXXXXX");
    const std::string socketPath = TempPath("/tmp/hepta-tool-fair-socket-XXXXXX");
    OmsJournal journal;
    assert(journal.Init(journalPath));
    ExecutionCoordinatorCallbacks executionCallbacks;
    ExecutionCoordinator execution(journal, executionCallbacks);
    std::mutex orderMutex;
    std::vector<std::string> executionOrder;
    TradingToolReadCallbacks reads;
    reads.marketGetQuote = [&](const TradingToolSession& session, const TradingToolCall&,
                               std::string& payload, std::string&) {
        {
            std::lock_guard<std::mutex> lock(orderMutex);
            executionOrder.push_back(session.executionContext.toolCallId);
        }
        usleep(100000);
        payload = "{}";
        return true;
    };
    TradingToolRegistry registry(execution, reads);
    TradingToolHost host(registry);
    TradingToolHostSessionBinding first;
    first.token = "fair-owner-first-token-000001";
    first.peerUid = static_cast<std::uint32_t>(getuid());
    first.session.executionContext.agentId = "fair-agent-a";
    first.session.executionContext.sessionId = "fair-session-a";
    first.session.executionContext.account = "DU123";
    first.session.environment = "WATCH";
    first.session.capabilities.insert("market.read");
    first.executionDomain = "IB-PAPER";
    first.expiresAtMs = static_cast<std::uint64_t>(OmsJournal::NowEpochMs()) + 60000;
    TradingToolHostSessionBinding second = first;
    second.token = "fair-owner-second-token-00001";
    second.session.executionContext.agentId = "fair-agent-b";
    second.session.executionContext.sessionId = "fair-session-b";
    std::string reason;
    assert(host.RegisterSession(first, reason));
    assert(host.RegisterSession(second, reason));
    UnixToolServer server(host);
    server.AllowMissingDecisionAuditForTests();
    assert(server.Start(socketPath, reason, 4096, 1000, 1, 16, 1, 8, 2));
    sockaddr_un address;
    std::memset(&address, 0, sizeof(address));
    address.sun_family = AF_UNIX;
    std::strncpy(address.sun_path, socketPath.c_str(), sizeof(address.sun_path) - 1);
    auto call = [&](const std::string& token, const std::string& callId) {
        const int client = socket(AF_UNIX, SOCK_STREAM | SOCK_CLOEXEC, 0);
        assert(client >= 0);
        assert(connect(client, reinterpret_cast<sockaddr*>(&address), sizeof(address)) == 0);
        TradingToolHostRequest request;
        request.sessionToken = token;
        request.toolCallId = callId;
        request.call.name = "market.get_quote";
        request.call.instrument = "EUR.USD";
        BindSchemaHash(registry, request);
        std::string body;
        std::string callReason;
        std::string response;
        assert(TypedToolProtocol::EncodeRequest(request, body, callReason));
        assert(TypedToolProtocol::WriteFrame(client, body, 1000, callReason));
        assert(TypedToolProtocol::ReadFrame(client, 4096, 1000, response, callReason));
        assert(response.find("\"status\":\"ok\"") != std::string::npos);
        close(client);
    };
    std::vector<std::thread> clients;
    clients.push_back(std::thread(call, first.token, "owner-a1"));
    usleep(20000);
    clients.push_back(std::thread(call, first.token, "owner-a2"));
    clients.push_back(std::thread(call, first.token, "owner-a3"));
    usleep(20000);
    clients.push_back(std::thread(call, second.token, "owner-b1"));
    for (std::size_t i = 0; i < clients.size(); ++i) clients[i].join();
    server.Stop();
    std::size_t bIndex = executionOrder.size();
    std::size_t a2Index = executionOrder.size();
    std::size_t a3Index = executionOrder.size();
    for (std::size_t i = 0; i < executionOrder.size(); ++i)
    {
        if (executionOrder[i] == "owner-b1") bIndex = i;
        if (executionOrder[i] == "owner-a2") a2Index = i;
        if (executionOrder[i] == "owner-a3") a3Index = i;
    }
    assert(bIndex < std::max(a2Index, a3Index));
    std::remove(journalPath.c_str());
}

void TestDeadlineCancellationAndDrain()
{
    const std::string journalPath = TempPath("/tmp/hepta-tool-lifecycle-journal-XXXXXX");
    const std::string socketPath = TempPath("/tmp/hepta-tool-lifecycle-socket-XXXXXX");
    OmsJournal journal;
    assert(journal.Init(journalPath));
    ExecutionCoordinatorCallbacks executionCallbacks;
    ExecutionCoordinator execution(journal, executionCallbacks);
    TradingToolReadCallbacks reads;
    reads.marketGetQuote = [](const TradingToolSession& session, const TradingToolCall&,
                              std::string& payload, std::string&) {
        if (session.executionContext.toolCallId.find("block-") == 0) usleep(250000);
        payload = "{}";
        return true;
    };
    TradingToolRegistry registry(execution, reads);
    TradingToolHost host(registry);
    TradingToolHostSessionBinding binding;
    binding.token = "lifecycle-owner-token-000001";
    binding.peerUid = static_cast<std::uint32_t>(getuid());
    binding.session.executionContext.agentId = "lifecycle-agent";
    binding.session.executionContext.sessionId = "lifecycle-session";
    binding.session.executionContext.account = "DU123";
    binding.session.environment = "WATCH";
    binding.session.capabilities.insert("market.read");
    binding.session.capabilities.insert("system.read");
    binding.executionDomain = "IB-PAPER";
    binding.expiresAtMs = static_cast<std::uint64_t>(OmsJournal::NowEpochMs()) + 60000;
    std::string reason;
    assert(host.RegisterSession(binding, reason));
    UnixToolServer server(host);
    server.AllowMissingDecisionAuditForTests();
    assert(server.Start(socketPath, reason, 4096, 2000, 1, 16, 1, 8, 2, 1000));

    auto call = [&](const TradingToolHostRequest& request) {
        const int client = socket(AF_UNIX, SOCK_STREAM | SOCK_CLOEXEC, 0);
        assert(client >= 0);
        sockaddr_un address;
        std::memset(&address, 0, sizeof(address));
        address.sun_family = AF_UNIX;
        std::strncpy(address.sun_path, socketPath.c_str(), sizeof(address.sun_path) - 1);
        assert(connect(client, reinterpret_cast<sockaddr*>(&address), sizeof(address)) == 0);
        std::string body;
        std::string callReason;
        std::string response;
        assert(TypedToolProtocol::EncodeRequest(request, body, callReason));
        assert(TypedToolProtocol::WriteFrame(client, body, 1000, callReason));
        assert(TypedToolProtocol::ReadFrame(client, 4096, 2000, response, callReason));
        close(client);
        return response;
    };
    auto quote = [&](const std::string& callId) {
        TradingToolHostRequest request;
        request.sessionToken = binding.token;
        request.toolCallId = callId;
        request.call.name = "market.get_quote";
        request.call.instrument = "EUR.USD";
        BindSchemaHash(registry, request);
        return request;
    };

    std::string blockerResponse;
    std::thread blocker([&]() { blockerResponse = call(quote("block-deadline")); });
    while (server.GetHealth().activeRequests == 0) usleep(1000);
    TradingToolHostRequest deadline = quote("deadline-target");
    deadline.queueDeadlineAtMs = static_cast<std::uint64_t>(OmsJournal::NowEpochMs()) + 50;
    const std::string deadlineResponse = call(deadline);
    blocker.join();
    assert(deadlineResponse.find("QUEUE_DEADLINE_EXCEEDED") != std::string::npos);
    assert(blockerResponse.find("\"status\":\"ok\"") != std::string::npos);

    std::thread secondBlocker([&]() { blockerResponse = call(quote("block-cancel")); });
    while (server.GetHealth().activeRequests == 0) usleep(1000);
    std::string targetResponse;
    std::thread target([&]() { targetResponse = call(quote("cancel-target")); });
    while (server.GetHealth().pendingConnections == 0) usleep(1000);
    TradingToolHostRequest cancel;
    cancel.sessionToken = binding.token;
    cancel.toolCallId = "cancel-command";
    cancel.call.name = "system.cancel_request";
    cancel.cancelToolCallId = "cancel-target";
    BindSchemaHash(registry, cancel);
    const std::string cancelResponse = call(cancel);
    target.join();
    secondBlocker.join();
    assert(cancelResponse.find("REQUEST_CANCELLED") != std::string::npos);
    assert(targetResponse.find("REQUEST_CANCELLED") != std::string::npos);

    std::thread drainRequest([&]() { blockerResponse = call(quote("block-drain")); });
    while (server.GetHealth().activeRequests == 0) usleep(1000);
    assert(server.Drain(1000));
    drainRequest.join();
    assert(blockerResponse.find("\"status\":\"ok\"") != std::string::npos);
    const UnixToolServerHealth health = server.GetHealth();
    assert(health.deadlineRejections >= 1);
    assert(health.cancelledRequests >= 1);
    std::remove(journalPath.c_str());
}

void TestMutationRateLimitDecisionAudit()
{
    const std::string journalPath =
        TempPath("/tmp/hepta-tool-rate-journal-XXXXXX");
    const std::string auditPath =
        TempPath("/tmp/hepta-tool-rate-audit-XXXXXX");
    const std::string socketPath =
        TempPath("/tmp/hepta-tool-rate-socket-XXXXXX");
    OmsJournal journal;
    assert(journal.Init(journalPath));
    ExecutionCoordinatorCallbacks callbacks;
    callbacks.cancelIbOrder = [](long) { return true; };
    ExecutionCoordinator execution(journal, callbacks);
    TradingToolRegistry registry(execution);
    TradingToolHost host(registry);
    TradingToolHostSessionBinding binding;
    binding.token = "rate-limit-session-token-0001";
    binding.peerUid = static_cast<std::uint32_t>(getuid());
    binding.session.executionContext.agentId = "rate-agent";
    binding.session.executionContext.sessionId = "rate-session";
    binding.session.executionContext.account = "DU123";
    binding.session.environment = "PAPER";
    binding.session.capabilities.insert("trade.cancel");
    binding.executionDomain = "IB-PAPER";
    binding.maxTradeCallsPerMinute = 1;
    binding.expiresAtMs =
        static_cast<std::uint64_t>(OmsJournal::NowEpochMs()) + 60000;
    std::string reason;
    assert(host.RegisterSession(binding, reason));
    SessionSupervisorAuditJournal decisionAudit;
    assert(decisionAudit.Init(auditPath, reason));
    UnixToolServer server(host);
    server.SetDecisionAuditJournal(&decisionAudit);
    assert(server.Start(socketPath, reason, 4096, 1000, 1, 8, 1, 4, 1));

    TradingToolDescriptor cancelDescriptor;
    assert(registry.GetDescriptor("trade.cancel_order", cancelDescriptor));
    TradingToolHostRequest request;
    request.sessionToken = binding.token;
    request.call.name = "trade.cancel_order";
    request.call.orderId = 77;
    request.expectedSchemaHash =
        TradingToolRegistry::DescriptorSchemaHash(cancelDescriptor);
    std::string response;
    request.toolCallId = "rate-cancel-first";
    assert(UnixToolClient::Call(
        socketPath, request, response, reason, 1000, 65536));
    request.toolCallId = "rate-cancel-second";
    response.clear();
    assert(UnixToolClient::Call(
        socketPath, request, response, reason, 1000, 65536));
    assert(response.find("RATE_LIMIT") == std::string::npos);
    request.toolCallId = "rate-cancel-third";
    response.clear();
    assert(UnixToolClient::Call(
        socketPath, request, response, reason, 1000, 65536));
    assert(response.find("RATE_LIMIT") == std::string::npos);
    request.toolCallId = "rate-cancel-fourth";
    response.clear();
    assert(UnixToolClient::Call(
        socketPath, request, response, reason, 1000, 65536));
    assert(response.find("RATE_LIMIT") == std::string::npos);
    request.toolCallId = "rate-cancel-fifth";
    response.clear();
    assert(UnixToolClient::Call(
        socketPath, request, response, reason, 1000, 65536));
    assert(response.find("AGENT_RISK_REDUCTION_RATE_LIMIT") !=
        std::string::npos);

    const std::string displacedAuditPath =
        TempPath("/tmp/hepta-tool-rate-audit-displaced-XXXXXX");
    assert(rename(auditPath.c_str(), displacedAuditPath.c_str()) == 0);
    const int replacement = open(
        auditPath.c_str(), O_WRONLY | O_CREAT | O_CLOEXEC, 0600);
    assert(replacement >= 0);
    close(replacement);
    request.toolCallId = "rate-cancel-audit-fail";
    response.clear();
    assert(UnixToolClient::Call(
        socketPath, request, response, reason, 1000, 65536));
    assert(response.find("DECISION_AUDIT_WRITE_FAILED") != std::string::npos);
    server.Stop();

    std::uint64_t records = 0;
    assert(SessionSupervisorAuditJournal::Verify(
        displacedAuditPath, records, reason));
    assert(records == 10);
    const std::string audited = AuditPayloads(displacedAuditPath);
    assert(audited.find("phase=intent") != std::string::npos);
    assert(audited.find("reason_code=AGENT_RISK_REDUCTION_RATE_LIMIT") !=
        std::string::npos);
    assert(audited.find(binding.token) == std::string::npos);
    std::remove(socketPath.c_str());
    std::remove(auditPath.c_str());
    std::remove(displacedAuditPath.c_str());
    std::remove(journalPath.c_str());
}

void TestActivatedSocketSurvivesStop()
{
    const std::string journalPath =
        TempPath("/tmp/hepta-tool-activated-journal-XXXXXX");
    const std::string auditPath =
        TempPath("/tmp/hepta-tool-activated-audit-XXXXXX");
    const std::string socketPath =
        TempPath("/tmp/hepta-tool-activated-socket-XXXXXX");
    OmsJournal journal;
    assert(journal.Init(journalPath));
    ExecutionCoordinatorCallbacks executionCallbacks;
    ExecutionCoordinator execution(journal, executionCallbacks);
    TradingToolReadCallbacks reads;
    TradingToolTradeCallbacks trades;
    TradingToolRegistry registry(execution, reads, trades);
    DecisionLeaseManager leases;
    TradingToolHost host(registry, leases);
    SessionSupervisorAuditJournal audit;
    std::string reason;
    assert(audit.Init(auditPath, reason));

    const int managerFd = socket(AF_UNIX, SOCK_STREAM | SOCK_CLOEXEC, 0);
    assert(managerFd >= 0);
    sockaddr_un address;
    std::memset(&address, 0, sizeof(address));
    address.sun_family = AF_UNIX;
    std::strncpy(address.sun_path, socketPath.c_str(),
        sizeof(address.sun_path) - 1);
    assert(bind(managerFd, reinterpret_cast<sockaddr*>(&address),
        sizeof(address)) == 0);
    assert(listen(managerFd, 8) == 0);

    UnixToolServer server(host);
    server.SetDecisionAuditJournal(&audit);
    assert(server.StartFromFd(dup(managerFd), reason,
        4096, 1000, 1, 8, 1, 4, 1));
    server.Stop();

    const int restartProbe = socket(AF_UNIX, SOCK_STREAM | SOCK_CLOEXEC, 0);
    assert(restartProbe >= 0);
    assert(connect(restartProbe, reinterpret_cast<sockaddr*>(&address),
        sizeof(address)) == 0);
    close(restartProbe);
    close(managerFd);
    unlink(socketPath.c_str());
    std::remove(auditPath.c_str());
    std::remove(journalPath.c_str());
}

void TestActivatedSocketSurvivesDrain()
{
    const std::string journalPath =
        TempPath("/tmp/hepta-tool-activated-drain-journal-XXXXXX");
    const std::string socketPath =
        TempPath("/tmp/hepta-tool-activated-drain-socket-XXXXXX");
    OmsJournal journal;
    assert(journal.Init(journalPath));
    ExecutionCoordinatorCallbacks callbacks;
    ExecutionCoordinator execution(journal, callbacks);
    TradingToolRegistry registry(execution);
    TradingToolHost host(registry);
    const int managerFd = socket(AF_UNIX, SOCK_STREAM | SOCK_CLOEXEC, 0);
    assert(managerFd >= 0);
    sockaddr_un address;
    std::memset(&address, 0, sizeof(address));
    address.sun_family = AF_UNIX;
    std::strncpy(address.sun_path, socketPath.c_str(),
        sizeof(address.sun_path) - 1);
    assert(bind(managerFd, reinterpret_cast<sockaddr*>(&address),
        sizeof(address)) == 0);
    assert(listen(managerFd, 8) == 0);

    UnixToolServer server(host);
    server.AllowMissingDecisionAuditForTests();
    std::string reason;
    assert(server.StartFromFd(dup(managerFd), reason,
        4096, 1000, 1, 8, 1, 4, 1));
    assert(server.Drain(1000));

    // Drain must not shutdown an activated socket object owned by the
    // manager.  The original descriptor remains usable for a restart probe.
    const int restartProbe = socket(AF_UNIX, SOCK_STREAM | SOCK_CLOEXEC, 0);
    assert(restartProbe >= 0);
    assert(connect(restartProbe, reinterpret_cast<sockaddr*>(&address),
        sizeof(address)) == 0);
    close(restartProbe);
    close(managerFd);
    unlink(socketPath.c_str());
    std::remove(journalPath.c_str());
}

void TestExistingSocketIsNeverUnlinked()
{
    const std::string journalPath =
        TempPath("/tmp/hepta-tool-existing-journal-XXXXXX");
    const std::string socketPath =
        TempPath("/tmp/hepta-tool-existing-socket-XXXXXX");
    OmsJournal journal;
    assert(journal.Init(journalPath));
    ExecutionCoordinatorCallbacks callbacks;
    ExecutionCoordinator execution(journal, callbacks);
    TradingToolRegistry registry(execution);
    TradingToolHost host(registry);

    const int owner = socket(AF_UNIX, SOCK_STREAM | SOCK_CLOEXEC, 0);
    assert(owner >= 0);
    sockaddr_un address;
    std::memset(&address, 0, sizeof(address));
    address.sun_family = AF_UNIX;
    std::strncpy(address.sun_path, socketPath.c_str(),
        sizeof(address.sun_path) - 1);
    assert(bind(owner, reinterpret_cast<sockaddr*>(&address),
        sizeof(address)) == 0);
    assert(listen(owner, 1) == 0);

    UnixToolServer server(host);
    std::string reason;
    assert(!server.Start(socketPath, reason, 4096, 1000, 1, 8, 1, 4, 1));
    assert(reason ==
        "socket path already exists; use activated fd or owner cleanup");
    struct stat preserved;
    assert(lstat(socketPath.c_str(), &preserved) == 0);
    assert(S_ISSOCK(preserved.st_mode));

    close(owner);
    unlink(socketPath.c_str());
    std::remove(journalPath.c_str());
}

void TestStopDoesNotUnlinkReplacedSocket()
{
    const std::string journalPath =
        TempPath("/tmp/hepta-tool-replaced-journal-XXXXXX");
    const std::string socketPath =
        TempPath("/tmp/hepta-tool-replaced-socket-XXXXXX");
    const std::string displacedPath =
        TempPath("/tmp/hepta-tool-replaced-displaced-XXXXXX");
    OmsJournal journal;
    assert(journal.Init(journalPath));
    ExecutionCoordinatorCallbacks callbacks;
    ExecutionCoordinator execution(journal, callbacks);
    TradingToolRegistry registry(execution);
    TradingToolHost host(registry);
    UnixToolServer server(host);
    server.AllowMissingDecisionAuditForTests();
    std::string reason;
    assert(server.Start(socketPath, reason, 4096, 1000, 1, 8, 1, 4, 1));
    struct stat socketMetadata;
    assert(lstat(socketPath.c_str(), &socketMetadata) == 0);
    assert((socketMetadata.st_mode & 0777) == 0600);

    // Simulate a restart/custodian replacing the rendezvous pathname while
    // the original listener still owns its open socket description.
    assert(rename(socketPath.c_str(), displacedPath.c_str()) == 0);
    const int replacement = socket(AF_UNIX, SOCK_STREAM | SOCK_CLOEXEC, 0);
    assert(replacement >= 0);
    sockaddr_un address;
    std::memset(&address, 0, sizeof(address));
    address.sun_family = AF_UNIX;
    std::strncpy(address.sun_path, socketPath.c_str(),
        sizeof(address.sun_path) - 1);
    assert(bind(replacement, reinterpret_cast<sockaddr*>(&address),
        sizeof(address)) == 0);
    assert(listen(replacement, 1) == 0);

    server.Stop();
    struct stat replacementPath;
    assert(lstat(socketPath.c_str(), &replacementPath) == 0);
    assert(S_ISSOCK(replacementPath.st_mode));

    close(replacement);
    unlink(socketPath.c_str());
    unlink(displacedPath.c_str());
    std::remove(journalPath.c_str());
}

void TestStartValidationFailureCleansSocket()
{
    const std::string journalPath =
        TempPath("/tmp/hepta-tool-start-fail-journal-XXXXXX");
    const std::string socketPath =
        TempPath("/tmp/hepta-tool-start-fail-socket-XXXXXX");
    OmsJournal journal;
    assert(journal.Init(journalPath));
    ExecutionCoordinatorCallbacks callbacks;
    ExecutionCoordinator execution(journal, callbacks);
    TradingToolRegistry registry(execution);
    TradingToolHost host(registry);
    UnixToolServer server(host);
    server.AllowMissingDecisionAuditForTests();
    std::string reason;
    // The path is bound before Activate() validates limits.  A rejected
    // activation must close that descriptor and remove only our new dentry.
    assert(!server.Start(socketPath, reason, 64, 1000, 1, 8, 1, 4, 1));
    assert(reason == "invalid server limits");
    assert(access(socketPath.c_str(), F_OK) != 0);
    assert(!server.IsRunning());
    std::remove(journalPath.c_str());
}

} // namespace

int main()
{
    TestSocketRoundTripAndStrictProtocol();
    TestGlobalQueueBackpressureDecisionAudit();
    TestWatchRejectionAndDescriptorEffectAudit();
    TestWatchSnapshotAuditCardinalityAndBoundedDelivery();
    TestMutationOutcomeAuditFailureIsUncertain();
    TestStopPreservesDurableMutationIntent();
    TestOwnerRoundRobin();
    TestDeadlineCancellationAndDrain();
    TestMutationRateLimitDecisionAudit();
    TestActivatedSocketSurvivesStop();
    TestActivatedSocketSurvivesDrain();
    TestExistingSocketIsNeverUnlinked();
    TestStopDoesNotUnlinkReplacedSocket();
    TestStartValidationFailureCleansSocket();
    std::cout << "unix_tool_server_tests: PASS" << std::endl;
    return 0;
}
