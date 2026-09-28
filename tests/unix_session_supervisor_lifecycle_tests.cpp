#include "unix_session_supervisor_test_support.h"

namespace hepta_supervisor_test {

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
	assert(reapFinished.load());
	releaseCommit.store(true);
	renewThread.join();
	reapThread.join();
	assert(renewed.accepted && renewed.leaseGeneration == 2);
    assert(!reapAccepted && reaped == 0 && reapReason == "SUPERVISOR_MAINTENANCE_BUSY");
    assert(server.ReapExpired(oldExpiry + 1, reaped, reapReason) && reaped == 0);
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


} // namespace hepta_supervisor_test
