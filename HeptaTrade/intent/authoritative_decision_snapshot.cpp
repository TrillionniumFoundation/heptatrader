#include "authoritative_decision_snapshot.h"

#include <cerrno>
#include <cmath>
#include <cstdlib>
#include <iomanip>
#include <limits>
#include <sstream>

namespace
{
const std::size_t kMaximumComponentBytes = 256u * 1024u;
const std::size_t kMaximumSnapshotBytes = 1024u * 1024u;

bool Reject(const char* code, const std::string& message,
            std::string& reasonCode, std::string& detail)
{
    reasonCode = code;
    detail = message;
    return false;
}

std::string EscapeJson(const std::string& value)
{
    std::ostringstream out;
    for (std::string::const_iterator it = value.begin(); it != value.end(); ++it)
    {
        const unsigned char c = static_cast<unsigned char>(*it);
        if (c == '"') out << "\\\"";
        else if (c == '\\') out << "\\\\";
        else if (c == '\n') out << "\\n";
        else if (c == '\r') out << "\\r";
        else if (c == '\t') out << "\\t";
        else if (c < 0x20)
            out << "\\u" << std::hex << std::setw(4)
                << std::setfill('0') << static_cast<unsigned int>(c)
                << std::dec;
        else out << *it;
    }
    return out.str();
}

bool JsonObject(const std::string& value)
{
    return value.size() >= 2 && value.size() <= kMaximumComponentBytes &&
        value.front() == '{' && value.back() == '}';
}

bool UniqueKeyOffset(const std::string& json, const std::string& key,
                     std::size_t& valueOffset)
{
    const std::string marker = "\"" + key + "\":";
    const std::size_t first = json.find(marker);
    if (first == std::string::npos ||
        json.find(marker, first + marker.size()) != std::string::npos)
        return false;
    valueOffset = first + marker.size();
    return true;
}

bool ExtractString(const std::string& json, const std::string& key,
                   std::string& value)
{
    std::size_t offset = 0;
    if (!UniqueKeyOffset(json, key, offset) || offset >= json.size() ||
        json[offset] != '"') return false;
    ++offset;
    value.clear();
    while (offset < json.size())
    {
        const char c = json[offset++];
        if (c == '"') return true;
        if (c == '\\')
        {
            if (offset >= json.size()) return false;
            const char escaped = json[offset++];
            if (escaped == '"' || escaped == '\\' || escaped == '/')
                value.push_back(escaped);
            else if (escaped == 'n') value.push_back('\n');
            else if (escaped == 'r') value.push_back('\r');
            else if (escaped == 't') value.push_back('\t');
            else return false;
        }
        else if (static_cast<unsigned char>(c) < 0x20) return false;
        else value.push_back(c);
        if (value.size() > 256) return false;
    }
    return false;
}

bool ExtractBool(const std::string& json, const std::string& key, bool& value)
{
    std::size_t offset = 0;
    if (!UniqueKeyOffset(json, key, offset)) return false;
    if (json.compare(offset, 4, "true") == 0)
    {
        value = true;
        return true;
    }
    if (json.compare(offset, 5, "false") == 0)
    {
        value = false;
        return true;
    }
    return false;
}

bool NumberToken(const std::string& json, const std::string& key,
                 std::string& token)
{
    std::size_t offset = 0;
    if (!UniqueKeyOffset(json, key, offset)) return false;
    std::size_t end = offset;
    while (end < json.size())
    {
        const char c = json[end];
        if ((c >= '0' && c <= '9') || c == '-' || c == '+' || c == '.' ||
            c == 'e' || c == 'E') ++end;
        else break;
    }
    if (end == offset || end - offset > 64) return false;
    token = json.substr(offset, end - offset);
    return true;
}

bool ExtractUnsigned(const std::string& json, const std::string& key,
                     std::uint64_t& value)
{
    std::string token;
    if (!NumberToken(json, key, token) || token[0] == '-') return false;
    char* end = nullptr;
    errno = 0;
    const unsigned long long parsed = std::strtoull(token.c_str(), &end, 10);
    if (errno != 0 || end == token.c_str() || *end != '\0') return false;
    value = static_cast<std::uint64_t>(parsed);
    return true;
}

bool ExtractDouble(const std::string& json, const std::string& key,
                   double& value)
{
    std::string token;
    if (!NumberToken(json, key, token)) return false;
    char* end = nullptr;
    errno = 0;
    const double parsed = std::strtod(token.c_str(), &end);
    if (errno != 0 || end == token.c_str() || *end != '\0' ||
        !std::isfinite(parsed)) return false;
    value = parsed;
    return true;
}

bool IsAuthoritative(const std::string& json)
{
    bool authoritative = false;
    return JsonObject(json) &&
        ExtractBool(json, "authoritative", authoritative) && authoritative;
}

struct HealthIdentity
{
    std::string epoch;
    std::uint64_t fencingGeneration = 0;
    std::uint64_t eventWatermark = 0;
};

bool ParseHealth(const std::string& json, HealthIdentity& identity)
{
    bool ready = false;
    return JsonObject(json) &&
        ExtractBool(json, "remote_execution_ready", ready) && ready &&
        ExtractString(json, "execution_service_epoch", identity.epoch) &&
        !identity.epoch.empty() &&
        ExtractUnsigned(json, "execution_service_fencing_generation",
                        identity.fencingGeneration) &&
        identity.fencingGeneration != 0 &&
        ExtractUnsigned(json, "event_watermark", identity.eventWatermark);
}

bool ExtractInstrumentPosition(const std::string& json,
                               const std::string& instrument,
                               double& quantity)
{
    const std::string marker =
        "{\"instrument\":\"" + EscapeJson(instrument) + "\",\"quantity\":";
    const std::size_t first = json.find(marker);
    if (first == std::string::npos)
    {
        quantity = 0.0;
        return true;
    }
    if (json.find(marker, first + marker.size()) != std::string::npos)
        return false;
    const std::size_t offset = first + marker.size();
    std::size_t end = offset;
    while (end < json.size())
    {
        const char c = json[end];
        if ((c >= '0' && c <= '9') || c == '-' || c == '+' || c == '.' ||
            c == 'e' || c == 'E') ++end;
        else break;
    }
    if (end == offset || end - offset > 64) return false;
    const std::string token = json.substr(offset, end - offset);
    char* parsedEnd = nullptr;
    errno = 0;
    const double parsed = std::strtod(token.c_str(), &parsedEnd);
    if (errno != 0 || parsedEnd == token.c_str() || *parsedEnd != '\0' ||
        !std::isfinite(parsed)) return false;
    quantity = parsed;
    return true;
}
}

