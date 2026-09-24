#include "session_supervisor_lease_store.h"

#include <algorithm>
#include <cerrno>
#include <chrono>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <fcntl.h>
#include <limits>
#include <openssl/evp.h>
#include <openssl/rand.h>
#include <set>
#include <sstream>
#if defined(__linux__)
#include <linux/fs.h>
#endif
#include <sys/file.h>
#include <sys/stat.h>
#include <sys/syscall.h>
#include <unistd.h>

#include "session_supervisor_lease_codec_internal.h"

namespace HeptaSessionLeaseCodec {
bool ParseUnsigned(const std::string& value, std::uint64_t maximum, std::uint64_t& parsed)
{
    if (value.empty()) return false;
    char* end = nullptr;
    errno = 0;
    const unsigned long long number = std::strtoull(value.c_str(), &end, 10);
    if (errno != 0 || end == value.c_str() || *end != '\0' || number > maximum) return false;
    parsed = static_cast<std::uint64_t>(number);
    return true;
}

std::vector<std::string> SplitTabs(const std::string& line)
{
    std::vector<std::string> fields;
    std::size_t start = 0;
    while (true)
    {
        const std::size_t tab = line.find('\t', start);
        fields.push_back(line.substr(start, tab == std::string::npos ? tab : tab - start));
        if (tab == std::string::npos) return fields;
        start = tab + 1;
    }
}

bool Sha256Hex(const std::string& value, std::string& digest)
{
    EVP_MD_CTX* context = EVP_MD_CTX_new();
    if (context == nullptr) return false;
    unsigned char bytes[EVP_MAX_MD_SIZE];
    unsigned int length = 0;
    const bool ok = EVP_DigestInit_ex(context, EVP_sha256(), nullptr) == 1 &&
        EVP_DigestUpdate(context, value.data(), value.size()) == 1 &&
        EVP_DigestFinal_ex(context, bytes, &length) == 1 && length == 32;
    EVP_MD_CTX_free(context);
    if (!ok) return false;
    static const char digits[] = "0123456789abcdef";
    digest.clear();
    digest.reserve(length * 2);
    for (unsigned int i = 0; i < length; ++i)
    {
        digest.push_back(digits[bytes[i] >> 4]);
        digest.push_back(digits[bytes[i] & 15]);
    }
    return true;
}

bool IsSha256(const std::string& value)
{
    if (value.size() != 64) return false;
    for (std::size_t i = 0; i < value.size(); ++i)
        if (std::string("0123456789abcdef").find(value[i]) ==
            std::string::npos)
            return false;
    return true;
}

bool IsPrefixedSha256(const std::string& value)
{
    return value.size() == 71 && value.compare(0, 7, "sha256:") == 0 &&
        IsSha256(value.substr(7));
}

bool FinalizationText(const std::string& value, std::size_t maximum)
{
    if (value.empty() || value.size() > maximum) return false;
    for (std::size_t i = 0; i < value.size(); ++i)
    {
        const unsigned char byte = static_cast<unsigned char>(value[i]);
        if (byte < 0x21 || byte > 0x7e) return false;
    }
    return true;
}

bool ValidPaperFinalizationRecord(
    const SessionSupervisorLeaseRecord& record)
{
    const SessionSupervisorPaperFinalizationState state =
        record.paperFinalizationState;
    if (state == SessionSupervisorPaperFinalizationState::None)
        return (!record.paperFinalizationRequired ||
                (record.templateId == "paper" && record.recoveryOnly)) &&
            record.recoveryId.empty() && record.finalizationId.empty() &&
            record.expectedOwnerSetSha256.empty() &&
            record.expectedOwnerCount == 0 && record.ownerTokenSha256.empty() &&
            record.finalizationReceiptSha256.empty() &&
            record.finalizationReceipt.empty();
    if (!record.paperFinalizationRequired ||
        record.templateId != "paper" || !record.recoveryOnly ||
        record.fencePending || record.fenceComplete ||
        !FinalizationText(record.recoveryId, 128) ||
        !FinalizationText(record.finalizationId, 128) ||
        !IsPrefixedSha256(record.expectedOwnerSetSha256) ||
        record.expectedOwnerCount == 0 || record.expectedOwnerCount > 4096 ||
        !IsPrefixedSha256(record.ownerTokenSha256))
        return false;
    if (state == SessionSupervisorPaperFinalizationState::FencePending ||
        state == SessionSupervisorPaperFinalizationState::FenceComplete)
        return record.finalizationReceiptSha256.empty() &&
            record.finalizationReceipt.empty();
    return state == SessionSupervisorPaperFinalizationState::AuditSealed &&
        IsPrefixedSha256(record.finalizationReceiptSha256) &&
        !record.finalizationReceipt.empty() &&
        record.finalizationReceipt.size() <= 4096;
}

bool SameLeaseFields(const SessionSupervisorLeaseRecord& left,
    const SessionSupervisorLeaseRecord& right)
{
    return left.templateId == right.templateId &&
        left.issuer == right.issuer && left.token == right.token &&
        left.agentId == right.agentId && left.sessionId == right.sessionId &&
        left.ownerAccount == right.ownerAccount &&
        left.ownerExecutionDomain == right.ownerExecutionDomain &&
        left.peerUid == right.peerUid && left.expiresAtMs == right.expiresAtMs &&
        left.leaseGeneration == right.leaseGeneration &&
        left.predecessorToken == right.predecessorToken &&
        left.predecessorGeneration == right.predecessorGeneration &&
        left.fencePending == right.fencePending &&
        left.fenceComplete == right.fenceComplete &&
        left.fenceReason == right.fenceReason &&
        left.recoveryOnly == right.recoveryOnly &&
        left.recoveryCommandId == right.recoveryCommandId &&
        left.paperFinalizationRequired ==
            right.paperFinalizationRequired;
}

bool SameFinalizationBinding(const SessionSupervisorLeaseRecord& left,
    const SessionSupervisorLeaseRecord& right)
{
    return left.ownerTokenSha256 == right.ownerTokenSha256 &&
        left.recoveryId == right.recoveryId &&
        left.finalizationId == right.finalizationId &&
        left.expectedOwnerSetSha256 == right.expectedOwnerSetSha256 &&
        left.expectedOwnerCount == right.expectedOwnerCount;
}

bool SameFinalizationGroup(const SessionSupervisorLeaseRecord& left,
    const SessionSupervisorLeaseRecord& right)
{
    return left.recoveryId == right.recoveryId &&
        left.finalizationId == right.finalizationId &&
        left.expectedOwnerSetSha256 == right.expectedOwnerSetSha256 &&
        left.expectedOwnerCount == right.expectedOwnerCount;
}

bool ValidPaperFinalizationAck(
    const SessionSupervisorPaperFinalizationAck& acknowledgement)
{
    return FinalizationText(acknowledgement.recoveryId, 128) &&
        FinalizationText(acknowledgement.finalizationId, 128) &&
        IsPrefixedSha256(acknowledgement.expectedOwnerSetSha256) &&
        acknowledgement.expectedOwnerCount > 0 &&
        acknowledgement.expectedOwnerCount <= 4096 &&
        IsPrefixedSha256(acknowledgement.receiptSha256) &&
        !acknowledgement.receipt.empty() &&
        acknowledgement.receipt.size() <= 4096 &&
		IsPrefixedSha256(acknowledgement.terminalReceiptSha256) &&
		!acknowledgement.terminalReceipt.empty() &&
		acknowledgement.terminalReceipt.size() <= 12288 &&
        IsPrefixedSha256(acknowledgement.acknowledgingOwnerTokenSha256) &&
        acknowledgement.acknowledgingOwnerGeneration > 0 &&
		FinalizationText(acknowledgement.acknowledgingOwnerIssuer, 128) &&
		FinalizationText(acknowledgement.terminalizingOwnerAgentId, 128) &&
		FinalizationText(acknowledgement.terminalizingOwnerSessionId, 128) &&
		FinalizationText(acknowledgement.terminalizingOwnerAccount, 128) &&
		FinalizationText(
			acknowledgement.terminalizingOwnerExecutionDomain, 128);
}

bool DecodeCanonicalHex(const std::string& encoded, std::string& decoded)
{
    if (encoded.empty() || (encoded.size() % 2) != 0) return false;
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

bool ParseCanonicalUnsigned(const std::string& value,
    std::uint64_t& parsed)
{
    if (value.empty() || (value.size() > 1 && value[0] == '0')) return false;
    std::uint64_t number = 0;
    for (std::size_t i = 0; i < value.size(); ++i)
    {
        if (value[i] < '0' || value[i] > '9') return false;
        const std::uint64_t digit =
            static_cast<std::uint64_t>(value[i] - '0');
        if (number >
            (std::numeric_limits<std::uint64_t>::max() - digit) / 10)
            return false;
        number = number * 10 + digit;
    }
    parsed = number;
    return true;
}

bool ValidPaperFinalizationReceipt(
    const std::string& receipt,
    const std::string& recoveryId,
    const std::string& finalizationId,
    const std::string& expectedOwnerSetSha256,
    std::uint64_t expectedOwnerCount,
    const std::string& expectedOwnerAccount,
    const std::string& expectedOwnerDomain)
{
    if (receipt.empty() || receipt.size() > 4096 ||
        receipt.back() != '\n') return false;
    static const char* keys[] = {
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
    std::vector<std::string> values;
    std::istringstream input(receipt);
    std::string line;
    for (std::size_t i = 0; i < sizeof(keys) / sizeof(keys[0]); ++i)
    {
        if (!std::getline(input, line)) return false;
        const std::string prefix = std::string(keys[i]) + "=";
        if (line.compare(0, prefix.size(), prefix) != 0) return false;
        values.push_back(line.substr(prefix.size()));
    }
    if (std::getline(input, line)) return false;
    if (values[0] != "hepta.paper-session-finalization-receipt.v1" ||
        values[1] != "1" || values[2] != "AUDIT_SEALED" ||
        values[3] != recoveryId || values[4] != finalizationId ||
        values[5] != expectedOwnerSetSha256 ||
        values[25] != "0" || values[26] != "1" ||
        values[27] != "0" || values[28] != "0" ||
        values[29] != "0" || values[30] != "1" || values[31] != "0" ||
        !FinalizationText(values[3], 128) ||
        !FinalizationText(values[4], 128) ||
        !IsPrefixedSha256(values[5]) ||
        !FinalizationText(values[8], 128) ||
        !FinalizationText(values[9], 128) ||
        !FinalizationText(values[10], 256) ||
        (!expectedOwnerAccount.empty() &&
            values[8] != expectedOwnerAccount) ||
        (!expectedOwnerDomain.empty() &&
            values[9] != expectedOwnerDomain))
        return false;

    std::uint64_t numeric[15] = {};
    const std::size_t numericIndexes[] = {
        6, 11, 12, 13, 14, 15, 16, 17, 18, 19, 20, 21, 22, 23, 24};
    for (std::size_t i = 0;
         i < sizeof(numericIndexes) / sizeof(numericIndexes[0]); ++i)
        if (!ParseCanonicalUnsigned(values[numericIndexes[i]], numeric[i]))
            return false;
    if (numeric[0] != expectedOwnerCount || numeric[0] == 0 ||
        numeric[0] > 4096 || numeric[1] == 0 || numeric[2] == 0 ||
        numeric[3] == 0 || numeric[4] == 0 || numeric[5] == 0 ||
        numeric[6] == 0 || numeric[7] == 0 || numeric[8] == 0 ||
        numeric[10] > numeric[11] || numeric[11] != numeric[9] ||
        numeric[12] != 0 || numeric[13] != 0 || numeric[14] != 0)
        return false;

    std::string canonical;
    std::string digest;
    if (!DecodeCanonicalHex(values[7], canonical) ||
        !Sha256Hex(canonical, digest) ||
        expectedOwnerSetSha256 != "sha256:" + digest)
        return false;
    std::uint64_t ownerLines = 0;
    for (std::size_t i = 0; i < canonical.size(); ++i)
        if (canonical[i] == '\n') ++ownerLines;
    return ownerLines == expectedOwnerCount && canonical.back() == '\n';
}

bool ValidPaperTerminalAckReceipt(
    const std::string& receipt,
    const std::string& recoveryId,
    const std::string& finalizationId,
    const std::string& expectedOwnerSetSha256,
    std::uint64_t expectedOwnerCount,
    const std::string& preliminaryReceiptSha256,
    const std::string& expectedOwnerAccount,
    const std::string& expectedOwnerDomain,
    const std::string& expectedOwnerAgentId,
    const std::string& expectedOwnerSessionId,
    std::uint64_t expectedOwnerGeneration)
{
    if (receipt.empty() || receipt.size() > 12288 ||
        receipt.back() != '\n' ||
        !IsPrefixedSha256(preliminaryReceiptSha256)) return false;

    static const char* v3Keys[] = {
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
        "terminal_evidence_file_sha256", "terminal_evidence_body_sha256"
    };
    std::map<std::string, std::string> v3;
    std::istringstream v3Input(receipt);
    std::string v3Line;
    for (std::size_t i = 0; i < sizeof(v3Keys) / sizeof(v3Keys[0]); ++i)
    {
        if (!std::getline(v3Input, v3Line)) return false;
        const std::string prefix = std::string(v3Keys[i]) + "=";
        if (v3Line.compare(0, prefix.size(), prefix) != 0 ||
            v3Line.size() == prefix.size()) return false;
        const std::string value = v3Line.substr(prefix.size());
        if (value.find('=') != std::string::npos ||
            !v3.insert(std::make_pair(v3Keys[i], value)).second)
            return false;
    }
    if (std::getline(v3Input, v3Line)) return false;
    if (v3["schema"] !=
            "hepta.paper-session-terminal-ack-receipt.v3" ||
        v3["version"] != "3" || v3["status"] != "TERMINAL_ACKED" ||
        v3["terminal_proof_kind"] !=
            "POST_CUTOFF_SIGNED_ACCOUNT_WITNESS_V1" ||
        v3["recovery_id"] != recoveryId ||
        v3["finalization_id"] != finalizationId ||
        v3["expected_owner_set_sha256"] != expectedOwnerSetSha256 ||
        v3["preliminary_finalization_receipt_sha256"] !=
            preliminaryReceiptSha256 ||
        v3["owner_agent_id"] != expectedOwnerAgentId ||
        v3["owner_session_id"] != expectedOwnerSessionId ||
        v3["owner_account"] != expectedOwnerAccount ||
        v3["owner_execution_domain"] != expectedOwnerDomain ||
        v3["provider_capability"] !=
            "ACCOUNT_WIDE_ATOMIC_OR_CAUSAL_POST_CUTOFF_READ_ONLY_V1" ||
        (v3["snapshot_consistency"] != "ATOMIC_ACCOUNT" &&
         v3["snapshot_consistency"] != "CAUSAL_WATERMARK") ||
        !FinalizationText(v3["recovery_id"], 128) ||
        !FinalizationText(v3["finalization_id"], 128) ||
        !FinalizationText(v3["campaign_id"], 128) ||
        !FinalizationText(v3["cycle_id"], 128) ||
        !FinalizationText(v3["owner_agent_id"], 128) ||
        !FinalizationText(v3["owner_session_id"], 128) ||
        !FinalizationText(v3["owner_account"], 128) ||
        !FinalizationText(v3["owner_execution_domain"], 128) ||
        !FinalizationText(v3["execution_service_epoch"], 256) ||
        !FinalizationText(v3["provider_id"], 128)) return false;

    const char* v3Digests[] = {
        "expected_owner_set_sha256",
        "preliminary_finalization_receipt_sha256", "account_id_sha256",
        "terminalizing_latch_sha256",
        "terminal_external_halt_latch_sha256",
        "transport_cutoff_receipt_file_sha256",
        "transport_cutoff_receipt_body_sha256",
        "post_cutoff_terminal_witness_file_sha256",
        "post_cutoff_terminal_witness_body_sha256",
        "provider_trust_policy_file_sha256",
        "provider_trust_policy_body_sha256", "signed_account_payload_sha256",
		"signed_account_signature_sha256", "egress_policy_sha256",
        "known_mutation_command_set_sha256",
        "known_correlation_set_sha256", "terminal_evidence_file_sha256",
        "terminal_evidence_body_sha256"};
    for (std::size_t i = 0;
         i < sizeof(v3Digests) / sizeof(v3Digests[0]); ++i)
        if (!IsPrefixedSha256(v3[v3Digests[i]]) ||
            v3[v3Digests[i]] ==
                "sha256:0000000000000000000000000000000000000000000000000000000000000000")
            return false;

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
    if (!ParseCanonicalUnsigned(v3["expected_owner_count"], ownerCount) ||
        ownerCount != expectedOwnerCount || ownerCount == 0 ||
        ownerCount > 4096 ||
        !ParseCanonicalUnsigned(v3["execution_service_fencing_generation"],
            serviceFence) || serviceFence == 0 ||
        !ParseCanonicalUnsigned(v3["recovery_ingress_fence"],
            recoveryFence) || recoveryFence == 0 ||
        recoveryFence != expectedOwnerGeneration ||
        !ParseCanonicalUnsigned(v3["terminalization_generation"],
            terminalGeneration) || terminalGeneration != 1 ||
		!ParseCanonicalUnsigned(v3["egress_publisher_pid"],
			egressPublisherPid) || egressPublisherPid == 0 ||
		!ParseCanonicalUnsigned(v3["egress_publisher_start_ticks"],
			egressPublisherStartTicks) || egressPublisherStartTicks == 0 ||
        !ParseCanonicalUnsigned(v3["egress_policy_generation"],
            egressGeneration) || egressGeneration == 0 ||
        !ParseCanonicalUnsigned(v3["known_mutation_command_count"],
            knownMutationCount) ||
        !ParseCanonicalUnsigned(v3["known_correlation_count"],
            knownCorrelationCount) ||
        !ParseCanonicalUnsigned(v3["settled_mutation_command_count"],
            settledMutationCount) || settledMutationCount != knownMutationCount)
        return false;

    const char* v3Zeros[] = {
        "unknown_mutation_command_count", "unresolved_mutation_command_count",
        "unknown_active_order_count", "active_order_count", "position_count",
        "nonzero_cash_fx_count", "gross_absolute_position",
        "gross_fx_exposure", "gross_risk", "mutation_connector_count",
        "broker_socket_count", "broker_process_count",
        "broker_credential_count"};
    for (std::size_t i = 0;
         i < sizeof(v3Zeros) / sizeof(v3Zeros[0]); ++i)
        if (v3[v3Zeros[i]] != "0") return false;
    const char* v3Truths[] = {
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
    for (std::size_t i = 0;
         i < sizeof(v3Truths) / sizeof(v3Truths[0]); ++i)
        if (v3[v3Truths[i]] != "1") return false;
    const char* v3Falses[] = {
        "broker_transport_connected", "broker_reconnect_permitted",
        "mutation_attempted", "paper_authorized", "live_authorized",
        "mutation_authorized", "direct_broker_access",
        "order_submission_authorized", "order_authorized",
        "authority_granted"};
    for (std::size_t i = 0;
         i < sizeof(v3Falses) / sizeof(v3Falses[0]); ++i)
        if (v3[v3Falses[i]] != "0") return false;

    if (v3["host_boot_id"].size() != 36 ||
        v3["host_boot_id"] ==
            "00000000-0000-0000-0000-000000000000") return false;
    for (std::size_t i = 0; i < v3["host_boot_id"].size(); ++i)
        if (i == 8 || i == 13 || i == 18 || i == 23)
        {
            if (v3["host_boot_id"][i] != '-') return false;
        }
        else if (!((v3["host_boot_id"][i] >= '0' &&
                    v3["host_boot_id"][i] <= '9') ||
                   (v3["host_boot_id"][i] >= 'a' &&
                    v3["host_boot_id"][i] <= 'f'))) return false;

    std::string accountDigest;
    if (!Sha256Hex(v3["owner_account"], accountDigest) ||
        v3["account_id_sha256"] != "sha256:" + accountDigest)
        return false;
    std::string canonical;
    std::string ownerDigest;
    if (!DecodeCanonicalHex(v3["owner_set_canonical_hex"], canonical) ||
        canonical.empty() || canonical.back() != '\n' ||
        !Sha256Hex(canonical, ownerDigest) ||
        expectedOwnerSetSha256 != "sha256:" + ownerDigest) return false;
    std::istringstream owners(canonical);
    std::string ownerLine;
    std::string previousOwnerLine;
    std::uint64_t ownerLines = 0;
    while (std::getline(owners, ownerLine))
    {
        if (ownerLine.empty() ||
            (!previousOwnerLine.empty() && ownerLine <= previousOwnerLine))
            return false;
        previousOwnerLine = ownerLine;
        std::string values[4];
        std::size_t offset = 0;
        for (int field = 0; field < 4; ++field)
        {
            const std::size_t separator = ownerLine.find('\t', offset);
            if ((field < 3 && separator == std::string::npos) ||
                (field == 3 && separator != std::string::npos)) return false;
            values[field] = ownerLine.substr(offset,
                separator == std::string::npos ? std::string::npos :
                separator - offset);
            offset = separator == std::string::npos ? ownerLine.size() :
                separator + 1;
        }
        std::uint64_t generation = 0;
        std::string account;
        std::string domain;
        if (!IsPrefixedSha256(values[0]) ||
            values[0] ==
                "sha256:0000000000000000000000000000000000000000000000000000000000000000" ||
            !ParseCanonicalUnsigned(values[1], generation) || generation == 0 ||
            !DecodeCanonicalHex(values[2], account) ||
            !DecodeCanonicalHex(values[3], domain) ||
            account != expectedOwnerAccount || domain != expectedOwnerDomain)
            return false;
        ++ownerLines;
    }
    return ownerLines == expectedOwnerCount;

}

bool PaperFinalizationReceiptContainsOwner(
    const std::string& receipt,
    const std::string& ownerTokenSha256)
{
    const std::string prefix = "owner_set_canonical_hex=";
    std::istringstream input(receipt);
    std::string line;
    std::string canonical;
    while (std::getline(input, line))
        if (line.compare(0, prefix.size(), prefix) == 0)
        {
            if (!DecodeCanonicalHex(
                    line.substr(prefix.size()), canonical))
                return false;
            break;
        }
    if (canonical.empty()) return false;
    std::istringstream owners(canonical);
    while (std::getline(owners, line))
        if (line.compare(0, ownerTokenSha256.size(),
                ownerTokenSha256) == 0 &&
            line.size() > ownerTokenSha256.size() &&
            line[ownerTokenSha256.size()] == '\t')
            return true;
    return false;
}

bool RetiredPaperOwner(
    const std::map<std::string,
        SessionSupervisorPaperFinalizationAck>& acknowledgements,
    const std::string& token)
{
    std::string digest;
    if (!Sha256Hex(token + "\n", digest)) return true;
    const std::string tokenSha256 = "sha256:" + digest;
    for (std::map<std::string,
             SessionSupervisorPaperFinalizationAck>::const_iterator it =
             acknowledgements.begin(); it != acknowledgements.end(); ++it)
        if (PaperFinalizationReceiptContainsOwner(
                it->second.receipt, tokenSha256))
            return true;
    return false;
}


}
using namespace HeptaSessionLeaseCodec;
namespace { const char* kAad = "HeptaTrader supervisor lease store HSL2"; }

std::string SessionSupervisorLeaseStore::HexEncode(const std::string& value)
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

bool SessionSupervisorLeaseStore::HexDecode(const std::string& value, std::string& decoded)
{
    if ((value.size() & 1) != 0) return false;
    decoded.clear();
    decoded.reserve(value.size() / 2);
    for (std::size_t i = 0; i < value.size(); i += 2)
    {
        const std::size_t high = std::string("0123456789abcdef").find(value[i]);
        const std::size_t low = std::string("0123456789abcdef").find(value[i + 1]);
        if (high == std::string::npos || low == std::string::npos) return false;
        decoded.push_back(static_cast<char>((high << 4) | low));
    }
    return true;
}

std::string SessionSupervisorLeaseStore::SerializePlaintext(
    SessionSupervisorLeaseCapacity* capacity) const
{
    std::ostringstream output;
    SessionSupervisorLeaseCapacity observed;
    std::uint64_t paperRecords = 0;
    // The encrypted envelope remains HSL2-compatible. HSL8 retains the HSL7
    // tombstone records but upgrades the acknowledgement ledger so success is
    // bound to both the preliminary audit and the independently durable
    // Execution terminal witness. Ledger lines can never be interpreted as
    // provisionable lease material.
    output << "HSL8\n";
    for (std::map<std::string, SessionSupervisorLeaseRecord>::const_iterator it = m_records.begin();
         it != m_records.end(); ++it)
    {
        const SessionSupervisorLeaseRecord& record = it->second;
        ++observed.leaseRecords;
        if (record.templateId == "paper") ++paperRecords;
        if (record.paperFinalizationState != SessionSupervisorPaperFinalizationState::None)
            ++observed.finalizingLeases;
        else if (record.recoveryOnly) ++observed.recoveryLeases;
        else if (record.fencePending) ++observed.fencedLeases;
        else ++observed.activeLeases;
        output << "R\t" << HexEncode(record.templateId) << '\t' << HexEncode(record.issuer) << '\t'
               << HexEncode(record.token) << '\t' << HexEncode(record.agentId) << '\t'
               << HexEncode(record.sessionId) << '\t' << record.peerUid << '\t'
               << record.expiresAtMs << '\t' << record.leaseGeneration << '\t'
               << HexEncode(record.predecessorToken) << '\t'
               << record.predecessorGeneration << '\t'
               << (record.fencePending ? 1 : 0) << '\t'
               << (record.fenceComplete ? 1 : 0) << '\t'
               << HexEncode(record.fenceReason) << '\t'
               << (record.recoveryOnly ? 1 : 0) << '\t'
               << HexEncode(record.recoveryCommandId) << '\t'
               << (record.paperFinalizationRequired ? 1 : 0) << '\t'
               << HexEncode(record.ownerAccount) << '\t'
               << HexEncode(record.ownerExecutionDomain) << '\t'
               << static_cast<std::uint32_t>(
                    record.paperFinalizationState) << '\t'
               << HexEncode(record.recoveryId) << '\t'
               << HexEncode(record.finalizationId) << '\t'
               << HexEncode(record.expectedOwnerSetSha256) << '\t'
               << record.expectedOwnerCount << '\t'
               << HexEncode(record.ownerTokenSha256) << '\t'
               << HexEncode(record.finalizationReceiptSha256) << '\t'
               << HexEncode(record.finalizationReceipt) << '\n';
    }
    observed.leasePlaintextBytes = static_cast<std::uint64_t>(output.tellp()) - 5;
    for (std::map<std::string,
             SessionSupervisorPaperFinalizationAck>::const_iterator it =
             m_paperFinalizationAcks.begin();
         it != m_paperFinalizationAcks.end(); ++it)
    {
        const SessionSupervisorPaperFinalizationAck& acknowledgement =
            it->second;
        output << "A\t" << HexEncode(acknowledgement.recoveryId) << '\t'
               << HexEncode(acknowledgement.finalizationId) << '\t'
               << HexEncode(acknowledgement.expectedOwnerSetSha256) << '\t'
               << acknowledgement.expectedOwnerCount << '\t'
               << HexEncode(acknowledgement.receiptSha256) << '\t'
               << HexEncode(acknowledgement.receipt) << '\t'
               << HexEncode(acknowledgement.terminalReceiptSha256) << '\t'
               << HexEncode(acknowledgement.terminalReceipt) << '\t'
               << HexEncode(
                    acknowledgement.acknowledgingOwnerTokenSha256) << '\t'
               << acknowledgement.acknowledgingOwnerGeneration << '\t'
               << HexEncode(acknowledgement.acknowledgingOwnerIssuer) << '\t'
               << HexEncode(acknowledgement.terminalizingOwnerAgentId) << '\t'
               << HexEncode(acknowledgement.terminalizingOwnerSessionId) << '\t'
               << HexEncode(acknowledgement.terminalizingOwnerAccount) << '\t'
               << HexEncode(
                    acknowledgement.terminalizingOwnerExecutionDomain)
               << '\n';
    }
    const std::string plaintext = output.str();
    if (capacity != nullptr)
    {
        observed.acknowledgementGroups = m_paperFinalizationAcks.size();
        observed.acknowledgementPlaintextBytes = plaintext.size() - 5 - observed.leasePlaintextBytes;
        observed.projectedEncodedBytes = 64 + 2 * static_cast<std::uint64_t>(plaintext.size());
        CompleteCapacity(observed, paperRecords);
        *capacity = observed;
    }
    return plaintext;
}

bool SessionSupervisorLeaseStore::ParsePlaintext(const std::string& plaintext, std::string& reason)
{
    if (!ParsePlaintextImpl(plaintext, nullptr, 0, nullptr, reason)) return false;
    SerializePlaintext(&m_capacity);
    m_capacity.encodedBytes = m_sourceSize;
    return true;
}

bool SessionSupervisorLeaseStore::ParsePlaintextForTerminalCleanup(
    const std::string& plaintext,
    const SessionSupervisorLegacyPaperCleanupRequest& request,
    std::uint64_t nowMs,
    SessionSupervisorLegacyPaperCleanupResult& result,
    std::string& reason)
{
    result = SessionSupervisorLegacyPaperCleanupResult();
    return ParsePlaintextImpl(plaintext, &request, nowMs, &result, reason);
}

bool SessionSupervisorLeaseStore::ParsePlaintextImpl(
    const std::string& plaintext,
    const SessionSupervisorLegacyPaperCleanupRequest* cleanupRequest,
    std::uint64_t nowMs,
    SessionSupervisorLegacyPaperCleanupResult* cleanupResult,
    std::string& reason)
{
    std::istringstream input(plaintext);
    std::string line;
    if (!std::getline(input, line) ||
        (line != "HSL1" && line != "HSL2" &&
         line != "HSL3" && line != "HSL4" && line != "HSL5" &&
         line != "HSL6" && line != "HSL7" && line != "HSL8"))
    { reason = "LEASE_STORE_PLAINTEXT_INVALID"; return false; }
    const bool hsl7 = line == "HSL7";
	const bool hsl8 = line == "HSL8";
	const bool tagged = hsl7 || hsl8;
    const bool terminalCleanupLegacy =
        line == "HSL4" || line == "HSL5";
    if (cleanupRequest != nullptr && !terminalCleanupLegacy)
    {
        reason = "LEASE_STORE_TERMINAL_CLEANUP_SOURCE_INVALID";
        return false;
    }
    const bool legacy = line == "HSL1";
    const bool completeState = line == "HSL3" || line == "HSL4" ||
        line == "HSL5" || line == "HSL6" || tagged;
    const bool predecessorState = line == "HSL4" || line == "HSL5" ||
        line == "HSL6" || tagged;
    const bool recoveryState = line == "HSL5" || line == "HSL6" || tagged;
    const bool ownerState = line == "HSL6" || tagged;
    const bool hsl5 = line == "HSL5";
    std::map<std::string, SessionSupervisorLeaseRecord> parsedRecords;
    std::map<std::string, SessionSupervisorPaperFinalizationAck> parsedAcks;
    std::set<std::string> parsedTokens;
    std::size_t retiredRecords = 0;
    while (std::getline(input, line))
    {
        if (line.empty()) continue;
        const std::vector<std::string> fields = SplitTabs(line);
        if (hsl7 && !fields.empty() && fields[0] == "A")
		{
			reason = "LEASE_STORE_LEGACY_PAPER_FINALIZATION_ACK_REJECTED";
			return false;
		}
        if (hsl8 && !fields.empty() && fields[0] == "A")
        {
            SessionSupervisorPaperFinalizationAck acknowledgement;
            std::uint64_t expectedOwnerCount = 0;
            std::uint64_t acknowledgingGeneration = 0;
            std::string receiptDigest;
            std::string terminalReceiptDigest;
            if (fields.size() != 16 ||
                !HexDecode(fields[1], acknowledgement.recoveryId) ||
                !HexDecode(fields[2], acknowledgement.finalizationId) ||
                !HexDecode(fields[3],
                    acknowledgement.expectedOwnerSetSha256) ||
                !ParseUnsigned(fields[4], 4096, expectedOwnerCount) ||
                !HexDecode(fields[5], acknowledgement.receiptSha256) ||
                !HexDecode(fields[6], acknowledgement.receipt) ||
                !HexDecode(fields[7],
                    acknowledgement.terminalReceiptSha256) ||
                !HexDecode(fields[8], acknowledgement.terminalReceipt) ||
                !HexDecode(fields[9],
                    acknowledgement.acknowledgingOwnerTokenSha256) ||
                !ParseUnsigned(fields[10],
                    std::numeric_limits<std::uint64_t>::max(),
                    acknowledgingGeneration) ||
                !HexDecode(fields[11],
                    acknowledgement.acknowledgingOwnerIssuer) ||
                !HexDecode(fields[12],
                    acknowledgement.terminalizingOwnerAgentId) ||
                !HexDecode(fields[13],
                    acknowledgement.terminalizingOwnerSessionId) ||
                !HexDecode(fields[14],
                    acknowledgement.terminalizingOwnerAccount) ||
                !HexDecode(fields[15],
                    acknowledgement.terminalizingOwnerExecutionDomain) ||
                expectedOwnerCount == 0 || acknowledgingGeneration == 0)
            {
                reason = "LEASE_STORE_PAPER_FINALIZATION_ACK_INVALID";
                return false;
            }
            acknowledgement.expectedOwnerCount = expectedOwnerCount;
            acknowledgement.acknowledgingOwnerGeneration =
                acknowledgingGeneration;
            if (!ValidPaperFinalizationAck(acknowledgement) ||
                !Sha256Hex(acknowledgement.receipt, receiptDigest) ||
                acknowledgement.receiptSha256 !=
                    "sha256:" + receiptDigest ||
                !Sha256Hex(acknowledgement.terminalReceipt,
                    terminalReceiptDigest) ||
                acknowledgement.terminalReceiptSha256 !=
                    "sha256:" + terminalReceiptDigest ||
                !ValidPaperFinalizationReceipt(
                    acknowledgement.receipt,
                    acknowledgement.recoveryId,
                    acknowledgement.finalizationId,
                    acknowledgement.expectedOwnerSetSha256,
                    acknowledgement.expectedOwnerCount,
                    acknowledgement.terminalizingOwnerAccount,
                    acknowledgement.terminalizingOwnerExecutionDomain) ||
                !ValidPaperTerminalAckReceipt(
                    acknowledgement.terminalReceipt,
                    acknowledgement.recoveryId,
                    acknowledgement.finalizationId,
                    acknowledgement.expectedOwnerSetSha256,
                    acknowledgement.expectedOwnerCount,
                    acknowledgement.receiptSha256,
                    acknowledgement.terminalizingOwnerAccount,
                    acknowledgement.terminalizingOwnerExecutionDomain,
                    acknowledgement.terminalizingOwnerAgentId,
                    acknowledgement.terminalizingOwnerSessionId,
                    acknowledgement.acknowledgingOwnerGeneration) ||
                parsedAcks.find(acknowledgement.finalizationId) !=
                    parsedAcks.end())
            {
                reason = "LEASE_STORE_PAPER_FINALIZATION_ACK_INVALID";
                return false;
            }
            parsedAcks[acknowledgement.finalizationId] = acknowledgement;
            continue;
        }
        if (tagged && (fields.empty() || fields[0] != "R"))
        {
            reason = "LEASE_STORE_PLAINTEXT_INVALID";
            return false;
        }
        const std::size_t base = tagged ? 1 : 0;
        SessionSupervisorLeaseRecord record;
        std::uint64_t peerUid = 0;
        std::uint64_t fencePending = 0;
        std::uint64_t fenceComplete = 0;
        std::uint64_t recoveryOnly = 0;
        std::uint64_t paperFinalizationRequired = 0;
        std::uint64_t paperFinalizationState = 0;
        std::uint64_t expectedOwnerCount = 0;
        if (fields.size() !=
                (tagged ? 27 : (legacy ? 8 : (ownerState ? 17 : (recoveryState ? 15 : (predecessorState ? 13 :
                    (completeState ? 11 : 10)))))) ||
            !HexDecode(fields[base + 0], record.templateId) ||
            !HexDecode(fields[base + 1], record.issuer) ||
            !HexDecode(fields[base + 2], record.token) ||
            !HexDecode(fields[base + 3], record.agentId) ||
            !HexDecode(fields[base + 4], record.sessionId) ||
            !ParseUnsigned(fields[base + 5],
                std::numeric_limits<std::uint32_t>::max(), peerUid) ||
            !ParseUnsigned(fields[base + 6],
                std::numeric_limits<std::uint64_t>::max(),
                record.expiresAtMs) ||
            !ParseUnsigned(fields[base + 7],
                std::numeric_limits<std::uint64_t>::max(),
                record.leaseGeneration) ||
            (predecessorState &&
             (!HexDecode(fields[base + 8], record.predecessorToken) ||
              !ParseUnsigned(fields[base + 9],
                std::numeric_limits<std::uint64_t>::max(),
                record.predecessorGeneration))) ||
            (!legacy && (!ParseUnsigned(
                         fields[base + (predecessorState ? 10 : 8)],
                         1, fencePending) ||
                         (completeState &&
                          !ParseUnsigned(
                            fields[base + (predecessorState ? 11 : 9)],
                            1, fenceComplete)) ||
                         !HexDecode(fields[base + (predecessorState ? 12 :
                                    (completeState ? 10 : 9))],
                                    record.fenceReason))) ||
            (recoveryState &&
             (!ParseUnsigned(fields[base + 13], 1, recoveryOnly) ||
              !HexDecode(fields[base + 14], record.recoveryCommandId))) ||
            (tagged &&
             !ParseUnsigned(fields[base + 15], 1,
                 paperFinalizationRequired)) ||
            (ownerState &&
             (!HexDecode(fields[base + (tagged ? 16 : 15)],
                  record.ownerAccount) ||
              !HexDecode(fields[base + (tagged ? 17 : 16)],
                  record.ownerExecutionDomain))) ||
            (tagged &&
             (!ParseUnsigned(fields[19], 3, paperFinalizationState) ||
              !HexDecode(fields[20], record.recoveryId) ||
              !HexDecode(fields[21], record.finalizationId) ||
              !HexDecode(fields[22], record.expectedOwnerSetSha256) ||
              !ParseUnsigned(fields[23], 4096, expectedOwnerCount) ||
              !HexDecode(fields[24], record.ownerTokenSha256) ||
              !HexDecode(fields[25], record.finalizationReceiptSha256) ||
              !HexDecode(fields[26], record.finalizationReceipt))) ||
            record.token.size() < 24 ||
            (record.templateId != "watch" &&
             record.templateId != "paper") ||
            record.issuer.empty() ||
            record.agentId.empty() || record.sessionId.empty() || record.expiresAtMs == 0 ||
            record.leaseGeneration == 0 ||
            (record.templateId == "watch" && ownerState &&
             (!record.ownerAccount.empty() ||
              !record.ownerExecutionDomain.empty())) ||
            (recoveryOnly == 1 && record.templateId != "paper") ||
            ((!record.predecessorToken.empty()) !=
             (record.predecessorGeneration != 0)) ||
            ((!record.predecessorToken.empty() ||
              record.predecessorGeneration != 0) &&
             (!((record.templateId == "watch" && fencePending == 1 &&
                 record.fenceReason == "session_revoked") ||
                (record.templateId == "paper" &&
                 ((fencePending == 1 &&
                   record.fenceReason == "session_revoked") ||
                  (recoveryOnly == 1 && fencePending == 0)))) ||
              record.predecessorToken.size() < 24 ||
              record.predecessorGeneration ==
                std::numeric_limits<std::uint64_t>::max() ||
              record.leaseGeneration !=
                record.predecessorGeneration + 1)) ||
            (completeState && fenceComplete == 1 &&
             (fencePending != 1 || record.templateId != "watch")) ||
            (!legacy && fencePending == 1 &&
             record.fenceReason != "session_revoked" &&
             record.fenceReason != "session_expired") ||
            (!legacy && fencePending == 0 &&
             (fenceComplete != 0 || !record.fenceReason.empty())) ||
	            (recoveryState &&
	             ((recoveryOnly == 0 && !record.recoveryCommandId.empty()) ||
	              record.recoveryCommandId.size() > 128)))
        { reason = "LEASE_STORE_RECORD_INVALID"; return false; }
        record.peerUid = static_cast<std::uint32_t>(peerUid);
        record.fencePending = !legacy && fencePending == 1;
        record.fenceComplete = completeState && fenceComplete == 1;
        record.recoveryOnly = recoveryState && recoveryOnly == 1;
        record.paperFinalizationRequired =
            tagged && paperFinalizationRequired == 1;
        record.paperFinalizationState =
            static_cast<SessionSupervisorPaperFinalizationState>(
                paperFinalizationState);
        record.expectedOwnerCount = expectedOwnerCount;
        std::string receiptDigest;
        if (!ValidPaperFinalizationRecord(record) ||
            (record.paperFinalizationState ==
                 SessionSupervisorPaperFinalizationState::AuditSealed &&
             (!Sha256Hex(record.finalizationReceipt, receiptDigest) ||
              record.finalizationReceiptSha256 !=
                "sha256:" + receiptDigest ||
              !ValidPaperFinalizationReceipt(
                record.finalizationReceipt, record.recoveryId,
                record.finalizationId, record.expectedOwnerSetSha256,
                record.expectedOwnerCount, record.ownerAccount,
                record.ownerExecutionDomain))))
        {
            reason = "LEASE_STORE_PAPER_FINALIZATION_RECORD_INVALID";
            return false;
        }
        if (!parsedTokens.insert(record.token).second)
        {
            reason = "LEASE_STORE_RECORD_INVALID";
            return false;
        }

        if (record.templateId == "paper" && !ownerState)
        {
            if (cleanupRequest == nullptr)
            {
                reason = hsl5 ?
                    "LEASE_STORE_LEGACY_PAPER_OWNER_MISSING" :
                    "LEASE_STORE_RECORD_INVALID";
                return false;
            }
            if (!terminalCleanupLegacy)
            {
                reason = "LEASE_STORE_RECORD_INVALID";
                return false;
            }
            if (record.issuer != cleanupRequest->expectedIssuer ||
                record.agentId != cleanupRequest->expectedAgentId ||
                record.peerUid != cleanupRequest->expectedPeerUid)
            {
                reason = "LEASE_STORE_TERMINAL_CLEANUP_RECORD_SCOPE_INVALID";
                return false;
            }
            if (record.expiresAtMs > nowMs)
            {
                reason = "LEASE_STORE_TERMINAL_CLEANUP_RECORD_NOT_EXPIRED";
                return false;
            }
            if (!record.predecessorToken.empty() ||
                record.predecessorGeneration != 0 || record.fencePending ||
                record.fenceComplete || !record.fenceReason.empty() ||
                record.recoveryOnly || !record.recoveryCommandId.empty())
            {
                reason = "LEASE_STORE_TERMINAL_CLEANUP_RECORD_STATE_INVALID";
                return false;
            }
            ++retiredRecords;
            continue;
        }
        if (record.templateId == "paper" &&
            (record.ownerAccount.empty() || record.ownerAccount.size() > 128 ||
             record.ownerExecutionDomain.empty() ||
             record.ownerExecutionDomain.size() > 128))
        {
            reason = "LEASE_STORE_RECORD_INVALID";
            return false;
        }
        if (parsedRecords.find(record.token) != parsedRecords.end())
        {
            reason = "LEASE_STORE_RECORD_INVALID";
            return false;
        }
        if (record.templateId == "watch")
            for (std::map<std::string, SessionSupervisorLeaseRecord>::const_iterator
                     existing = parsedRecords.begin();
                 existing != parsedRecords.end(); ++existing)
                if (existing->second.templateId == "watch" &&
                    existing->second.agentId == record.agentId &&
                    existing->second.sessionId == record.sessionId)
                {
                    reason = "LEASE_STORE_WATCH_OWNER_EXISTS";
                    return false;
                }
        parsedRecords[record.token] = record;
    }
    if (cleanupRequest != nullptr && retiredRecords == 0)
    {
        reason = "LEASE_STORE_TERMINAL_CLEANUP_RECORDS_REQUIRED";
        return false;
    }
    // Every active finalization binding covers the complete current PAPER
    // owner set.  Records may be at different pre-seal states during crash
    // recovery, but two group identities can never coexist.
    std::vector<SessionSupervisorLeaseRecord> paperRecords;
    const SessionSupervisorLeaseRecord* group = nullptr;
    bool anySealed = false;
    bool allBoundSealed = true;
    std::string sealedReceiptSha256;
    std::string sealedReceipt;
    std::string finalizationOwnerAccount;
    std::string finalizationOwnerDomain;
    for (std::map<std::string, SessionSupervisorLeaseRecord>::const_iterator it =
             parsedRecords.begin(); it != parsedRecords.end(); ++it)
    {
        if (it->second.templateId != "paper") continue;
        paperRecords.push_back(it->second);
        if (it->second.paperFinalizationState ==
            SessionSupervisorPaperFinalizationState::None)
        {
            allBoundSealed = false;
            continue;
        }
        if (finalizationOwnerAccount.empty())
        {
            finalizationOwnerAccount = it->second.ownerAccount;
            finalizationOwnerDomain = it->second.ownerExecutionDomain;
        }
        else if (it->second.ownerAccount != finalizationOwnerAccount ||
            it->second.ownerExecutionDomain != finalizationOwnerDomain)
        {
            reason = "LEASE_STORE_PAPER_FINALIZATION_SCOPE_MISMATCH";
            return false;
        }
        if (group == nullptr) group = &it->second;
        else if (!SameFinalizationGroup(*group, it->second))
        {
            reason = "LEASE_STORE_PAPER_FINALIZATION_GROUP_MISMATCH";
            return false;
        }
        const bool sealed = it->second.paperFinalizationState ==
            SessionSupervisorPaperFinalizationState::AuditSealed;
        if (sealed && sealedReceiptSha256.empty())
        {
            sealedReceiptSha256 = it->second.finalizationReceiptSha256;
            sealedReceipt = it->second.finalizationReceipt;
        }
        else if (sealed &&
            (it->second.finalizationReceiptSha256 != sealedReceiptSha256 ||
             it->second.finalizationReceipt != sealedReceipt))
        {
            reason = "LEASE_STORE_PAPER_FINALIZATION_RECEIPT_MISMATCH";
            return false;
        }
        anySealed = anySealed || sealed;
        allBoundSealed = allBoundSealed && sealed;
    }
        if (group != nullptr)
    {
        std::string actualOwnerSetSha256;
        bool sameScope = true;
        for (std::size_t i = 0; i < paperRecords.size(); ++i)
            sameScope = sameScope &&
                paperRecords[i].ownerAccount == group->ownerAccount &&
                paperRecords[i].ownerExecutionDomain ==
                    group->ownerExecutionDomain;
        bool allFinalizationRequired = true;
        for (std::size_t i = 0; i < paperRecords.size(); ++i)
            allFinalizationRequired = allFinalizationRequired &&
                paperRecords[i].paperFinalizationRequired;
        if (!sameScope)
        {
            reason = "LEASE_STORE_PAPER_FINALIZATION_SCOPE_MISMATCH";
            return false;
        }
        if (!allFinalizationRequired ||
            paperRecords.size() != group->expectedOwnerCount ||
            !PaperOwnerSetSha256(
                paperRecords, actualOwnerSetSha256, reason) ||
            actualOwnerSetSha256 != group->expectedOwnerSetSha256 ||
            (anySealed && !allBoundSealed))
        {
            if (reason.empty())
                reason = "LEASE_STORE_PAPER_FINALIZATION_GROUP_MISMATCH";
            return false;
        }
    }
    for (std::map<std::string,
             SessionSupervisorPaperFinalizationAck>::const_iterator it =
             parsedAcks.begin(); it != parsedAcks.end(); ++it)
        for (std::size_t i = 0; i < paperRecords.size(); ++i)
            if (paperRecords[i].finalizationId == it->first)
            {
                reason = "LEASE_STORE_PAPER_FINALIZATION_ACK_CONFLICT";
                return false;
            }
    m_records.swap(parsedRecords);
    m_paperFinalizationAcks.swap(parsedAcks);
    if (cleanupResult != nullptr)
        cleanupResult->retiredRecords = retiredRecords;
    reason.clear();
    return true;
}

bool SessionSupervisorLeaseStore::Encrypt(const std::string& plaintext,
    std::string& ciphertext, std::string& nonce, std::string& tag) const
{
    nonce.assign(12, '\0');
    tag.assign(16, '\0');
    ciphertext.assign(plaintext.size() + 16, '\0');
    if (RAND_bytes(reinterpret_cast<unsigned char*>(&nonce[0]), nonce.size()) != 1) return false;
    EVP_CIPHER_CTX* context = EVP_CIPHER_CTX_new();
    if (context == nullptr) return false;
    int outputLength = 0;
    int finalLength = 0;
    int aadLength = 0;
    bool ok = EVP_EncryptInit_ex(context, EVP_aes_256_gcm(), nullptr, nullptr, nullptr) == 1 &&
        EVP_CIPHER_CTX_ctrl(context, EVP_CTRL_GCM_SET_IVLEN, nonce.size(), nullptr) == 1 &&
        EVP_EncryptInit_ex(context, nullptr, nullptr, &m_key[0],
            reinterpret_cast<const unsigned char*>(nonce.data())) == 1 &&
        EVP_EncryptUpdate(context, nullptr, &aadLength,
            reinterpret_cast<const unsigned char*>(kAad), std::strlen(kAad)) == 1 &&
        EVP_EncryptUpdate(context, reinterpret_cast<unsigned char*>(&ciphertext[0]), &outputLength,
            reinterpret_cast<const unsigned char*>(plaintext.data()), plaintext.size()) == 1 &&
        EVP_EncryptFinal_ex(context,
            reinterpret_cast<unsigned char*>(&ciphertext[0]) + outputLength, &finalLength) == 1 &&
        EVP_CIPHER_CTX_ctrl(context, EVP_CTRL_GCM_GET_TAG, tag.size(), &tag[0]) == 1;
    EVP_CIPHER_CTX_free(context);
    if (!ok) return false;
    ciphertext.resize(outputLength + finalLength);
    return true;
}

bool SessionSupervisorLeaseStore::Decrypt(const std::string& ciphertext,
    const std::string& nonce, const std::string& tag, std::string& plaintext) const
{
    plaintext.assign(ciphertext.size() + 16, '\0');
    EVP_CIPHER_CTX* context = EVP_CIPHER_CTX_new();
    if (context == nullptr) return false;
    int outputLength = 0;
    int finalLength = 0;
    int aadLength = 0;
    bool ok = EVP_DecryptInit_ex(context, EVP_aes_256_gcm(), nullptr, nullptr, nullptr) == 1 &&
        EVP_CIPHER_CTX_ctrl(context, EVP_CTRL_GCM_SET_IVLEN, nonce.size(), nullptr) == 1 &&
        EVP_DecryptInit_ex(context, nullptr, nullptr, &m_key[0],
            reinterpret_cast<const unsigned char*>(nonce.data())) == 1 &&
        EVP_DecryptUpdate(context, nullptr, &aadLength,
            reinterpret_cast<const unsigned char*>(kAad), std::strlen(kAad)) == 1 &&
        EVP_DecryptUpdate(context, reinterpret_cast<unsigned char*>(&plaintext[0]), &outputLength,
            reinterpret_cast<const unsigned char*>(ciphertext.data()), ciphertext.size()) == 1 &&
        EVP_CIPHER_CTX_ctrl(context, EVP_CTRL_GCM_SET_TAG, tag.size(),
            const_cast<char*>(tag.data())) == 1 &&
        EVP_DecryptFinal_ex(context,
            reinterpret_cast<unsigned char*>(&plaintext[0]) + outputLength, &finalLength) == 1;
    EVP_CIPHER_CTX_free(context);
    if (!ok) return false;
    plaintext.resize(outputLength + finalLength);
    return true;
}
