#include "execution/paper_terminal_external_latch.h"

#include <cassert>
#include <cerrno>
#include <cstdlib>
#include <cstring>
#include <dirent.h>
#include <fcntl.h>
#include <fstream>
#include <iostream>
#include <map>
#include <sstream>
#include <string>
#include <vector>
#include <openssl/evp.h>
#include <sys/file.h>
#include <sys/stat.h>
#include <sys/types.h>
#include <sys/wait.h>
#include <unistd.h>

namespace
{
std::string Sha256(const std::string& value)
{
    unsigned char bytes[EVP_MAX_MD_SIZE];
    unsigned int length = 0;
    EVP_MD_CTX* context = EVP_MD_CTX_new();
    assert(context != nullptr);
    assert(EVP_DigestInit_ex(context, EVP_sha256(), nullptr) == 1);
    assert(EVP_DigestUpdate(context, value.data(), value.size()) == 1);
    assert(EVP_DigestFinal_ex(context, bytes, &length) == 1);
    EVP_MD_CTX_free(context);
    assert(length == 32);
    static const char digits[] = "0123456789abcdef";
    std::string result("sha256:");
    for (unsigned int i = 0; i < length; ++i)
    {
        result.push_back(digits[bytes[i] >> 4]);
        result.push_back(digits[bytes[i] & 15]);
    }
    return result;
}

std::string Hex(const std::string& value)
{
    static const char digits[] = "0123456789abcdef";
    std::string result;
    for (std::size_t i = 0; i < value.size(); ++i)
    {
        const unsigned char byte = static_cast<unsigned char>(value[i]);
        result.push_back(digits[byte >> 4]);
        result.push_back(digits[byte & 15]);
    }
    return result;
}

std::string ReadFile(const std::string& path)
{
    std::ifstream input(path.c_str(), std::ios::binary);
    assert(input.good());
    return std::string((std::istreambuf_iterator<char>(input)),
        std::istreambuf_iterator<char>());
}

std::string CurrentBootId()
{
    std::string bootId = ReadFile("/proc/sys/kernel/random/boot_id");
    assert(bootId.size() == 37 && bootId.back() == '\n');
    bootId.resize(36);
    return bootId;
}

void WriteFile(const std::string& path, const std::string& contents,
    mode_t mode)
{
    struct stat existing;
    if (::lstat(path.c_str(), &existing) == 0 && S_ISREG(existing.st_mode))
        assert(::chmod(path.c_str(), 0600) == 0);
    const int fd = ::open(path.c_str(),
        O_WRONLY | O_CREAT | O_TRUNC | O_CLOEXEC | O_NOFOLLOW, 0600);
    assert(fd >= 0);
    assert(::fchmod(fd, mode) == 0);
    std::size_t offset = 0;
    while (offset < contents.size())
    {
        const ssize_t count = ::write(fd, contents.data() + offset,
            contents.size() - offset);
        assert(count > 0);
        offset += static_cast<std::size_t>(count);
    }
    assert(::fsync(fd) == 0);
    assert(::close(fd) == 0);
}

void RemoveTree(const std::string& path)
{
    struct stat metadata;
    if (::lstat(path.c_str(), &metadata) != 0) return;
    if (!S_ISDIR(metadata.st_mode) || S_ISLNK(metadata.st_mode))
    {
        assert(::unlink(path.c_str()) == 0);
        return;
    }
    DIR* directory = ::opendir(path.c_str());
    assert(directory != nullptr);
    for (;;)
    {
        errno = 0;
        dirent* entry = ::readdir(directory);
        if (entry == nullptr)
        {
            assert(errno == 0);
            break;
        }
        const std::string name(entry->d_name);
        if (name == "." || name == "..") continue;
        RemoveTree(path + "/" + name);
    }
    assert(::closedir(directory) == 0);
    assert(::rmdir(path.c_str()) == 0);
}

struct TempDirectory
{
    std::string path;
    TempDirectory()
    {
        char pattern[] = "/tmp/hepta-paper-terminal-latch.XXXXXX";
        char* created = ::mkdtemp(pattern);
        assert(created != nullptr);
        path = created;
        assert(::chmod(path.c_str(), 0700) == 0);
    }
    ~TempDirectory() { RemoveTree(path); }
};

std::string BuildTerminalizingLatch()
{
    std::ostringstream out;
    out << "HPT1\n"
        << "state=TERMINALIZING\n"
        << "finalization_id=finalization-1\n"
        << "preliminary_finalization_receipt_sha256="
        << Sha256("preliminary") << '\n'
        << "owner_agent_id=agent-1\n"
        << "owner_session_id=session-1\n"
        << "owner_account=DU123456\n"
        << "owner_execution_domain=alpha\n"
        << "recovery_ingress_fence=17\n"
        << "terminalization_service_epoch=epoch-1\n"
        << "terminalization_service_fencing_generation=9\n"
        << "terminalization_generation=1\n";
    return out.str();
}

std::string OwnerSetCanonical(
    const std::string& account = "DU123456",
    const std::string& domain = "alpha")
{
    return Sha256("owner-token\n") + "\t7\t" + Hex(account) +
        "\t" + Hex(domain) + "\n";
}

typedef std::map<std::string, std::string> Changes;

std::string Changed(const Changes& changes, const std::string& key,
    const std::string& value)
{
    const Changes::const_iterator found = changes.find(key);
    return found == changes.end() ? value : found->second;
}

void ReplaceOnce(std::string& value, const std::string& before,
    const std::string& after)
{
    const std::size_t position = value.find(before);
    assert(position != std::string::npos);
    value.replace(position, before.size(), after);
}

std::string BuildCapsule(const std::string& terminalizing,
    const Changes& changes = Changes())
{
    const std::string ownerAccount =
        Changed(changes, "owner_account", "DU123456");
    const std::string ownerDomain =
        Changed(changes, "owner_execution_domain", "alpha");
    const std::string ownerSet = OwnerSetCanonical(ownerAccount, ownerDomain);
    const std::string canonical = Changed(changes,
        "owner_set_canonical_hex", Hex(ownerSet));
    std::ostringstream out;
    out << "HPC1\n"
        << "schema=" << Changed(changes, "schema",
            "hepta.paper-terminal-external-halt-commit-capsule.v1") << '\n'
        << "version=" << Changed(changes, "version", "1") << '\n'
        << "status=" << Changed(changes, "status",
            "POST_CUTOFF_TERMINAL_WITNESS_VERIFIED") << '\n'
        << "terminal_proof_kind=" << Changed(changes,
            "terminal_proof_kind",
            "POST_CUTOFF_SIGNED_ACCOUNT_WITNESS_V1") << '\n'
        << "recovery_id=" << Changed(changes, "recovery_id", "recovery-1") << '\n'
        << "finalization_id=" << Changed(changes, "finalization_id",
            "finalization-1") << '\n'
        << "campaign_id=" << Changed(changes, "campaign_id", "campaign-1") << '\n'
        << "cycle_id=" << Changed(changes, "cycle_id", "cycle-1") << '\n'
        << "expected_owner_set_sha256=" << Changed(changes,
            "expected_owner_set_sha256", Sha256(ownerSet)) << '\n'
        << "expected_owner_count=" << Changed(changes,
            "expected_owner_count", "1") << '\n'
        << "owner_set_canonical_hex=" << canonical << '\n'
        << "preliminary_finalization_receipt_sha256=" << Changed(changes,
            "preliminary_finalization_receipt_sha256", Sha256("preliminary")) << '\n'
        << "owner_agent_id=" << Changed(changes, "owner_agent_id", "agent-1") << '\n'
        << "owner_session_id=" << Changed(changes, "owner_session_id", "session-1") << '\n'
        << "owner_account=" << ownerAccount << '\n'
        << "owner_execution_domain=" << ownerDomain << '\n'
        << "account_id_sha256=" << Changed(changes,
            "account_id_sha256", Sha256("DU123456")) << '\n'
        << "execution_service_epoch=" << Changed(changes,
            "execution_service_epoch", "epoch-1") << '\n'
        << "execution_service_fencing_generation=" << Changed(changes,
            "execution_service_fencing_generation", "9") << '\n'
        << "recovery_ingress_fence=" << Changed(changes,
            "recovery_ingress_fence", "17") << '\n'
        << "terminalization_generation=" << Changed(changes,
            "terminalization_generation", "1") << '\n'
        << "terminalizing_latch_sha256=" << Changed(changes,
            "terminalizing_latch_sha256", Sha256(terminalizing)) << '\n'
        << "transport_cutoff_receipt_file_sha256=" << Changed(changes,
            "transport_cutoff_receipt_file_sha256", Sha256("cutoff-file")) << '\n'
        << "transport_cutoff_receipt_body_sha256=" << Changed(changes,
            "transport_cutoff_receipt_body_sha256", Sha256("cutoff-body")) << '\n'
        << "post_cutoff_terminal_witness_file_sha256=" << Changed(changes,
            "post_cutoff_terminal_witness_file_sha256", Sha256("witness-file")) << '\n'
        << "post_cutoff_terminal_witness_body_sha256=" << Changed(changes,
            "post_cutoff_terminal_witness_body_sha256", Sha256("witness-body")) << '\n'
        << "provider_trust_policy_file_sha256=" << Changed(changes,
            "provider_trust_policy_file_sha256", Sha256("trust-file")) << '\n'
        << "provider_trust_policy_body_sha256=" << Changed(changes,
            "provider_trust_policy_body_sha256", Sha256("trust-body")) << '\n'
        << "provider_id=" << Changed(changes, "provider_id",
            "reviewed-remote-account-authority-a") << '\n'
        << "provider_capability=" << Changed(changes, "provider_capability",
            "ACCOUNT_WIDE_ATOMIC_OR_CAUSAL_POST_CUTOFF_READ_ONLY_V1") << '\n'
        << "signed_account_payload_sha256=" << Changed(changes,
            "signed_account_payload_sha256", Sha256("signed-payload")) << '\n'
        << "signed_account_signature_sha256=" << Changed(changes,
            "signed_account_signature_sha256", Sha256("signature")) << '\n'
        << "host_boot_id=" << Changed(changes, "host_boot_id",
            CurrentBootId()) << '\n'
        << "egress_publisher_pid=" << Changed(changes,
            "egress_publisher_pid", "1234") << '\n'
        << "egress_publisher_start_ticks=" << Changed(changes,
            "egress_publisher_start_ticks", "5678") << '\n'
        << "egress_policy_generation=" << Changed(changes,
            "egress_policy_generation", "5") << '\n'
        << "egress_policy_sha256=" << Changed(changes,
            "egress_policy_sha256", Sha256("deny-all-policy")) << '\n'
        << "query_started_after_challenge=" << Changed(changes,
            "query_started_after_challenge", "1") << '\n'
        << "observed_after_cutoff=" << Changed(changes,
            "observed_after_cutoff", "1") << '\n'
        << "snapshot_consistency=" << Changed(changes,
            "snapshot_consistency", "CAUSAL_WATERMARK") << '\n'
        << "causal_watermark_dominates_cutoff=" << Changed(changes,
            "causal_watermark_dominates_cutoff", "1") << '\n'
        << "causal_watermark_dominates_all_mutations=" << Changed(changes,
            "causal_watermark_dominates_all_mutations", "1") << '\n'
        << "account_queries_complete=" << Changed(changes,
            "account_queries_complete", "1") << '\n'
        << "active_orders_complete=" << Changed(changes,
            "active_orders_complete", "1") << '\n'
        << "completed_orders_complete=" << Changed(changes,
            "completed_orders_complete", "1") << '\n'
        << "executions_complete=" << Changed(changes,
            "executions_complete", "1") << '\n'
        << "positions_complete=" << Changed(changes,
            "positions_complete", "1") << '\n'
        << "cash_fx_complete=" << Changed(changes,
            "cash_fx_complete", "1") << '\n'
        << "risk_complete=" << Changed(changes, "risk_complete", "1") << '\n'
        << "known_mutation_command_set_sha256=" << Changed(changes,
            "known_mutation_command_set_sha256", Sha256("known-commands")) << '\n'
        << "known_mutation_command_count=" << Changed(changes,
            "known_mutation_command_count", "2") << '\n'
        << "known_correlation_set_sha256=" << Changed(changes,
            "known_correlation_set_sha256", Sha256("known-correlations")) << '\n'
        << "known_correlation_count=" << Changed(changes,
            "known_correlation_count", "2") << '\n'
        << "all_known_mutation_commands_settled=" << Changed(changes,
            "all_known_mutation_commands_settled", "1") << '\n'
        << "settled_mutation_command_count=" << Changed(changes,
            "settled_mutation_command_count", "2") << '\n'
        << "unknown_mutation_command_count=" << Changed(changes,
            "unknown_mutation_command_count", "0") << '\n'
        << "unresolved_mutation_command_count=" << Changed(changes,
            "unresolved_mutation_command_count", "0") << '\n'
        << "unknown_active_order_count=" << Changed(changes,
            "unknown_active_order_count", "0") << '\n'
        << "active_order_count=" << Changed(changes, "active_order_count", "0") << '\n'
        << "position_count=" << Changed(changes, "position_count", "0") << '\n'
        << "nonzero_cash_fx_count=" << Changed(changes,
            "nonzero_cash_fx_count", "0") << '\n'
        << "gross_absolute_position=" << Changed(changes,
            "gross_absolute_position", "0") << '\n'
        << "gross_fx_exposure=" << Changed(changes,
            "gross_fx_exposure", "0") << '\n'
        << "gross_risk=" << Changed(changes, "gross_risk", "0") << '\n'
        << "mutation_connector_count=" << Changed(changes,
            "mutation_connector_count", "0") << '\n'
        << "broker_socket_count=" << Changed(changes,
            "broker_socket_count", "0") << '\n'
        << "broker_process_count=" << Changed(changes,
            "broker_process_count", "0") << '\n'
        << "broker_credential_count=" << Changed(changes,
            "broker_credential_count", "0") << '\n'
        << "execution_service_inactive=" << Changed(changes,
            "execution_service_inactive", "1") << '\n'
        << "paper_units_inactive=" << Changed(changes,
            "paper_units_inactive", "1") << '\n'
        << "execution_mutation_gate_closed=" << Changed(changes,
            "execution_mutation_gate_closed", "1") << '\n'
        << "broker_transport_connected=" << Changed(changes,
            "broker_transport_connected", "0") << '\n'
        << "broker_reconnect_permitted=" << Changed(changes,
            "broker_reconnect_permitted", "0") << '\n'
        << "read_only_authority=" << Changed(changes,
            "read_only_authority", "1") << '\n'
        << "mutation_attempted=" << Changed(changes,
            "mutation_attempted", "0") << '\n'
        << "paper_authorized=" << Changed(changes, "paper_authorized", "0") << '\n'
        << "live_authorized=" << Changed(changes, "live_authorized", "0") << '\n'
        << "mutation_authorized=" << Changed(changes,
            "mutation_authorized", "0") << '\n'
        << "direct_broker_access=" << Changed(changes,
            "direct_broker_access", "0") << '\n'
        << "order_submission_authorized=" << Changed(changes,
            "order_submission_authorized", "0") << '\n'
        << "order_authorized=" << Changed(changes,
            "order_authorized", "0") << '\n'
        << "paper_only=" << Changed(changes, "paper_only", "1") << '\n'
        << "authority_granted=" << Changed(changes,
            "authority_granted", "0") << '\n'
        << "terminal_witness_durable=" << Changed(changes,
            "terminal_witness_durable", "1") << '\n';
    const std::string body = out.str();
    out << "capsule_body_sha256=" << Changed(changes,
        "capsule_body_sha256", Sha256(body)) << '\n';
    return out.str();
}

struct Fixture
{
    TempDirectory temporary;
    std::string state;
    std::string capsule;
    std::string terminalizing;

