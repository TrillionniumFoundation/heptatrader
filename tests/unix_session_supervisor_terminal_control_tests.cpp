#include "unix_session_supervisor_test_support.h"

namespace hepta_supervisor_test {

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

void TestReapPagesAdvancePastUnexpiredOwners()
{
    const std::string journalPath = TempPath("/tmp/hepta-reap-page-journal-XXXXXX");
    const std::string socketPath = TempPath("/tmp/hepta-reap-page-socket-XXXXXX");
    const std::string storePath = TempPath("/tmp/hepta-reap-page-store-XXXXXX");
    const std::string keyPath = TempKeyPath();
    SessionSupervisorLeaseStore store; std::string reason;
    assert(store.Init(storePath, keyPath, reason));
    OmsJournal journal; assert(journal.Init(journalPath));
    ExecutionCoordinatorCallbacks callbacks; ExecutionCoordinator execution(journal, callbacks);
    TradingToolRegistry registry(execution); TradingToolHost host(registry);
    unsigned fences = 0;
    host.SetSessionRevokedObserver([&](const TradingToolHostSessionBinding& binding,
        const std::string&, std::string&) {
        assert(!binding.enabled); ++fences; return true;
    });
    TradingToolSessionControlPlane control(host,
        [](const std::string&, const TradingToolHostSessionBinding&, std::string&) { return true; });
    UnixSessionSupervisorServer server(control); server.SetLeaseStore(&store);
    std::map<std::uint32_t, std::string> issuers; issuers[::getuid()] = "page-issuer";
    assert(server.Start(socketPath, issuers,
        [](const SessionSupervisorRequest& request, TradingToolHostSessionBinding& binding, std::string&) {
            binding.token = request.token; binding.peerUid = request.peerUid;
            binding.session.executionContext.agentId = request.agentId;
            binding.session.executionContext.sessionId = request.sessionId;
            binding.session.executionContext.account = "DU123";
            binding.session.environment = "WATCH"; binding.executionDomain = "IB-PAPER";
            binding.expiresAtMs = OmsJournal::NowEpochMs() + request.ttlMs;
            return true;
        }, reason, 4096, 1000));
    const auto now = static_cast<std::uint64_t>(OmsJournal::NowEpochMs());
    for (unsigned i = 0; i < 40; ++i)
    {
        const std::string id = (i < 10 ? "0" : "") + std::to_string(i);
        SessionSupervisorLeaseRecord record;
        record.templateId = "watch"; record.issuer = "page-issuer";
        record.token = "reap-page-owner-token-000-" + id;
        record.agentId = "page-agent"; record.sessionId = "page-session-" + id;
        record.peerUid = ::getuid(); record.leaseGeneration = 1;
        record.expiresAtMs = i >= 38 ? now - 1 : now + 600000;
        assert(store.Put(record, reason));
    }
    unsigned passes = 0; std::size_t total = 0;
    while (total < 2 && passes < 8)
    {
        std::size_t reaped = 0;
        assert(server.ReapExpired(now, reaped, reason));
        if (passes < 2) assert(reaped == 0);
        total += reaped; ++passes;
    }
    assert(total == 2 && fences == 2 && store.List().size() == 38);
    server.Stop();
    std::cout << "SUPERVISOR_REAP_PAGES passes=" << passes << " expired=2 preserved=38\n";
    std::remove(storePath.c_str()); std::remove(keyPath.c_str()); std::remove(journalPath.c_str());
}

void TestSlowRecoveryOwnerDoesNotBlockUnrelatedOwner()
{
    const std::string journalPath = TempPath("/tmp/hepta-work-budget-journal-XXXXXX");
    const std::string socketPath = TempPath("/tmp/hepta-work-budget-socket-XXXXXX");
    const std::string storePath = TempPath("/tmp/hepta-work-budget-store-XXXXXX");
    const std::string keyPath = TempKeyPath();
    SessionSupervisorLeaseStore store; std::string reason;
    assert(store.Init(storePath, keyPath, reason));
    OmsJournal journal; assert(journal.Init(journalPath));
    ExecutionCoordinatorCallbacks callbacks;
    ExecutionCoordinator execution(journal, callbacks);
    TradingToolRegistry registry(execution); TradingToolHost host(registry);
    RecoveryControlAuthority authority;
    std::atomic<bool> entered{false}, release{false};
    authority.ownerAudit = [&](const ExecutionControlCommand& command) {
        assert(command.localDeadline != std::chrono::steady_clock::time_point::max());
        entered.store(true);
        while (!release.load() && std::chrono::steady_clock::now() < command.localDeadline)
            std::this_thread::sleep_for(std::chrono::milliseconds(1));
        ExecutionControlResult result;
        result.status = ExecutionCommandStatus::Uncertain;
        result.commandId = command.context.toolCallId;
        result.reasonCode = "TEST_OWNER_AUDIT_PENDING";
        return result;
    };
    authority.query = [](const ExecutionControlCommand& command) {
        ExecutionControlResult result; result.commandId = command.context.toolCallId;
        result.targetCommandId = command.targetCommandId;
        result.status = ExecutionCommandStatus::Uncertain; return result;
    };
    host.SetRecoveryControlAuthority(&authority);
    TradingToolSessionControlPlane control(host,
        [](const std::string& issuer, const TradingToolHostSessionBinding&, std::string&) {
            return issuer == "hepta.os.uid";
        });
    UnixSessionSupervisorServer server(control);
    server.SetLeaseStore(&store); server.SetRootCustodianUidForTests(::getuid());
    std::map<std::uint32_t, std::string> issuers;
    issuers[::getuid()] = "hepta.os.uid";
    assert(server.Start(socketPath, issuers,
        [](const SessionSupervisorRequest& request, TradingToolHostSessionBinding& binding, std::string&) {
            binding.token = request.token; binding.peerUid = request.peerUid;
            binding.session.executionContext.agentId = request.agentId;
            binding.session.executionContext.sessionId = request.sessionId;
            binding.session.executionContext.account = "DU123";
            binding.session.environment = request.templateId == "paper" ? "PAPER" : "WATCH";
            binding.executionDomain = "IB-PAPER";
            binding.expiresAtMs = OmsJournal::NowEpochMs() + request.ttlMs;
            return true;
        }, reason, 16384, 1500));
    SessionSupervisorRequest provision; provision.operation = SessionSupervisorOperation::Provision;
    provision.templateId = "paper"; provision.token = "work-budget-paper-owner-token-0001";
    provision.agentId = "work-agent"; provision.sessionId = "slow-owner";
    provision.peerUid = ::getuid(); provision.ttlMs = 60000;
    assert(Call(socketPath, provision).accepted);
    SessionSupervisorRequest recovery; recovery.operation = SessionSupervisorOperation::RecoveryQuery;
    recovery.token = provision.token; recovery.expectedGeneration = 1;
    recovery.targetCommandId = "work-budget-command";
    auto pending = std::async(std::launch::async, [&] { return Call(socketPath, recovery); });
    const auto waitDeadline = std::chrono::steady_clock::now() + std::chrono::seconds(3);
    while (!entered.load() && std::chrono::steady_clock::now() < waitDeadline)
        std::this_thread::sleep_for(std::chrono::milliseconds(1));
    assert(entered.load());
    SessionSupervisorRequest sameOwner = recovery;
    sameOwner.operation = SessionSupervisorOperation::Renew;
    sameOwner.targetCommandId.clear(); sameOwner.ttlMs = 60000;
    const auto start = std::chrono::steady_clock::now();
    const auto denied = Call(socketPath, sameOwner);
    auto unrelated = provision; unrelated.templateId = "watch";
    unrelated.token = "work-budget-watch-owner-token-0001";
    unrelated.sessionId = "independent-owner";
    const auto independent = Call(socketPath, unrelated);
    const bool completedWhileSlow = !release.load() &&
        pending.wait_for(std::chrono::milliseconds(0)) != std::future_status::ready;
    const auto controlNs = std::chrono::duration_cast<std::chrono::nanoseconds>(
        std::chrono::steady_clock::now() - start).count();
    release.store(true);
    const auto recovered = pending.get();
    assert(!denied.accepted && denied.ReasonCode() == "SUPERVISOR_OWNER_WORK_PENDING");
    assert(independent.accepted && completedWhileSlow);
    assert(!recovered.accepted);
    TradingToolHostSessionBinding retained;
    assert(host.GetSession(provision.token, retained) && retained.recoveryOnly);
    // Stop while a second recovery observation is genuinely in flight. The
    // fake authority obeys the same monotonic deadline as the real IPC client.
    entered.store(false); release.store(false);
    const int raw = ::socket(AF_UNIX, SOCK_STREAM | SOCK_CLOEXEC, 0);
    assert(raw >= 0);
    sockaddr_un address{}; address.sun_family = AF_UNIX;
    assert(socketPath.size() < sizeof(address.sun_path));
    std::memcpy(address.sun_path, socketPath.c_str(), socketPath.size() + 1);
    assert(::connect(raw, reinterpret_cast<sockaddr*>(&address), sizeof(address)) == 0);
    std::string requestBody;
    assert(SessionSupervisorProtocol::EncodeRequest(recovery, requestBody, reason));
    assert(TypedToolProtocol::WriteFrame(raw, requestBody, 1000, reason));
    const auto secondDeadline = std::chrono::steady_clock::now() + std::chrono::seconds(3);
    while (!entered.load() && std::chrono::steady_clock::now() < secondDeadline)
        std::this_thread::sleep_for(std::chrono::milliseconds(1));
    assert(entered.load());
    const auto stopStart = std::chrono::steady_clock::now(); server.Stop();
    ::close(raw);
    const auto stopNs = std::chrono::duration_cast<std::chrono::nanoseconds>(
        std::chrono::steady_clock::now() - stopStart).count();
    assert(controlNs < 1500000000LL && stopNs < 2000000000LL);
    std::cout << "{\"schema\":\"heptatrader.supervisor-work-cost.v1\",\"synthetic\":true,"
        << "\"independent_owner_completed_while_slow\":true,\"same_owner_rejected\":true,"
        << "\"control_ns\":" << controlNs << ",\"stop_ns\":" << stopNs << ",\"stop_during_recovery\":true}\n";
    std::remove(storePath.c_str()); std::remove(keyPath.c_str()); std::remove(journalPath.c_str());
}

// Exercise the actual failed-Provision recovery path. A slow local authorizer
// may exhaust the work interval, but durable/local fencing must still complete.
// It must not replenish the interval and start another remote observation.
void TestFailedPaperActivationRetainsOriginalWorkDeadline()
{
    for (const bool exhaustBudget : {false, true})
    {
        const std::string journalPath = TempPath("/tmp/hepta-activation-budget-journal-XXXXXX");
        const std::string socketPath = TempPath("/tmp/hepta-activation-budget-socket-XXXXXX");
        const std::string storePath = TempPath("/tmp/hepta-activation-budget-store-XXXXXX");
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
        RecoveryControlAuthority authority;
        std::atomic<unsigned> audits{0};
        authority.ownerAudit = [&](const ExecutionControlCommand& command) {
            ++audits;
            assert(command.localDeadline != std::chrono::steady_clock::time_point::max());
            ExecutionControlResult result;
            result.commandId = command.context.toolCallId;
            result.status = ExecutionCommandStatus::Uncertain;
            result.reasonCode = "TEST_RECOVERY_OBSERVATION_PENDING";
            return result;
        };
        host.SetRecoveryControlAuthority(&authority);
        const int workIntervalMs = 1000;
        TradingToolSessionControlPlane control(host,
            [&](const std::string&, const TradingToolHostSessionBinding& binding,
                std::string& failure) {
                if (binding.session.environment == "PAPER" && !binding.recoveryOnly)
                {
                    if (exhaustBudget)
                        std::this_thread::sleep_for(std::chrono::milliseconds(workIntervalMs + 50));
                    failure = "TEST_ACTIVATION_DENIED";
                    return false;
                }
                return true;
            });
        UnixSessionSupervisorServer server(control);
        server.SetLeaseStore(&store);
        std::map<std::uint32_t, std::string> issuers;
        issuers[::getuid()] = "activation-budget-issuer";
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
                binding.expiresAtMs = OmsJournal::NowEpochMs() + request.ttlMs;
                return true;
            }, reason, 16384, workIntervalMs));
        SessionSupervisorRequest provision;
        provision.operation = SessionSupervisorOperation::Provision;
        provision.templateId = "paper";
        provision.token = "activation-budget-paper-owner-token-0001";
        provision.agentId = "activation-budget-agent";
        provision.sessionId = "activation-budget-session";
        provision.peerUid = ::getuid();
        provision.ttlMs = 60000;
        const auto result = Call(socketPath, provision);
        server.Stop();
        TradingToolHostSessionBinding local;
        assert(host.GetSession(provision.token, local) && local.recoveryOnly);
        assert(store.Init(storePath, keyPath, reason));
        SessionSupervisorLeaseRecord durable;
        assert(store.Get(provision.token, durable) && durable.recoveryOnly);
        assert(durable.leaseGeneration == local.leaseGeneration && !durable.fencePending);
        std::cout << "SUPERVISOR_ACTIVATION_BUDGET exhausted=" << exhaustBudget
                  << " remote_audits=" << audits.load()
                  << " recovery_fence_reopened=true reason=" << result.ReasonCode() << std::endl;
        std::remove(storePath.c_str());
        std::remove(keyPath.c_str());
        std::remove(journalPath.c_str());
        assert(!result.accepted);
        assert(audits.load() == (exhaustBudget ? 0U : 1U));
        assert(result.ReasonCode() == (exhaustBudget ?
            "SUPERVISOR_WORK_BUDGET_EXHAUSTED" : "TEST_ACTIVATION_DENIED"));
    }
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

} // namespace hepta_supervisor_test
