#include "authoritative_decision_snapshot.h"
#include "bounded_json.h"

#include <cmath>
#include <iomanip>
#include <locale>
#include <limits>
#include <sstream>
#include <vector>

namespace
{
const std::size_t kMaximumSnapshotBytes = 1024u * 1024u;
// A single callback component may occupy the full bounded response envelope;
// the compound snapshot is checked against the same ceiling below.  Keeping
// this equal to the parser's aggregate limit avoids an accidental smaller
// per-field cap that would make exact wire-limit responses impossible.
const std::size_t kMaximumComponentBytes = kMaximumSnapshotBytes;
const std::size_t kMaximumIdentityBytes = 4096u;

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
    static const char kHex[] = "0123456789abcdef";
    for (std::string::const_iterator it = value.begin(); it != value.end(); ++it)
    {
        const unsigned char c = static_cast<unsigned char>(*it);
        if (c == '"') out << "\\\"";
        else if (c == '\\') out << "\\\\";
        else if (c == '\n') out << "\\n";
        else if (c == '\r') out << "\\r";
        else if (c == '\t') out << "\\t";
        else if (c < 0x20)
        {
            // Never replace a control byte with a printable placeholder:
            // doing so creates aliases at the authority boundary ("a\\0b"
            // and "a?b" would serialize identically).  JSON's \u00XX form
            // is lossless and remains valid even for escaped NULs.
            out << "\\u00" << kHex[(c >> 4) & 0x0f] << kHex[c & 0x0f];
        }
        else out << *it;
    }
    return out.str();
}

bool ValidIdentityText(const std::string& value, const char* field,
                       std::string& detail)
{
    if (value.empty())
    {
        detail = std::string(field) + " is empty";
        return false;
    }
    if (value.size() > kMaximumIdentityBytes)
    {
        detail = std::string(field) + " exceeds the bounded identity size";
        return false;
    }

    // Validate UTF-8 at the trust boundary.  Payload strings parsed by
    // BoundedJson are already checked, but the owner/instrument arguments are
    // supplied by the caller and are interpolated into the generated JSON.
    // Reject C0/C1 controls (and DEL) so logs, cache keys and JSON envelopes
    // cannot carry invisible identity separators.
    std::size_t offset = 0;
    while (offset < value.size())
    {
        const unsigned char first =
            static_cast<unsigned char>(value[offset]);
        std::uint32_t codepoint = 0;
        std::size_t continuationCount = 0;
        if (first <= 0x7fu)
        {
            codepoint = first;
            ++offset;
        }
        else
        {
            if (first >= 0xc2u && first <= 0xdfu)
                continuationCount = 1;
            else if (first >= 0xe0u && first <= 0xefu)
                continuationCount = 2;
            else if (first >= 0xf0u && first <= 0xf4u)
                continuationCount = 3;
            else
            {
                detail = std::string(field) + " is not valid UTF-8";
                return false;
            }
            if (value.size() - offset <= continuationCount)
            {
                detail = std::string(field) + " is truncated UTF-8";
                return false;
            }
            const unsigned char second =
                static_cast<unsigned char>(value[offset + 1]);
            if ((first == 0xe0u && second < 0xa0u) ||
                (first == 0xedu && second >= 0xa0u) ||
                (first == 0xf0u && second < 0x90u) ||
                (first == 0xf4u && second > 0x8fu))
            {
                detail = std::string(field) + " is not a Unicode scalar";
                return false;
            }
            for (std::size_t i = 1; i <= continuationCount; ++i)
            {
                const unsigned char continuation =
                    static_cast<unsigned char>(value[offset + i]);
                if (continuation < 0x80u || continuation > 0xbfu)
                {
                    detail = std::string(field) + " has an invalid UTF-8 continuation";
                    return false;
                }
            }
            if (continuationCount == 1)
            {
                codepoint = (static_cast<std::uint32_t>(first & 0x1fu) << 6) |
                    static_cast<std::uint32_t>(
                        static_cast<unsigned char>(value[offset + 1]) & 0x3fu);
            }
            else if (continuationCount == 2)
            {
                codepoint = (static_cast<std::uint32_t>(first & 0x0fu) << 12) |
                    (static_cast<std::uint32_t>(
                        static_cast<unsigned char>(value[offset + 1]) & 0x3fu) << 6) |
                    static_cast<std::uint32_t>(
                        static_cast<unsigned char>(value[offset + 2]) & 0x3fu);
            }
            else
            {
                codepoint = (static_cast<std::uint32_t>(first & 0x07u) << 18) |
                    (static_cast<std::uint32_t>(
                        static_cast<unsigned char>(value[offset + 1]) & 0x3fu) << 12) |
                    (static_cast<std::uint32_t>(
                        static_cast<unsigned char>(value[offset + 2]) & 0x3fu) << 6) |
                    static_cast<std::uint32_t>(
                        static_cast<unsigned char>(value[offset + 3]) & 0x3fu);
            }
            offset += continuationCount + 1u;
        }
        if (codepoint < 0x20u ||
            (codepoint >= 0x7fu && codepoint <= 0x9fu))
        {
            detail = std::string(field) + " contains a control character";
            return false;
        }
    }
    return true;
}

