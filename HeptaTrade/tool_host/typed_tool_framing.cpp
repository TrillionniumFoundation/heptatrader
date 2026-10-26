#include "typed_tool_protocol.h"

#include <arpa/inet.h>
#include <cerrno>
#include <chrono>
#include <cstdint>
#include <cstring>
#include <fcntl.h>
#include <limits>
#include <poll.h>
#include <sys/socket.h>
#include <sys/un.h>
#include <unistd.h>

namespace {

using Deadline = std::chrono::steady_clock::time_point;

bool WaitFd(int fd, short events, const Deadline& deadline)
{
    struct pollfd descriptor;
    descriptor.fd = fd;
    descriptor.events = events;
    descriptor.revents = 0;
    while (true)
    {
        const auto now = std::chrono::steady_clock::now();
        if (now >= deadline) return false;
        const auto remaining = std::chrono::duration_cast<std::chrono::milliseconds>(deadline - now);
        const int timeoutMs = remaining.count() >= std::numeric_limits<int>::max() ?
            std::numeric_limits<int>::max() : static_cast<int>(remaining.count()) + 1;
        const int result = ::poll(&descriptor, 1, timeoutMs);
        if (result < 0 && errno == EINTR) continue;
        return result > 0 && (descriptor.revents & events) != 0;
    }
}

bool ReadAll(int fd, char* data, std::size_t size, const Deadline& deadline)
{
    std::size_t offset = 0;
    while (offset < size)
    {
        if (!WaitFd(fd, POLLIN, deadline)) return false;
        const ssize_t count = ::recv(fd, data + offset, size - offset, MSG_DONTWAIT);
        if (count < 0 && (errno == EINTR || errno == EAGAIN || errno == EWOULDBLOCK)) continue;
        if (count <= 0) return false;
        offset += static_cast<std::size_t>(count);
    }
    return true;
}

bool WriteAll(int fd, const char* data, std::size_t size, const Deadline& deadline)
{
    std::size_t offset = 0;
    while (offset < size)
    {
        if (!WaitFd(fd, POLLOUT, deadline)) return false;
        const ssize_t count =
            ::send(fd, data + offset, size - offset, MSG_NOSIGNAL | MSG_DONTWAIT);
        if (count < 0 && (errno == EINTR || errno == EAGAIN || errno == EWOULDBLOCK)) continue;
        if (count <= 0) return false;
        offset += static_cast<std::size_t>(count);
    }
    return true;
}

} // namespace

bool TypedToolProtocol::ConnectUnixSocket(
    int fd, const std::string& socketPath, int timeoutMs)
{
    if (timeoutMs <= 0 || socketPath.empty() ||
        socketPath.size() >= sizeof(sockaddr_un::sun_path)) return false;
    const int flags = ::fcntl(fd, F_GETFL);
    if (flags < 0 || ::fcntl(fd, F_SETFL, flags | O_NONBLOCK) != 0) return false;
    sockaddr_un address;
    std::memset(&address, 0, sizeof(address));
    address.sun_family = AF_UNIX;
    std::memcpy(address.sun_path, socketPath.c_str(), socketPath.size() + 1);
    const Deadline deadline = std::chrono::steady_clock::now() +
        std::chrono::milliseconds(timeoutMs);
    bool connected = ::connect(fd, reinterpret_cast<sockaddr*>(&address), sizeof(address)) == 0;
    if (!connected && errno == EINPROGRESS && WaitFd(fd, POLLOUT, deadline))
    {
        int error = 0;
        socklen_t length = sizeof(error);
        connected = ::getsockopt(fd, SOL_SOCKET, SO_ERROR, &error, &length) == 0 &&
            length == sizeof(error) && error == 0;
    }
    // AF_UNIX reports a full accept queue as EAGAIN: no connection is queued.
    // Refuse that unsent call, rather than retrying or claiming connect success.
    const bool restored = ::fcntl(fd, F_SETFL, flags) == 0;
    return connected && restored;
}

bool TypedToolProtocol::ReadFrame(int fd,
                                  std::size_t maxBodyBytes,
                                  int timeoutMs,
                                  std::string& body,
                                  std::string& reason)
{
    const Deadline deadline = std::chrono::steady_clock::now() +
        std::chrono::milliseconds(timeoutMs);
    std::uint32_t networkLength = 0;
    if (!ReadAll(
            fd, reinterpret_cast<char*>(&networkLength),
            sizeof(networkLength), deadline))
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
    if (!ReadAll(fd, &body[0], length, deadline))
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
    const Deadline deadline = std::chrono::steady_clock::now() +
        std::chrono::milliseconds(timeoutMs);
    if (!WriteAll(
            fd, reinterpret_cast<const char*>(&networkLength),
            sizeof(networkLength), deadline) ||
        !WriteAll(fd, body.data(), body.size(), deadline))
    {
        reason = "FRAME_WRITE_TIMEOUT";
        return false;
    }
    reason.clear();
    return true;
}
