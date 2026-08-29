#include "paper_terminal_mutation_manifest.h"
#include "execution_coordinator.h"

#include <algorithm>
#include <cerrno>
#include <chrono>
#include <fcntl.h>
#include <iomanip>
#include <limits>
#include <map>
#include <openssl/evp.h>
#include <set>
#include <sstream>
#include <sys/stat.h>
#include <unistd.h>

namespace
{
const char* const kManifestFile =
    "ib-paper-terminal-mutation-manifest.v1";
const std::size_t kMaximumCommands = 4096;
const std::size_t kMaximumCorrelations = 4096;
const std::size_t kMaximumManifestBytes = 1024 * 1024;
// Keep this literal synchronized with the runtime's internal latch filename.
// It is local here deliberately: the manifest/core archive must not acquire
// a link-time dependency on the PAPER runtime composition object.
const char* const kTerminalLatchFileName =
    "ib-paper-terminal-halt.v1";

std::uint64_t TerminalNowEpochMs()
{
    return static_cast<std::uint64_t>(
        std::chrono::duration_cast<std::chrono::milliseconds>(
            std::chrono::system_clock::now().time_since_epoch()).count());
}

bool Text(const std::string& value, std::size_t maximum)
{
    if (value.empty() || value.size() > maximum) return false;
    for (std::size_t i = 0; i < value.size(); ++i)
    {
        const unsigned char byte = static_cast<unsigned char>(value[i]);
        if (byte < 0x21 || byte > 0x7e) return false;
    }
    return true;
}

bool Sha256Value(const std::string& value)
{
    if (value.size() != 71 || value.compare(0, 7, "sha256:") != 0)
        return false;
    for (std::size_t i = 7; i < value.size(); ++i)
        if (!((value[i] >= '0' && value[i] <= '9') ||
              (value[i] >= 'a' && value[i] <= 'f')))
            return false;
    return true;
}

std::string Sha256(const std::string& value)
{
    unsigned char digest[EVP_MAX_MD_SIZE];
    unsigned int length = 0;
    EVP_MD_CTX* context = EVP_MD_CTX_new();
    if (context == nullptr) return std::string();
    const bool ok = EVP_DigestInit_ex(context, EVP_sha256(), nullptr) == 1 &&
        EVP_DigestUpdate(context, value.data(), value.size()) == 1 &&
        EVP_DigestFinal_ex(context, digest, &length) == 1;
    EVP_MD_CTX_free(context);
    if (!ok || length != 32) return std::string();
    std::ostringstream output;
    output << "sha256:" << std::hex << std::setfill('0');
    for (unsigned int i = 0; i < length; ++i)
        output << std::setw(2) << static_cast<unsigned int>(digest[i]);
    return output.str();
}

std::string Hex(const std::string& value)
{
    static const char digits[] = "0123456789abcdef";
    std::string result;
    result.reserve(value.size() * 2);
    for (std::size_t i = 0; i < value.size(); ++i)
    {
        const unsigned char byte = static_cast<unsigned char>(value[i]);
        result.push_back(digits[byte >> 4]);
        result.push_back(digits[byte & 15]);
    }
    return result;
}

bool DecodeHex(const std::string& encoded, std::string& value)
{
    if (encoded.empty() || encoded.size() % 2 != 0) return false;
    value.clear();
    value.reserve(encoded.size() / 2);
    for (std::size_t i = 0; i < encoded.size(); i += 2)
    {
        unsigned int byte = 0;
        for (int digit = 0; digit < 2; ++digit)
        {
            const char character = encoded[i + digit];
            unsigned int nibble = 0;
            if (character >= '0' && character <= '9')
                nibble = static_cast<unsigned int>(character - '0');
            else if (character >= 'a' && character <= 'f')
                nibble = 10U + static_cast<unsigned int>(character - 'a');
            else return false;
            byte = byte * 16U + nibble;
        }
        value.push_back(static_cast<char>(byte));
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

std::string CommandLine(const PaperTerminalMutationRecord& record)
{
    return std::string("command=") + Hex(record.agentId) + "|" +
        Hex(record.sessionId) + "|" + Hex(record.toolCallId) + "|" +
        record.operation + "|" + Hex(record.venueCorrelationId) + "\n";
}

std::string CorrelationLine(const std::string& correlation)
{
    return std::string("correlation=") + Hex(correlation) + "\n";
}

bool ReadPrivateFileAt(int directoryFd, const char* name,
    std::string& contents)
{
    const int fd = ::openat(directoryFd, name,
        O_RDONLY | O_CLOEXEC | O_NOFOLLOW);
    if (fd < 0) return false;
    struct stat metadata;
    bool ok = ::fstat(fd, &metadata) == 0 &&
        S_ISREG(metadata.st_mode) && metadata.st_uid == ::geteuid() &&
        (metadata.st_mode & 07777) == 0600 && metadata.st_nlink == 1 &&
        metadata.st_size > 0 &&
        metadata.st_size <= static_cast<off_t>(kMaximumManifestBytes);
    if (ok)
    {
        contents.assign(static_cast<std::size_t>(metadata.st_size), '\0');
        std::size_t offset = 0;
        while (ok && offset < contents.size())
        {
            const ssize_t count = ::read(
                fd, &contents[offset], contents.size() - offset);
            if (count < 0 && errno == EINTR) continue;
            if (count <= 0) ok = false;
            else offset += static_cast<std::size_t>(count);
        }
    }
    if (::close(fd) != 0) ok = false;
    return ok;
}

bool OpenStateDirectory(const std::string& path, int& directoryFd)
{
    directoryFd = ::open(path.c_str(),
        O_RDONLY | O_DIRECTORY | O_CLOEXEC | O_NOFOLLOW);
    struct stat metadata;
    if (directoryFd < 0 || ::fstat(directoryFd, &metadata) != 0 ||
        !S_ISDIR(metadata.st_mode) || metadata.st_uid != ::geteuid() ||
        (metadata.st_mode & 0777) != 0700)
    {
        if (directoryFd >= 0) ::close(directoryFd);
        directoryFd = -1;
        return false;
    }
    return true;
}

bool CommitNoReplace(int directoryFd, const std::string& contents,
    bool& replay, std::string& reason)
{
    replay = false;
    std::string existing;
    struct stat metadata;
    if (::fstatat(directoryFd, kManifestFile, &metadata,
            AT_SYMLINK_NOFOLLOW) == 0)
    {
        if (!ReadPrivateFileAt(directoryFd, kManifestFile, existing) ||
            existing != contents)
        {
            reason = "IB_PAPER_TERMINAL_MANIFEST_CONFLICT";
            return false;
        }
        replay = true;
        return true;
    }
    if (errno != ENOENT)
    {
        reason = "IB_PAPER_TERMINAL_MANIFEST_UNSAFE";
        return false;
    }
    std::string temporary;
    int fd = -1;
    for (int attempt = 0; attempt < 16 && fd < 0; ++attempt)
    {
        temporary = std::string(".paper-terminal-manifest.tmp.") +
            std::to_string(static_cast<unsigned long>(::getpid())) + "." +
            std::to_string(attempt);
        fd = ::openat(directoryFd, temporary.c_str(),
            O_WRONLY | O_CREAT | O_EXCL | O_CLOEXEC | O_NOFOLLOW, 0600);
        if (fd < 0 && errno != EEXIST) break;
    }
    bool ok = fd >= 0 && ::fchmod(fd, 0600) == 0;
    std::size_t offset = 0;
    while (ok && offset < contents.size())
    {
        const ssize_t count = ::write(
            fd, contents.data() + offset, contents.size() - offset);
        if (count < 0 && errno == EINTR) continue;
        if (count <= 0) ok = false;
        else offset += static_cast<std::size_t>(count);
    }
    struct stat written;
    ok = ok && ::fsync(fd) == 0 && ::fstat(fd, &written) == 0 &&
        S_ISREG(written.st_mode) && written.st_uid == ::geteuid() &&
        (written.st_mode & 07777) == 0600 && written.st_nlink == 1;
    if (fd >= 0 && ::close(fd) != 0) ok = false;
    bool linked = false;
    if (ok)
    {
        linked = ::linkat(directoryFd, temporary.c_str(), directoryFd,
            kManifestFile, 0) == 0;
        if (!linked && errno == EEXIST)
        {
            ok = ReadPrivateFileAt(directoryFd, kManifestFile, existing) &&
                existing == contents;
            replay = ok;
        }
        else ok = linked;
    }
    if (!temporary.empty())
        ::unlinkat(directoryFd, temporary.c_str(), 0);
    ok = ok && ::fsync(directoryFd) == 0;
    if (!ok)
    {
        reason = linked ? "IB_PAPER_TERMINAL_MANIFEST_DURABILITY_FAILED" :
            "IB_PAPER_TERMINAL_MANIFEST_CONFLICT";
        return false;
    }
    return true;
}

bool Split(const std::string& value, char separator,
    std::vector<std::string>& fields)
{
    fields.clear();
    std::size_t offset = 0;
    for (;;)
    {
        const std::size_t found = value.find(separator, offset);
        fields.push_back(value.substr(offset,
            found == std::string::npos ? std::string::npos : found - offset));
        if (found == std::string::npos) return true;
        offset = found + 1;
    }
}

bool ParseManifest(const std::string& contents,
    const PaperTerminalFenceBinding& expected,
    PaperTerminalMutationManifest& manifest, std::string& reason)
{
    if (contents.size() > kMaximumManifestBytes ||
        contents.compare(0, 5, "HPM1\n") != 0)
    {
        reason = "IB_PAPER_TERMINAL_MANIFEST_INVALID";
        return false;
    }
    std::istringstream input(contents.substr(5));
    std::map<std::string, std::string> scalars;
    std::vector<PaperTerminalMutationRecord> records;
    std::vector<std::string> correlations;
    std::string line;
    while (std::getline(input, line))
    {
        if (line.empty()) continue;
        const std::size_t separator = line.find('=');
        if (separator == std::string::npos || separator == 0 ||
            separator + 1 >= line.size())
        {
            reason = "IB_PAPER_TERMINAL_MANIFEST_INVALID";
            return false;
        }
        const std::string name = line.substr(0, separator);
        const std::string value = line.substr(separator + 1);
        if (name == "command")
        {
            std::vector<std::string> fields;
            Split(value, '|', fields);
            PaperTerminalMutationRecord record;
            if (fields.size() != 5 ||
                !DecodeHex(fields[0], record.agentId) ||
                !DecodeHex(fields[1], record.sessionId) ||
                !DecodeHex(fields[2], record.toolCallId) ||
                (!fields[4].empty() &&
                 !DecodeHex(fields[4], record.venueCorrelationId)))
            {
                reason = "IB_PAPER_TERMINAL_MANIFEST_INVALID";
                return false;
            }
            record.operation = fields[3];
            records.push_back(record);
        }
        else if (name == "correlation")
        {
            std::string decoded;
            if (!DecodeHex(value, decoded))
            {
                reason = "IB_PAPER_TERMINAL_MANIFEST_INVALID";
                return false;
            }
            correlations.push_back(decoded);
        }
        else if (!scalars.insert(std::make_pair(name, value)).second)
        {
            reason = "IB_PAPER_TERMINAL_MANIFEST_INVALID";
            return false;
        }
    }
    static const char* const names[] = {
        "schema", "version", "finalization_id",
        "preliminary_finalization_receipt_sha256", "owner_agent_id_hex",
        "owner_session_id_hex", "owner_account_hex",
        "owner_execution_domain_hex", "recovery_ingress_fence",
        "terminalization_service_epoch_hex",
        "terminalization_service_fencing_generation", "service_process_id",
        "service_process_start_ticks", "broker_connection_epoch",
        "broker_socket_identity_sha256", "command_count",
        "correlation_count", "known_mutation_command_set_sha256",
        "known_correlation_set_sha256"
    };
    if (scalars.size() != sizeof(names) / sizeof(names[0]))
    {
        reason = "IB_PAPER_TERMINAL_MANIFEST_INVALID";
        return false;
    }
    for (std::size_t i = 0; i < sizeof(names) / sizeof(names[0]); ++i)
        if (scalars.find(names[i]) == scalars.end())
        {
            reason = "IB_PAPER_TERMINAL_MANIFEST_INVALID";
            return false;
        }
    PaperTerminalFenceBinding observed;
    std::string decoded;
    observed.finalizationId = scalars["finalization_id"];
    observed.preliminaryReceiptSha256 =
        scalars["preliminary_finalization_receipt_sha256"];
    if (!DecodeHex(scalars["owner_agent_id_hex"],
            observed.owner.agentId) ||
        !DecodeHex(scalars["owner_session_id_hex"],
            observed.owner.sessionId) ||
        !DecodeHex(scalars["owner_account_hex"],
            observed.owner.account) ||
        !DecodeHex(scalars["owner_execution_domain_hex"],
            observed.owner.executionDomain) ||
        !DecodeHex(scalars["terminalization_service_epoch_hex"],
            observed.serviceEpoch) ||
        !ParseUnsigned(scalars["recovery_ingress_fence"],
            observed.recoveryIngressFence) ||
        !ParseUnsigned(scalars["terminalization_service_fencing_generation"],
            observed.serviceFencingGeneration) ||
        !ParseUnsigned(scalars["service_process_id"],
            observed.serviceProcessId) ||
        !ParseUnsigned(scalars["service_process_start_ticks"],
            observed.serviceProcessStartTicks) ||
        !ParseUnsigned(scalars["broker_connection_epoch"],
            observed.brokerConnectionEpoch))
    {
        reason = "IB_PAPER_TERMINAL_MANIFEST_INVALID";
        return false;
    }
    observed.brokerSocketIdentitySha256 =
        scalars["broker_socket_identity_sha256"];
    std::uint64_t commandCount = 0;
    std::uint64_t correlationCount = 0;
    PaperTerminalMutationUniverse rebuilt;
    if (scalars["schema"] !=
            "hepta.ib-paper-terminal-mutation-manifest.v1" ||
        scalars["version"] != "1" ||
        !SamePaperTerminalFenceBinding(observed, expected) ||
        !ParseUnsigned(scalars["command_count"], commandCount) ||
        !ParseUnsigned(scalars["correlation_count"], correlationCount) ||
        commandCount != records.size() ||
        correlationCount != correlations.size() ||
        !BuildPaperTerminalMutationUniverse(records, rebuilt, reason) ||
        rebuilt.correlations != correlations ||
        rebuilt.commandSetSha256 !=
            scalars["known_mutation_command_set_sha256"] ||
        rebuilt.correlationSetSha256 !=
            scalars["known_correlation_set_sha256"])
    {
        if (reason.empty()) reason = "IB_PAPER_TERMINAL_MANIFEST_INVALID";
        return false;
    }
    manifest = PaperTerminalMutationManifest();
    manifest.contents = contents;
    manifest.fileSha256 = Sha256(contents);
    manifest.bodySha256 = Sha256(contents.substr(5));
    manifest.universe = rebuilt;
    reason.clear();
    return !manifest.fileSha256.empty() && !manifest.bodySha256.empty();
}
}

bool ValidPaperTerminalFenceBinding(
    const PaperTerminalFenceBinding& binding, std::string& reason)
{
    if (!Text(binding.owner.agentId, 128) ||
        !Text(binding.owner.sessionId, 128) ||
        !Text(binding.owner.account, 128) ||
        !Text(binding.owner.executionDomain, 128) ||
        !Text(binding.finalizationId, 128) ||
        !Sha256Value(binding.preliminaryReceiptSha256) ||
        binding.recoveryIngressFence == 0 ||
        !Text(binding.serviceEpoch, 128) ||
        binding.serviceFencingGeneration == 0 ||
        binding.serviceProcessId == 0 ||
        binding.serviceProcessStartTicks == 0 ||
        binding.brokerConnectionEpoch == 0 ||
        !Sha256Value(binding.brokerSocketIdentitySha256))
    {
        reason = "IB_PAPER_TERMINAL_FENCE_BINDING_INVALID";
        return false;
    }
    reason.clear();
    return true;
}

bool SamePaperTerminalFenceBinding(
    const PaperTerminalFenceBinding& left,
    const PaperTerminalFenceBinding& right)
{
    return left.owner.agentId == right.owner.agentId &&
        left.owner.sessionId == right.owner.sessionId &&
        left.owner.account == right.owner.account &&
        left.owner.executionDomain == right.owner.executionDomain &&
        left.finalizationId == right.finalizationId &&
        left.preliminaryReceiptSha256 == right.preliminaryReceiptSha256 &&
        left.recoveryIngressFence == right.recoveryIngressFence &&
        left.serviceEpoch == right.serviceEpoch &&
        left.serviceFencingGeneration == right.serviceFencingGeneration &&
        left.serviceProcessId == right.serviceProcessId &&
        left.serviceProcessStartTicks == right.serviceProcessStartTicks &&
        left.brokerConnectionEpoch == right.brokerConnectionEpoch &&
        left.brokerSocketIdentitySha256 == right.brokerSocketIdentitySha256;
}

std::string EncodePaperTerminalFenceBinding(
    const PaperTerminalFenceBinding& binding)
{
    std::string reason;
    if (!ValidPaperTerminalFenceBinding(binding, reason)) return std::string();
    std::ostringstream output;
    output << "HPTF2\n"
        << "owner_agent_id_hex=" << Hex(binding.owner.agentId) << '\n'
        << "owner_session_id_hex=" << Hex(binding.owner.sessionId) << '\n'
        << "owner_account_hex=" << Hex(binding.owner.account) << '\n'
        << "owner_execution_domain_hex="
        << Hex(binding.owner.executionDomain) << '\n'
        << "finalization_id=" << binding.finalizationId << '\n'
        << "preliminary_finalization_receipt_sha256="
        << binding.preliminaryReceiptSha256 << '\n'
        << "recovery_ingress_fence=" << binding.recoveryIngressFence << '\n'
        << "terminalization_service_epoch_hex="
        << Hex(binding.serviceEpoch) << '\n'
        << "terminalization_service_fencing_generation="
        << binding.serviceFencingGeneration << '\n'
        << "service_process_id=" << binding.serviceProcessId << '\n'
        << "service_process_start_ticks="
        << binding.serviceProcessStartTicks << '\n'
        << "broker_connection_epoch=" << binding.brokerConnectionEpoch << '\n'
        << "broker_socket_identity_sha256="
        << binding.brokerSocketIdentitySha256 << '\n';
    return output.str();
}

bool DecodePaperTerminalFenceBinding(
    const std::string& encoded, PaperTerminalFenceBinding& binding,
    std::string& reason)
{
    std::istringstream input(encoded);
    std::string line;
    if (!std::getline(input, line) || line != "HPTF2")
    {
        reason = "IB_PAPER_TERMINAL_FENCE_V2_REQUIRED";
        return false;
    }
    std::map<std::string, std::string> fields;
    while (std::getline(input, line))
    {
        if (line.empty()) continue;
        const std::size_t separator = line.find('=');
        if (separator == std::string::npos || separator == 0 ||
            separator + 1 >= line.size() ||
            !fields.insert(std::make_pair(line.substr(0, separator),
                line.substr(separator + 1))).second)
        {
            reason = "IB_PAPER_TERMINAL_FENCE_BINDING_INVALID";
            return false;
        }
    }
    static const char* const names[] = {
        "owner_agent_id_hex", "owner_session_id_hex", "owner_account_hex",
        "owner_execution_domain_hex", "finalization_id",
        "preliminary_finalization_receipt_sha256", "recovery_ingress_fence",
        "terminalization_service_epoch_hex",
        "terminalization_service_fencing_generation", "service_process_id",
        "service_process_start_ticks", "broker_connection_epoch",
        "broker_socket_identity_sha256"
    };
    if (fields.size() != sizeof(names) / sizeof(names[0]))
    {
        reason = "IB_PAPER_TERMINAL_FENCE_BINDING_INVALID";
        return false;
    }
    for (std::size_t i = 0; i < sizeof(names) / sizeof(names[0]); ++i)
        if (fields.find(names[i]) == fields.end())
        {
            reason = "IB_PAPER_TERMINAL_FENCE_BINDING_INVALID";
            return false;
        }
    binding = PaperTerminalFenceBinding();
    if (!DecodeHex(fields["owner_agent_id_hex"], binding.owner.agentId) ||
        !DecodeHex(fields["owner_session_id_hex"],
            binding.owner.sessionId) ||
        !DecodeHex(fields["owner_account_hex"], binding.owner.account) ||
        !DecodeHex(fields["owner_execution_domain_hex"],
            binding.owner.executionDomain) ||
        !DecodeHex(fields["terminalization_service_epoch_hex"],
            binding.serviceEpoch) ||
        !ParseUnsigned(fields["recovery_ingress_fence"],
            binding.recoveryIngressFence) ||
        !ParseUnsigned(fields["terminalization_service_fencing_generation"],
            binding.serviceFencingGeneration) ||
        !ParseUnsigned(fields["service_process_id"],
            binding.serviceProcessId) ||
        !ParseUnsigned(fields["service_process_start_ticks"],
            binding.serviceProcessStartTicks) ||
        !ParseUnsigned(fields["broker_connection_epoch"],
            binding.brokerConnectionEpoch))
    {
        reason = "IB_PAPER_TERMINAL_FENCE_BINDING_INVALID";
        return false;
    }
    binding.finalizationId = fields["finalization_id"];
    binding.preliminaryReceiptSha256 =
        fields["preliminary_finalization_receipt_sha256"];
    binding.brokerSocketIdentitySha256 =
        fields["broker_socket_identity_sha256"];
    return ValidPaperTerminalFenceBinding(binding, reason);
}

bool BuildPaperTerminalMutationUniverse(
    const std::vector<PaperTerminalMutationRecord>& records,
    PaperTerminalMutationUniverse& universe, std::string& reason)
{
    universe = PaperTerminalMutationUniverse();
    if (records.size() > kMaximumCommands)
    {
        reason = "IB_PAPER_TERMINAL_MUTATION_UNIVERSE_TOO_LARGE";
        return false;
    }
    universe.commands = records;
    for (std::size_t i = 0; i < universe.commands.size(); ++i)
    {
        const PaperTerminalMutationRecord& record = universe.commands[i];
        if (!Text(record.agentId, 128) || !Text(record.sessionId, 128) ||
            !Text(record.toolCallId, 128) ||
            (record.operation != "place" && record.operation != "cancel" &&
             record.operation != "flatten") ||
            (!record.venueCorrelationId.empty() &&
             !Text(record.venueCorrelationId, 192)))
        {
            reason = "IB_PAPER_TERMINAL_MUTATION_RECORD_INVALID";
            return false;
        }
    }
    std::sort(universe.commands.begin(), universe.commands.end(),
        [](const PaperTerminalMutationRecord& left,
           const PaperTerminalMutationRecord& right) {
            return CommandLine(left) < CommandLine(right);
        });
    std::string commandCanonical;
    std::set<std::string> correlations;
    std::string previous;
    for (std::size_t i = 0; i < universe.commands.size(); ++i)
    {
        const std::string line = CommandLine(universe.commands[i]);
        if (!previous.empty() && line == previous)
        {
            reason = "IB_PAPER_TERMINAL_MUTATION_RECORD_DUPLICATE";
            return false;
        }
        previous = line;
        commandCanonical.append(line);
        if (!universe.commands[i].venueCorrelationId.empty())
            correlations.insert(universe.commands[i].venueCorrelationId);
    }
    if (correlations.size() > kMaximumCorrelations)
    {
        reason = "IB_PAPER_TERMINAL_CORRELATION_UNIVERSE_TOO_LARGE";
        return false;
    }
    universe.correlations.assign(correlations.begin(), correlations.end());
    std::string correlationCanonical;
    for (std::size_t i = 0; i < universe.correlations.size(); ++i)
        correlationCanonical.append(CorrelationLine(universe.correlations[i]));
    universe.commandSetSha256 = Sha256(commandCanonical);
    universe.correlationSetSha256 = Sha256(correlationCanonical);
    if (universe.commandSetSha256.empty() ||
        universe.correlationSetSha256.empty())
    {
        reason = "IB_PAPER_TERMINAL_MUTATION_UNIVERSE_HASH_FAILED";
        return false;
    }
    reason.clear();
    return true;
}

bool BuildPaperTerminalMutationManifest(
    const PaperTerminalFenceBinding& binding,
    const PaperTerminalMutationUniverse& universe,
    PaperTerminalMutationManifest& manifest, std::string& reason)
{
    PaperTerminalMutationUniverse verified;
    if (!ValidPaperTerminalFenceBinding(binding, reason) ||
        !BuildPaperTerminalMutationUniverse(
            universe.commands, verified, reason) ||
        verified.commandSetSha256 != universe.commandSetSha256 ||
        verified.correlationSetSha256 != universe.correlationSetSha256 ||
        verified.correlations != universe.correlations)
    {
        if (reason.empty()) reason = "IB_PAPER_TERMINAL_UNIVERSE_MISMATCH";
        return false;
    }
    std::ostringstream body;
    body << "schema=hepta.ib-paper-terminal-mutation-manifest.v1\n"
        << "version=1\n"
        << "finalization_id=" << binding.finalizationId << '\n'
        << "preliminary_finalization_receipt_sha256="
        << binding.preliminaryReceiptSha256 << '\n'
        << "owner_agent_id_hex=" << Hex(binding.owner.agentId) << '\n'
        << "owner_session_id_hex=" << Hex(binding.owner.sessionId) << '\n'
        << "owner_account_hex=" << Hex(binding.owner.account) << '\n'
        << "owner_execution_domain_hex="
        << Hex(binding.owner.executionDomain) << '\n'
        << "recovery_ingress_fence=" << binding.recoveryIngressFence << '\n'
        << "terminalization_service_epoch_hex="
        << Hex(binding.serviceEpoch) << '\n'
        << "terminalization_service_fencing_generation="
        << binding.serviceFencingGeneration << '\n'
        << "service_process_id=" << binding.serviceProcessId << '\n'
        << "service_process_start_ticks="
        << binding.serviceProcessStartTicks << '\n'
        << "broker_connection_epoch=" << binding.brokerConnectionEpoch << '\n'
        << "broker_socket_identity_sha256="
        << binding.brokerSocketIdentitySha256 << '\n'
        << "command_count=" << verified.commands.size() << '\n'
        << "correlation_count=" << verified.correlations.size() << '\n'
        << "known_mutation_command_set_sha256="
        << verified.commandSetSha256 << '\n'
        << "known_correlation_set_sha256="
        << verified.correlationSetSha256 << '\n';
    for (std::size_t i = 0; i < verified.commands.size(); ++i)
        body << CommandLine(verified.commands[i]);
    for (std::size_t i = 0; i < verified.correlations.size(); ++i)
        body << CorrelationLine(verified.correlations[i]);
    manifest = PaperTerminalMutationManifest();
    manifest.contents = std::string("HPM1\n") + body.str();
    if (manifest.contents.size() > kMaximumManifestBytes)
    {
        reason = "IB_PAPER_TERMINAL_MANIFEST_TOO_LARGE";
        return false;
    }
    manifest.fileSha256 = Sha256(manifest.contents);
    manifest.bodySha256 = Sha256(body.str());
    manifest.universe = verified;
    if (manifest.fileSha256.empty() || manifest.bodySha256.empty())
    {
        reason = "IB_PAPER_TERMINAL_MANIFEST_HASH_FAILED";
        return false;
    }
    reason.clear();
    return true;
}

bool CommitPaperTerminalMutationManifest(
    const std::string& stateDirectory,
    const PaperTerminalMutationManifest& desired,
    PaperTerminalMutationManifest& committed, std::string& reason)
{
    int directoryFd = -1;
    if (!OpenStateDirectory(stateDirectory, directoryFd))
    {
        reason = "IB_PAPER_TERMINAL_MANIFEST_DIRECTORY_UNSAFE";
        return false;
    }
    bool replay = false;
    const bool ok = !desired.contents.empty() &&
        desired.contents.size() <= kMaximumManifestBytes &&
        Sha256(desired.contents) == desired.fileSha256 &&
        CommitNoReplace(directoryFd, desired.contents, replay, reason);
    if (::close(directoryFd) != 0 && ok)
    {
        reason = "IB_PAPER_TERMINAL_MANIFEST_DURABILITY_FAILED";
        return false;
    }
    if (!ok)
    {
        if (reason.empty()) reason = "IB_PAPER_TERMINAL_MANIFEST_INVALID";
        return false;
    }
    committed = desired;
    committed.replay = replay;
    reason.clear();
    return true;
}

bool LoadPaperTerminalMutationManifest(
    const std::string& stateDirectory,
    const PaperTerminalFenceBinding& expectedBinding,
    PaperTerminalMutationManifest& manifest, std::string& reason)
{
    int directoryFd = -1;
    if (!OpenStateDirectory(stateDirectory, directoryFd))
    {
        reason = "IB_PAPER_TERMINAL_MANIFEST_DIRECTORY_UNSAFE";
        return false;
    }
    std::string contents;
    const bool read = ReadPrivateFileAt(directoryFd, kManifestFile, contents);
    const bool closed = ::close(directoryFd) == 0;
    if (!read || !closed)
    {
        reason = "IB_PAPER_TERMINAL_MANIFEST_UNSAFE";
        return false;
    }
    return ParseManifest(contents, expectedBinding, manifest, reason);
}

const char* PaperTerminalMutationManifestFileName()
{
    return kManifestFile;
}

bool WriteTerminalLatchAtomic(
    const std::string& directoryPath,
    const std::string& contents,
    const std::string* expectedExistingContents,
    std::string& reason)
{
    const int directoryFd = ::open(directoryPath.c_str(),
        O_RDONLY | O_DIRECTORY | O_CLOEXEC | O_NOFOLLOW);
    struct stat directoryMetadata;
    if (directoryFd < 0 || ::fstat(directoryFd, &directoryMetadata) != 0 ||
        !S_ISDIR(directoryMetadata.st_mode) ||
        directoryMetadata.st_uid != ::geteuid() ||
        (directoryMetadata.st_mode & 0777) != 0700)
    {
        if (directoryFd >= 0) ::close(directoryFd);
        reason = "IB_PAPER_TERMINAL_LATCH_UNSAFE";
        return false;
    }
    struct stat existing;
    if (::fstatat(directoryFd, kTerminalLatchFileName, &existing,
                  AT_SYMLINK_NOFOLLOW) == 0)
    {
        if (!S_ISREG(existing.st_mode) ||
            existing.st_uid != ::geteuid() ||
            (existing.st_mode & 07777) != 0600 || existing.st_nlink != 1)
        {
            ::close(directoryFd);
            reason = "IB_PAPER_TERMINAL_LATCH_UNSAFE";
            return false;
        }
        if (expectedExistingContents == nullptr ||
            existing.st_size != static_cast<off_t>(
                expectedExistingContents->size()))
        {
            ::close(directoryFd);
            reason = "IB_PAPER_TERMINAL_LATCH_BINDING_MISMATCH";
            return false;
        }
        const int existingFd = ::openat(directoryFd,
            kTerminalLatchFileName,
            O_RDONLY | O_CLOEXEC | O_NOFOLLOW);
        std::string observed(expectedExistingContents->size(), '\0');
        std::size_t observedOffset = 0;
        bool observedOk = existingFd >= 0;
        while (observedOk && observedOffset < observed.size())
        {
            const ssize_t count = ::read(existingFd,
                &observed[observedOffset],
                observed.size() - observedOffset);
            if (count < 0 && errno == EINTR) continue;
            if (count <= 0) observedOk = false;
            else observedOffset += static_cast<std::size_t>(count);
        }
        if (existingFd >= 0 && ::close(existingFd) != 0)
            observedOk = false;
        if (!observedOk || observed != *expectedExistingContents)
        {
            ::close(directoryFd);
            reason = "IB_PAPER_TERMINAL_LATCH_BINDING_MISMATCH";
            return false;
        }
    }
    else if (errno != ENOENT || expectedExistingContents != nullptr)
    {
        ::close(directoryFd);
        reason = errno == ENOENT ?
            "IB_PAPER_TERMINAL_LATCH_BINDING_MISMATCH" :
            "IB_PAPER_TERMINAL_LATCH_UNSAFE";
        return false;
    }
    std::string temporary;
    int fd = -1;
    for (int attempt = 0; attempt < 16 && fd < 0; ++attempt)
    {
        temporary = std::string(".ib-paper-terminal-halt.tmp.") +
            std::to_string(static_cast<unsigned long>(::getpid())) + "." +
            std::to_string(TerminalNowEpochMs()) + "." +
            std::to_string(attempt);
        fd = ::openat(directoryFd, temporary.c_str(),
            O_WRONLY | O_CREAT | O_EXCL | O_CLOEXEC | O_NOFOLLOW, 0600);
        if (fd < 0 && errno != EEXIST) break;
    }
    bool ok = fd >= 0 && ::fchmod(fd, 0600) == 0;
    std::size_t offset = 0;
    while (ok && offset < contents.size())
    {
        const ssize_t count = ::write(
            fd, contents.data() + offset, contents.size() - offset);
        if (count < 0 && errno == EINTR) continue;
        if (count <= 0) ok = false;
        else offset += static_cast<std::size_t>(count);
    }
    struct stat written;
    ok = ok && ::fsync(fd) == 0 && ::fstat(fd, &written) == 0 &&
        S_ISREG(written.st_mode) && written.st_uid == ::geteuid() &&
        (written.st_mode & 07777) == 0600 && written.st_nlink == 1;
    if (fd >= 0 && ::close(fd) != 0) ok = false;
    bool installed = false;
    if (ok)
    {
        if (expectedExistingContents == nullptr)
        {
            installed = ::linkat(directoryFd, temporary.c_str(), directoryFd,
                kTerminalLatchFileName, 0) == 0;
            ok = installed;
        }
        else
        {
            installed = ::renameat(directoryFd, temporary.c_str(),
                directoryFd, kTerminalLatchFileName) == 0;
            ok = installed;
            if (installed) temporary.clear();
        }
        ok = ok && ::fsync(directoryFd) == 0;
    }
    if (!temporary.empty())
        ::unlinkat(directoryFd, temporary.c_str(), 0);
    if (::close(directoryFd) != 0) ok = false;
    if (!ok)
    {
        reason = "IB_PAPER_TERMINAL_LATCH_WRITE_FAILED";
        return false;
    }
    reason.clear();
    return true;
}

std::string TerminalLatchPrefix(
    const PaperTerminalFenceBinding& binding,
    const std::string& state,
    const PaperTerminalMutationManifest* manifest)
{
    std::ostringstream out;
    out << "HPT2\n"
        << "state=" << state << '\n'
        << "finalization_id=" << binding.finalizationId << '\n'
        << "preliminary_finalization_receipt_sha256="
        << binding.preliminaryReceiptSha256 << '\n'
        << "owner_agent_id=" << binding.owner.agentId << '\n'
        << "owner_session_id=" << binding.owner.sessionId << '\n'
        << "owner_account=" << binding.owner.account << '\n'
        << "owner_execution_domain="
        << binding.owner.executionDomain << '\n'
        << "recovery_ingress_fence="
        << binding.recoveryIngressFence << '\n'
        << "terminalization_service_epoch=" << binding.serviceEpoch << '\n'
        << "terminalization_service_fencing_generation="
        << binding.serviceFencingGeneration << '\n'
        << "terminalization_generation=1\n"
        << "service_process_id=" << binding.serviceProcessId << '\n'
        << "service_process_start_ticks="
        << binding.serviceProcessStartTicks << '\n'
        << "broker_connection_epoch=" << binding.brokerConnectionEpoch << '\n'
        << "broker_socket_identity_sha256="
        << binding.brokerSocketIdentitySha256 << '\n';
    if (manifest != nullptr)
    {
        out << "mutation_manifest_file="
            << PaperTerminalMutationManifestFileName() << '\n'
            << "mutation_manifest_file_sha256="
            << manifest->fileSha256 << '\n'
            << "mutation_manifest_body_sha256="
            << manifest->bodySha256 << '\n'
            << "known_mutation_command_set_sha256="
            << manifest->universe.commandSetSha256 << '\n'
            << "known_mutation_command_count="
            << manifest->universe.commands.size() << '\n'
            << "known_correlation_set_sha256="
            << manifest->universe.correlationSetSha256 << '\n'
            << "known_correlation_count="
            << manifest->universe.correlations.size() << '\n';
    }
    return out.str();
}

bool ReadSelfStartTicks(std::uint64_t& ticks)
{
    ticks = 0;
    const std::string path = std::string("/proc/") +
        std::to_string(static_cast<unsigned long>(::getpid())) + "/stat";
    const int fd = ::open(path.c_str(), O_RDONLY | O_CLOEXEC | O_NOFOLLOW);
    if (fd < 0) return false;
    char buffer[4096];
    const ssize_t count = ::read(fd, buffer, sizeof(buffer));
    const bool closed = ::close(fd) == 0;
    if (count <= 0 || count >= static_cast<ssize_t>(sizeof(buffer)) || !closed)
        return false;
    const std::string contents(buffer, static_cast<std::size_t>(count));
    const std::size_t commandEnd = contents.rfind(')');
    if (commandEnd == std::string::npos || commandEnd + 2 >= contents.size())
        return false;
    std::istringstream fields(contents.substr(commandEnd + 2));
    std::string value;
    for (int index = 3; index <= 22; ++index)
    {
        if (!(fields >> value)) return false;
        if (index == 22) return ParseUnsigned(value, ticks) && ticks != 0;
    }
    return false;
}

void AppendTerminalAudit(
    std::ostringstream& out, const ExecutionControlResult& audit)
{
    out << "broker_active_generation=" << audit.brokerActiveGeneration << '\n'
        << "broker_terminal_generation=" << audit.brokerTerminalGeneration << '\n'
        << "broker_risk_generation=" << audit.brokerRiskGeneration << '\n'
        << "broker_account_generation=" << audit.brokerAccountGeneration << '\n'
        << "broker_position_generation="
        << audit.brokerPositionGeneration << '\n'
        << "broker_fx_cash_generation=" << audit.brokerFxCashGeneration << '\n'
        << "broker_exposure_generation=" << audit.brokerExposureGeneration << '\n'
        << "broker_terminal_exposure_generation="
        << audit.brokerTerminalExposureGeneration << '\n'
        << "broker_risk_absorbed_exposure_generation="
        << audit.brokerRiskAbsorbedExposureGeneration << '\n'
        << "broker_global_active_order_count="
        << audit.brokerGlobalActiveOrderCount << '\n'
        << "owner_active_order_count=" << audit.ownerActiveOrderCount << '\n'
        << "owner_uncertain_command_count="
        << audit.ownerUncertainCommandCount << '\n'
        << "broker_post_fill_risk_reconciliation_pending="
        << (audit.brokerPostFillRiskReconciliationPending ? 1 : 0) << '\n'
        << "broker_recovery_audit_barrier_complete="
        << (audit.brokerRecoveryAuditBarrierComplete ? 1 : 0) << '\n'
        << "broker_recovery_audit_new_connection_epoch_required="
        << (audit.brokerRecoveryAuditNewConnectionEpochRequired ? 1 : 0)
        << '\n'
        << "broker_position_quantity=" << audit.brokerPositionQuantity << '\n'
        << "broker_gross_absolute_position="
        << audit.brokerGrossAbsolutePosition << '\n'
        << "execution_mutation_gate_closed=1\n"
        << "broker_transport_connected=0\n"
        << "broker_event_ingress_halted=1\n"
        << "broker_callback_queue_drained=1\n"
        << "broker_callbacks_in_flight=0\n"
        << "broker_reconnect_permitted=0\n"
        << "terminal_latch_durable=1\n";
}

namespace
{
bool LatchText(const std::string& value, std::size_t maximum)
{
    if (value.empty() || value.size() > maximum) return false;
    for (std::size_t i = 0; i < value.size(); ++i)
    {
        const unsigned char byte = static_cast<unsigned char>(value[i]);
        if (byte < 0x21 || byte > 0x7e || byte == '=') return false;
    }
    return true;
}

bool LatchSha256(const std::string& value)
{
    if (value.size() != 71 || value.compare(0, 7, "sha256:") != 0)
        return false;
    for (std::size_t i = 7; i < value.size(); ++i)
        if (!((value[i] >= '0' && value[i] <= '9') ||
              (value[i] >= 'a' && value[i] <= 'f')))
            return false;
    return true;
}

const std::string& LatchField(
    const std::map<std::string, std::string>& fields,
    const char* name)
{
    static const std::string missing;
    const std::map<std::string, std::string>::const_iterator found =
        fields.find(name);
    return found == fields.end() ? missing : found->second;
}

bool ValidateHaltedLatchAudit(
    const std::map<std::string, std::string>& fields,
    const std::string& latchSha256,
    ExecutionControlResult& terminal,
    std::string& reason)
{
    std::uint64_t* const numbers[] = {
        &terminal.brokerConnectionEpoch,
        &terminal.brokerActiveGeneration,
        &terminal.brokerTerminalGeneration,
        &terminal.brokerRiskGeneration,
        &terminal.brokerAccountGeneration,
        &terminal.brokerPositionGeneration,
        &terminal.brokerFxCashGeneration,
        &terminal.brokerExposureGeneration,
        &terminal.brokerTerminalExposureGeneration,
        &terminal.brokerRiskAbsorbedExposureGeneration,
        &terminal.brokerGlobalActiveOrderCount,
        &terminal.ownerActiveOrderCount,
        &terminal.ownerUncertainCommandCount,
        &terminal.terminalBrokerCallbacksInFlight
    };
    const char* const numberNames[] = {
        "broker_connection_epoch", "broker_active_generation",
        "broker_terminal_generation", "broker_risk_generation",
        "broker_account_generation", "broker_position_generation",
        "broker_fx_cash_generation", "broker_exposure_generation",
        "broker_terminal_exposure_generation",
        "broker_risk_absorbed_exposure_generation",
        "broker_global_active_order_count", "owner_active_order_count",
        "owner_uncertain_command_count", "broker_callbacks_in_flight"
    };
    bool numbersValid = true;
    for (std::size_t i = 0;
         i < sizeof(numbers) / sizeof(numbers[0]); ++i)
        numbersValid = numbersValid && ParseUnsigned(
            LatchField(fields, numberNames[i]), *numbers[i]);
    terminal.brokerPositionQuantity =
        LatchField(fields, "broker_position_quantity");
    terminal.brokerGrossAbsolutePosition =
        LatchField(fields, "broker_gross_absolute_position");
    const bool fixedValues =
        LatchField(fields, "broker_post_fill_risk_reconciliation_pending") == "0" &&
        LatchField(fields, "broker_recovery_audit_barrier_complete") == "1" &&
        LatchField(fields, "broker_recovery_audit_new_connection_epoch_required") == "0" &&
        LatchField(fields, "execution_mutation_gate_closed") == "1" &&
        LatchField(fields, "broker_transport_connected") == "0" &&
        LatchField(fields, "broker_event_ingress_halted") == "1" &&
        LatchField(fields, "broker_callback_queue_drained") == "1" &&
        LatchField(fields, "broker_reconnect_permitted") == "0" &&
        LatchField(fields, "terminal_latch_durable") == "1";
    terminal.brokerPostFillRiskReconciliationPending = false;
    terminal.brokerRecoveryAuditBarrierComplete = true;
    terminal.brokerRecoveryAuditNewConnectionEpochRequired = false;
    terminal.terminalLatchDurable = true;
    terminal.ownerAuditAuthoritative = true;
    terminal.ownerAuditComplete = true;
    terminal.mutationBlocked = true;
    if (!numbersValid || !fixedValues ||
        terminal.brokerConnectionEpoch == 0 ||
        terminal.brokerActiveGeneration == 0 ||
        terminal.brokerTerminalGeneration == 0 ||
        terminal.brokerRiskGeneration == 0 ||
        terminal.brokerAccountGeneration == 0 ||
        terminal.brokerPositionGeneration == 0 ||
        terminal.brokerFxCashGeneration == 0 ||
        terminal.brokerGlobalActiveOrderCount != 0 ||
        terminal.ownerActiveOrderCount != 0 ||
        terminal.ownerUncertainCommandCount != 0 ||
        terminal.terminalBrokerCallbacksInFlight != 0 ||
        terminal.brokerPositionQuantity != "0" ||
        terminal.brokerGrossAbsolutePosition != "0" ||
        terminal.brokerTerminalExposureGeneration >
            terminal.brokerRiskAbsorbedExposureGeneration ||
        terminal.brokerRiskAbsorbedExposureGeneration !=
            terminal.brokerExposureGeneration ||
        !LatchSha256(latchSha256))
    {
        reason = "IB_PAPER_TERMINAL_LATCH_INVALID";
        return false;
    }
    terminal.terminalRuntimeVerified = true;
    terminal.reasonCode = "PAPER_EXECUTION_TERMINAL_HALTED";
    return true;
}
}

bool DecodePaperTerminalLatchContents(
    const std::string& stateDirectory,
    const std::string& contents,
    PaperTerminalLatchDecoded& decoded,
    std::string& reason)
{
    decoded = PaperTerminalLatchDecoded();
    std::istringstream input(contents);
    std::string line;
    if (!std::getline(input, line) || line != "HPT2")
    {
        reason = "IB_PAPER_TERMINAL_LATCH_INVALID";
        return false;
    }
    std::map<std::string, std::string> fields;
    while (std::getline(input, line))
    {
        if (line.empty()) continue;
        const std::size_t separator = line.find('=');
        if (separator == std::string::npos || separator == 0 ||
            separator + 1 >= line.size() ||
            !fields.insert(std::make_pair(line.substr(0, separator),
                line.substr(separator + 1))).second)
        {
            reason = "IB_PAPER_TERMINAL_LATCH_INVALID";
            return false;
        }
    }
    const std::map<std::string, std::string>::const_iterator stateIt =
        fields.find("state");
    const bool preparing = stateIt != fields.end() &&
        stateIt->second == "PREPARING";
    const bool terminalizing = stateIt != fields.end() &&
        stateIt->second == "TERMINALIZING";
    const bool halted = stateIt != fields.end() &&
        stateIt->second == "TERMINAL_HALTED";
    const char* const commonNames[] = {
        "state", "finalization_id",
        "preliminary_finalization_receipt_sha256", "owner_agent_id",
        "owner_session_id", "owner_account", "owner_execution_domain",
        "recovery_ingress_fence", "terminalization_service_epoch",
        "terminalization_service_fencing_generation",
        "terminalization_generation", "service_process_id",
        "service_process_start_ticks", "broker_connection_epoch",
        "broker_socket_identity_sha256"
    };
    std::set<std::string> expected;
    for (std::size_t i = 0;
         i < sizeof(commonNames) / sizeof(commonNames[0]); ++i)
        expected.insert(commonNames[i]);
    const char* const manifestNames[] = {
        "mutation_manifest_file", "mutation_manifest_file_sha256",
        "mutation_manifest_body_sha256",
        "known_mutation_command_set_sha256", "known_mutation_command_count",
        "known_correlation_set_sha256", "known_correlation_count"
    };
    if (terminalizing || halted)
        for (std::size_t i = 0;
             i < sizeof(manifestNames) / sizeof(manifestNames[0]); ++i)
            expected.insert(manifestNames[i]);
    const char* const auditNames[] = {
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
        "execution_mutation_gate_closed", "broker_transport_connected",
        "broker_event_ingress_halted", "broker_callback_queue_drained",
        "broker_callbacks_in_flight", "broker_reconnect_permitted",
        "terminal_latch_durable"
    };
    if (halted)
        for (std::size_t i = 0;
             i < sizeof(auditNames) / sizeof(auditNames[0]); ++i)
            expected.insert(auditNames[i]);
    if ((!preparing && !terminalizing && !halted) ||
        fields.size() != expected.size())
    {
        reason = "IB_PAPER_TERMINAL_LATCH_INVALID";
        return false;
    }
    for (std::map<std::string, std::string>::const_iterator it =
             fields.begin(); it != fields.end(); ++it)
    {
        if (expected.find(it->first) == expected.end())
        {
            reason = "IB_PAPER_TERMINAL_LATCH_INVALID";
            return false;
        }
    }
    const std::string& finalization = LatchField(fields, "finalization_id");
    const std::string& preliminary =
        LatchField(fields, "preliminary_finalization_receipt_sha256");
    const std::string& agent = LatchField(fields, "owner_agent_id");
    const std::string& session = LatchField(fields, "owner_session_id");
    const std::string& account = LatchField(fields, "owner_account");
    const std::string& domain = LatchField(fields, "owner_execution_domain");
    const std::string& serviceEpoch =
        LatchField(fields, "terminalization_service_epoch");
    std::uint64_t serviceFence = 0;
    std::uint64_t terminalGeneration = 0;
    std::uint64_t recoveryIngressFence = 0;
    std::uint64_t serviceProcessId = 0;
    std::uint64_t serviceProcessStartTicks = 0;
    std::uint64_t brokerConnectionEpoch = 0;
    if (!LatchText(finalization, 128) || !LatchSha256(preliminary) ||
        !LatchText(agent, 128) || !LatchText(session, 128) ||
        !LatchText(account, 128) || !LatchText(domain, 128) ||
        !LatchText(serviceEpoch, 128) ||
        !ParseUnsigned(LatchField(fields, "recovery_ingress_fence"),
            recoveryIngressFence) || recoveryIngressFence == 0 ||
        !ParseUnsigned(LatchField(fields,
            "terminalization_service_fencing_generation"),
            serviceFence) ||
        !ParseUnsigned(LatchField(fields, "terminalization_generation"),
            terminalGeneration) || terminalGeneration != 1 ||
        !ParseUnsigned(LatchField(fields, "service_process_id"),
            serviceProcessId) ||
        serviceProcessId == 0 ||
        !ParseUnsigned(LatchField(fields, "service_process_start_ticks"),
            serviceProcessStartTicks) || serviceProcessStartTicks == 0 ||
        !ParseUnsigned(LatchField(fields, "broker_connection_epoch"),
            brokerConnectionEpoch) || brokerConnectionEpoch == 0 ||
        !LatchSha256(LatchField(fields, "broker_socket_identity_sha256")))
    {
        reason = "IB_PAPER_TERMINAL_LATCH_INVALID";
        return false;
    }

    PaperTerminalFenceBinding binding;
    binding.owner.agentId = agent;
    binding.owner.sessionId = session;
    binding.owner.account = account;
    binding.owner.executionDomain = domain;
    binding.finalizationId = finalization;
    binding.preliminaryReceiptSha256 = preliminary;
    binding.recoveryIngressFence = recoveryIngressFence;
    binding.serviceEpoch = serviceEpoch;
    binding.serviceFencingGeneration = serviceFence;
    binding.serviceProcessId = serviceProcessId;
    binding.serviceProcessStartTicks = serviceProcessStartTicks;
    binding.brokerConnectionEpoch = brokerConnectionEpoch;
    binding.brokerSocketIdentitySha256 =
        LatchField(fields, "broker_socket_identity_sha256");
    if (!ValidPaperTerminalFenceBinding(binding, reason))
    {
        reason = "IB_PAPER_TERMINAL_LATCH_INVALID";
        return false;
    }
    PaperTerminalMutationManifest manifest;
    std::uint64_t commandCount = 0;
    std::uint64_t correlationCount = 0;
    if ((terminalizing || halted) &&
        (LatchField(fields, "mutation_manifest_file") !=
            PaperTerminalMutationManifestFileName() ||
         !LatchSha256(LatchField(fields,
             "mutation_manifest_file_sha256")) ||
         !LatchSha256(LatchField(fields,
             "mutation_manifest_body_sha256")) ||
         !LatchSha256(LatchField(fields,
             "known_mutation_command_set_sha256")) ||
         !LatchSha256(LatchField(fields,
             "known_correlation_set_sha256")) ||
         !ParseUnsigned(LatchField(fields, "known_mutation_command_count"),
            commandCount) ||
         !ParseUnsigned(LatchField(fields, "known_correlation_count"),
            correlationCount) ||
         !LoadPaperTerminalMutationManifest(
            stateDirectory, binding, manifest, reason) ||
         manifest.fileSha256 != LatchField(fields,
             "mutation_manifest_file_sha256") ||
         manifest.bodySha256 != LatchField(fields,
             "mutation_manifest_body_sha256") ||
         manifest.universe.commandSetSha256 !=
            LatchField(fields, "known_mutation_command_set_sha256") ||
         manifest.universe.correlationSetSha256 !=
            LatchField(fields, "known_correlation_set_sha256") ||
         manifest.universe.commands.size() != commandCount ||
         manifest.universe.correlations.size() != correlationCount))
    {
        reason = "IB_PAPER_TERMINAL_MANIFEST_BINDING_MISMATCH";
        return false;
    }

    ExecutionControlResult terminal;
    terminal.status = ExecutionCommandStatus::Accepted;
    terminal.targetCommandId = finalization;
    terminal.ownerAccount = account;
    terminal.ownerExecutionDomain = domain;
    terminal.terminalizationServiceEpoch = serviceEpoch;
    terminal.terminalizationServiceFencingGeneration = serviceFence;
    terminal.terminalizationGeneration = terminalGeneration;
    terminal.terminalLatchSha256 = Sha256(contents);
    terminal.terminalServiceProcessId = serviceProcessId;
    terminal.terminalServiceProcessStartTicks = serviceProcessStartTicks;
    terminal.terminalBrokerSocketIdentitySha256 =
        binding.brokerSocketIdentitySha256;
    terminal.brokerConnectionEpoch = brokerConnectionEpoch;
    terminal.terminalMutationGateClosed = !preparing;
    terminal.terminalBrokerTransportConnected = false;
    terminal.terminalBrokerEventIngressHalted = true;
    terminal.terminalBrokerCallbackQueueDrained = halted;
    terminal.terminalBrokerCallbacksInFlight = 0;
    terminal.terminalBrokerReconnectPermitted = false;
    terminal.terminalRuntimeLatchLoaded = true;
    terminal.terminalLatchDurable = true;
    terminal.terminalReplay = true;
    if (!preparing)
    {
        terminal.terminalMutationManifestFile =
            PaperTerminalMutationManifestFileName();
        terminal.terminalMutationManifestFileSha256 = manifest.fileSha256;
        terminal.terminalMutationManifestBodySha256 = manifest.bodySha256;
        terminal.terminalKnownMutationCommandSetSha256 =
            manifest.universe.commandSetSha256;
        terminal.terminalKnownMutationCommandCount =
            manifest.universe.commands.size();
        terminal.terminalKnownCorrelationSetSha256 =
            manifest.universe.correlationSetSha256;
        terminal.terminalKnownCorrelationCount =
            manifest.universe.correlations.size();
    }
    if (halted && !ValidateHaltedLatchAudit(
            fields, terminal.terminalLatchSha256, terminal, reason))
        return false;

    decoded.preparing = preparing;
    decoded.halted = halted;
    decoded.binding = binding;
    decoded.manifest = manifest;
    decoded.terminal = terminal;
    reason.clear();
    return true;
}

bool ExecutionCoordinator::EnterPaperTerminalFenceAndProjectLocked(
    const PaperTerminalFenceBinding& binding,
    PaperTerminalMutationUniverse& universe,
    std::string& reason)
{
    if (m_mutationBlocked)
    {
        if (m_mutationBlockReason != "IB_PAPER_TERMINAL_HALTED" ||
            !m_paperTerminalFencePresent ||
            !SamePaperTerminalFenceBinding(
                m_paperTerminalFenceBinding, binding))
        {
            reason = m_mutationBlockReason == "IB_PAPER_TERMINAL_HALTED" ?
                "IB_PAPER_TERMINAL_FENCE_BINDING_MISMATCH" :
                (m_mutationBlockReason.empty() ?
                    "IB_PAPER_TERMINAL_FENCE_COORDINATOR_BLOCKED" :
                    m_mutationBlockReason);
            return false;
        }
    }
    else
    {
        if (!m_orderOwners.empty())
        {
            reason = "IB_PAPER_TERMINAL_FENCE_LOCAL_ORDERS_UNSAFE";
            return false;
        }
        AgentExecutionContext journalContext = binding.owner;
        journalContext.toolCallId = binding.finalizationId;
        const std::string encoded =
            EncodePaperTerminalFenceBinding(binding);
        if (encoded.empty())
        {
            reason = "IB_PAPER_TERMINAL_FENCE_BINDING_INVALID";
            return false;
        }
        const OmsJournalEvent event = BuildEvent(
            journalContext, "paper_terminal_fence", -1, "", "", 0.0,
            0.0, std::to_string(binding.recoveryIngressFence), encoded,
            "IB_PAPER_TERMINAL_HALTED",
            binding.preliminaryReceiptSha256,
            binding.brokerSocketIdentitySha256);
        if (!AppendOrBlockLocked(
                event, "OMS_PAPER_TERMINAL_FENCE_JOURNAL_FAILED"))
        {
            reason = "OMS_PAPER_TERMINAL_FENCE_JOURNAL_FAILED";
            return false;
        }
        m_mutationBlocked = true;
        m_mutationBlockReason = "IB_PAPER_TERMINAL_HALTED";
        m_paperTerminalFencePresent = true;
        m_paperTerminalFenceBinding = binding;
    }

    std::vector<PaperTerminalMutationRecord> records;
    for (std::unordered_map<std::string, RequestRecord>::const_iterator it =
             m_requests.begin(); it != m_requests.end(); ++it)
    {
        const RequestRecord& request = it->second;
        if (!request.durableMutationIntent ||
            request.context.account != binding.owner.account ||
            request.context.executionDomain !=
                binding.owner.executionDomain)
            continue;
        PaperTerminalMutationRecord record;
        record.agentId = request.context.agentId;
        record.sessionId = request.context.sessionId;
        record.toolCallId = request.context.toolCallId;
        record.operation = request.operation;
        record.venueCorrelationId = request.venueCorrelationId;
        records.push_back(record);
    }
    return BuildPaperTerminalMutationUniverse(records, universe, reason);
}

bool ExecutionCoordinator::ApplyRecoveredPaperTerminalFenceLocked(
    const OmsJournalEvent& event, const std::string& agentId)
{
    PaperTerminalFenceBinding binding;
    std::string parseReason;
    if (!DecodePaperTerminalFenceBinding(
            event.reason, binding, parseReason) ||
        binding.owner.agentId != agentId ||
        binding.owner.sessionId != event.traceId ||
        binding.owner.account != event.account ||
        binding.owner.executionDomain != event.executionDomain ||
        binding.finalizationId != event.reqId ||
        binding.preliminaryReceiptSha256 != event.requestHash ||
        binding.brokerSocketIdentitySha256 != event.venueCorrelationId ||
        event.status != std::to_string(binding.recoveryIngressFence) ||
        event.riskCode != "IB_PAPER_TERMINAL_HALTED")
    {
        BlockMutationsLocked(parseReason.empty() ?
            "OMS_PAPER_TERMINAL_FENCE_BINDING_INVALID" : parseReason);
        return true;
    }
    if (m_paperTerminalFencePresent &&
        !SamePaperTerminalFenceBinding(
            m_paperTerminalFenceBinding, binding))
    {
        BlockMutationsLocked("OMS_PAPER_TERMINAL_FENCE_CONFLICT");
        return true;
    }
    m_paperTerminalFencePresent = true;
    m_paperTerminalFenceBinding = binding;
    m_mutationBlocked = true;
    m_mutationBlockReason = "IB_PAPER_TERMINAL_HALTED";
    return true;
}
