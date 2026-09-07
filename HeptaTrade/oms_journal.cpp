#include "oms_journal.h"

#include <chrono>
#include <algorithm>
#include <cerrno>
#include <climits>
#include <cmath>
#include <cstdlib>
#include <fstream>
#include <fcntl.h>
#include <limits>
#include <locale>
#include <map>
#include <set>
#include <sstream>
#include <sys/stat.h>
#include <unistd.h>
#include <utility>

namespace {

static std::string JsonNumber(double v)
{
    std::ostringstream oss;
    oss.imbue(std::locale::classic());
    oss.precision(std::numeric_limits<double>::max_digits10);
    oss << v;
    return oss.str();
}

static bool SyncFileData(int fd)
{
    int result;
    do
    {
        result = ::fdatasync(fd);
    } while (result != 0 && errno == EINTR);
    return result == 0;
}

static bool SyncDirectory(int fd)
{
    int result;
    do
    {
        result = ::fsync(fd);
    } while (result != 0 && errno == EINTR);
    return result == 0;
}

static ssize_t ReadAt(int fd, void* data, std::size_t size, off_t offset)
{
    ssize_t result;
    do
    {
        result = ::pread(fd, data, size, offset);
    } while (result < 0 && errno == EINTR);
    return result;
}

static bool StatFileDescriptor(int fd, struct stat& metadata)
{
    int result;
    do
    {
        result = ::fstat(fd, &metadata);
    } while (result != 0 && errno == EINTR);
    return result == 0;
}

static bool StatPathWithoutFollowingLinks(const std::string& path,
                                          struct stat& metadata)
{
    int result;
    do
    {
        result = ::lstat(path.c_str(), &metadata);
    } while (result != 0 && errno == EINTR);
    return result == 0;
}

static bool HasPrivateRegularFileMetadata(const struct stat& metadata)
{
    return S_ISREG(metadata.st_mode) && metadata.st_uid == ::geteuid() &&
        (metadata.st_mode & 07777) == 0600 && metadata.st_nlink == 1;
}

struct JsonField
{
    enum Kind { String, Number, Other };
    Kind kind = Other;
    std::string value;
};

typedef std::map<std::string, JsonField> JsonFields;

class JsonObjectParser
{
public:
    explicit JsonObjectParser(const std::string& input)
        : m_input(input)
    {
    }

    bool Parse(JsonFields& fields)
    {
        SkipWhitespace();
        if (!ParseObject(0, &fields)) return false;
        SkipWhitespace();
        return m_pos == m_input.size();
    }

private:
    void SkipWhitespace()
    {
        while (m_pos < m_input.size())
        {
            const char value = m_input[m_pos];
            if (value != ' ' && value != '\t' && value != '\r' && value != '\n') break;
            ++m_pos;
        }
    }

    bool Consume(char value)
    {
        if (m_pos >= m_input.size() || m_input[m_pos] != value) return false;
        ++m_pos;
        return true;
    }

    bool ConsumeLiteral(const char* literal)
    {
        for (const char* cursor = literal; *cursor != '\0'; ++cursor)
        {
            if (!Consume(*cursor)) return false;
        }
        return true;
    }

    bool ParseHexQuad(unsigned int& value)
    {
        if (m_pos + 4 > m_input.size()) return false;
        value = 0;
        for (unsigned int i = 0; i < 4; ++i)
        {
            const unsigned char c = static_cast<unsigned char>(m_input[m_pos++]);
            value <<= 4;
            if (c >= '0' && c <= '9') value += c - '0';
            else if (c >= 'a' && c <= 'f') value += c - 'a' + 10;
            else if (c >= 'A' && c <= 'F') value += c - 'A' + 10;
            else return false;
        }
        return true;
    }

    static void AppendCodePoint(unsigned int codePoint, std::string& value)
    {
        if (codePoint <= 0x7f)
        {
            value.push_back(static_cast<char>(codePoint));
        }
        else if (codePoint <= 0x7ff)
        {
            value.push_back(static_cast<char>(0xc0 | (codePoint >> 6)));
            value.push_back(static_cast<char>(0x80 | (codePoint & 0x3f)));
        }
        else if (codePoint <= 0xffff)
        {
            value.push_back(static_cast<char>(0xe0 | (codePoint >> 12)));
            value.push_back(static_cast<char>(0x80 | ((codePoint >> 6) & 0x3f)));
            value.push_back(static_cast<char>(0x80 | (codePoint & 0x3f)));
        }
        else
        {
            value.push_back(static_cast<char>(0xf0 | (codePoint >> 18)));
            value.push_back(static_cast<char>(0x80 | ((codePoint >> 12) & 0x3f)));
            value.push_back(static_cast<char>(0x80 | ((codePoint >> 6) & 0x3f)));
            value.push_back(static_cast<char>(0x80 | (codePoint & 0x3f)));
        }
    }

