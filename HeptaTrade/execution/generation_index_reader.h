#pragma once

#include <algorithm>
#include <array>
#include <cerrno>
#include <cstdint>
#include <limits>
#include <string>
#include <sys/types.h>
#include <unistd.h>


// Bounded random row locator used by immutable generation-index binary search.
// The look-behind includes one extra byte so a maximum-sized row that is not
// the first row can still see the preceding newline delimiter.
inline bool GenerationReadLineContainingBounded(
    int fd, off_t fileSize, off_t probe, std::size_t maximumLineBytes,
    off_t& start, off_t& end, std::string& line)
{
    line.clear();
    start = 0;
    end = 0;
    if (fd < 0 || fileSize <= 0 || probe < 0 || maximumLineBytes == 0 ||
        maximumLineBytes >= static_cast<std::size_t>(
            std::numeric_limits<off_t>::max()))
        return false;
    if (probe >= fileSize) probe = fileSize - 1;

    const off_t backwardLimit = static_cast<off_t>(maximumLineBytes) + 1;
    const off_t backward = std::min<off_t>(probe, backwardLimit);
    const off_t base = probe - backward;
    std::string before(static_cast<std::size_t>(backward), '\0');
    if (backward > 0)
    {
        ssize_t count;
        do
        {
            count = ::pread(fd, &before[0], before.size(), base);
        } while (count < 0 && errno == EINTR);
        if (count != static_cast<ssize_t>(before.size())) return false;
    }
    const std::size_t newline = before.rfind('\n');
    if (newline == std::string::npos)
    {
        if (base != 0) return false;
        start = 0;
    }
    else
        start = base + static_cast<off_t>(newline + 1U);

    const off_t available = fileSize - start;
    if (available <= 0) return false;
    const off_t forwardLimit = static_cast<off_t>(maximumLineBytes) + 1;
    const std::size_t maximum = static_cast<std::size_t>(
        std::min<off_t>(forwardLimit, available));
    std::string forward(maximum, '\0');
    ssize_t count;
    do
    {
        count = ::pread(fd, &forward[0], forward.size(), start);
    } while (count < 0 && errno == EINTR);
    if (count <= 0) return false;
    forward.resize(static_cast<std::size_t>(count));
    const std::size_t found = forward.find('\n');
    if (found == std::string::npos || found > maximumLineBytes) return false;
    line.assign(forward.data(), found);
    end = start + static_cast<off_t>(found + 1U);
    return true;
}

// Exact-offset sequential reader for immutable generation indexes. Random
// binary-search probes use GenerationReadLineContaining in the owning
// translation unit; once a lower-bound/line boundary is known, this reader
// advances without rereading the preceding/following 64 KiB for every row.
class GenerationSequentialLineReader
{
public:
    GenerationSequentialLineReader(int fd, off_t fileSize, off_t start,
                                   std::size_t maximumLineBytes)
        : m_fd(fd), m_fileSize(fileSize), m_readOffset(start),
          m_logicalOffset(start), m_maximumLineBytes(maximumLineBytes)
    {
    }

    bool Read(off_t& start, off_t& end, std::string& line)
    {
        line.clear();
        start = m_logicalOffset;
        end = m_logicalOffset;
        if (m_fd < 0 || m_fileSize < 0 || m_logicalOffset < 0 ||
            m_logicalOffset >= m_fileSize || m_maximumLineBytes == 0)
            return false;

        for (;;)
        {
            const std::size_t newline = m_buffer.find('\n', m_cursor);
            if (newline != std::string::npos)
            {
                const std::size_t length = newline - m_cursor;
                if (length > m_maximumLineBytes) return false;
                line.assign(m_buffer.data() + m_cursor, length);
                m_cursor = newline + 1U;
                m_logicalOffset += static_cast<off_t>(length + 1U);
                end = m_logicalOffset;
                if (m_cursor == m_buffer.size())
                {
                    m_buffer.clear();
                    m_cursor = 0;
                }
                return true;
            }

            if (m_buffer.size() - m_cursor > m_maximumLineBytes ||
                m_readOffset >= m_fileSize)
                return false;

            if (m_cursor != 0)
            {
                m_buffer.erase(0, m_cursor);
                m_cursor = 0;
            }

            const std::size_t wanted = static_cast<std::size_t>(
                std::min<off_t>(static_cast<off_t>(m_chunk.size()),
                                m_fileSize - m_readOffset));
            ssize_t count;
            do
            {
                count = ::pread(m_fd, m_chunk.data(), wanted, m_readOffset);
            } while (count < 0 && errno == EINTR);
            if (count <= 0) return false;
            m_buffer.append(m_chunk.data(), static_cast<std::size_t>(count));
            m_readOffset += count;
            ++m_readCalls;
            const std::uint64_t observed = static_cast<std::uint64_t>(count);
            const std::uint64_t maximum =
                std::numeric_limits<std::uint64_t>::max();
            if (m_readBytes > maximum - observed) m_readBytes = maximum;
            else m_readBytes += observed;
        }
    }

    std::uint64_t ReadCalls() const noexcept { return m_readCalls; }
    std::uint64_t ReadBytes() const noexcept { return m_readBytes; }

private:
    int m_fd;
    off_t m_fileSize;
    off_t m_readOffset;
    off_t m_logicalOffset;
    std::size_t m_maximumLineBytes;
    std::array<char, 64U * 1024U> m_chunk{};
    std::string m_buffer;
    std::size_t m_cursor = 0;
    std::uint64_t m_readCalls = 0;
    std::uint64_t m_readBytes = 0;
};
