#include "unix_tool_client.h"

#include "typed_tool_protocol.h"

#include <cstring>
#include <sys/socket.h>
#include <sys/un.h>
#include <unistd.h>

bool UnixToolClient::Call(const std::string& socketPath,
                          const TradingToolHostRequest& request,
                          std::string& responseJson,
                          std::string& reason,
                          int timeoutMs,
                          std::size_t maxResponseBytes)
{
    if (socketPath.empty() || socketPath.size() >= sizeof(sockaddr_un::sun_path))
    {
        reason = "INVALID_SOCKET_PATH";
        return false;
    }

    std::string body;
    if (!TypedToolProtocol::EncodeRequest(request, body, reason)) return false;

    const int fd = ::socket(AF_UNIX, SOCK_STREAM | SOCK_CLOEXEC, 0);
    if (fd < 0)
    {
        reason = "SOCKET_CREATE_FAILED";
        return false;
    }

    sockaddr_un address;
    std::memset(&address, 0, sizeof(address));
    address.sun_family = AF_UNIX;
    std::strncpy(address.sun_path, socketPath.c_str(), sizeof(address.sun_path) - 1);
    if (::connect(fd, reinterpret_cast<sockaddr*>(&address), sizeof(address)) != 0)
    {
        ::close(fd);
        reason = "SOCKET_CONNECT_FAILED";
        return false;
    }

    const bool ok = TypedToolProtocol::WriteFrame(fd, body, timeoutMs, reason) &&
                    TypedToolProtocol::ReadFrame(fd, maxResponseBytes, timeoutMs, responseJson, reason);
    ::close(fd);
    return ok;
}
