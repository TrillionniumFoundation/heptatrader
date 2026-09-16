#include "execution_coordinator.h"

#include <exception>

ExecutionCommandResult ExecutionCoordinator::CancelOrder(const CancelOrderCommand& command)
{
    return ObserveCommand(1U, [&]() { return CancelOrderLocked(command); });
}

ExecutionCommandResult ExecutionCoordinator::CancelOrderLocked(const CancelOrderCommand& command)
{
    const AgentExecutionContext& context = command.context;

    if (context.toolCallId.empty() || context.agentId.empty() || context.sessionId.empty())
        return RefuseBeforeIntent(context, "INVALID_AGENT_CONTEXT", "agent_id, session_id and tool_call_id are required", command.orderId);
    const std::string requestHash = CancelRequestHash(command);
    if (requestHash.empty())
        return RefuseBeforeIntent(context, "REQUEST_HASH_FAILED", "canonical request hashing failed", command.orderId);
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
        return RefuseBeforeIntent(context, "SESSION_OWNER_FENCED", "revoked or expired session owner cannot mutate",
                            command.orderId);
    if (m_mutationBlocked)
        return RefuseBeforeIntent(context, "MUTATION_BLOCKED", m_mutationBlockReason, command.orderId);
    if (command.orderId < 0 || !m_callbacks.cancelOrder)
        return RefuseBeforeIntent(context, "INVALID_CANCEL", "valid order_id and cancel callback are required",
                            command.orderId);

    const std::unordered_map<long, ExecutionOrderOwner>::const_iterator ownerIt = m_orderOwners.find(command.orderId);
    if (!context.allowCancelAny)
    {
        if (ownerIt == m_orderOwners.end())
            return RefuseBeforeIntent(context, "ORDER_OWNER_UNKNOWN", "order is not owned by this coordinator",
                                command.orderId);
        if (ownerIt->second.agentId != context.agentId ||
            ownerIt->second.sessionId != context.sessionId ||
            ownerIt->second.account != context.account ||
            ownerIt->second.executionDomain != context.executionDomain)
            return RefuseBeforeIntent(context, "ORDER_OWNER_MISMATCH", "agent cannot cancel another agent's order",
                                command.orderId);
    }

    std::string suppressReason;
    if (m_callbacks.canCancelIbOrder &&
        !m_callbacks.canCancelIbOrder(command.orderId, &suppressReason) &&
        // A locally accepted order can legitimately be cancelled before IB
        // emits Submitted/OpenOrder.  The IB adapter records a pending cancel
        // and dispatches it on acknowledgement; all other guard failures
        // remain fail-closed.
        suppressReason != "NO_BROKER_ACK")
        return RefuseBeforeIntent(context, "IB_CANCEL_SUPPRESSED", suppressReason, command.orderId);

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

    const VenueCancelResult outcome = TryCancelAtVenueLocked(command.orderId);
    if (outcome.disposition == VenueCancelDisposition::Deferred)
        return HandleDeferredCancelLocked(command, context, instrument, side,
                                          requestHash, requestKey, pending);

    if (outcome.disposition == VenueCancelDisposition::RejectedBeforeSend &&
        !outcome.detail.empty())
    {
        const OmsJournalEvent reject = BuildEvent(context, "reject", command.orderId, instrument,
            side, 0.0, 0.0, "rejected", outcome.detail, "IB_CANCEL_REJECT", requestHash);
        AppendOrBlockLocked(reject, "OMS_CANCEL_REJECT_WRITE_FAILED");
        return RejectLocked(context, "IB_CANCEL_REJECT", outcome.detail,
                            command.orderId, requestHash);
    }
    if (outcome.disposition != VenueCancelDisposition::Submitted)
        return UncertainCancelOutcomeLocked(command, instrument, side,
            requestHash, requestKey, outcome.detail);

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

ExecutionCommandResult ExecutionCoordinator::UncertainCancelOutcomeLocked(
    const CancelOrderCommand& command, const std::string& instrument,
    const std::string& side, const std::string& requestHash,
    const std::string& requestKey, const std::string& detail)
{
    // Close admission before any diagnostic allocation. The existing durable
    // send-attempt plus pending identity survive even a later receipt failure.
    BlockMutationsLocked("RECOVERY_RECONCILE_REQUIRED");
    RequestRecord& record = m_requests.at(requestKey);
    record.status = ExecutionCommandStatus::Uncertain;
    record.reasonCode = "RECOVERY_RECONCILE_REQUIRED";
    record.detail = detail.empty() ? "cancel may have reached venue; reconciliation required" :
        detail.substr(0, 1024);
    // Reuse a supported critical record. Old readers already treat
    // cancel_pending/RECOVERY_RECONCILE_REQUIRED as unresolved, never rejected.
    const OmsJournalEvent uncertain = BuildEvent(command.context, "cancel", command.orderId,
        instrument, side, 0.0, 0.0, "cancel_pending", record.detail,
        "RECOVERY_RECONCILE_REQUIRED", requestHash);
    if (!AppendOrBlockLocked(uncertain, "OMS_CANCEL_UNCERTAIN_WRITE_FAILED"))
        record.reasonCode = "OMS_CANCEL_UNCERTAIN_WRITE_FAILED";
    ExecutionCommandResult result;
    result.status = ExecutionCommandStatus::Uncertain;
    result.commandId = command.context.toolCallId;
    result.orderId = command.orderId;
    result.reasonCode = record.reasonCode;
    result.detail = record.detail;
    return result;
}
