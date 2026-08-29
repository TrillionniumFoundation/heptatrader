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

namespace
{
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
	const ExecutionControlResult& audit,
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
	const ExecutionControlResult& audit,
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
	const ExecutionControlResult& left,
	const ExecutionControlResult& right)
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
	const ExecutionControlResult& audit)
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
	const std::string& value, std::size_t maximum = 128)
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
	const std::string& value, std::size_t maximum = 128)
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
			knownMutationCount) || knownMutationCount > 4096 ||
		!ParseCanonicalUnsigned(fields["known_correlation_count"],
			knownCorrelationCount) || knownCorrelationCount > 4096 ||
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

bool UnixSessionSupervisorServer::EnterPaperRecovery(
	SessionSupervisorLeaseRecord& record,
	std::uint64_t nowMs,
	const std::string& targetCommandId,
	ExecutionControlResult& commandResult,
	ExecutionControlResult& ownerAudit,
	std::string& reason)
{
	if (m_leaseStore == nullptr || record.templateId != "paper" ||
		record.paperFinalizationState !=
			SessionSupervisorPaperFinalizationState::None ||
		targetCommandId.size() > 128 ||
		nowMs > std::numeric_limits<std::uint64_t>::max() -
			m_maxSessionTtlMs)
	{
		reason = "SESSION_PAPER_RECOVERY_RECORD_INVALID";
		return false;
	}
	SessionSupervisorLeaseRecord recovery = record;
	std::string durableCurrentToken = record.token;
	recovery.expiresAtMs = nowMs + m_maxSessionTtlMs;
	recovery.fencePending = false;
	recovery.fenceComplete = false;
	recovery.fenceReason.clear();
	recovery.recoveryOnly = true;
	if (!targetCommandId.empty() && recovery.recoveryCommandId.empty())
		recovery.recoveryCommandId = targetCommandId;
	TradingToolHostSessionBinding local;
	bool localExists = m_controlPlane.m_host.GetSession(recovery.token, local);
	if (localExists &&
		(local.session.executionContext.agentId != recovery.agentId ||
		 local.session.executionContext.sessionId != recovery.sessionId ||
		 local.session.executionContext.account != recovery.ownerAccount ||
		 local.executionDomain != recovery.ownerExecutionDomain))
	{
		reason = "SESSION_RECOVERY_FENCE_BINDING_MISMATCH";
		return false;
	}
	if (localExists && local.leaseGeneration != recovery.leaseGeneration)
	{
		recovery.leaseGeneration = local.leaseGeneration;
		recovery.predecessorToken.clear();
		recovery.predecessorGeneration = 0;
	}
	else if (!localExists && !recovery.predecessorToken.empty() &&
		m_controlPlane.m_host.GetSession(recovery.predecessorToken, local))
	{
		if (local.session.executionContext.agentId != recovery.agentId ||
			local.session.executionContext.sessionId != recovery.sessionId ||
			local.session.executionContext.account != recovery.ownerAccount ||
			local.executionDomain != recovery.ownerExecutionDomain)
		{
			reason = "SESSION_RECOVERY_FENCE_BINDING_MISMATCH";
			return false;
		}
		recovery.token = recovery.predecessorToken;
		recovery.leaseGeneration = recovery.predecessorGeneration;
		recovery.predecessorToken.clear();
		recovery.predecessorGeneration = 0;
		localExists = true;
	}
	if (!localExists)
	{
		if (!m_leaseStore->Replace(
				durableCurrentToken, recovery, reason))
			return false;
		record = recovery;
		durableCurrentToken = record.token;
		TradingToolHostSessionBinding binding;
		if (!ResolveLeaseBinding(record, nowMs, binding, reason) ||
			!m_controlPlane.Provision(record.issuer, binding, reason))
			return false;
	}
	else
	{
		// Keep this adjusted identity in memory until the host commits the
		// durable recovery fence under its mutation-dispatch lock.  This
		// avoids publishing recovery-only state while the local bearer is
		// still entry-enabled.
		record = recovery;
	}
	return m_controlPlane.EnterRecoveryOnlyAndQuery(
		record.issuer, record.token, record.leaseGeneration,
		targetCommandId, *m_leaseStore, record, commandResult, reason,
		&ownerAudit, recovery.expiresAtMs, durableCurrentToken);
}

bool UnixSessionSupervisorServer::FinalizePaperRecovery(
	const SessionSupervisorLeaseRecord& record,
	ExecutionControlResult& ownerAudit,
	std::string& reason)
{
	if (m_leaseStore == nullptr || record.templateId != "paper" ||
		!record.recoveryOnly || record.paperFinalizationRequired ||
		record.paperFinalizationState !=
			SessionSupervisorPaperFinalizationState::None)
	{
		reason = record.paperFinalizationRequired ?
			"PAPER_FINALIZATION_OPERATION_REQUIRED" :
			"SESSION_PAPER_RECOVERY_RECORD_INVALID";
		return false;
	}
	if (!m_controlPlane.FinalizeRecoveryOnlyOwner(
			record.issuer, record.token, record.leaseGeneration,
			record, ownerAudit, reason))
		return false;
	return m_leaseStore->Remove(record.token, reason);
}

