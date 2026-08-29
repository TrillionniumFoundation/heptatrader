#include "hepta_sessionctl_command.h"

#include <cerrno>
#include <cstdlib>
#include <fcntl.h>
#include <limits>
#include <map>
#include <openssl/evp.h>
#include <sys/stat.h>
#include <unistd.h>

namespace
{
const uid_t kFixedAgentUid = 2004;

bool ParseUnsigned(const std::string& value, unsigned long long minimum,
                   unsigned long long maximum, unsigned long long& parsed)
{
    if (value.empty()) return false;
    char* end = nullptr;
    errno = 0;
    const unsigned long long number = std::strtoull(value.c_str(), &end, 10);
    if (errno != 0 || end == value.c_str() || *end != '\0' ||
        number < minimum || number > maximum) return false;
    parsed = number;
    return true;
}

bool CanonicalToken(const std::string& token)
{
    if (token.size() < 24 || token.size() > 512) return false;
    for (std::string::const_iterator it = token.begin(); it != token.end(); ++it)
    {
        const unsigned char byte = static_cast<unsigned char>(*it);
        if (byte < 0x21 || byte > 0x7e) return false;
    }
    return true;
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

bool FinalizationText(const std::string& value)
{
    if (value.empty() || value.size() > 128) return false;
    for (std::size_t i = 0; i < value.size(); ++i)
    {
        const unsigned char byte = static_cast<unsigned char>(value[i]);
        if (byte < 0x21 || byte > 0x7e) return false;
    }
    return true;
}

bool Require(const std::map<std::string, std::string>& options,
             const char* name, std::string& value, std::string& reason)
{
    const std::map<std::string, std::string>::const_iterator found = options.find(name);
    if (found == options.end() || found->second.empty())
    {
        reason = std::string("MISSING_OPTION:") + name;
        return false;
    }
    value = found->second;
    return true;
}

bool Sha256(const std::string& value, std::string& digest)
{
    unsigned char bytes[EVP_MAX_MD_SIZE];
    unsigned int length = 0;
    EVP_MD_CTX* context = EVP_MD_CTX_new();
    if (context == nullptr) return false;
    const bool ok = EVP_DigestInit_ex(context, EVP_sha256(), nullptr) == 1 &&
        EVP_DigestUpdate(context, value.data(), value.size()) == 1 &&
        EVP_DigestFinal_ex(context, bytes, &length) == 1;
    EVP_MD_CTX_free(context);
    if (!ok || length != 32) return false;
    static const char digits[] = "0123456789abcdef";
    digest = "sha256:";
    for (unsigned int i = 0; i < length; ++i)
    {
        digest.push_back(digits[bytes[i] >> 4]);
        digest.push_back(digits[bytes[i] & 15]);
    }
    return true;
}

bool OpenEvidenceParent(const std::string& path, int& directoryFd,
    std::string& leaf)
{
    directoryFd = -1;
    leaf.clear();
    if (path.empty() || path[0] != '/' || path.back() == '/') return false;
    const std::size_t last = path.rfind('/');
    if (last == std::string::npos || last + 1 >= path.size()) return false;
    leaf = path.substr(last + 1);
    if (leaf.empty() || leaf == "." || leaf == "..") return false;
    const std::string parent = last == 0 ? "/" : path.substr(0, last);
    int current = ::open("/", O_RDONLY | O_DIRECTORY | O_CLOEXEC);
    if (current < 0) return false;
    std::size_t offset = 1;
    while (offset < parent.size())
    {
        const std::size_t separator = parent.find('/', offset);
        const std::string component = parent.substr(offset,
            separator == std::string::npos ? std::string::npos :
            separator - offset);
        if (component.empty() || component == "." || component == "..")
        {
            ::close(current);
            return false;
        }
        const int next = ::openat(current, component.c_str(),
            O_RDONLY | O_DIRECTORY | O_CLOEXEC | O_NOFOLLOW);
        ::close(current);
        if (next < 0) return false;
        current = next;
        if (separator == std::string::npos) break;
        offset = separator + 1;
    }
    struct stat metadata;
    if (::fstat(current, &metadata) != 0 ||
        !S_ISDIR(metadata.st_mode) || metadata.st_uid != ::geteuid() ||
        (metadata.st_mode & 07777) != 0700)
    {
        ::close(current);
        return false;
    }
    directoryFd = current;
    return true;
}
}

const char* HeptaSessionCtlCommandParser::Usage()
{
    return "usage: hepta-sessionctl [--socket PATH] [--io-timeout-ms N] "
           "provision --template watch|paper --token-file PATH --agent-id ID "
           "--session-id ID --peer-uid UID --ttl-sec N | "
           "revoke --token-file PATH --generation N [--token-owner-uid UID] | "
           "recovery-query --token-file PATH --generation N --command-id ID "
           "[--require-paper-finalization] [--token-owner-uid UID] | "
           "paper-finalize --token-file PATH --generation N --recovery-id ID "
           "--finalization-id ID --expected-owner-set-sha256 SHA256 "
           "--expected-owner-count N [--token-owner-uid UID] | "
           "paper-finalize-ack --token-file PATH --generation N "
           "--recovery-id ID --finalization-id ID "
           "--expected-owner-set-sha256 SHA256 --expected-owner-count N "
           "--receipt-sha256 SHA256 [--token-owner-uid UID] | "
		   "paper-terminalize-ack --token-file PATH --generation N "
		   "--recovery-id ID --finalization-id ID "
		   "--expected-owner-set-sha256 SHA256 --expected-owner-count N "
		   "--receipt-sha256 SHA256 [--token-owner-uid UID] | "
		   "paper-terminal-witness-prepare --token-file PATH --generation N "
		   "--recovery-id ID --finalization-id ID "
		   "--expected-owner-set-sha256 SHA256 --expected-owner-count N "
		   "--receipt-sha256 SHA256 [--token-owner-uid UID] | "
           "paper-terminal-witness-ack --token-file PATH --generation N "
           "--recovery-id ID --finalization-id ID "
           "--expected-owner-set-sha256 SHA256 --expected-owner-count N "
           "--receipt-sha256 SHA256 --terminal-evidence-file PATH "
           "--terminal-evidence-sha256 SHA256 [--token-owner-uid UID] | "
           "renew --token-file PATH --generation N --ttl-sec N "
           "[--token-owner-uid UID] | "
           "rotate --token-file PATH --replacement-token-file PATH "
           "--generation N --ttl-sec N [--token-owner-uid UID]";
}

bool HeptaSessionCtlCommandParser::Parse(
    int argc, char** argv, HeptaSessionCtlCommand& command, std::string& reason)
{
    command = HeptaSessionCtlCommand();
    const char* socket = std::getenv("HEPTA_TOOL_SUPERVISOR_SOCKET");
    if (socket != nullptr) command.socketPath = socket;

    int index = 1;
    while (index < argc && std::string(argv[index]).find("--") == 0)
    {
        const std::string option = argv[index++];
        if (option != "--socket" && option != "--io-timeout-ms")
        {
            reason = "UNKNOWN_GLOBAL_OPTION:" + option;
            return false;
        }
        if (index >= argc)
        {
            reason = "MISSING_GLOBAL_OPTION_VALUE:" + option;
            return false;
        }
        const std::string value = argv[index++];
        if (option == "--socket") command.socketPath = value;
        else
        {
            unsigned long long parsed = 0;
            if (!ParseUnsigned(value, 1, 120000, parsed))
            {
                reason = "INVALID_IO_TIMEOUT";
                return false;
            }
            command.ioTimeoutMs = static_cast<int>(parsed);
        }
    }
    if (index >= argc)
    {
        reason = "MISSING_COMMAND";
        return false;
    }

    const std::string operation = argv[index++];
    if (operation == "provision") command.request.operation = SessionSupervisorOperation::Provision;
    else if (operation == "revoke") command.request.operation = SessionSupervisorOperation::Revoke;
    else if (operation == "renew") command.request.operation = SessionSupervisorOperation::Renew;
    else if (operation == "rotate") command.request.operation = SessionSupervisorOperation::Rotate;
    else if (operation == "recovery-query")
        command.request.operation = SessionSupervisorOperation::RecoveryQuery;
    else if (operation == "paper-finalize")
        command.request.operation = SessionSupervisorOperation::PaperFinalize;
    else if (operation == "paper-finalize-ack")
        command.request.operation =
            SessionSupervisorOperation::PaperFinalizeAck;
	else if (operation == "paper-terminalize-ack")
		command.request.operation =
			SessionSupervisorOperation::PaperTerminalizeAck;
    else if (operation == "paper-terminal-witness-prepare")
        command.request.operation =
            SessionSupervisorOperation::PaperTerminalWitnessPrepare;
    else if (operation == "paper-terminal-witness-ack")
        command.request.operation =
            SessionSupervisorOperation::PaperTerminalWitnessAck;
    else
    {
        reason = "INVALID_COMMAND";
        return false;
    }

    std::map<std::string, std::string> options;
    while (index < argc)
    {
        const std::string option = argv[index++];
        if (option.find("--") != 0)
        {
            reason = "INVALID_COMMAND_OPTION:" + option;
            return false;
        }
        if (options.find(option) != options.end())
        {
            reason = "DUPLICATE_COMMAND_OPTION:" + option;
            return false;
        }
        if (option == "--require-paper-finalization")
        {
            options[option] = "1";
            continue;
        }
        if (index >= argc)
        {
            reason = "INVALID_COMMAND_OPTION:" + option;
            return false;
        }
        options[option] = argv[index++];
    }
    if (command.socketPath.empty())
    {
        reason = "MISSING_SUPERVISOR_SOCKET";
        return false;
    }
    if (!Require(options, "--token-file", command.tokenFile, reason)) return false;

    std::string value;
    unsigned long long parsed = 0;
    if (command.request.operation == SessionSupervisorOperation::Provision)
    {
        if (options.size() != 6 ||
            !Require(options, "--template", command.request.templateId, reason) ||
            (command.request.templateId != "watch" && command.request.templateId != "paper") ||
            !Require(options, "--agent-id", command.request.agentId, reason) ||
            !Require(options, "--session-id", command.request.sessionId, reason) ||
            !Require(options, "--peer-uid", value, reason) ||
            !ParseUnsigned(value, 0, std::numeric_limits<std::uint32_t>::max(), parsed))
        {
            if (reason.empty()) reason = "INVALID_PROVISION_OPTIONS";
            return false;
        }
        command.request.peerUid = static_cast<std::uint32_t>(parsed);
        if (!Require(options, "--ttl-sec", value, reason) ||
            !ParseUnsigned(value, 60, 86400, parsed))
        {
            if (reason.empty()) reason = "INVALID_TTL";
            return false;
        }
        command.request.ttlMs = parsed * 1000;
    }
    else
    {
        const bool hasTokenOwner = options.find("--token-owner-uid") != options.end();
        const bool requirePaperFinalization =
            options.find("--require-paper-finalization") != options.end();
        const bool finalization = command.request.operation ==
                SessionSupervisorOperation::PaperFinalize ||
            command.request.operation ==
				SessionSupervisorOperation::PaperFinalizeAck ||
			command.request.operation ==
				SessionSupervisorOperation::PaperTerminalizeAck ||
			command.request.operation ==
				SessionSupervisorOperation::PaperTerminalWitnessPrepare ||
			command.request.operation ==
				SessionSupervisorOperation::PaperTerminalWitnessAck;
		const bool terminalWitness = command.request.operation ==
			SessionSupervisorOperation::PaperTerminalWitnessAck;
        const std::size_t expected = (command.request.operation ==
            SessionSupervisorOperation::Revoke ? 2 :
            (command.request.operation == SessionSupervisorOperation::RecoveryQuery ?
                3 + (requirePaperFinalization ? 1 : 0) :
            (command.request.operation == SessionSupervisorOperation::Renew ? 3 :
            (command.request.operation == SessionSupervisorOperation::Rotate ? 4 :
            (command.request.operation ==
                SessionSupervisorOperation::PaperFinalize ? 6 :
				(terminalWitness ? 9 : 7)))))) +
            (hasTokenOwner ? 1 : 0);
        if (options.size() != expected ||
            !Require(options, "--generation", value, reason) ||
            !ParseUnsigned(value, 1, std::numeric_limits<std::uint64_t>::max(), parsed))
        {
            if (reason.empty()) reason = "INVALID_GENERATION";
            return false;
        }
        command.request.expectedGeneration = static_cast<std::uint64_t>(parsed);
        if (hasTokenOwner)
        {
            if (!Require(options, "--token-owner-uid", value, reason) ||
                !ParseUnsigned(
                    value, 0, std::numeric_limits<std::uint32_t>::max(), parsed))
            {
                if (reason.empty()) reason = "INVALID_TOKEN_OWNER_UID";
                return false;
            }
            command.hasTokenOwnerUid = true;
            command.tokenOwnerUid = static_cast<std::uint32_t>(parsed);
        }
        if (finalization)
        {
            if (!Require(options, "--recovery-id",
                    command.request.recoveryId, reason) ||
                !Require(options, "--finalization-id",
                    command.request.finalizationId, reason) ||
                !Require(options, "--expected-owner-set-sha256",
                    command.request.expectedOwnerSetSha256, reason) ||
                !Require(options, "--expected-owner-count", value, reason) ||
                !FinalizationText(command.request.recoveryId) ||
                !FinalizationText(command.request.finalizationId) ||
                !CanonicalSha256(
                    command.request.expectedOwnerSetSha256) ||
                !ParseUnsigned(value, 1, 4096, parsed))
            {
                if (reason.empty())
                    reason = "INVALID_PAPER_FINALIZATION_OPTIONS";
                return false;
            }
            command.request.expectedOwnerCount =
                static_cast<std::uint64_t>(parsed);
			if (command.request.operation ==
					SessionSupervisorOperation::PaperFinalizeAck ||
				command.request.operation ==
					SessionSupervisorOperation::PaperTerminalizeAck ||
				command.request.operation ==
					SessionSupervisorOperation::PaperTerminalWitnessPrepare ||
				command.request.operation ==
					SessionSupervisorOperation::PaperTerminalWitnessAck)
            {
                if (!Require(options, "--receipt-sha256",
                        command.request.receiptSha256, reason) ||
                    !CanonicalSha256(command.request.receiptSha256))
                {
                    if (reason.empty()) reason = "INVALID_RECEIPT_SHA256";
                    return false;
                }
            }
			if (terminalWitness &&
				(!Require(options, "--terminal-evidence-file",
					command.terminalEvidenceFile, reason) ||
				 !Require(options, "--terminal-evidence-sha256",
					command.request.terminalEvidenceSha256, reason) ||
				 !CanonicalSha256(command.request.terminalEvidenceSha256)))
			{
				if (reason.empty())
					reason = "INVALID_TERMINAL_EVIDENCE_OPTIONS";
				return false;
			}
        }
        else if (command.request.operation == SessionSupervisorOperation::RecoveryQuery)
        {
            if (!Require(options, "--command-id",
                    command.request.targetCommandId, reason) ||
                command.request.targetCommandId.size() > 128)
            {
                if (reason.empty()) reason = "INVALID_COMMAND_ID";
                return false;
            }
            command.request.requirePaperFinalization =
                requirePaperFinalization;
        }
        else if (command.request.operation != SessionSupervisorOperation::Revoke)
        {
            if (!Require(options, "--ttl-sec", value, reason) ||
                !ParseUnsigned(value, 60, 86400, parsed))
            {
                if (reason.empty()) reason = "INVALID_TTL";
                return false;
            }
            command.request.ttlMs = parsed * 1000;
        }
        if (command.request.operation == SessionSupervisorOperation::Rotate &&
            !Require(options, "--replacement-token-file", command.replacementTokenFile, reason))
            return false;
    }
    reason.clear();
    return true;
}

bool HeptaSessionCtlCommandParser::ReadTokenFile(
    const std::string& path, bool hasExpectedOwnerUid,
    std::uint32_t expectedOwnerUid, std::string& token, std::string& reason)
{
    token.clear();
    struct stat before;
    if (path.empty() || ::lstat(path.c_str(), &before) != 0)
    {
        reason = "TOKEN_FILE_METADATA_REJECTED";
        return false;
    }
    const uid_t effectiveUid = ::geteuid();
    const bool trustedOwner = hasExpectedOwnerUid ?
        (before.st_uid == static_cast<uid_t>(expectedOwnerUid) &&
         (effectiveUid == 0 || effectiveUid == before.st_uid)) :
        (before.st_uid == 0 || before.st_uid == effectiveUid ||
         (effectiveUid == 0 && before.st_uid == kFixedAgentUid));
    if (!S_ISREG(before.st_mode) || S_ISLNK(before.st_mode) ||
        (before.st_mode & 0077) != 0 ||
        before.st_nlink != 1 ||
        !trustedOwner ||
        before.st_size < 24 || before.st_size > 514)
    {
        reason = "TOKEN_FILE_METADATA_REJECTED";
        return false;
    }
    const int descriptor = ::open(path.c_str(), O_RDONLY | O_CLOEXEC | O_NOFOLLOW);
    if (descriptor < 0)
    {
        reason = "TOKEN_FILE_OPEN_FAILED";
        return false;
    }
    struct stat after;
    char buffer[515];
    std::size_t offset = 0;
    bool readOk = true;
    while (offset < static_cast<std::size_t>(before.st_size))
    {
        const ssize_t count = ::read(
            descriptor, buffer + offset,
            static_cast<std::size_t>(before.st_size) - offset);
        if (count < 0 && errno == EINTR) continue;
        if (count <= 0)
        {
            readOk = false;
            break;
        }
        offset += static_cast<std::size_t>(count);
    }
    const bool metadataStable = ::fstat(descriptor, &after) == 0 &&
        before.st_dev == after.st_dev && before.st_ino == after.st_ino &&
        before.st_mode == after.st_mode &&
        before.st_nlink == after.st_nlink &&
        before.st_uid == after.st_uid && before.st_size == after.st_size;
    ::close(descriptor);
    if (!readOk || offset != static_cast<std::size_t>(before.st_size) || !metadataStable)
    {
        reason = "TOKEN_FILE_READ_UNSTABLE";
        return false;
    }
    token.assign(buffer, offset);
    while (!token.empty() && (token[token.size() - 1] == '\n' ||
                              token[token.size() - 1] == '\r'))
        token.erase(token.size() - 1);
    if (!CanonicalToken(token))
    {
        token.clear();
        reason = "TOKEN_FILE_CONTENT_REJECTED";
        return false;
    }
    reason.clear();
    return true;
}

bool HeptaSessionCtlCommandParser::ReadTerminalEvidenceFile(
    const std::string& path, const std::string& expectedSha256,
    std::string& evidence, std::string& reason)
{
    evidence.clear();
    int directoryFd = -1;
    std::string leaf;
    struct stat before;
    struct stat parentBefore;
    if (!CanonicalSha256(expectedSha256) ||
        !OpenEvidenceParent(path, directoryFd, leaf) ||
        ::fstat(directoryFd, &parentBefore) != 0 ||
        ::fstatat(directoryFd, leaf.c_str(), &before,
            AT_SYMLINK_NOFOLLOW) != 0 ||
        !S_ISREG(before.st_mode) || S_ISLNK(before.st_mode) ||
        (before.st_mode & 07777) != 0400 || before.st_nlink != 1 ||
        before.st_uid != ::geteuid() || before.st_size <= 0 ||
        before.st_size > 12288)
    {
        if (directoryFd >= 0) ::close(directoryFd);
        reason = "TERMINAL_EVIDENCE_FILE_METADATA_REJECTED";
        return false;
    }
    const int descriptor = ::openat(directoryFd, leaf.c_str(),
        O_RDONLY | O_CLOEXEC | O_NOFOLLOW | O_NONBLOCK);
    if (descriptor < 0)
    {
        ::close(directoryFd);
        reason = "TERMINAL_EVIDENCE_FILE_OPEN_FAILED";
        return false;
    }
    evidence.assign(static_cast<std::size_t>(before.st_size), '\0');
    std::size_t offset = 0;
    bool ok = true;
    while (ok && offset < evidence.size())
    {
        const ssize_t count = ::read(descriptor, &evidence[offset],
            evidence.size() - offset);
        if (count < 0 && errno == EINTR) continue;
        if (count <= 0) ok = false;
        else offset += static_cast<std::size_t>(count);
    }
    char extra = 0;
    ssize_t extraCount = -1;
    do { extraCount = ::read(descriptor, &extra, 1); }
    while (extraCount < 0 && errno == EINTR);
    struct stat after;
    struct stat parentAfter;
    const bool metadataStable = ::fstat(descriptor, &after) == 0 &&
        before.st_dev == after.st_dev && before.st_ino == after.st_ino &&
        before.st_mode == after.st_mode && before.st_uid == after.st_uid &&
        before.st_gid == after.st_gid && before.st_nlink == after.st_nlink &&
        before.st_size == after.st_size &&
        before.st_mtim.tv_sec == after.st_mtim.tv_sec &&
        before.st_mtim.tv_nsec == after.st_mtim.tv_nsec &&
        before.st_ctim.tv_sec == after.st_ctim.tv_sec &&
        before.st_ctim.tv_nsec == after.st_ctim.tv_nsec &&
        ::fstat(directoryFd, &parentAfter) == 0 &&
        parentBefore.st_dev == parentAfter.st_dev &&
        parentBefore.st_ino == parentAfter.st_ino &&
        parentBefore.st_mode == parentAfter.st_mode &&
        parentBefore.st_uid == parentAfter.st_uid &&
        parentBefore.st_gid == parentAfter.st_gid;
    if (::close(descriptor) != 0) ok = false;
    if (::close(directoryFd) != 0) ok = false;
    std::string digest;
    if (!ok || offset != evidence.size() || extraCount != 0 ||
        !metadataStable || evidence.back() != '\n' ||
        !Sha256(evidence, digest) || digest != expectedSha256)
    {
        evidence.clear();
        reason = "TERMINAL_EVIDENCE_FILE_CONTENT_REJECTED";
        return false;
    }
    reason.clear();
    return true;
}
