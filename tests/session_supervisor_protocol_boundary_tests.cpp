#include "../HeptaTrade/tool_host/session_supervisor_protocol.h"

#include <arpa/inet.h>
#include <cassert>
#include <cstdint>
#include <cstring>
#include <iostream>
#include <limits>
#include <string>

namespace {

void AppendField(std::string& body, std::uint16_t id,
	const std::string& value)
{
	const std::uint16_t networkId = htons(id);
	const std::uint32_t networkLength = htonl(
		static_cast<std::uint32_t>(value.size()));
	body.append(reinterpret_cast<const char*>(&networkId), sizeof(networkId));
	body.append(reinterpret_cast<const char*>(&networkLength),
		sizeof(networkLength));
	body.append(value);
}

std::string RewriteField(const std::string& input, std::uint16_t target,
	const std::string& replacement)
{
	assert(input.size() >= 4);
	std::string output = input.substr(0, 4);
	std::size_t offset = 4;
	bool replaced = false;
	while (offset < input.size())
	{
		assert(input.size() - offset >= 6);
		std::uint16_t networkId = 0;
		std::uint32_t networkLength = 0;
		std::memcpy(&networkId, input.data() + offset, sizeof(networkId));
		offset += sizeof(networkId);
		std::memcpy(&networkLength, input.data() + offset,
			sizeof(networkLength));
		offset += sizeof(networkLength);
		const std::uint16_t id = ntohs(networkId);
		const std::uint32_t length = ntohl(networkLength);
		assert(length <= input.size() - offset);
		const std::string value = input.substr(offset, length);
		AppendField(output, id, id == target ? replacement : value);
		replaced = replaced || id == target;
		offset += length;
	}
	assert(replaced);
	return output;
}

SessionSupervisorResult ValidRecoveryResult()
{
	SessionSupervisorResult result;
	result.ReasonCode() = "RECOVERY_QUERY_PROVEN_RECOVERY_ONLY";
	result.leaseGeneration = 7;
	result.TargetCommandId() = "command-7";
	result.authoritativeCommandStatus = true;
	result.CommandStatus() = "accepted";
	result.CommandReasonCode() = "NONE";
	result.orderId = -1;
	result.recoveryOnly = true;
	result.ownerFenced = false;
	result.ExecutionServiceEpoch() = "epoch-7";
	result.executionServiceFencingGeneration = 1;
	result.recoveryExpiresAtMs = 1000;
	result.ownerAuditAuthoritative = true;
	result.ownerAuditComplete = true;
	result.brokerConnectionEpoch = 1;
	result.brokerActiveGeneration = 1;
	result.brokerTerminalGeneration = 1;
	result.OwnerAccount() = "account";
	result.OwnerExecutionDomain() = "paper";
	return result;
}

SessionSupervisorResult ValidFinalizationResult()
{
	SessionSupervisorResult result;
	result.ReasonCode() = "PAPER_FINALIZATION_GROUP_PENDING";
	result.paperFinalizationRequired = true;
	result.leaseGeneration = 7;
	result.PaperFinalizationState() = "NONE";
	result.RecoveryId() = "recovery-7";
	result.FinalizationId() = "finalization-7";
	result.ExpectedOwnerSetSha256() = "sha256:" + std::string(64, 'a');
	result.expectedOwnerCount = 1;
	result.OwnerTokenSha256() = "sha256:" + std::string(64, 'b');
	result.OwnerAccount() = "account";
	result.OwnerExecutionDomain() = "paper";
	result.ExecutionServiceEpoch() = "epoch-7";
	return result;
}

void TestRequestBounds()
{
	SessionSupervisorRequest request;
	request.operation = SessionSupervisorOperation::Provision;
	request.token = "token";
	request.templateId = "paper";
	request.agentId = "agent";
	request.sessionId = "session";
	request.ttlMs = 86400001;
	std::string body = "stale-frame";
	std::string reason;
	assert(!SessionSupervisorProtocol::EncodeRequest(request, body, reason));
	assert(reason == "SUPERVISOR_INVALID_PROVISION_REQUEST");
	assert(body.empty());
	request.ttlMs = 1000;
	request.token = "\xE2\x80\x8B"; // ZERO WIDTH SPACE
	assert(!SessionSupervisorProtocol::EncodeRequest(request, body, reason));
	assert(reason == "SUPERVISOR_INVALID_TOKEN");
	assert(body.empty());

	body.assign("HSS1", 4);
	for (std::uint16_t id = 1000; id < 1129; ++id)
		AppendField(body, id, "x");
	SessionSupervisorRequest decoded;
	assert(!SessionSupervisorProtocol::DecodeRequest(body, decoded, reason));
	assert(reason == "SUPERVISOR_SCHEMA_TOO_MANY_FIELDS");

	body.assign("HSS1", 4);
	body.append(65537, 'x');
	assert(!SessionSupervisorProtocol::DecodeRequest(body, decoded, reason));
	assert(reason == "SUPERVISOR_SCHEMA_BODY_TOO_LARGE");
}

void TestResultBoundaries()
{
	std::string reason;
	SessionSupervisorResult result = ValidRecoveryResult();
	const std::string encoded = SessionSupervisorProtocol::EncodeResult(result);
	SessionSupervisorResult decoded;
	assert(SessionSupervisorProtocol::DecodeResult(encoded, decoded, reason));
	assert(decoded.TargetCommandId() == "command-7");

	assert(!SessionSupervisorProtocol::DecodeResult(
		RewriteField(encoded, 15, "bogus"), decoded, reason));
	assert(!SessionSupervisorProtocol::DecodeResult(
		RewriteField(encoded, 30, std::string("bad\0id", 6)), decoded,
		reason));
	assert(!SessionSupervisorProtocol::DecodeResult(
		RewriteField(encoded, 9, std::string(513, 'r')), decoded, reason));

	result = ValidFinalizationResult();
	const std::string finalization = SessionSupervisorProtocol::EncodeResult(result);
	assert(SessionSupervisorProtocol::DecodeResult(finalization, decoded, reason));
	assert(!SessionSupervisorProtocol::DecodeResult(
		RewriteField(finalization, 51, "nan"), decoded, reason));
	assert(!SessionSupervisorProtocol::DecodeResult(
		RewriteField(finalization, 30, std::string("account\0x", 9)),
		decoded, reason));
	result.accepted = true;
	const std::string normalized = SessionSupervisorProtocol::EncodeResult(result);
	assert(SessionSupervisorProtocol::DecodeResult(normalized, decoded, reason));
	assert(!decoded.accepted);

	result = SessionSupervisorResult();
	result.ReasonCode().assign(70000, 'r');
	assert(SessionSupervisorProtocol::EncodeResult(result).empty());
}

} // namespace

int main()
{
	TestRequestBounds();
	TestResultBoundaries();
	std::cout << "session_supervisor_protocol_boundary_tests: PASS\n";
	return 0;
}