bool UnixSessionSupervisorServer::HandlePaperFinalize(
	const SessionSupervisorRequest& request,
	SessionSupervisorResult& result)
{
	result.leaseGeneration = request.expectedGeneration;
	result.paperFinalizationRequired = true;
	result.PaperFinalizationState() = "NONE";
	result.RecoveryId() = request.recoveryId;
	result.FinalizationId() = request.finalizationId;
	result.ExpectedOwnerSetSha256() = request.expectedOwnerSetSha256;
	result.expectedOwnerCount = request.expectedOwnerCount;
	result.OwnerAccount() = "unavailable";
	result.OwnerExecutionDomain() = "unavailable";
	result.ExecutionServiceEpoch() = "unavailable";
	if (!PaperOwnerTokenSha256(
			request.token, result.OwnerTokenSha256()))
	{
		result.ReasonCode() = "PAPER_FINALIZATION_OWNER_HASH_FAILED";
		return false;
	}
	if (m_leaseStore == nullptr)
	{
		result.ReasonCode() = "SUPERVISOR_DURABLE_LEASE_STORE_REQUIRED";
		return false;
	}
	SessionSupervisorLeaseRecord record;
	if (!m_leaseStore->Get(request.token, record))
	{
		result.ReasonCode() = "SESSION_LEASE_NOT_FOUND";
		return false;
	}
	result.PaperFinalizationState() =
		SessionSupervisorPaperFinalizationStateName(
			record.paperFinalizationState);
	if (record.leaseGeneration != request.expectedGeneration)
	{
		result.ReasonCode() = "SESSION_LEASE_GENERATION_MISMATCH";
		return false;
	}
	if (record.templateId != "paper" || !record.recoveryOnly ||
		record.fencePending || record.fenceComplete)
	{
		result.ReasonCode() = "PAPER_FINALIZATION_RECOVERY_ONLY_REQUIRED";
		return false;
	}
	if (!record.paperFinalizationRequired)
	{
		result.ReasonCode() =
			"PAPER_FINALIZATION_TRANSITION_REQUIRED";
		return false;
	}
	const std::vector<SessionSupervisorLeaseRecord> initialRecords =
		m_leaseStore->List();
	std::string ownerSetCanonical;
	std::string actualOwnerSetSha256;
	std::string account;
	std::string domain;
	if (initialRecords.size() < request.expectedOwnerCount ||
		!CanonicalPaperOwnerSet(initialRecords, ownerSetCanonical,
			actualOwnerSetSha256, result.ReasonCode()) ||
		actualOwnerSetSha256 != request.expectedOwnerSetSha256)
	{
		if (result.ReasonCode().empty())
			result.ReasonCode() =
				"PAPER_FINALIZATION_OWNER_SET_MISMATCH";
		return false;
	}
	std::uint64_t actualOwnerCount = 0;
	for (std::size_t i = 0; i < initialRecords.size(); ++i)
		if (initialRecords[i].templateId == "paper") ++actualOwnerCount;
	if (actualOwnerCount != request.expectedOwnerCount ||
		!CommonPaperScope(
			initialRecords, account, domain, result.ReasonCode()))
	{
		if (result.ReasonCode().empty())
			result.ReasonCode() =
				"PAPER_FINALIZATION_OWNER_SET_MISMATCH";
		return false;
	}
	for (std::size_t i = 0; i < initialRecords.size(); ++i)
	{
		if (initialRecords[i].templateId != "paper") continue;
		if (!initialRecords[i].paperFinalizationRequired)
		{
			result.ReasonCode() =
				"PAPER_FINALIZATION_TRANSITION_REQUIRED";
			return false;
		}
		if (
			initialRecords[i].paperFinalizationState ==
				SessionSupervisorPaperFinalizationState::None)
			continue;
		if (!SameFinalizationGroup(initialRecords[i], request))
		{
			result.ReasonCode() =
				"PAPER_FINALIZATION_GROUP_MISMATCH";
			return false;
		}
	}
	if (record.paperFinalizationState !=
			SessionSupervisorPaperFinalizationState::None &&
		!SameFinalizationGroup(record, request))
	{
		result.ReasonCode() = "PAPER_FINALIZATION_GROUP_MISMATCH";
		return false;
	}
	if (record.paperFinalizationState ==
		SessionSupervisorPaperFinalizationState::AuditSealed)
	{
		result.FinalizationReceiptSha256() =
			record.finalizationReceiptSha256;
		result.PreliminaryFinalizationReceiptSha256() =
			record.finalizationReceiptSha256;
		result.FinalizationReceipt() = record.finalizationReceipt;
		if (!PopulateAuditFromReceipt(
				record.finalizationReceipt, result, result.ReasonCode()))
			return false;
		result.accepted = true;
		result.ReasonCode() = "PAPER_FINALIZATION_AUDIT_SEALED";
		return true;
	}
	if (record.paperFinalizationState ==
		SessionSupervisorPaperFinalizationState::None)
	{
		if (m_crashPointHook &&
			m_crashPointHook(
				"before_paper_finalization_pending_commit"))
		{
			result.ReasonCode() =
				"SUPERVISOR_FAULT_INJECTED:before_paper_finalization_pending_commit";
			return false;
		}
		SessionSupervisorLeaseRecord pending = record;
		pending.paperFinalizationState =
			SessionSupervisorPaperFinalizationState::FencePending;
		pending.recoveryId = request.recoveryId;
		pending.finalizationId = request.finalizationId;
		pending.expectedOwnerSetSha256 =
			request.expectedOwnerSetSha256;
		pending.expectedOwnerCount = request.expectedOwnerCount;
		pending.ownerTokenSha256 = result.OwnerTokenSha256();
		if (!m_leaseStore->AdvancePaperFinalization(
				record.token,
				SessionSupervisorPaperFinalizationState::None,
				pending, result.ReasonCode()))
			return false;
		record = pending;
		result.PaperFinalizationState() = "FENCE_PENDING";
		if (m_crashPointHook &&
			m_crashPointHook(
				"after_paper_finalization_pending_commit"))
		{
			result.ReasonCode() =
				"SUPERVISOR_FAULT_INJECTED:after_paper_finalization_pending_commit";
			return false;
		}
	}
	if (record.paperFinalizationState ==
		SessionSupervisorPaperFinalizationState::FencePending)
	{
		if (!m_controlPlane.FenceRecoveryOnlyOwner(
				record.issuer, record.token, record.leaseGeneration,
				record, result.ReasonCode()))
			return false;
		if (m_crashPointHook &&
			m_crashPointHook(
				"after_paper_finalization_remote_fence_before_complete_commit"))
		{
			result.ReasonCode() =
				"SUPERVISOR_FAULT_INJECTED:after_paper_finalization_remote_fence_before_complete_commit";
			return false;
		}
		SessionSupervisorLeaseRecord complete = record;
		complete.paperFinalizationState =
			SessionSupervisorPaperFinalizationState::FenceComplete;
		if (!m_leaseStore->AdvancePaperFinalization(
				record.token,
				SessionSupervisorPaperFinalizationState::FencePending,
				complete, result.ReasonCode()))
			return false;
		record = complete;
		result.PaperFinalizationState() = "FENCE_COMPLETE";
		if (m_crashPointHook &&
			m_crashPointHook(
				"after_paper_finalization_complete_commit"))
		{
			result.ReasonCode() =
				"SUPERVISOR_FAULT_INJECTED:after_paper_finalization_complete_commit";
			return false;
		}
	}
	const std::vector<SessionSupervisorLeaseRecord> completedRecords =
		m_leaseStore->List();
	std::vector<SessionSupervisorLeaseRecord> paperRecords;
	bool allComplete = true;
	bool allSealed = true;
	for (std::size_t i = 0; i < completedRecords.size(); ++i)
	{
		if (completedRecords[i].templateId != "paper") continue;
		paperRecords.push_back(completedRecords[i]);
		if (completedRecords[i].paperFinalizationState !=
				SessionSupervisorPaperFinalizationState::None &&
			!SameFinalizationGroup(completedRecords[i], request))
		{
			result.ReasonCode() =
				"PAPER_FINALIZATION_GROUP_MISMATCH";
			return false;
		}
		allComplete = allComplete &&
			(completedRecords[i].paperFinalizationState ==
				 SessionSupervisorPaperFinalizationState::FenceComplete ||
			 completedRecords[i].paperFinalizationState ==
				 SessionSupervisorPaperFinalizationState::AuditSealed);
		allSealed = allSealed &&
			completedRecords[i].paperFinalizationState ==
				SessionSupervisorPaperFinalizationState::AuditSealed;
	}
	if (!allComplete || paperRecords.size() != request.expectedOwnerCount)
	{
		result.ReasonCode() = "PAPER_FINALIZATION_GROUP_PENDING";
		return false;
	}
	if (allSealed)
	{
		const SessionSupervisorLeaseRecord& sealed = paperRecords.front();
		result.PaperFinalizationState() = "AUDIT_SEALED";
		result.FinalizationReceiptSha256() =
			sealed.finalizationReceiptSha256;
		result.PreliminaryFinalizationReceiptSha256() =
			sealed.finalizationReceiptSha256;
		result.FinalizationReceipt() = sealed.finalizationReceipt;
		if (!PopulateAuditFromReceipt(
				sealed.finalizationReceipt, result,
				result.ReasonCode()))
			return false;
		result.accepted = true;
		result.ReasonCode() = "PAPER_FINALIZATION_AUDIT_SEALED";
		return true;
	}
	std::sort(paperRecords.begin(), paperRecords.end(),
		[](const SessionSupervisorLeaseRecord& left,
			const SessionSupervisorLeaseRecord& right) {
			if (left.ownerTokenSha256 != right.ownerTokenSha256)
				return left.ownerTokenSha256 < right.ownerTokenSha256;
			return left.leaseGeneration < right.leaseGeneration;
		});
	ExecutionControlResult audit;
	bool firstAudit = true;
	for (std::size_t i = 0; i < paperRecords.size(); ++i)
	{
		ExecutionControlResult ownerAudit;
		std::string auditReason;
		if (!m_controlPlane.AuditFinalizedRecoveryOwner(
				paperRecords[i].issuer, paperRecords[i],
				ownerAudit, auditReason))
		{
			CopyFinalizationAudit(ownerAudit, result);
			result.ReasonCode() = auditReason.empty() ?
				"PAPER_FINALIZATION_AUDIT_INCOMPLETE" : auditReason;
			return false;
		}
		if (!ExactGlobalFinalizationAudit(
				account, domain, ownerAudit, result.ReasonCode()))
		{
			CopyFinalizationAudit(ownerAudit, result);
			return false;
		}
		if (!firstAudit &&
			!SameCompositeFinalizationBarrier(audit, ownerAudit))
		{
			CopyFinalizationAudit(ownerAudit, result);
			result.ReasonCode() =
				"PAPER_FINALIZATION_COMPOSITE_BARRIER_DRIFT";
			return false;
		}
		if (firstAudit)
		{
			audit = ownerAudit;
			firstAudit = false;
		}
	}
	CopyFinalizationAudit(audit, result);
	if (m_crashPointHook &&
		m_crashPointHook(
			"after_paper_finalization_audit_before_seal_commit"))
	{
		result.ReasonCode() =
			"SUPERVISOR_FAULT_INJECTED:after_paper_finalization_audit_before_seal_commit";
		return false;
	}
	const std::string receipt = BuildPaperFinalizationReceipt(
		request.recoveryId, request.finalizationId,
		request.expectedOwnerSetSha256, request.expectedOwnerCount,
		ownerSetCanonical, audit);
	std::string receiptSha256;
	if (!Sha256(receipt, receiptSha256) ||
		!m_leaseStore->SealPaperFinalizationGroup(
			request.recoveryId, request.finalizationId,
			request.expectedOwnerSetSha256, request.expectedOwnerCount,
			receiptSha256, receipt, result.ReasonCode()))
	{
		if (result.ReasonCode().empty())
			result.ReasonCode() = "PAPER_FINALIZATION_RECEIPT_FAILED";
		return false;
	}
	result.PaperFinalizationState() = "AUDIT_SEALED";
	result.FinalizationReceiptSha256() = receiptSha256;
	result.PreliminaryFinalizationReceiptSha256() = receiptSha256;
	result.FinalizationReceipt() = receipt;
	if (m_crashPointHook &&
		m_crashPointHook(
			"after_paper_finalization_audit_seal_commit"))
	{
		result.ReasonCode() =
			"SUPERVISOR_FAULT_INJECTED:after_paper_finalization_audit_seal_commit";
		return false;
	}
	result.accepted = true;
	result.ReasonCode() = "PAPER_FINALIZATION_AUDIT_SEALED";
	return true;
}

