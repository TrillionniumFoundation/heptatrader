#include "unix_execution_service_client.h"
#include "execution_service_protocol.h"
#include "unix_execution_service_internal.h"
#include <cerrno>
#include <algorithm>
#include <fstream>
#include <limits>
#include <mutex>
#include <poll.h>
#include <sstream>
#include <sys/socket.h>
#include <sys/un.h>
#include <unistd.h>
using namespace HeptaExecutionServiceInternal;
UnixExecutionServiceClient::UnixExecutionServiceClient(const std::string& socketPath,
                                                       int ioTimeoutMs,
                                                       std::size_t maxResponseBytes,
                                                       const std::set<std::uint32_t>& allowedServerUids,
                                                       int responseTimeoutMs,
                                                       std::uint32_t expectedServerPid,
                                                       std::uint64_t expectedServerStartTicks,
                                                       ProcessBindingMode processBindingMode)
    : m_socketPath(socketPath), m_expectedServerPid(expectedServerPid),
      m_expectedServerStartTicks(expectedServerStartTicks), m_processBindingMode(processBindingMode),
      m_ioTimeoutMs(ioTimeoutMs),
      m_responseTimeoutMs(responseTimeoutMs > 0 ? responseTimeoutMs : ioTimeoutMs),
      m_maxResponseBytes(maxResponseBytes), m_allowedServerUids(allowedServerUids)
{
    if (m_allowedServerUids.empty())
        m_allowedServerUids.insert(static_cast<std::uint32_t>(::geteuid()));
}

bool UnixExecutionServiceClient::ConfigureResponseBinding(
    int fd, ExecutionServiceOperation operation, bool& responseBound, std::string& reason) const
{
    responseBound = false;
    if (m_processBindingMode != ProcessBindingMode::InactiveOwnerAuditResponse) return true;
    // The special audit mode never degrades to the ordinary UID-only path.
    if (!m_expectedServerPid || !m_expectedServerStartTicks)
    {
        reason = "EXECUTION_SERVICE_PEER_PROCESS_MISMATCH"; return false;
    }
    responseBound = operation == ExecutionServiceOperation::GetServiceIdentity ||
        operation == ExecutionServiceOperation::ReadAuthoritativeState ||
        operation == ExecutionServiceOperation::AuditSessionOwner;
    if (!responseBound) return true;
    const int enabled = 1;
    // Set before connect so no queued response bytes can lack credentials.
    if (::setsockopt(fd, SOL_SOCKET, SO_PASSCRED, &enabled, sizeof(enabled)) != 0)
    {
        reason = "EXECUTION_SERVICE_RESPONSE_CREDENTIALS_UNAVAILABLE"; return false;
    }
    return true;
}

bool UnixExecutionServiceClient::VerifyServerPeer(
    int fd, bool responseBound, std::string& reason) const
{
    struct ucred credential;
    socklen_t length = sizeof(credential);
    if (::getsockopt(fd, SOL_SOCKET, SO_PEERCRED, &credential, &length) != 0 ||
        length != sizeof(credential) || !AllowedServerPeerCredential(
            static_cast<std::uint32_t>(credential.uid), credential.pid, m_allowedServerUids))
    {
        reason = "execution service peer uid rejected"; return false;
    }
    if (!m_expectedServerPid && !m_expectedServerStartTicks) return true;
    return VerifyServerProcess(responseBound ? m_expectedServerPid :
        static_cast<std::uint32_t>(credential.pid), reason);
}

bool UnixExecutionServiceClient::VerifyResponseSender(
    std::uint32_t uid, int pid, std::string& reason) const
{
    // The listener's PID 1 activation exception is not a sender exception.
    if (m_allowedServerUids.find(uid) == m_allowedServerUids.end())
    {
        reason = "EXECUTION_SERVICE_RESPONSE_SENDER_UID_REJECTED"; return false;
    }
    if (pid <= 0)
    {
        reason = "EXECUTION_SERVICE_PEER_PROCESS_MISMATCH"; return false;
    }
    return VerifyServerProcess(static_cast<std::uint32_t>(pid), reason);
}

