#include "unix_session_supervisor_server.h"

#include "typed_tool_protocol.h"

#include <algorithm>
#include <cerrno>
#include <chrono>
#include <fcntl.h>
#include <cstring>
#include <limits>
#include <openssl/evp.h>
#include <poll.h>
#include <sstream>
#include <sys/socket.h>
#include <sys/stat.h>
#include <sys/un.h>
#include <thread>
#include <unistd.h>

#include "session_supervisor_internal.h"
using namespace HeptaSessionSupervisorInternal;


UnixSessionSupervisorServer::UnixSessionSupervisorServer(
	TradingToolSessionControlPlane& controlPlane)
	: m_controlPlane(controlPlane), m_stop(true), m_listenFd(-1),
	  m_unlinkOnStop(false), m_socketPathDevice(0), m_socketPathInode(0),
	  m_socketPathIdentityValid(false), m_maxRequestBytes(16384), m_ioTimeoutMs(3000),
	  m_maxSessionTtlMs(86400000), m_leaseStore(nullptr), m_auditJournal(nullptr),
	  m_rootCustodianUid(0)
{
}

UnixSessionSupervisorServer::~UnixSessionSupervisorServer()
{
	Stop();
}

bool UnixSessionSupervisorServer::Start(const std::string& socketPath,
	const std::map<std::uint32_t, std::string>& authorizedIssuers,
	const BindingResolver& bindingResolver, std::string& reason,
	std::size_t maxRequestBytes, int ioTimeoutMs, std::uint64_t maxSessionTtlMs)
{
	if (!m_stop.load()) { reason = "supervisor already running"; return false; }
	if (socketPath.empty() || socketPath.size() >= sizeof(sockaddr_un::sun_path) ||
		authorizedIssuers.empty() || !bindingResolver || maxRequestBytes < 128 ||
		maxRequestBytes > 1024 * 1024 || ioTimeoutMs <= 0 || ioTimeoutMs > 30000)
	{
		reason = "invalid supervisor configuration";
		return false;
	}
	struct stat existing;
	if (::lstat(socketPath.c_str(), &existing) == 0)
	{
		if (!S_ISSOCK(existing.st_mode)) { reason = "socket path exists and is not a socket"; return false; }
		// A failed connect cannot prove that a pre-existing listener is stale.
		// Never unlink another supervisor's rendezvous pathname; production
		// restarts use FD activation or an owner-controlled custodian cleanup.
		reason = "socket path already exists; use activated fd or owner cleanup";
		return false;
	}
	const int fd = ::socket(AF_UNIX, SOCK_STREAM | SOCK_CLOEXEC, 0);
	if (fd < 0) { reason = std::strerror(errno); return false; }
	sockaddr_un address;
	std::memset(&address, 0, sizeof(address));
	address.sun_family = AF_UNIX;
	std::strncpy(address.sun_path, socketPath.c_str(), sizeof(address.sun_path) - 1);
	if (::bind(fd, reinterpret_cast<sockaddr*>(&address), sizeof(address)) != 0)
	{ reason = std::strerror(errno); ::close(fd); return false; }
	struct stat boundFd;
	if (::fstat(fd, &boundFd) != 0 || !S_ISSOCK(boundFd.st_mode))
	{ reason = std::strerror(errno); ::close(fd); return false; }
	std::uint64_t pathDevice = 0;
	std::uint64_t pathInode = 0;
	if (!CaptureSocketPathIdentity(socketPath, pathDevice, pathInode))
	{ reason = std::strerror(errno); ::close(fd); return false; }
	if (::fchmod(fd, 0600) != 0 || ::chmod(socketPath.c_str(), 0600) != 0 ||
		!SocketPathIdentityMatches(socketPath, pathDevice, pathInode) ||
		::listen(fd, 8) != 0)
	{ reason = std::strerror(errno); ::close(fd); UnlinkSocketIfUnchanged(socketPath, pathDevice, pathInode); return false; }
	if (!Activate(fd, socketPath, true, authorizedIssuers, bindingResolver, reason,
		maxRequestBytes, ioTimeoutMs, maxSessionTtlMs, pathDevice, pathInode, true))
	{
		UnlinkSocketIfUnchanged(socketPath, pathDevice, pathInode);
		return false;
	}
	return true;
}

bool UnixSessionSupervisorServer::StartFromFd(int listenFd,
	const std::map<std::uint32_t, std::string>& authorizedIssuers,
	const BindingResolver& bindingResolver, std::string& reason,
	std::size_t maxRequestBytes, int ioTimeoutMs, std::uint64_t maxSessionTtlMs)
{
	if (!m_stop.load()) { reason = "supervisor already running"; return false; }
	int socketType = 0;
	socklen_t socketTypeLength = sizeof(socketType);
	sockaddr_un address;
	socklen_t addressLength = sizeof(address);
	std::memset(&address, 0, sizeof(address));
	if (listenFd < 0 || ::getsockopt(listenFd, SOL_SOCKET, SO_TYPE,
		&socketType, &socketTypeLength) != 0 || socketType != SOCK_STREAM ||
		::getsockname(listenFd, reinterpret_cast<sockaddr*>(&address), &addressLength) != 0 ||
		address.sun_family != AF_UNIX)
	{
		reason = "invalid activated supervisor socket";
		return false;
	}
	const int duplicated = ::fcntl(listenFd, F_DUPFD_CLOEXEC, 3);
	if (duplicated < 0) { reason = std::strerror(errno); return false; }
	if (!Activate(duplicated, std::string(), false, authorizedIssuers, bindingResolver, reason,
		maxRequestBytes, ioTimeoutMs, maxSessionTtlMs))
	{
		return false;
	}
	return true;
}

bool UnixSessionSupervisorServer::Activate(int listenFd, const std::string& socketPath,
	bool unlinkOnStop, const std::map<std::uint32_t, std::string>& authorizedIssuers,
	const BindingResolver& bindingResolver, std::string& reason,
	std::size_t maxRequestBytes, int ioTimeoutMs, std::uint64_t maxSessionTtlMs,
	std::uint64_t socketPathDevice, std::uint64_t socketPathInode,
	bool socketPathIdentityValid)
{
	m_listenFd = listenFd;
	if (authorizedIssuers.empty() || !bindingResolver || maxRequestBytes < 128 ||
		maxRequestBytes > 1024 * 1024 || ioTimeoutMs <= 0 || ioTimeoutMs > 30000 ||
		maxSessionTtlMs == 0)
	{
		::close(listenFd);
		m_listenFd = -1;
		reason = "invalid supervisor configuration";
		return false;
	}
	m_socketPath = socketPath;
	m_unlinkOnStop = unlinkOnStop;
	m_socketPathDevice = socketPathDevice;
	m_socketPathInode = socketPathInode;
	m_socketPathIdentityValid = unlinkOnStop && !socketPath.empty() &&
		socketPathIdentityValid;
	m_authorizedIssuers = authorizedIssuers;
	m_bindingResolver = bindingResolver;
	m_maxRequestBytes = maxRequestBytes;
	m_ioTimeoutMs = ioTimeoutMs;
	m_maxSessionTtlMs = maxSessionTtlMs;
	if (!RestoreLeases(reason))
	{
		::close(listenFd);
		m_listenFd = -1;
		m_socketPath.clear();
		m_unlinkOnStop = false;
		m_socketPathIdentityValid = false;
		return false;
	}
	m_stop.store(false);
	try { m_acceptThread = std::thread(&UnixSessionSupervisorServer::AcceptLoop, this); }
	catch (...)
	{
		m_stop.store(true);
		const int failedFd = m_listenFd.exchange(-1);
		if (failedFd >= 0) ::close(failedFd);
		m_socketPath.clear();
		m_unlinkOnStop = false;
		m_socketPathIdentityValid = false;
		reason = "supervisor thread start failed";
		return false;
	}
	reason.clear();
	return true;
}

