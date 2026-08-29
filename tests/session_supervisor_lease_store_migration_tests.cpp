#include "../HeptaTrade/tool_host/session_supervisor_lease_store.h"

#include <cassert>
#include <cerrno>
#include <chrono>
#include <cstring>
#include <fcntl.h>
#include <limits>
#include <openssl/evp.h>
#include <openssl/rand.h>
#include <algorithm>
#include <sstream>
#include <string>
#include <sys/file.h>
#include <sys/stat.h>
#include <sys/wait.h>
#include <unistd.h>
#include <vector>

namespace {

const char* kAad = "HeptaTrader supervisor lease store HSL2";
const char* kIssuer = "hepta.os.bootstrap";
const char* kAgent = "alpha";
const std::uint32_t kPeerUid = 2104;

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

std::string Sha256(const std::string& value)
{
    EVP_MD_CTX* context = EVP_MD_CTX_new();
    assert(context != nullptr);
    unsigned char digest[EVP_MAX_MD_SIZE];
    unsigned int length = 0;
    assert(EVP_DigestInit_ex(context, EVP_sha256(), nullptr) == 1);
    assert(EVP_DigestUpdate(context, value.data(), value.size()) == 1);
    assert(EVP_DigestFinal_ex(context, digest, &length) == 1);
    EVP_MD_CTX_free(context);
    assert(length == 32);
    return HexEncode(std::string(
        reinterpret_cast<const char*>(digest), length));
}

std::string EncryptEnvelope(
    const std::string& plaintext, const std::string& key)
{
    assert(key.size() == 32);
    std::string nonce(12, '\0');
    std::string tag(16, '\0');
    std::string ciphertext(plaintext.size() + 16, '\0');
    assert(RAND_bytes(reinterpret_cast<unsigned char*>(&nonce[0]),
        static_cast<int>(nonce.size())) == 1);
    EVP_CIPHER_CTX* context = EVP_CIPHER_CTX_new();
    assert(context != nullptr);
    int outputLength = 0;
    int finalLength = 0;
    int aadLength = 0;
    assert(EVP_EncryptInit_ex(
        context, EVP_aes_256_gcm(), nullptr, nullptr, nullptr) == 1);
    assert(EVP_CIPHER_CTX_ctrl(context, EVP_CTRL_GCM_SET_IVLEN,
        static_cast<int>(nonce.size()), nullptr) == 1);
    assert(EVP_EncryptInit_ex(context, nullptr, nullptr,
        reinterpret_cast<const unsigned char*>(key.data()),
        reinterpret_cast<const unsigned char*>(nonce.data())) == 1);
    assert(EVP_EncryptUpdate(context, nullptr, &aadLength,
        reinterpret_cast<const unsigned char*>(kAad),
        static_cast<int>(std::strlen(kAad))) == 1);
    assert(EVP_EncryptUpdate(context,
        reinterpret_cast<unsigned char*>(&ciphertext[0]), &outputLength,
        reinterpret_cast<const unsigned char*>(plaintext.data()),
        static_cast<int>(plaintext.size())) == 1);
    assert(EVP_EncryptFinal_ex(context,
        reinterpret_cast<unsigned char*>(&ciphertext[0]) + outputLength,
        &finalLength) == 1);
    assert(EVP_CIPHER_CTX_ctrl(context, EVP_CTRL_GCM_GET_TAG,
        static_cast<int>(tag.size()), &tag[0]) == 1);
    EVP_CIPHER_CTX_free(context);
    ciphertext.resize(static_cast<std::size_t>(outputLength + finalLength));
    return "HSL2\n" + HexEncode(nonce) + "\n" + HexEncode(tag) + "\n" +
        HexEncode(ciphertext) + "\n";
}

bool WriteAll(int fd, const std::string& value)
{
    std::size_t offset = 0;
    while (offset < value.size())
    {
        const ssize_t count =
            ::write(fd, value.data() + offset, value.size() - offset);
        if (count < 0 && errno == EINTR) continue;
        if (count <= 0) return false;
        offset += static_cast<std::size_t>(count);
    }
    return true;
}

void WriteFile(const std::string& path, const std::string& value, mode_t mode)
{
    const int fd = ::open(path.c_str(), O_WRONLY | O_CREAT | O_EXCL |
        O_CLOEXEC, mode);
    assert(fd >= 0);
    assert(::fchmod(fd, mode) == 0);
    assert(WriteAll(fd, value));
    assert(::fsync(fd) == 0);
    assert(::close(fd) == 0);
}

std::string ReadFile(const std::string& path)
{
    const int fd = ::open(path.c_str(), O_RDONLY | O_CLOEXEC | O_NOFOLLOW);
    assert(fd >= 0);
    struct stat before;
    assert(::fstat(fd, &before) == 0 && S_ISREG(before.st_mode));
    std::string value;
    char buffer[8192];
    while (true)
    {
        const ssize_t count = ::read(fd, buffer, sizeof(buffer));
        if (count < 0 && errno == EINTR) continue;
        assert(count >= 0);
        if (count == 0) break;
        value.append(buffer, static_cast<std::size_t>(count));
    }
    struct stat after;
    assert(::fstat(fd, &after) == 0);
    assert(before.st_dev == after.st_dev && before.st_ino == after.st_ino);
    assert(before.st_size == after.st_size);
    assert(static_cast<std::uint64_t>(after.st_size) == value.size());
    assert(::close(fd) == 0);
    return value;
}

std::uint64_t NowMs()
{
    return static_cast<std::uint64_t>(
        std::chrono::duration_cast<std::chrono::milliseconds>(
            std::chrono::system_clock::now().time_since_epoch()).count());
}

std::string Hsl5Record(const SessionSupervisorLeaseRecord& record)
{
    std::ostringstream output;
    output << HexEncode(record.templateId) << '\t'
           << HexEncode(record.issuer) << '\t'
           << HexEncode(record.token) << '\t'
           << HexEncode(record.agentId) << '\t'
           << HexEncode(record.sessionId) << '\t'
           << record.peerUid << '\t' << record.expiresAtMs << '\t'
           << record.leaseGeneration << '\t'
           << HexEncode(record.predecessorToken) << '\t'
           << record.predecessorGeneration << '\t'
           << (record.fencePending ? 1 : 0) << '\t'
           << (record.fenceComplete ? 1 : 0) << '\t'
           << HexEncode(record.fenceReason) << '\t'
           << (record.recoveryOnly ? 1 : 0) << '\t'
           << HexEncode(record.recoveryCommandId) << '\n';
    return output.str();
}

std::string Hsl4Record(const SessionSupervisorLeaseRecord& record)
{
    std::ostringstream output;
    output << HexEncode(record.templateId) << '\t'
           << HexEncode(record.issuer) << '\t'
           << HexEncode(record.token) << '\t'
           << HexEncode(record.agentId) << '\t'
           << HexEncode(record.sessionId) << '\t'
           << record.peerUid << '\t' << record.expiresAtMs << '\t'
           << record.leaseGeneration << '\t'
           << HexEncode(record.predecessorToken) << '\t'
           << record.predecessorGeneration << '\t'
           << (record.fencePending ? 1 : 0) << '\t'
           << (record.fenceComplete ? 1 : 0) << '\t'
           << HexEncode(record.fenceReason) << '\n';
    return output.str();
}

std::string Hsl6Record(const SessionSupervisorLeaseRecord& record)
{
    std::ostringstream output;
    output << HexEncode(record.templateId) << '\t'
           << HexEncode(record.issuer) << '\t'
           << HexEncode(record.token) << '\t'
           << HexEncode(record.agentId) << '\t'
           << HexEncode(record.sessionId) << '\t'
           << record.peerUid << '\t' << record.expiresAtMs << '\t'
           << record.leaseGeneration << '\t'
           << HexEncode(record.predecessorToken) << '\t'
           << record.predecessorGeneration << '\t'
           << (record.fencePending ? 1 : 0) << '\t'
           << (record.fenceComplete ? 1 : 0) << '\t'
           << HexEncode(record.fenceReason) << '\t'
           << (record.recoveryOnly ? 1 : 0) << '\t'
           << HexEncode(record.recoveryCommandId) << '\t'
           << HexEncode(record.ownerAccount) << '\t'
           << HexEncode(record.ownerExecutionDomain) << '\n';
    return output.str();
}

std::string Hsl7Record(const SessionSupervisorLeaseRecord& record)
{
    std::ostringstream output;
    output << "R\t" << HexEncode(record.templateId) << '\t'
           << HexEncode(record.issuer) << '\t'
           << HexEncode(record.token) << '\t'
           << HexEncode(record.agentId) << '\t'
           << HexEncode(record.sessionId) << '\t'
           << record.peerUid << '\t' << record.expiresAtMs << '\t'
           << record.leaseGeneration << '\t'
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
           << static_cast<std::uint32_t>(record.paperFinalizationState)
           << '\t' << HexEncode(record.recoveryId) << '\t'
           << HexEncode(record.finalizationId) << '\t'
           << HexEncode(record.expectedOwnerSetSha256) << '\t'
           << record.expectedOwnerCount << '\t'
           << HexEncode(record.ownerTokenSha256) << '\t'
           << HexEncode(record.finalizationReceiptSha256) << '\t'
           << HexEncode(record.finalizationReceipt) << '\n';
    return output.str();
}

std::string Hsl7Ack(const SessionSupervisorPaperFinalizationAck& ack)
{
    std::ostringstream output;
    output << "A\t" << HexEncode(ack.recoveryId) << '\t'
           << HexEncode(ack.finalizationId) << '\t'
           << HexEncode(ack.expectedOwnerSetSha256) << '\t'
           << ack.expectedOwnerCount << '\t'
           << HexEncode(ack.receiptSha256) << '\t'
           << HexEncode(ack.receipt) << '\t'
           << HexEncode(ack.acknowledgingOwnerTokenSha256) << '\t'
           << ack.acknowledgingOwnerGeneration << '\n';
    return output.str();
}

std::string Hsl8Ack(const SessionSupervisorPaperFinalizationAck& ack)
{
    std::ostringstream output;
    output << "A\t" << HexEncode(ack.recoveryId) << '\t'
           << HexEncode(ack.finalizationId) << '\t'
           << HexEncode(ack.expectedOwnerSetSha256) << '\t'
           << ack.expectedOwnerCount << '\t'
           << HexEncode(ack.receiptSha256) << '\t'
           << HexEncode(ack.receipt) << '\t'
           << HexEncode(ack.terminalReceiptSha256) << '\t'
           << HexEncode(ack.terminalReceipt) << '\t'
           << HexEncode(ack.acknowledgingOwnerTokenSha256) << '\t'
           << ack.acknowledgingOwnerGeneration << '\t'
           << HexEncode(ack.acknowledgingOwnerIssuer) << '\t'
           << HexEncode(ack.terminalizingOwnerAgentId) << '\t'
           << HexEncode(ack.terminalizingOwnerSessionId) << '\t'
           << HexEncode(ack.terminalizingOwnerAccount) << '\t'
           << HexEncode(ack.terminalizingOwnerExecutionDomain) << '\n';
    return output.str();
}

std::string OwnerTokenSha256(const std::string& token)
{
    return "sha256:" + Sha256(token + "\n");
}

std::string OwnerSetCanonical(
    const std::vector<SessionSupervisorLeaseRecord>& records)
{
    std::vector<std::string> owners;
    for (std::size_t i = 0; i < records.size(); ++i)
        if (records[i].templateId == "paper")
            owners.push_back(OwnerTokenSha256(records[i].token) + "\t" +
                std::to_string(records[i].leaseGeneration) + "\t" +
                HexEncode(records[i].ownerAccount) + "\t" +
                HexEncode(records[i].ownerExecutionDomain) + "\n");
    std::sort(owners.begin(), owners.end());
    std::string canonical;
    for (std::size_t i = 0; i < owners.size(); ++i)
        canonical += owners[i];
    return canonical;
}

std::string FinalizationReceipt(
    const std::vector<SessionSupervisorLeaseRecord>& records,
    const std::string& recoveryId,
    const std::string& finalizationId,
    const std::string& ownerSetSha256)
{
    assert(!records.empty());
    const std::string canonical = OwnerSetCanonical(records);
    std::ostringstream receipt;
    receipt << "schema=hepta.paper-session-finalization-receipt.v1\n"
            << "version=1\n"
            << "status=AUDIT_SEALED\n"
            << "recovery_id=" << recoveryId << '\n'
            << "finalization_id=" << finalizationId << '\n'
            << "expected_owner_set_sha256=" << ownerSetSha256 << '\n'
            << "expected_owner_count=" << records.size() << '\n'
            << "owner_set_canonical_hex=" << HexEncode(canonical) << '\n'
            << "owner_account=" << records[0].ownerAccount << '\n'
            << "owner_execution_domain="
            << records[0].ownerExecutionDomain << '\n'
            << "execution_service_epoch=execution-epoch-hsl7-test\n"
            << "execution_service_fencing_generation=17\n"
            << "broker_connection_epoch=23\n"
            << "broker_active_generation=29\n"
            << "broker_terminal_generation=31\n"
            << "broker_risk_generation=37\n"
            << "broker_account_generation=41\n"
            << "broker_position_generation=43\n"
            << "broker_fx_cash_generation=47\n"
            << "broker_exposure_generation=0\n"
            << "broker_terminal_exposure_generation=0\n"
            << "broker_risk_absorbed_exposure_generation=0\n"
            << "broker_global_active_order_count=0\n"
            << "owner_active_order_count=0\n"
            << "owner_uncertain_command_count=0\n"
            << "broker_post_fill_risk_reconciliation_pending=0\n"
            << "broker_recovery_audit_barrier_complete=1\n"
            << "broker_recovery_audit_new_connection_epoch_required=0\n"
            << "broker_position_quantity=0\n"
            << "broker_gross_absolute_position=0\n"
            << "paper_only=1\n"
            << "live_authorized=0\n";
    return receipt.str();
}

std::string TerminalAckReceipt(
    const std::vector<SessionSupervisorLeaseRecord>& records,
    const std::string& recoveryId,
    const std::string& finalizationId,
    const std::string& ownerSetSha256,
    const std::string& preliminaryReceiptSha256)
{
    assert(!records.empty());
    const std::string canonical = OwnerSetCanonical(records);
    std::vector<SessionSupervisorLeaseRecord> sorted(records);
    std::sort(sorted.begin(), sorted.end(),
        [](const SessionSupervisorLeaseRecord& left,
           const SessionSupervisorLeaseRecord& right) {
            return OwnerTokenSha256(left.token) < OwnerTokenSha256(right.token);
        });
    const SessionSupervisorLeaseRecord& terminalOwner = sorted.front();
    const std::string digest = "sha256:" + std::string(64, 'a');
    std::ostringstream receipt;
    receipt << "schema=hepta.paper-session-terminal-ack-receipt.v3\n"
            << "version=3\n"
            << "status=TERMINAL_ACKED\n"
            << "terminal_proof_kind=POST_CUTOFF_SIGNED_ACCOUNT_WITNESS_V1\n"
            << "recovery_id=" << recoveryId << '\n'
            << "finalization_id=" << finalizationId << '\n'
            << "campaign_id=campaign-hsl8-test\n"
            << "cycle_id=cycle-hsl8-test\n"
            << "expected_owner_set_sha256=" << ownerSetSha256 << '\n'
            << "expected_owner_count=" << records.size() << '\n'
            << "owner_set_canonical_hex=" << HexEncode(canonical) << '\n'
            << "preliminary_finalization_receipt_sha256="
            << preliminaryReceiptSha256 << '\n'
            << "owner_agent_id=" << terminalOwner.agentId << '\n'
            << "owner_session_id=" << terminalOwner.sessionId << '\n'
            << "owner_account=" << records[0].ownerAccount << '\n'
            << "owner_execution_domain="
            << records[0].ownerExecutionDomain << '\n'
            << "account_id_sha256=sha256:"
            << Sha256(records[0].ownerAccount) << '\n'
            << "execution_service_epoch=execution-epoch-hsl7-test\n"
            << "execution_service_fencing_generation=17\n"
            << "recovery_ingress_fence="
            << terminalOwner.leaseGeneration << '\n'
            << "terminalization_generation=1\n"
            << "terminalizing_latch_sha256=" << digest << '\n'
            << "terminal_external_halt_latch_sha256=" << digest << '\n'
            << "transport_cutoff_receipt_file_sha256=" << digest << '\n'
            << "transport_cutoff_receipt_body_sha256=" << digest << '\n'
            << "post_cutoff_terminal_witness_file_sha256=" << digest << '\n'
            << "post_cutoff_terminal_witness_body_sha256=" << digest << '\n'
            << "provider_trust_policy_file_sha256=" << digest << '\n'
            << "provider_trust_policy_body_sha256=" << digest << '\n'
            << "provider_id=reviewed-provider-hsl8\n"
            << "provider_capability=ACCOUNT_WIDE_ATOMIC_OR_CAUSAL_POST_CUTOFF_READ_ONLY_V1\n"
            << "signed_account_payload_sha256=" << digest << '\n'
            << "signed_account_signature_sha256=" << digest << '\n'
            << "host_boot_id=11111111-1111-1111-1111-111111111111\n"
			<< "egress_publisher_pid=4102\n"
			<< "egress_publisher_start_ticks=99123\n"
            << "egress_policy_generation=23\n"
            << "egress_policy_sha256=" << digest << '\n'
            << "query_started_after_challenge=1\n"
            << "observed_after_cutoff=1\n"
            << "snapshot_consistency=CAUSAL_WATERMARK\n"
            << "causal_watermark_dominates_cutoff=1\n"
            << "causal_watermark_dominates_all_mutations=1\n"
            << "account_queries_complete=1\n"
            << "active_orders_complete=1\n"
            << "completed_orders_complete=1\n"
            << "executions_complete=1\n"
            << "positions_complete=1\n"
            << "cash_fx_complete=1\n"
            << "risk_complete=1\n"
            << "known_mutation_command_set_sha256=" << digest << '\n'
            << "known_mutation_command_count=2\n"
            << "known_correlation_set_sha256=" << digest << '\n'
            << "known_correlation_count=2\n"
            << "all_known_mutation_commands_settled=1\n"
            << "settled_mutation_command_count=2\n"
            << "unknown_mutation_command_count=0\n"
            << "unresolved_mutation_command_count=0\n"
            << "unknown_active_order_count=0\n"
            << "active_order_count=0\n"
            << "position_count=0\n"
            << "nonzero_cash_fx_count=0\n"
            << "gross_absolute_position=0\n"
            << "gross_fx_exposure=0\n"
            << "gross_risk=0\n"
            << "mutation_connector_count=0\n"
            << "broker_socket_count=0\n"
            << "broker_process_count=0\n"
            << "broker_credential_count=0\n"
            << "execution_service_inactive=1\n"
            << "paper_units_inactive=1\n"
            << "execution_mutation_gate_closed=1\n"
            << "broker_transport_connected=0\n"
            << "broker_reconnect_permitted=0\n"
            << "read_only_authority=1\n"
            << "mutation_attempted=0\n"
            << "paper_authorized=0\n"
            << "live_authorized=0\n"
            << "mutation_authorized=0\n"
            << "direct_broker_access=0\n"
            << "order_submission_authorized=0\n"
            << "order_authorized=0\n"
            << "paper_only=1\n"
            << "authority_granted=0\n"
            << "terminal_external_halt_latch_durable=1\n"
            << "terminal_witness_durable=1\n"
            << "current_host_boundary_verified=1\n"
            << "terminal_evidence_file_sha256=" << digest << '\n'
            << "terminal_evidence_body_sha256=" << digest << '\n';
    return receipt.str();
}

SessionSupervisorLeaseRecord Paper(const std::string& token,
    std::uint64_t expiresAtMs)
{
    SessionSupervisorLeaseRecord record;
    record.templateId = "paper";
    record.issuer = kIssuer;
    record.token = token;
    record.agentId = kAgent;
    record.sessionId = "legacy-paper-session";
    record.peerUid = kPeerUid;
    record.expiresAtMs = expiresAtMs;
    record.leaseGeneration = 1;
    return record;
}

SessionSupervisorLeaseRecord OwnedPaper(
    const std::string& token,
    const std::string& agentId,
    const std::string& sessionId)
{
    SessionSupervisorLeaseRecord record =
        Paper(token, NowMs() + 600000);
    record.issuer = "hepta.os.finalization";
    record.agentId = agentId;
    record.sessionId = sessionId;
    record.peerUid = static_cast<std::uint32_t>(::geteuid());
    record.ownerAccount = "DU12345";
    record.ownerExecutionDomain = "PAPER:alpha";
    return record;
}

SessionSupervisorLeaseRecord Watch(const std::string& token)
{
    SessionSupervisorLeaseRecord record;
    record.templateId = "watch";
    record.issuer = "hepta.os.uid";
    record.token = token;
    record.agentId = "watch-agent";
    record.sessionId = "watch-session";
    record.peerUid = 2103;
    record.expiresAtMs = NowMs() + 600000;
    record.leaseGeneration = 7;
    return record;
}

class Fixture
{
public:
    Fixture()
    {
        char pattern[] = "/tmp/hepta-hsl5-cleanup-XXXXXX";
        char* created = ::mkdtemp(pattern);
        assert(created != nullptr);
        directory = created;
        store = directory + "/session-leases.hsl2";
        key = directory + "/lease.key";
        backupDirectory = directory + "/privileged-backup";
        assert(::mkdir(backupDirectory.c_str(), 0700) == 0);
        backup = backupDirectory +
            "/session-leases.hsl2.terminal-cleanup.hsl5.backup";
        lockDirectory = directory + "/trusted-lock";
        assert(::mkdir(lockDirectory.c_str(), 0711) == 0);
        assert(::chmod(lockDirectory.c_str(), 0711) == 0);
        cleanupLock = lockDirectory + "/session-leases.lock";
        WriteFile(cleanupLock, std::string(), 0644);
        WriteFile(key, keyBytes, 0400);
    }