bool UnixExecutionServiceClient::VerifyServerProcess(
    std::uint32_t pid, std::string& reason) const
{
    if (!m_expectedServerPid || !m_expectedServerStartTicks ||
        pid == 0 || pid != m_expectedServerPid)
    {
        reason = "EXECUTION_SERVICE_PEER_PROCESS_MISMATCH"; return false;
    }
    std::ifstream input("/proc/" + std::to_string(pid) + "/stat");
    std::string line;
    if (!std::getline(input, line) || line.size() > 8192 || line.rfind(')') == std::string::npos)
    {
        reason = "EXECUTION_SERVICE_PEER_PROCESS_UNAVAILABLE"; return false;
    }
    std::istringstream fields(line.substr(line.rfind(')') + 1));
    std::string token;
    for (unsigned int i = 0; i != 20; ++i)
        if (!(fields >> token))
        {
            reason = "EXECUTION_SERVICE_PEER_PROCESS_UNAVAILABLE"; return false;
        }
    std::uint64_t ticks = 0;
    if (token.empty())
    {
        reason = "EXECUTION_SERVICE_PEER_PROCESS_UNAVAILABLE"; return false;
    }
    for (char digit : token)
    {
        if (digit < '0' || digit > '9' || ticks >
            (std::numeric_limits<std::uint64_t>::max() - (digit - '0')) / 10)
        {
            reason = "EXECUTION_SERVICE_PEER_PROCESS_UNAVAILABLE"; return false;
        }
        ticks = ticks * 10 + (digit - '0');
    }
    if (ticks != m_expectedServerStartTicks)
    {
        reason = "EXECUTION_SERVICE_PEER_PROCESS_MISMATCH"; return false;
    }
    return true;
}
ExecutionCommandResult UnixExecutionServiceClient::PlaceOrder(const PlaceOrderCommand& command)
{
    ExecutionServiceIdentity identity;
    std::string reason;
    if (!GetServiceIdentity(identity, reason))
        return TransportFailure(command.context.toolCallId, reason);
    return PlaceIbOrderWithIdentity(command, identity);
}
ExecutionCommandResult UnixExecutionServiceClient::PlaceIbOrderWithIdentity(
    const IbPlaceOrderCommand& command,
    const ExecutionServiceIdentity& identity)
{
    std::string reason;
    if (!ValidIdentity(identity))
        return TransportFailure(command.context.toolCallId,
            "EXECUTION_SERVICE_IDENTITY_INVALID");
    ExecutionServiceRequest request;
    request.operation = ExecutionServiceOperation::PlaceIbOrder;
    request.place = command;
    request.expectedServiceEpoch = identity.serviceEpoch;
    request.expectedServiceFencingGeneration = identity.serviceFencingGeneration;
    std::string body;
    if (!ExecutionServiceProtocol::EncodeRequest(request, body, reason))
        return TransportFailure(command.context.toolCallId, reason);
    const ExecutionCommandResult result = Call(command.context.toolCallId, body, identity, request.operation);
    if (result.reasonCode == "EXECUTION_SERVICE_EPOCH_MISMATCH" ||
        result.reasonCode == "EXECUTION_SERVICE_EPOCH_CHANGED")
        InvalidateServiceIdentity(identity);
    return result;
}
ExecutionCommandResult UnixExecutionServiceClient::CancelOrder(const CancelOrderCommand& command)
{
    ExecutionServiceIdentity identity;
    std::string reason;
    if (!GetServiceIdentity(identity, reason))
        return TransportFailure(command.context.toolCallId, reason);
    return CancelIbOrderWithIdentity(command, identity);
}
ExecutionCommandResult UnixExecutionServiceClient::CancelIbOrderWithIdentity(
    const IbCancelOrderCommand& command,
    const ExecutionServiceIdentity& identity)
{
    std::string reason;
    if (!ValidIdentity(identity))
        return TransportFailure(command.context.toolCallId,
            "EXECUTION_SERVICE_IDENTITY_INVALID");
    ExecutionServiceRequest request;
    request.operation = ExecutionServiceOperation::CancelIbOrder;
    request.cancel = command;
    request.expectedServiceEpoch = identity.serviceEpoch;
    request.expectedServiceFencingGeneration = identity.serviceFencingGeneration;
    std::string body;
    if (!ExecutionServiceProtocol::EncodeRequest(request, body, reason))
        return TransportFailure(command.context.toolCallId, reason);
    const ExecutionCommandResult result = Call(command.context.toolCallId, body, identity, request.operation);
    if (result.reasonCode == "EXECUTION_SERVICE_EPOCH_MISMATCH" ||
        result.reasonCode == "EXECUTION_SERVICE_EPOCH_CHANGED")
        InvalidateServiceIdentity(identity);
    return result;
}
ExecutionControlStatusResult UnixExecutionServiceClient::QueryCommandStatus(
    const ExecutionControlCommand& command)
{
    ExecutionServiceIdentity identity;
    std::string reason;
    if (!GetServiceIdentity(identity, reason, command.localDeadline))
        return NarrowControlStatusResult(
            ControlTransportFailure(command.context.toolCallId, reason));
    return QueryCommandStatusWithIdentity(command, identity);
}
ExecutionControlStatusResult UnixExecutionServiceClient::QueryCommandStatusWithIdentity(
    const ExecutionControlCommand& command,
    const ExecutionServiceIdentity& identity)
{
    const ExecutionServiceOperation operation =
        command.recoveryIngressFence == 0 ?
        ExecutionServiceOperation::QueryCommandStatus :
        ExecutionServiceOperation::RecoveryQueryCommandStatus;
    return NarrowControlStatusResult(
        DispatchControlWithIdentity(command, identity, operation));
}
ExecutionOwnerAuditResult UnixExecutionServiceClient::AuditSessionLease(
    const ExecutionControlCommand& command)
{
    ExecutionServiceIdentity identity;
    std::string reason;
    if (!GetServiceIdentity(identity, reason, command.localDeadline))
        return NarrowOwnerAuditResult(
            ControlTransportFailure(command.context.toolCallId, reason));
    return AuditSessionLeaseWithIdentity(command, identity);
}
ExecutionOwnerAuditResult UnixExecutionServiceClient::AuditSessionLeaseWithIdentity(
    const ExecutionControlCommand& command,
    const ExecutionServiceIdentity& identity)
{
    return NarrowOwnerAuditResult(DispatchControlWithIdentity(
        command, identity, ExecutionServiceOperation::AuditSessionLease));
}
ExecutionOwnerAuditResult UnixExecutionServiceClient::AuditSessionOwner(
    const ExecutionControlCommand& command)
{
    ExecutionServiceIdentity identity;
    std::string reason;
    if (!GetServiceIdentity(identity, reason, command.localDeadline))
        return NarrowOwnerAuditResult(
            ControlTransportFailure(command.context.toolCallId, reason));
    return AuditSessionOwnerWithIdentity(command, identity);
}
ExecutionOwnerAuditResult UnixExecutionServiceClient::AuditSessionOwnerWithIdentity(
    const ExecutionControlCommand& command,
    const ExecutionServiceIdentity& identity)
{
    return NarrowOwnerAuditResult(DispatchControlWithIdentity(
        command, identity, ExecutionServiceOperation::AuditSessionOwner));
}
ExecutionOwnerAuditResult UnixExecutionServiceClient::RecoveryAuditOwner(
    const ExecutionControlCommand& command)
{
    ExecutionServiceIdentity identity;
    std::string reason;
    if (!GetServiceIdentity(identity, reason, command.localDeadline))
        return NarrowOwnerAuditResult(
            ControlTransportFailure(command.context.toolCallId, reason));
    return RecoveryAuditOwnerWithIdentity(command, identity);
}
ExecutionOwnerAuditResult UnixExecutionServiceClient::RecoveryAuditOwnerWithIdentity(
    const ExecutionControlCommand& command,
    const ExecutionServiceIdentity& identity)
{
    return NarrowOwnerAuditResult(DispatchControlWithIdentity(
        command, identity, ExecutionServiceOperation::RecoveryAuditOwner));
}
ExecutionTerminalResult UnixExecutionServiceClient::TerminalizeRecoveryOwner(
    const ExecutionControlCommand& command)
{
    ExecutionServiceIdentity identity;
    std::string reason;
    if (!GetServiceIdentity(identity, reason, command.localDeadline))
        return NarrowTerminalResult(
            ControlTransportFailure(command.context.toolCallId, reason));
    return TerminalizeRecoveryOwnerWithIdentity(command, identity);
}
ExecutionTerminalResult
UnixExecutionServiceClient::TerminalizeRecoveryOwnerWithIdentity(
    const ExecutionControlCommand& command,
    const ExecutionServiceIdentity& identity)
{
    return NarrowTerminalResult(DispatchControlWithIdentity(
        command, identity,
        ExecutionServiceOperation::TerminalizeRecoveryOwner));
}
ExecutionControlStatusResult UnixExecutionServiceClient::FenceAccountNewEntries(
    const ExecutionControlCommand& command)
{
    ExecutionServiceIdentity identity;
    std::string reason;
    if (!GetServiceIdentity(identity, reason, command.localDeadline))
        return NarrowControlStatusResult(
            ControlTransportFailure(command.context.toolCallId, reason));
    return NarrowControlStatusResult(DispatchControlWithIdentity(
        command, identity, ExecutionServiceOperation::FenceAccountNewEntries));
}

