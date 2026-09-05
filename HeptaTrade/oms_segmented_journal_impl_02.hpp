            const std::uint32_t majority = (a & b) ^ (a & c) ^ (b & c);
            const std::uint32_t second = s0 + majority;
            h = g; g = f; f = e; e = d + first;
            d = c; c = b; b = a; a = first + second;
        }
        m_state[0] += a; m_state[1] += b; m_state[2] += c; m_state[3] += d;
        m_state[4] += e; m_state[5] += f; m_state[6] += g; m_state[7] += h;
    }

    std::array<std::uint32_t, 8> m_state;
    std::array<unsigned char, 64> m_block{};
    std::size_t m_used = 0;
    std::uint64_t m_totalBytes = 0;
};

std::string FormatSegmentFilename(const std::string& prefix,
                                  std::uint64_t sequence,
                                  std::size_t records,
                                  const std::string& digest)
{
    char sequenceText[kSegmentSequenceDigits + 1];
    char recordText[kSegmentRecordDigits + 1];
    const int sequenceLength = std::snprintf(
        sequenceText, sizeof(sequenceText), "%020llu",
        static_cast<unsigned long long>(sequence));
    const int recordLength = std::snprintf(
        recordText, sizeof(recordText), "%010llu",
        static_cast<unsigned long long>(records));
    if (sequenceLength != static_cast<int>(kSegmentSequenceDigits) ||
        recordLength != static_cast<int>(kSegmentRecordDigits) ||
        digest.size() != kSha256HexDigits) return std::string();
    return prefix + sequenceText + '.' + recordText + '.' + digest + ".jsonl";
}

bool ParseSegmentFilename(const std::string& prefix,
                          const std::string& filename,
                          OmsJournalSegmentDescriptor& descriptor)
{
    const std::size_t expected = prefix.size() + kSegmentSequenceDigits + 1 +
        kSegmentRecordDigits + 1 + kSha256HexDigits + 6;
    if (filename.size() != expected || filename.compare(0, prefix.size(), prefix) != 0)
        return false;
    std::size_t offset = prefix.size();
    if (filename[offset + kSegmentSequenceDigits] != '.') return false;
    const char* sequenceFirst = filename.data() + offset;
    const char* sequenceLast = sequenceFirst + kSegmentSequenceDigits;
    std::uint64_t sequence = 0;
    const auto sequenceResult = std::from_chars(sequenceFirst, sequenceLast, sequence);
    if (sequenceResult.ec != std::errc{} || sequenceResult.ptr != sequenceLast || sequence == 0)
        return false;
    offset += kSegmentSequenceDigits + 1;
    if (filename[offset + kSegmentRecordDigits] != '.') return false;
    const char* recordFirst = filename.data() + offset;
    const char* recordLast = recordFirst + kSegmentRecordDigits;
    unsigned long long records = 0;
    const auto recordResult = std::from_chars(recordFirst, recordLast, records);
    if (recordResult.ec != std::errc{} || recordResult.ptr != recordLast || records == 0 ||
        records > OmsSegmentedJournalLimits::kReplayRecordsCeiling) return false;
    offset += kSegmentRecordDigits + 1;
    const std::string digest = filename.substr(offset, kSha256HexDigits);
    for (const char ch : digest)
        if (!((ch >= '0' && ch <= '9') || (ch >= 'a' && ch <= 'f'))) return false;
    offset += kSha256HexDigits;
    if (filename.compare(offset, 6, ".jsonl") != 0) return false;
    descriptor.sequence = sequence;
    descriptor.records = static_cast<std::size_t>(records);
    descriptor.digest = digest;
    descriptor.filename = filename;
    return true;
}

bool RenameNoReplaceAt(int directoryFd,
                       const std::string& from,
                       const std::string& to)
{
#if defined(__linux__) && defined(SYS_renameat2)
#ifndef RENAME_NOREPLACE
#define RENAME_NOREPLACE (1U << 0)
#endif
    long result;
    do
    {
        result = ::syscall(SYS_renameat2, directoryFd, from.c_str(),
                           directoryFd, to.c_str(), RENAME_NOREPLACE);
    } while (result != 0 && errno == EINTR);
    return result == 0;
#else
    (void)directoryFd;
    (void)from;
    (void)to;
    errno = ENOSYS;
    return false;
#endif
}
}