bool UnixSessionSupervisorServer::HandlePaperFinalizeAck(
	const SessionSupervisorRequest& request,
	SessionSupervisorResult& result)
{
	result.leaseGeneration = request.expectedGeneration;
	result.paperFinalizationRequired = true;
	result.PaperFinalizationState() = "NONE";
	result.RecoveryId() = request.recoveryId;
	result.FinalizationId() = request.finalizationId;
	result.ExpectedOwnerSetSha256() = request.expectedOwnerSetSha256;
	result.expectedOwnerCount = request.expectedOwnerCount;
	result.OwnerAccount() = "unavailable";
	result.OwnerExecutionDomain() = "unavailable";
	result.ExecutionServiceEpoch() = "unavailable";
	if (!PaperOwnerTokenSha256(
			request.token, result.OwnerTokenSha256()))
	{
		result.ReasonCode() = "PAPER_FINALIZATION_OWNER_HASH_FAILED";
		return false;
	}
	result.ReasonCode() = "PAPER_FINALIZATION_LEGACY_ACK_DISABLED";
	return false;
}

bool UnixSessionSupervisorServer::HandlePaperTerminalizeAck(
	const SessionSupervisorRequest& request,
	SessionSupervisorResult& result)
{
	result.leaseGeneration = request.expectedGeneration;
	result.paperFinalizationRequired = true;
	result.PaperFinalizationState() = "NONE";
	result.RecoveryId() = request.recoveryId;
	result.FinalizationId() = request.finalizationId;
	result.ExpectedOwnerSetSha256() = request.expectedOwnerSetSha256;
	result.expectedOwnerCount = request.expectedOwnerCount;
	result.OwnerAccount() = "unavailable";
	result.OwnerExecutionDomain() = "unavailable";
	result.ExecutionServiceEpoch() = "unavailable";
	if (!PaperOwnerTokenSha256(
			request.token, result.OwnerTokenSha256()))
	{
		result.ReasonCode() = "PAPER_FINALIZATION_OWNER_HASH_FAILED";
		return false;
	}
	// The v2 operation depended on an unobservable vendor raw/socket drain.
	// Keep the wire command parse-compatible, but permanently disable it.
	result.ReasonCode() = "PAPER_TERMINAL_ACK_V2_DISABLED";
	return false;
}

