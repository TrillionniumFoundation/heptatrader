#include "unix_session_supervisor_client.h"

#include <arpa/inet.h>
#include <cerrno>
#include <cstring>
#include <limits>
#include <poll.h>
#include <sys/socket.h>
#include <sys/un.h>
#include <unistd.h>

namespace
{
bool WaitFd(int descriptor, short events, int timeoutMs)
{
    pollfd state;
    state.fd = descriptor;
    state.events = events;
    state.revents = 0;
    int result = 0;
    do { result = ::poll(&state, 1, timeoutMs); }
    while (result < 0 && errno == EINTR);
    return result > 0 && (state.revents & events) != 0;
}

bool ReadAll(int descriptor, char* data, std::size_t size, int timeoutMs)
{
    std::size_t offset = 0;
    while (offset < size)
    {
        if (!WaitFd(descriptor, POLLIN, timeoutMs)) return false;
        const ssize_t count = ::read(descriptor, data + offset, size - offset);
        if (count < 0 && errno == EINTR) continue;
        if (count <= 0) return false;
        offset += static_cast<std::size_t>(count);
    }
    return true;
}

bool WriteAll(int descriptor, const char* data, std::size_t size, int timeoutMs)
{
    std::size_t offset = 0;
    while (offset < size)
    {
        if (!WaitFd(descriptor, POLLOUT, timeoutMs)) return false;
        const ssize_t count = ::send(
            descriptor, data + offset, size - offset, MSG_NOSIGNAL);
        if (count < 0 && errno == EINTR) continue;
        if (count <= 0) return false;
        offset += static_cast<std::size_t>(count);
    }
    return true;
}

bool WriteFrame(int descriptor, const std::string& body,
                int timeoutMs, std::string& reason)
{
    if (body.empty() || body.size() > std::numeric_limits<std::uint32_t>::max())
    {
        reason = "INVALID_SUPERVISOR_FRAME_SIZE";
        return false;
    }
    const std::uint32_t length = htonl(static_cast<std::uint32_t>(body.size()));
    if (!WriteAll(descriptor, reinterpret_cast<const char*>(&length), sizeof(length), timeoutMs) ||
        !WriteAll(descriptor, body.data(), body.size(), timeoutMs))
    {
        reason = "SUPERVISOR_FRAME_WRITE_TIMEOUT";
        return false;
    }
    return true;
}

bool ReadFrame(int descriptor, std::size_t maximum, int timeoutMs,
               std::string& body, std::string& reason)
{
    std::uint32_t encodedLength = 0;
    if (!ReadAll(descriptor, reinterpret_cast<char*>(&encodedLength),
                 sizeof(encodedLength), timeoutMs))
    {
        reason = "SUPERVISOR_FRAME_HEADER_TIMEOUT";
        return false;
    }
    const std::uint32_t length = ntohl(encodedLength);
    if (length == 0 || length > maximum)
    {
        reason = "SUPERVISOR_FRAME_LENGTH_REJECTED";
        return false;
    }
    body.resize(length);
    if (!ReadAll(descriptor, &body[0], body.size(), timeoutMs))
    {
        reason = "SUPERVISOR_FRAME_BODY_TIMEOUT";
        return false;
    }
    return true;
}
}

bool UnixSessionSupervisorClient::Call(
    const std::string& socketPath,
    const SessionSupervisorRequest& request,
    SessionSupervisorResult& result,
    std::string& reason,
    int timeoutMs,
    std::size_t maxResponseBytes)
{
    result = SessionSupervisorResult();
    if (socketPath.empty() || socketPath.size() >= sizeof(sockaddr_un::sun_path))
    {
        reason = "INVALID_SUPERVISOR_SOCKET_PATH";
        return false;
    }
    if (timeoutMs <= 0 || timeoutMs > 120000 ||
        maxResponseBytes < 128 || maxResponseBytes > 65536)
    {
        reason = "INVALID_SUPERVISOR_CLIENT_LIMIT";
        return false;
    }

    std::string body;
    if (!SessionSupervisorProtocol::EncodeRequest(request, body, reason)) return false;

    const int descriptor = ::socket(AF_UNIX, SOCK_STREAM | SOCK_CLOEXEC, 0);
    if (descriptor < 0)
    {
        reason = "SUPERVISOR_SOCKET_CREATE_FAILED";
        return false;
    }

    sockaddr_un address;
    std::memset(&address, 0, sizeof(address));
    address.sun_family = AF_UNIX;
    std::strncpy(address.sun_path, socketPath.c_str(), sizeof(address.sun_path) - 1);
    if (::connect(descriptor, reinterpret_cast<sockaddr*>(&address), sizeof(address)) != 0)
    {
        ::close(descriptor);
        reason = "SUPERVISOR_SOCKET_CONNECT_FAILED";
        return false;
    }

    std::string response;
    const bool exchanged = WriteFrame(descriptor, body, timeoutMs, reason) &&
        ReadFrame(descriptor, maxResponseBytes, timeoutMs, response, reason);
    ::close(descriptor);
    if (!exchanged) return false;
    return SessionSupervisorProtocol::DecodeResult(response, result, reason);
}
