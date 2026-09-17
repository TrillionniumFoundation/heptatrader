#include "execution_coordinator.h"

#include <algorithm>
#include <array>
#include <cerrno>
#include <fcntl.h>
#include <iomanip>
#include <limits>
#include <openssl/evp.h>
#include <set>
#include <sstream>
#include <tuple>
#include <unistd.h>

namespace
{
const std::size_t kSessionIndexLineLimit = 64U * 1024U;
const std::size_t kTerminalMutationLimit = 4097U;
const std::size_t kGenerationMetadataLimit = 1024U * 1024U;
const char* const kRuntimeManifestV1 = "HEPTA_OMS_RUNTIME_GENERATION_V1";
const char* const kRuntimeManifestV2 = "HEPTA_OMS_RUNTIME_GENERATION_V2";

typedef std::map<std::string, std::string> SessionFields;

bool SessionPrivateDirectory(const struct stat& value)
{
    return S_ISDIR(value.st_mode) && value.st_uid == ::geteuid() &&
        (value.st_mode & 0777) == 0700;
}

bool SessionPrivateFile(const struct stat& value)
{
    return S_ISREG(value.st_mode) && value.st_uid == ::geteuid() &&
        (value.st_mode & 07777) == 0600 && value.st_nlink == 1;
}

bool SessionSameIdentity(const struct stat& left, const struct stat& right)
{
    return left.st_dev == right.st_dev && left.st_ino == right.st_ino &&
        left.st_mode == right.st_mode && left.st_nlink == right.st_nlink &&
        left.st_uid == right.st_uid && left.st_gid == right.st_gid &&
        left.st_size == right.st_size &&
        left.st_mtim.tv_sec == right.st_mtim.tv_sec &&
        left.st_mtim.tv_nsec == right.st_mtim.tv_nsec &&
        left.st_ctim.tv_sec == right.st_ctim.tv_sec &&
        left.st_ctim.tv_nsec == right.st_ctim.tv_nsec;
}

bool SessionHexDigest(const std::string& value)
{
    if (value.size() != 64U) return false;
    for (std::size_t i = 0; i < value.size(); ++i)
        if (!((value[i] >= '0' && value[i] <= '9') ||
              (value[i] >= 'a' && value[i] <= 'f')))
            return false;
    return true;
}

bool SessionSafeName(const std::string& value)
{
    if (value.empty() || value.size() > 128U || value == "." || value == "..")
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

bool SessionDecodeHex(const std::string& encoded, std::string& value)
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

bool SessionSplitTabs(const std::string& line, std::vector<std::string>& fields)
{
    fields.clear();
    if (line.empty() || line.size() > kSessionIndexLineLimit) return false;
    std::size_t offset = 0;
    for (;;)
    {
        const std::size_t next = line.find('\t', offset);
        fields.push_back(line.substr(offset,
            next == std::string::npos ? std::string::npos : next - offset));
        if (next == std::string::npos) return true;
        offset = next + 1U;
    }
}

std::string SessionSha256(const void* data, std::size_t size)
{
    unsigned char digest[EVP_MAX_MD_SIZE];
    unsigned int length = 0;
    EVP_MD_CTX* context = EVP_MD_CTX_new();
    if (context == nullptr) return std::string();
    const bool ok = EVP_DigestInit_ex(context, EVP_sha256(), nullptr) == 1 &&
        EVP_DigestUpdate(context, data, size) == 1 &&
        EVP_DigestFinal_ex(context, digest, &length) == 1;
    EVP_MD_CTX_free(context);
    if (!ok || length != 32U) return std::string();
    std::ostringstream out;
    out << std::hex << std::setfill('0');
    for (unsigned int i = 0; i < length; ++i)
        out << std::setw(2) << static_cast<unsigned int>(digest[i]);
    return out.str();
}

bool SessionHashFd(int fd, off_t size, std::string& digest)
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
        do { count = ::pread(fd, buffer.data(), wanted, offset); }
        while (count < 0 && errno == EINTR);
        if (count <= 0) { ok = false; break; }
        ok = EVP_DigestUpdate(context, buffer.data(),
            static_cast<std::size_t>(count)) == 1;
        offset += count;
    }
    unsigned char bytes[EVP_MAX_MD_SIZE];
    unsigned int length = 0;
    ok = ok && EVP_DigestFinal_ex(context, bytes, &length) == 1 && length == 32U;
    EVP_MD_CTX_free(context);
    if (!ok) return false;
    std::ostringstream out;
    out << std::hex << std::setfill('0');
    for (unsigned int i = 0; i < length; ++i)
        out << std::setw(2) << static_cast<unsigned int>(bytes[i]);
    digest = out.str();
    return true;
}

