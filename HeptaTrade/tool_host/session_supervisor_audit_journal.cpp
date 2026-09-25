#include "session_supervisor_audit_journal.h"

#include <algorithm>
#include <cerrno>
#include <chrono>
#include <cstdlib>
#include <cstring>
#include <fcntl.h>
#include <limits.h>
#include <openssl/evp.h>
#include <sys/file.h>
#include <sys/stat.h>
#include <unistd.h>
#include <vector>
#include <limits>
#include <set>
#include <cstdio>
#include <sys/syscall.h>
#if defined(__linux__)
#include <linux/fs.h>
#endif

namespace
{
const char kAuditMagic[] = "HJA2";
const char kZeroHash[] =
    "0000000000000000000000000000000000000000000000000000000000000000";
const std::size_t kMaximumAuditLineBytes = 1024 * 1024;
const std::uint64_t kMaximumAuditJournalBytes =
    1ULL * 1024ULL * 1024ULL * 1024ULL;

bool IsLowerHex(const std::string& value, std::size_t length)
{
    if (value.size() != length) return false;
    for (std::string::const_iterator it = value.begin(); it != value.end(); ++it)
        if (!((*it >= '0' && *it <= '9') || (*it >= 'a' && *it <= 'f'))) return false;
    return true;
}

bool ParseUnsigned(const std::string& value, std::uint64_t& parsed)
{
    if (value.empty()) return false;
    for (std::string::const_iterator it = value.begin(); it != value.end(); ++it)
        if (*it < '0' || *it > '9') return false;
    char* end = nullptr;
    errno = 0;
    const unsigned long long number = std::strtoull(value.c_str(), &end, 10);
    if (errno != 0 || end == value.c_str() || *end != '\0') return false;
    parsed = static_cast<std::uint64_t>(number);
    return true;
}

std::vector<std::string> SplitTabs(const std::string& line)
{
    std::vector<std::string> fields;
    std::size_t begin = 0;
    while (begin <= line.size())
    {
        const std::size_t end = line.find('\t', begin);
        fields.push_back(line.substr(begin,
            end == std::string::npos ? std::string::npos : end - begin));
        if (end == std::string::npos) break;
        begin = end + 1;
    }
    return fields;
}
}

SessionSupervisorAuditJournal::SessionSupervisorAuditJournal(
    std::uint64_t maximumBytes, std::uint64_t safetyReserveBytes)
    : m_maximumBytes(maximumBytes), m_safetyReserveBytes(safetyReserveBytes), m_fd(-1), m_device(0), m_inode(0), m_nextSequence(1),
      m_chainedRecords(0), m_previousHash(kZeroHash), m_cacheValid(false)
{
}

SessionSupervisorAuditJournal::~SessionSupervisorAuditJournal()
{
    std::lock_guard<std::mutex> lock(m_mutex);
    if (m_fd >= 0) ::close(m_fd);
}

