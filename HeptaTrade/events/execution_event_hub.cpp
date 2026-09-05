#include "execution_event_hub.h"

#include <algorithm>
#include <atomic>
#include <chrono>
#include <cmath>
#include <cstring>
#include <iomanip>
#include <locale>
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
    out.imbue(std::locale::classic());
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

bool ContainsAsciiInsensitive(const std::string& value,
                              const char* needle)
{
    if (needle == nullptr || *needle == '\0') return false;
    const std::size_t length = std::strlen(needle);
    if (length > value.size()) return false;
    for (std::size_t offset = 0; offset + length <= value.size(); ++offset)
    {
        bool matches = true;
        for (std::size_t i = 0; i < length; ++i)
        {
            char left = value[offset + i];
            char right = needle[i];
            if (left >= 'A' && left <= 'Z') left =
                static_cast<char>(left - 'A' + 'a');
            if (right >= 'A' && right <= 'Z') right =
                static_cast<char>(right - 'A' + 'a');
            if (left != right) { matches = false; break; }
        }
        if (matches) return true;
    }
    return false;
}

// Validate printable UTF-8 before JSON escaping.  Escaping C0 bytes alone is
// insufficient for local events.wait: malformed UTF-8, C1 controls and DEL
// can still be interpreted inconsistently by downstream Agent clients.
bool ContainsForbiddenControl(const std::string& value)
{
    for (std::size_t offset = 0; offset < value.size();)
    {
        const unsigned char first =
            static_cast<unsigned char>(value[offset]);
        if (first < 0x20u || first == 0x7fu ||
            (first >= 0x80u && first <= 0x9fu)) return true;
        if (first < 0x80u) { ++offset; continue; }
        std::size_t continuationCount = 0;
        if (first >= 0xc2u && first <= 0xdfu) continuationCount = 1;
        else if (first >= 0xe0u && first <= 0xefu) continuationCount = 2;
        else if (first >= 0xf0u && first <= 0xf4u) continuationCount = 3;
        else return true;
        if (value.size() - offset <= continuationCount) return true;
        const unsigned char second =
            static_cast<unsigned char>(value[offset + 1]);
        if ((first == 0xe0u && second < 0xa0u) ||
            (first == 0xedu && second >= 0xa0u) ||
            (first == 0xf0u && second < 0x90u) ||
            (first == 0xf4u && second > 0x8fu)) return true;
        std::uint32_t codepoint = first &
            (continuationCount == 1 ? 0x1fu :
             continuationCount == 2 ? 0x0fu : 0x07u);
        for (std::size_t i = 1; i <= continuationCount; ++i)
        {
            const unsigned char continuation =
                static_cast<unsigned char>(value[offset + i]);
            if (continuation < 0x80u || continuation > 0xbfu) return true;
            codepoint = (codepoint << 6) | (continuation & 0x3fu);
        }
        if (codepoint < 0x20u || codepoint == 0x7fu ||
            (codepoint >= 0x80u && codepoint <= 0x9fu)) return true;
        offset += continuationCount + 1u;
    }
    return false;
}

bool IsCanonicalEventCode(const std::string& value)
{
    if (value.empty() || value.size() > 256 ||
        value[0] < 'A' || value[0] > 'Z')
        return false;
    for (std::size_t i = 0; i < value.size(); ++i)
    {
        const unsigned char c = static_cast<unsigned char>(value[i]);
        if (!((c >= 'A' && c <= 'Z') || (c >= '0' && c <= '9') ||
              c == '_' || c == ':' || c == '=' || c == '-' || c == '.'))
            return false;
    }
    return true;
}

bool LooksLikeSensitiveEventText(const std::string& value,
                                 bool pathLike)
{
    if (ContainsForbiddenControl(value)) return true;
    // Preserve uppercase machine status/type codes, including meaningful
    // `_FAILED`, `_TOKEN`, and `_EXCEPTION` suffixes.  Free-form exception
    // prose does not satisfy this grammar and is still redacted below.
    if (IsCanonicalEventCode(value)) return false;
    static const char* const markers[] = {
        "exception", "what()", "credential", "secret", "password",
        "bearer", "authorization", "token", "private key", "api_key",
        "apikey", "errno", "stack trace", "threw", "could not",
        "not found", "failed"
    };
    for (std::size_t i = 0; i < sizeof(markers) / sizeof(markers[0]); ++i)
        if (ContainsAsciiInsensitive(value, markers[i])) return true;
    if (pathLike && (value.find("/private/") != std::string::npos ||
                     value.find("\\private\\") != std::string::npos ||
                     value.find("://") != std::string::npos ||
                     (!value.empty() && (value[0] == '/' || value[0] == '\\'))))
        return true;
    return false;
}

// Reason codes are machine-stable contract values, not free-form callback
// prose. Preserve canonical codes (including meaningful suffixes such as
// `_FAILED`) while routing malformed/path-bearing text to the generic event
// callback code below.
bool IsStableEventReasonCode(const std::string& value)
{
    if (value.empty() || value.size() > 256 ||
        ContainsForbiddenControl(value))
        return false;
    const bool startsUpper = value[0] >= 'A' && value[0] <= 'Z';
    // Canonical callback reasons are uppercase identifiers.  Keep the
    // historical lowercase structured health form (`generation=7`) but do
    // not mistake lowercase prose/credential markers for a reason code.
    if (!startsUpper && value.find('=') == std::string::npos &&
        value.find(':') == std::string::npos)
        return false;
    for (std::size_t i = 0; i < value.size(); ++i)
    {
        const unsigned char c = static_cast<unsigned char>(value[i]);
        if (!((c >= 'A' && c <= 'Z') || (c >= 'a' && c <= 'z') ||
              (c >= '0' && c <= '9') || c == '_' || c == ':' ||
              c == '=' || c == '-' || c == '.'))
            return false;
    }
    if (!startsUpper)
    {
        static const char* const sensitive[] = {
            "exception", "credential", "secret", "password", "bearer",
            "authorization", "token", "private key", "api_key", "apikey",
            "errno", "stack trace", "threw", "failed"
        };
        for (std::size_t i = 0; i < sizeof(sensitive) / sizeof(sensitive[0]); ++i)
            if (ContainsAsciiInsensitive(value, sensitive[i])) return false;
    }
    return true;
}

