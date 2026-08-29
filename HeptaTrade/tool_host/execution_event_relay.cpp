#include "execution_event_relay.h"

namespace
{
bool ValidIdentity(const ExecutionServiceIdentity& identity)
{
    return !identity.serviceEpoch.empty() && identity.serviceEpoch.size() <= 128 &&
        identity.serviceFencingGeneration != 0;
}

bool SameIdentity(const ExecutionServiceIdentity& left,
                  const ExecutionServiceIdentity& right)
{
    return left.serviceEpoch == right.serviceEpoch &&
        left.serviceFencingGeneration == right.serviceFencingGeneration;
}
}

ExecutionEventRelay::ExecutionEventRelay(ExecutionEventHub& localHub,
                                         const Reader& reader)
    : m_localHub(localHub), m_reader(reader)
{
}

std::uint64_t ExecutionEventRelay::PublishControlEvent(
    const ExecutionEventRelayOwner& owner,
    const std::string& type,
    const std::string& reasonCode,
    const ExecutionEventReadResult& upstream)
{
    ExecutionEvent event;
    event.executionDomain = owner.executionDomain;
    event.agentId = owner.agentId;
    event.sessionId = owner.sessionId;
    event.type = type;
    event.venue = "EXECUTION_SERVICE";
    event.status = "AuthoritativeResyncRequired";
    event.reasonCode = reasonCode;
    event.upstreamServiceEpoch = upstream.serviceIdentity.serviceEpoch;
    event.upstreamServiceFencingGeneration =
        upstream.serviceIdentity.serviceFencingGeneration;
    event.upstreamStreamEpoch = upstream.streamEpoch;
    event.upstreamSequence = upstream.droppedThroughSequence;
    return m_localHub.Publish(event);
}

ExecutionEventRelayStatus ExecutionEventRelay::Poll(
    const ExecutionEventRelayOwner& owner,
    ExecutionEventRelayCursor& cursor,
    int timeoutMs,
    std::string& reason)
{
    if (owner.executionDomain.empty() || owner.agentId.empty() || owner.sessionId.empty() ||
        !ValidIdentity(owner.serviceIdentity) ||
        timeoutMs < 0 || timeoutMs > 30000 || !m_reader)
    {
        reason = "EXECUTION_EVENT_RELAY_INVALID_REQUEST";
        return ExecutionEventRelayStatus::InvalidOwner;
    }

    if (!ValidIdentity(cursor.upstreamServiceIdentity))
    {
        cursor.upstreamServiceIdentity = owner.serviceIdentity;
        cursor.upstreamEpoch = owner.serviceIdentity.serviceEpoch;
        cursor.upstreamSequence = 0;
    }
    else if (!SameIdentity(cursor.upstreamServiceIdentity, owner.serviceIdentity))
    {
        ExecutionEventReadResult changed;
        changed.status = ExecutionEventReadStatus::ServiceIdentityMismatch;
        changed.serviceIdentity = owner.serviceIdentity;
        changed.streamEpoch = owner.serviceIdentity.serviceEpoch;
        cursor.upstreamServiceIdentity = owner.serviceIdentity;
        cursor.upstreamEpoch = owner.serviceIdentity.serviceEpoch;
        cursor.upstreamSequence = 0;
        cursor.authoritativeResyncRequired = true;
        PublishControlEvent(owner, "system.execution_service_identity_changed",
            "EXECUTION_EVENT_SERVICE_IDENTITY_CHANGED", changed);
        reason = "EXECUTION_EVENT_SERVICE_IDENTITY_CHANGED";
        return ExecutionEventRelayStatus::ServiceIdentityChanged;
    }
    if (cursor.authoritativeResyncRequired)
    {
        reason = "EXECUTION_EVENT_AUTHORITATIVE_RESYNC_REQUIRED";
        return ExecutionEventRelayStatus::ResyncRequired;
    }

    ExecutionEventFeedRequest request;
    request.operation = ExecutionEventFeedOperation::Wait;
    request.executionDomain = owner.executionDomain;
    request.agentId = owner.agentId;
    request.sessionId = owner.sessionId;
    request.expectedServiceIdentity = owner.serviceIdentity;
    request.afterSequence = cursor.upstreamSequence;
    request.timeoutMs = timeoutMs;
    const ExecutionEventReadResult upstream = m_reader(request);
    reason = upstream.reasonCode;
    if (upstream.status == ExecutionEventReadStatus::Event)
    {
        if (!SameIdentity(upstream.serviceIdentity, owner.serviceIdentity) ||
            upstream.event.executionDomain != owner.executionDomain ||
            upstream.event.agentId != owner.agentId ||
            upstream.event.sessionId != owner.sessionId)
        {
            reason = "EXECUTION_EVENT_RELAY_OWNER_MISMATCH";
            return ExecutionEventRelayStatus::TransportFailure;
        }
        ExecutionEvent event = upstream.event;
        event.upstreamServiceEpoch = upstream.serviceIdentity.serviceEpoch;
        event.upstreamServiceFencingGeneration =
            upstream.serviceIdentity.serviceFencingGeneration;
        event.upstreamStreamEpoch = upstream.streamEpoch;
        event.upstreamSequence = upstream.event.sequence;
        if (m_localHub.Publish(event) == 0)
        {
            reason = "EXECUTION_EVENT_RELAY_PUBLISH_FAILED";
            return ExecutionEventRelayStatus::TransportFailure;
        }
        cursor.upstreamEpoch = upstream.streamEpoch;
        cursor.upstreamSequence = upstream.event.sequence;
        reason.clear();
        return ExecutionEventRelayStatus::Published;
    }
    if (upstream.status == ExecutionEventReadStatus::Gap)
    {
        cursor.upstreamEpoch = upstream.streamEpoch;
        cursor.upstreamSequence = upstream.droppedThroughSequence;
        cursor.authoritativeResyncRequired = true;
        PublishControlEvent(owner, "system.execution_stream_gap",
            "EXECUTION_EVENT_GAP", upstream);
        return ExecutionEventRelayStatus::Gap;
    }
    if (upstream.status == ExecutionEventReadStatus::EpochChanged)
    {
        cursor.upstreamEpoch = upstream.streamEpoch;
        cursor.upstreamSequence = 0;
        cursor.authoritativeResyncRequired = true;
        PublishControlEvent(owner, "system.execution_stream_epoch_changed",
            "EXECUTION_EVENT_STREAM_EPOCH_CHANGED", upstream);
        return ExecutionEventRelayStatus::EpochChanged;
    }
    if (upstream.status == ExecutionEventReadStatus::ServiceIdentityMismatch)
        return ExecutionEventRelayStatus::ServiceIdentityMismatch;
    if (upstream.status == ExecutionEventReadStatus::InvalidOwner)
        return ExecutionEventRelayStatus::InvalidOwner;
    if (upstream.reasonCode != "EXECUTION_EVENT_TIMEOUT")
        return ExecutionEventRelayStatus::TransportFailure;
    return ExecutionEventRelayStatus::Timeout;
}

bool ExecutionEventRelay::AcknowledgeAuthoritativeResync(
    ExecutionEventRelayCursor& cursor,
    const ExecutionServiceIdentity& reconciledIdentity)
{
    if (!ValidIdentity(reconciledIdentity) ||
        !SameIdentity(cursor.upstreamServiceIdentity, reconciledIdentity))
        return false;
    cursor.authoritativeResyncRequired = false;
    return true;
}