bool SessionSupervisorAuditJournal::Init(const std::string& path, std::string& reason)
{
    if (path.empty()) { reason = "SUPERVISOR_AUDIT_PATH_REQUIRED"; return false; }
    if (m_maximumBytes == 0 || m_maximumBytes > kMaximumAuditJournalBytes ||
        m_safetyReserveBytes == 0 || m_safetyReserveBytes >= m_maximumBytes / 2)
    { reason = "SUPERVISOR_AUDIT_CAPACITY_INVALID"; return false; }
    std::lock_guard<std::mutex> lock(m_mutex);
    if (m_fd >= 0) { reason = "SUPERVISOR_AUDIT_ALREADY_INITIALIZED"; return false; }
    struct stat existingActive, existingHistory;
    if (::lstat(path.c_str(), &existingActive) != 0 && errno == ENOENT &&
        (::lstat((path + ".segments").c_str(), &existingHistory) == 0 || errno != ENOENT))
    { reason = "SUPERVISOR_AUDIT_SEGMENT_ACTIVE_MISSING"; return false; }
    const int fd = ::open(path.c_str(),
        O_RDWR | O_CREAT | O_APPEND | O_CLOEXEC | O_NOFOLLOW, 0600);
    if (fd < 0) { reason = std::strerror(errno); return false; }
    struct stat metadata;
    struct stat pathMetadata;
    char canonical[PATH_MAX];
    bool ok = ::fstat(fd, &metadata) == 0 &&
        ::lstat(path.c_str(), &pathMetadata) == 0;
    int saved = ok ? 0 : errno;
    if (!ok)
        reason = saved == 0 ? "SUPERVISOR_AUDIT_STAT_FAILED" : std::strerror(saved);
    else if (!S_ISREG(metadata.st_mode) || !S_ISREG(pathMetadata.st_mode) ||
        metadata.st_dev != pathMetadata.st_dev || metadata.st_ino != pathMetadata.st_ino ||
        metadata.st_nlink != 1 || metadata.st_uid != ::geteuid())
    {
        reason = "SUPERVISOR_AUDIT_UNSAFE_FILE";
        ok = false;
    }
    else if (::realpath(path.c_str(), canonical) == nullptr)
    {
        reason = "SUPERVISOR_AUDIT_REALPATH_FAILED";
        ok = false;
    }
    else if (::fchmod(fd, 0600) != 0 || ::fsync(fd) != 0)
    {
        saved = errno;
        reason = saved == 0 ? "SUPERVISOR_AUDIT_INIT_FAILED" : std::strerror(saved);
        ok = false;
    }
    std::uint64_t nextSequence = 0;
    std::uint64_t chainedRecords = 0;
    std::string previousHash;
    FileState verifiedState;
    bool locked = false;
    if (ok && ::flock(fd, LOCK_EX) != 0)
    {
        saved = errno;
        reason = saved == 0 ? "SUPERVISOR_AUDIT_LOCK_FAILED" : std::strerror(saved);
        ok = false;
    }
    else if (ok) locked = true;
    if (ok)
    {
        struct stat lockedPathMetadata;
        char lockedCanonical[PATH_MAX];
        if (::fstat(fd, &metadata) != 0 ||
            ::lstat(path.c_str(), &lockedPathMetadata) != 0 ||
            ::realpath(path.c_str(), lockedCanonical) == nullptr ||
            !S_ISREG(metadata.st_mode) ||
            metadata.st_dev != lockedPathMetadata.st_dev ||
            metadata.st_ino != lockedPathMetadata.st_ino ||
            metadata.st_nlink != 1 ||
            metadata.st_uid != ::geteuid() ||
            (metadata.st_mode & 0077) != 0 ||
            std::strcmp(canonical, lockedCanonical) != 0)
        {
            reason = "SUPERVISOR_AUDIT_PATH_IDENTITY_CHANGED";
            ok = false;
        }
        else
        {
            verifiedState = CaptureFileState(metadata);
            if (verifiedState.fileSize > kMaximumAuditJournalBytes)
            {
                reason = "SUPERVISOR_AUDIT_SIZE_LIMIT";
                ok = false;
            }
        }
    }
    std::uint64_t archivedRecords = 0;
    if (ok) ok = VerifyHistory(fd, path, archivedRecords, reason);
    if (ok)
        ok = LoadChain(fd, verifiedState.fileSize, nextSequence,
            previousHash, chainedRecords, reason);
    if (ok)
    {
        struct stat afterVerification;
        if (::fstat(fd, &afterVerification) != 0 ||
            !SameFileState(verifiedState, CaptureFileState(afterVerification)))
        {
            reason = "SUPERVISOR_AUDIT_CONCURRENT_MODIFICATION";
            ok = false;
        }
    }
    if (locked) ::flock(fd, LOCK_UN);
    if (!ok)
    {
        ::close(fd);
        if (reason.empty())
            reason = saved == 0 ?
                "SUPERVISOR_AUDIT_INIT_FAILED" : std::strerror(saved);
        return false;
    }
    m_fd = fd;
    m_path = path;
    m_canonicalPath = canonical;
    m_device = static_cast<std::uint64_t>(metadata.st_dev);
    m_inode = static_cast<std::uint64_t>(metadata.st_ino);
    m_fileState = verifiedState;
    m_nextSequence = nextSequence;
    m_chainedRecords = chainedRecords;
    m_previousHash = previousHash;
    m_cacheValid = true;
    reason.clear();
    return true;
}

bool SessionSupervisorAuditJournal::Append(const SessionSupervisorRequest& request,
    const std::string& issuer, const std::string& phase, const std::string& outcome,
    std::uint64_t leaseGeneration, std::string& reason)
{
    // Tokens and replacement tokens are deliberately excluded.  The stable,
    // server-bound Agent/session identity is sufficient for correlation.
    const std::string recoveryTarget =
        request.operation == SessionSupervisorOperation::RecoveryQuery ?
            "\ntarget_command_id=" + request.targetCommandId +
            "\nrequire_paper_finalization=" +
            (request.requirePaperFinalization ? "1" : "0") :
            std::string();
    const std::string payload =
        "operation=" + OperationName(request.operation) + "\n" +
        "phase=" + phase + "\n" +
        "outcome=" + outcome + "\n" +
        "issuer=" + issuer + "\n" +
        "agent_id=" + request.agentId + "\n" +
        "session_id=" + request.sessionId + "\n" +
        "lease_generation=" + std::to_string(leaseGeneration) +
        recoveryTarget;
    const bool admission = request.operation == SessionSupervisorOperation::Provision ||
        request.operation == SessionSupervisorOperation::Rotate;
    return AppendRecord("session-supervisor", payload, reason,
        admission ? RecordClass::Admission : RecordClass::Safety);
}

bool SessionSupervisorAuditJournal::AppendToolDecision(
    const ToolDecisionAuditRecord& record, std::string& reason)
{
    const std::string payload =
        "daemon_identity=" + record.daemonIdentity + "\n" +
        "peer_uid=" + (record.peerCredentialAvailable ?
            std::to_string(record.peerUid) : std::string()) + "\n" +
        "execution_domain=" + record.executionDomain + "\n" +
        "agent_id=" + record.agentId + "\n" +
        "session_id=" + record.sessionId + "\n" +
        "account=" + record.account + "\n" +
        "venue=" + record.venue + "\n" +
        "environment=" + record.environment + "\n" +
        "tool_call_id=" + record.toolCallId + "\n" +
        "tool_name=" + record.toolName + "\n" +
        "expected_schema_hash=" + record.expectedSchemaHash + "\n" +
        "request_fingerprint=" + record.requestFingerprint + "\n" +
        "phase=" + record.phase + "\n" +
        "outcome=" + record.outcome + "\n" +
        "reason_code=" + record.reasonCode;
    const bool observation = record.observational && record.phase == "outcome";
    const bool boundExit = !observation && record.peerCredentialAvailable && !record.agentId.empty() &&
        !record.sessionId.empty() && (record.toolName == "trade.cancel_order" ||
        record.toolName == "trade.flatten_position");
    return AppendRecord("tool-decision", payload, reason,
        boundExit ? RecordClass::Safety : (observation ? RecordClass::Observation : RecordClass::Admission));
}

