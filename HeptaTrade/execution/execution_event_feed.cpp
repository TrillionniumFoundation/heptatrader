#include "execution_event_feed_contract.h"

#include <cerrno>
#include <cmath>
#include <cstdlib>
#include <limits>
#include <map>
#include <set>
#include <sstream>

namespace
{
const char kMagic[] = {'H', 'E', 'V', '2'};

enum Field : unsigned int
{
    ExecutionDomain = 1,
    AgentId,
    SessionId,
    ExpectedServiceEpoch,
    AfterSequence,
    TimeoutMs,
    ExpectedServiceFencingGeneration,
    ReadStatus = 100,
    StreamEpoch,
    DroppedThroughSequence,
    LatestSequence,
    ReasonCode,
    EventSequence,
    EventTimestampMs,
    EventType,
    EventVenue,
    EventOrderId,
    EventInstrument,
    EventSide,
    EventStatus,
    EventReasonCode,
    EventFilledQuantity,
    EventRemainingQuantity,
    EventAverageFillPrice,
    ResultServiceEpoch = 130,
    ResultServiceFencingGeneration
};

void AppendU16(std::string& out, unsigned int value)
{
    out.push_back(static_cast<char>((value >> 8) & 0xff));
    out.push_back(static_cast<char>(value & 0xff));
}

void AppendU32(std::string& out, std::size_t value)
{
    out.push_back(static_cast<char>((value >> 24) & 0xff));
    out.push_back(static_cast<char>((value >> 16) & 0xff));
    out.push_back(static_cast<char>((value >> 8) & 0xff));
    out.push_back(static_cast<char>(value & 0xff));
}

bool ReadU16(const std::string& in, std::size_t& offset, unsigned int& value)
{
    if (offset + 2 > in.size()) return false;
    value = (static_cast<unsigned char>(in[offset]) << 8) |
        static_cast<unsigned char>(in[offset + 1]);
    offset += 2;
    return true;
}

bool ReadU32(const std::string& in, std::size_t& offset, std::size_t& value)
{
    if (offset + 4 > in.size()) return false;
    value = (static_cast<std::size_t>(static_cast<unsigned char>(in[offset])) << 24) |
        (static_cast<std::size_t>(static_cast<unsigned char>(in[offset + 1])) << 16) |
        (static_cast<std::size_t>(static_cast<unsigned char>(in[offset + 2])) << 8) |
        static_cast<unsigned char>(in[offset + 3]);
    offset += 4;
    return true;
}

void AppendField(std::string& out, unsigned int tag, const std::string& value)
{
    AppendU16(out, tag);
    AppendU32(out, value.size());
    out.append(value);
}

template <typename T>
std::string Number(T value)
{
    std::ostringstream out;
    out.precision(17);
    out << value;
    return out.str();
}

bool DecodeEnvelope(const std::string& body,
                    unsigned int& kind,
                    std::map<unsigned int, std::string>& fields,
                    std::string& reason)
{
    if (body.size() < 8 || body.compare(0, 4, kMagic, 4) != 0)
    {
        reason = "EXECUTION_EVENT_PROTOCOL_BAD_MAGIC";
        return false;
    }
    std::size_t offset = 4;
    unsigned int version = 0;
    if (!ReadU16(body, offset, version) ||
        version != ExecutionEventFeedProtocol::ProtocolVersion() ||
        !ReadU16(body, offset, kind))
    {
        reason = "EXECUTION_EVENT_PROTOCOL_UNSUPPORTED_VERSION";
        return false;
    }
    while (offset < body.size())
    {
        unsigned int tag = 0;
        std::size_t length = 0;
        if (!ReadU16(body, offset, tag) || !ReadU32(body, offset, length) ||
            length > 4096 || offset + length > body.size() || fields.count(tag) != 0)
        {
            reason = "EXECUTION_EVENT_PROTOCOL_INVALID_FIELD";
            return false;
        }
        fields[tag] = body.substr(offset, length);
        offset += length;
    }
    return true;
}

bool HasExactFields(const std::map<unsigned int, std::string>& fields,
                    const std::set<unsigned int>& expected,
                    std::string& reason)
{
    if (fields.size() != expected.size())
    {
        reason = "EXECUTION_EVENT_PROTOCOL_FIELD_SET_MISMATCH";
        return false;
    }
    for (std::set<unsigned int>::const_iterator it = expected.begin();
         it != expected.end(); ++it)
    {
        if (fields.find(*it) == fields.end())
        {
            reason = "EXECUTION_EVENT_PROTOCOL_FIELD_SET_MISMATCH";
            return false;
        }
    }
    return true;
}

bool ValidIdentity(const ExecutionServiceIdentity& identity)
{
    return !identity.serviceEpoch.empty() && identity.serviceEpoch.size() <= 128 &&
        identity.serviceFencingGeneration != 0;
}


bool ValidOwnerComponent(const std::string& value)
{
    if (value.empty() || value.size() > 256) return false;
    for (std::size_t i = 0; i < value.size(); ++i)
    {
        const unsigned char c = static_cast<unsigned char>(value[i]);
        if (c < 0x20 || c == 0x7f) return false;
    }
    return true;
}

bool Require(const std::map<unsigned int, std::string>& fields,
             unsigned int tag, std::string& value, std::string& reason)
{
    const std::map<unsigned int, std::string>::const_iterator found = fields.find(tag);
    if (found == fields.end())
    {
        reason = "EXECUTION_EVENT_PROTOCOL_MISSING_FIELD";
        return false;
    }
    value = found->second;
    return true;
}

bool ParseUnsigned(const std::string& value, std::uint64_t& parsed)
{
    if (value.empty() || value[0] == '-') return false;
    char* end = nullptr;
    errno = 0;
    const unsigned long long number = std::strtoull(value.c_str(), &end, 10);
    if (errno != 0 || end == value.c_str() || *end != '\0') return false;
    parsed = static_cast<std::uint64_t>(number);
    return true;
}

bool ParseLongLong(const std::string& value, long long& parsed)
{
    if (value.empty()) return false;
    char* end = nullptr;
    errno = 0;
    const long long number = std::strtoll(value.c_str(), &end, 10);
    if (errno != 0 || end == value.c_str() || *end != '\0') return false;
    parsed = number;
    return true;
}

bool ParseDouble(const std::string& value, double& parsed)
{
    if (value.empty()) return false;
    char* end = nullptr;
    errno = 0;
    const double number = std::strtod(value.c_str(), &end);
    if (errno != 0 || end == value.c_str() || *end != '\0' || !std::isfinite(number))
        return false;
    parsed = number;
    return true;
}

bool EmptyEvent(const ExecutionEvent& event)
{
    return event.streamEpoch.empty() && event.sequence == 0 &&
        event.upstreamServiceEpoch.empty() &&
        event.upstreamServiceFencingGeneration == 0 &&
        event.upstreamStreamEpoch.empty() && event.upstreamSequence == 0 &&
        event.timestampMs == 0 && event.executionDomain.empty() &&
        event.agentId.empty() && event.sessionId.empty() && event.type.empty() &&
        event.venue.empty() && event.orderId == -1 && event.instrument.empty() &&
        event.side.empty() && event.status.empty() && event.reasonCode.empty() &&
        event.filledQuantity == 0.0 && event.remainingQuantity == 0.0 &&
        event.averageFillPrice == 0.0;
}

bool ValidateResponse(const ExecutionEventReadResult& response, std::string& reason)
{
    if (!ValidIdentity(response.serviceIdentity) ||
        response.streamEpoch != response.serviceIdentity.serviceEpoch ||
        response.droppedThroughSequence > response.latestSequence)
    {
        reason = "EXECUTION_EVENT_RESPONSE_IDENTITY_INVALID";
        return false;
    }
    const bool emptyEvent = EmptyEvent(response.event);
    switch (response.status)
    {
    case ExecutionEventReadStatus::Event:
        if (!response.reasonCode.empty() ||
            response.event.streamEpoch != response.streamEpoch ||
            response.event.sequence == 0 ||
            response.event.sequence <= response.droppedThroughSequence ||
            response.event.sequence > response.latestSequence ||
            response.event.timestampMs == 0 ||
            !ValidOwnerComponent(response.event.executionDomain) ||
            !ValidOwnerComponent(response.event.agentId) ||
            !ValidOwnerComponent(response.event.sessionId) ||
            response.event.type.empty() || response.event.venue.empty() ||
            !std::isfinite(response.event.filledQuantity) ||
            !std::isfinite(response.event.remainingQuantity) ||
            !std::isfinite(response.event.averageFillPrice))
        {
            reason = "EXECUTION_EVENT_RESPONSE_EVENT_INVALID";
            return false;
        }
        break;
    case ExecutionEventReadStatus::Timeout:
        if (response.reasonCode != "EXECUTION_EVENT_TIMEOUT" || !emptyEvent)
        {
            reason = "EXECUTION_EVENT_RESPONSE_TIMEOUT_INVALID";
            return false;
        }
        break;
    case ExecutionEventReadStatus::Gap:
        if (response.reasonCode != "EXECUTION_EVENT_GAP" ||
            response.droppedThroughSequence == 0 || !emptyEvent)
        {
            reason = "EXECUTION_EVENT_RESPONSE_GAP_INVALID";
            return false;
        }
        break;
    case ExecutionEventReadStatus::InvalidOwner:
        if (response.reasonCode.empty() || !emptyEvent)
        {
            reason = "EXECUTION_EVENT_RESPONSE_REJECTION_INVALID";
            return false;
        }
        break;
    case ExecutionEventReadStatus::ServiceIdentity:
        if (response.reasonCode != "EXECUTION_EVENT_SERVICE_IDENTITY" ||
            response.droppedThroughSequence != 0 || response.latestSequence != 0 ||
            !emptyEvent)
        {
            reason = "EXECUTION_EVENT_RESPONSE_IDENTITY_QUERY_INVALID";
            return false;
        }
        break;
    case ExecutionEventReadStatus::ServiceIdentityMismatch:
        if (response.reasonCode != "EXECUTION_EVENT_SERVICE_IDENTITY_MISMATCH" ||
            response.droppedThroughSequence != 0 || response.latestSequence != 0 ||
            !emptyEvent)
        {
            reason = "EXECUTION_EVENT_RESPONSE_IDENTITY_MISMATCH_INVALID";
            return false;
        }
        break;
    case ExecutionEventReadStatus::ServiceNotReady:
        if (response.reasonCode != "EXECUTION_EVENT_SERVICE_NOT_READY" ||
            response.droppedThroughSequence != 0 || response.latestSequence != 0 ||
            !emptyEvent)
        {
            reason = "EXECUTION_EVENT_RESPONSE_NOT_READY_INVALID";
            return false;
        }
        break;
    case ExecutionEventReadStatus::ServiceStopping:
        if (response.reasonCode != "EXECUTION_EVENT_SERVICE_STOPPING" ||
            response.droppedThroughSequence != 0 || response.latestSequence != 0 ||
            !emptyEvent)
        {
            reason = "EXECUTION_EVENT_RESPONSE_STOPPING_INVALID";
            return false;
        }
        break;
    case ExecutionEventReadStatus::EpochChanged:
        reason = "EXECUTION_EVENT_RESPONSE_LEGACY_STATUS_REJECTED";
        return false;
    }
    reason.clear();
    return true;
}
} // namespace

