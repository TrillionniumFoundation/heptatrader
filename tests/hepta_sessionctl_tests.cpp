#include "../HeptaTrade/cli/hepta_sessionctl_command.h"
#include "../HeptaTrade/cli/hepta_sessionctl_terminal_cleanup.h"

#include <cassert>
#include <fcntl.h>
#include <iostream>
#include <openssl/evp.h>
#include <string>
#include <sys/stat.h>
#include <unistd.h>
#include <vector>

namespace
{
bool Parse(const std::vector<std::string>& arguments,
           HeptaSessionCtlCommand& command,
           std::string& reason)
{
    std::vector<std::string> storage(arguments);
    std::vector<char*> argv;
    for (std::size_t i = 0; i < storage.size(); ++i)
        argv.push_back(&storage[i][0]);
    return HeptaSessionCtlCommandParser::Parse(
        static_cast<int>(argv.size()), argv.data(), command, reason);
}

bool ParseTerminalCleanup(
    const std::vector<std::string>& arguments,
    HeptaSessionCtlTerminalCleanupCommand& command,
    std::string& reason)
{
    std::vector<std::string> storage(arguments);
    std::vector<char*> argv;
    for (std::size_t i = 0; i < storage.size(); ++i)
        argv.push_back(&storage[i][0]);
    return HeptaSessionCtlTerminalCleanup::Parse(
        static_cast<int>(argv.size()), argv.data(), command, reason);
}

std::string TempFile()
{
    std::string path("/tmp/hepta-sessionctl-token-XXXXXX");
    std::vector<char> buffer(path.begin(), path.end());
    buffer.push_back('\0');
    const int descriptor = ::mkstemp(buffer.data());
    assert(descriptor >= 0);
    const std::string token(32, 'A');
    assert(::write(descriptor, token.data(), token.size()) ==
        static_cast<ssize_t>(token.size()));
    assert(::fchmod(descriptor, 0600) == 0);
    ::close(descriptor);
    return std::string(buffer.data());
}

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
    std::string digest("sha256:");
    for (unsigned int i = 0; i < length; ++i)
    {
        digest.push_back(digits[bytes[i] >> 4]);
        digest.push_back(digits[bytes[i] & 15]);
    }
    return digest;
}

std::string TempEvidenceDirectory()
{
    char pattern[] = "/tmp/hepta-sessionctl-evidence.XXXXXX";
    char* path = ::mkdtemp(pattern);
    assert(path != nullptr);
    assert(::chmod(path, 0700) == 0);
    return path;
}

void WriteEvidence(const std::string& path, const std::string& evidence)
{
    const int descriptor = ::open(path.c_str(),
        O_WRONLY | O_CREAT | O_EXCL | O_CLOEXEC, 0400);
    assert(descriptor >= 0);
    assert(::write(descriptor, evidence.data(), evidence.size()) ==
        static_cast<ssize_t>(evidence.size()));
    assert(::fsync(descriptor) == 0);
    assert(::close(descriptor) == 0);
}
}

