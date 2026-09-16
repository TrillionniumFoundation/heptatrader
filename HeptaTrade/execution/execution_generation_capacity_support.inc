// Capacity bridge compiled after execution_generation_support.inc so it can
// reuse the same private generation identity helpers. This is not another
// translation unit or authority path.

bool OmsGenerationStore::RecoveryCapacity(
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
