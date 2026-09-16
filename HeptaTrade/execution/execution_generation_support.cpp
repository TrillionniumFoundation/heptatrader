// Canonical generation-backed OMS implementation. Compiled exactly once
// in the existing execution runtime target; no macro method renaming or
// textual .inc inclusion is required.
#include "execution_coordinator.h"

// Generation persistence remains part of the existing Execution runtime target.
// It is isolated behind oms_generation_store.h without a shadow service, product,
// or textual include boundary.

#include "../oms_generation_store.h"

#include <algorithm>
#include <array>
#include <cerrno>
#include <climits>
#include <cstdlib>
#include <fcntl.h>
#include <iomanip>
#include <limits>
#include <map>
#include <openssl/evp.h>
#include <sstream>
#include <set>
#include <sys/stat.h>
#include <tuple>
#include <unistd.h>

namespace
{
const std::size_t kMaximumGenerationMetadataBytes = 1024U * 1024U;
const std::size_t kMaximumGenerationIndexLineBytes = 64U * 1024U;
const std::size_t kHistoricalCommandCache = 256U;
const char* const kRuntimeCurrentHeader = "HEPTA_OMS_RUNTIME_CURRENT_V1";
const char* const kRuntimeManifestHeader = "HEPTA_OMS_RUNTIME_GENERATION_V1";

typedef std::map<std::string, std::string> GenerationFields;

bool GenerationPrivateDirectory(const struct stat& metadata)
{
    return S_ISDIR(metadata.st_mode) && metadata.st_uid == ::geteuid() &&
        (metadata.st_mode & 0777) == 0700;
}

bool GenerationPrivateFile(const struct stat& metadata)
{
    return S_ISREG(metadata.st_mode) && metadata.st_uid == ::geteuid() &&
        (metadata.st_mode & 07777) == 0600 && metadata.st_nlink == 1;
}

bool GenerationSameIdentity(const struct stat& left, const struct stat& right)
{
    return left.st_dev == right.st_dev && left.st_ino == right.st_ino &&
        left.st_mode == right.st_mode && left.st_nlink == right.st_nlink &&
        left.st_uid == right.st_uid && left.st_gid == right.st_gid &&
        left.st_size == right.st_size && left.st_mtim.tv_sec == right.st_mtim.tv_sec &&
        left.st_mtim.tv_nsec == right.st_mtim.tv_nsec &&
        left.st_ctim.tv_sec == right.st_ctim.tv_sec &&
        left.st_ctim.tv_nsec == right.st_ctim.tv_nsec;
}

bool GenerationHexDigest(const std::string& value)
{
    if (value.size() != 64) return false;
    for (std::size_t i = 0; i < value.size(); ++i)
        if (!((value[i] >= '0' && value[i] <= '9') ||
              (value[i] >= 'a' && value[i] <= 'f')))
            return false;
    return true;
}

bool GenerationSafeName(const std::string& value)
{
    if (value.empty() || value.size() > 128 || value == "." || value == "..")
        return false;
    for (std::size_t i = 0; i < value.size(); ++i)
    {
        const char c = value[i];
        if (!((c >= 'a' && c <= 'z') || (c >= 'A' && c <= 'Z') ||
              (c >= '0' && c <= '9') || c == '-' || c == '_' || c == '.'))
            return false;
    }
    return true;
}

bool GenerationParseUnsigned(const std::string& value, std::uint64_t& result)
{
    if (value.empty() || (value.size() > 1 && value[0] == '0')) return false;
    result = 0;
    for (std::size_t i = 0; i < value.size(); ++i)
    {
        if (value[i] < '0' || value[i] > '9') return false;
        const std::uint64_t digit = static_cast<std::uint64_t>(value[i] - '0');
        if (result > (std::numeric_limits<std::uint64_t>::max() - digit) / 10U)
            return false;
        result = result * 10U + digit;
    }
    return true;
}

bool GenerationParseSigned(const std::string& value, long& result)
{
    if (value.empty()) return false;
    char* end = nullptr;
    errno = 0;
    const long parsed = std::strtol(value.c_str(), &end, 10);
    if (errno != 0 || end == value.c_str() || *end != '\0') return false;
    result = parsed;
    return true;
}

bool GenerationParseSigned64(const std::string& value, std::int64_t& result)
{
    if (value.empty()) return false;
    char* end = nullptr;
    errno = 0;
    const long long parsed = std::strtoll(value.c_str(), &end, 10);
    if (errno != 0 || end == value.c_str() || *end != '\0') return false;
    result = static_cast<std::int64_t>(parsed);
    return true;
}

std::string GenerationHex(const std::string& value)
{
    static const char digits[] = "0123456789abcdef";
    std::string result;
    result.reserve(value.size() * 2U);
    for (std::size_t i = 0; i < value.size(); ++i)
    {
        const unsigned char byte = static_cast<unsigned char>(value[i]);
        result.push_back(digits[byte >> 4]);
        result.push_back(digits[byte & 15U]);
    }
    return result;
}

bool GenerationDecodeHex(const std::string& encoded, std::string& value)
{
    if (encoded.size() % 2U != 0) return false;
    value.clear();
    value.reserve(encoded.size() / 2U);
    for (std::size_t offset = 0; offset < encoded.size(); offset += 2U)
    {
        unsigned int byte = 0;
        for (std::size_t digit = 0; digit < 2U; ++digit)
        {
            const char c = encoded[offset + digit];
            unsigned int nibble = 0;
            if (c >= '0' && c <= '9') nibble = static_cast<unsigned int>(c - '0');
            else if (c >= 'a' && c <= 'f') nibble = 10U + static_cast<unsigned int>(c - 'a');
            else return false;
            byte = byte * 16U + nibble;
        }
        value.push_back(static_cast<char>(byte));
    }
    return true;
}

std::string GenerationSha256(const void* data, std::size_t size)
{
    unsigned char digest[EVP_MAX_MD_SIZE];
    unsigned int length = 0;
    EVP_MD_CTX* context = EVP_MD_CTX_new();
    if (context == nullptr) return std::string();
    const bool ok = EVP_DigestInit_ex(context, EVP_sha256(), nullptr) == 1 &&
        EVP_DigestUpdate(context, data, size) == 1 &&
        EVP_DigestFinal_ex(context, digest, &length) == 1;
    EVP_MD_CTX_free(context);
    if (!ok || length != 32) return std::string();
    std::ostringstream output;
    output << std::hex << std::setfill('0');
    for (unsigned int i = 0; i < length; ++i)
        output << std::setw(2) << static_cast<unsigned int>(digest[i]);
    return output.str();
}

bool GenerationHashFd(int fd, off_t size, std::string& digest)
{
    if (size < 0) return false;
    EVP_MD_CTX* context = EVP_MD_CTX_new();
    if (context == nullptr) return false;
    bool ok = EVP_DigestInit_ex(context, EVP_sha256(), nullptr) == 1;
    std::array<char, 64U * 1024U> buffer;
    off_t offset = 0;
    while (ok && offset < size)
    {
        const std::size_t wanted = static_cast<std::size_t>(std::min<off_t>(
            static_cast<off_t>(buffer.size()), size - offset));
        ssize_t count;
        do
        {
            count = ::pread(fd, buffer.data(), wanted, offset);
        } while (count < 0 && errno == EINTR);
        if (count <= 0) { ok = false; break; }
        ok = EVP_DigestUpdate(context, buffer.data(), static_cast<std::size_t>(count)) == 1;
        offset += count;
    }
    unsigned char bytes[EVP_MAX_MD_SIZE];
    unsigned int length = 0;
    ok = ok && EVP_DigestFinal_ex(context, bytes, &length) == 1 && length == 32;
    EVP_MD_CTX_free(context);
    if (!ok) return false;
    std::ostringstream output;
    output << std::hex << std::setfill('0');
    for (unsigned int i = 0; i < length; ++i)
        output << std::setw(2) << static_cast<unsigned int>(bytes[i]);
    digest = output.str();
    return true;
}

bool GenerationReadPrivateFileAt(int directoryFd, const char* name,
                                 std::size_t maximum, std::string& contents,
                                 struct stat* identity = nullptr)
{
    const int fd = ::openat(directoryFd, name, O_RDONLY | O_CLOEXEC | O_NOFOLLOW);
    if (fd < 0) return false;
    struct stat before;
    bool ok = ::fstat(fd, &before) == 0 && GenerationPrivateFile(before) &&
        before.st_size >= 0 && static_cast<std::uint64_t>(before.st_size) <= maximum;
    contents.clear();
    if (ok)
    {
        contents.resize(static_cast<std::size_t>(before.st_size));
        std::size_t offset = 0;
        while (ok && offset < contents.size())
        {
            ssize_t count;
            do
            {
                count = ::read(fd, &contents[offset], contents.size() - offset);
            } while (count < 0 && errno == EINTR);
            if (count <= 0) ok = false;
            else offset += static_cast<std::size_t>(count);
        }
    }
    struct stat after, named;
    ok = ok && ::fstat(fd, &after) == 0 &&
        ::fstatat(directoryFd, name, &named, AT_SYMLINK_NOFOLLOW) == 0 &&
        GenerationSameIdentity(before, after) && GenerationSameIdentity(after, named);
    if (identity != nullptr && ok) *identity = after;
    if (::close(fd) != 0) ok = false;
    return ok;
}

bool GenerationOpenHashedFileAt(int directoryFd, const char* name,
                                const std::string& expectedDigest,
                                int& fd, struct stat& identity)
{
    fd = ::openat(directoryFd, name, O_RDONLY | O_CLOEXEC | O_NOFOLLOW);
    if (fd < 0) return false;
    struct stat named;
    std::string digest;
    const bool ok = ::fstat(fd, &identity) == 0 && GenerationPrivateFile(identity) &&
        ::fstatat(directoryFd, name, &named, AT_SYMLINK_NOFOLLOW) == 0 &&
        GenerationSameIdentity(identity, named) &&
        GenerationHashFd(fd, identity.st_size, digest) && digest == expectedDigest;
    if (!ok)
    {
        ::close(fd);
        fd = -1;
    }
    return ok;
}

bool GenerationVerifyHashedFileAt(int directoryFd, const char* name,
                                  const std::string& expectedDigest)
{
    int fd = -1;
    struct stat identity;
    const bool ok = GenerationOpenHashedFileAt(
        directoryFd, name, expectedDigest, fd, identity);
    if (fd >= 0) ::close(fd);
    return ok;
}

bool GenerationParseFields(const std::string& contents, const char* header,
                           GenerationFields& fields)
{
    fields.clear();
    std::istringstream input(contents);
    std::string line;
    if (!std::getline(input, line) || line != header) return false;
    while (std::getline(input, line))
    {
        if (line.empty()) continue;
        const std::size_t separator = line.find('=');
        if (separator == std::string::npos || separator == 0 ||
            separator + 1 >= line.size() ||
            !fields.insert(std::make_pair(line.substr(0, separator),
                line.substr(separator + 1))).second)
            return false;
    }
    return true;
}

bool GenerationExactFieldNames(const GenerationFields& fields,
                               const char* const* names, std::size_t count)
{
    if (fields.size() != count) return false;
    for (std::size_t i = 0; i < count; ++i)
        if (fields.find(names[i]) == fields.end()) return false;
    return true;
}

bool GenerationSplitTabs(const std::string& line,
                         std::vector<std::string>& fields)
{
    fields.clear();
    if (line.size() > kMaximumGenerationIndexLineBytes || line.empty()) return false;
    std::size_t offset = 0;
    for (;;)
    {
        const std::size_t found = line.find('\t', offset);
        fields.push_back(line.substr(offset,
            found == std::string::npos ? std::string::npos : found - offset));
        if (found == std::string::npos) return true;
        offset = found + 1;
    }
}

bool GenerationReadLineContaining(int fd, off_t fileSize, off_t probe,
                                  off_t& start, off_t& end, std::string& line)
{
    if (fileSize <= 0) return false;
    if (probe >= fileSize) probe = fileSize - 1;
    const off_t backward = std::min<off_t>(probe,
        static_cast<off_t>(kMaximumGenerationIndexLineBytes));
    const off_t base = probe - backward;
    std::string before(static_cast<std::size_t>(backward), '\0');
    if (backward > 0)
    {
        ssize_t count;
        do
        {
            count = ::pread(fd, &before[0], before.size(), base);
        } while (count < 0 && errno == EINTR);
        if (count != static_cast<ssize_t>(before.size())) return false;
    }
    const std::size_t newline = before.rfind('\n');
    if (newline == std::string::npos)
    {
        if (base != 0) return false;
        start = 0;
    }
    else
        start = base + static_cast<off_t>(newline + 1U);

    const std::size_t maximum = static_cast<std::size_t>(std::min<off_t>(
        static_cast<off_t>(kMaximumGenerationIndexLineBytes + 1U), fileSize - start));
    std::string forward(maximum, '\0');
    ssize_t count;
    do
    {
        count = ::pread(fd, &forward[0], forward.size(), start);
    } while (count < 0 && errno == EINTR);
    if (count <= 0) return false;
    forward.resize(static_cast<std::size_t>(count));
    const std::size_t found = forward.find('\n');
    if (found == std::string::npos || found > kMaximumGenerationIndexLineBytes) return false;
    line.assign(forward.data(), found);
    end = start + static_cast<off_t>(found + 1U);
    return true;
}

int GenerationCompareKey(const std::vector<std::string>& fields,
                         const std::array<std::string, 3>& wanted)
{
    for (std::size_t i = 0; i < 3U; ++i)
    {
        if (fields[i] < wanted[i]) return -1;
        if (fields[i] > wanted[i]) return 1;
    }
    return 0;
}

bool GenerationReplayRange(int fd, off_t start, off_t end,
                           std::size_t maxBytes, std::size_t maxRecords,
                           std::size_t maxRecordBytes,
                           const std::function<void(const OmsJournalEvent&)>& onEvent,
                           std::size_t& records)
{
    records = 0;
    if (start < 0 || end < start ||
        static_cast<std::uint64_t>(end - start) > maxBytes)
        return false;
    std::string pending;
    std::array<char, 64U * 1024U> buffer;
    off_t offset = start;
    while (offset < end)
    {
        const std::size_t wanted = static_cast<std::size_t>(std::min<off_t>(
            static_cast<off_t>(buffer.size()), end - offset));
        ssize_t count;
        do
        {
            count = ::pread(fd, buffer.data(), wanted, offset);
        } while (count < 0 && errno == EINTR);
        if (count <= 0) return false;
        offset += count;
        pending.append(buffer.data(), static_cast<std::size_t>(count));
        for (;;)
        {
            const std::size_t newline = pending.find('\n');
            if (newline == std::string::npos) break;
            if (newline == 0 || newline > maxRecordBytes || records >= maxRecords)
                return false;
            const std::string line = pending.substr(0, newline);
            pending.erase(0, newline + 1U);
            OmsJournalEvent event;
            if (!OmsJournal::ParseJsonLine(line, event)) return false;
            onEvent(event);
            ++records;
        }
        if (pending.size() > maxRecordBytes) return false;
    }
    return pending.empty();
}

std::string GenerationRequestKey(const std::string& agentId,
                                 const std::string& sessionId,
                                 const std::string& commandId)
{
    return agentId + "\x1f" + sessionId + "\x1f" + commandId;
}

ExecutionCommandStatus HistoricalStatus(const std::string& value)
{
    if (value == "accepted") return ExecutionCommandStatus::Accepted;
    if (value == "rejected") return ExecutionCommandStatus::Rejected;
    return ExecutionCommandStatus::Uncertain;
}
}

