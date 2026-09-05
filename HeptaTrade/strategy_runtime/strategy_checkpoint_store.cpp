#include "strategy_checkpoint_store.h"

#include <cerrno>
#include <cstring>
#include <limits>
#include <openssl/evp.h>
#include <stdexcept>
#include <utility>
#include <vector>

#if defined(__linux__)
#include <fcntl.h>
#include <sys/file.h>
#include <sys/stat.h>
#include <unistd.h>
#endif

struct VerifiedStrategyCheckpoint::Data
{
    StrategyArtifactDescriptor descriptor;
    std::string payload, payloadDigest, recordDigest;
    std::uint64_t sequence = 0, sourceGeneration = 0, savedAtMs = 0;
};

const StrategyArtifactDescriptor& VerifiedStrategyCheckpoint::Descriptor() const
{
    if (!m_data) throw std::logic_error("invalid checkpoint");
    return m_data->descriptor;
}
const std::string& VerifiedStrategyCheckpoint::Payload() const
{
    if (!m_data) throw std::logic_error("invalid checkpoint");
    return m_data->payload;
}
const std::string& VerifiedStrategyCheckpoint::PayloadDigest() const
{
    if (!m_data) throw std::logic_error("invalid checkpoint");
    return m_data->payloadDigest;
}
const std::string& VerifiedStrategyCheckpoint::RecordDigest() const
{
    if (!m_data) throw std::logic_error("invalid checkpoint");
    return m_data->recordDigest;
}
std::uint64_t VerifiedStrategyCheckpoint::Sequence() const noexcept
{ return m_data ? m_data->sequence : 0; }
std::uint64_t VerifiedStrategyCheckpoint::SourceGeneration() const noexcept
{ return m_data ? m_data->sourceGeneration : 0; }
std::uint64_t VerifiedStrategyCheckpoint::SavedAtMs() const noexcept
{ return m_data ? m_data->savedAtMs : 0; }