int main()
{
    HeptaSessionCtlCommand command;
    std::string reason;
    assert(Parse({"hepta-sessionctl", "--socket", "/tmp/supervisor.sock",
                  "provision", "--template", "paper", "--token-file", "/tmp/token",
                  "--agent-id", "codex", "--session-id", "session-1",
                  "--peer-uid", "1000", "--ttl-sec", "120"}, command, reason));
    assert(command.request.operation == SessionSupervisorOperation::Provision);
    assert(command.request.templateId == "paper");
    assert(command.request.peerUid == 1000);
    assert(command.request.ttlMs == 120000);

    assert(Parse({"hepta-sessionctl", "--socket", "/tmp/supervisor.sock",
                  "rotate", "--token-file", "/tmp/old", "--replacement-token-file",
                  "/tmp/new", "--generation", "7", "--ttl-sec", "300",
                  "--token-owner-uid", "2104"},
                 command, reason));
    assert(command.request.operation == SessionSupervisorOperation::Rotate);
    assert(command.request.expectedGeneration == 7);
    assert(command.request.ttlMs == 300000);
    assert(command.hasTokenOwnerUid);
    assert(command.tokenOwnerUid == 2104);

    assert(Parse({"hepta-sessionctl", "--socket", "/tmp/supervisor.sock",
                  "revoke", "--token-file", "/tmp/root-token",
                  "--generation", "8", "--token-owner-uid", "0"},
                 command, reason));
    assert(command.request.operation == SessionSupervisorOperation::Revoke);
    assert(command.request.expectedGeneration == 8);
    assert(command.hasTokenOwnerUid);
    assert(command.tokenOwnerUid == 0);
    assert(Parse({"hepta-sessionctl", "--socket", "/tmp/supervisor.sock",
                  "recovery-query", "--token-file", "/tmp/root-token",
                  "--generation", "8", "--command-id",
                  "hexec-command-0123456789abcdef"},
                 command, reason));
    assert(command.request.operation ==
           SessionSupervisorOperation::RecoveryQuery);
    assert(command.request.expectedGeneration == 8);
    assert(command.request.targetCommandId ==
           "hexec-command-0123456789abcdef");
    assert(!command.request.requirePaperFinalization);
    assert(Parse({"hepta-sessionctl", "--socket", "/tmp/supervisor.sock",
                  "recovery-query", "--token-file", "/tmp/root-token",
                  "--generation", "8", "--command-id",
                  "hexec-command-0123456789abcdef",
                  "--require-paper-finalization"},
                 command, reason));
    assert(command.request.requirePaperFinalization);
    const std::string ownerSetSha = "sha256:" + std::string(64, 'a');
    const std::string receiptSha = "sha256:" + std::string(64, 'b');
    assert(Parse({"hepta-sessionctl", "--socket", "/tmp/supervisor.sock",
                  "paper-finalize", "--token-file", "/tmp/root-token",
                  "--generation", "8", "--recovery-id", "recovery-1",
                  "--finalization-id", "finalization-1",
                  "--expected-owner-set-sha256", ownerSetSha,
                  "--expected-owner-count", "2", "--token-owner-uid", "0"},
                 command, reason));
    assert(command.request.operation ==
           SessionSupervisorOperation::PaperFinalize);
    assert(command.request.expectedGeneration == 8);
    assert(command.request.recoveryId == "recovery-1");
    assert(command.request.finalizationId == "finalization-1");
    assert(command.request.expectedOwnerSetSha256 == ownerSetSha);
    assert(command.request.expectedOwnerCount == 2);
    assert(command.request.receiptSha256.empty());
    assert(Parse({"hepta-sessionctl", "--socket", "/tmp/supervisor.sock",
                  "paper-finalize-ack", "--token-file", "/tmp/root-token",
                  "--generation", "8", "--recovery-id", "recovery-1",
                  "--finalization-id", "finalization-1",
                  "--expected-owner-set-sha256", ownerSetSha,
                  "--expected-owner-count", "2",
                  "--receipt-sha256", receiptSha}, command, reason));
    assert(command.request.operation ==
           SessionSupervisorOperation::PaperFinalizeAck);
    assert(command.request.receiptSha256 == receiptSha);
    assert(Parse({"hepta-sessionctl", "--socket", "/tmp/supervisor.sock",
                  "paper-terminal-witness-prepare", "--token-file",
                  "/tmp/root-token", "--generation", "8", "--recovery-id",
                  "recovery-1", "--finalization-id", "finalization-1",
                  "--expected-owner-set-sha256", ownerSetSha,
                  "--expected-owner-count", "2", "--receipt-sha256",
                  receiptSha, "--token-owner-uid", "0"}, command, reason));
    assert(command.request.operation ==
           SessionSupervisorOperation::PaperTerminalWitnessPrepare);
    assert(command.request.receiptSha256 == receiptSha);
    assert(command.hasTokenOwnerUid && command.tokenOwnerUid == 0);
    const std::string evidenceSha = "sha256:" + std::string(64, 'c');
    assert(Parse({"hepta-sessionctl", "--socket", "/tmp/supervisor.sock",
                  "paper-terminal-witness-ack", "--token-file",
                  "/tmp/root-token", "--generation", "8", "--recovery-id",
                  "recovery-1", "--finalization-id", "finalization-1",
                  "--expected-owner-set-sha256", ownerSetSha,
                  "--expected-owner-count", "2", "--receipt-sha256",
                  receiptSha, "--terminal-evidence-file",
                  "/run/hepta/terminal-evidence.hpe1",
                  "--terminal-evidence-sha256", evidenceSha,
                  "--token-owner-uid", "0"}, command, reason));
    assert(command.request.operation ==
           SessionSupervisorOperation::PaperTerminalWitnessAck);
    assert(command.terminalEvidenceFile ==
           "/run/hepta/terminal-evidence.hpe1");
    assert(command.request.terminalEvidenceSha256 == evidenceSha);
    assert(command.hasTokenOwnerUid && command.tokenOwnerUid == 0);
    assert(!Parse({"hepta-sessionctl", "--socket", "/tmp/supervisor.sock",
                   "paper-terminal-witness-ack", "--token-file",
                   "/tmp/root-token", "--generation", "8", "--recovery-id",
                   "recovery-1", "--finalization-id", "finalization-1",
                   "--expected-owner-set-sha256", ownerSetSha,
                   "--expected-owner-count", "2", "--receipt-sha256",
                   receiptSha, "--terminal-evidence-file",
                   "/run/hepta/terminal-evidence.hpe1"}, command, reason));
    assert(!Parse({"hepta-sessionctl", "--socket", "/tmp/supervisor.sock",
                   "paper-finalize", "--token-file", "/tmp/root-token",
                   "--generation", "8", "--recovery-id", "recovery-1",
                   "--finalization-id", "finalization-1",
                   "--expected-owner-set-sha256", "sha256:ABC",
                   "--expected-owner-count", "2"}, command, reason));
    assert(!Parse({"hepta-sessionctl", "--socket", "/tmp/supervisor.sock",
                   "paper-finalize-ack", "--token-file", "/tmp/root-token",
                   "--generation", "8", "--recovery-id", "recovery-1",
                   "--finalization-id", "finalization-1",
                   "--expected-owner-set-sha256", ownerSetSha,
                   "--expected-owner-count", "0",
                   "--receipt-sha256", receiptSha}, command, reason));
    assert(!Parse({"hepta-sessionctl", "--socket", "/tmp/supervisor.sock",
                   "recovery-query", "--token-file", "/tmp/root-token",
                   "--generation", "8"}, command, reason));
    assert(!Parse({"hepta-sessionctl", "--socket", "/tmp/supervisor.sock",
                   "recovery-query", "--token-file", "/tmp/root-token",
                   "--generation", "8", "--command-id", ""},
                  command, reason));
    assert(!Parse({"hepta-sessionctl", "--socket", "/tmp/supervisor.sock",
                   "revoke", "--token-file", "/tmp/root-token",
                   "--generation", "8", "--token-owner-uid", "-1"},
                  command, reason));
    assert(!Parse({"hepta-sessionctl", "--socket", "/tmp/supervisor.sock",
                   "revoke", "--token-file", "/tmp/root-token",
                   "--generation", "8", "--token-owner-uid", "4294967296"},
                  command, reason));
    assert(!Parse({"hepta-sessionctl", "--socket", "/tmp/supervisor.sock",
                   "revoke", "--token-file", "/tmp/root-token",
                   "--generation", "8", "--token-owner-uid", "root"},
                  command, reason));

    assert(!Parse({"hepta-sessionctl", "--bad", "value", "revoke",
                   "--token-file", "/tmp/token", "--generation", "1"},
                  command, reason));
    assert(!reason.empty());
    assert(!Parse({"hepta-sessionctl", "--socket", "/tmp/supervisor.sock",
                   "renew", "--token-file", "/tmp/token", "--generation", "0",
                   "--ttl-sec", "120"}, command, reason));

    HeptaSessionCtlTerminalCleanupCommand cleanup;
    assert(ParseTerminalCleanup({
        "hepta-sessionctl", "terminal-cleanup-hsl5-paper",
        "--store", "/var/lib/hepta-tool-gateway-alpha/session-leases.hsl2",
        "--key-file", "/etc/heptatrader/credentials/trust-domains/alpha/hepta-supervisor-lease.key",
        "--backup", "/var/lib/hepta-local-ai-paper-agent/legacy-hsl5-paper-lease-store.backup.hsl2",
        "--lock-file", "/run/hepta-agent/session-lease-terminal-cleanup.lock",
        "--expected-issuer", "hepta.os.bootstrap",
        "--expected-agent-id", "alpha",
        "--expected-peer-uid", "2104",
        "--expected-source-uid", "2101",
        "--expected-source-gid", "2101",
        "--expected-source-mode", "0600",
        "--expected-key-uid", "0",
        "--expected-key-gid", "0",
        "--expected-key-mode", "0400",
        "--expected-key-file-sha256", "sha256:" + std::string(64, 'b'),
        "--expected-pre-store-sha256", "sha256:" + std::string(64, 'a'),
    }, cleanup, reason));
    assert(cleanup.expectedIssuer == "hepta.os.bootstrap");
    assert(cleanup.expectedAgentId == "alpha");
    assert(cleanup.expectedPeerUid == 2104);
    assert(cleanup.expectedSourceUid == 2101);
    assert(cleanup.expectedSourceGid == 2101);
    assert(cleanup.expectedSourceMode == 0600);
    assert(cleanup.cleanupLockPath ==
           "/run/hepta-agent/session-lease-terminal-cleanup.lock");
    assert(cleanup.expectedKeyUid == 0);
    assert(cleanup.expectedKeyGid == 0);
    assert(cleanup.expectedKeyMode == 0400);
    assert(cleanup.expectedKeyFileSha256 ==
           "sha256:" + std::string(64, 'b'));
    assert(cleanup.expectedPreStoreSha256 ==
           "sha256:" + std::string(64, 'a'));
    assert(!ParseTerminalCleanup({
        "hepta-sessionctl", "terminal-cleanup-hsl5-paper",
        "--store", "relative-store", "--key-file", "/tmp/key",
        "--backup", "/tmp/backup",
        "--lock-file", "/run/hepta-agent/session-lease-terminal-cleanup.lock",
        "--expected-issuer", "hepta.os.bootstrap",
        "--expected-agent-id", "alpha", "--expected-peer-uid", "2104",
        "--expected-source-uid", "2101", "--expected-source-gid", "2101",
        "--expected-source-mode", "0600",
        "--expected-key-uid", "0", "--expected-key-gid", "0",
        "--expected-key-mode", "0400",
        "--expected-key-file-sha256", "sha256:" + std::string(64, 'b'),
        "--expected-pre-store-sha256", "sha256:" + std::string(64, 'a'),
    }, cleanup, reason));
    assert(!ParseTerminalCleanup({
        "hepta-sessionctl", "terminal-cleanup-hsl5-paper",
        "--store", "/tmp/store", "--key-file", "/tmp/key",
        "--backup", "/tmp/backup",
        "--lock-file", "/run/hepta-agent/session-lease-terminal-cleanup.lock",
        "--expected-issuer", "hepta.os.bootstrap",
        "--expected-agent-id", "alpha", "--expected-peer-uid", "4294967296",
        "--expected-source-uid", "2101", "--expected-source-gid", "2101",
        "--expected-source-mode", "0600",
        "--expected-key-uid", "0", "--expected-key-gid", "0",
        "--expected-key-mode", "0400",
        "--expected-key-file-sha256", "sha256:" + std::string(64, 'b'),
        "--expected-pre-store-sha256", "sha256:" + std::string(64, 'a'),
    }, cleanup, reason));
    assert(!ParseTerminalCleanup({
        "hepta-sessionctl", "terminal-cleanup-hsl5-paper",
        "--store", "/tmp/store", "--key-file", "/tmp/key",
        "--backup", "/tmp/backup",
        "--lock-file", "/run/hepta-agent/session-lease-terminal-cleanup.lock",
        "--expected-issuer", "hepta.os.bootstrap",
        "--expected-agent-id", "alpha", "--expected-peer-uid", "2104",
        "--expected-source-uid", "2101", "--expected-source-gid", "2101",
        "--expected-source-mode", "0600",
        "--expected-key-uid", "0", "--expected-key-gid", "0",
        "--expected-key-mode", "0400",
        "--expected-key-file-sha256", "sha256:" + std::string(64, 'b'),
        "--expected-pre-store-sha256", std::string(64, 'a'),
    }, cleanup, reason));
    assert(!ParseTerminalCleanup({
        "hepta-sessionctl", "terminal-cleanup-hsl5-paper",
        "--store", "/tmp/store", "--key-file", "/tmp/key",
        "--backup", "relative-backup",
        "--lock-file", "/run/hepta-agent/session-lease-terminal-cleanup.lock",
        "--expected-issuer", "hepta.os.bootstrap",
        "--expected-agent-id", "alpha", "--expected-peer-uid", "2104",
        "--expected-source-uid", "2101", "--expected-source-gid", "2101",
        "--expected-source-mode", "0600",
        "--expected-key-uid", "0", "--expected-key-gid", "0",
        "--expected-key-mode", "0400",
        "--expected-key-file-sha256", "sha256:" + std::string(64, 'b'),
        "--expected-pre-store-sha256", "sha256:" + std::string(64, 'a'),
    }, cleanup, reason));
    assert(!ParseTerminalCleanup({
        "hepta-sessionctl", "terminal-cleanup-hsl5-paper",
        "--store", "/tmp/store", "--key-file", "/tmp/key",
        "--backup", "/tmp/backup",
        "--lock-file", "/run/hepta-agent/session-lease-terminal-cleanup.lock",
        "--expected-issuer", "hepta.os.bootstrap",
        "--expected-agent-id", "alpha", "--expected-peer-uid", "2104",
        "--expected-source-uid", "2101", "--expected-source-gid", "2101",
        "--expected-source-mode", "0640",
        "--expected-key-uid", "0", "--expected-key-gid", "0",
        "--expected-key-mode", "0400",
        "--expected-key-file-sha256", "sha256:" + std::string(64, 'b'),
        "--expected-pre-store-sha256", "sha256:" + std::string(64, 'a'),
    }, cleanup, reason));

    const std::string tokenPath = TempFile();
    std::string token;
    assert(HeptaSessionCtlCommandParser::ReadTokenFile(
        tokenPath, true, static_cast<std::uint32_t>(::geteuid()), token, reason));
    assert(token == std::string(32, 'A'));
    assert(!HeptaSessionCtlCommandParser::ReadTokenFile(
        tokenPath, true, static_cast<std::uint32_t>(::geteuid() + 1),
        token, reason));
    assert(reason == "TOKEN_FILE_METADATA_REJECTED");
    const std::string linkedPath = tokenPath + ".link";
    assert(::link(tokenPath.c_str(), linkedPath.c_str()) == 0);
    assert(!HeptaSessionCtlCommandParser::ReadTokenFile(
        tokenPath, true, static_cast<std::uint32_t>(::geteuid()),
        token, reason));
    assert(reason == "TOKEN_FILE_METADATA_REJECTED");
    assert(::unlink(linkedPath.c_str()) == 0);
    assert(::chmod(tokenPath.c_str(), 0644) == 0);
    assert(!HeptaSessionCtlCommandParser::ReadTokenFile(
        tokenPath, false, 0, token, reason));
    assert(reason == "TOKEN_FILE_METADATA_REJECTED");
    assert(::unlink(tokenPath.c_str()) == 0);

    const std::string evidenceDirectory = TempEvidenceDirectory();
    const std::string evidencePath = evidenceDirectory + "/terminal.hpe1";
    const std::string terminalEvidence = "HPE1\nschema=test\n";
    WriteEvidence(evidencePath, terminalEvidence);
    std::string evidence;
    assert(HeptaSessionCtlCommandParser::ReadTerminalEvidenceFile(
        evidencePath, Sha256(terminalEvidence), evidence, reason));
    assert(evidence == terminalEvidence);
    const std::string evidenceLink = evidencePath + ".link";
    assert(::link(evidencePath.c_str(), evidenceLink.c_str()) == 0);
    assert(!HeptaSessionCtlCommandParser::ReadTerminalEvidenceFile(
        evidencePath, Sha256(terminalEvidence), evidence, reason));
    assert(reason == "TERMINAL_EVIDENCE_FILE_METADATA_REJECTED");
    assert(::unlink(evidenceLink.c_str()) == 0);
    assert(::chmod(evidencePath.c_str(), 0600) == 0);
    assert(!HeptaSessionCtlCommandParser::ReadTerminalEvidenceFile(
        evidencePath, Sha256(terminalEvidence), evidence, reason));
    assert(reason == "TERMINAL_EVIDENCE_FILE_METADATA_REJECTED");
    assert(::chmod(evidencePath.c_str(), 0400) == 0);
    assert(!HeptaSessionCtlCommandParser::ReadTerminalEvidenceFile(
        evidencePath, "sha256:" + std::string(64, '0'), evidence, reason));
    assert(reason == "TERMINAL_EVIDENCE_FILE_CONTENT_REJECTED");
    const std::string leafSymlink = evidenceDirectory + "/terminal-link.hpe1";
    assert(::symlink(evidencePath.c_str(), leafSymlink.c_str()) == 0);
    assert(!HeptaSessionCtlCommandParser::ReadTerminalEvidenceFile(
        leafSymlink, Sha256(terminalEvidence), evidence, reason));
    assert(reason == "TERMINAL_EVIDENCE_FILE_METADATA_REJECTED");
    assert(::unlink(leafSymlink.c_str()) == 0);
    const std::string parentSymlink = evidenceDirectory + ".link";
    assert(::symlink(evidenceDirectory.c_str(), parentSymlink.c_str()) == 0);
    assert(!HeptaSessionCtlCommandParser::ReadTerminalEvidenceFile(
        parentSymlink + "/terminal.hpe1", Sha256(terminalEvidence),
        evidence, reason));
    assert(reason == "TERMINAL_EVIDENCE_FILE_METADATA_REJECTED");
    assert(::unlink(parentSymlink.c_str()) == 0);
    assert(::chmod(evidenceDirectory.c_str(), 0755) == 0);
    assert(!HeptaSessionCtlCommandParser::ReadTerminalEvidenceFile(
        evidencePath, Sha256(terminalEvidence), evidence, reason));
    assert(reason == "TERMINAL_EVIDENCE_FILE_METADATA_REJECTED");
    assert(::chmod(evidenceDirectory.c_str(), 0700) == 0);

    const std::string maximumPath = evidenceDirectory + "/maximum.hpe1";
    std::string maximumEvidence(12288, 'x');
    maximumEvidence.back() = '\n';
    WriteEvidence(maximumPath, maximumEvidence);
    assert(HeptaSessionCtlCommandParser::ReadTerminalEvidenceFile(
        maximumPath, Sha256(maximumEvidence), evidence, reason));
    assert(evidence == maximumEvidence);
    const std::string oversizedPath = evidenceDirectory + "/oversized.hpe1";
    std::string oversizedEvidence(12289, 'x');
    oversizedEvidence.back() = '\n';
    WriteEvidence(oversizedPath, oversizedEvidence);
    assert(!HeptaSessionCtlCommandParser::ReadTerminalEvidenceFile(
        oversizedPath, Sha256(oversizedEvidence), evidence, reason));
    assert(reason == "TERMINAL_EVIDENCE_FILE_METADATA_REJECTED");
    assert(::unlink(oversizedPath.c_str()) == 0);
    assert(::unlink(maximumPath.c_str()) == 0);
    assert(::unlink(evidencePath.c_str()) == 0);
    assert(::rmdir(evidenceDirectory.c_str()) == 0);

    std::cout << "hepta_sessionctl_tests: PASS\n";
    return 0;
}
