#include "session_supervisor_protocol.h"

#include <arpa/inet.h>
#include <cerrno>
#include <cstdlib>
#include <cstring>
#include <limits>
#include <map>

namespace {

const char kMagic[] = {'H', 'S', 'S', '1'};

enum FieldId
{
	Operation = 1,
	TemplateId = 2,
	Token = 3,
	AgentId = 4,
	SessionId = 5,
	PeerUid = 6,
	TtlMs = 7,
	Accepted = 8,
	ReasonCode = 9
	, ExpectedGeneration = 10
	, ReplacementToken = 11
	, LeaseGeneration = 12
	, TargetCommandId = 13
	, AuthoritativeCommandStatus = 14
	, CommandStatus = 15
	, OrderId = 16
	, RecoveryOnly = 17
	, OwnerFenced = 18
	, ExecutionServiceEpoch = 19
	, ExecutionServiceFencingGeneration = 20
	, CommandReasonCode = 21
	, RecoveryExpiresAtMs = 22
	, OwnerAuditAuthoritative = 23
	, OwnerAuditComplete = 24
	, OwnerActiveOrderCount = 25
	, OwnerUncertainCommandCount = 26
	, BrokerConnectionEpoch = 27
	, BrokerActiveGeneration = 28
	, BrokerTerminalGeneration = 29
	, OwnerAccount = 30
	, OwnerExecutionDomain = 31
	, RecoveryId = 32
	, FinalizationId = 33
	, ExpectedOwnerSetSha256 = 34
	, ExpectedOwnerCount = 35
	, ReceiptSha256 = 36
	, PaperFinalizationState = 37
	, OwnerTokenSha256 = 38
	, FinalizationReceipt = 39
	, BrokerRiskGeneration = 40
	, BrokerAccountGeneration = 41
	, BrokerPositionGeneration = 42
	, BrokerFxCashGeneration = 43
	, BrokerExposureGeneration = 44
	, BrokerTerminalExposureGeneration = 45
	, BrokerRiskAbsorbedExposureGeneration = 46
	, BrokerGlobalActiveOrderCount = 47
	, BrokerPostFillRiskReconciliationPending = 48
	, BrokerRecoveryAuditBarrierComplete = 49
	, BrokerRecoveryAuditNewConnectionEpochRequired = 50
	, BrokerPositionQuantity = 51
	, BrokerGrossAbsolutePosition = 52
	, PaperFinalizationRequired = 53
	, PreliminaryFinalizationReceiptSha256 = 54
	, TerminalizationServiceEpoch = 55
	, TerminalizationServiceFencingGeneration = 56
	, TerminalizationGeneration = 57
	, TerminalLatchSha256 = 58
	, TerminalMutationGateClosed = 59
	, TerminalBrokerTransportConnected = 60
	, TerminalBrokerEventIngressHalted = 61
	, TerminalBrokerCallbackQueueDrained = 62
	, TerminalBrokerCallbacksInFlight = 63
	, TerminalBrokerReconnectPermitted = 64
	, TerminalLatchDurable = 65
	, TerminalRuntimeLatchLoaded = 66
	, TerminalRuntimeVerified = 67
	, TerminalReplay = 68
	, TerminalEvidenceSha256 = 69
	, TerminalEvidence = 70
	, TerminalProofKind = 71
	, TerminalExternalLatchSha256 = 72
	, TransportCutoffReceiptFileSha256 = 73
	, TransportCutoffReceiptBodySha256 = 74
	, PostCutoffTerminalWitnessFileSha256 = 75
	, PostCutoffTerminalWitnessBodySha256 = 76
	, TerminalEvidenceBodySha256 = 77
	, EgressPolicySha256 = 78
	, ProviderTrustPolicyBodySha256 = 79
	, SignedAccountSignatureSha256 = 80
	, TerminalExternalLatchLoaded = 81
	, TerminalCurrentEvidenceVerified = 82
	, EgressPublisherPid = 85
	, EgressPublisherStartTicks = 86
};

void AppendField(std::string& body, std::uint16_t id, const std::string& value)
{
	const std::uint16_t networkId = htons(id);
	const std::uint32_t networkLength = htonl(static_cast<std::uint32_t>(value.size()));
	body.append(reinterpret_cast<const char*>(&networkId), sizeof(networkId));
	body.append(reinterpret_cast<const char*>(&networkLength), sizeof(networkLength));
	body.append(value);
}

bool DecodeFields(const std::string& body, std::map<std::uint16_t, std::string>& fields,
	std::string& reason)
{
	if (body.size() < sizeof(kMagic) || std::memcmp(body.data(), kMagic, sizeof(kMagic)) != 0)
	{
		reason = "SUPERVISOR_SCHEMA_MAGIC_MISMATCH";
		return false;
	}
	std::size_t offset = sizeof(kMagic);
	while (offset < body.size())
	{
		if (body.size() - offset < sizeof(std::uint16_t) + sizeof(std::uint32_t))
		{
			reason = "SUPERVISOR_SCHEMA_TRUNCATED_FIELD";
			return false;
		}
		std::uint16_t networkId = 0;
		std::uint32_t networkLength = 0;
		std::memcpy(&networkId, body.data() + offset, sizeof(networkId));
		offset += sizeof(networkId);
		std::memcpy(&networkLength, body.data() + offset, sizeof(networkLength));
		offset += sizeof(networkLength);
		const std::uint16_t id = ntohs(networkId);
		const std::uint32_t length = ntohl(networkLength);
		const std::uint32_t maximumLength =
			(id == TerminalEvidence || id == FinalizationReceipt) ?
				12288U : 4096U;
		if (length > maximumLength || length > body.size() - offset)
		{
			reason = "SUPERVISOR_SCHEMA_INVALID_FIELD_LENGTH";
			return false;
		}
		if (fields.find(id) != fields.end())
		{
			reason = "SUPERVISOR_SCHEMA_DUPLICATE_FIELD";
			return false;
		}
		fields[id] = body.substr(offset, length);
		offset += length;
	}
	return true;
}

bool ParseUnsigned(const std::string& value, std::uint64_t maximum,
	std::uint64_t& parsed)
{
	if (value.empty()) return false;
	char* end = nullptr;
	errno = 0;
	const unsigned long long number = std::strtoull(value.c_str(), &end, 10);
	if (errno != 0 || end == value.c_str() || *end != '\0' || number > maximum)
		return false;
	parsed = static_cast<std::uint64_t>(number);
	return true;
}

bool Require(const std::map<std::uint16_t, std::string>& fields,
	std::uint16_t id, std::string& value, std::string& reason)
{
	const std::map<std::uint16_t, std::string>::const_iterator found = fields.find(id);
	if (found == fields.end() || found->second.empty())
	{
		reason = "SUPERVISOR_SCHEMA_MISSING_REQUIRED_FIELD";
		return false;
	}
	value = found->second;
	return true;
}

bool ValidateText(const std::string& value, std::size_t maximum)
{
	if (value.empty() || value.size() > maximum) return false;
	for (std::size_t i = 0; i < value.size(); ++i)
		if (static_cast<unsigned char>(value[i]) < 0x20) return false;
	return true;
}

bool ValidateSha256(const std::string& value)
{
	if (value.size() != 71 || value.compare(0, 7, "sha256:") != 0)
		return false;
	for (std::size_t i = 7; i < value.size(); ++i)
		if ((value[i] < '0' || value[i] > '9') &&
			(value[i] < 'a' || value[i] > 'f'))
			return false;
	return true;
}

bool ParseSignedLong(const std::string& value, long& parsed)
{
	if (value.empty()) return false;
	char* end = nullptr;
	errno = 0;
	const long number = std::strtol(value.c_str(), &end, 10);
	if (errno != 0 || end == value.c_str() || *end != '\0') return false;
	parsed = number;
	return true;
}

} // namespace