bool SessionSupervisorAuditJournal::AppendRecord(
    const std::string& recordType, const std::string& payload, std::string& reason,
    RecordClass recordClass)
{
    std::lock_guard<std::mutex> lock(m_mutex);
    if (m_fd < 0) { reason = "SUPERVISOR_AUDIT_NOT_INITIALIZED"; return false; }
    if (::flock(m_fd, LOCK_EX) != 0) { reason = std::strerror(errno); return false; }

    FileState currentState;
    bool ok = ValidateOpenFile(currentState, reason);
    std::uint64_t nextSequence = m_nextSequence;
    std::uint64_t chainedRecords = m_chainedRecords;
    std::string previousHash = m_previousHash;
    bool cacheStateVerified = ok && m_cacheValid &&
        SameFileState(currentState, m_fileState);
    // Normal appends reuse the verified chain head.  Any peer writer,
    // truncation, or same-size rewrite changes the observed file state and
    // forces a full chain verification before another append is accepted.
    const bool fullVerificationRequired = ok && !cacheStateVerified;
    if (fullVerificationRequired)
    {
        m_cacheValid = false;
        cacheStateVerified = false;
        ok = LoadChain(m_fd, currentState.fileSize, nextSequence,
            previousHash, chainedRecords, reason);
        FileState afterVerification;
        if (ok)
            ok = ValidateOpenFile(afterVerification, reason);
        if (ok && !SameFileState(currentState, afterVerification))
        {
            reason = "SUPERVISOR_AUDIT_CONCURRENT_MODIFICATION";
            ok = false;
        }
        if (ok)
        {
            currentState = afterVerification;
            m_fileState = currentState;
            m_nextSequence = nextSequence;
            m_chainedRecords = chainedRecords;
            m_previousHash = previousHash;
            m_cacheValid = true;
            cacheStateVerified = true;
        }
    }
    const std::uint64_t nowMs = static_cast<std::uint64_t>(
        std::chrono::duration_cast<std::chrono::milliseconds>(
            std::chrono::system_clock::now().time_since_epoch()).count());
    const std::string unsignedLine = std::string(kAuditMagic) + "\t" +
        std::to_string(nextSequence) + "\t" + previousHash + "\t" +
        std::to_string(nowMs) + "\t" + recordType + "\t" + HexEncode(payload);
    const std::string recordHash = Sha256Hex(unsignedLine);
    const std::string line = unsignedLine + "\t" + recordHash + "\n";
    if (ok && nextSequence == std::numeric_limits<std::uint64_t>::max())
    { reason = "SUPERVISOR_AUDIT_SEQUENCE_EXHAUSTED"; ok = false; }
    if (ok && recordHash.empty())
    {
        reason = "SUPERVISOR_AUDIT_DIGEST_FAILED";
        ok = false;
    }
    if (ok && line.size() > kMaximumAuditLineBytes)
    {
        reason = "SUPERVISOR_AUDIT_RECORD_TOO_LARGE";
        ok = false;
    }
    const std::uint64_t reserve = recordClass == RecordClass::Safety ? 0 :
        (recordClass == RecordClass::Admission ? m_safetyReserveBytes : 2 * m_safetyReserveBytes);
    const std::uint64_t limit = m_maximumBytes - reserve;
    if (ok && (currentState.fileSize > limit || line.size() > limit - currentState.fileSize))
    {
        ::flock(m_fd, LOCK_UN);
        if (recordClass == RecordClass::Observation)
        {
            // Only routine observation is shed. This is explicitly counted,
            // not represented as a durable record or a false failed read.
            if (m_observationsShed != std::numeric_limits<std::uint64_t>::max()) ++m_observationsShed;
            reason = "SUPERVISOR_AUDIT_OBSERVATION_SHED";
            return true;
        }
        reason = recordClass == RecordClass::Admission ?
            "SUPERVISOR_AUDIT_EXIT_RESERVE_REQUIRED" : "SUPERVISOR_AUDIT_SIZE_LIMIT";
        return false;
    }
    bool writeAttempted = false;
    std::size_t offset = 0;
    while (ok && offset < line.size())
    {
        writeAttempted = true;
        const ssize_t written = ::write(m_fd, line.data() + offset, line.size() - offset);
        if (written < 0 && errno == EINTR) continue;
        if (written <= 0) { ok = false; break; }
        offset += static_cast<std::size_t>(written);
    }
    if (ok) ok = ::fdatasync(m_fd) == 0;

    FileState appendedState;
    if (ok)
        ok = ValidateOpenFile(appendedState, reason);
    if (ok && appendedState.fileSize != currentState.fileSize + line.size())
    {
        reason = "SUPERVISOR_AUDIT_CONCURRENT_MODIFICATION";
        ok = false;
    }
    // Confirm that O_APPEND placed this exact record at the expected offset.
    // This also catches writers that ignore the advisory flock.
    std::string observedLine;
    observedLine.resize(line.size());
    std::size_t observed = 0;
    while (ok && observed < observedLine.size())
    {
        const ssize_t count = ::pread(m_fd, &observedLine[observed],
            observedLine.size() - observed,
            static_cast<off_t>(currentState.fileSize + observed));
        if (count < 0 && errno == EINTR) continue;
        if (count <= 0) { ok = false; break; }
        observed += static_cast<std::size_t>(count);
    }
    if (ok && observedLine != line)
    {
        reason = "SUPERVISOR_AUDIT_CONCURRENT_MODIFICATION";
        ok = false;
    }
    const int saved = errno;
    ::flock(m_fd, LOCK_UN);
    if (!ok)
    {
        if (writeAttempted || !cacheStateVerified) m_cacheValid = false;
        if (reason.empty())
            reason = saved == 0 ? "SUPERVISOR_AUDIT_WRITE_FAILED" : std::strerror(saved);
        return false;
    }
    m_fileState = appendedState;
    m_nextSequence = nextSequence + 1;
    m_chainedRecords = chainedRecords + 1;
    m_previousHash = recordHash;
    m_cacheValid = true;
    reason.clear();
    return true;
}

