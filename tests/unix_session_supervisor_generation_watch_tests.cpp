#include "unix_session_supervisor_test_support.h"

namespace hepta_supervisor_test {

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

void TestUserPaperResumeAuditsBeforeActivation()
{
    if (::geteuid() == 0 || ::getegid() == 0) return;
    const std::string journalPath = TempPath("/tmp/hepta-resume-journal-XXXXXX");
    const std::string socketPath = TempPath("/tmp/hepta-resume-socket-XXXXXX");
    const std::string storePath = TempPath("/tmp/hepta-resume-store-XXXXXX");
    const std::string auditPath = TempPath("/tmp/hepta-resume-audit-XXXXXX");
    const std::string keyPath = TempKeyPath();
    std::string reason;
    SessionSupervisorLeaseStore store; assert(store.Init(storePath, keyPath, reason));
    SessionSupervisorAuditJournal audit; assert(audit.Init(auditPath, reason));
    OmsJournal journal; assert(journal.Init(journalPath));
    ExecutionCoordinatorCallbacks callbacks; ExecutionCoordinator execution(journal, callbacks);
    TradingToolRegistry registry(execution); TradingToolHost host(registry);
    RecoveryControlAuthority authority;
    bool complete = false; int auditCalls = 0, releaseCalls = 0;
    authority.ownerAudit = [&](const ExecutionControlCommand& command) {
        ++auditCalls;
        ExecutionControlResult result;
        result.commandId = command.context.toolCallId;
        result.status = ExecutionCommandStatus::Accepted;
        result.ownerAuditAuthoritative = true; result.ownerAuditComplete = complete;
        result.ownerAccount = command.context.account;
        result.ownerExecutionDomain = command.context.executionDomain;
        result.brokerConnectionEpoch = 1; result.brokerActiveGeneration = 1;
        result.brokerTerminalGeneration = 1;
        return result;
    };
    authority.release = [&](const ExecutionControlCommand& command) {
        ++releaseCalls; assert(auditCalls > 0 && complete);
        ExecutionControlStatusResult result; result.commandId = command.context.toolCallId;
        std::string why;
        assert(execution.AuditAndReleaseSessionOwnerFence("resume-agent", "resume-session", true, why));
        result.status = ExecutionCommandStatus::Accepted;
        result.reasonCode = "SESSION_OWNER_FENCE_RELEASED";
        return result;
    };
    host.SetRecoveryControlAuthority(&authority);
    TradingToolSessionControlPlane control(host, [](const std::string&, const TradingToolHostSessionBinding&, std::string&) {return true;});
    UnixSessionSupervisorServer server(control); server.SetLeaseStore(&store); server.SetAuditJournal(&audit);
    assert(server.ConfigureUserPaperCustodian(reason));
    std::map<std::uint32_t,std::string> issuers; issuers[::geteuid()]="test.owner";
    auto resolver=[](const SessionSupervisorRequest& request,TradingToolHostSessionBinding& binding,std::string&) {
        binding.token=request.token; binding.peerUid=request.peerUid;
        binding.session.executionContext.agentId=request.agentId;
        binding.session.executionContext.sessionId=request.sessionId;
        binding.session.executionContext.account="DU123"; binding.session.environment="PAPER";
        binding.executionDomain="IB-PAPER";
        binding.expiresAtMs=OmsJournal::NowEpochMs()+request.ttlMs; return true;
    };
    assert(server.Start(socketPath,issuers,resolver,reason,4096,2000));
    SessionSupervisorRequest request; request.operation=SessionSupervisorOperation::Provision;
    request.token=std::string(64,'z'); request.templateId="paper"; request.agentId="resume-agent";
    request.sessionId="resume-session"; request.peerUid=::geteuid(); request.ttlMs=60000;
    assert(Call(socketPath,request).accepted);
    execution.FenceSessionOwner("resume-agent","resume-session");
    request.operation=SessionSupervisorOperation::PaperResume; request.expectedGeneration=1;
    const auto refused=Call(socketPath,request);
    assert(!refused.accepted && releaseCalls==0);
    SessionSupervisorLeaseRecord stored; assert(store.Get(request.token,stored));
    assert(stored.leaseGeneration==1 && !stored.fencePending);
    assert(execution.IsSessionOwnerFenced("resume-agent","resume-session"));
    complete=true;
    const std::string tokenPath=WriteSessionCtlTokenFile(request.token);
    const auto accepted=RunSessionCtl({"--socket",socketPath,"paper-resume","--token-file",tokenPath,
        "--generation","1","--ttl-sec","60","--token-owner-uid",std::to_string(::geteuid())});
    assert(accepted.exitCode==0);
    assert(accepted.standardOutput.find("SESSION_OWNER_RESUMED")!=std::string::npos);
    assert(releaseCalls==1);
    assert(store.Get(request.token,stored) && stored.leaseGeneration==2 && !stored.fencePending);
    assert(!execution.IsSessionOwnerFenced("resume-agent","resume-session"));
    const auto stale=Call(socketPath,request);
    assert(!stale.accepted && stale.leaseGeneration==2 && releaseCalls==1);
    bool found=false; for (const auto& payload:AuditPayloads(auditPath))
        if (payload.find("operation=paper-resume")!=std::string::npos) found=true;
    assert(found);
    authority.leaseAudit=[&](const ExecutionControlCommand& command) {
        auto result=authority.ownerAudit(command);
        result.reasonCode="SESSION_LEASE_CONTINUATION_CONFIRMED";
        result.brokerPositionQuantity="-25000";result.brokerGrossAbsolutePosition="25000";
        result.brokerPositionGeneration=2;
        return result;
    };
    const auto continued=RunSessionCtl({"--socket",socketPath,"renew-active","--token-file",tokenPath,
        "--generation","2","--ttl-sec","60","--token-owner-uid",std::to_string(::geteuid())});
    assert(continued.exitCode==0);
    assert(continued.standardOutput.find("SESSION_LEASE_CONTINUED")!=std::string::npos);
    assert(releaseCalls==1 && store.Get(request.token,stored) && stored.leaseGeneration==3);
    complete=false;
    request.operation=SessionSupervisorOperation::RenewActive;request.expectedGeneration=3;
    assert(!Call(socketPath,request).accepted);
    assert(releaseCalls==1 && store.Get(request.token,stored) && stored.leaseGeneration==3);
    assert(!execution.IsSessionOwnerFenced("resume-agent","resume-session"));
    server.Stop();
    std::remove(tokenPath.c_str()); std::remove(storePath.c_str()); std::remove(keyPath.c_str());
    std::remove(journalPath.c_str()); std::remove(auditPath.c_str());
}

void TestUserRecoveryRestoreSurvivesHeldFxAndExplicitCryptoResume()
{
    if (::geteuid()==0 || ::getegid()==0)return;
    const auto journalPath=TempPath("/tmp/hepta-scoped-resume-journal-XXXXXX");
    const auto socketPath=TempPath("/tmp/hepta-scoped-resume-socket-XXXXXX");
    const auto storePath=TempPath("/tmp/hepta-scoped-resume-store-XXXXXX");
    const auto auditPath=TempPath("/tmp/hepta-scoped-resume-audit-XXXXXX");
    const auto keyPath=TempKeyPath();std::string reason;
    SessionSupervisorLeaseStore store;assert(store.Init(storePath,keyPath,reason));
    SessionSupervisorAuditJournal audit;assert(audit.Init(auditPath,reason));
    OmsJournal journal;assert(journal.Init(journalPath));
    ExecutionCoordinatorCallbacks callbacks;ExecutionCoordinator execution(journal,callbacks);
    TradingToolRegistry registry(execution);TradingToolHost host(registry);
    RecoveryControlAuthority authority;bool valid=false;int releaseCalls=0;
    authority.ownerAudit=[&](const ExecutionControlCommand& command){
        ExecutionControlResult result;result.commandId=command.context.toolCallId;
        result.ownerAccount=command.context.account;result.ownerExecutionDomain=command.context.executionDomain;
        result.brokerConnectionEpoch=1;result.brokerActiveGeneration=2;result.brokerTerminalGeneration=3;
        result.brokerRiskGeneration=4;result.brokerAccountGeneration=5;result.brokerPositionGeneration=6;
        result.brokerFxCashGeneration=7;result.brokerRecoveryAuditBarrierComplete=true;
        result.brokerPositionQuantity="-25000";result.brokerGrossAbsolutePosition="25000";
        result.status=ExecutionCommandStatus::Rejected;result.reasonCode="RECOVERY_OWNER_POSITION_NOT_FLAT";
        if(command.recoveryIngressFence) {
            std::string why;assert(execution.EnterRecoveryOnlyOwner(command.context.agentId,
                command.context.sessionId,command.recoveryIngressFence,why));
        } else if(command.context.sessionId=="crypto-owner") {
            result.status=ExecutionCommandStatus::Accepted;result.ownerAuditAuthoritative=true;
            result.ownerAuditComplete=valid;result.reasonCode="SESSION_CRYPTO_OWNER_ZERO_CONFIRMED";
        }
        return result;
    };
    authority.release=[&](const ExecutionControlCommand& command){
        ++releaseCalls;assert(valid && command.context.sessionId=="crypto-owner");
        std::string why;auto fence=execution.SessionOwnerRecoveryIngressFence("agent","crypto-owner");
        assert(execution.AuditAndReleaseSessionOwnerFence("agent","crypto-owner",true,why,fence));
        ExecutionControlStatusResult out;out.commandId=command.context.toolCallId;
        out.status=ExecutionCommandStatus::Accepted;out.reasonCode="SESSION_OWNER_FENCE_RELEASED";return out;
    };
    host.SetRecoveryControlAuthority(&authority);
    TradingToolSessionControlPlane control(host,[](const std::string&,const TradingToolHostSessionBinding&,std::string&){return true;});
    for(const auto* name : {"fx-owner","crypto-owner"}) {
        SessionSupervisorLeaseRecord rec;rec.templateId="paper";rec.issuer="test.owner";
        rec.token=std::string(64,name[0]);rec.agentId="agent";rec.sessionId=name;rec.peerUid=::geteuid();
        rec.ownerAccount="DU123";rec.ownerExecutionDomain="PAPER";rec.expiresAtMs=1;rec.leaseGeneration=1;
        assert(store.Put(rec,reason));
    }
    auto resolver=[](const SessionSupervisorRequest& req,TradingToolHostSessionBinding& binding,std::string&){
        binding.token=req.token;binding.peerUid=req.peerUid;binding.expiresAtMs=OmsJournal::NowEpochMs()+req.ttlMs;
        binding.session.executionContext.agentId=req.agentId;binding.session.executionContext.sessionId=req.sessionId;
        binding.session.executionContext.account="DU123";binding.session.environment="PAPER";binding.executionDomain="PAPER";
        const bool crypto=req.sessionId=="crypto-owner";const std::string instrument=crypto?"BTC.USD":"EUR.USD";
        IBContractLite c;c.symbol=crypto?"BTC":"EUR";c.secType=crypto?"CRYPTO":"CASH";
        c.exchange=crypto?"PAXOS":"IDEALPRO";c.currency="USD";
        binding.allowedInstruments.insert(instrument);binding.instrumentContracts[instrument]=c;
        binding.maxOrderQuantity=crypto?0.001:25000;return true;
    };
    UnixSessionSupervisorServer server(control);server.SetLeaseStore(&store);server.SetAuditJournal(&audit);
    assert(server.ConfigureUserPaperCustodian(reason));std::map<std::uint32_t,std::string> issuers;
    issuers[::geteuid()]="test.owner";assert(server.Start(socketPath,issuers,resolver,reason,4096,2000));
    TradingToolHostSessionBinding local;SessionSupervisorLeaseRecord rec;
    const auto cryptoToken=std::string(64,'c'),fxToken=std::string(64,'f');
    assert(host.GetSession(cryptoToken,local) && local.recoveryOnly);
    assert(host.GetSession(fxToken,local) && local.recoveryOnly);
    assert(store.Get(cryptoToken,rec) && rec.recoveryOnly && rec.leaseGeneration==1);
    const auto restoredExpiry=rec.expiresAtMs;std::size_t reaped=0;
    assert(server.ReapExpired(OmsJournal::NowEpochMs(),reaped,reason) && reaped==0);
    assert(store.Get(cryptoToken,rec) && rec.expiresAtMs==restoredExpiry && rec.recoveryOnly);
    SessionSupervisorRequest req;req.operation=SessionSupervisorOperation::RenewActive;req.token=cryptoToken;
    req.expectedGeneration=1;req.ttlMs=60000;assert(!Call(socketPath,req).accepted);
    req.operation=SessionSupervisorOperation::PaperResume;
    auto refused=Call(socketPath,req);assert(!refused.accepted && releaseCalls==0);
    assert(store.Get(cryptoToken,rec) && rec.recoveryOnly && rec.leaseGeneration==1);
    valid=true;const auto resumed=Call(socketPath,req);assert(resumed.accepted && releaseCalls==1);
    assert(store.Get(cryptoToken,rec) && !rec.recoveryOnly && !rec.fencePending && rec.leaseGeneration==2);
    assert(host.GetSession(cryptoToken,local) && !local.recoveryOnly && local.leaseGeneration==2);
    assert(host.GetSession(fxToken,local) && local.recoveryOnly && local.leaseGeneration==1);
    assert(execution.IsSessionOwnerRecoveryOnly("agent","fx-owner"));
    assert(!execution.IsSessionOwnerRecoveryOnly("agent","crypto-owner"));
    assert(!Call(socketPath,req).accepted && releaseCalls==1); // old generation cannot renew twice
    server.Stop();
    TradingToolHost restartedHost(registry);restartedHost.SetRecoveryControlAuthority(&authority);
    TradingToolSessionControlPlane restartedControl(restartedHost,[](const std::string&,const TradingToolHostSessionBinding&,std::string&){return true;});
    UnixSessionSupervisorServer restarted(restartedControl);restarted.SetLeaseStore(&store);restarted.SetAuditJournal(&audit);
    assert(restarted.ConfigureUserPaperCustodian(reason));assert(restarted.Start(socketPath,issuers,resolver,reason,4096,2000));
    assert(restartedHost.GetSession(cryptoToken,local) && !local.recoveryOnly && local.leaseGeneration==2);
    assert(restartedHost.GetSession(fxToken,local) && local.recoveryOnly);
    restarted.Stop();
    std::remove(journalPath.c_str());std::remove(storePath.c_str());std::remove(keyPath.c_str());std::remove(auditPath.c_str());
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


} // namespace hepta_supervisor_test