bool SessionSupervisorProtocol::EncodeRequest(
	const SessionSupervisorRequest& request, std::string& body, std::string& reason)
{
	body.assign(kMagic, sizeof(kMagic));
	if (!ValidateText(request.token, 512))
	{
		reason = "SUPERVISOR_INVALID_TOKEN";
		return false;
	}
	std::string operation;
	if (request.operation == SessionSupervisorOperation::Provision)
		operation = "provision";
	else if (request.operation == SessionSupervisorOperation::Revoke)
		operation = "revoke";
	else if (request.operation == SessionSupervisorOperation::Renew)
		operation = "renew";
	else if (request.operation == SessionSupervisorOperation::Rotate)
		operation = "rotate";
	else if (request.operation == SessionSupervisorOperation::RecoveryQuery)
		operation = "recovery-query";
	else if (request.operation == SessionSupervisorOperation::PaperFinalize)
		operation = "paper-finalize";
	else if (request.operation == SessionSupervisorOperation::PaperFinalizeAck)
		operation = "paper-finalize-ack";
	else if (request.operation ==
		SessionSupervisorOperation::PaperTerminalizeAck)
		operation = "paper-terminalize-ack";
	else if (request.operation ==
		SessionSupervisorOperation::PaperTerminalWitnessPrepare)
		operation = "paper-terminal-witness-prepare";
	else if (request.operation ==
		SessionSupervisorOperation::PaperTerminalWitnessAck)
		operation = "paper-terminal-witness-ack";
	else
	{
		reason = "SUPERVISOR_INVALID_OPERATION";
		return false;
	}
	AppendField(body, Operation, operation);
	AppendField(body, Token, request.token);
	if (request.operation == SessionSupervisorOperation::Provision)
	{
		if (!ValidateText(request.templateId, 32) || !ValidateText(request.agentId, 128) ||
			!ValidateText(request.sessionId, 256) || request.ttlMs == 0)
		{
			reason = "SUPERVISOR_INVALID_PROVISION_REQUEST";
			return false;
		}
		AppendField(body, TemplateId, request.templateId);
		AppendField(body, AgentId, request.agentId);
		AppendField(body, SessionId, request.sessionId);
		AppendField(body, PeerUid, std::to_string(request.peerUid));
		AppendField(body, TtlMs, std::to_string(request.ttlMs));
	}
	else if (request.operation == SessionSupervisorOperation::Revoke ||
		request.operation == SessionSupervisorOperation::RecoveryQuery)
	{
		if (request.expectedGeneration == 0 ||
			(request.operation == SessionSupervisorOperation::RecoveryQuery &&
			 !ValidateText(request.targetCommandId, 128)))
		{
			reason = request.operation == SessionSupervisorOperation::Revoke ?
				"SUPERVISOR_INVALID_REVOKE_REQUEST" :
				"SUPERVISOR_INVALID_RECOVERY_QUERY_REQUEST";
			return false;
		}
		AppendField(body, ExpectedGeneration, std::to_string(request.expectedGeneration));
		if (request.operation == SessionSupervisorOperation::RecoveryQuery)
		{
			AppendField(body, TargetCommandId, request.targetCommandId);
			if (request.requirePaperFinalization)
				AppendField(body, PaperFinalizationRequired, "1");
		}
	}
	else if (request.operation == SessionSupervisorOperation::Renew ||
		request.operation == SessionSupervisorOperation::Rotate)
	{
		if (request.ttlMs == 0 || request.expectedGeneration == 0 ||
			(request.operation == SessionSupervisorOperation::Rotate &&
			 !ValidateText(request.replacementToken, 512)))
		{
			reason = "SUPERVISOR_INVALID_LEASE_REQUEST";
			return false;
		}
		AppendField(body, TtlMs, std::to_string(request.ttlMs));
		AppendField(body, ExpectedGeneration, std::to_string(request.expectedGeneration));
		if (request.operation == SessionSupervisorOperation::Rotate)
			AppendField(body, ReplacementToken, request.replacementToken);
	}
	else if (request.operation == SessionSupervisorOperation::PaperFinalize ||
		request.operation == SessionSupervisorOperation::PaperFinalizeAck ||
		request.operation ==
			SessionSupervisorOperation::PaperTerminalizeAck ||
		request.operation ==
			SessionSupervisorOperation::PaperTerminalWitnessPrepare ||
		request.operation ==
			SessionSupervisorOperation::PaperTerminalWitnessAck)
	{
		if (request.expectedGeneration == 0 ||
			!ValidateText(request.recoveryId, 128) ||
			!ValidateText(request.finalizationId, 128) ||
			!ValidateSha256(request.expectedOwnerSetSha256) ||
			request.expectedOwnerCount == 0 ||
			request.expectedOwnerCount > 4096 ||
			 ((request.operation ==
					SessionSupervisorOperation::PaperFinalizeAck ||
			  request.operation ==
					SessionSupervisorOperation::PaperTerminalizeAck ||
			  request.operation ==
					SessionSupervisorOperation::PaperTerminalWitnessPrepare ||
			  request.operation ==
					SessionSupervisorOperation::PaperTerminalWitnessAck) &&
			 !ValidateSha256(request.receiptSha256)) ||
			(request.operation ==
				SessionSupervisorOperation::PaperTerminalWitnessAck &&
			 (!ValidateSha256(request.terminalEvidenceSha256) ||
			  request.terminalEvidence.empty() ||
			  request.terminalEvidence.size() > 12288)) ||
			(request.operation !=
				SessionSupervisorOperation::PaperTerminalWitnessAck &&
			 (!request.terminalEvidenceSha256.empty() ||
			  !request.terminalEvidence.empty())) ||
			(request.operation == SessionSupervisorOperation::PaperFinalize &&
			 !request.receiptSha256.empty()))
		{
			reason = "SUPERVISOR_INVALID_PAPER_FINALIZATION_REQUEST";
			return false;
		}
		AppendField(body, ExpectedGeneration,
			std::to_string(request.expectedGeneration));
		AppendField(body, RecoveryId, request.recoveryId);
		AppendField(body, FinalizationId, request.finalizationId);
		AppendField(body, ExpectedOwnerSetSha256,
			request.expectedOwnerSetSha256);
		AppendField(body, ExpectedOwnerCount,
			std::to_string(request.expectedOwnerCount));
		if (request.operation == SessionSupervisorOperation::PaperFinalizeAck ||
			request.operation ==
				SessionSupervisorOperation::PaperTerminalizeAck ||
			request.operation ==
				SessionSupervisorOperation::PaperTerminalWitnessPrepare ||
			request.operation ==
				SessionSupervisorOperation::PaperTerminalWitnessAck)
			AppendField(body, ReceiptSha256, request.receiptSha256);
		if (request.operation ==
			SessionSupervisorOperation::PaperTerminalWitnessAck)
		{
			AppendField(body, TerminalEvidenceSha256,
				request.terminalEvidenceSha256);
			AppendField(body, TerminalEvidence, request.terminalEvidence);
		}
	}
	reason.clear();
	return true;
}

