#include "unix_execution_service_server.h"
#include "unix_execution_service_internal.h"
#include "execution_decision_lease_authority.h"

#include <chrono>
#include <exception>
#include <iomanip>
#include <locale>
#include <sstream>

namespace
{
void AppendDispatchField(std::string& output, const std::string& value)
{
    output.append(std::to_string(value.size()));
    output.push_back(':');
    output.append(value);
    output.push_back('\n');
}

template <typename T>
std::string DispatchNumber(T value)
{
    std::ostringstream output;
    if (value == 0) return "0";
    output.imbue(std::locale::classic());
    output << std::setprecision(17) << value;
    return output.str();
}

std::string FlattenDispatchFingerprint(const FlattenPositionCommand& command)
{
    std::string value("flatten\n");
    AppendDispatchField(value, command.context.agentId);
    AppendDispatchField(value, command.context.sessionId);
    AppendDispatchField(value, command.context.strategy);
    AppendDispatchField(value, command.context.account);
    AppendDispatchField(value, command.context.venue);
    AppendDispatchField(value, command.context.executionDomain);
    AppendDispatchField(value, command.context.allowCancelAny ? "1" : "0");
    AppendDispatchField(value, command.instrument);
    AppendDispatchField(value, command.contract.symbol);
    AppendDispatchField(value, command.contract.secType);
    AppendDispatchField(value, command.contract.exchange);
    AppendDispatchField(value, command.contract.primaryExchange);
    AppendDispatchField(value, command.contract.currency);
    AppendDispatchField(value, command.contract.lastTradeDateOrContractMonth);
    AppendDispatchField(value, command.contract.right);
    AppendDispatchField(value, DispatchNumber(command.contract.strike));
    AppendDispatchField(value, command.contract.multiplier);
    AppendDispatchField(value, command.contract.tradingClass);
    AppendDispatchField(value, command.contract.localSymbol);
    return value;
}

std::string FlattenDispatchOwnerKey(const FlattenPositionCommand& command)
{
    std::string value;
    AppendDispatchField(value, command.context.agentId);
    AppendDispatchField(value, command.context.sessionId);
    return value;
}

std::string FlattenDispatchKey(const FlattenPositionCommand& command)
{
    std::string value("flatten\n");
    AppendDispatchField(value, command.context.agentId);
    AppendDispatchField(value, command.context.sessionId);
    AppendDispatchField(value, command.context.toolCallId);
    return value;
}

const std::size_t kMaxPreviewDispatchRecords = 2048;
const std::chrono::hours kPreviewDispatchReplayTtl(24);

bool ShouldRetainPreviewDispatch(const ExecutionCommandResult& result)
{
    // The one-time permit has already been consumed before the authority is
    // entered. A Rejected/Error result may still follow an opaque journal or
    // venue-side preflight, so retain every authority response for exact
    // replay rather than risking a second flatten dispatch.
    (void)result;
    return true;
}

ExecutionCommandResult PreviewDispatchInFlightResult(
    const std::string& commandId)
{
    ExecutionCommandResult result;
    result.status = ExecutionCommandStatus::Uncertain;
    result.commandId = commandId;
    result.reasonCode = "EXECUTION_COMMAND_IN_FLIGHT";
    result.detail =
        "the bound preview mutation is still being dispatched";
    return result;
}

ExecutionCommandResult ReplayPreviewDispatchResult(
    const ExecutionCommandResult& stored)
{
    ExecutionCommandResult result = stored;
    if (result.status == ExecutionCommandStatus::Accepted)
    {
        result.status = ExecutionCommandStatus::Duplicate;
        result.reasonCode = "DUPLICATE_TOOL_CALL";
        result.detail = "previous_status=accepted";
    }
    return result;
}

ExecutionCommandResult PreviewDispatchConflictResult(
    const std::string& commandId)
{
    ExecutionCommandResult result;
    result.status = ExecutionCommandStatus::Rejected;
    result.commandId = commandId;
    result.reasonCode = "IDEMPOTENCY_KEY_CONFLICT";
    result.detail =
        "tool_call_id was already used for a different preview mutation payload";
    return result;
}
}

