#include "oms_journal.h"
#include "observability/runtime_telemetry.h"

#include <chrono>
#include <charconv>
#include <string_view>
#include <algorithm>
#include <cerrno>
#include <climits>
#include <cmath>
#include <cstdlib>
#include <fstream>
#include <fcntl.h>
#include <limits>
#include <locale>
#include <sstream>
#include <sys/stat.h>
#include <unistd.h>
#include <utility>

namespace {

static bool ParseCanonicalUnsignedEnv(const char* raw,
                                      unsigned long long& parsed)
{
    if (raw == nullptr || *raw == '\0') return false;
    const std::string value(raw);
    if (value.size() > 1 && value[0] == '0') return false;
    for (const char c : value)
        if (c < '0' || c > '9') return false;
    char* end = nullptr;
    errno = 0;
    const unsigned long long number = std::strtoull(value.c_str(), &end, 10);
    if (errno != 0 || end == value.c_str() || *end != '\0') return false;
    parsed = number;
    return true;
}

static std::string JsonNumber(double v)
{
    std::ostringstream oss;
    oss.imbue(std::locale::classic());
    oss.setf(std::ios::fixed);
    oss.precision(8);
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

// Decode the journal's flat scalar record, not arbitrary JSON documents.
// Field lookup and conversion share one cursor: nested/string contents cannot
// masquerade as top-level fields, and malformed values never become defaults.
struct JournalStringField
{
    const char* name;
    std::string OmsJournalEvent::* member;
};

constexpr JournalStringField kJournalStringFields[] = {
    {"event", &OmsJournalEvent::eventType},
    {"req_id", &OmsJournalEvent::reqId},
    {"client_req_id", &OmsJournalEvent::clientReqId},
    {"trace_id", &OmsJournalEvent::traceId},
    {"event_id", &OmsJournalEvent::eventId},
    {"risk_code", &OmsJournalEvent::riskCode},
    {"venue", &OmsJournalEvent::venue},
    {"strategy", &OmsJournalEvent::strategy},
    {"account", &OmsJournalEvent::account},
    {"execution_domain", &OmsJournalEvent::executionDomain},
    {"request_hash", &OmsJournalEvent::requestHash},
    {"venue_correlation_id", &OmsJournalEvent::venueCorrelationId},
    {"broker_callback_type", &OmsJournalEvent::brokerCallbackType},
    {"broker_service_epoch", &OmsJournalEvent::brokerServiceEpoch},
    {"broker_message", &OmsJournalEvent::brokerMessage},
    {"broker_advanced_order_reject_json", &OmsJournalEvent::brokerAdvancedOrderRejectJson},
    {"broker_why_held", &OmsJournalEvent::brokerWhyHeld},
    {"broker_execution_id", &OmsJournalEvent::brokerExecutionId},
    {"instrument", &OmsJournalEvent::instrument},
    {"side", &OmsJournalEvent::side},
    {"status", &OmsJournalEvent::status},
    {"reason", &OmsJournalEvent::reason},
    {"source", &OmsJournalEvent::source},
};

bool ReadUtf8Scalar(const std::string& text, std::size_t& pos, std::uint32_t& value)
{
    if (pos == text.size()) return false;
    const unsigned char first = static_cast<unsigned char>(text[pos++]);
    if (first < 0x80) { value = first; return true; }
    unsigned int remaining;
    std::uint32_t minimum;
    if (first >= 0xc2 && first <= 0xdf)
        { remaining = 1; value = first & 0x1f; minimum = 0x80; }
    else if (first >= 0xe0 && first <= 0xef)
        { remaining = 2; value = first & 0x0f; minimum = 0x800; }
    else if (first >= 0xf0 && first <= 0xf4)
        { remaining = 3; value = first & 0x07; minimum = 0x10000; }
    else return false;
    if (text.size() - pos < remaining) return false;
    while (remaining--)
    {
        const unsigned char next = static_cast<unsigned char>(text[pos++]);
        if ((next & 0xc0) != 0x80) return false;
        value = (value << 6) | (next & 0x3f);
    }
    return value >= minimum && value <= 0x10ffff &&
        !(value >= 0xd800 && value <= 0xdfff);
}

bool ValidJournalStrings(const OmsJournalEvent& event)
{
    std::size_t total = 0;
    for (const auto& field : kJournalStringFields)
    {
        const std::string& value = event.*(field.member);
        if (value.size() > OmsJournal::kMaximumRecordBytes - total) return false;
        total += value.size();
        std::size_t pos = 0;
        std::uint32_t scalar = 0;
        while (pos < value.size())
            if (!ReadUtf8Scalar(value, pos, scalar)) return false;
    }
    return true;
}

class JournalRecordReader
{
public:
    explicit JournalRecordReader(const std::string& input) : m_input(input) {}

    bool Read(OmsJournalEvent& event)
    {
        SkipWhitespace();
        if (!Consume('{')) return false;
        SkipWhitespace();
        if (Consume('}')) return false; // A nonempty event name is required.
        for (;;)
        {
            std::string key;
            if (!ReadString(key)) return false;
            SkipWhitespace();
            if (!Consume(':')) return false;
            SkipWhitespace();
            if (!ReadField(key, event)) return false;
            SkipWhitespace();
            if (Consume('}')) break;
            if (!Consume(',')) return false;
            SkipWhitespace();
        }
        SkipWhitespace();
        return m_pos == m_input.size() && !event.eventType.empty() &&
            event.schemaVersion >= 1 && event.schemaVersion <= OmsJournal::kSchemaVersion;
    }

private:
    void SkipWhitespace()
    {
        while (m_pos < m_input.size() &&
               (m_input[m_pos] == ' ' || m_input[m_pos] == '\t' ||
                m_input[m_pos] == '\r' || m_input[m_pos] == '\n')) ++m_pos;
    }

    bool Consume(char ch)
    {
        if (m_pos == m_input.size() || m_input[m_pos] != ch) return false;
        ++m_pos;
        return true;
    }

    bool Mark(std::size_t field)
    {
        const std::uint64_t bit = std::uint64_t{1} << field;
        if (m_seen & bit) return false;
        m_seen |= bit;
        return true;
    }

    bool ReadHex(std::uint32_t& value)
    {
        if (m_input.size() - m_pos < 4) return false;
        value = 0;
        for (int digit = 0; digit < 4; ++digit)
        {
            const char ch = m_input[m_pos++];
            unsigned int nibble;
            if (ch >= '0' && ch <= '9') nibble = ch - '0';
            else if (ch >= 'a' && ch <= 'f') nibble = ch - 'a' + 10;
            else if (ch >= 'A' && ch <= 'F') nibble = ch - 'A' + 10;
            else return false;
            value = (value << 4) | nibble;
        }
        return true;
    }

    static void AppendUtf8(std::uint32_t value, std::string& output)
    {
        if (value < 0x80) output.push_back(static_cast<char>(value));
        else
        {
            if (value < 0x800) output.push_back(static_cast<char>(0xc0 | (value >> 6)));
            else
            {
                if (value < 0x10000) output.push_back(static_cast<char>(0xe0 | (value >> 12)));
                else
                {
                    output.push_back(static_cast<char>(0xf0 | (value >> 18)));
                    output.push_back(static_cast<char>(0x80 | ((value >> 12) & 0x3f)));
                }
                output.push_back(static_cast<char>(0x80 | ((value >> 6) & 0x3f)));
            }
            output.push_back(static_cast<char>(0x80 | (value & 0x3f)));
        }
    }

    bool ReadString(std::string& output)
    {
        if (!Consume('"')) return false;
        while (m_pos < m_input.size())
        {
            if (Consume('"')) return true;
            if (Consume('\\'))
            {
                if (m_pos == m_input.size()) return false;
                const char escaped = m_input[m_pos++];
                switch (escaped)
                {
                case '"': case '\\': case '/': output.push_back(escaped); break;
                case 'b': output.push_back('\b'); break;
                case 'f': output.push_back('\f'); break;
                case 'n': output.push_back('\n'); break;
                case 'r': output.push_back('\r'); break;
                case 't': output.push_back('\t'); break;
                case 'u':
                {
                    std::uint32_t value = 0;
                    if (!ReadHex(value)) return false;
                    if (value >= 0xd800 && value <= 0xdbff)
                    {
                        std::uint32_t low = 0;
                        if (!Consume('\\') || !Consume('u') || !ReadHex(low) ||
                            low < 0xdc00 || low > 0xdfff) return false;
                        value = 0x10000 + ((value - 0xd800) << 10) + low - 0xdc00;
                    }
                    else if (value >= 0xdc00 && value <= 0xdfff) return false;
                    AppendUtf8(value, output);
                    break;
                }
                default: return false;
                }
            }
            else
            {
                const std::size_t start = m_pos;
                std::uint32_t value = 0;
                if (!ReadUtf8Scalar(m_input, m_pos, value) || value < 0x20) return false;
                output.append(m_input, start, m_pos - start);
            }
        }
        return false;
    }

    static bool Digit(char ch) { return ch >= '0' && ch <= '9'; }

    bool NumberToken(std::string_view& token)
    {
        const std::size_t start = m_pos;
        Consume('-');
        if (m_pos == m_input.size()) return false;
        if (Consume('0'))
        {
            if (m_pos < m_input.size() && Digit(m_input[m_pos])) return false;
        }
        else
        {
            if (m_input[m_pos] < '1' || m_input[m_pos] > '9') return false;
            while (m_pos < m_input.size() && Digit(m_input[m_pos])) ++m_pos;
        }
        if (Consume('.'))
        {
            const std::size_t begin = m_pos;
            while (m_pos < m_input.size() && Digit(m_input[m_pos])) ++m_pos;
            if (begin == m_pos) return false;
        }
        if (Consume('e') || Consume('E'))
        {
            if (!Consume('+')) Consume('-');
            const std::size_t begin = m_pos;
            while (m_pos < m_input.size() && Digit(m_input[m_pos])) ++m_pos;
            if (begin == m_pos) return false;
        }
        token = std::string_view(m_input.data() + start, m_pos - start);
        return true;
    }

    template<typename Integer>
    bool ReadInteger(Integer& value)
    {
        std::string_view token;
        if (!NumberToken(token)) return false;
        const auto parsed = std::from_chars(token.data(), token.data() + token.size(), value);
        return parsed.ec == std::errc{} && parsed.ptr == token.data() + token.size();
    }

    bool ReadDouble(double& value)
    {
        std::string_view token;
        if (!NumberToken(token)) return false;
        std::istringstream stream{std::string(token)};
        stream.imbue(std::locale::classic());
        stream >> std::noskipws >> value;
        if (!stream || !stream.eof() || !std::isfinite(value)) return false;
        // Some standard libraries silently round extreme underflow to zero.
        // Absent optional fields may default; present nonzero values may not.
        if (value == 0)
            for (char ch : token)
            {
                if (ch == 'e' || ch == 'E') break;
                if (ch >= '1' && ch <= '9') return false;
            }
        return true;
    }

    bool ReadField(const std::string& key, OmsJournalEvent& event)
    {
        std::size_t index = 0;
        for (const auto& field : kJournalStringFields)
        {
            if (key == field.name) return Mark(index) && ReadString(event.*(field.member));
            ++index;
        }
        if (key == "schema_version") return Mark(index + 0) && ReadInteger(event.schemaVersion);
        if (key == "ts_ms") return Mark(index + 1) && ReadInteger(event.tsMs);
        if (key == "order_id") return Mark(index + 2) && ReadInteger(event.orderId);
        if (key == "broker_connection_epoch") return Mark(index + 3) && ReadInteger(event.brokerConnectionEpoch);
        if (key == "broker_request_id") return Mark(index + 4) && ReadInteger(event.brokerRequestId);
        if (key == "broker_error_code") return Mark(index + 5) && ReadInteger(event.brokerErrorCode);
        if (key == "qty") return Mark(index + 6) && ReadDouble(event.qty);
        if (key == "price") return Mark(index + 7) && ReadDouble(event.price);
        if (key == "broker_remaining_quantity") return Mark(index + 8) && ReadDouble(event.brokerRemainingQuantity);
        if (key == "broker_market_cap_price") return Mark(index + 9) && ReadDouble(event.brokerMarketCapPrice);
        return false; // Unknown fields require an explicit record-version change.
    }

    const std::string& m_input;
    std::size_t m_pos = 0;
    std::uint64_t m_seen = 0;
};

} // namespace

bool OmsJournal::IsCriticalEventType(const std::string& eventType)
{
    return eventType == "order_intent" || eventType == "place_send_attempt" ||
           eventType == "flatten_intent" ||
           eventType == "flatten_send_attempt" ||
           eventType == "cancel_send_attempt" ||
           eventType == "place_sent" || eventType == "flatten_sent" ||
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
        RuntimeRecordJournalFailure("OMS_PATH_IDENTITY_INVALID");
        return false;
    }
    return true;
}