bool SessionSupervisorProtocol::DecodeRequest(
	const std::string& body, SessionSupervisorRequest& request, std::string& reason)
{
	request.operation = SessionSupervisorOperation::Provision;
	request.templateId.clear();
	request.token.clear();
	request.replacementToken.clear();
	request.agentId.clear();
	request.sessionId.clear();
	request.peerUid = 0;
	request.ttlMs = 0;
	request.expectedGeneration = 0;
	request.targetCommandId.clear();
	request.requirePaperFinalization = false;
	request.recoveryId.clear();
	request.finalizationId.clear();
	request.expectedOwnerSetSha256.clear();
	request.expectedOwnerCount = 0;
	request.receiptSha256.clear();
	request.terminalEvidenceSha256.clear();
	request.terminalEvidence.clear();
	std::map<std::uint16_t, std::string> fields;
	if (!DecodeFields(body, fields, reason)) return false;
	for (std::map<std::uint16_t, std::string>::const_iterator it = fields.begin();
		 it != fields.end(); ++it)
	{
		if (it->first != Operation && it->first != TemplateId && it->first != Token &&
			it->first != AgentId && it->first != SessionId && it->first != PeerUid &&
			it->first != TtlMs && it->first != ExpectedGeneration &&
			it->first != ReplacementToken && it->first != TargetCommandId &&
			it->first != RecoveryId && it->first != FinalizationId &&
			it->first != ExpectedOwnerSetSha256 &&
			it->first != ExpectedOwnerCount && it->first != ReceiptSha256 &&
			it->first != PaperFinalizationRequired &&
			it->first != TerminalEvidenceSha256 &&
			it->first != TerminalEvidence)
		{
			reason = "SUPERVISOR_SCHEMA_UNEXPECTED_FIELD";
			return false;
		}
	}
	std::string operation;
	if (!Require(fields, Operation, operation, reason) ||
		!Require(fields, Token, request.token, reason) ||
		!ValidateText(request.token, 512)) return false;
	if (operation == "revoke")
	{
		if (fields.size() != 3)
		{
			reason = "SUPERVISOR_SCHEMA_UNEXPECTED_FIELD";
			return false;
		}
		request.operation = SessionSupervisorOperation::Revoke;
		request.token = fields.find(Token)->second;
		std::string generation;
		if (!Require(fields, ExpectedGeneration, generation, reason) ||
			!ParseUnsigned(generation, std::numeric_limits<std::uint64_t>::max(),
				request.expectedGeneration) || request.expectedGeneration == 0)
		{
			reason = "SUPERVISOR_INVALID_NUMERIC_FIELD";
			return false;
		}
		reason.clear();
		return true;
	}
	if (operation == "recovery-query")
	{
		if (fields.size() != 4 && fields.size() != 5)
		{
			reason = "SUPERVISOR_SCHEMA_UNEXPECTED_FIELD";
			return false;
		}
		request.operation = SessionSupervisorOperation::RecoveryQuery;
		request.token = fields.find(Token)->second;
		std::string generation;
		const std::map<std::uint16_t, std::string>::const_iterator
			finalizationRequired = fields.find(PaperFinalizationRequired);
		if (!Require(fields, ExpectedGeneration, generation, reason) ||
			!Require(fields, TargetCommandId, request.targetCommandId, reason) ||
			!ValidateText(request.targetCommandId, 128) ||
			(finalizationRequired != fields.end() &&
			 finalizationRequired->second != "1") ||
			!ParseUnsigned(generation, std::numeric_limits<std::uint64_t>::max(),
				request.expectedGeneration) || request.expectedGeneration == 0)
		{
			reason = "SUPERVISOR_INVALID_RECOVERY_QUERY_REQUEST";
			return false;
		}
		request.requirePaperFinalization =
			finalizationRequired != fields.end();
		reason.clear();
		return true;
	}
	if (operation == "paper-finalize" ||
		operation == "paper-finalize-ack" ||
		operation == "paper-terminalize-ack" ||
		operation == "paper-terminal-witness-prepare" ||
		operation == "paper-terminal-witness-ack")
	{
		const bool acknowledgement =
			operation == "paper-finalize-ack" ||
			operation == "paper-terminalize-ack" ||
			operation == "paper-terminal-witness-prepare" ||
			operation == "paper-terminal-witness-ack";
		const bool externalWitness =
			operation == "paper-terminal-witness-ack";
		if (fields.size() != (externalWitness ? 10U :
			(acknowledgement ? 8U : 7U)))
		{
			reason = "SUPERVISOR_SCHEMA_UNEXPECTED_FIELD";
			return false;
		}
		request.operation = externalWitness ?
			SessionSupervisorOperation::PaperTerminalWitnessAck :
			(operation == "paper-terminal-witness-prepare" ?
			SessionSupervisorOperation::PaperTerminalWitnessPrepare :
			(operation == "paper-terminalize-ack" ?
			SessionSupervisorOperation::PaperTerminalizeAck :
			(acknowledgement ?
				SessionSupervisorOperation::PaperFinalizeAck :
				SessionSupervisorOperation::PaperFinalize)));
		std::string generation;
		std::string ownerCount;
		if (!Require(fields, ExpectedGeneration, generation, reason) ||
			!Require(fields, RecoveryId, request.recoveryId, reason) ||
			!Require(fields, FinalizationId, request.finalizationId, reason) ||
			!Require(fields, ExpectedOwnerSetSha256,
				request.expectedOwnerSetSha256, reason) ||
			!Require(fields, ExpectedOwnerCount, ownerCount, reason) ||
			!ValidateText(request.recoveryId, 128) ||
			!ValidateText(request.finalizationId, 128) ||
			!ValidateSha256(request.expectedOwnerSetSha256) ||
			!ParseUnsigned(generation,
				std::numeric_limits<std::uint64_t>::max(),
				request.expectedGeneration) ||
			request.expectedGeneration == 0 ||
			!ParseUnsigned(ownerCount, 4096,
				request.expectedOwnerCount) ||
			request.expectedOwnerCount == 0 ||
			(acknowledgement &&
			 (!Require(fields, ReceiptSha256,
				request.receiptSha256, reason) ||
			  !ValidateSha256(request.receiptSha256))) ||
			(externalWitness &&
			 (!Require(fields, TerminalEvidenceSha256,
				request.terminalEvidenceSha256, reason) ||
			  !ValidateSha256(request.terminalEvidenceSha256) ||
			  !Require(fields, TerminalEvidence,
				request.terminalEvidence, reason) ||
			  request.terminalEvidence.size() > 12288)))
		{
			reason = "SUPERVISOR_INVALID_PAPER_FINALIZATION_REQUEST";
			return false;
		}
		reason.clear();
		return true;
	}
	if (operation == "renew" || operation == "rotate")
	{
		const std::size_t expectedFields = operation == "rotate" ? 5 : 4;
		if (fields.size() != expectedFields)
		{
			reason = "SUPERVISOR_SCHEMA_UNEXPECTED_FIELD";
			return false;
		}
		request.operation = operation == "rotate" ?
			SessionSupervisorOperation::Rotate : SessionSupervisorOperation::Renew;
		std::string ttlMs;
		std::string generation;
		if (!Require(fields, TtlMs, ttlMs, reason) ||
			!Require(fields, ExpectedGeneration, generation, reason) ||
			!ParseUnsigned(ttlMs, 86400000, request.ttlMs) || request.ttlMs == 0 ||
			!ParseUnsigned(generation, std::numeric_limits<std::uint64_t>::max(),
				request.expectedGeneration) || request.expectedGeneration == 0)
		{
			reason = "SUPERVISOR_INVALID_NUMERIC_FIELD";
			return false;
		}
		if (request.operation == SessionSupervisorOperation::Rotate &&
			(!Require(fields, ReplacementToken, request.replacementToken, reason) ||
			 !ValidateText(request.replacementToken, 512))) return false;
		reason.clear();
		return true;
	}
	if (operation != "provision" || fields.size() != 7)
	{
		reason = operation == "provision" ?
			"SUPERVISOR_SCHEMA_MISSING_REQUIRED_FIELD" : "SUPERVISOR_INVALID_OPERATION";
		return false;
	}
	request.operation = SessionSupervisorOperation::Provision;
	std::string peerUid;
	std::string ttlMs;
	if (!Require(fields, TemplateId, request.templateId, reason) ||
		!Require(fields, AgentId, request.agentId, reason) ||
		!Require(fields, SessionId, request.sessionId, reason) ||
		!Require(fields, PeerUid, peerUid, reason) ||
		!Require(fields, TtlMs, ttlMs, reason) ||
		!ValidateText(request.templateId, 32) || !ValidateText(request.agentId, 128) ||
		!ValidateText(request.sessionId, 256)) return false;
	std::uint64_t parsedPeerUid = 0;
	if (!ParseUnsigned(peerUid, std::numeric_limits<std::uint32_t>::max(), parsedPeerUid) ||
		!ParseUnsigned(ttlMs, 86400000, request.ttlMs) || request.ttlMs == 0)
	{
		reason = "SUPERVISOR_INVALID_NUMERIC_FIELD";
		return false;
	}
	request.peerUid = static_cast<std::uint32_t>(parsedPeerUid);
	reason.clear();
	return true;
}