unsigned int ExecutionEventFeedProtocol::ProtocolVersion()
{
    return 2;
}

bool ExecutionEventFeedProtocol::EncodeRequest(const ExecutionEventFeedRequest& request,
                                               std::string& body, std::string& reason)
{
    body.assign(kMagic, sizeof(kMagic));
    AppendU16(body, ProtocolVersion());
    AppendU16(body, static_cast<unsigned int>(request.operation));
    if (request.operation == ExecutionEventFeedOperation::GetServiceIdentity)
    {
        if (!request.executionDomain.empty() || !request.agentId.empty() ||
            !request.sessionId.empty() ||
            !request.expectedServiceIdentity.serviceEpoch.empty() ||
            request.expectedServiceIdentity.serviceFencingGeneration != 0 ||
            request.afterSequence != 0 || request.timeoutMs != 0)
        {
            reason = "EXECUTION_EVENT_IDENTITY_REQUEST_INVALID";
            return false;
        }
        reason.clear();
        return true;
    }
    if (request.operation != ExecutionEventFeedOperation::Wait ||
        !ValidOwnerComponent(request.executionDomain) ||
        !ValidOwnerComponent(request.agentId) ||
        !ValidOwnerComponent(request.sessionId) ||
        !ValidIdentity(request.expectedServiceIdentity) ||
        request.timeoutMs < 0 || request.timeoutMs > 30000)
    {
        reason = "EXECUTION_EVENT_WAIT_REQUEST_INVALID";
        return false;
    }
    AppendField(body, ExecutionDomain, request.executionDomain);
    AppendField(body, AgentId, request.agentId);
    AppendField(body, SessionId, request.sessionId);
    AppendField(body, ExpectedServiceEpoch,
        request.expectedServiceIdentity.serviceEpoch);
    AppendField(body, ExpectedServiceFencingGeneration,
        Number(request.expectedServiceIdentity.serviceFencingGeneration));
    AppendField(body, AfterSequence, Number(request.afterSequence));
    AppendField(body, TimeoutMs, Number(request.timeoutMs));
    reason.clear();
    return true;
}

