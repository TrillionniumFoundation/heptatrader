#include "execution_event_hub.h"

#include <algorithm>
#include <atomic>
#include <chrono>
#include <iomanip>
#include <sstream>
#include <unistd.h>

namespace {

std::uint64_t EpochNowMs()
{
    return static_cast<std::uint64_t>(
        std::chrono::duration_cast<std::chrono::milliseconds>(
            std::chrono::system_clock::now().time_since_epoch()).count());
}

std::string JsonEscape(const std::string& value)
{
    std::ostringstream out;
    for (std::string::const_iterator it = value.begin(); it != value.end(); ++it)
    {
        const unsigned char c = static_cast<unsigned char>(*it);
        switch (c)
        {
        case '\"': out << "\\\""; break;
        case '\\': out << "\\\\"; break;
        case '\b': out << "\\b"; break;
        case '\f': out << "\\f"; break;
        case '\n': out << "\\n"; break;
        case '\r': out << "\\r"; break;
        case '\t': out << "\\t"; break;
        default:
            if (c < 0x20)
            {
                out << "\\u" << std::hex << std::setw(4) << std::setfill('0')
                    << static_cast<unsigned int>(c) << std::dec;
            }
            else out << *it;
        }
    }
    return out.str();
}

} // namespace

std::string NewStreamEpoch()
{
    static std::atomic<std::uint64_t> nonce(1);
    std::ostringstream value;
    value << "execution-" << EpochNowMs() << "-" << ::getpid()
          << "-" << nonce.fetch_add(1);
    return value.str();
}

ExecutionEventHub::ExecutionEventHub(std::size_t capacityPerAgent,
                                     const std::string& streamEpoch)
    : m_capacityPerAgent(std::max<std::size_t>(1, capacityPerAgent)),
      m_streamEpoch(streamEpoch.empty() ? NewStreamEpoch() : streamEpoch),
      m_nextSequence(1)
{
}

std::uint64_t ExecutionEventHub::Publish(ExecutionEvent event)
{
    if (event.executionDomain.empty() || event.agentId.empty() || event.sessionId.empty()) return 0;

    std::lock_guard<std::mutex> lock(m_mutex);
    if (event.upstreamStreamEpoch.empty() && !event.streamEpoch.empty())
        event.upstreamStreamEpoch = event.streamEpoch;
    if (event.upstreamSequence == 0 && event.sequence != 0)
        event.upstreamSequence = event.sequence;
    event.sequence = m_nextSequence++;
    event.streamEpoch = m_streamEpoch;
    if (event.timestampMs == 0) event.timestampMs = EpochNowMs();
    std::deque<ExecutionEvent>& queue = m_queues[QueueKey(event.executionDomain, event.agentId, event.sessionId)];
    queue.push_back(event);
    while (queue.size() > m_capacityPerAgent)
    {
        m_droppedThrough[QueueKey(event.executionDomain, event.agentId, event.sessionId)] =
            queue.front().sequence;
        queue.pop_front();
    }
    m_changed.notify_all();
    return event.sequence;
}

std::string ExecutionEventHub::QueueKey(const std::string& executionDomain,
                                        const std::string& agentId,
                                        const std::string& sessionId)
{
    return std::to_string(executionDomain.size()) + ":" + executionDomain +
           std::to_string(agentId.size()) + ":" + agentId + sessionId;
}

bool ExecutionEventHub::FindNextLocked(const std::string& queueKey,
                                       std::uint64_t afterSequence,
                                       ExecutionEvent& out) const
{
    const std::unordered_map<std::string, std::deque<ExecutionEvent> >::const_iterator found = m_queues.find(queueKey);
    if (found == m_queues.end()) return false;
    for (std::deque<ExecutionEvent>::const_iterator it = found->second.begin(); it != found->second.end(); ++it)
    {
        if (it->sequence > afterSequence)
        {
            out = *it;
            return true;
        }
    }
    return false;
}

bool ExecutionEventHub::WaitNext(const std::string& executionDomain,
                                 const std::string& agentId,
                                 const std::string& sessionId,
                                 std::uint64_t afterSequence,
                                 int timeoutMs,
                                 ExecutionEvent& out)
{
    ExecutionEventReadResult result = ReadNext(
        executionDomain, agentId, sessionId, std::string(), afterSequence, timeoutMs);
    // Preserve the legacy API's best-effort behavior. New cross-process
    // consumers use ReadNext() directly and must handle the explicit gap.
    if (result.status == ExecutionEventReadStatus::Gap)
        result = ReadNext(executionDomain, agentId, sessionId, std::string(),
                          result.droppedThroughSequence, 0);
    if (result.status != ExecutionEventReadStatus::Event) return false;
    out = result.event;
    return true;
}

