#pragma once

#include "session_supervisor_protocol.h"
#include "session_supervisor_lease_store.h"
#include "session_supervisor_audit_journal.h"
#include "trading_tool_session_control_plane.h"

#include <atomic>
#include <cstdint>
#include <functional>
#include <map>
#include <mutex>
#include <string>
#include <thread>

class UnixSessionSupervisorServer
{
public:
	typedef std::function<bool(const SessionSupervisorRequest&,
		TradingToolHostSessionBinding&, std::string&)> BindingResolver;
	typedef std::function<bool(const std::string&)> CrashPointHook;

	explicit UnixSessionSupervisorServer(TradingToolSessionControlPlane& controlPlane);
	~UnixSessionSupervisorServer();

	bool Start(const std::string& socketPath,
		const std::map<std::uint32_t, std::string>& authorizedIssuers,
		const BindingResolver& bindingResolver, std::string& reason,
		std::size_t maxRequestBytes = 16384, int ioTimeoutMs = 3000,
		std::uint64_t maxSessionTtlMs = 86400000);
	bool StartFromFd(int listenFd,
		const std::map<std::uint32_t, std::string>& authorizedIssuers,
		const BindingResolver& bindingResolver, std::string& reason,
		std::size_t maxRequestBytes = 16384, int ioTimeoutMs = 3000,
		std::uint64_t maxSessionTtlMs = 86400000);
	void Stop();
	bool IsRunning() const;
	void SetLeaseStore(SessionSupervisorLeaseStore* leaseStore);
	void SetAuditJournal(SessionSupervisorAuditJournal* auditJournal);
	void SetCrashPointHook(const CrashPointHook& hook);
	// Production is fixed to uid 0.  This seam exists only so unprivileged
	// socket tests can exercise the root-only protocol branch.
	void SetRootCustodianUidForTests(std::uint32_t uid)
	{
		if (!m_stop.load()) return;
		m_rootCustodianUid = uid;
	}
	bool ReapExpired(std::uint64_t nowMs, std::size_t& reaped, std::string& reason);

private:
	void AcceptLoop();
	void HandleClient(int clientFd);
	bool Activate(int listenFd, const std::string& socketPath, bool unlinkOnStop,
		const std::map<std::uint32_t, std::string>& authorizedIssuers,
		const BindingResolver& bindingResolver, std::string& reason,
		std::size_t maxRequestBytes, int ioTimeoutMs, std::uint64_t maxSessionTtlMs,
		std::uint64_t socketPathDevice = 0,
		std::uint64_t socketPathInode = 0,
		bool socketPathIdentityValid = false);
	bool RestoreLeases(std::string& reason);
	bool EnterPaperRecovery(
		SessionSupervisorLeaseRecord& record,
		std::uint64_t nowMs,
		const std::string& targetCommandId,
		ExecutionControlResult& commandResult,
		ExecutionControlResult& ownerAudit,
		std::string& reason);
	bool FinalizePaperRecovery(
		const SessionSupervisorLeaseRecord& record,
		ExecutionControlResult& ownerAudit,
		std::string& reason);
	bool HandlePaperFinalize(
		const SessionSupervisorRequest& request,
		SessionSupervisorResult& result);
	bool HandlePaperFinalizeAck(
		const SessionSupervisorRequest& request,
		SessionSupervisorResult& result);
	bool HandlePaperTerminalizeAck(
		const SessionSupervisorRequest& request,
		SessionSupervisorResult& result);
	bool HandlePaperTerminalWitnessPrepare(
		const SessionSupervisorRequest& request,
		SessionSupervisorResult& result);
	bool HandlePaperTerminalWitnessAck(
		const SessionSupervisorRequest& request,
		SessionSupervisorResult& result);
	bool IsIssuerAllowed(const std::string& issuer) const;
	bool ResolveLeaseBinding(const SessionSupervisorLeaseRecord& record,
		std::uint64_t nowMs, TradingToolHostSessionBinding& binding,
		std::string& reason) const;
	bool MarkFencePending(const SessionSupervisorLeaseRecord& record,
		const std::string& fenceReason, SessionSupervisorLeaseRecord& pending,
		std::string& reason);
		bool FenceStoredRecord(const SessionSupervisorLeaseRecord& record,
			bool localSessionMayExist, std::string& reason);
		bool BeginWatchTransaction(
			const std::vector<SessionSupervisorLeaseRecord>& records,
			std::string& watchTransactionId, std::string& reason);
		bool RevokeExactWatchRecords(
			const std::vector<SessionSupervisorLeaseRecord>& records,
			const std::string& watchTransactionId,
			bool& allLocalAbsent, std::string& reason);
		bool FenceWatchRecord(const SessionSupervisorLeaseRecord& record,
			std::string& watchTransactionId, std::string& reason);
		bool RemoveLeaseAndReleaseWatchTransaction(
			const SessionSupervisorLeaseRecord& record,
			const std::vector<SessionSupervisorLeaseRecord>& transactionRecords,
			const std::string& watchTransactionId, std::string& reason);
		bool ReleaseWatchTransaction(
			const std::vector<SessionSupervisorLeaseRecord>& records,
			const std::string& watchTransactionId, std::string& reason);
		bool FenceCommittedMutation(const SessionSupervisorLeaseRecord& localRecord,
			const SessionSupervisorLeaseRecord& pendingRecord,
			const std::vector<SessionSupervisorLeaseRecord>&
				watchTransactionRecords,
			const std::string& watchTransactionId, std::string& reason);
	bool HasPendingOwner(const std::string& agentId,
		const std::string& sessionId) const;

	TradingToolSessionControlPlane& m_controlPlane;
	std::atomic<bool> m_stop;
	std::atomic<int> m_listenFd;
	std::string m_socketPath;
	bool m_unlinkOnStop;
	std::uint64_t m_socketPathDevice;
	std::uint64_t m_socketPathInode;
	bool m_socketPathIdentityValid;
	std::size_t m_maxRequestBytes;
	int m_ioTimeoutMs;
	std::uint64_t m_maxSessionTtlMs;
	std::map<std::uint32_t, std::string> m_authorizedIssuers;
	BindingResolver m_bindingResolver;
	SessionSupervisorLeaseStore* m_leaseStore;
	SessionSupervisorAuditJournal* m_auditJournal;
	CrashPointHook m_crashPointHook;
	std::uint32_t m_rootCustodianUid;
	std::mutex m_operationMutex;
	std::thread m_acceptThread;
};