namespace
{
const std::size_t kMaximumPayload = 16u * 1024u * 1024u;
const std::size_t kEnvelopeBytes = 1024u;
const char kMagic[] = "HEPTA_STRATEGY_CHECKPOINT_V1\n";

std::string Hex(const unsigned char* bytes, std::size_t count)
{
    static const char digits[] = "0123456789abcdef";
    std::string result(count * 2, '0');
    for (std::size_t i = 0; i < count; ++i)
    {
        result[i * 2] = digits[bytes[i] >> 4];
        result[i * 2 + 1] = digits[bytes[i] & 15];
    }
    return result;
}
std::string Hash(const std::string& bytes)
{
    unsigned char digest[EVP_MAX_MD_SIZE]; unsigned int size = 0;
    if (EVP_Digest(bytes.data(), bytes.size(), digest, &size, EVP_sha256(), nullptr) != 1 ||
        size != 32u) return std::string();
    return "sha256:" + Hex(digest, size);
}
bool Digest(const std::string& text)
{
    if (text.size() != 71 || text.compare(0, 7, "sha256:") != 0) return false;
    for (std::size_t i = 7; i < text.size(); ++i)
        if (!((text[i] >= '0' && text[i] <= '9') ||
              (text[i] >= 'a' && text[i] <= 'f'))) return false;
    return true;
}
bool Id(const std::string& text, std::size_t maximum)
{
    if (text.empty() || text.size() > maximum) return false;
    for (unsigned char c : text)
        if (!((c >= 'a' && c <= 'z') || (c >= 'A' && c <= 'Z') ||
              (c >= '0' && c <= '9') || c == '.' || c == ':' || c == '-' || c == '_'))
            return false;
    return true;
}
bool DescriptorValid(const StrategyArtifactDescriptor& d)
{
    return Id(d.moduleId, 128) && d.moduleId.compare(0, 6, "hepta.") == 0 &&
        Id(d.version, 64) && Digest(d.artifactDigest) && Digest(d.configDigest) &&
        (d.modelDigest.empty() || Digest(d.modelDigest)) &&
        d.budget.maxThreads > 0 && d.budget.maxThreads <= 64 &&
        d.budget.maxFileDescriptors > 0 && d.budget.maxFileDescriptors <= 4096 &&
        d.budget.maxMemoryBytes > 0 && d.budget.maxMemoryBytes <= (16ULL << 30) &&
        d.budget.maxCheckpointBytes > 0 &&
        d.budget.maxCheckpointBytes <= d.budget.maxMemoryBytes;
}
void U64(std::string& out, std::uint64_t value)
{
    for (int shift = 56; shift >= 0; shift -= 8)
        out.push_back(static_cast<char>((value >> shift) & 255u));
}
void Field(std::string& out, const std::string& value)
{ U64(out, value.size()); out.append(value); }
std::string Prefix(const StrategyArtifactDescriptor& d)
{
    std::string out = kMagic;
    Field(out, d.moduleId); Field(out, d.version); Field(out, d.artifactDigest);
    Field(out, d.configDigest); Field(out, d.modelDigest);
    U64(out, d.budget.maxThreads); U64(out, d.budget.maxFileDescriptors);
    U64(out, d.budget.maxMemoryBytes); U64(out, d.budget.maxCheckpointBytes);
    return out;
}
[[maybe_unused]] bool ReadU64(const std::string& bytes, std::size_t& cursor, std::uint64_t& value)
{
    if (cursor > bytes.size() || bytes.size() - cursor < 8) return false;
    value = 0;
    for (int i = 0; i < 8; ++i)
        value = (value << 8) | static_cast<unsigned char>(bytes[cursor++]);
    return true;
}
StrategyCheckpointResult Failure(const char* code)
{ StrategyCheckpointResult r; r.reasonCode = code; return r; }

#if defined(__linux__)
class Fd
{
public:
    explicit Fd(int value = -1) noexcept : value_(value) {}
    ~Fd() { if (value_ >= 0) ::close(value_); }
    Fd(Fd&& other) noexcept : value_(other.value_) { other.value_ = -1; }
    Fd& operator=(Fd&& other) noexcept
    {
        if (this != &other)
        { if (value_ >= 0) ::close(value_); value_ = other.value_; other.value_ = -1; }
        return *this;
    }
    Fd(const Fd&) = delete; Fd& operator=(const Fd&) = delete;
    int Get() const noexcept { return value_; }
    bool Close() noexcept
    {
        const int old = value_; value_ = -1;
        return old < 0 || ::close(old) == 0; // Never retry close on Linux.
    }
private:
    int value_;
};
bool SameInode(const struct stat& a, const struct stat& b)
{ return a.st_dev == b.st_dev && a.st_ino == b.st_ino; }
bool SameVersion(const struct stat& a, const struct stat& b)
{
    return SameInode(a, b) && a.st_size == b.st_size && a.st_mode == b.st_mode &&
        a.st_uid == b.st_uid && a.st_nlink == b.st_nlink &&
        a.st_mtim.tv_sec == b.st_mtim.tv_sec && a.st_mtim.tv_nsec == b.st_mtim.tv_nsec &&
        a.st_ctim.tv_sec == b.st_ctim.tv_sec && a.st_ctim.tv_nsec == b.st_ctim.tv_nsec;
}
bool PrivateFile(const struct stat& s)
{
    return S_ISREG(s.st_mode) && s.st_uid == ::geteuid() &&
        (s.st_mode & 07777) == 0600 && s.st_nlink == 1;
}
bool Sync(int fd)
{
    int result;
    do { result = ::fsync(fd); } while (result != 0 && errno == EINTR);
    return result == 0;
}

class Directory
{
    struct Node { std::string name; Fd fd; };
    std::vector<Node> nodes_;
    Fd lock_;
    std::string lockName_;
public:
    int Parent() const { return nodes_.back().fd.Get(); }
    const char* Open(const std::string& path, const std::string& filename)
    {
        if (path.empty() || path[0] != '/' || path.size() > 4096 ||
            path.find('\0') != std::string::npos || !Id(filename, 96) ||
            filename == "." || filename == "..") return "CHECKPOINT_PATH_INVALID";
        Node root; root.fd = Fd(::open("/", O_RDONLY | O_DIRECTORY | O_CLOEXEC));
        if (root.fd.Get() < 0) return "CHECKPOINT_DIRECTORY_OPEN_FAILED";
        nodes_.push_back(std::move(root));
        std::size_t start = 1;
        while (start < path.size())
        {
            const std::size_t end = path.find('/', start);
            Node node; node.name = path.substr(start, end - start);
            if (node.name.empty() || node.name == "." || node.name == ".." ||
                nodes_.size() >= 65) return "CHECKPOINT_PATH_INVALID";
            node.fd = Fd(::openat(Parent(), node.name.c_str(),
                                 O_RDONLY | O_DIRECTORY | O_NOFOLLOW | O_CLOEXEC));
            if (node.fd.Get() < 0) return "CHECKPOINT_DIRECTORY_OPEN_FAILED";
            nodes_.push_back(std::move(node));
            if (end == std::string::npos) break;
            start = end + 1;
        }
        struct stat s;
        if (::fstat(Parent(), &s) != 0 || s.st_uid != ::geteuid() ||
            (s.st_mode & 07777) != 0700) return "CHECKPOINT_DIRECTORY_NOT_PRIVATE";
        lockName_ = filename + ".lock";
        lock_ = Fd(::openat(Parent(), lockName_.c_str(),
            O_RDWR | O_CREAT | O_NOFOLLOW | O_CLOEXEC | O_NONBLOCK, 0600));
        if (lock_.Get() < 0 || ::fstat(lock_.Get(), &s) != 0 || !PrivateFile(s) || s.st_size != 0)
            return "CHECKPOINT_LOCK_INVALID";
        if (::flock(lock_.Get(), LOCK_EX | LOCK_NB) != 0) return "CHECKPOINT_LOCK_BUSY";
        return Bound() ? nullptr : "CHECKPOINT_PATH_CHANGED";
    }
    bool Bound() const
    {
        struct stat s, named;
        for (std::size_t i = 1; i < nodes_.size(); ++i)
            if (::fstat(nodes_[i].fd.Get(), &s) != 0 ||
                ::fstatat(nodes_[i - 1].fd.Get(), nodes_[i].name.c_str(),
                          &named, AT_SYMLINK_NOFOLLOW) != 0 ||
                !S_ISDIR(named.st_mode) || !SameInode(s, named)) return false;
        if (::fstat(Parent(), &s) != 0 || s.st_uid != ::geteuid() ||
            (s.st_mode & 07777) != 0700 || ::fstat(lock_.Get(), &s) != 0 ||
            !PrivateFile(s) || s.st_size != 0 || ::fstatat(Parent(), lockName_.c_str(), &named,
                                       AT_SYMLINK_NOFOLLOW) != 0 ||
            !SameVersion(s, named)) return false;
        return true;
    }
    bool Identity(std::uintmax_t& device, std::uintmax_t& inode,
                  std::uintmax_t& lockDevice, std::uintmax_t& lockInode) const
    {
        struct stat directory, lock;
        if (::fstat(Parent(), &directory) != 0 || ::fstat(lock_.Get(), &lock) != 0 || !Bound())
            return false;
        device = directory.st_dev; inode = directory.st_ino;
        lockDevice = lock.st_dev; lockInode = lock.st_ino;
        return true;
    }
    const char* Read(const std::string& name, std::size_t limit,
                     std::string& bytes, bool& absent, struct stat& version) const
    {
        bytes.clear(); absent = false;
        Fd file(::openat(Parent(), name.c_str(), O_RDONLY | O_NOFOLLOW | O_CLOEXEC | O_NONBLOCK));
        if (file.Get() < 0)
        {
            absent = errno == ENOENT;
            return absent && Bound() ? nullptr : "CHECKPOINT_OPEN_FAILED";
        }
        if (::fstat(file.Get(), &version) != 0 || !PrivateFile(version) ||
            version.st_size <= 0 || static_cast<std::uintmax_t>(version.st_size) > limit)
            return "CHECKPOINT_FILE_INVALID";
        bytes.resize(static_cast<std::size_t>(version.st_size));
        std::size_t offset = 0;
        while (offset < bytes.size())
        {
            const ssize_t n = ::pread(file.Get(), &bytes[offset], bytes.size() - offset,
                                      static_cast<off_t>(offset));
            if (n < 0 && errno == EINTR) continue;
            if (n <= 0) return "CHECKPOINT_READ_FAILED";
            offset += static_cast<std::size_t>(n);
        }
        char extra;
        ssize_t n;
        do { n = ::pread(file.Get(), &extra, 1, version.st_size); } while (n < 0 && errno == EINTR);
        struct stat after, named;
        if (n != 0 || ::fstat(file.Get(), &after) != 0 || !SameVersion(version, after) ||
            ::fstatat(Parent(), name.c_str(), &named, AT_SYMLINK_NOFOLLOW) != 0 ||
            !SameVersion(after, named) || !Bound()) return "CHECKPOINT_FILE_CHANGED";
        if (!file.Close()) return "CHECKPOINT_CLOSE_FAILED";
        return nullptr;
    }
    const char* Replace(const std::string& name, const std::string& bytes,
                        bool wasAbsent, const struct stat& old, bool& uncertain) const
    {
        // One exclusive staging slot bounds crash leftovers. Never truncate,
        // follow, reuse or automatically erase an orphan from a previous process.
        const std::string temp = "." + name + ".pending";
        Fd file(::openat(Parent(), temp.c_str(),
            O_WRONLY | O_CREAT | O_EXCL | O_NOFOLLOW | O_CLOEXEC, 0600));
        if (file.Get() < 0) return "CHECKPOINT_TEMP_OPEN_FAILED";
        struct Cleanup
        {
            int parent, fd; const std::string& name; bool renamed = false;
            ~Cleanup()
            {
                if (renamed) return;
                struct stat a, b;
                if (::fstat(fd, &a) == 0 &&
                    ::fstatat(parent, name.c_str(), &b, AT_SYMLINK_NOFOLLOW) == 0 &&
                    SameInode(a, b)) ::unlinkat(parent, name.c_str(), 0);
            }
        } cleanup{Parent(), file.Get(), temp, false};
        struct stat written, named;
        if (::fstat(file.Get(), &written) != 0 || !PrivateFile(written))
            return "CHECKPOINT_TEMP_INVALID";
        std::size_t offset = 0;
        while (offset < bytes.size())
        {
            const ssize_t n = ::write(file.Get(), bytes.data() + offset, bytes.size() - offset);
            if (n < 0 && errno == EINTR) continue;
            if (n <= 0) return "CHECKPOINT_WRITE_FAILED";
            offset += static_cast<std::size_t>(n);
        }
        if (!Sync(file.Get())) return "CHECKPOINT_FILE_SYNC_FAILED";
        if (::fstat(file.Get(), &written) != 0 || !PrivateFile(written) ||
            static_cast<std::uintmax_t>(written.st_size) != bytes.size() ||
            ::fstatat(Parent(), temp.c_str(), &named, AT_SYMLINK_NOFOLLOW) != 0 ||
            !SameVersion(written, named) || !Bound()) return "CHECKPOINT_TEMP_CHANGED";
        const int target = ::fstatat(Parent(), name.c_str(), &named, AT_SYMLINK_NOFOLLOW);
        if (wasAbsent ? (target == 0 || errno != ENOENT) :
            (target != 0 || !SameVersion(old, named))) return "CHECKPOINT_CONCURRENT_WRITE";
        if (::renameat(Parent(), temp.c_str(), Parent(), name.c_str()) != 0)
            return "CHECKPOINT_RENAME_FAILED";
        cleanup.renamed = true;
        uncertain = true;
        if (!Sync(Parent())) return "CHECKPOINT_DIRECTORY_SYNC_FAILED";
        if (::fstat(file.Get(), &written) != 0 || !PrivateFile(written) ||
            ::fstatat(Parent(), name.c_str(), &named, AT_SYMLINK_NOFOLLOW) != 0 ||
            !SameVersion(written, named) || !Bound()) return "CHECKPOINT_COMMIT_CHANGED";
        if (!file.Close()) return "CHECKPOINT_CLOSE_FAILED";
        if (!Bound()) return "CHECKPOINT_PATH_CHANGED";
        uncertain = false;
        return nullptr;
    }
};
#endif
}