bool AuthoritativeDecisionSnapshotCodec::Build(
    const std::string& agentId,
    const std::string& sessionId,
    const std::string& account,
    const std::string& executionDomain,
    const std::string& instrument,
    std::int64_t collectionStartedAtMs,
    std::int64_t collectionCompletedAtMs,
    std::uint64_t collectionWatermark,
    const AuthoritativeDecisionSnapshotPayloads& payloads,
    TargetPositionDecisionSnapshot& snapshot,
    std::string& outputJson,
    std::string& reasonCode,
    std::string& detail)
{
    snapshot = TargetPositionDecisionSnapshot();
    outputJson.clear();
    if (agentId.empty() || sessionId.empty() || account.empty() ||
        executionDomain.empty() || instrument.empty() ||
        collectionStartedAtMs <= 0 ||
        collectionCompletedAtMs < collectionStartedAtMs ||
        collectionWatermark == 0)
        return Reject("DECISION_SNAPSHOT_REQUEST_INVALID",
                      "snapshot owner, instrument, time or watermark is invalid",
                      reasonCode, detail);

    HealthIdentity before;
    HealthIdentity after;
    if (!ParseHealth(payloads.healthBefore, before) ||
        !ParseHealth(payloads.healthAfter, after))
        return Reject("DECISION_SNAPSHOT_HEALTH_INVALID",
                      "Gateway health lacks a ready execution identity or event watermark",
                      reasonCode, detail);
    if (before.epoch != after.epoch ||
        before.fencingGeneration != after.fencingGeneration)
        return Reject("DECISION_SNAPSHOT_EXECUTION_IDENTITY_CHANGED",
                      "execution epoch or fencing generation changed during collection",
                      reasonCode, detail);
    if (before.eventWatermark != after.eventWatermark)
        return Reject("DECISION_SNAPSHOT_EVENT_WATERMARK_CHANGED",
                      "an execution event arrived during snapshot collection",
                      reasonCode, detail);

    if (!IsAuthoritative(payloads.quote) ||
        !IsAuthoritative(payloads.account) ||
        !IsAuthoritative(payloads.positions) ||
        !IsAuthoritative(payloads.orders) ||
        !IsAuthoritative(payloads.riskLimits))
        return Reject("DECISION_SNAPSHOT_COMPONENT_INCOMPLETE",
                      "one or more authoritative components are incomplete",
                      reasonCode, detail);

    std::string quoteInstrument;
    std::uint64_t quoteObservedAtMs = 0;
    bool stale = true;
    double bid = 0.0;
    double ask = 0.0;
    if (!ExtractString(payloads.quote, "instrument", quoteInstrument) ||
        quoteInstrument != instrument ||
        !ExtractUnsigned(payloads.quote, "observed_at_ms", quoteObservedAtMs) ||
        quoteObservedAtMs == 0 ||
        !ExtractBool(payloads.quote, "stale", stale) || stale ||
        !ExtractDouble(payloads.quote, "bid", bid) ||
        !ExtractDouble(payloads.quote, "ask", ask) ||
        !(bid > 0.0) || ask < bid)
        return Reject("DECISION_SNAPSHOT_QUOTE_INVALID",
                      "quote is stale, malformed or bound to another instrument",
                      reasonCode, detail);
    if (quoteObservedAtMs >
        static_cast<std::uint64_t>(collectionCompletedAtMs))
        return Reject("DECISION_SNAPSHOT_QUOTE_INVALID",
                      "quote timestamp is after collection completion",
                      reasonCode, detail);

    double currentPosition = 0.0;
    if (!ExtractInstrumentPosition(
            payloads.positions, instrument, currentPosition))
        return Reject("DECISION_SNAPSHOT_POSITION_INVALID",
                      "instrument position is duplicated or malformed",
                      reasonCode, detail);

    snapshot.agentId = agentId;
    snapshot.sessionId = sessionId;
    snapshot.account = account;
    snapshot.executionDomain = executionDomain;
    snapshot.executionServiceEpoch = before.epoch;
    snapshot.fencingGeneration = before.fencingGeneration;
    snapshot.collectionWatermark = collectionWatermark;
    snapshot.eventWatermark = before.eventWatermark;
    snapshot.snapshotWatermark = collectionWatermark;
    snapshot.instrument = instrument;
    snapshot.collectionStartedAtMs = collectionStartedAtMs;
    snapshot.collectionCompletedAtMs = collectionCompletedAtMs;
    snapshot.quoteObservedAtMs = static_cast<std::int64_t>(quoteObservedAtMs);
    snapshot.bid = bid;
    snapshot.ask = ask;
    snapshot.currentPosition = currentPosition;

    std::ostringstream output;
    output << "{\"schema\":\"hepta.decision-snapshot.v1\","
           << "\"authoritative\":true,\"instrument\":\""
           << EscapeJson(instrument)
           << "\",\"execution_service_epoch\":\""
           << EscapeJson(before.epoch)
           << "\",\"fencing_generation\":"
           << before.fencingGeneration
           << ",\"collection_watermark\":" << collectionWatermark
           << ",\"event_watermark\":" << before.eventWatermark
           << ",\"snapshot_watermark\":" << collectionWatermark
           << ",\"collection_started_at_ms\":"
           << collectionStartedAtMs
           << ",\"collection_completed_at_ms\":"
           << collectionCompletedAtMs
           << ",\"current_position\":"
           << std::setprecision(17) << currentPosition
           << ",\"quote\":" << payloads.quote
           << ",\"account\":" << payloads.account
           << ",\"positions\":" << payloads.positions
           << ",\"orders\":" << payloads.orders
           << ",\"risk_limits\":" << payloads.riskLimits
           << ",\"health\":" << payloads.healthAfter << '}';
    outputJson = output.str();
    if (outputJson.size() > kMaximumSnapshotBytes)
    {
        outputJson.clear();
        return Reject("DECISION_SNAPSHOT_RESPONSE_TOO_LARGE",
                      "compound snapshot exceeds the bounded response size",
                      reasonCode, detail);
    }
    reasonCode.clear();
    detail.clear();
    return true;
}