bool UnixSessionSupervisorServer::HandlePaperTerminalWitnessPrepare(
	const SessionSupervisorRequest& request,
	SessionSupervisorResult& result)
{
	result.leaseGeneration = request.expectedGeneration;
	result.paperFinalizationRequired = true;
	result.PaperFinalizationState() = "NONE";
	result.RecoveryId() = request.recoveryId;
	result.FinalizationId() = request.finalizationId;
	result.ExpectedOwnerSetSha256() = request.expectedOwnerSetSha256;
	result.expectedOwnerCount = request.expectedOwnerCount;
	result.OwnerAccount() = "unavailable";
	result.OwnerExecutionDomain() = "unavailable";
	result.ExecutionServiceEpoch() = "unavailable";
	if (!PaperOwnerTokenSha256(request.token, result.OwnerTokenSha256()))
	{
		result.ReasonCode() = "PAPER_FINALIZATION_OWNER_HASH_FAILED";
		return false;
	}
	if (m_leaseStore == nullptr)
	{
		result.ReasonCode() = "SUPERVISOR_DURABLE_LEASE_STORE_REQUIRED";
		return false;
	}
	SessionSupervisorPaperFinalizationAck existingAck;
	if (m_leaseStore->GetPaperFinalizationAck(
			request.finalizationId, existingAck))
	{
		result.ReasonCode() = "PAPER_TERMINAL_WITNESS_ALREADY_ACKED";
		return false;
	}

	SessionSupervisorLeaseRecord requestOwner;
	if (!m_leaseStore->Get(request.token, requestOwner))
	{
		result.ReasonCode() = "SESSION_LEASE_NOT_FOUND";
		return false;
	}
	result.PaperFinalizationState() =
		SessionSupervisorPaperFinalizationStateName(
			requestOwner.paperFinalizationState);
	if (requestOwner.paperFinalizationState ==
		SessionSupervisorPaperFinalizationState::AuditSealed)
	{
		result.PreliminaryFinalizationReceiptSha256() =
			requestOwner.finalizationReceiptSha256;
		result.FinalizationReceiptSha256() =
			requestOwner.finalizationReceiptSha256;
		result.FinalizationReceipt() = requestOwner.finalizationReceipt;
		if (!PopulateAuditFromReceipt(requestOwner.finalizationReceipt,
				result, result.ReasonCode())) return false;
	}
	if (requestOwner.leaseGeneration != request.expectedGeneration ||
		!requestOwner.paperFinalizationRequired ||
		requestOwner.ownerTokenSha256 != result.OwnerTokenSha256() ||
		requestOwner.paperFinalizationState !=
			SessionSupervisorPaperFinalizationState::AuditSealed ||
		!SameFinalizationGroup(requestOwner, request) ||
		requestOwner.finalizationReceiptSha256 != request.receiptSha256)
	{
		result.ReasonCode() =
			"PAPER_TERMINAL_WITNESS_PREPARE_BINDING_MISMATCH";
		return false;
	}

	std::vector<SessionSupervisorLeaseRecord> paperRecords;
	const std::vector<SessionSupervisorLeaseRecord> records =
		m_leaseStore->List();
	std::string account;
	std::string domain;
	std::string canonical;
	std::string ownerSetSha256;
	for (std::size_t i = 0; i < records.size(); ++i)
	{
		if (records[i].templateId != "paper") continue;
		if (!records[i].paperFinalizationRequired ||
			records[i].paperFinalizationState !=
				SessionSupervisorPaperFinalizationState::AuditSealed ||
			!SameFinalizationGroup(records[i], request) ||
			records[i].finalizationReceiptSha256 != request.receiptSha256 ||
			records[i].finalizationReceipt != requestOwner.finalizationReceipt)
		{
			result.ReasonCode() =
				"PAPER_TERMINAL_WITNESS_PREPARE_BINDING_MISMATCH";
			return false;
		}
		paperRecords.push_back(records[i]);
	}
	if (paperRecords.size() != request.expectedOwnerCount ||
		!CanonicalPaperOwnerSet(paperRecords, canonical, ownerSetSha256,
			result.ReasonCode()) ||
		ownerSetSha256 != request.expectedOwnerSetSha256 ||
		!CommonPaperScope(paperRecords, account, domain,
			result.ReasonCode()))
	{
		if (result.ReasonCode().empty())
			result.ReasonCode() =
				"PAPER_TERMINAL_WITNESS_PREPARE_BINDING_MISMATCH";
		return false;
	}
	std::sort(paperRecords.begin(), paperRecords.end(),
		[](const SessionSupervisorLeaseRecord& left,
			const SessionSupervisorLeaseRecord& right) {
			if (left.ownerTokenSha256 != right.ownerTokenSha256)
				return left.ownerTokenSha256 < right.ownerTokenSha256;
			return left.leaseGeneration < right.leaseGeneration;
		});
	const SessionSupervisorLeaseRecord& terminalOwner = paperRecords.front();
	if (terminalOwner.ownerTokenSha256 != result.OwnerTokenSha256() ||
		terminalOwner.leaseGeneration != request.expectedGeneration)
	{
		result.ReasonCode() =
			"PAPER_TERMINAL_WITNESS_PREPARE_OWNER_NOT_DETERMINISTIC";
		return false;
	}
	if (result.OwnerAccount() != account ||
		result.OwnerExecutionDomain() != domain)
	{
		result.ReasonCode() =
			"PAPER_FINALIZATION_RECEIPT_SCOPE_MISMATCH";
		return false;
	}

	ExecutionControlResult terminal;
	std::string terminalReason;
	const bool localTerminal =
		m_controlPlane.TerminalizeFinalizedRecoveryOwner(
			terminalOwner.issuer, terminalOwner, request.receiptSha256,
			terminal, terminalReason);
	if (localTerminal)
	{
		result.ReasonCode() =
			"PAPER_TERMINAL_WITNESS_PREPARE_UNEXPECTED_LOCAL_TERMINAL";
		return false;
	}
	if (terminal.status != ExecutionCommandStatus::Rejected ||
		terminal.reasonCode != terminalReason ||
		terminal.targetCommandId != request.finalizationId ||
		terminal.ownerAccount != account ||
		terminal.ownerExecutionDomain != domain)
	{
		result.ReasonCode() =
			"PAPER_TERMINAL_WITNESS_PREPARE_RUNTIME_RESULT_INVALID";
		return false;
	}
	if (terminalReason == "IB_PAPER_TERMINALIZATION_INCOMPLETE")
	{
		// A bare TERMINALIZING latch proves intent only. Root must stop the
		// runtime and establish a fresh zero-boundary cutoff before attesting.
		result.ReasonCode() =
			"PAPER_TERMINAL_WITNESS_PREPARE_INTENT_PENDING";
		return false;
	}
	if (terminalReason != "POST_CUTOFF_SIGNED_WITNESS_REQUIRED")
	{
		result.ReasonCode() = terminalReason.empty() ?
			"PAPER_TERMINAL_WITNESS_PREPARE_FAILED" : terminalReason;
		return false;
	}
	if (m_crashPointHook && m_crashPointHook(
			"after_paper_terminal_witness_prepare_before_reply"))
	{
		result.ReasonCode() =
			"SUPERVISOR_FAULT_INJECTED:"
			"after_paper_terminal_witness_prepare_before_reply";
		return false;
	}
	result.accepted = true;
	result.ReasonCode() = "PAPER_TERMINAL_WITNESS_PREPARED";
	return true;
}