OmsGenerationStore::OmsGenerationStore(const std::string& journalPath)
    : m_journalPath(journalPath),
      m_storePath(journalPath.empty() ? std::string() : journalPath + ".generations")
{
}

OmsGenerationStore::~OmsGenerationStore() noexcept
{
    Close();
}

void OmsGenerationStore::Close() noexcept
{
    if (m_commandIndexFd >= 0) ::close(m_commandIndexFd);
    if (m_sendIndexFd >= 0) ::close(m_sendIndexFd);
    if (m_generationFd >= 0) ::close(m_generationFd);
    if (m_storeFd >= 0) ::close(m_storeFd);
    m_commandIndexFd = -1;
    m_sendIndexFd = -1;
    m_generationFd = -1;
    m_storeFd = -1;
    m_active = false;
    m_sendIndexWindowSorted = false;
    m_generation.clear();
    m_commandRecords = 0;
    m_sendAttemptRecords = 0;
    m_hotReplayRecords = 0;
    m_journalPrefixBytes = 0;
    m_journalPrefixSha256.clear();
}

bool OmsGenerationStore::HasStore() const
{
    if (m_storePath.empty()) return false;
    struct stat metadata;
    if (::lstat(m_storePath.c_str(), &metadata) != 0)
        return errno != ENOENT;
    return true;
}

