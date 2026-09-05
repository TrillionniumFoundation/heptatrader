    if (activeLogical > m_limits.maximumTotalBytes ||
        m_sealedBytes > m_limits.maximumTotalBytes - activeLogical ||
        m_sealedRecords + m_activeRecords > m_limits.maximumTotalRecords)
        return -1;

    std::vector<OmsJournalEvent> events;
    events.reserve(m_sealedRecords + m_activeRecords);
    for (const OmsJournalSegmentDescriptor& segment : m_segments)
    {
        std::size_t observed = 0;
        if (!ReadSegmentLocked(segment, &events, observed) ||
            observed != segment.records) return -1;
    }
    bool activeOverflow = false;
    const int activeRead = m_active->Replay([&](const OmsJournalEvent& event) {
        if (events.size() >= m_limits.maximumTotalRecords)
        {
            activeOverflow = true;
            return;
        }
        events.push_back(event);
    });
    if (activeRead < 0 || activeOverflow ||
        static_cast<std::size_t>(activeRead) != m_activeRecords ||
        events.size() != m_sealedRecords + m_activeRecords)
    {
        IncrementSaturating(m_segmentIntegrityRejects);
        return -1;
    }
    if (events.size() > static_cast<std::size_t>(std::numeric_limits<int>::max()))
        return -1;
    lock.unlock();
    if (onEvent)
        for (const OmsJournalEvent& event : events) onEvent(event);
    return static_cast<int>(events.size());
}

OmsSegmentedJournalHealthSnapshot
OmsSegmentedJournal::GetHealthSnapshot() const
{
    std::lock_guard<std::mutex> lock(m_segmentMutex);
    OmsSegmentedJournalHealthSnapshot out;
    out.initialized = m_initialized;
    out.activeAvailable = static_cast<bool>(m_active);
    out.sealedBytes = m_sealedBytes;
    out.sealedRecords = m_sealedRecords;
    out.sealedSegments = m_segments.size();
    out.nextSequence = m_nextSequence;
    out.rotations = m_rotations;
    out.rotationCapacityRejects = m_rotationCapacityRejects;
    out.totalCapacityRejects = m_totalCapacityRejects;
    out.segmentIntegrityRejects = m_segmentIntegrityRejects;
    out.replayBusyRejects = m_replayBusyRejects;
    out.limits = m_limits;
    if (m_active)
    {
        out.active = m_active->GetHealthSnapshot();
        std::size_t observedOnDisk = 0, observedRetained = 0;
        if (ObserveActiveLocked(observedOnDisk, observedRetained))
        {
            out.activeOnDiskBytes = observedOnDisk;
            out.activeRetainedBytes = observedRetained;
        }
        out.activeRecords = m_activeRecords;
        if (out.activeOnDiskBytes <= std::numeric_limits<std::size_t>::max() -
            out.activeRetainedBytes)
        {
            const std::size_t active = out.activeOnDiskBytes + out.activeRetainedBytes;
            if (m_sealedBytes <= std::numeric_limits<std::size_t>::max() - active)
                out.logicalTotalBytes = m_sealedBytes + active;
        }
    }
    out.logicalTotalRecords = m_sealedRecords + m_activeRecords;
    return out;
}

std::vector<OmsJournalSegmentDescriptor>
OmsSegmentedJournal::GetSealedSegments() const
{
    std::lock_guard<std::mutex> lock(m_segmentMutex);
    return m_segments;
}
