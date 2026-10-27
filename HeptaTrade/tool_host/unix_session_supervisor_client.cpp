#include "unix_session_supervisor_client.h"
#include "typed_tool_protocol.h"

#include <sys/socket.h>
#include <sys/un.h>
#include <unistd.h>

namespace
{
// Keep the control client's stable diagnostics while sharing the bounded
// frame transport. Linking its static archive pulls only the framing object,
// never the Agent-facing tool codec or any order/Broker implementation.
void SupervisorFrameReason(std::string& reason)
{
    reason = reason == "INVALID_FRAME_SIZE" ?
        "INVALID_SUPERVISOR_FRAME_SIZE" : "SUPERVISOR_" + reason;
}

bool WriteFrame(int descriptor, const std::string& body,
                int timeoutMs, std::string& reason)
{
    if (TypedToolProtocol::WriteFrame(descriptor, body, timeoutMs, reason)) return true;
    SupervisorFrameReason(reason);
    return false;
}

bool ReadFrame(int descriptor, std::size_t maximum, int timeoutMs,
               std::string& body, std::string& reason)
{
    if (TypedToolProtocol::ReadFrame(descriptor, maximum, timeoutMs, body, reason)) return true;
    SupervisorFrameReason(reason);
    return false;
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

    if (!TypedToolProtocol::ConnectUnixSocket(descriptor, socketPath, timeoutMs))
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