    ~Fixture()
    {
        ::unlink((store + ".tmp." +
            std::to_string(static_cast<long long>(::getpid()))).c_str());
        ::unlink(backup.c_str());
        ::rmdir(backupDirectory.c_str());
        ::unlink(cleanupLock.c_str());
        ::rmdir(lockDirectory.c_str());
        ::unlink(store.c_str());
        ::unlink(key.c_str());
        ::rmdir(directory.c_str());
    }

    std::string WriteHsl5(
        const std::vector<SessionSupervisorLeaseRecord>& records,
        mode_t mode = 0600)
    {
        const std::string encoded = EncodeHsl5(records);
        WriteFile(store, encoded, mode);
        return encoded;
    }

    std::string WriteHsl4(
        const std::vector<SessionSupervisorLeaseRecord>& records,
        mode_t mode = 0600)
    {
        const std::string encoded = EncodeHsl4(records);
        WriteFile(store, encoded, mode);
        return encoded;
    }

    std::string EncodeHsl4(
        const std::vector<SessionSupervisorLeaseRecord>& records) const
    {
        std::string plaintext = "HSL4\n";
        for (std::size_t i = 0; i < records.size(); ++i)
            plaintext += Hsl4Record(records[i]);
        return EncryptEnvelope(plaintext, keyBytes);
    }