bool ParseObject(const std::string& json,
       BoundedJsonValue& value,
       std::string& reason)
{
    return ParseBoundedJson(json, value, reason, kMaximumComponentBytes) &&
        value.IsObject();
}

bool RequiredBool(const BoundedJsonValue& object,
        const char* key,
        bool& value)
{
    const BoundedJsonValue* field = object.Find(key);
    return field != nullptr && field->Boolean(value);
}

bool RequiredString(const BoundedJsonValue& object,
          const char* key,
          std::string& value)
{
    const BoundedJsonValue* field = object.Find(key);
    return field != nullptr && field->String(value);
}

bool RequiredUnsigned(const BoundedJsonValue& object,
            const char* key,
            std::uint64_t& value)
{
    const BoundedJsonValue* field = object.Find(key);
    return field != nullptr && field->Unsigned(value);
}

bool RequiredNumber(const BoundedJsonValue& object,
          const char* key,
          double& value)
{
    const BoundedJsonValue* field = object.Find(key);
    return field != nullptr && field->Number(value) && std::isfinite(value) &&
        // Snapshot numbers are re-serialized into a canonical envelope;
        // reject a signed-zero lexeme before it can alias the canonical "0"
        // spelling and split equivalent authority records.
        !(value == 0.0 && std::signbit(value));
}

bool Authoritative(const BoundedJsonValue& object)
{
    bool authoritative = false;
    return RequiredBool(object, "authoritative", authoritative) && authoritative;
}

struct HealthIdentity
{
    std::string epoch;
    std::uint64_t fencingGeneration = 0;
    std::uint64_t eventWatermark = 0;
    // Some Execution implementations expose an explicit state generation in
    // health.  It is optional for older Simulator/core payloads, but when
    // present it must be a stable positive identity across the collection.
    bool hasStateGeneration = false;
    std::uint64_t stateGeneration = 0;
};

bool ParseHealth(const BoundedJsonValue& object, HealthIdentity& identity)
{
    // A snapshot is an authority-bound compound read.  Execution readiness
    // alone is insufficient when the gateway itself is stopped or fenced:
    // accepting such a payload would allow a stale/partial local health
    // response to be promoted to an authoritative decision snapshot.  The
    // gateway health contract always emits this field, so require the exact
    // ready value at this boundary (rather than treating omission as false).
    bool gatewayReady = false;
    bool ready = false;
    if (!(RequiredBool(object, "gateway_ready", gatewayReady) && gatewayReady &&
        RequiredBool(object, "remote_execution_ready", ready) && ready &&
        RequiredString(object, "execution_service_epoch", identity.epoch) &&
        !identity.epoch.empty() &&
        RequiredUnsigned(object, "execution_service_fencing_generation",
               identity.fencingGeneration) &&
        identity.fencingGeneration != 0 &&
        RequiredUnsigned(object, "event_watermark", identity.eventWatermark)))
        return false;

    const BoundedJsonValue* stateGeneration =
        object.Find("state_generation");
    if (stateGeneration == nullptr) return true;
    identity.hasStateGeneration = true;
    return stateGeneration->Unsigned(identity.stateGeneration) &&
        identity.stateGeneration != 0;
}

