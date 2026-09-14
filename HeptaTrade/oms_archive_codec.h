#pragma once

#include <algorithm>
#include <cerrno>
#include <cstdint>
#include <functional>
#include <limits>
#include <string>
#include <unistd.h>
#include <zlib.h>

// Complete gzip members only; unlike gzread, trailing garbage is an error.
// Callback receives bounded decoded chunks, NOT execution/recovery events.
// The caller must validate the complete snapshot before projecting any event.
namespace hepta_oms_archive {
inline ssize_t ReadAt(int fd, char* out, std::size_t length, off_t offset)
{
    ssize_t count;
    do { count = ::pread(fd, out, length, offset); } while (count < 0 && errno == EINTR);
    return count;
}

inline bool IsGzip(int fd, off_t size)
{
    char head[2];
    return size >= 2 && ReadAt(fd, head, 2, 0) == 2 &&
        static_cast<unsigned char>(head[0]) == 0x1f &&
        static_cast<unsigned char>(head[1]) == 0x8b;
}

inline bool EncodeMember(const std::string& plain, std::string& encoded)
{
    if (plain.size() > std::numeric_limits<uInt>::max()) return false;
    z_stream z = {};
    if (deflateInit2(&z, Z_DEFAULT_COMPRESSION, Z_DEFLATED, 31, 8, Z_DEFAULT_STRATEGY) != Z_OK)
        return false;
    struct End { z_stream& z; ~End() { deflateEnd(&z); } } end{z};
    encoded.resize(deflateBound(&z, static_cast<uLong>(plain.size())));
    z.next_in = reinterpret_cast<Bytef*>(const_cast<char*>(plain.data()));
    z.avail_in = static_cast<uInt>(plain.size());
    z.next_out = reinterpret_cast<Bytef*>(&encoded[0]);
    z.avail_out = static_cast<uInt>(encoded.size());
    if (deflate(&z, Z_FINISH) != Z_STREAM_END || z.avail_in) return false;
    encoded.resize(z.total_out);
    return true;
}

inline bool ReadSnapshot(int fd, off_t size, bool gzip, std::size_t maxBytes,
    const std::function<bool(const char*, std::size_t)>& consume,
    std::size_t& logicalBytes, std::string& reason)
{
    logicalBytes = 0;
    const std::uint64_t physicalLimit = gzip ?
        2ULL * maxBytes + 1048576ULL : static_cast<std::uint64_t>(maxBytes);
    if (size < 0 || static_cast<std::uint64_t>(size) > physicalLimit)
    {
        reason = gzip ? "OMS_REPLAY_STORAGE_LIMIT" : "OMS_REPLAY_BYTE_LIMIT";
        return false;
    }
    const auto deliver = [&](const char* data, std::size_t length) {
        if (length > maxBytes - logicalBytes)
        { reason = "OMS_REPLAY_BYTE_LIMIT"; return false; }
        logicalBytes += length;
        return length == 0 || consume(data, length);
    };
    char input[8192], output[8192];
    off_t offset = 0;
    if (!gzip)
    {
        while (offset < size)
        {
            const ssize_t n = ReadAt(fd, input, static_cast<std::size_t>(std::min<off_t>(sizeof(input), size-offset)), offset);
            if (n <= 0) { reason = "OMS_REPLAY_IO_OR_IDENTITY_FAILURE"; return false; }
            offset += n;
            if (!deliver(input, static_cast<std::size_t>(n))) return false;
        }
        return true;
    }
    z_stream z = {};
    if (inflateInit2(&z, 31) != Z_OK)
    { reason = "OMS_REPLAY_ALLOCATION_FAILURE"; return false; }
    struct End { z_stream& z; ~End() { inflateEnd(&z); } } end{z};
    bool memberEnded = false;
    std::size_t members = 0;
    for (;;)
    {
        if (!z.avail_in && offset < size)
        {
            const ssize_t n = ReadAt(fd, input, static_cast<std::size_t>(std::min<off_t>(sizeof(input), size-offset)), offset);
            if (n <= 0) { reason = "OMS_REPLAY_IO_OR_IDENTITY_FAILURE"; return false; }
            offset += n;
            z.next_in = reinterpret_cast<Bytef*>(input);
            z.avail_in = static_cast<uInt>(n);
        }
        if (memberEnded)
        {
            if (!z.avail_in && offset == size) return true;
            Bytef* next = z.next_in;
            const uInt available = z.avail_in;
            if (inflateReset2(&z, 31) != Z_OK)
            { reason = "OMS_REPLAY_ARCHIVE_INVALID"; return false; }
            z.next_in = next; z.avail_in = available;
            memberEnded = false;
        }
        z.next_out = reinterpret_cast<Bytef*>(output);
        z.avail_out = sizeof(output);
        const uInt priorIn = z.avail_in;
        const int result = inflate(&z, Z_NO_FLUSH);
        const std::size_t produced = sizeof(output) - z.avail_out;
        if (!deliver(output, produced)) return false;
        if (result == Z_STREAM_END)
        {
            if (++members > 1000000U)
            { reason = "OMS_REPLAY_ARCHIVE_MEMBER_LIMIT"; return false; }
            memberEnded = true;
        }
        else if (result != Z_OK || (z.avail_in == priorIn && produced == 0))
        {
            reason = result == Z_MEM_ERROR ? "OMS_REPLAY_ALLOCATION_FAILURE" : "OMS_REPLAY_ARCHIVE_INVALID";
            return false;
        }
    }
}
} // namespace hepta_oms_archive