bool SessionReadPrivateFileAt(int directoryFd, const char* name,
                              std::size_t maximum, std::string& contents)
{
    const int fd = ::openat(directoryFd, name, O_RDONLY | O_CLOEXEC | O_NOFOLLOW);
    if (fd < 0) return false;
    struct stat before;
    bool ok = ::fstat(fd, &before) == 0 && SessionPrivateFile(before) &&
        before.st_size >= 0 && static_cast<std::uint64_t>(before.st_size) <= maximum;
    contents.clear();
    if (ok)
    {
        contents.resize(static_cast<std::size_t>(before.st_size));
        std::size_t offset = 0;
        while (ok && offset < contents.size())
        {
            ssize_t count;
            do { count = ::read(fd, &contents[offset], contents.size() - offset); }
            while (count < 0 && errno == EINTR);
            if (count <= 0) ok = false;
            else offset += static_cast<std::size_t>(count);
        }
    }
    struct stat after, named;
    ok = ok && ::fstat(fd, &after) == 0 &&
        ::fstatat(directoryFd, name, &named, AT_SYMLINK_NOFOLLOW) == 0 &&
        SessionSameIdentity(before, after) && SessionSameIdentity(after, named);
    if (::close(fd) != 0) ok = false;
    return ok;
}

bool SessionOpenHashedFileAt(int directoryFd, const char* name,
                             const std::string& expected,
                             int& fd, struct stat& identity)
{
    fd = ::openat(directoryFd, name, O_RDONLY | O_CLOEXEC | O_NOFOLLOW);
    if (fd < 0) return false;
    struct stat named;
    std::string digest;
    const bool ok = ::fstat(fd, &identity) == 0 && SessionPrivateFile(identity) &&
        ::fstatat(directoryFd, name, &named, AT_SYMLINK_NOFOLLOW) == 0 &&
        SessionSameIdentity(identity, named) &&
        SessionHashFd(fd, identity.st_size, digest) && digest == expected;
    if (!ok) { ::close(fd); fd = -1; }
    return ok;
}

bool SessionVerifyHashedFileAt(int directoryFd, const char* name,
                               const std::string& expected)
{
    int fd = -1;
    struct stat identity;
    const bool ok = SessionOpenHashedFileAt(directoryFd, name, expected, fd, identity);
    if (fd >= 0) ::close(fd);
    return ok;
}

bool SessionParseFields(const std::string& contents, const char* header,
                        SessionFields& fields)
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
            separator + 1U >= line.size() ||
            !fields.insert(std::make_pair(line.substr(0, separator),
                line.substr(separator + 1U))).second)
            return false;
    }
    return true;
}

bool SessionExtractJsonDigest(const std::string& json, const char* field,
                              std::string& digest)
{
    const std::string marker = std::string("\"") + field + "\":\"";
    const std::size_t start = json.find(marker);
    if (start == std::string::npos ||
        json.find(marker, start + marker.size()) != std::string::npos)
        return false;
    const std::size_t valueStart = start + marker.size();
    const std::size_t valueEnd = json.find('"', valueStart);
    if (valueEnd == std::string::npos || valueEnd - valueStart != 64U)
        return false;
    digest = json.substr(valueStart, 64U);
    return SessionHexDigest(digest);
}

