#include "execution_coordinator.h"

#include <exception>

namespace
{
double FlattenAuditPrice(const AuthoritativeFlattenPlan& plan)
{
    return plan.order.lmtPrice > 0.0 ?
        plan.order.lmtPrice : plan.referencePrice;
}

bool IsCanonicalFlattenVenueRejectCode(const std::string& value)
{
    static const char* const codes[] = {
        "IB_PAPER_FLATTEN_QUOTE_CHANGED_BEFORE_SEND",
        "IB_FLATTEN_POSITION_SNAPSHOT_MISMATCH",
        "IB_FLATTEN_POSITION_CHANGED_BEFORE_SEND",
        "IB_FLATTEN_POSITION_CHANGED_BEFORE_NOOP",
        "IB_FLATTEN_ACTIVE_ORDER_SNAPSHOT_UNSAFE",
        "IB_FLATTEN_NOT_EXACT_REDUCE_ONLY",
        "IB_PAPER_KILL_SWITCH_ENGAGED",
        "IB_PAPER_KILL_SWITCH_STATE_UNCERTAIN",
        "IB_PAPER_BROKER_CONNECTION_CLOSED",
        "IB_PAPER_EVENT_STREAM_OVERFLOW",
        "IB_PAPER_RUNTIME_FATAL",
        "IB_PAPER_RUNTIME_NOT_READY",
    };
    for (const char* const code : codes)
        if (value == code) return true;
    return false;
}
}

ExecutionCommandResult
ExecutionCoordinator::RejectAuthoritativeFlattenLocked(
    const FlattenPositionCommand& command,
    const AuthoritativeFlattenPlan& plan,
    const AuthoritativeFlattenDispatchContext& dispatch,
    const std::string& reasonCode,
    const std::string& detail)
{
    const OmsJournalEvent event = BuildEvent(
        command.context, "flatten_reject", -1, command.instrument,
        plan.order.action, plan.order.totalQuantity, FlattenAuditPrice(plan),
        "rejected", detail, reasonCode, dispatch.requestHash,
        dispatch.venueCorrelationId);
    if (!AppendOrBlockLocked(
            event, "OMS_FLATTEN_REJECT_WRITE_FAILED"))
        return RejectLocked(
            command.context, "OMS_FLATTEN_REJECT_WRITE_FAILED",
            "flatten rejection audit failed", -1,
            dispatch.requestHash);
    const ExecutionCommandResult rejected = RejectLocked(
        command.context, reasonCode, detail, -1,
        dispatch.requestHash);
    RequestRecord& record = m_requests[dispatch.requestKey];
    record.venueCorrelationId = dispatch.venueCorrelationId;
    record.operation = "flatten";
    record.context = command.context;
    record.instrument = command.instrument;
    record.side = plan.order.action;
    record.quantity = plan.order.totalQuantity;
    record.price = FlattenAuditPrice(plan);
    return rejected;
}

ExecutionCommandResult
ExecutionCoordinator::UncertainAuthoritativeFlattenLocked(
    const FlattenPositionCommand& command,
    const AuthoritativeFlattenPlan& plan,
    const AuthoritativeFlattenDispatchContext& dispatch,
    long orderId,
    const std::string& detail)
{
    const char* const code = "IB_FLATTEN_OUTCOME_UNCERTAIN";
    const OmsJournalEvent event = BuildEvent(
        command.context, "flatten_outcome_uncertain", orderId,
        command.instrument, plan.order.action,
        plan.order.totalQuantity, FlattenAuditPrice(plan), "uncertain",
        detail, code, dispatch.requestHash,
        dispatch.venueCorrelationId);
    const bool journaled = AppendOrBlockLocked(
        event, "OMS_FLATTEN_UNCERTAIN_WRITE_FAILED");
    RequestRecord& record = m_requests[dispatch.requestKey];
    record.status = ExecutionCommandStatus::Uncertain;
    record.orderId = orderId;
    record.reasonCode = journaled ? code :
        "OMS_FLATTEN_UNCERTAIN_WRITE_FAILED";
    record.detail = detail;
    if (journaled)
        BlockMutationsLocked("RECOVERY_RECONCILE_REQUIRED");

    ExecutionCommandResult uncertain;
    uncertain.status = ExecutionCommandStatus::Uncertain;
    uncertain.commandId = command.context.toolCallId;
    uncertain.orderId = orderId;
    uncertain.reasonCode = record.reasonCode;
    uncertain.detail = detail;
    return uncertain;
}

