        ready = ScanSegmentsLocked() && OpenActiveLocked();
    }
    catch (...)
    {
        ready = false;
    }
    if (!ready)
    {
        m_active.reset();
        ::flock(m_lockFd, LOCK_UN);
        ::close(m_lockFd);
        ::close(m_directoryFd);
        m_lockFd = -1;
        m_directoryFd = -1;
        return false;
    }
    m_initialized = true;
    return true;
}

bool OmsSegmentedJournal::RotateLocked()
{
    if (!m_initialized || !ValidateDirectoryLocked()) return false;
    if (!m_active && !OpenActiveLocked()) return false;
    OmsJournalHealthSnapshot health = m_active->GetHealthSnapshot();
    std::size_t activeOnDisk = 0, activeRetained = 0;
    if (health.writePoisoned ||
        !ObserveActiveLocked(activeOnDisk, activeRetained) ||
        activeOnDisk > std::numeric_limits<std::size_t>::max() - activeRetained)
        return false;
    const std::size_t logicalActive = activeOnDisk + activeRetained;
    if (logicalActive == 0) return true;
    if (m_segments.size() >= m_limits.maximumSealedSegments ||
        logicalActive > m_limits.maximumTotalBytes - m_sealedBytes)
    {
        IncrementSaturating(m_rotationCapacityRejects);
        return false;
    }

    const int replayed = m_active->Replay({});
    if (replayed < 0 || static_cast<std::size_t>(replayed) != m_activeRecords)
    {
        IncrementSaturating(m_segmentIntegrityRejects);
        return false;
    }
    health = m_active->GetHealthSnapshot();
    if (health.writePoisoned ||
        !ObserveActiveLocked(activeOnDisk, activeRetained) ||
        activeRetained != 0 || activeOnDisk == 0 ||
        activeOnDisk > m_limits.maximumActiveBytes)
    {
        IncrementSaturating(m_segmentIntegrityRejects);
        return false;
    }

    OmsJournalSegmentDescriptor descriptor;
    descriptor.sequence = m_nextSequence;
    descriptor.bytes = activeOnDisk;
    descriptor.records = m_activeRecords;
    descriptor.filename = m_activeName;
    std::size_t verifiedRecords = 0;
    // Build a temporary descriptor with a digest-independent filename, then
    // hash/parse through the same bounded reader after computing its identity.
    int flags = O_RDONLY;
#ifdef O_CLOEXEC
    flags |= O_CLOEXEC;
#endif
#ifdef O_NOFOLLOW
    flags |= O_NOFOLLOW;
#endif
    const int fd = ::openat(m_directoryFd, m_activeName.c_str(), flags);
    if (fd < 0) return false;
    struct stat before;
    Sha256Accumulator digest;
    char buffer[8192];
    off_t offset = 0;
    bool hashed = StatFileDescriptor(fd, before) && PrivateSegmentMetadata(before) &&
        static_cast<std::size_t>(before.st_size) == descriptor.bytes;
    while (hashed && offset < before.st_size)
    {
        const off_t remaining = before.st_size - offset;
        const std::size_t wanted = remaining > static_cast<off_t>(sizeof(buffer)) ?
            sizeof(buffer) : static_cast<std::size_t>(remaining);
        const ssize_t count = ReadAt(fd, buffer, wanted, offset);
        if (count <= 0) { hashed = false; break; }
        digest.Update(reinterpret_cast<const unsigned char*>(buffer),
                      static_cast<std::size_t>(count));
        offset += count;
    }
    struct stat after;
    if (hashed && (!StatFileDescriptor(fd, after) ||
                   !SameFileObservation(before, after))) hashed = false;
    ::close(fd);
    if (!hashed) return false;
    descriptor.digest = digest.FinalHex();
    descriptor.filename = FormatSegmentFilename(
        m_segmentPrefix, descriptor.sequence, descriptor.records, descriptor.digest);
    if (descriptor.filename.empty()) return false;

    m_active.reset();
    if (!RenameNoReplaceAt(m_directoryFd, m_activeName, descriptor.filename))
    {
        OpenActiveLocked();
        return false;
    }
    if (!SyncDirectory(m_directoryFd))
    {
        IncrementSaturating(m_segmentIntegrityRejects);
        m_initialized = false;
        return false;
    }
    bool verified = false;
    try
    {
        verified = ReadSegmentLocked(descriptor, nullptr, verifiedRecords) &&
            verifiedRecords == descriptor.records;
    }
    catch (...)
    {
        verified = false;
    }
    if (!verified)
    {
        m_initialized = false;
        return false;
    }

    m_sealedBytes += descriptor.bytes;
    m_sealedRecords += descriptor.records;
    m_segments.push_back(std::move(descriptor));
    m_activeRecords = 0;
    ++m_nextSequence;
    IncrementSaturating(m_rotations);
    if (!OpenActiveLocked())
    {
        m_initialized = false;
        return false;
    }
    return true;
}

