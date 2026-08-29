#pragma once

#include <array>
#include <cstdint>
#include <string>

enum class SessionSupervisorOperation
{
	Provision,
	Revoke,
	Renew,
	Rotate,
	RecoveryQuery,
	PaperFinalize,
	PaperFinalizeAck,
	PaperTerminalizeAck,
	PaperTerminalWitnessPrepare,
	PaperTerminalWitnessAck
};

struct SessionSupervisorRequest
{
	SessionSupervisorOperation operation = SessionSupervisorOperation::Provision;
	std::string templateId;
	std::string token;
	std::string replacementToken;
	std::string agentId;
	std::string sessionId;
	std::uint32_t peerUid = 0;
	std::uint64_t ttlMs = 0;
	std::uint64_t expectedGeneration = 0;
	std::string targetCommandId;
	bool requirePaperFinalization = false;
	std::string recoveryId;
	std::string finalizationId;
	std::string expectedOwnerSetSha256;
	std::uint64_t expectedOwnerCount = 0;
	std::string receiptSha256;
	std::string terminalEvidenceSha256;
	std::string terminalEvidence;
};

struct SessionSupervisorResult
{
	bool accepted = false;
	std::uint64_t leaseGeneration = 0;
	bool authoritativeCommandStatus = false;
	long orderId = -1;
	bool recoveryOnly = false;
	bool ownerFenced = false;
	std::uint64_t executionServiceFencingGeneration = 0;
	std::uint64_t recoveryExpiresAtMs = 0;
	bool ownerAuditAuthoritative = false;
	bool ownerAuditComplete = false;
	std::uint64_t ownerActiveOrderCount = 0;
	std::uint64_t ownerUncertainCommandCount = 0;
	std::uint64_t brokerConnectionEpoch = 0;
	std::uint64_t brokerActiveGeneration = 0;
	std::uint64_t brokerTerminalGeneration = 0;
	std::uint64_t brokerRiskGeneration = 0;
	std::uint64_t brokerAccountGeneration = 0;
	std::uint64_t brokerPositionGeneration = 0;
	std::uint64_t brokerFxCashGeneration = 0;
	std::uint64_t brokerExposureGeneration = 0;
	std::uint64_t brokerTerminalExposureGeneration = 0;
	std::uint64_t brokerRiskAbsorbedExposureGeneration = 0;
	std::uint64_t brokerGlobalActiveOrderCount = 0;
	bool brokerPostFillRiskReconciliationPending = false;
	bool brokerRecoveryAuditBarrierComplete = false;
	bool brokerRecoveryAuditNewConnectionEpochRequired = false;
	bool paperFinalizationRequired = false;
	std::uint64_t expectedOwnerCount = 0;
	std::uint64_t terminalizationServiceFencingGeneration = 0;
	std::uint64_t terminalizationGeneration = 0;
	bool terminalMutationGateClosed = false;
	bool terminalBrokerTransportConnected = true;
	bool terminalBrokerEventIngressHalted = false;
	bool terminalBrokerCallbackQueueDrained = false;
	std::uint64_t terminalBrokerCallbacksInFlight = 0;
	bool terminalBrokerReconnectPermitted = true;
	bool terminalLatchDurable = false;
	bool terminalRuntimeLatchLoaded = false;
	bool terminalRuntimeVerified = false;
	bool terminalReplay = false;
	bool terminalExternalLatchLoaded = false;
	bool terminalCurrentEvidenceVerified = false;
	std::uint64_t egressPublisherPid = 0;
	std::uint64_t egressPublisherStartTicks = 0;