bool OmsGenerationStore::PrepareGenerationV1(std::string& reason)
{
    Close();
    m_storeFd = ::open(m_storePath.c_str(),
        O_RDONLY | O_DIRECTORY | O_CLOEXEC | O_NOFOLLOW);
    struct stat storeMetadata;
    if (m_storeFd < 0 || ::fstat(m_storeFd, &storeMetadata) != 0 ||
        !GenerationPrivateDirectory(storeMetadata))
    {
        reason = "OMS_GENERATION_STORE_UNSAFE";
        Close();
        return false;
    }

    std::string runtimeCurrent, current;
    if (!GenerationReadPrivateFileAt(m_storeFd, "CURRENT.runtime",
            kMaximumGenerationMetadataBytes, runtimeCurrent) ||
        !GenerationReadPrivateFileAt(m_storeFd, "CURRENT",
            kMaximumGenerationMetadataBytes, current))
    {
        reason = "OMS_GENERATION_CURRENT_UNSAFE";
        Close();
        return false;
    }
    GenerationFields selected;
    static const char* const currentNames[] = {
        "generation", "current_sha256", "manifest_sha256",
        "runtime_manifest_sha256"
    };
    if (!GenerationParseFields(runtimeCurrent, kRuntimeCurrentHeader, selected) ||
        !GenerationExactFieldNames(selected, currentNames,
            sizeof(currentNames) / sizeof(currentNames[0])) ||
        !GenerationSafeName(selected["generation"]) ||
        !GenerationHexDigest(selected["current_sha256"]) ||
        !GenerationHexDigest(selected["manifest_sha256"]) ||
        !GenerationHexDigest(selected["runtime_manifest_sha256"]) ||
        GenerationSha256(current.data(), current.size()) != selected["current_sha256"])
    {
        reason = "OMS_GENERATION_RUNTIME_CURRENT_INVALID";
        Close();
        return false;
    }
    const std::string expectedCurrent =
        std::string("{\"generation\":\"") + selected["generation"] +
        "\",\"manifest_sha256\":\"" + selected["manifest_sha256"] +
        "\",\"runtime_manifest_sha256\":\"" + selected["runtime_manifest_sha256"] +
        "\",\"schema\":\"heptatrader.oms-current.v1\"}\n";
    if (current != expectedCurrent)
    {
        reason = "OMS_GENERATION_CURRENT_BINDING_INVALID";
        Close();
        return false;
    }
    m_generation = selected["generation"];
    m_generationFd = ::openat(m_storeFd, m_generation.c_str(),
        O_RDONLY | O_DIRECTORY | O_CLOEXEC | O_NOFOLLOW);
    struct stat generationMetadata;
    if (m_generationFd < 0 || ::fstat(m_generationFd, &generationMetadata) != 0 ||
        !GenerationPrivateDirectory(generationMetadata))
    {
        reason = "OMS_GENERATION_DIRECTORY_UNSAFE";
        Close();
        return false;
    }
    if (!GenerationVerifyHashedFileAt(m_generationFd, "manifest.json",
            selected["manifest_sha256"]))
    {
        reason = "OMS_GENERATION_MANIFEST_DIGEST_MISMATCH";
        Close();
        return false;
    }

    std::string runtimeManifest;
    if (!GenerationReadPrivateFileAt(m_generationFd, "runtime-manifest.txt",
            kMaximumGenerationMetadataBytes, runtimeManifest) ||
        GenerationSha256(runtimeManifest.data(), runtimeManifest.size()) !=
            selected["runtime_manifest_sha256"])
    {
        reason = "OMS_GENERATION_RUNTIME_MANIFEST_DIGEST_MISMATCH";
        Close();
        return false;
    }
    GenerationFields manifest;
    static const char* const manifestNames[] = {
        "generation", "parent_generation", "journal_prefix_bytes",
        "journal_prefix_sha256", "journal_records", "command_records",
        "send_attempt_records", "hot_replay_records", "segment_sha256",
        "checkpoint_sha256", "command_index_sha256",
        "runtime_command_index_sha256", "send_attempt_index_sha256",
        "hot_replay_sha256", "authorization_effect", "paper_authorized",
        "live_authorized"
    };
    std::uint64_t journalRecords = 0;
    if (!GenerationParseFields(runtimeManifest, kRuntimeManifestHeader, manifest) ||
        !GenerationExactFieldNames(manifest, manifestNames,
            sizeof(manifestNames) / sizeof(manifestNames[0])) ||
        manifest["generation"] != m_generation ||
        (manifest["parent_generation"] != "-" &&
         !GenerationSafeName(manifest["parent_generation"])) ||
        !GenerationParseUnsigned(manifest["journal_prefix_bytes"], m_journalPrefixBytes) ||
        !GenerationParseUnsigned(manifest["journal_records"], journalRecords) ||
        !GenerationParseUnsigned(manifest["command_records"], m_commandRecords) ||
        !GenerationParseUnsigned(manifest["send_attempt_records"], m_sendAttemptRecords) ||
        !GenerationParseUnsigned(manifest["hot_replay_records"], m_hotReplayRecords) ||
        !GenerationHexDigest(manifest["journal_prefix_sha256"]) ||
        !GenerationHexDigest(manifest["segment_sha256"]) ||
        !GenerationHexDigest(manifest["checkpoint_sha256"]) ||
        !GenerationHexDigest(manifest["command_index_sha256"]) ||
        !GenerationHexDigest(manifest["runtime_command_index_sha256"]) ||
        !GenerationHexDigest(manifest["send_attempt_index_sha256"]) ||
        !GenerationHexDigest(manifest["hot_replay_sha256"]) ||
        manifest["authorization_effect"] != "NONE" ||
        manifest["paper_authorized"] != "0" || manifest["live_authorized"] != "0")
    {
        reason = "OMS_GENERATION_RUNTIME_MANIFEST_INVALID";
        Close();
        return false;
    }
    m_journalPrefixSha256 = manifest["journal_prefix_sha256"];
    if (!GenerationVerifyHashedFileAt(m_generationFd, "segment-000001.jsonl",
            manifest["segment_sha256"]) ||
        !GenerationVerifyHashedFileAt(m_generationFd, "checkpoint.json",
            manifest["checkpoint_sha256"]) ||
        !GenerationVerifyHashedFileAt(m_generationFd, "command-index.tsv",
            manifest["command_index_sha256"]))
    {
        reason = "OMS_GENERATION_RUNTIME_FILE_DIGEST_MISMATCH";
        Close();
        return false;
    }
    struct stat segmentMetadata;
    if (::fstatat(m_generationFd, "segment-000001.jsonl", &segmentMetadata,
            AT_SYMLINK_NOFOLLOW) != 0 || !GenerationPrivateFile(segmentMetadata) ||
        static_cast<std::uint64_t>(segmentMetadata.st_size) != m_journalPrefixBytes)
    {
        reason = "OMS_GENERATION_PREFIX_SIZE_MISMATCH";
        Close();
        return false;
    }
    if (!GenerationOpenHashedFileAt(m_generationFd, "runtime-command-index.tsv",
            manifest["runtime_command_index_sha256"],
            m_commandIndexFd, m_commandIndexIdentity) ||
        !GenerationOpenHashedFileAt(m_generationFd, "send-attempt-index.tsv",
            manifest["send_attempt_index_sha256"],
            m_sendIndexFd, m_sendIndexIdentity) ||
        !GenerationVerifyHashedFileAt(m_generationFd, "hot-replay.jsonl",
            manifest["hot_replay_sha256"]))
    {
        reason = "OMS_GENERATION_RUNTIME_INDEX_DIGEST_MISMATCH";
        Close();
        return false;
    }
    reason.clear();
    return true;
}

bool OmsGenerationStore::ValidatePinnedIndex(
    int fd, const std::string& name, const struct stat& expected) const
{
    if (fd < 0 || m_generationFd < 0) return false;
    struct stat actual, named;
    return ::fstat(fd, &actual) == 0 &&
        ::fstatat(m_generationFd, name.c_str(), &named, AT_SYMLINK_NOFOLLOW) == 0 &&
        GenerationPrivateFile(actual) && GenerationSameIdentity(expected, actual) &&
        GenerationSameIdentity(actual, named);
}

bool OmsGenerationStore::RecoverGenerationV1(
    std::size_t maxTailBytes,
    std::size_t maxTailRecords,
    std::size_t maxRecordBytes,
    const std::function<void(const OmsJournalEvent&)>& onEvent,
    std::string& reason)
{
    if (!PrepareGenerationV1(reason)) return false;
    const int hotFd = ::openat(m_generationFd, "hot-replay.jsonl",
        O_RDONLY | O_CLOEXEC | O_NOFOLLOW);
    struct stat hotIdentity;
    bool ok = hotFd >= 0 && ::fstat(hotFd, &hotIdentity) == 0 &&
        GenerationPrivateFile(hotIdentity) && hotIdentity.st_size >= 0 &&
        static_cast<std::uint64_t>(hotIdentity.st_size) <= maxTailBytes;
    std::size_t hotRecords = 0;
    if (ok)
        ok = GenerationReplayRange(hotFd, 0, hotIdentity.st_size, maxTailBytes,
            maxTailRecords, maxRecordBytes, onEvent, hotRecords) &&
            hotRecords == m_hotReplayRecords;
    if (hotFd >= 0) ::close(hotFd);
    if (!ok)
    {
        reason = "OMS_GENERATION_HOT_REPLAY_FAILED";
        Close();
        return false;
    }

    const int journalFd = ::open(m_journalPath.c_str(),
        O_RDONLY | O_CLOEXEC | O_NOFOLLOW);
    struct stat before, named;
    if (journalFd < 0 || ::fstat(journalFd, &before) != 0 ||
        !GenerationPrivateFile(before) || before.st_size < 0 ||
        static_cast<std::uint64_t>(before.st_size) < m_journalPrefixBytes ||
        ::lstat(m_journalPath.c_str(), &named) != 0 ||
        !GenerationSameIdentity(before, named))
    {
        if (journalFd >= 0) ::close(journalFd);
        reason = "OMS_GENERATION_ACTIVE_JOURNAL_UNSAFE";
        Close();
        return false;
    }
    std::string prefixDigest;
    if (!GenerationHashFd(journalFd, static_cast<off_t>(m_journalPrefixBytes),
            prefixDigest) || prefixDigest != m_journalPrefixSha256)
    {
        ::close(journalFd);
        reason = "OMS_GENERATION_ACTIVE_PREFIX_MISMATCH";
        Close();
        return false;
    }
    if (m_journalPrefixBytes > 0)
    {
        char newline = 0;
        ssize_t count;
        do
        {
            count = ::pread(journalFd, &newline, 1,
                static_cast<off_t>(m_journalPrefixBytes - 1U));
        } while (count < 0 && errno == EINTR);
        if (count != 1 || newline != '\n')
        {
            ::close(journalFd);
            reason = "OMS_GENERATION_PREFIX_NOT_RECORD_ALIGNED";
            Close();
            return false;
        }
    }
    std::size_t tailRecords = 0;
    ok = GenerationReplayRange(journalFd,
        static_cast<off_t>(m_journalPrefixBytes), before.st_size,
        maxTailBytes, maxTailRecords, maxRecordBytes, onEvent, tailRecords);
    struct stat after, namedAfter;
    ok = ok && ::fstat(journalFd, &after) == 0 &&
        ::lstat(m_journalPath.c_str(), &namedAfter) == 0 &&
        GenerationSameIdentity(before, after) && GenerationSameIdentity(after, namedAfter);
    if (::close(journalFd) != 0) ok = false;
    if (!ok)
    {
        reason = "OMS_GENERATION_TAIL_REPLAY_FAILED";
        Close();
        return false;
    }
    m_active = true;
    reason.clear();
    return true;
}

OmsGenerationLookupStatus OmsGenerationStore::LookupCommand(
    const std::string& agentId,
    const std::string& sessionId,
    const std::string& commandId,
    OmsGenerationCommandRecord& record,
    std::string& reason) const
{
    record = OmsGenerationCommandRecord();
    if (!m_active) return OmsGenerationLookupStatus::Missing;
    if (!ValidatePinnedIndex(m_commandIndexFd, "runtime-command-index.tsv",
            m_commandIndexIdentity))
    {
        reason = "OMS_GENERATION_COMMAND_INDEX_CHANGED";
        return OmsGenerationLookupStatus::Error;
    }
    const std::array<std::string, 3> wanted{{
        GenerationHex(agentId), GenerationHex(sessionId), GenerationHex(commandId)}};
    off_t low = 0;
    off_t high = m_commandIndexIdentity.st_size;
    while (low < high)
    {
        const off_t midpoint = low + (high - low) / 2;
        off_t start = 0, end = 0;
        std::string line;
        std::vector<std::string> fields;
        if (!GenerationReadLineContaining(m_commandIndexFd,
                m_commandIndexIdentity.st_size, midpoint, start, end, line) ||
            !GenerationSplitTabs(line, fields) || fields.size() != 13U)
        {
            reason = "OMS_GENERATION_COMMAND_INDEX_INVALID";
            return OmsGenerationLookupStatus::Error;
        }
        const int comparison = GenerationCompareKey(fields, wanted);
        if (comparison < 0)
        {
            if (end <= low)
            {
                reason = "OMS_GENERATION_COMMAND_INDEX_INVALID";
                return OmsGenerationLookupStatus::Error;
            }
            low = end;
            continue;
        }
        if (comparison > 0)
        {
            if (start >= high && high != 0)
            {
                reason = "OMS_GENERATION_COMMAND_INDEX_INVALID";
                return OmsGenerationLookupStatus::Error;
            }
            high = start;
            continue;
        }
        long orderId = -1;
        std::uint64_t sequence = 0;
        if (!GenerationDecodeHex(fields[0], record.agentId) ||
            !GenerationDecodeHex(fields[1], record.sessionId) ||
            !GenerationDecodeHex(fields[2], record.commandId) ||
            !GenerationDecodeHex(fields[3], record.requestHash) ||
            !GenerationParseSigned(fields[6], orderId) ||
            !GenerationDecodeHex(fields[7], record.reasonCode) ||
            !GenerationDecodeHex(fields[8], record.venueCorrelationId) ||
            !GenerationParseUnsigned(fields[9], sequence) ||
            !GenerationDecodeHex(fields[10], record.account) ||
            !GenerationDecodeHex(fields[11], record.executionDomain) ||
            (fields[12] != "0" && fields[12] != "1") ||
            record.agentId != agentId || record.sessionId != sessionId ||
            record.commandId != commandId ||
            (fields[4] != "place" && fields[4] != "cancel" &&
             fields[4] != "flatten") ||
            (fields[5] != "accepted" && fields[5] != "rejected" &&
             fields[5] != "uncertain"))
        {
            reason = "OMS_GENERATION_COMMAND_INDEX_INVALID";
            return OmsGenerationLookupStatus::Error;
        }
        record.operation = fields[4];
        record.status = fields[5];
        record.orderId = orderId;
        record.lastSequence = sequence;
        record.durableMutationIntent = fields[12] == "1";
        reason.clear();
        return OmsGenerationLookupStatus::Found;
    }
    reason.clear();
    return OmsGenerationLookupStatus::Missing;
}