bool OmsSegmentedJournal::Rotate()
{
    std::lock_guard<std::mutex> lock(m_segmentMutex);
    return RotateLocked();
}

bool OmsSegmentedJournal::Append(const OmsJournalEvent& event)
{
    std::lock_guard<std::mutex> lock(m_segmentMutex);
    if (!m_initialized || !ValidateDirectoryLocked()) return false;
    if (!m_active && !OpenActiveLocked()) return false;
    if (event.eventType.empty() || event.schemaVersion > OmsJournal::kSchemaVersion ||
        !ValidJournalStrings(event) || !std::isfinite(event.qty) ||
        !std::isfinite(event.price) ||
        !std::isfinite(event.brokerRemainingQuantity) ||
        !std::isfinite(event.brokerMarketCapPrice)) return false;
    const std::string line = OmsJournal::BuildJsonLine(event);
    if (line.size() > OmsJournal::kMaximumRecordBytes) return false;
    const std::size_t bytes = line.size() + 1;
    if (bytes > m_limits.maximumActiveBytes)
    {
        IncrementSaturating(m_rotationCapacityRejects);
        return false;
    }
    OmsJournalHealthSnapshot health = m_active->GetHealthSnapshot();
    std::size_t activeOnDisk = 0, activeRetained = 0;
    if (health.writePoisoned ||
        !ObserveActiveLocked(activeOnDisk, activeRetained) ||
        activeOnDisk > std::numeric_limits<std::size_t>::max() - activeRetained)
        return false;
    std::size_t logicalActive = activeOnDisk + activeRetained;
    if (logicalActive > m_limits.maximumTotalBytes ||
        m_sealedBytes > m_limits.maximumTotalBytes - logicalActive ||
        bytes > m_limits.maximumTotalBytes - m_sealedBytes - logicalActive ||
        m_sealedRecords + m_activeRecords >= m_limits.maximumTotalRecords)
    {
        IncrementSaturating(m_totalCapacityRejects);
        return false;
    }
    if (bytes > m_limits.maximumActiveBytes - logicalActive)
    {
        if (!RotateLocked()) return false;
        health = m_active->GetHealthSnapshot();
        if (!ObserveActiveLocked(activeOnDisk, activeRetained)) return false;
        logicalActive = activeOnDisk + activeRetained;
        if (logicalActive != 0 || bytes > m_limits.maximumActiveBytes) return false;
    }
    if (!m_active->Append(event)) return false;
    ++m_activeRecords;
    return true;
}

int OmsSegmentedJournal::Replay(
    const std::function<void(const OmsJournalEvent&)>& onEvent) const
{
    if (m_segmentReplayInProgress.test_and_set(std::memory_order_acquire))
    {
        std::lock_guard<std::mutex> lock(m_segmentMutex);
        IncrementSaturating(m_replayBusyRejects);
        return -1;
    }
    struct Reservation
    {
        std::atomic_flag& flag;
        ~Reservation() { flag.clear(std::memory_order_release); }
    } reservation{m_segmentReplayInProgress};

    std::unique_lock<std::mutex> lock(m_segmentMutex);
    if (!m_initialized || !ValidateDirectoryLocked() || !m_active) return -1;
    const OmsJournalHealthSnapshot health = m_active->GetHealthSnapshot();
    std::size_t activeOnDisk = 0, activeRetained = 0;
    if (health.writePoisoned ||
        !ObserveActiveLocked(activeOnDisk, activeRetained) ||
        activeOnDisk > std::numeric_limits<std::size_t>::max() - activeRetained)
        return -1;
    const std::size_t activeLogical = activeOnDisk + activeRetained;