bool SessionReplayRange(int fd, off_t start, off_t end,
                        std::size_t maxRecordBytes,
                        const std::function<void(const OmsJournalEvent&)>& onEvent,
                        std::size_t& records)
{
    records = 0;
    if (start < 0 || end < start) return false;
    std::string pending;
    std::array<char, 64U * 1024U> buffer;
    off_t offset = start;
    while (offset < end)
    {
        const std::size_t wanted = static_cast<std::size_t>(std::min<off_t>(
            static_cast<off_t>(buffer.size()), end - offset));
        ssize_t count;
        do { count = ::pread(fd, buffer.data(), wanted, offset); }
        while (count < 0 && errno == EINTR);
        if (count <= 0) return false;
        offset += count;
        pending.append(buffer.data(), static_cast<std::size_t>(count));
        for (;;)
        {
            const std::size_t newline = pending.find('\n');
            if (newline == std::string::npos) break;
            if (newline == 0 || newline > maxRecordBytes ||
                records == std::numeric_limits<std::size_t>::max())
                return false;
            OmsJournalEvent event;
            if (!OmsJournal::ParseJsonLine(pending.substr(0, newline), event)) return false;
            onEvent(event);
            ++records;
            pending.erase(0, newline + 1U);
        }
        if (pending.size() > maxRecordBytes) return false;
    }
    return pending.empty();
}

struct CompleteHistorySegment
{
    std::string generation;
    std::string sha256;
};
}

bool OmsGenerationStore::EnumerateMutationRecords(
    const std::string& agentId,
    const std::string& sessionId,
    const std::string& account,
    const std::string& executionDomain,
    std::vector<OmsGenerationMutationRecord>& records,
    std::string& reason) const
{
    records.clear();
    if (!m_active) { reason.clear(); return true; }
    if (!ValidatePinnedIndex(m_commandIndexFd, "runtime-command-index.tsv",
            m_commandIndexIdentity))
    {
        reason = "OMS_GENERATION_COMMAND_INDEX_CHANGED";
        return false;
    }

    std::string pending;
    std::array<char, 64U * 1024U> buffer;
    off_t offset = 0;
    while (offset < m_commandIndexIdentity.st_size)
    {
        const std::size_t wanted = static_cast<std::size_t>(std::min<off_t>(
            static_cast<off_t>(buffer.size()), m_commandIndexIdentity.st_size - offset));
        ssize_t count;
        do { count = ::pread(m_commandIndexFd, buffer.data(), wanted, offset); }
        while (count < 0 && errno == EINTR);
        if (count <= 0)
        {
            reason = "OMS_GENERATION_COMMAND_INDEX_INVALID";
            return false;
        }
        offset += count;
        pending.append(buffer.data(), static_cast<std::size_t>(count));
        for (;;)
        {
            const std::size_t newline = pending.find('\n');
            if (newline == std::string::npos) break;
            const std::string line = pending.substr(0, newline);
            pending.erase(0, newline + 1U);
            std::vector<std::string> fields;
            if (!SessionSplitTabs(line, fields) || fields.size() != 13U)
            {
                reason = "OMS_GENERATION_COMMAND_INDEX_INVALID";
                return false;
            }
            std::string rowAgent, rowSession, rowAccount, rowDomain;
            if (!SessionDecodeHex(fields[0], rowAgent) ||
                !SessionDecodeHex(fields[1], rowSession) ||
                !SessionDecodeHex(fields[10], rowAccount) ||
                !SessionDecodeHex(fields[11], rowDomain) ||
                (fields[12] != "0" && fields[12] != "1"))
            {
                reason = "OMS_GENERATION_COMMAND_INDEX_INVALID";
                return false;
            }
            if (fields[12] == "1" && rowAgent == agentId &&
                rowSession == sessionId && rowAccount == account && rowDomain == executionDomain)
            {
                if (records.size() >= kTerminalMutationLimit)
                {
                    reason = "OMS_GENERATION_TERMINAL_MUTATION_UNIVERSE_TOO_LARGE";
                    return false;
                }
                OmsGenerationMutationRecord record;
                record.agentId = rowAgent;
                record.sessionId = rowSession;
                if (!SessionDecodeHex(fields[2], record.commandId) ||
                    !SessionDecodeHex(fields[8], record.venueCorrelationId) ||
                    (fields[4] != "place" && fields[4] != "cancel" && fields[4] != "flatten"))
                {
                    reason = "OMS_GENERATION_COMMAND_INDEX_INVALID";
                    return false;
                }
                record.operation = fields[4];
                records.push_back(record);
            }
        }
        if (pending.size() > kSessionIndexLineLimit)
        {
            reason = "OMS_GENERATION_COMMAND_INDEX_INVALID";
            return false;
        }
    }
    if (!pending.empty() ||
        !ValidatePinnedIndex(m_commandIndexFd, "runtime-command-index.tsv",
            m_commandIndexIdentity))
    {
        reason = "OMS_GENERATION_COMMAND_INDEX_CHANGED";
        return false;
    }
    reason.clear();
    return true;
}