    Fixture() : state(temporary.path + "/state"),
        capsule(temporary.path + "/capsule.v1"),
        terminalizing(BuildTerminalizingLatch())
    {
        assert(::mkdir(state.c_str(), 0700) == 0);
        WriteFile(state + "/ib-paper-runtime.lock", "lock\n", 0600);
        WriteFile(state + "/ib-paper-terminal-halt.v1", terminalizing, 0600);
        WriteFile(capsule, BuildCapsule(terminalizing), 0440);
    }

    bool Commit(hepta::PaperTerminalExternalLatchResult& result,
        std::string& reason, uid_t expected = ::geteuid())
    {
        return hepta::CommitPaperTerminalExternalLatch(
            state, capsule, expected, ::getegid(), ::geteuid(), ::getegid(),
            0440, result, reason);
    }
};

void TestCommitAndExactReplay()
{
    Fixture fixture;
    hepta::PaperTerminalExternalLatchResult first;
    std::string reason;
    assert(fixture.Commit(first, reason));
    assert(!first.replay);
    assert(first.recoveryId == "recovery-1");
    assert(first.finalizationId == "finalization-1");
    const std::string path = fixture.state + "/" +
        hepta::PaperTerminalExternalLatchFileName();
    const std::string observed = ReadFile(path);
    assert(observed == first.latchContents);
    assert(first.latchSha256 == Sha256(observed));
    assert(first.terminalizingLatchSha256 == Sha256(fixture.terminalizing));
    assert(observed.find("state=TERMINAL_EXTERNAL_HALTED\n") !=
        std::string::npos);
    assert(observed.find("paper_authorized=0\n") != std::string::npos);
    assert(ReadFile(fixture.state + "/ib-paper-terminal-halt.v1") ==
        fixture.terminalizing);
    struct stat metadata;
    assert(::lstat(path.c_str(), &metadata) == 0);
    assert(S_ISREG(metadata.st_mode));
    assert(metadata.st_uid == ::geteuid());
    assert((metadata.st_mode & 07777) == 0600);
    assert(metadata.st_nlink == 1);

    hepta::PaperTerminalExternalLatchResult replay;
    assert(fixture.Commit(replay, reason));
    assert(replay.replay);
    assert(replay.latchContents == first.latchContents);
    assert(replay.latchSha256 == first.latchSha256);
    assert(ReadFile(path) == observed);
}

void TestCapsuleSemanticTamperRejected()
{
    std::string wrongBootId = CurrentBootId();
    wrongBootId[0] = wrongBootId[0] == '0' ? '1' : '0';
    const std::vector<Changes> cases = {
        Changes{{"active_order_count", "1"}},
        Changes{{"unknown_mutation_command_count", "1"}},
        Changes{{"gross_risk", "0.0"}},
        Changes{{"read_only_authority", "0"}},
        Changes{{"mutation_attempted", "1"}},
        Changes{{"paper_authorized", "1"}},
        Changes{{"order_authorized", "1"}},
        Changes{{"paper_only", "0"}},
        Changes{{"authority_granted", "1"}},
        Changes{{"account_queries_complete", "0"}},
        Changes{{"query_started_after_challenge", "0"}},
        Changes{{"observed_after_cutoff", "0"}},
        Changes{{"causal_watermark_dominates_all_mutations", "0"}},
        Changes{{"snapshot_consistency", "EVENTUAL"}},
        Changes{{"settled_mutation_command_count", "1"}},
        Changes{{"expected_owner_count", "2"}},
        Changes{{"terminalization_generation", "2"}},
        Changes{{"execution_service_fencing_generation", "0"}},
        Changes{{"provider_capability", "UNREVIEWED"}},
        Changes{{"account_id_sha256", Sha256("DU999999")}},
        Changes{{"egress_publisher_pid", "0"}},
        Changes{{"egress_publisher_start_ticks", "0"}},
        Changes{{"host_boot_id", "not-a-boot-id"}},
        Changes{{"host_boot_id", wrongBootId}},
        Changes{{"signed_account_signature_sha256", "sha256:BAD"}}
    };
    for (std::size_t i = 0; i < cases.size(); ++i)
    {
        Fixture fixture;
        WriteFile(fixture.capsule,
            BuildCapsule(fixture.terminalizing, cases[i]), 0440);
        hepta::PaperTerminalExternalLatchResult result;
        std::string reason;
        assert(!fixture.Commit(result, reason));
        assert(reason == "PAPER_TERMINAL_COMMIT_CAPSULE_INVALID");
    }
    {
        Fixture fixture;
        std::string capsule = BuildCapsule(fixture.terminalizing);
        const std::size_t value = capsule.find("active_order_count=0\n");
        assert(value != std::string::npos);
        capsule[value + std::strlen("active_order_count=")] = '1';
        WriteFile(fixture.capsule, capsule, 0440);
        hepta::PaperTerminalExternalLatchResult result;
        std::string reason;
        assert(!fixture.Commit(result, reason));
        assert(reason == "PAPER_TERMINAL_COMMIT_CAPSULE_INVALID");
    }
    {
        Fixture fixture;
        std::string capsule = BuildCapsule(fixture.terminalizing);
        capsule += "unexpected=field\n";
        WriteFile(fixture.capsule, capsule, 0440);
        hepta::PaperTerminalExternalLatchResult result;
        std::string reason;
        assert(!fixture.Commit(result, reason));
        assert(reason == "PAPER_TERMINAL_COMMIT_CAPSULE_INVALID");
    }
}

void TestHpt1BindingAndStateSafety()
{
    const std::vector<Changes> cases = {
        Changes{{"finalization_id", "other-finalization"}},
        Changes{{"owner_account", "DU999999"}},
        Changes{{"owner_agent_id", "other-agent"}},
        Changes{{"recovery_ingress_fence", "18"}},
        Changes{{"execution_service_epoch", "epoch-2"}},
        Changes{{"terminalizing_latch_sha256", Sha256("other-hpt1")}},
        Changes{{"preliminary_finalization_receipt_sha256",
            Sha256("other-preliminary")}}
    };
    for (std::size_t i = 0; i < cases.size(); ++i)
    {
        Fixture fixture;
        WriteFile(fixture.capsule,
            BuildCapsule(fixture.terminalizing, cases[i]), 0440);
        hepta::PaperTerminalExternalLatchResult result;
        std::string reason;
        assert(!fixture.Commit(result, reason));
        assert(reason == "PAPER_TERMINAL_COMMIT_CAPSULE_INVALID");
    }

    {
        Fixture fixture;
        assert(::unlink((fixture.state + "/ib-paper-terminal-halt.v1").c_str()) == 0);
        hepta::PaperTerminalExternalLatchResult result;
        std::string reason;
        assert(!fixture.Commit(result, reason));
        assert(reason == "PAPER_TERMINAL_TERMINALIZING_LATCH_UNSAFE");
    }
    {
        Fixture fixture;
        std::string halted = fixture.terminalizing;
        const std::size_t position = halted.find("state=TERMINALIZING");
        assert(position != std::string::npos);
        halted.replace(position, std::strlen("state=TERMINALIZING"),
            "state=TERMINAL_HALTED");
        WriteFile(fixture.state + "/ib-paper-terminal-halt.v1", halted, 0600);
        WriteFile(fixture.capsule, BuildCapsule(halted), 0440);
        hepta::PaperTerminalExternalLatchResult result;
        std::string reason;
        assert(!fixture.Commit(result, reason));
        assert(reason == "PAPER_TERMINAL_TERMINALIZING_LATCH_INVALID");
    }
    {
        Fixture fixture;
        assert(::chmod((fixture.state + "/ib-paper-terminal-halt.v1").c_str(),
            0644) == 0);
        hepta::PaperTerminalExternalLatchResult result;
        std::string reason;
        assert(!fixture.Commit(result, reason));
        assert(reason == "PAPER_TERMINAL_TERMINALIZING_LATCH_UNSAFE");
    }
    {
        Fixture fixture;
        const std::string latch = fixture.state + "/ib-paper-terminal-halt.v1";
        assert(::unlink(latch.c_str()) == 0);
        assert(::symlink(fixture.capsule.c_str(), latch.c_str()) == 0);
        hepta::PaperTerminalExternalLatchResult result;
        std::string reason;
        assert(!fixture.Commit(result, reason));
    }
    {
        Fixture fixture;
        assert(::chmod(fixture.state.c_str(), 0750) == 0);
        hepta::PaperTerminalExternalLatchResult result;
        std::string reason;
        assert(!fixture.Commit(result, reason));
        assert(reason == "PAPER_TERMINAL_STATE_DIRECTORY_UNSAFE");
    }
    {
        Fixture fixture;
        hepta::PaperTerminalExternalLatchResult result;
        std::string reason;
        assert(!fixture.Commit(result, reason, ::geteuid() + 1));
        assert(reason == "PAPER_TERMINAL_STATE_DIRECTORY_UNSAFE");
    }
}

void TestProductionIdentityShapes()
{
    Fixture fixture;
    const std::string agent =
        "agent:telegram-bot-8681289317:explicit:123e4567-e89b-12d3-a456-426614174000";
    const std::string session = "123e4567-e89b-12d3-a456-426614174001";
    const std::string domain = "PAPER:alpha";
    const std::string epoch =
        "hexec-v6-0123456789abcdef0123456789abcdef";
    ReplaceOnce(fixture.terminalizing, "owner_agent_id=agent-1\n",
        "owner_agent_id=" + agent + "\n");
    ReplaceOnce(fixture.terminalizing, "owner_session_id=session-1\n",
        "owner_session_id=" + session + "\n");
    ReplaceOnce(fixture.terminalizing, "owner_execution_domain=alpha\n",
        "owner_execution_domain=" + domain + "\n");
    ReplaceOnce(fixture.terminalizing,
        "terminalization_service_epoch=epoch-1\n",
        "terminalization_service_epoch=" + epoch + "\n");
    WriteFile(fixture.state + "/ib-paper-terminal-halt.v1",
        fixture.terminalizing, 0600);
    const Changes identities = {
        {"owner_agent_id", agent}, {"owner_session_id", session},
        {"owner_execution_domain", domain},
        {"execution_service_epoch", epoch}
    };
    WriteFile(fixture.capsule,
        BuildCapsule(fixture.terminalizing, identities), 0440);
    hepta::PaperTerminalExternalLatchResult result;
    std::string reason;
    assert(fixture.Commit(result, reason));
    assert(result.latchContents.find(
        "owner_execution_domain=PAPER:alpha\n") != std::string::npos);
    assert(result.latchContents.find(
        "execution_service_epoch=" + epoch + "\n") != std::string::npos);
}

void TestCapsuleSafeOpenAndRuntimeLock()
{
    {
        Fixture fixture;
        assert(::chmod(fixture.capsule.c_str(), 0600) == 0);
        hepta::PaperTerminalExternalLatchResult result;
        std::string reason;
        assert(!fixture.Commit(result, reason));
        assert(reason == "PAPER_TERMINAL_COMMIT_CAPSULE_UNSAFE");
    }
    {
        Fixture fixture;
        const std::string actual = fixture.temporary.path + "/actual-capsule";
        assert(::rename(fixture.capsule.c_str(), actual.c_str()) == 0);
        assert(::symlink(actual.c_str(), fixture.capsule.c_str()) == 0);
        hepta::PaperTerminalExternalLatchResult result;
        std::string reason;
        assert(!fixture.Commit(result, reason));
        assert(reason == "PAPER_TERMINAL_COMMIT_CAPSULE_UNSAFE");
    }
    {
        Fixture fixture;
        const std::string hardLink = fixture.temporary.path + "/capsule-link";
        assert(::link(fixture.capsule.c_str(), hardLink.c_str()) == 0);
        hepta::PaperTerminalExternalLatchResult result;
        std::string reason;
        assert(!fixture.Commit(result, reason));
        assert(reason == "PAPER_TERMINAL_COMMIT_CAPSULE_UNSAFE");
    }
    {
        Fixture fixture;
        const std::string lock = fixture.state + "/ib-paper-runtime.lock";
        assert(::unlink(lock.c_str()) == 0);
        hepta::PaperTerminalExternalLatchResult result;
        std::string reason;
        assert(!fixture.Commit(result, reason));
        assert(reason == "PAPER_TERMINAL_STATE_LOCK_UNAVAILABLE");
    }
    {
        Fixture fixture;
        const int lock = ::open((fixture.state + "/ib-paper-runtime.lock").c_str(),
            O_RDWR | O_CLOEXEC | O_NOFOLLOW);
        assert(lock >= 0);
        assert(::flock(lock, LOCK_EX | LOCK_NB) == 0);
        hepta::PaperTerminalExternalLatchResult result;
        std::string reason;
        assert(!fixture.Commit(result, reason));
        assert(reason == "PAPER_TERMINAL_STATE_LOCK_UNAVAILABLE");
        assert(::flock(lock, LOCK_UN) == 0);
        assert(::close(lock) == 0);
    }
    {
        Fixture fixture;
        const std::string realParent = fixture.temporary.path + "/real-parent";
        const std::string linkedParent = fixture.temporary.path + "/linked-parent";
        assert(::mkdir(realParent.c_str(), 0700) == 0);
        const std::string realCapsule = realParent + "/capsule";
        WriteFile(realCapsule, BuildCapsule(fixture.terminalizing), 0440);
        assert(::symlink(realParent.c_str(), linkedParent.c_str()) == 0);
        fixture.capsule = linkedParent + "/capsule";
        hepta::PaperTerminalExternalLatchResult result;
        std::string reason;
        assert(!fixture.Commit(result, reason));
        assert(reason == "PAPER_TERMINAL_COMMIT_CAPSULE_UNSAFE");
    }
}

void TestNoReplaceConflictTornAndSymlink()
{
    const std::string name = hepta::PaperTerminalExternalLatchFileName();
    {
        Fixture fixture;
        WriteFile(fixture.state + "/" + name, "torn\n", 0600);
        hepta::PaperTerminalExternalLatchResult result;
        std::string reason;
        assert(!fixture.Commit(result, reason));
        assert(reason == "PAPER_TERMINAL_EXTERNAL_LATCH_CONFLICT");
    }
    {
        Fixture fixture;
        WriteFile(fixture.state + "/" + name, "conflict\n", 0644);
        hepta::PaperTerminalExternalLatchResult result;
        std::string reason;
        assert(!fixture.Commit(result, reason));
        assert(reason == "PAPER_TERMINAL_EXTERNAL_LATCH_UNSAFE");
    }
    {
        Fixture fixture;
        assert(::symlink(fixture.capsule.c_str(),
            (fixture.state + "/" + name).c_str()) == 0);
        hepta::PaperTerminalExternalLatchResult result;
        std::string reason;
        assert(!fixture.Commit(result, reason));
        assert(reason == "PAPER_TERMINAL_EXTERNAL_LATCH_UNSAFE");
    }
    {
        Fixture fixture;
        const std::string stale = fixture.state +
            "/.ib-paper-terminal-external-halt.tmp." +
            std::to_string(static_cast<unsigned long>(::getpid())) + ".0";
        WriteFile(stale, "stale-temporary\n", 0600);
        hepta::PaperTerminalExternalLatchResult result;
        std::string reason;
        assert(fixture.Commit(result, reason));
        assert(!result.replay);
    }
    {
        Fixture fixture;
        hepta::PaperTerminalExternalLatchResult first;
        std::string reason;
        assert(fixture.Commit(first, reason));
        const std::string committed = first.latchContents;
        const Changes changedWitness = {
            {"post_cutoff_terminal_witness_file_sha256",
                Sha256("different-witness-file")},
            {"post_cutoff_terminal_witness_body_sha256",
                Sha256("different-witness-body")}
        };
        WriteFile(fixture.capsule,
            BuildCapsule(fixture.terminalizing, changedWitness), 0440);
        hepta::PaperTerminalExternalLatchResult conflict;
        assert(!fixture.Commit(conflict, reason));
        assert(reason == "PAPER_TERMINAL_EXTERNAL_LATCH_CONFLICT");
        assert(ReadFile(fixture.state + "/" + name) == committed);
    }
}

void TestCliAndUnitHardening()
{
    Fixture fixture;
    const std::string output = fixture.temporary.path + "/cli-output";
    const pid_t child = ::fork();
    assert(child >= 0);
    if (child == 0)
    {
        const int fd = ::open(output.c_str(),
            O_WRONLY | O_CREAT | O_TRUNC | O_CLOEXEC, 0600);
        if (fd < 0 || ::dup2(fd, STDOUT_FILENO) < 0 ||
            ::dup2(fd, STDERR_FILENO) < 0)
            _exit(127);
        ::close(fd);
        ::execl(HEPTA_TERMINAL_COMMITTER_PATH,
            HEPTA_TERMINAL_COMMITTER_PATH,
            "--state-directory", fixture.state.c_str(),
            "--capsule", fixture.capsule.c_str(),
            static_cast<char*>(nullptr));
        _exit(127);
    }
    int status = 0;
    assert(::waitpid(child, &status, 0) == child);
    // Production has no arbitrary --capsule escape hatch.  Only the fixed
    // root:root 0440 systemd credential named by CREDENTIALS_DIRECTORY is
    // accepted by the CLI.
    assert(WIFEXITED(status) && WEXITSTATUS(status) == 2);
    const std::string cliOutput = ReadFile(output);
    assert(cliOutput.find("systemd credential required") != std::string::npos);

    const std::string unit = ReadFile(std::string(HEPTA_SOURCE_ROOT) +
        "/systemd/hepta-paper-terminal-latch-committer@.service");
    const char* const required[] = {
        "User=hepta-ib-exec-%i\n", "Group=hepta-ib-exec-%i\n",
        "PrivateNetwork=yes\n", "RestrictAddressFamilies=AF_UNIX\n",
        "ProtectProc=invisible\n",
        "CapabilityBoundingSet=\n", "AmbientCapabilities=\n",
        "TimeoutStartSec=30s\n",
        "StateDirectory=hepta-ib-execution-%i\n",
        "StateDirectoryMode=0700\n",
        "ExecStart=/usr/libexec/hepta-paper-terminal-latch-committer "
            "--state-directory /var/lib/hepta-ib-execution-%i\n",
        "ReadOnlyPaths=/run /tmp /var/tmp\n",
        "ReadWritePaths=/var/lib/hepta-ib-execution-%i\n",
        "LoadCredential=hepta-paper-terminal-commit-capsule:",
        "Conflicts=hepta-execution-ib-paper@%i.service "
            "hepta-execution-ib-paper@%i.socket "
            "hepta-execution-events-ib-paper@%i.socket\n"
    };
    for (std::size_t i = 0; i < sizeof(required) / sizeof(required[0]); ++i)
        assert(unit.find(required[i]) != std::string::npos);
    const char* const forbidden[] = {
        "AF_INET", "AF_INET6", "EnvironmentFile=", "IPAddressAllow=",
        "hepta-execution-fence", "hepta-ib-paper-authorization",
        "hepta-fx-cash-baseline", "ProcSubset=pid", "\n[Install]\n"
    };
    for (std::size_t i = 0; i < sizeof(forbidden) / sizeof(forbidden[0]); ++i)
        assert(unit.find(forbidden[i]) == std::string::npos);
    std::size_t credentialCount = 0;
    std::size_t offset = 0;
    while ((offset = unit.find("LoadCredential=", offset)) !=
        std::string::npos)
    {
        ++credentialCount;
        offset += std::strlen("LoadCredential=");
    }
    assert(credentialCount == 1);
}
}

int main()
{
    TestCommitAndExactReplay();
    TestCapsuleSemanticTamperRejected();
    TestHpt1BindingAndStateSafety();
    TestProductionIdentityShapes();
    TestCapsuleSafeOpenAndRuntimeLock();
    TestNoReplaceConflictTornAndSymlink();
    TestCliAndUnitHardening();
    std::cout << "paper terminal external latch tests passed\n";
    return 0;
}