bool UnixSessionSupervisorServer::HandlePaperTerminalWitnessAck(
	const SessionSupervisorRequest& request,
	SessionSupervisorResult& result)
{
	result.leaseGeneration = request.expectedGeneration;
	result.paperFinalizationRequired = true;
	result.PaperFinalizationState() = "NONE";
	result.RecoveryId() = request.recoveryId;
	result.FinalizationId() = request.finalizationId;
	result.ExpectedOwnerSetSha256() = request.expectedOwnerSetSha256;
	result.expectedOwnerCount = request.expectedOwnerCount;
	result.OwnerAccount() = "unavailable";
	result.OwnerExecutionDomain() = "unavailable";
	result.ExecutionServiceEpoch() = "unavailable";
	if (!PaperOwnerTokenSha256(request.token, result.OwnerTokenSha256()))
	{
		result.ReasonCode() = "PAPER_FINALIZATION_OWNER_HASH_FAILED";
		return false;
	}
	if (m_leaseStore == nullptr)
	{
		result.ReasonCode() = "SUPERVISOR_DURABLE_LEASE_STORE_REQUIRED";
		return false;
	}

	SessionSupervisorPaperFinalizationAck existingAck;
	if (m_leaseStore->GetPaperFinalizationAck(
			request.finalizationId, existingAck))
	{
		if (existingAck.recoveryId != request.recoveryId ||
			existingAck.expectedOwnerSetSha256 !=
				request.expectedOwnerSetSha256 ||
			existingAck.expectedOwnerCount != request.expectedOwnerCount ||
			existingAck.receiptSha256 != request.receiptSha256 ||
			existingAck.acknowledgingOwnerTokenSha256 !=
				result.OwnerTokenSha256() ||
			existingAck.acknowledgingOwnerGeneration !=
				request.expectedGeneration)
		{
			result.ReasonCode() =
				"PAPER_TERMINAL_ACK_BINDING_MISMATCH";
			return false;
		}
		SessionSupervisorResult preliminary;
		preliminary.RecoveryId() = existingAck.recoveryId;
		preliminary.FinalizationId() = existingAck.finalizationId;
		preliminary.ExpectedOwnerSetSha256() =
			existingAck.expectedOwnerSetSha256;
		preliminary.expectedOwnerCount = existingAck.expectedOwnerCount;
		if (!PopulateAuditFromReceipt(existingAck.receipt, preliminary,
				result.ReasonCode())) return false;
		SessionSupervisorLeaseRecord replayOwner;
		replayOwner.templateId = "paper";
		replayOwner.issuer = existingAck.acknowledgingOwnerIssuer;
		replayOwner.token = request.token;
		replayOwner.agentId = existingAck.terminalizingOwnerAgentId;
		replayOwner.sessionId = existingAck.terminalizingOwnerSessionId;
		replayOwner.ownerAccount = existingAck.terminalizingOwnerAccount;
		replayOwner.ownerExecutionDomain =
			existingAck.terminalizingOwnerExecutionDomain;
		replayOwner.leaseGeneration =
			existingAck.acknowledgingOwnerGeneration;
		std::map<std::string, std::string> evidence;
		if (!ValidateTerminalEvidence(request, replayOwner, preliminary,
				replayOwner.ownerAccount, replayOwner.ownerExecutionDomain,
				evidence, result.ReasonCode())) return false;
		const std::string rebuilt = BuildPaperTerminalWitnessAckReceipt(
			evidence, request.terminalEvidenceSha256);
		std::string rebuiltSha256;
		if (!Sha256(rebuilt, rebuiltSha256) ||
			rebuilt != existingAck.terminalReceipt ||
			rebuiltSha256 != existingAck.terminalReceiptSha256)
		{
			result.ReasonCode() =
				"PAPER_TERMINAL_ACK_REPLAY_EVIDENCE_MISMATCH";
			return false;
		}
		CopyTerminalEvidenceResult(evidence,
			request.terminalEvidenceSha256, true, result);
		result.PreliminaryFinalizationReceiptSha256() =
			existingAck.receiptSha256;
		result.PaperFinalizationState() = "ACKED";
		result.FinalizationReceiptSha256() =
			existingAck.terminalReceiptSha256;
		result.FinalizationReceipt() = existingAck.terminalReceipt;
		result.accepted = true;
		result.ReasonCode() = "PAPER_FINALIZATION_TERMINAL_ACKED";
		return true;
	}

	SessionSupervisorLeaseRecord requestOwner;
	if (!m_leaseStore->Get(request.token, requestOwner))
	{
		result.ReasonCode() = "SESSION_LEASE_NOT_FOUND";
		return false;
	}
	result.PaperFinalizationState() =
		SessionSupervisorPaperFinalizationStateName(
			requestOwner.paperFinalizationState);
	if (requestOwner.paperFinalizationState ==
		SessionSupervisorPaperFinalizationState::AuditSealed)
	{
		result.PreliminaryFinalizationReceiptSha256() =
			requestOwner.finalizationReceiptSha256;
		result.FinalizationReceiptSha256() =
			requestOwner.finalizationReceiptSha256;
		result.FinalizationReceipt() = requestOwner.finalizationReceipt;
		if (!PopulateAuditFromReceipt(requestOwner.finalizationReceipt,
				result, result.ReasonCode())) return false;
	}
	if (requestOwner.leaseGeneration != request.expectedGeneration ||
		!requestOwner.paperFinalizationRequired ||
		requestOwner.ownerTokenSha256 != result.OwnerTokenSha256() ||
		requestOwner.paperFinalizationState !=
			SessionSupervisorPaperFinalizationState::AuditSealed ||
		!SameFinalizationGroup(requestOwner, request) ||
		requestOwner.finalizationReceiptSha256 != request.receiptSha256)
	{
		result.ReasonCode() = "PAPER_TERMINAL_ACK_BINDING_MISMATCH";
		return false;
	}

	std::vector<SessionSupervisorLeaseRecord> paperRecords;
	const std::vector<SessionSupervisorLeaseRecord> records =
		m_leaseStore->List();
	std::string account;
	std::string domain;
	std::string canonical;
	std::string ownerSetSha256;
	for (std::size_t i = 0; i < records.size(); ++i)
	{
		if (records[i].templateId != "paper") continue;
		if (!records[i].paperFinalizationRequired ||
			records[i].paperFinalizationState !=
				SessionSupervisorPaperFinalizationState::AuditSealed ||
			!SameFinalizationGroup(records[i], request) ||
			records[i].finalizationReceiptSha256 != request.receiptSha256 ||
			records[i].finalizationReceipt != requestOwner.finalizationReceipt)
		{
			result.ReasonCode() =
				"PAPER_TERMINAL_ACK_BINDING_MISMATCH";
			return false;
		}
		paperRecords.push_back(records[i]);
	}
	if (paperRecords.size() != request.expectedOwnerCount ||
		!CanonicalPaperOwnerSet(paperRecords, canonical, ownerSetSha256,
			result.ReasonCode()) ||
		ownerSetSha256 != request.expectedOwnerSetSha256 ||
		!CommonPaperScope(paperRecords, account, domain,
			result.ReasonCode()))
	{
		if (result.ReasonCode().empty())
			result.ReasonCode() = "PAPER_TERMINAL_ACK_BINDING_MISMATCH";
		return false;
	}
	std::sort(paperRecords.begin(), paperRecords.end(),
		[](const SessionSupervisorLeaseRecord& left,
			const SessionSupervisorLeaseRecord& right) {
			if (left.ownerTokenSha256 != right.ownerTokenSha256)
				return left.ownerTokenSha256 < right.ownerTokenSha256;
			return left.leaseGeneration < right.leaseGeneration;
		});
	const SessionSupervisorLeaseRecord& terminalOwner = paperRecords.front();
	if (terminalOwner.ownerTokenSha256 != result.OwnerTokenSha256() ||
		terminalOwner.leaseGeneration != request.expectedGeneration)
	{
		result.ReasonCode() =
			"PAPER_TERMINAL_ACK_OWNER_NOT_DETERMINISTIC";
		return false;
	}
	SessionSupervisorResult preliminary;
	preliminary.RecoveryId() = request.recoveryId;
	preliminary.FinalizationId() = request.finalizationId;
	preliminary.ExpectedOwnerSetSha256() = request.expectedOwnerSetSha256;
	preliminary.expectedOwnerCount = request.expectedOwnerCount;
	if (!PopulateAuditFromReceipt(requestOwner.finalizationReceipt,
			preliminary, result.ReasonCode()) ||
		preliminary.OwnerAccount() != account ||
		preliminary.OwnerExecutionDomain() != domain)
	{
		if (result.ReasonCode().empty())
			result.ReasonCode() =
				"PAPER_FINALIZATION_RECEIPT_SCOPE_MISMATCH";
		return false;
	}
	std::map<std::string, std::string> evidence;
	if (!ValidateTerminalEvidence(request, terminalOwner, preliminary,
			account, domain, evidence, result.ReasonCode())) return false;
	const std::string terminalReceipt =
		BuildPaperTerminalWitnessAckReceipt(
			evidence, request.terminalEvidenceSha256);
	std::string terminalReceiptSha256;
	if (terminalReceipt.empty() || terminalReceipt.size() > 12288 ||
		!Sha256(terminalReceipt, terminalReceiptSha256))
	{
		result.ReasonCode() = "PAPER_TERMINAL_ACK_RECEIPT_HASH_FAILED";
		return false;
	}
	if (m_crashPointHook && m_crashPointHook(
			"after_paper_terminal_witness_evidence_before_bearer_purge"))
	{
		result.ReasonCode() =
			"SUPERVISOR_FAULT_INJECTED:"
			"after_paper_terminal_witness_evidence_before_bearer_purge";
		return false;
	}
	for (std::size_t i = 0; i < paperRecords.size(); ++i)
	{
		if (!m_controlPlane.PurgeFinalizedRecoveryOwner(
				paperRecords[i].issuer, paperRecords[i],
				result.ReasonCode())) return false;
		if (i + 1 < paperRecords.size() && m_crashPointHook &&
			m_crashPointHook(
				"after_paper_terminal_witness_partial_bearer_purge"))
		{
			result.ReasonCode() =
				"SUPERVISOR_FAULT_INJECTED:"
				"after_paper_terminal_witness_partial_bearer_purge";
			return false;
		}
	}
	if (m_crashPointHook && m_crashPointHook(
			"after_paper_terminal_witness_bearer_purge_before_ack_commit"))
	{
		result.ReasonCode() =
			"SUPERVISOR_FAULT_INJECTED:"
			"after_paper_terminal_witness_bearer_purge_before_ack_commit";
		return false;
	}
	SessionSupervisorPaperFinalizationAck acknowledgement;
	bool alreadyAcknowledged = false;
	if (!m_leaseStore->AcknowledgeAndPurgePaperFinalizationGroup(
			request.recoveryId, request.finalizationId,
			request.expectedOwnerSetSha256, request.expectedOwnerCount,
			request.receiptSha256, terminalReceiptSha256, terminalReceipt,
			result.OwnerTokenSha256(), request.expectedGeneration,
			terminalOwner.issuer, terminalOwner.agentId,
			terminalOwner.sessionId, terminalOwner.ownerAccount,
			terminalOwner.ownerExecutionDomain, acknowledgement,
			alreadyAcknowledged, result.ReasonCode())) return false;
	(void)alreadyAcknowledged;
	if (acknowledgement.terminalReceiptSha256 != terminalReceiptSha256 ||
		acknowledgement.terminalReceipt != terminalReceipt)
	{
		result.ReasonCode() = "PAPER_TERMINAL_ACK_LEDGER_MISMATCH";
		return false;
	}
	CopyTerminalEvidenceResult(evidence,
		request.terminalEvidenceSha256, false, result);
	result.PreliminaryFinalizationReceiptSha256() = request.receiptSha256;
	result.PaperFinalizationState() = "ACKED";
	result.FinalizationReceiptSha256() =
		acknowledgement.terminalReceiptSha256;
	result.FinalizationReceipt() = acknowledgement.terminalReceipt;
	if (m_crashPointHook &&
		m_crashPointHook("after_paper_terminal_witness_ack_commit"))
	{
		result.ReasonCode() =
			"SUPERVISOR_FAULT_INJECTED:"
			"after_paper_terminal_witness_ack_commit";
		return false;
	}
	result.accepted = true;
	result.ReasonCode() = "PAPER_FINALIZATION_TERMINAL_ACKED";
	return true;
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
				ExecutionControlResult commandResult;
				ExecutionControlResult ownerAudit;
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
				ExecutionControlResult commandResult;
				ExecutionControlResult ownerAudit;
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
				ExecutionControlResult commandResult;
				ExecutionControlResult ownerAudit;
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
					ExecutionControlResult commandResult;
					ExecutionControlResult ownerAudit;
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
						ExecutionControlResult commandResult;
						ExecutionControlResult ownerAudit;
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
						ExecutionControlResult ownerAudit;
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
						ExecutionControlResult commandResult;
						ExecutionControlResult ownerAudit;
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
							ExecutionControlResult commandResult;
							ExecutionControlResult ownerAudit;
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
							ExecutionControlResult commandResult;
							ExecutionControlResult ownerAudit;
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
								ExecutionControlResult commandResult;
								ExecutionControlResult ownerAudit;
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