    std::string EncodeHsl5(
        const std::vector<SessionSupervisorLeaseRecord>& records) const
    {
        std::string plaintext = "HSL5\n";
        for (std::size_t i = 0; i < records.size(); ++i)
            plaintext += Hsl5Record(records[i]);
        return EncryptEnvelope(plaintext, keyBytes);
    }

    void ReplaceStore(const std::string& encoded)
    {
        const std::string replacement = store + ".external-replacement";
        WriteFile(replacement, encoded, 0600);
        assert(::rename(replacement.c_str(), store.c_str()) == 0);
    }

    SessionSupervisorLegacyPaperCleanupRequest Request(
        const std::string& encoded) const
    {
        SessionSupervisorLegacyPaperCleanupRequest request;
        request.expectedIssuer = kIssuer;
        request.expectedAgentId = kAgent;
        request.expectedPeerUid = kPeerUid;
        request.expectedPreStoreSha256 = Sha256(encoded);
        request.backupPath = backup;
        request.cleanupLockPath = cleanupLock;
        request.expectedLockUid = static_cast<std::uint32_t>(::geteuid());
        request.expectedLockGid = static_cast<std::uint32_t>(::getegid());
        request.expectedSourceUid = static_cast<std::uint32_t>(::geteuid());
        request.expectedSourceGid = static_cast<std::uint32_t>(::getegid());
        request.expectedSourceMode = 0600;
        request.expectedKeyUid = static_cast<std::uint32_t>(::geteuid());
        request.expectedKeyGid = static_cast<std::uint32_t>(::getegid());
        request.expectedKeyMode = 0400;
        request.expectedKeySha256 = Sha256(keyBytes);
        return request;
    }

    bool Init(SessionSupervisorLeaseStore& leaseStore,
        std::string& reason, const std::string& storePath = std::string(),
        const std::string& keyPath = std::string()) const
    {
        return leaseStore.Init(storePath.empty() ? store : storePath,
            keyPath.empty() ? key : keyPath, cleanupLock,
            static_cast<std::uint32_t>(::geteuid()),
            static_cast<std::uint32_t>(::getegid()), reason);
    }