bool OmsGenerationStore::ReadPlaceSendAttemptTimes(
    const std::string& account,
    const std::string& executionDomain,
    std::int64_t cutoffMs,
    const std::set<std::string>& excludedRequestKeys,
    std::vector<OmsGenerationSendAttempt>& attempts,
    std::string& reason) const
{
    attempts.clear();
    if (!m_active) return true;
    if (!ValidatePinnedIndex(m_sendIndexFd, "send-attempt-index.tsv",
            m_sendIndexIdentity))
    {
        reason = "OMS_GENERATION_SEND_INDEX_CHANGED";
        return false;
    }

    const std::string wantedAccount = GenerationHex(account);
    const std::string wantedDomain = GenerationHex(executionDomain);
    const auto parseRow = [](const std::vector<std::string>& fields,
                             std::string& rowAccount,
                             std::string& rowDomain,
                             std::int64_t& ts,
                             std::string& agent,
                             std::string& session,
                             std::string& command,
                             std::uint64_t& sequence) {
        return fields.size() == 7U &&
            GenerationDecodeHex(fields[0], rowAccount) &&
            GenerationDecodeHex(fields[1], rowDomain) &&
            GenerationParseSigned64(fields[2], ts) &&
            GenerationDecodeHex(fields[3], agent) &&
            GenerationDecodeHex(fields[4], session) &&
            GenerationDecodeHex(fields[5], command) &&
            GenerationParseUnsigned(fields[6], sequence);
    };

    off_t offset = 0;
    if (m_sendIndexWindowSorted && m_sendIndexIdentity.st_size > 0)
    {
        // The index is ordered by encoded account, encoded execution domain,
        // signed timestamp, durable sequence, then request identity. Locate the
        // first row in the requested account/domain with timestamp > cutoff;
        // normal rate checks therefore touch O(log history + window) rows.
        off_t low = 0;
        off_t high = m_sendIndexIdentity.st_size;
        while (low < high)
        {
            const off_t midpoint = low + (high - low) / 2;
            off_t start = 0, end = 0;
            std::string line;
            std::vector<std::string> fields;
            std::string rowAccount, rowDomain, agent, session, command;
            std::int64_t ts = 0;
            std::uint64_t sequence = 0;
            if (!GenerationReadLineContaining(m_sendIndexFd,
                    m_sendIndexIdentity.st_size, midpoint, start, end, line) ||
                !GenerationSplitTabs(line, fields) ||
                !parseRow(fields, rowAccount, rowDomain, ts,
                    agent, session, command, sequence))
            {
                reason = "OMS_GENERATION_SEND_INDEX_INVALID";
                return false;
            }
            const bool beforeWindow =
                fields[0] < wantedAccount ||
                (fields[0] == wantedAccount && fields[1] < wantedDomain) ||
                (fields[0] == wantedAccount && fields[1] == wantedDomain &&
                 ts <= cutoffMs);
            if (beforeWindow)
            {
                if (end <= low)
                {
                    reason = "OMS_GENERATION_SEND_INDEX_INVALID";
                    return false;
                }
                low = end;
            }
            else
            {
                if (start >= high && high != 0)
                {
                    reason = "OMS_GENERATION_SEND_INDEX_INVALID";
                    return false;
                }
                high = start;
            }
        }
        offset = low;
    }

    while (offset < m_sendIndexIdentity.st_size)
    {
        off_t start = 0, end = 0;
        std::string line;
        std::vector<std::string> fields;
        if (!GenerationReadLineContaining(m_sendIndexFd,
                m_sendIndexIdentity.st_size, offset, start, end, line) ||
            start != offset || end <= offset ||
            !GenerationSplitTabs(line, fields))
        {
            reason = "OMS_GENERATION_SEND_INDEX_INVALID";
            return false;
        }
        std::string rowAccount, rowDomain, agent, session, command;
        std::int64_t ts = 0;
        std::uint64_t sequence = 0;
        if (!parseRow(fields, rowAccount, rowDomain, ts,
                agent, session, command, sequence))
        {
            reason = "OMS_GENERATION_SEND_INDEX_INVALID";
            return false;
        }
        if (m_sendIndexWindowSorted)
        {
            if (fields[0] != wantedAccount || fields[1] != wantedDomain)
                break;
            if (ts <= cutoffMs)
            {
                reason = "OMS_GENERATION_SEND_INDEX_ORDER_INVALID";
                return false;
            }
        }
        if (rowAccount == account && rowDomain == executionDomain && ts > cutoffMs)
        {
            const std::string requestKey = GenerationRequestKey(agent, session, command);
            if (excludedRequestKeys.find(requestKey) == excludedRequestKeys.end())
            {
                OmsGenerationSendAttempt attempt;
                attempt.requestKey = requestKey;
                attempt.tsMs = ts;
                attempt.sequence = sequence;
                attempts.push_back(attempt);
            }
        }
        offset = end;
    }
    std::sort(attempts.begin(), attempts.end(),
        [](const OmsGenerationSendAttempt& left,
           const OmsGenerationSendAttempt& right) {
            return left.sequence < right.sequence;
        });
    reason.clear();
    return true;
}

bool OmsGenerationStore::EnumerateMutationRecords(
    const std::string& account,
    const std::string& executionDomain,
    std::vector<OmsGenerationMutationRecord>& records,
    std::string& reason) const
{
    records.clear();
    if (!m_active) return true;
    if (!ValidatePinnedIndex(m_commandIndexFd, "runtime-command-index.tsv",
            m_commandIndexIdentity))
    {
        reason = "OMS_GENERATION_COMMAND_INDEX_CHANGED";
        return false;
    }
    off_t offset = 0;
    while (offset < m_commandIndexIdentity.st_size)
    {
        off_t start = 0, end = 0;
        std::string line;
        std::vector<std::string> fields;
        if (!GenerationReadLineContaining(m_commandIndexFd,
                m_commandIndexIdentity.st_size, offset, start, end, line) ||
            start != offset || end <= offset ||
            !GenerationSplitTabs(line, fields) || fields.size() != 13U)
        {
            reason = "OMS_GENERATION_COMMAND_INDEX_INVALID";
            return false;
        }
        std::string rowAccount, rowDomain;
        if (!GenerationDecodeHex(fields[10], rowAccount) ||
            !GenerationDecodeHex(fields[11], rowDomain) ||
            (fields[12] != "0" && fields[12] != "1"))
        {
            reason = "OMS_GENERATION_COMMAND_INDEX_INVALID";
            return false;
        }
        if (fields[12] == "1" && rowAccount == account &&
            rowDomain == executionDomain)
        {
            OmsGenerationMutationRecord record;
            if (!GenerationDecodeHex(fields[0], record.agentId) ||
                !GenerationDecodeHex(fields[1], record.sessionId) ||
                !GenerationDecodeHex(fields[2], record.commandId) ||
                !GenerationDecodeHex(fields[8], record.venueCorrelationId) ||
                (fields[4] != "place" && fields[4] != "cancel" &&
                 fields[4] != "flatten"))
            {
                reason = "OMS_GENERATION_COMMAND_INDEX_INVALID";
                return false;
            }
            record.operation = fields[4];
            records.push_back(record);
        }
        offset = end;
    }
    reason.clear();
    return true;
}

