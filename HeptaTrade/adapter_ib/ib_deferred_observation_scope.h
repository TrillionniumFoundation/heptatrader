#pragma once

#include <array>
#include <cstddef>
#include <string>

// Diagnostic output only: immutable lines are collected on the calling stack
// and synchronously flushed by the outermost scope after trading locks leave
// scope. Nested adapter calls reuse the runtime's scope. No OMS evidence,
// broker result, background writer, retry or runtime authority is created.
class IbDeferredObservationScope
{
public:
    IbDeferredObservationScope() noexcept;
    ~IbDeferredObservationScope() noexcept;
    IbDeferredObservationScope(const IbDeferredObservationScope&) = delete;
    IbDeferredObservationScope& operator=(const IbDeferredObservationScope&) = delete;

    // Returns false only when no scope owns this thread. Overflow/allocation
    // failures are counted and reported after unlock, never flushed in-place.
    static bool DeferIfScoped(const std::string& line, const std::string& path) noexcept;

private:
    struct Record { std::string line; std::string path; };
    static const std::size_t kMaxRecords = 16;
    static const std::size_t kMaxBytes = 65536;
    std::array<Record, kMaxRecords> m_records;
    std::size_t m_size = 0;
    std::size_t m_bytes = 0;
    std::size_t m_dropped = 0;
    bool m_allocationFailed = false;
    bool m_owner;
};
