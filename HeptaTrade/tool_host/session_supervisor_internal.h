#pragma once
// Private shared support: not an installed API or a second authority.
#include "unix_session_supervisor_server.h"
#include <sys/stat.h>
namespace HeptaSessionSupervisorInternal {
bool UnlinkSocketIfUnchanged(const std::string& path,
	std::uint64_t expectedDevice, std::uint64_t expectedInode);
bool CaptureSocketPathIdentity(const std::string& path,
	std::uint64_t& device, std::uint64_t& inode);
bool SocketPathIdentityMatches(const std::string& path,
	std::uint64_t device, std::uint64_t inode);
TradingToolHostSessionBinding WatchIdentity(
	const SessionSupervisorLeaseRecord& record);
std::vector<SessionSupervisorLeaseRecord> WatchTransactionRecords(
	const SessionSupervisorLeaseRecord& record);
std::string HexEncode(const std::string& value);
bool Sha256(const std::string& value, std::string& prefixed);
bool PaperOwnerTokenSha256(
	const std::string& token, std::string& sha256);
bool CanonicalPaperOwnerSet(
	const std::vector<SessionSupervisorLeaseRecord>& records,
	std::string& canonical, std::string& sha256, std::string& reason);
bool SameFinalizationGroup(
	const SessionSupervisorLeaseRecord& record,
	const SessionSupervisorRequest& request);
bool IsTransientPaperRecoveryAuditReason(const std::string& reason);
bool CommonPaperScope(
	const std::vector<SessionSupervisorLeaseRecord>& records,
	std::string& account, std::string& domain, std::string& reason);
void CopyFinalizationAudit(
	const ExecutionOwnerAuditResult& audit,
	SessionSupervisorResult& result);
bool CanonicalSha256(const std::string& value);
bool ExactGlobalFinalizationAudit(
	const std::string& account,
	const std::string& domain,
	const ExecutionOwnerAuditResult& audit,
	std::string& reason);
bool SameCompositeFinalizationBarrier(
	const ExecutionOwnerAuditResult& left,
	const ExecutionOwnerAuditResult& right);
std::string BuildPaperFinalizationReceipt(
	const std::string& recoveryId,
	const std::string& finalizationId,
	const std::string& ownerSetSha256,
	std::uint64_t ownerCount,
	const std::string& ownerSetCanonical,
	const ExecutionOwnerAuditResult& audit);
bool ParseReceiptUnsigned(
	const std::string& value, std::uint64_t& parsed);
bool ParseCanonicalUnsigned(
	const std::string& value, std::uint64_t& parsed);
bool DecodeCanonicalHex(const std::string& encoded, std::string& decoded);
bool TerminalEvidenceIdentifier(
	const std::string& value, std::size_t maximum = 128);
bool TerminalEvidenceText(
	const std::string& value, std::size_t maximum = 128);
bool TerminalEvidenceBootId(const std::string& value);
bool ParseTerminalEvidence(
	const std::string& evidence,
	std::map<std::string, std::string>& fields,
	std::string& body);
bool ValidateTerminalOwnerSet(
	const std::string& encoded, const std::string& expectedSha256,
	std::uint64_t expectedCount, const std::string& account,
	const std::string& domain);
bool ValidateTerminalEvidence(
	const SessionSupervisorRequest& request,
	const SessionSupervisorLeaseRecord& terminalOwner,
	const SessionSupervisorResult& preliminary,
	const std::string& account, const std::string& domain,
	std::map<std::string, std::string>& fields,
	std::string& reason);
std::string BuildPaperTerminalWitnessAckReceipt(
	const std::map<std::string, std::string>& evidence,
	const std::string& evidenceFileSha256);
void CopyTerminalEvidenceResult(
	const std::map<std::string, std::string>& evidence,
	const std::string& evidenceFileSha256, bool replay,
	SessionSupervisorResult& result);
bool PopulateAuditFromReceipt(
	const std::string& receipt,
	SessionSupervisorResult& result,
	std::string& reason);
}
