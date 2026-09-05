// Intentionally compiled as one translation unit so the segmented profile
// reuses the existing typed journal codec and durability helpers without
// duplicating private parsing or synchronization logic.
#include "oms_journal.h"
#define GetHealthSnapshot GetHealthSnapshotCore
#include "oms_journal_core.hpp"
#undef GetHealthSnapshot

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
    out.retainedBytes = m_retainedBytes;
    out.queueCapacityRejects = m_queueCapacityRejects;
    out.replayCapacityRejects = m_replayCapacityRejects;
    out.replayBusyRejects = m_replayBusyRejects;
    out.workerStoppedOnFailure = m_workerStoppedOnFailure;
    struct stat onDiskMetadata;
    if (m_fd >= 0 && StatFileDescriptor(m_fd, onDiskMetadata) &&
        onDiskMetadata.st_size >= 0)
    {
        out.onDiskBytes = static_cast<std::size_t>(onDiskMetadata.st_size);
        out.onDiskBytesValid = true;
    }
    out.limits = m_limits;
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

#include "oms_segmented_journal.h"

#include <array>
#include <cstdio>
#include <cstring>
#include <dirent.h>
#include <iomanip>
#include <map>
#include <sys/file.h>
#include <sys/syscall.h>

#include "oms_segmented_journal_impl_01.hpp"
#include "oms_segmented_journal_impl_02.hpp"
#include "oms_segmented_journal_impl_03.hpp"
#include "oms_segmented_journal_impl_04.hpp"
#include "oms_segmented_journal_impl_05.hpp"