bool ValidateOptionalOwnerScope(const BoundedJsonValue& object,
                                const std::string& agentId,
                                const std::string& sessionId,
                                const std::string& account,
                                const std::string& executionDomain,
                                std::string& detail)
{
    const BoundedJsonValue* scope = object.Find("owner_scope");
    if (scope != nullptr)
    {
        if (!scope->IsObject())
        {
            detail = "owner_scope";
            return false;
        }
        const struct OwnerField
        {
            const char* name;
            const std::string* expected;
        } fields[] = {
            {"agent_id", &agentId},
            {"session_id", &sessionId},
            {"account", &account},
            {"execution_domain", &executionDomain}
        };
        for (std::size_t i = 0; i < sizeof(fields) / sizeof(fields[0]); ++i)
        {
            std::string actual;
            const BoundedJsonValue* value = scope->Find(fields[i].name);
            if (value == nullptr || !value->String(actual) ||
                actual != *fields[i].expected)
            {
                detail = std::string("owner_scope.") + fields[i].name;
                return false;
            }
        }
    }

    // A few legacy authority payloads expose identity fields at the top
    // level instead of under owner_scope.  Validate them when present; an
    // omitted optional field remains compatible with the older payload shape.
    const struct TopLevelOwnerField
    {
        const char* name;
        const std::string* expected;
    } topLevel[] = {
        {"agent_id", &agentId},
        {"session_id", &sessionId},
        {"account", &account},
        {"execution_domain", &executionDomain}
    };
    for (std::size_t i = 0; i < sizeof(topLevel) / sizeof(topLevel[0]); ++i)
    {
        const BoundedJsonValue* value = object.Find(topLevel[i].name);
        if (value == nullptr) continue;
        std::string actual;
        if (!value->String(actual) || actual != *topLevel[i].expected)
        {
            detail = topLevel[i].name;
            return false;
        }
    }
    detail.clear();
    return true;
}