    bool ParseRawUtf8(std::string& value)
    {
        const std::size_t start = m_pos;
        const unsigned char lead = static_cast<unsigned char>(m_input[m_pos]);
        unsigned int length = 0;
        unsigned int codePoint = 0;
        if (lead >= 0xc2 && lead <= 0xdf)
        {
            length = 2;
            codePoint = lead & 0x1f;
        }
        else if (lead >= 0xe0 && lead <= 0xef)
        {
            length = 3;
            codePoint = lead & 0x0f;
        }
        else if (lead >= 0xf0 && lead <= 0xf4)
        {
            length = 4;
            codePoint = lead & 0x07;
        }
        else
        {
            return false;
        }
        if (m_pos + length > m_input.size()) return false;
        for (unsigned int i = 1; i < length; ++i)
        {
            const unsigned char next =
                static_cast<unsigned char>(m_input[m_pos + i]);
            if ((next & 0xc0) != 0x80) return false;
            codePoint = (codePoint << 6) | (next & 0x3f);
        }
        if ((length == 2 && codePoint < 0x80) ||
            (length == 3 && codePoint < 0x800) ||
            (length == 4 && codePoint < 0x10000) ||
            (codePoint >= 0xd800 && codePoint <= 0xdfff) ||
            codePoint > 0x10ffff)
            return false;
        m_pos += length;
        value.append(m_input, start, length);
        return true;
    }

    bool ParseString(std::string& value)
    {
        value.clear();
        if (!Consume('"')) return false;
        while (m_pos < m_input.size())
        {
            const unsigned char c = static_cast<unsigned char>(m_input[m_pos++]);
            if (c == '"') return true;
            if (c < 0x20) return false;
            if (c >= 0x80)
            {
                --m_pos;
                if (!ParseRawUtf8(value)) return false;
                continue;
            }
            if (c != '\\')
            {
                value.push_back(static_cast<char>(c));
                continue;
            }
            if (m_pos >= m_input.size()) return false;
            const char escaped = m_input[m_pos++];
            if (escaped == '"' || escaped == '\\' || escaped == '/')
                value.push_back(escaped);
            else if (escaped == 'b') value.push_back('\b');
            else if (escaped == 'f') value.push_back('\f');
            else if (escaped == 'n') value.push_back('\n');
            else if (escaped == 'r') value.push_back('\r');
            else if (escaped == 't') value.push_back('\t');
            else if (escaped == 'u')
            {
                unsigned int codePoint = 0;
                if (!ParseHexQuad(codePoint)) return false;
                if (codePoint >= 0xd800 && codePoint <= 0xdbff)
                {
                    if (m_pos + 2 > m_input.size() ||
                        m_input[m_pos] != '\\' || m_input[m_pos + 1] != 'u')
                        return false;
                    m_pos += 2;
                    unsigned int low = 0;
                    if (!ParseHexQuad(low) || low < 0xdc00 || low > 0xdfff)
                        return false;
                    codePoint = 0x10000 +
                        ((codePoint - 0xd800) << 10) + (low - 0xdc00);
                }
                else if (codePoint >= 0xdc00 && codePoint <= 0xdfff)
                {
                    return false;
                }
                AppendCodePoint(codePoint, value);
            }
            else
            {
                return false;
            }
        }
        return false;
    }

    bool ParseNumber()
    {
        if (m_pos < m_input.size() && m_input[m_pos] == '-') ++m_pos;
        if (m_pos >= m_input.size()) return false;
        if (m_input[m_pos] == '0')
        {
            ++m_pos;
            if (m_pos < m_input.size() && m_input[m_pos] >= '0' &&
                m_input[m_pos] <= '9') return false;
        }
        else
        {
            if (m_input[m_pos] < '1' || m_input[m_pos] > '9') return false;
            do
            {
                ++m_pos;
            } while (m_pos < m_input.size() && m_input[m_pos] >= '0' &&
                     m_input[m_pos] <= '9');
        }
        if (m_pos < m_input.size() && m_input[m_pos] == '.')
        {
            ++m_pos;
            const std::size_t digits = m_pos;
            while (m_pos < m_input.size() && m_input[m_pos] >= '0' &&
                   m_input[m_pos] <= '9') ++m_pos;
            if (m_pos == digits) return false;
        }
        if (m_pos < m_input.size() &&
            (m_input[m_pos] == 'e' || m_input[m_pos] == 'E'))
        {
            ++m_pos;
            if (m_pos < m_input.size() &&
                (m_input[m_pos] == '+' || m_input[m_pos] == '-')) ++m_pos;
            const std::size_t digits = m_pos;
            while (m_pos < m_input.size() && m_input[m_pos] >= '0' &&
                   m_input[m_pos] <= '9') ++m_pos;
            if (m_pos == digits) return false;
        }
        return true;
    }