StrategyCheckpointStore::StrategyCheckpointStore(std::string directory, std::string filename,
    StrategyArtifactDescriptor descriptor, std::size_t maximumPayloadBytes)
    : m_directory(std::move(directory)), m_filename(std::move(filename)),
      m_descriptor(std::move(descriptor)), m_maximumPayloadBytes(maximumPayloadBytes) {}

bool StrategyCheckpointStore::IsReady() const
{ std::lock_guard<std::mutex> lock(m_mutex); return m_ready; }

StrategyCheckpointResult StrategyCheckpointStore::Load(const std::string& expectedRecordDigest)
{
    std::lock_guard<std::mutex> lock(m_mutex);
    m_ready = false;
    if (!DescriptorValid(m_descriptor) || m_maximumPayloadBytes == 0 ||
        m_maximumPayloadBytes > kMaximumPayload ||
        (!expectedRecordDigest.empty() && !Digest(expectedRecordDigest)))
        return Failure("CHECKPOINT_CONFIGURATION_INVALID");
#if !defined(__linux__)
    return Failure("CHECKPOINT_PLATFORM_UNSUPPORTED");
#else
    Directory directory;
    const char* error = directory.Open(m_directory, m_filename);
    if (error) return Failure(error);
    std::uintmax_t device, inode, lockDevice, lockInode;
    if (!directory.Identity(device, inode, lockDevice, lockInode) ||
        (m_haveBinding && (device != m_directoryDevice || inode != m_directoryInode ||
                          lockDevice != m_lockDevice || lockInode != m_lockInode)))
        return Failure("CHECKPOINT_PATH_CHANGED");
    std::string bytes; bool absent; struct stat version;
    error = directory.Read(m_filename, m_maximumPayloadBytes + kEnvelopeBytes,
                           bytes, absent, version);
    if (error) return Failure(error);
    if (absent)
    {
        if (!expectedRecordDigest.empty() || m_current.IsValid())
            return Failure("CHECKPOINT_EXPECTED_RECORD_MISSING");
        StrategyCheckpointResult result; result.accepted = true;
        result.reasonCode = "CHECKPOINT_EMPTY";
        m_directoryDevice = device; m_directoryInode = inode;
        m_lockDevice = lockDevice; m_lockInode = lockInode; m_haveBinding = true;
        m_ready = true; return result;
    }
    const std::string recordDigest = Hash(bytes);
    if (expectedRecordDigest.empty() || recordDigest.empty() || recordDigest != expectedRecordDigest)
        return Failure("CHECKPOINT_RECORD_DIGEST_MISMATCH");
    const std::string prefix = Prefix(m_descriptor);
    if (bytes.compare(0, prefix.size(), prefix) != 0) return Failure("CHECKPOINT_IDENTITY_MISMATCH");
    auto data = std::make_shared<VerifiedStrategyCheckpoint::Data>();
    data->descriptor = m_descriptor;
    std::size_t cursor = prefix.size(); std::uint64_t length = 0;
    if (!ReadU64(bytes, cursor, data->sequence) || !ReadU64(bytes, cursor, data->sourceGeneration) ||
        !ReadU64(bytes, cursor, data->savedAtMs) || !ReadU64(bytes, cursor, length) ||
        data->sequence == 0 || data->sourceGeneration == 0 || data->savedAtMs == 0 ||
        length == 0 || length > m_maximumPayloadBytes || length > m_descriptor.budget.maxCheckpointBytes ||
        cursor > bytes.size() || bytes.size() - cursor != length)
        return Failure("CHECKPOINT_FORMAT_INVALID");
    data->payload.assign(bytes, cursor, static_cast<std::size_t>(length));
    data->payloadDigest = Hash(data->payload);
    if (data->payloadDigest.empty()) return Failure("CHECKPOINT_HASH_FAILED");
    data->recordDigest = recordDigest;
    if (m_current.IsValid() && (data->sequence < m_current.Sequence() ||
        data->savedAtMs < m_current.SavedAtMs() || (data->sequence == m_current.Sequence() &&
        data->recordDigest != m_current.RecordDigest()))) return Failure("CHECKPOINT_HISTORY_REGRESSION");
    StrategyCheckpointResult result;
    result.accepted = true; result.reasonCode = "CHECKPOINT_LOADED";
    result.checkpoint.m_data = data;
    m_directoryDevice = device; m_directoryInode = inode;
    m_lockDevice = lockDevice; m_lockInode = lockInode; m_haveBinding = true;
    m_current = result.checkpoint; m_ready = true;
    return result;
#endif
}