std::string OpaqueEventText(const std::string& value,
                            std::size_t maximum,
                            const std::string& fallback)
{
    if (value.size() > maximum || ContainsForbiddenControl(value))
        return fallback;
    return value;
}

std::string SensitiveEventText(const std::string& value,
                               std::size_t maximum,
                               const std::string& fallback,
                               bool pathLike = true)
{
    if (value.size() > maximum || LooksLikeSensitiveEventText(value, pathLike))
        return fallback;
    return value;
}

ExecutionEvent AgentSafeEvent(const ExecutionEvent& input)
{
    ExecutionEvent event = input;
    // Owner/session identifiers are opaque request-bound values. Preserve
    // words such as "secret-session" when they are valid identifiers; the
    // server/gateway owner binding, not substring heuristics, authenticates
    // them. Other display fields receive sensitive-text redaction.
    event.streamEpoch = OpaqueEventText(event.streamEpoch, 256, std::string());
    event.executionDomain = OpaqueEventText(event.executionDomain, 256, std::string());
    event.agentId = OpaqueEventText(event.agentId, 256, std::string());
    event.sessionId = OpaqueEventText(event.sessionId, 256, std::string());
    event.upstreamServiceEpoch = SensitiveEventText(
        event.upstreamServiceEpoch, 256, std::string(), true);
    event.upstreamStreamEpoch = SensitiveEventText(
        event.upstreamStreamEpoch, 256, std::string(), true);
    event.type = SensitiveEventText(event.type, 256, "event.error");
    event.venue = SensitiveEventText(event.venue, 256, "UNKNOWN", false);
    event.instrument = SensitiveEventText(event.instrument, 256, std::string(), false);
    event.side = SensitiveEventText(event.side, 256, std::string());
    event.status = SensitiveEventText(event.status, 256, "Error");
    event.reasonCode = event.reasonCode.empty() ||
        IsStableEventReasonCode(event.reasonCode) ? event.reasonCode :
        "EXECUTION_EVENT_CALLBACK_EXCEPTION";
    if (event.orderId < -1) event.orderId = -1;
    if (!std::isfinite(event.filledQuantity)) event.filledQuantity = 0.0;
    if (!std::isfinite(event.remainingQuantity)) event.remainingQuantity = 0.0;
    if (!std::isfinite(event.averageFillPrice)) event.averageFillPrice = 0.0;
    return event;
}

} // namespace

std::string NewStreamEpoch()
{
    static std::atomic<std::uint64_t> nonce(1);
    std::ostringstream value;
    value.imbue(std::locale::classic());
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

std::uint64_t ExecutionEventHub::LatestSequence() const
{
    std::lock_guard<std::mutex> lock(m_mutex);
    return m_nextSequence == 0 ? 0 : m_nextSequence - 1;
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
    const ExecutionEvent safeEvent = AgentSafeEvent(event);
    std::ostringstream out;
    // Event JSON is an Agent-facing wire representation.  Never inherit a
    // process-global locale (for example a comma-decimal locale), otherwise
    // finite numeric fields become invalid JSON and disagree with the typed
    // event-feed codec.
    out.imbue(std::locale::classic());
    out << "{\"stream_epoch\":\"" << JsonEscape(safeEvent.streamEpoch)
        << "\",\"sequence\":" << safeEvent.sequence
        << ",\"upstream_service_epoch\":\""
        << JsonEscape(safeEvent.upstreamServiceEpoch)
        << "\",\"upstream_service_fencing_generation\":"
        << safeEvent.upstreamServiceFencingGeneration
        << ",\"upstream_stream_epoch\":\"" << JsonEscape(safeEvent.upstreamStreamEpoch)
        << "\",\"upstream_sequence\":" << safeEvent.upstreamSequence
        << ",\"timestamp_ms\":" << safeEvent.timestampMs
        << ",\"execution_domain\":\"" << JsonEscape(safeEvent.executionDomain) << "\""
        << ",\"agent_id\":\"" << JsonEscape(safeEvent.agentId)
        << "\",\"session_id\":\"" << JsonEscape(safeEvent.sessionId)
        << "\",\"type\":\"" << JsonEscape(safeEvent.type)
        << "\",\"venue\":\"" << JsonEscape(safeEvent.venue)
        << "\",\"order_id\":" << safeEvent.orderId
        << ",\"instrument\":\"" << JsonEscape(safeEvent.instrument)
        << "\",\"side\":\"" << JsonEscape(safeEvent.side)
        << "\",\"status\":\"" << JsonEscape(safeEvent.status)
        << "\",\"reason_code\":\"" << JsonEscape(safeEvent.reasonCode)
        << "\",\"filled_quantity\":" << safeEvent.filledQuantity
        << ",\"remaining_quantity\":" << safeEvent.remainingQuantity
        << ",\"average_fill_price\":" << safeEvent.averageFillPrice << "}";
    return out.str();
}