bool OmsGenerationStore::ReplayCompleteHistory(
    std::size_t maxRecordBytes,
    const std::function<void(const OmsJournalEvent&)>& onEvent,
    std::uint64_t& records,
    std::string& reason)
{
    records = 0;
    if (!Prepare(reason)) return false;
    if (!m_segmentedTail)
    {
        reason = "OMS_GENERATION_COMPLETE_REPLAY_REQUIRES_V2";
        Close();
        return false;
    }

    std::vector<CompleteHistorySegment> reverseSegments;
    std::set<std::string> seen;
    std::string generation = m_generation;
    for (;;)
    {
        if (!seen.insert(generation).second)
        {
            reason = "OMS_GENERATION_PARENT_CHAIN_INVALID";
            Close();
            return false;
        }
        const int generationFd = ::openat(m_storeFd, generation.c_str(),
            O_RDONLY | O_DIRECTORY | O_CLOEXEC | O_NOFOLLOW);
        struct stat directoryIdentity;
        if (generationFd < 0 || ::fstat(generationFd, &directoryIdentity) != 0 ||
            !SessionPrivateDirectory(directoryIdentity))
        {
            if (generationFd >= 0) ::close(generationFd);
            reason = "OMS_GENERATION_DIRECTORY_UNSAFE";
            Close();
            return false;
        }
        std::string runtimeManifest;
        if (!SessionReadPrivateFileAt(generationFd, "runtime-manifest.txt",
                kGenerationMetadataLimit, runtimeManifest))
        {
            ::close(generationFd);
            reason = "OMS_GENERATION_COMPLETE_REPLAY_MANIFEST_INVALID";
            Close();
            return false;
        }
        SessionFields fields;
        const bool v2 = SessionParseFields(runtimeManifest, kRuntimeManifestV2, fields);
        if (!v2)
        {
            fields.clear();
            if (!SessionParseFields(runtimeManifest, kRuntimeManifestV1, fields) ||
                fields["generation"] != generation ||
                !SessionHexDigest(fields["segment_sha256"]) ||
                !SessionVerifyHashedFileAt(generationFd, "segment-000001.jsonl",
                    fields["segment_sha256"]))
            {
                ::close(generationFd);
                reason = "OMS_GENERATION_COMPLETE_REPLAY_MANIFEST_INVALID";
                Close();
                return false;
            }
            reverseSegments.push_back({generation, fields["segment_sha256"]});
            ::close(generationFd);
            break;
        }
        if (fields["generation"] != generation ||
            !SessionHexDigest(fields["segment_sha256"]) ||
            (fields["parent_generation"] != "-" && !SessionSafeName(fields["parent_generation"])) ||
            ((fields["parent_generation"] == "-") !=
             (fields["parent_manifest_sha256"] == "-")) ||
            (fields["parent_generation"] != "-" &&
             !SessionHexDigest(fields["parent_manifest_sha256"])) ||
            !SessionVerifyHashedFileAt(generationFd, "segment-000001.jsonl",
                fields["segment_sha256"]))
        {
            ::close(generationFd);
            reason = "OMS_GENERATION_COMPLETE_REPLAY_MANIFEST_INVALID";
            Close();
            return false;
        }
        reverseSegments.push_back({generation, fields["segment_sha256"]});
        const std::string parent = fields["parent_generation"];
        const std::string parentManifestDigest = fields["parent_manifest_sha256"];
        ::close(generationFd);
        if (parent == "-") break;

        const int parentFd = ::openat(m_storeFd, parent.c_str(),
            O_RDONLY | O_DIRECTORY | O_CLOEXEC | O_NOFOLLOW);
        struct stat parentIdentity;
        std::string parentManifestJson, parentRuntime, expectedRuntimeDigest;
        const bool parentOk = parentFd >= 0 &&
            ::fstat(parentFd, &parentIdentity) == 0 &&
            SessionPrivateDirectory(parentIdentity) &&
            SessionVerifyHashedFileAt(parentFd, "manifest.json", parentManifestDigest) &&
            SessionReadPrivateFileAt(parentFd, "manifest.json", kGenerationMetadataLimit,
                parentManifestJson) &&
            SessionExtractJsonDigest(parentManifestJson, "runtime_manifest_sha256",
                expectedRuntimeDigest) &&
            SessionReadPrivateFileAt(parentFd, "runtime-manifest.txt",
                kGenerationMetadataLimit, parentRuntime) &&
            SessionSha256(parentRuntime.data(), parentRuntime.size()) == expectedRuntimeDigest;
        if (parentFd >= 0) ::close(parentFd);
        if (!parentOk)
        {
            reason = "OMS_GENERATION_PARENT_BINDING_INVALID";
            Close();
            return false;
        }
        generation = parent;
    }

    for (std::vector<CompleteHistorySegment>::const_reverse_iterator it =
             reverseSegments.rbegin(); it != reverseSegments.rend(); ++it)
    {
        const int generationFd = ::openat(m_storeFd, it->generation.c_str(),
            O_RDONLY | O_DIRECTORY | O_CLOEXEC | O_NOFOLLOW);
        int segmentFd = -1;
        struct stat segmentIdentity;
        if (generationFd < 0 ||
            !SessionOpenHashedFileAt(generationFd, "segment-000001.jsonl",
                it->sha256, segmentFd, segmentIdentity) || segmentIdentity.st_size < 0)
        {
            if (segmentFd >= 0) ::close(segmentFd);
            if (generationFd >= 0) ::close(generationFd);
            reason = "OMS_GENERATION_COMPLETE_REPLAY_SEGMENT_INVALID";
            Close();
            return false;
        }
        std::size_t segmentRecords = 0;
        const bool ok = SessionReplayRange(segmentFd, 0, segmentIdentity.st_size,
            maxRecordBytes, onEvent, segmentRecords);
        const bool closed = ::close(segmentFd) == 0 && ::close(generationFd) == 0;
        if (!ok || !closed ||
            records > std::numeric_limits<std::uint64_t>::max() - segmentRecords)
        {
            reason = "OMS_GENERATION_COMPLETE_REPLAY_SEGMENT_FAILED";
            Close();
            return false;
        }
        records += static_cast<std::uint64_t>(segmentRecords);
    }

    const int journalFd = ::open(m_journalPath.c_str(), O_RDONLY | O_CLOEXEC | O_NOFOLLOW);
    struct stat before, named;
    if (journalFd < 0 || ::fstat(journalFd, &before) != 0 ||
        !SessionPrivateFile(before) || before.st_size < 0 ||
        static_cast<std::uint64_t>(before.st_size) < m_journalPrefixBytes ||
        ::lstat(m_journalPath.c_str(), &named) != 0 ||
        !SessionSameIdentity(before, named))
    {
        if (journalFd >= 0) ::close(journalFd);
        reason = "OMS_GENERATION_ACTIVE_TAIL_UNSAFE";
        Close();
        return false;
    }
    std::string headerDigest;
    if (!SessionHashFd(journalFd, static_cast<off_t>(m_journalPrefixBytes), headerDigest) ||
        headerDigest != m_journalPrefixSha256)
    {
        ::close(journalFd);
        reason = "OMS_GENERATION_ACTIVE_TAIL_LINEAGE_MISMATCH";
        Close();
        return false;
    }
    std::size_t tailRecords = 0;
    bool ok = SessionReplayRange(journalFd, static_cast<off_t>(m_journalPrefixBytes),
        before.st_size, maxRecordBytes, onEvent, tailRecords);
    struct stat after, namedAfter;
    ok = ok && ::fstat(journalFd, &after) == 0 &&
        ::lstat(m_journalPath.c_str(), &namedAfter) == 0 &&
        SessionSameIdentity(before, after) && SessionSameIdentity(after, namedAfter);
    if (::close(journalFd) != 0) ok = false;
    if (!ok || records > std::numeric_limits<std::uint64_t>::max() - tailRecords)
    {
        reason = "OMS_GENERATION_COMPLETE_REPLAY_TAIL_FAILED";
        Close();
        return false;
    }
    records += static_cast<std::uint64_t>(tailRecords);
    m_active = true;
    reason.clear();
    return true;
}