bool OmsJournal::WriteLineToPinnedFileLocked(const std::string& line, bool durable)
{
    if (m_fd < 0 || m_writePoisoned)
    {
        ++m_writeFailTotal;
        RuntimeRecordJournalFailure("OMS_JOURNAL_UNAVAILABLE");
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
            RuntimeRecordJournalFailure("OMS_WRITE_FAILED");
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
            RuntimeRecordJournalFailure("OMS_FDATASYNC_FAILED");
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

    unsigned long long parsedEnv = 0;
    const char* pBatch = std::getenv("HEPTA_OMS_BATCH_SIZE");
    m_batchSize = 8;
    if (ParseCanonicalUnsignedEnv(pBatch, parsedEnv) &&
        parsedEnv <= std::numeric_limits<std::size_t>::max())
        m_batchSize = static_cast<std::size_t>(std::max(1ULL, parsedEnv));
    const char* pInterval = std::getenv("HEPTA_OMS_FLUSH_INTERVAL_MS");
    m_flushIntervalMs = 250;
    if (ParseCanonicalUnsignedEnv(pInterval, parsedEnv) &&
        parsedEnv <= static_cast<unsigned long long>(LLONG_MAX))
        m_flushIntervalMs = static_cast<long long>(parsedEnv);
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
    RuntimeLatencyScope appendLatency("hepta_oms_append_latency_microseconds");

    std::lock_guard<std::mutex> lk(m_mtx);
    if (m_path.empty() || m_fd < 0 || m_writePoisoned ||
        evt.eventType.empty() || evt.schemaVersion > kSchemaVersion ||
        !ValidJournalStrings(evt) || !std::isfinite(evt.qty) ||
        !std::isfinite(evt.price) ||
        !std::isfinite(evt.brokerRemainingQuantity) ||
        !std::isfinite(evt.brokerMarketCapPrice)) return false;
    if (!ValidatePinnedPathLocked()) return false;

    std::string line = BuildJsonLine(evt);
    if (line.size() > kMaximumRecordBytes) return false;
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
            // A critical record is written directly only after every older
            // asynchronous record has been drained.  Otherwise a queued
            // non-critical event can be appended to the file *after* this
            // critical event, even though Append() observed the opposite
            // order.  Replay is defined in append order, so preserving that
            // ordering is mandatory whenever critical writes are synchronous.
            // Keep the legacy knob for configuration compatibility, but do
            // not let it disable this safety barrier.
            if (!FlushQueuedNoLock()) return false;
            if (!FlushBufferedLocked()) return false;
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
    RuntimeTelemetry::Global().SetGaugeKey(
        "hepta_oms_journal_backlog",
        static_cast<double>(out.queueDepth + out.bufferedDepth));
    RuntimeTelemetry::Global().SetGaugeKey(
        "hepta_oms_journal_write_failures",
        static_cast<double>(out.writeFailTotal));
    RuntimeTelemetry::Global().SetGaugeKey(
        "hepta_oms_journal_poisoned", out.writePoisoned ? 1.0 : 0.0);
    return out;
}

std::string OmsJournal::GetPath() const
{
    std::lock_guard<std::mutex> lk(m_mtx);
    return m_path;
}

int OmsJournal::Replay(const std::function<void(const OmsJournalEvent&)>& onEvent) const
{
    RuntimeLatencyScope replayLatency("hepta_oms_replay_latency_microseconds");

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
            if (newline > kMaximumRecordBytes) return -1;
            const std::string line = pending.substr(0, newline);
            pending.erase(0, newline + 1);
            if (line.empty()) return -1;
            OmsJournalEvent evt;
            if (!ParseJsonLine(line, evt)) return -1;
            events.push_back(std::move(evt));
        }
        if (pending.size() > kMaximumRecordBytes) return -1;
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
        default:
            if ((unsigned char)ch < 0x20) out += ' ';
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
    // Journal lines are replay identity and JSON; keep numeric formatting
    // independent of the embedding process locale.
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
    if (line.empty() || line.size() > kMaximumRecordBytes) return false;
    OmsJournalEvent parsed;
    parsed.schemaVersion = 1; // Pre-versioned historical records.
    if (!JournalRecordReader(line).Read(parsed)) return false;
    if (!parsed.reqId.empty() && !parsed.clientReqId.empty() &&
        parsed.reqId != parsed.clientReqId) return false;
    if (parsed.reqId.empty()) parsed.reqId = parsed.clientReqId;
    if (parsed.clientReqId.empty()) parsed.clientReqId = parsed.reqId;
    parsed.rawLine = line;
    out = std::move(parsed);
    return true;
}