bool OmsGenerationStore::SummarizeMutationRecords(
    const std::string& account,
    const std::string& executionDomain,
    OmsGenerationMutationSummary& summary,
    std::string& reason) const
{
    summary = OmsGenerationMutationSummary();
    if (!m_active)
    {
        summary.commandBindingSha256 =
            "sha256:e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855";
        summary.correlationBindingSha256 = summary.commandBindingSha256;
        reason.clear();
        return true;
    }
    if (!ValidatePinnedIndex(m_commandIndexFd, "runtime-command-index.tsv",
            m_commandIndexIdentity))
    {
        reason = "OMS_GENERATION_COMMAND_INDEX_CHANGED";
        return false;
    }

    EVP_MD_CTX* commandDigest = EVP_MD_CTX_new();
    EVP_MD_CTX* correlationDigest = EVP_MD_CTX_new();
    if (commandDigest == nullptr || correlationDigest == nullptr ||
        EVP_DigestInit_ex(commandDigest, EVP_sha256(), nullptr) != 1 ||
        EVP_DigestInit_ex(correlationDigest, EVP_sha256(), nullptr) != 1)
    {
        if (commandDigest != nullptr) EVP_MD_CTX_free(commandDigest);
        if (correlationDigest != nullptr) EVP_MD_CTX_free(correlationDigest);
        reason = "OMS_GENERATION_TERMINAL_DIGEST_FAILED";
        return false;
    }

    bool ok = true;
    off_t offset = 0;
    while (ok && offset < m_commandIndexIdentity.st_size)
    {
        off_t start = 0, end = 0;
        std::string line;
        std::vector<std::string> fields;
        if (!GenerationReadLineContaining(m_commandIndexFd,
                m_commandIndexIdentity.st_size, offset, start, end, line) ||
            start != offset || end <= offset ||
            !GenerationSplitTabs(line, fields) || fields.size() != 13U)
        {
            reason = "OMS_GENERATION_COMMAND_INDEX_INVALID";
            ok = false;
            break;
        }
        std::string rowAccount, rowDomain;
        if (!GenerationDecodeHex(fields[10], rowAccount) ||
            !GenerationDecodeHex(fields[11], rowDomain) ||
            (fields[12] != "0" && fields[12] != "1") ||
            (fields[4] != "place" && fields[4] != "cancel" &&
             fields[4] != "flatten"))
        {
            reason = "OMS_GENERATION_COMMAND_INDEX_INVALID";
            ok = false;
            break;
        }
        if (fields[12] == "1" && rowAccount == account &&
            rowDomain == executionDomain)
        {
            const std::string commandLine = std::string("command=") +
                fields[0] + "|" + fields[1] + "|" + fields[2] + "|" +
                fields[4] + "|" + fields[8] + "\n";
            ok = EVP_DigestUpdate(commandDigest, commandLine.data(),
                    commandLine.size()) == 1;
            if (!ok)
            {
                reason = "OMS_GENERATION_TERMINAL_DIGEST_FAILED";
                break;
            }
            if (summary.commandCount ==
                std::numeric_limits<std::uint64_t>::max())
            {
                reason = "OMS_GENERATION_TERMINAL_COUNT_OVERFLOW";
                ok = false;
                break;
            }
            ++summary.commandCount;
            if (!fields[8].empty())
            {
                const std::string correlationLine =
                    std::string("correlation-ref=") + fields[8] + "\n";
                ok = EVP_DigestUpdate(correlationDigest,
                        correlationLine.data(), correlationLine.size()) == 1;
                if (!ok)
                {
                    reason = "OMS_GENERATION_TERMINAL_DIGEST_FAILED";
                    break;
                }
                if (summary.correlationReferenceCount ==
                    std::numeric_limits<std::uint64_t>::max())
                {
                    reason = "OMS_GENERATION_TERMINAL_COUNT_OVERFLOW";
                    ok = false;
                    break;
                }
                ++summary.correlationReferenceCount;
            }
        }
        offset = end;
    }

    auto finish = [](EVP_MD_CTX* context, std::string& output) {
        unsigned char digest[EVP_MAX_MD_SIZE];
        unsigned int length = 0;
        if (EVP_DigestFinal_ex(context, digest, &length) != 1 || length != 32)
            return false;
        static const char digits[] = "0123456789abcdef";
        output = "sha256:";
        output.reserve(71);
        for (unsigned int i = 0; i < length; ++i)
        {
            output.push_back(digits[digest[i] >> 4]);
            output.push_back(digits[digest[i] & 15U]);
        }
        return true;
    };
    if (ok)
        ok = finish(commandDigest, summary.commandBindingSha256) &&
            finish(correlationDigest, summary.correlationBindingSha256);
    EVP_MD_CTX_free(commandDigest);
    EVP_MD_CTX_free(correlationDigest);
    if (!ok)
    {
        if (reason.empty()) reason = "OMS_GENERATION_TERMINAL_DIGEST_FAILED";
        summary = OmsGenerationMutationSummary();
        return false;
    }
    reason.clear();
    return true;
}

ExecutionCoordinator::RequestRecordStore::RequestRecordStore(
    OmsGenerationStore* generationStore)
    : m_generationStore(generationStore)
{
}

bool ExecutionCoordinator::RequestRecordStore::DecodeRequestKey(
    const std::string& key,
    std::string& agentId,
    std::string& sessionId,
    std::string& commandId)
{
    const char separator = '\x1f';
    const std::size_t first = key.find(separator);
    if (first == std::string::npos) return false;
    const std::size_t second = key.find(separator, first + 1U);
    if (second == std::string::npos ||
        key.find(separator, second + 1U) != std::string::npos)
        return false;
    agentId = key.substr(0, first);
    sessionId = key.substr(first + 1U, second - first - 1U);
    commandId = key.substr(second + 1U);
    return !agentId.empty() && !sessionId.empty() && !commandId.empty();
}

void ExecutionCoordinator::RequestRecordStore::RememberHistorical(
    const std::string& key)
{
    if (!m_historicalKeys.insert(key).second) return;
    m_historicalOrder.push_back(key);
    while (m_historicalKeys.size() > kHistoricalCommandCache &&
           !m_historicalOrder.empty())
    {
        const std::string oldest = m_historicalOrder.front();
        m_historicalOrder.pop_front();
        if (m_historicalKeys.erase(oldest) != 0)
            Base::erase(oldest);
    }
    if (m_historicalOrder.size() > kHistoricalCommandCache * 4U)
    {
        std::deque<std::string> compact;
        for (std::deque<std::string>::const_iterator it = m_historicalOrder.begin();
             it != m_historicalOrder.end(); ++it)
            if (m_historicalKeys.find(*it) != m_historicalKeys.end())
                compact.push_back(*it);
        m_historicalOrder.swap(compact);
    }
}

void ExecutionCoordinator::RequestRecordStore::Promote(const std::string& key)
{
    m_historicalKeys.erase(key);
}

ExecutionCoordinator::RequestRecordStore::Base::iterator
ExecutionCoordinator::RequestRecordStore::LoadHistorical(const std::string& key)
{
    Base::iterator existing = Base::find(key);
    if (existing != Base::end()) return existing;
    if (m_generationStore == nullptr || !m_generationStore->IsActive())
        return Base::end();

    std::string agentId, sessionId, commandId;
    if (!DecodeRequestKey(key, agentId, sessionId, commandId))
        return Base::end();

    OmsGenerationCommandRecord historical;
    std::string reason;
    const OmsGenerationLookupStatus status = m_generationStore->LookupCommand(
        agentId, sessionId, commandId, historical, reason);
    if (status == OmsGenerationLookupStatus::Missing)
        return Base::end();

    RequestRecord record;
    record.context.agentId = agentId;
    record.context.sessionId = sessionId;
    record.context.toolCallId = commandId;
    record.durableMutationIntent = true;
    if (status == OmsGenerationLookupStatus::Error)
    {
        record.status = ExecutionCommandStatus::Uncertain;
        record.reasonCode = "OMS_GENERATION_INDEX_FAILED";
        record.detail = reason.empty() ?
            "permanent command index is unavailable" : reason;
    }
    else
    {
        record.status = HistoricalStatus(historical.status);
        record.orderId = historical.orderId;
        record.reasonCode = historical.reasonCode;
        record.requestHash = historical.requestHash;
        record.venueCorrelationId = historical.venueCorrelationId;
        record.operation = historical.operation;
        record.context.account = historical.account;
        record.context.executionDomain = historical.executionDomain;
        record.durableMutationIntent = historical.durableMutationIntent;
    }
    const std::pair<Base::iterator, bool> inserted =
        Base::insert(std::make_pair(key, record));
    RememberHistorical(key);
    return inserted.first;
}

ExecutionCoordinator::RequestRecordStore::Base::iterator
ExecutionCoordinator::RequestRecordStore::find(const std::string& key)
{
    Base::iterator existing = Base::find(key);
    return existing != Base::end() ? existing : LoadHistorical(key);
}

ExecutionCoordinator::RequestRecordStore::Base::const_iterator
ExecutionCoordinator::RequestRecordStore::find(const std::string& key) const
{
    Base::const_iterator existing = Base::find(key);
    if (existing != Base::end()) return existing;
    RequestRecordStore* self = const_cast<RequestRecordStore*>(this);
    const Base::iterator loaded = self->LoadHistorical(key);
    return loaded == self->Base::end() ? Base::end() : loaded;
}

ExecutionCoordinator::RequestRecord&
ExecutionCoordinator::RequestRecordStore::operator[](const std::string& key)
{
    Base::iterator existing = find(key);
    if (existing != Base::end())
    {
        Promote(key);
        return existing->second;
    }
    return Base::operator[](key);
}

void ExecutionCoordinator::RequestRecordStore::clear()
{
    Base::clear();
    m_historicalOrder.clear();
    m_historicalKeys.clear();
}

std::size_t ExecutionCoordinator::RequestRecordStore::HotSize() const
{
    return Base::size() >= m_historicalKeys.size() ?
        Base::size() - m_historicalKeys.size() : 0U;
}