    bool ParseArray(unsigned int depth)
    {
        if (depth >= 64 || !Consume('[')) return false;
        SkipWhitespace();
        if (Consume(']')) return true;
        for (;;)
        {
            JsonField ignored;
            if (!ParseValue(depth + 1, ignored)) return false;
            SkipWhitespace();
            if (Consume(']')) return true;
            if (!Consume(',')) return false;
            SkipWhitespace();
        }
    }

    bool ParseObject(unsigned int depth, JsonFields* fields = nullptr)
    {
        if (depth >= 64 || !Consume('{')) return false;
        SkipWhitespace();
        if (Consume('}')) return true;
        std::set<std::string> keys;
        for (;;)
        {
            std::string key;
            if (!ParseString(key) || !keys.insert(key).second) return false;
            SkipWhitespace();
            if (!Consume(':')) return false;
            SkipWhitespace();
            JsonField field;
            if (!ParseValue(depth + 1, field)) return false;
            if (fields != nullptr) fields->emplace(key, std::move(field));
            SkipWhitespace();
            if (Consume('}')) return true;
            if (!Consume(',')) return false;
            SkipWhitespace();
        }
    }

    bool ParseValue(unsigned int depth, JsonField& field)
    {
        if (depth >= 64 || m_pos >= m_input.size()) return false;
        switch (m_input[m_pos])
        {
        case '{': return ParseObject(depth);
        case '[': return ParseArray(depth);
        case '"':
            field.kind = JsonField::String;
            return ParseString(field.value);
        case 't': return ConsumeLiteral("true");
        case 'f': return ConsumeLiteral("false");
        case 'n': return ConsumeLiteral("null");
        default:
            const std::size_t start = m_pos;
            if (!ParseNumber()) return false;
            field.kind = JsonField::Number;
            field.value = m_input.substr(start, m_pos - start);
            return true;
        }
    }

private:
    const std::string& m_input;
    std::size_t m_pos = 0;
};

static bool ReadString(const JsonFields& fields, const char* key, std::string& out)
{
    const JsonFields::const_iterator found = fields.find(key);
    if (found == fields.end()) return true;
    if (found->second.kind != JsonField::String) return false;
    out = found->second.value;
    return true;
}

template <typename Integer>
bool ReadInteger(const JsonFields& fields, const char* key, Integer& out)
{
    const JsonFields::const_iterator found = fields.find(key);
    if (found == fields.end()) return true;
    const JsonField& field = found->second;
    if (field.kind != JsonField::Number ||
        field.value.find_first_of(".eE") != std::string::npos) return false;
    const bool negative = field.value[0] == '-';
    if (negative && !std::numeric_limits<Integer>::is_signed) return false;
    const std::uint64_t maximum =
        static_cast<std::uint64_t>(std::numeric_limits<Integer>::max());
    const std::uint64_t limit = negative ? maximum + 1 : maximum;
    std::uint64_t magnitude = 0;
    for (std::size_t index = negative ? 1 : 0; index < field.value.size(); ++index)
    {
        const unsigned int digit = field.value[index] - '0';
        if (digit > 9 || magnitude > (limit - digit) / 10) return false;
        magnitude = magnitude * 10 + digit;
    }
    if (negative && magnitude == maximum + 1)
        out = std::numeric_limits<Integer>::min();
    else if (negative)
        out = -static_cast<Integer>(magnitude);
    else
        out = static_cast<Integer>(magnitude);
    return true;
}

static bool ReadDouble(const JsonFields& fields, const char* key, double& out)
{
    const JsonFields::const_iterator found = fields.find(key);
    if (found == fields.end()) return true;
    const JsonField& field = found->second;
    if (field.kind != JsonField::Number) return false;
    std::istringstream input(field.value);
    input.imbue(std::locale::classic());
    double value = 0.0;
    input >> value;
    if (input.fail() || !input.eof() || !std::isfinite(value)) return false;
    // An unrepresentable nonzero value must not silently become an absent zero.
    const std::string significand = field.value.substr(0, field.value.find_first_of("eE"));
    if (value == 0.0 && significand.find_first_of("123456789") != std::string::npos)
        return false;
    out = value;
    return true;
}

} // namespace

bool OmsJournal::IsCriticalEventType(const std::string& eventType)
{
    return eventType == "order_intent" || eventType == "place_send_attempt" ||
           eventType == "flatten_intent" ||
           eventType == "flatten_send_attempt" ||
           eventType == "cancel_send_attempt" ||
           eventType == "place_sent" || eventType == "place_activated" || eventType == "flatten_sent" ||
           eventType == "flatten_noop" ||
           eventType == "flatten_reject" ||
           eventType == "flatten_outcome_uncertain" ||
           eventType == "place_outcome_uncertain" ||
           eventType == "status" ||
           eventType == "cancel" || eventType == "reject" || eventType == "risk_blocked" ||
           eventType == "session_owner_fenced" || eventType == "session_owner_fence_release" ||
           eventType == "order_owner_reconciled_terminal" ||
           eventType == "execution_projection_failed" ||
           eventType == "execution_projection_resolved" ||
           eventType == "execution_command_resolved" ||
           eventType == "cancel_command_resolved" ||
           eventType == "broker_order_status" ||
           eventType == "broker_order_accepted" ||
           eventType == "broker_error" ||
           eventType == "broker_execution" ||
           eventType == "broker_completed_order" ||
           eventType == "broker_completed_orders_end" ||
           eventType == "broker_execution_details_end";
}

