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

namespace HeptaSessionSupervisorInternal {
bool UnlinkSocketIfUnchanged(const std::string& path,
	std::uint64_t expectedDevice, std::uint64_t expectedInode)
{
	struct stat current;
	if (::lstat(path.c_str(), &current) != 0 || !S_ISSOCK(current.st_mode) ||
		static_cast<std::uint64_t>(current.st_dev) != expectedDevice ||
		static_cast<std::uint64_t>(current.st_ino) != expectedInode)
		return false;
	return ::unlink(path.c_str()) == 0;
}

bool CaptureSocketPathIdentity(const std::string& path,
	std::uint64_t& device, std::uint64_t& inode)
{
	// Linux gives a bound AF_UNIX descriptor a socket-object inode, while
	// lstat(2) reports the dentry inode for its pathname. They are deliberately
	// tracked separately; comparing the two directly rejects every valid bind.
	struct stat pathStat;
	if (::lstat(path.c_str(), &pathStat) != 0 ||
		!S_ISSOCK(pathStat.st_mode)) return false;
	device = static_cast<std::uint64_t>(pathStat.st_dev);
	inode = static_cast<std::uint64_t>(pathStat.st_ino);
	return true;
}

bool SocketPathIdentityMatches(const std::string& path,
	std::uint64_t device, std::uint64_t inode)
{
	struct stat pathStat;
	return ::lstat(path.c_str(), &pathStat) == 0 &&
		S_ISSOCK(pathStat.st_mode) &&
		static_cast<std::uint64_t>(pathStat.st_dev) == device &&
		static_cast<std::uint64_t>(pathStat.st_ino) == inode;
}

TradingToolHostSessionBinding WatchIdentity(
	const SessionSupervisorLeaseRecord& record)
{
	TradingToolHostSessionBinding binding;
	binding.token = record.token;
	binding.peerUid = record.peerUid;
	binding.session.executionContext.agentId = record.agentId;
	binding.session.executionContext.sessionId = record.sessionId;
	binding.session.environment = "WATCH";
	binding.expiresAtMs = record.expiresAtMs;
	binding.leaseGeneration = record.leaseGeneration;
	return binding;
}

std::vector<SessionSupervisorLeaseRecord> WatchTransactionRecords(
	const SessionSupervisorLeaseRecord& record)
{
	std::vector<SessionSupervisorLeaseRecord> records;
	if (!record.predecessorToken.empty())
	{
		SessionSupervisorLeaseRecord predecessor = record;
		predecessor.token = record.predecessorToken;
		predecessor.leaseGeneration = record.predecessorGeneration;
		predecessor.predecessorToken.clear();
		predecessor.predecessorGeneration = 0;
		records.push_back(predecessor);
	}
	records.push_back(record);
	return records;
}

std::string HexEncode(const std::string& value)
{
	static const char digits[] = "0123456789abcdef";
	std::string encoded;
	encoded.reserve(value.size() * 2);
	for (std::size_t i = 0; i < value.size(); ++i)
	{
		const unsigned char byte = static_cast<unsigned char>(value[i]);
		encoded.push_back(digits[byte >> 4]);
		encoded.push_back(digits[byte & 15]);
	}
	return encoded;
}

bool Sha256(const std::string& value, std::string& prefixed)
{
	unsigned char digest[EVP_MAX_MD_SIZE];
	unsigned int length = 0;
	EVP_MD_CTX* context = EVP_MD_CTX_new();
	if (context == nullptr) return false;
	const bool ok =
		EVP_DigestInit_ex(context, EVP_sha256(), nullptr) == 1 &&
		EVP_DigestUpdate(context, value.data(), value.size()) == 1 &&
		EVP_DigestFinal_ex(context, digest, &length) == 1;
	EVP_MD_CTX_free(context);
	if (!ok || length != 32) return false;
	prefixed = "sha256:" + HexEncode(std::string(
		reinterpret_cast<const char*>(digest), length));
	return true;
}

bool PaperOwnerTokenSha256(
	const std::string& token, std::string& sha256)
{
	// The durable bearer is a canonical token line. SessionCtl removes the
	// newline while parsing it, so the external checkpoint identity is the
	// digest of token + "\\n", not the in-memory token alone.
	return Sha256(token + "\n", sha256);
}

bool CanonicalPaperOwnerSet(
	const std::vector<SessionSupervisorLeaseRecord>& records,
	std::string& canonical, std::string& sha256, std::string& reason)
{
	std::vector<std::string> owners;
	for (std::size_t i = 0; i < records.size(); ++i)
	{
		if (records[i].templateId != "paper") continue;
		std::string tokenSha256;
		if (!PaperOwnerTokenSha256(records[i].token, tokenSha256))
		{
			reason = "PAPER_FINALIZATION_OWNER_HASH_FAILED";
			return false;
		}
		if (!records[i].ownerTokenSha256.empty() &&
			records[i].ownerTokenSha256 != tokenSha256)
		{
			reason = "PAPER_FINALIZATION_OWNER_TOKEN_MISMATCH";
			return false;
		}
		owners.push_back(tokenSha256 + "\t" +
			std::to_string(records[i].leaseGeneration) + "\t" +
			HexEncode(records[i].ownerAccount) + "\t" +
			HexEncode(records[i].ownerExecutionDomain) + "\n");
	}
	if (owners.empty())
	{
		reason = "PAPER_FINALIZATION_OWNER_SET_REQUIRED";
		return false;
	}
	std::sort(owners.begin(), owners.end());
	canonical.clear();
	for (std::size_t i = 0; i < owners.size(); ++i)
		canonical += owners[i];
	if (!Sha256(canonical, sha256))
	{
		reason = "PAPER_FINALIZATION_OWNER_HASH_FAILED";
		return false;
	}
	reason.clear();
	return true;
}

bool SameFinalizationGroup(
	const SessionSupervisorLeaseRecord& record,
	const SessionSupervisorRequest& request)
{
	return record.recoveryId == request.recoveryId &&
		record.finalizationId == request.finalizationId &&
		record.expectedOwnerSetSha256 ==
			request.expectedOwnerSetSha256 &&
		record.expectedOwnerCount == request.expectedOwnerCount;
}

// A paper recovery owner audit is deliberately fail-closed, but the first
// audit after a process/reconnect boundary can be a transport-level
// observation rather than a terminal safety result.  The execution runtime
// responds to this observation by creating a new broker connection epoch and
// sealing a coherent active/terminal/risk snapshot.  RestoreLeases runs
// before the supervisor's normal ReapExpired loop, so a single observation
// here would otherwise abort startup before that state machine can run.
// Keep this allow-list intentionally narrow: no position/order/identity or
// uncertain-command result is retryable at this layer.
bool IsTransientPaperRecoveryAuditReason(const std::string& reason)
{
	return reason == "IB_RECOVERY_AUDIT_NEW_CONNECTION_EPOCH_REQUIRED" ||
		reason == "RECOVERY_OWNER_BROKER_BARRIER_INCOMPLETE" ||
		reason == "EXECUTION_EVENT_SERVICE_NOT_READY" ||
		reason == "EXECUTION_SERVICE_NOT_READY" ||
		reason == "EXECUTION_SERVICE_EPOCH_CHANGED" ||
		reason == "EXECUTION_SERVICE_CONNECT_FAILED" ||
		reason == "EXECUTION_SERVICE_READ_FAILED" ||
		reason == "EXECUTION_SERVICE_RESPONSE_READ_FAILED" ||
		reason == "connect failed" ||
		reason == "read failed" ||
		reason == "response read failed";
}

bool CommonPaperScope(
	const std::vector<SessionSupervisorLeaseRecord>& records,
	std::string& account, std::string& domain, std::string& reason)
{
	account.clear();
	domain.clear();
	for (std::size_t i = 0; i < records.size(); ++i)
	{
		if (records[i].templateId != "paper") continue;
		if (records[i].ownerAccount.empty() ||
			records[i].ownerExecutionDomain.empty())
		{
			reason = "PAPER_FINALIZATION_OWNER_SCOPE_REQUIRED";
			return false;
		}
		if (account.empty())
		{
			account = records[i].ownerAccount;
			domain = records[i].ownerExecutionDomain;
		}
		else if (account != records[i].ownerAccount ||
			domain != records[i].ownerExecutionDomain)
		{
			reason = "PAPER_FINALIZATION_OWNER_SCOPE_MISMATCH";
			return false;
		}
	}
	if (account.empty())
	{
		reason = "PAPER_FINALIZATION_OWNER_SET_REQUIRED";
		return false;
	}
	reason.clear();
	return true;
}

void CopyFinalizationAudit(
	const ExecutionOwnerAuditResult& audit,
	SessionSupervisorResult& result)
{
	result.ownerAuditAuthoritative = audit.ownerAuditAuthoritative;
	result.ownerAuditComplete = audit.ownerAuditComplete;
	result.ownerActiveOrderCount = audit.ownerActiveOrderCount;
	result.ownerUncertainCommandCount = audit.ownerUncertainCommandCount;
	result.OwnerAccount() = audit.ownerAccount;
	result.OwnerExecutionDomain() = audit.ownerExecutionDomain;
	result.ExecutionServiceEpoch() = audit.serviceEpoch;
	result.executionServiceFencingGeneration =
		audit.serviceFencingGeneration;
	result.brokerConnectionEpoch = audit.brokerConnectionEpoch;
	result.brokerActiveGeneration = audit.brokerActiveGeneration;
	result.brokerTerminalGeneration = audit.brokerTerminalGeneration;
	result.brokerRiskGeneration = audit.brokerRiskGeneration;
	result.brokerAccountGeneration = audit.brokerAccountGeneration;
	result.brokerPositionGeneration = audit.brokerPositionGeneration;
	result.brokerFxCashGeneration = audit.brokerFxCashGeneration;
	result.brokerExposureGeneration = audit.brokerExposureGeneration;
	result.brokerTerminalExposureGeneration =
		audit.brokerTerminalExposureGeneration;
	result.brokerRiskAbsorbedExposureGeneration =
		audit.brokerRiskAbsorbedExposureGeneration;
	result.brokerGlobalActiveOrderCount =
		audit.brokerGlobalActiveOrderCount;
	result.brokerPostFillRiskReconciliationPending =
		audit.brokerPostFillRiskReconciliationPending;
	result.brokerRecoveryAuditBarrierComplete =
		audit.brokerRecoveryAuditBarrierComplete;
	result.brokerRecoveryAuditNewConnectionEpochRequired =
		audit.brokerRecoveryAuditNewConnectionEpochRequired;
	result.BrokerPositionQuantity() = audit.brokerPositionQuantity;
	result.BrokerGrossAbsolutePosition() =
		audit.brokerGrossAbsolutePosition;
}

bool CanonicalSha256(const std::string& value)
{
	if (value.size() != 71 || value.compare(0, 7, "sha256:") != 0)
		return false;
	for (std::size_t i = 7; i < value.size(); ++i)
		if ((value[i] < '0' || value[i] > '9') &&
			(value[i] < 'a' || value[i] > 'f'))
			return false;
	return true;
}

bool ExactGlobalFinalizationAudit(
	const std::string& account,
	const std::string& domain,
	const ExecutionOwnerAuditResult& audit,
	std::string& reason)
{
	if (audit.status != ExecutionCommandStatus::Accepted ||
		!audit.ownerAuditAuthoritative || !audit.ownerAuditComplete ||
		audit.ownerAccount != account ||
		audit.ownerExecutionDomain != domain ||
		audit.serviceEpoch.empty() ||
		audit.serviceFencingGeneration == 0 ||
		audit.brokerConnectionEpoch == 0 ||
		audit.brokerActiveGeneration == 0 ||
		audit.brokerTerminalGeneration == 0 ||
		audit.brokerRiskGeneration == 0 ||
		audit.brokerAccountGeneration == 0 ||
		audit.brokerPositionGeneration == 0 ||
		audit.brokerFxCashGeneration == 0)
	{
		reason = audit.reasonCode.empty() ?
			"PAPER_FINALIZATION_AUDIT_INCOMPLETE" : audit.reasonCode;
		return false;
	}
	if (audit.ownerActiveOrderCount != 0 ||
		audit.ownerUncertainCommandCount != 0 ||
		audit.brokerGlobalActiveOrderCount != 0 ||
		audit.brokerPostFillRiskReconciliationPending ||
		!audit.brokerRecoveryAuditBarrierComplete ||
		audit.brokerRecoveryAuditNewConnectionEpochRequired ||
		audit.brokerPositionQuantity != "0" ||
		audit.brokerGrossAbsolutePosition != "0" ||
		audit.brokerTerminalExposureGeneration >
			audit.brokerRiskAbsorbedExposureGeneration ||
		audit.brokerRiskAbsorbedExposureGeneration !=
			audit.brokerExposureGeneration)
	{
		reason = audit.reasonCode.empty() ?
			"PAPER_FINALIZATION_GLOBAL_ZERO_PROOF_REQUIRED" :
			audit.reasonCode;
		return false;
	}
	reason.clear();
	return true;
}

bool SameCompositeFinalizationBarrier(
	const ExecutionOwnerAuditResult& left,
	const ExecutionOwnerAuditResult& right)
{
	return left.serviceEpoch == right.serviceEpoch &&
		left.serviceFencingGeneration == right.serviceFencingGeneration &&
		left.brokerConnectionEpoch == right.brokerConnectionEpoch &&
		left.brokerActiveGeneration == right.brokerActiveGeneration &&
		left.brokerTerminalGeneration == right.brokerTerminalGeneration &&
		left.brokerRiskGeneration == right.brokerRiskGeneration &&
		left.brokerAccountGeneration == right.brokerAccountGeneration &&
		left.brokerPositionGeneration == right.brokerPositionGeneration &&
		left.brokerFxCashGeneration == right.brokerFxCashGeneration &&
		left.brokerExposureGeneration == right.brokerExposureGeneration &&
		left.brokerTerminalExposureGeneration ==
			right.brokerTerminalExposureGeneration &&
		left.brokerRiskAbsorbedExposureGeneration ==
			right.brokerRiskAbsorbedExposureGeneration &&
		left.brokerGlobalActiveOrderCount ==
			right.brokerGlobalActiveOrderCount &&
		left.brokerPostFillRiskReconciliationPending ==
			right.brokerPostFillRiskReconciliationPending &&
		left.brokerRecoveryAuditBarrierComplete ==
			right.brokerRecoveryAuditBarrierComplete &&
		left.brokerRecoveryAuditNewConnectionEpochRequired ==
			right.brokerRecoveryAuditNewConnectionEpochRequired &&
		left.brokerPositionQuantity == right.brokerPositionQuantity &&
		left.brokerGrossAbsolutePosition ==
			right.brokerGrossAbsolutePosition;
}

std::string BuildPaperFinalizationReceipt(
	const std::string& recoveryId,
	const std::string& finalizationId,
	const std::string& ownerSetSha256,
	std::uint64_t ownerCount,
	const std::string& ownerSetCanonical,
	const ExecutionOwnerAuditResult& audit)
{
	std::ostringstream receipt;
	receipt << "schema=hepta.paper-session-finalization-receipt.v1\n"
		<< "version=1\n"
		<< "status=AUDIT_SEALED\n"
		<< "recovery_id=" << recoveryId << '\n'
		<< "finalization_id=" << finalizationId << '\n'
		<< "expected_owner_set_sha256=" << ownerSetSha256 << '\n'
		<< "expected_owner_count=" << ownerCount << '\n'
		<< "owner_set_canonical_hex=" <<
			HexEncode(ownerSetCanonical) << '\n'
		<< "owner_account=" << audit.ownerAccount << '\n'
		<< "owner_execution_domain=" <<
			audit.ownerExecutionDomain << '\n'
		<< "execution_service_epoch=" << audit.serviceEpoch << '\n'
		<< "execution_service_fencing_generation=" <<
			audit.serviceFencingGeneration << '\n'
		<< "broker_connection_epoch=" <<
			audit.brokerConnectionEpoch << '\n'
		<< "broker_active_generation=" <<
			audit.brokerActiveGeneration << '\n'
		<< "broker_terminal_generation=" <<
			audit.brokerTerminalGeneration << '\n'
		<< "broker_risk_generation=" << audit.brokerRiskGeneration << '\n'
		<< "broker_account_generation=" <<
			audit.brokerAccountGeneration << '\n'
		<< "broker_position_generation=" <<
			audit.brokerPositionGeneration << '\n'
		<< "broker_fx_cash_generation=" <<
			audit.brokerFxCashGeneration << '\n'
		<< "broker_exposure_generation=" <<
			audit.brokerExposureGeneration << '\n'
		<< "broker_terminal_exposure_generation=" <<
			audit.brokerTerminalExposureGeneration << '\n'
		<< "broker_risk_absorbed_exposure_generation=" <<
			audit.brokerRiskAbsorbedExposureGeneration << '\n'
		<< "broker_global_active_order_count=" <<
			audit.brokerGlobalActiveOrderCount << '\n'
		<< "owner_active_order_count=" <<
			audit.ownerActiveOrderCount << '\n'
		<< "owner_uncertain_command_count=" <<
			audit.ownerUncertainCommandCount << '\n'
		<< "broker_post_fill_risk_reconciliation_pending=0\n"
		<< "broker_recovery_audit_barrier_complete=1\n"
		<< "broker_recovery_audit_new_connection_epoch_required=0\n"
		<< "broker_position_quantity=0\n"
		<< "broker_gross_absolute_position=0\n"
		<< "paper_only=1\n"
		<< "live_authorized=0\n";
	return receipt.str();
}

bool ParseReceiptUnsigned(
	const std::string& value, std::uint64_t& parsed)
{
	if (value.empty()) return false;
	std::uint64_t number = 0;
	for (std::size_t i = 0; i < value.size(); ++i)
	{
		if (value[i] < '0' || value[i] > '9') return false;
		const std::uint64_t digit =
			static_cast<std::uint64_t>(value[i] - '0');
		if (number > (std::numeric_limits<std::uint64_t>::max() - digit) /
			10) return false;
		number = number * 10 + digit;
	}
	parsed = number;
	return true;
}

bool ParseCanonicalUnsigned(
	const std::string& value, std::uint64_t& parsed)
{
	if (value.empty() || (value.size() > 1 && value[0] == '0')) return false;
	return ParseReceiptUnsigned(value, parsed);
}

bool DecodeCanonicalHex(const std::string& encoded, std::string& decoded)
{
	if (encoded.empty() || encoded.size() > 8192 ||
		(encoded.size() % 2) != 0) return false;
	decoded.clear();
	decoded.reserve(encoded.size() / 2);
	for (std::size_t i = 0; i < encoded.size(); i += 2)
	{
		const char high = encoded[i];
		const char low = encoded[i + 1];
		const int highValue = high >= '0' && high <= '9' ? high - '0' :
			(high >= 'a' && high <= 'f' ? high - 'a' + 10 : -1);
		const int lowValue = low >= '0' && low <= '9' ? low - '0' :
			(low >= 'a' && low <= 'f' ? low - 'a' + 10 : -1);
		if (highValue < 0 || lowValue < 0) return false;
		decoded.push_back(static_cast<char>((highValue << 4) | lowValue));
	}
	return true;
}

bool TerminalEvidenceIdentifier(
	const std::string& value, std::size_t maximum)
{
	if (value.empty() || value.size() > maximum ||
		!((value[0] >= 'A' && value[0] <= 'Z') ||
		  (value[0] >= 'a' && value[0] <= 'z') ||
		  (value[0] >= '0' && value[0] <= '9'))) return false;
	for (std::size_t i = 1; i < value.size(); ++i)
		if (!((value[i] >= 'A' && value[i] <= 'Z') ||
			  (value[i] >= 'a' && value[i] <= 'z') ||
			  (value[i] >= '0' && value[i] <= '9') ||
			  value[i] == '.' || value[i] == '_' || value[i] == ':' ||
			  value[i] == '-')) return false;
	return true;
}

bool TerminalEvidenceText(
	const std::string& value, std::size_t maximum)
{
	if (value.empty() || value.size() > maximum) return false;
	for (std::size_t i = 0; i < value.size(); ++i)
	{
		const unsigned char byte = static_cast<unsigned char>(value[i]);
		if (byte < 0x21 || byte > 0x7e || byte == '=') return false;
	}
	return true;
}

bool TerminalEvidenceBootId(const std::string& value)
{
	if (value.size() != 36 ||
		value == "00000000-0000-0000-0000-000000000000") return false;
	for (std::size_t i = 0; i < value.size(); ++i)
	{
		if (i == 8 || i == 13 || i == 18 || i == 23)
		{
			if (value[i] != '-') return false;
		}
		else if (!((value[i] >= '0' && value[i] <= '9') ||
			(value[i] >= 'a' && value[i] <= 'f'))) return false;
	}
	return true;
}

const char* const kTerminalEvidenceKeys[] = {
	"schema", "version", "status", "terminal_proof_kind",
	"recovery_id", "finalization_id", "campaign_id", "cycle_id",
	"expected_owner_set_sha256", "expected_owner_count",
	"owner_set_canonical_hex", "preliminary_finalization_receipt_sha256",
	"owner_agent_id", "owner_session_id", "owner_account",
	"owner_execution_domain", "account_id_sha256",
	"execution_service_epoch", "execution_service_fencing_generation",
	"recovery_ingress_fence", "terminalization_generation",
	"terminalizing_latch_sha256", "terminal_external_halt_latch_sha256",
	"transport_cutoff_receipt_file_sha256",
	"transport_cutoff_receipt_body_sha256",
	"post_cutoff_terminal_witness_file_sha256",
	"post_cutoff_terminal_witness_body_sha256",
	"provider_trust_policy_file_sha256",
	"provider_trust_policy_body_sha256", "provider_id",
	"provider_capability", "signed_account_payload_sha256",
	"signed_account_signature_sha256", "host_boot_id",
	"egress_publisher_pid", "egress_publisher_start_ticks",
	"egress_policy_generation", "egress_policy_sha256",
	"query_started_after_challenge", "observed_after_cutoff",
	"snapshot_consistency", "causal_watermark_dominates_cutoff",
	"causal_watermark_dominates_all_mutations", "account_queries_complete",
	"active_orders_complete", "completed_orders_complete",
	"executions_complete", "positions_complete", "cash_fx_complete",
	"risk_complete", "known_mutation_command_set_sha256",
	"known_mutation_command_count", "known_correlation_set_sha256",
	"known_correlation_count", "all_known_mutation_commands_settled",
	"settled_mutation_command_count", "unknown_mutation_command_count",
	"unresolved_mutation_command_count", "unknown_active_order_count",
	"active_order_count", "position_count", "nonzero_cash_fx_count",
	"gross_absolute_position", "gross_fx_exposure", "gross_risk",
	"mutation_connector_count", "broker_socket_count",
	"broker_process_count", "broker_credential_count",
	"execution_service_inactive", "paper_units_inactive",
	"execution_mutation_gate_closed", "broker_transport_connected",
	"broker_reconnect_permitted", "read_only_authority",
	"mutation_attempted", "paper_authorized", "live_authorized",
	"mutation_authorized", "direct_broker_access",
	"order_submission_authorized", "order_authorized", "paper_only",
	"authority_granted", "terminal_external_halt_latch_durable",
	"terminal_witness_durable", "current_host_boundary_verified",
	"evidence_body_sha256"
};

bool ParseTerminalEvidence(
	const std::string& evidence,
	std::map<std::string, std::string>& fields,
	std::string& body)
{
	if (evidence.empty() || evidence.size() > 12288 ||
		evidence.back() != '\n') return false;
	std::istringstream input(evidence);
	std::string line;
	if (!std::getline(input, line) || line != "HPE1") return false;
	fields.clear();
	std::ostringstream prefix;
	prefix << "HPE1\n";
	for (std::size_t i = 0;
		i < sizeof(kTerminalEvidenceKeys) /
			sizeof(kTerminalEvidenceKeys[0]); ++i)
	{
		if (!std::getline(input, line)) return false;
		const std::string expected =
			std::string(kTerminalEvidenceKeys[i]) + "=";
		if (line.compare(0, expected.size(), expected) != 0 ||
			line.size() == expected.size()) return false;
		const std::string value = line.substr(expected.size());
		if (value.find('=') != std::string::npos ||
			!fields.insert(std::make_pair(
				kTerminalEvidenceKeys[i], value)).second) return false;
		if (i + 1 < sizeof(kTerminalEvidenceKeys) /
			sizeof(kTerminalEvidenceKeys[0])) prefix << line << '\n';
	}
	if (std::getline(input, line)) return false;
	body = prefix.str();
	return true;
}

bool ValidateTerminalOwnerSet(
	const std::string& encoded, const std::string& expectedSha256,
	std::uint64_t expectedCount, const std::string& account,
	const std::string& domain)
{
	std::string canonical;
	std::string digest;
	if (!DecodeCanonicalHex(encoded, canonical) || canonical.empty() ||
		canonical.back() != '\n' || !Sha256(canonical, digest) ||
		digest != expectedSha256) return false;
	std::istringstream input(canonical);
	std::string line;
	std::string previous;
	std::uint64_t count = 0;
	while (std::getline(input, line))
	{
		if (line.empty() || (!previous.empty() && line <= previous))
			return false;
		previous = line;
		std::string values[4];
		std::size_t offset = 0;
		for (int field = 0; field < 4; ++field)
		{
			const std::size_t separator = line.find('\t', offset);
			if ((field < 3 && separator == std::string::npos) ||
				(field == 3 && separator != std::string::npos)) return false;
			values[field] = line.substr(offset,
				separator == std::string::npos ? std::string::npos :
				separator - offset);
			offset = separator == std::string::npos ? line.size() :
				separator + 1;
		}
		std::uint64_t generation = 0;
		std::string decodedAccount;
		std::string decodedDomain;
		if (!CanonicalSha256(values[0]) ||
			!ParseCanonicalUnsigned(values[1], generation) || generation == 0 ||
			!DecodeCanonicalHex(values[2], decodedAccount) ||
			!DecodeCanonicalHex(values[3], decodedDomain) ||
			decodedAccount != account || decodedDomain != domain) return false;
		++count;
	}
	return count == expectedCount;
}

bool ValidateTerminalEvidence(
	const SessionSupervisorRequest& request,
	const SessionSupervisorLeaseRecord& terminalOwner,
	const SessionSupervisorResult& preliminary,
	const std::string& account, const std::string& domain,
	std::map<std::string, std::string>& fields,
	std::string& reason)
{
	std::string body;
	std::string fileSha256;
	std::string bodySha256;
	if (!ParseTerminalEvidence(request.terminalEvidence, fields, body) ||
		!Sha256(request.terminalEvidence, fileSha256) ||
		fileSha256 != request.terminalEvidenceSha256 ||
		!Sha256(body, bodySha256) ||
		fields["evidence_body_sha256"] != bodySha256)
	{
		reason = "PAPER_TERMINAL_EVIDENCE_INVALID";
		return false;
	}
	const char* const identifiers[] = {
		"recovery_id", "finalization_id", "campaign_id", "cycle_id",
		"owner_agent_id", "owner_session_id", "owner_execution_domain",
		"execution_service_epoch", "provider_id"};
	for (std::size_t i = 0;
		i < sizeof(identifiers) / sizeof(identifiers[0]); ++i)
		if (!TerminalEvidenceIdentifier(fields[identifiers[i]]))
		{
			reason = "PAPER_TERMINAL_EVIDENCE_INVALID";
			return false;
		}
	const char* const digests[] = {
		"expected_owner_set_sha256",
		"preliminary_finalization_receipt_sha256", "account_id_sha256",
		"terminalizing_latch_sha256",
		"terminal_external_halt_latch_sha256",
		"transport_cutoff_receipt_file_sha256",
		"transport_cutoff_receipt_body_sha256",
		"post_cutoff_terminal_witness_file_sha256",
		"post_cutoff_terminal_witness_body_sha256",
		"provider_trust_policy_file_sha256",
		"provider_trust_policy_body_sha256",
		"signed_account_payload_sha256", "signed_account_signature_sha256",
		"egress_policy_sha256", "known_mutation_command_set_sha256",
		"known_correlation_set_sha256", "evidence_body_sha256"};
	for (std::size_t i = 0; i < sizeof(digests) / sizeof(digests[0]); ++i)
		if (!CanonicalSha256(fields[digests[i]]) ||
			fields[digests[i]] ==
				"sha256:0000000000000000000000000000000000000000000000000000000000000000")
		{
			reason = "PAPER_TERMINAL_EVIDENCE_INVALID";
			return false;
		}
	std::uint64_t ownerCount = 0;
	std::uint64_t serviceFence = 0;
	std::uint64_t recoveryFence = 0;
	std::uint64_t terminalGeneration = 0;
	std::uint64_t egressPublisherPid = 0;
	std::uint64_t egressPublisherStartTicks = 0;
	std::uint64_t egressGeneration = 0;
	std::uint64_t knownMutationCount = 0;
	std::uint64_t knownCorrelationCount = 0;
	std::uint64_t settledMutationCount = 0;
	if (!ParseCanonicalUnsigned(fields["expected_owner_count"], ownerCount) ||
		ownerCount == 0 || ownerCount > 4096 ||
		!ParseCanonicalUnsigned(
			fields["execution_service_fencing_generation"], serviceFence) ||
		serviceFence == 0 ||
		!ParseCanonicalUnsigned(fields["recovery_ingress_fence"],
			recoveryFence) || recoveryFence == 0 ||
		!ParseCanonicalUnsigned(fields["terminalization_generation"],
			terminalGeneration) || terminalGeneration != 1 ||
		!ParseCanonicalUnsigned(fields["egress_publisher_pid"],
			egressPublisherPid) || egressPublisherPid == 0 ||
		!ParseCanonicalUnsigned(fields["egress_publisher_start_ticks"],
			egressPublisherStartTicks) || egressPublisherStartTicks == 0 ||
		!ParseCanonicalUnsigned(fields["egress_policy_generation"],
			egressGeneration) || egressGeneration == 0 ||
		!ParseCanonicalUnsigned(fields["known_mutation_command_count"],
			knownMutationCount) ||
		!ParseCanonicalUnsigned(fields["known_correlation_count"],
			knownCorrelationCount) ||
		!ParseCanonicalUnsigned(fields["settled_mutation_command_count"],
			settledMutationCount) || settledMutationCount != knownMutationCount)
	{
		reason = "PAPER_TERMINAL_EVIDENCE_INVALID";
		return false;
	}
	const char* const zeros[] = {
		"unknown_mutation_command_count", "unresolved_mutation_command_count",
		"unknown_active_order_count", "active_order_count", "position_count",
		"nonzero_cash_fx_count", "gross_absolute_position",
		"gross_fx_exposure", "gross_risk", "mutation_connector_count",
		"broker_socket_count", "broker_process_count",
		"broker_credential_count"};
	for (std::size_t i = 0; i < sizeof(zeros) / sizeof(zeros[0]); ++i)
		if (fields[zeros[i]] != "0")
		{
			reason = "PAPER_TERMINAL_EVIDENCE_NOT_FLAT";
			return false;
		}
	const char* const truths[] = {
		"query_started_after_challenge", "observed_after_cutoff",
		"causal_watermark_dominates_cutoff",
		"causal_watermark_dominates_all_mutations", "account_queries_complete",
		"active_orders_complete", "completed_orders_complete",
		"executions_complete", "positions_complete", "cash_fx_complete",
		"risk_complete", "all_known_mutation_commands_settled",
		"execution_service_inactive", "paper_units_inactive",
		"execution_mutation_gate_closed", "read_only_authority", "paper_only",
		"terminal_external_halt_latch_durable", "terminal_witness_durable",
		"current_host_boundary_verified"};
	for (std::size_t i = 0; i < sizeof(truths) / sizeof(truths[0]); ++i)
		if (fields[truths[i]] != "1")
		{
			reason = "PAPER_TERMINAL_EVIDENCE_INCOMPLETE";
			return false;
		}
	const char* const falses[] = {
		"broker_transport_connected", "broker_reconnect_permitted",
		"mutation_attempted", "paper_authorized", "live_authorized",
		"mutation_authorized", "direct_broker_access",
		"order_submission_authorized", "order_authorized",
		"authority_granted"};
	for (std::size_t i = 0; i < sizeof(falses) / sizeof(falses[0]); ++i)
		if (fields[falses[i]] != "0")
		{
			reason = "PAPER_TERMINAL_EVIDENCE_AUTHORITY_ACTIVE";
			return false;
		}
	std::string accountIdSha256;
	if (!Sha256(account, accountIdSha256) ||
		fields["account_id_sha256"] != accountIdSha256)
	{
		reason = "PAPER_TERMINAL_EVIDENCE_BINDING_MISMATCH";
		return false;
	}
	if (fields["schema"] != "hepta.paper-terminal-witness-evidence.v1" ||
		fields["version"] != "1" ||
		fields["status"] !=
			"CURRENT_POST_CUTOFF_TERMINAL_WITNESS_VERIFIED" ||
		fields["terminal_proof_kind"] !=
			"POST_CUTOFF_SIGNED_ACCOUNT_WITNESS_V1" ||
		fields["provider_capability"] !=
			"ACCOUNT_WIDE_ATOMIC_OR_CAUSAL_POST_CUTOFF_READ_ONLY_V1" ||
		(fields["snapshot_consistency"] != "ATOMIC_ACCOUNT" &&
		 fields["snapshot_consistency"] != "CAUSAL_WATERMARK") ||
		!TerminalEvidenceText(fields["owner_account"]) ||
		!TerminalEvidenceBootId(fields["host_boot_id"]) ||
		fields["recovery_id"] != request.recoveryId ||
		fields["finalization_id"] != request.finalizationId ||
		fields["expected_owner_set_sha256"] !=
			request.expectedOwnerSetSha256 ||
		ownerCount != request.expectedOwnerCount ||
		fields["preliminary_finalization_receipt_sha256"] !=
			request.receiptSha256 ||
		fields["owner_agent_id"] != terminalOwner.agentId ||
		fields["owner_session_id"] != terminalOwner.sessionId ||
		fields["owner_account"] != account ||
		fields["owner_execution_domain"] != domain ||
		fields["execution_service_epoch"] !=
			preliminary.ExecutionServiceEpoch() ||
		serviceFence != preliminary.executionServiceFencingGeneration ||
		recoveryFence != terminalOwner.leaseGeneration ||
		recoveryFence != request.expectedGeneration ||
		!ValidateTerminalOwnerSet(fields["owner_set_canonical_hex"],
			request.expectedOwnerSetSha256, ownerCount, account, domain))
	{
		reason = "PAPER_TERMINAL_EVIDENCE_BINDING_MISMATCH";
		return false;
	}
	reason.clear();
	return true;
}

std::string BuildPaperTerminalWitnessAckReceipt(
	const std::map<std::string, std::string>& evidence,
	const std::string& evidenceFileSha256)
{
	std::ostringstream receipt;
	receipt << "schema=hepta.paper-session-terminal-ack-receipt.v3\n"
		<< "version=3\n"
		<< "status=TERMINAL_ACKED\n";
	for (std::size_t i = 3;
		i + 1 < sizeof(kTerminalEvidenceKeys) /
			sizeof(kTerminalEvidenceKeys[0]); ++i)
		receipt << kTerminalEvidenceKeys[i] << '='
			<< evidence.at(kTerminalEvidenceKeys[i]) << '\n';
	receipt << "terminal_evidence_file_sha256="
		<< evidenceFileSha256 << '\n'
		<< "terminal_evidence_body_sha256="
		<< evidence.at("evidence_body_sha256") << '\n';
	return receipt.str();
}

void CopyTerminalEvidenceResult(
	const std::map<std::string, std::string>& evidence,
	const std::string& evidenceFileSha256, bool replay,
	SessionSupervisorResult& result)
{
	result.OwnerAccount() = evidence.at("owner_account");
	result.OwnerExecutionDomain() = evidence.at("owner_execution_domain");
	result.ExecutionServiceEpoch() = evidence.at("execution_service_epoch");
	result.TerminalizationServiceEpoch() =
		evidence.at("execution_service_epoch");
	ParseCanonicalUnsigned(evidence.at("execution_service_fencing_generation"),
		result.executionServiceFencingGeneration);
	result.terminalizationServiceFencingGeneration =
		result.executionServiceFencingGeneration;
	ParseCanonicalUnsigned(evidence.at("terminalization_generation"),
		result.terminalizationGeneration);
	result.TerminalLatchSha256() = evidence.at("terminalizing_latch_sha256");
	result.TerminalExternalLatchSha256() =
		evidence.at("terminal_external_halt_latch_sha256");
	result.TerminalProofKind() = evidence.at("terminal_proof_kind");
	result.TransportCutoffReceiptFileSha256() =
		evidence.at("transport_cutoff_receipt_file_sha256");
	result.TransportCutoffReceiptBodySha256() =
		evidence.at("transport_cutoff_receipt_body_sha256");
	result.PostCutoffTerminalWitnessFileSha256() =
		evidence.at("post_cutoff_terminal_witness_file_sha256");
	result.PostCutoffTerminalWitnessBodySha256() =
		evidence.at("post_cutoff_terminal_witness_body_sha256");
	result.TerminalEvidenceSha256() = evidenceFileSha256;
	result.TerminalEvidenceBodySha256() =
		evidence.at("evidence_body_sha256");
	result.EgressPolicySha256() = evidence.at("egress_policy_sha256");
	ParseCanonicalUnsigned(evidence.at("egress_publisher_pid"),
		result.egressPublisherPid);
	ParseCanonicalUnsigned(evidence.at("egress_publisher_start_ticks"),
		result.egressPublisherStartTicks);
	result.ProviderTrustPolicyBodySha256() =
		evidence.at("provider_trust_policy_body_sha256");
	result.SignedAccountSignatureSha256() =
		evidence.at("signed_account_signature_sha256");
	result.ownerAuditAuthoritative = true;
	result.ownerAuditComplete = true;
	result.ownerActiveOrderCount = 0;
	result.ownerUncertainCommandCount = 0;
	result.BrokerPositionQuantity() = "0";
	result.BrokerGrossAbsolutePosition() = "0";
	result.terminalMutationGateClosed = true;
	result.terminalBrokerTransportConnected = false;
	result.terminalBrokerEventIngressHalted = true;
	// External signed account evidence intentionally never claims that the
	// opaque vendor raw/callback queue was drained locally.
	result.terminalBrokerCallbackQueueDrained = false;
	result.terminalBrokerCallbacksInFlight = 0;
	result.terminalBrokerReconnectPermitted = false;
	result.terminalLatchDurable = true;
	result.terminalRuntimeLatchLoaded = false;
	result.terminalRuntimeVerified = false;
	result.terminalExternalLatchLoaded = true;
	result.terminalCurrentEvidenceVerified = true;
	result.terminalReplay = replay;
}

bool PopulateAuditFromReceipt(
	const std::string& receipt,
	SessionSupervisorResult& result,
	std::string& reason)
{
	std::map<std::string, std::string> fields;
	std::istringstream input(receipt);
	std::string line;
	while (std::getline(input, line))
	{
		if (line.empty()) continue;
		const std::size_t separator = line.find('=');
		if (separator == std::string::npos || separator == 0 ||
			!fields.insert(std::make_pair(
				line.substr(0, separator),
				line.substr(separator + 1))).second)
		{
			reason = "PAPER_FINALIZATION_RECEIPT_INVALID";
			return false;
		}
	}
	const char* required[] = {
		"schema", "version", "status", "recovery_id",
		"finalization_id", "expected_owner_set_sha256",
		"expected_owner_count", "owner_set_canonical_hex",
		"owner_account", "owner_execution_domain",
		"execution_service_epoch",
		"execution_service_fencing_generation",
		"broker_connection_epoch", "broker_active_generation",
		"broker_terminal_generation", "broker_risk_generation",
		"broker_account_generation", "broker_position_generation",
		"broker_fx_cash_generation", "broker_exposure_generation",
		"broker_terminal_exposure_generation",
		"broker_risk_absorbed_exposure_generation",
		"broker_global_active_order_count", "owner_active_order_count",
		"owner_uncertain_command_count",
		"broker_post_fill_risk_reconciliation_pending",
		"broker_recovery_audit_barrier_complete",
		"broker_recovery_audit_new_connection_epoch_required",
		"broker_position_quantity", "broker_gross_absolute_position",
		"paper_only", "live_authorized"};
	if (fields.size() != sizeof(required) / sizeof(required[0]))
	{
		reason = "PAPER_FINALIZATION_RECEIPT_INVALID";
		return false;
	}
	for (std::size_t i = 0;
		i < sizeof(required) / sizeof(required[0]); ++i)
		if (fields.find(required[i]) == fields.end())
		{
			reason = "PAPER_FINALIZATION_RECEIPT_INVALID";
			return false;
		}
	if (fields["schema"] !=
			"hepta.paper-session-finalization-receipt.v1" ||
		fields["version"] != "1" ||
		fields["status"] != "AUDIT_SEALED" ||
		fields["broker_post_fill_risk_reconciliation_pending"] != "0" ||
		fields["broker_recovery_audit_barrier_complete"] != "1" ||
		fields["broker_recovery_audit_new_connection_epoch_required"] != "0" ||
		fields["broker_position_quantity"] != "0" ||
		fields["broker_gross_absolute_position"] != "0" ||
		fields["paper_only"] != "1" || fields["live_authorized"] != "0")
	{
		reason = "PAPER_FINALIZATION_RECEIPT_INVALID";
		return false;
	}
	std::uint64_t ignoredOwnerCount = 0;
	std::uint64_t* values[] = {
		&ignoredOwnerCount,
		&result.executionServiceFencingGeneration,
		&result.brokerConnectionEpoch,
		&result.brokerActiveGeneration,
		&result.brokerTerminalGeneration,
		&result.brokerRiskGeneration,
		&result.brokerAccountGeneration,
		&result.brokerPositionGeneration,
		&result.brokerFxCashGeneration,
		&result.brokerExposureGeneration,
		&result.brokerTerminalExposureGeneration,
		&result.brokerRiskAbsorbedExposureGeneration,
		&result.brokerGlobalActiveOrderCount,
		&result.ownerActiveOrderCount,
		&result.ownerUncertainCommandCount};
	const char* numeric[] = {
		"expected_owner_count", "execution_service_fencing_generation",
		"broker_connection_epoch", "broker_active_generation",
		"broker_terminal_generation", "broker_risk_generation",
		"broker_account_generation", "broker_position_generation",
		"broker_fx_cash_generation", "broker_exposure_generation",
		"broker_terminal_exposure_generation",
		"broker_risk_absorbed_exposure_generation",
		"broker_global_active_order_count", "owner_active_order_count",
		"owner_uncertain_command_count"};
	for (std::size_t i = 0;
		i < sizeof(numeric) / sizeof(numeric[0]); ++i)
		if (!ParseReceiptUnsigned(fields[numeric[i]], *values[i]))
		{
			reason = "PAPER_FINALIZATION_RECEIPT_INVALID";
			return false;
		}
	result.OwnerAccount() = fields["owner_account"];
	result.OwnerExecutionDomain() = fields["owner_execution_domain"];
	result.ExecutionServiceEpoch() = fields["execution_service_epoch"];
	result.ownerAuditAuthoritative = true;
	result.ownerAuditComplete = true;
	result.brokerPostFillRiskReconciliationPending = false;
	result.brokerRecoveryAuditBarrierComplete = true;
	result.brokerRecoveryAuditNewConnectionEpochRequired = false;
	result.BrokerPositionQuantity() = "0";
	result.BrokerGrossAbsolutePosition() = "0";
	reason.clear();
	return true;
}
}