    std::string directory;
    std::string store;
    std::string key;
    std::string backup;
    std::string backupDirectory;
    std::string lockDirectory;
    std::string cleanupLock;
    const std::string keyBytes = std::string(32, 'K');
};

void TestNormalInitRejectsOwnerlessHsl5Paper()
{
    Fixture fixture;
    const std::string encoded = fixture.WriteHsl5({
        Paper("normal-init-legacy-paper-token-0001", NowMs() - 1000)});
    SessionSupervisorLeaseStore store;
    std::string reason;
    assert(!fixture.Init(store, reason));
    assert(reason == "LEASE_STORE_LEGACY_PAPER_OWNER_MISSING");
    assert(ReadFile(fixture.store) == encoded);
    assert(::access(fixture.backup.c_str(), F_OK) != 0);
}

void TestNormalInitRejectsOwnerlessHsl4Paper()
{
    Fixture fixture;
    const std::string encoded = fixture.WriteHsl4({
        Paper("normal-init-hsl4-paper-token-0001", NowMs() - 1000)});
    SessionSupervisorLeaseStore store;
    std::string reason;
    assert(!fixture.Init(store, reason));
    assert(reason == "LEASE_STORE_RECORD_INVALID");
    assert(ReadFile(fixture.store) == encoded);
    assert(::access(fixture.backup.c_str(), F_OK) != 0);
}

void TestHappyPathPreservesWatchMetadataAndIsIdempotent()
{
    Fixture fixture;
    const SessionSupervisorLeaseRecord watch =
        Watch("preserved-watch-session-token-0001");
    const std::string encoded = fixture.WriteHsl5({
        Paper("expired-legacy-paper-token-0001", NowMs() - 5000),
        Paper("expired-legacy-paper-token-0002", NowMs() - 4000),
        watch}, 0600);
    struct stat before;
    assert(::lstat(fixture.store.c_str(), &before) == 0);

    SessionSupervisorLeaseStore store;
    SessionSupervisorLegacyPaperCleanupResult result;
    std::string reason;
    const SessionSupervisorLegacyPaperCleanupRequest request =
        fixture.Request(encoded);
    assert(store.MigrateHsl5PaperForTerminalCleanup(
        fixture.store, fixture.key, request, result, reason));
    assert(result.retiredRecords == 2);
    assert(result.preStoreSha256 == Sha256(encoded));
    assert(result.postStoreSha256 == Sha256(ReadFile(fixture.store)));
    assert(!result.alreadyMigrated);
    assert(ReadFile(fixture.backup) == encoded);

    struct stat after;
    struct stat backup;
    assert(::lstat(fixture.store.c_str(), &after) == 0);
    assert(after.st_uid == before.st_uid && after.st_gid == before.st_gid);
    assert((after.st_mode & 07777) == (before.st_mode & 07777));
    assert(::lstat(fixture.backup.c_str(), &backup) == 0);
    assert((backup.st_mode & 0777) == 0400);
    assert(backup.st_uid == ::geteuid() && backup.st_gid == ::getegid());

    {
        SessionSupervisorLeaseStore reopened;
        assert(fixture.Init(reopened, reason));
        const std::vector<SessionSupervisorLeaseRecord> records =
            reopened.List();
        assert(records.size() == 1);
        assert(records[0].templateId == "watch");
        assert(records[0].token == watch.token);
        assert(records[0].agentId == watch.agentId);
        assert(records[0].sessionId == watch.sessionId);
        assert(records[0].expiresAtMs == watch.expiresAtMs);
        assert(!records[0].paperFinalizationRequired);
        assert(records[0].paperFinalizationState ==
            SessionSupervisorPaperFinalizationState::None);
    }

    const std::string migrated = ReadFile(fixture.store);
    SessionSupervisorLeaseStore retry;
    SessionSupervisorLegacyPaperCleanupResult retryResult;
    assert(retry.MigrateHsl5PaperForTerminalCleanup(
        fixture.store, fixture.key, request, retryResult, reason));
    assert(retryResult.retiredRecords == 2);
    assert(retryResult.preStoreSha256 == Sha256(encoded));
    assert(retryResult.postStoreSha256 == Sha256(migrated));
    assert(retryResult.alreadyMigrated);
    assert(ReadFile(fixture.store) == migrated);
}

void TestHsl4HappyPathPreservesWatchMetadataAndIsIdempotent()
{
    Fixture fixture;
    const SessionSupervisorLeaseRecord watch =
        Watch("preserved-hsl4-watch-session-token-0001");
    const std::string encoded = fixture.WriteHsl4({
        Paper("expired-hsl4-paper-token-0001", NowMs() - 5000), watch});
    const SessionSupervisorLegacyPaperCleanupRequest request =
        fixture.Request(encoded);
    SessionSupervisorLeaseStore store;
    SessionSupervisorLegacyPaperCleanupResult result;
    std::string reason;
    assert(store.MigrateHsl5PaperForTerminalCleanup(
        fixture.store, fixture.key, request, result, reason));
    assert(result.retiredRecords == 1);
    assert(result.preStoreSha256 == Sha256(encoded));
    assert(result.postStoreSha256 == Sha256(ReadFile(fixture.store)));
    assert(!result.alreadyMigrated);
    assert(ReadFile(fixture.backup) == encoded);

    {
        SessionSupervisorLeaseStore reopened;
        assert(fixture.Init(reopened, reason));
        const std::vector<SessionSupervisorLeaseRecord> records =
            reopened.List();
        assert(records.size() == 1);
        assert(records[0].templateId == "watch");
        assert(records[0].token == watch.token);
    }

    const std::string migrated = ReadFile(fixture.store);
    SessionSupervisorLeaseStore retry;
    SessionSupervisorLegacyPaperCleanupResult retryResult;
    assert(retry.MigrateHsl5PaperForTerminalCleanup(
        fixture.store, fixture.key, request, retryResult, reason));
    assert(retryResult.retiredRecords == 1);
    assert(retryResult.preStoreSha256 == Sha256(encoded));
    assert(retryResult.postStoreSha256 == Sha256(migrated));
    assert(retryResult.alreadyMigrated);
    assert(ReadFile(fixture.store) == migrated);
}

void TestHsl4UnsafeRecordsRejectWithoutRewrite()
{
    {
        Fixture fixture;
        const std::string encoded = fixture.WriteHsl4({
            Paper("unexpired-hsl4-paper-token-0001", NowMs() + 60000)});
        SessionSupervisorLeaseStore store;
        SessionSupervisorLegacyPaperCleanupResult result;
        std::string reason;
        assert(!store.MigrateHsl5PaperForTerminalCleanup(
            fixture.store, fixture.key, fixture.Request(encoded),
            result, reason));
        assert(reason ==
            "LEASE_STORE_TERMINAL_CLEANUP_RECORD_NOT_EXPIRED");
        assert(ReadFile(fixture.store) == encoded);
        assert(::access(fixture.backup.c_str(), F_OK) != 0);
    }

    {
        Fixture fixture;
        SessionSupervisorLeaseRecord fenced =
            Paper("fenced-hsl4-paper-token-0001", NowMs() - 1000);
        fenced.fencePending = true;
        fenced.fenceReason = "session_expired";
        const std::string encoded = fixture.WriteHsl4({fenced});
        SessionSupervisorLeaseStore store;
        SessionSupervisorLegacyPaperCleanupResult result;
        std::string reason;
        assert(!store.MigrateHsl5PaperForTerminalCleanup(
            fixture.store, fixture.key, fixture.Request(encoded),
            result, reason));
        assert(reason ==
            "LEASE_STORE_TERMINAL_CLEANUP_RECORD_STATE_INVALID");
        assert(ReadFile(fixture.store) == encoded);
        assert(::access(fixture.backup.c_str(), F_OK) != 0);
    }


    {
        Fixture fixture;
        SessionSupervisorLeaseRecord wrongScope =
            Paper("wrong-scope-hsl4-paper-token-0001", NowMs() - 1000);
        wrongScope.peerUid = kPeerUid + 1;
        const std::string encoded = fixture.WriteHsl4({wrongScope});
        SessionSupervisorLeaseStore store;
        SessionSupervisorLegacyPaperCleanupResult result;
        std::string reason;
        assert(!store.MigrateHsl5PaperForTerminalCleanup(
            fixture.store, fixture.key, fixture.Request(encoded),
            result, reason));
        assert(reason ==
            "LEASE_STORE_TERMINAL_CLEANUP_RECORD_SCOPE_INVALID");
        assert(ReadFile(fixture.store) == encoded);
        assert(::access(fixture.backup.c_str(), F_OK) != 0);
    }

    {
        Fixture fixture;
        const std::string encoded = fixture.WriteHsl4({});
        SessionSupervisorLeaseStore store;
        SessionSupervisorLegacyPaperCleanupResult result;
        std::string reason;
        assert(!store.MigrateHsl5PaperForTerminalCleanup(
            fixture.store, fixture.key, fixture.Request(encoded),
            result, reason));
        assert(reason ==
            "LEASE_STORE_TERMINAL_CLEANUP_RECORDS_REQUIRED");
        assert(ReadFile(fixture.store) == encoded);
        assert(::access(fixture.backup.c_str(), F_OK) != 0);
    }
}

void TestNonHsl4OrHsl5CleanupSourcesRejectWithoutRewrite()
{
    const char* versions[] = {
        "HSL1\n", "HSL2\n", "HSL3\n", "HSL6\n"};
    for (std::size_t i = 0; i < sizeof(versions) / sizeof(versions[0]); ++i)
    {
        Fixture fixture;
        const std::string encoded =
            EncryptEnvelope(versions[i], fixture.keyBytes);
        WriteFile(fixture.store, encoded, 0600);
        SessionSupervisorLeaseStore store;
        SessionSupervisorLegacyPaperCleanupResult result;
        std::string reason;
        assert(!store.MigrateHsl5PaperForTerminalCleanup(
            fixture.store, fixture.key, fixture.Request(encoded),
            result, reason));
        assert(reason ==
            "LEASE_STORE_TERMINAL_CLEANUP_SOURCE_INVALID");
        assert(ReadFile(fixture.store) == encoded);
        assert(::access(fixture.backup.c_str(), F_OK) != 0);
    }
}

void ExpectRejected(SessionSupervisorLeaseRecord paper,
    const std::string& expectedReason)
{
    Fixture fixture;
    const std::string encoded = fixture.WriteHsl5({paper});
    SessionSupervisorLeaseStore store;
    SessionSupervisorLegacyPaperCleanupResult result;
    std::string reason;
    assert(!store.MigrateHsl5PaperForTerminalCleanup(
        fixture.store, fixture.key, fixture.Request(encoded), result, reason));
    assert(reason == expectedReason);
    assert(result.retiredRecords == 0);
    assert(ReadFile(fixture.store) == encoded);
    assert(::access(fixture.backup.c_str(), F_OK) != 0);
}

void TestUnexpiredScopeAndFlaggedRecordsRejectWithoutRewrite()
{
    ExpectRejected(Paper("unexpired-legacy-paper-token-0001", NowMs() + 60000),
        "LEASE_STORE_TERMINAL_CLEANUP_RECORD_NOT_EXPIRED");

    SessionSupervisorLeaseRecord wrongIssuer =
        Paper("wrong-issuer-paper-session-token-0001", NowMs() - 1000);
    wrongIssuer.issuer = "unknown.issuer";
    ExpectRejected(wrongIssuer,
        "LEASE_STORE_TERMINAL_CLEANUP_RECORD_SCOPE_INVALID");
    SessionSupervisorLeaseRecord wrongAgent =
        Paper("wrong-agent-paper-session-token-0001", NowMs() - 1000);
    wrongAgent.agentId = "beta";
    ExpectRejected(wrongAgent,
        "LEASE_STORE_TERMINAL_CLEANUP_RECORD_SCOPE_INVALID");
    SessionSupervisorLeaseRecord wrongPeer =
        Paper("wrong-peer-paper-session-token-0001", NowMs() - 1000);
    wrongPeer.peerUid = kPeerUid + 1;
    ExpectRejected(wrongPeer,
        "LEASE_STORE_TERMINAL_CLEANUP_RECORD_SCOPE_INVALID");

    SessionSupervisorLeaseRecord fenced =
        Paper("fenced-legacy-paper-session-token-0001", NowMs() - 1000);
    fenced.fencePending = true;
    fenced.fenceReason = "session_expired";
    ExpectRejected(fenced,
        "LEASE_STORE_TERMINAL_CLEANUP_RECORD_STATE_INVALID");

    SessionSupervisorLeaseRecord recovering =
        Paper("recovery-legacy-paper-session-token-0001", NowMs() - 1000);
    recovering.recoveryOnly = true;
    recovering.recoveryCommandId = "terminal-command";
    ExpectRejected(recovering,
        "LEASE_STORE_TERMINAL_CLEANUP_RECORD_STATE_INVALID");

    SessionSupervisorLeaseRecord predecessor =
        Paper("predecessor-paper-session-token-0001", NowMs() - 1000);
    predecessor.predecessorToken = "previous-paper-session-token-0001";
    predecessor.predecessorGeneration = 1;
    predecessor.leaseGeneration = 2;
    predecessor.fencePending = true;
    predecessor.fenceReason = "session_revoked";
    ExpectRejected(predecessor,
        "LEASE_STORE_TERMINAL_CLEANUP_RECORD_STATE_INVALID");
}

void TestPreHashAndHeldLockFailClosed()
{
    Fixture fixture;
    const std::string encoded = fixture.WriteHsl5({
        Paper("prehash-legacy-paper-session-token-0001", NowMs() - 1000)});
    SessionSupervisorLegacyPaperCleanupRequest request =
        fixture.Request(encoded);
    request.expectedPreStoreSha256 = std::string(64, '0');
    SessionSupervisorLeaseStore store;
    SessionSupervisorLegacyPaperCleanupResult result;
    std::string reason;
    assert(!store.MigrateHsl5PaperForTerminalCleanup(
        fixture.store, fixture.key, request, result, reason));
    assert(reason == "LEASE_STORE_TERMINAL_CLEANUP_PRE_HASH_MISMATCH");
    assert(ReadFile(fixture.store) == encoded);

    const int lockFd = ::open(fixture.cleanupLock.c_str(),
        O_RDWR | O_CLOEXEC | O_NOFOLLOW);
    assert(lockFd >= 0);
    assert(::flock(lockFd, LOCK_EX | LOCK_NB) == 0);
    SessionSupervisorLeaseStore normal;
    assert(!fixture.Init(normal, reason));
    assert(reason == "LEASE_STORE_TERMINAL_CLEANUP_IN_PROGRESS");
    assert(::flock(lockFd, LOCK_UN) == 0);
    assert(::close(lockFd) == 0);
}

void TestUnknownTemplateAndKeyDriftFailClosed()
{
    {
        Fixture fixture;
        SessionSupervisorLeaseRecord unknown =
            Watch("unknown-template-session-token-0001");
        unknown.templateId = "unknown";
        const std::string encoded = fixture.WriteHsl5({unknown});
        SessionSupervisorLeaseStore store;
        SessionSupervisorLegacyPaperCleanupResult result;
        std::string reason;
        assert(!store.MigrateHsl5PaperForTerminalCleanup(
            fixture.store, fixture.key, fixture.Request(encoded),
            result, reason));
        assert(reason == "LEASE_STORE_RECORD_INVALID");
        assert(ReadFile(fixture.store) == encoded);
        assert(::access(fixture.backup.c_str(), F_OK) != 0);
    }

    {
        Fixture fixture;
        const std::string encoded = fixture.WriteHsl5({
            Paper("key-drift-paper-session-token-0001", NowMs() - 1000)});
        SessionSupervisorLegacyPaperCleanupRequest request =
            fixture.Request(encoded);
        request.expectedKeySha256 = std::string(64, '0');
        SessionSupervisorLeaseStore hashMismatch;
        SessionSupervisorLegacyPaperCleanupResult result;
        std::string reason;
        assert(!hashMismatch.MigrateHsl5PaperForTerminalCleanup(
            fixture.store, fixture.key, request, result, reason));
        assert(reason ==
            "LEASE_STORE_TERMINAL_CLEANUP_KEY_METADATA_MISMATCH");
        assert(ReadFile(fixture.store) == encoded);
        assert(::access(fixture.backup.c_str(), F_OK) != 0);

        request = fixture.Request(encoded);
        assert(::chmod(fixture.key.c_str(), 0600) == 0);
        SessionSupervisorLeaseStore metadataMismatch;
        assert(!metadataMismatch.MigrateHsl5PaperForTerminalCleanup(
            fixture.store, fixture.key, request, result, reason));
        assert(reason ==
            "LEASE_STORE_TERMINAL_CLEANUP_KEY_METADATA_MISMATCH");
        assert(ReadFile(fixture.store) == encoded);
        assert(::access(fixture.backup.c_str(), F_OK) != 0);
        assert(::chmod(fixture.key.c_str(), 0400) == 0);
    }
}

void TestSourceHardlinkAndUnsafeLockFailClosed()
{
    {
        Fixture fixture;
        const std::string encoded = fixture.WriteHsl5({
            Paper("hardlink-source-paper-session-token-0001",
                NowMs() - 1000)});
        const std::string hardlink = fixture.directory + "/store-hardlink";
        assert(::link(fixture.store.c_str(), hardlink.c_str()) == 0);
        SessionSupervisorLeaseStore store;
        SessionSupervisorLegacyPaperCleanupResult result;
        std::string reason;
        assert(!store.MigrateHsl5PaperForTerminalCleanup(
            fixture.store, fixture.key, fixture.Request(encoded),
            result, reason));
        assert(reason == "LEASE_STORE_LINKS_UNSAFE");
        assert(::access(fixture.backup.c_str(), F_OK) != 0);
        assert(::unlink(hardlink.c_str()) == 0);
    }

    {
        Fixture fixture;
        const std::string encoded = fixture.WriteHsl5({
            Paper("unsafe-lock-paper-session-token-0001", NowMs() - 1000)});
        const SessionSupervisorLegacyPaperCleanupRequest request =
            fixture.Request(encoded);
        SessionSupervisorLegacyPaperCleanupResult result;
        std::string reason;

        assert(::chmod(fixture.cleanupLock.c_str(), 0600) == 0);
        SessionSupervisorLeaseStore wrongMode;
        assert(!wrongMode.MigrateHsl5PaperForTerminalCleanup(
            fixture.store, fixture.key, request, result, reason));
        assert(reason == "LEASE_STORE_TERMINAL_CLEANUP_LOCK_UNSAFE");
        assert(::chmod(fixture.cleanupLock.c_str(), 0644) == 0);

        const std::string realLock = fixture.lockDirectory + "/real.lock";
        assert(::chmod(fixture.lockDirectory.c_str(), 0700) == 0);
        assert(::rename(fixture.cleanupLock.c_str(), realLock.c_str()) == 0);
        assert(::symlink(realLock.c_str(), fixture.cleanupLock.c_str()) == 0);
        assert(::chmod(fixture.lockDirectory.c_str(), 0711) == 0);
        SessionSupervisorLeaseStore replacedPath;
        assert(!replacedPath.MigrateHsl5PaperForTerminalCleanup(
            fixture.store, fixture.key, request, result, reason));
        assert(reason == "LEASE_STORE_TERMINAL_CLEANUP_LOCK_UNSAFE");
        assert(ReadFile(fixture.store) == encoded);
        assert(::access(fixture.backup.c_str(), F_OK) != 0);

        assert(::chmod(fixture.lockDirectory.c_str(), 0700) == 0);
        assert(::unlink(fixture.cleanupLock.c_str()) == 0);
        assert(::rename(realLock.c_str(), fixture.cleanupLock.c_str()) == 0);
        assert(::chmod(fixture.lockDirectory.c_str(), 0711) == 0);
    }
}

void TestOrphanBackupStageDoesNotBlockPublication()
{
    Fixture fixture;
    const std::string encoded = fixture.WriteHsl5({
        Paper("orphan-stage-paper-session-token-0001", NowMs() - 1000)});
    const std::string orphan =
        fixture.backupDirectory + "/.hepta-hsl5-backup-stage.abandoned";
    WriteFile(orphan, "partial-backup", 0400);

    SessionSupervisorLeaseStore store;
    SessionSupervisorLegacyPaperCleanupResult result;
    std::string reason;
    assert(store.MigrateHsl5PaperForTerminalCleanup(
        fixture.store, fixture.key, fixture.Request(encoded), result, reason));
    assert(ReadFile(fixture.backup) == encoded);
    assert(ReadFile(orphan) == "partial-backup");
    assert(::unlink(orphan.c_str()) == 0);
}

void TestSymlinkInputsAndIdempotentDriftFailClosed()
{
    Fixture fixture;
    const SessionSupervisorLeaseRecord watch =
        Watch("drift-preserved-watch-session-token-0001");
    const std::string encoded = fixture.WriteHsl5({
        Paper("drift-legacy-paper-session-token-0001", NowMs() - 1000),
        watch});
    const std::string storeLink = fixture.directory + "/store-link";
    const std::string keyLink = fixture.directory + "/key-link";
    assert(::symlink(fixture.store.c_str(), storeLink.c_str()) == 0);
    assert(::symlink(fixture.key.c_str(), keyLink.c_str()) == 0);
    SessionSupervisorLeaseStore unsafeStore;
    std::string reason;
    assert(!fixture.Init(unsafeStore, reason, storeLink));
    assert(reason == "LEASE_STORE_PERMISSIONS_UNSAFE");
    SessionSupervisorLeaseStore unsafeKey;
    assert(!fixture.Init(unsafeKey, reason, fixture.store, keyLink));
    assert(reason == "LEASE_STORE_KEY_PERMISSIONS_UNSAFE");
    assert(::unlink(storeLink.c_str()) == 0);
    assert(::unlink(keyLink.c_str()) == 0);

    const SessionSupervisorLegacyPaperCleanupRequest request =
        fixture.Request(encoded);
    SessionSupervisorLeaseStore migration;
    SessionSupervisorLegacyPaperCleanupResult result;
    assert(migration.MigrateHsl5PaperForTerminalCleanup(
        fixture.store, fixture.key, request, result, reason));

    {
        SessionSupervisorLeaseStore changed;
        assert(fixture.Init(changed, reason));
        SessionSupervisorLeaseRecord extraWatch =
            Watch("drift-extra-watch-session-token-0001");
        extraWatch.agentId = "other-watch-agent";
        extraWatch.sessionId = "other-watch-session";
        assert(changed.Put(extraWatch, reason));
    }
    const std::string drifted = ReadFile(fixture.store);

    SessionSupervisorLeaseStore retry;
    SessionSupervisorLegacyPaperCleanupResult retryResult;
    assert(!retry.MigrateHsl5PaperForTerminalCleanup(
        fixture.store, fixture.key, request, retryResult, reason));
    assert(reason ==
        "LEASE_STORE_TERMINAL_CLEANUP_ALREADY_MIGRATED_INVALID");
    assert(retryResult.retiredRecords == 0);
    assert(ReadFile(fixture.store) == drifted);
}

void ExpectForkedMigrationBlocked(
    const Fixture& fixture,
    const SessionSupervisorLegacyPaperCleanupRequest& request,
    const std::string& expectedStore)
{
    const pid_t child = ::fork();
    assert(child >= 0);
    if (child == 0)
    {
        SessionSupervisorLeaseStore migration;
        SessionSupervisorLegacyPaperCleanupResult result;
        std::string reason;
        const bool accepted = migration.MigrateHsl5PaperForTerminalCleanup(
            fixture.store, fixture.key, request, result, reason);
        const bool passed = !accepted &&
            reason == "LEASE_STORE_TERMINAL_CLEANUP_IN_PROGRESS" &&
            ReadFile(fixture.store) == expectedStore &&
            ::access(fixture.backup.c_str(), F_OK) != 0;
        ::_exit(passed ? 0 : 1);
    }
    int status = 0;
    assert(::waitpid(child, &status, 0) == child);
    assert(WIFEXITED(status) && WEXITSTATUS(status) == 0);
    assert(ReadFile(fixture.store) == expectedStore);
}

void TestInitLifetimeSharedLockProtectsNormalWrites()
{
    Fixture fixture;
    std::string reason;
    {
        SessionSupervisorLeaseStore normal;
        assert(fixture.Init(normal, reason));
        const std::string before = ReadFile(fixture.store);
        ExpectForkedMigrationBlocked(
            fixture, fixture.Request(before), before);

        const SessionSupervisorLeaseRecord watch =
            Watch("lifetime-normal-write-watch-token-0001");
        assert(normal.Put(watch, reason));
        assert(normal.List().size() == 1);
        assert(normal.List()[0].token == watch.token);
        assert(ReadFile(fixture.store) != before);
    }

    SessionSupervisorLeaseStore reopened;
    assert(fixture.Init(reopened, reason));
    assert(reopened.List().size() == 1);
    assert(reopened.List()[0].token ==
        "lifetime-normal-write-watch-token-0001");
}

void TestDestructorReleasesSharedLockForMigration()
{
    Fixture fixture;
    const std::string hsl5 = fixture.EncodeHsl5({
        Paper("lifetime-release-paper-session-token-0001", NowMs() - 1000)});
    const SessionSupervisorLegacyPaperCleanupRequest request =
        fixture.Request(hsl5);
    std::string reason;
    {
        SessionSupervisorLeaseStore active;
        assert(fixture.Init(active, reason));
        fixture.ReplaceStore(hsl5);
        ExpectForkedMigrationBlocked(fixture, request, hsl5);
    }

    SessionSupervisorLeaseStore migration;
    SessionSupervisorLegacyPaperCleanupResult result;
    assert(migration.MigrateHsl5PaperForTerminalCleanup(
        fixture.store, fixture.key, request, result, reason));
    assert(result.retiredRecords == 1);
    assert(!result.alreadyMigrated);
    assert(ReadFile(fixture.backup) == hsl5);
}

void TestMissingSourceBackupFaultRejectsThenRecovers()
{
    Fixture fixture;
    const SessionSupervisorLeaseRecord watch =
        Watch("missing-source-preserved-watch-token-0001");
    const std::string hsl5 = fixture.WriteHsl5({
        Paper("missing-source-paper-session-token-0001", NowMs() - 1000),
        watch});
    const SessionSupervisorLegacyPaperCleanupRequest request =
        fixture.Request(hsl5);
    std::string reason;
    SessionSupervisorLeaseStore first;
    SessionSupervisorLegacyPaperCleanupResult firstResult;
    assert(first.MigrateHsl5PaperForTerminalCleanup(
        fixture.store, fixture.key, request, firstResult, reason));
    const std::string durableBackup = ReadFile(fixture.backup);
    assert(durableBackup == hsl5);
    assert(::unlink(fixture.store.c_str()) == 0);

    assert(::unlink(fixture.backup.c_str()) == 0);
    WriteFile(fixture.backup, "corrupt-backup", 0400);
    SessionSupervisorLeaseStore rejected;
    SessionSupervisorLegacyPaperCleanupResult rejectedResult;
    assert(!rejected.MigrateHsl5PaperForTerminalCleanup(
        fixture.store, fixture.key, request, rejectedResult, reason));
    assert(reason ==
        "LEASE_STORE_TERMINAL_CLEANUP_BACKUP_HASH_MISMATCH");
    assert(rejectedResult.retiredRecords == 0);
    assert(::access(fixture.store.c_str(), F_OK) != 0);

    assert(::unlink(fixture.backup.c_str()) == 0);
    WriteFile(fixture.backup, durableBackup, 0400);
    SessionSupervisorLeaseStore recovery;
    SessionSupervisorLegacyPaperCleanupResult recoveryResult;
    assert(recovery.MigrateHsl5PaperForTerminalCleanup(
        fixture.store, fixture.key, request, recoveryResult, reason));
    assert(recoveryResult.retiredRecords == 1);
    assert(!recoveryResult.alreadyMigrated);
    assert(recoveryResult.preStoreSha256 == Sha256(hsl5));
    assert(recoveryResult.postStoreSha256 == Sha256(ReadFile(fixture.store)));
    struct stat restored;
    assert(::lstat(fixture.store.c_str(), &restored) == 0);
    assert(restored.st_uid == ::geteuid());
    assert(restored.st_gid == ::getegid());
    assert((restored.st_mode & 0777) == 0600);

    SessionSupervisorLeaseStore reopened;
    assert(fixture.Init(reopened, reason));
    assert(reopened.List().size() == 1);
    assert(reopened.List()[0].token == watch.token);
}

void TestHsl6DefaultsFalseAndHsl7TransitionCannotDowngrade()
{
    Fixture fixture;
    const SessionSupervisorLeaseRecord legacy = OwnedPaper(
        "hsl6-owned-paper-session-token-0001",
        "hsl6-owner-agent", "hsl6-owner-session");
    WriteFile(fixture.store,
        EncryptEnvelope("HSL6\n" + Hsl6Record(legacy), fixture.keyBytes),
        0600);
    std::string reason;
    SessionSupervisorLeaseStore store;
    assert(fixture.Init(store, reason));
    assert(store.List().size() == 1);
    assert(!store.List()[0].paperFinalizationRequired);
    assert(store.List()[0].paperFinalizationState ==
        SessionSupervisorPaperFinalizationState::None);

    SessionSupervisorLeaseRecord transitioned = store.List()[0];
    transitioned.recoveryOnly = true;
    transitioned.recoveryCommandId = "external-recovery-query-hsl6";
    transitioned.paperFinalizationRequired = true;
    assert(store.Replace(transitioned.token, transitioned, reason));
    SessionSupervisorLeaseRecord durable;
    assert(store.Get(transitioned.token, durable));
    assert(durable.paperFinalizationRequired && durable.recoveryOnly);

    SessionSupervisorLeaseRecord downgrade = durable;
    downgrade.paperFinalizationRequired = false;
    assert(!store.Replace(durable.token, downgrade, reason));
    assert(reason ==
        "LEASE_STORE_PAPER_FINALIZATION_DOWNGRADE_REJECTED");
    assert(!store.Remove(durable.token, reason));
    assert(reason == "LEASE_STORE_PAPER_FINALIZATION_ACK_REQUIRED");

    SessionSupervisorLeaseStore reopened;
    assert(fixture.Init(reopened, reason));
    assert(reopened.Get(transitioned.token, durable));
    assert(durable.paperFinalizationRequired && durable.recoveryOnly);
}

void TestHsl7FinalizationStoreStateMachineAndAckReplay()
{
    Fixture fixture;
    SessionSupervisorLeaseStore store;
    std::string reason;
    assert(fixture.Init(store, reason));
    SessionSupervisorLeaseRecord ownerA = OwnedPaper(
        "hsl7-finalization-owner-a-token-0001",
        "hsl7-owner-a", "hsl7-session-a");
    SessionSupervisorLeaseRecord ownerB = OwnedPaper(
        "hsl7-finalization-owner-b-token-0001",
        "hsl7-owner-b", "hsl7-session-b");
    assert(store.Put(ownerA, reason));
    assert(store.Put(ownerB, reason));
    ownerA.recoveryOnly = true;
    ownerA.recoveryCommandId = "external-query-owner-a";
    ownerA.paperFinalizationRequired = true;
    ownerB.recoveryOnly = true;
    ownerB.recoveryCommandId = "external-query-owner-b";
    ownerB.paperFinalizationRequired = true;
    assert(store.Replace(ownerA.token, ownerA, reason));
    assert(store.Replace(ownerB.token, ownerB, reason));

    std::vector<SessionSupervisorLeaseRecord> owners = store.List();
    std::string ownerSetSha256;
    assert(SessionSupervisorLeaseStore::PaperOwnerSetSha256(
        owners, ownerSetSha256, reason));
    const std::string recoveryId = "hsl7-recovery-group-1";
    const std::string finalizationId = "hsl7-finalization-group-1";
    SessionSupervisorLeaseRecord pendingA = ownerA;
    pendingA.paperFinalizationState =
        SessionSupervisorPaperFinalizationState::FencePending;
    pendingA.recoveryId = recoveryId;
    pendingA.finalizationId = finalizationId;
    pendingA.expectedOwnerSetSha256 = ownerSetSha256;
    pendingA.expectedOwnerCount = 2;
    pendingA.ownerTokenSha256 = OwnerTokenSha256(ownerA.token);
    assert(store.AdvancePaperFinalization(
        ownerA.token, SessionSupervisorPaperFinalizationState::None,
        pendingA, reason));
    SessionSupervisorLeaseRecord completeA = pendingA;
    completeA.paperFinalizationState =
        SessionSupervisorPaperFinalizationState::FenceComplete;
    assert(store.AdvancePaperFinalization(
        ownerA.token,
        SessionSupervisorPaperFinalizationState::FencePending,
        completeA, reason));

    SessionSupervisorLeaseRecord wrongGroup = ownerB;
    wrongGroup.paperFinalizationState =
        SessionSupervisorPaperFinalizationState::FencePending;
    wrongGroup.recoveryId = recoveryId;
    wrongGroup.finalizationId = "wrong-group";
    wrongGroup.expectedOwnerSetSha256 = ownerSetSha256;
    wrongGroup.expectedOwnerCount = 2;
    wrongGroup.ownerTokenSha256 = OwnerTokenSha256(ownerB.token);
    assert(!store.AdvancePaperFinalization(
        ownerB.token, SessionSupervisorPaperFinalizationState::None,
        wrongGroup, reason));
    assert(reason == "LEASE_STORE_PAPER_FINALIZATION_GROUP_MISMATCH");

    SessionSupervisorLeaseRecord pendingB = wrongGroup;
    pendingB.finalizationId = finalizationId;
    assert(store.AdvancePaperFinalization(
        ownerB.token, SessionSupervisorPaperFinalizationState::None,
        pendingB, reason));
    SessionSupervisorLeaseRecord completeB = pendingB;
    completeB.paperFinalizationState =
        SessionSupervisorPaperFinalizationState::FenceComplete;
    assert(store.AdvancePaperFinalization(
        ownerB.token,
        SessionSupervisorPaperFinalizationState::FencePending,
        completeB, reason));

    owners = store.List();
    const std::string receipt = FinalizationReceipt(
        owners, recoveryId, finalizationId, ownerSetSha256);
    const std::string receiptSha256 = "sha256:" + Sha256(receipt);
    const std::string terminalReceipt = TerminalAckReceipt(
        owners, recoveryId, finalizationId, ownerSetSha256, receiptSha256);
    const std::string terminalReceiptSha256 =
        "sha256:" + Sha256(terminalReceipt);
    assert(store.SealPaperFinalizationGroup(
        recoveryId, finalizationId, ownerSetSha256, 2,
        receiptSha256, receipt, reason));
    assert(store.SealPaperFinalizationGroup(
        recoveryId, finalizationId, ownerSetSha256, 2,
        receiptSha256, receipt, reason));
    owners = store.List();
    assert(owners.size() == 2);
    for (std::size_t i = 0; i < owners.size(); ++i)
        assert(owners[i].paperFinalizationState ==
            SessionSupervisorPaperFinalizationState::AuditSealed);
    std::sort(owners.begin(), owners.end(),
        [](const SessionSupervisorLeaseRecord& left,
           const SessionSupervisorLeaseRecord& right) {
            return left.ownerTokenSha256 < right.ownerTokenSha256;
        });

    SessionSupervisorPaperFinalizationAck acknowledgement;
    bool alreadyAcknowledged = false;
    assert(!store.AcknowledgeAndPurgePaperFinalizationGroup(
        recoveryId, finalizationId, ownerSetSha256, 2,
        "sha256:" + std::string(64, '0'),
        terminalReceiptSha256, terminalReceipt,
        owners[0].ownerTokenSha256, owners[0].leaseGeneration,
        owners[0].issuer, owners[0].agentId, owners[0].sessionId,
        owners[0].ownerAccount, owners[0].ownerExecutionDomain,
        acknowledgement, alreadyAcknowledged, reason));
    assert(store.List().size() == 2);
    const std::string oversizedTerminalReceipt(12289, 'x');
    assert(!store.AcknowledgeAndPurgePaperFinalizationGroup(
        recoveryId, finalizationId, ownerSetSha256, 2,
        receiptSha256, "sha256:" + Sha256(oversizedTerminalReceipt),
        oversizedTerminalReceipt, owners[0].ownerTokenSha256,
        owners[0].leaseGeneration, owners[0].issuer,
        owners[0].agentId, owners[0].sessionId,
        owners[0].ownerAccount, owners[0].ownerExecutionDomain,
        acknowledgement, alreadyAcknowledged, reason));
    assert(reason == "LEASE_STORE_PAPER_FINALIZATION_ACK_INVALID");
    assert(store.List().size() == 2);
    std::string legacyTerminalReceipt = terminalReceipt;
    const std::size_t legacySchema = legacyTerminalReceipt.find(
        "schema=hepta.paper-session-terminal-ack-receipt.v3\n");
    const std::size_t legacyVersion = legacyTerminalReceipt.find(
        "version=3\n");
    assert(legacySchema != std::string::npos &&
        legacyVersion != std::string::npos);
    legacyTerminalReceipt.replace(
        legacySchema, std::string(
            "schema=hepta.paper-session-terminal-ack-receipt.v3\n").size(),
        "schema=hepta.paper-session-terminal-ack-receipt.v2\n");
    legacyTerminalReceipt.replace(
        legacyTerminalReceipt.find("version=3\n"),
        std::string("version=3\n").size(), "version=2\n");
    const std::string legacyTerminalReceiptSha256 =
        "sha256:" + Sha256(legacyTerminalReceipt);
    assert(!store.AcknowledgeAndPurgePaperFinalizationGroup(
        recoveryId, finalizationId, ownerSetSha256, 2,
        receiptSha256, legacyTerminalReceiptSha256,
        legacyTerminalReceipt, owners[0].ownerTokenSha256,
        owners[0].leaseGeneration, owners[0].issuer,
        owners[0].agentId, owners[0].sessionId,
        owners[0].ownerAccount, owners[0].ownerExecutionDomain,
        acknowledgement, alreadyAcknowledged, reason));
    assert(reason == "LEASE_STORE_PAPER_TERMINAL_ACK_RECEIPT_INVALID");
    assert(store.List().size() == 2);
    assert(store.AcknowledgeAndPurgePaperFinalizationGroup(
        recoveryId, finalizationId, ownerSetSha256, 2,
        receiptSha256, terminalReceiptSha256, terminalReceipt,
        owners[0].ownerTokenSha256,
        owners[0].leaseGeneration,
        owners[0].issuer, owners[0].agentId, owners[0].sessionId,
        owners[0].ownerAccount, owners[0].ownerExecutionDomain,
        acknowledgement,
        alreadyAcknowledged, reason));
    assert(!alreadyAcknowledged && store.List().empty());
    assert(acknowledgement.receipt == receipt);

    SessionSupervisorLeaseStore reopened;
    assert(fixture.Init(reopened, reason));
    SessionSupervisorPaperFinalizationAck durableAck;
    assert(reopened.GetPaperFinalizationAck(finalizationId, durableAck));
    assert(durableAck.receipt == receipt &&
        durableAck.receiptSha256 == receiptSha256);
    assert(reopened.AcknowledgeAndPurgePaperFinalizationGroup(
        recoveryId, finalizationId, ownerSetSha256, 2,
        receiptSha256, terminalReceiptSha256, terminalReceipt,
        owners[0].ownerTokenSha256, owners[0].leaseGeneration,
        owners[0].issuer, owners[0].agentId, owners[0].sessionId,
        owners[0].ownerAccount, owners[0].ownerExecutionDomain,
        acknowledgement,
        alreadyAcknowledged, reason));
    assert(alreadyAcknowledged && acknowledgement.receipt == receipt);
    SessionSupervisorLeaseRecord retiredReuse = OwnedPaper(
        owners[0].token, "retired-owner-reuse", "retired-session-reuse");
    assert(!reopened.Put(retiredReuse, reason));
    assert(reason == "LEASE_STORE_PAPER_FINALIZATION_OWNER_RETIRED");

    // The exact same HSL7 API is valid for the normal single-owner case.
    SessionSupervisorLeaseRecord single = OwnedPaper(
        "hsl7-single-owner-finalization-token-0001",
        "hsl7-single-owner", "hsl7-single-session");
    assert(reopened.Put(single, reason));
    single.recoveryOnly = true;
    single.recoveryCommandId = "single-owner-external-query";
    single.paperFinalizationRequired = true;
    assert(reopened.Replace(single.token, single, reason));
    std::vector<SessionSupervisorLeaseRecord> singleOwner = reopened.List();
    std::string singleOwnerSetSha256;
    assert(SessionSupervisorLeaseStore::PaperOwnerSetSha256(
        singleOwner, singleOwnerSetSha256, reason));
    SessionSupervisorLeaseRecord singlePending = single;
    singlePending.paperFinalizationState =
        SessionSupervisorPaperFinalizationState::FencePending;
    singlePending.recoveryId = "hsl7-single-recovery";
    singlePending.finalizationId = "hsl7-single-finalization";
    singlePending.expectedOwnerSetSha256 = singleOwnerSetSha256;
    singlePending.expectedOwnerCount = 1;
    singlePending.ownerTokenSha256 = OwnerTokenSha256(single.token);
    assert(reopened.AdvancePaperFinalization(
        single.token, SessionSupervisorPaperFinalizationState::None,
        singlePending, reason));
    SessionSupervisorLeaseRecord singleComplete = singlePending;
    singleComplete.paperFinalizationState =
        SessionSupervisorPaperFinalizationState::FenceComplete;
    assert(reopened.AdvancePaperFinalization(
        single.token,
        SessionSupervisorPaperFinalizationState::FencePending,
        singleComplete, reason));
    singleOwner = reopened.List();
    const std::string singleReceipt = FinalizationReceipt(
        singleOwner, singlePending.recoveryId,
        singlePending.finalizationId, singleOwnerSetSha256);
    const std::string singleReceiptSha256 =
        "sha256:" + Sha256(singleReceipt);
    const std::string singleTerminalReceipt = TerminalAckReceipt(
        singleOwner, singlePending.recoveryId,
        singlePending.finalizationId, singleOwnerSetSha256,
        singleReceiptSha256);
    const std::string singleTerminalReceiptSha256 =
        "sha256:" + Sha256(singleTerminalReceipt);
    assert(reopened.SealPaperFinalizationGroup(
        singlePending.recoveryId, singlePending.finalizationId,
        singleOwnerSetSha256, 1, singleReceiptSha256,
        singleReceipt, reason));
    assert(reopened.AcknowledgeAndPurgePaperFinalizationGroup(
        singlePending.recoveryId, singlePending.finalizationId,
        singleOwnerSetSha256, 1, singleReceiptSha256,
        singleTerminalReceiptSha256, singleTerminalReceipt,
        singlePending.ownerTokenSha256, single.leaseGeneration,
        single.issuer, single.agentId, single.sessionId,
        single.ownerAccount, single.ownerExecutionDomain,
        acknowledgement, alreadyAcknowledged, reason));
    assert(!alreadyAcknowledged && reopened.List().empty());
    assert(acknowledgement.receipt == singleReceipt);
}

void TestHsl7TornCorruptAndDuplicateLedgerFailClosed()
{
    SessionSupervisorLeaseRecord sealed = OwnedPaper(
        "hsl7-corrupt-owner-token-0001",
        "hsl7-corrupt-agent", "hsl7-corrupt-session");
    sealed.recoveryOnly = true;
    sealed.recoveryCommandId = "hsl7-corrupt-query";
    sealed.paperFinalizationRequired = true;
    sealed.paperFinalizationState =
        SessionSupervisorPaperFinalizationState::AuditSealed;
    sealed.recoveryId = "hsl7-corrupt-recovery";
    sealed.finalizationId = "hsl7-corrupt-finalization";
    sealed.expectedOwnerCount = 1;
    sealed.ownerTokenSha256 = OwnerTokenSha256(sealed.token);
    std::vector<SessionSupervisorLeaseRecord> records(1, sealed);
    sealed.expectedOwnerSetSha256 =
        "sha256:" + Sha256(OwnerSetCanonical(records));
    records[0] = sealed;
    sealed.finalizationReceipt = FinalizationReceipt(
        records, sealed.recoveryId, sealed.finalizationId,
        sealed.expectedOwnerSetSha256);
    sealed.finalizationReceiptSha256 =
        "sha256:" + Sha256(sealed.finalizationReceipt);
    SessionSupervisorPaperFinalizationAck ack;
    ack.recoveryId = sealed.recoveryId;
    ack.finalizationId = sealed.finalizationId;
    ack.expectedOwnerSetSha256 = sealed.expectedOwnerSetSha256;
    ack.expectedOwnerCount = 1;
    ack.receiptSha256 = sealed.finalizationReceiptSha256;
    ack.receipt = sealed.finalizationReceipt;
    ack.terminalReceipt = TerminalAckReceipt(
        records, sealed.recoveryId, sealed.finalizationId,
        sealed.expectedOwnerSetSha256, sealed.finalizationReceiptSha256);
    ack.terminalReceiptSha256 = "sha256:" + Sha256(ack.terminalReceipt);
    ack.acknowledgingOwnerTokenSha256 = sealed.ownerTokenSha256;
    ack.acknowledgingOwnerGeneration = sealed.leaseGeneration;
    ack.acknowledgingOwnerIssuer = sealed.issuer;
    ack.terminalizingOwnerAgentId = sealed.agentId;
    ack.terminalizingOwnerSessionId = sealed.sessionId;
    ack.terminalizingOwnerAccount = sealed.ownerAccount;
    ack.terminalizingOwnerExecutionDomain = sealed.ownerExecutionDomain;

    {
        Fixture fixture;
        WriteFile(fixture.store, EncryptEnvelope(
            "HSL7\n" + Hsl7Record(sealed), fixture.keyBytes), 0600);
        SessionSupervisorLeaseStore valid;
        std::string reason;
        assert(fixture.Init(valid, reason));
        assert(valid.List().size() == 1);
    }
    {
        Fixture fixture;
        std::string torn = Hsl7Record(sealed);
        torn.resize(torn.rfind('\t'));
        torn += '\n';
        WriteFile(fixture.store,
            EncryptEnvelope("HSL7\n" + torn, fixture.keyBytes), 0600);
        SessionSupervisorLeaseStore rejected;
        std::string reason;
        assert(!fixture.Init(rejected, reason));
        assert(reason == "LEASE_STORE_RECORD_INVALID");
    }
    {
        Fixture fixture;
        SessionSupervisorLeaseRecord corrupt = sealed;
        const std::string expected = "status=AUDIT_SEALED";
        const std::size_t offset =
            corrupt.finalizationReceipt.find(expected);
        assert(offset != std::string::npos);
        corrupt.finalizationReceipt.replace(
            offset, expected.size(), "status=NOT_SEALED");
        corrupt.finalizationReceiptSha256 =
            "sha256:" + Sha256(corrupt.finalizationReceipt);
        WriteFile(fixture.store, EncryptEnvelope(
            "HSL7\n" + Hsl7Record(corrupt), fixture.keyBytes), 0600);
        SessionSupervisorLeaseStore rejected;
        std::string reason;
        assert(!fixture.Init(rejected, reason));
        assert(reason ==
            "LEASE_STORE_PAPER_FINALIZATION_RECORD_INVALID");
    }
    {
        Fixture fixture;
        SessionSupervisorLeaseRecord downgraded = sealed;
        downgraded.paperFinalizationRequired = false;
        WriteFile(fixture.store, EncryptEnvelope(
            "HSL7\n" + Hsl7Record(downgraded), fixture.keyBytes), 0600);
        SessionSupervisorLeaseStore rejected;
        std::string reason;
        assert(!fixture.Init(rejected, reason));
        assert(reason ==
            "LEASE_STORE_PAPER_FINALIZATION_RECORD_INVALID");
    }
    {
        Fixture fixture;
        WriteFile(fixture.store, EncryptEnvelope(
            "HSL7\n" + Hsl7Ack(ack), fixture.keyBytes), 0600);
        SessionSupervisorLeaseStore rejected;
        std::string reason;
        assert(!fixture.Init(rejected, reason));
        assert(reason ==
            "LEASE_STORE_LEGACY_PAPER_FINALIZATION_ACK_REJECTED");
    }
    {
        Fixture fixture;
        WriteFile(fixture.store, EncryptEnvelope(
            "HSL8\n" + Hsl8Ack(ack), fixture.keyBytes), 0600);
        SessionSupervisorLeaseStore valid;
        std::string reason;
        assert(fixture.Init(valid, reason));
        SessionSupervisorPaperFinalizationAck parsed;
        assert(valid.GetPaperFinalizationAck(ack.finalizationId, parsed));
        assert(parsed.receipt == ack.receipt &&
            parsed.terminalReceipt == ack.terminalReceipt);
    }
    {
        Fixture fixture;
        WriteFile(fixture.store, EncryptEnvelope(
            "HSL8\n" + Hsl8Ack(ack) + Hsl8Ack(ack),
            fixture.keyBytes), 0600);
        SessionSupervisorLeaseStore rejected;
        std::string reason;
        assert(!fixture.Init(rejected, reason));
        assert(reason ==
            "LEASE_STORE_PAPER_FINALIZATION_ACK_INVALID");
    }
    {
        Fixture fixture;
        std::string torn = Hsl8Ack(ack);
        torn.resize(torn.rfind('\t'));
        torn += '\n';
        WriteFile(fixture.store,
            EncryptEnvelope("HSL8\n" + torn, fixture.keyBytes), 0600);
        SessionSupervisorLeaseStore rejected;
        std::string reason;
        assert(!fixture.Init(rejected, reason));
        assert(reason ==
            "LEASE_STORE_PAPER_FINALIZATION_ACK_INVALID");
    }
    {
        Fixture fixture;
        SessionSupervisorPaperFinalizationAck corrupt = ack;
        const std::string expected =
            "recovery_id=" + corrupt.recoveryId;
        const std::size_t offset = corrupt.receipt.find(expected);
        assert(offset != std::string::npos);
        corrupt.receipt.replace(
            offset, expected.size(), "recovery_id=wrong-recovery");
        corrupt.receiptSha256 = "sha256:" + Sha256(corrupt.receipt);
        WriteFile(fixture.store, EncryptEnvelope(
            "HSL8\n" + Hsl8Ack(corrupt), fixture.keyBytes), 0600);
        SessionSupervisorLeaseStore rejected;
        std::string reason;
        assert(!fixture.Init(rejected, reason));
        assert(reason ==
            "LEASE_STORE_PAPER_FINALIZATION_ACK_INVALID");
    }
    {
        Fixture fixture;
        SessionSupervisorPaperFinalizationAck crossScope = ack;
        const std::string expected = "owner_account=DU12345";
        const std::size_t offset = crossScope.receipt.find(expected);
        assert(offset != std::string::npos);
        crossScope.receipt.replace(
            offset, expected.size(), "owner_account=DU99999");
        crossScope.receiptSha256 =
            "sha256:" + Sha256(crossScope.receipt);
        crossScope.terminalReceipt = TerminalAckReceipt(
            records, sealed.recoveryId, sealed.finalizationId,
            sealed.expectedOwnerSetSha256, crossScope.receiptSha256);
        crossScope.terminalReceiptSha256 =
            "sha256:" + Sha256(crossScope.terminalReceipt);
        WriteFile(fixture.store, EncryptEnvelope(
            "HSL8\n" + Hsl8Ack(crossScope), fixture.keyBytes), 0600);
        SessionSupervisorLeaseStore rejected;
        std::string reason;
        assert(!fixture.Init(rejected, reason));
        assert(reason ==
            "LEASE_STORE_PAPER_FINALIZATION_ACK_INVALID");
    }
}

void TestLinuxOPathOpensSearchOnlyLockParent()
{
#if defined(__linux__)
    char pattern[] = "/tmp/hepta-search-only-lock-parent-XXXXXX";
    char* directory = ::mkdtemp(pattern);
    assert(directory != nullptr);
    assert(::chmod(directory, 0111) == 0);
    const int pathFd = ::open(directory,
        O_PATH | O_DIRECTORY | O_CLOEXEC | O_NOFOLLOW);
    assert(pathFd >= 0);
    struct stat metadata;
    assert(::fstat(pathFd, &metadata) == 0 && S_ISDIR(metadata.st_mode));
    assert(::close(pathFd) == 0);
    if (::geteuid() != 0)
    {
        errno = 0;
        const int readFd = ::open(directory,
            O_RDONLY | O_DIRECTORY | O_CLOEXEC | O_NOFOLLOW);
        assert(readFd < 0 && errno == EACCES);
    }
    assert(::chmod(directory, 0700) == 0);
    assert(::rmdir(directory) == 0);
#endif
}

} // namespace