StrategyCheckpointResult StrategyCheckpointStore::Save(std::uint64_t sequence,
    std::uint64_t sourceGeneration, const std::string& payload, std::uint64_t savedAtMs)
{
    std::lock_guard<std::mutex> lock(m_mutex);
    if (!m_ready) return Failure("CHECKPOINT_NOT_READY");
    if (sequence == 0 || sourceGeneration == 0 || savedAtMs == 0 || payload.empty() ||
        payload.size() > m_maximumPayloadBytes || payload.size() > m_descriptor.budget.maxCheckpointBytes)
        return Failure("CHECKPOINT_INPUT_INVALID");
    const std::uint64_t prior = m_current.Sequence();
    if (sequence != prior && (prior == std::numeric_limits<std::uint64_t>::max() || sequence != prior + 1))
        return Failure("CHECKPOINT_SEQUENCE_INVALID");
    if (savedAtMs < m_current.SavedAtMs()) return Failure("CHECKPOINT_TIME_REGRESSION");
    std::string bytes = Prefix(m_descriptor);
    U64(bytes, sequence); U64(bytes, sourceGeneration); U64(bytes, savedAtMs); Field(bytes, payload);
    auto data = std::make_shared<VerifiedStrategyCheckpoint::Data>();
    data->descriptor = m_descriptor; data->sequence = sequence; data->sourceGeneration = sourceGeneration;
    data->savedAtMs = savedAtMs; data->payload = payload; data->payloadDigest = Hash(payload);
    data->recordDigest = Hash(bytes);
    if (data->payloadDigest.empty() || data->recordDigest.empty()) return Failure("CHECKPOINT_HASH_FAILED");
    if (sequence == prior && data->recordDigest != m_current.RecordDigest())
        return Failure("CHECKPOINT_SEQUENCE_CONFLICT");
    // Prepare all successful result allocations before any persistent mutation.
    StrategyCheckpointResult result;
    result.attemptedRecordDigest = data->recordDigest;
    result.checkpoint.m_data = data;
    m_ready = false; // Every following error or exception requires explicit Load.
#if !defined(__linux__)
    return Failure("CHECKPOINT_PLATFORM_UNSUPPORTED");
#else
    Directory directory;
    const char* error = directory.Open(m_directory, m_filename);
    std::uintmax_t device, inode, lockDevice, lockInode;
    if (!error && (!directory.Identity(device, inode, lockDevice, lockInode) ||
        device != m_directoryDevice || inode != m_directoryInode ||
        lockDevice != m_lockDevice || lockInode != m_lockInode)) error = "CHECKPOINT_PATH_CHANGED";
    std::string disk; bool absent = false; struct stat version{};
    if (!error) error = directory.Read(m_filename, m_maximumPayloadBytes + kEnvelopeBytes,
                                        disk, absent, version);
    if (!error && (absent != !m_current.IsValid() ||
        (!absent && Hash(disk) != m_current.RecordDigest()))) error = "CHECKPOINT_CONCURRENT_WRITE";
    if (!error && sequence != prior)
        error = directory.Replace(m_filename, bytes, absent, version, result.uncertain);
    if (error)
    {
        result.reasonCode = error;
        result.checkpoint = VerifiedStrategyCheckpoint();
        return result;
    }
    result.accepted = true; result.duplicate = sequence == prior;
    result.reasonCode = result.duplicate ? "CHECKPOINT_DUPLICATE" : "CHECKPOINT_SAVED";
    m_current = result.checkpoint; m_ready = true;
    return result;
#endif
}

