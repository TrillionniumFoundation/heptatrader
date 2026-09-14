#pragma once

#include <cstdint>

// Trusted journal observations only; never populate this view from an Agent.
// This early new-entry guard is not a disk quota or a reservation for all
// possible future callbacks. Exit evidence must still be written at capacity.
struct NewEntryCapacity {
    NewEntryCapacity(bool isKnown = false, bool poisoned = false,
        std::uint64_t byteLimit = 0, std::uint64_t recordLimit = 0,
        std::uint64_t bytes = 0, std::uint64_t records = 0,
        std::uint64_t pending = 0, std::uint64_t queued = 0,
        std::uint64_t buffered = 0)
        : known(isKnown), writePoisoned(poisoned), maxBytes(byteLimit),
          maxRecords(recordLimit), writtenBytes(bytes), writtenRecords(records),
          pendingBytes(pending), queuedRecords(queued), bufferedRecords(buffered) {}
    bool known = false;
    bool writePoisoned = false;
    std::uint64_t maxBytes = 0;
    std::uint64_t maxRecords = 0;
    std::uint64_t writtenBytes = 0;
    std::uint64_t writtenRecords = 0;
    std::uint64_t pendingBytes = 0;
    std::uint64_t queuedRecords = 0;
    std::uint64_t bufferedRecords = 0;
};

inline const char* NewEntryCapacityReason(const NewEntryCapacity& capacity)
{
    if (!capacity.known || capacity.writePoisoned ||
        capacity.maxBytes == 0 || capacity.maxRecords == 0)
        return "OMS_NEW_ENTRY_CAPACITY_UNKNOWN";
    // Match the existing capacity warning boundary, rounded up. Compare by
    // subtraction: hostile/overflow-sized observations must never wrap to OK.
    const auto atWarning = [](std::uint64_t limit, std::uint64_t written,
                              std::uint64_t pending, std::uint64_t buffered) {
        std::uint64_t remaining = limit - limit / 5U;
        if (written >= remaining) return true;
        remaining -= written;
        if (pending >= remaining) return true;
        remaining -= pending;
        return buffered >= remaining;
    };
    if (atWarning(capacity.maxBytes, capacity.writtenBytes,
                  capacity.pendingBytes, 0) ||
        atWarning(capacity.maxRecords, capacity.writtenRecords,
                  capacity.queuedRecords, capacity.bufferedRecords))
        return "OMS_NEW_ENTRY_CAPACITY_EXHAUSTED";
    return nullptr;
}
