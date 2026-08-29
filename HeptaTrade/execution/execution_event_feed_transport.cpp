#include "execution_event_feed_transport.h"

#include <arpa/inet.h>
#include <cerrno>
#include <limits>
#include <poll.h>
#include <sys/socket.h>

namespace HeptaExecutionEventFeedTransport
{
namespace
{
bool ReadAll(int fd, char* data, std::size_t size, const Deadline& deadline)
{
    std::size_t offset = 0;
    while (offset < size)
    {
        const ssize_t count = ::recv(fd, data + offset, size - offset, 0);
        if (count > 0)
        {
            offset += static_cast<std::size_t>(count);
            continue;
        }
        if (count < 0 && errno == EINTR) continue;
        if (count < 0 && (errno == EAGAIN || errno == EWOULDBLOCK))
        {
            if (!WaitFd(fd, POLLIN, deadline)) return false;
            continue;
        }
        return false;
    }
    return true;
}

bool WriteAll(int fd,
              const char* data,
              std::size_t size,
              const Deadline& deadline)
{
    std::size_t offset = 0;
    while (offset < size)
    {
        const ssize_t count =
            ::send(fd, data + offset, size - offset, MSG_NOSIGNAL);
        if (count > 0)
        {
            offset += static_cast<std::size_t>(count);
            continue;
        }
        if (count < 0 && errno == EINTR) continue;
        if (count < 0 && (errno == EAGAIN || errno == EWOULDBLOCK))
        {
            if (!WaitFd(fd, POLLOUT, deadline)) return false;
            continue;
        }
        return false;
    }
    return true;
}
} // namespace

bool ValidIdentity(const ExecutionServiceIdentity& identity)
{
    return !identity.serviceEpoch.empty() && identity.serviceEpoch.size() <= 128 &&
        identity.serviceFencingGeneration != 0;
}

bool SameIdentity(const ExecutionServiceIdentity& left,
                  const ExecutionServiceIdentity& right)
{
    return left.serviceEpoch == right.serviceEpoch &&
        left.serviceFencingGeneration == right.serviceFencingGeneration;
}

int RemainingMs(const Deadline& deadline)
{
    const std::chrono::milliseconds remaining =
        std::chrono::duration_cast<std::chrono::milliseconds>(
            deadline - std::chrono::steady_clock::now());
    if (remaining.count() <= 0) return 0;
    return remaining.count() > std::numeric_limits<int>::max() ?
        std::numeric_limits<int>::max() : static_cast<int>(remaining.count());
}

bool WaitFd(int fd, short events, const Deadline& deadline)
{
    while (true)
    {
        const int remaining = RemainingMs(deadline);
        if (remaining <= 0) return false;
        struct pollfd descriptor;
        descriptor.fd = fd;
        descriptor.events = events;
        descriptor.revents = 0;
        const int rc = ::poll(&descriptor, 1, remaining);
        if (rc < 0 && errno == EINTR) continue;
        return rc > 0 && (descriptor.revents & events) != 0;
    }
}

bool ReadFrame(int fd,
               std::size_t maxBytes,
               const Deadline& deadline,
               std::string& body)
{
    std::uint32_t networkLength = 0;
    if (!ReadAll(fd, reinterpret_cast<char*>(&networkLength),
            sizeof(networkLength), deadline))
        return false;
    const std::size_t length = ntohl(networkLength);
    if (length == 0 || length > maxBytes) return false;
    body.assign(length, '\0');
    return ReadAll(fd, &body[0], body.size(), deadline);
}

bool WriteFrame(int fd,
                const std::string& body,
                const Deadline& deadline)
{
    if (body.empty() || body.size() > 0xffffffffu) return false;
    const std::uint32_t length =
        htonl(static_cast<std::uint32_t>(body.size()));
    return WriteAll(fd, reinterpret_cast<const char*>(&length),
               sizeof(length), deadline) &&
        WriteAll(fd, body.data(), body.size(), deadline);
}
} // namespace HeptaExecutionEventFeedTransport