	std::string& ReasonCode() { return text[0]; }
	const std::string& ReasonCode() const { return text[0]; }
	std::string& TargetCommandId() { return text[1]; }
	const std::string& TargetCommandId() const { return text[1]; }
	std::string& CommandStatus() { return text[2]; }
	const std::string& CommandStatus() const { return text[2]; }
	std::string& CommandReasonCode() { return text[3]; }
	const std::string& CommandReasonCode() const { return text[3]; }
	std::string& ExecutionServiceEpoch() { return text[4]; }
	const std::string& ExecutionServiceEpoch() const { return text[4]; }
	std::string& OwnerAccount() { return text[5]; }
	const std::string& OwnerAccount() const { return text[5]; }
	std::string& OwnerExecutionDomain() { return text[6]; }
	const std::string& OwnerExecutionDomain() const { return text[6]; }
	std::string& PaperFinalizationState() { return text[7]; }
	const std::string& PaperFinalizationState() const { return text[7]; }
	std::string& RecoveryId() { return text[8]; }
	const std::string& RecoveryId() const { return text[8]; }
	std::string& FinalizationId() { return text[9]; }
	const std::string& FinalizationId() const { return text[9]; }
	std::string& ExpectedOwnerSetSha256() { return text[10]; }
	const std::string& ExpectedOwnerSetSha256() const { return text[10]; }
	std::string& OwnerTokenSha256() { return text[11]; }
	const std::string& OwnerTokenSha256() const { return text[11]; }
	std::string& FinalizationReceiptSha256() { return text[12]; }
	const std::string& FinalizationReceiptSha256() const { return text[12]; }
	std::string& FinalizationReceipt() { return text[13]; }
	const std::string& FinalizationReceipt() const { return text[13]; }
	std::string& BrokerPositionQuantity() { return text[14]; }
	const std::string& BrokerPositionQuantity() const { return text[14]; }
	std::string& BrokerGrossAbsolutePosition() { return text[15]; }
	const std::string& BrokerGrossAbsolutePosition() const { return text[15]; }
	std::string& PreliminaryFinalizationReceiptSha256() { return text[16]; }
	const std::string& PreliminaryFinalizationReceiptSha256() const { return text[16]; }
	std::string& TerminalizationServiceEpoch() { return text[17]; }
	const std::string& TerminalizationServiceEpoch() const { return text[17]; }
	std::string& TerminalLatchSha256() { return text[18]; }
	const std::string& TerminalLatchSha256() const { return text[18]; }
	std::string& TerminalProofKind() { return text[19]; }
	const std::string& TerminalProofKind() const { return text[19]; }
	std::string& TerminalExternalLatchSha256() { return text[20]; }
	const std::string& TerminalExternalLatchSha256() const { return text[20]; }
	std::string& TransportCutoffReceiptFileSha256() { return text[21]; }
	const std::string& TransportCutoffReceiptFileSha256() const { return text[21]; }
	std::string& TransportCutoffReceiptBodySha256() { return text[22]; }
	const std::string& TransportCutoffReceiptBodySha256() const { return text[22]; }
	std::string& PostCutoffTerminalWitnessFileSha256() { return text[23]; }
	const std::string& PostCutoffTerminalWitnessFileSha256() const { return text[23]; }
	std::string& PostCutoffTerminalWitnessBodySha256() { return text[24]; }
	const std::string& PostCutoffTerminalWitnessBodySha256() const { return text[24]; }
	std::string& TerminalEvidenceSha256() { return text[25]; }
	const std::string& TerminalEvidenceSha256() const { return text[25]; }
	std::string& TerminalEvidenceBodySha256() { return text[26]; }
	const std::string& TerminalEvidenceBodySha256() const { return text[26]; }
	std::string& EgressPolicySha256() { return text[27]; }
	const std::string& EgressPolicySha256() const { return text[27]; }
	std::string& ProviderTrustPolicyBodySha256() { return text[28]; }
	const std::string& ProviderTrustPolicyBodySha256() const { return text[28]; }
	std::string& SignedAccountSignatureSha256() { return text[29]; }
	const std::string& SignedAccountSignatureSha256() const { return text[29]; }

private:
	// A fixed aggregate keeps this wire DTO's destructor inline and avoids
	// widening the root-supervisor ABI with one symbol per named string field.
	std::array<std::string, 30> text;
};

class SessionSupervisorProtocol
{
public:
	static bool EncodeRequest(const SessionSupervisorRequest& request,
		std::string& body, std::string& reason);
	static bool DecodeRequest(const std::string& body,
		SessionSupervisorRequest& request, std::string& reason);
	static std::string EncodeResult(const SessionSupervisorResult& result);
	static bool DecodeResult(const std::string& body,
		SessionSupervisorResult& result, std::string& reason);
};