ExecutionEventReadResult ExecutionEventHub::ReadNext(
    const std::string& executionDomain,
    const std::string& agentId,
    const std::string& sessionId,
    const std::string& expectedEpoch,
    std::uint64_t afterSequence,
    int timeoutMs)
{
    ExecutionEventReadResult result;
    result.streamEpoch = m_streamEpoch;
    if (executionDomain.empty() || agentId.empty() || sessionId.empty())
    {
        result.status = ExecutionEventReadStatus::InvalidOwner;
        result.reasonCode = "EXECUTION_EVENT_OWNER_REQUIRED";
        return result;
    }
    if (!expectedEpoch.empty() && expectedEpoch != m_streamEpoch)
    {
        result.status = ExecutionEventReadStatus::EpochChanged;
        result.reasonCode = "EXECUTION_EVENT_STREAM_EPOCH_CHANGED";
        return result;
    }
    const std::string queueKey = QueueKey(executionDomain, agentId, sessionId);
    std::unique_lock<std::mutex> lock(m_mutex);
    const auto populate = [this, &queueKey, afterSequence, &result]() {
        const std::unordered_map<std::string, std::uint64_t>::const_iterator dropped =
            m_droppedThrough.find(queueKey);
        result.droppedThroughSequence = dropped == m_droppedThrough.end() ? 0 : dropped->second;
        result.latestSequence = m_nextSequence == 0 ? 0 : m_nextSequence - 1;
        if (afterSequence < result.droppedThroughSequence)
        {
            result.status = ExecutionEventReadStatus::Gap;
            result.reasonCode = "EXECUTION_EVENT_GAP";
            return true;
        }
        if (FindNextLocked(queueKey, afterSequence, result.event))
        {
            result.status = ExecutionEventReadStatus::Event;
            result.reasonCode.clear();
            return true;
        }
        return false;
    };
    if (populate()) return result;
    if (timeoutMs <= 0)
    {
        result.status = ExecutionEventReadStatus::Timeout;
        result.reasonCode = "EXECUTION_EVENT_TIMEOUT";
        return result;
    }
    m_changed.wait_for(lock, std::chrono::milliseconds(timeoutMs), populate);
    if (result.status != ExecutionEventReadStatus::Event &&
        result.status != ExecutionEventReadStatus::Gap)
    {
        result.status = ExecutionEventReadStatus::Timeout;
        result.reasonCode = "EXECUTION_EVENT_TIMEOUT";
        result.latestSequence = m_nextSequence == 0 ? 0 : m_nextSequence - 1;
    }
    return result;
}

const std::string& ExecutionEventHub::StreamEpoch() const
{
    return m_streamEpoch;
}

std::size_t ExecutionEventHub::Pending(const std::string& executionDomain,
                                       const std::string& agentId,
                                       const std::string& sessionId,
                                       std::uint64_t afterSequence) const
{
    std::lock_guard<std::mutex> lock(m_mutex);
    const std::unordered_map<std::string, std::deque<ExecutionEvent> >::const_iterator found =
        m_queues.find(QueueKey(executionDomain, agentId, sessionId));
    if (found == m_queues.end()) return 0;
    std::size_t count = 0;
    for (std::deque<ExecutionEvent>::const_iterator it = found->second.begin(); it != found->second.end(); ++it)
        if (it->sequence > afterSequence) ++count;
    return count;
}

std::string ExecutionEventHub::ToJson(const ExecutionEvent& event)
{
    std::ostringstream out;
    out << "{\"stream_epoch\":\"" << JsonEscape(event.streamEpoch)
        << "\",\"sequence\":" << event.sequence
        << ",\"upstream_service_epoch\":\""
        << JsonEscape(event.upstreamServiceEpoch)
        << "\",\"upstream_service_fencing_generation\":"
        << event.upstreamServiceFencingGeneration
        << ",\"upstream_stream_epoch\":\"" << JsonEscape(event.upstreamStreamEpoch)
        << "\",\"upstream_sequence\":" << event.upstreamSequence
        << ",\"timestamp_ms\":" << event.timestampMs
        << ",\"execution_domain\":\"" << JsonEscape(event.executionDomain) << "\""
        << ",\"agent_id\":\"" << JsonEscape(event.agentId)
        << "\",\"session_id\":\"" << JsonEscape(event.sessionId)
        << "\",\"type\":\"" << JsonEscape(event.type)
        << "\",\"venue\":\"" << JsonEscape(event.venue)
        << "\",\"order_id\":" << event.orderId
        << ",\"instrument\":\"" << JsonEscape(event.instrument)
        << "\",\"side\":\"" << JsonEscape(event.side)
        << "\",\"status\":\"" << JsonEscape(event.status)
        << "\",\"reason_code\":\"" << JsonEscape(event.reasonCode)
        << "\",\"filled_quantity\":" << event.filledQuantity
        << ",\"remaining_quantity\":" << event.remainingQuantity
        << ",\"average_fill_price\":" << event.averageFillPrice << "}";
    return out.str();
}