bool OmsJournal::WriteLineDirect(const std::string& line)
{
    return WriteLineToPinnedFileLocked(line, true);
}

bool OmsJournal::ValidatePinnedPathLocked()
{
    if (m_fd < 0 || m_path.empty() || m_writePoisoned) return false;

    struct stat pinnedMetadata;
    struct stat pathMetadata;
    const bool safe = StatFileDescriptor(m_fd, pinnedMetadata) &&
        StatPathWithoutFollowingLinks(m_path, pathMetadata) &&
        HasPrivateRegularFileMetadata(pinnedMetadata) &&
        HasPrivateRegularFileMetadata(pathMetadata) &&
        pinnedMetadata.st_dev == pathMetadata.st_dev &&
        pinnedMetadata.st_ino == pathMetadata.st_ino;
    if (!safe)
    {
        m_writePoisoned = true;
        ++m_writeFailTotal;
        return false;
    }
    return true;
}

bool OmsJournal::WriteLineToPinnedFileLocked(const std::string& line, bool durable)
{
    if (m_fd < 0 || m_writePoisoned)
    {
        ++m_writeFailTotal;
        return false;
    }
    if (!ValidatePinnedPathLocked()) return false;

    const std::string record = line + "\n";
    std::size_t offset = 0;
    while (offset < record.size())
    {
        if (offset > 0 && !ValidatePinnedPathLocked()) return false;
        const std::size_t remaining = record.size() - offset;
        const std::size_t maximumWrite =
            static_cast<std::size_t>(std::numeric_limits<ssize_t>::max());
        const std::size_t wanted = std::min(remaining, maximumWrite);
        const ssize_t count = ::write(m_fd, record.data() + offset,
                                      wanted);
        if (count < 0 && errno == EINTR) continue;
        if (count <= 0)
        {
            m_writePoisoned = true;
            ++m_writeFailTotal;
            return false;
        }
        offset += static_cast<std::size_t>(count);
    }

    // A rename/replacement detected after the write is still fatal.  The
    // record may exist on the pinned inode for forensics, but it must never be
    // reported as committed because a restart would resolve a different path.
    if (!ValidatePinnedPathLocked()) return false;

    if (durable)
    {
        if (!SyncFileData(m_fd))
        {
            m_writePoisoned = true;
            ++m_writeFailTotal;
            ++m_durableSyncFailures;
            return false;
        }
        ++m_durableSyncWrites;
    }
    ++m_flushedTotal;
    m_lastFlushMs = NowEpochMs();
    return true;
}

bool OmsJournal::OpenPinnedFileLocked(const std::string& path)
{
    const std::size_t slash = path.find_last_of('/');
    const std::string parent = slash == std::string::npos ? "." :
        (slash == 0 ? "/" : path.substr(0, slash));

    int flags = O_RDWR | O_APPEND;
#ifdef O_CLOEXEC
    flags |= O_CLOEXEC;
#endif
#ifdef O_NOFOLLOW
    flags |= O_NOFOLLOW;
#endif
    int fd = ::open(path.c_str(), flags | O_CREAT | O_EXCL, 0600);
    const bool created = fd >= 0;
    if (fd < 0 && errno == EEXIST)
        fd = ::open(path.c_str(), flags);
    if (fd < 0) return false;

#ifndef O_CLOEXEC
    if (::fcntl(fd, F_SETFD, FD_CLOEXEC) != 0)
    {
        ::close(fd);
        return false;
    }
#endif
    if (created && ::fchmod(fd, 0600) != 0)
    {
        ::close(fd);
        return false;
    }

    struct stat metadata;
    struct stat pathMetadata;
    char tail = '\0';
    const bool safe = StatFileDescriptor(fd, metadata) &&
        StatPathWithoutFollowingLinks(path, pathMetadata) &&
        HasPrivateRegularFileMetadata(metadata) &&
        HasPrivateRegularFileMetadata(pathMetadata) &&
        metadata.st_dev == pathMetadata.st_dev &&
        metadata.st_ino == pathMetadata.st_ino &&
        (metadata.st_size == 0 ||
         (ReadAt(fd, &tail, 1, metadata.st_size - 1) == 1 && tail == '\n'));
    if (!safe)
    {
        ::close(fd);
        return false;
    }
    // Persist the file inode before the directory entry.  This is required for
    // a newly created empty journal and harmless for an existing journal.
    if (!SyncFileData(fd))
    {
        ::close(fd);
        return false;
    }

    int directoryFlags = O_RDONLY;
#ifdef O_CLOEXEC
    directoryFlags |= O_CLOEXEC;
#endif
#ifdef O_DIRECTORY
    directoryFlags |= O_DIRECTORY;
#endif
    const int directoryFd = ::open(parent.c_str(), directoryFlags);
    struct stat directoryMetadata;
    const bool directoryDurable = directoryFd >= 0 &&
        StatFileDescriptor(directoryFd, directoryMetadata) &&
        S_ISDIR(directoryMetadata.st_mode) && SyncDirectory(directoryFd);
    if (directoryFd >= 0) ::close(directoryFd);
    if (!directoryDurable)
    {
        ::close(fd);
        return false;
    }
    m_fd = fd;
    return true;
}