void UnixSessionSupervisorServer::Stop()
{
	if (m_stop.exchange(true)) return;
	if (m_acceptThread.joinable()) m_acceptThread.join();
	const int listenFd = m_listenFd.exchange(-1);
	struct stat listenerIdentity;
	const bool listenerIdentityValid = listenFd >= 0 &&
		::fstat(listenFd, &listenerIdentity) == 0 &&
		S_ISSOCK(listenerIdentity.st_mode);
	if (listenFd >= 0)
	{
		// A systemd socket-activation descriptor shares its open socket
		// description with PID 1. shutdown(2) here would therefore poison the
		// manager-owned listener and make the next service process inherit an
		// ECONNREFUSED socket. The accept loop polls with a bounded timeout, so
		// stop it first and close only this process' descriptor after it exits.
		::close(listenFd);
	}
	if (m_unlinkOnStop && !m_socketPath.empty() &&
		m_socketPathIdentityValid && listenerIdentityValid)
		UnlinkSocketIfUnchanged(m_socketPath, m_socketPathDevice,
			m_socketPathInode);
	m_socketPath.clear();
	m_unlinkOnStop = false;
	m_socketPathIdentityValid = false;
}

bool UnixSessionSupervisorServer::IsRunning() const
{
	return !m_stop.load();
}

void UnixSessionSupervisorServer::SetLeaseStore(SessionSupervisorLeaseStore* leaseStore)
{
	m_leaseStore = leaseStore;
}

void UnixSessionSupervisorServer::SetAuditJournal(SessionSupervisorAuditJournal* auditJournal)
{
	m_auditJournal = auditJournal;
}

void UnixSessionSupervisorServer::SetCrashPointHook(const CrashPointHook& hook)
{
	m_crashPointHook = hook;
}

bool UnixSessionSupervisorServer::ReapExpired(std::uint64_t nowMs,
	std::size_t& reaped, std::string& reason)
{
	std::lock_guard<std::mutex> operationLock(m_operationMutex);
	reaped = 0;
	if (m_leaseStore != nullptr)
	{
		const std::vector<SessionSupervisorLeaseRecord> records = m_leaseStore->List();
		std::string firstFailure;
		for (std::size_t i = 0; i < records.size(); ++i)
		{
			if (records[i].paperFinalizationState !=
				SessionSupervisorPaperFinalizationState::None)
				continue;
			if (records[i].templateId == "paper" &&
				(records[i].recoveryOnly || records[i].fencePending ||
				 records[i].expiresAtMs <= nowMs))
			{
				SessionSupervisorLeaseRecord recovery = records[i];
				ExecutionControlStatusResult commandResult;
				ExecutionOwnerAuditResult ownerAudit;
				std::string recoveryReason;
				if (!EnterPaperRecovery(recovery, nowMs, std::string(),
						commandResult, ownerAudit, recoveryReason))
				{
					if (firstFailure.empty()) firstFailure = recoveryReason;
					continue;
				}
				if (ownerAudit.status == ExecutionCommandStatus::Accepted &&
					ownerAudit.ownerAuditAuthoritative &&
					ownerAudit.ownerAuditComplete &&
					ownerAudit.ownerActiveOrderCount == 0 &&
					ownerAudit.ownerUncertainCommandCount == 0)
				{
					if (recovery.paperFinalizationRequired)
					{
						// External exact zero is not deletion authority. Keep the
						// recovery-only binding until HSL7 finalize + ACK.
						continue;
					}
					ExecutionControlResult finalAudit;
					if (FinalizePaperRecovery(
							recovery, finalAudit, recoveryReason))
						++reaped;
					else if (recoveryReason !=
							"SESSION_OWNER_RECOVERY_REQUIRED" &&
						firstFailure.empty())
						firstFailure = recoveryReason;
				}
				else if (ownerAudit.reasonCode !=
						"RECOVERY_OWNER_UNCERTAIN_COMMANDS_REMAIN" &&
					ownerAudit.status != ExecutionCommandStatus::Accepted &&
					firstFailure.empty())
					firstFailure = ownerAudit.reasonCode.empty() ?
						"SESSION_OWNER_AUDIT_INCOMPLETE" :
						ownerAudit.reasonCode;
				continue;
			}
			if (records[i].fenceComplete)
			{
				const std::vector<SessionSupervisorLeaseRecord>
					transactionRecords =
						WatchTransactionRecords(records[i]);
				std::string watchTransactionId;
				if (!BeginWatchTransaction(
					transactionRecords, watchTransactionId, reason))
					return false;
				bool allLocalAbsent = false;
				if (!RevokeExactWatchRecords(
						transactionRecords, watchTransactionId,
						allLocalAbsent, reason) &&
					!allLocalAbsent)
					return false;
				if (records[i].expiresAtMs > nowMs)
					continue;
				if (!RemoveLeaseAndReleaseWatchTransaction(
					records[i], transactionRecords,
					watchTransactionId, reason)) return false;
				++reaped;
				continue;
			}
			if (!records[i].fencePending && records[i].expiresAtMs > nowMs) continue;
			SessionSupervisorLeaseRecord pending = records[i];
			std::string watchTransactionId;
			if (pending.templateId == "watch" &&
				!BeginWatchTransaction(
					WatchTransactionRecords(pending),
					watchTransactionId, reason))
				return false;
			if (!pending.fencePending &&
				!MarkFencePending(records[i], "session_expired", pending, reason))
				return false;
			std::string fenceReason;
			const bool fenced = pending.templateId == "watch" ?
				FenceWatchRecord(
					pending, watchTransactionId, fenceReason) :
				FenceStoredRecord(pending, true, fenceReason);
			if (!fenced)
			{
				if (firstFailure.empty()) firstFailure = fenceReason;
				continue;
			}
			if (pending.templateId == "watch")
			{
				const std::vector<SessionSupervisorLeaseRecord>
					transactionRecords =
						WatchTransactionRecords(pending);
				if (!RemoveLeaseAndReleaseWatchTransaction(
					pending, transactionRecords,
					watchTransactionId, reason)) return false;
			}
			else if (!m_leaseStore->Remove(pending.token, reason))
				return false;
			++reaped;
		}
		if (!firstFailure.empty())
		{
			reason = firstFailure;
			return false;
		}
		reason.clear();
		return true;
	}
	reaped = m_controlPlane.ReapExpired(nowMs);
	reason.clear();
	return true;
}

bool UnixSessionSupervisorServer::IsIssuerAllowed(
	const std::string& issuerName) const
{
	for (std::map<std::uint32_t, std::string>::const_iterator issuer =
			 m_authorizedIssuers.begin(); issuer != m_authorizedIssuers.end(); ++issuer)
		if (issuer->second == issuerName) return true;
	return false;
}

bool UnixSessionSupervisorServer::ResolveLeaseBinding(
	const SessionSupervisorLeaseRecord& record,
	std::uint64_t nowMs,
	TradingToolHostSessionBinding& binding,
	std::string& reason) const
{
	SessionSupervisorRequest request;
	request.operation = SessionSupervisorOperation::Provision;
	request.templateId = record.templateId;
	request.token = record.token;
	request.agentId = record.agentId;
	request.sessionId = record.sessionId;
	request.peerUid = record.peerUid;
	const std::uint64_t remaining = record.expiresAtMs > nowMs ?
		record.expiresAtMs - nowMs : 0;
	// The resolver is also the reviewed template/identity authority. During
	// recovery, satisfy its new-provision TTL floor only to reconstruct that
	// identity; the durable expiry is restored immediately below and a pending
	// record is never registered as an enabled session.
	request.ttlMs = remaining < 60000 ? 60000 : remaining;
	if (request.ttlMs > m_maxSessionTtlMs)
		request.ttlMs = m_maxSessionTtlMs;
	if (!m_bindingResolver(request, binding, reason)) return false;
	binding.expiresAtMs = record.expiresAtMs;
	binding.leaseGeneration = record.leaseGeneration;
	binding.recoveryOnly = record.recoveryOnly;
	if (binding.token != record.token || binding.peerUid != record.peerUid ||
		binding.session.executionContext.agentId != record.agentId ||
		binding.session.executionContext.sessionId != record.sessionId ||
		(record.templateId == "paper" &&
		 (binding.session.executionContext.account != record.ownerAccount ||
		  binding.executionDomain != record.ownerExecutionDomain)))
	{
		reason = "LEASE_STORE_BINDING_IDENTITY_MISMATCH";
		return false;
	}
	reason.clear();
	return true;
}