bool SessionSupervisorAuditJournal::LoadChain(
    int fd, std::uint64_t fileSize, std::uint64_t& nextSequence,
    std::string& previousHash, std::uint64_t& chainedRecords,
    std::string& reason)
{
    nextSequence = 1;
    previousHash = kZeroHash;
    chainedRecords = 0;

    EVP_MD_CTX* legacyDigest = EVP_MD_CTX_new();
    if (legacyDigest == nullptr ||
        EVP_DigestInit_ex(legacyDigest, EVP_sha256(), nullptr) != 1)
    {
        EVP_MD_CTX_free(legacyDigest);
        reason = "SUPERVISOR_AUDIT_DIGEST_FAILED";
        return false;
    }

    std::string pending;
    bool legacySeen = false;
    bool chainStarted = false;
    std::uint64_t offset = 0;
    char buffer[8192];
    while (offset < fileSize)
    {
        const std::size_t requested = static_cast<std::size_t>(
            std::min<std::uint64_t>(sizeof(buffer), fileSize - offset));
        const ssize_t count = ::pread(fd, buffer, requested, static_cast<off_t>(offset));
        if (count < 0 && errno == EINTR) continue;
        if (count <= 0)
        {
            EVP_MD_CTX_free(legacyDigest);
            reason = "SUPERVISOR_AUDIT_READ_FAILED";
            return false;
        }
        pending.append(buffer, static_cast<std::size_t>(count));
        offset += static_cast<std::uint64_t>(count);
        if (pending.size() > kMaximumAuditLineBytes &&
            pending.find('\n') == std::string::npos)
        {
            EVP_MD_CTX_free(legacyDigest);
            reason = "SUPERVISOR_AUDIT_LINE_TOO_LARGE";
            return false;
        }

        std::size_t newline = std::string::npos;
        while ((newline = pending.find('\n')) != std::string::npos)
        {
            const std::string rawLine = pending.substr(0, newline + 1);
            const std::string line = pending.substr(0, newline);
            pending.erase(0, newline + 1);
            if (line.compare(0, sizeof(kAuditMagic) - 1, kAuditMagic) != 0)
            {
                if (chainStarted)
                {
                    EVP_MD_CTX_free(legacyDigest);
                    reason = "SUPERVISOR_AUDIT_LEGACY_RECORD_AFTER_CHAIN";
                    return false;
                }
                legacySeen = true;
                if (EVP_DigestUpdate(legacyDigest, rawLine.data(), rawLine.size()) != 1)
                {
                    EVP_MD_CTX_free(legacyDigest);
                    reason = "SUPERVISOR_AUDIT_DIGEST_FAILED";
                    return false;
                }
                continue;
            }

            if (!chainStarted)
            {
                if (legacySeen)
                {
                    unsigned char digest[EVP_MAX_MD_SIZE];
                    unsigned int length = 0;
                    if (EVP_DigestFinal_ex(legacyDigest, digest, &length) != 1)
                    {
                        EVP_MD_CTX_free(legacyDigest);
                        reason = "SUPERVISOR_AUDIT_DIGEST_FAILED";
                        return false;
                    }
                    previousHash = HexEncode(std::string(
                        reinterpret_cast<char*>(digest), length));
                }
                chainStarted = true;
            }

            if (line.size() > kMaximumAuditLineBytes)
            {
                EVP_MD_CTX_free(legacyDigest);
                reason = "SUPERVISOR_AUDIT_LINE_TOO_LARGE";
                return false;
            }
            const std::vector<std::string> fields = SplitTabs(line);
            std::uint64_t sequence = 0;
            std::uint64_t timestamp = 0;
            if (fields.size() != 7 || fields[0] != kAuditMagic ||
                !ParseUnsigned(fields[1], sequence) ||
                sequence != nextSequence ||
                fields[2] != previousHash ||
                !IsLowerHex(fields[2], 64) ||
                !ParseUnsigned(fields[3], timestamp) ||
                fields[4].empty() ||
                !IsLowerHex(fields[5], fields[5].size()) ||
                fields[5].size() % 2 != 0 ||
                !IsLowerHex(fields[6], 64))
            {
                EVP_MD_CTX_free(legacyDigest);
                reason = "SUPERVISOR_AUDIT_CHAIN_RECORD_INVALID";
                return false;
            }
            const std::size_t hashSeparator = line.rfind('\t');
            if (hashSeparator == std::string::npos ||
                Sha256Hex(line.substr(0, hashSeparator)) != fields[6])
            {
                EVP_MD_CTX_free(legacyDigest);
                reason = "SUPERVISOR_AUDIT_CHAIN_HASH_MISMATCH";
                return false;
            }
            previousHash = fields[6];
            ++nextSequence;
            ++chainedRecords;
        }
    }
    if (!pending.empty())
    {
        EVP_MD_CTX_free(legacyDigest);
        reason = "SUPERVISOR_AUDIT_TRUNCATED_RECORD";
        return false;
    }
    if (!chainStarted && legacySeen)
    {
        unsigned char digest[EVP_MAX_MD_SIZE];
        unsigned int length = 0;
        if (EVP_DigestFinal_ex(legacyDigest, digest, &length) != 1)
        {
            EVP_MD_CTX_free(legacyDigest);
            reason = "SUPERVISOR_AUDIT_DIGEST_FAILED";
            return false;
        }
        previousHash = HexEncode(std::string(
            reinterpret_cast<char*>(digest), length));
    }
    EVP_MD_CTX_free(legacyDigest);
    reason.clear();
    return true;
}

