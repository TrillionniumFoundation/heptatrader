#include "unix_execution_service_server.h"
#include "execution_decision_lease_authority.h"

#include <sstream>
ExecutionCommandResult UnixExecutionServiceServer::DispatchFlattenPosition(
    const FlattenPositionCommand& command)
{
    FlattenPositionCommand authorized = command;
    if (m_readAuthority != nullptr &&
        m_readAuthority->IsDurableFlattenReplay(authorized))
    {
        authorized.previewPermit.clear();
        return m_authority.FlattenPosition(authorized);
    }

    ExecutionCommandResult result;
    result.commandId = command.context.toolCallId;
    std::string reason;
    if (!ConsumeFlattenPreviewPermit(authorized, reason))
    {
        result.reasonCode = reason;
        result.detail =
            "Execution Service rejected the missing, expired, replayed, or mismatched flatten preview permit";
        return result;
    }
    if (!m_decisionLeases)
    {
        result.reasonCode = "EXECUTION_DECISION_LEASE_AUTHORITY_REQUIRED";
        return result;
    }
    if (!m_decisionLeases->Authorize(
            authorized.context, authorized.instrument, reason))
    {
        result.reasonCode = reason;
        result.detail =
            "Execution Service could not grant the flatten mutation lease";
        return result;
    }
    authorized.previewPermit.clear();
    return m_authority.FlattenPosition(authorized);
}

ExecutionCommandResult UnixExecutionServiceServer::DispatchFlattenPreview(
    const FlattenPositionCommand& command)
{
    ExecutionCommandResult result;
    result.commandId = command.context.toolCallId;
    if (m_readAuthority == nullptr)
    {
        result.reasonCode = "EXECUTION_FLATTEN_PREVIEW_UNAVAILABLE";
        return result;
    }
    result = m_readAuthority->PreviewFlattenPosition(command);
    if (result.status != ExecutionCommandStatus::Accepted) return result;

    std::string permit;
    std::string mutationCommandId;
    long long permitExpiry = 0;
    std::string reason;
    if (!IssueFlattenPreviewPermit(
            command, result, permit, mutationCommandId,
            permitExpiry, reason))
    {
        result.status = ExecutionCommandStatus::Rejected;
        result.reasonCode = reason;
        result.detail.clear();
        return result;
    }
    const std::string authoritative =
        result.detail.empty() ? "null" : result.detail;
    std::ostringstream payload;
    payload << "{\"approved\":true,\"preview_permit\":\""
            << permit << "\",\"command_id\":\"" << mutationCommandId
            << "\",\"permit_expires_at_ms\":" << permitExpiry
            << ",\"single_use\":true,\"service_epoch\":\""
            << m_serviceIdentity.serviceEpoch
            << "\",\"service_fencing_generation\":"
            << m_serviceIdentity.serviceFencingGeneration
            << ",\"authoritative_preview\":" << authoritative << '}';
    result.detail = payload.str();
    return result;
}
