#include "unix_execution_service_client.h"

#include "execution_service_protocol.h"
#include "unix_execution_service_internal.h"

using namespace HeptaExecutionServiceInternal;

ExecutionCommandResult UnixExecutionServiceClient::FlattenPosition(
    const FlattenPositionCommand& command)
{
    ExecutionServiceIdentity identity;
    std::string reason;
    if (!GetServiceIdentity(identity, reason))
        return TransportFailure(command.context.toolCallId, reason);
    return FlattenPositionWithIdentity(command, identity);
}

ExecutionCommandResult UnixExecutionServiceClient::FlattenPositionWithIdentity(
    const FlattenPositionCommand& command,
    const ExecutionServiceIdentity& identity)
{
    std::string reason;
    if (!ValidIdentity(identity))
        return TransportFailure(command.context.toolCallId,
            "EXECUTION_SERVICE_IDENTITY_INVALID");
    ExecutionServiceRequest request;
    request.operation = ExecutionServiceOperation::FlattenPosition;
    request.flatten = command;
    request.expectedServiceEpoch = identity.serviceEpoch;
    request.expectedServiceFencingGeneration =
        identity.serviceFencingGeneration;
    std::string body;
    if (!ExecutionServiceProtocol::EncodeRequest(request, body, reason))
        return TransportFailure(command.context.toolCallId, reason);
    const ExecutionCommandResult result =
        Call(command.context.toolCallId, body, identity);
    if (result.reasonCode == "EXECUTION_SERVICE_EPOCH_MISMATCH" ||
        result.reasonCode == "EXECUTION_SERVICE_EPOCH_CHANGED")
        InvalidateServiceIdentity(identity);
    return result;
}

ExecutionCommandResult UnixExecutionServiceClient::PreviewFlattenPosition(
    const FlattenPositionCommand& command)
{
    ExecutionServiceIdentity identity;
    std::string reason;
    if (!GetServiceIdentity(identity, reason))
        return TransportFailure(command.context.toolCallId, reason);
    return PreviewFlattenPositionWithIdentity(command, identity);
}

ExecutionCommandResult
UnixExecutionServiceClient::PreviewFlattenPositionWithIdentity(
    const FlattenPositionCommand& command,
    const ExecutionServiceIdentity& identity)
{
    if (!ValidIdentity(identity))
        return TransportFailure(command.context.toolCallId,
            "EXECUTION_SERVICE_IDENTITY_INVALID");
    ExecutionServiceRequest request;
    request.operation =
        ExecutionServiceOperation::PreviewFlattenPosition;
    request.flatten = command;
    request.expectedServiceEpoch = identity.serviceEpoch;
    request.expectedServiceFencingGeneration =
        identity.serviceFencingGeneration;
    std::string body;
    std::string reason;
    if (!ExecutionServiceProtocol::EncodeRequest(request, body, reason))
        return TransportFailure(command.context.toolCallId, reason);
    const ExecutionCommandResult result =
        Call(command.context.toolCallId, body, identity);
    if (result.reasonCode == "EXECUTION_SERVICE_EPOCH_MISMATCH" ||
        result.reasonCode == "EXECUTION_SERVICE_EPOCH_CHANGED")
        InvalidateServiceIdentity(identity);
    return result;
}