bool OmsJournal::ClosePinnedFileLocked()
{
    if (m_fd < 0) return true;
    bool ok = true;
    if (!m_writePoisoned && !SyncFileData(m_fd))
    {
        m_writePoisoned = true;
        ++m_writeFailTotal;
        ++m_durableSyncFailures;
        ok = false;
    }
    if (::close(m_fd) != 0)
    {
        ++m_writeFailTotal;
        ok = false;
    }
    m_fd = -1;
    return ok;
}

bool OmsJournal::FlushBufferedLocked()
{
    if (m_bufferedLines.empty()) return true;
    if (m_path.empty()) return false;

    std::size_t written = 0;
    while (written < m_bufferedLines.size())
    {
        if (!WriteLineToPinnedFileLocked(m_bufferedLines[written], false))
        {
            if (written > 0)
                m_bufferedLines.erase(m_bufferedLines.begin(),
                    m_bufferedLines.begin() + static_cast<std::ptrdiff_t>(written));
            return false;
        }
        ++written;
    }
    m_bufferedLines.clear();
    return true;
}

bool OmsJournal::FlushQueuedNoLock()
{
    while (!m_asyncQueue.empty())
    {
        m_bufferedLines.emplace_back(std::move(m_asyncQueue.front()));
        m_asyncQueue.pop_front();
        if (m_batchSize > 0 && m_bufferedLines.size() >= m_batchSize)
        {
            if (!FlushBufferedLocked()) return false;
        }
    }
    return FlushBufferedLocked();
}

void OmsJournal::WorkerLoop()
{
    std::unique_lock<std::mutex> lk(m_mtx);
    while (!m_stopWorker)
    {
        const auto waitDur = std::chrono::milliseconds(std::max(1LL, m_flushIntervalMs));
        m_cv.wait_for(lk, waitDur, [&]() { return m_stopWorker || !m_asyncQueue.empty(); });
        try
        {
            FlushQueuedNoLock();
        }
        catch (...)
        {
            m_writePoisoned = true;
            ++m_writeFailTotal;
            m_stopWorker = true;
        }
    }
    try
    {
        FlushQueuedNoLock();
    }
    catch (...)
    {
        m_writePoisoned = true;
        ++m_writeFailTotal;
    }
}

OmsJournal::~OmsJournal() noexcept
{
    {
        std::lock_guard<std::mutex> lk(m_mtx);
        m_stopWorker = true;
        m_cv.notify_all();
    }
    if (m_worker.joinable()) m_worker.join();

    std::lock_guard<std::mutex> lk(m_mtx);
    try
    {
        FlushQueuedNoLock();
        FlushBufferedLocked();
    }
    catch (...)
    {
        m_writePoisoned = true;
        ++m_writeFailTotal;
    }
    ClosePinnedFileLocked();
}

bool OmsJournal::Init(const std::string& path)
{
    if (path.empty()) return false;

    std::lock_guard<std::mutex> lk(m_mtx);
    // Init is deliberately one-shot.  Reinitializing an object with a live fd
    // or worker could discard queued records or replace a joinable thread.
    if (m_fd >= 0 || !m_path.empty() || m_worker.joinable()) return false;

    m_writePoisoned = false;
    m_bufferedLines.clear();
    m_asyncQueue.clear();
    m_stopWorker = false;
    m_enqueuedTotal = 0;
    m_flushedTotal = 0;
    m_writeFailTotal = 0;
    m_criticalSyncWrites = 0;
    m_criticalAsyncWrites = 0;
    m_durableSyncWrites = 0;
    m_durableSyncFailures = 0;
    m_maxQueueDepth = 0;

    const char* pBatch = std::getenv("HEPTA_OMS_BATCH_SIZE");
    m_batchSize = (pBatch && pBatch[0] != '\0') ?
        static_cast<std::size_t>(std::max(1, std::atoi(pBatch))) : 8;
    const char* pInterval = std::getenv("HEPTA_OMS_FLUSH_INTERVAL_MS");
    m_flushIntervalMs = (pInterval && pInterval[0] != '\0') ?
        std::max(0, std::atoi(pInterval)) : 250;
    m_lastFlushMs = NowEpochMs();

    const char* pAsync = std::getenv("HEPTA_OMS_ASYNC_FLUSH");
    m_asyncEnabled = (pAsync != nullptr &&
        (std::string(pAsync) == "1" || std::string(pAsync) == "true" ||
         std::string(pAsync) == "TRUE"));
    const char* pSyncCritical = std::getenv("HEPTA_OMS_SYNC_CRITICAL");
    m_syncCritical = (pSyncCritical == nullptr) ? true :
        (std::string(pSyncCritical) == "1" || std::string(pSyncCritical) == "true" ||
         std::string(pSyncCritical) == "TRUE");
    const char* pCritFlushQueued = std::getenv("HEPTA_OMS_CRITICAL_FLUSH_QUEUED");
    m_criticalFlushQueued = (pCritFlushQueued != nullptr &&
        (std::string(pCritFlushQueued) == "1" ||
         std::string(pCritFlushQueued) == "true" ||
         std::string(pCritFlushQueued) == "TRUE"));

    try
    {
        if (m_bufferedLines.capacity() < m_batchSize)
            m_bufferedLines.reserve(m_batchSize);
        if (!OpenPinnedFileLocked(path)) return false;
        m_path = path;
        if (m_asyncEnabled)
            m_worker = std::thread(&OmsJournal::WorkerLoop, this);
    }
    catch (...)
    {
        m_stopWorker = true;
        ClosePinnedFileLocked();
        m_path.clear();
        m_asyncEnabled = false;
        return false;
    }
    return true;
}