bool ExecutionCoordinator::EnterPaperTerminalFence(
    const AgentExecutionContext& context,
    const std::string& finalizationId,
    std::string& reason)
{
    (void)context;
    (void)finalizationId;
    reason = "IB_PAPER_TERMINAL_FENCE_V2_BINDING_REQUIRED";
    return false;
}

bool ExecutionCoordinator::EnterPaperTerminalFenceAndProject(
    const PaperTerminalFenceBinding& binding,
    PaperTerminalMutationUniverse& universe,
    std::string& reason)
{
    universe = PaperTerminalMutationUniverse();
    if (!ValidPaperTerminalFenceBinding(binding, reason)) return false;
    std::lock_guard<std::mutex> lock(m_mutex);

    if (m_mutationBlocked)
    {
        if (m_mutationBlockReason != "IB_PAPER_TERMINAL_HALTED" ||
            !m_paperTerminalFencePresent ||
            !SamePaperTerminalFenceBinding(m_paperTerminalFenceBinding, binding))
        {
            reason = m_mutationBlockReason == "IB_PAPER_TERMINAL_HALTED" ?
                "IB_PAPER_TERMINAL_FENCE_BINDING_MISMATCH" :
                (m_mutationBlockReason.empty() ?
                    "IB_PAPER_TERMINAL_FENCE_COORDINATOR_BLOCKED" : m_mutationBlockReason);
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
        if (!AppendOrBlockLocked(event, "OMS_PAPER_TERMINAL_FENCE_JOURNAL_FAILED"))
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
    std::set<std::tuple<std::string, std::string, std::string,
                        std::string, std::string>> seen;
    if (m_generationStore.IsActive())
    {
        std::vector<OmsGenerationMutationRecord> historical;
        if (!m_generationStore.EnumerateMutationRecords(
                binding.owner.agentId, binding.owner.sessionId,
                binding.owner.account, binding.owner.executionDomain,
                historical, reason))
            return false;
        for (std::size_t i = 0; i < historical.size(); ++i)
        {
            const OmsGenerationMutationRecord& source = historical[i];
            const std::tuple<std::string, std::string, std::string,
                             std::string, std::string> key(
                source.agentId, source.sessionId, source.commandId,
                source.operation, source.venueCorrelationId);
            if (!seen.insert(key).second) continue;
            PaperTerminalMutationRecord record;
            record.agentId = source.agentId;
            record.sessionId = source.sessionId;
            record.toolCallId = source.commandId;
            record.operation = source.operation;
            record.venueCorrelationId = source.venueCorrelationId;
            records.push_back(record);
        }
    }
    for (RequestRecordStore::Base::const_iterator it = m_requests.begin();
         it != m_requests.end(); ++it)
    {
        const RequestRecord& request = it->second;
        if (!request.durableMutationIntent ||
            request.context.agentId != binding.owner.agentId ||
            request.context.sessionId != binding.owner.sessionId ||
            request.context.account != binding.owner.account ||
            request.context.executionDomain != binding.owner.executionDomain)
            continue;
        const std::tuple<std::string, std::string, std::string,
                         std::string, std::string> key(
            request.context.agentId, request.context.sessionId,
            request.context.toolCallId, request.operation, request.venueCorrelationId);
        if (!seen.insert(key).second) continue;
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