std::string SessionSupervisorProtocol::EncodeResult(
	const SessionSupervisorResult& result)
{
	std::string body(kMagic, sizeof(kMagic));
	AppendField(body, Accepted, result.accepted ? "1" : "0");
	AppendField(body, ReasonCode, result.ReasonCode().empty() ? "OK" : result.ReasonCode());
	AppendField(body, LeaseGeneration, std::to_string(result.leaseGeneration));
	if (!result.TargetCommandId().empty())
	{
		AppendField(body, AuthoritativeCommandStatus,
			result.authoritativeCommandStatus ? "1" : "0");
		AppendField(body, TargetCommandId, result.TargetCommandId());
		AppendField(body, CommandStatus, result.CommandStatus());
		AppendField(body, CommandReasonCode,
			result.CommandReasonCode().empty() ? "unavailable" :
				result.CommandReasonCode());
		AppendField(body, OrderId, std::to_string(result.orderId));
		AppendField(body, RecoveryOnly, result.recoveryOnly ? "1" : "0");
		AppendField(body, OwnerFenced, result.ownerFenced ? "1" : "0");
		AppendField(body, ExecutionServiceEpoch, result.ExecutionServiceEpoch());
		AppendField(body, ExecutionServiceFencingGeneration,
			std::to_string(result.executionServiceFencingGeneration));
		AppendField(body, RecoveryExpiresAtMs,
			std::to_string(result.recoveryExpiresAtMs));
		AppendField(body, OwnerAuditAuthoritative,
			result.ownerAuditAuthoritative ? "1" : "0");
		AppendField(body, OwnerAuditComplete,
			result.ownerAuditComplete ? "1" : "0");
		AppendField(body, OwnerActiveOrderCount,
			std::to_string(result.ownerActiveOrderCount));
		AppendField(body, OwnerUncertainCommandCount,
			std::to_string(result.ownerUncertainCommandCount));
		AppendField(body, BrokerConnectionEpoch,
			std::to_string(result.brokerConnectionEpoch));
		AppendField(body, BrokerActiveGeneration,
			std::to_string(result.brokerActiveGeneration));
		AppendField(body, BrokerTerminalGeneration,
			std::to_string(result.brokerTerminalGeneration));
		AppendField(body, OwnerAccount, result.OwnerAccount());
		AppendField(body, OwnerExecutionDomain,
			result.OwnerExecutionDomain());
		AppendField(body, PaperFinalizationRequired,
			result.paperFinalizationRequired ? "1" : "0");
	}
	else if (!result.FinalizationId().empty())
	{
		AppendField(body, PaperFinalizationState,
			result.PaperFinalizationState());
		AppendField(body, RecoveryId, result.RecoveryId());
		AppendField(body, FinalizationId, result.FinalizationId());
		AppendField(body, ExpectedOwnerSetSha256,
			result.ExpectedOwnerSetSha256());
		AppendField(body, ExpectedOwnerCount,
			std::to_string(result.expectedOwnerCount));
		AppendField(body, OwnerTokenSha256, result.OwnerTokenSha256());
		AppendField(body, ReceiptSha256,
			result.FinalizationReceiptSha256());
		AppendField(body, FinalizationReceipt,
			result.FinalizationReceipt());
		AppendField(body, OwnerAuditAuthoritative,
			result.ownerAuditAuthoritative ? "1" : "0");
		AppendField(body, OwnerAuditComplete,
			result.ownerAuditComplete ? "1" : "0");
		AppendField(body, OwnerActiveOrderCount,
			std::to_string(result.ownerActiveOrderCount));
		AppendField(body, OwnerUncertainCommandCount,
			std::to_string(result.ownerUncertainCommandCount));
		AppendField(body, OwnerAccount, result.OwnerAccount());
		AppendField(body, OwnerExecutionDomain,
			result.OwnerExecutionDomain());
		AppendField(body, ExecutionServiceEpoch,
			result.ExecutionServiceEpoch());
		AppendField(body, ExecutionServiceFencingGeneration,
			std::to_string(result.executionServiceFencingGeneration));
		AppendField(body, BrokerConnectionEpoch,
			std::to_string(result.brokerConnectionEpoch));
		AppendField(body, BrokerActiveGeneration,
			std::to_string(result.brokerActiveGeneration));
		AppendField(body, BrokerTerminalGeneration,
			std::to_string(result.brokerTerminalGeneration));
		AppendField(body, BrokerRiskGeneration,
			std::to_string(result.brokerRiskGeneration));
		AppendField(body, BrokerAccountGeneration,
			std::to_string(result.brokerAccountGeneration));
		AppendField(body, BrokerPositionGeneration,
			std::to_string(result.brokerPositionGeneration));
		AppendField(body, BrokerFxCashGeneration,
			std::to_string(result.brokerFxCashGeneration));
		AppendField(body, BrokerExposureGeneration,
			std::to_string(result.brokerExposureGeneration));
		AppendField(body, BrokerTerminalExposureGeneration,
			std::to_string(result.brokerTerminalExposureGeneration));
		AppendField(body, BrokerRiskAbsorbedExposureGeneration,
			std::to_string(result.brokerRiskAbsorbedExposureGeneration));
		AppendField(body, BrokerGlobalActiveOrderCount,
			std::to_string(result.brokerGlobalActiveOrderCount));
		AppendField(body, BrokerPostFillRiskReconciliationPending,
			result.brokerPostFillRiskReconciliationPending ? "1" : "0");
		AppendField(body, BrokerRecoveryAuditBarrierComplete,
			result.brokerRecoveryAuditBarrierComplete ? "1" : "0");
		AppendField(body, BrokerRecoveryAuditNewConnectionEpochRequired,
			result.brokerRecoveryAuditNewConnectionEpochRequired ? "1" : "0");
		AppendField(body, BrokerPositionQuantity,
			result.BrokerPositionQuantity());
		AppendField(body, BrokerGrossAbsolutePosition,
			result.BrokerGrossAbsolutePosition());
		AppendField(body, PaperFinalizationRequired,
			result.paperFinalizationRequired ? "1" : "0");
		AppendField(body, PreliminaryFinalizationReceiptSha256,
			result.PreliminaryFinalizationReceiptSha256().empty() &&
				result.PaperFinalizationState() == "AUDIT_SEALED" ?
				result.FinalizationReceiptSha256() :
				result.PreliminaryFinalizationReceiptSha256());
		AppendField(body, TerminalizationServiceEpoch,
			result.TerminalizationServiceEpoch());
		AppendField(body, TerminalizationServiceFencingGeneration,
			std::to_string(
				result.terminalizationServiceFencingGeneration));
		AppendField(body, TerminalizationGeneration,
			std::to_string(result.terminalizationGeneration));
		AppendField(body, TerminalLatchSha256,
			result.TerminalLatchSha256());
		AppendField(body, TerminalMutationGateClosed,
			result.terminalMutationGateClosed ? "1" : "0");
		AppendField(body, TerminalBrokerTransportConnected,
			result.terminalBrokerTransportConnected ? "1" : "0");
		AppendField(body, TerminalBrokerEventIngressHalted,
			result.terminalBrokerEventIngressHalted ? "1" : "0");
		AppendField(body, TerminalBrokerCallbackQueueDrained,
			result.terminalBrokerCallbackQueueDrained ? "1" : "0");
		AppendField(body, TerminalBrokerCallbacksInFlight,
			std::to_string(result.terminalBrokerCallbacksInFlight));
		AppendField(body, TerminalBrokerReconnectPermitted,
			result.terminalBrokerReconnectPermitted ? "1" : "0");
		AppendField(body, TerminalLatchDurable,
			result.terminalLatchDurable ? "1" : "0");
		AppendField(body, TerminalRuntimeLatchLoaded,
			result.terminalRuntimeLatchLoaded ? "1" : "0");
		AppendField(body, TerminalRuntimeVerified,
			result.terminalRuntimeVerified ? "1" : "0");
		AppendField(body, TerminalReplay,
			result.terminalReplay ? "1" : "0");
		AppendField(body, TerminalProofKind,
			result.TerminalProofKind());
		AppendField(body, TerminalExternalLatchSha256,
			result.TerminalExternalLatchSha256());
		AppendField(body, TransportCutoffReceiptFileSha256,
			result.TransportCutoffReceiptFileSha256());
		AppendField(body, TransportCutoffReceiptBodySha256,
			result.TransportCutoffReceiptBodySha256());
		AppendField(body, PostCutoffTerminalWitnessFileSha256,
			result.PostCutoffTerminalWitnessFileSha256());
		AppendField(body, PostCutoffTerminalWitnessBodySha256,
			result.PostCutoffTerminalWitnessBodySha256());
		AppendField(body, TerminalEvidenceSha256,
			result.TerminalEvidenceSha256());
		AppendField(body, TerminalEvidenceBodySha256,
			result.TerminalEvidenceBodySha256());
		AppendField(body, EgressPolicySha256,
			result.EgressPolicySha256());
		AppendField(body, EgressPublisherPid,
			std::to_string(result.egressPublisherPid));
		AppendField(body, EgressPublisherStartTicks,
			std::to_string(result.egressPublisherStartTicks));
		AppendField(body, ProviderTrustPolicyBodySha256,
			result.ProviderTrustPolicyBodySha256());
		AppendField(body, SignedAccountSignatureSha256,
			result.SignedAccountSignatureSha256());
		AppendField(body, TerminalExternalLatchLoaded,
			result.terminalExternalLatchLoaded ? "1" : "0");
		AppendField(body, TerminalCurrentEvidenceVerified,
			result.terminalCurrentEvidenceVerified ? "1" : "0");
	}
	return body;
}

