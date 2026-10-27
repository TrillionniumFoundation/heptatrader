#include "unix_session_supervisor_test_support.h"

namespace hepta_supervisor_test {

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
	ExerciseSlowIngressIsolation(socketPath);

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
	// Peer authentication precedes frame reads. The server may send its
	// rejection and close before an unauthorized caller can write anything.
	// Requiring WriteFrame success here races that correct early rejection.
	const int deniedClient = Connect(socketPath);
	std::string deniedBody;
	assert(TypedToolProtocol::ReadFrame(deniedClient, 16384, 5000, deniedBody, reason));
	assert(SessionSupervisorProtocol::DecodeResult(deniedBody, result, reason));
	close(deniedClient);
	assert(!result.accepted);
	assert(result.ReasonCode() == "SUPERVISOR_PEER_UID_DENIED");
	assert(host.SessionCount() == 0);
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

void TestDurableAuditBindsOwnerBeforeMutation()
{
    const auto journalPath = TempPath("/tmp/hepta-audit-owner-oms-XXXXXX");
    const auto socketPath = TempPath("/tmp/hepta-audit-owner-socket-XXXXXX");
    const auto storePath = TempPath("/tmp/hepta-audit-owner-store-XXXXXX");
    const auto auditPath = TempPath("/tmp/hepta-audit-owner-journal-XXXXXX");
    const auto keyPath = TempKeyPath();
    std::string reason;
    SessionSupervisorLeaseStore store; assert(store.Init(storePath, keyPath, reason));
    SessionSupervisorAuditJournal audit; assert(audit.Init(auditPath, reason));
    OmsJournal journal; assert(journal.Init(journalPath));
    ExecutionCoordinator execution(journal, ExecutionCoordinatorCallbacks());
    TradingToolRegistry registry(execution); TradingToolHost host(registry);
    TradingToolSessionControlPlane control(host,
        [](const std::string&, const TradingToolHostSessionBinding&, std::string&) { return true; });
    UnixSessionSupervisorServer server(control);
    server.SetLeaseStore(&store); server.SetAuditJournal(&audit);
    std::map<std::uint32_t, std::string> issuers;
    issuers[::getuid()] = "test.audit-owner";
    assert(server.Start(socketPath, issuers,
        [](const SessionSupervisorRequest& request, TradingToolHostSessionBinding& binding, std::string&) {
            binding.token = request.token; binding.peerUid = request.peerUid;
            binding.session.executionContext.agentId = request.agentId;
            binding.session.executionContext.sessionId = request.sessionId;
            binding.session.executionContext.account = "SYNTHETIC";
            binding.session.environment = "WATCH"; binding.executionDomain = "SIM-AUDIT";
            binding.expiresAtMs = OmsJournal::NowEpochMs() + request.ttlMs;
            return true;
        }, reason, 4096, 1000));
    SessionSupervisorRequest provision;
    provision.templateId = "watch"; provision.token = "private-audit-owner-original-bearer";
    provision.agentId = "bound-agent"; provision.sessionId = "bound-session";
    provision.peerUid = ::getuid(); provision.ttlMs = 60000;
    assert(Call(socketPath, provision).accepted);
    SessionSupervisorRequest mutation;
    mutation.operation = SessionSupervisorOperation::Renew;
    mutation.token = provision.token; mutation.expectedGeneration = 1; mutation.ttlMs = 60000;
    assert(mutation.agentId.empty() && mutation.sessionId.empty());
    auto result = Call(socketPath, mutation);
    assert(result.accepted && result.leaseGeneration == 2);
    mutation.operation = SessionSupervisorOperation::Rotate;
    mutation.expectedGeneration = 2; mutation.replacementToken = "private-audit-owner-replacement-bearer";
    result = Call(socketPath, mutation);
    assert(result.accepted && result.leaseGeneration == 3);
    SessionSupervisorLeaseRecord record;
    assert(!store.Get(provision.token, record));
    mutation.operation = SessionSupervisorOperation::Revoke;
    mutation.token = mutation.replacementToken; mutation.expectedGeneration = 3;
    result = Call(socketPath, mutation);
    assert(result.accepted && store.List().empty() && host.SessionCount() == 0);
    const auto bound = AuditPayloads(auditPath);
    assert(bound.size() == 8);
    for (const auto& payload : bound)
    {
        assert(payload.find("agent_id=bound-agent\n") != std::string::npos);
        assert(payload.find("session_id=bound-session\n") != std::string::npos);
        assert(payload.find(provision.token) == std::string::npos);
        assert(payload.find(mutation.replacementToken) == std::string::npos);
    }
    assert(bound[2].find("operation=renew\n") != std::string::npos);
    assert(bound[4].find("operation=rotate\n") != std::string::npos);
    assert(bound[6].find("operation=revoke\n") != std::string::npos);
    // Missing durable authority cannot acquire an attribution from caller
    // strings or from the last successful operation on this worker.
    mutation.agentId = "caller-claimed-agent"; mutation.sessionId = "caller-claimed-session";
    assert(!Call(socketPath, mutation).accepted);
    const auto absent = AuditPayloads(auditPath);
    assert(absent.size() == 10);
    for (std::size_t i = 8; i < absent.size(); ++i)
    {
        assert(absent[i].find("agent_id=\nsession_id=\n") != std::string::npos);
        assert(absent[i].find("caller-claimed") == std::string::npos);
    }
    server.Stop();
    std::uint64_t records = 0;
    assert(SessionSupervisorAuditJournal::Verify(auditPath, records, reason) && records == 10);
    for (const auto& path : {journalPath, storePath, auditPath, keyPath}) std::remove(path.c_str());
}

// Real local HSS1 server and encrypted lease store, WATCH-only. There is
// no Broker transport, PAPER session or order submission in this fixture.
void TestAuditReserveRejectsRenewalBeforeDurableLeaseMutation()
{
    char pattern[] = "/tmp/hepta-supervisor-audit-reserve-XXXXXX";
    const char* created = ::mkdtemp(pattern); assert(created != nullptr);
    const std::string root = created;
    const std::string socketPath = root + "/supervisor.sock";
    const std::string journalPath = root + "/oms";
    const std::string storePath = root + "/leases";
    const std::string auditPath = root + "/audit";
    const std::string keyPath = TempKeyPath();
    {
        std::string reason;
        SessionSupervisorLeaseStore store;
        assert(store.Init(storePath, keyPath, reason));
        SessionSupervisorAuditJournal audit(16384, 4096);
        assert(audit.Init(auditPath, reason));
        OmsJournal journal; assert(journal.Init(journalPath));
        ExecutionCoordinator execution(journal, ExecutionCoordinatorCallbacks());
        TradingToolRegistry registry(execution);
        TradingToolHost host(registry);
        TradingToolSessionControlPlane control(host,
            [](const std::string& issuer, const TradingToolHostSessionBinding&, std::string&) {
                return issuer == "hepta.os.uid";
            });
        UnixSessionSupervisorServer server(control);
        server.SetLeaseStore(&store);
        server.SetAuditJournal(&audit);
        std::map<std::uint32_t, std::string> issuers;
        issuers[static_cast<std::uint32_t>(::getuid())] = "hepta.os.uid";
        assert(server.Start(socketPath, issuers,
            [](const SessionSupervisorRequest& request, TradingToolHostSessionBinding& binding,
               std::string&) {
                if (request.templateId != "watch" || request.peerUid != ::getuid()) return false;
                binding.token = request.token;
                binding.peerUid = request.peerUid;
                binding.session.executionContext.agentId = request.agentId;
                binding.session.executionContext.sessionId = request.sessionId;
                binding.session.executionContext.account = "SYNTHETIC";
                binding.session.executionContext.venue = "SIM";
                binding.session.environment = "WATCH";
                binding.session.capabilities.insert("market.read");
                binding.executionDomain = "SIM-AUDIT-RESERVE";
                binding.expiresAtMs = OmsJournal::NowEpochMs() + request.ttlMs;
                return true;
            }, reason, 4096, 1000));
        SessionSupervisorRequest provision;
        provision.operation = SessionSupervisorOperation::Provision;
        provision.templateId = "watch";
        provision.token = "synthetic-audit-reserve-watch-token-0001";
        provision.agentId = "synthetic-audit-agent";
        provision.sessionId = "synthetic-audit-session";
        provision.peerUid = ::getuid();
        provision.ttlMs = 60000;
        const auto initial = Call(socketPath, provision);
        assert(initial.accepted && initial.leaseGeneration == 1);
        SessionSupervisorLeaseRecord before, after;
        assert(store.Get(provision.token, before));
        SessionSupervisorRequest renew;
        renew.operation = SessionSupervisorOperation::Renew;
        renew.token = provision.token;
        renew.expectedGeneration = 1;
        renew.ttlMs = 120000;
        unsigned records = 0;
        while (audit.Append(renew, "hepta.os.uid", "intent", "pending", 1, reason))
            assert(++records < 100);
        assert(reason == "SUPERVISOR_AUDIT_EXIT_RESERVE_REQUIRED");
        const auto denied = Call(socketPath, renew);
        assert(!denied.accepted);
        assert(denied.ReasonCode() ==
            "SUPERVISOR_AUDIT_INTENT_FAILED:SUPERVISOR_AUDIT_EXIT_RESERVE_REQUIRED");
        assert(store.Get(provision.token, after));
        assert(after.leaseGeneration == before.leaseGeneration);
        assert(after.expiresAtMs == before.expiresAtMs);
        TradingToolHostSessionBinding live;
        assert(host.GetSession(provision.token, live));
        assert(live.leaseGeneration == before.leaseGeneration && live.expiresAtMs == before.expiresAtMs);
        SessionSupervisorRequest revoke;
        revoke.operation = SessionSupervisorOperation::Revoke;
        revoke.token = provision.token;
        revoke.expectedGeneration = 1;
        const auto exited = Call(socketPath, revoke);
        assert(exited.accepted);
        assert(store.List().empty() && host.SessionCount() == 0);
        server.Stop();
        SessionSupervisorLeaseStore reopened;
        assert(reopened.Init(storePath, keyPath, reason));
        assert(reopened.List().empty());
        std::uint64_t auditRecords = 0;
        assert(SessionSupervisorAuditJournal::Verify(auditPath, auditRecords, reason));
        assert(auditRecords == records + 4); // provision + revoke intent/outcome pairs
    }
    for (const auto& path : {journalPath, storePath, auditPath, keyPath})
        assert(::unlink(path.c_str()) == 0);
    assert(::rmdir(root.c_str()) == 0);
}


} // namespace hepta_supervisor_test