bool SessionSupervisorAuditJournal::Verify(
    const std::string& path, std::uint64_t& chainedRecords, std::string& reason)
{
    chainedRecords = 0;
    if (path.empty()) { reason = "SUPERVISOR_AUDIT_PATH_REQUIRED"; return false; }
    const int fd = ::open(path.c_str(), O_RDONLY | O_CLOEXEC | O_NOFOLLOW);
    if (fd < 0) { reason = std::strerror(errno); return false; }
    struct stat descriptorMetadata;
    struct stat pathMetadata;
    bool ok = ::fstat(fd, &descriptorMetadata) == 0 &&
        ::lstat(path.c_str(), &pathMetadata) == 0 &&
        S_ISREG(descriptorMetadata.st_mode) && S_ISREG(pathMetadata.st_mode) &&
        descriptorMetadata.st_dev == pathMetadata.st_dev &&
        descriptorMetadata.st_ino == pathMetadata.st_ino &&
        descriptorMetadata.st_nlink == 1 &&
        descriptorMetadata.st_uid == ::geteuid() &&
        (descriptorMetadata.st_mode & 0077) == 0;
    if (!ok)
        reason = "SUPERVISOR_AUDIT_UNSAFE_FILE";
    std::uint64_t nextSequence = 0;
    std::string previousHash;
    bool locked = false;
    if (ok && ::flock(fd, LOCK_SH) != 0)
    {
        reason = std::strerror(errno);
        ok = false;
    }
    else if (ok) locked = true;
    if (ok)
    {
        ok = ::fstat(fd, &descriptorMetadata) == 0 &&
            ::lstat(path.c_str(), &pathMetadata) == 0 &&
            S_ISREG(descriptorMetadata.st_mode) &&
            S_ISREG(pathMetadata.st_mode) &&
            descriptorMetadata.st_dev == pathMetadata.st_dev &&
            descriptorMetadata.st_ino == pathMetadata.st_ino &&
            descriptorMetadata.st_nlink == 1 &&
            descriptorMetadata.st_uid == ::geteuid() &&
            (descriptorMetadata.st_mode & 0077) == 0;
        if (!ok) reason = "SUPERVISOR_AUDIT_PATH_IDENTITY_CHANGED";
    }
    FileState verifiedState;
    if (ok)
    {
        verifiedState = CaptureFileState(descriptorMetadata);
        if (verifiedState.fileSize > kMaximumAuditJournalBytes)
        {
            reason = "SUPERVISOR_AUDIT_SIZE_LIMIT";
            ok = false;
        }
    }
    std::uint64_t archivedRecords = 0;
    if (ok) ok = VerifyHistory(fd, path, archivedRecords, reason);
    if (ok)
        ok = LoadChain(fd, verifiedState.fileSize, nextSequence,
            previousHash, chainedRecords, reason);
    if (ok)
    {
        struct stat afterVerification;
        if (::fstat(fd, &afterVerification) != 0 ||
            !SameFileState(verifiedState, CaptureFileState(afterVerification)))
        {
            reason = "SUPERVISOR_AUDIT_CONCURRENT_MODIFICATION";
            ok = false;
        }
    }
    if (locked) ::flock(fd, LOCK_UN);
    ::close(fd);
    if (ok && archivedRecords > std::numeric_limits<std::uint64_t>::max() - chainedRecords)
    { reason = "SUPERVISOR_AUDIT_HISTORY_COUNT_OVERFLOW"; ok = false; }
    if (ok) { chainedRecords += archivedRecords; reason.clear(); }
    return ok;
}

SessionSupervisorAuditJournal::FileState
SessionSupervisorAuditJournal::CaptureFileState(const struct stat& metadata)
{
    FileState state;
    state.fileSize = static_cast<std::uint64_t>(metadata.st_size);
#if defined(__APPLE__)
    state.modifiedSeconds = static_cast<std::int64_t>(metadata.st_mtimespec.tv_sec);
    state.modifiedNanoseconds = static_cast<std::int64_t>(metadata.st_mtimespec.tv_nsec);
    state.changedSeconds = static_cast<std::int64_t>(metadata.st_ctimespec.tv_sec);
    state.changedNanoseconds = static_cast<std::int64_t>(metadata.st_ctimespec.tv_nsec);
#else
    state.modifiedSeconds = static_cast<std::int64_t>(metadata.st_mtim.tv_sec);
    state.modifiedNanoseconds = static_cast<std::int64_t>(metadata.st_mtim.tv_nsec);
    state.changedSeconds = static_cast<std::int64_t>(metadata.st_ctim.tv_sec);
    state.changedNanoseconds = static_cast<std::int64_t>(metadata.st_ctim.tv_nsec);
#endif
    return state;
}

