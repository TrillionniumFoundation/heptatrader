#pragma once

#include "oms_journal.h"

#include <atomic>
#include <cstdint>
#include <functional>
#include <memory>
#include <mutex>
#include <string>
#include <vector>

// A sealed segment has a deterministic sequence and a SHA-256 content identity.
// The digest detects byte replacement; it does not authenticate who produced it.
struct OmsJournalSegmentDescriptor
{
    std::uint64_t sequence = 0;
    std::size_t bytes = 0;
    std::size_t records = 0;
    std::string digest;
    std::string filename;
};

// One segmented object bounds active/retained journal bytes. It never prunes
// authoritative history automatically: exhaustion fails closed until a later,
// separately qualified checkpoint/retention mechanism exists.
struct OmsSegmentedJournalLimits
{
    static constexpr std::size_t kActiveBytesCeiling = 64 * 1024 * 1024;
    static constexpr std::size_t kTotalBytesCeiling = 256 * 1024 * 1024;
    static constexpr std::size_t kSealedSegmentsCeiling = 255;
    static constexpr std::size_t kReplayRecordsCeiling =
        OmsJournalLimits::kReplayRecordsCeiling;

    std::size_t maximumQueuedRecords = OmsJournalLimits::kQueuedRecordsCeiling;
    std::size_t maximumQueuedBytes = 16 * 1024 * 1024;
    std::size_t maximumActiveBytes = 16 * 1024 * 1024;
    std::size_t maximumTotalBytes = kTotalBytesCeiling;
    std::size_t maximumSealedSegments = kSealedSegmentsCeiling;
    std::size_t maximumTotalRecords = kReplayRecordsCeiling;
};

struct OmsSegmentedJournalHealthSnapshot
{
    bool initialized = false;
    bool activeAvailable = false;
    std::size_t activeOnDiskBytes = 0;
    std::size_t activeRetainedBytes = 0;
    std::size_t activeRecords = 0;
    std::size_t sealedBytes = 0;
    std::size_t sealedRecords = 0;
    std::size_t sealedSegments = 0;
    std::size_t logicalTotalBytes = 0;
    std::size_t logicalTotalRecords = 0;
    std::uint64_t nextSequence = 1;
    std::uint64_t rotations = 0;
    std::uint64_t rotationCapacityRejects = 0;
    std::uint64_t totalCapacityRejects = 0;
    std::uint64_t segmentIntegrityRejects = 0;
    std::uint64_t replayBusyRejects = 0;
    OmsSegmentedJournalLimits limits;
    OmsJournalHealthSnapshot active;
};

// Linux local-filesystem segmented wrapper around OmsJournal. The supplied
// directory must already exist, be owned by the effective UID and mode 0700.
// A retained nonblocking flock prevents a second cooperating writer.
class OmsSegmentedJournal
{
public:
    OmsSegmentedJournal() = default;
    explicit OmsSegmentedJournal(const OmsSegmentedJournalLimits& limits)
        : m_limits(limits) {}
    ~OmsSegmentedJournal() noexcept;

    OmsSegmentedJournal(const OmsSegmentedJournal&) = delete;
    OmsSegmentedJournal& operator=(const OmsSegmentedJournal&) = delete;

    bool Init(const std::string& directory, const std::string& baseName);
    bool Append(const OmsJournalEvent& event);
    bool Rotate();
    int Replay(const std::function<void(const OmsJournalEvent&)>& onEvent) const;
    OmsSegmentedJournalHealthSnapshot GetHealthSnapshot() const;
    std::vector<OmsJournalSegmentDescriptor> GetSealedSegments() const;

private:
    bool OpenActiveLocked();
    bool RotateLocked();
    bool ScanSegmentsLocked();
    bool ValidateDirectoryLocked() const;
    bool ObserveActiveLocked(std::size_t& onDiskBytes,
                             std::size_t& retainedBytes) const;
    bool ReadSegmentLocked(
        const OmsJournalSegmentDescriptor& segment,
        std::vector<OmsJournalEvent>* events,
        std::size_t& recordCount) const;
    std::string DescriptorPathLocked(const std::string& filename) const;

private:
    const OmsSegmentedJournalLimits m_limits{};
    mutable std::mutex m_segmentMutex;
    mutable std::atomic_flag m_segmentReplayInProgress = ATOMIC_FLAG_INIT;
    std::string m_directory;
    std::string m_baseName;
    std::string m_activeName;
    std::string m_lockName;
    std::string m_segmentPrefix;
    int m_directoryFd = -1;
    int m_lockFd = -1;
    std::uintmax_t m_directoryDevice = 0;
    std::uintmax_t m_directoryInode = 0;
    bool m_initialized = false;
    std::unique_ptr<OmsJournal> m_active;
    std::vector<OmsJournalSegmentDescriptor> m_segments;
    std::size_t m_sealedBytes = 0;
    std::size_t m_sealedRecords = 0;
    std::size_t m_activeRecords = 0;
    std::uint64_t m_nextSequence = 1;
    std::uint64_t m_rotations = 0;
    std::uint64_t m_rotationCapacityRejects = 0;
    std::uint64_t m_totalCapacityRejects = 0;
    mutable std::uint64_t m_segmentIntegrityRejects = 0;
    mutable std::uint64_t m_replayBusyRejects = 0;
};