OmsSegmentedJournal::~OmsSegmentedJournal() noexcept
{
    std::lock_guard<std::mutex> lock(m_segmentMutex);
    m_active.reset();
    if (m_lockFd >= 0)
    {
        ::flock(m_lockFd, LOCK_UN);
        ::close(m_lockFd);
        m_lockFd = -1;
    }
    if (m_directoryFd >= 0)
    {
        ::close(m_directoryFd);
        m_directoryFd = -1;
    }
}

std::string OmsSegmentedJournal::DescriptorPathLocked(
    const std::string& filename) const
{
    return std::string("/proc/self/fd/") + std::to_string(m_directoryFd) + '/' + filename;
}

bool OmsSegmentedJournal::ValidateDirectoryLocked() const
{
    if (m_directoryFd < 0 || m_lockFd < 0) return false;
    struct stat metadata;
    return StatFileDescriptor(m_directoryFd, metadata) &&
        PrivateDirectoryMetadata(metadata) &&
        static_cast<std::uintmax_t>(metadata.st_dev) == m_directoryDevice &&
        static_cast<std::uintmax_t>(metadata.st_ino) == m_directoryInode;
}

bool OmsSegmentedJournal::ObserveActiveLocked(
    std::size_t& onDiskBytes, std::size_t& retainedBytes) const
{
    return m_active && ObserveJournal(*m_active, onDiskBytes, retainedBytes);
}

bool OmsSegmentedJournal::ReadSegmentLocked(
    const OmsJournalSegmentDescriptor& segment,
    std::vector<OmsJournalEvent>* events,
    std::size_t& recordCount) const
{
    recordCount = 0;
    int flags = O_RDONLY;
#ifdef O_CLOEXEC
    flags |= O_CLOEXEC;
#endif
#ifdef O_NOFOLLOW
    flags |= O_NOFOLLOW;
#endif
    const int fd = ::openat(m_directoryFd, segment.filename.c_str(), flags);
    if (fd < 0)
    {
        IncrementSaturating(m_segmentIntegrityRejects);
        return false;
    }
    struct stat before;
    if (!StatFileDescriptor(fd, before) || !PrivateSegmentMetadata(before) ||
        static_cast<std::uintmax_t>(before.st_size) != segment.bytes)
    {
        ::close(fd);
        IncrementSaturating(m_segmentIntegrityRejects);
        return false;
    }

    Sha256Accumulator digest;
    std::string pending;
    pending.reserve(8192);
    char buffer[8192];
    off_t offset = 0;
    bool valid = true;
    while (valid && offset < before.st_size)
    {
        const off_t remaining = before.st_size - offset;
        const std::size_t wanted = remaining > static_cast<off_t>(sizeof(buffer)) ?
            sizeof(buffer) : static_cast<std::size_t>(remaining);
        const ssize_t count = ReadAt(fd, buffer, wanted, offset);
        if (count <= 0)
        {
            valid = false;
            break;
        }
        digest.Update(reinterpret_cast<const unsigned char*>(buffer),
                      static_cast<std::size_t>(count));
        offset += count;
        pending.append(buffer, static_cast<std::size_t>(count));
        std::size_t newline = std::string::npos;
        while ((newline = pending.find('\n')) != std::string::npos)
        {
            if (newline == 0 || newline > OmsJournal::kMaximumRecordBytes ||
                recordCount >= segment.records ||
                (events != nullptr && events->size() >= m_limits.maximumTotalRecords))
            {
                valid = false;
                break;
            }
            OmsJournalEvent event;
            if (!OmsJournal::ParseJsonLine(pending.substr(0, newline), event))
            {
                valid = false;
                break;
            }
            pending.erase(0, newline + 1);
            ++recordCount;
            if (events != nullptr) events->push_back(std::move(event));
        }
        if (pending.size() > OmsJournal::kMaximumRecordBytes) valid = false;
    }
    struct stat after;
    if (!StatFileDescriptor(fd, after)) valid = false;
    ::close(fd);
    if (!pending.empty() || recordCount != segment.records ||
