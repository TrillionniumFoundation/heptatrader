#include "session_supervisor_lease_store.h"

#include <algorithm>
#include <cerrno>
#include <chrono>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <fcntl.h>
#include <limits>
#include <openssl/evp.h>
#include <openssl/rand.h>
#include <set>
#include <sstream>
#if defined(__linux__)
#include <linux/fs.h>
#endif
#include <sys/file.h>
#include <sys/stat.h>
#include <sys/syscall.h>
#include <unistd.h>

namespace {

const char* kAad = "HeptaTrader supervisor lease store HSL2";
const std::size_t kMaximumLeaseStoreBytes = 2 * 1024 * 1024;
const std::size_t kMaximumLeaseKeyBytes = 65;

void SnapshotTimes(const struct stat& metadata,
    std::int64_t& mtimeSec, std::int64_t& mtimeNsec,
    std::int64_t& ctimeSec, std::int64_t& ctimeNsec)
{
#if defined(__APPLE__)
    mtimeSec = static_cast<std::int64_t>(metadata.st_mtimespec.tv_sec);
    mtimeNsec = static_cast<std::int64_t>(metadata.st_mtimespec.tv_nsec);
    ctimeSec = static_cast<std::int64_t>(metadata.st_ctimespec.tv_sec);
    ctimeNsec = static_cast<std::int64_t>(metadata.st_ctimespec.tv_nsec);
#else
    mtimeSec = static_cast<std::int64_t>(metadata.st_mtim.tv_sec);
    mtimeNsec = static_cast<std::int64_t>(metadata.st_mtim.tv_nsec);
    ctimeSec = static_cast<std::int64_t>(metadata.st_ctim.tv_sec);
    ctimeNsec = static_cast<std::int64_t>(metadata.st_ctim.tv_nsec);
#endif
}

bool ParseUnsigned(const std::string& value, std::uint64_t maximum, std::uint64_t& parsed)
{
    if (value.empty()) return false;
    char* end = nullptr;
    errno = 0;
    const unsigned long long number = std::strtoull(value.c_str(), &end, 10);
    if (errno != 0 || end == value.c_str() || *end != '\0' || number > maximum) return false;
    parsed = static_cast<std::uint64_t>(number);
    return true;
}

std::vector<std::string> SplitTabs(const std::string& line)
{
    std::vector<std::string> fields;
    std::size_t start = 0;
    while (true)
    {
        const std::size_t tab = line.find('\t', start);
        fields.push_back(line.substr(start, tab == std::string::npos ? tab : tab - start));
        if (tab == std::string::npos) return fields;
        start = tab + 1;
    }
}

bool SameIdentity(const struct stat& left, const struct stat& right)
{
    return left.st_dev == right.st_dev && left.st_ino == right.st_ino;
}

bool SameSnapshot(const struct stat& left, const struct stat& right)
{
    if (!SameIdentity(left, right) || left.st_mode != right.st_mode ||
        left.st_uid != right.st_uid || left.st_gid != right.st_gid ||
        left.st_size != right.st_size || left.st_nlink != right.st_nlink)
        return false;
#if defined(__APPLE__)
    return left.st_mtimespec.tv_sec == right.st_mtimespec.tv_sec &&
        left.st_mtimespec.tv_nsec == right.st_mtimespec.tv_nsec &&
        left.st_ctimespec.tv_sec == right.st_ctimespec.tv_sec &&
        left.st_ctimespec.tv_nsec == right.st_ctimespec.tv_nsec;
#else
    return left.st_mtim.tv_sec == right.st_mtim.tv_sec &&
        left.st_mtim.tv_nsec == right.st_mtim.tv_nsec &&
        left.st_ctim.tv_sec == right.st_ctim.tv_sec &&
        left.st_ctim.tv_nsec == right.st_ctim.tv_nsec;
#endif
}

class ScopedFd
{
public:
    ScopedFd() : m_fd(-1) {}
    explicit ScopedFd(int fd) : m_fd(fd) {}
    ~ScopedFd() { if (m_fd >= 0) ::close(m_fd); }
    int Get() const { return m_fd; }
    void Reset(int fd = -1)
    {
        if (m_fd >= 0) ::close(m_fd);
        m_fd = fd;
    }
    int Release()
    {
        const int fd = m_fd;
        m_fd = -1;
        return fd;
    }

private:
    ScopedFd(const ScopedFd&);
    ScopedFd& operator=(const ScopedFd&);
    int m_fd;
};

bool ReadDescriptor(int fd, std::string& value, std::size_t maximumBytes)
{
    value.clear();
    char buffer[8192];
    while (true)
    {
        const std::size_t remaining = maximumBytes >= value.size() ?
            maximumBytes - value.size() : 0;
        const std::size_t request = std::min<std::size_t>(
            sizeof(buffer), remaining == 0 ? 1 : remaining);
        const ssize_t count = ::read(fd, buffer, request);
        if (count < 0 && errno == EINTR) continue;
        if (count < 0) return false;
        if (count == 0) return true;
        value.append(buffer, static_cast<std::size_t>(count));
        if (value.size() > maximumBytes) return false;
    }
}

bool WriteDescriptor(int fd, const std::string& value)
{
    std::size_t offset = 0;
    while (offset < value.size())
    {
        const ssize_t written =
            ::write(fd, value.data() + offset, value.size() - offset);
        if (written < 0 && errno == EINTR) continue;
        if (written <= 0) return false;
        offset += static_cast<std::size_t>(written);
    }
    return true;
}

std::string ParentDirectory(const std::string& path)
{
    const std::size_t slash = path.find_last_of('/');
    return slash == std::string::npos ? "." :
        (slash == 0 ? "/" : path.substr(0, slash));
}

std::string BaseName(const std::string& path)
{
    const std::size_t slash = path.find_last_of('/');
    return slash == std::string::npos ? path : path.substr(slash + 1);
}

bool FsyncParentDirectory(const std::string& path)
{
    const std::string directory = ParentDirectory(path);
    const int fd =
        ::open(directory.c_str(), O_RDONLY | O_DIRECTORY | O_CLOEXEC);
    if (fd < 0) return false;
    const bool ok = ::fsync(fd) == 0;
    const int saved = errno;
    ::close(fd);
    errno = saved;
    return ok;
}

bool OpenPrivilegedBackupDirectory(const std::string& path,
    ScopedFd& directoryFd, std::string& leaf, struct stat& directoryMetadata,
    std::string& reason)
{
    if (path.empty() || path[0] != '/' || path.back() == '/' ||
        path == "/" || path.find("/../") != std::string::npos ||
        path.compare(path.size() >= 3 ? path.size() - 3 : 0,
            std::min<std::size_t>(3, path.size()), "/..") == 0 ||
        path.find("/./") != std::string::npos ||
        path.compare(path.size() >= 2 ? path.size() - 2 : 0,
            std::min<std::size_t>(2, path.size()), "/.") == 0)
    {
        reason = "LEASE_STORE_TERMINAL_CLEANUP_BACKUP_PATH_INVALID";
        return false;
    }
    const std::string parent = ParentDirectory(path);
    leaf = BaseName(path);
    if (leaf.empty() || leaf == "." || leaf == "..")
    {
        reason = "LEASE_STORE_TERMINAL_CLEANUP_BACKUP_PATH_INVALID";
        return false;
    }
    struct stat pathMetadata;
    if (::lstat(parent.c_str(), &pathMetadata) != 0 ||
        !S_ISDIR(pathMetadata.st_mode))
    {
        reason = "LEASE_STORE_TERMINAL_CLEANUP_BACKUP_PARENT_UNSAFE";
        return false;
    }
    const uid_t expectedUid = ::geteuid() == 0 ? 0 : ::geteuid();
    const gid_t expectedGid = ::geteuid() == 0 ? 0 : ::getegid();
    if (pathMetadata.st_uid != expectedUid ||
        pathMetadata.st_gid != expectedGid ||
        (pathMetadata.st_mode & 07777) != 0700)
    {
        reason = "LEASE_STORE_TERMINAL_CLEANUP_BACKUP_PARENT_UNSAFE";
        return false;
    }
    const int fd = ::open(
        parent.c_str(), O_RDONLY | O_DIRECTORY | O_CLOEXEC | O_NOFOLLOW);
    if (fd < 0)
    {
        reason = "LEASE_STORE_TERMINAL_CLEANUP_BACKUP_PARENT_UNSAFE";
        return false;
    }
    struct stat opened;
    bool ok = ::fstat(fd, &opened) == 0 &&
        SameIdentity(pathMetadata, opened) &&
        opened.st_uid == expectedUid && opened.st_gid == expectedGid &&
        (opened.st_mode & 07777) == 0700;
    struct stat pathAfter;
    if (!ok || ::lstat(parent.c_str(), &pathAfter) != 0 ||
        !SameIdentity(opened, pathAfter) ||
        pathAfter.st_uid != expectedUid || pathAfter.st_gid != expectedGid ||
        (pathAfter.st_mode & 07777) != 0700)
    {
        ::close(fd);
        reason = "LEASE_STORE_TERMINAL_CLEANUP_BACKUP_PARENT_UNSAFE";
        return false;
    }
    directoryMetadata = opened;
    directoryFd.Reset(fd);
    reason.clear();
    return true;
}

bool ValidateReadableMetadata(const struct stat& metadata,
    std::size_t maximumBytes, std::string& reason,
    bool allowSystemdCredential = false)
{
    const mode_t mode = metadata.st_mode & 07777;
    const bool privateMode = (mode & 0077) == 0;
    const bool systemdCredentialMode = allowSystemdCredential &&
        mode == 0440 && metadata.st_uid == 0 && metadata.st_gid == 0;
    if (!S_ISREG(metadata.st_mode) || (!privateMode && !systemdCredentialMode))
    {
        reason = "LEASE_STORE_PERMISSIONS_UNSAFE";
        return false;
    }
    if (metadata.st_nlink != 1)
    {
        reason = "LEASE_STORE_LINKS_UNSAFE";
        return false;
    }
    if (metadata.st_size < 0 ||
        static_cast<std::uint64_t>(metadata.st_size) > maximumBytes)
    {
        reason = "LEASE_STORE_SIZE_UNSAFE";
        return false;
    }
    return true;
}

bool ReadStableRegularFile(const std::string& path, std::string& value,
    struct stat& metadata, bool& missing, std::string& reason,
    std::size_t maximumBytes, bool allowSystemdCredential = false)
{
    missing = false;
    struct stat pathBefore;
    if (::lstat(path.c_str(), &pathBefore) != 0)
    {
        if (errno == ENOENT)
        {
            missing = true;
            reason.clear();
            return true;
        }
        reason = "LEASE_STORE_READ_FAILED";
        return false;
    }
    if (!ValidateReadableMetadata(pathBefore, maximumBytes, reason,
            allowSystemdCredential)) return false;
    const int fd =
        ::open(path.c_str(), O_RDONLY | O_CLOEXEC | O_NOFOLLOW);
    if (fd < 0)
    {
        reason = errno == ELOOP ? "LEASE_STORE_PERMISSIONS_UNSAFE" :
            "LEASE_STORE_READ_FAILED";
        return false;
    }
    struct stat opened;
    bool ok = ::fstat(fd, &opened) == 0 &&
        SameSnapshot(pathBefore, opened);
    if (ok) ok = ValidateReadableMetadata(opened, maximumBytes, reason,
        allowSystemdCredential);
    if (ok) ok = ReadDescriptor(fd, value, maximumBytes);
    struct stat afterRead;
    if (ok) ok = ::fstat(fd, &afterRead) == 0 &&
        SameSnapshot(opened, afterRead);
    const int saved = errno;
    const bool closed = ::close(fd) == 0;
    errno = saved;
    if (!ok || !closed)
    {
        reason = ok ? "LEASE_STORE_READ_FAILED" :
            "LEASE_STORE_SOURCE_CHANGED";
        return false;
    }
    struct stat pathAfter;
    if (::lstat(path.c_str(), &pathAfter) != 0 ||
        !SameSnapshot(opened, pathAfter))
    {
        reason = "LEASE_STORE_SOURCE_CHANGED";
        return false;
    }
    metadata = opened;
    reason.clear();
    return true;
}

bool ReadStableRegularFileAt(int directoryFd, const std::string& leaf,
    std::string& value, struct stat& metadata, bool& missing,
    std::string& reason, std::size_t maximumBytes)
{
    missing = false;
    struct stat pathBefore;
    if (::fstatat(directoryFd, leaf.c_str(), &pathBefore,
            AT_SYMLINK_NOFOLLOW) != 0)
    {
        if (errno == ENOENT)
        {
            missing = true;
            reason.clear();
            return true;
        }
        reason = "LEASE_STORE_READ_FAILED";
        return false;
    }
    if (!ValidateReadableMetadata(pathBefore, maximumBytes, reason))
        return false;
    const int fd = ::openat(directoryFd, leaf.c_str(),
        O_RDONLY | O_CLOEXEC | O_NOFOLLOW);
    if (fd < 0)
    {
        reason = errno == ELOOP ? "LEASE_STORE_PERMISSIONS_UNSAFE" :
            "LEASE_STORE_READ_FAILED";
        return false;
    }
    struct stat opened;
    bool ok = ::fstat(fd, &opened) == 0 &&
        SameSnapshot(pathBefore, opened);
    if (ok) ok = ValidateReadableMetadata(opened, maximumBytes, reason);
    if (ok) ok = ReadDescriptor(fd, value, maximumBytes);
    struct stat afterRead;
    if (ok) ok = ::fstat(fd, &afterRead) == 0 &&
        SameSnapshot(opened, afterRead);
    const int saved = errno;
    const bool closed = ::close(fd) == 0;
    errno = saved;
    if (!ok || !closed)
    {
        if (reason.empty()) reason = ok ? "LEASE_STORE_READ_FAILED" :
            "LEASE_STORE_SOURCE_CHANGED";
        return false;
    }
    struct stat pathAfter;
    if (::fstatat(directoryFd, leaf.c_str(), &pathAfter,
            AT_SYMLINK_NOFOLLOW) != 0 ||
        !SameSnapshot(opened, pathAfter))
    {
        reason = "LEASE_STORE_SOURCE_CHANGED";
        return false;
    }
    metadata = opened;
    reason.clear();
    return true;
}

bool Sha256Hex(const std::string& value, std::string& digest)
{
    EVP_MD_CTX* context = EVP_MD_CTX_new();
    if (context == nullptr) return false;
    unsigned char bytes[EVP_MAX_MD_SIZE];
    unsigned int length = 0;
    const bool ok = EVP_DigestInit_ex(context, EVP_sha256(), nullptr) == 1 &&
        EVP_DigestUpdate(context, value.data(), value.size()) == 1 &&
        EVP_DigestFinal_ex(context, bytes, &length) == 1 && length == 32;
    EVP_MD_CTX_free(context);
    if (!ok) return false;
    static const char digits[] = "0123456789abcdef";
    digest.clear();
    digest.reserve(length * 2);
    for (unsigned int i = 0; i < length; ++i)
    {
        digest.push_back(digits[bytes[i] >> 4]);
        digest.push_back(digits[bytes[i] & 15]);
    }
    return true;
}

bool IsSha256(const std::string& value)
{
    if (value.size() != 64) return false;
    for (std::size_t i = 0; i < value.size(); ++i)
        if (std::string("0123456789abcdef").find(value[i]) ==
            std::string::npos)
            return false;
    return true;
}

bool IsPrefixedSha256(const std::string& value)
{
    return value.size() == 71 && value.compare(0, 7, "sha256:") == 0 &&
        IsSha256(value.substr(7));
}

bool FinalizationText(const std::string& value, std::size_t maximum)
{
    if (value.empty() || value.size() > maximum) return false;
    for (std::size_t i = 0; i < value.size(); ++i)
    {
        const unsigned char byte = static_cast<unsigned char>(value[i]);
        if (byte < 0x21 || byte > 0x7e) return false;
    }
    return true;
}

bool ValidPaperFinalizationRecord(
    const SessionSupervisorLeaseRecord& record)
{
    const SessionSupervisorPaperFinalizationState state =
        record.paperFinalizationState;
    if (state == SessionSupervisorPaperFinalizationState::None)
        return (!record.paperFinalizationRequired ||
                (record.templateId == "paper" && record.recoveryOnly)) &&
            record.recoveryId.empty() && record.finalizationId.empty() &&
            record.expectedOwnerSetSha256.empty() &&
            record.expectedOwnerCount == 0 && record.ownerTokenSha256.empty() &&
            record.finalizationReceiptSha256.empty() &&
            record.finalizationReceipt.empty();
    if (!record.paperFinalizationRequired ||
        record.templateId != "paper" || !record.recoveryOnly ||
        record.fencePending || record.fenceComplete ||
        !FinalizationText(record.recoveryId, 128) ||
        !FinalizationText(record.finalizationId, 128) ||
        !IsPrefixedSha256(record.expectedOwnerSetSha256) ||
        record.expectedOwnerCount == 0 || record.expectedOwnerCount > 4096 ||
        !IsPrefixedSha256(record.ownerTokenSha256))
        return false;
    if (state == SessionSupervisorPaperFinalizationState::FencePending ||
        state == SessionSupervisorPaperFinalizationState::FenceComplete)
        return record.finalizationReceiptSha256.empty() &&
            record.finalizationReceipt.empty();
    return state == SessionSupervisorPaperFinalizationState::AuditSealed &&
        IsPrefixedSha256(record.finalizationReceiptSha256) &&
        !record.finalizationReceipt.empty() &&
        record.finalizationReceipt.size() <= 4096;
}

bool SameLeaseFields(const SessionSupervisorLeaseRecord& left,
    const SessionSupervisorLeaseRecord& right)
{
    return left.templateId == right.templateId &&
        left.issuer == right.issuer && left.token == right.token &&
        left.agentId == right.agentId && left.sessionId == right.sessionId &&
        left.ownerAccount == right.ownerAccount &&
        left.ownerExecutionDomain == right.ownerExecutionDomain &&
        left.peerUid == right.peerUid && left.expiresAtMs == right.expiresAtMs &&
        left.leaseGeneration == right.leaseGeneration &&
        left.predecessorToken == right.predecessorToken &&
        left.predecessorGeneration == right.predecessorGeneration &&
        left.fencePending == right.fencePending &&
        left.fenceComplete == right.fenceComplete &&
        left.fenceReason == right.fenceReason &&
        left.recoveryOnly == right.recoveryOnly &&
        left.recoveryCommandId == right.recoveryCommandId &&
        left.paperFinalizationRequired ==
            right.paperFinalizationRequired;
}

bool SameFinalizationBinding(const SessionSupervisorLeaseRecord& left,
    const SessionSupervisorLeaseRecord& right)
{
    return left.ownerTokenSha256 == right.ownerTokenSha256 &&
        left.recoveryId == right.recoveryId &&
        left.finalizationId == right.finalizationId &&
        left.expectedOwnerSetSha256 == right.expectedOwnerSetSha256 &&
        left.expectedOwnerCount == right.expectedOwnerCount;
}

bool SameFinalizationGroup(const SessionSupervisorLeaseRecord& left,
    const SessionSupervisorLeaseRecord& right)
{
    return left.recoveryId == right.recoveryId &&
        left.finalizationId == right.finalizationId &&
        left.expectedOwnerSetSha256 == right.expectedOwnerSetSha256 &&
        left.expectedOwnerCount == right.expectedOwnerCount;
}

bool ValidPaperFinalizationAck(
    const SessionSupervisorPaperFinalizationAck& acknowledgement)
{
    return FinalizationText(acknowledgement.recoveryId, 128) &&
        FinalizationText(acknowledgement.finalizationId, 128) &&
        IsPrefixedSha256(acknowledgement.expectedOwnerSetSha256) &&
        acknowledgement.expectedOwnerCount > 0 &&
        acknowledgement.expectedOwnerCount <= 4096 &&
        IsPrefixedSha256(acknowledgement.receiptSha256) &&
        !acknowledgement.receipt.empty() &&
        acknowledgement.receipt.size() <= 4096 &&
		IsPrefixedSha256(acknowledgement.terminalReceiptSha256) &&
		!acknowledgement.terminalReceipt.empty() &&
		acknowledgement.terminalReceipt.size() <= 12288 &&
        IsPrefixedSha256(acknowledgement.acknowledgingOwnerTokenSha256) &&
        acknowledgement.acknowledgingOwnerGeneration > 0 &&
		FinalizationText(acknowledgement.acknowledgingOwnerIssuer, 128) &&
		FinalizationText(acknowledgement.terminalizingOwnerAgentId, 128) &&
		FinalizationText(acknowledgement.terminalizingOwnerSessionId, 128) &&
		FinalizationText(acknowledgement.terminalizingOwnerAccount, 128) &&
		FinalizationText(
			acknowledgement.terminalizingOwnerExecutionDomain, 128);
}

bool DecodeCanonicalHex(const std::string& encoded, std::string& decoded)
{
    if (encoded.empty() || (encoded.size() % 2) != 0) return false;
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

bool ParseCanonicalUnsigned(const std::string& value,
    std::uint64_t& parsed)
{
    if (value.empty() || (value.size() > 1 && value[0] == '0')) return false;
    std::uint64_t number = 0;
    for (std::size_t i = 0; i < value.size(); ++i)
    {
        if (value[i] < '0' || value[i] > '9') return false;
        const std::uint64_t digit =
            static_cast<std::uint64_t>(value[i] - '0');
        if (number >
            (std::numeric_limits<std::uint64_t>::max() - digit) / 10)
            return false;
        number = number * 10 + digit;
    }
    parsed = number;
    return true;
}

bool ValidPaperFinalizationReceipt(
    const std::string& receipt,
    const std::string& recoveryId,
    const std::string& finalizationId,
    const std::string& expectedOwnerSetSha256,
    std::uint64_t expectedOwnerCount,
    const std::string& expectedOwnerAccount,
    const std::string& expectedOwnerDomain)
{
    if (receipt.empty() || receipt.size() > 4096 ||
        receipt.back() != '\n') return false;
    static const char* keys[] = {
        "schema", "version", "status", "recovery_id",
        "finalization_id", "expected_owner_set_sha256",
        "expected_owner_count", "owner_set_canonical_hex",
        "owner_account", "owner_execution_domain",
        "execution_service_epoch",
        "execution_service_fencing_generation",
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
        "paper_only", "live_authorized"};
    std::vector<std::string> values;
    std::istringstream input(receipt);
    std::string line;
    for (std::size_t i = 0; i < sizeof(keys) / sizeof(keys[0]); ++i)
    {
        if (!std::getline(input, line)) return false;
        const std::string prefix = std::string(keys[i]) + "=";
        if (line.compare(0, prefix.size(), prefix) != 0) return false;
        values.push_back(line.substr(prefix.size()));
    }
    if (std::getline(input, line)) return false;
    if (values[0] != "hepta.paper-session-finalization-receipt.v1" ||
        values[1] != "1" || values[2] != "AUDIT_SEALED" ||
        values[3] != recoveryId || values[4] != finalizationId ||
        values[5] != expectedOwnerSetSha256 ||
        values[25] != "0" || values[26] != "1" ||
        values[27] != "0" || values[28] != "0" ||
        values[29] != "0" || values[30] != "1" || values[31] != "0" ||
        !FinalizationText(values[3], 128) ||
        !FinalizationText(values[4], 128) ||
        !IsPrefixedSha256(values[5]) ||
        !FinalizationText(values[8], 128) ||
        !FinalizationText(values[9], 128) ||
        !FinalizationText(values[10], 256) ||
        (!expectedOwnerAccount.empty() &&
            values[8] != expectedOwnerAccount) ||
        (!expectedOwnerDomain.empty() &&
            values[9] != expectedOwnerDomain))
        return false;

    std::uint64_t numeric[15] = {};
    const std::size_t numericIndexes[] = {
        6, 11, 12, 13, 14, 15, 16, 17, 18, 19, 20, 21, 22, 23, 24};
    for (std::size_t i = 0;
         i < sizeof(numericIndexes) / sizeof(numericIndexes[0]); ++i)
        if (!ParseCanonicalUnsigned(values[numericIndexes[i]], numeric[i]))
            return false;
    if (numeric[0] != expectedOwnerCount || numeric[0] == 0 ||
        numeric[0] > 4096 || numeric[1] == 0 || numeric[2] == 0 ||
        numeric[3] == 0 || numeric[4] == 0 || numeric[5] == 0 ||
        numeric[6] == 0 || numeric[7] == 0 || numeric[8] == 0 ||
        numeric[10] > numeric[11] || numeric[11] != numeric[9] ||
        numeric[12] != 0 || numeric[13] != 0 || numeric[14] != 0)
        return false;

    std::string canonical;
    std::string digest;
    if (!DecodeCanonicalHex(values[7], canonical) ||
        !Sha256Hex(canonical, digest) ||
        expectedOwnerSetSha256 != "sha256:" + digest)
        return false;
    std::uint64_t ownerLines = 0;
    for (std::size_t i = 0; i < canonical.size(); ++i)
        if (canonical[i] == '\n') ++ownerLines;
    return ownerLines == expectedOwnerCount && canonical.back() == '\n';
}

bool ValidPaperTerminalAckReceipt(
    const std::string& receipt,
    const std::string& recoveryId,
    const std::string& finalizationId,
    const std::string& expectedOwnerSetSha256,
    std::uint64_t expectedOwnerCount,
    const std::string& preliminaryReceiptSha256,
    const std::string& expectedOwnerAccount,
    const std::string& expectedOwnerDomain,
    const std::string& expectedOwnerAgentId,
    const std::string& expectedOwnerSessionId,
    std::uint64_t expectedOwnerGeneration)
{
    if (receipt.empty() || receipt.size() > 12288 ||
        receipt.back() != '\n' ||
        !IsPrefixedSha256(preliminaryReceiptSha256)) return false;

    static const char* v3Keys[] = {
        "schema", "version", "status", "terminal_proof_kind",
        "recovery_id", "finalization_id", "campaign_id", "cycle_id",
        "expected_owner_set_sha256", "expected_owner_count",
        "owner_set_canonical_hex", "preliminary_finalization_receipt_sha256",
        "owner_agent_id", "owner_session_id", "owner_account",
        "owner_execution_domain", "account_id_sha256",
        "execution_service_epoch", "execution_service_fencing_generation",
        "recovery_ingress_fence", "terminalization_generation",
        "terminalizing_latch_sha256", "terminal_external_halt_latch_sha256",
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
        "authority_granted", "terminal_external_halt_latch_durable",
        "terminal_witness_durable", "current_host_boundary_verified",
        "terminal_evidence_file_sha256", "terminal_evidence_body_sha256"
    };
    std::map<std::string, std::string> v3;
    std::istringstream v3Input(receipt);
    std::string v3Line;
    for (std::size_t i = 0; i < sizeof(v3Keys) / sizeof(v3Keys[0]); ++i)
    {
        if (!std::getline(v3Input, v3Line)) return false;
        const std::string prefix = std::string(v3Keys[i]) + "=";
        if (v3Line.compare(0, prefix.size(), prefix) != 0 ||
            v3Line.size() == prefix.size()) return false;
        const std::string value = v3Line.substr(prefix.size());
        if (value.find('=') != std::string::npos ||
            !v3.insert(std::make_pair(v3Keys[i], value)).second)
            return false;
    }
    if (std::getline(v3Input, v3Line)) return false;
    if (v3["schema"] !=
            "hepta.paper-session-terminal-ack-receipt.v3" ||
        v3["version"] != "3" || v3["status"] != "TERMINAL_ACKED" ||
        v3["terminal_proof_kind"] !=
            "POST_CUTOFF_SIGNED_ACCOUNT_WITNESS_V1" ||
        v3["recovery_id"] != recoveryId ||
        v3["finalization_id"] != finalizationId ||
        v3["expected_owner_set_sha256"] != expectedOwnerSetSha256 ||
        v3["preliminary_finalization_receipt_sha256"] !=
            preliminaryReceiptSha256 ||
        v3["owner_agent_id"] != expectedOwnerAgentId ||
        v3["owner_session_id"] != expectedOwnerSessionId ||
        v3["owner_account"] != expectedOwnerAccount ||
        v3["owner_execution_domain"] != expectedOwnerDomain ||
        v3["provider_capability"] !=
            "ACCOUNT_WIDE_ATOMIC_OR_CAUSAL_POST_CUTOFF_READ_ONLY_V1" ||
        (v3["snapshot_consistency"] != "ATOMIC_ACCOUNT" &&
         v3["snapshot_consistency"] != "CAUSAL_WATERMARK") ||
        !FinalizationText(v3["recovery_id"], 128) ||
        !FinalizationText(v3["finalization_id"], 128) ||
        !FinalizationText(v3["campaign_id"], 128) ||
        !FinalizationText(v3["cycle_id"], 128) ||
        !FinalizationText(v3["owner_agent_id"], 128) ||
        !FinalizationText(v3["owner_session_id"], 128) ||
        !FinalizationText(v3["owner_account"], 128) ||
        !FinalizationText(v3["owner_execution_domain"], 128) ||
        !FinalizationText(v3["execution_service_epoch"], 256) ||
        !FinalizationText(v3["provider_id"], 128)) return false;

    const char* v3Digests[] = {
        "expected_owner_set_sha256",
        "preliminary_finalization_receipt_sha256", "account_id_sha256",
        "terminalizing_latch_sha256",
        "terminal_external_halt_latch_sha256",
        "transport_cutoff_receipt_file_sha256",
        "transport_cutoff_receipt_body_sha256",
        "post_cutoff_terminal_witness_file_sha256",
        "post_cutoff_terminal_witness_body_sha256",
        "provider_trust_policy_file_sha256",
        "provider_trust_policy_body_sha256", "signed_account_payload_sha256",
		"signed_account_signature_sha256", "egress_policy_sha256",
        "known_mutation_command_set_sha256",
        "known_correlation_set_sha256", "terminal_evidence_file_sha256",
        "terminal_evidence_body_sha256"};
    for (std::size_t i = 0;
         i < sizeof(v3Digests) / sizeof(v3Digests[0]); ++i)
        if (!IsPrefixedSha256(v3[v3Digests[i]]) ||
            v3[v3Digests[i]] ==
                "sha256:0000000000000000000000000000000000000000000000000000000000000000")
            return false;

    std::uint64_t ownerCount = 0;
    std::uint64_t serviceFence = 0;
    std::uint64_t recoveryFence = 0;
    std::uint64_t terminalGeneration = 0;
	std::uint64_t egressPublisherPid = 0;
	std::uint64_t egressPublisherStartTicks = 0;
    std::uint64_t egressGeneration = 0;
    std::uint64_t knownMutationCount = 0;
    std::uint64_t knownCorrelationCount = 0;
    std::uint64_t settledMutationCount = 0;
    if (!ParseCanonicalUnsigned(v3["expected_owner_count"], ownerCount) ||
        ownerCount != expectedOwnerCount || ownerCount == 0 ||
        ownerCount > 4096 ||
        !ParseCanonicalUnsigned(v3["execution_service_fencing_generation"],
            serviceFence) || serviceFence == 0 ||
        !ParseCanonicalUnsigned(v3["recovery_ingress_fence"],
            recoveryFence) || recoveryFence == 0 ||
        recoveryFence != expectedOwnerGeneration ||
        !ParseCanonicalUnsigned(v3["terminalization_generation"],
            terminalGeneration) || terminalGeneration != 1 ||
		!ParseCanonicalUnsigned(v3["egress_publisher_pid"],
			egressPublisherPid) || egressPublisherPid == 0 ||
		!ParseCanonicalUnsigned(v3["egress_publisher_start_ticks"],
			egressPublisherStartTicks) || egressPublisherStartTicks == 0 ||
        !ParseCanonicalUnsigned(v3["egress_policy_generation"],
            egressGeneration) || egressGeneration == 0 ||
        !ParseCanonicalUnsigned(v3["known_mutation_command_count"],
            knownMutationCount) || knownMutationCount > 4096 ||
        !ParseCanonicalUnsigned(v3["known_correlation_count"],
            knownCorrelationCount) || knownCorrelationCount > 4096 ||
        !ParseCanonicalUnsigned(v3["settled_mutation_command_count"],
            settledMutationCount) || settledMutationCount != knownMutationCount)
        return false;

    const char* v3Zeros[] = {
        "unknown_mutation_command_count", "unresolved_mutation_command_count",
        "unknown_active_order_count", "active_order_count", "position_count",
        "nonzero_cash_fx_count", "gross_absolute_position",
        "gross_fx_exposure", "gross_risk", "mutation_connector_count",
        "broker_socket_count", "broker_process_count",
        "broker_credential_count"};
    for (std::size_t i = 0;
         i < sizeof(v3Zeros) / sizeof(v3Zeros[0]); ++i)
        if (v3[v3Zeros[i]] != "0") return false;
    const char* v3Truths[] = {
        "query_started_after_challenge", "observed_after_cutoff",
        "causal_watermark_dominates_cutoff",
        "causal_watermark_dominates_all_mutations", "account_queries_complete",
        "active_orders_complete", "completed_orders_complete",
        "executions_complete", "positions_complete", "cash_fx_complete",
        "risk_complete", "all_known_mutation_commands_settled",
        "execution_service_inactive", "paper_units_inactive",
        "execution_mutation_gate_closed", "read_only_authority", "paper_only",
        "terminal_external_halt_latch_durable", "terminal_witness_durable",
        "current_host_boundary_verified"};
    for (std::size_t i = 0;
         i < sizeof(v3Truths) / sizeof(v3Truths[0]); ++i)
        if (v3[v3Truths[i]] != "1") return false;
    const char* v3Falses[] = {
        "broker_transport_connected", "broker_reconnect_permitted",
        "mutation_attempted", "paper_authorized", "live_authorized",
        "mutation_authorized", "direct_broker_access",
        "order_submission_authorized", "order_authorized",
        "authority_granted"};
    for (std::size_t i = 0;
         i < sizeof(v3Falses) / sizeof(v3Falses[0]); ++i)
        if (v3[v3Falses[i]] != "0") return false;

    if (v3["host_boot_id"].size() != 36 ||
        v3["host_boot_id"] ==
            "00000000-0000-0000-0000-000000000000") return false;
    for (std::size_t i = 0; i < v3["host_boot_id"].size(); ++i)
        if (i == 8 || i == 13 || i == 18 || i == 23)
        {
            if (v3["host_boot_id"][i] != '-') return false;
        }
        else if (!((v3["host_boot_id"][i] >= '0' &&
                    v3["host_boot_id"][i] <= '9') ||
                   (v3["host_boot_id"][i] >= 'a' &&
                    v3["host_boot_id"][i] <= 'f'))) return false;

    std::string accountDigest;
    if (!Sha256Hex(v3["owner_account"], accountDigest) ||
        v3["account_id_sha256"] != "sha256:" + accountDigest)
        return false;
    std::string canonical;
    std::string ownerDigest;
    if (!DecodeCanonicalHex(v3["owner_set_canonical_hex"], canonical) ||
        canonical.empty() || canonical.back() != '\n' ||
        !Sha256Hex(canonical, ownerDigest) ||
        expectedOwnerSetSha256 != "sha256:" + ownerDigest) return false;
    std::istringstream owners(canonical);
    std::string ownerLine;
    std::string previousOwnerLine;
    std::uint64_t ownerLines = 0;
    while (std::getline(owners, ownerLine))
    {
        if (ownerLine.empty() ||
            (!previousOwnerLine.empty() && ownerLine <= previousOwnerLine))
            return false;
        previousOwnerLine = ownerLine;
        std::string values[4];
        std::size_t offset = 0;
        for (int field = 0; field < 4; ++field)
        {
            const std::size_t separator = ownerLine.find('\t', offset);
            if ((field < 3 && separator == std::string::npos) ||
                (field == 3 && separator != std::string::npos)) return false;
            values[field] = ownerLine.substr(offset,
                separator == std::string::npos ? std::string::npos :
                separator - offset);
            offset = separator == std::string::npos ? ownerLine.size() :
                separator + 1;
        }
        std::uint64_t generation = 0;
        std::string account;
        std::string domain;
        if (!IsPrefixedSha256(values[0]) ||
            values[0] ==
                "sha256:0000000000000000000000000000000000000000000000000000000000000000" ||
            !ParseCanonicalUnsigned(values[1], generation) || generation == 0 ||
            !DecodeCanonicalHex(values[2], account) ||
            !DecodeCanonicalHex(values[3], domain) ||
            account != expectedOwnerAccount || domain != expectedOwnerDomain)
            return false;
        ++ownerLines;
    }
    return ownerLines == expectedOwnerCount;

}

bool PaperFinalizationReceiptContainsOwner(
    const std::string& receipt,
    const std::string& ownerTokenSha256)
{
    const std::string prefix = "owner_set_canonical_hex=";
    std::istringstream input(receipt);
    std::string line;
    std::string canonical;
    while (std::getline(input, line))
        if (line.compare(0, prefix.size(), prefix) == 0)
        {
            if (!DecodeCanonicalHex(
                    line.substr(prefix.size()), canonical))
                return false;
            break;
        }
    if (canonical.empty()) return false;
    std::istringstream owners(canonical);
    while (std::getline(owners, line))
        if (line.compare(0, ownerTokenSha256.size(),
                ownerTokenSha256) == 0 &&
            line.size() > ownerTokenSha256.size() &&
            line[ownerTokenSha256.size()] == '\t')
            return true;
    return false;
}

bool RetiredPaperOwner(
    const std::map<std::string,
        SessionSupervisorPaperFinalizationAck>& acknowledgements,
    const std::string& token)
{
    std::string digest;
    if (!Sha256Hex(token + "\n", digest)) return true;
    const std::string tokenSha256 = "sha256:" + digest;
    for (std::map<std::string,
             SessionSupervisorPaperFinalizationAck>::const_iterator it =
             acknowledgements.begin(); it != acknowledgements.end(); ++it)
        if (PaperFinalizationReceiptContainsOwner(
                it->second.receipt, tokenSha256))
            return true;
    return false;
}

bool RandomSuffix(std::string& suffix)
{
    unsigned char bytes[12];
    if (RAND_bytes(bytes, sizeof(bytes)) != 1) return false;
    static const char digits[] = "0123456789abcdef";
    suffix.clear();
    suffix.reserve(sizeof(bytes) * 2);
    for (std::size_t i = 0; i < sizeof(bytes); ++i)
    {
        suffix.push_back(digits[bytes[i] >> 4]);
        suffix.push_back(digits[bytes[i] & 15]);
    }
    return true;
}

bool RenameNoReplaceAt(int directoryFd, const std::string& source,
    const std::string& destination)
{
#if defined(SYS_renameat2)
    return ::syscall(SYS_renameat2, directoryFd, source.c_str(),
        directoryFd, destination.c_str(), RENAME_NOREPLACE) == 0;
#else
    (void)directoryFd;
    (void)source;
    (void)destination;
    errno = ENOSYS;
    return false;
#endif
}

bool CreateImmutableBackupAt(int directoryFd, const std::string& leaf,
    const std::string& content, std::string& reason)
{
    std::string suffix;
    std::string temporary;
    int fd = -1;
    for (int attempt = 0; attempt < 8 && fd < 0; ++attempt)
    {
        if (!RandomSuffix(suffix))
        {
            reason = "LEASE_STORE_TERMINAL_CLEANUP_BACKUP_CREATE_FAILED";
            return false;
        }
        temporary = ".hepta-hsl5-backup-stage." + suffix;
        fd = ::openat(directoryFd, temporary.c_str(),
            O_WRONLY | O_CREAT | O_EXCL | O_CLOEXEC | O_NOFOLLOW, 0400);
        if (fd < 0 && errno != EEXIST) break;
    }
    if (fd < 0)
    {
        reason = "LEASE_STORE_TERMINAL_CLEANUP_BACKUP_CREATE_FAILED";
        return false;
    }
    bool ok = true;
    if (::geteuid() == 0) ok = ::fchown(fd, 0, 0) == 0;
    if (ok) ok = ::fchmod(fd, 0400) == 0;
    if (ok) ok = WriteDescriptor(fd, content);
    if (ok) ok = ::fsync(fd) == 0;
    struct stat metadata;
    if (ok) ok = ::fstat(fd, &metadata) == 0;
    const uid_t expectedUid = ::geteuid() == 0 ? 0 : ::geteuid();
    const gid_t expectedGid = ::geteuid() == 0 ? 0 : ::getegid();
    if (ok) ok = metadata.st_uid == expectedUid &&
        metadata.st_gid == expectedGid &&
        (metadata.st_mode & 07777) == 0400 && metadata.st_nlink == 1;
    const int saved = errno;
    if (::close(fd) != 0) ok = false;
    errno = saved;
    if (!ok)
    {
        ::unlinkat(directoryFd, temporary.c_str(), 0);
        ::fsync(directoryFd);
        reason = "LEASE_STORE_TERMINAL_CLEANUP_BACKUP_CREATE_FAILED";
        return false;
    }

    std::string staged;
    struct stat stagedMetadata;
    bool stagedMissing = false;
    if (!ReadStableRegularFileAt(directoryFd, temporary, staged,
            stagedMetadata, stagedMissing, reason, kMaximumLeaseStoreBytes) ||
        stagedMissing || staged != content ||
        stagedMetadata.st_uid != expectedUid ||
        stagedMetadata.st_gid != expectedGid ||
        (stagedMetadata.st_mode & 07777) != 0400 ||
        stagedMetadata.st_nlink != 1)
    {
        ::unlinkat(directoryFd, temporary.c_str(), 0);
        ::fsync(directoryFd);
        reason = "LEASE_STORE_TERMINAL_CLEANUP_BACKUP_CREATE_FAILED";
        return false;
    }
    if (!RenameNoReplaceAt(directoryFd, temporary, leaf))
    {
        const int renameError = errno;
        ::unlinkat(directoryFd, temporary.c_str(), 0);
        ::fsync(directoryFd);
        reason = renameError == EEXIST ?
            "LEASE_STORE_TERMINAL_CLEANUP_BACKUP_EXISTS" :
            "LEASE_STORE_TERMINAL_CLEANUP_BACKUP_CREATE_FAILED";
        return false;
    }
    if (::fsync(directoryFd) != 0)
    {
        // The complete, fsynced, atomically published backup is retained.
        // A retry validates it byte-for-byte before doing any rewrite.
        reason = "LEASE_STORE_TERMINAL_CLEANUP_BACKUP_SYNC_FAILED";
        return false;
    }
    reason.clear();
    return true;
}

bool BackupMetadataSafe(const struct stat& metadata)
{
    const uid_t expectedUid = ::geteuid() == 0 ? 0 : ::geteuid();
    const gid_t expectedGid = ::geteuid() == 0 ? 0 : ::getegid();
    return S_ISREG(metadata.st_mode) && metadata.st_uid == expectedUid &&
        metadata.st_gid == expectedGid &&
        (metadata.st_mode & 07777) == 0400 && metadata.st_nlink == 1;
}

bool SameRecord(const SessionSupervisorLeaseRecord& left,
    const SessionSupervisorLeaseRecord& right)
{
    return SameLeaseFields(left, right) &&
        left.paperFinalizationState == right.paperFinalizationState &&
        SameFinalizationBinding(left, right) &&
        left.finalizationReceiptSha256 == right.finalizationReceiptSha256 &&
        left.finalizationReceipt == right.finalizationReceipt;
}

bool SameRecordMap(
    const std::map<std::string, SessionSupervisorLeaseRecord>& left,
    const std::map<std::string, SessionSupervisorLeaseRecord>& right)
{
    if (left.size() != right.size()) return false;
    std::map<std::string, SessionSupervisorLeaseRecord>::const_iterator
        leftIt = left.begin();
    std::map<std::string, SessionSupervisorLeaseRecord>::const_iterator
        rightIt = right.begin();
    for (; leftIt != left.end(); ++leftIt, ++rightIt)
        if (leftIt->first != rightIt->first ||
            !SameRecord(leftIt->second, rightIt->second))
            return false;
    return true;
}

class ScopedCleanupLock
{
public:
    ScopedCleanupLock() : m_fd(-1) {}
    ~ScopedCleanupLock()
    {
        if (m_fd >= 0)
        {
            ::flock(m_fd, LOCK_UN);
            ::close(m_fd);
        }
    }

    bool AcquireSharedForInit(const std::string& path,
        std::uint32_t expectedUid, std::uint32_t expectedGid,
        std::string& reason)
    {
        return Acquire(path, expectedUid, expectedGid, false, reason);
    }

    bool AcquireExclusive(const std::string& path,
        std::uint32_t expectedUid, std::uint32_t expectedGid,
        std::string& reason)
    {
        return Acquire(path, expectedUid, expectedGid, true, reason);
    }

    int Release()
    {
        const int fd = m_fd;
        m_fd = -1;
        return fd;
    }

private:
    bool Acquire(const std::string& path, std::uint32_t expectedUid,
        std::uint32_t expectedGid, bool exclusive, std::string& reason)
    {
        if (path.empty() || path[0] != '/' || path.back() == '/' ||
            BaseName(path).empty() || BaseName(path) == "." ||
            BaseName(path) == "..")
        {
            reason = "LEASE_STORE_TERMINAL_CLEANUP_LOCK_UNSAFE";
            return false;
        }
        const std::string parent = ParentDirectory(path);
        const std::string leaf = BaseName(path);
        struct stat parentPathMetadata;
        if (::lstat(parent.c_str(), &parentPathMetadata) != 0 ||
            !S_ISDIR(parentPathMetadata.st_mode) ||
            parentPathMetadata.st_uid != static_cast<uid_t>(expectedUid) ||
            parentPathMetadata.st_gid != static_cast<gid_t>(expectedGid) ||
            (parentPathMetadata.st_mode & 07777) != 0711)
        {
            reason = "LEASE_STORE_TERMINAL_CLEANUP_LOCK_UNSAFE";
            return false;
        }
        int parentOpenFlags = O_RDONLY | O_DIRECTORY | O_CLOEXEC | O_NOFOLLOW;
#if defined(__linux__)
        // The production parent is root:root 0711. O_PATH needs search but not
        // read permission, while still supporting fstat/fstatat/openat below.
        parentOpenFlags = O_PATH | O_DIRECTORY | O_CLOEXEC | O_NOFOLLOW;
#endif
        ScopedFd parentFd(::open(parent.c_str(), parentOpenFlags));
        struct stat openedParent;
        if (parentFd.Get() < 0 || ::fstat(parentFd.Get(), &openedParent) != 0 ||
            !SameIdentity(parentPathMetadata, openedParent) ||
            openedParent.st_uid != static_cast<uid_t>(expectedUid) ||
            openedParent.st_gid != static_cast<gid_t>(expectedGid) ||
            (openedParent.st_mode & 07777) != 0711)
        {
            reason = "LEASE_STORE_TERMINAL_CLEANUP_LOCK_UNSAFE";
            return false;
        }
        struct stat pathMetadata;
        if (::fstatat(parentFd.Get(), leaf.c_str(), &pathMetadata,
                AT_SYMLINK_NOFOLLOW) != 0 ||
            !S_ISREG(pathMetadata.st_mode) || pathMetadata.st_nlink != 1 ||
            pathMetadata.st_uid != static_cast<uid_t>(expectedUid) ||
            pathMetadata.st_gid != static_cast<gid_t>(expectedGid) ||
            (pathMetadata.st_mode & 07777) != 0644)
        {
            reason = "LEASE_STORE_TERMINAL_CLEANUP_LOCK_UNSAFE";
            return false;
        }
        m_fd = ::openat(parentFd.Get(), leaf.c_str(),
            (exclusive ? O_RDWR : O_RDONLY) | O_CLOEXEC | O_NOFOLLOW);
        struct stat opened;
        if (m_fd < 0 || ::fstat(m_fd, &opened) != 0 ||
            !SameSnapshot(pathMetadata, opened))
        {
            reason = "LEASE_STORE_TERMINAL_CLEANUP_LOCK_UNSAFE";
            return false;
        }
        if (::flock(m_fd, (exclusive ? LOCK_EX : LOCK_SH) | LOCK_NB) != 0)
        {
            reason = errno == EWOULDBLOCK || errno == EAGAIN ?
                "LEASE_STORE_TERMINAL_CLEANUP_IN_PROGRESS" :
                "LEASE_STORE_TERMINAL_CLEANUP_LOCK_UNSAFE";
            return false;
        }
        struct stat descriptorAfter;
        struct stat pathAfter;
        if (::fstat(m_fd, &descriptorAfter) != 0 ||
            !SameSnapshot(opened, descriptorAfter) ||
            ::fstatat(parentFd.Get(), leaf.c_str(), &pathAfter,
                AT_SYMLINK_NOFOLLOW) != 0 ||
            !SameSnapshot(opened, pathAfter))
        {
            reason = "LEASE_STORE_TERMINAL_CLEANUP_LOCK_UNSAFE";
            return false;
        }
        reason.clear();
        return true;
    }

    int m_fd;
};

} // namespace

const char* SessionSupervisorPaperFinalizationStateName(
    SessionSupervisorPaperFinalizationState state)
{
    switch (state)
    {
    case SessionSupervisorPaperFinalizationState::None:
        return "NONE";
    case SessionSupervisorPaperFinalizationState::FencePending:
        return "FENCE_PENDING";
    case SessionSupervisorPaperFinalizationState::FenceComplete:
        return "FENCE_COMPLETE";
    case SessionSupervisorPaperFinalizationState::AuditSealed:
        return "AUDIT_SEALED";
    }
    return "INVALID";
}

SessionSupervisorLeaseStore::~SessionSupervisorLeaseStore()
{
    if (m_cleanupLockFd >= 0)
    {
        ::flock(m_cleanupLockFd, LOCK_UN);
        ::close(m_cleanupLockFd);
        m_cleanupLockFd = -1;
    }
}

bool SessionSupervisorLeaseStore::Init(const std::string& path, std::string& reason)
{
    reason = "LEASE_STORE_ENCRYPTION_KEY_REQUIRED";
    return false;
}

bool SessionSupervisorLeaseStore::Init(const std::string& path,
    const std::string& keyPath, std::string& reason)
{
    if (!InitStoreState(path, keyPath, reason)) return false;
    const int previousCleanupLockFd = m_cleanupLockFd;
    m_cleanupLockFd = -1;
    if (previousCleanupLockFd >= 0)
    {
        ::flock(previousCleanupLockFd, LOCK_UN);
        ::close(previousCleanupLockFd);
    }
    reason.clear();
    return true;
}

bool SessionSupervisorLeaseStore::Init(const std::string& path,
    const std::string& keyPath, const std::string& cleanupLockPath,
    std::uint32_t expectedLockUid, std::uint32_t expectedLockGid,
    std::string& reason)
{
    if (path.empty()) { reason = "LEASE_STORE_PATH_REQUIRED"; return false; }
    ScopedCleanupLock cleanupLock;
    if (!cleanupLock.AcquireSharedForInit(
            cleanupLockPath, expectedLockUid, expectedLockGid, reason))
        return false;
    if (!InitStoreState(path, keyPath, reason)) return false;
    const int previousCleanupLockFd = m_cleanupLockFd;
    m_cleanupLockFd = cleanupLock.Release();
    if (previousCleanupLockFd >= 0)
    {
        ::flock(previousCleanupLockFd, LOCK_UN);
        ::close(previousCleanupLockFd);
    }
    reason.clear();
    return true;
}

bool SessionSupervisorLeaseStore::InitStoreState(const std::string& path,
    const std::string& keyPath, std::string& reason)
{
    if (path.empty()) { reason = "LEASE_STORE_PATH_REQUIRED"; return false; }
    if (!LoadKey(keyPath, reason)) return false;
    std::lock_guard<std::mutex> lock(m_mutex);
    m_path = path;
    m_records.clear();
    m_paperFinalizationAcks.clear();
    m_sourceMetadataValid = false;
    m_createMetadataValid = false;
    std::string plaintext;
    bool missing = false;
    if (!LoadEncryptedPlaintextLocked(path, plaintext, missing, reason)) return false;
    if (missing)
    {
        if (!PersistLocked(reason)) return false;
    }
    else if (!ParsePlaintext(plaintext, reason)) return false;
    reason.clear();
    return true;
}

bool SessionSupervisorLeaseStore::MigrateHsl5PaperForTerminalCleanup(
    const std::string& path, const std::string& keyPath,
    const SessionSupervisorLegacyPaperCleanupRequest& request,
    SessionSupervisorLegacyPaperCleanupResult& result, std::string& reason)
{
    result = SessionSupervisorLegacyPaperCleanupResult();
    SessionSupervisorLegacyPaperCleanupResult workingResult;
    if (path.empty()) { reason = "LEASE_STORE_PATH_REQUIRED"; return false; }
    if (request.expectedIssuer.empty() || request.expectedAgentId.empty() ||
        !IsSha256(request.expectedPreStoreSha256) ||
        request.expectedSourceMode != 0600 ||
        request.cleanupLockPath.empty() ||
        !IsSha256(request.expectedKeySha256) ||
        (request.expectedKeyMode != 0400 &&
         request.expectedKeyMode != 0600))
    {
        reason = "LEASE_STORE_TERMINAL_CLEANUP_SCOPE_REQUIRED";
        return false;
    }
    ScopedFd backupDirectoryFd;
    std::string backupLeaf;
    struct stat backupParentMetadata;
    if (!OpenPrivilegedBackupDirectory(request.backupPath,
            backupDirectoryFd, backupLeaf, backupParentMetadata, reason))
        return false;
    struct stat storeParentMetadata;
    if (::lstat(ParentDirectory(path).c_str(), &storeParentMetadata) != 0 ||
        !S_ISDIR(storeParentMetadata.st_mode) ||
        SameIdentity(backupParentMetadata, storeParentMetadata))
    {
        reason = "LEASE_STORE_TERMINAL_CLEANUP_BACKUP_PATH_INVALID";
        return false;
    }
    ScopedCleanupLock cleanupLock;
    if (!cleanupLock.AcquireExclusive(
            request.cleanupLockPath, request.expectedLockUid,
            request.expectedLockGid, reason))
        return false;
    if (!LoadKeyForTerminalCleanup(keyPath, request, reason)) return false;
    std::lock_guard<std::mutex> lock(m_mutex);
    m_path = path;
    m_records.clear();
    m_paperFinalizationAcks.clear();
    m_sourceMetadataValid = false;
    m_createMetadataValid = false;
    std::string plaintext;
    std::string encoded;
    bool sourceMissing = false;
    if (!LoadEncryptedPlaintextLocked(
            path, plaintext, sourceMissing, reason, &encoded)) return false;
    if (!sourceMissing &&
        (m_sourceUid != request.expectedSourceUid ||
         m_sourceGid != request.expectedSourceGid ||
         m_sourceMode != request.expectedSourceMode))
    {
        reason = "LEASE_STORE_TERMINAL_CLEANUP_SOURCE_METADATA_MISMATCH";
        return false;
    }
    std::string currentSha256;
    if (!sourceMissing && !Sha256Hex(encoded, currentSha256))
    {
        reason = "LEASE_STORE_TERMINAL_CLEANUP_HASH_FAILED";
        return false;
    }

    std::string backupEncoded;
    struct stat backupMetadata;
    bool backupMissing = false;
    if (!ReadStableRegularFileAt(backupDirectoryFd.Get(), backupLeaf,
            backupEncoded, backupMetadata, backupMissing, reason,
            kMaximumLeaseStoreBytes))
        return false;

    const std::uint64_t nowMs = static_cast<std::uint64_t>(
        std::chrono::duration_cast<std::chrono::milliseconds>(
            std::chrono::system_clock::now().time_since_epoch()).count());
    if (backupMissing)
    {
        if (sourceMissing)
        {
            reason = "LEASE_STORE_TERMINAL_CLEANUP_SOURCE_REQUIRED";
            return false;
        }
        if (currentSha256 != request.expectedPreStoreSha256)
        {
            reason = "LEASE_STORE_TERMINAL_CLEANUP_PRE_HASH_MISMATCH";
            return false;
        }
        if (!ParsePlaintextForTerminalCleanup(
                plaintext, request, nowMs, workingResult, reason))
            return false;
        if (!CreateImmutableBackupAt(
                backupDirectoryFd.Get(), backupLeaf, encoded, reason))
            return false;
        std::string verifiedBackup;
        struct stat verifiedBackupMetadata;
        bool verifiedBackupMissing = false;
        if (!ReadStableRegularFileAt(backupDirectoryFd.Get(), backupLeaf,
                verifiedBackup, verifiedBackupMetadata,
                verifiedBackupMissing, reason, kMaximumLeaseStoreBytes) ||
            verifiedBackupMissing || verifiedBackup != encoded ||
            !BackupMetadataSafe(verifiedBackupMetadata))
        {
            reason = "LEASE_STORE_TERMINAL_CLEANUP_BACKUP_VERIFY_FAILED";
            return false;
        }
        workingResult.preStoreSha256 = currentSha256;
        if (!PersistLocked(reason, &workingResult.postStoreSha256))
            return false;
        workingResult.alreadyMigrated = false;
        result = workingResult;
        return true;
    }

    if (!BackupMetadataSafe(backupMetadata))
    {
        reason = "LEASE_STORE_TERMINAL_CLEANUP_BACKUP_UNSAFE";
        return false;
    }
    std::string backupSha256;
    if (!Sha256Hex(backupEncoded, backupSha256))
    {
        reason = "LEASE_STORE_TERMINAL_CLEANUP_HASH_FAILED";
        return false;
    }
    if (backupSha256 != request.expectedPreStoreSha256)
    {
        reason = "LEASE_STORE_TERMINAL_CLEANUP_BACKUP_HASH_MISMATCH";
        return false;
    }
    std::string backupPlaintext;
    if (!DecodeEncryptedPlaintext(backupEncoded, backupPlaintext, reason))
        return false;
    SessionSupervisorLegacyPaperCleanupResult backupResult;
    if (!ParsePlaintextForTerminalCleanup(
            backupPlaintext, request, nowMs, backupResult, reason))
        return false;
    const std::map<std::string, SessionSupervisorLeaseRecord>
        expectedPreservedRecords = m_records;
    workingResult.retiredRecords = backupResult.retiredRecords;
    workingResult.preStoreSha256 = backupSha256;

    if (sourceMissing)
    {
        m_createMetadataValid = true;
        m_createUid = request.expectedSourceUid;
        m_createGid = request.expectedSourceGid;
        m_createMode = request.expectedSourceMode;
        m_records = expectedPreservedRecords;
        if (!PersistLocked(reason, &workingResult.postStoreSha256))
            return false;
        workingResult.alreadyMigrated = false;
        result = workingResult;
        return true;
    }

    if (currentSha256 == backupSha256)
    {
        if (encoded != backupEncoded)
        {
            reason = "LEASE_STORE_TERMINAL_CLEANUP_SOURCE_CHANGED";
            return false;
        }
        m_records = expectedPreservedRecords;
        if (!PersistLocked(reason, &workingResult.postStoreSha256))
            return false;
        workingResult.alreadyMigrated = false;
        result = workingResult;
        return true;
    }

    if ((plaintext.compare(0, 5, "HSL6\n") != 0 &&
         plaintext.compare(0, 5, "HSL7\n") != 0 &&
         plaintext.compare(0, 5, "HSL8\n") != 0) ||
        !ParsePlaintext(plaintext, reason) ||
        !SameRecordMap(m_records, expectedPreservedRecords))
    {
        reason = "LEASE_STORE_TERMINAL_CLEANUP_ALREADY_MIGRATED_INVALID";
        return false;
    }
    workingResult.postStoreSha256 = currentSha256;
    workingResult.alreadyMigrated = true;
    result = workingResult;
    reason.clear();
    return true;
}

bool SessionSupervisorLeaseStore::Put(const SessionSupervisorLeaseRecord& record, std::string& reason)
{
    const bool hasPredecessor =
        !record.predecessorToken.empty() || record.predecessorGeneration != 0;
    if (record.token.size() < 24 ||
        (record.templateId != "watch" && record.templateId != "paper") ||
        record.issuer.empty() ||
        record.agentId.empty() || record.sessionId.empty() || record.expiresAtMs == 0 ||
        record.leaseGeneration == 0 ||
        (record.templateId == "watch" &&
         (!record.ownerAccount.empty() ||
          !record.ownerExecutionDomain.empty())) ||
        (record.recoveryOnly && record.templateId != "paper") ||
        (record.templateId == "paper" &&
         (record.ownerAccount.empty() || record.ownerAccount.size() > 128 ||
          record.ownerExecutionDomain.empty() ||
          record.ownerExecutionDomain.size() > 128)) ||
        ((!record.predecessorToken.empty()) !=
         (record.predecessorGeneration != 0)) ||
        (hasPredecessor &&
         (!((record.templateId == "watch" && record.fencePending &&
             record.fenceReason == "session_revoked") ||
            (record.templateId == "paper" &&
             ((record.fencePending &&
               record.fenceReason == "session_revoked") ||
              (record.recoveryOnly && !record.fencePending)))) ||
          record.predecessorToken.size() < 24 ||
          record.predecessorGeneration ==
            std::numeric_limits<std::uint64_t>::max() ||
          record.leaseGeneration != record.predecessorGeneration + 1)) ||
        (record.fenceComplete &&
         (!record.fencePending || record.templateId != "watch")) ||
        (record.fencePending &&
         record.fenceReason != "session_revoked" &&
         record.fenceReason != "session_expired") ||
        (!record.fencePending &&
         (record.fenceComplete || !record.fenceReason.empty())) ||
        (!record.recoveryOnly && !record.recoveryCommandId.empty()) ||
        record.recoveryCommandId.size() > 128 ||
        record.paperFinalizationRequired ||
        !ValidPaperFinalizationRecord(record) ||
        record.paperFinalizationState !=
            SessionSupervisorPaperFinalizationState::None)
    { reason = "LEASE_STORE_RECORD_INVALID"; return false; }
    std::lock_guard<std::mutex> lock(m_mutex);
    if (record.templateId == "paper" &&
        RetiredPaperOwner(m_paperFinalizationAcks, record.token))
    {
        reason = "LEASE_STORE_PAPER_FINALIZATION_OWNER_RETIRED";
        return false;
    }
    if (m_records.find(record.token) != m_records.end())
    { reason = "LEASE_STORE_TOKEN_EXISTS"; return false; }
    if (record.templateId == "paper")
        for (std::map<std::string, SessionSupervisorLeaseRecord>::const_iterator
                 existing = m_records.begin(); existing != m_records.end();
             ++existing)
            if (existing->second.templateId == "paper" &&
                (existing->second.paperFinalizationRequired ||
                 existing->second.paperFinalizationState !=
                    SessionSupervisorPaperFinalizationState::None))
            {
                reason = "LEASE_STORE_PAPER_FINALIZATION_IN_PROGRESS";
                return false;
            }
    if (record.templateId == "watch")
        for (std::map<std::string, SessionSupervisorLeaseRecord>::const_iterator
                 existing = m_records.begin(); existing != m_records.end();
             ++existing)
            if (existing->second.templateId == "watch" &&
                existing->second.agentId == record.agentId &&
                existing->second.sessionId == record.sessionId)
            {
                reason = "LEASE_STORE_WATCH_OWNER_EXISTS";
                return false;
            }
    m_records[record.token] = record;
    if (PersistLocked(reason)) return true;
    m_records.erase(record.token);
    return false;
}

bool SessionSupervisorLeaseStore::Remove(const std::string& token, std::string& reason)
{
    std::lock_guard<std::mutex> lock(m_mutex);
    const std::map<std::string, SessionSupervisorLeaseRecord>::iterator found = m_records.find(token);
    if (found == m_records.end()) { reason = "LEASE_STORE_TOKEN_NOT_FOUND"; return false; }
    if (found->second.paperFinalizationRequired ||
        found->second.paperFinalizationState !=
            SessionSupervisorPaperFinalizationState::None)
    {
        reason = "LEASE_STORE_PAPER_FINALIZATION_ACK_REQUIRED";
        return false;
    }
    const SessionSupervisorLeaseRecord previous = found->second;
    m_records.erase(found);
    if (PersistLocked(reason)) return true;
    m_records[token] = previous;
    return false;
}

bool SessionSupervisorLeaseStore::Replace(const std::string& currentToken,
    const SessionSupervisorLeaseRecord& record, std::string& reason)
{
    const bool hasPredecessor =
        !record.predecessorToken.empty() || record.predecessorGeneration != 0;
    if (record.token.size() < 24 ||
        (record.templateId != "watch" && record.templateId != "paper") ||
        record.issuer.empty() ||
        record.agentId.empty() || record.sessionId.empty() || record.expiresAtMs == 0 ||
        record.leaseGeneration == 0 ||
        (record.templateId == "watch" &&
         (!record.ownerAccount.empty() ||
          !record.ownerExecutionDomain.empty())) ||
        (record.recoveryOnly && record.templateId != "paper") ||
        (record.templateId == "paper" &&
         (record.ownerAccount.empty() || record.ownerAccount.size() > 128 ||
          record.ownerExecutionDomain.empty() ||
          record.ownerExecutionDomain.size() > 128)) ||
        ((!record.predecessorToken.empty()) !=
         (record.predecessorGeneration != 0)) ||
        (hasPredecessor &&
         (!((record.templateId == "watch" && record.fencePending &&
             record.fenceReason == "session_revoked") ||
            (record.templateId == "paper" &&
             ((record.fencePending &&
               record.fenceReason == "session_revoked") ||
              (record.recoveryOnly && !record.fencePending)))) ||
          record.predecessorToken.size() < 24 ||
          record.predecessorGeneration ==
            std::numeric_limits<std::uint64_t>::max() ||
          record.leaseGeneration != record.predecessorGeneration + 1)) ||
        (record.fenceComplete &&
         (!record.fencePending || record.templateId != "watch")) ||
        (record.fencePending &&
         record.fenceReason != "session_revoked" &&
         record.fenceReason != "session_expired") ||
        (!record.fencePending &&
         (record.fenceComplete || !record.fenceReason.empty())) ||
        (!record.recoveryOnly && !record.recoveryCommandId.empty()) ||
        record.recoveryCommandId.size() > 128 ||
        !ValidPaperFinalizationRecord(record))
    { reason = "LEASE_STORE_RECORD_INVALID"; return false; }
    std::lock_guard<std::mutex> lock(m_mutex);
    const std::map<std::string, SessionSupervisorLeaseRecord>::iterator found = m_records.find(currentToken);
    if (found == m_records.end()) { reason = "LEASE_STORE_TOKEN_NOT_FOUND"; return false; }
    if (record.templateId == "paper" && record.token != currentToken &&
        RetiredPaperOwner(m_paperFinalizationAcks, record.token))
    {
        reason = "LEASE_STORE_PAPER_FINALIZATION_OWNER_RETIRED";
        return false;
    }
    if (found->second.paperFinalizationState !=
            SessionSupervisorPaperFinalizationState::None ||
        record.paperFinalizationState !=
            SessionSupervisorPaperFinalizationState::None)
    {
        reason = "LEASE_STORE_PAPER_FINALIZATION_REQUIRES_EXACT_API";
        return false;
    }
    if (found->second.paperFinalizationRequired &&
        !record.paperFinalizationRequired)
    {
        reason = "LEASE_STORE_PAPER_FINALIZATION_DOWNGRADE_REJECTED";
        return false;
    }
    if (currentToken != record.token && m_records.find(record.token) != m_records.end())
    { reason = "LEASE_STORE_TOKEN_EXISTS"; return false; }
    if (record.templateId == "watch")
        for (std::map<std::string, SessionSupervisorLeaseRecord>::const_iterator
                 existing = m_records.begin(); existing != m_records.end();
             ++existing)
            if (existing->first != currentToken &&
                existing->second.templateId == "watch" &&
                existing->second.agentId == record.agentId &&
                existing->second.sessionId == record.sessionId)
            {
                reason = "LEASE_STORE_WATCH_OWNER_EXISTS";
                return false;
            }
    const SessionSupervisorLeaseRecord previous = found->second;
    m_records.erase(found);
    m_records[record.token] = record;
    if (PersistLocked(reason)) return true;
    m_records.erase(record.token);
    m_records[currentToken] = previous;
    return false;
}

bool SessionSupervisorLeaseStore::PaperOwnerSetSha256(
    const std::vector<SessionSupervisorLeaseRecord>& records,
    std::string& sha256, std::string& reason)
{
    std::vector<std::string> owners;
    for (std::size_t i = 0; i < records.size(); ++i)
    {
        if (records[i].templateId != "paper") continue;
        std::string tokenDigest;
        // Bearer owner references are hashes of the canonical token-file
        // bytes.  SessionCtl strips the required trailing newline before IPC,
        // so restore it here to stay byte-identical with the external
        // recovery checkpoint's token_sha256/revoke_bearer_sha256.
        if (!Sha256Hex(records[i].token + "\n", tokenDigest))
        {
            reason = "LEASE_STORE_PAPER_OWNER_SET_HASH_FAILED";
            return false;
        }
        const std::string tokenSha256 = "sha256:" + tokenDigest;
        if (!records[i].ownerTokenSha256.empty() &&
            records[i].ownerTokenSha256 != tokenSha256)
        {
            reason = "LEASE_STORE_PAPER_OWNER_TOKEN_IDENTITY_MISMATCH";
            return false;
        }
        owners.push_back(tokenSha256 + "\t" +
            std::to_string(records[i].leaseGeneration) + "\t" +
            HexEncode(records[i].ownerAccount) + "\t" +
            HexEncode(records[i].ownerExecutionDomain) + "\n");
    }
    if (owners.empty())
    {
        reason = "LEASE_STORE_PAPER_OWNER_SET_REQUIRED";
        return false;
    }
    std::sort(owners.begin(), owners.end());
    std::string canonical;
    for (std::size_t i = 0; i < owners.size(); ++i)
        canonical += owners[i];
    std::string digest;
    if (!Sha256Hex(canonical, digest))
    {
        reason = "LEASE_STORE_PAPER_OWNER_SET_HASH_FAILED";
        return false;
    }
    sha256 = "sha256:" + digest;
    reason.clear();
    return true;
}

bool SessionSupervisorLeaseStore::AdvancePaperFinalization(
    const std::string& token,
    SessionSupervisorPaperFinalizationState expectedState,
    const SessionSupervisorLeaseRecord& replacement,
    std::string& reason)
{
    if (token != replacement.token ||
        !replacement.paperFinalizationRequired ||
        !ValidPaperFinalizationRecord(replacement))
    {
        reason = "LEASE_STORE_PAPER_FINALIZATION_RECORD_INVALID";
        return false;
    }
    const bool begin =
        expectedState == SessionSupervisorPaperFinalizationState::None &&
        replacement.paperFinalizationState ==
            SessionSupervisorPaperFinalizationState::FencePending;
    const bool complete =
        expectedState ==
            SessionSupervisorPaperFinalizationState::FencePending &&
        replacement.paperFinalizationState ==
            SessionSupervisorPaperFinalizationState::FenceComplete;
    if (!begin && !complete)
    {
        reason = "LEASE_STORE_PAPER_FINALIZATION_TRANSITION_INVALID";
        return false;
    }
    std::lock_guard<std::mutex> lock(m_mutex);
    const std::map<std::string, SessionSupervisorLeaseRecord>::iterator found =
        m_records.find(token);
    if (found == m_records.end())
    {
        reason = "LEASE_STORE_TOKEN_NOT_FOUND";
        return false;
    }
    const SessionSupervisorLeaseRecord previous = found->second;
    if (previous.paperFinalizationState != expectedState ||
        !SameLeaseFields(previous, replacement) ||
        (complete && !SameFinalizationBinding(previous, replacement)))
    {
        reason = "LEASE_STORE_PAPER_FINALIZATION_BINDING_MISMATCH";
        return false;
    }
    std::vector<SessionSupervisorLeaseRecord> paper;
    for (std::map<std::string, SessionSupervisorLeaseRecord>::const_iterator it =
             m_records.begin(); it != m_records.end(); ++it)
    {
        if (it->second.templateId != "paper") continue;
        paper.push_back(it->second);
        if (!it->second.paperFinalizationRequired)
        {
            reason =
                "LEASE_STORE_PAPER_FINALIZATION_TRANSITION_REQUIRED";
            return false;
        }
        if (it->second.ownerAccount != replacement.ownerAccount ||
            it->second.ownerExecutionDomain !=
                replacement.ownerExecutionDomain)
        {
            reason = "LEASE_STORE_PAPER_FINALIZATION_SCOPE_MISMATCH";
            return false;
        }
        if (it->first != token &&
            it->second.paperFinalizationState !=
                SessionSupervisorPaperFinalizationState::None &&
            !SameFinalizationGroup(it->second, replacement))
        {
            reason = "LEASE_STORE_PAPER_FINALIZATION_GROUP_MISMATCH";
            return false;
        }
    }
    std::string actualOwnerSetSha256;
    if (paper.size() != replacement.expectedOwnerCount ||
        !PaperOwnerSetSha256(paper, actualOwnerSetSha256, reason) ||
        actualOwnerSetSha256 != replacement.expectedOwnerSetSha256 ||
        m_paperFinalizationAcks.find(replacement.finalizationId) !=
            m_paperFinalizationAcks.end())
    {
        if (reason.empty())
            reason = "LEASE_STORE_PAPER_FINALIZATION_OWNER_SET_MISMATCH";
        return false;
    }
    std::string tokenDigest;
    if (!Sha256Hex(previous.token + "\n", tokenDigest) ||
        replacement.ownerTokenSha256 != "sha256:" + tokenDigest)
    {
        reason = "LEASE_STORE_PAPER_OWNER_TOKEN_IDENTITY_MISMATCH";
        return false;
    }
    m_records[token] = replacement;
    if (PersistLocked(reason)) return true;
    m_records[token] = previous;
    return false;
}

bool SessionSupervisorLeaseStore::SealPaperFinalizationGroup(
    const std::string& recoveryId,
    const std::string& finalizationId,
    const std::string& expectedOwnerSetSha256,
    std::uint64_t expectedOwnerCount,
    const std::string& receiptSha256,
    const std::string& receipt,
    std::string& reason)
{
    std::string digest;
    if (!FinalizationText(recoveryId, 128) ||
        !FinalizationText(finalizationId, 128) ||
        !IsPrefixedSha256(expectedOwnerSetSha256) ||
        expectedOwnerCount == 0 || expectedOwnerCount > 4096 ||
        !IsPrefixedSha256(receiptSha256) || receipt.empty() ||
        receipt.size() > 4096 || !Sha256Hex(receipt, digest) ||
        receiptSha256 != "sha256:" + digest)
    {
        reason = "LEASE_STORE_PAPER_FINALIZATION_RECEIPT_INVALID";
        return false;
    }
    std::lock_guard<std::mutex> lock(m_mutex);
    std::vector<SessionSupervisorLeaseRecord> paper;
    bool allSealed = true;
    std::string ownerAccount;
    std::string ownerDomain;
    for (std::map<std::string, SessionSupervisorLeaseRecord>::const_iterator it =
             m_records.begin(); it != m_records.end(); ++it)
    {
        if (it->second.templateId != "paper") continue;
        paper.push_back(it->second);
        if (!it->second.paperFinalizationRequired)
        {
            reason =
                "LEASE_STORE_PAPER_FINALIZATION_TRANSITION_REQUIRED";
            return false;
        }
        if (ownerAccount.empty())
        {
            ownerAccount = it->second.ownerAccount;
            ownerDomain = it->second.ownerExecutionDomain;
        }
        else if (it->second.ownerAccount != ownerAccount ||
            it->second.ownerExecutionDomain != ownerDomain)
        {
            reason = "LEASE_STORE_PAPER_FINALIZATION_SCOPE_MISMATCH";
            return false;
        }
        if (it->second.recoveryId != recoveryId ||
            it->second.finalizationId != finalizationId ||
            it->second.expectedOwnerSetSha256 != expectedOwnerSetSha256 ||
            it->second.expectedOwnerCount != expectedOwnerCount ||
            (it->second.paperFinalizationState !=
                 SessionSupervisorPaperFinalizationState::FenceComplete &&
             it->second.paperFinalizationState !=
                 SessionSupervisorPaperFinalizationState::AuditSealed))
        {
            reason = "LEASE_STORE_PAPER_FINALIZATION_GROUP_MISMATCH";
            return false;
        }
        allSealed = allSealed &&
            it->second.paperFinalizationState ==
                SessionSupervisorPaperFinalizationState::AuditSealed;
    }
    std::string actualOwnerSetSha256;
    if (paper.size() != expectedOwnerCount ||
        !PaperOwnerSetSha256(paper, actualOwnerSetSha256, reason) ||
        actualOwnerSetSha256 != expectedOwnerSetSha256 ||
        !ValidPaperFinalizationReceipt(
            receipt, recoveryId, finalizationId,
            expectedOwnerSetSha256, expectedOwnerCount,
            ownerAccount, ownerDomain))
    {
        if (reason.empty())
            reason = "LEASE_STORE_PAPER_FINALIZATION_OWNER_SET_MISMATCH";
        return false;
    }
    if (allSealed)
    {
        for (std::size_t i = 0; i < paper.size(); ++i)
            if (paper[i].finalizationReceiptSha256 != receiptSha256 ||
                paper[i].finalizationReceipt != receipt)
            {
                reason = "LEASE_STORE_PAPER_FINALIZATION_RECEIPT_MISMATCH";
                return false;
            }
        reason.clear();
        return true;
    }
    const std::map<std::string, SessionSupervisorLeaseRecord> previous =
        m_records;
    for (std::map<std::string, SessionSupervisorLeaseRecord>::iterator it =
             m_records.begin(); it != m_records.end(); ++it)
    {
        if (it->second.templateId != "paper") continue;
        if (it->second.paperFinalizationState !=
            SessionSupervisorPaperFinalizationState::FenceComplete)
        {
            reason = "LEASE_STORE_PAPER_FINALIZATION_PARTIAL_SEAL_INVALID";
            return false;
        }
        it->second.paperFinalizationState =
            SessionSupervisorPaperFinalizationState::AuditSealed;
        it->second.finalizationReceiptSha256 = receiptSha256;
        it->second.finalizationReceipt = receipt;
    }
    if (PersistLocked(reason)) return true;
    m_records = previous;
    return false;
}

bool SessionSupervisorLeaseStore::AcknowledgeAndPurgePaperFinalizationGroup(
    const std::string& recoveryId,
    const std::string& finalizationId,
    const std::string& expectedOwnerSetSha256,
    std::uint64_t expectedOwnerCount,
    const std::string& receiptSha256,
	const std::string& terminalReceiptSha256,
	const std::string& terminalReceipt,
    const std::string& acknowledgingOwnerTokenSha256,
    std::uint64_t acknowledgingOwnerGeneration,
	const std::string& acknowledgingOwnerIssuer,
	const std::string& terminalizingOwnerAgentId,
	const std::string& terminalizingOwnerSessionId,
	const std::string& terminalizingOwnerAccount,
	const std::string& terminalizingOwnerExecutionDomain,
    SessionSupervisorPaperFinalizationAck& acknowledgement,
    bool& alreadyAcknowledged,
    std::string& reason)
{
    acknowledgement = SessionSupervisorPaperFinalizationAck();
    alreadyAcknowledged = false;
    if (!FinalizationText(recoveryId, 128) ||
        !FinalizationText(finalizationId, 128) ||
        !IsPrefixedSha256(expectedOwnerSetSha256) ||
        expectedOwnerCount == 0 || expectedOwnerCount > 4096 ||
        !IsPrefixedSha256(receiptSha256) ||
		!IsPrefixedSha256(terminalReceiptSha256) ||
		terminalReceipt.empty() || terminalReceipt.size() > 12288 ||
        !IsPrefixedSha256(acknowledgingOwnerTokenSha256) ||
        acknowledgingOwnerGeneration == 0 ||
		!FinalizationText(acknowledgingOwnerIssuer, 128) ||
		!FinalizationText(terminalizingOwnerAgentId, 128) ||
		!FinalizationText(terminalizingOwnerSessionId, 128) ||
		!FinalizationText(terminalizingOwnerAccount, 128) ||
		!FinalizationText(terminalizingOwnerExecutionDomain, 128))
    {
        reason = "LEASE_STORE_PAPER_FINALIZATION_ACK_INVALID";
        return false;
    }
    std::lock_guard<std::mutex> lock(m_mutex);
    const std::map<std::string,
        SessionSupervisorPaperFinalizationAck>::const_iterator acknowledged =
            m_paperFinalizationAcks.find(finalizationId);
    if (acknowledged != m_paperFinalizationAcks.end())
    {
        const SessionSupervisorPaperFinalizationAck& existing =
            acknowledged->second;
        if (existing.recoveryId != recoveryId ||
            existing.expectedOwnerSetSha256 != expectedOwnerSetSha256 ||
            existing.expectedOwnerCount != expectedOwnerCount ||
            existing.receiptSha256 != receiptSha256 ||
			existing.terminalReceiptSha256 != terminalReceiptSha256 ||
			existing.terminalReceipt != terminalReceipt ||
            existing.acknowledgingOwnerTokenSha256 !=
                acknowledgingOwnerTokenSha256 ||
            existing.acknowledgingOwnerGeneration !=
                acknowledgingOwnerGeneration ||
			existing.acknowledgingOwnerIssuer != acknowledgingOwnerIssuer ||
			existing.terminalizingOwnerAgentId != terminalizingOwnerAgentId ||
			existing.terminalizingOwnerSessionId != terminalizingOwnerSessionId ||
			existing.terminalizingOwnerAccount != terminalizingOwnerAccount ||
			existing.terminalizingOwnerExecutionDomain !=
				terminalizingOwnerExecutionDomain)
        {
            reason = "LEASE_STORE_PAPER_FINALIZATION_ACK_BINDING_MISMATCH";
            return false;
        }
        acknowledgement = existing;
        alreadyAcknowledged = true;
        reason.clear();
        return true;
    }
    std::vector<SessionSupervisorLeaseRecord> paper;
    bool acknowledgingOwnerFound = false;
    std::string receipt;
    std::string ownerAccount;
    std::string ownerDomain;
    for (std::map<std::string, SessionSupervisorLeaseRecord>::const_iterator it =
             m_records.begin(); it != m_records.end(); ++it)
    {
        if (it->second.templateId != "paper") continue;
        const SessionSupervisorLeaseRecord& record = it->second;
        paper.push_back(record);
        if (!record.paperFinalizationRequired)
        {
            reason =
                "LEASE_STORE_PAPER_FINALIZATION_TRANSITION_REQUIRED";
            return false;
        }
        if (ownerAccount.empty())
        {
            ownerAccount = record.ownerAccount;
            ownerDomain = record.ownerExecutionDomain;
        }
        else if (record.ownerAccount != ownerAccount ||
            record.ownerExecutionDomain != ownerDomain)
        {
            reason = "LEASE_STORE_PAPER_FINALIZATION_SCOPE_MISMATCH";
            return false;
        }
        if (record.paperFinalizationState !=
                SessionSupervisorPaperFinalizationState::AuditSealed ||
            record.recoveryId != recoveryId ||
            record.finalizationId != finalizationId ||
            record.expectedOwnerSetSha256 != expectedOwnerSetSha256 ||
            record.expectedOwnerCount != expectedOwnerCount ||
            record.finalizationReceiptSha256 != receiptSha256 ||
            (!receipt.empty() && receipt != record.finalizationReceipt))
        {
            reason = "LEASE_STORE_PAPER_FINALIZATION_ACK_BINDING_MISMATCH";
            return false;
        }
        receipt = record.finalizationReceipt;
        const bool acknowledgingOwner =
            record.ownerTokenSha256 == acknowledgingOwnerTokenSha256 &&
            record.leaseGeneration == acknowledgingOwnerGeneration;
        if (acknowledgingOwner &&
            (record.issuer != acknowledgingOwnerIssuer ||
             record.agentId != terminalizingOwnerAgentId ||
             record.sessionId != terminalizingOwnerSessionId ||
             record.ownerAccount != terminalizingOwnerAccount ||
             record.ownerExecutionDomain !=
                terminalizingOwnerExecutionDomain))
        {
            reason = "LEASE_STORE_PAPER_TERMINAL_OWNER_BINDING_MISMATCH";
            return false;
        }
        acknowledgingOwnerFound = acknowledgingOwnerFound ||
            acknowledgingOwner;
    }
    std::string actualOwnerSetSha256;
    if (!acknowledgingOwnerFound || paper.size() != expectedOwnerCount ||
        !PaperOwnerSetSha256(paper, actualOwnerSetSha256, reason) ||
        actualOwnerSetSha256 != expectedOwnerSetSha256 || receipt.empty())
    {
        if (reason.empty())
            reason = "LEASE_STORE_PAPER_FINALIZATION_ACK_BINDING_MISMATCH";
        return false;
    }
    SessionSupervisorPaperFinalizationAck created;
    created.recoveryId = recoveryId;
    created.finalizationId = finalizationId;
    created.expectedOwnerSetSha256 = expectedOwnerSetSha256;
    created.expectedOwnerCount = expectedOwnerCount;
    created.receiptSha256 = receiptSha256;
    created.receipt = receipt;
	created.terminalReceiptSha256 = terminalReceiptSha256;
	created.terminalReceipt = terminalReceipt;
    created.acknowledgingOwnerTokenSha256 = acknowledgingOwnerTokenSha256;
    created.acknowledgingOwnerGeneration = acknowledgingOwnerGeneration;
	created.acknowledgingOwnerIssuer = acknowledgingOwnerIssuer;
	created.terminalizingOwnerAgentId = terminalizingOwnerAgentId;
	created.terminalizingOwnerSessionId = terminalizingOwnerSessionId;
	created.terminalizingOwnerAccount = terminalizingOwnerAccount;
	created.terminalizingOwnerExecutionDomain =
		terminalizingOwnerExecutionDomain;
    if (!ValidPaperFinalizationAck(created))
    {
        reason = "LEASE_STORE_PAPER_FINALIZATION_ACK_INVALID";
        return false;
    }
    if (!ValidPaperFinalizationReceipt(
            created.receipt, created.recoveryId, created.finalizationId,
            created.expectedOwnerSetSha256, created.expectedOwnerCount,
            ownerAccount, ownerDomain))
    {
        reason = "LEASE_STORE_PAPER_FINALIZATION_RECEIPT_INVALID";
        return false;
    }
	std::string terminalReceiptDigest;
	if (!Sha256Hex(created.terminalReceipt, terminalReceiptDigest) ||
		created.terminalReceiptSha256 !=
			"sha256:" + terminalReceiptDigest ||
		!ValidPaperTerminalAckReceipt(
			created.terminalReceipt, created.recoveryId,
			created.finalizationId, created.expectedOwnerSetSha256,
			created.expectedOwnerCount, created.receiptSha256,
			created.terminalizingOwnerAccount,
			created.terminalizingOwnerExecutionDomain,
			created.terminalizingOwnerAgentId,
			created.terminalizingOwnerSessionId,
			created.acknowledgingOwnerGeneration) ||
		created.terminalizingOwnerAccount != ownerAccount ||
		created.terminalizingOwnerExecutionDomain != ownerDomain)
	{
		reason = "LEASE_STORE_PAPER_TERMINAL_ACK_RECEIPT_INVALID";
		return false;
	}
    const std::map<std::string, SessionSupervisorLeaseRecord> oldRecords =
        m_records;
    const std::map<std::string, SessionSupervisorPaperFinalizationAck>
        oldAcknowledgements = m_paperFinalizationAcks;
    for (std::map<std::string, SessionSupervisorLeaseRecord>::iterator it =
             m_records.begin(); it != m_records.end();)
    {
        if (it->second.templateId == "paper")
            it = m_records.erase(it);
        else
            ++it;
    }
    m_paperFinalizationAcks[finalizationId] = created;
    if (PersistLocked(reason))
    {
        acknowledgement = created;
        reason.clear();
        return true;
    }
    m_records = oldRecords;
    m_paperFinalizationAcks = oldAcknowledgements;
    return false;
}

bool SessionSupervisorLeaseStore::GetPaperFinalizationAck(
    const std::string& finalizationId,
    SessionSupervisorPaperFinalizationAck& acknowledgement) const
{
    std::lock_guard<std::mutex> lock(m_mutex);
    const std::map<std::string,
        SessionSupervisorPaperFinalizationAck>::const_iterator found =
            m_paperFinalizationAcks.find(finalizationId);
    if (found == m_paperFinalizationAcks.end()) return false;
    acknowledgement = found->second;
    return true;
}

bool SessionSupervisorLeaseStore::Get(const std::string& token, SessionSupervisorLeaseRecord& record) const
{
    std::lock_guard<std::mutex> lock(m_mutex);
    const std::map<std::string, SessionSupervisorLeaseRecord>::const_iterator found = m_records.find(token);
    if (found == m_records.end()) return false;
    record = found->second;
    return true;
}

std::vector<SessionSupervisorLeaseRecord> SessionSupervisorLeaseStore::List() const
{
    std::lock_guard<std::mutex> lock(m_mutex);
    std::vector<SessionSupervisorLeaseRecord> records;
    for (std::map<std::string, SessionSupervisorLeaseRecord>::const_iterator it = m_records.begin();
         it != m_records.end(); ++it) records.push_back(it->second);
    return records;
}

std::string SessionSupervisorLeaseStore::HexEncode(const std::string& value)
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

bool SessionSupervisorLeaseStore::HexDecode(const std::string& value, std::string& decoded)
{
    if ((value.size() & 1) != 0) return false;
    decoded.clear();
    decoded.reserve(value.size() / 2);
    for (std::size_t i = 0; i < value.size(); i += 2)
    {
        const std::size_t high = std::string("0123456789abcdef").find(value[i]);
        const std::size_t low = std::string("0123456789abcdef").find(value[i + 1]);
        if (high == std::string::npos || low == std::string::npos) return false;
        decoded.push_back(static_cast<char>((high << 4) | low));
    }
    return true;
}

bool SessionSupervisorLeaseStore::LoadKey(const std::string& keyPath, std::string& reason)
{
    if (keyPath.empty()) { reason = "LEASE_STORE_ENCRYPTION_KEY_REQUIRED"; return false; }
    std::string encoded;
    struct stat metadata;
    bool missing = false;
    if (!ReadStableRegularFile(
            keyPath, encoded, metadata, missing, reason,
            kMaximumLeaseKeyBytes, true) || missing)
    {
        reason = reason == "LEASE_STORE_PERMISSIONS_UNSAFE" ?
            "LEASE_STORE_KEY_PERMISSIONS_UNSAFE" :
            "LEASE_STORE_KEY_READ_FAILED";
        return false;
    }
    while (!encoded.empty() && (encoded.back() == '\n' || encoded.back() == '\r')) encoded.pop_back();
    if (encoded.size() == 32) m_key.assign(encoded.begin(), encoded.end());
    else
    {
        std::string decoded;
        if (encoded.size() != 64 || !HexDecode(encoded, decoded) || decoded.size() != 32)
        { reason = "LEASE_STORE_KEY_INVALID"; return false; }
        m_key.assign(decoded.begin(), decoded.end());
    }
    reason.clear();
    return true;
}

bool SessionSupervisorLeaseStore::LoadKeyForTerminalCleanup(
    const std::string& keyPath,
    const SessionSupervisorLegacyPaperCleanupRequest& request,
    std::string& reason)
{
    if (keyPath.empty())
    {
        reason = "LEASE_STORE_ENCRYPTION_KEY_REQUIRED";
        return false;
    }
    std::string encoded;
    struct stat metadata;
    bool missing = false;
    if (!ReadStableRegularFile(keyPath, encoded, metadata, missing, reason,
            kMaximumLeaseKeyBytes) || missing)
    {
        reason = "LEASE_STORE_TERMINAL_CLEANUP_KEY_READ_FAILED";
        return false;
    }
    std::string keySha256;
    if (metadata.st_uid != static_cast<uid_t>(request.expectedKeyUid) ||
        metadata.st_gid != static_cast<gid_t>(request.expectedKeyGid) ||
        static_cast<std::uint32_t>(metadata.st_mode & 07777) !=
            request.expectedKeyMode ||
        metadata.st_nlink != 1 || !Sha256Hex(encoded, keySha256) ||
        keySha256 != request.expectedKeySha256)
    {
        reason = "LEASE_STORE_TERMINAL_CLEANUP_KEY_METADATA_MISMATCH";
        return false;
    }
    while (!encoded.empty() &&
        (encoded.back() == '\n' || encoded.back() == '\r'))
        encoded.pop_back();
    if (encoded.size() == 32)
        m_key.assign(encoded.begin(), encoded.end());
    else
    {
        std::string decoded;
        if (encoded.size() != 64 || !HexDecode(encoded, decoded) ||
            decoded.size() != 32)
        {
            reason = "LEASE_STORE_KEY_INVALID";
            return false;
        }
        m_key.assign(decoded.begin(), decoded.end());
    }
    reason.clear();
    return true;
}

bool SessionSupervisorLeaseStore::LoadEncryptedPlaintextLocked(
    const std::string& path, std::string& plaintext, bool& missing,
    std::string& reason, std::string* encodedOutput)
{
    struct stat metadata;
    std::string encoded;
    if (!ReadStableRegularFile(
            path, encoded, metadata, missing, reason,
            kMaximumLeaseStoreBytes))
        return false;
    if (missing)
    {
        m_sourceMetadataValid = false;
        m_sourceSha256.clear();
        if (encodedOutput != nullptr) encodedOutput->clear();
        plaintext.clear();
        return true;
    }
    m_sourceMetadataValid = true;
    m_sourceDevice = static_cast<std::uint64_t>(metadata.st_dev);
    m_sourceInode = static_cast<std::uint64_t>(metadata.st_ino);
    m_sourceUid = static_cast<std::uint64_t>(metadata.st_uid);
    m_sourceGid = static_cast<std::uint64_t>(metadata.st_gid);
    m_sourceMode = static_cast<std::uint32_t>(metadata.st_mode & 07777);
    m_sourceSize = static_cast<std::uint64_t>(metadata.st_size);
    m_sourceNlink = static_cast<std::uint64_t>(metadata.st_nlink);
    SnapshotTimes(metadata, m_sourceMtimeSec, m_sourceMtimeNsec,
        m_sourceCtimeSec, m_sourceCtimeNsec);
    if (!Sha256Hex(encoded, m_sourceSha256))
    {
        reason = "LEASE_STORE_HASH_FAILED";
        return false;
    }
    if (encodedOutput != nullptr) *encodedOutput = encoded;
    return DecodeEncryptedPlaintext(encoded, plaintext, reason);
}

bool SessionSupervisorLeaseStore::DecodeEncryptedPlaintext(
    const std::string& encoded, std::string& plaintext,
    std::string& reason) const
{
    if (encoded.compare(0, 5, "HSL1\n") == 0)
    {
        reason = "LEASE_STORE_PLAINTEXT_MIGRATION_REQUIRED";
        return false;
    }
    if (encoded.compare(0, 5, "HSL2\n") != 0)
    {
        reason = "LEASE_STORE_HEADER_INVALID";
        return false;
    }
    std::istringstream envelope(encoded.substr(5));
    std::string nonceHex;
    std::string tagHex;
    std::string cipherHex;
    std::string extra;
    if (!std::getline(envelope, nonceHex) ||
        !std::getline(envelope, tagHex) ||
        !std::getline(envelope, cipherHex) || std::getline(envelope, extra))
    {
        reason = "LEASE_STORE_ENVELOPE_INVALID";
        return false;
    }
    std::string nonce;
    std::string tag;
    std::string ciphertext;
    if (!HexDecode(nonceHex, nonce) || nonce.size() != 12 ||
        !HexDecode(tagHex, tag) || tag.size() != 16 ||
        !HexDecode(cipherHex, ciphertext) ||
        !Decrypt(ciphertext, nonce, tag, plaintext))
    {
        reason = "LEASE_STORE_DECRYPT_FAILED";
        return false;
    }
    reason.clear();
    return true;
}

std::string SessionSupervisorLeaseStore::SerializePlaintext() const
{
    std::ostringstream output;
    // The encrypted envelope remains HSL2-compatible. HSL8 retains the HSL7
    // tombstone records but upgrades the acknowledgement ledger so success is
    // bound to both the preliminary audit and the independently durable
    // Execution terminal witness. Ledger lines can never be interpreted as
    // provisionable lease material.
    output << "HSL8\n";
    for (std::map<std::string, SessionSupervisorLeaseRecord>::const_iterator it = m_records.begin();
         it != m_records.end(); ++it)
    {
        const SessionSupervisorLeaseRecord& record = it->second;
        output << "R\t" << HexEncode(record.templateId) << '\t' << HexEncode(record.issuer) << '\t'
               << HexEncode(record.token) << '\t' << HexEncode(record.agentId) << '\t'
               << HexEncode(record.sessionId) << '\t' << record.peerUid << '\t'
               << record.expiresAtMs << '\t' << record.leaseGeneration << '\t'
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
               << static_cast<std::uint32_t>(
                    record.paperFinalizationState) << '\t'
               << HexEncode(record.recoveryId) << '\t'
               << HexEncode(record.finalizationId) << '\t'
               << HexEncode(record.expectedOwnerSetSha256) << '\t'
               << record.expectedOwnerCount << '\t'
               << HexEncode(record.ownerTokenSha256) << '\t'
               << HexEncode(record.finalizationReceiptSha256) << '\t'
               << HexEncode(record.finalizationReceipt) << '\n';
    }
    for (std::map<std::string,
             SessionSupervisorPaperFinalizationAck>::const_iterator it =
             m_paperFinalizationAcks.begin();
         it != m_paperFinalizationAcks.end(); ++it)
    {
        const SessionSupervisorPaperFinalizationAck& acknowledgement =
            it->second;
        output << "A\t" << HexEncode(acknowledgement.recoveryId) << '\t'
               << HexEncode(acknowledgement.finalizationId) << '\t'
               << HexEncode(acknowledgement.expectedOwnerSetSha256) << '\t'
               << acknowledgement.expectedOwnerCount << '\t'
               << HexEncode(acknowledgement.receiptSha256) << '\t'
               << HexEncode(acknowledgement.receipt) << '\t'
               << HexEncode(acknowledgement.terminalReceiptSha256) << '\t'
               << HexEncode(acknowledgement.terminalReceipt) << '\t'
               << HexEncode(
                    acknowledgement.acknowledgingOwnerTokenSha256) << '\t'
               << acknowledgement.acknowledgingOwnerGeneration << '\t'
               << HexEncode(acknowledgement.acknowledgingOwnerIssuer) << '\t'
               << HexEncode(acknowledgement.terminalizingOwnerAgentId) << '\t'
               << HexEncode(acknowledgement.terminalizingOwnerSessionId) << '\t'
               << HexEncode(acknowledgement.terminalizingOwnerAccount) << '\t'
               << HexEncode(
                    acknowledgement.terminalizingOwnerExecutionDomain)
               << '\n';
    }
    return output.str();
}

bool SessionSupervisorLeaseStore::ParsePlaintext(const std::string& plaintext, std::string& reason)
{
    return ParsePlaintextImpl(plaintext, nullptr, 0, nullptr, reason);
}

bool SessionSupervisorLeaseStore::ParsePlaintextForTerminalCleanup(
    const std::string& plaintext,
    const SessionSupervisorLegacyPaperCleanupRequest& request,
    std::uint64_t nowMs,
    SessionSupervisorLegacyPaperCleanupResult& result,
    std::string& reason)
{
    result = SessionSupervisorLegacyPaperCleanupResult();
    return ParsePlaintextImpl(plaintext, &request, nowMs, &result, reason);
}

bool SessionSupervisorLeaseStore::ParsePlaintextImpl(
    const std::string& plaintext,
    const SessionSupervisorLegacyPaperCleanupRequest* cleanupRequest,
    std::uint64_t nowMs,
    SessionSupervisorLegacyPaperCleanupResult* cleanupResult,
    std::string& reason)
{
    std::istringstream input(plaintext);
    std::string line;
    if (!std::getline(input, line) ||
        (line != "HSL1" && line != "HSL2" &&
         line != "HSL3" && line != "HSL4" && line != "HSL5" &&
         line != "HSL6" && line != "HSL7" && line != "HSL8"))
    { reason = "LEASE_STORE_PLAINTEXT_INVALID"; return false; }
    const bool hsl7 = line == "HSL7";
	const bool hsl8 = line == "HSL8";
	const bool tagged = hsl7 || hsl8;
    const bool terminalCleanupLegacy =
        line == "HSL4" || line == "HSL5";
    if (cleanupRequest != nullptr && !terminalCleanupLegacy)
    {
        reason = "LEASE_STORE_TERMINAL_CLEANUP_SOURCE_INVALID";
        return false;
    }
    const bool legacy = line == "HSL1";
    const bool completeState = line == "HSL3" || line == "HSL4" ||
        line == "HSL5" || line == "HSL6" || tagged;
    const bool predecessorState = line == "HSL4" || line == "HSL5" ||
        line == "HSL6" || tagged;
    const bool recoveryState = line == "HSL5" || line == "HSL6" || tagged;
    const bool ownerState = line == "HSL6" || tagged;
    const bool hsl5 = line == "HSL5";
    std::map<std::string, SessionSupervisorLeaseRecord> parsedRecords;
    std::map<std::string, SessionSupervisorPaperFinalizationAck> parsedAcks;
    std::set<std::string> parsedTokens;
    std::size_t retiredRecords = 0;
    while (std::getline(input, line))
    {
        if (line.empty()) continue;
        const std::vector<std::string> fields = SplitTabs(line);
        if (hsl7 && !fields.empty() && fields[0] == "A")
		{
			reason = "LEASE_STORE_LEGACY_PAPER_FINALIZATION_ACK_REJECTED";
			return false;
		}
        if (hsl8 && !fields.empty() && fields[0] == "A")
        {
            SessionSupervisorPaperFinalizationAck acknowledgement;
            std::uint64_t expectedOwnerCount = 0;
            std::uint64_t acknowledgingGeneration = 0;
            std::string receiptDigest;
            std::string terminalReceiptDigest;
            if (fields.size() != 16 ||
                !HexDecode(fields[1], acknowledgement.recoveryId) ||
                !HexDecode(fields[2], acknowledgement.finalizationId) ||
                !HexDecode(fields[3],
                    acknowledgement.expectedOwnerSetSha256) ||
                !ParseUnsigned(fields[4], 4096, expectedOwnerCount) ||
                !HexDecode(fields[5], acknowledgement.receiptSha256) ||
                !HexDecode(fields[6], acknowledgement.receipt) ||
                !HexDecode(fields[7],
                    acknowledgement.terminalReceiptSha256) ||
                !HexDecode(fields[8], acknowledgement.terminalReceipt) ||
                !HexDecode(fields[9],
                    acknowledgement.acknowledgingOwnerTokenSha256) ||
                !ParseUnsigned(fields[10],
                    std::numeric_limits<std::uint64_t>::max(),
                    acknowledgingGeneration) ||
                !HexDecode(fields[11],
                    acknowledgement.acknowledgingOwnerIssuer) ||
                !HexDecode(fields[12],
                    acknowledgement.terminalizingOwnerAgentId) ||
                !HexDecode(fields[13],
                    acknowledgement.terminalizingOwnerSessionId) ||
                !HexDecode(fields[14],
                    acknowledgement.terminalizingOwnerAccount) ||
                !HexDecode(fields[15],
                    acknowledgement.terminalizingOwnerExecutionDomain) ||
                expectedOwnerCount == 0 || acknowledgingGeneration == 0)
            {
                reason = "LEASE_STORE_PAPER_FINALIZATION_ACK_INVALID";
                return false;
            }
            acknowledgement.expectedOwnerCount = expectedOwnerCount;
            acknowledgement.acknowledgingOwnerGeneration =
                acknowledgingGeneration;
            if (!ValidPaperFinalizationAck(acknowledgement) ||
                !Sha256Hex(acknowledgement.receipt, receiptDigest) ||
                acknowledgement.receiptSha256 !=
                    "sha256:" + receiptDigest ||
                !Sha256Hex(acknowledgement.terminalReceipt,
                    terminalReceiptDigest) ||
                acknowledgement.terminalReceiptSha256 !=
                    "sha256:" + terminalReceiptDigest ||
                !ValidPaperFinalizationReceipt(
                    acknowledgement.receipt,
                    acknowledgement.recoveryId,
                    acknowledgement.finalizationId,
                    acknowledgement.expectedOwnerSetSha256,
                    acknowledgement.expectedOwnerCount,
                    acknowledgement.terminalizingOwnerAccount,
                    acknowledgement.terminalizingOwnerExecutionDomain) ||
                !ValidPaperTerminalAckReceipt(
                    acknowledgement.terminalReceipt,
                    acknowledgement.recoveryId,
                    acknowledgement.finalizationId,
                    acknowledgement.expectedOwnerSetSha256,
                    acknowledgement.expectedOwnerCount,
                    acknowledgement.receiptSha256,
                    acknowledgement.terminalizingOwnerAccount,
                    acknowledgement.terminalizingOwnerExecutionDomain,
                    acknowledgement.terminalizingOwnerAgentId,
                    acknowledgement.terminalizingOwnerSessionId,
                    acknowledgement.acknowledgingOwnerGeneration) ||
                parsedAcks.find(acknowledgement.finalizationId) !=
                    parsedAcks.end())
            {
                reason = "LEASE_STORE_PAPER_FINALIZATION_ACK_INVALID";
                return false;
            }
            parsedAcks[acknowledgement.finalizationId] = acknowledgement;
            continue;
        }
        if (tagged && (fields.empty() || fields[0] != "R"))
        {
            reason = "LEASE_STORE_PLAINTEXT_INVALID";
            return false;
        }
        const std::size_t base = tagged ? 1 : 0;
        SessionSupervisorLeaseRecord record;
        std::uint64_t peerUid = 0;
        std::uint64_t fencePending = 0;
        std::uint64_t fenceComplete = 0;
        std::uint64_t recoveryOnly = 0;
        std::uint64_t paperFinalizationRequired = 0;
        std::uint64_t paperFinalizationState = 0;
        std::uint64_t expectedOwnerCount = 0;
        if (fields.size() !=
                (tagged ? 27 : (legacy ? 8 : (ownerState ? 17 : (recoveryState ? 15 : (predecessorState ? 13 :
                    (completeState ? 11 : 10)))))) ||
            !HexDecode(fields[base + 0], record.templateId) ||
            !HexDecode(fields[base + 1], record.issuer) ||
            !HexDecode(fields[base + 2], record.token) ||
            !HexDecode(fields[base + 3], record.agentId) ||
            !HexDecode(fields[base + 4], record.sessionId) ||
            !ParseUnsigned(fields[base + 5],
                std::numeric_limits<std::uint32_t>::max(), peerUid) ||
            !ParseUnsigned(fields[base + 6],
                std::numeric_limits<std::uint64_t>::max(),
                record.expiresAtMs) ||
            !ParseUnsigned(fields[base + 7],
                std::numeric_limits<std::uint64_t>::max(),
                record.leaseGeneration) ||
            (predecessorState &&
             (!HexDecode(fields[base + 8], record.predecessorToken) ||
              !ParseUnsigned(fields[base + 9],
                std::numeric_limits<std::uint64_t>::max(),
                record.predecessorGeneration))) ||
            (!legacy && (!ParseUnsigned(
                         fields[base + (predecessorState ? 10 : 8)],
                         1, fencePending) ||
                         (completeState &&
                          !ParseUnsigned(
                            fields[base + (predecessorState ? 11 : 9)],
                            1, fenceComplete)) ||
                         !HexDecode(fields[base + (predecessorState ? 12 :
                                    (completeState ? 10 : 9))],
                                    record.fenceReason))) ||
            (recoveryState &&
             (!ParseUnsigned(fields[base + 13], 1, recoveryOnly) ||
              !HexDecode(fields[base + 14], record.recoveryCommandId))) ||
            (tagged &&
             !ParseUnsigned(fields[base + 15], 1,
                 paperFinalizationRequired)) ||
            (ownerState &&
             (!HexDecode(fields[base + (tagged ? 16 : 15)],
                  record.ownerAccount) ||
              !HexDecode(fields[base + (tagged ? 17 : 16)],
                  record.ownerExecutionDomain))) ||
            (tagged &&
             (!ParseUnsigned(fields[19], 3, paperFinalizationState) ||
              !HexDecode(fields[20], record.recoveryId) ||
              !HexDecode(fields[21], record.finalizationId) ||
              !HexDecode(fields[22], record.expectedOwnerSetSha256) ||
              !ParseUnsigned(fields[23], 4096, expectedOwnerCount) ||
              !HexDecode(fields[24], record.ownerTokenSha256) ||
              !HexDecode(fields[25], record.finalizationReceiptSha256) ||
              !HexDecode(fields[26], record.finalizationReceipt))) ||
            record.token.size() < 24 ||
            (record.templateId != "watch" &&
             record.templateId != "paper") ||
            record.issuer.empty() ||
            record.agentId.empty() || record.sessionId.empty() || record.expiresAtMs == 0 ||
            record.leaseGeneration == 0 ||
            (record.templateId == "watch" && ownerState &&
             (!record.ownerAccount.empty() ||
              !record.ownerExecutionDomain.empty())) ||
            (recoveryOnly == 1 && record.templateId != "paper") ||
            ((!record.predecessorToken.empty()) !=
             (record.predecessorGeneration != 0)) ||
            ((!record.predecessorToken.empty() ||
              record.predecessorGeneration != 0) &&
             (!((record.templateId == "watch" && fencePending == 1 &&
                 record.fenceReason == "session_revoked") ||
                (record.templateId == "paper" &&
                 ((fencePending == 1 &&
                   record.fenceReason == "session_revoked") ||
                  (recoveryOnly == 1 && fencePending == 0)))) ||
              record.predecessorToken.size() < 24 ||
              record.predecessorGeneration ==
                std::numeric_limits<std::uint64_t>::max() ||
              record.leaseGeneration !=
                record.predecessorGeneration + 1)) ||
            (completeState && fenceComplete == 1 &&
             (fencePending != 1 || record.templateId != "watch")) ||
            (!legacy && fencePending == 1 &&
             record.fenceReason != "session_revoked" &&
             record.fenceReason != "session_expired") ||
            (!legacy && fencePending == 0 &&
             (fenceComplete != 0 || !record.fenceReason.empty())) ||
	            (recoveryState &&
	             ((recoveryOnly == 0 && !record.recoveryCommandId.empty()) ||
	              record.recoveryCommandId.size() > 128)))
        { reason = "LEASE_STORE_RECORD_INVALID"; return false; }
        record.peerUid = static_cast<std::uint32_t>(peerUid);
        record.fencePending = !legacy && fencePending == 1;
        record.fenceComplete = completeState && fenceComplete == 1;
        record.recoveryOnly = recoveryState && recoveryOnly == 1;
        record.paperFinalizationRequired =
            tagged && paperFinalizationRequired == 1;
        record.paperFinalizationState =
            static_cast<SessionSupervisorPaperFinalizationState>(
                paperFinalizationState);
        record.expectedOwnerCount = expectedOwnerCount;
        std::string receiptDigest;
        if (!ValidPaperFinalizationRecord(record) ||
            (record.paperFinalizationState ==
                 SessionSupervisorPaperFinalizationState::AuditSealed &&
             (!Sha256Hex(record.finalizationReceipt, receiptDigest) ||
              record.finalizationReceiptSha256 !=
                "sha256:" + receiptDigest ||
              !ValidPaperFinalizationReceipt(
                record.finalizationReceipt, record.recoveryId,
                record.finalizationId, record.expectedOwnerSetSha256,
                record.expectedOwnerCount, record.ownerAccount,
                record.ownerExecutionDomain))))
        {
            reason = "LEASE_STORE_PAPER_FINALIZATION_RECORD_INVALID";
            return false;
        }
        if (!parsedTokens.insert(record.token).second)
        {
            reason = "LEASE_STORE_RECORD_INVALID";
            return false;
        }

        if (record.templateId == "paper" && !ownerState)
        {
            if (cleanupRequest == nullptr)
            {
                reason = hsl5 ?
                    "LEASE_STORE_LEGACY_PAPER_OWNER_MISSING" :
                    "LEASE_STORE_RECORD_INVALID";
                return false;
            }
            if (!terminalCleanupLegacy)
            {
                reason = "LEASE_STORE_RECORD_INVALID";
                return false;
            }
            if (record.issuer != cleanupRequest->expectedIssuer ||
                record.agentId != cleanupRequest->expectedAgentId ||
                record.peerUid != cleanupRequest->expectedPeerUid)
            {
                reason = "LEASE_STORE_TERMINAL_CLEANUP_RECORD_SCOPE_INVALID";
                return false;
            }
            if (record.expiresAtMs > nowMs)
            {
                reason = "LEASE_STORE_TERMINAL_CLEANUP_RECORD_NOT_EXPIRED";
                return false;
            }
            if (!record.predecessorToken.empty() ||
                record.predecessorGeneration != 0 || record.fencePending ||
                record.fenceComplete || !record.fenceReason.empty() ||
                record.recoveryOnly || !record.recoveryCommandId.empty())
            {
                reason = "LEASE_STORE_TERMINAL_CLEANUP_RECORD_STATE_INVALID";
                return false;
            }
            ++retiredRecords;
            continue;
        }
        if (record.templateId == "paper" &&
            (record.ownerAccount.empty() || record.ownerAccount.size() > 128 ||
             record.ownerExecutionDomain.empty() ||
             record.ownerExecutionDomain.size() > 128))
        {
            reason = "LEASE_STORE_RECORD_INVALID";
            return false;
        }
        if (parsedRecords.find(record.token) != parsedRecords.end())
        {
            reason = "LEASE_STORE_RECORD_INVALID";
            return false;
        }
        if (record.templateId == "watch")
            for (std::map<std::string, SessionSupervisorLeaseRecord>::const_iterator
                     existing = parsedRecords.begin();
                 existing != parsedRecords.end(); ++existing)
                if (existing->second.templateId == "watch" &&
                    existing->second.agentId == record.agentId &&
                    existing->second.sessionId == record.sessionId)
                {
                    reason = "LEASE_STORE_WATCH_OWNER_EXISTS";
                    return false;
                }
        parsedRecords[record.token] = record;
    }
    if (cleanupRequest != nullptr && retiredRecords == 0)
    {
        reason = "LEASE_STORE_TERMINAL_CLEANUP_RECORDS_REQUIRED";
        return false;
    }
    // Every active finalization binding covers the complete current PAPER
    // owner set.  Records may be at different pre-seal states during crash
    // recovery, but two group identities can never coexist.
    std::vector<SessionSupervisorLeaseRecord> paperRecords;
    const SessionSupervisorLeaseRecord* group = nullptr;
    bool anySealed = false;
    bool allBoundSealed = true;
    std::string sealedReceiptSha256;
    std::string sealedReceipt;
    std::string finalizationOwnerAccount;
    std::string finalizationOwnerDomain;
    for (std::map<std::string, SessionSupervisorLeaseRecord>::const_iterator it =
             parsedRecords.begin(); it != parsedRecords.end(); ++it)
    {
        if (it->second.templateId != "paper") continue;
        paperRecords.push_back(it->second);
        if (it->second.paperFinalizationState ==
            SessionSupervisorPaperFinalizationState::None)
        {
            allBoundSealed = false;
            continue;
        }
        if (finalizationOwnerAccount.empty())
        {
            finalizationOwnerAccount = it->second.ownerAccount;
            finalizationOwnerDomain = it->second.ownerExecutionDomain;
        }
        else if (it->second.ownerAccount != finalizationOwnerAccount ||
            it->second.ownerExecutionDomain != finalizationOwnerDomain)
        {
            reason = "LEASE_STORE_PAPER_FINALIZATION_SCOPE_MISMATCH";
            return false;
        }
        if (group == nullptr) group = &it->second;
        else if (!SameFinalizationGroup(*group, it->second))
        {
            reason = "LEASE_STORE_PAPER_FINALIZATION_GROUP_MISMATCH";
            return false;
        }
        const bool sealed = it->second.paperFinalizationState ==
            SessionSupervisorPaperFinalizationState::AuditSealed;
        if (sealed && sealedReceiptSha256.empty())
        {
            sealedReceiptSha256 = it->second.finalizationReceiptSha256;
            sealedReceipt = it->second.finalizationReceipt;
        }
        else if (sealed &&
            (it->second.finalizationReceiptSha256 != sealedReceiptSha256 ||
             it->second.finalizationReceipt != sealedReceipt))
        {
            reason = "LEASE_STORE_PAPER_FINALIZATION_RECEIPT_MISMATCH";
            return false;
        }
        anySealed = anySealed || sealed;
        allBoundSealed = allBoundSealed && sealed;
    }
        if (group != nullptr)
    {
        std::string actualOwnerSetSha256;
        bool sameScope = true;
        for (std::size_t i = 0; i < paperRecords.size(); ++i)
            sameScope = sameScope &&
                paperRecords[i].ownerAccount == group->ownerAccount &&
                paperRecords[i].ownerExecutionDomain ==
                    group->ownerExecutionDomain;
        bool allFinalizationRequired = true;
        for (std::size_t i = 0; i < paperRecords.size(); ++i)
            allFinalizationRequired = allFinalizationRequired &&
                paperRecords[i].paperFinalizationRequired;
        if (!sameScope)
        {
            reason = "LEASE_STORE_PAPER_FINALIZATION_SCOPE_MISMATCH";
            return false;
        }
        if (!allFinalizationRequired ||
            paperRecords.size() != group->expectedOwnerCount ||
            !PaperOwnerSetSha256(
                paperRecords, actualOwnerSetSha256, reason) ||
            actualOwnerSetSha256 != group->expectedOwnerSetSha256 ||
            (anySealed && !allBoundSealed))
        {
            if (reason.empty())
                reason = "LEASE_STORE_PAPER_FINALIZATION_GROUP_MISMATCH";
            return false;
        }
    }
    for (std::map<std::string,
             SessionSupervisorPaperFinalizationAck>::const_iterator it =
             parsedAcks.begin(); it != parsedAcks.end(); ++it)
        for (std::size_t i = 0; i < paperRecords.size(); ++i)
            if (paperRecords[i].finalizationId == it->first)
            {
                reason = "LEASE_STORE_PAPER_FINALIZATION_ACK_CONFLICT";
                return false;
            }
    m_records.swap(parsedRecords);
    m_paperFinalizationAcks.swap(parsedAcks);
    if (cleanupResult != nullptr)
        cleanupResult->retiredRecords = retiredRecords;
    reason.clear();
    return true;
}

bool SessionSupervisorLeaseStore::Encrypt(const std::string& plaintext,
    std::string& ciphertext, std::string& nonce, std::string& tag) const
{
    nonce.assign(12, '\0');
    tag.assign(16, '\0');
    ciphertext.assign(plaintext.size() + 16, '\0');
    if (RAND_bytes(reinterpret_cast<unsigned char*>(&nonce[0]), nonce.size()) != 1) return false;
    EVP_CIPHER_CTX* context = EVP_CIPHER_CTX_new();
    if (context == nullptr) return false;
    int outputLength = 0;
    int finalLength = 0;
    int aadLength = 0;
    bool ok = EVP_EncryptInit_ex(context, EVP_aes_256_gcm(), nullptr, nullptr, nullptr) == 1 &&
        EVP_CIPHER_CTX_ctrl(context, EVP_CTRL_GCM_SET_IVLEN, nonce.size(), nullptr) == 1 &&
        EVP_EncryptInit_ex(context, nullptr, nullptr, &m_key[0],
            reinterpret_cast<const unsigned char*>(nonce.data())) == 1 &&
        EVP_EncryptUpdate(context, nullptr, &aadLength,
            reinterpret_cast<const unsigned char*>(kAad), std::strlen(kAad)) == 1 &&
        EVP_EncryptUpdate(context, reinterpret_cast<unsigned char*>(&ciphertext[0]), &outputLength,
            reinterpret_cast<const unsigned char*>(plaintext.data()), plaintext.size()) == 1 &&
        EVP_EncryptFinal_ex(context,
            reinterpret_cast<unsigned char*>(&ciphertext[0]) + outputLength, &finalLength) == 1 &&
        EVP_CIPHER_CTX_ctrl(context, EVP_CTRL_GCM_GET_TAG, tag.size(), &tag[0]) == 1;
    EVP_CIPHER_CTX_free(context);
    if (!ok) return false;
    ciphertext.resize(outputLength + finalLength);
    return true;
}

bool SessionSupervisorLeaseStore::Decrypt(const std::string& ciphertext,
    const std::string& nonce, const std::string& tag, std::string& plaintext) const
{
    plaintext.assign(ciphertext.size() + 16, '\0');
    EVP_CIPHER_CTX* context = EVP_CIPHER_CTX_new();
    if (context == nullptr) return false;
    int outputLength = 0;
    int finalLength = 0;
    int aadLength = 0;
    bool ok = EVP_DecryptInit_ex(context, EVP_aes_256_gcm(), nullptr, nullptr, nullptr) == 1 &&
        EVP_CIPHER_CTX_ctrl(context, EVP_CTRL_GCM_SET_IVLEN, nonce.size(), nullptr) == 1 &&
        EVP_DecryptInit_ex(context, nullptr, nullptr, &m_key[0],
            reinterpret_cast<const unsigned char*>(nonce.data())) == 1 &&
        EVP_DecryptUpdate(context, nullptr, &aadLength,
            reinterpret_cast<const unsigned char*>(kAad), std::strlen(kAad)) == 1 &&
        EVP_DecryptUpdate(context, reinterpret_cast<unsigned char*>(&plaintext[0]), &outputLength,
            reinterpret_cast<const unsigned char*>(ciphertext.data()), ciphertext.size()) == 1 &&
        EVP_CIPHER_CTX_ctrl(context, EVP_CTRL_GCM_SET_TAG, tag.size(),
            const_cast<char*>(tag.data())) == 1 &&
        EVP_DecryptFinal_ex(context,
            reinterpret_cast<unsigned char*>(&plaintext[0]) + outputLength, &finalLength) == 1;
    EVP_CIPHER_CTX_free(context);
    if (!ok) return false;
    plaintext.resize(outputLength + finalLength);
    return true;
}

bool SessionSupervisorLeaseStore::PersistLocked(
    std::string& reason, std::string* storeSha256)
{
    if (m_path.empty() || m_key.size() != 32) { reason = "LEASE_STORE_NOT_INITIALIZED"; return false; }
    std::string ciphertext;
    std::string nonce;
    std::string tag;
    if (!Encrypt(SerializePlaintext(), ciphertext, nonce, tag))
    { reason = "LEASE_STORE_ENCRYPT_FAILED"; return false; }
    const std::string content = "HSL2\n" + HexEncode(nonce) + "\n" + HexEncode(tag) +
        "\n" + HexEncode(ciphertext) + "\n";
    std::string contentSha256;
    if (!Sha256Hex(content, contentSha256))
    {
        reason = "LEASE_STORE_HASH_FAILED";
        return false;
    }

    const auto sourceUnchanged = [this](std::string& failureReason) {
        if (!m_sourceMetadataValid)
        {
            struct stat unexpected;
            if (::lstat(m_path.c_str(), &unexpected) == 0 || errno != ENOENT)
            {
                failureReason = "LEASE_STORE_SOURCE_CHANGED";
                return false;
            }
            return true;
        }
        std::string current;
        struct stat metadata;
        bool missing = false;
        std::string readReason;
        if (!ReadStableRegularFile(
                m_path, current, metadata, missing, readReason,
                kMaximumLeaseStoreBytes) || missing)
        {
            failureReason = "LEASE_STORE_SOURCE_CHANGED";
            return false;
        }
        std::string currentSha256;
        std::int64_t mtimeSec = 0;
        std::int64_t mtimeNsec = 0;
        std::int64_t ctimeSec = 0;
        std::int64_t ctimeNsec = 0;
        SnapshotTimes(metadata, mtimeSec, mtimeNsec, ctimeSec, ctimeNsec);
        if (!Sha256Hex(current, currentSha256) ||
            static_cast<std::uint64_t>(metadata.st_dev) != m_sourceDevice ||
            static_cast<std::uint64_t>(metadata.st_ino) != m_sourceInode ||
            static_cast<std::uint64_t>(metadata.st_uid) != m_sourceUid ||
            static_cast<std::uint64_t>(metadata.st_gid) != m_sourceGid ||
            static_cast<std::uint32_t>(metadata.st_mode & 07777) !=
                m_sourceMode ||
            static_cast<std::uint64_t>(metadata.st_size) != m_sourceSize ||
            static_cast<std::uint64_t>(metadata.st_nlink) != m_sourceNlink ||
            mtimeSec != m_sourceMtimeSec ||
            mtimeNsec != m_sourceMtimeNsec ||
            ctimeSec != m_sourceCtimeSec ||
            ctimeNsec != m_sourceCtimeNsec ||
            currentSha256 != m_sourceSha256)
        {
            failureReason = "LEASE_STORE_SOURCE_CHANGED";
            return false;
        }
        return true;
    };
    if (!sourceUnchanged(reason)) return false;

    std::string temporary;
    std::string suffix;
    int fd = -1;
    for (int attempt = 0; attempt < 8 && fd < 0; ++attempt)
    {
        if (!RandomSuffix(suffix)) break;
        temporary = m_path + ".tmp." + suffix;
        fd = ::open(temporary.c_str(), O_WRONLY | O_CREAT | O_EXCL |
            O_CLOEXEC | O_NOFOLLOW, 0600);
        if (fd < 0 && errno != EEXIST) break;
    }
    if (fd < 0)
    {
        reason = "LEASE_STORE_TEMP_CREATE_FAILED";
        return false;
    }
    bool ok = true;
    struct stat temporaryMetadata;
    const std::uint64_t desiredUid = m_sourceMetadataValid ?
        m_sourceUid : (m_createMetadataValid ? m_createUid :
            static_cast<std::uint64_t>(::geteuid()));
    const std::uint64_t desiredGid = m_sourceMetadataValid ?
        m_sourceGid : (m_createMetadataValid ? m_createGid :
            static_cast<std::uint64_t>(::getegid()));
    const std::uint32_t desiredMode = m_sourceMetadataValid ?
        m_sourceMode : (m_createMetadataValid ? m_createMode : 0600);
    if (m_sourceMetadataValid)
    {
        struct stat created;
        ok = ::fstat(fd, &created) == 0;
        if (ok && (static_cast<std::uint64_t>(created.st_uid) != m_sourceUid ||
                   static_cast<std::uint64_t>(created.st_gid) != m_sourceGid))
            ok = ::fchown(fd, static_cast<uid_t>(m_sourceUid),
                static_cast<gid_t>(m_sourceGid)) == 0;
        if (ok) ok = ::fchmod(fd, static_cast<mode_t>(m_sourceMode)) == 0;
    }
    else if (m_createMetadataValid)
    {
        struct stat created;
        ok = ::fstat(fd, &created) == 0;
        if (ok && (static_cast<std::uint64_t>(created.st_uid) != m_createUid ||
                   static_cast<std::uint64_t>(created.st_gid) != m_createGid))
            ok = ::fchown(fd, static_cast<uid_t>(m_createUid),
                static_cast<gid_t>(m_createGid)) == 0;
        if (ok) ok = ::fchmod(fd, static_cast<mode_t>(m_createMode)) == 0;
    }
    else
        ok = ::fchmod(fd, 0600) == 0;
    if (ok) ok = WriteDescriptor(fd, content);
    if (ok) ok = ::fsync(fd) == 0;
    if (ok) ok = ::fstat(fd, &temporaryMetadata) == 0;
    const int saved = errno;
    if (::close(fd) != 0) ok = false;
    errno = saved;
    if (!ok)
    {
        ::unlink(temporary.c_str());
        reason = "LEASE_STORE_PERSIST_FAILED";
        return false;
    }
    if (!sourceUnchanged(reason))
    {
        ::unlink(temporary.c_str());
        return false;
    }
    if (::rename(temporary.c_str(), m_path.c_str()) != 0)
    {
        ::unlink(temporary.c_str());
        reason = "LEASE_STORE_RENAME_FAILED";
        return false;
    }

    const bool directorySynced = FsyncParentDirectory(m_path);
    std::string persisted;
    struct stat persistedMetadata;
    bool missing = false;
    if (!ReadStableRegularFile(m_path, persisted, persistedMetadata,
            missing, reason, kMaximumLeaseStoreBytes) || missing ||
        persisted != content ||
        static_cast<std::uint64_t>(persistedMetadata.st_dev) !=
            static_cast<std::uint64_t>(temporaryMetadata.st_dev) ||
        static_cast<std::uint64_t>(persistedMetadata.st_ino) !=
            static_cast<std::uint64_t>(temporaryMetadata.st_ino) ||
        static_cast<std::uint64_t>(persistedMetadata.st_uid) !=
            desiredUid ||
        static_cast<std::uint64_t>(persistedMetadata.st_gid) !=
            desiredGid ||
        static_cast<std::uint32_t>(persistedMetadata.st_mode & 07777) !=
            desiredMode || persistedMetadata.st_nlink != 1)
    {
        reason = "LEASE_STORE_POST_WRITE_VERIFY_FAILED";
        return false;
    }
    m_sourceMetadataValid = true;
    m_sourceDevice = static_cast<std::uint64_t>(persistedMetadata.st_dev);
    m_sourceInode = static_cast<std::uint64_t>(persistedMetadata.st_ino);
    m_sourceUid = static_cast<std::uint64_t>(persistedMetadata.st_uid);
    m_sourceGid = static_cast<std::uint64_t>(persistedMetadata.st_gid);
    m_sourceMode = static_cast<std::uint32_t>(
        persistedMetadata.st_mode & 07777);
    m_sourceSize = static_cast<std::uint64_t>(persistedMetadata.st_size);
    m_sourceNlink = static_cast<std::uint64_t>(persistedMetadata.st_nlink);
    SnapshotTimes(persistedMetadata, m_sourceMtimeSec, m_sourceMtimeNsec,
        m_sourceCtimeSec, m_sourceCtimeNsec);
    m_sourceSha256 = contentSha256;
    m_createMetadataValid = false;
    if (!directorySynced)
    {
        reason = "LEASE_STORE_DIRECTORY_SYNC_FAILED";
        return false;
    }
    if (storeSha256 != nullptr) *storeSha256 = contentSha256;
    reason.clear();
    return true;
}