bool ExecutionEventFeedProtocol::DecodeRequest(const std::string& body,
                                               ExecutionEventFeedRequest& request,
                                               std::string& reason)
{
    request = ExecutionEventFeedRequest();
    unsigned int kind = 0;
    std::map<unsigned int, std::string> fields;
    if (!DecodeEnvelope(body, kind, fields, reason)) return false;
    if (kind == static_cast<unsigned int>(
            ExecutionEventFeedOperation::GetServiceIdentity))
    {
        if (!HasExactFields(fields, std::set<unsigned int>(), reason)) return false;
        request.operation = ExecutionEventFeedOperation::GetServiceIdentity;
        reason.clear();
        return true;
    }
    if (kind != static_cast<unsigned int>(ExecutionEventFeedOperation::Wait))
    {
        reason = "EXECUTION_EVENT_PROTOCOL_INVALID_OPERATION";
        return false;
    }
    const std::set<unsigned int> expectedFields{
        ExecutionDomain, AgentId, SessionId, ExpectedServiceEpoch,
        ExpectedServiceFencingGeneration, AfterSequence, TimeoutMs};
    if (!HasExactFields(fields, expectedFields, reason)) return false;
    std::string afterSequence;
    std::string timeoutMs;
    std::string serviceGeneration;
    std::uint64_t parsedTimeout = 0;
    if (!Require(fields, ExecutionDomain, request.executionDomain, reason) ||
        !Require(fields, AgentId, request.agentId, reason) ||
        !Require(fields, SessionId, request.sessionId, reason) ||
        !Require(fields, ExpectedServiceEpoch,
            request.expectedServiceIdentity.serviceEpoch, reason) ||
        !Require(fields, ExpectedServiceFencingGeneration, serviceGeneration, reason) ||
        !Require(fields, AfterSequence, afterSequence, reason) ||
        !Require(fields, TimeoutMs, timeoutMs, reason) ||
        !ParseUnsigned(afterSequence, request.afterSequence) ||
        !ParseUnsigned(serviceGeneration,
            request.expectedServiceIdentity.serviceFencingGeneration) ||
        !ParseUnsigned(timeoutMs, parsedTimeout) || parsedTimeout > 30000 ||
        !ValidOwnerComponent(request.executionDomain) ||
        !ValidOwnerComponent(request.agentId) ||
        !ValidOwnerComponent(request.sessionId) ||
        !ValidIdentity(request.expectedServiceIdentity))
    {
        if (reason.empty()) reason = "EXECUTION_EVENT_WAIT_REQUEST_INVALID";
        return false;
    }
    request.operation = ExecutionEventFeedOperation::Wait;
    request.timeoutMs = static_cast<int>(parsedTimeout);
    reason.clear();
    return true;
}