bool OmsJournal::Append(const OmsJournalEvent& evt)
{
    std::lock_guard<std::mutex> lk(m_mtx);
    if (m_path.empty() || m_fd < 0 || m_writePoisoned ||
        evt.eventType.empty() || !std::isfinite(evt.qty) ||
        !std::isfinite(evt.price) ||
        !std::isfinite(evt.brokerRemainingQuantity) ||
        !std::isfinite(evt.brokerMarketCapPrice)) return false;
    if (!ValidatePinnedPathLocked()) return false;

    std::string line = BuildJsonLine(evt);
    OmsJournalEvent checked;
    if (!ParseJsonLine(line, checked)) return false;
    const bool critical = IsCriticalEventType(evt.eventType);

    if (critical)
    {
        if (!m_syncCritical)
        {
            ++m_criticalAsyncWrites;
            if (m_asyncEnabled)
            {
                m_asyncQueue.emplace_back(std::move(line));
                ++m_enqueuedTotal;
                m_maxQueueDepth = std::max(m_maxQueueDepth, (long long)m_asyncQueue.size());
                m_cv.notify_one();
                return true;
            }
            m_bufferedLines.emplace_back(std::move(line));
            ++m_enqueuedTotal;
            return FlushBufferedLocked();
        }
        ++m_criticalSyncWrites;
        if (m_asyncEnabled)
        {
            if (m_criticalFlushQueued)
            {
                if (!FlushQueuedNoLock()) return false;
                if (!FlushBufferedLocked()) return false;
            }
            return WriteLineDirect(line);
        }
        if (!FlushBufferedLocked()) return false;
        return WriteLineDirect(line);
    }

    if (m_asyncEnabled)
    {
        m_asyncQueue.emplace_back(std::move(line));
        ++m_enqueuedTotal;
        m_maxQueueDepth = std::max(m_maxQueueDepth, (long long)m_asyncQueue.size());
        if (m_asyncQueue.size() >= m_batchSize)
        {
            m_cv.notify_one();
        }
        return true;
    }

    m_bufferedLines.emplace_back(std::move(line));
    ++m_enqueuedTotal;
    const long long nowMs = NowEpochMs();
    const bool shouldFlushBySize = (m_batchSize <= 1 || m_bufferedLines.size() >= m_batchSize);
    const bool shouldFlushByTime = (m_flushIntervalMs == 0 || (nowMs - m_lastFlushMs) >= m_flushIntervalMs);
    if (shouldFlushBySize || shouldFlushByTime)
    {
        return FlushBufferedLocked();
    }
    return true;
}

OmsJournalHealthSnapshot OmsJournal::GetHealthSnapshot() const
{
    std::lock_guard<std::mutex> lk(m_mtx);
    OmsJournalHealthSnapshot out;
    out.asyncEnabled = m_asyncEnabled;
    out.syncCritical = m_syncCritical;
    out.queueDepth = m_asyncQueue.size();
    out.bufferedDepth = m_bufferedLines.size();
    out.enqueuedTotal = m_enqueuedTotal;
    out.flushedTotal = m_flushedTotal;
    out.writeFailTotal = m_writeFailTotal;
    out.criticalSyncWrites = m_criticalSyncWrites;
    out.criticalAsyncWrites = m_criticalAsyncWrites;
    out.durableSyncWrites = m_durableSyncWrites;
    out.durableSyncFailures = m_durableSyncFailures;
    out.maxQueueDepth = m_maxQueueDepth;
    out.lastFlushMs = m_lastFlushMs;
    out.writePoisoned = m_writePoisoned;
    return out;
}