bool UnixSessionSupervisorServer::MarkFencePending(
	const SessionSupervisorLeaseRecord& record,
	const std::string& fenceReason,
	SessionSupervisorLeaseRecord& pending,
	std::string& reason)
{
	pending = record;
	if (pending.fencePending)
	{
		if (pending.fenceReason != fenceReason)
		{
			reason = "SESSION_FENCE_REASON_MISMATCH";
			return false;
		}
		return true;
	}
	pending.fencePending = true;
	pending.fenceReason = fenceReason;
	if (m_leaseStore == nullptr) return true;
	return m_leaseStore->Replace(record.token, pending, reason);
}

bool UnixSessionSupervisorServer::FenceStoredRecord(
	const SessionSupervisorLeaseRecord& record,
	bool localSessionMayExist,
	std::string& reason)
{
	if (record.templateId == "paper")
	{
		reason = "SESSION_PAPER_RECOVERY_REQUIRED";
		return false;
	}
	if (!record.fencePending ||
		(record.fenceReason != "session_revoked" &&
		 record.fenceReason != "session_expired"))
	{
		reason = "SESSION_FENCE_RECORD_INVALID";
		return false;
	}
	if (!IsIssuerAllowed(record.issuer))
	{
		reason = "LEASE_STORE_ISSUER_NOT_ALLOWLISTED";
		return false;
	}
	if (localSessionMayExist)
	{
		const bool revoked = record.fenceReason == "session_expired" ?
			m_controlPlane.RevokeExpired(record.issuer, record.token,
				record.leaseGeneration, reason) :
			m_controlPlane.Revoke(record.issuer, record.token,
				record.leaseGeneration, reason);
		if (revoked) return true;
		if (reason == "SESSION_LEASE_GENERATION_MISMATCH")
		{
			if (m_controlPlane.RevokeCurrentIfOwner(
				record.issuer, record.token, record.agentId, record.sessionId,
				record.fenceReason, reason))
				return true;
		}
		if (reason != "SESSION_NOT_FOUND") return false;
	}
	const std::uint64_t nowMs = static_cast<std::uint64_t>(
		std::chrono::duration_cast<std::chrono::milliseconds>(
			std::chrono::system_clock::now().time_since_epoch()).count());
	TradingToolHostSessionBinding binding;
	if (!ResolveLeaseBinding(record, nowMs, binding, reason)) return false;
	binding.enabled = false;
	return m_controlPlane.FenceRestored(
		record.issuer, binding, record.fenceReason, reason);
}

bool UnixSessionSupervisorServer::BeginWatchTransaction(
	const std::vector<SessionSupervisorLeaseRecord>& records,
	std::string& watchTransactionId,
	std::string& reason)
{
	watchTransactionId.clear();
	if (records.empty() || records.size() > 2)
	{
		reason = "WATCH_TRANSACTION_BINDINGS_INVALID";
		return false;
	}
	std::vector<TradingToolHostSessionBinding> expectedBindings;
	expectedBindings.reserve(records.size());
	for (std::size_t i = 0; i < records.size(); ++i)
	{
		if (records[i].templateId != "watch" ||
			records[i].issuer != records[0].issuer ||
			records[i].agentId != records[0].agentId ||
			records[i].sessionId != records[0].sessionId)
		{
			reason = "WATCH_TRANSACTION_BINDINGS_INVALID";
			return false;
		}
		expectedBindings.push_back(WatchIdentity(records[i]));
	}
	return m_controlPlane.BeginWatchTransaction(
		records[0].issuer, expectedBindings,
		watchTransactionId, reason);
}

bool UnixSessionSupervisorServer::RevokeExactWatchRecords(
	const std::vector<SessionSupervisorLeaseRecord>& records,
	const std::string& watchTransactionId,
	bool& allLocalAbsent,
	std::string& reason)
{
	allLocalAbsent = false;
	if (records.empty() || watchTransactionId.empty())
	{
		reason = "WATCH_TRANSACTION_BINDINGS_INVALID";
		return false;
	}
	std::vector<TradingToolHostSessionBinding> expectedBindings;
	expectedBindings.reserve(records.size());
	for (std::size_t i = 0; i < records.size(); ++i)
		expectedBindings.push_back(WatchIdentity(records[i]));
	return m_controlPlane.RevokeExactWatchTransaction(
		records[0].issuer, watchTransactionId, expectedBindings,
		records.back().fenceReason, allLocalAbsent, reason);
}

bool UnixSessionSupervisorServer::FenceWatchRecord(
	const SessionSupervisorLeaseRecord& record,
	std::string& watchTransactionId,
	std::string& reason)
{
	if (record.templateId != "watch" || !record.fencePending ||
		(record.fenceReason != "session_revoked" &&
		 record.fenceReason != "session_expired"))
	{
		reason = "SESSION_FENCE_RECORD_INVALID";
		return false;
	}
	const std::vector<SessionSupervisorLeaseRecord> records =
		WatchTransactionRecords(record);
	if (watchTransactionId.empty() &&
		!BeginWatchTransaction(records, watchTransactionId, reason))
		return false;
	bool allLocalAbsent = false;
	if (RevokeExactWatchRecords(
		records, watchTransactionId, allLocalAbsent, reason))
		return true;
	if (!allLocalAbsent) return false;
	reason.clear();
	return FenceStoredRecord(record, false, reason);
}

bool UnixSessionSupervisorServer::ReleaseWatchTransaction(
	const std::vector<SessionSupervisorLeaseRecord>& records,
	const std::string& watchTransactionId,
	std::string& reason)
{
	if (records.empty() || watchTransactionId.empty())
	{
		reason = "WATCH_TRANSACTION_RESERVATION_INVALID";
		return false;
	}
	std::vector<TradingToolHostSessionBinding> expectedBindings;
	expectedBindings.reserve(records.size());
	for (std::size_t i = 0; i < records.size(); ++i)
		expectedBindings.push_back(WatchIdentity(records[i]));
	return m_controlPlane.ReleaseWatchTransaction(
		records[0].issuer, watchTransactionId,
		expectedBindings, reason);
}

bool UnixSessionSupervisorServer::RemoveLeaseAndReleaseWatchTransaction(
	const SessionSupervisorLeaseRecord& record,
	const std::vector<SessionSupervisorLeaseRecord>& transactionRecords,
	const std::string& watchTransactionId,
	std::string& reason)
{
	if (record.templateId != "watch" ||
		transactionRecords.empty())
	{
		reason = "SESSION_FENCE_RECORD_INVALID";
		return false;
	}
	if (m_leaseStore == nullptr ||
		!m_leaseStore->Remove(record.token, reason))
		return false;
	return ReleaseWatchTransaction(
		transactionRecords, watchTransactionId, reason);
}

bool UnixSessionSupervisorServer::FenceCommittedMutation(
	const SessionSupervisorLeaseRecord& localRecord,
	const SessionSupervisorLeaseRecord& pendingRecord,
	const std::vector<SessionSupervisorLeaseRecord>&
		watchTransactionRecords,
	const std::string& watchTransactionId,
	std::string& reason)
{
	if (pendingRecord.templateId == "paper" ||
		localRecord.templateId == "paper")
	{
		reason = "SESSION_PAPER_RECOVERY_REQUIRED";
		return false;
	}
	if (!pendingRecord.fencePending ||
		pendingRecord.fenceReason != "session_revoked")
	{
		reason = "SESSION_FENCE_RECORD_INVALID";
		return false;
	}
	if (!IsIssuerAllowed(pendingRecord.issuer))
	{
		reason = "LEASE_STORE_ISSUER_NOT_ALLOWLISTED";
		return false;
	}
	if (pendingRecord.templateId == "watch")
	{
		if (watchTransactionRecords.empty())
		{
			reason = "WATCH_TRANSACTION_BINDINGS_INVALID";
			return false;
		}
		bool allLocalAbsent = false;
		if (RevokeExactWatchRecords(
			watchTransactionRecords, watchTransactionId,
			allLocalAbsent, reason))
			return true;
		if (!allLocalAbsent) return false;
		reason.clear();
		return FenceStoredRecord(pendingRecord, false, reason);
	}

	if (!localRecord.token.empty())
	{
		if (m_controlPlane.RevokeCurrentIfOwner(
			localRecord.issuer, localRecord.token, localRecord.agentId,
			localRecord.sessionId, "session_revoked", reason))
			return true;
		if (reason != "SESSION_NOT_FOUND") return false;
	}
	if (pendingRecord.token != localRecord.token)
	{
		if (m_controlPlane.RevokeCurrentIfOwner(
			pendingRecord.issuer, pendingRecord.token, pendingRecord.agentId,
			pendingRecord.sessionId, "session_revoked", reason))
			return true;
		if (reason != "SESSION_NOT_FOUND") return false;
	}
	return FenceStoredRecord(pendingRecord, false, reason);
}

