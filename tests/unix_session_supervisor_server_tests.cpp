#include "../HeptaTrade/tool_host/session_supervisor_protocol.h"
#include "../HeptaTrade/execution/execution_coordinator.h"
#include "../HeptaTrade/tool_host/agent_os_runtime_config.h"
#include "../HeptaTrade/tool_host/execution_gateway_runtime_config.h"
#include "../HeptaTrade/tool_host/tool_gateway_session_policy.h"
#include "../HeptaTrade/tool_host/typed_tool_protocol.h"
#include "../HeptaTrade/tool_host/unix_session_supervisor_server.h"

#include <atomic>
#include <cassert>
#include <arpa/inet.h>
#include <chrono>
#include <cstdio>
#include <cstring>
#include <fcntl.h>
#include <fstream>
#include <iostream>
#include <limits>
#include <map>
#include <openssl/evp.h>
#include <sstream>
#include <string>
#include <sys/socket.h>
#include <sys/stat.h>
#include <sys/un.h>
#include <sys/wait.h>
#include <unistd.h>
#include <vector>
#include <thread>

namespace {

std::string TempPath(const char* pattern);

std::string Sha256Prefixed(const std::string& value)
{
	unsigned char digest[EVP_MAX_MD_SIZE];
	unsigned int length = 0;
	EVP_MD_CTX* context = EVP_MD_CTX_new();
	assert(context != nullptr);
	assert(EVP_DigestInit_ex(context, EVP_sha256(), nullptr) == 1);
	assert(EVP_DigestUpdate(context, value.data(), value.size()) == 1);
	assert(EVP_DigestFinal_ex(context, digest, &length) == 1);
	EVP_MD_CTX_free(context);
	assert(length == 32);
	static const char digits[] = "0123456789abcdef";
	std::string result = "sha256:";
	for (unsigned int i = 0; i < length; ++i)
	{
		result.push_back(digits[digest[i] >> 4]);
		result.push_back(digits[digest[i] & 15]);
	}
	return result;
}

std::string HexEncodeTest(const std::string& value)
{
	static const char digits[] = "0123456789abcdef";
	std::string encoded;
	for (std::size_t i = 0; i < value.size(); ++i)
	{
		const unsigned char byte = static_cast<unsigned char>(value[i]);
		encoded.push_back(digits[byte >> 4]);
		encoded.push_back(digits[byte & 15]);
	}
	return encoded;
}

std::string TerminalOwnerCanonical(
	const std::vector<SessionSupervisorLeaseRecord>& records)
{
	std::vector<std::string> owners;
	for (std::size_t i = 0; i < records.size(); ++i)
		if (records[i].templateId == "paper")
			owners.push_back(Sha256Prefixed(records[i].token + "\n") + "\t" +
				std::to_string(records[i].leaseGeneration) + "\t" +
				HexEncodeTest(records[i].ownerAccount) + "\t" +
				HexEncodeTest(records[i].ownerExecutionDomain) + "\n");
	std::sort(owners.begin(), owners.end());
	std::string canonical;
	for (std::size_t i = 0; i < owners.size(); ++i) canonical += owners[i];
	return canonical;
}

std::string BuildTerminalEvidence(
	const std::vector<SessionSupervisorLeaseRecord>& records,
	const SessionSupervisorLeaseRecord& terminalOwner,
	const SessionSupervisorRequest& request,
	const SessionSupervisorResult& sealed)
{
	const std::string digest = "sha256:" + std::string(64, 'a');
	const std::string canonical = TerminalOwnerCanonical(records);
	std::ostringstream out;
	out << "HPE1\n"
		<< "schema=hepta.paper-terminal-witness-evidence.v1\n"
		<< "version=1\n"
		<< "status=CURRENT_POST_CUTOFF_TERMINAL_WITNESS_VERIFIED\n"
		<< "terminal_proof_kind=POST_CUTOFF_SIGNED_ACCOUNT_WITNESS_V1\n"
		<< "recovery_id=" << request.recoveryId << '\n'
		<< "finalization_id=" << request.finalizationId << '\n'
		<< "campaign_id=campaign-supervisor-test\n"
		<< "cycle_id=cycle-supervisor-test\n"
		<< "expected_owner_set_sha256="
		<< request.expectedOwnerSetSha256 << '\n'
		<< "expected_owner_count=" << request.expectedOwnerCount << '\n'
		<< "owner_set_canonical_hex=" << HexEncodeTest(canonical) << '\n'
		<< "preliminary_finalization_receipt_sha256="
		<< request.receiptSha256 << '\n'
		<< "owner_agent_id=" << terminalOwner.agentId << '\n'
		<< "owner_session_id=" << terminalOwner.sessionId << '\n'
		<< "owner_account=" << terminalOwner.ownerAccount << '\n'
		<< "owner_execution_domain="
		<< terminalOwner.ownerExecutionDomain << '\n'
		<< "account_id_sha256="
		<< Sha256Prefixed(terminalOwner.ownerAccount) << '\n'
		<< "execution_service_epoch=" << sealed.ExecutionServiceEpoch() << '\n'
		<< "execution_service_fencing_generation="
		<< sealed.executionServiceFencingGeneration << '\n'
		<< "recovery_ingress_fence="
		<< terminalOwner.leaseGeneration << '\n'
		<< "terminalization_generation=1\n"
		<< "terminalizing_latch_sha256=" << digest << '\n'
		<< "terminal_external_halt_latch_sha256=" << digest << '\n'
		<< "transport_cutoff_receipt_file_sha256=" << digest << '\n'
		<< "transport_cutoff_receipt_body_sha256=" << digest << '\n'
		<< "post_cutoff_terminal_witness_file_sha256=" << digest << '\n'
		<< "post_cutoff_terminal_witness_body_sha256=" << digest << '\n'
		<< "provider_trust_policy_file_sha256=" << digest << '\n'
		<< "provider_trust_policy_body_sha256=" << digest << '\n'
		<< "provider_id=reviewed-provider-supervisor-test\n"
		<< "provider_capability=ACCOUNT_WIDE_ATOMIC_OR_CAUSAL_POST_CUTOFF_READ_ONLY_V1\n"
		<< "signed_account_payload_sha256=" << digest << '\n'
		<< "signed_account_signature_sha256=" << digest << '\n'
		<< "host_boot_id=11111111-1111-1111-1111-111111111111\n"
		<< "egress_publisher_pid=4102\n"
		<< "egress_publisher_start_ticks=99123\n"
		<< "egress_policy_generation=23\n"
		<< "egress_policy_sha256=" << digest << '\n'
		<< "query_started_after_challenge=1\n"
		<< "observed_after_cutoff=1\n"
		<< "snapshot_consistency=CAUSAL_WATERMARK\n"
		<< "causal_watermark_dominates_cutoff=1\n"
		<< "causal_watermark_dominates_all_mutations=1\n"
		<< "account_queries_complete=1\n"
		<< "active_orders_complete=1\n"
		<< "completed_orders_complete=1\n"
		<< "executions_complete=1\n"
		<< "positions_complete=1\n"
		<< "cash_fx_complete=1\n"
		<< "risk_complete=1\n"
		<< "known_mutation_command_set_sha256=" << digest << '\n'
		<< "known_mutation_command_count=2\n"
		<< "known_correlation_set_sha256=" << digest << '\n'
		<< "known_correlation_count=2\n"
		<< "all_known_mutation_commands_settled=1\n"
		<< "settled_mutation_command_count=2\n"
		<< "unknown_mutation_command_count=0\n"
		<< "unresolved_mutation_command_count=0\n"
		<< "unknown_active_order_count=0\n"
		<< "active_order_count=0\n"
		<< "position_count=0\n"
		<< "nonzero_cash_fx_count=0\n"
		<< "gross_absolute_position=0\n"
		<< "gross_fx_exposure=0\n"
		<< "gross_risk=0\n"
		<< "mutation_connector_count=0\n"
		<< "broker_socket_count=0\n"
		<< "broker_process_count=0\n"
		<< "broker_credential_count=0\n"
		<< "execution_service_inactive=1\n"
		<< "paper_units_inactive=1\n"
		<< "execution_mutation_gate_closed=1\n"
		<< "broker_transport_connected=0\n"
		<< "broker_reconnect_permitted=0\n"
		<< "read_only_authority=1\n"
		<< "mutation_attempted=0\n"
		<< "paper_authorized=0\n"
		<< "live_authorized=0\n"
		<< "mutation_authorized=0\n"
		<< "direct_broker_access=0\n"
		<< "order_submission_authorized=0\n"
		<< "order_authorized=0\n"
		<< "paper_only=1\n"
		<< "authority_granted=0\n"
		<< "terminal_external_halt_latch_durable=1\n"
		<< "terminal_witness_durable=1\n"
		<< "current_host_boundary_verified=1\n";
	const std::string body = out.str();
	out << "evidence_body_sha256=" << Sha256Prefixed(body) << '\n';
	return out.str();
}

std::string RewriteTerminalEvidenceField(
	const std::string& evidence, const std::string& name,
	const std::string& value)
{
	const std::string prefix = name + "=";
	const std::size_t start = evidence.find("\n" + prefix);
	assert(start != std::string::npos);
	const std::size_t valueStart = start + 1 + prefix.size();
	const std::size_t end = evidence.find('\n', valueStart);
	assert(end != std::string::npos);
	std::string rewritten = evidence;
	rewritten.replace(valueStart, end - valueStart, value);
	const std::string digestPrefix = "\nevidence_body_sha256=";
	const std::size_t digest = rewritten.rfind(digestPrefix);
	assert(digest != std::string::npos);
	const std::string body = rewritten.substr(0, digest + 1);
	return body + "evidence_body_sha256=" + Sha256Prefixed(body) + "\n";
}

struct SessionCtlProcessResult
{
	int exitCode = -1;
	std::string standardOutput;
};

std::string JsonEncodedStringField(
	const std::string& json, const std::string& name)
{
	const std::string prefix = "\"" + name + "\":\"";
	const std::size_t start = json.find(prefix);
	assert(start != std::string::npos);
	std::size_t cursor = start + prefix.size();
	bool escaped = false;
	for (std::size_t i = cursor; i < json.size(); ++i)
	{
		if (!escaped && json[i] == '"')
			return json.substr(cursor, i - cursor);
		if (!escaped && json[i] == '\\') escaped = true;
		else escaped = false;
	}
	assert(false);
	return std::string();
}

SessionCtlProcessResult RunSessionCtl(
	const std::vector<std::string>& arguments)
{
	int outputPipe[2];
	assert(pipe2(outputPipe, O_CLOEXEC) == 0);
	const pid_t child = fork();
	assert(child >= 0);
	if (child == 0)
	{
		assert(dup2(outputPipe[1], STDOUT_FILENO) == STDOUT_FILENO);
		close(outputPipe[0]);
		close(outputPipe[1]);
		std::vector<std::string> storage;
		storage.push_back(HEPTA_SESSIONCTL_TEST_BINARY);
		storage.insert(storage.end(), arguments.begin(), arguments.end());
		std::vector<char*> argv;
		for (std::size_t i = 0; i < storage.size(); ++i)
			argv.push_back(&storage[i][0]);
		argv.push_back(nullptr);
		execv(argv[0], argv.data());
		_exit(127);
	}
	close(outputPipe[1]);
	SessionCtlProcessResult result;
	char buffer[4096];
	while (true)
	{
		const ssize_t count = read(outputPipe[0], buffer, sizeof(buffer));
		if (count < 0 && errno == EINTR) continue;
		assert(count >= 0);
		if (count == 0) break;
		result.standardOutput.append(buffer,
			static_cast<std::size_t>(count));
	}
	close(outputPipe[0]);
	int status = 0;
	assert(waitpid(child, &status, 0) == child);
	assert(WIFEXITED(status));
	result.exitCode = WEXITSTATUS(status);
	return result;
}

std::string WriteSessionCtlTokenFile(const std::string& token)
{
	const std::string path =
		TempPath("/tmp/hepta-sessionctl-e2e-token-XXXXXX");
	const int fd = open(path.c_str(),
		O_WRONLY | O_CREAT | O_EXCL | O_CLOEXEC, 0600);
	assert(fd >= 0);
	const std::string canonical = token + "\n";
	assert(write(fd, canonical.data(), canonical.size()) ==
		static_cast<ssize_t>(canonical.size()));
	assert(fsync(fd) == 0);
	assert(close(fd) == 0);
	return path;
}

std::string WriteSessionCtlEvidenceFile(
	const std::string& evidence, std::string& directory)
{
	char pattern[] = "/tmp/hepta-sessionctl-e2e-evidence.XXXXXX";
	char* created = ::mkdtemp(pattern);
	assert(created != nullptr);
	directory = created;
	assert(::chmod(directory.c_str(), 0700) == 0);
	const std::string path = directory + "/terminal-evidence.v1";
	const int fd = ::open(path.c_str(),
		O_WRONLY | O_CREAT | O_EXCL | O_CLOEXEC, 0400);
	assert(fd >= 0);
	assert(::write(fd, evidence.data(), evidence.size()) ==
		static_cast<ssize_t>(evidence.size()));
	assert(::fsync(fd) == 0);
	assert(::close(fd) == 0);
	return path;
}

void TestPaperFinalizationProtocol()
{
	SessionSupervisorRequest request;
	request.operation = SessionSupervisorOperation::RecoveryQuery;
	request.token = std::string(64, 'a');
	request.expectedGeneration = 7;
	request.targetCommandId = "external-recovery-command-7";
	request.requirePaperFinalization = true;
	std::string body;
	std::string reason;
	assert(SessionSupervisorProtocol::EncodeRequest(request, body, reason));
	SessionSupervisorRequest decoded;
	assert(SessionSupervisorProtocol::DecodeRequest(body, decoded, reason));
	assert(decoded.operation == SessionSupervisorOperation::RecoveryQuery);
	assert(decoded.requirePaperFinalization);

	request = SessionSupervisorRequest();
	request.operation = SessionSupervisorOperation::PaperFinalize;
	request.token = std::string(64, 'a');
	request.expectedGeneration = 7;
	request.recoveryId = "recovery-7";
	request.finalizationId = "finalization-7";
	request.expectedOwnerSetSha256 = "sha256:" + std::string(64, 'b');
	request.expectedOwnerCount = 2;
	assert(SessionSupervisorProtocol::EncodeRequest(request, body, reason));
	assert(SessionSupervisorProtocol::DecodeRequest(body, decoded, reason));
	assert(decoded.operation == SessionSupervisorOperation::PaperFinalize);
	assert(decoded.token == request.token);
	assert(decoded.recoveryId == request.recoveryId);
	assert(decoded.finalizationId == request.finalizationId);
	assert(decoded.expectedOwnerSetSha256 ==
		request.expectedOwnerSetSha256);
	assert(decoded.expectedOwnerCount == 2);
	request.operation = SessionSupervisorOperation::PaperFinalizeAck;
	request.receiptSha256 = "sha256:" + std::string(64, 'c');
	assert(SessionSupervisorProtocol::EncodeRequest(request, body, reason));
	assert(SessionSupervisorProtocol::DecodeRequest(body, decoded, reason));
	assert(decoded.operation ==
		SessionSupervisorOperation::PaperFinalizeAck);
	assert(decoded.receiptSha256 == request.receiptSha256);
	request.operation = SessionSupervisorOperation::PaperTerminalizeAck;
	assert(SessionSupervisorProtocol::EncodeRequest(request, body, reason));
	assert(SessionSupervisorProtocol::DecodeRequest(body, decoded, reason));
	assert(decoded.operation ==
		SessionSupervisorOperation::PaperTerminalizeAck);
	assert(decoded.receiptSha256 == request.receiptSha256);
	request.operation =
		SessionSupervisorOperation::PaperTerminalWitnessPrepare;
	assert(SessionSupervisorProtocol::EncodeRequest(request, body, reason));
	assert(SessionSupervisorProtocol::DecodeRequest(body, decoded, reason));
	assert(decoded.operation ==
		SessionSupervisorOperation::PaperTerminalWitnessPrepare);
	assert(decoded.receiptSha256 == request.receiptSha256);
	request.operation = SessionSupervisorOperation::PaperTerminalWitnessAck;
	request.terminalEvidence = "HPE1\n";
	request.terminalEvidenceSha256 = Sha256Prefixed(request.terminalEvidence);
	assert(SessionSupervisorProtocol::EncodeRequest(request, body, reason));
	assert(SessionSupervisorProtocol::DecodeRequest(body, decoded, reason));
	assert(decoded.operation ==
		SessionSupervisorOperation::PaperTerminalWitnessAck);
	assert(decoded.terminalEvidence == request.terminalEvidence);
	assert(decoded.terminalEvidenceSha256 ==
		request.terminalEvidenceSha256);
	request.token.assign(512, 't');
	request.expectedGeneration =
		std::numeric_limits<std::uint64_t>::max();
	request.recoveryId.assign(128, 'r');
	request.finalizationId.assign(128, 'f');
	request.expectedOwnerCount = 4096;
	request.terminalEvidence.assign(12288, 'e');
	request.terminalEvidence.replace(0, 5, "HPE1\n");
	request.terminalEvidence.back() = '\n';
	request.terminalEvidenceSha256 = Sha256Prefixed(
		request.terminalEvidence);
	assert(SessionSupervisorProtocol::EncodeRequest(request, body, reason));
	assert(body.size() <= 16384);
	assert(SessionSupervisorProtocol::DecodeRequest(body, decoded, reason));
	assert(decoded.token.size() == 512);
	assert(decoded.terminalEvidence.size() == 12288);
	request.terminalEvidence.push_back('x');
	request.terminalEvidenceSha256 = Sha256Prefixed(
		request.terminalEvidence);
	assert(!SessionSupervisorProtocol::EncodeRequest(request, body, reason));
	request.token.assign(64, 'a');
	request.expectedGeneration = 7;
	request.recoveryId = "recovery-7";
	request.finalizationId = "finalization-7";
	request.expectedOwnerCount = 2;
	request.terminalEvidence = "HPE1\n";
	request.terminalEvidenceSha256 = Sha256Prefixed(
		request.terminalEvidence);

	SessionSupervisorResult result;
	result.accepted = true;
	result.paperFinalizationRequired = true;
	result.leaseGeneration = 7;
	result.ReasonCode() = "PAPER_FINALIZATION_AUDIT_SEALED";
	result.PaperFinalizationState() = "AUDIT_SEALED";
	result.RecoveryId() = request.recoveryId;
	result.FinalizationId() = request.finalizationId;
	result.ExpectedOwnerSetSha256() = request.expectedOwnerSetSha256;
	result.expectedOwnerCount = 2;
	result.OwnerTokenSha256() = "sha256:" + std::string(64, 'd');
	result.FinalizationReceiptSha256() = request.receiptSha256;
	result.FinalizationReceipt() = "typed\nreceipt\n";
	result.ownerAuditAuthoritative = true;
	result.ownerAuditComplete = true;
	result.OwnerAccount() = "DU1";
	result.OwnerExecutionDomain() = "paper-alpha";
	result.ExecutionServiceEpoch() = "epoch-1";
	result.executionServiceFencingGeneration = 3;
	result.brokerConnectionEpoch = 4;
	result.brokerActiveGeneration = 5;
	result.brokerTerminalGeneration = 6;
	result.brokerRiskGeneration = 7;
	result.brokerAccountGeneration = 8;
	result.brokerPositionGeneration = 9;
	result.brokerFxCashGeneration = 10;
	result.brokerExposureGeneration = 0;
	result.brokerTerminalExposureGeneration = 0;
	result.brokerRiskAbsorbedExposureGeneration = 0;
	result.brokerRecoveryAuditBarrierComplete = true;
	result.BrokerPositionQuantity() = "0";
	result.BrokerGrossAbsolutePosition() = "0";
	SessionSupervisorResult decodedResult;
	assert(SessionSupervisorProtocol::DecodeResult(
		SessionSupervisorProtocol::EncodeResult(result),
		decodedResult, reason));
	assert(decodedResult.accepted);
	assert(decodedResult.PaperFinalizationState() == "AUDIT_SEALED");
	assert(decodedResult.FinalizationReceipt() == "typed\nreceipt\n");
	assert(decodedResult.brokerFxCashGeneration == 10);
	assert(decodedResult.BrokerPositionQuantity() == "0");
	result.PaperFinalizationState() = "ACKED";
	result.ReasonCode() = "PAPER_FINALIZATION_TERMINAL_ACKED";
	result.PreliminaryFinalizationReceiptSha256() = request.receiptSha256;
	result.TerminalizationServiceEpoch() = "epoch-1";
	result.terminalizationServiceFencingGeneration = 3;
	result.terminalizationGeneration = 1;
	result.TerminalLatchSha256() = "sha256:" + std::string(64, 'e');
	result.TerminalProofKind() =
		"POST_CUTOFF_SIGNED_ACCOUNT_WITNESS_V1";
	result.TerminalExternalLatchSha256() =
		"sha256:" + std::string(64, 'f');
	result.TransportCutoffReceiptFileSha256() =
		"sha256:" + std::string(64, '1');
	result.TransportCutoffReceiptBodySha256() =
		"sha256:" + std::string(64, '2');
	result.PostCutoffTerminalWitnessFileSha256() =
		"sha256:" + std::string(64, '3');
	result.PostCutoffTerminalWitnessBodySha256() =
		"sha256:" + std::string(64, '4');
	result.TerminalEvidenceSha256() =
		"sha256:" + std::string(64, '5');
	result.TerminalEvidenceBodySha256() =
		"sha256:" + std::string(64, '6');
	result.EgressPolicySha256() = "sha256:" + std::string(64, '7');
	result.egressPublisherPid = 4102;
	result.egressPublisherStartTicks = 99123;
	result.ProviderTrustPolicyBodySha256() =
		"sha256:" + std::string(64, '8');
	result.SignedAccountSignatureSha256() =
		"sha256:" + std::string(64, '9');
	result.terminalMutationGateClosed = true;
	result.terminalBrokerTransportConnected = false;
	result.terminalBrokerEventIngressHalted = true;
	result.terminalBrokerCallbackQueueDrained = false;
	result.terminalBrokerCallbacksInFlight = 0;
	result.terminalBrokerReconnectPermitted = false;
	result.terminalLatchDurable = true;
	result.terminalRuntimeLatchLoaded = false;
	result.terminalRuntimeVerified = false;
	result.terminalExternalLatchLoaded = true;
	result.terminalCurrentEvidenceVerified = true;
	assert(SessionSupervisorProtocol::DecodeResult(
		SessionSupervisorProtocol::EncodeResult(result),
		decodedResult, reason));
	assert(decodedResult.PaperFinalizationState() == "ACKED");
	assert(decodedResult.TerminalLatchSha256() ==
		result.TerminalLatchSha256());
	result.PaperFinalizationState() = "NONE";
	result.FinalizationReceiptSha256().clear();
	result.FinalizationReceipt().clear();
	result.PreliminaryFinalizationReceiptSha256().clear();
	result.TerminalizationServiceEpoch().clear();
	result.terminalizationServiceFencingGeneration = 0;
	result.terminalizationGeneration = 0;
	result.TerminalLatchSha256().clear();
	result.TerminalProofKind().clear();
	result.TerminalExternalLatchSha256().clear();
	result.TransportCutoffReceiptFileSha256().clear();
	result.TransportCutoffReceiptBodySha256().clear();
	result.PostCutoffTerminalWitnessFileSha256().clear();
	result.PostCutoffTerminalWitnessBodySha256().clear();
	result.TerminalEvidenceSha256().clear();
	result.TerminalEvidenceBodySha256().clear();
	result.EgressPolicySha256().clear();
	result.egressPublisherPid = 0;
	result.egressPublisherStartTicks = 0;
	result.ProviderTrustPolicyBodySha256().clear();
	result.SignedAccountSignatureSha256().clear();
	result.terminalMutationGateClosed = false;
	result.terminalBrokerTransportConnected = true;
	result.terminalBrokerEventIngressHalted = false;
	result.terminalBrokerCallbackQueueDrained = false;
	result.terminalBrokerReconnectPermitted = true;
	result.terminalLatchDurable = false;
	result.terminalRuntimeLatchLoaded = false;
	result.terminalRuntimeVerified = false;
	result.terminalExternalLatchLoaded = false;
	result.terminalCurrentEvidenceVerified = false;
	assert(SessionSupervisorProtocol::DecodeResult(
		SessionSupervisorProtocol::EncodeResult(result),
		decodedResult, reason));
}

class RecoveryControlAuthority : public ExecutionControlAuthority
{
public:
	std::function<ExecutionControlResult(const ExecutionControlCommand&)>
		query;
	std::function<ExecutionControlResult(const ExecutionControlCommand&)>
		ownerAudit;
	std::function<ExecutionControlResult(const ExecutionControlCommand&)>
		fence;
	std::function<ExecutionControlResult(const ExecutionControlCommand&)>
		terminalize;