StrategyRuntimeControlResult StrategyRuntimeControl::RestoreCheckpoint(
    const std::string& moduleId, std::uint64_t expectedGeneration,
    const VerifiedStrategyCheckpoint& checkpoint, std::uint64_t observedAtMs)
{
    if (!checkpoint.IsValid()) return Reject("STRATEGY_CHECKPOINT_RECEIPT_INVALID", nullptr);
    std::lock_guard<std::mutex> lock(m_mutex);
    const auto found = m_records.find(moduleId);
    if (found == m_records.end()) return Reject("STRATEGY_NOT_FOUND", nullptr);
    auto& snapshot = found->second;
    if (!Guard(snapshot, expectedGeneration, observedAtMs))
        return GuardFailure(snapshot, expectedGeneration, observedAtMs);
    if (snapshot.phase != StrategyRuntimePhase::Admitted)
        return Reject("STRATEGY_CHECKPOINT_RESTORE_STATE_INVALID", &snapshot);
    if (!SameDescriptor(snapshot.descriptor, checkpoint.Descriptor()))
        return Reject("STRATEGY_CHECKPOINT_IDENTITY_MISMATCH", &snapshot);
    if (checkpoint.SavedAtMs() > observedAtMs)
        return Reject("STRATEGY_CHECKPOINT_TIME_INVALID", &snapshot);
    if (snapshot.checkpointSequence != 0)
    {
        if (snapshot.checkpointSequence != checkpoint.Sequence() ||
            snapshot.checkpointDigest != checkpoint.PayloadDigest() ||
            snapshot.checkpointBytes != checkpoint.Payload().size())
            return Reject("STRATEGY_CHECKPOINT_RESTORE_CONFLICT", &snapshot);
        auto result = Accept("STRATEGY_CHECKPOINT_RESTORE_DUPLICATE", snapshot);
        result.duplicate = true; return result;
    }
    auto proposed = snapshot;
    if (!Advance(proposed, observedAtMs)) return Reject("STRATEGY_GENERATION_EXHAUSTED", &snapshot);
    proposed.checkpointSequence = checkpoint.Sequence();
    proposed.checkpointDigest = checkpoint.PayloadDigest();
    proposed.checkpointBytes = checkpoint.Payload().size();
    proposed.reasonCode = "STRATEGY_CHECKPOINT_RESTORED";
    // Remain Admitted. No code execution, payload decoding or old generation revival.
    return Commit(snapshot, std::move(proposed));
}