bool ExecutionEventFeedProtocol::EncodeResponse(const ExecutionEventReadResult& response,
                                                std::string& body, std::string& reason)
{
    if (!ValidateResponse(response, reason)) return false;
    body.assign(kMagic, sizeof(kMagic));
    AppendU16(body, ProtocolVersion());
    AppendU16(body, 0);
    AppendField(body, ReadStatus, Number(static_cast<int>(response.status)));
    AppendField(body, StreamEpoch, response.streamEpoch);
    AppendField(body, DroppedThroughSequence, Number(response.droppedThroughSequence));
    AppendField(body, LatestSequence, Number(response.latestSequence));
    AppendField(body, ReasonCode, response.reasonCode);
    AppendField(body, EventSequence, Number(response.event.sequence));
    AppendField(body, EventTimestampMs, Number(response.event.timestampMs));
    AppendField(body, ExecutionDomain, response.event.executionDomain);
    AppendField(body, AgentId, response.event.agentId);
    AppendField(body, SessionId, response.event.sessionId);
    AppendField(body, EventType, response.event.type);
    AppendField(body, EventVenue, response.event.venue);
    AppendField(body, EventOrderId, Number(response.event.orderId));
    AppendField(body, EventInstrument, response.event.instrument);
    AppendField(body, EventSide, response.event.side);
    AppendField(body, EventStatus, response.event.status);
    AppendField(body, EventReasonCode, response.event.reasonCode);
    AppendField(body, EventFilledQuantity, Number(response.event.filledQuantity));
    AppendField(body, EventRemainingQuantity, Number(response.event.remainingQuantity));
    AppendField(body, EventAverageFillPrice, Number(response.event.averageFillPrice));
    AppendField(body, ResultServiceEpoch, response.serviceIdentity.serviceEpoch);
    AppendField(body, ResultServiceFencingGeneration,
        Number(response.serviceIdentity.serviceFencingGeneration));
    reason.clear();
    return true;
}

