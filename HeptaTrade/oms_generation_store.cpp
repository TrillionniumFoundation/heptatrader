#include "oms_generation_store.h"

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
#include <sys/stat.h>
#include <unistd.h>

namespace
{
const std::size_t kMaximumMetadataBytes = 1024U * 1024U;
const std::size_t kMaximumIndexLineBytes = 64U * 1024U;
const std::size_t kMaximumTerminalMutationRecords = 4097U;
const char* const kRuntimeCurrentHeader = "HEPTA_OMS_RUNTIME_CURRENT_V1";
const char* const kRuntimeManifestHeader = "HEPTA_OMS_RUNTIME_GENERATION_V1";

typedef std::map<std::string, std::string> Fields;

bool PrivateDirectory(const struct stat& metadata)
{
    return S_ISDIR(metadata.st_mode) && metadata.st_uid == ::geteuid() &&
        (metadata.st_mode & 0777) == 0700;
}

bool PrivateFile(const struct stat& metadata)
{
    return S_ISREG(metadata.st_mode) && metadata.st_uid == ::geteuid() &&
        (metadata.st_mode & 07777) == 0600 && metadata.st_nlink == 1;
}

bool SameIdentity(const struct stat& left, const struct stat& right)
{
    return left.st_dev == right.st_dev && left.st_ino == right.st_ino &&
        left.st_mode == right.st_mode && left.st_nlink == right.st_nlink &&
        left.st_uid == right.st_uid && left.st_gid == right.st_gid &&
        left.st_size == right.st_size && left.st_mtim.tv_sec == right.st_mtim.tv_sec &&
        left.st_mtim.tv_nsec == right.st_mtim.tv_nsec &&
        left.st_ctim.tv_sec == right.st_ctim.tv_sec &&
        left.st_ctim.tv_nsec == right.st_ctim.tv_nsec;
}

bool HexDigest(const std::string& value)
{
    if (value.size() != 64) return false;
    for (std::size_t i = 0; i < value.size(); ++i)
        if (!((value[i] >= '0' && value[i] <= '9') ||
              (value[i] >= 'a' && value[i] <= 'f')))
            return false;
    return true;
}

bool SafeGenerationName(const std::string& value)
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

bool ParseUnsigned(const std::string& value, std::uint64_t& result)
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

bool ParseSigned(const std::string& value, long& result)
{
    if (value.empty()) return false;
    char* end = nullptr;
    errno = 0;
    const long parsed = std::strtol(value.c_str(), &end, 10);
    if (errno != 0 || end == value.c_str() || *end != '\0') return false;
    result = parsed;
    return true;
}

bool ParseSigned64(const std::string& value, std::int64_t& result)
{
    if (value.empty()) return false;
    char* end = nullptr;
    errno = 0;
    const long long parsed = std::strtoll(value.c_str(), &end, 10);
    if (errno != 0 || end == value.c_str() || *end != '\0') return false;
    result = static_cast<std::int64_t>(parsed);
    return true;
}

std::string Hex(const std::string& value)
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

bool DecodeHex(const std::string& encoded, std::string& value)
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

std::string Sha256(const void* data, std::size_t size)
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

bool HashFd(int fd, off_t size, std::string& digest)
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

bool ReadPrivateFileAt(int directoryFd, const char* name, std::size_t maximum,
                       std::string& contents, struct stat* identity = nullptr)
{
    const int fd = ::openat(directoryFd, name, O_RDONLY | O_CLOEXEC | O_NOFOLLOW);
    if (fd < 0) return false;
    struct stat before;
    bool ok = ::fstat(fd, &before) == 0 && PrivateFile(before) &&
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
        SameIdentity(before, after) && SameIdentity(after, named);
    if (identity != nullptr && ok) *identity = after;
    if (::close(fd) != 0) ok = false;
    return ok;
}

bool OpenHashedFileAt(int directoryFd, const char* name,
                      const std::string& expectedDigest,
                      int& fd, struct stat& identity)
{
    fd = ::openat(directoryFd, name, O_RDONLY | O_CLOEXEC | O_NOFOLLOW);
    if (fd < 0) return false;
    struct stat named;
    std::string digest;
    const bool ok = ::fstat(fd, &identity) == 0 && PrivateFile(identity) &&
        ::fstatat(directoryFd, name, &named, AT_SYMLINK_NOFOLLOW) == 0 &&
        SameIdentity(identity, named) && HashFd(fd, identity.st_size, digest) &&
        digest == expectedDigest;
    if (!ok)
    {
        ::close(fd);
        fd = -1;
    }
    return ok;
}

bool VerifyHashedFileAt(int directoryFd, const char* name,
                        const std::string& expectedDigest)
{
    int fd = -1;
    struct stat identity;
    const bool ok = OpenHashedFileAt(directoryFd, name, expectedDigest, fd, identity);
    if (fd >= 0) ::close(fd);
    return ok;
}

bool ParseFields(const std::string& contents, const char* header, Fields& fields)
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

bool ExactFieldNames(const Fields& fields, const char* const* names,
                     std::size_t count)
{
    if (fields.size() != count) return false;
    for (std::size_t i = 0; i < count; ++i)
        if (fields.find(names[i]) == fields.end()) return false;
    return true;
}

bool SplitTabs(const std::string& line, std::vector<std::string>& fields)
{
    fields.clear();
    if (line.size() > kMaximumIndexLineBytes || line.empty()) return false;
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

bool ReadLineContaining(int fd, off_t fileSize, off_t probe,
                        off_t& start, off_t& end, std::string& line)
{
    if (fileSize <= 0) return false;
    if (probe >= fileSize) probe = fileSize - 1;
    const off_t backward = std::min<off_t>(probe,
        static_cast<off_t>(kMaximumIndexLineBytes));
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
        static_cast<off_t>(kMaximumIndexLineBytes + 1U), fileSize - start));
    std::string forward(maximum, '\0');
    ssize_t count;
    do
    {
        count = ::pread(fd, &forward[0], forward.size(), start);
    } while (count < 0 && errno == EINTR);
    if (count <= 0) return false;
    forward.resize(static_cast<std::size_t>(count));
    const std::size_t found = forward.find('\n');
    if (found == std::string::npos || found > kMaximumIndexLineBytes) return false;
    line.assign(forward.data(), found);
    end = start + static_cast<off_t>(found + 1U);
    return true;
}