bool SessionSupervisorProtocol::DecodeResult(
	const std::string& body, SessionSupervisorResult& result, std::string& reason)
{
	std::map<std::uint16_t, std::string> fields;
	if (!DecodeFields(body, fields, reason)) return false;
	const bool recoveryResult = fields.size() == 23;
	const bool finalizationResult =
		fields.find(PaperFinalizationState) != fields.end();
	if ((finalizationResult && fields.size() != 66) ||
		(!finalizationResult && !recoveryResult && fields.size() != 3) ||
		fields.find(Accepted) == fields.end() ||
		fields.find(ReasonCode) == fields.end() ||
		fields.find(LeaseGeneration) == fields.end() ||
		(fields.find(Accepted)->second != "0" && fields.find(Accepted)->second != "1"))
	{
		reason = "SUPERVISOR_INVALID_RESULT";
		return false;
	}
	result.accepted = fields.find(Accepted)->second == "1";
	result.ReasonCode() = fields.find(ReasonCode)->second;
	if (!ParseUnsigned(fields.find(LeaseGeneration)->second,
		std::numeric_limits<std::uint64_t>::max(), result.leaseGeneration))
	{
		reason = "SUPERVISOR_INVALID_RESULT";
		return false;
	}
	if (recoveryResult && !finalizationResult)
	{
		const std::map<std::uint16_t, std::string>::const_iterator authoritative =
			fields.find(AuthoritativeCommandStatus);
		const std::map<std::uint16_t, std::string>::const_iterator target =
			fields.find(TargetCommandId);
		const std::map<std::uint16_t, std::string>::const_iterator status =
			fields.find(CommandStatus);
		const std::map<std::uint16_t, std::string>::const_iterator commandReason =
			fields.find(CommandReasonCode);
		const std::map<std::uint16_t, std::string>::const_iterator order =
			fields.find(OrderId);
		const std::map<std::uint16_t, std::string>::const_iterator recovery =
			fields.find(RecoveryOnly);
		const std::map<std::uint16_t, std::string>::const_iterator fenced =
			fields.find(OwnerFenced);
		const std::map<std::uint16_t, std::string>::const_iterator epoch =
			fields.find(ExecutionServiceEpoch);
		const std::map<std::uint16_t, std::string>::const_iterator generation =
			fields.find(ExecutionServiceFencingGeneration);
		const std::map<std::uint16_t, std::string>::const_iterator recoveryExpiry =
			fields.find(RecoveryExpiresAtMs);
		const std::map<std::uint16_t, std::string>::const_iterator auditAuthoritative =
			fields.find(OwnerAuditAuthoritative);
		const std::map<std::uint16_t, std::string>::const_iterator auditComplete =
			fields.find(OwnerAuditComplete);
		const std::map<std::uint16_t, std::string>::const_iterator activeCount =
			fields.find(OwnerActiveOrderCount);
		const std::map<std::uint16_t, std::string>::const_iterator uncertainCount =
			fields.find(OwnerUncertainCommandCount);
		const std::map<std::uint16_t, std::string>::const_iterator brokerEpoch =
			fields.find(BrokerConnectionEpoch);
		const std::map<std::uint16_t, std::string>::const_iterator activeGeneration =
			fields.find(BrokerActiveGeneration);
		const std::map<std::uint16_t, std::string>::const_iterator terminalGeneration =
			fields.find(BrokerTerminalGeneration);
		const std::map<std::uint16_t, std::string>::const_iterator ownerAccount =
			fields.find(OwnerAccount);
		const std::map<std::uint16_t, std::string>::const_iterator ownerDomain =
			fields.find(OwnerExecutionDomain);
		const std::map<std::uint16_t, std::string>::const_iterator
			finalizationRequired = fields.find(PaperFinalizationRequired);
		if (authoritative == fields.end() || target == fields.end() ||
			status == fields.end() || commandReason == fields.end() ||
			order == fields.end() ||
			recovery == fields.end() || fenced == fields.end() ||
			epoch == fields.end() || generation == fields.end() ||
			(authoritative->second != "0" && authoritative->second != "1") ||
			(recovery->second != "0" && recovery->second != "1") ||
			(fenced->second != "0" && fenced->second != "1") ||
			!ValidateText(target->second, 128) || status->second.empty() ||
			commandReason->second.empty() || commandReason->second.size() > 256 ||
			!ParseSignedLong(order->second, result.orderId) ||
			recoveryExpiry == fields.end() ||
			auditAuthoritative == fields.end() || auditComplete == fields.end() ||
			activeCount == fields.end() || uncertainCount == fields.end() ||
			brokerEpoch == fields.end() || activeGeneration == fields.end() ||
			terminalGeneration == fields.end() || ownerAccount == fields.end() ||
			ownerDomain == fields.end() ||
			finalizationRequired == fields.end() ||
			(finalizationRequired->second != "0" &&
			 finalizationRequired->second != "1") ||
			(auditAuthoritative->second != "0" &&
			 auditAuthoritative->second != "1") ||
			(auditComplete->second != "0" && auditComplete->second != "1") ||
			!ValidateText(ownerAccount->second, 128) ||
			!ValidateText(ownerDomain->second, 128) ||
			!ParseUnsigned(generation->second,
				std::numeric_limits<std::uint64_t>::max(),
				result.executionServiceFencingGeneration) ||
			!ParseUnsigned(recoveryExpiry->second,
				std::numeric_limits<std::uint64_t>::max(),
				result.recoveryExpiresAtMs) ||
			!ParseUnsigned(activeCount->second,
				std::numeric_limits<std::uint64_t>::max(),
				result.ownerActiveOrderCount) ||
			!ParseUnsigned(uncertainCount->second,
				std::numeric_limits<std::uint64_t>::max(),
				result.ownerUncertainCommandCount) ||
			!ParseUnsigned(brokerEpoch->second,
				std::numeric_limits<std::uint64_t>::max(),
				result.brokerConnectionEpoch) ||
			!ParseUnsigned(activeGeneration->second,
				std::numeric_limits<std::uint64_t>::max(),
				result.brokerActiveGeneration) ||
			!ParseUnsigned(terminalGeneration->second,
				std::numeric_limits<std::uint64_t>::max(),
				result.brokerTerminalGeneration))
		{
			reason = "SUPERVISOR_INVALID_RESULT";
			return false;
		}
		result.authoritativeCommandStatus = authoritative->second == "1";
		result.TargetCommandId() = target->second;
		result.CommandStatus() = status->second;
		result.CommandReasonCode() = commandReason->second;
		result.recoveryOnly = recovery->second == "1";
		result.ownerFenced = fenced->second == "1";
		result.ExecutionServiceEpoch() = epoch->second;
		result.ownerAuditAuthoritative = auditAuthoritative->second == "1";
		result.ownerAuditComplete = auditComplete->second == "1";
		result.OwnerAccount() = ownerAccount->second;
		result.OwnerExecutionDomain() = ownerDomain->second;
		result.paperFinalizationRequired =
			finalizationRequired->second == "1";
	}
	else if (finalizationResult)
	{
		const std::map<std::uint16_t, std::string>::const_iterator state =
			fields.find(PaperFinalizationState);
		const std::map<std::uint16_t, std::string>::const_iterator recoveryId =
			fields.find(RecoveryId);
		const std::map<std::uint16_t, std::string>::const_iterator finalizationId =
			fields.find(FinalizationId);
		const std::map<std::uint16_t, std::string>::const_iterator ownerSet =
			fields.find(ExpectedOwnerSetSha256);
		const std::map<std::uint16_t, std::string>::const_iterator ownerCount =
			fields.find(ExpectedOwnerCount);
		const std::map<std::uint16_t, std::string>::const_iterator ownerToken =
			fields.find(OwnerTokenSha256);
		const std::map<std::uint16_t, std::string>::const_iterator receiptSha =
			fields.find(ReceiptSha256);
		const std::map<std::uint16_t, std::string>::const_iterator receipt =
			fields.find(FinalizationReceipt);
		const std::map<std::uint16_t, std::string>::const_iterator auditAuth =
			fields.find(OwnerAuditAuthoritative);
		const std::map<std::uint16_t, std::string>::const_iterator auditComplete =
			fields.find(OwnerAuditComplete);
		const std::map<std::uint16_t, std::string>::const_iterator activeCount =
			fields.find(OwnerActiveOrderCount);
		const std::map<std::uint16_t, std::string>::const_iterator uncertainCount =
			fields.find(OwnerUncertainCommandCount);
		const std::map<std::uint16_t, std::string>::const_iterator account =
			fields.find(OwnerAccount);
		const std::map<std::uint16_t, std::string>::const_iterator domain =
			fields.find(OwnerExecutionDomain);
		const std::map<std::uint16_t, std::string>::const_iterator serviceEpoch =
			fields.find(ExecutionServiceEpoch);
		const std::map<std::uint16_t, std::string>::const_iterator serviceFence =
			fields.find(ExecutionServiceFencingGeneration);
		const std::uint16_t numericIds[] = {
			BrokerConnectionEpoch, BrokerActiveGeneration,
			BrokerTerminalGeneration, BrokerRiskGeneration,
			BrokerAccountGeneration, BrokerPositionGeneration,
			BrokerFxCashGeneration, BrokerExposureGeneration,
			BrokerTerminalExposureGeneration,
			BrokerRiskAbsorbedExposureGeneration,
			BrokerGlobalActiveOrderCount};
		std::uint64_t* numericValues[] = {
			&result.brokerConnectionEpoch, &result.brokerActiveGeneration,
			&result.brokerTerminalGeneration, &result.brokerRiskGeneration,
			&result.brokerAccountGeneration, &result.brokerPositionGeneration,
			&result.brokerFxCashGeneration, &result.brokerExposureGeneration,
			&result.brokerTerminalExposureGeneration,
			&result.brokerRiskAbsorbedExposureGeneration,
			&result.brokerGlobalActiveOrderCount};
		const std::map<std::uint16_t, std::string>::const_iterator postFill =
			fields.find(BrokerPostFillRiskReconciliationPending);
		const std::map<std::uint16_t, std::string>::const_iterator barrier =
			fields.find(BrokerRecoveryAuditBarrierComplete);
		const std::map<std::uint16_t, std::string>::const_iterator newEpoch =
			fields.find(BrokerRecoveryAuditNewConnectionEpochRequired);
		const std::map<std::uint16_t, std::string>::const_iterator position =
			fields.find(BrokerPositionQuantity);
		const std::map<std::uint16_t, std::string>::const_iterator gross =
			fields.find(BrokerGrossAbsolutePosition);
		const std::map<std::uint16_t, std::string>::const_iterator
			finalizationRequired = fields.find(PaperFinalizationRequired);
		const std::map<std::uint16_t, std::string>::const_iterator
			preliminaryReceiptSha =
				fields.find(PreliminaryFinalizationReceiptSha256);
		const std::map<std::uint16_t, std::string>::const_iterator
			terminalServiceEpoch = fields.find(TerminalizationServiceEpoch);
		const std::map<std::uint16_t, std::string>::const_iterator
			terminalServiceFence =
				fields.find(TerminalizationServiceFencingGeneration);
		const std::map<std::uint16_t, std::string>::const_iterator
			terminalGeneration = fields.find(TerminalizationGeneration);
		const std::map<std::uint16_t, std::string>::const_iterator
			terminalLatchSha = fields.find(TerminalLatchSha256);
		const std::map<std::uint16_t, std::string>::const_iterator
			terminalMutationClosed = fields.find(TerminalMutationGateClosed);
		const std::map<std::uint16_t, std::string>::const_iterator
			terminalTransportConnected =
				fields.find(TerminalBrokerTransportConnected);
		const std::map<std::uint16_t, std::string>::const_iterator
			terminalIngressHalted =
				fields.find(TerminalBrokerEventIngressHalted);
		const std::map<std::uint16_t, std::string>::const_iterator
			terminalQueueDrained =
				fields.find(TerminalBrokerCallbackQueueDrained);
		const std::map<std::uint16_t, std::string>::const_iterator
			terminalCallbacks = fields.find(TerminalBrokerCallbacksInFlight);
		const std::map<std::uint16_t, std::string>::const_iterator
			terminalReconnect = fields.find(TerminalBrokerReconnectPermitted);
		const std::map<std::uint16_t, std::string>::const_iterator
			terminalDurable = fields.find(TerminalLatchDurable);
		const std::map<std::uint16_t, std::string>::const_iterator
			terminalLoaded = fields.find(TerminalRuntimeLatchLoaded);
		const std::map<std::uint16_t, std::string>::const_iterator
			terminalVerified = fields.find(TerminalRuntimeVerified);
		const std::map<std::uint16_t, std::string>::const_iterator
			terminalReplay = fields.find(TerminalReplay);
		const std::map<std::uint16_t, std::string>::const_iterator
			terminalProofKind = fields.find(TerminalProofKind);
		const std::map<std::uint16_t, std::string>::const_iterator
			terminalExternalLatchSha =
				fields.find(TerminalExternalLatchSha256);
		const std::map<std::uint16_t, std::string>::const_iterator
			cutoffFileSha = fields.find(TransportCutoffReceiptFileSha256);
		const std::map<std::uint16_t, std::string>::const_iterator
			cutoffBodySha = fields.find(TransportCutoffReceiptBodySha256);
		const std::map<std::uint16_t, std::string>::const_iterator
			witnessFileSha =
				fields.find(PostCutoffTerminalWitnessFileSha256);
		const std::map<std::uint16_t, std::string>::const_iterator
			witnessBodySha =
				fields.find(PostCutoffTerminalWitnessBodySha256);
		const std::map<std::uint16_t, std::string>::const_iterator
			terminalEvidenceSha = fields.find(TerminalEvidenceSha256);
		const std::map<std::uint16_t, std::string>::const_iterator
			terminalEvidenceBodySha =
				fields.find(TerminalEvidenceBodySha256);
		const std::map<std::uint16_t, std::string>::const_iterator
			egressSha = fields.find(EgressPolicySha256);
		const std::map<std::uint16_t, std::string>::const_iterator
			egressPublisherPid = fields.find(EgressPublisherPid);
		const std::map<std::uint16_t, std::string>::const_iterator
			egressPublisherStartTicks = fields.find(EgressPublisherStartTicks);
		const std::map<std::uint16_t, std::string>::const_iterator
			providerTrustSha = fields.find(ProviderTrustPolicyBodySha256);
		const std::map<std::uint16_t, std::string>::const_iterator
			signedAccountSignatureSha =
				fields.find(SignedAccountSignatureSha256);
		const std::map<std::uint16_t, std::string>::const_iterator
			terminalExternalLoaded = fields.find(TerminalExternalLatchLoaded);
		const std::map<std::uint16_t, std::string>::const_iterator
			terminalCurrentVerified =
				fields.find(TerminalCurrentEvidenceVerified);
		const bool validState = state != fields.end() &&
			(state->second == "NONE" ||
			 state->second == "FENCE_PENDING" ||
			 state->second == "FENCE_COMPLETE" ||
			 state->second == "AUDIT_SEALED" ||
			 state->second == "TERMINAL_HALTED" ||
			 state->second == "ACKED");
		if (!validState || recoveryId == fields.end() ||
			finalizationId == fields.end() || ownerSet == fields.end() ||
			ownerCount == fields.end() || ownerToken == fields.end() ||
			receiptSha == fields.end() || receipt == fields.end() ||
			auditAuth == fields.end() || auditComplete == fields.end() ||
			activeCount == fields.end() || uncertainCount == fields.end() ||
			account == fields.end() || domain == fields.end() ||
			serviceEpoch == fields.end() || serviceFence == fields.end() ||
			postFill == fields.end() || barrier == fields.end() ||
			newEpoch == fields.end() || position == fields.end() ||
			gross == fields.end() ||
			finalizationRequired == fields.end() ||
			preliminaryReceiptSha == fields.end() ||
			terminalServiceEpoch == fields.end() ||
			terminalServiceFence == fields.end() ||
			terminalGeneration == fields.end() ||
			terminalLatchSha == fields.end() ||
			terminalMutationClosed == fields.end() ||
			terminalTransportConnected == fields.end() ||
			terminalIngressHalted == fields.end() ||
			terminalQueueDrained == fields.end() ||
			terminalCallbacks == fields.end() ||
			terminalReconnect == fields.end() ||
			terminalDurable == fields.end() ||
			terminalLoaded == fields.end() ||
			terminalVerified == fields.end() ||
			terminalReplay == fields.end() ||
			terminalProofKind == fields.end() ||
			terminalExternalLatchSha == fields.end() ||
			cutoffFileSha == fields.end() || cutoffBodySha == fields.end() ||
			witnessFileSha == fields.end() || witnessBodySha == fields.end() ||
			terminalEvidenceSha == fields.end() ||
			terminalEvidenceBodySha == fields.end() ||
			egressSha == fields.end() ||
			egressPublisherPid == fields.end() ||
			egressPublisherStartTicks == fields.end() ||
			providerTrustSha == fields.end() ||
			signedAccountSignatureSha == fields.end() ||
			terminalExternalLoaded == fields.end() ||
			terminalCurrentVerified == fields.end() ||
			finalizationRequired->second != "1" ||
			!ValidateText(recoveryId->second, 128) ||
			!ValidateText(finalizationId->second, 128) ||
			!ValidateSha256(ownerSet->second) ||
			!ValidateSha256(ownerToken->second) ||
			!ParseUnsigned(ownerCount->second, 4096,
				result.expectedOwnerCount) || result.expectedOwnerCount == 0 ||
			(auditAuth->second != "0" && auditAuth->second != "1") ||
			(auditComplete->second != "0" && auditComplete->second != "1") ||
			(postFill->second != "0" && postFill->second != "1") ||
			(barrier->second != "0" && barrier->second != "1") ||
			(newEpoch->second != "0" && newEpoch->second != "1") ||
			(terminalMutationClosed->second != "0" &&
			 terminalMutationClosed->second != "1") ||
			(terminalTransportConnected->second != "0" &&
			 terminalTransportConnected->second != "1") ||
			(terminalIngressHalted->second != "0" &&
			 terminalIngressHalted->second != "1") ||
			(terminalQueueDrained->second != "0" &&
			 terminalQueueDrained->second != "1") ||
			(terminalReconnect->second != "0" &&
			 terminalReconnect->second != "1") ||
			(terminalDurable->second != "0" &&
			 terminalDurable->second != "1") ||
			(terminalLoaded->second != "0" &&
			 terminalLoaded->second != "1") ||
			(terminalVerified->second != "0" &&
			 terminalVerified->second != "1") ||
			(terminalReplay->second != "0" &&
			 terminalReplay->second != "1") ||
			(terminalExternalLoaded->second != "0" &&
			 terminalExternalLoaded->second != "1") ||
			(terminalCurrentVerified->second != "0" &&
			 terminalCurrentVerified->second != "1") ||
			!ParseUnsigned(activeCount->second,
				std::numeric_limits<std::uint64_t>::max(),
				result.ownerActiveOrderCount) ||
			!ParseUnsigned(uncertainCount->second,
				std::numeric_limits<std::uint64_t>::max(),
				result.ownerUncertainCommandCount) ||
			!ParseUnsigned(serviceFence->second,
				std::numeric_limits<std::uint64_t>::max(),
				result.executionServiceFencingGeneration) ||
			!ParseUnsigned(terminalServiceFence->second,
				std::numeric_limits<std::uint64_t>::max(),
				result.terminalizationServiceFencingGeneration) ||
			!ParseUnsigned(terminalGeneration->second,
				std::numeric_limits<std::uint64_t>::max(),
				result.terminalizationGeneration) ||
			!ParseUnsigned(terminalCallbacks->second,
				std::numeric_limits<std::uint64_t>::max(),
				result.terminalBrokerCallbacksInFlight) ||
			!ParseUnsigned(egressPublisherPid->second,
				std::numeric_limits<std::uint64_t>::max(),
				result.egressPublisherPid) ||
			!ParseUnsigned(egressPublisherStartTicks->second,
				std::numeric_limits<std::uint64_t>::max(),
				result.egressPublisherStartTicks))
		{
			reason = "SUPERVISOR_INVALID_RESULT";
			return false;
		}
		for (std::size_t i = 0;
			 i < sizeof(numericIds) / sizeof(numericIds[0]); ++i)
		{
			const std::map<std::uint16_t, std::string>::const_iterator value =
				fields.find(numericIds[i]);
			if (value == fields.end() ||
				!ParseUnsigned(value->second,
					std::numeric_limits<std::uint64_t>::max(),
					*numericValues[i]))
			{
				reason = "SUPERVISOR_INVALID_RESULT";
				return false;
			}
		}
		const bool receiptRequired = state->second == "AUDIT_SEALED" ||
			state->second == "TERMINAL_HALTED" ||
			state->second == "ACKED";
		const bool terminalRequired =
			state->second == "TERMINAL_HALTED" ||
			state->second == "ACKED";
		if ((receiptRequired &&
			 (!ValidateSha256(receiptSha->second) ||
			  receipt->second.empty())) ||
			(!receiptRequired &&
			 (!receiptSha->second.empty() || !receipt->second.empty())))
		{
			reason = "SUPERVISOR_INVALID_RESULT";
			return false;
		}
		const bool externalProof = terminalProofKind->second ==
			"POST_CUTOFF_SIGNED_ACCOUNT_WITNESS_V1";
		const bool terminalShape = terminalRequired ?
			(ValidateSha256(preliminaryReceiptSha->second) &&
			 externalProof && state->second == "ACKED" &&
			 ValidateText(terminalServiceEpoch->second, 256) &&
			 result.terminalizationServiceFencingGeneration != 0 &&
			 result.terminalizationGeneration == 1 &&
			 ValidateSha256(terminalLatchSha->second) &&
			 ValidateSha256(terminalExternalLatchSha->second) &&
			 ValidateSha256(cutoffFileSha->second) &&
			 ValidateSha256(cutoffBodySha->second) &&
			 ValidateSha256(witnessFileSha->second) &&
			 ValidateSha256(witnessBodySha->second) &&
			 ValidateSha256(terminalEvidenceSha->second) &&
			 ValidateSha256(terminalEvidenceBodySha->second) &&
			 ValidateSha256(egressSha->second) &&
			 result.egressPublisherPid != 0 &&
			 result.egressPublisherStartTicks != 0 &&
			 ValidateSha256(providerTrustSha->second) &&
			 ValidateSha256(signedAccountSignatureSha->second) &&
			 terminalMutationClosed->second == "1" &&
			 terminalTransportConnected->second == "0" &&
			 terminalIngressHalted->second == "1" &&
			 terminalQueueDrained->second == "0" &&
			 result.terminalBrokerCallbacksInFlight == 0 &&
			 terminalReconnect->second == "0" &&
			 terminalDurable->second == "1" &&
			 terminalLoaded->second == "0" &&
			 terminalVerified->second == "0" &&
			 terminalExternalLoaded->second == "1" &&
			 terminalCurrentVerified->second == "1") :
			((state->second != "AUDIT_SEALED" ||
			  preliminaryReceiptSha->second == receiptSha->second) &&
			 terminalProofKind->second.empty() &&
			 terminalServiceEpoch->second.empty() &&
			 result.terminalizationServiceFencingGeneration == 0 &&
			 result.terminalizationGeneration == 0 &&
			 terminalLatchSha->second.empty() &&
			 terminalMutationClosed->second == "0" &&
			 terminalTransportConnected->second == "1" &&
			 terminalIngressHalted->second == "0" &&
			 terminalQueueDrained->second == "0" &&
			 result.terminalBrokerCallbacksInFlight == 0 &&
			 terminalReconnect->second == "1" &&
			 terminalDurable->second == "0" &&
			 terminalLoaded->second == "0" &&
			 terminalVerified->second == "0" &&
			 terminalReplay->second == "0" &&
			 terminalExternalLatchSha->second.empty() &&
			 cutoffFileSha->second.empty() && cutoffBodySha->second.empty() &&
			 witnessFileSha->second.empty() && witnessBodySha->second.empty() &&
			 terminalEvidenceSha->second.empty() &&
			 terminalEvidenceBodySha->second.empty() &&
			 egressSha->second.empty() && providerTrustSha->second.empty() &&
			 result.egressPublisherPid == 0 &&
			 result.egressPublisherStartTicks == 0 &&
			 signedAccountSignatureSha->second.empty() &&
			 terminalExternalLoaded->second == "0" &&
			 terminalCurrentVerified->second == "0");
		if (!terminalShape)
		{
			reason = "SUPERVISOR_INVALID_RESULT";
			return false;
		}
		result.PaperFinalizationState() = state->second;
		result.RecoveryId() = recoveryId->second;
		result.FinalizationId() = finalizationId->second;
		result.ExpectedOwnerSetSha256() = ownerSet->second;
		result.OwnerTokenSha256() = ownerToken->second;
		result.FinalizationReceiptSha256() = receiptSha->second;
		result.FinalizationReceipt() = receipt->second;
		result.ownerAuditAuthoritative = auditAuth->second == "1";
		result.ownerAuditComplete = auditComplete->second == "1";
		result.OwnerAccount() = account->second;
		result.OwnerExecutionDomain() = domain->second;
		result.ExecutionServiceEpoch() = serviceEpoch->second;
		result.brokerPostFillRiskReconciliationPending =
			postFill->second == "1";
		result.brokerRecoveryAuditBarrierComplete =
			barrier->second == "1";
		result.brokerRecoveryAuditNewConnectionEpochRequired =
			newEpoch->second == "1";
		result.BrokerPositionQuantity() = position->second;
		result.BrokerGrossAbsolutePosition() = gross->second;
		result.PreliminaryFinalizationReceiptSha256() =
			preliminaryReceiptSha->second;
		result.TerminalizationServiceEpoch() = terminalServiceEpoch->second;
		result.TerminalLatchSha256() = terminalLatchSha->second;
		result.terminalMutationGateClosed =
			terminalMutationClosed->second == "1";
		result.terminalBrokerTransportConnected =
			terminalTransportConnected->second == "1";
		result.terminalBrokerEventIngressHalted =
			terminalIngressHalted->second == "1";
		result.terminalBrokerCallbackQueueDrained =
			terminalQueueDrained->second == "1";
		result.terminalBrokerReconnectPermitted =
			terminalReconnect->second == "1";
		result.terminalLatchDurable = terminalDurable->second == "1";
		result.terminalRuntimeLatchLoaded = terminalLoaded->second == "1";
		result.terminalRuntimeVerified = terminalVerified->second == "1";
		result.terminalReplay = terminalReplay->second == "1";
		result.TerminalProofKind() = terminalProofKind->second;
		result.TerminalExternalLatchSha256() =
			terminalExternalLatchSha->second;
		result.TransportCutoffReceiptFileSha256() = cutoffFileSha->second;
		result.TransportCutoffReceiptBodySha256() = cutoffBodySha->second;
		result.PostCutoffTerminalWitnessFileSha256() = witnessFileSha->second;
		result.PostCutoffTerminalWitnessBodySha256() = witnessBodySha->second;
		result.TerminalEvidenceSha256() = terminalEvidenceSha->second;
		result.TerminalEvidenceBodySha256() =
			terminalEvidenceBodySha->second;
		result.EgressPolicySha256() = egressSha->second;
		result.ProviderTrustPolicyBodySha256() = providerTrustSha->second;
		result.SignedAccountSignatureSha256() =
			signedAccountSignatureSha->second;
		result.terminalExternalLatchLoaded =
			terminalExternalLoaded->second == "1";
		result.terminalCurrentEvidenceVerified =
			terminalCurrentVerified->second == "1";
		result.paperFinalizationRequired = true;
	}
	reason.clear();
	return true;
}