	ExecutionControlResult QueryCommandStatus(
		const ExecutionControlCommand& command) override
	{
		assert(query);
		return query(command);
	}
	ExecutionControlResult FenceSessionOwner(
		const ExecutionControlCommand& command) override
	{
		return fence ? fence(command) : Rejected(command);
	}
	ExecutionControlResult ReleaseSessionOwnerFence(
		const ExecutionControlCommand& command) override
	{
		return Rejected(command);
	}
	ExecutionControlResult ReconcileAuthoritativeState(
		const ExecutionControlCommand& command) override
	{
		return Rejected(command);
	}
	ExecutionControlResult RecoveryAuditOwner(
		const ExecutionControlCommand& command) override
	{
		return ownerAudit ? ownerAudit(command) : Rejected(command);
	}
	ExecutionControlResult TerminalizeRecoveryOwner(
		const ExecutionControlCommand& command) override
	{
		return terminalize ? terminalize(command) : Rejected(command);
	}

private:
	static ExecutionControlResult Rejected(
		const ExecutionControlCommand& command)
	{
		ExecutionControlResult result;
		result.commandId = command.context.toolCallId;
		result.reasonCode = "TEST_CONTROL_OPERATION_UNAVAILABLE";
		return result;
	}
};

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

std::string TempKeyPath()
{
	const std::string path = TempPath("/tmp/hepta-supervisor-key-XXXXXX");
	const int fd = open(path.c_str(), O_WRONLY | O_CREAT | O_TRUNC | O_CLOEXEC, 0600);
	assert(fd >= 0);
	const std::string key(32, 'K');
	assert(write(fd, key.data(), key.size()) == static_cast<ssize_t>(key.size()));
	assert(fsync(fd) == 0);
	close(fd);
	return path;
}

bool HexNibble(char value, unsigned char& nibble)
{
	if (value >= '0' && value <= '9')
		nibble = static_cast<unsigned char>(value - '0');
	else if (value >= 'a' && value <= 'f')
		nibble = static_cast<unsigned char>(value - 'a' + 10);
	else
		return false;
	return true;
}

std::vector<std::string> AuditPayloads(const std::string& path)
{
	std::ifstream input(path.c_str());
	std::vector<std::string> payloads;
	std::string line;
	while (std::getline(input, line))
	{
		std::vector<std::string> fields;
		std::stringstream stream(line);
		std::string field;
		while (std::getline(stream, field, '\t')) fields.push_back(field);
		assert(fields.size() == 7);
		assert(fields[5].size() % 2 == 0);
		std::string payload;
		payload.reserve(fields[5].size() / 2);
		for (std::size_t i = 0; i < fields[5].size(); i += 2)
		{
			unsigned char high = 0;
			unsigned char low = 0;
			assert(HexNibble(fields[5][i], high));
			assert(HexNibble(fields[5][i + 1], low));
			payload.push_back(static_cast<char>((high << 4) | low));
		}
		payloads.push_back(payload);
	}
	return payloads;
}

SessionSupervisorResult Call(const std::string& socketPath,
	const SessionSupervisorRequest& request)
{
	const int client = socket(AF_UNIX, SOCK_STREAM | SOCK_CLOEXEC, 0);
	assert(client >= 0);
	sockaddr_un address;
	std::memset(&address, 0, sizeof(address));
	address.sun_family = AF_UNIX;
	std::strncpy(address.sun_path, socketPath.c_str(), sizeof(address.sun_path) - 1);
	assert(connect(client, reinterpret_cast<sockaddr*>(&address), sizeof(address)) == 0);
	std::string body;
	std::string reason;
	assert(SessionSupervisorProtocol::EncodeRequest(request, body, reason));
	assert(TypedToolProtocol::WriteFrame(client, body, 1000, reason));
	std::string response;
	assert(TypedToolProtocol::ReadFrame(client, 16384, 5000, response, reason));
	SessionSupervisorResult result;
	if (!SessionSupervisorProtocol::DecodeResult(response, result, reason))
	{
		std::cerr << "DecodeResult failed: operation="
			<< static_cast<int>(request.operation)
			<< " reason=" << reason
			<< " response_bytes=" << response.size() << std::endl;
		assert(false);
	}
	close(client);
	return result;
}

int Connect(const std::string& socketPath)
{
	const int client = socket(AF_UNIX, SOCK_STREAM | SOCK_CLOEXEC, 0);
	assert(client >= 0);
	sockaddr_un address;
	std::memset(&address, 0, sizeof(address));
	address.sun_family = AF_UNIX;
	std::strncpy(address.sun_path, socketPath.c_str(), sizeof(address.sun_path) - 1);
	assert(connect(client, reinterpret_cast<sockaddr*>(&address), sizeof(address)) == 0);
	return client;
}

void ExerciseDisconnectPhases(const std::string& socketPath)
{
	auto invalidFrame = [&](std::uint32_t length, const std::string& payload,
		const std::string& expected) {
		const int faultClient = Connect(socketPath);
		const std::uint32_t encodedLength = htonl(length);
		assert(write(faultClient, &encodedLength, sizeof(encodedLength)) ==
			static_cast<ssize_t>(sizeof(encodedLength)));
		if (!payload.empty())
			assert(write(faultClient, payload.data(), payload.size()) ==
				static_cast<ssize_t>(payload.size()));
		std::string response;
		std::string reason;
		assert(TypedToolProtocol::ReadFrame(faultClient, 4096, 2000, response, reason));
		SessionSupervisorResult result;
		assert(SessionSupervisorProtocol::DecodeResult(response, result, reason));
		assert(!result.accepted && result.ReasonCode().find(expected) != std::string::npos);
		close(faultClient);
	};
	invalidFrame(4097, std::string(), "FRAME_LENGTH_REJECTED");
	invalidFrame(4, "NOPE", "SUPERVISOR_SCHEMA_MAGIC_MISMATCH");
	{
		const int timeoutClient = Connect(socketPath);
		const std::uint32_t timeoutLength = htonl(8);
		assert(write(timeoutClient, &timeoutLength, sizeof(timeoutLength)) ==
			static_cast<ssize_t>(sizeof(timeoutLength)));
		assert(write(timeoutClient, "HSS", 3) == 3);
		const std::chrono::steady_clock::time_point started = std::chrono::steady_clock::now();
		std::string response;
		std::string reason;
		assert(TypedToolProtocol::ReadFrame(timeoutClient, 4096, 2500, response, reason));
		const long elapsedMs = static_cast<long>(std::chrono::duration_cast<std::chrono::milliseconds>(
			std::chrono::steady_clock::now() - started).count());
		assert(elapsedMs >= 800 && elapsedMs < 2200);
		SessionSupervisorResult result;
		assert(SessionSupervisorProtocol::DecodeResult(response, result, reason));
		assert(result.ReasonCode().find("FRAME_BODY_TIMEOUT") != std::string::npos);
		close(timeoutClient);
	}
	int client = Connect(socketPath);
	const std::uint32_t networkLength = htonl(64);
	assert(write(client, &networkLength, 2) == 2);
	close(client);
	client = Connect(socketPath);
	assert(write(client, &networkLength, sizeof(networkLength)) ==
		static_cast<ssize_t>(sizeof(networkLength)));
	assert(write(client, "HSS", 3) == 3);
	close(client);
	SessionSupervisorRequest unknown;
	unknown.operation = SessionSupervisorOperation::Renew;
	unknown.token = "disconnect-response-session-token-0001";
	unknown.expectedGeneration = 1;
	unknown.ttlMs = 60000;
	std::string body;
	std::string reason;
	assert(SessionSupervisorProtocol::EncodeRequest(unknown, body, reason));
	client = Connect(socketPath);
	assert(TypedToolProtocol::WriteFrame(client, body, 1000, reason));
	close(client);
}

void TestAuditJournalSecurity()
{
	const std::string path =
		TempPath("/tmp/hepta-supervisor-secure-audit-XXXXXX");
	SessionSupervisorAuditJournal journal;
	std::string reason;
	assert(journal.Init(path, reason));
	SessionSupervisorRequest request;
	request.operation = SessionSupervisorOperation::Provision;
	request.token = "audit-secret-session-token-0001";
	request.agentId = "audit-agent";
	request.sessionId = "audit-session";
	assert(journal.Append(
		request, "hepta.os.uid", "intent", "pending", 1, reason));
	std::uint64_t records = 0;
	assert(SessionSupervisorAuditJournal::Verify(path, records, reason));
	assert(records == 1);

	const int tamper = open(path.c_str(), O_RDWR | O_CLOEXEC);
	assert(tamper >= 0);
	assert(pwrite(tamper, "2", 1, 5) == 1);
	assert(fsync(tamper) == 0);
	close(tamper);
	assert(!SessionSupervisorAuditJournal::Verify(path, records, reason));
	assert(!journal.Append(
		request, "hepta.os.uid", "outcome", "accepted", 1, reason));

	const std::string legacyPath =
		TempPath("/tmp/hepta-supervisor-legacy-audit-XXXXXX");
	const int legacy = open(
		legacyPath.c_str(), O_WRONLY | O_CREAT | O_CLOEXEC, 0600);
	assert(legacy >= 0);
	const std::string legacyLine = "legacy-audit-record\n";
	assert(write(legacy, legacyLine.data(), legacyLine.size()) ==
		static_cast<ssize_t>(legacyLine.size()));
	close(legacy);
	SessionSupervisorAuditJournal migrated;
	assert(migrated.Init(legacyPath, reason));
	assert(migrated.Append(
		request, "hepta.os.uid", "outcome", "accepted", 1, reason));
	assert(SessionSupervisorAuditJournal::Verify(
		legacyPath, records, reason));
	assert(records == 1);

	const std::string hardlinkTarget =
		TempPath("/tmp/hepta-supervisor-hardlink-target-XXXXXX");
	const int target = open(
		hardlinkTarget.c_str(), O_WRONLY | O_CREAT | O_CLOEXEC, 0600);
	assert(target >= 0);
	close(target);
	const std::string hardlinkPath =
		TempPath("/tmp/hepta-supervisor-hardlink-path-XXXXXX");
	assert(link(hardlinkTarget.c_str(), hardlinkPath.c_str()) == 0);
	SessionSupervisorAuditJournal hardlinked;
	assert(!hardlinked.Init(hardlinkTarget, reason));
	unlink(hardlinkPath.c_str());

	const std::string symlinkPath =
		TempPath("/tmp/hepta-supervisor-symlink-XXXXXX");
	assert(symlink(hardlinkTarget.c_str(), symlinkPath.c_str()) == 0);
	SessionSupervisorAuditJournal symlinked;
	assert(!symlinked.Init(symlinkPath, reason));

	const std::string pinnedPath =
		TempPath("/tmp/hepta-supervisor-pinned-audit-XXXXXX");
	const std::string displacedPath =
		TempPath("/tmp/hepta-supervisor-displaced-audit-XXXXXX");
	SessionSupervisorAuditJournal pinned;
	assert(pinned.Init(pinnedPath, reason));
	assert(rename(pinnedPath.c_str(), displacedPath.c_str()) == 0);
	const int replacement = open(
		pinnedPath.c_str(), O_WRONLY | O_CREAT | O_CLOEXEC, 0600);
	assert(replacement >= 0);
	close(replacement);
	assert(!pinned.Append(
		request, "hepta.os.uid", "intent", "pending", 1, reason));

	unlink(pinnedPath.c_str());
	unlink(displacedPath.c_str());
	unlink(symlinkPath.c_str());
	unlink(hardlinkTarget.c_str());
	unlink(legacyPath.c_str());
	unlink(path.c_str());
}

void TestAuditJournalCacheAndGrowthBounds()
{
	SessionSupervisorRequest request;
	request.operation = SessionSupervisorOperation::Provision;
	request.agentId = "audit-cache-agent";
	request.sessionId = "audit-cache-session";
	std::string reason;
	std::uint64_t records = 0;

	const std::string sharedPath =
		TempPath("/tmp/hepta-supervisor-shared-audit-XXXXXX");
	SessionSupervisorAuditJournal first;
	SessionSupervisorAuditJournal second;
	assert(first.Init(sharedPath, reason));
	assert(first.Append(
		request, "hepta.os.uid", "intent", "pending", 1, reason));
	for (std::uint64_t generation = 2; generation <= 17; ++generation)
		assert(first.Append(
			request, "hepta.os.uid", "intent", "pending", generation, reason));
	assert(second.Init(sharedPath, reason));
	assert(second.Append(
		request, "hepta.os.uid", "outcome", "accepted", 17, reason));
	assert(first.Append(
		request, "hepta.os.uid", "intent", "pending", 18, reason));
	assert(SessionSupervisorAuditJournal::Verify(sharedPath, records, reason));
	assert(records == 19);

	const int injected = open(
		sharedPath.c_str(), O_WRONLY | O_APPEND | O_CLOEXEC);
	assert(injected >= 0);
	const std::string injectedLine = "unreviewed-external-record\n";
	assert(write(injected, injectedLine.data(), injectedLine.size()) ==
		static_cast<ssize_t>(injectedLine.size()));
	assert(fsync(injected) == 0);
	close(injected);
	assert(!first.Append(
		request, "hepta.os.uid", "outcome", "accepted", 18, reason));
	assert(reason == "SUPERVISOR_AUDIT_LEGACY_RECORD_AFTER_CHAIN");

	const std::string truncatedPath =
		TempPath("/tmp/hepta-supervisor-truncated-audit-XXXXXX");
	SessionSupervisorAuditJournal truncated;
	assert(truncated.Init(truncatedPath, reason));
	assert(truncated.Append(
		request, "hepta.os.uid", "intent", "pending", 1, reason));
	struct stat metadata;
	assert(stat(truncatedPath.c_str(), &metadata) == 0);
	assert(metadata.st_size > 1);
	assert(truncate(truncatedPath.c_str(), metadata.st_size - 1) == 0);
	assert(!truncated.Append(
		request, "hepta.os.uid", "outcome", "accepted", 1, reason));
	assert(reason == "SUPERVISOR_AUDIT_TRUNCATED_RECORD");

	const std::string oversizedPath =
		TempPath("/tmp/hepta-supervisor-oversized-audit-XXXXXX");
	const int oversized = open(
		oversizedPath.c_str(), O_WRONLY | O_CREAT | O_CLOEXEC, 0600);
	assert(oversized >= 0);
	assert(ftruncate(
		oversized, 1ULL * 1024ULL * 1024ULL * 1024ULL + 1ULL) == 0);
	close(oversized);
	SessionSupervisorAuditJournal bounded;
	assert(!bounded.Init(oversizedPath, reason));
	assert(reason == "SUPERVISOR_AUDIT_SIZE_LIMIT");
	assert(!SessionSupervisorAuditJournal::Verify(
		oversizedPath, records, reason));
	assert(reason == "SUPERVISOR_AUDIT_SIZE_LIMIT");

	unlink(oversizedPath.c_str());
	unlink(truncatedPath.c_str());
	unlink(sharedPath.c_str());
}

void TestSupervisorPeerCredentialAndLifecycle()
{
	const std::string journalPath = TempPath("/tmp/hepta-supervisor-journal-XXXXXX");
	const std::string socketPath = TempPath("/tmp/hepta-supervisor-socket-XXXXXX");
	OmsJournal journal;
	assert(journal.Init(journalPath));
	ExecutionCoordinatorCallbacks callbacks;
	ExecutionCoordinator execution(journal, callbacks);
	TradingToolRegistry registry(execution);
	TradingToolHost host(registry);
	TradingToolSessionControlPlane controlPlane(host,
		[](const std::string& issuer, const TradingToolHostSessionBinding&, std::string& reason) {
			if (issuer != "hepta.os.uid") { reason = "ISSUER_DENIED"; return false; }
			reason.clear();
			return true;
		});
	UnixSessionSupervisorServer server(controlPlane);
	std::map<std::uint32_t, std::string> issuers;
	issuers[static_cast<std::uint32_t>(getuid())] = "hepta.os.uid";
	std::string reason;
	assert(server.Start(socketPath, issuers,
		[](const SessionSupervisorRequest& request, TradingToolHostSessionBinding& binding,
		   std::string& rejectReason) {
			if (request.templateId != "watch" ||
				request.peerUid != static_cast<std::uint32_t>(getuid()))
			{
				rejectReason = "TEMPLATE_OR_AGENT_UID_DENIED";
				return false;
			}
			binding.token = request.token;
			binding.peerUid = request.peerUid;
			binding.session.executionContext.agentId = request.agentId;
			binding.session.executionContext.sessionId = request.sessionId;
			binding.session.executionContext.account = "DU123";
			binding.session.executionContext.venue = "IB";
			binding.session.environment = "WATCH";
			binding.session.capabilities.insert("market.read");
			binding.executionDomain = "IB-PAPER";
			binding.expiresAtMs = static_cast<std::uint64_t>(OmsJournal::NowEpochMs()) + request.ttlMs;
			rejectReason.clear();
			return true;
			}, reason, 4096, 1000));
	struct stat socketMetadata;
	assert(lstat(socketPath.c_str(), &socketMetadata) == 0);
	assert((socketMetadata.st_mode & 0777) == 0600);
	ExerciseDisconnectPhases(socketPath);

	SessionSupervisorRequest provision;
	provision.operation = SessionSupervisorOperation::Provision;
	provision.templateId = "watch";
	provision.token = "supervisor-session-token-0001";
	provision.agentId = "supervisor-agent";
	provision.sessionId = "supervisor-session";
	provision.peerUid = static_cast<std::uint32_t>(getuid());
	provision.ttlMs = 60000;
	SessionSupervisorResult result = Call(socketPath, provision);
	assert(result.accepted);
	assert(result.ReasonCode() == "OK");
	assert(result.leaseGeneration == 1);
	assert(host.SessionCount() == 1);

	SessionSupervisorRequest renew;
	renew.operation = SessionSupervisorOperation::Renew;
	renew.token = provision.token;
	renew.ttlMs = 120000;
	renew.expectedGeneration = 1;
	result = Call(socketPath, renew);
	assert(result.accepted);
	assert(result.ReasonCode() == "OK");
	assert(result.leaseGeneration == 2);
	result = Call(socketPath, renew);
	assert(!result.accepted);
	assert(result.ReasonCode() == "SESSION_LEASE_GENERATION_MISMATCH");

	SessionSupervisorRequest rotate;
	rotate.operation = SessionSupervisorOperation::Rotate;
	rotate.token = provision.token;
	rotate.replacementToken = "supervisor-session-token-rotated-0002";
	rotate.ttlMs = 120000;
	rotate.expectedGeneration = 2;
	result = Call(socketPath, rotate);
	assert(result.accepted);
	assert(result.ReasonCode() == "OK");
	assert(result.leaseGeneration == 3);
	TradingToolHostSessionBinding rotated;
	assert(!host.GetSession(provision.token, rotated));
	assert(host.GetSession(rotate.replacementToken, rotated));
	assert(rotated.leaseGeneration == 3);

	SessionSupervisorRequest revoke;
	revoke.operation = SessionSupervisorOperation::Revoke;
	revoke.token = rotate.replacementToken;
	revoke.expectedGeneration = 2;
	result = Call(socketPath, revoke);
	assert(!result.accepted);
	assert(result.ReasonCode() == "SESSION_LEASE_GENERATION_MISMATCH");
	revoke.expectedGeneration = 3;
	result = Call(socketPath, revoke);
	assert(result.accepted);
	assert(result.ReasonCode() == "OK");
	assert(result.leaseGeneration == 3);
	assert(host.SessionCount() == 0);
	server.Stop();

	issuers.clear();
	issuers[static_cast<std::uint32_t>(getuid()) + 1] = "hepta.os.uid";
	assert(server.Start(socketPath, issuers,
		[](const SessionSupervisorRequest&, TradingToolHostSessionBinding&, std::string&) {
			return false;
		}, reason, 4096, 1000));
	result = Call(socketPath, provision);
	assert(!result.accepted);
	assert(result.ReasonCode() == "SUPERVISOR_PEER_UID_DENIED");
	server.Stop();

	const int activatedFd = socket(AF_UNIX, SOCK_STREAM | SOCK_CLOEXEC, 0);
	assert(activatedFd >= 0);
	sockaddr_un activatedAddress;
	std::memset(&activatedAddress, 0, sizeof(activatedAddress));
	activatedAddress.sun_family = AF_UNIX;
	std::strncpy(activatedAddress.sun_path, socketPath.c_str(), sizeof(activatedAddress.sun_path) - 1);
	assert(bind(activatedFd, reinterpret_cast<sockaddr*>(&activatedAddress), sizeof(activatedAddress)) == 0);
	assert(listen(activatedFd, 8) == 0);
	issuers.clear();
	issuers[static_cast<std::uint32_t>(getuid())] = "hepta.os.uid";
	assert(server.StartFromFd(activatedFd, issuers,
		[](const SessionSupervisorRequest& request, TradingToolHostSessionBinding& binding,
		   std::string&) {
			binding.token = request.token;
			binding.peerUid = request.peerUid;
			binding.session.executionContext.agentId = request.agentId;
			binding.session.executionContext.sessionId = request.sessionId;
			binding.session.executionContext.account = "DU123";
			binding.session.environment = "WATCH";
			binding.executionDomain = "IB-PAPER";
			binding.expiresAtMs = static_cast<std::uint64_t>(OmsJournal::NowEpochMs()) + request.ttlMs;
			return true;
		}, reason, 4096, 1000));
	provision.token = "activated-supervisor-token-0001";
	result = Call(socketPath, provision);
	assert(result.accepted);
	server.Stop();
	assert(access(socketPath.c_str(), F_OK) == 0);
	// Stopping a consumer of a systemd-style duplicated descriptor must not
	// shutdown the manager-owned listening socket needed by the next process.
	const int restartProbe = Connect(socketPath);
	close(restartProbe);
	close(activatedFd);
	unlink(socketPath.c_str());

	std::remove(journalPath.c_str());
}

void TestDurableLiveExpiryReap()
{
	const std::string journalPath = TempPath("/tmp/hepta-supervisor-reap-journal-XXXXXX");
	const std::string socketPath = TempPath("/tmp/hepta-supervisor-reap-socket-XXXXXX");
	const std::string storePath = TempPath("/tmp/hepta-supervisor-reap-store-XXXXXX");
	const std::string keyPath = TempKeyPath();
	SessionSupervisorLeaseStore store;
	std::string reason;
	assert(store.Init(storePath, keyPath, reason));
	OmsJournal journal;
	assert(journal.Init(journalPath));
	ExecutionCoordinatorCallbacks callbacks;
	ExecutionCoordinator execution(journal, callbacks);
	TradingToolRegistry registry(execution);
	TradingToolHost host(registry);
	bool remoteFenceReady = false;
	std::size_t fenceAttempts = 0;
	host.SetSessionRevokedObserver(
		[&](const TradingToolHostSessionBinding& binding,
			const std::string& revokeReason,
			std::string& failureReason) {
			++fenceAttempts;
			assert(binding.session.executionContext.agentId == "reap-agent");
			assert(binding.session.executionContext.sessionId == "reap-session");
			assert(!binding.enabled);
			assert(revokeReason == "session_expired");
			if (!remoteFenceReady)
			{
				failureReason = "REMOTE_FENCE_UNAVAILABLE";
				return false;
			}
			failureReason.clear();
			return true;
		});
	TradingToolSessionControlPlane control(host,
		[](const std::string& issuer, const TradingToolHostSessionBinding&, std::string&) {
			return issuer == "hepta.os.uid";
		});
	UnixSessionSupervisorServer server(control);
	server.SetLeaseStore(&store);
	std::map<std::uint32_t, std::string> issuers;
	issuers[static_cast<std::uint32_t>(getuid())] = "hepta.os.uid";
	assert(server.Start(socketPath, issuers,
		[](const SessionSupervisorRequest& request, TradingToolHostSessionBinding& binding,
			std::string&) {
			binding.token = request.token;
			binding.peerUid = request.peerUid;
			binding.session.executionContext.agentId = request.agentId;
			binding.session.executionContext.sessionId = request.sessionId;
			binding.session.executionContext.account = "DU123";
			binding.session.environment = "WATCH";
			binding.executionDomain = "IB-PAPER";
			binding.expiresAtMs = static_cast<std::uint64_t>(OmsJournal::NowEpochMs()) +
				request.ttlMs;
			return true;
		}, reason, 4096, 1000));
	SessionSupervisorRequest provision;
	provision.operation = SessionSupervisorOperation::Provision;
	provision.templateId = "watch";
	provision.token = "durable-live-reap-session-token-0001";
	provision.agentId = "reap-agent";
	provision.sessionId = "reap-session";
	provision.peerUid = static_cast<std::uint32_t>(getuid());
	provision.ttlMs = 60000;
	assert(Call(socketPath, provision).accepted);
	const std::uint64_t expiresAtMs = store.List()[0].expiresAtMs;
	std::size_t reaped = 99;
	assert(server.ReapExpired(expiresAtMs - 1, reaped, reason));
	assert(reaped == 0 && store.List().size() == 1 && host.SessionCount() == 1);
	assert(!server.ReapExpired(expiresAtMs, reaped, reason));
	assert(reason == "REMOTE_FENCE_UNAVAILABLE");
	assert(reaped == 0 && store.List().size() == 1 && host.SessionCount() == 1);
	assert(store.List()[0].fencePending);
	assert(store.List()[0].fenceReason == "session_expired");
	TradingToolHostSessionBinding disabled;
	assert(host.GetSession(provision.token, disabled) && !disabled.enabled);
	SessionSupervisorRequest bypass = provision;
	bypass.token = "durable-live-reap-session-token-0002";
	assert(Call(socketPath, bypass).ReasonCode() == "SESSION_OWNER_FENCE_PENDING");
	remoteFenceReady = true;
	assert(server.ReapExpired(expiresAtMs, reaped, reason));
	assert(reaped == 1 && store.List().empty() && host.SessionCount() == 0);
	assert(fenceAttempts == 2);
	server.Stop();
	std::remove(storePath.c_str());
	std::remove(keyPath.c_str());
	std::remove(journalPath.c_str());
}

void TestWriteAheadActivationFailureFencesOwner()
{
	const std::string journalPath =
		TempPath("/tmp/hepta-supervisor-activation-journal-XXXXXX");
	const std::string socketPath =
		TempPath("/tmp/hepta-supervisor-activation-socket-XXXXXX");
	const std::string storePath =
		TempPath("/tmp/hepta-supervisor-activation-store-XXXXXX");
	const std::string keyPath = TempKeyPath();
	SessionSupervisorLeaseStore store;
	std::string reason;
	assert(store.Init(storePath, keyPath, reason));
	OmsJournal journal;
	assert(journal.Init(journalPath));
	ExecutionCoordinatorCallbacks callbacks;
	ExecutionCoordinator execution(journal, callbacks);
	TradingToolRegistry registry(execution);
	TradingToolHost host(registry);
	bool remoteFenceReady = false;
	host.SetSessionRevokedObserver(
		[&](const TradingToolHostSessionBinding& binding,
			const std::string& revokeReason,
			std::string& failureReason) {
			assert(!binding.enabled);
			assert(revokeReason == "session_revoked");
			if (!remoteFenceReady)
			{
				failureReason = "REMOTE_FENCE_UNAVAILABLE";
				return false;
			}
			failureReason.clear();
			return true;
		});
	TradingToolSessionControlPlane control(host,
		[](const std::string& issuer,
			const TradingToolHostSessionBinding&, std::string&) {
			return issuer == "hepta.os.uid";
		});
	UnixSessionSupervisorServer server(control);
	server.SetLeaseStore(&store);
	std::map<std::uint32_t, std::string> issuers;
	issuers[static_cast<std::uint32_t>(getuid())] = "hepta.os.uid";
	assert(server.Start(socketPath, issuers,
		[](const SessionSupervisorRequest& request,
			TradingToolHostSessionBinding& binding, std::string&) {
			binding.token = request.token;
			binding.peerUid = request.peerUid;
			binding.session.executionContext.agentId = request.agentId;
			binding.session.executionContext.sessionId = request.sessionId;
			binding.session.executionContext.account = "DU123";
			binding.session.environment = "WATCH";
			binding.executionDomain = "IB-PAPER";
			binding.expiresAtMs =
				static_cast<std::uint64_t>(OmsJournal::NowEpochMs()) +
				request.ttlMs;
			return true;
		}, reason, 4096, 1000));

	server.SetCrashPointHook([](const std::string& point) {
		return point == "before_lease_activation_commit";
	});
	SessionSupervisorRequest provision;
	provision.operation = SessionSupervisorOperation::Provision;
	provision.templateId = "watch";
	provision.token = "activation-failure-session-token-0001";
	provision.agentId = "activation-failure-agent";
	provision.sessionId = "activation-failure-session";
	provision.peerUid = static_cast<std::uint32_t>(getuid());
	provision.ttlMs = 60000;
	SessionSupervisorResult result = Call(socketPath, provision);
	assert(!result.accepted && result.ReasonCode() == "REMOTE_FENCE_UNAVAILABLE");
	assert(store.List().size() == 1 && store.List()[0].fencePending);
	TradingToolHostSessionBinding disabled;
	assert(host.GetSession(provision.token, disabled) && !disabled.enabled);
	SessionSupervisorRequest bypass = provision;
	bypass.token = "activation-failure-session-token-0002";
	assert(Call(socketPath, bypass).ReasonCode() == "SESSION_OWNER_FENCE_PENDING");

	server.SetCrashPointHook([](const std::string&) { return false; });
	remoteFenceReady = true;
	std::size_t reaped = 0;
	assert(server.ReapExpired(0, reaped, reason));
	assert(reaped == 1 && store.List().empty() && host.SessionCount() == 0);

	provision.token = "activation-renew-session-token-0001";
	provision.agentId = "activation-renew-agent";
	provision.sessionId = "activation-renew-session";
	result = Call(socketPath, provision);
	assert(result.accepted && result.leaseGeneration == 1);
	remoteFenceReady = false;
	server.SetCrashPointHook([](const std::string& point) {
		return point == "before_lease_activation_commit";
	});
	SessionSupervisorRequest renew;
	renew.operation = SessionSupervisorOperation::Renew;
	renew.token = provision.token;
	renew.expectedGeneration = 1;
	renew.ttlMs = 120000;
	result = Call(socketPath, renew);
	assert(!result.accepted && result.ReasonCode() == "REMOTE_FENCE_UNAVAILABLE");
	assert(store.List().size() == 1 && store.List()[0].fencePending);
	assert(store.List()[0].leaseGeneration == 2);
	assert(host.GetSession(provision.token, disabled));
	assert(!disabled.enabled && disabled.leaseGeneration == 2);
	remoteFenceReady = true;
	server.SetCrashPointHook([](const std::string&) { return false; });
	assert(server.ReapExpired(0, reaped, reason));
	assert(reaped == 1 && store.List().empty() && host.SessionCount() == 0);

	server.Stop();
	std::remove(storePath.c_str());
	std::remove(keyPath.c_str());
	std::remove(journalPath.c_str());
}

void TestRejectedRenewFenceRetryUsesExactPredecessorScope()
{
	const std::string journalPath =
		TempPath("/tmp/hepta-supervisor-rejected-renew-journal-XXXXXX");
	const std::string socketPath =
		TempPath("/tmp/hepta-supervisor-rejected-renew-socket-XXXXXX");
	const std::string storePath =
		TempPath("/tmp/hepta-supervisor-rejected-renew-store-XXXXXX");
	const std::string keyPath = TempKeyPath();
	SessionSupervisorLeaseStore store;
	std::string reason;
	assert(store.Init(storePath, keyPath, reason));
	OmsJournal journal;
	assert(journal.Init(journalPath));
	ExecutionCoordinatorCallbacks callbacks;
	ExecutionCoordinator execution(journal, callbacks);
	TradingToolRegistry registry(execution);
	TradingToolHost host(registry);
	bool rejectLeaseMutation = false;
	bool remoteFenceReady = true;
	std::size_t fenceAttempts = 0;
	host.SetSessionRevokedObserver(
		[&](const TradingToolHostSessionBinding& binding,
			const std::string& revokeReason,
			std::string& failureReason) {
			++fenceAttempts;
			assert(binding.session.executionContext.agentId ==
				"rejected-renew-agent");
			assert(binding.session.executionContext.sessionId ==
				"rejected-renew-session");
			assert(!binding.enabled);
			assert(revokeReason == "session_revoked");
			if (!remoteFenceReady)
			{
				failureReason = "REMOTE_FENCE_UNAVAILABLE";
				return false;
			}
			failureReason.clear();
			return true;
		});
	TradingToolSessionControlPlane control(host,
		[&](const std::string& issuer,
			const TradingToolHostSessionBinding& binding,
			std::string& rejectReason) {
			if (issuer != "hepta.os.uid")
			{
				rejectReason = "ISSUER_DENIED";
				return false;
			}
			// Revoke authorization supplies token identity only. Reject the
			// replacement lease after its write-ahead generation is durable,
			// while keeping the owner-fence recovery path authorized.
			if (rejectLeaseMutation && binding.expiresAtMs != 0)
			{
				rejectReason = "LEASE_UPDATE_DENIED";
				return false;
			}
			rejectReason.clear();
			return true;
		});
	UnixSessionSupervisorServer server(control);
	server.SetLeaseStore(&store);
	std::map<std::uint32_t, std::string> issuers;
	issuers[static_cast<std::uint32_t>(getuid())] = "hepta.os.uid";
	assert(server.Start(socketPath, issuers,
		[](const SessionSupervisorRequest& request,
			TradingToolHostSessionBinding& binding, std::string&) {
			binding.token = request.token;
			binding.peerUid = request.peerUid;
			binding.session.executionContext.agentId = request.agentId;
			binding.session.executionContext.sessionId = request.sessionId;
			binding.session.executionContext.account = "DU123";
			binding.session.environment = "WATCH";
			binding.executionDomain = "IB-PAPER";
			binding.expiresAtMs =
				static_cast<std::uint64_t>(OmsJournal::NowEpochMs()) +
				request.ttlMs;
			return true;
		}, reason, 4096, 1000));

	SessionSupervisorRequest provision;
	provision.operation = SessionSupervisorOperation::Provision;
	provision.templateId = "watch";
	provision.token = "rejected-renew-session-token-0001";
	provision.agentId = "rejected-renew-agent";
	provision.sessionId = "rejected-renew-session";
	provision.peerUid = static_cast<std::uint32_t>(getuid());
	provision.ttlMs = 60000;
	const SessionSupervisorResult provisioned = Call(socketPath, provision);
	assert(provisioned.accepted && provisioned.leaseGeneration == 1);

	rejectLeaseMutation = true;
	remoteFenceReady = false;
	SessionSupervisorRequest renew;
	renew.operation = SessionSupervisorOperation::Renew;
	renew.token = provision.token;
	renew.expectedGeneration = 1;
	renew.ttlMs = 120000;
	const SessionSupervisorResult rejected = Call(socketPath, renew);
	assert(!rejected.accepted);
	assert(rejected.ReasonCode() == "REMOTE_FENCE_UNAVAILABLE");
	assert(fenceAttempts == 1);
	assert(store.List().size() == 1);
	assert(store.List()[0].fencePending);
	assert(store.List()[0].leaseGeneration == 2);
	assert(store.List()[0].predecessorToken == provision.token);
	assert(store.List()[0].predecessorGeneration == 1);
	SessionSupervisorLeaseStore reopenedStore;
	assert(reopenedStore.Init(storePath, keyPath, reason));
	assert(reopenedStore.List().size() == 1);
	assert(reopenedStore.List()[0].predecessorToken == provision.token);
	assert(reopenedStore.List()[0].predecessorGeneration == 1);
	TradingToolHostSessionBinding current;
	assert(host.GetSession(provision.token, current));
	assert(!current.enabled && current.leaseGeneration == 1);

	remoteFenceReady = true;
	std::size_t reaped = 0;
	assert(server.ReapExpired(0, reaped, reason));
	assert(reaped == 1);
	assert(store.List().empty() && host.SessionCount() == 0);
	assert(fenceAttempts == 2);

	server.Stop();
	std::remove(storePath.c_str());
	std::remove(keyPath.c_str());
	std::remove(journalPath.c_str());
}

void TestReapSerializesWithRenewCommit()
{
	const std::string journalPath =
		TempPath("/tmp/hepta-supervisor-race-journal-XXXXXX");
	const std::string socketPath =
		TempPath("/tmp/hepta-supervisor-race-socket-XXXXXX");
	const std::string storePath =
		TempPath("/tmp/hepta-supervisor-race-store-XXXXXX");
	const std::string keyPath = TempKeyPath();
	SessionSupervisorLeaseStore store;
	std::string reason;
	assert(store.Init(storePath, keyPath, reason));
	OmsJournal journal;
	assert(journal.Init(journalPath));
	ExecutionCoordinatorCallbacks callbacks;
	ExecutionCoordinator execution(journal, callbacks);
	TradingToolRegistry registry(execution);
	TradingToolHost host(registry);
	TradingToolSessionControlPlane control(host,
		[](const std::string& issuer,
			const TradingToolHostSessionBinding&, std::string&) {
			return issuer == "hepta.os.uid";
		});
	UnixSessionSupervisorServer server(control);
	server.SetLeaseStore(&store);
	std::map<std::uint32_t, std::string> issuers;
	issuers[static_cast<std::uint32_t>(getuid())] = "hepta.os.uid";
	assert(server.Start(socketPath, issuers,
		[](const SessionSupervisorRequest& request,
			TradingToolHostSessionBinding& binding, std::string&) {
			binding.token = request.token;
			binding.peerUid = request.peerUid;
			binding.session.executionContext.agentId = request.agentId;
			binding.session.executionContext.sessionId = request.sessionId;
			binding.session.executionContext.account = "DU123";
			binding.session.environment = "WATCH";
			binding.executionDomain = "IB-PAPER";
			binding.expiresAtMs =
				static_cast<std::uint64_t>(OmsJournal::NowEpochMs()) +
				request.ttlMs;
			return true;
		}, reason, 4096, 1000));
	SessionSupervisorRequest provision;
	provision.operation = SessionSupervisorOperation::Provision;
	provision.templateId = "watch";
	provision.token = "serialized-renew-session-token-0001";
	provision.agentId = "serialized-renew-agent";
	provision.sessionId = "serialized-renew-session";
	provision.peerUid = static_cast<std::uint32_t>(getuid());
	provision.ttlMs = 60000;
	assert(Call(socketPath, provision).accepted);
	const std::uint64_t oldExpiry = store.List()[0].expiresAtMs;

	std::atomic<bool> commitReached(false);
	std::atomic<bool> releaseCommit(false);
	std::atomic<bool> reapFinished(false);
	server.SetCrashPointHook([&](const std::string& point) {
		if (point != "after_lease_commit") return false;
		commitReached.store(true);
		while (!releaseCommit.load()) usleep(1000);
		return false;
	});
	SessionSupervisorRequest renew;
	renew.operation = SessionSupervisorOperation::Renew;
	renew.token = provision.token;
	renew.expectedGeneration = 1;
	renew.ttlMs = 120000;
	SessionSupervisorResult renewed;
	std::thread renewThread([&]() { renewed = Call(socketPath, renew); });
	for (int i = 0; i < 2000 && !commitReached.load(); ++i) usleep(1000);
	assert(commitReached.load());
	std::size_t reaped = 99;
	std::string reapReason;
	bool reapAccepted = false;
	std::thread reapThread([&]() {
		reapAccepted = server.ReapExpired(oldExpiry + 1, reaped, reapReason);
		reapFinished.store(true);
	});
	usleep(50000);
	assert(!reapFinished.load());
	releaseCommit.store(true);
	renewThread.join();
	reapThread.join();
	assert(renewed.accepted && renewed.leaseGeneration == 2);
	assert(reapAccepted && reaped == 0);
	assert(store.List().size() == 1 && !store.List()[0].fencePending);
	assert(store.List()[0].leaseGeneration == 2);
	TradingToolHostSessionBinding active;
	assert(host.GetSession(provision.token, active));
	assert(active.enabled && active.leaseGeneration == 2);

	server.Stop();
	std::remove(storePath.c_str());
	std::remove(keyPath.c_str());
	std::remove(journalPath.c_str());
}

void TestDurablePendingFenceRestartRecoveryWithReviewedPolicy()
{
	const std::string journalPath =
		TempPath("/tmp/hepta-supervisor-pending-journal-XXXXXX");
	const std::string socketPath =
		TempPath("/tmp/hepta-supervisor-pending-socket-XXXXXX");
	const std::string storePath =
		TempPath("/tmp/hepta-supervisor-pending-store-XXXXXX");
	const std::string keyPath = TempKeyPath();
	std::string reason;

	AgentOsRuntimeConfig agentConfig;
	agentConfig.agentUid = static_cast<std::uint32_t>(getuid());
	agentConfig.supervisorMaxTtlMs = 3600000;
	agentConfig.toolSocket = "/tmp/hepta-policy-tool.sock";
	agentConfig.supervisorSocket = socketPath;
	ExecutionGatewayRuntimeConfig executionConfig;
	executionConfig.mode = ExecutionGatewayMode::Simulator;
	executionConfig.executionSocket = "/tmp/hepta-policy-execution.sock";
	executionConfig.eventSocket = "/tmp/hepta-policy-events.sock";
	executionConfig.executionServiceUid =
		static_cast<std::uint32_t>(getuid());
	executionConfig.executionServiceUidConfigured = true;
	std::map<std::string, std::string> values;
	values["HEPTA_TOOL_AGENT_ID"] = "pending-restart-agent";
	values["HEPTA_TOOL_ACCOUNT"] = "SIM";
	ToolGatewaySessionPolicy policy;
	assert(ToolGatewaySessionPolicy::FromValues(
		values, executionConfig, agentConfig, policy, reason));
	auto resolver = [&policy](const SessionSupervisorRequest& request,
		TradingToolHostSessionBinding& binding, std::string& resolveReason) {
		return policy.Resolve(request, binding, resolveReason);
	};
	auto authorizer = [&policy](const std::string& issuer,
		const TradingToolHostSessionBinding& binding, std::string& authorizeReason) {
		return policy.Authorize(issuer, binding, authorizeReason);
	};
	std::map<std::uint32_t, std::string> issuers;
	issuers[static_cast<std::uint32_t>(getuid())] = "hepta.os.bootstrap";

	OmsJournal journal;
	assert(journal.Init(journalPath));
	ExecutionCoordinatorCallbacks callbacks;
	ExecutionCoordinator execution(journal, callbacks);
	TradingToolRegistry registry(execution);
	SessionSupervisorRequest provision;
	provision.operation = SessionSupervisorOperation::Provision;
	provision.templateId = "watch";
	provision.token = "durable-pending-session-token-0001";
	provision.agentId = "pending-restart-agent";
	provision.sessionId = "pending-restart-session";
	provision.peerUid = static_cast<std::uint32_t>(getuid());
	provision.ttlMs = 60000;

	{
		SessionSupervisorLeaseStore store;
		assert(store.Init(storePath, keyPath, reason));
		TradingToolHost host(registry);
		host.SetSessionRevokedObserver(
			[](const TradingToolHostSessionBinding& binding,
				const std::string& revokeReason,
				std::string& failureReason) {
				assert(!binding.enabled);
				assert(revokeReason == "session_revoked");
				failureReason = "REMOTE_FENCE_UNAVAILABLE";
				return false;
			});
		TradingToolSessionControlPlane control(host, authorizer);
		UnixSessionSupervisorServer server(control);
		server.SetLeaseStore(&store);
		assert(server.Start(
			socketPath, issuers, resolver, reason, 4096, 1000, 3600000));
		const SessionSupervisorResult provisioned = Call(socketPath, provision);
		assert(provisioned.accepted);
		SessionSupervisorRequest revoke;
		revoke.operation = SessionSupervisorOperation::Revoke;
		revoke.token = provision.token;
		revoke.expectedGeneration = provisioned.leaseGeneration;
		const SessionSupervisorResult rejected = Call(socketPath, revoke);
		assert(!rejected.accepted);
		assert(rejected.ReasonCode() == "REMOTE_FENCE_UNAVAILABLE");
		assert(store.List().size() == 1);
		assert(store.List()[0].fencePending);
		assert(store.List()[0].fenceReason == "session_revoked");
		TradingToolHostSessionBinding disabled;
		assert(host.GetSession(provision.token, disabled));
		assert(!disabled.enabled);
		SessionSupervisorRequest rotate = revoke;
		rotate.operation = SessionSupervisorOperation::Rotate;
		rotate.replacementToken = "durable-pending-session-token-0002";
		rotate.ttlMs = 60000;
		assert(Call(socketPath, rotate).ReasonCode() ==
			"SESSION_OWNER_FENCE_PENDING");
		SessionSupervisorRequest bypass = provision;
		bypass.token = "durable-pending-session-token-0003";
		assert(Call(socketPath, bypass).ReasonCode() ==
			"SESSION_OWNER_FENCE_PENDING");
		server.Stop();
	}

	// Remaining TTL is now below the reviewed 60-second provisioning floor.
	// Recovery must reconstruct the identity without re-enabling it.
	{
		SessionSupervisorLeaseStore store;
		assert(store.Init(storePath, keyPath, reason));
		TradingToolHost host(registry);
		host.SetSessionRevokedObserver(
			[](const TradingToolHostSessionBinding&,
				const std::string&, std::string& failureReason) {
				failureReason = "REMOTE_FENCE_STILL_UNAVAILABLE";
				return false;
			});
		TradingToolSessionControlPlane control(host, authorizer);
		UnixSessionSupervisorServer server(control);
		server.SetLeaseStore(&store);
		assert(!server.Start(
			socketPath, issuers, resolver, reason, 4096, 1000, 3600000));
		assert(reason == "REMOTE_FENCE_STILL_UNAVAILABLE");
		assert(host.SessionCount() == 0);
		assert(store.List().size() == 1 && store.List()[0].fencePending);
	}

	{
		SessionSupervisorLeaseStore store;
		assert(store.Init(storePath, keyPath, reason));
		TradingToolHost host(registry);
		std::size_t recovered = 0;
		host.SetSessionRevokedObserver(
			[&](const TradingToolHostSessionBinding& binding,
				const std::string& revokeReason,
				std::string& failureReason) {
				++recovered;
				assert(!binding.enabled);
				assert(revokeReason == "session_revoked");
				assert(binding.session.executionContext.agentId ==
					"pending-restart-agent");
				failureReason.clear();
				return true;
			});
		TradingToolSessionControlPlane control(host, authorizer);
		UnixSessionSupervisorServer server(control);
		server.SetLeaseStore(&store);
		assert(server.Start(
			socketPath, issuers, resolver, reason, 4096, 1000, 3600000));
		assert(recovered == 1);
		assert(store.List().size() == 1 && host.SessionCount() == 0);
		assert(store.List()[0].fencePending);
		assert(store.List()[0].fenceComplete);
		SessionSupervisorRequest recoveredRevoke;
		recoveredRevoke.operation = SessionSupervisorOperation::Revoke;
		recoveredRevoke.token = provision.token;
		recoveredRevoke.expectedGeneration =
			store.List()[0].leaseGeneration;
		const SessionSupervisorResult recoveredResult =
			Call(socketPath, recoveredRevoke);
		assert(recoveredResult.accepted);
		assert(recoveredResult.ReasonCode() == "OK");
		assert(store.List().empty());
		server.Stop();

		SessionSupervisorLeaseRecord expired;
		expired.templateId = "watch";
		expired.issuer = "hepta.os.bootstrap";
		expired.token = "durable-expired-session-token-0001";
		expired.agentId = "pending-restart-agent";
		expired.sessionId = "expired-restart-session";
		expired.peerUid = static_cast<std::uint32_t>(getuid());
		expired.expiresAtMs =
			static_cast<std::uint64_t>(OmsJournal::NowEpochMs()) - 1;
		expired.leaseGeneration = 1;
		assert(store.Put(expired, reason));
	}

	{
		SessionSupervisorLeaseStore store;
		assert(store.Init(storePath, keyPath, reason));
		TradingToolHost host(registry);
		std::size_t expiredFences = 0;
		host.SetSessionRevokedObserver(
			[&](const TradingToolHostSessionBinding& binding,
				const std::string& revokeReason,
				std::string& failureReason) {
				++expiredFences;
				assert(!binding.enabled);
				assert(revokeReason == "session_expired");
					assert(binding.session.executionContext.agentId ==
						"pending-restart-agent");
				failureReason.clear();
				return true;
			});
		TradingToolSessionControlPlane control(host, authorizer);
		UnixSessionSupervisorServer server(control);
		server.SetLeaseStore(&store);
		assert(server.Start(
			socketPath, issuers, resolver, reason, 4096, 1000, 3600000));
		assert(expiredFences == 1);
		assert(store.List().empty() && host.SessionCount() == 0);
		server.Stop();
	}

	std::remove(storePath.c_str());
	std::remove(keyPath.c_str());
	std::remove(journalPath.c_str());
}

void TestDeterministicGenerationSchedules()
{
	const std::string journalPath = TempPath("/tmp/hepta-supervisor-seed-journal-XXXXXX");
	const std::string socketPath = TempPath("/tmp/hepta-supervisor-seed-socket-XXXXXX");
	const std::string storePath = TempPath("/tmp/hepta-supervisor-seed-store-XXXXXX");
	const std::string keyPath = TempKeyPath();
	SessionSupervisorLeaseStore store;
	std::string reason;
	assert(store.Init(storePath, keyPath, reason));
	OmsJournal journal;
	assert(journal.Init(journalPath));
	ExecutionCoordinatorCallbacks callbacks;
	ExecutionCoordinator execution(journal, callbacks);
	TradingToolRegistry registry(execution);
	TradingToolHost host(registry);
	TradingToolSessionControlPlane control(host,
		[](const std::string& issuer, const TradingToolHostSessionBinding&, std::string&) {
			return issuer == "hepta.os.seed";
		});
	UnixSessionSupervisorServer server(control);
	server.SetLeaseStore(&store);
	std::map<std::uint32_t, std::string> issuers;
	issuers[static_cast<std::uint32_t>(getuid())] = "hepta.os.seed";
	assert(server.Start(socketPath, issuers,
		[](const SessionSupervisorRequest& request, TradingToolHostSessionBinding& binding,
			std::string&) {
			binding.token = request.token;
			binding.peerUid = request.peerUid;
			binding.session.executionContext.agentId = request.agentId;
			binding.session.executionContext.sessionId = request.sessionId;
			binding.session.executionContext.account = "DU123";
			binding.session.environment = "WATCH";
			binding.executionDomain = "IB-PAPER";
			binding.expiresAtMs = static_cast<std::uint64_t>(OmsJournal::NowEpochMs()) +
				request.ttlMs;
			return true;
		}, reason, 4096, 1000));
	for (std::uint32_t seed = 1; seed <= 4; ++seed)
	{
		SessionSupervisorRequest session;
		session.operation = SessionSupervisorOperation::Provision;
		session.templateId = "watch";
		session.token = "seeded-generation-session-token-" + std::to_string(seed);
		session.agentId = "seed-agent-" + std::to_string(seed);
		session.sessionId = "seed-session-" + std::to_string(seed);
		session.peerUid = static_cast<std::uint32_t>(getuid());
		session.ttlMs = 60000;
		assert(Call(socketPath, session).accepted);
		std::uint64_t generation = 1;
		std::uint32_t state = seed * 0x9e3779b9U;
		for (int step = 0; step < 16; ++step)
		{
			state ^= state << 13;
			state ^= state >> 17;
			state ^= state << 5;
			SessionSupervisorRequest mutation = session;
			mutation.expectedGeneration = generation;
			mutation.ttlMs = 60000;
			if ((state & 1U) == 0)
				mutation.operation = SessionSupervisorOperation::Renew;
			else
			{
				mutation.operation = SessionSupervisorOperation::Rotate;
				mutation.replacementToken = mutation.token + "-g" +
					std::to_string(generation + 1);
			}
			const SessionSupervisorResult changed = Call(socketPath, mutation);
			assert(changed.accepted && changed.leaseGeneration == generation + 1);
			assert(!Call(socketPath, mutation).accepted);
			generation = changed.leaseGeneration;
			if (mutation.operation == SessionSupervisorOperation::Rotate)
				session.token = mutation.replacementToken;
		}
		session.operation = SessionSupervisorOperation::Revoke;
		session.expectedGeneration = generation;
		assert(Call(socketPath, session).accepted);
	}
	assert(store.List().empty());
	assert(host.SessionCount() == 0);
	server.Stop();
	std::remove(storePath.c_str());
	std::remove(keyPath.c_str());
	std::remove(journalPath.c_str());
}

void TestRestartAtEveryPersistedGeneration()
{
	const std::string journalPath = TempPath("/tmp/hepta-supervisor-generation-journal-XXXXXX");
	const std::string socketPath = TempPath("/tmp/hepta-supervisor-generation-socket-XXXXXX");
	const std::string storePath = TempPath("/tmp/hepta-supervisor-generation-store-XXXXXX");
	const std::string keyPath = TempKeyPath();
	SessionSupervisorLeaseStore store;
	std::string reason;
	assert(store.Init(storePath, keyPath, reason));
	OmsJournal journal;
	assert(journal.Init(journalPath));
	ExecutionCoordinatorCallbacks callbacks;
	ExecutionCoordinator execution(journal, callbacks);
	TradingToolRegistry registry(execution);
	std::map<std::uint32_t, std::string> issuers;
	issuers[static_cast<std::uint32_t>(getuid())] = "hepta.os.restart";
	auto resolver = [](const SessionSupervisorRequest& request,
		TradingToolHostSessionBinding& binding, std::string&) {
		binding.token = request.token;
		binding.peerUid = request.peerUid;
		binding.session.executionContext.agentId = request.agentId;
		binding.session.executionContext.sessionId = request.sessionId;
		binding.session.executionContext.account = "DU123";
		binding.session.environment = "PAPER";
		binding.executionDomain = "IB-PAPER";
		binding.expiresAtMs = static_cast<std::uint64_t>(OmsJournal::NowEpochMs()) +
			request.ttlMs;
		return true;
	};
	auto authorizer = [](const std::string& issuer,
		const TradingToolHostSessionBinding&, std::string&) {
		return issuer == "hepta.os.restart";
	};
	auto exactZeroAudit = [](const ExecutionControlCommand& command) {
		ExecutionControlResult result;
		result.commandId = command.context.toolCallId;
		result.status = ExecutionCommandStatus::Accepted;
		result.ownerAuditAuthoritative = true;
		result.ownerAuditComplete = true;
		result.ownerAccount = command.context.account;
		result.ownerExecutionDomain = command.context.executionDomain;
		result.brokerConnectionEpoch = 41;
		result.brokerActiveGeneration = 7;
		result.brokerTerminalGeneration = 11;
		return result;
	};
	auto zeroFence = [](const ExecutionControlCommand& command) {
		ExecutionControlResult result;
		result.commandId = command.context.toolCallId;
		result.status = ExecutionCommandStatus::Accepted;
		return result;
	};
	std::string token = "restart-generation-session-token-0001";
	std::uint64_t generation = 1;
	{
		TradingToolHost host(registry);
		RecoveryControlAuthority recoveryAuthority;
		recoveryAuthority.ownerAudit = exactZeroAudit;
		recoveryAuthority.fence = zeroFence;
		host.SetRecoveryControlAuthority(&recoveryAuthority);
		TradingToolSessionControlPlane control(host, authorizer);
		UnixSessionSupervisorServer server(control);
		server.SetLeaseStore(&store);
		assert(server.Start(socketPath, issuers, resolver, reason, 4096, 1000));
		SessionSupervisorRequest provision;
		provision.operation = SessionSupervisorOperation::Provision;
		provision.templateId = "paper";
		provision.token = token;
		provision.agentId = "restart-agent";
		provision.sessionId = "restart-session";
		provision.peerUid = static_cast<std::uint32_t>(getuid());
		provision.ttlMs = 60000;
		assert(Call(socketPath, provision).accepted);
		server.Stop();
	}
	for (int step = 0; step < 8; ++step)
	{
		TradingToolHost host(registry);
		RecoveryControlAuthority recoveryAuthority;
		recoveryAuthority.ownerAudit = exactZeroAudit;
		recoveryAuthority.fence = zeroFence;
		host.SetRecoveryControlAuthority(&recoveryAuthority);
		TradingToolSessionControlPlane control(host, authorizer);
		UnixSessionSupervisorServer server(control);
		server.SetLeaseStore(&store);
		assert(server.Start(socketPath, issuers, resolver, reason, 4096, 1000));
		TradingToolHostSessionBinding restored;
		assert(host.GetSession(token, restored));
		assert(restored.leaseGeneration == generation);
		SessionSupervisorRequest mutation;
		mutation.token = token;
		mutation.expectedGeneration = generation;
		mutation.ttlMs = 60000;
		if ((step & 1) == 0)
			mutation.operation = SessionSupervisorOperation::Renew;
		else
		{
			mutation.operation = SessionSupervisorOperation::Rotate;
			mutation.replacementToken = token + "-g" + std::to_string(generation + 1);
		}
		const SessionSupervisorResult result = Call(socketPath, mutation);
		assert(result.accepted && result.leaseGeneration == generation + 1);
		generation = result.leaseGeneration;
		if (mutation.operation == SessionSupervisorOperation::Rotate)
			token = mutation.replacementToken;
		server.Stop();
	}
	{
		TradingToolHost host(registry);
		RecoveryControlAuthority recoveryAuthority;
		recoveryAuthority.ownerAudit = exactZeroAudit;
		recoveryAuthority.fence = zeroFence;
		host.SetRecoveryControlAuthority(&recoveryAuthority);
		TradingToolSessionControlPlane control(host, authorizer);
		UnixSessionSupervisorServer server(control);
		server.SetLeaseStore(&store);
		assert(server.Start(socketPath, issuers, resolver, reason, 4096, 1000));
		TradingToolHostSessionBinding restored;
		assert(host.GetSession(token, restored));
		assert(restored.leaseGeneration == generation);
		SessionSupervisorRequest revoke;
		revoke.operation = SessionSupervisorOperation::Revoke;
		revoke.token = token;
		revoke.expectedGeneration = generation;
		const SessionSupervisorResult legacyRevoke =
			Call(socketPath, revoke);
		assert(legacyRevoke.accepted);
		assert(legacyRevoke.ReasonCode() == "OK");
		assert(host.SessionCount() == 0 && store.List().empty());
		server.Stop();
	}
	std::remove(storePath.c_str());
	std::remove(keyPath.c_str());
	std::remove(journalPath.c_str());
}

void TestWatchLeaseRestartFailsClosed()
{
	const std::string journalPath = TempPath("/tmp/hepta-supervisor-restore-journal-XXXXXX");
	const std::string socketPath = TempPath("/tmp/hepta-supervisor-restore-socket-XXXXXX");
	const std::string storePath = TempPath("/tmp/hepta-supervisor-lease-store-XXXXXX");
	const std::string keyPath = TempKeyPath();
	SessionSupervisorLeaseStore store;
	std::string reason;
	assert(store.Init(storePath, keyPath, reason));
	struct stat storeMetadata;
	assert(stat(storePath.c_str(), &storeMetadata) == 0);
	assert((storeMetadata.st_mode & 0077) == 0);
	std::map<std::uint32_t, std::string> issuers;
	issuers[static_cast<std::uint32_t>(getuid())] = "hepta.os.uid";
	auto resolver = [](const SessionSupervisorRequest& request,
		TradingToolHostSessionBinding& binding, std::string&) {
		binding.token = request.token;
		binding.peerUid = request.peerUid;
		binding.session.executionContext.agentId = request.agentId;
		binding.session.executionContext.sessionId = request.sessionId;
		binding.session.executionContext.account = "DU123";
		binding.session.environment = "WATCH";
		binding.executionDomain = "IB-PAPER";
		binding.expiresAtMs = static_cast<std::uint64_t>(OmsJournal::NowEpochMs()) + request.ttlMs;
		return true;
	};
	OmsJournal journal;
	assert(journal.Init(journalPath));
	ExecutionCoordinatorCallbacks callbacks;
	ExecutionCoordinator execution(journal, callbacks);
	TradingToolRegistry registry(execution);
	TradingToolHost firstHost(registry);
	TradingToolSessionControlPlane firstControl(firstHost,
		[](const std::string& issuer, const TradingToolHostSessionBinding&, std::string&) {
			return issuer == "hepta.os.uid";
		});
	UnixSessionSupervisorServer firstServer(firstControl);
	firstServer.SetLeaseStore(&store);
	assert(firstServer.Start(socketPath, issuers, resolver, reason, 4096, 1000));
	SessionSupervisorRequest provision;
	provision.operation = SessionSupervisorOperation::Provision;
	provision.templateId = "watch";
	provision.token = "durable-supervisor-token-0001";
	provision.agentId = "durable-agent";
	provision.sessionId = "durable-session";
	provision.peerUid = static_cast<std::uint32_t>(getuid());
	provision.ttlMs = 60000;
	assert(Call(socketPath, provision).accepted);
	firstServer.Stop();

	// WATCH runtime bearer material lives below /run and may disappear on a
	// host reboot. A restart must first persist a pending fence; inability to
	// complete the remote owner fence prevents Gateway activation.
	TradingToolHost failedHost(registry);
	failedHost.SetSessionRevokedObserver(
		[](const TradingToolHostSessionBinding& binding,
			const std::string& revokeReason, std::string& failureReason) {
			assert(!binding.enabled);
			assert(binding.session.environment == "WATCH");
			assert(revokeReason == "session_revoked");
			failureReason = "WATCH_RESTART_FENCE_UNAVAILABLE";
			return false;
		});
	TradingToolSessionControlPlane failedControl(failedHost,
		[](const std::string& issuer, const TradingToolHostSessionBinding&, std::string&) {
			return issuer == "hepta.os.uid";
		});
	UnixSessionSupervisorServer failedServer(failedControl);
	failedServer.SetLeaseStore(&store);
	assert(!failedServer.Start(
		socketPath, issuers, resolver, reason, 4096, 1000));
	assert(reason == "WATCH_RESTART_FENCE_UNAVAILABLE");
	assert(failedHost.SessionCount() == 0);
	assert(store.List().size() == 1);
	assert(store.List()[0].fencePending);
	assert(store.List()[0].fenceReason == "session_revoked");

	// A crash after the remote owner fence but before the completed
	// tombstone commit must leave the durable pending record in place. The
	// next restart may repeat the idempotent fence, but it must never restore
	// WATCH authority.
	TradingToolHost injectedHost(registry);
	std::size_t injectedFences = 0;
	injectedHost.SetSessionRevokedObserver(
		[&](const TradingToolHostSessionBinding& binding,
			const std::string& revokeReason, std::string& failureReason) {
			++injectedFences;
			assert(!binding.enabled);
			assert(binding.session.environment == "WATCH");
			assert(revokeReason == "session_revoked");
			failureReason.clear();
			return true;
		});
	TradingToolSessionControlPlane injectedControl(injectedHost,
		[](const std::string& issuer, const TradingToolHostSessionBinding&, std::string&) {
			return issuer == "hepta.os.uid";
		});
	UnixSessionSupervisorServer injectedServer(injectedControl);
	injectedServer.SetLeaseStore(&store);
	injectedServer.SetCrashPointHook([](const std::string& point) {
		return point ==
			"after_watch_restart_fence_before_tombstone_commit";
		});
	assert(!injectedServer.Start(
		socketPath, issuers, resolver, reason, 4096, 1000));
	assert(reason ==
		"SUPERVISOR_FAULT_INJECTED:"
		"after_watch_restart_fence_before_tombstone_commit");
	assert(injectedFences == 1);
	assert(injectedHost.SessionCount() == 0);
	assert(store.List().size() == 1);
	assert(store.List()[0].fencePending);
	assert(!store.List()[0].fenceComplete);

	TradingToolHost restoredHost(registry);
	std::size_t restartFences = 0;
	restoredHost.SetSessionRevokedObserver(
		[&](const TradingToolHostSessionBinding& binding,
			const std::string& revokeReason, std::string& failureReason) {
			++restartFences;
			assert(!binding.enabled);
			assert(binding.session.environment == "WATCH");
			assert(revokeReason == "session_revoked");
			failureReason.clear();
			return true;
		});
	TradingToolSessionControlPlane restoredControl(restoredHost,
		[](const std::string& issuer, const TradingToolHostSessionBinding&, std::string&) {
			return issuer == "hepta.os.uid";
		});
	UnixSessionSupervisorServer restoredServer(restoredControl);
	restoredServer.SetLeaseStore(&store);
	assert(restoredServer.Start(socketPath, issuers, resolver, reason, 4096, 1000));
	TradingToolHostSessionBinding restored;
	assert(!restoredHost.GetSession(provision.token, restored));
	assert(restartFences == 1);
	assert(restoredHost.SessionCount() == 0);
	assert(store.List().size() == 1);
	assert(store.List()[0].fencePending);
	assert(store.List()[0].fenceComplete);
	assert(store.List()[0].fenceReason == "session_revoked");
	restoredServer.Stop();

	// A completed WATCH restart fence is a durable, generation-bound
	// tombstone. Reopening the encrypted store must not replay authority or
	// repeat the remote fence, and exact revoke must consume it idempotently
	// before the original TTL expires.
	SessionSupervisorLeaseStore reopenedStore;
	assert(reopenedStore.Init(storePath, keyPath, reason));
	SessionSupervisorRequest revoke;
	revoke.operation = SessionSupervisorOperation::Revoke;
	revoke.token = provision.token;
	revoke.expectedGeneration = 1;
	{
		TradingToolHost noReplayHost(registry);
		std::size_t noReplayFences = 0;
		noReplayHost.SetSessionRevokedObserver(
			[&](const TradingToolHostSessionBinding&,
				const std::string&, std::string&) {
				++noReplayFences;
				return true;
			});
		TradingToolSessionControlPlane noReplayControl(noReplayHost,
			[](const std::string& issuer,
				const TradingToolHostSessionBinding&, std::string&) {
				return issuer == "hepta.os.uid";
			});
		UnixSessionSupervisorServer noReplayServer(noReplayControl);
		noReplayServer.SetLeaseStore(&reopenedStore);
		assert(noReplayServer.Start(
			socketPath, issuers, resolver, reason, 4096, 1000));
		assert(noReplayFences == 0);
		assert(noReplayHost.SessionCount() == 0);
		assert(reopenedStore.List().size() == 1);
		SessionSupervisorRequest wrongRevoke = revoke;
		wrongRevoke.expectedGeneration = 2;
		const SessionSupervisorResult wrongGeneration =
			Call(socketPath, wrongRevoke);
		assert(!wrongGeneration.accepted);
		assert(wrongGeneration.ReasonCode() ==
			"SESSION_LEASE_GENERATION_MISMATCH");
		assert(reopenedStore.List().size() == 1);
		noReplayServer.Stop();
	}

	// Matching the tombstone generation is insufficient when an ambiguous
	// local bearer survives. Generation and owner drift are quarantined
	// locally, and the durable tombstone must remain.
	{
		TradingToolHost generationHost(registry);
		std::size_t generationFences = 0;
		generationHost.SetSessionRevokedObserver(
			[&](const TradingToolHostSessionBinding&,
				const std::string&, std::string&) {
				++generationFences;
				return true;
			});
		TradingToolSessionControlPlane generationControl(generationHost,
			[](const std::string& issuer,
				const TradingToolHostSessionBinding&, std::string&) {
				return issuer == "hepta.os.uid";
			});
		TradingToolHostSessionBinding generationDrift;
		assert(resolver(provision, generationDrift, reason));
		generationDrift.leaseGeneration = 2;
		assert(generationControl.Provision(
			"hepta.os.uid", generationDrift, reason));
		UnixSessionSupervisorServer generationServer(generationControl);
		generationServer.SetLeaseStore(&reopenedStore);
		assert(!generationServer.Start(
			socketPath, issuers, resolver, reason, 4096, 1000));
		assert(reason == "SESSION_LEASE_GENERATION_MISMATCH");
		std::size_t tombstoneReaped = 0;
		assert(!generationServer.ReapExpired(
			std::numeric_limits<std::uint64_t>::max(),
			tombstoneReaped, reason));
		assert(reason == "SESSION_LEASE_GENERATION_MISMATCH");
		assert(tombstoneReaped == 0);
		TradingToolHostSessionBinding quarantined;
		assert(generationHost.GetSession(provision.token, quarantined));
		assert(!quarantined.enabled);
		assert(reopenedStore.List().size() == 1);
		assert(generationFences == 0);
		assert(!generationHost.RevokeSession(
			provision.token, 2, "session_revoked", reason));
		assert(reason == "SESSION_OWNER_FENCE_PENDING");
		assert(generationFences == 0);
	}
	{
		TradingToolHost ownerHost(registry);
		std::size_t ownerFences = 0;
		ownerHost.SetSessionRevokedObserver(
			[&](const TradingToolHostSessionBinding&,
				const std::string&, std::string&) {
				++ownerFences;
				return true;
			});
		TradingToolSessionControlPlane ownerControl(ownerHost,
			[](const std::string& issuer,
				const TradingToolHostSessionBinding&, std::string&) {
				return issuer == "hepta.os.uid";
			});
		TradingToolHostSessionBinding ownerDrift;
		assert(resolver(provision, ownerDrift, reason));
		ownerDrift.session.executionContext.agentId = "ambiguous-agent";
		ownerDrift.session.executionContext.sessionId = "ambiguous-session";
		assert(ownerControl.Provision(
			"hepta.os.uid", ownerDrift, reason));
		UnixSessionSupervisorServer ownerServer(ownerControl);
		ownerServer.SetLeaseStore(&reopenedStore);
		assert(!ownerServer.Start(
			socketPath, issuers, resolver, reason, 4096, 1000));
		assert(reason == "SESSION_OWNER_IDENTITY_MISMATCH");
		TradingToolHostSessionBinding quarantined;
		assert(ownerHost.GetSession(provision.token, quarantined));
		assert(!quarantined.enabled);
		assert(reopenedStore.List().size() == 1);
		assert(ownerFences == 0);
		assert(!ownerHost.RevokeSession(
			provision.token, 1, "session_revoked", reason));
		assert(reason == "SESSION_OWNER_FENCE_PENDING");
		assert(ownerFences == 0);
	}

	// Exact local binding cleanup must first revoke the local bearer and its
	// remote owner fence. Fence uncertainty keeps both the disabled local
	// bearer and durable tombstone; retry succeeds before client cleanup.
	{
		TradingToolHost exactHost(registry);
		std::size_t exactFences = 0;
		bool allowExactFence = false;
		exactHost.SetSessionRevokedObserver(
			[&](const TradingToolHostSessionBinding&,
				const std::string&, std::string& failureReason) {
				++exactFences;
				if (!allowExactFence)
				{
					failureReason = "TOMBSTONE_REMOTE_FENCE_UNAVAILABLE";
					return false;
				}
				failureReason.clear();
				return true;
			});
		TradingToolSessionControlPlane exactControl(exactHost,
			[](const std::string& issuer,
				const TradingToolHostSessionBinding&, std::string&) {
				return issuer == "hepta.os.uid";
			});
		TradingToolHostSessionBinding exactLocal;
		assert(resolver(provision, exactLocal, reason));
		assert(exactControl.Provision(
			"hepta.os.uid", exactLocal, reason));
		UnixSessionSupervisorServer exactServer(exactControl);
		exactServer.SetLeaseStore(&reopenedStore);
		assert(!exactServer.Start(
			socketPath, issuers, resolver, reason, 4096, 1000));
		assert(reason == "TOMBSTONE_REMOTE_FENCE_UNAVAILABLE");
		TradingToolHostSessionBinding quarantined;
		assert(exactHost.GetSession(provision.token, quarantined));
		assert(!quarantined.enabled);
		assert(reopenedStore.List().size() == 1);
		assert(exactFences == 1);
		allowExactFence = true;
		assert(exactServer.Start(
			socketPath, issuers, resolver, reason, 4096, 1000));
		assert(!exactHost.GetSession(provision.token, quarantined));
		assert(reopenedStore.List().size() == 1);
		assert(exactFences == 2);
		const SessionSupervisorResult revoked = Call(socketPath, revoke);
		assert(revoked.accepted);
		assert(revoked.ReasonCode() == "OK");
		assert(revoked.leaseGeneration == 1);
		assert(reopenedStore.List().empty());
		assert(exactFences == 2);
		const SessionSupervisorResult missing = Call(socketPath, revoke);
		assert(!missing.accepted);
		assert(missing.ReasonCode() == "SESSION_LEASE_NOT_FOUND");
		exactServer.Stop();
	}
	std::remove(storePath.c_str());
	std::remove(keyPath.c_str());
	std::remove(journalPath.c_str());
}

void TestSameHostWatchRestartFencesExactLocalSession()
{
	const std::string journalPath =
		TempPath("/tmp/hepta-supervisor-same-host-journal-XXXXXX");
	const std::string socketPath =
		TempPath("/tmp/hepta-supervisor-same-host-socket-XXXXXX");
	const std::string storePath =
		TempPath("/tmp/hepta-supervisor-same-host-store-XXXXXX");
	const std::string keyPath = TempKeyPath();
	SessionSupervisorLeaseStore store;
	std::string reason;
	assert(store.Init(storePath, keyPath, reason));
	std::map<std::uint32_t, std::string> issuers;
	issuers[static_cast<std::uint32_t>(getuid())] = "hepta.os.uid";
	auto resolver = [](const SessionSupervisorRequest& request,
		TradingToolHostSessionBinding& binding, std::string&) {
		binding.token = request.token;
		binding.peerUid = request.peerUid;
		binding.session.executionContext.agentId = request.agentId;
		binding.session.executionContext.sessionId = request.sessionId;
		binding.session.executionContext.account = "DU123";
		binding.session.environment = "WATCH";
		binding.executionDomain = "IB-PAPER";
		binding.expiresAtMs =
			static_cast<std::uint64_t>(OmsJournal::NowEpochMs()) +
			request.ttlMs;
		return true;
	};
	OmsJournal journal;
	assert(journal.Init(journalPath));
	ExecutionCoordinatorCallbacks callbacks;
	ExecutionCoordinator execution(journal, callbacks);
	TradingToolRegistry registry(execution);
	TradingToolHost host(registry);
	std::size_t exactFences = 0;
	host.SetSessionRevokedObserver(
		[&](const TradingToolHostSessionBinding& binding,
			const std::string& revokeReason, std::string& failureReason) {
			++exactFences;
			assert(!binding.enabled);
			assert(binding.session.environment == "WATCH");
			assert(revokeReason == "session_revoked");
			failureReason.clear();
			return true;
		});
	TradingToolSessionControlPlane control(host,
		[](const std::string& issuer,
			const TradingToolHostSessionBinding&, std::string&) {
			return issuer == "hepta.os.uid";
		});
	UnixSessionSupervisorServer server(control);
	server.SetLeaseStore(&store);
	assert(server.Start(
		socketPath, issuers, resolver, reason, 4096, 1000));
	SessionSupervisorRequest provision;
	provision.operation = SessionSupervisorOperation::Provision;
	provision.templateId = "watch";
	provision.token = "same-host-watch-session-token-0001";
	provision.agentId = "same-host-agent";
	provision.sessionId = "same-host-session";
	provision.peerUid = static_cast<std::uint32_t>(getuid());
	provision.ttlMs = 60000;
	assert(Call(socketPath, provision).accepted);
	TradingToolHostSessionBinding local;
	assert(host.GetSession(provision.token, local));
	assert(local.enabled && local.leaseGeneration == 1);
	server.Stop();

	// Restarting only the supervisor object must not rely on process death to
	// clear the local bearer. The exact local binding is revoked before the
	// completed restart-fence tombstone is committed. The token reservation
	// also closes the proof-to-commit race against direct control-plane use.
	TradingToolHostSessionBinding ambiguous;
	assert(resolver(provision, ambiguous, reason));
	ambiguous.leaseGeneration = 2;
	std::atomic<bool> restartFenceReached(false);
	std::atomic<bool> releaseRestartFence(false);
	server.SetCrashPointHook([&](const std::string& point) {
		if (point !=
			"after_watch_restart_fence_before_tombstone_commit")
			return false;
		restartFenceReached.store(true);
		while (!releaseRestartFence.load()) usleep(1000);
		return false;
		});
	bool restartAccepted = false;
	std::string restartReason;
	std::thread restartThread([&]() {
		restartAccepted = server.Start(
			socketPath, issuers, resolver, restartReason, 4096, 1000);
		});
	for (int i = 0; i < 2000 && !restartFenceReached.load(); ++i)
		usleep(1000);
	assert(restartFenceReached.load());
	assert(!control.Provision("hepta.os.uid", ambiguous, reason));
	assert(reason == "SESSION_TOKEN_FENCE_PENDING");
	SessionSupervisorRequest ownerBypassRequest = provision;
	ownerBypassRequest.token =
		"same-host-owner-bypass-session-token-0002";
	TradingToolHostSessionBinding ownerBypass;
	assert(resolver(ownerBypassRequest, ownerBypass, reason));
	assert(!control.Provision("hepta.os.uid", ownerBypass, reason));
	assert(reason == "SESSION_OWNER_FENCE_PENDING");
	releaseRestartFence.store(true);
	restartThread.join();
	assert(restartAccepted);
	assert(restartReason.empty());
	server.SetCrashPointHook([](const std::string&) { return false; });
	assert(exactFences == 1);
	assert(!host.GetSession(provision.token, local));
	assert(store.List().size() == 1);
	assert(store.List()[0].fencePending);
	assert(store.List()[0].fenceComplete);
	server.Stop();

	// The completed tombstone keeps a host-side token fence, so direct
	// control-plane provisioning cannot race the durable commit or resurrect
	// a same-host bearer while the tombstone remains.
	assert(!control.Provision("hepta.os.uid", ambiguous, reason));
	assert(reason == "SESSION_TOKEN_FENCE_PENDING");
	assert(!host.GetSession(provision.token, local));
	TradingToolHostSessionBinding rotating;
	SessionSupervisorRequest rotatingRequest = provision;
	rotatingRequest.token = "same-host-rotating-session-token-0002";
	rotatingRequest.agentId = "same-host-rotating-agent";
	rotatingRequest.sessionId = "same-host-rotating-session";
	assert(resolver(rotatingRequest, rotating, reason));
	assert(control.Provision("hepta.os.uid", rotating, reason));
	std::uint64_t rotatedGeneration = 0;
	assert(!control.Rotate(
		"hepta.os.uid", rotating.token, provision.token, 1,
		static_cast<std::uint64_t>(OmsJournal::NowEpochMs()) + 60000,
		rotatedGeneration, reason));
	assert(reason == "SESSION_TOKEN_FENCE_PENDING");
	host.SetSessionRevokedObserver(
		[](const TradingToolHostSessionBinding&,
			const std::string&, std::string& failureReason) {
			failureReason.clear();
			return true;
		});
	assert(host.RevokeSession(
		rotating.token, 1, "session_revoked", reason));
	assert(store.List().size() == 1);
	assert(store.List()[0].fenceComplete);
	assert(exactFences == 1);
	assert(server.Start(
		socketPath, issuers, resolver, reason, 4096, 1000));
	assert(exactFences == 1);
	assert(host.SessionCount() == 0);
	assert(store.List().size() == 1);
	server.Stop();

	// Reaping an expired completed tombstone must apply the same exact proof:
	// a matching local bearer is revoked and remotely fenced before removal.
	TradingToolHost reaperHost(registry);
	std::size_t reaperFences = 0;
	reaperHost.SetSessionRevokedObserver(
		[&](const TradingToolHostSessionBinding& binding,
			const std::string& revokeReason, std::string& failureReason) {
			++reaperFences;
			assert(!binding.enabled);
			assert(binding.session.environment == "WATCH");
			assert(revokeReason == "session_revoked");
			failureReason.clear();
			return true;
		});
	TradingToolSessionControlPlane reaperControl(reaperHost,
		[](const std::string& issuer,
			const TradingToolHostSessionBinding&, std::string&) {
			return issuer == "hepta.os.uid";
		});
	TradingToolHostSessionBinding exactLocal;
	assert(resolver(provision, exactLocal, reason));
	assert(reaperControl.Provision("hepta.os.uid", exactLocal, reason));
	UnixSessionSupervisorServer reaperServer(reaperControl);
	reaperServer.SetLeaseStore(&store);
	std::size_t reaped = 0;
	assert(reaperServer.ReapExpired(
		std::numeric_limits<std::uint64_t>::max(), reaped, reason));
	assert(reaped == 1);
	assert(reaperFences == 1);
	assert(reaperHost.SessionCount() == 0);
	assert(store.List().empty());
	std::remove(storePath.c_str());
	std::remove(keyPath.c_str());
	std::remove(journalPath.c_str());
}

void TestWatchTransactionReservationRaces()
{
	const std::string journalPath =
		TempPath("/tmp/hepta-supervisor-watch-transaction-journal-XXXXXX");
	const std::string socketPath =
		TempPath("/tmp/hepta-supervisor-watch-transaction-socket-XXXXXX");
	const std::string storePath =
		TempPath("/tmp/hepta-supervisor-watch-transaction-store-XXXXXX");
	const std::string keyPath = TempKeyPath();
	SessionSupervisorLeaseStore store;
	std::string reason;
	assert(store.Init(storePath, keyPath, reason));
	std::map<std::uint32_t, std::string> issuers;
	issuers[static_cast<std::uint32_t>(getuid())] = "hepta.os.uid";
	auto resolver = [](const SessionSupervisorRequest& request,
		TradingToolHostSessionBinding& binding, std::string&) {
		binding.token = request.token;
		binding.peerUid = request.peerUid;
		binding.session.executionContext.agentId = request.agentId;
		binding.session.executionContext.sessionId = request.sessionId;
		binding.session.executionContext.account = "DU123";
		binding.session.environment = "WATCH";
		binding.executionDomain = "IB-PAPER";
		binding.expiresAtMs =
			static_cast<std::uint64_t>(OmsJournal::NowEpochMs()) +
			request.ttlMs;
		return true;
	};
	OmsJournal journal;
	assert(journal.Init(journalPath));
	ExecutionCoordinatorCallbacks callbacks;
	ExecutionCoordinator execution(journal, callbacks);
	TradingToolRegistry registry(execution);
	TradingToolHost host(registry);
	host.SetSessionRevokedObserver(
		[](const TradingToolHostSessionBinding& binding,
			const std::string& revokeReason, std::string& failureReason) {
			assert(!binding.enabled);
			assert(binding.session.environment == "WATCH");
			assert(revokeReason == "session_revoked");
			failureReason.clear();
			return true;
		});
	TradingToolSessionControlPlane control(host,
		[](const std::string& issuer,
			const TradingToolHostSessionBinding&, std::string&) {
			return issuer == "hepta.os.uid";
		});
	UnixSessionSupervisorServer server(control);
	server.SetLeaseStore(&store);
	assert(server.Start(
		socketPath, issuers, resolver, reason, 4096, 1000));

	SessionSupervisorRequest provision;
	provision.operation = SessionSupervisorOperation::Provision;
	provision.templateId = "watch";
	provision.token = "watch-transaction-session-token-0001";
	provision.agentId = "watch-transaction-agent";
	provision.sessionId = "watch-transaction-session";
	provision.peerUid = static_cast<std::uint32_t>(getuid());
	provision.ttlMs = 60000;

	// The WATCH reservation spans local Provision through the durable active
	// commit. A direct Rotate cannot move the pending local bearer to a token
	// outside that exact transaction.
	std::atomic<bool> localProvisionReached(false);
	std::atomic<bool> releaseLocalProvision(false);
	server.SetCrashPointHook([&](const std::string& point) {
		if (point != "before_lease_activation_commit") return false;
		localProvisionReached.store(true);
		while (!releaseLocalProvision.load()) usleep(1000);
		return false;
		});
	SessionSupervisorResult provisioned;
	std::thread provisionThread([&]() {
		provisioned = Call(socketPath, provision);
		});
	for (int i = 0; i < 2000 && !localProvisionReached.load(); ++i)
		usleep(1000);
	assert(localProvisionReached.load());
	TradingToolHostSessionBinding pendingLocal;
	assert(host.GetSession(provision.token, pendingLocal));
	assert(pendingLocal.enabled && pendingLocal.leaseGeneration == 1);
	assert(!control.Revoke(
		"hepta.os.uid", provision.token, 1, reason));
	assert(reason == "SESSION_OWNER_FENCE_PENDING");
	assert(!control.RevokeExpired(
		"hepta.os.uid", provision.token, 1, reason));
	assert(reason == "SESSION_OWNER_FENCE_PENDING");
	assert(control.ReapExpired(UINT64_MAX) == 0);
	assert(host.GetSession(provision.token, pendingLocal));
	assert(pendingLocal.enabled && pendingLocal.leaseGeneration == 1);
	std::uint64_t directGeneration = 0;
	assert(!control.Rotate(
		"hepta.os.uid", provision.token,
		"watch-transaction-direct-rotate-token-0002", 1,
		static_cast<std::uint64_t>(OmsJournal::NowEpochMs()) + 60000,
		directGeneration, reason));
	assert(reason == "SESSION_OWNER_FENCE_PENDING");
	releaseLocalProvision.store(true);
	provisionThread.join();
	assert(provisioned.accepted && provisioned.leaseGeneration == 1);
	assert(store.List().size() == 1);
	assert(!store.List()[0].fencePending);

	// Reserve old owner/token and replacement token before write-ahead Replace.
	// A direct Provision at the replacement token is rejected while the
	// durable pending mutation is paused at after_lease_commit.
	std::atomic<bool> writeAheadReached(false);
	std::atomic<bool> releaseWriteAhead(false);
	server.SetCrashPointHook([&](const std::string& point) {
		if (point != "after_lease_commit") return false;
		writeAheadReached.store(true);
		while (!releaseWriteAhead.load()) usleep(1000);
		return false;
		});
	SessionSupervisorRequest rotate;
	rotate.operation = SessionSupervisorOperation::Rotate;
	rotate.token = provision.token;
	rotate.replacementToken =
		"watch-transaction-replacement-token-0003";
	rotate.expectedGeneration = 1;
	rotate.ttlMs = 120000;
	SessionSupervisorResult rotated;
	std::thread rotateThread([&]() {
		rotated = Call(socketPath, rotate);
		});
	for (int i = 0; i < 2000 && !writeAheadReached.load(); ++i)
		usleep(1000);
	assert(writeAheadReached.load());
	assert(store.List().size() == 1);
	assert(store.List()[0].fencePending);
	assert(store.List()[0].token == rotate.replacementToken);
	assert(store.List()[0].predecessorToken == provision.token);
	assert(store.List()[0].predecessorGeneration == 1);
	SessionSupervisorRequest collisionRequest = provision;
	collisionRequest.token = rotate.replacementToken;
	collisionRequest.agentId = "replacement-collision-agent";
	collisionRequest.sessionId = "replacement-collision-session";
	TradingToolHostSessionBinding collision;
	assert(resolver(collisionRequest, collision, reason));
	assert(!control.Provision("hepta.os.uid", collision, reason));
	assert(reason == "SESSION_TOKEN_FENCE_PENDING");
	releaseWriteAhead.store(true);
	rotateThread.join();
	assert(rotated.accepted && rotated.leaseGeneration == 2);
	assert(store.List().size() == 1);
	assert(store.List()[0].token == rotate.replacementToken);
	assert(store.List()[0].predecessorToken.empty());
	assert(store.List()[0].predecessorGeneration == 0);
	assert(!store.List()[0].fencePending);
	TradingToolHostSessionBinding active;
	assert(!host.GetSession(provision.token, active));
	assert(host.GetSession(rotate.replacementToken, active));
	assert(active.enabled && active.leaseGeneration == 2);

	server.SetCrashPointHook([](const std::string&) { return false; });
	SessionSupervisorRequest revoke;
	revoke.operation = SessionSupervisorOperation::Revoke;
	revoke.token = rotate.replacementToken;
	revoke.expectedGeneration = 2;
	assert(Call(socketPath, revoke).accepted);
	assert(store.List().empty() && host.SessionCount() == 0);

	server.Stop();
	std::remove(storePath.c_str());
	std::remove(keyPath.c_str());
	std::remove(journalPath.c_str());
}

void TestLeaseCommitCrashConvergenceMatrix()
{
	const std::string journalPath = TempPath("/tmp/hepta-supervisor-crash-journal-XXXXXX");
	const std::string socketPath = TempPath("/tmp/hepta-supervisor-crash-socket-XXXXXX");
	const std::string storePath = TempPath("/tmp/hepta-supervisor-crash-store-XXXXXX");
	const std::string keyPath = TempKeyPath();
	SessionSupervisorLeaseStore store;
	std::string reason;
	assert(store.Init(storePath, keyPath, reason));
	std::map<std::uint32_t, std::string> issuers;
	issuers[static_cast<std::uint32_t>(getuid())] = "hepta.os.uid";
	auto resolver = [](const SessionSupervisorRequest& request,
		TradingToolHostSessionBinding& binding, std::string&) {
		binding.token = request.token;
		binding.peerUid = request.peerUid;
		binding.session.executionContext.agentId = request.agentId;
		binding.session.executionContext.sessionId = request.sessionId;
		binding.session.executionContext.account = "DU123";
		binding.session.environment = "PAPER";
		binding.executionDomain = "IB-PAPER";
		binding.expiresAtMs = static_cast<std::uint64_t>(OmsJournal::NowEpochMs()) + request.ttlMs;
		return true;
	};
	auto authorizer = [](const std::string& issuer,
		const TradingToolHostSessionBinding&, std::string&) {
		return issuer == "hepta.os.uid";
	};
	auto exactZeroAudit = [](const ExecutionControlCommand& command) {
		ExecutionControlResult result;
		result.commandId = command.context.toolCallId;
		result.status = ExecutionCommandStatus::Accepted;
		result.ownerAuditAuthoritative = true;
		result.ownerAuditComplete = true;
		result.ownerAccount = command.context.account;
		result.ownerExecutionDomain = command.context.executionDomain;
		result.brokerConnectionEpoch = 41;
		result.brokerActiveGeneration = 7;
		result.brokerTerminalGeneration = 11;
		result.reasonCode = "RECOVERY_OWNER_ZERO_CONFIRMED";
		return result;
	};
	auto zeroFence = [](const ExecutionControlCommand& command) {
		ExecutionControlResult result;
		result.commandId = command.context.toolCallId;
		result.status = ExecutionCommandStatus::Accepted;
		result.affectedCount = 0;
		return result;
	};
	OmsJournal journal;
	assert(journal.Init(journalPath));
	ExecutionCoordinatorCallbacks callbacks;
	ExecutionCoordinator execution(journal, callbacks);
	TradingToolRegistry registry(execution);
	SessionSupervisorRequest request;
	request.operation = SessionSupervisorOperation::Provision;
	request.templateId = "paper";
	request.token = "crash-matrix-session-token-0001";
	request.agentId = "crash-agent";
	request.sessionId = "crash-session";
	request.peerUid = static_cast<std::uint32_t>(getuid());
	request.ttlMs = 60000;

	{
		TradingToolHost host(registry);
		RecoveryControlAuthority recoveryAuthority;
		recoveryAuthority.ownerAudit = exactZeroAudit;
		recoveryAuthority.fence = zeroFence;
		host.SetRecoveryControlAuthority(&recoveryAuthority);
		TradingToolSessionControlPlane control(host, authorizer);
		UnixSessionSupervisorServer server(control);
		server.SetLeaseStore(&store);
		assert(server.Start(socketPath, issuers, resolver, reason, 4096, 1000));
		assert(Call(socketPath, request).accepted);
		server.Stop();
	}

	request.operation = SessionSupervisorOperation::Revoke;
	request.expectedGeneration = 1;
	{
		TradingToolHost host(registry);
		RecoveryControlAuthority recoveryAuthority;
		recoveryAuthority.ownerAudit = exactZeroAudit;
		recoveryAuthority.fence = zeroFence;
		host.SetRecoveryControlAuthority(&recoveryAuthority);
		TradingToolSessionControlPlane control(host, authorizer);
		UnixSessionSupervisorServer server(control);
		server.SetLeaseStore(&store);
		assert(server.Start(socketPath, issuers, resolver, reason, 4096, 1000));
		assert(host.SessionCount() == 1);
		server.SetCrashPointHook([](const std::string& point) {
			return point == "after_lease_commit";
		});
		const SessionSupervisorResult crashed = Call(socketPath, request);
		assert(!crashed.accepted);
		assert(crashed.ReasonCode() ==
			"SUPERVISOR_FAULT_INJECTED:after_lease_commit");
		assert(store.List().size() == 1);
		assert(!store.List()[0].fencePending);
		assert(store.List()[0].fenceReason.empty());
		assert(store.List()[0].recoveryOnly);
		assert(host.SessionCount() == 1);
		TradingToolHostSessionBinding recovery;
		assert(host.GetSession(request.token, recovery));
		assert(recovery.enabled);
		assert(recovery.recoveryOnly);
		server.Stop();
	}
	{
		// If the remote recovery authority is unavailable after restart, the
		// durable and local leases must remain recovery-only and startup must
		// fail closed without deleting the checkpoint.
		TradingToolHost host(registry);
		TradingToolSessionControlPlane control(host, authorizer);
		UnixSessionSupervisorServer server(control);
		server.SetLeaseStore(&store);
		assert(!server.Start(
			socketPath, issuers, resolver, reason, 4096, 1000));
		assert(reason == "SESSION_RECOVERY_QUERY_UNAVAILABLE");
		assert(store.List().size() == 1);
		assert(store.List()[0].recoveryOnly);
		TradingToolHostSessionBinding recovery;
		assert(host.GetSession(request.token, recovery));
		assert(recovery.enabled && recovery.recoveryOnly);
	}
	{
		TradingToolHost host(registry);
		RecoveryControlAuthority recoveryAuthority;
		recoveryAuthority.ownerAudit = exactZeroAudit;
		recoveryAuthority.fence = zeroFence;
		host.SetRecoveryControlAuthority(&recoveryAuthority);
		TradingToolSessionControlPlane control(host, authorizer);
		UnixSessionSupervisorServer server(control);
		server.SetLeaseStore(&store);
		assert(server.Start(socketPath, issuers, resolver, reason, 4096, 1000));
		// The default/local discriminator remains false, so restart may
		// complete its established exact-zero finalize-and-delete path.
		assert(host.SessionCount() == 0);
		assert(store.List().empty());
		server.Stop();
	}
	std::remove(storePath.c_str());
	std::remove(keyPath.c_str());
	std::remove(journalPath.c_str());
}

void TestPaperRecoveryRetriesEventServiceNotReady()
{
	const std::string journalPath = TempPath(
		"/tmp/hepta-supervisor-event-not-ready-journal-XXXXXX");
	const std::string socketPath = TempPath(
		"/tmp/hepta-supervisor-event-not-ready-socket-XXXXXX");
	const std::string storePath = TempPath(
		"/tmp/hepta-supervisor-event-not-ready-store-XXXXXX");
	const std::string keyPath = TempKeyPath();
	SessionSupervisorLeaseStore store;
	std::string reason;
	assert(store.Init(storePath, keyPath, reason));

	SessionSupervisorLeaseRecord durable;
	durable.templateId = "paper";
	durable.issuer = "hepta.os.uid";
	durable.token = std::string(64, 'e');
	durable.agentId = "event-not-ready-agent";
	durable.sessionId = "event-not-ready-session";
	durable.ownerAccount = "DU123";
	durable.ownerExecutionDomain = "PAPER:alpha";
	durable.peerUid = static_cast<std::uint32_t>(getuid());
	durable.expiresAtMs = 1;
	durable.leaseGeneration = 1;
	durable.recoveryOnly = true;
	assert(store.Put(durable, reason));

	OmsJournal journal;
	assert(journal.Init(journalPath));
	ExecutionCoordinatorCallbacks callbacks;
	ExecutionCoordinator execution(journal, callbacks);
	TradingToolRegistry registry(execution);
	TradingToolHost host(registry);
	std::atomic<int> ownerAuditCalls(0);
	RecoveryControlAuthority recoveryAuthority;
	recoveryAuthority.ownerAudit =
		[&](const ExecutionControlCommand& command) {
			ExecutionControlResult result;
			result.commandId = command.context.toolCallId;
			result.ownerAccount = command.context.account;
			result.ownerExecutionDomain = command.context.executionDomain;
			if (++ownerAuditCalls == 1)
			{
				result.status = ExecutionCommandStatus::Rejected;
				result.reasonCode = "EXECUTION_EVENT_SERVICE_NOT_READY";
				return result;
			}
			result.status = ExecutionCommandStatus::Accepted;
			result.ownerAuditAuthoritative = true;
			result.ownerAuditComplete = true;
			result.brokerConnectionEpoch = 41;
			result.brokerActiveGeneration = 7;
			result.brokerTerminalGeneration = 11;
			return result;
		};
	recoveryAuthority.fence =
		[](const ExecutionControlCommand& command) {
			ExecutionControlResult result;
			result.commandId = command.context.toolCallId;
			result.status = ExecutionCommandStatus::Accepted;
			result.affectedCount = 0;
			return result;
		};
	host.SetRecoveryControlAuthority(&recoveryAuthority);
	auto authorizer = [](const std::string& issuer,
		const TradingToolHostSessionBinding&, std::string&) {
		return issuer == "hepta.os.uid";
	};
	TradingToolSessionControlPlane control(host, authorizer);
	UnixSessionSupervisorServer server(control);
	server.SetLeaseStore(&store);
	server.SetRootCustodianUidForTests(
		static_cast<std::uint32_t>(getuid()));
	std::map<std::uint32_t, std::string> issuers;
	issuers[static_cast<std::uint32_t>(getuid())] = "hepta.os.uid";
	auto resolver = [](const SessionSupervisorRequest& request,
		TradingToolHostSessionBinding& binding, std::string&) {
		binding.token = request.token;
		binding.peerUid = request.peerUid;
		binding.session.executionContext.agentId = request.agentId;
		binding.session.executionContext.sessionId = request.sessionId;
		binding.session.executionContext.account = "DU123";
		binding.session.executionContext.venue = "IB";
		binding.session.environment = "PAPER";
		binding.executionDomain = "PAPER:alpha";
		binding.expiresAtMs =
			static_cast<std::uint64_t>(OmsJournal::NowEpochMs()) + request.ttlMs;
		binding.leaseGeneration = 1;
		return true;
	};
	assert(server.Start(socketPath, issuers, resolver, reason, 4096, 1000));
	assert(ownerAuditCalls.load() >= 3);
	assert(store.List().empty());
	assert(host.SessionCount() == 0);
	server.Stop();

	std::remove(storePath.c_str());
	std::remove(keyPath.c_str());
	std::remove(journalPath.c_str());
}

void TestWatchProvisionRejectsResolverScopeDrift()
{
	const std::string journalPath =
		TempPath("/tmp/hepta-supervisor-watch-drift-journal-XXXXXX");
	const std::string socketPath =
		TempPath("/tmp/hepta-supervisor-watch-drift-socket-XXXXXX");
	const std::string storePath =
		TempPath("/tmp/hepta-supervisor-watch-drift-store-XXXXXX");
	const std::string keyPath = TempKeyPath();
	SessionSupervisorLeaseStore store;
	std::string reason;
	assert(store.Init(storePath, keyPath, reason));
	OmsJournal journal;
	assert(journal.Init(journalPath));
	ExecutionCoordinatorCallbacks callbacks;
	ExecutionCoordinator execution(journal, callbacks);
	TradingToolRegistry registry(execution);
	TradingToolHost host(registry);
	TradingToolSessionControlPlane control(host,
		[](const std::string& issuer,
		   const TradingToolHostSessionBinding&, std::string&) {
			return issuer == "hepta.os.watch";
		});
	UnixSessionSupervisorServer server(control);
	server.SetLeaseStore(&store);
	std::map<std::uint32_t, std::string> issuers;
	issuers[static_cast<std::uint32_t>(getuid())] = "hepta.os.watch";
	assert(server.Start(socketPath, issuers,
		[](const SessionSupervisorRequest& request,
		   TradingToolHostSessionBinding& binding, std::string&) {
			binding.token = request.token;
			binding.peerUid = request.peerUid;
			binding.session.executionContext.agentId = request.agentId;
			binding.session.executionContext.sessionId = request.sessionId;
			binding.session.executionContext.account = "DU123";
			binding.session.environment = "PAPER";
			binding.executionDomain = "IB-PAPER";
			binding.expiresAtMs =
				static_cast<std::uint64_t>(OmsJournal::NowEpochMs()) +
				request.ttlMs;
			return true;
		}, reason, 4096, 1000));
	SessionSupervisorRequest provision;
	provision.operation = SessionSupervisorOperation::Provision;
	provision.templateId = "watch";
	provision.token = "watch-resolver-drift-session-token-0001";
	provision.agentId = "watch-resolver-drift-agent";
	provision.sessionId = "watch-resolver-drift-session";
	provision.peerUid = static_cast<std::uint32_t>(getuid());
	provision.ttlMs = 60000;
	const SessionSupervisorResult result = Call(socketPath, provision);
	assert(!result.accepted);
	assert(result.ReasonCode() == "WATCH_TRANSACTION_RESERVATION_MISMATCH");
	assert(store.List().empty());
	assert(host.SessionCount() == 0);
	server.Stop();
	std::remove(storePath.c_str());
	std::remove(keyPath.c_str());
	std::remove(journalPath.c_str());
}

void TestRootRecoveryQueryIsDurableAndRestartClosed()
{
	const std::string journalPath =
		TempPath("/tmp/hepta-supervisor-recovery-query-journal-XXXXXX");
	const std::string socketPath =
		TempPath("/tmp/hepta-supervisor-recovery-query-socket-XXXXXX");
	const std::string storePath =
		TempPath("/tmp/hepta-supervisor-recovery-query-store-XXXXXX");
	const std::string keyPath = TempKeyPath();
	const std::string auditPath =
		TempPath("/tmp/hepta-supervisor-recovery-query-audit-XXXXXX");
	SessionSupervisorLeaseStore store;
	std::string reason;
	assert(store.Init(storePath, keyPath, reason));
	std::map<std::uint32_t, std::string> issuers;
	issuers[static_cast<std::uint32_t>(getuid())] = "hepta.os.uid";
	auto resolver = [](const SessionSupervisorRequest& request,
		TradingToolHostSessionBinding& binding, std::string&) {
		binding.token = request.token;
		binding.peerUid = request.peerUid;
		binding.session.executionContext.agentId = request.agentId;
		binding.session.executionContext.sessionId = request.sessionId;
		binding.session.executionContext.account = "DU123";
		binding.session.executionContext.venue = "IB";
		binding.session.executionContext.strategy = "agent-native";
		binding.session.environment = "PAPER";
		binding.executionDomain = "PAPER:recovery-agent";
		binding.expiresAtMs =
			static_cast<std::uint64_t>(OmsJournal::NowEpochMs()) +
			request.ttlMs;
		binding.leaseGeneration = 1;
		return true;
	};
	auto authorizer = [](const std::string& issuer,
		const TradingToolHostSessionBinding&, std::string&) {
		return issuer == "hepta.os.uid";
	};
	SessionSupervisorRequest provision;
	provision.operation = SessionSupervisorOperation::Provision;
	provision.templateId = "paper";
	provision.token = "recovery-query-session-token-0001";
	provision.agentId = "recovery-agent";
	provision.sessionId = "recovery-session";
	provision.peerUid = static_cast<std::uint32_t>(getuid());
	provision.ttlMs = 120000;
	SessionSupervisorRequest query;
	query.operation = SessionSupervisorOperation::RecoveryQuery;
	query.token = provision.token;
	query.expectedGeneration = 1;
	query.targetCommandId = "hexec-command-recovery-0001";
	const std::string tokenFile =
		WriteSessionCtlTokenFile(provision.token);

	{
		OmsJournal journal;
		assert(journal.Init(journalPath));
		ExecutionCoordinatorCallbacks callbacks;
		ExecutionCoordinator execution(journal, callbacks);
		TradingToolRegistry registry(execution);
		TradingToolHost host(registry);
		RecoveryControlAuthority recoveryAuthority;
		recoveryAuthority.ownerAudit =
			[](const ExecutionControlCommand& command) {
				ExecutionControlResult result;
				result.commandId = command.context.toolCallId;
				result.status = ExecutionCommandStatus::Accepted;
				result.ownerAuditAuthoritative = true;
				result.ownerAuditComplete = true;
				result.ownerAccount = command.context.account;
				result.ownerExecutionDomain = command.context.executionDomain;
				result.brokerConnectionEpoch = 41;
				result.brokerActiveGeneration = 7;
				result.brokerTerminalGeneration = 11;
				return result;
			};
		recoveryAuthority.fence =
			[](const ExecutionControlCommand& command) {
				ExecutionControlResult result;
				result.commandId = command.context.toolCallId;
				result.status = ExecutionCommandStatus::Accepted;
				return result;
			};
		host.SetRecoveryControlAuthority(&recoveryAuthority);
		TradingToolSessionControlPlane control(host, authorizer);
		UnixSessionSupervisorServer server(control);
		server.SetLeaseStore(&store);
		server.SetRootCustodianUidForTests(
			static_cast<std::uint32_t>(getuid()) + 1);
		assert(server.Start(socketPath, issuers, resolver, reason, 4096, 1000));
		assert(Call(socketPath, provision).accepted);
		const SessionSupervisorResult denied = Call(socketPath, query);
		assert(!denied.accepted);
		assert(denied.ReasonCode() == "SUPERVISOR_ROOT_CUSTODIAN_REQUIRED");
		SessionSupervisorRequest deniedPrepare;
		deniedPrepare.operation =
			SessionSupervisorOperation::PaperTerminalWitnessPrepare;
		deniedPrepare.token = provision.token;
		deniedPrepare.expectedGeneration = 1;
		deniedPrepare.recoveryId = "denied-recovery";
		deniedPrepare.finalizationId = "denied-finalization";
		deniedPrepare.expectedOwnerSetSha256 =
			"sha256:" + std::string(64, 'a');
		deniedPrepare.expectedOwnerCount = 1;
		deniedPrepare.receiptSha256 =
			"sha256:" + std::string(64, 'b');
		const SessionSupervisorResult prepareDenied =
			Call(socketPath, deniedPrepare);
		assert(!prepareDenied.accepted);
		assert(prepareDenied.ReasonCode() ==
			"SUPERVISOR_ROOT_CUSTODIAN_REQUIRED");
		assert(store.List().size() == 1 && !store.List()[0].recoveryOnly);
		SessionSupervisorRequest revoke;
		revoke.operation = SessionSupervisorOperation::Revoke;
		revoke.token = provision.token;
		revoke.expectedGeneration = 1;
		const SessionSupervisorResult legacyRevoke =
			Call(socketPath, revoke);
		assert(legacyRevoke.accepted);
		assert(legacyRevoke.ReasonCode() == "OK");
		assert(store.List().empty());
		server.Stop();
	}

	{
		OmsJournal journal;
		assert(journal.Init(journalPath));
		ExecutionCoordinatorCallbacks callbacks;
		ExecutionCoordinator execution(journal, callbacks);
		TradingToolRegistry registry(execution);
		TradingToolHost host(registry);
		RecoveryControlAuthority recoveryAuthority;
		recoveryAuthority.ownerAudit =
			[](const ExecutionControlCommand& command) {
				ExecutionControlResult result;
				result.commandId = command.context.toolCallId;
				result.status = ExecutionCommandStatus::Accepted;
				result.ownerAuditAuthoritative = true;
				result.ownerAuditComplete = true;
				result.ownerAccount = command.context.account;
				result.ownerExecutionDomain = command.context.executionDomain;
				result.brokerConnectionEpoch = 41;
				result.brokerActiveGeneration = 7;
				result.brokerTerminalGeneration = 11;
				return result;
			};
		recoveryAuthority.query =
			[&](const ExecutionControlCommand& command) {
				assert(command.context.agentId ==
					"recovery-agent");
				assert(command.context.sessionId ==
					"recovery-session");
				assert(command.recoveryIngressFence == 1);
				ExecutionControlResult result;
				result.status = ExecutionCommandStatus::Accepted;
				result.targetCommandId = command.targetCommandId;
				result.targetStatus = ExecutionCommandStatus::Accepted;
				result.orderId = 9123;
				result.serviceEpoch = "hexec-v6-test";
				result.serviceFencingGeneration = 7;
				return result;
			};
		host.SetRecoveryControlAuthority(&recoveryAuthority);
		TradingToolSessionControlPlane control(host, authorizer);
		UnixSessionSupervisorServer server(control);
		server.SetLeaseStore(&store);
		server.SetRootCustodianUidForTests(
			static_cast<std::uint32_t>(getuid()));
		assert(server.Start(socketPath, issuers, resolver, reason, 4096, 1000));
		assert(Call(socketPath, provision).accepted);
		const SessionCtlProcessResult localQuery = RunSessionCtl({
			"--socket", socketPath, "recovery-query", "--token-file",
			tokenFile, "--generation", "1", "--command-id",
			query.targetCommandId});
		assert(localQuery.exitCode == 0);
		assert(localQuery.standardOutput.find(
			"\"paper_finalization_required\":false") !=
			std::string::npos);
		std::cout << "HEPTA_REAL_CLI_LOCAL_QUERY="
			<< localQuery.standardOutput;
		const SessionSupervisorResult recovered = Call(socketPath, query);
		assert(recovered.accepted);
		assert(recovered.authoritativeCommandStatus);
		assert(recovered.CommandStatus() == "accepted");
		assert(recovered.CommandReasonCode() == "NONE");
		assert(recovered.orderId == 9123);
		assert(recovered.recoveryOnly && !recovered.ownerFenced);
		assert(!recovered.paperFinalizationRequired);
		assert(recovered.ReasonCode() == "RECOVERY_QUERY_CANNOT_FULL_FENCE");
		assert(store.List().size() == 1);
		assert(store.List()[0].recoveryOnly);
		assert(!store.List()[0].paperFinalizationRequired);
		assert(store.List()[0].recoveryCommandId == query.targetCommandId);
		TradingToolHostSessionBinding active;
		assert(host.GetSession(provision.token, active));
		assert(active.recoveryOnly && active.enabled);
		SessionSupervisorRequest renew = query;
		renew.operation = SessionSupervisorOperation::Renew;
		renew.ttlMs = 120000;
		const SessionSupervisorResult renewal = Call(socketPath, renew);
		assert(!renewal.accepted);
		assert(renewal.ReasonCode() == "SESSION_OWNER_FENCE_PENDING");
		server.Stop();
	}

	// A Gateway restart must restore the lease only in recovery-only mode.  It
	// may query later durable risk-reduction commands, but can never silently
	// regain entry authority or replace the original fence provenance.
	{
		OmsJournal journal;
		assert(journal.Init(journalPath));
		ExecutionCoordinatorCallbacks callbacks;
		ExecutionCoordinator execution(journal, callbacks);
		TradingToolRegistry registry(execution);
		TradingToolHost host(registry);
		bool ownerRecoveryComplete = false;
		RecoveryControlAuthority recoveryAuthority;
		recoveryAuthority.ownerAudit =
			[&](const ExecutionControlCommand& command) {
				ExecutionControlResult result;
				result.commandId = command.context.toolCallId;
				result.status = ExecutionCommandStatus::Accepted;
				result.ownerAuditAuthoritative = true;
				result.ownerAuditComplete = true;
				result.ownerUncertainCommandCount =
					ownerRecoveryComplete ? 0 : 1;
				result.ownerAccount = command.context.account;
				result.ownerExecutionDomain = command.context.executionDomain;
				result.brokerConnectionEpoch = 42;
				result.brokerActiveGeneration = 8;
				result.brokerTerminalGeneration = 12;
				return result;
			};
		recoveryAuthority.fence =
			[](const ExecutionControlCommand& command) {
				ExecutionControlResult result;
				result.commandId = command.context.toolCallId;
				result.status = ExecutionCommandStatus::Accepted;
				return result;
			};
		recoveryAuthority.query =
			[&](const ExecutionControlCommand& command) {
				ExecutionControlResult result;
				result.targetCommandId = command.targetCommandId;
				result.serviceEpoch = "hexec-v6-restarted";
				result.serviceFencingGeneration = 8;
				if (command.targetCommandId == query.targetCommandId)
				{
					result.status = ExecutionCommandStatus::Rejected;
					result.reasonCode = "EXECUTION_COMMAND_NOT_FOUND";
				}
				else
				{
					result.status = ExecutionCommandStatus::Accepted;
					result.targetStatus = ExecutionCommandStatus::Accepted;
					result.orderId = -1;
					result.reasonCode = "POSITION_ALREADY_FLAT";
				}
				return result;
			};
		host.SetRecoveryControlAuthority(&recoveryAuthority);
		TradingToolSessionControlPlane control(host, authorizer);
		UnixSessionSupervisorServer server(control);
		SessionSupervisorAuditJournal audit;
		assert(audit.Init(auditPath, reason));
		server.SetAuditJournal(&audit);
		server.SetLeaseStore(&store);
		server.SetRootCustodianUidForTests(
			static_cast<std::uint32_t>(getuid()));
		assert(server.Start(socketPath, issuers, resolver, reason, 4096, 1000));
		TradingToolHostSessionBinding restored;
		assert(host.GetSession(provision.token, restored));
		assert(restored.recoveryOnly && restored.enabled);
		const SessionSupervisorResult missing = Call(socketPath, query);
		assert(missing.accepted && missing.authoritativeCommandStatus);
		assert(missing.CommandStatus() == "not_found");
		assert(missing.CommandReasonCode() == "EXECUTION_COMMAND_NOT_FOUND");
		assert(missing.recoveryOnly && !missing.ownerFenced);
		// Recovery may have sent an owned flatten after the first fence and then
		// lost its response.  After a process restart the root custodian must be
		// able to query that separately durable mutation intent without changing
		// the first command ID retained as fence provenance in HSL5.
		SessionSupervisorRequest lostFlatten = query;
		lostFlatten.targetCommandId = "hexec-lost-flatten-0002";
		const SessionSupervisorResult alreadyFlat =
			Call(socketPath, lostFlatten);
		assert(alreadyFlat.accepted &&
			alreadyFlat.authoritativeCommandStatus);
		assert(alreadyFlat.TargetCommandId() ==
			lostFlatten.targetCommandId);
		assert(alreadyFlat.CommandStatus() == "accepted");
		assert(alreadyFlat.orderId == -1);
		assert(alreadyFlat.CommandReasonCode() == "POSITION_ALREADY_FLAT");
		assert(alreadyFlat.ReasonCode() ==
			"RECOVERY_QUERY_PROVEN_RECOVERY_ONLY");
		assert(alreadyFlat.recoveryOnly && !alreadyFlat.ownerFenced);
		assert(store.List().size() == 1);
		assert(store.List()[0].recoveryCommandId ==
			query.targetCommandId);
		const std::vector<std::string> auditPayloads =
			AuditPayloads(auditPath);
		std::size_t originalTargetRecords = 0;
		std::size_t flattenTargetRecords = 0;
		for (std::vector<std::string>::const_iterator payload =
				auditPayloads.begin(); payload != auditPayloads.end(); ++payload)
		{
			assert(payload->find(provision.token) == std::string::npos);
			if (payload->find("target_command_id=" +
					query.targetCommandId) != std::string::npos)
				++originalTargetRecords;
			if (payload->find("target_command_id=" +
					lostFlatten.targetCommandId) != std::string::npos)
				++flattenTargetRecords;
		}
		assert(originalTargetRecords == 2);
		assert(flattenTargetRecords == 2);
		ownerRecoveryComplete = true;
		SessionSupervisorRequest revoke;
		revoke.operation = SessionSupervisorOperation::Revoke;
		revoke.token = provision.token;
		revoke.expectedGeneration = 1;
		const SessionCtlProcessResult localRevoke = RunSessionCtl({
			"--socket", socketPath, "revoke", "--token-file",
			tokenFile, "--generation", "1"});
		assert(localRevoke.exitCode == 0);
		assert(localRevoke.standardOutput.find(
			"\"accepted\":true") != std::string::npos);
		assert(localRevoke.standardOutput.find(
			"\"reason_code\":\"OK\"") != std::string::npos);
		std::cout << "HEPTA_REAL_CLI_LOCAL_REVOKE="
			<< localRevoke.standardOutput;
		assert(store.List().empty());
		server.Stop();
	}

	std::remove(storePath.c_str());
	std::remove(keyPath.c_str());
	std::remove(journalPath.c_str());
	std::remove(auditPath.c_str());
	std::remove(tokenFile.c_str());
}

void TestPaperFinalizationMultiOwnerStateMachineAndCrashReplay()
{
	const std::string journalPath = TempPath(
		"/tmp/hepta-paper-finalization-journal-XXXXXX");
	const std::string socketPath = TempPath(
		"/tmp/hepta-paper-finalization-socket-XXXXXX");
	const std::string storePath = TempPath(
		"/tmp/hepta-paper-finalization-store-XXXXXX");
	const std::string keyPath = TempKeyPath();
	SessionSupervisorLeaseStore store;
	std::string reason;
	assert(store.Init(storePath, keyPath, reason));
	OmsJournal journal;
	assert(journal.Init(journalPath));
	ExecutionCoordinatorCallbacks callbacks;
	ExecutionCoordinator execution(journal, callbacks);
	TradingToolRegistry registry(execution);
	TradingToolHost host(registry);
	std::atomic<int> auditMode(0);
	std::atomic<int> fenceCalls(0);
	std::atomic<int> terminalizeCalls(0);
	std::atomic<int> terminalizeMode(0);
	RecoveryControlAuthority recoveryAuthority;
	recoveryAuthority.ownerAudit =
		[&](const ExecutionControlCommand& command) {
			ExecutionControlResult result;
			result.commandId = command.context.toolCallId;
			result.status = ExecutionCommandStatus::Accepted;
			result.reasonCode = "RECOVERY_OWNER_ZERO_CONFIRMED";
			result.ownerAuditAuthoritative = true;
			result.ownerAuditComplete = true;
			result.ownerAccount = command.context.account;
			result.ownerExecutionDomain = command.context.executionDomain;
			result.serviceEpoch = "execution-epoch-finalization-1";
			result.serviceFencingGeneration = 17;
			result.brokerConnectionEpoch = 23;
			result.brokerActiveGeneration = 29;
			result.brokerTerminalGeneration = 31;
			result.brokerRiskGeneration = 37;
			result.brokerAccountGeneration = 41;
			result.brokerPositionGeneration = 43;
			result.brokerFxCashGeneration = 47;
			// A NO_TRADE/no-fill owner legitimately has a zero exposure
			// watermark. The ordering/equality, not non-zero-ness, is proof.
			result.brokerExposureGeneration = 0;
			result.brokerTerminalExposureGeneration = 0;
			result.brokerRiskAbsorbedExposureGeneration = 0;
			result.brokerRecoveryAuditBarrierComplete = true;
			result.brokerPositionQuantity = "0";
			result.brokerGrossAbsolutePosition = "0";
			if (command.context.agentId == "finalization-agent-b" &&
				auditMode.load() == 1)
			{
				result.ownerUncertainCommandCount = 1;
				result.reasonCode =
					"RECOVERY_OWNER_UNCERTAIN_COMMANDS_REMAIN";
			}
			if (command.context.agentId == "finalization-agent-b" &&
				auditMode.load() == 2)
				++result.brokerRiskGeneration;
			return result;
		};
	recoveryAuthority.query =
		[&](const ExecutionControlCommand& command) {
			bool durableTransition = false;
			const std::vector<SessionSupervisorLeaseRecord> durable =
				store.List();
			for (std::size_t i = 0; i < durable.size(); ++i)
				durableTransition = durableTransition ||
					(durable[i].agentId == command.context.agentId &&
					 durable[i].recoveryOnly &&
					 durable[i].paperFinalizationRequired);
			assert(durableTransition);
			ExecutionControlResult result;
			result.status = ExecutionCommandStatus::Rejected;
			result.reasonCode = "EXECUTION_COMMAND_NOT_FOUND";
			result.targetCommandId = command.targetCommandId;
			result.serviceEpoch = "execution-epoch-finalization-1";
			result.serviceFencingGeneration = 17;
			return result;
		};
	recoveryAuthority.fence =
		[&](const ExecutionControlCommand& command) {
			ExecutionControlResult result;
			result.commandId = command.context.toolCallId;
			result.status = ExecutionCommandStatus::Accepted;
			result.affectedCount = 0;
			++fenceCalls;
			return result;
		};
	recoveryAuthority.terminalize =
		[&](const ExecutionControlCommand& command) {
			assert(command.targetCommandId ==
				"finalization-multiowner-1");
			assert(command.terminalPreliminaryReceiptSha256.compare(
				0, 7, "sha256:") == 0);
			++terminalizeCalls;
			ExecutionControlResult result;
			result.commandId = command.context.toolCallId;
			result.targetCommandId = command.targetCommandId;
			result.status = ExecutionCommandStatus::Rejected;
			result.reasonCode = terminalizeMode.load() == 0 ?
				"POST_CUTOFF_SIGNED_WITNESS_REQUIRED" :
				"IB_PAPER_TERMINALIZATION_INCOMPLETE";
			result.ownerAccount = command.context.account;
			result.ownerExecutionDomain = command.context.executionDomain;
			return result;
		};
	host.SetRecoveryControlAuthority(&recoveryAuthority);
	auto authorizer = [](const std::string& issuer,
		const TradingToolHostSessionBinding&, std::string&) {
		return issuer == "hepta.os.finalization";
	};
	TradingToolSessionControlPlane control(host, authorizer);
	UnixSessionSupervisorServer server(control);
	server.SetLeaseStore(&store);
	server.SetRootCustodianUidForTests(
		static_cast<std::uint32_t>(getuid()));
	std::map<std::uint32_t, std::string> issuers;
	issuers[static_cast<std::uint32_t>(getuid())] =
		"hepta.os.finalization";
	auto resolver = [](const SessionSupervisorRequest& request,
		TradingToolHostSessionBinding& binding, std::string&) {
		binding.token = request.token;
		binding.peerUid = request.peerUid;
		binding.session.executionContext.agentId = request.agentId;
		binding.session.executionContext.sessionId = request.sessionId;
		binding.session.executionContext.account = "DU12345";
		binding.session.executionContext.venue = "IB";
		binding.session.environment = "PAPER";
		binding.executionDomain = "PAPER:alpha";
		binding.expiresAtMs =
			static_cast<std::uint64_t>(OmsJournal::NowEpochMs()) +
			request.ttlMs;
		binding.leaseGeneration = 1;
		return true;
	};
	assert(server.Start(
		socketPath, issuers, resolver, reason, 16384, 1000));

	SessionSupervisorRequest ownerA;
	ownerA.operation = SessionSupervisorOperation::Provision;
	ownerA.templateId = "paper";
	ownerA.token = std::string(64, 'a');
	ownerA.agentId = "finalization-agent-a";
	ownerA.sessionId = "finalization-session-a";
	ownerA.peerUid = static_cast<std::uint32_t>(getuid());
	ownerA.ttlMs = 120000;
	SessionSupervisorRequest ownerB = ownerA;
	ownerB.token = std::string(64, 'b');
	ownerB.agentId = "finalization-agent-b";
	ownerB.sessionId = "finalization-session-b";
	const std::string ownerATokenFile =
		WriteSessionCtlTokenFile(ownerA.token);
	const std::string ownerBTokenFile =
		WriteSessionCtlTokenFile(ownerB.token);
	assert(Call(socketPath, ownerA).accepted);
	assert(Call(socketPath, ownerB).accepted);
	for (int i = 0; i < 2; ++i)
	{
		const std::string commandId = i == 0 ?
			"external-recovery-query-owner-a" :
			"external-recovery-query-owner-b";
		const SessionCtlProcessResult transitioned = RunSessionCtl({
			"--socket", socketPath, "recovery-query", "--token-file",
			i == 0 ? ownerATokenFile : ownerBTokenFile,
			"--generation", "1", "--command-id", commandId,
			"--require-paper-finalization"});
		assert(transitioned.exitCode == 0);
		assert(transitioned.standardOutput.find(
			"\"paper_finalization_required\":true") !=
			std::string::npos);
		if (i == 0)
			std::cout << "HEPTA_REAL_CLI_EXTERNAL_QUERY="
				<< transitioned.standardOutput;
	}
	for (int i = 0; i < 2; ++i)
	{
		const SessionCtlProcessResult result = RunSessionCtl({
			"--socket", socketPath, "revoke", "--token-file",
			i == 0 ? ownerATokenFile : ownerBTokenFile,
			"--generation", "1"});
		assert(result.exitCode == 4);
		assert(result.standardOutput.find(
			"\"reason_code\":\"PAPER_FINALIZATION_OPERATION_REQUIRED\"") !=
			std::string::npos);
		if (i == 0)
			std::cout << "HEPTA_REAL_CLI_EXTERNAL_REVOKE="
				<< result.standardOutput;
	}
	assert(store.List().size() == 2 && host.SessionCount() == 2);
	assert(store.List()[0].paperFinalizationRequired &&
		store.List()[1].paperFinalizationRequired);
	SessionSupervisorRequest omittedFlag;
	omittedFlag.operation = SessionSupervisorOperation::RecoveryQuery;
	omittedFlag.token = ownerA.token;
	omittedFlag.expectedGeneration = 1;
	omittedFlag.targetCommandId = "external-downgrade-bypass";
	const SessionSupervisorResult downgradeBypass =
		Call(socketPath, omittedFlag);
	assert(!downgradeBypass.accepted);
	assert(downgradeBypass.paperFinalizationRequired);
	assert(downgradeBypass.ReasonCode() ==
		"PAPER_FINALIZATION_DOWNGRADE_REJECTED");
	SessionSupervisorRequest forbiddenMutation;
	forbiddenMutation.operation = SessionSupervisorOperation::Renew;
	forbiddenMutation.token = ownerA.token;
	forbiddenMutation.expectedGeneration = 1;
	forbiddenMutation.ttlMs = 120000;
	assert(!Call(socketPath, forbiddenMutation).accepted);
	forbiddenMutation.operation = SessionSupervisorOperation::Rotate;
	forbiddenMutation.replacementToken = std::string(64, 'c');
	assert(!Call(socketPath, forbiddenMutation).accepted);
	std::uint64_t reapAt = 0;
	for (std::size_t i = 0; i < store.List().size(); ++i)
		reapAt = std::max(reapAt, store.List()[i].expiresAtMs);
	std::size_t reaped = 0;
	assert(server.ReapExpired(reapAt + 1, reaped, reason));
	assert(reaped == 0 && store.List().size() == 2);
	assert(store.List()[0].paperFinalizationRequired &&
		store.List()[1].paperFinalizationRequired);
	std::string ownerSetSha256;
	assert(SessionSupervisorLeaseStore::PaperOwnerSetSha256(
		store.List(), ownerSetSha256, reason));

	SessionSupervisorRequest finalizeA;
	finalizeA.operation = SessionSupervisorOperation::PaperFinalize;
	finalizeA.token = ownerA.token;
	finalizeA.expectedGeneration = 1;
	finalizeA.recoveryId = "recovery-multiowner-1";
	finalizeA.finalizationId = "finalization-multiowner-1";
	finalizeA.expectedOwnerSetSha256 = ownerSetSha256;
	finalizeA.expectedOwnerCount = 2;
	SessionSupervisorRequest finalizeB = finalizeA;
	finalizeB.token = ownerB.token;

	// Wrong generation/set/group are rejected before any HSL7 mutation.
	SessionSupervisorRequest wrong = finalizeA;
	wrong.expectedGeneration = 2;
	assert(!Call(socketPath, wrong).accepted);
	wrong = finalizeA;
	wrong.expectedOwnerSetSha256 = "sha256:" + std::string(64, 'f');
	assert(!Call(socketPath, wrong).accepted);
	assert(store.List()[0].paperFinalizationState ==
		SessionSupervisorPaperFinalizationState::None);
	assert(store.List()[1].paperFinalizationState ==
		SessionSupervisorPaperFinalizationState::None);

	// Crash before PENDING has no durable effect.
	server.SetCrashPointHook([](const std::string& point) {
		return point == "before_paper_finalization_pending_commit";
	});
	SessionSupervisorResult fault = Call(socketPath, finalizeA);
	assert(!fault.accepted && fault.PaperFinalizationState() == "NONE");
	assert(store.List()[0].paperFinalizationState ==
		SessionSupervisorPaperFinalizationState::None);

	// PENDING commit survives a lost response; restart reconstructs a
	// disabled exact tombstone and retry repeats the remote fence.
	server.SetCrashPointHook([](const std::string& point) {
		return point == "after_paper_finalization_pending_commit";
	});
	fault = Call(socketPath, finalizeA);
	assert(!fault.accepted &&
		fault.PaperFinalizationState() == "FENCE_PENDING");
	SessionSupervisorLeaseRecord pendingA;
	assert(store.Get(ownerA.token, pendingA));
	assert(pendingA.paperFinalizationState ==
		SessionSupervisorPaperFinalizationState::FencePending);
	server.Stop();
	{
		TradingToolHost restartedHost(registry);
		restartedHost.SetRecoveryControlAuthority(&recoveryAuthority);
		TradingToolSessionControlPlane restartedControl(
			restartedHost, authorizer);
		UnixSessionSupervisorServer restarted(restartedControl);
		restarted.SetLeaseStore(&store);
		restarted.SetRootCustodianUidForTests(
			static_cast<std::uint32_t>(getuid()));
		assert(restarted.Start(
			socketPath, issuers, resolver, reason, 16384, 1000));
		TradingToolHostSessionBinding tombstone;
		assert(restartedHost.GetSession(ownerA.token, tombstone));
		assert(!tombstone.enabled && tombstone.recoveryOnly);
		TradingToolHostRequest blocked;
		blocked.sessionToken = ownerA.token;
		blocked.toolCallId = "blocked-before-audit";
		assert(restartedHost.Invoke(ownerA.peerUid, blocked).reasonCode ==
			"SESSION_DISABLED");
		restarted.SetCrashPointHook([](const std::string& point) {
			return point ==
				"after_paper_finalization_remote_fence_before_complete_commit";
		});
		fault = Call(socketPath, finalizeA);
		assert(!fault.accepted &&
			fault.PaperFinalizationState() == "FENCE_PENDING");
		assert(store.Get(ownerA.token, pendingA));
		assert(pendingA.paperFinalizationState ==
			SessionSupervisorPaperFinalizationState::FencePending);
		restarted.SetCrashPointHook(
			[](const std::string&) { return false; });
		restarted.SetCrashPointHook([](const std::string& point) {
			return point ==
				"after_paper_finalization_complete_commit";
		});
		fault = Call(socketPath, finalizeA);
		assert(!fault.accepted);
		assert(fault.PaperFinalizationState() == "FENCE_COMPLETE");
		assert(store.Get(ownerA.token, pendingA));
		assert(pendingA.paperFinalizationState ==
			SessionSupervisorPaperFinalizationState::FenceComplete);
		restarted.SetCrashPointHook(
			[](const std::string&) { return false; });
		const SessionCtlProcessResult groupPendingCli = RunSessionCtl({
			"--socket", socketPath, "paper-finalize", "--token-file",
			ownerATokenFile, "--generation", "1", "--recovery-id",
			finalizeA.recoveryId, "--finalization-id",
			finalizeA.finalizationId, "--expected-owner-set-sha256",
			ownerSetSha256, "--expected-owner-count", "2"});
		assert(groupPendingCli.exitCode == 4);
		assert(groupPendingCli.standardOutput.find(
			"\"reason_code\":\"PAPER_FINALIZATION_GROUP_PENDING\"") !=
			std::string::npos);
		std::cout << "HEPTA_REAL_CLI_GROUP_PENDING="
			<< groupPendingCli.standardOutput;
		const SessionSupervisorResult groupPending =
			Call(socketPath, finalizeA);
		assert(!groupPending.accepted);
		assert(groupPending.ReasonCode() ==
			"PAPER_FINALIZATION_GROUP_PENDING");
		assert(groupPending.PaperFinalizationState() == "FENCE_COMPLETE");
		assert(!groupPending.ownerAuditAuthoritative &&
			!groupPending.ownerAuditComplete);
		assert(restartedHost.GetSession(ownerA.token, tombstone));
		assert(!tombstone.enabled);
		assert(restartedHost.GetContractCatalogSnapshot().sessionCount == 2);

		wrong = finalizeB;
		wrong.finalizationId = "wrong-finalization";
		assert(!Call(socketPath, wrong).accepted);
		SessionSupervisorLeaseRecord untouchedB;
		assert(store.Get(ownerB.token, untouchedB));
		assert(untouchedB.paperFinalizationState ==
			SessionSupervisorPaperFinalizationState::None);

		// Owner B uncertain blocks group sealing; then a drifted global
		// barrier blocks it again. Only identical per-owner barriers seal.
		auditMode.store(1);
		SessionSupervisorResult rejected = Call(socketPath, finalizeB);
		assert(!rejected.accepted);
		assert(rejected.PaperFinalizationState() == "FENCE_COMPLETE");
		assert(rejected.ReasonCode() ==
			"RECOVERY_OWNER_UNCERTAIN_COMMANDS_REMAIN");
		assert(store.Get(ownerA.token, pendingA));
		assert(pendingA.paperFinalizationState ==
			SessionSupervisorPaperFinalizationState::FenceComplete);
		auditMode.store(2);
		rejected = Call(socketPath, finalizeB);
		assert(!rejected.accepted);
		assert(rejected.ReasonCode() ==
			"PAPER_FINALIZATION_COMPOSITE_BARRIER_DRIFT");
		auditMode.store(0);
		restarted.SetCrashPointHook([](const std::string& point) {
			return point ==
				"after_paper_finalization_audit_before_seal_commit";
		});
		rejected = Call(socketPath, finalizeB);
		assert(!rejected.accepted);
		assert(rejected.PaperFinalizationState() == "FENCE_COMPLETE");
		restarted.SetCrashPointHook([](const std::string& point) {
			return point ==
				"after_paper_finalization_audit_seal_commit";
		});
		rejected = Call(socketPath, finalizeB);
		assert(!rejected.accepted);
		assert(rejected.PaperFinalizationState() == "AUDIT_SEALED");
		restarted.SetCrashPointHook(
			[](const std::string&) { return false; });
		const SessionCtlProcessResult sealedCli = RunSessionCtl({
			"--socket", socketPath, "paper-finalize", "--token-file",
			ownerBTokenFile, "--generation", "1", "--recovery-id",
			finalizeB.recoveryId, "--finalization-id",
			finalizeB.finalizationId, "--expected-owner-set-sha256",
			ownerSetSha256, "--expected-owner-count", "2"});
		assert(sealedCli.exitCode == 0);
		assert(sealedCli.standardOutput.find(
			"\"paper_finalization_state\":\"AUDIT_SEALED\"") !=
			std::string::npos);
		std::cout << "HEPTA_REAL_CLI_AUDIT_SEALED="
			<< sealedCli.standardOutput;
		const SessionSupervisorResult sealed =
			Call(socketPath, finalizeB);
		assert(sealed.accepted);
		assert(sealed.ReasonCode() ==
			"PAPER_FINALIZATION_AUDIT_SEALED");
		assert(sealed.PaperFinalizationState() == "AUDIT_SEALED");
		assert(!sealed.FinalizationReceipt().empty());
		assert(sealed.FinalizationReceipt()[
			sealed.FinalizationReceipt().size() - 1] == '\n');
		assert(sealed.brokerExposureGeneration == 0 &&
			sealed.brokerTerminalExposureGeneration == 0 &&
			sealed.brokerRiskAbsorbedExposureGeneration == 0);
		assert(restartedHost.SessionCount() == 2);
		assert(restartedHost.GetContractCatalogSnapshot().sessionCount == 2);
		const SessionSupervisorResult replay =
			Call(socketPath, finalizeB);
		assert(replay.accepted);
		assert(replay.FinalizationReceiptSha256() ==
			sealed.FinalizationReceiptSha256());
		assert(replay.FinalizationReceipt() ==
			sealed.FinalizationReceipt());

		std::vector<SessionSupervisorLeaseRecord> sealedRecords =
			store.List();
		assert(sealedRecords.size() == 2);
		std::sort(sealedRecords.begin(), sealedRecords.end(),
			[](const SessionSupervisorLeaseRecord& left,
				const SessionSupervisorLeaseRecord& right) {
				return left.ownerTokenSha256 < right.ownerTokenSha256;
			});
		SessionSupervisorRequest acknowledge = finalizeA;
		acknowledge.operation =
			SessionSupervisorOperation::PaperTerminalWitnessAck;
		acknowledge.token = sealedRecords.front().token;
		acknowledge.expectedGeneration =
			sealedRecords.front().leaseGeneration;
		acknowledge.receiptSha256 =
			sealed.FinalizationReceiptSha256();
		acknowledge.terminalEvidence = BuildTerminalEvidence(
			sealedRecords, sealedRecords.front(), acknowledge, sealed);
		acknowledge.terminalEvidenceSha256 =
			Sha256Prefixed(acknowledge.terminalEvidence);
		SessionSupervisorRequest legacyAck = acknowledge;
		legacyAck.operation = SessionSupervisorOperation::PaperFinalizeAck;
		legacyAck.terminalEvidence.clear();
		legacyAck.terminalEvidenceSha256.clear();
		const SessionSupervisorResult legacyRejected =
			Call(socketPath, legacyAck);
		assert(!legacyRejected.accepted);
		assert(legacyRejected.ReasonCode() ==
			"PAPER_FINALIZATION_LEGACY_ACK_DISABLED");
		SessionSupervisorRequest localV2 = acknowledge;
		localV2.operation = SessionSupervisorOperation::PaperTerminalizeAck;
		localV2.terminalEvidence.clear();
		localV2.terminalEvidenceSha256.clear();
		const SessionSupervisorResult localV2Rejected =
			Call(socketPath, localV2);
		assert(!localV2Rejected.accepted);
		assert(localV2Rejected.ReasonCode() ==
			"PAPER_TERMINAL_ACK_V2_DISABLED");
		assert(terminalizeCalls.load() == 0);
		const std::string acknowledgeTokenFile =
			acknowledge.token == ownerA.token ?
				ownerATokenFile : ownerBTokenFile;
		SessionSupervisorRequest prepare = acknowledge;
		prepare.operation =
			SessionSupervisorOperation::PaperTerminalWitnessPrepare;
		prepare.terminalEvidence.clear();
		prepare.terminalEvidenceSha256.clear();
		SessionSupervisorRequest wrongPrepare = prepare;
		wrongPrepare.token = sealedRecords.back().token;
		wrongPrepare.expectedGeneration =
			sealedRecords.back().leaseGeneration;
		const SessionSupervisorResult wrongPrepareResult =
			Call(socketPath, wrongPrepare);
		assert(!wrongPrepareResult.accepted);
		assert(wrongPrepareResult.ReasonCode() ==
			"PAPER_TERMINAL_WITNESS_PREPARE_OWNER_NOT_DETERMINISTIC");
		assert(terminalizeCalls.load() == 0);

		// An exact HPT1 intent observed before the in-memory mutation gate and
		// transport cutoff is never promoted to a completed prepare.
		terminalizeMode.store(1);
		const SessionSupervisorResult intentPending =
			Call(socketPath, prepare);
		assert(!intentPending.accepted);
		assert(intentPending.ReasonCode() ==
			"PAPER_TERMINAL_WITNESS_PREPARE_INTENT_PENDING");
		assert(intentPending.PaperFinalizationState() == "AUDIT_SEALED");
		assert(store.List().size() == 2 &&
			restartedHost.SessionCount() == 2);
		assert(terminalizeCalls.load() == 1);

		// A completed transport cut whose response is lost leaves no local
		// authority/store mutation. A restart-style INCOMPLETE replay remains
		// pending until root establishes the external cutoff.
		terminalizeMode.store(0);
		restarted.SetCrashPointHook([](const std::string& point) {
			return point ==
				"after_paper_terminal_witness_prepare_before_reply";
		});
		const SessionSupervisorResult prepareReplyFault =
			Call(socketPath, prepare);
		assert(!prepareReplyFault.accepted);
		assert(prepareReplyFault.ReasonCode() ==
			"SUPERVISOR_FAULT_INJECTED:"
			"after_paper_terminal_witness_prepare_before_reply");
		assert(store.List().size() == 2 &&
			restartedHost.SessionCount() == 2);
		assert(terminalizeCalls.load() == 2);
		restarted.SetCrashPointHook(
			[](const std::string&) { return false; });
		terminalizeMode.store(1);
		const SessionSupervisorResult prepareRestartPending =
			Call(socketPath, prepare);
		assert(!prepareRestartPending.accepted);
		assert(prepareRestartPending.ReasonCode() ==
			"PAPER_TERMINAL_WITNESS_PREPARE_INTENT_PENDING");
		assert(terminalizeCalls.load() == 3);

		terminalizeMode.store(0);
		const SessionCtlProcessResult preparedCli = RunSessionCtl({
			"--socket", socketPath, "paper-terminal-witness-prepare",
			"--token-file", acknowledgeTokenFile, "--generation",
			std::to_string(prepare.expectedGeneration), "--recovery-id",
			prepare.recoveryId, "--finalization-id", prepare.finalizationId,
			"--expected-owner-set-sha256",
			prepare.expectedOwnerSetSha256, "--expected-owner-count", "2",
			"--receipt-sha256", prepare.receiptSha256});
		assert(preparedCli.exitCode == 0);
		assert(preparedCli.standardOutput.find(
			"\"reason_code\":\"PAPER_TERMINAL_WITNESS_PREPARED\"") !=
			std::string::npos);
		assert(preparedCli.standardOutput.find(
			"\"paper_finalization_state\":\"AUDIT_SEALED\"") !=
			std::string::npos);
		assert(store.List().size() == 2 &&
			restartedHost.SessionCount() == 2);
		assert(terminalizeCalls.load() == 4);
		SessionSupervisorRequest nondeterministic = acknowledge;
		nondeterministic.token = sealedRecords.back().token;
		nondeterministic.expectedGeneration =
			sealedRecords.back().leaseGeneration;
		const SessionSupervisorResult wrongAck =
			Call(socketPath, nondeterministic);
		assert(!wrongAck.accepted);
		assert(wrongAck.ReasonCode() ==
			"PAPER_TERMINAL_ACK_OWNER_NOT_DETERMINISTIC");
		assert(store.List().size() == 2 &&
			restartedHost.SessionCount() == 2);
		SessionSupervisorRequest tamperedEvidence = acknowledge;
		tamperedEvidence.terminalEvidence = RewriteTerminalEvidenceField(
			acknowledge.terminalEvidence, "owner_account", "DU99999");
		tamperedEvidence.terminalEvidenceSha256 = Sha256Prefixed(
			tamperedEvidence.terminalEvidence);
		const SessionSupervisorResult tamperedEvidenceRejected =
			Call(socketPath, tamperedEvidence);
		assert(!tamperedEvidenceRejected.accepted);
		assert(tamperedEvidenceRejected.ReasonCode() ==
			"PAPER_TERMINAL_EVIDENCE_BINDING_MISMATCH");
		assert(store.List().size() == 2 &&
			restartedHost.SessionCount() == 2);

		restarted.SetCrashPointHook([](const std::string& point) {
			return point ==
				"after_paper_terminal_witness_evidence_before_bearer_purge";
		});
		const SessionSupervisorResult haltFault =
			Call(socketPath, acknowledge);
		assert(!haltFault.accepted);
		assert(haltFault.PaperFinalizationState() == "AUDIT_SEALED");
		assert(!haltFault.terminalCurrentEvidenceVerified);
		assert(!haltFault.terminalReplay);
		assert(store.List().size() == 2);
		assert(restartedHost.SessionCount() == 2);

		restarted.SetCrashPointHook([](const std::string& point) {
			return point ==
				"after_paper_terminal_witness_partial_bearer_purge";
		});
		const SessionSupervisorResult partialPurgeFault =
			Call(socketPath, acknowledge);
		assert(!partialPurgeFault.accepted);
		assert(partialPurgeFault.ReasonCode() ==
			"SUPERVISOR_FAULT_INJECTED:"
			"after_paper_terminal_witness_partial_bearer_purge");
		assert(partialPurgeFault.PaperFinalizationState() == "AUDIT_SEALED");
		assert(!partialPurgeFault.terminalCurrentEvidenceVerified);
		assert(store.List().size() == 2);
		assert(restartedHost.SessionCount() == 1);
		assert(restartedHost.GetContractCatalogSnapshot().sessionCount == 1);

		restarted.SetCrashPointHook([](const std::string& point) {
			return point ==
				"after_paper_terminal_witness_bearer_purge_before_ack_commit";
		});
		const SessionSupervisorResult purgeFault =
			Call(socketPath, acknowledge);
		assert(!purgeFault.accepted);
		assert(purgeFault.PaperFinalizationState() == "AUDIT_SEALED");
		assert(!purgeFault.terminalCurrentEvidenceVerified);
		assert(!purgeFault.terminalReplay);
		assert(store.List().size() == 2);
		assert(restartedHost.SessionCount() == 0);
		assert(restartedHost.GetContractCatalogSnapshot().sessionCount == 0);
		restarted.SetCrashPointHook([](const std::string& point) {
			return point == "after_paper_terminal_witness_ack_commit";
		});
		std::string evidenceDirectory;
		const std::string evidenceFile = WriteSessionCtlEvidenceFile(
			acknowledge.terminalEvidence, evidenceDirectory);
		const SessionCtlProcessResult lostAckCli = RunSessionCtl({
			"--socket", socketPath, "paper-terminal-witness-ack", "--token-file",
			acknowledgeTokenFile, "--generation",
			std::to_string(acknowledge.expectedGeneration), "--recovery-id",
			acknowledge.recoveryId, "--finalization-id",
			acknowledge.finalizationId, "--expected-owner-set-sha256",
			acknowledge.expectedOwnerSetSha256, "--expected-owner-count",
			"2", "--receipt-sha256", acknowledge.receiptSha256,
			"--terminal-evidence-file", evidenceFile,
			"--terminal-evidence-sha256",
			acknowledge.terminalEvidenceSha256});
		assert(lostAckCli.exitCode == 4);
		assert(lostAckCli.standardOutput.find(
			"\"paper_finalization_state\":\"ACKED\"") !=
			std::string::npos);
		std::cout << "HEPTA_REAL_CLI_ACK_COMMITTED_REPLY_FAULT="
			<< lostAckCli.standardOutput;
		assert(store.List().empty());
		assert(restartedHost.SessionCount() == 0);
		assert(restartedHost.GetContractCatalogSnapshot().sessionCount == 0);
		restarted.SetCrashPointHook(
			[](const std::string&) { return false; });
		SessionSupervisorRequest replayTamperedEvidence = acknowledge;
		replayTamperedEvidence.terminalEvidence = RewriteTerminalEvidenceField(
			acknowledge.terminalEvidence, "provider_id",
			"different-reviewed-provider");
		replayTamperedEvidence.terminalEvidenceSha256 = Sha256Prefixed(
			replayTamperedEvidence.terminalEvidence);
		const SessionSupervisorResult replayTamperRejected =
			Call(socketPath, replayTamperedEvidence);
		assert(!replayTamperRejected.accepted);
		assert(replayTamperRejected.ReasonCode() ==
			"PAPER_TERMINAL_ACK_REPLAY_EVIDENCE_MISMATCH");
		const SessionCtlProcessResult ackReplayCli = RunSessionCtl({
			"--socket", socketPath, "paper-terminal-witness-ack", "--token-file",
			acknowledgeTokenFile, "--generation",
			std::to_string(acknowledge.expectedGeneration), "--recovery-id",
			acknowledge.recoveryId, "--finalization-id",
			acknowledge.finalizationId, "--expected-owner-set-sha256",
			acknowledge.expectedOwnerSetSha256, "--expected-owner-count",
			"2", "--receipt-sha256", acknowledge.receiptSha256,
			"--terminal-evidence-file", evidenceFile,
			"--terminal-evidence-sha256",
			acknowledge.terminalEvidenceSha256});
		assert(ackReplayCli.exitCode == 0);
		assert(JsonEncodedStringField(
			lostAckCli.standardOutput,
			"finalization_receipt_sha256") ==
			JsonEncodedStringField(
				ackReplayCli.standardOutput,
				"finalization_receipt_sha256"));
		assert(JsonEncodedStringField(
			lostAckCli.standardOutput, "finalization_receipt") ==
			JsonEncodedStringField(
				ackReplayCli.standardOutput,
				"finalization_receipt"));
		std::cout << "HEPTA_REAL_CLI_ACK_REPLAY="
			<< ackReplayCli.standardOutput;
		const SessionSupervisorResult ackReplay =
			Call(socketPath, acknowledge);
		assert(ackReplay.accepted);
		assert(ackReplay.ReasonCode() ==
			"PAPER_FINALIZATION_TERMINAL_ACKED");
		assert(ackReplay.PaperFinalizationState() == "ACKED");
		assert(ackReplay.PreliminaryFinalizationReceiptSha256() ==
			sealed.FinalizationReceiptSha256());
		assert(ackReplay.FinalizationReceiptSha256() !=
			sealed.FinalizationReceiptSha256());
		assert(ackReplay.FinalizationReceipt().find(
			"schema=hepta.paper-session-terminal-ack-receipt.v3\n") == 0);
		assert(ackReplay.terminalReplay);
		assert(ackReplay.terminalExternalLatchLoaded);
		assert(ackReplay.terminalCurrentEvidenceVerified);
		assert(!ackReplay.terminalBrokerCallbackQueueDrained);
		assert(terminalizeCalls.load() == 4);
		assert(::unlink(evidenceFile.c_str()) == 0);
		assert(::rmdir(evidenceDirectory.c_str()) == 0);
		restarted.Stop();
	}
	assert(fenceCalls.load() >= 3);
	std::remove(storePath.c_str());
	std::remove(keyPath.c_str());
	std::remove(journalPath.c_str());
	std::remove(ownerATokenFile.c_str());
	std::remove(ownerBTokenFile.c_str());
}

void TestExistingSupervisorSocketIsNeverUnlinked()
{
	const std::string journalPath =
		TempPath("/tmp/hepta-supervisor-existing-journal-XXXXXX");
	const std::string socketPath =
		TempPath("/tmp/hepta-supervisor-existing-socket-XXXXXX");
	OmsJournal journal;
	assert(journal.Init(journalPath));
	ExecutionCoordinatorCallbacks callbacks;
	ExecutionCoordinator execution(journal, callbacks);
	TradingToolRegistry registry(execution);
	TradingToolHost host(registry);
	TradingToolSessionControlPlane control(host,
		[](const std::string&, const TradingToolHostSessionBinding&, std::string&) {
			return true;
		});
	std::map<std::uint32_t, std::string> issuers;
	issuers[static_cast<std::uint32_t>(getuid())] = "hepta.os.uid";
	UnixSessionSupervisorServer::BindingResolver resolver =
		[](const SessionSupervisorRequest&, TradingToolHostSessionBinding&, std::string&) {
			return false;
		};

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

	UnixSessionSupervisorServer server(control);
	std::string reason;
	assert(!server.Start(socketPath, issuers, resolver, reason, 4096, 1000));
	assert(reason ==
		"socket path already exists; use activated fd or owner cleanup");
	struct stat preserved;
	assert(lstat(socketPath.c_str(), &preserved) == 0);
	assert(S_ISSOCK(preserved.st_mode));

	close(owner);
	unlink(socketPath.c_str());
	std::remove(journalPath.c_str());
}

} // namespace