int CompareKey(const std::vector<std::string>& fields,
               const std::array<std::string, 3>& wanted)
{
    for (std::size_t i = 0; i < 3U; ++i)
    {
        if (fields[i] < wanted[i]) return -1;
        if (fields[i] > wanted[i]) return 1;
    }
    return 0;
}

bool ReplayRange(int fd, off_t start, off_t end,
                 std::size_t maxBytes, std::size_t maxRecords,
                 std::size_t maxRecordBytes,
                 const std::function<void(const OmsJournalEvent&)>& onEvent,
                 std::size_t& records)
{
    records = 0;
    if (start < 0 || end < start || static_cast<std::uint64_t>(end - start) > maxBytes)
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

std::string RequestKey(const std::string& agentId,
                       const std::string& sessionId,
                       const std::string& commandId)
{
    return agentId + "\x1f" + sessionId + "\x1f" + commandId;
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

bool OmsGenerationStore::Prepare(std::string& reason)
{
    Close();
    m_storeFd = ::open(m_storePath.c_str(),
        O_RDONLY | O_DIRECTORY | O_CLOEXEC | O_NOFOLLOW);
    struct stat storeMetadata;
    if (m_storeFd < 0 || ::fstat(m_storeFd, &storeMetadata) != 0 ||
        !PrivateDirectory(storeMetadata))
    {
        reason = "OMS_GENERATION_STORE_UNSAFE";
        Close();
        return false;
    }

    std::string runtimeCurrent, current;
    if (!ReadPrivateFileAt(m_storeFd, "CURRENT.runtime", kMaximumMetadataBytes,
            runtimeCurrent) ||
        !ReadPrivateFileAt(m_storeFd, "CURRENT", kMaximumMetadataBytes, current))
    {
        reason = "OMS_GENERATION_CURRENT_UNSAFE";
        Close();
        return false;
    }
    Fields selected;
    static const char* const currentNames[] = {
        "generation", "current_sha256", "manifest_sha256",
        "runtime_manifest_sha256"
    };
    if (!ParseFields(runtimeCurrent, kRuntimeCurrentHeader, selected) ||
        !ExactFieldNames(selected, currentNames,
            sizeof(currentNames) / sizeof(currentNames[0])) ||
        !SafeGenerationName(selected["generation"]) ||
        !HexDigest(selected["current_sha256"]) ||
        !HexDigest(selected["manifest_sha256"]) ||
        !HexDigest(selected["runtime_manifest_sha256"]) ||
        Sha256(current.data(), current.size()) != selected["current_sha256"])
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
        !PrivateDirectory(generationMetadata))
    {
        reason = "OMS_GENERATION_DIRECTORY_UNSAFE";
        Close();
        return false;
    }
    if (!VerifyHashedFileAt(m_generationFd, "manifest.json",
            selected["manifest_sha256"]))
    {
        reason = "OMS_GENERATION_MANIFEST_DIGEST_MISMATCH";
        Close();
        return false;
    }

    std::string runtimeManifest;
    if (!ReadPrivateFileAt(m_generationFd, "runtime-manifest.txt",
            kMaximumMetadataBytes, runtimeManifest) ||
        Sha256(runtimeManifest.data(), runtimeManifest.size()) !=
            selected["runtime_manifest_sha256"])
    {
        reason = "OMS_GENERATION_RUNTIME_MANIFEST_DIGEST_MISMATCH";
        Close();
        return false;
    }
    Fields manifest;
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
    if (!ParseFields(runtimeManifest, kRuntimeManifestHeader, manifest) ||
        !ExactFieldNames(manifest, manifestNames,
            sizeof(manifestNames) / sizeof(manifestNames[0])) ||
        manifest["generation"] != m_generation ||
        (manifest["parent_generation"] != "-" &&
         !SafeGenerationName(manifest["parent_generation"])) ||
        !ParseUnsigned(manifest["journal_prefix_bytes"], m_journalPrefixBytes) ||
        !ParseUnsigned(manifest["journal_records"], journalRecords) ||
        !ParseUnsigned(manifest["command_records"], m_commandRecords) ||
        !ParseUnsigned(manifest["send_attempt_records"], m_sendAttemptRecords) ||
        !ParseUnsigned(manifest["hot_replay_records"], m_hotReplayRecords) ||
        !HexDigest(manifest["journal_prefix_sha256"]) ||
        !HexDigest(manifest["segment_sha256"]) ||
        !HexDigest(manifest["checkpoint_sha256"]) ||
        !HexDigest(manifest["command_index_sha256"]) ||
        !HexDigest(manifest["runtime_command_index_sha256"]) ||
        !HexDigest(manifest["send_attempt_index_sha256"]) ||
        !HexDigest(manifest["hot_replay_sha256"]) ||
        manifest["authorization_effect"] != "NONE" ||
        manifest["paper_authorized"] != "0" || manifest["live_authorized"] != "0")
    {
        reason = "OMS_GENERATION_RUNTIME_MANIFEST_INVALID";
        Close();
        return false;
    }
    m_journalPrefixSha256 = manifest["journal_prefix_sha256"];
    if (!VerifyHashedFileAt(m_generationFd, "segment-000001.jsonl",
            manifest["segment_sha256"]) ||
        !VerifyHashedFileAt(m_generationFd, "checkpoint.json",
            manifest["checkpoint_sha256"]) ||
        !VerifyHashedFileAt(m_generationFd, "command-index.tsv",
            manifest["command_index_sha256"]))
    {
        reason = "OMS_GENERATION_RUNTIME_FILE_DIGEST_MISMATCH";
        Close();
        return false;
    }
    struct stat segmentMetadata;
    if (::fstatat(m_generationFd, "segment-000001.jsonl", &segmentMetadata,
            AT_SYMLINK_NOFOLLOW) != 0 || !PrivateFile(segmentMetadata) ||
        static_cast<std::uint64_t>(segmentMetadata.st_size) != m_journalPrefixBytes)
    {
        reason = "OMS_GENERATION_PREFIX_SIZE_MISMATCH";
        Close();
        return false;
    }
    if (!OpenHashedFileAt(m_generationFd, "runtime-command-index.tsv",
            manifest["runtime_command_index_sha256"],
            m_commandIndexFd, m_commandIndexIdentity) ||
        !OpenHashedFileAt(m_generationFd, "send-attempt-index.tsv",
            manifest["send_attempt_index_sha256"],
            m_sendIndexFd, m_sendIndexIdentity) ||
        !VerifyHashedFileAt(m_generationFd, "hot-replay.jsonl",
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
        PrivateFile(actual) && SameIdentity(expected, actual) && SameIdentity(actual, named);
}

bool OmsGenerationStore::Recover(
    std::size_t maxTailBytes,
    std::size_t maxTailRecords,
    std::size_t maxRecordBytes,
    const std::function<void(const OmsJournalEvent&)>& onEvent,
    std::string& reason)
{
    if (!Prepare(reason)) return false;
    int hotFd = -1;
    struct stat hotIdentity;
    // The digest was checked in Prepare; re-open under the same pinned directory
    // and retain a stable identity while events are projected.
    hotFd = ::openat(m_generationFd, "hot-replay.jsonl",
        O_RDONLY | O_CLOEXEC | O_NOFOLLOW);
    bool ok = hotFd >= 0 && ::fstat(hotFd, &hotIdentity) == 0 &&
        PrivateFile(hotIdentity) && hotIdentity.st_size >= 0 &&
        static_cast<std::uint64_t>(hotIdentity.st_size) <= maxTailBytes;
    std::size_t hotRecords = 0;
    if (ok)
        ok = ReplayRange(hotFd, 0, hotIdentity.st_size, maxTailBytes,
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
        !PrivateFile(before) || before.st_size < 0 ||
        static_cast<std::uint64_t>(before.st_size) < m_journalPrefixBytes ||
        ::lstat(m_journalPath.c_str(), &named) != 0 ||
        !SameIdentity(before, named))
    {
        if (journalFd >= 0) ::close(journalFd);
        reason = "OMS_GENERATION_ACTIVE_JOURNAL_UNSAFE";
        Close();
        return false;
    }
    std::string prefixDigest;
    if (!HashFd(journalFd, static_cast<off_t>(m_journalPrefixBytes), prefixDigest) ||
        prefixDigest != m_journalPrefixSha256)
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
    ok = ReplayRange(journalFd, static_cast<off_t>(m_journalPrefixBytes),
        before.st_size, maxTailBytes, maxTailRecords, maxRecordBytes,
        onEvent, tailRecords);
    struct stat after, namedAfter;
    ok = ok && ::fstat(journalFd, &after) == 0 &&
        ::lstat(m_journalPath.c_str(), &namedAfter) == 0 &&
        SameIdentity(before, after) && SameIdentity(after, namedAfter);
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
        Hex(agentId), Hex(sessionId), Hex(commandId)}};
    off_t low = 0;
    off_t high = m_commandIndexIdentity.st_size;
    while (low < high)
    {
        const off_t midpoint = low + (high - low) / 2;
        off_t start = 0, end = 0;
        std::string line;
        std::vector<std::string> fields;
        if (!ReadLineContaining(m_commandIndexFd, m_commandIndexIdentity.st_size,
                midpoint, start, end, line) ||
            !SplitTabs(line, fields) || fields.size() != 13U)
        {
            reason = "OMS_GENERATION_COMMAND_INDEX_INVALID";
            return OmsGenerationLookupStatus::Error;
        }
        const int comparison = CompareKey(fields, wanted);
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
        if (!DecodeHex(fields[0], record.agentId) ||
            !DecodeHex(fields[1], record.sessionId) ||
            !DecodeHex(fields[2], record.commandId) ||
            !DecodeHex(fields[3], record.requestHash) ||
            !ParseSigned(fields[6], orderId) ||
            !DecodeHex(fields[7], record.reasonCode) ||
            !DecodeHex(fields[8], record.venueCorrelationId) ||
            !ParseUnsigned(fields[9], sequence) ||
            !DecodeHex(fields[10], record.account) ||
            !DecodeHex(fields[11], record.executionDomain) ||
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
    off_t offset = 0;
    while (offset < m_sendIndexIdentity.st_size)
    {
        off_t start = 0, end = 0;
        std::string line;
        std::vector<std::string> fields;
        if (!ReadLineContaining(m_sendIndexFd, m_sendIndexIdentity.st_size,
                offset, start, end, line) || start != offset || end <= offset ||
            !SplitTabs(line, fields) || fields.size() != 7U)
        {
            reason = "OMS_GENERATION_SEND_INDEX_INVALID";
            return false;
        }
        std::string rowAccount, rowDomain, agent, session, command;
        std::int64_t ts = 0;
        std::uint64_t sequence = 0;
        if (!DecodeHex(fields[0], rowAccount) || !DecodeHex(fields[1], rowDomain) ||
            !ParseSigned64(fields[2], ts) || !DecodeHex(fields[3], agent) ||
            !DecodeHex(fields[4], session) || !DecodeHex(fields[5], command) ||
            !ParseUnsigned(fields[6], sequence))
        {
            reason = "OMS_GENERATION_SEND_INDEX_INVALID";
            return false;
        }
        if (rowAccount == account && rowDomain == executionDomain && ts > cutoffMs)
        {
            const std::string requestKey = RequestKey(agent, session, command);
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
        if (!ReadLineContaining(m_commandIndexFd, m_commandIndexIdentity.st_size,
                offset, start, end, line) || start != offset || end <= offset ||
            !SplitTabs(line, fields) || fields.size() != 13U)
        {
            reason = "OMS_GENERATION_COMMAND_INDEX_INVALID";
            return false;
        }
        std::string rowAccount, rowDomain;
        if (!DecodeHex(fields[10], rowAccount) || !DecodeHex(fields[11], rowDomain) ||
            (fields[12] != "0" && fields[12] != "1"))
        {
            reason = "OMS_GENERATION_COMMAND_INDEX_INVALID";
            return false;
        }
        if (fields[12] == "1" && rowAccount == account && rowDomain == executionDomain)
        {
            if (records.size() >= kMaximumTerminalMutationRecords)
            {
                reason = "OMS_GENERATION_TERMINAL_MUTATION_UNIVERSE_TOO_LARGE";
                return false;
            }
            OmsGenerationMutationRecord record;
            if (!DecodeHex(fields[0], record.agentId) ||
                !DecodeHex(fields[1], record.sessionId) ||
                !DecodeHex(fields[2], record.commandId) ||
                !DecodeHex(fields[8], record.venueCorrelationId) ||
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
