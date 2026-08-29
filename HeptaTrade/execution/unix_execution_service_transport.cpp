#include "unix_execution_service_internal.h"

#include <arpa/inet.h>
#include <cerrno>
#include <chrono>
#include <cstring>
#include <poll.h>
#include <sys/socket.h>
#include <sys/un.h>
#include <unistd.h>

namespace HeptaExecutionServiceInternal
{
namespace
{
int RemainingTimeoutMs(const IoDeadline& deadline)
{
    const std::chrono::steady_clock::time_point now =
        std::chrono::steady_clock::now();
    if (now >= deadline) return 0;
    const std::chrono::milliseconds remaining =
        std::chrono::duration_cast<std::chrono::milliseconds>(deadline - now);
    return static_cast<int>(remaining.count() > 0 ? remaining.count() : 1);
}

bool ReadAll(int fd, char* data, std::size_t size, const IoDeadline& deadline)
{
    std::size_t offset = 0;
    while (offset < size)
    {
        if (!WaitFd(fd, POLLIN, deadline)) return false;
        const ssize_t count = ::read(fd, data + offset, size - offset);
        if (count < 0 && errno == EINTR) continue;
        if (count < 0 && (errno == EAGAIN || errno == EWOULDBLOCK)) continue;
        if (count <= 0) return false;
        offset += static_cast<std::size_t>(count);
    }
    return true;
}

bool WriteAll(int fd,
              const char* data,
              std::size_t size,
              const IoDeadline& deadline)
{
    std::size_t offset = 0;
    while (offset < size)
    {
        if (!WaitFd(fd, POLLOUT, deadline)) return false;
        const ssize_t count =
            ::send(fd, data + offset, size - offset, MSG_NOSIGNAL);
        if (count < 0 && errno == EINTR) continue;
        if (count < 0 && (errno == EAGAIN || errno == EWOULDBLOCK)) continue;
        if (count <= 0) return false;
        offset += static_cast<std::size_t>(count);
    }
    return true;
}
} // namespace

bool ValidIdentity(const ExecutionServiceIdentity& identity)
{
    return !identity.serviceEpoch.empty() &&
        identity.serviceEpoch.size() <= 128 &&
        identity.serviceFencingGeneration != 0;
}

bool SameIdentity(const ExecutionServiceIdentity& left,
                  const ExecutionServiceIdentity& right)
{
    return left.serviceEpoch == right.serviceEpoch &&
        left.serviceFencingGeneration == right.serviceFencingGeneration;
}

IoDeadline DeadlineAfter(int timeoutMs)
{
    return std::chrono::steady_clock::now() +
        std::chrono::milliseconds(timeoutMs);
}

bool WaitFd(int fd, short events, const IoDeadline& deadline)
{
    struct pollfd pfd;
    pfd.fd = fd;
    pfd.events = events;
    pfd.revents = 0;
    int rc = -1;
    do
    {
        const int timeoutMs = RemainingTimeoutMs(deadline);
        if (timeoutMs <= 0) return false;
        rc = ::poll(&pfd, 1, timeoutMs);
    }
    while (rc < 0 && errno == EINTR);
    return rc > 0 && (pfd.revents & events) != 0;
}

bool ReadFrame(int fd,
               std::size_t maxBytes,
               const IoDeadline& deadline,
               std::string& body)
{
    std::uint32_t networkLength = 0;
    if (!ReadAll(
            fd,
            reinterpret_cast<char*>(&networkLength),
            sizeof(networkLength),
            deadline))
        return false;
    const std::size_t length = ntohl(networkLength);
    if (length == 0 || length > maxBytes) return false;
    body.assign(length, '\0');
    return ReadAll(fd, &body[0], body.size(), deadline);
}

bool WriteFrame(int fd, const std::string& body, const IoDeadline& deadline)
{
    if (body.empty() || body.size() > 0xffffffffu) return false;
    const std::uint32_t networkLength =
        htonl(static_cast<std::uint32_t>(body.size()));
    return WriteAll(
               fd,
               reinterpret_cast<const char*>(&networkLength),
               sizeof(networkLength),
               deadline) &&
        WriteAll(fd, body.data(), body.size(), deadline);
}

ExecutionCommandResult TransportFailure(const std::string& commandId,
                                        const std::string& detail)
{
    ExecutionCommandResult result;
    result.status = ExecutionCommandStatus::Uncertain;
    result.commandId = commandId;
    result.reasonCode = "EXECUTION_SERVICE_UNAVAILABLE";
    result.detail = detail;
    return result;
}

ExecutionControlResult ControlTransportFailure(const std::string& commandId,
                                               const std::string& detail)
{
    ExecutionControlResult result;
    result.status = ExecutionCommandStatus::Uncertain;
    result.commandId = commandId;
    result.reasonCode = "EXECUTION_SERVICE_UNAVAILABLE";
    result.detail = detail;
    return result;
}

bool BuildAddress(const std::string& socketPath,
                  struct sockaddr_un& address,
                  std::string& reason)
{
    if (socketPath.empty() || socketPath.size() >= sizeof(address.sun_path))
    {
        reason = "EXECUTION_SOCKET_PATH_INVALID";
        return false;
    }
    std::memset(&address, 0, sizeof(address));
    address.sun_family = AF_UNIX;
    std::memcpy(address.sun_path, socketPath.c_str(), socketPath.size() + 1);
    return true;
}
} // namespace HeptaExecutionServiceInternal