int main()
{
	TestPaperFinalizationProtocol();
	TestAuditJournalSecurity();
	TestAuditJournalCacheAndGrowthBounds();
	TestSupervisorPeerCredentialAndLifecycle();
	TestWatchLeaseRestartFailsClosed();
	TestSameHostWatchRestartFencesExactLocalSession();
	TestWatchTransactionReservationRaces();
	TestLeaseCommitCrashConvergenceMatrix();
	TestPaperRecoveryRetriesEventServiceNotReady();
	TestDurableLiveExpiryReap();
	TestWriteAheadActivationFailureFencesOwner();
	TestRejectedRenewFenceRetryUsesExactPredecessorScope();
	TestWatchProvisionRejectsResolverScopeDrift();
	TestRootRecoveryQueryIsDurableAndRestartClosed();
	TestPaperFinalizationMultiOwnerStateMachineAndCrashReplay();
	TestReapSerializesWithRenewCommit();
	TestDurablePendingFenceRestartRecoveryWithReviewedPolicy();
	TestDeterministicGenerationSchedules();
	TestRestartAtEveryPersistedGeneration();
	TestExistingSupervisorSocketIsNeverUnlinked();
	std::cout << "unix_session_supervisor_server_tests: PASS" << std::endl;
	return 0;
}