bool UnixSessionSupervisorServer::HasPendingOwner(
	const std::string& agentId,
	const std::string& sessionId) const
{
	if (m_leaseStore == nullptr) return false;
	const std::vector<SessionSupervisorLeaseRecord> records = m_leaseStore->List();
	for (std::size_t i = 0; i < records.size(); ++i)
		if ((records[i].fencePending || records[i].recoveryOnly ||
			records[i].paperFinalizationRequired ||
			records[i].paperFinalizationState !=
				SessionSupervisorPaperFinalizationState::None) &&
			records[i].agentId == agentId &&
			records[i].sessionId == sessionId)
			return true;
	return false;
}

bool UnixSessionSupervisorServer::RestoreLeases(std::string& reason)
{
	if (m_leaseStore == nullptr) return true;
	const std::uint64_t nowMs = static_cast<std::uint64_t>(
		std::chrono::duration_cast<std::chrono::milliseconds>(
			std::chrono::system_clock::now().time_since_epoch()).count());
	const std::vector<SessionSupervisorLeaseRecord> records = m_leaseStore->List();
	for (std::size_t i = 0; i < records.size(); ++i)
	{
		if (!IsIssuerAllowed(records[i].issuer))
		{
			reason = "LEASE_STORE_ISSUER_NOT_ALLOWLISTED";
			return false;
		}
		SessionSupervisorLeaseRecord record = records[i];
		if (record.paperFinalizationState !=
			SessionSupervisorPaperFinalizationState::None)
		{
			// HSL7 finalization rows are non-authorizing tombstones.  PENDING
			// is resumed only by the exact root finalization request. Rebuild
			// the exact local/catalog correlation in a disabled state so the
			// final audit never mistakes bare absence for proof and ACK remains
			// the only operation that can purge it.
			TradingToolHostSessionBinding tombstone;
			if (!ResolveLeaseBinding(record, nowMs, tombstone, reason) ||
				!m_controlPlane.RestorePaperFinalizationTombstone(
					record.issuer, tombstone, record, reason))
				return false;
			continue;
		}
		if (record.templateId == "paper" &&
			(record.recoveryOnly || record.fencePending ||
				record.expiresAtMs <= nowMs))
		{
			// The execution runtime may need to reconnect once after the first
			// recovery audit observes the old broker epoch.  RestoreLeases runs
			// before the normal ReapExpired loop, so give only this narrowly
			// allow-listed transient state a bounded same-process retry.  The
			// owner audit still has to return authoritative, complete, flat
			// evidence; no safety result is accepted merely because it is being
			// retried.
			const std::chrono::steady_clock::time_point retryDeadline =
				std::chrono::steady_clock::now() +
				std::chrono::seconds(60);
			for (;;)
			{
				const std::uint64_t recoveryNowMs =
					static_cast<std::uint64_t>(
						std::chrono::duration_cast<std::chrono::milliseconds>(
							std::chrono::system_clock::now().time_since_epoch()).count());
				ExecutionControlStatusResult commandResult;
				ExecutionOwnerAuditResult ownerAudit;
				if (!EnterPaperRecovery(record, recoveryNowMs, std::string(),
						commandResult, ownerAudit, reason))
				{
					if (!IsTransientPaperRecoveryAuditReason(reason) ||
						std::chrono::steady_clock::now() >= retryDeadline)
						return false;
					std::this_thread::sleep_for(
						std::chrono::milliseconds(100));
					continue;
				}
				if (ownerAudit.status == ExecutionCommandStatus::Accepted &&
					ownerAudit.ownerAuditAuthoritative &&
					ownerAudit.ownerAuditComplete &&
					ownerAudit.ownerActiveOrderCount == 0 &&
					ownerAudit.ownerUncertainCommandCount == 0)
				{
					if (!record.paperFinalizationRequired)
					{
						ExecutionControlResult finalAudit;
						if (!FinalizePaperRecovery(record, finalAudit, reason) &&
							reason != "SESSION_OWNER_RECOVERY_REQUIRED")
							return false;
					}
					// External restart reconstructs recovery-only authority and
					// retains it; explicit PAPER finalization owns deletion.
					break;
				}
				if (ownerAudit.status != ExecutionCommandStatus::Accepted &&
					ownerAudit.reasonCode !=
						"RECOVERY_OWNER_UNCERTAIN_COMMANDS_REMAIN")
				{
					reason = ownerAudit.reasonCode.empty() ?
						"SESSION_OWNER_AUDIT_INCOMPLETE" :
						ownerAudit.reasonCode;
					if (!IsTransientPaperRecoveryAuditReason(reason) ||
						std::chrono::steady_clock::now() >= retryDeadline)
						return false;
					std::this_thread::sleep_for(
						std::chrono::milliseconds(100));
					continue;
				}
				// Preserve the existing fail-closed handling for uncertain
				// commands.  It is never converted into a retryable result.
				break;
			}
			continue;
		}
		if (record.fenceComplete)
		{
			const std::vector<SessionSupervisorLeaseRecord>
				transactionRecords = WatchTransactionRecords(record);
			std::string watchTransactionId;
			if (!BeginWatchTransaction(
				transactionRecords, watchTransactionId, reason))
				return false;
			bool allLocalAbsent = false;
			if (!RevokeExactWatchRecords(
					transactionRecords, watchTransactionId,
					allLocalAbsent, reason) &&
				!allLocalAbsent)
				return false;
			if (record.expiresAtMs <= nowMs &&
				!RemoveLeaseAndReleaseWatchTransaction(
					record, transactionRecords,
					watchTransactionId, reason))
				return false;
			continue;
		}
		// WATCH bearer material is deliberately runtime-only. Never restore an
		// active WATCH lease across a Gateway process restart: /run may have
		// been cleared by a host reboot while the encrypted lease store
		// survived. Persist a pending owner fence before touching the restored
		// control plane, then complete and remove it through the same durable
		// recovery path used by explicit revoke.
		const bool restartFence =
			record.templateId == "watch" &&
			!record.fencePending &&
			record.expiresAtMs > nowMs;
		if (record.fencePending || record.expiresAtMs <= nowMs || restartFence)
		{
			std::string watchTransactionId;
			if (record.templateId == "watch" &&
				!BeginWatchTransaction(
					WatchTransactionRecords(record),
					watchTransactionId, reason))
				return false;
			if (!record.fencePending &&
				!MarkFencePending(
					records[i],
					restartFence ? "session_revoked" : "session_expired",
					record,
					reason))
				return false;
			if (record.templateId == "watch")
			{
				if (!FenceWatchRecord(
					record, watchTransactionId, reason)) return false;
				if (record.expiresAtMs > nowMs)
				{
					if (m_crashPointHook &&
						m_crashPointHook(
							"after_watch_restart_fence_before_tombstone_commit"))
					{
						reason =
							"SUPERVISOR_FAULT_INJECTED:"
							"after_watch_restart_fence_before_tombstone_commit";
						return false;
					}
					record.fenceComplete = true;
					if (!m_leaseStore->Replace(
						record.token, record, reason)) return false;
					continue;
				}
			}
			else if (!FenceStoredRecord(record, false, reason))
				return false;
			if (record.templateId == "watch")
			{
				const std::vector<SessionSupervisorLeaseRecord>
					transactionRecords =
						WatchTransactionRecords(record);
				if (!RemoveLeaseAndReleaseWatchTransaction(
					record, transactionRecords,
					watchTransactionId, reason)) return false;
			}
			else if (!m_leaseStore->Remove(record.token, reason))
				return false;
			continue;
		}
		TradingToolHostSessionBinding binding;
		if (!ResolveLeaseBinding(record, nowMs, binding, reason)) return false;
		if (!m_controlPlane.Provision(record.issuer, binding, reason)) return false;
	}
	reason.clear();
	return true;
}

