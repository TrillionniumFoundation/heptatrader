#include "paper_terminal_external_latch.h"

#include <cerrno>
#include <fcntl.h>
#include <limits>
#include <locale>
#include <map>
#include <sstream>
#include <vector>
#include <openssl/evp.h>
#include <sys/file.h>
#include <sys/stat.h>
#include <sys/syscall.h>
#include <unistd.h>

#if defined(__linux__)
#include <linux/fs.h>
#endif

namespace
{
const char kTerminalizingLatchFile[] = "ib-paper-terminal-halt.v1";
const char kExternalLatchFile[] = "ib-paper-terminal-external-halt.v1";
const char kRuntimeLockFile[] = "ib-paper-runtime.lock";
const std::size_t kMaximumTerminalizingLatchBytes = 16384;
const std::size_t kMaximumCapsuleBytes = 192 * 1024;
const std::size_t kMaximumExternalLatchBytes = 8192;

struct OpenDirectory
{
    int fd;
    OpenDirectory() : fd(-1) {}
    ~OpenDirectory() { if (fd >= 0) ::close(fd); }
};

struct LockedFile
{
    int fd;
    LockedFile() : fd(-1) {}
    ~LockedFile()
    {
        if (fd >= 0)
        {
            ::flock(fd, LOCK_UN);
            ::close(fd);
        }
    }
};

bool Sha256(const std::string& value, std::string& digest)
{
    unsigned char bytes[EVP_MAX_MD_SIZE];
    unsigned int length = 0;
    EVP_MD_CTX* context = EVP_MD_CTX_new();
    if (context == nullptr) return false;
    const bool ok =
        EVP_DigestInit_ex(context, EVP_sha256(), nullptr) == 1 &&
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

bool IsSha256(const std::string& value)
{
    if (value.size() != 71 || value.compare(0, 7, "sha256:") != 0)
        return false;
    for (std::size_t i = 7; i < value.size(); ++i)
        if (!((value[i] >= '0' && value[i] <= '9') ||
              (value[i] >= 'a' && value[i] <= 'f')))
            return false;
    return true;
}

bool IsNonzeroSha256(const std::string& value)
{
    return IsSha256(value) &&
        value != "sha256:0000000000000000000000000000000000000000000000000000000000000000";
}

bool IsIdentifier(const std::string& value, std::size_t maximum = 128)
{
    if (value.empty() || value.size() > maximum ||
        !((value[0] >= 'A' && value[0] <= 'Z') ||
          (value[0] >= 'a' && value[0] <= 'z') ||
          (value[0] >= '0' && value[0] <= '9')))
        return false;
    for (std::size_t i = 1; i < value.size(); ++i)
        if (!((value[i] >= 'A' && value[i] <= 'Z') ||
              (value[i] >= 'a' && value[i] <= 'z') ||
              (value[i] >= '0' && value[i] <= '9') ||
              value[i] == '.' || value[i] == '_' || value[i] == ':' ||
              value[i] == '-'))
            return false;
    return true;
}

bool IsTerminalText(const std::string& value, std::size_t maximum = 128)
{
    if (value.empty() || value.size() > maximum) return false;
    for (std::size_t i = 0; i < value.size(); ++i)
    {
        const unsigned char byte = static_cast<unsigned char>(value[i]);
        if (byte < 0x21 || byte > 0x7e || byte == '=') return false;
    }
    return true;
}

bool ParseUnsigned(const std::string& value, std::uint64_t& parsed)
{
    if (value.empty() || (value.size() > 1 && value[0] == '0')) return false;
    parsed = 0;
    for (std::size_t i = 0; i < value.size(); ++i)
    {
        if (value[i] < '0' || value[i] > '9') return false;
        const std::uint64_t digit =
            static_cast<std::uint64_t>(value[i] - '0');
        if (parsed >
            (std::numeric_limits<std::uint64_t>::max() - digit) / 10)
            return false;
        parsed = parsed * 10 + digit;
    }
    return true;
}

bool DecodeHex(const std::string& encoded, std::string& decoded)
{
    if (encoded.empty() || encoded.size() > 131072 ||
        (encoded.size() % 2) != 0)
        return false;
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

bool IsBootId(const std::string& value)
{
    if (value.size() != 36) return false;
    for (std::size_t i = 0; i < value.size(); ++i)
    {
        if (i == 8 || i == 13 || i == 18 || i == 23)
        {
            if (value[i] != '-') return false;
        }
        else if (!((value[i] >= '0' && value[i] <= '9') ||
                   (value[i] >= 'a' && value[i] <= 'f')))
            return false;
    }
    return true;
}

bool OpenAbsoluteDirectoryNoSymlink(
    const std::string& path, OpenDirectory& directory)
{
    if (path.empty() || path[0] != '/' ||
        (path.size() > 1 && path.back() == '/')) return false;
    int current = ::open("/", O_RDONLY | O_DIRECTORY | O_CLOEXEC);
    if (current < 0) return false;
    if (path == "/")
    {
        directory.fd = current;
        return true;
    }
    std::size_t offset = 1;
    while (offset < path.size())
    {
        const std::size_t separator = path.find('/', offset);
        const std::string component = path.substr(offset,
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
    directory.fd = current;
    return true;
}

bool SplitAbsoluteFilePath(const std::string& path,
    std::string& directory, std::string& name)
{
    if (path.empty() || path[0] != '/' || path.back() == '/') return false;
    const std::size_t separator = path.rfind('/');
    if (separator == std::string::npos || separator + 1 >= path.size())
        return false;
    directory = separator == 0 ? "/" : path.substr(0, separator);
    name = path.substr(separator + 1);
    return !name.empty() && name != "." && name != ".." &&
        name.find('/') == std::string::npos;
}

bool ReadRegularFileAt(int directoryFd, const std::string& name,
    uid_t expectedOwnerUid, gid_t expectedOwnerGid, mode_t expectedMode,
    std::size_t maximumBytes, std::string& contents)
{
    const int fd = ::openat(directoryFd, name.c_str(),
        O_RDONLY | O_CLOEXEC | O_NOFOLLOW | O_NONBLOCK);
    if (fd < 0) return false;
    struct stat metadata;
    const bool statOk = ::fstat(fd, &metadata) == 0;
    bool ok = statOk && S_ISREG(metadata.st_mode) && metadata.st_nlink == 1 &&
        metadata.st_uid == expectedOwnerUid &&
        metadata.st_gid == expectedOwnerGid &&
        (metadata.st_mode & 07777) == expectedMode && metadata.st_size > 0 &&
        static_cast<std::uint64_t>(metadata.st_size) <= maximumBytes;
    if (ok)
    {
        contents.assign(static_cast<std::size_t>(metadata.st_size), '\0');
        std::size_t offset = 0;
        while (ok && offset < contents.size())
        {
            const ssize_t count = ::read(fd, &contents[offset],
                contents.size() - offset);
            if (count < 0 && errno == EINTR) continue;
            if (count <= 0) ok = false;
            else offset += static_cast<std::size_t>(count);
        }
        char extra = 0;
        ssize_t count = -1;
        do { count = ::read(fd, &extra, 1); }
        while (count < 0 && errno == EINTR);
        ok = ok && count == 0;
    }
    if (::close(fd) != 0) ok = false;
    return ok;
}

bool ReadAbsoluteCapsule(const std::string& path, uid_t expectedOwnerUid,
    gid_t expectedOwnerGid, mode_t expectedMode, std::string& contents)
{
    std::string directoryPath;
    std::string name;
    if (!SplitAbsoluteFilePath(path, directoryPath, name)) return false;
    OpenDirectory directory;
    return OpenAbsoluteDirectoryNoSymlink(directoryPath, directory) &&
        ReadRegularFileAt(directory.fd, name, expectedOwnerUid,
            expectedOwnerGid, expectedMode,
            kMaximumCapsuleBytes, contents);
}

bool ReadCurrentBootId(std::string& bootId)
{
    OpenDirectory directory;
    if (!OpenAbsoluteDirectoryNoSymlink(
            "/proc/sys/kernel/random", directory))
        return false;
    const int fd = ::openat(directory.fd, "boot_id",
        O_RDONLY | O_CLOEXEC | O_NOFOLLOW | O_NONBLOCK);
    if (fd < 0) return false;
    struct stat metadata;
    bool ok = ::fstat(fd, &metadata) == 0 &&
        S_ISREG(metadata.st_mode) && metadata.st_uid == 0 &&
        metadata.st_gid == 0 && (metadata.st_mode & 07777) == 0444 &&
        metadata.st_nlink == 1;
    std::string contents;
    char buffer[64];
    while (ok)
    {
        const ssize_t count = ::read(fd, buffer, sizeof(buffer));
        if (count < 0 && errno == EINTR) continue;
        if (count < 0)
        {
            ok = false;
            break;
        }
        if (count == 0) break;
        if (contents.size() + static_cast<std::size_t>(count) >
            sizeof(buffer))
        {
            ok = false;
            break;
        }
        contents.append(buffer, static_cast<std::size_t>(count));
    }
    if (::close(fd) != 0) ok = false;
    if (!ok || contents.size() != 37 || contents.back() != '\n')
        return false;
    contents.resize(contents.size() - 1);
    if (!IsBootId(contents) ||
        contents == "00000000-0000-0000-0000-000000000000")
        return false;
    bootId = contents;
    return true;
}

bool AcquireRuntimeLock(int directoryFd, uid_t expectedOwnerUid,
    LockedFile& lock)
{
    const int fd = ::openat(directoryFd, kRuntimeLockFile,
        O_RDWR | O_CLOEXEC | O_NOFOLLOW | O_NONBLOCK);
    if (fd < 0) return false;
    struct stat metadata;
    const bool ok = ::fstat(fd, &metadata) == 0 &&
        S_ISREG(metadata.st_mode) && metadata.st_uid == expectedOwnerUid &&
        (metadata.st_mode & 07777) == 0600 && metadata.st_nlink == 1 &&
        ::flock(fd, LOCK_EX | LOCK_NB) == 0;
    if (!ok)
    {
        ::close(fd);
        return false;
    }
    lock.fd = fd;
    return true;
}

bool ParseExactLines(const std::string& contents, const char* header,
    const char* const* keys, std::size_t keyCount,
    std::map<std::string, std::string>& fields,
    std::string* prefixBeforeLast = nullptr)
{
    if (contents.empty() || contents.back() != '\n') return false;
    std::istringstream input(contents);
    std::string line;
    if (!std::getline(input, line) || line != header) return false;
    fields.clear();
    std::ostringstream prefix;
    prefix << header << '\n';
    for (std::size_t i = 0; i < keyCount; ++i)
    {
        if (!std::getline(input, line)) return false;
        const std::string expectedPrefix = std::string(keys[i]) + "=";
        if (line.compare(0, expectedPrefix.size(), expectedPrefix) != 0 ||
            line.size() == expectedPrefix.size())
            return false;
        const std::string value = line.substr(expectedPrefix.size());
        if (value.find('=') != std::string::npos ||
            !fields.insert(std::make_pair(keys[i], value)).second)
            return false;
        if (i + 1 < keyCount) prefix << line << '\n';
    }
    if (std::getline(input, line)) return false;
    if (prefixBeforeLast != nullptr) *prefixBeforeLast = prefix.str();
    return true;
}

bool ValidateOwnerSet(const std::string& encoded,
    const std::string& expectedSha256, std::uint64_t expectedCount,
    const std::string& account, const std::string& domain)
{
    std::string canonical;
    std::string digest;
    if (!DecodeHex(encoded, canonical) || canonical.empty() ||
        canonical.back() != '\n' || !Sha256(canonical, digest) ||
        digest != expectedSha256)
        return false;
    std::istringstream input(canonical);
    std::string line;
    std::string previous;
    std::uint64_t count = 0;
    while (std::getline(input, line))
    {
        if (line.empty() || (!previous.empty() && line <= previous))
            return false;
        previous = line;
        std::vector<std::string> values;
        std::size_t offset = 0;
        for (int field = 0; field < 4; ++field)
        {
            const std::size_t separator = line.find('\t', offset);
            if (field < 3 && separator == std::string::npos) return false;
            if (field == 3 && separator != std::string::npos) return false;
            values.push_back(line.substr(offset,
                separator == std::string::npos ? std::string::npos :
                separator - offset));
            offset = separator == std::string::npos ? line.size() :
                separator + 1;
        }
        std::uint64_t generation = 0;
        std::string decodedAccount;
        std::string decodedDomain;
        if (!IsNonzeroSha256(values[0]) ||
            !ParseUnsigned(values[1], generation) || generation == 0 ||
            !DecodeHex(values[2], decodedAccount) ||
            !DecodeHex(values[3], decodedDomain) ||
            decodedAccount != account || decodedDomain != domain)
            return false;
        ++count;
    }
    return count == expectedCount;
}

bool ParseTerminalizingLatch(const std::string& contents,
    std::map<std::string, std::string>& fields)
{
    static const char* const keys[] = {
        "state", "finalization_id",
        "preliminary_finalization_receipt_sha256", "owner_agent_id",
        "owner_session_id", "owner_account", "owner_execution_domain",
        "recovery_ingress_fence", "terminalization_service_epoch",
        "terminalization_service_fencing_generation",
        "terminalization_generation"
    };
    if (!ParseExactLines(contents, "HPT1", keys,
        sizeof(keys) / sizeof(keys[0]), fields))
        return false;
    std::uint64_t recoveryFence = 0;
    std::uint64_t serviceFence = 0;
    std::uint64_t terminalGeneration = 0;
    return fields["state"] == "TERMINALIZING" &&
        IsIdentifier(fields["finalization_id"]) &&
        IsNonzeroSha256(
            fields["preliminary_finalization_receipt_sha256"]) &&
        IsIdentifier(fields["owner_agent_id"]) &&
        IsIdentifier(fields["owner_session_id"]) &&
        IsTerminalText(fields["owner_account"]) &&
        IsIdentifier(fields["owner_execution_domain"]) &&
        ParseUnsigned(fields["recovery_ingress_fence"], recoveryFence) &&
        recoveryFence > 0 &&
        IsIdentifier(fields["terminalization_service_epoch"]) &&
        ParseUnsigned(fields["terminalization_service_fencing_generation"],
            serviceFence) && serviceFence > 0 &&
        ParseUnsigned(fields["terminalization_generation"],
            terminalGeneration) && terminalGeneration == 1;
}

const char* const kCapsuleKeys[] = {
    "schema", "version", "status", "terminal_proof_kind",
    "recovery_id", "finalization_id", "campaign_id", "cycle_id",
    "expected_owner_set_sha256", "expected_owner_count",
    "owner_set_canonical_hex", "preliminary_finalization_receipt_sha256",
    "owner_agent_id", "owner_session_id", "owner_account",
    "owner_execution_domain", "account_id_sha256",
    "execution_service_epoch", "execution_service_fencing_generation",
    "recovery_ingress_fence", "terminalization_generation",
    "terminalizing_latch_sha256",
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
    "authority_granted", "terminal_witness_durable",
    "capsule_body_sha256"
};

bool ValidateCapsule(const std::string& contents,
    const std::map<std::string, std::string>& terminalizing,
    const std::string& terminalizingSha256,
    const std::string& currentBootId,
    std::map<std::string, std::string>& fields,
    std::string& capsuleBodySha256)
{
    std::string body;
    if (!ParseExactLines(contents, "HPC1", kCapsuleKeys,
        sizeof(kCapsuleKeys) / sizeof(kCapsuleKeys[0]), fields, &body) ||
        !Sha256(body, capsuleBodySha256) ||
        fields["capsule_body_sha256"] != capsuleBodySha256)
        return false;
    const char* const identifiers[] = {
        "recovery_id", "finalization_id", "campaign_id", "cycle_id",
        "owner_agent_id", "owner_session_id", "owner_execution_domain",
        "execution_service_epoch", "provider_id"
    };
    for (std::size_t i = 0;
         i < sizeof(identifiers) / sizeof(identifiers[0]); ++i)
        if (!IsIdentifier(fields[identifiers[i]])) return false;
    if (!IsTerminalText(fields["owner_account"])) return false;
    const char* const digests[] = {
        "expected_owner_set_sha256",
        "preliminary_finalization_receipt_sha256", "account_id_sha256",
        "terminalizing_latch_sha256",
        "transport_cutoff_receipt_file_sha256",
        "transport_cutoff_receipt_body_sha256",
        "post_cutoff_terminal_witness_file_sha256",
        "post_cutoff_terminal_witness_body_sha256",
        "provider_trust_policy_file_sha256",
        "provider_trust_policy_body_sha256",
        "signed_account_payload_sha256", "signed_account_signature_sha256",
        "egress_policy_sha256", "known_mutation_command_set_sha256",
        "known_correlation_set_sha256", "capsule_body_sha256"
    };
    for (std::size_t i = 0; i < sizeof(digests) / sizeof(digests[0]); ++i)
        if (!IsNonzeroSha256(fields[digests[i]])) return false;

    std::uint64_t ownerCount = 0;
    std::uint64_t serviceFence = 0;
    std::uint64_t recoveryFence = 0;
    std::uint64_t terminalGeneration = 0;
    std::uint64_t publisherPid = 0;
    std::uint64_t publisherStartTicks = 0;
    std::uint64_t egressGeneration = 0;
    std::uint64_t knownMutationCount = 0;
    std::uint64_t knownCorrelationCount = 0;
    std::uint64_t settledMutationCount = 0;
    if (!ParseUnsigned(fields["expected_owner_count"], ownerCount) ||
        ownerCount == 0 || ownerCount > 128 ||
        !ParseUnsigned(fields["execution_service_fencing_generation"],
            serviceFence) || serviceFence == 0 ||
        !ParseUnsigned(fields["recovery_ingress_fence"], recoveryFence) ||
        recoveryFence == 0 ||
        !ParseUnsigned(fields["terminalization_generation"],
            terminalGeneration) || terminalGeneration != 1 ||
        !ParseUnsigned(fields["egress_publisher_pid"], publisherPid) ||
        publisherPid == 0 ||
        !ParseUnsigned(fields["egress_publisher_start_ticks"],
            publisherStartTicks) || publisherStartTicks == 0 ||
        !ParseUnsigned(fields["egress_policy_generation"],
            egressGeneration) || egressGeneration == 0 ||
        !ParseUnsigned(fields["known_mutation_command_count"],
            knownMutationCount) || knownMutationCount > 4096 ||
        !ParseUnsigned(fields["known_correlation_count"],
            knownCorrelationCount) || knownCorrelationCount > 4096 ||
        !ParseUnsigned(fields["settled_mutation_command_count"],
            settledMutationCount) || settledMutationCount != knownMutationCount)
        return false;

    const char* const zeroCounts[] = {
        "unknown_mutation_command_count", "unresolved_mutation_command_count",
        "unknown_active_order_count", "active_order_count", "position_count",
        "nonzero_cash_fx_count", "mutation_connector_count",
        "broker_socket_count", "broker_process_count",
        "broker_credential_count"
    };
    for (std::size_t i = 0;
         i < sizeof(zeroCounts) / sizeof(zeroCounts[0]); ++i)
        if (fields[zeroCounts[i]] != "0") return false;
    const char* const zeroDecimals[] = {
        "gross_absolute_position", "gross_fx_exposure", "gross_risk"
    };
    for (std::size_t i = 0;
         i < sizeof(zeroDecimals) / sizeof(zeroDecimals[0]); ++i)
        if (fields[zeroDecimals[i]] != "0") return false;
    const char* const trueFields[] = {
        "query_started_after_challenge", "observed_after_cutoff",
        "causal_watermark_dominates_cutoff",
        "causal_watermark_dominates_all_mutations", "account_queries_complete",
        "active_orders_complete", "completed_orders_complete",
        "executions_complete", "positions_complete", "cash_fx_complete",
        "risk_complete", "all_known_mutation_commands_settled",
        "execution_service_inactive", "paper_units_inactive",
        "execution_mutation_gate_closed", "read_only_authority",
        "paper_only", "terminal_witness_durable"
    };
    for (std::size_t i = 0;
         i < sizeof(trueFields) / sizeof(trueFields[0]); ++i)
        if (fields[trueFields[i]] != "1") return false;
    const char* const falseFields[] = {
        "broker_transport_connected", "broker_reconnect_permitted",
        "mutation_attempted", "paper_authorized", "live_authorized",
        "mutation_authorized", "direct_broker_access",
        "order_submission_authorized", "order_authorized",
        "authority_granted"
    };
    for (std::size_t i = 0;
         i < sizeof(falseFields) / sizeof(falseFields[0]); ++i)
        if (fields[falseFields[i]] != "0") return false;

    std::string accountSha256;
    if (!Sha256(fields["owner_account"], accountSha256)) return false;
    if (fields["schema"] !=
            "hepta.paper-terminal-external-halt-commit-capsule.v1" ||
        fields["version"] != "1" ||
        fields["status"] != "POST_CUTOFF_TERMINAL_WITNESS_VERIFIED" ||
        fields["terminal_proof_kind"] !=
            "POST_CUTOFF_SIGNED_ACCOUNT_WITNESS_V1" ||
        fields["provider_capability"] !=
            "ACCOUNT_WIDE_ATOMIC_OR_CAUSAL_POST_CUTOFF_READ_ONLY_V1" ||
        (fields["snapshot_consistency"] != "ATOMIC_ACCOUNT" &&
         fields["snapshot_consistency"] != "CAUSAL_WATERMARK") ||
        !IsBootId(fields["host_boot_id"]) ||
        fields["host_boot_id"] == "00000000-0000-0000-0000-000000000000" ||
        fields["host_boot_id"] != currentBootId ||
        fields["terminalizing_latch_sha256"] != terminalizingSha256 ||
        fields["finalization_id"] != terminalizing.at("finalization_id") ||
        fields["preliminary_finalization_receipt_sha256"] !=
            terminalizing.at("preliminary_finalization_receipt_sha256") ||
        fields["owner_agent_id"] != terminalizing.at("owner_agent_id") ||
        fields["owner_session_id"] != terminalizing.at("owner_session_id") ||
        fields["owner_account"] != terminalizing.at("owner_account") ||
        fields["owner_execution_domain"] !=
            terminalizing.at("owner_execution_domain") ||
        fields["recovery_ingress_fence"] !=
            terminalizing.at("recovery_ingress_fence") ||
        fields["execution_service_epoch"] !=
            terminalizing.at("terminalization_service_epoch") ||
        fields["execution_service_fencing_generation"] !=
            terminalizing.at("terminalization_service_fencing_generation") ||
        fields["terminalization_generation"] !=
            terminalizing.at("terminalization_generation") ||
        fields["account_id_sha256"] != accountSha256 ||
        !ValidateOwnerSet(fields["owner_set_canonical_hex"],
            fields["expected_owner_set_sha256"], ownerCount,
            fields["owner_account"], fields["owner_execution_domain"]))
        return false;
    return true;
}

std::string BuildExternalLatch(
    const std::map<std::string, std::string>& fields,
    const std::string& capsuleFileSha256,
    const std::string& capsuleBodySha256)
{
    std::ostringstream out;
    out.imbue(std::locale::classic());
    out << "HPW1\n"
        << "schema=hepta.paper-terminal-external-halt-latch.v1\n"
        << "version=1\n"
        << "state=TERMINAL_EXTERNAL_HALTED\n"
        << "terminal_proof_kind=" << fields.at("terminal_proof_kind") << '\n'
        << "recovery_id=" << fields.at("recovery_id") << '\n'
        << "finalization_id=" << fields.at("finalization_id") << '\n'
        << "campaign_id=" << fields.at("campaign_id") << '\n'
        << "cycle_id=" << fields.at("cycle_id") << '\n'
        << "expected_owner_set_sha256="
        << fields.at("expected_owner_set_sha256") << '\n'
        << "expected_owner_count=" << fields.at("expected_owner_count") << '\n'
        << "preliminary_finalization_receipt_sha256="
        << fields.at("preliminary_finalization_receipt_sha256") << '\n'
        << "owner_account=" << fields.at("owner_account") << '\n'
        << "owner_execution_domain="
        << fields.at("owner_execution_domain") << '\n'
        << "account_id_sha256=" << fields.at("account_id_sha256") << '\n'
        << "execution_service_epoch="
        << fields.at("execution_service_epoch") << '\n'
        << "execution_service_fencing_generation="
        << fields.at("execution_service_fencing_generation") << '\n'
        << "recovery_ingress_fence="
        << fields.at("recovery_ingress_fence") << '\n'
        << "terminalization_generation=1\n"
        << "terminalizing_latch_sha256="
        << fields.at("terminalizing_latch_sha256") << '\n'
        << "commit_capsule_file_sha256=" << capsuleFileSha256 << '\n'
        << "commit_capsule_body_sha256=" << capsuleBodySha256 << '\n'
        << "transport_cutoff_receipt_file_sha256="
        << fields.at("transport_cutoff_receipt_file_sha256") << '\n'
        << "transport_cutoff_receipt_body_sha256="
        << fields.at("transport_cutoff_receipt_body_sha256") << '\n'
        << "post_cutoff_terminal_witness_file_sha256="
        << fields.at("post_cutoff_terminal_witness_file_sha256") << '\n'
        << "post_cutoff_terminal_witness_body_sha256="
        << fields.at("post_cutoff_terminal_witness_body_sha256") << '\n'
        << "terminal_external_halt_latch_durable=1\n"
        << "paper_authorized=0\n"
        << "live_authorized=0\n"
        << "mutation_authorized=0\n"
        << "order_submission_authorized=0\n"
        << "order_authorized=0\n"
        << "paper_only=1\n"
        << "authority_granted=0\n";
    std::string body = out.str();
    std::string bodySha256;
    if (!Sha256(body, bodySha256)) return std::string();
    out << "latch_body_sha256=" << bodySha256 << '\n';
    return out.str();
}

bool ReadExistingExternalLatch(int directoryFd, uid_t expectedOwnerUid,
    gid_t expectedOwnerGid, bool& exists, std::string& contents)
{
    struct stat metadata;
    if (::fstatat(directoryFd, kExternalLatchFile, &metadata,
            AT_SYMLINK_NOFOLLOW) != 0)
    {
        if (errno == ENOENT)
        {
            exists = false;
            contents.clear();
            return true;
        }
        return false;
    }
    exists = true;
    return ReadRegularFileAt(directoryFd, kExternalLatchFile,
        expectedOwnerUid, expectedOwnerGid, 0600,
        kMaximumExternalLatchBytes, contents);
}

bool RenameNoReplace(int directoryFd, const std::string& temporary)
{
#if defined(__linux__) && defined(SYS_renameat2) && defined(RENAME_NOREPLACE)
    return ::syscall(SYS_renameat2, directoryFd, temporary.c_str(),
        directoryFd, kExternalLatchFile, RENAME_NOREPLACE) == 0;
#else
    (void)directoryFd;
    (void)temporary;
    errno = ENOSYS;
    return false;
#endif
}

bool CommitNoReplace(int directoryFd, uid_t expectedOwnerUid,
    gid_t expectedOwnerGid,
    const std::string& desired, bool& replay, std::string& reason)
{
    bool exists = false;
    std::string existing;
    if (!ReadExistingExternalLatch(directoryFd, expectedOwnerUid,
            expectedOwnerGid,
            exists, existing))
    {
        reason = "PAPER_TERMINAL_EXTERNAL_LATCH_UNSAFE";
        return false;
    }
    if (exists)
    {
        if (existing != desired)
        {
            reason = "PAPER_TERMINAL_EXTERNAL_LATCH_CONFLICT";
            return false;
        }
        if (::fsync(directoryFd) != 0)
        {
            reason = "PAPER_TERMINAL_EXTERNAL_LATCH_DURABILITY_FAILED";
            return false;
        }
        replay = true;
        return true;
    }

    std::string temporary;
    int fd = -1;
    for (unsigned int attempt = 0; attempt < 64 && fd < 0; ++attempt)
    {
        temporary = ".ib-paper-terminal-external-halt.tmp." +
            std::to_string(static_cast<unsigned long>(::getpid())) + "." +
            std::to_string(attempt);
        fd = ::openat(directoryFd, temporary.c_str(),
            O_WRONLY | O_CREAT | O_EXCL | O_CLOEXEC | O_NOFOLLOW, 0600);
        if (fd < 0 && errno != EEXIST) break;
    }
    const bool created = fd >= 0;
    bool ok = fd >= 0 && ::fchmod(fd, 0600) == 0;
    std::size_t offset = 0;
    while (ok && offset < desired.size())
    {
        const ssize_t count = ::write(fd, desired.data() + offset,
            desired.size() - offset);
        if (count < 0 && errno == EINTR) continue;
        if (count <= 0) ok = false;
        else offset += static_cast<std::size_t>(count);
    }
    struct stat written;
    ok = ok && ::fsync(fd) == 0 && ::fstat(fd, &written) == 0 &&
        S_ISREG(written.st_mode) && written.st_uid == expectedOwnerUid &&
        written.st_gid == expectedOwnerGid &&
        (written.st_mode & 07777) == 0600 && written.st_nlink == 1;
    if (fd >= 0 && ::close(fd) != 0) ok = false;
    if (!ok)
    {
        if (created) ::unlinkat(directoryFd, temporary.c_str(), 0);
        reason = "PAPER_TERMINAL_EXTERNAL_LATCH_WRITE_FAILED";
        return false;
    }

    if (!RenameNoReplace(directoryFd, temporary))
    {
        const int savedErrno = errno;
        ::unlinkat(directoryFd, temporary.c_str(), 0);
        if (savedErrno == EEXIST)
        {
            if (!ReadExistingExternalLatch(directoryFd, expectedOwnerUid,
                    expectedOwnerGid,
                    exists, existing) || !exists || existing != desired)
            {
                reason = "PAPER_TERMINAL_EXTERNAL_LATCH_CONFLICT";
                return false;
            }
            if (::fsync(directoryFd) != 0)
            {
                reason = "PAPER_TERMINAL_EXTERNAL_LATCH_DURABILITY_FAILED";
                return false;
            }
            replay = true;
            return true;
        }
        reason = "PAPER_TERMINAL_EXTERNAL_LATCH_WRITE_FAILED";
        return false;
    }
    if (::fsync(directoryFd) != 0)
    {
        reason = "PAPER_TERMINAL_EXTERNAL_LATCH_DURABILITY_FAILED";
        return false;
    }
    if (!ReadExistingExternalLatch(directoryFd, expectedOwnerUid,
            expectedOwnerGid,
            exists, existing) || !exists || existing != desired)
    {
        reason = "PAPER_TERMINAL_EXTERNAL_LATCH_VERIFY_FAILED";
        return false;
    }
    replay = false;
    return true;
}
}

namespace hepta
{

const char* PaperTerminalExternalLatchFileName()
{
    return kExternalLatchFile;
}

bool CommitPaperTerminalExternalLatch(
    const std::string& stateDirectory,
    const std::string& capsulePath,
    uid_t expectedOwnerUid,
    gid_t expectedOwnerGid,
    uid_t expectedCapsuleUid,
    gid_t expectedCapsuleGid,
    mode_t expectedCapsuleMode,
    PaperTerminalExternalLatchResult& result,
    std::string& reason)
{
    result = PaperTerminalExternalLatchResult();
    OpenDirectory state;
    struct stat stateMetadata;
    if (!OpenAbsoluteDirectoryNoSymlink(stateDirectory, state) ||
        ::fstat(state.fd, &stateMetadata) != 0 ||
        !S_ISDIR(stateMetadata.st_mode) ||
        stateMetadata.st_uid != expectedOwnerUid ||
        stateMetadata.st_gid != expectedOwnerGid ||
        (stateMetadata.st_mode & 07777) != 0700)
    {
        reason = "PAPER_TERMINAL_STATE_DIRECTORY_UNSAFE";
        return false;
    }
    LockedFile runtimeLock;
    if (!AcquireRuntimeLock(state.fd, expectedOwnerUid, runtimeLock))
    {
        reason = "PAPER_TERMINAL_STATE_LOCK_UNAVAILABLE";
        return false;
    }

    std::string terminalizingContents;
    if (!ReadRegularFileAt(state.fd, kTerminalizingLatchFile,
            expectedOwnerUid, expectedOwnerGid, 0600,
            kMaximumTerminalizingLatchBytes, terminalizingContents))
    {
        reason = "PAPER_TERMINAL_TERMINALIZING_LATCH_UNSAFE";
        return false;
    }
    std::map<std::string, std::string> terminalizing;
    std::string terminalizingSha256;
    if (!ParseTerminalizingLatch(terminalizingContents, terminalizing) ||
        !Sha256(terminalizingContents, terminalizingSha256))
    {
        reason = "PAPER_TERMINAL_TERMINALIZING_LATCH_INVALID";
        return false;
    }

    std::string capsuleContents;
    std::string capsuleFileSha256;
    if (expectedCapsuleMode != 0440 ||
        !ReadAbsoluteCapsule(capsulePath, expectedCapsuleUid,
            expectedCapsuleGid, expectedCapsuleMode,
            capsuleContents) || !Sha256(capsuleContents, capsuleFileSha256))
    {
        reason = "PAPER_TERMINAL_COMMIT_CAPSULE_UNSAFE";
        return false;
    }
    std::map<std::string, std::string> capsule;
    std::string capsuleBodySha256;
    std::string currentBootId;
    if (!ReadCurrentBootId(currentBootId))
    {
        reason = "PAPER_TERMINAL_CURRENT_BOOT_ID_UNAVAILABLE";
        return false;
    }
    if (!ValidateCapsule(capsuleContents, terminalizing,
            terminalizingSha256, currentBootId,
            capsule, capsuleBodySha256))
    {
        reason = "PAPER_TERMINAL_COMMIT_CAPSULE_INVALID";
        return false;
    }

    const std::string desired = BuildExternalLatch(
        capsule, capsuleFileSha256, capsuleBodySha256);
    if (desired.empty() || desired.size() > kMaximumExternalLatchBytes)
    {
        reason = "PAPER_TERMINAL_EXTERNAL_LATCH_INVALID";
        return false;
    }

    // Reopen the original HPT1 under the runtime lock immediately before the
    // no-replace commit. A changed or removed terminalization intent can never
    // be paired with the already-validated capsule.
    std::string currentTerminalizing;
    if (!ReadRegularFileAt(state.fd, kTerminalizingLatchFile,
            expectedOwnerUid, expectedOwnerGid, 0600,
            kMaximumTerminalizingLatchBytes, currentTerminalizing) ||
        currentTerminalizing != terminalizingContents)
    {
        reason = "PAPER_TERMINAL_TERMINALIZING_LATCH_CHANGED";
        return false;
    }

    bool replay = false;
    if (!CommitNoReplace(state.fd, expectedOwnerUid, expectedOwnerGid,
            desired, replay, reason))
        return false;
    currentTerminalizing.clear();
    if (!ReadRegularFileAt(state.fd, kTerminalizingLatchFile,
            expectedOwnerUid, expectedOwnerGid, 0600,
            kMaximumTerminalizingLatchBytes, currentTerminalizing) ||
        currentTerminalizing != terminalizingContents)
    {
        reason = "PAPER_TERMINAL_TERMINALIZING_LATCH_CHANGED";
        return false;
    }
    std::string latchSha256;
    if (!Sha256(desired, latchSha256))
    {
        reason = "PAPER_TERMINAL_EXTERNAL_LATCH_HASH_FAILED";
        return false;
    }
    result.latchContents = desired;
    result.latchSha256 = latchSha256;
    result.terminalizingLatchSha256 = terminalizingSha256;
    result.capsuleFileSha256 = capsuleFileSha256;
    result.capsuleBodySha256 = capsuleBodySha256;
    result.recoveryId = capsule["recovery_id"];
    result.finalizationId = capsule["finalization_id"];
    result.replay = replay;
    reason.clear();
    return true;
}

}