int main()
{
    TestNormalInitRejectsOwnerlessHsl5Paper();
    TestNormalInitRejectsOwnerlessHsl4Paper();
    TestHappyPathPreservesWatchMetadataAndIsIdempotent();
    TestHsl4HappyPathPreservesWatchMetadataAndIsIdempotent();
    TestHsl4UnsafeRecordsRejectWithoutRewrite();
    TestNonHsl4OrHsl5CleanupSourcesRejectWithoutRewrite();
    TestUnexpiredScopeAndFlaggedRecordsRejectWithoutRewrite();
    TestPreHashAndHeldLockFailClosed();
    TestUnknownTemplateAndKeyDriftFailClosed();
    TestSourceHardlinkAndUnsafeLockFailClosed();
    TestOrphanBackupStageDoesNotBlockPublication();
    TestSymlinkInputsAndIdempotentDriftFailClosed();
    TestInitLifetimeSharedLockProtectsNormalWrites();
    TestDestructorReleasesSharedLockForMigration();
    TestMissingSourceBackupFaultRejectsThenRecovers();
    TestHsl6DefaultsFalseAndHsl7TransitionCannotDowngrade();
    TestHsl7FinalizationStoreStateMachineAndAckReplay();
    TestHsl7TornCorruptAndDuplicateLedgerFailClosed();
    TestLinuxOPathOpensSearchOnlyLockParent();
    return 0;
}
