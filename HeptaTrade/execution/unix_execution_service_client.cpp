#include "unix_execution_service_client.h"
#include "execution_service_protocol.h"
#include "unix_execution_service_internal.h"
#include <cerrno>
#include <algorithm>
#include <mutex>
#include <poll.h>
#include <sys/socket.h>
#include <sys/un.h>
#include <unistd.h>
using namespace HeptaExecutionServiceInternal;
UnixExecutionServiceClient::UnixExecutionServiceClient(const std::string& socketPath,
                                                       int ioTimeoutMs,
                                                       std::size_t maxResponseBytes,
                                                       const std::set<std::uint32_t>& allowedServerUids,
                                                       int responseTimeoutMs)
    : m_socketPath(socketPath), m_ioTimeoutMs(ioTimeoutMs),
      m_responseTimeoutMs(responseTimeoutMs > 0 ? responseTimeoutMs : ioTimeoutMs),
      m_maxResponseBytes(maxResponseBytes), m_allowedServerUids(allowedServerUids)
{
    if (m_allowedServerUids.empty())
        m_allowedServerUids.insert(static_cast<std::uint32_t>(::geteuid()));
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
    const ExecutionCommandResult result = Call(command.context.toolCallId, body, identity);
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
    const ExecutionCommandResult result = Call(command.context.toolCallId, body, identity);
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
        CallControl(command.context.toolCallId, body, identity, command.localDeadline);
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
        command.context.toolCallId, body, identity);
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
        command.context.toolCallId, body, identity);
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
        "__service_identity__", body, ExecutionServiceIdentity(), deadline);
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
    std::lock_guard<std::timed_mutex> lock(m_serviceIdentityMutex);
    if (SameIdentity(m_serviceIdentity, identity))
        m_serviceIdentity = ExecutionServiceIdentity();
}
ExecutionCommandResult UnixExecutionServiceClient::Call(const std::string& commandId,
                                                        const std::string& requestBody,
                                                        const ExecutionServiceIdentity&
                                                            expectedIdentity,
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
    if (!m_allowedServerUids.empty())
    {
        struct ucred credential;
        socklen_t credentialLength = sizeof(credential);
        if (::getsockopt(fd, SOL_SOCKET, SO_PEERCRED, &credential, &credentialLength) != 0 ||
            credentialLength != sizeof(credential) ||
            !AllowedServerPeerCredential(
                static_cast<std::uint32_t>(credential.uid),
                credential.pid, m_allowedServerUids))
        {
            ::close(fd);
            return TransportFailure(commandId, "execution service peer uid rejected");
        }
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
    if (!ReadFrame(fd, m_maxResponseBytes, responseDeadline, responseBody))
    {
        ::close(fd);
        return TransportFailure(commandId, "response read failed");
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
    struct ucred credential;
    socklen_t credentialLength = sizeof(credential);
    if (::getsockopt(fd, SOL_SOCKET, SO_PEERCRED, &credential, &credentialLength) != 0 ||
        credentialLength != sizeof(credential) ||
        !AllowedServerPeerCredential(
            static_cast<std::uint32_t>(credential.uid),
            credential.pid, m_allowedServerUids))
    {
        ::close(fd);
        return ControlTransportFailure(commandId, "execution service peer uid rejected");
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
    if (!ReadFrame(fd, m_maxResponseBytes, responseDeadline, responseBody))
    {
        ::close(fd);
        return ControlTransportFailure(commandId, "response read failed");
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
