#include "execution_coordinator.h"

#include <exception>

ExecutionCommandResult ExecutionCoordinator::CancelOrder(const CancelOrderCommand& command)
{
    std::lock_guard<std::mutex> lock(m_mutex);
    const AgentExecutionContext& context = command.context;

    if (context.toolCallId.empty() || context.agentId.empty() || context.sessionId.empty())
        return RejectLocked(context, "INVALID_AGENT_CONTEXT", "agent_id, session_id and tool_call_id are required", command.orderId);
    const std::string requestHash = CancelRequestHash(command);
    if (requestHash.empty())
        return RejectLocked(context, "REQUEST_HASH_FAILED", "canonical request hashing failed", command.orderId);
    const std::string requestKey = RequestKey(context.agentId, context.sessionId, context.toolCallId);
    const std::unordered_map<std::string, RequestRecord>::const_iterator existing =
        m_requests.find(requestKey);
    if (existing != m_requests.end())
    {
        if (!existing->second.requestHash.empty() && existing->second.requestHash != requestHash)
            return IdempotencyConflictLocked(context, existing->second.orderId);
        return DuplicateResultLocked(context);
    }
    if (m_fencedSessionOwners.find(OwnerKey(context.agentId, context.sessionId)) !=
        m_fencedSessionOwners.end())
        return RejectLocked(context, "SESSION_OWNER_FENCED", "revoked or expired session owner cannot mutate",
                            command.orderId, requestHash);
    if (m_mutationBlocked)
        return RejectLocked(context, "MUTATION_BLOCKED", m_mutationBlockReason, command.orderId, requestHash);
    if (command.orderId < 0 || !m_callbacks.cancelIbOrder)
        return RejectLocked(context, "INVALID_CANCEL", "valid order_id and cancel callback are required",
                            command.orderId, requestHash);

    const std::unordered_map<long, ExecutionOrderOwner>::const_iterator ownerIt = m_orderOwners.find(command.orderId);
    if (!context.allowCancelAny)
    {
        if (ownerIt == m_orderOwners.end())
            return RejectLocked(context, "ORDER_OWNER_UNKNOWN", "order is not owned by this coordinator",
                                command.orderId, requestHash);
        if (ownerIt->second.agentId != context.agentId ||
            ownerIt->second.sessionId != context.sessionId ||
            ownerIt->second.account != context.account ||
            ownerIt->second.executionDomain != context.executionDomain)
            return RejectLocked(context, "ORDER_OWNER_MISMATCH", "agent cannot cancel another agent's order",
                                command.orderId, requestHash);
    }

    std::string suppressReason;
    bool cancelAllowed = true;
    if (m_callbacks.canCancelIbOrder)
    {
        try
        {
            cancelAllowed = m_callbacks.canCancelIbOrder(
                command.orderId, &suppressReason);
        }
        catch (const std::exception& error)
        {
            cancelAllowed = false;
            suppressReason = error.what();
        }
        catch (...)
        {
            cancelAllowed = false;
            suppressReason = "cancel safety callback threw";
        }
    }
    // A locally accepted order can legitimately be cancelled before IB emits
    // Submitted/OpenOrder.  The IB adapter records a pending cancel and
    // dispatches it on acknowledgement; all other guard failures (including a
    // callback exception) remain fail-closed.
    if (!cancelAllowed && suppressReason != "NO_BROKER_ACK")
    {
        if (suppressReason.empty())
            suppressReason = "cancel safety state is uncertain";
        return RejectLocked(context, "IB_CANCEL_SUPPRESSED", suppressReason,
                            command.orderId, requestHash);
    }

    const std::string instrument = !command.instrument.empty() ? command.instrument :
        (ownerIt != m_orderOwners.end() ? ownerIt->second.instrument : "");
    const std::string side = !command.side.empty() ? command.side :
        (ownerIt != m_orderOwners.end() ? ownerIt->second.side : "");
    const OmsJournalEvent intent = BuildEvent(context, "cancel", command.orderId, instrument,
                                              side, 0.0, 0.0, "intent_recorded", "", "", requestHash);
    if (!AppendOrBlockLocked(intent, "OMS_CANCEL_INTENT_WRITE_FAILED"))
        return RejectLocked(context, "OMS_CANCEL_INTENT_WRITE_FAILED", "cancel was not sent",
                            command.orderId, requestHash);

    RequestRecord pending;
    pending.status = ExecutionCommandStatus::Uncertain;
    pending.orderId = command.orderId;
    pending.reasonCode = "BROKER_RESULT_PENDING";
    pending.requestHash = requestHash;
    pending.operation = "cancel";
    pending.context = context;
    pending.instrument = instrument;
    pending.side = side;
    pending.durableMutationIntent = true;
    m_requests[requestKey] = pending;

    // Persist the cancel send boundary before broker I/O. A crash after this
    // record must never cause an automatic second cancel request.
    const OmsJournalEvent sendAttempt = BuildEvent(
        context, "cancel_send_attempt", command.orderId, instrument, side,
        0.0, 0.0, "attempt_recorded", "", "", requestHash);
    if (!AppendOrBlockLocked(sendAttempt, "OMS_CANCEL_SEND_ATTEMPT_WRITE_FAILED"))
        return RejectLocked(context, "OMS_CANCEL_SEND_ATTEMPT_WRITE_FAILED",
                            "cancel was not sent", command.orderId, requestHash);

    std::string rejectReason;
    const bool cancelled = TryCancelAtVenueLocked(
        command.orderId, rejectReason);

    if (cancelled && rejectReason == "IB_CANCEL_DEFERRED_UNTIL_BROKER_ACK")
        return HandleDeferredCancelLocked(command, context, instrument, side,
                                          requestHash, requestKey, pending);

    if (!cancelled)
    {
        if (rejectReason.empty()) rejectReason = "IB adapter rejected cancel";
        const OmsJournalEvent reject = BuildEvent(context, "reject", command.orderId, instrument,
                                                  side, 0.0, 0.0, "rejected", rejectReason,
                                                  "IB_CANCEL_REJECT", requestHash);
        AppendOrBlockLocked(reject, "OMS_CANCEL_REJECT_WRITE_FAILED");
        return RejectLocked(context, "IB_CANCEL_REJECT", rejectReason, command.orderId, requestHash);
    }

    const OmsJournalEvent sent = BuildEvent(context, "cancel", command.orderId, instrument,
                                            side, 0.0, 0.0, "cancel_sent", "", "", requestHash);
    if (!AppendOrBlockLocked(sent, "OMS_CANCEL_RECEIPT_WRITE_FAILED"))
    {
        RequestRecord record;
        record.status = ExecutionCommandStatus::Uncertain;
        record.orderId = command.orderId;
        record.reasonCode = "OMS_CANCEL_RECEIPT_WRITE_FAILED";
        record.requestHash = requestHash;
        record.operation = "cancel";
        record.context = context;
        record.instrument = instrument;
        record.side = side;
        record.durableMutationIntent = true;
        m_requests[requestKey] = record;

        ExecutionCommandResult result;
        result.status = ExecutionCommandStatus::Uncertain;
        result.commandId = context.toolCallId;
        result.orderId = command.orderId;
        result.reasonCode = record.reasonCode;
        result.detail = "cancel may have reached broker; reconciliation required";
        return result;
    }

    bool projectionOk = true;
    std::string projectionReason;
    if (m_callbacks.onIbCancelSent)
    {
        try
        {
            projectionOk = m_callbacks.onIbCancelSent(command, &projectionReason);
        }
        catch (const std::exception& ex)
        {
            projectionOk = false;
            projectionReason = ex.what();
        }
        catch (...)
        {
            projectionOk = false;
            projectionReason = "unknown cancel projection exception";
        }
    }

    if (!projectionOk)
        return HandleCancelProjectionFailureLocked(
            command, instrument, side, requestHash, requestKey,
            projectionReason);

    RequestRecord record;
    record.status = ExecutionCommandStatus::Accepted;
    record.orderId = command.orderId;
    record.requestHash = requestHash;
    record.operation = "cancel";
    record.context = context;
    record.instrument = instrument;
    record.side = side;
    record.durableMutationIntent = true;
    m_requests[requestKey] = record;

    ExecutionCommandResult result;
    result.status = ExecutionCommandStatus::Accepted;
    result.commandId = context.toolCallId;
    result.orderId = command.orderId;
    return result;
}