bool ExecutionEventFeedProtocol::DecodeResponse(const std::string& body,
                                                ExecutionEventReadResult& response,
                                                std::string& reason)
{
    response = ExecutionEventReadResult();
    unsigned int kind = 0;
    std::map<unsigned int, std::string> fields;
    if (!DecodeEnvelope(body, kind, fields, reason) || kind != 0)
    {
        if (reason.empty()) reason = "EXECUTION_EVENT_RESPONSE_KIND_INVALID";
        return false;
    }
    const std::set<unsigned int> expectedFields{
        ReadStatus, StreamEpoch, DroppedThroughSequence, LatestSequence, ReasonCode,
        EventSequence, EventTimestampMs, ExecutionDomain, AgentId, SessionId,
        EventType, EventVenue, EventOrderId, EventInstrument, EventSide,
        EventStatus, EventReasonCode, EventFilledQuantity, EventRemainingQuantity,
        EventAverageFillPrice, ResultServiceEpoch, ResultServiceFencingGeneration};
    if (!HasExactFields(fields, expectedFields, reason)) return false;
    std::string readStatus;
    std::string dropped;
    std::string latest;
    std::string sequence;
    std::string timestamp;
    std::string orderId;
    std::string filled;
    std::string remaining;
    std::string average;
    std::string serviceGeneration;
    long long parsedStatus = -1;
    long long parsedOrderId = -1;
    if (!Require(fields, ReadStatus, readStatus, reason) ||
        !Require(fields, StreamEpoch, response.streamEpoch, reason) ||
        !Require(fields, DroppedThroughSequence, dropped, reason) ||
        !Require(fields, LatestSequence, latest, reason) ||
        !Require(fields, ReasonCode, response.reasonCode, reason) ||
        !Require(fields, EventSequence, sequence, reason) ||
        !Require(fields, EventTimestampMs, timestamp, reason) ||
        !Require(fields, ExecutionDomain, response.event.executionDomain, reason) ||
        !Require(fields, AgentId, response.event.agentId, reason) ||
        !Require(fields, SessionId, response.event.sessionId, reason) ||
        !Require(fields, EventType, response.event.type, reason) ||
        !Require(fields, EventVenue, response.event.venue, reason) ||
        !Require(fields, EventOrderId, orderId, reason) ||
        !Require(fields, EventInstrument, response.event.instrument, reason) ||
        !Require(fields, EventSide, response.event.side, reason) ||
        !Require(fields, EventStatus, response.event.status, reason) ||
        !Require(fields, EventReasonCode, response.event.reasonCode, reason) ||
        !Require(fields, EventFilledQuantity, filled, reason) ||
        !Require(fields, EventRemainingQuantity, remaining, reason) ||
        !Require(fields, EventAverageFillPrice, average, reason) ||
        !Require(fields, ResultServiceEpoch,
            response.serviceIdentity.serviceEpoch, reason) ||
        !Require(fields, ResultServiceFencingGeneration, serviceGeneration, reason) ||
        !ParseLongLong(readStatus, parsedStatus) || parsedStatus < 0 ||
        parsedStatus > static_cast<long long>(ExecutionEventReadStatus::ServiceStopping) ||
        !ParseUnsigned(dropped, response.droppedThroughSequence) ||
        !ParseUnsigned(latest, response.latestSequence) ||
        !ParseUnsigned(serviceGeneration,
            response.serviceIdentity.serviceFencingGeneration) ||
        !ParseUnsigned(sequence, response.event.sequence) ||
        !ParseUnsigned(timestamp, response.event.timestampMs) ||
        !ParseLongLong(orderId, parsedOrderId) ||
        parsedOrderId < std::numeric_limits<long>::min() ||
        parsedOrderId > std::numeric_limits<long>::max() ||
        !ParseDouble(filled, response.event.filledQuantity) ||
        !ParseDouble(remaining, response.event.remainingQuantity) ||
        !ParseDouble(average, response.event.averageFillPrice))
    {
        if (reason.empty()) reason = "EXECUTION_EVENT_RESPONSE_INVALID";
        return false;
    }
    response.status = static_cast<ExecutionEventReadStatus>(parsedStatus);
    response.event.orderId = static_cast<long>(parsedOrderId);
    if (response.status == ExecutionEventReadStatus::Event)
        response.event.streamEpoch = response.streamEpoch;
    return ValidateResponse(response, reason);
}