std::string OmsJournal::GetPath() const
{
    std::lock_guard<std::mutex> lk(m_mtx);
    return m_path;
}

int OmsJournal::Replay(const std::function<void(const OmsJournalEvent&)>& onEvent) const
{
    std::unique_lock<std::mutex> lk(m_mtx);
    OmsJournal* const self = const_cast<OmsJournal*>(this);
    if (!self->FlushQueuedNoLock() || !self->FlushBufferedLocked() ||
        m_path.empty() || m_fd < 0 || m_writePoisoned ||
        !self->ValidatePinnedPathLocked()) return -1;
    if (!SyncFileData(m_fd))
    {
        self->m_writePoisoned = true;
        ++self->m_writeFailTotal;
        ++self->m_durableSyncFailures;
        return -1;
    }

    struct stat metadata;
    if (!StatFileDescriptor(m_fd, metadata) || !S_ISREG(metadata.st_mode)) return -1;
    std::vector<OmsJournalEvent> events;
    std::string pending;
    pending.reserve(8192);
    char buffer[8192];
    off_t offset = 0;
    while (offset < metadata.st_size)
    {
        const off_t remaining = metadata.st_size - offset;
        const std::size_t wanted = remaining > static_cast<off_t>(sizeof(buffer)) ?
            sizeof(buffer) : static_cast<std::size_t>(remaining);
        const ssize_t count = ReadAt(m_fd, buffer, wanted, offset);
        if (count <= 0) return -1;
        offset += count;
        pending.append(buffer, static_cast<std::size_t>(count));

        std::size_t newline = std::string::npos;
        while ((newline = pending.find('\n')) != std::string::npos)
        {
            const std::string line = pending.substr(0, newline);
            pending.erase(0, newline + 1);
            if (line.empty()) return -1;
            OmsJournalEvent evt;
            if (!ParseJsonLine(line, evt)) return -1;
            events.push_back(evt);
        }
    }
    if (!pending.empty()) return -1;
    if (!self->ValidatePinnedPathLocked()) return -1;
    if (events.size() > static_cast<std::size_t>(std::numeric_limits<int>::max()))
        return -1;
    lk.unlock();
    if (onEvent)
        for (std::vector<OmsJournalEvent>::const_iterator it = events.begin();
             it != events.end(); ++it)
            onEvent(*it);
    return static_cast<int>(events.size());
}

long long OmsJournal::NowEpochMs()
{
    using namespace std::chrono;
    return duration_cast<milliseconds>(system_clock::now().time_since_epoch()).count();
}

std::string OmsJournal::EscapeJson(const std::string& s)
{
    std::string out;
    out.reserve(s.size() + 8);
    for (char ch : s)
    {
        switch (ch)
        {
        case '\\': out += "\\\\"; break;
        case '"': out += "\\\""; break;
        case '\n': out += "\\n"; break;
        case '\r': out += "\\r"; break;
        case '\t': out += "\\t"; break;
        case '\b': out += "\\b"; break;
        case '\f': out += "\\f"; break;
        default:
            if (static_cast<unsigned char>(ch) < 0x20)
            {
                static const char digits[] = "0123456789abcdef";
                out += "\\u00";
                out += digits[(static_cast<unsigned char>(ch) >> 4) & 0xf];
                out += digits[static_cast<unsigned char>(ch) & 0xf];
            }
            else out += ch;
            break;
        }
    }
    return out;
}