ExecutionCommandResult UnixExecutionServiceServer::DispatchFlattenPosition(
    const FlattenPositionCommand& command)
{
    FlattenPositionCommand authorized = command;
    const std::string dispatchKey = FlattenDispatchKey(command);
    const std::string dispatchFingerprint =
        FlattenDispatchFingerprint(command);
    ExecutionCommandResult result;
    // Keep the response explicitly rejected until a successful authority
    // result has been obtained; this makes the local validation boundary
    // self-documenting even if the result default changes later.
    result.status = ExecutionCommandStatus::Rejected;
    result.commandId = command.context.toolCallId;

    // Replay/in-flight lookup is lock-only. Never call a user authority while
    // m_previewMutex is held: an authority may synchronously query the
    // service and would otherwise deadlock.
    {
        std::lock_guard<std::mutex> lock(m_previewMutex);
        const std::chrono::steady_clock::time_point steadyNow =
            std::chrono::steady_clock::now();
        for (std::unordered_map<std::string,
                 PreviewDispatchRecord>::iterator it =
                 m_previewDispatches.begin();
             it != m_previewDispatches.end();)
        {
            if (it->second.complete &&
                it->second.steadyExpiresAt <= steadyNow)
                it = m_previewDispatches.erase(it);
            else
                ++it;
        }
        const std::unordered_map<std::string,
            PreviewDispatchRecord>::const_iterator existing =
            m_previewDispatches.find(dispatchKey);
        if (existing != m_previewDispatches.end())
        {
            if (existing->second.fingerprint != dispatchFingerprint)
                return PreviewDispatchConflictResult(command.context.toolCallId);
            if (!existing->second.complete)
                return PreviewDispatchInFlightResult(command.context.toolCallId);
            return ReplayPreviewDispatchResult(existing->second.result);
        }
    }

    bool durableFlattenReplay = false;
    try
    {
        durableFlattenReplay = m_readAuthority != nullptr &&
            m_readAuthority->IsDurableFlattenReplay(authorized);
    }
    catch (...)
    {
        durableFlattenReplay = false;
    }
    if (durableFlattenReplay)
    {
        // Claim durable replays as well as permit-backed dispatches.  The
        // durable probe and authority call are separate operations, so two
        // direct callers can otherwise both invoke the authority before either
        // one installs a local replay witness.
        {
            std::lock_guard<std::mutex> lock(m_previewMutex);
            const std::chrono::steady_clock::time_point steadyNow =
                std::chrono::steady_clock::now();
            for (std::unordered_map<std::string,
                     PreviewDispatchRecord>::iterator it =
                     m_previewDispatches.begin();
                 it != m_previewDispatches.end();)
            {
                if (it->second.complete &&
                    it->second.steadyExpiresAt <= steadyNow)
                    it = m_previewDispatches.erase(it);
                else
                    ++it;
            }
            const std::unordered_map<std::string,
                PreviewDispatchRecord>::const_iterator existing =
                m_previewDispatches.find(dispatchKey);
            if (existing != m_previewDispatches.end())
            {
                if (existing->second.fingerprint != dispatchFingerprint)
                    return PreviewDispatchConflictResult(
                        command.context.toolCallId);
                if (!existing->second.complete)
                    return PreviewDispatchInFlightResult(
                        command.context.toolCallId);
                return ReplayPreviewDispatchResult(existing->second.result);
            }
            if (m_previewDispatches.size() >= kMaxPreviewDispatchRecords)
            {
                // Replay witnesses are a bounded optimization, not durable
                // state. Evict the earliest completed witness when full so a
                // burst of unique command ids cannot deny new mutations. Keep
                // every in-flight witness until its authority call returns;
                // evicting one would permit a concurrent double dispatch.
                std::unordered_map<std::string,
                    PreviewDispatchRecord>::iterator oldest =
                    m_previewDispatches.end();
                for (std::unordered_map<std::string,
                         PreviewDispatchRecord>::iterator it =
                         m_previewDispatches.begin();
                     it != m_previewDispatches.end(); ++it)
                {
                    if (!it->second.complete) continue;
                    if (oldest == m_previewDispatches.end() ||
                        it->second.steadyExpiresAt <
                            oldest->second.steadyExpiresAt)
                        oldest = it;
                }
                if (oldest != m_previewDispatches.end())
                    m_previewDispatches.erase(oldest);
                else
                {
                    result.status = ExecutionCommandStatus::Rejected;
                    result.reasonCode =
                        "EXECUTION_PREVIEW_DISPATCH_CAPACITY_EXHAUSTED";
                    result.detail =
                        "too many preview mutations are currently in flight or retained for replay";
                    return result;
                }
            }
            PreviewDispatchRecord dispatch;
            dispatch.ownerKey = FlattenDispatchOwnerKey(command);
            dispatch.fingerprint = dispatchFingerprint;
            dispatch.flatten = true;
            dispatch.complete = false;
            dispatch.steadyExpiresAt = steadyNow +
                kPreviewDispatchReplayTtl;
            m_previewDispatches[dispatchKey] = dispatch;
        }
        authorized.previewPermit.clear();
        ExecutionCommandResult replay;
        try
        {
            replay = m_authority.FlattenPosition(authorized);
        }
        catch (const std::exception&)
        {
            replay.status = ExecutionCommandStatus::Uncertain;
            replay.commandId = command.context.toolCallId;
            replay.reasonCode = "EXECUTION_AUTHORITY_EXCEPTION";
            // Authority exception text is process-local and may contain
            // credentials, paths or adapter diagnostics.  Replay responses
            // cross the Unix IPC boundary, so keep a stable detail only.
            replay.detail = "execution authority outcome is uncertain";
        }
        catch (...)
        {
            replay.status = ExecutionCommandStatus::Uncertain;
            replay.commandId = command.context.toolCallId;
            replay.reasonCode = "EXECUTION_AUTHORITY_EXCEPTION";
            replay.detail = "execution authority outcome is uncertain";
        }
        if (replay.commandId.empty())
            replay.commandId = command.context.toolCallId;
        {
            std::lock_guard<std::mutex> lock(m_previewMutex);
            const std::chrono::steady_clock::time_point steadyNow =
                std::chrono::steady_clock::now();
            for (std::unordered_map<std::string,
                     PreviewDispatchRecord>::iterator it =
                     m_previewDispatches.begin();
                 it != m_previewDispatches.end();)
            {
                if (it->second.complete &&
                    it->second.steadyExpiresAt <= steadyNow)
                    it = m_previewDispatches.erase(it);
                else
                    ++it;
            }
            const std::unordered_map<std::string,
                PreviewDispatchRecord>::iterator claimed =
                m_previewDispatches.find(dispatchKey);
            if (claimed != m_previewDispatches.end() &&
                claimed->second.fingerprint == dispatchFingerprint)
            {
                claimed->second.complete = true;
                claimed->second.result = replay;
                claimed->second.permit.clear();
                claimed->second.steadyExpiresAt = steadyNow +
                    kPreviewDispatchReplayTtl;
            }
        }
        return replay;
    }

    std::string reason;
    // Keep permit validation non-destructive until the independent mutation
    // lease has been granted.  A transient lease failure must not strand a
    // still-valid flatten preview.
    if (!ValidateFlattenPreviewPermit(authorized, reason))
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

    {
        std::lock_guard<std::mutex> lock(m_previewMutex);
        const std::chrono::steady_clock::time_point steadyNow =
            std::chrono::steady_clock::now();
        for (std::unordered_map<std::string,
                 PreviewDispatchRecord>::iterator it =
                 m_previewDispatches.begin();
             it != m_previewDispatches.end();)
        {
            if (it->second.complete &&
                it->second.steadyExpiresAt <= steadyNow)
                it = m_previewDispatches.erase(it);
            else
                ++it;
        }
        const std::unordered_map<std::string,
            PreviewDispatchRecord>::const_iterator existing =
            m_previewDispatches.find(dispatchKey);
        if (existing != m_previewDispatches.end())
        {
            if (existing->second.fingerprint != dispatchFingerprint)
                return PreviewDispatchConflictResult(command.context.toolCallId);
            if (!existing->second.complete)
                return PreviewDispatchInFlightResult(command.context.toolCallId);
            return ReplayPreviewDispatchResult(existing->second.result);
        }
        if (m_previewDispatches.size() >= kMaxPreviewDispatchRecords)
        {
            std::unordered_map<std::string,
                PreviewDispatchRecord>::iterator oldest =
                m_previewDispatches.end();
            for (std::unordered_map<std::string,
                     PreviewDispatchRecord>::iterator it =
                     m_previewDispatches.begin();
                 it != m_previewDispatches.end(); ++it)
            {
                if (!it->second.complete) continue;
                if (oldest == m_previewDispatches.end() ||
                    it->second.steadyExpiresAt <
                        oldest->second.steadyExpiresAt)
                    oldest = it;
            }
            if (oldest != m_previewDispatches.end())
                m_previewDispatches.erase(oldest);
            else
            {
                result.status = ExecutionCommandStatus::Rejected;
                result.reasonCode =
                    "EXECUTION_PREVIEW_DISPATCH_CAPACITY_EXHAUSTED";
                result.detail =
                    "too many preview mutations are currently in flight or retained for replay";
                return result;
            }
        }
        PreviewDispatchRecord dispatch;
        dispatch.ownerKey = FlattenDispatchOwnerKey(command);
        dispatch.fingerprint = dispatchFingerprint;
        dispatch.permit = command.previewPermit;
        dispatch.flatten = true;
        dispatch.complete = false;
        dispatch.steadyExpiresAt = steadyNow +
            std::chrono::milliseconds(1);
        m_previewDispatches[dispatchKey] = dispatch;
    }

    // Revalidate while holding the permit-store lock and consume exactly once
    // after all pre-dispatch gates pass.  A concurrent winner is rejected
    // without sending a second venue command.
    if (!ConsumeFlattenPreviewPermit(authorized, reason))
    {
        std::lock_guard<std::mutex> lock(m_previewMutex);
        const std::unordered_map<std::string,
            PreviewDispatchRecord>::iterator claimed =
            m_previewDispatches.find(dispatchKey);
        if (claimed != m_previewDispatches.end() &&
            !claimed->second.complete &&
            claimed->second.fingerprint == dispatchFingerprint)
            m_previewDispatches.erase(claimed);
        result.reasonCode = reason;
        result.detail =
            "Execution Service rejected the flatten preview permit after lease validation";
        return result;
    }
    authorized.previewPermit.clear();
    try
    {
        result = m_authority.FlattenPosition(authorized);
    }
    catch (const std::exception&)
    {
        result.status = ExecutionCommandStatus::Uncertain;
        result.commandId = command.context.toolCallId;
        result.reasonCode = "EXECUTION_AUTHORITY_EXCEPTION";
        // Do not expose raw authority exception text to an Agent/MCP peer.
        result.detail = "execution authority outcome is uncertain";
    }
    catch (...)
    {
        result.status = ExecutionCommandStatus::Uncertain;
        result.commandId = command.context.toolCallId;
        result.reasonCode = "EXECUTION_AUTHORITY_EXCEPTION";
        result.detail = "execution authority outcome is uncertain";
    }
    if (result.commandId.empty()) result.commandId = command.context.toolCallId;

    {
        std::lock_guard<std::mutex> lock(m_previewMutex);
        const std::unordered_map<std::string,
            PreviewDispatchRecord>::iterator claimed =
            m_previewDispatches.find(dispatchKey);
        if (claimed != m_previewDispatches.end() &&
            claimed->second.fingerprint == dispatchFingerprint)
        {
            // The permit was consumed before authority dispatch. Preserve a
            // terminal Rejected/Error response too; the caller can replay the
            // deterministic outcome without sending a second flatten command.
            if (ShouldRetainPreviewDispatch(result))
            {
                claimed->second.complete = true;
                claimed->second.result = result;
                claimed->second.permit.clear();
                claimed->second.steadyExpiresAt =
                    std::chrono::steady_clock::now() +
                    kPreviewDispatchReplayTtl;
            }
        }
    }
    return result;
}

ExecutionCommandResult UnixExecutionServiceServer::DispatchFlattenPreview(
    const FlattenPositionCommand& command)
{
    ExecutionCommandResult result;
    result.status = ExecutionCommandStatus::Rejected;
    result.commandId = command.context.toolCallId;
    if (m_readAuthority == nullptr)
    {
        result.reasonCode = "EXECUTION_FLATTEN_PREVIEW_UNAVAILABLE";
        return result;
    }
    result = m_readAuthority->PreviewFlattenPosition(command);
    if (result.status != ExecutionCommandStatus::Accepted) return result;
    if (!result.detail.empty() && !HeptaExecutionServiceInternal::ValidPreviewJson(
            result.detail))
    {
        result.status = ExecutionCommandStatus::Rejected;
        result.commandId = command.context.toolCallId;
        result.reasonCode = "EXECUTION_PREVIEW_RESPONSE_INVALID";
        result.detail = "execution authority response was invalid";
        return result;
    }

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
    payload.imbue(std::locale::classic());
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
