#include "execution_coordinator.h"
#include <exception>
#include <set>
ExecutionCommandResult
ExecutionCoordinator::UncertainPlaceOutcomeLocked(
    const IbPlaceOrderCommand& command,
    const PlaceOrderDispatchContext& dispatch,
    long orderId,
    const std::string& detail)
{
    const char* const code = "IB_PLACE_OUTCOME_UNCERTAIN";
    const OmsJournalEvent event = BuildEvent(
        command.context, "place_outcome_uncertain", orderId,
        dispatch.instrument, command.order.action,
        command.order.totalQuantity, dispatch.eventPrice, "uncertain",
        detail, code, dispatch.requestHash,
        dispatch.venueCorrelationId);
    const bool journaled = AppendOrBlockLocked(
        event, "OMS_PLACE_UNCERTAIN_WRITE_FAILED");
    RequestRecord& record = m_requests[dispatch.requestKey];
    record.status = ExecutionCommandStatus::Uncertain;
    record.orderId = orderId;
    record.reasonCode = journaled ? code :
        "OMS_PLACE_UNCERTAIN_WRITE_FAILED";
    record.detail = detail;
    if (journaled)
        BlockMutationsLocked("RECOVERY_RECONCILE_REQUIRED");
    ExecutionCommandResult result;
    result.status = ExecutionCommandStatus::Uncertain;
    result.commandId = command.context.toolCallId;
    result.orderId = orderId;
    result.reasonCode = record.reasonCode;
    result.detail = detail;
    return result;
}
ExecutionCommandResult
ExecutionCoordinator::CompletePlaceOrderLocked(
    const IbPlaceOrderCommand& command,
    const PlaceOrderDispatchContext& dispatch,
    long orderId)
{
    const AgentExecutionContext& context = command.context;
    ExecutionOrderOwner owner;
    owner.agentId = context.agentId;
    owner.sessionId = context.sessionId;
    owner.strategy = context.strategy;
    owner.account = context.account;
    owner.executionDomain = context.executionDomain;
    owner.instrument = dispatch.instrument;
    owner.side = command.order.action;
    m_orderOwners[orderId] = owner;
    // Track before receipt IO so watchdog coverage survives a write failure.
    if (m_callbacks.trackOrder)
        m_callbacks.trackOrder(
            context.venue.empty() ? "IB" : context.venue, orderId, "",
            dispatch.instrument, command.order.action, context.strategy);
    bool projectionOk = true;
    std::string projectionReason;
    if (m_callbacks.onIbOrderPlaced)
    {
        try
        {
            projectionOk = m_callbacks.onIbOrderPlaced(
                command, orderId, &projectionReason);
        }
        catch (const std::exception& error)
        {
            projectionOk = false;
            projectionReason = error.what();
        }
        catch (...)
        {
            projectionOk = false;
            projectionReason = "unknown order projection exception";
        }
    }
    const OmsJournalEvent sent = BuildEvent(
        context, "place_sent", orderId, dispatch.instrument,
        command.order.action, command.order.totalQuantity,
        dispatch.eventPrice, "submitted", "", "", dispatch.requestHash,
        dispatch.venueCorrelationId);
    if (!AppendOrBlockLocked(sent, "OMS_PLACE_RECEIPT_WRITE_FAILED"))
    {
        RequestRecord& record = m_requests[dispatch.requestKey];
        record.status = ExecutionCommandStatus::Uncertain;
        record.orderId = orderId;
        record.reasonCode = "OMS_PLACE_RECEIPT_WRITE_FAILED";
        ExecutionCommandResult result;
        result.status = ExecutionCommandStatus::Uncertain;
        result.commandId = context.toolCallId;
        result.orderId = orderId;
        result.reasonCode = record.reasonCode;
        result.detail =
            "broker send may have succeeded; reconciliation required";
        return result;
    }
    if (!projectionOk)
    {
        const char* const code = "AUTHORITATIVE_ORDER_PROJECTION_FAILED";
        const OmsJournalEvent failure = BuildEvent(
            context, "execution_projection_failed", orderId,
            dispatch.instrument, command.order.action,
            command.order.totalQuantity, dispatch.eventPrice,
            "place_projection_failed", projectionReason, code,
            dispatch.requestHash, dispatch.venueCorrelationId);
        if (AppendOrBlockLocked(
                failure,
                "OMS_EXECUTION_PROJECTION_FAILURE_WRITE_FAILED"))
            BlockMutationsLocked(code);
        RequestRecord& record = m_requests[dispatch.requestKey];
        record.status = ExecutionCommandStatus::Uncertain;
        record.orderId = orderId;
        record.reasonCode = code;
        record.detail = projectionReason;
        ExecutionCommandResult result;
        result.status = ExecutionCommandStatus::Uncertain;
        result.commandId = context.toolCallId;
        result.orderId = orderId;
        result.reasonCode = code;
        result.detail = projectionReason;
        return result;
    }
    RequestRecord& record = m_requests[dispatch.requestKey];
    record.status = ExecutionCommandStatus::Accepted;
    record.orderId = orderId;
    record.reasonCode.clear();
    record.detail.clear();
    ExecutionCommandResult result;
    result.status = ExecutionCommandStatus::Accepted;
    result.commandId = context.toolCallId;
    result.orderId = orderId;
    return result;
}
bool ExecutionCoordinator::PreVenuePlaceAllowedLocked(
    const IbPlaceOrderCommand& command,
    const PlaceOrderDispatchContext& dispatch,
    ExecutionCommandResult& rejection)
{
    if (!m_callbacks.preVenuePlaceCheck)
        return true;
    std::string reason;
    bool allowed = false;
    try
    {
        allowed = m_callbacks.preVenuePlaceCheck(command, &reason);
    }
    catch (const std::exception& error)
    {
        reason = error.what();
    }
    catch (...)
    {
        reason = "pre-venue place check threw";
    }
    if (allowed)
        return true;
    if (reason.empty())
        reason = "IB_PAPER_KILL_SWITCH_STATE_UNCERTAIN";
    const AgentExecutionContext& context = command.context;
    const OmsJournalEvent reject = BuildEvent(
        context, "reject", -1, dispatch.instrument, command.order.action,
        command.order.totalQuantity, dispatch.eventPrice, "rejected",
        "pre-venue risk-increase check rejected broker send", reason,
        dispatch.requestHash, dispatch.venueCorrelationId);
    AppendOrBlockLocked(reject, "OMS_REJECT_WRITE_FAILED");
    rejection = RejectLocked(
        context, reason,
        "pre-venue risk-increase check rejected broker send", -1,
        dispatch.requestHash);
    return false;
}