bool SessionSupervisorAuditJournal::SameFileState(
    const FileState& left, const FileState& right)
{
    return left.fileSize == right.fileSize &&
        left.modifiedSeconds == right.modifiedSeconds &&
        left.modifiedNanoseconds == right.modifiedNanoseconds &&
        left.changedSeconds == right.changedSeconds &&
        left.changedNanoseconds == right.changedNanoseconds;
}

bool SessionSupervisorAuditJournal::ValidateOpenFile(
    FileState& state, std::string& reason) const
{
    struct stat descriptorMetadata;
    struct stat pathMetadata;
    char canonical[PATH_MAX];
    if (::fstat(m_fd, &descriptorMetadata) != 0 ||
        ::lstat(m_path.c_str(), &pathMetadata) != 0 ||
        ::realpath(m_path.c_str(), canonical) == nullptr)
    {
        reason = "SUPERVISOR_AUDIT_PATH_UNAVAILABLE";
        return false;
    }
    if (!S_ISREG(descriptorMetadata.st_mode) ||
        !S_ISREG(pathMetadata.st_mode) ||
        descriptorMetadata.st_dev != pathMetadata.st_dev ||
        descriptorMetadata.st_ino != pathMetadata.st_ino ||
        static_cast<std::uint64_t>(descriptorMetadata.st_dev) != m_device ||
        static_cast<std::uint64_t>(descriptorMetadata.st_ino) != m_inode ||
        descriptorMetadata.st_nlink != 1 ||
        descriptorMetadata.st_uid != ::geteuid() ||
        (descriptorMetadata.st_mode & 0077) != 0 ||
        m_canonicalPath != canonical)
    {
        reason = "SUPERVISOR_AUDIT_PATH_IDENTITY_CHANGED";
        return false;
    }
    state = CaptureFileState(descriptorMetadata);
    if (state.fileSize > kMaximumAuditJournalBytes)
    {
        reason = "SUPERVISOR_AUDIT_SIZE_LIMIT";
        return false;
    }
    reason.clear();
    return true;
}

std::string SessionSupervisorAuditJournal::HexEncode(const std::string& value)
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

std::string SessionSupervisorAuditJournal::OperationName(SessionSupervisorOperation operation)
{
    if (operation == SessionSupervisorOperation::Provision) return "provision";
    if (operation == SessionSupervisorOperation::Renew) return "renew";
    if (operation == SessionSupervisorOperation::Rotate) return "rotate";
    if (operation == SessionSupervisorOperation::RecoveryQuery)
        return "recovery-query";
    if (operation == SessionSupervisorOperation::PaperFinalize)
        return "paper-finalize";
    if (operation == SessionSupervisorOperation::PaperFinalizeAck)
        return "paper-finalize-ack";
    if (operation == SessionSupervisorOperation::PaperTerminalizeAck)
        return "paper-terminalize-ack";
    if (operation == SessionSupervisorOperation::PaperTerminalWitnessPrepare)
        return "paper-terminal-witness-prepare";
    if (operation == SessionSupervisorOperation::PaperTerminalWitnessAck)
        return "paper-terminal-witness-ack";
    return "revoke";
}

std::string SessionSupervisorAuditJournal::Sha256Hex(const std::string& value)
{
    unsigned char digest[EVP_MAX_MD_SIZE];
    unsigned int length = 0;
    EVP_MD_CTX* context = EVP_MD_CTX_new();
    if (context == nullptr) return std::string();
    const bool ok = EVP_DigestInit_ex(context, EVP_sha256(), nullptr) == 1 &&
        EVP_DigestUpdate(context, value.data(), value.size()) == 1 &&
        EVP_DigestFinal_ex(context, digest, &length) == 1;
    EVP_MD_CTX_free(context);
    if (!ok) return std::string();
    return HexEncode(std::string(reinterpret_cast<char*>(digest), length));
}

namespace {
class AuditFd
{
public:
    explicit AuditFd(int value = -1) : fd(value) {}
    ~AuditFd() { if (fd >= 0) ::close(fd); }
    AuditFd(const AuditFd&) = delete;
    AuditFd& operator=(const AuditFd&) = delete;
    int fd;
};

bool AuditPrivateFile(int fd, struct stat& metadata)
{
    return fd >= 0 && ::fstat(fd, &metadata) == 0 && S_ISREG(metadata.st_mode) &&
        metadata.st_nlink == 1 && metadata.st_uid == ::geteuid() &&
        (metadata.st_mode & 0077) == 0 && metadata.st_size >= 0 &&
        static_cast<std::uint64_t>(metadata.st_size) <= kMaximumAuditJournalBytes;
}

bool AuditHashFile(int fd, std::uint64_t size, std::string& hash, int copyFd = -1)
{
    EVP_MD_CTX* context = EVP_MD_CTX_new();
    if (!context) return false;
    bool ok = EVP_DigestInit_ex(context, EVP_sha256(), nullptr) == 1;
    std::uint64_t offset = 0;
    char bytes[65536];
    while (ok && offset < size)
    {
        const ssize_t count = ::pread(fd, bytes, std::min<std::uint64_t>(sizeof(bytes), size - offset), offset);
        if (count < 0 && errno == EINTR) continue;
        if (count <= 0) { ok = false; break; }
        ok = EVP_DigestUpdate(context, bytes, count) == 1;
        std::size_t written = 0;
        while (ok && copyFd >= 0 && written < static_cast<std::size_t>(count))
        {
            const ssize_t n = ::write(copyFd, bytes + written, count - written);
            if (n < 0 && errno == EINTR) continue;
            if (n <= 0) { ok = false; break; }
            written += n;
        }
        offset += count;
    }
    unsigned char digest[EVP_MAX_MD_SIZE]; unsigned int length = 0;
    if (ok) ok = EVP_DigestFinal_ex(context, digest, &length) == 1 && length == 32;
    EVP_MD_CTX_free(context);
    hash.clear();
    if (ok)
    {
        const char hex[] = "0123456789abcdef";
        for (unsigned i = 0; i < length; ++i)
        { hash.push_back(hex[digest[i] >> 4]); hash.push_back(hex[digest[i] & 15]); }
    }
    return ok;
}

// Each new active file starts with one legacy-compatible anchor. HJA2 hashes
// bind that exact anchor; current readers also require its full retained chain.
bool AuditAnchor(int fd, std::string& digest, std::uint64_t& records)
{
    char data[192]; ssize_t count;
    do { count = ::pread(fd, data, sizeof(data), 0); } while (count < 0 && errno == EINTR);
    if (count < 0) return false;
    std::string prefix(data, static_cast<std::size_t>(count));
    digest.clear(); records = 0;
    if (prefix.compare(0, 4, "HJA3") != 0) return true;
    const auto end = prefix.find('\n');
    if (end == std::string::npos) return false;
    const auto fields = SplitTabs(prefix.substr(0, end));
    if (fields.size() != 3 || fields[0] != "HJA3" ||
        !IsLowerHex(fields[1], 64) || !ParseUnsigned(fields[2], records) || records == 0)
        return false;
    digest = fields[1];
    return true;
}
}

