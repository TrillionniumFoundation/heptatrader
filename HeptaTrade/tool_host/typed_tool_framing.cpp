#include "typed_tool_protocol.h"

#include <arpa/inet.h>
#include <cerrno>
#include <cstdint>
#include <limits>
#include <poll.h>
#include <sys/socket.h>
#include <unistd.h>

namespace {

bool WaitFd(int fd, short events, int timeoutMs)
{
    struct pollfd descriptor;
    descriptor.fd = fd;
    descriptor.events = events;
    descriptor.revents = 0;
    int result;
    do
    {
        result = ::poll(&descriptor, 1, timeoutMs);
    } while (result < 0 && errno == EINTR);
    return result > 0 && (descriptor.revents & events) != 0;
}

bool ReadAll(int fd, char* data, std::size_t size, int timeoutMs)
{
    std::size_t offset = 0;
    while (offset < size)
    {
        if (!WaitFd(fd, POLLIN, timeoutMs)) return false;
        const ssize_t count = ::read(fd, data + offset, size - offset);
        if (count <= 0) return false;
        offset += static_cast<std::size_t>(count);
    }
    return true;
}

bool WriteAll(int fd, const char* data, std::size_t size, int timeoutMs)
{
    std::size_t offset = 0;
    while (offset < size)
    {
        if (!WaitFd(fd, POLLOUT, timeoutMs)) return false;
        const ssize_t count =
            ::send(fd, data + offset, size - offset, MSG_NOSIGNAL);
        if (count < 0 && errno == EINTR) continue;
        if (count <= 0) return false;
        offset += static_cast<std::size_t>(count);
    }
    return true;
}

} // namespace

bool TypedToolProtocol::ReadFrame(int fd,
                                  std::size_t maxBodyBytes,
                                  int timeoutMs,
                                  std::string& body,
                                  std::string& reason)
{
    std::uint32_t networkLength = 0;
    if (!ReadAll(
            fd, reinterpret_cast<char*>(&networkLength),
            sizeof(networkLength), timeoutMs))
    {
        reason = "FRAME_HEADER_TIMEOUT";
        return false;
    }
    const std::uint32_t length = ntohl(networkLength);
    if (length == 0 || length > maxBodyBytes)
    {
        reason = "FRAME_LENGTH_REJECTED";
        return false;
    }
    body.resize(length);
    if (!ReadAll(fd, &body[0], length, timeoutMs))
    {
        reason = "FRAME_BODY_TIMEOUT";
        return false;
    }
    reason.clear();
    return true;
}

bool TypedToolProtocol::WriteFrame(int fd,
                                   const std::string& body,
                                   int timeoutMs,
                                   std::string& reason)
{
    if (body.empty() ||
        body.size() > std::numeric_limits<std::uint32_t>::max())
    {
        reason = "INVALID_FRAME_SIZE";
        return false;
    }
    const std::uint32_t networkLength =
        htonl(static_cast<std::uint32_t>(body.size()));
    if (!WriteAll(
            fd, reinterpret_cast<const char*>(&networkLength),
            sizeof(networkLength), timeoutMs) ||
        !WriteAll(fd, body.data(), body.size(), timeoutMs))
    {
        reason = "FRAME_WRITE_TIMEOUT";
        return false;
    }
    reason.clear();
    return true;
}