ExecutionCommandResult
ExecutionCoordinator::CompleteAuthoritativeFlattenLocked(
    const FlattenPositionCommand& command,
    const AuthoritativeFlattenPlan& plan,
    const AuthoritativeFlattenDispatchContext& dispatch,
    long orderId)
{
    const AgentExecutionContext& context = command.context;
    ExecutionOrderOwner owner;
    owner.agentId = context.agentId;
    owner.sessionId = context.sessionId;
    owner.strategy = context.strategy;
    owner.account = context.account;
    owner.executionDomain = context.executionDomain;
    owner.instrument = command.instrument;
    owner.side = plan.order.action;
    m_orderOwners[orderId] = owner;
    if (m_callbacks.trackOrder)
        m_callbacks.trackOrder(
            context.venue.empty() ? "IB" : context.venue, orderId, "",
            command.instrument, plan.order.action, context.strategy);

    bool projectionOk = true;
    std::string projectionReason;
    if (m_callbacks.onIbOrderPlaced)
    {
        PlaceOrderCommand projected;
        projected.context = context;
        projected.contract = plan.contract;
        projected.order = plan.order;
        projected.instrument = plan.instrument;
        projected.timeInForce = plan.timeInForce;
        projected.referencePrice = plan.referencePrice;
        try
        {
            projectionOk = m_callbacks.onIbOrderPlaced(
                projected, orderId, &projectionReason);
        }
        catch (const std::exception& error)
        {
            projectionOk = false;
            projectionReason = error.what();
        }
        catch (...)
        {
            projectionOk = false;
            projectionReason =
                "unknown flatten projection exception";
        }
    }

    const OmsJournalEvent sent = BuildEvent(
        context, "flatten_sent", orderId, command.instrument,
        plan.order.action, plan.order.totalQuantity,
        FlattenAuditPrice(plan), "submitted", dispatch.snapshotEvidence, "",
        dispatch.requestHash, dispatch.venueCorrelationId);
    if (!AppendOrBlockLocked(
            sent, "OMS_FLATTEN_RECEIPT_WRITE_FAILED"))
    {
        RequestRecord& record = m_requests[dispatch.requestKey];
        record.status = ExecutionCommandStatus::Uncertain;
        record.orderId = orderId;
        record.reasonCode = "OMS_FLATTEN_RECEIPT_WRITE_FAILED";
        ExecutionCommandResult uncertain;
        uncertain.status = ExecutionCommandStatus::Uncertain;
        uncertain.commandId = context.toolCallId;
        uncertain.orderId = orderId;
        uncertain.reasonCode = record.reasonCode;
        uncertain.detail =
            "flatten broker send may have succeeded; reconciliation required";
        return uncertain;
    }
    if (!projectionOk)
    {
        const char* const code =
            "AUTHORITATIVE_FLATTEN_PROJECTION_FAILED";
        const OmsJournalEvent failure = BuildEvent(
            context, "execution_projection_failed", orderId,
            command.instrument, plan.order.action,
            plan.order.totalQuantity, FlattenAuditPrice(plan),
            "flatten_projection_failed", projectionReason, code,
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
        ExecutionCommandResult uncertain;
        uncertain.status = ExecutionCommandStatus::Uncertain;
        uncertain.commandId = context.toolCallId;
        uncertain.orderId = orderId;
        uncertain.reasonCode = code;
        uncertain.detail = projectionReason;
        return uncertain;
    }

    RequestRecord& record = m_requests[dispatch.requestKey];
    record.status = ExecutionCommandStatus::Accepted;
    record.orderId = orderId;
    record.reasonCode.clear();
    record.detail.clear();
    ExecutionCommandResult accepted;
    accepted.status = ExecutionCommandStatus::Accepted;
    accepted.commandId = context.toolCallId;
    accepted.orderId = orderId;
    return accepted;
}

ExecutionCommandResult
ExecutionCoordinator::DispatchAuthoritativeFlattenLocked(
    const FlattenPositionCommand& command,
    const AuthoritativeFlattenPlan& plan,
    const AuthoritativeFlattenDispatchContext& dispatch)
{
    const AgentExecutionContext& context = command.context;
    if (plan.expectedPositionQuantity == 0.0)
        return CompleteAuthoritativeFlattenNoopLocked(
            command, plan, dispatch);
    if (!m_callbacks.placeIbReduceOnlyOrderCorrelated)
        return RejectAuthoritativeFlattenLocked(
            command, plan, dispatch, "IB_FLATTEN_CALLBACK_MISSING",
            "authoritative reduce-only venue callback is not configured");

    const OmsJournalEvent sendAttempt = BuildEvent(
        context, "flatten_send_attempt", -1, command.instrument,
        plan.order.action, plan.order.totalQuantity, FlattenAuditPrice(plan),
        "attempt_recorded", dispatch.snapshotEvidence, "",
        dispatch.requestHash, dispatch.venueCorrelationId);
    if (!AppendOrBlockLocked(
            sendAttempt, "OMS_FLATTEN_SEND_ATTEMPT_WRITE_FAILED"))
        return RejectLocked(
            context, "OMS_FLATTEN_SEND_ATTEMPT_WRITE_FAILED",
            "flatten broker send was not attempted", -1,
            dispatch.requestHash);
    if (m_placeSendAttemptKeys.insert(dispatch.requestKey).second)
    {
        PlaceSendAttempt attempt;
        attempt.requestKey = dispatch.requestKey;
        attempt.account = context.account;
        attempt.executionDomain = context.executionDomain;
        attempt.tsMs = sendAttempt.tsMs;
        m_placeSendAttempts.push_back(attempt);
    }

    std::string venueReason;
    if (m_callbacks.preVenueFlattenCheck)
    {
        bool allowed = false;
        try
        {
            allowed = m_callbacks.preVenueFlattenCheck(
                command, plan, &venueReason);
        }
        catch (const std::exception& error)
        {
            venueReason = error.what();
        }
        catch (...)
        {
            venueReason = "pre-venue flatten check threw";
        }
        if (!allowed)
        {
            if (venueReason.empty())
                venueReason =
                    "IB_PAPER_KILL_SWITCH_STATE_UNCERTAIN";
            return RejectAuthoritativeFlattenLocked(
                command, plan, dispatch, venueReason,
                "pre-venue flatten safety check rejected broker send");
        }
    }

    long orderId = -1;
    bool placed = false;
    bool callbackThrew = false;
    try
    {
        placed = m_callbacks.placeIbReduceOnlyOrderCorrelated(
            plan, dispatch.venueCorrelationId, &orderId);
    }
    catch (const std::exception& error)
    {
        callbackThrew = true;
        venueReason = error.what();
    }
    catch (...)
    {
        callbackThrew = true;
        venueReason = "unknown authoritative flatten exception";
    }
    if (callbackThrew)
        return UncertainAuthoritativeFlattenLocked(
            command, plan, dispatch, orderId,
            venueReason.empty() ?
                "authoritative flatten callback threw after dispatch" :
                venueReason);
    if (!placed)
    {
        bool reliableReject = false;
        if (m_callbacks.lastIbRejectReason)
        {
            try
            {
                venueReason = m_callbacks.lastIbRejectReason();
                reliableReject = !venueReason.empty();
            }
            catch (const std::exception& error)
            {
                venueReason = error.what();
            }
            catch (...)
            {
                venueReason =
                    "authoritative flatten rejection reader threw";
            }
        }
        if (reliableReject)
            return RejectAuthoritativeFlattenLocked(
                command, plan, dispatch,
                IsCanonicalFlattenVenueRejectCode(venueReason) ?
                    venueReason : "IB_FLATTEN_REJECT",
                venueReason);
        return UncertainAuthoritativeFlattenLocked(
            command, plan, dispatch, orderId,
            venueReason.empty() ?
                "adapter returned false without a reliable rejection reason" :
                venueReason);
    }
    if (orderId < 0)
        return UncertainAuthoritativeFlattenLocked(
            command, plan, dispatch, orderId,
            "adapter accepted authoritative flatten without an order id");
    return CompleteAuthoritativeFlattenLocked(
        command, plan, dispatch, orderId);
}