bool ExecutionCoordinator::EnterPaperTerminalFenceAndProjectGenerationAwareLocked(
    const PaperTerminalFenceBinding& binding,
    PaperTerminalMutationUniverse& universe,
    std::string& reason)
{
    universe = PaperTerminalMutationUniverse();
    if (m_mutationBlocked)
    {
        if (m_mutationBlockReason != "IB_PAPER_TERMINAL_HALTED" ||
            !m_paperTerminalFencePresent ||
            !SamePaperTerminalFenceBinding(m_paperTerminalFenceBinding, binding))
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
        const std::string encoded = EncodePaperTerminalFenceBinding(binding);
        if (encoded.empty())
        {
            reason = "IB_PAPER_TERMINAL_FENCE_BINDING_INVALID";
            return false;
        }
        const OmsJournalEvent event = BuildEvent(
            journalContext, "paper_terminal_fence", -1, "", "", 0.0, 0.0,
            std::to_string(binding.recoveryIngressFence), encoded,
            "IB_PAPER_TERMINAL_HALTED", binding.preliminaryReceiptSha256,
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

    OmsGenerationMutationSummary sealed;
    if (!m_generationStore.SummarizeMutationRecords(
            binding.owner.account, binding.owner.executionDomain,
            sealed, reason))
        return false;

    std::vector<PaperTerminalMutationRecord> activeTail;
    for (RequestRecordStore::Base::const_iterator it = m_requests.begin();
         it != m_requests.end(); ++it)
    {
        const RequestRecord& request = it->second;
        if (!request.durableMutationIntent ||
            request.context.account != binding.owner.account ||
            request.context.executionDomain != binding.owner.executionDomain)
            continue;
        OmsGenerationCommandRecord historical;
        std::string lookupReason;
        const OmsGenerationLookupStatus lookup = m_generationStore.LookupCommand(
            request.context.agentId, request.context.sessionId,
            request.context.toolCallId, historical, lookupReason);
        if (lookup == OmsGenerationLookupStatus::Error)
        {
            reason = lookupReason.empty() ?
                "OMS_GENERATION_COMMAND_INDEX_FAILED" : lookupReason;
            return false;
        }
        if (lookup == OmsGenerationLookupStatus::Found)
            continue; // already sealed into the fixed-size history binding
        PaperTerminalMutationRecord record;
        record.agentId = request.context.agentId;
        record.sessionId = request.context.sessionId;
        record.toolCallId = request.context.toolCallId;
        record.operation = request.operation;
        record.venueCorrelationId = request.venueCorrelationId;
        activeTail.push_back(record);
    }
    return BuildPaperTerminalPartitionedUniverse(
        sealed.commandCount, sealed.commandBindingSha256,
        sealed.correlationReferenceCount, sealed.correlationBindingSha256,
        activeTail, universe, reason);
}

// Capacity bridge compiled after execution_generation_support.inc so it can
// reuse the same private generation identity helpers. This is not another
// translation unit or authority path.

bool OmsGenerationStore::RecoveryCapacityGenerationV1(
    std::uint64_t& bytes,
    std::uint64_t& records,
    std::string& reason) const
{
    bytes = 0;
    records = 0;
    if (!m_active || m_generationFd < 0)
    {
        reason = "OMS_GENERATION_NOT_ACTIVE";
        return false;
    }

    struct stat hot;
    if (::fstatat(m_generationFd, "hot-replay.jsonl", &hot,
            AT_SYMLINK_NOFOLLOW) != 0 || !GenerationPrivateFile(hot) ||
        hot.st_size < 0)
    {
        reason = "OMS_GENERATION_HOT_REPLAY_CHANGED";
        return false;
    }

    const int journalFd = ::open(m_journalPath.c_str(),
        O_RDONLY | O_CLOEXEC | O_NOFOLLOW);
    struct stat before, named;
    if (journalFd < 0 || ::fstat(journalFd, &before) != 0 ||
        !GenerationPrivateFile(before) || before.st_size < 0 ||
        static_cast<std::uint64_t>(before.st_size) < m_journalPrefixBytes ||
        ::lstat(m_journalPath.c_str(), &named) != 0 ||
        !GenerationSameIdentity(before, named))
    {
        if (journalFd >= 0) ::close(journalFd);
        reason = "OMS_GENERATION_ACTIVE_JOURNAL_CHANGED";
        return false;
    }

    std::uint64_t tailRecords = 0;
    off_t offset = static_cast<off_t>(m_journalPrefixBytes);
    std::array<char, 64U * 1024U> buffer;
    bool ok = true;
    while (offset < before.st_size)
    {
        const std::size_t wanted = static_cast<std::size_t>(std::min<off_t>(
            static_cast<off_t>(buffer.size()), before.st_size - offset));
        ssize_t count;
        do
        {
            count = ::pread(journalFd, buffer.data(), wanted, offset);
        } while (count < 0 && errno == EINTR);
        if (count <= 0) { ok = false; break; }
        for (ssize_t i = 0; i < count; ++i)
            if (buffer[static_cast<std::size_t>(i)] == '\n')
            {
                if (tailRecords == std::numeric_limits<std::uint64_t>::max())
                { ok = false; break; }
                ++tailRecords;
            }
        if (!ok) break;
        offset += count;
    }
    struct stat after, namedAfter;
    ok = ok && ::fstat(journalFd, &after) == 0 &&
        ::lstat(m_journalPath.c_str(), &namedAfter) == 0 &&
        GenerationSameIdentity(before, after) && GenerationSameIdentity(after, namedAfter);
    if (::close(journalFd) != 0) ok = false;
    if (!ok)
    {
        reason = "OMS_GENERATION_CAPACITY_SCAN_FAILED";
        return false;
    }

    const std::uint64_t hotBytes = static_cast<std::uint64_t>(hot.st_size);
    const std::uint64_t tailBytes = static_cast<std::uint64_t>(before.st_size) -
        m_journalPrefixBytes;
    if (hotBytes > std::numeric_limits<std::uint64_t>::max() - tailBytes ||
        m_hotReplayRecords > std::numeric_limits<std::uint64_t>::max() - tailRecords)
    {
        reason = "OMS_GENERATION_CAPACITY_OVERFLOW";
        return false;
    }
    bytes = hotBytes + tailBytes;
    records = m_hotReplayRecords + tailRecords;
    reason.clear();
    return true;
}

void OmsJournal::AdoptValidatedIncrementalRecoveryCapacity(
    std::uint64_t decodedBytes,
    std::uint64_t records)
{
    std::lock_guard<std::mutex> lock(m_mtx);
    m_capacityKnown = true;
    m_capacityBytes = decodedBytes;
    m_capacityRecords = records;
    m_replayObservedBytes = decodedBytes > std::numeric_limits<std::size_t>::max() ?
        std::numeric_limits<std::size_t>::max() : static_cast<std::size_t>(decodedBytes);
    m_replayValidatedRecords = records > std::numeric_limits<std::size_t>::max() ?
        std::numeric_limits<std::size_t>::max() : static_cast<std::size_t>(records);
    m_replayReasonCode = "OMS_GENERATION_INCREMENTAL_RECOVERY";
}

// V2 compatibility layer for lineage-sealed OMS generations.
//
// execution_generation_support.inc remains the exact V1 implementation. The
// owning translation unit compiles it under private *GenerationV1 method names,
// then this file provides the public Prepare/Recover/RecoveryCapacity dispatch.
// All lookup/index methods stay shared because V1 and V2 intentionally use the
// same cumulative full-key runtime index formats.

namespace
{
const char* const kRuntimeManifestHeaderV2 = "HEPTA_OMS_RUNTIME_GENERATION_V2";
const std::uint64_t kMaximumActiveTailHeaderBytes = 512U;

struct GenerationReplayNode
{
    std::string generation;
    std::string parentGeneration;
    std::string parentManifestSha256;
    std::string segmentSha256;
    std::uint64_t segmentRecords = 0;
};

bool GenerationCanonicalJsonString(const std::string& json,
                                   const std::string& key,
                                   std::string& value)
{
    const std::string needle = "\"" + key + "\":\"";
    const std::size_t found = json.find(needle);
    if (found == std::string::npos ||
        json.find(needle, found + needle.size()) != std::string::npos)
        return false;
    const std::size_t begin = found + needle.size();
    const std::size_t end = json.find('"', begin);
    if (end == std::string::npos || end == begin) return false;
    value.assign(json, begin, end - begin);
    return value.find('\\') == std::string::npos &&
        value.find_first_of("\r\n") == std::string::npos;
}

bool GenerationLoadReplayNode(int storeFd,
                              const std::string& generation,
                              const std::string& expectedManifestSha256,
                              GenerationReplayNode& node,
                              std::string& reason)
{
    if (!GenerationSafeName(generation) ||
        !GenerationHexDigest(expectedManifestSha256))
    {
        reason = "OMS_GENERATION_HISTORY_BINDING_INVALID";
        return false;
    }
    const int generationFd = ::openat(storeFd, generation.c_str(),
        O_RDONLY | O_DIRECTORY | O_CLOEXEC | O_NOFOLLOW);
    struct stat directoryMetadata;
    if (generationFd < 0 || ::fstat(generationFd, &directoryMetadata) != 0 ||
        !GenerationPrivateDirectory(directoryMetadata))
    {
        if (generationFd >= 0) ::close(generationFd);
        reason = "OMS_GENERATION_HISTORY_DIRECTORY_UNSAFE";
        return false;
    }

    std::string manifestJson, runtimeManifest, runtimeManifestSha256;
    const bool authorityOk =
        GenerationVerifyHashedFileAt(generationFd, "manifest.json",
            expectedManifestSha256) &&
        GenerationReadPrivateFileAt(generationFd, "manifest.json",
            kMaximumGenerationMetadataBytes, manifestJson) &&
        GenerationCanonicalJsonString(manifestJson,
            "runtime_manifest_sha256", runtimeManifestSha256) &&
        GenerationHexDigest(runtimeManifestSha256) &&
        GenerationReadPrivateFileAt(generationFd, "runtime-manifest.txt",
            kMaximumGenerationMetadataBytes, runtimeManifest) &&
        GenerationSha256(runtimeManifest.data(), runtimeManifest.size()) ==
            runtimeManifestSha256;
    if (!authorityOk)
    {
        ::close(generationFd);
        reason = "OMS_GENERATION_HISTORY_MANIFEST_INVALID";
        return false;
    }

    GenerationFields fields;
    const bool v2 = GenerationParseFields(
        runtimeManifest, kRuntimeManifestHeaderV2, fields);
    const bool v1 = !v2 && GenerationParseFields(
        runtimeManifest, kRuntimeManifestHeader, fields);
    if ((!v1 && !v2) || fields["generation"] != generation ||
        !GenerationHexDigest(fields["segment_sha256"]))
    {
        ::close(generationFd);
        reason = "OMS_GENERATION_HISTORY_RUNTIME_MANIFEST_INVALID";
        return false;
    }
    std::uint64_t segmentRecords = 0;
    const char* countField = v2 ? "segment_records" : "journal_records";
    if (!GenerationParseUnsigned(fields[countField], segmentRecords) ||
        !GenerationVerifyHashedFileAt(generationFd,
            "segment-000001.jsonl", fields["segment_sha256"]))
    {
        ::close(generationFd);
        reason = "OMS_GENERATION_HISTORY_SEGMENT_INVALID";
        return false;
    }

    node = GenerationReplayNode();
    node.generation = generation;
    node.segmentSha256 = fields["segment_sha256"];
    node.segmentRecords = segmentRecords;
    if (v2 && fields["parent_generation"] != "-")
    {
        if (!GenerationSafeName(fields["parent_generation"]) ||
            !GenerationHexDigest(fields["parent_manifest_sha256"]))
        {
            ::close(generationFd);
            reason = "OMS_GENERATION_HISTORY_PARENT_INVALID";
            return false;
        }
        node.parentGeneration = fields["parent_generation"];
        node.parentManifestSha256 = fields["parent_manifest_sha256"];
    }
    if (::close(generationFd) != 0)
    {
        reason = "OMS_GENERATION_HISTORY_DIRECTORY_CLOSE_FAILED";
        return false;
    }
    reason.clear();
    return true;
}
}

bool OmsGenerationStore::Prepare(std::string& reason)
{
    m_segmentedTail = false;
    std::string v1Reason;
    if (PrepareGenerationV1(v1Reason))
    {
        m_sendIndexWindowSorted = true;
        reason.clear();
        return true;
    }

    // A V1 failure is never treated as absence. Try V2 only against the exact
    // same CURRENT selection; any malformed/corrupt state still fails closed.
    Close();
    m_segmentedTail = false;
    m_storeFd = ::open(m_storePath.c_str(),
        O_RDONLY | O_DIRECTORY | O_CLOEXEC | O_NOFOLLOW);
    struct stat storeMetadata;
    if (m_storeFd < 0 || ::fstat(m_storeFd, &storeMetadata) != 0 ||
        !GenerationPrivateDirectory(storeMetadata))
    {
        reason = "OMS_GENERATION_STORE_UNSAFE";
        Close();
        return false;
    }

    std::string runtimeCurrent, current;
    if (!GenerationReadPrivateFileAt(m_storeFd, "CURRENT.runtime",
            kMaximumGenerationMetadataBytes, runtimeCurrent) ||
        !GenerationReadPrivateFileAt(m_storeFd, "CURRENT",
            kMaximumGenerationMetadataBytes, current))
    {
        reason = "OMS_GENERATION_CURRENT_UNSAFE";
        Close();
        return false;
    }
    GenerationFields selected;
    static const char* const currentNames[] = {
        "generation", "current_sha256", "manifest_sha256",
        "runtime_manifest_sha256"
    };
    if (!GenerationParseFields(runtimeCurrent, kRuntimeCurrentHeader, selected) ||
        !GenerationExactFieldNames(selected, currentNames,
            sizeof(currentNames) / sizeof(currentNames[0])) ||
        !GenerationSafeName(selected["generation"]) ||
        !GenerationHexDigest(selected["current_sha256"]) ||
        !GenerationHexDigest(selected["manifest_sha256"]) ||
        !GenerationHexDigest(selected["runtime_manifest_sha256"]) ||
        GenerationSha256(current.data(), current.size()) != selected["current_sha256"])
    {
        reason = "OMS_GENERATION_RUNTIME_CURRENT_INVALID";
        Close();
        return false;
    }
    const std::string expectedCurrent =
        std::string("{\"generation\":\"") + selected["generation"] +
        "\",\"manifest_sha256\":\"" + selected["manifest_sha256"] +
        "\",\"runtime_manifest_sha256\":\"" + selected["runtime_manifest_sha256"] +
        "\",\"schema\":\"heptatrader.oms-current.v1\"}\n";
    if (current != expectedCurrent)
    {
        reason = "OMS_GENERATION_CURRENT_BINDING_INVALID";
        Close();
        return false;
    }

    m_generation = selected["generation"];
    m_generationFd = ::openat(m_storeFd, m_generation.c_str(),
        O_RDONLY | O_DIRECTORY | O_CLOEXEC | O_NOFOLLOW);
    struct stat generationMetadata;
    if (m_generationFd < 0 || ::fstat(m_generationFd, &generationMetadata) != 0 ||
        !GenerationPrivateDirectory(generationMetadata))
    {
        reason = "OMS_GENERATION_DIRECTORY_UNSAFE";
        Close();
        return false;
    }
    if (!GenerationVerifyHashedFileAt(m_generationFd, "manifest.json",
            selected["manifest_sha256"]))
    {
        reason = "OMS_GENERATION_MANIFEST_DIGEST_MISMATCH";
        Close();
        return false;
    }

    std::string runtimeManifest;
    if (!GenerationReadPrivateFileAt(m_generationFd, "runtime-manifest.txt",
            kMaximumGenerationMetadataBytes, runtimeManifest) ||
        GenerationSha256(runtimeManifest.data(), runtimeManifest.size()) !=
            selected["runtime_manifest_sha256"])
    {
        reason = "OMS_GENERATION_RUNTIME_MANIFEST_DIGEST_MISMATCH";
        Close();
        return false;
    }

    GenerationFields manifest;
    static const char* const manifestNamesLegacy[] = {
        "generation", "parent_generation", "parent_manifest_sha256",
        "history_records", "segment_records", "command_records",
        "send_attempt_records", "hot_replay_records", "segment_sha256",
        "checkpoint_sha256", "command_index_sha256",
        "runtime_command_index_sha256", "send_attempt_index_sha256",
        "hot_replay_sha256", "active_tail_header_bytes",
        "active_tail_header_sha256", "authorization_effect",
        "paper_authorized", "live_authorized"
    };
    static const char* const manifestNamesSorted[] = {
        "generation", "parent_generation", "parent_manifest_sha256",
        "history_records", "segment_records", "command_records",
        "send_attempt_records", "hot_replay_records", "segment_sha256",
        "checkpoint_sha256", "command_index_sha256",
        "runtime_command_index_sha256", "send_attempt_index_sha256",
        "send_attempt_index_order", "hot_replay_sha256",
        "active_tail_header_bytes", "active_tail_header_sha256",
        "authorization_effect", "paper_authorized", "live_authorized"
    };
    std::uint64_t historyRecords = 0, segmentRecords = 0;
    if (!GenerationParseFields(runtimeManifest, kRuntimeManifestHeaderV2, manifest))
    {
        reason = "OMS_GENERATION_RUNTIME_MANIFEST_V2_INVALID";
        Close();
        return false;
    }
    const bool sortedSendIndex = GenerationExactFieldNames(
        manifest, manifestNamesSorted,
        sizeof(manifestNamesSorted) / sizeof(manifestNamesSorted[0]));
    const bool legacySendIndex = GenerationExactFieldNames(
        manifest, manifestNamesLegacy,
        sizeof(manifestNamesLegacy) / sizeof(manifestNamesLegacy[0]));
    if ((!sortedSendIndex && !legacySendIndex) ||
        (sortedSendIndex &&
         manifest["send_attempt_index_order"] != "account-domain-time-v1") ||
        manifest["generation"] != m_generation ||
        (manifest["parent_generation"] != "-" &&
         !GenerationSafeName(manifest["parent_generation"])) ||
        ((manifest["parent_generation"] == "-") !=
         (manifest["parent_manifest_sha256"] == "-")) ||
        (manifest["parent_generation"] != "-" &&
         !GenerationHexDigest(manifest["parent_manifest_sha256"])) ||
        !GenerationParseUnsigned(manifest["history_records"], historyRecords) ||
        !GenerationParseUnsigned(manifest["segment_records"], segmentRecords) ||
        segmentRecords > historyRecords ||
        !GenerationParseUnsigned(manifest["command_records"], m_commandRecords) ||
        !GenerationParseUnsigned(manifest["send_attempt_records"], m_sendAttemptRecords) ||
        !GenerationParseUnsigned(manifest["hot_replay_records"], m_hotReplayRecords) ||
        !GenerationParseUnsigned(manifest["active_tail_header_bytes"], m_journalPrefixBytes) ||
        m_journalPrefixBytes == 0 || m_journalPrefixBytes > kMaximumActiveTailHeaderBytes ||
        !GenerationHexDigest(manifest["active_tail_header_sha256"]) ||
        !GenerationHexDigest(manifest["segment_sha256"]) ||
        !GenerationHexDigest(manifest["checkpoint_sha256"]) ||
        !GenerationHexDigest(manifest["command_index_sha256"]) ||
        !GenerationHexDigest(manifest["runtime_command_index_sha256"]) ||
        !GenerationHexDigest(manifest["send_attempt_index_sha256"]) ||
        !GenerationHexDigest(manifest["hot_replay_sha256"]) ||
        manifest["authorization_effect"] != "NONE" ||
        manifest["paper_authorized"] != "0" || manifest["live_authorized"] != "0")
    {
        reason = "OMS_GENERATION_RUNTIME_MANIFEST_V2_INVALID";
        Close();
        return false;
    }
    m_journalPrefixSha256 = manifest["active_tail_header_sha256"];
    m_sendIndexWindowSorted = sortedSendIndex;

    if (!GenerationVerifyHashedFileAt(m_generationFd, "segment-000001.jsonl",
            manifest["segment_sha256"]) ||
        !GenerationVerifyHashedFileAt(m_generationFd, "checkpoint.json",
            manifest["checkpoint_sha256"]) ||
        !GenerationVerifyHashedFileAt(m_generationFd, "command-index.tsv",
            manifest["command_index_sha256"]))
    {
        reason = "OMS_GENERATION_RUNTIME_FILE_DIGEST_MISMATCH";
        Close();
        return false;
    }
    if (!GenerationOpenHashedFileAt(m_generationFd, "runtime-command-index.tsv",
            manifest["runtime_command_index_sha256"],
            m_commandIndexFd, m_commandIndexIdentity) ||
        !GenerationOpenHashedFileAt(m_generationFd, "send-attempt-index.tsv",
            manifest["send_attempt_index_sha256"],
            m_sendIndexFd, m_sendIndexIdentity) ||
        !GenerationVerifyHashedFileAt(m_generationFd, "hot-replay.jsonl",
            manifest["hot_replay_sha256"]))
    {
        reason = "OMS_GENERATION_RUNTIME_INDEX_DIGEST_MISMATCH";
        Close();
        return false;
    }

    if (manifest["parent_generation"] != "-")
    {
        const int parentFd = ::openat(m_storeFd, manifest["parent_generation"].c_str(),
            O_RDONLY | O_DIRECTORY | O_CLOEXEC | O_NOFOLLOW);
        struct stat parentMetadata;
        const bool parentOk = parentFd >= 0 &&
            ::fstat(parentFd, &parentMetadata) == 0 &&
            GenerationPrivateDirectory(parentMetadata) &&
            GenerationVerifyHashedFileAt(parentFd, "manifest.json",
                manifest["parent_manifest_sha256"]);
        if (parentFd >= 0) ::close(parentFd);
        if (!parentOk)
        {
            reason = "OMS_GENERATION_PARENT_BINDING_INVALID";
            Close();
            return false;
        }
    }

    m_segmentedTail = true;
    reason.clear();
    return true;
}

bool OmsGenerationStore::Recover(
    std::size_t maxTailBytes,
    std::size_t maxTailRecords,
    std::size_t maxRecordBytes,
    const std::function<void(const OmsJournalEvent&)>& onEvent,
    std::string& reason)
{
    if (!Prepare(reason)) return false;
    if (!m_segmentedTail)
    {
        // Re-run the unchanged V1 path so all of its prefix/alignment/identity
        // checks remain exactly authoritative.
        Close();
        m_segmentedTail = false;
        return RecoverGenerationV1(
            maxTailBytes, maxTailRecords, maxRecordBytes, onEvent, reason);
    }

    const int hotFd = ::openat(m_generationFd, "hot-replay.jsonl",
        O_RDONLY | O_CLOEXEC | O_NOFOLLOW);
    struct stat hotIdentity;
    bool ok = hotFd >= 0 && ::fstat(hotFd, &hotIdentity) == 0 &&
        GenerationPrivateFile(hotIdentity) && hotIdentity.st_size >= 0 &&
        static_cast<std::uint64_t>(hotIdentity.st_size) <= maxTailBytes;
    std::size_t hotRecords = 0;
    if (ok)
        ok = GenerationReplayRange(hotFd, 0, hotIdentity.st_size,
            maxTailBytes, maxTailRecords, maxRecordBytes, onEvent, hotRecords) &&
            hotRecords == m_hotReplayRecords;
    if (hotFd >= 0) ::close(hotFd);
    if (!ok)
    {
        reason = "OMS_GENERATION_HOT_REPLAY_FAILED";
        Close();
        return false;
    }

    const int journalFd = ::open(m_journalPath.c_str(),
        O_RDONLY | O_CLOEXEC | O_NOFOLLOW);
    struct stat before, named;
    if (journalFd < 0 || ::fstat(journalFd, &before) != 0 ||
        !GenerationPrivateFile(before) || before.st_size < 0 ||
        static_cast<std::uint64_t>(before.st_size) < m_journalPrefixBytes ||
        ::lstat(m_journalPath.c_str(), &named) != 0 ||
        !GenerationSameIdentity(before, named))
    {
        if (journalFd >= 0) ::close(journalFd);
        reason = "OMS_GENERATION_ACTIVE_TAIL_UNSAFE";
        Close();
        return false;
    }
    std::string headerDigest;
    if (!GenerationHashFd(journalFd, static_cast<off_t>(m_journalPrefixBytes),
            headerDigest) || headerDigest != m_journalPrefixSha256)
    {
        ::close(journalFd);
        reason = "OMS_GENERATION_ACTIVE_TAIL_LINEAGE_MISMATCH";
        Close();
        return false;
    }
    char newline = 0;
    ssize_t count;
    do
    {
        count = ::pread(journalFd, &newline, 1,
            static_cast<off_t>(m_journalPrefixBytes - 1U));
    } while (count < 0 && errno == EINTR);
    if (count != 1 || newline != '\n')
    {
        ::close(journalFd);
        reason = "OMS_GENERATION_ACTIVE_TAIL_HEADER_INVALID";
        Close();
        return false;
    }

    std::size_t tailRecords = 0;
    ok = GenerationReplayRange(journalFd,
        static_cast<off_t>(m_journalPrefixBytes), before.st_size,
        maxTailBytes, maxTailRecords, maxRecordBytes, onEvent, tailRecords);
    struct stat after, namedAfter;
    ok = ok && ::fstat(journalFd, &after) == 0 &&
        ::lstat(m_journalPath.c_str(), &namedAfter) == 0 &&
        GenerationSameIdentity(before, after) && GenerationSameIdentity(after, namedAfter);
    if (::close(journalFd) != 0) ok = false;
    if (!ok)
    {
        reason = "OMS_GENERATION_TAIL_REPLAY_FAILED";
        Close();
        return false;
    }
    m_active = true;
    reason.clear();
    return true;
}

bool OmsGenerationStore::ReplayCompleteHistory(
    std::size_t maxSegmentBytes,
    std::size_t maxSegmentRecords,
    std::size_t maxRecordBytes,
    const std::function<void(const OmsJournalEvent&)>& onEvent,
    std::uint64_t& records,
    std::string& reason)
{
    records = 0;
    if (!HasStore() || !onEvent || maxSegmentBytes == 0 ||
        maxSegmentRecords == 0 || maxRecordBytes == 0)
    {
        reason = HasStore() ? "OMS_GENERATION_HISTORY_ARGUMENT_INVALID" :
            "OMS_GENERATION_STORE_ABSENT";
        return false;
    }
    if (!Recover(maxSegmentBytes, maxSegmentRecords, maxRecordBytes,
            [](const OmsJournalEvent&) {}, reason))
        return false;

    std::string runtimeCurrent;
    GenerationFields selected;
    static const char* const currentNames[] = {
        "generation", "current_sha256", "manifest_sha256",
        "runtime_manifest_sha256"
    };
    if (!GenerationReadPrivateFileAt(m_storeFd, "CURRENT.runtime",
            kMaximumGenerationMetadataBytes, runtimeCurrent) ||
        !GenerationParseFields(runtimeCurrent, kRuntimeCurrentHeader, selected) ||
        !GenerationExactFieldNames(selected, currentNames,
            sizeof(currentNames) / sizeof(currentNames[0])) ||
        selected["generation"] != m_generation ||
        !GenerationHexDigest(selected["manifest_sha256"]))
    {
        reason = "OMS_GENERATION_HISTORY_CURRENT_INVALID";
        return false;
    }

    std::vector<GenerationReplayNode> reverseChain;
    std::set<std::string> seen;
    std::string generation = m_generation;
    std::string manifestSha256 = selected["manifest_sha256"];
    for (;;)
    {
        if (!seen.insert(generation).second)
        {
            reason = "OMS_GENERATION_HISTORY_CYCLE";
            return false;
        }
        GenerationReplayNode node;
        if (!GenerationLoadReplayNode(m_storeFd, generation,
                manifestSha256, node, reason))
            return false;
        reverseChain.push_back(node);
        if (node.parentGeneration.empty()) break;
        generation = node.parentGeneration;
        manifestSha256 = node.parentManifestSha256;
    }
    std::reverse(reverseChain.begin(), reverseChain.end());

    for (std::vector<GenerationReplayNode>::const_iterator it =
             reverseChain.begin(); it != reverseChain.end(); ++it)
    {
        const int generationFd = ::openat(m_storeFd, it->generation.c_str(),
            O_RDONLY | O_DIRECTORY | O_CLOEXEC | O_NOFOLLOW);
        struct stat directoryMetadata;
        if (generationFd < 0 || ::fstat(generationFd, &directoryMetadata) != 0 ||
            !GenerationPrivateDirectory(directoryMetadata))
        {
            if (generationFd >= 0) ::close(generationFd);
            reason = "OMS_GENERATION_HISTORY_DIRECTORY_CHANGED";
            return false;
        }
        int segmentFd = -1;
        struct stat segmentIdentity;
        if (!GenerationOpenHashedFileAt(generationFd,
                "segment-000001.jsonl", it->segmentSha256,
                segmentFd, segmentIdentity))
        {
            ::close(generationFd);
            reason = "OMS_GENERATION_HISTORY_SEGMENT_CHANGED";
            return false;
        }
        std::size_t segmentRecords = 0;
        const bool replayed = GenerationReplayRange(segmentFd, 0,
            segmentIdentity.st_size, maxSegmentBytes, maxSegmentRecords,
            maxRecordBytes, onEvent, segmentRecords) &&
            segmentRecords == it->segmentRecords;
        const bool closedSegment = ::close(segmentFd) == 0;
        const bool closedDirectory = ::close(generationFd) == 0;
        if (!replayed || !closedSegment || !closedDirectory)
        {
            reason = "OMS_GENERATION_HISTORY_SEGMENT_REPLAY_FAILED";
            return false;
        }
        if (records > std::numeric_limits<std::uint64_t>::max() - segmentRecords)
        {
            reason = "OMS_GENERATION_HISTORY_RECORD_OVERFLOW";
            return false;
        }
        records += segmentRecords;
    }

    const int journalFd = ::open(m_journalPath.c_str(),
        O_RDONLY | O_CLOEXEC | O_NOFOLLOW);
    struct stat before, named;
    if (journalFd < 0 || ::fstat(journalFd, &before) != 0 ||
        !GenerationPrivateFile(before) || before.st_size < 0 ||
        static_cast<std::uint64_t>(before.st_size) < m_journalPrefixBytes ||
        ::lstat(m_journalPath.c_str(), &named) != 0 ||
        !GenerationSameIdentity(before, named))
    {
        if (journalFd >= 0) ::close(journalFd);
        reason = "OMS_GENERATION_HISTORY_ACTIVE_JOURNAL_CHANGED";
        return false;
    }
    std::string prefixDigest;
    if (!GenerationHashFd(journalFd,
            static_cast<off_t>(m_journalPrefixBytes), prefixDigest) ||
        prefixDigest != m_journalPrefixSha256)
    {
        ::close(journalFd);
        reason = "OMS_GENERATION_HISTORY_ACTIVE_PREFIX_MISMATCH";
        return false;
    }
    std::size_t tailRecords = 0;
    bool replayedTail = GenerationReplayRange(journalFd,
        static_cast<off_t>(m_journalPrefixBytes), before.st_size,
        maxSegmentBytes, maxSegmentRecords, maxRecordBytes,
        onEvent, tailRecords);
    struct stat after, namedAfter;
    replayedTail = replayedTail && ::fstat(journalFd, &after) == 0 &&
        ::lstat(m_journalPath.c_str(), &namedAfter) == 0 &&
        GenerationSameIdentity(before, after) &&
        GenerationSameIdentity(after, namedAfter);
    if (::close(journalFd) != 0) replayedTail = false;
    if (!replayedTail)
    {
        reason = "OMS_GENERATION_HISTORY_ACTIVE_TAIL_REPLAY_FAILED";
        return false;
    }
    if (records > std::numeric_limits<std::uint64_t>::max() - tailRecords)
    {
        reason = "OMS_GENERATION_HISTORY_RECORD_OVERFLOW";
        return false;
    }
    records += tailRecords;
    reason.clear();
    return true;
}

bool OmsGenerationStore::RecoveryCapacity(
    std::uint64_t& bytes,
    std::uint64_t& records,
    std::string& reason) const
{
    // Both formats represent their replay start in m_journalPrefixBytes:
    // full immutable prefix for V1; lineage sentinel for V2. The retained V1
    // scanner therefore measures the correct bounded hot+tail working set for
    // either format after Recover has already verified the prefix/sentinel.
    return RecoveryCapacityGenerationV1(bytes, records, reason);
}