void FindInstrumentPositions(const BoundedJsonValue& value,
                   const std::string& instrument,
                   std::vector<double>& quantities,
                   bool& invalidCanonicalNumber)
{
    if (value.IsObject())
    {
        const BoundedJsonValue* instrumentField = value.Find("instrument");
        const BoundedJsonValue* quantityField = value.Find("quantity");
        std::string candidate;
        double quantity = 0.0;
        if (instrumentField != nullptr && quantityField != nullptr &&
  instrumentField->String(candidate) && candidate == instrument &&
  quantityField->Number(quantity) && std::isfinite(quantity))
        {
            if (quantity == 0.0 && std::signbit(quantity))
                invalidCanonicalNumber = true;
            else
                quantities.push_back(quantity);
        }
        for (std::map<std::string, BoundedJsonValue>::const_iterator it =
       value.Object().begin(); it != value.Object().end(); ++it)
  FindInstrumentPositions(it->second, instrument, quantities,
                          invalidCanonicalNumber);
    }
    else if (value.IsArray())
    {
        for (std::vector<BoundedJsonValue>::const_iterator it =
       value.Array().begin(); it != value.Array().end(); ++it)
  FindInstrumentPositions(*it, instrument, quantities,
                          invalidCanonicalNumber);
    }
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
    std::string identityDetail;
    if (!ValidIdentityText(agentId, "agent_id", identityDetail) ||
        !ValidIdentityText(sessionId, "session_id", identityDetail) ||
        !ValidIdentityText(account, "account", identityDetail) ||
        !ValidIdentityText(executionDomain, "execution_domain", identityDetail) ||
        !ValidIdentityText(instrument, "instrument", identityDetail) ||
        collectionStartedAtMs <= 0 ||
        collectionCompletedAtMs < collectionStartedAtMs ||
        collectionWatermark == 0)
        return Reject("DECISION_SNAPSHOT_REQUEST_INVALID",
            identityDetail.empty() ?
                "snapshot owner, instrument, time or watermark is invalid" :
                identityDetail,
            reasonCode, detail);

    BoundedJsonValue healthBefore;
    BoundedJsonValue healthAfter;
    BoundedJsonValue quote;
    BoundedJsonValue accountState;
    BoundedJsonValue positions;
    BoundedJsonValue orders;
    BoundedJsonValue riskLimits;
    std::string parseReason;
    if (!ParseObject(payloads.healthBefore, healthBefore, parseReason) ||
        !ParseObject(payloads.healthAfter, healthAfter, parseReason))
        return Reject("DECISION_SNAPSHOT_HEALTH_INVALID", parseReason,
            reasonCode, detail);

    HealthIdentity before;
    HealthIdentity after;
    if (!ParseHealth(healthBefore, before) || !ParseHealth(healthAfter, after))
        return Reject("DECISION_SNAPSHOT_HEALTH_INVALID",
            "Gateway health lacks a ready execution identity or event watermark",
            reasonCode, detail);
    std::string healthDetail;
    if (!ValidIdentityText(before.epoch, "execution_service_epoch", healthDetail) ||
        !ValidIdentityText(after.epoch, "execution_service_epoch", healthDetail))
        return Reject("DECISION_SNAPSHOT_HEALTH_INVALID", healthDetail,
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
    if (before.hasStateGeneration != after.hasStateGeneration ||
        (before.hasStateGeneration &&
         before.stateGeneration != after.stateGeneration))
        return Reject("DECISION_SNAPSHOT_STATE_GENERATION_CHANGED",
            "authoritative state generation changed during collection",
            reasonCode, detail);

    if (!ParseObject(payloads.quote, quote, parseReason) ||
        !ParseObject(payloads.account, accountState, parseReason) ||
        !ParseObject(payloads.positions, positions, parseReason) ||
        !ParseObject(payloads.orders, orders, parseReason) ||
        !ParseObject(payloads.riskLimits, riskLimits, parseReason))
        return Reject("DECISION_SNAPSHOT_COMPONENT_INVALID", parseReason,
            reasonCode, detail);
    if (!Authoritative(quote) || !Authoritative(accountState) ||
        !Authoritative(positions) || !Authoritative(orders) ||
        !Authoritative(riskLimits))
        return Reject("DECISION_SNAPSHOT_COMPONENT_INCOMPLETE",
            "one or more authoritative components are incomplete",
            reasonCode, detail);

    // Authority callbacks normally receive an already authenticated session
    // context and older payloads do not carry owner metadata.  If a callback
    // does expose owner_scope (or legacy top-level identity fields), however,
    // bind it exactly to the requested session instead of silently accepting
    // a response for another account/agent.
    std::string ownerDetail;
    if (!ValidateOptionalOwnerScope(healthBefore, agentId, sessionId, account,
                                    executionDomain, ownerDetail) ||
        !ValidateOptionalOwnerScope(healthAfter, agentId, sessionId, account,
                                    executionDomain, ownerDetail) ||
        !ValidateOptionalOwnerScope(quote, agentId, sessionId, account,
                                    executionDomain, ownerDetail) ||
        !ValidateOptionalOwnerScope(accountState, agentId, sessionId, account,
                                    executionDomain, ownerDetail) ||
        !ValidateOptionalOwnerScope(positions, agentId, sessionId, account,
                                    executionDomain, ownerDetail) ||
        !ValidateOptionalOwnerScope(orders, agentId, sessionId, account,
                                    executionDomain, ownerDetail) ||
        !ValidateOptionalOwnerScope(riskLimits, agentId, sessionId, account,
                                    executionDomain, ownerDetail))
        return Reject("DECISION_SNAPSHOT_OWNER_MISMATCH", ownerDetail,
                       reasonCode, detail);

    std::string quoteInstrument;
    std::uint64_t quoteObservedAtMs = 0;
    bool stale = true;
    double bid = 0.0;
    double ask = 0.0;
    if (!RequiredString(quote, "instrument", quoteInstrument) ||
        quoteInstrument != instrument ||
        !RequiredUnsigned(quote, "observed_at_ms", quoteObservedAtMs) ||
        quoteObservedAtMs == 0 ||
        !RequiredBool(quote, "stale", stale) || stale ||
        !RequiredNumber(quote, "bid", bid) ||
        !RequiredNumber(quote, "ask", ask) ||
        !(bid > 0.0) || ask < bid ||
        quoteObservedAtMs >
            static_cast<std::uint64_t>(std::numeric_limits<std::int64_t>::max()) ||
        quoteObservedAtMs < static_cast<std::uint64_t>(collectionStartedAtMs) ||
        quoteObservedAtMs > static_cast<std::uint64_t>(collectionCompletedAtMs))
        return Reject("DECISION_SNAPSHOT_QUOTE_INVALID",
            "quote is stale, malformed or bound to another instrument",
            reasonCode, detail);

    std::vector<double> matchingPositions;
    bool invalidPositionNumber = false;
    FindInstrumentPositions(positions, instrument, matchingPositions,
                            invalidPositionNumber);
    if (invalidPositionNumber)
        return Reject("DECISION_SNAPSHOT_POSITION_INVALID",
            "instrument position uses a non-canonical signed zero",
            reasonCode, detail);
    if (matchingPositions.size() > 1)
        return Reject("DECISION_SNAPSHOT_POSITION_INVALID",
            "instrument position is duplicated", reasonCode, detail);
    const double currentPosition = matchingPositions.empty() ?
        0.0 : matchingPositions.front();

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
    output.imbue(std::locale::classic());
    output << "{\"schema\":\"hepta.decision-snapshot.v1\","
 << "\"authoritative\":true,\"owner_scope\":{\"agent_id\":\""
 << EscapeJson(agentId)
 << "\",\"session_id\":\"" << EscapeJson(sessionId)
 << "\",\"account\":\"" << EscapeJson(account)
 << "\",\"execution_domain\":\"" << EscapeJson(executionDomain)
 << "\"},\"instrument\":\""
 << EscapeJson(instrument)
 << "\",\"execution_service_epoch\":\""
 << EscapeJson(before.epoch)
 << "\",\"fencing_generation\":" << before.fencingGeneration
 << ",\"collection_watermark\":" << collectionWatermark
 << ",\"event_watermark\":" << before.eventWatermark
 << ",\"snapshot_watermark\":" << collectionWatermark
 << ",\"collection_started_at_ms\":" << collectionStartedAtMs
 << ",\"collection_completed_at_ms\":" << collectionCompletedAtMs
 << ",\"current_position\":" << std::setprecision(17)
 << currentPosition
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
    // Validate the final envelope after interpolation.  Every component was
    // parsed independently above, but this catches accidental serializer
    // regressions (locale-specific numbers, malformed escaping or duplicate
    // top-level keys) before an authority-bound snapshot is returned.
    BoundedJsonValue encodedSnapshot;
    std::string encodedReason;
    if (!ParseBoundedJson(outputJson, encodedSnapshot, encodedReason,
                          kMaximumSnapshotBytes) ||
        !encodedSnapshot.IsObject())
    {
        outputJson.clear();
        return Reject("DECISION_SNAPSHOT_RESPONSE_INVALID",
            encodedReason.empty() ? "generated snapshot envelope is invalid" :
                encodedReason,
            reasonCode, detail);
    }
    reasonCode.clear();
    detail.clear();
    return true;
}