void UnixSessionSupervisorServer::AcceptLoop()
{
	const int listenFd = m_listenFd;
	while (!m_stop.load())
	{
		pollfd ready;
		ready.fd = listenFd;
		ready.events = POLLIN;
		ready.revents = 0;
		const int pollResult = ::poll(&ready, 1, 100);
		if (pollResult < 0) { if (errno == EINTR) continue; break; }
		if (pollResult == 0) continue;
		if ((ready.revents & (POLLERR | POLLHUP | POLLNVAL)) != 0) break;
		if ((ready.revents & POLLIN) == 0) continue;
		if (m_stop.load()) break;
		const int clientFd = ::accept4(listenFd, nullptr, nullptr, SOCK_CLOEXEC);
		if (clientFd < 0)
		{
			if (errno == EINTR) continue;
			if (m_stop.load() || errno == EBADF || errno == EINVAL) break;
			continue;
		}
		HandleClient(clientFd);
	}
}

void UnixSessionSupervisorServer::HandleClient(int clientFd)
{
	std::lock_guard<std::mutex> operationLock(m_operationMutex);
	SessionSupervisorResult result;
	SessionSupervisorRequest request;
	std::string issuerName;
	bool decodedRequest = false;
	struct ucred credentials;
	socklen_t credentialsLength = sizeof(credentials);
	const std::map<std::uint32_t, std::string>::const_iterator issuer =
		::getsockopt(clientFd, SOL_SOCKET, SO_PEERCRED, &credentials, &credentialsLength) == 0 ?
		m_authorizedIssuers.find(static_cast<std::uint32_t>(credentials.uid)) :
		m_authorizedIssuers.end();
	if (issuer == m_authorizedIssuers.end())
	{
		result.ReasonCode() = "SUPERVISOR_PEER_UID_DENIED";
	}
	else
	{
		issuerName = issuer->second;
		std::string body;
		std::string reason;
		if (!TypedToolProtocol::ReadFrame(clientFd, m_maxRequestBytes, m_ioTimeoutMs, body, reason))
			result.ReasonCode() = "SUPERVISOR_INVALID_FRAME:" + reason;
		else if (!SessionSupervisorProtocol::DecodeRequest(body, request, reason))
			result.ReasonCode() = reason;
		else if ((request.operation ==
				SessionSupervisorOperation::RecoveryQuery ||
			 request.operation ==
				SessionSupervisorOperation::PaperFinalize ||
				 request.operation ==
					SessionSupervisorOperation::PaperFinalizeAck ||
				 request.operation ==
					SessionSupervisorOperation::PaperTerminalizeAck ||
				 request.operation ==
					SessionSupervisorOperation::PaperTerminalWitnessPrepare ||
				 request.operation ==
					SessionSupervisorOperation::PaperTerminalWitnessAck) &&
			static_cast<std::uint32_t>(credentials.uid) != m_rootCustodianUid)
			result.ReasonCode() = "SUPERVISOR_ROOT_CUSTODIAN_REQUIRED";
		else if (request.ttlMs > m_maxSessionTtlMs)
			result.ReasonCode() = "SUPERVISOR_TTL_EXCEEDS_LIMIT";
		else if (m_auditJournal != nullptr &&
			!m_auditJournal->Append(request, issuerName, "intent", "pending",
				request.expectedGeneration, result.ReasonCode()))
			result.ReasonCode() = "SUPERVISOR_AUDIT_INTENT_FAILED:" + result.ReasonCode();
		else if (request.operation ==
			SessionSupervisorOperation::PaperFinalize)
		{
			decodedRequest = true;
			HandlePaperFinalize(request, result);
		}
			else if (request.operation ==
				SessionSupervisorOperation::PaperFinalizeAck)
		{
			decodedRequest = true;
				HandlePaperFinalizeAck(request, result);
			}
			else if (request.operation ==
				SessionSupervisorOperation::PaperTerminalizeAck)
			{
				decodedRequest = true;
				HandlePaperTerminalizeAck(request, result);
			}
			else if (request.operation ==
				SessionSupervisorOperation::PaperTerminalWitnessPrepare)
			{
				decodedRequest = true;
				HandlePaperTerminalWitnessPrepare(request, result);
			}
			else if (request.operation ==
				SessionSupervisorOperation::PaperTerminalWitnessAck)
			{
				decodedRequest = true;
				HandlePaperTerminalWitnessAck(request, result);
			}
		else if (request.operation == SessionSupervisorOperation::RecoveryQuery)
		{
			decodedRequest = true;
			result.TargetCommandId() = request.targetCommandId;
			result.CommandStatus() = "unavailable";
			result.CommandReasonCode() = "unavailable";
			result.ExecutionServiceEpoch() = "unavailable";
			result.OwnerAccount() = "unavailable";
			result.OwnerExecutionDomain() = "unavailable";
			result.ownerFenced = false;
			SessionSupervisorLeaseRecord previous;
			if (m_leaseStore == nullptr)
				result.ReasonCode() = "SUPERVISOR_DURABLE_LEASE_STORE_REQUIRED";
			else if (!m_leaseStore->Get(request.token, previous))
				result.ReasonCode() = "SESSION_LEASE_NOT_FOUND";
			else if (previous.leaseGeneration != request.expectedGeneration)
				result.ReasonCode() = "SESSION_LEASE_GENERATION_MISMATCH";
			else if (previous.templateId != "paper")
				result.ReasonCode() = "SESSION_RECOVERY_QUERY_PAPER_REQUIRED";
			else if (previous.paperFinalizationRequired &&
				!request.requirePaperFinalization)
			{
				result.paperFinalizationRequired = true;
				result.ReasonCode() =
					"PAPER_FINALIZATION_DOWNGRADE_REJECTED";
			}
			else if (previous.paperFinalizationState !=
				SessionSupervisorPaperFinalizationState::None)
				result.ReasonCode() =
					"PAPER_FINALIZATION_OPERATION_REQUIRED";
			else
			{
				if (request.requirePaperFinalization)
					previous.paperFinalizationRequired = true;
				result.paperFinalizationRequired =
					previous.paperFinalizationRequired;
				ExecutionControlStatusResult commandResult;
				ExecutionOwnerAuditResult ownerAudit;
				std::string queryReason;
				const std::uint64_t nowMs = static_cast<std::uint64_t>(
					std::chrono::duration_cast<std::chrono::milliseconds>(
						std::chrono::system_clock::now().time_since_epoch()).count());
				if (!EnterPaperRecovery(previous, nowMs,
						request.targetCommandId, commandResult,
						ownerAudit, queryReason))
				{
					result.ReasonCode() = queryReason.empty() ?
						"SESSION_RECOVERY_QUERY_FAILED" : queryReason;
					SessionSupervisorLeaseRecord recovery;
					if (m_leaseStore->Get(request.token, recovery) &&
						recovery.recoveryOnly)
					{
						result.paperFinalizationRequired =
							recovery.paperFinalizationRequired;
						result.recoveryOnly = true;
						result.leaseGeneration =
							recovery.leaseGeneration;
						result.recoveryExpiresAtMs =
							recovery.expiresAtMs;
					}
					goto write_result;
				}
				result.recoveryOnly = true;
				result.paperFinalizationRequired =
					previous.paperFinalizationRequired;
				result.leaseGeneration = previous.leaseGeneration;
				result.recoveryExpiresAtMs = previous.expiresAtMs;
				result.ownerAuditAuthoritative =
					ownerAudit.ownerAuditAuthoritative;
				result.ownerAuditComplete = ownerAudit.ownerAuditComplete;
				result.ownerActiveOrderCount =
					ownerAudit.ownerActiveOrderCount;
				result.ownerUncertainCommandCount =
					ownerAudit.ownerUncertainCommandCount;
				result.brokerConnectionEpoch =
					ownerAudit.brokerConnectionEpoch;
				result.brokerActiveGeneration =
					ownerAudit.brokerActiveGeneration;
				result.brokerTerminalGeneration =
					ownerAudit.brokerTerminalGeneration;
				result.OwnerAccount() = ownerAudit.ownerAccount.empty() ?
					"unavailable" : ownerAudit.ownerAccount;
				result.OwnerExecutionDomain() =
					ownerAudit.ownerExecutionDomain.empty() ?
						"unavailable" : ownerAudit.ownerExecutionDomain;
				result.CommandReasonCode() = commandResult.reasonCode.empty() ?
					"NONE" : commandResult.reasonCode;
				result.ExecutionServiceEpoch() = commandResult.serviceEpoch.empty() ?
					"unavailable" : commandResult.serviceEpoch;
				result.executionServiceFencingGeneration =
					commandResult.serviceFencingGeneration;
				const bool authoritativeIdentity =
					!commandResult.serviceEpoch.empty() &&
					commandResult.serviceFencingGeneration != 0;
				if (!ownerAudit.ownerAuditAuthoritative ||
					!ownerAudit.ownerAuditComplete)
				{
					result.ReasonCode() = ownerAudit.reasonCode.empty() ?
						"SESSION_OWNER_AUDIT_INCOMPLETE" :
						ownerAudit.reasonCode;
					goto write_result;
				}
				if (commandResult.status == ExecutionCommandStatus::Rejected &&
					commandResult.reasonCode == "EXECUTION_COMMAND_NOT_FOUND" &&
					commandResult.targetCommandId == request.targetCommandId &&
					authoritativeIdentity)
				{
					result.accepted = true;
					result.authoritativeCommandStatus = true;
					result.CommandStatus() = "not_found";
					result.orderId = -1;
					result.ReasonCode() =
						"RECOVERY_QUERY_NOT_FOUND_PROVEN_RECOVERY_ONLY";
				}
				else if (commandResult.status == ExecutionCommandStatus::Accepted &&
					commandResult.targetCommandId == request.targetCommandId &&
					authoritativeIdentity &&
					(commandResult.targetStatus == ExecutionCommandStatus::Accepted ||
					 commandResult.targetStatus == ExecutionCommandStatus::Rejected ||
					 commandResult.targetStatus == ExecutionCommandStatus::Uncertain))
				{
					result.accepted = true;
					result.authoritativeCommandStatus = true;
					result.orderId = commandResult.orderId;
					result.CommandStatus() =
						commandResult.targetStatus == ExecutionCommandStatus::Accepted ?
							"accepted" :
						(commandResult.targetStatus == ExecutionCommandStatus::Rejected ?
							"rejected" : "uncertain");
					result.ReasonCode() =
						(commandResult.targetStatus == ExecutionCommandStatus::Uncertain ||
						 commandResult.orderId >= 0) ?
							"RECOVERY_QUERY_CANNOT_FULL_FENCE" :
							"RECOVERY_QUERY_PROVEN_RECOVERY_ONLY";
				}
				else
				{
					result.ReasonCode() = commandResult.reasonCode.empty() ?
						"SESSION_RECOVERY_QUERY_UNAVAILABLE" :
						commandResult.reasonCode;
				}
			}
		}
		else if (request.operation == SessionSupervisorOperation::Revoke)
			{
				decodedRequest = true;
			SessionSupervisorLeaseRecord previous;
			if (m_leaseStore != nullptr &&
				!m_leaseStore->Get(request.token, previous))
				result.ReasonCode() = "SESSION_LEASE_NOT_FOUND";
			else if (m_leaseStore != nullptr &&
				previous.leaseGeneration != request.expectedGeneration)
				result.ReasonCode() = "SESSION_LEASE_GENERATION_MISMATCH";
			else if (m_leaseStore != nullptr &&
				previous.templateId == "paper")
			{
				result.leaseGeneration = previous.leaseGeneration;
				// External PAPER opts into an irreversible explicit-finalization
				// transition. Naked revoke may fence but can never delete it.
				// Ordinary/local PAPER keeps the established zero-audit revoke.
				if (previous.paperFinalizationRequired ||
					previous.paperFinalizationState !=
						SessionSupervisorPaperFinalizationState::None)
				{
					result.accepted = false;
					result.ReasonCode() =
						"PAPER_FINALIZATION_OPERATION_REQUIRED";
				}
				else
				{
					const std::uint64_t nowMs =
						static_cast<std::uint64_t>(
							std::chrono::duration_cast<
								std::chrono::milliseconds>(
								std::chrono::system_clock::now().
									time_since_epoch()).count());
					ExecutionControlStatusResult commandResult;
					ExecutionOwnerAuditResult ownerAudit;
					if (!EnterPaperRecovery(previous, nowMs, std::string(),
							commandResult, ownerAudit,
							result.ReasonCode()))
						result.accepted = false;
					else if (m_crashPointHook &&
						m_crashPointHook("after_lease_commit"))
					{
						result.accepted = false;
						result.ReasonCode() =
							"SUPERVISOR_FAULT_INJECTED:after_lease_commit";
					}
					else
					{
						if (ownerAudit.status !=
								ExecutionCommandStatus::Accepted ||
							!ownerAudit.ownerAuditAuthoritative ||
							!ownerAudit.ownerAuditComplete ||
							ownerAudit.ownerActiveOrderCount != 0 ||
							ownerAudit.ownerUncertainCommandCount != 0)
						{
							result.accepted = false;
							result.ReasonCode() =
								"SESSION_OWNER_RECOVERY_REQUIRED";
						}
						else
						{
							ExecutionControlResult finalAudit;
							result.accepted = FinalizePaperRecovery(
								previous, finalAudit,
								result.ReasonCode());
						}
					}
				}
			}
			else if (m_leaseStore != nullptr && previous.fenceComplete)
			{
				const std::vector<SessionSupervisorLeaseRecord>
					transactionRecords =
						WatchTransactionRecords(previous);
				std::string watchTransactionId;
				result.accepted = BeginWatchTransaction(
					transactionRecords, watchTransactionId,
					result.ReasonCode());
				bool allLocalAbsent = false;
				if (result.accepted)
				{
					result.accepted = RevokeExactWatchRecords(
						transactionRecords, watchTransactionId,
						allLocalAbsent, result.ReasonCode());
					if (!result.accepted && allLocalAbsent)
						result.accepted = true;
				}
				if (result.accepted)
					result.accepted =
						RemoveLeaseAndReleaseWatchTransaction(
							previous, transactionRecords,
							watchTransactionId, result.ReasonCode());
				if (result.accepted)
					result.leaseGeneration = request.expectedGeneration;
			}
			else
			{
				SessionSupervisorLeaseRecord pending = previous;
				std::string watchTransactionId;
				if (m_leaseStore != nullptr &&
					pending.templateId == "watch" &&
					!BeginWatchTransaction(
						WatchTransactionRecords(pending),
						watchTransactionId,
						result.ReasonCode()))
				{
					result.accepted = false;
					goto write_result;
				}
				if (m_leaseStore != nullptr &&
					!MarkFencePending(previous, "session_revoked", pending,
						result.ReasonCode()))
					result.accepted = false;
				else if (m_leaseStore != nullptr)
					result.accepted = pending.templateId == "watch" ?
						FenceWatchRecord(
							pending, watchTransactionId,
							result.ReasonCode()) :
						FenceStoredRecord(pending, true, result.ReasonCode());
				else
					result.accepted = m_controlPlane.Revoke(
						issuer->second, request.token,
						request.expectedGeneration, result.ReasonCode());
				if (result.accepted &&
					m_crashPointHook &&
					m_crashPointHook("after_lease_commit"))
				{
					result.ReasonCode() = "SUPERVISOR_FAULT_INJECTED:after_lease_commit";
					result.accepted = false;
					goto write_result;
				}
				if (result.accepted && m_leaseStore != nullptr &&
					(pending.templateId == "watch" ?
						!RemoveLeaseAndReleaseWatchTransaction(
							pending,
							WatchTransactionRecords(pending),
							watchTransactionId, result.ReasonCode()) :
						!m_leaseStore->Remove(
							request.token, result.ReasonCode())))
				{
					result.accepted = false;
				}
				if (result.accepted)
					result.leaseGeneration = request.expectedGeneration;
			}
		}
		else if (request.operation == SessionSupervisorOperation::Renew ||
			request.operation == SessionSupervisorOperation::Rotate)
		{
			decodedRequest = true;
			const std::uint64_t nowMs = static_cast<std::uint64_t>(
				std::chrono::duration_cast<std::chrono::milliseconds>(
					std::chrono::system_clock::now().time_since_epoch()).count());
				SessionSupervisorLeaseRecord previous;
				SessionSupervisorLeaseRecord replacement;
				std::vector<SessionSupervisorLeaseRecord>
					watchTransactionRecords;
				std::string watchTransactionId;
			if (m_leaseStore != nullptr &&
				(!m_leaseStore->Get(request.token, previous) ||
				 previous.leaseGeneration != request.expectedGeneration))
				result.ReasonCode() = "SESSION_LEASE_GENERATION_MISMATCH";
			else if (m_leaseStore != nullptr &&
				(previous.fencePending ||
				 HasPendingOwner(previous.agentId, previous.sessionId)))
				result.ReasonCode() = "SESSION_OWNER_FENCE_PENDING";
			else
			{
				if (m_leaseStore != nullptr)
					{
						replacement = previous;
					replacement.token = request.operation == SessionSupervisorOperation::Rotate ?
						request.replacementToken : request.token;
					replacement.expiresAtMs = nowMs + request.ttlMs;
					replacement.leaseGeneration = request.expectedGeneration + 1;
					// A write-ahead mutation is never restartable as active authority.
					// A crash or any later store failure leaves this owner in the
					// durable fence-recovery path.
						replacement.fencePending = true;
						replacement.fenceReason = "session_revoked";
						if (replacement.templateId == "watch" ||
							replacement.templateId == "paper")
						{
							replacement.predecessorToken = previous.token;
							replacement.predecessorGeneration =
								previous.leaseGeneration;
						}
						if (replacement.templateId == "watch")
						{
							watchTransactionRecords.push_back(previous);
							if (previous.token != replacement.token ||
								previous.leaseGeneration !=
									replacement.leaseGeneration)
								watchTransactionRecords.push_back(replacement);
							if (!BeginWatchTransaction(
								watchTransactionRecords,
								watchTransactionId,
								result.ReasonCode()))
							{
								result.accepted = false;
								goto write_result;
							}
						}
						if (!m_leaseStore->Replace(request.token, replacement, result.ReasonCode()))
					{
						result.accepted = false;
						goto write_result;
					}
				}
				if (m_crashPointHook && m_crashPointHook("after_lease_commit"))
				{
					result.ReasonCode() = "SUPERVISOR_FAULT_INJECTED:after_lease_commit";
					if (m_leaseStore != nullptr &&
						replacement.templateId == "paper")
					{
						SessionSupervisorLeaseRecord recovery = previous;
						recovery.expiresAtMs = nowMs + m_maxSessionTtlMs;
						recovery.recoveryOnly = true;
						recovery.recoveryCommandId.clear();
						recovery.fencePending = false;
						recovery.fenceReason.clear();
						if (!m_leaseStore->Replace(
								replacement.token, recovery,
								result.ReasonCode()))
							goto write_result;
						ExecutionControlStatusResult commandResult;
						ExecutionOwnerAuditResult ownerAudit;
						std::string recoveryReason;
						if (!EnterPaperRecovery(recovery, nowMs,
								std::string(), commandResult, ownerAudit,
								recoveryReason))
							result.ReasonCode() = recoveryReason;
						else
							result.ReasonCode() =
								"SUPERVISOR_FAULT_INJECTED:after_lease_commit";
					}
					else if (m_leaseStore != nullptr)
						{
							std::string fenceReason;
							if (!FenceCommittedMutation(
								previous, replacement,
								watchTransactionRecords,
								watchTransactionId, fenceReason))
								result.ReasonCode() = fenceReason;
							else
							{
								std::string removeReason;
								const bool removed =
									replacement.templateId == "watch" ?
										RemoveLeaseAndReleaseWatchTransaction(
											replacement,
											watchTransactionRecords,
											watchTransactionId,
											removeReason) :
										m_leaseStore->Remove(
											replacement.token,
											removeReason);
								if (!removed)
									result.ReasonCode() =
									"SUPERVISOR_FENCED_RECORD_REMOVE_FAILED:" +
									removeReason;
						}
					}
					goto write_result;
				}
					if (m_leaseStore != nullptr &&
						replacement.templateId == "watch")
						result.accepted =
							m_controlPlane.RotateForWatchTransaction(
								issuer->second, watchTransactionId,
								WatchIdentity(previous), request.token,
								replacement.token,
								request.expectedGeneration,
								nowMs + request.ttlMs,
								result.leaseGeneration,
								result.ReasonCode());
					else if (m_leaseStore != nullptr &&
						replacement.templateId == "paper")
					{
						ExecutionOwnerAuditResult ownerAudit;
						result.accepted = m_controlPlane.RenewPaperAfterAudit(
							issuer->second, request.token,
							replacement.token, request.expectedGeneration,
							nowMs + request.ttlMs,
							result.leaseGeneration, ownerAudit,
							result.ReasonCode());
					}
					else
						result.accepted =
							request.operation ==
								SessionSupervisorOperation::Renew ?
								m_controlPlane.Renew(
									issuer->second, request.token,
									request.expectedGeneration,
									nowMs + request.ttlMs,
									result.leaseGeneration,
									result.ReasonCode()) :
								m_controlPlane.Rotate(
									issuer->second, request.token,
									request.replacementToken,
									request.expectedGeneration,
									nowMs + request.ttlMs,
									result.leaseGeneration,
									result.ReasonCode());
				if (m_leaseStore != nullptr && !result.accepted)
				{
					const std::string rejectionReason = result.ReasonCode();
					if (replacement.templateId == "paper")
					{
						SessionSupervisorLeaseRecord recovery = previous;
						recovery.expiresAtMs = nowMs + m_maxSessionTtlMs;
						recovery.recoveryOnly = true;
						recovery.recoveryCommandId.clear();
						recovery.fencePending = false;
						recovery.fenceReason.clear();
						if (!m_leaseStore->Replace(
								replacement.token, recovery,
								result.ReasonCode()))
							goto write_result;
						ExecutionControlStatusResult commandResult;
						ExecutionOwnerAuditResult ownerAudit;
						std::string recoveryReason;
						if (!EnterPaperRecovery(recovery, nowMs,
								std::string(), commandResult, ownerAudit,
								recoveryReason))
							result.ReasonCode() = recoveryReason;
						else
							result.ReasonCode() = rejectionReason;
					}
					else
					{
						std::string fenceReason;
						if (!FenceCommittedMutation(
							previous, replacement,
							watchTransactionRecords,
							watchTransactionId, fenceReason))
						result.ReasonCode() = fenceReason;
					else
					{
						std::string removeReason;
							const bool removed =
								replacement.templateId == "watch" ?
									RemoveLeaseAndReleaseWatchTransaction(
										replacement,
										watchTransactionRecords,
										watchTransactionId,
										removeReason) :
									m_leaseStore->Remove(
										replacement.token,
										removeReason);
							if (!removed)
							result.ReasonCode() =
								"SUPERVISOR_FENCED_RECORD_REMOVE_FAILED:" +
								removeReason;
						else
							result.ReasonCode() = rejectionReason;
						}
					}
				}
				else if (m_leaseStore != nullptr)
				{
						SessionSupervisorLeaseRecord active = replacement;
						active.predecessorToken.clear();
						active.predecessorGeneration = 0;
						active.fencePending = false;
					active.fenceReason.clear();
					std::string activationReason;
					const bool injectedFailure = m_crashPointHook &&
						m_crashPointHook("before_lease_activation_commit");
					if (injectedFailure ||
						!m_leaseStore->Replace(
							replacement.token, active, activationReason))
					{
						result.accepted = false;
						result.leaseGeneration = 0;
						const std::string persistReason = injectedFailure ?
							"SUPERVISOR_FAULT_INJECTED:before_lease_activation_commit" :
							"SUPERVISOR_LEASE_ACTIVATION_FAILED:" + activationReason;
						if (replacement.templateId == "paper")
						{
							SessionSupervisorLeaseRecord recovery = replacement;
							recovery.expiresAtMs = nowMs + m_maxSessionTtlMs;
							recovery.recoveryOnly = true;
							recovery.recoveryCommandId.clear();
							recovery.fencePending = false;
							recovery.fenceReason.clear();
							ExecutionControlStatusResult commandResult;
							ExecutionOwnerAuditResult ownerAudit;
							std::string recoveryReason;
							if (!EnterPaperRecovery(recovery, nowMs,
									std::string(), commandResult, ownerAudit,
									recoveryReason))
								result.ReasonCode() = recoveryReason;
							else
								result.ReasonCode() = persistReason;
						}
						else
						{
							std::string fenceReason;
							if (!FenceCommittedMutation(
								active, replacement,
								watchTransactionRecords,
								watchTransactionId, fenceReason))
							result.ReasonCode() = fenceReason;
						else
						{
							std::string removeReason;
								const bool removed =
									replacement.templateId == "watch" ?
										RemoveLeaseAndReleaseWatchTransaction(
											replacement,
											watchTransactionRecords,
											watchTransactionId,
											removeReason) :
										m_leaseStore->Remove(
											replacement.token,
											removeReason);
								if (!removed)
								result.ReasonCode() =
									"SUPERVISOR_FENCED_RECORD_REMOVE_FAILED:" +
									removeReason;
							else
								result.ReasonCode() = persistReason;
							}
						}
						}
						else if (replacement.templateId == "watch" &&
							!ReleaseWatchTransaction(
								watchTransactionRecords,
								watchTransactionId,
								result.ReasonCode()))
						{
							result.accepted = false;
							result.leaseGeneration = 0;
						}
					}
			}
		}
		else
		{
			decodedRequest = true;
			const std::uint64_t nowMs = static_cast<std::uint64_t>(
				std::chrono::duration_cast<std::chrono::milliseconds>(
					std::chrono::system_clock::now().time_since_epoch()).count());
			TradingToolHostSessionBinding binding;
			if (!m_bindingResolver(request, binding, result.ReasonCode())) result.accepted = false;
			else if (HasPendingOwner(
				binding.session.executionContext.agentId,
				binding.session.executionContext.sessionId))
				result.ReasonCode() = "SESSION_OWNER_FENCE_PENDING";
			else
			{
				SessionSupervisorLeaseRecord record;
				record.templateId = request.templateId;
				record.issuer = issuer->second;
				record.token = request.token;
				record.agentId = request.agentId;
				record.sessionId = request.sessionId;
				// Durable owner scope is a PAPER recovery invariant. WATCH has no
				// mutation/recovery authority and must keep both owner fields empty
				// so HSL6 cannot accidentally make a WATCH bearer look broker-owned.
				if (request.templateId == "paper")
				{
					record.ownerAccount =
						binding.session.executionContext.account;
					record.ownerExecutionDomain = binding.executionDomain;
				}
				record.peerUid = request.peerUid;
				record.expiresAtMs = binding.expiresAtMs;
				record.leaseGeneration = binding.leaseGeneration;
					record.fencePending = m_leaseStore != nullptr;
					record.fenceReason =
						m_leaseStore != nullptr ? "session_revoked" : std::string();
					std::vector<SessionSupervisorLeaseRecord>
						watchTransactionRecords;
					std::string watchTransactionId;
					if (m_leaseStore != nullptr &&
						record.templateId == "watch")
					{
						watchTransactionRecords.push_back(record);
						if (!BeginWatchTransaction(
							watchTransactionRecords,
							watchTransactionId,
							result.ReasonCode()))
						{
							result.accepted = false;
							goto write_result;
						}
					}
					if (m_leaseStore != nullptr && !m_leaseStore->Put(record, result.ReasonCode()))
						result.accepted = false;
				else
				{
					if (m_crashPointHook && m_crashPointHook("after_lease_commit"))
					{
						result.ReasonCode() = "SUPERVISOR_FAULT_INJECTED:after_lease_commit";
						goto write_result;
					}
						result.accepted =
							record.templateId == "watch" &&
							m_leaseStore != nullptr ?
								m_controlPlane.ProvisionForWatchTransaction(
									issuer->second,
									watchTransactionId,
									binding,
									result.ReasonCode()) :
								m_controlPlane.Provision(
									issuer->second,
									binding,
									result.ReasonCode());
				}
				if (m_leaseStore != nullptr && !result.accepted &&
					result.ReasonCode().find(
						"SUPERVISOR_FAULT_INJECTED:after_lease_commit") != 0)
					{
						const std::string rejectionReason = result.ReasonCode();
						if (record.templateId == "paper")
						{
							ExecutionControlStatusResult commandResult;
							ExecutionOwnerAuditResult ownerAudit;
							std::string recoveryReason;
							if (!EnterPaperRecovery(record, nowMs, std::string(),
									commandResult, ownerAudit, recoveryReason))
								result.ReasonCode() = recoveryReason;
							else
								result.ReasonCode() = rejectionReason;
						}
						else
						{
							std::string fenceReason;
							const bool fenced = record.templateId == "watch" ?
								FenceWatchRecord(
									record, watchTransactionId,
									fenceReason) :
								FenceStoredRecord(record, true, fenceReason);
							if (!fenced)
								result.ReasonCode() = fenceReason;
						else
						{
							std::string removeReason;
								const bool removed =
									record.templateId == "watch" ?
										RemoveLeaseAndReleaseWatchTransaction(
											record,
											watchTransactionRecords,
											watchTransactionId,
											removeReason) :
									m_leaseStore->Remove(
										record.token, removeReason);
							if (!removed)
								result.ReasonCode() =
									"SUPERVISOR_FENCED_RECORD_REMOVE_FAILED:" +
								removeReason;
						else
							result.ReasonCode() = rejectionReason;
						}
					}
				}
				else if (m_leaseStore != nullptr && result.accepted)
				{
					SessionSupervisorLeaseRecord active = record;
					active.fencePending = false;
					active.fenceReason.clear();
					std::string activationReason;
					const bool injectedFailure = m_crashPointHook &&
						m_crashPointHook("before_lease_activation_commit");
					if (injectedFailure ||
						!m_leaseStore->Replace(record.token, active, activationReason))
					{
						result.accepted = false;
						const std::string persistReason = injectedFailure ?
							"SUPERVISOR_FAULT_INJECTED:before_lease_activation_commit" :
							"SUPERVISOR_LEASE_ACTIVATION_FAILED:" + activationReason;
							if (record.templateId == "paper")
							{
								ExecutionControlStatusResult commandResult;
								ExecutionOwnerAuditResult ownerAudit;
								std::string recoveryReason;
								if (!EnterPaperRecovery(record, nowMs,
										std::string(), commandResult, ownerAudit,
										recoveryReason))
									result.ReasonCode() = recoveryReason;
								else
									result.ReasonCode() = persistReason;
							}
							else
							{
								std::string fenceReason;
								const bool fenced = record.templateId == "watch" ?
									FenceWatchRecord(
										record, watchTransactionId,
										fenceReason) :
									FenceStoredRecord(record, true, fenceReason);
							if (!fenced)
							result.ReasonCode() = fenceReason;
						else
						{
							std::string removeReason;
								const bool removed =
									record.templateId == "watch" ?
										RemoveLeaseAndReleaseWatchTransaction(
											record,
											watchTransactionRecords,
											watchTransactionId,
											removeReason) :
									m_leaseStore->Remove(
										record.token, removeReason);
							if (!removed)
								result.ReasonCode() =
									"SUPERVISOR_FENCED_RECORD_REMOVE_FAILED:" +
									removeReason;
							else
									result.ReasonCode() = persistReason;
								}
							}
						}
						else if (record.templateId == "watch" &&
							!ReleaseWatchTransaction(
								watchTransactionRecords,
								watchTransactionId,
								result.ReasonCode()))
							result.accepted = false;
					}
				if (result.accepted) result.leaseGeneration = binding.leaseGeneration;
			}
		}
		if (result.accepted && result.ReasonCode().empty()) result.ReasonCode() = "OK";
	}
	write_result:
	if (decodedRequest && m_auditJournal != nullptr)
	{
		std::string auditReason;
		const bool acceptedBeforeAudit = result.accepted;
		if (!m_auditJournal->Append(request, issuerName, "outcome",
			result.accepted ? "accepted" : result.ReasonCode(),
			result.leaseGeneration == 0 ? request.expectedGeneration : result.leaseGeneration,
			auditReason))
		{
			result.accepted = false;
			result.ReasonCode() = acceptedBeforeAudit ?
				"SUPERVISOR_AUDIT_OUTCOME_UNCERTAIN" :
				"SUPERVISOR_AUDIT_OUTCOME_FAILED";
		}
	}
	std::string reason;
	TypedToolProtocol::WriteFrame(clientFd,
		SessionSupervisorProtocol::EncodeResult(result), m_ioTimeoutMs, reason);
	::shutdown(clientFd, SHUT_RDWR);
	::close(clientFd);
}