ExecutionCommandResult
ExecutionCoordinator::DispatchPlaceOrderLocked(
    const IbPlaceOrderCommand& command,
    const PlaceOrderDispatchContext& dispatch)
{
    const AgentExecutionContext& context = command.context;
    // This marker is immediately before venue IO and restores the rolling
    // send-attempt budget even after a crash between send and receipt.
    const OmsJournalEvent sendAttempt = BuildEvent(
        context, "place_send_attempt", -1, dispatch.instrument,
        command.order.action, command.order.totalQuantity,
        dispatch.eventPrice, "attempt_recorded", "", "",
        dispatch.requestHash, dispatch.venueCorrelationId);
    if (!AppendOrBlockLocked(
            sendAttempt, "OMS_PLACE_SEND_ATTEMPT_WRITE_FAILED"))
        return RejectLocked(
            context, "OMS_PLACE_SEND_ATTEMPT_WRITE_FAILED",
            "broker send was not attempted", -1, dispatch.requestHash);
    if (m_placeSendAttemptKeys.insert(dispatch.requestKey).second)
    {
        PlaceSendAttempt attempt;
        attempt.requestKey = dispatch.requestKey;
        attempt.account = context.account;
        attempt.executionDomain = context.executionDomain;
        attempt.tsMs = sendAttempt.tsMs;
        m_placeSendAttempts.push_back(attempt);
    }
    ExecutionCommandResult preVenueRejection;
    if (!PreVenuePlaceAllowedLocked(command, dispatch, preVenueRejection))
        return preVenueRejection;
    std::string rejectReason;
    long orderId = -1;
    bool placed = false;
    bool callbackThrew = false;
    try
    {
        if (m_callbacks.placeIbOrderCommandCorrelated)
            placed = m_callbacks.placeIbOrderCommandCorrelated(
                command, dispatch.venueCorrelationId, &orderId);
        else
            placed = m_callbacks.placeIbOrderCorrelated ?
                m_callbacks.placeIbOrderCorrelated(command.contract,
                    command.order, dispatch.venueCorrelationId, &orderId) :
                m_callbacks.placeIbOrder(command.contract, command.order,
                    &orderId);
    }
    catch (const std::exception& error)
    {
        callbackThrew = true;
        rejectReason = error.what();
    }
    catch (...)
    {
        callbackThrew = true;
        rejectReason = "unknown IB place exception";
    }
    if (callbackThrew)
        return UncertainPlaceOutcomeLocked(
            command, dispatch, orderId, rejectReason.empty() ?
                "IB place callback threw after dispatch" : rejectReason);
    if (!placed)
    {
        bool reliableReject = false;
        if (m_callbacks.lastIbRejectReason)
        {
            try
            {
                rejectReason = m_callbacks.lastIbRejectReason();
                reliableReject = !rejectReason.empty();
            }
            catch (const std::exception& error)
            {
                rejectReason = error.what();
            }
            catch (...)
            {
                rejectReason =
                    "IB place rejection reader threw";
            }
        }
        if (!reliableReject)
            return UncertainPlaceOutcomeLocked(
                command, dispatch, orderId, rejectReason.empty() ?
                    "adapter returned false without a reliable rejection reason" :
                    rejectReason);
        static const std::set<std::string> exactRejectCodes = {
            "IB_PAPER_KILL_SWITCH_ENGAGED", "IB_POST_FILL_RISK_REFRESH_PENDING",
            "IB_PAPER_KILL_SWITCH_STATE_UNCERTAIN",
            "IB_PAPER_PLACE_QUOTE_BINDING_REQUIRED", "IB_PAPER_PLACE_CONTRACT_MISMATCH",
            "IB_PAPER_PLACE_QUOTE_CHANGED_BEFORE_SEND"};
        const std::string rejectCode = exactRejectCodes.count(rejectReason) ?
            rejectReason : "IB_PLACE_REJECT";
        const OmsJournalEvent reject = BuildEvent(
            context, "reject", orderId, dispatch.instrument,
            command.order.action, command.order.totalQuantity,
            dispatch.eventPrice, "rejected", rejectReason, rejectCode,
            dispatch.requestHash, dispatch.venueCorrelationId);
        AppendOrBlockLocked(reject, "OMS_REJECT_WRITE_FAILED");
        return RejectLocked(
            context, rejectCode, rejectReason, orderId,
            dispatch.requestHash);
    }
    if (orderId < 0)
        return UncertainPlaceOutcomeLocked(
            command, dispatch, orderId,
            "adapter accepted place without an order id");
    return CompletePlaceOrderLocked(command, dispatch, orderId);
}