ExecutionControlStatusResult UnixExecutionServiceClient::FenceSessionOwner(
    const ExecutionControlCommand& command)
{
    ExecutionServiceIdentity identity;
    std::string reason;
    if (!GetServiceIdentity(identity, reason, command.localDeadline))
        return NarrowControlStatusResult(
            ControlTransportFailure(command.context.toolCallId, reason));
    return FenceSessionOwnerWithIdentity(command, identity);
}
ExecutionControlStatusResult UnixExecutionServiceClient::FenceSessionOwnerWithIdentity(
    const ExecutionControlCommand& command,
    const ExecutionServiceIdentity& identity)
{
    return NarrowControlStatusResult(DispatchControlWithIdentity(
        command, identity, ExecutionServiceOperation::FenceSessionOwner));
}
ExecutionControlStatusResult UnixExecutionServiceClient::ReleaseSessionOwnerFence(
    const ExecutionControlCommand& command)
{
    ExecutionServiceIdentity identity;
    std::string reason;
    if (!GetServiceIdentity(identity, reason, command.localDeadline))
        return NarrowControlStatusResult(
            ControlTransportFailure(command.context.toolCallId, reason));
    return ReleaseSessionOwnerFenceWithIdentity(command, identity);
}
ExecutionControlStatusResult UnixExecutionServiceClient::ReleaseSessionOwnerFenceWithIdentity(
    const ExecutionControlCommand& command,
    const ExecutionServiceIdentity& identity)
{
    return NarrowControlStatusResult(DispatchControlWithIdentity(
        command, identity,
        ExecutionServiceOperation::ReleaseSessionOwnerFence));
}
ExecutionControlStatusResult UnixExecutionServiceClient::ReconcileAuthoritativeState(
    const ExecutionControlCommand& command)
{
    ExecutionServiceIdentity identity;
    std::string reason;
    if (!GetServiceIdentity(identity, reason, command.localDeadline))
        return NarrowControlStatusResult(
            ControlTransportFailure(command.context.toolCallId, reason));
    return ReconcileAuthoritativeStateWithIdentity(command, identity);
}
ExecutionControlStatusResult UnixExecutionServiceClient::ReconcileAuthoritativeStateWithIdentity(
    const ExecutionControlCommand& command,
    const ExecutionServiceIdentity& identity)
{
    return NarrowControlStatusResult(DispatchControlWithIdentity(
        command, identity,
        ExecutionServiceOperation::ReconcileAuthoritativeState));
}
ExecutionControlResult UnixExecutionServiceClient::DispatchControlWithIdentity(
    const ExecutionControlCommand& command,
    const ExecutionServiceIdentity& identity,
    ExecutionServiceOperation operation)
{
    if (!ValidIdentity(identity))
        return ControlTransportFailure(command.context.toolCallId,
            "EXECUTION_SERVICE_IDENTITY_INVALID");
    ExecutionServiceRequest request;
    request.operation = operation;
    request.control = command;
    request.expectedServiceEpoch = identity.serviceEpoch;
    request.expectedServiceFencingGeneration = identity.serviceFencingGeneration;
    std::string body;
    std::string reason;
    if (!ExecutionServiceProtocol::EncodeRequest(request, body, reason))
        return ControlTransportFailure(command.context.toolCallId, reason);
    const ExecutionControlResult result =
        CallControl(command.context.toolCallId, body, identity, request.operation, command.localDeadline);
    if (result.reasonCode == "EXECUTION_SERVICE_EPOCH_MISMATCH" ||
        result.reasonCode == "EXECUTION_SERVICE_EPOCH_CHANGED")
        InvalidateServiceIdentity(identity);
    return result;
}
ExecutionCommandResult UnixExecutionServiceClient::ReadAuthoritativeState(
    const ExecutionReadCommand& command)
{
    ExecutionServiceIdentity identity;
    std::string reason;
    if (!GetServiceIdentity(identity, reason))
        return TransportFailure(command.context.toolCallId, reason);
    return ReadAuthoritativeStateWithIdentity(command, identity);
}
ExecutionCommandResult UnixExecutionServiceClient::PreviewOrder(
    const PlaceOrderCommand& command)
{
    ExecutionServiceIdentity identity;
    std::string reason;
    if (!GetServiceIdentity(identity, reason))
        return TransportFailure(command.context.toolCallId, reason);
    return PreviewOrderWithIdentity(command, identity);
}
ExecutionCommandResult UnixExecutionServiceClient::PreviewOrderWithIdentity(
    const PlaceOrderCommand& command,
    const ExecutionServiceIdentity& identity)
{
    if (!ValidIdentity(identity))
        return TransportFailure(command.context.toolCallId,
            "EXECUTION_SERVICE_IDENTITY_INVALID");
    ExecutionServiceRequest request;
    request.operation = ExecutionServiceOperation::PreviewOrder;
    request.place = command;
    request.expectedServiceEpoch = identity.serviceEpoch;
    request.expectedServiceFencingGeneration = identity.serviceFencingGeneration;
    std::string body;
    std::string reason;
    if (!ExecutionServiceProtocol::EncodeRequest(request, body, reason))
        return TransportFailure(command.context.toolCallId, reason);
    const ExecutionCommandResult result = Call(
        command.context.toolCallId, body, identity, request.operation);
    if (result.reasonCode == "EXECUTION_SERVICE_EPOCH_MISMATCH" ||
        result.reasonCode == "EXECUTION_SERVICE_EPOCH_CHANGED")
        InvalidateServiceIdentity(identity);
    return result;
}
ExecutionCommandResult UnixExecutionServiceClient::ReadAuthoritativeStateWithIdentity(
    const ExecutionReadCommand& command,
    const ExecutionServiceIdentity& identity)
{
    if (!ValidIdentity(identity))
        return TransportFailure(command.context.toolCallId,
            "EXECUTION_SERVICE_IDENTITY_INVALID");
    ExecutionServiceRequest request;
    request.operation = ExecutionServiceOperation::ReadAuthoritativeState;
    request.read = command;
    request.expectedServiceEpoch = identity.serviceEpoch;
    request.expectedServiceFencingGeneration = identity.serviceFencingGeneration;
    std::string body;
    std::string reason;
    if (!ExecutionServiceProtocol::EncodeRequest(request, body, reason))
        return TransportFailure(command.context.toolCallId, reason);
    const ExecutionCommandResult result = Call(
        command.context.toolCallId, body, identity, request.operation);
    if (result.reasonCode == "EXECUTION_SERVICE_EPOCH_MISMATCH" ||
        result.reasonCode == "EXECUTION_SERVICE_EPOCH_CHANGED")
        InvalidateServiceIdentity(identity);
    return result;
}
bool UnixExecutionServiceClient::GetServiceIdentity(
    ExecutionServiceIdentity& identity,
    std::string& reason, std::chrono::steady_clock::time_point deadline)
{
    std::unique_lock<std::timed_mutex> lock(m_serviceIdentityMutex, std::defer_lock);
    if (deadline == std::chrono::steady_clock::time_point::max()) lock.lock();
    else if (!lock.try_lock_until(deadline))
    {
        reason = "EXECUTION_CONTROL_WORK_BUDGET_EXHAUSTED";
        return false;
    }
    if (ValidIdentity(m_serviceIdentity))
    {
        identity = m_serviceIdentity;
        reason.clear();
        return true;
    }
    ExecutionServiceRequest request;
    request.operation = ExecutionServiceOperation::GetServiceIdentity;
    std::string body;
    if (!ExecutionServiceProtocol::EncodeRequest(request, body, reason)) return false;
    const ExecutionCommandResult result = Call(
        "__service_identity__", body, ExecutionServiceIdentity(), request.operation, deadline);
    ExecutionServiceIdentity received;
    received.serviceEpoch = result.serviceEpoch;
    received.serviceFencingGeneration = result.serviceFencingGeneration;
    if (result.status != ExecutionCommandStatus::Accepted || !ValidIdentity(received))
    {
        reason = result.detail.empty() ? result.reasonCode : result.detail;
        if (reason.empty()) reason = "EXECUTION_SERVICE_IDENTITY_INVALID";
        return false;
    }
    m_serviceIdentity = received;
    identity = m_serviceIdentity;
    reason.clear();
    return true;
}
void UnixExecutionServiceClient::InvalidateServiceIdentity(
    const ExecutionServiceIdentity& identity)
{
    InvalidateServiceIdentity(identity,
        std::chrono::steady_clock::time_point::max());
}
bool UnixExecutionServiceClient::InvalidateServiceIdentity(
    const ExecutionServiceIdentity& identity,
    std::chrono::steady_clock::time_point deadline)
{
    std::unique_lock<std::timed_mutex> lock(m_serviceIdentityMutex, std::defer_lock);
    if (deadline == std::chrono::steady_clock::time_point::max()) lock.lock();
    else if (!lock.try_lock_until(deadline)) return false;
    if (SameIdentity(m_serviceIdentity, identity))
        m_serviceIdentity = ExecutionServiceIdentity();
    return true;
}
ExecutionCommandResult UnixExecutionServiceClient::Call(const std::string& commandId,
                                                        const std::string& requestBody,
                                                        const ExecutionServiceIdentity&
                                                            expectedIdentity,
                                                        ExecutionServiceOperation operation,
                                                        std::chrono::steady_clock::time_point deadline)
{
    if (std::chrono::steady_clock::now() >= deadline)
        return TransportFailure(commandId, "EXECUTION_CONTROL_WORK_BUDGET_EXHAUSTED");
    const IoDeadline requestDeadline = std::min(DeadlineAfter(m_ioTimeoutMs), deadline);
    struct sockaddr_un address;
    std::string reason;
    if (!BuildAddress(m_socketPath, address, reason)) return TransportFailure(commandId, reason);
    const int fd = ::socket(AF_UNIX, SOCK_STREAM | SOCK_CLOEXEC | SOCK_NONBLOCK, 0);
    if (fd < 0) return TransportFailure(commandId, "socket creation failed");
    bool responseBound = false;
    if (!ConfigureResponseBinding(fd, operation, responseBound, reason))
    {
        ::close(fd);
        return TransportFailure(commandId, reason);
    }
    int rc = ::connect(fd, reinterpret_cast<struct sockaddr*>(&address), sizeof(address));
    if (rc != 0 && errno == EINPROGRESS)
    {
        if (!WaitFd(fd, POLLOUT, requestDeadline))
        {
            ::close(fd);
            return TransportFailure(commandId, "connect timeout");
        }
        int socketError = 0;
        socklen_t socketErrorLength = sizeof(socketError);
        if (::getsockopt(fd, SOL_SOCKET, SO_ERROR, &socketError, &socketErrorLength) != 0 || socketError != 0)
        {
            ::close(fd);
            return TransportFailure(commandId, "connect failed");
        }
    }
    else if (rc != 0)
    {
        ::close(fd);
        return TransportFailure(commandId, "connect failed");
    }
    if (!VerifyServerPeer(fd, responseBound, reason))
    {
        ::close(fd);
        return TransportFailure(commandId, reason);
    }
    if (!WriteFrame(fd, requestBody, requestDeadline))
    {
        ::close(fd);
        return TransportFailure(commandId, "request write failed");
    }
    // Once a complete request is written, waiting for its durable authority
    // result has a separate bound from connect/framing. Expiry remains a
    // conservative transport failure and never triggers an automatic retry.
    const IoDeadline responseDeadline = std::min(DeadlineAfter(m_responseTimeoutMs), deadline);
    std::string responseBody;
    const bool read = responseBound ? ReadFrameWithCredentials(
        fd, m_maxResponseBytes, responseDeadline, responseBody,
        [this](std::uint32_t uid, int pid, std::string& error) {
            return VerifyResponseSender(uid, pid, error);
        }, reason) : ReadFrame(fd, m_maxResponseBytes, responseDeadline, responseBody);
    if (!read)
    {
        ::close(fd);
        return TransportFailure(commandId, reason.empty() ? "response read failed" : reason);
    }
    ::close(fd);
    ExecutionCommandResult result;
    if (!ExecutionServiceProtocol::DecodeResponse(responseBody, result, reason))
        return TransportFailure(commandId, reason);
    if (result.commandId != commandId)
        return TransportFailure(commandId, "EXECUTION_PROTOCOL_RESPONSE_COMMAND_ID_MISMATCH");
    ExecutionServiceIdentity receivedIdentity;
    receivedIdentity.serviceEpoch = result.serviceEpoch;
    receivedIdentity.serviceFencingGeneration = result.serviceFencingGeneration;
    if (!ValidIdentity(receivedIdentity) ||
        (ValidIdentity(expectedIdentity) &&
         !SameIdentity(receivedIdentity, expectedIdentity) &&
         result.reasonCode != "EXECUTION_SERVICE_EPOCH_MISMATCH"))
        return TransportFailure(commandId, "EXECUTION_SERVICE_EPOCH_CHANGED");
    return result;
}
ExecutionControlResult UnixExecutionServiceClient::CallControl(
    const std::string& commandId,
    const std::string& requestBody,
    const ExecutionServiceIdentity& expectedIdentity,
    ExecutionServiceOperation operation,
    std::chrono::steady_clock::time_point deadline)
{
    if (std::chrono::steady_clock::now() >= deadline)
        return ControlTransportFailure(commandId, "EXECUTION_CONTROL_WORK_BUDGET_EXHAUSTED");
    const IoDeadline requestDeadline = std::min(DeadlineAfter(m_ioTimeoutMs), deadline);
    struct sockaddr_un address;
    std::string reason;
    if (!BuildAddress(m_socketPath, address, reason))
        return ControlTransportFailure(commandId, reason);
    const int fd = ::socket(AF_UNIX, SOCK_STREAM | SOCK_CLOEXEC | SOCK_NONBLOCK, 0);
    if (fd < 0) return ControlTransportFailure(commandId, "socket creation failed");
    bool responseBound = false;
    if (!ConfigureResponseBinding(fd, operation, responseBound, reason))
    {
        ::close(fd);
        return ControlTransportFailure(commandId, reason);
    }
    int rc = ::connect(fd, reinterpret_cast<struct sockaddr*>(&address), sizeof(address));
    if (rc != 0 && errno == EINPROGRESS)
    {
        if (!WaitFd(fd, POLLOUT, requestDeadline))
        {
            ::close(fd);
            return ControlTransportFailure(commandId, "connect timeout");
        }
        int socketError = 0;
        socklen_t socketErrorLength = sizeof(socketError);
        if (::getsockopt(fd, SOL_SOCKET, SO_ERROR, &socketError, &socketErrorLength) != 0 ||
            socketError != 0)
        {
            ::close(fd);
            return ControlTransportFailure(commandId, "connect failed");
        }
    }
    else if (rc != 0)
    {
        ::close(fd);
        return ControlTransportFailure(commandId, "connect failed");
    }
    if (!VerifyServerPeer(fd, responseBound, reason))
    {
        ::close(fd);
        return ControlTransportFailure(commandId, reason);
    }
    if (!WriteFrame(fd, requestBody, requestDeadline))
    {
        ::close(fd);
        return ControlTransportFailure(commandId, "request write failed");
    }
    // Once a complete request is written, waiting for its durable authority
    // result has a separate bound from connect/framing. Expiry remains a
    // conservative transport failure and never triggers an automatic retry.
    const IoDeadline responseDeadline = std::min(DeadlineAfter(m_responseTimeoutMs), deadline);
    std::string responseBody;
    const bool read = responseBound ? ReadFrameWithCredentials(
        fd, m_maxResponseBytes, responseDeadline, responseBody,
        [this](std::uint32_t uid, int pid, std::string& error) {
            return VerifyResponseSender(uid, pid, error);
        }, reason) : ReadFrame(fd, m_maxResponseBytes, responseDeadline, responseBody);
    if (!read)
    {
        ::close(fd);
        return ControlTransportFailure(commandId, reason.empty() ? "response read failed" : reason);
    }
    ::close(fd);
    ExecutionControlResult result;
    if (!ExecutionServiceProtocol::DecodeControlResponse(responseBody, result, reason))
        return ControlTransportFailure(commandId, reason);
    if (result.commandId != commandId)
        return ControlTransportFailure(commandId,
            "EXECUTION_PROTOCOL_RESPONSE_COMMAND_ID_MISMATCH");
    ExecutionServiceIdentity receivedIdentity;
    receivedIdentity.serviceEpoch = result.serviceEpoch;
    receivedIdentity.serviceFencingGeneration = result.serviceFencingGeneration;
    if (!ValidIdentity(receivedIdentity) ||
        !ValidIdentity(expectedIdentity) ||
        (!SameIdentity(receivedIdentity, expectedIdentity) &&
         result.reasonCode != "EXECUTION_SERVICE_EPOCH_MISMATCH"))
        return ControlTransportFailure(commandId, "EXECUTION_SERVICE_EPOCH_CHANGED");
    return result;
}