SessionSupervisorAuditCapacity SessionSupervisorAuditJournal::CapacitySnapshot() const
{
    std::lock_guard<std::mutex> lock(m_mutex);
    SessionSupervisorAuditCapacity result;
    FileState state; std::string reason;
    result.known = m_fd >= 0 && m_cacheValid && ValidateOpenFile(state, reason) &&
        SameFileState(state, m_fileState);
    result.bytes = result.known ? state.fileSize : 0;
    result.maximumBytes = m_maximumBytes;
    result.safetyReserveBytes = m_safetyReserveBytes;
    result.observationsShed = m_observationsShed;
    return result;
}

bool SessionSupervisorAuditJournal::VerifyHistory(int activeFd, const std::string& path,
    std::uint64_t& records, std::string& reason)
{
    records = 0;
    std::string digest;
    std::uint64_t expected = 0;
    if (!AuditAnchor(activeFd, digest, expected))
    { reason = "SUPERVISOR_AUDIT_SEGMENT_ANCHOR_INVALID"; return false; }
    if (digest.empty()) return true;
    records = expected;
    AuditFd directory(::open((path + ".segments").c_str(), O_RDONLY | O_DIRECTORY | O_CLOEXEC | O_NOFOLLOW));
    struct stat metadata;
    if (directory.fd < 0 || ::fstat(directory.fd, &metadata) != 0 ||
        metadata.st_uid != ::geteuid() || (metadata.st_mode & 0077) != 0)
    { reason = "SUPERVISOR_AUDIT_SEGMENT_DIRECTORY_UNSAFE"; return false; }
    std::set<std::string> visited;
    while (!digest.empty())
    {
        if (!visited.insert(digest).second)
        { reason = "SUPERVISOR_AUDIT_SEGMENT_CYCLE"; return false; }
        AuditFd segment(::openat(directory.fd, (digest + ".hja2").c_str(),
            O_RDONLY | O_CLOEXEC | O_NOFOLLOW | O_NONBLOCK));
        struct stat before, after;
        std::string actual, previous;
        std::uint64_t next = 0, count = 0, parentCount = 0;
        if (!AuditPrivateFile(segment.fd, before) ||
            !AuditHashFile(segment.fd, before.st_size, actual) || actual != digest ||
            !LoadChain(segment.fd, before.st_size, next, previous, count, reason) ||
            !AuditAnchor(segment.fd, digest, parentCount) ||
            parentCount > expected || count != expected - parentCount ||
            !AuditPrivateFile(segment.fd, after) ||
            !SameFileState(CaptureFileState(before), CaptureFileState(after)))
        { reason = "SUPERVISOR_AUDIT_SEGMENT_INVALID"; return false; }
        expected = parentCount;
    }
    if (expected != 0) { reason = "SUPERVISOR_AUDIT_SEGMENT_COUNT_INVALID"; return false; }
    reason.clear();
    return true;
}