std::string OmsJournal::BuildJsonLine(const OmsJournalEvent& evt)
{
    const std::string reqId = evt.reqId.empty() ? evt.clientReqId : evt.reqId;
    std::ostringstream oss;
    oss.imbue(std::locale::classic());
    oss << "{"
        << "\"schema_version\":" << (evt.schemaVersion > 0 ? evt.schemaVersion : kSchemaVersion)
        << ",\"event\":\"" << EscapeJson(evt.eventType) << "\""
        << ",\"ts_ms\":" << evt.tsMs
        << ",\"order_id\":" << evt.orderId
        << ",\"req_id\":\"" << EscapeJson(reqId) << "\""
        << ",\"client_req_id\":\"" << EscapeJson(reqId) << "\""
        << ",\"trace_id\":\"" << EscapeJson(evt.traceId) << "\""
        << ",\"event_id\":\"" << EscapeJson(evt.eventId) << "\""
        << ",\"risk_code\":\"" << EscapeJson(evt.riskCode) << "\""
        << ",\"venue\":\"" << EscapeJson(evt.venue) << "\""
        << ",\"strategy\":\"" << EscapeJson(evt.strategy) << "\""
        << ",\"account\":\"" << EscapeJson(evt.account) << "\""
        << ",\"execution_domain\":\"" << EscapeJson(evt.executionDomain) << "\""
        << ",\"request_hash\":\"" << EscapeJson(evt.requestHash) << "\""
        << ",\"venue_correlation_id\":\"" << EscapeJson(evt.venueCorrelationId) << "\""
        << ",\"broker_callback_type\":\"" << EscapeJson(evt.brokerCallbackType) << "\""
        << ",\"broker_service_epoch\":\"" << EscapeJson(evt.brokerServiceEpoch) << "\""
        << ",\"broker_connection_epoch\":" << evt.brokerConnectionEpoch
        << ",\"broker_request_id\":" << evt.brokerRequestId
        << ",\"broker_error_code\":" << evt.brokerErrorCode
        << ",\"broker_message\":\"" << EscapeJson(evt.brokerMessage) << "\""
        << ",\"broker_advanced_order_reject_json\":\""
        << EscapeJson(evt.brokerAdvancedOrderRejectJson) << "\""
        << ",\"broker_why_held\":\"" << EscapeJson(evt.brokerWhyHeld) << "\""
        << ",\"broker_execution_id\":\"" << EscapeJson(evt.brokerExecutionId) << "\""
        << ",\"broker_remaining_quantity\":"
        << JsonNumber(evt.brokerRemainingQuantity)
        << ",\"broker_market_cap_price\":"
        << JsonNumber(evt.brokerMarketCapPrice)
        << ",\"instrument\":\"" << EscapeJson(evt.instrument) << "\""
        << ",\"side\":\"" << EscapeJson(evt.side) << "\""
        << ",\"qty\":" << JsonNumber(evt.qty)
        << ",\"price\":" << JsonNumber(evt.price)
        << ",\"status\":\"" << EscapeJson(evt.status) << "\""
        << ",\"reason\":\"" << EscapeJson(evt.reason) << "\""
        << ",\"source\":\"" << EscapeJson(evt.source) << "\""
        << "}";
    return oss.str();
}

bool OmsJournal::ParseJsonLine(const std::string& line, OmsJournalEvent& out)
{
    out = OmsJournalEvent{};
    out.rawLine = line;
    out.schemaVersion = 1;
    JsonFields fields;
    if (!JsonObjectParser(line).Parse(fields)) return false;
    if (!ReadInteger(fields, "schema_version", out.schemaVersion) ||
        out.schemaVersion < 1 || out.schemaVersion > kSchemaVersion) return false;
    if (!ReadString(fields, "event", out.eventType)) return false;
    if (!ReadString(fields, "req_id", out.reqId)) return false;
    if (!ReadString(fields, "client_req_id", out.clientReqId)) return false;
    if (!ReadString(fields, "trace_id", out.traceId)) return false;
    if (!ReadString(fields, "event_id", out.eventId)) return false;
    if (!ReadString(fields, "risk_code", out.riskCode)) return false;
    if (!ReadString(fields, "venue", out.venue)) return false;
    if (!ReadString(fields, "strategy", out.strategy)) return false;
    if (!ReadString(fields, "account", out.account)) return false;
    if (!ReadString(fields, "execution_domain", out.executionDomain)) return false;
    if (!ReadString(fields, "request_hash", out.requestHash)) return false;
    if (!ReadString(fields, "venue_correlation_id", out.venueCorrelationId)) return false;
    if (!ReadString(fields, "broker_callback_type", out.brokerCallbackType)) return false;
    if (!ReadString(fields, "broker_service_epoch", out.brokerServiceEpoch)) return false;
    if (!ReadString(fields, "broker_message", out.brokerMessage)) return false;
    if (!ReadString(fields, "broker_advanced_order_reject_json", out.brokerAdvancedOrderRejectJson)) return false;
    if (!ReadString(fields, "broker_why_held", out.brokerWhyHeld)) return false;
    if (!ReadString(fields, "broker_execution_id", out.brokerExecutionId)) return false;
    if (!ReadString(fields, "instrument", out.instrument)) return false;
    if (!ReadString(fields, "side", out.side)) return false;
    if (!ReadString(fields, "status", out.status)) return false;
    if (!ReadString(fields, "reason", out.reason)) return false;
    if (!ReadString(fields, "source", out.source)) return false;
    if (!ReadInteger(fields, "ts_ms", out.tsMs)) return false;
    if (!ReadInteger(fields, "order_id", out.orderId)) return false;
    if (!ReadInteger(fields, "broker_connection_epoch", out.brokerConnectionEpoch)) return false;
    if (!ReadInteger(fields, "broker_request_id", out.brokerRequestId)) return false;
    if (!ReadInteger(fields, "broker_error_code", out.brokerErrorCode)) return false;
    if (!ReadDouble(fields, "qty", out.qty)) return false;
    if (!ReadDouble(fields, "price", out.price)) return false;
    if (!ReadDouble(fields, "broker_remaining_quantity", out.brokerRemainingQuantity)) return false;
    if (!ReadDouble(fields, "broker_market_cap_price", out.brokerMarketCapPrice)) return false;
    if (out.eventType.empty()) return false;
    if (out.reqId.empty()) out.reqId = out.clientReqId;
    if (out.clientReqId.empty()) out.clientReqId = out.reqId;
    return true;
}