bool SessionSupervisorAuditJournal::SealSegment(const std::string& path, std::string& reason)
{
#if !defined(__linux__) || !defined(SYS_renameat2)
    (void)path; reason = "SUPERVISOR_AUDIT_SEAL_PLATFORM_UNSUPPORTED"; return false;
#else
    if (path.empty() || path[0] != '/' || path.back() == '/')
    { reason = "SUPERVISOR_AUDIT_ABSOLUTE_PATH_REQUIRED"; return false; }
    const auto separator = path.rfind('/');
    const std::string parent = separator == 0 ? "/" : path.substr(0, separator);
    AuditFd directory(::open(parent.c_str(), O_RDONLY | O_DIRECTORY | O_CLOEXEC | O_NOFOLLOW));
    if (directory.fd < 0 || ::flock(directory.fd, LOCK_EX | LOCK_NB) != 0)
    { reason = "SUPERVISOR_AUDIT_MAINTENANCE_BUSY"; return false; }
    AuditFd source(::open(path.c_str(), O_RDONLY | O_CLOEXEC | O_NOFOLLOW | O_NONBLOCK));
    struct stat before, named;
    if (!AuditPrivateFile(source.fd, before) || ::flock(source.fd, LOCK_EX | LOCK_NB) != 0 ||
        ::lstat(path.c_str(), &named) != 0 || before.st_dev != named.st_dev || before.st_ino != named.st_ino)
    { reason = "SUPERVISOR_AUDIT_UNSAFE_OR_BUSY"; return false; }
    std::uint64_t archived = 0, local = 0, next = 0;
    std::string previous;
    if (!VerifyHistory(source.fd, path, archived, reason) ||
        !LoadChain(source.fd, before.st_size, next, previous, local, reason)) return false;
    if (local == 0)
    {
        // Retrying a published-but-unsynced anchor must complete directory
        // durability instead of turning the no-op into an unearned success.
        if (::fsync(directory.fd) != 0)
        { reason = "SUPERVISOR_AUDIT_PUBLICATION_INDETERMINATE"; return false; }
        reason.clear(); return true;
    }
    if (archived > std::numeric_limits<std::uint64_t>::max() - local)
    { reason = "SUPERVISOR_AUDIT_HISTORY_COUNT_OVERFLOW"; return false; }
    const std::string archivePath = path + ".segments";
    if (::mkdir(archivePath.c_str(), 0700) != 0 && errno != EEXIST)
    { reason = "SUPERVISOR_AUDIT_SEGMENT_CREATE_FAILED"; return false; }
    AuditFd archiveDir(::open(archivePath.c_str(), O_RDONLY | O_DIRECTORY | O_CLOEXEC | O_NOFOLLOW));
    struct stat archiveMetadata;
    if (archiveDir.fd < 0 || ::fstat(archiveDir.fd, &archiveMetadata) != 0 ||
        archiveMetadata.st_uid != ::geteuid() || (archiveMetadata.st_mode & 0077) != 0)
    { reason = "SUPERVISOR_AUDIT_SEGMENT_DIRECTORY_UNSAFE"; return false; }
    std::string stageName = archivePath + "/.stage-XXXXXX";
    std::vector<char> stage(stageName.begin(), stageName.end()); stage.push_back(0);
    AuditFd copy(::mkstemp(stage.data()));
    std::string digest;
    if (copy.fd < 0 || ::fcntl(copy.fd, F_SETFD, FD_CLOEXEC) != 0 ||
        !AuditHashFile(source.fd, before.st_size, digest, copy.fd) || ::fsync(copy.fd) != 0)
    { ::unlink(stage.data()); reason = "SUPERVISOR_AUDIT_SEGMENT_SYNC_FAILED"; return false; }
    const std::string sealed = archivePath + "/" + digest + ".hja2";
    if (::syscall(SYS_renameat2, AT_FDCWD, stage.data(), AT_FDCWD, sealed.c_str(), RENAME_NOREPLACE) != 0)
    {
        const int error = errno; ::unlink(stage.data());
        AuditFd existing(::open(sealed.c_str(), O_RDONLY | O_NOFOLLOW | O_CLOEXEC | O_NONBLOCK));
        struct stat current; std::string existingHash;
        if (error != EEXIST || !AuditPrivateFile(existing.fd, current) ||
            !AuditHashFile(existing.fd, current.st_size, existingHash) || existingHash != digest)
        { reason = "SUPERVISOR_AUDIT_SEGMENT_PUBLICATION_FAILED"; return false; }
    }
    // The archive and its directory are durable before replacing the active
    // path. A crash can leave an unreferenced segment, never missing history.
    if (::fsync(archiveDir.fd) != 0 || ::fsync(directory.fd) != 0)
    { reason = "SUPERVISOR_AUDIT_SEGMENT_DIRECTORY_SYNC_FAILED"; return false; }
    const std::string anchor = "HJA3\t" + digest + "\t" + std::to_string(archived + local) + "\n";
    stageName = path + ".rotate-XXXXXX";
    stage.assign(stageName.begin(), stageName.end()); stage.push_back(0);
    AuditFd active(::mkstemp(stage.data()));
    if (active.fd < 0 || ::fcntl(active.fd, F_SETFD, FD_CLOEXEC) != 0)
    { ::unlink(stage.data()); reason = "SUPERVISOR_AUDIT_ACTIVE_STAGE_FAILED"; return false; }
    std::size_t written = 0;
    while (written < anchor.size())
    {
        const ssize_t n = ::write(active.fd, anchor.data() + written, anchor.size() - written);
        if (n < 0 && errno == EINTR) continue;
        if (n <= 0) { ::unlink(stage.data()); reason = "SUPERVISOR_AUDIT_ACTIVE_WRITE_FAILED"; return false; }
        written += n;
    }
    struct stat after;
    if (::fsync(active.fd) != 0 || ::fstat(source.fd, &after) != 0 ||
        !SameFileState(CaptureFileState(before), CaptureFileState(after)) ||
        ::lstat(path.c_str(), &named) != 0 || before.st_dev != named.st_dev || before.st_ino != named.st_ino)
    { ::unlink(stage.data()); reason = "SUPERVISOR_AUDIT_SOURCE_CHANGED_OR_SYNC_FAILED"; return false; }
    if (::rename(stage.data(), path.c_str()) != 0)
    { ::unlink(stage.data()); reason = "SUPERVISOR_AUDIT_ACTIVE_RENAME_FAILED"; return false; }
    if (::fsync(directory.fd) != 0)
    { reason = "SUPERVISOR_AUDIT_PUBLICATION_INDETERMINATE"; return false; }
    reason.clear();
    return true;
#endif
}
